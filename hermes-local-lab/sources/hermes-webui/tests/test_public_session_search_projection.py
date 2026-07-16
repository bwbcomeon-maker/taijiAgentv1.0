from types import SimpleNamespace
from urllib.parse import urlparse


FORBIDDEN_KEYS = {
    "args", "result", "path", "token", "pending", "pending_attachments",
    "attachments", "dynamic",
}


def _assert_strict(value):
    if isinstance(value, dict):
        assert not (FORBIDDEN_KEYS & set(value)), value
        function = value.get("function")
        if isinstance(function, dict):
            assert "arguments" not in function
        for item in value.values():
            _assert_strict(item)
    elif isinstance(value, list):
        for item in value:
            _assert_strict(item)


def _session_item(session_id, *, workspace, title="Customer session"):
    return {
        "session_id": session_id,
        "title": title,
        "workspace": workspace,
        "model": "test-model",
        "message_count": 2,
        "args": {"command": "cat /opt/taiji-agent/runtime/config"},
        "result": {"path": "/opt/taiji-agent/runtime/result"},
        "token": "secret-token",
        "pending": {"dynamic": "private"},
        "pending_attachments": [{"path": "/opt/taiji-agent/runtime/upload"}],
        "dynamic": {"function": {"arguments": "raw"}},
    }


def _invoke(monkeypatch, query, sessions, messages_by_sid=None):
    import api.routes as routes

    captured = {}
    monkeypatch.setattr(routes, "all_sessions", lambda: sessions)
    monkeypatch.setattr(
        routes,
        "get_session",
        lambda sid: SimpleNamespace(messages=(messages_by_sid or {}).get(sid, [])),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, **_kwargs: captured.setdefault("payload", payload),
    )
    routes._handle_sessions_search(object(), urlparse(f"/api/sessions/search?{query}"))
    return captured["payload"]


def test_empty_search_projects_every_session_item(monkeypatch):
    payload = _invoke(
        monkeypatch,
        "q=",
        [
            _session_item("internal", workspace="/opt/taiji-agent/runtime"),
            _session_item("customer", workspace="/Users/customer/taiji-project"),
        ],
    )

    assert "workspace" not in payload["sessions"][0]
    assert payload["sessions"][1]["workspace"] == "/Users/customer/taiji-project"
    _assert_strict(payload)


def test_title_search_keeps_match_type_after_strict_projection(monkeypatch):
    payload = _invoke(
        monkeypatch,
        "q=customer",
        [_session_item("customer", workspace="/Users/customer/project", title="Customer title")],
    )

    item = payload["sessions"][0]
    assert item["match_type"] == "title"
    assert item["title"] == "Customer title"
    assert item["workspace"] == "/Users/customer/project"
    _assert_strict(payload)


def test_content_search_keeps_safe_preview_after_strict_projection(monkeypatch):
    payload = _invoke(
        monkeypatch,
        "q=needle&content=1&depth=5",
        [_session_item("content", workspace="/opt/taiji-agent/runtime", title="Other")],
        {"content": [{"role": "assistant", "content": "Visible needle context"}]},
    )

    item = payload["sessions"][0]
    assert item["match_type"] == "content"
    assert item["match_preview"] == "Visible needle context"
    assert "workspace" not in item
    _assert_strict(payload)
