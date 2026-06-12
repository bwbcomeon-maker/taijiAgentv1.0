"""Regression tests for per-turn response duration in WebUI.

The WebUI should expose how long an agent turn took, using backend timing so
reload/reconnect does not lose the measurement.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STREAMING_PY = (REPO / "api" / "streaming.py").read_text(encoding="utf-8")
MESSAGES_JS = (REPO / "static" / "messages.js").read_text(encoding="utf-8")
ROUTES_PY = (REPO / "api" / "routes.py").read_text(encoding="utf-8")
TURN_DURATION_PY = (REPO / "api" / "turn_duration.py").read_text(encoding="utf-8")
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")


def test_streaming_done_payload_includes_backend_turn_duration():
    assert "duration_seconds" in STREAMING_PY, (
        "api/streaming.py should include a backend-measured duration_seconds "
        "field in the done usage payload."
    )
    assert "pending_started_at" in STREAMING_PY and "time.time()" in STREAMING_PY, (
        "Turn duration should be measured from the persisted pending_started_at "
        "start time, not only from browser-local state."
    )
    assert "recovered/legacy flows" in STREAMING_PY, (
        "The missing-start fallback should be documented so it is not mistaken "
        "for the primary timing path."
    )
    assert (
        "stamp_turn_duration_on_latest_assistant" in STREAMING_PY
        and "_turnDuration" in TURN_DURATION_PY
    ), (
        "The measured duration should be persisted through the shared turn-duration "
        "helper so it survives reload after the SSE stream settles."
    )


def test_done_handler_persists_duration_on_last_assistant_message():
    assert "d.usage.duration_seconds" in MESSAGES_JS, (
        "static/messages.js should read duration_seconds from the done usage payload."
    )
    assert "lastAsst._turnDuration" in MESSAGES_JS, (
        "The done handler should attach the duration to the last assistant message "
        "so renderMessages() can display it after the live stream settles."
    )


def test_ui_formats_and_renders_turn_duration_in_footer_and_activity_summary():
    assert "function _formatTurnDuration" in UI_JS, (
        "ui.js should centralize duration formatting for footer and compact activity display."
    )
    assert "msg-duration-inline" in UI_JS and "Done in" in UI_JS, (
        "Expanded/non-activity display should show a subtle footer chip like 'Done in 42s'."
    )
    assert "tool-call-group-duration" in UI_JS, (
        "Compact tool activity summary should have a dedicated duration span at the end of the line."
    )
    assert "data-turn-duration" in UI_JS, (
        "Activity groups need a stable data-turn-duration hook so settled duration can update the summary."
    )
    assert "compactActivityForMessage" in UI_JS, (
        "When compact activity is present, duration should live on the Activity row "
        "instead of being duplicated in the assistant footer."
    )
    assert ".msg-duration-inline" in CSS and ".tool-call-group-duration" in CSS, (
        "Duration UI should have explicit CSS hooks for the footer chip and compact activity summary."
    )


def test_public_activity_duration_row_covers_plain_assistant_turns():
    assert "function _publicActivityDurationForMessage" in UI_JS, (
        "Public activity mode should compute a visible duration for plain assistant "
        "turns, not only turns that had tool or thinking metadata."
    )
    assert "assistantIdxs.filter" in UI_JS and "_publicActivityDurationForMessage(S.messages[aIdx]" in UI_JS, (
        "When activity_details=false, settled public duration rows should include "
        "assistant messages with _turnDuration even if there were no tools."
    )
    assert "publicActivityForMessage" in UI_JS and "_publicActivityDurationForMessage(msg" in UI_JS, (
        "The footer duration chip should be suppressed when the public duration "
        "row is rendered, avoiding duplicate per-turn time labels."
    )


def test_public_activity_duration_does_not_invent_zero_from_equal_timestamps():
    helper_start = UI_JS.index("function _publicActivityDurationForMessage")
    helper_end = UI_JS.index("function _syncPublicActivityStatus", helper_start)
    helper = UI_JS[helper_start:helper_end]
    assert "direct>0" in helper
    assert "direct>=0" not in helper
    assert "ended>started" in helper, (
        "Timestamp fallback must only render a duration when the end timestamp is "
        "strictly later than the start timestamp; equal timestamps mean unknown "
        "legacy duration, not '00:00'."
    )
    assert "ended>=started" not in helper
    fallback_start = MESSAGES_JS.index("function _attachFallbackTurnDuration")
    fallback_end = MESSAGES_JS.index("function _markActiveSessionViewedOnReturn", fallback_start)
    fallback = MESSAGES_JS[fallback_start:fallback_end]
    assert "ended<=started" in fallback, (
        "The client-side fallback should not write _turnDuration=0 when start "
        "and end timestamps collapse to the same second."
    )
    assert "Math.max(0,ended-started)" not in fallback


def test_active_compact_activity_elapsed_timer_uses_persisted_start_time():
    assert '"pending_started_at": s.pending_started_at' in ROUTES_PY, (
        "/api/chat/start should return the persisted pending_started_at timestamp "
        "so the live timer starts from backend/session truth."
    )
    assert "startData.pending_started_at" in MESSAGES_JS, (
        "send() should copy chat-start pending_started_at into S.session before "
        "attaching the live stream."
    )
    assert "function _formatActiveElapsedTimer" in UI_JS and "padStart(2,'0')" in UI_JS, (
        "ui.js should format the running timer in MM:SS form."
    )
    assert "data-turn-started-at" in UI_JS and "data-active-turn-elapsed" in UI_JS, (
        "Live compact Activity groups need stable start-time and active-elapsed "
        "hooks for browser QA and reconnect/rerender safety."
    )
    assert "Working " in UI_JS, (
        "The in-progress Activity summary should distinguish the live counter "
        "from the settled 'Done in …' duration."
    )
    assert "setInterval" in UI_JS and "_clearActivityElapsedTimer" in UI_JS, (
        "The active elapsed label should tick while running and clear its interval "
        "on terminal/error/session-switch cleanup paths."
    )
