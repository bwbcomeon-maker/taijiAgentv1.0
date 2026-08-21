"""Shared deterministic fixtures for the Windows fake candidate pipeline."""

import copy
import hashlib
import json
import os
import struct
from pathlib import Path
import subprocess
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_regular(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return path


def make_minimal_inno_setup_pe(version="1.0.4.0"):
    del version
    data = bytearray(512)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", data, 0x84, 0x014C)
    struct.pack_into("<H", data, 0x98, 0x10B)
    return bytes(data)


def _canonical_sha(value):
    return sha256_bytes(canonical_json_bytes(value))


def _requirements():
    return json.loads(
        (ROOT / "packaging/windows/cache-requirements.json").read_text(encoding="utf-8")
    )


def _host_facts():
    return {
        "schema": "taiji-windows-host-facts/v1",
        "host_alias": "windows-direct",
        "os": "Windows",
        "os_version": "10.0",
        "architecture": "AMD64",
        "filesystem": "NTFS",
        "powershell_version": "5.1",
    }


def _cache_observation(requirements_sha):
    return {
        "schema": "taiji-windows-cache-observation/v1",
        "target_id": "windows-x64",
        "requirements_sha256": requirements_sha,
        "cache_root": r"D:\tw\cache",
        "entries": [
            {
                "id": "npm-cache",
                "type": "directory",
                "relative_path": "npm",
                "bytes": 11,
                "sha256": "1" * 64,
                "members": [
                    {"path": "_cacache", "bytes": 11, "sha256": "2" * 64}
                ],
            },
            {
                "id": "electron-39.8.10-win32-x64",
                "type": "regular-file",
                "relative_path": "electron/electron-v39.8.10-win32-x64.zip",
                "bytes": 22,
                "sha256": "3" * 64,
                "members": [
                    {"path": "electron.exe", "bytes": 33, "sha256": "4" * 64}
                ],
            },
            {
                "id": "private-python-runtime",
                "type": "directory",
                "relative_path": "python-runtime",
                "bytes": 44,
                "sha256": "5" * 64,
                "members": [
                    {"path": "python.exe", "bytes": 55, "sha256": "6" * 64},
                    {"path": "python311._pth", "bytes": 66, "sha256": "7" * 64},
                ],
            },
        ],
        "observed_at": "2026-08-20T12:00:00Z",
    }


def _complete_online():
    requirements_sha = _canonical_sha(_requirements())
    observation = _cache_observation(requirements_sha)
    observation_identity = copy.deepcopy(observation)
    observation_identity.pop("observed_at")
    host_facts = _host_facts()
    return {
        "schema": "taiji-package-online-doctor/v2",
        "builder_status": "BUILDER_READY",
        "blockers": [],
        "python_path": json.loads(
            (ROOT / "packaging/pipeline/targets/windows-x64.json").read_text(encoding="utf-8")
        )["python"],
        "cache_requirements_sha256": requirements_sha,
        "cache_observation": observation,
        "cache_observation_sha256": _canonical_sha(observation_identity),
        "host_facts": host_facts,
        "host_facts_sha256": _canonical_sha(host_facts),
    }


def _tool_evidence(plan):
    target = plan["target_config"]
    tools = {}
    for name in ("powershell", "tar", "node", "npm", "python", "iscc"):
        tools[name] = {
            "path": target[name],
            "bytes": 1,
            "sha256": "a" * 64,
            "version": "1.0.0",
        }
    tools["safe_tar"] = {
        "path": plan["remote_run_dir"] + "\\" + plan["controller_bootstrap"]["safe_tar"]["remote_path"].lstrip("\\"),
        "bytes": 1,
        "sha256": "b" * 64,
        "version": "taiji-safe-tar/v1",
    }
    return tools


class _FixtureControllerGitRunner:
    def __call__(self, argv, **_kwargs):
        command = [str(item) for item in argv]
        repo = Path(command[2])
        if command[3:] == ["status", "--porcelain=v2", "--branch"]:
            stdout = "# branch.oid {}\n# branch.head main\n".format("a" * 40)
        elif command[3:] == ["rev-parse", "HEAD^{commit}"]:
            stdout = "{}\n".format("a" * 40)
        elif command[3:] == ["rev-parse", "HEAD^{tree}"]:
            stdout = "{}\n".format("b" * 40)
        elif command[3:] == ["show", "a" * 40 + ":VERSION"]:
            stdout = "1.0.4\n"
        elif command[3:] == ["show", "a" * 40 + ":apps/taiji-desktop/package.json"]:
            stdout = '{"name":"taiji-desktop","version":"1.0.4"}\n'
        elif command[3] == "show" and command[4].startswith("a" * 40 + ":"):
            relative_path = command[4].split(":", 1)[1]
            stdout = (repo / relative_path).read_bytes()
        else:
            raise AssertionError("unexpected fixture Git command: {}".format(command))
        return subprocess.CompletedProcess(command, 0, stdout, "")


def make_windows_plan(root, **overrides):
    from packaging.pipeline.adapters import windows_x64 as adapter_module
    from packaging.pipeline.adapters.windows_x64 import WindowsX64Adapter

    class FixtureWindowsAdapter(WindowsX64Adapter):
        def inspect_input(self, repo, source_commit):
            del repo, source_commit
            return {"status": "MISSING", "files": {}}

    root = Path(root).resolve()
    repo = root / "source"
    repo.mkdir(parents=True, exist_ok=True)
    for relative_path in (
        "packaging/pipeline/targets/windows-x64.json",
        "packaging/windows/asset-provenance.json",
        "packaging/windows/safe_tar.py",
    ):
        write_regular(repo / relative_path, (ROOT / relative_path).read_bytes())
    state_root = root / "state"
    target = json.loads(
        (ROOT / "packaging/pipeline/targets/windows-x64.json").read_text(encoding="utf-8")
    )
    adapter = FixtureWindowsAdapter(controller_runner=_FixtureControllerGitRunner())
    source_commit = "a" * 40
    input_stem = "taijiagent-windows-builder-input-{}".format(source_commit)
    input_basenames = {
        "archive": input_stem + ".tar.gz",
        "manifest": input_stem + ".manifest.json",
        "sidecar": input_stem + ".tar.gz.sha256",
    }
    files = {}
    for role, basename in (
        ("archive", input_basenames["archive"]),
        ("manifest", input_basenames["manifest"]),
        ("checksum", input_basenames["sidecar"]),
    ):
        data = ("fixture-{}-{}\n".format(role, source_commit)).encode("utf-8")
        path = write_regular(repo / basename, data)
        files[role] = {
            "path": str(path),
            "basename": basename,
            "bytes": len(data),
            "sha256": sha256_bytes(data),
            "exists": True,
        }
    with mock.patch.multiple(
        adapter_module,
        TARGET_CONFIG_PATH=repo / "packaging/pipeline/targets/windows-x64.json",
        ASSET_PROVENANCE_PATH=repo / "packaging/windows/asset-provenance.json",
        SAFE_TAR_SOURCE_PATH=repo / "packaging/windows/safe_tar.py",
    ):
        plan = adapter.build_plan(
            repo,
            target,
            state_root,
            run_id=overrides.pop("run_id", "20260820T120000Z-aaaaaaaaaaaa-aaaaaaaa"),
            ssh_config=None,
        )
    plan["input"] = {"status": "REUSABLE", "files": files}
    plan["cache_requirements"] = _requirements()
    online = _complete_online()
    plan = adapter.bind_online_plan(plan, online)
    for key, value in overrides.items():
        plan[key] = copy.deepcopy(value)
    return plan


class FakeArtifactInspector:
    def __init__(
        self,
        *,
        file_version="1.0.4.0",
        product_version="1.0.4.0",
        authenticode_status="NotSigned"
    ):
        self.file_version = file_version
        self.product_version = product_version
        self.authenticode_status = authenticode_status

    def inspect(self, path):
        path = Path(path)
        if not path.is_file():
            raise AssertionError("fake artifact is missing: {}".format(path))
        return {
            "pe_machine": "0x014c",
            "pe_optional_magic": "0x10b",
            "file_version": self.file_version,
            "product_version": self.product_version,
            "authenticode_status": self.authenticode_status,
        }


def _write_json(path, value, *, canonical=True):
    if canonical:
        data = canonical_json_bytes(value) + b"\n"
    else:
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return write_regular(path, data)


def make_windows_review(root, plan, *, corruption=None):
    allowed = {
        None,
        "missing-review-file",
        "extra-review-file",
        "review-symlink",
        "sidecar-sha",
        "manifest-source",
        "manifest-input",
        "manifest-payload-sha",
        "artifact-sha",
        "pe-machine",
        "pe-optional-magic",
        "file-version",
        "product-version",
        "authenticode-status",
        "remote-state",
        "marker-sha",
        "noncanonical-json",
    }
    if corruption not in allowed:
        raise ValueError("unknown review corruption: {}".format(corruption))
    root = Path(root)
    review = root / "review"
    review.mkdir(parents=True, exist_ok=True)
    remote_log = root / "logs" / "remote-build.log"
    version = plan["version"]
    basename = "TaijiAgent-Setup-{}-win-x64.exe".format(version)
    artifact = write_regular(review / basename, make_minimal_inno_setup_pe(version))
    artifact_sha = sha256_bytes(artifact.read_bytes())
    sidecar = "{}  {}\n".format(artifact_sha, basename).encode("utf-8")
    write_regular(review / (basename + ".sha256"), sidecar)

    payload_identity = {
        "schema": "taiji-windows-payload-manifest/v1",
        "source_commit": plan["source_commit"],
        "source_tree": plan["source_tree"],
        "entries": [
            {"path": basename, "bytes": artifact.stat().st_size, "sha256": artifact_sha}
        ],
        "file_count": 1,
        "total_bytes": artifact.stat().st_size,
    }
    payload = copy.deepcopy(payload_identity)
    payload["manifest_sha256"] = _canonical_sha(payload_identity)
    manifest = {
        "schema": "taiji-package-manifest/v2",
        "run_id": plan["run_id"],
        "target_id": "windows-x64",
        "source": {
            "branch": plan["source_branch"],
            "commit": plan["source_commit"],
            "tree": plan["source_tree"],
        },
        "input": {
            "archive": {
                "basename": plan["input"]["files"]["archive"]["basename"],
                "bytes": plan["input"]["files"]["archive"]["bytes"],
                "sha256": plan["input"]["files"]["archive"]["sha256"],
            },
            "manifest": {
                "basename": plan["input"]["files"]["manifest"]["basename"],
                "bytes": plan["input"]["files"]["manifest"]["bytes"],
                "sha256": plan["input"]["files"]["manifest"]["sha256"],
            },
            "sidecar": {
                "basename": plan["input"]["files"]["checksum"]["basename"],
                "bytes": plan["input"]["files"]["checksum"]["bytes"],
                "sha256": plan["input"]["files"]["checksum"]["sha256"],
            },
        },
        "target_config_sha256": plan["target_config_sha256"],
        "asset_provenance_sha256": plan["asset_provenance_sha256"],
        "cache_requirements_sha256": plan["cache_requirements_sha256"],
        "cache_observation_sha256": plan["cache_observation_sha256"],
        "tools": _tool_evidence(plan),
        "payload": payload,
        "formal_tests": {
            "checks": [
                {"id": "source-session-identity", "result": "PASS", "exit_code": 0},
                {"id": "offline-npm-ci", "result": "PASS", "exit_code": 0},
                {"id": "electron-win32-x64", "result": "PASS", "exit_code": 0},
                {"id": "payload-import-menu-policy", "result": "PASS", "exit_code": 0},
                {"id": "payload-hygiene-closure", "result": "PASS", "exit_code": 0},
                {"id": "inno-compile", "result": "PASS", "exit_code": 0},
                {"id": "installer-pe-version-authenticode", "result": "PASS", "exit_code": 0},
            ],
            "log_basename": "formal-build-tests.log",
            "log_bytes": 0,
            "log_sha256": "0" * 64,
            "status": "PASS",
        },
        "artifact": {
            "kind": "exe",
            "basename": basename,
            "version": version,
            "bytes": artifact.stat().st_size,
            "sha256": artifact_sha,
            "pe_machine": "0x014c",
            "pe_optional_magic": "0x10b",
            "file_version": version + ".0",
            "product_version": version + ".0",
            "authenticode_status": "NotSigned",
        },
        "boundaries": {
            "installation": False,
            "interactive_acceptance": False,
            "production_license": False,
            "signing": False,
            "publication": False,
        },
        "started_at": "2026-08-20T12:00:00Z",
        "finished_at": "2026-08-20T12:01:00Z",
    }
    if corruption == "manifest-source":
        manifest["source"]["commit"] = "f" * 40
    elif corruption == "manifest-input":
        manifest["input"]["manifest"]["sha256"] = "f" * 64
    elif corruption == "manifest-payload-sha":
        manifest["payload"]["manifest_sha256"] = "f" * 64
    elif corruption == "artifact-sha":
        manifest["artifact"]["sha256"] = "f" * 64
    formal_lines = [
        "01 source-session-identity PASS exit=0",
        "02 offline-npm-ci PASS exit=0",
        "03 electron-win32-x64 PASS exit=0",
        "04 payload-import-menu-policy PASS exit=0",
        "05 payload-hygiene-closure PASS exit=0",
        "06 inno-compile PASS exit=0",
        "07 installer-pe-version-authenticode PASS exit=0",
        "SUMMARY PASS checks=7",
    ]
    formal_log = write_regular(
        review / "formal-build-tests.log", ("\n".join(formal_lines) + "\n").encode("utf-8")
    )
    manifest["formal_tests"]["log_bytes"] = formal_log.stat().st_size
    manifest["formal_tests"]["log_sha256"] = sha256_bytes(formal_log.read_bytes())
    _write_json(
        review / "taiji-package-manifest.json",
        manifest,
        canonical=corruption != "noncanonical-json",
    )
    write_regular(review / "构建报告.txt", "fake Windows candidate review\n".encode("utf-8"))
    remote_state = {
        "schema": "taiji-package-remote-run/v1",
        "run_id": plan["run_id"],
        "target_id": "windows-x64",
        "source_commit": plan["source_commit"],
        "host_facts_sha256": plan["host_facts_sha256"],
        "stage_history": [
            {
                "stage": "review-ready",
                "started_at": "2026-08-20T12:00:00Z",
                "finished_at": "2026-08-20T12:01:00Z",
                "result": "PASS",
            }
        ],
        "terminal_status": "REMOTE_BUILD_SUCCEEDED",
        "started_at": "2026-08-20T12:00:00Z",
        "finished_at": "2026-08-20T12:01:00Z",
    }
    if corruption == "remote-state":
        remote_state["source_commit"] = "f" * 40
    _write_json(review / "run-state.json", remote_state)
    marker = {
        "schema": "taiji-package-build-success/v1",
        "run_id": plan["run_id"],
        "target_id": "windows-x64",
        "source_commit": plan["source_commit"],
        "artifact_basename": basename,
        "artifact_bytes": artifact.stat().st_size,
        "artifact_sha256": artifact_sha,
        "package_manifest_basename": "taiji-package-manifest.json",
        "package_manifest_bytes": (review / "taiji-package-manifest.json").stat().st_size,
        "package_manifest_sha256": sha256_bytes((review / "taiji-package-manifest.json").read_bytes()),
        "formal_build_tests_log_basename": "formal-build-tests.log",
        "formal_build_tests_log_bytes": (review / "formal-build-tests.log").stat().st_size,
        "formal_build_tests_log_sha256": sha256_bytes((review / "formal-build-tests.log").read_bytes()),
        "report_basename": "构建报告.txt",
        "report_bytes": (review / "构建报告.txt").stat().st_size,
        "report_sha256": sha256_bytes((review / "构建报告.txt").read_bytes()),
        "remote_state_basename": "run-state.json",
        "remote_state_bytes": (review / "run-state.json").stat().st_size,
        "remote_state_sha256": sha256_bytes((review / "run-state.json").read_bytes()),
    }
    if corruption == "marker-sha":
        marker["artifact_sha256"] = "f" * 64
    _write_json(review / ".build-success", marker)
    if corruption == "sidecar-sha":
        write_regular(review / (basename + ".sha256"), ("0" * 64 + "  " + basename + "\n").encode("utf-8"))
    if corruption == "pe-machine":
        data = bytearray(artifact.read_bytes())
        struct.pack_into("<H", data, 0x84, 0x8664)
        write_regular(artifact, bytes(data))
    if corruption == "pe-optional-magic":
        data = bytearray(artifact.read_bytes())
        struct.pack_into("<H", data, 0x98, 0x20B)
        write_regular(artifact, bytes(data))
    if corruption == "missing-review-file":
        (review / "构建报告.txt").unlink()
    if corruption == "extra-review-file":
        write_regular(review / "extra.txt", b"extra")
    if corruption == "review-symlink":
        path = review / "构建报告.txt"
        path.unlink()
        os.symlink(review / basename, path)
    write_regular(remote_log, b"fake remote build log\n")
    inspector = FakeArtifactInspector(
        file_version="9.9.9.9" if corruption == "file-version" else "1.0.4.0",
        product_version="9.9.9.9" if corruption == "product-version" else "1.0.4.0",
        authenticode_status="Valid" if corruption == "authenticode-status" else "NotSigned",
    )
    return review, remote_log, inspector


class FakeWindowsTransport:
    def __init__(self, review_factory, *, failure_at=None, events=None):
        self.review_factory = review_factory
        self.failure_at = failure_at
        self.events = events if events is not None else []
        self.remote_build_succeeded = False
        self.remote_run_created = False
        self.last_inspector = None
        self.last_plan = None

    def online_doctor(self):
        from packaging.pipeline.core.errors import PipelineError

        self.events.append("online-doctor")
        if self.failure_at == "builder-unreachable":
            raise PipelineError("fake Windows builder is unreachable", category="BUILDER_UNREACHABLE")
        if self.failure_at == "cache-missing":
            raise PipelineError("fake Windows cache is missing", category="WINDOWS_CACHE_MISSING")
        return _complete_online()

    def create_remote_run(self, plan):
        from packaging.pipeline.core.errors import PipelineError

        self.events.append("create-remote-run")
        if self.failure_at == "create-remote-run":
            raise PipelineError("fake run creation failed", category="WINDOWS_RUN_FAILED")
        self.last_plan = copy.deepcopy(plan)
        self.remote_run_created = True

    def transfer_input(self, plan):
        from packaging.pipeline.core.errors import PipelineError

        self.events.append("transfer-input")
        if self.failure_at == "transfer":
            raise PipelineError("fake input transfer interrupted", category="SCP_INTERRUPTED")
        if not self.remote_run_created or plan.get("run_id") != self.last_plan.get("run_id"):
            raise AssertionError("fake transfer was not bound to its new run")

    def verify_remote_input(self, plan):
        from packaging.pipeline.core.errors import PipelineError

        self.events.append("remote-input-verify")
        if self.failure_at == "input-sha":
            raise PipelineError("fake input identity failed", category="INPUT_VERIFICATION_FAILED")
        if plan.get("source_commit") != self.last_plan.get("source_commit"):
            raise AssertionError("fake input verification source drifted")

    def build_remote_candidate(self, plan):
        from packaging.pipeline.core.errors import PipelineError

        self.events.append("remote-candidate-build")
        failure_categories = {
            "payload": "WINDOWS_PAYLOAD_FAILED",
            "inno": "WINDOWS_INNO_FAILED",
        }
        if self.failure_at in failure_categories:
            raise PipelineError("fake Windows build failed", category=failure_categories[self.failure_at])
        if not self.remote_run_created or plan.get("source_tree") != self.last_plan.get("source_tree"):
            raise AssertionError("fake build was not bound to the frozen plan")
        self.remote_build_succeeded = True

    def fetch(self, plan, staging_dir):
        from packaging.pipeline.core.errors import PipelineError

        if not self.remote_build_succeeded:
            raise PipelineError("fake remote build has not succeeded", category="FETCH_NOT_ALLOWED")
        review, remote_log, inspector = self.review_factory(Path(staging_dir), plan)
        self.last_inspector = inspector
        self.events.append("fetch-review")
        if self.failure_at == "fetch-review":
            raise PipelineError("fake review fetch interrupted", category="SCP_INTERRUPTED")
        if not remote_log.is_file():
            raise PipelineError("fake remote log is missing", category="SCP_INTERRUPTED")
        fetched_log = Path(staging_dir) / "remote-build.log"
        write_regular(fetched_log, remote_log.read_bytes())
        self.events.append("fetch-log")
        if self.failure_at == "fetch-log":
            raise PipelineError("fake log fetch interrupted", category="SCP_INTERRUPTED")
        return {"review_path": str(review), "remote_log_path": str(fetched_log)}


__all__ = [
    "sha256_bytes",
    "canonical_json_bytes",
    "write_regular",
    "make_minimal_inno_setup_pe",
    "make_windows_plan",
    "make_windows_review",
    "FakeArtifactInspector",
    "FakeWindowsTransport",
]
