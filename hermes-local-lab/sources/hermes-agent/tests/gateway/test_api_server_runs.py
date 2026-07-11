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
import json
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


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def auth_adapter():
    return _make_adapter(api_key="sk-secret")


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
    async def test_cancelled_async_wrapper_holds_busy_guard_until_worker_exits(
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
                try:
                    await task
                except asyncio.CancelledError:
                    pass

                assert adapter._managed_session_runs.get("session-cancel-race") == payload["run_id"]
                worker_release.set()
                for _ in range(40):
                    if "session-cancel-race" not in adapter._managed_session_runs:
                        break
                    await asyncio.sleep(0.025)

        assert "session-cancel-race" not in adapter._managed_session_runs
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
