"""Tests for /v1/runs endpoints: start, status, events, and stop.

Covers:
- POST /v1/runs — start a run (202)
- GET /v1/runs/{run_id} — poll run status
- GET /v1/runs/{run_id}/events — SSE event stream
- POST /v1/runs/{run_id}/stop — interrupt a running agent
- Auth, error handling, and cleanup
"""

import asyncio
import base64
import inspect
import json
import sqlite3
import threading
import time as _time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    cors_middleware,
    security_headers_middleware,
)
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    """Create an adapter with optional API key."""
    extra = {}
    if api_key:
        extra["key"] = api_key
    config = PlatformConfig(enabled=True, extra=extra)
    adapter = APIServerAdapter(config)
    production_resolver = adapter._resolve_agent_route

    def resolve_route(*, requested_model=None, requested_provider=None):
        if requested_model is None and requested_provider is None:
            return _test_default_route()
        return production_resolver(
            requested_model=requested_model,
            requested_provider=requested_provider,
        )

    adapter._resolve_agent_route = resolve_route
    return adapter


def _create_runs_app(adapter: APIServerAdapter) -> web.Application:
    """Create an aiohttp app with /v1/runs routes registered."""
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/runs", adapter._handle_runs)
    app.router.add_get("/v1/runs/{run_id}", adapter._handle_get_run)
    app.router.add_get("/v1/runs/{run_id}/events", adapter._handle_run_events)
    app.router.add_post("/v1/runs/{run_id}/approval", adapter._handle_run_approval)
    app.router.add_post("/v1/runs/{run_id}/stop", adapter._handle_stop_run)
    return app


def _direct_run_request(body, *, headers=None):
    """Build the minimum request surface needed to await the handler directly."""
    request = MagicMock()
    request.headers = headers or {}
    request.json = AsyncMock(return_value=body)
    return request


def _make_slow_agent(**kwargs):
    """Create a mock agent that blocks in run_conversation until interrupted.

    Returns (mock_agent, agent_ready_event, interrupt_event) where
    agent_ready_event is set once run_conversation starts, and
    interrupt_event is set when interrupt() is called.
    """
    ready = threading.Event()
    interrupted = threading.Event()

    mock_agent = MagicMock()

    def _do_interrupt(message=None):
        interrupted.set()

    mock_agent.interrupt = MagicMock(side_effect=_do_interrupt)

    def _slow_run(user_message=None, conversation_history=None, task_id=None):
        ready.set()
        # Block until interrupt() is called
        interrupted.wait(timeout=10)
        return {"final_response": "interrupted"}

    mock_agent.run_conversation.side_effect = _slow_run
    mock_agent.session_prompt_tokens = 0
    mock_agent.session_completion_tokens = 0
    mock_agent.session_total_tokens = 0

    return mock_agent, ready, interrupted


def _test_default_route():
    return {
        "model": "configured/model",
        "provider": "configured-provider",
        "runtime_kwargs": {
            "provider": "configured-provider",
            "api_mode": "chat_completions",
            "base_url": "https://configured.example/v1",
            "api_key": "configured-key",
            "command": None,
            "args": [],
            "credential_pool": None,
        },
        "fallback_model": None,
    }


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def auth_adapter(monkeypatch):
    adapter = _make_adapter(api_key="sk-secret")
    monkeypatch.setattr(
        adapter,
        "_resolve_agent_route",
        lambda **_kwargs: _test_default_route(),
    )
    return adapter


@pytest.fixture(autouse=True)
def allow_runs_tests_through_the_explicit_license_seam(monkeypatch):
    monkeypatch.setattr(
        APIServerAdapter,
        "_license_guard_response",
        lambda self: None,
    )


# ---------------------------------------------------------------------------
# POST /v1/runs — start a run
# ---------------------------------------------------------------------------


class TestStartRun:
    @pytest.mark.asyncio
    async def test_start_preserves_tool_history_and_webui_turn_identity(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-phase2", "api_server")
        history = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "42"},
        ]
        db.append_message(
            "session-phase2",
            "assistant",
            "",
            tool_calls=history[0]["tool_calls"],
        )
        db.append_message(
            "session-phase2",
            "tool",
            "42",
            tool_call_id="call-1",
        )
        adapter._session_db = db
        app = _create_runs_app(adapter)
        try:
            async with TestClient(TestServer(app)) as cli:
                with patch.object(adapter, "_create_agent") as mock_create:
                    mock_agent = MagicMock()
                    mock_agent.run_conversation.return_value = {"final_response": "done"}
                    mock_agent.session_prompt_tokens = 0
                    mock_agent.session_completion_tokens = 0
                    mock_agent.session_total_tokens = 0
                    mock_create.return_value = mock_agent

                    resp = await cli.post(
                        "/v1/runs",
                        json={
                            "input": [{"role": "user", "content": "follow up"}],
                            "conversation_history": history,
                            "platform_message_id": "webui-turn:turn-123",
                            "session_id": "session-phase2",
                        },
                    )
                    assert resp.status == 202, await resp.text()
                    for _ in range(20):
                        if mock_agent.run_conversation.called:
                            break
                        await asyncio.sleep(0.05)

                    kwargs = mock_agent.run_conversation.call_args.kwargs
                    assert kwargs["conversation_history"] == history
                    assert (
                        kwargs["persist_user_platform_message_id"]
                        == "webui-turn:turn-123"
                    )
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_start_accepts_bare_multimodal_content_parts(self, adapter):
        image_input = [
            {"type": "input_text", "text": "Describe this image."},
            {"type": "input_image", "image_url": "https://example.com/cat.png"},
        ]
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "A cat."}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": image_input})
                assert resp.status == 202, await resp.text()

                for _ in range(20):
                    if mock_agent.run_conversation.called:
                        break
                    await asyncio.sleep(0.05)

                mock_agent.run_conversation.assert_called_once()
                assert mock_agent.run_conversation.call_args.kwargs["user_message"] == [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
                ]

    @pytest.mark.asyncio
    async def test_start_preserves_multimodal_standard_messages(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "Earlier image"},
                                    {"type": "input_image", "image_url": "https://example.com/earlier.png"},
                                ],
                            },
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_image", "image_url": "https://example.com/latest.png"},
                                ],
                            },
                        ]
                    },
                )
                assert resp.status == 202, await resp.text()

                for _ in range(20):
                    if mock_agent.run_conversation.called:
                        break
                    await asyncio.sleep(0.05)

                kwargs = mock_agent.run_conversation.call_args.kwargs
                assert kwargs["conversation_history"] == [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Earlier image"},
                            {"type": "image_url", "image_url": {"url": "https://example.com/earlier.png"}},
                        ],
                    }
                ]
                assert kwargs["user_message"] == [
                    {"type": "image_url", "image_url": {"url": "https://example.com/latest.png"}},
                ]

    @pytest.mark.asyncio
    async def test_start_explicit_history_takes_precedence_and_preserves_images(self, adapter):
        adapter._response_store.put(
            "resp_previous",
            {"conversation_history": [{"role": "assistant", "content": "stored history"}]},
        )
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": [
                            {"role": "assistant", "content": "ignored input history"},
                            {"role": "user", "content": "latest"},
                        ],
                        "conversation_history": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_image", "image_url": "https://example.com/history.png"},
                                ],
                            }
                        ],
                        "previous_response_id": "resp_previous",
                    },
                )
                assert resp.status == 202, await resp.text()

                for _ in range(20):
                    if mock_agent.run_conversation.called:
                        break
                    await asyncio.sleep(0.05)

                kwargs = mock_agent.run_conversation.call_args.kwargs
                assert kwargs["conversation_history"] == [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "https://example.com/history.png"}},
                        ],
                    },
                    {"role": "assistant", "content": "ignored input history"},
                ]
                assert kwargs["user_message"] == "latest"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content", "expected_code"),
        [
            ([{"type": "file", "file": {"file_id": "f_1"}}], "unsupported_content_type"),
            ([{"type": "input_file", "file_id": "f_1"}], "unsupported_content_type"),
            (
                [{"type": "image_url", "image_url": {"url": "data:text/plain;base64,SGVsbG8="}}],
                "unsupported_content_type",
            ),
            ([{"type": "image_url", "image_url": {"url": "ftp://example.com/image.png"}}], "invalid_image_url"),
        ],
    )
    async def test_start_rejects_invalid_multimodal_input_before_allocating_run(
        self, adapter, content, expected_code
    ):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs", json={"input": content})
            body = await resp.json()

        assert resp.status == 400
        assert body["error"]["code"] == expected_code
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_platform_message_id",
        [
            pytest.param(None, id="null"),
            pytest.param(123, id="non-string"),
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace"),
            pytest.param("x" * 257, id="too-long"),
            pytest.param("bad\rid", id="carriage-return"),
            pytest.param("bad\nid", id="line-feed"),
            pytest.param("bad\x00id", id="nul"),
        ],
    )
    async def test_managed_start_rejects_invalid_platform_message_id_before_side_effects(
        self, adapter, tmp_path, invalid_platform_message_id
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-invalid-platform-message-id"
        db.create_session(session_id, "webui")
        db.append_message(session_id, "user", "existing question")
        rows_before = db.get_messages(session_id)
        adapter._session_db = db
        app = _create_runs_app(adapter)

        try:
            async with TestClient(TestServer(app)) as cli:
                with (
                    patch.object(
                        db,
                        "acquire_managed_run_lease",
                        side_effect=AssertionError("lease must not be acquired"),
                    ) as acquire_lease,
                    patch.object(adapter, "_create_agent") as create_agent,
                ):
                    resp = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "current question",
                            "session_id": session_id,
                            "platform_message_id": invalid_platform_message_id,
                        },
                    )
                    payload = await resp.json()

            assert resp.status == 400
            assert payload["error"] == {
                "message": (
                    "platform_message_id must be a non-empty string of at most "
                    "256 characters without CR, LF, or NUL"
                ),
                "type": "invalid_request_error",
                "param": "platform_message_id",
                "code": "invalid_platform_message_id",
            }
            acquire_lease.assert_not_called()
            create_agent.assert_not_called()
            assert adapter._run_streams == {}
            assert adapter._run_statuses == {}
            assert db.get_messages(session_id) == rows_before
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_start_returns_202(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 10
                mock_agent.session_completion_tokens = 5
                mock_agent.session_total_tokens = 15
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                assert data["status"] == "started"
                assert data["run_id"].startswith("run_")
                assert data["session_id"] == data["run_id"]
                assert resp.headers["X-Hermes-Session-Id"] == data["session_id"]

                status_resp = await cli.get(f"/v1/runs/{data['run_id']}")
                assert status_resp.status == 200
                status = await status_resp.json()
                assert status["run_id"] == data["run_id"]
                assert status["status"] in {"queued", "running", "completed"}
                assert status["object"] == "hermes.run"

    @pytest.mark.asyncio
    async def test_start_routes_requested_model_and_provider_into_agent_runtime(
        self, adapter, monkeypatch
    ):
        """The public run selection must reach the real agent construction seam."""
        resolver_calls = []
        constructed = {}
        finished = threading.Event()

        def fake_resolve_runtime_provider(
            *,
            requested=None,
            explicit_api_key=None,
            explicit_base_url=None,
            target_model=None,
        ):
            resolver_calls.append(
                {
                    "requested": requested,
                    "target_model": target_model,
                }
            )
            if requested == "requested-provider" and target_model == "requested/model":
                return {
                    "provider": "requested-provider",
                    "api_mode": "chat_completions",
                    "base_url": "https://requested.example/v1",
                    "api_key": "requested-key",
                }
            return {
                "provider": "configured-provider",
                "api_mode": "chat_completions",
                "base_url": "https://configured.example/v1",
                "api_key": "configured-key",
            }

        class RecordingAgent:
            session_prompt_tokens = 0
            session_completion_tokens = 0
            session_total_tokens = 0

            def __init__(self, **kwargs):
                constructed.update(kwargs)

            def run_conversation(self, **kwargs):
                finished.set()
                return {"final_response": "done"}

        monkeypatch.setattr(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            fake_resolve_runtime_provider,
        )
        monkeypatch.setattr("run_agent.AIAgent", RecordingAgent)
        monkeypatch.setattr(
            "gateway.run._resolve_gateway_model",
            lambda: "configured/model",
        )
        monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
        monkeypatch.setattr(
            "gateway.run.GatewayRunner._load_reasoning_config",
            staticmethod(lambda: {}),
        )
        monkeypatch.setattr(
            "gateway.run.GatewayRunner._load_fallback_model",
            staticmethod(lambda: [{"provider": "must-not-be-used"}]),
        )
        monkeypatch.setattr(
            "hermes_cli.tools_config._get_platform_tools",
            lambda *_: set(),
        )
        monkeypatch.setattr(adapter, "_ensure_session_db", lambda: None)
        app = _create_runs_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={
                    "input": "route this turn",
                    "model": "requested/model",
                    "provider": "requested-provider",
                },
            )
            payload = await resp.json()
            assert await asyncio.to_thread(finished.wait, 3.0)

            status_resp = await cli.get(f"/v1/runs/{payload['run_id']}")
            status_payload = await status_resp.json()

        assert resp.status == 202, payload
        assert resolver_calls == [
            {
                "requested": "requested-provider",
                "target_model": "requested/model",
            }
        ]
        assert constructed["model"] == "requested/model"
        assert constructed["provider"] == "requested-provider"
        assert constructed["base_url"] == "https://requested.example/v1"
        assert constructed["api_key"] == "requested-key"
        assert constructed["fallback_model"] is None
        assert status_payload["model"] == "requested/model"
        assert status_payload["provider"] == "requested-provider"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "route_fields",
        [
            {"model": "requested/model"},
            {"provider": "openrouter"},
            {"model": "default", "provider": "openrouter"},
        ],
    )
    async def test_start_requires_a_complete_explicit_route_before_resolution(
        self, adapter, route_fields
    ):
        with patch.object(adapter, "_resolve_agent_route") as resolve_route:
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello", **route_fields},
                )
                payload = await resp.json()

        assert resp.status == 400
        assert payload["error"]["code"] == "incomplete_model_route"
        resolve_route.assert_not_called()
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}

    @pytest.mark.asyncio
    async def test_start_normalizes_qualified_selector_before_runtime_resolution(
        self, adapter, monkeypatch
    ):
        resolver_calls = []
        finished = threading.Event()

        def fake_runtime_resolver(
            *,
            requested=None,
            explicit_api_key=None,
            explicit_base_url=None,
            target_model=None,
        ):
            resolver_calls.append((requested, target_model))
            return {
                "provider": "anthropic",
                "api_mode": "anthropic_messages",
                "base_url": "https://api.anthropic.com",
                "api_key": "anthropic-key",
            }

        class RecordingAgent:
            session_prompt_tokens = 0
            session_completion_tokens = 0
            session_total_tokens = 0

            def __init__(self, **kwargs):
                assert kwargs["model"] == "claude-sonnet-4-6"

            def run_conversation(self, **kwargs):
                finished.set()
                return {"final_response": "done"}

        monkeypatch.setattr(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            fake_runtime_resolver,
        )
        monkeypatch.setattr("run_agent.AIAgent", RecordingAgent)
        monkeypatch.setattr("gateway.run._load_gateway_config", lambda: {})
        monkeypatch.setattr(
            "gateway.run.GatewayRunner._load_reasoning_config",
            staticmethod(lambda: {}),
        )
        monkeypatch.setattr(
            "hermes_cli.tools_config._get_platform_tools",
            lambda *_: set(),
        )
        monkeypatch.setattr(adapter, "_ensure_session_db", lambda: None)
        monkeypatch.setattr(
            adapter,
            "_resolve_agent_route",
            APIServerAdapter._resolve_agent_route.__get__(adapter),
        )
        app = _create_runs_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={
                    "input": "hello",
                    "model": "@AnThRoPiC:anthropic/claude-sonnet-4.6",
                    "provider": "ANTHROPIC",
                },
            )
            payload = await resp.json()
            assert await asyncio.to_thread(finished.wait, 3.0)

        assert resp.status == 202, payload
        assert resolver_calls == [("anthropic", "claude-sonnet-4-6")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model", "provider"),
        [
            ("@openrouter:anthropic/claude-sonnet-4.6", "anthropic"),
            ("@anthropic:", "anthropic"),
        ],
    )
    async def test_start_rejects_mismatched_or_empty_qualified_selector(
        self, adapter, model, provider
    ):
        with patch.object(adapter, "_resolve_agent_route") as resolve_route:
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "model": model,
                        "provider": provider,
                    },
                )
                payload = await resp.json()

        assert resp.status == 400
        assert payload["error"]["code"] == "invalid_model_selector"
        resolve_route.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_preserves_custom_selector_remainder_including_suffix(
        self, adapter
    ):
        route = _test_default_route()
        route["model"] = "org/model:free"
        route["provider"] = "custom:my-relay"
        route["runtime_kwargs"] = {
            **route["runtime_kwargs"],
            "provider": "custom",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "no-key-required",
        }
        finished = threading.Event()

        def resolve_route(*, requested_model=None, requested_provider=None):
            assert requested_model == "org/model:free"
            assert requested_provider == "custom:my-relay"
            return route

        agent = MagicMock()
        agent.run_conversation.side_effect = lambda **_kwargs: (
            finished.set() or {"final_response": "done"}
        )
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        with (
            patch.object(adapter, "_resolve_agent_route", side_effect=resolve_route),
            patch.object(adapter, "_create_agent", return_value=agent),
        ):
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "model": "@custom:my-relay:org/model:free",
                        "provider": "custom:my-relay",
                    },
                )
                payload = await resp.json()
                assert await asyncio.to_thread(finished.wait, 3.0)

        assert resp.status == 202, payload

    @pytest.mark.asyncio
    async def test_default_route_is_resolved_once_and_status_uses_actual_route(
        self, adapter
    ):
        route = _test_default_route()
        finished = threading.Event()
        agent = MagicMock()
        agent.run_conversation.side_effect = lambda **_kwargs: (
            finished.set() or {"final_response": "done"}
        )
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        with (
            patch.object(
                adapter,
                "_resolve_agent_route",
                return_value=route,
            ) as resolve_route,
            patch.object(
                adapter,
                "_create_agent",
                return_value=agent,
            ) as create_agent,
        ):
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                payload = await resp.json()
                assert await asyncio.to_thread(finished.wait, 3.0)
                status_resp = await cli.get(f"/v1/runs/{payload['run_id']}")
                status_payload = await status_resp.json()

        assert resp.status == 202, payload
        resolve_route.assert_called_once_with(
            requested_model=None,
            requested_provider=None,
        )
        assert create_agent.call_args.kwargs["resolved_route"] is route
        assert status_payload["model"] == "configured/model"
        assert status_payload["provider"] == "configured-provider"

    @pytest.mark.asyncio
    async def test_invalid_platform_message_id_never_calls_route_resolver(
        self, adapter
    ):
        with patch.object(adapter, "_resolve_agent_route") as resolve_route:
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "platform_message_id": "bad\nid",
                    },
                )
                payload = await resp.json()

        assert resp.status == 400
        assert payload["error"]["code"] == "invalid_platform_message_id"
        resolve_route.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_resolved_credentials_fail_before_run_allocation(
        self, adapter
    ):
        invalid_route = {
            "model": "anthropic/claude-sonnet-4.6",
            "provider": "openrouter",
            "runtime_kwargs": {
                "provider": "openrouter",
                "api_mode": "chat_completions",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "",
                "command": None,
                "args": [],
                "credential_pool": None,
            },
            "fallback_model": None,
        }
        with (
            patch.object(
                adapter,
                "_resolve_agent_route",
                return_value=invalid_route,
            ),
            patch.object(adapter, "_create_agent") as create_agent,
        ):
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "model": "anthropic/claude-sonnet-4.6",
                        "provider": "openrouter",
                    },
                )
                payload = await resp.json()

        assert resp.status == 400
        assert payload["error"]["code"] == "model_configuration_error"
        create_agent.assert_not_called()
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_resolver_non_task_factory_result_is_503_and_unowned(
        self, adapter
    ):
        adapter._MAX_RETAINED_RUN_STREAMS = 0
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task
        captured = {}
        bare_future = loop.create_future()

        def _create_task(coroutine, *args, **kwargs):
            if (
                "coroutine" not in captured
                and getattr(
                    getattr(coroutine, "cr_code", None),
                    "co_name",
                    "",
                )
                == "to_thread"
            ):
                captured["coroutine"] = coroutine
                loop.call_soon(
                    lambda: (
                        None
                        if bare_future.done()
                        else bare_future.set_result(_test_default_route())
                    )
                )
                return bare_future
            return original_create_task(coroutine, *args, **kwargs)

        with patch.object(
            loop,
            "create_task",
            side_effect=_create_task,
        ):
            response = await adapter._handle_runs(
                _direct_run_request({"input": "reject a non-task resolver"})
            )

        payload = json.loads(response.text)
        assert response.status == 503
        assert payload["error"]["code"] == "run_executor_unavailable"
        assert bare_future.cancelled()
        assert inspect.getcoroutinestate(captured["coroutine"]) == "CORO_CLOSED"
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_concurrent_admission_reserves_at_most_ten_before_resolution(
        self, adapter
    ):
        entered = 0
        entered_lock = threading.Lock()
        release_resolvers = threading.Event()

        def delayed_resolver(**_kwargs):
            nonlocal entered
            with entered_lock:
                entered += 1
            release_resolvers.wait(timeout=5)
            return _test_default_route()

        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "done"}
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        with (
            patch.object(
                adapter,
                "_resolve_agent_route",
                side_effect=delayed_resolver,
            ),
            patch.object(adapter, "_create_agent", return_value=agent),
        ):
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                requests = [
                    asyncio.create_task(
                        cli.post(
                            "/v1/runs",
                            json={
                                "input": f"run {i}",
                                "model": "requested/model",
                                "provider": "requested-provider",
                            },
                        )
                    )
                    for i in range(15)
                ]
                deadline = asyncio.get_running_loop().time() + 3
                while entered < 10 and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.01)
                observed_during_resolution = entered
                release_resolvers.set()
                responses = await asyncio.gather(*requests)

        statuses = [response.status for response in responses]
        assert observed_during_resolution == 10
        assert statuses.count(202) == 10
        assert statuses.count(429) == 5
        assert entered == 10
        await asyncio.sleep(0)
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_cancelled_resolver_storm_keeps_slots_until_threads_exit(
        self, adapter
    ):
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        unhandled_contexts = []
        loop.set_exception_handler(
            lambda _loop, context: unhandled_contexts.append(context)
        )
        entered = 0
        entered_lock = threading.Lock()
        ten_entered = threading.Event()
        release_resolvers = threading.Event()

        def delayed_resolver(**_kwargs):
            nonlocal entered
            with entered_lock:
                entered += 1
                if entered == adapter._MAX_CONCURRENT_RUNS:
                    ten_entered.set()
            release_resolvers.wait()
            raise RuntimeError("resolver completed after its request was cancelled")

        request_tasks = []
        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "done"}
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0
        try:
            with (
                patch.object(
                    adapter,
                    "_resolve_agent_route",
                    side_effect=delayed_resolver,
                ),
                patch.object(adapter, "_create_agent", return_value=agent),
            ):
                for i in range(adapter._MAX_CONCURRENT_RUNS):
                    request_tasks.append(
                        asyncio.create_task(
                            adapter._handle_runs(
                                _direct_run_request({
                                    "input": f"cancelled resolver {i}",
                                    "model": "requested/model",
                                    "provider": "requested-provider",
                                })
                            )
                        )
                    )

                assert await asyncio.to_thread(ten_entered.wait, 3.0)
                for task in request_tasks:
                    task.cancel()
                cancelled = await asyncio.gather(
                    *request_tasks,
                    return_exceptions=True,
                )
                assert all(
                    isinstance(item, asyncio.CancelledError) for item in cancelled
                )

                overflow_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "must remain rejected while resolver threads run",
                            "model": "requested/model",
                            "provider": "requested-provider",
                        })
                    )
                )
                await asyncio.sleep(0.05)
                overflow_was_immediate = overflow_task.done()
                observed_entered = entered
                observed_reservations = len(adapter._run_admission_reservations)

                release_resolvers.set()
                overflow = await overflow_task
                overflow_payload = json.loads(overflow.text)
                deadline = asyncio.get_running_loop().time() + 3
                while (
                    adapter._run_admission_reservations
                    and asyncio.get_running_loop().time() < deadline
                ):
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0)
        finally:
            release_resolvers.set()
            loop.set_exception_handler(previous_exception_handler)

        assert overflow_was_immediate
        assert overflow.status == 429
        assert overflow_payload["error"]["code"] == "rate_limit_exceeded"
        assert observed_entered == adapter._MAX_CONCURRENT_RUNS
        assert observed_reservations == adapter._MAX_CONCURRENT_RUNS
        assert entered == adapter._MAX_CONCURRENT_RUNS
        assert adapter._run_admission_reservations == set()
        assert unhandled_contexts == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "resolver_outcome",
        ["success", "error", "cancel"],
    )
    async def test_resolver_callback_failure_keeps_exact_admission_owner(
        self,
        adapter,
        resolver_outcome,
    ):
        loop = asyncio.get_running_loop()
        previous_task_factory = loop.get_task_factory()
        resolver_started = [
            threading.Event()
            for _ in range(adapter._MAX_CONCURRENT_RUNS)
        ]
        resolver_release = [
            threading.Event()
            for _ in range(adapter._MAX_CONCURRENT_RUNS)
        ]
        resolver_finished = [
            threading.Event()
            for _ in range(adapter._MAX_CONCURRENT_RUNS)
        ]
        resolver_calls = 0
        resolver_lock = threading.Lock()
        callback_attempts = []
        resolver_tasks = []
        request_tasks = []
        resolver_tokens = []
        release_calls = []
        factory_state = {
            "armed": False,
            "callback_failed": None,
        }
        original_release_admission = adapter._release_run_admission

        class _ResolverCallbackFailingTask(asyncio.Task):
            def __init__(
                self,
                coroutine,
                *,
                task_loop,
                context,
                callback_failed,
            ):
                kwargs = {"loop": task_loop}
                if context is not None:
                    kwargs["context"] = context
                super().__init__(coroutine, **kwargs)
                self._callback_failed = callback_failed

            def add_done_callback(self, callback, *, context=None):
                if (
                    getattr(callback, "__name__", "")
                    == "_release_after_preworker"
                ):
                    callback_attempts.append(self)
                    self._callback_failed.set()
                    raise RuntimeError(
                        "preworker release callback registration failed"
                    )
                if context is None:
                    return super().add_done_callback(callback)
                return super().add_done_callback(
                    callback,
                    context=context,
                )

        def _delegate_task(factory_loop, coroutine, context):
            if previous_task_factory is not None:
                if context is None:
                    return previous_task_factory(factory_loop, coroutine)
                return previous_task_factory(
                    factory_loop,
                    coroutine,
                    context=context,
                )
            kwargs = {"loop": factory_loop}
            if context is not None:
                kwargs["context"] = context
            return asyncio.Task(coroutine, **kwargs)

        def _selective_task_factory(factory_loop, coroutine, context=None):
            coroutine_name = getattr(
                getattr(coroutine, "cr_code", None),
                "co_name",
                "",
            )
            if factory_state["armed"] and coroutine_name == "to_thread":
                factory_state["armed"] = False
                task = _ResolverCallbackFailingTask(
                    coroutine,
                    task_loop=factory_loop,
                    context=context,
                    callback_failed=factory_state["callback_failed"],
                )
                resolver_tasks.append(task)
                return task
            return _delegate_task(factory_loop, coroutine, context)

        def _resolver(**_kwargs):
            nonlocal resolver_calls
            with resolver_lock:
                call_index = resolver_calls
                resolver_calls += 1
            if call_index >= adapter._MAX_CONCURRENT_RUNS:
                return _test_default_route()
            resolver_started[call_index].set()
            resolver_release[call_index].wait()
            try:
                if resolver_outcome == "error":
                    raise RuntimeError("resolver failed after callback failure")
                return _test_default_route()
            finally:
                resolver_finished[call_index].set()

        def _record_release(token):
            release_calls.append(token)
            original_release_admission(token)

        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "done"}
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0
        observed = {}
        loop.set_task_factory(_selective_task_factory)
        try:
            with (
                patch.object(
                    adapter,
                    "_resolve_agent_route",
                    side_effect=_resolver,
                ),
                patch.object(adapter, "_create_agent", return_value=agent),
                patch.object(
                    adapter,
                    "_release_run_admission",
                    side_effect=_record_release,
                ),
            ):
                request_results = []
                for index in range(adapter._MAX_CONCURRENT_RUNS):
                    callback_failed = asyncio.Event()
                    factory_state["callback_failed"] = callback_failed
                    factory_state["armed"] = True
                    request_task = asyncio.create_task(
                        adapter._handle_runs(
                            _direct_run_request({
                                "input": f"callback failure {index}",
                                "model": "requested/model",
                                "provider": "requested-provider",
                            })
                        )
                    )
                    request_tasks.append(request_task)
                    assert await asyncio.to_thread(
                        resolver_started[index].wait,
                        3.0,
                    )
                    current_tokens = (
                        set(adapter._run_admission_reservations)
                        - set(resolver_tokens)
                    )
                    assert len(current_tokens) == 1
                    resolver_tokens.append(current_tokens.pop())

                    request_task.cancel()
                    await asyncio.wait_for(callback_failed.wait(), 3.0)
                    # A second request cancellation must not break whichever
                    # owner takes responsibility after callback failure.
                    request_task.cancel()
                    if resolver_outcome == "cancel":
                        resolver_tasks[index].cancel()
                    resolver_release[index].set()
                    request_result = (
                        await asyncio.gather(
                            request_task,
                            return_exceptions=True,
                        )
                    )[0]
                    request_results.append(request_result)
                    assert await asyncio.to_thread(
                        resolver_finished[index].wait,
                        3.0,
                    )
                    await asyncio.gather(
                        resolver_tasks[index],
                        return_exceptions=True,
                    )
                    await asyncio.sleep(0)

                observed["request_results"] = request_results
                observed["reservations_after_resolvers"] = set(
                    adapter._run_admission_reservations
                )
                observed["resolver_release_counts"] = {
                    token: release_calls.count(token)
                    for token in resolver_tokens
                }

                eleventh = await adapter._handle_runs(
                    _direct_run_request({
                        "input": "capacity must recover",
                        "model": "requested/model",
                        "provider": "requested-provider",
                    })
                )
                observed["eleventh_status"] = eleventh.status
                deadline = loop.time() + 3.0
                while (
                    adapter._active_run_tasks
                    and loop.time() < deadline
                ):
                    await asyncio.sleep(0.01)
        finally:
            loop.set_task_factory(previous_task_factory)
            for release_event in resolver_release:
                release_event.set()
            for request_task in request_tasks:
                if not request_task.done():
                    request_task.cancel()
            await asyncio.gather(*request_tasks, return_exceptions=True)
            await asyncio.gather(*resolver_tasks, return_exceptions=True)
            await adapter.cancel_background_tasks()
            for token in tuple(adapter._run_admission_reservations):
                original_release_admission(token)

        assert (
            observed["reservations_after_resolvers"],
            observed["eleventh_status"],
        ) == (set(), 202)
        assert len(callback_attempts) == adapter._MAX_CONCURRENT_RUNS
        assert all(
            isinstance(result, asyncio.CancelledError)
            for result in observed["request_results"]
        )
        assert set(observed["resolver_release_counts"].values()) == {1}

    async def _assert_cancelled_preworker_query_holds_admission(
        self,
        adapter,
        tmp_path,
        *,
        query_name,
        query_outcome,
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_ids = [
            f"session-{query_name}-{index}"
            for index in range(adapter._MAX_CONCURRENT_RUNS)
        ]
        for session_id in session_ids:
            db.create_session(session_id, "api_server")
        adapter._session_db = db
        adapter._MAX_RETAINED_RUN_STREAMS = 0

        original_query = getattr(db, query_name)
        original_release_admission = adapter._release_run_admission
        query_continue = threading.Event()
        ten_queries_entered = threading.Event()
        all_queries_finished = threading.Event()
        query_lock = threading.Lock()
        query_counts = {"entered": 0, "finished": 0}
        release_calls = []
        request_tasks = []
        observed = {}
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        unhandled_contexts = []

        def _blocked_query(*args, **kwargs):
            with query_lock:
                query_counts["entered"] += 1
                if (
                    query_counts["entered"]
                    == adapter._MAX_CONCURRENT_RUNS
                ):
                    ten_queries_entered.set()
            query_continue.wait()
            try:
                if query_outcome == "error":
                    raise sqlite3.OperationalError(
                        f"{query_name} blocked query failed"
                    )
                return original_query(*args, **kwargs)
            finally:
                with query_lock:
                    query_counts["finished"] += 1
                    if (
                        query_counts["finished"]
                        == adapter._MAX_CONCURRENT_RUNS
                    ):
                        all_queries_finished.set()

        def _record_admission_release(token):
            release_calls.append(token)
            original_release_admission(token)

        loop.set_exception_handler(
            lambda _loop, context: unhandled_contexts.append(context)
        )
        try:
            with (
                patch.object(
                    db,
                    query_name,
                    side_effect=_blocked_query,
                ),
                patch.object(
                    adapter,
                    "_release_run_admission",
                    side_effect=_record_admission_release,
                ),
            ):
                request_tasks = [
                    asyncio.create_task(
                        adapter._handle_runs(
                            _direct_run_request({
                                "input": f"cancel {query_name} {index}",
                                "session_id": session_id,
                            })
                        )
                    )
                    for index, session_id in enumerate(session_ids)
                ]
                assert await asyncio.to_thread(
                    ten_queries_entered.wait,
                    3.0,
                )
                reservation_tokens = set(
                    adapter._run_admission_reservations
                )

                for request_task in request_tasks:
                    request_task.cancel()
                    request_task.cancel()
                request_results = await asyncio.gather(
                    *request_tasks,
                    return_exceptions=True,
                )

                observed["request_results"] = request_results
                observed["reservations_while_queries_blocked"] = len(
                    adapter._run_admission_reservations
                )
                observed["queries_finished_while_blocked"] = (
                    query_counts["finished"]
                )

                overflow = await adapter._handle_runs(
                    _direct_run_request({
                        "input": "must remain rate limited",
                    })
                )
                observed["overflow_status"] = overflow.status
                observed["overflow_code"] = json.loads(overflow.text)[
                    "error"
                ]["code"]

                query_continue.set()
                assert await asyncio.to_thread(
                    all_queries_finished.wait,
                    3.0,
                )
                deadline = loop.time() + 3.0
                while (
                    adapter._run_admission_reservations
                    and loop.time() < deadline
                ):
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0)

                observed["reservation_tokens"] = reservation_tokens
                observed["release_calls"] = list(release_calls)
                observed["reservations_after_queries"] = set(
                    adapter._run_admission_reservations
                )
                observed["query_counts"] = dict(query_counts)
                observed["unhandled_contexts"] = list(unhandled_contexts)
        finally:
            query_continue.set()
            for request_task in request_tasks:
                if not request_task.done():
                    request_task.cancel()
            await asyncio.gather(
                *request_tasks,
                return_exceptions=True,
            )
            loop.set_exception_handler(previous_exception_handler)
            for session_id in session_ids:
                lease = db.get_managed_run_lease(session_id)
                if lease is not None:
                    db.release_managed_run_lease(
                        session_id,
                        owner_id=lease["owner_id"],
                        run_id=lease["run_id"],
                    )
            db.close()

        assert all(
            isinstance(result, asyncio.CancelledError)
            for result in observed["request_results"]
        )
        assert observed["reservations_while_queries_blocked"] == (
            adapter._MAX_CONCURRENT_RUNS
        )
        assert observed["queries_finished_while_blocked"] == 0
        assert (
            observed["overflow_status"],
            observed["overflow_code"],
        ) == (429, "rate_limit_exceeded")
        assert observed["reservations_after_queries"] == set()
        assert observed["query_counts"] == {
            "entered": adapter._MAX_CONCURRENT_RUNS,
            "finished": adapter._MAX_CONCURRENT_RUNS,
        }
        assert set(observed["release_calls"]) == observed[
            "reservation_tokens"
        ]
        assert all(
            observed["release_calls"].count(token) == 1
            for token in observed["reservation_tokens"]
        )
        assert observed["unhandled_contexts"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query_outcome", ["success", "error"])
    async def test_cancelled_get_session_queries_keep_all_admission_slots(
        self,
        adapter,
        tmp_path,
        query_outcome,
    ):
        await self._assert_cancelled_preworker_query_holds_admission(
            adapter,
            tmp_path,
            query_name="get_session",
            query_outcome=query_outcome,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query_outcome", ["success", "error"])
    async def test_cancelled_lease_key_queries_keep_all_admission_slots(
        self,
        adapter,
        tmp_path,
        query_outcome,
    ):
        await self._assert_cancelled_preworker_query_holds_admission(
            adapter,
            tmp_path,
            query_name="get_managed_run_lease_key",
            query_outcome=query_outcome,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("query_name", "owned_task_name"),
        [
            ("get_session", "managed-run-session-query-"),
            (
                "get_managed_run_lease_key",
                "managed-run-lease-key-query-",
            ),
        ],
    )
    @pytest.mark.parametrize("failure_mode", ["query", "task_creation"])
    async def test_preworker_db_query_failure_is_stable_503(
        self,
        adapter,
        tmp_path,
        query_name,
        owned_task_name,
        failure_mode,
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = f"session-{query_name}-{failure_mode}"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        adapter._MAX_RETAINED_RUN_STREAMS = 0
        original_create_owned_task = adapter._create_owned_task
        created_query_tasks = []

        def _selective_create_owned_task(coroutine, *, name):
            if name.startswith(owned_task_name):
                created_query_tasks.append(name)
                if failure_mode == "task_creation":
                    coroutine.close()
                    raise RuntimeError(f"{query_name} task unavailable")
            return original_create_owned_task(coroutine, name=name)

        query_patch = (
            patch.object(
                db,
                query_name,
                side_effect=sqlite3.OperationalError(
                    f"{query_name} unavailable"
                ),
            )
            if failure_mode == "query"
            else patch.object(db, query_name, wraps=getattr(db, query_name))
        )

        try:
            with (
                query_patch,
                patch.object(
                    adapter,
                    "_create_owned_task",
                    side_effect=_selective_create_owned_task,
                ),
                patch.object(adapter, "_create_agent") as create_agent,
            ):
                result = (
                    await asyncio.gather(
                        adapter._handle_runs(
                            _direct_run_request({
                                "input": "stable DB query failure",
                                "session_id": session_id,
                            })
                        ),
                        return_exceptions=True,
                    )
                )[0]

            assert getattr(result, "status", None) == 503
            payload = json.loads(result.text)
            assert payload["error"]["code"] == "session_db_unavailable"
            create_agent.assert_not_called()
            assert db.get_managed_run_lease(session_id) is None
            assert adapter._managed_lease_lifecycle_tasks == set()
            assert adapter._run_admission_reservations == set()
            if failure_mode == "task_creation":
                assert len(created_query_tasks) == 1
                assert created_query_tasks[0].startswith(owned_task_name)
        finally:
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                db.release_managed_run_lease(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

    @pytest.mark.asyncio
    async def test_cancelled_managed_lease_acquire_keeps_admission_until_precise_cleanup(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-cancelled-lease-acquire"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        acquire_entered = threading.Event()
        acquire_continue = threading.Event()
        acquire_finished = threading.Event()
        release_entered = threading.Event()
        release_continue = threading.Event()
        acquire_call = {}
        release_calls = []
        original_acquire = db.acquire_managed_run_lease
        original_release = db.release_managed_run_lease
        request_task = None
        drain_tasks = []

        def _blocked_acquire(
            lease_session_id,
            *,
            owner_id,
            run_id,
            lease_seconds,
        ):
            acquire_call.update({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            acquire_entered.set()
            acquire_continue.wait()
            try:
                return original_acquire(
                    lease_session_id,
                    owner_id=owner_id,
                    run_id=run_id,
                    lease_seconds=lease_seconds,
                )
            finally:
                acquire_finished.set()

        def _blocked_release(lease_session_id, *, owner_id, run_id):
            release_calls.append({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            release_entered.set()
            release_continue.wait()
            return original_release(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )

        observed = {}
        try:
            with (
                patch.object(
                    db,
                    "acquire_managed_run_lease",
                    side_effect=_blocked_acquire,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_blocked_release,
                ),
            ):
                request_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "cancel during lease acquire",
                            "session_id": session_id,
                        })
                    )
                )
                assert await asyncio.to_thread(acquire_entered.wait, 3.0)

                request_task.cancel()
                request_task.cancel()
                drain_tasks = [
                    asyncio.create_task(adapter.cancel_background_tasks()),
                    asyncio.create_task(adapter.cancel_background_tasks()),
                ]
                cancelled = await asyncio.gather(
                    request_task,
                    return_exceptions=True,
                )
                observed["cancelled"] = isinstance(
                    cancelled[0], asyncio.CancelledError
                )
                observed["reservations_while_acquire_blocked"] = len(
                    adapter._run_admission_reservations
                )
                await asyncio.sleep(0)
                observed["drains_done_while_acquire_blocked"] = [
                    task.done() for task in drain_tasks
                ]

                acquire_continue.set()
                assert await asyncio.to_thread(acquire_finished.wait, 3.0)
                assert await asyncio.to_thread(release_entered.wait, 3.0)
                observed["drains_done_while_release_blocked"] = [
                    task.done() for task in drain_tasks
                ]
                observed["reservations_while_release_blocked"] = len(
                    adapter._run_admission_reservations
                )
                observed["release_call"] = dict(release_calls[0])

                release_continue.set()
                await asyncio.gather(*drain_tasks)
                observed["release_call_count"] = len(release_calls)
                observed["reservations_after_cleanup"] = set(
                    adapter._run_admission_reservations
                )
                observed["lease_after_cleanup"] = db.get_managed_run_lease(
                    session_id
                )

            adapter._MAX_RETAINED_RUN_STREAMS = 0
            next_response = await adapter._handle_runs(
                _direct_run_request({
                    "input": "next run must enter immediately",
                    "session_id": session_id,
                })
            )
            observed["next_status"] = next_response.status
            observed["next_code"] = json.loads(next_response.text)["error"][
                "code"
            ]
        finally:
            acquire_continue.set()
            release_continue.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if drain_tasks:
                await asyncio.gather(*drain_tasks, return_exceptions=True)
            if acquire_entered.is_set() and not acquire_finished.is_set():
                await asyncio.to_thread(acquire_finished.wait, 3.0)
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                db.release_managed_run_lease(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        assert observed["cancelled"]
        assert observed["reservations_while_acquire_blocked"] == 1
        assert observed["drains_done_while_acquire_blocked"] == [False, False]
        assert acquire_call["session_id"] == session_id
        assert acquire_call["owner_id"] == adapter._managed_run_lease_owner_id
        assert acquire_call["run_id"].startswith("run_")
        assert observed["drains_done_while_release_blocked"] == [False, False]
        assert observed["reservations_while_release_blocked"] == 1
        assert observed["release_call"] == acquire_call
        assert observed["release_call_count"] == 1
        assert observed["lease_after_cleanup"] is None
        assert observed["reservations_after_cleanup"] == set()
        assert observed["next_status"] == 429
        assert observed["next_code"] == "run_stream_capacity_exceeded"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("acquire_outcome", ["false", "error"])
    async def test_cancelled_managed_lease_acquire_shutdown_drains_non_acquire(
        self, adapter, tmp_path, acquire_outcome
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = f"session-cancelled-acquire-{acquire_outcome}"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        acquire_entered = threading.Event()
        acquire_continue = threading.Event()
        release_calls = []
        request_task = None
        drain_tasks = []

        def _blocked_acquire(*_args, **_kwargs):
            acquire_entered.set()
            acquire_continue.wait()
            if acquire_outcome == "error":
                raise sqlite3.OperationalError("acquire failed after cancellation")
            return False

        def _unexpected_release(*args, **kwargs):
            release_calls.append((args, kwargs))
            return False

        observed = {}
        try:
            with (
                patch.object(
                    db,
                    "acquire_managed_run_lease",
                    side_effect=_blocked_acquire,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_unexpected_release,
                ),
            ):
                request_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "cancel non-acquire outcome",
                            "session_id": session_id,
                        })
                    )
                )
                assert await asyncio.to_thread(acquire_entered.wait, 3.0)

                request_task.cancel()
                request_task.cancel()
                drain_tasks = [
                    asyncio.create_task(adapter.cancel_background_tasks()),
                    asyncio.create_task(adapter.cancel_background_tasks()),
                ]
                cancelled = await asyncio.gather(
                    request_task,
                    return_exceptions=True,
                )
                observed["cancelled"] = isinstance(
                    cancelled[0], asyncio.CancelledError
                )
                await asyncio.sleep(0)
                observed["drains_while_blocked"] = [
                    task.done() for task in drain_tasks
                ]
                observed["reservations_while_blocked"] = len(
                    adapter._run_admission_reservations
                )

                acquire_continue.set()
                await asyncio.gather(*drain_tasks)
                observed["reservations_after_drain"] = set(
                    adapter._run_admission_reservations
                )
                observed["lease_after_drain"] = db.get_managed_run_lease(
                    session_id
                )
        finally:
            acquire_continue.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if drain_tasks:
                await asyncio.gather(*drain_tasks, return_exceptions=True)
            db.close()

        assert observed["cancelled"]
        assert observed["drains_while_blocked"] == [False, False]
        assert observed["reservations_while_blocked"] == 1
        assert release_calls == []
        assert observed["reservations_after_drain"] == set()
        assert observed["lease_after_drain"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("release_outcome", ["false", "error"])
    async def test_cancelled_managed_lease_acquire_release_failure_is_fail_closed(
        self, adapter, tmp_path, release_outcome
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = f"session-cancelled-release-{release_outcome}"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        acquire_entered = threading.Event()
        acquire_continue = threading.Event()
        release_entered = threading.Event()
        release_continue = threading.Event()
        acquire_call = {}
        release_calls = []
        original_acquire = db.acquire_managed_run_lease
        original_release = db.release_managed_run_lease
        request_task = None
        drain_tasks = []

        def _blocked_acquire(
            lease_session_id,
            *,
            owner_id,
            run_id,
            lease_seconds,
        ):
            acquire_call.update({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            acquire_entered.set()
            acquire_continue.wait()
            return original_acquire(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
                lease_seconds=lease_seconds,
            )

        def _failed_release(lease_session_id, *, owner_id, run_id):
            release_calls.append({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            release_entered.set()
            release_continue.wait()
            if release_outcome == "error":
                raise sqlite3.OperationalError("release failed after cancellation")
            return False

        observed = {}
        try:
            with (
                patch.object(
                    db,
                    "acquire_managed_run_lease",
                    side_effect=_blocked_acquire,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_failed_release,
                ),
            ):
                request_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "cancel before failed exact release",
                            "session_id": session_id,
                        })
                    )
                )
                assert await asyncio.to_thread(acquire_entered.wait, 3.0)

                request_task.cancel()
                request_task.cancel()
                drain_tasks = [
                    asyncio.create_task(adapter.cancel_background_tasks()),
                    asyncio.create_task(adapter.cancel_background_tasks()),
                ]
                cancelled = await asyncio.gather(
                    request_task,
                    return_exceptions=True,
                )
                observed["cancelled"] = isinstance(
                    cancelled[0], asyncio.CancelledError
                )
                await asyncio.sleep(0)
                observed["drains_while_acquire_blocked"] = [
                    task.done() for task in drain_tasks
                ]

                acquire_continue.set()
                assert await asyncio.to_thread(release_entered.wait, 3.0)
                observed["drains_while_release_blocked"] = [
                    task.done() for task in drain_tasks
                ]
                observed["reservations_while_release_blocked"] = len(
                    adapter._run_admission_reservations
                )

                release_continue.set()
                await asyncio.gather(*drain_tasks)
                observed["reservations_after_drain"] = set(
                    adapter._run_admission_reservations
                )
                observed["lease_after_drain"] = db.get_managed_run_lease(
                    session_id
                )

            next_response = await adapter._handle_runs(
                _direct_run_request({
                    "input": "durable lease must remain fail closed",
                    "session_id": session_id,
                })
            )
            observed["next_status"] = next_response.status
            observed["next_code"] = json.loads(next_response.text)["error"][
                "code"
            ]
        finally:
            acquire_continue.set()
            release_continue.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if drain_tasks:
                await asyncio.gather(*drain_tasks, return_exceptions=True)
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                original_release(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        assert observed["cancelled"]
        assert observed["drains_while_acquire_blocked"] == [False, False]
        assert observed["drains_while_release_blocked"] == [False, False]
        assert observed["reservations_while_release_blocked"] == 1
        assert release_calls == [acquire_call]
        assert observed["reservations_after_drain"] == set()
        assert observed["lease_after_drain"] is not None
        assert observed["lease_after_drain"]["owner_id"] == acquire_call["owner_id"]
        assert observed["lease_after_drain"]["run_id"] == acquire_call["run_id"]
        assert observed["next_status"] == 409
        assert observed["next_code"] == "session_busy"

    @pytest.mark.asyncio
    async def test_shutdown_snapshot_before_request_cancel_still_drains_exact_release(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-shutdown-snapshot-before-cancel"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        acquire_entered = threading.Event()
        acquire_continue = threading.Event()
        release_entered = threading.Event()
        release_continue = threading.Event()
        first_shutdown_snapshot = asyncio.Event()
        acquire_call = {}
        release_calls = []
        original_acquire = db.acquire_managed_run_lease
        original_release = db.release_managed_run_lease
        request_task = None
        shutdown_task = None

        class _ObservedLifecycleSet(set):
            def __iter__(self):
                first_shutdown_snapshot.set()
                return super().__iter__()

        adapter._managed_lease_lifecycle_tasks = _ObservedLifecycleSet()

        def _blocked_acquire(
            lease_session_id,
            *,
            owner_id,
            run_id,
            lease_seconds,
        ):
            acquire_call.update({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            acquire_entered.set()
            acquire_continue.wait()
            return original_acquire(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
                lease_seconds=lease_seconds,
            )

        def _blocked_release(lease_session_id, *, owner_id, run_id):
            release_calls.append({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            release_entered.set()
            release_continue.wait()
            return original_release(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )

        observed = {}
        try:
            with (
                patch.object(
                    db,
                    "acquire_managed_run_lease",
                    side_effect=_blocked_acquire,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_blocked_release,
                ),
            ):
                request_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "shutdown snapshot before request cancel",
                            "session_id": session_id,
                        })
                    )
                )
                assert await asyncio.to_thread(acquire_entered.wait, 3.0)

                shutdown_task = asyncio.create_task(
                    adapter.cancel_background_tasks()
                )
                await asyncio.wait_for(first_shutdown_snapshot.wait(), 3.0)
                request_task.cancel()
                cancelled = await asyncio.gather(
                    request_task,
                    return_exceptions=True,
                )
                observed["cancelled"] = isinstance(
                    cancelled[0], asyncio.CancelledError
                )
                observed["shutdown_done_before_acquire"] = shutdown_task.done()
                observed["reservations_while_acquire_blocked"] = len(
                    adapter._run_admission_reservations
                )

                acquire_continue.set()
                assert await asyncio.to_thread(release_entered.wait, 3.0)
                observed["shutdown_done_before_release"] = shutdown_task.done()
                observed["reservations_while_release_blocked"] = len(
                    adapter._run_admission_reservations
                )

                release_continue.set()
                await shutdown_task
                await adapter.cancel_background_tasks()
                observed["reservations_after_shutdown"] = set(
                    adapter._run_admission_reservations
                )
                observed["lease_after_shutdown"] = db.get_managed_run_lease(
                    session_id
                )
        finally:
            acquire_continue.set()
            release_continue.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if shutdown_task is not None:
                await asyncio.gather(shutdown_task, return_exceptions=True)
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                original_release(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        assert observed["cancelled"]
        assert not observed["shutdown_done_before_acquire"]
        assert observed["reservations_while_acquire_blocked"] == 1
        assert not observed["shutdown_done_before_release"]
        assert observed["reservations_while_release_blocked"] == 1
        assert release_calls == [acquire_call]
        assert observed["reservations_after_shutdown"] == set()
        assert observed["lease_after_shutdown"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("acquire_outcome", ["true", "false", "error"])
    async def test_supervisor_creation_failure_keeps_request_as_exact_owner(
        self, adapter, tmp_path, acquire_outcome
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = f"session-request-owner-{acquire_outcome}"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        acquire_entered = threading.Event()
        acquire_continue = threading.Event()
        acquire_call = {}
        release_calls = []
        supervisor_coroutines = []
        original_create_owned_task = adapter._create_owned_task
        original_acquire = db.acquire_managed_run_lease
        original_release = db.release_managed_run_lease
        request_task = None
        shutdown_task = None

        def _blocked_acquire(
            lease_session_id,
            *,
            owner_id,
            run_id,
            lease_seconds,
        ):
            acquire_call.update({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            acquire_entered.set()
            acquire_continue.wait()
            if acquire_outcome == "error":
                raise sqlite3.OperationalError(
                    "acquire failed under request ownership"
                )
            if acquire_outcome == "false":
                return False
            return original_acquire(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
                lease_seconds=lease_seconds,
            )

        def _recording_release(lease_session_id, *, owner_id, run_id):
            release_calls.append({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            return original_release(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )

        def _fail_supervisor_creation(coroutine, *, name):
            if name.startswith("managed-run-lease-supervisor-"):
                supervisor_coroutines.append(coroutine)
                coroutine.close()
                raise RuntimeError("supervisor task creation unavailable")
            return original_create_owned_task(coroutine, name=name)

        observed = {}
        try:
            with (
                patch.object(
                    db,
                    "acquire_managed_run_lease",
                    side_effect=_blocked_acquire,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_recording_release,
                ),
                patch.object(
                    adapter,
                    "_create_owned_task",
                    side_effect=_fail_supervisor_creation,
                ),
            ):
                request_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "request must own failed handoff",
                            "session_id": session_id,
                        })
                    )
                )
                assert await asyncio.to_thread(acquire_entered.wait, 3.0)

                request_task.cancel()
                await asyncio.sleep(0)
                request_task.cancel()
                await asyncio.sleep(0)
                observed["request_done_while_acquire_blocked"] = (
                    request_task.done()
                )
                observed["reservations_while_acquire_blocked"] = len(
                    adapter._run_admission_reservations
                )

                shutdown_task = asyncio.create_task(
                    adapter.cancel_background_tasks()
                )
                await asyncio.sleep(0)
                observed["shutdown_done_while_acquire_blocked"] = (
                    shutdown_task.done()
                )

                acquire_continue.set()
                request_result = (
                    await asyncio.gather(
                        request_task,
                        return_exceptions=True,
                    )
                )[0]
                await asyncio.wait_for(shutdown_task, 3.0)
                observed["request_cancelled"] = isinstance(
                    request_result,
                    asyncio.CancelledError,
                )
                observed["reservations_after_cleanup"] = set(
                    adapter._run_admission_reservations
                )
                observed["lease_after_cleanup"] = db.get_managed_run_lease(
                    session_id
                )
        finally:
            acquire_continue.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
            if request_task is not None:
                await asyncio.gather(request_task, return_exceptions=True)
            if shutdown_task is not None:
                await asyncio.gather(shutdown_task, return_exceptions=True)
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                original_release(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        assert not observed["request_done_while_acquire_blocked"]
        assert observed["reservations_while_acquire_blocked"] == 1
        assert not observed["shutdown_done_while_acquire_blocked"]
        assert observed["request_cancelled"]
        assert len(supervisor_coroutines) == 1
        assert (
            inspect.getcoroutinestate(supervisor_coroutines[0])
            == "CORO_CLOSED"
        )
        if acquire_outcome == "true":
            assert release_calls == [acquire_call]
        else:
            assert release_calls == []
        assert observed["lease_after_cleanup"] is None
        assert observed["reservations_after_cleanup"] == set()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("acquire_outcome", "release_outcome"),
        [
            ("false", "unused"),
            ("error", "unused"),
            ("true", "true"),
            ("true", "false"),
            ("true", "error"),
        ],
    )
    async def test_supervisor_registration_failure_request_owns_exact_cleanup(
        self, adapter, tmp_path, acquire_outcome, release_outcome
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = (
            f"session-register-fallback-{acquire_outcome}-{release_outcome}"
        )
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        acquire_entered = threading.Event()
        acquire_continue = threading.Event()
        acquire_finished = threading.Event()
        acquire_call = {}
        release_calls = []
        original_acquire = db.acquire_managed_run_lease
        original_release = db.release_managed_run_lease
        request_task = None
        shutdown_task = None

        def _blocked_acquire(
            lease_session_id,
            *,
            owner_id,
            run_id,
            lease_seconds,
        ):
            acquire_call.update({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            acquire_entered.set()
            acquire_continue.wait()
            try:
                if acquire_outcome == "error":
                    raise sqlite3.OperationalError(
                        "acquire failed after registration failure"
                    )
                if acquire_outcome == "false":
                    return False
                return original_acquire(
                    lease_session_id,
                    owner_id=owner_id,
                    run_id=run_id,
                    lease_seconds=lease_seconds,
                )
            finally:
                acquire_finished.set()

        def _fallback_release(lease_session_id, *, owner_id, run_id):
            release_calls.append({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            if release_outcome == "error":
                raise sqlite3.OperationalError(
                    "release failed in registration fallback"
                )
            if release_outcome == "false":
                return False
            return original_release(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )

        observed = {}
        try:
            with (
                patch.object(
                    db,
                    "acquire_managed_run_lease",
                    side_effect=_blocked_acquire,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_fallback_release,
                ),
                patch.object(
                    adapter,
                    "_register_managed_lease_lifecycle",
                    side_effect=RuntimeError("supervisor registration failed"),
                ),
            ):
                request_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "force supervisor registration failure",
                            "session_id": session_id,
                        })
                    )
                )
                assert await asyncio.to_thread(acquire_entered.wait, 3.0)

                request_task.cancel()
                await asyncio.sleep(0)
                observed["request_pending_while_acquire_blocked"] = (
                    not request_task.done()
                )
                observed["reservations_while_acquire_blocked"] = len(
                    adapter._run_admission_reservations
                )

                shutdown_task = asyncio.create_task(
                    adapter.cancel_background_tasks()
                )
                acquire_continue.set()
                assert await asyncio.to_thread(acquire_finished.wait, 3.0)
                request_result = (
                    await asyncio.gather(
                        request_task,
                        return_exceptions=True,
                    )
                )[0]
                await asyncio.wait_for(shutdown_task, 3.0)
                observed["request_cancelled"] = isinstance(
                    request_result,
                    asyncio.CancelledError,
                )
                observed["reservations_after_fallback"] = set(
                    adapter._run_admission_reservations
                )
                observed["lease_after_fallback"] = db.get_managed_run_lease(
                    session_id
                )
        finally:
            acquire_continue.set()
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if shutdown_task is not None:
                await asyncio.gather(shutdown_task, return_exceptions=True)
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                original_release(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        assert observed["request_pending_while_acquire_blocked"]
        assert observed["request_cancelled"]
        assert observed["reservations_while_acquire_blocked"] == 1
        assert observed["reservations_after_fallback"] == set()
        if acquire_outcome == "true":
            assert release_calls == [acquire_call]
            if release_outcome == "true":
                assert observed["lease_after_fallback"] is None
            else:
                assert observed["lease_after_fallback"] is not None
                assert (
                    observed["lease_after_fallback"]["owner_id"]
                    == acquire_call["owner_id"]
                )
                assert (
                    observed["lease_after_fallback"]["run_id"]
                    == acquire_call["run_id"]
                )
        else:
            assert release_calls == []
            assert observed["lease_after_fallback"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("bare_stage", "acquire_outcome", "release_outcome"),
        [
            ("supervisor", "false", "unused"),
            ("supervisor", "error", "unused"),
            ("supervisor", "true", "true"),
            ("release", "true", "true"),
            ("release", "true", "false"),
            ("release", "true", "error"),
        ],
    )
    async def test_non_task_cleanup_result_has_one_owner_and_fails_closed(
        self,
        adapter,
        tmp_path,
        bare_stage,
        acquire_outcome,
        release_outcome,
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = (
            f"session-non-task-{bare_stage}-{acquire_outcome}-{release_outcome}"
        )
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        loop = asyncio.get_running_loop()
        previous_task_factory = loop.get_task_factory()
        acquire_entered = threading.Event()
        acquire_continue = threading.Event()
        acquire_finished = threading.Event()
        bare_created = asyncio.Event()
        release_factory_armed = [False]
        acquire_call = {}
        release_calls = []
        bare_results = []
        original_acquire = db.acquire_managed_run_lease
        original_release = db.release_managed_run_lease
        request_task = None
        shutdown_task = None

        def _delegate_task(factory_loop, coroutine, context):
            if previous_task_factory is not None:
                if context is None:
                    return previous_task_factory(factory_loop, coroutine)
                return previous_task_factory(
                    factory_loop,
                    coroutine,
                    context=context,
                )
            kwargs = {"loop": factory_loop}
            if context is not None:
                kwargs["context"] = context
            return asyncio.Task(coroutine, **kwargs)

        def _selective_task_factory(factory_loop, coroutine, context=None):
            coroutine_name = getattr(
                getattr(coroutine, "cr_code", None),
                "co_name",
                "",
            )
            use_bare_future = (
                bare_stage == "supervisor"
                and coroutine_name
                == "_supervise_cancelled_managed_lease_acquire"
            ) or (
                bare_stage == "release"
                and release_factory_armed[0]
                and coroutine_name == "to_thread"
            )
            if not use_bare_future:
                return _delegate_task(factory_loop, coroutine, context)
            release_factory_armed[0] = False
            bare_future = factory_loop.create_future()
            bare_results.append((bare_future, coroutine))
            bare_created.set()
            return bare_future

        def _blocked_acquire(
            lease_session_id,
            *,
            owner_id,
            run_id,
            lease_seconds,
        ):
            acquire_call.update({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            acquire_entered.set()
            acquire_continue.wait()
            try:
                if acquire_outcome == "error":
                    raise sqlite3.OperationalError(
                        "acquire failed after non-task result"
                    )
                if acquire_outcome == "false":
                    return False
                return original_acquire(
                    lease_session_id,
                    owner_id=owner_id,
                    run_id=run_id,
                    lease_seconds=lease_seconds,
                )
            finally:
                acquire_finished.set()

        def _release_exact(lease_session_id, *, owner_id, run_id):
            release_calls.append({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            if release_outcome == "error":
                raise sqlite3.OperationalError(
                    "release failed after non-task result"
                )
            if release_outcome == "false":
                return False
            return original_release(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )

        observed = {}
        loop.set_task_factory(_selective_task_factory)
        try:
            with (
                patch.object(
                    db,
                    "acquire_managed_run_lease",
                    side_effect=_blocked_acquire,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_release_exact,
                ),
            ):
                request_task = asyncio.create_task(
                    adapter._handle_runs(
                        _direct_run_request({
                            "input": "force non-task factory result",
                            "session_id": session_id,
                        })
                    )
                )
                assert await asyncio.to_thread(acquire_entered.wait, 3.0)

                request_task.cancel()
                await asyncio.sleep(0)
                observed["request_pending_while_acquire_blocked"] = (
                    not request_task.done()
                )

                shutdown_task = asyncio.create_task(
                    adapter.cancel_background_tasks()
                )
                if bare_stage == "release":
                    release_factory_armed[0] = True
                acquire_continue.set()
                await asyncio.wait_for(bare_created.wait(), 3.0)
                request_result = (
                    await asyncio.gather(
                        request_task,
                        return_exceptions=True,
                    )
                )[0]
                observed["request_cancelled"] = isinstance(
                    request_result,
                    asyncio.CancelledError,
                )

                deadline = loop.time() + 1.0
                while not shutdown_task.done() and loop.time() < deadline:
                    await asyncio.sleep(0.01)
                observed["shutdown_done"] = shutdown_task.done()
                observed["release_calls"] = list(release_calls)
                observed["reservations"] = set(
                    adapter._run_admission_reservations
                )
                observed["lifecycle_count"] = len(
                    adapter._managed_lease_lifecycle_tasks
                )
                observed["lease"] = db.get_managed_run_lease(session_id)
                observed["bare_cancelled"] = all(
                    future.cancelled()
                    for future, _coroutine in bare_results
                )
                observed["coroutines_closed"] = all(
                    coroutine.cr_frame is None
                    for _future, coroutine in bare_results
                )
        finally:
            loop.set_task_factory(previous_task_factory)
            acquire_continue.set()
            for future, coroutine in bare_results:
                future.cancel()
                coroutine.close()
            if acquire_entered.is_set() and not acquire_finished.is_set():
                await asyncio.to_thread(acquire_finished.wait, 3.0)
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            for lifecycle in tuple(adapter._managed_lease_lifecycle_tasks):
                acquire_task = lifecycle.acquire_task
                if acquire_task is not None and acquire_task.done():
                    try:
                        acquire_task.result()
                    except BaseException:
                        pass
                adapter._finish_managed_lease_lifecycle(
                    lifecycle,
                    release_admission=True,
                )
            if shutdown_task is not None:
                await asyncio.gather(shutdown_task, return_exceptions=True)
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                original_release(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        if bare_stage == "supervisor":
            assert observed["request_pending_while_acquire_blocked"]
        assert observed["request_cancelled"]
        assert observed["shutdown_done"]
        assert observed["bare_cancelled"]
        assert observed["coroutines_closed"]
        assert observed["reservations"] == set()
        assert observed["lifecycle_count"] == 0
        if acquire_outcome == "true":
            assert observed["release_calls"] == [acquire_call]
            if release_outcome == "true":
                assert observed["lease"] is None
            else:
                assert observed["lease"] is not None
                assert observed["lease"]["owner_id"] == acquire_call["owner_id"]
                assert observed["lease"]["run_id"] == acquire_call["run_id"]
        else:
            assert observed["release_calls"] == []
            assert observed["lease"] is None

    @pytest.mark.asyncio
    async def test_unconsumed_stream_capacity_is_bounded_independently(
        self, adapter
    ):
        adapter._MAX_RETAINED_RUN_STREAMS = 1
        adapter._run_streams["unconsumed-run"] = asyncio.Queue()

        with patch.object(adapter, "_create_agent") as create_agent:
            app = _create_runs_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                payload = await resp.json()

        assert resp.status == 429
        assert payload["error"]["code"] == "run_stream_capacity_exceeded"
        create_agent.assert_not_called()
        await asyncio.sleep(0)
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_direct_handler_releases_admission_when_managed_db_is_unavailable(
        self, adapter
    ):
        request = _direct_run_request({
            "input": "hello",
            "session_id": "session-db-unavailable-direct",
        })

        with patch.object(adapter, "_ensure_session_db", return_value=None):
            response = await adapter._handle_runs(request)

        payload = json.loads(response.text)
        assert response.status == 503
        assert payload["error"]["code"] == "session_db_unavailable"
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_direct_handler_releases_admission_when_managed_lease_is_busy(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-busy-direct", "api_server")
        adapter._session_db = db
        request = _direct_run_request({
            "input": "hello",
            "session_id": "session-busy-direct",
        })

        try:
            with patch.object(
                db,
                "acquire_managed_run_lease",
                return_value=False,
            ):
                response = await adapter._handle_runs(request)

            payload = json.loads(response.text)
            assert response.status == 409
            assert payload["error"]["code"] == "session_busy"
            assert adapter._run_admission_reservations == set()
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_initial_lease_non_task_factory_result_is_503_and_unowned(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-non-task-initial-acquire"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task
        captured = {}
        bare_future = loop.create_future()

        def _create_task(coroutine, *args, **kwargs):
            coroutine_frame = getattr(coroutine, "cr_frame", None)
            to_thread_target = (
                coroutine_frame.f_locals.get("func")
                if coroutine_frame is not None
                else None
            )
            if (
                getattr(to_thread_target, "__name__", "")
                == "acquire_managed_run_lease"
            ):
                captured["coroutine"] = coroutine
                loop.call_soon(
                    lambda: (
                        None
                        if bare_future.done()
                        else bare_future.set_result(False)
                    )
                )
                return bare_future
            return original_create_task(coroutine, *args, **kwargs)

        try:
            with (
                patch.object(loop, "create_task", side_effect=_create_task),
                patch.object(adapter, "_create_agent") as create_agent,
            ):
                response = await adapter._handle_runs(
                    _direct_run_request({
                        "input": "reject a non-task initial acquire",
                        "session_id": session_id,
                    })
                )

            payload = json.loads(response.text)
            assert response.status == 503
            assert payload["error"]["code"] == "session_lease_unavailable"
            create_agent.assert_not_called()
            assert bare_future.cancelled()
            assert (
                inspect.getcoroutinestate(captured["coroutine"])
                == "CORO_CLOSED"
            )
            assert db.get_managed_run_lease(session_id) is None
            assert adapter._managed_lease_lifecycle_tasks == set()
            assert adapter._run_admission_reservations == set()
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_initial_lease_name_failure_keeps_real_task_ownership(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-real-task-name-failure"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        adapter._MAX_RETAINED_RUN_STREAMS = 0
        loop = asyncio.get_running_loop()
        previous_task_factory = loop.get_task_factory()
        lease_tasks = []
        set_name_attempts = []

        class _SetNameFailingTask(asyncio.Task):
            def set_name(self, value):
                set_name_attempts.append(value)
                raise RuntimeError("task naming unavailable")

        def _delegate_task(factory_loop, coroutine, context):
            if previous_task_factory is not None:
                if context is None:
                    return previous_task_factory(factory_loop, coroutine)
                return previous_task_factory(
                    factory_loop,
                    coroutine,
                    context=context,
                )
            kwargs = {"loop": factory_loop}
            if context is not None:
                kwargs["context"] = context
            return asyncio.Task(coroutine, **kwargs)

        def _selective_task_factory(factory_loop, coroutine, context=None):
            coroutine_frame = getattr(coroutine, "cr_frame", None)
            to_thread_target = (
                coroutine_frame.f_locals.get("func")
                if coroutine_frame is not None
                else None
            )
            if (
                getattr(to_thread_target, "__name__", "")
                == "acquire_managed_run_lease"
            ):
                kwargs = {"loop": factory_loop}
                if context is not None:
                    kwargs["context"] = context
                task = _SetNameFailingTask(coroutine, **kwargs)
                lease_tasks.append(task)
                return task
            return _delegate_task(factory_loop, coroutine, context)

        loop.set_task_factory(_selective_task_factory)
        try:
            with patch.object(adapter, "_create_agent") as create_agent:
                response = await adapter._handle_runs(
                    _direct_run_request({
                        "input": "keep the real acquire owner",
                        "session_id": session_id,
                    })
                )

            payload = json.loads(response.text)
            assert response.status == 429
            assert payload["error"]["code"] == "run_stream_capacity_exceeded"
            create_agent.assert_not_called()
            assert len(lease_tasks) == 1
            assert lease_tasks[0].done()
            assert lease_tasks[0].result() is True
            assert len(set_name_attempts) == 1
            assert set_name_attempts[0].startswith(
                "managed-run-lease-acquire-"
            )
            assert db.get_managed_run_lease(session_id) is None
            assert adapter._run_admission_reservations == set()
        finally:
            loop.set_task_factory(previous_task_factory)
            db.close()

    @pytest.mark.asyncio
    async def test_direct_handler_releases_admission_after_managed_history_failure(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-history-direct", "api_server")
        adapter._session_db = db
        request = _direct_run_request({
            "input": "hello",
            "session_id": "session-history-direct",
        })

        try:
            with patch.object(
                db,
                "get_messages_as_conversation",
                side_effect=sqlite3.OperationalError("database is locked"),
            ):
                response = await adapter._handle_runs(request)

            payload = json.loads(response.text)
            assert response.status == 503
            assert payload["error"]["code"] == "session_db_unavailable"
            assert db.get_managed_run_lease("session-history-direct") is None
            assert adapter._run_admission_reservations == set()
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_direct_handler_releases_admission_after_managed_stream_cap(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-stream-cap-direct", "api_server")
        adapter._session_db = db
        adapter._MAX_RETAINED_RUN_STREAMS = 0
        request = _direct_run_request({
            "input": "hello",
            "session_id": "session-stream-cap-direct",
        })

        try:
            response = await adapter._handle_runs(request)

            payload = json.loads(response.text)
            assert response.status == 429
            assert payload["error"]["code"] == "run_stream_capacity_exceeded"
            assert db.get_managed_run_lease("session-stream-cap-direct") is None
            assert adapter._run_admission_reservations == set()
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_direct_handler_releases_admission_without_a_current_task(
        self, adapter
    ):
        adapter._MAX_RETAINED_RUN_STREAMS = 0
        request = _direct_run_request({"input": "hello"})

        with patch(
            "gateway.platforms.api_server.asyncio.current_task",
            return_value=None,
        ):
            response = await adapter._handle_runs(request)

        payload = json.loads(response.text)
        assert response.status == 429
        assert payload["error"]["code"] == "run_stream_capacity_exceeded"
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_non_task_executor_result_rolls_back_managed_run_start(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-non-task-executor", "api_server")
        adapter._session_db = db
        adapter._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.01
        request = _direct_run_request({
            "input": "hello",
            "session_id": "session-non-task-executor",
        })
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task
        captured = {}

        def _return_non_task_for_worker(coroutine, *args, **kwargs):
            coroutine_name = getattr(
                getattr(coroutine, "cr_code", None),
                "co_name",
                "",
            )
            if coroutine_name == "_run_and_close":
                captured["coroutine"] = coroutine
                return object()
            return original_create_task(coroutine, *args, **kwargs)

        try:
            with (
                patch.object(
                    loop,
                    "create_task",
                    side_effect=_return_non_task_for_worker,
                ),
                patch.object(adapter, "_create_agent") as create_agent,
            ):
                response = await adapter._handle_runs(request)

            payload = json.loads(response.text)
            assert response.status == 503
            assert payload["error"]["code"] == "run_executor_unavailable"
            create_agent.assert_not_called()
            assert inspect.getcoroutinestate(captured["coroutine"]) == "CORO_CLOSED"
            assert db.get_managed_run_lease("session-non-task-executor") is None
            assert adapter._managed_session_runs == {}
            assert adapter._run_streams == {}
            assert adapter._run_statuses == {}
            assert adapter._run_admission_reservations == set()
        finally:
            coroutine = captured.get("coroutine")
            if (
                coroutine is not None
                and inspect.getcoroutinestate(coroutine) != "CORO_CLOSED"
            ):
                coroutine.close()
            lease = db.get_managed_run_lease("session-non-task-executor")
            if lease is not None:
                db.release_managed_run_lease(
                    "session-non-task-executor",
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

    @pytest.mark.asyncio
    async def test_pending_bare_future_rolls_back_managed_run_start(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-bare-future-executor", "api_server")
        adapter._session_db = db
        adapter._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.01
        request = _direct_run_request({
            "input": "hello",
            "session_id": "session-bare-future-executor",
        })
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task
        pending_future = loop.create_future()
        captured = {}
        observed = {}

        def _return_bare_future_for_worker(coroutine, *args, **kwargs):
            coroutine_name = getattr(
                getattr(coroutine, "cr_code", None),
                "co_name",
                "",
            )
            if coroutine_name == "_run_and_close":
                captured["coroutine"] = coroutine
                return pending_future
            return original_create_task(coroutine, *args, **kwargs)

        try:
            with (
                patch.object(
                    loop,
                    "create_task",
                    side_effect=_return_bare_future_for_worker,
                ),
                patch.object(adapter, "_create_agent") as create_agent,
            ):
                response = await adapter._handle_runs(request)

            observed = {
                "payload": json.loads(response.text),
                "status": response.status,
                "coroutine_state": inspect.getcoroutinestate(captured["coroutine"]),
                "lease": db.get_managed_run_lease("session-bare-future-executor"),
                "managed_session_runs": dict(adapter._managed_session_runs),
                "run_streams": dict(adapter._run_streams),
                "run_statuses": dict(adapter._run_statuses),
                "reservations": set(adapter._run_admission_reservations),
            }
        finally:
            if not pending_future.done():
                pending_future.cancel()
                await asyncio.sleep(0)
            coroutine = captured.get("coroutine")
            if (
                coroutine is not None
                and inspect.getcoroutinestate(coroutine) != "CORO_CLOSED"
            ):
                coroutine.close()
            lease = db.get_managed_run_lease("session-bare-future-executor")
            if lease is not None:
                db.release_managed_run_lease(
                    "session-bare-future-executor",
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        assert observed["status"] == 503
        assert observed["payload"]["error"]["code"] == "run_executor_unavailable"
        create_agent.assert_not_called()
        assert observed["coroutine_state"] == "CORO_CLOSED"
        assert observed["lease"] is None
        assert observed["managed_session_runs"] == {}
        assert observed["run_streams"] == {}
        assert observed["run_statuses"] == {}
        assert observed["reservations"] == set()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("managed", "failed_callback"),
        [
            (False, 1),
            (True, 1),
        ],
    )
    async def test_worker_callback_failure_rolls_back_real_task_start(
        self,
        adapter,
        tmp_path,
        managed,
        failed_callback,
    ):
        loop = asyncio.get_running_loop()
        previous_task_factory = loop.get_task_factory()
        session_id = (
            f"session-worker-callback-{failed_callback}"
            if managed
            else ""
        )
        db = None
        if managed:
            db = SessionDB(db_path=tmp_path / "state.db")
            db.create_session(session_id, "api_server")
            adapter._session_db = db
        callback_attempts = []
        worker_tasks = []
        admission_release_calls = []
        original_release_admission = adapter._release_run_admission
        agent_started = threading.Event()
        agent_continue = threading.Event()

        class _WorkerCallbackFailingTask(asyncio.Task):
            def __init__(self, coroutine, *, task_loop, context):
                kwargs = {"loop": task_loop}
                if context is not None:
                    kwargs["context"] = context
                super().__init__(coroutine, **kwargs)
                self._callback_count = 0
                self._callback_failed = False

            def add_done_callback(self, callback, *, context=None):
                self._callback_count += 1
                callback_attempts.append(callback)
                if (
                    not self._callback_failed
                    and self._callback_count == failed_callback
                ):
                    self._callback_failed = True
                    raise RuntimeError(
                        f"worker callback {failed_callback} failed"
                    )
                if context is None:
                    return super().add_done_callback(callback)
                return super().add_done_callback(
                    callback,
                    context=context,
                )

        def _delegate_task(factory_loop, coroutine, context):
            if previous_task_factory is not None:
                if context is None:
                    return previous_task_factory(factory_loop, coroutine)
                return previous_task_factory(
                    factory_loop,
                    coroutine,
                    context=context,
                )
            kwargs = {"loop": factory_loop}
            if context is not None:
                kwargs["context"] = context
            return asyncio.Task(coroutine, **kwargs)

        def _selective_task_factory(factory_loop, coroutine, context=None):
            coroutine_name = getattr(
                getattr(coroutine, "cr_code", None),
                "co_name",
                "",
            )
            if coroutine_name == "_run_and_close":
                task = _WorkerCallbackFailingTask(
                    coroutine,
                    task_loop=factory_loop,
                    context=context,
                )
                worker_tasks.append(task)
                return task
            return _delegate_task(factory_loop, coroutine, context)

        def _blocking_run(**_kwargs):
            agent_started.set()
            agent_continue.wait()
            return {"final_response": "unexpected worker execution"}

        def _record_admission_release(token):
            admission_release_calls.append(token)
            original_release_admission(token)

        agent = MagicMock()
        agent.run_conversation.side_effect = _blocking_run
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0
        request_body = {"input": "callback registration must be atomic"}
        if managed:
            request_body["session_id"] = session_id
        result = None
        observed = {}
        loop.set_task_factory(_selective_task_factory)
        try:
            with (
                patch.object(
                    adapter,
                    "_create_agent",
                    return_value=agent,
                ) as create_agent,
                patch.object(
                    adapter,
                    "_release_run_admission",
                    side_effect=_record_admission_release,
                ),
            ):
                result = (
                    await asyncio.gather(
                        adapter._handle_runs(
                            _direct_run_request(request_body)
                        ),
                        return_exceptions=True,
                    )
                )[0]
                worker_ran = await asyncio.to_thread(
                    agent_started.wait,
                    0.2,
                )
                observed = {
                    "active_tasks": dict(adapter._active_run_tasks),
                    "background_tasks": set(adapter._background_tasks),
                    "lease": (
                        db.get_managed_run_lease(session_id)
                        if db is not None
                        else None
                    ),
                    "managed_session_runs": dict(
                        adapter._managed_session_runs
                    ),
                    "reservations": set(
                        adapter._run_admission_reservations
                    ),
                    "run_statuses": dict(adapter._run_statuses),
                    "run_streams": dict(adapter._run_streams),
                    "worker_ran": worker_ran,
                    "admission_release_count": len(
                        admission_release_calls
                    ),
                }
        finally:
            loop.set_task_factory(previous_task_factory)
            agent_continue.set()
            for worker_task in worker_tasks:
                if not worker_task.done():
                    worker_task.cancel()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            await adapter.cancel_background_tasks()
            if db is not None:
                lease = db.get_managed_run_lease(session_id)
                if lease is not None:
                    db.release_managed_run_lease(
                        session_id,
                        owner_id=lease["owner_id"],
                        run_id=lease["run_id"],
                    )
                db.close()

        assert getattr(result, "status", None) == 503
        assert json.loads(result.text)["error"]["code"] == (
            "run_executor_unavailable"
        )
        expected_callbacks = ["_reconcile_worker_terminal"]
        assert [
            getattr(callback, "__name__", "")
            for callback in callback_attempts[:failed_callback]
        ] == expected_callbacks[:failed_callback]
        create_agent.assert_not_called()
        assert observed == {
            "active_tasks": {},
            "background_tasks": set(),
            "lease": None,
            "managed_session_runs": {},
            "reservations": set(),
            "run_statuses": {},
            "run_streams": {},
            "worker_ran": False,
            "admission_release_count": 1,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("managed", [False, True])
    async def test_cancel_before_worker_first_step_reconciles_terminal_state(
        self, adapter, tmp_path, managed
    ):
        db = None
        session_id = ""
        if managed:
            session_id = "session-cancel-before-worker-first-step"
            db = SessionDB(db_path=tmp_path / "state.db")
            db.create_session(session_id, "api_server")
            adapter._session_db = db

        request_body = {"input": "cancel before worker first step"}
        if managed:
            request_body["session_id"] = session_id

        shutdown_tasks = []
        task = None
        try:
            with patch.object(adapter, "_create_agent") as create_agent:
                response = await adapter._handle_runs(
                    _direct_run_request(request_body)
                )
                payload = json.loads(response.text)
                assert response.status == 202, payload
                run_id = payload["run_id"]
                task = adapter._active_run_tasks[run_id]
                queue = adapter._run_streams[run_id]
                assert (
                    inspect.getcoroutinestate(task.get_coro())
                    == "CORO_CREATED"
                )

                task.cancel()
                task.cancel()
                shutdown_tasks = [
                    asyncio.create_task(adapter.cancel_background_tasks()),
                    asyncio.create_task(adapter.cancel_background_tasks()),
                ]
                task_result = (
                    await asyncio.gather(
                        task,
                        return_exceptions=True,
                    )
                )[0]
                await asyncio.gather(*shutdown_tasks)

            queued_events = []
            while not queue.empty():
                queued_events.append(queue.get_nowait())

            assert isinstance(task_result, asyncio.CancelledError)
            create_agent.assert_not_called()
            assert adapter._run_statuses[run_id]["status"] == "cancelled"
            assert (
                adapter._run_statuses[run_id]["last_event"]
                == "run.cancelled"
            )
            assert any(
                isinstance(event, dict)
                and event.get("event") == "run.cancelled"
                for event in queued_events
            )
            assert queued_events[-1] is None
            assert run_id not in adapter._active_run_tasks
            assert run_id not in adapter._active_run_agents
            assert run_id not in adapter._run_approval_sessions
            assert task not in adapter._background_tasks
            assert adapter._run_admission_reservations == set()
            if managed:
                assert adapter._managed_session_runs == {}
                assert db.get_managed_run_lease(session_id) is None
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if shutdown_tasks:
                await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            if db is not None:
                lease = db.get_managed_run_lease(session_id)
                if lease is not None:
                    db.release_managed_run_lease(
                        session_id,
                        owner_id=lease["owner_id"],
                        run_id=lease["run_id"],
                    )
                db.close()

    @pytest.mark.asyncio
    async def test_prestart_release_non_task_result_keeps_exact_owner(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-prestart-release-non-task"
        db.create_session(session_id, "api_server")
        adapter._session_db = db
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task
        original_release = db.release_managed_run_lease
        bare_future = loop.create_future()
        release_entered = threading.Event()
        release_continue = threading.Event()
        release_calls = []
        captured = {}
        task = None
        shutdown_task = None

        def _selective_create_task(coroutine, *args, **kwargs):
            coroutine_name = getattr(
                getattr(coroutine, "cr_code", None),
                "co_name",
                "",
            )
            if coroutine_name == "_release_prestart_and_reconcile":
                captured["release_coroutine"] = coroutine
                return bare_future
            return original_create_task(coroutine, *args, **kwargs)

        def _blocked_release(lease_session_id, *, owner_id, run_id):
            release_calls.append({
                "session_id": lease_session_id,
                "owner_id": owner_id,
                "run_id": run_id,
            })
            release_entered.set()
            release_continue.wait()
            return original_release(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )

        try:
            with (
                patch.object(
                    loop,
                    "create_task",
                    side_effect=_selective_create_task,
                ),
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_blocked_release,
                ),
                patch.object(adapter, "_create_agent") as create_agent,
            ):
                response = await adapter._handle_runs(
                    _direct_run_request({
                        "input": "cancel before managed worker first step",
                        "session_id": session_id,
                    })
                )
                payload = json.loads(response.text)
                assert response.status == 202, payload
                run_id = payload["run_id"]
                task = adapter._active_run_tasks[run_id]
                assert (
                    inspect.getcoroutinestate(task.get_coro())
                    == "CORO_CREATED"
                )

                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                assert await asyncio.to_thread(
                    release_entered.wait,
                    3.0,
                )

                shutdown_task = asyncio.create_task(
                    adapter.cancel_background_tasks()
                )
                await asyncio.sleep(0.05)

                assert not shutdown_task.done()
                assert len(adapter._run_admission_reservations) == 1
                assert db.get_managed_run_lease(session_id) is not None

                release_continue.set()
                await shutdown_task

            queued_events = []
            queue = adapter._run_streams[run_id]
            while not queue.empty():
                queued_events.append(queue.get_nowait())

            create_agent.assert_not_called()
            assert bare_future.cancelled()
            assert (
                inspect.getcoroutinestate(captured["release_coroutine"])
                == "CORO_CLOSED"
            )
            assert len(release_calls) == 1
            assert db.get_managed_run_lease(session_id) is None
            assert adapter._managed_session_runs == {}
            assert adapter._managed_lease_lifecycle_tasks == set()
            assert adapter._run_admission_reservations == set()
            assert adapter._run_statuses[run_id]["status"] == "cancelled"
            assert queued_events[-1] is None
        finally:
            release_continue.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if shutdown_task is not None:
                await asyncio.gather(
                    shutdown_task,
                    return_exceptions=True,
                )
            lease = db.get_managed_run_lease(session_id)
            if lease is not None:
                original_release(
                    session_id,
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

    @pytest.mark.asyncio
    async def test_eager_terminal_worker_is_reconciled_after_outer_map_insert(
        self, adapter
    ):
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task

        async def _already_terminal():
            return None

        eager_task = original_create_task(_already_terminal())
        await eager_task
        captured = {}

        def _create_task(coroutine, *args, **kwargs):
            if (
                getattr(
                    getattr(coroutine, "cr_code", None),
                    "co_name",
                    "",
                )
                == "_run_and_close"
            ):
                captured["worker_coroutine"] = coroutine
                coroutine.close()
                return eager_task
            return original_create_task(coroutine, *args, **kwargs)

        with (
            patch.object(loop, "create_task", side_effect=_create_task),
            patch.object(adapter, "_create_agent") as create_agent,
        ):
            response = await adapter._handle_runs(
                _direct_run_request({"input": "eager terminal worker"})
            )
            payload = json.loads(response.text)
            await asyncio.sleep(0)

        run_id = payload["run_id"]
        queue = adapter._run_streams[run_id]
        queued_events = []
        while not queue.empty():
            queued_events.append(queue.get_nowait())

        assert response.status == 202
        create_agent.assert_not_called()
        assert (
            inspect.getcoroutinestate(captured["worker_coroutine"])
            == "CORO_CLOSED"
        )
        assert adapter._run_statuses[run_id]["status"] == "cancelled"
        assert run_id not in adapter._active_run_tasks
        assert run_id not in adapter._active_run_agents
        assert run_id not in adapter._run_approval_sessions
        assert eager_task not in adapter._background_tasks
        assert adapter._run_admission_reservations == set()
        assert any(
            isinstance(event, dict)
            and event.get("event") == "run.cancelled"
            for event in queued_events
        )
        assert queued_events[-1] is None

    @pytest.mark.asyncio
    async def test_real_credential_pool_route_is_admitted_for_managed_run(
        self, adapter, tmp_path, monkeypatch
    ):
        from agent.credential_pool import CredentialPool, PooledCredential
        from hermes_cli import runtime_provider

        pool = CredentialPool(
            "anthropic",
            [
                PooledCredential.from_dict(
                    "anthropic",
                    {
                        "id": "managed-run-pool",
                        "label": "managed run pool",
                        "auth_type": "api_key",
                        "priority": 0,
                        "source": "manual",
                        "access_token": "pool-secret",
                        "base_url": "https://api.anthropic.com",
                    },
                )
            ],
        )
        monkeypatch.setattr(runtime_provider, "load_pool", lambda provider: pool)
        monkeypatch.setattr(
            runtime_provider,
            "_get_model_config",
            lambda: {
                "provider": "anthropic",
                "default": "claude-sonnet-4-6",
            },
        )

        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-real-pool", "api_server")
        adapter._session_db = db
        finished = threading.Event()
        agent = MagicMock()
        agent.run_conversation.side_effect = lambda **_kwargs: (
            finished.set() or {"final_response": "done"}
        )
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        try:
            with patch.object(
                adapter,
                "_create_agent",
                return_value=agent,
            ) as create_agent:
                app = _create_runs_app(adapter)
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-real-pool",
                            "model": "claude-sonnet-4-6",
                            "provider": "anthropic",
                        },
                    )
                    payload = await response.json()
                    assert await asyncio.to_thread(finished.wait, 3.0)

            assert response.status == 202, payload
            resolved_route = create_agent.call_args.kwargs["resolved_route"]
            assert resolved_route["runtime_kwargs"]["api_key"] == "pool-secret"
            assert resolved_route["runtime_kwargs"]["credential_pool"] is pool
        finally:
            db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "value", "expected_code"),
        [
            ("model", 7, "invalid_model"),
            ("model", "   ", "invalid_model"),
            ("model", "bad\nmodel", "invalid_model"),
            ("provider", ["openrouter"], "invalid_provider"),
            ("provider", "   ", "invalid_provider"),
            ("provider", "bad\x00provider", "invalid_provider"),
        ],
    )
    async def test_start_rejects_invalid_route_selection_before_side_effects(
        self, adapter, field, value, expected_code
    ):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as create_agent:
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello", field: value},
                )
                payload = await resp.json()

        assert resp.status == 400
        assert payload["error"]["code"] == expected_code
        assert payload["error"]["param"] == field
        create_agent.assert_not_called()
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_start_rejects_unresolvable_explicit_route_before_run_allocation(
        self, adapter, monkeypatch
    ):
        def fail_resolution(**kwargs):
            raise RuntimeError("credential details must stay private")

        monkeypatch.setattr(
            "gateway.run._resolve_runtime_agent_kwargs",
            fail_resolution,
        )
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as create_agent:
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "model": "requested/model",
                        "provider": "requested-provider",
                    },
                )
                payload = await resp.json()

        assert resp.status == 400
        assert payload["error"] == {
            "message": "Requested model/provider could not be resolved from configured credentials.",
            "type": "invalid_request_error",
            "param": None,
            "code": "model_configuration_error",
        }
        create_agent.assert_not_called()
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_start_invalid_json_returns_400(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_start_missing_input_returns_400(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs", json={"model": "test"})
            assert resp.status == 400
            data = await resp.json()
            assert "input" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_start_empty_input_returns_400(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs", json={"input": ""})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_start_invalid_history_does_not_allocate_run(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={"input": "hello", "conversation_history": {"role": "user"}},
            )
        assert resp.status == 400
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}

    @pytest.mark.asyncio
    async def test_start_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs", json={"input": "hello"})
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_start_with_valid_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(auth_adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "ok"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello"},
                    headers={"Authorization": "Bearer sk-secret"},
                )
                assert resp.status == 202

    @pytest.mark.asyncio
    async def test_header_and_body_session_id_conflict_returns_400(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={"input": "hello", "session_id": "session-body"},
                headers={"X-Hermes-Session-Id": "session-header"},
            )
            payload = await resp.json()

        assert resp.status == 400
        assert payload["error"]["code"] == "session_id_conflict"
        assert adapter._run_streams == {}

    @pytest.mark.asyncio
    async def test_unknown_managed_session_returns_404_without_allocating_run(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={"input": "hello", "session_id": "session-missing"},
            )
            payload = await resp.json()

        assert resp.status == 404
        assert payload["error"]["code"] == "session_not_found"
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}

    @pytest.mark.asyncio
    async def test_managed_session_loads_state_db_history_and_echoes_session_id(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-known", "api_server")
        db.append_message("session-known", "user", "first question")
        db.append_message("session-known", "assistant", "first answer")
        adapter._session_db = db
        app = _create_runs_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "second answer"}
                mock_agent.session_prompt_tokens = 4
                mock_agent.session_completion_tokens = 2
                mock_agent.session_total_tokens = 6
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "second question", "session_id": "session-known"},
                )
                payload = await resp.json()
                for _ in range(40):
                    if mock_agent.run_conversation.called:
                        break
                    await asyncio.sleep(0.025)

        assert resp.status == 202
        assert payload["session_id"] == "session-known"
        assert resp.headers["X-Hermes-Session-Id"] == "session-known"
        assert mock_agent.run_conversation.call_args.kwargs["conversation_history"] == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ]
        db.close()

    @pytest.mark.asyncio
    async def test_managed_current_checkpoint_reaches_provider_once_and_db_stays_exact_once(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-current-checkpoint"
        platform_message_id = "webui-turn:turn-current"
        canary = "managed-current-turn-canary"
        db.create_session(session_id, "webui")
        db.append_message(session_id, "user", "earlier question")
        db.append_message(session_id, "assistant", "earlier answer")
        db.append_message(
            session_id,
            "user",
            [{"type": "text", "text": canary}],
            platform_message_id=platform_message_id,
        )
        adapter._session_db = db
        app = _create_runs_app(adapter)
        provider_payloads = []
        finished = threading.Event()

        class RecordingAgent:
            session_prompt_tokens = 0
            session_completion_tokens = 0
            session_total_tokens = 0

            def run_conversation(
                self,
                *,
                user_message,
                conversation_history,
                task_id,
                persist_user_platform_message_id=None,
            ):
                provider_payloads.append([
                    *conversation_history,
                    {"role": "user", "content": user_message},
                ])
                db.append_message(
                    task_id,
                    "user",
                    user_message,
                    platform_message_id=persist_user_platform_message_id,
                )
                db.append_message(task_id, "assistant", "provider answer")
                finished.set()
                return {"final_response": "provider answer"}

        try:
            async with TestClient(TestServer(app)) as cli:
                with patch.object(adapter, "_create_agent", return_value=RecordingAgent()):
                    resp = await cli.post(
                        "/v1/runs",
                        json={
                            "input": [{"type": "input_text", "text": canary}],
                            "session_id": session_id,
                            "platform_message_id": platform_message_id,
                        },
                    )
                    payload = await resp.json()
                    assert await asyncio.to_thread(finished.wait, 3.0)

            assert resp.status == 202, payload
            assert len(provider_payloads) == 1
            assert json.dumps(provider_payloads[0], ensure_ascii=False).count(canary) == 1
            assert provider_payloads[0][:-1] == [
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
            ]
            rows = db.get_messages(session_id)
            checkpoint_rows = [
                row
                for row in rows
                if row.get("platform_message_id") == platform_message_id
            ]
            assert len(checkpoint_rows) == 1
            assert [row["role"] for row in rows] == [
                "user",
                "assistant",
                "user",
                "assistant",
            ]
            assert db.get_session(session_id)["message_count"] == 4
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_managed_current_checkpoint_same_id_different_content_fails_closed(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-checkpoint-conflict"
        platform_message_id = "webui-turn:turn-conflict"
        db.create_session(session_id, "webui")
        db.append_message(
            session_id,
            "user",
            "accepted checkpoint",
            platform_message_id=platform_message_id,
        )
        adapter._session_db = db
        app = _create_runs_app(adapter)

        try:
            async with TestClient(TestServer(app)) as cli:
                with patch.object(adapter, "_create_agent") as mock_create:
                    resp = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "different payload",
                            "session_id": session_id,
                            "platform_message_id": platform_message_id,
                        },
                    )
                    payload = await resp.json()

            assert resp.status == 409
            assert payload["error"]["code"] == "platform_message_conflict"
            mock_create.assert_not_called()
            assert adapter._run_streams == {}
            assert [
                (row["role"], row["content"], row["platform_message_id"])
                for row in db.get_messages(session_id)
            ] == [("user", "accepted checkpoint", platform_message_id)]
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_managed_display_checkpoint_allows_distinct_prepared_image_input_once(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-display-checkpoint-image-input"
        platform_message_id = "webui-turn:turn-image"
        display_checkpoint = "visible display checkpoint canary"
        prepared_canary = "prepared provider image canary"
        prepared_input = [
            {"type": "input_text", "text": prepared_canary},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,AA==",
            },
        ]
        db.create_session(session_id, "webui")
        db.append_message(session_id, "user", "earlier question")
        db.append_message(session_id, "assistant", "earlier answer")
        db.append_message(
            session_id,
            "user",
            display_checkpoint,
            platform_message_id=platform_message_id,
        )
        adapter._session_db = db
        app = _create_runs_app(adapter)
        provider_payloads = []
        finished = threading.Event()

        class RecordingAgent:
            session_prompt_tokens = 0
            session_completion_tokens = 0
            session_total_tokens = 0

            def run_conversation(
                self,
                *,
                user_message,
                conversation_history,
                task_id,
                persist_user_platform_message_id=None,
            ):
                provider_payloads.append([
                    *conversation_history,
                    {"role": "user", "content": user_message},
                ])
                db.append_message(
                    task_id,
                    "user",
                    user_message,
                    platform_message_id=persist_user_platform_message_id,
                )
                db.append_message(task_id, "assistant", "provider answer")
                finished.set()
                return {"final_response": "provider answer"}

        try:
            async with TestClient(TestServer(app)) as cli:
                with patch.object(adapter, "_create_agent", return_value=RecordingAgent()):
                    resp = await cli.post(
                        "/v1/runs",
                        json={
                            "input": prepared_input,
                            "checkpoint_content": display_checkpoint,
                            "session_id": session_id,
                            "platform_message_id": platform_message_id,
                        },
                    )
                    response_payload = await resp.json()
                    assert await asyncio.to_thread(finished.wait, 3.0)

            assert resp.status == 202, response_payload
            assert len(provider_payloads) == 1
            provider_serialized = json.dumps(provider_payloads[0], ensure_ascii=False)
            assert provider_serialized.count(prepared_canary) == 1
            assert display_checkpoint not in provider_serialized
            assert "checkpoint_content" not in provider_serialized
            assert provider_payloads[0][:-1] == [
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
            ]
            assert provider_payloads[0][-1]["content"] == [
                {"type": "text", "text": prepared_canary},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AA=="},
                },
            ]
            assert display_checkpoint not in json.dumps(
                response_payload, ensure_ascii=False
            )
            rows = db.get_messages(session_id)
            assert sum(
                row.get("platform_message_id") == platform_message_id
                for row in rows
            ) == 1
            assert [
                (row["role"], row["content"])
                for row in rows
            ] == [
                ("user", "earlier question"),
                ("assistant", "earlier answer"),
                ("user", display_checkpoint),
                ("assistant", "provider answer"),
            ]
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_managed_forged_checkpoint_content_fails_closed(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-forged-checkpoint-content"
        platform_message_id = "webui-turn:turn-forged"
        accepted_content = "accepted display checkpoint"
        db.create_session(session_id, "webui")
        db.append_message(
            session_id,
            "user",
            accepted_content,
            platform_message_id=platform_message_id,
        )
        adapter._session_db = db
        app = _create_runs_app(adapter)

        try:
            async with TestClient(TestServer(app)) as cli:
                with patch.object(adapter, "_create_agent") as mock_create:
                    resp = await cli.post(
                        "/v1/runs",
                        json={
                            "input": accepted_content,
                            "checkpoint_content": "forged display checkpoint",
                            "session_id": session_id,
                            "platform_message_id": platform_message_id,
                        },
                    )
                    payload = await resp.json()

            assert resp.status == 409
            assert payload["error"]["code"] == "platform_message_conflict"
            mock_create.assert_not_called()
            assert adapter._run_streams == {}
            assert [
                (row["role"], row["content"], row["platform_message_id"])
                for row in db.get_messages(session_id)
            ] == [("user", accepted_content, platform_message_id)]
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_managed_platform_id_without_checkpoint_match_keeps_full_history(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-unmatched-platform-id"
        old_platform_message_id = "webui-turn:turn-old"
        new_platform_message_id = "webui-turn:turn-new"
        db.create_session(session_id, "webui")
        db.append_message(
            session_id,
            "user",
            "older checkpoint",
            platform_message_id=old_platform_message_id,
        )
        db.append_message(session_id, "assistant", "older answer")
        adapter._session_db = db
        app = _create_runs_app(adapter)
        calls = []
        finished = threading.Event()

        class RecordingAgent:
            session_prompt_tokens = 0
            session_completion_tokens = 0
            session_total_tokens = 0

            def run_conversation(
                self,
                *,
                user_message,
                conversation_history,
                task_id,
                persist_user_platform_message_id=None,
            ):
                calls.append((conversation_history, user_message))
                db.append_message(
                    task_id,
                    "user",
                    user_message,
                    platform_message_id=persist_user_platform_message_id,
                )
                db.append_message(task_id, "assistant", "new answer")
                finished.set()
                return {"final_response": "new answer"}

        try:
            async with TestClient(TestServer(app)) as cli:
                with patch.object(adapter, "_create_agent", return_value=RecordingAgent()):
                    resp = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "new question",
                            "session_id": session_id,
                            "platform_message_id": new_platform_message_id,
                        },
                    )
                    payload = await resp.json()
                    assert await asyncio.to_thread(finished.wait, 3.0)

            assert resp.status == 202, payload
            assert calls[0][0] == [
                {
                    "role": "user",
                    "content": "older checkpoint",
                    "message_id": old_platform_message_id,
                },
                {"role": "assistant", "content": "older answer"},
            ]
            assert calls[0][1] == "new question"
            rows = db.get_messages(session_id)
            assert sum(
                row.get("platform_message_id") == old_platform_message_id
                for row in rows
            ) == 1
            assert sum(
                row.get("platform_message_id") == new_platform_message_id
                for row in rows
            ) == 1
        finally:
            db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("malformed_checkpoint", ["non_user", "multiple", "not_tail"])
    async def test_managed_malformed_current_checkpoint_identity_fails_closed(
        self, adapter, tmp_path, malformed_checkpoint
    ):
        db = SessionDB(db_path=tmp_path / f"{malformed_checkpoint}.db")
        session_id = f"session-malformed-{malformed_checkpoint}"
        platform_message_id = "webui-turn:turn-malformed"
        db.create_session(session_id, "webui")
        if malformed_checkpoint == "non_user":
            db.append_message(
                session_id,
                "assistant",
                "checkpoint content",
                platform_message_id=platform_message_id,
            )
        elif malformed_checkpoint == "multiple":
            db.append_message(
                session_id,
                "user",
                "checkpoint content",
                platform_message_id=platform_message_id,
            )
            db.append_message(
                session_id,
                "assistant",
                "checkpoint content",
                platform_message_id=platform_message_id,
            )
        else:
            db.append_message(
                session_id,
                "user",
                "checkpoint content",
                platform_message_id=platform_message_id,
            )
            db.append_message(session_id, "assistant", "later history")
        adapter._session_db = db
        app = _create_runs_app(adapter)

        try:
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "checkpoint content",
                        "session_id": session_id,
                        "platform_message_id": platform_message_id,
                    },
                )
                payload = await resp.json()

            assert resp.status == 409
            assert payload["error"]["code"] == "platform_message_conflict"
            assert adapter._run_streams == {}
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_managed_explicit_history_is_checked_before_checkpoint_filtering(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_id = "session-explicit-history-before-filter"
        platform_message_id = "webui-turn:turn-current"
        db.create_session(session_id, "webui")
        db.append_message(session_id, "user", "earlier question")
        db.append_message(session_id, "assistant", "earlier answer")
        db.append_message(
            session_id,
            "user",
            "current question",
            platform_message_id=platform_message_id,
        )
        adapter._session_db = db
        app = _create_runs_app(adapter)

        try:
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "current question",
                        "session_id": session_id,
                        "platform_message_id": platform_message_id,
                        "conversation_history": [
                            {"role": "user", "content": "earlier question"},
                            {"role": "assistant", "content": "earlier answer"},
                        ],
                    },
                )
                payload = await resp.json()

            assert resp.status == 409
            assert payload["error"]["code"] == "session_history_conflict"
            assert adapter._run_streams == {}
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_managed_session_accepts_current_multimodal_content_parts(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-image", "api_server")
        adapter._session_db = db
        app = _create_runs_app(adapter)
        current_input = [
            {"type": "text", "text": "describe this"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AA=="},
            },
        ]

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "an image"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": current_input, "session_id": "session-image"},
                )
                payload = await resp.json()
                for _ in range(40):
                    if mock_agent.run_conversation.called:
                        break
                    await asyncio.sleep(0.025)

        assert resp.status == 202, payload
        assert mock_agent.run_conversation.call_args.kwargs["user_message"] == current_input
        assert mock_agent.run_conversation.call_args.kwargs["conversation_history"] == []
        db.close()

    @pytest.mark.asyncio
    async def test_managed_session_rejects_conflicting_explicit_history(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-known", "api_server")
        db.append_message("session-known", "user", "stored question")
        db.append_message("session-known", "assistant", "stored answer")
        adapter._session_db = db
        app = _create_runs_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs",
                json={
                    "input": "next question",
                    "session_id": "session-known",
                    "conversation_history": [
                        {"role": "user", "content": "different question"},
                        {"role": "assistant", "content": "different answer"},
                    ],
                },
            )
            payload = await resp.json()

        assert resp.status == 409
        assert payload["error"]["code"] == "session_history_conflict"
        assert adapter._run_streams == {}
        db.close()

    @pytest.mark.asyncio
    async def test_managed_session_accepts_matching_explicit_history(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-known", "api_server")
        stored = [
            {"role": "user", "content": "stored question"},
            {"role": "assistant", "content": "stored answer"},
        ]
        for message in stored:
            db.append_message(
                "session-known", message["role"], message["content"]
            )
        adapter._session_db = db
        app = _create_runs_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "next question",
                        "session_id": "session-known",
                        "conversation_history": stored,
                    },
                )
                for _ in range(40):
                    if mock_agent.run_conversation.called:
                        break
                    await asyncio.sleep(0.025)

        assert resp.status == 202
        assert mock_agent.run_conversation.call_args.kwargs["conversation_history"] == stored
        db.close()

    @pytest.mark.asyncio
    async def test_managed_api_run_allows_real_compression_child_append(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "compression-api.db")
        db.create_session("compression-api-parent", "api_server")
        adapter._session_db = db
        mock_agent = MagicMock()

        def _run(**kwargs):
            db.end_session("compression-api-parent", "compression")
            db.create_session(
                "compression-api-child",
                "api_server",
                parent_session_id="compression-api-parent",
            )
            db.append_message("compression-api-child", "assistant", "continued")
            return {"final_response": "done"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "compress", "session_id": "compression-api-parent"},
                )
                payload = await resp.json()
                for _ in range(80):
                    status = await (await cli.get(f"/v1/runs/{payload['run_id']}")).json()
                    if status["status"] in {"completed", "failed"}:
                        break
                    await asyncio.sleep(0.025)
        assert resp.status == 202
        assert status["status"] == "completed"
        assert db.get_messages("compression-api-child")[-1]["content"] == "continued"
        assert db.get_managed_run_lease("compression-api-parent") is None
        assert db.get_managed_run_lease("compression-api-child") is None
        db.close()

    @pytest.mark.asyncio
    async def test_managed_api_run_rejects_precompression_branch_append(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "branch-api.db")
        db.create_session("branch-api-parent", "api_server")
        db.create_session(
            "branch-api-child", "api_server", parent_session_id="branch-api-parent"
        )
        adapter._session_db = db
        mock_agent = MagicMock()

        def _run(**kwargs):
            db.end_session("branch-api-parent", "compression")
            db.append_message("branch-api-child", "assistant", "must-not-land")
            return {"final_response": "unreachable"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "branch", "session_id": "branch-api-parent"},
                )
                payload = await resp.json()
                for _ in range(80):
                    status = await (await cli.get(f"/v1/runs/{payload['run_id']}")).json()
                    if status["status"] in {"completed", "failed"}:
                        break
                    await asyncio.sleep(0.025)
        assert resp.status == 202
        assert status["status"] == "failed"
        assert db.get_messages("branch-api-child") == []
        assert db.get_managed_run_lease("branch-api-parent") is None
        db.close()

    @pytest.mark.asyncio
    async def test_second_active_run_for_managed_session_returns_409(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-busy", "api_server")
        adapter._session_db = db
        app = _create_runs_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent, agent_ready, _ = _make_slow_agent()
                mock_create.return_value = mock_agent
                first = await cli.post(
                    "/v1/runs",
                    json={"input": "first", "session_id": "session-busy"},
                )
                agent_ready.wait(timeout=3.0)
                second = await cli.post(
                    "/v1/runs",
                    json={"input": "second", "session_id": "session-busy"},
                )
                second_payload = await second.json()
                first_payload = await first.json()
                await cli.post(f"/v1/runs/{first_payload['run_id']}/stop")

        assert first.status == 202
        assert second.status == 409
        assert second_payload["error"]["code"] == "session_busy"
        db.close()

    @pytest.mark.asyncio
    async def test_two_adapters_sharing_state_db_allow_only_one_managed_run(
        self, tmp_path
    ):
        db_path = tmp_path / "state.db"
        db_a = SessionDB(db_path=db_path)
        db_a.create_session("session-shared", "api_server")
        db_b = SessionDB(db_path=db_path)
        adapter_a = _make_adapter()
        adapter_b = _make_adapter()
        adapter_a._session_db = db_a
        adapter_b._session_db = db_b

        worker_release = threading.Event()
        entered_workers = []
        entered_lock = threading.Lock()

        def _agent_for(label):
            mock_agent = MagicMock()

            def _run(**kwargs):
                with entered_lock:
                    entered_workers.append(label)
                worker_release.wait(timeout=3.0)
                return {"final_response": label}

            mock_agent.run_conversation.side_effect = _run
            mock_agent.session_prompt_tokens = 0
            mock_agent.session_completion_tokens = 0
            mock_agent.session_total_tokens = 0
            return mock_agent

        app_a = _create_runs_app(adapter_a)
        app_b = _create_runs_app(adapter_b)
        try:
            async with (
                TestClient(TestServer(app_a)) as cli_a,
                TestClient(TestServer(app_b)) as cli_b,
            ):
                with (
                    patch.object(adapter_a, "_create_agent", return_value=_agent_for("a")),
                    patch.object(adapter_b, "_create_agent", return_value=_agent_for("b")),
                ):
                    response_a, response_b = await asyncio.gather(
                        cli_a.post(
                            "/v1/runs",
                            json={"input": "from a", "session_id": "session-shared"},
                        ),
                        cli_b.post(
                            "/v1/runs",
                            json={"input": "from b", "session_id": "session-shared"},
                        ),
                    )
                    payload_a, payload_b = await asyncio.gather(
                        response_a.json(), response_b.json()
                    )
                    for _ in range(40):
                        with entered_lock:
                            if entered_workers:
                                break
                        await asyncio.sleep(0.025)
        finally:
            worker_release.set()
            for _ in range(40):
                if not adapter_a._active_run_tasks and not adapter_b._active_run_tasks:
                    break
                await asyncio.sleep(0.025)
            db_a.close()
            db_b.close()

        responses = [(response_a.status, payload_a), (response_b.status, payload_b)]
        assert sorted(status for status, _ in responses) == [202, 409]
        busy_payload = next(payload for status, payload in responses if status == 409)
        assert busy_payload["error"]["code"] == "session_busy"
        assert len(entered_workers) == 1

    @pytest.mark.asyncio
    async def test_busy_lease_is_checked_before_loading_managed_history(
        self, tmp_path
    ):
        db_path = tmp_path / "state.db"
        owner_db = SessionDB(db_path=db_path)
        owner_db.create_session("session-history-race", "api_server")
        owner_db.append_message("session-history-race", "user", "baseline")
        assert owner_db.acquire_managed_run_lease(
            "session-history-race",
            owner_id="old-owner",
            run_id="old-run",
            lease_seconds=30.0,
        ) is True
        contender_db = SessionDB(db_path=db_path)
        adapter = _make_adapter()
        adapter._session_db = contender_db
        original_history = contender_db.get_messages_as_conversation
        history_was_loaded = threading.Event()

        def _stale_history_then_old_owner_finishes(session_id):
            stale = original_history(session_id)
            history_was_loaded.set()
            owner_db.append_message(session_id, "assistant", "old run committed")
            owner_db.release_managed_run_lease(
                session_id,
                owner_id="old-owner",
                run_id="old-run",
            )
            return stale

        app = _create_runs_app(adapter)
        try:
            with patch.object(
                contender_db,
                "get_messages_as_conversation",
                side_effect=_stale_history_then_old_owner_finishes,
            ), patch.object(adapter, "_create_agent") as create_agent:
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "contender",
                            "session_id": "session-history-race",
                        },
                    )
                    payload = await response.json()
        finally:
            for _ in range(80):
                if not adapter._active_run_tasks:
                    break
                await asyncio.sleep(0.025)
            owner_db.release_managed_run_lease(
                "session-history-race",
                owner_id="old-owner",
                run_id="old-run",
            )
            for lease_db in (contender_db, owner_db):
                lease = lease_db.get_managed_run_lease("session-history-race")
                if lease is not None:
                    lease_db.release_managed_run_lease(
                        "session-history-race",
                        owner_id=lease["owner_id"],
                        run_id=lease["run_id"],
                    )
                    break
            contender_db.close()
            owner_db.close()

        assert response.status == 409, payload
        assert payload["error"]["code"] == "session_busy"
        assert not history_was_loaded.is_set()
        create_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_history_read_is_covered_by_managed_lease_heartbeat(
        self, tmp_path
    ):
        db_path = tmp_path / "state.db"
        db_a = SessionDB(db_path=db_path)
        db_a.create_session("session-history-heartbeat", "api_server")
        db_b = SessionDB(db_path=db_path)
        adapter_a = _make_adapter()
        adapter_b = _make_adapter()
        adapter_a._session_db = db_a
        adapter_b._session_db = db_b
        adapter_a._MANAGED_RUN_LEASE_SECONDS = 0.12
        adapter_a._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.02
        adapter_b._MANAGED_RUN_LEASE_SECONDS = 0.12
        adapter_b._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.02
        history_entered = threading.Event()
        history_release = threading.Event()
        original_history = db_a.get_messages_as_conversation

        def _blocked_history(session_id):
            history_entered.set()
            history_release.wait(timeout=3.0)
            return original_history(session_id)

        agent_a = MagicMock()
        agent_a.run_conversation.return_value = {"final_response": "a"}
        agent_a.session_prompt_tokens = 0
        agent_a.session_completion_tokens = 0
        agent_a.session_total_tokens = 0
        app_a = _create_runs_app(adapter_a)
        app_b = _create_runs_app(adapter_b)

        try:
            with patch.object(
                db_a,
                "get_messages_as_conversation",
                side_effect=_blocked_history,
            ), patch.object(
                adapter_a, "_create_agent", return_value=agent_a
            ), patch.object(adapter_b, "_create_agent") as create_agent_b:
                async with (
                    TestClient(TestServer(app_a)) as cli_a,
                    TestClient(TestServer(app_b)) as cli_b,
                ):
                    request_a = asyncio.create_task(
                        cli_a.post(
                            "/v1/runs",
                            json={
                                "input": "a",
                                "session_id": "session-history-heartbeat",
                            },
                        )
                    )
                    assert await asyncio.to_thread(
                        history_entered.wait, 3.0
                    )
                    probe_started = asyncio.get_running_loop().time()
                    await asyncio.sleep(0.05)
                    probe_delay = asyncio.get_running_loop().time() - probe_started
                    await asyncio.sleep(0.25)
                    response_b = await cli_b.post(
                        "/v1/runs",
                        json={
                            "input": "b",
                            "session_id": "session-history-heartbeat",
                        },
                    )
                    payload_b = await response_b.json()
                    history_release.set()
                    response_a = await request_a
                    payload_a = await response_a.json()
                    for _ in range(80):
                        if not adapter_a._active_run_tasks:
                            break
                        await asyncio.sleep(0.025)

            assert probe_delay < 0.15
            assert response_b.status == 409, payload_b
            assert payload_b["error"]["code"] == "session_busy"
            create_agent_b.assert_not_called()
            assert response_a.status == 202, payload_a
            assert agent_a.run_conversation.called
        finally:
            history_release.set()
            for _ in range(80):
                if not adapter_a._active_run_tasks and not adapter_b._active_run_tasks:
                    break
                await asyncio.sleep(0.025)
            db_a.close()
            db_b.close()

    @pytest.mark.asyncio
    async def test_history_takeover_rejects_stale_owner_before_worker_admission(
        self, tmp_path
    ):
        db_path = tmp_path / "state.db"
        db_a = SessionDB(db_path=db_path)
        db_a.create_session("session-history-takeover", "api_server")
        db_b = SessionDB(db_path=db_path)
        adapter_a = _make_adapter()
        adapter_b = _make_adapter()
        adapter_a._session_db = db_a
        adapter_b._session_db = db_b
        adapter_a._MANAGED_RUN_LEASE_SECONDS = 0.10
        adapter_a._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 1.0
        adapter_b._MANAGED_RUN_LEASE_SECONDS = 1.0
        adapter_b._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.05
        history_entered = threading.Event()
        history_release = threading.Event()
        worker_b_started = threading.Event()
        worker_b_release = threading.Event()
        original_history = db_a.get_messages_as_conversation

        def _blocked_history(session_id):
            history_entered.set()
            history_release.wait(timeout=3.0)
            return original_history(session_id)

        agent_a = MagicMock()
        agent_a.run_conversation.return_value = {"final_response": "stale"}
        agent_a.session_prompt_tokens = 0
        agent_a.session_completion_tokens = 0
        agent_a.session_total_tokens = 0
        agent_b = MagicMock()

        def _run_b(**kwargs):
            worker_b_started.set()
            worker_b_release.wait(timeout=3.0)
            return {"final_response": "current"}

        agent_b.run_conversation.side_effect = _run_b
        agent_b.session_prompt_tokens = 0
        agent_b.session_completion_tokens = 0
        agent_b.session_total_tokens = 0
        app_a = _create_runs_app(adapter_a)
        app_b = _create_runs_app(adapter_b)

        try:
            with patch.object(
                db_a,
                "get_messages_as_conversation",
                side_effect=_blocked_history,
            ), patch.object(
                adapter_a, "_create_agent", return_value=agent_a
            ), patch.object(adapter_b, "_create_agent", return_value=agent_b):
                async with (
                    TestClient(TestServer(app_a)) as cli_a,
                    TestClient(TestServer(app_b)) as cli_b,
                ):
                    request_a = asyncio.create_task(
                        cli_a.post(
                            "/v1/runs",
                            json={
                                "input": "a",
                                "session_id": "session-history-takeover",
                            },
                        )
                    )
                    assert await asyncio.to_thread(
                        history_entered.wait, 3.0
                    )
                    await asyncio.sleep(0.15)
                    response_b = await cli_b.post(
                        "/v1/runs",
                        json={
                            "input": "b",
                            "session_id": "session-history-takeover",
                        },
                    )
                    payload_b = await response_b.json()
                    assert response_b.status == 202, payload_b
                    assert await asyncio.to_thread(
                        worker_b_started.wait, 3.0
                    )
                    history_release.set()
                    response_a = await request_a
                    payload_a = await response_a.json()
                    lease = db_b.get_managed_run_lease(
                        "session-history-takeover"
                    )

            assert response_a.status == 409, payload_a
            assert payload_a["error"]["code"] == "session_lease_lost"
            agent_a.run_conversation.assert_not_called()
            assert lease is not None
            assert lease["run_id"] == payload_b["run_id"]
            assert adapter_a._managed_session_runs == {}
            assert adapter_a._run_streams == {}
            assert adapter_a._run_statuses == {}
        finally:
            history_release.set()
            worker_b_release.set()
            for _ in range(120):
                if not adapter_a._active_run_tasks and not adapter_b._active_run_tasks:
                    break
                await asyncio.sleep(0.025)
            db_a.close()
            db_b.close()

    @pytest.mark.asyncio
    async def test_executor_revalidates_exact_lease_before_agent_call(
        self, tmp_path
    ):
        db_path = tmp_path / "state.db"
        db_a = SessionDB(db_path=db_path)
        db_a.create_session("session-executor-takeover", "api_server")
        db_b = SessionDB(db_path=db_path)
        adapter = _make_adapter()
        adapter._session_db = db_a
        adapter._MANAGED_RUN_LEASE_SECONDS = 1.0
        adapter._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 10.0
        takeover_done = threading.Event()
        original_heartbeat = db_a.heartbeat_managed_run_lease
        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "stale"}
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        def _renew_then_take_over(session_id, **kwargs):
            renewed = original_heartbeat(session_id, **kwargs)
            if not takeover_done.is_set():
                assert renewed is True
                assert db_b.release_managed_run_lease(
                    session_id,
                    owner_id=kwargs["owner_id"],
                    run_id=kwargs["run_id"],
                ) is True
                assert db_b.acquire_managed_run_lease(
                    session_id,
                    owner_id="owner-b",
                    run_id="run-b",
                    lease_seconds=10.0,
                ) is True
                takeover_done.set()
            return renewed

        app = _create_runs_app(adapter)
        payload = None
        status_payload = None
        try:
            with patch.object(
                db_a,
                "heartbeat_managed_run_lease",
                side_effect=_renew_then_take_over,
            ), patch.object(adapter, "_create_agent", return_value=agent):
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "a",
                            "session_id": "session-executor-takeover",
                        },
                    )
                    payload = await response.json()
                    assert response.status == 202, payload
                    for _ in range(120):
                        status_response = await cli.get(
                            f"/v1/runs/{payload['run_id']}"
                        )
                        status_payload = await status_response.json()
                        if status_payload["status"] in {"completed", "failed"}:
                            break
                        await asyncio.sleep(0.025)

            assert takeover_done.is_set()
            assert status_payload is not None
            assert status_payload["status"] == "failed"
            assert "lease lost" in status_payload["error"]
            agent.run_conversation.assert_not_called()
            lease = db_b.get_managed_run_lease("session-executor-takeover")
            assert lease is not None
            assert lease["owner_id"] == "owner-b"
            assert lease["run_id"] == "run-b"
        finally:
            lease = db_b.get_managed_run_lease("session-executor-takeover")
            if lease is not None:
                db_b.release_managed_run_lease(
                    "session-executor-takeover",
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db_a.close()
            db_b.close()

    @pytest.mark.asyncio
    async def test_managed_lease_heartbeat_prevents_takeover_during_long_run(
        self, tmp_path
    ):
        db_path = tmp_path / "state.db"
        db_a = SessionDB(db_path=db_path)
        db_a.create_session("session-heartbeat", "api_server")
        db_b = SessionDB(db_path=db_path)
        adapter_a = _make_adapter()
        adapter_b = _make_adapter()
        adapter_a._session_db = db_a
        adapter_b._session_db = db_b
        adapter_a._MANAGED_RUN_LEASE_SECONDS = 0.3
        adapter_a._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.05
        adapter_b._MANAGED_RUN_LEASE_SECONDS = 0.3
        adapter_b._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.05

        worker_ready = threading.Event()
        worker_release = threading.Event()
        mock_agent = MagicMock()

        def _run(**kwargs):
            worker_ready.set()
            worker_release.wait(timeout=3.0)
            return {"final_response": "done"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0

        app_a = _create_runs_app(adapter_a)
        app_b = _create_runs_app(adapter_b)
        try:
            async with (
                TestClient(TestServer(app_a)) as cli_a,
                TestClient(TestServer(app_b)) as cli_b,
            ):
                with (
                    patch.object(adapter_a, "_create_agent", return_value=mock_agent),
                    patch.object(adapter_b, "_create_agent", return_value=mock_agent),
                ):
                    first = await cli_a.post(
                        "/v1/runs",
                        json={"input": "first", "session_id": "session-heartbeat"},
                    )
                    first_payload = await first.json()
                    assert first.status == 202, first_payload
                    assert worker_ready.wait(timeout=3.0)
                    await asyncio.sleep(0.55)

                    second = await cli_b.post(
                        "/v1/runs",
                        json={"input": "second", "session_id": "session-heartbeat"},
                    )
                    second_payload = await second.json()
        finally:
            worker_release.set()
            for _ in range(80):
                if not adapter_a._active_run_tasks and not adapter_b._active_run_tasks:
                    break
                await asyncio.sleep(0.025)
            db_a.close()
            db_b.close()

        assert second.status == 409, second_payload
        assert second_payload["error"]["code"] == "session_busy"

    @pytest.mark.asyncio
    async def test_expired_owner_is_fenced_from_late_history_writes(
        self, tmp_path
    ):
        db_path = tmp_path / "state.db"
        db_a = SessionDB(db_path=db_path)
        db_a.create_session("session-fenced", "api_server")
        db_b = SessionDB(db_path=db_path)
        adapter_a = _make_adapter()
        adapter_b = _make_adapter()
        adapter_a._session_db = db_a
        adapter_b._session_db = db_b
        adapter_a._MANAGED_RUN_LEASE_SECONDS = 0.2
        adapter_a._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.05
        adapter_b._MANAGED_RUN_LEASE_SECONDS = 0.2
        adapter_b._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.05
        heartbeat_entered = threading.Event()
        heartbeat_continue = threading.Event()
        a_ready = threading.Event()
        a_write = threading.Event()
        original_heartbeat = db_a.heartbeat_managed_run_lease

        def _blocked_heartbeat(*args, **kwargs):
            if not threading.current_thread().name.startswith(
                "managed-run-lease-"
            ):
                return original_heartbeat(*args, **kwargs)
            heartbeat_entered.set()
            heartbeat_continue.wait(timeout=3.0)
            return original_heartbeat(*args, **kwargs)

        agent_a = MagicMock()

        def _run_a(**kwargs):
            a_ready.set()
            a_write.wait(timeout=3.0)
            db_a.append_message("session-fenced", "user", "a stale question")
            db_a.append_message("session-fenced", "assistant", "a late answer")
            return {"final_response": "a late answer"}

        agent_a.run_conversation.side_effect = _run_a
        agent_a.session_prompt_tokens = 0
        agent_a.session_completion_tokens = 0
        agent_a.session_total_tokens = 0

        agent_b = MagicMock()

        def _run_b(**kwargs):
            db_b.append_message("session-fenced", "user", "b question")
            db_b.append_message("session-fenced", "assistant", "b answer")
            return {"final_response": "b answer"}

        agent_b.run_conversation.side_effect = _run_b
        agent_b.session_prompt_tokens = 0
        agent_b.session_completion_tokens = 0
        agent_b.session_total_tokens = 0
        app_a = _create_runs_app(adapter_a)
        app_b = _create_runs_app(adapter_b)

        try:
            with patch.object(
                db_a,
                "heartbeat_managed_run_lease",
                side_effect=_blocked_heartbeat,
            ), patch.object(
                adapter_a, "_create_agent", return_value=agent_a
            ), patch.object(adapter_b, "_create_agent", return_value=agent_b):
                async with (
                    TestClient(TestServer(app_a)) as cli_a,
                    TestClient(TestServer(app_b)) as cli_b,
                ):
                    response_a = await cli_a.post(
                        "/v1/runs",
                        json={"input": "a", "session_id": "session-fenced"},
                    )
                    payload_a = await response_a.json()
                    assert response_a.status == 202, payload_a
                    assert a_ready.wait(timeout=3.0)
                    assert heartbeat_entered.wait(timeout=3.0)
                    await asyncio.sleep(0.3)

                    response_b = await cli_b.post(
                        "/v1/runs",
                        json={"input": "b", "session_id": "session-fenced"},
                    )
                    payload_b = await response_b.json()
                    assert response_b.status == 202, payload_b
                    for _ in range(120):
                        status_b = await (
                            await cli_b.get(f"/v1/runs/{payload_b['run_id']}")
                        ).json()
                        if status_b["status"] == "completed":
                            break
                        await asyncio.sleep(0.025)
                    for _ in range(80):
                        if db_b.get_managed_run_lease("session-fenced") is None:
                            break
                        await asyncio.sleep(0.025)

                    a_write.set()
                    heartbeat_continue.set()
                    for _ in range(120):
                        status_a = await (
                            await cli_a.get(f"/v1/runs/{payload_a['run_id']}")
                        ).json()
                        if status_a["status"] in {"failed", "completed"}:
                            break
                        await asyncio.sleep(0.025)

            assert status_b["status"] == "completed"
            assert status_a["status"] == "failed"
            assert db_b.get_messages_as_conversation("session-fenced") == [
                {"role": "user", "content": "b question"},
                {"role": "assistant", "content": "b answer"},
            ]
        finally:
            a_write.set()
            heartbeat_continue.set()
            for _ in range(120):
                if not adapter_a._active_run_tasks and not adapter_b._active_run_tasks:
                    break
                await asyncio.sleep(0.025)
            db_a.close()
            db_b.close()

    @pytest.mark.asyncio
    async def test_managed_lease_releases_after_terminal_status_and_persisted_turn(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-release-order", "api_server")
        adapter._session_db = db
        release_observations = []
        original_release = db.release_managed_run_lease

        def _recording_release(session_id, *, owner_id, run_id):
            release_observations.append({
                "status": adapter._run_statuses.get(run_id, {}).get("status"),
                "history": db.get_messages_as_conversation(session_id),
            })
            return original_release(
                session_id, owner_id=owner_id, run_id=run_id
            )

        mock_agent = MagicMock()

        def _run(**kwargs):
            db.append_message("session-release-order", "user", "question")
            db.append_message("session-release-order", "assistant", "answer")
            return {"final_response": "answer"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)

        try:
            with patch.object(
                db, "release_managed_run_lease", side_effect=_recording_release
            ), patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "question",
                            "session_id": "session-release-order",
                        },
                    )
                    payload = await response.json()
                    assert response.status == 202, payload
                    for _ in range(80):
                        if release_observations:
                            break
                        await asyncio.sleep(0.025)
        finally:
            db.close()

        assert release_observations == [{
            "status": "completed",
            "history": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ],
        }]

    @pytest.mark.asyncio
    async def test_blocked_managed_release_retains_all_ten_admission_slots(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        session_ids = [f"session-blocked-release-{i}" for i in range(11)]
        for session_id in session_ids:
            db.create_session(session_id, "api_server")
        adapter._session_db = db
        release_continue = threading.Event()
        ten_releases_entered = threading.Event()
        release_calls = []
        release_lock = threading.Lock()
        original_release = db.release_managed_run_lease

        def _blocked_release(lease_session_id, *, owner_id, run_id):
            with release_lock:
                release_calls.append({
                    "session_id": lease_session_id,
                    "owner_id": owner_id,
                    "run_id": run_id,
                })
                if len(release_calls) >= adapter._MAX_CONCURRENT_RUNS:
                    ten_releases_entered.set()
            release_continue.wait()
            return original_release(
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )

        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "done"}
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        try:
            with (
                patch.object(
                    db,
                    "release_managed_run_lease",
                    side_effect=_blocked_release,
                ),
                patch.object(adapter, "_create_agent", return_value=agent),
            ):
                first_responses = await asyncio.gather(
                    *(
                        adapter._handle_runs(
                            _direct_run_request({
                                "input": f"run {index}",
                                "session_id": session_ids[index],
                            })
                        )
                        for index in range(adapter._MAX_CONCURRENT_RUNS)
                    )
                )
                assert all(
                    response.status == 202
                    for response in first_responses
                )
                assert await asyncio.to_thread(
                    ten_releases_entered.wait,
                    3.0,
                )

                reservations_while_release_blocked = len(
                    adapter._run_admission_reservations
                )
                eleventh = await adapter._handle_runs(
                    _direct_run_request({
                        "input": "must remain rate limited",
                        "session_id": session_ids[-1],
                    })
                )
                eleventh_payload = json.loads(eleventh.text)
                if eleventh.status == 202:
                    unexpected_run_id = eleventh_payload["run_id"]
                    unexpected_task = adapter._active_run_tasks[
                        unexpected_run_id
                    ]
                    unexpected_task.cancel()
                    await asyncio.gather(
                        unexpected_task,
                        return_exceptions=True,
                    )

                assert reservations_while_release_blocked == (
                    adapter._MAX_CONCURRENT_RUNS
                )
                assert eleventh.status == 429
                assert (
                    eleventh_payload["error"]["code"]
                    == "rate_limit_exceeded"
                )
        finally:
            release_continue.set()
            deadline = asyncio.get_running_loop().time() + 3.0
            while (
                adapter._active_run_tasks
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.01)
            for session_id in session_ids:
                lease = db.get_managed_run_lease(session_id)
                if lease is not None:
                    original_release(
                        session_id,
                        owner_id=lease["owner_id"],
                        run_id=lease["run_id"],
                    )
            db.close()

        assert len(release_calls) >= adapter._MAX_CONCURRENT_RUNS
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_cancel_after_worker_commit_keeps_completed_terminal_state(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-commit-cancel-race", "api_server")
        adapter._session_db = db
        worker_ready = threading.Event()
        worker_release = threading.Event()
        mock_agent = MagicMock()

        def _run(**kwargs):
            db.append_message("session-commit-cancel-race", "user", "question")
            db.append_message("session-commit-cancel-race", "assistant", "answer")
            worker_ready.set()
            worker_release.wait(timeout=3.0)
            return {"final_response": "answer"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)

        try:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "question",
                            "session_id": "session-commit-cancel-race",
                        },
                    )
                    payload = await response.json()
                    assert response.status == 202, payload
                    assert worker_ready.wait(timeout=3.0)
                    task = adapter._active_run_tasks[payload["run_id"]]
                    worker_release.set()
                    # Hold the event loop long enough for the executor worker
                    # to return, then cancel before its Future callback can
                    # publish the terminal event.
                    _time.sleep(0.05)
                    task.cancel()
                    await task
                    status_response = await cli.get(
                        f"/v1/runs/{payload['run_id']}"
                    )
                    status_payload = await status_response.json()

            assert status_payload["status"] == "completed"
            assert status_payload["output"] == "answer"
            assert db.get_messages_as_conversation(
                "session-commit-cancel-race"
            ) == [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ]
            assert db.get_managed_run_lease("session-commit-cancel-race") is None
        finally:
            worker_release.set()
            db.close()

    @pytest.mark.asyncio
    async def test_managed_lease_acquire_failure_is_503_without_execution(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-db-busy", "api_server")
        adapter._session_db = db
        app = _create_runs_app(adapter)

        try:
            with patch.object(
                db,
                "acquire_managed_run_lease",
                side_effect=sqlite3.OperationalError("database is locked"),
            ), patch.object(adapter, "_create_agent") as create_agent:
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={"input": "hello", "session_id": "session-db-busy"},
                    )
                    payload = await response.json()
        finally:
            db.close()

        assert response.status == 503
        assert payload["error"]["code"] == "session_lease_unavailable"
        create_agent.assert_not_called()
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}

    @pytest.mark.asyncio
    async def test_managed_lease_contention_does_not_block_event_loop(
        self, adapter, tmp_path
    ):
        db_path = tmp_path / "state.db"
        db = SessionDB(db_path=db_path)
        db.create_session("session-loop-responsive", "api_server")
        adapter._session_db = db
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"final_response": "done"}
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)
        lock_conn = sqlite3.connect(
            str(db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        lock_conn.execute("BEGIN IMMEDIATE")

        def _release_db_lock():
            _time.sleep(0.3)
            lock_conn.commit()

        release_thread = threading.Thread(target=_release_db_lock)
        release_thread.start()

        async def _event_loop_probe():
            started_at = asyncio.get_running_loop().time()
            await asyncio.sleep(0.05)
            return asyncio.get_running_loop().time() - started_at

        try:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    probe_task = asyncio.create_task(_event_loop_probe())
                    await asyncio.sleep(0)
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-loop-responsive",
                        },
                    )
                    payload = await response.json()
                    probe_delay = await probe_task

            assert response.status == 202, payload
            assert probe_delay < 0.15
        finally:
            release_thread.join(timeout=2.0)
            lock_conn.close()
            for _ in range(80):
                if not adapter._active_run_tasks:
                    break
                await asyncio.sleep(0.025)
            db.close()

    @pytest.mark.asyncio
    async def test_managed_lease_release_does_not_block_event_loop(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-release-responsive", "api_server")
        adapter._session_db = db
        worker_ready = threading.Event()
        worker_release = threading.Event()
        original_release = db.release_managed_run_lease

        def _slow_release(*args, **kwargs):
            _time.sleep(0.3)
            return original_release(*args, **kwargs)

        mock_agent = MagicMock()

        def _run(**kwargs):
            worker_ready.set()
            worker_release.wait(timeout=3.0)
            return {"final_response": "done"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)

        async def _event_loop_probe():
            started_at = asyncio.get_running_loop().time()
            await asyncio.sleep(0.05)
            return asyncio.get_running_loop().time() - started_at

        try:
            with patch.object(
                db,
                "release_managed_run_lease",
                side_effect=_slow_release,
            ), patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-release-responsive",
                        },
                    )
                    payload = await response.json()
                    assert response.status == 202, payload
                    assert worker_ready.wait(timeout=3.0)
                    probe_task = asyncio.create_task(_event_loop_probe())
                    await asyncio.sleep(0)
                    worker_release.set()
                    probe_delay = await probe_task
                    for _ in range(80):
                        if not adapter._active_run_tasks:
                            break
                        await asyncio.sleep(0.025)

            assert probe_delay < 0.15
        finally:
            worker_release.set()
            db.close()

    @pytest.mark.asyncio
    async def test_managed_history_read_failure_releases_admission_lease(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-history-db-failure", "api_server")
        adapter._session_db = db
        app = _create_runs_app(adapter)

        try:
            with patch.object(
                db,
                "get_messages_as_conversation",
                side_effect=sqlite3.OperationalError("database is locked"),
            ), patch.object(adapter, "_create_agent") as create_agent:
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-history-db-failure",
                        },
                    )
                    payload = await response.json()

            assert response.status == 503
            assert payload["error"]["code"] == "session_db_unavailable"
            create_agent.assert_not_called()
            assert db.get_managed_run_lease("session-history-db-failure") is None
            assert adapter._run_streams == {}
            assert adapter._run_statuses == {}
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_task_creation_failure_releases_lease_without_execution(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-task-create-failure", "api_server")
        adapter._session_db = db
        adapter._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.01
        app = _create_runs_app(adapter)
        loop = asyncio.get_running_loop()
        original_create_task = loop.create_task
        response = None
        payload = None

        def _fail_worker_task_creation(coroutine, *args, **kwargs):
            coroutine_name = getattr(
                getattr(coroutine, "cr_code", None),
                "co_name",
                "",
            )
            if coroutine_name == "_run_and_close":
                raise RuntimeError("executor unavailable")
            return original_create_task(coroutine, *args, **kwargs)

        try:
            with patch.object(
                loop,
                "create_task",
                side_effect=_fail_worker_task_creation,
            ), patch.object(adapter, "_create_agent") as create_agent:
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-task-create-failure",
                        },
                    )
                    payload = await response.json()
        finally:
            lease = db.get_managed_run_lease("session-task-create-failure")
            if lease is not None:
                db.release_managed_run_lease(
                    "session-task-create-failure",
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

        assert response is not None
        assert response.status == 503
        assert payload["error"]["code"] == "run_executor_unavailable"
        create_agent.assert_not_called()
        assert adapter._managed_session_runs == {}
        assert adapter._run_streams == {}
        assert adapter._run_statuses == {}
        assert adapter._run_admission_reservations == set()

    @pytest.mark.asyncio
    async def test_managed_lease_heartbeat_failure_interrupts_and_fails_run(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-heartbeat-failure", "api_server")
        adapter._session_db = db
        adapter._MANAGED_RUN_LEASE_SECONDS = 0.3
        adapter._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.05
        worker_ready = threading.Event()
        interrupted = threading.Event()
        original_heartbeat = db.heartbeat_managed_run_lease
        mock_agent = MagicMock()
        mock_agent.interrupt.side_effect = lambda message=None: interrupted.set()

        def _fail_background_heartbeat(*args, **kwargs):
            if not threading.current_thread().name.startswith(
                "managed-run-lease-"
            ):
                return original_heartbeat(*args, **kwargs)
            raise sqlite3.OperationalError("database is locked")

        def _run(**kwargs):
            worker_ready.set()
            interrupted.wait(timeout=3.0)
            return {"final_response": "must not complete"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)

        try:
            with patch.object(
                db,
                "heartbeat_managed_run_lease",
                side_effect=_fail_background_heartbeat,
            ), patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-heartbeat-failure",
                        },
                    )
                    payload = await response.json()
                    assert response.status == 202, payload
                    assert worker_ready.wait(timeout=3.0)
                    for _ in range(120):
                        status_response = await cli.get(
                            f"/v1/runs/{payload['run_id']}"
                        )
                        status_payload = await status_response.json()
                        if status_payload["status"] == "failed":
                            break
                        await asyncio.sleep(0.025)
                    for _ in range(80):
                        if db.get_managed_run_lease(
                            "session-heartbeat-failure"
                        ) is None:
                            break
                        await asyncio.sleep(0.025)

            assert interrupted.is_set()
            assert status_payload["status"] == "failed"
            assert "lease lost" in status_payload["error"]
            assert db.get_managed_run_lease("session-heartbeat-failure") is None
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_terminal_release_does_not_race_inflight_heartbeat(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-heartbeat-release-race", "api_server")
        adapter._session_db = db
        adapter._MANAGED_RUN_LEASE_SECONDS = 0.5
        adapter._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 0.01
        heartbeat_entered = threading.Event()
        heartbeat_continue = threading.Event()
        original_heartbeat = db.heartbeat_managed_run_lease

        def _blocked_heartbeat(*args, **kwargs):
            if not threading.current_thread().name.startswith(
                "managed-run-lease-"
            ):
                return original_heartbeat(*args, **kwargs)
            heartbeat_entered.set()
            heartbeat_continue.wait(timeout=3.0)
            return original_heartbeat(*args, **kwargs)

        mock_agent = MagicMock()

        def _run(**kwargs):
            assert heartbeat_entered.wait(timeout=3.0)
            return {"final_response": "done"}

        mock_agent.run_conversation.side_effect = _run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)

        try:
            with patch.object(
                db,
                "heartbeat_managed_run_lease",
                side_effect=_blocked_heartbeat,
            ), patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-heartbeat-release-race",
                        },
                    )
                    payload = await response.json()
                    assert response.status == 202, payload
                    for _ in range(120):
                        status_response = await cli.get(
                            f"/v1/runs/{payload['run_id']}"
                        )
                        status_payload = await status_response.json()
                        if status_payload["status"] == "completed":
                            break
                        await asyncio.sleep(0.025)
                    heartbeat_continue.set()
                    for _ in range(120):
                        if (
                            not adapter._active_run_tasks
                            and db.get_managed_run_lease(
                                "session-heartbeat-release-race"
                            ) is None
                        ):
                            break
                        await asyncio.sleep(0.025)

            assert status_payload["status"] == "completed"
            mock_agent.interrupt.assert_not_called()
            assert db.get_managed_run_lease(
                "session-heartbeat-release-race"
            ) is None
        finally:
            heartbeat_continue.set()
            db.close()

    @pytest.mark.asyncio
    async def test_managed_worker_exception_releases_after_failed_status(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-worker-error", "api_server")
        adapter._session_db = db
        release_statuses = []
        original_release = db.release_managed_run_lease

        def _recording_release(session_id, *, owner_id, run_id):
            release_statuses.append(
                adapter._run_statuses.get(run_id, {}).get("status")
            )
            return original_release(
                session_id, owner_id=owner_id, run_id=run_id
            )

        mock_agent = MagicMock()
        mock_agent.run_conversation.side_effect = RuntimeError("worker failed")
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)

        try:
            with patch.object(
                db, "release_managed_run_lease", side_effect=_recording_release
            ), patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    response = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "hello",
                            "session_id": "session-worker-error",
                        },
                    )
                    payload = await response.json()
                    assert response.status == 202, payload
                    for _ in range(80):
                        if release_statuses:
                            break
                        await asyncio.sleep(0.025)

            assert release_statuses == ["failed"]
            assert db.get_managed_run_lease("session-worker-error") is None
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_release_db_failure_keeps_durable_guard_fail_closed(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-release-db-failure", "api_server")
        adapter._session_db = db
        original_release = db.release_managed_run_lease
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"final_response": "done"}
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0
        app = _create_runs_app(adapter)

        try:
            with patch.object(
                db,
                "release_managed_run_lease",
                side_effect=sqlite3.OperationalError("database is locked"),
            ), patch.object(adapter, "_create_agent", return_value=mock_agent):
                async with TestClient(TestServer(app)) as cli:
                    first = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "first",
                            "session_id": "session-release-db-failure",
                        },
                    )
                    first_payload = await first.json()
                    assert first.status == 202, first_payload
                    for _ in range(80):
                        status_response = await cli.get(
                            f"/v1/runs/{first_payload['run_id']}"
                        )
                        status_payload = await status_response.json()
                        if status_payload["status"] == "completed":
                            break
                        await asyncio.sleep(0.025)

                    second = await cli.post(
                        "/v1/runs",
                        json={
                            "input": "second",
                            "session_id": "session-release-db-failure",
                        },
                    )
                    second_payload = await second.json()

            assert status_payload["status"] == "completed"
            assert second.status == 409, second_payload
            assert second_payload["error"]["code"] == "session_busy"
            assert mock_agent.run_conversation.call_count == 1
            lease = db.get_managed_run_lease("session-release-db-failure")
            assert lease is not None
            assert lease["run_id"] == first_payload["run_id"]
        finally:
            lease = db.get_managed_run_lease("session-release-db-failure")
            if lease is not None:
                original_release(
                    "session-release-db-failure",
                    owner_id=lease["owner_id"],
                    run_id=lease["run_id"],
                )
            db.close()

    @pytest.mark.asyncio
    async def test_cancelled_async_wrapper_reconciles_after_worker_exits(
        self, adapter, tmp_path
    ):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("session-cancel-race", "api_server")
        adapter._session_db = db
        app = _create_runs_app(adapter)
        worker_ready = threading.Event()
        worker_release = threading.Event()
        mock_agent = MagicMock()

        def _slow_run(**kwargs):
            worker_ready.set()
            worker_release.wait(timeout=3.0)
            db.append_message("session-cancel-race", "user", "first")
            db.append_message("session-cancel-race", "assistant", "late result")
            return {"final_response": "late result"}

        mock_agent.run_conversation.side_effect = _slow_run
        mock_agent.session_prompt_tokens = 0
        mock_agent.session_completion_tokens = 0
        mock_agent.session_total_tokens = 0

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "first", "session_id": "session-cancel-race"},
                )
                payload = await resp.json()
                assert worker_ready.wait(timeout=3.0)
                task = adapter._active_run_tasks[payload["run_id"]]
                task.cancel()
                await asyncio.sleep(0.05)

                assert adapter._managed_session_runs.get("session-cancel-race") == payload["run_id"]
                lease = db.get_managed_run_lease("session-cancel-race")
                assert lease is not None
                assert lease["run_id"] == payload["run_id"]
                worker_release.set()
                await task
                for _ in range(40):
                    if (
                        "session-cancel-race" not in adapter._managed_session_runs
                        and db.get_managed_run_lease("session-cancel-race") is None
                    ):
                        break
                    await asyncio.sleep(0.025)

        assert "session-cancel-race" not in adapter._managed_session_runs
        assert db.get_managed_run_lease("session-cancel-race") is None
        assert adapter._run_statuses[payload["run_id"]]["status"] == "cancelled"
        assert adapter._run_statuses[payload["run_id"]]["output"] == "late result"
        assert db.get_messages_as_conversation("session-cancel-race") == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "late result"},
        ]
        db.close()


# ---------------------------------------------------------------------------
# GET /v1/runs/{run_id} — poll run status
# ---------------------------------------------------------------------------


class TestRunStatus:
    @pytest.mark.asyncio
    async def test_status_completed_run_includes_output_and_usage(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 4
                mock_agent.session_completion_tokens = 2
                mock_agent.session_total_tokens = 6
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                data = await resp.json()
                run_id = data["run_id"]

                for _ in range(20):
                    status_resp = await cli.get(f"/v1/runs/{run_id}")
                    assert status_resp.status == 200
                    status = await status_resp.json()
                    if status["status"] == "completed":
                        break
                    await asyncio.sleep(0.05)

                assert status["status"] == "completed"
                assert status["output"] == "done"
                assert status["usage"]["total_tokens"] == 6
                assert status["last_event"] == "run.completed"

    @pytest.mark.asyncio
    async def test_status_reflects_explicit_session_id(self, adapter, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("space-session", "api_server")
        adapter._session_db = db
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello", "session_id": "space-session"},
                )
                data = await resp.json()
                run_id = data["run_id"]

                for _ in range(20):
                    status_resp = await cli.get(f"/v1/runs/{run_id}")
                    status = await status_resp.json()
                    if status["status"] == "completed":
                        break
                    await asyncio.sleep(0.05)

                mock_agent.run_conversation.assert_called_once()
                assert mock_agent.run_conversation.call_args.kwargs["task_id"] == "space-session"
                assert status["session_id"] == "space-session"
        db.close()

    @pytest.mark.asyncio
    async def test_status_not_found_returns_404(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_nonexistent")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_any")
        assert resp.status == 401


# ---------------------------------------------------------------------------
# GET /v1/runs/{run_id}/events — SSE event stream
# ---------------------------------------------------------------------------


class TestRunEvents:
    @pytest.mark.asyncio
    async def test_image_tool_completed_event_carries_opaque_verified_ref(
        self, adapter, tmp_path, monkeypatch
    ):
        cache = tmp_path / "home" / "cache" / "images"
        cache.mkdir(parents=True)
        image_path = cache / "generated.png"
        image_path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        ))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        run_id = "run-image-1"
        adapter._run_streams[run_id] = asyncio.Queue()
        adapter._run_statuses[run_id] = {"status": "running"}
        _, complete_cb = adapter._make_run_tool_callbacks(
            run_id,
            asyncio.get_running_loop(),
        )

        complete_cb(
            "call-image-1",
            "image_generate",
            {},
            json.dumps({
                "success": True,
                "image": str(image_path),
                "provider": "secret-provider",
                "prompt": "secret prompt",
            }),
        )
        event = await asyncio.wait_for(adapter._run_streams[run_id].get(), timeout=1)

        assert event["structured_result"] == {
            "success": True,
            "image_ref": "generated.png",
            "sha256": "431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460",
        }
        assert event["toolCallId"] == "call-image-1"
        encoded = json.dumps(event)
        assert str(image_path) not in encoded
        assert "data:image" not in encoded
        assert "https://" not in encoded
        assert "secret prompt" not in encoded
        assert "secret-provider" not in encoded

        complete_cb(
            "call-terminal-1",
            "terminal",
            {},
            "CANARY-RAW-TOOL-RESULT",
        )
        terminal_event = await asyncio.wait_for(adapter._run_streams[run_id].get(), timeout=1)
        assert "structured_result" not in terminal_event
        assert "CANARY-RAW-TOOL-RESULT" not in json.dumps(terminal_event)

    @pytest.mark.asyncio
    async def test_structured_tool_callbacks_preserve_ids_for_same_name_calls(self, adapter):
        run_id = "run-tools"
        adapter._run_streams[run_id] = asyncio.Queue()
        adapter._run_statuses[run_id] = {"status": "running"}
        start_cb, complete_cb = adapter._make_run_tool_callbacks(
            run_id,
            asyncio.get_running_loop(),
        )

        start_cb("call-1", "terminal", {"command": "pwd"})
        start_cb("call-2", "terminal", {"command": "ls"})
        complete_cb("call-1", "terminal", {"command": "pwd"}, "ok")
        complete_cb("call-2", "terminal", {"command": "ls"}, "ok")

        events = [
            await asyncio.wait_for(adapter._run_streams[run_id].get(), timeout=1.0)
            for _ in range(4)
        ]
        assert [(event["event"], event["tool_call_id"]) for event in events] == [
            ("tool.started", "call-1"),
            ("tool.started", "call-2"),
            ("tool.completed", "call-1"),
            ("tool.completed", "call-2"),
        ]
        assert events[0]["args"] == {"command": "pwd"}
        assert events[1]["args"] == {"command": "ls"}

    @pytest.mark.asyncio
    async def test_events_stream_returns_completed(self, adapter):
        """Events stream should receive run.completed when agent finishes."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "Hello!"}
                mock_agent.session_prompt_tokens = 10
                mock_agent.session_completion_tokens = 5
                mock_agent.session_total_tokens = 15
                mock_create.return_value = mock_agent

                # Start run
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                # Subscribe to events
                events_resp = await cli.get(f"/v1/runs/{run_id}/events")
                assert events_resp.status == 200
                body = await events_resp.text()

                # Should contain run.completed
                assert "run.completed" in body
                assert "Hello!" in body



    @pytest.mark.asyncio
    async def test_approval_response_without_pending_returns_409(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                data = await resp.json()
                run_id = data["run_id"]

                approval_resp = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once"},
                )
                assert approval_resp.status == 409
                approval_data = await approval_resp.json()
                assert approval_data["error"]["code"] in {
                    "approval_not_active",
                    "approval_not_pending",
                }

    @pytest.mark.asyncio
    async def test_approval_string_false_does_not_resolve_all(self, adapter):
        """Quoted false must not fan out approval resolution across the queue."""
        app = _create_runs_app(adapter)
        run_id = "run_bool_parse"
        adapter._run_statuses[run_id] = {"run_id": run_id, "status": "running"}
        adapter._run_approval_sessions[run_id] = "session-123"

        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
                approval_resp = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once", "all": "false"},
                )

        assert approval_resp.status == 200
        mock_resolve.assert_called_once_with(
            "session-123",
            "once",
            resolve_all=False,
        )

    @pytest.mark.asyncio
    async def test_events_not_found_returns_404(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_nonexistent/events")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_events_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_any/events")
        assert resp.status == 401


# ---------------------------------------------------------------------------
# POST /v1/runs/{run_id}/stop — interrupt a running agent
# ---------------------------------------------------------------------------


class TestStopRun:
    @pytest.mark.asyncio
    async def test_stop_running_agent(self, adapter):
        """Stop should interrupt the agent and cancel the task."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent, agent_ready, _ = _make_slow_agent()
                mock_create.return_value = mock_agent

                # Start run
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                # Wait for agent to start running in the thread
                agent_ready.wait(timeout=3.0)
                await asyncio.sleep(0.1)

                # Verify agent ref is stored
                assert run_id in adapter._active_run_agents

                # Stop the run
                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 200
                stop_data = await stop_resp.json()
                assert stop_data["run_id"] == run_id
                assert stop_data["status"] == "stopping"

                # Agent interrupt should have been called
                mock_agent.interrupt.assert_called_once_with("Stop requested via API")

                status_resp = await cli.get(f"/v1/runs/{run_id}")
                assert status_resp.status == 200
                status_data = await status_resp.json()
                assert status_data["status"] in {"stopping", "cancelled"}

                # Refs should be cleaned up
                await asyncio.sleep(0.5)
                assert run_id not in adapter._active_run_agents
                assert run_id not in adapter._active_run_tasks

    @pytest.mark.asyncio
    async def test_stop_nonexistent_run_returns_404(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs/run_nonexistent/stop")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_stop_requires_auth(self, auth_adapter):
        app = _create_runs_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs/run_any/stop")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_stop_already_completed_run_returns_404(self, adapter):
        """Stopping a run that already finished should return 404 (refs cleaned up)."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent = MagicMock()
                mock_agent.run_conversation.return_value = {"final_response": "done"}
                mock_agent.session_prompt_tokens = 0
                mock_agent.session_completion_tokens = 0
                mock_agent.session_total_tokens = 0
                mock_create.return_value = mock_agent

                # Start and wait for completion
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                await asyncio.sleep(0.3)

                # Run should be done, refs cleaned up
                assert run_id not in adapter._active_run_agents

                # Stop should return 404
                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 404

    @pytest.mark.asyncio
    async def test_stop_interrupt_exception_does_not_crash(self, adapter):
        """If agent.interrupt() raises, stop should still succeed."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent, agent_ready, interrupted = _make_slow_agent()

                # Override the interrupt side_effect to raise. Still trip
                # ``interrupted`` so the slow_run thread unblocks at teardown
                # — without this the agent thread blocks the full 10s
                # timeout and the test teardown waits the same amount.
                def _raising_interrupt(message=None):
                    interrupted.set()
                    raise RuntimeError("interrupt failed")

                mock_agent.interrupt = MagicMock(side_effect=_raising_interrupt)
                mock_create.return_value = mock_agent

                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                agent_ready.wait(timeout=3.0)
                await asyncio.sleep(0.1)

                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 200
                stop_data = await stop_resp.json()
                assert stop_data["status"] == "stopping"

    @pytest.mark.asyncio
    async def test_stop_sends_sentinel_to_events_stream(self, adapter):
        """After stop, the events stream should close."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent") as mock_create:
                mock_agent, agent_ready, _ = _make_slow_agent()
                mock_create.return_value = mock_agent

                # Start run
                resp = await cli.post("/v1/runs", json={"input": "hello"})
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                agent_ready.wait(timeout=3.0)
                await asyncio.sleep(0.1)

                # Subscribe to events in background
                events_task = asyncio.ensure_future(
                    cli.get(f"/v1/runs/{run_id}/events")
                )

                await asyncio.sleep(0.1)

                # Stop the run
                stop_resp = await cli.post(f"/v1/runs/{run_id}/stop")
                assert stop_resp.status == 200

                # Events stream should close
                events_resp = await asyncio.wait_for(events_task, timeout=5.0)
                assert events_resp.status == 200
                body = await events_resp.text()
                # Stream should have received run.failed and closed
                assert "run.failed" in body or "stream closed" in body
