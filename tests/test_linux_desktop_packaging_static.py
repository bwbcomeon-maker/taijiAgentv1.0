import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class LinuxDesktopPackagingStaticTest(unittest.TestCase):
    def test_build_script_has_release_gates_for_electron_deb_and_desktop_entry(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn("verify_linux_electron_runtime", build)
        self.assertIn('ldd "$ELECTRON_BIN"', build)
        self.assertIn("desktop-file-validate", build)
        self.assertIn("scan_deb_release_artifact", build)
        for forbidden in ("LIBARCHIVE", "com.apple", "PaxHeaders", "SCHILY.xattr"):
            self.assertIn(forbidden, build)

    def test_deb_declares_electron_runtime_libraries_for_kylin_v10(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        expected_deps = (
            "libx11-6",
            "libxcomposite1",
            "libxdamage1",
            "libxext6",
            "libxfixes3",
            "libxrandr2",
            "libxrender1",
            "libxshmfence1",
            "libxcb1",
            "libcups2",
            "libdbus-1-3",
            "libglib2.0-0",
            "libatk1.0-0",
            "libatspi2.0-0",
        )
        for dep in expected_deps:
            self.assertIn(dep, build)

    def test_native_verify_checks_packaged_electron_runtime(self):
        verify = read_text("hermes-local-lab/scripts/taiji-native-verify")

        self.assertIn("Electron runtime exists", verify)
        self.assertIn("ldd", verify)
        self.assertIn("not found", verify)
        self.assertIn("desktop smoke test", verify)

    def test_setup_local_can_recover_from_stale_uv_lockfile_on_kylin_build_host(self):
        setup = read_text("hermes-local-lab/scripts/setup-local.sh")

        self.assertIn("TAIJI_UV_LOCK_MODE", setup)
        self.assertIn("strict", setup)
        self.assertIn("auto", setup)
        self.assertIn("uv sync --extra all --locked", setup)
        self.assertIn("uv sync --extra all", setup)
        self.assertIn("retrying without --locked", setup)

    def test_operator_doc_records_confirmed_kylin_target_and_offline_boundary(self):
        doc = read_text("docs/taiji-desktop-uos-packaging.md")

        self.assertIn("Kylin V10 SP1", doc)
        self.assertIn("glibc 2.31", doc)
        self.assertIn("离线优先", doc)
        self.assertIn("不内置模型", doc)
        self.assertIn("Node.js 10 / npm 6", doc)
        self.assertIn("TAIJI_UV_LOCK_MODE=strict", doc)


if __name__ == "__main__":
    unittest.main()
