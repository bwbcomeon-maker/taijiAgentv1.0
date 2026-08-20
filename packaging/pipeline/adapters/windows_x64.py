"""Windows x64 candidate adapter with a fake-phase-only transport boundary."""

import copy
import hashlib
import json
import re
import subprocess
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

    def __init__(self, transport_factory=None, review_validator=None, controller_runner=None):
        self.transport_factory = transport_factory
        self.review_validator = review_validator
        self.controller_runner = controller_runner

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
        target = self.validate_target(target)
        identity = self._controller_identity(repo)
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
            "input": {"status": "MISSING", "files": {}},
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
            return self.transport_factory(repo, target, ssh_config, command_runner)
        del repo, target, ssh_config, command_runner
        _pipeline_error("real Windows transport is not enabled in fake phase", "BUILDER_UNREACHABLE")

    def validate_review(self, plan, review, remote_log):
        if self.review_validator is not None:
            return self.review_validator(plan, review, remote_log)
        del plan, review, remote_log
        _pipeline_error("Windows review validator is not enabled in Task 2", "LOCAL_REVIEW_INVALID")

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
