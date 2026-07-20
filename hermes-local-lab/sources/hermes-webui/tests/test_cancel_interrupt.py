"""
Unit tests for cancel/interrupt functionality.
Tests the integration between cancel_stream() and agent.interrupt().
"""
import queue
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import api.streaming as streaming
from api.streaming import cancel_stream
from api.config import (
    AGENT_INSTANCES,
    CANCEL_FLAGS,
    SESSION_AGENT_CACHE,
    SESSION_AGENT_CACHE_LOCK,
    SESSION_AGENT_RETIREMENT_EPOCHS,
    SESSION_AGENT_RETIREMENT_LOCK,
    SESSION_AGENT_RETIREMENT_STREAM_IDS,
    STREAMS,
    STREAM_SESSION_IDS,
    cache_session_agent_if_current_generation,
    get_session_agent_retirement_epoch,
    retire_session_agent_generation,
)


class TestCancelInterrupt:
    """Test suite for cancel/interrupt functionality"""

    def setup_method(self):
        """Clean up before each test"""
        AGENT_INSTANCES.clear()
        STREAMS.clear()
        CANCEL_FLAGS.clear()
        SESSION_AGENT_CACHE.clear()
        STREAM_SESSION_IDS.clear()
        with SESSION_AGENT_RETIREMENT_LOCK:
            SESSION_AGENT_RETIREMENT_EPOCHS.clear()
            SESSION_AGENT_RETIREMENT_STREAM_IDS.clear()

    def teardown_method(self):
        """Clean up after each test"""
        AGENT_INSTANCES.clear()
        STREAMS.clear()
        CANCEL_FLAGS.clear()
        SESSION_AGENT_CACHE.clear()
        STREAM_SESSION_IDS.clear()
        with SESSION_AGENT_RETIREMENT_LOCK:
            SESSION_AGENT_RETIREMENT_EPOCHS.clear()
            SESSION_AGENT_RETIREMENT_STREAM_IDS.clear()

    def test_cancel_calls_agent_interrupt(self):
        """Verify that cancel_stream() calls agent.interrupt() when agent exists"""
        # Setup
        stream_id = "test_stream_123"
        mock_agent = Mock()
        mock_agent.interrupt = Mock()

        STREAMS[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = mock_agent

        # Execute
        result = cancel_stream(stream_id)

        # Assert
        assert result is True
        mock_agent.interrupt.assert_called_once_with("Cancelled by user")
        # CANCEL_FLAGS is eagerly popped after cancel (#776 fix) so the flag
        # is no longer in the dict — verify the pop happened instead
        assert stream_id not in CANCEL_FLAGS, \
            "cancel_stream() should eagerly pop CANCEL_FLAGS after signalling"

    def test_cancel_retires_cached_agent_before_next_turn_can_reuse_it(self):
        stream_id = "stream-old"
        session_id = "session-shared"
        old_agent = Mock()
        old_agent.session_id = session_id
        old_agent.interrupt = Mock()
        old_agent._codex_session = Mock()
        SESSION_AGENT_CACHE[session_id] = (old_agent, "same-signature")
        STREAMS[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = old_agent

        session = Mock()
        session.active_stream_id = stream_id
        session.pending_user_message = "old request"
        session.pending_attachments = []
        session.pending_started_at = 1.0

        with patch("api.streaming.get_session", return_value=session):
            assert cancel_stream(stream_id) is True

        assert session_id not in SESSION_AGENT_CACHE
        assert old_agent._webui_cancelled_stream_id == stream_id

    def test_cancel_handles_interrupt_exception(self):
        """Verify that cancel_stream() handles interrupt() exceptions gracefully"""
        stream_id = "test_stream_456"
        mock_agent = Mock()
        mock_agent.interrupt = Mock(side_effect=RuntimeError("Agent error"))

        STREAMS[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = mock_agent

        # Should not raise exception
        result = cancel_stream(stream_id)

        # Assert
        assert result is True
        mock_agent.interrupt.assert_called_once()
        assert stream_id not in CANCEL_FLAGS, \
            "cancel_stream() should eagerly pop CANCEL_FLAGS even on interrupt exception"

    def test_cancel_before_agent_ready_retires_generation(self):
        """Test cancel when agent not yet stored in AGENT_INSTANCES (race condition)"""
        stream_id = "test_stream_789"
        session_id = "session-before-agent-ready"

        STREAMS[stream_id] = queue.Queue()
        cancel_event = threading.Event()
        CANCEL_FLAGS[stream_id] = cancel_event
        STREAM_SESSION_IDS[stream_id] = session_id
        old_cached_agent = Mock()
        SESSION_AGENT_CACHE[session_id] = (
            old_cached_agent,
            "same-signature",
        )
        # Note: AGENT_INSTANCES[stream_id] not set (simulating race condition)

        session = Mock()
        session.active_stream_id = stream_id
        session.messages = []
        session.pending_user_message = None
        session.pending_attachments = []
        session.pending_started_at = None

        with patch("api.streaming.get_session", return_value=session):
            result = cancel_stream(stream_id)

        # Assert
        assert result is True
        # CANCEL_FLAGS is eagerly popped; the agent thread checks the event
        # object it already has a reference to — pop doesn't clear the event
        assert stream_id not in CANCEL_FLAGS, \
            "cancel_stream() should eagerly pop CANCEL_FLAGS even without an agent"
        assert cancel_event.is_set()
        assert get_session_agent_retirement_epoch(session_id) == 1
        assert (
            SESSION_AGENT_RETIREMENT_STREAM_IDS[(session_id, 1)]
            == stream_id
        )
        assert session_id not in SESSION_AGENT_CACHE
        assert (
            old_cached_agent._webui_cancelled_stream_id
            == stream_id
        )
        # A worker that captured epoch 0 before cancel cannot cache or reuse an
        # agent after the epoch advances, even though cancel won before the
        # AIAgent was stored in AGENT_INSTANCES.
        assert (
            streaming._agent_generation_is_current(
                session_id,
                0,
                cancel_event,
            )
            is False
        )

    def test_cancel_new_turn_cache_pop_preserves_old_cleanup_owner(self, monkeypatch):
        """Deterministic cancel bump -> new pop -> old finally interleaving."""
        session_id = "session-overlap"
        old_stream_id = "stream-old"
        new_stream_id = "stream-new"

        class ResourceAgent:
            def __init__(self, stream_session_id):
                self.session_id = stream_session_id
                self.interrupt = Mock()
                self._codex_session = Mock()
                self.shutdown_memory_provider = Mock()
                self._session_db = Mock()
                self._session_messages = [
                    {"role": "user", "content": "old turn"}
                ]

        old_agent = ResourceAgent(session_id)
        new_agent = ResourceAgent(session_id)
        old_codex_session = old_agent._codex_session
        old_session_db = old_agent._session_db

        STREAMS[old_stream_id] = queue.Queue()
        CANCEL_FLAGS[old_stream_id] = threading.Event()
        AGENT_INSTANCES[old_stream_id] = old_agent
        STREAM_SESSION_IDS[old_stream_id] = session_id
        SESSION_AGENT_CACHE[session_id] = (
            old_agent,
            "same-signature",
            0,
        )

        session = Mock()
        session.active_stream_id = old_stream_id
        session.messages = []
        session.pending_user_message = "old request"
        session.pending_attachments = []
        session.pending_started_at = 1.0
        monkeypatch.setattr(streaming, "get_session", lambda _sid: session)
        monkeypatch.setattr(
            streaming,
            "_lifecycle_prepare_agent_retirement",
            lambda _sid, _agent, wait=True: True,
        )

        cancel_result = []
        cancel_thread = threading.Thread(
            target=lambda: cancel_result.append(
                cancel_stream(old_stream_id)
            ),
            daemon=True,
        )

        # Hold the cache lock so cancel can advance the retirement epoch under
        # STREAMS_LOCK but cannot yet pop the old cache entry.
        with SESSION_AGENT_CACHE_LOCK:
            cancel_thread.start()
            deadline = time.monotonic() + 2
            while (
                get_session_agent_retirement_epoch(session_id) != 1
                and time.monotonic() < deadline
            ):
                time.sleep(0.001)
            assert get_session_agent_retirement_epoch(session_id) == 1

            # Deterministically model the newer worker winning the cache race.
            retired_entry = SESSION_AGENT_CACHE.pop(session_id)
            SESSION_AGENT_CACHE[session_id] = (
                new_agent,
                "same-signature",
                1,
            )

        # Production performs this after leaving SESSION_AGENT_CACHE_LOCK.
        assert (
            streaming._mark_agent_cancelled_for_retired_generation(
                session_id,
                0,
                retired_entry[0],
            )
            == old_stream_id
        )
        cancel_thread.join(timeout=2)
        assert not cancel_thread.is_alive()
        assert cancel_result == [True]

        # The old worker finally owns and performs cleanup. Calling it again is
        # a no-op, and the new generation remains cached and fully alive.
        streaming._finalize_cancelled_agent_after_worker(
            session_id,
            old_stream_id,
            old_agent,
        )
        streaming._finalize_cancelled_agent_after_worker(
            session_id,
            old_stream_id,
            old_agent,
        )
        streaming._finalize_cancelled_agent_after_worker(
            session_id,
            new_stream_id,
            new_agent,
        )

        assert old_agent._webui_cancelled_stream_id == old_stream_id
        old_codex_session.close.assert_called_once_with()
        old_agent.shutdown_memory_provider.assert_called_once_with(
            old_agent._session_messages
        )
        old_session_db.close.assert_called_once_with()
        assert SESSION_AGENT_CACHE[session_id][0] is new_agent
        new_agent._codex_session.close.assert_not_called()
        new_agent.shutdown_memory_provider.assert_not_called()
        new_agent._session_db.close.assert_not_called()

    def test_chat_route_publishes_session_identity_with_stream(self):
        source = (
            Path(__file__).resolve().parents[1] / "api" / "routes.py"
        ).read_text(encoding="utf-8")
        start = source.index("def _start_chat_stream_for_session(")
        end = source.index(
            "def _handle_chat_start(",
            start,
        )
        route = source[start:end]
        registration = (
            "with STREAMS_LOCK:\n"
            "                    STREAMS[stream_id] = stream\n"
            "                    STREAM_SESSION_IDS[stream_id] = s.session_id"
        )

        assert registration in route
        assert route.index(registration) < route.index(
            "start_legacy_migration_guarded_worker("
        )

    def test_retired_epoch_cannot_publish_agent_after_cancel_cache_pop(self):
        session_id = "session-check-store-gap"
        stream_id = "stream-cancelled-before-cache-store"
        cancel_event = threading.Event()

        assert retire_session_agent_generation(
            session_id,
            stream_id,
        ) == 1
        cached, evicted = cache_session_agent_if_current_generation(
            session_id,
            (Mock(), "same-signature", 0),
            expected_epoch=0,
            cancel_event=cancel_event,
        )

        assert cached is False
        assert evicted == []
        assert session_id not in SESSION_AGENT_CACHE

    def test_cancel_nonexistent_stream(self):
        """Test cancel for a stream that doesn't exist"""
        result = cancel_stream("nonexistent_stream")
        assert result is False

    def test_cancel_sets_cancel_event(self):
        """Verify that cancel_stream() sets the cancel_event flag"""
        stream_id = "test_stream_event"

        STREAMS[stream_id] = queue.Queue()
        cancel_event = threading.Event()
        CANCEL_FLAGS[stream_id] = cancel_event

        result = cancel_stream(stream_id)

        assert result is True
        assert cancel_event.is_set()

    def test_cancel_puts_sentinel_in_queue(self):
        """Verify that cancel_stream() puts cancel sentinel in queue"""
        stream_id = "test_stream_queue"
        q = queue.Queue()

        STREAMS[stream_id] = q
        CANCEL_FLAGS[stream_id] = threading.Event()

        result = cancel_stream(stream_id)

        assert result is True
        # Check that cancel message was queued
        assert not q.empty()
        event_type, data = q.get_nowait()
        assert event_type == 'cancel'
        assert data['message'] == 'Cancelled by user'
