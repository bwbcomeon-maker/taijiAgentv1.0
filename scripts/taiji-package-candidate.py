#!/usr/bin/python3
"""Thin controller for resumable Taiji x86 Kylin candidate builds."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import posixpath
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import packaging.pipeline as _pipeline_package
from packaging.pipeline.core.errors import PipelineError
from packaging.pipeline.core.models import new_run_state


_EXPECTED_PIPELINE = (_REPO_ROOT / "packaging/pipeline/__init__.py").resolve()
_ACTUAL_PIPELINE = Path(_pipeline_package.__file__).resolve()
if _ACTUAL_PIPELINE != _EXPECTED_PIPELINE:
    raise RuntimeError(
        "unexpected packaging.pipeline origin: {}".format(_ACTUAL_PIPELINE)
    )


ROOT = _REPO_ROOT
DEFAULT_TARGET = ROOT / "packaging/pipeline/targets/kylin-amd64.json"
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_STATE_ROOT = ACCOUNT_HOME / ".local/state/taiji-package"
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
GIT_LOCATION_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
REQUIRED_INTERFACE_PATH = Path("packaging/linux/taiji-packaging-interface.json")
REQUIRED_PREFLIGHT_PATH = Path("taijiagent 打包交付/01_制包机_发布预检.sh")


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
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", payload["host_alias"]) is None:
        raise PipelineError("target adapter host_alias is not a safe SSH alias")
    if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", payload["remote_user"]) is None:
        raise PipelineError("target adapter remote_user is invalid")
    if payload.get("architecture") != "amd64":
        raise PipelineError("target adapter architecture must be amd64")
    for key in ("minimum_free_gib", "minimum_free_inodes"):
        if type(payload.get(key)) is not int or payload[key] <= 0:
            raise PipelineError("target adapter {} must be a positive integer".format(key))
    for key in ("remote_account_home", "remote_root"):
        value = payload[key]
        if (
            re.fullmatch(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+", value) is None
            or posixpath.normpath(value) != value
        ):
            raise PipelineError("target adapter {} must be a normalized absolute path".format(key))
    if (
        payload["remote_root"] == payload["remote_account_home"]
        or posixpath.commonpath(
            [payload["remote_account_home"], payload["remote_root"]]
        )
        != payload["remote_account_home"]
    ):
        raise PipelineError("remote_root must be inside the fixed remote account home")
    return payload


def _command_environment(*, include_home: bool = False) -> Dict[str, str]:
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if include_home:
        environment["HOME"] = str(ACCOUNT_HOME)
    return environment


def _git_environment() -> Dict[str, str]:
    environment = _command_environment()
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    for name in GIT_LOCATION_VARIABLES:
        environment.pop(name, None)
    return environment


def _run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Optional[Dict[str, str]] = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=environment or _command_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PipelineError("command could not run: {}: {}".format(argv[0], exc)) from exc


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess:
    return _run_command(
        ["/usr/bin/git", "-C", str(repo), *arguments],
        cwd=repo,
        environment=_git_environment(),
    )


def _path_writable_without_creation(path: Path) -> bool:
    candidate = Path(path).expanduser()
    while not candidate.exists() and not candidate.is_symlink():
        parent = candidate.parent
        if parent == candidate:
            return False
        candidate = parent
    try:
        metadata = candidate.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return False
    return os.access(str(candidate), os.W_OK | os.X_OK)


def _load_packaging_interface(repo: Path) -> Dict[str, Any]:
    interface = _load_json_object(repo / REQUIRED_INTERFACE_PATH, "packaging interface")
    if (
        interface.get("schema") != "taiji-packaging-interface/v1"
        or interface.get("repository_id") != "taiji-agentv1.0"
        or not isinstance(interface.get("builder_input_entry"), str)
        or not isinstance(interface.get("build_host_entry"), str)
    ):
        raise PipelineError("packaging interface identity is invalid")
    return interface


def _effective_ssh_config(
    host_alias: str, ssh_config: Optional[Path]
) -> subprocess.CompletedProcess:
    argv = ["/usr/bin/ssh", "-G"]
    if ssh_config is not None:
        argv.extend(["-F", str(Path(ssh_config).expanduser().resolve())])
    argv.append(host_alias)
    return _run_command(
        argv,
        cwd=ACCOUNT_HOME,
        environment=_command_environment(include_home=True),
    )


def _parse_ssh_effective(stdout: str) -> Dict[str, str]:
    effective = {}  # type: Dict[str, str]
    for raw_line in stdout.splitlines():
        key, separator, value = raw_line.partition(" ")
        if separator and key not in effective:
            effective[key] = value.strip()
    return effective


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


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError("private state directory is unavailable: {}".format(path)) from exc
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
        self.root = Path(os.path.abspath(os.path.expanduser(str(root))))
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
        _validate_run_id(run_id)
        _require_private_directory(self.root)
        _require_private_directory(self.runs_root)
        _require_private_directory(self.run_dir(run_id))
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


class RunLock:
    """Exclusive per-run lock with a token-bound, no-stale-repair contract."""

    def __init__(self, store: RunStateStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id
        self.path = store.run_dir(run_id) / "run.lock"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def __enter__(self) -> "RunLock":
        payload = (
            json.dumps(
                {"pid": os.getpid(), "token": self.token, "acquired_at": utc_now()},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        try:
            descriptor = os.open(
                str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise PipelineError(
                "run is already locked: {}".format(self.run_id), category="RUN_LOCKED"
            ) from exc
        except OSError as exc:
            raise PipelineError(
                "cannot acquire run lock: {}".format(exc), category="RUN_LOCK_FAILED"
            ) from exc
        self.acquired = True
        self.store.update(
            self.run_id,
            {
                "lock": {
                    "status": "held",
                    "pid": os.getpid(),
                    "token": self.token,
                    "acquired_at": utc_now(),
                }
            },
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
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
            self.store.update(
                self.run_id,
                {"lock": {"status": "released", "released_at": utc_now()}},
            )
        finally:
            self.acquired = False


def _controller_log(store: RunStateStore, run_id: str, message: str) -> None:
    path = store.run_dir(run_id) / "controller.log"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    line = "{}\t{}\n".format(utc_now(), message).encode("utf-8")
    try:
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError(
            "cannot write controller log: {}".format(exc), category="STATE_WRITE_FAILED"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise PipelineError("controller log is unsafe", category="STATE_WRITE_FAILED")


def _recorded_stage(
    store: RunStateStore, run_id: str, stage: str, callback: Any
) -> Any:
    started_at = utc_now()
    started = time.monotonic()
    store.update(run_id, {"stage": stage})
    _controller_log(store, run_id, "stage-start\t{}".format(stage))
    try:
        result = callback()
    except PipelineError as exc:
        duration = max(0.0, time.monotonic() - started)
        state = store.load(run_id)
        history = list(state.get("stage_history", []))
        history.append(
            {
                "stage": stage,
                "status": "failed",
                "started_at": started_at,
                "ended_at": utc_now(),
                "duration_seconds": duration,
                "failure_category": exc.category,
            }
        )
        store.update(run_id, {"stage_history": history})
        _controller_log(
            store, run_id, "stage-fail\t{}\t{}".format(stage, exc.category)
        )
        raise
    duration = max(0.0, time.monotonic() - started)
    state = store.load(run_id)
    history = list(state.get("stage_history", []))
    history.append(
        {
            "stage": stage,
            "status": "passed",
            "started_at": started_at,
            "ended_at": utc_now(),
            "duration_seconds": duration,
        }
    )
    store.update(run_id, {"stage": stage, "stage_history": history})
    _controller_log(store, run_id, "stage-pass\t{}".format(stage))
    return result


def _ssh_environment() -> Dict[str, str]:
    environment = _command_environment(include_home=True)
    socket_path = os.environ.get("SSH_AUTH_SOCK")
    if socket_path:
        try:
            metadata = os.lstat(socket_path)
        except OSError:
            metadata = None
        if (
            metadata is not None
            and stat.S_ISSOCK(metadata.st_mode)
            and metadata.st_uid == os.getuid()
            and not (stat.S_IMODE(metadata.st_mode) & 0o022)
        ):
            environment["SSH_AUTH_SOCK"] = socket_path
    return environment


def _ssh_prefix(target: Dict[str, Any], ssh_config: Optional[Path]) -> list:
    argv = [
        "/usr/bin/ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
    ]
    if ssh_config is not None:
        argv.extend(["-F", str(Path(ssh_config).expanduser().resolve())])
    argv.append(target["host_alias"])
    return argv


def _scp_prefix(ssh_config: Optional[Path]) -> list:
    argv = [
        "/usr/bin/scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
    ]
    if ssh_config is not None:
        argv.extend(["-F", str(Path(ssh_config).expanduser().resolve())])
    return argv


def _plan_command(plan: Dict[str, Any], stage: str) -> list:
    matches = [command for command in plan.get("commands", []) if command.get("stage") == stage]
    if len(matches) != 1 or not isinstance(matches[0].get("argv"), list):
        raise PipelineError(
            "candidate plan must contain exactly one {} command".format(stage),
            category="PLAN_INVALID",
        )
    return list(matches[0]["argv"])


def _online_doctor_script(target: Dict[str, Any]) -> str:
    remote_root = target["remote_root"]
    account_home = target["remote_account_home"]
    python_probe = (
        "import fcntl,os; "
        "fd=os.memfd_create('taiji-doctor', os.MFD_ALLOW_SEALING); "
        "os.write(fd,b'x'); "
        "fcntl.fcntl(fd,fcntl.F_ADD_SEALS,"
        "fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL); "
        "os.close(fd)"
    )
    statements = [
        "set -u",
        "printf 'schema=taiji-online-doctor-v1\\n'",
        "kernel=$(/usr/bin/uname -s 2>/dev/null || :)",
        "machine=$(/usr/bin/uname -m 2>/dev/null || :)",
        "dpkg_arch=$(/usr/bin/dpkg --print-architecture 2>/dev/null || :)",
        "apt=''; [ -x /usr/bin/apt-get ] && apt=/usr/bin/apt-get",
        "dpkg=''; [ -x /usr/bin/dpkg ] && dpkg=/usr/bin/dpkg",
        (
            "glibc=''; if [ -x /usr/bin/ldd ]; then "
            "glibc=$(/usr/bin/ldd --version 2>&1 | /usr/bin/sed -n '1p'); fi"
        ),
        (
            "sudo_status=blocked; if [ -x /usr/bin/sudo ] "
            "&& /usr/bin/sudo -n /usr/bin/true >/dev/null 2>&1; "
            "then sudo_status=ready; fi"
        ),
        "capacity_path={}".format(shlex.quote(account_home)),
        (
            "if [ -d {root} ] && [ ! -L {root} ]; then capacity_path={root}; fi"
        ).format(root=shlex.quote(remote_root)),
        "free_kib=$(/bin/df -Pk \"$capacity_path\" 2>/dev/null | /usr/bin/awk 'NR==2 {print $4}' || :)",
        "free_inodes=$(/bin/df -Pi \"$capacity_path\" 2>/dev/null | /usr/bin/awk 'NR==2 {print $4}' || :)",
        "case \"$free_kib\" in ''|*[!0-9]*) free_kib=0 ;; esac",
        "case \"$free_inodes\" in ''|*[!0-9]*) free_inodes=0 ;; esac",
        "printf 'kernel=%s\\n' \"$kernel\"",
        "printf 'machine=%s\\n' \"$machine\"",
        "printf 'dpkg_arch=%s\\n' \"$dpkg_arch\"",
        "printf 'apt=%s\\n' \"$apt\"",
        "printf 'dpkg=%s\\n' \"$dpkg\"",
        "printf 'glibc=%s\\n' \"$glibc\"",
        "printf 'sudo=%s\\n' \"$sudo_status\"",
        "printf 'free_kib=%s\\n' \"$free_kib\"",
        "printf 'free_inodes=%s\\n' \"$free_inodes\"",
        (
            "proc_status=blocked; if [ -d /proc/self/fd ] && [ -r /proc/self/fd/0 ]; "
            "then proc_status=ready; fi; printf 'proc=%s\\n' \"$proc_status\""
        ),
        (
            "memfd_status=blocked; if [ -x /usr/bin/python3 ] "
            "&& /usr/bin/python3 -I -B -c {probe} >/dev/null 2>&1; "
            "then memfd_status=ready; fi; printf 'memfd=%s\\n' \"$memfd_status\""
        ).format(probe=shlex.quote(python_probe)),
        "remote_candidate={}".format(shlex.quote(account_home)),
        (
            "if [ -e {root} ] || [ -L {root} ]; then remote_candidate={root}; fi"
        ).format(root=shlex.quote(remote_root)),
        (
            "remote_root_status=blocked; if [ -d \"$remote_candidate\" ] "
            "&& [ ! -L \"$remote_candidate\" ] && [ -O \"$remote_candidate\" ] "
            "&& [ -w \"$remote_candidate\" ]; then "
            "remote_mode=$(/usr/bin/stat -c '%a' \"$remote_candidate\" 2>/dev/null || :); "
            "if [[ \"$remote_mode\" =~ ^[0-7]+$ ]] "
            "&& (( (8#$remote_mode & 0022) == 0 )); then remote_root_status=ready; fi; fi; "
            "printf 'remote_root=%s\\n' \"$remote_root_status\""
        ),
    ]
    return "; ".join(statements)


def _version_tuple(value: str) -> tuple:
    return tuple(int(part) for part in value.split("."))


def _parse_online_payload(
    stdout: str, target: Dict[str, Any], minimum_glibc: str
) -> Dict[str, Any]:
    values = {}  # type: Dict[str, str]
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in values:
            values[key] = value
    required = {
        "schema",
        "kernel",
        "machine",
        "dpkg_arch",
        "apt",
        "dpkg",
        "glibc",
        "sudo",
        "free_kib",
        "free_inodes",
        "proc",
        "memfd",
        "remote_root",
    }
    blockers = []  # type: list
    if set(values) != required or values.get("schema") != "taiji-online-doctor-v1":
        blockers.append("remote doctor output schema is incomplete")
    if values.get("kernel") != "Linux":
        blockers.append("builder kernel must be Linux")
    if values.get("machine") not in {"x86_64", "amd64"}:
        blockers.append("builder machine must be x86_64")
    if values.get("dpkg_arch") != "amd64":
        blockers.append("dpkg architecture must be amd64")
    for key in ("apt", "dpkg"):
        if not values.get(key):
            blockers.append("remote capability {} is missing".format(key))
    glibc_matches = re.findall(r"(?<![0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)*)(?![0-9])", values.get("glibc", ""))
    glibc_version = glibc_matches[-1] if glibc_matches else ""
    if not glibc_version:
        blockers.append("remote capability glibc is missing")
    elif _version_tuple(glibc_version) < _version_tuple(minimum_glibc):
        blockers.append(
            "remote glibc {} is below policy minimum {}".format(
                glibc_version, minimum_glibc
            )
        )
    for key in ("sudo", "proc", "memfd", "remote_root"):
        if values.get(key) != "ready":
            blockers.append("remote capability {} is not ready".format(key))
    try:
        free_kib = int(values.get("free_kib", ""))
        free_inodes = int(values.get("free_inodes", ""))
    except ValueError:
        free_kib = 0
        free_inodes = 0
        blockers.append("remote capacity values are invalid")
    if free_kib < target["minimum_free_gib"] * 1024 * 1024:
        blockers.append("remote free space is below {} GiB".format(target["minimum_free_gib"]))
    if free_inodes < target["minimum_free_inodes"]:
        blockers.append("remote free inode count is below {}".format(target["minimum_free_inodes"]))
    return {
        "schema": "taiji-package-online-doctor/v1",
        "online_checked": True,
        "builder_status": "BUILDER_READY" if not blockers else "BLOCKED",
        "host_alias": target["host_alias"],
        "architecture": values.get("dpkg_arch", ""),
        "machine": values.get("machine", ""),
        "glibc": values.get("glibc", ""),
        "glibc_version": glibc_version,
        "minimum_glibc": minimum_glibc,
        "free_kib": free_kib,
        "free_inodes": free_inodes,
        "blockers": blockers,
    }


class RealSshTransport:
    """Real SSH/SCP adapter using fixed argv and an optional explicit SSH config."""

    def __init__(
        self,
        repo: Path,
        target: Dict[str, Any],
        *,
        ssh_config: Optional[Path] = None,
        command_runner: Any = _run_command,
    ) -> None:
        self.repo = Path(repo).expanduser().resolve()
        self.target = dict(target)
        self.ssh_config = Path(ssh_config).expanduser().resolve() if ssh_config else None
        self.command_runner = command_runner
        self.minimum_glibc = _canonical_policy_minimum_glibc(self.repo)

    def _execute(self, argv: Sequence[str], category: str, timeout: int) -> None:
        result = self.command_runner(
            list(argv),
            cwd=self.repo,
            environment=_ssh_environment(),
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "command returned non-zero"
            raise PipelineError("{}: {}".format(category, detail), category=category)

    def online_doctor(self) -> Dict[str, Any]:
        argv = _ssh_prefix(self.target, self.ssh_config)
        argv.append(
            _remote_clean_shell(
                self.target["remote_account_home"], _online_doctor_script(self.target)
            )
        )
        result = self.command_runner(
            argv,
            cwd=self.repo,
            environment=_ssh_environment(),
            timeout=20,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "SSH connection or remote capability probe failed"
            return {
                "schema": "taiji-package-online-doctor/v1",
                "online_checked": True,
                "builder_status": "BUILDER_UNREACHABLE",
                "host_alias": self.target["host_alias"],
                "blockers": [detail],
            }
        return _parse_online_payload(result.stdout, self.target, self.minimum_glibc)

    def create_remote_run(self, plan: Dict[str, Any]) -> None:
        self._execute(_plan_command(plan, "create-remote-run"), "SSH_FAILED", 30)

    def transfer_input(self, plan: Dict[str, Any]) -> None:
        self._execute(_plan_command(plan, "transfer-input"), "SCP_INTERRUPTED", 3600)

    def verify_remote_input(self, plan: Dict[str, Any]) -> None:
        self._execute(_plan_command(plan, "remote-input-verify"), "REMOTE_VERIFY_FAILED", 300)

    def build_remote_candidate(self, plan: Dict[str, Any]) -> None:
        self._execute(
            _plan_command(plan, "remote-candidate-build"), "REMOTE_BUILD_FAILED", 14400
        )

    def fetch(self, plan: Dict[str, Any], staging_dir: Path) -> Dict[str, str]:
        staging = _create_private_directory_exclusive(Path(staging_dir))
        review = staging / "review"
        remote_log = staging / "remote-build.log"
        review_argv = _scp_prefix(self.ssh_config) + [
            "-r",
            "{}:{}/review/taiji-agentv1.0".format(
                self.target["host_alias"], plan["remote_run_dir"]
            ),
            str(review),
        ]
        log_argv = _scp_prefix(self.ssh_config) + [
            "{}:{}/remote-build.log".format(
                self.target["host_alias"], plan["remote_run_dir"]
            ),
            str(remote_log),
        ]
        self._execute(review_argv, "SCP_INTERRUPTED", 3600)
        self._execute(log_argv, "SCP_INTERRUPTED", 600)
        return {"review_path": str(review), "remote_log_path": str(remote_log)}


class FakeSshTransport:
    """Deterministic in-process transport used only by local pipeline tests."""

    FAILURE_CATEGORIES = {
        "create-remote-run": "SSH_FAILED",
        "transfer-input": "SCP_INTERRUPTED",
        "remote-input-verify": "REMOTE_VERIFY_FAILED",
        "build-00": "BUILD_00_FAILED",
        "build-01": "BUILD_01_FAILED",
        "fetch-review": "SCP_INTERRUPTED",
        "fetch-log": "SCP_INTERRUPTED",
    }

    def __init__(
        self,
        *,
        builder_status: str = "BUILDER_READY",
        fail_stage: Optional[str] = None,
        review_source: Optional[Path] = None,
    ) -> None:
        self.builder_status = builder_status
        self.fail_stage = fail_stage
        self.review_source = Path(review_source) if review_source else None
        self.calls = []  # type: list

    def _record(self, stage: str) -> None:
        self.calls.append(stage)
        if self.fail_stage == stage:
            raise PipelineError(
                "fake transport failed at {}".format(stage),
                category=self.FAILURE_CATEGORIES[stage],
            )

    def online_doctor(self) -> Dict[str, Any]:
        self.calls.append("online-doctor")
        return {
            "schema": "taiji-package-online-doctor/v1",
            "online_checked": True,
            "builder_status": self.builder_status,
            "host_alias": "kylin",
            "architecture": "amd64" if self.builder_status == "BUILDER_READY" else "",
            "free_kib": 20 * 1024 * 1024,
            "free_inodes": 200000,
            "blockers": [] if self.builder_status == "BUILDER_READY" else ["unreachable"],
        }

    def create_remote_run(self, plan: Dict[str, Any]) -> None:
        del plan
        self._record("create-remote-run")

    def transfer_input(self, plan: Dict[str, Any]) -> None:
        del plan
        self._record("transfer-input")

    def verify_remote_input(self, plan: Dict[str, Any]) -> None:
        del plan
        self._record("remote-input-verify")

    def build_remote_candidate(self, plan: Dict[str, Any]) -> None:
        del plan
        self.calls.append("remote-candidate-build")
        if self.fail_stage in {"build-00", "build-01"}:
            stage = str(self.fail_stage)
            raise PipelineError(
                "fake transport failed at {}".format(stage),
                category=self.FAILURE_CATEGORIES[stage],
            )

    def fetch(self, plan: Dict[str, Any], staging_dir: Path) -> Dict[str, str]:
        del plan
        self._record("fetch-review")
        staging = _create_private_directory_exclusive(Path(staging_dir))
        review = staging / "review"
        if self.review_source is None:
            review.mkdir(mode=0o700)
        else:
            shutil.copytree(str(self.review_source), str(review), symlinks=True)
        self._record("fetch-log")
        remote_log = staging / "remote-build.log"
        remote_log.write_text("fake remote candidate build completed\n", encoding="utf-8")
        remote_log.chmod(0o600)
        return {"review_path": str(review), "remote_log_path": str(remote_log)}


def _create_private_directory_exclusive(path: Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        candidate.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise PipelineError(
            "local fetch staging already exists: {}".format(candidate),
            category="LOCAL_OUTPUT_OCCUPIED",
        ) from exc
    except OSError as exc:
        raise PipelineError(
            "cannot create local fetch staging {}: {}".format(candidate, exc),
            category="LOCAL_OUTPUT_UNWRITABLE",
        ) from exc
    metadata = candidate.lstat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise PipelineError(
            "local fetch staging is not current-user private",
            category="LOCAL_OUTPUT_UNWRITABLE",
        )
    return candidate.resolve()


def execute_candidate_transport(
    plan: Dict[str, Any],
    transport: Any,
    staging_dir: Path,
    *,
    confirmed: bool,
    prepare_input: Optional[Any] = None,
) -> Dict[str, Any]:
    online = transport.online_doctor()
    if online.get("builder_status") != "BUILDER_READY":
        status = str(online.get("builder_status", "BLOCKED"))
        category = "BUILDER_UNREACHABLE" if status == "BUILDER_UNREACHABLE" else "ONLINE_DOCTOR_BLOCKED"
        raise PipelineError(
            "online doctor did not report BUILDER_READY: {}".format(status),
            category=category,
        )
    if not confirmed:
        raise PipelineError(
            "candidate build requires one explicit confirmation after the displayed plan",
            category="CONFIRMATION_REQUIRED",
        )
    input_status = plan.get("input", {}).get("status")
    if input_status == "MISSING":
        if prepare_input is None:
            raise PipelineError(
                "builder input preparation callback is required",
                category="INPUT_PREPARATION_REQUIRED",
            )
        prepare_input()
    elif input_status != "REUSABLE":
        raise PipelineError("candidate plan input is not reusable", category="PLAN_INVALID")
    transport.create_remote_run(plan)
    transport.transfer_input(plan)
    transport.verify_remote_input(plan)
    transport.build_remote_candidate(plan)
    fetched = transport.fetch(plan, staging_dir)
    return {
        "online_doctor": online,
        "remote_build_succeeded": True,
        "review_path": fetched["review_path"],
        "remote_log_path": fetched["remote_log_path"],
    }


def _run_builder_input_preparer(
    plan: Dict[str, Any], command_runner: Any = _run_command
) -> None:
    argv = _plan_command(plan, "prepare-input")
    repo = Path(plan["repo_root"])
    result = command_runner(
        argv,
        cwd=repo,
        environment=_command_environment(),
        timeout=7200,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "99 returned non-zero"
        raise PipelineError(
            "builder input preparation failed: {}".format(detail),
            category="INPUT_PREPARATION_FAILED",
        )


def _final_output_paths(store: RunStateStore, run_id: str) -> Dict[str, Path]:
    run_dir = store.run_dir(run_id)
    return {"review": run_dir / "review", "remote_log": run_dir / "remote-build.log"}


def _assert_final_outputs_absent(store: RunStateStore, run_id: str) -> None:
    occupied = [
        str(path)
        for path in _final_output_paths(store, run_id).values()
        if path.exists() or path.is_symlink()
    ]
    if occupied:
        raise PipelineError(
            "local candidate output is already occupied: {}".format(", ".join(occupied)),
            category="LOCAL_OUTPUT_OCCUPIED",
        )


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _publish_fetched_outputs(
    store: RunStateStore, run_id: str, fetched: Dict[str, str]
) -> Dict[str, str]:
    _assert_final_outputs_absent(store, run_id)
    run_dir = store.run_dir(run_id).resolve()
    review_requested = Path(
        os.path.abspath(os.path.expanduser(str(fetched["review_path"])))
    )
    log_requested = Path(
        os.path.abspath(os.path.expanduser(str(fetched["remote_log_path"])))
    )
    try:
        review_metadata = review_requested.lstat()
        log_metadata = log_requested.lstat()
    except OSError as exc:
        raise PipelineError(
            "fetched outputs are missing: {}".format(exc),
            category="LOCAL_PUBLISH_FAILED",
        ) from exc
    if (
        stat.S_ISLNK(review_metadata.st_mode)
        or not stat.S_ISDIR(review_metadata.st_mode)
        or review_metadata.st_uid != os.getuid()
        or stat.S_ISLNK(log_metadata.st_mode)
        or not stat.S_ISREG(log_metadata.st_mode)
        or log_metadata.st_uid != os.getuid()
        or log_metadata.st_nlink != 1
    ):
        raise PipelineError(
            "fetched review root or remote log is unsafe",
            category="LOCAL_PUBLISH_FAILED",
        )
    review_source = review_requested.resolve()
    log_source = log_requested.resolve()
    staging = review_source.parent
    if (
        log_source.parent != staging
        or not _path_is_within(staging, run_dir)
        or staging == run_dir
    ):
        raise PipelineError(
            "fetched outputs are outside the private run staging directory",
            category="LOCAL_PUBLISH_FAILED",
        )
    final = _final_output_paths(store, run_id)
    try:
        os.rename(str(log_source), str(final["remote_log"]))
        os.rename(str(review_source), str(final["review"]))
        staging.rmdir()
    except OSError as exc:
        raise PipelineError(
            "cannot publish fetched outputs without overwrite: {}".format(exc),
            category="LOCAL_PUBLISH_FAILED",
        ) from exc
    return {"review_path": str(final["review"]), "remote_log_path": str(final["remote_log"])}


def _fetch_staging_path(store: RunStateStore, run_id: str) -> Path:
    return store.run_dir(run_id) / ".fetch-{}".format(uuid.uuid4().hex[:16])


def _initial_run_state(plan: Dict[str, Any], online: Dict[str, Any]) -> Dict[str, Any]:
    adapter = _facade_adapter_factory(plan.get("target_id", "kylin-amd64"))
    state_plan = deepcopy(plan)
    if state_plan.get("input", {}).get("status") == "MISSING":
        state_plan["input"] = {"status": "MISSING", "files": {}}
    return new_run_state(state_plan, online, adapter)


def _failure_payload(exc: PipelineError) -> Dict[str, Any]:
    return {"category": exc.category, "detail": str(exc), "recorded_at": utc_now()}


def run_candidate_build(
    plan: Dict[str, Any],
    store: RunStateStore,
    transport: Any,
    *,
    confirmed: bool,
    online_result: Optional[Dict[str, Any]] = None,
    prepare_input: Optional[Any] = None,
    command_runner: Any = _run_command,
    review_validator: Optional[Any] = None,
) -> Dict[str, Any]:
    online = online_result if online_result is not None else transport.online_doctor()
    if online.get("builder_status") != "BUILDER_READY":
        status = str(online.get("builder_status", "BLOCKED"))
        category = "BUILDER_UNREACHABLE" if status == "BUILDER_UNREACHABLE" else "ONLINE_DOCTOR_BLOCKED"
        raise PipelineError(
            "online doctor did not report BUILDER_READY: {}".format(status),
            category=category,
        )
    if not confirmed:
        raise PipelineError(
            "candidate build requires one explicit confirmation",
            category="CONFIRMATION_REQUIRED",
        )
    run_id = str(plan.get("run_id", ""))
    validator = review_validator or validate_candidate_review
    _validate_run_id(run_id)
    if str(plan.get("local_run_dir", "")) != str(store.run_dir(run_id)):
        raise PipelineError("plan/state root mismatch", category="PLAN_INVALID")
    store.create(run_id, _initial_run_state(plan, online))
    _controller_log(store, run_id, "run-created")
    remote_succeeded = False
    try:
        with RunLock(store, run_id):
            def verify_input() -> Dict[str, Any]:
                if plan["input"]["status"] == "MISSING":
                    callback = prepare_input
                    if callback is None:
                        callback = lambda: _run_builder_input_preparer(plan, command_runner)
                    callback()
                return inspect_builder_input(
                    Path(plan["repo_root"]), str(plan["source_commit"])
                )

            input_identity = verify_input()
            if input_identity["status"] != "REUSABLE":
                raise PipelineError(
                    "builder input is not reusable after preparation",
                    category="INPUT_VERIFICATION_FAILED",
                )
            manifest_sha256 = input_identity["files"]["manifest"]["sha256"]
            bound_state = store.bind_verified_input(
                run_id, input_identity, manifest_sha256
            )
            plan = bound_state["plan"]
            _recorded_stage(
                store, run_id, "INPUT_VERIFIED", lambda: None
            )

            _recorded_stage(
                store, run_id, "REMOTE_RUN_CREATED", lambda: transport.create_remote_run(plan)
            )
            _recorded_stage(
                store, run_id, "INPUT_TRANSFERRED", lambda: transport.transfer_input(plan)
            )
            _recorded_stage(
                store,
                run_id,
                "REMOTE_INPUT_VERIFIED",
                lambda: transport.verify_remote_input(plan),
            )
            _recorded_stage(
                store,
                run_id,
                "REMOTE_BUILD_SUCCEEDED",
                lambda: transport.build_remote_candidate(plan),
            )
            remote_succeeded = True
            store.update(
                run_id, {"remote_build_succeeded": True, "fetch_allowed": True}
            )
            _assert_final_outputs_absent(store, run_id)
            fetched = _recorded_stage(
                store,
                run_id,
                "REVIEW_FETCHED",
                lambda: transport.fetch(plan, _fetch_staging_path(store, run_id)),
            )
            candidate = _recorded_stage(
                store,
                run_id,
                "LOCAL_REVIEW_VERIFIED",
                lambda: validator(
                    plan, Path(fetched["review_path"]), Path(fetched["remote_log_path"])
                ),
            )
            published = _recorded_stage(
                store,
                run_id,
                "CANDIDATE_BUILT",
                lambda: _publish_fetched_outputs(store, run_id, fetched),
            )
            final_deb = Path(published["review_path"]) / candidate["relative_path"]
            candidate["path"] = str(final_deb)
            candidate["kind"] = "deb"
            store.update(
                run_id,
                {
                    "stage": "CANDIDATE_BUILT",
                    "status_label": "候选 DEB 已构建",
                    "finished_at": utc_now(),
                    "fetch_allowed": False,
                    "failure": None,
                    "artifact": candidate,
                    "deb": candidate,
                },
            )
    except PipelineError as exc:
        current = store.load(run_id)
        remote_succeeded = bool(current.get("remote_build_succeeded")) or remote_succeeded
        failure_stage = "FETCH_PENDING" if remote_succeeded else "FAILED"
        store.update(
            run_id,
            {
                "stage": failure_stage,
                "status_label": "候选 DEB 取回待恢复" if remote_succeeded else "候选 DEB 未构建",
                "finished_at": None if remote_succeeded else utc_now(),
                "fetch_allowed": remote_succeeded,
                "failure": _failure_payload(exc),
            },
        )
        raise
    return store.load(run_id)


def fetch_candidate(
    store: RunStateStore,
    run_id: str,
    transport: Any,
    *,
    review_validator: Optional[Any] = None,
) -> Dict[str, Any]:
    state = store.load(run_id)
    if (
        state.get("stage") != "FETCH_PENDING"
        or not state.get("remote_build_succeeded")
        or not state.get("fetch_allowed")
    ):
        raise PipelineError(
            "fetch is allowed only after remote build success and local retrieval failure",
            category="FETCH_NOT_ALLOWED",
        )
    _assert_final_outputs_absent(store, run_id)
    plan = state.get("plan")
    if not isinstance(plan, dict):
        raise PipelineError("run state lacks its candidate plan", category="PLAN_INVALID")
    validator = review_validator or validate_candidate_review
    try:
        with RunLock(store, run_id):
            fetched = _recorded_stage(
                store,
                run_id,
                "REVIEW_FETCHED",
                lambda: transport.fetch(plan, _fetch_staging_path(store, run_id)),
            )
            candidate = _recorded_stage(
                store,
                run_id,
                "LOCAL_REVIEW_VERIFIED",
                lambda: validator(
                    plan, Path(fetched["review_path"]), Path(fetched["remote_log_path"])
                ),
            )
            published = _recorded_stage(
                store,
                run_id,
                "CANDIDATE_BUILT",
                lambda: _publish_fetched_outputs(store, run_id, fetched),
            )
            candidate["path"] = str(
                Path(published["review_path"]) / candidate["relative_path"]
            )
            candidate["kind"] = "deb"
            store.update(
                run_id,
                {
                    "stage": "CANDIDATE_BUILT",
                    "status_label": "候选 DEB 已构建",
                    "finished_at": utc_now(),
                    "fetch_allowed": False,
                    "failure": None,
                    "artifact": candidate,
                    "deb": candidate,
                },
            )
    except PipelineError as exc:
        store.update(
            run_id,
            {
                "stage": "FETCH_PENDING",
                "status_label": "候选 DEB 取回待恢复",
                "fetch_allowed": True,
                "failure": _failure_payload(exc),
            },
        )
        raise
    return store.load(run_id)


def local_doctor(
    repo: Path,
    target: Dict[str, Any],
    state_root: Path,
    *,
    ssh_config: Optional[Path] = None,
) -> Dict[str, Any]:
    blockers = []  # type: list
    failure_categories = []  # type: list
    checks = []  # type: list

    def record(name: str, passed: bool, category: str, detail: str) -> None:
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})
        if not passed:
            blockers.append(detail)
            failure_categories.append(category)

    try:
        repo_root = Path(repo).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PipelineError("operator-supplied repository path is invalid: {}".format(exc)) from exc
    record("repo-directory", repo_root.is_dir(), "REPO_INVALID", str(repo_root))

    branch = ""
    source_commit = ""
    status_output = ""
    top_level = _git(repo_root, "rev-parse", "--show-toplevel")
    if top_level.returncode == 0:
        observed_top = Path(top_level.stdout.strip()).resolve()
        record(
            "explicit-repo",
            observed_top == repo_root,
            "REPO_IDENTITY_MISMATCH",
            "git top-level must equal the operator-supplied repository path",
        )
    else:
        record("explicit-repo", False, "REPO_INVALID", top_level.stderr.strip() or "not a Git repository")

    branch_result = _git(repo_root, "branch", "--show-current")
    if branch_result.returncode == 0:
        branch = branch_result.stdout.strip()
    record("branch-main", branch == "main", "BRANCH_NOT_MAIN", "branch must be main")

    commit_result = _git(repo_root, "rev-parse", "--verify", "HEAD")
    if commit_result.returncode == 0:
        source_commit = commit_result.stdout.strip()
    record(
        "full-source-commit",
        re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None,
        "SOURCE_COMMIT_INVALID",
        "HEAD must resolve to one full 40-character commit",
    )

    status_result = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    if status_result.returncode == 0:
        status_output = status_result.stdout
    record(
        "worktree-clean",
        status_result.returncode == 0 and not status_output,
        "WORKTREE_NOT_CLEAN",
        "worktree must be clean",
    )

    interface = None  # type: Optional[Dict[str, Any]]
    try:
        interface = _load_packaging_interface(repo_root)
        record("packaging-interface", True, "PACKAGING_INTERFACE_INVALID", "packaging interface accepted")
    except PipelineError as exc:
        record("packaging-interface", False, "PACKAGING_INTERFACE_INVALID", str(exc))

    if interface is not None:
        required_entries = (
            interface["builder_input_entry"],
            interface["build_host_entry"],
            str(REQUIRED_PREFLIGHT_PATH),
        )
        missing_entries = [entry for entry in required_entries if not (repo_root / entry).is_file()]
        record(
            "packaging-entrypoints",
            not missing_entries,
            "PACKAGING_ENTRYPOINT_MISSING",
            "missing packaging entrypoints: {}".format(", ".join(missing_entries))
            if missing_entries
            else "99/00/01 entrypoints exist",
        )

    ssh_result = _effective_ssh_config(target["host_alias"], ssh_config)
    effective = _parse_ssh_effective(ssh_result.stdout) if ssh_result.returncode == 0 else {}
    alias_ok = (
        ssh_result.returncode == 0
        and effective.get("user") == target["remote_user"]
        and bool(effective.get("hostname"))
    )
    record(
        "ssh-alias",
        alias_ok,
        "SSH_ALIAS_MISSING",
        "SSH alias {} must resolve to remote user {}".format(
            target["host_alias"], target["remote_user"]
        ),
    )

    record(
        "state-root-writable",
        _path_writable_without_creation(Path(state_root)),
        "STATE_ROOT_UNWRITABLE",
        "state/artifact root must be writable without scanning other directories",
    )

    controller_status = "CONTROLLER_READY" if not blockers else "BLOCKED"
    return {
        "schema": "taiji-package-doctor/v1",
        "repo_root": str(repo_root),
        "branch": branch,
        "source_commit": source_commit,
        "clean": not status_output and status_result.returncode == 0,
        "target_id": target["target_id"],
        "host_alias": target["host_alias"],
        "controller_status": controller_status,
        "builder_status": "BUILDER_UNREACHABLE",
        "online_checked": False,
        "checks": checks,
        "failure_categories": failure_categories,
        "blockers": blockers,
    }


def _new_run_id(source_commit: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "{}-{}-{}".format(stamp, source_commit[:12], uuid.uuid4().hex[:8])


def input_triplet_paths(repo: Path, source_commit: str) -> Dict[str, Path]:
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise PipelineError("source commit must be a full SHA", category="SOURCE_COMMIT_INVALID")
    archive_name = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    return {
        "archive": Path(repo) / archive_name,
        "manifest": Path(repo) / "taijiagent-制包机输入-{}.manifest.json".format(source_commit),
        "checksum": Path(repo) / (archive_name + ".sha256"),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise PipelineError(
            "cannot hash builder input {}: {}".format(path.name, exc),
            category="INPUT_VERIFICATION_FAILED",
        ) from exc
    return digest.hexdigest()


def _input_file_metadata(path: Path) -> Dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError(
            "builder input disappeared during verification: {}".format(path.name),
            category="INPUT_VERIFICATION_FAILED",
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
    ):
        raise PipelineError(
            "builder input must be a current-user-owned single-link regular file: {}".format(
                path.name
            ),
            category="INPUT_VERIFICATION_FAILED",
        )
    return {
        "path": str(path),
        "basename": path.name,
        "bytes": metadata.st_size,
        "sha256": _sha256_file(path),
        "exists": True,
    }


def inspect_builder_input(repo: Path, source_commit: str) -> Dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    paths = input_triplet_paths(repo_root, source_commit)
    presence = {
        name: path.exists() or path.is_symlink() for name, path in paths.items()
    }
    if not any(presence.values()):
        return {
            "status": "MISSING",
            "prepare_required": True,
            "source_commit": source_commit,
            "files": {
                name: {"path": str(path), "basename": path.name, "exists": False}
                for name, path in paths.items()
            },
        }
    if not all(presence.values()):
        raise PipelineError(
            "builder input triplet is partial; preserve it and stop without repair",
            category="INPUT_TRIPLET_PARTIAL",
        )

    helper = repo_root / "packaging/linux/builder-input-package.py"
    if not helper.is_file() or helper.is_symlink():
        raise PipelineError(
            "formal builder-input verifier is missing or unsafe",
            category="INPUT_VERIFICATION_FAILED",
        )
    result = _run_command(
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(helper),
            "verify",
            "--archive",
            str(paths["archive"]),
            "--manifest",
            str(paths["manifest"]),
            "--checksum",
            str(paths["checksum"]),
        ],
        cwd=repo_root,
        environment=_command_environment(),
        timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "verifier returned non-zero"
        raise PipelineError(
            "formal builder-input verification failed: {}".format(detail),
            category="INPUT_VERIFICATION_FAILED",
        )
    manifest = _load_json_object(paths["manifest"], "builder input manifest")
    if manifest.get("source_commit") != source_commit:
        raise PipelineError(
            "builder input manifest source commit differs from HEAD",
            category="INPUT_VERIFICATION_FAILED",
        )
    files = {name: _input_file_metadata(path) for name, path in paths.items()}
    return {
        "status": "REUSABLE",
        "prepare_required": False,
        "source_commit": source_commit,
        "archive_sha256": manifest.get("archive_sha256"),
        "files": files,
    }


def _canonical_policy_sha256(repo: Path) -> str:
    helper = Path(repo) / "packaging/linux/compatibility_policy.py"
    policy = Path(repo) / "packaging/linux/compatibility-policy.json"
    result = _run_command(
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(helper),
            "validate",
            "--policy",
            str(policy),
            "--print-sha256",
        ],
        cwd=Path(repo),
        environment=_command_environment(),
        timeout=60,
    )
    digest = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        detail = result.stderr.strip() or "canonical policy helper returned invalid output"
        raise PipelineError(detail, category="COMPATIBILITY_POLICY_INVALID")
    return digest


def _canonical_policy_minimum_glibc(repo: Path) -> str:
    _canonical_policy_sha256(repo)
    policy = _load_json_object(
        Path(repo) / "packaging/linux/compatibility-policy.json",
        "canonical compatibility policy",
    )
    minimum_supported = policy.get("minimum_supported")
    minimum = (
        minimum_supported.get("glibc")
        if isinstance(minimum_supported, dict)
        else None
    )
    if not isinstance(minimum, str) or re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)*", minimum) is None:
        raise PipelineError(
            "canonical policy glibc minimum is invalid",
            category="COMPATIBILITY_POLICY_INVALID",
        )
    return minimum


def _safe_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PipelineError(
            "{} is missing: {}".format(label, path), category="LOCAL_REVIEW_INVALID"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
    ):
        raise PipelineError(
            "{} is not a current-user-owned single-link regular file".format(label),
            category="LOCAL_REVIEW_INVALID",
        )
    return metadata


def _parse_marker(path: Path) -> Dict[str, str]:
    _safe_regular_file(path, "build marker")
    result = {}  # type: Dict[str, str]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PipelineError("build marker is unreadable", category="LOCAL_REVIEW_INVALID") from exc
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or not key or not value or key in result:
            raise PipelineError("build marker is malformed", category="LOCAL_REVIEW_INVALID")
        result[key] = value
    return result


def _verify_repo_source_identity(repo: Path, source_commit: str) -> None:
    try:
        repo_root = Path(repo).expanduser().resolve(strict=True)
        top_level = _git(repo_root, "rev-parse", "--show-toplevel")
        branch = _git(repo_root, "branch", "--show-current")
        head = _git(repo_root, "rev-parse", "--verify", "HEAD")
        status_result = _git(
            repo_root, "status", "--porcelain", "--untracked-files=all"
        )
    except (OSError, RuntimeError, PipelineError) as exc:
        raise PipelineError(
            "source repository cannot be rechecked: {}".format(exc),
            category="SOURCE_DRIFT",
        ) from exc
    checks = (
        top_level.returncode == 0
        and Path(top_level.stdout.strip()).resolve() == repo_root
        and branch.returncode == 0
        and branch.stdout.strip() == "main"
        and head.returncode == 0
        and head.stdout.strip() == source_commit
        and status_result.returncode == 0
        and not status_result.stdout
    )
    if not checks:
        raise PipelineError(
            "source repository is no longer clean main at the planned commit",
            category="SOURCE_DRIFT",
        )


def validate_candidate_review(
    plan: Dict[str, Any],
    review_path: Path,
    remote_log_path: Path,
    *,
    command_runner: Any = _run_command,
) -> Dict[str, Any]:
    review_requested = Path(
        os.path.abspath(os.path.expanduser(str(review_path)))
    )
    try:
        review_metadata = review_requested.lstat()
    except OSError as exc:
        raise PipelineError("retrieved review is missing", category="LOCAL_REVIEW_INVALID") from exc
    if (
        stat.S_ISLNK(review_metadata.st_mode)
        or not stat.S_ISDIR(review_metadata.st_mode)
        or review_metadata.st_uid != os.getuid()
    ):
        raise PipelineError("retrieved review root is unsafe", category="LOCAL_REVIEW_INVALID")
    review = review_requested.resolve()
    remote_log_requested = Path(
        os.path.abspath(os.path.expanduser(str(remote_log_path)))
    )
    _safe_regular_file(remote_log_requested, "remote build log")
    remote_log = remote_log_requested.resolve()

    repo = Path(str(plan.get("repo_root", ""))).expanduser().resolve()
    source_commit = str(plan.get("source_commit", ""))
    expected_policy_sha = str(plan.get("canonical_policy_sha256", ""))
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise PipelineError("plan source commit is invalid", category="PLAN_INVALID")
    if re.fullmatch(r"[0-9a-f]{64}", expected_policy_sha) is None:
        raise PipelineError("plan policy SHA256 is invalid", category="PLAN_INVALID")
    _verify_repo_source_identity(repo, source_commit)

    delivery = review / "taijiagent 打包交付"
    output = delivery / "生成的安装包"
    if not delivery.is_dir() or delivery.is_symlink() or not output.is_dir() or output.is_symlink():
        raise PipelineError("review delivery/output tree is incomplete", category="LOCAL_REVIEW_INVALID")
    try:
        output_names = {entry.name for entry in output.iterdir()}
    except OSError as exc:
        raise PipelineError("review output cannot be listed", category="LOCAL_REVIEW_INVALID") from exc
    deb_names = [name for name in output_names if re.fullmatch(r"taiji-agent_.+_amd64\.deb", name)]
    if len(deb_names) != 1:
        raise PipelineError("review must contain exactly one amd64 DEB", category="LOCAL_REVIEW_INVALID")
    deb_name = deb_names[0]
    expected_names = {
        deb_name,
        deb_name + ".sha256",
        "taiji-package-manifest.json",
        "formal-build-tests.log",
        "构建报告.txt",
        ".build-success",
    }
    if output_names != expected_names:
        raise PipelineError("review output file set is not canonical", category="LOCAL_REVIEW_INVALID")

    deb = output / deb_name
    deb_metadata = _safe_regular_file(deb, "candidate DEB")
    deb_sha = _sha256_file(deb)
    sidecar = output / (deb_name + ".sha256")
    _safe_regular_file(sidecar, "candidate DEB sidecar")
    try:
        sidecar_payload = sidecar.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PipelineError("candidate sidecar is unreadable", category="LOCAL_REVIEW_INVALID") from exc
    if sidecar_payload != "{}  {}\n".format(deb_sha, deb_name):
        raise PipelineError(
            "candidate DEB SHA256 sidecar mismatch", category="ARTIFACT_SHA_MISMATCH"
        )

    manifest_path = output / "taiji-package-manifest.json"
    _safe_regular_file(manifest_path, "candidate manifest")
    manifest = _load_json_object(manifest_path, "candidate manifest")
    required_manifest = {
        "schema": "taiji-package-manifest/v3",
        "architecture": "amd64",
        "source_commit": source_commit,
        "deb_basename": deb_name,
        "deb_sha256": deb_sha,
        "compatibility_policy_sha256": expected_policy_sha,
        "formal_build_tests_status": "pass",
        "formal_build_tests_log_basename": "formal-build-tests.log",
    }
    for key, expected in required_manifest.items():
        if manifest.get(key) != expected:
            category = "ARTIFACT_SHA_MISMATCH" if key == "deb_sha256" else "LOCAL_REVIEW_INVALID"
            raise PipelineError(
                "candidate manifest {} mismatch".format(key), category=category
            )

    formal_log = output / "formal-build-tests.log"
    formal_metadata = _safe_regular_file(formal_log, "formal build test log")
    if formal_metadata.st_size == 0 or manifest.get("formal_build_tests_log_sha256") != _sha256_file(
        formal_log
    ):
        raise PipelineError("formal build test log mismatch", category="LOCAL_REVIEW_INVALID")
    report = output / "构建报告.txt"
    if _safe_regular_file(report, "build report").st_size == 0:
        raise PipelineError("build report is empty", category="LOCAL_REVIEW_INVALID")

    marker = _parse_marker(output / ".build-success")
    marker_expected = {
        "source_commit": source_commit,
        "deb": deb_name,
        "deb_sha256": deb_sha,
        "compatibility_policy_sha256": expected_policy_sha,
        "formal_build_tests_status": "pass",
        "formal_build_tests_log_basename": "formal-build-tests.log",
        "formal_build_tests_log_sha256": _sha256_file(formal_log),
    }
    for key, expected in marker_expected.items():
        if marker.get(key) != expected:
            category = "ARTIFACT_SHA_MISMATCH" if key == "deb_sha256" else "LOCAL_REVIEW_INVALID"
            raise PipelineError("build marker {} mismatch".format(key), category=category)

    local_preflight = repo / REQUIRED_PREFLIGHT_PATH
    review_preflight = delivery / REQUIRED_PREFLIGHT_PATH.name
    _safe_regular_file(local_preflight, "local frozen preflight")
    _safe_regular_file(review_preflight, "review frozen preflight")
    if local_preflight.read_bytes() != review_preflight.read_bytes():
        raise PipelineError("review preflight differs from source commit", category="LOCAL_REVIEW_INVALID")
    environment = _command_environment()
    environment.update(
        {
            "TAIJI_RELEASE_REQUIRE_ARTIFACTS": "1",
            "TAIJI_RELEASE_SKIP_GIT_CHECK": "1",
            "TAIJI_REPO_ROOT": str(review),
        }
    )
    preflight = command_runner(
        ["/bin/bash", "-p", str(review_preflight)],
        cwd=review,
        environment=environment,
        timeout=3600,
    )
    if preflight.returncode != 0:
        detail = preflight.stderr.strip() or preflight.stdout.strip() or "preflight returned non-zero"
        raise PipelineError(
            "local candidate preflight failed: {}".format(detail),
            category="LOCAL_PREFLIGHT_FAILED",
        )
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise PipelineError("candidate version is invalid", category="LOCAL_REVIEW_INVALID")
    return {
        "basename": deb_name,
        "bytes": deb_metadata.st_size,
        "sha256": deb_sha,
        "version": version,
        "path": str(deb),
        "relative_path": str(deb.relative_to(review)),
        "manifest_path": str(manifest_path),
        "marker_path": str(output / ".build-success"),
    }


def _planned_remote_verify_script(remote_dir: str, checksum_name: str) -> str:
    return "; ".join(
        [
            "set -Eeuo pipefail",
            "umask 077",
            "cd {}".format(shlex.quote(remote_dir)),
            "/usr/bin/sha256sum -c {}".format(shlex.quote(checksum_name)),
        ]
    )


def _planned_remote_script(remote_dir: str, source_commit: str) -> str:
    source_archive = "taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(source_commit)
    delivery = "taijiagent 打包交付"
    statements = [
        "set -Eeuo pipefail",
        "umask 077",
        "cd {}".format(shlex.quote(remote_dir)),
        "/usr/bin/tar --no-same-owner --no-same-permissions -xzf {}".format(
            shlex.quote("taijiagent-制包机输入-{}.tar.gz".format(source_commit))
        ),
        "cd {}".format(shlex.quote(remote_dir + "/" + delivery)),
        (
            "TAIJI_UV_LOCK_MODE=strict /bin/bash -p "
            "./00_制包机_生成离线交付包.sh 2>&1 | /usr/bin/tee {}"
        ).format(shlex.quote(remote_dir + "/remote-build.log")),
        "cd {}".format(shlex.quote(remote_dir)),
        "/usr/bin/install -d -m 0700 -- review",
        "/usr/bin/tar --no-same-owner --no-same-permissions -xzf {} -C review".format(
            shlex.quote(remote_dir + "/" + delivery + "/" + source_archive)
        ),
        "/bin/cp -a -- {}/. {}/".format(
            shlex.quote(remote_dir + "/" + delivery),
            shlex.quote(remote_dir + "/review/taiji-agentv1.0/" + delivery),
        ),
    ]
    return "; ".join(statements)


def _remote_clean_shell(account_home: str, script: str) -> str:
    return (
        "/usr/bin/env -i HOME={} TMPDIR=/tmp PATH=/usr/bin:/bin LANG=C LC_ALL=C "
        "/bin/bash -p -c {}"
    ).format(shlex.quote(account_home), shlex.quote(script))


def build_candidate_plan(
    repo: Path,
    target: Dict[str, Any],
    state_root: Path,
    *,
    run_id: Optional[str] = None,
    ssh_config: Optional[Path] = None,
) -> Dict[str, Any]:
    doctor = local_doctor(repo, target, state_root, ssh_config=ssh_config)
    if doctor["controller_status"] != "CONTROLLER_READY":
        raise PipelineError("local doctor blocked: {}".format("; ".join(doctor["blockers"])))
    repo_root = Path(doctor["repo_root"])
    source_commit = doctor["source_commit"]
    actual_run_id = run_id or _new_run_id(source_commit)
    _validate_run_id(actual_run_id)
    triplet = input_triplet_paths(repo_root, source_commit)
    input_status = inspect_builder_input(repo_root, source_commit)
    remote_dir = "{}/{}/{}".format(
        target["remote_root"].rstrip("/"), source_commit, actual_run_id
    )
    state_root_path = RunStateStore(state_root).root
    local_run_dir = state_root_path / "runs" / actual_run_id
    remote_parent = "{}/{}".format(target["remote_root"].rstrip("/"), source_commit)
    ssh_prefix = _ssh_prefix(target, ssh_config)
    scp_prefix = _scp_prefix(ssh_config)
    create_script = (
        "set -Eeuo pipefail; umask 077; /usr/bin/install -d -m 0700 -- {}; "
        "/bin/mkdir -m 0700 -- {}"
    ).format(shlex.quote(remote_parent), shlex.quote(remote_dir))
    commands = []  # type: list
    if input_status["status"] == "MISSING":
        interface = _load_packaging_interface(repo_root)
        commands.append(
            {
                "stage": "prepare-input",
                "argv": ["/bin/bash", "-p", str(repo_root / interface["builder_input_entry"])],
            }
        )
    commands.extend(
        [
            {
                "stage": "create-remote-run",
                "argv": ssh_prefix
                + [_remote_clean_shell(target["remote_account_home"], create_script)],
            },
            {
                "stage": "transfer-input",
                "argv": scp_prefix
                + [
                    str(triplet["archive"]),
                    str(triplet["manifest"]),
                    str(triplet["checksum"]),
                    "{}:{}/".format(target["host_alias"], remote_dir),
                ],
            },
            {
                "stage": "remote-input-verify",
                "argv": ssh_prefix
                + [
                    _remote_clean_shell(
                        target["remote_account_home"],
                        _planned_remote_verify_script(
                            remote_dir, triplet["checksum"].name
                        ),
                    )
                ],
            },
            {
                "stage": "remote-candidate-build",
                "argv": ssh_prefix
                + [
                    _remote_clean_shell(
                        target["remote_account_home"],
                        _planned_remote_script(remote_dir, source_commit),
                    )
                ],
            },
            {
                "stage": "fetch-review",
                "argv": scp_prefix
                + [
                    "-r",
                    "{}:{}/review/taiji-agentv1.0".format(target["host_alias"], remote_dir),
                    str(local_run_dir / "review"),
                ],
            },
            {
                "stage": "fetch-log",
                "argv": scp_prefix
                + [
                    "{}:{}/remote-build.log".format(target["host_alias"], remote_dir),
                    str(local_run_dir / "remote-build.log"),
                ],
            },
            {
                "stage": "local-candidate-preflight",
                "argv": [
                    "/bin/bash",
                    "-p",
                    str(local_run_dir / "review/taijiagent 打包交付/01_制包机_发布预检.sh"),
                ],
            },
        ]
    )
    source_tree_result = _git(repo_root, "rev-parse", "HEAD^{tree}")
    source_tree = source_tree_result.stdout.strip() if source_tree_result.returncode == 0 else ""
    controller_commit_result = _git(ROOT, "rev-parse", "HEAD")
    controller_commit = (
        controller_commit_result.stdout.strip()
        if controller_commit_result.returncode == 0
        else ""
    )
    if re.fullmatch(r"[0-9a-f]{40}", source_tree) is None:
        raise PipelineError("source tree must be a full SHA", category="PLAN_INVALID")
    if re.fullmatch(r"[0-9a-f]{40}", controller_commit) is None:
        raise PipelineError("controller commit must be a full SHA", category="PLAN_INVALID")
    return {
        "schema": "taiji-package-candidate-plan/v1",
        "run_id": actual_run_id,
        "repo_root": str(repo_root),
        "source_commit": source_commit,
        "source_branch": doctor["branch"],
        "source_tree": source_tree,
        "controller_commit": controller_commit,
        "canonical_policy_sha256": _canonical_policy_sha256(repo_root),
        "target_id": target["target_id"],
        "target_config": dict(target),
        "target_adapter": dict(target),
        "architecture": target["architecture"],
        "host_alias": target["host_alias"],
        "remote_run_dir": remote_dir,
        "local_run_dir": str(local_run_dir),
        "input": input_status,
        "commands": commands,
        "authorization_blocks": [
            {
                "stage": "SSH 与传输",
                "identity": {
                    "host_alias": target["host_alias"],
                    "source_commit": source_commit,
                    "input_files": [
                        {
                            "role": role,
                            "basename": metadata["basename"],
                            "bytes": metadata.get("bytes"),
                            "sha256": metadata.get("sha256"),
                            "verification_status": input_status["status"],
                        }
                        for role, metadata in input_status["files"].items()
                    ],
                    "direction": "controller-to-builder input; builder-to-controller review/log",
                    "remote_run_dir": remote_dir,
                },
                "impact": "create one unique remote run directory and transfer the bound trio",
                "rollback_and_stop": "preserve the failed remote run for audit; do not auto-clean or continue",
            },
            {
                "stage": "依赖与网络",
                "identity": {
                    "host_alias": target["host_alias"],
                    "build_entry": "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
                    "network": "apt and source-authorized fixed tool downloads may occur",
                    "sudo": "frozen 00 may install its declared build dependencies",
                },
                "impact": "remote package metadata, declared build dependencies, and build caches may change",
                "rollback_and_stop": "stop on doctor, apt, download, or capability failure; do not install the candidate",
            },
            {
                "stage": "候选构建",
                "identity": {
                    "source_commit": source_commit,
                    "host_alias": target["host_alias"],
                    "remote_run_dir": remote_dir,
                    "local_run_dir": str(local_run_dir),
                    "success_label": "候选 DEB 已构建",
                },
                "impact": "run frozen 00/01 and retrieve one candidate review tree plus build log",
                "rollback_and_stop": "retain evidence at the failed stage; never continue to install, accept, sign, or publish",
            },
        ],
        "boundaries": {
            "network": "remote 00 may use apt and source-authorized downloads",
            "sudo": "remote 00 may install its declared build dependencies",
            "output": "candidate DEB review only",
            "excluded": [
                "installation",
                "offline-lifecycle",
                "desktop-acceptance",
                "certification",
                "signing",
                "publication",
            ],
        },
        "stop_before": "input preparation when online doctor does not pass",
        "resume_from": "fetch only after remote candidate build success",
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--target", default=None)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--ssh-config", type=Path)
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


def _legacy_main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "status":
            payload = RunStateStore(args.state_root).load(args.run_id)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        target_reference = args.target
        if target_reference is None and args.command in ("doctor", "plan", "build"):
            target_reference = DEFAULT_TARGET
        target = load_target(Path(target_reference))
        if args.command == "doctor":
            local = local_doctor(
                args.repo, target, args.state_root, ssh_config=args.ssh_config
            )
            online = None
            if args.online and local["controller_status"] == "CONTROLLER_READY":
                online = RealSshTransport(
                    args.repo, target, ssh_config=args.ssh_config
                ).online_doctor()
            payload = {"local": local, "online": online}
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            else:
                print(local["controller_status"])
                print(online["builder_status"] if online is not None else local["builder_status"])
                for blocker in local["blockers"]:
                    print("BLOCKER\t{}".format(blocker))
                if online is not None:
                    for blocker in online["blockers"]:
                        print("BLOCKER\t{}".format(blocker))
            local_ready = local["controller_status"] == "CONTROLLER_READY"
            online_ready = not args.online or (
                online is not None and online["builder_status"] == "BUILDER_READY"
            )
            return 0 if local_ready and online_ready else 2
        if args.command == "plan":
            payload = build_candidate_plan(
                args.repo,
                target,
                args.state_root,
                ssh_config=args.ssh_config,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == "build":
            plan = build_candidate_plan(
                args.repo,
                target,
                args.state_root,
                ssh_config=args.ssh_config,
            )
            transport = RealSshTransport(
                args.repo, target, ssh_config=args.ssh_config
            )
            online = transport.online_doctor()
            if online.get("builder_status") != "BUILDER_READY":
                status = str(online.get("builder_status", "BLOCKED"))
                category = (
                    "BUILDER_UNREACHABLE"
                    if status == "BUILDER_UNREACHABLE"
                    else "ONLINE_DOCTOR_BLOCKED"
                )
                raise PipelineError(
                    "online doctor did not report BUILDER_READY: {}".format(status),
                    category=category,
                )
            print(
                json.dumps(
                    {"online_doctor": online, "plan": plan},
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
            try:
                confirmation = input(
                    "输入 BUILD 以确认输入准备、远程传输和候选构建三个阶段："
                ).strip()
            except EOFError as exc:
                raise PipelineError(
                    "interactive BUILD confirmation is required",
                    category="CONFIRMATION_REQUIRED",
                ) from exc
            if confirmation != "BUILD":
                raise PipelineError(
                    "candidate build confirmation did not match BUILD",
                    category="CONFIRMATION_REQUIRED",
                )
            state = run_candidate_build(
                plan,
                RunStateStore(args.state_root),
                transport,
                confirmed=True,
                online_result=online,
            )
            print(json.dumps(state, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "fetch":
            store = RunStateStore(args.state_root)
            current = store.load(args.run_id)
            plan = current.get("plan")
            if not isinstance(plan, dict):
                raise PipelineError("run state lacks its candidate plan", category="PLAN_INVALID")
            if plan.get("target_adapter") != target:
                raise PipelineError("target adapter differs from run state", category="PLAN_INVALID")
            transport = RealSshTransport(
                Path(plan["repo_root"]), target, ssh_config=args.ssh_config
            )
            state = fetch_candidate(store, args.run_id, transport)
            print(json.dumps(state, ensure_ascii=False, sort_keys=True))
            return 0
        raise PipelineError("{} is not implemented yet".format(args.command))
    except PipelineError as exc:
        print("BLOCKED\t{}\t{}".format(exc.category, exc), file=sys.stderr)
        return 2


_legacy_local_doctor = local_doctor
_legacy_inspect_builder_input = inspect_builder_input
_legacy_build_candidate_plan = build_candidate_plan
_legacy_run_builder_input_preparer = _run_builder_input_preparer
_legacy_validate_candidate_review = validate_candidate_review
_legacy_real_transport = RealSshTransport
_legacy_fake_transport = FakeSshTransport

from packaging.pipeline.adapters.kylin_amd64 import (
    FakeSshTransport as _KylinFakeSshTransport,
    KylinAmd64Adapter,
    RealSshTransport as _KylinRealSshTransport,
    bind_legacy_namespace,
)
from packaging.pipeline.core.registry import create_adapter
from packaging.pipeline.core.state import RunLock as _CoreRunLock
from packaging.pipeline.core.state import RunStateStore as _CoreRunStateStore
from packaging.pipeline import cli as pipeline_cli
from packaging.pipeline.core.orchestration import (
    _publish_fetched_outputs as _core_publish_fetched_outputs,
)
from packaging.pipeline.adapters.windows_ssh import WindowsSshTransport

bind_legacy_namespace(globals(), _legacy_real_transport, _legacy_fake_transport)
RunStateStore = _CoreRunStateStore
RunLock = _CoreRunLock
RealSshTransport = _KylinRealSshTransport
FakeSshTransport = _KylinFakeSshTransport


def _facade_adapter_factory(target_id):
    adapter = create_adapter(target_id)
    if target_id == "kylin-amd64":
        adapter.transport_factory = (
            lambda repo, target, ssh_config, command_runner: RealSshTransport(
                repo, target, ssh_config=ssh_config, command_runner=command_runner
            )
        )
        adapter.review_validator = (
            lambda plan, review, remote_log: validate_candidate_review(
                plan, review, remote_log
            )
        )
    elif target_id == "windows-x64":
        adapter.transport_factory = (
            lambda target, *, ssh_config, command_runner: WindowsSshTransport(
                target,
                ssh_config=ssh_config,
                command_runner=command_runner,
            )
        )
    return adapter


def _facade_publisher(store, run_id, fetched, artifact):
    published_paths = _core_publish_fetched_outputs(store, run_id, fetched)
    published = dict(artifact)
    published["path"] = str(
        Path(published_paths["review_path"]) / artifact["relative_path"]
    )
    return published


def main(argv: Optional[Sequence[str]] = None) -> int:
    return pipeline_cli.main(
        argv,
        adapter_factory=_facade_adapter_factory,
        command_runner=_run_command,
        input_reader=input,
        publisher=_facade_publisher,
    )


if __name__ == "__main__":
    raise SystemExit(main())
