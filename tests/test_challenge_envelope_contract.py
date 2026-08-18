"""Canonical signing-challenge envelope and replay-state contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/taiji-challenge-envelope.py"
ASSEMBLE_CERTIFICATION = ROOT / "scripts/assemble-taiji-certification-set.py"
ASSEMBLE_PUBLICATION = ROOT / "scripts/assemble-taiji-release-evidence.py"
SIGNER = ROOT / "scripts/sign-taiji-release-evidence.sh"


def load_helper():
    spec = importlib.util.spec_from_file_location("taiji_challenge_envelope_test", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load challenge-envelope helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


class ChallengeEnvelopeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_helper()
        self.now = datetime(2026, 8, 11, 8, 0, 0, tzinfo=timezone.utc)
        self.commit = "a" * 40
        self.deb_basename = "taiji-agent_1.0.2_amd64.deb"
        self.deb_sha256 = hashlib.sha256(b"candidate").hexdigest()

    def envelope(self, **updates):
        value = {
            "schema": "taiji-signing-challenge/v1",
            "purpose": "certification",
            "nonce": "ab" * 32,
            "issued_at_utc": utc_text(self.now - timedelta(minutes=5)),
            "expires_at_utc": utc_text(self.now + timedelta(minutes=5)),
            "source_commit": self.commit,
            "deb_basename": self.deb_basename,
            "deb_sha256": self.deb_sha256,
        }
        value.update(updates)
        return value

    def verify(self, envelope, **updates):
        values = {
            "purpose": "certification",
            "source_commit": self.commit,
            "deb_basename": self.deb_basename,
            "deb_sha256": self.deb_sha256,
            "at": self.now,
            "require_active": True,
            "evidence_times": (self.now,),
        }
        values.update(updates)
        return self.module.verify_envelope(envelope, **values)

    def test_accepts_exact_canonical_envelope_and_bytes_are_stable(self) -> None:
        envelope = self.envelope()

        verified = self.verify(envelope)

        self.assertEqual(verified, envelope)
        self.assertEqual(
            json.loads(self.module.canonical_bytes(envelope).decode("utf-8")),
            envelope,
        )
        self.assertTrue(self.module.canonical_bytes(envelope).endswith(b"\n"))

    def test_rejects_extra_or_missing_fields_and_duplicate_json_keys(self) -> None:
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "fields"):
            self.verify(self.envelope(extra="not-allowed"))
        missing = self.envelope()
        missing.pop("purpose")
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "fields"):
            self.verify(missing)
        duplicate = (
            '{"schema":"taiji-signing-challenge/v1","purpose":"certification",'
            '"purpose":"publication"}'
        ).encode("utf-8")
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "duplicate"):
            self.module.parse_envelope_bytes(duplicate)

    def test_rejects_wrong_purpose_commit_or_deb_identity(self) -> None:
        cases = (
            ({"purpose": "publication"}, "purpose"),
            ({"source_commit": "b" * 40}, "source_commit"),
            ({"deb_basename": "taiji-agent_9.9.9_amd64.deb"}, "deb_basename"),
            ({"deb_sha256": "f" * 64}, "deb_sha256"),
        )
        for update, expected in cases:
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    self.module.ChallengeEnvelopeError, expected
                ):
                    self.verify(self.envelope(**update))

    def test_signing_requires_active_window_and_evidence_time_inside_window(self) -> None:
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "expired"):
            self.verify(
                self.envelope(
                    issued_at_utc=utc_text(self.now - timedelta(minutes=10)),
                    expires_at_utc=utc_text(self.now - timedelta(minutes=1)),
                )
            )
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "future"):
            self.verify(
                self.envelope(
                    issued_at_utc=utc_text(self.now + timedelta(minutes=1)),
                    expires_at_utc=utc_text(self.now + timedelta(minutes=10)),
                )
            )
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "evidence"):
            self.verify(
                self.envelope(),
                evidence_times=(self.now - timedelta(minutes=6),),
            )
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "future"):
            self.verify(
                self.envelope(),
                evidence_times=(self.now + timedelta(seconds=1),),
            )
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "order"):
            self.verify(
                self.envelope(),
                require_active=False,
                evidence_times=(self.now,),
                evidence_not_after=self.now - timedelta(seconds=1),
            )

    def test_archival_verification_uses_signed_window_not_current_age(self) -> None:
        envelope = self.envelope()

        verified = self.verify(
            envelope,
            at=self.now + timedelta(days=365),
            require_active=False,
            evidence_times=(self.now,),
        )

        self.assertEqual(verified["nonce"], envelope["nonce"])

    def test_window_has_a_fixed_maximum_and_issue_cli_rejects_oversized_ttl(self) -> None:
        self.assertEqual(self.module.MAX_TTL_SECONDS, 7 * 24 * 60 * 60)
        issued = self.now - timedelta(minutes=1)
        too_long = self.envelope(
            issued_at_utc=utc_text(issued),
            expires_at_utc=utc_text(
                issued + timedelta(seconds=self.module.MAX_TTL_SECONDS + 1)
            ),
        )
        with self.assertRaisesRegex(self.module.ChallengeEnvelopeError, "maximum"):
            self.verify(too_long, require_active=False, evidence_times=())

        with tempfile.TemporaryDirectory(prefix="taiji-challenge-ttl-") as temporary:
            root = Path(temporary)
            deb = root / self.deb_basename
            deb.write_bytes(b"candidate")
            result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "issue",
                    "--purpose",
                    "certification",
                    "--source-commit",
                    self.commit,
                    "--deb",
                    str(deb),
                    "--output",
                    str(root / "too-long.json"),
                    "--ttl-seconds",
                    str(self.module.MAX_TTL_SECONDS + 1),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("maximum", result.stderr)

    def test_deb_identity_uses_streaming_digest_not_bounded_bytes_loader(self) -> None:
        with tempfile.TemporaryDirectory(prefix="taiji-challenge-deb-") as temporary:
            deb = Path(temporary) / self.deb_basename
            deb.write_bytes((b"stream-me-" * 1024 * 1024) + b"done")
            expected = hashlib.sha256(deb.read_bytes()).hexdigest()

            with mock.patch.object(
                self.module,
                "_regular_bytes",
                side_effect=AssertionError("DEB must not be loaded into memory"),
            ):
                basename, digest = self.module._deb_identity(deb)

            self.assertEqual(basename, deb.name)
            self.assertEqual(digest, expected)

    def test_nonce_reservation_is_cross_purpose_and_key_location_independent(self) -> None:
        fingerprint = "1" * 64
        evidence_sha = "2" * 64
        publication = self.envelope(purpose="publication")
        with tempfile.TemporaryDirectory(prefix="taiji-signer-state-") as temporary:
            account_home = Path(temporary)
            first = self.module.reserve_nonce(
                self.envelope(),
                evidence_sha256=evidence_sha,
                public_key_fingerprint=fingerprint,
                account_home=account_home,
                reserved_at=self.now,
            )
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
            self.assertEqual(first.name, f"{self.envelope()['nonce']}.used")
            self.assertIn(fingerprint, first.parts)
            with self.assertRaisesRegex(
                self.module.ChallengeEnvelopeError, "already used"
            ):
                self.module.reserve_nonce(
                    publication,
                    evidence_sha256=evidence_sha,
                    public_key_fingerprint=fingerprint,
                    account_home=account_home,
                    reserved_at=self.now,
                )

    def test_replay_state_rejects_symlink_and_non_owner_only_product_directory(self) -> None:
        fingerprint = "3" * 64
        with tempfile.TemporaryDirectory(prefix="taiji-signer-state-") as temporary:
            account_home = Path(temporary)
            product = account_home / ".local/state/taiji-release-evidence"
            product.parent.mkdir(parents=True)
            product.mkdir(mode=0o755)
            with self.assertRaisesRegex(
                self.module.ChallengeEnvelopeError, "owner-only"
            ):
                self.module.reserve_nonce(
                    self.envelope(),
                    evidence_sha256="4" * 64,
                    public_key_fingerprint=fingerprint,
                    account_home=account_home,
                    reserved_at=self.now,
                )
        with tempfile.TemporaryDirectory(prefix="taiji-signer-state-") as temporary:
            root = Path(temporary)
            account_home = root / "account"
            redirected = root / "redirected"
            account_home.mkdir()
            redirected.mkdir()
            (account_home / ".local").symlink_to(redirected, target_is_directory=True)
            with self.assertRaisesRegex(
                self.module.ChallengeEnvelopeError, "symlink|unsafe"
            ):
                self.module.reserve_nonce(
                    self.envelope(),
                    evidence_sha256="4" * 64,
                    public_key_fingerprint=fingerprint,
                    account_home=account_home,
                    reserved_at=self.now,
                )

    def test_issue_cli_writes_one_canonical_artifact_bound_envelope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="taiji-challenge-issue-") as temporary:
            root = Path(temporary)
            deb = root / self.deb_basename
            deb.write_bytes(b"candidate")
            output = root / "certification-envelope.json"
            command = [
                sys.executable,
                str(HELPER),
                "issue",
                "--purpose",
                "certification",
                "--source-commit",
                self.commit,
                "--deb",
                str(deb),
                "--output",
                str(output),
                "--nonce",
                "ef" * 32,
            ]

            first = subprocess.run(command, text=True, capture_output=True, check=False)
            second = subprocess.run(command, text=True, capture_output=True, check=False)

            self.assertEqual(first.returncode, 0, first.stderr)
            envelope = self.module.load_envelope_file(output)
            self.assertEqual(output.read_bytes(), self.module.canonical_bytes(envelope))
            self.assertEqual(envelope["deb_sha256"], self.deb_sha256)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("File exists", second.stderr)

    def test_formal_assemblers_and_signer_require_embedded_envelope(self) -> None:
        certification = ASSEMBLE_CERTIFICATION.read_text(encoding="utf-8")
        publication = ASSEMBLE_PUBLICATION.read_text(encoding="utf-8")
        signer = SIGNER.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--challenge-envelope", required=True', certification)
        self.assertIn('parser.add_argument("--challenge-envelope", required=True', publication)
        self.assertIn('"challenge_envelope": challenge_envelope', certification)
        self.assertIn('"challenge_envelope": challenge_envelope', publication)
        self.assertIn("reserve --envelope", signer)
        self.assertNotIn(".taiji-release-evidence-used-challenges", signer)


if __name__ == "__main__":
    unittest.main()
