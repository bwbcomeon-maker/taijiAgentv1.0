from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_JS = (REPO_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
SESSIONS_JS = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
PRESENTER_JS = (REPO_ROOT / "static" / "expert-team-presenter.js").read_text(encoding="utf-8")
EXPERT_UI_JS = (REPO_ROOT / "static" / "expert-team-ui.js").read_text(encoding="utf-8")
ACTIONS_JS = (REPO_ROOT / "static" / "expert-team-actions.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_expert_team_scripts_are_loaded_before_legacy_ui_shell():
    assert "static/expert-team-presenter.js" in INDEX_HTML
    assert "static/expert-team-ui.js" in INDEX_HTML
    assert "static/expert-team-actions.js" in INDEX_HTML
    assert INDEX_HTML.index("static/expert-team-presenter.js") < INDEX_HTML.index("static/ui.js")
    assert INDEX_HTML.index("static/expert-team-ui.js") < INDEX_HTML.index("static/ui.js")


def test_presenter_is_the_only_source_of_main_state_and_action():
    assert "function buildExpertTeamPresentation" in PRESENTER_JS
    assert "function buildExpertTeamCardFromRun" in PRESENTER_JS
    assert "view.presentation" in PRESENTER_JS
    assert "presentation.state" in PRESENTER_JS
    assert "presentation.primary_action" in PRESENTER_JS
    assert "window.buildExpertTeamPresentation=buildExpertTeamPresentation" in PRESENTER_JS
    assert "window.buildExpertTeamCardFromRun=buildExpertTeamCardFromRun" in PRESENTER_JS

    for old_source in ("statusLabel", "can_retry", "stage_confirmation_points"):
        assert old_source not in PRESENTER_JS


def test_ui_shell_delegates_expert_team_cards_to_presenter():
    assert "function _expertTeamStatusCardFromRun" in UI_JS
    fn_start = UI_JS.index("function _expertTeamStatusCardFromRun")
    fn_body = UI_JS[fn_start : UI_JS.index("function _isExpertTeamStatusCard", fn_start)]
    assert "buildExpertTeamCardFromRun(run)" in fn_body
    assert "run.view||{}" not in fn_body
    assert "stage_confirmation_points" not in fn_body


def test_no_chat_confirmation_card_or_delivery_scanner_remains():
    joined = "\n".join([COMMANDS_JS, PANELS_JS, SESSIONS_JS, UI_JS, PRESENTER_JS, EXPERT_UI_JS, ACTIONS_JS])
    assert "syncExpertTeamChatConfirmationCard" not in joined
    assert "_expertTeamChatConfirmationCardHtml" not in joined
    assert "_expertTeamDeliveryMessageInfo" not in joined
    assert "openExpertTeamQuestionPopover(this)" not in joined
    assert "去确认" not in EXPERT_UI_JS or "expert-team-lifecycle-card" not in EXPERT_UI_JS


def test_session_hydration_uses_tri_state_and_preserve_blocks_writeflow_fallback():
    hydrate_start = SESSIONS_JS.index("async function _hydrateExpertTeamStatusCardForSession")
    hydrate_body = SESSIONS_JS[hydrate_start : SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession", hydrate_start)]
    assert "return {status:'handled'};" in hydrate_body
    assert "return {status:'preserved'};" in hydrate_body
    assert "return {status:'missing'};" in hydrate_body
    assert "shouldPreserveExpertTeamDraftDock(sid)" in hydrate_body
    assert hydrate_body.index("shouldPreserveExpertTeamDraftDock(sid)") < hydrate_body.index("api(`/api/expert-teams/run?session_id=")

    refresh_start = SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession")
    refresh_body = SESSIONS_JS[refresh_start : SESSIONS_JS.index("async function refreshWriteflowStatusDockForActiveSession", refresh_start)]
    assert "expertTeamHydration.status==='handled'" in refresh_body
    assert "expertTeamHydration.status==='preserved'" in refresh_body
    assert "expertTeamHydration.status==='missing'" in refresh_body
    assert refresh_body.index("expertTeamHydration.status==='missing'") < refresh_body.index("/api/writeflow/run?session_id=")


def test_dock_and_workspace_render_from_single_presentation():
    assert "function renderExpertTeamDockFromPresentation" in EXPERT_UI_JS
    assert "function renderExpertTeamWorkspaceFromPresentation" in EXPERT_UI_JS
    assert "card.presentation" in EXPERT_UI_JS
    assert "presentation.primaryAction" in EXPERT_UI_JS
    assert "presentation.state" in EXPERT_UI_JS
    assert "专家团正在生成" in EXPERT_UI_JS
    assert "草稿未通过校验" in EXPERT_UI_JS
    assert "阶段成果待复核" in EXPERT_UI_JS


def test_actions_map_only_presentation_actions_to_api_calls():
    assert "function handleExpertTeamPresentationAction" in ACTIONS_JS
    assert "answer_required" in ACTIONS_JS
    assert "answer_optional" in ACTIONS_JS
    assert "start_generation" in ACTIONS_JS
    assert "review_stage" in ACTIONS_JS
    assert "regenerate" in ACTIONS_JS
    assert "view_result" in ACTIONS_JS
    assert "/api/expert-teams/stage/approve" in ACTIONS_JS
    assert "/api/expert-teams/resume" in ACTIONS_JS
    assert "/api/writeflow/run" not in ACTIONS_JS


def test_modal_examples_are_office_material_templates_not_long_prompt_cards():
    modal_start = PANELS_JS.index("function openWriteflowTeamModal")
    modal_body = PANELS_JS[modal_start : PANELS_JS.index("function closeWriteflowTeamModal", modal_start)]
    assert "writeflow-example-label" in modal_body
    assert "writeflow-example-summary" in modal_body
    assert "writeflow-example-prompt-preview" in modal_body
    assert "公众号长文" not in modal_body
    assert "完整 prompt" not in modal_body
    assert ".writeflow-modal-prompt-card" in STYLE_CSS
    assert "-webkit-line-clamp:2" in STYLE_CSS


def test_default_user_visible_copy_has_no_public_account_language():
    joined = "\n".join([COMMANDS_JS, PANELS_JS, SESSIONS_JS, UI_JS, PRESENTER_JS, EXPERT_UI_JS, ACTIONS_JS])
    for text in ("公众号长文", "文章大纲", "标题党", "你有没有", "读者", "封面配图", "发布前检查"):
        assert text not in joined
