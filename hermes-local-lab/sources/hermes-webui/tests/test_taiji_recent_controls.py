from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
HOME_JS = (ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_taiji_project_filters_are_rendered_from_projects():
    assert 'id="taijiProjectFilters"' in INDEX_HTML
    assert "function renderProjectFilters()" in HOME_JS
    assert "taijiProjectFilters" in HOME_JS
    assert 'data-taiji-session-filter="project:' in HOME_JS
    assert "session.project_id!==projectId" in HOME_JS
    assert "state.sessionFilter=`project:${res.project.project_id}`" in HOME_JS
    assert ".taiji-project-filters" in STYLE_CSS


def test_taiji_view_all_toggles_recent_session_filter():
    assert 'onclick="taijiHomeToggleAllSessions()"' in INDEX_HTML
    assert "showAllSessions:false" in HOME_JS
    assert "window.taijiHomeToggleAllSessions" in HOME_JS
    assert "if(!state.showAllSessions)" in HOME_JS
    assert "taijiViewAllLabel" in HOME_JS
    assert "查看最近会话" in HOME_JS
