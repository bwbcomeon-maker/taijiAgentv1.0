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


def test_taiji_recent_sessions_collect_crud_actions_in_more_menu():
    assert "data-taiji-session-more" in HOME_JS
    assert "function showSessionActionMenu" in HOME_JS
    assert "function renameSessionFromRecent" in HOME_JS
    assert "data-taiji-session-rename" in HOME_JS
    assert "data-taiji-session-move" in HOME_JS
    assert "data-taiji-session-delete" in HOME_JS
    assert "'/api/session/rename'" in HOME_JS
    assert "'/api/session/move'" in HOME_JS
    assert "project_id:projectId||null" in HOME_JS
    assert "moveSessionToProject(session,project.project_id" in HOME_JS
    assert "新建分组并加入" in HOME_JS
    assert ".taiji-session-more" in STYLE_CSS
    assert ".taiji-session-action-menu" in STYLE_CSS
    assert ".taiji-session-action-menu-item" in STYLE_CSS
    assert ".taiji-project-menu" in STYLE_CSS


def test_taiji_recent_sessions_render_only_expert_or_qa_kind_labels():
    assert "function taijiSessionKind(session)" in HOME_JS
    assert "return '专家团'" in HOME_JS
    assert "return '问答'" in HOME_JS
    assert "const kind=taijiSessionKind(session);" in HOME_JS
    assert 'data-kind="${kindCode}"' in HOME_JS
    assert 'class="taiji-session-kind"' in HOME_JS
    assert ".taiji-session-kind" in STYLE_CSS
    assert '[data-kind="expert"]' in STYLE_CSS
    assert '[data-kind="qa"]' in STYLE_CSS


def test_taiji_recent_sessions_classify_expert_team_start_titles():
    assert r"/^召唤[^：:\n]{0,64}专家团[：:]/.test(rawTitle)" in HOME_JS
    kind_start = HOME_JS.index("function taijiSessionKind(session)")
    kind_body = HOME_JS[kind_start : HOME_JS.index("function taijiSessionFullTitle", kind_start)]
    assert "rawLooksExpertTeam" in kind_body
    assert "rawLooksWriteflow||rawLooksExpertTeam||displayLooksWriteflow" in kind_body


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
