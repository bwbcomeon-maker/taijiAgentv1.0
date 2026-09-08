from pathlib import Path
import json
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static" / "expert-team-v3.js"
PRESENTER = ROOT / "static" / "expert-team-presenter.js"
STYLE = ROOT / "static" / "expert-team-v3.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_node(source: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _run_v3_hooks(body: str) -> dict:
    return _run_node(
        textwrap.dedent(
            f"""
            const fs=require('fs');const vm=require('vm');
            const context={{
              window:{{}},
              document:{{readyState:'loading',addEventListener(){{}},getElementById(){{return null;}}}},
              console,queueMicrotask,setTimeout,
            }};
            vm.createContext(context);
            let source=fs.readFileSync('static/expert-team-v3.js','utf8');
            source=source.replace(
              'window.ExpertTeamV3 = Object.freeze({{',
              `window.__researchV2Hooks={{
                renderTeamDialog,statePanel,workbenchHtml,
                scheduleResearchAutoContinuation,handleWorkbenchClick,cardFromResponse,
                uploadResearchLaunchSources,
                setSelectedTeam(value){{
                  state.selectedTeam=value;
                  state.selectedExample=(value.examples||[]).find(item=>item.available===true)||null;
                  state.suggestionMode=false;
                }},
                setCard(value){{ state.card=value; }},
                setBusy(value){{ state.busy=Boolean(value); }},
                getCollapsed(){{ return state.collapsed; }},
                getAutoContinuationKeyCount(){{ return state.autoContinuationKeys.size; }},
                getAutoContinuationFailureKeyCount(){{ return state.autoContinuationFailureKeys.size; }},
              }};\n  window.ExpertTeamV3 = Object.freeze({{`
            );
            vm.runInContext(source,context);
            const hooks=context.window.__researchV2Hooks;
            {body}
            """
        )
    )


def test_research_attachment_upload_uses_the_session_created_from_expert_center():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');
            const uploads=[];
            class TestFormData{
              constructor(){this.values=[];}
              append(...value){this.values.push(value);}
            }
            const file={name:'资料.txt',size:8,lastModified:1};
            const context={
              window:{},console,queueMicrotask,setTimeout,FormData:TestFormData,URL,
              location:{href:'http://localhost/session/expert-center'},
              document:{readyState:'loading',baseURI:'http://localhost/session/expert-center',addEventListener(){},getElementById(id){return id==='expertTeamV3ResearchSources'?{files:[file]}:null;}},
              fetch:async(_url,options)=>{
                uploads.push(options.body.values);
                return {ok:true,json:async()=>({ref:'资料.txt',name:'资料.txt',mime:'text/plain',size:8})};
              },
            };
            vm.createContext(context);
            let source=`let S={session:null}; async function newSession(){S.session={session_id:'created-from-expert-center'};return S.session;}\n`+fs.readFileSync('static/expert-team-v3.js','utf8');
            source=source.replace(
              'window.ExpertTeamV3 = Object.freeze({',
              'window.__attachmentHook=uploadResearchLaunchSources; window.ExpertTeamV3 = Object.freeze({'
            );
            vm.runInContext(source,context);
            (async()=>{
              const payload=await context.window.__attachmentHook();
              console.log(JSON.stringify({payload,uploads,windowState:context.window.S||null}));
            })().catch(error=>{console.error(error);process.exit(1);});
            """
        )
    )

    assert result["windowState"] is None
    assert result["payload"] == {
        "source_session_id": "created-from-expert-center",
        "source_attachments": [{"name": "资料.txt", "ref": "资料.txt", "mime": "text/plain", "size": 8}],
    }
    assert result["uploads"] == [[
        ["session_id", "created-from-expert-center"],
        ["file", {"name": "资料.txt", "size": 8, "lastModified": 1}, "资料.txt"],
    ]]


def test_presenter_projects_research_v2_contract_without_inventing_frontend_state():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');
            const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const run={
              run_id:'research-run',session_id:'research-session',schema_version:3,version:4,
              team_id:'deep-research-team',launch_profile_id:'research-report',
              view:{
                product_mode:'standalone',public_state:'executing',allowed_actions:[],
                brief:{original_request:'研究本地优先 AI 助理在企业办公中的落地趋势'},
                research_progress:{
                  current_step:'local_knowledge',status_text:'正在补充本地资料',
                  public_status:'unavailable',local_knowledge_status:'running',
                  safe_fallback_reason:'公网资料暂时不可用，已自动继续使用可用资料。'
                },
                evidence_summary:{
                  public_source_count:0,local_source_count:3,unverified_model_claim_count:1,
                  coverage_level:'partial',source_basis:{id:'includes_model_knowledge',text:'包含模型知识·未外部核验'}
                },
                workflow:{stages:[],current_stage:{},progress:{}},workspace:{},presentation:{},
              },
            };
            const card=context.window.buildExpertTeamCardFromRun(run,{});
            console.log(JSON.stringify({
              researchV2:card.researchV2,
              progress:card.researchProgress,
              evidence:card.evidenceSummary,
            }));
            """
        )
    )

    assert result == {
        "researchV2": True,
        "progress": {
            "currentStep": "local_knowledge",
            "statusText": "正在补充本地资料",
            "publicStatus": "unavailable",
            "localKnowledgeStatus": "running",
            "safeFallbackReason": "公网资料暂时不可用，已自动继续使用可用资料。",
        },
        "evidence": {
            "publicSourceCount": 0,
            "localSourceCount": 3,
            "unverifiedModelClaimCount": 1,
            "coverageLevel": "partial",
            "sourceBasis": {
                "id": "includes_model_knowledge",
                "text": "包含模型知识·未外部核验",
            },
        },
    }


def test_research_v2_launch_is_one_semantic_form_with_only_original_request():
    result = _run_v3_hooks(
        """
        const dialog={innerHTML:''};
        const backdrop={hidden:true};
        const portal={inert:false};
        const root={querySelector(selector){
          if(selector==='[data-et3-dialog]')return dialog;
          if(selector==='[data-et3-dialog-backdrop]')return backdrop;
          if(selector==='.et3-portal')return portal;
          return null;
        }};
        const title={focus(){}};
        context.document.getElementById=id=>id==='expertTeamV3PortalRoot'?root:(id==='expertTeamV3DialogTitle'?title:null);
        hooks.setSelectedTeam({
          id:'deep-research-team',title:'深度材料研究团',category:'材料研究',
          description:'基于原始诉求自动检索资料并形成深度研究报告。',
          members:[{name:'不应展示的研究总导演',role:'内部流程'}],
          examples:[{id:'research',label:'研究报告',summary:'内部任务摘要',launch_profile_id:'research-report',available:true,prompt:''}],
        });
        hooks.renderTeamDialog();
        console.log(JSON.stringify({html:dialog.innerHTML,textareaCount:(dialog.innerHTML.match(/<textarea/g)||[]).length}));
        """
    )

    html = result["html"]
    assert '<form data-et3-research-launch-form' in html
    assert "基于原始诉求自动检索资料并形成深度研究报告。" in html
    assert "原始诉求" in html
    assert 'for="expertTeamV3Prompt"' in html
    assert 'type="submit"' in html
    assert "开始研究" in html
    assert result["textareaCount"] == 1
    for forbidden in (
        "团队成员",
        "不应展示的研究总导演",
        "选择文档任务",
        "文档标题",
        "文档用途",
        "阅读对象",
        "使用场景",
        "保存规格",
        "来源模式",
    ):
        assert forbidden not in html


def test_non_research_team_keeps_catalog_driven_members_and_task_choices():
    result = _run_v3_hooks(
        """
        const dialog={innerHTML:''};const backdrop={hidden:true};const portal={inert:false};
        const root={querySelector(selector){
          if(selector==='[data-et3-dialog]')return dialog;
          if(selector==='[data-et3-dialog-backdrop]')return backdrop;
          if(selector==='.et3-portal')return portal;
          return null;
        }};
        context.document.getElementById=id=>id==='expertTeamV3PortalRoot'?root:(id==='expertTeamV3DialogTitle'?{focus(){}}:null);
        hooks.setSelectedTeam({
          id:'content-creator-team',title:'内容创作专家团',category:'内容创作',description:'日常办公材料编制',
          members:[{name:'流程编排',role:'协作'}],
          examples:[{id:'work-report',label:'工作汇报',summary:'形成汇报',launch_profile_id:'content-work-report',available:true,prompt:''}],
        });
        hooks.renderTeamDialog();console.log(JSON.stringify({html:dialog.innerHTML}));
        """
    )

    assert "团队成员" in result["html"]
    assert "流程编排" in result["html"]
    assert "选择文档任务" in result["html"]
    assert "工作汇报" in result["html"]
    assert "发起专家团任务" in result["html"]


def test_research_v2_workbench_shows_only_request_safe_progress_evidence_and_no_internal_stage_review():
    result = _run_v3_hooks(
        """
        const card={
          researchV2:true,productMode:'standalone',publicState:'awaiting_stage_confirmation',
          workflowState:'awaiting_stage_confirmation',allowedActions:['stage_confirm','stage_revise'],
          presentation:{visibleTitle:'研究本地优先 AI 助理在企业办公中的落地趋势'},team:{title:'深度材料研究团'},phase:'evidence',
          brief:{originalRequest:'研究本地优先 AI 助理在企业办公中的落地趋势'},
          progress:{done:3,total:6,current:'evidence'},
          researchProgress:{currentStep:'local_knowledge',statusText:'正在补充本地资料',publicStatus:'unavailable',localKnowledgeStatus:'running',safeFallbackReason:'公网资料暂时不可用，已自动继续使用可用资料。'},
          evidenceSummary:{publicSourceCount:0,localSourceCount:3,unverifiedModelClaimCount:1,coverageLevel:'partial',sourceBasis:{id:'includes_model_knowledge',text:'包含模型知识·未外部核验'}},
          stageResult:{content:'机密检索日志：query=internal-only'},stageReview:{output:{content:'内部阶段产物'}},reviewItems:[{title:'内部复核项'}],
        };
        console.log(JSON.stringify({html:hooks.workbenchHtml(card)}));
        """
    )

    html = result["html"]
    request = "研究本地优先 AI 助理在企业办公中的落地趋势"
    assert html.count(request) == 1
    assert '正在补充本地资料' in html
    assert '公网资料暂时不可用，已自动继续使用可用资料。' in html
    assert '包含模型知识·未外部核验' in html
    assert '本地资料 3' in html
    assert 'data-et3-research-progress' in html
    assert 'aria-live="polite"' in html
    for forbidden in (
        '机密检索日志',
        '内部阶段产物',
        '内部复核项',
        '阶段成果',
        '加入修改意见',
        '无修改，进入下一阶段',
        '第 3/6 步',
    ):
        assert forbidden not in html


def test_research_v2_pending_input_is_one_keyboard_submittable_conclusion_question():
    result = _run_v3_hooks(
        """
        const card={
          researchV2:true,productMode:'standalone',publicState:'ready',workflowState:'awaiting_stage_input',
          allowedActions:['submit_stage_input'],
          pendingInput:{id:'input-1',scope:'conclusion',blocking:true,
            question:'核心结论应以单一部门还是全公司为研究对象？',
            impact:'对象范围会改变成本结论和推广建议。',options:['单一部门','全公司']},
        };
        console.log(JSON.stringify({html:hooks.statePanel(card,'ready')}));
        """
    )

    html = result["html"]
    assert '<form data-et3-research-question-form' in html
    assert "核心结论应以单一部门还是全公司为研究对象？" in html
    assert "对象范围会改变成本结论和推广建议。" in html
    assert html.count('name="research-answer"') == 2
    assert html.count('type="radio"') == 2
    assert 'type="submit"' in html
    assert "确认并继续研究" in html
    assert "网络失败" not in html
    assert "资料不足" not in html


def test_research_v2_primary_button_uses_the_high_contrast_action_token():
    style = _read(STYLE)

    assert "--et3-action-primary: #06798d" in style
    primary_start = style.index(".et3-button--primary")
    primary_rule = style[primary_start : style.index("}", primary_start)]
    assert "background: var(--et3-action-primary)" in primary_rule
    assert "color: #fff" in primary_rule


def test_research_v2_failed_auto_continuation_is_not_rescheduled_for_the_same_snapshot():
    result = _run_v3_hooks(
        """
        let calls=0;
        context.window.api=async()=>{calls+=1;throw new Error('provider unavailable');};
        const card={
          researchV2:true,runId:'research-run',sourceSessionId:'research-session',version:7,
          workflowState:'ready_to_generate',currentStageId:'research',productMode:'standalone',
          publicState:'ready',allowedActions:['resume'],pendingInputId:'',
        };
        hooks.setCard(card);
        hooks.scheduleResearchAutoContinuation(card);
        setTimeout(()=>{
          hooks.scheduleResearchAutoContinuation(card);
          setTimeout(()=>console.log(JSON.stringify({calls,keyCount:hooks.getAutoContinuationKeyCount(),failureKeyCount:hooks.getAutoContinuationFailureKeyCount()})),0);
        },0);
        """
    )

    assert result == {"calls": 1, "keyCount": 1, "failureKeyCount": 1}


def test_research_v3_valid_awaiting_review_stage_resumes_once_with_its_bound_artifact():
    result = _run_v3_hooks(
        """
        const calls=[];
        context.window.api=async(_endpoint, request)=>{calls.push(JSON.parse(request.body));return {};};
        const card={
          researchV3:{completed_units:0,total_units:7},runId:'research-run',sourceSessionId:'research-session',version:7,
          workflowState:'awaiting_review',currentStageId:'direction',productMode:'standalone',
          publicState:'awaiting_stage_confirmation',allowedActions:['resume'],pendingInputId:'',
          stageActionBinding:{session_id:'research-session',run_id:'research-run',expected_version:7,stage_id:'direction',stage_attempt:1,artifact_id:'direction:1',artifact_sha256:'a'.repeat(64)},
        };
        hooks.setCard(card);
        hooks.scheduleResearchAutoContinuation(card);
        setTimeout(()=>{
          hooks.scheduleResearchAutoContinuation(card);
          setTimeout(()=>console.log(JSON.stringify({calls,keyCount:hooks.getAutoContinuationKeyCount(),failureKeyCount:hooks.getAutoContinuationFailureKeyCount()})),0);
        },0);
        """
    )

    assert len(result["calls"]) == 1
    assert result["calls"][0]["artifact_id"] == "direction:1"
    assert result["calls"][0]["artifact_sha256"] == "a" * 64
    assert result["calls"][0]["stage_attempt"] == 1
    assert result["keyCount"] == 1
    assert result["failureKeyCount"] == 0


def test_research_v3_delivery_auto_continuation_uses_the_pending_system_stage():
    result = _run_v3_hooks(
        """
        const calls=[];
        context.window.api=async(_endpoint, request)=>{calls.push(JSON.parse(request.body));return {};};
        const card={
          researchV3:{completed_units:7,total_units:7},runId:'research-run',sourceSessionId:'research-session',version:57,
          workflowState:'delivery_validation_required',currentStageId:'review',productMode:'standalone',
          publicState:'generating_document',allowedActions:[],pendingInputId:'',
        };
        hooks.setCard(card);
        hooks.scheduleResearchAutoContinuation(card);
        setTimeout(()=>console.log(JSON.stringify({calls})),0);
        """
    )

    assert len(result["calls"]) == 1
    assert result["calls"][0]["stage_id"] == "delivery"
    assert result["calls"][0]["expected_version"] == 57


def test_research_v3_ready_review_exposes_the_existing_start_generation_action():
    result = _run_v3_hooks(
        """
        const card={
          researchV3:{completed_units:7,total_units:7},runId:'research-run',sourceSessionId:'research-session',version:56,
          workflowState:'ready_to_generate',currentStageId:'review',productMode:'standalone',
          publicState:'ready',allowedActions:['start_generation'],pendingInputId:'',
          team:{title:'深度材料研究团'},brief:{originalRequest:'形成深度研究报告'},evidenceSummary:{},
        };
        console.log(JSON.stringify({html:hooks.workbenchHtml(card)}));
        """
    )

    assert "正文已生成" in result["html"]
    assert "开始独立审核" in result["html"]
    assert 'data-et3-action="start-generation"' in result["html"]


def test_research_v3_product_error_response_is_presented_and_stops_auto_continuation():
    presented = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');
            const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const run={
              run_id:'research-run',session_id:'research-session',schema_version:3,version:7,
              team_id:'deep-research-team',launch_profile_id:'research-report',
              view:{product_mode:'standalone',public_state:'ready',allowed_actions:['resume'],
                research_v3:{completed_units:0,total_units:7},workflow:{stages:[],current_stage:{},progress:{}},workspace:{},presentation:{}}
            };
            const card=context.window.buildExpertTeamCardFromRun(run,{
              ok:false,run,
              product_error:{schema:'taiji.product.error.v1',code:'backend_unavailable',title:'服务暂不可用',message:'已停止自动继续。'}
            });
            console.log(JSON.stringify({researchV3:Boolean(card.researchV3),productError:card.productError}));
            """
        )
    )
    assert presented == {
        "researchV3": True,
        "productError": {
            "schema": "taiji.product.error.v1",
            "code": "backend_unavailable",
            "title": "服务暂不可用",
            "message": "已停止自动继续。",
            "incidentId": "",
            "retryable": False,
            "recoveryActions": [],
        },
    }

    scheduled = _run_v3_hooks(
        """
        let calls=0;
        context.window.api=async()=>{calls+=1;return {};};
        const card={
          researchV3:{completed_units:0,total_units:7},runId:'research-run',sourceSessionId:'research-session',version:7,
          workflowState:'ready_to_generate',currentStageId:'direction',productMode:'standalone',
          publicState:'ready',allowedActions:['resume'],pendingInputId:'',
          productError:{schema:'taiji.product.error.v1',code:'backend_unavailable'},
        };
        hooks.setCard(card);
        hooks.scheduleResearchAutoContinuation(card);
        setTimeout(()=>console.log(JSON.stringify({calls,keyCount:hooks.getAutoContinuationKeyCount()})),0);
        """
    )

    assert scheduled == {"calls": 0, "keyCount": 0}


def test_research_v2_close_remains_available_while_auto_continuation_is_busy():
    result = _run_v3_hooks(
        """
        let restoreFocused=false;
        const root={
          classList:{add(){}},
          querySelector(selector){
            if(selector==='[data-et3-action="restore-workbench"]')return {focus(){restoreFocused=true;}};
            return null;
          },
          querySelectorAll(){return [];},
        };
        context.document.body={classList:{add(){}}};
        context.document.getElementById=id=>id==='expertTeamV3Workbench'?root:null;
        hooks.setCard({runId:'research-run'});
        hooks.setBusy(true);
        const button={type:'button',dataset:{et3Action:'close-workbench'}};
        Promise.resolve(hooks.handleWorkbenchClick({target:{closest(){return button;}}})).then(()=>{
          console.log(JSON.stringify({collapsed:hooks.getCollapsed(),restoreFocused}));
        });
        """
    )

    assert result == {"collapsed": True, "restoreFocused": True}


def test_research_v2_provider_failure_replaces_in_progress_copy_with_recovery_state():
    result = _run_v3_hooks(
        """
        const card={
          researchV2:true,runId:'research-run',sourceSessionId:'research-session',version:8,
          workflowState:'start_failed',productMode:'standalone',publicState:'failed',
          allowedActions:['resume'],team:{title:'深度材料研究团'},
          researchProgress:{statusText:'正在形成研究报告'},
          evidenceSummary:{},
          productError:{
            schema:'taiji.product.error.v1',code:'backend_unavailable',title:'模型服务暂不可用',
            message:'任务进度和资料已保留，请稍后重试。',recoveryActions:[{id:'retry',label:'重试'}],
          },
        };
        console.log(JSON.stringify({html:hooks.workbenchHtml(card)}));
        """
    )

    assert "模型服务暂不可用" in result["html"]
    assert result["html"].count("模型服务暂不可用") == 1
    assert "研究已暂停" in result["html"]
    assert "任务进度和资料已保留，请稍后重试。" in result["html"]
    assert "重试当前阶段" in result["html"]
    assert "正在形成研究报告" not in result["html"]
    assert "正在自动继续研究" not in result["html"]
