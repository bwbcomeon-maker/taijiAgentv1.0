"""Contract tests for the thin x86 Kylin candidate pipeline."""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
