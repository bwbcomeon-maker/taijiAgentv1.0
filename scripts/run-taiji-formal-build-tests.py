#!/usr/bin/env python3
"""Direct, unprivileged formal-build-tests/v2 driver.

The driver is intentionally self-contained: the target registry is the only
authority for formal targets, while callers provide only a source root and
already-open tool descriptors.  It never discovers alternate repositories or
accepts caller-supplied tool hashes.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import stat
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


FORMAL_TARGET_CONTRACT_BYTES = 1864
FORMAL_TARGET_CONTRACT_SHA256 = "5fdcd9335ac9c722b224c06b03d817bd505cff4abc514b09f8d9ba604c11953b"
FORMAL_SUITE_NAMES = (
    "root-runtime", "desktop-evidence-node", "kylin-install-simulation",
    "agent", "webui-runtime-lint", "webui-python",
)
FORMAL_TARGET_REGISTRY = (
    ("root-runtime", "unittest", "tests/test_taiji_license_issuer_gui.py"),
    ("desktop-evidence-node", "node-test", "tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js"),
    ("kylin-install-simulation", "unittest", "tests/test_kylin_install_script_simulation.py"),
    ("agent", "pytest", "hermes-local-lab/sources/hermes-agent/tests/tools/test_taiji_security_mode.py"),
    ("agent", "pytest", "hermes-local-lab/sources/hermes-agent/tests/test_taiji_license.py"),
    ("agent", "pytest", "hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_license.py"),
    ("agent", "pytest", "hermes-local-lab/sources/hermes-agent/tests/gateway/test_session_api.py"),
    ("agent", "pytest", "hermes-local-lab/sources/hermes-agent/tests/tools/test_image_generation_readiness.py"),
    ("webui-runtime-lint", "eslint", "hermes-local-lab/sources/hermes-webui/static/**/*.js"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_brand_privacy.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_approval_queue.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_approval_sse.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_pr1350_sse_notify_correctness.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_issue1800_file_html_interactions.py"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_writeflow_frontend.py::test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell"),
    ("webui-python", "pytest", "hermes-local-lab/sources/hermes-webui/tests/test_issue1116_composer_placeholder.py"),
)
TARGET_COUNT_KEYS = ("collected", "deselected", "executed", "passed", "failed", "errors", "skipped")
RUNNERS = frozenset(("unittest", "node-test", "pytest", "eslint"))
MAX_OUTPUT = 1024 * 1024
MAX_RESULT = 64 * 1024
SUITE_TIMEOUT_SECONDS = 3600

HELD_COMMONJS_LOADER = r"""
const fs = require("fs");
const Module = require("module");
const path = require("path");
const heldPath = process.argv[1];
const canonicalPath = process.argv[2];
const args = process.argv.slice(3);
let source = fs.readFileSync(heldPath, "utf8");
source = source.replace(/^#![^\n]*(?:\n|$)/, "\n");
process.argv = [process.execPath, canonicalPath, ...args];
const scriptModule = new Module(canonicalPath, module);
scriptModule.filename = canonicalPath;
scriptModule.paths = Module._nodeModulePaths(path.dirname(canonicalPath));
scriptModule._compile(source, canonicalPath);
""".strip()

UNITTEST_RESULT_RECORD_SOURCE = """
skipped_count = len(getattr(result, "skipped", ()))
expected_failure_count = len(getattr(result, "expectedFailures", ()))
unexpected_success_count = len(getattr(result, "unexpectedSuccesses", ()))
failed_count = (
    len(result.failures) + expected_failure_count + unexpected_success_count
)
error_count = len(result.errors)
record = {
    "ordinal": ordinal,
    "collected": collected,
    "deselected": 0,
    "executed": result.testsRun,
    "passed": result.testsRun - failed_count - error_count - skipped_count,
    "failed": failed_count,
    "errors": error_count,
    "skipped": skipped_count,
}
""".strip()


def serialize_target_registry(registry: Sequence[Tuple[str, str, str]]) -> bytes:
    if not isinstance(registry, (tuple, list)) or len(registry) != 20 or len(set(registry)) != 20:
        raise ValueError("formal target registry cardinality is not exact")
    rows = []
    for record in registry:
        if not isinstance(record, tuple) or len(record) != 3 or any(not isinstance(v, str) or not v for v in record):
            raise ValueError("formal target registry record is not canonical")
        if any(any(c in v for c in "\t\r\n\x00") for v in record):
            raise ValueError("formal target registry contains control data")
        rows.append("\t".join(record) + "\n")
    return "".join(rows).encode("utf-8")


def target_contract_sha256(registry: Sequence[Tuple[str, str, str]]) -> str:
    payload = serialize_target_registry(registry)
    if len(payload) != FORMAL_TARGET_CONTRACT_BYTES:
        raise ValueError("formal target contract byte count changed")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != FORMAL_TARGET_CONTRACT_SHA256:
        raise ValueError("formal target contract digest changed")
    return digest


def validate_target_record(record: dict, ordinal: int) -> dict:
    if not isinstance(record, dict) or tuple(record) != ("ordinal",) + TARGET_COUNT_KEYS:
        raise ValueError("formal target result keys are not exact")
    if record["ordinal"] != ordinal or isinstance(record["ordinal"], bool):
        raise ValueError("formal target ordinal is not exact")
    for key in TARGET_COUNT_KEYS:
        value = record[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("formal target count is not a nonnegative integer")
    if (record["collected"] <= 0 or record["deselected"] or
            record["executed"] != record["collected"] or
            record["passed"] != record["collected"] or
            record["failed"] or record["errors"] or record["skipped"]):
        raise ValueError("formal target did not execute completely")
    return record


def _canonical_json_line(payload: bytes) -> dict:
    if not payload.endswith(b"\n") or b"\r" in payload or b"\x00" in payload:
        raise ValueError("formal result payload is not canonical")
    lines = payload.decode("utf-8").splitlines()
    if len(lines) != 1:
        raise ValueError("formal result cardinality is not exact")
    record = json.loads(lines[0], object_pairs_hook=_strict_object)
    if json.dumps(record, separators=(",", ":"), ensure_ascii=True) != lines[0]:
        raise ValueError("formal result JSON is not canonical")
    return record


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def validate_log_lines(lines: Sequence[str]) -> None:
    if len(lines) < 2 or not lines or lines[-1] != "overall_status=pass" or lines.count("overall_status=pass") != 1:
        raise ValueError("overall status must be the unique final record")
    if any(line == "overall_status=pass" for line in lines[:-1]):
        raise ValueError("overall status appeared before cleanup")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run-taiji-formal-build-tests.py")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--python-fd", required=True, type=int)
    parser.add_argument("--node-fd", required=True, type=int)
    parser.add_argument("--npm-cli-fd", required=True, type=int)
    parser.add_argument("--eslint-fd", required=True, type=int)
    parser.add_argument("--log-fd", required=True, type=int)
    return parser


def _fd_path(fd: int) -> str:
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 3:
        raise ValueError("formal tool descriptor is unsafe")
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("formal tool descriptor is not regular")
    return "/proc/self/fd/" + str(fd)


def _inherited_fd_path(fd: int) -> str:
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 3:
        raise ValueError("formal inherited descriptor is unsafe")
    os.fstat(fd)
    return "/proc/self/fd/" + str(fd)


def _clean_environment(work: Path) -> Dict[str, str]:
    home = work / "home"
    temporary = work / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "NPM_CONFIG_USERCONFIG": "/dev/null",
        "NPM_CONFIG_GLOBALCONFIG": "/dev/null",
        "NPM_CONFIG_CACHE": str(work / "npm-cache"),
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NPM_CONFIG_IGNORE_SCRIPTS": "true",
    }


def _held_commonjs_command(
    node_fd: int,
    script_fd: int,
    logical_path: str,
    script_args: Sequence[str],
) -> Tuple[List[str], Tuple[int, ...]]:
    if (
        not isinstance(logical_path, str)
        or not logical_path.startswith("/")
        or any(character in logical_path for character in "\r\n\x00")
    ):
        raise ValueError("formal held CommonJS logical path is unsafe")
    argv = [
        _fd_path(node_fd),
        "-e",
        HELD_COMMONJS_LOADER,
        _fd_path(script_fd),
        logical_path,
    ]
    argv.extend(script_args)
    return argv, tuple(sorted((node_fd, script_fd)))


def _normalize_tool_version(kind: str, raw: str) -> str:
    value = raw.strip()
    prefixes = {"python": "Python ", "node": "v", "npm": ""}
    if kind not in prefixes:
        raise ValueError("unknown formal tool version kind")
    prefix = prefixes[kind]
    if prefix:
        if not value.startswith(prefix):
            raise ValueError("formal tool version prefix is not canonical")
        value = value[len(prefix):]
    numeric = r"(?:0|[1-9][0-9]*)"
    if re.fullmatch(numeric + r"\." + numeric + r"\." + numeric, value) is None:
        raise ValueError("formal tool version is not canonical semver")
    return value


def _same_open_file(fd: int, path: Path) -> bool:
    opened = os.fstat(fd)
    current = path.stat()
    return opened.st_dev == current.st_dev and opened.st_ino == current.st_ino


def _held_regular_path(
    candidate: Path,
    descriptor: int,
    label: str,
    executable: bool = False,
) -> Path:
    if candidate.is_symlink():
        raise ValueError("formal {} is symlinked".format(label))
    try:
        canonical = candidate.resolve(strict=True)
        current = canonical.stat()
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise ValueError("formal {} is missing".format(label)) from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or (
            executable
            and (not current.st_mode & 0o111 or not os.access(str(canonical), os.X_OK))
        )
        or opened.st_dev != current.st_dev
        or opened.st_ino != current.st_ino
    ):
        raise ValueError(
            "formal held {} identity is not canonical".format(label)
        )
    return canonical


def _python_logical_path(work: Path, python_fd: int) -> str:
    candidate = work.parent / "formal-agent-venv/bin/python"
    return str(
        _held_regular_path(
            candidate,
            python_fd,
            "Python executable",
            executable=True,
        )
    )


def _node_logical_path(work: Path, node_fd: int) -> str:
    candidate = work.parent / ".build-tools/node/current/bin/node"
    return str(
        _held_regular_path(
            candidate,
            node_fd,
            "Node executable",
            executable=True,
        )
    )


def _npm_logical_path(work: Path, node_fd: int, npm_fd: int) -> str:
    node_root = work.parent / ".build-tools/node/current"
    _node_logical_path(work, node_fd)
    return str(
        _held_regular_path(
            node_root / "lib/node_modules/npm/bin/npm-cli.js",
            npm_fd,
            "npm CLI",
        )
    )


def _eslint_logical_path(root: Path, eslint_fd: int) -> str:
    eslint_path = (
        root
        / "hermes-local-lab/sources/hermes-webui/node_modules/eslint/bin/eslint.js"
    ).resolve()
    try:
        eslint_path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("formal ESLint logical path escaped source root") from exc
    if (
        not eslint_path.is_file()
        or eslint_path.is_symlink()
        or not _same_open_file(eslint_fd, eslint_path)
    ):
        raise ValueError("formal held ESLint CLI is not the canonical source member")
    return str(eslint_path)


def _hash_fd(fd: int) -> str:
    position = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(fd, position, os.SEEK_SET)
    return digest.hexdigest()


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("formal log write was truncated")
        offset += written


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait()


def _leader_exited_without_reap(proc: subprocess.Popen) -> bool:
    if not hasattr(os, "waitid") or not hasattr(os, "WNOWAIT"):
        raise RuntimeError("formal process supervision requires waitid WNOWAIT")
    try:
        observed = os.waitid(
            os.P_PID,
            proc.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError as exc:
        raise RuntimeError("formal child was reaped outside its supervisor") from exc
    return observed is not None


def _kill_process_group(process_group: int) -> None:
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _collect_process(
    proc: subprocess.Popen,
    result_fd: int,
    deadline: float,
    stdout_limit: int,
    stderr_limit: int,
    result_limit: int,
) -> Tuple[bytes, bytes, bytes]:
    if min(stdout_limit, stderr_limit, result_limit) < 0:
        _terminate_process_group(proc)
        try:
            os.close(result_fd)
        except OSError:
            pass
        raise RuntimeError("formal child output budget is invalid")
    stream_selector = selectors.DefaultSelector()
    try:
        process_group = os.getpgid(proc.pid)
    except ProcessLookupError as exc:
        _terminate_process_group(proc)
        try:
            os.close(result_fd)
        except OSError:
            pass
        raise RuntimeError("formal child process group disappeared") from exc
    if process_group != proc.pid:
        _terminate_process_group(proc)
        try:
            os.close(result_fd)
        except OSError:
            pass
        raise RuntimeError("formal child is not its process-group leader")
    buffers = {
        "stdout": bytearray(),
        "stderr": bytearray(),
        "result": bytearray(),
    }
    limits = {
        "stdout": stdout_limit,
        "stderr": stderr_limit,
        "result": result_limit,
    }
    sources = {
        proc.stdout.fileno(): ("stdout", proc.stdout),
        proc.stderr.fileno(): ("stderr", proc.stderr),
        result_fd: ("result", result_fd),
    }
    group_cleaned = False
    try:
        for descriptor, (channel, _source) in sources.items():
            os.set_blocking(descriptor, False)
            stream_selector.register(descriptor, selectors.EVENT_READ, channel)
        while stream_selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("formal suite deadline exceeded")
            ready = stream_selector.select(min(remaining, 0.05))
            if not ready and deadline - time.monotonic() <= 0:
                raise RuntimeError("formal suite deadline exceeded")
            for key, _mask in ready:
                descriptor = key.fd
                channel = key.data
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    stream_selector.unregister(descriptor)
                    source = sources.pop(descriptor)[1]
                    if isinstance(source, int):
                        os.close(source)
                    else:
                        source.close()
                    continue
                buffers[channel].extend(chunk)
                if len(buffers[channel]) > limits[channel]:
                    raise RuntimeError("formal child output exceeded bound: " + channel)
            if not group_cleaned and _leader_exited_without_reap(proc):
                _kill_process_group(process_group)
                group_cleaned = True
        while not group_cleaned:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("formal suite deadline exceeded")
            if _leader_exited_without_reap(proc):
                _kill_process_group(process_group)
                group_cleaned = True
                break
            time.sleep(min(remaining, 0.01))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("formal suite deadline exceeded")
        proc.wait(timeout=remaining)
        return tuple(bytes(buffers[name]) for name in ("stdout", "stderr", "result"))
    except BaseException:
        _terminate_process_group(proc)
        raise
    finally:
        for descriptor, (_channel, source) in tuple(sources.items()):
            try:
                stream_selector.unregister(descriptor)
            except (KeyError, ValueError):
                pass
            try:
                if isinstance(source, int):
                    os.close(source)
                else:
                    source.close()
            except OSError:
                pass
        stream_selector.close()


def _safe_target(root: Path, target: str) -> str:
    file_part, separator, node_part = target.partition("::")
    if not file_part or target.startswith("/") or "\\" in target or any(p in ("", ".", "..") for p in Path(file_part).parts):
        raise ValueError("formal target path is unsafe")
    candidate = (root / file_part).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("formal target escaped source root") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("formal target is missing or symlinked")
    return str(candidate) + (separator + node_part if separator else "")


def _tool_version(
    fd: int,
    kind: str,
    work: Path,
    via_fd: Optional[int] = None,
    logical_path: Optional[str] = None,
) -> str:
    if kind == "python":
        argv = [
            _fd_path(fd),
            "-I",
            "-B",
            "-c",
            "import platform; print(platform.python_version())",
        ]
        pass_fds = (fd,)
    elif via_fd is not None:
        if logical_path is None:
            raise ValueError("formal held script logical path is required")
        argv, pass_fds = _held_commonjs_command(
            via_fd,
            fd,
            logical_path,
            ("--version",),
        )
    else:
        argv = [_fd_path(fd), "--version"]
        pass_fds = (fd,)
    result = subprocess.run(
        argv,
        cwd=str(work),
        env=_clean_environment(work),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
        pass_fds=pass_fds,
    )
    if result.returncode:
        raise RuntimeError("formal tool version probe failed")
    line = (result.stdout or result.stderr).splitlines()
    if len(line) != 1:
        raise RuntimeError("formal tool version probe cardinality is not exact")
    raw = line[0].strip()
    if kind == "python":
        raw = "Python " + raw
    return _normalize_tool_version(kind, raw)


def _parse_eslint_result(payload: bytes, ordinal: int, suite_root: Path) -> dict:
    try:
        reports = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("formal ESLint result is not valid JSON") from exc
    if not isinstance(reports, list) or not reports:
        raise ValueError("formal ESLint matched zero files")
    observed = set()
    failed = 0
    errors = 0
    static_root = (suite_root / "static").resolve()
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError("formal ESLint report is not an object")
        file_path = report.get("filePath")
        if not isinstance(file_path, str) or not file_path:
            raise ValueError("formal ESLint report file identity is missing")
        candidate = Path(file_path).resolve()
        try:
            candidate.relative_to(static_root)
        except ValueError as exc:
            raise ValueError("formal ESLint report escaped static root") from exc
        if (
            candidate in observed
            or not candidate.is_file()
            or candidate.is_symlink()
            or candidate.suffix != ".js"
        ):
            raise ValueError("formal ESLint report file identity is unsafe")
        observed.add(candidate)
        error_count = report.get("errorCount")
        fatal_count = report.get("fatalErrorCount")
        if (
            not isinstance(error_count, int)
            or isinstance(error_count, bool)
            or error_count < 0
            or not isinstance(fatal_count, int)
            or isinstance(fatal_count, bool)
            or fatal_count < 0
            or fatal_count > error_count
        ):
            raise ValueError("formal ESLint report counts are invalid")
        if fatal_count:
            errors += 1
        elif error_count:
            failed += 1
    collected = len(reports)
    record = {
        "ordinal": ordinal,
        "collected": collected,
        "deselected": 0,
        "executed": collected,
        "passed": collected - failed - errors,
        "failed": failed,
        "errors": errors,
        "skipped": 0,
    }
    return validate_target_record(record, ordinal)


def _run_target(
    runner: str,
    target: str,
    root: Path,
    fd_map: Dict[str, int],
    ordinal: int,
    work: Path,
    deadline: Optional[float] = None,
    stdout_limit: int = MAX_OUTPUT,
    stderr_limit: int = MAX_OUTPUT,
) -> Tuple[dict, bytes, bytes]:
    if deadline is None:
        deadline = time.monotonic() + SUITE_TIMEOUT_SECONDS
    python = _fd_path(fd_map["python"])
    node = _fd_path(fd_map["node"])
    suite_root = root
    suite_target = target
    if target.startswith("hermes-local-lab/sources/hermes-agent/"):
        suite_root = root / "hermes-local-lab/sources/hermes-agent"
        suite_target = target[len("hermes-local-lab/sources/hermes-agent/"):]
    elif target.startswith("hermes-local-lab/sources/hermes-webui/"):
        suite_root = root / "hermes-local-lab/sources/hermes-webui"
        suite_target = target[len("hermes-local-lab/sources/hermes-webui/"):]
    if runner == "eslint":
        if target.startswith("/") or "\\" in target or ".." in Path(target).parts:
            raise ValueError("formal eslint target path is unsafe")
        target_path = suite_target
    else:
        target_path = _safe_target(root, target)
    env = _clean_environment(work)
    if runner == "pytest":
        python_logical = _python_logical_path(work, fd_map["python"])
        node_logical = _node_logical_path(work, fd_map["node"])
        env["PATH"] = str(Path(node_logical).parent) + ":/usr/bin:/bin"
        if suite_root.name == "hermes-webui":
            agent_candidate = root / "hermes-local-lab/sources/hermes-agent"
            if not agent_candidate.is_dir() or agent_candidate.is_symlink():
                raise ValueError("formal WebUI Agent source root is unsafe")
            agent_root = agent_candidate.resolve()
            try:
                agent_root.relative_to(root.resolve())
            except ValueError as exc:
                raise ValueError(
                    "formal WebUI Agent source root escaped source root"
                ) from exc
            env["HERMES_WEBUI_AGENT_DIR"] = str(agent_root)
            env["HERMES_WEBUI_PYTHON"] = python_logical
    elif target == "tests/test_taiji_license_issuer_gui.py":
        env["TAIJI_AGENT_PYTHON"] = _python_logical_path(
            work, fd_map["python"]
        )
        env["TAIJI_TEST_NODE"] = _node_logical_path(work, fd_map["node"])
    scratch = None
    result_read = -1
    result_write = -1
    proc = None
    try:
        if runner == "pytest":
            scratch = Path(
                tempfile.mkdtemp(
                    prefix="target-{:02d}-".format(ordinal),
                    dir=str(work / "tmp"),
                )
            ).resolve()
        result_read, result_write = os.pipe()
        if runner == "unittest":
            code = """import importlib.util
import json
import os
import sys
import unittest

spec = importlib.util.spec_from_file_location("taiji_formal_target", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("cannot load unittest target file")
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.defaultTestLoader.loadTestsFromModule(module)
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
collected = suite.countTestCases()
ordinal = int(sys.argv[2])
""" + UNITTEST_RESULT_RECORD_SOURCE + """
with open("/proc/self/fd/" + sys.argv[3], "w", encoding="utf-8") as output:
    json.dump(record, output, separators=(",", ":"))
    output.write("\\n")
"""
            argv = [python, "-I", "-B", "-c", code, target_path, str(ordinal), str(result_write)]
            pass_fds = (fd_map["python"], result_write)
            child_cwd = root
        elif runner == "node-test":
            code = "const fs=require('node:fs');const {run}=require('node:test');(async()=>{let s;for await(const e of run({files:[process.argv[1]],concurrency:false}))if(e.type==='test:summary')s=e.data;if(!s)throw Error('missing summary');let c=s.counts,r={ordinal:+process.argv[2],collected:c.tests,deselected:0,executed:c.tests,passed:c.passed,failed:c.failed,errors:c.cancelled,skipped:c.skipped+c.todo};fs.writeFileSync('/proc/self/fd/'+process.argv[3],JSON.stringify(r)+'\\n');if(!s.success)process.exitCode=1})().catch(e=>{console.error(e);process.exitCode=1})"
            argv = [node, "-e", code, target_path, str(ordinal), str(result_write)]
            pass_fds = (fd_map["node"], result_write)
            child_cwd = root
        elif runner == "pytest":
            runner_path = (
                root
                / "hermes-local-lab/sources/hermes-agent/scripts/run_tests_parallel.py"
            )
            if not runner_path.is_file() or runner_path.is_symlink():
                raise ValueError("formal pytest runner is missing or unsafe")
            argv = [python, "-I", "-B", str(runner_path), "--no-duration-cache", "--require-nonempty-explicit-files", "--formal-results-fd", str(result_write), "--formal-first-ordinal", str(ordinal), "--formal-test-root", str(suite_root), suite_target]
            pass_fds = (fd_map["python"], result_write)
            child_cwd = scratch
        elif runner == "eslint":
            config_path = (suite_root / "eslint.runtime-guard.config.mjs").resolve()
            if not config_path.is_file() or config_path.is_symlink():
                raise ValueError("formal ESLint config is missing or unsafe")
            logical_path = _eslint_logical_path(root, fd_map["eslint"])
            eslint_args = (
                "--no-config-lookup",
                "-c",
                str(config_path),
                "--format",
                "json",
                "--output-file",
                _inherited_fd_path(result_write),
                target_path,
            )
            argv, held_fds = _held_commonjs_command(
                fd_map["node"],
                fd_map["eslint"],
                logical_path,
                eslint_args,
            )
            pass_fds = held_fds + (result_write,)
            child_cwd = suite_root
        else:
            raise ValueError("unknown formal runner")
        pass_fds = tuple(sorted(set(pass_fds)))
        proc = subprocess.Popen(
            argv,
            cwd=str(child_cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        os.close(result_write)
        result_write = -1
        try:
            stdout, stderr, payload = _collect_process(
                proc,
                result_read,
                deadline,
                stdout_limit,
                stderr_limit,
                MAX_RESULT,
            )
        finally:
            result_read = -1
        if proc.returncode != 0:
            raise RuntimeError("formal target failed: " + target)
        if runner == "eslint":
            record = _parse_eslint_result(payload, ordinal, suite_root)
        else:
            record = validate_target_record(_canonical_json_line(payload), ordinal)
        return record, stdout, stderr
    finally:
        for descriptor in (result_write, result_read):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if proc is not None and proc.poll() is None:
            _terminate_process_group(proc)
        if scratch is not None:
            shutil.rmtree(str(scratch))


def run(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        return 2
    root = Path(args.source_root).resolve(); work = Path(args.work_root).resolve()
    if not root.is_dir() or root.is_symlink() or not work.is_dir() or work.is_symlink():
        return 2
    fd_map = {"python": args.python_fd, "node": args.node_fd, "npm": args.npm_cli_fd, "eslint": args.eslint_fd}
    hashes = {name: _hash_fd(fd) for name, fd in fd_map.items()}
    npm_logical_path = _npm_logical_path(work, fd_map["node"], fd_map["npm"])
    versions = {
        "python": _tool_version(fd_map["python"], "python", work),
        "node": _tool_version(fd_map["node"], "node", work),
        "npm": _tool_version(
            fd_map["npm"],
            "npm",
            work,
            via_fd=fd_map["node"],
            logical_path=npm_logical_path,
        ),
    }
    lines = ["schema=taiji-formal-build-tests/v2", "source_commit=" + args.source_commit, "python_version=" + versions["python"], "python_executable_sha256=" + hashes["python"], "node_version=" + versions["node"], "node_executable_sha256=" + hashes["node"], "npm_version=" + versions["npm"], "npm_cli_sha256=" + hashes["npm"], "eslint_cli_sha256=" + hashes["eslint"], "target_count=20", "target_contract_sha256=" + target_contract_sha256(FORMAL_TARGET_REGISTRY)]
    try:
        ordinal = 0
        while ordinal < len(FORMAL_TARGET_REGISTRY):
            suite = FORMAL_TARGET_REGISTRY[ordinal][0]
            suite_deadline = time.monotonic() + SUITE_TIMEOUT_SECONDS
            suite_stdout = bytearray()
            suite_stderr = bytearray()
            suite_records = []
            target_lines = []
            lines.append("suite_begin=" + suite)
            while (
                ordinal < len(FORMAL_TARGET_REGISTRY)
                and FORMAL_TARGET_REGISTRY[ordinal][0] == suite
            ):
                _suite, runner, target = FORMAL_TARGET_REGISTRY[ordinal]
                record, stdout, stderr = _run_target(
                    runner,
                    target,
                    root,
                    fd_map,
                    ordinal,
                    work,
                    deadline=suite_deadline,
                    stdout_limit=MAX_OUTPUT - len(suite_stdout),
                    stderr_limit=MAX_OUTPUT - len(suite_stderr),
                )
                suite_stdout.extend(stdout)
                suite_stderr.extend(stderr)
                suite_records.append(record)
                target_lines.append(
                    "target_result="
                    + str(ordinal)
                    + "\t"
                    + suite
                    + "\t"
                    + runner
                    + "\t"
                    + target
                    + "\t"
                    + "\t".join(str(record[key]) for key in TARGET_COUNT_KEYS)
                )
                ordinal += 1
            for channel, payload in (
                ("stdout", bytes(suite_stdout)),
                ("stderr", bytes(suite_stderr)),
            ):
                if payload:
                    lines.append(
                        "child_output="
                        + suite
                        + "\t"
                        + channel
                        + "\t"
                        + base64.b64encode(payload).decode("ascii")
                    )
            lines.extend(target_lines)
            totals = [
                sum(record[key] for record in suite_records)
                for key in TARGET_COUNT_KEYS
            ]
            lines.append(
                "suite_counts="
                + suite
                + "\t"
                + str(len(suite_records))
                + "\t"
                + "\t".join(map(str, totals))
            )
            lines.append("suite_status=" + suite + ":pass")
        lines.append("overall_status=pass")
        validate_log_lines(lines)
        _write_all(args.log_fd, ("\n".join(lines) + "\n").encode("utf-8"))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        try:
            _write_all(args.log_fd, ("schema=taiji-formal-build-tests/v2\nformal_error=" + str(exc) + "\n").encode("utf-8"))
        except OSError:
            pass
        return 1


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except SystemExit:
        raise
    except (OSError, ValueError, RuntimeError):
        return 2


if __name__ == "__main__":
    sys.exit(main())
