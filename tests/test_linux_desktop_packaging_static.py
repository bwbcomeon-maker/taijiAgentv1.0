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

    def test_build_script_distinguishes_public_pem_from_private_keys(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn("scan_private_key_material", build)
        self.assertIn("BEGIN .*PRIVATE KEY", build)
        self.assertIn("-name '*.key'", build)
        self.assertIn("-name '.env'", build)
        self.assertNotIn("-name '*.pem' -o -name 'id_rsa'", build)

    def test_postinst_repairs_electron_chrome_sandbox_permissions(self):
        postinst = read_text("packaging/linux/deb/postinst")

        self.assertIn("chrome-sandbox", postinst)
        self.assertIn("chown root:root", postinst)
        self.assertIn("chmod 4755", postinst)

    def test_desktop_entry_uses_single_main_category(self):
        desktop = read_text("packaging/linux/taiji-agent.desktop")

        self.assertIn("Categories=Utility;", desktop)
        self.assertNotIn("Categories=Utility;Development;", desktop)

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

    def test_delivery_install_script_replaces_legacy_webui_package_safely(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn("taiji-agent-webui.service", install)
        self.assertIn("taiji-agent-gateway.service", install)
        self.assertIn("clean_reinstall_legacy_package", install)
        self.assertIn("systemctl disable", install)
        self.assertIn("apt-mark unhold taiji-agent", install)
        self.assertIn("apt-get purge -y taiji-agent", install)
        self.assertIn("dpkg --remove --force-remove-reinstreq taiji-agent", install)
        self.assertIn("dpkg --purge --force-all taiji-agent", install)
        self.assertIn("LEGACY_PROCESS_PATTERNS", install)
        self.assertIn("check_port_conflict", install)
        self.assertIn("--reinstall --allow-downgrades --allow-change-held-packages", install)

    def test_delivery_install_script_removes_legacy_runtime_without_backup(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        for forbidden in (
            "BACKUP_DIR",
            "backup_legacy_installation",
            "restore_active_legacy_services",
            "cleanup_stale_backup_temps",
            "tar -C / -czf",
            "旧版备份",
        ):
            self.assertNotIn(forbidden, install)

        prepare = install[
            install.index("prepare_legacy_replacement()"):
            install.index("install_package()", install.index("prepare_legacy_replacement()"))
        ]
        self.assertLess(prepare.index("check_port_conflict \"安装前\""), prepare.index("clean_reinstall_legacy_package"))
        self.assertLess(prepare.index("clean_reinstall_legacy_package"), prepare.index("check_port_conflict \"安装前清理后\""))

        clean = install[
            install.index("clean_reinstall_legacy_package()"):
            install.index("install_package()", install.index("clean_reinstall_legacy_package()"))
        ]
        self.assertLess(clean.index("stop_and_disable_legacy_services"), clean.index("stop_legacy_processes"))
        self.assertLess(clean.index("stop_legacy_processes"), clean.index("purge_legacy_package_state"))
        self.assertLess(clean.index("purge_legacy_package_state"), clean.index("remove_legacy_files"))
        self.assertLess(clean.index("remove_legacy_files"), clean.index("systemctl daemon-reload"))
        remove_files = install[
            install.index("remove_legacy_files()"):
            install.index("pid_uses_taiji_install_root()", install.index("remove_legacy_files()"))
        ]
        self.assertIn("remove_legacy_path /opt/taiji-agent", remove_files)


if __name__ == "__main__":
    unittest.main()
