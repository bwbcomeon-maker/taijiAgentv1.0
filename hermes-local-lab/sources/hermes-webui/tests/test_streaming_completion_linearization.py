import threading
import time

import pytest

from tests.test_artifact_registry import _run_legacy_image_turn


@pytest.mark.requires_agent_modules
def test_late_cancel_after_successful_save_cannot_rewrite_completed_turn(
    monkeypatch,
    tmp_path,
):
    """Successful persistence is the turn's completion linearization point."""
    import api.config as config
    import api.models as models
    import api.streaming as streaming

    original_save = models.Session.save
    cancel_threads: list[threading.Thread] = []
    cancel_results: list[bool] = []
    cancel_started = threading.Event()

    def save_then_race_cancel(self, *args, **kwargs):
        result = original_save(self, *args, **kwargs)
        is_completed_turn = any(
            isinstance(message, dict)
            and message.get("role") == "assistant"
            and message.get("content") == "image generated"
            for message in self.messages
        )
        if is_completed_turn and not cancel_started.is_set():
            cancel_started.set()
            stream_id = f"stream-{self.session_id}"
            cancel_flag = config.CANCEL_FLAGS[stream_id]
            cancel_thread = threading.Thread(
                target=lambda: cancel_results.append(
                    streaming.cancel_stream(stream_id)
                ),
                daemon=True,
            )
            cancel_threads.append(cancel_thread)
            cancel_thread.start()
            deadline = time.monotonic() + 2
            while not cancel_flag.is_set() and time.monotonic() < deadline:
                time.sleep(0.001)
            assert cancel_flag.is_set(), "late cancel did not reach the worker"
        return result

    monkeypatch.setattr(models.Session, "save", save_then_race_cancel)

    saved, _artifact_root, events = _run_legacy_image_turn(
        monkeypatch,
        tmp_path,
    )

    for cancel_thread in cancel_threads:
        cancel_thread.join(timeout=2)
        assert not cancel_thread.is_alive()

    event_names = [event for event, _data in list(events.queue)]
    assistant_messages = [
        message
        for message in saved.messages
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]

    assert cancel_started.is_set()
    assert cancel_results == [True]
    assert "done" in event_names
    assert "cancel" not in event_names
    assert any(
        message.get("content") == "image generated"
        for message in assistant_messages
    )
    assert all(
        "cancel" not in str(message.get("content") or "").lower()
        for message in assistant_messages
    )
