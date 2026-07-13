from collections import OrderedDict
import base64
import hashlib
from email.message import Message
import json
from pathlib import Path
import re
import urllib.error
import time

import api.config as config
import api.gateway_chat as gateway_chat
import api.models as models
import api.streaming as streaming
from api.turn_journal import append_turn_journal_event, read_turn_journal
from api.config import STREAMS, create_stream_channel
from api.models import new_session
from api.gateway_chat import (
    _gateway_run_request_body,
    _gateway_http_error_event,
    _gateway_sse_error_event,
    _gateway_sse_delta,
    _gateway_stream_usage,
    _gateway_tool_progress_event,
    gateway_chat_config_status,
    webui_chat_backend_mode,
    webui_gateway_chat_enabled,
)


WEBUI_ROOT = Path(__file__).resolve().parents[1]


def _assert_no_public_hermes(value):
    serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    assert "hermes" not in serialized.lower()
    assert "HERMES_" not in serialized
    assert "API_SERVER_KEY" not in serialized


def test_gateway_chat_backend_is_default_off_for_truthy_values():
    for value in (None, "", "1", "true", "yes", "on", "enabled", "runner-local"):
        env = {}
        if value is not None:
            env["HERMES_WEBUI_CHAT_BACKEND"] = value
        assert webui_chat_backend_mode({}, env) == "legacy"
        assert webui_gateway_chat_enabled({}, env) is False


def test_gateway_chat_backend_only_accepts_explicit_gateway_aliases():
    for value in ("gateway", "api_server", "api-server", " Gateway "):
        assert webui_chat_backend_mode({}, {"HERMES_WEBUI_CHAT_BACKEND": value}) == "gateway"
        assert webui_gateway_chat_enabled({}, {"HERMES_WEBUI_CHAT_BACKEND": value}) is True


def test_gateway_chat_backend_can_be_enabled_from_config_without_env():
    assert webui_chat_backend_mode({"webui_chat_backend": "api_server"}, {}) == "gateway"


def test_gateway_chat_config_status_is_redacted_and_reports_missing_key():
    status = gateway_chat_config_status(
        {},
        {
            "HERMES_WEBUI_CHAT_BACKEND": "gateway",
            "HERMES_WEBUI_GATEWAY_BASE_URL": "http://gateway.local",
        },
    )

    assert status == {
        "enabled": True,
        "backend": "gateway",
        "base_url_configured": True,
        "api_key_configured": False,
    }


def test_gateway_chat_config_status_reports_fallback_api_server_key_without_exposing_value():
    status = gateway_chat_config_status(
        {},
        {
            "HERMES_WEBUI_CHAT_BACKEND": "gateway",
            "API_SERVER_KEY": "secret-token",
        },
    )

    assert status["api_key_configured"] is True
    assert "secret-token" not in repr(status)


def test_gateway_chat_backend_env_wins_over_config_and_stays_safe():
    assert webui_chat_backend_mode(
        {"webui_chat_backend": "gateway"},
        {"HERMES_WEBUI_CHAT_BACKEND": "legacy-direct"},
    ) == "legacy"


def test_gateway_sse_delta_extracts_openai_chat_chunks():
    assert _gateway_sse_delta({"choices": [{"delta": {"content": "hel"}}]}) == "hel"
    assert _gateway_sse_delta({"choices": [{"message": {"content": "done"}}]}) == "done"
    assert _gateway_sse_delta({"choices": [{"delta": {}}]}) == ""


def test_gateway_run_request_body_uses_canonical_user_message_array():
    result = _gateway_run_request_body(
        {
            "model": "deepseek-chat",
            "provider": "deepseek",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "prepared visual context"},
            ],
        },
        session_id="session-a",
    )

    assert result["input"] == [
        {"role": "user", "content": "prepared visual context"}
    ]


def test_gateway_stream_usage_normalizes_token_names():
    assert _gateway_stream_usage({"usage": {"prompt_tokens": 7, "completion_tokens": 3}}) == {
        "input_tokens": 7,
        "output_tokens": 3,
        "estimated_cost": 0,
    }
    assert _gateway_stream_usage({"usage": {"input_tokens": 5, "output_tokens": 2, "estimated_cost_usd": 0.01}}) == {
        "input_tokens": 5,
        "output_tokens": 2,
        "estimated_cost": 0.01,
    }
    assert _gateway_stream_usage({}) == {}


def test_gateway_tool_progress_event_translates_gateway_lifecycle_payloads():
    assert _gateway_tool_progress_event(
        {
            "tool": "terminal",
            "label": "terminal: pytest",
            "toolCallId": "call-1",
            "status": "running",
        }
    ) == (
        "tool",
        {
            "event_type": "tool.started",
            "name": "terminal",
            "preview": "terminal: pytest",
            "args": {},
            "is_error": False,
            "tid": "call-1",
        },
    )
    assert _gateway_tool_progress_event(
        {"tool": "terminal", "toolCallId": "call-1", "status": "completed"}
    ) == (
        "tool_complete",
        {
            "event_type": "tool.completed",
            "name": "terminal",
            "preview": None,
            "args": {},
            "is_error": False,
            "tid": "call-1",
        },
    )
    assert _gateway_tool_progress_event({"tool": "_thinking", "status": "running"}) is None


def test_gateway_http_401_reports_gateway_auth_not_provider_key():
    exc = urllib.error.HTTPError(
        "http://gateway.local/v1/chat/completions",
        401,
        "Unauthorized",
        hdrs=Message(),
        fp=None,
    )

    event = _gateway_http_error_event(
        exc,
        '{"error":{"message":"Invalid API key","code":"invalid_api_key"}}',
        api_key_configured=False,
    )

    assert event["label"] == "本地对话服务认证失败"
    assert event["type"] == "gateway_auth_error"
    assert "HTTP 401" in event["message"]
    assert "Invalid API key" not in event["hint"]
    _assert_no_public_hermes(event)


def test_gateway_http_401_with_key_suggests_key_mismatch():
    exc = urllib.error.HTTPError(
        "http://gateway.local/v1/chat/completions",
        401,
        "Unauthorized",
        hdrs=Message(),
        fp=None,
    )

    event = _gateway_http_error_event(exc, "", api_key_configured=True)

    assert event["type"] == "gateway_auth_error"
    assert event["hint"] == "请重启太极智能体，或导出诊断报告后交给管理员排查。"
    _assert_no_public_hermes(event)


def test_gateway_sse_error_event_sanitizes_model_configuration_errors():
    event = _gateway_sse_error_event({
        "error": {
            "message": (
                "Provider 'deepseek' is set in config.yaml but no API key was found. "
                "Set the DEEPSEEK_API_KEY environment variable, or switch to a different "
                "provider with `hermes model`."
            ),
            "code": "model_configuration_error",
        }
    })

    assert event is not None
    assert event["type"] == "model_configuration_error"
    assert "模型服务未配置或不可用" in event["message"]
    _assert_no_public_hermes(event)


def test_frontend_renders_gateway_auth_error_with_specific_label():
    src = (WEBUI_ROOT / "static/messages.js").read_text(encoding="utf-8")
    start = src.find("source.addEventListener('apperror'")
    end = src.find("source.addEventListener('warning'", start)
    assert start != -1 and end != -1, "apperror handler not found"
    block = src[start:end]

    assert "d.type==='gateway_auth_error'" in block
    assert "isGatewayAuthError" in block
    assert "gateway_auth_label" in block
    assert "本地对话服务认证失败" in block
    assert "isGatewayAuthError?(typeof t==='function'?t('gateway_auth_label'):'本地对话服务认证失败'):isAuthMismatch" in block, (
        "Gateway API key failures should use their own label before generic provider mismatch handling."
    )


def test_gateway_auth_label_i18n_key_exists_for_every_locale():
    src = (WEBUI_ROOT / "static/i18n.js").read_text(encoding="utf-8")
    locale_names = [
        match.group("quoted") or match.group("plain")
        for match in re.finditer(
            r"^\s{2}(?:'(?P<quoted>[A-Za-z0-9-]+)'|(?P<plain>[A-Za-z0-9-]+))\s*:\s*\{",
            src,
            re.MULTILINE,
        )
    ]
    assert src.count("gateway_auth_label") >= len(locale_names)


def test_gateway_chat_health_payload_is_documented_as_operator_diagnostic_only():
    # The Gateway-backed-chat operator docs moved out of the README into
    # docs/advanced-chat-setup.md during the v0.51.192 README IA pass (it's a
    # niche self-hosted feature). The contract — that gateway_chat is documented
    # as an operator-only diagnostic, not a user-facing banner — now lives there.
    # CHANGELOG keeps its release-note entry. (Contract test moved with content.)
    advanced = (WEBUI_ROOT / "docs/advanced-chat-setup.md").read_text(encoding="utf-8")
    changelog = (WEBUI_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for text in (advanced, changelog):
        assert "gateway_chat" in text
        assert "operator diagnostic" in text
        assert "not currently rendered as a user-facing health banner" in text


def test_gateway_chat_worker_translates_sse_and_persists_session(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield b'event: hermes.tool.progress\n'
            yield b'data: {"tool":"terminal","label":"terminal: pytest","toolCallId":"call-1","status":"running"}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
            yield b'event: hermes.tool.progress\n'
            yield b'data: {"tool":"terminal","toolCallId":"call-1","status":"completed"}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"lo"}}],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n'
            yield b'data: [DONE]\n\n'

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data.decode("utf-8")
        return FakeResponse()

    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_API_KEY", "secret-token")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda cfg: {"status": "loaded", "source": "test", "label": "test", "message_count": 1, "messages": [{"role": "user", "content": "prefill"}]})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda ctx, cfg: list(ctx["messages"]) + [{"role": "user", "content": "webui session context"}])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)

    s = new_session()
    stream_id = "stream-gateway-test"
    s.active_stream_id = stream_id
    s.pending_user_message = "Say hello"
    s.pending_attachments = []
    s.pending_started_at = time.time()
    s.save()
    submitted = append_turn_journal_event(
        s.session_id,
        {
            "event": "submitted",
            "turn_id": "turn-gateway-test",
            "stream_id": stream_id,
            "role": "user",
            "content": "Say hello",
        },
        session_dir=session_dir,
    )
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        s.session_id,
        "Say hello",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
    )

    saved = models.get_session(s.session_id)
    assert [m["role"] for m in saved.messages] == ["user", "assistant"]
    assert saved.messages[-1]["content"] == "hello"
    assert saved.messages[-1]["_turnDuration"] > 0
    reloaded = models.Session.load(s.session_id)
    assert reloaded.messages[-1]["_turnDuration"] == saved.messages[-1]["_turnDuration"]
    assert isinstance(saved.messages[0]["timestamp"], float)
    assert isinstance(saved.messages[1]["timestamp"], float)
    assert saved.messages[0]["timestamp"] < saved.messages[1]["timestamp"]
    assert saved.active_stream_id is None
    assert stream_id not in STREAMS
    assert captured["url"] == "http://gateway.local/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["X-hermes-session-id"] == s.session_id
    assert captured["headers"]["X-hermes-session-key"] == f"webui:{s.session_id}"
    assert '"stream": true' in captured["body"]
    payload = json.loads(captured["body"])
    expected_system_prompt = f"{gateway_chat.BRAND_PRIVACY_SYSTEM_PROMPT}\n\n{streaming._WEBUI_PROGRESS_PROMPT}"
    assert [m["content"] for m in payload["messages"]] == [
        expected_system_prompt,
        "prefill",
        "webui session context",
        "Say hello",
    ]
    assert payload["messages"][0]["role"] == "system"
    assert "Final visible assistant replies" in payload["messages"][0]["content"]
    assert "Need script" in payload["messages"][0]["content"]
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    assert ("tool", {
        "event_type": "tool.started",
        "name": "terminal",
        "preview": "terminal: pytest",
        "args": {},
        "is_error": False,
        "tid": "call-1",
    }) in events
    assert ("tool_complete", {
        "event_type": "tool.completed",
        "name": "terminal",
        "preview": None,
        "args": {},
        "is_error": False,
        "tid": "call-1",
    }) in events
    done_events = [payload for name, payload in events if name == "done"]
    assert done_events
    assert done_events[-1]["usage"]["duration_seconds"] == saved.messages[-1]["_turnDuration"]
    lifecycle = [
        event
        for event in read_turn_journal(s.session_id, session_dir=session_dir)["events"]
        if event.get("stream_id") == stream_id
    ]
    assert [event["event"] for event in lifecycle] == ["submitted", "assistant_started", "completed"]
    assert all(event["turn_id"] == submitted["turn_id"] for event in lifecycle)
    assistant_index = len(saved.messages) - 1
    for event in lifecycle[1:]:
        assert event["assistant_message_index"] == assistant_index
        assert event["assistant_content_sha256"] == hashlib.sha256(b"hello").hexdigest()
        assert event["user_message_index"] == assistant_index - 1
        assert event["user_content_sha256"] == hashlib.sha256(b"Say hello").hexdigest()


def test_gateway_required_input_helper_survives_optional_prefill_import_failure(
    tmp_path, monkeypatch
):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.delattr(streaming, "_WEBUI_PROGRESS_PROMPT")
    captured = []

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b'data: [DONE]\n\n'

    def fake_urlopen(req, timeout=0):
        captured.append(req.full_url)
        return FakeResponse()

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)
    session = new_session()
    stream_id = "stream-gateway-prefill-import-failure"
    session.active_stream_id = stream_id
    session.pending_user_message = "hello"
    session.pending_started_at = 1.0
    session.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "hello",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
    )

    assert captured == ["http://127.0.0.1:8642/v1/chat/completions"]
    reloaded = models.Session.load(session.session_id)
    assert reloaded.messages[-1]["content"] == "ok"


def test_gateway_chat_worker_maps_sse_error_to_taiji_message(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            payload = {
                "error": {
                    "message": (
                        "Provider 'deepseek' is set in config.yaml but no API key was found. "
                        "Set the DEEPSEEK_API_KEY environment variable, or switch to a different provider "
                        "with `hermes model`."
                    ),
                    "code": "model_configuration_error",
                }
            }
            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
            yield b'data: {"choices":[{"delta":{},"finish_reason":"error"}]}\n\n'
            yield b'data: [DONE]\n\n'

    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda req, timeout=0: FakeResponse())

    s = new_session()
    stream_id = "stream-gateway-error-test"
    s.active_stream_id = stream_id
    s.save()
    append_turn_journal_event(
        s.session_id,
        {"event": "submitted", "turn_id": "turn-gateway-error", "stream_id": stream_id},
        session_dir=session_dir,
    )
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        s.session_id,
        "你好",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
        turn_id="turn-gateway-error",
    )

    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    app_errors = [data for event, data in events if event == "apperror"]
    assert app_errors
    assert app_errors[-1]["type"] == "model_configuration_error"
    assert "模型服务未配置或不可用" in app_errors[-1]["message"]
    _assert_no_public_hermes(app_errors[-1])
    terminal = [
        event for event in read_turn_journal(s.session_id, session_dir=session_dir)["events"]
        if event.get("stream_id") == stream_id and event.get("event") == "interrupted"
    ]
    assert terminal[-1]["turn_id"] == "turn-gateway-error"
    assert terminal[-1]["reason"] == "model_configuration_error"


def test_gateway_chat_worker_forwards_image_attachments_as_multimodal_parts(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())

    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(image_bytes)
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"saw it"}}]}\n\n'
            yield b'data: [DONE]\n\n'

    def fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(config, "get_config", lambda: {"agent": {"image_input_mode": "native"}})
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda cfg: {"status": "not_configured", "source": "none", "label": "", "message_count": 0, "messages": []})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda ctx, cfg: [{"role": "user", "content": "webui session context"}])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)

    s = new_session()
    stream_id = "stream-gateway-image-test"
    s.active_stream_id = stream_id
    s.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        s.session_id,
        "What is in this image?",
        "test-model",
        str(tmp_path),
        stream_id,
        [{"path": str(image_path), "mime": "image/png", "is_image": True}],
    )

    content = captured["body"]["messages"][-1]["content"]
    assert captured["body"]["messages"][0]["role"] == "system"
    assert "Final visible assistant replies" in captured["body"]["messages"][0]["content"]
    assert captured["body"]["messages"][1] == {"role": "user", "content": "webui session context"}
    assert content[0] == {"type": "text", "text": "What is in this image?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_gateway_runs_uses_auxiliary_vision_text_before_main_request(tmp_path, monkeypatch):
    from tools import vision_tools

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    attachment_root = tmp_path / "attachments"
    uploaded_dir = attachment_root / "session-a"
    uploaded_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image_path = uploaded_dir / "photo.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    ))
    cfg = {
        "agent": {"image_input_mode": "auto"},
        "model": {"provider": "deepseek", "default": "deepseek-chat", "supports_vision": False},
        "auxiliary": {"vision": {"provider": "alibaba", "model": "qwen3-vl-plus"}},
    }
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {
        "status": "not_configured", "source": "none", "label": "", "message_count": 0, "messages": [],
    })
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda _ctx, _cfg: [])
    vision_calls = []

    async def fake_vision_analyze_tool(**kwargs):
        vision_calls.append(kwargs)
        return json.dumps({"success": True, "analysis": "a red warning sign"})

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fake_vision_analyze_tool)
    captured = {}

    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"run_id":"remote-vision"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"event":"run.completed","output":"described"}\n\n'

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/v1/runs"):
            captured["run"] = json.loads(req.data.decode("utf-8"))
            return StartResponse()
        assert req.full_url.endswith("/events")
        return EventResponse()

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)
    s = new_session()
    stream_id = "stream-gateway-vision-runs"
    s.active_stream_id = stream_id
    s.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        s.session_id,
        "What is shown?",
        "deepseek-chat",
        str(tmp_path / "workspace"),
        stream_id,
        [{"name": image_path.name, "path": str(image_path), "mime": "image/png", "is_image": True}],
        model_provider="deepseek",
    )

    assert len(vision_calls) == 1
    assert captured["run"]["provider"] == "deepseek"
    assert captured["run"]["model"] == "deepseek-chat"
    content = captured["run"]["input"][0]["content"]
    assert "a red warning sign" in content
    assert "What is shown?" in content
    assert content != "What is shown?"
    assert str(image_path) not in content
    assert "image_url" not in content
    assert "base64" not in content


def test_gateway_blocks_main_request_when_auxiliary_vision_fails(tmp_path, monkeypatch):
    from tools import vision_tools

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    attachment_root = tmp_path / "attachments"
    uploaded_dir = attachment_root / "session-a"
    uploaded_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    old_image_path = uploaded_dir / "old.png"
    image_path = uploaded_dir / "current.png"
    old_image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    ))
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    ))
    cfg = {
        "agent": {"image_input_mode": "text"},
        "auxiliary": {"vision": {"provider": "alibaba", "model": "qwen3-vl-plus"}},
    }
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {
        "status": "not_configured", "source": "none", "label": "", "message_count": 0, "messages": [],
    })
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda _ctx, _cfg: [])

    async def failed_vision(**_kwargs):
        return json.dumps({"success": False, "analysis": "provider secret /private/path"})

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", failed_vision)
    urlopen_calls = []
    monkeypatch.setattr(
        gateway_chat.urllib.request,
        "urlopen",
        lambda *args, **kwargs: urlopen_calls.append((args, kwargs)),
    )
    s = new_session()
    stream_id = "stream-gateway-vision-error"
    old_attachment = {"name": old_image_path.name, "path": str(old_image_path), "mime": "image/png", "is_image": True}
    attachment = {"name": image_path.name, "path": str(image_path), "mime": "image/png", "is_image": True}
    s.messages = [
        {"role": "user", "content": "describe", "attachments": [old_attachment], "timestamp": 1},
        {"role": "assistant", "content": "old answer", "timestamp": 2},
    ]
    s.active_stream_id = stream_id
    s.pending_user_message = "describe"
    s.pending_attachments = [attachment]
    s.pending_started_at = 1.0
    s.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        s.session_id,
        "describe",
        "deepseek-chat",
        str(tmp_path / "workspace"),
        stream_id,
        [attachment],
        model_provider="deepseek",
    )

    assert urlopen_calls == []
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    errors = [data for name, data in events if name == "apperror"]
    assert errors[-1]["type"] == "vision_analysis_error"
    public_error = json.dumps(errors[-1], ensure_ascii=False)
    assert str(image_path) not in public_error
    assert "provider secret" not in public_error
    reloaded = models.Session.load(s.session_id)
    assert [message["role"] for message in reloaded.messages] == ["user", "assistant", "user", "assistant"]
    assert reloaded.messages[2]["content"] == "describe"
    assert reloaded.messages[2]["attachments"] == [attachment]
    assert reloaded.messages[3]["_error"] is True
    assert reloaded.messages[3]["error_type"] == "vision_analysis_error"
    persisted = json.dumps(reloaded.messages, ensure_ascii=False)
    assert "provider secret" not in persisted
    assert str(image_path) not in reloaded.messages[3]["content"]


def test_gateway_cancellation_after_first_auxiliary_image_skips_second_and_main_request(tmp_path, monkeypatch):
    from tools import vision_tools

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    attachment_root = tmp_path / "attachments"
    uploaded_dir = attachment_root / "session-a"
    uploaded_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image_paths = [uploaded_dir / "first.png", uploaded_dir / "second.png"]
    for image_path in image_paths:
        image_path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        ))
    cfg = {
        "agent": {"image_input_mode": "text"},
        "auxiliary": {"vision": {"provider": "alibaba", "model": "qwen3-vl-plus"}},
    }
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {
        "status": "not_configured", "source": "none", "label": "", "message_count": 0, "messages": [],
    })
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda _ctx, _cfg: [])
    stream_id = "stream-gateway-cancel-after-vision"
    vision_calls = []

    async def vision_then_cancel(**kwargs):
        vision_calls.append(kwargs["image_url"])
        gateway_chat.CANCEL_FLAGS[stream_id].set()
        return json.dumps({"success": True, "analysis": "vision finished"})

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", vision_then_cancel)
    urlopen_calls = []
    monkeypatch.setattr(
        gateway_chat.urllib.request,
        "urlopen",
        lambda *args, **kwargs: urlopen_calls.append((args, kwargs)),
    )
    session = new_session()
    session.active_stream_id = stream_id
    session.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "describe",
        "deepseek-chat",
        str(tmp_path),
        stream_id,
        [
            {"name": image_path.name, "path": str(image_path), "mime": "image/png", "is_image": True}
            for image_path in image_paths
        ],
        model_provider="deepseek",
    )

    assert [Path(path).name for path in vision_calls] == ["first.png"]
    assert urlopen_calls == []
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    terminal = [name for name, _data in events if name in {"cancel", "apperror", "error", "done"}]
    assert terminal == ["cancel"]
    assert not any(name == "token" for name, _data in events)


def test_gateway_chat_worker_injects_document_attachment_context(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attachment_root = tmp_path / "attachments"
    uploaded_dir = attachment_root / "session-a"
    uploaded_dir.mkdir(parents=True)
    doc = uploaded_dir / "例子-工具手册.txt"
    doc.write_text("附件正文：这是一份AI公文写作工具手册，包含标题、正文、落款规范。", encoding="utf-8")
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"summary"}}]}\n\n'
            yield b'data: [DONE]\n\n'

    def fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda cfg: {"status": "not_configured", "source": "none", "label": "", "message_count": 0, "messages": []})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda ctx, cfg: [])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)

    s = new_session()
    stream_id = "stream-gateway-doc-test"
    s.active_stream_id = stream_id
    s.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        s.session_id,
        "这份文件主要讲什么？",
        "test-model",
        str(workspace),
        stream_id,
        [{"name": doc.name, "path": str(doc), "mime": "text/plain", "is_image": False}],
    )

    content = captured["body"]["messages"][-1]["content"]
    assert isinstance(content, str)
    assert "[Uploaded file context]" in content
    assert "例子-工具手册.txt" in content
    assert "AI公文写作工具手册" in content
    assert "这份文件主要讲什么？" in content
    assert str(uploaded_dir) not in content
    assert str(doc) not in content


def test_gateway_runs_user_cancel_returns_cancelled_even_with_partial_text(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"run_id":"remote-cancelled"}'

    cancel = gateway_chat.threading.Event()
    partial = "partial " * 40

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield f'data: {json.dumps({"event": "message.delta", "delta": partial})}\n\n'.encode()
            cancel.set()
            yield b'data: {"event":"message.delta","delta":"ignored"}\n\n'

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_stop_gateway_run", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)
    events = []

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local", headers={}, body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-cancel", stream_id="stream-runs-cancel", cancel_event=cancel,
        brand_token_tail=[""], put_gateway_event=lambda name, data: events.append((name, data)),
    )

    assert result["terminal_outcome"] == "cancelled"
    assert result["final_text"]
    assert result["final_text"] in partial
    assert not any(name == "cancel" for name, _data in events)


def test_gateway_runs_server_cancelled_preserves_cancelled_outcome(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"run_id":"remote-server-cancelled"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"event":"run.cancelled"}\n\n'

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local", headers={}, body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-server-cancel", stream_id="stream-runs-server-cancel",
        cancel_event=gateway_chat.threading.Event(), brand_token_tail=[""], put_gateway_event=lambda *_args: None,
    )

    assert result["terminal_outcome"] == "cancelled"
    assert result["error_event"] is None


def test_gateway_runs_partial_eof_is_not_completed(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return b'{"run_id":"remote-truncated"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield f'data: {json.dumps({"event": "message.delta", "delta": "partial " * 40})}\n\n'.encode()

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local", headers={}, body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-truncated", stream_id="stream-runs-truncated",
        cancel_event=gateway_chat.threading.Event(), brand_token_tail=[""], put_gateway_event=lambda *_args: None,
    )

    assert result["final_text"]
    assert result["terminal_outcome"] == "failed"
    assert result["error_event"]["type"] == "gateway_error"
