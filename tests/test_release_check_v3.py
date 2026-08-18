"""Contract tests for the v3 release-check publication gates."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/taiji-release-check.sh"
SIGNER = ROOT / "scripts/sign-taiji-release-evidence.sh"


class ReleaseCheckV3Tests(unittest.TestCase):
    def setUp(self):
        self.check = CHECK.read_text(encoding="utf-8")
        self.signer = SIGNER.read_text(encoding="utf-8")

    def test_release_check_requires_certification_set_and_v3_publication(self):
        for token in (
            "certification-set.json",
            "certification-set.json.sig",
            "release-evidence.json",
            "release-evidence.json.sig",
            "certification",
            "release",
        ):
            self.assertIn(token, self.check)
        self.assertIn("taiji-release-evidence/v3", self.check)
        self.assertNotIn("TAIJI_CERTIFICATION_CHALLENGE", self.check)
        self.assertNotIn("TAIJI_PUBLICATION_CHALLENGE", self.check)

    def test_release_check_blocks_v2_only_and_binds_all_deb_policy_hashes(self):
        for token in (
            "schema_version=2",
            "compatibility_policy_sha256",
            "certification_set_sha256",
            "deb_sha256",
            "source_commit",
        ):
            self.assertIn(token, self.check)
        self.assertIn("current release", self.check.lower())

    def test_signer_uses_purpose_bound_embedded_challenge_envelopes(self):
        self.assertIn('CHALLENGE_HELPER="$ROOT_DIR/scripts/taiji-challenge-envelope.py"', self.signer)
        self.assertIn('"taiji-linux-certification-set/v1": "certification"', self.signer)
        self.assertIn('"taiji-release-evidence/v3": "publication"', self.signer)
        self.assertIn('reserve --envelope "$SNAPSHOT_ENVELOPE"', self.signer)
        self.assertNotIn("TAIJI_CERTIFICATION_CHALLENGE", self.signer)
        self.assertNotIn("TAIJI_PUBLICATION_CHALLENGE", self.signer)
        self.assertIn("certification-set", self.signer)
        self.assertIn("release-evidence/v3", self.signer)

    def test_signer_validates_the_complete_certification_bundle_before_signing(self):
        self.assertIn("validate_certification_set_v1", self.signer)
        self.assertIn("certification-set physical bundle", self.signer)

    def test_signer_validates_the_complete_publication_bundle_before_signing(self):
        self.assertIn("validate_release_evidence_v3", self.signer)
        self.assertIn("publication physical bundle", self.signer)

    def test_current_v3_release_check_does_not_require_legacy_offline_apt_repository(self):
        self.assertNotIn('"$DELIVERY_DIR/离线依赖/Packages"', self.check)
        self.assertNotIn('"$DELIVERY_DIR/离线依赖/Packages.gz"', self.check)
        self.assertNotIn('--packages "$DELIVERY_DIR/离线依赖/Packages"', self.check)
        self.assertNotIn('--packages-gz "$DELIVERY_DIR/离线依赖/Packages.gz"', self.check)

    def test_formal_release_check_revalidates_github_ci_live(self):
        runner = (ROOT / "scripts/run-taiji-release-python-tests.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("revalidate-taiji-github-ci-evidence.py", self.check)
        self.assertIn("github-ci-live-revalidation", self.check)
        self.assertIn("tests.test_github_ci_live_revalidation", runner)
        self.assertIn('"$DELIVERY_DIR/github-ci-evidence.json"', self.check)
        self.assertIn('"$commit"', self.check)
        self.assertIn('/usr/bin/python3 -I -B "$EVIDENCE_VALIDATOR" release', self.check)
        self.assertNotIn("TAIJI_CI_SKIP", self.check)
        self.assertLess(
            self.check.index("github-ci-live-revalidation"),
            self.check.index("openssl dgst -sha256 -verify"),
        )
        self.assertIn("revalidate-taiji-github-ci-evidence.py", self.signer)
        self.assertLess(
            self.signer.index("github-ci-live-revalidation"),
            self.signer.index("private_fingerprint="),
        )


if __name__ == "__main__":
    unittest.main()
