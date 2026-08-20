import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
import subprocess


def canonical_json_sha256_for_fixture(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def complete_target():
    return {
        "schema": "taiji-package-target/v1",
        "target_id": "kylin-amd64",
        "host_alias": "kylin",
        "architecture": "amd64",
    }


def complete_input_files(root, source_commit="a" * 40):
    repo = Path(root).resolve() / "repo"
    archive = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    names = {
        "archive": archive,
        "manifest": "taijiagent-制包机输入-{}.manifest.json".format(source_commit),
        "checksum": archive + ".sha256",
    }
    hashes = {"archive": "1" * 64, "manifest": "2" * 64, "checksum": "3" * 64}
    sizes = {"archive": 101, "manifest": 202, "checksum": 303}
    return {
        role: {
            "path": str(repo / basename),
            "basename": basename,
            "bytes": sizes[role],
            "sha256": hashes[role],
            "exists": True,
        }
        for role, basename in names.items()
    }


def complete_plan(root, run_id="run-1", input_status="REUSABLE"):
    root = Path(root).resolve()
    target = complete_target()
    input_files = complete_input_files(root) if input_status == "REUSABLE" else {}
    return {
        "schema": "taiji-package-candidate-plan/v1",
        "run_id": run_id,
        "target_id": "kylin-amd64",
        "target_config": target,
        "target_adapter": target,
        "repo_root": str(root / "repo"),
        "source_branch": "main",
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "controller_commit": "c" * 40,
        "host_alias": "kylin",
        "architecture": "amd64",
        "remote_run_dir": "/home/kylin/taiji-builds/" + run_id,
        "local_run_dir": str(root / "state/runs" / run_id),
        "input": {"status": input_status, "files": input_files},
        "commands": [],
        "authorization_blocks": [],
        "boundaries": {},
    }


def complete_online(builder_status="BUILDER_READY"):
    return {
        "schema": "taiji-package-online-doctor/v1",
        "builder_status": builder_status,
        "host_facts_sha256": "d" * 64,
        "blockers": [],
    }


def complete_v2_payload(root, run_id="run-1", input_status="REUSABLE", **overrides):
    root = Path(root).resolve()
    target = complete_target()
    plan = complete_plan(root, run_id=run_id, input_status=input_status)
    manifest_sha256 = (
        plan["input"]["files"]["manifest"]["sha256"]
        if input_status == "REUSABLE" else None
    )
    payload = {
        "schema": "taiji-package-run-state/v2",
        "run_id": run_id,
        "target_id": "kylin-amd64",
        "target_config": target,
        "target_config_sha256": canonical_json_sha256_for_fixture(target),
        "source": {
            "repo_root": str(root / "repo"), "branch": "main",
            "commit": "a" * 40, "tree": "b" * 40,
        },
        "identity": {
            "controller_commit": "c" * 40,
            "asset_provenance_sha256": None,
            "input_manifest_sha256": manifest_sha256,
            "cache_requirements_sha256": None,
            "cache_observation_sha256": None,
            "host_facts_sha256": "d" * 64,
        },
        "stage": "PLANNED",
        "status_label": "候选 DEB 未构建",
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
        "started_at": "2026-08-20T12:00:00Z",
        "finished_at": None,
        "host": {
            "alias": "kylin", "architecture": "amd64",
            "remote_run_dir": "/home/kylin/taiji-builds/" + run_id,
        },
        "paths": {"local_run_dir": str(root / "state/runs" / run_id)},
        "input": deepcopy(plan["input"]),
        "policy": {
            "kind": "canonical-compatibility-policy", "sha256": "e" * 64,
        },
        "remote_build_succeeded": False,
        "fetch_allowed": False,
        "artifact": None,
        "failure": None,
        "stage_history": [],
        "lock": {"status": "released"},
        "logs": {
            "controller": str(root / "state/runs" / run_id / "controller.log"),
            "remote_build": str(root / "state/runs" / run_id / "remote-build.log"),
        },
        "plan": plan,
    }
    payload.update(deepcopy(overrides))
    return payload


def complete_v1_fetch_pending(root, run_id="legacy-run"):
    root = Path(root).resolve()
    target = complete_target()
    return {
        "schema": "taiji-package-run-state/v1",
        "run_id": run_id,
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
        "stage": "FETCH_PENDING",
        "status_label": "候选 DEB 取回待恢复",
        "source_commit": "a" * 40,
        "canonical_policy_sha256": "e" * 64,
        "remote_build_succeeded": True,
        "fetch_allowed": True,
        "failure": {"category": "SCP_INTERRUPTED", "detail": "fixture"},
        "plan": {
            "run_id": run_id,
            "target_adapter": target,
            "repo_root": str(root / "repo"),
            "source_commit": "a" * 40,
            "canonical_policy_sha256": "e" * 64,
            "remote_run_dir": "/home/kylin/taiji-builds/" + run_id,
            "local_run_dir": str(root / "state/runs" / run_id),
            "input": {"status": "REUSABLE", "files": {}},
        },
    }


def write_secure_v1_state(state_root, run_id, payload):
    state_root = Path(state_root)
    state_root.mkdir(parents=True, mode=0o700)
    state_root.chmod(0o700)
    runs = state_root / "runs"
    runs.mkdir(mode=0o700, exist_ok=True)
    runs.chmod(0o700)
    run_dir = runs / run_id
    run_dir.mkdir(mode=0o700)
    run_dir.chmod(0o700)
    path = run_dir / "run-state.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


class ForbiddenExternalRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        normalized = [str(item) for item in argv]
        self.calls.append(normalized)
        if normalized and normalized[0] in ("/usr/bin/ssh", "/usr/bin/scp", "ssh", "scp"):
            raise AssertionError("external transport is forbidden in unit tests")
        return subprocess.CompletedProcess(normalized, 0, "", "")


class RecordingTransport:
    def __init__(self, events, builder_status="BUILDER_READY"):
        self.events = events
        self.builder_status = builder_status

    def online_doctor(self):
        self.events.append("online_doctor")
        return complete_online(self.builder_status)

    def create_remote_run(self, plan):
        del plan
        self.events.append("create_remote_run")

    def transfer_input(self, plan):
        del plan
        self.events.append("transfer_input")

    def verify_remote_input(self, plan):
        del plan
        self.events.append("verify_remote_input")

    def build_remote_candidate(self, plan):
        del plan
        self.events.append("build_remote_candidate")

    def fetch(self, plan, staging_dir):
        del plan
        staging_dir = Path(staging_dir)
        review = staging_dir / "review"
        review.mkdir(parents=True, mode=0o700)
        artifact = review / "candidate.bin"
        artifact.write_bytes(b"candidate")
        artifact.chmod(0o600)
        self.events.append("fetch-review")
        remote_log = staging_dir / "remote-build.log"
        remote_log.write_text("fake remote build\n", encoding="utf-8")
        remote_log.chmod(0o600)
        self.events.append("fetch-log")
        return {"review_path": str(review), "remote_log_path": str(remote_log)}


class RecordingAdapter:
    target_id = "kylin-amd64"
    artifact_kind = "bin"
    success_label = "candidate built"
    pending_label = "candidate fetch pending"
    not_built_label = "candidate not built"
    online_plan_keys = ()

    def __init__(self, root, events, input_status="REUSABLE", builder_status="BUILDER_READY"):
        self.root = Path(root)
        self.events = events
        self.input_status = input_status
        self.transport = RecordingTransport(events, builder_status)
        self.transport_repo = None

    def validate_target(self, payload):
        self.events.append("validate_target")
        if payload.get("target_id") != self.target_id:
            raise AssertionError("fixture target id mismatch")
        return deepcopy(payload)

    def local_doctor(self, repo, target, state_root, *, ssh_config):
        del repo, target, state_root, ssh_config
        self.events.append("local_doctor")
        return {
            "controller_status": "CONTROLLER_READY",
            "builder_status": "BUILDER_UNREACHABLE",
            "blockers": [],
        }

    def inspect_input(self, repo, source_commit):
        del repo
        self.events.append("inspect_input")
        files = (
            complete_input_files(self.root, source_commit)
            if self.input_status == "REUSABLE" else {}
        )
        return {"status": self.input_status, "files": files}

    def build_plan(self, repo, target, state_root, *, run_id, ssh_config):
        del ssh_config
        self.events.append("build_plan")
        plan = complete_plan(
            self.root, run_id=run_id or "run-1", input_status=self.input_status
        )
        plan["repo_root"] = str(Path(repo).resolve())
        plan["target_config"] = deepcopy(target)
        plan["target_adapter"] = deepcopy(target)
        plan["local_run_dir"] = str(
            Path(state_root).resolve() / "runs" / plan["run_id"]
        )
        return plan

    def bind_online_plan(self, plan, online):
        del online
        self.events.append("bind_online_plan")
        return deepcopy(plan)

    def prepare_input(self, plan, command_runner):
        del plan, command_runner
        self.events.append("prepare_input")
        self.input_status = "REUSABLE"

    def create_transport(self, repo, target, *, ssh_config, command_runner):
        del target, ssh_config, command_runner
        self.events.append("create_transport")
        self.transport_repo = str(Path(repo).resolve())
        return self.transport

    def validate_review(self, plan, review, remote_log):
        del plan, remote_log
        self.events.append("validate_review")
        artifact = Path(review) / "candidate.bin"
        return {
            "kind": "bin",
            "basename": artifact.name,
            "bytes": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "path": str(artifact),
            "relative_path": "candidate.bin",
        }

    def initial_state_patch(self, plan, online):
        del plan, online
        self.events.append("initial_state_patch")
        return {"policy": None, "identity": {}}

    def success_state_patch(self, artifact):
        del artifact
        self.events.append("success_state_patch")
        return {}

    def normalize_legacy_state(self, state):
        self.events.append("normalize_legacy_state")
        normalized = deepcopy(state)
        normalized["target_id"] = "kylin-amd64"
        normalized["target_config"] = deepcopy(state["plan"]["target_adapter"])
        normalized["target_config_sha256"] = canonical_json_sha256_for_fixture(
            normalized["target_config"]
        )
        return normalized


class RecordingPublisher:
    def __init__(self, events):
        self.events = events

    def __call__(self, store, run_id, fetched, artifact):
        self.events.append("publish")
        published = deepcopy(artifact)
        review = store.run_dir(run_id) / "review"
        if review.exists():
            raise AssertionError("recording publisher would overwrite output")
        review.mkdir(mode=0o700)
        destination = review / artifact["basename"]
        shutil.copy2(artifact["path"], str(destination))
        destination.chmod(0o600)
        published["path"] = str(destination)
        return published
