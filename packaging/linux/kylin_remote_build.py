#!/usr/bin/env python3
"""Resume one remote Kylin ``00`` build without starting it twice.

The local side only queries and reconciles the fixed result file.  The remote
launcher publishes a complete ``RUNNING`` record with a no-clobber link before
detaching the worker; the worker owns the existing ``99 -> 00 -> review``
sequence and atomically publishes its terminal record.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


RESULT_SCHEMA = "taiji-kylin-remote-build-result/v1"
POLL_INTERVAL_SECONDS = 300
RESULT_BASENAME = "remote-build-result.json"
REMOTE_LOG_BASENAME = "02-remote-build.log"
MAX_RESULT_BYTES = 256 * 1024
MAX_DECLARED_FILE_BYTES = 2 * 1024 * 1024 * 1024
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT_RE = re.compile(r"^[0-9a-f]{16}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REMOTE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
STAT_RESULT_ABSENT = 3
STAT_RESULT_UNSAFE = 4


class RemoteBuildError(RuntimeError):
    """Raised for any malformed, unsafe, or unreconciled remote state."""


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RemoteBuildError("remote build result contains duplicate key: {}".format(key))
        result[key] = value
    return result


def _reject_json_constant(value: str):
    raise RemoteBuildError("remote build result contains non-standard JSON constant: {}".format(value))


def _parse_raw_json(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, (bytes, bytearray)):
        raise RemoteBuildError("remote build result must be UTF-8 JSON bytes")
    try:
        text = bytes(raw).decode("utf-8", "strict")
    except UnicodeError as exc:
        raise RemoteBuildError("remote build result is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except RemoteBuildError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RemoteBuildError("remote build result is not valid JSON") from exc
    if type(value) is not dict:
        raise RemoteBuildError("remote build result must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set, label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise RemoteBuildError(
            "{} keys mismatch; missing={} extra={}".format(
                label, sorted(expected - actual), sorted(actual - expected)
            )
        )


def _require_text(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise RemoteBuildError("{} must be a non-empty string".format(label))
    return value


def _validate_commit(value: Any, label: str) -> str:
    value = _require_text(value, label)
    if not COMMIT_RE.fullmatch(value):
        raise RemoteBuildError("{} is not a full lowercase commit".format(label))
    return value


def _validate_attempt(value: Any, label: str) -> str:
    value = _require_text(value, label)
    if not ATTEMPT_RE.fullmatch(value):
        raise RemoteBuildError("{} is not a 16-character attempt id".format(label))
    return value


def _validate_basename(value: Any, label: str) -> str:
    value = _require_text(value, label)
    if (
        len(value.encode("utf-8")) > 255
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or Path(value).name != value
    ):
        raise RemoteBuildError("{} is not a safe basename".format(label))
    return value


def _validate_bytes(value: Any, label: str, *, allow_zero: bool) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1) or value > MAX_DECLARED_FILE_BYTES:
        raise RemoteBuildError("{} is outside the bounded byte range".format(label))
    return value


def _validate_sha256(value: Any, label: str) -> str:
    value = _require_text(value, label)
    if not SHA256_RE.fullmatch(value):
        raise RemoteBuildError("{} is not a lowercase SHA256".format(label))
    return value


def _validate_file_identity(value: Any, label: str, *, allow_zero: bool) -> Dict[str, Any]:
    if type(value) is not dict:
        raise RemoteBuildError("{} must be an object".format(label))
    _require_exact_keys(value, {"basename", "bytes", "sha256"}, label)
    return {
        "basename": _validate_basename(value["basename"], label + ".basename"),
        "bytes": _validate_bytes(value["bytes"], label + ".bytes", allow_zero=allow_zero),
        "sha256": _validate_sha256(value["sha256"], label + ".sha256"),
    }


def _validate_timestamp(value: Any, label: str) -> _datetime.datetime:
    value = _require_text(value, label)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise RemoteBuildError("{} is not a UTC timestamp".format(label))
    try:
        parsed = _datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise RemoteBuildError("{} is not a valid UTC timestamp".format(label)) from exc
    return parsed.replace(tzinfo=_datetime.timezone.utc)


def _expected_input_identity(source_commit: str) -> Dict[str, Dict[str, Any]]:
    archive = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    return {
        "archive": {"basename": archive},
        "manifest": {"basename": archive[:-7] + ".manifest.json"},
        "checksum": {"basename": archive + ".sha256"},
    }


def _normalise_expected_input_identity(value: Any) -> Dict[str, Dict[str, Any]]:
    if type(value) is not dict:
        raise RemoteBuildError("input identity must be an object")
    _require_exact_keys(value, {"archive", "manifest", "checksum"}, "input identity")
    result = {}
    for key in ("archive", "manifest", "checksum"):
        item = value[key]
        if type(item) is not dict:
            raise RemoteBuildError("input identity.{} must be an object".format(key))
        if set(item) == {"basename", "bytes", "sha256"}:
            result[key] = _validate_file_identity(item, "input identity." + key, allow_zero=False)
        elif set(item) == {"path", "basename", "size", "sha256"}:
            # The orchestrator stores ``size`` and ``path`` in its checkpoint;
            # the remote result deliberately publishes the smaller exact trio.
            if type(item["path"]) is not str or not item["path"].startswith("/"):
                raise RemoteBuildError("input identity.{}.path is invalid".format(key))
            result[key] = {
                "basename": _validate_basename(item["basename"], "input identity." + key + ".basename"),
                "bytes": _validate_bytes(item["size"], "input identity." + key + ".size", allow_zero=False),
                "sha256": _validate_sha256(item["sha256"], "input identity." + key + ".sha256"),
            }
        else:
            raise RemoteBuildError("input identity.{} has an invalid shape".format(key))
    return result


def _validate_result(value: Dict[str, Any], *, source_commit: str, remote_attempt_id: str, input_identity: Any) -> Dict[str, Any]:
    _require_exact_keys(
        value,
        {
            "schema",
            "source_commit",
            "remote_attempt_id",
            "input",
            "status",
            "phase",
            "exit_code",
            "started_at",
            "finished_at",
            "remote_log",
        },
        "remote build result",
    )
    source_commit = _validate_commit(source_commit, "expected source_commit")
    remote_attempt_id = _validate_attempt(remote_attempt_id, "expected remote_attempt_id")
    if value["schema"] != RESULT_SCHEMA:
        raise RemoteBuildError("remote build result schema is unsupported")
    if value["source_commit"] != source_commit:
        raise RemoteBuildError("remote build result source identity mismatches")
    if value["remote_attempt_id"] != remote_attempt_id:
        raise RemoteBuildError("remote build result attempt identity mismatches")

    expected_input = _normalise_expected_input_identity(input_identity)
    actual_input = value["input"]
    if type(actual_input) is not dict:
        raise RemoteBuildError("remote build result input must be an object")
    _require_exact_keys(actual_input, {"archive", "manifest", "checksum"}, "remote build result input")
    normalised_input = {
        key: _validate_file_identity(actual_input[key], "remote build result input." + key, allow_zero=False)
        for key in ("archive", "manifest", "checksum")
    }
    for key in ("archive", "manifest", "checksum"):
        if normalised_input[key] != expected_input[key]:
            raise RemoteBuildError("remote build result input identity mismatches {}".format(key))
    expected_names = _expected_input_identity(source_commit)
    for key in expected_names:
        if normalised_input[key]["basename"] != expected_names[key]["basename"]:
            raise RemoteBuildError("remote build result input basename is not source-bound")

    status = value["status"]
    if type(status) is not str or status not in {"RUNNING", "FAILED", "SUCCEEDED"}:
        raise RemoteBuildError("remote build result status is unknown")
    phase = value["phase"]
    if type(phase) is not str or phase not in {"00", "review"}:
        raise RemoteBuildError("remote build result phase is unknown")
    exit_code = value["exit_code"]
    if exit_code is not None and (type(exit_code) is not int or exit_code < 0 or exit_code > 255):
        raise RemoteBuildError("remote build result exit_code is invalid")
    started_at = _validate_timestamp(value["started_at"], "remote build result started_at")
    finished_raw = value["finished_at"]
    if finished_raw is None:
        finished_at = None
    else:
        finished_at = _validate_timestamp(finished_raw, "remote build result finished_at")
        if finished_at < started_at:
            raise RemoteBuildError("remote build result finished_at precedes started_at")
    remote_log = _validate_file_identity(value["remote_log"], "remote build result remote_log", allow_zero=True)
    if remote_log["basename"] != REMOTE_LOG_BASENAME:
        raise RemoteBuildError("remote build result remote_log basename is not fixed")
    if status == "RUNNING":
        if exit_code is not None or finished_at is not None:
            raise RemoteBuildError("RUNNING result has terminal fields")
    elif finished_at is None or exit_code is None:
        raise RemoteBuildError("terminal result is missing exit_code or finished_at")
    elif status == "FAILED" and exit_code == 0:
        raise RemoteBuildError("FAILED result must have a nonzero exit_code")
    elif status == "SUCCEEDED" and exit_code != 0:
        raise RemoteBuildError("SUCCEEDED result must have exit_code zero")
    return {
        "schema": RESULT_SCHEMA,
        "source_commit": source_commit,
        "remote_attempt_id": remote_attempt_id,
        "input": normalised_input,
        "status": status,
        "phase": phase,
        "exit_code": exit_code,
        "started_at": value["started_at"],
        "finished_at": finished_raw,
        "remote_log": remote_log,
    }


def load_remote_build_result(raw, *, source_commit, remote_attempt_id, input_identity):
    """Parse and validate one complete remote result without fallback-to-missing."""

    value = _parse_raw_json(raw)
    return _validate_result(
        value,
        source_commit=source_commit,
        remote_attempt_id=remote_attempt_id,
        input_identity=input_identity,
    )


def decide_remote_build_action(result):
    """Return the only legal reconciliation action for a validated result."""

    if result is None:
        return "START"
    if type(result) is not dict:
        raise RemoteBuildError("remote build decision requires None or a result object")
    status = result.get("status")
    if status == "RUNNING":
        return "POLL"
    if status == "FAILED":
        return "FAIL"
    if status == "SUCCEEDED":
        return "CONTINUE"
    raise RemoteBuildError("remote build decision received an unvalidated result")


def _validate_remote_path(value: str, label: str) -> str:
    if type(value) is not str or not REMOTE_PATH_RE.fullmatch(value) or ".." in Path(value).parts:
        raise RemoteBuildError("{} is not a safe absolute remote path".format(label))
    return value.rstrip("/") or "/"


def _validate_host(value: str) -> str:
    if type(value) is not str or not HOST_RE.fullmatch(value):
        raise RemoteBuildError("remote host is invalid")
    return value


def _validate_socket_environment() -> Dict[str, str]:
    value = os.environ.get("SSH_AUTH_SOCK")
    if value is None:
        return {}
    if not value.startswith("/") or "\x00" in value:
        raise RemoteBuildError("SSH_AUTH_SOCK is not an absolute path")
    try:
        metadata = os.lstat(value)
    except OSError as exc:
        raise RemoteBuildError("SSH_AUTH_SOCK is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISSOCK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RemoteBuildError("SSH_AUTH_SOCK is not a trusted current-user socket")
    return {"SSH_AUTH_SOCK": value}


def _replacement_environment(account_home: str) -> Dict[str, str]:
    environment = {
        "HOME": account_home,
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/tmp",
    }
    environment.update(_validate_socket_environment())
    return environment


def _remote_shell(account_home: str, script: str) -> str:
    return "/usr/bin/env -i HOME={} PATH=/usr/bin:/bin LANG=C LC_ALL=C TMPDIR=/tmp /bin/bash -p -c {}".format(
        _shell_quote(account_home), _shell_quote(script)
    )


def _shell_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _ssh(host: str, account_home: str, script: str, environment: Dict[str, str]) -> subprocess.CompletedProcess:
    argv = ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, _remote_shell(account_home, script)]
    try:
        return subprocess.run(
            argv,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RemoteBuildError("fixed SSH command failed to execute") from exc


def _query_script(remote_dir: str, result_basename: str) -> str:
    result_path = remote_dir.rstrip("/") + "/" + result_basename
    quoted = _shell_quote(result_path)
    return "".join(
        [
            "set -Eeuo pipefail; ",
            "if [ ! -e {} ] && [ ! -L {} ]; then exit {}; fi; ".format(quoted, quoted, STAT_RESULT_ABSENT),
            "if [ -L {} ] || [ ! -f {} ]; then exit {}; fi; ".format(quoted, quoted, STAT_RESULT_UNSAFE),
            "metadata=$(/usr/bin/stat -c '%u %a %h %s' -- {}); ".format(quoted),
            "set -- $metadata; ",
            "[ $# -eq 4 ] || exit {}; ".format(STAT_RESULT_UNSAFE),
            "[ \"$1\" = \"$(/usr/bin/id -u)\" ] || exit {}; ".format(STAT_RESULT_UNSAFE),
            "[ \"$2\" = 600 ] && [ \"$3\" = 1 ] || exit {}; ".format(STAT_RESULT_UNSAFE),
            "[ \"$4\" -gt 0 ] && [ \"$4\" -le {} ] || exit {}; ".format(MAX_RESULT_BYTES, STAT_RESULT_UNSAFE),
            "/usr/bin/cat -- {}".format(quoted),
        ]
    )


def _query_remote(host: str, account_home: str, remote_dir: str, result_basename: str, source_commit: str, remote_attempt_id: str, input_identity: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    completed = _ssh(host, account_home, _query_script(remote_dir, result_basename), _replacement_environment(account_home))
    if completed.returncode == STAT_RESULT_ABSENT:
        if completed.stdout:
            raise RemoteBuildError("remote result query claimed absent but returned bytes")
        return None
    if completed.returncode == STAT_RESULT_UNSAFE:
        raise RemoteBuildError("remote result query found an unsafe result file")
    if completed.returncode != 0:
        raise RemoteBuildError("remote result query failed; state is not absent")
    if len(completed.stdout) > MAX_RESULT_BYTES:
        raise RemoteBuildError("remote result query exceeded its bound")
    return load_remote_build_result(
        completed.stdout,
        source_commit=source_commit,
        remote_attempt_id=remote_attempt_id,
        input_identity=input_identity,
    )


def _result_printf(input_identity: Dict[str, Dict[str, Any]], *, status: str, phase: str, exit_code: str, started_var: str, finished_var: str, log_bytes_var: str, log_sha_var: str) -> str:
    finished_format = "%s" if finished_var == "null" else '"%s"'
    format_string = (
        '{"schema":"%s","source_commit":"%s","remote_attempt_id":"%s",'
        '"input":{"archive":{"basename":"%s","bytes":%s,"sha256":"%s"},'
        '"checksum":{"basename":"%s","bytes":%s,"sha256":"%s"},'
        '"manifest":{"basename":"%s","bytes":%s,"sha256":"%s"}},'
        '"status":"%s","phase":"%s","exit_code":%s,"started_at":"%s",'
        '"finished_at":__FINISHED_AT__,"remote_log":{"basename":"%s","bytes":%s,"sha256":"%s"}}\n'
    )
    format_string = format_string.replace("__FINISHED_AT__", finished_format)
    # Values that are shell variables are intentionally supplied as printf
    # arguments; all other values were validated as fixed ASCII/Unicode names.
    # The worker substitutes the two timestamp variables and terminal code.
    ordered = [
        RESULT_SCHEMA,
        "$source_commit",
        "$remote_attempt_id",
        input_identity["archive"]["basename"],
        str(input_identity["archive"]["bytes"]),
        input_identity["archive"]["sha256"],
        input_identity["checksum"]["basename"],
        str(input_identity["checksum"]["bytes"]),
        input_identity["checksum"]["sha256"],
        input_identity["manifest"]["basename"],
        str(input_identity["manifest"]["bytes"]),
        input_identity["manifest"]["sha256"],
        status,
        phase,
        exit_code,
        started_var,
        finished_var,
        REMOTE_LOG_BASENAME,
        log_bytes_var,
        log_sha_var,
    ]

    def shell_argument(value: str) -> str:
        if value.startswith("$"):
            return '"{}"'.format(value)
        return _shell_quote(value)

    return "printf {} {}".format(
        _shell_quote(format_string),
        " ".join(shell_argument(value) for value in ordered),
    )


def _worker_script(remote_dir: str, source_commit: str, remote_attempt_id: str, input_identity: Dict[str, Dict[str, Any]], started_at: str) -> str:
    delivery = remote_dir.rstrip("/") + "/taijiagent 打包交付"
    remote_log = remote_dir.rstrip("/") + "/" + REMOTE_LOG_BASENAME
    result_path = remote_dir.rstrip("/") + "/" + RESULT_BASENAME
    archive = input_identity["archive"]["basename"]
    checksum = input_identity["checksum"]["basename"]
    source_archive = "taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(source_commit)
    # The worker keeps the existing 00 semantics and only adds result updates.
    return "\n".join(
        [
            "set -Eeuo pipefail",
            "umask 077",
            "phase=00",
            "finalized=0",
            "source_commit={}".format(_shell_quote(source_commit)),
            "remote_attempt_id={}".format(_shell_quote(remote_attempt_id)),
            "started_at=\"${TAIJI_REMOTE_STARTED_AT:?}\"",
            "remote_dir={}".format(_shell_quote(remote_dir)),
            "result_path={}".format(_shell_quote(result_path)),
            "remote_log={}".format(_shell_quote(remote_log)),
            "write_terminal_result() {",
            "  terminal_status=\"$1\"",
            "  terminal_phase=\"$2\"",
            "  terminal_code=\"$3\"",
            "  finished_at=$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')",
            "  log_bytes=$(/usr/bin/stat -c '%s' -- \"$remote_log\")",
            "  log_sha=$(/usr/bin/sha256sum -- \"$remote_log\" | /usr/bin/awk '{print $1}')",
            "  temporary=\"$remote_dir/.remote-build-result.json.worker.$$\"",
            "  (set -C; {} ) > \"$temporary\"".format(
                _result_printf(
                    input_identity,
                    status="$terminal_status",
                    phase="$terminal_phase",
                    exit_code="$terminal_code",
                    started_var="$started_at",
                    finished_var="$finished_at",
                    log_bytes_var="$log_bytes",
                    log_sha_var="$log_sha",
                )
            ),
            "  /usr/bin/chmod 0600 -- \"$temporary\"",
            "  /usr/bin/mv -- \"$temporary\" \"$result_path\"",
            "  finalized=1",
            "}",
            "on_exit() {",
            "  status=$?",
            "  if [ \"$finalized\" -eq 0 ]; then",
            "    if [ \"$status\" -eq 0 ]; then status=1; fi",
            "    write_terminal_result FAILED \"$phase\" \"$status\"",
            "  fi",
            "  exit \"$status\"",
            "}",
            "trap on_exit EXIT",
            "unset TAIJI_ALLOW_UV_LOCK_REFRESH",
            "cd -- \"$remote_dir\"",
            "/usr/bin/sha256sum -c -- {}".format(_shell_quote(checksum)),
            "/usr/bin/tar --no-same-owner --no-same-permissions -xzf -- {}".format(_shell_quote(archive)),
            "cd -- {}".format(_shell_quote(delivery)),
            "set +e",
            "TAIJI_UV_LOCK_MODE=strict /bin/bash -p ./00_制包机_生成离线交付包.sh 2>&1 | /usr/bin/tee -- \"$remote_log\"",
            "pipeline_status=(\"${PIPESTATUS[@]}\")",
            "build_status=${pipeline_status[0]}",
            "tee_status=${pipeline_status[1]}",
            "set -e",
            "if [ \"$build_status\" -ne 0 ]; then exit \"$build_status\"; fi",
            "if [ \"$tee_status\" -ne 0 ]; then exit \"$tee_status\"; fi",
            "phase=review",
            "/usr/bin/install -d -m 0700 -- {}".format(_shell_quote(remote_dir + "/review")),
            "/usr/bin/tar --no-same-owner --no-same-permissions -xzf -- {} -C {}".format(
                _shell_quote(delivery + "/" + source_archive), _shell_quote(remote_dir + "/review")
            ),
            "/bin/cp -a -- {}/. {}/".format(
                _shell_quote(delivery), _shell_quote(remote_dir + "/review/taiji-agentv1.0/taijiagent 打包交付")
            ),
            "write_terminal_result SUCCEEDED review 0",
            "exit 0",
        ]
    )


def _launch_script(account_home: str, remote_dir: str, source_commit: str, remote_attempt_id: str, input_identity: Dict[str, Dict[str, Any]], result_basename: str) -> str:
    result_path = remote_dir.rstrip("/") + "/" + result_basename
    remote_log = remote_dir.rstrip("/") + "/" + REMOTE_LOG_BASENAME
    started_at = "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
    running_json = _result_printf(
        input_identity,
        status="RUNNING",
        phase="00",
        exit_code="null",
        started_var="$started_at",
        finished_var="null",
        log_bytes_var="0",
        log_sha_var=hashlib.sha256(b"").hexdigest(),
    )
    worker = _worker_script(remote_dir, source_commit, remote_attempt_id, input_identity, "$started_at")
    wrapper = "\n".join(
        [
            "set -Eeuo pipefail",
            "umask 077",
            "remote_dir={}".format(_shell_quote(remote_dir)),
            "result_path={}".format(_shell_quote(result_path)),
            "remote_log={}".format(_shell_quote(remote_log)),
            "source_commit={}".format(_shell_quote(source_commit)),
            "remote_attempt_id={}".format(_shell_quote(remote_attempt_id)),
            "if [ -e \"$result_path\" ] || [ -L \"$result_path\" ]; then exit 0; fi",
            "if [ -e \"$remote_log\" ] || [ -L \"$remote_log\" ]; then exit 0; fi",
            "(set -C; : > \"$remote_log\")",
            "/usr/bin/chmod 0600 -- \"$remote_log\"",
            "started_at={}".format(started_at),
            "export TAIJI_REMOTE_STARTED_AT=\"$started_at\"",
            "temporary=\"$remote_dir/.remote-build-result.json.launch.$$\"",
            "(set -C; {} ) > \"$temporary\"".format(running_json),
            "/usr/bin/chmod 0600 -- \"$temporary\"",
            "/usr/bin/ln -- \"$temporary\" \"$result_path\"",
            "/usr/bin/unlink -- \"$temporary\"",
            "worker={}".format(_shell_quote(worker)),
            "exec /bin/bash -p -c \"$worker\"",
        ]
    )
    return "\n".join(
        [
            "set -Eeuo pipefail",
            "umask 077",
            "wrapper={}".format(_shell_quote(wrapper)),
            "/usr/bin/nohup /bin/bash -p -c \"$wrapper\" </dev/null >/dev/null 2>&1 &",
        ]
    )


def _launch_remote(host: str, account_home: str, remote_dir: str, source_commit: str, remote_attempt_id: str, input_identity: Dict[str, Dict[str, Any]], result_basename: str) -> None:
    completed = _ssh(
        host,
        account_home,
        _launch_script(account_home, remote_dir, source_commit, remote_attempt_id, input_identity, result_basename),
        _replacement_environment(account_home),
    )
    if completed.returncode != 0:
        raise RemoteBuildError("remote launch failed before durable RUNNING confirmation")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--host", required=True)
    parser.add_argument("--account-home", required=True)
    parser.add_argument("--remote-dir", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--remote-attempt-id", required=True)
    for key in ("archive", "manifest", "checksum"):
        parser.add_argument("--{}-basename".format(key), required=True)
        parser.add_argument("--{}-bytes".format(key), required=True, type=int)
        parser.add_argument("--{}-sha256".format(key), required=True)
    parser.add_argument("--result-basename", default=RESULT_BASENAME)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        host = _validate_host(args.host)
        account_home = _validate_remote_path(args.account_home, "account home")
        remote_dir = _validate_remote_path(args.remote_dir, "remote directory")
        source_commit = _validate_commit(args.source_commit, "source commit")
        remote_attempt_id = _validate_attempt(args.remote_attempt_id, "remote attempt id")
        if args.result_basename != RESULT_BASENAME:
            raise RemoteBuildError("result basename is not fixed")
        input_identity = {
            key: {
                "basename": getattr(args, key + "_basename"),
                "bytes": getattr(args, key + "_bytes"),
                "sha256": getattr(args, key + "_sha256"),
            }
            for key in ("archive", "manifest", "checksum")
        }
        input_identity = _normalise_expected_input_identity(input_identity)
        expected_names = _expected_input_identity(source_commit)
        for key in expected_names:
            if input_identity[key]["basename"] != expected_names[key]["basename"]:
                raise RemoteBuildError("{} basename is not bound to source commit".format(key))

        def query():
            return _query_remote(
                host,
                account_home,
                remote_dir,
                RESULT_BASENAME,
                source_commit,
                remote_attempt_id,
                input_identity,
            )

        launched = False
        while True:
            result = query()
            action = decide_remote_build_action(result)
            if action == "START":
                if launched:
                    raise RemoteBuildError("remote result disappeared after launch")
                _launch_remote(
                    host,
                    account_home,
                    remote_dir,
                    source_commit,
                    remote_attempt_id,
                    input_identity,
                    RESULT_BASENAME,
                )
                launched = True
                if query() is None:
                    raise RemoteBuildError("durable RUNNING result is unreadable after launch")
                continue
            if action == "POLL":
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            if action == "CONTINUE":
                return 0
            if action == "FAIL":
                return 1
            raise RemoteBuildError("unknown reconciliation action")
    except (RemoteBuildError, OSError, ValueError) as exc:
        print("kylin remote build: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
