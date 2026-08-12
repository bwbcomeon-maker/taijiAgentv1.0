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
import subprocess
import sys
import time
import stat
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


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


def _safe_target(root: Path, target: str) -> str:
    file_part = target.split("::", 1)[0]
    if not file_part or target.startswith("/") or "\\" in target or any(p in ("", ".", "..") for p in Path(file_part).parts):
        raise ValueError("formal target path is unsafe")
    candidate = (root / file_part).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("formal target escaped source root") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("formal target is missing or symlinked")
    return str(candidate)


def _tool_version(fd: int, flag: str = "--version", via_fd: int = None) -> str:
    path = _fd_path(fd)
    argv = [_fd_path(via_fd), path, flag] if via_fd is not None else [path, flag]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=False, pass_fds=tuple(x for x in (fd, via_fd) if x is not None))
    if result.returncode:
        raise RuntimeError("formal tool version probe failed")
    line = (result.stdout or result.stderr).splitlines()
    return line[0].strip() if line else "unknown"


def _run_target(runner: str, target: str, root: Path, fd_map: Dict[str, int], ordinal: int, work: Path) -> Tuple[dict, bytes, bytes]:
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
    result_read, result_write = os.pipe()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(work / "home"), "TMPDIR": str(work / "tmp"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONHASHSEED": "0", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    os.makedirs(env["HOME"], exist_ok=True); os.makedirs(env["TMPDIR"], exist_ok=True)
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
record = {
    "ordinal": int(sys.argv[2]),
    "collected": collected,
    "deselected": 0,
    "executed": result.testsRun,
    "passed": result.testsRun - len(result.failures) - len(result.errors),
    "failed": len(result.failures),
    "errors": len(result.errors),
    "skipped": len(getattr(result, "skipped", ())),
}
with open("/proc/self/fd/" + sys.argv[3], "w", encoding="utf-8") as output:
    json.dump(record, output, separators=(",", ":"))
    output.write("\\n")
"""
        argv = [python, "-I", "-B", "-c", code, target_path, str(ordinal), str(result_write)]
    elif runner == "node-test":
        code = "const fs=require('node:fs');const {run}=require('node:test');(async()=>{let s;for await(const e of run({files:[process.argv[1]],concurrency:false}))if(e.type==='test:summary')s=e.data;if(!s)throw Error('missing summary');let c=s.counts,r={ordinal:+process.argv[2],collected:c.tests,deselected:0,executed:c.tests,passed:c.passed,failed:c.failed,errors:c.cancelled,skipped:c.skipped+c.todo};fs.writeFileSync('/proc/self/fd/'+process.argv[3],JSON.stringify(r)+'\\n');if(!s.success)process.exitCode=1})().catch(e=>{console.error(e);process.exitCode=1})"
        argv = [node, "-e", code, target_path, str(ordinal), str(result_write)]
    elif runner == "pytest":
        runner_path = suite_root / "scripts/run_tests_parallel.py"
        argv = [python, "-I", "-B", str(runner_path), "--no-duration-cache", "--require-nonempty-explicit-files", "--formal-results-fd", str(result_write), "--formal-first-ordinal", str(ordinal), "--formal-test-root", str(suite_root), suite_target]
    elif runner == "eslint":
        node_code = "const fs=require('node:fs');const {ESLint}=require('eslint');(async()=>{let e=new ESLint({cwd:process.argv[1],overrideConfigFile:process.argv[2],errorOnUnmatchedPattern:true}),a=await e.lintFiles([process.argv[3]]);if(!a.length)throw Error('eslint matched zero files');let r={ordinal:+process.argv[4],collected:a.length,deselected:0,executed:a.length,passed:a.filter(x=>!x.errorCount).length,failed:a.filter(x=>x.errorCount&&!x.fatalErrorCount).length,errors:a.filter(x=>x.fatalErrorCount).length,skipped:0};fs.writeFileSync('/proc/self/fd/'+process.argv[5],JSON.stringify(r)+'\\n');if(r.failed||r.errors)process.exitCode=1})().catch(e=>{console.error(e);process.exitCode=1})"
        argv = [node, "-e", node_code, str(suite_root), str(suite_root / "eslint.runtime-guard.config.mjs"), target_path, str(ordinal), str(result_write)]
    else:
        raise ValueError("unknown formal runner")
    pass_fds = tuple(sorted(set((result_write,) + tuple(fd_map.values()))))
    proc = subprocess.Popen(argv, cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=pass_fds)
    os.close(result_write)
    try:
        stdout, stderr = proc.communicate(timeout=3600)
    except subprocess.TimeoutExpired:
        proc.kill(); stdout, stderr = proc.communicate(); raise RuntimeError("formal target deadline exceeded")
    payload = os.read(result_read, MAX_RESULT + 1)
    os.close(result_read)
    if len(stdout) > MAX_OUTPUT or len(stderr) > MAX_OUTPUT or len(payload) > MAX_RESULT:
        raise RuntimeError("formal target output exceeded bound")
    if proc.returncode != 0:
        raise RuntimeError("formal target failed: " + target)
    record = validate_target_record(_canonical_json_line(payload), ordinal)
    return record, stdout, stderr


def run(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        return 2
    root = Path(args.source_root).resolve(); work = Path(args.work_root).resolve()
    if not root.is_dir() or root.is_symlink() or not work.is_dir() or work.is_symlink():
        return 2
    fd_map = {"python": args.python_fd, "node": args.node_fd, "npm": args.npm_cli_fd, "eslint": args.eslint_fd}
    hashes = {name: _hash_fd(fd) for name, fd in fd_map.items()}
    versions = {
        "python": _tool_version(fd_map["python"]),
        "node": _tool_version(fd_map["node"]),
        "npm": _tool_version(fd_map["npm"], via_fd=fd_map["node"]),
    }
    lines = ["schema=taiji-formal-build-tests/v2", "source_commit=" + args.source_commit, "python_version=" + versions["python"], "python_executable_sha256=" + hashes["python"], "node_version=" + versions["node"], "node_executable_sha256=" + hashes["node"], "npm_version=" + versions["npm"], "npm_cli_sha256=" + hashes["npm"], "eslint_cli_sha256=" + hashes["eslint"], "target_count=20", "target_contract_sha256=" + target_contract_sha256(FORMAL_TARGET_REGISTRY)]
    try:
        for ordinal, (suite, runner, target) in enumerate(FORMAL_TARGET_REGISTRY):
            if ordinal == 0 or FORMAL_TARGET_REGISTRY[ordinal - 1][0] != suite:
                lines.append("suite_begin=" + suite)
            record, stdout, stderr = _run_target(runner, target, root, fd_map, ordinal, work)
            for channel, payload in (("stdout", stdout), ("stderr", stderr)):
                if payload:
                    lines.append("child_output=" + suite + "\t" + channel + "\t" + base64.b64encode(payload).decode("ascii"))
            lines.append("target_result=" + str(ordinal) + "\t" + suite + "\t" + runner + "\t" + target + "\t" + "\t".join(str(record[k]) for k in TARGET_COUNT_KEYS))
            next_suite = FORMAL_TARGET_REGISTRY[ordinal + 1][0] if ordinal + 1 < len(FORMAL_TARGET_REGISTRY) else None
            if next_suite != suite:
                suite_records = [r for r in []]  # aggregate from emitted target lines below
                target_lines = [x for x in lines if x.startswith("target_result=") and x.split("\t", 2)[1] == suite]
                totals = [sum(int(x.split("\t")[4 + i]) for x in target_lines) for i in range(7)]
                lines.append("suite_counts=" + suite + "\t" + str(len(target_lines)) + "\t" + "\t".join(map(str, totals)))
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
