import hashlib
import os
import shutil
import subprocess
import tempfile
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

        self.assertIn("set +e", verify)
        self.assertIn("set +o pipefail", verify)
        self.assertIn("Electron runtime exists", verify)
        self.assertIn("ldd", verify)
        self.assertIn("not found", verify)
        self.assertIn("desktop smoke test", verify)
        self.assertIn("-m taiji_runtime.main --help", verify)
        self.assertIn("Taiji runtime module entrypoint works", verify)
        self.assertIn("verify_agent_runtime_imports", verify)
        self.assertIn("plugins.memory", verify)
        self.assertIn("plugins.context_engine", verify)
        self.assertIn("Agent runtime plugin modules are importable", verify)
        self.assertIn("verify_packaged_config", verify)
        self.assertIn("printf '000\\n'", verify)
        self.assertIn("/api/model-config", verify)
        self.assertIn("/api/settings", verify)

    def test_native_verify_closed_health_ports_do_not_abort_under_inherited_errexit(self):
        env = os.environ.copy()
        env.update(
            {
                "SHELLOPTS": "errexit:pipefail",
                "AGENT_API_PORT": "9",
                "WEBUI_PORT": "10",
                "TAIJI_VERIFY_DESKTOP_SMOKE": "0",
            }
        )

        result = subprocess.run(
            ["bash", str(ROOT / "hermes-local-lab/scripts/taiji-native-verify")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Agent health not reachable", output)
        self.assertIn("WebUI health not reachable", output)
        self.assertIn("Summary:", output)

    def test_desktop_runtime_does_not_depend_on_venv_console_script_shebang(self):
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        local_cli = read_text("hermes-local-lab/scripts/taiji")
        cli = read_text("packaging/linux/bin/taiji")
        health_check = read_text("hermes-local-lab/scripts/health-check.sh")
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn("-m taiji_runtime.main gateway run --accept-hooks", start_agent)
        self.assertNotIn('venv/bin/hermes" gateway run', start_agent)
        self.assertIn('source "$RUNTIME_ENV"', local_cli)
        self.assertIn('SOURCE_PATH="${BASH_SOURCE[0]}"', local_cli)
        self.assertIn('while [ -L "$SOURCE_PATH" ]', local_cli)
        self.assertIn('readlink "$SOURCE_PATH"', local_cli)
        self.assertIn('TAIJI_AGENT_USE_USER_DIRS="${TAIJI_AGENT_USE_USER_DIRS:-1}"', local_cli)
        self.assertIn("print_taiji_version", local_cli)
        self.assertIn("--version|-V|version", local_cli)
        self.assertIn('cd "$AGENT_DIR"', local_cli)
        self.assertIn("-m taiji_runtime.main", local_cli)
        self.assertNotIn("venv/bin/hermes", local_cli)
        self.assertIn("print_taiji_version", cli)
        self.assertIn("--version|-V|version", cli)
        self.assertIn('cd "$APP_ROOT/runtime/agent"', cli)
        self.assertIn("-m taiji_runtime.main", cli)
        self.assertNotIn("venv/bin/hermes", cli)
        self.assertIn("-m taiji_runtime.main --help", health_check)
        self.assertIn("-m taiji_runtime.main --version", health_check)
        self.assertIn("-m taiji_runtime.main --help", build)

    def test_health_check_reads_user_dir_runtime_env_for_desktop_launches(self):
        health_check = read_text("hermes-local-lab/scripts/health-check.sh")
        runtime_env = read_text("hermes-local-lab/scripts/runtime-env.sh")
        main_js = read_text("apps/taiji-desktop/src/main.js")

        self.assertIn('"$LAB_DIR/runtime/agent"', health_check)
        self.assertIn('"$LAB_DIR/runtime/web"', health_check)
        self.assertIn('server.pyc', health_check)
        self.assertIn("Taiji Agent runtime exists", health_check)
        self.assertIn("Taiji WebUI runtime exists", health_check)
        self.assertNotIn("pyproject.toml", health_check)
        self.assertNotIn("Taiji Agent source missing", health_check)
        self.assertNotIn("Taiji WebUI source missing", health_check)
        self.assertIn('TAIJI_AGENT_USE_USER_DIRS:-0', health_check)
        self.assertIn('TAIJI_AGENT_RUNTIME_ENV:-$TMP_DIR/runtime.env', health_check)
        self.assertIn('TAIJI_ENV_FILE="$TAIJI_RUNTIME_HOME/.env"', health_check)
        self.assertIn('TAIJI_ENV_FILE="$TAIJI_RUNTIME_HOME/.env"', runtime_env)
        self.assertNotIn("${TAIJI_AGENT_ENV_FILE", health_check)
        self.assertNotIn("${TAIJI_AGENT_ENV_FILE", runtime_env)
        self.assertIn("TAIJI_IGNORED_RUNTIME_SELECTOR_COUNT", runtime_env)
        self.assertIn("ignored_legacy_runtime_selectors=", read_text("hermes-local-lab/scripts/taiji-agent-diagnose"))
        diagnose = read_text("hermes-local-lab/scripts/taiji-agent-diagnose")
        self.assertIn("canonical_env.exists=", diagnose)
        self.assertIn("deepseek_key.canonical.suffix=", diagnose)
        self.assertIn("legacy_runtime_differs=", diagnose)
        self.assertIn("env.TAIJI_RUNTIME_HOME", main_js)
        self.assertIn('path.join(userDataDir(), "runtime-home")', main_js)

    def test_desktop_allows_isolated_user_data_for_playwright_app_smoke(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")

        self.assertIn("TAIJI_DESKTOP_USER_DATA_DIR", main_js)
        self.assertIn('app.setPath("userData"', main_js)
        self.assertLess(
            main_js.index("app.setPath(\"userData\""),
            main_js.index("app.requestSingleInstanceLock()"),
        )

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

    def test_runtime_start_output_does_not_print_internal_access_addresses(self):
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        start_webui = read_text("hermes-local-lab/scripts/start-webui.sh")

        for script in (start_agent, start_webui):
            self.assertNotIn("ready at http://", script)
            self.assertNotIn("did not become healthy at $health_url", script)
            self.assertNotIn("Log: $LOG_FILE", script)
            self.assertIn("service ready", script)
            self.assertIn("did not become healthy within", script)

    def test_linux_desktop_hides_application_menu_bar(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")

        self.assertIn('process.platform === "linux"', main_js)
        self.assertIn("Menu.setApplicationMenu(null)", main_js)
        self.assertIn("autoHideMenuBar", main_js)
        self.assertIn("taiji-agent-diagnose", main_js)

    def test_desktop_web_access_uses_private_token_and_sanitized_logs(self):
        main_js = read_text("apps/taiji-desktop/src/main.js")
        server_py = read_text("hermes-local-lab/sources/hermes-webui/server.py")

        self.assertIn("TAIJI_DESKTOP_ONLY", main_js)
        self.assertIn("TAIJI_DESKTOP_ACCESS_TOKEN", main_js)
        self.assertIn("taiji_desktop_token", main_js)
        self.assertIn('appendDesktopLog(desktopLog, "loading desktop workspace")', main_js)
        self.assertNotIn("loading ${target.toString()}", main_js)

        self.assertIn("def _desktop_access_required", server_py)
        self.assertIn("def _request_has_desktop_access", server_py)
        self.assertIn("请从桌面应用启动太极 Agent", server_py)
        self.assertNotIn("Then open:", server_py)
        self.assertNotIn("Remote access:", server_py)

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
            self.assertIn("TAIJI_LICENSE_STATE_FILE", text)
            self.assertIn("TAIJI_LICENSE_REQUIRED", text)
            self.assertIn("TAIJI_LICENSE_MACHINE_BINDING_REQUIRED", text)
            self.assertNotIn("HERMES_LICENSE", text)
            self.assertNotIn("HERMES_LICENSE_FILE", text)

        self.assertIn('$TAIJI_CONFIG_DIR/license.jwt', runtime_env)
        self.assertIn('$TAIJI_STATE_DIR/license-state.json', runtime_env)
        self.assertIn('TAIJI_LICENSE_MACHINE_BINDING_REQUIRED="${TAIJI_LICENSE_MACHINE_BINDING_REQUIRED:-1}"', runtime_env)
        self.assertIn('TAIJI_LICENSE_REQUIRED="${TAIJI_LICENSE_REQUIRED:-1}"', start_agent)
        self.assertIn('TAIJI_LICENSE_REQUIRED="${TAIJI_LICENSE_REQUIRED:-1}"', start_webui)

    def test_packaging_never_embeds_customer_license_or_private_key_inputs(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        gitignore = read_text(".gitignore")

        self.assertIn("scan_private_key_material", build)
        self.assertIn("license.jwt", build)
        self.assertIn("TAIJI_LICENSE_PRIVATE_KEY", build)
        self.assertNotIn("cp \"$ROOT_DIR/license.jwt\"", build)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", build)
        self.assertNotIn("taiji-license-issuer", build)
        self.assertIn("tools/taiji-license-issuer/private/signing-private.pem", gitignore)
        self.assertIn("tools/taiji-license-issuer/*.jwt", gitignore)
        self.assertIn("tools/taiji-license-issuer/*.zip", gitignore)
        self.assertIn("tools/taiji-license-issuer/taiji-machine-request*.json", gitignore)

    def test_packaged_runtime_uses_product_layout_and_sourceless_python(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn('AGENT_RUNTIME="$INSTALL_ROOT/runtime/agent"', build)
        self.assertIn('WEB_RUNTIME="$INSTALL_ROOT/runtime/web"', build)
        self.assertIn("stage_python_runtime", build)
        self.assertIn("compile_sourceless_python", build)
        self.assertIn("scan_product_privacy", build)
        self.assertNotIn('"$LAB_DIR"/ "$INSTALL_ROOT"/', build)

    def test_packaged_agent_runtime_keeps_importable_plugin_package(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        stage_start = build.index("stage_python_runtime()")
        stage = build[stage_start:build.index("rename_internal_agent_modules", stage_start)]
        agent_copy = stage[:stage.index('"$SOURCE_AGENT_DIR"/ "$AGENT_RUNTIME"/')]

        self.assertNotIn("--exclude 'plugins'", agent_copy)
        self.assertIn("--exclude 'plugins/hermes-achievements'", agent_copy)
        self.assertIn("--exclude 'plugins/kanban/systemd'", agent_copy)
        self.assertIn("--exclude 'plugins/security-guidance'", agent_copy)
        self.assertIn("scan_product_privacy", build)

    def test_packaged_agent_runtime_excludes_upstream_helper_scripts(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        stage_start = build.index("stage_python_runtime()")
        stage = build[stage_start:build.index("rename_internal_agent_modules", stage_start)]
        agent_copy = stage[:stage.index('"$SOURCE_AGENT_DIR"/ "$AGENT_RUNTIME"/')]

        self.assertIn("--exclude 'scripts'", agent_copy)
        self.assertIn("scan_product_privacy", build)
        self.assertNotIn("scripts/hermes-gateway", build)

    def test_packaged_runtime_excludes_dev_templates_and_repairs_venv_paths(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        for expected in (
            "--exclude '.env.example'",
            "--exclude '.env.docker.example'",
            "--exclude '*.example'",
            "--exclude '.dockerignore'",
            "--exclude '.gitignore'",
            "--exclude 'Dockerfile'",
            "--exclude 'docker-compose*'",
            "--exclude 'datagen-config-examples'",
            "--exclude 'flake.*'",
            "--exclude 'MANIFEST.in'",
            "--exclude 'uv.lock'",
            "--exclude 'package*.json'",
            "--exclude 'pyproject.toml'",
            "--exclude 'ctl.sh'",
            "--exclude 'start.sh'",
            "--exclude 'LICENSE'",
        ):
            self.assertIn(expected, build)

        self.assertIn("repair_packaged_venv_paths", build)
        self.assertIn("SOURCE_VENV", build)
        self.assertIn("/opt/taiji-agent/runtime/agent/venv", build)
        self.assertIn('"$AGENT_RUNTIME/venv/bin"', build)
        self.assertIn('"$AGENT_RUNTIME/venv/pyvenv.cfg"', build)
        self.assertIn("-path \"$AGENT_RUNTIME/venv/lib*\" -prune", build)

    def test_packaged_launch_surface_has_no_hermes_visible_tokens(self):
        paths = [
            "hermes-local-lab/scripts/runtime-env.sh",
            "hermes-local-lab/scripts/start-agent.sh",
            "hermes-local-lab/scripts/start-webui.sh",
            "hermes-local-lab/scripts/stop-all.sh",
            "hermes-local-lab/scripts/taiji",
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

    def test_setup_local_installs_user_taiji_launcher(self):
        setup = read_text("hermes-local-lab/scripts/setup-local.sh")

        self.assertIn('TAIJI_USER_BIN="${TAIJI_USER_BIN:-$HOME/.local/bin}"', setup)
        self.assertIn('ln -sfn "$LAB_DIR/scripts/taiji" "$TAIJI_USER_BIN/taiji"', setup)
        self.assertIn('hash -r', setup)
        self.assertIn('$TAIJI_USER_BIN/taiji status', setup)
        self.assertNotIn('venv/bin/hermes" "$@"', setup)

    def test_operator_doc_records_confirmed_kylin_target_and_offline_boundary(self):
        doc = read_text("docs/taiji-desktop-uos-packaging.md")

        self.assertIn("Kylin V10 SP1", doc)
        self.assertIn("glibc 2.31", doc)
        self.assertIn("离线优先", doc)
        self.assertIn("不内置模型", doc)
        self.assertIn("Node.js 10 / npm 6", doc)
        self.assertIn("TAIJI_UV_LOCK_MODE=auto", doc)
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

    def test_delivery_install_script_supports_fully_offline_local_apt_repo(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn("OFFLINE_REPO", install)
        self.assertIn("Packages.gz", install)
        self.assertIn("file:", install)
        self.assertIn("Dir::Etc::sourcelist", install)
        self.assertIn("install_taiji_package", install)
        self.assertIn("prepare_offline_apt_repo_source_path", install)
        self.assertIn("/tmp/taiji-agent-offline-repo.XXXXXX", install)
        self.assertIn('ln -s "$repo_path" "$OFFLINE_APT_REPO_MOUNT/repo"', install)
        self.assertIn("OFFLINE_APT_REPO_SOURCE", install)
        self.assertNotIn('printf \'deb [trusted=yes] file:%s ./\\n\' "$repo_path"', install)
        self.assertIn("apt-get update", install)
        self.assertIn("dpkg-scanpackages", builder)
        self.assertIn("apt-get download", builder)
        self.assertIn("build_offline_dependency_repo", builder)
        self.assertIn("git archive", builder)

    def test_delivery_release_preflight_is_a_hard_gate(self):
        preflight_path = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
        self.assertTrue(preflight_path.exists())

        preflight = preflight_path.read_text(encoding="utf-8")
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        docs = read_text("taijiagent 打包交付/操作说明.md")
        gitignore = read_text(".gitignore")

        self.assertIn("run_release_preflight", builder)
        self.assertIn("01_制包机_发布预检.sh", builder)
        main_body = builder[builder.index("main() {") :]
        self.assertLess(
            main_body.index("install_build_dependencies"),
            main_body.index("prepare_source_release"),
        )
        preflight_body = builder[
            builder.index("preflight() {") : builder.index("prepare_source_release() {")
        ]
        self.assertNotIn("require_cmd git", preflight_body)
        self.assertNotIn("require_cmd dpkg-scanpackages", preflight_body)
        self.assertIn("01_制包机_发布预检.sh", docs)
        self.assertIn("!/taijiagent 打包交付/01_制包机_发布预检.sh", gitignore)
        self.assertIn("99_本机_准备制包输入包.sh", docs)
        self.assertIn("!/taijiagent 打包交付/99_本机_准备制包输入包.sh", gitignore)
        self.assertIn("/taijiagent-制包机输入-*.tar.gz", gitignore)
        self.assertIn('git -C "$REPO_ROOT" diff --quiet', preflight)
        self.assertIn('git -C "$REPO_ROOT" diff --cached --quiet', preflight)
        self.assertIn("taiji-agentv1.0-kylin-build-src-*.tar.gz", preflight)
        self.assertIn("SHA256SUMS.txt", preflight)
        self.assertIn("生成的安装包", preflight)
        self.assertIn("离线依赖", preflight)
        self.assertIn("Packages.gz", preflight)
        self.assertIn("taiji-package-manifest.json", preflight)
        self.assertIn("__MACOSX", preflight)
        self.assertIn(".DS_Store", preflight)
        self.assertIn("._*", preflight)
        self.assertIn("将自动清理", preflight)
        self.assertIn("rm -rf --", preflight)
        self.assertIn("TAIJI_RELEASE_REQUIRE_ARTIFACTS", preflight)

    def test_delivery_scripts_have_failure_diagnostics_and_admin_preflight(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")
        prepare = read_text("taijiagent 打包交付/99_本机_准备制包输入包.sh")
        docs = read_text("taijiagent 打包交付/操作说明.md")

        for script in (builder, install):
            self.assertIn("write_failure_diagnostic", script)
            self.assertIn("failure_next_steps", script)
            self.assertIn("write_environment_snapshot", script)
            self.assertIn("失败诊断-", script)
            self.assertIn("CURRENT_STAGE", script)
            self.assertIn("require_admin_capability", script)
            self.assertIn("sudo -v", script)
            self.assertIn("sudo -n true", script)

        self.assertIn("taijiagent-制包机输入-", prepare)
        self.assertIn("tarfile.USTAR_FORMAT", prepare)
        self.assertIn("PaxHeaders", prepare)
        self.assertIn("._", prepare)
        self.assertIn("失败诊断", docs)

    def test_release_preflight_cleans_macos_copy_metadata(self):
        if not shutil.which("sha256sum"):
            self.skipTest("sha256sum is required by release preflight")

        source_script = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
        with tempfile.TemporaryDirectory() as tmp:
            delivery = Path(tmp) / "taijiagent 打包交付"
            delivery.mkdir()
            script = delivery / "01_制包机_发布预检.sh"
            shutil.copy2(source_script, script)

            archive = delivery / "taiji-agentv1.0-kylin-build-src-test.tar.gz"
            archive.write_bytes(b"fake source archive\n")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (delivery / "SHA256SUMS.txt").write_text(
                f"{digest}  {archive.name}\n",
                encoding="utf-8",
            )

            (delivery / "._01_制包机_发布预检.sh").write_text("metadata", encoding="utf-8")
            (delivery / ".DS_Store").write_text("metadata", encoding="utf-8")
            apple_dir = delivery / "__MACOSX"
            apple_dir.mkdir()
            (apple_dir / "._payload").write_text("metadata", encoding="utf-8")

            result = subprocess.run(
                ["bash", str(script)],
                cwd=delivery,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertIn("将自动清理", result.stdout)
            self.assertFalse((delivery / "._01_制包机_发布预检.sh").exists())
            self.assertFalse((delivery / ".DS_Store").exists())
            self.assertFalse(apple_dir.exists())

    def test_offline_builder_generates_manifest_and_does_not_refresh_lock_by_default(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn("MANIFEST_FILE", builder)
        self.assertIn("taiji-package-manifest.json", builder)
        self.assertIn("write_release_manifest", builder)
        self.assertIn("packages_gz_sha256", builder)
        self.assertIn("build_glibc", builder)
        self.assertIn("target_matrix", builder)
        self.assertIn("support_boundary", builder)
        self.assertIn("TAIJI_ALLOW_UV_LOCK_REFRESH", builder)
        self.assertIn('uv_lock_mode="${TAIJI_UV_LOCK_MODE:-auto}"', builder)
        self.assertIn('run_setup_local "$uv_lock_mode"', builder)
        self.assertIn('TAIJI_UV_LOCK_MODE="$uv_lock_mode" ./scripts/setup-local.sh', builder)
        self.assertIn("Python 依赖 lock 漂移", builder)
        self.assertNotIn("TAIJI_UV_LOCK_MODE=strict ./scripts/setup-local.sh", builder)
        self.assertNotIn("\n  uv lock\n", builder)
        self.assertIn('printf \'%s  %s\\n\' "$deb_sha" "$deb_name" > "$OUTPUT_DIR/$checksum_name"', builder)

        manifest_body = builder[
            builder.index("write_release_manifest() {"):
            builder.index("write_build_report() {")
        ]
        self.assertIn("json_escape", builder)
        self.assertIn("json_string", builder)
        self.assertIn("Permission denied by kysec", builder)
        self.assertLess(builder.index('*"kysec"*'), builder.index('*"源码包"*'))
        self.assertNotIn('|*"commit"*)', builder)
        self.assertNotIn("python3 - <<'PY'", manifest_body)
        self.assertNotIn("Path(os.environ", manifest_body)
        self.assertNotIn("write_text", manifest_body)
        self.assertIn('} > "$MANIFEST_FILE"', manifest_body)

        self.assertIn("MANIFEST_PATH", install)
        self.assertIn("validate_release_manifest", install)
        self.assertIn("manifest", install)
        self.assertIn("packages_gz_sha256", install)
        self.assertIn("verify_deb_checksum", install)
        self.assertNotIn('sha256sum -c "$(basename "$CHECKSUM_PATH")"', install)

    def test_delivery_install_script_requires_offline_repo_unless_explicitly_online(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn('ONLINE_OK="${ONLINE_OK:-0}"', install)
        self.assertIn("缺少离线依赖仓库", install)
        self.assertIn("ONLINE_OK=1", install)
        self.assertIn("完全离线发布包", install)

    def test_install_script_uses_tmp_apt_lists_for_offline_repo(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn("/tmp/taiji-agent-apt-lists.XXXXXX", install)
        self.assertIn("OFFLINE_APT_LISTS_DIR", install)
        self.assertIn("Dir::State::Lists=$lists_dir", install)
        self.assertNotIn('lists_dir="$LOG_DIR/apt-lists"', install)

    def test_build_report_avoids_tr_pipefail_for_apt_sources(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn("apt_source_summary", builder)
        self.assertNotIn("tr '\\n' '; '", builder)

    def test_offline_builder_uses_ascii_tmp_build_root_and_repairs_source_permissions(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn('DEFAULT_BUILD_ROOT="/tmp/taiji-agent-build-$(id -u 2>/dev/null || printf user)"', builder)
        self.assertIn('BUILD_ROOT="${TAIJI_BUILD_ROOT:-$DEFAULT_BUILD_ROOT}"', builder)
        self.assertNotIn('BUILD_ROOT="$SCRIPT_DIR/构建工作区"', builder)
        self.assertIn("reset_build_root", builder)
        self.assertIn("repair_build_tree_permissions", builder)
        self.assertIn("chmod -R u+rwX,go+rX", builder)
        self.assertIn("pyproject.toml", builder)
        self.assertIn("Permission denied", builder)
        self.assertIn("run_setup_local", builder)
        self.assertIn("setup-local-", builder)

        unpack = builder[builder.index("unpack_source() {") : builder.index("npm_ci_with_network_fallback() {")]
        self.assertLess(unpack.index("reset_build_root"), unpack.index('tar -xzf "$SRC_ARCHIVE"'))
        self.assertLess(unpack.index('tar -xzf "$SRC_ARCHIVE"'), unpack.index("repair_build_tree_permissions"))

    def test_build_script_audits_final_deb_payload_and_webui_offline_assets(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn("scan_webui_offline_assets", build)
        self.assertIn(r"cdn\.jsdelivr\.net", build)
        self.assertIn(r"unpkg\.com", build)
        self.assertIn(r"cdnjs\.cloudflare\.com", build)
        self.assertIn("--include='*.mjs'", build)
        self.assertIn("vendor/xterm/5.3.0/xterm.css", build)
        self.assertIn("vendor/prismjs/1.29.0/prism.min.js", build)
        self.assertIn("vendor/pdfjs-dist/4.9.155/pdf.min.mjs", build)
        self.assertIn("vendor/mermaid/10.9.3/mermaid.min.js", build)
        self.assertIn("audit_deb_payload", build)
        self.assertIn("dpkg-deb -c", build)
        for required in (
            "./opt/taiji-agent/runtime/agent/venv/bin/python",
            "./opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron",
            "./opt/taiji-agent/runtime/web/server.pyc",
            "./opt/taiji-agent/scripts/taiji-native-verify",
            "./usr/share/applications/taiji-agent.desktop",
            "./usr/bin/taiji",
            "./usr/bin/taiji-agent",
        ):
            self.assertIn(required, build)
        self.assertIn('out_deb_name="$(basename "$OUT_DEB")"', build)
        self.assertIn('sha256sum "$out_deb_name" > "$out_deb_name.sha256"', build)

    def test_webui_runtime_assets_are_local_for_offline_target(self):
        static_root = ROOT / "hermes-local-lab/sources/hermes-webui/static"
        checked = {}
        for path in sorted(static_root.rglob("*")):
            if path.suffix not in {".html", ".js", ".css", ".mjs"}:
                continue
            rel = path.relative_to(static_root).as_posix()
            checked[rel] = path.read_text(encoding="utf-8")

        for path, text in checked.items():
            for forbidden in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com"):
                self.assertNotIn(forbidden, text, path)
            self.assertNotIn("-taiji-shell-", text, path)

        index = checked["index.html"]
        ui = checked["ui.js"]
        terminal = checked["terminal.js"]
        index_assets = (
            "static/vendor/xterm/5.3.0/xterm.css",
            "static/vendor/xterm/5.3.0/xterm.js",
            "static/vendor/xterm-addon-fit/0.8.0/xterm-addon-fit.js",
            "static/vendor/xterm-addon-web-links/0.9.0/xterm-addon-web-links.js",
            "static/vendor/prismjs/1.29.0/themes/prism-tomorrow.min.css",
            "static/vendor/prismjs/1.29.0/prism.min.js",
        )
        for local_asset in index_assets:
            self.assertIn(local_asset, index)

        for local_asset in (
            *index_assets,
            "static/vendor/pdfjs-dist/4.9.155/pdf.min.mjs",
            "static/vendor/pdfjs-dist/4.9.155/pdf.worker.min.mjs",
            "static/vendor/mermaid/10.9.3/mermaid.min.js",
        ):
            self.assertTrue((static_root / local_asset.removeprefix("static/")).exists(), local_asset)
        self.assertIn("static/vendor/pdfjs-dist/4.9.155/pdf.min.mjs", ui)
        self.assertIn("static/vendor/pdfjs-dist/4.9.155/pdf.worker.min.mjs", ui)
        self.assertIn("static/vendor/mermaid/10.9.3/mermaid.min.js", ui)
        self.assertIn("本地静态资源", terminal)

    def test_offline_builder_normalizes_source_checksum_paths(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn("checksum_source_archive_hash", builder)
        self.assertIn("verify_source_archive_checksum", builder)
        self.assertIn('archive_name="$(basename "$SRC_ARCHIVE")"', builder)
        self.assertIn('printf \'%s  %s\\n\' "$actual" "$archive_name" > "$CHECKSUM_FILE"', builder)
        self.assertIn('verify_source_archive_checksum', builder)
        self.assertIn("length(hash) != 64", builder)
        self.assertNotIn("[[:xdigit:]]{64}", builder)
        self.assertNotIn("sha256sum -c SHA256SUMS.txt", builder)

    def test_offline_builder_checksum_parser_accepts_prefixed_paths(self):
        if shutil.which("sha256sum") is None:
            self.skipTest("sha256sum is required for the shell-level checksum parser check")

        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        builder = builder.replace('\nmain "$@"\n', '\n# main disabled for parser test\n')
        archive_name = "taiji-agentv1.0-kylin-build-src-test123.tar.gz"
        payload = b"payload\n"
        expected = hashlib.sha256(payload).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "builder.sh").write_text(builder, encoding="utf-8")
            (tmp_path / archive_name).write_bytes(payload)
            (tmp_path / "SHA256SUMS.txt").write_text(
                f"{expected}  taijiagent 打包交付/{archive_name}\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        "source ./builder.sh; "
                        "resolve_source_archive; "
                        "verify_source_archive_checksum; "
                        'printf "SRC_ARCHIVE=%s\\n" "$SRC_ARCHIVE"; '
                        "cat SHA256SUMS.txt"
                    ),
                ],
                cwd=tmp_path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
            )

        self.assertIn("SRC_ARCHIVE=", result.stdout)
        self.assertIn(f"/{archive_name}", result.stdout)
        self.assertIn(f"{expected}  {archive_name}", result.stdout)
        self.assertNotIn(f"taijiagent 打包交付/{archive_name}", result.stdout)

    def test_offline_builder_has_network_mirror_fallbacks_for_build_tools(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn("TAIJI_NODE_MIRRORS", builder)
        self.assertIn("node_mirrors()", builder)
        self.assertIn("https://nodejs.org/dist", builder)
        self.assertIn("https://mirrors.tuna.tsinghua.edu.cn/nodejs-release", builder)
        self.assertIn("for mirror in $(node_mirrors)", builder)
        self.assertIn("--connect-timeout", builder)
        self.assertIn("TAIJI_NPM_REGISTRIES", builder)
        self.assertIn("TAIJI_ELECTRON_MIRRORS", builder)
        self.assertIn("npm_ci_with_network_fallback", builder)
        self.assertIn("https://registry.npmjs.org", builder)
        self.assertIn("https://github.com/electron/electron/releases/download/", builder)
        self.assertIn("无法下载 Node.js", builder)
        self.assertIn("npm ci 失败", builder)
        self.assertNotIn("hermes-local-lab", builder.lower())
        self.assertNotIn("hermes-agent", builder.lower())

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
        self.assertIn("TAIJI_DIAG_DESKTOP_ACCESS_TOKEN", diagnose)
        self.assertIn("X-Taiji-Desktop-Token", diagnose)
        self.assertIn("/api/crons", diagnose)
        self.assertIn("/api/expert-teams/catalog", diagnose)
        self.assertIn("sendExpertTeamAction", diagnose)
        self.assertIn("asset.commands.version", diagnose)
        self.assertIn("诊断报告", delivery)

    def test_packaged_webui_has_stable_version_and_agent_import_bootstrap(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        server = read_text("hermes-local-lab/sources/hermes-webui/server.py")
        routes = read_text("hermes-local-lab/sources/hermes-webui/api/routes.py")
        updates = read_text("hermes-local-lab/sources/hermes-webui/api/updates.py")
        index = read_text("hermes-local-lab/sources/hermes-webui/static/index.html")
        sw = read_text("hermes-local-lab/sources/hermes-webui/static/sw.js")

        self.assertIn("write_packaged_webui_version", build)
        self.assertIn('api/_version.txt', build)
        self.assertIn("TAIJI_WEBUI_VERSION", build)
        self.assertIn("sha256sum", build)

        self.assertIn("def _bootstrap_agent_import_path", server)
        self.assertIn("TAIJI_WEBUI_AGENT_DIR", server)
        self.assertIn("sys.path.insert(0, agent_dir)", server)
        self.assertLess(
            server.index("_bootstrap_agent_import_path()"),
            server.index("from api.auth import check_auth"),
        )
        self.assertIn("cron_component_unavailable", routes)
        self.assertIn("计划任务组件未加载，请重启应用或导出诊断报告。", routes)
        self.assertIn('logger.exception("Cron jobs component is unavailable")', routes)

        self.assertIn("TAIJI_WEBUI_VERSION", updates)
        self.assertIn("_version.txt", updates)
        self.assertIn("return baked", updates)

        self.assertNotIn("-taiji-shell-", index)
        self.assertIn('static/commands.js?v=__WEBUI_VERSION__"', index)
        self.assertIn('static/panels.js?v=__WEBUI_VERSION__"', index)
        self.assertIn("const VQ = '?v=__WEBUI_VERSION__';", sw)

    def test_delivery_folder_does_not_include_chat_cleanup_utility(self):
        docs = read_text("taijiagent 打包交付/操作说明.md")
        gitignore = read_text(".gitignore")

        self.assertFalse((ROOT / "taijiagent 打包交付/04_目标终端_清空对话记录.sh").exists())
        self.assertNotIn("04_目标终端_清空对话记录.sh", docs)
        self.assertNotIn("04_目标终端_清空对话记录.sh", gitignore)

    def test_delivery_docs_hide_legacy_runtime_entrypoints_and_log_names(self):
        texts = "\n".join(
            read_text(path)
            for path in (
                "docs/taiji-desktop-uos-packaging.md",
                "taijiagent 打包交付/操作说明.md",
                "taijiagent 打包交付/版本信息.txt",
            )
        )

        for forbidden in (
            "venv/bin/hermes",
            "hermes_cli.main",
            "hermes-agent.log",
            "hermes-home",
            "Hermes home",
        ):
            self.assertNotIn(forbidden, texts)

    def test_delivery_docs_do_not_expose_browser_access_or_ports(self):
        docs = "\n".join(
            read_text(path)
            for path in (
                "docs/taiji-desktop-uos-packaging.md",
                "packages/麒麟操作系统安装包/README.md",
            )
        )

        for forbidden in (
            "浏览器版",
            "浏览器 WebUI",
            "浏览器访问",
            "端口",
            "WebUI 和本地端口链路",
            "18642/18787",
        ):
            self.assertNotIn(forbidden, docs)


if __name__ == "__main__":
    unittest.main()
