import json
import os
import threading
import time
from pathlib import Path


def test_generic_agent_without_taiji_runtime_keeps_upstream_capabilities(monkeypatch):
    monkeypatch.delenv("TAIJI_SECURITY_MODE", raising=False)
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)
    monkeypatch.delenv("TAIJI_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("TAIJI_DESKTOP_ONLY", raising=False)

    from tools.taiji_security_mode import is_terminal_allowed, security_mode

    assert security_mode() == "full"
    assert is_terminal_allowed() is True


def test_explicit_invalid_security_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "unexpected")
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)

    from tools.taiji_security_mode import is_terminal_allowed, security_mode

    assert security_mode() == "restricted"
    assert is_terminal_allowed() is False


def test_taiji_runtime_without_explicit_mode_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("TAIJI_SECURITY_MODE", raising=False)
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(tmp_path / "runtime-home"))

    from tools.taiji_security_mode import is_terminal_allowed, security_mode

    assert security_mode() == "restricted"
    assert is_terminal_allowed() is False


def test_restricted_mode_blocks_terminal(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)

    from tools.terminal_tool import terminal_tool

    result = json.loads(terminal_tool("echo should-not-run"))

    assert result["status"] == "capability_blocked"
    assert result["capability"] == "terminal"
    assert result["approval_applicable"] is False
    assert result["exit_code"] == -1
    assert "TAIJI_ALLOW_TERMINAL=1" in result["error"]


def test_restricted_mode_blocks_execute_code(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.delenv("TAIJI_ALLOW_EXECUTE_CODE", raising=False)

    from tools.code_execution_tool import execute_code

    result = json.loads(execute_code("print('should not run')"))

    assert result["status"] == "capability_blocked"
    assert result["capability"] == "execute_code"
    assert result["approval_applicable"] is False
    assert "TAIJI_ALLOW_EXECUTE_CODE=1" in result["error"]


def _run_with_gateway_capability(monkeypatch, sid, target):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("HERMES_SESSION_KEY", sid)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "webui")
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)
    monkeypatch.delenv("TAIJI_ALLOW_EXECUTE_CODE", raising=False)

    events = []
    result = {}

    from tools.approval import register_gateway_notify, unregister_gateway_notify

    register_gateway_notify(sid, lambda data: events.append(data))

    thread = threading.Thread(target=lambda: result.setdefault("value", target()))
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not events:
        time.sleep(0.01)
    assert events, "restricted capability should request gateway approval"
    return events, result, thread, lambda: unregister_gateway_notify(sid)


def test_terminal_gateway_capability_once_allows_current_call(monkeypatch):
    sid = "capability-once-terminal"

    from tools.approval import resolve_gateway_approval
    from tools.terminal_tool import terminal_tool

    events, result, thread, cleanup = _run_with_gateway_capability(
        monkeypatch,
        sid,
        lambda: json.loads(terminal_tool("printf taiji-capability-ok")),
    )
    try:
        pending = events[0]
        assert pending["approval_type"] == "capability_enable"
        assert pending["capability"] == "terminal"
        assert pending["allow_var"] == "TAIJI_ALLOW_TERMINAL"

        assert resolve_gateway_approval(sid, "once") == 1
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert result["value"]["exit_code"] == 0
        assert result["value"]["output"] == "taiji-capability-ok"
        assert "TAIJI_ALLOW_TERMINAL" not in os.environ
    finally:
        cleanup()


def test_capability_session_approval_is_remembered(monkeypatch):
    sid = "capability-session-terminal"

    from tools.approval import (
        clear_session,
        request_capability_approval,
        resolve_gateway_approval,
        unregister_gateway_notify,
    )
    from gateway.session_context import clear_session_vars, set_session_vars

    events = []
    from tools.approval import register_gateway_notify

    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("HERMES_SESSION_KEY", sid)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "webui")
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)
    clear_session(sid)
    register_gateway_notify(sid, lambda data: events.append(data))
    result = {}

    def _request_in_gateway_context():
        tokens = set_session_vars(platform="webui", session_key=sid)
        try:
            result.setdefault(
                "first",
                request_capability_approval("terminal", "TAIJI_ALLOW_TERMINAL"),
            )
        finally:
            clear_session_vars(tokens)

    thread = threading.Thread(
        target=_request_in_gateway_context
    )
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not events:
        time.sleep(0.01)
    assert events
    try:
        assert resolve_gateway_approval(sid, "session") == 1
        thread.join(timeout=10)
        assert result["first"]["approved"] is True
        assert result["first"]["scope"] == "session"
        tokens = set_session_vars(platform="webui", session_key=sid)
        try:
            second = request_capability_approval(
                "terminal",
                "TAIJI_ALLOW_TERMINAL",
            )
        finally:
            clear_session_vars(tokens)
        assert second["approved"] is True
        assert second["scope"] == "session"
        assert len(events) == 1
    finally:
        unregister_gateway_notify(sid)
        clear_session(sid)


def test_capability_always_persists_only_current_allow_var(monkeypatch, tmp_path):
    sid = "capability-always-terminal"
    runtime_home = tmp_path / "runtime-home"

    from tools.approval import request_capability_approval, resolve_gateway_approval
    from tools.approval import register_gateway_notify, unregister_gateway_notify

    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("HERMES_SESSION_KEY", sid)
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "webui")
    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)
    monkeypatch.delenv("TAIJI_ALLOW_EXECUTE_CODE", raising=False)

    events = []
    result = {}
    register_gateway_notify(sid, lambda data: events.append(data))
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "value",
            request_capability_approval("terminal", "TAIJI_ALLOW_TERMINAL"),
        )
    )
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not events:
        time.sleep(0.01)
    assert events
    try:
        assert resolve_gateway_approval(sid, "always") == 1
        thread.join(timeout=10)
        assert result["value"]["approved"] is True
        assert result["value"]["scope"] == "always"
        assert result["value"]["persisted"] is True
        env_text = (runtime_home / ".env").read_text(encoding="utf-8")
        assert "TAIJI_ALLOW_TERMINAL=1" in env_text
        assert "TAIJI_ALLOW_EXECUTE_CODE=1" not in env_text
        if os.name != "nt":
            assert (runtime_home / ".env").stat().st_mode & 0o777 == 0o600
        assert os.environ["TAIJI_ALLOW_TERMINAL"] == "1"
    finally:
        unregister_gateway_notify(sid)


def test_capability_persistence_uses_canonical_env_writer(
    monkeypatch,
    tmp_path,
):
    from agent import provider_credentials
    from tools.taiji_security_mode import enable_capability_env

    runtime_home = tmp_path / "runtime-home"
    calls = []

    def _record(updates, *, config_path=None, **_kwargs):
        calls.append((dict(updates), config_path))
        return {key: True for key in updates}

    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setattr(
        provider_credentials,
        "mutate_env_unique",
        _record,
    )

    result = enable_capability_env("TAIJI_ALLOW_TERMINAL")

    assert result["persisted"] is True
    assert calls == [
        (
            {
                "TAIJI_SECURITY_MODE": "restricted",
                "TAIJI_ALLOW_TERMINAL": "1",
            },
            runtime_home / "config.yaml",
        )
    ]


def test_security_status_reports_local_controlled_profile(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.setenv("TAIJI_ALLOW_TERMINAL", "1")
    monkeypatch.setenv("TAIJI_ALLOW_EXECUTE_CODE", "1")
    monkeypatch.setenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", "1")
    monkeypatch.setenv("TAIJI_ALLOW_DELEGATE_TASK", "1")

    from tools.taiji_security_mode import build_security_status

    status = build_security_status()

    assert status["profile"] == "local_controlled"
    assert status["mode"] == "restricted"
    assert status["capabilities"]["terminal"]["allowed"] is True
    assert status["capabilities"]["terminal"]["enabled"] is True
    assert status["capabilities"]["terminal"]["approval_required"] is False
    assert status["capabilities"]["execute_code"]["allowed"] is True
    assert status["capabilities"]["execute_code"]["enabled"] is True
    assert status["capabilities"]["execute_code"]["approval_required"] is False
    assert status["capabilities"]["document_read"]["allowed"] is True
    assert status["capabilities"]["document_read"]["enabled"] is True


def test_security_status_contract_reports_blocked_capability_reason(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)

    from tools.taiji_security_mode import build_security_status

    status = build_security_status()
    terminal = status["capabilities"]["terminal"]

    assert status["profile"] == "strict"
    assert terminal["allowed"] is False
    assert terminal["enabled"] is False
    assert terminal["approval_required"] is True
    assert terminal["restart_required"] is False
    assert "TAIJI_ALLOW_TERMINAL" in terminal["reason"]


def test_restricted_mode_blocks_cron_scripts(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.delenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", raising=False)

    from tools.cronjob_tools import cronjob

    result = json.loads(
        cronjob(
            action="create",
            prompt="collect status",
            schedule="every 1h",
            script="collect.py",
        )
    )

    assert result["success"] is False
    assert "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS=1" in result["error"]


def test_restricted_mode_blocks_delegation(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "restricted")
    monkeypatch.delenv("TAIJI_ALLOW_DELEGATE_TASK", raising=False)

    from tools.delegate_tool import delegate_task

    result = json.loads(delegate_task(goal="spawn child"))

    assert result["success"] is False
    assert "TAIJI_ALLOW_DELEGATE_TASK=1" in result["error"]


def test_full_mode_allows_security_sensitive_tools_by_default(monkeypatch):
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "full")
    monkeypatch.delenv("TAIJI_ALLOW_TERMINAL", raising=False)
    monkeypatch.delenv("TAIJI_ALLOW_EXECUTE_CODE", raising=False)
    monkeypatch.delenv("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS", raising=False)
    monkeypatch.delenv("TAIJI_ALLOW_DELEGATE_TASK", raising=False)

    from tools.taiji_security_mode import (
        is_cron_script_allowed,
        is_delegate_task_allowed,
        is_execute_code_allowed,
        is_terminal_allowed,
    )

    assert is_terminal_allowed()
    assert is_execute_code_allowed()
    assert is_cron_script_allowed()
    assert is_delegate_task_allowed()


def test_execute_code_uses_taiji_tmp_dir_when_allowed(monkeypatch, tmp_path):
    taiji_tmp = tmp_path / "taiji-tmp"
    monkeypatch.setenv("TAIJI_SECURITY_MODE", "full")
    monkeypatch.setenv("TAIJI_AGENT_TMP_DIR", str(taiji_tmp))

    from tools.code_execution_tool import execute_code

    result = json.loads(execute_code("import os; print(os.path.dirname(__file__))"))

    assert result["status"] == "success"
    cwd = Path(result["output"].strip())
    assert cwd.parent == taiji_tmp
    assert cwd.name.startswith("hermes_sandbox_")
