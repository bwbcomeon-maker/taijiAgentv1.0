from pathlib import Path
from io import BytesIO
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
SCRIPT = ROOT / "static" / "expert-team-v3.js"
STYLE = ROOT / "static" / "expert-team-v3.css"
PANELS = ROOT / "static" / "panels.js"
ELECTRON_SMOKE = ROOT / "tests" / "expert_team_v3_electron_smoke.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def test_v3_exposes_every_runtime_state_as_a_user_actionable_screen():
    script = _read(SCRIPT)

    for state in (
        "collecting_required",
        "collecting_optional",
        "ready_to_generate",
        "generating",
        "awaiting_stage_input",
        "awaiting_review",
        "revising",
        "office_acceptance_required",
        "completed",
        "legacy_read_only",
    ):
        assert state in script
    for label in (
        "加入修改意见",
        "提交修改意见",
        "无修改，进入下一阶段",
        "Office 验收",
        "打开最终 DOCX",
    ):
        assert label in script


def test_v3_dialog_and_workbench_have_keyboard_and_live_feedback_contracts():
    script = _read(SCRIPT)

    assert 'role="dialog"' in script
    assert 'aria-modal="true"' in script
    assert 'aria-live="polite"' in script
    assert "event.key === 'Escape'" in script or 'event.key === "Escape"' in script
    assert "focus()" in script
    assert "trapDialogFocus" in script
    assert 'data-et3-action="choose-source-file"' in script
    assert 'data-et3-action="choose-office-evidence"' in script
    assert 'class="et3-visually-hidden"' in script


def test_v3_preserves_drafts_and_saves_brief_fields_before_answering():
    script = _read(SCRIPT)

    assert "captureWorkbenchDraft" in script
    assert "restoreWorkbenchDraft" in script
    assert "await saveBriefFields(button," in script
    assert "Object.values(patch).some(Boolean)" not in script
    assert "question__" in script


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


def test_v3_draft_and_office_evidence_are_bound_to_authoritative_objects():
    script = _read(SCRIPT)

    assert "draftFingerprint" in script
    assert "stageReviewId" in script
    assert "documentSha256" in script
    assert "officeEvidenceKey" in script


def test_v3_has_a_real_electron_flow_and_non_expert_isolation_gate():
    smoke = _read(ELECTRON_SMOKE)

    assert "_electron.launch" in smoke
    assert "#expertTeamV3PortalRoot" in smoke
    assert "#expertTeamV3Workbench" in smoke
    assert "加入修改意见" in smoke
    assert "无修改，进入下一阶段" in smoke
    assert "switchPanel(\"tasks\")" in smoke
    assert "expert-team-v3-active" in smoke
    assert "page.screenshot" in smoke


def test_v3_stage_approval_is_fail_closed_behind_trusted_approver_identity():
    script = _read(SCRIPT)

    assert "/api/expert-teams/identity/status" in script
    assert "/api/expert-teams/identity/start" in script
    assert "document-approver" in script
    assert "使用企业审批身份登录" in script
    assert "data-et3-identity-action" in script


def test_v3_office_flow_matches_the_enterprise_review_contract():
    script = _read(SCRIPT)

    for check in (
        "document_opened",
        "title_and_cover_match",
        "genre_and_structure_match",
        "content_order_correct",
        "figures_unique_and_readable",
        "tables_readable",
        "headers_footers_pagination",
        "no_placeholders_or_workflow_text",
        "citations_readable",
    ):
        assert check in script
    for contract in (
        "document-reviewer",
        "/api/docx-engine-v2/quality/wps-visual/begin",
        "/api/docx-engine-v2/quality/wps-visual/evidence",
        "/api/docx-engine-v2/quality/wps-visual",
        "/api/expert-teams/office-revisions/create",
        "data-et3-office-evidence",
        "data-et3-office-issue",
    ):
        assert contract in script
    assert "mutate('/api/expert-teams/stage/revise'" not in script.split("function submitOffice", 1)[-1]


def test_panel_switch_owns_v3_cleanup_without_changing_non_expert_markup():
    panels = _read(PANELS)
    smoke = _read(ELECTRON_SMOKE)

    assert "window.ExpertTeamV3.clearStatusSurface()" in panels
    assert 'await switchPanel("tasks")' in smoke
    assert "ExpertTeamV3.clearStatusSurface(); await switchPanel" not in smoke
