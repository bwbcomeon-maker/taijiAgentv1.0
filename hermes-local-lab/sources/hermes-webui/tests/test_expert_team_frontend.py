from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_JS = (REPO_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
SESSIONS_JS = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_expert_team_start_uses_structured_runtime_not_visible_prompt_send():
    assert "async function sendExpertTeamAction" in COMMANDS_JS
    assert "api('/api/expert-teams/start'" in COMMANDS_JS
    assert "renderWriteflowStatusDock(card)" in COMMANDS_JS

    fn_start = COMMANDS_JS.index("async function sendExpertTeamAction")
    fn_body = COMMANDS_JS[fn_start : COMMANDS_JS.index("async function cmdPersonality", fn_start)]
    assert "api('/api/writeflow/compose'" not in fn_body
    assert "await send();" not in fn_body
    assert "$('msg').value=data.message" not in fn_body


def test_writeflow_summon_routes_through_expert_team_runtime():
    fn_start = PANELS_JS.index("async function summonWriteflowTeam")
    fn_body = PANELS_JS[fn_start : PANELS_JS.index("function _writeflowModeLabel", fn_start)]

    assert "window.sendExpertTeamAction({" in fn_body
    assert "sendWriteflowAction({" not in fn_body
    assert "team_id: team.id" in fn_body
    assert "new_session: true" in fn_body
    assert "请先填写本次需求。" in fn_body
    assert "请先填写本次写作需求。" not in fn_body
    assert "页面资源版本不一致，正在刷新。" in fn_body
    assert "hardRefreshWebUIClient()" in fn_body
    assert "const started=await window.sendExpertTeamAction" in fn_body
    assert "if(started)closeWriteflowTeamModal();" in fn_body
    assert fn_body.index("typeof window.sendExpertTeamAction!=='function'") < fn_body.index("const started=await window.sendExpertTeamAction")


def test_expert_team_start_returns_boolean_for_modal_lifecycle():
    fn_start = COMMANDS_JS.index("async function sendExpertTeamAction")
    fn_body = COMMANDS_JS[fn_start : COMMANDS_JS.index("async function cmdPersonality", fn_start)]

    assert "return false;" in fn_body
    assert "return true;" in fn_body
    assert "showToast('专家团已创建，请先完成需求确认。');" in fn_body
    assert "showToast('专家团启动失败：'+(e&&e.message||e));" in fn_body
    assert fn_body.index("try{") < fn_body.index("await newSession(wantsNewSession)")
    assert fn_body.index("return true;") < fn_body.index("}catch(e){")


def test_expert_team_center_loads_only_expert_team_catalog():
    fn_start = PANELS_JS.index("async function loadWriteflow")
    fn_body = PANELS_JS[fn_start : PANELS_JS.index("function _writeflowInputPayload", fn_start)]

    assert "api('/api/expert-teams/catalog')" in fn_body
    assert "_writeflowApplyServerTeams(expertCatalog && expertCatalog.teams)" in fn_body
    assert "_writeflowApplyServerTeams(data.teams)" not in fn_body
    assert "api(_writeflowStatusUrl())" not in fn_body


def test_deep_research_team_has_expert_team_status_phases():
    assert "'deep-research-team':['需求确认','资料调研','结构提纲','正文初稿','审稿交付']" in UI_JS


def test_expert_team_status_card_has_questions_members_tasks_and_process_hooks():
    assert "function _expertTeamStatusCardFromRun" in UI_JS
    assert "function _isExpertTeamStatusCard" in UI_JS
    assert "function _expertTeamDockSummary" in UI_JS
    assert "function _expertTeamWorkspacePanelHtml" in UI_JS
    assert "function renderExpertTeamWorkspacePanel" in UI_JS
    assert "function syncExpertTeamBottomDockState" in UI_JS
    assert "function clearExpertTeamWorkspacePanel" in UI_JS
    assert "async function answerExpertTeamQuestion" in UI_JS
    assert "/api/expert-teams/answer" in UI_JS
    assert "card.questions=visualQuestions" in UI_JS
    assert "const view=run.view||{}" in UI_JS
    assert "card.actions=view.actions||{}" in UI_JS
    assert "card.health=view.health||{}" in UI_JS
    assert "card.intake=view.intake||{}" in UI_JS
    assert "card.primaryConfirmation=view.primary_confirmation||{}" in UI_JS
    assert "card.reviewItems=Array.isArray(view.review_items)" in UI_JS
    assert "usedInRevision:!!(item&&item.used_in_revision" in UI_JS
    assert "view.phase_progress||run.phase_progress" in UI_JS
    assert "taiji-expert-team-active" in UI_JS
    assert "syncExpertTeamBottomDockState(card)" in UI_JS
    assert "clearExpertTeamWorkspacePanel()" in UI_JS
    assert "status-card-expert-bottom-body" in UI_JS
    assert "status-card-expert-questions" in UI_JS
    assert "status-card-expert-question" in UI_JS
    assert "expert-team-workspace-panel" in UI_JS
    assert "expert-team-panel-questions" in UI_JS
    assert "data-expert-team-question-id" in UI_JS
    assert "data-expert-team-run-id" in UI_JS
    assert "data-expert-team-answer" in UI_JS
    assert "expert-team-member-strip" in UI_JS
    assert "expert-team-process-panel" in UI_JS
    assert "任务进程" in UI_JS
    assert "status-card-expert-dock-summary" in UI_JS

    assert ".status-card-expert-questions" in STYLE_CSS
    assert ".expert-team-member-strip" in STYLE_CSS
    assert ".expert-team-process-panel" in STYLE_CSS
    assert ".expert-team-workspace-panel" in STYLE_CSS
    assert ".taiji-home-shell.taiji-expert-team-active" in STYLE_CSS
    assert ".status-card-expert-dock-summary" in STYLE_CSS


def test_pending_expert_team_questions_are_visible_and_answerable():
    assert "card.kind==='expert_team'" in UI_JS
    assert "function _expertTeamQuestionIsTerminal" in UI_JS
    assert "['answered','skipped'].includes" in UI_JS
    assert "data-expert-team-answer-input" in UI_JS
    assert "status-card-expert-question-input" in UI_JS
    assert "expert-team-confirmation-workspace" in UI_JS
    assert "expert-team-confirmation-header" in UI_JS
    assert "待你确认：${confirmations.length} 项" in UI_JS
    assert "status-card-expert-question-submit" in UI_JS
    assert "data-expert-team-empty-label=\"请先填写\"" in UI_JS
    assert "data-expert-team-ready-label=\"确认此项并继续\"" in UI_JS
    assert "aria-label=\"${esc(_expertTeamQuestionAriaLabel(question,context))}\"" in UI_JS
    assert "oninput=\"syncExpertTeamQuestionInputState(this)\"" in UI_JS
    assert "function syncExpertTeamQuestionInputState" in UI_JS
    assert "questionEl&&questionEl.dataset?questionEl.dataset.expertTeamRunId" in UI_JS
    assert "root.dataset.expertTeamRunId" in UI_JS
    assert "请先填写确认信息。" in UI_JS
    assert "请填写补充内容，或点击跳过并开始生成。" in UI_JS
    assert "skip_optional:skipOptional" in UI_JS
    assert "需求已确认，正在进入生成。" in UI_JS
    assert "已跳过" in UI_JS
    assert ".status-card-expert-question-input" in STYLE_CSS
    assert ".expert-team-confirmation-workspace" in STYLE_CSS
    assert ".status-card-expert-question.pending.is-current" in STYLE_CSS
    assert ".status-card-expert-question-submit" in STYLE_CSS
    assert ".status-card-expert-question-submit:disabled" in STYLE_CSS


def test_expert_team_uses_unified_confirmation_entrypoints():
    assert "card.pendingConfirmations" in UI_JS
    assert "view.pending_confirmations" in UI_JS
    assert "card.primaryConfirmation" in UI_JS
    assert "function _expertTeamPendingConfirmations" in UI_JS
    assert "function _expertTeamConfirmationSummary" in UI_JS
    assert "待你确认：${confirmations.length} 项" in UI_JS
    assert "expert-team-confirmation-stage-summary" in UI_JS
    assert "expert-team-confirmation-fallback" in UI_JS
    assert "聊天中有待确认事项，请查看最新专家团回复。" in UI_JS
    assert "function _expertTeamChatConfirmationCardHtml" in UI_JS
    assert "function syncExpertTeamChatConfirmationCard" in UI_JS
    assert "expert-team-chat-confirmation-card" in UI_JS
    assert "data-expert-team-chat-confirmation" in UI_JS
    assert "focusExpertTeamBottomDock(this)" in UI_JS
    assert "window.syncExpertTeamChatConfirmationCard=syncExpertTeamChatConfirmationCard" in UI_JS
    assert "add(_expertTeamChatConfirmationSignature())" in UI_JS

    assert ".expert-team-chat-confirmation-card" in STYLE_CSS
    assert ".expert-team-confirmation-stage-summary" in STYLE_CSS
    assert ".expert-team-confirmation-fallback" in STYLE_CSS


def test_expert_team_question_confirmation_uses_dock_popover_flow():
    assert "function _expertTeamQuestionWizardState" in UI_JS
    assert "function _expertTeamActiveQuestionSet" in UI_JS
    assert "confirmationGroup:question.confirmation_group||question.confirmationGroup||''" in UI_JS
    assert "sourceTaskId:question.source_task_id||question.sourceTaskId||''" in UI_JS
    assert "const activeQuestions=_expertTeamActiveQuestionSet(card)" in UI_JS
    assert "function _expertTeamQuestionPopoverHtml" in UI_JS
    assert "function openExpertTeamQuestionPopover" in UI_JS
    assert "function closeExpertTeamQuestionPopover" in UI_JS
    assert "function goExpertTeamQuestionStep" in UI_JS
    assert "function submitExpertTeamQuestionStep" in UI_JS
    assert "function editExpertTeamAnsweredQuestion" in UI_JS
    assert "expert-team-question-popover" in UI_JS
    assert "data-expert-team-question-popover" in UI_JS
    assert "需求确认" in UI_JS
    assert "必填问题" in UI_JS
    assert "可选补充" in UI_JS
    assert "跳过并开始生成" in UI_JS
    assert "保存补充并生成" in UI_JS
    assert "确认并下一题" in UI_JS
    assert "保存草稿" in UI_JS
    assert "已回答" in UI_JS
    assert "openExpertTeamQuestionPopover(this)" in UI_JS
    assert "focusExpertTeamBottomDock(this)" in UI_JS

    chat_start = UI_JS.index("function _expertTeamChatConfirmationCardHtml")
    chat_body = UI_JS[chat_start : UI_JS.index("function syncExpertTeamChatConfirmationCard", chat_start)]
    assert "hasQuestion" in chat_body
    assert "openExpertTeamQuestionPopover(this)" in chat_body
    assert "focusExpertTeamBottomDock(this)" in chat_body
    assert chat_body.index("openExpertTeamQuestionPopover(this)") < chat_body.index("focusExpertTeamBottomDock(this)")

    panel_start = UI_JS.index("function _expertTeamWorkspacePanelHtml")
    panel_body = UI_JS[panel_start : UI_JS.index("function _setExpertTeamWorkspaceActive", panel_start)]
    assert "打开需求确认" in panel_body
    assert "expert-team-question-summary" in panel_body
    assert "待人工补充事项" in panel_body
    assert "expert-team-review-items" in panel_body

    assert ".expert-team-question-popover" in STYLE_CSS
    assert ".expert-team-question-progress" in STYLE_CSS
    assert ".expert-team-question-groups" in STYLE_CSS
    assert ".expert-team-question-skip" in STYLE_CSS
    assert ".expert-team-question-review" in STYLE_CSS
    assert ".expert-team-review-items" in STYLE_CSS


def test_expert_team_question_inputs_survive_status_refresh_rerender():
    assert "function _captureExpertTeamQuestionInputState" in UI_JS
    assert "function _restoreExpertTeamQuestionInputState" in UI_JS
    assert "function _expertTeamWorkspaceRenderKey" in UI_JS
    assert "document.activeElement" in UI_JS
    assert "selectionStart" in UI_JS
    assert "selectionEnd" in UI_JS
    assert "focus({preventScroll:true})" in UI_JS
    assert "_syncExpertTeamQuestionInputs(root)" in UI_JS
    assert ".classList.contains('answered')" in UI_JS

    panel_start = UI_JS.index("function renderExpertTeamWorkspacePanel")
    panel_body = UI_JS[panel_start : UI_JS.index("function clearExpertTeamWorkspacePanel", panel_start)]
    assert "document.createElement('aside')" not in panel_body
    assert "panel.innerHTML=_expertTeamWorkspacePanelHtml(card);" not in panel_body
    assert "return syncExpertTeamBottomDockState(card)" in panel_body

    dock_start = UI_JS.index("function renderWriteflowStatusDock")
    dock_body = UI_JS[dock_start : UI_JS.index("function clearWriteflowStatusDock", dock_start)]
    assert "const isExpertTeam=_isExpertTeamStatusCard(card);" in dock_body
    assert "const dockInputState=isExpertTeam?_captureExpertTeamQuestionInputState(dock):null;" in dock_body
    assert "_restoreExpertTeamQuestionInputState(dock,dockInputState);" in dock_body
    assert "delete dock.dataset.expertTeamRenderKey" in UI_JS


def test_expert_team_workspace_visibility_is_chat_scoped_and_user_hideable():
    assert "function _expertTeamActivePanelName" in UI_JS
    assert "function _syncExpertTeamWorkspacePanelVisibility" in UI_JS
    assert "function focusExpertTeamBottomDock" in UI_JS
    assert "function _setExpertTeamBottomDockExpanded" in UI_JS
    assert "function hideExpertTeamWorkspacePanel" in UI_JS
    assert "function showExpertTeamWorkspacePanel" in UI_JS
    assert "data-expert-team-hide-run-id" in UI_JS
    assert "hideExpertTeamWorkspacePanel(this)" in UI_JS
    assert "window._syncExpertTeamWorkspacePanelVisibility=_syncExpertTeamWorkspacePanelVisibility" in UI_JS
    assert "window.hideExpertTeamWorkspacePanel=hideExpertTeamWorkspacePanel" in UI_JS
    assert "window.showExpertTeamWorkspacePanel=showExpertTeamWorkspacePanel" in UI_JS
    assert "window.focusExpertTeamBottomDock=focusExpertTeamBottomDock" in UI_JS

    focus_start = UI_JS.index("function focusExpertTeamBottomDock")
    focus_body = UI_JS[focus_start : UI_JS.index("if(typeof window!=='undefined'){", focus_start)]
    assert "_setExpertTeamBottomDockExpanded(true,trigger)" in focus_body
    assert "focusTarget.scrollIntoView({block:'nearest',inline:'nearest'});" in focus_body
    assert ".expert-team-question-popover:not([hidden]) textarea:not(:disabled)" in focus_body
    assert ".expert-team-stage-approve:not(:disabled)" in focus_body
    assert ".expert-team-stage-revision-toggle:not(:disabled)" in focus_body
    assert ".expert-team-stage-feedback:not([hidden]) textarea" in focus_body
    assert focus_body.index(".expert-team-question-popover:not([hidden]) textarea:not(:disabled)") < focus_body.index(".expert-team-stage-approve:not(:disabled)")
    assert focus_body.index(".expert-team-stage-approve:not(:disabled)") < focus_body.index(".expert-team-stage-revision-toggle:not(:disabled)")
    assert focus_body.index(".expert-team-stage-revision-toggle:not(:disabled)") < focus_body.index(".expert-team-stage-feedback:not([hidden]) textarea")

    expand_start = UI_JS.index("function _setExpertTeamBottomDockExpanded")
    expand_body = UI_JS[expand_start : UI_JS.index("function hideExpertTeamWorkspacePanel", expand_start)]
    assert "_persistExpertTeamBottomDockExpanded(card,shouldExpand)" in expand_body
    assert "function _persistExpertTeamBottomDockExpanded" in UI_JS

    assert ".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock" in STYLE_CSS
    assert ".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-writeflow.is-expanded" in STYLE_CSS
    assert ".taiji-home-shell.taiji-expert-team-active .expert-team-workspace-panel{display:none!important;}" in STYLE_CSS
    assert ".expert-team-panel-hide" in STYLE_CSS


def test_expert_team_blank_area_click_collapses_without_intercepting_controls():
    assert "function handleExpertTeamPanelBlankCollapse" in UI_JS
    assert "function _syncExpertTeamBlankCollapseListener" in UI_JS
    assert "window.handleExpertTeamPanelBlankCollapse=handleExpertTeamPanelBlankCollapse" in UI_JS

    panel_start = UI_JS.index("function _expertTeamWorkspacePanelHtml")
    panel_body = UI_JS[panel_start : UI_JS.index("function _setExpertTeamWorkspaceActive", panel_start)]
    assert 'onclick="handleExpertTeamPanelBlankCollapse(event)"' in panel_body

    handler_start = UI_JS.index("const EXPERT_TEAM_PANEL_BLANK_COLLAPSE_PROTECTED_SELECTOR")
    handler_body = UI_JS[handler_start : UI_JS.index("function hideExpertTeamWorkspacePanel", handler_start)]
    assert "card.classList.contains('is-expanded')" in handler_body
    assert "_setExpertTeamBottomDockExpanded(false,target)" in handler_body
    assert "document.getElementById('writeflowStatusDock')" in handler_body
    assert "_syncExpertTeamWorkspacePanelVisibility()" in handler_body
    for protected_selector in (
        "button",
        "input",
        "textarea",
        "select",
        "a",
        "[role=\"button\"]",
        ".expert-team-panel-section",
        ".expert-team-panel-priority-card",
        ".expert-team-panel-phase",
        ".expert-team-panel-execution-row",
    ):
        assert protected_selector in handler_body

    render_start = UI_JS.index("function renderWriteflowStatusDock")
    render_body = UI_JS[render_start : UI_JS.index("function clearWriteflowStatusDock", render_start)]
    assert "dock.onclick=isExpertTeam?handleExpertTeamPanelBlankCollapse:null;" in render_body
    assert "_syncExpertTeamBlankCollapseListener(isExpertTeam);" in render_body

    clear_start = UI_JS.index("function clearWriteflowStatusDock")
    clear_body = UI_JS[clear_start : UI_JS.index("if(typeof window!=='undefined')", clear_start)]
    assert "dock.onclick=null;" in clear_body
    assert "_syncExpertTeamBlankCollapseListener(false);" in clear_body


def test_expert_team_workspace_visibility_syncs_on_panel_switches():
    switch_start = PANELS_JS.index("async function switchPanel")
    switch_body = PANELS_JS[switch_start : PANELS_JS.index("// ── Cron panel ──", switch_start)]
    assert "_syncExpertTeamWorkspacePanelVisibility()" in switch_body
    assert switch_body.find("mainEl.classList.toggle('showing-' + p, nextPanel === p);") < switch_body.find("_syncExpertTeamWorkspacePanelVisibility()")

    force_start = SESSIONS_JS.index("function _forceChatSessionPanel")
    force_body = SESSIONS_JS[force_start : SESSIONS_JS.index("async function openChatSession", force_start)]
    assert "_syncExpertTeamWorkspacePanelVisibility()" in force_body


def test_expert_team_workspace_drawer_prioritizes_full_title_actions_and_artifacts():
    assert "function _expertTeamPrimaryArtifact" in UI_JS
    assert "function _expertTeamAnsweredQuestionsSummary" in UI_JS
    assert "function _expertTeamExecutionRows" in UI_JS
    assert "function handleExpertTeamDockAction" in UI_JS
    assert "onclick=\"handleExpertTeamDockAction(this);event.stopPropagation()\"" in UI_JS
    assert "data-expert-team-primary-artifact-path" in UI_JS
    assert "data-writeflow-artifact-path" in UI_JS
    assert "expert-team-panel-topbar" in UI_JS
    assert "expert-team-panel-title" in UI_JS
    assert "title=\"${esc(taskTitle)}\"" in UI_JS
    assert "expert-team-panel-collapse-toggle" in UI_JS
    assert "expert-team-panel-priority-grid" in UI_JS
    assert "expert-team-panel-execution" in UI_JS
    assert "expert-team-panel-member-avatars" in UI_JS
    assert "expert-team-panel-answered-summary" in UI_JS
    assert "expert-team-panel-artifacts-section is-priority" in UI_JS
    assert "card.type==='writeflow'||card.kind==='writeflow'||_isExpertTeamStatusCard(card)" in UI_JS

    panel_start = UI_JS.index("function _expertTeamWorkspacePanelHtml")
    panel_body = UI_JS[panel_start : UI_JS.index("function _setExpertTeamWorkspaceActive", panel_start)]
    rows_start = UI_JS.index("function _expertTeamExecutionRows")
    rows_body = UI_JS[rows_start : UI_JS.index("function _expertTeamQuestionHtml", rows_start)]
    panel_return = panel_body[panel_body.index("return `<div") :]
    assert "const taskTitle=card.promptSummary||card.subtitle||team.title||'专家团任务';" in panel_body
    assert "const expertTeamMemberCount=members.length;" in panel_body
    assert "const phaseProgress=_expertTeamPhaseProgress(card,{phaseList,phaseIdx,readyArtifacts,pending,stateClass});" in panel_body
    assert "const artifactSectionHtml=" in panel_body
    assert "let confirmationSectionHtml='';" in panel_body
    assert "const executionRows=_expertTeamExecutionRows(card,{phaseList,pending,readyArtifacts,done,total,stateClass});" in panel_body
    assert "${phaseProgress.done}/${phaseProgress.total}" in panel_body
    assert "${done}/${total||tasks.length||0}" not in panel_body
    assert "class=\"expert-team-panel-artifact-open\"" in panel_body
    assert "${readyArtifacts.length||deliveredArtifacts.length?artifactSectionHtml:''}" in panel_body
    assert "${confirmationSectionHtml}" in panel_body
    assert "成员简况" not in panel_body
    assert panel_return.find("${confirmationSectionHtml}") < panel_return.find("expert-team-panel-phases")
    assert panel_return.find("${confirmationSectionHtml}") < panel_return.find("expert-team-panel-priority-grid")
    assert panel_return.find("${confirmationSectionHtml}") < panel_return.find("expert-team-panel-execution")
    assert panel_return.find("expert-team-panel-execution") < panel_return.find("${readyArtifacts.length||deliveredArtifacts.length?artifactSectionHtml:''}")
    assert "phaseList.map((label,idx)=>" in rows_body
    assert "members.length" in rows_body
    assert ".slice(0,4)" in rows_body

    assert ".expert-team-panel-title" in STYLE_CSS
    assert "-webkit-line-clamp:3" in STYLE_CSS
    assert "overflow-wrap:anywhere" in STYLE_CSS
    assert ".expert-team-panel-collapse-toggle" in STYLE_CSS
    assert ".expert-team-panel-priority-grid" in STYLE_CSS
    assert ".expert-team-panel-execution" in STYLE_CSS
    assert ".expert-team-panel-expanded-body{min-height:0;display:flex;flex:1 1 auto;flex-direction:column;gap:7px;overflow:hidden auto;" in STYLE_CSS
    assert ".expert-team-panel-execution{flex:0 0 auto;display:flex;flex-direction:column;overflow:visible;}" in STYLE_CSS
    assert ".expert-team-panel-member-avatars" in STYLE_CSS
    assert ".expert-team-panel-artifacts-section.is-priority" in STYLE_CSS
    assert ".expert-team-panel-answered-summary" in STYLE_CSS
    assert ".expert-team-panel-artifact-open:not(:disabled)" in STYLE_CSS
    assert ".status-card-writeflow.is-collapsed .status-card-expert-bottom-body" in STYLE_CSS
    assert ".expert-team-panel-head strong{color:var(--text);font-size:16px;line-height:1.25;font-weight:820;letter-spacing:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" not in STYLE_CSS


def test_expert_team_title_uses_available_header_space_for_long_prompts():
    overview_start = STYLE_CSS.index(".expert-team-panel-overview")
    overview_block = STYLE_CSS[overview_start : STYLE_CSS.index("}", overview_start)]
    title_start = STYLE_CSS.index(".expert-team-panel-title")
    title_block = STYLE_CSS[title_start : STYLE_CSS.index("}", title_start)]

    assert "grid-template-columns:minmax(0,1fr) minmax(92px,128px)" in overview_block
    assert "gap:10px" in overview_block
    assert "font-size:13px" in title_block
    assert "line-height:1.22" in title_block
    assert "-webkit-line-clamp:3" in title_block
    assert "overflow-wrap:anywhere" in title_block
    assert "word-break:break-word" in title_block


def test_expert_team_panel_title_prefers_full_prompt_summary_over_short_project_title():
    card_start = UI_JS.index("function _writeflowStatusCardFromRun")
    card_body = UI_JS[card_start : UI_JS.index("function _expertTeamStatusCardFromRun", card_start)]
    assert "promptSummary:run.prompt_summary||''" in card_body

    panel_start = UI_JS.index("function _expertTeamWorkspacePanelHtml")
    panel_body = UI_JS[panel_start : UI_JS.index("function _setExpertTeamWorkspaceActive", panel_start)]
    assert "const taskTitle=card.promptSummary||card.subtitle||team.title||'专家团任务';" in panel_body


def test_expert_team_pending_question_draft_survives_silent_status_refresh_miss():
    assert "function shouldPreserveExpertTeamDraftDock" in UI_JS
    assert "window.shouldPreserveExpertTeamDraftDock=shouldPreserveExpertTeamDraftDock" in UI_JS

    helper_start = UI_JS.index("function shouldPreserveExpertTeamDraftDock")
    helper_body = UI_JS[helper_start : UI_JS.index("function renderWriteflowStatusDock", helper_start)]
    assert "dock.dataset.writeflowSourceSessionId" in helper_body
    assert ".status-card-expert-question.pending [data-expert-team-answer-input]" in helper_body
    assert "document.activeElement" in helper_body
    assert "String(input.value||'').trim()" in helper_body

    hydrate_start = SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession")
    hydrate_body = SESSIONS_JS[hydrate_start : SESSIONS_JS.index("function _removeWriteflowStatusCardsFromMessages", hydrate_start)]
    assert "if(options.silent&&typeof shouldPreserveExpertTeamDraftDock==='function'&&shouldPreserveExpertTeamDraftDock(sid))return false;" in hydrate_body


def test_expert_team_workspace_uses_bottom_dock_without_top_panel_squeeze():
    dock_start = STYLE_CSS.index(".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock")
    dock_block = STYLE_CSS[dock_start : STYLE_CSS.index(".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-writeflow", dock_start)]
    expanded_start = STYLE_CSS.index(".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-writeflow.is-expanded")
    expanded_block = STYLE_CSS[expanded_start : STYLE_CSS.index(".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-expert-dock-summary", expanded_start)]

    assert "bottom:calc(100% + 12px)!important;" in dock_block
    assert "width:100%!important;" in dock_block
    assert "max-width:100%!important;" in dock_block
    assert "display:block!important;" in dock_block
    assert "display:none!important;" not in dock_block
    assert "max-height:min(72vh,620px)!important;" in expanded_block
    assert "overflow:hidden auto!important;" in expanded_block

    inner_start = STYLE_CSS.index(".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-expert-bottom-body .expert-team-panel-inner")
    inner_block = STYLE_CSS[inner_start : STYLE_CSS.index("}", inner_start)]
    assert "max-height:none!important;" in inner_block
    assert "overflow:visible!important;" in inner_block
    assert "overflow:hidden auto!important;" not in inner_block

    legacy_visible = STYLE_CSS.find(".taiji-home-shell.taiji-expert-team-panel-visible .expert-team-workspace-panel")
    if legacy_visible != -1:
        legacy_block = STYLE_CSS[legacy_visible : STYLE_CSS.find("}", legacy_visible)]
        assert "display:flex!important" not in legacy_block
    assert "top:calc(var(--taiji-expert-panel-top) + var(--taiji-expert-panel-h)" not in STYLE_CSS
    assert "#writeflowStatusDock{\n    position:absolute!important;" in STYLE_CSS


def test_expert_team_artifact_actions_open_products_before_focusing_panel():
    assert "async function openWriteflowArtifact" in UI_JS
    assert "async function openExpertTeamArtifact" in UI_JS
    assert "await openArtifactPath(path)" in UI_JS
    assert "ensureWorkspacePreviewVisible" in UI_JS
    assert "expert-team-panel-artifact-focus" in UI_JS
    assert "无法打开产物" in UI_JS
    assert "window.openExpertTeamArtifact=openExpertTeamArtifact" in UI_JS
    assert "window.handleExpertTeamDockAction=handleExpertTeamDockAction" in UI_JS

    panel_start = UI_JS.index("function _expertTeamWorkspacePanelHtml")
    panel_body = UI_JS[panel_start : UI_JS.index("function _setExpertTeamWorkspaceActive", panel_start)]
    assert "onclick=\"openExpertTeamArtifact(this);event.stopPropagation()\"" in panel_body
    assert "onclick=\"openWriteflowArtifact(this);event.stopPropagation()\"" not in panel_body

    expert_start = UI_JS.index("async function openExpertTeamArtifact")
    expert_body = UI_JS[expert_start : UI_JS.index("function downloadWriteflowArtifact", expert_start)]
    assert "await openWriteflowArtifact(btn)" in expert_body
    assert "ensureWorkspacePreviewVisible()" in expert_body
    assert "_focusExpertTeamArtifactEntry(btn,path)" in expert_body
    assert "btn.classList.add('is-opening')" in expert_body
    assert "btn.classList.add('is-opened')" in expert_body

    handler_start = UI_JS.index("async function handleExpertTeamDockAction")
    handler_body = UI_JS[handler_start : UI_JS.index("if(typeof window!=='undefined'){", handler_start)]
    assert "btn.dataset.expertTeamPrimaryArtifactPath" in handler_body
    assert "await openExpertTeamArtifact(btn)" in handler_body
    assert "return focusExpertTeamBottomDock(btn)" in handler_body


def test_expert_team_chat_delivery_is_not_presented_as_openable_file_artifact():
    ready_start = UI_JS.index("function _expertTeamArtifactIsOpenable")
    ready_body = UI_JS[ready_start : UI_JS.index("function _expertTeamPhaseProgress", ready_start)]
    assert "String(item.path||'').trim()" in ready_body
    assert "item.openable!==false" in ready_body
    assert "artifacts.filter(item=>_expertTeamArtifactIsOpenable(item))" in ready_body
    assert "_expertTeamArtifactDeliveredToChat(item)" in UI_JS

    summary_start = UI_JS.index("function _expertTeamDockSummary")
    summary_body = UI_JS[summary_start : UI_JS.index("function _expertTeamStatusBadgeLabel", summary_start)]
    assert "deliveredArtifacts.length" in summary_body
    assert "结果已写入当前对话" in summary_body
    assert "action:'查看结果'" in summary_body

    card_start = UI_JS.index("function _writeflowStatusCardFromRun")
    card_body = UI_JS[card_start : UI_JS.index("function _expertTeamStatusCardFromRun", card_start)]
    assert "openable:item.openable===true" in card_body
    assert "const readyArtifacts=visualArtifacts.filter(item=>item&&item.openable===true);" in card_body

    panel_start = UI_JS.index("function _expertTeamWorkspacePanelHtml")
    panel_body = UI_JS[panel_start : UI_JS.index("function _setExpertTeamWorkspaceActive", panel_start)]
    assert "const deliveredArtifacts=" in panel_body
    assert "deliveredArtifacts.length" in panel_body
    assert "已写入当前对话" in panel_body
    assert "data-expert-team-chat-delivery" in panel_body
    assert "openExpertTeamChatDelivery(this)" in panel_body
    assert "readyArtifacts.length?'查看产物':'查看对话结果'" in panel_body


def test_expert_team_answer_response_attaches_real_stream_runtime():
    assert "function _applyExpertTeamStreamResponse" in UI_JS
    assert "data&&data.stream_id" in UI_JS
    assert "S.activeStreamId=data.stream_id" in UI_JS
    assert "S.session.active_stream_id=data.stream_id" in UI_JS
    assert "S.session.pending_user_message=data.pending_user_message" in UI_JS
    assert "S.session.pending_started_at=data.pending_started_at" in UI_JS
    assert "markInflight(sid,data.stream_id)" in UI_JS
    assert "saveInflightState(sid,{streamId:data.stream_id" in UI_JS
    assert "attachLiveStream(sid,data.stream_id" in UI_JS
    assert "_applyExpertTeamStreamResponse(data);" in UI_JS


def test_expert_team_workspace_shows_resume_action_for_stale_running_runs():
    assert "function resumeExpertTeamRun" in UI_JS
    assert "function cancelExpertTeamRun" in UI_JS
    assert "/api/expert-teams/resume" in UI_JS
    assert "/api/expert-teams/cancel" in UI_JS
    assert "card.needsResume||card.needs_resume" in UI_JS
    assert "expert-team-panel-resume" in UI_JS
    assert "expert-team-panel-cancel" in UI_JS
    assert "expert-team-panel-retry" in UI_JS
    assert "继续生成" in UI_JS
    assert "停止生成" in UI_JS
    assert "重新尝试" in UI_JS
    assert "data-expert-team-resume-run-id" in UI_JS
    assert "data-expert-team-cancel-run-id" in UI_JS
    assert "card.actions&&card.actions.can_cancel" in UI_JS
    assert "card.actions&&card.actions.can_retry" in UI_JS
    assert "window.resumeExpertTeamRun=resumeExpertTeamRun" in UI_JS
    assert "window.cancelExpertTeamRun=cancelExpertTeamRun" in UI_JS


def test_expert_team_workspace_exposes_stage_review_actions():
    stage_review_start = UI_JS.index("const stageReviewSectionHtml=")
    stage_review_body = UI_JS[stage_review_start : UI_JS.index("const primaryArtifact=", stage_review_start)]

    assert "function approveExpertTeamStage" in UI_JS
    assert "function reviseExpertTeamStage" in UI_JS
    assert "function toggleExpertTeamStageRevision" in UI_JS
    assert "function locateExpertTeamStageOutput" in UI_JS
    assert "function appendExpertTeamReviewItemToRevision" in UI_JS
    assert "function markExpertTeamReviewItemRead" in UI_JS
    assert "/api/expert-teams/stage/approve" in UI_JS
    assert "/api/expert-teams/stage/revise" in UI_JS
    assert "card.stageReview=view.stage_review||{}" in UI_JS
    assert "card.stageOutputs=Array.isArray(run.stage_outputs)?run.stage_outputs:[]" in UI_JS
    assert "expert-team-stage-review" in stage_review_body
    assert "expert-team-stage-output" in stage_review_body
    assert "查看初稿" in stage_review_body
    assert "data-expert-team-stage-feedback" in stage_review_body
    assert "data-expert-team-stage-output-locator" in stage_review_body
    assert "data-expert-team-stage-revision-toggle" in stage_review_body
    assert 'class="expert-team-stage-feedback" hidden aria-hidden="true"' in stage_review_body
    assert "请确认阶段成果" in stage_review_body
    assert stage_review_body.index("查看初稿") < stage_review_body.index("无修改，进入下一阶段")
    assert "无修改，进入下一阶段" in stage_review_body
    assert "需要修改" in stage_review_body
    assert "提交修改意见" in stage_review_body
    assert "提出修改意见" not in stage_review_body
    assert "actions.can_approve_stage" in UI_JS
    assert "actions.can_request_revision" in UI_JS
    assert "window.approveExpertTeamStage=approveExpertTeamStage" in UI_JS
    assert "window.reviseExpertTeamStage=reviseExpertTeamStage" in UI_JS
    assert "window.toggleExpertTeamStageRevision=toggleExpertTeamStageRevision" in UI_JS
    assert "window.locateExpertTeamStageOutput=locateExpertTeamStageOutput" in UI_JS
    assert "window.appendExpertTeamReviewItemToRevision=appendExpertTeamReviewItemToRevision" in UI_JS
    assert "window.markExpertTeamReviewItemRead=markExpertTeamReviewItemRead" in UI_JS

    assert ".expert-team-stage-review" in STYLE_CSS
    assert ".expert-team-stage-output" in STYLE_CSS
    assert ".expert-team-stage-actions" in STYLE_CSS
    assert ".expert-team-stage-revision-toggle" in STYLE_CSS
    assert ".expert-team-stage-feedback[hidden]" in STYLE_CSS


def test_expert_team_review_items_are_actionable_without_blocking_stage_review():
    panel_start = UI_JS.index("function _expertTeamWorkspacePanelHtml")
    panel_body = UI_JS[panel_start : UI_JS.index("function _setExpertTeamWorkspaceActive", panel_start)]

    assert "待人工补充事项" in panel_body
    assert "加入修改意见" in panel_body
    assert "标记已阅" in panel_body
    assert "data-expert-team-review-item-title" in panel_body
    assert "data-expert-team-review-item-read" in panel_body
    assert "usedInRevision" in panel_body
    assert "appendExpertTeamReviewItemToRevision(this)" in panel_body
    assert "markExpertTeamReviewItemRead(this)" in panel_body

    append_start = UI_JS.index("function appendExpertTeamReviewItemToRevision")
    append_body = UI_JS[append_start : UI_JS.index("function markExpertTeamReviewItemRead", append_start)]
    assert "toggleExpertTeamStageRevision(toggle)" in append_body
    assert "data-expert-team-stage-feedback" in append_body
    assert "修改意见已加入" in append_body

    mark_start = UI_JS.index("function markExpertTeamReviewItemRead")
    mark_body = UI_JS[mark_start : UI_JS.index("async function approveExpertTeamStage", mark_start)]
    assert "is-read" in mark_body
    assert "已阅" in mark_body

    assert ".expert-team-review-item-actions" in STYLE_CSS
    assert ".expert-team-review-item.is-read" in STYLE_CSS


def test_expert_team_center_exposes_six_office_material_templates():
    assert "工作汇报" in PANELS_JS
    assert "会议纪要" in PANELS_JS
    assert "通知通报" in PANELS_JS
    assert "方案说明" in PANELS_JS
    assert "总结计划" in PANELS_JS
    assert "材料润色" in PANELS_JS
    assert 'placeholder="写清材料类型、主题、对象、素材、语气和边界；召唤后会进入新的聊天任务。"' in PANELS_JS


def test_expert_team_session_refresh_does_not_require_loaded_message_array():
    hydrate_start = SESSIONS_JS.index("async function _hydrateExpertTeamStatusCardForSession")
    hydrate_body = SESSIONS_JS[hydrate_start : SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession", hydrate_start)]

    assert "!Array.isArray(S.messages)" not in hydrate_body
    assert "_isWriteflowHydrationForActiveSession(sid)" in hydrate_body


def test_expert_team_hydrates_before_writeflow_fallback():
    assert "async function _hydrateExpertTeamStatusCardForSession" in SESSIONS_JS
    assert "/api/expert-teams/run?session_id=" in SESSIONS_JS
    hydrate = SESSIONS_JS[SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession") :]
    assert "await _hydrateExpertTeamStatusCardForSession(sid)" in hydrate
    assert hydrate.find("await _hydrateExpertTeamStatusCardForSession(sid)") < hydrate.find("/api/writeflow/run?session_id=")
