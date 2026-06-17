import json

import pytest

from agent.brand_safety import block_reason_for_terminal
from tools.file_tools import read_file_tool, search_tool


@pytest.fixture(autouse=True)
def public_chat_guard(monkeypatch):
    monkeypatch.setenv("TAIJI_PUBLIC_CHAT_GUARD", "1")
    yield


def test_public_chat_blocks_reading_runtime_license(monkeypatch):
    def _fail_ops(*_args, **_kwargs):
        raise AssertionError("blocked internal read must not reach file ops")

    monkeypatch.setattr("tools.file_tools._get_file_ops", _fail_ops)

    result = json.loads(
        read_file_tool("/opt/taiji-agent/runtime/licenses/agent-runtime.LICENSE", task_id="brand")
    )

    assert result["status"] == "blocked"
    assert "内部实现" in result["error"]


def test_public_chat_blocks_searching_internal_runtime(monkeypatch):
    def _fail_ops(*_args, **_kwargs):
        raise AssertionError("blocked internal search must not reach file ops")

    monkeypatch.setattr("tools.file_tools._get_file_ops", _fail_ops)

    result = json.loads(
        search_tool("Nous Research", path="/opt/taiji-agent/runtime", task_id="brand")
    )

    assert result["status"] == "blocked"
    assert "内部实现" in result["error"]


def test_public_chat_blocks_terminal_runtime_probe(monkeypatch):
    reason = block_reason_for_terminal(
        "find /opt/taiji-agent -iname '*LICENSE*' -print",
    )

    assert reason
    assert "内部实现" in reason


def test_public_chat_blocks_terminal_local_service_access_probe():
    probes = [
        "curl http://127.0.0.1:18787/health",
        "lsof -nP -iTCP -sTCP:LISTEN",
        "ps -ef | grep taiji",
        "cat ~/.local/state/taiji-agent/logs/web.log",
    ]

    for command in probes:
        reason = block_reason_for_terminal(command)
        assert reason, command
        assert "内部实现" in reason


def test_public_chat_blocks_reading_desktop_runtime_logs(monkeypatch):
    def _fail_ops(*_args, **_kwargs):
        raise AssertionError("blocked log read must not reach file ops")

    monkeypatch.setattr("tools.file_tools._get_file_ops", _fail_ops)

    result = json.loads(
        read_file_tool("~/.local/state/taiji-agent/logs/taiji-desktop.log", task_id="brand")
    )

    assert result["status"] == "blocked"
    assert "内部实现" in result["error"]


def test_public_chat_allows_user_workspace_read(monkeypatch, tmp_path):
    sample = tmp_path / "customer.txt"
    sample.write_text("业务材料\n", encoding="utf-8")

    class _Result:
        content = "1|业务材料"

        def to_dict(self):
            return {"content": self.content, "total_lines": 1, "file_size": 13}

    class _Ops:
        def read_file(self, *_args, **_kwargs):
            return _Result()

    monkeypatch.setattr("tools.file_tools._get_file_ops", lambda *_args, **_kwargs: _Ops())

    result = json.loads(read_file_tool(str(sample), task_id="brand"))

    assert "error" not in result
    assert "业务材料" in result["content"]
