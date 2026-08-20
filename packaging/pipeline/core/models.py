"""Platform-neutral run-state models and canonical identity helpers."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re


CURRENT_STATE_SCHEMA = "taiji-package-run-state/v2"
LEGACY_STATE_SCHEMA = "taiji-package-run-state/v1"
SUPPORTED_STATE_SCHEMAS = (LEGACY_STATE_SCHEMA, CURRENT_STATE_SCHEMA)

V2_REQUIRED_TOP_LEVEL = {
    "schema", "run_id", "target_id", "target_config", "target_config_sha256",
    "source", "identity", "stage", "status_label", "created_at", "updated_at",
    "started_at", "finished_at", "host", "paths", "input", "policy",
    "remote_build_succeeded", "fetch_allowed", "artifact", "failure",
    "stage_history", "lock", "logs", "plan",
}

IDENTITY_KEYS = {
    "controller_commit", "asset_provenance_sha256", "input_manifest_sha256",
    "cache_requirements_sha256", "cache_observation_sha256", "host_facts_sha256",
}
NULLABLE_IDENTITY_KEYS = IDENTITY_KEYS - {"controller_commit"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_sha256(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value):
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _is_commit(value):
    return isinstance(value, str) and COMMIT_RE.fullmatch(value) is not None


def _require(condition, message):
    if not condition:
        from .errors import PipelineError

        raise PipelineError(message, category="PLAN_INVALID")


def validate_v2_state(state):
    """Validate a complete v2 state without applying platform-specific rules."""

    _require(isinstance(state, dict), "run state must be an object")
    _require(set(state) == V2_REQUIRED_TOP_LEVEL, "run state v2 fields are incomplete")
    _require(state["schema"] == CURRENT_STATE_SCHEMA, "unsupported run state schema")
    _require(isinstance(state["run_id"], str) and state["run_id"], "run id is invalid")
    _require(isinstance(state["target_id"], str) and state["target_id"], "target id is invalid")
    _require(isinstance(state["target_config"], dict), "target config must be an object")
    _require(_is_sha256(state["target_config_sha256"]), "target config SHA is invalid")
    _require(
        state["target_config_sha256"] == canonical_json_sha256(state["target_config"]),
        "target config SHA does not match canonical target",
    )

    source = state["source"]
    _require(isinstance(source, dict), "source must be an object")
    _require(
        set(source) == {"repo_root", "branch", "commit", "tree"},
        "source fields are incomplete",
    )
    _require(isinstance(source["repo_root"], str) and source["repo_root"], "source repo root is invalid")
    _require(isinstance(source["branch"], str) and source["branch"], "source branch is invalid")
    _require(_is_commit(source["commit"]), "source commit is invalid")
    _require(_is_commit(source["tree"]), "source tree is invalid")

    identity = state["identity"]
    _require(isinstance(identity, dict) and set(identity) == IDENTITY_KEYS, "identity fields are incomplete")
    _require(_is_commit(identity["controller_commit"]), "controller commit is invalid")
    for key in NULLABLE_IDENTITY_KEYS:
        value = identity[key]
        _require(value is None or _is_sha256(value), "identity {} is invalid".format(key))

    for key in ("stage", "status_label", "created_at", "updated_at", "started_at"):
        _require(isinstance(state[key], str) and state[key], "state {} is invalid".format(key))
    _require(state["finished_at"] is None or isinstance(state["finished_at"], str), "finished_at is invalid")

    host = state["host"]
    _require(isinstance(host, dict), "host must be an object")
    _require(set(host) == {"alias", "architecture", "remote_run_dir"}, "host fields are incomplete")
    for key in ("alias", "architecture", "remote_run_dir"):
        _require(isinstance(host[key], str) and host[key], "host {} is invalid".format(key))

    paths = state["paths"]
    _require(isinstance(paths, dict) and set(paths) == {"local_run_dir"}, "paths are invalid")
    _require(isinstance(paths["local_run_dir"], str) and paths["local_run_dir"], "local run dir is invalid")

    input_state = state["input"]
    _require(isinstance(input_state, dict), "input must be an object")
    _require(input_state.get("status") in ("MISSING", "REUSABLE"), "input status is invalid")
    _require(isinstance(input_state.get("files"), dict), "input files must be an object")
    _require(isinstance(state["plan"], dict), "execution plan must be an object")
    _require(state["plan"].get("input") == input_state, "state and execution plan input differ")
    if input_state["status"] == "REUSABLE":
        _require(set(input_state["files"]) == {"archive", "manifest", "checksum"}, "input triplet is incomplete")
        for role, metadata in input_state["files"].items():
            _require(isinstance(metadata, dict), "input {} metadata is invalid".format(role))
            _require(isinstance(metadata.get("basename"), str) and metadata["basename"], "input basename is invalid")
            _require(type(metadata.get("bytes")) is int and metadata["bytes"] >= 0, "input bytes are invalid")
            _require(_is_sha256(metadata.get("sha256")), "input SHA is invalid")
        _require(_is_sha256(identity["input_manifest_sha256"]), "input manifest identity is missing")
        _require(
            identity["input_manifest_sha256"] == input_state["files"]["manifest"]["sha256"],
            "input manifest identity does not match input",
        )
    else:
        _require(input_state["files"] == {}, "missing input cannot contain files")
        _require(identity["input_manifest_sha256"] is None, "missing input has an identity")

    policy = state["policy"]
    _require(policy is None or isinstance(policy, dict), "policy is invalid")
    _require(type(state["remote_build_succeeded"]) is bool, "remote build flag is invalid")
    _require(type(state["fetch_allowed"]) is bool, "fetch flag is invalid")
    _require(state["artifact"] is None or isinstance(state["artifact"], dict), "artifact is invalid")
    _require(state["failure"] is None or isinstance(state["failure"], dict), "failure is invalid")
    _require(isinstance(state["stage_history"], list), "stage history is invalid")
    _require(isinstance(state["lock"], dict), "lock is invalid")
    logs = state["logs"]
    _require(isinstance(logs, dict) and set(logs) == {"controller", "remote_build"}, "logs are invalid")
    _require(all(isinstance(value, str) and value for value in logs.values()), "log paths are invalid")
    return state


def new_run_state(plan, online, adapter):
    """Create the complete, platform-neutral v2 state from a finalized plan."""

    from .errors import PipelineError

    _require(isinstance(plan, dict), "candidate plan must be an object")
    _require(isinstance(online, dict), "online doctor result must be an object")
    patch = adapter.initial_state_patch(plan, online)
    _require(isinstance(patch, dict), "initial state patch must be an object")
    _require(set(patch).issubset({"identity", "policy"}), "initial state patch contains unknown fields")
    patch_identity = patch.get("identity", {})
    _require(isinstance(patch_identity, dict), "initial identity patch must be an object")
    _require(set(patch_identity).issubset(IDENTITY_KEYS), "initial identity patch contains unknown keys")

    target_config = deepcopy(plan.get("target_config"))
    _require(isinstance(target_config, dict), "plan target config is missing")
    identity = {
        "controller_commit": plan.get("controller_commit"),
        "asset_provenance_sha256": None,
        "input_manifest_sha256": None,
        "cache_requirements_sha256": None,
        "cache_observation_sha256": None,
        "host_facts_sha256": online.get("host_facts_sha256"),
    }
    _require(_is_commit(identity["controller_commit"]), "plan controller commit is invalid")
    _require(identity["host_facts_sha256"] is None or _is_sha256(identity["host_facts_sha256"]), "host facts SHA is invalid")
    for key, value in patch_identity.items():
        if key == "controller_commit":
            _require(value == identity["controller_commit"], "initial patch cannot replace controller commit")
        else:
            _require(value is None or _is_sha256(value), "initial identity {} is invalid".format(key))
        identity[key] = deepcopy(value)

    input_state = deepcopy(plan.get("input"))
    _require(isinstance(input_state, dict), "plan input is missing")
    if input_state.get("status") == "REUSABLE":
        identity["input_manifest_sha256"] = input_state.get("files", {}).get("manifest", {}).get("sha256")
    elif input_state.get("status") != "MISSING":
        _require(False, "plan input status is invalid")
    timestamp = utc_now()
    local_run_dir = plan.get("local_run_dir")
    _require(isinstance(local_run_dir, str) and local_run_dir, "plan local run dir is invalid")
    state = {
        "schema": CURRENT_STATE_SCHEMA,
        "run_id": plan.get("run_id"),
        "target_id": plan.get("target_id"),
        "target_config": target_config,
        "target_config_sha256": canonical_json_sha256(target_config),
        "source": {
            "repo_root": plan.get("repo_root"),
            "branch": plan.get("source_branch"),
            "commit": plan.get("source_commit"),
            "tree": plan.get("source_tree"),
        },
        "identity": identity,
        "stage": "PLANNED",
        "status_label": adapter.not_built_label,
        "created_at": timestamp,
        "updated_at": timestamp,
        "started_at": timestamp,
        "finished_at": None,
        "host": {
            "alias": plan.get("host_alias"),
            "architecture": plan.get("architecture"),
            "remote_run_dir": plan.get("remote_run_dir"),
        },
        "paths": {"local_run_dir": local_run_dir},
        "input": deepcopy(input_state),
        "policy": deepcopy(patch.get("policy")),
        "remote_build_succeeded": False,
        "fetch_allowed": False,
        "artifact": None,
        "failure": None,
        "stage_history": [],
        "lock": {"status": "released"},
        "logs": {
            "controller": str(Path(local_run_dir) / "controller.log"),
            "remote_build": str(Path(local_run_dir) / "remote-build.log"),
        },
        "plan": deepcopy(plan),
    }
    try:
        validate_v2_state(state)
    except PipelineError:
        raise
    return state
