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
    assert "async function answerExpertTeamQuestion" in UI_JS
    assert "/api/expert-teams/answer" in UI_JS
    assert "card.questions=visualQuestions" in UI_JS
    assert "status-card-expert-questions" in UI_JS
    assert "status-card-expert-question" in UI_JS
    assert "data-expert-team-question-id" in UI_JS
    assert "data-expert-team-answer" in UI_JS
    assert "expert-team-member-strip" in UI_JS
    assert "expert-team-process-panel" in UI_JS
    assert "任务进程" in UI_JS

    assert ".status-card-expert-questions" in STYLE_CSS
    assert ".expert-team-member-strip" in STYLE_CSS
    assert ".expert-team-process-panel" in STYLE_CSS


def test_expert_team_hydrates_before_writeflow_fallback():
    assert "async function _hydrateExpertTeamStatusCardForSession" in SESSIONS_JS
    assert "/api/expert-teams/run?session_id=" in SESSIONS_JS
    hydrate = SESSIONS_JS[SESSIONS_JS.index("async function _hydrateWriteflowStatusCardForSession") :]
    assert "await _hydrateExpertTeamStatusCardForSession(sid)" in hydrate
    assert hydrate.find("await _hydrateExpertTeamStatusCardForSession(sid)") < hydrate.find("/api/writeflow/run?session_id=")
