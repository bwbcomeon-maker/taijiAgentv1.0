"""Kylin amd64 adapter boundary and Kylin-specific execution seams."""

import copy
import posixpath
import re
from pathlib import Path

from ..core.errors import PipelineError
from ..core.models import canonical_json_sha256
from .base import CandidateAdapter


_LEGACY_NAMESPACE = None
_LEGACY_REAL_TRANSPORT = None
_LEGACY_FAKE_TRANSPORT = None


def bind_legacy_namespace(namespace, real_transport, fake_transport):
    """Bind the still-compatible facade implementation during extraction.

    The binding is deliberately private to the facade bootstrap path.  The
    adapter owns the public transport types and hook dispatch; the binding
    only keeps the Task 4 legacy CLI executable until the common orchestrator
    replaces it in the next plan step.
    """

    global _LEGACY_NAMESPACE, _LEGACY_REAL_TRANSPORT, _LEGACY_FAKE_TRANSPORT
    _LEGACY_NAMESPACE = namespace
    _LEGACY_REAL_TRANSPORT = real_transport
    _LEGACY_FAKE_TRANSPORT = fake_transport


def _legacy(name):
    if _LEGACY_NAMESPACE is None or name not in _LEGACY_NAMESPACE:
        raise PipelineError("Kylin legacy implementation is not bound", category="PLAN_INVALID")
    return _LEGACY_NAMESPACE[name]


class RealSshTransport:
    """Compatibility type whose implementation is supplied by the facade."""

    def __new__(cls, *args, **kwargs):
        if _LEGACY_REAL_TRANSPORT is None:
            raise PipelineError("Kylin transport implementation is not bound", category="PLAN_INVALID")
        return _LEGACY_REAL_TRANSPORT(*args, **kwargs)


class FakeSshTransport:
    """Compatibility type whose deterministic implementation is facade-bound."""

    def __new__(cls, *args, **kwargs):
        if _LEGACY_FAKE_TRANSPORT is None:
            raise PipelineError("Kylin fake transport implementation is not bound", category="PLAN_INVALID")
        return _LEGACY_FAKE_TRANSPORT(*args, **kwargs)


class KylinAmd64Adapter(CandidateAdapter):
    target_id = "kylin-amd64"
    artifact_kind = "deb"
    success_label = "候选 DEB 已构建"
    pending_label = "候选 DEB 取回待恢复"
    not_built_label = "候选 DEB 未构建"
    online_plan_keys = ()

    def __init__(self, transport_factory=None, review_validator=None):
        self.transport_factory = transport_factory
        self.review_validator = review_validator

    def validate_target(self, payload):
        if not isinstance(payload, dict):
            raise PipelineError("target must be an object", category="TARGET_INVALID")
        if any(key in payload for key in ("python_class", "module", "adapter", "factory")):
            raise PipelineError("target cannot specify executable code", category="TARGET_INVALID")
        required = {
            "architecture", "host_alias", "minimum_free_gib", "minimum_free_inodes",
            "remote_account_home", "remote_root", "remote_user", "schema", "target_id",
        }
        if set(payload) != required:
            raise PipelineError("Kylin target fields are invalid", category="TARGET_INVALID")
        if payload.get("schema") != "taiji-package-target/v1" or payload.get("target_id") != self.target_id:
            raise PipelineError("Kylin target identity is invalid", category="TARGET_INVALID")
        if payload.get("architecture") != "amd64":
            raise PipelineError("Kylin target architecture is invalid", category="TARGET_INVALID")
        if not isinstance(payload.get("host_alias"), str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", payload["host_alias"]) is None:
            raise PipelineError("Kylin target host alias is invalid", category="TARGET_INVALID")
        if not isinstance(payload.get("remote_user"), str) or re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", payload["remote_user"]) is None:
            raise PipelineError("Kylin target remote user is invalid", category="TARGET_INVALID")
        for key in ("minimum_free_gib", "minimum_free_inodes"):
            if type(payload.get(key)) is not int or payload[key] <= 0:
                raise PipelineError("Kylin target {} is invalid".format(key), category="TARGET_INVALID")
        for key in ("remote_account_home", "remote_root"):
            value = payload.get(key)
            if not isinstance(value, str) or re.fullmatch(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+", value) is None or posixpath.normpath(value) != value:
                raise PipelineError("Kylin target {} is invalid".format(key), category="TARGET_INVALID")
        if payload["remote_root"] == payload["remote_account_home"] or posixpath.commonpath([payload["remote_account_home"], payload["remote_root"]]) != payload["remote_account_home"]:
            raise PipelineError("Kylin target remote root is invalid", category="TARGET_INVALID")
        return copy.deepcopy(payload)

    def bind_online_plan(self, plan, online):
        if not isinstance(plan, dict) or not isinstance(online, dict) or online.get("builder_status") != "BUILDER_READY":
            raise PipelineError("Kylin online plan is not ready", category="ONLINE_DOCTOR_BLOCKED")
        return copy.deepcopy(plan)

    def local_doctor(self, repo, target, state_root, *, ssh_config):
        return _legacy("_legacy_local_doctor")(
            repo, target, state_root, ssh_config=ssh_config
        )

    def inspect_input(self, repo, source_commit):
        return _legacy("_legacy_inspect_builder_input")(repo, source_commit)

    def build_plan(self, repo, target, state_root, *, run_id, ssh_config):
        return _legacy("_legacy_build_candidate_plan")(
            repo, target, state_root, run_id=run_id, ssh_config=ssh_config
        )

    def prepare_input(self, plan, command_runner):
        return _legacy("_legacy_run_builder_input_preparer")(
            plan, command_runner
        )

    def create_transport(self, repo, target, *, ssh_config, command_runner):
        factory = self.transport_factory
        if factory is None:
            return RealSshTransport(
                repo, target, ssh_config=ssh_config, command_runner=command_runner
            )
        return factory(repo, target, ssh_config, command_runner)

    def validate_review(self, plan, review, remote_log):
        validator = self.review_validator
        if validator is None:
            validator = _legacy("_legacy_validate_candidate_review")
        return validator(plan, review, remote_log)

    def initial_state_patch(self, plan, online):
        del online
        policy_sha = plan.get("canonical_policy_sha256")
        policy = None
        if policy_sha is not None:
            policy = {"kind": "canonical-compatibility-policy", "sha256": policy_sha}
        return {"identity": {}, "policy": policy}

    def success_state_patch(self, artifact):
        return {"deb": copy.deepcopy(artifact)}

    def normalize_legacy_state(self, state):
        normalized = copy.deepcopy(state)
        if normalized.get("schema") != "taiji-package-run-state/v1":
            return normalized
        plan = normalized.get("plan")
        if not isinstance(plan, dict):
            raise PipelineError("legacy state lacks its candidate plan", category="PLAN_INVALID")
        target = plan.get("target_adapter")
        source_commit = normalized.get("source_commit") or plan.get("source_commit")
        policy_sha256 = normalized.get("canonical_policy_sha256") or plan.get("canonical_policy_sha256")
        if not isinstance(target, dict) or not isinstance(source_commit, str) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
            raise PipelineError("legacy state target or source identity is incomplete", category="PLAN_INVALID")
        if not isinstance(policy_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", policy_sha256) is None:
            raise PipelineError("legacy state policy identity is incomplete", category="PLAN_INVALID")
        artifact = normalized.get("artifact")
        if artifact is None:
            deb = normalized.get("deb")
            if isinstance(deb, dict):
                artifact = copy.deepcopy(deb)
                artifact["kind"] = "deb"
        if artifact is not None and isinstance(artifact, dict):
            artifact.setdefault("kind", "deb")
        normalized["target_id"] = self.target_id
        normalized["target_config"] = copy.deepcopy(target)
        normalized["target_config_sha256"] = canonical_json_sha256(target)
        normalized["source"] = {
            "repo_root": plan.get("repo_root"),
            "branch": plan.get("source_branch", "main"),
            "commit": source_commit,
            "tree": plan.get("source_tree"),
        }
        normalized["policy"] = {"kind": "canonical-compatibility-policy", "sha256": policy_sha256}
        normalized["artifact"] = artifact
        return normalized
