import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VersionedRuntimeDependsIntegrationTest(unittest.TestCase):
    def test_deb_control_uses_validated_target_versions_as_minimums(self):
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("render-depends", build)
        self.assertIn('--profile "$TARGET_BASELINE_SNAPSHOT"', build)
        self.assertIn('--depends-file "$RUNTIME_DEPENDS_FILE"', build)
        self.assertNotIn(
            "DEB_DEPENDS=\"$(awk 'NF && $1 !~ /^#/",
            build,
        )
        self.assertIn("Depends: $DEB_DEPENDS", build)


if __name__ == "__main__":
    unittest.main()
