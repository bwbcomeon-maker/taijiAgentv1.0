from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
HELPER = ROOT / "packaging/linux/builder-input-package.py"


class BuilderInputConsumerGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = BUILDER.read_text(encoding="utf-8")

    def test_builder_pins_and_invokes_the_reviewed_input_verifier(self) -> None:
        helper_sha = hashlib.sha256(HELPER.read_bytes()).hexdigest()
        match = re.search(
            r'^BUILDER_INPUT_HELPER_SHA256="([0-9a-f]{64})"$',
            self.builder,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), helper_sha)
        self.assertIn('BUILDER_INPUT_HELPER="$SCRIPT_DIR/builder-input-package.py"', self.builder)
        self.assertIn('"$BUILDER_INPUT_HELPER" verify', self.builder)
        for argument in (
            '--archive "$input_archive"',
            '--manifest "$input_manifest"',
            '--checksum "$input_checksum"',
            '--extracted-dir "$SCRIPT_DIR"',
        ):
            self.assertIn(argument, self.builder)

    def test_builder_rejects_nonunique_or_cross_commit_input_triplets(self) -> None:
        function = self.builder.split("verify_builder_input_package() {", 1)[1].split(
            "archive_previous_build_outputs() {", 1
        )[0]
        self.assertIn("taijiagent-制包机输入-$source_commit.tar.gz", function)
        self.assertIn("taijiagent-制包机输入-$source_commit.manifest.json", function)
        self.assertIn('expected = {archive.name, manifest.name, checksum.name}', function)
        self.assertIn('if len(candidates) != 3 or {path.name for path in candidates} != expected:', function)

    def test_input_verification_runs_after_python_install_and_before_source_unpack(self) -> None:
        main = self.builder.split("main() {", 1)[1]
        self.assertLess(main.index("install_build_dependencies"), main.index("verify_builder_input_package"))
        self.assertLess(main.index("verify_builder_input_package"), main.index("prepare_source_release"))
        self.assertLess(main.index("verify_builder_input_package"), main.index("unpack_source"))


if __name__ == "__main__":
    unittest.main()
