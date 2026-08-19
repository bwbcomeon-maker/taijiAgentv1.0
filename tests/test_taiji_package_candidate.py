"""Contract tests for the thin x86 Kylin candidate pipeline."""

from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "taiji-package"
CANDIDATE = ROOT / "scripts/taiji-package-candidate.py"
TARGET = ROOT / "packaging/pipeline/targets/kylin-amd64.json"


def load_candidate():
    spec = importlib.util.spec_from_file_location("taiji_package_candidate", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Taiji candidate pipeline")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CandidatePipelineContractTests(unittest.TestCase):
    def test_unified_cli_and_target_adapter_exist(self):
        self.assertTrue(ENTRYPOINT.is_file(), "unified taiji-package CLI is missing")
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        self.assertTrue(TARGET.is_file(), "kylin-amd64 target adapter is missing")

    def test_local_doctor_and_plan_contracts_exist(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(callable(module.local_doctor))
        self.assertTrue(callable(module.build_candidate_plan))

    def test_run_state_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(hasattr(module, "RunStateStore"))

    def test_fetch_recovery_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(callable(module.fetch_candidate))

    def test_target_adapter_is_non_sensitive_and_python38_compatible(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        self.assertTrue(TARGET.is_file(), "kylin-amd64 target adapter is missing")
        module = load_candidate()

        target = module.load_target(TARGET)

        self.assertEqual(target["schema"], "taiji-package-target/v1")
        self.assertEqual(target["target_id"], "kylin-amd64")
        self.assertEqual(target["host_alias"], "kylin")
        self.assertEqual(target["remote_user"], "kylin")
        self.assertEqual(target["remote_account_home"], "/home/kylin")
        self.assertEqual(target["remote_root"], "/home/kylin/taiji-builds")
        self.assertEqual(target["architecture"], "amd64")
        self.assertEqual(target["minimum_free_gib"], 12)
        self.assertEqual(target["minimum_free_inodes"], 100000)
        serialized = json.dumps(target, ensure_ascii=False)
        for secret_name in ("password", "private_key", "token", "credential"):
            self.assertNotIn(secret_name, serialized.lower())

    def test_parser_exposes_only_candidate_lifecycle_commands(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()

        doctor = module.parse_args(["doctor", "--online"])
        plan = module.parse_args(["plan"])
        build = module.parse_args(["build"])
        status = module.parse_args(["status", "--run", "run-1"])
        fetch = module.parse_args(["fetch", "--run", "run-1"])

        self.assertEqual((doctor.command, doctor.online), ("doctor", True))
        self.assertEqual(plan.command, "plan")
        self.assertEqual(build.command, "build")
        self.assertEqual((status.command, status.run_id), ("status", "run-1"))
        self.assertEqual((fetch.command, fetch.run_id), ("fetch", "run-1"))

    def test_run_state_store_creates_updates_and_refuses_overwrite(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-package-state-") as temporary:
            state_root = Path(temporary) / "state"
            store = module.RunStateStore(state_root)

            created = store.create(
                "run-1",
                {
                    "source_commit": "a" * 40,
                    "stage": "CREATED",
                    "host_alias": "kylin",
                },
            )
            loaded = store.load("run-1")

            self.assertEqual(created, loaded)
            self.assertEqual(loaded["schema"], "taiji-package-run-state/v1")
            self.assertEqual(loaded["run_id"], "run-1")
            self.assertEqual(loaded["source_commit"], "a" * 40)
            self.assertEqual(loaded["stage"], "CREATED")
            run_dir = state_root / "runs/run-1"
            self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((run_dir / "run-state.json").stat().st_mode), 0o600
            )

            updated = store.update("run-1", {"stage": "FETCH_PENDING"})
            self.assertEqual(updated["stage"], "FETCH_PENDING")
            self.assertEqual(store.load("run-1")["stage"], "FETCH_PENDING")
            with self.assertRaises(module.PipelineError):
                store.create("run-1", {"stage": "CREATED"})
            with self.assertRaises(module.PipelineError):
                store.load("../escape")


if __name__ == "__main__":
    unittest.main()
