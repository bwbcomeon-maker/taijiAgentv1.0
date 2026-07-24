"""JSON storage for expert team runs."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX: macOS and Linux production targets.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None

try:  # Windows fallback for packaged desktop builds.
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX only
    msvcrt = None


START_TRANSACTION_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_START_TRANSACTION_THREAD_LOCKS: dict[str, threading.Lock] = {}
_START_TRANSACTION_THREAD_LOCKS_GUARD = threading.Lock()
START_FILE_LOCK_TIMEOUT_SECONDS = 5.0
_MAX_TRANSACTION_JSON_BYTES = 16 * 1024 * 1024
_MAX_RUN_JSON_BYTES = 64 * 1024 * 1024
_START_PROJECTION_FIELDS = (
    "schema_version",
    "contract_version",
    "product_mode",
    "start_transaction_id",
    "run_id",
    "session_id",
    "team_id",
    "team_title",
    "team_image",
    "title",
    "prompt",
    "created_at",
    "launch_profile_id",
    "launch_profile_snapshot",
    "review_policy",
    "_tasks_template",
)


class StartTransactionIntegrityError(ValueError):
    """Raised when a durable start receipt cannot be trusted."""


def _safe_storage_directory(workspace: Path, *parts: str) -> Path:
    """Resolve one storage directory without following nested symlinks.

    ``resolve_trusted_workspace`` validates the workspace root, but an attacker
    or damaged local state can still replace ``.taiji`` (or a deeper storage
    directory) with a symlink. Walk every existing component with ``lstat`` so
    reads, temp-file creation, and lock files cannot escape that trusted root.
    """
    try:
        current = Path(workspace).expanduser().resolve(strict=True)
        if not current.is_dir():
            raise StartTransactionIntegrityError("expert team workspace is not a directory")
    except StartTransactionIntegrityError:
        raise
    except OSError as exc:
        raise StartTransactionIntegrityError("expert team workspace is unavailable") from exc
    for part in parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StartTransactionIntegrityError(
                "expert team storage parent is unreadable"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise StartTransactionIntegrityError(
                "expert team storage parent must not be a symlink"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise StartTransactionIntegrityError(
                "expert team storage parent is not a directory"
            )
    return current


def _storage_workspace_and_relative_path(path: Path) -> tuple[Path, tuple[str, ...]]:
    """Recover the lexical workspace anchor for one generated storage path."""
    candidate = Path(os.path.abspath(Path(path).expanduser()))
    storage_root = next(
        (
            parent
            for parent in candidate.parents
            if parent.name == "expert-teams" and parent.parent.name == ".taiji"
        ),
        None,
    )
    if storage_root is None:
        raise StartTransactionIntegrityError("path is outside expert team storage")
    workspace = storage_root.parent.parent
    try:
        relative = candidate.relative_to(workspace).parts
    except ValueError as exc:  # pragma: no cover - defensive only
        raise StartTransactionIntegrityError(
            "path is outside expert team workspace"
        ) from exc
    if len(relative) < 3 or relative[:2] != (".taiji", "expert-teams"):
        raise StartTransactionIntegrityError("expert team storage path is invalid")
    return workspace, relative


@contextmanager
def _anchored_storage_parent(path: Path, *, create: bool):
    """Yield an opened parent directory and leaf without following symlinks.

    POSIX operations below use the returned fd with ``dir_fd`` for the actual
    open/rename/unlink.  A concurrent rename followed by a symlink exchange
    therefore cannot redirect the operation after validation.  Windows keeps
    the existing validated-path fallback because Python does not expose the
    required ``openat`` family there.
    """
    candidate = Path(os.path.abspath(Path(path).expanduser()))
    workspace, relative = _storage_workspace_and_relative_path(candidate)
    parent_parts = relative[:-1]
    leaf = relative[-1]
    if not leaf or leaf in {".", ".."}:
        raise StartTransactionIntegrityError("expert team storage leaf is invalid")

    if os.name == "nt":  # pragma: no cover - Windows packaged fallback
        parent = _safe_storage_directory(workspace, *parent_parts)
        if create:
            parent.mkdir(parents=True, exist_ok=True)
            parent = _safe_storage_directory(workspace, *parent_parts)
        yield None, leaf
        return

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    anchor = Path(candidate.anchor or os.sep)
    fd = -1
    try:
        fd = os.open(anchor, directory_flags)
        workspace_components = workspace.parts[len(anchor.parts) :]
        components = [
            (part, False) for part in workspace_components
        ] + [
            (part, create) for part in parent_parts
        ]
        for part, may_create in components:
            if not part or part in {".", ".."}:
                raise StartTransactionIntegrityError(
                    "expert team storage directory component is invalid"
                )
            try:
                next_fd = os.open(part, directory_flags, dir_fd=fd)
            except FileNotFoundError:
                if not may_create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    # A competing creator may have won. The no-follow open
                    # below decides whether it created a trustworthy directory.
                    pass
                next_fd = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    except StartTransactionIntegrityError:
        if fd >= 0:
            os.close(fd)
        raise
    except FileNotFoundError:
        if fd >= 0:
            os.close(fd)
        raise
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        raise StartTransactionIntegrityError(
            "expert team storage parent changed or is unsafe"
        ) from exc
    try:
        yield fd, leaf
    finally:
        if fd >= 0:
            os.close(fd)


def _assert_anchored_parent_is_current(path: Path, parent_fd: int | None) -> None:
    """Fail when the lexical storage path no longer names the opened parent."""
    if parent_fd is None:  # pragma: no cover - Windows fallback
        workspace, relative = _storage_workspace_and_relative_path(path)
        _safe_storage_directory(workspace, *relative[:-1])
        return
    opened = os.fstat(parent_fd)
    with _anchored_storage_parent(path, create=False) as (current_fd, _leaf):
        if current_fd is None:  # pragma: no cover - POSIX caller only
            return
        current = os.fstat(current_fd)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise StartTransactionIntegrityError(
            "expert team storage parent changed during operation"
        )


def runs_dir(workspace: Path) -> Path:
    return _safe_storage_directory(workspace, ".taiji", "expert-teams", "runs")


def start_transactions_dir(workspace: Path) -> Path:
    return _safe_storage_directory(
        workspace,
        ".taiji",
        "expert-teams",
        "start-transactions",
    )


def start_transaction_id(session_id: str, idempotency_key: str) -> str:
    identity = f"expert-team-start-v1\0{session_id}\0{idempotency_key}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def start_idempotency_key_hash(idempotency_key: str) -> str:
    return hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()


def start_session_metadata_digest(snapshot: dict) -> str:
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def start_run_digest(run: dict) -> str:
    payload = json.dumps(
        run,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def start_run_projection_digest(run: dict) -> str:
    projection = {
        key: json.loads(json.dumps(run.get(key), ensure_ascii=False))
        for key in _START_PROJECTION_FIELDS
    }
    return start_run_digest(projection)


def start_receipt_digest(receipt: dict) -> str:
    return start_run_digest(receipt)


def _validate_start_session_metadata_snapshot(receipt: dict) -> None:
    snapshot_present = "session_metadata_before_start" in receipt
    digest_present = "session_metadata_before_start_sha256" in receipt
    if not snapshot_present or not digest_present:
        raise StartTransactionIntegrityError(
            "start transaction Session metadata snapshot is missing"
        )
    snapshot = receipt.get("session_metadata_before_start")
    digest = str(receipt.get("session_metadata_before_start_sha256") or "")
    if not isinstance(snapshot, dict) or _SHA256_PATTERN.fullmatch(digest) is None:
        raise StartTransactionIntegrityError("start transaction Session metadata snapshot is invalid")
    expected_fields = {
        "title",
        "message_count",
        "user_message_count",
        "updated_at",
        "last_message_at",
    }
    if set(snapshot) != expected_fields:
        raise StartTransactionIntegrityError("start transaction Session metadata fields are invalid")
    if not isinstance(snapshot.get("title"), str):
        raise StartTransactionIntegrityError("start transaction Session title snapshot is invalid")
    message_count = snapshot.get("message_count")
    user_message_count = snapshot.get("user_message_count")
    if type(message_count) is not int or message_count < 0:
        raise StartTransactionIntegrityError("start transaction Session message count is invalid")
    if user_message_count is not None and (
        type(user_message_count) is not int or user_message_count < 0
    ):
        raise StartTransactionIntegrityError("start transaction Session user message count is invalid")
    for field in ("updated_at", "last_message_at"):
        value = snapshot.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise StartTransactionIntegrityError(
                f"start transaction Session {field} snapshot is invalid"
            )
    if start_session_metadata_digest(snapshot) != digest:
        raise StartTransactionIntegrityError("start transaction Session metadata digest does not match")
    owned_updated_at = receipt.get("session_updated_at_after_start")
    if (
        isinstance(owned_updated_at, bool)
        or not isinstance(owned_updated_at, (int, float))
        or not math.isfinite(float(owned_updated_at))
        or owned_updated_at < 0
    ):
        raise StartTransactionIntegrityError(
            "start transaction owned Session timestamp is invalid"
        )

    title_after_start = receipt.get("session_title_after_start")
    if not isinstance(title_after_start, str):
        raise StartTransactionIntegrityError(
            "start transaction owned Session title is invalid"
        )


def _read_json_object(path: Path, *, max_bytes: int) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    with _anchored_storage_parent(path, create=False) as (parent_fd, leaf):
        fd = -1
        try:
            if parent_fd is None:  # pragma: no cover - Windows fallback
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise StartTransactionIntegrityError(
                        f"{path.name} is not a regular file"
                    )
                fd = os.open(path, flags)
            else:
                metadata = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise StartTransactionIntegrityError(
                        f"{path.name} is not a regular file"
                    )
                fd = os.open(leaf, flags, dir_fd=parent_fd)
            if metadata.st_size < 0 or metadata.st_size > max_bytes:
                raise StartTransactionIntegrityError(
                    f"{path.name} exceeds the safe size limit"
                )
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
                raise StartTransactionIntegrityError(
                    f"{path.name} is not a safe JSON file"
                )
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                value = json.load(handle)
            _assert_anchored_parent_is_current(path, parent_fd)
        except StartTransactionIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StartTransactionIntegrityError(f"{path.name} is unreadable") from exc
        finally:
            if fd >= 0:
                os.close(fd)
    if not isinstance(value, dict):
        raise StartTransactionIntegrityError(f"{path.name} is not a JSON object")
    return value


def _validate_start_transaction_payload(receipt: dict, transaction_id: str) -> dict:
    required_identity = {
        "schema_version": START_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
    }
    if any(receipt.get(key) != value for key, value in required_identity.items()):
        raise StartTransactionIntegrityError("start transaction receipt identity does not match")
    if not str(receipt.get("session_id") or ""):
        raise StartTransactionIntegrityError("start transaction Session identity is invalid")
    if _SHA256_PATTERN.fullmatch(str(receipt.get("request_fingerprint") or "")) is None:
        raise StartTransactionIntegrityError("start transaction fingerprint is invalid")
    if _SHA256_PATTERN.fullmatch(str(receipt.get("idempotency_key_hash") or "")) is None:
        raise StartTransactionIntegrityError("start transaction idempotency identity is invalid")
    try:
        safe_run_id(str(receipt.get("run_id") or ""))
    except ValueError as exc:
        raise StartTransactionIntegrityError("start transaction run_id is invalid") from exc
    if receipt.get("state") not in {
        "prepared",
        "committed",
        "rolled_back",
        "recovery_required",
    }:
        raise StartTransactionIntegrityError("start transaction state is invalid")
    _validate_start_session_metadata_snapshot(receipt)
    _validate_start_transaction_timestamps(receipt)
    initial = receipt.get("initial_run_snapshot")
    initial_sha = str(receipt.get("initial_run_sha256") or "")
    projection_sha = str(receipt.get("initial_start_projection_sha256") or "")
    if (
        not isinstance(initial, dict)
        or _SHA256_PATTERN.fullmatch(initial_sha) is None
        or _SHA256_PATTERN.fullmatch(projection_sha) is None
        or start_run_digest(initial) != initial_sha
        or start_run_projection_digest(initial) != projection_sha
    ):
        raise StartTransactionIntegrityError(
            "start transaction immutable Run snapshot is invalid"
        )
    if (
        str(initial.get("run_id") or "") != str(receipt.get("run_id") or "")
        or str(initial.get("session_id") or "") != str(receipt.get("session_id") or "")
        or str(initial.get("start_transaction_id") or "")
        != str(receipt.get("transaction_id") or "")
        or int(initial.get("schema_version") or 0) != 3
        or str(initial.get("product_mode") or "") != "standalone"
    ):
        raise StartTransactionIntegrityError(
            "start transaction immutable Run ownership is invalid"
        )
    pending_sha = receipt.get("pending_run_sha256")
    if pending_sha is not None and pending_sha != initial_sha:
        raise StartTransactionIntegrityError("start transaction pending Run digest is invalid")
    return receipt


def _validate_start_transaction_timestamps(receipt: dict) -> None:
    for field in ("created_at", "updated_at"):
        value = receipt.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise StartTransactionIntegrityError(
                f"start transaction {field} is invalid"
            )


def start_transaction_path(workspace: Path, transaction_id: str) -> Path:
    if _SHA256_PATTERN.fullmatch(str(transaction_id or "")) is None:
        raise ValueError("Invalid expert team start transaction_id")
    return start_transactions_dir(workspace) / f"{transaction_id}.json"


def start_run_binding_path(workspace: Path, run_id: str) -> Path:
    return _safe_storage_directory(
        workspace,
        ".taiji",
        "expert-teams",
        "start-transactions",
        "by-run",
    ) / f"{safe_run_id(run_id)}.json"


def start_session_binding_path(workspace: Path, session_id: str) -> Path:
    session_id = str(session_id or "")
    if not session_id:
        raise ValueError("Invalid expert team start session_id")
    digest = hashlib.sha256(
        f"expert-team-start-session-index-v1\0{session_id}".encode("utf-8")
    ).hexdigest()
    return _safe_storage_directory(
        workspace,
        ".taiji",
        "expert-teams",
        "start-transactions",
        "by-session",
    ) / f"{digest}.json"


def pending_run_path(workspace: Path, run_id: str) -> Path:
    return _safe_storage_directory(
        workspace,
        ".taiji",
        "expert-teams",
        "start-transactions",
        "pending",
    ) / f"{safe_run_id(run_id)}.json"


def safe_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if run_id in {".", ".."} or not run_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("Invalid expert team run_id")
    return run_id


def run_path(workspace: Path, run_id: str) -> Path:
    return runs_dir(workspace) / f"{safe_run_id(run_id)}.json"


def run_lock_path(workspace: Path, run_id: str) -> Path:
    return _safe_storage_directory(
        workspace,
        ".taiji",
        "expert-teams",
        "runs",
        ".locks",
    ) / f"{safe_run_id(run_id)}.lock"


def start_transaction_lock_path(workspace: Path, transaction_id: str) -> Path:
    if _SHA256_PATTERN.fullmatch(str(transaction_id or "")) is None:
        raise ValueError("Invalid expert team start transaction_id")
    return _safe_storage_directory(
        workspace,
        ".taiji",
        "expert-teams",
        "start-transactions",
        ".locks",
    ) / f"{transaction_id}.lock"


def start_session_lock_path(workspace: Path, session_id: str) -> Path:
    session_id = str(session_id or "")
    if not session_id:
        raise ValueError("Invalid expert team start session_id")
    digest = hashlib.sha256(
        f"expert-team-start-session-v1\0{session_id}".encode("utf-8")
    ).hexdigest()
    return _safe_storage_directory(
        workspace,
        ".taiji",
        "expert-teams",
        "start-transactions",
        ".session-locks",
    ) / f"{digest}.lock"


def _acquire_storage_file_lock(fd: int, deadline: float, label: str) -> None:
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                    os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - unsupported platform only
                raise RuntimeError("No supported OS file-lock implementation is available")
            return
        except (BlockingIOError, OSError):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for {label} lock")
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _open_anchored_lock_file(
    path: Path,
    parent_fd: int | None,
    leaf: str,
) -> int:
    """Open/create one lock inode without a concurrent-create ambiguity."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if parent_fd is None:  # pragma: no cover - Windows fallback
        return os.open(path, os.O_CREAT | os.O_RDWR | nofollow, 0o600)
    open_existing_flags = os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0)
    create_flags = open_existing_flags | os.O_CREAT | os.O_EXCL
    for _attempt in range(16):
        try:
            return os.open(leaf, open_existing_flags, dir_fd=parent_fd)
        except FileNotFoundError:
            try:
                return os.open(
                    leaf,
                    create_flags,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                # Another process atomically created the shared lock inode.
                continue
    raise StartTransactionIntegrityError("could not open expert team lock file")


@contextmanager
def run_file_lock(
    workspace: Path,
    run_id: str,
    *,
    timeout_seconds: float = START_FILE_LOCK_TIMEOUT_SECONDS,
):
    """Hold an inter-process exclusive lock for one run's full CAS window."""
    timeout = float(timeout_seconds)
    if timeout < 0:
        raise TimeoutError("timed out waiting for expert team Run lock")
    deadline = time.monotonic() + timeout
    path = run_lock_path(workspace, run_id)
    with _anchored_storage_parent(path, create=True) as (parent_fd, leaf):
        fd = _open_anchored_lock_file(path, parent_fd, leaf)
        locked = False
        try:
            _acquire_storage_file_lock(fd, deadline, "expert team Run")
            locked = True
            _assert_anchored_parent_is_current(path, parent_fd)
            yield
        finally:
            try:
                if locked and fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif locked and msvcrt is not None:  # pragma: no cover - Windows only
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)


@contextmanager
def _start_file_lock(
    path: Path,
    *,
    timeout_seconds: float = START_FILE_LOCK_TIMEOUT_SECONDS,
):
    timeout = float(timeout_seconds)
    if timeout < 0:
        raise TimeoutError("timed out waiting for expert team start lock")
    deadline = time.monotonic() + timeout
    lock_key = str(path.resolve())
    with _START_TRANSACTION_THREAD_LOCKS_GUARD:
        thread_lock = _START_TRANSACTION_THREAD_LOCKS.setdefault(lock_key, threading.Lock())
    if not thread_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError("timed out waiting for expert team start lock")
    try:
        with _anchored_storage_parent(path, create=True) as (parent_fd, leaf):
            fd = _open_anchored_lock_file(path, parent_fd, leaf)
            locked = False
            try:
                _acquire_storage_file_lock(fd, deadline, "expert team start")
                locked = True
                _assert_anchored_parent_is_current(path, parent_fd)
                yield
            finally:
                try:
                    if locked and fcntl is not None:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    elif locked and msvcrt is not None:  # pragma: no cover - Windows only
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                finally:
                    os.close(fd)
    finally:
        thread_lock.release()


@contextmanager
def start_transaction_lock(
    workspace: Path,
    transaction_id: str,
    *,
    timeout_seconds: float = START_FILE_LOCK_TIMEOUT_SECONDS,
):
    """Serialize one idempotent start across threads and desktop processes."""
    with _start_file_lock(
        start_transaction_lock_path(workspace, transaction_id),
        timeout_seconds=timeout_seconds,
    ):
        yield


@contextmanager
def start_session_lock(
    workspace: Path,
    session_id: str,
    *,
    timeout_seconds: float = START_FILE_LOCK_TIMEOUT_SECONDS,
):
    """Serialize all expert-team starts that mutate one Session transcript."""
    with _start_file_lock(
        start_session_lock_path(workspace, session_id),
        timeout_seconds=timeout_seconds,
    ):
        yield


def _fsync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    try:
        directory_fd = os.open(path, directory_flag)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


def _write_json_atomic(path: Path, payload: dict) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    with _anchored_storage_parent(path, create=True) as (parent_fd, leaf):
        if parent_fd is None:  # pragma: no cover - Windows fallback
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temp_path = Path(handle.name)
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
                temp_path = None
                _fsync_directory(path.parent)
                _assert_anchored_parent_is_current(path, parent_fd)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
            return

        temp_leaf = None
        fd = -1
        try:
            for attempt in range(16):
                candidate = (
                    f".{leaf}.{os.getpid()}.{threading.get_ident()}."
                    f"{time.time_ns()}.{attempt}.tmp"
                )
                try:
                    fd = os.open(
                        candidate,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    temp_leaf = candidate
                    break
                except FileExistsError:
                    continue
            if fd < 0 or temp_leaf is None:
                raise StartTransactionIntegrityError(
                    "could not reserve expert team atomic temp file"
                )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temp_leaf,
                leaf,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            temp_leaf = None
            os.fsync(parent_fd)
            _assert_anchored_parent_is_current(path, parent_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            if temp_leaf is not None:
                try:
                    os.unlink(temp_leaf, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass


def write_run(workspace: Path, run: dict) -> dict:
    run_id = safe_run_id(str(run.get("run_id") or ""))
    path = run_path(workspace, run_id)
    if path.exists():
        existing = read_run_raw(workspace, run_id)
        if _requires_standalone_start_binding(existing):
            _validate_public_standalone_run(
                workspace,
                existing,
                require_committed=True,
            )
            _validate_public_standalone_run(workspace, run, require_committed=True)
    _write_json_atomic(path, run)
    return run


def _requires_standalone_start_binding(run: dict) -> bool:
    """Return whether *run* belongs to the transaction-bound v3 contract."""
    try:
        schema_version = int(run.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    # Either marker is enough. A partially stripped v3 payload must not fall
    # back to the permissive legacy-v2 path.
    return schema_version >= 3 or str(run.get("product_mode") or "") == "standalone"


def _validate_public_standalone_run(
    workspace: Path,
    run: dict,
    *,
    require_committed: bool,
) -> dict:
    """Prove the immutable launch binding for a standalone Run on read/write."""
    run_id = safe_run_id(str(run.get("run_id") or ""))
    if (
        int(run.get("schema_version") or 0) != 3
        or str(run.get("product_mode") or "") != "standalone"
    ):
        raise StartTransactionIntegrityError(
            "standalone Run contract markers are incomplete"
        )
    receipt = read_start_transaction_for_run(workspace, run_id)
    if receipt is None:
        raise StartTransactionIntegrityError("standalone Run start binding is missing")
    if require_committed and receipt.get("state") != "committed":
        raise StartTransactionIntegrityError("standalone Run start is not committed")
    initial = receipt.get("initial_run_snapshot")
    if not isinstance(initial, dict):
        raise StartTransactionIntegrityError("standalone Run snapshot is missing")
    expected = {
        "run_id": str(receipt.get("run_id") or ""),
        "session_id": str(receipt.get("session_id") or ""),
        "start_transaction_id": str(receipt.get("transaction_id") or ""),
        "launch_profile_id": str(initial.get("launch_profile_id") or ""),
    }
    if any(str(run.get(key) or "") != value for key, value in expected.items()):
        raise StartTransactionIntegrityError(
            "standalone Run ownership does not match its start receipt"
        )
    projection_digest = str(receipt.get("initial_start_projection_sha256") or "")
    if (
        _SHA256_PATTERN.fullmatch(projection_digest) is None
        or start_run_projection_digest(initial) != projection_digest
        or start_run_projection_digest(run) != projection_digest
    ):
        raise StartTransactionIntegrityError(
            "standalone Run immutable launch projection changed"
        )
    return run


def _read_start_run_binding(workspace: Path, run_id: str) -> dict:
    requested_run_id = safe_run_id(run_id)
    path = start_run_binding_path(workspace, requested_run_id)
    if not path.exists():
        raise StartTransactionIntegrityError("start Run binding is missing")
    binding = _read_json_object(path, max_bytes=_MAX_TRANSACTION_JSON_BYTES)
    transaction_id = str(binding.get("transaction_id") or "")
    allowed = {
        "schema_version",
        "run_id",
        "transaction_id",
        "session_id",
        "receipt_sha256",
        "previous_receipt_sha256",
    }
    if (
        not set(binding).issubset(allowed)
        or binding.get("schema_version") != START_TRANSACTION_SCHEMA_VERSION
        or binding.get("run_id") != requested_run_id
        or not str(binding.get("session_id") or "")
        or _SHA256_PATTERN.fullmatch(transaction_id) is None
        or _SHA256_PATTERN.fullmatch(str(binding.get("receipt_sha256") or "")) is None
        or (
            binding.get("previous_receipt_sha256") is not None
            and _SHA256_PATTERN.fullmatch(
                str(binding.get("previous_receipt_sha256") or "")
            )
            is None
        )
    ):
        raise StartTransactionIntegrityError("start Run binding is invalid")
    return binding


def _read_start_session_binding(workspace: Path, session_id: str) -> dict:
    requested_session_id = str(session_id or "")
    path = start_session_binding_path(workspace, requested_session_id)
    if not path.exists():
        raise StartTransactionIntegrityError("start Session binding is missing")
    binding = _read_json_object(path, max_bytes=_MAX_TRANSACTION_JSON_BYTES)
    transaction_ids = binding.get("transaction_ids")
    if (
        set(binding) != {"schema_version", "session_id", "transaction_ids"}
        or binding.get("schema_version") != START_TRANSACTION_SCHEMA_VERSION
        or binding.get("session_id") != requested_session_id
        or not isinstance(transaction_ids, list)
    ):
        raise StartTransactionIntegrityError("start Session binding is invalid")
    normalized = [str(value or "") for value in transaction_ids]
    if (
        len(normalized) != len(set(normalized))
        or any(_SHA256_PATTERN.fullmatch(value) is None for value in normalized)
    ):
        raise StartTransactionIntegrityError(
            "start Session binding contains an invalid transaction"
        )
    return binding


def _receipt_digest_matches_run_binding(receipt: dict, binding: dict) -> bool:
    digest = start_receipt_digest(receipt)
    return digest in {
        str(binding.get("receipt_sha256") or ""),
        str(binding.get("previous_receipt_sha256") or ""),
    }


def _validate_start_state_transition(before: str, after: str) -> None:
    allowed = {
        "prepared": {"prepared", "committed", "rolled_back", "recovery_required"},
        "recovery_required": {
            "prepared",
            "committed",
            "rolled_back",
            "recovery_required",
        },
        "rolled_back": {"rolled_back", "prepared"},
        "committed": {"committed"},
    }
    if after not in allowed.get(before, set()):
        raise StartTransactionIntegrityError(
            f"invalid start transaction state transition: {before} -> {after}"
        )


def _raw_start_transaction_payload(workspace: Path, transaction_id: str) -> dict | None:
    path = start_transaction_path(workspace, transaction_id)
    if not path.exists():
        return None
    receipt = _read_json_object(path, max_bytes=_MAX_TRANSACTION_JSON_BYTES)
    return _validate_start_transaction_payload(receipt, transaction_id)


def write_start_transaction(workspace: Path, receipt: dict) -> dict:
    transaction_id = str(receipt.get("transaction_id") or "")
    if _SHA256_PATTERN.fullmatch(transaction_id) is None:
        raise ValueError("Invalid expert team start transaction_id")
    run_id = safe_run_id(str(receipt.get("run_id") or ""))
    session_id = str(receipt.get("session_id") or "")
    if not session_id:
        raise ValueError("Invalid expert team start session_id")
    _validate_start_transaction_payload(receipt, transaction_id)
    receipt_sha256 = start_receipt_digest(receipt)

    # The Run binding is written first so a canonical Run can never become
    # publicly visible before its transaction is known.  The receipt itself is
    # then durable before the Session index: a crash must never leave by-session
    # pointing at a missing receipt that permanently poisons Session reads.
    run_binding = {
        "schema_version": START_TRANSACTION_SCHEMA_VERSION,
        "run_id": run_id,
        "transaction_id": transaction_id,
        "session_id": session_id,
        "receipt_sha256": receipt_sha256,
    }
    run_binding_path = start_run_binding_path(workspace, run_id)
    existing_receipt = _raw_start_transaction_payload(workspace, transaction_id)
    if run_binding_path.exists():
        existing_binding = _read_start_run_binding(workspace, run_id)
        if any(
            existing_binding.get(key) != run_binding.get(key)
            for key in ("schema_version", "run_id", "transaction_id", "session_id")
        ):
            raise StartTransactionIntegrityError("start Run binding conflicts with receipt")
        if existing_receipt is not None:
            if not _receipt_digest_matches_run_binding(existing_receipt, existing_binding):
                raise StartTransactionIntegrityError(
                    "start transaction receipt digest does not match its Run binding"
                )
            _validate_start_state_transition(
                str(existing_receipt.get("state") or ""),
                str(receipt.get("state") or ""),
            )
            immutable_receipt_fields = (
                "schema_version",
                "transaction_id",
                "session_id",
                "idempotency_key_hash",
                "request_fingerprint",
                "run_id",
                "initial_run_snapshot",
                "initial_run_sha256",
                "initial_start_projection_sha256",
                "session_metadata_before_start",
                "session_metadata_before_start_sha256",
                "session_updated_at_after_start",
                "session_title_after_start",
                "created_at",
            )
            if any(existing_receipt.get(key) != receipt.get(key) for key in immutable_receipt_fields):
                raise StartTransactionIntegrityError(
                    "start transaction immutable receipt fields changed"
                )
            previous_digest = start_receipt_digest(existing_receipt)
            if previous_digest != receipt_sha256:
                run_binding["previous_receipt_sha256"] = previous_digest
        elif receipt_sha256 not in {
            str(existing_binding.get("receipt_sha256") or ""),
            str(existing_binding.get("previous_receipt_sha256") or ""),
        }:
            raise StartTransactionIntegrityError(
                "start transaction receipt is missing behind a conflicting Run binding"
            )
        if existing_binding != run_binding:
            _write_json_atomic(run_binding_path, run_binding)
    else:
        if existing_receipt is not None:
            raise StartTransactionIntegrityError("start Run binding is missing")
        _write_json_atomic(run_binding_path, run_binding)

    session_binding_path = start_session_binding_path(workspace, session_id)
    transaction_ids: list[str] = []
    if session_binding_path.exists():
        session_binding = _read_start_session_binding(workspace, session_id)
        transaction_ids = [str(value) for value in session_binding["transaction_ids"]]
        if transaction_id in transaction_ids and existing_receipt is None:
            raise StartTransactionIntegrityError(
                "start Session binding points to a missing transaction receipt"
            )
    elif existing_receipt is not None:
        raise StartTransactionIntegrityError("start Session binding is missing")
    session_binding_payload = None
    if transaction_id not in transaction_ids:
        transaction_ids.append(transaction_id)
        session_binding_payload = {
            "schema_version": START_TRANSACTION_SCHEMA_VERSION,
            "session_id": session_id,
            "transaction_ids": transaction_ids,
        }
    _write_json_atomic(start_transaction_path(workspace, transaction_id), receipt)
    if session_binding_payload is not None:
        _write_json_atomic(
            session_binding_path,
            session_binding_payload,
        )
    return receipt


def read_start_transaction(
    workspace: Path,
    *,
    session_id: str,
    idempotency_key: str,
) -> dict | None:
    transaction_id = start_transaction_id(session_id, idempotency_key)
    receipt = read_start_transaction_by_id(workspace, transaction_id)
    if receipt is None:
        session_binding_path = start_session_binding_path(workspace, session_id)
        if session_binding_path.exists():
            binding = _read_start_session_binding(workspace, session_id)
            if transaction_id in binding["transaction_ids"]:
                raise StartTransactionIntegrityError(
                    "start Session binding points to a missing transaction receipt"
                )
        by_run_root = start_transactions_dir(workspace) / "by-run"
        if by_run_root.exists():
            for path in by_run_root.glob("*.json"):
                binding = _read_json_object(path, max_bytes=_MAX_TRANSACTION_JSON_BYTES)
                if str(binding.get("transaction_id") or "") == transaction_id:
                    raise StartTransactionIntegrityError(
                        "start Run binding points to a missing transaction receipt"
                    )
        return None
    expected_key_hash = start_idempotency_key_hash(idempotency_key)
    required = {
        "schema_version": START_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "session_id": session_id,
        "idempotency_key_hash": expected_key_hash,
    }
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in required.items()):
        raise StartTransactionIntegrityError("start transaction receipt identity does not match")
    return receipt


def read_start_transaction_by_id(
    workspace: Path,
    transaction_id: str,
) -> dict | None:
    receipt = _raw_start_transaction_payload(workspace, transaction_id)
    if receipt is None:
        return None
    run_binding = _read_start_run_binding(workspace, str(receipt["run_id"]))
    if (
        str(run_binding.get("transaction_id") or "") != transaction_id
        or str(run_binding.get("session_id") or "") != str(receipt["session_id"])
        or not _receipt_digest_matches_run_binding(receipt, run_binding)
    ):
        raise StartTransactionIntegrityError(
            "start transaction receipt does not match its Run binding"
        )
    session_binding = _read_start_session_binding(workspace, str(receipt["session_id"]))
    if transaction_id not in session_binding["transaction_ids"]:
        raise StartTransactionIntegrityError(
            "start transaction receipt is absent from its Session binding"
        )
    return receipt


def read_start_transaction_for_run(workspace: Path, run_id: str) -> dict | None:
    requested_run_id = safe_run_id(run_id)
    path = start_run_binding_path(workspace, requested_run_id)
    if not path.exists():
        return None
    binding = _read_start_run_binding(workspace, requested_run_id)
    transaction_id = str(binding.get("transaction_id") or "")
    receipt = read_start_transaction_by_id(workspace, transaction_id)
    if (
        receipt is None
        or str(receipt.get("run_id") or "") != requested_run_id
        or str(receipt.get("session_id") or "") != str(binding.get("session_id") or "")
    ):
        raise StartTransactionIntegrityError("start Run binding does not match receipt")
    return receipt


def list_start_transactions_for_session(workspace: Path, session_id: str) -> list[dict]:
    requested_session_id = str(session_id or "")
    path = start_session_binding_path(workspace, requested_session_id)
    if not path.exists():
        return []
    binding = _read_start_session_binding(workspace, requested_session_id)
    transaction_ids = binding["transaction_ids"]
    receipts = []
    for transaction_id in transaction_ids:
        transaction_id = str(transaction_id or "")
        receipt = read_start_transaction_by_id(workspace, transaction_id)
        if receipt is None or str(receipt.get("session_id") or "") != requested_session_id:
            raise StartTransactionIntegrityError("start Session binding does not match receipt")
        receipts.append(receipt)
    return receipts


def write_pending_run(workspace: Path, run: dict) -> dict:
    run_id = safe_run_id(str(run.get("run_id") or ""))
    _write_json_atomic(pending_run_path(workspace, run_id), run)
    return run


def read_pending_run(workspace: Path, run_id: str) -> dict:
    requested_run_id = safe_run_id(run_id)
    path = pending_run_path(workspace, requested_run_id)
    if not path.exists():
        raise FileNotFoundError(run_id)
    data = _read_json_object(path, max_bytes=_MAX_RUN_JSON_BYTES)
    if str(data.get("run_id") or "") != requested_run_id:
        raise StartTransactionIntegrityError("pending run identity does not match filename")
    return data


def publish_pending_run(workspace: Path, run_id: str) -> dict:
    """Publish the validated pending payload without trusting its path again.

    The pending leaf is mutable after ``read_pending_run`` returns. Renaming
    that pathname would let a concurrent replacement (including a symlink) be
    promoted to the canonical Run. Instead, materialize the already validated
    payload through the canonical atomic writer, verify the public copy, and
    only then unlink whatever remains at the private pending name.
    """
    run = read_pending_run(workspace, run_id)
    try:
        canonical = read_run_raw(workspace, run_id)
    except FileNotFoundError:
        _write_json_atomic(run_path(workspace, run_id), run)
        canonical = read_run_raw(workspace, run_id)
    if canonical != run:
        raise StartTransactionIntegrityError(
            "canonical run conflicts with pending run"
        )
    delete_pending_run(workspace, run_id)
    verified = read_run_raw(workspace, run_id)
    if verified != run:
        raise StartTransactionIntegrityError(
            "canonical run changed during pending Run publication"
        )
    return verified


def _unlink_storage_file(path: Path) -> None:
    try:
        with _anchored_storage_parent(path, create=False) as (parent_fd, leaf):
            if parent_fd is None:  # pragma: no cover - Windows fallback
                path.unlink(missing_ok=True)
                if path.parent.exists():
                    _fsync_directory(path.parent)
                return
            try:
                os.unlink(leaf, dir_fd=parent_fd)
            except FileNotFoundError:
                return
            os.fsync(parent_fd)
            _assert_anchored_parent_is_current(path, parent_fd)
    except FileNotFoundError:
        return


def delete_pending_run(workspace: Path, run_id: str) -> None:
    _unlink_storage_file(pending_run_path(workspace, run_id))


def delete_canonical_run(workspace: Path, run_id: str) -> None:
    _unlink_storage_file(run_path(workspace, run_id))


def read_run_raw(workspace: Path, run_id: str) -> dict:
    requested_run_id = safe_run_id(run_id)
    path = run_path(workspace, requested_run_id)
    if not path.exists():
        raise FileNotFoundError(run_id)
    data = _read_json_object(path, max_bytes=_MAX_RUN_JSON_BYTES)
    payload_run_id = str(data.get("run_id") or "").strip()
    if not payload_run_id and int(data.get("schema_version") or 0) < 2:
        data["run_id"] = requested_run_id
        return data
    if payload_run_id != requested_run_id:
        raise ValueError(
            f"Expert team run_id does not match filename: {payload_run_id or 'missing'} != {requested_run_id}"
        )
    return data


def read_run(workspace: Path, run_id: str) -> dict:
    requested_run_id = safe_run_id(run_id)
    run = read_run_raw(workspace, requested_run_id)
    try:
        if _requires_standalone_start_binding(run):
            _validate_public_standalone_run(
                workspace,
                run,
                require_committed=True,
            )
        else:
            # Runs without a binding predate the standalone transaction. If a
            # legacy Run does have one, that binding remains authoritative.
            receipt = read_start_transaction_for_run(workspace, requested_run_id)
            if receipt is not None and receipt.get("state") != "committed":
                raise StartTransactionIntegrityError(
                    "transaction-bound legacy Run is not committed"
                )
    except Exception as exc:
        raise FileNotFoundError(run_id) from exc
    return run


def list_runs(workspace: Path) -> list[dict]:
    root = runs_dir(workspace)
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            rows.append(read_run(workspace, path.stem))
        except Exception:
            continue
    return rows


def latest_run_for_session(workspace: Path, session_id: str) -> dict:
    sid = str(session_id or "").strip()
    for run in list_runs(workspace):
        if str(run.get("session_id") or "").strip() == sid:
            return run
    raise FileNotFoundError(session_id)
