from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms.api_server import taiji_license


class _FakeSessionDB:
    def get_session(self, session_id):
        return {"id": session_id, "source": "api_server", "title": "Trial"}

    def get_messages_as_conversation(self, session_id):
        return []


def _make_adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(enabled=True, extra={}))


def _create_license_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app["api_server_adapter"] = adapter
    app.router.add_get("/health", adapter._handle_health)
    app.router.add_get("/v1/license/status", adapter._handle_license_status)
    app.router.add_post("/v1/license/activate", adapter._handle_license_activate)
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    app.router.add_post("/v1/responses", adapter._handle_responses)
    app.router.add_post("/v1/runs", adapter._handle_runs)
    app.router.add_post("/api/sessions/{session_id}/chat", adapter._handle_session_chat)
    app.router.add_post("/api/sessions/{session_id}/chat/stream", adapter._handle_session_chat_stream)
    return app


@pytest.fixture()
def required_missing_license(monkeypatch, tmp_path):
    monkeypatch.setenv("TAIJI_LICENSE_REQUIRED", "1")
    monkeypatch.setenv("TAIJI_LICENSE_FILE", str(tmp_path / "missing.jwt"))
    monkeypatch.delenv("TAIJI_LICENSE_PUBLIC_KEY_FILE", raising=False)


@pytest.mark.asyncio
async def test_license_status_is_public_and_health_stays_available(required_missing_license):
    adapter = _make_adapter()
    app = _create_license_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        health = await cli.get("/health")
        assert health.status == 200

        resp = await cli.get("/v1/license/status")
        assert resp.status == 200
        data = await resp.json()

    assert data["status"] == "missing"
    assert data["code"] == "license_missing"
    assert "path" not in data
    assert "token" not in data


@pytest.mark.asyncio
async def test_license_activate_endpoint_is_reserved_and_stable(monkeypatch):
    monkeypatch.setenv("TAIJI_LICENSE_REQUIRED", "1")
    adapter = _make_adapter()
    app = _create_license_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        with patch("gateway.platforms.api_server.taiji_license.require_valid_license") as guard:
            resp = await cli.post("/v1/license/activate", json={"activation_code": "TAIJI-TEST"})
            body = await resp.json()

    assert resp.status == 501
    assert body["error"]["code"] == "license_online_activation_unavailable"
    assert "后续版本" in body["error"]["message"]
    guard.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/v1/chat/completions", {"messages": [{"role": "user", "content": "hello"}]}),
        ("/v1/responses", {"input": "hello"}),
        ("/v1/runs", {"input": "hello"}),
        ("/api/sessions/sess_1/chat", {"message": "hello"}),
        ("/api/sessions/sess_1/chat/stream", {"message": "hello"}),
    ],
)
async def test_execution_endpoints_are_blocked_before_agent_run(required_missing_license, path, payload):
    adapter = _make_adapter()
    adapter._session_db = _FakeSessionDB()
    app = _create_license_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        with patch.object(adapter, "_create_agent") as create_agent, patch.object(
            adapter, "_run_agent", new_callable=AsyncMock
        ) as run_agent:
            create_agent.return_value = MagicMock()
            resp = await cli.post(path, json=payload)
            body = await resp.json()

    assert resp.status == 403
    assert body["error"]["code"] == "license_missing"
    assert "授权" in body["error"]["message"]
    create_agent.assert_not_called()
    run_agent.assert_not_called()


@pytest.mark.asyncio
async def test_clock_rollback_code_is_preserved_before_agent_run(monkeypatch):
    monkeypatch.setenv("TAIJI_LICENSE_REQUIRED", "1")
    adapter = _make_adapter()
    app = _create_license_app(adapter)
    blocked = taiji_license.LicenseStatus(
        status="invalid",
        required=True,
        code="license_clock_rollback",
        message="检测到系统时间异常，请校准本机时间后重试。",
    )

    async with TestClient(TestServer(app)) as cli:
        with patch(
            "gateway.platforms.api_server.taiji_license.require_valid_license",
            return_value=blocked,
        ), patch.object(adapter, "_create_agent") as create_agent:
            resp = await cli.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}]})
            body = await resp.json()

    assert resp.status == 403
    assert body["error"]["code"] == "license_clock_rollback"
    assert "系统时间异常" in body["error"]["message"]
    create_agent.assert_not_called()


@pytest.mark.asyncio
async def test_machine_mismatch_code_is_preserved_before_agent_run(monkeypatch):
    monkeypatch.setenv("TAIJI_LICENSE_REQUIRED", "1")
    adapter = _make_adapter()
    app = _create_license_app(adapter)
    blocked = taiji_license.LicenseStatus(
        status="invalid",
        required=True,
        code="license_machine_mismatch",
        message="授权文件与本机不匹配，请联系服务方重新签发。",
        machine_binding_required=True,
        machine_bound=True,
        machine_matched=False,
        machine_code_short="aaaaaaaaaaaa",
        bound_machine_code_short="bbbbbbbbbbbb",
    )

    async with TestClient(TestServer(app)) as cli:
        with patch(
            "gateway.platforms.api_server.taiji_license.require_valid_license",
            return_value=blocked,
        ), patch.object(adapter, "_create_agent") as create_agent:
            resp = await cli.post("/v1/responses", json={"input": "hello"})
            body = await resp.json()

    assert resp.status == 403
    assert body["error"]["code"] == "license_machine_mismatch"
    assert "不匹配" in body["error"]["message"]
    create_agent.assert_not_called()
