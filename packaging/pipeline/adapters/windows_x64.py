"""Windows x64 candidate adapter with a fake-phase-only transport boundary."""

import copy
import hashlib
import inspect
import json
import os
import re
import stat
import struct
import subprocess
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core.errors import PipelineError
from ..core.models import canonical_json_sha256
from .base import CandidateAdapter


ROOT = Path(__file__).resolve().parents[3]
CACHE_REQUIREMENTS_PATH = ROOT / "packaging/windows/cache-requirements.json"
ASSET_PROVENANCE_PATH = ROOT / "packaging/windows/asset-provenance.json"
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:\\.+")


EXPECTED_TARGET_KEYS = {
    "allowed_source_branches", "architecture", "cache_requirements", "cache_root",
    "git", "host_alias", "iscc", "minimum_free_gib", "node", "npm", "powershell",
    "python", "remote_root", "schema", "target_id", "tar",
}

REVIEW_FORMAL_CHECK_IDS = (
    "source-session-identity",
    "offline-npm-ci",
    "electron-win32-x64",
    "payload-import-menu-policy",
    "payload-hygiene-closure",
    "inno-compile",
    "installer-pe-version-authenticode",
)
REVIEW_MANIFEST_KEYS = {
    "schema", "run_id", "target_id", "source", "input", "target_config_sha256",
    "asset_provenance_sha256", "cache_requirements_sha256", "cache_observation_sha256",
    "tools", "payload", "formal_tests", "artifact", "boundaries", "started_at", "finished_at",
}
REVIEW_PAYLOAD_KEYS = {
    "entries", "file_count", "manifest_sha256", "schema", "source_commit", "source_tree", "total_bytes",
}
REVIEW_ARTIFACT_KEYS = {
    "authenticode_status", "basename", "bytes", "file_version", "kind", "pe_machine",
    "pe_optional_magic", "product_version", "sha256", "version",
}
REVIEW_REMOTE_STATE_KEYS = {
    "schema", "run_id", "target_id", "source_commit", "host_facts_sha256", "stage_history",
    "terminal_status", "started_at", "finished_at",
}
REVIEW_MARKER_KEYS = {
    "schema", "run_id", "target_id", "source_commit", "artifact_basename", "artifact_bytes",
    "artifact_sha256", "package_manifest_basename", "package_manifest_bytes", "package_manifest_sha256",
    "formal_build_tests_log_basename", "formal_build_tests_log_bytes", "formal_build_tests_log_sha256",
    "report_basename", "report_bytes", "report_sha256", "remote_state_basename", "remote_state_bytes",
    "remote_state_sha256",
}
REVIEW_INPUT_ROLES = ("archive", "manifest", "sidecar")
REVIEW_TOOL_NAMES = ("iscc", "node", "npm", "powershell", "python", "safe_tar", "tar")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
VERSION_FULL_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.0\Z")


def _pipeline_error(message, category="PLAN_INVALID"):
    raise PipelineError(message, category=category)


def _read_json(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _pipeline_error("{} is unreadable: {}".format(label, exc))
    if not isinstance(value, dict):
        _pipeline_error("{} must be an object".format(label))
    return value


def _review_error(message):
    _pipeline_error(message, "LOCAL_REVIEW_INVALID")


def _canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_exact(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        _review_error("{} fields are not exact".format(label))


def _regular_file(path, label):
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        _review_error("{} is unavailable: {}".format(label, exc))
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
    ):
        _review_error("{} is not a private regular file".format(label))
    return path


def _file_sha256_and_size(path, label):
    path = _regular_file(path, label)
    try:
        data = path.read_bytes()
    except OSError as exc:
        _review_error("{} cannot be read: {}".format(label, exc))
    return len(data), hashlib.sha256(data).hexdigest(), data


def _read_canonical_json(path, label):
    _size, _sha, raw = _file_sha256_and_size(path, label)
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n") or raw[:-1].endswith(b"\n"):
        _review_error("{} is not canonical UTF-8 JSON".format(label))
    try:
        text = raw[:-1].decode("utf-8", errors="strict")
        value = json.loads(text)
    except (UnicodeError, ValueError) as exc:
        _review_error("{} is invalid JSON: {}".format(label, exc))
    if _canonical_bytes(value) + b"\n" != raw:
        _review_error("{} is not canonical JSON".format(label))
    return value


def _sha256_value(value, label):
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _review_error("{} is not a lowercase SHA256".format(label))
    return value


def _commit_value(value, label):
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        _review_error("{} is not a lowercase commit".format(label))
    return value


def _utc_value(value, label):
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _review_error("{} is not a UTC timestamp".format(label))
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        _review_error("{} is invalid: {}".format(label, exc))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _review_error("{} is not UTC".format(label))
    return parsed


def _basename_value(value, label):
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or Path(value).name != value
    ):
        _review_error("{} is not a safe basename".format(label))
    return value


def _positive_bytes(value, label):
    if type(value) is not int or value <= 0:
        _review_error("{} is not a positive byte count".format(label))
    return value


def _sha256_file(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        _pipeline_error("cannot read {}: {}".format(path, exc), "PLAN_INVALID")


def _new_run_id(source_commit):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "{}-{}-{}".format(timestamp, uuid.uuid4().hex[:12], source_commit[:8])


def _result_stdout(result):
    if isinstance(result, subprocess.CompletedProcess):
        if result.returncode != 0:
            _pipeline_error("controller Git command failed", "LOCAL_PREFLIGHT_FAILED")
        return result.stdout
    if getattr(result, "returncode", 0) != 0:
        _pipeline_error("controller Git command failed", "LOCAL_PREFLIGHT_FAILED")
    return getattr(result, "stdout", result)


class WindowsX64Adapter(CandidateAdapter):
    target_id = "windows-x64"
    artifact_kind = "exe"
    success_label = "候选 EXE 已构建"
    pending_label = "候选 EXE 取回待恢复"
    not_built_label = "候选 EXE 未构建"
    online_plan_keys = (
        "cache_requirements_sha256",
        "cache_observation",
        "cache_observation_sha256",
        "host_facts",
        "host_facts_sha256",
    )

    def __init__(
        self,
        transport_factory=None,
        review_validator=None,
        controller_runner=None,
        artifact_inspector=None,
    ):
        self.transport_factory = transport_factory
        self.review_validator = review_validator
        self.controller_runner = controller_runner
        self.artifact_inspector = artifact_inspector

    def validate_target(self, payload):
        if not isinstance(payload, dict) or set(payload) != EXPECTED_TARGET_KEYS:
            _pipeline_error("Windows target fields are invalid", "TARGET_INVALID")
        if payload.get("schema") != "taiji-package-target/v2" or payload.get("target_id") != self.target_id:
            _pipeline_error("Windows target identity is invalid", "TARGET_INVALID")
        if payload.get("architecture") != "x64" or payload.get("allowed_source_branches") != ["main"]:
            _pipeline_error("Windows target source policy is invalid", "TARGET_INVALID")
        if type(payload.get("minimum_free_gib")) is not int or payload["minimum_free_gib"] <= 0:
            _pipeline_error("Windows target free-space policy is invalid", "TARGET_INVALID")
        for key in ("cache_root", "git", "iscc", "node", "npm", "powershell", "python", "remote_root", "tar"):
            value = payload.get(key)
            if not isinstance(value, str) or not WINDOWS_PATH_RE.fullmatch(value):
                _pipeline_error("Windows target path is invalid: {}".format(key), "TARGET_INVALID")
            lowered = value.lower()
            if any(token in lowered for token in ("password", "secret", "private_key", "ssh_key", "@")):
                _pipeline_error("Windows target contains forbidden credential material", "TARGET_INVALID")
        if payload.get("cache_requirements") != "packaging/windows/cache-requirements.json":
            _pipeline_error("Windows cache requirements path is invalid", "TARGET_INVALID")
        if not isinstance(payload.get("host_alias"), str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", payload["host_alias"]) is None:
            _pipeline_error("Windows host alias is invalid", "TARGET_INVALID")
        if not isinstance(payload.get("remote_root"), str):
            _pipeline_error("Windows remote root is invalid", "TARGET_INVALID")
        return copy.deepcopy(payload)

    def _controller_git(self, repo, args):
        allowed = (
            ("status", "--porcelain=v2", "--branch"),
            ("rev-parse", "HEAD^{commit}"),
            ("rev-parse", "HEAD^{tree}"),
        )
        if tuple(args) not in allowed and not (
            len(args) == 2 and args[0] == "show" and ":" in args[1]
        ):
            _pipeline_error("Windows controller Git command is outside allowlist", "LOCAL_PREFLIGHT_FAILED")
        command = ["/usr/bin/git", "-C", str(Path(repo))] + list(args)
        runner = self.controller_runner
        if runner is None:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        else:
            result = runner(command)
        return _result_stdout(result)

    def _controller_identity(self, repo):
        status = str(self._controller_git(repo, ("status", "--porcelain=v2", "--branch")))
        branch = None
        dirty = []
        for line in status.splitlines():
            if line.startswith("# branch.head "):
                branch = line[len("# branch.head "):].strip()
            elif line and not line.startswith("#"):
                dirty.append(line)
        if branch is None:
            _pipeline_error("controller branch identity is unavailable", "REPO_IDENTITY_MISMATCH")
        commit = str(self._controller_git(repo, ("rev-parse", "HEAD^{commit}"))).strip()
        tree = str(self._controller_git(repo, ("rev-parse", "HEAD^{tree}"))).strip()
        if COMMIT_RE.fullmatch(commit) is None:
            _pipeline_error("controller commit identity is invalid", "SOURCE_COMMIT_INVALID")
        if COMMIT_RE.fullmatch(tree) is None:
            _pipeline_error("controller tree identity is invalid", "REPO_IDENTITY_MISMATCH")
        if dirty:
            _pipeline_error("controller worktree is dirty", "WORKTREE_NOT_CLEAN")
        version = str(self._controller_git(repo, ("show", "{}:VERSION".format(commit)))).strip()
        package_text = str(
            self._controller_git(repo, ("show", "{}:apps/taiji-desktop/package.json".format(commit)))
        )
        if VERSION_RE.fullmatch(version) is None:
            _pipeline_error("controller VERSION is invalid", "REPO_IDENTITY_MISMATCH")
        try:
            package = json.loads(package_text)
        except (TypeError, ValueError) as exc:
            _pipeline_error("desktop package JSON is invalid: {}".format(exc), "REPO_IDENTITY_MISMATCH")
        if not isinstance(package, dict) or package.get("version") != version:
            _pipeline_error("controller version sources differ", "REPO_IDENTITY_MISMATCH")
        return {"branch": branch, "commit": commit, "tree": tree, "version": version}

    def local_doctor(self, repo, target, state_root, *, ssh_config):
        del state_root, ssh_config
        try:
            target = self.validate_target(target)
            identity = self._controller_identity(repo)
        except PipelineError as exc:
            return {
                "controller_status": "BLOCKED",
                "builder_status": "BUILDER_UNREACHABLE",
                "blockers": [str(exc)],
                "failure_categories": [exc.category],
            }
        if identity["branch"] not in target["allowed_source_branches"]:
            return {
                "controller_status": "BLOCKED",
                "builder_status": "BUILDER_UNREACHABLE",
                "blockers": ["source branch is not allowed: {}".format(identity["branch"])],
                "failure_categories": ["BRANCH_NOT_MAIN"],
            }
        return {
            "controller_status": "CONTROLLER_READY",
            "builder_status": "BUILDER_UNREACHABLE",
            "blockers": [],
            "failure_categories": [],
            "source": identity,
        }

    def inspect_input(self, repo, source_commit):
        names = self._input_basenames(source_commit)
        files = {}
        roles = (("archive", names["archive"]), ("manifest", names["manifest"]), ("checksum", names["sidecar"]))
        for role, basename in roles:
            path = Path(repo) / basename
            try:
                metadata = path.lstat()
                if not path.is_file() or path.is_symlink() or metadata.st_nlink != 1:
                    return {"status": "MISSING", "files": {}}
                data = path.read_bytes()
            except OSError:
                return {"status": "MISSING", "files": {}}
            files[role] = {
                "path": str(path),
                "basename": basename,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "exists": True,
            }
        return {"status": "REUSABLE", "files": files}

    def _input_basenames(self, source_commit):
        if COMMIT_RE.fullmatch(str(source_commit)) is None:
            _pipeline_error("source commit is invalid", "SOURCE_COMMIT_INVALID")
        stem = "taijiagent-windows-builder-input-{}".format(source_commit)
        return {
            "archive": stem + ".tar.gz",
            "manifest": stem + ".manifest.json",
            "sidecar": stem + ".tar.gz.sha256",
        }

    def build_plan(self, repo, target, state_root, *, run_id, ssh_config):
        del ssh_config
        controller_repo = repo
        repo = Path(repo).resolve()
        target = self.validate_target(target)
        identity = self._controller_identity(controller_repo)
        if identity["branch"] not in target["allowed_source_branches"]:
            _pipeline_error("source branch is not allowed", "BRANCH_NOT_MAIN")
        run_id = run_id or _new_run_id(identity["commit"])
        names = self._input_basenames(identity["commit"])
        requirements = _read_json(CACHE_REQUIREMENTS_PATH, "Windows cache requirements")
        if requirements.get("target_id") != self.target_id:
            _pipeline_error("Windows cache requirements target is invalid")
        return {
            "schema": "taiji-package-candidate-plan/v1",
            "run_id": run_id,
            "target_id": self.target_id,
            "target_config": copy.deepcopy(target),
            "target_adapter": copy.deepcopy(target),
            "repo_root": str(Path(repo).resolve()),
            "source_branch": identity["branch"],
            "source_commit": identity["commit"],
            "source_tree": identity["tree"],
            "controller_commit": identity["commit"],
            "version": identity["version"],
            "host_alias": target["host_alias"],
            "architecture": target["architecture"],
            "remote_run_dir": target["remote_root"] + "\\" + identity["commit"] + "\\" + run_id,
            "local_run_dir": str(Path(state_root).resolve() / "runs" / run_id),
            "input": self.inspect_input(repo, identity["commit"]),
            "input_basenames": names,
            "asset_provenance_sha256": _sha256_file(ASSET_PROVENANCE_PATH),
            "target_config_sha256": canonical_json_sha256(target),
            "cache_requirements": copy.deepcopy(requirements),
            "authorization_blocks": [
                "ssh-and-transfer",
                "offline-cache-and-filesystem",
                "candidate-build",
            ],
            "boundaries": {
                "installation": False,
                "interactive_acceptance": False,
                "production_license": False,
                "signing": False,
                "publication": False,
            },
        }

    def bind_online_plan(self, plan, online):
        if not isinstance(plan, dict) or not isinstance(online, dict):
            _pipeline_error("Windows online plan must be objects")
        if any(key in plan for key in self.online_plan_keys):
            _pipeline_error("Windows online identity already exists")
        required_keys = {"schema", "builder_status", "blockers"} | set(self.online_plan_keys)
        if set(online) != required_keys or online.get("builder_status") != "BUILDER_READY":
            _pipeline_error("Windows online result fields are incomplete", "ONLINE_DOCTOR_BLOCKED")
        if online.get("schema") != "taiji-package-online-doctor/v2" or online.get("blockers") != []:
            _pipeline_error("Windows online result is not ready", "ONLINE_DOCTOR_BLOCKED")
        requirements = _read_json(CACHE_REQUIREMENTS_PATH, "Windows cache requirements")
        requirements_sha = canonical_json_sha256(requirements)
        if online["cache_requirements_sha256"] != requirements_sha:
            _pipeline_error("Windows cache requirements identity drifted")
        observation = online["cache_observation"]
        if not isinstance(observation, dict):
            _pipeline_error("Windows cache observation is invalid", "ONLINE_DOCTOR_BLOCKED")
        observation_without_time = copy.deepcopy(observation)
        observed_at = observation_without_time.pop("observed_at", None)
        if not isinstance(observed_at, str) or canonical_json_sha256(observation_without_time) != online["cache_observation_sha256"]:
            _pipeline_error("Windows cache observation identity drifted")
        if observation.get("schema") != "taiji-windows-cache-observation/v1" or observation.get("target_id") != self.target_id:
            _pipeline_error("Windows cache observation schema is invalid")
        if observation.get("requirements_sha256") != requirements_sha:
            _pipeline_error("Windows cache observation requirements drifted")
        host_facts = online["host_facts"]
        if not isinstance(host_facts, dict) or set(host_facts) != {
            "schema", "host_alias", "os", "os_version", "architecture", "filesystem", "powershell_version"
        }:
            _pipeline_error("Windows host facts are invalid")
        if canonical_json_sha256(host_facts) != online["host_facts_sha256"]:
            _pipeline_error("Windows host facts identity drifted")
        if host_facts.get("schema") != "taiji-windows-host-facts/v1" or host_facts.get("architecture") != "AMD64" or host_facts.get("filesystem") != "NTFS":
            _pipeline_error("Windows host facts stable identity is invalid")
        finalized = copy.deepcopy(plan)
        finalized["cache_requirements_sha256"] = requirements_sha
        finalized["cache_observation"] = copy.deepcopy(observation)
        finalized["cache_observation_sha256"] = online["cache_observation_sha256"]
        finalized["host_facts"] = copy.deepcopy(host_facts)
        finalized["host_facts_sha256"] = online["host_facts_sha256"]
        return finalized

    def prepare_input(self, plan, command_runner):
        del plan, command_runner
        _pipeline_error("Windows input preparation requires the later real builder phase", "INPUT_PREPARATION_REQUIRED")

    def create_transport(self, repo, target, *, ssh_config, command_runner):
        if self.transport_factory is not None:
            parameters = inspect.signature(self.transport_factory).parameters
            if "repo" in parameters or len(parameters) >= 4:
                return self.transport_factory(repo, target, ssh_config, command_runner)
            return self.transport_factory(
                target,
                ssh_config=ssh_config,
                command_runner=command_runner,
            )
        del repo, target, ssh_config, command_runner
        _pipeline_error("real Windows transport is not enabled in fake phase", "BUILDER_UNREACHABLE")

    def validate_review(self, plan, review, remote_log):
        if self.review_validator is not None:
            return self.review_validator(plan, review, remote_log)
        return self._validate_windows_review(plan, Path(review), Path(remote_log))

    def _validate_windows_review(self, plan, review, remote_log):
        if not isinstance(plan, dict) or plan.get("target_id") != self.target_id:
            _review_error("Windows review plan identity is invalid")
        try:
            review_metadata = review.lstat()
        except OSError as exc:
            _review_error("review directory is unavailable: {}".format(exc))
        if stat.S_ISLNK(review_metadata.st_mode) or not stat.S_ISDIR(review_metadata.st_mode):
            _review_error("review path is not a directory")
        version = plan.get("version")
        if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
            _review_error("review plan version is invalid")
        artifact_basename = "TaijiAgent-Setup-{}-win-x64.exe".format(version)
        expected_review_names = {
            artifact_basename,
            artifact_basename + ".sha256",
            "taiji-package-manifest.json",
            "formal-build-tests.log",
            "构建报告.txt",
            ".build-success",
            "run-state.json",
        }
        try:
            actual_review_names = {entry.name for entry in review.iterdir()}
        except OSError as exc:
            _review_error("review directory cannot be listed: {}".format(exc))
        if actual_review_names != expected_review_names:
            _review_error("review exact file set is invalid")
        for name in expected_review_names:
            _regular_file(review / name, "review file {}".format(name))
        log_size, log_sha, log_bytes = _file_sha256_and_size(remote_log, "remote build log")
        if log_size <= 0:
            _review_error("remote build log is empty")
        if log_bytes.startswith(b"\xef\xbb\xbf"):
            _review_error("remote build log contains a BOM")
        try:
            log_bytes.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            _review_error("remote build log is not UTF-8: {}".format(exc))

        manifest_path = review / "taiji-package-manifest.json"
        manifest = _read_canonical_json(manifest_path, "package manifest")
        _require_exact(manifest, REVIEW_MANIFEST_KEYS, "package manifest")
        if manifest["schema"] != "taiji-package-manifest/v2":
            _review_error("package manifest schema is invalid")
        if manifest["run_id"] != plan.get("run_id") or manifest["target_id"] != self.target_id:
            _review_error("package manifest run identity is invalid")
        source = manifest["source"]
        _require_exact(source, {"branch", "commit", "tree"}, "package manifest source")
        if (
            source["branch"] != plan.get("source_branch")
            or source["branch"] != "main"
            or source["commit"] != plan.get("source_commit")
            or source["tree"] != plan.get("source_tree")
        ):
            _review_error("package manifest source identity drifted")
        _commit_value(source["commit"], "package manifest source commit")
        _commit_value(source["tree"], "package manifest source tree")
        for key in (
            "target_config_sha256",
            "asset_provenance_sha256",
            "cache_requirements_sha256",
            "cache_observation_sha256",
        ):
            _sha256_value(manifest[key], "package manifest {}".format(key))
            if manifest[key] != plan.get(key):
                _review_error("package manifest {} drifted".format(key))
        if manifest["target_config_sha256"] != canonical_json_sha256(plan["target_config"]):
            _review_error("package manifest target config SHA drifted")
        boundaries = manifest["boundaries"]
        _require_exact(
            boundaries,
            {"installation", "interactive_acceptance", "production_license", "signing", "publication"},
            "package manifest boundaries",
        )
        if any(type(value) is not bool or value for value in boundaries.values()):
            _review_error("package manifest boundaries are not all false")
        _utc_value(manifest["started_at"], "package manifest started_at")
        finished_at = _utc_value(manifest["finished_at"], "package manifest finished_at")
        if finished_at < _utc_value(manifest["started_at"], "package manifest started_at"):
            _review_error("package manifest finished_at precedes started_at")

        self._validate_review_input(manifest["input"], plan)
        self._validate_review_tools(manifest["tools"], plan)
        self._validate_review_payload(manifest["payload"], plan, artifact_basename, review)
        self._validate_review_formal_tests(manifest["formal_tests"], review)
        artifact, artifact_path = self._validate_review_artifact(
            manifest["artifact"], plan, review, artifact_basename
        )
        self._validate_review_remote_state(review / "run-state.json", plan)
        self._validate_review_marker(
            review / ".build-success",
            plan,
            artifact_path,
            manifest_path,
            review / "formal-build-tests.log",
            review / "构建报告.txt",
            review / "run-state.json",
        )
        del log_size, log_sha
        return {
            "kind": "exe",
            "basename": artifact["basename"],
            "bytes": artifact["bytes"],
            "sha256": artifact["sha256"],
            "path": str(artifact_path.resolve()),
            "relative_path": artifact["basename"],
        }

    def _validate_review_input(self, value, plan):
        _require_exact(value, set(REVIEW_INPUT_ROLES), "package manifest input")
        plan_input = plan.get("input")
        if not isinstance(plan_input, dict) or plan_input.get("status") != "REUSABLE":
            _review_error("review plan input is not reusable")
        files = plan_input.get("files")
        if not isinstance(files, dict) or set(files) != {"archive", "manifest", "checksum"}:
            _review_error("review plan input triplet is incomplete")
        role_map = {"archive": "archive", "manifest": "manifest", "sidecar": "checksum"}
        for review_role, plan_role in role_map.items():
            metadata = value[review_role]
            _require_exact(metadata, {"basename", "bytes", "sha256"}, "input {}".format(review_role))
            expected = files.get(plan_role)
            if not isinstance(expected, dict):
                _review_error("plan input {} is invalid".format(plan_role))
            _basename_value(metadata["basename"], "input {} basename".format(review_role))
            _positive_bytes(metadata["bytes"], "input {} bytes".format(review_role))
            _sha256_value(metadata["sha256"], "input {} SHA".format(review_role))
            for key in ("basename", "bytes", "sha256"):
                if metadata[key] != expected.get(key):
                    _review_error("input {} identity drifted".format(review_role))

    def _validate_review_tools(self, value, plan):
        _require_exact(value, set(REVIEW_TOOL_NAMES), "package manifest tools")
        target = plan.get("target_config")
        if not isinstance(target, dict):
            _review_error("review target config is missing")
        for name in REVIEW_TOOL_NAMES:
            tool = value[name]
            _require_exact(tool, {"bytes", "path", "sha256", "version"}, "tool {}".format(name))
            if not isinstance(tool["path"], str) or WINDOWS_PATH_RE.fullmatch(tool["path"]) is None:
                _review_error("tool {} path is not absolute Windows path".format(name))
            _positive_bytes(tool["bytes"], "tool {} bytes".format(name))
            _sha256_value(tool["sha256"], "tool {} SHA".format(name))
            if not isinstance(tool["version"], str) or not tool["version"]:
                _review_error("tool {} version is empty".format(name))
            if name != "safe_tar" and VERSION_RE.fullmatch(tool["version"]) is None:
                _review_error("tool {} version is invalid".format(name))
            if name == "safe_tar":
                if tool["version"] != "taiji-safe-tar/v1" or not tool["path"].startswith(plan.get("remote_run_dir", "") + "\\"):
                    _review_error("safe_tar evidence is not bound to the remote run")
            elif tool["path"] != target.get(name):
                _review_error("tool {} path drifted from target".format(name))

    def _validate_review_payload(self, value, plan, artifact_basename, review):
        _require_exact(value, REVIEW_PAYLOAD_KEYS, "package manifest payload")
        if value["schema"] != "taiji-windows-payload-manifest/v1":
            _review_error("payload schema is invalid")
        if value["source_commit"] != plan.get("source_commit") or value["source_tree"] != plan.get("source_tree"):
            _review_error("payload source identity drifted")
        _commit_value(value["source_commit"], "payload source commit")
        _commit_value(value["source_tree"], "payload source tree")
        entries = value["entries"]
        if not isinstance(entries, list) or not entries:
            _review_error("payload entries are empty")
        previous = None
        total_bytes = 0
        seen = set()
        for entry in entries:
            _require_exact(entry, {"bytes", "path", "sha256"}, "payload entry")
            path_value = entry["path"]
            if (
                not isinstance(path_value, str)
                or not path_value
                or unicodedata.normalize("NFC", path_value) != path_value
                or path_value.startswith("/")
                or "\\" in path_value
                or ":" in path_value
                or "\x00" in path_value
            ):
                _review_error("payload path is invalid")
            parts = path_value.split("/")
            if any(not part or part in (".", "..") or part.endswith((".", " ")) for part in parts):
                _review_error("payload path escapes its root")
            reserved = {"CON", "PRN", "AUX", "NUL", "CLOCK$"} | {
                "COM{}".format(index) for index in range(1, 10)
            } | {"LPT{}".format(index) for index in range(1, 10)}
            if any(part.split(".", 1)[0].upper() in reserved for part in parts):
                _review_error("payload path uses a reserved Windows name")
            if path_value in seen:
                _review_error("payload entries contain a duplicate path")
            seen.add(path_value)
            if previous is not None and path_value.encode("utf-8") <= previous:
                _review_error("payload entries are not UTF-8 path sorted")
            previous = path_value.encode("utf-8")
            _positive_bytes(entry["bytes"], "payload entry bytes")
            _sha256_value(entry["sha256"], "payload entry SHA")
            total_bytes += entry["bytes"]
            if path_value == artifact_basename:
                artifact_path = review / artifact_basename
                actual_size, actual_sha, _raw = _file_sha256_and_size(artifact_path, "payload artifact")
                if entry["bytes"] != actual_size or entry["sha256"] != actual_sha:
                    _review_error("payload artifact identity drifted")
        if value["file_count"] != len(entries) or value["total_bytes"] != total_bytes:
            _review_error("payload counts are inconsistent")
        payload_identity = copy.deepcopy(value)
        payload_identity.pop("manifest_sha256")
        if value["manifest_sha256"] != hashlib.sha256(_canonical_bytes(payload_identity)).hexdigest():
            _review_error("payload manifest SHA drifted")
        _sha256_value(value["manifest_sha256"], "payload manifest SHA")

    def _validate_review_formal_tests(self, value, review):
        _require_exact(value, {"checks", "log_basename", "log_bytes", "log_sha256", "status"}, "formal tests")
        if value["log_basename"] != "formal-build-tests.log" or value["status"] != "PASS":
            _review_error("formal test summary is invalid")
        checks = value["checks"]
        expected_checks = [
            {"id": identifier, "result": "PASS", "exit_code": 0}
            for identifier in REVIEW_FORMAL_CHECK_IDS
        ]
        if checks != expected_checks:
            _review_error("formal test checks are not the required seven PASS entries")
        log_path = review / value["log_basename"]
        size, sha, raw = _file_sha256_and_size(log_path, "formal build test log")
        if value["log_bytes"] != size or value["log_sha256"] != sha:
            _review_error("formal build test log identity drifted")
        if raw.startswith(b"\xef\xbb\xbf"):
            _review_error("formal build test log contains a BOM")
        expected_lines = [
            "01 source-session-identity PASS exit=0",
            "02 offline-npm-ci PASS exit=0",
            "03 electron-win32-x64 PASS exit=0",
            "04 payload-import-menu-policy PASS exit=0",
            "05 payload-hygiene-closure PASS exit=0",
            "06 inno-compile PASS exit=0",
            "07 installer-pe-version-authenticode PASS exit=0",
            "SUMMARY PASS checks=7",
        ]
        expected = ("\n".join(expected_lines) + "\n").encode("utf-8")
        if raw != expected:
            _review_error("formal build test log contents are invalid")

    def _validate_review_artifact(self, value, plan, review, artifact_basename):
        _require_exact(value, REVIEW_ARTIFACT_KEYS, "package manifest artifact")
        if value["kind"] != "exe" or value["basename"] != artifact_basename or value["version"] != plan.get("version"):
            _review_error("artifact identity is invalid")
        expected_file_version = plan["version"] + ".0"
        if value["file_version"] != expected_file_version or value["product_version"] != expected_file_version:
            _review_error("artifact version information is invalid")
        if value["pe_machine"] != "0x8664" or value["pe_optional_magic"] != "0x20b":
            _review_error("artifact PE identity is invalid")
        if value["authenticode_status"] != "NotSigned":
            _review_error("artifact Authenticode status is not NotSigned")
        artifact_path = review / artifact_basename
        size, sha, raw = _file_sha256_and_size(artifact_path, "Windows candidate EXE")
        if value["bytes"] != size or value["sha256"] != sha:
            _review_error("artifact bytes or SHA drifted")
        _sidecar_size, _sidecar_sha, sidecar_raw = _file_sha256_and_size(
            review / (artifact_basename + ".sha256"), "Windows candidate EXE sidecar"
        )
        expected_sidecar = ("{}  {}\n".format(sha, artifact_basename)).encode("utf-8")
        if sidecar_raw != expected_sidecar:
            _review_error("Windows candidate EXE sidecar does not match the artifact")
        if len(raw) < 0x9A or raw[:2] != b"MZ":
            _review_error("artifact is not an MZ PE file")
        try:
            pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
            signature = raw[pe_offset:pe_offset + 4]
            machine = struct.unpack_from("<H", raw, pe_offset + 4)[0]
            optional_magic = struct.unpack_from("<H", raw, pe_offset + 0x18)[0]
        except (IndexError, struct.error) as exc:
            _review_error("artifact PE headers are unreadable: {}".format(exc))
        if signature != b"PE\x00\x00" or machine != 0x8664 or optional_magic != 0x20B:
            _review_error("artifact PE headers are not AMD64 PE32+")
        actual_pe = {
            "pe_machine": "0x{:04x}".format(machine),
            "pe_optional_magic": "0x{:03x}".format(optional_magic),
        }
        inspector = self.artifact_inspector
        if inspector is None or not callable(getattr(inspector, "inspect", None)):
            _review_error("Windows artifact inspector is not configured")
        try:
            inspected = inspector.inspect(artifact_path)
        except PipelineError:
            raise
        except Exception as exc:
            _review_error("Windows artifact inspector failed: {}".format(exc))
        _require_exact(
            inspected,
            {"pe_machine", "pe_optional_magic", "file_version", "product_version", "authenticode_status"},
            "Windows artifact inspector result",
        )
        if inspected["pe_machine"] != actual_pe["pe_machine"] or inspected["pe_optional_magic"] != actual_pe["pe_optional_magic"]:
            _review_error("inspector PE identity differs from actual bytes")
        for key in ("pe_machine", "pe_optional_magic", "file_version", "product_version", "authenticode_status"):
            if inspected[key] != value[key]:
                _review_error("artifact {} differs from inspector".format(key))
        if not isinstance(value["file_version"], str) or VERSION_FULL_RE.fullmatch(value["file_version"]) is None:
            _review_error("artifact FileVersion is malformed")
        if not isinstance(value["product_version"], str) or VERSION_FULL_RE.fullmatch(value["product_version"]) is None:
            _review_error("artifact ProductVersion is malformed")
        return value, artifact_path

    def _validate_review_remote_state(self, path, plan):
        state = _read_canonical_json(path, "remote run state")
        _require_exact(state, REVIEW_REMOTE_STATE_KEYS, "remote run state")
        if (
            state["schema"] != "taiji-package-remote-run/v1"
            or state["run_id"] != plan.get("run_id")
            or state["target_id"] != self.target_id
            or state["source_commit"] != plan.get("source_commit")
            or state["host_facts_sha256"] != plan.get("host_facts_sha256")
            or state["terminal_status"] != "REMOTE_BUILD_SUCCEEDED"
        ):
            _review_error("remote run state identity is invalid")
        _commit_value(state["source_commit"], "remote run state source commit")
        _sha256_value(state["host_facts_sha256"], "remote run state host facts SHA")
        started = _utc_value(state["started_at"], "remote run state started_at")
        finished = _utc_value(state["finished_at"], "remote run state finished_at")
        if finished < started:
            _review_error("remote run state finished_at precedes started_at")
        history = state["stage_history"]
        if not isinstance(history, list) or not history:
            _review_error("remote run stage history is empty")
        previous_finished = None
        for item in history:
            _require_exact(item, {"stage", "started_at", "finished_at", "result"}, "remote stage history")
            if not isinstance(item["stage"], str) or not item["stage"] or item["result"] != "PASS":
                _review_error("remote stage history item is invalid")
            item_started = _utc_value(item["started_at"], "remote stage started_at")
            item_finished = _utc_value(item["finished_at"], "remote stage finished_at")
            if item_finished < item_started or (previous_finished is not None and item_started < previous_finished):
                _review_error("remote stage history is not ordered")
            previous_finished = item_finished
        if history[-1]["stage"] != "review-ready" or history[-1]["result"] != "PASS":
            _review_error("remote stage history does not end at review-ready")

    def _validate_review_marker(self, path, plan, artifact_path, manifest_path, formal_path, report_path, state_path):
        marker = _read_canonical_json(path, "build success marker")
        _require_exact(marker, REVIEW_MARKER_KEYS, "build success marker")
        if (
            marker["schema"] != "taiji-package-build-success/v1"
            or marker["run_id"] != plan.get("run_id")
            or marker["target_id"] != self.target_id
            or marker["source_commit"] != plan.get("source_commit")
        ):
            _review_error("build success marker identity is invalid")
        _commit_value(marker["source_commit"], "build success marker source commit")
        checks = (
            ("artifact", artifact_path),
            ("package_manifest", manifest_path),
            ("formal_build_tests_log", formal_path),
            ("report", report_path),
            ("remote_state", state_path),
        )
        for prefix, file_path in checks:
            size, sha, _raw = _file_sha256_and_size(file_path, "marker {} file".format(prefix))
            if marker["{}_basename".format(prefix)] != Path(file_path).name:
                _review_error("marker {} basename drifted".format(prefix))
            if marker["{}_bytes".format(prefix)] != size or marker["{}_sha256".format(prefix)] != sha:
                _review_error("marker {} identity drifted".format(prefix))
            _sha256_value(marker["{}_sha256".format(prefix)], "marker {} SHA".format(prefix))

    def initial_state_patch(self, plan, online):
        del online
        values = {}
        for key in (
            "asset_provenance_sha256",
            "cache_requirements_sha256",
            "cache_observation_sha256",
        ):
            value = plan.get(key)
            if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                _pipeline_error("Windows plan identity is incomplete")
            values[key] = value
        return {"identity": values, "policy": None}

    def success_state_patch(self, artifact):
        return {"exe": copy.deepcopy(artifact)}

    def normalize_legacy_state(self, state):
        del state
        _pipeline_error("Windows adapter does not normalize Kylin v1 state", "PLAN_INVALID")
