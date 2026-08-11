import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER_PATH = ROOT / "tools/taiji-desktop-acceptance/assemble-target-evidence.py"
MATRIX_PATH = ROOT / "packaging/linux/certification-matrix.json"
SPEC = importlib.util.spec_from_file_location("taiji_environment_evidence_v2", ASSEMBLER_PATH)
assert SPEC is not None and SPEC.loader is not None
ASSEMBLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ASSEMBLER)

CHALLENGE = "d" * 64
MACHINE_COMMITMENT = hashlib.sha256(
    ("taiji-machine-identity-v1\0" + "fixture-machine-one").encode("utf-8")
).hexdigest()


def machine_fingerprint(challenge: str, commitment: str) -> str:
    return hashlib.sha256((challenge + "\0" + commitment).encode("utf-8")).hexdigest()


def observation() -> dict:
    return {
        "schema": "taiji-linux-environment-observation/v1",
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
        "machine_identity_commitment_sha256": MACHINE_COMMITMENT,
        "os_id": "kylin",
        "os_version": "v10/2503",
        "desktop_environment": "UKUI",
        "security_facts": {
            "administrator_available": True,
            "business_data_mutation": False,
            "graphical_desktop": True,
            "network_observation": "continuous-process-sampling-no-non-loopback-up",
            "package_manager": "dpkg",
            "security_profile": "supported-default",
            "kysec_detected": False,
            "kysec_enabled": False,
            "kysec_exec_control": "not-present",
            "os_release_sha256": "d" * 64,
            "os_version_sha256": "not-present",
        },
        "checks": {"preflight": "PASS", "install": "PASS"},
        "attachments": [],
    }


def driver_checks() -> dict:
    return {
        "visible_first_configuration_completion": True,
        "desktop_launch": True,
        "real_model_conversation": True,
        "attachment_flow": True,
        "window_close_exit": True,
        "diagnostic_export": True,
        "three_restart_cycles": True,
        "second_instance_focus": True,
        "model_configuration_state_consistent": True,
        "no_new_electron_core": True,
    }


def attachment_hashes() -> dict[str, str]:
    basenames = {
        "target-verification.json",
        "environment-observation.json",
        "single-deb-install-observation.json",
        "single-deb-install-method-attestation.json",
        "single-deb-graphical-installer.png",
        "desktop-driver-result.json",
        "desktop-app.png",
        "taiji-support-bundle.json",
    }
    return {name: format(index + 1, "064x") for index, name in enumerate(sorted(basenames))}


class EnvironmentEvidenceV2ContractTest(unittest.TestCase):
    def setUp(self):
        self.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    def test_seed_observation_is_not_itself_final_certification_evidence(self):
        seed = observation()
        ASSEMBLER.validate_environment_observation(seed, self.matrix)
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "schema|field set"):
            ASSEMBLER.validate_environment_record(seed, self.matrix)

    def test_final_positive_record_binds_target_and_all_driver_checks(self):
        record = ASSEMBLER.build_positive_environment_evidence(
            observation(),
            matrix=self.matrix,
            driver_checks=driver_checks(),
            challenge=CHALLENGE,
            acceptance_session_id="e" * 32,
            attachment_hashes=attachment_hashes(),
        )
        validated = ASSEMBLER.validate_environment_record(record, self.matrix)
        self.assertEqual(validated["schema"], "taiji-linux-environment-evidence/v2")
        self.assertEqual(validated["checks"]["model_configuration_state_consistent"], "PASS")
        self.assertEqual(validated["checks"]["three_restart_cycles"], "PASS")
        self.assertIn(
            "target-verification.json",
            {item["basename"] for item in validated["attachments"]},
        )
        self.assertNotIn("uninstall", validated["checks"])
        self.assertNotIn("reinstall", validated["checks"])
        self.assertEqual(
            validated["machine_fingerprint_sha256"],
            machine_fingerprint(CHALLENGE, MACHINE_COMMITMENT),
        )

    def test_final_positive_record_rejects_missing_target_or_failed_check(self):
        hashes = attachment_hashes()
        hashes.pop("target-verification.json")
        with self.assertRaises(ASSEMBLER.AssemblyError):
            ASSEMBLER.build_positive_environment_evidence(
                observation(),
                matrix=self.matrix,
                driver_checks=driver_checks(),
                challenge=CHALLENGE,
                acceptance_session_id="e" * 32,
                attachment_hashes=hashes,
            )

        failed = driver_checks()
        failed["three_restart_cycles"] = False
        with self.assertRaises(ASSEMBLER.AssemblyError):
            ASSEMBLER.build_positive_environment_evidence(
                observation(),
                matrix=self.matrix,
                driver_checks=failed,
                challenge=CHALLENGE,
                acceptance_session_id="e" * 32,
                attachment_hashes=attachment_hashes(),
            )

    def test_final_record_rejects_attachment_tampering_and_extra_checks(self):
        record = ASSEMBLER.build_positive_environment_evidence(
            observation(),
            matrix=self.matrix,
            driver_checks=driver_checks(),
            challenge=CHALLENGE,
            acceptance_session_id="e" * 32,
            attachment_hashes=attachment_hashes(),
        )
        tampered = copy.deepcopy(record)
        tampered["attachments"][0]["sha256"] = "x" * 64
        with self.assertRaises(ASSEMBLER.AssemblyError):
            ASSEMBLER.validate_environment_record(tampered, self.matrix)
        extra = copy.deepcopy(record)
        extra["checks"]["uninstall"] = "PASS"
        with self.assertRaises(ASSEMBLER.AssemblyError):
            ASSEMBLER.validate_environment_record(extra, self.matrix)

    def test_positive_record_rejects_unknown_or_semantically_unsafe_security_facts(self):
        record = ASSEMBLER.build_positive_environment_evidence(
            observation(),
            matrix=self.matrix,
            driver_checks=driver_checks(),
            challenge=CHALLENGE,
            acceptance_session_id="e" * 32,
            attachment_hashes=attachment_hashes(),
        )

        unknown = copy.deepcopy(record)
        unknown["security_facts"]["operator_claimed_safe"] = True
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "security"):
            ASSEMBLER.validate_environment_record(unknown, self.matrix)

        unsafe = copy.deepcopy(record)
        unsafe["security_facts"].update(
            {
                "kysec_detected": True,
                "kysec_enabled": True,
                "kysec_exec_control": "on",
            }
        )
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "Kysec|security"):
            ASSEMBLER.validate_environment_record(unsafe, self.matrix)

    def test_positive_record_rejects_arbitrary_commitment_or_fingerprint(self):
        record = ASSEMBLER.build_positive_environment_evidence(
            observation(),
            matrix=self.matrix,
            driver_checks=driver_checks(),
            challenge=CHALLENGE,
            acceptance_session_id="e" * 32,
            attachment_hashes=attachment_hashes(),
        )

        old_positive_v2 = copy.deepcopy(record)
        old_positive_v2.pop("machine_identity_commitment_sha256")
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "field|commitment"):
            ASSEMBLER.validate_environment_record(old_positive_v2, self.matrix)

        arbitrary_commitment = copy.deepcopy(record)
        arbitrary_commitment["machine_identity_commitment_sha256"] = "1" * 64
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "commitment|fingerprint"):
            ASSEMBLER.validate_environment_record(arbitrary_commitment, self.matrix)

        arbitrary_fingerprint = copy.deepcopy(record)
        arbitrary_fingerprint["machine_fingerprint_sha256"] = "2" * 64
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "commitment|fingerprint"):
            ASSEMBLER.validate_environment_record(arbitrary_fingerprint, self.matrix)

    def test_same_machine_keeps_commitment_across_challenges(self):
        records = []
        for index, challenge in enumerate(("d" * 64, "e" * 64), start=1):
            records.append(
                ASSEMBLER.build_positive_environment_evidence(
                    observation(),
                    matrix=self.matrix,
                    driver_checks=driver_checks(),
                    challenge=challenge,
                    acceptance_session_id=format(index, "032x"),
                    attachment_hashes=attachment_hashes(),
                )
            )

        self.assertEqual(
            records[0]["machine_identity_commitment_sha256"],
            records[1]["machine_identity_commitment_sha256"],
        )
        self.assertNotEqual(
            records[0]["machine_fingerprint_sha256"],
            records[1]["machine_fingerprint_sha256"],
        )
        for record in records:
            self.assertEqual(
                record["machine_fingerprint_sha256"],
                machine_fingerprint(
                    record["challenge_nonce"],
                    record["machine_identity_commitment_sha256"],
                ),
            )


if __name__ == "__main__":
    unittest.main()
