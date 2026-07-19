"""Tests for the hermes-tools-as-MCP server module surface.

We don't run a live MCP session in unit tests — that requires the codex
subprocess + client + an event loop. These tests pin the static
contract: the module imports, the EXPOSED_TOOLS list is sane, and the
build helper assembles a server when the SDK is present.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class TestModuleSurface:
    def test_module_imports_clean(self):
        from agent.transports import hermes_tools_mcp_server as m
        assert callable(m.main)
        assert callable(m._build_server)
        assert isinstance(m.EXPOSED_TOOLS, tuple)
        assert len(m.EXPOSED_TOOLS) > 0

    def test_exposed_tools_are_safe_subset(self):
        """We MUST NOT expose tools codex already has, because codex'
        own builtins are better-integrated with its sandbox + approvals.
        Specifically: no terminal/shell, no read_file/write_file, no
        patch — those are codex's built-in tools."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        forbidden = {
            "terminal", "shell", "read_file", "write_file", "patch",
            "search_files", "process",
        }
        leaked = forbidden & set(EXPOSED_TOOLS)
        assert not leaked, (
            f"these tools must NOT be exposed via the codex callback "
            f"because codex has built-in equivalents: {leaked}"
        )

    def test_expected_hermes_specific_tools_listed(self):
        """The Hermes-specific tools should be present so users on the
        codex runtime keep access to them."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        for required in (
            "web_search",
            "web_extract",
            "browser_navigate",
            "vision_analyze",
            "image_generate",
            "skill_view",
        ):
            assert required in EXPOSED_TOOLS, f"missing {required!r}"

    def test_agent_loop_tools_not_exposed(self):
        """delegate_task / memory / session_search / todo require the
        running AIAgent context to dispatch, so a stateless MCP callback
        can't drive them. They must NOT be in EXPOSED_TOOLS."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        for agent_loop_tool in ("delegate_task", "memory", "session_search", "todo"):
            assert agent_loop_tool not in EXPOSED_TOOLS, (
                f"{agent_loop_tool!r} requires the agent loop context "
                "and can't be reached through a stateless MCP callback"
            )

    def test_kanban_worker_tools_exposed(self):
        """Kanban workers run as `hermes chat -q` subprocesses; if they
        come up on the codex_app_server runtime, the worker can do the
        actual work via codex's shell but needs the kanban tools through
        the MCP callback to report back to the kernel. Without these
        tools available, the worker would hang at completion time."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        # Worker handoff tools — every dispatched worker uses at least
        # one of {complete, block, comment} to close out its task.
        for worker_tool in (
            "kanban_complete",
            "kanban_block",
            "kanban_comment",
            "kanban_heartbeat",
        ):
            assert worker_tool in EXPOSED_TOOLS, (
                f"{worker_tool!r} missing from codex callback — kanban "
                "workers on codex_app_server runtime would hang"
            )

    def test_kanban_orchestrator_tools_exposed(self):
        """Orchestrator agents need to dispatch new tasks, query the
        board, and unblock/link tasks. Exposed so an orchestrator on
        codex_app_server can do its job."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        for orch_tool in (
            "kanban_create",
            "kanban_show",
            "kanban_list",
            "kanban_unblock",
            "kanban_link",
        ):
            assert orch_tool in EXPOSED_TOOLS, (
                f"{orch_tool!r} missing from codex callback"
            )

    def test_mcp_image_handler_freezes_private_capability_fingerprint(
        self,
        monkeypatch,
    ):
        """An MCP handler must keep its creation-generation caller identity."""
        from agent import image_runtime
        from agent.transports import hermes_tools_mcp_server as m
        import mcp.server.fastmcp as fastmcp_module
        import model_tools

        definitions = [
            {
                "type": "function",
                "function": {
                    "name": "image_generate",
                    "description": "Generate an image.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                        },
                        "required": ["prompt"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "q": {"type": "string"},
                        },
                        "required": ["q"],
                    },
                },
            },
        ]
        calls = []

        class FakeFastMCP:
            def __init__(self, *_args, **_kwargs):
                self.handlers = {}

            def add_tool(self, handler, *, name, description):
                self.handlers[name] = handler

        monkeypatch.setattr(fastmcp_module, "FastMCP", FakeFastMCP)
        monkeypatch.setattr(
            model_tools,
            "get_tool_definitions",
            lambda **_kwargs: definitions,
        )

        def fake_handle(name, kwargs, **dispatch_kwargs):
            calls.append((name, kwargs, dispatch_kwargs))
            return json.dumps({"success": True})

        monkeypatch.setattr(
            model_tools,
            "handle_function_call",
            fake_handle,
        )
        generation = {
            "value": SimpleNamespace(
                stable=True,
                image_generation=(
                    1,
                    "mcp-image-generation-a",
                    "verified",
                    True,
                ),
            )
        }
        monkeypatch.setattr(
            image_runtime,
            "capture_capability_runtime_generation",
            lambda: generation["value"],
        )

        server = m._build_server()
        generation["value"] = SimpleNamespace(
            stable=True,
            image_generation=(
                1,
                "mcp-image-generation-b",
                "verified",
                True,
            ),
        )

        server.handlers["image_generate"](prompt="draw a cat")
        server.handlers["web_search"](q="cat")

        assert calls == [
            (
                "image_generate",
                {"prompt": "draw a cat"},
                {
                    "caller_capability_fingerprint":
                        "mcp-image-generation-a",
                },
            ),
            ("web_search", {"q": "cat"}, {}),
        ]
        assert "caller_capability_fingerprint" not in json.dumps(
            definitions
        )

    def test_old_mcp_image_handler_fails_stale_before_provider_boundary(
        self,
        monkeypatch,
    ):
        """Rotating capability state must make an existing handler inert."""
        from agent import image_runtime
        from agent.transports import hermes_tools_mcp_server as m
        import mcp.server.fastmcp as fastmcp_module
        import model_tools
        from tools import image_generation_tool

        class FakeFastMCP:
            def __init__(self, *_args, **_kwargs):
                self.handlers = {}

            def add_tool(self, handler, *, name, description):
                self.handlers[name] = handler

        monkeypatch.setattr(fastmcp_module, "FastMCP", FakeFastMCP)
        monkeypatch.setattr(
            model_tools,
            "get_tool_definitions",
            lambda **_kwargs: [
                {
                    "type": "function",
                    "function": {
                        "name": "image_generate",
                        "description": "Generate an image.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"},
                            },
                            "required": ["prompt"],
                        },
                    },
                }
            ],
        )
        monkeypatch.setattr(
            image_runtime,
            "capture_capability_runtime_generation",
            lambda: SimpleNamespace(
                stable=True,
                image_generation=(
                    1,
                    "mcp-image-generation-old",
                    "verified",
                    True,
                ),
            ),
        )
        current = {
            "schema_version": 1,
            "fingerprint": "mcp-image-generation-new",
            "status": "verified",
            "available": True,
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "reason_code": "ready",
        }
        monkeypatch.setattr(
            image_runtime,
            "verification_runtime_snapshot",
            lambda *_args, **_kwargs: dict(current),
        )
        provider_boundary = []

        def must_not_capture_binding():
            provider_boundary.append("binding")
            raise AssertionError("stale MCP handler reached Provider boundary")

        monkeypatch.setattr(
            image_generation_tool,
            "_capture_image_gen_request_binding",
            must_not_capture_binding,
        )

        server = m._build_server()
        result = json.loads(
            server.handlers["image_generate"](prompt="draw a cat")
        )

        assert result["success"] is False
        assert result["error_code"] == "capability_caller_stale"
        assert provider_boundary == []


class TestMain:
    def test_main_returns_2_when_mcp_unavailable(self, monkeypatch):
        """When the mcp package isn't installed, main() should exit
        cleanly with code 2 and an install hint, not crash."""
        import agent.transports.hermes_tools_mcp_server as m

        def boom_build(*a, **kw):
            raise ImportError("mcp not installed")

        monkeypatch.setattr(m, "_build_server", boom_build)
        rc = m.main(["--verbose"])
        assert rc == 2

    def test_main_handles_keyboard_interrupt(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        class FakeServer:
            def run(self):
                raise KeyboardInterrupt()

        monkeypatch.setattr(m, "_build_server", lambda: FakeServer())
        rc = m.main([])
        assert rc == 0

    def test_main_returns_1_on_runtime_error(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        class CrashingServer:
            def run(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(m, "_build_server", lambda: CrashingServer())
        rc = m.main([])
        assert rc == 1
