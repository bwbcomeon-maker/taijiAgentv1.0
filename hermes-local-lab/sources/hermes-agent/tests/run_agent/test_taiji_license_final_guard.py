from unittest.mock import patch
import traceback

import pytest

import run_agent
import taiji_license


def test_aiagent_final_license_guard_blocks_before_conversation_loop():
    agent = object.__new__(run_agent.AIAgent)
    blocked = taiji_license.LicenseStatus(
        status="missing",
        required=True,
        code="license_missing",
        message=taiji_license.MESSAGE_MISSING,
    )

    with patch("run_agent.taiji_license.require_valid_license", return_value=blocked), patch(
        "agent.conversation_loop.run_conversation"
    ) as conversation_loop:
        with pytest.raises(taiji_license.LicenseExecutionBlocked) as caught:
            agent.run_conversation("hello")

    assert caught.value.code == "license_missing"
    assert caught.value.status_code == 403
    conversation_loop.assert_not_called()


def test_aiagent_source_development_reaches_conversation_loop():
    agent = object.__new__(run_agent.AIAgent)
    expected = {"final_response": "ok"}

    with patch("run_agent.taiji_license.require_valid_license", return_value=None), patch(
        "agent.conversation_loop.run_conversation", return_value=expected
    ) as conversation_loop:
        result = agent.run_conversation("hello")

    assert result == expected
    conversation_loop.assert_called_once()


def test_aiagent_final_license_guard_fails_closed_when_status_check_crashes():
    agent = object.__new__(run_agent.AIAgent)

    try:
        with patch(
            "run_agent.taiji_license.require_valid_license",
            side_effect=OSError("sensitive path"),
        ), patch("agent.conversation_loop.run_conversation") as conversation_loop:
            agent.run_conversation("hello")
    except taiji_license.LicenseExecutionBlocked as caught:
        caught_error = caught
        rendered = "".join(traceback.format_exception(caught))
    else:
        pytest.fail("final guard did not fail closed")

    assert caught_error.code == "license_status_unavailable"
    assert "sensitive path" not in rendered
    conversation_loop.assert_not_called()


def test_quiet_cli_catches_license_block_without_traceback():
    cli_source = (run_agent.Path(run_agent.__file__).with_name("cli.py")).read_text(
        encoding="utf-8"
    )
    quiet_start = cli_source.index("# Suppress streaming display callbacks")
    quiet_end = cli_source.index("# Exit with error code if credentials", quiet_start)
    quiet_branch = cli_source[quiet_start:quiet_end]

    assert "except taiji_license.LicenseExecutionBlocked as exc:" in quiet_branch
    assert 'print(f"Error: {exc.message}", file=sys.stderr)' in quiet_branch
