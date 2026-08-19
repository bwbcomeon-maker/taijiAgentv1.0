#!/usr/bin/python3
"""Thin controller for resumable Taiji x86 Kylin candidate builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
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


class PipelineError(RuntimeError):
    """A stable, operator-actionable candidate pipeline failure."""

    def __init__(self, message: str, *, category: str = "PIPELINE_BLOCKED") -> None:
        super().__init__(message)
        self.category = category


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
    minimum_kib = target["minimum_free_gib"] * 1024 * 1024
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
        "set -Eeuo pipefail",
        "printf 'schema=taiji-online-doctor-v1\\n'",
        "printf 'kernel=%s\\n' \"$(/usr/bin/uname -s)\"",
        "printf 'machine=%s\\n' \"$(/usr/bin/uname -m)\"",
        "printf 'dpkg_arch=%s\\n' \"$(/usr/bin/dpkg --print-architecture)\"",
        "printf 'apt=%s\\n' \"$(command -v apt-get)\"",
        "printf 'dpkg=%s\\n' \"$(command -v dpkg)\"",
        "printf 'glibc=%s\\n' \"$(/usr/bin/ldd --version 2>&1 | /usr/bin/sed -n '1p')\"",
        "/usr/bin/sudo -n /usr/bin/true",
        "printf 'sudo=ready\\n'",
        "free_kib=$(/bin/df -Pk {} | /usr/bin/awk 'NR==2 {{print $4}}')".format(
            shlex.quote(account_home)
        ),
        "free_inodes=$(/bin/df -Pi {} | /usr/bin/awk 'NR==2 {{print $4}}')".format(
            shlex.quote(account_home)
        ),
        "[ \"$free_kib\" -ge {} ]".format(minimum_kib),
        "[ \"$free_inodes\" -ge {} ]".format(target["minimum_free_inodes"]),
        "printf 'free_kib=%s\\n' \"$free_kib\"",
        "printf 'free_inodes=%s\\n' \"$free_inodes\"",
        "[ -d /proc/self/fd ] && [ -r /proc/self/fd/0 ]",
        "printf 'proc=ready\\n'",
        "/usr/bin/python3 -I -B -c {}".format(shlex.quote(python_probe)),
        "printf 'memfd=ready\\n'",
        (
            "if [ -e {root} ] || [ -L {root} ]; then "
            "[ -d {root} ] && [ ! -L {root} ] && [ -O {root} ] && [ -w {root} ]; "
            "else [ -d {home} ] && [ -O {home} ] && [ -w {home} ]; fi"
        ).format(root=shlex.quote(remote_root), home=shlex.quote(account_home)),
        "printf 'remote_root=ready\\n'",
    ]
    return "; ".join(statements)


def _parse_online_payload(stdout: str, target: Dict[str, Any]) -> Dict[str, Any]:
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
    for key in ("apt", "dpkg", "glibc"):
        if not values.get(key):
            blockers.append("remote capability {} is missing".format(key))
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
        return _parse_online_payload(result.stdout, self.target)

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
    state_root_path = Path(state_root).expanduser().resolve()
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
    return {
        "schema": "taiji-package-candidate-plan/v1",
        "run_id": actual_run_id,
        "source_commit": source_commit,
        "target_id": target["target_id"],
        "host_alias": target["host_alias"],
        "remote_run_dir": remote_dir,
        "local_run_dir": str(local_run_dir),
        "input": input_status,
        "commands": commands,
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
        target = load_target(args.target)
        if args.command == "doctor":
            if args.online:
                raise PipelineError("online doctor is implemented by the transport task")
            payload = local_doctor(args.repo, target, args.state_root)
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            else:
                print(payload["controller_status"])
                print(payload["builder_status"])
                for blocker in payload["blockers"]:
                    print("BLOCKER\t{}".format(blocker))
            return 0 if payload["controller_status"] == "CONTROLLER_READY" else 2
        if args.command == "plan":
            payload = build_candidate_plan(args.repo, target, args.state_root)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
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
