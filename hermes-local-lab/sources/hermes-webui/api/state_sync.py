"""
Hermes Web UI -- Optional state.db sync bridge.

Mirrors WebUI session metadata (token usage, title, model) into the
hermes-agent state.db so that /insights, session lists, and cost
tracking include WebUI activity.

This is opt-in via the 'sync_to_insights' setting (default: off).
All operations are wrapped in try/except -- if state.db is unavailable,
locked, or the schema doesn't match, the WebUI continues normally.

The bridge uses absolute token counts (not deltas) because the WebUI
Session object already accumulates totals across turns. This avoids
any double-counting risk.
"""
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def semantic_messages_for_state(messages) -> list[dict]:
    """Return completed user/assistant/tool rows using canonical validation."""
    import copy
    from api.streaming import _api_safe_message_positions

    source = list(messages or [])
    completed = []
    for source_index, safe_message in _api_safe_message_positions(source):
        if safe_message.get("role") not in {"user", "assistant", "tool"}:
            continue
        projected = copy.deepcopy(safe_message)
        original = source[source_index]
        platform_message_id = original.get("platform_message_id") or original.get("message_id")
        if platform_message_id:
            projected["platform_message_id"] = platform_message_id
        completed.append(projected)
    return completed


def _get_state_db(
    profile: Optional[str] = None,
    *,
    strict: bool = False,
    create_if_missing: bool = False,
):
    """Get a SessionDB instance for a profile's state.db.

    When ``profile`` is provided the function resolves *that* profile's
    home directory directly (via ``_resolve_profile_home_for_name``).
    If resolution fails (unknown profile name, IO error, etc.) the
    function returns ``None`` rather than silently falling back to
    ``HERMES_HOME`` — silently routing the write to the wrong DB
    would defeat the point of the explicit-profile path (#2762).

    When ``profile`` is None it falls back to the TLS-based
    ``get_active_hermes_home()`` lookup for backward compatibility,
    with a final ``HERMES_HOME`` fallback only on that path. TLS may be
    unset in background/worker threads, in which case the lookup falls
    through to the process-global active profile and can write to the
    wrong DB. Callers that know the session's profile (e.g.
    ``sync_session_usage`` after a stream completes on a background
    thread) should pass it explicitly to avoid that race.

    In the default best-effort mode, returns ``None`` if hermes_state is not
    importable, the explicit profile cannot be resolved, or the DB is
    unavailable.  ``strict=True`` surfaces those conditions as RuntimeError;
    ``create_if_missing=True`` lets SessionDB initialize a first-install
    profile through its normal schema path.  Each successful caller is
    responsible for calling ``db.close()`` when done.
    """
    if profile is not None:
        # Explicit-profile path — a resolution failure here MUST NOT
        # silently fall back to HERMES_HOME or the caller's "write to
        # the named profile" contract is broken (the original #2762
        # symptom: writes leaking into the wrong profile's state.db).
        #
        # Defense-in-depth (per #2827 maintainer review): validate the
        # name shape BEFORE handing it to ``_resolve_profile_home_for_name``.
        # The resolver itself rarely raises — for an invalid-but-non-
        # malicious name (e.g. one that fails ``_PROFILE_ID_RE``) it
        # quietly returns ``_DEFAULT_HERMES_HOME``, which is the exact
        # leak we're trying to prevent on the explicit-profile path.
        # Validating up-front turns that quiet leak into an explicit
        # "refuse + log + return None" so the contract is "write to
        # the EXACT named profile, or write nowhere."
        try:
            from api.profiles import (
                _resolve_profile_home_for_name,
                _PROFILE_ID_RE,
                _is_root_profile,
            )
            if not (_is_root_profile(profile) or _PROFILE_ID_RE.fullmatch(profile)):
                logger.warning(
                    "state_sync: refusing invalid profile name %r — skipping "
                    "write rather than leaking to the default state.db (#2762).",
                    profile,
                )
                if strict:
                    raise RuntimeError(f"invalid state.db profile {profile!r}")
                return None
            hermes_home = Path(_resolve_profile_home_for_name(profile)).expanduser().resolve()
        except Exception as exc:
            if strict:
                raise RuntimeError(
                    f"could not resolve state.db profile {profile!r}"
                ) from exc
            logger.warning(
                "state_sync: could not resolve profile %r — skipping write rather "
                "than leaking to the active profile (#2762).", profile,
            )
            return None
    else:
        # Implicit / TLS-fallback path — preserves pre-#2762 behavior
        # for any caller that doesn't pass profile= explicitly.
        try:
            from api.profiles import get_active_hermes_home
            hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
        except Exception:
            logger.debug("Failed to resolve hermes home, using default")
            hermes_home = Path(os.getenv('HERMES_HOME', str(Path.home() / '.hermes')))

    db_path = hermes_home / 'state.db'
    if not db_path.exists():
        if not create_if_missing:
            if strict:
                raise RuntimeError(f"state.db does not exist at {db_path}")
            return None
        try:
            hermes_home.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            if strict:
                raise RuntimeError(
                    f"failed to create state.db home at {hermes_home}"
                ) from exc
            return None

    try:
        from hermes_state import SessionDB
    except ImportError as exc:
        if strict:
            raise RuntimeError("state.db support is unavailable") from exc
        return None

    try:
        return SessionDB(db_path)
    except Exception as exc:
        if strict:
            raise RuntimeError(f"failed to open existing state.db at {db_path}") from exc
        logger.debug("Failed to open state.db")
        return None


def sync_session_start(session_id: str, model=None, profile: Optional[str] = None) -> None:
    """Register a WebUI session in state.db (idempotent).
    Called when a session's first message is sent.

    ``profile`` lets the caller name the target state.db explicitly,
    avoiding the TLS-vs-background-thread mismatch in #2762. When
    omitted, the active profile is resolved from TLS (then process
    globals) as before.
    """
    db = _get_state_db(profile=profile)
    if not db:
        return
    try:
        db.ensure_session(
            session_id=session_id,
            source='webui',
            model=model,
        )
    except Exception:
        logger.debug("Failed to sync session start to state.db")
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close state.db")


def sync_webui_user_turn(
    *,
    session_id: str,
    content,
    turn_id: str,
    model=None,
    profile: Optional[str] = None,
) -> bool:
    """Durably checkpoint one accepted WebUI user turn before its worker runs."""
    db = _get_state_db(
        profile=profile,
        strict=True,
        create_if_missing=True,
    )
    if not db:
        raise RuntimeError(f"state.db unavailable for WebUI session {session_id}")
    try:
        db.ensure_session(session_id=session_id, source="webui", model=model)
        db.append_message(
            session_id=session_id,
            role="user",
            content=content,
            platform_message_id=f"webui-turn:{turn_id}",
        )
        return True
    except Exception as exc:
        raise RuntimeError(
            f"failed to checkpoint WebUI user turn {turn_id}"
        ) from exc
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close state.db")


def replace_webui_session_messages(
    *,
    session_id: str,
    messages,
    model=None,
    profile: Optional[str] = None,
) -> bool:
    """Atomically replace the semantic transcript in the profile state.db.

    A first-install profile gets its database through SessionDB's normal schema
    creation path. Resolution, creation, open, or replacement failures are
    surfaced so lifecycle endpoints cannot report success with stale history.
    """
    db = _get_state_db(
        profile=profile,
        strict=True,
        create_if_missing=True,
    )
    if not db:
        raise RuntimeError(f"state.db unavailable for session {session_id}")
    try:
        db.replace_messages(
            session_id,
            list(messages or []),
            ensure_source="webui",
            ensure_model=model,
        )
        return True
    except Exception as exc:
        raise RuntimeError(
            f"failed to replace state.db transcript for session {session_id}"
        ) from exc
    finally:
        try:
            db.close()
        except Exception:
            logger.warning("Failed to close state.db after transcript replacement")


def sync_session_usage(session_id: str, input_tokens: int=0, output_tokens: int=0,
                       estimated_cost=None, model=None, title: Optional[str] = None,
                       message_count: Optional[int] = None, profile: Optional[str] = None) -> None:
    """Update token usage and title for a WebUI session in state.db.
    Called after each turn completes. Uses absolute=True to set totals
    (the WebUI Session already accumulates across turns).

    ``profile`` lets the caller name the target state.db explicitly,
    which is what fixes #2762: this function is invoked from the
    agent streaming worker thread, where the request-thread's TLS
    profile context has not been propagated. Without an explicit
    profile, the TLS lookup falls back to the process-global active
    profile and writes the session's usage to the wrong state.db
    (e.g. ``hiyuki``'s instead of the cookie-switched ``maiko``'s).
    """
    db = _get_state_db(profile=profile)
    if not db:
        return
    try:
        # Ensure session exists first (idempotent)
        db.ensure_session(session_id=session_id, source='webui', model=model)
        # Set absolute token counts
        db.update_token_counts(
            session_id=session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            model=model,
            absolute=True,
        )
        # Update title if we have one, using the public API
        if title:
            try:
                db.set_session_title(session_id, title)
            except Exception:
                logger.debug("Failed to sync session title to state.db")
        # Update message count
        if message_count is not None:
            try:
                def _set_msg_count(conn):
                    conn.execute(
                        "UPDATE sessions SET message_count = ? WHERE id = ?",
                        (message_count, session_id),
                    )
                db._execute_write(_set_msg_count)
            except Exception:
                logger.debug("Failed to sync message count to state.db")
    except Exception:
        logger.debug("Failed to sync session usage to state.db")
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close state.db")
