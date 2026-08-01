from pathlib import Path
from io import BytesIO
import json
import subprocess
import textwrap
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
SCRIPT = ROOT / "static" / "expert-team-v3.js"
MESSAGES = ROOT / "static" / "messages.js"
STYLE = ROOT / "static" / "expert-team-v3.css"
PANELS = ROOT / "static" / "panels.js"
ELECTRON_SMOKE = ROOT / "tests" / "expert_team_v3_electron_smoke.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_node(source: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_v3_hooks(body: str) -> dict:
    return _run_node(
        textwrap.dedent(
            f"""
            const fs=require('fs');const vm=require('vm');
            const context={{window:{{}},document:{{readyState:'loading',addEventListener(){{}},getElementById(){{return null;}}}},console}};
            vm.createContext(context);
            let source=fs.readFileSync('static/expert-team-v3.js','utf8');
            source=source.replace(
              'window.ExpertTeamV3 = Object.freeze({{',
              `window.__expertTeamV3TestHooks = {{
                stageBindingFingerprint, conflictDraftMatches, restoreConflictRevisionDraft,
              deliveryBindingFingerprint, conflictDeliveryDraftMatches, restoreConflictDeliveryDraft,
              draftFingerprint, captureWorkbenchDraft, restoreWorkbenchDraft, stateCopyFor, statePanel, workbenchHtml,
              classifyDocumentTaskPrompt,
              requestV3Confirmation,
              replacementExample,
              returnSuggestionToComposer, continueRegularChat,
              clearSuggestionComposerAfterLaunch,
              resumePanel, reviewDocumentHtml,
              handleWorkbenchClick, deliveryActionControl, applyResponse,
                expertTeamDiagnosticText, copyExpertTeamDiagnostics,
                deferStatusRenderDuringComposition, releaseDeferredStatusCard,
                bindWorkbenchEvents, renderStatusSurface,
                briefPanel, buildBriefPatch, clientBriefFieldErrors,
                showBriefFieldErrors,
                setConflictDraft(value) {{ state.conflictRevisionDraft = value; }},
                setConflictDeliveryDraft(value) {{ state.conflictDeliveryDraft = value; }},
                setCard(value) {{ state.card = value; state.busy = false; }},
                getCard() {{ return state.card; }},
                setCatalog(value) {{ state.catalog = value; state.catalogStatus = 'ready'; }},
                getRelaunchState() {{
                  return {{
                    teamId: String(state.selectedTeam?.id || ''),
                    exampleId: String(state.selectedExample?.id || ''),
                    suggestionMode: state.suggestionMode,
                    prompt: state.suggestedPrompt,
                  }};
                }},
                setCompositionActive(value) {{ state.compositionActive = value; }},
                setSuggestion(value, prompt, returnFocus, sourceSessionId) {{
                  state.suggestionMode = Boolean(value);
                  state.suggestedPrompt = String(prompt || '');
                  state.dialogReturnFocus = returnFocus || null;
                  state.suggestedSourceSessionId = String(sourceSessionId || '');
                }},
              }};\n  window.ExpertTeamV3 = Object.freeze({{`
            );
            vm.runInContext(source,context);
            const hooks=context.window.__expertTeamV3TestHooks;
            {body}
            """
        )
    )


def test_v3_assets_are_loaded_after_existing_shell_modules():
    index = _read(INDEX)

    assert 'id="expertTeamV3PortalRoot"' in index
    assert 'static/expert-team-v3.css?v=__WEBUI_VERSION__' in index
    assert 'static/expert-team-v3.js?v=__WEBUI_VERSION__' in index
    assert index.index("static/panels.js") < index.index("static/expert-team-v3.js")


def test_v3_owns_one_scoped_namespace_and_uses_delegated_events():
    script = _read(SCRIPT)

    assert "window.ExpertTeamV3" in script
    assert "new AbortController" in script
    assert "addEventListener('click'" in script or 'addEventListener("click"' in script
    assert "onclick=" not in script
    assert "window._activeExpertTeamStatusCard" not in script
    assert "writeflowStatusDock" not in script


def test_v3_source_removal_uses_accessible_fail_closed_confirmation():
    result = _run_v3_hooks(
        """
        (async()=>{
          const calls=[];
          context.window.showConfirmDialog=async options=>{calls.push(options);return true;};
          const confirmed=await hooks.requestV3Confirmation({
            title:'移除这份资料？',confirmLabel:'移除资料',cancelLabel:'保留资料',danger:true,
          });
          delete context.window.showConfirmDialog;
          const unavailable=await hooks.requestV3Confirmation({title:'unavailable'});
          context.window.showConfirmDialog=async()=>{throw new Error('dialog failed');};
          const failed=await hooks.requestV3Confirmation({title:'failed'});
          console.log(JSON.stringify({confirmed,unavailable,failed,calls}));
        })();
        """
    )

    assert result == {
        "confirmed": True,
        "unavailable": False,
        "failed": False,
        "calls": [
            {
                "title": "移除这份资料？",
                "confirmLabel": "移除资料",
                "cancelLabel": "保留资料",
                "danger": True,
                "focusCancel": True,
            }
        ],
    }

    script = _read(SCRIPT)
    remove_source = script[
        script.index("if (action === 'remove-source')") : script.index("if (action === 'save-brief')")
    ]
    assert "await requestV3Confirmation" in remove_source
    assert "window.confirm" not in remove_source
    assert "cancelLabel: '保留资料'" in remove_source
    assert "danger: true" in remove_source


def test_v3_free_form_document_intent_suggests_a_confirmable_task_without_hijacking_questions():
    result = _run_v3_hooks(
        """
        const prompts=[
          '请帮我写一份方案，主题是提升营业厅服务质效',
          '请起草一份部门月度工作汇报',
          '整理一次供电服务专题会议纪要',
          '起草近期安全生产检查通知',
          '形成阶段性工作总结和下一步计划',
          '请帮我润色这份办公材料',
          '编制一份本地 AI 助理专题研究报告',
          '请问怎么写一份方案',
          '分析一下这份方案的问题',
          '请总结一下这段对话',
        ];
        console.log(JSON.stringify(prompts.map(prompt=>hooks.classifyDocumentTaskPrompt(prompt)?.launchProfileId||null)));
        """
    )

    assert result == [
        "content-plan",
        "content-work-report",
        "content-meeting-minutes",
        "content-notice",
        "content-summary-plan",
        "content-polish",
        "research-report",
        None,
        None,
        None,
    ]


def test_v3_free_form_suggestion_requires_visible_confirmation_change_or_regular_chat_choice():
    script = _read(SCRIPT)
    messages = _read(MESSAGES)

    for text in (
        "已识别为",
        "更换文档任务",
        "系统不会未经确认自动发起",
        'data-et3-action="continue-regular-chat"',
        "继续普通对话",
        "suggestFromPrompt",
    ):
        assert text in script
    assert "suggestFromPrompt" in script[script.index("window.ExpertTeamV3") :]
    send_body = messages[messages.index("async function send()") : messages.index("const LIVE_STREAMS")]
    assert "options.skipExpertTeamSuggestion" in send_body
    assert "window.ExpertTeamV3.suggestFromPrompt(text)" in send_body
    suggestion_at = send_body.index("window.ExpertTeamV3.suggestFromPrompt(text)")
    normal_session_at = send_body.index(
        "if(!S.session){await newSession();await renderSessionList();}", suggestion_at
    )
    assert suggestion_at < normal_session_at


def test_v3_free_form_exit_paths_restore_the_visible_chat_composer_before_sending():
    result = _run_v3_hooks(
        """
        (async()=>{
          const calls=[];
          const composer={value:'',isConnected:true,focus(){calls.push('focus');}};
          const promptField={value:'用户修改后的方案需求'};
          const backdrop={hidden:false};
          const portal={inert:true};
          const root={querySelector(selector){
            if(selector==='[data-et3-dialog-backdrop]')return backdrop;
            if(selector==='.et3-portal')return portal;
            return null;
          }};
          context.document.getElementById=id=>{
            if(id==='expertTeamV3PortalRoot')return root;
            if(id==='expertTeamV3Prompt')return promptField;
            if(id==='msg')return composer;
            return null;
          };
          context.switchPanel=async panel=>{calls.push(`panel:${panel}`);};
          context.autoResize=()=>{calls.push('resize');};
          context.window.send=async options=>{calls.push(`send:${Boolean(options&&options.skipExpertTeamSuggestion)}`);return true;};

          hooks.setSuggestion(true,'原始方案需求',composer);
          const returned=await hooks.returnSuggestionToComposer();
          const first={returned,value:composer.value,backdropHidden:backdrop.hidden,portalInert:portal.inert,calls:[...calls]};

          calls.length=0;backdrop.hidden=false;portal.inert=true;promptField.value='继续普通聊天的方案需求';
          hooks.setSuggestion(true,'原始方案需求',composer);
          const sent=await hooks.continueRegularChat();
          console.log(JSON.stringify({first,second:{sent,value:composer.value,backdropHidden:backdrop.hidden,portalInert:portal.inert,calls}}));
        })();
        """
    )

    assert result["first"]["returned"] == "用户修改后的方案需求"
    assert result["first"]["value"] == "用户修改后的方案需求"
    assert result["first"]["backdropHidden"] is True
    assert result["first"]["portalInert"] is False
    assert "panel:chat" in result["first"]["calls"]
    assert result["second"]["sent"] is True
    assert result["second"]["value"] == "继续普通聊天的方案需求"
    assert result["second"]["backdropHidden"] is True
    assert result["second"]["portalInert"] is False
    assert result["second"]["calls"].index("panel:chat") < result["second"]["calls"].index("send:true")


def test_v3_successful_suggestion_launch_consumes_the_original_composer_and_draft_once():
    result = _run_v3_hooks(
        """
        const calls=[];
        const composer={value:'请帮我写一份方案'};
        context.document.getElementById=id=>id==='msg'?composer:null;
        context._clearComposerDraft=sessionId=>calls.push(`clear:${sessionId}`);
        context.autoResize=()=>calls.push('resize');
        context.updateSendBtn=()=>calls.push('button');
        hooks.setSuggestion(true,'请帮我写一份方案',null,'source-session-1');
        hooks.clearSuggestionComposerAfterLaunch();
        console.log(JSON.stringify({value:composer.value,calls}));
        """
    )

    assert result == {
        "value": "",
        "calls": ["clear:source-session-1", "resize", "button"],
    }

    script = _read(SCRIPT)
    summon = script[script.index("async function summon(") : script.index("async function loadCatalog(")]
    success = summon[summon.index("if (started)") :]
    assert "launchedFromSuggestion" in summon
    assert "clearSuggestionComposerAfterLaunch()" in success
    assert success.index("clearSuggestionComposerAfterLaunch()") < success.index("closeDialog()")


def test_v3_defers_only_the_latest_same_run_card_while_ime_is_composing():
    result = _run_v3_hooks(
        """
        const base={kind:'expert_team',runId:'run-1',sourceSessionId:'session-1',version:4};
        hooks.setCard(base);
        hooks.setCompositionActive(true);
        const first=hooks.deferStatusRenderDuringComposition({...base,version:5});
        const older=hooks.deferStatusRenderDuringComposition({...base,version:3});
        const other=hooks.deferStatusRenderDuringComposition({...base,runId:'run-2',version:9});
        const released=hooks.releaseDeferredStatusCard();
        console.log(JSON.stringify({first,older,other,released}));
        """
    )

    assert result == {
        "first": True,
        "older": True,
        "other": False,
        "released": {"kind": "expert_team", "runId": "run-1", "sourceSessionId": "session-1", "version": 5},
    }


def test_v3_workbench_binds_composition_events_and_releases_after_final_input_event():
    script = _read(SCRIPT)

    assert "compositionActive" in script
    assert "deferredCard" in script
    assert "root.addEventListener('compositionstart'" in script
    assert "root.addEventListener('compositionend'" in script
    assert "setTimeout(() =>" in script
    assert "renderStatusSurface(deferred)" in script


def test_v3_ime_composition_defers_poll_renders_and_restores_final_input_focus_selection_and_scroll():
    result = _run_v3_hooks(
        """
        const queued=[];
        context.setTimeout=(callback)=>{queued.push(callback);return queued.length;};
        context.AbortController=AbortController;
        context.window.S={session:{session_id:'session-1'}};
        const listeners={};
        let replaced=0;
        let useNewControls=false;
        const oldScroll={scrollTop:318};
        const newScroll={scrollTop:0};
        const oldControl={
          id:'et3-brief-purpose',type:'textarea',tagName:'TEXTAREA',dataset:{et3BriefPath:'purpose'},
          value:'候选词尚未确认',checked:false,selectionStart:3,selectionEnd:6,
          closest(){return this;},
        };
        const newControl={
          id:'et3-brief-purpose',type:'textarea',tagName:'TEXTAREA',dataset:{et3BriefPath:'purpose'},
          value:'',checked:false,focusOptions:null,selection:null,
          focus(options){this.focusOptions=options;context.document.activeElement=this;},
          setSelectionRange(start,end){this.selection=[start,end];},
        };
        const root={
          id:'expertTeamV3Workbench',dataset:{},parentElement:null,
          classList:{toggle(){}},
          addEventListener(name,handler){listeners[name]=handler;},
          querySelectorAll(){return useNewControls?[newControl]:[oldControl];},
          querySelector(selector){
            if(selector==='.et3-workbench-scroll')return useNewControls?newScroll:oldScroll;
            return null;
          },
          set innerHTML(value){this._innerHTML=value;replaced+=1;useNewControls=true;},
          get innerHTML(){return this._innerHTML||'';},
        };
        const host={appendChild(node){node.parentElement=this;}};
        root.parentElement=host;
        const main={parentElement:host};
        let domReads=0;
        context.document.activeElement=oldControl;
        context.document.body={classList:{add(){},toggle(){},remove(){}}};
        context.document.querySelector=()=>null;
        context.document.getElementById=(id)=>{
          domReads+=1;
          if(id==='mainChat')return main;
          if(id==='expertTeamV3Workbench')return root;
          return null;
        };
        const base={
          kind:'expert_team',runId:'run-1',sourceSessionId:'session-1',version:4,
          productMode:'standalone',readOnly:false,publicState:'intake',allowedActions:['answer'],
          questions:[],presentation:{visibleTitle:'输入法测试'},team:{title:'内容创作专家团'},
          progress:{done:0,total:5},workflow:{currentStage:{}},
          brief:{originalRequest:'测试输入法',documentTypeLabel:'工作汇报',fieldSchema:[
            {path:'purpose',label:'文档用途',control:'textarea',required:true,value:''},
          ],sources:[]},
        };
        hooks.setCard(base);
        hooks.bindWorkbenchEvents(root);
        listeners.compositionstart({target:oldControl});
        domReads=0;
        const first=hooks.renderStatusSurface({...base,version:5});
        const second=hooks.renderStatusSurface({...base,version:6});
        const deferredDidNotTouchDom={domReads,replaced};
        oldControl.value='最终确认的中文内容';
        oldControl.selectionStart=4;
        oldControl.selectionEnd=7;
        listeners.compositionend({target:oldControl});
        const queuedAfterCompositionEnd=queued.length;
        while(queued.length)queued.shift()();
        console.log(JSON.stringify({
          first,second,deferredDidNotTouchDom,queuedAfterCompositionEnd,
          appliedVersion:hooks.getCard().version,
          value:newControl.value,
          focusPreventedScroll:newControl.focusOptions?.preventScroll===true,
          selection:newControl.selection,
          scrollTop:newScroll.scrollTop,
          replaced,
        }));
        """
    )

    assert result == {
        "first": True,
        "second": True,
        "deferredDidNotTouchDom": {"domReads": 0, "replaced": 0},
        "queuedAfterCompositionEnd": 1,
        "appliedVersion": 6,
        "value": "最终确认的中文内容",
        "focusPreventedScroll": True,
        "selection": [4, 7],
        "scrollTop": 318,
        "replaced": 1,
    }


def test_v3_poll_render_preserves_the_open_quality_report_disclosure():
    result = _run_v3_hooks(
        """
        context.AbortController=AbortController;
        context.window.S={session:{session_id:'session-1'}};
        let useNewNodes=false;
        const oldDetails={dataset:{et3Disclosure:'quality-report'},open:true};
        const newDetails={dataset:{et3Disclosure:'quality-report'},open:false};
        const oldScroll={scrollTop:91};
        const newScroll={scrollTop:0};
        const root={
          id:'expertTeamV3Workbench',dataset:{},parentElement:null,
          classList:{toggle(){}},addEventListener(){},
          querySelectorAll(selector){
            if(selector==='details[data-et3-disclosure]')return [useNewNodes?newDetails:oldDetails];
            return [];
          },
          querySelector(selector){
            if(selector==='.et3-workbench-scroll')return useNewNodes?newScroll:oldScroll;
            return null;
          },
          set innerHTML(value){this._innerHTML=value;useNewNodes=true;},
          get innerHTML(){return this._innerHTML||'';},
        };
        const host={appendChild(node){node.parentElement=this;}};
        root.parentElement=host;
        const main={parentElement:host};
        context.document.activeElement=null;
        context.document.body={classList:{add(){},toggle(){},remove(){}}};
        context.document.querySelector=()=>null;
        context.document.getElementById=(id)=>{
          if(id==='mainChat')return main;
          if(id==='expertTeamV3Workbench')return root;
          return null;
        };
        const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
        const base={
          kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,
          currentStageId:'delivery',publicState:'completed',allowedActions:['delivery_open_quality_report'],
          deliveryActionBinding:binding,standaloneDelivery:{automaticCheckSummary:{status:'passed',passedCount:25}},
          presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5},team:{title:'内容创作专家团'},
        };
        hooks.setCard(base);
        hooks.renderStatusSurface({...base,version:13});
        console.log(JSON.stringify({open:newDetails.open,scrollTop:newScroll.scrollTop}));
        """
    )

    assert result == {"open": True, "scrollTop": 91}


def test_v3_styles_are_scoped_and_do_not_restyle_non_expert_shell():
    style = _read(STYLE)

    assert "[data-expert-team-v3]" in style
    assert "body.expert-team-v3-active #mainChat" in style
    assert "#mainWriting:not(" not in style
    assert "#mainChat .messages-shell" not in style
    assert "#composerWrap" not in style


def test_v3_portal_is_catalog_only_and_exposes_two_pilot_combinations():
    script = _read(SCRIPT)

    assert "专家团中心" in script
    assert "内容创作专家团" in script
    assert "深度材料研究团" in script
    assert "work_report" in script
    assert "research_report" in script
    assert "全局任务列表" not in script
    for asset in ("team-content-creator.png", "team-research.png"):
        assert (ROOT / "static" / "assets" / "writeflow" / asset).is_file()


def test_v3_configuration_errors_are_not_presented_as_unreleased_tasks():
    script = _read(SCRIPT)

    assert "已开放 ${availableCount}/${examples.length}" in script
    assert "et3-template-unavailable-reason" in script
    assert "当前任务配置异常" in script
    assert "暂未开放" not in script
    assert "当前没有已通过交付验证的文档任务" not in script


def test_v3_uses_server_product_error_instead_of_frontend_business_mapping():
    script = _read(SCRIPT)

    assert "function mutationErrorMessage(error" in script
    assert "stage_attempt_in_progress" not in script
    assert "当前阶段已有生成任务正在处理" not in script
    mutate_body = script.split("async function mutate(endpoint", 1)[1].split(
        "function isConflictError", 1
    )[0]
    assert "mutationErrorMessage(error" in mutate_body


def test_v3_brief_exposes_source_binding_and_explicit_start_gate():
    script = _read(SCRIPT)

    for marker in (
        "资料与依据",
        "添加文字资料",
        "添加本地文件",
        "/api/expert-teams/brief/sources/add",
        "/api/expert-teams/brief/sources/remove",
        "确认规格",
        "开始生成",
    ):
        assert marker in script
    assert "{ expected_brief_revision: Number(state.card.brief?.revision || 0), patch }" in script
    assert "{ expected_brief_revision: Number(state.card.brief?.revision || 0) }" in script


def test_v3_intake_actions_use_truthful_two_step_labels_and_observable_success_feedback():
    script = _read(SCRIPT)

    assert "保存回答" in script
    assert "确认规格并继续" in script
    assert "保存并继续" not in script
    assert "回答已保存，请确认规格。" in script
    assert "data-et3-brief-form" in script


def test_v3_text_source_fields_clear_only_after_the_server_accepts_the_source():
    script = _read(SCRIPT)
    start = script.index("async function addTextSource")
    end = script.index("async function addLocalFile", start)
    function_body = script[start:end]

    assert "const saved = await mutate(" in function_body
    assert "if (!saved) return false" in function_body
    assert "const clearedTextField = workbenchRoot().querySelector('[data-et3-source-text]')" in function_body
    assert "const clearedLabelField = workbenchRoot().querySelector('[data-et3-source-label]')" in function_body
    assert "clearedTextField.value = ''" in function_body
    assert "clearedLabelField.value = ''" in function_body
    assert "clearedLabelField?.focus()" in function_body
    assert function_body.index("if (!saved) return false") < function_body.index("clearedTextField.value = ''")


def test_v3_source_mutation_matches_backend_contract_and_presenter_keeps_safe_projection():
    script = _read(SCRIPT)
    presenter = _read(ROOT / "static" / "expert-team-presenter.js")

    assert "expected_brief_revision" in script
    assert "source: { kind: 'provided_text', label, text }" in script
    assert "sources:arr(brief.sources)" in presenter


def test_v3_exposes_every_public_state_as_a_user_actionable_screen():
    script = _read(SCRIPT)

    for state in (
        "intake",
        "ready",
        "executing",
        "awaiting_stage_confirmation",
        "revising",
        "generating_document",
        "awaiting_delivery_confirmation",
        "completed",
        "legacy_read_only",
    ):
        assert state in script
    for label in (
        "加入修改意见",
        "提交修改意见",
        "无修改，进入下一阶段",
        "打开文档",
    ):
        assert label in script


def test_v3_dialog_and_workbench_have_keyboard_and_live_feedback_contracts():
    script = _read(SCRIPT)

    assert 'role="dialog"' in script
    assert 'aria-modal="true"' in script
    assert 'aria-live="polite"' in script
    assert "event.key === 'Escape'" in script or 'event.key === "Escape"' in script
    assert "state.keyboardBound = true" in script
    assert "focus()" in script
    assert "state.dialogReturnFocus?.isConnected" in script
    assert 'data-team-id="${CSS.escape(state.selectedTeam.id)}"' in script
    assert "trapDialogFocus" in script
    assert 'data-et3-action="choose-source-file"' in script
    assert 'data-et3-revision' in script
    assert 'class="et3-visually-hidden"' in script


def test_v3_preserves_drafts_and_saves_brief_fields_before_answering():
    script = _read(SCRIPT)

    assert "captureWorkbenchDraft" in script
    assert "restoreWorkbenchDraft" in script
    assert "await saveBriefFields(button," in script
    assert "Object.values(patch).some(Boolean)" not in script
    assert "question__" in script


def test_v3_multi_request_brief_actions_do_not_replace_the_active_form_mid_sequence():
    script = _read(SCRIPT)

    assert "function updateCardFromResponse" in script
    assert "saveBriefFields(button, values, false)" in script
    assert "renderResponse: false" in script
    assert "保存中…" in script
    assert "正在确认规格…" in script


def test_v3_source_integrity_failure_offers_a_visible_new_task_action():
    script = _read(SCRIPT)

    assert "actionIds.has('start_new')" in script
    assert 'data-et3-action="start-new-task"' in script
    assert "action === 'start-new-task'" in script
    assert "重新发起任务" in script


def test_presenter_keeps_profile_brief_schema_nested_values_and_field_errors():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const run={
              run_id:'run-brief',session_id:'session-brief',schema_version:3,version:1,
              view:{product_mode:'standalone',public_state:'intake',allowed_actions:['answer'],
                brief:{status:'draft',revision:1,document_type:'research_report',document_type_label:'研究报告',
                  required_sections:['研究问题','证据','分析','结论边界','引用'],
                  field_schema:[
                    {path:'exact_title',label:'文档标题',control:'text',required:true,placeholder:'填写标题',help:'使用准确标题',value:'专题研究报告'},
                    {path:'details.core_question',label:'核心研究问题',control:'textarea',required:true,placeholder:'填写研究问题',help:'限定研究主线',value:'如何落地'},
                  ],
                  field_errors:[{field:'details.core_question',code:'required',message:'请填写核心研究问题'}],
                  source_requirement:{minimum_ready:1,empty_help:'必须添加一份可核对资料'},sources:[],
                },
                workflow:{stages:[],current_stage:{},progress:{done:0,total:6,is_intake:true}},
                workspace:{},presentation:{},intake:{questions:[]}},
            };
            const card=context.window.buildExpertTeamCardFromRun(run,{});
            console.log(JSON.stringify({
              fields:card.brief.fieldSchema,
              errors:card.brief.fieldErrors,
              requirement:card.brief.sourceRequirement,
              requiredSections:card.brief.requiredSections,
            }));
            """
        )
    )

    assert result["fields"][1] == {
        "path": "details.core_question",
        "label": "核心研究问题",
        "control": "textarea",
        "required": True,
        "placeholder": "填写研究问题",
        "help": "限定研究主线",
        "value": "如何落地",
    }
    assert result["errors"] == [
        {
            "field": "details.core_question",
            "code": "required",
            "message": "请填写核心研究问题",
        }
    ]
    assert result["requirement"] == {
        "minimumReady": 1,
        "emptyHelp": "必须添加一份可核对资料",
    }
    assert result["requiredSections"] == ["研究问题", "证据", "分析", "结论边界", "引用"]


def test_v3_profile_fields_render_chinese_help_and_associated_inline_errors():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',allowedActions:['answer'],questions:[],brief:{
          originalRequest:'形成专题研究报告',documentTypeLabel:'研究报告',sources:[],
          requiredSections:['研究问题','证据','分析','结论边界','引用'],
          fieldSchema:[
            {path:'exact_title',label:'文档标题',control:'text',required:true,placeholder:'填写标题',help:'使用准确标题',value:''},
            {path:'details.core_question',label:'核心研究问题',control:'textarea',required:true,placeholder:'填写研究问题',help:'限定研究主线',value:''},
          ],
          fieldErrors:[{field:'details.core_question',code:'required',message:'请填写核心研究问题'}],
          sourceRequirement:{minimumReady:1,emptyHelp:'研究报告必须至少添加一份可核对资料，并在正文中保留引用。'},
        }};
        console.log(JSON.stringify({html:hooks.briefPanel(card)}));
        """
    )
    html = result["html"]

    assert "核心研究问题" in html
    assert "限定研究主线" in html
    assert 'name="details.core_question"' in html
    assert 'aria-invalid="true"' in html
    assert "请填写核心研究问题" in html
    assert "研究报告必须至少添加一份可核对资料" in html
    assert "data-et3-source-error" in html


def test_v3_required_sections_are_visible_during_intake_and_before_generation():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',allowedActions:['answer','start_generation'],questions:[],subtitle:'专题研究报告',brief:{
          originalRequest:'形成专题研究报告',documentTypeLabel:'研究报告',audience:'项目决策小组',sources:[],
          requiredSections:['研究问题','证据','分析','结论边界','引用'],fieldSchema:[],fieldErrors:[],sourceRequirement:{},
        }};
        console.log(JSON.stringify({intake:hooks.briefPanel(card),ready:hooks.statePanel(card,'ready')}));
        """
    )

    for html in (result["intake"], result["ready"]):
        assert "必备章节" in html
        for section in ("研究问题", "证据", "分析", "结论边界", "引用"):
            assert section in html
    assert "DOCX 自动检查" in result["intake"]


def test_v3_brief_patch_uses_schema_whitelist_and_writes_nested_fields():
    result = _run_v3_hooks(
        """
        const schema=[
          {path:'exact_title',required:true},
          {path:'details.core_question',required:true},
          {path:'details.time_range.start',required:true},
          {path:'details.time_range.end',required:true},
          {path:'source_policy.as_of_date',required:true},
        ];
        const values={
          exact_title:'专题研究报告',
          'details.core_question':'人工智能辅助办公如何落地',
          'details.time_range.start':'2025-01-01',
          'details.time_range.end':'2026-07-25',
          'source_policy.as_of_date':'2026-07-25',
          'question__q1':'保留为问答而非 Brief 字段',
          'data_handling.model_policy_id':'client-forged-policy',
        };
        console.log(JSON.stringify({patch:hooks.buildBriefPatch(values,schema)}));
        """
    )

    assert result["patch"] == {
        "exact_title": "专题研究报告",
        "details": {
            "core_question": "人工智能辅助办公如何落地",
            "time_range": {"start": "2025-01-01", "end": "2026-07-25"},
        },
        "source_policy": {"as_of_date": "2026-07-25"},
    }


def test_v3_custom_confirmation_validates_before_request_and_links_server_field_errors():
    script = _read(SCRIPT)
    save_start = script.index("async function saveBrief(button, confirmAfter)")
    save_end = script.index("async function addTextSource", save_start)
    save_source = script[save_start:save_end]
    submit_start = script.index("async function submitAnswers(button)")
    submit_end = script.index("function saveBriefFields", submit_start)
    submit_source = script[submit_start:submit_end]

    assert "form.reportValidity()" in save_source
    assert save_source.index("form.reportValidity()") < save_source.index("saveBriefFields")
    assert "const nativeValid = !confirmAfter || form.reportValidity();" in save_source
    assert "if (!nativeValid || errors.length)" in save_source
    assert "form.reportValidity()" in submit_source
    assert submit_source.index("form.reportValidity()") < submit_source.index("saveBriefFields")
    assert "const nativeValid = form.reportValidity();" in submit_source
    assert "if (!nativeValid || errors.length)" in submit_source
    assert "clientBriefFieldErrors" in save_source
    assert "showBriefFieldErrors" in script
    assert "error.payload.field" in script
    assert "aria-describedby" in script


def test_v3_source_requirement_error_focuses_and_scrolls_to_actionable_source_input():
    result = _run_v3_hooks(
        """
        let focused=false;let scrolled=false;
        const sourceSlot={dataset:{},textContent:'',hidden:true};
        const sourceField={focus(){focused=true;},scrollIntoView(options){scrolled=options&&options.block==='center';}};
        const form={querySelectorAll(selector){return [];}};
        const root={querySelector(selector){
          if(selector==='[data-et3-brief-form]')return form;
          if(selector==='[data-et3-source-error]')return sourceSlot;
          if(selector==='[data-et3-source-text]')return sourceField;
          return null;
        }};
        context.document.getElementById=()=>root;
        const accepted=hooks.showBriefFieldErrors([{field:'source_policy.source_refs',code:'source_required',message:'请先添加需要润色的原始材料。'}]);
        console.log(JSON.stringify({accepted,focused,scrolled,message:sourceSlot.textContent,hidden:sourceSlot.hidden}));
        """
    )

    assert result == {
        "accepted": False,
        "focused": True,
        "scrolled": True,
        "message": "请先添加需要润色的原始材料。",
        "hidden": False,
    }


def test_v3_save_and_continue_skips_blank_optional_intake_questions():
    script = _read(SCRIPT)
    submit_start = script.index("async function submitAnswers(button)")
    submit_end = script.index("function saveBriefFields", submit_start)
    submit_source = script[submit_start:submit_end]

    assert "skip_optional: true" in submit_source


def test_v3_client_required_validation_returns_one_error_per_profile_field():
    result = _run_v3_hooks(
        """
        const schema=[
          {path:'exact_title',label:'文档标题',required:true},
          {path:'details.reporting_period',label:'汇报周期',required:true},
          {path:'details.reporting_unit',label:'汇报单位',required:true},
        ];
        const errors=hooks.clientBriefFieldErrors(
          {exact_title:'月度汇报','details.reporting_period':'','details.reporting_unit':'  '},
          schema
        );
        console.log(JSON.stringify({errors}));
        """
    )
    assert result["errors"] == [
        {
            "field": "details.reporting_period",
            "code": "required",
            "message": "请填写汇报周期",
        },
        {
            "field": "details.reporting_unit",
            "code": "required",
            "message": "请填写汇报单位",
        },
    ]


def test_v3_can_collapse_restore_and_recover_without_legacy_result_globals():
    script = _read(SCRIPT)

    assert 'data-et3-action="restore-workbench"' in script
    assert 'data-et3-action="refresh-run"' in script
    assert 'data-et3-action="cancel-run"' in script
    assert "openExpertTeamResultViewer" not in script


def test_enterprise_identity_cookie_covers_expert_and_docx_api_routes():
    routes = _read(ROOT / "api" / "routes.py")

    assert "Path=/api/expert-teams; HttpOnly; Secure; SameSite=Lax" in routes
    assert "Path=/api/docx-engine-v2/quality/wps-visual; HttpOnly; Secure; SameSite=Lax" in routes
    assert "Path=/api; HttpOnly" not in routes


def test_identity_callback_emits_both_narrow_cookie_paths(monkeypatch):
    from api import routes
    from api.expert_teams import trusted_identity

    class Resolver:
        def complete_login(self, **_kwargs):
            return {"session_id": "trusted-session", "principal": {"principal_id": "reviewer-1"}}

    class Handler:
        def __init__(self):
            self.headers = []
            self.wfile = BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, name, value):
            self.headers.append((name, value))

        def end_headers(self):
            return None

    monkeypatch.setattr(trusted_identity, "get_trusted_identity_resolver", lambda: Resolver())
    handler = Handler()

    assert routes.handle_get(handler, urlsplit("/api/expert-teams/identity/callback?state=s&code=c")) is True
    cookies = [value for name, value in handler.headers if name == "Set-Cookie"]
    assert len(cookies) == 2
    assert any("Path=/api/expert-teams;" in value for value in cookies)
    assert any("Path=/api/docx-engine-v2/quality/wps-visual;" in value for value in cookies)


def test_v3_revision_draft_and_stage_mutations_are_bound_to_authoritative_objects():
    script = _read(SCRIPT)

    assert "draftFingerprint" in script
    assert "stageActionBinding" in script
    assert "artifact_sha256" in script
    assert "conflictRevisionDraft" in script


def test_v3_electron_smoke_script_covers_flow_and_non_expert_isolation_gate():
    smoke = _read(ELECTRON_SMOKE)

    assert "_electron.launch" in smoke
    assert "#expertTeamV3PortalRoot" in smoke
    assert "#expertTeamV3Workbench" in smoke
    assert "加入修改意见" in smoke
    assert "无修改，进入下一阶段" in smoke
    assert "switchPanel(\"tasks\")" in smoke
    assert "expert-team-v3-active" in smoke
    assert "page.screenshot" in smoke


def test_v3_electron_smoke_binds_python_to_the_current_worktree_by_default():
    smoke = _read(ELECTRON_SMOKE)

    assert "process.env.HERMES_WEBUI_PYTHON || path.join(repoRoot" in smoke
    assert "HERMES_WEBUI_PYTHON: pythonBin" in smoke
    assert "TAIJI_AGENT_PYTHON: pythonBin" in smoke
    assert "TAIJI_WEBUI_PYTHON: pythonBin" in smoke
    assert "pythonRequestedPath: path.resolve(pythonBin)" in smoke
    assert "pythonBinRealpath: fs.realpathSync(pythonBin)" in smoke
    assert "const electronHostRoot = process.env.TAIJI_ELECTRON_HOST_ROOT || process.env.TAIJI_MAIN_REPO_ROOT || repoRoot" in smoke
    assert "/Users/bwb/" not in smoke
    assert "path.join(formalRoot, 'hermes-local-lab', 'sources', 'hermes-agent', '.venv'" not in smoke


def test_v3_standalone_stage_confirmation_uses_local_contract_without_enterprise_calls():
    script = _read(SCRIPT)

    assert "/api/expert-teams/stage/confirm" in script
    assert "stage_confirm" in script
    assert "stage_revise" in script
    for forbidden in (
        "/api/expert-teams/stage/approve",
        "/api/expert-teams/identity/",
        "/api/docx-engine-v2/quality/wps-visual",
        "/api/expert-teams/office-revisions/create",
        "使用企业审批身份登录",
        "使用企业验收身份登录",
    ):
        assert forbidden not in script


def test_v3_standalone_write_controls_are_fail_closed_by_allowed_actions():
    script = _read(SCRIPT)

    assert "function actionAllowed(card, action)" in script
    assert "list(card.allowedActions).includes(action)" in script
    for action in (
        "answer",
        "start_generation",
        "submit_stage_input",
        "resume",
        "cancel",
        "stage_confirm",
        "stage_recheck",
        "stage_revise",
    ):
        assert action in script
    assert "const requiredAction = {" in script
    assert "当前状态尚不允许开始生成" in script
    assert "服务端尚未允许当前操作" in script


def test_v3_ready_state_distinguishes_start_stage_input_and_resume_by_allowed_action():
    script = _read(SCRIPT)

    assert "current === 'ready' && actionAllowed(card, 'submit_stage_input')" in script
    assert "current === 'ready' && actionAllowed(card, 'resume')" in script
    assert "return stageInputPanel(card)" in script
    assert "return resumePanel(card)" in script
    assert "/api/expert-teams/stage/input" in script
    assert "input_id: state.card.pendingInputId" in script
    assert "'submit-stage-input': 'submit_stage_input'" in script
    assert "'retry-run': 'resume'" in script
    assert "需要你的补充" in script
    assert "任务等待恢复" in script


def test_v3_generated_invalid_state_explains_safe_regeneration_instead_of_generic_recovery():
    result = _run_v3_hooks(
        """
        const card={
          productMode:'standalone',publicState:'ready',workflowState:'generated_invalid',allowedActions:['resume'],
          title:'起草工作汇报初稿',presentation:{
            title:'生成格式需要重新处理',
            detail:'本次生成结果格式不完整，系统没有采用这份内容。请重新生成当前阶段。'
          }
        };
        const copy=hooks.stateCopyFor(card,'ready');
        const panel=hooks.statePanel(card,'ready');
        console.log(JSON.stringify({copy,panel}));
        """
    )

    assert result["copy"][0] == "生成格式需要重新处理"
    assert "系统没有采用" in result["copy"][1]
    assert "重新生成当前阶段" in result["panel"]
    assert "任务等待恢复" not in result["panel"]


def test_v3_delivery_failure_names_the_safe_docx_only_retry():
    result = _run_v3_hooks(
        """
        const card={
          productMode:'standalone',publicState:'failed',workflowState:'generated_invalid',
          currentStageId:'delivery',allowedActions:['resume'],
          title:'起草工作汇报初稿',
          productError:{
            schema:'taiji.product.error.v1',
            title:'DOCX 生成未完成',
            message:'已确认内容仍然保留，可以只重新生成最终文档。',
            recoveryActions:[{id:'open_result'},{id:'retry'}],
          },
        };
        console.log(JSON.stringify({
          failure:hooks.statePanel(card,'failed'),
          resume:hooks.resumePanel(card),
        }));
        """
    )

    assert "重新生成最终 DOCX" in result["failure"]
    assert "重新生成最终 DOCX" in result["resume"]
    assert "不会重新调用模型" in result["resume"]
    assert "重试当前阶段" not in result["failure"]


def test_presenter_hides_expert_team_protocol_messages_but_keeps_normal_chat_unchanged():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const messages=[
              {role:'user',content:'专家团开始生成：流程安排 · 起草工作汇报初稿'},
              {role:'assistant',content:'<<<TAIJI_META_V1>>>{"payload":{}}<<<TAIJI_META_END>>>'},
              {role:'user',content:'你好'},
              {role:'assistant',content:'正常回复'},
            ];
            console.log(JSON.stringify({
              launchMessage:context.window.isExpertTeamExecutionDisplayMessage(messages[0].content),
              isProtocol:context.window.isExpertTeamProtocolAssistant(messages,1),
              explicitLive:context.window.isExpertTeamProtocolLiveStream({expertTeamProtocol:true},[
                {role:'assistant',content:'stale snapshot without the current launch marker'},
              ]),
              explicitNormal:context.window.isExpertTeamProtocolLiveStream({expertTeamProtocol:false},messages),
              projected:context.window.projectExpertTeamTranscriptContent(messages,1,messages[1].content),
              normal:context.window.projectExpertTeamTranscriptContent(messages,3,messages[3].content),
            }));
            """
        )
    )

    assert result == {
        "launchMessage": True,
        "isProtocol": True,
        "explicitLive": True,
        "explicitNormal": False,
        "projected": "专家团阶段处理已结束，请在右侧工作台查看结果状态和下一步。",
        "normal": "正常回复",
    }


def test_expert_team_live_stream_uses_explicit_start_context_instead_of_stale_inflight_position():
    ui = _read(ROOT / "static" / "ui.js")
    sessions = _read(ROOT / "static" / "sessions.js")
    messages = _read(ROOT / "static" / "messages.js")

    apply_start = ui.index("function _applyExpertTeamStreamResponse(data)")
    apply_end = ui.index("async function resumeExpertTeamRun", apply_start)
    apply_body = ui[apply_start:apply_end]
    assert "attachLiveStream(sid,data.stream_id,[],{expertTeamProtocol:true})" in apply_body

    attach_start = messages.index("function attachLiveStream(")
    attach_end = messages.index("function transcript()", attach_start)
    attach_body = messages[attach_start:attach_end]
    assert "window.isExpertTeamProtocolLiveStream(options" in attach_body

    reconnect = "expertTeamProtocol:window.isExpertTeamExecutionDisplayMessage(S.session.pending_user_message)"
    assert reconnect in sessions


def test_v3_response_hands_stream_to_the_shared_chat_runtime_before_rendering():
    result = _run_v3_hooks(
        """
        const calls=[];
        context.window._applyExpertTeamStreamResponse=payload=>{
          calls.push({kind:'stream',streamId:payload.stream_id});
          return true;
        };
        context.window.buildExpertTeamCardFromRun=()=>null;
        const rendered=hooks.applyResponse({
          stream_id:'stream-v3',
          pending_user_message:'专家团开始生成：流程安排 · 起草通知通报初稿',
          run:{run_id:'run-v3',session_id:'session-v3',workflow_state:'generating',execution_stream_id:'stream-v3'},
        });
        console.log(JSON.stringify({calls,rendered}));
        """
    )

    assert result == {
        "calls": [{"kind": "stream", "streamId": "stream-v3"}],
        "rendered": False,
    }

    ui = _read(ROOT / "static" / "ui.js")
    messages = _read(ROOT / "static" / "messages.js")
    assert "projectExpertTeamTranscriptContent" in ui
    assert "isExpertTeamProtocolAssistant" in messages
    assert "专家团正在生成当前阶段" in messages


def test_presenter_uses_standalone_run_view_as_the_only_public_state_and_action_source():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const binding={session_id:'session-1',run_id:'run-1',expected_version:7,stage_id:'draft',stage_attempt:2,artifact_id:'draft:2',artifact_sha256:'a'.repeat(64)};
            const run={
              run_id:'run-1',session_id:'session-1',schema_version:3,version:7,product_mode:'standalone',
              workflow_state:'completed',questions:[{id:'raw-question',title:'不得使用'}],
              view:{
                product_mode:'standalone',public_state:'awaiting_stage_confirmation',
                allowed_actions:['stage_confirm','stage_revise'],stage_action_binding:binding,
                presentation:{state:'completed',title:'旧状态不得使用'},
                workflow:{stages:[{id:'draft',title:'初稿撰写'}],current_stage:{id:'draft',title:'初稿撰写'},progress:{done:1,total:5,current:'初稿撰写'}},
                workspace:{current_stage:{id:'draft',title:'初稿撰写'}},
                intake:{questions:[{id:'view-question',title:'服务端视图问题',status:'pending',required:true}]},
                stage_review:{output:{content:'权威阶段成果'}},stage_result:{content:'权威阶段成果'},
              },
            };
            const card=context.window.buildExpertTeamCardFromRun(run,{});
            const payload=context.window.buildExpertTeamStageActionPayload(card,'idem-1');
            console.log(JSON.stringify({
              status:card.status,publicState:card.publicState,productMode:card.productMode,
              allowedActions:card.allowedActions,stageActionBinding:card.stageActionBinding,
              questionIds:card.questions.map(item=>item.id),payload,
            }));
            """
        )
    )

    assert result["status"] == "awaiting_stage_confirmation"
    assert result["publicState"] == "awaiting_stage_confirmation"
    assert result["productMode"] == "standalone"
    assert result["allowedActions"] == ["stage_confirm", "stage_revise"]
    assert result["questionIds"] == ["view-question"]
    assert result["payload"] == {
        **result["stageActionBinding"],
        "idempotency_key": "idem-1",
    }
    assert set(result["payload"]) == {
        "session_id",
        "run_id",
        "expected_version",
        "stage_id",
        "stage_attempt",
        "artifact_id",
        "artifact_sha256",
        "idempotency_key",
    }


def test_presenter_rejects_stage_binding_for_another_session_run_or_version():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            function project(binding, version=7){
              const run={run_id:'run-1',session_id:'session-1',schema_version:3,version,
                view:{product_mode:'standalone',public_state:'awaiting_stage_confirmation',
                  allowed_actions:['stage_confirm','stage_revise'],stage_action_binding:binding,
                  presentation:{},workflow:{stages:[],current_stage:{id:'draft'},progress:{}}}};
              const card=context.window.buildExpertTeamCardFromRun(run,{});
              return {binding:card.stageActionBinding,payload:context.window.buildExpertTeamStageActionPayload(card,'idem')};
            }
            const base={session_id:'session-1',run_id:'run-1',expected_version:7,stage_id:'draft',stage_attempt:2,artifact_id:'draft:2',artifact_sha256:'a'.repeat(64)};
            console.log(JSON.stringify({
              valid:project(base),
              wrongSession:project({...base,session_id:'session-2'}),
              wrongRun:project({...base,run_id:'run-2'}),
              wrongVersion:project({...base,expected_version:6}),
            }));
            """
        )
    )

    assert result["valid"]["binding"] is not None
    assert result["valid"]["payload"]["idempotency_key"] == "idem"
    for key in ("wrongSession", "wrongRun", "wrongVersion"):
        assert result[key] == {"binding": None, "payload": None}


def test_stage_review_buttons_are_disabled_when_server_binding_is_missing():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',allowedActions:['stage_confirm','stage_revise'],stageActionBinding:null,
          stageReview:{output:{content:'阶段成果'}},stageResult:{},presentation:{},reviewItems:[]};
        const html=hooks.statePanel(card,'awaiting_stage_confirmation');
        console.log(JSON.stringify({
          reviseDisabled:html.includes('data-et3-action="submit-revision" disabled'),
          confirmDisabled:html.includes('data-et3-action="confirm-stage" disabled'),
          explains:html.includes('服务端尚未允许当前操作'),
        }));
        """
    )

    assert result == {
        "reviseDisabled": True,
        "confirmDisabled": True,
        "explains": True,
    }


def test_stage_review_uses_deliverable_and_summary_before_placeholder():
    result = _run_v3_hooks(
        """
        function render(output){
          const card={productMode:'standalone',allowedActions:[],stageActionBinding:null,
            stageReview:{output},stageResult:{},presentation:{},reviewItems:[]};
          return hooks.statePanel(card,'awaiting_stage_confirmation');
        }
        const deliverableHtml=render({deliverable:'完整阶段成果',summary:'阶段摘要'});
        const summaryHtml=render({summary:'仅有阶段摘要'});
        console.log(JSON.stringify({
          usesDeliverable:deliverableHtml.includes('完整阶段成果'),
          usesSummary:summaryHtml.includes('仅有阶段摘要'),
          hidesPlaceholder:!deliverableHtml.includes('阶段成果已生成，请稍后刷新状态。'),
        }));
        """
    )

    assert result == {
        "usesDeliverable": True,
        "usesSummary": True,
        "hidesPlaceholder": True,
    }


def test_stage_review_renders_safe_readable_document_instead_of_raw_markdown():
    result = _run_v3_hooks(
        """
        const html=hooks.reviewDocumentHtml(`# 月度工作汇报

## 工作开展情况

- **稳定性验证**：已完成。
- 风险项：<script>alert('x')</script>

## 下一步工作安排

1. 完成真实用户验收
2. 形成交付记录`);
        console.log(JSON.stringify({html}));
        """
    )

    assert "<h2>月度工作汇报</h2>" in result["html"]
    assert "<h3>工作开展情况</h3>" in result["html"]
    assert "<ul>" in result["html"]
    assert "<strong>稳定性验证</strong>" in result["html"]
    assert "<ol>" in result["html"]
    assert "## 工作开展情况" not in result["html"]
    assert "- **稳定性验证**" not in result["html"]
    assert "<script>" not in result["html"]
    assert "&lt;script&gt;alert(&#039;x&#039;)&lt;/script&gt;" in result["html"]


def test_stage_review_explains_warning_is_reviewable_and_shows_next_action():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',allowedActions:[],stageActionBinding:null,
          stageReview:{output:{content:'会议纪要素材台账'}},presentation:{},reviewItems:[],
          stageResult:{stage_quality:{state:'attention',blocking_count:0,warning_count:1,issues:[
            {severity:'warning',message:'当前未提供会议原始记录',suggested_action:'请人工核对并补充'}
          ]}}};
        const html=hooks.statePanel(card,'awaiting_stage_confirmation');
        console.log(JSON.stringify({
          hasAttentionTitle:html.includes('可继续，但有待确认事项'),
          hasMessage:html.includes('当前未提供会议原始记录'),
          hasAction:html.includes('请人工核对并补充'),
          doesNotClaimClear:!html.includes('未发现阻断问题。仍建议阅读完整成果后确认。'),
        }));
        """
    )

    assert result == {
        "hasAttentionTitle": True,
        "hasMessage": True,
        "hasAction": True,
        "doesNotClaimClear": True,
    }


def test_stage_review_shows_persisted_semantic_block_and_disables_confirmation():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',allowedActions:['stage_revise'],
          stageActionBinding:{
            session_id:'session-1',run_id:'run-1',expected_version:7,stage_id:'draft',
            stage_attempt:2,artifact_id:'draft:2',artifact_sha256:'a'.repeat(64)
          },
          stageReview:{output:{content:'润色后的正式材料'}},presentation:{},reviewItems:[],
          stageResult:{stage_quality:{state:'blocked',blocking_count:1,warning_count:0,issues:[
            {
              severity:'blocking',
              code:'source_anchor_missing',
              message:'润色正文遗漏了原文中的关键事实或数字。',
              suggested_action:'提交修改意见后重新生成当前阶段。'
            }
          ]}}};
        const html=hooks.statePanel(card,'awaiting_stage_confirmation');
        console.log(JSON.stringify({
          hasBlockedTitle:html.includes('当前成果存在阻断问题'),
          hasMessage:html.includes('润色正文遗漏了原文中的关键事实或数字。'),
          hasAction:html.includes('提交修改意见后重新生成当前阶段。'),
          confirmDisabled:html.includes('data-et3-action="confirm-stage" disabled'),
          revisionEnabled:html.includes('data-et3-action="submit-revision"')
            && !html.includes('data-et3-action="submit-revision" disabled'),
          doesNotClaimClear:!html.includes('未发现阻断问题。仍建议阅读完整成果后确认。'),
        }));
        """
    )

    assert result == {
        "hasBlockedTitle": True,
        "hasMessage": True,
        "hasAction": True,
        "confirmDisabled": True,
        "revisionEnabled": True,
        "doesNotClaimClear": True,
    }


def test_stage_review_exposes_recheck_for_same_artifact_when_server_allows_it():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',allowedActions:['stage_recheck','stage_revise'],
          stageActionBinding:{
            session_id:'session-1',run_id:'run-1',expected_version:8,stage_id:'polish',
            stage_attempt:1,artifact_id:'polish:1',artifact_sha256:'a'.repeat(64)
          },
          stageReview:{output:{content:'已保留的通知通报正文'}},presentation:{},reviewItems:[],
          stageResult:{stage_quality:{state:'blocked',blocking_count:1,warning_count:0,issues:[
            {severity:'blocking',code:'review_issue_unresolved',message:'旧版复核策略将待补充事项标记为阻断',suggested_action:'可重新检查当前结果'}
          ]}}};
        const html=hooks.statePanel(card,'awaiting_stage_confirmation');
        console.log(JSON.stringify({
          hasRecheck:html.includes('data-et3-action="recheck-stage"'),
          label:html.includes('重新检查当前结果'),
          recheckEnabled:html.includes('data-et3-action="recheck-stage"')
            && !html.includes('data-et3-action="recheck-stage" disabled'),
          normalConfirmHidden:!html.includes('data-et3-action="confirm-stage"'),
          revisionEnabled:html.includes('data-et3-action="submit-revision"')
            && !html.includes('data-et3-action="submit-revision" disabled'),
        }));
        """
    )

    assert result == {
        "hasRecheck": True,
        "label": True,
        "recheckEnabled": True,
        "normalConfirmHidden": True,
        "revisionEnabled": True,
    }


def test_presenter_projects_only_a_complete_server_cancel_action_binding():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const binding={session_id:'session-1',run_id:'run-1',expected_version:11,stage_id:'draft',idempotency_key:'server-cancel-retry-1'};
            function card(cancelBinding){
              return context.window.buildExpertTeamCardFromRun({
                run_id:'run-1',session_id:'session-1',schema_version:3,version:11,
                view:{
                  product_mode:'standalone',public_state:'cancelling',
                  allowed_actions:['refresh','retry_cancel'],cancel_action_binding:cancelBinding,
                  presentation:{},workflow:{stages:[],current_stage:{id:'draft'},progress:{}},
                },
              },{});
            }
            console.log(JSON.stringify({
              valid:card(binding).cancelActionBinding ?? null,
              missingKey:card({...binding,idempotency_key:''}).cancelActionBinding ?? null,
              missingStage:card({...binding,stage_id:''}).cancelActionBinding ?? null,
              invalidVersion:card({...binding,expected_version:-1}).cancelActionBinding ?? null,
              wrongSession:card({...binding,session_id:'session-2'}).cancelActionBinding ?? null,
              wrongRun:card({...binding,run_id:'run-2'}).cancelActionBinding ?? null,
            }));
            """
        )
    )

    assert result["valid"] == {
        "session_id": "session-1",
        "run_id": "run-1",
        "expected_version": 11,
        "stage_id": "draft",
        "idempotency_key": "server-cancel-retry-1",
    }
    assert result["missingKey"] is None
    assert result["missingStage"] is None
    assert result["invalidVersion"] is None
    assert result["wrongSession"] is None
    assert result["wrongRun"] is None


def test_v3_409_adopts_authoritative_run_and_keeps_revision_draft():
    script = _read(SCRIPT)

    assert "isConflictError" in script
    assert "error.payload.run" in script
    assert "conflictRevisionDraft" in script
    assert "restoreConflictRevisionDraft" in script
    assert "状态已更新，修改意见已保留" in script


def test_v3_conflict_revision_draft_is_bound_to_the_exact_stage_artifact():
    script = _read(SCRIPT)

    assert "stageBindingFingerprint" in script
    assert "stageFingerprint" in script
    assert "draft.stageFingerprint === stageBindingFingerprint(card)" in script
    assert "上一阶段有未提交的修改意见" in script
    assert "readonly data-et3-stale-revision" in script


def test_v3_conflict_draft_behavior_requires_every_stage_binding_field_to_match():
    result = _run_v3_hooks(
        """
        const binding={session_id:'session-1',run_id:'run-1',expected_version:7,stage_id:'draft',stage_attempt:2,artifact_id:'draft:2',artifact_sha256:'a'.repeat(64)};
        const card={runId:'run-1',sourceSessionId:'session-1',stageActionBinding:binding};
        const fingerprint=hooks.stageBindingFingerprint(card);
        const draft={runId:'run-1',stageFingerprint:fingerprint,value:'保留这条意见'};
        hooks.setConflictDraft(draft);
        const changes={};
        for (const [key,value] of Object.entries({session_id:'session-2',run_id:'run-2',expected_version:8,stage_id:'review',stage_attempt:3,artifact_id:'review:3',artifact_sha256:'b'.repeat(64)})) {
          const changed={...card,stageActionBinding:{...binding,[key]:value}};
          const field={value:''};
          changes[key]={matches:hooks.conflictDraftMatches(changed,draft),restored:hooks.restoreConflictRevisionDraft({querySelector(){return field;}},changed),value:field.value};
        }
        const sameField={value:''};
        const sameRestored=hooks.restoreConflictRevisionDraft({querySelector(){return sameField;}},card);
        console.log(JSON.stringify({fingerprint,incomplete:hooks.stageBindingFingerprint({runId:'run-1',stageActionBinding:{}}),sameRestored,sameValue:sameField.value,changes}));
        """
    )

    assert result["fingerprint"]
    assert result["incomplete"] == ""
    assert result["sameRestored"] is True
    assert result["sameValue"] == "保留这条意见"
    for changed in result["changes"].values():
        assert changed == {"matches": False, "restored": False, "value": ""}


def test_v3_general_draft_restore_cannot_bypass_the_complete_stage_binding():
    result = _run_v3_hooks(
        """
        const binding={session_id:'session-1',run_id:'run-1',expected_version:7,stage_id:'draft',stage_attempt:2,artifact_id:'draft:2',artifact_sha256:'a'.repeat(64)};
        const card={runId:'run-1',publicState:'awaiting_stage_confirmation',productMode:'standalone',readOnly:false,stageActionBinding:binding};
        const saved={fingerprint:hooks.draftFingerprint(card),values:[{key:'revision-field',value:'旧阶段意见',checked:false,kind:'textarea'}],focusKey:'',selectionStart:null,selectionEnd:null,scrollTop:0};
        function restore(targetCard) {
          const field={id:'revision-field',type:'textarea',tagName:'TEXTAREA',value:'',checked:false,focus(){},setSelectionRange(){}};
          const root={querySelectorAll(){return [field];},querySelector(){return null;}};
          hooks.restoreWorkbenchDraft(root,saved,targetCard);
          return field.value;
        }
        const changed={...card,stageActionBinding:{...binding,expected_version:8}};
        const incomplete={...card,stageActionBinding:{}};
        console.log(JSON.stringify({same:restore(card),changed:restore(changed),incomplete:restore(incomplete)}));
        """
    )

    assert result == {"same": "旧阶段意见", "changed": "", "incomplete": ""}


def test_v3_stage_input_and_exception_states_never_use_generation_ready_copy():
    script = _read(SCRIPT)

    assert "function stateCopyFor(card, current)" in script
    assert "actionAllowed(card, 'submit_stage_input')" in script
    assert "需要你的补充" in script
    for state in ("failed", "cancelled", "cancelling"):
        assert state in script
    assert "正在停止专家团" in script


def test_v3_state_surfaces_override_stale_ready_copy_and_keep_exception_actions_safe():
    result = _run_v3_hooks(
        """
        const base={kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',subtitle:'任务',phase:'初稿撰写',progress:{done:1,total:5},workflow:{currentStage:{}},brief:{sources:[]},presentation:{statusLabel:'任务规格已确认',detail:'旧文案'},team:{title:'内容创作专家团'},allowedActions:[]};
        const html={};
        for (const current of ['failed','cancelled','cancelling']) html[current]=hooks.workbenchHtml({...base,publicState:current});
        const stageInput={...base,publicState:'ready',allowedActions:['submit_stage_input'],pendingInputId:'input-1',pendingInput:{question:'请选择统计口径',description:'确认后继续',options:[]}};
        html.stageInput=hooks.workbenchHtml(stageInput);
        const failedResume=hooks.statePanel({...base,allowedActions:['resume']},'failed');
        console.log(JSON.stringify({html,failedResume}));
        """
    )

    for state, label in (("failed", "任务未完成"), ("cancelled", "任务已取消"), ("cancelling", "正在停止专家团")):
        assert label in result["html"][state]
        assert '<span class="et3-state-pill">任务规格已确认</span>' not in result["html"][state]
        assert "开始生成" not in result["html"][state]
    assert "需要你的补充" in result["html"]["stageInput"]
    assert '<span class="et3-state-pill">需要你的补充</span>' in result["html"]["stageInput"]
    assert "刷新停止状态" in result["html"]["cancelling"]
    assert "恢复任务" in result["failedResume"]


def test_v3_model_configuration_failure_has_safe_visible_recovery_actions():
    result = _run_v3_hooks(
        """
        const card={
          kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',
          publicState:'failed',allowedActions:['resume'],presentation:{detail:'内部原始错误不应展示'},
          productError:{schema:'taiji.product.error.v1',code:'model_configuration_required',title:'模型配置待完成',message:'请先完成模型配置，再重新执行此操作。',recoveryActions:[{id:'open_model_settings',label:'打开模型配置'},{id:'export_diagnostics',label:'导出诊断'}]},
          workflow:{currentStage:{}},brief:{sources:[]},progress:{done:0,total:5},team:{title:'内容创作专家团'},
        };
        console.log(JSON.stringify({html:hooks.statePanel(card,'failed')}));
        """
    )

    assert "模型配置待完成" in result["html"]
    assert "请先完成模型配置" in result["html"]
    assert 'data-et3-action="open-model-settings"' in result["html"]
    assert 'data-et3-action="export-diagnostics"' in result["html"]
    assert 'data-et3-action="copy-diagnostics"' in result["html"]
    assert "导出完整诊断" in result["html"]
    assert "内部原始错误不应展示" not in result["html"]


def test_v3_product_error_actions_reuse_existing_settings_and_diagnostics_entrypoints():
    script = _read(SCRIPT)

    assert "window.switchSettingsSection('models')" in script
    assert "window.exportProductDiagnostics()" in script


def test_v3_product_error_takes_priority_over_generic_resume_panel():
    result = _run_v3_hooks(
        """
        const card={
          productMode:'standalone',allowedActions:['resume'],presentation:{detail:'raw provider detail'},
          productError:{schema:'taiji.product.error.v1',title:'模型配置待完成',message:'请先完成模型配置，再重新执行此操作。',recoveryActions:[{id:'open_model_settings',label:'打开模型配置'},{id:'export_diagnostics',label:'导出诊断'}]}
        };
        console.log(JSON.stringify({html:hooks.statePanel(card,'ready')}));
        """
    )

    assert "模型配置待完成" in result["html"]
    assert "打开模型配置" in result["html"]
    assert "导出完整诊断" in result["html"]
    assert "任务等待恢复" not in result["html"]
    assert "raw provider detail" not in result["html"]


def test_v3_generated_invalid_failure_exposes_preserved_result_and_concrete_remedies():
    result = _run_v3_hooks(
        """
        const card={
          productMode:'standalone',publicState:'failed',workflowState:'generated_invalid',
          currentStageId:'research',allowedActions:['resume'],presentation:{detail:'内部原始错误不应展示'},
          productError:{
            schema:'taiji.product.error.v1',title:'当前阶段需要补充依据',
            message:'阶段成果未通过企业合同校验，已保留供你核对。',
            recoveryActions:[{id:'open_result'},{id:'regenerate'},{id:'export_diagnostics'}],
          },
          stageResult:{
            summary:'研究阶段已形成初步分析，但证据不足。',
            content:'# 研究阶段结果\\n\\n## 证据\\n\\n当前资料仅能支持任务类型和资料门槛。',
            stage_quality:{state:'blocked',blocking_count:2,warning_count:0,issues:[
              {severity:'blocking',message:'缺少具体复核步骤与责任角色依据。',suggested_action:'补充包含复核流程和责任分工的资料后重新发起。'},
              {severity:'blocking',message:'缺少 DOCX 自动检查机制依据。',suggested_action:'补充交付校验规则或缩小研究结论范围。'},
            ]},
          },
        };
        console.log(JSON.stringify({html:hooks.statePanel(card,'failed')}));
        """
    )

    assert "已保留的阶段结果" in result["html"]
    assert "研究阶段结果" in result["html"]
    assert "当前资料仅能支持任务类型和资料门槛。" in result["html"]
    assert "缺少具体复核步骤与责任角色依据。" in result["html"]
    assert "补充包含复核流程和责任分工的资料后重新发起。" in result["html"]
    assert "缺少 DOCX 自动检查机制依据。" in result["html"]
    assert "补充交付校验规则或缩小研究结论范围。" in result["html"]
    assert 'data-et3-result-document' in result["html"]
    assert "查看已保留结果" in result["html"]
    assert "以下结果未被采用" in result["html"]
    assert "<<<TAIJI_META_V1>>>" not in result["html"]
    assert "内部原始错误不应展示" not in result["html"]


def test_v3_evidence_block_offers_prefilled_relaunch_without_blind_retry():
    result = _run_v3_hooks(
        """
        const team={examples:[
          {id:'work-report',document_type:'work_report',launch_profile_id:'content-work-report',available:true},
          {id:'research',document_type:'research_report',launch_profile_id:'research-report',available:true},
        ]};
        const selected=hooks.replacementExample(
          team,
          'research_report',
          '请基于所附资料形成专家团交付机制研究报告。'
        );
        const card={
          productMode:'standalone',publicState:'failed',workflowState:'generated_invalid',
          currentStageId:'research',allowedActions:['resume'],
          productError:{
            schema:'taiji.product.error.v1',code:'expert_team_evidence_required',
            title:'研究依据需要补充',
            message:'当前冻结规格中的依据不足，直接重试不会增加资料。',
            recoveryActions:[{id:'open_result'},{id:'start_new'},{id:'export_diagnostics'}],
          },
          stageResult:{content:'已保留的研究阶段结果'},
        };
        console.log(JSON.stringify({selectedId:selected?.id||'',html:hooks.statePanel(card,'failed')}));
        """
    )

    assert result["selectedId"] == "research"
    assert "重新发起并补充资料" in result["html"]
    assert 'data-et3-action="start-new-task"' in result["html"]
    assert 'data-et3-action="retry-run"' not in result["html"]
    assert "查看已保留结果" in result["html"]

    script = _read(SCRIPT)
    assert "card.brief?.originalRequest" in script
    assert "card.brief?.documentType" in script
    assert "correction: true" in script


def test_v3_evidence_relaunch_click_preserves_task_type_and_original_request():
    result = _run_v3_hooks(
        """
        (async()=>{
          context.document.body={classList:{remove(){}}};
          hooks.setCatalog([{
            id:'deep-research-team',title:'深度材料研究团',examples:[
              {id:'research',document_type:'research_report',launch_profile_id:'research-report',available:true},
            ],
          }]);
          hooks.setCard({
            team:{id:'deep-research-team'},
            brief:{
              originalRequest:'请基于所附资料形成专家团交付机制研究报告。',
              documentType:'research_report',
            },
          });
          const button={dataset:{et3Action:'start-new-task'}};
          await hooks.handleWorkbenchClick({target:{closest(){return button;}}});
          console.log(JSON.stringify(hooks.getRelaunchState()));
        })();
        """
    )

    assert result == {
        "teamId": "deep-research-team",
        "exampleId": "research",
        "suggestionMode": True,
        "prompt": "请基于所附资料形成专家团交付机制研究报告。",
    }


def test_presenter_projects_only_allowlisted_expert_team_diagnostics():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const run={run_id:'run-1',session_id:'session-1',schema_version:3,version:7,
              view:{product_mode:'standalone',public_state:'failed',allowed_actions:['resume'],
                product_error:{schema:'taiji.product.error.v1',code:'model_output_invalid',title:'生成结果格式异常',message:'安全提示',incident_id:'inc-0123456789ab',retryable:true,recovery_actions:[{id:'regenerate',label:'重新生成'},{id:'start_new',label:'重新发起'},{id:'export_diagnostics',label:'导出诊断'},{id:'unsafe_action',label:'不得透传'}]},
                diagnostics:{schema:'expert-team-diagnostics/v1',commit:'abc123',source_mode:'development-linked-worktree',run_id:'run-1',stage_id:'draft',stage_attempt:3,error_code:'model_output_invalid',incident_id:'inc-0123456789ab',blocking_count:1,warning_count:2,provider_error_category:'',delivery_state:'pending',absolute_path:'/Users/example/private.docx',raw_prompt:'不得透传'},
                presentation:{},workflow:{stages:[],current_stage:{id:'draft'},progress:{}},workspace:{}}};
            const card=context.window.buildExpertTeamCardFromRun(run,{});
            console.log(JSON.stringify({diagnostics:card.diagnostics,productError:card.productError}));
            """
        )
    )

    assert result["diagnostics"] == {
        "schema": "expert-team-diagnostics/v1",
        "commit": "abc123",
        "sourceMode": "development-linked-worktree",
        "runId": "run-1",
        "stageId": "draft",
        "stageAttempt": 3,
        "errorCode": "model_output_invalid",
        "incidentId": "inc-0123456789ab",
        "blockingCount": 1,
        "warningCount": 2,
        "providerErrorCategory": "",
        "deliveryState": "pending",
    }
    assert [action["id"] for action in result["productError"]["recoveryActions"]] == [
        "regenerate",
        "start_new",
        "export_diagnostics",
    ]


def test_v3_copies_only_the_allowlisted_diagnostic_summary():
    result = _run_v3_hooks(
        """
        (async()=>{
          let copied='';
          context.window._copyText=async value=>{copied=value;};
          hooks.setCard({runId:'run-1',currentStageId:'draft',deliveryStatus:'pending',
            presentation:{detail:'用户正文和 secret=must-not-copy'},workspace:{path:'/Users/example/private'},
            productError:{schema:'taiji.product.error.v1',code:'model_output_invalid',incidentId:'inc-0123456789ab'},
            diagnostics:{schema:'expert-team-diagnostics/v1',commit:'abc123',sourceMode:'development-linked-worktree',runId:'run-1',stageId:'draft',stageAttempt:3,errorCode:'model_output_invalid',incidentId:'inc-0123456789ab',blockingCount:1,warningCount:2,providerErrorCategory:'',deliveryState:'pending'}});
          const ok=await hooks.copyExpertTeamDiagnostics();
          console.log(JSON.stringify({ok,copied}));
        })();
        """
    )

    assert result["ok"] is True
    assert result["copied"].splitlines() == [
        "commit: abc123",
        "source_mode: development-linked-worktree",
        "run_id: run-1",
        "stage_id: draft",
        "stage_attempt: 3",
        "error_code: model_output_invalid",
        "incident_id: inc-0123456789ab",
        "blocking_count: 1",
        "warning_count: 2",
        "provider_error_category: ",
        "delivery_state: pending",
    ]
    assert "用户正文" not in result["copied"]
    assert "secret" not in result["copied"]
    assert "/Users/" not in result["copied"]


def test_v3_model_output_failure_uses_explicit_regenerate_action():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',readOnly:false,allowedActions:['resume'],presentation:{},
          productError:{schema:'taiji.product.error.v1',code:'model_output_invalid',title:'生成结果格式异常',message:'任务规格和资料已保留。',incidentId:'inc-0123456789ab',recoveryActions:[{id:'regenerate',label:'重新生成'},{id:'export_diagnostics',label:'导出诊断'}]}};
        console.log(JSON.stringify({html:hooks.statePanel(card,'failed')}));
        """
    )

    assert 'data-et3-action="retry-run">重新生成当前阶段</button>' in result["html"]
    assert "配置完成后恢复任务" not in result["html"]


def test_v3_retry_cancel_uses_only_the_complete_server_binding_and_keeps_refresh():
    result = _run_v3_hooks(
        """
        (async()=>{
          const binding={session_id:'session-1',run_id:'run-1',expected_version:11,stage_id:'draft',idempotency_key:'server-cancel-retry-1'};
          const base={
            kind:'expert_team',productMode:'standalone',readOnly:false,
            runId:'run-1',sourceSessionId:'session-1',currentStageId:'draft',version:11,
            publicState:'cancelling',allowedActions:['refresh','retry_cancel'],
            presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:1,total:5},
          };
          const valid={...base,cancelActionBinding:binding};
          const missing={...base,cancelActionBinding:null};
          const validHtml=hooks.statePanel(valid,'cancelling');
          const missingHtml=hooks.statePanel(missing,'cancelling');
          const requests=[];
          context.window.api=async(url,options)=>{requests.push({url,method:options.method,body:JSON.parse(options.body)});return {};};
          function button(){return {dataset:{et3Action:'retry-cancel'},textContent:'重试停止',disabled:false,setAttribute(){}};}
          hooks.setCard(valid);
          await hooks.handleWorkbenchClick({target:{closest(){return button();}}});
          hooks.setCard(missing);
          await hooks.handleWorkbenchClick({target:{closest(){return button();}}});
          console.log(JSON.stringify({validHtml,missingHtml,requests}));
        })();
        """
    )

    assert 'data-et3-action="refresh-run"' in result["validHtml"]
    assert 'data-et3-action="retry-cancel"' in result["validHtml"]
    assert 'data-et3-action="refresh-run"' in result["missingHtml"]
    assert 'data-et3-action="retry-cancel"' not in result["missingHtml"]
    assert result["requests"] == [
        {
            "url": "/api/expert-teams/cancel",
            "method": "POST",
            "body": {
                "session_id": "session-1",
                "run_id": "run-1",
                "expected_version": 11,
                "stage_id": "draft",
                "idempotency_key": "server-cancel-retry-1",
            },
        }
    ]


def test_panel_switch_owns_v3_cleanup_without_changing_non_expert_markup():
    panels = _read(PANELS)
    smoke = _read(ELECTRON_SMOKE)

    assert "window.ExpertTeamV3.clearStatusSurface()" in panels
    assert 'await switchPanel("tasks")' in smoke
    assert "ExpertTeamV3.clearStatusSurface(); await switchPanel" not in smoke


def test_presenter_projects_only_a_complete_hash_bound_delivery_action_binding():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const binding={
              session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',
              stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,
              delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64),
            };
            const summary={document_name:'部门月度工作汇报.docx',delivery_attempt:1,document_sha256:'c'.repeat(64),automatic_check_summary:{status:'passed',passed_count:5,failed_count:0,warning_count:0,blocking_count:0},quality_report_sha256:'d'.repeat(64)};
            function card(candidate=binding,delivery=summary,version=12){
              return context.window.buildExpertTeamCardFromRun({
                run_id:'run-1',session_id:'session-1',schema_version:3,version,
                view:{product_mode:'standalone',public_state:'awaiting_delivery_confirmation',allowed_actions:['delivery_open_document','delivery_open_folder','delivery_revise','delivery_confirm'],delivery_action_binding:candidate,standalone_delivery:delivery,presentation:{},workflow:{stages:[],current_stage:{id:'delivery'},progress:{done:5,total:5,current:'正式文档交付',current_index:4}}},
              },{});
            }
            const valid=card();
            const payload=context.window.buildExpertTeamDeliveryActionPayload(valid,'delivery-idem-1');
            const invalid={};
            for (const [key,value] of Object.entries({session_id:'',run_id:'',expected_version:-1,stage_id:'',stage_attempt:0,artifact_id:'',artifact_sha256:'x',delivery_attempt:0,delivery_binding_sha256:'x',document_sha256:'x'})) invalid[key]=card({...binding,[key]:value}).deliveryActionBinding;
            console.log(JSON.stringify({
              binding:valid.deliveryActionBinding,delivery:valid.standaloneDelivery,payload,invalid,
              wrongSession:card({...binding,session_id:'session-2'}).deliveryActionBinding,
              wrongRun:card({...binding,run_id:'run-2'}).deliveryActionBinding,
              wrongVersion:card({...binding,expected_version:13}).deliveryActionBinding,
            }));
            """
        )
    )

    assert result["binding"] is not None
    assert result["delivery"]["documentName"] == "部门月度工作汇报.docx"
    assert result["delivery"]["automaticCheckSummary"] == {
        "status": "passed",
        "passedCount": 5,
        "failedCount": 0,
        "warningCount": 0,
        "blockingCount": 0,
    }
    assert result["payload"] == {**result["binding"], "idempotency_key": "delivery-idem-1"}
    assert set(result["payload"]) == {
        "session_id", "run_id", "expected_version", "stage_id", "stage_attempt",
        "artifact_id", "artifact_sha256", "delivery_attempt", "delivery_binding_sha256",
        "document_sha256", "idempotency_key",
    }
    assert all(value is None for value in result["invalid"].values())
    assert result["wrongSession"] is None
    assert result["wrongRun"] is None
    assert result["wrongVersion"] is None


def test_presenter_projects_a_separate_strict_delivery_recovery_binding_and_payload():
    result = _run_node(
        textwrap.dedent(
            """
            const fs=require('fs');const vm=require('vm');const context={window:{},console};vm.createContext(context);
            vm.runInContext(fs.readFileSync('static/expert-team-presenter.js','utf8'),context);
            const binding={
              session_id:'session-1',run_id:'run-1',expected_version:17,stage_id:'delivery',
              stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,
              delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64),
            };
            function card(candidate=binding,version=17){
              return context.window.buildExpertTeamCardFromRun({
                run_id:'run-1',session_id:'session-1',schema_version:3,version,
                view:{product_mode:'standalone',public_state:'awaiting_delivery_confirmation',allowed_actions:['delivery_recover'],delivery_recovery_binding:candidate,delivery_status:'delivery_drifted',presentation:{state:'completed_invalid',title:'交付文档已变化'},workflow:{stages:[],current_stage:{id:'delivery'},progress:{done:5,total:5,current:'正式文档交付',current_index:4}}},
              },{});
            }
            const valid=card();
            const payload=context.window.buildExpertTeamDeliveryRecoveryPayload(valid,'recover-idem-1');
            const invalid={};
            for(const [key,value] of Object.entries({session_id:'',run_id:'',expected_version:-1,stage_id:'',stage_attempt:0,artifact_id:'',artifact_sha256:'x',delivery_attempt:0,delivery_binding_sha256:'x',document_sha256:'x'})) invalid[key]=card({...binding,[key]:value}).deliveryRecoveryBinding;
            console.log(JSON.stringify({binding:valid.deliveryRecoveryBinding,payload,deliveryActionBinding:valid.deliveryActionBinding,invalid,wrongVersion:card(binding,18).deliveryRecoveryBinding}));
            """
        )
    )

    assert result["binding"] is not None
    assert result["deliveryActionBinding"] is None
    assert result["payload"] == {**result["binding"], "idempotency_key": "recover-idem-1"}
    assert set(result["payload"]) == {
        "session_id", "run_id", "expected_version", "stage_id", "stage_attempt",
        "artifact_id", "artifact_sha256", "delivery_attempt", "delivery_binding_sha256",
        "document_sha256", "idempotency_key",
    }
    assert all(value is None for value in result["invalid"].values())
    assert result["wrongVersion"] is None


def test_v3_delivery_surface_is_driven_only_by_allowed_actions_and_shows_text_progress():
    result = _run_v3_hooks(
        """
        context.document.documentElement={dataset:{taijiDesktop:'1'}};
        const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
        const base={
          kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,
          publicState:'awaiting_delivery_confirmation',subtitle:'部门月度工作汇报',phase:'正式文档交付',progress:{done:5,total:5,current:'正式文档交付',currentIndex:4},
          workflow:{currentStage:{id:'delivery',title:'正式文档交付'},progress:{done:5,total:5,current:'正式文档交付',current_index:4}},brief:{sources:[]},presentation:{},team:{title:'内容创作专家团'},
          deliveryActionBinding:binding,standaloneDelivery:{documentName:'部门月度工作汇报.docx',automaticCheckSummary:{status:'passed',passedCount:5,failedCount:0,warningCount:0,blockingCount:0}},
        };
        const all=hooks.workbenchHtml({...base,allowedActions:['delivery_open_document','delivery_save_copy','delivery_open_folder','delivery_open_quality_report','delivery_rerender','delivery_revise','delivery_confirm']});
        const openOnly=hooks.statePanel({...base,allowedActions:['delivery_open_document']},'awaiting_delivery_confirmation');
        const confirmOnly=hooks.statePanel({...base,allowedActions:['delivery_confirm']},'awaiting_delivery_confirmation');
        const invalid=hooks.statePanel({...base,allowedActions:['delivery_open_document','delivery_confirm'],deliveryActionBinding:null},'awaiting_delivery_confirmation');
        console.log(JSON.stringify({all,openOnly,confirmOnly,invalid}));
        """
    )

    assert "第 5/5 步 · 正式文档交付" in result["all"]
    assert "部门月度工作汇报.docx" in result["all"]
    assert "自动检查通过 5 项" in result["all"]
    for label in ("打开最终 DOCX", "保存副本", "打开文件夹", "查看质量报告", "仅重新生成 DOCX", "退回修改并重新生成", "确认文档可交付"):
        assert label in result["all"]
    assert "打开最终 DOCX" in result["openOnly"]
    assert "打开文件夹" not in result["openOnly"]
    assert "退回修改并重新生成" not in result["openOnly"]
    assert "确认文档可交付" not in result["openOnly"]
    assert "确认文档可交付" in result["confirmOnly"]
    assert "打开最终 DOCX" not in result["confirmOnly"]
    assert 'data-et3-action="delivery-open-document"' not in result["invalid"]
    assert 'data-et3-action="delivery-confirm"' not in result["invalid"]
    assert "交付操作信息不完整" in result["invalid"]


def test_v3_browser_delivery_open_degrades_to_an_explicit_download_without_server_open():
    result = _run_v3_hooks(
        """
        (async()=>{
          context.document.documentElement={dataset:{}};
          const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
          const card={kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,currentStageId:'delivery',publicState:'completed',allowedActions:['delivery_open_document'],deliveryActionBinding:binding,standaloneDelivery:{documentName:'部门月度工作汇报.docx'},presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5}};
          const live={textContent:'',classList:{toggle(){}},setAttribute(){}};
          const root={querySelector(selector){if(selector==='[data-et3-live]')return live;return null;}};
          let clicked=false;const requests=[];
          context.document.getElementById=id=>id==='expertTeamV3Workbench'?root:null;
          context.document.createElement=()=>({click(){clicked=true;},remove(){},set href(value){this._href=value;},get href(){return this._href;},download:''});
          context.document.body={appendChild(){}};
          context.window.URL={createObjectURL:()=> 'blob:delivery',revokeObjectURL(){}};
          context.window.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true,blob:async()=>({kind:'docx'})};};
          context.window.api=async(url)=>{requests.push({url});return {ok:true};};
          context.window.buildExpertTeamDeliveryActionPayload=(target,key)=>({...target.deliveryActionBinding,idempotency_key:key});
          const button={dataset:{et3Action:'delivery-open-document'},textContent:'下载最终 DOCX',disabled:false,setAttribute(){}};
          hooks.setCard(card);
          const html=hooks.statePanel(card,'completed');
          await hooks.handleWorkbenchClick({target:{closest(){return button;}}});
          console.log(JSON.stringify({html,requests,clicked,live:live.textContent}));
        })();
        """
    )

    assert "下载最终 DOCX" in result["html"]
    assert "打开最终 DOCX" not in result["html"]
    assert [item["url"] for item in result["requests"]] == [
        "/api/expert-teams/delivery/download",
    ]
    assert result["clicked"] is True
    assert result["live"] == "已开始下载最终 DOCX：部门月度工作汇报.docx"


def test_v3_quality_report_is_user_readable_in_workbench_not_a_raw_json_open_action():
    result = _run_v3_hooks(
        """
        const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
        const card={
          kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,
          publicState:'completed',allowedActions:['delivery_open_document','delivery_open_quality_report'],
          deliveryActionBinding:binding,presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5},
          standaloneDelivery:{documentName:'部门月度工作汇报.docx',documentSha256:'c'.repeat(64),qualityReportSha256:'d'.repeat(64),automaticCheckSummary:{status:'passed',passedCount:25,failedCount:0,warningCount:0,blockingCount:0}},
        };
        console.log(JSON.stringify({html:hooks.statePanel(card,'completed')}));
        """
    )

    html = result["html"]
    assert "<details" in html
    assert "文档质量报告" in html
    assert "总体结果" in html
    assert "25 项通过" in html
    assert "已完成本机确认" in html
    assert "底层校验明细已随交付证据保留" in html
    assert 'data-et3-action="delivery-open-quality-report"' not in html


def test_v3_delivery_drift_has_one_explicit_recovery_surface_and_no_stale_file_actions():
    result = _run_v3_hooks(
        """
        const recovery={session_id:'session-1',run_id:'run-1',expected_version:17,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
        const card={
          kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:17,
          publicState:'awaiting_delivery_confirmation',allowedActions:['delivery_recover'],deliveryStatus:'delivery_drifted',deliveryRecoveryBinding:recovery,deliveryActionBinding:null,
          subtitle:'部门月度工作汇报',phase:'正式文档交付',progress:{done:5,total:5,current:'正式文档交付',currentIndex:4},
          workflow:{currentStage:{id:'delivery'},progress:{done:5,total:5,current:'正式文档交付',current_index:4}},brief:{exactTitle:'部门月度工作汇报',sources:[]},presentation:{title:'交付文档已变化'},team:{title:'内容创作专家团'},
        };
        const html=hooks.workbenchHtml(card);
        console.log(JSON.stringify({html}));
        """
    )

    html = result["html"]
    assert "交付文档已变化" in html
    assert "原本机确认已失效" in html
    assert "重新生成 DOCX" in html
    assert 'data-et3-action="delivery-recover"' in html
    assert 'data-et3-action="delivery-open-document"' not in html
    assert 'data-et3-action="delivery-open-folder"' not in html
    assert 'data-et3-action="delivery-confirm"' not in html
    assert 'data-et3-action="submit-delivery-revision"' not in html


def test_v3_delivery_recovery_posts_only_the_server_bound_identity_and_never_a_path():
    result = _run_v3_hooks(
        """
        (async()=>{
          const recovery={session_id:'session-1',run_id:'run-1',expected_version:17,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
          const card={kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:17,currentStageId:'delivery',publicState:'awaiting_delivery_confirmation',allowedActions:['delivery_recover'],deliveryStatus:'delivery_drifted',deliveryRecoveryBinding:recovery,presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5}};
          const live={textContent:'',classList:{toggle(){}},setAttribute(){}};
          const root={querySelector(selector){if(selector==='[data-et3-live]')return live;return null;}};
          context.document.getElementById=id=>id==='expertTeamV3Workbench'?root:null;
          context.window.buildExpertTeamDeliveryRecoveryPayload=(target,key)=>({...target.deliveryRecoveryBinding,idempotency_key:key});
          const requests=[];
          context.window.api=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true};};
          const button={dataset:{et3Action:'delivery-recover'},textContent:'重新生成 DOCX',disabled:false,setAttribute(){}};
          hooks.setCard(card);
          await hooks.handleWorkbenchClick({target:{closest(){return button;}}});
          console.log(JSON.stringify({requests,live:live.textContent,publicState:hooks.getCard().publicState}));
        })();
        """
    )

    assert len(result["requests"]) == 1
    assert result["requests"][0]["url"] == "/api/expert-teams/delivery/recover"
    body = result["requests"][0]["body"]
    assert set(body) == {
        "session_id", "run_id", "expected_version", "stage_id", "stage_attempt",
        "artifact_id", "artifact_sha256", "delivery_attempt", "delivery_binding_sha256",
        "document_sha256", "idempotency_key",
    }
    assert not any("path" in key for key in body)
    assert result["publicState"] == "awaiting_delivery_confirmation"


def test_v3_delivery_requests_are_hash_bound_never_send_paths_and_do_not_fake_completion():
    result = _run_v3_hooks(
        """
        (async()=>{
          context.document.documentElement={dataset:{taijiDesktop:'1'}};
          const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
          const card={kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,currentStageId:'delivery',publicState:'awaiting_delivery_confirmation',allowedActions:['delivery_open_document','delivery_open_folder','delivery_rerender','delivery_revise','delivery_confirm'],deliveryActionBinding:binding,presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5}};
          let feedback='';const live={textContent:'',classList:{toggle(){}},setAttribute(){}};
          const feedbackField={get value(){return feedback;},set value(value){feedback=value;},setAttribute(){},removeAttribute(){},focus(){}};
          const root={querySelector(selector){if(selector==='[data-et3-delivery-revision]')return feedbackField;if(selector==='[data-et3-live]')return live;return null;}};
          context.document.getElementById=id=>id==='expertTeamV3Workbench'?root:null;
          context.window.buildExpertTeamDeliveryActionPayload=(target,key)=>({...target.deliveryActionBinding,idempotency_key:key});
          const requests=[];
          context.window.api=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true};};
          function button(action){return {dataset:{et3Action:action},textContent:action,disabled:false,setAttribute(){}};}
          hooks.setCard(card);
          await hooks.handleWorkbenchClick({target:{closest(){return button('delivery-open-document');}}});
          await hooks.handleWorkbenchClick({target:{closest(){return button('delivery-open-folder');}}});
          await hooks.handleWorkbenchClick({target:{closest(){return button('delivery-rerender');}}});
          await hooks.handleWorkbenchClick({target:{closest(){return button('submit-delivery-revision');}}});
          const emptyMessage=live.textContent;
          feedback='补充第三部分负责人和时间节点';
          await hooks.handleWorkbenchClick({target:{closest(){return button('submit-delivery-revision');}}});
          await hooks.handleWorkbenchClick({target:{closest(){return button('delivery-confirm');}}});
          console.log(JSON.stringify({requests,emptyMessage,publicState:hooks.getCard().publicState}));
        })();
        """
    )

    assert result["emptyMessage"] == "请先填写需要修改的内容；如果文档无需修改，请确认可交付。"
    assert [item["url"] for item in result["requests"]] == [
        "/api/expert-teams/delivery/open",
        "/api/expert-teams/delivery/open",
        "/api/expert-teams/delivery/rerender",
        "/api/expert-teams/delivery/revise",
        "/api/expert-teams/delivery/confirm",
    ]
    assert result["requests"][0]["body"]["target"] == "document"
    assert result["requests"][1]["body"]["target"] == "folder"
    assert "feedback" not in result["requests"][2]["body"]
    assert result["requests"][3]["body"]["feedback"] == "补充第三部分负责人和时间节点"
    assert all("path" not in item["body"] for item in result["requests"])
    for item in result["requests"]:
        for field in (
            "session_id", "run_id", "expected_version", "stage_id", "stage_attempt",
            "artifact_id", "artifact_sha256", "delivery_attempt", "delivery_binding_sha256",
            "document_sha256", "idempotency_key",
        ):
            assert field in item["body"]
    assert result["publicState"] == "awaiting_delivery_confirmation"


def test_v3_delivery_save_copy_uses_native_directory_picker_and_legacy_quality_report_click_is_inert():
    result = _run_v3_hooks(
        """
        (async()=>{
          const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
          const card={kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,currentStageId:'delivery',publicState:'awaiting_delivery_confirmation',allowedActions:['delivery_save_copy','delivery_open_quality_report'],deliveryActionBinding:binding,standaloneDelivery:{documentName:'部门月度工作汇报.docx'},presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5}};
          const live={textContent:'',classList:{toggle(){}},setAttribute(){}};
          const root={querySelector(selector){if(selector==='[data-et3-live]')return live;return null;}};
          context.document.getElementById=id=>id==='expertTeamV3Workbench'?root:null;
          context.window.buildExpertTeamDeliveryActionPayload=(target,key)=>({...target.deliveryActionBinding,idempotency_key:key});
          context.window.taijiDesktop={pickDirectory:async()=>({ok:true,path:'/Users/example/Documents'})};
          const requests=[];
          context.window.api=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true,saved_name:'部门月度工作汇报.docx'};};
          function button(action){return {dataset:{et3Action:action},textContent:action,disabled:false,setAttribute(){}};}
          hooks.setCard(card);
          await hooks.handleWorkbenchClick({target:{closest(){return button('delivery-save-copy');}}});
          const saveMessage=live.textContent;
          await hooks.handleWorkbenchClick({target:{closest(){return button('delivery-open-quality-report');}}});
          console.log(JSON.stringify({requests,saveMessage,finalMessage:live.textContent}));
        })();
        """
    )

    assert [item["url"] for item in result["requests"]] == [
        "/api/expert-teams/delivery/save-copy",
    ]
    save_body = result["requests"][0]["body"]
    assert save_body["destination_dir"] == "/Users/example/Documents"
    assert "path" not in save_body
    assert "filename" not in save_body
    assert result["saveMessage"] == "已保存副本：部门月度工作汇报.docx"
    assert result["finalMessage"] == result["saveMessage"]


def test_v3_delivery_save_copy_falls_back_to_an_explicit_browser_download():
    result = _run_v3_hooks(
        """
        (async()=>{
          const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
          const card={kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,currentStageId:'delivery',publicState:'completed',allowedActions:['delivery_save_copy'],deliveryActionBinding:binding,standaloneDelivery:{documentName:'部门月度工作汇报.docx'},presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5}};
          const live={textContent:'',classList:{toggle(){}},setAttribute(){}};
          const root={querySelector(selector){if(selector==='[data-et3-live]')return live;return null;}};
          let clicked=false;let revoked='';const requests=[];
          context.document.getElementById=id=>id==='expertTeamV3Workbench'?root:null;
          context.document.createElement=()=>({click(){clicked=true;},remove(){},set href(value){this._href=value;},get href(){return this._href;},download:''});
          context.document.body={appendChild(){}};
          context.window.URL={createObjectURL:()=> 'blob:delivery',revokeObjectURL:value=>{revoked=value;}};
          context.window.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body),credentials:options.credentials});return {ok:true,blob:async()=>({kind:'docx'})};};
          context.window.buildExpertTeamDeliveryActionPayload=(target,key)=>({...target.deliveryActionBinding,idempotency_key:key});
          const button={dataset:{et3Action:'delivery-save-copy'},textContent:'保存副本',disabled:false,setAttribute(){}};
          hooks.setCard(card);
          await hooks.handleWorkbenchClick({target:{closest(){return button;}}});
          console.log(JSON.stringify({requests,clicked,revoked,live:live.textContent}));
        })();
        """
    )

    assert len(result["requests"]) == 1
    assert result["requests"][0]["url"] == "/api/expert-teams/delivery/download"
    assert result["requests"][0]["credentials"] == "include"
    assert "path" not in result["requests"][0]["body"]
    assert result["clicked"] is True
    assert result["revoked"] == "blob:delivery"
    assert result["live"] == "已开始下载副本：部门月度工作汇报.docx"
