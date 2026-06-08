import json
from pathlib import Path
from unittest.mock import MagicMock

from tools import mcp_tool


def test_mcp_tool_call_log_redacts_arguments_and_writes_jsonl(tmp_path, monkeypatch):
    log_path = tmp_path / "mcp-tool-calls.jsonl"
    monkeypatch.setattr(mcp_tool, "_mcp_tool_call_log_path", lambda: log_path)

    mcp_tool._append_mcp_tool_call_log(
        server_name="filesystem",
        tool_name="write_file",
        arguments={"path": "/tmp/demo.txt", "API_KEY": "secret-token"},
        ok=True,
        duration_ms=12,
    )

    row = json.loads(log_path.read_text(encoding="utf-8"))
    assert row["server"] == "filesystem"
    assert row["tool"] == "write_file"
    assert row["ok"] is True
    assert row["arguments"]["API_KEY"] == "[REDACTED]"
    assert "secret-token" not in log_path.read_text(encoding="utf-8")


def test_dangerous_mcp_tool_uses_approval_callback_and_denies(monkeypatch):
    monkeypatch.setattr(
        mcp_tool,
        "_get_mcp_tool_approval_callback",
        lambda: (lambda command, description, allow_permanent=False: "deny"),
    )

    message = mcp_tool._mcp_confirmation_error(
        "filesystem",
        "write_file",
        {"path": "/tmp/demo.txt", "content": "replace"},
    )

    assert message is not None
    payload = json.loads(message)
    assert "requires user confirmation" in payload["error"]
    assert "write_file" in payload["error"]


def test_dangerous_mcp_tool_allows_after_once_approval(monkeypatch):
    monkeypatch.setattr(
        mcp_tool,
        "_get_mcp_tool_approval_callback",
        lambda: (lambda command, description, allow_permanent=False: "once"),
    )

    assert mcp_tool._mcp_confirmation_error(
        "playwright",
        "browser_type",
        {"element": "Email", "text": "user@example.com"},
    ) is None


def test_mcp_call_handler_logs_success_and_failure(tmp_path, monkeypatch):
    log_path = tmp_path / "mcp-tool-calls.jsonl"
    monkeypatch.setattr(mcp_tool, "_mcp_tool_call_log_path", lambda: log_path)
    monkeypatch.setattr(mcp_tool, "_mcp_confirmation_error", lambda *_args, **_kwargs: None)

    server = MagicMock()
    server.session = object()
    server._rpc_lock = MagicMock()
    monkeypatch.setitem(mcp_tool._servers, "filesystem", server)
    monkeypatch.setattr(
        mcp_tool,
        "_run_on_mcp_loop",
        lambda _call, timeout: json.dumps({"result": "ok"}),
    )

    handler = mcp_tool._make_tool_handler("filesystem", "read_file", 10)
    assert json.loads(handler({"path": "/tmp/demo.txt"}))["result"] == "ok"

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["server"] == "filesystem"
    assert rows[-1]["tool"] == "read_file"
    assert rows[-1]["ok"] is True
