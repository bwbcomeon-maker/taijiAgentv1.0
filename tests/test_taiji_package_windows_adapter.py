"""Contract tests for the Windows x64 target and adapter."""

import copy
import hashlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET_PATH = ROOT / "packaging/pipeline/targets/windows-x64.json"
CACHE_REQUIREMENTS_PATH = ROOT / "packaging/windows/cache-requirements.json"
ADAPTER_PATH = ROOT / "packaging/pipeline/adapters/windows_x64.py"

EXPECTED_TARGET = {
    "allowed_source_branches": ["main"],
    "architecture": "x64",
    "cache_requirements": "packaging/windows/cache-requirements.json",
    "cache_root": "D:\\tw\\cache",
    "git": "C:\\Program Files\\Git\\cmd\\git.exe",
    "host_alias": "windows-direct",
    "iscc": "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe",
    "minimum_free_gib": 20,
    "node": "C:\\Program Files\\nodejs\\node.exe",
    "npm": "C:\\Program Files\\nodejs\\npm.cmd",
    "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
    "python": "D:\\tw\\cache\\python-runtime\\python.exe",
    "remote_root": "D:\\tw\\taiji-builds",
    "schema": "taiji-package-target/v2",
    "target_id": "windows-x64",
    "tar": "C:\\Windows\\System32\\tar.exe",
}

EXPECTED_CACHE_REQUIREMENTS = {
    "entries": [
        {
            "architecture": "any",
            "id": "npm-cache",
            "relative_path": "npm",
            "required_members": ["_cacache"],
            "type": "directory",
            "version": "package-lock-bound",
        },
        {
            "architecture": "x64",
            "id": "electron-39.8.10-win32-x64",
            "relative_path": "electron/electron-v39.8.10-win32-x64.zip",
            "required_members": ["electron.exe"],
            "type": "regular-file",
            "version": "39.8.10",
        },
        {
            "architecture": "x64",
            "id": "private-python-runtime",
            "relative_path": "python-runtime",
            "required_members": ["python.exe", "python311._pth"],
            "type": "directory",
            "version": "3.11",
        },
    ],
    "schema": "taiji-windows-cache-requirements/v1",
    "target_id": "windows-x64",
}


def canonical_sha(value):
    models = importlib.import_module("packaging.pipeline.core.models")
    return models.canonical_json_sha256(value)


def load_windows_contract():
    for path in (TARGET_PATH, CACHE_REQUIREMENTS_PATH, ADAPTER_PATH):
        if not path.is_file():
            raise AssertionError("missing Windows contract file: {}".format(path))
    registry = importlib.import_module("packaging.pipeline.core.registry")
    adapter_module = importlib.import_module("packaging.pipeline.adapters.windows_x64")
    return registry, adapter_module


class ControllerGitRunner:
    def __init__(self, branch="main"):
        self.branch = branch
        self.calls = []

    def __call__(self, argv, **kwargs):
        del kwargs
        command = [str(item) for item in argv]
        self.calls.append(command)
        if command[:3] != ["/usr/bin/git", "-C", command[2]]:
            raise AssertionError("Windows adapter used an unexpected Git command")
        if command[3:] == ["status", "--porcelain=v2", "--branch"]:
            stdout = "# branch.oid {}\n# branch.head {}\n".format("a" * 40, self.branch)
        elif command[3:] == ["rev-parse", "HEAD^{commit}"]:
            stdout = "{}\n".format("a" * 40)
        elif command[3:] == ["rev-parse", "HEAD^{tree}"]:
            stdout = "{}\n".format("b" * 40)
        elif command[3:] == ["show", "a" * 40 + ":VERSION"]:
            stdout = "1.0.4\n"
        elif command[3:] == ["show", "a" * 40 + ":apps/taiji-desktop/package.json"]:
            stdout = '{"name":"taiji-desktop","version":"1.0.4"}\n'
        else:
            raise AssertionError("Windows adapter used an unapproved Git command: {}".format(command))
        return type("Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()


def complete_host_facts():
    return {
        "schema": "taiji-windows-host-facts/v1",
        "host_alias": "windows-direct",
        "os": "Windows",
        "os_version": "10.0",
        "architecture": "AMD64",
        "filesystem": "NTFS",
        "powershell_version": "5.1",
    }


def complete_cache_observation(requirements_sha):
    return {
        "schema": "taiji-windows-cache-observation/v1",
        "target_id": "windows-x64",
        "requirements_sha256": requirements_sha,
        "cache_root": "D:\\tw\\cache",
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


def complete_online(requirements_sha):
    host_facts = complete_host_facts()
    observation = complete_cache_observation(requirements_sha)
    observation_identity = copy.deepcopy(observation)
    observation_identity.pop("observed_at")
    return {
        "schema": "taiji-package-online-doctor/v2",
        "builder_status": "BUILDER_READY",
        "blockers": [],
        "cache_requirements_sha256": requirements_sha,
        "cache_observation": observation,
        "cache_observation_sha256": canonical_sha(observation_identity),
        "host_facts": host_facts,
        "host_facts_sha256": canonical_sha(host_facts),
    }


class WindowsAdapterContractTests(unittest.TestCase):
    def test_target_and_cache_requirements_are_exact(self):
        load_windows_contract()
        target = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
        requirements = json.loads(CACHE_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(target, EXPECTED_TARGET)
        self.assertEqual(requirements, EXPECTED_CACHE_REQUIREMENTS)
        self.assertEqual(
            set(target),
            set(EXPECTED_TARGET),
        )
        for forbidden in ("password", "private_key", "secret", "10.10."):
            self.assertNotIn(forbidden, TARGET_PATH.read_text(encoding="utf-8").lower())

    def test_windows_adapter_validates_target_and_has_exact_labels(self):
        _registry, adapter_module = load_windows_contract()
        adapter = adapter_module.WindowsX64Adapter()
        self.assertEqual(adapter.validate_target(EXPECTED_TARGET), EXPECTED_TARGET)
        self.assertEqual(adapter.target_id, "windows-x64")
        self.assertEqual(adapter.artifact_kind, "exe")
        self.assertEqual(adapter.not_built_label, "候选 EXE 未构建")
        self.assertEqual(adapter.pending_label, "候选 EXE 取回待恢复")
        self.assertEqual(adapter.success_label, "候选 EXE 已构建")
        self.assertEqual(
            adapter.online_plan_keys,
            (
                "cache_requirements_sha256",
                "cache_observation",
                "cache_observation_sha256",
                "host_facts",
                "host_facts_sha256",
            ),
        )

    def test_registry_maps_windows_to_windows_adapter(self):
        registry, adapter_module = load_windows_contract()
        adapter = registry.create_adapter("windows-x64")
        self.assertIsInstance(adapter, adapter_module.WindowsX64Adapter)

    def test_source_branch_and_version_are_controller_bound(self):
        _registry, adapter_module = load_windows_contract()
        with tempfile.TemporaryDirectory() as temporary:
            runner = ControllerGitRunner()
            adapter = adapter_module.WindowsX64Adapter(controller_runner=runner)
            target = adapter.validate_target(EXPECTED_TARGET)
            local = adapter.local_doctor(Path(temporary), target, Path(temporary) / "state", ssh_config=None)
            self.assertEqual(local["controller_status"], "CONTROLLER_READY")
            plan = adapter.build_plan(
                Path(temporary), target, Path(temporary) / "state", run_id="run-1", ssh_config=None
            )
            self.assertEqual(plan["source_branch"], "main")
            self.assertEqual(plan["source_commit"], "a" * 40)
            self.assertEqual(plan["source_tree"], "b" * 40)
            self.assertEqual(plan["version"], "1.0.4")
            self.assertEqual(plan["input_basenames"]["archive"], "taijiagent-windows-builder-input-{}.tar.gz".format("a" * 40))
            self.assertEqual(plan["input_basenames"]["manifest"], "taijiagent-windows-builder-input-{}.manifest.json".format("a" * 40))
            self.assertEqual(plan["input_basenames"]["sidecar"], "taijiagent-windows-builder-input-{}.tar.gz.sha256".format("a" * 40))
            for command in runner.calls:
                self.assertEqual(command[0], "/usr/bin/git")
                self.assertEqual(command[1:3], ["-C", str(Path(temporary))])

    def test_feature_branch_is_blocked_before_plan(self):
        _registry, adapter_module = load_windows_contract()
        with tempfile.TemporaryDirectory() as temporary:
            runner = ControllerGitRunner(branch="codex/windows")
            adapter = adapter_module.WindowsX64Adapter(controller_runner=runner)
            local = adapter.local_doctor(
                Path(temporary), EXPECTED_TARGET, Path(temporary) / "state", ssh_config=None
            )
            self.assertEqual(local["controller_status"], "BLOCKED")
            self.assertEqual(local["failure_categories"], ["BRANCH_NOT_MAIN"])

    def test_default_windows_transport_is_blocked_in_fake_phase(self):
        _registry, adapter_module = load_windows_contract()
        adapter = adapter_module.WindowsX64Adapter()
        pipeline_error = importlib.import_module("packaging.pipeline.core.errors").PipelineError
        with self.assertRaises(pipeline_error) as context:
            adapter.create_transport(Path("/tmp/repo"), EXPECTED_TARGET, ssh_config=None, command_runner=lambda argv: None)
        self.assertEqual(context.exception.category, "BUILDER_UNREACHABLE")

    def test_online_plan_binding_adds_only_frozen_cache_and_host_identity(self):
        _registry, adapter_module = load_windows_contract()
        with tempfile.TemporaryDirectory() as temporary:
            runner = ControllerGitRunner()
            adapter = adapter_module.WindowsX64Adapter(controller_runner=runner)
            target = adapter.validate_target(EXPECTED_TARGET)
            plan = adapter.build_plan(Path(temporary), target, Path(temporary) / "state", run_id="run-1", ssh_config=None)
            requirements = json.loads(CACHE_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
            requirements_sha = canonical_sha(requirements)
            online = complete_online(requirements_sha)
            original_plan = copy.deepcopy(plan)
            finalized = adapter.bind_online_plan(plan, online)
            self.assertEqual(plan, original_plan)
            self.assertEqual(set(finalized) - set(plan), set(adapter.online_plan_keys))
            self.assertEqual(finalized["cache_requirements_sha256"], requirements_sha)
            self.assertEqual(finalized["cache_observation_sha256"], online["cache_observation_sha256"])
            self.assertEqual(finalized["host_facts_sha256"], online["host_facts_sha256"])
            self.assertEqual(finalized["cache_observation"], online["cache_observation"])
            self.assertEqual(finalized["host_facts"], online["host_facts"])

    def test_online_plan_binding_rejects_missing_extra_or_drifted_identity(self):
        _registry, adapter_module = load_windows_contract()
        pipeline_error = importlib.import_module("packaging.pipeline.core.errors").PipelineError
        with tempfile.TemporaryDirectory() as temporary:
            runner = ControllerGitRunner()
            adapter = adapter_module.WindowsX64Adapter(controller_runner=runner)
            target = adapter.validate_target(EXPECTED_TARGET)
            plan = adapter.build_plan(Path(temporary), target, Path(temporary) / "state", run_id="run-1", ssh_config=None)
            requirements = json.loads(CACHE_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
            requirements_sha = canonical_sha(requirements)
            base = complete_online(requirements_sha)
            cases = []
            missing = copy.deepcopy(base)
            del missing["host_facts"]
            cases.append(missing)
            extra = copy.deepcopy(base)
            extra["unexpected"] = True
            cases.append(extra)
            drift = copy.deepcopy(base)
            drift["host_facts_sha256"] = "f" * 64
            cases.append(drift)
            wrong_requirements = copy.deepcopy(base)
            wrong_requirements["cache_requirements_sha256"] = "e" * 64
            cases.append(wrong_requirements)
            for online in cases:
                with self.subTest(online=online):
                    with self.assertRaises(pipeline_error) as context:
                        adapter.bind_online_plan(plan, online)
                    self.assertIn(context.exception.category, ("PLAN_INVALID", "ONLINE_DOCTOR_BLOCKED"))

    def test_initial_state_patch_binds_asset_and_cache_identity(self):
        _registry, adapter_module = load_windows_contract()
        with tempfile.TemporaryDirectory() as temporary:
            runner = ControllerGitRunner()
            adapter = adapter_module.WindowsX64Adapter(controller_runner=runner)
            target = adapter.validate_target(EXPECTED_TARGET)
            plan = adapter.build_plan(Path(temporary), target, Path(temporary) / "state", run_id="run-1", ssh_config=None)
            requirements = json.loads(CACHE_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
            online = complete_online(canonical_sha(requirements))
            finalized = adapter.bind_online_plan(plan, online)
            patch = adapter.initial_state_patch(finalized, online)
            identity = patch["identity"]
            self.assertEqual(identity["asset_provenance_sha256"], finalized["asset_provenance_sha256"])
            self.assertEqual(identity["cache_requirements_sha256"], finalized["cache_requirements_sha256"])
            self.assertEqual(identity["cache_observation_sha256"], finalized["cache_observation_sha256"])
            self.assertEqual(patch["policy"], None)


if __name__ == "__main__":
    unittest.main()
