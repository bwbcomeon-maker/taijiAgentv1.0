from pathlib import Path


def test_webui_drains_only_matching_background_completion_events():
    src = Path("api/streaming.py").read_text(encoding="utf-8")

    assert "def _drain_webui_process_notifications(session_id: str)" in src
    assert "from tools.process_registry import process_registry" in src
    assert "proc = process_registry.get(evt_sid)" in src
    assert "getattr(proc, 'session_key', None) != session_id" in src
    assert "skipped_events.append(evt)" in src
    assert "completion_queue.put(evt)" in src


def test_webui_injects_process_notifications_only_for_ordinary_chat_without_persisting_them():
    src = Path("api/streaming.py").read_text(encoding="utf-8")

    assert "[] if strict_turn else _drain_webui_process_notifications(session_id)" in src
    assert "[*_process_notifications, msg_text]" in src
    assert 'str(turn_envelope.model_messages[1]["content"])' in src
    assert "if strict_turn" in src
    prepare_idx = src.index("else prepare_webui_chat_input(")
    run_idx = src.index("result = agent.run_conversation(", prepare_idx)
    assert prepare_idx < run_idx
    assert "except WebUIChatInputError as exc:" in src[prepare_idx:run_idx]
    assert "put('apperror', exc.payload)" in src[prepare_idx:run_idx]
    assert "if cancel_event.is_set():" in src[prepare_idx:run_idx]
    assert "persist_user_message=persist_msg_text" in src


def test_webui_sets_gateway_session_platform_for_background_watchers():
    src = Path("api/streaming.py").read_text(encoding="utf-8")

    assert "'HERMES_SESSION_PLATFORM': 'webui'" in src
    assert "os.environ['HERMES_SESSION_PLATFORM'] = 'webui'" in src
    assert "old_session_platform = os.environ.get('HERMES_SESSION_PLATFORM')" in src
    assert "os.environ.pop('HERMES_SESSION_PLATFORM', None)" in src
