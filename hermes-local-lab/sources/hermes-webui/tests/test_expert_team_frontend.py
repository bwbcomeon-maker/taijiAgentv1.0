import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PY = REPO_ROOT / "api" / "expert_teams" / "catalog.py"
COMMANDS_JS = (REPO_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
SESSIONS_JS = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
PRESENTER_JS = (REPO_ROOT / "static" / "expert-team-presenter.js").read_text(encoding="utf-8")
EXPERT_UI_JS = (REPO_ROOT / "static" / "expert-team-ui.js").read_text(encoding="utf-8")
ACTIONS_JS = (REPO_ROOT / "static" / "expert-team-actions.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _load_expert_team_catalog():
    spec = importlib.util.spec_from_file_location("_expert_team_catalog_test", CATALOG_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expert_team_scripts_are_loaded_before_legacy_ui_shell():
    assert "static/expert-team-presenter.js" in INDEX_HTML
    assert "static/expert-team-ui.js" in INDEX_HTML
    assert "static/expert-team-actions.js" in INDEX_HTML
    assert INDEX_HTML.index("static/expert-team-presenter.js") < INDEX_HTML.index("static/ui.js")
    assert INDEX_HTML.index("static/expert-team-ui.js") < INDEX_HTML.index("static/ui.js")


def test_public_expert_team_catalog_images_exist():
    catalog = _load_expert_team_catalog()._CATALOG
    for team in catalog.values():
        for image_ref in [team.get("image"), *(member.get("image") for member in team.get("members", []))]:
            assert image_ref
            assert (REPO_ROOT / image_ref).exists(), f"missing expert team image: {image_ref}"


def test_presenter_is_the_only_source_of_main_state_and_action():
    assert "function buildExpertTeamPresentation" in PRESENTER_JS
    assert "function buildExpertTeamCardFromRun" in PRESENTER_JS
    assert "view.presentation" in PRESENTER_JS
    assert "presentation.state" in PRESENTER_JS
    assert "presentation.primary_action" in PRESENTER_JS
    assert "view.workspace" in PRESENTER_JS
    assert "view.team" in PRESENTER_JS
    assert "view.workflow" in PRESENTER_JS
    assert "view.pending_input" in PRESENTER_JS
    assert "view.stage_result" in PRESENTER_JS
    assert "window.buildExpertTeamPresentation=buildExpertTeamPresentation" in PRESENTER_JS
    assert "window.buildExpertTeamCardFromRun=buildExpertTeamCardFromRun" in PRESENTER_JS

    assert "statusLabel:STATE_LABELS[state]" in PRESENTER_JS
    assert "presentation.statusLabel" in EXPERT_UI_JS
    for old_source in ("can_retry", "stage_confirmation_points"):
        assert old_source not in PRESENTER_JS
    assert "view.timeline_events" in PRESENTER_JS
    assert "member&&member.image" in PRESENTER_JS


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
    assert "return {status:'preserved',reason:'transient_error'};" in hydrate_body
    assert "return {status:'missing',reason:'not_found'};" in hydrate_body
    assert "shouldPreserveExpertTeamDraftDock(sid)" not in hydrate_body
    assert "api(`/api/expert-teams/run?session_id=" in hydrate_body

    refresh_start = SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession")
    refresh_body = SESSIONS_JS[refresh_start : SESSIONS_JS.index("async function refreshWriteflowStatusDockForActiveSession", refresh_start)]
    assert "expertTeamHydration.status==='handled'" in refresh_body
    assert "expertTeamHydration.status==='preserved'" in refresh_body
    assert "expertTeamHydration.status==='missing'" in refresh_body
    assert "/api/writeflow/run?session_id=" not in refresh_body


def test_expert_team_workspace_panel_is_right_side_surface_and_not_bottom_dock():
    assert "function renderExpertTeamWorkspaceFromPresentation" in EXPERT_UI_JS
    assert "function mountExpertTeamWorkspacePanel" in UI_JS
    assert "function renderExpertTeamStatusSurface" in UI_JS
    assert "renderExpertTeamStatusSurface(card)" in UI_JS
    mount_start = UI_JS.index("function mountExpertTeamWorkspacePanel")
    mount_body = UI_JS[mount_start : UI_JS.index("function _expertTeamWorkspaceStorageKey", mount_start)]
    assert "document.getElementById('mainChat')" in mount_body
    assert "mainChat.insertBefore(panel,messagesShell)" in mount_body
    assert "messages.insertBefore(panel,msgInner)" not in mount_body
    assert ".expert-team-workspace-panel{display:none;}" not in STYLE_CSS
    desktop_css = STYLE_CSS[STYLE_CSS.index('@media (min-width:901px)') :]
    assert ".taiji-home-shell.taiji-expert-team-active .expert-team-workspace-panel{display:none!important;}" not in desktop_css
    assert ".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock{display:none!important;}" in desktop_css
    assert "right:clamp(" in desktop_css
    assert "width:clamp(240px" in desktop_css or "width:clamp(260px" in desktop_css


def test_expert_team_workspace_uses_reserved_layout_not_overlay():
    assert "Expert team desktop split workspace" in STYLE_CSS
    assert "grid-template-columns:minmax(0,1fr) clamp(380px,36%,500px)!important;" in STYLE_CSS
    assert "grid-template-rows:minmax(0,1fr) auto!important;" in STYLE_CSS
    assert "grid-row:1 / span 2!important;" in STYLE_CSS
    assert "position:relative!important;" in STYLE_CSS
    assert "transform:none!important;" in STYLE_CSS
    assert "grid-column:1!important;" in STYLE_CSS
    assert "grid-row:2!important;" in STYLE_CSS
    assert ".expert-team-workspace-panel .expert-team-question-popover{" in STYLE_CSS
    assert ".expert-team-workspace-panel .expert-team-stage-actions{" in STYLE_CSS
    assert "grid-template-areas:\"locate\" \"approve\" \"revise\" \"feedback\";" in STYLE_CSS
    assert ".expert-team-workspace-panel .expert-team-stage-approve{" in STYLE_CSS
    assert "justify-self:stretch!important;" in STYLE_CSS
    assert "min-width:0!important;" in STYLE_CSS
    assert "@media (min-width:901px) and (max-width:1320px)" in STYLE_CSS
    assert "max-height:min(46vh,360px)!important;" not in STYLE_CSS
    assert "data-expert-team-workspace-mode" in EXPERT_UI_JS


def test_expert_team_workspace_uses_summary_tabs_and_confirmation_wizard():
    assert "expert-team-panel-tabs" in EXPERT_UI_JS
    assert "data-expert-team-workspace-tab=\"task\"" in EXPERT_UI_JS
    assert "data-expert-team-workspace-tab=\"process\"" in EXPERT_UI_JS
    assert "data-expert-team-workspace-tab=\"todo\"" not in EXPERT_UI_JS
    assert "data-expert-team-workspace-tab=\"collaboration\"" not in EXPERT_UI_JS
    assert "data-expert-team-workspace-tab=\"flow\"" not in EXPERT_UI_JS
    assert "data-expert-team-workspace-tab=\"members\"" not in EXPERT_UI_JS
    assert "data-expert-team-workspace-tab=\"result\"" in EXPERT_UI_JS
    assert "<span>任务</span>" in EXPERT_UI_JS
    assert "<span>成果</span>" in EXPERT_UI_JS
    assert "<span>过程</span>" in EXPERT_UI_JS
    assert "AI 阶段协作状态" in EXPERT_UI_JS
    assert "不代表独立人工专家审计" in EXPERT_UI_JS


def test_current_task_panel_precedes_gates_and_brief_in_the_expanded_workspace():
    expanded = EXPERT_UI_JS.split('id="expert-team-workspace-expanded"', 1)[1]
    assert expanded.index("tabPanel('task',todoPanelHtml,true)") < expanded.index("${completionGatesHtml}")
    assert expanded.index("tabPanel('task',todoPanelHtml,true)") < expanded.index("${briefCardHtml}")
    assert "expert-team-confirmation-wizard" in EXPERT_UI_JS
    assert "需求确认 1/" in EXPERT_UI_JS
    assert "确认并下一题" in EXPERT_UI_JS
    assert "保存草稿" in EXPERT_UI_JS
    assert "稍后处理" in EXPERT_UI_JS
    assert "expert-team-primary-task-card" in EXPERT_UI_JS
    assert "expert-team-collaboration-card" in EXPERT_UI_JS
    assert "expert-team-collaboration-grid" in EXPERT_UI_JS
    assert "expert-team-collaboration-current" in EXPERT_UI_JS
    assert "当前" in EXPERT_UI_JS
    assert "generated_invalid:'草稿未通过校验'" in EXPERT_UI_JS
    assert "function tabStatusText" in EXPERT_UI_JS
    assert "return status==='generated_invalid'||status==='failed'?'需处理':statusText(status)" in EXPERT_UI_JS
    assert "const currentCollaborationState='当前处理'" in EXPERT_UI_JS
    assert "正在处理：" in EXPERT_UI_JS
    assert "data-expert-team-workspace-mode=\"confirm\"" in EXPERT_UI_JS
    assert "window.restoreExpertTeamWorkspaceTab=restoreExpertTeamWorkspaceTab" in ACTIONS_JS
    assert "function normalizeExpertTeamWorkspaceTab" in ACTIONS_JS
    assert "if(tab==='todo')return 'task'" in ACTIONS_JS
    assert "if(tab==='flow'||tab==='members'||tab==='collaboration')return 'process'" in ACTIONS_JS
    assert "restoreExpertTeamWorkspaceTab(panel)" in UI_JS
    assert "expert-team-member-list" in EXPERT_UI_JS
    assert "expert-team-member-row" in EXPERT_UI_JS
    assert "expert-team-member-state" in EXPERT_UI_JS
    assert ".expert-team-collaboration-grid{display:grid" in STYLE_CSS
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in STYLE_CSS
    assert "grid-auto-rows:minmax(74px,1fr)" in STYLE_CSS
    assert ".expert-team-collaboration-current-copy small{min-width:0;color:var(--muted);font-size:11px;line-height:1.35;font-weight:720;white-space:normal;overflow:visible;text-overflow:clip;" in STYLE_CSS
    assert ".expert-team-collaboration-grid .expert-team-member-row{grid-template-columns:38px minmax(0,1fr);grid-template-areas:\"avatar copy\" \"avatar state\"" in STYLE_CSS
    assert ".expert-team-collaboration-card" in STYLE_CSS
    assert ".expert-team-member-row{min-width:0;display:grid" in STYLE_CSS


def test_expert_team_workspace_preserves_scroll_on_same_run_refresh():
    assert "function _captureExpertTeamWorkspaceScrollState" in UI_JS
    assert "function _restoreExpertTeamWorkspaceScrollState" in UI_JS
    assert ".expert-team-panel-expanded-body" in UI_JS
    assert "bottomGap<=8?max" in UI_JS
    mount_start = UI_JS.index("function mountExpertTeamWorkspacePanel")
    mount_body = UI_JS[mount_start : UI_JS.index("function _expertTeamWorkspaceStorageKey", mount_start)]
    assert "const popoverState=_captureExpertTeamQuestionPopoverState(panel);" in mount_body
    assert "const scrollState=popoverState.scrollState;" in mount_body
    assert "panel.innerHTML=typeof renderExpertTeamWorkspaceFromPresentation" in mount_body
    assert "restoreExpertTeamWorkspaceTab(panel)" in mount_body
    assert "_restoreExpertTeamWorkspaceScrollState(panel,scrollState);" in mount_body
    assert mount_body.index("const popoverState=_captureExpertTeamQuestionPopoverState(panel);") < mount_body.index("panel.innerHTML=typeof renderExpertTeamWorkspaceFromPresentation")
    assert mount_body.index("restoreExpertTeamWorkspaceTab(panel)") < mount_body.index("_restoreExpertTeamWorkspaceScrollState(panel,scrollState);")


def test_workspace_panel_can_collapse_and_expand_without_becoming_chat_message():
    assert "function toggleExpertTeamWorkspacePanel" in UI_JS
    assert "window.toggleExpertTeamWorkspacePanel=toggleExpertTeamWorkspacePanel" in UI_JS
    assert "_expertTeamWorkspacePanelHiddenForRun(runId)" in UI_JS
    sync_start = UI_JS.index("function _syncExpertTeamWorkspacePanelVisibility")
    sync_body = UI_JS[sync_start : UI_JS.index("function syncExpertTeamBottomDockState", sync_start)]
    assert "taiji-expert-team-panel-collapsed" in sync_body
    assert "shell.classList.toggle('taiji-expert-team-panel-collapsed',collapsed)" in sync_body
    assert "panel.hidden=!visible" in sync_body
    assert "expert-team-panel-collapse-toggle" in EXPERT_UI_JS
    assert "toggleExpertTeamWorkspaceFromControl(this)" in EXPERT_UI_JS
    assert 'aria-expanded="true"' in EXPERT_UI_JS
    assert 'aria-controls="expert-team-workspace-expanded"' in EXPERT_UI_JS


def test_question_popover_scrolls_into_workspace_panel_when_opened():
    assert "function _scrollExpertTeamQuestionPopoverIntoPanel" in UI_JS
    scroll_start = UI_JS.index("function _scrollExpertTeamQuestionPopoverIntoPanel")
    scroll_body = UI_JS[scroll_start : UI_JS.index("function _syncExpertTeamQuestionPopover", scroll_start)]
    assert ".expert-team-panel-expanded-body" in scroll_body
    assert "scroller.clientHeight<popover.offsetHeight*.7" in scroll_body
    assert ".status-card-expert-question.pending.is-current" in scroll_body
    assert "scroller.scrollTop+(focusRect.top-scrollerRect.top)-12" in scroll_body
    assert "scroller.scrollTo({top:nextTop,behavior:'auto'})" in scroll_body

    focus_start = UI_JS.index("function _focusExpertTeamQuestionPopover")
    focus_body = UI_JS[focus_start : UI_JS.index("function _scrollExpertTeamQuestionPopoverIntoPanel", focus_start)]
    assert "closest('.expert-team-workspace-panel')" in focus_body
    assert "target.scrollIntoView&&!insideWorkspace" in focus_body

    open_start = UI_JS.index("function openExpertTeamQuestionPopover")
    open_body = UI_JS[open_start : UI_JS.index("function closeExpertTeamQuestionPopover", open_start)]
    assert "_scrollExpertTeamQuestionPopoverIntoPanel(popover||trigger);" in open_body


def test_right_workspace_and_capsule_render_from_single_presentation():
    assert "function renderExpertTeamWorkspaceFromPresentation" in EXPERT_UI_JS
    assert "card.presentation" in EXPERT_UI_JS
    assert "card.workspace" in EXPERT_UI_JS
    assert "card.team" in EXPERT_UI_JS
    assert "card.workflow" in EXPERT_UI_JS
    assert "card.pendingInput" in EXPERT_UI_JS
    assert "card.stageResult" in EXPERT_UI_JS
    assert "presentation.primaryAction" in EXPERT_UI_JS
    assert "presentation.state" in EXPERT_UI_JS
    assert "专家团正在生成" in EXPERT_UI_JS
    assert "草稿未通过校验" in EXPERT_UI_JS
    assert "阶段成果待复核" in EXPERT_UI_JS
    assert "expert-team-member-list" in EXPERT_UI_JS
    assert "expert-team-member-avatar" in EXPERT_UI_JS
    assert "expert-team-collaboration-card" in EXPERT_UI_JS
    assert "expert-team-collaboration-current" in EXPERT_UI_JS
    assert "collaborationTaskForMember(member,tasks)" in EXPERT_UI_JS
    assert "专家团工作台" in EXPERT_UI_JS
    assert "expert-team-capsule" in EXPERT_UI_JS
    assert "Math.max(done,currentIndex+1)" not in EXPERT_UI_JS
    assert "progress.currentIndex" in EXPERT_UI_JS


def test_review_action_opens_workspace_review_panel_not_only_bottom_dock():
    review_start = ACTIONS_JS.index("if(action==='review_stage')")
    review_body = ACTIONS_JS[review_start : ACTIONS_JS.index("if(action==='revise_stage')", review_start)]
    assert "openExpertTeamReviewPanel(btn)" in review_body
    assert "focusExpertTeamBottomDock(btn)" not in review_body
    assert "function openExpertTeamReviewPanel" in UI_JS or "function openExpertTeamReviewPanel" in ACTIONS_JS
    assert "window.openExpertTeamReviewPanel=openExpertTeamReviewPanel" in UI_JS or "window.openExpertTeamReviewPanel=openExpertTeamReviewPanel" in ACTIONS_JS


def test_workspace_review_panel_exposes_stage_review_controls():
    assert "expert-team-stage-review" in EXPERT_UI_JS
    assert "data-expert-team-stage-feedback" in EXPERT_UI_JS
    assert "查看成果" in EXPERT_UI_JS
    assert "无修改，进入下一阶段" in EXPERT_UI_JS or "presentation.secondaryActions" in EXPERT_UI_JS
    assert "需要修改" in EXPERT_UI_JS or "presentation.secondaryActions" in EXPERT_UI_JS
    assert "submitExpertTeamStageRevision(this)" in EXPERT_UI_JS
    assert "approve_stage" in EXPERT_UI_JS
    assert "revise_stage" in EXPERT_UI_JS


def test_real_expert_team_start_syncs_session_messages_immediately():
    fn_start = COMMANDS_JS.index("async function sendExpertTeamAction")
    fn_body = COMMANDS_JS[fn_start : COMMANDS_JS.index("if(typeof window!=='undefined')window.sendExpertTeamAction", fn_start)]
    assert "data.session_messages" in fn_body
    assert "S.messages.push" in fn_body
    assert "renderMessages()" in fn_body
    assert "expert_team_run_id" in fn_body


def test_actions_map_only_presentation_actions_to_api_calls():
    assert "function handleExpertTeamPresentationAction" in ACTIONS_JS
    assert "function applyExpertTeamActionResponse" in ACTIONS_JS
    assert "_applyExpertTeamStreamResponse(data)" in ACTIONS_JS
    assert "renderExpertTeamStatusSurface(card)" in ACTIONS_JS
    assert "renderWriteflowStatusDock(card)" not in ACTIONS_JS
    assert "answer_required" in ACTIONS_JS
    assert "answer_optional" in ACTIONS_JS
    assert "submit_stage_input" in ACTIONS_JS
    assert "start_generation" in ACTIONS_JS
    assert "review_stage" in ACTIONS_JS
    assert "regenerate" in ACTIONS_JS
    assert "view_result" in ACTIONS_JS
    assert "/api/expert-teams/stage/approve" in ACTIONS_JS
    assert "/api/expert-teams/stage/input" in ACTIONS_JS
    assert "/api/expert-teams/resume" in ACTIONS_JS
    assert "/api/writeflow/run" not in ACTIONS_JS


def test_stage_input_confirmation_is_in_right_workspace_not_chat_or_bottom_dock():
    assert "awaiting_stage_input" in EXPERT_UI_JS
    assert "expert-team-stage-input-card" in EXPERT_UI_JS
    assert "data-expert-team-stage-input-text" in EXPERT_UI_JS
    assert "确认并继续生成" in EXPERT_UI_JS
    assert "稍后处理" in EXPERT_UI_JS
    assert "pendingInput" in EXPERT_UI_JS
    assert "stage_input" not in SESSIONS_JS
    joined = "\n".join([COMMANDS_JS, PANELS_JS, SESSIONS_JS, UI_JS])
    assert "expert-team-chat-confirmation-card" not in joined
    assert "status-card-expert-dock-summary" not in ACTIONS_JS


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
    assert "适用对象" in EXPERT_UI_JS


def test_expert_team_actions_never_call_legacy_writeflow_api():
    joined = "\n".join([SESSIONS_JS, ACTIONS_JS, PRESENTER_JS, EXPERT_UI_JS])
    assert "/api/writeflow/run" not in joined
    assert "/api/writeflow/status" not in joined
    assert "sendWriteflowAction(" not in ACTIONS_JS


def test_expert_team_hydration_never_renders_bottom_dock_for_expert_team():
    hydrate_start = SESSIONS_JS.index("async function _hydrateExpertTeamStatusCardForSession")
    hydrate_body = SESSIONS_JS[hydrate_start : SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession", hydrate_start)]
    assert "renderExpertTeamStatusSurface(card)" in hydrate_body
    assert "renderWriteflowStatusDock(card)" not in hydrate_body

    send_start = COMMANDS_JS.index("async function sendExpertTeamAction")
    send_body = COMMANDS_JS[send_start : COMMANDS_JS.index("if(typeof window!=='undefined')window.sendExpertTeamAction", send_start)]
    assert "renderExpertTeamStatusSurface(card)" in send_body
    assert "renderWriteflowStatusDock(card)" not in send_body
