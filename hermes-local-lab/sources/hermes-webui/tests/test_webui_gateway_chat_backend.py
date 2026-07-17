from collections import OrderedDict
import base64
import hashlib
from email.message import Message
import io
import json
from pathlib import Path
import re
import threading
import urllib.error
import time

import pytest

import api.config as config
import api.gateway_chat as gateway_chat
import api.models as models
import api.streaming as streaming
from api.turn_journal import append_turn_journal_event, read_turn_journal
from api.config import STREAMS, STREAM_LIVE_TOOL_CALLS, create_stream_channel
from api.models import new_session
from api.gateway_chat import (
    _gateway_run_request_body,
    _gateway_http_error_event,
    _gateway_run_error_event,
    _gateway_sse_error_event,
    _gateway_sse_delta,
    _gateway_sse_reasoning_delta,
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


def test_gateway_sse_reasoning_delta_extracts_reasoning_without_treating_it_as_content():
    assert _gateway_sse_reasoning_delta(
        {"choices": [{"delta": {"reasoning_content": "hidden"}}]}
    ) == "hidden"
    assert _gateway_sse_reasoning_delta(
        {"choices": [{"message": {"reasoning": "final hidden"}}]}
    ) == "final hidden"
    assert _gateway_sse_reasoning_delta(
        {"choices": [{"delta": {"content": "visible"}}]}
    ) == ""


def test_gateway_run_request_body_uses_current_user_content_without_history_replay():
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

    assert result["input"] == "prepared visual context"
    assert result["model"] == "deepseek-chat"
    assert result["provider"] == "deepseek"


@pytest.mark.parametrize(
    "route_fields",
    [
        {"model": "default"},
        {"model": "concrete-model"},
        {"provider": "deepseek"},
        {"model": " default ", "provider": " deepseek "},
        {"model": "concrete-model", "provider": " default "},
    ],
)
def test_gateway_run_request_body_omits_incomplete_model_routes(route_fields):
    result = _gateway_run_request_body(
        {
            **route_fields,
            "messages": [{"role": "user", "content": "hello"}],
        },
        session_id="session-a",
    )

    assert "model" not in result
    assert "provider" not in result


def test_gateway_run_request_body_trims_complete_model_route():
    result = _gateway_run_request_body(
        {
            "model": "  deepseek-chat  ",
            "provider": "  deepseek  ",
            "messages": [{"role": "user", "content": "hello"}],
        },
        session_id="session-a",
    )

    assert result["model"] == "deepseek-chat"
    assert result["provider"] == "deepseek"


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
            "status": "running",
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
            "status": "completed",
            "is_error": False,
            "tid": "call-1",
        },
    )
    assert _gateway_tool_progress_event({"tool": "_thinking", "status": "running"}) is None


def test_gateway_image_candidate_is_private_and_requires_completed_correlated_envelope():
    payload = {
        "tool": "image_generate",
        "toolCallId": "call-image-1",
        "status": "completed",
        "structured_result": {
            "success": True,
            "image_ref": "generated.png",
            "sha256": "a" * 64,
        },
    }
    assert gateway_chat._gateway_image_artifact_candidate(payload) == {
        "tool_name": "image_generate",
        "tool_call_id": "call-image-1",
        "structured_result": {
            "success": True,
            "image_ref": "generated.png",
            "sha256": "a" * 64,
        },
    }
    public = _gateway_tool_progress_event(payload)
    assert public[0] == "tool_complete"
    assert "structured_result" not in public[1]
    assert "/runtime/cache" not in json.dumps(public)
    assert gateway_chat._gateway_image_artifact_candidate({**payload, "toolCallId": ""}) is None
    assert gateway_chat._gateway_image_artifact_candidate({**payload, "tool": "terminal"}) is None
    missing_hash = json.loads(json.dumps(payload))
    missing_hash["structured_result"].pop("sha256")
    assert gateway_chat._gateway_image_artifact_candidate(missing_hash) is None


def test_gateway_chat_completion_promotes_image_to_current_assistant_without_media_text(
    tmp_path, monkeypatch,
):
    session_dir = tmp_path / "sessions"
    state_dir = tmp_path / "web"
    runtime_home = tmp_path / "runtime"
    cache = runtime_home / "cache" / "images"
    session_dir.mkdir()
    cache.mkdir(parents=True)
    image = cache / "生成 图片.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda *_args: [])

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            progress = {
                "tool": "image_generate",
                "toolCallId": "call-image-1",
                "status": "completed",
                "structured_result": {
                    "success": True,
                    "image_ref": image.name,
                    "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                },
            }
            yield b"event: hermes.tool.progress\n"
            yield f"data: {json.dumps(progress)}\n\n".encode()
            yield b'data: {"choices":[{"delta":{"content":"done"}}]}\n\n'
            yield b"data: [DONE]\n\n"

    public_events = []

    class CapturingRunJournal:
        def __init__(self, *_args, **_kwargs): pass
        def append_sse_event(self, event, data):
            public_events.append((event, json.loads(json.dumps(data))))
            return {}

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(gateway_chat, "RunJournalWriter", CapturingRunJournal)
    session = new_session()
    stream_id = "stream-image-artifact"
    session.active_stream_id = stream_id
    session.pending_user_message = "generate"
    session.pending_started_at = time.time()
    session.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "generate",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
        turn_id="turn-image-1",
    )

    saved = models.Session.load(session.session_id)
    assistant = saved.messages[-1]
    assert assistant["content"] == "done"
    assert "MEDIA:" not in assistant["content"]
    assert len(assistant["artifacts"]) == 1
    assert set(assistant["artifacts"][0]) == {
        "artifact_id", "kind", "mime", "name", "size", "sha256", "status"
    }
    reloaded = models.Session.load(session.session_id)
    assert reloaded.messages[-1]["artifacts"] == assistant["artifacts"]
    while not subscriber.empty():
        public_events.append(subscriber.get_nowait())
    public_text = json.dumps(public_events, ensure_ascii=False)
    assert str(image) not in public_text
    assert "structured_result" not in public_text


@pytest.mark.parametrize("failure_mode", ["stale", "commit", "save", "crash"])
def test_gateway_artifact_commit_and_session_save_have_safe_failure_order(
    failure_mode, tmp_path, monkeypatch,
):
    session_dir = tmp_path / "sessions"
    state_dir = tmp_path / "web"
    runtime_home = tmp_path / "runtime"
    cache = runtime_home / "cache" / "images"
    session_dir.mkdir()
    cache.mkdir(parents=True)
    image = cache / "generated.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(runtime_home))
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda *_args: [])

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            progress = {
                "tool": "image_generate",
                "toolCallId": "call-image-cleanup",
                "status": "completed",
                "structured_result": {
                    "success": True,
                    "image_ref": image.name,
                    "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                },
            }
            yield b"event: hermes.tool.progress\n"
            yield f"data: {json.dumps(progress)}\n\n".encode()
            yield b'data: {"choices":[{"delta":{"content":"done"}}]}\n\n'
            yield b"data: [DONE]\n\n"

    monkeypatch.setattr(
        gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse()
    )
    session = new_session()
    stream_id = f"stream-image-cleanup-{failure_mode}"
    session.active_stream_id = stream_id
    session.pending_user_message = "generate"
    session.pending_started_at = time.time()
    session.save()
    STREAMS[stream_id] = create_stream_channel()

    if failure_mode == "stale":
        original_ingest = gateway_chat._ingest_gateway_artifact_candidates

        def ingest_then_stale(*args, **kwargs):
            result = original_ingest(*args, **kwargs)
            session.active_stream_id = "replacement-stream"
            return result

        monkeypatch.setattr(
            gateway_chat, "_ingest_gateway_artifact_candidates", ingest_then_stale
        )
    elif failure_mode == "commit":
        from api.artifacts import ArtifactRegistry

        monkeypatch.setattr(
            ArtifactRegistry,
            "commit_artifacts",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("commit failed")),
        )
    else:
        original_save = models.Session.save

        def fail_artifact_save(self, *args, **kwargs):
            if any(message.get("artifacts") for message in self.messages):
                if failure_mode == "crash":
                    raise SystemExit("simulated process crash after artifact commit")
                raise OSError("session save failed")
            return original_save(self, *args, **kwargs)

        monkeypatch.setattr(models.Session, "save", fail_artifact_save)

    run = lambda: gateway_chat._run_gateway_chat_streaming(
        session.session_id, "generate", "test-model", str(tmp_path),
        stream_id, [], turn_id=f"turn-image-cleanup-{failure_mode}",
    )
    if failure_mode == "crash":
        with pytest.raises(SystemExit, match="simulated process crash"):
            run()
    else:
        run()

    manifest = json.loads(
        (state_dir / "artifacts" / session.session_id / "manifest.json").read_text("utf-8")
    )
    reloaded = models.Session.load(session.session_id)
    cached = models.SESSIONS.get(session.session_id)
    assert all(not message.get("artifacts") for message in reloaded.messages)
    assert cached is None or all(not message.get("artifacts") for message in cached.messages)
    if failure_mode in {"save", "crash"}:
        assert len(manifest["artifacts"]) == 1
        assert manifest["artifacts"][0]["commit_state"] == "committed"
        assert len(list((state_dir / "artifacts" / session.session_id).glob("*.png"))) == 1
    else:
        assert manifest["artifacts"] == []
        assert not list((state_dir / "artifacts" / session.session_id).glob("*.png"))


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


def test_gateway_public_errors_do_not_echo_provider_secrets_paths_or_raw_exceptions():
    raw = "provider exploded sk-abcdefghijklmnopqrstuvwxyz at /private/provider/model.py"
    exc = urllib.error.HTTPError(
        "http://gateway.local/v1/runs",
        503,
        raw,
        hdrs=Message(),
        fp=None,
    )

    events = [
        _gateway_http_error_event(exc, raw, api_key_configured=True),
        gateway_chat._gateway_run_error_event({"error": raw}, raw),
    ]

    for event in events:
        serialized = json.dumps(event, ensure_ascii=False)
        assert "provider exploded" not in serialized
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in serialized
        assert "/private/provider/model.py" not in serialized
        assert "HTTP 503" in serialized or "暂时不可用" in serialized


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
    prior_messages = [
        {"role": "user", "content": "Earlier question", "timestamp": 1.0},
        {"role": "assistant", "content": "Earlier answer", "timestamp": 2.0},
    ]
    s.messages = list(prior_messages)
    s.context_messages = list(prior_messages)
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
    from api.turn_envelope import TurnEnvelope

    placeholder_envelope = TurnEnvelope.create(
        turn_id="turn-gateway-test",
        session_id=s.session_id,
        submitted_at=s.pending_started_at,
        display_user_message="Say hello",
        model_messages=[{"role": "user", "content": "placeholder only"}],
        attachments=[],
    )
    effective_envelopes = []
    original_with_model_messages = TurnEnvelope.with_model_messages

    def capture_effective(self, messages):
        effective = original_with_model_messages(self, messages)
        effective_envelopes.append(effective)
        return effective

    monkeypatch.setattr(TurnEnvelope, "with_model_messages", capture_effective)

    gateway_chat._run_gateway_chat_streaming(
        s.session_id,
        "Say hello",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
        turn_envelope=placeholder_envelope,
    )

    saved = models.get_session(s.session_id)
    assert [m["role"] for m in saved.messages] == [
        "user", "assistant", "user", "assistant",
    ]
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
    assert payload["messages"] == list(effective_envelopes[-1].model_messages)
    assert "placeholder only" not in str(payload["messages"])
    expected_system_prompt = f"{gateway_chat.BRAND_PRIVACY_SYSTEM_PROMPT}\n\n{streaming._WEBUI_PROGRESS_PROMPT}"
    assert [m["content"] for m in payload["messages"]] == [
        expected_system_prompt,
        "prefill",
        "webui session context",
        "Earlier question",
        "Earlier answer",
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
        "status": "running",
        "is_error": False,
        "tid": "call-1",
    }) in events
    assert ("tool_complete", {
        "event_type": "tool.completed",
        "name": "terminal",
        "status": "completed",
        "is_error": False,
        "tid": "call-1",
    }) in events
    assert not any("args" in data or "preview" in data for name, data in events if name in {"tool", "tool_complete"})
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


def test_gateway_chat_completions_buffers_reasoning_until_done(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda *_args: [])

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"This runtime uses Her"}}]}\n\n'
            assert not [item for item in list(subscriber.queue) if item[0] == "reasoning"]
            yield b'data: {"choices":[{"delta":{"reasoning_content":"mes Agent via run_agent.py"}}]}\n\n'
            assert not [item for item in list(subscriber.queue) if item[0] == "reasoning"]
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b'data: [DONE]\n\n'

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    s = new_session()
    stream_id = "stream-gateway-chat-reasoning"
    s.active_stream_id = stream_id
    s.pending_user_message = "q"
    s.pending_attachments = []
    s.pending_started_at = time.time()
    s.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        s.session_id, "q", "test-model", str(tmp_path), stream_id, []
    )

    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    reasoning_events = [data["text"] for name, data in events if name == "reasoning"]
    assert len(reasoning_events) == 1
    assert reasoning_events[0] == streaming._finalize_public_reasoning(
        "This runtime uses Hermes Agent via run_agent.py"
    )
    assert "hermes" not in reasoning_events[0].lower()
    assert "run_agent.py" not in reasoning_events[0]


@pytest.mark.parametrize("terminal", ["cancel", "failed", "eof"])
def test_gateway_chat_completions_discards_incomplete_reasoning(
    terminal, tmp_path, monkeypatch
):
    session_dir = tmp_path / terminal / "sessions"
    session_dir.mkdir(parents=True)
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda *_args: [])

    stream_id = f"stream-gateway-chat-reasoning-{terminal}"

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"incomplete sensitive reasoning"}}]}\n\n'
            if terminal == "cancel":
                gateway_chat.CANCEL_FLAGS[stream_id].set()
                yield b'data: {"choices":[{"delta":{"content":"ignored"}}]}\n\n'
            elif terminal == "failed":
                yield b'data: {"error":{"type":"provider_error","message":"failed"}}\n\n'
                yield b'data: [DONE]\n\n'
            else:
                yield b'data: {"choices":[{"delta":{"content":"partial visible"}}]}\n\n'

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    session = new_session()
    session.active_stream_id = stream_id
    session.pending_user_message = "q"
    session.pending_attachments = []
    session.pending_started_at = time.time()
    session.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        session.session_id, "q", "test-model", str(tmp_path), stream_id, []
    )

    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    assert not [item for item in events if item[0] == "reasoning"]


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


def test_gateway_final_save_validates_visible_assistant_but_keeps_internal_context(
    tmp_path,
    monkeypatch,
):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "chat_completions")
    raw_user = "ordinary user /tmp/customer/input.txt"
    raw_content = (
        "业务回复：内部配置项 HERMES_WEBUI_PORT=8765，"
        "Authorization: Bearer sk-gateway-internal-canary，随后继续。"
    )
    journal_events = []

    class CapturingRunJournal:
        def __init__(self, *_args, **_kwargs):
            pass

        def append_sse_event(self, event, data):
            journal_events.append((event, json.loads(json.dumps(data))))
            return {}

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield f'data: {json.dumps({"choices": [{"delta": {"content": raw_content}}]})}\n\n'.encode()
            yield b'data: [DONE]\n\n'

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(gateway_chat, "RunJournalWriter", CapturingRunJournal)
    session = new_session()
    stream_id = "stream-gateway-visible-assistant-gate"
    session.active_stream_id = stream_id
    session.pending_user_message = raw_user
    session.pending_started_at = 1.0
    session.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        raw_user,
        "test-model",
        str(tmp_path),
        stream_id,
        [],
    )

    saved = models.Session.load(session.session_id)
    assert saved.messages[-2]["content"] == raw_user
    assert saved.messages[-1]["content"] != raw_content
    assert "HERMES_WEBUI_PORT" not in saved.messages[-1]["content"]
    assert "sk-gateway-internal-canary" not in saved.messages[-1]["content"]
    assert saved.context_messages[-1]["content"] == raw_content
    public_events = []
    while not subscriber.empty():
        public_events.append(subscriber.get_nowait())
    public_serialized = json.dumps([public_events, journal_events], ensure_ascii=False)
    assert "HERMES_WEBUI_PORT" not in public_serialized
    assert "sk-gateway-internal-canary" not in public_serialized


def test_gateway_runs_accumulates_raw_internal_and_filtered_public_final_text(monkeypatch):
    raw_content = "HERMES_WEBUI_PORT=8765 Authorization: Bearer sk-runs-internal-canary"

    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-public-internal","session_id":"sid-runs-public-internal"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield f'data: {json.dumps({"event": "message.delta", "delta": raw_content})}\n\n'.encode()
            yield f'data: {json.dumps({"event": "run.completed", "output": raw_content})}\n\n'.encode()

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)
    events = []

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-public-internal",
        stream_id="stream-runs-public-internal",
        cancel_event=gateway_chat.threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: events.append((name, data)),
    )

    assert result["raw_final_text"] == raw_content
    assert result["public_final_text"] == result["final_text"]
    assert result["public_final_text"] != raw_content
    public_serialized = json.dumps(events, ensure_ascii=False)
    assert "HERMES_WEBUI_PORT" not in public_serialized
    assert "sk-runs-internal-canary" not in public_serialized


def test_gateway_runs_short_buffered_delta_is_not_duplicated_by_completed_output(monkeypatch):
    short_reply = "Worktree 会话已通过本地确定性模型完成并持久化。"

    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-short-buffered","session_id":"sid-runs-short-buffered"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield f'data: {json.dumps({"event": "message.delta", "delta": short_reply})}\n\n'.encode()
            yield f'data: {json.dumps({"event": "run.completed", "output": short_reply})}\n\n'.encode()

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)
    events = []

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-short-buffered",
        stream_id="stream-runs-short-buffered",
        cancel_event=gateway_chat.threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: events.append((name, data)),
    )

    assert result["raw_final_text"] == short_reply
    assert result["public_final_text"] == short_reply
    assert "".join(data["text"] for name, data in events if name == "token") == short_reply


def test_gateway_runs_collects_private_image_candidate_without_public_path(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-image","session_id":"sid-image"}'

    image_ref = "generated.png"

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            completed = {
                "event": "tool.completed",
                "tool": "image_generate",
                "toolCallId": "call-image-runs",
                "structured_result": {
                    "success": True, "image_ref": image_ref, "sha256": "b" * 64,
                },
            }
            yield f"data: {json.dumps(completed)}\n\n".encode()
            yield b'data: {"event":"run.completed","output":"done"}\n\n'

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)
    public_events = []

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-image",
        stream_id="stream-image",
        cancel_event=gateway_chat.threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: public_events.append((name, data)),
    )

    assert result["artifact_candidates"] == [{
        "tool_name": "image_generate",
        "tool_call_id": "call-image-runs",
        "structured_result": {
            "success": True, "image_ref": image_ref, "sha256": "b" * 64,
        },
    }]
    serialized = json.dumps(public_events)
    assert "/runtime/cache/images" not in serialized
    assert "structured_result" not in serialized


def test_failed_runs_turn_never_promotes_collected_image_candidate(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "runs")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda _cfg: {})
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda *_args: [])

    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return json.dumps({
                "run_id": "remote-failed-image",
                "session_id": session.session_id,
            }).encode("utf-8")

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            completed = {
                "event": "tool.completed",
                "tool": "image_generate",
                "toolCallId": "call-image-failed",
                "structured_result": {
                    "success": True,
                    "image": "/runtime/cache/images/should-not-register.png",
                },
            }
            yield f"data: {json.dumps(completed)}\n\n".encode()
            yield b'data: {"event":"run.failed","error":"model failed"}\n\n'

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)
    promoted = []
    monkeypatch.setattr(
        gateway_chat,
        "_ingest_gateway_artifact_candidates",
        lambda *_args, **_kwargs: promoted.append((_args, _kwargs)),
    )
    session = new_session()
    stream_id = "stream-failed-image"
    session.active_stream_id = stream_id
    session.pending_user_message = "generate"
    session.pending_started_at = time.time()
    session.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "generate",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
        turn_id="turn-failed-image",
    )

    assert promoted == []
    reloaded = models.Session.load(session.session_id)
    assert not any(message.get("artifacts") for message in reloaded.messages)


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
    attachment_root = tmp_path / "attachments"
    uploaded_dir = attachment_root / s.session_id
    uploaded_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image_path = uploaded_dir / "photo.png"
    image_path.write_bytes(image_bytes)
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
        [{"name": image_path.name, "ref": image_path.name, "mime": "image/png", "is_image": True}],
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
    monkeypatch.setattr(
        gateway_chat,
        "_ensure_gateway_managed_session",
        lambda **_kwargs: "session-a",
    )
    vision_calls = []

    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"

    async def fake_vision_analyze_tool(**kwargs):
        vision_calls.append(kwargs)
        return json.dumps({
            "success": True,
            "analysis": f"a red warning sign {secret} /Users/alice/private/image.png",
        })

    monkeypatch.setattr(vision_tools, "vision_analyze_tool", fake_vision_analyze_tool)
    captured = {}

    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-vision","session_id":"session-a"}'

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
    s = models.Session(session_id="session-a")
    models.SESSIONS[s.session_id] = s
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
        [{"name": image_path.name, "ref": image_path.name, "mime": "image/png", "is_image": True}],
        model_provider="deepseek",
    )

    assert len(vision_calls) == 1
    assert captured["run"]["provider"] == "deepseek"
    assert captured["run"]["model"] == "deepseek-chat"
    content = captured["run"]["input"]
    assert "a red warning sign" in content
    assert secret not in content
    assert "/Users/alice/private/image.png" not in content
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
    s = models.Session(session_id="session-a")
    models.SESSIONS[s.session_id] = s
    stream_id = "stream-gateway-vision-error"
    old_attachment = {"name": old_image_path.name, "ref": old_image_path.name, "mime": "image/png", "is_image": True}
    attachment = {"name": image_path.name, "ref": image_path.name, "mime": "image/png", "is_image": True}
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
    session = models.Session(session_id="session-a")
    models.SESSIONS[session.session_id] = session
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
            {"name": image_path.name, "ref": image_path.name, "mime": "image/png", "is_image": True}
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

    s = models.Session(session_id="session-a")
    models.SESSIONS[s.session_id] = s
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
        [{"name": doc.name, "ref": doc.name, "mime": "text/plain", "is_image": False}],
    )

    content = captured["body"]["messages"][-1]["content"]
    assert isinstance(content, str)
    assert "[Uploaded file context]" in content
    assert "例子-工具手册.txt" in content
    assert "AI公文写作工具手册" in content
    assert "这份文件主要讲什么？" in content
    assert str(uploaded_dir) not in content
    assert str(doc) not in content


def test_gateway_runs_request_uses_only_current_input_and_instructions():
    body = {
        "model": "test-model",
        "provider": "test-provider",
        "messages": [
            {"role": "system", "content": "product instructions"},
            {"role": "user", "content": "stored question"},
            {"role": "assistant", "content": "stored answer"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "current question"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AA=="},
                    },
                ],
            },
        ],
    }

    payload = _gateway_run_request_body(body, session_id="managed-session")

    assert payload == {
        "model": "test-model",
        "provider": "test-provider",
        "input": body["messages"][-1]["content"],
        "session_id": "managed-session",
        "instructions": "product instructions",
    }
    assert "conversation_history" not in payload
    assert "messages" not in payload


def test_gateway_runs_keeps_ephemeral_user_prefill_without_replaying_history():
    ephemeral = [
        {"role": "system", "content": "product instructions"},
        {"role": "user", "content": "recall context from notes"},
    ]
    body = {
        "model": "test-model",
        "messages": [
            *ephemeral,
            {"role": "user", "content": "stored question"},
            {"role": "assistant", "content": "stored answer"},
            {"role": "user", "content": "current question"},
        ],
    }

    payload = _gateway_run_request_body(
        body,
        session_id="managed-session",
        ephemeral_messages=ephemeral,
    )

    assert payload["input"] == "current question"
    assert "product instructions" in payload["instructions"]
    assert "role=user" in payload["instructions"]
    assert "recall context from notes" in payload["instructions"]
    assert "stored question" not in payload["instructions"]
    assert "stored answer" not in payload["instructions"]
    assert "conversation_history" not in payload


def test_gateway_run_error_preserves_model_configuration_classification():
    event = _gateway_run_error_event({
        "event": "run.failed",
        "error": {
            "message": "Set DEEPSEEK_API_KEY in the environment",
            "code": "model_configuration_error",
        },
    })

    assert event["type"] == "model_configuration_error"
    assert "模型服务未配置或不可用" in event["message"]
    _assert_no_public_hermes(event)


@pytest.mark.parametrize(
    ("terminal_event", "expected_local_event", "expected_error_type"),
    [
        ({"event": "run.cancelled"}, None, None),
        (
            {
                "event": "run.failed",
                "error": {
                    "message": "Set DEEPSEEK_API_KEY in the environment",
                    "code": "model_configuration_error",
                },
            },
            None,
            "model_configuration_error",
        ),
    ],
)
def test_gateway_runs_terminal_events_keep_cancel_and_error_semantics(
    monkeypatch, terminal_event, expected_local_event, expected_error_type
):
    class FakeResponse:
        def __init__(self, payload=None, lines=None):
            self.payload = payload or {}
            self.lines = lines or []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

        def __iter__(self):
            yield from self.lines

    responses = iter([
        FakeResponse({"run_id": "run-terminal", "session_id": "session-terminal"}),
        FakeResponse(lines=[
            f"data: {json.dumps(terminal_event)}\n\n".encode("utf-8"),
        ]),
    ])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda req, timeout=0: next(responses))
    monkeypatch.setattr(gateway_chat, "update_active_run", lambda *args, **kwargs: None)
    emitted = []

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "test", "messages": [{"role": "user", "content": "hello"}]},
        session_id="session-terminal",
        stream_id="stream-terminal",
        cancel_event=threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, payload: emitted.append((name, payload)),
    )

    if expected_local_event:
        assert any(name == expected_local_event for name, _ in emitted)
    else:
        assert not any(name == "cancel" for name, _ in emitted)
    if expected_error_type:
        assert result["error_event"]["type"] == expected_error_type
    else:
        assert result["error_event"] is None
    if terminal_event["event"] == "run.cancelled":
        assert result["cancelled"] is True


def test_gateway_runs_stops_orphan_when_started_session_id_does_not_match(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "run_id": "run-orphan",
                "session_id": "unexpected-session",
            }).encode("utf-8")

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda req, timeout=0: FakeResponse())
    stopped = []
    monkeypatch.setattr(
        gateway_chat,
        "_stop_gateway_run",
        lambda base_url, headers, run_id: stopped.append((base_url, run_id)),
    )
    monkeypatch.setattr(
        gateway_chat,
        "_clear_gateway_run_approvals_from_webui",
        lambda session_id, run_id: None,
    )

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "test", "messages": [{"role": "user", "content": "hello"}]},
        session_id="expected-session",
        stream_id="stream-orphan",
        cancel_event=threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, payload: None,
    )

    assert stopped == [("http://gateway.local", "run-orphan")]
    assert result["error_event"]["type"] == "gateway_error"


def test_gateway_runs_same_name_tool_completion_matches_stable_id(monkeypatch):
    class FakeResponse:
        def __init__(self, payload=None, lines=None):
            self.payload = payload or {}
            self.lines = lines or []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

        def __iter__(self):
            yield from self.lines

    responses = iter([
        FakeResponse({"run_id": "run-tools", "session_id": "session-tools"}),
        FakeResponse(lines=[
            b'data: {"event":"tool.started","tool":"terminal","tool_call_id":"call-1","args":{"command":"one"}}\n\n',
            b'data: {"event":"tool.started","tool":"terminal","tool_call_id":"call-2","args":{"command":"two"}}\n\n',
            b'data: {"event":"tool.completed","tool":"terminal","tool_call_id":"call-1","error":false}\n\n',
            b'data: {"event":"run.completed","output":"done","usage":{}}\n\n',
        ]),
    ])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda req, timeout=0: next(responses))
    monkeypatch.setattr(gateway_chat, "update_active_run", lambda *args, **kwargs: None)
    stream_id = "stream-same-name-tools"
    STREAM_LIVE_TOOL_CALLS[stream_id] = []
    try:
        gateway_chat._stream_gateway_run_events(
            base_url="http://gateway.local",
            headers={},
            body={"model": "test", "messages": [{"role": "user", "content": "hello"}]},
            session_id="session-tools",
            stream_id=stream_id,
            cancel_event=threading.Event(),
            brand_token_tail=[""],
            put_gateway_event=lambda name, payload: None,
        )

        by_id = {item["tid"]: item for item in STREAM_LIVE_TOOL_CALLS[stream_id]}
        assert by_id["call-1"]["done"] is True
        assert by_id["call-2"]["done"] is False
    finally:
        STREAM_LIVE_TOOL_CALLS.pop(stream_id, None)


def test_gateway_live_tool_completion_with_id_never_falls_back_to_name():
    stream_id = "stream-tool-id-strict"
    STREAM_LIVE_TOOL_CALLS[stream_id] = [
        {"name": "terminal", "tid": "call-1", "done": False},
        {"name": "terminal", "tid": "call-2", "done": False},
    ]
    try:
        gateway_chat._mark_gateway_live_tool_complete(
            stream_id,
            tool_name="terminal",
            tool_call_id="call-1",
            is_error=False,
        )

        by_id = {item["tid"]: item for item in STREAM_LIVE_TOOL_CALLS[stream_id]}
        assert by_id["call-1"]["done"] is True
        assert by_id["call-2"]["done"] is False
    finally:
        STREAM_LIVE_TOOL_CALLS.pop(stream_id, None)


def test_gateway_run_incomplete_event_stream_stops_run_and_returns_error(monkeypatch):
    class FakeResponse:
        def __init__(self, payload=None, lines=None):
            self.payload = payload or {}
            self.lines = lines or []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

        def __iter__(self):
            yield from self.lines

    responses = iter([
        FakeResponse({"run_id": "run-incomplete", "session_id": "session-incomplete"}),
        FakeResponse(lines=[b'data: {"event":"message.delta","delta":"partial"}\n\n']),
    ])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda req, timeout=0: next(responses))
    monkeypatch.setattr(gateway_chat, "update_active_run", lambda *args, **kwargs: None)
    stopped = []
    monkeypatch.setattr(
        gateway_chat,
        "_stop_gateway_run",
        lambda base_url, headers, run_id: stopped.append(run_id),
    )
    monkeypatch.setattr(
        gateway_chat,
        "_clear_gateway_run_approvals_from_webui",
        lambda session_id, run_id: None,
    )

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "test", "messages": [{"role": "user", "content": "hello"}]},
        session_id="session-incomplete",
        stream_id="stream-incomplete",
        cancel_event=threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, payload: None,
    )

    assert stopped == ["run-incomplete"]
    assert result["error_event"]["type"] == "gateway_error"


def test_gateway_runs_fallback_only_when_start_endpoint_is_unavailable():
    session_endpoint_unavailable = urllib.error.HTTPError(
        "http://gateway.local/api/sessions",
        404,
        "Not Found",
        hdrs=Message(),
        fp=io.BytesIO(b'{"error":{"message":"Not Found"}}'),
    )
    unavailable = urllib.error.HTTPError(
        "http://gateway.local/v1/runs",
        404,
        "Not Found",
        hdrs=Message(),
        fp=io.BytesIO(b'{"error":{"message":"Not Found"}}'),
    )
    managed_session_missing = urllib.error.HTTPError(
        "http://gateway.local/v1/runs",
        404,
        "Not Found",
        hdrs=Message(),
        fp=io.BytesIO(b'{"error":{"message":"Session not found","code":"session_not_found"}}'),
    )
    event_stream_missing = urllib.error.HTTPError(
        "http://gateway.local/v1/runs/run-1/events",
        404,
        "Not Found",
        hdrs=Message(),
        fp=io.BytesIO(b'{"error":{"message":"Run not found","code":"run_not_found"}}'),
    )

    assert gateway_chat._gateway_run_fallback_allowed(session_endpoint_unavailable) is True
    assert gateway_chat._gateway_run_fallback_allowed(unavailable) is True
    assert gateway_chat._gateway_run_fallback_allowed(managed_session_missing) is False
    assert gateway_chat._gateway_run_fallback_allowed(event_stream_missing) is False
    # The classifier may inspect the response body, but outer HTTP error
    # handling still needs that body for a useful user-facing diagnostic.
    assert gateway_chat._gateway_http_error_body(managed_session_missing) == (
        '{"error":{"message":"Session not found","code":"session_not_found"}}'
    )


def test_gateway_runs_compatibility_fallback_sends_full_webui_history(
    tmp_path, monkeypatch
):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "runs")
    monkeypatch.setattr(
        streaming,
        "_load_webui_prefill_context",
        lambda cfg: {
            "status": "not_configured",
            "source": "none",
            "label": "",
            "message_count": 0,
            "messages": [],
        },
    )
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda ctx, cfg: [])
    captured = {"urls": [], "chat_body": None}

    class FakeChatResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"new answer"}}]}\n\n'
            yield b'data: [DONE]\n\n'

    def fake_urlopen(req, timeout=0):
        captured["urls"].append(req.full_url)
        if req.full_url.endswith("/api/sessions"):
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "Not Found",
                hdrs=Message(),
                fp=io.BytesIO(b'{"error":{"message":"Not Found"}}'),
            )
        if req.full_url.endswith("/v1/chat/completions"):
            captured["chat_body"] = json.loads(req.data.decode("utf-8"))
            return FakeChatResponse()
        raise AssertionError(f"unexpected URL: {req.full_url}")

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)
    session = new_session()
    prior = [
        {"role": "user", "content": "old question", "timestamp": 1.0},
        {"role": "assistant", "content": "old answer", "timestamp": 2.0},
    ]
    session.messages = list(prior)
    session.context_messages = list(prior)
    stream_id = "stream-runs-fallback"
    session.active_stream_id = stream_id
    session.pending_user_message = "new question"
    session.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "new question",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
    )

    assert captured["urls"] == [
        "http://gateway.local/api/sessions",
        "http://gateway.local/v1/chat/completions",
    ]
    visible_messages = [
        message
        for message in captured["chat_body"]["messages"]
        if message["role"] != "system"
    ]
    assert "checkpoint_content" not in captured["chat_body"]
    assert visible_messages == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
    ]
    saved = models.get_session(session.session_id)
    assert [message["content"] for message in saved.messages] == [
        "old question",
        "old answer",
        "new question",
        "new answer",
    ]


@pytest.mark.parametrize(
    ("model", "model_provider", "expected_route"),
    [
        ("test-model", None, {}),
        (
            "deepseek-chat",
            "deepseek",
            {"model": "deepseek-chat", "provider": "deepseek"},
        ),
    ],
)
def test_gateway_runs_transport_creates_managed_session_and_matches_visible_events(
    tmp_path, monkeypatch, model, model_provider, expected_route
):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_API_KEY", "secret-token")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "runs")
    monkeypatch.setattr(
        streaming,
        "_load_webui_prefill_context",
        lambda cfg: {
            "status": "not_configured",
            "source": "none",
            "label": "",
            "message_count": 0,
            "messages": [],
        },
    )
    monkeypatch.setattr(
        streaming,
        "_prefill_messages_with_webui_context",
        lambda ctx, cfg: [],
    )
    captured = {"urls": [], "bodies": {}}

    class FakeResponse:
        def __init__(self, payload=None, lines=None, headers=None):
            self.payload = payload or {}
            self.lines = lines or []
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

        def __iter__(self):
            yield from self.lines

    def fake_urlopen(req, timeout=0):
        captured["urls"].append(req.full_url)
        if req.data:
            captured["bodies"][req.full_url] = json.loads(req.data.decode("utf-8"))
        if req.full_url.endswith("/api/sessions"):
            return FakeResponse({
                "object": "hermes.session",
                "session": {"id": session.session_id},
            })
        if req.full_url.endswith("/v1/runs"):
            return FakeResponse({
                "run_id": "run-managed",
                "session_id": session.session_id,
                "status": "started",
            })
        if req.full_url.endswith("/v1/runs/run-managed/events"):
            return FakeResponse(lines=[
                b'data: {"event":"message.delta","delta":"hello"}\n\n',
                b'data: {"event":"tool.started","tool":"terminal","tool_call_id":"call-1","preview":"terminal: pwd","args":{"command":"pwd"}}\n\n',
                b'data: {"event":"tool.completed","tool":"terminal","tool_call_id":"call-1","duration":0.1,"error":false}\n\n',
                b'data: {"event":"run.completed","output":"hello","usage":{"input_tokens":4,"output_tokens":2,"total_tokens":6}}\n\n',
            ])
        raise AssertionError(f"unexpected URL: {req.full_url}")

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)
    session = new_session()
    stream_id = "stream-managed-runs"
    session.active_stream_id = stream_id
    session.pending_user_message = "Say hello"
    session.pending_attachments = []
    session.pending_started_at = 123
    session.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "Say hello",
        model,
        str(tmp_path),
        stream_id,
        [],
        model_provider=model_provider,
    )

    run_body = captured["bodies"]["http://gateway.local/v1/runs"]
    assert run_body["session_id"] == session.session_id
    assert run_body["input"] == "Say hello"
    assert {
        key: run_body[key]
        for key in ("model", "provider")
        if key in run_body
    } == expected_route
    assert "conversation_history" not in run_body
    assert "messages" not in run_body
    assert captured["urls"][:2] == [
        "http://gateway.local/api/sessions",
        "http://gateway.local/v1/runs",
    ]
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    assert any(name == "token" and data["text"] == "hello" for name, data in events)
    assert any(
        name == "tool"
        and data["name"] == "terminal"
        and data["tid"] == "call-1"
        for name, data in events
    )
    assert any(name == "tool_complete" and data["name"] == "terminal" for name, data in events)
    done = [data for name, data in events if name == "done"][-1]
    assert done["usage"]["input_tokens"] == 4
    assert done["usage"]["output_tokens"] == 2
    saved = models.get_session(session.session_id)
    assert [message["role"] for message in saved.messages] == ["user", "assistant"]
    assert saved.messages[-1]["content"] == "hello"


def test_gateway_runs_cancelled_result_uses_cancel_path_without_success_or_empty_error(
    tmp_path, monkeypatch
):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "runs")
    monkeypatch.setattr(
        streaming,
        "_load_webui_prefill_context",
        lambda cfg: {
            "status": "not_configured",
            "source": "none",
            "label": "",
            "message_count": 0,
            "messages": [],
        },
    )
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda ctx, cfg: [])
    monkeypatch.setattr(gateway_chat, "_ensure_gateway_managed_session", lambda **kwargs: "managed")
    monkeypatch.setattr(
        gateway_chat,
        "_stream_gateway_run_events",
        lambda **kwargs: {
            "final_text": "",
            "usage": {},
            "error_event": None,
            "cancelled": True,
        },
    )
    session = new_session()
    stream_id = "stream-managed-cancelled"
    session.active_stream_id = stream_id
    session.pending_user_message = "cancel this"
    session.pending_started_at = 123.0
    session.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "cancel this",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
    )

    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    assert any(name == "cancel" for name, _ in events)
    assert not any(name in {"apperror", "done"} for name, _ in events)
    saved = models.get_session(session.session_id)
    assert saved.active_stream_id is None
    assert [message["role"] for message in saved.messages] == ["user", "assistant"]
    assert saved.messages[-1].get("_error") is True
    assert "cancel" in saved.messages[-1]["content"].lower()


def test_gateway_runs_suppresses_transport_error_after_user_cancel(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "runs")
    monkeypatch.setattr(streaming, "_load_webui_prefill_context", lambda cfg: {
        "status": "not_configured",
        "source": "none",
        "label": "",
        "message_count": 0,
        "messages": [],
    })
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda ctx, cfg: [])
    monkeypatch.setattr(gateway_chat, "_ensure_gateway_managed_session", lambda **kwargs: "managed")

    def cancelled_transport(**kwargs):
        kwargs["cancel_event"].set()
        raise RuntimeError("socket closed while stopping")

    monkeypatch.setattr(gateway_chat, "_stream_gateway_run_events", cancelled_transport)
    session = new_session()
    stream_id = "stream-cancel-error-race"
    session.active_stream_id = stream_id
    session.pending_user_message = "cancel this"
    session.save()
    channel = create_stream_channel()
    subscriber = channel.subscribe()
    STREAMS[stream_id] = channel

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "cancel this",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
    )

    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    assert not any(name == "apperror" for name, _ in events)


def test_gateway_worker_finalizes_cancel_when_stream_was_removed_before_registration(
    tmp_path, monkeypatch
):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    session = new_session()
    stream_id = "stream-cancel-before-worker"
    session.active_stream_id = stream_id
    session.pending_user_message = "cancel before start"
    session.pending_started_at = 123.0
    session.save()
    STREAMS.pop(stream_id, None)

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "cancel before start",
        "test-model",
        str(tmp_path),
        stream_id,
        [],
    )

    saved = models.get_session(session.session_id)
    assert saved.active_stream_id is None
    assert saved.pending_user_message is None
    assert [message["role"] for message in saved.messages] == ["user", "assistant"]
    assert saved.messages[-1].get("_error") is True


def test_gateway_runs_transport_keeps_current_image_attachment_in_input(
    tmp_path, monkeypatch
):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setenv("HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT", "runs")
    monkeypatch.setattr(config, "get_config", lambda: {"agent": {"image_input_mode": "native"}})
    monkeypatch.setattr(
        streaming,
        "_load_webui_prefill_context",
        lambda cfg: {
            "status": "not_configured",
            "source": "none",
            "label": "",
            "message_count": 0,
            "messages": [],
        },
    )
    monkeypatch.setattr(streaming, "_prefill_messages_with_webui_context", lambda ctx, cfg: [])
    session = models.Session(session_id="session-managed-image")
    models.SESSIONS[session.session_id] = session
    attachment_root = tmp_path / "attachments"
    upload_dir = attachment_root / session.session_id
    upload_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(attachment_root))
    image_path = upload_dir / "photo.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    ))
    captured = {}

    class FakeResponse:
        def __init__(self, payload=None, lines=None):
            self.payload = payload or {}
            self.lines = lines or []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

        def __iter__(self):
            yield from self.lines

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/api/sessions"):
            return FakeResponse({"session": {"id": session.session_id}})
        if req.full_url.endswith("/v1/runs"):
            captured["run_body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"run_id": "run-image", "session_id": session.session_id})
        return FakeResponse(lines=[
            b'data: {"event":"run.completed","output":"saw it","usage":{}}\n\n',
        ])

    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", fake_urlopen)
    stream_id = "stream-managed-image"
    session.active_stream_id = stream_id
    session.save()
    STREAMS[stream_id] = create_stream_channel()

    gateway_chat._run_gateway_chat_streaming(
        session.session_id,
        "What is in this image?",
        "test-model",
        str(tmp_path),
        stream_id,
        [{"name": image_path.name, "ref": image_path.name, "mime": "image/png", "is_image": True}],
    )

    current_input = captured["run_body"]["input"]
    assert isinstance(current_input, list), repr(current_input)
    assert current_input[0] == {"type": "text", "text": "What is in this image?"}
    assert current_input[1]["type"] == "image_url"
    assert current_input[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["run_body"]["checkpoint_content"] == "What is in this image?"
    assert "conversation_history" not in captured["run_body"]


def test_gateway_runs_user_cancel_returns_cancelled_even_with_partial_text(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-cancelled","session_id":"sid-runs-cancel"}'

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
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-cancel",
        stream_id="stream-runs-cancel",
        cancel_event=cancel,
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: events.append((name, data)),
    )

    assert result["terminal_outcome"] == "cancelled"
    assert result["final_text"]
    assert result["final_text"] in partial
    assert not any(name == "cancel" for name, _data in events)


def test_gateway_run_reasoning_uses_stateful_cross_chunk_filter(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-reasoning","session_id":"sid-runs-reasoning"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield f'data: {json.dumps({"event": "reasoning.available", "text": "internal path /Users/me/her"})}\n\n'.encode()
            yield f'data: {json.dumps({"event": "reasoning.available", "text": "mes-local-lab/run_agent.py"})}\n\n'.encode()
            yield b'data: {"event":"run.completed","output":"ok"}\n\n'

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)
    events = []

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-reasoning",
        stream_id="stream-runs-reasoning",
        cancel_event=gateway_chat.threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: events.append((name, data)),
    )

    reasoning = "".join(
        str(data.get("text") or "")
        for name, data in events
        if name == "reasoning"
    )
    assert result["terminal_outcome"] == "completed"
    assert len([1 for name, _data in events if name == "reasoning"]) == 1
    assert reasoning
    assert reasoning.startswith("taiji Agent")
    assert "hermes" not in reasoning.lower()
    assert "/Users/me/hermes-local-lab" not in reasoning


def test_gateway_runs_server_cancelled_preserves_cancelled_outcome(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-server-cancelled","session_id":"sid-runs-server-cancel"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"event":"reasoning.available","text":"incomplete sensitive reasoning"}\n\n'
            yield b'data: {"event":"run.cancelled"}\n\n'

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)

    events = []
    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-server-cancel",
        stream_id="stream-runs-server-cancel",
        cancel_event=gateway_chat.threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: events.append((name, data)),
    )

    assert result["terminal_outcome"] == "cancelled"
    assert result["error_event"] is None
    assert not [event for event in events if event[0] == "reasoning"]


def test_gateway_runs_partial_eof_is_not_completed(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-truncated","session_id":"sid-runs-truncated"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"event":"reasoning.available","text":"incomplete sensitive reasoning"}\n\n'
            yield f'data: {json.dumps({"event": "message.delta", "delta": "partial " * 40})}\n\n'.encode()

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_stop_gateway_run", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)

    events = []
    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-truncated",
        stream_id="stream-runs-truncated",
        cancel_event=gateway_chat.threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: events.append((name, data)),
    )

    assert result["final_text"]
    assert result["terminal_outcome"] == "failed"
    assert result["error_event"]["type"] == "gateway_error"
    assert not [event for event in events if event[0] == "reasoning"]


def test_gateway_runs_failed_discards_buffered_reasoning(monkeypatch):
    class StartResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return b'{"run_id":"remote-failed","session_id":"sid-runs-failed"}'

    class EventResponse:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self):
            yield b'data: {"event":"reasoning.available","text":"incomplete sensitive reasoning"}\n\n'
            yield b'data: {"event":"run.failed","error":"provider failed"}\n\n'

    responses = iter([StartResponse(), EventResponse()])
    monkeypatch.setattr(gateway_chat.urllib.request, "urlopen", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(gateway_chat, "_clear_gateway_run_approvals_from_webui", lambda *_args, **_kwargs: None)
    events = []

    result = gateway_chat._stream_gateway_run_events(
        base_url="http://gateway.local",
        headers={},
        body={"model": "m", "messages": [{"role": "user", "content": "q"}]},
        session_id="sid-runs-failed",
        stream_id="stream-runs-failed",
        cancel_event=gateway_chat.threading.Event(),
        brand_token_tail=[""],
        put_gateway_event=lambda name, data: events.append((name, data)),
    )

    assert result["terminal_outcome"] == "failed"
    assert not [event for event in events if event[0] == "reasoning"]
