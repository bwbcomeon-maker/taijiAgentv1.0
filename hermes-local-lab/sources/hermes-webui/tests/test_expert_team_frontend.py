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

    assert "sendExpertTeamAction({" in fn_body
    assert "sendWriteflowAction({" not in fn_body
    assert "team_id: team.id" in fn_body
    assert "new_session: true" in fn_body
    assert "请先填写本次需求。" in fn_body
    assert "请先填写本次写作需求。" not in fn_body


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
    assert "question.status||'')!=='answered'" in UI_JS
    assert "data-expert-team-answer-input" in UI_JS
    assert "status-card-expert-question-input" in UI_JS
    assert "questionEl&&questionEl.dataset?questionEl.dataset.expertTeamRunId" in UI_JS
    assert "root.dataset.expertTeamRunId" in UI_JS
    assert "请先填写确认信息。" in UI_JS
    assert ".status-card-expert-question-input" in STYLE_CSS


def test_expert_team_question_inputs_survive_status_refresh_rerender():
    assert "function _captureExpertTeamQuestionInputState" in UI_JS
    assert "function _restoreExpertTeamQuestionInputState" in UI_JS
    assert "function _expertTeamWorkspaceRenderKey" in UI_JS
    assert "document.activeElement" in UI_JS
    assert "selectionStart" in UI_JS
    assert "selectionEnd" in UI_JS
    assert "focus({preventScroll:true})" in UI_JS
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
    assert ".status-card-expert-question.pending textarea" in focus_body

    expand_start = UI_JS.index("function _setExpertTeamBottomDockExpanded")
    expand_body = UI_JS[expand_start : UI_JS.index("function hideExpertTeamWorkspacePanel", expand_start)]
    assert "_persistExpertTeamBottomDockExpanded(card,shouldExpand)" in expand_body
    assert "function _persistExpertTeamBottomDockExpanded" in UI_JS

    assert ".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock" in STYLE_CSS
    assert ".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-writeflow.is-expanded" in STYLE_CSS
    assert ".taiji-home-shell.taiji-expert-team-active .expert-team-workspace-panel{display:none!important;}" in STYLE_CSS
    assert ".expert-team-panel-hide" in STYLE_CSS


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
    assert "const taskTitle=card.subtitle||team.title||'专家团任务';" in panel_body
    assert "const expertTeamMemberCount=members.length;" in panel_body
    assert "const phaseProgress=_expertTeamPhaseProgress(card,{phaseList,phaseIdx,readyArtifacts,pending,stateClass});" in panel_body
    assert "const artifactSectionHtml=" in panel_body
    assert "const questionSectionHtml=" in panel_body
    assert "const executionRows=_expertTeamExecutionRows(card,{phaseList,pending,readyArtifacts,done,total,stateClass});" in panel_body
    assert "${phaseProgress.done}/${phaseProgress.total}" in panel_body
    assert "${done}/${total||tasks.length||0}" not in panel_body
    assert "class=\"expert-team-panel-artifact-open\"" in panel_body
    assert "${readyArtifacts.length?artifactSectionHtml:''}" in panel_body
    assert "${pending.length?questionSectionHtml:''}" in panel_body
    assert "成员简况" not in panel_body
    assert panel_return.find("${pending.length?questionSectionHtml:''}") < panel_return.find("expert-team-panel-execution")
    assert panel_return.find("expert-team-panel-execution") < panel_return.find("${readyArtifacts.length?artifactSectionHtml:''}")
    assert "phaseList.map((label,idx)=>" in rows_body
    assert "members.length" in rows_body
    assert ".slice(0,4)" in rows_body

    assert ".expert-team-panel-title" in STYLE_CSS
    assert "-webkit-line-clamp:2" in STYLE_CSS
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
