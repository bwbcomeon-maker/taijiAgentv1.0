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
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/assemble-taiji-certification-set.py"
VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"
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
        self.challenge = "c" * 64
        self.session_id = "b" * 32
        self.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        self.policy = ROOT / "packaging/linux/compatibility-policy.json"
        self.policy_sha = load_script()._policy_identity(self.policy)[1]
        self.offline_log = self.root / "offline-install-rehearsal-session.json"
        self.offline_log.write_text(
            json.dumps(
                {
                    "schema": "taiji.offline-install-rehearsal.v1",
                    "generated_at_utc": self.generated_at,
                    "rehearsal_session_id": self.session_id,
                    "challenge_nonce": self.challenge,
                    "source_commit": self.source_commit,
                    "deb_basename": self.deb.name,
                    "deb_sha256": self.deb_sha,
                    "platform": "linux/amd64",
                    "environment": "container-kylin-policy-fixture-v1",
                    "os_id": "ubuntu",
                    "os_version": "20.04",
                    "network": "none",
                    "checks": {"install": True, "uninstall": True, "reinstall": True},
                    "desktop_app_verified": False,
                    "target_verified": False,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.previous_deb = self.root / "taiji-agent_1.2.2_amd64.deb"
        self.previous_deb.write_bytes(b"immutable-n-minus-one-deb")
        self.previous_deb_sha = hashlib.sha256(self.previous_deb.read_bytes()).hexdigest()
        self.previous_checksum = self.root / f"{self.previous_deb.name}.sha256"
        self.previous_checksum.write_text(
            f"{self.previous_deb_sha}  {self.previous_deb.name}\n",
            encoding="ascii",
        )
        self.previous_manifest = self.root / "previous-release-manifest.json"
        self.previous_manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": "1.2.2",
                    "architecture": "amd64",
                    "source_commit": "8" * 40,
                    "deb_basename": self.previous_deb.name,
                    "deb_sha256": self.previous_deb_sha,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": self.policy_sha,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        stable_data_hash = "7" * 64
        self.offline = self.root / "offline-install-rehearsal.json"
        self.offline.write_text(
            json.dumps(
                {
                    "schema": "taiji.offline-install-rehearsal.v1",
                    "status": "PASS",
                    "generated_at_utc": self.generated_at,
                    "rehearsal_session_id": self.session_id,
                    "challenge_nonce": self.challenge,
                    "source_commit": self.source_commit,
                    "version": self.version,
                    "architecture": "amd64",
                    "deb_basename": self.deb.name,
                    "deb_sha256": self.deb_sha,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": self.policy_sha,
                    "delivery_inventory_sha256": "9" * 64,
                    "platform": "linux/amd64",
                    "environment": "container-kylin-policy-fixture-v1",
                    "os_id": "ubuntu",
                    "os_version": "20.04",
                    "network": "none",
                    "checks": {"install": "PASS", "uninstall": "PASS", "reinstall": "PASS"},
                    "desktop_app_verified": False,
                    "target_verified": False,
                    "log_basename": self.offline_log.name,
                    "log_sha256": hashlib.sha256(self.offline_log.read_bytes()).hexdigest(),
                    "steps": [
                        "fresh_install_n",
                        "same_version_reinstall_n",
                        "seed_n_minus_one",
                        "upgrade_n_minus_one_to_n",
                        "data_manifest_after_upgrade",
                        "inject_postinst_failure_same_candidate",
                        "automatic_rollback_to_n_minus_one",
                        "upgrade_n_again",
                        "remove_preserves_user_data",
                        "purge_clears_root_state_only",
                    ],
                    "receipts": [
                        {
                            "operation": operation,
                            "result": "PASS",
                            "state": "committed",
                            "transaction_id": f"tx-{index}",
                            "deb_sha256": self.deb_sha,
                            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                            "compatibility_policy_sha256": self.policy_sha,
                            "network": "none",
                        }
                        for index, operation in enumerate(
                            ["fresh_install", "reinstall", "upgrade", "rollback", "upgrade_again"],
                            start=1,
                        )
                    ],
                    "data_manifests": {
                        "before_upgrade": stable_data_hash,
                        "after_upgrade": stable_data_hash,
                        "after_rollback": stable_data_hash,
                        "after_remove": stable_data_hash,
                        "after_purge": stable_data_hash,
                    },
                    "journal": {
                        "upgrade_transaction_id": "tx-upgrade",
                        "rollback_transaction_id": "tx-rollback",
                        "second_upgrade_transaction_id": "tx-upgrade-again",
                        "resume": "verified",
                        "power_loss_resume_checked": True,
                        "partial_journal_treated_as_committed": False,
                        "partial_journal_result": "manual_recovery_required",
                        "manual_recovery_required": False,
                    },
                    "package_actions": [
                        {"command": command, "package": "taiji-agent", "network": "none", "download": False}
                        for command in ("dpkg --install", "dpkg --remove", "dpkg --purge")
                    ],
                    "previous_release": {
                        "source_commit": "8" * 40,
                        "version": "1.2.2",
                        "deb_basename": self.previous_deb.name,
                        "deb_sha256": self.previous_deb_sha,
                        "checksum_basename": self.previous_checksum.name,
                        "checksum_sha256": hashlib.sha256(self.previous_checksum.read_bytes()).hexdigest(),
                        "manifest_basename": self.previous_manifest.name,
                        "manifest_sha256": hashlib.sha256(self.previous_manifest.read_bytes()).hexdigest(),
                        "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                        "compatibility_policy_sha256": self.policy_sha,
                    },
                },
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        self.matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        self.positive_checks = {
            "preflight": "PASS",
            "visible_first_configuration_completion": "PASS",
            "desktop_launch": "PASS",
            "real_model_conversation": "PASS",
            "attachment_flow": "PASS",
            "diagnostic_export": "PASS",
            "model_configuration_state_consistent": "PASS",
            "install": "PASS",
            "window_close_exit": "PASS",
            "three_restart_cycles": "PASS",
            "second_instance_focus": "PASS",
            "no_new_electron_core": "PASS",
        }
        self.positive_attachment_payloads = {
            basename: ("evidence:" + basename).encode("utf-8")
            for basename in {
                "target-verification.json",
                "environment-observation.json",
                "single-deb-install-observation.json",
                "single-deb-install-method-attestation.json",
                "single-deb-graphical-installer.png",
                "desktop-driver-result.json",
                "desktop-app.png",
                "taiji-support-bundle.json",
            }
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
        attachments = []
        if category_kind == "positive":
            for basename, payload in sorted(self.positive_attachment_payloads.items()):
                (directory / basename).write_bytes(payload)
                attachments.append(
                    {"basename": basename, "sha256": hashlib.sha256(payload).hexdigest()}
                )
        else:
            boundary = next(
                item for item in self.matrix["negative_boundaries"] if item["id"] == category_id
            )
            preflight_payload = (
                json.dumps(
                    {
                        "schema": "taiji-install-preflight/v1",
                        "status": "BLOCKED",
                        "policy_id": "taiji-linux-amd64-deb-v1",
                        "compatibility_policy_sha256": self.policy_sha,
                        "error_code": boundary["stable_error_code"],
                        "reason_zh": "受控负向边界预检阻断",
                        "failed_capabilities": [boundary["stable_error_code"]],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            (directory / "preflight-result.json").write_bytes(preflight_payload)
            preflight_sha = hashlib.sha256(preflight_payload).hexdigest()
            attachments.append(
                {"basename": "preflight-result.json", "sha256": preflight_sha}
            )
            protected_paths = [
                "home/customer/.config/taiji-agent",
                "home/customer/.config/taiji-agent-desktop",
                "home/customer/.local/share/taiji-agent",
                "home/customer/.local/share/taiji-agent-desktop",
                "home/customer/.local/state/taiji-agent",
                "home/customer/.local/state/taiji-agent-desktop",
                "home/customer/.cache/taiji-agent",
                "home/customer/.cache/taiji-agent-desktop",
                "opt/taiji-agent",
            ]
            inventory_entries = [
                {"path": path, "sha256": hashlib.sha256(path.encode("utf-8")).hexdigest()}
                for path in protected_paths
            ]
            inventory_payload = (
                json.dumps(
                    {
                        "schema": "taiji-business-data-inventory/v1",
                        "scope_id": "taiji-user-and-install-state-v1",
                        "protected_paths": protected_paths,
                        "before": inventory_entries,
                        "after": inventory_entries,
                        "unchanged": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
            inventory_path = directory / "business-data-inventory.json"
            inventory_path.write_bytes(inventory_payload)
            inventory_sha = hashlib.sha256(inventory_payload).hexdigest()
            aggregate_payload = (
                json.dumps(
                    {"entries": inventory_entries},
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
            aggregate_sha = hashlib.sha256(aggregate_payload).hexdigest()
            attachments.append(
                {"basename": inventory_path.name, "sha256": inventory_sha}
            )
            security_facts = {
                "business_data_mutation": False,
                "business_data_before_sha256": aggregate_sha,
                "business_data_after_sha256": aggregate_sha,
                "business_data_scope_id": "taiji-user-and-install-state-v1",
                "business_data_inventory_sha256": inventory_sha,
                "boundary": boundary["boundary"],
                "observed_value": "controlled-negative-value",
                "stable_error_code": boundary["stable_error_code"],
                "execution_environment": "controlled-root-fixture-v1",
                "preflight_result_sha256": preflight_sha,
            }
        acceptance_session_id = self.session_id
        machine_fingerprint_sha256 = "f" * 64
        machine_identity_commitment_sha256 = None
        os_version = "controlled-negative-fixture-v1"
        if category_kind == "positive":
            category = next(
                item for item in self.matrix["positive_categories"] if item["id"] == category_id
            )
            profile = category["platform_profile"]
            release_id = {
                "kylin-min-ukui": "2403",
                "kylin-current-standard": "2503",
                "kylin-hardened": "2503",
                "uos-min-dde": "1070u2",
                "uos-current-or-hardened": "25",
                "openkylin-current": "2.0-SP2",
            }[category_id]
            os_id = profile["os_id"]
            os_version = f'{profile["version_id"]}/{release_id}'
            desktop_environment = profile["desktop_environments"][0]
            hardened = category_id == "kylin-hardened"
            security_facts = {
                "administrator_available": True,
                "business_data_mutation": False,
                "graphical_desktop": True,
                "network_observation": "continuous-process-sampling-no-non-loopback-up",
                "package_manager": "dpkg",
                "security_profile": profile["security_profile"],
                "kysec_detected": hardened,
                "kysec_enabled": hardened,
                "kysec_exec_control": "off" if hardened else "not-present",
                "os_release_sha256": hashlib.sha256(
                    ("os-release\0" + category_id).encode("utf-8")
                ).hexdigest(),
                "os_version_sha256": (
                    hashlib.sha256(("os-version\0" + category_id).encode("utf-8")).hexdigest()
                    if os_id == "uos"
                    else "not-present"
                ),
            }
            acceptance_session_id = hashlib.sha256(
                ("session\0" + category_id).encode("utf-8")
            ).hexdigest()[:32]
            machine_identity_commitment_sha256 = hashlib.sha256(
                ("taiji-machine-identity-v1\0" + category_id).encode("utf-8")
            ).hexdigest()
            machine_fingerprint_sha256 = hashlib.sha256(
                (
                    self.challenge
                    + "\0"
                    + machine_identity_commitment_sha256
                ).encode("utf-8")
            ).hexdigest()
        record = {
            "schema": "taiji-linux-environment-evidence/v2",
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
            "os_version": os_version,
            "desktop_environment": desktop_environment,
            "security_facts": security_facts or {"business_data_mutation": False, "graphical_desktop": True},
            "checks": checks,
            "attachments": attachments,
            "challenge_nonce": self.challenge,
            "acceptance_session_id": acceptance_session_id,
            "machine_fingerprint_sha256": machine_fingerprint_sha256,
        }
        if machine_identity_commitment_sha256 is not None:
            record["machine_identity_commitment_sha256"] = (
                machine_identity_commitment_sha256
            )
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
                "--challenge", self.challenge,
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

    def test_certification_challenge_binds_every_record_and_positive_targets_are_distinct(self):
        for category in self.matrix["positive_categories"] + self.matrix["negative_boundaries"]:
            record_path = self.records / category["id"] / "environment-evidence.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["challenge_nonce"] = "d" * 64
            if record["category_kind"] == "positive":
                record["machine_fingerprint_sha256"] = hashlib.sha256(
                    (
                        record["challenge_nonce"]
                        + "\0"
                        + record["machine_identity_commitment_sha256"]
                    ).encode("utf-8")
                ).hexdigest()
            record_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("challenge", result.stderr.lower())

        self.tearDown()
        self.setUp()
        first, second = self.matrix["positive_categories"][:2]
        first_path = self.records / first["id"] / "environment-evidence.json"
        second_path = self.records / second["id"] / "environment-evidence.json"
        first_record = json.loads(first_path.read_text(encoding="utf-8"))
        second_record = json.loads(second_path.read_text(encoding="utf-8"))
        second_record["machine_identity_commitment_sha256"] = first_record[
            "machine_identity_commitment_sha256"
        ]
        second_record["machine_fingerprint_sha256"] = hashlib.sha256(
            (
                second_record["challenge_nonce"]
                + "\0"
                + second_record["machine_identity_commitment_sha256"]
            ).encode("utf-8")
        ).hexdigest()
        second_path.write_text(json.dumps(second_record, sort_keys=True) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("machine", result.stderr.lower())

    def test_mixed_deb_hash_and_binding_drift_are_rejected(self):
        path = self.records / "kylin-min-ukui" / "environment-evidence.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["deb_sha256"] = "d" * 64
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEB", result.stderr)

    def test_release_validator_compares_every_environment_record_to_build_binding(self):
        validator_path = ROOT / "scripts/validate-taiji-release-evidence.py"
        spec = importlib.util.spec_from_file_location(
            "taiji_release_validator_environment_binding_test",
            validator_path,
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
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
        matrix_sha = hashlib.sha256(MATRIX.read_bytes()).hexdigest()
        certification = {
            "schema": "taiji-linux-certification-set/v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "challenge_nonce": "c" * 64,
            "source_commit": self.source_commit,
            "version": self.version,
            "architecture": "amd64",
            "deb_basename": self.deb.name,
            "deb_sha256": self.deb_sha,
            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
            "compatibility_policy_sha256": self.policy_sha,
            "certification_profile": {
                "matrix_schema": "taiji-linux-certification-matrix/v2",
                "matrix_sha256": matrix_sha,
                "positive_category_count": 6,
                "negative_boundary_count": 6,
            },
            "offline_rehearsal": {
                "basename": self.offline.name,
                "sha256": hashlib.sha256(self.offline.read_bytes()).hexdigest(),
                "status": "PASS",
            },
            "environments": [],
            "negative_boundaries": [],
        }
        for category in self.matrix["positive_categories"]:
            record_path = self.records / category["id"] / "environment-evidence.json"
            certification["environments"].append(
                {
                    "category_id": category["id"],
                    "compatibility": "CERTIFIED",
                    "record_basename": f"records/{category['id']}/environment-evidence.json",
                    "record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
                }
            )
        for category in self.matrix["negative_boundaries"]:
            record_path = self.records / category["id"] / "environment-evidence.json"
            certification["negative_boundaries"].append(
                {
                    "category_id": category["id"],
                    "compatibility": "BLOCKED",
                    "record_basename": f"records/{category['id']}/environment-evidence.json",
                    "record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
                }
            )
        record_path = self.records / "kylin-min-ukui" / "environment-evidence.json"
        original_record = json.loads(record_path.read_text(encoding="utf-8"))
        original_contract = validator._load_environment_contract_for_certification()
        relaxed_record_contract = SimpleNamespace(
            validate_certification_matrix=original_contract.validate_certification_matrix,
            validate_environment_record=lambda _record, _matrix: None,
        )
        args = SimpleNamespace(challenge="c" * 64, matrix=MATRIX)
        mutations = {
            "source_commit": "b" * 40,
            "version": "9.9.9",
            "architecture": "arm64",
            "deb_basename": "taiji-agent_9.9.9_amd64.deb",
            "deb_sha256": "c" * 64,
            "compatibility_policy_id": "other-policy-v1",
            "compatibility_policy_sha256": "d" * 64,
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                record = dict(original_record)
                record[field] = replacement
                record_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
                summary = next(
                    item
                    for item in certification["environments"]
                    if item["category_id"] == "kylin-min-ukui"
                )
                summary["record_sha256"] = hashlib.sha256(record_path.read_bytes()).hexdigest()
                with patch.object(
                    validator,
                    "canonical_policy_identity",
                    return_value=("taiji-linux-amd64-deb-v1", self.policy_sha),
                ), patch.object(
                    validator,
                    "_load_environment_contract_for_certification",
                    return_value=relaxed_record_contract,
                ):
                    with self.assertRaisesRegex(validator.EvidenceError, field):
                        validator.validate_certification_set_v1(
                            certification,
                            self.root / "certification-set.json",
                            args,
                            binding,
                        )
        record_path.write_text(json.dumps(original_record, sort_keys=True) + "\n", encoding="utf-8")

    def test_unbound_current_v1_offline_evidence_is_rejected(self):
        self.offline.write_text(
            json.dumps(
                {
                    "schema": "taiji.offline-install-rehearsal.v1",
                    "status": "PASS",
                    "checks": {
                        "install": "PASS",
                        "uninstall": "PASS",
                        "reinstall": "PASS",
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("offline", result.stderr.lower())

    def test_quick_offline_rehearsal_cannot_enter_formal_certification(self):
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        for key in (
            "steps",
            "receipts",
            "data_manifests",
            "journal",
            "package_actions",
            "previous_release",
        ):
            evidence.pop(key)
        self.offline.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("N-1", result.stderr)

    def test_unknown_current_v1_offline_evidence_field_is_rejected(self):
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        evidence["unknown"] = True
        self.offline.write_text(json.dumps(evidence) + "\n", encoding="utf-8")

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未知字段", result.stderr)

    def test_current_v1_offline_evidence_log_hash_mismatch_is_rejected(self):
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        evidence["log_sha256"] = "0" * 64
        self.offline.write_text(json.dumps(evidence) + "\n", encoding="utf-8")

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("log_sha256", result.stderr)

    def test_current_certification_set_requires_kylin_policy_fixture_evidence(self):
        canonical_session = json.loads(self.offline_log.read_text(encoding="utf-8"))
        canonical_evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        cases = (
            ("environment", "container"),
            ("os_id", "debian"),
            ("os_version", "22.04"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                session = dict(canonical_session)
                session[field] = value
                self.offline_log.write_text(
                    json.dumps(session, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                evidence = dict(canonical_evidence)
                evidence[field] = value
                evidence["log_sha256"] = hashlib.sha256(
                    self.offline_log.read_bytes()
                ).hexdigest()
                self.offline.write_text(
                    json.dumps(evidence, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                result = self.command()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("current certification set requires", result.stderr)

    def test_current_certification_set_rejects_historical_offline_evidence(self):
        self.offline.write_text(
            json.dumps(
                {
                    "schema": "taiji-linux-offline-evidence/v1",
                    "status": "PASS",
                    "checks": {
                        "install": True,
                        "uninstall": True,
                        "reinstall": True,
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema", result.stderr.lower())

    def test_delivery_copy_loads_current_validator_from_assembler_directory(self):
        isolated_tools = self.root / "isolated-delivery" / "验收工具"
        isolated_tools.mkdir(parents=True)
        isolated_script = isolated_tools / SCRIPT.name
        shutil.copy2(SCRIPT, isolated_script)
        shutil.copy2(VALIDATOR, isolated_tools / VALIDATOR.name)
        spec = importlib.util.spec_from_file_location(
            "taiji_certification_set_isolated_copy_test",
            isolated_script,
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        isolated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolated)

        accepted, accepted_sha = isolated._validate_offline_evidence(
            self.offline,
            source_commit=self.source_commit,
            version=self.version,
            deb_basename=self.deb.name,
            deb_sha256=self.deb_sha,
            policy_id="taiji-linux-amd64-deb-v1",
            policy_sha256=self.policy_sha,
        )

        self.assertEqual(accepted["source_commit"], self.source_commit)
        self.assertEqual(accepted_sha, hashlib.sha256(self.offline.read_bytes()).hexdigest())

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
        self.assertIn("successful target checks", result.stderr)

    def test_negative_raw_preflight_must_match_matrix_error_code(self):
        directory = self.records / "arm-blocked"
        preflight_path = directory / "preflight-result.json"
        payload = json.loads(preflight_path.read_text(encoding="utf-8"))
        payload["error_code"] = "TAIJI-LINUX-E999-FORGED"
        payload["failed_capabilities"] = [payload["error_code"]]
        preflight_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        preflight_sha = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
        record_path = directory / "environment-evidence.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["attachments"][0]["sha256"] = preflight_sha
        record["security_facts"]["preflight_result_sha256"] = preflight_sha
        record_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("preflight", result.stderr.lower())

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
