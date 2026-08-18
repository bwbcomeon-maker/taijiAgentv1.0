"""Contract tests for representative Linux certification categories and records."""

from __future__ import annotations

import copy
import hashlib
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
NEGATIVE_ERROR_CODES = {
    "arm-blocked": "TAIJI-LINUX-E001-ARCH",
    "rpm-only-blocked": "TAIJI-LINUX-E003-DPKG",
    "glibc-below-min-blocked": "TAIJI-LINUX-E004-GLIBC",
    "missing-core-capability-blocked": "TAIJI-LINUX-E014-RUNTIME",
    "no-admin-blocked": "TAIJI-LINUX-E010-PRIVILEGE",
    "no-graphical-desktop-blocked": "TAIJI-LINUX-E006-DESKTOP",
}
BUSINESS_CHECKS = {
    "visible_first_configuration_completion",
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "diagnostic_export",
    "model_configuration_state_consistent",
}
LIFECYCLE_CHECKS = {
    "install",
    "window_close_exit",
    "three_restart_cycles",
    "second_instance_focus",
    "no_new_electron_core",
}
POSITIVE_CHECK_RESULTS = {
    "preflight": "PASS",
    **{key: "PASS" for key in BUSINESS_CHECKS | LIFECYCLE_CHECKS},
}
PLATFORM_PROFILES = {
    "kylin-min-ukui": {
        "os_id": "kylin",
        "version_id": "v10",
        "release_id_pattern": r"2403",
        "desktop_environments": ["UKUI"],
        "security_profile": "supported-default",
    },
    "kylin-current-standard": {
        "os_id": "kylin",
        "version_id": "v10",
        "release_id_pattern": r"2503",
        "desktop_environments": ["UKUI"],
        "security_profile": "supported-default",
    },
    "kylin-hardened": {
        "os_id": "kylin",
        "version_id": "v10",
        "release_id_pattern": r"2503",
        "desktop_environments": ["UKUI"],
        "security_profile": "kysec-enabled-exec-control-off",
    },
    "uos-min-dde": {
        "os_id": "uos",
        "version_id": "20",
        "release_id_pattern": r"1070(?:u2)?",
        "desktop_environments": ["DDE"],
        "security_profile": "supported-default",
    },
    "uos-current-or-hardened": {
        "os_id": "uos",
        "version_id": "25",
        "release_id_pattern": r"25[0-9A-Za-z._-]*",
        "desktop_environments": ["DDE"],
        "security_profile": "supported-default",
    },
    "openkylin-current": {
        "os_id": "openkylin",
        "version_id": "2.0",
        "release_id_pattern": r"2\.0-SP2",
        "desktop_environments": ["UKUI", "GNOME"],
        "security_profile": "supported-default",
    },
}
POSITIVE_ATTACHMENTS = [
    {"basename": basename, "sha256": format(index + 1, "064x")}
    for index, basename in enumerate(
        sorted(
            {
                "target-verification.json",
                "environment-observation.json",
                "single-deb-install-observation.json",
                "single-deb-install-method-attestation.json",
                "single-deb-graphical-installer.png",
                "desktop-driver-result.json",
                "desktop-app.png",
                "taiji-support-bundle.json",
            }
        )
    )
]
VERIFICATION_IDENTITY = {
    "challenge_nonce": "d" * 64,
    "acceptance_session_id": "e" * 32,
    "machine_fingerprint_sha256": "f" * 64,
}
POSITIVE_MACHINE_COMMITMENT = hashlib.sha256(
    b"taiji-machine-identity-v1\0matrix-contract-machine"
).hexdigest()
POSITIVE_VERIFICATION_IDENTITY = {
    **VERIFICATION_IDENTITY,
    "machine_identity_commitment_sha256": POSITIVE_MACHINE_COMMITMENT,
    "machine_fingerprint_sha256": hashlib.sha256(
        ("d" * 64 + "\0" + POSITIVE_MACHINE_COMMITMENT).encode("utf-8")
    ).hexdigest(),
}


def positive_security_facts(profile="supported-default", hardened=False):
    return {
        "administrator_available": True,
        "business_data_mutation": False,
        "graphical_desktop": True,
        "network_observation": "continuous-process-sampling-no-non-loopback-up",
        "package_manager": "dpkg",
        "security_profile": profile,
        "kysec_detected": hardened,
        "kysec_enabled": hardened,
        "kysec_exec_control": "off" if hardened else "not-present",
        "os_release_sha256": "d" * 64,
        "os_version_sha256": "not-present",
    }


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
        self.assertEqual(self.matrix["schema"], "taiji-linux-certification-matrix/v2")
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
            self.assertEqual(item["stable_error_code"], NEGATIVE_ERROR_CODES[item["id"]])

    def test_each_positive_category_requires_full_business_and_lifecycle_checks(self):
        for category in self.matrix["positive_categories"]:
            with self.subTest(category=category["id"]):
                self.assertEqual(set(category["required_business_checks"]), BUSINESS_CHECKS)
                self.assertEqual(set(category["required_lifecycle_checks"]), LIFECYCLE_CHECKS)
                self.assertEqual(category["expected_compatibility"], "COMPATIBLE")

    def test_each_positive_category_has_one_exact_executable_platform_profile(self):
        for category in self.matrix["positive_categories"]:
            with self.subTest(category=category["id"]):
                self.assertEqual(category["platform_profile"], PLATFORM_PROFILES[category["id"]])
                self.assertEqual(category["os_ids"], [category["platform_profile"]["os_id"]])
                self.assertEqual(
                    category["desktop_environments"],
                    category["platform_profile"]["desktop_environments"],
                )

    def test_matrix_rejects_unknown_category_fields_and_weak_platform_constraints(self):
        unknown = copy.deepcopy(self.matrix)
        unknown["positive_categories"][0]["not_a_contract_field"] = True
        with self.assertRaisesRegex(self.assembler.AssemblyError, "field set|unknown"):
            self.assembler.validate_certification_matrix(unknown)

        weak = copy.deepcopy(self.matrix)
        weak["positive_categories"][0]["platform_profile"]["release_id_pattern"] = ".*"
        with self.assertRaisesRegex(self.assembler.AssemblyError, "release|platform"):
            self.assembler.validate_certification_matrix(weak)

    def test_each_record_binds_source_deb_policy_and_category(self):
        record = {
            "schema": "taiji-linux-environment-evidence/v2",
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
            "os_version": "v10/2503",
            "desktop_environment": "UKUI",
            "security_facts": positive_security_facts(),
            "checks": dict(POSITIVE_CHECK_RESULTS),
            "attachments": copy.deepcopy(POSITIVE_ATTACHMENTS),
            **POSITIVE_VERIFICATION_IDENTITY,
        }
        validated = self.assembler.validate_environment_record(record, self.matrix)
        self.assertEqual(validated["category_id"], "kylin-current-standard")
        self.assertNotIn("CERTIFIED", json.dumps(validated))

    def test_positive_record_must_match_category_os_version_desktop_and_security_profile(self):
        base = {
            "schema": "taiji-linux-environment-evidence/v2",
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
            "os_version": "v10/2503",
            "desktop_environment": "UKUI",
            "security_facts": positive_security_facts(),
            "checks": dict(POSITIVE_CHECK_RESULTS),
            "attachments": copy.deepcopy(POSITIVE_ATTACHMENTS),
            **POSITIVE_VERIFICATION_IDENTITY,
        }
        mutations = {
            "wrong OS": ("os_id", "uos"),
            "wrong release": ("os_version", "v10/2403"),
            "wrong desktop": ("desktop_environment", "DDE"),
        }
        for label, (key, value) in mutations.items():
            with self.subTest(case=label):
                candidate = copy.deepcopy(base)
                candidate[key] = value
                with self.assertRaisesRegex(self.assembler.AssemblyError, "platform|OS|version|desktop"):
                    self.assembler.validate_environment_record(candidate, self.matrix)

        hardened = copy.deepcopy(base)
        hardened["category_id"] = "kylin-hardened"
        with self.assertRaisesRegex(self.assembler.AssemblyError, "security"):
            self.assembler.validate_environment_record(hardened, self.matrix)

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
        release_ids = {
            "kylin-min-ukui": "2403",
            "kylin-current-standard": "2503",
            "kylin-hardened": "2503",
            "uos-min-dde": "1070u2",
            "uos-current-or-hardened": "25",
            "openkylin-current": "2.0-SP2",
        }
        for category_id in POSITIVE_IDS:
            profile = PLATFORM_PROFILES[category_id]
            security_facts = positive_security_facts(
                profile["security_profile"],
                hardened=category_id == "kylin-hardened",
            )
            records.append(
                {
                    "schema": "taiji-linux-environment-evidence/v2",
                    "category_id": category_id,
                    "category_kind": "positive",
                    "compatibility": "COMPATIBLE",
                    "source_commit": "a" * 40,
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "deb_basename": "taiji-agent_1.2.3_amd64.deb",
                    "deb_sha256": ("b" if len(records) == 0 else "d") * 64,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "c" * 64,
                    "os_id": profile["os_id"],
                    "os_version": f'{profile["version_id"]}/{release_ids[category_id]}',
                    "desktop_environment": profile["desktop_environments"][0],
                    "security_facts": security_facts,
                    "checks": dict(POSITIVE_CHECK_RESULTS),
                    "attachments": copy.deepcopy(POSITIVE_ATTACHMENTS),
                    **POSITIVE_VERIFICATION_IDENTITY,
                }
            )
        with self.assertRaisesRegex(self.assembler.AssemblyError, "DEB hash"):
            self.assembler.validate_environment_records(records, self.matrix)

    def test_full_positive_matrix_requires_distinct_machine_and_session_evidence(self):
        records = []
        for category in self.matrix["positive_categories"]:
            profile = category["platform_profile"]
            release = {
                "kylin-min-ukui": "2403",
                "kylin-current-standard": "2503",
                "kylin-hardened": "2503",
                "uos-min-dde": "1070u2",
                "uos-current-or-hardened": "25",
                "openkylin-current": "2.0-SP2",
            }[category["id"]]
            security_facts = positive_security_facts(
                profile["security_profile"],
                hardened=category["id"] == "kylin-hardened",
            )
            commitment = hashlib.sha256(
                ("taiji-machine-identity-v1\0" + category["id"]).encode("utf-8")
            ).hexdigest()
            records.append(
                {
                    "schema": "taiji-linux-environment-evidence/v2",
                    "category_id": category["id"],
                    "category_kind": "positive",
                    "compatibility": "COMPATIBLE",
                    "source_commit": "a" * 40,
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "deb_basename": "taiji-agent_1.2.3_amd64.deb",
                    "deb_sha256": "b" * 64,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "c" * 64,
                    "os_id": profile["os_id"],
                    "os_version": f'{profile["version_id"]}/{release}',
                    "desktop_environment": profile["desktop_environments"][0],
                    "security_facts": security_facts,
                    "checks": dict(POSITIVE_CHECK_RESULTS),
                    "attachments": copy.deepcopy(POSITIVE_ATTACHMENTS),
                    "challenge_nonce": "d" * 64,
                    "acceptance_session_id": format(len(records) + 1, "032x"),
                    "machine_identity_commitment_sha256": commitment,
                    "machine_fingerprint_sha256": hashlib.sha256(
                        ("d" * 64 + "\0" + commitment).encode("utf-8")
                    ).hexdigest(),
                }
            )

        self.assertEqual(len(self.assembler.validate_environment_records(records, self.matrix)), 6)

        duplicate_machine = copy.deepcopy(records)
        duplicate_machine[1]["machine_identity_commitment_sha256"] = duplicate_machine[0][
            "machine_identity_commitment_sha256"
        ]
        duplicate_machine[1]["machine_fingerprint_sha256"] = hashlib.sha256(
            (
                duplicate_machine[1]["challenge_nonce"]
                + "\0"
                + duplicate_machine[1]["machine_identity_commitment_sha256"]
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(self.assembler.AssemblyError, "machine|commitment"):
            self.assembler.validate_environment_records(duplicate_machine, self.matrix)

        duplicate_session = copy.deepcopy(records)
        duplicate_session[1]["acceptance_session_id"] = duplicate_session[0]["acceptance_session_id"]
        with self.assertRaisesRegex(self.assembler.AssemblyError, "session"):
            self.assembler.validate_environment_records(duplicate_session, self.matrix)

    def test_negative_samples_block_before_business_data_mutation(self):
        records = []
        for category in NEGATIVE_IDS:
            boundary = next(
                item for item in self.matrix["negative_boundaries"] if item["id"] == category
            )
            preflight_sha = format(len(records) + 21, "064x")
            inventory_sha = format(len(records) + 31, "064x")
            records.append(
                {
                    "schema": "taiji-linux-environment-evidence/v2",
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
                    "security_facts": {
                        "business_data_mutation": False,
                        "business_data_before_sha256": "1" * 64,
                        "business_data_after_sha256": "1" * 64,
                        "business_data_scope_id": "taiji-user-and-install-state-v1",
                        "business_data_inventory_sha256": inventory_sha,
                        "boundary": boundary["boundary"],
                        "observed_value": "controlled-negative-value",
                        "stable_error_code": boundary["stable_error_code"],
                        "execution_environment": "controlled-root-fixture-v1",
                        "preflight_result_sha256": preflight_sha,
                    },
                    "checks": {"preflight": "BLOCKED"},
                    "attachments": [
                        {"basename": "preflight-result.json", "sha256": preflight_sha},
                        {"basename": "business-data-inventory.json", "sha256": inventory_sha},
                    ],
                    **VERIFICATION_IDENTITY,
                }
            )
        validated = self.assembler.validate_environment_records(records, self.matrix)
        self.assertEqual(len(validated), 6)

    def test_negative_record_requires_stable_code_unchanged_business_data_and_raw_preflight(self):
        category = self.matrix["negative_boundaries"][0]
        record = {
            "schema": "taiji-linux-environment-evidence/v2",
            "category_id": category["id"],
            "category_kind": "negative",
            "compatibility": "BLOCKED",
            "source_commit": "a" * 40,
            "version": "1.2.3",
            "architecture": "amd64",
            "deb_basename": "taiji-agent_1.2.3_amd64.deb",
            "deb_sha256": "b" * 64,
            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
            "compatibility_policy_sha256": "c" * 64,
            "os_id": "kylin",
            "os_version": "controlled-negative-fixture-v1",
            "desktop_environment": "UKUI",
            "security_facts": {"business_data_mutation": False},
            "checks": {"preflight": "BLOCKED"},
            "attachments": [],
            **VERIFICATION_IDENTITY,
        }

        with self.assertRaisesRegex(self.assembler.AssemblyError, "negative.*security"):
            self.assembler.validate_environment_record(record, self.matrix)

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
