"""Contract tests for representative Linux certification categories and records."""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "packaging/linux/certification-matrix.json"
ASSEMBLER_PATH = ROOT / "tools/taiji-desktop-acceptance/assemble-target-evidence.py"

POSITIVE_IDS = {
    "kylin-min-ukui",
    "kylin-current-standard",
    "kylin-hardened",
    "uos-min-dde",
    "uos-current-or-hardened",
    "openkylin-current",
}
NEGATIVE_IDS = {
    "arm-blocked",
    "rpm-only-blocked",
    "glibc-below-min-blocked",
    "missing-core-capability-blocked",
    "no-admin-blocked",
    "no-graphical-desktop-blocked",
}
BUSINESS_CHECKS = {
    "visible_first_configuration_completion",
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "diagnostic_export",
}
LIFECYCLE_CHECKS = {"install", "uninstall", "reinstall", "window_close_exit"}


def load_assembler():
    spec = importlib.util.spec_from_file_location("taiji_target_evidence_assembler_matrix_test", ASSEMBLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load target evidence assembler")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CertificationMatrixContractTest(unittest.TestCase):
    def setUp(self):
        self.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assembler = load_assembler()

    def test_matrix_has_exact_six_positive_categories_and_required_negative_boundaries(self):
        self.assertEqual(self.matrix["schema"], "taiji-linux-certification-matrix/v1")
        self.assertEqual(
            {item["id"] for item in self.matrix["positive_categories"]}, POSITIVE_IDS
        )
        self.assertEqual(
            {item["id"] for item in self.matrix["negative_boundaries"]}, NEGATIVE_IDS
        )
        self.assertEqual(len(self.matrix["positive_categories"]), 6)
        self.assertEqual(len(self.matrix["negative_boundaries"]), 6)
        for item in self.matrix["negative_boundaries"]:
            self.assertEqual(item["expected_compatibility"], "BLOCKED")
            self.assertTrue(item["block_before_business_data_mutation"])

    def test_each_positive_category_requires_full_business_and_lifecycle_checks(self):
        for category in self.matrix["positive_categories"]:
            with self.subTest(category=category["id"]):
                self.assertEqual(set(category["required_business_checks"]), BUSINESS_CHECKS)
                self.assertEqual(set(category["required_lifecycle_checks"]), LIFECYCLE_CHECKS)
                self.assertEqual(category["expected_compatibility"], "COMPATIBLE")

    def test_each_record_binds_source_deb_policy_and_category(self):
        record = {
            "schema": "taiji-linux-environment-evidence/v1",
            "category_id": "kylin-current-standard",
            "category_kind": "positive",
            "compatibility": "COMPATIBLE",
            "source_commit": "a" * 40,
            "version": "1.2.3",
            "architecture": "amd64",
            "deb_basename": "taiji-agent_1.2.3_amd64.deb",
            "deb_sha256": "b" * 64,
            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
            "compatibility_policy_sha256": "c" * 64,
            "os_id": "kylin",
            "os_version": "V10",
            "desktop_environment": "UKUI",
            "security_facts": {"graphical_desktop": True, "administrator_available": True},
            "checks": {"install": "PASS", "desktop_launch": "PASS"},
            "attachments": [],
        }
        validated = self.assembler.validate_environment_record(record, self.matrix)
        self.assertEqual(validated["category_id"], "kylin-current-standard")
        self.assertNotIn("CERTIFIED", json.dumps(validated))

    def test_records_never_self_claim_certified(self):
        record = {
            "schema": "taiji-linux-environment-evidence/v1",
            "category_id": "kylin-current-standard",
            "category_kind": "positive",
            "compatibility": "CERTIFIED",
        }
        with self.assertRaisesRegex(self.assembler.AssemblyError, "CERTIFIED"):
            self.assembler.validate_environment_record(record, self.matrix)

    def test_matrix_rejects_duplicate_category_or_mixed_deb_hash(self):
        duplicate = copy.deepcopy(self.matrix)
        duplicate["positive_categories"].append(copy.deepcopy(duplicate["positive_categories"][0]))
        with self.assertRaisesRegex(self.assembler.AssemblyError, "duplicate"):
            self.assembler.validate_certification_matrix(duplicate)

        records = []
        for category in POSITIVE_IDS:
            records.append(
                {
                    "schema": "taiji-linux-environment-evidence/v1",
                    "category_id": category,
                    "category_kind": "positive",
                    "compatibility": "COMPATIBLE",
                    "source_commit": "a" * 40,
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "deb_basename": "taiji-agent_1.2.3_amd64.deb",
                    "deb_sha256": ("b" if len(records) == 0 else "d") * 64,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "c" * 64,
                    "os_id": "kylin",
                    "os_version": "V10",
                    "desktop_environment": "UKUI",
                    "security_facts": {},
                    "checks": {},
                    "attachments": [],
                }
            )
        with self.assertRaisesRegex(self.assembler.AssemblyError, "DEB hash"):
            self.assembler.validate_environment_records(records, self.matrix)

    def test_negative_samples_block_before_business_data_mutation(self):
        records = []
        for category in NEGATIVE_IDS:
            records.append(
                {
                    "schema": "taiji-linux-environment-evidence/v1",
                    "category_id": category,
                    "category_kind": "negative",
                    "compatibility": "BLOCKED",
                    "source_commit": "a" * 40,
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "deb_basename": "taiji-agent_1.2.3_amd64.deb",
                    "deb_sha256": "b" * 64,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "c" * 64,
                    "os_id": "debian",
                    "os_version": "12",
                    "desktop_environment": "none",
                    "security_facts": {"business_data_mutation": False},
                    "checks": {"preflight": "BLOCKED"},
                    "attachments": [],
                }
            )
        validated = self.assembler.validate_environment_records(records, self.matrix)
        self.assertEqual(len(validated), 6)

    def test_full_matrix_is_required_for_runtime_policy_or_lifecycle_changes(self):
        self.assertEqual(
            self.matrix["coverage_rules"]["runtime_policy_or_lifecycle_change"], "all-positive-and-negative"
        )
        self.assertEqual(
            self.matrix["coverage_rules"]["application_only_change"], "three-family-core-path"
        )

    def test_three_family_core_path_is_minimum_for_application_only_change(self):
        self.assertEqual(
            set(self.matrix["minimum_application_only_categories"]),
            {"kylin-current-standard", "uos-current-or-hardened", "openkylin-current"},
        )


if __name__ == "__main__":
    unittest.main()
