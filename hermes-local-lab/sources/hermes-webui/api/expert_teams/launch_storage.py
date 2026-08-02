"""Durable global receipts for atomic expert-team portal launches."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX only
    msvcrt = None


LAUNCH_TRANSACTION_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,240}")
_MAX_JSON_BYTES = 2 * 1024 * 1024
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
LAUNCH_FILE_LOCK_TIMEOUT_SECONDS = 5.0
_ALLOWED_STATES = frozenset(
    {"reserved", "recovery_required", "cleanup_required", "rolled_back", "committed"}
)
_TRANSITIONS = {
    "reserved": {"reserved", "recovery_required", "cleanup_required", "committed"},
    "recovery_required": {"recovery_required", "cleanup_required", "committed"},
    "cleanup_required": {"cleanup_required", "recovery_required", "committed", "rolled_back"},
    "rolled_back": {"rolled_back"},
    "committed": {"committed"},
}
_IMMUTABLE_FIELDS = (
    "schema_version",
    "transaction_id",
    "idempotency_key_hash",
    "request_fingerprint",
    "launch_profile_id",
    "launch_profile_snapshot",
    "launch_profile_sha256",
    "prompt_sha256",
    "session_options",
    "session_id",
    "workspace",
    "initial_session_snapshot",
    "initial_session_sha256",
    "created_at",
)


class LaunchTransactionIntegrityError(ValueError):
    pass


class LaunchTransactionLockTimeout(TimeoutError):
    """A recoverable contention failure for one idempotent portal launch."""

    retryable = True


def _state_root() -> Path:
    from api import config

    return Path(os.path.abspath(Path(config.STATE_DIR).expanduser()))


def _root() -> Path:
    return _state_root() / "expert-team-launches" / "v1"


def launch_transaction_id(idempotency_key: str) -> str:
    payload = f"expert-team-launch-v1\0{idempotency_key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def launch_idempotency_key_hash(idempotency_key: str) -> str:
    return hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()


def launch_session_snapshot_digest(snapshot: dict) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def launch_profile_snapshot_digest(snapshot: dict) -> str:
    return launch_session_snapshot_digest(snapshot)


def _validate_id(value: object, *, label: str) -> str:
    normalized = str(value or "")
    if _SHA256.fullmatch(normalized) is None:
        raise LaunchTransactionIntegrityError(f"invalid {label}")
    return normalized


def _validate_session_id(value: object) -> str:
    normalized = str(value or "")
    if _SAFE_SESSION_ID.fullmatch(normalized) is None:
        raise LaunchTransactionIntegrityError("invalid launch Session id")
    return normalized


def _validate_run_id(value: object) -> str:
    normalized = str(value or "")
    if _SAFE_SESSION_ID.fullmatch(normalized) is None:
        raise LaunchTransactionIntegrityError("invalid launch Run id")
    return normalized


def _validate_receipt(receipt: object) -> dict:
    if type(receipt) is not dict:
        raise LaunchTransactionIntegrityError("launch receipt is not an object")
    value = copy.deepcopy(receipt)
    if value.get("schema_version") != LAUNCH_TRANSACTION_SCHEMA_VERSION:
        raise LaunchTransactionIntegrityError("unsupported launch receipt schema")
    transaction_id = _validate_id(value.get("transaction_id"), label="launch transaction id")
    _validate_id(value.get("idempotency_key_hash"), label="launch idempotency hash")
    _validate_id(value.get("request_fingerprint"), label="launch request fingerprint")
    _validate_id(value.get("prompt_sha256"), label="launch prompt hash")
    _validate_session_id(value.get("session_id"))
    if value.get("state") not in _ALLOWED_STATES:
        raise LaunchTransactionIntegrityError("invalid launch receipt state")
    if not isinstance(value.get("workspace"), str) or not value["workspace"]:
        raise LaunchTransactionIntegrityError("launch receipt workspace is invalid")
    if not isinstance(value.get("launch_profile_id"), str) or not value["launch_profile_id"]:
        raise LaunchTransactionIntegrityError("launch receipt profile is invalid")
    launch_profile_snapshot = value.get("launch_profile_snapshot")
    launch_profile_sha256 = str(value.get("launch_profile_sha256") or "")
    if (
        type(launch_profile_snapshot) is not dict
        or _SHA256.fullmatch(launch_profile_sha256) is None
        or launch_profile_snapshot_digest(launch_profile_snapshot)
        != launch_profile_sha256
        or str(launch_profile_snapshot.get("id") or "")
        != str(value.get("launch_profile_id") or "")
    ):
        raise LaunchTransactionIntegrityError(
            "launch receipt Profile snapshot is invalid"
        )
    if type(value.get("session_options")) is not dict:
        raise LaunchTransactionIntegrityError("launch receipt Session options are invalid")
    initial_session_snapshot = value.get("initial_session_snapshot")
    initial_session_sha256 = str(value.get("initial_session_sha256") or "")
    if (
        type(initial_session_snapshot) is not dict
        or _SHA256.fullmatch(initial_session_sha256) is None
        or launch_session_snapshot_digest(initial_session_snapshot)
        != initial_session_sha256
        or str(initial_session_snapshot.get("session_id") or "")
        != str(value.get("session_id") or "")
        or str(
            initial_session_snapshot.get("expert_team_launch_transaction_id")
            or ""
        )
        != transaction_id
    ):
        raise LaunchTransactionIntegrityError(
            "launch receipt initial Session snapshot is invalid"
        )
    if str(value.get("run_id") or "") and not re.fullmatch(
        r"[A-Za-z0-9_-]{1,240}", str(value["run_id"])
    ):
        raise LaunchTransactionIntegrityError("launch receipt Run id is invalid")
    if str(value.get("start_transaction_id") or ""):
        _validate_id(value["start_transaction_id"], label="start transaction id")
    if value.get("state") == "committed" and not (
        value.get("run_id") and value.get("start_transaction_id")
    ):
        raise LaunchTransactionIntegrityError("committed launch receipt is incomplete")
    if str(value.get("transaction_id")) != transaction_id:
        raise LaunchTransactionIntegrityError("launch transaction identity changed")
    return value


def _storage_path_parts(path: Path) -> tuple[Path, Path, tuple[str, ...]]:
    candidate = Path(os.path.abspath(Path(path).expanduser()))
    state_root = _state_root()
    try:
        relative = candidate.relative_to(state_root)
    except ValueError as exc:
        raise LaunchTransactionIntegrityError(
            "launch storage escaped the state directory"
        ) from exc
    if not relative.parts:
        raise LaunchTransactionIntegrityError("launch storage path is incomplete")
    return candidate, state_root, relative.parts


def _safe_windows_parent(path: Path, *, create: bool) -> Path:
    """Best-effort no-symlink fallback where Python has no openat support."""
    candidate, state_root, relative = _storage_path_parts(path)
    if create:
        state_root.mkdir(parents=True, exist_ok=True)
    try:
        state_metadata = state_root.lstat()
    except OSError as exc:  # pragma: no cover - Windows only
        raise LaunchTransactionIntegrityError(
            "launch storage state directory is unavailable"
        ) from exc
    if stat.S_ISLNK(state_metadata.st_mode) or not stat.S_ISDIR(state_metadata.st_mode):
        raise LaunchTransactionIntegrityError("launch storage state directory is unsafe")
    current = state_root
    for part in relative[:-1]:
        if not part or part in {".", ".."}:
            raise LaunchTransactionIntegrityError(
                "launch storage directory component is invalid"
            )
        current = current / part
        if create:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - Windows only
            raise LaunchTransactionIntegrityError(
                "launch storage parent is unavailable"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise LaunchTransactionIntegrityError("launch storage parent is unsafe")
    return candidate.parent


@contextmanager
def _anchored_storage_parent(path: Path, *, create: bool):
    """Yield one opened parent so path swaps cannot redirect I/O on POSIX."""
    candidate, state_root, relative = _storage_path_parts(path)
    leaf = relative[-1]
    if not leaf or leaf in {".", ".."}:
        raise LaunchTransactionIntegrityError("launch storage leaf is invalid")

    if os.name == "nt":  # pragma: no cover - Windows packaged fallback
        parent = _safe_windows_parent(candidate, create=create)
        yield None, leaf, parent
        return

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    anchor = Path(state_root.anchor or os.sep)
    descriptor = -1
    try:
        descriptor = os.open(anchor, directory_flags)
        components = (
            state_root.parts[len(anchor.parts) :]
            + relative[:-1]
        )
        for part in components:
            if not part or part in {".", ".."}:
                raise LaunchTransactionIntegrityError(
                    "launch storage directory component is invalid"
                )
            try:
                next_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                next_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=descriptor,
                )
            os.close(descriptor)
            descriptor = next_descriptor
    except LaunchTransactionIntegrityError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except FileNotFoundError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise LaunchTransactionIntegrityError(
            "launch storage parent changed or is unsafe"
        ) from exc
    try:
        yield descriptor, leaf, candidate.parent
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_anchored_parent_is_current(path: Path, parent_fd: int | None) -> None:
    if parent_fd is None:  # pragma: no cover - Windows fallback
        _safe_windows_parent(path, create=False)
        return
    opened = os.fstat(parent_fd)
    try:
        with _anchored_storage_parent(path, create=False) as (
            current_fd,
            _leaf,
            _parent,
        ):
            if current_fd is None:  # pragma: no cover - POSIX caller only
                return
            current = os.fstat(current_fd)
    except (FileNotFoundError, LaunchTransactionIntegrityError) as exc:
        raise LaunchTransactionIntegrityError(
            "launch storage parent changed or is unsafe"
        ) from exc
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise LaunchTransactionIntegrityError(
            "launch storage parent changed during operation"
        )


def _ensure_directory(path: Path) -> Path:
    target = Path(os.path.abspath(Path(path).expanduser()))
    sentinel = target / ".launch-directory-check"
    with _anchored_storage_parent(sentinel, create=True) as (
        parent_fd,
        _leaf,
        _parent,
    ):
        _assert_anchored_parent_is_current(sentinel, parent_fd)
    return target


def _list_json_paths(directory: Path) -> list[Path]:
    """List JSON entries from one held directory, never a reopened pathname."""
    target = _ensure_directory(directory)
    sentinel = target / ".launch-enumeration-check"
    with _anchored_storage_parent(sentinel, create=True) as (
        parent_fd,
        _leaf,
        parent,
    ):
        try:
            names = os.listdir(parent if parent_fd is None else parent_fd)
            _assert_anchored_parent_is_current(sentinel, parent_fd)
        except LaunchTransactionIntegrityError:
            raise
        except OSError as exc:
            raise LaunchTransactionIntegrityError(
                "launch storage registry is unreadable"
            ) from exc
    return [
        target / name
        for name in sorted(names)
        if isinstance(name, str)
        and Path(name).name == name
        and name.endswith(".json")
    ]


def _atomic_write_json(path: Path, payload: dict) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")
    if len(encoded) > _MAX_JSON_BYTES:
        raise LaunchTransactionIntegrityError("launch receipt is too large")

    with _anchored_storage_parent(path, create=True) as (
        parent_fd,
        leaf,
        parent,
    ):
        leaf_digest = hashlib.sha256(leaf.encode("utf-8")).hexdigest()[:16]
        if parent_fd is None:  # pragma: no cover - Windows packaged fallback
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".launch-{leaf_digest}.",
                suffix=".tmp",
                dir=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                _assert_anchored_parent_is_current(path, parent_fd)
                os.replace(temporary, path)
                _assert_anchored_parent_is_current(path, parent_fd)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            return

        temporary_leaf = None
        descriptor = -1
        try:
            for attempt in range(16):
                candidate = (
                    f".launch-{leaf_digest}.{os.getpid()}.{threading.get_ident()}."
                    f"{time.time_ns()}.{attempt}.tmp"
                )
                try:
                    descriptor = os.open(
                        candidate,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    temporary_leaf = candidate
                    break
                except FileExistsError:
                    continue
            if descriptor < 0 or temporary_leaf is None:
                raise LaunchTransactionIntegrityError(
                    "could not reserve launch atomic temp file"
                )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            _assert_anchored_parent_is_current(path, parent_fd)
            os.replace(
                temporary_leaf,
                leaf,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            temporary_leaf = None
            os.fsync(parent_fd)
            _assert_anchored_parent_is_current(path, parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_leaf is not None:
                try:
                    os.unlink(temporary_leaf, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass


def _read_json(path: Path) -> dict | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    with _anchored_storage_parent(path, create=True) as (
        parent_fd,
        leaf,
        _parent,
    ):
        descriptor = -1
        try:
            try:
                if parent_fd is None:  # pragma: no cover - Windows fallback
                    metadata = path.lstat()
                else:
                    metadata = os.stat(
                        leaf,
                        dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise LaunchTransactionIntegrityError("launch storage file is unsafe")
            if metadata.st_size < 0 or metadata.st_size > _MAX_JSON_BYTES:
                raise LaunchTransactionIntegrityError("launch storage file is too large")
            try:
                if parent_fd is None:  # pragma: no cover - Windows fallback
                    descriptor = os.open(path, flags)
                else:
                    descriptor = os.open(leaf, flags, dir_fd=parent_fd)
            except FileNotFoundError as exc:
                raise LaunchTransactionIntegrityError(
                    "launch storage file changed during read"
                ) from exc
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > _MAX_JSON_BYTES
                or (metadata.st_dev, metadata.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                raise LaunchTransactionIntegrityError("launch storage file is unsafe")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                value = json.load(handle)
            _assert_anchored_parent_is_current(path, parent_fd)
        except LaunchTransactionIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LaunchTransactionIntegrityError(
                "launch storage file is unreadable"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    if type(value) is not dict:
        raise LaunchTransactionIntegrityError("launch storage payload is invalid")
    return value


def _receipt_path(transaction_id: str) -> Path:
    return _root() / "receipts" / f"{_validate_id(transaction_id, label='launch transaction id')}.json"


def _session_binding_path(session_id: str) -> Path:
    return _root() / "by-session" / f"{_validate_session_id(session_id)}.json"


def _run_binding_path(run_id: str) -> Path:
    return _root() / "by-run" / f"{_validate_run_id(run_id)}.json"


def _launch_lock_timeout() -> LaunchTransactionLockTimeout:
    return LaunchTransactionLockTimeout(
        "timed out waiting for expert-team launch transaction lock; "
        "retry the same idempotent request"
    )


def _acquire_launch_file_lock(descriptor: int, deadline: float) -> None:
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - unsupported platform only
                raise RuntimeError("no supported OS file lock is available")
            return
        except (BlockingIOError, OSError):
            if time.monotonic() >= deadline:
                raise _launch_lock_timeout()
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _open_anchored_lock_file(
    path: Path,
    parent_fd: int | None,
    leaf: str,
) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if parent_fd is None:  # pragma: no cover - Windows fallback
        return os.open(path, os.O_CREAT | os.O_RDWR | nofollow, 0o600)
    existing_flags = os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0)
    create_flags = existing_flags | os.O_CREAT | os.O_EXCL
    for _attempt in range(16):
        try:
            return os.open(leaf, existing_flags, dir_fd=parent_fd)
        except FileNotFoundError:
            try:
                return os.open(
                    leaf,
                    create_flags,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
    raise LaunchTransactionIntegrityError(
        "could not open launch transaction lock file"
    )


@contextmanager
def launch_transaction_lock(
    transaction_id: str,
    *,
    timeout_seconds: float = LAUNCH_FILE_LOCK_TIMEOUT_SECONDS,
):
    transaction_id = _validate_id(transaction_id, label="launch transaction id")
    timeout = float(timeout_seconds)
    if timeout < 0:
        raise _launch_lock_timeout()
    deadline = time.monotonic() + timeout
    lock_path = _root() / "locks" / f"{transaction_id}.lock"
    thread_lock_key = str(lock_path)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.setdefault(thread_lock_key, threading.RLock())
    if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise _launch_lock_timeout()
    try:
        with _anchored_storage_parent(lock_path, create=True) as (
            parent_fd,
            leaf,
            _parent,
        ):
            try:
                descriptor = _open_anchored_lock_file(lock_path, parent_fd, leaf)
            except OSError as exc:
                raise LaunchTransactionIntegrityError(
                    "launch transaction lock is unsafe"
                ) from exc
            locked = False
            try:
                _acquire_launch_file_lock(descriptor, deadline)
                locked = True
                _assert_anchored_parent_is_current(lock_path, parent_fd)
                yield
            finally:
                try:
                    if locked and fcntl is not None:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    elif locked and msvcrt is not None:  # pragma: no cover - Windows only
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                finally:
                    os.close(descriptor)
    finally:
        lock.release()


def read_launch_transaction(transaction_id: str) -> dict | None:
    value = _read_json(_receipt_path(transaction_id))
    if value is None:
        return None
    receipt = _validate_receipt(value)
    if receipt["transaction_id"] != transaction_id:
        raise LaunchTransactionIntegrityError("launch receipt path identity mismatch")
    binding = _read_json(_session_binding_path(receipt["session_id"]))
    if type(binding) is not dict or binding != {
        "schema_version": LAUNCH_TRANSACTION_SCHEMA_VERSION,
        "session_id": receipt["session_id"],
        "transaction_id": transaction_id,
    }:
        raise LaunchTransactionIntegrityError("launch Session binding is missing or invalid")
    run_id = str(receipt.get("run_id") or "")
    if run_id:
        run_binding = _read_json(_run_binding_path(run_id))
        if type(run_binding) is not dict or run_binding != {
            "schema_version": LAUNCH_TRANSACTION_SCHEMA_VERSION,
            "run_id": run_id,
            "session_id": receipt["session_id"],
            "transaction_id": transaction_id,
        }:
            raise LaunchTransactionIntegrityError(
                "launch Run binding is missing or invalid"
            )
    return receipt


def read_or_repair_launch_transaction(transaction_id: str) -> dict | None:
    """Repair only the missing reverse binding for a fully valid receipt.

    The caller must hold ``launch_transaction_lock(transaction_id)``. This is
    the single crash window in initial metadata publication: receipt rename
    succeeded but the by-session binding rename did not.
    """
    raw = _read_json(_receipt_path(transaction_id))
    if raw is None:
        return None
    receipt = _validate_receipt(raw)
    if receipt["transaction_id"] != transaction_id:
        raise LaunchTransactionIntegrityError("launch receipt path identity mismatch")
    binding_path = _session_binding_path(receipt["session_id"])
    binding = _read_json(binding_path)
    expected = {
        "schema_version": LAUNCH_TRANSACTION_SCHEMA_VERSION,
        "session_id": receipt["session_id"],
        "transaction_id": transaction_id,
    }
    if binding is None:
        _atomic_write_json(binding_path, expected)
    elif binding != expected:
        raise LaunchTransactionIntegrityError("launch Session binding conflicts with receipt")
    run_id = str(receipt.get("run_id") or "")
    if run_id:
        run_binding_path = _run_binding_path(run_id)
        run_binding = _read_json(run_binding_path)
        expected_run_binding = {
            "schema_version": LAUNCH_TRANSACTION_SCHEMA_VERSION,
            "run_id": run_id,
            "session_id": receipt["session_id"],
            "transaction_id": transaction_id,
        }
        if run_binding is None:
            _atomic_write_json(run_binding_path, expected_run_binding)
        elif run_binding != expected_run_binding:
            raise LaunchTransactionIntegrityError(
                "launch Run binding conflicts with receipt"
            )
    return receipt


def read_launch_transaction_for_session(session_id: str) -> dict | None:
    binding = _read_json(_session_binding_path(session_id))
    if binding is None:
        return None
    if type(binding) is not dict or binding.get("schema_version") != LAUNCH_TRANSACTION_SCHEMA_VERSION:
        raise LaunchTransactionIntegrityError("launch Session binding is invalid")
    if str(binding.get("session_id") or "") != _validate_session_id(session_id):
        raise LaunchTransactionIntegrityError("launch Session binding identity mismatch")
    transaction_id = _validate_id(binding.get("transaction_id"), label="launch transaction id")
    receipt = read_launch_transaction(transaction_id)
    if receipt is None or receipt["session_id"] != session_id:
        raise LaunchTransactionIntegrityError("launch Session binding points to a missing receipt")
    return receipt


def read_launch_transaction_for_run(run_id: str) -> dict | None:
    run_id = _validate_run_id(run_id)
    binding = _read_json(_run_binding_path(run_id))
    if binding is None:
        return None
    if type(binding) is not dict or binding.get("schema_version") != LAUNCH_TRANSACTION_SCHEMA_VERSION:
        raise LaunchTransactionIntegrityError("launch Run binding is invalid")
    if str(binding.get("run_id") or "") != run_id:
        raise LaunchTransactionIntegrityError("launch Run binding identity mismatch")
    transaction_id = _validate_id(
        binding.get("transaction_id"),
        label="launch transaction id",
    )
    receipt = read_launch_transaction(transaction_id)
    if (
        receipt is None
        or str(receipt.get("run_id") or "") != run_id
        or str(receipt.get("session_id") or "")
        != str(binding.get("session_id") or "")
    ):
        raise LaunchTransactionIntegrityError(
            "launch Run binding points to a missing receipt"
        )
    return receipt


def write_launch_transaction(receipt: dict) -> dict:
    candidate = _validate_receipt(receipt)
    transaction_id = candidate["transaction_id"]
    raw_existing = _read_json(_receipt_path(transaction_id))
    existing = _validate_receipt(raw_existing) if raw_existing is not None else None
    if existing is not None and existing["transaction_id"] != transaction_id:
        raise LaunchTransactionIntegrityError("launch receipt path identity mismatch")
    if existing is not None:
        if any(existing.get(field) != candidate.get(field) for field in _IMMUTABLE_FIELDS):
            raise LaunchTransactionIntegrityError("launch receipt immutable fields changed")
        if candidate["state"] not in _TRANSITIONS.get(existing["state"], set()):
            raise LaunchTransactionIntegrityError(
                f"invalid launch state transition: {existing['state']} -> {candidate['state']}"
            )
        for field in ("run_id", "start_transaction_id"):
            if existing.get(field) and existing.get(field) != candidate.get(field):
                raise LaunchTransactionIntegrityError(f"launch receipt {field} changed")
    binding_path = _session_binding_path(candidate["session_id"])
    binding = _read_json(binding_path)
    expected_binding = {
        "schema_version": LAUNCH_TRANSACTION_SCHEMA_VERSION,
        "session_id": candidate["session_id"],
        "transaction_id": transaction_id,
    }
    if binding is not None and binding != expected_binding:
        raise LaunchTransactionIntegrityError("launch Session already belongs to another transaction")
    run_id = str(candidate.get("run_id") or "")
    run_binding_path = _run_binding_path(run_id) if run_id else None
    run_binding = _read_json(run_binding_path) if run_binding_path else None
    expected_run_binding = (
        {
            "schema_version": LAUNCH_TRANSACTION_SCHEMA_VERSION,
            "run_id": run_id,
            "session_id": candidate["session_id"],
            "transaction_id": transaction_id,
        }
        if run_id
        else None
    )
    if run_binding is not None and run_binding != expected_run_binding:
        raise LaunchTransactionIntegrityError(
            "launch Run already belongs to another transaction"
        )
    # Receipt first: a by-session file may never point at missing evidence.
    _atomic_write_json(_receipt_path(transaction_id), candidate)
    if binding is None:
        _atomic_write_json(binding_path, expected_binding)
    if run_binding_path is not None and run_binding is None:
        _atomic_write_json(run_binding_path, expected_run_binding)
    return copy.deepcopy(candidate)


def is_session_public(session_id: str) -> bool:
    """Fail closed only for Sessions that carry a Launch reverse binding."""
    try:
        receipt = read_launch_transaction_for_session(session_id)
    except (LaunchTransactionIntegrityError, OSError):
        return False
    return receipt is None or receipt.get("state") == "committed"


def is_launch_marker_committed(session_id: str, transaction_id: str) -> bool:
    """Validate a durable Session marker against the complete receipt bundle."""
    try:
        receipt = read_launch_transaction(
            _validate_id(transaction_id, label="launch transaction id")
        )
    except (LaunchTransactionIntegrityError, OSError):
        return False
    return bool(
        receipt
        and receipt.get("state") == "committed"
        and receipt.get("session_id") == session_id
    )


def is_run_public(run_id: str) -> bool:
    """Return False for every Run owned by a non-committed portal launch."""
    try:
        receipt = read_launch_transaction_for_run(run_id)
    except (LaunchTransactionIntegrityError, OSError):
        return False
    if receipt is not None:
        return receipt.get("state") == "committed"

    # A missing reverse binding is a crash/tamper window, not evidence that a
    # launch-owned Run became ordinary. Scan receipts and fail closed on any
    # matching or unreadable candidate until same-key recovery repairs it.
    receipts_root = _root() / "receipts"
    try:
        for path in _list_json_paths(receipts_root):
            raw = _read_json(path)
            candidate = _validate_receipt(raw)
            if str(candidate.get("run_id") or "") == str(run_id or ""):
                return False
    except Exception:
        return False
    return True


def hidden_session_ids() -> set[str]:
    root = _root() / "by-session"
    entries = _list_json_paths(root)
    hidden: set[str] = set()
    for path in entries:
        session_id = path.stem
        try:
            if not is_session_public(session_id):
                hidden.add(session_id)
        except Exception:
            hidden.add(session_id)
    # Receipt scan is the recovery-side complement to by-session. It catches a
    # crash or tamper that removed the reverse binding after the receipt rename,
    # including state.db rows that can otherwise re-enter the sidebar as CLI
    # metadata without carrying the Session's internal marker.
    receipts_root = _root() / "receipts"
    receipt_paths = _list_json_paths(receipts_root)
    for path in receipt_paths:
        try:
            raw = _read_json(path)
            receipt = _validate_receipt(raw)
            session_id = str(receipt["session_id"])
            strict = read_launch_transaction(str(receipt["transaction_id"]))
            if strict is None or strict.get("state") != "committed":
                hidden.add(session_id)
        except Exception:
            try:
                raw = _read_json(path)
                session_id = str((raw or {}).get("session_id") or "")
                if _SAFE_SESSION_ID.fullmatch(session_id):
                    hidden.add(session_id)
                else:
                    raise LaunchTransactionIntegrityError(
                        "launch receipt registry is unsafe"
                    )
            except LaunchTransactionIntegrityError:
                raise
            except Exception as exc:
                raise LaunchTransactionIntegrityError(
                    "launch receipt registry changed or is unsafe"
                ) from exc
    return hidden


def is_session_deletion_public(session_id: str) -> bool:
    """Decide DELETE visibility without reading the Session sidecar.

    A corrupt/symlinked ordinary sidecar must still be reachable by the
    anchored deletion transaction.  Conversely, a launch receipt remains
    authoritative when its by-session reverse binding is missing.  Enumerate
    both registry directions and fail closed on any unreadable or changing
    launch evidence.
    """
    try:
        normalized = _validate_session_id(session_id)
        return normalized not in hidden_session_ids()
    except Exception:
        return False


def new_reserved_receipt(
    *,
    transaction_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    launch_profile_id: str,
    launch_profile_snapshot: dict,
    prompt: str,
    session_options: dict,
    session_id: str,
    workspace: str,
    initial_session_snapshot: dict,
) -> dict:
    now = time.time()
    return {
        "schema_version": LAUNCH_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "state": "reserved",
        "idempotency_key_hash": launch_idempotency_key_hash(idempotency_key),
        "request_fingerprint": request_fingerprint,
        "launch_profile_id": str(launch_profile_id),
        "launch_profile_snapshot": copy.deepcopy(launch_profile_snapshot),
        "launch_profile_sha256": launch_profile_snapshot_digest(
            launch_profile_snapshot
        ),
        "prompt_sha256": hashlib.sha256(str(prompt).encode("utf-8")).hexdigest(),
        "session_options": copy.deepcopy(session_options),
        "session_id": session_id,
        "workspace": workspace,
        "initial_session_snapshot": copy.deepcopy(initial_session_snapshot),
        "initial_session_sha256": launch_session_snapshot_digest(
            initial_session_snapshot
        ),
        "run_id": None,
        "start_transaction_id": None,
        "created_at": now,
        "updated_at": now,
    }
