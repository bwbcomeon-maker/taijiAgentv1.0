import importlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "scripts/taiji-package-candidate.py"
LAUNCHER = ROOT / "taiji-package"
TARGET_DIR = ROOT / "packaging/pipeline/targets"


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


if __name__ == "__main__":
    unittest.main()
