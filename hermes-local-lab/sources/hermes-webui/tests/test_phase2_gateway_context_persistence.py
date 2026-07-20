from types import SimpleNamespace

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
