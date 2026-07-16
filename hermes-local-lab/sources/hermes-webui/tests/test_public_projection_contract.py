"""Canaries for the strict public session/message/event boundary."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.parse import urlparse


FORBIDDEN_OPERATIONAL_KEYS = {
    "args",
    "result",
    "snippet",
    "path",
    "token",
    "command",
}


class _JsonHandler:
    """Minimal real JSON response sink for exercising handle_get."""

    def __init__(self):
        self.status = None
        self.headers = {}
        self.sent_headers = []
        self.body = bytearray()
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def _assert_strict_public_tree(value):
    if isinstance(value, dict):
        assert not (FORBIDDEN_OPERATIONAL_KEYS & set(value)), value
        for key in ("attachments", "pending_attachments"):
            if key in value:
                assert isinstance(value[key], list)
                for attachment in value[key]:
                    assert set(attachment) <= {"name", "filename", "mime", "size", "is_image", "ref"}
        function = value.get("function")
        if isinstance(function, dict):
            assert "arguments" not in function, value
        for item in value.values():
            _assert_strict_public_tree(item)
    elif isinstance(value, list):
        for item in value:
            _assert_strict_public_tree(item)


def _hostile_session_payload(*, workspace="/Users/customer/project"):
    return {
        "session_id": "public-canary",
        "title": "Customer task",
        "workspace": workspace,
        "model": "test-model",
        "model_provider": "test-provider",
        "message_count": 2,
        "updated_at": 42,
        "active_stream_id": "stream-1",
        "pending_user_message": "continue",
        "pending_started_at": 40,
        "pending_attachments": [{"path": "/private/runtime/upload.png", "token": "upload-secret"}],
        "messages": [
            {
                "role": "assistant",
                "content": "Visible answer",
                "timestamp": 41,
                "attachments": [{"path": "/private/runtime/report.md"}],
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "cat /private/runtime/config"}),
                    },
                    "args": {"token": "nested-secret"},
                    "result": {"path": "/private/runtime/result"},
                    "snippet": "raw-output",
                }],
            },
            {
                "role": "tool",
                "name": "terminal",
                "tool_call_id": "call-1",
                "content": "raw tool result",
                "args": {"command": "cat /private/runtime/config"},
                "result": "raw-output",
                "snippet": "raw-output",
                "status": "completed",
                "summary": "Command completed",
            },
        ],
        "tool_calls": [{
            "name": "terminal",
            "status": "completed",
            "summary": "Command completed",
            "args": {"command": "cat /private/runtime/config"},
            "result": "raw-output",
            "snippet": "raw-output",
            "done": True,
            "assistant_msg_idx": 0,
        }],
        "generic": {"args": {"token": "secret"}, "path": "/private/runtime"},
    }


def test_get_session_projection_keeps_ui_runtime_fields_but_drops_operational_canaries():
    from api.brand_privacy import public_session_projection

    cleaned = public_session_projection(_hostile_session_payload())

    assert cleaned["session_id"] == "public-canary"
    assert cleaned["workspace"] == "/Users/customer/project"
    assert cleaned["active_stream_id"] == "stream-1"
    assert cleaned["pending_user_message"] == "continue"
    assert cleaned["pending_started_at"] == 40
    assert cleaned["pending_attachments"] == []
    assert cleaned["messages"][0]["content"] == "Visible answer"
    assert cleaned["messages"][0]["attachments"] == []
    assert cleaned["messages"][0]["tool_calls"][0]["name"] == "terminal"
    assert cleaned["messages"][1] == {
        "role": "tool",
        "event_type": "tool.completed",
        "name": "terminal",
        "status": "completed",
        "summary": "Command completed",
        "tool_call_id": "call-1",
        "tid": "call-1",
    }
    _assert_strict_public_tree(cleaned)


def test_session_projection_hides_only_internal_workspace_not_customer_workspace():
    from api.brand_privacy import public_session_projection

    internal = public_session_projection(
        _hostile_session_payload(workspace="/opt/taiji-agent/runtime")
    )
    customer = public_session_projection(
        _hostile_session_payload(workspace="/Users/customer/taiji-project")
    )

    assert "workspace" not in internal
    assert customer["workspace"] == "/Users/customer/taiji-project"


def test_session_status_projection_drops_runtime_paths_but_keeps_business_fields():
    from api.brand_privacy import public_session_status_projection

    projected = public_session_status_projection({
        "session_id": "status-1",
        "profile": "research",
        "model": "customer-model",
        "workspace": "/Users/customer/taiji-project",
        "hermes_home": "/Users/customer/.hermes/profiles/research",
        "runtime_path": "/opt/taiji-agent/runtime",
        "config": {"token": "canary-secret"},
        "agent_running": False,
        "total_tokens": 42,
    })

    assert projected == {
        "session_id": "status-1",
        "profile": "research",
        "model": "customer-model",
        "workspace": "/Users/customer/taiji-project",
        "agent_running": False,
        "total_tokens": 42,
    }


def test_sync_chat_and_import_response_projection_canaries():
    from api.brand_privacy import public_response_projection

    for surface in ("chat_sync", "session_cli_import", "session_json_import"):
        cleaned = public_response_projection(
            {
                "answer": "Visible answer",
                "status": "done",
                "imported": surface != "chat_sync",
                "session": _hostile_session_payload(),
                "result": {
                    "completed": True,
                    "args": {"command": "cat /private/runtime"},
                    "token": "provider-token",
                },
            },
            surface=surface,
        )
        assert cleaned["answer"] == "Visible answer"
        assert cleaned["session"]["model"] == "test-model"
        assert "result" not in cleaned
        _assert_strict_public_tree(cleaned)


def test_ephemeral_done_event_uses_same_strict_session_projection():
    from api.brand_privacy import public_event_projection

    cleaned = public_event_projection(
        {
            "session": _hostile_session_payload(),
            "usage": {"input_tokens": 1, "output_tokens": 2, "token": "secret"},
            "ephemeral": True,
            "answer": "Visible answer",
            "result": {"path": "/private/runtime"},
        },
        event_name="done",
    )

    assert cleaned["ephemeral"] is True
    assert cleaned["answer"] == "Visible answer"
    assert cleaned["session"]["session_id"] == "public-canary"
    assert cleaned["usage"] == {"input_tokens": 1, "output_tokens": 2}
    _assert_strict_public_tree(cleaned)


def test_non_tool_journal_replay_projects_event_payload(monkeypatch):
    import api.routes as routes

    handler = SimpleNamespace(wfile=io.BytesIO())
    monkeypatch.setattr(
        routes,
        "find_run_summary",
        lambda stream_id: {"session_id": "public-canary", "run_id": stream_id, "terminal": True},
    )
    monkeypatch.setattr(
        routes,
        "read_run_events",
        lambda *_args, **_kwargs: {
            "events": [{
                "event": "token",
                "payload": {
                    "text": "visible",
                    "args": {"command": "cat /private/runtime"},
                    "result": "raw-output",
                    "path": "/private/runtime",
                    "token": "secret",
                },
                "event_id": "run-1:1",
            }]
        },
    )

    assert routes._replay_run_journal(handler, "run-1", 0) is True
    body = handler.wfile.getvalue().decode("utf-8")
    assert '"text": "visible"' in body
    for canary in ("args", "command", "result", "/private/runtime", "secret"):
        assert canary not in body


def test_approval_get_and_sse_projection_never_returns_pending_internal_dict(monkeypatch):
    from api import routes

    sid = "approval-public-canary"
    captured = {}
    with routes._lock:
        routes._pending[sid] = [{
            "approval_id": "approval-1",
            "command": "cat /private/runtime/config.yaml --token secret",
            "description": "Read runtime config",
            "pattern_key": "read config",
            "pattern_keys": ["read config"],
            "_gateway_run_id": "internal-run-id",
            "args": {"path": "/private/runtime/config.yaml"},
            "result": "private-result",
        }]
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, **_kwargs: captured.setdefault("payload", payload),
    )
    try:
        routes._handle_approval_pending(
            object(),
            SimpleNamespace(query=f"session_id={sid}"),
        )
        pending = captured["payload"]["pending"]
        assert pending["approval_id"] == "approval-1"
        assert pending["description"] == "Read runtime config"
        assert "summary" in pending
        _assert_strict_public_tree(captured["payload"])

        q = routes._approval_sse_subscribe(sid)
        try:
            routes._approval_sse_notify(sid, routes._pending[sid][0], 1)
            pushed = q.get(timeout=1)
            assert pushed["pending"] == pending
            _assert_strict_public_tree(pushed)
        finally:
            routes._approval_sse_unsubscribe(sid, q)
    finally:
        with routes._lock:
            routes._pending.pop(sid, None)


def test_active_profile_get_never_returns_internal_runtime_path(monkeypatch):
    from api import profiles, routes

    internal_home = "/Users/canary/.local/share/taiji-agent/runtime-home"
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: internal_home)
    monkeypatch.setattr(profiles, "taiji_single_runtime_mode", lambda: True)

    handler = _JsonHandler()
    routes.handle_get(handler, urlparse("/api/profile/active"))

    payload = handler.json()
    assert handler.status == 200
    assert payload == {"name": "default", "single_runtime": True}
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "path" not in payload
    assert internal_home not in serialized
    assert "runtime-home" not in serialized


def test_profiles_get_projects_only_fields_consumed_by_profile_ui(monkeypatch):
    from api import profiles, routes

    internal_home = "/Users/canary/.local/share/taiji-agent/runtime-home/profiles/research"
    provider_token = "sk-profile-canary-secret"
    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [{
            "name": "research",
            "path": internal_home,
            "home": internal_home,
            "config": {"token": provider_token},
            "runtime": {"cwd": internal_home},
            "unknown_internal": provider_token,
            "is_default": False,
            "is_active": True,
            "gateway_running": True,
            "model": "provider/model",
            "provider": "provider",
            "has_env": True,
            "skill_count": 2,
            "enabled_skills": 2,
            "total_skills": 3,
        }],
    )
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "research")
    monkeypatch.setattr(profiles, "taiji_single_runtime_mode", lambda: False)

    handler = _JsonHandler()
    routes.handle_get(handler, urlparse("/api/profiles"))

    payload = handler.json()
    assert handler.status == 200
    assert payload == {
        "profiles": [{
            "name": "research",
            "is_default": False,
            "is_active": True,
            "gateway_running": True,
            "model": "provider/model",
            "provider": "provider",
            "has_env": True,
            "skill_count": 2,
            "enabled_skills": 2,
            "total_skills": 3,
        }],
        "active": "research",
        "single_runtime": False,
    }
    profile = payload["profiles"][0]
    assert not ({"path", "home", "config", "runtime", "unknown_internal"} & set(profile))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert internal_home not in serialized
    assert provider_token not in serialized
