"""Regression tests for gateway-backed WebUI approval prompts."""

from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_gateway_run_approval_event_becomes_webui_pending_payload():
    from api.gateway_chat import _gateway_run_approval_payload

    payload = _gateway_run_approval_payload(
        "webui-session-1",
        {
            "event": "approval.request",
            "run_id": "run_123",
            "command": "rm -rf .git",
            "description": "recursive delete",
            "pattern_key": "recursive delete",
            "pattern_keys": ["recursive delete"],
        },
    )

    assert payload["command"] == "rm -rf .git"
    assert payload["description"] == "recursive delete"
    assert payload["_session_id"] == "webui-session-1"
    assert payload["_gateway_run_id"] == "run_123"
    assert payload["approval_id"]


def test_gateway_run_approval_payload_falls_back_to_stream_run_id():
    from api.gateway_chat import _gateway_run_approval_payload

    payload = _gateway_run_approval_payload(
        "webui-session-1",
        {
            "event": "approval.request",
            "approval_id": "approval_123",
            "command": "cat <<EOF > report.xlsx",
            "description": "script execution via heredoc",
        },
        run_id="run_from_stream",
    )

    assert payload["_gateway_run_id"] == "run_from_stream"


def test_webui_approval_response_resolves_gateway_run(monkeypatch):
    from api import routes

    sid = "webui-session-bridge"
    approval_id = "approval-bridge-1"
    routes.submit_pending(
        sid,
        {
            "approval_id": approval_id,
            "_gateway_run_id": "run_bridge",
            "command": "rm -rf .git",
            "description": "recursive delete",
            "pattern_key": "recursive delete",
            "pattern_keys": ["recursive delete"],
        },
    )

    calls = []

    def fake_resolve(pending, choice):
        calls.append((dict(pending), choice))
        return True

    monkeypatch.setattr("api.gateway_chat.resolve_gateway_run_approval", fake_resolve)

    assert routes._resolve_approval_legacy(sid, approval_id, "once") is True
    assert calls == [
        (
            {
                "approval_id": approval_id,
                "_gateway_run_id": "run_bridge",
                "command": "rm -rf .git",
                "description": "recursive delete",
                "pattern_key": "recursive delete",
                "pattern_keys": ["recursive delete"],
            },
            "once",
        )
    ]
    assert routes._handle_approval_pending.__name__ == "_handle_approval_pending"


def test_gateway_resolve_failure_restores_pending_card(monkeypatch):
    from api import routes

    sid = "webui-session-bridge-fail"
    approval_id = "approval-bridge-fail"
    routes.submit_pending(
        sid,
        {
            "approval_id": approval_id,
            "_gateway_run_id": "run_bridge_fail",
            "command": "rm -rf .git",
            "description": "recursive delete",
            "pattern_key": "recursive delete",
            "pattern_keys": ["recursive delete"],
        },
    )

    monkeypatch.setattr("api.gateway_chat.resolve_gateway_run_approval", lambda _pending, _choice: False)

    assert routes._resolve_approval_legacy(sid, approval_id, "once") is False
    with routes._lock:
        queue = routes._pending.get(sid)
        assert isinstance(queue, list)
        assert queue[0]["approval_id"] == approval_id


def test_gateway_run_clear_only_removes_matching_run():
    from api import routes

    sid = "webui-session-bridge-clear"
    routes.submit_pending(
        sid,
        {
            "approval_id": "approval-run-a",
            "_gateway_run_id": "run_a",
            "command": "rm -rf .git",
            "description": "recursive delete",
            "pattern_key": "recursive delete",
            "pattern_keys": ["recursive delete"],
        },
    )
    routes.submit_pending(
        sid,
        {
            "approval_id": "approval-run-b",
            "_gateway_run_id": "run_b",
            "command": "pkill -9 demo",
            "description": "force kill processes",
            "pattern_key": "force kill processes",
            "pattern_keys": ["force kill processes"],
        },
    )

    assert routes.clear_gateway_run_pending_approvals(sid, "run_a") == 1
    with routes._lock:
        queue = routes._pending.get(sid)
        assert isinstance(queue, list)
        assert [entry["approval_id"] for entry in queue] == ["approval-run-b"]
