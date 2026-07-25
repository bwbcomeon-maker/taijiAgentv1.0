from pathlib import Path
from io import BytesIO
import json
import subprocess
import textwrap
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
SCRIPT = ROOT / "static" / "expert-team-v3.js"
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
                draftFingerprint, restoreWorkbenchDraft, stateCopyFor, statePanel, workbenchHtml,
                handleWorkbenchClick, deliveryActionControl,
                briefPanel, buildBriefPatch, clientBriefFieldErrors,
                setConflictDraft(value) {{ state.conflictRevisionDraft = value; }},
                setConflictDeliveryDraft(value) {{ state.conflictDeliveryDraft = value; }},
                setCard(value) {{ state.card = value; state.busy = false; }},
                getCard() {{ return state.card; }},
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


def test_v3_profile_fields_render_chinese_help_and_associated_inline_errors():
    result = _run_v3_hooks(
        """
        const card={productMode:'standalone',allowedActions:['answer'],questions:[],brief:{
          originalRequest:'形成专题研究报告',documentTypeLabel:'研究报告',sources:[],
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
        const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
        const base={
          kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,
          publicState:'awaiting_delivery_confirmation',subtitle:'部门月度工作汇报',phase:'正式文档交付',progress:{done:5,total:5,current:'正式文档交付',currentIndex:4},
          workflow:{currentStage:{id:'delivery',title:'正式文档交付'},progress:{done:5,total:5,current:'正式文档交付',current_index:4}},brief:{sources:[]},presentation:{},team:{title:'内容创作专家团'},
          deliveryActionBinding:binding,standaloneDelivery:{documentName:'部门月度工作汇报.docx',automaticCheckSummary:{status:'passed',passedCount:5,failedCount:0,warningCount:0,blockingCount:0}},
        };
        const all=hooks.workbenchHtml({...base,allowedActions:['delivery_open_document','delivery_open_folder','delivery_revise','delivery_confirm']});
        const openOnly=hooks.statePanel({...base,allowedActions:['delivery_open_document']},'awaiting_delivery_confirmation');
        const confirmOnly=hooks.statePanel({...base,allowedActions:['delivery_confirm']},'awaiting_delivery_confirmation');
        const invalid=hooks.statePanel({...base,allowedActions:['delivery_open_document','delivery_confirm'],deliveryActionBinding:null},'awaiting_delivery_confirmation');
        console.log(JSON.stringify({all,openOnly,confirmOnly,invalid}));
        """
    )

    assert "第 5/5 步 · 正式文档交付" in result["all"]
    assert "部门月度工作汇报.docx" in result["all"]
    assert "自动检查通过 5 项" in result["all"]
    for label in ("打开文档", "打开所在文件夹", "退回修改并重新生成", "确认文档可交付"):
        assert label in result["all"]
    assert "打开文档" in result["openOnly"]
    assert "打开所在文件夹" not in result["openOnly"]
    assert "退回修改并重新生成" not in result["openOnly"]
    assert "确认文档可交付" not in result["openOnly"]
    assert "确认文档可交付" in result["confirmOnly"]
    assert "打开文档" not in result["confirmOnly"]
    assert 'data-et3-action="delivery-open-document"' not in result["invalid"]
    assert 'data-et3-action="delivery-confirm"' not in result["invalid"]
    assert "交付操作信息不完整" in result["invalid"]


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
          const binding={session_id:'session-1',run_id:'run-1',expected_version:12,stage_id:'delivery',stage_attempt:1,artifact_id:'delivery:1',artifact_sha256:'a'.repeat(64),delivery_attempt:1,delivery_binding_sha256:'b'.repeat(64),document_sha256:'c'.repeat(64)};
          const card={kind:'expert_team',productMode:'standalone',readOnly:false,runId:'run-1',sourceSessionId:'session-1',version:12,currentStageId:'delivery',publicState:'awaiting_delivery_confirmation',allowedActions:['delivery_open_document','delivery_open_folder','delivery_revise','delivery_confirm'],deliveryActionBinding:binding,presentation:{},workflow:{currentStage:{}},brief:{sources:[]},progress:{done:5,total:5}};
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
        "/api/expert-teams/delivery/revise",
        "/api/expert-teams/delivery/confirm",
    ]
    assert result["requests"][0]["body"]["target"] == "document"
    assert result["requests"][1]["body"]["target"] == "folder"
    assert result["requests"][2]["body"]["feedback"] == "补充第三部分负责人和时间节点"
    assert all("path" not in item["body"] for item in result["requests"])
    for item in result["requests"]:
        for field in (
            "session_id", "run_id", "expected_version", "stage_id", "stage_attempt",
            "artifact_id", "artifact_sha256", "delivery_attempt", "delivery_binding_sha256",
            "document_sha256", "idempotency_key",
        ):
            assert field in item["body"]
    assert result["publicState"] == "awaiting_delivery_confirmation"
