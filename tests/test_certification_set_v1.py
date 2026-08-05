"""Strict certification-set assembler contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/assemble-taiji-certification-set.py"
MATRIX = ROOT / "packaging/linux/certification-matrix.json"


def load_script():
    spec = importlib.util.spec_from_file_location("taiji_certification_set_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load certification-set assembler")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CertificationSetV1Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-certification-set-")
        self.root = Path(self.temporary.name)
        self.records = self.root / "records"
        self.records.mkdir(mode=0o700)
        self.deb = self.root / "taiji-agent_1.2.3_amd64.deb"
        self.deb.write_bytes(b"immutable-deb-candidate-v1")
        self.deb_sha = hashlib.sha256(self.deb.read_bytes()).hexdigest()
        self.source_commit = "a" * 40
        self.version = "1.2.3"
        self.policy = self.root / "compatibility-policy.json"
        self.policy.write_text(
            json.dumps({"policy_id": "taiji-linux-amd64-deb-v1", "schema": "test-policy/v1"}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.policy_sha = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        self.offline = self.root / "offline-install-rehearsal.json"
        self.offline.write_text(
            json.dumps(
                {
                    "schema": "taiji.offline-install-rehearsal.v1",
                    "status": "PASS",
                    "source_commit": self.source_commit,
                    "version": self.version,
                    "architecture": "amd64",
                    "deb_basename": self.deb.name,
                    "deb_sha256": self.deb_sha,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": self.policy_sha,
                    "checks": {"install": "PASS", "uninstall": "PASS", "reinstall": "PASS"},
                },
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        self.matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        self.positive_checks = {
            "visible_first_configuration_completion": "PASS",
            "desktop_launch": "PASS",
            "real_model_conversation": "PASS",
            "attachment_flow": "PASS",
            "diagnostic_export": "PASS",
            "install": "PASS",
            "uninstall": "PASS",
            "reinstall": "PASS",
            "window_close_exit": "PASS",
        }
        self._write_records()
        self.output = self.root / "certification"

    def tearDown(self):
        self.temporary.cleanup()

    def _write_records(self):
        for category in self.matrix["positive_categories"]:
            self._write_record(category["id"], "positive", "COMPATIBLE", self.positive_checks)
        for category in self.matrix["negative_boundaries"]:
            self._write_record(
                category["id"],
                "negative",
                "BLOCKED",
                {"preflight": "BLOCKED"},
                security_facts={"business_data_mutation": False},
                os_id="debian",
                desktop_environment="none",
            )

    def _write_record(
        self,
        category_id,
        category_kind,
        compatibility,
        checks,
        *,
        security_facts=None,
        os_id="kylin",
        desktop_environment="UKUI",
    ):
        directory = self.records / category_id
        directory.mkdir(mode=0o700, exist_ok=True)
        record = {
            "schema": "taiji-linux-environment-evidence/v1",
            "category_id": category_id,
            "category_kind": category_kind,
            "compatibility": compatibility,
            "source_commit": self.source_commit,
            "version": self.version,
            "architecture": "amd64",
            "deb_basename": self.deb.name,
            "deb_sha256": self.deb_sha,
            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
            "compatibility_policy_sha256": self.policy_sha,
            "os_id": os_id,
            "os_version": "V10",
            "desktop_environment": desktop_environment,
            "security_facts": security_facts or {"business_data_mutation": False, "graphical_desktop": True},
            "checks": checks,
            "attachments": [],
        }
        (directory / "environment-evidence.json").write_text(
            json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
        )

    def command(self, *extra):
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--matrix", str(MATRIX),
                "--records-dir", str(self.records),
                "--offline-evidence", str(self.offline),
                "--deb", str(self.deb),
                "--policy", str(self.policy),
                "--output", str(self.output),
                "--challenge", "c" * 64,
                *extra,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_assembles_canonical_set_and_promotes_positive_results(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.output / "certification-set.json"
        self.assertTrue(output.is_file())
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "taiji-linux-certification-set/v1")
        self.assertEqual(payload["source_commit"], self.source_commit)
        self.assertEqual(payload["deb_sha256"], self.deb_sha)
        self.assertEqual(len(payload["environments"]), 6)
        self.assertTrue(all(item["compatibility"] == "CERTIFIED" for item in payload["environments"]))
        self.assertEqual(len(payload["negative_boundaries"]), 6)
        self.assertNotEqual(self.deb.read_bytes(), b"")

    def test_release_validator_exposes_and_accepts_certification_set_v1_contract(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        validator_path = ROOT / "scripts/validate-taiji-release-evidence.py"
        spec = importlib.util.spec_from_file_location("taiji_release_validator_certification_test", validator_path)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        data = json.loads((self.output / "certification-set.json").read_text(encoding="utf-8"))
        binding = validator.BuildBinding(
            source_commit=self.source_commit,
            version=self.version,
            architecture="amd64",
            deb_basename=self.deb.name,
            deb_sha256=self.deb_sha,
            compatibility_policy_id="taiji-linux-amd64-deb-v1",
            compatibility_policy_sha256=self.policy_sha,
            electron_executable_sha256="e" * 64,
            desktop_entry_sha256="f" * 64,
        )
        args = SimpleNamespace(challenge="c" * 64, matrix=MATRIX)
        with patch.object(validator, "canonical_policy_identity", return_value=("taiji-linux-amd64-deb-v1", self.policy_sha)):
            validator.validate_certification_set_v1(
                data,
                self.output / "certification-set.json",
                args,
                binding,
            )

    def test_missing_or_duplicate_category_is_rejected(self):
        (self.records / "openkylin-current" / "environment-evidence.json").unlink()
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("category", result.stderr.lower())
        self._write_record("openkylin-current", "positive", "COMPATIBLE", self.positive_checks)
        duplicate = self.records / "openkylin-current" / "duplicate.json"
        duplicate.write_text("{}\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly", result.stderr.lower())

    def test_mixed_deb_hash_and_binding_drift_are_rejected(self):
        path = self.records / "kylin-min-ukui" / "environment-evidence.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["deb_sha256"] = "d" * 64
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEB", result.stderr)

    def test_unknown_fields_positive_nonpass_and_missing_negative_boundary_fail(self):
        path = self.records / "kylin-min-ukui" / "environment-evidence.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["unknown"] = True
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("field", result.stderr.lower())
        path.unlink()
        self._write_record("kylin-min-ukui", "positive", "COMPATIBLE", {"install": "FAIL"})
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PASS", result.stderr)

    def test_path_escape_symlink_hardlink_attachment_and_existing_output_fail(self):
        path = self.records / "kylin-min-ukui" / "environment-evidence.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["attachments"] = [{"basename": "../escape.txt", "sha256": "a" * 64}]
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("attachment", result.stderr.lower())
        path.unlink()
        path.symlink_to(self.offline)
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        path.unlink()
        path.write_text("{}\n", encoding="utf-8")
        hardlink = self.records / "kylin-min-ukui" / "hardlink.json"
        hardlink.hardlink_to(path)
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly", result.stderr.lower())
        hardlink.unlink()
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.output.exists() is False or not (self.output / "certification-set.json").exists())

    def test_noncanonical_output_or_overwrite_is_rejected(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        second = self.command()
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("overwrite", second.stderr.lower())
        data = json.loads((self.output / "certification-set.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data), {
            "schema", "generated_at_utc", "challenge_nonce", "source_commit", "version",
            "architecture", "deb_basename", "deb_sha256", "compatibility_policy_id",
            "compatibility_policy_sha256", "certification_profile", "offline_rehearsal",
            "environments", "negative_boundaries",
        })


if __name__ == "__main__":
    unittest.main()
