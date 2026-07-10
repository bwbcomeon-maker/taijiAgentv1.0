"""JSON storage for expert team runs."""

from __future__ import annotations

import json
import os
import re
import tempfile
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


def runs_dir(workspace: Path) -> Path:
    return Path(workspace) / ".taiji" / "expert-teams" / "runs"


def safe_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if run_id in {".", ".."} or not run_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("Invalid expert team run_id")
    return run_id


def run_path(workspace: Path, run_id: str) -> Path:
    return runs_dir(workspace) / f"{safe_run_id(run_id)}.json"


def run_lock_path(workspace: Path, run_id: str) -> Path:
    return runs_dir(workspace) / ".locks" / f"{safe_run_id(run_id)}.lock"


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


def read_run(workspace: Path, run_id: str) -> dict:
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
