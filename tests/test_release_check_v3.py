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
            "TAIJI_CERTIFICATION_CHALLENGE",
            "TAIJI_PUBLICATION_CHALLENGE",
            "certification",
            "release",
        ):
            self.assertIn(token, self.check)
        self.assertIn("taiji-release-evidence/v3", self.check)

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

    def test_signer_uses_independent_certification_and_publication_challenges(self):
        self.assertIn("TAIJI_CERTIFICATION_CHALLENGE", self.signer)
        self.assertIn("TAIJI_PUBLICATION_CHALLENGE", self.signer)
        self.assertIn("certification-set", self.signer)
        self.assertIn("release-evidence/v3", self.signer)


if __name__ == "__main__":
    unittest.main()
