"""JSON storage for expert team runs."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import tempfile
import threading
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


class StartTransactionIntegrityError(ValueError):
    """Raised when a durable start receipt cannot be trusted."""


def runs_dir(workspace: Path) -> Path:
    return Path(workspace) / ".taiji" / "expert-teams" / "runs"


def start_transactions_dir(workspace: Path) -> Path:
    return Path(workspace) / ".taiji" / "expert-teams" / "start-transactions"


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


def _validate_start_session_metadata_snapshot(receipt: dict) -> None:
    snapshot_present = "session_metadata_before_start" in receipt
    digest_present = "session_metadata_before_start_sha256" in receipt
    if not snapshot_present and not digest_present:
        return
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
    if owned_updated_at is not None and (
        isinstance(owned_updated_at, bool)
        or not isinstance(owned_updated_at, (int, float))
        or not math.isfinite(float(owned_updated_at))
        or owned_updated_at < 0
    ):
        raise StartTransactionIntegrityError(
            "start transaction owned Session timestamp is invalid"
        )


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
    return start_transactions_dir(workspace) / "by-run" / f"{safe_run_id(run_id)}.json"


def start_session_binding_path(workspace: Path, session_id: str) -> Path:
    session_id = str(session_id or "")
    if not session_id:
        raise ValueError("Invalid expert team start session_id")
    digest = hashlib.sha256(
        f"expert-team-start-session-index-v1\0{session_id}".encode("utf-8")
    ).hexdigest()
    return start_transactions_dir(workspace) / "by-session" / f"{digest}.json"


def pending_run_path(workspace: Path, run_id: str) -> Path:
    return start_transactions_dir(workspace) / "pending" / f"{safe_run_id(run_id)}.json"


def safe_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if run_id in {".", ".."} or not run_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("Invalid expert team run_id")
    return run_id


def run_path(workspace: Path, run_id: str) -> Path:
    return runs_dir(workspace) / f"{safe_run_id(run_id)}.json"


def run_lock_path(workspace: Path, run_id: str) -> Path:
    return runs_dir(workspace) / ".locks" / f"{safe_run_id(run_id)}.lock"


def start_transaction_lock_path(workspace: Path, transaction_id: str) -> Path:
    if _SHA256_PATTERN.fullmatch(str(transaction_id or "")) is None:
        raise ValueError("Invalid expert team start transaction_id")
    return start_transactions_dir(workspace) / ".locks" / f"{transaction_id}.lock"


def start_session_lock_path(workspace: Path, session_id: str) -> Path:
    session_id = str(session_id or "")
    if not session_id:
        raise ValueError("Invalid expert team start session_id")
    digest = hashlib.sha256(
        f"expert-team-start-session-v1\0{session_id}".encode("utf-8")
    ).hexdigest()
    return start_transactions_dir(workspace) / ".session-locks" / f"{digest}.lock"


@contextmanager
def run_file_lock(workspace: Path, run_id: str):
    """Hold an inter-process exclusive lock for one run's full CAS window."""
    path = run_lock_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        elif msvcrt is not None:  # pragma: no cover - Windows only
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            locked = True
        else:  # Fail closed instead of pretending cross-process CAS is safe.
            raise RuntimeError("No supported OS file-lock implementation is available")
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
def _start_file_lock(path: Path):
    lock_key = str(path.resolve())
    with _START_TRANSACTION_THREAD_LOCKS_GUARD:
        thread_lock = _START_TRANSACTION_THREAD_LOCKS.setdefault(lock_key, threading.Lock())
    with thread_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            elif msvcrt is not None:  # pragma: no cover - Windows only
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                    os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            else:  # pragma: no cover - unsupported platform only
                raise RuntimeError("No supported OS file-lock implementation is available")
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
def start_transaction_lock(workspace: Path, transaction_id: str):
    """Serialize one idempotent start across threads and desktop processes."""
    with _start_file_lock(start_transaction_lock_path(workspace, transaction_id)):
        yield


@contextmanager
def start_session_lock(workspace: Path, session_id: str):
    """Serialize all expert-team starts that mutate one Session transcript."""
    with _start_file_lock(start_session_lock_path(workspace, session_id)):
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
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
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
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def write_run(workspace: Path, run: dict) -> dict:
    run_id = safe_run_id(str(run.get("run_id") or ""))
    path = run_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(run, ensure_ascii=False, indent=2)
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is not None:
            try:
                directory_fd = os.open(path.parent, directory_flag)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return run


def write_start_transaction(workspace: Path, receipt: dict) -> dict:
    transaction_id = str(receipt.get("transaction_id") or "")
    if _SHA256_PATTERN.fullmatch(transaction_id) is None:
        raise ValueError("Invalid expert team start transaction_id")
    run_id = safe_run_id(str(receipt.get("run_id") or ""))
    session_id = str(receipt.get("session_id") or "")
    if not session_id:
        raise ValueError("Invalid expert team start session_id")
    _validate_start_session_metadata_snapshot(receipt)
    _validate_start_transaction_timestamps(receipt)

    # The Run binding is written first so a canonical Run can never become
    # publicly visible before its transaction is known.  The receipt itself is
    # then durable before the Session index: a crash must never leave by-session
    # pointing at a missing receipt that permanently poisons Session reads.
    run_binding = {
        "schema_version": START_TRANSACTION_SCHEMA_VERSION,
        "run_id": run_id,
        "transaction_id": transaction_id,
        "session_id": session_id,
    }
    run_binding_path = start_run_binding_path(workspace, run_id)
    if run_binding_path.exists():
        try:
            existing_binding = json.loads(run_binding_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StartTransactionIntegrityError("start Run binding is unreadable") from exc
        if existing_binding != run_binding:
            raise StartTransactionIntegrityError("start Run binding conflicts with receipt")
    else:
        _write_json_atomic(run_binding_path, run_binding)

    session_binding_path = start_session_binding_path(workspace, session_id)
    transaction_ids: list[str] = []
    if session_binding_path.exists():
        try:
            session_binding = json.loads(session_binding_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StartTransactionIntegrityError("start Session binding is unreadable") from exc
        if (
            not isinstance(session_binding, dict)
            or session_binding.get("schema_version") != START_TRANSACTION_SCHEMA_VERSION
            or session_binding.get("session_id") != session_id
            or not isinstance(session_binding.get("transaction_ids"), list)
        ):
            raise StartTransactionIntegrityError("start Session binding is invalid")
        transaction_ids = [str(value) for value in session_binding["transaction_ids"]]
        if any(_SHA256_PATTERN.fullmatch(value) is None for value in transaction_ids):
            raise StartTransactionIntegrityError("start Session binding contains an invalid transaction")
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
    path = start_transaction_path(workspace, transaction_id)
    if not path.exists():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StartTransactionIntegrityError("start transaction receipt is unreadable") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != START_TRANSACTION_SCHEMA_VERSION
        or receipt.get("transaction_id") != transaction_id
    ):
        raise StartTransactionIntegrityError("start transaction receipt identity does not match")
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
    return receipt


def read_start_transaction_for_run(workspace: Path, run_id: str) -> dict | None:
    requested_run_id = safe_run_id(run_id)
    path = start_run_binding_path(workspace, requested_run_id)
    if not path.exists():
        return None
    try:
        binding = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StartTransactionIntegrityError("start Run binding is unreadable") from exc
    transaction_id = str(binding.get("transaction_id") or "") if isinstance(binding, dict) else ""
    if (
        not isinstance(binding, dict)
        or binding.get("schema_version") != START_TRANSACTION_SCHEMA_VERSION
        or binding.get("run_id") != requested_run_id
        or _SHA256_PATTERN.fullmatch(transaction_id) is None
    ):
        raise StartTransactionIntegrityError("start Run binding is invalid")
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
    try:
        binding = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StartTransactionIntegrityError("start Session binding is unreadable") from exc
    transaction_ids = binding.get("transaction_ids") if isinstance(binding, dict) else None
    if (
        not isinstance(binding, dict)
        or binding.get("schema_version") != START_TRANSACTION_SCHEMA_VERSION
        or binding.get("session_id") != requested_session_id
        or not isinstance(transaction_ids, list)
    ):
        raise StartTransactionIntegrityError("start Session binding is invalid")
    receipts = []
    for transaction_id in transaction_ids:
        transaction_id = str(transaction_id or "")
        if _SHA256_PATTERN.fullmatch(transaction_id) is None:
            raise StartTransactionIntegrityError("start Session binding contains an invalid transaction")
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
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("run_id") or "") != requested_run_id:
        raise StartTransactionIntegrityError("pending run identity does not match filename")
    return data


def publish_pending_run(workspace: Path, run_id: str) -> dict:
    run = read_pending_run(workspace, run_id)
    source = pending_run_path(workspace, run_id)
    target = run_path(workspace, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = read_run_raw(workspace, run_id)
        if existing != run:
            raise StartTransactionIntegrityError("canonical run conflicts with pending run")
        source.unlink(missing_ok=True)
        _fsync_directory(source.parent)
        return existing
    os.replace(source, target)
    _fsync_directory(source.parent)
    _fsync_directory(target.parent)
    return run


def delete_pending_run(workspace: Path, run_id: str) -> None:
    path = pending_run_path(workspace, run_id)
    path.unlink(missing_ok=True)
    if path.parent.exists():
        _fsync_directory(path.parent)


def delete_canonical_run(workspace: Path, run_id: str) -> None:
    path = run_path(workspace, run_id)
    path.unlink(missing_ok=True)
    if path.parent.exists():
        _fsync_directory(path.parent)


def read_run_raw(workspace: Path, run_id: str) -> dict:
    requested_run_id = safe_run_id(run_id)
    path = run_path(workspace, requested_run_id)
    if not path.exists():
        raise FileNotFoundError(run_id)
    data = json.loads(path.read_text(encoding="utf-8"))
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
        receipt = read_start_transaction_for_run(workspace, requested_run_id)
    except Exception as exc:
        raise FileNotFoundError(run_id) from exc
    # Runs without a binding predate the standalone start transaction and keep
    # their legacy visibility.  A transaction-bound Run is public only after
    # its receipt is durably committed.
    if receipt is not None and receipt.get("state") != "committed":
        raise FileNotFoundError(run_id)
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
