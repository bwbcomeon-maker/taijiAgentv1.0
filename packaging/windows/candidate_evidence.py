"""Publish a private Windows candidate evidence bundle."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve()
EVIDENCE_DIRNAME = "evidence"
EVIDENCE_JSON = "windows-candidate-evidence.json"
HANDOFF_MD = "windows-candidate-handoff.md"
STAGING_PREFIX = ".evidence-"
# symlink and hardlink inputs are rejected before publication.
TARGET_ID = "windows-x64"
REQUIRED_STAGE = "CANDIDATE_BUILT"
CURRENT_VERIFIED = "CURRENT_VERIFIED"
NOT_VERIFIED = "NOT_VERIFIED"
NOT_COMPLETED = "NOT_COMPLETED"
NOT_EXECUTED = "NOT_EXECUTED"
REVIEW_BASENAMES = (
    "taiji-package-manifest.json",
    "formal-build-tests.log",
    "构建报告.txt",
    ".build-success",
    "run-state.json",
)
FORMAL_LOG_LINES = (
    "01 source-session-identity PASS exit=0",
    "02 offline-npm-ci PASS exit=0",
    "03 electron-win32-x64 PASS exit=0",
    "04 payload-import-menu-policy PASS exit=0",
    "05 payload-hygiene-closure PASS exit=0",
    "06 inno-compile PASS exit=0",
    "07 installer-pe-version-authenticode PASS exit=0",
    "SUMMARY PASS checks=7",
)
REMOTE_LOG_LINES = (
    "remote build started",
    "source-session-identity PASS exit=0",
    "offline-npm-ci PASS exit=0",
    "electron-win32-x64 PASS exit=0",
    "payload-import-menu-policy PASS exit=0",
    "payload-hygiene-closure PASS exit=0",
    "inno-compile PASS exit=0",
    "installer-pe-version-authenticode PASS exit=0",
    "SUMMARY PASS checks=7",
)
MANIFEST_KEYS = {
    "schema", "run_id", "target_id", "source", "input", "target_config_sha256",
    "asset_provenance_sha256", "cache_requirements_sha256", "cache_observation_sha256",
    "tools", "payload", "formal_tests", "artifact", "boundaries", "started_at", "finished_at",
}
PAYLOAD_KEYS = {
    "entries", "file_count", "manifest_sha256", "schema", "source_commit",
    "source_tree", "total_bytes",
}
ARTIFACT_KEYS = {
    "authenticode_status", "basename", "bytes", "file_version", "kind", "pe_machine",
    "pe_optional_magic", "product_version", "sha256", "version",
}
REMOTE_STATE_KEYS = {
    "schema", "run_id", "target_id", "source_commit", "host_facts_sha256",
    "stage_history", "terminal_status", "started_at", "finished_at",
}
MARKER_KEYS = {
    "schema", "run_id", "target_id", "source_commit", "artifact_basename", "artifact_bytes",
    "artifact_sha256", "package_manifest_basename", "package_manifest_bytes", "package_manifest_sha256",
    "formal_build_tests_log_basename", "formal_build_tests_log_bytes", "formal_build_tests_log_sha256",
    "report_basename", "report_bytes", "report_sha256", "remote_state_basename", "remote_state_bytes",
    "remote_state_sha256",
}
TOOL_NAMES = {"powershell", "tar", "node", "npm", "python", "iscc", "safe_tar"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z")


class EvidenceError(RuntimeError):
    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


def _core_contracts():
    """Import the exact checkout's state contracts even under Python isolated mode."""

    repo_root = MODULE_PATH.parents[2]
    root_text = str(repo_root)
    inserted = not sys.path or sys.path[0] != root_text
    if inserted:
        sys.path.insert(0, root_text)
    try:
        from packaging.pipeline.core import models as core_models
        from packaging.pipeline.core import state as core_state
    except Exception as exc:
        raise EvidenceError(
            f"cannot load checkout state contracts: {exc}", category="PLAN_INVALID"
        ) from exc
    finally:
        if inserted and sys.path and sys.path[0] == root_text:
            sys.path.pop(0)
    expected_models = (repo_root / "packaging/pipeline/core/models.py").resolve()
    expected_state = (repo_root / "packaging/pipeline/core/state.py").resolve()
    if Path(core_models.__file__).resolve() != expected_models or Path(core_state.__file__).resolve() != expected_state:
        raise EvidenceError("state contracts were imported from another checkout", category="PLAN_INVALID")
    return core_models, core_state


def _require_exact(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise EvidenceError(f"{label} fields are not exact", category="LOCAL_REVIEW_INVALID")


def _require_sha256(value, label):
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise EvidenceError(f"{label} is not a lowercase SHA256", category="LOCAL_REVIEW_INVALID")
    return value


def _require_commit(value, label):
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise EvidenceError(f"{label} is not a lowercase commit", category="LOCAL_REVIEW_INVALID")
    return value


def _require_utc(value, label):
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise EvidenceError(f"{label} is not UTC", category="LOCAL_REVIEW_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError(f"{label} is invalid", category="LOCAL_REVIEW_INVALID") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EvidenceError(f"{label} is not UTC", category="LOCAL_REVIEW_INVALID")
    return parsed


def _canonical_json_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_lstat(path: Path, label: str):
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EvidenceError(
            f"{label} is unavailable: {exc}", category="LOCAL_REVIEW_INVALID"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise EvidenceError(
            f"{label} is not current-user private", category="LOCAL_REVIEW_INVALID"
        )
    return metadata


def _require_private_directory(path: Path, label: str) -> Path:
    metadata = _safe_lstat(path, label)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise EvidenceError(
            f"{label} is not a 0700 private directory", category="LOCAL_REVIEW_INVALID"
        )
    return path


def _require_private_file(path: Path, label: str) -> bytes:
    metadata = _safe_lstat(path, label)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise EvidenceError(
            f"{label} is not a 0600 private regular file",
            category="LOCAL_REVIEW_INVALID",
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EvidenceError(
            f"{label} cannot be read: {exc}", category="LOCAL_REVIEW_INVALID"
        ) from exc


def _load_canonical_json_file(path: Path, label: str):
    payload = _require_private_file(path, label)
    if payload.startswith(b"\xef\xbb\xbf") or not payload.endswith(b"\n"):
        raise EvidenceError(
            f"{label} is not canonical UTF-8 JSON", category="LOCAL_REVIEW_INVALID"
        )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(
            f"{label} is invalid JSON: {exc}", category="LOCAL_REVIEW_INVALID"
        ) from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object", category="LOCAL_REVIEW_INVALID")
    if payload != _canonical_json_bytes(value) + b"\n":
        raise EvidenceError(
            f"{label} is not canonical JSON", category="LOCAL_REVIEW_INVALID"
        )
    return value, payload


def _helper_self_check() -> None:
    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", str(MODULE_PATH), "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise EvidenceError("helper self-check failed", category="LOCAL_REVIEW_INVALID")


def _validate_state(state):
    core_models, _core_state = _core_contracts()
    try:
        core_models.validate_v2_state(state)
    except Exception as exc:
        raise EvidenceError(f"state v2 contract is invalid: {exc}", category="PLAN_INVALID") from exc
    if state.get("target_id") != TARGET_ID:
        raise EvidenceError("target_id must be windows-x64", category="PLAN_INVALID")
    if state.get("stage") != REQUIRED_STAGE:
        raise EvidenceError("state stage must be CANDIDATE_BUILT", category="PLAN_INVALID")
    artifact = state.get("artifact")
    required = {"kind", "basename", "bytes", "sha256", "path", "relative_path"}
    if not isinstance(artifact, dict) or set(artifact) != required:
        raise EvidenceError("artifact metadata is incomplete", category="PLAN_INVALID")
    if artifact.get("kind") != "exe":
        raise EvidenceError("artifact kind must be exe", category="PLAN_INVALID")
    if not isinstance(artifact.get("path"), str) or not artifact["path"]:
        raise EvidenceError("artifact path is invalid", category="PLAN_INVALID")
    if type(artifact.get("bytes")) is not int or artifact["bytes"] <= 0:
        raise EvidenceError("artifact bytes are invalid", category="PLAN_INVALID")
    if not isinstance(artifact.get("sha256"), str) or len(artifact["sha256"]) != 64:
        raise EvidenceError("artifact sha256 is invalid", category="PLAN_INVALID")
    if SHA256_RE.fullmatch(artifact["sha256"]) is None:
        raise EvidenceError("artifact sha256 is invalid", category="PLAN_INVALID")
    if artifact.get("basename") != artifact.get("relative_path"):
        raise EvidenceError("artifact relative path must match basename", category="PLAN_INVALID")
    plan = state["plan"]
    if (
        plan.get("target_id") != TARGET_ID
        or plan.get("run_id") != state["run_id"]
        or plan.get("local_run_dir") != state["paths"]["local_run_dir"]
        or plan.get("target_config") != state["target_config"]
        or plan.get("source_commit") != state["source"]["commit"]
        or plan.get("source_tree") != state["source"]["tree"]
        or plan.get("source_branch") != state["source"]["branch"]
    ):
        raise EvidenceError("state frozen plan identity drifted", category="PLAN_INVALID")
    if state.get("remote_build_succeeded") is not True or state.get("fetch_allowed") is not False:
        raise EvidenceError("state terminal flags are invalid", category="PLAN_INVALID")
    expected_identity = {
        "asset_provenance_sha256": plan.get("asset_provenance_sha256"),
        "cache_requirements_sha256": plan.get("cache_requirements_sha256"),
        "cache_observation_sha256": plan.get("cache_observation_sha256"),
        "host_facts_sha256": plan.get("host_facts_sha256"),
    }
    for key, expected in expected_identity.items():
        if state["identity"].get(key) != expected:
            raise EvidenceError(f"state identity drifted: {key}", category="PLAN_INVALID")
    return artifact


def _inspect_pe_bytes(data: bytes) -> dict[str, str]:
    if len(data) < 256 or data[:2] != b"MZ":
        raise EvidenceError("artifact is not an MZ executable", category="LOCAL_REVIEW_INVALID")
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if pe_offset + 26 >= len(data) or data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        raise EvidenceError("artifact PE signature is invalid", category="LOCAL_REVIEW_INVALID")
    return {
        "pe_machine": "0x{:04x}".format(int.from_bytes(data[pe_offset + 4:pe_offset + 6], "little")),
        "pe_optional_magic": "0x{:03x}".format(int.from_bytes(data[pe_offset + 24:pe_offset + 26], "little")),
    }


def _payload_manifest_identity(payload_manifest: dict) -> str:
    identity = {
        key: value
        for key, value in payload_manifest.items()
        if key != "manifest_sha256"
    }
    return _sha256_bytes(_canonical_json_bytes(identity))


def _formal_log_identity(path: Path) -> tuple[list[str], bytes]:
    payload = _require_private_file(path, "formal build log")
    if b"\r" in payload:
        raise EvidenceError("formal build log must use LF", category="LOCAL_REVIEW_INVALID")
    lines = payload.decode("utf-8").splitlines()
    if lines != list(FORMAL_LOG_LINES):
        raise EvidenceError("formal build log is invalid", category="LOCAL_REVIEW_INVALID")
    return lines, payload


def _remote_log_identity(path: Path) -> bytes:
    payload = _require_private_file(path, "remote build log")
    if b"\r" in payload or not payload.endswith(b"\n"):
        raise EvidenceError("remote build log must use one LF per line", category="LOCAL_REVIEW_INVALID")
    try:
        lines = payload.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise EvidenceError("remote build log is not UTF-8", category="LOCAL_REVIEW_INVALID") from exc
    if lines != list(REMOTE_LOG_LINES):
        raise EvidenceError("remote build log checks are not exact", category="LOCAL_REVIEW_INVALID")
    return payload


def _expected_review_names(artifact_basename: str):
    return {
        artifact_basename,
        artifact_basename + ".sha256",
        *REVIEW_BASENAMES,
    }


def _state_review_paths(state, artifact_path: Path):
    paths = state.get("paths")
    local_run_dir = paths.get("local_run_dir") if isinstance(paths, dict) else None
    if not isinstance(local_run_dir, str) or not local_run_dir:
        raise EvidenceError("state paths.local_run_dir is missing", category="PLAN_INVALID")
    run_dir = Path(local_run_dir).resolve()
    review_dir = run_dir / "review"
    remote_log = run_dir / "remote-build.log"
    if artifact_path.resolve().parent != review_dir:
        raise EvidenceError("artifact path must live under local_run_dir/review", category="PLAN_INVALID")
    return review_dir, remote_log


def _inspect_review(state):
    artifact = _validate_state(state)
    artifact_path = Path(artifact["path"])
    artifact_bytes = _require_private_file(artifact_path, "candidate artifact")
    if len(artifact_bytes) != artifact["bytes"] or _sha256_bytes(artifact_bytes) != artifact["sha256"]:
        raise EvidenceError("artifact sha drifted", category="LOCAL_REVIEW_INVALID")
    pe_identity = _inspect_pe_bytes(artifact_bytes)
    review_dir, remote_log_path = _state_review_paths(state, artifact_path)
    _require_private_directory(review_dir, "review directory")
    remote_log_bytes = _remote_log_identity(remote_log_path)
    actual_names = {entry.name for entry in review_dir.iterdir()}
    expected_names = _expected_review_names(artifact["basename"])
    if actual_names != expected_names:
        raise EvidenceError("review exact set drifted", category="LOCAL_REVIEW_INVALID")
    sidecar_path = review_dir / (artifact["basename"] + ".sha256")
    sidecar_bytes = _require_private_file(sidecar_path, "artifact sidecar")
    expected_sidecar = "{}  {}\n".format(artifact["sha256"], artifact["basename"]).encode("utf-8")
    if sidecar_bytes != expected_sidecar:
        raise EvidenceError("artifact sidecar drifted", category="LOCAL_REVIEW_INVALID")
    manifest, manifest_bytes = _load_canonical_json_file(
        review_dir / "taiji-package-manifest.json", "package manifest"
    )
    marker, marker_bytes = _load_canonical_json_file(
        review_dir / ".build-success", "success marker"
    )
    remote_state, remote_state_bytes = _load_canonical_json_file(
        review_dir / "run-state.json", "remote run state"
    )
    report_bytes = _require_private_file(review_dir / "构建报告.txt", "build report")
    formal_lines, formal_bytes = _formal_log_identity(review_dir / "formal-build-tests.log")
    expected_report = "Windows candidate review PASS\nrun={}\n".format(state["run_id"]).encode("utf-8")
    if report_bytes != expected_report:
        raise EvidenceError("build report is invalid", category="LOCAL_REVIEW_INVALID")
    _require_exact(manifest, MANIFEST_KEYS, "package manifest")
    _require_exact(marker, MARKER_KEYS, "success marker")
    _require_exact(remote_state, REMOTE_STATE_KEYS, "remote run state")
    if manifest.get("schema") != "taiji-package-manifest/v2":
        raise EvidenceError("package manifest schema is invalid", category="LOCAL_REVIEW_INVALID")
    if manifest.get("run_id") != state["run_id"] or manifest.get("target_id") != TARGET_ID:
        raise EvidenceError("package manifest identity drifted", category="LOCAL_REVIEW_INVALID")
    source = manifest.get("source")
    _require_exact(source, {"branch", "commit", "tree"}, "package manifest source")
    _require_commit(source["commit"], "package manifest source commit")
    _require_commit(source["tree"], "package manifest source tree")
    if source != {
        "branch": state["source"]["branch"],
        "commit": state["source"]["commit"],
        "tree": state["source"]["tree"],
    }:
        raise EvidenceError("package manifest source drifted", category="LOCAL_REVIEW_INVALID")
    identity_fields = (
        ("target_config_sha256", state["target_config_sha256"]),
        ("asset_provenance_sha256", state["identity"]["asset_provenance_sha256"]),
        ("cache_requirements_sha256", state["identity"]["cache_requirements_sha256"]),
        ("cache_observation_sha256", state["identity"]["cache_observation_sha256"]),
    )
    for key, expected in identity_fields:
        _require_sha256(manifest.get(key), f"package manifest {key}")
        if manifest[key] != expected:
            raise EvidenceError(f"package manifest identity drifted: {key}", category="LOCAL_REVIEW_INVALID")

    _require_exact(manifest.get("input"), {"archive", "manifest", "sidecar"}, "package manifest input")
    state_files = state["input"]["files"]
    for review_role, state_role in (("archive", "archive"), ("manifest", "manifest"), ("sidecar", "checksum")):
        metadata = manifest["input"][review_role]
        _require_exact(metadata, {"basename", "bytes", "sha256"}, f"package manifest input {review_role}")
        expected = state_files[state_role]
        if metadata != {key: expected[key] for key in ("basename", "bytes", "sha256")}:
            raise EvidenceError(f"package manifest input drifted: {review_role}", category="LOCAL_REVIEW_INVALID")
    manifest_artifact = manifest.get("artifact")
    _require_exact(manifest_artifact, ARTIFACT_KEYS, "package manifest artifact")
    version = state["plan"].get("version")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise EvidenceError("frozen candidate version is invalid", category="PLAN_INVALID")
    expected_basename = "TaijiAgent-Setup-{}-win-x64.exe".format(version)
    if artifact["basename"] != expected_basename or manifest_artifact.get("basename") != artifact["basename"]:
        raise EvidenceError("manifest artifact basename drifted", category="LOCAL_REVIEW_INVALID")
    if manifest_artifact.get("bytes") != artifact["bytes"]:
        raise EvidenceError("manifest artifact bytes drifted", category="LOCAL_REVIEW_INVALID")
    if manifest_artifact.get("sha256") != artifact["sha256"]:
        raise EvidenceError("manifest artifact sha drifted", category="LOCAL_REVIEW_INVALID")
    if manifest_artifact.get("kind") != "exe" or manifest_artifact.get("version") != version:
        raise EvidenceError("manifest artifact version drifted", category="LOCAL_REVIEW_INVALID")
    if manifest_artifact.get("file_version") != version + ".0" or manifest_artifact.get("product_version") != version + ".0":
        raise EvidenceError("manifest PE version drifted", category="LOCAL_REVIEW_INVALID")
    if manifest_artifact.get("authenticode_status") != "NotSigned":
        raise EvidenceError("artifact must remain NotSigned", category="LOCAL_REVIEW_INVALID")
    if manifest_artifact.get("pe_machine") != "0x8664":
        raise EvidenceError("artifact PE machine drifted", category="LOCAL_REVIEW_INVALID")
    if manifest_artifact.get("pe_optional_magic") != "0x20b":
        raise EvidenceError("artifact PE optional magic drifted", category="LOCAL_REVIEW_INVALID")
    if pe_identity["pe_machine"] != "0x8664" or pe_identity["pe_optional_magic"] != "0x20b":
        raise EvidenceError("artifact PE bytes drifted", category="LOCAL_REVIEW_INVALID")
    payload_manifest = manifest.get("payload")
    _require_exact(payload_manifest, PAYLOAD_KEYS, "payload manifest")
    if (
        payload_manifest.get("schema") != "taiji-windows-payload-manifest/v1"
        or payload_manifest.get("source_commit") != source["commit"]
        or payload_manifest.get("source_tree") != source["tree"]
    ):
        raise EvidenceError("payload manifest source drifted", category="LOCAL_REVIEW_INVALID")
    if payload_manifest.get("manifest_sha256") != _payload_manifest_identity(payload_manifest):
        raise EvidenceError("payload manifest sha drifted", category="LOCAL_REVIEW_INVALID")
    entries = payload_manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise EvidenceError("payload entries are invalid", category="LOCAL_REVIEW_INVALID")
    previous_path_bytes = None
    total_bytes = 0
    for entry in entries:
        _require_exact(entry, {"path", "bytes", "sha256"}, "payload entry")
        path_value = entry["path"]
        if not isinstance(path_value, str) or not path_value or "\\" in path_value or path_value.startswith("/"):
            raise EvidenceError("payload entry path is invalid", category="LOCAL_REVIEW_INVALID")
        encoded_path = path_value.encode("utf-8")
        if previous_path_bytes is not None and encoded_path <= previous_path_bytes:
            raise EvidenceError("payload entries are not strictly ordered", category="LOCAL_REVIEW_INVALID")
        previous_path_bytes = encoded_path
        if type(entry["bytes"]) is not int or entry["bytes"] < 0:
            raise EvidenceError("payload entry bytes are invalid", category="LOCAL_REVIEW_INVALID")
        _require_sha256(entry["sha256"], "payload entry SHA")
        total_bytes += entry["bytes"]
    if payload_manifest.get("file_count") != len(entries) or payload_manifest.get("total_bytes") != total_bytes:
        raise EvidenceError("payload aggregate identity drifted", category="LOCAL_REVIEW_INVALID")
    formal_tests = manifest.get("formal_tests")
    if (
        not isinstance(formal_tests, dict)
        or formal_tests.get("status") != "PASS"
        or formal_tests.get("log_basename") != "formal-build-tests.log"
        or formal_tests.get("log_bytes") != len(formal_bytes)
        or formal_tests.get("log_sha256") != _sha256_bytes(formal_bytes)
        or formal_tests.get("checks") != [
            {"id": "source-session-identity", "result": "PASS", "exit_code": 0},
            {"id": "offline-npm-ci", "result": "PASS", "exit_code": 0},
            {"id": "electron-win32-x64", "result": "PASS", "exit_code": 0},
            {"id": "payload-import-menu-policy", "result": "PASS", "exit_code": 0},
            {"id": "payload-hygiene-closure", "result": "PASS", "exit_code": 0},
            {"id": "inno-compile", "result": "PASS", "exit_code": 0},
            {"id": "installer-pe-version-authenticode", "result": "PASS", "exit_code": 0},
        ]
    ):
        raise EvidenceError("formal test identity drifted", category="LOCAL_REVIEW_INVALID")
    boundaries = manifest.get("boundaries")
    if boundaries != {
        "installation": False,
        "interactive_acceptance": False,
        "production_license": False,
        "signing": False,
        "publication": False,
    }:
        raise EvidenceError("manifest boundaries drifted", category="LOCAL_REVIEW_INVALID")
    tools = manifest.get("tools")
    if not isinstance(tools, dict) or set(tools) != TOOL_NAMES:
        raise EvidenceError("tool identity set is invalid", category="LOCAL_REVIEW_INVALID")
    if tools["safe_tar"].get("version") != "taiji-safe-tar/v1":
        raise EvidenceError("safe tar version drifted", category="LOCAL_REVIEW_INVALID")
    for tool_name, tool_value in tools.items():
        if (
            not isinstance(tool_value, dict)
            or set(tool_value) != {"path", "bytes", "sha256", "version"}
            or not isinstance(tool_value.get("path"), str)
            or not tool_value.get("path")
            or type(tool_value.get("bytes")) is not int
            or tool_value["bytes"] <= 0
            or not isinstance(tool_value.get("sha256"), str)
            or len(tool_value["sha256"]) != 64
            or not isinstance(tool_value.get("version"), str)
            or not tool_value["version"].strip()
        ):
            raise EvidenceError(
                f"tool identity is invalid: {tool_name}", category="LOCAL_REVIEW_INVALID"
            )
        _require_sha256(tool_value["sha256"], f"tool {tool_name} SHA")
        if tool_name != "safe_tar" and tool_value["path"] != state["target_config"][tool_name]:
            raise EvidenceError(f"tool path drifted: {tool_name}", category="LOCAL_REVIEW_INVALID")
    safe_tar_plan = state["plan"].get("controller_bootstrap", {}).get("safe_tar", {})
    expected_safe_tar_path = state["host"]["remote_run_dir"].rstrip("\\/") + "\\" + str(
        safe_tar_plan.get("remote_path", "")
    ).replace("/", "\\").lstrip("\\")
    if tools["safe_tar"]["path"] != expected_safe_tar_path:
        raise EvidenceError("safe tar path drifted", category="LOCAL_REVIEW_INVALID")

    manifest_started = _require_utc(manifest.get("started_at"), "package manifest started_at")
    manifest_finished = _require_utc(manifest.get("finished_at"), "package manifest finished_at")
    if manifest_finished < manifest_started:
        raise EvidenceError("package manifest timestamps are reversed", category="LOCAL_REVIEW_INVALID")
    if remote_state.get("schema") != "taiji-package-remote-run/v1":
        raise EvidenceError("remote run state schema is invalid", category="LOCAL_REVIEW_INVALID")
    if (
        remote_state.get("run_id") != state["run_id"]
        or remote_state.get("target_id") != TARGET_ID
        or remote_state.get("terminal_status") != "REMOTE_BUILD_SUCCEEDED"
        or remote_state.get("source_commit") != source["commit"]
        or remote_state.get("host_facts_sha256") != state["identity"]["host_facts_sha256"]
    ):
        raise EvidenceError("remote run state identity drifted", category="LOCAL_REVIEW_INVALID")
    remote_started = _require_utc(remote_state.get("started_at"), "remote state started_at")
    remote_finished = _require_utc(remote_state.get("finished_at"), "remote state finished_at")
    if remote_finished < remote_started:
        raise EvidenceError("remote state timestamps are reversed", category="LOCAL_REVIEW_INVALID")
    history = remote_state.get("stage_history")
    if not isinstance(history, list) or not history:
        raise EvidenceError("remote stage history is empty", category="LOCAL_REVIEW_INVALID")
    previous_finished = None
    for item in history:
        _require_exact(item, {"stage", "started_at", "finished_at", "result"}, "remote stage history item")
        item_started = _require_utc(item["started_at"], "remote stage started_at")
        item_finished = _require_utc(item["finished_at"], "remote stage finished_at")
        if (
            not isinstance(item["stage"], str)
            or not item["stage"]
            or item["result"] != "PASS"
            or item_finished < item_started
            or (previous_finished is not None and item_started < previous_finished)
        ):
            raise EvidenceError("remote stage history is invalid", category="LOCAL_REVIEW_INVALID")
        previous_finished = item_finished
    if history[-1]["stage"] != "review-ready":
        raise EvidenceError("remote stage history does not end at review-ready", category="LOCAL_REVIEW_INVALID")
    if marker.get("schema") != "taiji-package-build-success/v1":
        raise EvidenceError("success marker schema is invalid", category="LOCAL_REVIEW_INVALID")
    expected_marker = {
        "run_id": state["run_id"],
        "target_id": TARGET_ID,
        "source_commit": source["commit"],
        "artifact_basename": artifact["basename"],
        "artifact_bytes": artifact["bytes"],
        "artifact_sha256": artifact["sha256"],
        "package_manifest_basename": "taiji-package-manifest.json",
        "package_manifest_bytes": len(manifest_bytes),
        "package_manifest_sha256": _sha256_bytes(manifest_bytes),
        "formal_build_tests_log_basename": "formal-build-tests.log",
        "formal_build_tests_log_bytes": len(formal_bytes),
        "formal_build_tests_log_sha256": _sha256_bytes(formal_bytes),
        "report_basename": "构建报告.txt",
        "report_bytes": len(report_bytes),
        "report_sha256": _sha256_bytes(report_bytes),
        "remote_state_basename": "run-state.json",
        "remote_state_bytes": len(remote_state_bytes),
        "remote_state_sha256": _sha256_bytes(remote_state_bytes),
    }
    for key, expected in expected_marker.items():
        if marker.get(key) != expected:
            raise EvidenceError(f"success marker drifted: {key}", category="LOCAL_REVIEW_INVALID")
    return {
        "artifact_path": artifact_path.resolve(),
        "artifact_bytes": artifact_bytes,
        "artifact_sha256": artifact["sha256"],
        "review_dir": review_dir.resolve(),
        "remote_log_path": remote_log_path.resolve(),
        "remote_log_sha256": _sha256_bytes(remote_log_bytes),
        "source_commit": source["commit"],
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "marker_sha256": _sha256_bytes(marker_bytes),
        "formal_lines": formal_lines,
    }


def build_evidence_payload(state):
    _helper_self_check()
    inspected = _inspect_review(state)
    return {
        "schema": "taiji-windows-candidate-evidence/v1",
        "target_id": TARGET_ID,
        "run_id": state["run_id"],
        "stage": REQUIRED_STAGE,
        "artifact": {
            "basename": inspected["artifact_path"].name,
            "bytes": len(inspected["artifact_bytes"]),
            "sha256": inspected["artifact_sha256"],
            "path": str(inspected["artifact_path"]),
        },
        "review": {
            "path": str(inspected["review_dir"]),
            "remote_log_path": str(inspected["remote_log_path"]),
            "remote_log_sha256": inspected["remote_log_sha256"],
            "manifest_sha256": inspected["manifest_sha256"],
            "marker_sha256": inspected["marker_sha256"],
        },
        "evidence_layers": {
            "Source": CURRENT_VERIFIED,
            "Payload": CURRENT_VERIFIED,
            "Installer": CURRENT_VERIFIED,
            "Installed Runtime": NOT_VERIFIED,
            "Interactive Acceptance": NOT_VERIFIED,
            "Production License": NOT_COMPLETED,
            "Release": NOT_EXECUTED,
        },
        "helper": {
            "python": "/usr/bin/python3",
            "path": str(MODULE_PATH),
            "flags": ["-I", "-B"],
        },
    }


def render_handoff(state):
    payload = build_evidence_payload(state)
    artifact = payload["artifact"]
    return "\n".join(
        [
            "# Windows Candidate Handoff",
            "",
            "- target: windows-x64",
            "- stage: CANDIDATE_BUILT",
            f"- artifact: {artifact['basename']}",
            f"- sha256: {artifact['sha256']}",
            "- Source/Payload/Installer: CURRENT_VERIFIED",
            "- Installed Runtime: NOT_VERIFIED，未安装，未启动",
            "- Interactive Acceptance: NOT_VERIFIED，未GUI验收",
            "- Production License: NOT_COMPLETED，未production授权",
            "- Installer Signing: CURRENT_VERIFIED，未签名",
            "- Release: NOT_EXECUTED，未发布",
            "",
        ]
    )


def _fsync_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    except OSError:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _existing_bundle_bytes(path: Path):
    if not os.path.lexists(str(path)):
        return None
    _require_private_directory(path, "evidence directory")
    entries = sorted(item.name for item in path.iterdir())
    if entries != sorted([HANDOFF_MD, EVIDENCE_JSON]):
        raise EvidenceError("existing evidence output is occupied", category="LOCAL_OUTPUT_OCCUPIED")
    return {
        EVIDENCE_JSON: _require_private_file(path / EVIDENCE_JSON, EVIDENCE_JSON),
        HANDOFF_MD: _require_private_file(path / HANDOFF_MD, HANDOFF_MD),
    }


def _load_persisted_state(run_dir: Path, run_id: str):
    core_models, core_state = _core_contracts()
    if run_dir.name != run_id or run_dir.parent.name != "runs":
        raise EvidenceError("run directory identity drifted", category="PLAN_INVALID")
    state_root = run_dir.parent.parent
    try:
        persisted = core_state.RunStateStore(state_root).load(run_id)
        core_models.validate_v2_state(persisted)
    except Exception as exc:
        raise EvidenceError(f"persisted run state is invalid: {exc}", category="PLAN_INVALID") from exc
    return persisted


@contextmanager
def _exclusive_run_directory(path: Path):
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def publish_evidence_bundle(run_dir, state):
    requested_run_dir = Path(run_dir)
    _require_private_directory(requested_run_dir, "run directory")
    run_dir = requested_run_dir.resolve()
    _validate_state(state)
    if Path(state["paths"]["local_run_dir"]).resolve() != run_dir:
        raise EvidenceError("state local run directory drifted", category="PLAN_INVALID")
    persisted = _load_persisted_state(run_dir, state["run_id"])
    if persisted != state:
        raise EvidenceError("provided state differs from persisted terminal state", category="PLAN_INVALID")

    with _exclusive_run_directory(run_dir):
        payload = build_evidence_payload(persisted)
        handoff_text = render_handoff(persisted)
        json_bytes = _canonical_json_bytes(payload) + b"\n"
        handoff_bytes = handoff_text.encode("utf-8")
        final_dir = run_dir / EVIDENCE_DIRNAME
        existing = _existing_bundle_bytes(final_dir)
        if existing is not None:
            if existing[EVIDENCE_JSON] == json_bytes and existing[HANDOFF_MD] == handoff_bytes:
                return {
                    "status": "EVIDENCE_READY",
                    "directory": str(final_dir.resolve()),
                    "files": [str((final_dir / EVIDENCE_JSON).resolve()), str((final_dir / HANDOFF_MD).resolve())],
                }
            raise EvidenceError("existing evidence output differs", category="LOCAL_OUTPUT_OCCUPIED")
        staging_dir = run_dir / (STAGING_PREFIX + secrets.token_hex(8))
        try:
            staging_dir.mkdir(mode=0o700)
        except OSError as exc:
            raise EvidenceError(
                f"cannot create evidence staging directory: {exc}",
                category="LOCAL_OUTPUT_OCCUPIED",
            ) from exc
        os.chmod(staging_dir, 0o700)
        _fsync_file(staging_dir / EVIDENCE_JSON, json_bytes)
        _fsync_file(staging_dir / HANDOFF_MD, handoff_bytes)
        _fsync_directory(staging_dir)
        try:
            os.rename(str(staging_dir), str(final_dir))
            _fsync_directory(run_dir)
        except OSError as exc:
            raise EvidenceError(
                f"cannot publish evidence bundle: {exc}", category="LOCAL_OUTPUT_OCCUPIED"
            ) from exc
    return {
        "status": "EVIDENCE_READY",
        "directory": str(final_dir.resolve()),
        "files": [str((final_dir / EVIDENCE_JSON).resolve()), str((final_dir / HANDOFF_MD).resolve())],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish Windows candidate evidence")
    subparsers = parser.add_subparsers(dest="command")
    write_parser = subparsers.add_parser("write", help="publish from persisted run state")
    write_parser.add_argument("--state-root", required=True)
    write_parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    if args.command != "write":
        return 0
    run_root = Path(args.state_root) / "runs" / args.run
    state = _load_persisted_state(run_root.resolve(), args.run)
    if state.get("paths", {}).get("local_run_dir") != str(run_root.resolve()):
        raise EvidenceError("run state local_run_dir drifted", category="PLAN_INVALID")
    result = publish_evidence_bundle(run_root, state)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
