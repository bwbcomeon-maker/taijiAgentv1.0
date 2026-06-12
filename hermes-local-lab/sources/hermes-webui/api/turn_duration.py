"""Helpers for persisting per-turn execution duration on assistant messages."""

from __future__ import annotations

import math
import time
from typing import Any


def _epoch_seconds(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    if seconds > 100_000_000_000:
        seconds = seconds / 1000.0
    return seconds


def compute_turn_duration_seconds(started_at: Any, ended_at: Any = None) -> float | None:
    started = _epoch_seconds(started_at)
    if started is None:
        return None
    ended = _epoch_seconds(time.time() if ended_at is None else ended_at)
    if ended is None or ended < started:
        return None
    elapsed = ended - started
    if elapsed > 0 and elapsed < 0.001:
        elapsed = 0.001
    return round(elapsed, 3)


def stamp_turn_duration_on_latest_assistant(
    session: Any,
    started_at: Any = None,
    ended_at: Any = None,
) -> float | None:
    """Persist turn duration on the newest assistant message in ``session``."""

    if started_at is None:
        started_at = getattr(session, "pending_started_at", None)
    duration = compute_turn_duration_seconds(started_at, ended_at)
    if duration is None:
        return None
    messages = getattr(session, "messages", None)
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            message["_turnDuration"] = duration
            return duration
    return None
