import json
import copy
import queue
import sqlite3
from collections import OrderedDict
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_agent_modules


def _make_state_db(path: Path, sid: str, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, title TEXT, model TEXT, started_at REAL, message_count INTEGER)"
    )
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp REAL)"
    )
    conn.execute(
        "INSERT INTO sessions (id, source, title, model, started_at, message_count) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, "webui", "Context Reconcile", "test-model", 1000.0, len(rows)),
    )
    for row in rows:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (sid, row["role"], row["content"], row.get("timestamp", 1000.0)),
        )
    conn.commit()
    conn.close()


def test_next_webui_turn_context_includes_state_db_external_messages(monkeypatch, tmp_path):
    import api.config as config
    import api.models as models
    import api.profiles as profiles
    import api.streaming as streaming
    from api.models import Session

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_file = session_dir / "_index.json"
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_file)
    monkeypatch.setattr(models, "SESSIONS", OrderedDict(), raising=False)
    monkeypatch.setattr(config, "SESSION_DIR", session_dir, raising=False)
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", index_file, raising=False)
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir, raising=False)
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: tmp_path)
    monkeypatch.setattr(models, "_active_state_db_path", lambda: tmp_path / "state.db", raising=False)
    config.STREAMS.clear()
    config.CANCEL_FLAGS.clear()
    config.AGENT_INSTANCES.clear()
    config.SESSION_AGENT_LOCKS.clear()

    sid = "webui_context_reconcile_001"
    sidecar_messages = [
        {"role": "user", "content": "old user", "timestamp": 1000.0},
        {"role": "assistant", "content": "old assistant", "timestamp": 1001.0},
    ]
    session = Session(
        session_id=sid,
        title="Context Reconcile",
        workspace=str(tmp_path),
        model="test-model",
        profile="maiko",
        messages=list(sidecar_messages),
        context_messages=list(sidecar_messages),
    )
    session.active_stream_id = "stream-context-reconcile"
    session.pending_user_message = "new webui turn"
    session.pending_started_at = 1004.0
    session.save(touch_updated_at=False)
    models.SESSIONS[sid] = session
    monkeypatch.setattr(
        models,
        "_get_profile_home",
        lambda profile: tmp_path if profile == "maiko" else tmp_path / "wrong-profile",
    )

    _make_state_db(
        tmp_path / "state.db",
        sid,
        [
            {"role": "user", "content": "old user", "timestamp": 1000.0},
            {"role": "assistant", "content": "old assistant", "timestamp": 1001.0},
            {"role": "user", "content": "external gateway user", "timestamp": 1002.0},
            {"role": "assistant", "content": "external gateway assistant", "timestamp": 1003.0},
        ],
    )

    captured = {}
    profile_reads = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.session_id = sid
            self.context_compressor = None
            self.ephemeral_system_prompt = None

        def run_conversation(self, **kwargs):
            captured["conversation_history"] = copy.deepcopy(
                kwargs.get("conversation_history")
            )
            captured["user_message"] = copy.deepcopy(kwargs.get("user_message"))
            history = kwargs.get("conversation_history") or []
            return {
                "completed": True,
                "final_response": "ok",
                "messages": history + [
                    {"role": "user", "content": kwargs.get("persist_user_message", "")},
                    {"role": "assistant", "content": "ok"},
                ],
            }

    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: FakeAgent)
    monkeypatch.setattr(streaming, "resolve_model_provider", lambda *args, **kwargs: ("test-model", None, None))
    monkeypatch.setattr(streaming, "get_config", lambda: {})
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda *args, **kwargs: [])
    real_state_reader = streaming.get_state_db_session_messages

    def profile_aware_state_reader(session_id, *, profile=None, **kwargs):
        profile_reads.append(profile)
        return real_state_reader(session_id, profile=profile, **kwargs)

    monkeypatch.setattr(streaming, "get_state_db_session_messages", profile_aware_state_reader)

    from api.turn_envelope import TurnEnvelope

    placeholder_envelope = TurnEnvelope.create(
        turn_id="turn-context-reconcile",
        session_id=sid,
        submitted_at=1004.0,
        display_user_message="new webui turn",
        model_messages=[{"role": "user", "content": "placeholder only"}],
        attachments=[],
    )
    effective_messages = []
    original_with_model_messages = TurnEnvelope.with_model_messages

    def capture_effective(self, messages):
        effective = original_with_model_messages(self, messages)
        effective_messages.append(effective.model_messages)
        return effective

    monkeypatch.setattr(TurnEnvelope, "with_model_messages", capture_effective)

    stream_id = "stream-context-reconcile"
    config.STREAMS[stream_id] = queue.Queue()
    try:
        streaming._run_agent_streaming(
            session_id=sid,
            msg_text="new webui turn",
            model="test-model",
            workspace=str(tmp_path),
            stream_id=stream_id,
            attachments=[],
            turn_envelope=placeholder_envelope,
        )
    finally:
        config.STREAMS.pop(stream_id, None)

    history_contents = [m.get("content") for m in captured.get("conversation_history") or []]
    assert history_contents == [
        "old user",
        "old assistant",
        "external gateway user",
        "external gateway assistant",
    ]
    assert profile_reads == [session.profile]
    effective_contents = [m.get("content") for m in effective_messages[-1]]
    assert effective_contents[:-1] == [
        "old user",
        "old assistant",
        "external gateway user",
        "external gateway assistant",
    ]
    assert effective_contents[-1].endswith("\nnew webui turn")
    assert effective_messages[-1][:-1] == tuple(captured["conversation_history"])
    assert effective_messages[-1][-1]["content"] == captured["user_message"]
    assert "placeholder only" not in effective_contents


def test_legacy_final_save_validates_visible_assistant_fields_only(monkeypatch, tmp_path):
    import api.config as config
    import api.models as models
    import api.profiles as profiles
    import api.streaming as streaming
    from api.models import Session

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_file = session_dir / "_index.json"
    sessions = OrderedDict()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_file)
    monkeypatch.setattr(models, "SESSIONS", sessions, raising=False)
    monkeypatch.setattr(config, "SESSION_DIR", session_dir, raising=False)
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", index_file, raising=False)
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir, raising=False)
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: tmp_path)
    monkeypatch.setattr(models, "_active_state_db_path", lambda: tmp_path / "state.db", raising=False)
    config.STREAMS.clear()
    config.CANCEL_FLAGS.clear()
    config.AGENT_INSTANCES.clear()
    config.SESSION_AGENT_LOCKS.clear()

    sid = "legacy-visible-assistant-gate"
    stream_id = "stream-legacy-visible-assistant-gate"
    raw_user = "inspect /tmp/customer/input.txt with token sk-user-original"
    raw_content = "业务结果已保存到 /Users/alice/customer/output/report.docx。"
    raw_reasoning = "读取 /private/customer/source.md 后完成。"
    raw_args = '{"path":"/private/customer/input.txt","token":"sk-tool-original"}'
    session = Session(
        session_id=sid,
        workspace=str(tmp_path),
        model="test-model",
        active_stream_id=stream_id,
        pending_user_message=raw_user,
        pending_started_at=1.0,
    )
    session.save(touch_updated_at=False)
    sessions[sid] = session

    class FakeAgent:
        def __init__(self, **_kwargs):
            self.session_id = sid
            self.context_compressor = None
            self.ephemeral_system_prompt = None

        def run_conversation(self, **kwargs):
            return {
                "completed": True,
                "final_response": raw_content,
                "messages": [
                    {"role": "user", "content": kwargs["persist_user_message"]},
                    {
                        "role": "assistant",
                        "content": raw_content,
                        "reasoning": raw_reasoning,
                        "tool_calls": [{
                            "id": "call-internal",
                            "function": {"name": "read_file", "arguments": raw_args},
                        }],
                    },
                ],
            }

    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: FakeAgent)
    monkeypatch.setattr(streaming, "resolve_model_provider", lambda *args, **kwargs: ("test-model", None, None))
    monkeypatch.setattr(streaming, "get_config", lambda: {})
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda *args, **kwargs: [])
    event_queue = queue.Queue()
    config.STREAMS[stream_id] = event_queue
    try:
        streaming._run_agent_streaming(
            session_id=sid,
            msg_text=raw_user,
            model="test-model",
            workspace=str(tmp_path),
            stream_id=stream_id,
            attachments=[],
        )
    finally:
        config.STREAMS.pop(stream_id, None)

    saved = Session.load(sid)
    visible_assistant = saved.messages[-1]
    internal_assistant = saved.context_messages[-1]
    assert saved.messages[-2]["content"] == raw_user
    assert "/Users/alice" not in visible_assistant["content"]
    assert "/private/customer" not in visible_assistant["reasoning"]
    assert visible_assistant["tool_calls"] == [
        {"event_type": "tool.started", "name": "read_file", "tid": "call-internal"}
    ]
    assert internal_assistant["content"] == raw_content
    assert internal_assistant["reasoning"] == raw_reasoning
    assert internal_assistant["tool_calls"][0]["function"]["arguments"] == raw_args

    from api.brand_privacy import public_session_projection, scrub_public_export_payload

    live_events = []
    while not event_queue.empty():
        live_events.append(event_queue.get_nowait())
    public_reload = public_session_projection({
        **saved.compact(),
        "messages": saved.messages,
    })
    public_export = scrub_public_export_payload(saved.__dict__)
    public_serialized = json.dumps(
        {"live": live_events, "reload": public_reload, "export": public_export},
        ensure_ascii=False,
    )
    assert "/Users/alice" not in public_serialized
    assert "/private/customer" not in public_serialized
    assert "sk-tool-original" not in public_serialized
