from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_taiji_recent_sessions_expose_confirmed_delete_action():
    assert "showSessionActionMenu" in HOME_JS
    assert "window.taijiHomeDeleteSession" in HOME_JS
    assert "data-taiji-session-more" in HOME_JS
    assert "data-taiji-session-delete" in HOME_JS
    assert "window.taijiHomeDeleteSession(sid,event)" in HOME_JS
    assert ".stopPropagation()" in HOME_JS
    assert "globalFn('deleteSession')" in HOME_JS
    assert "await deleteSessionFn(sid)" in HOME_JS
    assert "renderSessionList" in HOME_JS
    assert "refreshSessions()" in HOME_JS


def test_taiji_recent_session_delete_control_has_stable_hit_area():
    assert ".taiji-session-more{" in STYLE_CSS
    assert ".taiji-session-row:hover .taiji-session-more" in STYLE_CSS
    assert ".taiji-session-more:focus-visible" in STYLE_CSS
    assert ".taiji-session-action-menu{" in STYLE_CSS
    assert ".taiji-session-action-menu-item.is-danger" in STYLE_CSS
