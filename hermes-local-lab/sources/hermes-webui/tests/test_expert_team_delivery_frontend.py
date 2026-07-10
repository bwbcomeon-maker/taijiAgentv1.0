import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _node(source: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_results_tab_renders_versioned_artifacts_with_direct_docx_open_and_missing_state():
    source = textwrap.dedent(
        f"""
        global.window=global;
        global.esc=(value)=>String(value==null?'':value);
        const fs=require('fs');
        eval(fs.readFileSync({json.dumps(str(ROOT / 'static' / 'expert-team-ui.js'))},'utf8'));
        const html=window.renderExpertTeamWorkspaceFromPresentation({{
          runId:'run-1',schemaVersion:2,version:8,readOnly:false,currentStageId:'delivery',
          presentation:{{state:'completed',title:'已完成',visibleTitle:'方案交付',detail:'交付完成',secondaryActions:[],result:{{content:'# 方案'}}}},
          workspace:{{currentStage:{{id:'delivery',phase:'交付确认'}},currentWorker:{{}},stageResult:{{summary:'已生成最终文档'}}}},
          workflow:{{stages:[],currentStage:{{id:'delivery',phase:'交付确认'}},progress:{{done:5,total:5,current_index:4}}}},
          members:[],artifacts:[
            {{id:'delivery:1:final_document',kind:'final_document',label:'最终 DOCX',path:'.taiji/final.docx',exists:true,stage:'delivery',attempt:1,status:'ready'}},
            {{id:'delivery:1:delivery_package',kind:'delivery_package',label:'完整交付包',path:'.taiji/delivery',exists:true,stage:'delivery',attempt:1,status:'ready'}},
            {{id:'delivery:1:quality_report',kind:'quality_report',label:'质量报告',path:'.taiji/missing.json',exists:false,stage:'delivery',attempt:1,status:'missing'}}
          ]
        }});
        console.log(JSON.stringify({{
          direct:/onclick="openExpertTeamFileArtifact\\(this\\)/.test(html),
          download:/onclick="downloadExpertTeamFileArtifact\\(this\\)/.test(html)&&html.includes('>下载<'),
          directoryDownload:(html.match(/downloadExpertTeamFileArtifact/g)||[]).length!==1,
          docx:/data-expert-team-artifact-kind="final_document"/.test(html)&&html.includes('打开 DOCX'),
          version:html.includes('交付确认 · 第 1 版'),
          missing:html.includes('文件不存在')&&/data-expert-team-artifact-kind="quality_report"[^>]*disabled/.test(html),
          generic:html.includes('openExpertTeamArtifact(this)')
        }}));
        """
    )
    result = _node(source)
    assert result == {
        "direct": True,
        "download": True,
        "directoryDownload": False,
        "docx": True,
        "version": True,
        "missing": True,
        "generic": False,
    }


def test_direct_file_open_download_and_legacy_relaunch_use_real_helpers():
    source = textwrap.dedent(
        f"""
        global.window=global;
        global.S={{session:{{session_id:'sid-1'}}}};
        const calls=[];
        global.api=async(path,options)=>{{calls.push([path,JSON.parse(options.body)]);return {{ok:true}};}};
        global.showToast=()=>{{}};
        const downloads=[];
        global.downloadFile=(path,name)=>downloads.push([path,name]);
        let relaunched='';
        global.openWriteflowTeamModal=(team)=>{{relaunched=team;}};
        window._activeExpertTeamStatusCard={{runId:'legacy-1',schemaVersion:1,readOnly:true,team:{{id:'deep-research-team'}}}};
        const fs=require('fs');
        eval(fs.readFileSync({json.dumps(str(ROOT / 'static' / 'expert-team-actions.js'))},'utf8'));
        const root={{dataset:{{expertTeamRunId:'legacy-1',expertTeamSchemaVersion:'1',expertTeamReadOnly:'true'}},querySelectorAll:()=>[]}};
        const openBtn={{dataset:{{expertTeamArtifactPath:'.taiji/final.docx',expertTeamArtifactExists:'true',expertTeamArtifactKind:'final_document'}},disabled:false,setAttribute:()=>{{}},removeAttribute:()=>{{}}}};
        await window.openExpertTeamFileArtifact(openBtn);
        await window.downloadExpertTeamFileArtifact(openBtn);
        const relaunchBtn={{dataset:{{expertTeamAction:'relaunch'}},closest:()=>root}};
        await window.handleExpertTeamPresentationAction(relaunchBtn);
        console.log(JSON.stringify({{calls,downloads,relaunched}}));
        """
    )
    result = _node(f"(async()=>{{{source}}})().catch(e=>{{console.error(e);process.exit(1);}})")
    assert result["calls"] == [["/api/file/open", {"session_id": "sid-1", "path": ".taiji/final.docx"}]]
    assert result["downloads"] == [[".taiji/final.docx", "final.docx"]]
    assert result["relaunched"] == "deep-research-team"


def test_legacy_workspace_has_visible_relaunch_action_and_no_mutation_controls():
    source = textwrap.dedent(
        f"""
        global.window=global;
        global.esc=(value)=>String(value==null?'':value);
        const fs=require('fs');
        eval(fs.readFileSync({json.dumps(str(ROOT / 'static' / 'expert-team-ui.js'))},'utf8'));
        const html=window.renderExpertTeamWorkspaceFromPresentation({{
          runId:'legacy-1',schemaVersion:1,version:0,readOnly:true,currentStageId:'draft',
          team:{{id:'content-creator-team',title:'内容创作专家团'}},
          presentation:{{state:'awaiting_review',title:'历史任务',detail:'仅查看',secondaryActions:[{{id:'approve_stage',label:'批准'}}],result:{{content:'历史内容'}}}},
          workspace:{{currentStage:{{id:'draft'}},currentWorker:{{}},stageResult:{{}}}},
          workflow:{{stages:[],currentStage:{{id:'draft'}},progress:{{done:0,total:0}}}},members:[],artifacts:[]
        }});
        console.log(JSON.stringify({{
          relaunch:html.includes('以新任务重新发起')&&html.includes('data-expert-team-action="relaunch"'),
          mutation:/data-expert-team-action="(?:approve_stage|revise_stage|start_generation|regenerate)"/.test(html)
        }}));
        """
    )
    assert _node(source) == {"relaunch": True, "mutation": False}
