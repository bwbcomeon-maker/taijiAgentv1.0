"""Strict certification-set assembler contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
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
        challenge_issued = datetime.now(timezone.utc) - timedelta(days=1)
        challenge_expires = datetime.now(timezone.utc) + timedelta(days=1)
        self.challenge_envelope = self.root / "certification-challenge.json"
        self.challenge_envelope.write_text(
            json.dumps(
                {
                    "schema": "taiji-signing-challenge/v1",
                    "purpose": "certification",
                    "nonce": self.challenge,
                    "issued_at_utc": challenge_issued.isoformat(timespec="seconds").replace(
                        "+00:00", "Z"
                    ),
                    "expires_at_utc": challenge_expires.isoformat(timespec="seconds").replace(
                        "+00:00", "Z"
                    ),
                    "source_commit": self.source_commit,
                    "deb_basename": self.deb.name,
                    "deb_sha256": self.deb_sha,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.policy = ROOT / "packaging/linux/compatibility-policy.json"
        self.policy_sha = load_script()._policy_identity(self.policy)[1]
        self.offline_dir = self.root / "offline-rehearsal"
        self.offline_dir.mkdir(mode=0o700)
        self.offline_log = self.offline_dir / "offline-install-rehearsal-session.json"
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
        self.previous_deb = self.offline_dir / "taiji-agent_1.2.2_amd64.deb"
        self.previous_deb.write_bytes(b"immutable-n-minus-one-deb")
        self.previous_deb_sha = hashlib.sha256(self.previous_deb.read_bytes()).hexdigest()
        self.test_release_private_key = self.root / "test-release-private.pem"
        self.test_release_public_key = self.root / "test-release-public.pem"
        generated = subprocess.run(
            [
                "openssl", "genpkey", "-algorithm", "RSA",
                "-pkeyopt", "rsa_keygen_bits:2048",
                "-out", str(self.test_release_private_key),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr.decode(errors="replace"))
        exported = subprocess.run(
            [
                "openssl", "pkey", "-in", str(self.test_release_private_key),
                "-pubout", "-out", str(self.test_release_public_key),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(exported.returncode, 0, exported.stderr.decode(errors="replace"))
        derived = subprocess.run(
            [
                "openssl", "pkey", "-pubin", "-in", str(self.test_release_public_key),
                "-outform", "DER",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(derived.returncode, 0, derived.stderr.decode(errors="replace"))
        self.test_release_public_fingerprint = hashlib.sha256(derived.stdout).hexdigest()
        self.previous_signature = self.offline_dir / f"{self.previous_deb.name}.sig"
        signed = subprocess.run(
            [
                "openssl", "dgst", "-sha256",
                "-sign", str(self.test_release_private_key),
                "-out", str(self.previous_signature),
                str(self.previous_deb),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(signed.returncode, 0, signed.stderr.decode(errors="replace"))
        self.previous_signature_sha = hashlib.sha256(
            self.previous_signature.read_bytes()
        ).hexdigest()
        self.previous_checksum = self.offline_dir / f"{self.previous_deb.name}.sha256"
        self.previous_checksum.write_text(
            f"{self.previous_deb_sha}  {self.previous_deb.name}\n",
            encoding="ascii",
        )
        self.previous_manifest = self.offline_dir / "previous-release-manifest.json"
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
        self.offline_lifecycle = self.offline_dir / "offline-install-rehearsal-lifecycle.json"
        lifecycle = json.loads(self.offline_log.read_text(encoding="utf-8"))
        lifecycle.update(
            {
                "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                "compatibility_policy_sha256": self.policy_sha,
                "previous_deb_basename": "taiji-agent_1.2.2_amd64.deb",
                "previous_deb_sha256": self.previous_deb_sha,
                "previous_version": "1.2.2",
                "previous_signature_basename": self.previous_signature.name,
                "previous_signature_sha256": self.previous_signature_sha,
                "previous_signature_verification": "PASS",
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
                        "result": result,
                        "state": "committed",
                        "transaction_id": transaction_id,
                        "deb_sha256": self.deb_sha,
                        "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                        "compatibility_policy_sha256": self.policy_sha,
                        "network": "none",
                    }
                    for operation, result, transaction_id in (
                        ("fresh_install", "installed", "fresh-n"),
                        ("reinstall", "reinstalled", "reinstall-n"),
                        ("upgrade", "upgraded", "upgrade-n"),
                        ("rollback", "rolled_back", "rollback-n"),
                        ("upgrade_again", "upgraded", "upgrade-again-n"),
                    )
                ],
                "data_manifests": {
                    "before_upgrade": "d" * 64,
                    "after_upgrade": "d" * 64,
                    "after_rollback": "d" * 64,
                    "after_remove": "d" * 64,
                    "after_purge": "d" * 64,
                },
                "journal": {
                    "upgrade_transaction_id": "upgrade-n",
                    "rollback_transaction_id": "rollback-n",
                    "second_upgrade_transaction_id": "upgrade-again-n",
                    "resume": "partial journal is never committed; manual_recovery_required is explicit",
                    "power_loss_resume_checked": True,
                    "partial_journal_treated_as_committed": False,
                    "partial_journal_result": "manual_recovery_required",
                    "manual_recovery_required": False,
                },
                "package_actions": [
                    {
                        "command": command,
                        "package": "taiji-agent",
                        "network": "none",
                        "download": False,
                    }
                    for command in ("dpkg --install", "dpkg --remove", "dpkg --purge")
                ],
            }
        )
        self.offline_lifecycle.write_text(
            json.dumps(lifecycle, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.offline = self.offline_dir / "offline-install-rehearsal.json"
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
                    "previous_release": {
                        "source_commit": "8" * 40,
                        "version": "1.2.2",
                        "deb_basename": self.previous_deb.name,
                        "deb_sha256": self.previous_deb_sha,
                        "checksum_basename": self.previous_checksum.name,
                        "checksum_sha256": hashlib.sha256(self.previous_checksum.read_bytes()).hexdigest(),
                        "signature_basename": self.previous_signature.name,
                        "signature_sha256": self.previous_signature_sha,
                        "signature_verification": "PASS",
                        "manifest_basename": self.previous_manifest.name,
                        "manifest_sha256": hashlib.sha256(self.previous_manifest.read_bytes()).hexdigest(),
                        "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                        "compatibility_policy_sha256": self.policy_sha,
                    },
                    "lifecycle_log_basename": self.offline_lifecycle.name,
                    "lifecycle_log_sha256": hashlib.sha256(
                        self.offline_lifecycle.read_bytes()
                    ).hexdigest(),
                    "previous_deb_basename": "taiji-agent_1.2.2_amd64.deb",
                    "previous_deb_sha256": self.previous_deb_sha,
                    "previous_version": "1.2.2",
                    "previous_signature_basename": self.previous_signature.name,
                    "previous_signature_sha256": self.previous_signature_sha,
                    "previous_signature_verification": "PASS",
                    "steps": lifecycle["steps"],
                    "receipts": lifecycle["receipts"],
                    "data_manifests": lifecycle["data_manifests"],
                    "journal": lifecycle["journal"],
                    "package_actions": lifecycle["package_actions"],
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
        self._write_records()
        self.output = self.root / "certification"

    def tearDown(self):
        self.temporary.cleanup()

    def _load_test_validator(self, name: str):
        spec = importlib.util.spec_from_file_location(name, VALIDATOR)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        validator.PINNED_RELEASE_PUBLIC_KEY_PATH = self.test_release_public_key
        validator.PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = (
            self.test_release_public_fingerprint
        )
        return validator

    def _rebind_previous_release_size(self, size: int) -> None:
        with self.previous_deb.open("wb") as handle:
            handle.seek(size - 1)
            handle.write(b"\0")
        self.previous_deb_sha = hashlib.sha256(self.previous_deb.read_bytes()).hexdigest()
        self.previous_checksum.write_text(
            f"{self.previous_deb_sha}  {self.previous_deb.name}\n",
            encoding="ascii",
        )
        previous_manifest = json.loads(self.previous_manifest.read_text(encoding="utf-8"))
        previous_manifest["deb_sha256"] = self.previous_deb_sha
        self.previous_manifest.write_text(
            json.dumps(previous_manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        signed = subprocess.run(
            [
                "openssl", "dgst", "-sha256",
                "-sign", str(self.test_release_private_key),
                "-out", str(self.previous_signature),
                str(self.previous_deb),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(signed.returncode, 0, signed.stderr.decode(errors="replace"))
        self.previous_signature_sha = hashlib.sha256(
            self.previous_signature.read_bytes()
        ).hexdigest()
        lifecycle = json.loads(self.offline_lifecycle.read_text(encoding="utf-8"))
        lifecycle["previous_deb_sha256"] = self.previous_deb_sha
        lifecycle["previous_signature_sha256"] = self.previous_signature_sha
        self.offline_lifecycle.write_text(
            json.dumps(lifecycle, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        evidence["previous_deb_sha256"] = self.previous_deb_sha
        evidence["previous_signature_sha256"] = self.previous_signature_sha
        previous = evidence["previous_release"]
        previous["deb_sha256"] = self.previous_deb_sha
        previous["checksum_sha256"] = hashlib.sha256(
            self.previous_checksum.read_bytes()
        ).hexdigest()
        previous["signature_sha256"] = self.previous_signature_sha
        previous["manifest_sha256"] = hashlib.sha256(
            self.previous_manifest.read_bytes()
        ).hexdigest()
        evidence["lifecycle_log_sha256"] = hashlib.sha256(
            self.offline_lifecycle.read_bytes()
        ).hexdigest()
        self.offline.write_text(
            json.dumps(evidence, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_safe_regular_never_accepts_swap_and_restore_payload(self):
        module = load_script()
        source = self.root / "swap-source.json"
        source.write_bytes(b"ORIGINAL")
        original_payload = source.read_bytes()
        replacement = self.root / "swap-replacement.json"
        replacement.write_bytes(b"MALICIOU")
        parked = self.root / "swap-parked.json"
        original_read_bytes = Path.read_bytes

        def swap_restore(path):
            if path == source:
                source.rename(parked)
                replacement.rename(source)
                try:
                    return original_read_bytes(source)
                finally:
                    source.rename(replacement)
                    parked.rename(source)
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", swap_restore):
            observed = module._safe_regular(source, "swap source")

        self.assertEqual(observed, original_payload)

    def test_offline_validator_consumes_the_same_private_snapshot_it_archives(self):
        source = SCRIPT.read_text(encoding="utf-8")
        start = source.index("def _validate_offline_evidence(")
        end = source.index("\ndef _validate_attachment(", start)
        function = source[start:end]

        self.assertIn("offline-validation-snapshot", function)
        self.assertIn("_stream_regular_snapshot", function)
        self.assertIn("snapshot_root / entry.name", function)
        self.assertIn("snapshot_root / evidence_basename", function)
        self.assertNotIn(
            '_safe_regular(evidence_path, "offline rehearsal evidence")',
            function,
        )

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

    def _positive_attachment_fixture(self, record):
        from tests.test_release_evidence_schema_v3 import ReleaseEvidenceSchemaV3Test

        fixture = ReleaseEvidenceSchemaV3Test(
            "test_positive_certification_bundle_is_recursively_validated_not_hash_only"
        )
        fixture.setUp()
        try:
            _base_record, payloads = fixture._positive_certification_bundle_fixture()
        finally:
            fixture.tearDown()

        payloads = dict(payloads)
        driver = json.loads(payloads["desktop-driver-result.json"].decode("utf-8"))
        driver["acceptance_session_id"] = record["acceptance_session_id"]
        driver["challenge_nonce"] = record["challenge_nonce"]
        payloads["desktop-driver-result.json"] = (
            json.dumps(driver, sort_keys=True) + "\n"
        ).encode("utf-8")

        identity_fields = (
            "category_id",
            "category_kind",
            "compatibility",
            "source_commit",
            "version",
            "architecture",
            "deb_basename",
            "deb_sha256",
            "compatibility_policy_id",
            "compatibility_policy_sha256",
            "machine_identity_commitment_sha256",
            "os_id",
            "os_version",
            "desktop_environment",
            "security_facts",
        )
        environment = {key: record[key] for key in identity_fields}
        environment.update(
            {
                "schema": "taiji-linux-environment-observation/v1",
                "checks": {"preflight": "PASS", "install": "PASS"},
                "attachments": [],
            }
        )
        payloads["environment-observation.json"] = (
            json.dumps(environment, sort_keys=True) + "\n"
        ).encode("utf-8")

        observation = json.loads(
            payloads["single-deb-install-observation.json"].decode("utf-8")
        )
        observation.update(
            {
                "challenge_nonce": record["challenge_nonce"],
                "machine_identity_commitment_sha256": record[
                    "machine_identity_commitment_sha256"
                ],
                "machine_fingerprint_sha256": record["machine_fingerprint_sha256"],
                "source_commit": record["source_commit"],
                "deb_observed_basename": record["deb_basename"],
                "deb_sha256": record["deb_sha256"],
            }
        )
        payloads["single-deb-install-observation.json"] = (
            json.dumps(observation, sort_keys=True) + "\n"
        ).encode("utf-8")

        attestation = json.loads(
            payloads["single-deb-install-method-attestation.json"].decode("utf-8")
        )
        attestation.update(
            {
                "observation_sha256": hashlib.sha256(
                    payloads["single-deb-install-observation.json"]
                ).hexdigest(),
                "challenge_nonce": record["challenge_nonce"],
                "machine_fingerprint_sha256": record["machine_fingerprint_sha256"],
                "boot_fingerprint_sha256": observation["boot_fingerprint_sha256"],
                "deb_sha256": record["deb_sha256"],
                "graphical_installer_evidence_sha256": hashlib.sha256(
                    payloads["single-deb-graphical-installer.png"]
                ).hexdigest(),
            }
        )
        payloads["single-deb-install-method-attestation.json"] = (
            json.dumps(attestation, sort_keys=True) + "\n"
        ).encode("utf-8")

        target = json.loads(payloads["target-verification.json"].decode("utf-8"))
        for key in identity_fields:
            if key != "security_facts":
                target[key] = record[key]
        target.update(
            {
                "acceptance_session_id": record["acceptance_session_id"],
                "challenge_nonce": record["challenge_nonce"],
                "machine_fingerprint_sha256": record["machine_fingerprint_sha256"],
                "release_artifacts_sha256": "9" * 64,
                "checks": dict(driver["checks"]),
            }
        )
        pointers = {
            "environment_observation": "environment-observation.json",
            "install_observation": "single-deb-install-observation.json",
            "install_method_attestation": "single-deb-install-method-attestation.json",
            "graphical_installer_evidence": "single-deb-graphical-installer.png",
            "driver_result": "desktop-driver-result.json",
            "screenshot": "desktop-app.png",
            "diagnostic": "taiji-support-bundle.json",
        }
        for field, basename in pointers.items():
            target[field + "_basename"] = basename
            target[field + "_sha256"] = hashlib.sha256(payloads[basename]).hexdigest()
        payloads["target-verification.json"] = (
            json.dumps(target, sort_keys=True) + "\n"
        ).encode("utf-8")
        return payloads

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
        if category_kind != "positive":
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
        if category_kind == "positive":
            for basename, payload in sorted(
                self._positive_attachment_fixture(record).items()
            ):
                (directory / basename).write_bytes(payload)
                attachments.append(
                    {"basename": basename, "sha256": hashlib.sha256(payload).hexdigest()}
                )
            record["attachments"] = attachments
        (directory / "environment-evidence.json").write_text(
            json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
        )

    def command(self, *extra):
        runner = self.root / "assemble-with-test-release-trust-root.py"
        runner.write_text(
            textwrap.dedent(
                f'''\
                import importlib.util
                import pathlib
                import sys

                validator_spec = importlib.util.spec_from_file_location(
                    "taiji_test_certification_validator", pathlib.Path({str(VALIDATOR)!r})
                )
                if validator_spec is None or validator_spec.loader is None:
                    raise SystemExit(97)
                validator = importlib.util.module_from_spec(validator_spec)
                sys.modules[validator_spec.name] = validator
                validator_spec.loader.exec_module(validator)
                validator.PINNED_RELEASE_PUBLIC_KEY_PATH = pathlib.Path({str(self.test_release_public_key)!r})
                validator.PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = {self.test_release_public_fingerprint!r}

                assembler_path = pathlib.Path({str(SCRIPT)!r})
                assembler_spec = importlib.util.spec_from_file_location(
                    "taiji_test_certification_assembler", assembler_path
                )
                if assembler_spec is None or assembler_spec.loader is None:
                    raise SystemExit(97)
                assembler = importlib.util.module_from_spec(assembler_spec)
                sys.modules[assembler_spec.name] = assembler
                assembler_spec.loader.exec_module(assembler)
                assembler._load_release_validator = lambda: validator
                sys.argv = [str(assembler_path), *sys.argv[1:]]
                raise SystemExit(assembler.main())
                '''
            ),
            encoding="utf-8",
        )
        return subprocess.run(
            [
                "python3",
                str(runner),
                "--matrix", str(MATRIX),
                "--records-dir", str(self.records),
                "--offline-evidence", str(self.offline_dir),
                "--deb", str(self.deb),
                "--policy", str(self.policy),
                "--output", str(self.output),
                "--challenge-envelope", str(self.challenge_envelope),
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
        self.assertEqual(payload["challenge_envelope"]["nonce"], self.challenge)
        self.assertEqual(payload["challenge_envelope"]["purpose"], "certification")
        self.assertEqual(len(payload["environments"]), 6)
        self.assertTrue(all(item["compatibility"] == "CERTIFIED" for item in payload["environments"]))
        self.assertEqual(len(payload["negative_boundaries"]), 6)
        self.assertNotEqual(self.deb.read_bytes(), b"")

    def test_archives_and_binds_the_complete_offline_rehearsal_directory(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        archived = self.output / "offline-rehearsal"
        self.assertEqual(
            {path.name for path in archived.iterdir()},
            {
                self.offline.name,
                self.offline_log.name,
                self.offline_lifecycle.name,
                self.previous_deb.name,
                self.previous_checksum.name,
                self.previous_signature.name,
                self.previous_manifest.name,
            },
        )
        payload = json.loads((self.output / "certification-set.json").read_text(encoding="utf-8"))
        summary = payload["offline_rehearsal"]
        self.assertEqual(summary["directory_basename"], archived.name)
        self.assertEqual(
            {item["basename"] for item in summary["files"]},
            {path.name for path in archived.iterdir()},
        )

    def test_formal_certification_rejects_missing_lifecycle_original(self):
        self.offline_lifecycle.unlink()

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_formal_certification_reverifies_archived_previous_signature(self):
        self.previous_signature.write_bytes(b"forged-but-rehashed-signature\n")
        forged_sha = hashlib.sha256(self.previous_signature.read_bytes()).hexdigest()
        lifecycle = json.loads(self.offline_lifecycle.read_text(encoding="utf-8"))
        lifecycle["previous_signature_sha256"] = forged_sha
        self.offline_lifecycle.write_text(
            json.dumps(lifecycle, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        evidence["previous_signature_sha256"] = forged_sha
        evidence["previous_release"]["signature_sha256"] = forged_sha
        evidence["lifecycle_log_sha256"] = hashlib.sha256(
            self.offline_lifecycle.read_bytes()
        ).hexdigest()
        self.offline.write_text(
            json.dumps(evidence, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signature", result.stderr.lower())
        self.assertFalse(self.output.exists())

    def test_final_validator_ignores_path_injected_fake_openssl_for_previous_signature(self):
        self.previous_signature.write_bytes(b"forged-but-rehashed-signature\n")
        forged_sha = hashlib.sha256(self.previous_signature.read_bytes()).hexdigest()
        lifecycle = json.loads(self.offline_lifecycle.read_text(encoding="utf-8"))
        lifecycle["previous_signature_sha256"] = forged_sha
        self.offline_lifecycle.write_text(
            json.dumps(lifecycle, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        evidence["previous_signature_sha256"] = forged_sha
        evidence["previous_release"]["signature_sha256"] = forged_sha
        evidence["lifecycle_log_sha256"] = hashlib.sha256(
            self.offline_lifecycle.read_bytes()
        ).hexdigest()
        self.offline.write_text(
            json.dumps(evidence, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        actual_der = subprocess.run(
            [
                "/usr/bin/openssl", "pkey", "-pubin",
                "-in", str(self.test_release_public_key), "-outform", "DER",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        der_path = self.root / "test-release-public.der"
        der_path.write_bytes(actual_der)
        fake_bin = self.root / "fake-openssl-bin"
        fake_bin.mkdir()
        fake_openssl = fake_bin / "openssl"
        fake_openssl.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = pkey ]; then cat \"$FAKE_OPENSSL_DER\"; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_openssl.chmod(0o755)
        validator = self._load_test_validator("taiji_path_injected_fake_openssl_validator")
        binding = validator.BuildBinding(
            source_commit=self.source_commit,
            version=self.version,
            architecture="amd64",
            deb_basename=self.deb.name,
            deb_sha256=self.deb_sha,
            compatibility_policy_id="taiji-linux-amd64-deb-v1",
            compatibility_policy_sha256=self.policy_sha,
            electron_executable_sha256="e" * 64,
            desktop_entry_sha256="d" * 64,
            delivery_inventory_sha256=evidence["delivery_inventory_sha256"],
        )

        with (
            patch.dict(
                os.environ,
                {
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "FAKE_OPENSSL_DER": str(der_path),
                },
                clear=False,
            ),
            self.assertRaisesRegex(
                validator.EvidenceError,
                "openssl|fingerprint|signature|\u7b7e名",
            ),
        ):
            validator.validate_offline_evidence_v1(
                evidence,
                self.offline,
                SimpleNamespace(
                    challenge=self.challenge,
                    source_commit=self.source_commit,
                    deb=Path(self.deb.name),
                ),
                binding,
                require_lifecycle=True,
            )

    def test_final_validator_streams_previous_deb_larger_than_generic_evidence_limit(self):
        self._rebind_previous_release_size(40 * 1024 * 1024)
        validator = self._load_test_validator("taiji_large_previous_validator")
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        binding = validator.BuildBinding(
            source_commit=self.source_commit,
            version=self.version,
            architecture="amd64",
            deb_basename=self.deb.name,
            deb_sha256=self.deb_sha,
            compatibility_policy_id="taiji-linux-amd64-deb-v1",
            compatibility_policy_sha256=self.policy_sha,
            electron_executable_sha256="e" * 64,
            desktop_entry_sha256="d" * 64,
            delivery_inventory_sha256=evidence["delivery_inventory_sha256"],
        )

        validator.validate_offline_evidence_v1(
            evidence,
            self.offline,
            SimpleNamespace(
                challenge=self.challenge,
                source_commit=self.source_commit,
                deb=Path(self.deb.name),
            ),
            binding,
            require_lifecycle=True,
        )

    def test_certification_assembler_streams_large_previous_deb_snapshot(self):
        self._rebind_previous_release_size(40 * 1024 * 1024)

        result = self.command()

        self.assertEqual(result.returncode, 0, result.stderr)
        archived_previous = self.output / "offline-rehearsal" / self.previous_deb.name
        self.assertEqual(archived_previous.stat().st_size, 40 * 1024 * 1024)
        self.assertEqual(
            hashlib.sha256(archived_previous.read_bytes()).hexdigest(),
            self.previous_deb_sha,
        )

    def test_certification_assembler_keeps_generic_offline_attachment_limit(self):
        with self.offline_log.open("r+b") as handle:
            handle.seek(2 * 1024 * 1024 - 1)
            handle.write(b"\0")

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid size", result.stderr)
        self.assertFalse(self.output.exists())

    def test_formal_certification_rejects_rebound_newer_previous_version(self):
        lifecycle = json.loads(self.offline_lifecycle.read_text(encoding="utf-8"))
        lifecycle["previous_version"] = "9.9.9"
        lifecycle["previous_deb_basename"] = "taiji-agent_9.9.9_amd64.deb"
        lifecycle["previous_signature_basename"] = "taiji-agent_9.9.9_amd64.deb.sig"
        self.offline_lifecycle.write_text(
            json.dumps(lifecycle, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        evidence = json.loads(self.offline.read_text(encoding="utf-8"))
        evidence["previous_version"] = "9.9.9"
        evidence["previous_deb_basename"] = "taiji-agent_9.9.9_amd64.deb"
        evidence["previous_signature_basename"] = "taiji-agent_9.9.9_amd64.deb.sig"
        evidence["lifecycle_log_sha256"] = hashlib.sha256(
            self.offline_lifecycle.read_bytes()
        ).hexdigest()
        self.offline.write_text(
            json.dumps(evidence, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("严格早于", result.stderr)
        self.assertFalse(self.output.exists())

    def test_validator_rejects_tampered_archived_offline_or_environment_attachment(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)

        validator = self._load_test_validator("taiji_certification_physical_validator")
        data = json.loads((self.output / "certification-set.json").read_text(encoding="utf-8"))
        binding = validator.BuildBinding(
            source_commit=self.source_commit,
            version=self.version,
            architecture="amd64",
            deb_basename=self.deb.name,
            deb_sha256=self.deb_sha,
            compatibility_policy_id="taiji-linux-amd64-deb-v1",
            compatibility_policy_sha256=self.policy_sha,
            electron_executable_sha256="c" * 64,
            desktop_entry_sha256="d" * 64,
        )
        args = SimpleNamespace(challenge=self.challenge, matrix=MATRIX)

        archived_attachment = self.output / "records/kylin-min-ukui/desktop-driver-result.json"
        original_attachment = archived_attachment.read_bytes()
        archived_attachment.write_text('{"ok":false}\n', encoding="utf-8")
        with patch.object(validator, "canonical_policy_identity", return_value=("taiji-linux-amd64-deb-v1", self.policy_sha)):
            with self.assertRaisesRegex(validator.EvidenceError, "attachment|附件"):
                validator.validate_certification_set_v1(
                    data, self.output / "certification-set.json", args, binding
                )

        archived_attachment.write_bytes(original_attachment)
        (self.output / "offline-rehearsal/offline-install-rehearsal-session.json").write_text(
            "{}\n", encoding="utf-8"
        )
        with patch.object(validator, "canonical_policy_identity", return_value=("taiji-linux-amd64-deb-v1", self.policy_sha)):
            with self.assertRaisesRegex(validator.EvidenceError, "offline|离线"):
                validator.validate_certification_set_v1(
                    data, self.output / "certification-set.json", args, binding
                )

    def test_release_validator_exposes_and_accepts_certification_set_v1_contract(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        validator = self._load_test_validator("taiji_release_validator_certification_test")
        data = json.loads((self.output / "certification-set.json").read_text(encoding="utf-8"))
        binding = validator.BuildBinding(
            source_commit=self.source_commit,
            version=self.version,
            architecture="amd64",
            deb_basename=self.deb.name,
            deb_sha256=self.deb_sha,
            compatibility_policy_id="taiji-linux-amd64-deb-v1",
            compatibility_policy_sha256=self.policy_sha,
            electron_executable_sha256="c" * 64,
            desktop_entry_sha256="d" * 64,
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
        self.assertRegex(result.stderr.lower(), r"challenge|fingerprint")

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
        self.assertRegex(result.stderr, r"DEB|deb_sha256")

    def test_release_validator_compares_every_environment_record_to_build_binding(self):
        validator = self._load_test_validator(
            "taiji_release_validator_environment_binding_test"
        )
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
        generated_at = datetime.now(timezone.utc)
        certification = {
            "schema": "taiji-linux-certification-set/v1",
            "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
            "challenge_nonce": "c" * 64,
            "challenge_envelope": {
                "schema": "taiji-signing-challenge/v1",
                "purpose": "certification",
                "nonce": "c" * 64,
                "issued_at_utc": (generated_at - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "expires_at_utc": (generated_at + timedelta(minutes=55)).isoformat().replace("+00:00", "Z"),
                "source_commit": self.source_commit,
                "deb_basename": self.deb.name,
                "deb_sha256": self.deb_sha,
            },
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
            "previous_deb_basename",
            "previous_deb_sha256",
            "previous_version",
            "previous_signature_basename",
            "previous_signature_sha256",
            "previous_signature_verification",
            "lifecycle_log_basename",
            "lifecycle_log_sha256",
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
        canonical_lifecycle = json.loads(
            self.offline_lifecycle.read_text(encoding="utf-8")
        )
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
                lifecycle = dict(canonical_lifecycle)
                lifecycle[field] = value
                self.offline_lifecycle.write_text(
                    json.dumps(lifecycle, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                evidence["log_sha256"] = hashlib.sha256(
                    self.offline_log.read_bytes()
                ).hexdigest()
                evidence["lifecycle_log_sha256"] = hashlib.sha256(
                    self.offline_lifecycle.read_bytes()
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
        isolated_validator = isolated_tools / VALIDATOR.name
        validator_source = VALIDATOR.read_text(encoding="utf-8").replace(
            'PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"',
            f'PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = "{self.test_release_public_fingerprint}"',
        )
        isolated_validator.write_text(validator_source, encoding="utf-8")
        shutil.copy2(self.test_release_public_key, isolated_tools / "signing-public.pem")
        spec = importlib.util.spec_from_file_location(
            "taiji_certification_set_isolated_copy_test",
            isolated_script,
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        isolated = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolated)

        accepted, copied, inventory, inventory_sha = isolated._validate_offline_evidence(
            self.offline_dir,
            source_commit=self.source_commit,
            version=self.version,
            deb_basename=self.deb.name,
            deb_sha256=self.deb_sha,
            policy_id="taiji-linux-amd64-deb-v1",
            policy_sha256=self.policy_sha,
        )

        self.assertEqual(accepted["source_commit"], self.source_commit)
        self.assertEqual(
            set(copied),
            {
                self.offline.name,
                self.offline_log.name,
                self.offline_lifecycle.name,
                self.previous_deb.name,
                self.previous_checksum.name,
                self.previous_signature.name,
                self.previous_manifest.name,
            },
        )
        self.assertEqual({item["basename"] for item in inventory}, set(copied))
        self.assertRegex(inventory_sha, r"^[0-9a-f]{64}$")

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
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("os.rename(temp_dir, args.output)", source)
        self.assertIn("_publish_directory_noreplace(temp_dir, args.output)", source)
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        second = self.command()
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("overwrite", second.stderr.lower())
        data = json.loads((self.output / "certification-set.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data), {
            "schema", "generated_at_utc", "challenge_nonce", "challenge_envelope", "source_commit", "version",
            "architecture", "deb_basename", "deb_sha256", "compatibility_policy_id",
            "compatibility_policy_sha256", "certification_profile", "offline_rehearsal",
            "environments", "negative_boundaries",
        })


if __name__ == "__main__":
    unittest.main()
