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
        self.assertIn("validate_packaged_config_template", build)
        self.assertIn("config/taiji-default-config.yaml", build)
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
        self.assertIn("-m taiji_runtime.main --help", verify)
        self.assertIn("Taiji runtime module entrypoint works", verify)
        self.assertIn("verify_packaged_config", verify)
        self.assertIn("/api/model-config", verify)
        self.assertIn("/api/settings", verify)

    def test_desktop_runtime_does_not_depend_on_venv_console_script_shebang(self):
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        cli = read_text("packaging/linux/bin/taiji")
        health_check = read_text("hermes-local-lab/scripts/health-check.sh")
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn("-m taiji_runtime.main gateway run --accept-hooks", start_agent)
        self.assertNotIn('venv/bin/hermes" gateway run', start_agent)
        self.assertIn("-m taiji_runtime.main", cli)
        self.assertNotIn("venv/bin/hermes", cli)
        self.assertIn("-m taiji_runtime.main --help", health_check)
        self.assertIn("-m taiji_runtime.main --version", health_check)
        self.assertIn("-m taiji_runtime.main --help", build)

    def test_health_check_reads_user_dir_runtime_env_for_desktop_launches(self):
        health_check = read_text("hermes-local-lab/scripts/health-check.sh")

        self.assertIn('TAIJI_AGENT_USE_USER_DIRS:-0', health_check)
        self.assertIn('TAIJI_AGENT_RUNTIME_ENV:-$TMP_DIR/runtime.env', health_check)
        self.assertIn('TAIJI_AGENT_ENV_FILE:-$TAIJI_CONFIG_DIR/.env', health_check)

    def test_desktop_startup_errors_include_recent_script_output(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")

        self.assertIn("outputTail", main_js)
        self.assertIn("最近输出", main_js)
        self.assertIn("[${scriptName} error]", main_js)

    def test_runtime_start_scripts_use_configurable_timeouts_and_recent_log_tail(self):
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        start_webui = read_text("hermes-local-lab/scripts/start-webui.sh")

        self.assertIn('START_TIMEOUT_SECONDS="${TAIJI_AGENT_START_TIMEOUT:-90}"', start_agent)
        self.assertIn("Taiji Agent API startup requested", start_agent)
        self.assertIn("tail_recent_log", start_agent)
        self.assertIn("within ${START_TIMEOUT_SECONDS}s", start_agent)
        self.assertNotIn("for _ in $(seq 1 50)", start_agent)

        self.assertIn('START_TIMEOUT_SECONDS="${TAIJI_WEBUI_START_TIMEOUT:-60}"', start_webui)
        self.assertIn("Taiji WebUI startup requested", start_webui)
        self.assertIn("tail_recent_log", start_webui)
        self.assertIn("within ${START_TIMEOUT_SECONDS}s", start_webui)
        self.assertNotIn("for _ in $(seq 1 50)", start_webui)

    def test_linux_desktop_hides_application_menu_bar(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")

        self.assertIn('process.platform === "linux"', main_js)
        self.assertIn("Menu.setApplicationMenu(null)", main_js)
        self.assertIn("autoHideMenuBar", main_js)
        self.assertIn("taiji-agent-diagnose", main_js)

    def test_desktop_menu_preserves_standard_edit_roles_for_paste(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")

        self.assertIn('label: "编辑"', main_js)
        for role in ("undo", "redo", "cut", "copy", "paste", "pasteAndMatchStyle", "selectAll"):
            self.assertIn(f'role: "{role}"', main_js)
        self.assertLess(main_js.index('process.platform === "linux"'), main_js.index('label: "编辑"'))
        self.assertLess(main_js.index('label: "编辑"'), main_js.index("Menu.buildFromTemplate(template)"))

    def test_desktop_exposes_guarded_clipboard_read_for_webui_secret_paste(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")
        preload_js = read_text("apps/taiji-desktop/src/preload.js")

        self.assertIn("clipboard", main_js)
        self.assertIn('ipcMain.handle("taiji:read-clipboard-text"', main_js)
        self.assertIn("isAllowedDesktopMediaOrigin(senderUrl)", main_js)
        self.assertIn("clipboard.readText", main_js)
        self.assertIn("readClipboardText", preload_js)
        self.assertIn('ipcRenderer.invoke("taiji:read-clipboard-text")', preload_js)

    def test_desktop_defers_webui_gateway_key_to_start_webui_script(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")
        start_webui = read_text("hermes-local-lab/scripts/start-webui.sh")

        self.assertIn("env.API_SERVER_KEY = crypto.randomBytes", main_js)
        self.assertIn("env.TAIJI_WEBUI_GATEWAY_BASE_URL", main_js)
        self.assertNotIn("env.TAIJI_WEBUI_GATEWAY_API_KEY", main_js)
        self.assertIn(
            'TAIJI_WEBUI_GATEWAY_API_KEY="${TAIJI_WEBUI_GATEWAY_API_KEY:-$API_SERVER_KEY}"',
            start_webui,
        )

    def test_runtime_start_scripts_enable_product_license_gate(self):
        runtime_env = read_text("hermes-local-lab/scripts/runtime-env.sh")
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        start_webui = read_text("hermes-local-lab/scripts/start-webui.sh")
        main_js = read_text("apps/taiji-desktop/src/main.js")

        for text in (runtime_env, start_agent, start_webui, main_js):
            self.assertIn("TAIJI_LICENSE_FILE", text)
            self.assertIn("TAIJI_LICENSE_REQUIRED", text)
            self.assertNotIn("HERMES_LICENSE", text)
            self.assertNotIn("HERMES_LICENSE_FILE", text)

        self.assertIn('$TAIJI_CONFIG_DIR/license.jwt', runtime_env)
        self.assertIn('TAIJI_LICENSE_REQUIRED="${TAIJI_LICENSE_REQUIRED:-1}"', start_agent)
        self.assertIn('TAIJI_LICENSE_REQUIRED="${TAIJI_LICENSE_REQUIRED:-1}"', start_webui)

    def test_packaging_never_embeds_customer_license_or_private_key_inputs(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn("scan_private_key_material", build)
        self.assertIn("license.jwt", build)
        self.assertIn("TAIJI_LICENSE_PRIVATE_KEY", build)
        self.assertNotIn("cp \"$ROOT_DIR/license.jwt\"", build)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", build)

    def test_packaged_runtime_uses_product_layout_and_sourceless_python(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn('AGENT_RUNTIME="$INSTALL_ROOT/runtime/agent"', build)
        self.assertIn('WEB_RUNTIME="$INSTALL_ROOT/runtime/web"', build)
        self.assertIn("stage_python_runtime", build)
        self.assertIn("compile_sourceless_python", build)
        self.assertIn("scan_product_privacy", build)
        self.assertNotIn('"$LAB_DIR"/ "$INSTALL_ROOT"/', build)

    def test_packaged_launch_surface_has_no_hermes_visible_tokens(self):
        paths = [
            "hermes-local-lab/scripts/runtime-env.sh",
            "hermes-local-lab/scripts/start-agent.sh",
            "hermes-local-lab/scripts/start-webui.sh",
            "hermes-local-lab/scripts/stop-all.sh",
            "hermes-local-lab/scripts/taiji-native-verify",
            "hermes-local-lab/scripts/taiji-agent-diagnose",
            "packaging/linux/bin/taiji",
            "packaging/linux/bin/taiji-agent",
            "packaging/linux/bin/taiji-agent-diagnose",
            "packaging/linux/deb/prerm",
            "apps/taiji-desktop/src/main.js",
        ]
        forbidden = ("hermes", "HERMES_", "hermes_cli", "hermes-agent", "hermes-webui", "hermes-home")
        for path in paths:
            text = read_text(path)
            lowered = text.lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), lowered, f"{token} leaked in {path}")

    def test_stop_all_cleans_legacy_pid_files_without_visible_legacy_tokens(self):
        stop_all = read_text("hermes-local-lab/scripts/stop-all.sh")
        lowered = stop_all.lower()

        self.assertIn("legacy_pid_files", stop_all)
        self.assertIn("pid_uses_managed_runtime", stop_all)
        self.assertIn("process_command", stop_all)
        self.assertIn("not managed by this Taiji runtime", stop_all)
        self.assertIn("lsof", stop_all)
        for forbidden in ("hermes-agent.pid", "hermes-webui.pid", "hermes_cli.main"):
            self.assertNotIn(forbidden, lowered)

    def test_api_server_public_health_and_capability_payloads_use_product_brand(self):
        api_server = read_text("hermes-local-lab/sources/hermes-agent/gateway/platforms/api_server.py")

        self.assertIn('"platform": "taiji-agent"', api_server)
        self.assertIn('"owned_by": "taiji"', api_server)
        self.assertIn('"object": "taiji.api_server.capabilities"', api_server)
        self.assertNotIn('"platform": "hermes-agent"', api_server)
        self.assertNotIn('"owned_by": "hermes"', api_server)
        self.assertNotIn('"object": "hermes.api_server.capabilities"', api_server)

    def test_webui_gateway_error_surface_uses_product_copy(self):
        gateway_chat = read_text("hermes-local-lab/sources/hermes-webui/api/gateway_chat.py")
        http_error = gateway_chat[
            gateway_chat.index("def _gateway_http_error_event"):
            gateway_chat.index("def _gateway_sse_delta")
        ]
        empty_response = gateway_chat[
            gateway_chat.index("if not assistant_text:"):
            gateway_chat.index("with _get_session_agent_lock", gateway_chat.index("if not assistant_text:"))
        ]

        for text in (http_error, empty_response):
            lowered = text.lower()
            self.assertIn("太极", text)
            self.assertNotIn("hermes", lowered)
            self.assertNotIn("gateway returned no assistant message", lowered)
            self.assertNotIn("hermes_webui_gateway_api_key", lowered)

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
        self.assertIn("新版桌面端会自动选择空闲端口", install)
        self.assertIn("03_目标终端_导出诊断报告.sh", install)

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

    def test_diagnose_entrypoints_are_packaged_and_delivery_script_exists(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        launcher = read_text("packaging/linux/bin/taiji-agent-diagnose")
        diagnose = read_text("hermes-local-lab/scripts/taiji-agent-diagnose")
        delivery = read_text("taijiagent 打包交付/03_目标终端_导出诊断报告.sh")

        self.assertIn("taiji-agent-diagnose", build)
        self.assertIn("scripts/taiji-agent-diagnose", launcher)
        self.assertIn("TAIJI_AGENT_USE_USER_DIRS", launcher)
        self.assertIn("redact_stream", diagnose)
        self.assertIn("/api/model-config", diagnose)
        self.assertIn("诊断报告", delivery)


if __name__ == "__main__":
    unittest.main()
