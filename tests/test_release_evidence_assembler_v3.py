"""RED/contract tests for the signed v3 publication evidence assembler."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/assemble-taiji-release-evidence.py"
POLICY = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER = ROOT / "packaging/linux/compatibility_policy.py"


class ReleaseEvidenceAssemblerV3Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-release-evidence-v3-")
        self.root = Path(self.temporary.name)
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


if __name__ == "__main__":
    unittest.main()
