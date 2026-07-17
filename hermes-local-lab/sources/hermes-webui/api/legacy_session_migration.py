"""Audited, opt-in repair for legacy WebUI session state.

Startup callers may run :func:`audit_legacy_sessions`; it is deliberately
read-only.  Applying a repair requires an explicit call, creates a backup first,
and only backfills user rows when sidecar, turn journal and state.db form an
exact, order-preserving proof.  Ambiguous history is reported, never guessed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

from api.artifacts import ArtifactRegistry, validate_image_bytes
from api.helpers import _redact_text
from api.turn_journal import read_turn_journal


_MEDIA_LINE_RE = re.compile(r"^\s*MEDIA:\s*(?P<path>[^\r\n]+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
_SEMANTIC_MESSAGE_FIELDS = (
    "role", "content", "tool_call_id", "tool_calls", "tool_name", "token_count",
    "finish_reason", "reasoning", "reasoning_content", "reasoning_details",
    "codex_reasoning_items", "codex_message_items", "platform_message_id",
    "message_id", "observed",
)
MIGRATION_EXCLUSIVE_WAIT_SECONDS = 30.0


class MigrationStateBusyError(RuntimeError):
    """Exclusive migration could not start before its bounded wait elapsed."""

    code = "migration_state_busy"

    def __init__(self) -> None:
        super().__init__("migration state is busy; retry later")


class _MigrationStateBarrier:
    """Reader/writer barrier for sidecar + state.db + artifact snapshots.

    Normal session requests take a shared lease and therefore remain concurrent.
    An apply/restore operation takes the exclusive lease for its complete
    backup/mutation/rollback window.  Writer preference prevents a continuous
    SSE/read workload from starving a confirmed migration forever.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._readers = 0
        self._reader_depths: dict[int, int] = {}
        self._waiting_writers = 0
        self._writer_owner: int | None = None
        self._writer_depth = 0

    class _WorkerReadLease:
        """Exactly-once shared lease that may be released by another thread."""

        def __init__(self, barrier: "_MigrationStateBarrier") -> None:
            self._barrier = barrier
            self._released = False
            self._release_lock = threading.Lock()
            self._bound_ident: int | None = None

        def bind_current_thread(self) -> None:
            """Make the transferred slot re-entrant for sinks in its worker.

            The reader count is reserved by the parent *before* ``Thread.start``.
            Binding only records that the child owns that existing slot; it does
            not acquire another reader.  Consequently a writer that queues
            between start and the child's first Session/DB/artifact sink cannot
            deadlock the child behind writer preference while itself waiting for
            the already-reserved slot.
            """
            ident = threading.get_ident()
            with self._release_lock:
                if self._released:
                    raise RuntimeError("migration worker lease is already released")
                if self._bound_ident is not None:
                    raise RuntimeError("migration worker lease is already bound")
                self._bound_ident = ident
            self._barrier._bind_worker_read(ident)

        def release(self) -> None:
            with self._release_lock:
                if self._released:
                    return
                self._released = True
                bound_ident = self._bound_ident
                self._bound_ident = None
            self._barrier._release_worker_read(bound_ident)

    @contextmanager
    def read(self):
        ident = threading.get_ident()
        mode = "writer"
        with self._condition:
            if self._writer_owner != ident:
                if self._reader_depths.get(ident, 0):
                    self._reader_depths[ident] += 1
                    mode = "reader"
                else:
                    while self._writer_owner is not None or self._waiting_writers:
                        self._condition.wait()
                    self._readers += 1
                    self._reader_depths[ident] = 1
                    mode = "reader"
        try:
            yield
        finally:
            if mode == "reader":
                with self._condition:
                    depth = self._reader_depths.get(ident, 0) - 1
                    if depth > 0:
                        self._reader_depths[ident] = depth
                    else:
                        self._reader_depths.pop(ident, None)
                        self._readers -= 1
                        if self._readers == 0:
                            self._condition.notify_all()

    @contextmanager
    def write(self, timeout: float | None = None):
        ident = threading.get_ident()
        wait_seconds = None if timeout is None else max(0.0, float(timeout))
        deadline = (
            None if wait_seconds is None else time.monotonic() + wait_seconds
        )
        with self._condition:
            if self._writer_owner == ident:
                self._writer_depth += 1
            else:
                if self._reader_depths.get(ident, 0):
                    raise RuntimeError(
                        "migration state barrier does not allow read-to-write upgrade"
                    )
                self._waiting_writers += 1
                try:
                    while self._writer_owner is not None or self._readers:
                        if deadline is None:
                            self._condition.wait()
                            continue
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise MigrationStateBusyError()
                        self._condition.wait(timeout=remaining)
                    self._writer_owner = ident
                    self._writer_depth = 1
                finally:
                    self._waiting_writers -= 1
                    if self._writer_owner != ident:
                        # A timed-out writer must not leave fresh readers
                        # stranded behind a writer-preference predicate that no
                        # longer exists.
                        self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                if self._writer_owner != ident:
                    raise RuntimeError("migration state barrier ownership lost")
                self._writer_depth -= 1
                if self._writer_depth == 0:
                    self._writer_owner = None
                    self._condition.notify_all()

    def reserve_worker_read(self) -> "_MigrationStateBarrier._WorkerReadLease":
        """Reserve a reader slot in a handler and transfer it to its worker.

        A request that already owns a normal read lease may reserve while a
        writer is queued: that request began before the writer.  A genuinely
        new request waits behind the queued writer, preserving writer
        preference and preventing new workers from starting during Apply.
        """
        ident = threading.get_ident()
        with self._condition:
            if self._writer_owner == ident:
                raise RuntimeError("migration writer cannot start a state worker")
            if not self._reader_depths.get(ident, 0):
                while self._writer_owner is not None or self._waiting_writers:
                    self._condition.wait()
            self._readers += 1
        return self._WorkerReadLease(self)

    def _bind_worker_read(self, ident: int) -> None:
        with self._condition:
            self._reader_depths[ident] = self._reader_depths.get(ident, 0) + 1

    def _release_worker_read(self, bound_ident: int | None = None) -> None:
        with self._condition:
            if self._readers <= 0:
                raise RuntimeError("migration worker lease ownership lost")
            if bound_ident is not None:
                depth = self._reader_depths.get(bound_ident, 0) - 1
                if depth > 0:
                    self._reader_depths[bound_ident] = depth
                else:
                    self._reader_depths.pop(bound_ident, None)
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()


_MIGRATION_STATE_BARRIER = _MigrationStateBarrier()


@contextmanager
def legacy_migration_state_guard():
    """Shared lease used by every public reader/session mutation route."""
    with _MIGRATION_STATE_BARRIER.read():
        yield


def install_state_db_migration_guard():
    """Register the WebUI barrier in Agent's optional process-local DB hook."""
    from hermes_state import install_state_write_guard

    return install_state_write_guard(legacy_migration_state_guard)


@contextmanager
def _legacy_migration_exclusive_guard(wait_seconds: float | None = None):
    timeout = (
        MIGRATION_EXCLUSIVE_WAIT_SECONDS
        if wait_seconds is None
        else wait_seconds
    )
    with _MIGRATION_STATE_BARRIER.write(timeout=timeout):
        yield


# Importing this WebUI module opts this process into guarded SessionDB writes.
# Standalone Hermes Agent never imports this module and retains its no-op hook.
_PREVIOUS_STATE_DB_WRITE_GUARD = install_state_db_migration_guard()


def reserve_legacy_migration_worker_lease():
    """Reserve a transferable shared lease before starting a state worker."""
    return _MIGRATION_STATE_BARRIER.reserve_worker_read()


def start_legacy_migration_guarded_worker(
    target, *, args=(), kwargs=None, daemon: bool = True, name: str | None = None
):
    """Start ``target`` under a lease released on return, cancel, or error."""
    lease = reserve_legacy_migration_worker_lease()

    def run():
        return run_legacy_migration_guarded_worker(
            lease, target, args=args, kwargs=kwargs
        )

    thread = threading.Thread(target=run, daemon=daemon, name=name)
    try:
        thread.start()
    except BaseException:
        lease.release()
        raise
    return thread


def run_legacy_migration_guarded_worker(lease, target, *, args=(), kwargs=None):
    """Execute one worker and release its transferred lease exactly once."""
    try:
        lease.bind_current_thread()
        return target(*args, **(kwargs or {}))
    finally:
        lease.release()


def _route_touches_migration_state(method: str, path: str) -> bool:
    """Return whether a request can read or mutate a migrated truth layer."""
    path = str(path or "")
    if method == "GET":
        if path in {
            "/api/chat/stream", "/api/sessions/gateway/stream",
            "/api/sessions/events",
        }:
            # These are long-lived, journal/event-only SSE transports.  The
            # migration backs up but never mutates turn/run journals, so holding
            # a shared lease for the connection lifetime adds no consistency and
            # would starve the exclusive apply window.
            return False
        return path.startswith((
            "/api/session",       # detail, export, status/audit
            "/api/sessions",      # finite list and search snapshots
            "/api/media",         # artifact authorization and bytes
        ))
    if method == "POST":
        if path == "/api/session/migration/apply":
            # The apply function takes the exclusive lease itself.  Taking a
            # shared lease here first would be an unsafe read-to-write upgrade.
            return False
        if path in {"/api/crons/run", "/api/cron/run"}:
            # ``/api/crons/run`` is the canonical manual-run route.  Keep the
            # historical singular spelling classified too, so an alias cannot
            # later bypass the state barrier when its Agent child writes the
            # selected profile's state.db.
            return True
        return path.startswith((
            "/api/session", "/api/sessions", "/api/chat", "/api/background",
            "/api/goal", "/api/btw", "/api/expert-teams", "/api/writeflow",
        ))
    return False


def migration_consistent_http_routes(method: str):
    """Decorate route dispatch without serializing unrelated/concurrent I/O."""
    normalized = str(method or "").upper()

    def decorate(func):
        @wraps(func)
        def guarded(handler, parsed, *args, **kwargs):
            if not _route_touches_migration_state(normalized, parsed.path):
                return func(handler, parsed, *args, **kwargs)
            with legacy_migration_state_guard():
                return func(handler, parsed, *args, **kwargs)
        return guarded
    return decorate


def _shared_migration_state(func):
    @wraps(func)
    def guarded(*args, **kwargs):
        with legacy_migration_state_guard():
            return func(*args, **kwargs)
    return guarded


def _exclusive_migration_state(func):
    @wraps(func)
    def guarded(*args, **kwargs):
        with _legacy_migration_exclusive_guard():
            return func(*args, **kwargs)
    return guarded


class MigrationStageError(RuntimeError):
    def __init__(self, stage: str, index: int, cause: Exception):
        super().__init__(f"{stage} failed at item {index}")
        self.stage = stage
        self.index = index
        self.__cause__ = cause


class MigrationRestoreError(RuntimeError):
    """Restore did not commit; ``code`` states whether rollback was complete."""

    def __init__(self, code: str, *, quarantined: bool, cause: Exception):
        super().__init__("migration backup restore did not commit")
        self.code = code
        self.quarantined = bool(quarantined)
        self.__cause__ = cause


def _item(session_id: str, code: str, reason: str, **fields: Any) -> dict:
    # Reports are a public-safe diagnosis: no content, paths, args or values.
    return {"session_id": session_id, "code": code, "reason": reason, **fields}


def _read_json_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_state_db_messages(state_db_path: Path, session_id: str) -> list[dict] | None:
    path = Path(state_db_path)
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(messages)")
            }
            if not {"session_id", "role", "content"}.issubset(columns):
                return None
            selected = [field for field in _SEMANTIC_MESSAGE_FIELDS if field in columns]
            order = "id" if "id" in columns else "rowid"
            rows = connection.execute(
                f"SELECT {', '.join(selected)} FROM messages "
                f"WHERE session_id = ? ORDER BY {order}",
                (session_id,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return None
    result: list[dict] = []
    for row in rows:
        message = dict(row)
        for field in ("tool_calls", "reasoning_details", "codex_reasoning_items", "codex_message_items"):
            if isinstance(message.get(field), str):
                try:
                    message[field] = json.loads(message[field])
                except (TypeError, json.JSONDecodeError):
                    pass
        result.append(message)
    return result


def _message_turn_id(message: dict) -> str:
    explicit = str(message.get("turn_id") or "").strip()
    if explicit:
        return explicit
    platform_id = str(
        message.get("platform_message_id") or message.get("message_id") or ""
    ).strip()
    return platform_id.removeprefix("webui-turn:") if platform_id.startswith("webui-turn:") else ""


def _media_directives(content: Any) -> list[dict]:
    """Return exact non-code-fence MEDIA line spans."""
    text = str(content or "")
    directives: list[dict] = []
    offset = 0
    fence_char = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        fence_match = _FENCE_RE.match(body)
        if fence_match:
            marker = fence_match.group("fence")
            if not fence_char:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char, fence_length = "", 0
            offset += len(line)
            continue
        if not fence_char:
            match = _MEDIA_LINE_RE.fullmatch(body)
            if match:
                directives.append({
                    "start": offset,
                    "end": offset + len(line),
                    "raw": match.group("path").strip().strip("`\"'"),
                })
        offset += len(line)
    return directives


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    result = str(text)
    for start, end in sorted(spans, reverse=True):
        result = result[:start] + result[end:]
    return result.strip()


def _strip_promotable_media(content: Any, registry: ArtifactRegistry) -> str:
    text = str(content or "")
    spans = [
        (item["start"], item["end"])
        for item in _media_directives(text)
        if registry.generated_source_is_allowed(Path(item["raw"]))
    ]
    return _remove_spans(text, spans)


def _semantic_message(message: dict, registry: ArtifactRegistry) -> dict:
    return {
        "role": str(message.get("role") or ""),
        "content": _strip_promotable_media(message.get("content"), registry),
        "platform_message_id": str(
            message.get("platform_message_id") or message.get("message_id") or ""
        ),
    }


def _backfill_proof(
    session_id: str,
    messages: list[dict],
    db_messages: list[dict] | None,
    journal_events: list[dict],
    registry: ArtifactRegistry,
) -> tuple[str, list[dict] | None]:
    users = [message for message in messages if str(message.get("role")) == "user"]
    if not users:
        return "not_needed", None
    if db_messages is None:
        return "state_db_unreadable", None

    submitted: dict[str, dict] = {}
    submitted_order: list[str] = []
    for event in journal_events:
        if str(event.get("event") or "") != "submitted":
            continue
        turn_id = str(event.get("turn_id") or "").strip()
        if turn_id:
            submitted[turn_id] = event
            submitted_order.append(turn_id)

    user_turns: list[str] = []
    for message in users:
        turn_id = _message_turn_id(message)
        if not turn_id:
            return "missing_turn_id", None
        user_turns.append(turn_id)
        event = submitted.get(turn_id)
        if event is None:
            return "journal_missing", None
        if event.get("content") != message.get("content"):
            return "content_mismatch", None
    filtered_submitted = [turn_id for turn_id in submitted_order if turn_id in set(user_turns)]
    if filtered_submitted != user_turns:
        return "order_mismatch", None

    sidecar_semantic = [_semantic_message(message, registry) for message in messages]
    db_semantic = [_semantic_message(message, registry) for message in db_messages]
    expected_without_missing_users = []
    existing_user_keys = {
        (item["platform_message_id"], item["content"])
        for item in db_semantic if item["role"] == "user"
    }
    missing = 0
    for item in sidecar_semantic:
        if item["role"] != "user":
            expected_without_missing_users.append(item)
            continue
        key = (item["platform_message_id"], item["content"])
        if key in existing_user_keys:
            expected_without_missing_users.append(item)
        else:
            missing += 1
    if not missing:
        return "not_needed", None
    if len(expected_without_missing_users) != len(db_semantic):
        return "order_mismatch", None
    for expected, actual in zip(expected_without_missing_users, db_semantic):
        if expected["role"] != actual["role"]:
            return "order_mismatch", None
        if expected["content"] != actual["content"]:
            return "content_mismatch", None
    return "turn_id_order_content_exact", copy.deepcopy(messages)


def _iter_media_candidates(
    messages: list[dict], registry: ArtifactRegistry
) -> list[dict]:
    candidates: list[dict] = []
    for index, message in enumerate(messages):
        if str(message.get("role") or "") not in {"assistant", "tool"}:
            continue
        if message.get("artifacts"):
            continue
        content = str(message.get("content") or "")
        for media_index, directive in enumerate(_media_directives(content)):
            raw = directive["raw"]
            source = Path(raw).expanduser()
            if not registry.generated_source_is_allowed(source):
                continue
            try:
                data = source.read_bytes()
                validate_image_bytes(
                    data, max_bytes=registry.max_bytes, max_pixels=registry.max_pixels
                )
            except Exception:
                continue
            candidates.append({
                "message_index": index,
                "media_index": media_index,
                "source": source,
                "sha256": hashlib.sha256(data).hexdigest(),
                "start": directive["start"],
                "end": directive["end"],
            })
    return candidates


def _sensitive_leaf_locations(value: Any, prefix: str):
    if isinstance(value, str):
        if _redact_text(value, _enabled=True) != value:
            yield prefix
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _sensitive_leaf_locations(item, f"{prefix}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _sensitive_leaf_locations(item, child)


def _scan_message_leaks(
    session_id: str, messages: list[dict], top_level_tools: Any
) -> list[dict]:
    findings: list[dict] = []
    for index, message in enumerate(messages):
        if str(message.get("role") or "") not in {"assistant", "tool"}:
            continue
        for field in (
            "content", "reasoning", "reasoning_content", "reasoning_details",
            "tool_calls",
        ):
            if field not in message:
                continue
            for location in _sensitive_leaf_locations(
                message.get(field), f"messages[{index}].{field}"
            ):
                findings.append(_item(
                    session_id,
                    "credential_leak_detected",
                    "manual_review_required",
                    location=location,
                ))
    for location in _sensitive_leaf_locations(top_level_tools, "tool_calls"):
        findings.append(_item(
            session_id, "credential_leak_detected", "manual_review_required",
            location=location,
        ))
    return findings


def _privacy_context_problem(privacy: Any, messages: list[dict]) -> str | None:
    if not isinstance(privacy, dict):
        return "invalid_privacy_context"
    try:
        remaining = int(privacy.get("remaining_turns") or 0)
    except (TypeError, ValueError, OverflowError):
        return "invalid_privacy_context"
    source_turn_id = str(privacy.get("source_turn_id") or "").strip()
    if not privacy.get("risk_type") or not source_turn_id or remaining != 1:
        return "invalid_privacy_context"
    user_turns = [
        _message_turn_id(message)
        for message in messages
        if str(message.get("role") or "") == "user" and _message_turn_id(message)
    ]
    if not user_turns:
        return "orphan_privacy_context"
    if (
        source_turn_id not in user_turns
        or user_turns.index(source_turn_id) != len(user_turns) - 1
    ):
        return "stale_privacy_context"
    return None


def _payload_state_db_matches(payload: dict, state_db_path: Path) -> bool:
    """Fail closed when an explicitly profiled sidecar belongs to another DB."""
    if "profile" not in payload or not str(payload.get("profile") or "").strip():
        return True
    profile = str(payload.get("profile") or "").strip()
    try:
        from api.profiles import get_hermes_home_for_profile

        expected = (Path(get_hermes_home_for_profile(profile)) / "state.db").resolve(
            strict=False
        )
        supplied = Path(state_db_path).resolve(strict=False)
    except Exception:
        return False
    return expected == supplied


def _audit_one(
    session_path: Path,
    state_db_path: Path,
    registry: ArtifactRegistry,
) -> tuple[list[dict], dict | None]:
    payload = _read_json_object(session_path)
    session_id = session_path.stem
    if payload is None or str(payload.get("session_id") or session_id) != session_id:
        return [_item(session_id, "session_unreadable", "invalid_session_json")], None
    messages = payload.get("messages")
    if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
        return [_item(session_id, "session_unreadable", "invalid_messages")], payload
    items: list[dict] = []
    if not _payload_state_db_matches(payload, state_db_path):
        items.append(_item(
            session_id,
            "profile_state_db_mismatch",
            "session_profile_database_not_active",
        ))
    if "brand_privacy_tainted" in payload:
        items.append(_item(session_id, "legacy_privacy_taint", "legacy_unbounded_taint"))
    privacy = payload.get("privacy_context")
    privacy_problem = (
        _privacy_context_problem(privacy, messages) if privacy is not None else None
    )
    if privacy_problem:
        items.append(_item(session_id, "legacy_privacy_taint", privacy_problem))

    journal = read_turn_journal(session_id, session_dir=session_path.parent)
    replacement = None
    if _payload_state_db_matches(payload, state_db_path):
        db_messages = _read_state_db_messages(state_db_path, session_id)
        proof, replacement = _backfill_proof(
            session_id, messages, db_messages, journal.get("events") or [], registry
        )
        if proof == "turn_id_order_content_exact":
            items.append(_item(
                session_id, "state_db_user_backfill_exact", proof,
                missing_users=sum(1 for item in messages if item.get("role") == "user")
                - sum(1 for item in db_messages or [] if item.get("role") == "user"),
            ))
        elif proof not in {"not_needed"}:
            items.append(_item(session_id, "state_db_user_backfill_skipped", proof))

    for candidate in _iter_media_candidates(messages, registry):
        items.append(_item(
            session_id,
            "legacy_cached_image",
            "existing_cache_image_exact",
            message_index=candidate["message_index"],
        ))
    items.extend(_scan_message_leaks(
        session_id, messages, payload.get("tool_calls") or []
    ))
    # Submitted/user originals are deliberately outside the scan target.
    for index, event in enumerate(journal.get("events") or []):
        if (
            str(event.get("role") or "").lower() == "user"
            or str(event.get("event") or "").lower() == "submitted"
        ):
            continue
        for location in _sensitive_leaf_locations(event, f"turn_journal[{index}]"):
            items.append(_item(
                session_id, "credential_leak_detected", "manual_review_required",
                location=location,
            ))
    run_root = session_path.parent / "_run_journal" / session_id
    for run_path in sorted(run_root.glob("*.jsonl")) if run_root.is_dir() else []:
        try:
            lines = run_path.read_text("utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or (
                str(event.get("role") or "").lower() == "user"
                or str(event.get("event") or "").lower() == "submitted"
            ):
                continue
            prefix = f"run_journal[{run_path.stem}][{index}]"
            for location in _sensitive_leaf_locations(event, prefix):
                items.append(_item(
                    session_id, "credential_leak_detected",
                    "manual_review_required", location=location,
                ))
    if replacement is not None:
        payload["_migration_db_replacement"] = replacement
    return items, payload


@_shared_migration_state
def audit_legacy_sessions(
    session_dir: Path,
    state_db_path: Path,
    artifact_registry: ArtifactRegistry,
) -> dict:
    """Return a public-safe, read-only repair report."""
    root = Path(session_dir)
    session_paths = [
        path for path in sorted(root.glob("*.json"))
        if not path.name.startswith("_") and path.is_file()
    ] if root.exists() else []
    items: list[dict] = []
    for path in session_paths:
        try:
            session_items, _payload = _audit_one(
                path, Path(state_db_path), artifact_registry
            )
        except Exception:
            session_items = [_item(
                path.stem, "session_unreadable", "audit_failed"
            )]
        items.extend(session_items)
    skipped = sum(
        item["code"] in {
            "state_db_user_backfill_skipped", "session_unreadable",
            "profile_state_db_mismatch",
        }
        or item["reason"] == "manual_review_required"
        for item in items
    )
    quarantine_count = 0
    for quarantine_root in (
        root / "_quarantine", artifact_registry.root / ".quarantine"
    ):
        if not quarantine_root.is_dir():
            continue
        try:
            quarantine_count += sum(
                1 for item in quarantine_root.iterdir()
                if item.is_dir() and not item.is_symlink()
            )
        except OSError:
            quarantine_count += 1
    return {
        "scanned": len(session_paths),
        "modified": 0,
        "skipped": skipped,
        "failed": 0,
        "backup_path": None,
        "quarantine_count": quarantine_count,
        "quarantine_status": "manual_review_required" if quarantine_count else "clean",
        "needs_repair": bool(items) or quarantine_count > 0,
        "items": items,
    }


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_regular_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise OSError("backup source contains a symbolic link")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _backup_file_rows(destination: Path) -> list[dict]:
    rows = []
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or path.name == "backup-manifest.json":
            continue
        data = path.read_bytes()
        rows.append({
            "path": path.relative_to(destination).as_posix(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return rows


def _backup_before_apply(
    session_dir: Path,
    state_db_path: Path,
    backup_root: Path,
    artifact_registry: ArtifactRegistry,
) -> Path:
    destination = Path(backup_root) / (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    )
    destination.mkdir(parents=True, exist_ok=False)
    referenced_cache: list[dict] = []
    try:
        _copy_regular_tree(Path(session_dir), destination / "sessions")
        _copy_regular_tree(artifact_registry.root, destination / "artifacts")
        if Path(state_db_path).is_file():
            with sqlite3.connect(f"file:{state_db_path}?mode=ro", uri=True) as source:
                with sqlite3.connect(destination / "state.db") as target:
                    source.backup(target)
        seen_cache: set[Path] = set()
        for session_path in sorted(Path(session_dir).glob("*.json")):
            payload = _read_json_object(session_path) or {}
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                continue
            for candidate in _iter_media_candidates(
                [item for item in messages if isinstance(item, dict)],
                artifact_registry,
            ):
                source = candidate["source"].resolve()
                if source in seen_cache:
                    continue
                seen_cache.add(source)
                suffix = source.suffix.lower()
                relative = (
                    f"referenced-cache/{candidate['sha256']}-{len(seen_cache):04d}{suffix}"
                )
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                referenced_cache.append({
                    "source_path": str(source),
                    "path": relative,
                    "sha256": candidate["sha256"],
                })
        manifest = {
            "schema_version": 1,
            "created_at": time.time(),
            "sqlite_backup": {
                "method": "sqlite_backup_api",
                "captures_committed_wal_state": True,
            },
            "referenced_cache": referenced_cache,
            "files": _backup_file_rows(destination),
        }
        _atomic_write(
            destination / "backup-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return destination
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _validated_backup_manifest(backup: Path) -> dict:
    manifest = _read_json_object(backup / "backup-manifest.json")
    if not manifest or manifest.get("schema_version") != 1:
        raise ValueError("migration backup manifest is invalid")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("migration backup file manifest is invalid")
    for row in files:
        if not isinstance(row, dict):
            raise ValueError("migration backup file entry is invalid")
        relative = Path(str(row.get("path") or ""))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("migration backup path is invalid")
        source = backup / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("migration backup file is unavailable")
        data = source.read_bytes()
        expected_size = row.get("size")
        if not isinstance(expected_size, int) or len(data) != expected_size:
            raise ValueError("migration backup size mismatch")
        if hashlib.sha256(data).hexdigest() != str(row.get("sha256") or ""):
            raise ValueError("migration backup hash mismatch")
    return manifest


def _replace_tree_from_backup(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.restore-{uuid.uuid4().hex}")
    previous = destination.with_name(f".{destination.name}.previous-{uuid.uuid4().hex}")
    shutil.copytree(source, temporary)
    try:
        if destination.exists():
            os.replace(destination, previous)
        os.replace(temporary, destination)
        shutil.rmtree(previous, ignore_errors=True)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if previous.exists() and not destination.exists():
            os.replace(previous, destination)
        raise


_SESSION_JOURNAL_DIRS = frozenset({"_turn_journal", "_run_journal"})


def _restore_session_tree_preserving_journals(
    source: Path, destination: Path
) -> None:
    """Restore sidecars/indexes without replacing live append-only journals.

    Migration never mutates turn/run journals. Restoring the entire sessions
    directory would nevertheless roll back metering/SSE events appended after
    the backup (or race an open append). Restore every non-journal entry under
    the exclusive state barrier while leaving the two journal directories at
    their stable paths.
    """
    source = Path(source)
    destination = Path(destination)
    if not source.is_dir():
        raise ValueError("migration backup session tree is unavailable")
    destination.mkdir(parents=True, exist_ok=True)
    source_entries = {
        entry.name: entry
        for entry in source.iterdir()
        if entry.name not in _SESSION_JOURNAL_DIRS
    }
    for current in list(destination.iterdir()):
        if current.name in _SESSION_JOURNAL_DIRS or current.name in source_entries:
            continue
        if current.is_dir() and not current.is_symlink():
            shutil.rmtree(current)
        else:
            current.unlink(missing_ok=True)
    for name, backup_entry in source_entries.items():
        if backup_entry.is_symlink():
            raise OSError("backup session tree contains a symbolic link")
        target = destination / name
        if backup_entry.is_file():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            _atomic_write(target, backup_entry.read_bytes())
        elif backup_entry.is_dir():
            _replace_tree_from_backup(backup_entry, target)


def _remove_session_state_preserving_journals(destination: Path) -> None:
    destination = Path(destination)
    if not destination.is_dir():
        return
    for current in list(destination.iterdir()):
        if current.name in _SESSION_JOURNAL_DIRS:
            continue
        if current.is_dir() and not current.is_symlink():
            shutil.rmtree(current)
        else:
            current.unlink(missing_ok=True)
    try:
        destination.rmdir()
    except OSError:
        pass


def _restore_sqlite_backup(source_path: Path, destination_path: Path) -> None:
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(destination_path) as target:
            source.backup(target)


def _session_ids_in_tree(root: Path) -> set[str]:
    if not Path(root).is_dir():
        return set()
    return {
        path.stem for path in Path(root).glob("*.json")
        if path.is_file() and not path.name.startswith("_")
    }


@_exclusive_migration_state
def restore_legacy_migration_backup(
    backup_path: Path,
    session_dir: Path,
    state_db_path: Path,
    artifact_registry: ArtifactRegistry,
) -> dict:
    """Verify a migration backup completely, then restore its logical snapshot."""
    backup = Path(backup_path).resolve()
    manifest = _validated_backup_manifest(backup)
    target_session_dir = Path(session_dir)
    target_state_db = Path(state_db_path)
    affected_session_ids = _session_ids_in_tree(target_session_dir) | _session_ids_in_tree(
        backup / "sessions"
    )
    cache_targets: list[tuple[Path, bytes, bool, bytes]] = []
    for row in manifest.get("referenced_cache") or []:
        source_path = Path(str(row.get("source_path") or "")).expanduser()
        resolved = source_path.resolve(strict=False)
        if not any(
            resolved == root or root in resolved.parents
            for root in artifact_registry.allowed_source_roots
        ):
            raise ValueError("migration backup cache target is outside allowed roots")
        existed = resolved.is_file()
        current = resolved.read_bytes() if existed else b""
        restored = (backup / str(row.get("path"))).read_bytes()
        cache_targets.append((resolved, current, existed, restored))

    quarantine_root = backup.parent / ".restore-quarantine"
    current_snapshot = _backup_before_apply(
        target_session_dir,
        target_state_db,
        quarantine_root,
        artifact_registry,
    )
    sessions_existed = target_session_dir.is_dir()
    artifacts_existed = artifact_registry.root.is_dir()
    db_existed = target_state_db.is_file()
    restored_cache = 0
    try:
        _restore_session_tree_preserving_journals(
            backup / "sessions", target_session_dir
        )
        if (backup / "artifacts").is_dir():
            _replace_tree_from_backup(backup / "artifacts", artifact_registry.root)
        if (backup / "state.db").is_file():
            _restore_sqlite_backup(backup / "state.db", target_state_db)
        for target, _current, _existed, restored in cache_targets:
            _atomic_write(target, restored)
            restored_cache += 1
        _invalidate_cached_sessions(target_session_dir, affected_session_ids)
    except Exception as cause:
        rollback_complete = True
        try:
            if sessions_existed:
                _restore_session_tree_preserving_journals(
                    current_snapshot / "sessions", target_session_dir
                )
            elif target_session_dir.exists():
                _remove_session_state_preserving_journals(target_session_dir)
        except Exception:
            rollback_complete = False
        try:
            if artifacts_existed and (current_snapshot / "artifacts").is_dir():
                _replace_tree_from_backup(
                    current_snapshot / "artifacts", artifact_registry.root
                )
            elif artifact_registry.root.exists():
                shutil.rmtree(artifact_registry.root)
        except Exception:
            rollback_complete = False
        try:
            if db_existed and (current_snapshot / "state.db").is_file():
                _restore_sqlite_backup(current_snapshot / "state.db", target_state_db)
            elif target_state_db.exists():
                target_state_db.unlink()
        except Exception:
            rollback_complete = False
        for target, current, existed, _restored in cache_targets:
            try:
                if existed:
                    _atomic_write(target, current)
                else:
                    target.unlink(missing_ok=True)
            except Exception:
                rollback_complete = False
        try:
            _invalidate_cached_sessions(target_session_dir, affected_session_ids)
        except Exception:
            rollback_complete = False
        if rollback_complete:
            shutil.rmtree(current_snapshot, ignore_errors=True)
            raise MigrationRestoreError(
                "restore_rolled_back", quarantined=False, cause=cause
            ) from cause
        try:
            _atomic_write(
                current_snapshot / "restore-failure.json",
                json.dumps({
                    "schema_version": 1,
                    "code": "rollback_incomplete",
                    "quarantined": True,
                    "created_at": time.time(),
                }, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        except Exception:
            pass
        raise MigrationRestoreError(
            "rollback_incomplete", quarantined=True, cause=cause
        ) from cause
    shutil.rmtree(current_snapshot, ignore_errors=True)
    return {
        "verified": True,
        "restored_files": len(manifest.get("files") or []),
        "restored_cache_files": restored_cache,
    }


def _semantic_db_messages(messages: list[dict]) -> list[dict]:
    return [
        {field: copy.deepcopy(message[field]) for field in _SEMANTIC_MESSAGE_FIELDS if field in message}
        for message in messages
        if isinstance(message, dict) and message.get("role")
    ]


def _existing_db_messages_after_safe_media_repair(
    messages: list[dict], registry: ArtifactRegistry
) -> list[dict]:
    """Project only already-present assistant/tool rows after promotion.

    An ambiguous user proof must never copy the sidecar wholesale because that
    would guess the missing user row.  It is nevertheless safe and necessary to
    remove a successfully promoted MEDIA directive from semantic rows already
    present in state.db, so the database and visible sidecar do not diverge.
    """
    repaired = copy.deepcopy(messages)
    for message in repaired:
        if str(message.get("role") or "") not in {"assistant", "tool"}:
            continue
        if isinstance(message.get("content"), str):
            message["content"] = _strip_promotable_media(
                message.get("content"), registry
            )
    return repaired


def _write_state_db_messages(state_db_path: Path, session_id: str, messages: list[dict]) -> None:
    from hermes_state import SessionDB

    db = SessionDB(Path(state_db_path))
    try:
        db.replace_messages(
            session_id,
            _semantic_db_messages(messages),
            ensure_source="webui",
        )
    finally:
        db.close()


def _replace_state_db_messages(state_db_path: Path, session_id: str, messages: list[dict]) -> None:
    """Patch point kept narrow so fault-injection can verify rollback."""
    _write_state_db_messages(state_db_path, session_id, messages)


def _invalidate_cached_sessions(session_dir: Path, session_ids: set[str]) -> None:
    """Evict and revoke stale Session objects while the writer lease is held."""
    if not session_ids:
        return
    from api import models

    if Path(models.SESSION_DIR).resolve(strict=False) != Path(session_dir).resolve(
        strict=False
    ):
        return
    with models.LOCK:
        for session_id in session_ids:
            cached = models.SESSIONS.pop(session_id, None)
            if cached is not None:
                cached._invalidated_by_legacy_migration = True


def _promote_media(
    session_id: str,
    messages: list[dict],
    registry: ArtifactRegistry,
    created: set[str],
) -> int:
    candidates = _iter_media_candidates(messages, registry)
    modified = 0
    successful_spans: dict[int, list[tuple[int, int]]] = {}
    for candidate_index, candidate in enumerate(candidates, start=1):
        index = candidate["message_index"]
        message = messages[index]
        turn_id = _message_turn_id(message) or f"legacy-m{index}"
        source_turn_id = f"legacy-{hashlib.sha256(turn_id.encode()).hexdigest()[:20]}"
        tool_id = f"media-{candidate['media_index']}-{candidate['sha256'][:16]}"
        try:
            descriptor = registry.register_image_file(
                session_id,
                source_turn_id,
                tool_id,
                candidate["source"],
                name=candidate["source"].name,
                expected_sha256=candidate["sha256"],
            )
        except Exception as exc:
            raise MigrationStageError(
                "artifact_promotion", candidate_index, exc
            ) from exc
        created.add(str(descriptor["artifact_id"]))
        message.setdefault("artifacts", []).append(descriptor)
        successful_spans.setdefault(index, []).append(
            (candidate["start"], candidate["end"])
        )
        modified += 1
    for index, spans in successful_spans.items():
        messages[index]["content"] = _remove_spans(
            str(messages[index].get("content") or ""), spans
        )
    return modified


@_exclusive_migration_state
def migrate_legacy_sessions(
    session_dir: Path,
    state_db_path: Path,
    artifact_registry: ArtifactRegistry,
    *,
    dry_run: bool = True,
    backup_root: Path | None = None,
) -> dict:
    """Audit by default; apply only exact repairs after creating a backup."""
    if dry_run:
        return audit_legacy_sessions(session_dir, state_db_path, artifact_registry)
    root = Path(session_dir)
    report = audit_legacy_sessions(root, Path(state_db_path), artifact_registry)
    mismatched_sessions = {
        item["session_id"] for item in report["items"]
        if item["code"] == "profile_state_db_mismatch"
    }
    eligible_sessions = {
        item["session_id"] for item in report["items"]
        if item["code"] in {
            "legacy_privacy_taint", "state_db_user_backfill_exact", "legacy_cached_image"
        }
    } - mismatched_sessions
    if not eligible_sessions:
        return report
    backup_root = Path(backup_root) if backup_root is not None else root.parent / "migration-backups"
    backup = _backup_before_apply(
        root, Path(state_db_path), backup_root, artifact_registry
    )
    report["backup_path"] = str(backup)
    modified = 0
    failed = 0
    for session_id in sorted(eligible_sessions):
        session_path = root / f"{session_id}.json"
        original_bytes = session_path.read_bytes()
        original_db = _read_state_db_messages(Path(state_db_path), session_id)
        items, payload = _audit_one(session_path, Path(state_db_path), artifact_registry)
        if payload is None:
            continue
        replacement = payload.pop("_migration_db_replacement", None)
        created_artifacts: set[str] = set()
        batch_modified = 0
        db_touched = False
        stage = "sidecar_update"
        try:
            if "brand_privacy_tainted" in payload:
                payload.pop("brand_privacy_tainted", None)
                batch_modified += 1
            if any(
                item["code"] == "legacy_privacy_taint"
                for item in items
            ):
                payload.pop("privacy_context", None)
                batch_modified += 1
            stage = "artifact_promotion"
            media_modified = _promote_media(
                session_id,
                payload.get("messages") or [],
                artifact_registry,
                created_artifacts,
            )
            batch_modified += media_modified
            if batch_modified:
                stage = "sidecar_write"
                _atomic_write(
                    session_path,
                    json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                )
            if batch_modified or replacement is not None:
                # Use the post-promotion message view so state.db and sidecar agree.
                stage = "state_db_replace"
                ambiguous_user_backfill = any(
                    item["code"] == "state_db_user_backfill_skipped"
                    for item in items
                )
                db_messages = (
                    _existing_db_messages_after_safe_media_repair(
                        original_db, artifact_registry
                    )
                    if ambiguous_user_backfill and replacement is None
                    and original_db is not None
                    else payload.get("messages") or []
                )
                db_touched = True
                _replace_state_db_messages(
                    Path(state_db_path), session_id, db_messages
                )
                batch_modified += 1
            _invalidate_cached_sessions(root, {session_id})
            modified += batch_modified
        except Exception as exc:
            failed += 1
            rollback_complete = True
            try:
                _atomic_write(session_path, original_bytes)
            except Exception:
                rollback_complete = False
            try:
                removed = artifact_registry.rollback_registered_artifacts(
                    session_id, created_artifacts
                )
                if removed != len(created_artifacts):
                    rollback_complete = False
            except Exception:
                rollback_complete = False
            if original_db is not None and db_touched:
                try:
                    _write_state_db_messages(
                        Path(state_db_path), session_id, original_db
                    )
                    if _read_state_db_messages(
                        Path(state_db_path), session_id
                    ) != original_db:
                        rollback_complete = False
                except Exception:
                    rollback_complete = False
            try:
                _invalidate_cached_sessions(root, {session_id})
            except Exception:
                rollback_complete = False
            failed_stage = (
                exc.stage if isinstance(exc, MigrationStageError) else stage
            )
            report["items"].append(_item(
                session_id,
                "migration_failed",
                "batch_rolled_back" if rollback_complete else "rollback_incomplete",
                stage=failed_stage,
                failed_index=(
                    exc.index if isinstance(exc, MigrationStageError) else None
                ),
                rollback_complete=rollback_complete,
            ))
            break
    report["modified"] = modified
    report["failed"] = failed
    report["needs_repair"] = failed > 0 or any(
        item["reason"] == "manual_review_required"
        or item["code"] in {
            "state_db_user_backfill_skipped", "session_unreadable",
            "profile_state_db_mismatch",
        }
        for item in report["items"]
    )
    return report
