"""Phase 4 UI contracts for durable chat image artifacts.

The tests execute the real JavaScript helpers from ``static/ui.js`` in Node.
They intentionally avoid a second Python implementation of the renderer so a
green result means the browser-facing code produced the asserted markup/state.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
UI_JS = ROOT / "static" / "ui.js"
MESSAGES_JS = ROOT / "static" / "messages.js"
PANELS_JS = ROOT / "static" / "panels.js"
STYLE_CSS = ROOT / "static" / "style.css"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _run_node(script: str, payload: dict | None = None) -> dict:
    assert NODE is not None
    result = subprocess.run(
        [NODE, "-e", script, str(UI_JS), str(MESSAGES_JS), str(PANELS_JS)],
        input=json.dumps(payload or {}, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


EXTRACTOR = r"""
const fs=require('fs');
const src=fs.readFileSync(process.argv[1],'utf8');
function extractFunc(name){
  const re=new RegExp('function\\s+'+name+'\\s*\\(');
  const start=src.search(re);if(start<0)throw new Error(name+' not found');
  let i=src.indexOf('{',start)+1,depth=1;
  while(depth>0&&i<src.length){if(src[i]==='{')depth++;else if(src[i]==='}')depth--;i++;}
  return src.slice(start,i);
}
global.esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
global.t=key=>({media_download:'下载',regenerate:'重新生成'}[key]||key);
global.li=()=>'<svg aria-hidden="true"></svg>';
"""


def test_structured_artifact_renderer_uses_session_authorized_url_and_no_path():
    script = EXTRACTOR + r"""
eval(extractFunc('_artifactMediaUrl'));
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_messageArtifactsHtml'));
const payload=JSON.parse(require('fs').readFileSync(0,'utf8'));
const html=_messageArtifactsHtml(payload.message,payload.sessionId,payload.messageIndex);
process.stdout.write(JSON.stringify({html}));
"""
    rendered = _run_node(
        script,
        {
            "sessionId": "session-a",
            "messageIndex": 3,
            "message": {
                "role": "assistant",
                "artifacts": [{
                    "artifact_id": "artifact-1",
                    "kind": "image",
                    "mime": "image/png",
                    "name": "生成 图片.png",
                    "size": 123,
                    "sha256": "a" * 64,
                    "status": "ready",
                    "storage_path": "/Users/private/runtime/cache/secret.png",
                }],
            },
        },
    )["html"]
    assert 'class="message-artifacts"' in rendered
    assert "api/media?session_id=session-a&amp;artifact_id=artifact-1" in rendered
    assert f"&amp;v={'a' * 64}" in rendered
    assert "/Users/private" not in rendered
    assert "chat-artifact-image" in rendered
    assert 'role="button"' in rendered and 'tabindex="0"' in rendered
    assert 'aria-label="查看图片 生成 图片.png"' in rendered
    assert 'aria-label="下载 生成 图片.png"' in rendered


def test_artifact_media_url_uses_fixed_schema_version_when_sha_is_missing():
    script = EXTRACTOR + r"""
eval(extractFunc('_artifactMediaUrl'));
process.stdout.write(JSON.stringify({url:_artifactMediaUrl('session-a','artifact-1',true,'')}));
"""
    url = _run_node(script)["url"]
    assert "v=artifact-v1" in url
    assert url.endswith("&download=1")


def test_artifact_failure_and_unavailable_states_are_actionable_without_broken_img():
    script = EXTRACTOR + r"""
eval(extractFunc('_artifactMediaUrl'));
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_messageArtifactsHtml'));
const payload=JSON.parse(require('fs').readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify({html:_messageArtifactsHtml(payload.message,'session-a',5)}));
"""
    failure = _run_node(
        script,
        {
            "message": {
                "role": "assistant",
                "artifact_errors": ["/Users/private/cache/missing.png: secret-canary"],
            }
        },
    )["html"]
    assert "图片未能保存" in failure
    assert "重新生成" in failure
    assert 'aria-label="重新生成图片"' in failure
    assert "/Users/private" not in failure and "secret-canary" not in failure
    assert "<img" not in failure

    unavailable = _run_node(
        script,
        {
            "message": {
                "role": "assistant",
                "artifacts": [{
                    "artifact_id": "artifact-gone",
                    "kind": "image",
                    "mime": "image/png",
                    "name": "old.png",
                    "status": "unavailable",
                }],
            }
        },
    )["html"]
    assert "资源已不可用" in unavailable
    assert "<img" not in unavailable


def test_historical_artifact_retry_appends_original_prompt_without_truncating_messages():
    script = EXTRACTOR + r"""
eval(extractFunc('msgContent'));
eval('async '+extractFunc('regenerateResponse'));
eval(extractFunc('_imageRetryDraftBlocker'));
eval('async '+extractFunc('retryImageArtifact'));
const original=[
  {role:'user',content:'请生成一张蓝色科技风图片。'},
  {role:'assistant',content:'图片已生成',artifacts:[{artifact_id:'image-1',kind:'image',status:'ready'}]},
  {role:'user',content:'把构图收紧一些。'},
  {role:'assistant',content:'已经完成后续细化。'},
];
global.S={busy:false,session:{session_id:'session-a'},messages:JSON.parse(JSON.stringify(original))};
const composer={value:'',focus(){this.focused=true;}};
const apiCalls=[];
const toasts=[];
global.$=id=>id==='msg'?composer:null;
global.api=async(url,options)=>{apiCalls.push({url,options});return {ok:true};};
global.renderMessages=()=>{};
global.autoResize=()=>{};
global.setStatus=()=>{};
global.showToast=(message)=>toasts.push(message);
global.send=async()=>{
  const text=composer.value;
  composer.value='';
  S.messages.push({role:'user',content:text});
  return true;
};
const row={dataset:{msgIdx:'1'}};
const button={closest:selector=>selector==='[data-msg-idx]'?row:null};
(async()=>{
  await retryImageArtifact(button);
  process.stdout.write(JSON.stringify({original,messages:S.messages,apiCalls,toasts,composer}));
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = _run_node(script)
    assert result["apiCalls"] == []
    assert result["messages"][:4] == result["original"]
    assert result["messages"][4] == {
        "role": "user",
        "content": "请生成一张蓝色科技风图片。",
    }
    assert any("新消息" in message for message in result["toasts"])

    label_script = EXTRACTOR + r"""
global.S={messages:[
  {role:'user',content:'图片要求'},
  {role:'assistant',content:'图片结果'},
  {role:'user',content:'后续要求'},
  {role:'assistant',content:'后续结果'},
]};
eval(extractFunc('_artifactMediaUrl'));
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_messageArtifactsHtml'));
const html=_messageArtifactsHtml({
  role:'assistant',
  artifacts:[{artifact_id:'image-1',kind:'image',name:'历史图片.png',status:'unavailable'}]
},'session-a',1);
process.stdout.write(JSON.stringify({html}));
"""
    rendered = _run_node(label_script)["html"]
    assert "作为新消息重新生成" in rendered
    assert 'aria-label="作为新消息重新生成图片"' in rendered


def test_historical_artifact_retry_guards_busy_missing_prompt_and_send_failure():
    script = EXTRACTOR + r"""
eval(extractFunc('msgContent'));
eval('async '+extractFunc('regenerateResponse'));
eval(extractFunc('_imageRetryDraftBlocker'));
eval('async '+extractFunc('retryImageArtifact'));
const composer={value:'',focus(){this.focused=true;}};
const toasts=[];
let sendCalls=0;
global.$=id=>id==='msg'?composer:null;
global.api=async()=>{throw new Error('truncate must not be called');};
global.renderMessages=()=>{};
global.autoResize=()=>{};
global.setStatus=()=>{};
global.showToast=(message)=>toasts.push(message);
const row={dataset:{msgIdx:'1'}};
const button={closest:selector=>selector==='[data-msg-idx]'?row:null};
(async()=>{
  global.S={
    busy:true,
    session:{session_id:'session-a'},
    messages:[
      {role:'user',content:'图片要求'},
      {role:'assistant',content:'图片结果'},
      {role:'user',content:'后续要求'},
    ],
  };
  global.send=async()=>{sendCalls++;return true;};
  await retryImageArtifact(button);
  const busy={messages:JSON.parse(JSON.stringify(S.messages)),toasts:[...toasts],sendCalls};

  toasts.length=0;
  S={
    busy:false,
    session:{session_id:'session-a'},
    messages:[
      {role:'assistant',content:'没有对应用户提示词'},
      {role:'user',content:'后续要求'},
    ],
  };
  const missingRow={dataset:{msgIdx:'0'}};
  await retryImageArtifact({closest:selector=>selector==='[data-msg-idx]'?missingRow:null});
  const missing={messages:JSON.parse(JSON.stringify(S.messages)),toasts:[...toasts],sendCalls};

  toasts.length=0;
  S={
    busy:false,
    session:{session_id:'session-a'},
    messages:[
      {role:'user',content:'图片要求'},
      {role:'assistant',content:'图片结果'},
      {role:'user',content:'后续要求'},
      {role:'assistant',content:'后续结果'},
    ],
  };
  global.send=async()=>{
    sendCalls++;
    const text=composer.value;
    composer.value='';
    S.messages.push({role:'user',content:text});
    return false;
  };
  const beforeFailure=JSON.parse(JSON.stringify(S.messages));
  await retryImageArtifact(button);
  const failure={
    before:beforeFailure,
    messages:JSON.parse(JSON.stringify(S.messages)),
    toasts:[...toasts],
    sendCalls,
  };
  process.stdout.write(JSON.stringify({busy,missing,failure}));
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = _run_node(script)
    assert result["busy"]["sendCalls"] == 0
    assert any("等待" in message for message in result["busy"]["toasts"])
    assert result["missing"]["sendCalls"] == 0
    assert any("原始" in message for message in result["missing"]["toasts"])
    assert result["failure"]["messages"][:4] == result["failure"]["before"]
    assert result["failure"]["messages"][4] == {
        "role": "user",
        "content": "图片要求",
    }
    assert any("未启动" in message for message in result["failure"]["toasts"])


def test_historical_artifact_retry_preserves_attachment_draft_and_requires_proven_send_start():
    script = EXTRACTOR + r"""
eval(extractFunc('msgContent'));
eval('async '+extractFunc('regenerateResponse'));
eval(extractFunc('_imageRetryDraftBlocker'));
eval('async '+extractFunc('retryImageArtifact'));
const composer={value:'',focus(){this.focused=true;}};
const toasts=[];
let sendCalls=0;
global.$=id=>id==='msg'?composer:null;
global.api=async()=>{throw new Error('truncate must not be called');};
global.renderMessages=()=>{};
global.autoResize=()=>{};
global.setStatus=()=>{};
global.showToast=(message)=>toasts.push(message);
const row={dataset:{msgIdx:'1'}};
const button={closest:selector=>selector==='[data-msg-idx]'?row:null};
const messages=[
  {role:'user',content:'图片要求'},
  {role:'assistant',content:'图片结果'},
  {role:'user',content:'后续要求'},
  {role:'assistant',content:'后续结果'},
];
(async()=>{
  global.S={
    busy:false,
    session:{session_id:'session-a'},
    messages:JSON.parse(JSON.stringify(messages)),
    pendingFiles:[{name:'草稿附件.png',token:'attachment-draft'}],
  };
  global.send=async()=>{sendCalls++;return true;};
  const attachmentsBefore=JSON.parse(JSON.stringify(S.pendingFiles));
  const pendingAccepted=await retryImageArtifact(button);
  const pending={
    accepted:pendingAccepted,
    sendCalls,
    composerValue:composer.value,
    attachmentsBefore,
    attachmentsAfter:JSON.parse(JSON.stringify(S.pendingFiles)),
    toasts:[...toasts],
  };

  toasts.length=0;
  composer.value='';
  S={
    busy:false,
    session:{session_id:'session-a'},
    messages:JSON.parse(JSON.stringify(messages)),
    pendingFiles:[],
  };
  global.send=async()=>{sendCalls++;return undefined;};
  const undefinedAccepted=await retryImageArtifact(button);
  const undefinedResult={
    accepted:undefinedAccepted,
    sendCalls,
    composerValue:composer.value,
    toasts:[...toasts],
  };
  process.stdout.write(JSON.stringify({pending,undefinedResult}));
})().catch(error=>{console.error(error);process.exit(1);});
"""
    result = _run_node(script)
    assert result["pending"]["accepted"] is False
    assert result["pending"]["sendCalls"] == 0
    assert result["pending"]["composerValue"] == ""
    assert result["pending"]["attachmentsAfter"] == result["pending"]["attachmentsBefore"]
    assert any("附件" in message for message in result["pending"]["toasts"])

    assert result["undefinedResult"]["accepted"] is False
    assert result["undefinedResult"]["sendCalls"] == 1
    assert result["undefinedResult"]["composerValue"] == "图片要求"
    assert not any("已将" in message for message in result["undefinedResult"]["toasts"])
    assert any("未启动" in message for message in result["undefinedResult"]["toasts"])


def test_historical_image_generation_failure_labels_retry_as_a_new_message():
    script = EXTRACTOR + r"""
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_imageGenerationStatusHtml'));
eval(extractFunc('_historyImageGenerationStatusHtml'));
const html=_historyImageGenerationStatusHtml({},[{
  name:'image_generate',
  status:'failed',
  done:true,
  is_error:true,
  summary:'图片未能安全保存',
  tid:'historical-image-failure'
}],{historical:true});
process.stdout.write(JSON.stringify({html}));
"""
    rendered = _run_node(script)["html"]
    assert "作为新消息重新生成" in rendered
    assert 'aria-label="作为新消息重新生成图片"' in rendered


def test_artifacts_make_assistant_message_visible_and_part_of_render_cache_signature():
    script = EXTRACTOR + r"""
global._isRecoveryControlMessage=()=>false;
eval(extractFunc('msgContent'));
eval(extractFunc('_assistantMessageHasVisibleContent'));
const payload=JSON.parse(require('fs').readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify({visible:_assistantMessageHasVisibleContent(payload)}));
"""
    assert _run_node(
        script,
        {"role": "assistant", "content": "", "artifacts": [{"artifact_id": "a", "status": "ready"}]},
    )["visible"] is True
    assert _run_node(
        script,
        {"role": "assistant", "content": "", "artifact_errors": ["failed"]},
    )["visible"] is True

    source = UI_JS.read_text(encoding="utf-8")
    signature = source[source.index("function _messageRenderCacheSignature"):]
    signature = signature[:signature.index("function _captureMessageScrollSnapshot")]
    assert "m.artifacts" in signature
    assert "m.artifact_errors" in signature


def test_image_generation_status_renderer_covers_loading_success_failure_cancel_timeout():
    script = EXTRACTOR + r"""
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_imageGenerationStatusHtml'));
const payload=JSON.parse(require('fs').readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(payload).map(([key,value])=>[key,_imageGenerationStatusHtml(value)]))));
"""
    rendered = _run_node(
        script,
        {
            "loading": {"name": "image_generate", "status": "running", "done": False, "tid": "i1"},
            "success": {"name": "image_generate", "status": "completed", "done": True, "tid": "i1"},
            "failure": {"name": "image_generate", "status": "failed", "done": True, "is_error": True, "summary": "图片格式不受支持", "tid": "i1"},
            "cancel": {"name": "image_generate", "status": "cancelled", "done": True, "tid": "i1"},
            "timeout": {"name": "image_generate", "status": "timeout", "done": True, "is_error": True, "tid": "i1"},
        },
    )
    assert "正在生成图片" in rendered["loading"]
    assert 'role="status"' in rendered["loading"] and 'aria-live="polite"' in rendered["loading"]
    assert "正在保存" in rendered["success"]
    assert "图片格式不受支持" in rendered["failure"]
    assert "已取消" in rendered["cancel"]
    assert "超时" in rendered["timeout"]
    for key in ("failure", "cancel", "timeout"):
        assert 'aria-label="重新生成图片"' in rendered[key]


def test_live_tool_wiring_upserts_image_state_and_terminal_handlers_finalize_it():
    ui = UI_JS.read_text(encoding="utf-8")
    append = ui[ui.index("function appendLiveToolCard"):]
    append = append[:append.index("function clearLiveToolCards")]
    assert "_upsertLiveImageGenerationStatus" in append

    messages = MESSAGES_JS.read_text(encoding="utf-8")
    for event, state in (("apperror", "failed"), ("cancel", "cancelled")):
        marker = f"source.addEventListener('{event}',e=>{{"
        start = messages.index(marker)
        block = messages[start:messages.index("\n    });", start) + len("\n    });")]
        assert f"_finalizeLiveImageGenerationStates('{state}'" in block
    assert "_finalizeLiveImageGenerationStates('timeout'" in messages


def test_terminal_image_state_is_promoted_to_safe_message_state_before_live_cleanup():
    ui = UI_JS.read_text(encoding="utf-8")
    assert "function _attachImageGenerationTerminalEvents" in ui
    attach = ui[ui.index("function _attachImageGenerationTerminalEvents"):]
    attach = attach[:attach.index("function ", 20)]
    assert "image_generation_events" in attach
    assert "event_type" in attach and "summary" in attach
    assert "args" not in attach and "result" not in attach

    signature = ui[ui.index("function _messageRenderCacheSignature"):]
    signature = signature[:signature.index("function _captureMessageScrollSnapshot")]
    assert "m.image_generation_events" in signature
    visible = ui[ui.index("function _assistantMessageHasVisibleContent"):]
    visible = visible[:visible.index("function ", 20)]
    assert "m.image_generation_events" in visible

    messages = MESSAGES_JS.read_text(encoding="utf-8")
    for event in ("apperror", "cancel"):
        marker = f"source.addEventListener('{event}',e=>{{"
        start = messages.index(marker)
        block = messages[start:messages.index("\n    });", start) + len("\n    });")]
        assert "_imageTerminalEvents" in block
        assert "_attachImageGenerationTerminalEvents" in block
        assert block.index("_finalizeLiveImageGenerationStates") < block.index("clearLiveToolCards")
    timeout = messages[messages.index("function _handleStreamError"):]
    timeout = timeout[:timeout.index("\n  }", 20)]
    assert "_imageTerminalEvents" in timeout
    assert "_attachImageGenerationTerminalEvents" in timeout
    assert timeout.index("_finalizeLiveImageGenerationStates") < timeout.index("clearLiveToolCards")


def test_promoted_terminal_image_event_is_whitelisted_and_renderable_without_tool_payload():
    script = EXTRACTOR + r"""
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_imageGenerationStatusHtml'));
eval(extractFunc('_historyImageGenerationStatusHtml'));
eval(extractFunc('_attachImageGenerationTerminalEvents'));
const message={role:'assistant',content:'连接中断'};
_attachImageGenerationTerminalEvents(message,[{
  event_type:'tool.completed',name:'image_generate',status:'timeout',duration:2,
  summary:'安全摘要',is_error:true,done:true,tid:'image-1',
  args:{token:'secret'},result:{path:'/Users/private/image.png'},storage_path:'/Users/private/image.png'
}]);
const html=_historyImageGenerationStatusHtml(message,message.image_generation_events);
process.stdout.write(JSON.stringify({message,html}));
"""
    result = _run_node(script)
    event = result["message"]["image_generation_events"][0]
    assert set(event) == {"event_type", "name", "status", "duration", "summary", "is_error", "done", "tid"}
    assert event["status"] == "timeout"
    assert "重新生成" in result["html"] and "图片生成超时" in result["html"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "secret" not in serialized and "/Users/private" not in serialized


def test_terminal_image_event_respects_current_user_turn_boundary():
    script = EXTRACTOR + r"""
eval(extractFunc('_attachImageGenerationTerminalEvents'));
eval(extractFunc('_attachImageGenerationTerminalEventsToCurrentTurn'));
const event=[{name:'image_generate',status:'cancelled',summary:'用户已取消本次生成',tid:'image-current'}];
const cases={
  noCurrentAssistant:[
    {role:'user',content:'上一轮问题'},
    {role:'assistant',content:'上一轮回答'},
    {role:'user',content:'当前图片请求'},
  ],
  currentAssistant:[
    {role:'user',content:'上一轮问题'},
    {role:'assistant',content:'上一轮回答'},
    {role:'user',content:'当前图片请求'},
    {role:'assistant',content:'当前部分回复'},
  ],
  noUser:[{role:'assistant',content:'孤立助手消息'}],
  consecutiveUsers:[
    {role:'user',content:'上一轮问题'},
    {role:'assistant',content:'上一轮回答'},
    {role:'user',content:'当前补充一'},
    {role:'user',content:'当前补充二'},
  ],
};
const out={};
for(const [key,messages] of Object.entries(cases)){
  const previousAssistant=messages.find(m=>m.role==='assistant');
  const target=_attachImageGenerationTerminalEventsToCurrentTurn(messages,event);
  out[key]={messages,targetIndex:target?messages.indexOf(target):-1,previousAssistant};
}
process.stdout.write(JSON.stringify(out));
"""
    result = _run_node(script)

    no_current = result["noCurrentAssistant"]
    assert "image_generation_events" not in no_current["previousAssistant"]
    assert no_current["targetIndex"] == 3
    assert no_current["messages"][3]["_transient"] is True
    assert no_current["messages"][3]["image_generation_events"][0]["status"] == "cancelled"

    current = result["currentAssistant"]
    assert current["targetIndex"] == 3
    assert current["messages"] == [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
        {"role": "user", "content": "当前图片请求"},
        {
            "role": "assistant",
            "content": "当前部分回复",
            "image_generation_events": [{
                "event_type": "tool.completed", "name": "image_generate",
                "status": "cancelled", "duration": 0,
                "summary": "用户已取消本次生成", "is_error": False,
                "done": True, "tid": "image-current",
            }],
        },
    ]

    assert result["noUser"]["targetIndex"] == -1
    assert result["noUser"]["messages"] == [{"role": "assistant", "content": "孤立助手消息"}]

    consecutive = result["consecutiveUsers"]
    assert "image_generation_events" not in consecutive["previousAssistant"]
    assert consecutive["targetIndex"] == 4
    assert consecutive["messages"][4]["_transient"] is True


def test_transient_terminal_image_message_is_dropped_before_the_next_user_turn():
    ui = UI_JS.read_text(encoding="utf-8")
    assert "function _discardTransientImageTerminalMessages" in ui
    messages = MESSAGES_JS.read_text(encoding="utf-8")
    send_start = messages.index("async function send")
    next_turn = messages[send_start:messages.index("const userMsg=", send_start)]
    assert "_discardTransientImageTerminalMessages" in next_turn


def test_replayed_image_tool_state_survives_refresh_without_overriding_ready_artifact():
    script = EXTRACTOR + r"""
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_imageGenerationStatusHtml'));
eval(extractFunc('_historyImageGenerationStatusHtml'));
const payload=JSON.parse(require('fs').readFileSync(0,'utf8'));
const loading=_historyImageGenerationStatusHtml({},[payload.loading]);
const failure=_historyImageGenerationStatusHtml({},[payload.failure]);
const ready=_historyImageGenerationStatusHtml({artifacts:[payload.artifact]},[payload.loading]);
process.stdout.write(JSON.stringify({loading,failure,ready}));
"""
    rendered = _run_node(
        script,
        {
            "loading": {"name": "image_generate", "status": "running", "done": False, "tid": "i1"},
            "failure": {"name": "image_generate", "status": "failed", "done": True, "is_error": True, "tid": "i1"},
            "artifact": {"artifact_id": "a1", "kind": "image", "status": "ready"},
        },
    )
    assert "正在生成图片" in rendered["loading"]
    assert "图片生成失败" in rendered["failure"]
    assert rendered["ready"] == ""
    ui = UI_JS.read_text(encoding="utf-8")
    assert "_renderHistoryImageGenerationState(anchorRow.parentElement" in ui


def test_lightbox_focus_escape_and_keyboard_activation_use_real_dom_helpers():
    script = EXTRACTOR + r"""
class El{
  constructor(tag){this.tagName=String(tag||'div').toUpperCase();this.children=[];this.attributes={};this.style={};this.parentNode=null;this.className='';this.src='';this.alt='';}
  appendChild(c){c.parentNode=this;this.children.push(c);return c;}
  removeChild(c){this.children=this.children.filter(x=>x!==c);c.parentNode=null;}
  setAttribute(k,v){this.attributes[k]=String(v);}
  querySelector(sel){if(sel==='img')return this.children.find(x=>x.tagName==='IMG')||null;return null;}
  querySelectorAll(sel){if(sel==='button:not([disabled]),a[href]')return this.children.filter(x=>x.tagName==='BUTTON'||x.tagName==='A');return [];}
  focus(){document.activeElement=this;}
  closest(){return null;}
}
global.document={body:new El('body'),activeElement:null,_handlers:{},createElement:t=>new El(t),addEventListener:(n,h)=>document._handlers[n]=h,removeEventListener:(n,h)=>{if(document._handlers[n]===h)delete document._handlers[n];}};
global.window={matchMedia:()=>({matches:true})};
global.setTimeout=fn=>{fn();return 1;};
eval(extractFunc('_openImgLightboxWithNav'));
eval(extractFunc('_navigateLightbox'));
eval(extractFunc('_closeImgLightbox'));
eval(extractFunc('_openImgLightbox'));
eval(extractFunc('_handleMessageImageKeydown'));
const trigger=new El('img');trigger.className='msg-media-img chat-artifact-image';trigger.src='api/media?session_id=s&artifact_id=a';trigger.alt='图片';trigger.focus();
_handleMessageImageKeydown({key:'Enter',target:trigger,preventDefault(){this.prevented=true;}});
const lb=document.body.children[0];
const opened={active:document.activeElement&&document.activeElement.className,modal:lb&&lb.attributes['aria-modal']};
lb._keyHandler({key:'Escape',preventDefault(){}});
process.stdout.write(JSON.stringify({opened,restored:document.activeElement===trigger,remaining:document.body.children.length}));
"""
    result = _run_node(script)
    assert result["opened"] == {"active": "img-lightbox-close", "modal": "true"}
    assert result["restored"] is True
    assert result["remaining"] == 0


def test_image_load_error_scroll_and_responsive_contracts():
    ui = UI_JS.read_text(encoding="utf-8")
    assert "_handleChatArtifactImageLoad" in ui
    assert "_handleChatArtifactImageError" in ui
    load_block = ui[ui.index("function _handleChatArtifactImageLoad"):]
    load_block = load_block[:load_block.index("function _handleChatArtifactImageError")]
    assert "_messageUserUnpinned" in load_block
    assert "scrollIfPinned" in load_block
    error_block = ui[ui.index("function _handleChatArtifactImageError"):]
    error_block = error_block[:error_block.index("document.addEventListener", 20)]
    assert "资源已不可用" in error_block
    assert "outerHTML" not in error_block

    assert "function _settleLoadedArtifactLayout" in ui
    settle = ui[ui.index("function _settleLoadedArtifactLayout"):]
    settle = settle[:settle.index("function ", 20)]
    assert "_messageUserUnpinned" in settle
    assert "scrollTop" in settle
    assert "getBoundingClientRect" in settle
    assert "before.bottom" in settle

    css = STYLE_CSS.read_text(encoding="utf-8")
    assert "@media(max-width:640px)" in css.replace(" ", "")
    assert ".message-artifacts" in css
    assert ".chat-artifact-card" in css
    assert "min-height:44px" in css
    assert "prefers-reduced-motion" in css


def test_dynamic_historical_image_load_error_uses_new_message_retry_action():
    script = EXTRACTOR + r"""
class ClassList{
  constructor(values=[]){this.values=new Set(values);}
  contains(value){return this.values.has(value);}
  add(value){this.values.add(value);}
}
class El{
  constructor(tag){
    this.tagName=String(tag||'div').toUpperCase();
    this.children=[];
    this.attributes={};
    this.className='';
    this.classList=new ClassList();
    this.textContent='';
    this.dataset={};
  }
  setAttribute(key,value){this.attributes[key]=String(value);}
  replaceChildren(...children){this.children=children;}
  closest(selector){
    if(selector==='[data-msg-idx]')return this.row||null;
    return null;
  }
}
global.S={messages:[
  {role:'user',content:'请生成一张历史蓝色科技图。'},
  {role:'assistant',content:'历史图片'},
  {role:'user',content:'后续要求一'},
  {role:'assistant',content:'后续回复一'},
  {role:'user',content:'后续要求二'},
  {role:'assistant',content:'后续回复二'},
]};
global.document={createElement:tag=>new El(tag)};
global.retryImageArtifact=()=>{};
eval(extractFunc('_imageRetryCta'));
eval(extractFunc('_handleChatArtifactImageError'));
const row={dataset:{msgIdx:'1'}};
const card=new El('section');card.row=row;
const img=new El('img');
img.classList=new ClassList(['chat-artifact-image']);
img.closest=selector=>selector==='.chat-artifact-card'?card:null;
_handleChatArtifactImageError({target:img});
const retry=card.children[2];
process.stdout.write(JSON.stringify({
  text:retry&&retry.textContent,
  ariaLabel:retry&&retry.attributes['aria-label'],
}));
"""
    result = _run_node(script)
    assert result == {
        "text": "作为新消息重新生成",
        "ariaLabel": "作为新消息重新生成图片",
    }


def test_clear_confirmation_mentions_seven_day_artifact_recovery():
    script = EXTRACTOR + r"""
const panelSrc=require('fs').readFileSync(process.argv[3],'utf8');
function extractPanelFunc(name){
  const re=new RegExp('function\\s+'+name+'\\s*\\(');
  const start=panelSrc.search(re);if(start<0)throw new Error(name+' not found');
  let i=panelSrc.indexOf('{',start)+1,depth=1;
  while(depth>0&&i<panelSrc.length){if(panelSrc[i]==='{')depth++;else if(panelSrc[i]==='}')depth--;i++;}
  return panelSrc.slice(start,i);
}
global.t=key=>({clear_conversation_message:'要清空所有消息吗？此操作无法撤销。',clear_conversation_artifact_retention:'图片将进入回收区，7 天后永久删除。'}[key]||key);
eval(extractPanelFunc('_clearConversationConfirmMessage'));
process.stdout.write(JSON.stringify({message:_clearConversationConfirmMessage()}));
"""
    message = _run_node(script)["message"]
    assert "7 天" in message
    assert "图片" in message
    panels = PANELS_JS.read_text(encoding="utf-8")
    clear_block = panels[panels.index("async function clearConversation"):]
    clear_block = clear_block[:clear_block.index("// ── Writing workflow panel")]
    assert "_clearConversationConfirmMessage()" in clear_block
