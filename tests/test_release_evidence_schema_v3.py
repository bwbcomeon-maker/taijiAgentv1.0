import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts/validate-taiji-release-evidence.py"
POLICY_PATH = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER_PATH = ROOT / "packaging/linux/compatibility_policy.py"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "taiji_release_evidence_validator_schema3_test", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register the dynamic module before dataclass processing. Python 3.14's
    # dataclasses resolves postponed annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_policy_helper():
    spec = importlib.util.spec_from_file_location(
        "taiji_release_evidence_policy_schema3_test", POLICY_HELPER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {POLICY_HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseEvidenceSchemaV3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = load_validator()
        self.policy_helper = load_policy_helper()
        self.policy = self.policy_helper.load_and_validate(POLICY_PATH)
        self.policy_id = self.policy["policy_id"]
        self.policy_sha256 = self.policy_helper.canonical_sha256(self.policy)
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-schema3-evidence-")
        self.root = Path(self.temporary.name)
        self.commit = "a" * 40
        self.deb = self.root / "taiji-agent_1.0.0_amd64.deb"
        self.deb.write_bytes(b"deb-v3")
        self.manifest = self.root / "taiji-package-manifest.json"
        self.write_manifest()
        self.evidence = self.root / "release-evidence.json"
        self.write_evidence()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_manifest(self, **updates) -> None:
        manifest = {
            "schema": "taiji-package-manifest/v3",
            "package": "taiji-agent",
            "version": "1.0.0",
            "architecture": "amd64",
            "source_commit": self.commit,
            "deb_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "elf_abi_audit_basename": "elf-abi-audit.json",
            "elf_abi_audit_sha256": "e" * 64,
            "electron_executable_sha256": "c" * 64,
            "desktop_entry_sha256": "d" * 64,
            "maintainer": "Taiji Agent Product Team <noreply@localhost>",
            "built_at_utc": "2026-08-05T00:00:00Z",
        }
        manifest.update(updates)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    def write_evidence(self, **updates) -> None:
        evidence = {
            "schema": "taiji-release-evidence/v3",
            "evidence_type": "single-deb-publication",
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "challenge_nonce": "ab" * 32,
            "source_commit": self.commit,
            "version": "1.0.0",
            "architecture": "amd64",
            "deb_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "certification_set_basename": "certification-set.json",
            "certification_set_sha256": "f" * 64,
            "certification_set_signature_basename": "certification-set.json.sig",
            "certification_set_signature_sha256": "1" * 64,
            "maintainer": "Taiji Agent Product Team <noreply@localhost>",
            "customer_filename": self.deb.name,
            "customer_folder_contract": "exactly-one-deb",
            "signing_public_key_fingerprint": "2" * 64,
            "formal_gates": {"build": "PASS"},
        }
        evidence.update(updates)
        self.evidence.write_text(json.dumps(evidence), encoding="utf-8")

    def args(self, **updates):
        values = {
            "source_commit": self.commit,
            "deb": self.deb,
            "manifest": self.manifest,
            "checksum": None,
            "challenge": "ab" * 32,
        }
        values.update(updates)
        return argparse.Namespace(**values)

    def run_cli(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR_PATH),
                "release",
                "--evidence",
                str(self.evidence),
                "--source-commit",
                self.commit,
                "--deb",
                str(self.deb),
                "--manifest",
                str(self.manifest),
                "--challenge",
                "ab" * 32,
                "--pre-sign",
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_v3_build_binding_uses_compatibility_policy_identity(self):
        binding = self.validator.validate_build_binding(self.args())
        self.assertIsInstance(binding, self.validator.BuildBinding)
        self.assertEqual(binding.source_commit, self.commit)
        self.assertEqual(binding.version, "1.0.0")
        self.assertEqual(binding.architecture, "amd64")
        self.assertEqual(binding.deb_basename, self.deb.name)
        self.assertEqual(binding.deb_sha256, self.sha256(self.deb))
        self.assertEqual(binding.compatibility_policy_id, self.policy_id)
        self.assertEqual(binding.compatibility_policy_sha256, self.policy_sha256)

        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release-evidence-pre-sign-valid", result.stdout)

    def test_v3_rejects_target_baseline_fields(self):
        self.write_manifest(target_baseline_profile_id="legacy-profile")
        with self.assertRaisesRegex(self.validator.EvidenceError, "target baseline"):
            self.validator.validate_build_binding(self.args())
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("target baseline", result.stderr)

    def test_v3_rejects_policy_hash_mismatch(self):
        self.write_manifest(compatibility_policy_sha256="0" * 64)
        with self.assertRaisesRegex(self.validator.EvidenceError, "canonical policy"):
            self.validator.validate_build_binding(self.args())
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical policy", result.stderr)


if __name__ == "__main__":
    unittest.main()
