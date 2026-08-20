"""Boundary contracts for the common core and Kylin adapter facade."""

import importlib
import importlib.util
import inspect
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "scripts/taiji-package-candidate.py"


def load_facade():
    spec = importlib.util.spec_from_file_location("taiji_package_candidate_boundary", FACADE)
    if spec is None or spec.loader is None:
        raise AssertionError("candidate facade cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, AttributeError) as exc:
        raise AssertionError("candidate facade is not importable: {}".format(exc))
    return module


class ForbiddenExternalRunner:
    def __call__(self, *args, **kwargs):
        raise AssertionError("the adapter attempted an external command")


class TaijiPackageCoreBoundaryTests(unittest.TestCase):
    def test_facade_exports_exact_legacy_compatibility_set(self):
        facade = load_facade()
        names = (
            "PipelineError", "RunStateStore", "RunLock", "RealSshTransport",
            "FakeSshTransport", "_online_doctor_script", "load_target",
            "local_doctor", "input_triplet_paths", "inspect_builder_input",
            "build_candidate_plan", "validate_candidate_review",
            "execute_candidate_transport", "run_candidate_build",
            "fetch_candidate", "parse_args", "main",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(hasattr(facade, name), "facade is missing {}".format(name))

    def test_facade_types_are_same_core_or_adapter_objects(self):
        facade = load_facade()
        errors = importlib.import_module("packaging.pipeline.core.errors")
        state = importlib.import_module("packaging.pipeline.core.state")
        kylin = importlib.import_module("packaging.pipeline.adapters.kylin_amd64")
        self.assertIs(facade.PipelineError, errors.PipelineError)
        self.assertIs(facade.RunStateStore, state.RunStateStore)
        self.assertIs(facade.RunLock, state.RunLock)
        self.assertIs(facade.RealSshTransport, kylin.RealSshTransport)
        self.assertIs(facade.FakeSshTransport, kylin.FakeSshTransport)

    def test_legacy_function_signatures_remain_compatible(self):
        facade = load_facade()
        expected = {
            "_online_doctor_script": ["target"],
            "load_target": ["path"],
            "local_doctor": ["repo", "target", "state_root", "ssh_config"],
            "input_triplet_paths": ["repo", "source_commit"],
            "inspect_builder_input": ["repo", "source_commit"],
            "build_candidate_plan": ["repo", "target", "state_root", "run_id", "ssh_config"],
            "validate_candidate_review": ["plan", "review_path", "remote_log_path", "command_runner"],
            "execute_candidate_transport": ["plan", "transport", "staging_dir", "confirmed", "prepare_input"],
            "run_candidate_build": [
                "plan", "store", "transport", "confirmed", "online_result", "prepare_input",
                "command_runner", "review_validator",
            ],
            "fetch_candidate": ["store", "run_id", "transport", "review_validator"],
            "parse_args": ["argv"],
            "main": ["argv"],
        }
        for name, parameters in expected.items():
            with self.subTest(name=name):
                function = getattr(facade, name, None)
                self.assertTrue(callable(function), "missing callable {}".format(name))
                self.assertEqual(list(inspect.signature(function).parameters), parameters)

    def test_facade_factory_reads_transport_and_validator_globals_at_call_time(self):
        facade = load_facade()
        factory = getattr(facade, "_facade_adapter_factory", None)
        self.assertTrue(callable(factory), "facade adapter factory is missing")
        validator_marker = {"marker": True}

        class PatchedTransport:
            def __init__(self, repo, target, *, ssh_config, command_runner):
                self.args = (repo, target, ssh_config, command_runner)

        def patched_validator(plan, review, remote_log):
            return validator_marker

        with mock.patch.object(facade, "RealSshTransport", PatchedTransport), mock.patch.object(
            facade, "validate_candidate_review", patched_validator
        ):
            adapter = factory("kylin-amd64")
            with tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary) / "repo"
                target = {"target_id": "kylin-amd64"}
                transport = adapter.create_transport(
                    repo,
                    target,
                    ssh_config=None,
                    command_runner=ForbiddenExternalRunner(),
                )
                self.assertIsInstance(transport, PatchedTransport)
                self.assertIs(transport.args[-1].__class__, ForbiddenExternalRunner)
                self.assertEqual(
                    adapter.validate_review({}, Path("review"), Path("log")),
                    {"marker": True, "kind": "deb"},
                )

    def test_extracted_common_modules_have_no_platform_build_literals(self):
        forbidden = (
            r"99_本机", r"00_制包机", r"01_制包机", r"\bdpkg\b", r"\bapt\b",
            r"\.deb\b", r"canonical_policy_sha256", r"deb_sha256",
        )
        paths = [
            ROOT / "packaging/pipeline/core/{}.py".format(name)
            for name in ("models", "state", "errors", "registry")
        ]
        for path in paths:
            self.assertTrue(path.is_file(), str(path))
            text = path.read_text(encoding="utf-8")
            for literal in forbidden:
                with self.subTest(path=str(path), literal=literal):
                    self.assertIsNone(re.search(literal, text))

    def test_final_common_modules_have_no_platform_build_literals(self):
        forbidden = (
            r"99_本机", r"00_制包机", r"01_制包机", r"\bdpkg\b", r"\bapt\b",
            r"\.deb\b", r"canonical_policy_sha256", r"deb_sha256",
        )
        paths = [
            ROOT / "packaging/pipeline/cli.py",
            ROOT / "packaging/pipeline/core/orchestration.py",
        ]
        for path in paths:
            self.assertTrue(path.is_file(), str(path))
            text = path.read_text(encoding="utf-8")
            for literal in forbidden:
                with self.subTest(path=str(path), literal=literal):
                    self.assertIsNone(re.search(literal, text))


if __name__ == "__main__":
    unittest.main()
