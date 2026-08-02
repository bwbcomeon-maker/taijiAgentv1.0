import inspect
import json
import queue
import sys
import threading
import types
from types import SimpleNamespace

import pytest

import api.gateway_chat as gateway_chat
import api.state_sync as state_sync


def _history_session():
    return SimpleNamespace(
        session_id="session-phase2",
        messages=[
            {"role": "user", "content": "first question", "timestamp": 1.0},
            {
                "role": "assistant",
                "content": "calling a tool",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
                "timestamp": 2.0,
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": '{"value": 42}',
                "timestamp": 3.0,
            },
            {"role": "assistant", "content": "the answer is 42", "timestamp": 4.0},
        ],
        context_messages=[],
        truncation_watermark=None,
    )


def test_chat_completions_turn_uses_standard_reconciliation_and_sanitization(monkeypatch):
    session = _history_session()
    calls = []

    def reconciled(candidate, **kwargs):
        calls.append(("reconciled", kwargs))
        return list(candidate.messages)

    def new_turn(messages, current):
        calls.append(("new_turn", current))
        return list(messages)

    def dedupe(messages):
        calls.append(("dedupe", len(messages)))
        return list(messages)

    def sanitize(messages, *, cfg=None, capability_generation=None):
        calls.append(("sanitize", (cfg, capability_generation)))
        return list(messages)

    monkeypatch.setattr(gateway_chat, "reconciled_state_db_messages_for_session", reconciled)
    monkeypatch.setattr(gateway_chat, "_new_turn_context_from_messages", new_turn)
    monkeypatch.setattr(gateway_chat, "_deduplicate_context_messages", dedupe)
    monkeypatch.setattr(gateway_chat, "_sanitize_messages_for_api", sanitize)

    messages = gateway_chat._gateway_messages_for_new_turn(
        session,
        "follow up",
        [{"role": "system", "content": "## Current Session Context\nWebUI"}],
        {"type": "text", "text": "prepared follow up"},
        cfg={"test": True},
        state_messages=list(session.messages),
    )

    assert [name for name, _ in calls] == ["reconciled", "new_turn", "dedupe", "sanitize"]
    assert calls[-1][1][0] == {"test": True}
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"] == {"type": "text", "text": "prepared follow up"}
    assert sum("Current Session Context" in str(m.get("content")) for m in messages if m["role"] == "user") == 0


def test_managed_runs_send_only_current_input_and_ephemeral_instructions():
    ephemeral_messages = [
        {"role": "system", "content": "temporary instructions"},
        {"role": "user", "content": "temporary recall context"},
    ]
    messages = [
        *ephemeral_messages,
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "42"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "follow up"},
    ]

    run_body = gateway_chat._gateway_run_request_body(
        {
            "model": "test",
            "messages": messages,
            "platform_message_id": "webui-turn:turn-123",
            "checkpoint_content": "visible follow up",
        },
        session_id="session-phase2",
        ephemeral_messages=ephemeral_messages,
    )

    assert run_body["input"] == "follow up"
    assert run_body["checkpoint_content"] == "visible follow up"
    assert run_body["platform_message_id"] == "webui-turn:turn-123"
    assert "temporary instructions" in run_body["instructions"]
    assert "role=user" in run_body["instructions"]
    assert "temporary recall context" in run_body["instructions"]
    assert "conversation_history" not in run_body
    assert "messages" not in run_body
    assert "first" not in repr(run_body)
    assert "done" not in repr(run_body)


def test_turn_envelope_has_stable_webui_platform_message_id():
    envelope = gateway_chat.TurnEnvelope.create(
        turn_id="turn-123",
        session_id="session-phase2",
        submitted_at=123.5,
        display_user_message="visible",
        model_messages=[{"role": "user", "content": "model"}],
        attachments=[{"name": "a.txt"}],
    )

    assert envelope.platform_message_id == "webui-turn:turn-123"
    assert envelope.display_user_message == "visible"
    assert envelope.model_messages[-1]["content"] == "model"


def test_turn_envelope_effective_messages_are_deeply_isolated():
    original = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "canonical"}],
        }
    ]
    envelope = gateway_chat.TurnEnvelope.create(
        turn_id="turn-isolated",
        session_id="session-phase2",
        submitted_at=123.5,
        display_user_message="visible",
        model_messages=[{"role": "user", "content": "placeholder"}],
        attachments=[{"name": "a.txt", "metadata": {"size": 1}}],
    )

    effective = envelope.with_model_messages(original)
    original[0]["content"][0]["text"] = "mutated after construction"

    assert envelope.model_messages[-1]["content"] == "placeholder"
    assert effective.model_messages[-1]["content"][0]["text"] == "canonical"
    assert effective.platform_message_id == envelope.platform_message_id


def test_strict_turn_envelope_cannot_be_replaced_by_chat_history():
    messages = [
        {"role": "system", "content": "strict system"},
        {"role": "user", "content": "strict user"},
    ]
    envelope = gateway_chat.TurnEnvelope.create(
        turn_id="turn-strict",
        session_id="session-phase2",
        submitted_at=123.5,
        display_user_message="visible summary",
        model_messages=messages,
        attachments=[],
        strict_model_messages=True,
        tools_disabled=True,
    )

    assert envelope.model_messages_for_runtime(
        [{"role": "user", "content": "historical overwrite"}]
    ) == messages
    with pytest.raises(ValueError, match="strict model messages"):
        envelope.with_model_messages(
            [{"role": "user", "content": "historical overwrite"}]
        )


def test_local_strict_turn_agent_overrides_are_stateless_exact_and_uncached():
    from api import streaming

    envelope = gateway_chat.TurnEnvelope.create(
        turn_id="turn-strict-local",
        session_id="ordinary-session",
        submitted_at=123.5,
        display_user_message="visible summary",
        model_messages=[
            {"role": "system", "content": "strict system"},
            {"role": "user", "content": "strict user"},
        ],
        attachments=[],
        strict_model_messages=True,
        tools_disabled=True,
    )

    overrides = streaming._strict_turn_agent_overrides(envelope)

    assert overrides == {
        "session_id": "expert-exec-turn-strict-local",
        "session_db": None,
        "gateway_session_key": None,
        "exact_system_prompt": True,
        "tools_disabled": True,
        "skip_context_files": True,
        "skip_memory": True,
        "fallback_model": None,
        "enabled_toolsets": [],
        "max_iterations": 1,
        "cacheable": False,
    }


def test_strict_turn_result_gate_accepts_only_one_complete_provider_result():
    from api import streaming

    accepted = {
        "completed": True,
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": None,
        "api_calls": 1,
        "final_response": "阶段成果",
    }

    assert streaming._strict_turn_result_is_authoritative(accepted) is True


@pytest.mark.parametrize(
    "patch_value",
    [
        {"completed": False},
        {"failed": True},
        {"partial": True},
        {"interrupted": True},
        {"error": "provider failed"},
        {"api_calls": 0},
        {"api_calls": 2},
        {"final_response": ""},
        {"final_response": None},
    ],
)
def test_strict_turn_result_gate_rejects_partial_failed_or_ambiguous_results(
    patch_value,
):
    from api import streaming

    result = {
        "completed": True,
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": None,
        "api_calls": 1,
        "final_response": "阶段成果",
    }
    result.update(patch_value)

    assert streaming._strict_turn_result_is_authoritative(result) is False


def test_strict_stream_source_guards_self_heal_lifecycle_and_title_generation():
    from api import streaming

    source = inspect.getsource(streaming._run_agent_streaming)

    assert "elif _is_auth and not _self_healed and not strict_turn:" in source
    assert "if not _self_healed and not strict_turn:" in source
    assert "if not ephemeral and not strict_turn:" in source
    assert "if strict_turn:\n                put('stream_end'" in source
    assert "strict_turn and not _strict_turn_result_is_authoritative(result)" in source


def test_strict_stream_dynamically_ignores_compression_lifecycle_and_rotation(
    monkeypatch,
    tmp_path,
):
    from api import config, metering, models, oauth, profiles, streaming, truth_rewrite

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    session_index = session_dir / "_index.json"
    session_id = "strict-compression-origin"
    continuation_id = "strict-compression-continuation"
    stream_id = "strict-compression-stream"
    display_message = "执行严格专家团阶段"
    final_response = "严格阶段已生成权威结果。"
    session_context = {
        "context_length": 777_001,
        "threshold_tokens": 654_321,
        "last_prompt_tokens": 12_345,
    }
    isolated_compressor_context = {
        "context_length": 128_003,
        "threshold_tokens": 100_007,
        "last_prompt_tokens": 1_009,
    }

    monkeypatch.setattr(config, "SESSION_DIR", session_dir)
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", session_index)
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_index)
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir)
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda _profile: profile_home,
    )
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda _home: {})
    monkeypatch.setattr(profiles, "patch_skill_home_modules", lambda _home: None)
    monkeypatch.setattr(
        streaming,
        "resolve_model_provider",
        lambda _model: ("gpt-test", "openai", None),
    )
    monkeypatch.setattr(streaming, "_prewarm_skill_tool_modules", lambda: None)
    monkeypatch.setattr(streaming, "register_active_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(streaming, "update_active_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(streaming, "unregister_active_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        streaming,
        "append_turn_journal_event_for_stream",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        streaming,
        "RunJournalWriter",
        lambda *_args, **_kwargs: SimpleNamespace(
            append_sse_event=lambda *_event_args, **_event_kwargs: {}
        ),
    )
    monkeypatch.setattr(streaming, "get_state_db_session_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(config, "get_config", lambda **_kwargs: {
        "model": {"default": "gpt-test", "context_length": 128000}
    })
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda _cfg: [])
    monkeypatch.setattr(config, "load_settings", lambda: {})
    raw_live_usage = []
    real_public_egress_scrub = streaming.public_egress_scrub

    def capture_live_usage(payload, *, surface, event_name=None):
        if (
            event_name == "metering"
            and isinstance(payload, dict)
            and isinstance(payload.get("usage"), dict)
        ):
            raw_live_usage.append(dict(payload["usage"]))
        return real_public_egress_scrub(
            payload,
            surface=surface,
            event_name=event_name,
        )

    monkeypatch.setattr(streaming, "public_egress_scrub", capture_live_usage)
    monkeypatch.setattr(
        oauth,
        "resolve_runtime_provider_with_anthropic_env_lock",
        lambda resolver, requested=None: resolver(requested=requested),
    )

    runtime_module = types.ModuleType("hermes_cli.runtime_provider")
    runtime_module.resolve_runtime_provider = lambda requested=None: {
        "provider": requested or "openai",
        "base_url": None,
        "api_key": "test-key",
        "api_mode": "chat_completions",
        "command": None,
        "args": [],
        "credential_pool": None,
    }
    hermes_cli = types.ModuleType("hermes_cli")
    hermes_cli.runtime_provider = runtime_module
    hermes_state = types.ModuleType("hermes_state")
    hermes_state.SessionDB = lambda **_kwargs: None
    hermes_state.install_state_write_guard = lambda _guard: None
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", runtime_module)
    monkeypatch.setitem(sys.modules, "hermes_state", hermes_state)

    class FakeCompressor:
        def __init__(self):
            self.compression_count = 0
            self.context_length = isolated_compressor_context["context_length"]
            self.threshold_tokens = isolated_compressor_context["threshold_tokens"]
            self.last_prompt_tokens = isolated_compressor_context["last_prompt_tokens"]

    class FakeAgent:
        def __init__(self, status_callback=None, **kwargs):
            from agent.image_runtime import capture_capability_runtime_generation

            generation = capture_capability_runtime_generation()
            assert generation.stable
            self._capability_runtime_identity = generation.identity
            self.status_callback = status_callback
            self.session_id = kwargs["session_id"]
            self.model = kwargs["model"]
            self.provider = kwargs["provider"]
            self.base_url = kwargs.get("base_url")
            self.stream_delta_callback = kwargs.get("stream_delta_callback")
            self.tool_progress_callback = kwargs.get("tool_progress_callback")
            self.reasoning_callback = kwargs.get("reasoning_callback")
            self.clarify_callback = kwargs.get("clarify_callback")
            self.context_compressor = FakeCompressor()
            self.session_prompt_tokens = 1200
            self.session_completion_tokens = 80
            self.session_estimated_cost_usd = 0.01
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.ephemeral_system_prompt = None
            self._last_error = None

        def run_conversation(self, **kwargs):
            assert self.session_id == "expert-exec-strict-compression-turn"
            assert kwargs["system_message"] == "strict system"
            assert kwargs["user_message"] == "strict user"
            assert kwargs["conversation_history"] == []
            self.status_callback("lifecycle", "preflight compression")
            self.stream_delta_callback("strict-live-token-" * 8)
            self.context_compressor.compression_count = 1
            self.session_id = continuation_id
            return {
                "completed": True,
                "failed": False,
                "partial": False,
                "interrupted": False,
                "error": None,
                "api_calls": 1,
                "final_response": final_response,
                "messages": [
                    {
                        "role": "user",
                        "content": kwargs["persist_user_message"],
                        "platform_message_id": kwargs[
                            "persist_user_platform_message_id"
                        ],
                    },
                    {"role": "assistant", "content": final_response},
                ],
            }

        def interrupt(self, _message):
            return None

    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: FakeAgent)

    shared_mappings = [
        config.SESSIONS,
        config.SESSION_AGENT_LOCKS,
        config.SESSION_AGENT_CACHE,
        streaming.STREAMS,
        streaming.CANCEL_FLAGS,
        streaming.AGENT_INSTANCES,
        streaming.STREAM_SESSION_IDS,
        streaming.STREAM_PARTIAL_TEXT,
        streaming.STREAM_REASONING_TEXT,
        streaming.STREAM_LIVE_TOOL_CALLS,
    ]
    snapshots = [(mapping, list(mapping.items())) for mapping in shared_mappings]
    with truth_rewrite._LOCKS_GUARD:
        truth_locks_snapshot = dict(truth_rewrite._LOCKS)
    test_meter = metering.meter()
    with test_meter._lock:
        meter_snapshot = (
            dict(test_meter._sessions),
            list(test_meter._readings),
            test_meter._window_start,
        )
    threads_before = set(threading.enumerate())

    def is_test_worker(thread):
        return (
            thread.name == f"ckpt-{session_id[:8]}"
            or "_metering_ticker" in thread.name
        )

    try:
        for mapping in shared_mappings:
            mapping.clear()
        assert streaming.SESSIONS is config.SESSIONS is models.SESSIONS

        session = models.Session(
            session_id=session_id,
            title="Strict compression isolation",
            workspace=str(tmp_path),
            model="gpt-test",
            model_provider="openai",
            profile="default",
            active_stream_id=stream_id,
            pending_user_message=display_message,
            context_length=session_context["context_length"],
            threshold_tokens=session_context["threshold_tokens"],
            last_prompt_tokens=session_context["last_prompt_tokens"],
        )
        session.save(touch_updated_at=False, skip_index=True)
        with config.LOCK:
            config.SESSIONS[session_id] = session
        original_lock = config._get_session_agent_lock(session_id)
        stream_queue = queue.Queue()
        streaming.STREAMS[stream_id] = stream_queue
        envelope = gateway_chat.TurnEnvelope.create(
            turn_id="strict-compression-turn",
            session_id=session_id,
            submitted_at=123.5,
            display_user_message=display_message,
            model_messages=[
                {"role": "system", "content": "strict system"},
                {"role": "user", "content": "strict user"},
            ],
            attachments=[],
            strict_model_messages=True,
            tools_disabled=True,
        )

        streaming._run_agent_streaming(
            session_id=session_id,
            msg_text=display_message,
            model="gpt-test",
            model_provider="openai",
            workspace=str(tmp_path),
            stream_id=stream_id,
            turn_envelope=envelope,
        )

        events = list(stream_queue.queue)
        event_names = [event for event, _payload in events]
        assert "done" in event_names
        assert "stream_end" in event_names
        assert "apperror" not in event_names
        assert list(config.SESSIONS) == [session_id]
        assert config.SESSIONS[session_id] is session
        assert session.session_id == session_id
        with test_meter._lock:
            assert stream_id not in test_meter._sessions
        assert {
            "context_length": session.context_length,
            "threshold_tokens": session.threshold_tokens,
            "last_prompt_tokens": session.last_prompt_tokens,
        } == session_context
        assert config.SESSION_AGENT_LOCKS == {session_id: original_lock}
        assert config.SESSION_AGENT_LOCKS[session_id] is original_lock
        assert config.SESSION_AGENT_CACHE == {}
        assert not (session_dir / f"{continuation_id}.json").exists()
        stored = json.loads((session_dir / f"{session_id}.json").read_text(encoding="utf-8"))
        assert stored["session_id"] == session_id
        assert stored["messages"][-1]["role"] == "assistant"
        assert stored["messages"][-1]["content"] == final_response
        assert {
            "context_length": stored["context_length"],
            "threshold_tokens": stored["threshold_tokens"],
            "last_prompt_tokens": stored["last_prompt_tokens"],
        } == session_context
        assert raw_live_usage, events
        for usage in raw_live_usage:
            assert {
                "context_length": usage["context_length"],
                "threshold_tokens": usage["threshold_tokens"],
                "last_prompt_tokens": usage["last_prompt_tokens"],
            } == session_context
            assert usage["input_tokens"] == 1200
            assert usage["output_tokens"] == 80
            assert usage["estimated_cost"] == 0.01
        done_payloads = [payload for event, payload in events if event == "done"]
        assert len(done_payloads) == 1
        done_usage = done_payloads[0]["usage"]
        assert {
            "context_length": done_usage["context_length"],
            "threshold_tokens": done_usage["threshold_tokens"],
            "last_prompt_tokens": done_usage["last_prompt_tokens"],
        } == session_context
        assert done_usage["input_tokens"] == 1200
        assert done_usage["output_tokens"] == 80
        assert done_usage["estimated_cost"] == 0.01
        assert "compressed" not in event_names
        assert "compressing" not in event_names
        leaked_workers = [
            thread
            for thread in threading.enumerate()
            if thread not in threads_before and is_test_worker(thread)
        ]
        assert leaked_workers == []
    finally:
        for thread in threading.enumerate():
            if thread not in threads_before and is_test_worker(thread):
                thread.join(timeout=1)
        with truth_rewrite._LOCKS_GUARD:
            truth_rewrite._LOCKS.clear()
            truth_rewrite._LOCKS.update(truth_locks_snapshot)
        with test_meter._lock:
            test_meter._sessions.clear()
            test_meter._sessions.update(meter_snapshot[0])
            test_meter._readings[:] = meter_snapshot[1]
            test_meter._window_start = meter_snapshot[2]
        for mapping, items in reversed(snapshots):
            mapping.clear()
            mapping.update(items)


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "user", "content": "missing system"}],
        [{"role": "system", "content": "x"}, {"role": "assistant", "content": "y"}],
        [{"role": "system", "content": ""}, {"role": "user", "content": "y"}],
    ],
)
def test_strict_turn_envelope_rejects_invalid_role_contract(messages):
    with pytest.raises(ValueError, match="strict model messages"):
        gateway_chat.TurnEnvelope.create(
            turn_id="turn-invalid",
            session_id="session-phase2",
            submitted_at=123.5,
            display_user_message="visible",
            model_messages=messages,
            attachments=[],
            strict_model_messages=True,
            tools_disabled=True,
        )


def test_strict_gateway_request_uses_exact_messages_zero_tools_and_no_session_history_headers():
    messages = [
        {"role": "system", "content": "strict system"},
        {"role": "user", "content": "strict user"},
    ]
    envelope = gateway_chat.TurnEnvelope.create(
        turn_id="turn-strict-gateway",
        session_id="session-phase2",
        submitted_at=123.5,
        display_user_message="visible",
        model_messages=messages,
        attachments=[],
        strict_model_messages=True,
        tools_disabled=True,
    )

    body = gateway_chat._gateway_request_body(
        model="gpt-test",
        provider="openai",
        turn_envelope=envelope,
        ordinary_messages=[{"role": "user", "content": "history"}],
    )
    headers = gateway_chat._gateway_request_headers(
        envelope.session_id,
        "secret",
        profile_name="default",
        event_stream=True,
        session_continuation=False,
    )

    assert body["messages"] == messages
    assert body["tools"] == []
    assert body["tool_choice"] == "none"
    assert body["model"] == "gpt-test"
    assert body["provider"] == "openai"
    assert body["taiji_expert_execution"]["contract_version"] == (
        "taiji.expert-team-execution/v1"
    )
    assert len(body["taiji_expert_execution"]["request_sha256"]) == 64
    assert "X-Hermes-Session-Id" not in headers
    assert "X-Hermes-Session-Key" not in headers
    assert headers["Authorization"] == "Bearer secret"


def test_ordinary_gateway_request_keeps_existing_session_and_has_no_zero_tools_override():
    envelope = gateway_chat.TurnEnvelope.create(
        turn_id="turn-ordinary",
        session_id="session-phase2",
        submitted_at=123.5,
        display_user_message="visible",
        model_messages=[{"role": "user", "content": "placeholder"}],
        attachments=[],
    )
    ordinary = [{"role": "user", "content": "ordinary canonical"}]

    body = gateway_chat._gateway_request_body(
        model="gpt-test",
        provider=None,
        turn_envelope=envelope,
        ordinary_messages=ordinary,
    )
    headers = gateway_chat._gateway_request_headers(
        envelope.session_id,
        "secret",
        session_continuation=True,
    )

    assert body["messages"] == ordinary
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "taiji_expert_execution" not in body
    assert headers["X-Hermes-Session-Id"] == "session-phase2"
    assert headers["X-Hermes-Session-Key"] == "webui:session-phase2"


def test_gateway_state_db_read_uses_session_profile(monkeypatch):
    session = _history_session()
    session.profile = "maiko"
    calls = []

    def read_state(session_id, *, profile=None, **_kwargs):
        calls.append((session_id, profile))
        return list(session.messages)

    monkeypatch.setattr(gateway_chat, "get_state_db_session_messages", read_state)

    messages = gateway_chat._gateway_messages_for_new_turn(
        session,
        "follow up",
        [],
        "follow up",
        cfg={},
    )

    assert calls == [(session.session_id, "maiko")]
    assert messages[-1] == {"role": "user", "content": "follow up"}


def test_user_turn_checkpoint_is_idempotent_before_worker_start(monkeypatch):
    calls = []

    class FakeDB:
        def ensure_session(self, **kwargs):
            calls.append(("ensure", kwargs))

        def append_message(self, **kwargs):
            calls.append(("append", kwargs))

        def close(self):
            calls.append(("close", {}))

    monkeypatch.setattr(
        state_sync,
        "_get_state_db",
        lambda profile=None, strict=False, create_if_missing=False: FakeDB(),
    )

    assert state_sync.sync_webui_user_turn(
        session_id="session-phase2",
        content="visible user text",
        turn_id="turn-123",
        model="test-model",
        profile="default",
    ) is True

    assert [name for name, _ in calls] == ["ensure", "append", "close"]
    assert calls[1][1]["platform_message_id"] == "webui-turn:turn-123"


def test_gateway_restart_recovers_complete_history_from_state_db():
    durable_history = _history_session().messages
    restarted = _history_session()
    restarted.messages = []
    restarted.context_messages = []

    messages = gateway_chat._gateway_messages_for_new_turn(
        restarted,
        "follow up after restart",
        [],
        "follow up after restart",
        cfg={},
        state_messages=durable_history,
    )

    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert len(messages[:-1]) == 4
    assert messages[-1]["content"] == "follow up after restart"


def test_gateway_after_clear_sees_only_the_new_turn():
    cleared = _history_session()
    cleared.messages = []
    cleared.context_messages = []
    cleared.truncation_watermark = 0.0

    messages = gateway_chat._gateway_messages_for_new_turn(
        cleared,
        "brand new question",
        [],
        "brand new question",
        cfg={},
        state_messages=[],
    )

    assert messages == [{"role": "user", "content": "brand new question"}]
    assert "first question" not in str(messages)


def test_gateway_replays_fifty_completed_turns_once_after_restart():
    durable_history = []
    for index in range(50):
        durable_history.extend([
            {
                "role": "user",
                "content": f"question {index}",
                "platform_message_id": f"webui-turn:turn-{index}",
            },
            {"role": "assistant", "content": f"answer {index}"},
        ])
    restarted = _history_session()
    restarted.messages = []
    restarted.context_messages = []

    messages = gateway_chat._gateway_messages_for_new_turn(
        restarted,
        "question 50",
        [],
        "question 50",
        cfg={},
        state_messages=durable_history,
    )

    assert len(messages) == 101
    assert [message["role"] for message in messages[:-1]].count("user") == 50
    assert [message["role"] for message in messages[:-1]].count("assistant") == 50
    for index in range(50):
        assert sum(message.get("content") == f"question {index}" for message in messages) == 1
        assert sum(message.get("content") == f"answer {index}" for message in messages) == 1
    assert messages[-1] == {"role": "user", "content": "question 50"}
