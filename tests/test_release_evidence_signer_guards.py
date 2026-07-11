import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIGNER = ROOT / "scripts/sign-taiji-release-evidence.sh"
CHALLENGE = "ab" * 32


class ReleaseEvidenceSignerGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-signer-guard-")
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "offline-install-rehearsal.json"
        self.evidence.write_text(
            json.dumps(
                {
                    "evidence_type": "offline-install-rehearsal",
                    "challenge_nonce": CHALLENGE,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.private_key = self.root / "release-private.pem"
        self.private_key.write_text("fixture private key\n", encoding="utf-8")
        self.private_key.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_signer(self, challenge: str = CHALLENGE, private_key: Path | None = None):
        env = os.environ.copy()
        env["TAIJI_OFFLINE_REHEARSAL_CHALLENGE"] = challenge
        return subprocess.run(
            ["bash", str(SIGNER), str(self.evidence), str(private_key or self.private_key)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def assert_no_signature(self) -> None:
        self.assertFalse(Path(f"{self.evidence}.sig").exists())

    def test_rejects_group_readable_private_key_before_signing(self) -> None:
        self.private_key.chmod(0o640)

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("0400/0600", result.stderr)
        self.assert_no_signature()

    def test_rejects_hardlinked_private_key_before_signing(self) -> None:
        hardlink = self.root / "release-private-hardlink.pem"
        os.link(self.private_key, hardlink)

        result = self.run_signer(private_key=hardlink)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("硬链接", result.stderr)
        self.assert_no_signature()

    def test_rejects_independent_challenge_mismatch_before_key_use(self) -> None:
        result = self.run_signer(challenge="cd" * 32)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("challenge 不一致", result.stderr)
        self.assert_no_signature()

    def test_invalid_private_key_reports_a_fail_closed_error(self) -> None:
        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_rejects_non_root_owned_ancestor_symlink_for_private_key(self) -> None:
        key_directory = self.root / "real-key-directory"
        key_directory.mkdir(mode=0o700)
        key = key_directory / "release-private.pem"
        key.write_text("fixture private key\n", encoding="utf-8")
        key.chmod(0o600)
        linked_directory = self.root / "linked-key-directory"
        linked_directory.symlink_to(key_directory, target_is_directory=True)

        result = self.run_signer(private_key=linked_directory / key.name)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("祖先符号链接", result.stderr)
        self.assert_no_signature()


if __name__ == "__main__":
    unittest.main()
