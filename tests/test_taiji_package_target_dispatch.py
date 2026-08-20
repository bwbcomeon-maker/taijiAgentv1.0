import importlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "scripts/taiji-package-candidate.py"
LAUNCHER = ROOT / "taiji-package"
TARGET_DIR = ROOT / "packaging/pipeline/targets"
WINDOWS_TARGET = TARGET_DIR / "windows-x64.json"


def required(module_name, symbol):
    try:
        module = importlib.import_module(module_name)
        return getattr(module, symbol)
    except (ImportError, AttributeError) as exc:
        raise AssertionError(
            "missing production symbol {}.{}: {}".format(module_name, symbol, exc)
        )


def load_facade():
    spec = importlib.util.spec_from_file_location("taiji_package_candidate_facade", FACADE)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load candidate facade")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, AttributeError) as exc:
        raise AssertionError("candidate facade is not importable: {}".format(exc))
    return module


class TaijiPackageTargetDispatchTests(unittest.TestCase):
    def test_parser_preserves_omitted_target_as_none(self):
        facade = load_facade()
        self.assertIsNone(facade.parse_args(["doctor"]).target)
        self.assertEqual(
            facade.parse_args(["--target", "windows-x64", "doctor"]).target,
            "windows-x64",
        )

    def test_registered_id_resolves_exact_builtin_file(self):
        resolve_target_reference = required(
            "packaging.pipeline.core.registry", "resolve_target_reference"
        )
        path = resolve_target_reference(
            "kylin-amd64",
            TARGET_DIR,
            registered={"kylin-amd64": "kylin-amd64.json"},
        )
        self.assertEqual(path, (TARGET_DIR / "kylin-amd64.json").resolve())

    def test_registered_windows_id_resolves_exact_builtin_file(self):
        resolve_target_reference = required(
            "packaging.pipeline.core.registry", "resolve_target_reference"
        )
        self.assertTrue(WINDOWS_TARGET.is_file(), str(WINDOWS_TARGET))
        path = resolve_target_reference(
            "windows-x64",
            TARGET_DIR,
            registered={
                "kylin-amd64": "kylin-amd64.json",
                "windows-x64": "windows-x64.json",
            },
        )
        self.assertEqual(path, WINDOWS_TARGET.resolve())

    def test_absolute_config_remains_supported(self):
        resolve_target_reference = required(
            "packaging.pipeline.core.registry", "resolve_target_reference"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom-target.json"
            path.write_text(json.dumps({"target_id": "kylin-amd64"}), encoding="utf-8")
            resolved = resolve_target_reference(
                str(path),
                TARGET_DIR,
                registered={"kylin-amd64": "kylin-amd64.json"},
            )
            self.assertEqual(resolved, path.resolve())

    def test_relative_unknown_and_option_like_target_are_rejected(self):
        resolve_target_reference = required(
            "packaging.pipeline.core.registry", "resolve_target_reference"
        )
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        for value in ("windows-x64", "../x.json", "targets/x.json", "-oProxyCommand=x"):
            with self.subTest(value=value):
                with self.assertRaises(pipeline_error) as context:
                    resolve_target_reference(
                        value,
                        TARGET_DIR,
                        registered={"kylin-amd64": "kylin-amd64.json"},
                    )
                self.assertEqual(context.exception.category, "TARGET_INVALID")

    def test_isolated_facade_bootstraps_exact_repo_package_from_external_cwd(self):
        probe = (
            "import runpy,sys;"
            "ns=runpy.run_path(sys.argv[1],run_name='candidate_probe');"
            "print(ns['_pipeline_package'].__file__)"
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                ["/usr/bin/python3", "-I", "-B", "-c", probe, str(FACADE)],
                cwd=temporary,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            Path(completed.stdout.strip()).resolve(),
            (ROOT / "packaging/pipeline/__init__.py").resolve(),
        )

    def test_launcher_and_shim_help_work_from_external_cwd(self):
        with tempfile.TemporaryDirectory() as temporary:
            launcher = subprocess.run(
                [str(LAUNCHER), "--help"],
                cwd=temporary,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            shim = subprocess.run(
                ["/usr/bin/python3", "-I", "-B", str(FACADE), "--help"],
                cwd=temporary,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(launcher.returncode, 0, launcher.stderr)
        self.assertEqual(shim.returncode, 0, shim.stderr)
        self.assertNotIn("Traceback", launcher.stderr + shim.stderr)

    def test_candidate_adapter_exposes_exact_eleven_hooks(self):
        adapter_type = required(
            "packaging.pipeline.adapters.base", "CandidateAdapter"
        )
        hooks = (
            "validate_target", "local_doctor", "inspect_input", "build_plan",
            "bind_online_plan", "prepare_input", "create_transport",
            "validate_review", "initial_state_patch", "success_state_patch",
            "normalize_legacy_state",
        )
        for hook in hooks:
            with self.subTest(hook=hook):
                self.assertTrue(callable(getattr(adapter_type, hook, None)))

    def test_registry_returns_platform_specific_adapters(self):
        create_adapter = required(
            "packaging.pipeline.core.registry", "create_adapter"
        )
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        windows_adapter_type = required(
            "packaging.pipeline.adapters.windows_x64", "WindowsX64Adapter"
        )
        kylin = create_adapter("kylin-amd64")
        self.assertEqual(kylin.target_id, "kylin-amd64")
        windows = create_adapter("windows-x64")
        self.assertIsInstance(windows, windows_adapter_type)
        self.assertEqual(windows.target_id, "windows-x64")
        with self.assertRaises(pipeline_error) as context:
            create_adapter("unknown-target")
        self.assertEqual(context.exception.category, "TARGET_INVALID")

    def test_registry_never_imports_class_from_target_json(self):
        create_adapter = required(
            "packaging.pipeline.core.registry", "create_adapter"
        )
        pipeline_error = required("packaging.pipeline.core.errors", "PipelineError")
        adapter = create_adapter("kylin-amd64")
        payload = json.loads(
            (TARGET_DIR / "kylin-amd64.json").read_text(encoding="utf-8")
        )
        payload["python_class"] = "builtins.object"
        payload["module"] = "os"
        with self.assertRaises(pipeline_error) as context:
            adapter.validate_target(payload)
        self.assertEqual(context.exception.category, "TARGET_INVALID")

    def test_kylin_adapter_normalizes_v1_without_mutating_source(self):
        adapter_type = required(
            "packaging.pipeline.adapters.kylin_amd64", "KylinAmd64Adapter"
        )
        canonical_json_sha256 = required(
            "packaging.pipeline.core.models", "canonical_json_sha256"
        )
        adapter = adapter_type()
        target = {"target_id": "kylin-amd64", "host_alias": "kylin", "architecture": "amd64"}
        legacy = {
            "schema": "taiji-package-run-state/v1",
            "run_id": "legacy-run",
            "source_commit": "a" * 40,
            "canonical_policy_sha256": "b" * 64,
            "deb": {"basename": "taiji-agent_1.0_amd64.deb", "bytes": 10, "sha256": "c" * 64, "path": "/tmp/deb"},
            "plan": {"target_adapter": target, "repo_root": "/tmp/repo", "source_commit": "a" * 40},
        }
        before = deepcopy(legacy)
        normalized = adapter.normalize_legacy_state(legacy)
        self.assertEqual(normalized["target_id"], "kylin-amd64")
        self.assertEqual(normalized["target_config"], target)
        self.assertEqual(normalized["target_config_sha256"], canonical_json_sha256(target))
        self.assertEqual(normalized["source"]["commit"], "a" * 40)
        self.assertEqual(normalized["policy"]["sha256"], "b" * 64)
        self.assertEqual(normalized["artifact"]["kind"], "deb")
        self.assertEqual(legacy, before)

    def test_no_non_kylin_module_contains_legacy_linux_mapping_literals(self):
        core_forbidden = ("canonical_policy_sha256", "deb_sha256", "normalize_legacy_state")
        core_paths = [
            ROOT / "packaging/pipeline/core/{}.py".format(name)
            for name in ("models", "state", "errors", "registry")
        ]
        for path in core_paths:
            self.assertTrue(path.is_file(), str(path))
            text = path.read_text(encoding="utf-8")
            for literal in core_forbidden:
                with self.subTest(path=str(path), literal=literal):
                    self.assertNotIn(literal, text)

        base_path = ROOT / "packaging/pipeline/adapters/base.py"
        self.assertTrue(base_path.is_file(), str(base_path))
        base_text = base_path.read_text(encoding="utf-8")
        for literal in ("canonical_policy_sha256", "deb_sha256"):
            with self.subTest(path=str(base_path), literal=literal):
                self.assertNotIn(literal, base_text)
        self.assertIn("def normalize_legacy_state(self, state):", base_text)

        kylin_path = ROOT / "packaging/pipeline/adapters/kylin_amd64.py"
        self.assertTrue(kylin_path.is_file(), str(kylin_path))
        kylin_text = kylin_path.read_text(encoding="utf-8")
        self.assertIn("def normalize_legacy_state(self, state):", kylin_text)
        self.assertIn("canonical_policy_sha256", kylin_text)


if __name__ == "__main__":
    unittest.main()
