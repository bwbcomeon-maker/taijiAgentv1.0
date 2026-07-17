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
        response = None
        payload = None

        try:
            with patch(
                "gateway.platforms.api_server.asyncio.create_task",
                side_effect=RuntimeError("executor unavailable"),
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
