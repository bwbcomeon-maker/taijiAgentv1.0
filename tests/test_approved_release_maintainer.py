import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "packaging/linux/validate-approved-maintainer.py"


class ApprovedReleaseMaintainerTest(unittest.TestCase):
    def run_validator(self, path: Path, *extra: str):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), "--file", str(path), *extra],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_descriptor(self, root: Path, **overrides) -> Path:
        payload = {
            "schema": "taiji-approved-maintainer/v1",
            "maintainer": "Acme Product Support <support@acme.cn>",
        }
        payload.update(overrides)
        path = root / "approved-maintainer.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o644)
        return path

    def test_prints_and_exactly_matches_the_approved_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = self.write_descriptor(Path(tmp))
            printed = self.run_validator(descriptor, "--print")
            matched = self.run_validator(
                descriptor,
                "--expect",
                "Acme Product Support <support@acme.cn>",
            )

            self.assertEqual(printed.returncode, 0, printed.stderr)
            self.assertEqual(
                printed.stdout, "Acme Product Support <support@acme.cn>\n"
            )
            self.assertEqual(matched.returncode, 0, matched.stderr)

    def test_rejects_unapproved_or_placeholder_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            descriptor = self.write_descriptor(root)
            mismatch = self.run_validator(
                descriptor, "--expect", "Fake Release <fake@fake.cn>"
            )
            placeholder = self.write_descriptor(
                root, maintainer="Example Support <support@example.invalid>"
            )
            rejected_placeholder = self.run_validator(placeholder)

            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("does not match", mismatch.stderr)
            self.assertNotEqual(rejected_placeholder.returncode, 0)
            self.assertIn("placeholder", rejected_placeholder.stderr)

    def test_rejects_unknown_or_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = self.write_descriptor(root, customer="secret")
            result_unknown = self.run_validator(unknown)
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"schema":"taiji-approved-maintainer/v1",'
                '"maintainer":"One <one@acme.cn>",'
                '"maintainer":"Two <two@acme.cn>"}\n',
                encoding="utf-8",
            )
            duplicate.chmod(0o644)
            result_duplicate = self.run_validator(duplicate)

            self.assertNotEqual(result_unknown.returncode, 0)
            self.assertIn("unknown fields", result_unknown.stderr)
            self.assertNotEqual(result_duplicate.returncode, 0)
            self.assertIn("duplicate", result_duplicate.stderr)

    def test_rejects_symlink_hardlink_and_group_writable_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            descriptor = self.write_descriptor(root)
            symlink = root / "linked.json"
            symlink.symlink_to(descriptor)
            hardlink = root / "hardlinked.json"
            os.link(descriptor, hardlink)

            self.assertNotEqual(self.run_validator(symlink).returncode, 0)
            self.assertNotEqual(self.run_validator(hardlink).returncode, 0)

        with tempfile.TemporaryDirectory() as tmp:
            descriptor = self.write_descriptor(Path(tmp))
            descriptor.chmod(0o664)
            result = self.run_validator(descriptor)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("group/other writable", result.stderr)

    def test_build_and_preflight_bind_deb_to_the_formal_source_identity(self):
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(
            encoding="utf-8"
        )
        builder = (
            ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
        ).read_text(encoding="utf-8")
        preflight = (
            ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
        ).read_text(encoding="utf-8")

        for source in (build, builder, preflight):
            self.assertIn("approved-maintainer.json", source)
            self.assertIn("validate-approved-maintainer.py", source)
        self.assertIn('--expect "$PACKAGE_MAINTAINER"', build)
        self.assertIn('--expect "$PACKAGE_MAINTAINER"', builder)
        self.assertIn('dpkg-deb -f "$DEB_PATH" Maintainer', preflight)


if __name__ == "__main__":
    unittest.main()
