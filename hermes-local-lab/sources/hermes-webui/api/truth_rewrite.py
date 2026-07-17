"""Crash recovery for coordinated WebUI sidecar/state.db transcript rewrites.

This is a narrow transaction journal, not a legacy-data migration.  It stores
only opaque before/target semantic hashes.  The target payload is recovered
from the already-committed sidecar, so credentials, message text, tool
arguments, filesystem paths, and provider results never enter the intent.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import string
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Callable


INTENT_SCHEMA_VERSION = "taiji-truth-rewrite-intent/v1"
_INTENT_DIR_NAME = ".truth-rewrite-intents"
_HASH_FIELDS = (
    "role",
    "content",
    "tool_call_id",
    "tool_calls",
    "tool_name",
    "token_count",
    "finish_reason",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
    "platform_message_id",
    "observed",
)
_INTENT_KEYS = {
    "schema_version",
    "session_id",
    "profile",
    "before_semantic_sha256",
    "target_semantic_sha256",
    "created_at",
}
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


class TruthRewriteRecoveryError(RuntimeError):
    """The durable intent could not be reconciled without guessing."""


def _safe_session_id(session_id: object) -> str:
    value = str(session_id or "")
    allowed = frozenset(string.ascii_letters + string.digits + "_-")
    if not value or any(char not in allowed for char in value):
        raise TruthRewriteRecoveryError("truth rewrite intent has an unsafe session id")
    return value


def _safe_profile(profile: object) -> str | None:
    if profile is None:
        return None
    value = str(profile)
    allowed = frozenset(string.ascii_lowercase + string.digits + "_-")
    if (
        not value
        or len(value) > 64
        or value[0] not in frozenset(string.ascii_lowercase + string.digits)
        or any(char not in allowed for char in value)
    ):
        raise TruthRewriteRecoveryError("truth rewrite intent has an unsafe profile")
    return value


def _session_path(session) -> Path:
    value = getattr(session, "path", None)
    path = value() if callable(value) else value
    if path is None:
        raise TruthRewriteRecoveryError("truth rewrite session path is unavailable")
    return Path(path)


def truth_rewrite_intent_path(session) -> Path:
    session_id = _safe_session_id(getattr(session, "session_id", None))
    return _session_path(session).parent / _INTENT_DIR_NAME / f"{session_id}.json"


@contextmanager
def truth_rewrite_lock(session_id: object):
    key = _safe_session_id(session_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


def _normalized_semantic_messages(messages) -> list[dict]:
    from api.state_sync import semantic_messages_for_state

    normalized = []
    for message in semantic_messages_for_state(list(messages or [])):
        if not isinstance(message, dict):
            continue
        row = {}
        for field in _HASH_FIELDS:
            value = message.get(field)
            if field == "observed":
                if value:
                    row[field] = True
                continue
            if value is None or value == "" or value == [] or value == {}:
                continue
            row[field] = value
        if row.get("role") in {"user", "assistant", "tool"}:
            normalized.append(row)
    return normalized


def semantic_truth_hash(messages) -> str:
    payload = json.dumps(
        _normalized_semantic_messages(messages),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(str(directory), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, payload: dict) -> None:
    parent_was_missing = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if parent_was_missing:
        _fsync_directory(path.parent.parent)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    data = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_truth_rewrite_intent(session, before_messages, target_messages) -> Path:
    path = truth_rewrite_intent_path(session)
    if path.exists():
        raise TruthRewriteRecoveryError(
            f"unresolved truth rewrite intent for {getattr(session, 'session_id', '')}"
        )
    payload = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "session_id": _safe_session_id(getattr(session, "session_id", None)),
        "profile": _safe_profile(getattr(session, "profile", None)),
        "before_semantic_sha256": semantic_truth_hash(before_messages),
        "target_semantic_sha256": semantic_truth_hash(target_messages),
        "created_at": time.time(),
    }
    _atomic_write_json(path, payload)
    return path


def clear_truth_rewrite_intent(session) -> None:
    path = truth_rewrite_intent_path(session)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def discard_committed_shrink_backup(session) -> None:
    """Remove the pre-commit shrink snapshot once both truth stores agree.

    ``Session.save`` deliberately creates ``.json.bak`` before any transcript
    shrink.  That snapshot is essential while a coordinated rewrite is still
    in flight, but becomes actively dangerous after state.db and the sidecar
    both commit the shorter transcript: generic startup recovery would
    otherwise mistake the authorized retry/undo/truncate for data loss and
    restore the old messages.  Callers must run this before clearing the
    durable rewrite intent so a crash during cleanup remains retryable.
    """
    backup_path = _session_path(session).with_suffix(".json.bak")
    try:
        backup_path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(backup_path.parent)


def _read_intent_file(path: Path, expected_session_id: object) -> dict | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TruthRewriteRecoveryError("truth rewrite intent is not a regular file")
    if info.st_size > 4096:
        raise TruthRewriteRecoveryError("truth rewrite intent exceeds the size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TruthRewriteRecoveryError("truth rewrite intent is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != _INTENT_KEYS:
        raise TruthRewriteRecoveryError("truth rewrite intent fields are invalid")
    if payload.get("schema_version") != INTENT_SCHEMA_VERSION:
        raise TruthRewriteRecoveryError("truth rewrite intent version is unsupported")
    if payload.get("session_id") != _safe_session_id(expected_session_id):
        raise TruthRewriteRecoveryError("truth rewrite intent session does not match")
    _safe_profile(payload.get("profile"))
    for field in ("before_semantic_sha256", "target_semantic_sha256"):
        value = payload.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise TruthRewriteRecoveryError("truth rewrite intent hash is invalid")
    if not isinstance(payload.get("created_at"), (int, float)):
        raise TruthRewriteRecoveryError("truth rewrite intent timestamp is invalid")
    return payload


def _read_intent(session) -> dict | None:
    payload = _read_intent_file(
        truth_rewrite_intent_path(session),
        getattr(session, "session_id", None),
    )
    if payload is not None and payload.get("profile") != _safe_profile(
        getattr(session, "profile", None)
    ):
        raise TruthRewriteRecoveryError("truth rewrite intent profile does not match")
    return payload


def _default_read_state_messages(session) -> list[dict]:
    from api.state_sync import _get_state_db

    db = _get_state_db(
        profile=getattr(session, "profile", None),
        strict=True,
        create_if_missing=True,
    )
    try:
        return list(db.get_messages(str(session.session_id)) or [])
    finally:
        db.close()


def _default_replace_state_messages(session, messages) -> bool:
    from api.state_sync import replace_webui_session_messages

    return replace_webui_session_messages(
        session_id=str(session.session_id),
        messages=list(messages or []),
        model=getattr(session, "model", None),
        profile=getattr(session, "profile", None),
    )


def recover_truth_rewrite_intent(
    session,
    *,
    read_state_messages: Callable | None = None,
    replace_state_messages: Callable | None = None,
) -> dict:
    """Recover one crash intent only when the two hashes prove the action."""
    path = truth_rewrite_intent_path(session)
    if not path.exists():
        return {
            "kind": "truth_rewrite_crash_recovery",
            "status": "not_needed",
            "session_id": str(session.session_id),
        }

    from api.legacy_session_migration import legacy_migration_state_guard

    with legacy_migration_state_guard(), truth_rewrite_lock(session.session_id):
        intent = _read_intent(session)
        if intent is None:
            return {
                "kind": "truth_rewrite_crash_recovery",
                "status": "not_needed",
                "session_id": str(session.session_id),
            }
        read_state = read_state_messages or _default_read_state_messages
        replace_state = replace_state_messages or _default_replace_state_messages
        sidecar_hash = semantic_truth_hash(getattr(session, "messages", None) or [])
        state_hash = semantic_truth_hash(read_state(session))
        before_hash = intent["before_semantic_sha256"]
        target_hash = intent["target_semantic_sha256"]

        if sidecar_hash == before_hash and state_hash == before_hash:
            clear_truth_rewrite_intent(session)
            status = "aborted"
        elif sidecar_hash == target_hash and state_hash == before_hash:
            replaced = replace_state(session, list(getattr(session, "messages", None) or []))
            if replaced is not True:
                raise TruthRewriteRecoveryError("truth rewrite roll-forward was not confirmed")
            discard_committed_shrink_backup(session)
            clear_truth_rewrite_intent(session)
            status = "rolled_forward"
        elif sidecar_hash == target_hash and state_hash == target_hash:
            discard_committed_shrink_backup(session)
            clear_truth_rewrite_intent(session)
            status = "completed"
        else:
            return {
                "kind": "truth_rewrite_crash_recovery",
                "status": "diverged",
                "reason": "sidecar_or_state_hash_unknown",
                "session_id": str(session.session_id),
            }
        return {
            "kind": "truth_rewrite_crash_recovery",
            "status": status,
            "session_id": str(session.session_id),
        }


def recover_orphan_truth_rewrite_intents(
    session_dir: Path,
    *,
    read_state_messages: Callable | None = None,
) -> list[dict]:
    """Recover durable intents before the HTTP server becomes visible.

    A new-session crash can happen after the durable marker is fsynced but
    before ``Session.save`` creates ``<session_id>.json``.  No normal
    ``Session.load`` can discover that marker, so startup scans the private
    intent directory.  It removes an orphan only when state.db still matches
    the marker's before hash; every other state remains blocked for audit.

    When the sidecar *was* published, startup must eagerly invoke the normal
    full-load recovery as well.  Otherwise a new conversation that crashed
    before its index update would remain invisible forever because the sidebar
    fast path only consumes the existing index.
    """
    session_dir = Path(session_dir)
    intent_dir = session_dir / _INTENT_DIR_NAME
    try:
        marker_paths = sorted(intent_dir.glob("*.json"))
    except OSError as exc:
        raise TruthRewriteRecoveryError(
            "truth rewrite intent directory is unreadable"
        ) from exc

    from api.legacy_session_migration import legacy_migration_state_guard

    outcomes = []
    for marker_path in marker_paths:
        session_id = marker_path.stem
        try:
            session_id = _safe_session_id(session_id)
            with legacy_migration_state_guard(), truth_rewrite_lock(session_id):
                sidecar_path = session_dir / f"{session_id}.json"
                if sidecar_path.exists():
                    from api.models import Session

                    loaded = Session.load(session_id)
                    if loaded is None or marker_path.exists():
                        outcomes.append(
                            {
                                "kind": "truth_rewrite_crash_recovery",
                                "status": "diverged",
                                "reason": "published_sidecar_unresolved",
                                "session_id": session_id,
                            }
                        )
                    else:
                        outcomes.append(
                            {
                                "kind": "truth_rewrite_crash_recovery",
                                "status": "existing_recovered",
                                "session_id": session_id,
                            }
                        )
                    continue
                intent = _read_intent_file(marker_path, session_id)
                if intent is None:
                    continue
                session = SimpleNamespace(
                    session_id=session_id,
                    path=sidecar_path,
                    messages=[],
                    model=None,
                    profile=intent.get("profile"),
                )
                read_state = read_state_messages or _default_read_state_messages
                state_hash = semantic_truth_hash(read_state(session))
                if state_hash == intent["before_semantic_sha256"]:
                    clear_truth_rewrite_intent(session)
                    outcomes.append(
                        {
                            "kind": "truth_rewrite_crash_recovery",
                            "status": "orphan_aborted",
                            "session_id": session_id,
                        }
                    )
                else:
                    outcomes.append(
                        {
                            "kind": "truth_rewrite_crash_recovery",
                            "status": "diverged",
                            "reason": "sidecar_missing_state_not_before",
                            "session_id": session_id,
                        }
                    )
        except TruthRewriteRecoveryError:
            outcomes.append(
                {
                    "kind": "truth_rewrite_crash_recovery",
                    "status": "invalid",
                    "reason": "invalid_intent",
                    "session_id": session_id,
                }
            )
        except Exception:
            outcomes.append(
                {
                    "kind": "truth_rewrite_crash_recovery",
                    "status": "error",
                    "reason": "state_unavailable",
                    "session_id": session_id,
                }
            )
    return outcomes
