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
    assert "function clearExpertTeamWorkspacePanel" in UI_JS
    assert "async function answerExpertTeamQuestion" in UI_JS
    assert "/api/expert-teams/answer" in UI_JS
    assert "card.questions=visualQuestions" in UI_JS
    assert "taiji-expert-team-active" in UI_JS
    assert "renderExpertTeamWorkspacePanel(card)" in UI_JS
    assert "clearExpertTeamWorkspacePanel()" in UI_JS
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
    assert "const renderKey=_expertTeamWorkspaceRenderKey(card);" in panel_body
    assert "panel.dataset.expertTeamRenderKey===renderKey" in panel_body
    assert "const inputState=_captureExpertTeamQuestionInputState(panel);" in panel_body
    assert "panel.innerHTML=_expertTeamWorkspacePanelHtml(card);" in panel_body
    assert "_restoreExpertTeamQuestionInputState(panel,inputState);" in panel_body

    dock_start = UI_JS.index("function renderWriteflowStatusDock")
    dock_body = UI_JS[dock_start : UI_JS.index("function clearWriteflowStatusDock", dock_start)]
    assert "const isExpertTeam=_isExpertTeamStatusCard(card);" in dock_body
    assert "const dockInputState=isExpertTeam?_captureExpertTeamQuestionInputState(dock):null;" in dock_body
    assert "_restoreExpertTeamQuestionInputState(dock,dockInputState);" in dock_body
    assert "delete dock.dataset.expertTeamRenderKey" in UI_JS


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
    assert "/api/expert-teams/resume" in UI_JS
    assert "card.needsResume||card.needs_resume" in UI_JS
    assert "expert-team-panel-resume" in UI_JS
    assert "继续生成" in UI_JS
    assert "data-expert-team-resume-run-id" in UI_JS
    assert "window.resumeExpertTeamRun=resumeExpertTeamRun" in UI_JS


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
