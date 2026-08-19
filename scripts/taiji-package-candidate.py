#!/usr/bin/python3
"""Thin controller for resumable Taiji x86 Kylin candidate builds."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "packaging/pipeline/targets/kylin-amd64.json"
DEFAULT_STATE_ROOT = Path.home() / ".local/state/taiji-package"
STATE_SCHEMA = "taiji-package-run-state/v1"
TARGET_SCHEMA = "taiji-package-target/v1"
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
TARGET_FIELDS = {
    "architecture",
    "host_alias",
    "minimum_free_gib",
    "minimum_free_inodes",
    "remote_account_home",
    "remote_root",
    "remote_user",
    "schema",
    "target_id",
}


class PipelineError(RuntimeError):
    """A stable, operator-actionable candidate pipeline failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json_object(path: Path, label: str) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError("{} is unreadable or invalid: {}".format(label, exc)) from exc
    if not isinstance(payload, dict):
        raise PipelineError("{} must be a JSON object".format(label))
    return payload


def load_target(path: Path) -> Dict[str, Any]:
    target_path = Path(path).expanduser().resolve()
    payload = _load_json_object(target_path, "target adapter")
    if set(payload) != TARGET_FIELDS:
        raise PipelineError("target adapter fields do not match taiji-package-target/v1")
    if payload.get("schema") != TARGET_SCHEMA:
        raise PipelineError("unsupported target adapter schema")
    if payload.get("target_id") != "kylin-amd64":
        raise PipelineError("this controller accepts only the kylin-amd64 adapter")
    for key in ("host_alias", "remote_user", "remote_account_home", "remote_root"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise PipelineError("target adapter {} is invalid".format(key))
    if payload.get("architecture") != "amd64":
        raise PipelineError("target adapter architecture must be amd64")
    for key in ("minimum_free_gib", "minimum_free_inodes"):
        if type(payload.get(key)) is not int or payload[key] <= 0:
            raise PipelineError("target adapter {} must be a positive integer".format(key))
    if not payload["remote_account_home"].startswith("/"):
        raise PipelineError("remote_account_home must be absolute")
    if not payload["remote_root"].startswith(payload["remote_account_home"] + "/"):
        raise PipelineError("remote_root must be inside the fixed remote account home")
    return payload


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise PipelineError("invalid run-id")


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError("cannot create private state directory {}: {}".format(path, exc)) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise PipelineError("state directory is not current-user private: {}".format(path))


class RunStateStore:
    """No-scan, no-overwrite run-state storage under one explicit root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.runs_root = self.root / "runs"

    def run_dir(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self.runs_root / run_id

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run-state.json"

    def create(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        _validate_run_id(run_id)
        _ensure_private_directory(self.root)
        _ensure_private_directory(self.runs_root)
        run_dir = self.run_dir(run_id)
        try:
            run_dir.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise PipelineError("run already exists: {}".format(run_id)) from exc
        except OSError as exc:
            raise PipelineError("cannot create run {}: {}".format(run_id, exc)) from exc
        now = utc_now()
        state = dict(payload)
        state.update(
            {
                "schema": STATE_SCHEMA,
                "run_id": run_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        self._atomic_write(run_id, state)
        return state

    def load(self, run_id: str) -> Dict[str, Any]:
        path = self.state_path(run_id)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise PipelineError("run state is unavailable: {}".format(run_id)) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise PipelineError("run state file is unsafe: {}".format(path))
        state = _load_json_object(path, "run state")
        if state.get("schema") != STATE_SCHEMA or state.get("run_id") != run_id:
            raise PipelineError("run state identity is invalid")
        return state

    def update(self, run_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        state = self.load(run_id)
        for protected in ("schema", "run_id", "created_at"):
            if protected in changes and changes[protected] != state[protected]:
                raise PipelineError("cannot change protected run state field: {}".format(protected))
        state.update(changes)
        state["updated_at"] = utc_now()
        self._atomic_write(run_id, state)
        return state

    def _atomic_write(self, run_id: str, state: Dict[str, Any]) -> None:
        run_dir = self.run_dir(run_id)
        path = self.state_path(run_id)
        temporary = run_dir / ".run-state.{}.{}.tmp".format(os.getpid(), uuid.uuid4().hex)
        payload = (
            json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        descriptor = -1
        try:
            descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(path))
            directory_descriptor = os.open(str(run_dir), os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise PipelineError("cannot persist run state {}: {}".format(run_id, exc)) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class RealSshTransport:
    """Real SSH/SCP adapter. Behavior is added by the transport task."""


class FakeSshTransport:
    """Deterministic transport adapter for local pipeline tests."""


def local_doctor(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    raise PipelineError("local doctor is not implemented yet")


def build_candidate_plan(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    raise PipelineError("candidate planning is not implemented yet")


def fetch_candidate(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    raise PipelineError("candidate fetch recovery is not implemented yet")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--json", action="store_true", dest="json_output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", allow_abbrev=False)
    doctor.add_argument("--online", action="store_true")
    subparsers.add_parser("plan", allow_abbrev=False)
    subparsers.add_parser("build", allow_abbrev=False)
    status = subparsers.add_parser("status", allow_abbrev=False)
    status.add_argument("--run", required=True, dest="run_id")
    fetch = subparsers.add_parser("fetch", allow_abbrev=False)
    fetch.add_argument("--run", required=True, dest="run_id")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "status":
            payload = RunStateStore(args.state_root).load(args.run_id)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        raise PipelineError("{} is not implemented yet".format(args.command))
    except PipelineError as exc:
        print("BLOCKED\t{}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
