"""Regression coverage for quiet collapsed tool-card previews.

Tool rows are public transcript metadata. They expose only a safe summary and
status; raw arguments and result snippets remain internal runtime state.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
UI_JS_PATH = REPO_ROOT / "static" / "ui.js"
MESSAGES_JS_PATH = REPO_ROOT / "static" / "messages.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")

_DRIVER_SRC = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function extractFunc(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(start, i);
}
eval(extractFunc('_toolCardPreviewText'));
let buf = '';
process.stdin.on('data', c => { buf += c; });
process.stdin.on('end', () => {
  const payload = JSON.parse(buf || '{}');
  process.stdout.write(_toolCardPreviewText(payload.tc || {}, payload.displaySnippet || ''));
});
"""


@pytest.fixture(scope="module")
def driver_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("tool_preview_driver") / "driver.js"
    p.write_text(_DRIVER_SRC, encoding="utf-8")
    return str(p)


def _preview(driver_path: str, tc: dict, display_snippet: str = "") -> str:
    assert NODE is not None
    result = subprocess.run(
        [NODE, driver_path, str(UI_JS_PATH)],
        input=json.dumps({"tc": tc, "displaySnippet": display_snippet}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def test_tool_header_uses_public_summary_and_ignores_raw_args_and_result(driver_path):
    preview = _preview(
        driver_path,
        {
            "name": "search_files",
            "summary": "Searched workspace",
            "args": {"path": "/tmp/private-canary", "token": "secret-canary"},
            "snippet": '{"total_count": 26, "matches": [{"path": "..."}]}',
            "done": True,
        },
        '{"total_count": 26, "matches": [{"path": "..."}]}',
    )

    assert preview == "Searched workspace"
    assert "/tmp/private-canary" not in preview
    assert "secret-canary" not in preview
    assert "total_count" not in preview
    assert "matches" not in preview


def test_tool_header_uses_status_when_no_summary(driver_path):
    preview = _preview(driver_path, {"name": "terminal", "done": True}, "long stdout that belongs in detail")
    assert preview == "Completed"


def test_failed_tool_status_takes_priority_over_success_sounding_summary(driver_path):
    preview = _preview(
        driver_path,
        {
            "name": "terminal",
            "summary": "Command completed",
            "status": "failed",
            "is_error": True,
            "done": True,
        },
    )
    assert preview == "Failed"


def test_running_tool_header_uses_safe_status_not_legacy_preview(driver_path):
    preview = _preview(
        driver_path,
        {"name": "terminal", "preview": "pytest --token secret-canary", "args": {"command": "pytest"}, "done": False},
        "stdout",
    )
    assert preview == "Running"


def test_build_tool_card_does_not_render_raw_args_or_result_snippets():
    src = UI_JS_PATH.read_text(encoding="utf-8")
    start = src.index("function buildToolCard(tc")
    end = src.index("function _syncToolCallGroupSummary", start)
    block = src[start:end]
    assert "tool-card-args" not in block
    assert "tc.args" not in block
    assert "tc.snippet" not in block
    assert "aria-label" in block


def test_multiple_public_tool_cards_have_no_dead_expand_controls_and_keep_safe_text():
    src = UI_JS_PATH.read_text(encoding="utf-8")
    render_start = src.index("function renderMessages")
    render_end = src.index("function _toolDisplayName", render_start)
    render_block = src[render_start:render_end]
    assert "tool-cards-toggle" not in render_block
    assert "expand_all" not in render_block
    assert "collapse_all" not in render_block

    script = r"""
const fs=require('fs');
const src=fs.readFileSync(process.argv[1],'utf8');
function extractFunc(name){const start=src.indexOf('function '+name);let i=src.indexOf('{',start)+1,depth=1;while(depth>0){if(src[i]==='{')depth++;else if(src[i]==='}')depth--;i++;}return src.slice(start,i);}
class FakeElement{constructor(){this.className='';this.innerHTML='';}}
global.document={createElement:()=>new FakeElement()};
global.esc=s=>String(s);global.toolIcon=()=>'';global._toolDisplayName=tc=>tc.name||'tool';
eval(extractFunc('_toolCardPreviewText'));eval(extractFunc('buildToolCard'));
const rows=[
  buildToolCard({name:'search',summary:'Safe workspace search',status:'completed',done:true}),
  buildToolCard({name:'terminal',status:'running',done:false})
];
process.stdout.write(JSON.stringify(rows.map(row=>row.innerHTML)));
"""
    result = subprocess.run(
        [NODE, "-e", script, str(UI_JS_PATH)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    joined = "\n".join(rendered)
    assert "Safe workspace search" in joined
    assert "Running" in joined
    assert "Expand all" not in joined
    assert "Collapse all" not in joined


def test_only_live_tool_cards_use_status_live_region_semantics():
    script = r"""
const fs=require('fs');const src=fs.readFileSync(process.argv[1],'utf8');
function extractFunc(name){const start=src.indexOf('function '+name);let i=src.indexOf('{',start)+1,depth=1;while(depth>0){if(src[i]==='{')depth++;else if(src[i]==='}')depth--;i++;}return src.slice(start,i);}
class FakeElement{constructor(){this.className='';this.innerHTML='';}}
global.document={createElement:()=>new FakeElement()};global.esc=s=>String(s);global.toolIcon=()=>'';global._toolDisplayName=tc=>tc.name||'tool';
eval(extractFunc('_toolCardPreviewText'));eval(extractFunc('buildToolCard'));
const tc={name:'terminal',summary:'Safe summary',status:'running',done:false};
process.stdout.write(JSON.stringify({settled:buildToolCard(tc).innerHTML,live:buildToolCard(tc,{live:true}).innerHTML}));
"""
    result = subprocess.run(
        [NODE, "-e", script, str(UI_JS_PATH)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert 'role="status"' not in rendered["settled"]
    assert 'aria-live=' not in rendered["settled"]
    assert 'role="status"' in rendered["live"]
    assert 'aria-live="polite"' in rendered["live"]


def test_live_sse_tool_start_and_complete_render_safe_summary_and_status():
    script = r"""
const fs=require('fs');
const ui=fs.readFileSync(process.argv[1],'utf8');
const messages=fs.readFileSync(process.argv[2],'utf8');
function extractFunc(src,name){
  const re=new RegExp('function\\s+'+name+'\\s*\\(');
  const start=src.search(re); if(start<0) throw new Error(name+' missing');
  let i=src.indexOf('{',start),depth=1;i++;
  while(depth>0&&i<src.length){if(src[i]==='{')depth++;else if(src[i]==='}')depth--;i++;}
  return src.slice(start,i);
}
function extractListener(name){
  const marker=`source.addEventListener('${name}',e=>{`;
  const start=messages.indexOf(marker); if(start<0) throw new Error(name+' listener missing');
  const brace=messages.indexOf('{',start); let i=brace+1,depth=1;
  while(depth>0&&i<messages.length){if(messages[i]==='{')depth++;else if(messages[i]==='}')depth--;i++;}
  return new Function('e',messages.slice(brace+1,i-1));
}
class FakeElement{constructor(){this.className='';this.innerHTML='';}}
global.document={createElement:()=>new FakeElement()};
global.esc=s=>String(s).replace(/[&<>\"]/g,'');
global.toolIcon=()=>'';
global._toolDisplayName=tc=>String(tc&&tc.name||'tool');
eval(extractFunc(ui,'_toolCardPreviewText'));
eval(extractFunc(ui,'buildToolCard'));
global.activeSid='sid';
global.S={session:{session_id:'sid'},messages:[],toolCalls:[],activeStreamId:'stream'};
global.INFLIGHT={};
global.persistInflightState=()=>{};
global.scheduleRenderSessionArtifacts=()=>{};
global.finalizeThinkingCard=()=>{};
global.liveReasoningText='';global.reasoningText='';
global.$=()=>null;
global.snapshotLiveTurn=()=>{};
global._flushPendingSegmentRender=()=>{};
global._freshSegment=false;
global._smdEndParser=()=>{};
global._resetAssistantSegment=()=>{};
global.scrollIfPinned=()=>{};
global.noteWorkspaceMutationsFromToolCall=()=>{};
global.refreshOpenPreviewIfMutated=()=>{};
global.setComposerStatus=()=>{};global.showToast=()=>{};global.refreshSecurityStatus=()=>{};
const rendered=[];
global.appendLiveToolCard=tc=>{const row=buildToolCard(tc,{live:true});rendered.push({tc:{...tc},html:row.innerHTML});};
const onTool=extractListener('tool');
const onComplete=extractListener('tool_complete');
onTool({data:JSON.stringify({name:'terminal',summary:'Safe start summary',status:'running',tid:'call-1',args:{token:'secret-canary'},result:'raw-result'})});
onComplete({data:JSON.stringify({name:'terminal',summary:'Safe complete summary',status:'completed',done:true,tid:'call-1',args:{token:'secret-canary'},result:'raw-result'})});
process.stdout.write(JSON.stringify(rendered));
"""
    result = subprocess.run(
        [NODE, "-e", script, str(UI_JS_PATH), str(MESSAGES_JS_PATH)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert len(rendered) == 2
    assert rendered[0]["tc"]["summary"] == "Safe start summary"
    assert rendered[0]["tc"]["status"] == "running"
    assert "Safe start summary" in rendered[0]["html"]
    assert "tool-card-running" in rendered[0]["html"]
    assert 'role="status"' in rendered[0]["html"]
    assert 'aria-live="polite"' in rendered[0]["html"]
    assert rendered[1]["tc"]["summary"] == "Safe complete summary"
    assert rendered[1]["tc"]["status"] == "completed"
    assert "Safe complete summary" in rendered[1]["html"]
    assert "tool-card-running" not in rendered[1]["html"]
    serialized = json.dumps(rendered)
    assert "secret-canary" not in serialized
    assert "raw-result" not in serialized
