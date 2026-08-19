"""Transport contract tests for the x86 Kylin candidate pipeline."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "scripts/taiji-package-candidate.py"


def load_candidate():
    spec = importlib.util.spec_from_file_location("taiji_package_transport", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Taiji candidate transport")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CandidateTransportContractTests(unittest.TestCase):
    def test_real_ssh_transport_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(hasattr(module, "RealSshTransport"))

    def test_fake_ssh_transport_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(hasattr(module, "FakeSshTransport"))


if __name__ == "__main__":
    unittest.main()
