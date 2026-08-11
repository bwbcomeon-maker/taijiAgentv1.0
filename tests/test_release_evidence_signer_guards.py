import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIGNER = ROOT / "scripts/sign-taiji-release-evidence.sh"
CHALLENGE = "ab" * 32
SOURCE_COMMIT = "a" * 40
DEB_BASENAME = "taiji-agent_1.0.2_amd64.deb"
DEB_SHA256 = "1" * 64


class ReleaseEvidenceSignerGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-signer-guard-")
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "certification-set.json"
        self.write_evidence()
        self.private_key = self.root / "release-private.pem"
        self.private_key.write_text("fixture private key\n", encoding="utf-8")
        self.private_key.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_evidence(
        self,
        *,
        schema: str = "taiji-linux-certification-set/v1",
        purpose: str = "certification",
        envelope_updates: dict | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        envelope = {
            "schema": "taiji-signing-challenge/v1",
            "purpose": purpose,
            "nonce": CHALLENGE,
            "issued_at_utc": (now - timedelta(minutes=5)).isoformat().replace(
                "+00:00", "Z"
            ),
            "expires_at_utc": (now + timedelta(minutes=55)).isoformat().replace(
                "+00:00", "Z"
            ),
            "source_commit": SOURCE_COMMIT,
            "deb_basename": DEB_BASENAME,
            "deb_sha256": DEB_SHA256,
        }
        envelope.update(envelope_updates or {})
        self.evidence.write_text(
            json.dumps(
                {
                    "schema": schema,
                    "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
                    "challenge_nonce": CHALLENGE,
                    "challenge_envelope": envelope,
                    "source_commit": SOURCE_COMMIT,
                    "deb_basename": DEB_BASENAME,
                    "deb_sha256": DEB_SHA256,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def run_signer(self, private_key: Path | None = None):
        return subprocess.run(
            ["bash", str(SIGNER), str(self.evidence), str(private_key or self.private_key)],
            cwd=ROOT,
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

    def test_rejects_wrong_embedded_purpose_before_key_use(self) -> None:
        self.write_evidence(purpose="publication")

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("purpose", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_invalid_private_key_reports_a_fail_closed_error(self) -> None:
        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_rejects_existing_signature_without_overwrite(self) -> None:
        signature = Path(f"{self.evidence}.sig")
        signature.write_bytes(b"existing-signature")

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("拒绝覆盖", result.stderr)
        self.assertEqual(signature.read_bytes(), b"existing-signature")

    def test_rejects_historical_v2_evidence(self) -> None:
        self.evidence.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "evidence_type": "offline-install-rehearsal",
                    "challenge_nonce": CHALLENGE,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.run_signer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("只接受 certification-set v1 或 release-evidence v3", result.stderr)
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

    def test_validation_challenge_reservation_and_signature_share_one_private_snapshot(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")

        self.assertIn('SNAPSHOT_EVIDENCE="$SNAPSHOT_ROOT/evidence.json"', source)
        self.assertIn(
            'SNAPSHOT_ENVELOPE="$SNAPSHOT_ROOT/challenge-envelope.json"', source
        )
        self.assertIn('reserve --envelope "$SNAPSHOT_ENVELOPE"', source)
        self.assertIn('--evidence "$SNAPSHOT_EVIDENCE"', source)
        self.assertIn('--public-key-fingerprint "$public_fingerprint"', source)
        self.assertNotIn(".taiji-release-evidence-used-challenges", source)
        self.assertIn('-out "$tmp_signature" "$SNAPSHOT_EVIDENCE"', source)
        self.assertIn('-signature "$tmp_signature" "$SNAPSHOT_EVIDENCE"', source)
        self.assertIn('-signature "$SIGNATURE" "$EVIDENCE"', source)
        self.assertIn('rm -f -- "$SIGNATURE"', source)

    def test_rejects_incomplete_publication_before_private_key_use(self) -> None:
        self.write_evidence(
            schema="taiji-release-evidence/v3",
            purpose="publication",
        )

        result = subprocess.run(
            ["bash", str(SIGNER), str(self.evidence), str(self.private_key)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("publication physical bundle", result.stderr)
        self.assertNotIn("无法读取发布私钥", result.stderr)
        self.assert_no_signature()

    def test_publication_signer_validates_ci_trio_from_recursive_bundle_snapshot(self) -> None:
        source = SIGNER.read_text(encoding="utf-8")

        self.assertIn('bundle_evidence = delivery / "release-evidence.json"', source)
        self.assertIn("signing evidence and recursive bundle snapshot differ", source)
        self.assertIn(
            "validator.validate_release_evidence_v3(data, bundle_evidence, args, binding)",
            source,
        )
        self.assertIn("revalidate-taiji-github-ci-evidence.py", source)
        self.assertIn(
            '"$SNAPSHOT_ROOT/delivery/github-ci-evidence.json"', source
        )
        self.assertLess(
            source.index("github-ci-live-revalidation"),
            source.index("private_fingerprint="),
        )


if __name__ == "__main__":
    unittest.main()
