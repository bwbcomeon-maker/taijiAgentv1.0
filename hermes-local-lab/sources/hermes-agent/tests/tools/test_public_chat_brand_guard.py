import json

import pytest

from agent.brand_safety import block_reason_for_terminal, block_reason_for_tool
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


def test_public_chat_blocks_tool_aliases_that_probe_internal_resources():
    probes = [
        (
            "read_file_tool",
            {"path": "/opt/taiji-agent/runtime/licenses/agent-runtime.LICENSE"},
        ),
        (
            "list_directory",
            {"path": "/opt/taiji-agent/runtime"},
        ),
        (
            "search",
            {"query": "Nous Research", "path": "/opt/taiji-agent/runtime"},
        ),
        (
            "execute_command",
            {"command": "ps -ef | grep taiji"},
        ),
        (
            "mcp_call",
            {"tool": "filesystem.read", "path": "/opt/taiji-agent/runtime/config.yaml"},
        ),
    ]

    for tool_name, args in probes:
        reason = block_reason_for_tool(tool_name, args)
        assert reason, tool_name
        assert "内部实现" in reason


def test_public_chat_blocks_terminal_local_service_access_probe():
    probes = [
        "curl http://127.0.0.1:18787/taiji/health",
        "lsof -nP -iTCP -sTCP:LISTEN | grep taiji",
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


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("read_file", {"path": "/workspace/customer/runtime/config.yaml"}),
        ("read_file", {"path": "/workspace/customer/agent.log"}),
        ("read_file", {"path": "/workspace/reports/site-packages-inventory.txt"}),
        ("search_files", {"query": "Nous Research", "path": "/workspace/vendor-review"}),
        ("browser_navigate", {"url": "http://127.0.0.1:8443/customer/health"}),
        ("execute_command", {"command": "curl http://localhost:8443/customer/health"}),
        ("execute_command", {"command": "python -c 'print(\"runtime ready\")'"}),
        ("execute_command", {"command": "lsof -nP -iTCP -sTCP:LISTEN"}),
    ],
)
def test_public_chat_allows_normal_business_runtime_and_local_service_tasks(tool_name, args):
    assert block_reason_for_tool(tool_name, args) is None


def test_public_chat_requires_internal_target_for_sensitive_access_intent():
    assert block_reason_for_terminal("cat /opt/taiji-agent/runtime/config.yaml")
    assert block_reason_for_terminal("ps -ef | grep taiji")
    assert block_reason_for_terminal("curl http://localhost:18787/taiji/health")
    assert block_reason_for_terminal("curl http://localhost:8443/customer/health") is None


@pytest.mark.parametrize(
    "command",
    [
        "python -c \"open('/opt/taiji-agent/runtime/config.yaml').read()\"",
        "node -e \"require('fs').readFileSync('/opt/taiji-agent/runtime/config.yaml')\"",
        "cp /opt/taiji-agent/runtime/licenses/agent-runtime.LICENSE /tmp/license-copy",
        "dd if=/opt/taiji-agent/runtime/licenses/agent-runtime.LICENSE of=/tmp/license-copy",
        "tar -cf /tmp/runtime.tar /opt/taiji-agent/runtime",
        "base64 /opt/taiji-agent/runtime/licenses/agent-runtime.LICENSE",
        "sh -c '. /opt/taiji-agent/runtime/runtime-env.sh'",
    ],
)
def test_public_chat_terminal_fails_closed_for_indirect_internal_reads(command):
    reason = block_reason_for_terminal(command)
    assert reason, command
    assert "内部实现" in reason


@pytest.mark.parametrize(
    "command",
    [
        "pgrep -af taiji-agent",
        "launchctl print gui/501/com.taiji.agent",
        "systemctl status taiji-agent.service",
        "journalctl -u taiji-agent",
        "busybox top -b | sed -n '/taiji-agent/p'",
    ],
)
def test_public_chat_terminal_blocks_explicit_internal_targets_without_command_allowlist(command):
    reason = block_reason_for_terminal(command)
    assert reason, command
    assert "内部实现" in reason


@pytest.mark.parametrize(
    "command",
    [
        "rg 'taiji' README.md",
        "python -c \"print('taiji project release notes')\"",
        "npm test -- taiji-customer-workflow",
    ],
)
def test_public_chat_terminal_allows_ordinary_project_text_containing_taiji(command):
    assert block_reason_for_terminal(command) is None
