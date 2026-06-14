from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
HOME_JS = (ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_taiji_project_filters_are_rendered_from_projects():
    assert 'id="taijiProjectFilters"' in INDEX_HTML
    assert "function renderProjectFilters()" in HOME_JS
    assert "taijiProjectFilters" in HOME_JS
    assert 'data-taiji-session-filter="project:' in HOME_JS
    assert "session.project_id!==projectId" in HOME_JS
    assert "state.sessionFilter=`project:${res.project.project_id}`" in HOME_JS
    assert ".taiji-project-filters" in STYLE_CSS


def test_taiji_recent_sessions_expose_project_move_action():
    assert "data-taiji-session-move" in HOME_JS
    assert "window.taijiHomeMoveSession" in HOME_JS
    assert "taijiHomeMoveSession(moveBtn.dataset.sessionId,event)" in HOME_JS
    assert "'/api/session/move'" in HOME_JS
    assert "project_id:projectId||null" in HOME_JS
    assert "moveSessionToProject(session,project.project_id" in HOME_JS
    assert "新建分组并加入" in HOME_JS
    assert ".taiji-session-move" in STYLE_CSS
    assert ".taiji-project-menu" in STYLE_CSS


def test_taiji_new_chat_inherits_active_project_filter():
    assert "function activeProjectId()" in HOME_JS
    assert "const projectId=activeProjectId();" in HOME_JS
    assert "await newSessionFn(true,{project_id:projectId})" in HOME_JS


def test_new_session_accepts_explicit_project_id_option():
    assert "Object.prototype.hasOwnProperty.call(options,'project_id')" in SESSIONS_JS
    assert "reqBody.project_id=options.project_id" in SESSIONS_JS
    assert "else if(_activeProject&&_activeProject!==NO_PROJECT_FILTER)" in SESSIONS_JS


def test_taiji_view_all_toggles_recent_session_filter():
    assert 'onclick="taijiHomeToggleAllSessions()"' in INDEX_HTML
    assert "showAllSessions:false" in HOME_JS
    assert "window.taijiHomeToggleAllSessions" in HOME_JS
    assert "if(!state.showAllSessions)" in HOME_JS
    assert "taijiViewAllLabel" in HOME_JS
    assert "查看最近会话" in HOME_JS
