"""RED/contract tests for the signed v3 publication evidence assembler."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/assemble-taiji-release-evidence.py"
POLICY = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER = ROOT / "packaging/linux/compatibility_policy.py"


TOOLCHAIN = {
    "python_dependency_lock_status": "strict-locked",
    "python_lock_basename": "uv.lock",
    "python_lock_sha256": "dbab12665d98aef021ba64953c61b0ed8a908cfb56a1c01e2fcb4b052b71a2a1",
    "python_version": "3.11.15",
    "python_executable_sha256": "5" * 64,
    "uv_version": "0.12.2",
    "uv_archive_sha256": "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4",
    "uv_executable_sha256": "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2",
    "node_version": "22.23.1",
    "node_archive_sha256": "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578",
    "node_executable_sha256": "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068",
    "electron_version": "39.8.10",
    "electron_archive_sha256": "92e8b031fa5327c78a972279fd75fc8503fcd1773401809f4557e4de583eabd1",
    "electron_executable_sha256": "c" * 64,
}


class ReleaseEvidenceAssemblerV3Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-release-evidence-v3-")
        self.root = Path(self.temporary.name).resolve()
        self.deb = self.root / "taiji-agent_1.2.3_amd64.deb"
        self.deb.write_bytes(b"immutable-deb-v3")
        self.commit = "a" * 40
        self.policy_helper = self._load_policy_helper()
        self.policy = self.policy_helper.load_and_validate(POLICY)
        self.policy_sha = self.policy_helper.canonical_sha256(self.policy)
        self.manifest = self.root / "taiji-package-manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "source_commit": self.commit,
                    "deb_basename": self.deb.name,
                    "deb_sha256": hashlib.sha256(self.deb.read_bytes()).hexdigest(),
                    "compatibility_policy_id": self.policy["policy_id"],
                    "compatibility_policy_sha256": self.policy_sha,
                    "electron_executable_sha256": "c" * 64,
                    "desktop_entry_sha256": "d" * 64,
                    "maintainer": "Taiji Agent Product Team <noreply@localhost>",
                    **TOOLCHAIN,
                },
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        self.certification_set = self.root / "certification-set.json"
        self.certification_set.write_text(
            json.dumps(
                {
                    "schema": "taiji-linux-certification-set/v1",
                    "challenge_nonce": "c" * 64,
                    "source_commit": self.commit,
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "deb_basename": self.deb.name,
                    "deb_sha256": hashlib.sha256(self.deb.read_bytes()).hexdigest(),
                    "compatibility_policy_id": self.policy["policy_id"],
                    "compatibility_policy_sha256": self.policy_sha,
                },
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        self.signature = Path(f"{self.certification_set}.sig")
        self.signature.write_bytes(b"not-a-signature")
        self.ci_evidence = self.root / "github-ci-evidence.json"
        self.write_ci_evidence()
        self.output = self.root / "release-evidence.json"

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _load_policy_helper():
        spec = importlib.util.spec_from_file_location("taiji_release_policy_v3_test", POLICY_HELPER)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load policy helper")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _load_assembler():
        spec = importlib.util.spec_from_file_location("taiji_release_assembler_v3_test", SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load release assembler")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def write_ci_evidence(self, **updates):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        payload = {
            "schema": "taiji-github-ci-evidence/v1",
            "provider": "github-actions",
            "repository": "example/taiji-agent",
            "workflow_name": "Pull Request CI",
            "required_check_name": "CI Gate",
            "run_id": 123456789,
            "run_attempt": 1,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "head_sha": self.commit,
            "html_url": "https://github.com/example/taiji-agent/actions/runs/123456789",
            "completed_at_utc": now,
            "collected_at_utc": now,
        }
        payload.update(updates)
        self.ci_evidence.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    def assemble_with_verified_certification(self):
        assembler = self._load_assembler()
        args = Namespace(
            manifest=self.manifest,
            deb=self.deb,
            policy=POLICY,
            certification_set=self.certification_set,
            certification_signature=self.signature,
            ci_evidence=self.ci_evidence,
            output=self.output,
            challenge="d" * 64,
        )
        with patch.object(
            assembler,
            "_validate_certification_set",
            return_value=hashlib.sha256(self.signature.read_bytes()).hexdigest(),
        ):
            return assembler.assemble(args)

    def command(self, *, manifest=None, challenge="d" * 64):
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--manifest", str(manifest or self.manifest),
                "--deb", str(self.deb),
                "--policy", str(POLICY),
                "--certification-set", str(self.certification_set),
                "--certification-signature", str(self.signature),
                "--ci-evidence", str(self.ci_evidence),
                "--output", str(self.output),
                "--challenge", challenge,
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_unsigned_certification_set_cannot_generate_v3(self):
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_v2_cannot_be_resigned_as_current_release(self):
        v2 = json.loads(self.manifest.read_text(encoding="utf-8"))
        v2["schema"] = None
        v2["schema_version"] = 2
        v2["deb"] = v2.pop("deb_basename")
        self.manifest.write_text(json.dumps(v2) + "\n", encoding="utf-8")
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("v3", result.stderr)

    def test_publication_challenge_must_be_independent(self):
        result = self.command(challenge="c" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("challenge", result.stderr.lower())

    def test_candidate_deb_is_never_changed_on_failure(self):
        before = self.deb.read_bytes()
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.deb.read_bytes(), before)

    def test_formal_release_binds_exact_successful_ci_gate(self):
        output = self.assemble_with_verified_certification()
        evidence = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(evidence["ci_evidence_basename"], self.ci_evidence.name)
        self.assertEqual(
            evidence["ci_evidence_sha256"],
            hashlib.sha256(self.ci_evidence.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            evidence["formal_gates"],
            {
                "candidate_deb_unchanged": "PASS",
                "canonical_policy": "PASS",
                "certification_set": "PASS",
                "certification_signature": "PASS",
                "github_ci_gate": "PASS",
                "manifest_binding": "PASS",
            },
        )

    def test_ci_head_sha_or_conclusion_mismatch_blocks_before_output(self):
        for updates in ({"head_sha": "b" * 40}, {"conclusion": "failure"}):
            with self.subTest(updates=updates):
                if self.output.exists():
                    self.output.unlink()
                self.write_ci_evidence(**updates)
                with self.assertRaisesRegex(ValueError, "CI"):
                    self.assemble_with_verified_certification()
                self.assertFalse(self.output.exists())

    def test_old_v3_missing_strict_toolchain_cannot_be_assembled(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest.pop("uv_executable_sha256")
        self.manifest.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        result = self.command()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("toolchain", result.stderr.lower())
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
