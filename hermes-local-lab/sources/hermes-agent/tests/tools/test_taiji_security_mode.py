import json
from pathlib import Path


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
    assert status["capabilities"]["execute_code"]["allowed"] is True
    assert status["capabilities"]["document_read"]["allowed"] is True
    assert status["capabilities"]["terminal"]["approval_applicable"] is True


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
