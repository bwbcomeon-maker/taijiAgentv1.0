#!/usr/bin/env python3
"""Plan and checkpoint the Taiji Linux golden-release flow without executing it.

This is deliberately a thin coordinator.  It emits exact commands for the
existing source-controlled trust boundaries and records operator checkpoints;
it does not run SSH, Docker, target acceptance, signing, or publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


CONFIG_SCHEMA = "taiji-linux-golden-orchestrator-config/v1"
STATE_SCHEMA = "taiji-linux-golden-orchestrator-state/v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
REMOTE_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REMOTE_ROOT_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")
DEB_RE = re.compile(r"^taiji-agent_[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}_amd64\.deb$")
STAGES = (
    "input_verify",
    "remote_build",
    "artifact_preflight",
    "offline_rehearsal",
    "target_acceptance",
    "certification_sign",
    "publication_sign",
    "release_check",
    "publish",
)
EXPLICIT_APPROVAL_STAGES = {
    "remote_build",
    "offline_rehearsal",
    "target_acceptance",
    "certification_sign",
    "publication_sign",
    "release_check",
    "publish",
}
MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_FILE_BYTES = 2 * 1024 * 1024 * 1024


class OrchestratorError(RuntimeError):
    """The orchestration state or requested transition was unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strict_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise OrchestratorError("JSON contains duplicate key: {}".format(key))
        result[key] = value
    return result


def _identity(value: os.stat_result) -> Tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_regular(path: Path, label: str, max_bytes: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise OrchestratorError("{} is missing: {}".format(label, path)) from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size <= 0
        or before.st_size > max_bytes
    ):
        raise OrchestratorError(
            "{} must be a bounded, current-user, single-link regular file that is not group/other writable".format(
                label
            )
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise OrchestratorError("{} cannot be opened safely: {}".format(label, path)) from exc
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise OrchestratorError("{} changed before read".format(label))
        remaining = opened.st_size
        chunks = []  # type: List[bytes]
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise OrchestratorError("{} was truncated while reading".format(label))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OrchestratorError("{} grew while reading".format(label))
        after = os.fstat(descriptor)
        current = path.lstat()
        if _identity(after) != _identity(opened) or _identity(current) != _identity(opened):
            raise OrchestratorError("{} changed while reading".format(label))
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_json(path: Path, label: str) -> Tuple[Dict[str, Any], bytes]:
    payload = _read_regular(path, label, MAX_CONTROL_FILE_BYTES)
    try:
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OrchestratorError("{} is not strict UTF-8 JSON".format(label)) from exc
    if type(parsed) is not dict:
        raise OrchestratorError("{} must be a JSON object".format(label))
    return parsed, payload


def _fingerprint(path: Path, label: str, max_bytes: int = MAX_EVIDENCE_FILE_BYTES) -> Dict[str, Any]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise OrchestratorError("{} is missing: {}".format(label, path)) from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size <= 0
        or before.st_size > max_bytes
    ):
        raise OrchestratorError(
            "{} must be a bounded, current-user, single-link regular file that is not group/other writable".format(
                label
            )
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise OrchestratorError("{} cannot be opened safely: {}".format(label, path)) from exc
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise OrchestratorError("{} changed before hashing".format(label))
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise OrchestratorError("{} was truncated while hashing".format(label))
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OrchestratorError("{} grew while hashing".format(label))
        after = os.fstat(descriptor)
        current = path.lstat()
        if _identity(after) != _identity(opened) or _identity(current) != _identity(opened):
            raise OrchestratorError("{} changed while hashing".format(label))
    finally:
        os.close(descriptor)
    return {
        "path": str(path.resolve()),
        "basename": path.name,
        "size": before.st_size,
        "sha256": digest.hexdigest(),
    }


def _same_fingerprint(expected: Dict[str, Any], label: str) -> None:
    path_value = expected.get("path")
    if type(path_value) is not str:
        raise OrchestratorError("{} checkpoint path is invalid".format(label))
    actual = _fingerprint(Path(path_value), label)
    if actual != expected:
        raise OrchestratorError("{} identity drifted: {}".format(label, path_value))


def _require_exact_keys(payload: Dict[str, Any], expected: set, label: str) -> None:
    actual = set(payload)
    if actual != expected:
        raise OrchestratorError(
            "{} keys mismatch; missing={} extra={}".format(
                label,
                sorted(expected - actual),
                sorted(actual - expected),
            )
        )


def _require_mapping(value: Any, label: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise OrchestratorError("{} must be an object".format(label))
    return value


def _absolute_path(value: Any, label: str) -> str:
    if type(value) is not str or not value.startswith("/"):
        raise OrchestratorError("{} must be an absolute path".format(label))
    path = Path(value)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise OrchestratorError("{} contains an unsafe path component".format(label))
    return str(path)


def _existing_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OrchestratorError("{} is missing: {}".format(label, path)) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise OrchestratorError("{} must be a current-user, non-writable-by-others directory".format(label))
    return path.resolve()


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _validate_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_exact_keys(
        payload,
        {"schema", "source_commit", "repo_root", "input", "remote", "workspace", "offline", "target", "release"},
        "config",
    )
    if payload["schema"] != CONFIG_SCHEMA:
        raise OrchestratorError("config schema is not supported")
    source_commit = payload["source_commit"]
    if type(source_commit) is not str or not COMMIT_RE.fullmatch(source_commit):
        raise OrchestratorError("source commit must be a full lowercase commit")
    payload["repo_root"] = _absolute_path(payload["repo_root"], "repo_root")
    _existing_directory(Path(payload["repo_root"]), "repo_root")

    input_config = _require_mapping(payload["input"], "input")
    _require_exact_keys(input_config, {"archive", "manifest", "checksum"}, "input")
    for key in sorted(input_config):
        input_config[key] = _absolute_path(input_config[key], "input.{}".format(key))

    remote = _require_mapping(payload["remote"], "remote")
    _require_exact_keys(remote, {"host", "root"}, "remote")
    if type(remote["host"]) is not str or not REMOTE_HOST_RE.fullmatch(remote["host"]):
        raise OrchestratorError("remote.host must be a configured SSH alias")
    if (
        type(remote["root"]) is not str
        or not REMOTE_ROOT_RE.fullmatch(remote["root"])
        or ".." in Path(remote["root"]).parts
    ):
        raise OrchestratorError("remote.root must be a safe absolute ASCII path")

    workspace = _require_mapping(payload["workspace"], "workspace")
    _require_exact_keys(workspace, {"review_root", "logs_dir"}, "workspace")
    workspace["review_root"] = _absolute_path(workspace["review_root"], "workspace.review_root")
    workspace["logs_dir"] = _absolute_path(workspace["logs_dir"], "workspace.logs_dir")
    if Path(workspace["review_root"]).name != "taiji-agentv1.0":
        raise OrchestratorError("workspace.review_root basename must be taiji-agentv1.0")
    _existing_directory(Path(workspace["review_root"]).parent, "workspace.review_root parent")
    _existing_directory(Path(workspace["logs_dir"]), "workspace.logs_dir")

    offline = _require_mapping(payload["offline"], "offline")
    _require_exact_keys(
        offline,
        {"image", "challenge", "output_dir", "previous_deb", "previous_signature", "previous_manifest"},
        "offline",
    )
    if type(offline["image"]) is not str or not IMAGE_RE.fullmatch(offline["image"]):
        raise OrchestratorError("offline.image is invalid")
    if type(offline["challenge"]) is not str or not CHALLENGE_RE.fullmatch(offline["challenge"]):
        raise OrchestratorError("offline.challenge is invalid")
    for key in ("output_dir", "previous_deb", "previous_signature", "previous_manifest"):
        offline[key] = _absolute_path(offline[key], "offline.{}".format(key))

    target = _require_mapping(payload["target"], "target")
    target_keys = {
        "delivery_dir",
        "customer_dir",
        "install_observation",
        "method_attestation",
        "installer_screenshot",
        "category_id",
        "operator_id",
        "challenge",
        "environment_observation",
        "target_dir",
        "timeout_ms",
    }
    _require_exact_keys(target, target_keys, "target")
    for key in target_keys - {"category_id", "operator_id", "challenge", "timeout_ms"}:
        target[key] = _absolute_path(target[key], "target.{}".format(key))
    if type(target["category_id"]) is not str or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", target["category_id"]):
        raise OrchestratorError("target.category_id is invalid")
    if type(target["operator_id"]) is not str or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", target["operator_id"]):
        raise OrchestratorError("target.operator_id is invalid")
    if type(target["challenge"]) is not str or not CHALLENGE_RE.fullmatch(target["challenge"]):
        raise OrchestratorError("target.challenge is invalid")
    if type(target["timeout_ms"]) is not int or not 30000 <= target["timeout_ms"] <= 1800000:
        raise OrchestratorError("target.timeout_ms is outside the installed runner contract")

    release = _require_mapping(payload["release"], "release")
    release_keys = {
        "records_dir",
        "certification_output_dir",
        "certification_challenge",
        "publication_challenge",
        "ci_evidence",
        "private_key",
        "customer_output",
        "receipt_root",
    }
    _require_exact_keys(release, release_keys, "release")
    for key in release_keys - {"certification_challenge", "publication_challenge"}:
        release[key] = _absolute_path(release[key], "release.{}".format(key))
    for key in ("certification_challenge", "publication_challenge"):
        if type(release[key]) is not str or not CHALLENGE_RE.fullmatch(release[key]):
            raise OrchestratorError("release.{} is invalid".format(key))
    challenges = {
        offline["challenge"],
        target["challenge"],
        release["certification_challenge"],
        release["publication_challenge"],
    }
    if len(challenges) != 4:
        raise OrchestratorError(
            "offline, target, certification, and publication challenges must be independent"
        )
    return payload


def _state_parent(path: Path) -> Path:
    if not path.is_absolute():
        raise OrchestratorError("state path must be absolute")
    return _existing_directory(path.parent, "state parent")


def _write_state(path: Path, payload: Dict[str, Any], create: bool) -> None:
    parent = _state_parent(path)
    if create and (path.exists() or path.is_symlink()):
        raise OrchestratorError("state already exists: {}".format(path))
    if not create:
        current = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or path.is_symlink()
            or current.st_uid != os.getuid()
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise OrchestratorError("state file is unsafe")
        before_identity = _identity(current)
    else:
        before_identity = None
    rendered = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temp = parent / (".{}-{}.tmp".format(path.name, uuid.uuid4().hex))
    descriptor = os.open(
        str(temp),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    failed = True
    try:
        view = memoryview(rendered)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OrchestratorError("state write failed")
            view = view[written:]
        os.fsync(descriptor)
        if before_identity is not None and _identity(path.lstat()) != before_identity:
            raise OrchestratorError("state changed concurrently")
        os.replace(str(temp), str(path))
        failed = False
    finally:
        os.close(descriptor)
        if failed:
            try:
                temp.unlink()
            except OSError:
                pass


def initialize(config_path: Path, state_path: Path) -> Dict[str, Any]:
    config, config_bytes = _load_json(config_path, "orchestrator config")
    config = _validate_config(config)
    source_commit = config["source_commit"]
    input_config = config["input"]
    manifest, _ = _load_json(Path(input_config["manifest"]), "builder input manifest")
    if manifest.get("source_commit") != source_commit:
        raise OrchestratorError("builder input manifest source commit does not match config")
    expected_archive = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    expected_manifest = "taijiagent-制包机输入-{}.manifest.json".format(source_commit)
    if Path(input_config["archive"]).name != expected_archive:
        raise OrchestratorError("builder input archive basename does not bind source commit")
    if Path(input_config["manifest"]).name != expected_manifest:
        raise OrchestratorError("builder input manifest basename does not bind source commit")
    if Path(input_config["checksum"]).name != expected_archive + ".sha256":
        raise OrchestratorError("builder input checksum basename does not bind source commit")

    input_identity = {
        key: _fingerprint(
            Path(input_config[key]),
            "builder input {}".format(key),
            MAX_EVIDENCE_FILE_BYTES if key == "archive" else MAX_CONTROL_FILE_BYTES,
        )
        for key in ("archive", "manifest", "checksum")
    }
    now = _now()
    state = {
        "schema": STATE_SCHEMA,
        "source_commit": source_commit,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "config": config,
        "input_identity": input_identity,
        "candidate_deb": None,
        "remote_attempt_id": uuid.uuid4().hex[:16],
        "current_stage": STAGES[0],
        "stages": {
            stage: {"status": "pending", "attempts": 0, "history": []}
            for stage in STAGES
        },
        "created_at_utc": now,
        "updated_at_utc": now,
        "revision": 0,
        "scope": "checkpoint-plan-only; trusted release gates remain authoritative",
    }
    _write_state(state_path, state, create=True)
    return state


def _load_state(path: Path) -> Dict[str, Any]:
    state, _ = _load_json(path, "orchestrator state")
    if state.get("schema") != STATE_SCHEMA:
        raise OrchestratorError("orchestrator state schema is not supported")
    if state.get("source_commit") is None or not COMMIT_RE.fullmatch(str(state["source_commit"])):
        raise OrchestratorError("orchestrator state source commit is invalid")
    stages = state.get("stages")
    if type(stages) is not dict or set(stages) != set(STAGES):
        raise OrchestratorError("orchestrator state stages are invalid")
    current_stage = state.get("current_stage")
    if current_stage is not None and current_stage not in STAGES:
        raise OrchestratorError("orchestrator state checkpoint sequence is invalid")
    current_index = len(STAGES) if current_stage is None else STAGES.index(current_stage)
    for index, stage in enumerate(STAGES):
        entry = _require_mapping(stages[stage], "stage {}".format(stage))
        status = entry.get("status")
        if index < current_index:
            expected_statuses = {"passed"}
        elif index == current_index and current_stage is not None:
            expected_statuses = {"pending", "failed"}
        else:
            expected_statuses = {"pending"}
        if status not in expected_statuses:
            raise OrchestratorError("orchestrator state checkpoint sequence is invalid at {}".format(stage))
        if type(entry.get("attempts")) is not int or entry["attempts"] < 0:
            raise OrchestratorError("orchestrator state checkpoint attempts are invalid at {}".format(stage))
        if type(entry.get("history")) is not list:
            raise OrchestratorError("orchestrator state checkpoint history is invalid at {}".format(stage))
    config = _require_mapping(state.get("config"), "state.config")
    _validate_config(config)
    if config["source_commit"] != state["source_commit"]:
        raise OrchestratorError("state config source commit drifted")
    if not re.fullmatch(r"[0-9a-f]{16}", str(state.get("remote_attempt_id", ""))):
        raise OrchestratorError("state remote attempt id is invalid")
    input_identity = _require_mapping(state.get("input_identity"), "input_identity")
    if set(input_identity) != {"archive", "manifest", "checksum"}:
        raise OrchestratorError("orchestrator state input identity is incomplete")
    for key, expected in input_identity.items():
        _same_fingerprint(_require_mapping(expected, "input_identity.{}".format(key)), "builder input {}".format(key))
    candidate = state.get("candidate_deb")
    if candidate is not None:
        if type(candidate) is not dict:
            raise OrchestratorError("candidate DEB checkpoint is invalid")
        try:
            _same_fingerprint(candidate, "candidate DEB")
        except OrchestratorError as exc:
            raise OrchestratorError("candidate DEB identity drifted") from exc
    candidate_required = current_stage is None or current_index > STAGES.index("remote_build")
    if candidate_required != (candidate is not None):
        raise OrchestratorError("orchestrator state candidate checkpoint sequence is invalid")
    for stage in STAGES:
        entry = _require_mapping(stages[stage], "stage {}".format(stage))
        if entry.get("status") == "passed":
            evidence_items = entry.get("evidence")
            if type(evidence_items) is not list or not evidence_items:
                raise OrchestratorError("passed stage evidence is incomplete at {}".format(stage))
            if stage in EXPLICIT_APPROVAL_STAGES and entry.get("explicit_approval_recorded") is not True:
                raise OrchestratorError("explicit approval checkpoint is missing at {}".format(stage))
            for index, evidence in enumerate(evidence_items):
                _same_fingerprint(
                    _require_mapping(evidence, "stage evidence"),
                    "{} evidence {}".format(stage, index),
                )
            _same_fingerprint(_require_mapping(entry.get("log"), "stage log"), "{} log".format(stage))
    return state


def _validate_expectations(
    state: Dict[str, Any],
    expected_source_commit: str,
    expected_deb_sha256: Optional[str],
) -> None:
    if expected_source_commit != state["source_commit"]:
        raise OrchestratorError("expected source commit does not match checkpoint state")
    candidate = state.get("candidate_deb")
    if candidate is None:
        if expected_deb_sha256 is not None:
            raise OrchestratorError("expect-deb-sha256 was supplied before a candidate was bound")
        return
    if expected_deb_sha256 is None:
        raise OrchestratorError("--expect-deb-sha256 is required after the candidate DEB is bound")
    if not SHA256_RE.fullmatch(expected_deb_sha256) or expected_deb_sha256 != candidate["sha256"]:
        raise OrchestratorError("expected candidate DEB SHA256 does not match checkpoint state")


def _command(
    label: str,
    argv: Sequence[str],
    cwd: str,
    log_path: str,
    boundary: str,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    return {
        "label": label,
        "argv": list(argv),
        "cwd": cwd,
        "env": dict(sorted((env or {}).items())),
        "log_path": log_path,
        "boundary": boundary,
    }


def _stage_log(config: Dict[str, Any], ordinal: int, stage: str) -> str:
    return str(Path(config["workspace"]["logs_dir"]) / "{:02d}-{}.log".format(ordinal, stage))


def _remote_directory(state: Dict[str, Any]) -> str:
    config = state["config"]
    return "{}/{}/{}".format(
        config["remote"]["root"].rstrip("/"),
        state["source_commit"],
        state["remote_attempt_id"],
    )


def _remote_script(state: Dict[str, Any]) -> str:
    config = state["config"]
    source_commit = state["source_commit"]
    remote_dir = _remote_directory(state)
    archive = Path(config["input"]["archive"]).name
    checksum = Path(config["input"]["checksum"]).name
    source_archive = "taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(source_commit)
    delivery = "taijiagent 打包交付"
    remote_log = remote_dir + "/02-remote-build.log"
    statements = [
        "set -Eeuo pipefail",
        "umask 077",
        "unset TAIJI_ALLOW_UV_LOCK_REFRESH",
        "cd {}".format(shlex.quote(remote_dir)),
        "sha256sum -c {}".format(shlex.quote(checksum)),
        "tar --no-same-owner --no-same-permissions -xzf {}".format(shlex.quote(archive)),
        "cd {}".format(shlex.quote(remote_dir + "/" + delivery)),
        "TAIJI_UV_LOCK_MODE=strict bash ./00_制包机_生成离线交付包.sh 2>&1 | tee {}".format(shlex.quote(remote_log)),
        "cd {}".format(shlex.quote(remote_dir)),
        "install -d -m 0700 -- review",
        "tar --no-same-owner --no-same-permissions -xzf {} -C review".format(
            shlex.quote(remote_dir + "/" + delivery + "/" + source_archive)
        ),
        "cp -a -- {}/. {}/".format(
            shlex.quote(remote_dir + "/" + delivery),
            shlex.quote(remote_dir + "/review/taiji-agentv1.0/" + delivery),
        ),
    ]
    return "; ".join(statements)


def _commands_for_stage(state: Dict[str, Any], stage: str) -> List[Dict[str, Any]]:
    config = state["config"]
    repo = Path(config["repo_root"])
    review = Path(config["workspace"]["review_root"])
    delivery = review / "taijiagent 打包交付"
    build_output = delivery / "生成的安装包"
    policy = repo / "packaging/linux/compatibility-policy.json"
    logs = config["workspace"]["logs_dir"]
    common_env = {"LANG": "C", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"}

    if stage == "input_verify":
        input_config = config["input"]
        return [
            _command(
                "verify same-commit builder input trio",
                [
                    "python3",
                    str(repo / "packaging/linux/builder-input-package.py"),
                    "verify",
                    "--archive",
                    input_config["archive"],
                    "--manifest",
                    input_config["manifest"],
                    "--checksum",
                    input_config["checksum"],
                ],
                str(repo),
                _stage_log(config, 1, stage),
                "local-read-only",
                common_env,
            )
        ]

    if stage == "remote_build":
        if review.exists() or review.is_symlink():
            raise OrchestratorError(
                "review root already exists; archive it explicitly instead of overwriting: {}".format(review)
            )
        remote = config["remote"]
        remote_parent = "{}/{}".format(remote["root"].rstrip("/"), state["source_commit"])
        remote_dir = _remote_directory(state)
        remote_log = remote_dir + "/02-remote-build.log"
        review_parent = str(review.parent)
        return [
            _command(
                "create commit-specific remote build directory",
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    remote["host"],
                    "install -d -m 0700 -- {} && mkdir -m 0700 -- {}".format(
                        shlex.quote(remote_parent), shlex.quote(remote_dir)
                    ),
                ],
                str(repo),
                _stage_log(config, 2, stage),
                "remote-external-approval",
            ),
            _command(
                "transfer exact input trio",
                [
                    "scp",
                    config["input"]["archive"],
                    config["input"]["manifest"],
                    config["input"]["checksum"],
                    "{}:{}/".format(remote["host"], remote_dir),
                ],
                str(repo),
                _stage_log(config, 2, stage),
                "remote-external-approval",
            ),
            _command(
                "run frozen 00 builder and prepare immutable review tree",
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    remote["host"],
                    "bash -lc {}".format(shlex.quote(_remote_script(state))),
                ],
                str(repo),
                _stage_log(config, 2, stage),
                "remote-external-approval",
            ),
            _command(
                "retrieve complete review tree",
                [
                    "scp",
                    "-r",
                    "{}:{}/review/taiji-agentv1.0".format(remote["host"], remote_dir),
                    review_parent + "/",
                ],
                str(repo),
                _stage_log(config, 2, stage),
                "remote-external-approval",
            ),
            _command(
                "retrieve remote build log",
                [
                    "scp",
                    "{}:{}".format(remote["host"], remote_log),
                    str(Path(logs) / "02-remote-build.log"),
                ],
                str(repo),
                _stage_log(config, 2, stage),
                "remote-external-approval",
            ),
        ]

    candidate = state.get("candidate_deb")
    if candidate is None:
        raise OrchestratorError("stage {} requires a bound candidate DEB".format(stage))
    deb = candidate["path"]
    manifest = build_output / "taiji-package-manifest.json"
    certification_dir = Path(config["release"]["certification_output_dir"])
    certification_set = certification_dir / "certification-set.json"
    certification_signature = Path(str(certification_set) + ".sig")
    release_evidence = delivery / "release-evidence.json"
    release_signature = Path(str(release_evidence) + ".sig")

    if stage == "artifact_preflight":
        return [
            _command(
                "prove review preflight script matches frozen local source",
                [
                    "cmp",
                    "--",
                    str(repo / "taijiagent 打包交付/01_制包机_发布预检.sh"),
                    str(delivery / "01_制包机_发布预检.sh"),
                ],
                str(review),
                _stage_log(config, 3, stage),
                "local-read-only",
            ),
            _command(
                "run physical candidate preflight",
                ["bash", str(delivery / "01_制包机_发布预检.sh")],
                str(review),
                _stage_log(config, 3, stage),
                "local-read-only",
                {
                    **common_env,
                    "TAIJI_RELEASE_REQUIRE_ARTIFACTS": "1",
                    "TAIJI_RELEASE_SKIP_GIT_CHECK": "1",
                    "TAIJI_REPO_ROOT": str(review),
                },
            ),
        ]

    if stage == "offline_rehearsal":
        offline = config["offline"]
        return [
            _command(
                "run no-network candidate and controlled N-1 lifecycle",
                [
                    "python3",
                    str(repo / "scripts/produce-taiji-offline-rehearsal.py"),
                    "--deb",
                    deb,
                    "--previous-deb",
                    offline["previous_deb"],
                    "--previous-signature",
                    offline["previous_signature"],
                    "--previous-manifest",
                    offline["previous_manifest"],
                    "--build-manifest",
                    str(manifest),
                    "--policy",
                    str(policy),
                    "--output-dir",
                    offline["output_dir"],
                    "--image",
                    offline["image"],
                    "--challenge",
                    offline["challenge"],
                ],
                str(repo),
                _stage_log(config, 4, stage),
                "docker-external-approval",
                common_env,
            )
        ]

    if stage == "target_acceptance":
        target = config["target"]
        target_delivery = Path(target["delivery_dir"])
        observer = target_delivery / "验收工具/observe-single-deb-install.py"
        target_manifest = target_delivery / "生成的安装包/taiji-package-manifest.json"
        target_matrix = target_delivery / "验收工具/certification-matrix.json"
        output_dir = str(Path(target["install_observation"]).parent)
        screenshot = target["installer_screenshot"]
        return [
            _command(
                "start controlled pre-install observer on target",
                [
                    "/usr/bin/python3",
                    "-B",
                    str(observer),
                    "observe",
                    "--customer-dir",
                    target["customer_dir"],
                    "--manifest",
                    str(target_manifest),
                    "--challenge",
                    target["challenge"],
                    "--matrix",
                    str(target_matrix),
                    "--category-id",
                    target["category_id"],
                    "--output-dir",
                    output_dir,
                ],
                target["delivery_dir"],
                _stage_log(config, 5, stage),
                "target-manual-external-approval",
            ),
            {
                "label": "operator performs witnessed offline double-click installation",
                "argv": [],
                "cwd": target["customer_dir"],
                "env": {},
                "log_path": _stage_log(config, 5, stage),
                "boundary": "target-human-gate",
                "manual_action": "断开非必要外网，在文件管理器双击唯一 DEB，保存完整图形安装器成功 PNG；编排器不会自动越过。",
            },
            _command(
                "record operator method attestation",
                [
                    "/usr/bin/python3",
                    "-B",
                    str(observer),
                    "attest",
                    "--observation",
                    target["install_observation"],
                    "--graphical-evidence",
                    screenshot,
                    "--challenge",
                    target["challenge"],
                    "--operator-id",
                    target["operator_id"],
                    "--confirmation",
                    "I-observed-desktop-double-click-and-system-installer",
                    "--output-dir",
                    output_dir,
                ],
                target["delivery_dir"],
                _stage_log(config, 5, stage),
                "target-human-gate",
            ),
            _command(
                "run DEB-installed root-owned acceptance entrypoint",
                [
                    "/usr/bin/taiji-agent-acceptance",
                    "--delivery-dir",
                    target["delivery_dir"],
                    "--customer-dir",
                    target["customer_dir"],
                    "--install-observation",
                    target["install_observation"],
                    "--method-attestation",
                    target["method_attestation"],
                    "--installer-screenshot",
                    screenshot,
                    "--category-id",
                    target["category_id"],
                    "--challenge",
                    target["challenge"],
                    "--environment-observation",
                    target["environment_observation"],
                    "--target-dir",
                    target["target_dir"],
                    "--timeout-ms",
                    str(target["timeout_ms"]),
                ],
                target["delivery_dir"],
                _stage_log(config, 5, stage),
                "target-manual-external-approval",
            ),
        ]

    if stage == "certification_sign":
        release = config["release"]
        return [
            _command(
                "assemble immutable certification set",
                [
                    "python3",
                    str(repo / "scripts/assemble-taiji-certification-set.py"),
                    "--matrix",
                    str(repo / "packaging/linux/certification-matrix.json"),
                    "--records-dir",
                    release["records_dir"],
                    "--offline-evidence",
                    config["offline"]["output_dir"],
                    "--deb",
                    deb,
                    "--policy",
                    str(policy),
                    "--output",
                    str(certification_dir),
                    "--challenge",
                    release["certification_challenge"],
                ],
                str(repo),
                _stage_log(config, 6, stage),
                "offline-signing-human-approval",
                common_env,
            ),
            _command(
                "sign certification set with offline key",
                [
                    "bash",
                    str(repo / "scripts/sign-taiji-release-evidence.sh"),
                    str(certification_set),
                    release["private_key"],
                ],
                str(repo),
                _stage_log(config, 6, stage),
                "offline-signing-human-approval",
                {"TAIJI_CERTIFICATION_CHALLENGE": release["certification_challenge"]},
            ),
        ]

    if stage == "publication_sign":
        release = config["release"]
        return [
            _command(
                "assemble v3 publication evidence",
                [
                    "python3",
                    str(repo / "scripts/assemble-taiji-release-evidence.py"),
                    "--manifest",
                    str(manifest),
                    "--deb",
                    deb,
                    "--policy",
                    str(policy),
                    "--certification-set",
                    str(certification_set),
                    "--certification-signature",
                    str(certification_signature),
                    "--ci-evidence",
                    release["ci_evidence"],
                    "--output",
                    str(release_evidence),
                    "--challenge",
                    release["publication_challenge"],
                ],
                str(repo),
                _stage_log(config, 7, stage),
                "offline-signing-human-approval",
                common_env,
            ),
            _command(
                "sign publication evidence with offline key",
                [
                    "bash",
                    str(repo / "scripts/sign-taiji-release-evidence.sh"),
                    str(release_evidence),
                    release["private_key"],
                ],
                str(repo),
                _stage_log(config, 7, stage),
                "offline-signing-human-approval",
                {"TAIJI_PUBLICATION_CHALLENGE": release["publication_challenge"]},
            ),
        ]

    if stage == "release_check":
        release = config["release"]
        return [
            _command(
                "run formal release check including live CI revalidation",
                [
                    "bash",
                    str(repo / "scripts/taiji-release-check.sh"),
                    "--delivery-dir",
                    str(delivery),
                    "--certification-set",
                    str(certification_set),
                    "--certification-signature",
                    str(certification_signature),
                    "--release-evidence",
                    str(release_evidence),
                    "--release-signature",
                    str(release_signature),
                ],
                str(repo),
                _stage_log(config, 8, stage),
                "network-and-release-human-approval",
                {
                    "TAIJI_CERTIFICATION_CHALLENGE": release["certification_challenge"],
                    "TAIJI_PUBLICATION_CHALLENGE": release["publication_challenge"],
                    "TAIJI_RELEASE_REPO_ROOT": str(repo),
                },
            )
        ]

    if stage == "publish":
        release = config["release"]
        return [
            _command(
                "atomically publish exactly one customer DEB",
                [
                    "bash",
                    str(repo / "packaging/linux/deb/publish-single-deb.sh"),
                    "--delivery-dir",
                    str(delivery),
                    "--candidate-deb",
                    deb,
                    "--policy",
                    str(policy),
                    "--certification-set",
                    str(certification_set),
                    "--certification-signature",
                    str(certification_signature),
                    "--release-evidence",
                    str(release_evidence),
                    "--release-signature",
                    str(release_signature),
                    "--output-dir",
                    release["customer_output"],
                    "--receipt-root",
                    release["receipt_root"],
                ],
                str(repo),
                _stage_log(config, 9, stage),
                "publication-human-approval",
                {
                    "TAIJI_CERTIFICATION_CHALLENGE": release["certification_challenge"],
                    "TAIJI_PUBLICATION_CHALLENGE": release["publication_challenge"],
                },
            )
        ]
    raise OrchestratorError("unknown stage: {}".format(stage))


def build_plan(state: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    stage = state.get("current_stage")
    identity = {
        "source_commit": state["source_commit"],
        "input": state["input_identity"],
        "candidate_deb": state.get("candidate_deb"),
    }
    if stage is None:
        return (
            {
                "schema": "taiji-linux-golden-orchestrator-plan/v1",
                "status": "CHECKPOINTS_COMPLETE",
                "stage": None,
                "identities": identity,
                "commands": [],
                "checkpoint_required": False,
                "auto_advance": False,
                "scope_note": "Checkpoint completion is not release proof; formal gates and evidence remain authoritative.",
            },
            0,
        )
    stage_entry = state["stages"][stage]
    if stage_entry["status"] == "failed":
        return (
            {
                "schema": "taiji-linux-golden-orchestrator-plan/v1",
                "status": "STOPPED",
                "stage": stage,
                "identities": identity,
                "commands": [],
                "checkpoint_required": True,
                "auto_advance": False,
                "failure": {
                    "log": stage_entry["log"],
                    "recorded_at_utc": stage_entry["recorded_at_utc"],
                },
                "next_action": "Inspect the recorded log, fix the frozen source locally if needed, or explicitly retry the same stage. Never patch remote source in place.",
            },
            3,
        )
    return (
        {
            "schema": "taiji-linux-golden-orchestrator-plan/v1",
            "status": "READY",
            "stage": stage,
            "identities": identity,
            "commands": _commands_for_stage(state, stage),
            "checkpoint_required": True,
            "auto_advance": False,
            "explicit_approval_required": stage in EXPLICIT_APPROVAL_STAGES,
            "scope_note": "Commands are a plan only. This tool does not execute them and cannot replace their evidence gates.",
        },
        0,
    )


def _next_stage(stage: str) -> Optional[str]:
    index = STAGES.index(stage)
    if index + 1 == len(STAGES):
        return None
    return STAGES[index + 1]


def checkpoint(
    state_path: Path,
    expected_source_commit: str,
    expected_deb_sha256: Optional[str],
    stage: str,
    result: str,
    log_path: Path,
    evidence_paths: Sequence[Path],
    deb_path: Optional[Path],
    approve_stage: Optional[str],
) -> Dict[str, Any]:
    state = _load_state(state_path)
    _validate_expectations(state, expected_source_commit, expected_deb_sha256)
    current = state.get("current_stage")
    if stage != current:
        raise OrchestratorError("requested stage is not the current stage: {} != {}".format(stage, current))
    entry = state["stages"][stage]
    if entry["status"] != "pending":
        raise OrchestratorError("current stage is not pending; inspect or retry it first")
    logs_root = Path(state["config"]["workspace"]["logs_dir"])
    if not _within(log_path, logs_root):
        raise OrchestratorError("log path must stay inside workspace.logs_dir")
    log = _fingerprint(log_path, "stage log", MAX_EVIDENCE_FILE_BYTES)
    if result not in {"pass", "fail"}:
        raise OrchestratorError("checkpoint result must be pass or fail")
    if result == "pass" and not evidence_paths:
        raise OrchestratorError("a passing checkpoint requires at least one evidence file")
    if result == "pass" and stage in EXPLICIT_APPROVAL_STAGES and approve_stage != stage:
        raise OrchestratorError("explicit approval is required for external/manual stage {}".format(stage))
    evidence = [
        _fingerprint(path, "stage evidence", MAX_EVIDENCE_FILE_BYTES)
        for path in evidence_paths
    ]
    if deb_path is not None:
        if stage != "remote_build" or result != "pass" or state.get("candidate_deb") is not None:
            raise OrchestratorError("--deb is allowed only when remote_build first passes")
        review = Path(state["config"]["workspace"]["review_root"])
        if not _within(deb_path, review) or not DEB_RE.fullmatch(deb_path.name):
            raise OrchestratorError("candidate DEB must be the fixed-name amd64 file under review_root")
        state["candidate_deb"] = _fingerprint(deb_path, "candidate DEB")
    elif stage == "remote_build" and result == "pass":
        raise OrchestratorError("remote_build pass must bind the retrieved candidate with --deb")
    if stage not in {"input_verify", "remote_build"} and state.get("candidate_deb") is None:
        raise OrchestratorError("stage {} cannot pass before a candidate DEB is bound".format(stage))

    recorded_at = _now()
    history = entry["history"]
    history.append(
        {
            "event": "checkpoint",
            "result": result,
            "recorded_at_utc": recorded_at,
            "log": log,
            "approval": approve_stage == stage,
        }
    )
    entry.update(
        {
            "status": "passed" if result == "pass" else "failed",
            "attempts": int(entry.get("attempts", 0)) + 1,
            "recorded_at_utc": recorded_at,
            "log": log,
            "evidence": evidence,
            "explicit_approval_recorded": approve_stage == stage,
        }
    )
    if result == "pass":
        state["current_stage"] = _next_stage(stage)
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at_utc"] = recorded_at
    _write_state(state_path, state, create=False)
    return state


def retry(
    state_path: Path,
    expected_source_commit: str,
    expected_deb_sha256: Optional[str],
    stage: str,
) -> Dict[str, Any]:
    state = _load_state(state_path)
    _validate_expectations(state, expected_source_commit, expected_deb_sha256)
    if state.get("current_stage") != stage:
        raise OrchestratorError("retry is allowed only for the current stage")
    entry = state["stages"][stage]
    if entry.get("status") != "failed":
        raise OrchestratorError("retry is allowed only after a recorded failure")
    entry["history"].append({"event": "retry", "recorded_at_utc": _now()})
    entry["status"] = "pending"
    for key in ("recorded_at_utc", "log", "evidence", "explicit_approval_recorded"):
        entry.pop(key, None)
    if stage == "remote_build":
        state["remote_attempt_id"] = uuid.uuid4().hex[:16]
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at_utc"] = _now()
    _write_state(state_path, state, create=False)
    return state


def _summary(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema": state["schema"],
        "source_commit": state["source_commit"],
        "candidate_deb": state.get("candidate_deb"),
        "remote_attempt_id": state["remote_attempt_id"],
        "current_stage": state.get("current_stage"),
        "input_identity": state["input_identity"],
        "revision": state["revision"],
        "scope": state["scope"],
    }


def _add_expectations(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--expect-source-commit", required=True)
    parser.add_argument("--expect-deb-sha256")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", allow_abbrev=False)
    init_parser.add_argument("--config", required=True, type=Path)
    init_parser.add_argument("--state", required=True, type=Path)
    for name in ("plan", "dry-run"):
        _add_expectations(subparsers.add_parser(name, allow_abbrev=False))
    checkpoint_parser = subparsers.add_parser("checkpoint", allow_abbrev=False)
    _add_expectations(checkpoint_parser)
    checkpoint_parser.add_argument("--stage", required=True, choices=STAGES)
    checkpoint_parser.add_argument("--result", required=True, choices=("pass", "fail"))
    checkpoint_parser.add_argument("--log-path", required=True, type=Path)
    checkpoint_parser.add_argument("--evidence", action="append", default=[], type=Path)
    checkpoint_parser.add_argument("--deb", type=Path)
    checkpoint_parser.add_argument("--approve-stage", choices=STAGES)
    retry_parser = subparsers.add_parser("retry", allow_abbrev=False)
    _add_expectations(retry_parser)
    retry_parser.add_argument("--stage", required=True, choices=STAGES)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "init":
            output = _summary(initialize(args.config, args.state))
            exit_code = 0
        elif args.command in {"plan", "dry-run"}:
            state = _load_state(args.state)
            _validate_expectations(state, args.expect_source_commit, args.expect_deb_sha256)
            output, exit_code = build_plan(state)
        elif args.command == "checkpoint":
            output = _summary(
                checkpoint(
                    args.state,
                    args.expect_source_commit,
                    args.expect_deb_sha256,
                    args.stage,
                    args.result,
                    args.log_path,
                    args.evidence,
                    args.deb,
                    args.approve_stage,
                )
            )
            exit_code = 0
        else:
            output = _summary(
                retry(
                    args.state,
                    args.expect_source_commit,
                    args.expect_deb_sha256,
                    args.stage,
                )
            )
            exit_code = 0
    except (OrchestratorError, OSError, ValueError, TypeError) as exc:
        print("taiji-linux-golden-orchestrator-failed\t{}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
