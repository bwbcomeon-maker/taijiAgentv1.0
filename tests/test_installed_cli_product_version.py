import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "packaging/linux/bin/taiji"
PRODUCT_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


class InstalledCliProductVersionTest(unittest.TestCase):
    def _run_version(self, version_content: str | None, *, symlink: bool = False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "installed"
            root.mkdir()
            version = root / "VERSION"
            if symlink:
                target = Path(temporary) / "outside-version"
                target.write_text(version_content or "", encoding="utf-8")
                version.symlink_to(target)
            elif version_content is not None:
                version.write_text(version_content, encoding="utf-8")

            launcher = Path(temporary) / "taiji"
            source = CLI.read_text(encoding="utf-8").replace(
                'APP_ROOT="/opt/taiji-agent"', f'APP_ROOT="{root}"'
            )
            launcher.write_text(source, encoding="utf-8")
            launcher.chmod(0o755)
            return subprocess.run(
                ["/bin/bash", str(launcher), "--version"],
                text=True,
                capture_output=True,
                check=False,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            )

    def test_version_reads_the_packaged_product_version_without_starting_runtime(self):
        result = self._run_version(f"{PRODUCT_VERSION}\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"Taiji Agent {PRODUCT_VERSION}\n")
        source = CLI.read_text(encoding="utf-8")
        self.assertIn('PRODUCT_VERSION_FILE="$APP_ROOT/VERSION"', source)
        self.assertNotIn("taiji_runtime.main --version", source)

    def test_version_fails_closed_for_missing_malformed_or_symlinked_identity(self):
        for content, symlink in (
            (None, False),
            ("0.15.2-runtime\n", False),
            (f"{PRODUCT_VERSION}\n", True),
        ):
            with self.subTest(content=content, symlink=symlink):
                result = self._run_version(content, symlink=symlink)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("product version", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
