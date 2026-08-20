"""Safe run-state storage, locking, and platform-neutral stage helpers."""

import json
import os
import re
import stat
import time
import uuid
from copy import deepcopy
from pathlib import Path

from .errors import PipelineError
from .models import (
    CURRENT_STATE_SCHEMA,
    IDENTITY_KEYS,
    LEGACY_STATE_SCHEMA,
    NULLABLE_IDENTITY_KEYS,
    SHA256_RE,
    validate_v2_state,
    utc_now,
)


RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
FROZEN_PATHS = (
    "schema", "run_id", "target_id", "target_config", "target_config_sha256",
    "created_at", "source.repo_root", "source.branch", "source.commit",
    "source.tree", "identity.controller_commit", "host.alias",
    "host.remote_run_dir", "paths.local_run_dir",
)


def _validate_run_id(run_id):
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise PipelineError("invalid run-id", category="PLAN_INVALID")


def _load_json_object(path, label):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError("{} is unreadable or invalid: {}".format(label, exc), category="PLAN_INVALID") from exc
    if not isinstance(payload, dict):
        raise PipelineError("{} must be a JSON object".format(label), category="PLAN_INVALID")
    return payload


def _ensure_private_directory(path):
    path = Path(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError("cannot create private state directory {}: {}".format(path, exc), category="STATE_WRITE_FAILED") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise PipelineError("state directory is not current-user private: {}".format(path), category="STATE_WRITE_FAILED")


def _require_private_directory(path):
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError("private state directory is unavailable: {}".format(path), category="STATE_WRITE_FAILED") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise PipelineError("state directory is not current-user private: {}".format(path), category="STATE_WRITE_FAILED")


def _deep_merge(original, changes):
    result = deepcopy(original)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _get_path(payload, dotted):
    current = payload
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_path(payload, dotted, value):
    parts = dotted.split(".")
    current = payload
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = deepcopy(value)


def _without_plan_input(plan):
    result = deepcopy(plan)
    if isinstance(result, dict):
        result.pop("input", None)
    return result


def _validate_nullable_identity_changes(before, after):
    for key in IDENTITY_KEYS:
        old_value = before["identity"].get(key)
        new_value = after["identity"].get(key)
        if key == "controller_commit":
            if old_value != new_value:
                raise PipelineError("frozen identity changed: {}".format(key), category="PLAN_INVALID")
            continue
        if old_value is None:
            if new_value is not None and SHA256_RE.fullmatch(str(new_value)) is None:
                raise PipelineError("identity {} is invalid".format(key), category="PLAN_INVALID")
        elif new_value != old_value:
            raise PipelineError("frozen identity changed: {}".format(key), category="PLAN_INVALID")


class RunStateStore:
    """No-scan, no-overwrite run-state storage under one explicit root."""

    def __init__(self, root):
        self.root = Path(os.path.abspath(os.path.expanduser(str(root))))
        self.runs_root = self.root / "runs"

    def run_dir(self, run_id):
        _validate_run_id(run_id)
        return self.runs_root / run_id

    def state_path(self, run_id):
        return self.run_dir(run_id) / "run-state.json"

    def create(self, run_id, payload):
        _validate_run_id(run_id)
        if not isinstance(payload, dict) or payload.get("schema") != CURRENT_STATE_SCHEMA:
            raise PipelineError("new run state must use complete v2 schema", category="PLAN_INVALID")
        if payload.get("run_id") != run_id:
            raise PipelineError("run state run id does not match path", category="PLAN_INVALID")
        validate_v2_state(payload)
        _ensure_private_directory(self.root)
        _ensure_private_directory(self.runs_root)
        run_dir = self.run_dir(run_id)
        try:
            run_dir.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise PipelineError("run already exists: {}".format(run_id), category="RUN_LOCKED") from exc
        except OSError as exc:
            raise PipelineError("cannot create run {}: {}".format(run_id, exc), category="STATE_WRITE_FAILED") from exc
        state = deepcopy(payload)
        try:
            self._atomic_write(run_id, state)
        except PipelineError:
            raise
        except OSError as exc:
            raise PipelineError("cannot persist run state {}: {}".format(run_id, exc), category="STATE_WRITE_FAILED") from exc
        return state

    def load(self, run_id):
        _validate_run_id(run_id)
        _require_private_directory(self.root)
        _require_private_directory(self.runs_root)
        _require_private_directory(self.run_dir(run_id))
        path = self.state_path(run_id)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise PipelineError("run state is unavailable: {}".format(run_id), category="STATE_WRITE_FAILED") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise PipelineError("run state file is unsafe: {}".format(path), category="STATE_WRITE_FAILED")
        state = _load_json_object(path, "run state")
        if state.get("schema") not in (LEGACY_STATE_SCHEMA, CURRENT_STATE_SCHEMA) or state.get("run_id") != run_id:
            raise PipelineError("run state identity is invalid", category="PLAN_INVALID")
        if state.get("schema") == CURRENT_STATE_SCHEMA:
            validate_v2_state(state)
        return state

    def update(self, run_id, changes):
        if not isinstance(changes, dict):
            raise PipelineError("run state changes must be an object", category="PLAN_INVALID")
        state = self.load(run_id)
        prospective = _deep_merge(state, changes)
        if state.get("schema") == LEGACY_STATE_SCHEMA:
            if prospective.get("schema") != LEGACY_STATE_SCHEMA:
                raise PipelineError("legacy state schema cannot be upgraded", category="PLAN_INVALID")
            if any(key in prospective for key in ("target_config", "target_config_sha256", "source", "identity", "host", "paths")):
                raise PipelineError("legacy state cannot be upgraded in place", category="PLAN_INVALID")
        else:
            for path in FROZEN_PATHS:
                if _get_path(state, path) != _get_path(prospective, path):
                    raise PipelineError("frozen run state path changed: {}".format(path), category="PLAN_INVALID")
            if _without_plan_input(state.get("plan")) != _without_plan_input(prospective.get("plan")):
                raise PipelineError("frozen execution plan changed", category="PLAN_INVALID")
            _validate_nullable_identity_changes(state, prospective)
            if state.get("stage") != "PLANNED":
                if prospective.get("input") != state.get("input") or prospective.get("plan", {}).get("input") != state.get("plan", {}).get("input"):
                    raise PipelineError("input is frozen after verification", category="PLAN_INVALID")
            if prospective.get("input") != prospective.get("plan", {}).get("input"):
                raise PipelineError("state and execution plan input differ", category="PLAN_INVALID")
            validate_v2_state(prospective)
        prospective["updated_at"] = utc_now()
        self._atomic_write(run_id, prospective)
        return prospective

    def bind_verified_input(self, run_id, inspected_input, manifest_sha256):
        state = self.load(run_id)
        if state.get("schema") != CURRENT_STATE_SCHEMA:
            raise PipelineError("only v2 state can bind verified input", category="PLAN_INVALID")
        if state.get("stage") != "PLANNED":
            raise PipelineError("verified input can only bind during PLANNED", category="PLAN_INVALID")
        if not isinstance(inspected_input, dict) or inspected_input.get("status") != "REUSABLE":
            raise PipelineError("verified input is not reusable", category="INPUT_VERIFICATION_FAILED")
        if not isinstance(manifest_sha256, str) or SHA256_RE.fullmatch(manifest_sha256) is None:
            raise PipelineError("input manifest identity is invalid", category="PLAN_INVALID")
        input_files = inspected_input.get("files")
        if not isinstance(input_files, dict) or set(input_files) != {"archive", "manifest", "checksum"}:
            raise PipelineError("verified input triplet is incomplete", category="INPUT_VERIFICATION_FAILED")
        if input_files["manifest"].get("sha256") != manifest_sha256:
            raise PipelineError("input manifest identity does not match files", category="PLAN_INVALID")
        current_input = state["input"]
        if current_input.get("status") == "REUSABLE":
            if current_input != inspected_input or state["identity"].get("input_manifest_sha256") != manifest_sha256:
                raise PipelineError("verified input identity changed", category="PLAN_INVALID")
            return deepcopy(state)
        if current_input.get("status") != "MISSING" or state["plan"].get("input") != current_input:
            raise PipelineError("state input is not bindable", category="PLAN_INVALID")
        prospective = deepcopy(state)
        prospective["input"] = deepcopy(inspected_input)
        prospective["plan"]["input"] = deepcopy(inspected_input)
        prospective["identity"]["input_manifest_sha256"] = manifest_sha256
        validate_v2_state(prospective)
        prospective["updated_at"] = utc_now()
        try:
            self._atomic_write(run_id, prospective)
        except PipelineError:
            raise
        except OSError as exc:
            raise PipelineError("cannot persist verified input: {}".format(exc), category="STATE_WRITE_FAILED") from exc
        return prospective

    def _atomic_write(self, run_id, state):
        run_dir = self.run_dir(run_id)
        path = self.state_path(run_id)
        temporary = run_dir / ".run-state.{}.{}.tmp".format(os.getpid(), uuid.uuid4().hex)
        payload = (json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
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
            raise PipelineError("cannot persist run state {}: {}".format(run_id, exc), category="STATE_WRITE_FAILED") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class RunLock:
    """Exclusive per-run lock with a token-bound, no-stale-repair contract."""

    def __init__(self, store, run_id):
        self.store = store
        self.run_id = run_id
        self.path = store.run_dir(run_id) / "run.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self):
        payload = (json.dumps({"pid": os.getpid(), "token": self.token, "acquired_at": utc_now()}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            descriptor = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise PipelineError("run is already locked: {}".format(self.run_id), category="RUN_LOCKED") from exc
        except OSError as exc:
            raise PipelineError("cannot acquire run lock: {}".format(exc), category="RUN_LOCK_FAILED") from exc
        self.acquired = True
        try:
            self.store.update(self.run_id, {"lock": {"status": "held", "pid": os.getpid(), "token": self.token, "acquired_at": utc_now()}})
        except Exception:
            self.path.unlink(missing_ok=True)
            self.acquired = False
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        if not self.acquired:
            return
        try:
            metadata = self.path.lstat()
            payload = _load_json_object(self.path, "run lock")
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or payload.get("token") != self.token
            ):
                raise PipelineError("run lock identity changed", category="RUN_LOCK_FAILED")
            self.path.unlink()
            self.store.update(self.run_id, {"lock": {"status": "released", "released_at": utc_now()}})
        finally:
            self.acquired = False


def controller_log(store, run_id, message):
    path = store.run_dir(run_id) / "controller.log"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(("{}\t{}\n".format(utc_now(), message)).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError("cannot write controller log: {}".format(exc), category="STATE_WRITE_FAILED") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise PipelineError("controller log is unsafe", category="STATE_WRITE_FAILED")


def recorded_stage(store, run_id, stage, callback):
    started_at = utc_now()
    started = time.monotonic()
    store.update(run_id, {"stage": stage, "stage_started_at": started_at})
    controller_log(store, run_id, "stage-start\t{}".format(stage))
    try:
        result = callback()
    except PipelineError as exc:
        history = list(store.load(run_id).get("stage_history", []))
        history.append({"stage": stage, "status": "failed", "started_at": started_at, "ended_at": utc_now(), "duration_seconds": max(0.0, time.monotonic() - started), "failure_category": exc.category})
        store.update(run_id, {"stage_history": history})
        controller_log(store, run_id, "stage-fail\t{}\t{}".format(stage, exc.category))
        raise
    history = list(store.load(run_id).get("stage_history", []))
    history.append({"stage": stage, "status": "passed", "started_at": started_at, "ended_at": utc_now(), "duration_seconds": max(0.0, time.monotonic() - started)})
    store.update(run_id, {"stage": stage, "stage_history": history})
    controller_log(store, run_id, "stage-pass\t{}".format(stage))
    return result
