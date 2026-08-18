import copy
import hashlib
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER_PATH = ROOT / "tools/taiji-desktop-acceptance/assemble-target-evidence.py"
SPEC = importlib.util.spec_from_file_location("taiji_target_evidence_v2_contract", ASSEMBLER_PATH)
assert SPEC is not None and SPEC.loader is not None
ASSEMBLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ASSEMBLER)


CHALLENGE = "a" * 64
TEST_IDENTITY = "target-evidence-contract-machine"


def valid_driver() -> dict:
    checks = {
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
    rounds = []
    for round_number in range(1, 4):
        rounds.append(
            {
                "round": round_number,
                "ready": True,
                "electron_pid": 4100 + round_number,
                "agent_pid": 4200 + round_number,
                "web_pid": 4300 + round_number,
                "secondary_pid": 4400 + round_number,
                "cdp_port": 15000 + round_number,
                "webui_port": 18000 + round_number,
                "second_instance_exit_code": 0,
                "electron_exit_code": 0,
                "restored_and_focused": True,
                "page_close_sent": True,
                "process_identities_gone": {
                    "electron": True,
                    "agent": True,
                    "webui": True,
                    "secondary": True,
                },
                "ports_closed": {"cdp": True, "webui": True},
                "pidfiles_absent": True,
                "model_config_observed": True,
                "profile_continuity_observed": True,
            }
        )
    return {
        "schema": "taiji.desktop.acceptance-driver.v2",
        "acceptance_session_id": "b" * 32,
        "challenge_nonce": CHALLENGE,
        "electron_pid": rounds[0]["electron_pid"],
        "electron_executable": ASSEMBLER.ELECTRON_PATH,
        "electron_executable_sha256": "c" * 64,
        "desktop_entry_sha256": "d" * 64,
        "app_url": "http://127.0.0.1:18001/?taiji_desktop=1",
        "webui_origin": "http://127.0.0.1:18001",
        "desktop_auth_cookie": {
            "name": "taiji_desktop_token",
            "present": True,
            "http_only": True,
            "same_site": "Strict",
            "path": "/",
            "value_format": "lowercase-hex-64",
        },
        "model": "deepseek-chat",
        "attachment_probe_sha256": "e" * 64,
        "agent_pid": rounds[0]["agent_pid"],
        "web_pid": rounds[0]["web_pid"],
        "screenshot_basename": "desktop-app.png",
        "diagnostic_basename": "taiji-support-bundle.json",
        "restart_rounds": rounds,
        "persistent_user_data": {
            "mode": "electron-default-persistent",
            "restart_rounds": 3,
            "user_data_override": False,
            "profile_reset": False,
            "environment_reused": True,
            "continuity_observed_rounds": 3,
            "continuity_token": "f" * 64,
        },
        "core_observation": {
            "status": "verified",
            "mechanism": "journalctl-json-user-electron",
            "baseline_entry_count": 0,
            "baseline_cursor_set_token": "1" * 64,
            "rounds": [
                {
                    "round": round_number,
                    "status": "verified",
                    "added_entry_count": 0,
                    "cursor_set_token": str(round_number + 1) * 64,
                }
                for round_number in range(1, 4)
            ],
        },
        "model_config_observation": {
            "observed_rounds": 3,
            "consistent": True,
            "public_projection_token": "5" * 64,
        },
        "checks": checks,
        "js_error_count": 0,
        "unexpected_http_failures": 0,
        "electron_exit_code": 0,
    }


class TargetEvidenceV2ContractTest(unittest.TestCase):
    def test_accepts_only_complete_driver_v2(self):
        driver = valid_driver()
        ASSEMBLER.validate_driver_result(driver, CHALLENGE)

        legacy = copy.deepcopy(driver)
        legacy["schema"] = "taiji.desktop.acceptance-driver.v1"
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "schema"):
            ASSEMBLER.validate_driver_result(legacy, CHALLENGE)

    def test_rejects_each_restart_and_observation_downgrade(self):
        mutations = []
        driver = valid_driver()
        downgraded_round = copy.deepcopy(driver)
        downgraded_round["restart_rounds"][1]["ports_closed"]["webui"] = False
        mutations.append(downgraded_round)

        downgraded_profile = copy.deepcopy(driver)
        downgraded_profile["persistent_user_data"]["continuity_observed_rounds"] = 2
        mutations.append(downgraded_profile)

        downgraded_core = copy.deepcopy(driver)
        downgraded_core["core_observation"]["status"] = "unverified"
        mutations.append(downgraded_core)

        downgraded_model = copy.deepcopy(driver)
        downgraded_model["model_config_observation"]["consistent"] = False
        mutations.append(downgraded_model)

        downgraded_check = copy.deepcopy(driver)
        downgraded_check["checks"]["second_instance_focus"] = False
        mutations.append(downgraded_check)

        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ASSEMBLER.AssemblyError):
                    ASSEMBLER.validate_driver_result(candidate, CHALLENGE)

    def test_canonical_target_schema_is_v2(self):
        self.assertEqual(
            ASSEMBLER.CANONICAL_TARGET_EVIDENCE_SCHEMA,
            "taiji-linux-target-verification/v2",
        )
        self.assertIn(
            "machine_identity_commitment_sha256",
            ASSEMBLER.CANONICAL_TARGET_EVIDENCE_KEYS,
        )

    def test_canonical_observation_is_bound_to_current_machine_commitment(self):
        machine_identity = f"non-linux-contract-test-machine:{TEST_IDENTITY}"
        boot_identity = f"non-linux-contract-test-boot:{TEST_IDENTITY}"
        with patch.object(
            ASSEMBLER,
            "_current_target_identities",
            return_value=(machine_identity, boot_identity),
        ):
            commitment, machine_fingerprint, boot_fingerprint = (
                ASSEMBLER.current_target_identity_binding(CHALLENGE)
            )
            target_uid, home_fingerprint, paths_fingerprint = (
                ASSEMBLER.current_user_context_fingerprints(CHALLENGE)
            )
            expected_commitment = hashlib.sha256(
                ("taiji-machine-identity-v1\0" + machine_identity).encode("utf-8")
            ).hexdigest()
            self.assertEqual(commitment, expected_commitment)
            self.assertEqual(
                machine_fingerprint,
                hashlib.sha256(
                    (CHALLENGE + "\0" + expected_commitment).encode("utf-8")
                ).hexdigest(),
            )

            observation = {
                "schema": "taiji.single-deb-install-observation/v2",
                "generated_at_utc": "2026-08-11T00:00:02Z",
                "started_at_utc": "2026-08-11T00:00:00Z",
                "completed_at_utc": "2026-08-11T00:00:01Z",
                "challenge_nonce": CHALLENGE,
                "machine_identity_commitment_sha256": commitment,
                "machine_fingerprint_sha256": machine_fingerprint,
                "boot_fingerprint_sha256": boot_fingerprint,
                "target_uid": target_uid,
                "canonical_home_fingerprint_sha256": home_fingerprint,
                "user_state_paths_fingerprint_sha256": paths_fingerprint,
                "source_commit": "b" * 40,
                "manifest_sha256": "c" * 64,
                "deb_observed_basename": "taiji-agent_1.2.3_amd64.deb",
                "deb_sha256": "d" * 64,
                "candidate_file_count": 1,
                "additional_install_files_observed": False,
                "package_status_before": "not-installed",
                "package_status_after": "install ok installed",
                "package_status_transitions": [
                    "not-installed",
                    "install ok installed",
                ],
                "network_observation": "continuous-process-sampling-no-non-loopback-up",
                "network_sample_interval_ms": 250,
                "network_sample_count": 2,
                "user_state_before": "absent",
                "user_state_after_install_before_first_launch": "absent",
                "first_launch_eligible": True,
                "installation_method_machine_observed": False,
                "observation_process_continuous": True,
            }
            validated = ASSEMBLER.validate_canonical_install_observation(
                observation,
                challenge=CHALLENGE,
                manifest_sha256="c" * 64,
                deb=Path("/tmp/taiji-agent_1.2.3_amd64.deb"),
                deb_sha256="d" * 64,
                source_commit="b" * 40,
            )
            self.assertEqual(validated, (commitment, machine_fingerprint))

            old_positive_v2 = copy.deepcopy(observation)
            old_positive_v2.pop("machine_identity_commitment_sha256")
            with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "field|commitment"):
                ASSEMBLER.validate_canonical_install_observation(
                    old_positive_v2,
                    challenge=CHALLENGE,
                    manifest_sha256="c" * 64,
                    deb=Path("/tmp/taiji-agent_1.2.3_amd64.deb"),
                    deb_sha256="d" * 64,
                    source_commit="b" * 40,
                )

            forged = copy.deepcopy(observation)
            forged["machine_identity_commitment_sha256"] = "e" * 64
            forged["machine_fingerprint_sha256"] = hashlib.sha256(
                (
                    CHALLENGE
                    + "\0"
                    + forged["machine_identity_commitment_sha256"]
                ).encode("utf-8")
            ).hexdigest()
            with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "current target|commitment"):
                ASSEMBLER.validate_canonical_install_observation(
                    forged,
                    challenge=CHALLENGE,
                    manifest_sha256="c" * 64,
                    deb=Path("/tmp/taiji-agent_1.2.3_amd64.deb"),
                    deb_sha256="d" * 64,
                    source_commit="b" * 40,
                )


if __name__ == "__main__":
    unittest.main()
