import json
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ACTIONS_JS = (REPO_ROOT / "static" / "expert-team-actions.js").read_text(encoding="utf-8")
PRESENTER_JS = (REPO_ROOT / "static" / "expert-team-presenter.js").read_text(encoding="utf-8")
EXPERT_UI_JS = (REPO_ROOT / "static" / "expert-team-ui.js").read_text(encoding="utf-8")
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
SESSIONS_JS = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_expert_team_pilot_payload_is_explicit_and_fails_closed_when_rollout_is_off():
    assert "function _writeflowExpertTeamStartPayload" in PANELS_JS
    body = _function_body(
        PANELS_JS,
        "function _writeflowExpertTeamStartPayload",
        "async function summonWriteflowTeam",
    )
    for token in (
        "contract_version",
        "expert-team-contract/v1",
        "intake_example_id",
        "document_type",
        "document_brief_seed",
        "contractRollout.mode==='pilot'",
    ):
        assert token in body
    assert "delete payload.template_id" in body
    assert "template_id:example.id" in body


def test_expert_team_modal_labels_draft_capability_and_has_a_visible_prompt_label():
    assert "企业合同试点" in PANELS_JS
    assert "草稿能力" in PANELS_JS
    assert '<label for="writeflowTeamPrompt"' in PANELS_JS
    assert 'id="writeflowTeamPrompt"' in PANELS_JS


def test_expert_team_modal_names_original_request_and_explains_no_automatic_generation():
    assert '>原始诉求</label>' in PANELS_JS
    assert 'aria-describedby="writeflowTeamPromptHelp"' in PANELS_JS
    assert '作为文档规格中的“原始诉求”' in PANELS_JS
    assert '不会自动开始生成' in PANELS_JS


def test_expert_team_start_payload_runtime_behavior_for_off_and_pilot_modes():
    helper = _function_body(
        PANELS_JS,
        "function _writeflowExpertTeamStartPayload",
        "async function summonWriteflowTeam",
    )
    result = _run_node(
        textwrap.dedent(
            f"""
            let _writeflowContractRollout={{mode:'off',contract_version:'expert-team-contract/v1',document_types:[]}};
            {helper}
            const team={{id:'content-creator-team'}};
            const example={{
              id:'work_report',intake_example_id:'work_report',document_type:'work_report',task_mode:'create',
              prompt:'起草工作汇报',document_brief_seed:{{document_control:{{render_template_id:'enterprise-work-report'}}}},
            }};
            const off=_writeflowExpertTeamStartPayload(team,example,{{prompt:'起草工作汇报'}});
            _writeflowContractRollout={{mode:'pilot',contract_version:'expert-team-contract/v1',document_types:['work_report']}};
            const pilot=_writeflowExpertTeamStartPayload(team,example,{{prompt:'起草工作汇报'}});
            console.log(JSON.stringify({{off,pilot}}));
            """
        )
    )
    assert result["off"]["template_id"] == "work_report"
    assert "contract_version" not in result["off"]
    assert "template_id" not in result["pilot"]
    assert result["pilot"]["contract_version"] == "expert-team-contract/v1"
    assert result["pilot"]["intake_example_id"] == "work_report"
    assert result["pilot"]["document_type"] == "work_report"
    assert result["pilot"]["document_brief_seed"]["task_mode"] == "create"


def _run_node(source: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _actions_harness(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs=require('fs');
        const vm=require('vm');
        const context={{
          console,
          setTimeout,
          clearTimeout,
          AbortController,
          DOMException,
          crypto:{{randomUUID:()=> 'uuid-fixed'}},
          window:{{}},
          document:{{getElementById:()=>null}},
          S:{{session:{{session_id:'session-1'}}}},
          showToast:()=>{{}},
          renderSessionList:()=>{{}},
          _applyExpertTeamStreamResponse:()=>true,
          _expertTeamStatusCardFromRun:(run)=>run.card,
          renderExpertTeamStatusSurface:()=>{{}},
        }};
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/expert-team-actions.js','utf8'),context);
        {body}
        """
    )


def test_v2_mutation_runner_sends_complete_contract_and_deduplicates_double_click():
    result = _run_node(
        _actions_harness(
            """
            const calls=[];
            let resolveRequest;
            context.api=(path,opts)=>{
              calls.push({path,payload:JSON.parse(opts.body)});
              return new Promise(resolve=>{resolveRequest=resolve;});
            };
            context.window._activeExpertTeamStatusCard={
              runId:'run-1', sourceSessionId:'session-1', schemaVersion:2,
              version:7, currentStageId:'stage-2', readOnly:false,
            };
            const attrs={};
            const button={
              disabled:false, isConnected:true, dataset:{}, textContent:'批准',
              closest:()=>null,
              setAttribute:(key,value)=>{attrs[key]=value;},
              removeAttribute:(key)=>{delete attrs[key];},
            };
            const first=context.window.runExpertTeamMutation(button,{
              action:'approve_stage', endpoint:'/api/expert-teams/stage/approve'
            });
            const second=context.window.runExpertTeamMutation(button,{
              action:'approve_stage', endpoint:'/api/expert-teams/stage/approve'
            });
            const busy={disabled:button.disabled,ariaBusy:attrs['aria-busy'],samePromise:first===second};
            resolveRequest({run:{workflow_state:'generating'},stream_id:'stream-1'});
            Promise.all([first,second]).then(()=>{
              console.log(JSON.stringify({calls,busy,restored:{disabled:button.disabled,ariaBusy:attrs['aria-busy']||''}}));
            });
            """
        )
    )
    assert len(result["calls"]) == 1
    assert result["calls"][0]["path"] == "/api/expert-teams/stage/approve"
    assert result["calls"][0]["payload"] == {
        "run_id": "run-1",
        "session_id": "session-1",
        "expected_version": 7,
        "stage_id": "stage-2",
        "idempotency_key": "expert-team:run-1:7:stage-2:approve_stage:uuid-fixed",
    }
    assert result["busy"] == {"disabled": True, "ariaBusy": "true", "samePromise": True}
    assert result["restored"] == {"disabled": False, "ariaBusy": ""}


def test_last_requirement_submit_is_atomic_and_closes_when_intake_is_authoritatively_accepted():
    answer_start = UI_JS.index("async function answerExpertTeamQuestion")
    answer_end = UI_JS.index("if(typeof window!=='undefined'){", answer_start)
    answer_body = UI_JS[answer_start:answer_end]
    assert "runExpertTeamMutation" in answer_body
    assert "closeOnAcceptedIntake:true" in answer_body
    assert "executionStarted" in answer_body
    assert "showToast('需求已确认，专家团已开始生成。')" in answer_body
    assert "确认并开始生成" in UI_JS
    assert "需求已确认，正在进入生成。" not in answer_body


def test_execution_truth_requires_both_generating_state_and_stream_identity():
    result = _run_node(
        _actions_harness(
            """
            const check=context.window.isExpertTeamExecutionStarted;
            console.log(JSON.stringify({
              real:check({stream_id:'stream-1',run:{workflow_state:'generating',execution_stream_id:'stream-1'}}),
              missingRunStream:check({stream_id:'stream-1',run:{workflow_state:'generating'}}),
              missingStream:check({run:{workflow_state:'generating'}}),
              wrongState:check({stream_id:'stream-1',run:{workflow_state:'ready_to_generate'}}),
              failed:check({stream_id:'stream-1',run:{workflow_state:'start_failed'}}),
            }));
            """
        )
    )
    assert result == {
        "real": True,
        "missingRunStream": False,
        "missingStream": False,
        "wrongState": False,
        "failed": False,
    }
    stream_start = UI_JS.index("function _applyExpertTeamStreamResponse")
    stream_end = UI_JS.index("async function resumeExpertTeamRun", stream_start)
    stream_body = UI_JS[stream_start:stream_end]
    assert "isExpertTeamExecutionStarted(data)" in stream_body
    assert stream_body.index("isExpertTeamExecutionStarted(data)") < stream_body.index("S.busy=true")


def test_completed_intake_closes_for_starting_and_start_failed_but_not_when_pending():
    result = _run_node(
        _actions_harness(
            """
            const check=context.window.isExpertTeamIntakeAccepted;
            console.log(JSON.stringify({
              generating:check({workflow_state:'generating',questions:[{required:true,status:'answered'}]}),
              starting:check({workflow_state:'starting',questions:[{required:true,status:'answered'}]}),
              startFailed:check({workflow_state:'start_failed',questions:[{required:true,status:'answered'}]}),
              pending:check({workflow_state:'starting',questions:[{required:true,status:'pending'}]}),
            }));
            """
        )
    )
    assert result == {
        "generating": True,
        "starting": True,
        "startFailed": True,
        "pending": False,
    }


def test_presenter_and_workspace_expose_v2_mutation_identity_and_read_only_state():
    for token in (
        "schemaVersion",
        "version",
        "readOnly",
        "executionStreamId",
        "currentStageId",
        "pendingInputId",
        "stageReviewId",
    ):
        assert token in PRESENTER_JS
    for attribute in (
        "data-expert-team-schema-version",
        "data-expert-team-version",
        "data-expert-team-stage-id",
        "data-expert-team-stream-id",
        "data-expert-team-input-id",
        "data-expert-team-review-id",
        "data-expert-team-read-only",
    ):
        assert attribute in EXPERT_UI_JS
    assert "历史任务仅支持查看" in EXPERT_UI_JS
    assert "executionStreamId:str(run.execution_stream_id)" in PRESENTER_JS
    assert "execution_stream_id||run.execution_runtime_run_id" not in PRESENTER_JS


def _function_body(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    return source[start : source.index(next_signature, start)]


def test_every_ui_mutation_delegates_to_the_single_v2_runner():
    functions = (
        ("async function answerExpertTeamQuestion", "if(typeof window!=='undefined'){"),
        ("async function resumeExpertTeamRun", "async function cancelExpertTeamRun"),
        ("async function cancelExpertTeamRun", "function _expertTeamRunIdFromStageButton"),
        ("async function approveExpertTeamStage", "async function reviseExpertTeamStage"),
        ("async function reviseExpertTeamStage", "async function submitExpertTeamStageRevision"),
    )
    for signature, next_signature in functions:
        body = _function_body(UI_JS, signature, next_signature)
        assert "runExpertTeamMutation" in body, signature
        assert "await api(" not in body, signature

    action_start = ACTIONS_JS.index("async function handleExpertTeamPresentationAction")
    action_end = ACTIONS_JS.index("if(typeof window!=='undefined'){", action_start)
    action_body = ACTIONS_JS[action_start:action_end]
    assert "runExpertTeamMutation" in action_body
    assert "input_id:card.pendingInputId" in action_body
    assert "await api('/api/expert-teams" not in action_body


def test_cancel_requires_confirmation_and_keeps_stream_while_cancellation_is_pending():
    body = _function_body(UI_JS, "async function cancelExpertTeamRun", "function _expertTeamRunIdFromStageButton")
    assert "window.confirm" in body
    assert "workflow_state==='cancelled'" in body
    assert "data.cancelled_stream" in body
    clear_index = body.index("S.activeStreamId=null")
    terminal_index = body.index("workflow_state==='cancelled'")
    assert terminal_index < clear_index
    assert "workflow_state==='cancelling'" in body
    assert "停止请求已提交" in body


def test_stage_input_and_stage_review_mutations_carry_exact_subresource_identity():
    assert "input_id:card.pendingInputId" in ACTIONS_JS
    assert "review_id" in ACTIONS_JS
    result = _run_node(
        _actions_harness(
            """
            context.window._activeExpertTeamStatusCard={
              runId:'run-1', sourceSessionId:'session-1', schemaVersion:2,
              version:8, currentStageId:'stage-3', pendingInputId:'input-9',
              stageReviewId:'review-5', readOnly:false,
            };
            const input=context.window.expertTeamMutationContract(null,'submit_stage_input').payload;
            const review=context.window.expertTeamMutationContract(null,'approve_stage').payload;
            console.log(JSON.stringify({input,review}));
            """
        )
    )
    assert result["input"]["input_id"] == "input-9"
    assert result["review"]["review_id"] == "review-5"


def test_retry_after_network_failure_reuses_same_idempotency_key():
    result = _run_node(
        _actions_harness(
            """
            const keys=[];
            let attempt=0;
            context.api=(_path,opts)=>{
              keys.push(JSON.parse(opts.body).idempotency_key);
              attempt+=1;
              if(attempt===1)return Promise.reject(new Error('offline'));
              return Promise.resolve({run:{workflow_state:'generating'},stream_id:'stream-2'});
            };
            context.window._activeExpertTeamStatusCard={
              runId:'run-1', sourceSessionId:'session-1', schemaVersion:2,
              version:9, currentStageId:'stage-4', readOnly:false,
            };
            context.window.runExpertTeamMutation(null,{action:'resume',endpoint:'/api/expert-teams/resume'})
              .catch(()=>context.window.runExpertTeamMutation(null,{action:'resume',endpoint:'/api/expert-teams/resume'}))
              .then(()=>console.log(JSON.stringify({keys})));
            """
        )
    )
    assert len(result["keys"]) == 2
    assert result["keys"][0] == result["keys"][1]


def test_recovery_actions_are_visible_and_have_handlers():
    for action in ("retry_cancel", "refresh", "refresh_status", "retry_cleanup", "regenerate_unverified"):
        assert f"action==='{action}'" in ACTIONS_JS
    assert "refreshExpertTeamRun" in ACTIONS_JS
    assert "已有结果可能尚未核验" in ACTIONS_JS
    refresh = _function_body(ACTIONS_JS, "async function refreshExpertTeamRun", "function applyExpertTeamActionResponse")
    assert "run_id=${encodeURIComponent(card.runId)}" in refresh
    assert "仍未找到可安全绑定的结果" in refresh
    assert "专家团状态已核验" in refresh


def test_read_only_workspace_renders_no_mutating_controls():
    source = textwrap.dedent(
        """
        const fs=require('fs');
        const vm=require('vm');
        const context={window:{},console};
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/expert-team-ui.js','utf8'),context);
        const html=context.window.renderExpertTeamWorkspaceFromPresentation({
          runId:'legacy-1', schemaVersion:1, version:0, readOnly:true,
          presentation:{state:'awaiting_review',title:'待复核',primaryAction:{id:'review_stage',label:'去复核'},secondaryActions:[
            {id:'approve_stage',label:'批准'},{id:'revise_stage',label:'修改'}
          ]},
          workflow:{currentStage:{id:'stage-1'},stages:[],progress:{}},
          workspace:{},
          stageResult:{summary:'历史结果'},
          reviewItems:[{title:'复核项'}],
          questions:[{id:'q1',status:'pending',required:true}],
          members:[],
        });
        console.log(JSON.stringify({
          html,
          hasMutation:/data-expert-team-action="(?:approve_stage|revise_stage|review_stage|start_generation|regenerate|cancel|submit_stage_input)"/.test(html),
          hasReviewMutation:/appendExpertTeamReviewItemToRevision|markExpertTeamReviewItemRead/.test(html),
        }));
        """
    )
    result = _run_node(source)
    assert result["hasMutation"] is False
    assert result["hasReviewMutation"] is False
    assert "历史任务仅支持查看" in result["html"]


def test_conflict_applies_authoritative_run_and_restores_unsubmitted_form_state():
    result = _run_node(
        _actions_harness(
            """
            const events=[];
            context.captureExpertTeamWorkspaceFormState=()=>({draft:'未提交'});
            context.restoreExpertTeamWorkspaceFormState=(_root,state)=>{events.push(['restore',state.draft]);return true;};
            context._expertTeamStatusCardFromRun=(run)=>({runId:run.run_id});
            context.renderExpertTeamStatusSurface=(card)=>events.push(['render',card.runId]);
            context.api=()=>{
              const error=new Error('conflict');
              error.status=409;
              error.payload={run:{run_id:'run-1',workflow_state:'awaiting_review',questions:[{status:'pending'}]}};
              return Promise.reject(error);
            };
            context.window._activeExpertTeamStatusCard={
              runId:'run-1', sourceSessionId:'session-1', schemaVersion:2,
              version:2, currentStageId:'stage-1', readOnly:false,
            };
            context.window.runExpertTeamMutation(null,{action:'approve_stage',endpoint:'/api/expert-teams/stage/approve'})
              .catch(()=>console.log(JSON.stringify({events})));
            """
        )
    )
    assert result["events"] == [["render", "run-1"], ["restore", "未提交"]]


def test_start_failure_applies_authoritative_state_and_closes_completed_intake():
    result = _run_node(
        _actions_harness(
            """
            const events=[];
            context.captureExpertTeamWorkspaceFormState=()=>({draft:'已提交'});
            context.restoreExpertTeamWorkspaceFormState=()=>{events.push(['restore']);return true;};
            context.closeExpertTeamQuestionPopover=()=>{events.push(['close']);return true;};
            context._expertTeamStatusCardFromRun=(run)=>({runId:run.run_id});
            context.renderExpertTeamStatusSurface=(card)=>events.push(['render',card.runId]);
            context.api=()=>{
              const error=new Error('start failed');
              error.status=500;
              error.payload={run:{run_id:'run-1',workflow_state:'start_failed',questions:[{status:'answered'}]}};
              return Promise.reject(error);
            };
            context.window._activeExpertTeamStatusCard={
              runId:'run-1', sourceSessionId:'session-1', schemaVersion:2,
              version:2, currentStageId:'stage-1', readOnly:false,
            };
            context.window.runExpertTeamMutation(null,{
              action:'answer',endpoint:'/api/expert-teams/answer',closeOnAcceptedIntake:true
            }).catch(()=>console.log(JSON.stringify({events})));
            """
        )
    )
    assert result["events"] == [["close"], ["render", "run-1"], ["restore"]]


def test_polling_only_clears_workspace_on_explicit_404():
    hydrate_start = SESSIONS_JS.index("async function _hydrateExpertTeamStatusCardForSession")
    hydrate_end = SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession", hydrate_start)
    hydrate = SESSIONS_JS[hydrate_start:hydrate_end]
    assert "error&&error.status===404" in hydrate
    assert "return {status:'preserved',reason:'transient_error'}" in hydrate
    assert "return {status:'missing',reason:'not_found'}" in hydrate
    assert "return {status:'preserved',reason:'invalid_response'}" in hydrate
    assert "return {status:'preserved',reason:'stale_session'}" in hydrate


def test_silent_poll_always_fetches_authoritative_run_even_with_a_focused_local_draft():
    hydrate_start = SESSIONS_JS.index("async function _hydrateExpertTeamStatusCardForSession")
    hydrate_end = SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession", hydrate_start)
    hydrate = SESSIONS_JS[hydrate_start:hydrate_end]
    source = textwrap.dedent(
        f"""
        const calls=[];
        const rendered=[];
        const S={{session:{{session_id:'session-1'}}}};
        const api=async(path)=>{{
          calls.push(path);
          return {{run:{{run_id:'run-1',session_id:'session-1',workflow_state:'awaiting_review'}}}};
        }};
        const _isWriteflowHydrationForActiveSession=(sid)=>sid==='session-1';
        const shouldPreserveExpertTeamDraftDock=()=>true;
        const _expertTeamStatusCardFromRun=(run)=>({{runId:run.run_id,state:run.workflow_state}});
        const renderExpertTeamStatusSurface=(card)=>rendered.push(card);
        const _scheduleWriteflowStatusRefresh=()=>{{}};
        const _removeWriteflowStatusCardsFromMessages=()=>{{}};
        const renderSessionArtifacts=()=>{{}};
        {hydrate}
        _hydrateExpertTeamStatusCardForSession('session-1',{{silent:true}}).then(result=>{{
          console.log(JSON.stringify({{calls,rendered,result}}));
        }});
        """
    )
    result = _run_node(source)
    assert result["calls"] == ["/api/expert-teams/run?session_id=session-1"]
    assert result["rendered"] == [{"runId": "run-1", "state": "awaiting_review"}]
    assert result["result"] == {"status": "handled"}


def test_workspace_rerender_preserves_all_form_controls_focus_selection_tab_and_scroll():
    for token in (
        "function captureExpertTeamWorkspaceFormState",
        "function restoreExpertTeamWorkspaceFormState",
        "textarea,input,select",
        "selectionStart",
        "selectionEnd",
        "activeTab",
        "scrollTop",
    ):
        assert token in UI_JS
    mount = _function_body(UI_JS, "function mountExpertTeamWorkspacePanel", "function _expertTeamWorkspaceStorageKey")
    assert "const formState=popoverState.formState" in mount
    assert "restoreExpertTeamWorkspaceFormState(panel,formState,card)" in mount
    assert mount.index("_captureExpertTeamQuestionPopoverState(panel)") < mount.index("panel.innerHTML=")
    assert mount.index("panel.innerHTML=") < mount.index("restoreExpertTeamWorkspaceFormState(panel,formState,card)")


def test_workspace_tabs_and_result_dialog_have_complete_keyboard_semantics():
    for token in (
        'role="tablist"',
        'role="tab"',
        'role="tabpanel"',
        "aria-controls=",
        "aria-labelledby=",
        "handleExpertTeamWorkspaceTabKeydown(event)",
    ):
        assert token in EXPERT_UI_JS
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert key in ACTIONS_JS
    assert "aria-live=\"polite\"" in EXPERT_UI_JS

    viewer_start = UI_JS.index("function openExpertTeamResultViewer")
    viewer_end = UI_JS.index("function locateExpertTeamDeliveryMessage", viewer_start)
    viewer = UI_JS[viewer_start:viewer_end]
    assert 'aria-modal="true"' in viewer
    assert "trapExpertTeamResultViewerKeydown(event)" in viewer
    assert "_expertTeamResultViewerReturnFocus=trigger" in viewer
    close = _function_body(UI_JS, "function closeExpertTeamResultViewer", "function openExpertTeamResultViewer")
    assert "_expertTeamResultViewerReturnFocus" in close
    assert ".focus" in close
    assert "event.key==='Escape'" in UI_JS


def test_question_confirmation_dialog_and_mobile_targets_are_accessible():
    start = UI_JS.index("function _expertTeamQuestionPopoverHtml")
    end = UI_JS.index("function trapExpertTeamQuestionPopoverKeydown", start)
    popover = UI_JS[start:end]
    assert 'role="dialog"' in popover
    assert 'aria-modal="true"' in popover
    assert "@media (max-width:700px)" in STYLE_CSS
    assert ".expert-team-question-close{min-width:44px;min-height:44px" in STYLE_CSS
    assert ".expert-team-question-options button,.expert-team-question-actions button{min-height:44px" in STYLE_CSS


def test_all_runtime_states_have_user_facing_chinese_labels():
    for mapping in (
        "starting:'正在启动'",
            "start_failed:'启动失败'",
            "generation_failed:'生成失败'",
            "result_unverified:'结果待核验'",
            "legacy_result_unverified:'历史结果未绑定'",
        "revising:'重做中'",
        "cancelling:'正在停止'",
        "cancelled:'已取消'",
    ):
        assert mapping in EXPERT_UI_JS
    assert "if(state==='generated_invalid'||state==='start_failed'||state==='generation_failed'||state==='result_unverified'||state==='legacy_result_unverified'||state==='failed')return 'issue'" in EXPERT_UI_JS


def test_form_state_restore_keeps_same_question_draft_but_never_leaks_it_to_next_question():
    start = UI_JS.index("function _expertTeamWorkspaceControlKey")
    end = UI_JS.index("if(typeof window!=='undefined'){", start)
    form_code = UI_JS[start:end]
    source = textwrap.dedent(
        f"""
        let activeTab={{dataset:{{expertTeamWorkspaceTab:'todo'}}}};
        let scroller={{scrollTop:64}};
        let controls=[];
        const document={{activeElement:null}};
        function _expertTeamWorkspaceActiveTab(){{return 'todo';}}
        function _syncExpertTeamQuestionInputs(){{}}
        function switchExpertTeamWorkspaceTab(){{return true;}}
        function owner(id){{return {{dataset:{{expertTeamQuestionId:id}}}};}}
        function control(id,value){{
          return {{
            value,selectionStart:2,selectionEnd:5,checked:false,multiple:false,
            tagName:'TEXTAREA',type:'',id:'',name:'',focused:false,selection:null,
            closest:(selector)=>selector==='[data-expert-team-question-id]'?owner(id):null,
            getAttribute:(name)=>name==='data-expert-team-answer-input'?'1':'',
            focus(){{this.focused=true;document.activeElement=this;}},
            setSelectionRange(start,end){{this.selection=[start,end];}},
          }};
        }}
        const scope={{
          dataset:{{expertTeamRunId:'run-1'}},
          querySelector:(selector)=>selector==='[data-expert-team-workspace-tab].is-active'?activeTab:(selector==='.expert-team-panel-expanded-body'?scroller:null),
          querySelectorAll:(selector)=>selector==='textarea,input,select'?controls:[],
        }};
        {form_code}
        const first=control('q1','第一题草稿');
        controls=[first];document.activeElement=first;
        const saved=captureExpertTeamWorkspaceFormState(scope);
        const card={{sourceSessionId:'',runId:'run-1',currentStageId:'',questions:[{{id:'q1',status:'pending'}}]}};
        const same=control('q1','服务端默认');controls=[same];
        restoreExpertTeamWorkspaceFormState(scope,saved,card);
        const sameResult={{value:same.value,focused:same.focused,selection:same.selection}};
        const next=control('q2','第二题服务端值');controls=[next];
        restoreExpertTeamWorkspaceFormState(scope,saved,card);
        console.log(JSON.stringify({{same:sameResult,next:{{value:next.value,focused:next.focused}}}}));
        """
    )
    result = _run_node(source)
    assert result["same"] == {"value": "第一题草稿", "focused": True, "selection": [2, 5]}
    assert result["next"] == {"value": "第二题服务端值", "focused": False}


def test_authority_match_never_uses_the_old_dom_to_keep_an_advanced_question_alive():
    start = UI_JS.index("function _expertTeamWorkspaceFormStateMatchesCard")
    end = UI_JS.index("function restoreExpertTeamWorkspaceFormState", start)
    matcher = UI_JS[start:end]
    source = textwrap.dedent(
        f"""
        {matcher}
        const state={{
          sessionId:'session-1',runId:'run-1',stageId:'intake',questionId:'q1',inputId:'',
          draftCandidate:{{text:'未提交草稿',label:'第一题',questionId:'q1',inputId:''}},
        }};
        const oldScope={{
          dataset:{{expertTeamSourceSessionId:'session-1',expertTeamRunId:'run-1',expertTeamStageId:'intake'}},
          querySelector:(selector)=>selector.includes('status-card-expert-question.pending')&&selector.includes('q1')?{{}}:null,
        }};
        const advanced={{
          sourceSessionId:'session-1',runId:'run-1',currentStageId:'intake',
          questions:[{{id:'q1',status:'answered'}},{{id:'q2',status:'pending'}}],
        }};
        const missingIdentity={{questions:[{{id:'q1',status:'pending'}}]}};
        console.log(JSON.stringify({{
          matches:_expertTeamWorkspaceFormStateMatchesCard(advanced,state,oldScope),
          missingIdentity:_expertTeamWorkspaceFormStateMatchesCard(missingIdentity,state,oldScope),
        }}));
        """
    )
    assert _run_node(source) == {"matches": False, "missingIdentity": False}


def test_form_state_preserves_selected_stage_input_choice_across_poll_rerender():
    assert "selectedStageChoices" in UI_JS
    assert "[data-expert-team-stage-input-choice].is-selected" in UI_JS
    assert "[data-expert-team-stage-input-choice]" in UI_JS
    assert "classList.toggle('is-selected'" in UI_JS


def test_review_item_read_marker_is_not_exposed_as_a_fake_non_persistent_action():
    assert "markExpertTeamReviewItemRead" not in EXPERT_UI_JS
    assert "markExpertTeamReviewItemRead" not in UI_JS
    assert "data-expert-team-review-item-read" not in UI_JS
    assert "标记已阅" not in EXPERT_UI_JS


def test_failed_and_cancelled_terminal_runs_render_no_fake_retry_action():
    source = textwrap.dedent(
        """
        const fs=require('fs');
        const vm=require('vm');
        const context={window:{},console};
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/expert-team-ui.js','utf8'),context);
        function render(state){
          return context.window.renderExpertTeamWorkspaceFromPresentation({
            runId:'run-1',schemaVersion:2,version:4,readOnly:false,
            presentation:{state,title:state,detail:'终态',primaryAction:null,secondaryActions:[]},
            workflow:{currentStage:{id:'stage-1'},stages:[],progress:{}},workspace:{},members:[],
          });
        }
        const html=render('failed')+render('cancelled');
        console.log(JSON.stringify({hasRetry:/start_generation|regenerate|继续生成|重新尝试/.test(html)}));
        """
    )
    assert _run_node(source)["hasRetry"] is False


def test_pending_question_popover_survives_poll_only_for_same_session_run_stage_and_pending_question():
    signature = "function _expertTeamCanRestoreQuestionPopover"
    assert signature in UI_JS
    start = UI_JS.index(signature)
    end = UI_JS.index("function _captureExpertTeamQuestionPopoverState", start)
    helper = UI_JS[start:end]
    source = textwrap.dedent(
        f"""
        {helper}
        const saved={{open:true,sessionId:'session-1',runId:'run-1',stageId:'intake',questionId:'q1'}};
        const same={{sourceSessionId:'session-1',runId:'run-1',currentStageId:'intake',status:'collecting_required',questions:[{{id:'q1',status:'pending'}}]}};
        const next={{sourceSessionId:'session-1',runId:'run-1',currentStageId:'intake',status:'collecting_required',questions:[{{id:'q1',status:'answered'}},{{id:'q2',status:'pending'}}]}};
        const completed={{sourceSessionId:'session-1',runId:'run-1',currentStageId:'intake',status:'starting',questions:[{{id:'q1',status:'answered'}}]}};
        const switchedRun={{sourceSessionId:'session-1',runId:'run-2',currentStageId:'intake',status:'collecting_required',questions:[{{id:'q1',status:'pending'}}]}};
        const switchedSession={{sourceSessionId:'session-2',runId:'run-1',currentStageId:'intake',status:'collecting_required',questions:[{{id:'q1',status:'pending'}}]}};
        const advancedStage={{sourceSessionId:'session-1',runId:'run-1',currentStageId:'plan',status:'collecting_required',questions:[{{id:'q1',status:'pending'}}]}};
        console.log(JSON.stringify({{
          pollSamePending:_expertTeamCanRestoreQuestionPopover(same,saved),
          nextQuestion:_expertTeamCanRestoreQuestionPopover(next,saved),
          acceptedFinal:_expertTeamCanRestoreQuestionPopover(completed,saved),
          switchedRun:_expertTeamCanRestoreQuestionPopover(switchedRun,saved),
          switchedSession:_expertTeamCanRestoreQuestionPopover(switchedSession,saved),
          advancedStage:_expertTeamCanRestoreQuestionPopover(advancedStage,saved),
          explicitlyClosed:_expertTeamCanRestoreQuestionPopover(same,{{...saved,open:false}}),
        }}));
        """
    )
    assert _run_node(source) == {
        "pollSamePending": True,
        "nextQuestion": False,
        "acceptedFinal": False,
        "switchedRun": False,
        "switchedSession": False,
        "advancedStage": False,
        "explicitlyClosed": False,
    }


def test_advanced_authoritative_state_keeps_text_only_as_one_recoverable_draft_hint():
    for token in (
        "function _expertTeamRememberRecoverableDraft",
        "function _expertTeamRecoverableDraftHintHtml",
        "data-expert-team-recoverable-draft",
        "上一项未提交内容已保留",
        "复制草稿",
        "忽略草稿",
    ):
        assert token in UI_JS
    mount = _function_body(UI_JS, "function mountExpertTeamWorkspacePanel", "function _expertTeamWorkspaceStorageKey")
    assert "_expertTeamWorkspaceFormStateMatchesCard(card,formState)" in mount
    assert "_expertTeamRememberRecoverableDraft(card,formState)" in mount
    assert "restoreExpertTeamWorkspaceFormState(panel,formState,card)" in mount
    assert mount.index("_expertTeamRememberRecoverableDraft(card,formState)") < mount.index("panel.innerHTML=")
    assert "_expertTeamRecoverableDraftHintHtml(card)" in EXPERT_UI_JS
    assert "_expertTeamCanRestoreQuestionPopover(card,popoverState)" in mount
    assert "if(canRestoreForm)_restoreExpertTeamWorkspaceScrollState(panel,scrollState)" in mount


def test_recoverable_draft_hint_is_bounded_to_the_same_session_and_run():
    match_start = UI_JS.index("function _expertTeamWorkspaceFormStateMatchesCard")
    match_end = UI_JS.index("function restoreExpertTeamWorkspaceFormState", match_start)
    draft_start = UI_JS.index("let _expertTeamRecoverableDraft=null;")
    draft_end = UI_JS.index("function _expertTeamCanRestoreQuestionPopover", draft_start)
    source = textwrap.dedent(
        f"""
        const esc=(value)=>String(value||'');
        {UI_JS[match_start:match_end]}
        {UI_JS[draft_start:draft_end]}
        const state={{
          sessionId:'session-1',runId:'run-1',stageId:'intake',questionId:'q1',inputId:'',
          draftCandidate:{{text:'这是未提交内容',label:'第一项',questionId:'q1',inputId:''}},
        }};
        const advanced={{sourceSessionId:'session-1',runId:'run-1',currentStageId:'plan',questions:[{{id:'q1',status:'answered'}}]}};
        const remembered=_expertTeamRememberRecoverableDraft(advanced,state);
        const hint=_expertTeamRecoverableDraftHintHtml(advanced);
        const otherSession=_expertTeamRecoverableDraftHintHtml({{sourceSessionId:'session-2',runId:'run-1',currentStageId:'plan'}});
        const afterSwitch=_expertTeamRecoverableDraftHintHtml(advanced);
        console.log(JSON.stringify({{
          remembered,
          hasText:hint.includes('这是未提交内容'),
          hasOldQuestion:hint.includes('data-expert-team-question-popover'),
          otherSession,
          afterSwitch,
        }}));
        """
    )
    assert _run_node(source) == {
        "remembered": True,
        "hasText": True,
        "hasOldQuestion": False,
        "otherSession": "",
        "afterSwitch": "",
    }


def test_answer_optional_action_click_invokes_popover_open_path():
    result = _run_node(
        _actions_harness(
            """
            const popover={hidden:true};
            context.openExpertTeamQuestionPopover=()=>{popover.hidden=false;return true;};
            context.window._activeExpertTeamStatusCard={runId:'run-1'};
            const root={dataset:{expertTeamRunId:'run-1'}};
            const button={dataset:{expertTeamAction:'answer_optional'},closest:()=>root};
            context.window.handleExpertTeamPresentationAction(button).then(handled=>{
              console.log(JSON.stringify({handled,hidden:popover.hidden}));
            });
            """
        )
    )
    assert result == {"handled": True, "hidden": False}


def test_question_popover_lookup_falls_back_from_local_status_card_to_workspace():
    start = UI_JS.index("function _expertTeamQuestionPopoverElement")
    end = UI_JS.index("function _focusExpertTeamQuestionPopover", start)
    lookup = UI_JS[start:end]
    source = textwrap.dedent(
        f"""
        const popover={{hidden:true}};
        const localStatusCard={{querySelector:()=>null}};
        const workspace={{querySelector:(selector)=>selector==='[data-expert-team-question-popover]'?popover:null}};
        const trigger={{closest:(selector)=>selector==='.status-card-writeflow'?localStatusCard:null}};
        const document={{getElementById:(id)=>id==='expertTeamWorkspacePanel'?workspace:null}};
        {lookup}
        console.log(JSON.stringify({{found:_expertTeamQuestionPopoverElement(trigger)===popover}}));
        """
    )
    assert _run_node(source) == {"found": True}


def test_workspace_mount_captures_and_restores_open_popover_with_form_focus_and_scroll():
    for token in (
        "function _captureExpertTeamQuestionPopoverState",
        "function _restoreExpertTeamQuestionPopoverState",
        "formState",
        "scrollState",
        "focusWithin",
    ):
        assert token in UI_JS
    mount = _function_body(UI_JS, "function mountExpertTeamWorkspacePanel", "function _expertTeamWorkspaceStorageKey")
    assert "_captureExpertTeamQuestionPopoverState(panel)" in mount
    assert "_restoreExpertTeamQuestionPopoverState(panel,card,popoverState)" in mount
    assert mount.index("_captureExpertTeamQuestionPopoverState(panel)") < mount.index("panel.innerHTML=")
    assert mount.index("panel.innerHTML=") < mount.index("_restoreExpertTeamQuestionPopoverState(panel,card,popoverState)")
    assert "&&!_expertTeamQuestionIsTerminal(question)" in UI_JS
    answer = _function_body(UI_JS, "async function answerExpertTeamQuestion", "if(typeof window!=='undefined'){")
    assert "continuesIntake" in answer
    assert "openExpertTeamQuestionPopover(null)" in answer


def test_expert_catalog_fallback_uses_current_local_delivery_capability_copy():
    assert "等待后端模板" not in PANELS_JS
    assert "statusLabel: '本地交付已就绪'" in PANELS_JS
    assert "description: '支持需求确认、分阶段协作与本地文档交付。'" in PANELS_JS


def test_compact_desktop_expert_workspace_reserves_chat_context_and_scrolls_inside_panel():
    compact_start = STYLE_CSS.index("@media (min-width:901px) and (max-width:1320px)", STYLE_CSS.index("Expert team desktop split workspace"))
    compact_end = STYLE_CSS.index("\n.expert-team-capsule{", compact_start)
    compact = STYLE_CSS[compact_start:compact_end]
    assert "grid-template-rows:min(42vh,320px) minmax(112px,1fr) auto!important;" in compact
    assert "grid-template-rows:minmax(360px,auto)" not in compact
    assert "height:100%!important;" in compact
    assert "max-height:min(42vh,320px)!important;" in compact
    assert "min-height:112px!important;" in compact
    assert "overflow:hidden!important;" in compact

    wide_start = STYLE_CSS.index("/* Expert team desktop split workspace: reserve layout space instead of overlaying the task board. */")
    wide = STYLE_CSS[wide_start:compact_start]
    assert "grid-template-columns:minmax(0,1fr) clamp(380px,36%,500px)!important;" in wide
    assert "grid-row:1 / span 2!important;" in wide
    assert "overflow:hidden auto!important;" in wide


def _enterprise_brief(*, status="draft"):
    return {
        "schema_version": "document-brief/v1",
        "revision": 3,
        "status": status,
        "team_id": "content-creator-team",
        "task_mode": "create",
        "original_request": "请根据已提供材料形成迎峰度夏保供电月度汇报。",
        "document_type": "work_report",
        "intake_example_id": "work_report",
        "exact_title": "迎峰度夏保供电重点工作月度汇报",
        "purpose": "提交经营班子审议",
        "audience": "公司经营班子",
        "usage_scenario": "月度经营分析会",
        "source_policy": {"source_refs": []},
        "data_handling": {},
        "document_control": {"render_template_id": "enterprise-work-report"},
        "content_constraints": {},
        "details": {},
        "approval": {},
        "additional_context": "只使用已批准资料。",
        "confirmed_revision": 3 if status == "confirmed" else None,
        "confirmed_at": "2026-07-15T10:00:00+08:00" if status == "confirmed" else None,
        "confirmed_sha256": "b" * 64 if status == "confirmed" else None,
    }


def test_enterprise_view_exposes_persistent_brief_progress_capability_and_honest_legacy_copy():
    from api.expert_teams.view import expert_team_run_view

    enterprise = expert_team_run_view(
        {
            "run_id": "run-enterprise",
            "contract_version": "expert-team-contract/v1",
            "team_id": "content-creator-team",
            "workflow_state": "collecting_required",
            "document_brief": _enterprise_brief(),
            "tasks": [{"id": "draft"}, {"id": "review"}],
        }
    )
    assert enterprise["brief"]["original_request"] == "请根据已提供材料形成迎峰度夏保供电月度汇报。"
    assert enterprise["brief"]["original_request_summary"]
    assert enterprise["brief"]["exact_title"] == "迎峰度夏保供电重点工作月度汇报"
    assert enterprise["brief"]["document_type_label"] == "工作汇报"
    assert enterprise["brief"]["revision"] == 3
    assert enterprise["brief"]["view_action"]["label"] == "查看/编辑文档规格"
    assert enterprise["presentation"]["progress_text"] == "0/2"
    assert enterprise["capability"]["label"] == "企业合同试点"

    confirmed_not_started = expert_team_run_view(
        {
            "run_id": "run-confirmed",
            "contract_version": "expert-team-contract/v1",
            "team_id": "content-creator-team",
            "workflow_state": "ready_to_generate",
            "document_brief": _enterprise_brief(status="confirmed"),
            "tasks": [{"id": "draft"}, {"id": "review"}],
        }
    )
    assert confirmed_not_started["presentation"]["progress_text"] == "0/2"

    legacy = expert_team_run_view({"run_id": "run-legacy", "workflow_state": "completed"})
    assert legacy["capability"]["label"] == "历史任务，未按企业合同验证"
    assert "brief" not in legacy
    assert legacy["completion_gates"]["content"]["status"] != "passed"


def test_completion_gates_fail_closed_and_do_not_treat_a_document_file_as_document_passed():
    from api.expert_teams.view import expert_team_run_view

    view = expert_team_run_view(
        {
            "run_id": "run-gates",
            "contract_version": "expert-team-contract/v1",
            "team_id": "content-creator-team",
            "workflow_state": "delivery_validation_required",
            "document_brief": _enterprise_brief(status="confirmed"),
            "canonical_document_ref": {
                "artifact_id": "artifact-1",
                "sha256": "a" * 64,
                "brief_revision": 3,
                "brief_sha256": "b" * 64,
            },
            "approved_stage_artifact_refs": {"review": {"artifact_id": "artifact-1", "sha256": "a" * 64}},
            "artifacts": [{"kind": "final_document", "status": "ready", "path": "delivery/document.docx"}],
            "enterprise_quality_gates": {
                "brief": "passed",
                "semantic": "passed",
                "evidence": "passed",
                "asset": "passed",
                "render": "failed",
                "office": "pending",
                "delivery": "pending",
            },
        }
    )
    assert view["completion_gates"]["content"]["status"] == "passed"
    assert view["completion_gates"]["document"]["status"] == "failed"
    assert view["completion_gates"]["office"]["status"] == "pending"
    assert all(gate["next_action"] for gate in view["completion_gates"].values())
    assert view["artifact_validation"]["status"] == "unavailable"
    assert view["delivery_status"] != "passed"
    assert view["next_action"]["type"] == "repair_document"


def test_presenter_is_a_pure_state_mapper_for_brief_review_delivery_and_capability_states():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');
            const vm=require('vm');
            const context={window:{},console};
            vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const present=context.window.buildExpertTeamPresentation;
            function model(state,extra={}){
              return present({workflow_state:state,view:{
                presentation:{state,title:'内部标题',progress_text:'0/2'},
                brief:{status:'draft',revision:2,original_request:'原始诉求',original_request_summary:'原始诉求',exact_title:'精确标题',document_type:'work_report',document_type_label:'工作汇报'},
                completion_gates:{content:{status:'pending'},document:{status:'pending'},office:{status:'pending'}},
                delivery_status:'pending',next_action:{type:'confirm_brief',label:'确认文档规格'},
                capability:{kind:'enterprise_pilot',label:'企业合同试点'},
                ...extra,
              }});
            }
            const scenarios={
              draft:model('collecting_required'),
              confirmed:model('ready_to_generate',{brief:{status:'confirmed'},next_action:{type:'start_generation',label:'开始生成'}}),
              generating:model('generating',{next_action:{type:'wait',label:'正在生成'}}),
              review:model('awaiting_review',{next_action:{type:'review_stage',label:'复核阶段成果'}}),
              invalid:model('generated_invalid',{next_action:{type:'regenerate',label:'查看问题并重新生成'}}),
              documentPending:model('delivery_validation_required',{completion_gates:{content:{status:'passed'},document:{status:'running'},office:{status:'pending'}},next_action:{type:'wait_document',label:'正在生成文档'}}),
              officeFailed:model('awaiting_review',{completion_gates:{content:{status:'passed'},document:{status:'passed'},office:{status:'failed'}},delivery_status:'office_failed',next_action:{type:'repair_office',label:'处理 Office 验收问题'}}),
              delivered:model('completed',{completion_gates:{content:{status:'passed'},document:{status:'passed'},office:{status:'passed'}},delivery_status:'passed',next_action:{type:'view_result',label:'查看完整成果'}}),
            };
            console.log(JSON.stringify(scenarios));
            """
        )
    )
    assert result["draft"]["brief"]["originalRequestLabel"] == "原始诉求"
    assert result["draft"]["nextAction"]["label"] == "确认文档规格"
    assert result["confirmed"]["nextAction"]["label"] == "开始生成"
    assert result["generating"]["statusLabel"] == "AI 阶段协作正在生成"
    assert result["review"]["statusLabel"] == "阶段成果待复核"
    assert result["invalid"]["statusLabel"] == "草稿未通过校验"
    assert result["documentPending"]["gateSummary"] == "内容已确认，正在生成文档"
    assert result["officeFailed"]["gateSummary"] == "Office 验收不通过，待修改"
    assert result["delivered"]["gateSummary"] == "交付已通过"
    assert result["delivered"]["capabilityLabel"] == "企业合同试点"


def test_workspace_keeps_brief_visible_and_collapsed_capsule_keyboard_discoverable():
    for token in (
        "expert-team-brief-card",
        "原始诉求",
        "精确标题",
        "文种",
        "Brief revision",
        "查看/编辑文档规格",
        'aria-expanded="false"',
        'aria-controls="expert-team-workspace-expanded"',
        'id="expert-team-workspace-expanded"',
    ):
        assert token in EXPERT_UI_JS


def test_chat_surface_has_no_confirmation_controls_and_keeps_terminal_result_entry():
    lifecycle = _function_body(UI_JS, "function _expertTeamLifecycleCardHtml", "function renderExpertTeamLifecycleNotice")
    assert "data-expert-team-action" not in lifecycle
    assert "answerExpertTeamQuestion" not in lifecycle
    assert "右侧专家团工作台" in lifecycle
    assert "查看完整成果" in UI_JS


def _enterprise_delivery_run():
    return {
        "run_id": "run-delivery",
        "contract_version": "expert-team-contract/v1",
        "team_id": "content-creator-team",
        "workflow_state": "completed",
        "document_brief": _enterprise_brief(status="confirmed"),
        "canonical_document_ref": {
            "artifact_id": "artifact-1",
            "sha256": "a" * 64,
            "brief_revision": 3,
            "brief_sha256": "b" * 64,
        },
        "approved_stage_artifact_refs": {"review": {"artifact_id": "artifact-1", "sha256": "a" * 64}},
        "current_delivery_manifest_ref": {"delivery_binding_sha256": "c" * 64, "delivery_attempt": 2},
        "enterprise_quality_gates": {
            "brief": "passed",
            "semantic": "passed",
            "evidence": "passed",
            "asset": "passed",
            "render": "passed",
            "office": "passed",
            "delivery": "passed",
        },
        "completion_integrity": {
            "status": "passed",
            "transaction_state": "committed",
            "summary_closed": True,
        },
        "completion_transaction_ref": {"transaction_id": "tx-1", "delivery_attempt": 2},
    }


def test_document_gate_never_uses_legacy_delivery_pass_to_override_incomplete_upstream_gates():
    from api.expert_teams.view import expert_team_run_view

    for status in (None, "pending", "failed"):
        run = _enterprise_delivery_run()
        run["delivery_gate"] = {"status": "passed"}
        if status is None:
            run["enterprise_quality_gates"].pop("semantic")
        else:
            run["enterprise_quality_gates"]["semantic"] = status
        view = expert_team_run_view(run)
        assert view["completion_gates"]["document"]["status"] != "passed", status
        assert view["delivery_status"] != "passed", status


def test_failed_office_quality_gate_cannot_be_overridden_by_completion_integrity():
    from api.expert_teams.view import expert_team_run_view

    run = _enterprise_delivery_run()
    run["enterprise_quality_gates"]["office"] = "failed"
    view = expert_team_run_view(run)
    assert view["completion_gates"]["office"]["status"] == "failed"
    assert view["delivery_status"] != "passed"


def test_completion_requires_committed_transaction_bound_to_current_delivery_attempt():
    from api.expert_teams.view import expert_team_run_view

    committed = expert_team_run_view(_enterprise_delivery_run())
    assert committed["completion_gates"]["office"]["status"] == "passed"
    assert committed["delivery_status"] == "passed"

    prepared_run = _enterprise_delivery_run()
    prepared_run["completion_integrity"] = {
        "status": "reconciling",
        "transaction_state": "prepared",
        "summary_closed": False,
    }
    prepared = expert_team_run_view(prepared_run)
    assert prepared["completion_gates"]["office"]["status"] != "passed"
    assert prepared["delivery_status"] == "finalizing"

    stale_run = _enterprise_delivery_run()
    stale_run["completion_transaction_ref"] = {"transaction_id": "tx-stale", "delivery_attempt": 1}
    stale = expert_team_run_view(stale_run)
    assert stale["completion_gates"]["office"]["status"] != "passed"
    assert stale["delivery_status"] != "passed"


def test_unresolved_upstream_blocking_issues_keep_content_status_and_count_consistent():
    from api.expert_teams.view import expert_team_run_view

    run = _enterprise_delivery_run()
    run["enterprise_quality_issues"] = [
        {
            "issue_id": "semantic:unsupported-claim",
            "gate": "semantic",
            "severity": "blocking",
            "completion_blocking": True,
            "disposition": "unresolved",
        }
    ]
    view = expert_team_run_view(run)
    content = view["completion_gates"]["content"]
    assert content["blocking_issue_count"] == 1
    assert content["status"] == "failed"
    assert content["reason_code"] == "content_blocking_issues"
    assert view["delivery_status"] != "passed"


def test_brief_freezes_as_soon_as_execution_or_first_stage_reservation_starts():
    from api.expert_teams.view import expert_team_run_view

    cases = (
        {"workflow_state": "starting"},
        {"workflow_state": "generating"},
        {
            "workflow_state": "ready_to_generate",
            "current_stage_attempt_reservation": {
                "reservation_id": "stage-attempt-1",
                "stage_id": "draft",
                "stage_attempt": 1,
                "executor": "model",
                "status": "reserved",
            },
        },
    )
    for extra in cases:
        run = {
            "run_id": "run-freeze",
            "contract_version": "expert-team-contract/v1",
            "team_id": "content-creator-team",
            "document_brief": _enterprise_brief(status="confirmed"),
            "stage_outputs": [],
            **extra,
        }
        brief = expert_team_run_view(run)["brief"]
        assert brief["editable"] is False, extra
        assert brief["edit_policy"] == "new_run_required", extra


def test_capsule_aria_expanded_is_updated_by_the_same_workspace_toggle_result():
    for token in (
        "function setExpertTeamCapsuleExpanded",
        "function showExpertTeamWorkspaceFromCapsule",
        "function toggleExpertTeamWorkspaceFromControl",
        "setExpertTeamCapsuleExpanded(trigger,expanded)",
        "showExpertTeamWorkspaceFromCapsule(this)",
        "toggleExpertTeamWorkspaceFromControl(this)",
    ):
        assert token in EXPERT_UI_JS


def test_task_result_process_tabs_top_gates_and_collapsed_capsule_match_plan_a():
    for token in (
        'data-expert-team-workspace-tab="task"',
        'data-expert-team-workspace-tab="result"',
        'data-expert-team-workspace-tab="process"',
        '<span>任务</span>',
        '<span>成果</span>',
        '<span>过程</span>',
        'expert-team-completion-gates',
        '内容确认',
        'DOCX 生成',
        'Office 验收',
        'expert-team-capsule-todo-count',
        'aria-expanded="false"',
        'aria-controls="expert-team-workspace-expanded"',
    ):
        assert token in EXPERT_UI_JS


def test_brief_editor_groups_fields_labels_original_request_and_explains_freeze():
    for token in (
        'data-expert-team-brief-editor',
        '<legend>文档目标</legend>',
        '<legend>使用与资料边界</legend>',
        '<legend>交付控制</legend>',
        '>原始诉求<',
        '这是创建任务时的原始要求，可在首阶段启动前修正。',
        'aria-describedby=',
        'data-expert-team-field-error',
        '基于当前规格创建新任务',
        '首阶段已经启动，整份文档规格已冻结',
    ):
        assert token in EXPERT_UI_JS
    rendered = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');
            const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-ui.js','utf8'),context);
            const html=context.window.renderExpertTeamWorkspaceFromPresentation({
              runId:'run-1',schemaVersion:2,version:2,presentation:{state:'collecting_required'},
              brief:{editable:true,revision:2,originalRequest:'原始诉求',exactTitle:'标题',documentTypeLabel:'工作汇报',documentControl:{},validation:{}},
              workflow:{currentStage:{},stages:[],progress:{}},workspace:{},members:[],
            });
            console.log(JSON.stringify({original:html.includes('name="original_request"'),context:html.includes('name="additional_context"')}));
            """
        )
    )
    assert rendered == {'original': True, 'context': True}


def test_brief_mutations_are_separate_from_generation_and_handle_field_and_revision_errors():
    for token in (
        '/api/expert-teams/brief/update',
        '/api/expert-teams/brief/confirm',
        'function collectExpertTeamBriefPayload',
        'function focusFirstExpertTeamBriefError',
        'brief_revision_conflict',
        '文档规格已确认，请点击“开始生成”继续。',
    ):
        assert token in ACTIONS_JS
    brief_body = _function_body(
        ACTIONS_JS,
        'async function submitExpertTeamBrief',
        'function expertTeamIdentityCapability',
    )
    assert 'start_generation' not in brief_body
    assert '/api/expert-teams/resume' not in brief_body
    assert 'focusFirstExpertTeamBriefError' in brief_body


def test_brief_confirmation_runtime_saves_then_confirms_without_starting_generation():
    result = _run_node(
        _actions_harness(
            """
            const calls=[];
            context.api=async(path,opts)=>{
              const payload=JSON.parse(opts.body);calls.push({path,payload});
              if(path.endsWith('/update'))return {run:{run_id:'run-1',version:8,document_brief:{revision:4}}};
              return {run:{run_id:'run-1',version:9,workflow_state:'ready_to_generate',document_brief:{revision:4,status:'confirmed'}}};
            };
            context.window._activeExpertTeamStatusCard={
              runId:'run-1',sourceSessionId:'session-1',schemaVersion:2,version:7,currentStageId:'intake',readOnly:false,
              brief:{revision:3},
            };
            const controls=[
              {name:'original_request',value:'原始诉求',disabled:false},
              {name:'exact_title',value:'精确标题',disabled:false},
              {name:'additional_context',value:'补充背景',disabled:false},
            ];
            const form={dataset:{expertTeamBriefRevision:'3'},querySelectorAll:()=>controls,querySelector:()=>null};
            const root={dataset:{expertTeamRunId:'run-1',expertTeamSchemaVersion:'2',expertTeamVersion:'7',expertTeamStageId:'intake',expertTeamReadOnly:'false'}};
            const button={disabled:false,isConnected:true,textContent:'确认文档规格',setAttribute:()=>{},removeAttribute:()=>{},closest:(selector)=>selector==='[data-expert-team-brief-editor]'?form:root};
            context.window.submitExpertTeamBrief(button,true).then(ok=>console.log(JSON.stringify({ok,calls})));
            """
        )
    )
    assert result['ok'] is True
    assert [call['path'] for call in result['calls']] == [
        '/api/expert-teams/brief/update',
        '/api/expert-teams/brief/confirm',
    ]
    assert result['calls'][0]['payload']['patch'] == {
        'original_request': '原始诉求',
        'exact_title': '精确标题',
        'additional_context': '补充背景',
    }
    assert result['calls'][1]['payload']['expected_brief_revision'] == 4
    for call in result['calls']:
        assert not ({'token', 'principal', 'role', 'roles'} & set(call['payload']))


def test_brief_confirmation_without_dirty_fields_skips_update_and_uses_current_revision():
    result = _run_node(
        _actions_harness(
            """
            const calls=[];
            context.api=async(path,opts)=>{
              calls.push({path,payload:JSON.parse(opts.body)});
              return {run:{run_id:'run-1',version:8,workflow_state:'ready_to_generate',document_brief:{revision:3,status:'confirmed'}}};
            };
            context.window._activeExpertTeamStatusCard={
              runId:'run-1',sourceSessionId:'session-1',schemaVersion:2,version:7,currentStageId:'intake',readOnly:false,
              brief:{revision:3},
            };
            const controls=[
              {name:'original_request',value:'原始诉求',disabled:false},
              {name:'exact_title',value:'精确标题',disabled:false},
            ];
            const form={
              dataset:{expertTeamBriefRevision:'3',expertTeamBriefSnapshot:JSON.stringify({original_request:'原始诉求',exact_title:'精确标题'})},
              querySelectorAll:()=>controls,querySelector:()=>null,
            };
            const root={dataset:{expertTeamRunId:'run-1',expertTeamSchemaVersion:'2',expertTeamVersion:'7',expertTeamStageId:'intake',expertTeamReadOnly:'false'}};
            const button={disabled:false,isConnected:true,textContent:'确认文档规格',setAttribute:()=>{},removeAttribute:()=>{},closest:(selector)=>selector==='[data-expert-team-brief-editor]'?form:root};
            context.window.submitExpertTeamBrief(button,true).then(ok=>console.log(JSON.stringify({ok,calls})));
            """
        )
    )
    assert result['ok'] is True
    assert [call['path'] for call in result['calls']] == ['/api/expert-teams/brief/confirm']
    assert result['calls'][0]['payload']['expected_brief_revision'] == 3


def test_brief_control_patch_preserves_non_rendered_enterprise_metadata():
    result = _run_node(
        _actions_harness(
            """
            const form={
              dataset:{expertTeamDocumentControl:JSON.stringify({render_template_id:'enterprise-work-report',client_unit:'客户单位',compiler_unit:'编制单位',classification:'internal'})},
              querySelectorAll:()=>[{name:'document_control.classification',value:'restricted',disabled:false}],
            };
            console.log(JSON.stringify(context.window.collectExpertTeamBriefPayload(form)));
            """
        )
    )
    assert result['document_control'] == {
        'render_template_id': 'enterprise-work-report',
        'client_unit': '客户单位',
        'compiler_unit': '编制单位',
        'classification': 'restricted',
    }


def test_brief_revision_conflict_restores_only_dirty_fields_against_authoritative_run():
    result = _run_node(
        _actions_harness(
            """
            const events=[];
            const controls={
              exact_title:{name:'exact_title',value:'本地草稿',disabled:false,setAttribute:()=>{},removeAttribute:()=>{},focus:()=>{}},
              purpose:{name:'purpose',value:'服务端并发新用途',disabled:false,setAttribute:()=>{},removeAttribute:()=>{},focus:()=>{}},
            };
            const form={
              dataset:{expertTeamBriefRevision:'3',expertTeamBriefSnapshot:JSON.stringify({exact_title:'旧标题',purpose:'服务端并发新用途'})},
              querySelectorAll:()=>Object.values(controls),
              querySelector:(selector)=>selector.includes('exact_title')?controls.exact_title:(selector.includes('purpose')?controls.purpose:null),
            };
            context.document.getElementById=()=>form;
            context._expertTeamStatusCardFromRun=(run)=>({runId:run.run_id});
            context.renderExpertTeamStatusSurface=(card)=>{
              events.push(['render',card.runId]);
              controls.exact_title.value='服务端标题';
              controls.purpose.value='服务端并发新用途';
            };
            context.api=()=>{const error=new Error('规格已被更新');error.status=409;error.payload={code:'brief_revision_conflict',field:'expected_brief_revision',run:{run_id:'run-1',version:8,workflow_state:'collecting_required'}};return Promise.reject(error);};
            context.window._activeExpertTeamStatusCard={runId:'run-1',sourceSessionId:'session-1',schemaVersion:2,version:7,currentStageId:'intake',readOnly:false,brief:{revision:3}};
            const root={dataset:{expertTeamRunId:'run-1',expertTeamSchemaVersion:'2',expertTeamVersion:'7',expertTeamStageId:'intake',expertTeamReadOnly:'false'}};
            const button={disabled:false,isConnected:true,textContent:'保存规格',setAttribute:()=>{},removeAttribute:()=>{},closest:(selector)=>selector==='[data-expert-team-brief-editor]'?form:root};
            context.window.submitExpertTeamBrief(button,false).then(ok=>console.log(JSON.stringify({ok,events,title:controls.exact_title.value,purpose:controls.purpose.value})));
            """
        )
    )
    assert result == {
        'ok': False,
        'events': [['render', 'run-1']],
        'title': '本地草稿',
        'purpose': '服务端并发新用途',
    }


def test_identity_flow_is_discoverable_fail_closed_and_never_persists_credentials():
    for token in (
        '/api/expert-teams/identity/status',
        '/api/expert-teams/identity/start',
        '/api/expert-teams/identity/logout',
        '使用企业审批身份登录',
        '未配置企业身份提供方',
        '登录已取消',
        '企业身份已过期',
        '当前身份缺少文档审批权限',
        'window.open',
        'document-approver',
    ):
        assert token in ACTIONS_JS or token in EXPERT_UI_JS
    assert 'localStorage.setItem' not in ACTIONS_JS
    assert 'token:' not in ACTIONS_JS
    assert "body:JSON.stringify({redirect_uri:redirectUri,purpose:'login'})" in ACTIONS_JS
    assert "body:'{}'" in ACTIONS_JS
    assert "require('electron')" not in ACTIONS_JS
    assert 'require("electron")' not in ACTIONS_JS
    mutation_body = _function_body(ACTIONS_JS, 'async function submitExpertTeamBrief', 'function expertTeamIdentityCapability')
    assert 'principal' not in mutation_body
    assert 'roles' not in mutation_body


def test_identity_capability_runtime_is_fail_closed_for_provider_login_expiry_and_role():
    result = _run_node(
        _actions_harness(
            """
            const capability=context.window.expertTeamIdentityCapability;
            console.log(JSON.stringify({
              noProvider:capability({enabled:false}),
              signedOut:capability({enabled:true,authenticated:false}),
              expired:capability({enabled:true,authenticated:true,expired:true,principal:{roles:['document-approver']}}),
              wrongRole:capability({enabled:true,authenticated:true,principal:{display_name:'王工',roles:['office-reviewer']}}),
              approved:capability({enabled:true,authenticated:true,principal:{display_name:'李工',roles:['document-approver']}}),
            }));
            """
        )
    )
    assert result['noProvider']['allowed'] is False
    assert result['signedOut']['allowed'] is False
    assert result['expired']['allowed'] is False
    assert result['wrongRole']['allowed'] is False
    assert result['approved'] == {'allowed': True, 'label': '李工'}


def test_identity_runtime_handles_success_cancel_callback_failure_timeout_logout_and_focus_return():
    result = _run_node(
        _actions_harness(
            """
            context.setTimeout=(fn)=>{fn();return 1;};
            context.window.location={origin:'http://127.0.0.1:18787'};
            function button(){return {disabled:false,isConnected:true,textContent:'登录',focusCount:0,setAttribute:()=>{},removeAttribute:()=>{},focus(){this.focusCount+=1;}};}
            async function loginScenario(kind){
              const btn=button();let statusCalls=0;
              context.window.open=()=>null;
              context.api=async(path)=>{
                if(path.endsWith('/start'))return {authorization_url:'https://login.example.test/authorize'};
                if(path.endsWith('/status')){
                  statusCalls+=1;
                  if(kind==='cancel'&&statusCalls===1)context.window.cancelExpertTeamIdentityLogin(btn);
                  if(kind==='callbackFailure')throw new Error('callback failed');
                  if(kind==='success'&&statusCalls===2)return {enabled:true,authenticated:true,principal:{display_name:'李工',roles:['document-approver']}};
                  return {enabled:true,authenticated:false};
                }
                throw new Error('unexpected '+path);
              };
              const ok=await context.window.startExpertTeamIdentityLogin(btn);
              return {ok,statusCalls,focusCount:btn.focusCount};
            }
            (async()=>{
              const success=await loginScenario('success');
              const cancel=await loginScenario('cancel');
              const callbackFailure=await loginScenario('callbackFailure');
              const timeout=await loginScenario('timeout');
              const logoutButton=button();const logoutCalls=[];
              context.api=async(path,opts)=>{logoutCalls.push({path,body:opts.body});return {ok:true};};
              const logout=await context.window.logoutExpertTeamIdentity(logoutButton);
              console.log(JSON.stringify({success,cancel,callbackFailure,timeout,logout:{ok:logout,focusCount:logoutButton.focusCount,calls:logoutCalls}}));
            })();
            """
        )
    )
    assert result['success'] == {'ok': True, 'statusCalls': 2, 'focusCount': 1}
    assert result['cancel']['ok'] is False and result['cancel']['focusCount'] == 1
    assert result['callbackFailure']['ok'] is False and result['callbackFailure']['focusCount'] == 1
    assert result['timeout']['ok'] is False and result['timeout']['statusCalls'] == 120 and result['timeout']['focusCount'] == 1
    assert result['logout'] == {
        'ok': True,
        'focusCount': 1,
        'calls': [{'path': '/api/expert-teams/identity/logout', 'body': '{}'}],
    }


def test_identity_external_browser_launch_never_depends_on_popup_handle_or_closed_state():
    start = _function_body(
        ACTIONS_JS,
        'async function startExpertTeamIdentityLogin',
        'async function logoutExpertTeamIdentity',
    )
    assert 'window.open' in start
    assert 'if(!popup)' not in start
    assert 'popup.closed' not in start
    assert 'cancelExpertTeamIdentityLogin' in ACTIONS_JS
    assert 'data-expert-team-identity-action="cancel"' in EXPERT_UI_JS


def test_dynamic_brief_error_merges_and_clears_aria_describedby_without_losing_help():
    result = _run_node(
        _actions_harness(
            """
            const attrs={'aria-describedby':'title-help'};
            const control={focusCount:0,setAttribute:(key,value)=>{attrs[key]=value;},removeAttribute:(key)=>{delete attrs[key];},getAttribute:(key)=>attrs[key]||'',focus(){this.focusCount+=1;}};
            const error={id:'title-error',textContent:''};
            const form={querySelector:(selector)=>selector.startsWith('[name=')?control:(selector.startsWith('[data-expert-team-field-error=')?error:null),querySelectorAll:(selector)=>selector==='[data-expert-team-field-error]'?[error]:(selector==='[aria-invalid="true"]'?[control]:[])};
            const focused=context.window.focusFirstExpertTeamBriefError(form,'exact_title','请填写精确标题');
            const afterFocus={focused,describedby:attrs['aria-describedby'],invalid:attrs['aria-invalid'],message:error.textContent,focusCount:control.focusCount};
            context.window.clearExpertTeamBriefErrors(form);
            console.log(JSON.stringify({afterFocus,afterClear:{describedby:attrs['aria-describedby'],invalid:attrs['aria-invalid']||'',message:error.textContent}}));
            """
        )
    )
    assert result['afterFocus'] == {
        'focused': True,
        'describedby': 'title-help title-error',
        'invalid': 'true',
        'message': '请填写精确标题',
        'focusCount': 1,
    }
    assert result['afterClear'] == {'describedby': 'title-help', 'invalid': '', 'message': ''}


def test_brief_payload_contains_only_dirty_fields_against_authoritative_snapshot():
    result = _run_node(_actions_harness("""
      const form={dataset:{expertTeamBriefSnapshot:JSON.stringify({original_request:'原始诉求',exact_title:'旧标题',purpose:'审议'}),expertTeamDocumentControl:JSON.stringify({render_template_id:'enterprise-work-report',classification:'internal'})},querySelectorAll:()=>[
        {name:'original_request',value:'原始诉求',disabled:false},{name:'exact_title',value:'新标题',disabled:false},{name:'purpose',value:'审议',disabled:false},{name:'document_control.classification',value:'internal',disabled:false},
      ]};
      console.log(JSON.stringify(context.window.collectExpertTeamBriefPayload(form)));
    """))
    assert result == {'exact_title': '新标题'}


def test_brief_confirm_failure_after_committed_update_reports_saved_but_unconfirmed():
    result = _run_node(_actions_harness("""
      const toasts=[];context.showToast=(message)=>toasts.push(message);
      let calls=0;context.api=async(path)=>{calls+=1;if(path.endsWith('/update'))return {run:{run_id:'run-1',version:8,document_brief:{revision:4}}};throw new Error('confirm offline');};
      context.window._activeExpertTeamStatusCard={runId:'run-1',sourceSessionId:'session-1',schemaVersion:2,version:7,currentStageId:'intake',readOnly:false,brief:{revision:3}};
      const form={dataset:{expertTeamBriefRevision:'3',expertTeamBriefSnapshot:'{}'},querySelectorAll:()=>[{name:'exact_title',value:'新标题',disabled:false}],querySelector:()=>null};
      const root={dataset:{expertTeamRunId:'run-1',expertTeamSchemaVersion:'2',expertTeamVersion:'7',expertTeamStageId:'intake',expertTeamReadOnly:'false'}};
      const button={disabled:false,isConnected:true,textContent:'确认',setAttribute:()=>{},removeAttribute:()=>{},closest:(selector)=>selector==='[data-expert-team-brief-editor]'?form:root};
      context.window.submitExpertTeamBrief(button,true).then(ok=>console.log(JSON.stringify({ok,calls,toasts})));
    """))
    assert result['ok'] is False and result['calls'] == 2
    assert any('规格已保存，但确认未完成' in message for message in result['toasts'])


def test_logout_aborts_old_identity_attempt_and_stale_authenticated_response_is_ignored():
    result = _run_node(_actions_harness("""
      context.setTimeout=(fn)=>{fn();return 1;};context.window.location={origin:'http://127.0.0.1:18787'};context.window.open=()=>null;
      let resolveOld;let statusRequested=false;
      context.api=async(path)=>{if(path.endsWith('/start'))return {authorization_url:'https://login.example.test/authorize'};if(path.endsWith('/status')){statusRequested=true;return new Promise(resolve=>{resolveOld=resolve;});}if(path.endsWith('/logout'))return {ok:true};throw new Error(path);};
      function button(action){return {disabled:false,isConnected:true,textContent:'身份',dataset:{expertTeamIdentityAction:action},focusCount:0,setAttribute:()=>{},removeAttribute:()=>{},focus(){this.focusCount+=1;}};}
      (async()=>{const loginButton=button('login');const logoutButton=button('logout');const login=context.window.startExpertTeamIdentityLogin(loginButton);while(!statusRequested)await Promise.resolve();const logout=await context.window.logoutExpertTeamIdentity(logoutButton);resolveOld({enabled:true,authenticated:true,principal:{display_name:'旧身份',roles:['document-approver']}});await login;console.log(JSON.stringify({logout,identity:context.window._expertTeamIdentityStatus,loginFocus:loginButton.focusCount,logoutFocus:logoutButton.focusCount}));})();
    """))
    assert result['logout'] is True
    assert result['identity']['authenticated'] is False
    assert result['loginFocus'] == 0
    assert result['logoutFocus'] == 1


def test_both_workspace_expand_controls_publish_and_sync_the_same_aria_state():
    assert 'expert-team-panel-collapse-toggle"' in EXPERT_UI_JS
    collapse_markup = EXPERT_UI_JS[EXPERT_UI_JS.index('expert-team-panel-collapse-toggle"') :]
    assert 'aria-expanded="true"' in collapse_markup
    assert 'aria-controls="expert-team-workspace-expanded"' in collapse_markup
    sync = _function_body(EXPERT_UI_JS, 'function setExpertTeamCapsuleExpanded', 'function showExpertTeamWorkspaceFromCapsule')
    assert "querySelectorAll('[aria-controls=\"expert-team-workspace-expanded\"]')" in sync


def test_stage_approval_requires_valid_approver_and_zero_unresolved_warnings():
    source = textwrap.dedent(
        """
        const fs=require('fs');
        const vm=require('vm');
        const context={window:{},console};
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/expert-team-ui.js','utf8'),context);
        function render(identityStatus,validation){
          return context.window.renderExpertTeamWorkspaceFromPresentation({
            runId:'run-1',schemaVersion:2,version:7,currentStageId:'review',stageReviewId:'review-1',
            presentation:{state:'awaiting_review',title:'待复核',secondaryActions:[
              {id:'view_result',label:'查看本阶段成果'},{id:'approve_stage',label:'确认并继续'},{id:'revise_stage',label:'需要修改'}
            ]},
            workflow:{currentStage:{id:'review',phase:'复核交付'},stages:[],progress:{}},workspace:{},members:[],
            identityStatus,artifactValidation:validation,stageResult:{summary:'待复核'},
          });
        }
        const scenarios={
          disabled:render({enabled:false,authenticated:false},{unresolved_warning_count:0}),
          missing:render({enabled:true,authenticated:false},{unresolved_warning_count:0}),
          wrongRole:render({enabled:true,authenticated:true,principal:{display_name:'王工',roles:['office-reviewer']}},{unresolved_warning_count:0}),
          approver:render({enabled:true,authenticated:true,principal:{display_name:'李工',roles:['document-approver']}},{unresolved_warning_count:0}),
          warning:render({enabled:true,authenticated:true,principal:{display_name:'李工',roles:['document-approver']}},{unresolved_warning_count:1}),
        };
        function state(html){
          const match=html.match(/data-expert-team-action="approve_stage"[^>]*>/);
          return {button:match&&match[0],hasReason:/审批身份|阻断问题|warning|警告/.test(html),waiver:/申请授权/.test(html)};
        }
        console.log(JSON.stringify(Object.fromEntries(Object.entries(scenarios).map(([key,html])=>[key,state(html)]))));
        """
    )
    result = _run_node(source)
    assert 'disabled' in result['disabled']['button']
    assert 'disabled' in result['missing']['button']
    assert 'disabled' in result['wrongRole']['button']
    assert 'disabled' not in result['approver']['button']
    assert 'disabled' in result['warning']['button']
    assert result['warning']['waiver'] is False


def test_chat_keeps_conclusion_and_full_result_but_never_embeds_brief_or_review_mutations():
    lifecycle = _function_body(UI_JS, 'function _expertTeamLifecycleCardHtml', 'function renderExpertTeamLifecycleNotice')
    assert 'data-expert-team-action' not in lifecycle
    assert 'brief/confirm' not in lifecycle
    assert 'approve_stage' not in lifecycle
    assert '查看完整成果' in UI_JS
    assert '下一步' in UI_JS
