import gzip
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def png_fixture(width: int = 1120, height: int = 720, color_type: int = 2, *, varied: bool = True) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    if varied:
        rgb = [bytes((index % 251, (index * 3) % 251, (index * 7) % 251)) for index in range(width)]
        pixel_row = b"".join(pixel if color_type == 2 else pixel + b"\xff" for pixel in rgb)
    else:
        pixel = b"\x00\x00\x00" if color_type == 2 else b"\x00\x00\x00\xff"
        pixel_row = pixel * width
    scanline = b"\x00" + pixel_row
    pixels = zlib.compress(scanline * height, level=9)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def delivery_inventory_fixture_sha256(delivery_dir: Path) -> str:
    excluded = {"offline-install-rehearsal", "target-verification", "构建日志", "诊断报告"}
    entries = [("D", ".", "")]
    for path in delivery_dir.rglob("*"):
        relative = path.relative_to(delivery_dir)
        if relative.parts and relative.parts[0] in excluded:
            continue
        if path.is_dir() and not path.is_symlink():
            entries.append(("D", relative.as_posix(), ""))
        elif path.is_file() and not path.is_symlink():
            entries.append(
                (
                    "F",
                    relative.as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    digest = hashlib.sha256()
    for kind, relative, file_hash in sorted(entries):
        digest.update(kind.encode("ascii") + b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if kind == "F":
            digest.update(file_hash.encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


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

    def test_taiji_runtime_defaults_to_restricted_security_and_local_tmp(self):
        runtime_env = read_text("hermes-local-lab/scripts/runtime-env.sh")
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        start_webui = read_text("hermes-local-lab/scripts/start-webui.sh")
        main_js = read_text("apps/taiji-desktop/src/main.js")

        self.assertIn('TAIJI_SECURITY_MODE="${TAIJI_SECURITY_MODE:-restricted}"', runtime_env)
        self.assertIn('"$TAIJI_RUNTIME_HOME/skills"', runtime_env)
        self.assertIn('"$TAIJI_RUNTIME_HOME/scripts"', runtime_env)
        for var in ("TMPDIR", "TMP", "TEMP"):
            self.assertIn(f'export {var}="$TMP_DIR"', runtime_env)
        self.assertIn("TAIJI_SECURITY_MODE", start_agent)
        self.assertIn("TAIJI_SECURITY_MODE", start_webui)
        self.assertIn("resolveSecurityProfile", main_js)
        self.assertIn('profile.name === "local_controlled"', main_js)
        self.assertIn('env.TAIJI_SECURITY_MODE = process.env.TAIJI_SECURITY_MODE || profile.mode', main_js)
        for var in (
            "TAIJI_ALLOW_TERMINAL",
            "TAIJI_ALLOW_EXECUTE_CODE",
            "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
            "TAIJI_ALLOW_DELEGATE_TASK",
        ):
            self.assertIn(var, main_js)

    def test_taiji_diagnose_exports_security_and_allowlist_reports(self):
        diagnose = read_text("hermes-local-lab/scripts/taiji-agent-diagnose")

        self.assertIn("--security", diagnose)
        self.assertIn("--allowlist", diagnose)
        self.assertIn("print_security_report", diagnose)
        self.assertIn("print_allowlist_report", diagnose)
        self.assertIn("TAIJI_SECURITY_MODE", diagnose)
        self.assertIn("effective_security_profile=", diagnose)
        self.assertIn("approval_applicable.terminal=", diagnose)
        self.assertIn("document_read.native=", diagnose)
        self.assertIn("TAIJI_AGENT_TMP_DIR", diagnose)

    def test_webui_exposes_security_status_and_profile_controls(self):
        routes = read_text("hermes-local-lab/sources/hermes-webui/api/routes.py")
        index = read_text("hermes-local-lab/sources/hermes-webui/static/index.html")
        ui = read_text("hermes-local-lab/sources/hermes-webui/static/ui.js")
        security_status = read_text("hermes-local-lab/sources/hermes-webui/api/security_status.py")

        self.assertIn("/api/security/status", routes)
        self.assertIn("/api/security/profile", routes)
        self.assertNotIn("securityModeChip", index)
        self.assertIn("settingsSecurityProfile", index)
        self.assertIn("refreshSecurityStatus", ui)
        self.assertIn("saveSecurityProfile", ui)
        for field in ("enabled", "approval_required", "reason", "restart_required"):
            self.assertIn(field, security_status)
        self.assertIn("cap.approval_required", ui)
        self.assertNotIn("可用/需审批", ui)

    def test_agent_security_mode_fails_closed_for_taiji_product_runtime(self):
        security_mode = read_text("hermes-local-lab/sources/hermes-agent/tools/taiji_security_mode.py")

        self.assertIn("def _taiji_product_runtime_configured()", security_mode)
        self.assertIn('env_flag_enabled("TAIJI_DESKTOP_ONLY")', security_mode)
        self.assertIn('os.environ.get("TAIJI_RUNTIME_HOME", "")', security_mode)
        self.assertIn(
            'return "restricted" if _taiji_product_runtime_configured() else "full"',
            security_mode,
        )
        self.assertIn('return "restricted"', security_mode)

    def test_agent_test_runner_skips_incomplete_stale_virtualenvs(self):
        runner = read_text("hermes-local-lab/sources/hermes-agent/scripts/run_tests.sh")

        self.assertIn("is_usable_test_venv", runner)
        self.assertIn("import pytest", runner)
        self.assertIn("import aiohttp", runner)
        self.assertIn("skipping incomplete test virtualenv", runner)
        self.assertLess(runner.index('"$REPO_ROOT/venv"'), runner.index('"$REPO_ROOT/.venv"'))

    def test_webui_test_server_fixture_keeps_startup_logs_for_failures(self):
        conftest = read_text("hermes-local-lab/sources/hermes-webui/tests/conftest.py")

        self.assertIn("server-test.log", conftest)
        self.assertIn("server_log_tail", conftest)
        self.assertNotIn("stdout=subprocess.DEVNULL", conftest)
        self.assertNotIn("stderr=subprocess.DEVNULL", conftest)

    def test_webui_storage_adapter_migrates_legacy_keys_to_taiji_keys(self):
        index = read_text("hermes-local-lab/sources/hermes-webui/static/index.html")
        storage = read_text("hermes-local-lab/sources/hermes-webui/static/taiji-storage.js")

        self.assertIn('src="static/taiji-storage.js', index)
        self.assertLess(index.index("static/taiji-storage.js"), index.index("static/brand.js"))
        self.assertIn("TAIJI_STORAGE_KEY_PREFIX", storage)
        self.assertIn("mapStorageKey", storage)
        self.assertIn("migrateLegacyStorage", storage)
        self.assertIn("window.Storage.prototype", storage)
        self.assertIn("proto.setItem", storage)
        self.assertIn("proto.getItem", storage)

    def test_root_release_check_gate_exists_and_requires_target_evidence(self):
        release_check = read_text("scripts/taiji-release-check.sh")
        docs = read_text("docs/taiji-sale-readiness.md")

        self.assertIn("run_root_tests", release_check)
        self.assertIn("run_agent_tests", release_check)
        self.assertIn("run_webui_tests", release_check)
        self.assertIn("tests/test_issue1800_file_html_interactions.py", release_check)
        self.assertIn("check_delivery_artifacts", release_check)
        self.assertIn("run_delivery_preflight", release_check)
        self.assertIn("TAIJI_RELEASE_REQUIRE_ARTIFACTS=1", release_check)
        self.assertIn("TAIJI_TARGET_VERIFICATION_DIR", release_check)
        self.assertIn("target-verification.json", release_check)
        self.assertIn("目标机已验证", docs)
        self.assertIn("x86_64/amd64", docs)

    def test_root_release_check_runs_all_release_evidence_tool_tests(self):
        release_check = read_text("scripts/taiji-release-check.sh")

        self.assertIn("tests.test_offline_rehearsal_producer", release_check)
        self.assertIn("tests.test_target_desktop_acceptance_producer", release_check)
        self.assertIn("tests.test_release_evidence_signer_guards", release_check)
        self.assertIn("run_desktop_evidence_tool_tests()", release_check)
        self.assertIn(
            "node --test tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js",
            release_check,
        )
        self.assertIn(
            "python3 -B tools/taiji-desktop-acceptance/test_assemble_target_evidence.py",
            release_check,
        )
        main = release_check[release_check.index("main() {") :]
        self.assertIn(
            'run_step "run_desktop_evidence_tool_tests" run_desktop_evidence_tool_tests',
            main,
        )

    def test_release_check_cannot_mask_an_earlier_webui_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            webui = tmp_path / "webui"
            agent_python = tmp_path / "hermes-agent" / "venv" / "bin" / "python"
            fake_bin = tmp_path / "bin"
            webui.mkdir()
            agent_python.parent.mkdir(parents=True)
            fake_bin.mkdir()
            agent_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            agent_python.chmod(0o755)
            npm = fake_bin / "npm"
            npm.write_text("#!/usr/bin/env bash\nexit 23\n", encoding="utf-8")
            npm.chmod(0o755)
            harness = tmp_path / "masked-webui-failure.sh"
            harness.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f'export PATH="{fake_bin}:$PATH"',
                        f'source "{ROOT / "scripts/taiji-release-check.sh"}"',
                        f'WEBUI_DIR="{webui}"',
                        'run_step "webui" run_webui_tests',
                        '[ "$failures" -eq 1 ]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["bash", str(harness)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _run_release_evidence_gate(
        self,
        gate_name,
        payload_transform,
        artifact_transform=None,
        diagnostic_transform=None,
        screenshot_transform=None,
        driver_transform=None,
        symlink_evidence_dir=False,
        symlink_evidence_ancestor=False,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            delivery_dir = tmp_path / "delivery"
            package_dir = delivery_dir / "生成的安装包"
            package_dir.mkdir(parents=True)
            offline_repo = delivery_dir / "离线依赖"
            offline_repo.mkdir()
            deb = package_dir / "taiji-agent_0.1.0-preview_amd64.deb"
            deb.write_bytes(b"current deb bytes")
            deb_sha256 = hashlib.sha256(deb.read_bytes()).hexdigest()
            source_commit = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            challenge = "ab" * 32
            test_private_key = tmp_path / "test-attestation-private.pem"
            test_public_key = tmp_path / "test-attestation-public.pem"
            subprocess.run(
                [
                    "openssl",
                    "genpkey",
                    "-algorithm",
                    "EC",
                    "-pkeyopt",
                    "ec_paramgen_curve:P-256",
                    "-out",
                    str(test_private_key),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            public_der = subprocess.run(
                ["openssl", "pkey", "-in", str(test_private_key), "-pubout", "-outform", "DER"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout
            test_public_key.write_bytes(
                subprocess.run(
                    ["openssl", "pkey", "-in", str(test_private_key), "-pubout"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                ).stdout
            )
            test_public_fingerprint = hashlib.sha256(public_der).hexdigest()
            source_archive = delivery_dir / f"taiji-agentv1.0-kylin-build-src-{source_commit}.tar.gz"
            source_archive.write_bytes(b"current source archive bytes")
            packages = offline_repo / "Packages"
            packages_gz = offline_repo / "Packages.gz"
            packages.write_bytes(b"Package: taiji-agent\nArchitecture: amd64\n")
            packages_gz.write_bytes(b"\x1f\x8bfixture packages gz")
            (offline_repo / deb.name).write_bytes(deb.read_bytes())
            (offline_repo / "dependency-fixture_1.0_amd64.deb").write_bytes(b"offline dependency")
            (offline_repo / "runtime-dependencies.txt").write_text("dependency-fixture\n", encoding="utf-8")
            (offline_repo / "SHA256SUMS.txt").write_text("fixture inventory\n", encoding="utf-8")
            source_sha256 = hashlib.sha256(source_archive.read_bytes()).hexdigest()
            packages_sha256 = hashlib.sha256(packages.read_bytes()).hexdigest()
            packages_gz_sha256 = hashlib.sha256(packages_gz.read_bytes()).hexdigest()
            checksum = package_dir / f"{deb.name}.sha256"
            checksum.write_text(f"{deb_sha256}  {deb.name}\n", encoding="utf-8")
            manifest = package_dir / "taiji-package-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "package": "taiji-agent",
                        "version": "0.1.0-preview",
                        "build_arch": "x86_64",
                        "dpkg_arch": "amd64",
                        "deb": deb.name,
                        "deb_sha256": deb_sha256,
                        "checksum": checksum.name,
                        "source_archive": source_archive.name,
                        "source_commit": source_commit,
                        "source_sha256": source_sha256,
                        "packages_sha256": packages_sha256,
                        "packages_gz_sha256": packages_gz_sha256,
                        "electron_executable_sha256": "4" * 64,
                        "desktop_entry_sha256": "5" * 64,
                        "built_at": generated_at,
                    }
                ),
                encoding="utf-8",
            )
            build_marker = package_dir / ".build-success"
            build_marker.write_text(
                "\n".join(
                    [
                        "version=0.1.0-preview",
                        f"source_archive={source_archive.name}",
                        f"source_sha256={source_sha256}",
                        f"deb={deb.name}",
                        f"deb_sha256={deb_sha256}",
                        f"checksum={checksum.name}",
                        "built_at=2026-07-11T08:00:00+0800",
                        f"manifest={manifest.name}",
                        f"packages_sha256={packages_sha256}",
                        f"packages_gz_sha256={packages_gz_sha256}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (package_dir / "构建报告.txt").write_text("current build report\n", encoding="utf-8")
            for filename in (
                "00_制包机_生成离线交付包.sh",
                "01_制包机_发布预检.sh",
                "02_目标终端_安装并验证.sh",
                "03_目标终端_导出诊断报告.sh",
                "04_目标终端_桌面App验收并导出证据.sh",
                "99_本机_准备制包输入包.sh",
            ):
                script = delivery_dir / filename
                script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
                script.chmod(0o755)
            acceptance_tools = delivery_dir / "验收工具"
            acceptance_tools.mkdir(mode=0o755)
            (acceptance_tools / "run-installed-electron-acceptance.js").write_text(
                "// fixture desktop acceptance driver\n", encoding="utf-8"
            )
            (acceptance_tools / "assemble-target-evidence.py").write_text(
                "# fixture target assembler\n", encoding="utf-8"
            )
            (acceptance_tools / "validate-taiji-release-evidence.py").write_text(
                "# fixture release validator\n", encoding="utf-8"
            )
            (acceptance_tools / "signing-public.pem").write_text(
                "fixture release public key\n", encoding="utf-8"
            )
            (delivery_dir / "SHA256SUMS.txt").write_text(
                f"{source_sha256}  {source_archive.name}\n",
                encoding="utf-8",
            )
            (delivery_dir / "操作说明.md").write_text("fixture instructions\n", encoding="utf-8")
            (delivery_dir / "版本信息.txt").write_text("0.1.0-preview\n", encoding="utf-8")
            release_artifacts_sha256 = delivery_inventory_fixture_sha256(delivery_dir)

            driver_result = None
            driver_payload = None
            if gate_name == "check_offline_install_rehearsal":
                real_evidence_dir = tmp_path / "offline-install-rehearsal-real"
                evidence_name = "offline-install-rehearsal.json"
                log = real_evidence_dir / "offline-install-rehearsal-session.json"
                session_id = "1" * 32
                base_payload = {
                    "schema_version": 1,
                    "evidence_type": "offline-install-rehearsal",
                    "generated_at_utc": generated_at,
                    "rehearsal_session_id": session_id,
                    "challenge_nonce": challenge,
                    "release_artifacts_sha256": release_artifacts_sha256,
                    "source_commit": source_commit,
                    "deb_basename": deb.name,
                    "deb_sha256": deb_sha256,
                    "platform": "linux/amd64",
                    "environment": "container",
                    "os_id": "debian",
                    "os_version": "13",
                    "network": "none",
                    "install": True,
                    "uninstall": True,
                    "reinstall": True,
                    "desktop_app_verified": False,
                    "target_verified": False,
                    "log_basename": log.name,
                    "log_sha256": "",
                }
                environment_name = "TAIJI_OFFLINE_REHEARSAL_DIR"
                session_payload = {
                    "schema": "taiji.offline-install-rehearsal.v1",
                    "generated_at_utc": generated_at,
                    "rehearsal_session_id": session_id,
                    "challenge_nonce": challenge,
                    "source_commit": source_commit,
                    "deb_basename": deb.name,
                    "deb_sha256": deb_sha256,
                    "platform": "linux/amd64",
                    "environment": "container",
                    "os_id": "debian",
                    "os_version": "13",
                    "network": "none",
                    "checks": {"install": True, "uninstall": True, "reinstall": True},
                    "desktop_app_verified": False,
                    "target_verified": False,
                }
            elif gate_name == "check_target_verification":
                real_evidence_dir = tmp_path / "target-verification-real"
                evidence_name = "target-verification.json"
                log = real_evidence_dir / "desktop-acceptance-session.json"
                screenshot = real_evidence_dir / "desktop-app.png"
                diagnostic = real_evidence_dir / "taiji-support-bundle.json"
                driver_result = real_evidence_dir / "desktop-driver-result.json"
                session_id = "2" * 32
                machine_fingerprint = "3" * 64
                base_payload = {
                    "schema_version": 1,
                    "evidence_type": "target-desktop-verification",
                    "application": "taiji-electron-desktop",
                    "generated_at_utc": generated_at,
                    "acceptance_session_id": session_id,
                    "challenge_nonce": challenge,
                    "machine_fingerprint_sha256": machine_fingerprint,
                    "release_artifacts_sha256": release_artifacts_sha256,
                    "electron_executable_sha256": "4" * 64,
                    "desktop_entry_sha256": "5" * 64,
                    "installed_package_version": "0.1.0-preview",
                    "source_commit": source_commit,
                    "deb_basename": deb.name,
                    "deb_sha256": deb_sha256,
                    "platform": "linux/amd64",
                    "os_id": "kylin",
                    "os_version": "V10",
                    "desktop_environment": "UKUI",
                    "target_verified": True,
                    "desktop_launch": True,
                    "real_model_conversation": True,
                    "attachment_flow": True,
                    "window_close_exit": True,
                    "diagnostic_export": True,
                    "session_log_basename": log.name,
                    "session_log_sha256": "",
                    "screenshot_basename": screenshot.name,
                    "screenshot_sha256": "",
                    "diagnostic_basename": diagnostic.name,
                    "diagnostic_sha256": "",
                    "driver_result_basename": driver_result.name,
                    "driver_result_sha256": "",
                }
                environment_name = "TAIJI_TARGET_VERIFICATION_DIR"
                session_payload = {
                    "schema": "taiji.desktop.acceptance.v1",
                    "application": "taiji-electron-desktop",
                    "generated_at_utc": generated_at,
                    "acceptance_session_id": session_id,
                    "challenge_nonce": challenge,
                    "source_commit": source_commit,
                    "deb_sha256": deb_sha256,
                    "platform": "linux/amd64",
                    "os_id": "kylin",
                    "os_version": "V10",
                    "desktop_environment": "UKUI",
                    "machine_fingerprint_sha256": machine_fingerprint,
                    "electron_pid": 4242,
                    "electron_executable": "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron",
                    "electron_executable_sha256": "4" * 64,
                    "desktop_entry_sha256": "5" * 64,
                    "installed_package_version": "0.1.0-preview",
                    "transport": "electron-cdp",
                    "desktop_token_present": True,
                    "web_fallback_used": False,
                    "checks": {
                        "desktop_launch": True,
                        "real_model_conversation": True,
                        "attachment_flow": True,
                        "window_close_exit": True,
                        "diagnostic_export": True,
                    },
                    "js_error_count": 0,
                    "unexpected_http_failures": 0,
                }
                driver_payload = {
                    "schema": "taiji.desktop.acceptance-driver.v1",
                    "acceptance_session_id": session_id,
                    "challenge_nonce": challenge,
                    "electron_pid": 4242,
                    "electron_executable": "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron",
                    "electron_executable_sha256": "4" * 64,
                    "desktop_entry_sha256": "5" * 64,
                    "app_url": (
                        "http://127.0.0.1:18787/?taiji_desktop=1&"
                        "taiji_desktop_token=%3Credacted%3E"
                    ),
                    "webui_origin": "http://127.0.0.1:18787",
                    "model": "openai/gpt-test",
                    "attachment_probe_sha256": "6" * 64,
                    "agent_pid": 4243,
                    "web_pid": 4244,
                    "screenshot_basename": screenshot.name,
                    "diagnostic_basename": diagnostic.name,
                    "checks": dict(session_payload["checks"]),
                    "js_error_count": 0,
                    "unexpected_http_failures": 0,
                    "electron_exit_code": 0,
                }
            else:
                raise AssertionError(f"unknown gate: {gate_name}")

            real_evidence_dir.mkdir()
            if symlink_evidence_ancestor:
                evidence_ancestor = tmp_path / "evidence-ancestor-link"
                evidence_ancestor.symlink_to(tmp_path, target_is_directory=True)
                evidence_dir = evidence_ancestor / real_evidence_dir.name
            else:
                evidence_dir = tmp_path / ("evidence-link" if symlink_evidence_dir else real_evidence_dir.name)
            if symlink_evidence_dir and not symlink_evidence_ancestor:
                evidence_dir.symlink_to(real_evidence_dir, target_is_directory=True)
            if gate_name == "check_offline_install_rehearsal":
                log.write_text(json.dumps(session_payload), encoding="utf-8")
                base_payload["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
            else:
                log.write_text(json.dumps(session_payload), encoding="utf-8")
                screenshot_payload = png_fixture()
                if screenshot_transform:
                    screenshot_payload = screenshot_transform(screenshot_payload)
                screenshot.write_bytes(screenshot_payload)
                diagnostic_payload = {
                    "schema": "taiji.product.support-bundle.v1",
                    "manifest": {
                        "redacted": True,
                        "logs_included": False,
                        "paths_included": False,
                        "secrets_included": False,
                    },
                    "diagnostics": {
                        "schema": "taiji.product.diagnostics.v1",
                        "generated_at": generated_at,
                        "incident_id": "inc-0123456789ab",
                        "overall": "ready",
                        "components": [
                            {"id": item, "label": label, "status": "ready"}
                            for item, label in (
                                ("webui", "桌面界面"),
                                ("agent", "智能体服务"),
                                ("gateway", "本地任务服务"),
                                ("license", "授权状态"),
                                ("docx", "文档引擎"),
                                ("skills", "专家能力"),
                                ("node", "运行环境"),
                            )
                        ],
                    },
                }
                if diagnostic_transform:
                    diagnostic_payload = diagnostic_transform(diagnostic_payload)
                diagnostic.write_text(json.dumps(diagnostic_payload), encoding="utf-8")
                if driver_transform:
                    driver_payload = driver_transform(dict(driver_payload))
                driver_result.write_text(json.dumps(driver_payload), encoding="utf-8")
                driver_result.chmod(0o600)
                base_payload["session_log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
                base_payload["screenshot_sha256"] = hashlib.sha256(screenshot.read_bytes()).hexdigest()
                base_payload["diagnostic_sha256"] = hashlib.sha256(diagnostic.read_bytes()).hexdigest()
                base_payload["driver_result_sha256"] = hashlib.sha256(
                    driver_result.read_bytes()
                ).hexdigest()

            if payload_transform != "missing":
                evidence = evidence_dir / evidence_name
                payload = payload_transform(dict(base_payload)) if callable(payload_transform) else payload_transform
                if isinstance(payload, str):
                    evidence.write_text(payload, encoding="utf-8")
                else:
                    evidence.write_text(json.dumps(payload), encoding="utf-8")
                signature = Path(f"{evidence}.sig")
                subprocess.run(
                    [
                        "openssl",
                        "dgst",
                        "-sha256",
                        "-sign",
                        str(test_private_key),
                        "-out",
                        str(signature),
                        str(evidence),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                evidence = evidence_dir / evidence_name
                signature = Path(f"{evidence}.sig")

            artifact_paths = {
                "deb": deb,
                "checksum": checksum,
                "manifest": manifest,
                "build_marker": build_marker,
                "source_archive": source_archive,
                "packages": packages,
                "packages_gz": packages_gz,
                "offline_dependency": offline_repo / "dependency-fixture_1.0_amd64.deb",
                "offline_repo": offline_repo,
                "delivery_dir": delivery_dir,
                "install_script": delivery_dir / "02_目标终端_安装并验证.sh",
                "evidence_dir": evidence_dir,
                "real_evidence_dir": real_evidence_dir,
                "evidence": evidence,
                "signature": signature,
                "driver_result": driver_result,
            }
            if artifact_transform:
                artifact_transform(artifact_paths)

            harness = tmp_path / "run-offline-rehearsal-gate.sh"
            harness.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f'export TAIJI_RELEASE_REPO_ROOT="{ROOT}"',
                        f'export TAIJI_DELIVERY_DIR="{delivery_dir}"',
                        f'export {environment_name}="{evidence_dir}"',
                        f'export TAIJI_OFFLINE_REHEARSAL_CHALLENGE="{challenge}"',
                        f'export TAIJI_TARGET_ACCEPTANCE_CHALLENGE="{challenge}"',
                        f'source "{ROOT / "scripts/taiji-release-check.sh"}"',
                        f'EVIDENCE_ATTESTATION_PUBLIC_KEY="{test_public_key}"',
                        f'EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT="{test_public_fingerprint}"',
                        gate_name,
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return subprocess.run(
                ["bash", str(harness)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_release_check_requires_valid_offline_lifecycle_evidence(self):
        invalid_cases = (
            ("missing", "missing"),
            ("malformed", "{not-json\n"),
            ("wrong_platform", lambda data: {**data, "platform": "linux/arm64"}),
            ("online", lambda data: {**data, "network": "bridge"}),
            ("missing_install", lambda data: {**data, "install": False}),
            ("missing_uninstall", lambda data: {**data, "uninstall": False}),
            ("missing_reinstall", lambda data: {**data, "reinstall": False}),
            ("claims_desktop", lambda data: {**data, "desktop_app_verified": True}),
            ("claims_target", lambda data: {**data, "target_verified": True}),
            ("string_boolean", lambda data: {**data, "install": "true"}),
            ("wrong_commit", lambda data: {**data, "source_commit": "deadbeef"}),
            ("wrong_deb_hash", lambda data: {**data, "deb_sha256": "0" * 64}),
            ("wrong_challenge", lambda data: {**data, "challenge_nonce": "cd" * 32}),
            ("wrong_log_hash", lambda data: {**data, "log_sha256": "0" * 64}),
            ("unsafe_log_path", lambda data: {**data, "log_basename": "../outside.log"}),
            ("invalid_environment_type", lambda data: {**data, "environment": ["container"]}),
            ("extra_field", lambda data: {**data, "actual_network": "bridge"}),
            ("missing_field", lambda data: {key: value for key, value in data.items() if key != "network"}),
            (
                "duplicate_key",
                lambda data: json.dumps(data)[:-1] + ', "network": "none"}',
            ),
        )

        for label, payload in invalid_cases:
            with self.subTest(label=label):
                result = self._run_release_evidence_gate("check_offline_install_rehearsal", payload)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

        accepted = self._run_release_evidence_gate("check_offline_install_rehearsal", lambda data: data)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertIn("离线生命周期演练证据有效", accepted.stdout + accepted.stderr)

    def test_release_check_requires_strict_current_desktop_target_evidence(self):
        invalid_cases = (
            ("missing", "missing"),
            ("malformed", '{"target_verified": true, "desktop_launch": true, "diagnostic_export": true'),
            ("wrong_app", lambda data: {**data, "application": "web"}),
            ("wrong_os", lambda data: {**data, "os_id": "debian"}),
            ("wrong_commit", lambda data: {**data, "source_commit": "deadbeef"}),
            ("wrong_deb_hash", lambda data: {**data, "deb_sha256": "f" * 64}),
            ("wrong_electron_hash", lambda data: {**data, "electron_executable_sha256": "0" * 64}),
            ("wrong_desktop_entry_hash", lambda data: {**data, "desktop_entry_sha256": "0" * 64}),
            ("wrong_challenge", lambda data: {**data, "challenge_nonce": "cd" * 32}),
            ("no_desktop_launch", lambda data: {**data, "desktop_launch": False}),
            ("no_model_conversation", lambda data: {**data, "real_model_conversation": False}),
            ("no_attachment", lambda data: {**data, "attachment_flow": False}),
            ("no_window_exit", lambda data: {**data, "window_close_exit": False}),
            ("no_diagnostics", lambda data: {**data, "diagnostic_export": False}),
            ("string_boolean", lambda data: {**data, "target_verified": "true"}),
            ("wrong_session_log_hash", lambda data: {**data, "session_log_sha256": "0" * 64}),
            ("unsafe_screenshot_path", lambda data: {**data, "screenshot_basename": "../web.png"}),
            ("wrong_diagnostic_hash", lambda data: {**data, "diagnostic_sha256": "0" * 64}),
            ("wrong_driver_hash", lambda data: {**data, "driver_result_sha256": "0" * 64}),
            ("unsafe_driver_path", lambda data: {**data, "driver_result_basename": "../driver.json"}),
            (
                "same_file_for_all_evidence",
                lambda data: {
                    **data,
                    "screenshot_basename": data["session_log_basename"],
                    "screenshot_sha256": data["session_log_sha256"],
                    "diagnostic_basename": data["session_log_basename"],
                    "diagnostic_sha256": data["session_log_sha256"],
                },
            ),
            ("extra_mobile_claim", lambda data: {**data, "mobile_verified": True}),
        )

        for label, payload in invalid_cases:
            with self.subTest(label=label):
                result = self._run_release_evidence_gate("check_target_verification", payload)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

        accepted = self._run_release_evidence_gate("check_target_verification", lambda data: data)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertIn("桌面 App 目标机证据有效", accepted.stdout + accepted.stderr)

        for label, transform in (
            (
                "driver unknown field",
                lambda data: {**data, "unexpected": True},
            ),
            (
                "driver session mismatch",
                lambda data: {**data, "acceptance_session_id": "f" * 32},
            ),
            (
                "driver challenge mismatch",
                lambda data: {**data, "challenge_nonce": "e" * 64},
            ),
            (
                "driver electron pid mismatch",
                lambda data: {**data, "electron_pid": 5252},
            ),
        ):
            with self.subTest(label=label):
                result = self._run_release_evidence_gate(
                    "check_target_verification",
                    lambda data: data,
                    driver_transform=transform,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

        copied_driver = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=lambda paths: paths["driver_result"].chmod(0o644),
        )
        self.assertEqual(
            copied_driver.returncode,
            0,
            copied_driver.stdout + copied_driver.stderr,
        )

        def emulate_permission_losing_copy(paths):
            delivery = paths["delivery_dir"]
            delivery.chmod(0o755)
            for item in delivery.rglob("*"):
                if item.is_dir() and not item.is_symlink():
                    item.chmod(0o700 if len(item.parts) % 2 else 0o755)
                elif item.is_file() and not item.is_symlink():
                    item.chmod(0o644)

        copied_delivery = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=emulate_permission_losing_copy,
        )
        self.assertEqual(
            copied_delivery.returncode,
            0,
            copied_delivery.stdout + copied_delivery.stderr,
        )

        tampered_driver = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=lambda paths: paths["driver_result"].write_text(
                '{"schema":"tampered"}', encoding="utf-8"
            ),
        )
        self.assertNotEqual(
            tampered_driver.returncode,
            0,
            tampered_driver.stdout + tampered_driver.stderr,
        )

        def hardlink_driver(paths):
            os.link(paths["driver_result"], paths["driver_result"].with_suffix(".hardlink"))

        hardlinked_driver = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=hardlink_driver,
        )
        self.assertNotEqual(
            hardlinked_driver.returncode,
            0,
            hardlinked_driver.stdout + hardlinked_driver.stderr,
        )

        def contradict_diagnostic_overall(bundle):
            bundle["diagnostics"]["components"][1]["status"] = "blocked"
            return bundle

        contradictory = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            diagnostic_transform=contradict_diagnostic_overall,
        )
        self.assertNotEqual(contradictory.returncode, 0, contradictory.stdout + contradictory.stderr)

        def add_private_diagnostic_field(bundle):
            bundle["diagnostics"]["raw_secret"] = "/Users/alice/sk-private"
            return bundle

        private_diagnostic = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            diagnostic_transform=add_private_diagnostic_field,
        )
        self.assertNotEqual(private_diagnostic.returncode, 0, private_diagnostic.stdout + private_diagnostic.stderr)

        blank_screenshot = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            screenshot_transform=lambda _payload: png_fixture(varied=False),
        )
        self.assertNotEqual(blank_screenshot.returncode, 0, blank_screenshot.stdout + blank_screenshot.stderr)

    def test_release_evidence_binds_build_manifest_and_rejects_symlinked_evidence_directory(self):
        def corrupt_manifest(paths):
            payload = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            payload["source_commit"] = "deadbeef"
            paths["manifest"].write_text(json.dumps(payload), encoding="utf-8")

        manifest_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=corrupt_manifest,
        )
        self.assertNotEqual(manifest_result.returncode, 0, manifest_result.stdout + manifest_result.stderr)

        def corrupt_sidecar(paths):
            paths["checksum"].write_text(f"{'0' * 64}  {paths['deb'].name}\n", encoding="utf-8")

        checksum_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=corrupt_sidecar,
        )
        self.assertNotEqual(checksum_result.returncode, 0, checksum_result.stdout + checksum_result.stderr)

        dependency_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=lambda paths: paths["offline_dependency"].write_bytes(
                b"tampered offline dependency after evidence signing"
            ),
        )
        self.assertNotEqual(dependency_result.returncode, 0, dependency_result.stdout + dependency_result.stderr)

        writable_directory_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=lambda paths: paths["offline_repo"].chmod(0o777),
        )
        self.assertNotEqual(
            writable_directory_result.returncode,
            0,
            writable_directory_result.stdout + writable_directory_result.stderr,
        )

        writable_script_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=lambda paths: paths["install_script"].chmod(0o777),
        )
        self.assertNotEqual(
            writable_script_result.returncode,
            0,
            writable_script_result.stdout + writable_script_result.stderr,
        )

        signature_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=lambda paths: paths["signature"].unlink(),
        )
        self.assertNotEqual(signature_result.returncode, 0, signature_result.stdout + signature_result.stderr)

        tampered_evidence_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            artifact_transform=lambda paths: paths["evidence"].write_text(
                paths["evidence"].read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            ),
        )
        self.assertNotEqual(
            tampered_evidence_result.returncode,
            0,
            tampered_evidence_result.stdout + tampered_evidence_result.stderr,
        )

        symlink_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            symlink_evidence_dir=True,
        )
        self.assertNotEqual(symlink_result.returncode, 0, symlink_result.stdout + symlink_result.stderr)

        ancestor_symlink_result = self._run_release_evidence_gate(
            "check_target_verification",
            lambda data: data,
            symlink_evidence_ancestor=True,
        )
        self.assertNotEqual(
            ancestor_symlink_result.returncode,
            0,
            ancestor_symlink_result.stdout + ancestor_symlink_result.stderr,
        )

    def test_release_check_runs_offline_rehearsal_gate_before_target_gate(self):
        release_check = read_text("scripts/taiji-release-check.sh")
        docs = read_text("docs/taiji-sale-readiness.md")

        self.assertIn("TAIJI_OFFLINE_REHEARSAL_DIR", release_check)
        self.assertIn("offline-install-rehearsal.json", release_check)
        self.assertIn("check_offline_install_rehearsal", release_check)
        self.assertIn("python3", release_check)
        main = release_check[release_check.index("main() {") :]
        self.assertLess(
            main.index("check_offline_install_rehearsal"),
            main.index("check_target_verification"),
        )
        self.assertIn("TAIJI_OFFLINE_REHEARSAL_DIR", docs)
        self.assertIn('"network": "none"', docs)
        self.assertIn('"target_verified": false', docs)

    def test_release_check_main_aggregates_both_missing_evidence_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harness = tmp_path / "run-release-main.sh"
            harness.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f'export TAIJI_RELEASE_REPO_ROOT="{ROOT}"',
                        f'export TAIJI_DELIVERY_DIR="{tmp_path / "delivery"}"',
                        f'export TAIJI_OFFLINE_REHEARSAL_DIR="{tmp_path / "offline"}"',
                        f'export TAIJI_TARGET_VERIFICATION_DIR="{tmp_path / "target"}"',
                        f'source "{ROOT / "scripts/taiji-release-check.sh"}"',
                        "run_root_tests() { :; }",
                        "run_desktop_evidence_tool_tests() { :; }",
                        "run_agent_tests() { :; }",
                        "run_webui_tests() { :; }",
                        "run_delivery_preflight() { :; }",
                        "check_delivery_artifacts() { :; }",
                        "main",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(harness)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("check_offline_install_rehearsal", output)
        self.assertIn("check_target_verification", output)
        self.assertIn("2 项失败", output)

    def test_release_delivery_check_propagates_source_failure_and_uses_build_output_metadata(self):
        release_check = read_text("scripts/taiji-release-check.sh")
        self.assertIn("check_source_archive || return 1", release_check)
        self.assertIn("生成的安装包/taiji-package-manifest.json", release_check)
        self.assertIn("生成的安装包/构建报告.txt", release_check)
        self.assertIn('EVIDENCE_VALIDATOR="$SCRIPT_ROOT/scripts/validate-taiji-release-evidence.py"', release_check)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            delivery = tmp_path / "delivery"
            output = delivery / "生成的安装包"
            offline = delivery / "离线依赖"
            output.mkdir(parents=True)
            offline.mkdir()
            (output / "taiji-agent_fixture_amd64.deb").write_bytes(b"deb")
            (output / "taiji-agent_fixture_amd64.deb.sha256").write_text("fixture\n", encoding="utf-8")
            (output / ".build-success").write_text("fixture\n", encoding="utf-8")
            (output / "taiji-package-manifest.json").write_text("{}\n", encoding="utf-8")
            (output / "构建报告.txt").write_text("fixture\n", encoding="utf-8")
            (offline / "Packages").write_text("fixture\n", encoding="utf-8")
            (offline / "Packages.gz").write_bytes(b"fixture")
            harness = tmp_path / "check-delivery-propagation.sh"
            harness.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f'export TAIJI_RELEASE_REPO_ROOT="{ROOT}"',
                        f'export TAIJI_DELIVERY_DIR="{delivery}"',
                        f'source "{ROOT / "scripts/taiji-release-check.sh"}"',
                        "check_source_archive() { return 1; }",
                        "check_delivery_artifacts",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(harness)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_release_evidence_signer_uses_fixed_offline_trust_anchor(self):
        signer = read_text("scripts/sign-taiji-release-evidence.sh")
        release_check = read_text("scripts/taiji-release-check.sh")

        self.assertIn("tools/taiji-release-evidence/signing-public.pem", signer)
        self.assertIn("839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da", signer)
        self.assertIn("openssl dgst -sha256 -sign", signer)
        self.assertIn("openssl dgst -sha256 -verify", signer)
        self.assertIn('SIGNATURE="${EVIDENCE}.sig"', signer)
        self.assertIn('--attestation-signature "${evidence}.sig"', release_check)
        self.assertIn("EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT", release_check)
        self.assertIn("TAIJI_RELEASE_SKIP_GIT_CHECK=0", signer)
        self.assertIn("TAIJI_RELEASE_SKIP_GIT_CHECK=0", release_check)
        self.assertIn("st_nlink", signer)
        self.assertIn("stat.S_IMODE", signer)
        self.assertIn("O_EXCL", signer)
        self.assertIn("used-challenges", signer)
        self.assertIn("TAIJI_OFFLINE_REHEARSAL_CHALLENGE", signer)
        self.assertIn("TAIJI_TARGET_ACCEPTANCE_CHALLENGE", signer)
        self.assertIn('"$CHALLENGE" = "$EXPECTED_CHALLENGE"', signer)
        self.assertIn("st_size > 1024 * 1024", signer)
        self.assertIn("O_NOFOLLOW", signer)
        self.assertIn("os.fsync(state_descriptor)", signer)
        self.assertLess(
            signer.index('--attestation-signature "$tmp_signature"'),
            signer.index('mv -f "$tmp_signature" "$SIGNATURE"'),
        )

    def test_release_preflight_rebuilds_source_and_semantically_checks_offline_repo(self):
        preflight = read_text("taijiagent 打包交付/01_制包机_发布预检.sh")

        self.assertIn("check_source_archive_matches_git_head", preflight)
        self.assertIn("git -C \"$REPO_ROOT\" archive", preflight)
        self.assertIn("gzip -n", preflight)
        self.assertIn('cmp -s "$expected_archive" "$SOURCE_ARCHIVE"', preflight)
        self.assertIn("verify_offline_repository_integrity", preflight)
        self.assertIn('gzip -t "$OFFLINE_REPO/Packages.gz"', preflight)
        self.assertIn('gzip -dc "$OFFLINE_REPO/Packages.gz" | cmp -s - "$OFFLINE_REPO/Packages"', preflight)
        self.assertIn("Packages index does not exactly cover repository DEBs", preflight)
        self.assertIn('dpkg-deb --info "$repo_deb"', preflight)
        self.assertIn('["dpkg-deb", "-f", str(package_path), field]', preflight)
        self.assertIn("offline taiji-agent DEB does not match release DEB", preflight)
        self.assertIn("electron_executable_sha256", preflight)
        self.assertIn("desktop_entry_sha256", preflight)

        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        self.assertIn('"electron_executable_sha256"', builder)
        self.assertIn('"desktop_entry_sha256"', builder)
        self.assertIn("cleanup_delivery_build_cache", builder)
        self.assertIn("cleanup_temporary_build_root", builder)
        self.assertIn("umask 022", builder)
        self.assertIn("normalize_delivery_permissions", builder)
        self.assertIn("-type f -links +1", builder)
        self.assertIn('find "$SCRIPT_DIR" -xdev -mindepth 1 \\( -type d -o -type f \\) -exec chmod go-w', builder)
        self.assertLess(
            builder.index("cleanup_delivery_build_cache", builder.index("main() {")),
            builder.index("最终发布预检", builder.index("main() {")),
        )
        self.assertGreater(
            builder.index("cleanup_temporary_build_root", builder.index("main() {")),
            builder.index("run_release_preflight", builder.index("最终发布预检")),
        )
        self.assertIn("validate_build_root_location", builder)
        self.assertIn("umask 022", read_text("taijiagent 打包交付/99_本机_准备制包输入包.sh"))

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

    def test_runtime_start_scripts_defer_license_policy_to_build_profile(self):
        runtime_env = read_text("hermes-local-lab/scripts/runtime-env.sh")
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        start_webui = read_text("hermes-local-lab/scripts/start-webui.sh")
        main_js = read_text("apps/taiji-desktop/src/main.js")

        for text in (runtime_env, start_agent, start_webui, main_js):
            self.assertIn("TAIJI_LICENSE_FILE", text)
            self.assertIn("TAIJI_LICENSE_STATE_FILE", text)
            self.assertNotIn("TAIJI_LICENSE_REQUIRED", text)
            self.assertNotIn("TAIJI_LICENSE_MACHINE_BINDING_REQUIRED", text)
            self.assertNotIn("HERMES_LICENSE", text)
            self.assertNotIn("HERMES_LICENSE_FILE", text)

        self.assertIn(
            'TAIJI_LICENSE_FILE="$TAIJI_ACCOUNT_HOME/.config/taiji-agent/licenses/active-license.jwt"',
            runtime_env,
        )
        self.assertIn(
            'TAIJI_LICENSE_STATE_FILE="$TAIJI_ACCOUNT_HOME/.local/state/taiji-agent/license-state.json"',
            runtime_env,
        )
        self.assertIn('accountHome = String(os.userInfo().homedir || "").trim()', main_js)
        self.assertIn("env.TAIJI_ACCOUNT_HOME = accountHome", main_js)
        self.assertIn('path.join(accountHome, ".config", "taiji-agent", "licenses", "active-license.jwt")', main_js)
        self.assertIn('path.join(accountHome, ".local", "state", "taiji-agent", "license-state.json")', main_js)
        self.assertNotIn("os.homedir()", main_js)

    def test_installed_payload_profile_is_generated_before_sourceless_compile(self):
        source_profile = read_text(
            "hermes-local-lab/sources/hermes-agent/taiji-runtime-profile.json"
        )
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn('"profile": "source-development"', source_profile)
        profile_call = "  write_installed_runtime_profile\n"
        self.assertIn(profile_call, build)
        self.assertIn('"profile": "installed-production"', build)
        self.assertLess(
            build.index(profile_call),
            build.index('compile_sourceless_python "$AGENT_RUNTIME"'),
        )

    def test_build_fixes_root_owned_trust_anchor_directory_modes(self):
        build = read_text("packaging/linux/deb/build-deb.sh")

        self.assertIn('chmod 0755 "$PKG_ROOT/opt" "$INSTALL_ROOT"', build)
        self.assertIn('chmod 0755 "$INSTALL_ROOT/resources"', build)
        self.assertIn('chmod 0755 "$INSTALL_ROOT/resources/license"', build)

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

    def test_packaged_runtime_excludes_dev_templates_and_stages_portable_python(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        python_stager = read_text("packaging/linux/stage-python-runtime.py")

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

        self.assertIn("stage-python-runtime.py", build)
        self.assertIn('--source-venv "$SOURCE_AGENT_DIR/venv"', build)
        self.assertIn('--destination "$AGENT_RUNTIME/venv"', build)
        self.assertIn("--require-linux-x86-64", build)
        self.assertNotIn('"$SOURCE_AGENT_DIR/venv"/ "$AGENT_RUNTIME/venv"/', build)
        self.assertNotIn("repair_packaged_venv_paths", build)
        self.assertIn("def assert_no_source_paths", python_stager)
        self.assertIn("def run_relocation_smoke", python_stager)
        self.assertIn("/opt/taiji-agent/runtime/agent/venv", python_stager)
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
        empty_response_start = gateway_chat.index("if not internal_assistant_text:")
        empty_response = gateway_chat[
            empty_response_start:
            gateway_chat.index(
                "artifacts, artifact_errors, uncommitted_artifact_ids",
                empty_response_start,
            )
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
        self.assertIn("stage_privileged_install_inputs", install)
        self.assertIn("/var/tmp/taiji-agent-install.XXXXXX", install)
        self.assertIn('cat -- "$source" | sudo tee -- "$destination" >/dev/null', install)
        self.assertNotIn('sudo install -m 0644 "$file"', install)
        self.assertIn('sudo mkdir -p -m 0700', install)
        self.assertIn("OFFLINE_APT_REPO_SOURCE", install)
        self.assertNotIn('ln -s "$repo_path"', install)
        self.assertNotIn('printf \'deb [trusted=yes] file:%s ./\\n\' "$repo_path"', install)
        self.assertIn("apt-get update", install)
        self.assertIn("dpkg-scanpackages", builder)
        self.assertIn("dpkg-scanpackages . /dev/null > Packages", builder)
        self.assertIn("gzip -9c Packages > Packages.gz", builder)
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
        self.assertIn('缺少离线依赖/Packages"', preflight)
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

    def _run_release_preflight_artifact_gate(
        self,
        sidecar_mode,
        tampered_acceptance_tool=None,
        extra_output_case=None,
    ):
        source_script = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            delivery = tmp_path / "taijiagent 打包交付"
            output_dir = delivery / "生成的安装包"
            offline_repo = delivery / "离线依赖"
            fake_bin = tmp_path / "bin"
            repo_root = tmp_path / "repo"
            verifier = repo_root / "packaging/linux/verify-payload.py"
            for directory in (delivery, output_dir, offline_repo, fake_bin, verifier.parent):
                directory.mkdir(parents=True, exist_ok=True)

            script = delivery / "01_制包机_发布预检.sh"
            shutil.copy2(source_script, script)
            target_script_relative = Path("taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh")
            source_target_script = repo_root / target_script_relative
            source_target_script.parent.mkdir(parents=True, exist_ok=True)
            source_target_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            source_target_script.chmod(0o755)
            target_script = delivery / source_target_script.name
            shutil.copy2(source_target_script, target_script)
            acceptance_tools = delivery / "验收工具"
            acceptance_tools.mkdir()
            acceptance_sources = {
                "run-installed-electron-acceptance.js": Path(
                    "tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js"
                ),
                "assemble-target-evidence.py": Path(
                    "tools/taiji-desktop-acceptance/assemble-target-evidence.py"
                ),
                "validate-taiji-release-evidence.py": Path(
                    "scripts/validate-taiji-release-evidence.py"
                ),
                "signing-public.pem": Path("tools/taiji-release-evidence/signing-public.pem"),
            }
            for filename, relative_source in acceptance_sources.items():
                source = repo_root / relative_source
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative_source, source)
                shutil.copy2(source, acceptance_tools / filename)
            if tampered_acceptance_tool is not None:
                tamper_target = (
                    target_script
                    if tampered_acceptance_tool == target_script.name
                    else acceptance_tools / tampered_acceptance_tool
                )
                with tamper_target.open("a", encoding="utf-8") as handle:
                    if tampered_acceptance_tool.endswith(".js"):
                        handle.write("\n// bytewise tamper that preserves existing semantic checks\n")
                    elif tampered_acceptance_tool.endswith(".py"):
                        handle.write("\n# bytewise tamper that preserves existing semantic checks\n")
                    else:
                        handle.write("\n")

            archive = delivery / "taiji-agentv1.0-kylin-build-src-test.tar.gz"
            archive.write_bytes(b"fake source archive\n")
            archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (delivery / "SHA256SUMS.txt").write_text(
                f"{archive_digest}  {archive.name}\n",
                encoding="utf-8",
            )

            deb = output_dir / "taiji-agent_1.0.0_amd64.deb"
            deb.write_bytes(b"fake deb payload\n")
            deb_digest = hashlib.sha256(deb.read_bytes()).hexdigest()
            sidecar = Path(f"{deb}.sha256")
            sidecar_contents = {
                "valid": f"{deb_digest}  {deb.name}\n",
                "hash_mismatch": f"{'0' * 64}  {deb.name}\n",
                "basename_mismatch": f"{deb_digest}  another-package.deb\n",
                "missing": None,
            }
            contents = sidecar_contents[sidecar_mode]
            if contents is not None:
                sidecar.write_text(contents, encoding="utf-8")

            electron_payload = b"fake electron\n"
            desktop_payload = b"fake desktop\n"
            (output_dir / ".build-success").write_text("ok\n", encoding="utf-8")
            (output_dir / "taiji-package-manifest.json").write_text(
                json.dumps(
                    {
                        "electron_executable_sha256": hashlib.sha256(electron_payload).hexdigest(),
                        "desktop_entry_sha256": hashlib.sha256(desktop_payload).hexdigest(),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "构建报告.txt").write_text("ok\n", encoding="utf-8")
            if extra_output_case == "old_deb":
                (output_dir / "old.deb").write_bytes(b"stale deb\n")
            elif extra_output_case == "extra_sidecar":
                (output_dir / "old.deb.sha256").write_text("stale\n", encoding="utf-8")
            elif extra_output_case == "zip":
                (output_dir / "historical-build.zip").write_bytes(b"stale zip\n")
            elif extra_output_case == "nested_history":
                historical = output_dir / "history"
                historical.mkdir()
                (historical / "old.deb").write_bytes(b"stale nested deb\n")
            elif extra_output_case == "extra_file":
                (output_dir / "notes.txt").write_text("unexpected\n", encoding="utf-8")
            elif extra_output_case == "symlink":
                external_sidecar = tmp_path / "external-sidecar"
                shutil.copy2(sidecar, external_sidecar)
                sidecar.unlink()
                sidecar.symlink_to(external_sidecar)
            elif extra_output_case == "hardlink":
                os.link(deb, tmp_path / "external-deb-hardlink")
            elif extra_output_case == "fifo":
                os.mkfifo(output_dir / "package-fifo")
            elif extra_output_case is not None:
                raise AssertionError(f"unknown extra output case: {extra_output_case}")
            repo_deb = offline_repo / deb.name
            repo_deb.write_bytes(deb.read_bytes())
            packages_payload = (
                "Package: taiji-agent\n"
                "Version: 1.0.0\n"
                "Architecture: amd64\n"
                f"Filename: ./{repo_deb.name}\n"
                f"Size: {repo_deb.stat().st_size}\n"
                f"SHA256: {deb_digest}\n\n"
            ).encode("utf-8")
            (offline_repo / "Packages").write_bytes(packages_payload)
            (offline_repo / "Packages.gz").write_bytes(gzip.compress(packages_payload))
            (offline_repo / "runtime-dependencies.txt").write_text("", encoding="utf-8")
            checksum_lines = []
            for path in sorted(offline_repo.iterdir()):
                if path.name == "SHA256SUMS.txt":
                    continue
                checksum_lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
            (offline_repo / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
            verifier.write_text("raise SystemExit(0)\n", encoding="utf-8")

            unpack_log = tmp_path / "dpkg-deb.log"
            fake_dpkg_deb = fake_bin / "dpkg-deb"
            fake_dpkg_deb.write_text(
                "#!/usr/bin/env bash\n"
                "set -eu\n"
                "printf '%s\\n' \"$*\" >> \"$TAIJI_TEST_DPKG_LOG\"\n"
                "if [ \"${1:-}\" = \"--info\" ]; then exit 0; fi\n"
                "if [ \"${1:-}\" = \"-f\" ]; then\n"
                "  case \"${3:-}\" in\n"
                "    Package) printf 'taiji-agent\\n' ;;\n"
                "    Version) printf '1.0.0\\n' ;;\n"
                "    Architecture) printf 'amd64\\n' ;;\n"
                "    *) exit 2 ;;\n"
                "  esac\n"
                "  exit 0\n"
                "fi\n"
                "[ \"${1:-}\" = \"-x\" ] || exit 2\n"
                "mkdir -p \"$3/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist\" \"$3/usr/share/applications\"\n"
                "printf 'fake electron\\n' > \"$3/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron\"\n"
                "printf 'fake desktop\\n' > \"$3/usr/share/applications/taiji-agent.desktop\"\n",
                encoding="utf-8",
            )
            fake_dpkg_deb.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                    "TAIJI_RELEASE_REQUIRE_ARTIFACTS": "1",
                    "TAIJI_RELEASE_SKIP_GIT_CHECK": "1",
                    "TAIJI_REPO_ROOT": str(repo_root),
                    "TAIJI_TEST_DPKG_LOG": str(unpack_log),
                }
            )
            result = subprocess.run(
                ["bash", str(script)],
                cwd=delivery,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            unpack_calls = unpack_log.read_text(encoding="utf-8") if unpack_log.exists() else ""

        return result, unpack_calls

    def test_release_preflight_rejects_every_non_allowlisted_package_output_entry(self):
        if not shutil.which("sha256sum"):
            self.skipTest("sha256sum is required by release preflight")

        cases = (
            "old_deb",
            "extra_sidecar",
            "zip",
            "nested_history",
            "extra_file",
            "symlink",
            "hardlink",
            "fifo",
        )
        for extra_output_case in cases:
            with self.subTest(extra_output_case=extra_output_case):
                result, unpack_calls = self._run_release_preflight_artifact_gate(
                    "valid",
                    extra_output_case=extra_output_case,
                )
                output = result.stdout + result.stderr

                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn("生成的安装包/ 含不允许的条目", output)
                self.assertEqual(unpack_calls, "", output)

    def test_release_preflight_rejects_bytewise_modified_staged_acceptance_tool(self):
        if not shutil.which("sha256sum"):
            self.skipTest("sha256sum is required by release preflight")

        for filename in (
            "04_目标终端_桌面App验收并导出证据.sh",
            "run-installed-electron-acceptance.js",
            "assemble-target-evidence.py",
            "validate-taiji-release-evidence.py",
            "signing-public.pem",
        ):
            with self.subTest(filename=filename):
                result, unpack_calls = self._run_release_preflight_artifact_gate(
                    "valid",
                    tampered_acceptance_tool=filename,
                )
                output = result.stdout + result.stderr

                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn("目标终端验收工具与当前源码不一致", output)
                self.assertEqual(unpack_calls, "", output)

    def test_release_preflight_rejects_invalid_deb_checksum_sidecars_before_payload_unpack(self):
        if not shutil.which("sha256sum"):
            self.skipTest("sha256sum is required by release preflight")

        cases = (
            ("missing", "缺少 DEB SHA256 sidecar"),
            ("hash_mismatch", "DEB SHA256 不匹配"),
            ("basename_mismatch", "DEB SHA256 sidecar 指向的文件不是当前 DEB"),
        )
        for sidecar_mode, expected_error in cases:
            with self.subTest(sidecar_mode=sidecar_mode):
                result, unpack_calls = self._run_release_preflight_artifact_gate(sidecar_mode)
                output = result.stdout + result.stderr

                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(expected_error, output)
                self.assertEqual(unpack_calls, "", output)

    def test_release_preflight_accepts_valid_deb_checksum_before_payload_unpack(self):
        if not shutil.which("sha256sum"):
            self.skipTest("sha256sum is required by release preflight")

        result, unpack_calls = self._run_release_preflight_artifact_gate("valid")
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("DEB SHA256 sidecar 校验通过", output)
        self.assertIn("-x ", unpack_calls)

    def test_offline_builder_generates_manifest_and_does_not_refresh_lock_by_default(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn("MANIFEST_FILE", builder)
        self.assertIn("taiji-package-manifest.json", builder)
        self.assertIn("write_release_manifest", builder)
        self.assertIn("packages_sha256", builder)
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
        self.assertIn("packages_sha256", install)
        self.assertIn("packages_gz_sha256", install)
        self.assertIn("verify_deb_checksum", install)
        self.assertNotIn('sha256sum -c "$(basename "$CHECKSUM_PATH")"', install)

    def test_delivery_install_script_requires_offline_repo_unless_explicitly_online(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn('ONLINE_OK="${ONLINE_OK:-0}"', install)
        self.assertIn("缺少离线依赖仓库", install)
        self.assertIn("ONLINE_OK=1", install)
        self.assertIn("完全离线发布包", install)

    def test_install_script_uses_root_owned_staged_inputs_for_offline_repo(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn('ROOT_INSTALL_STAGING=""', install)
        self.assertIn('STAGED_DEB_PATH=""', install)
        self.assertIn('sudo mktemp -d "/var/tmp/taiji-agent-install.XXXXXX"', install)
        self.assertIn("OFFLINE_APT_LISTS_DIR", install)
        self.assertIn("Dir::State::Lists=$lists_dir", install)
        self.assertIn('source_file="$ROOT_INSTALL_STAGING/taiji-agent-offline.list"', install)
        self.assertIn('lists_dir="$ROOT_INSTALL_STAGING/apt-lists"', install)
        self.assertIn('verify_staged_install_inputs', install)
        self.assertIn('publish_staged_install_inputs', install)
        self.assertIn('sudo chown _apt:root "$ROOT_INSTALL_STAGING/apt-lists/partial"', install)
        self.assertIn('sudo chmod 0700 "$ROOT_INSTALL_STAGING/apt-lists/partial"', install)
        self.assertIn("EXPECTED_BUILD_MARKER_FILE_SHA256", install)
        self.assertIn("EXPECTED_CHECKSUM_FILE_SHA256", install)
        self.assertIn("EXPECTED_MANIFEST_FILE_SHA256", install)
        self.assertIn("stat -c '%h'", install)
        self.assertIn('sudo install -o root -g root -m 0600 /dev/null "$destination"', install)
        self.assertIn("离线 apt 仓库索引更新失败", install)
        self.assertLess(install.index('*"离线 apt 仓库索引"*'), install.index('*"管理员权限"*'))
        self.assertNotIn('lists_dir="$LOG_DIR/apt-lists"', install)

        install_package = install[
            install.index("install_package() {") : install.index("verify_installation() {")
        ]
        self.assertLess(
            install_package.index("stage_privileged_install_inputs"),
            install_package.index("prepare_legacy_replacement"),
        )
        package_install = install[
            install.index("install_taiji_package() {") : install.index("install_package() {")
        ]
        self.assertIn('"$STAGED_DEB_PATH"', package_install)
        self.assertNotIn('"$DEB_PATH"', package_install)

        cleanup = install[
            install.index("cleanup_offline_apt_repo_mount() {") : install.index("require_admin_capability() {")
        ]
        self.assertIn('sudo rm -rf -- "$ROOT_INSTALL_STAGING"', cleanup)
        self.assertIn("root staging 清理失败", cleanup)
        self.assertNotIn('|| true', cleanup)

        publish = install[
            install.index("publish_staged_install_inputs() {") : install.index("stage_privileged_install_inputs() {")
        ]
        self.assertLess(
            publish.index("getent passwd _apt"),
            publish.index('if [ "$STAGED_OFFLINE_REPO_AVAILABLE" = "1" ]'),
        )

    def test_install_script_requires_explicit_headless_rehearsal_mode(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn('TAIJI_ALLOW_HEADLESS_REHEARSAL="${TAIJI_ALLOW_HEADLESS_REHEARSAL:-0}"', install)
        self.assertIn('TAIJI_ALLOW_HEADLESS_REHEARSAL=1', install)
        self.assertIn("仅离线安装演练，不是桌面 App/目标机验证", install)
        self.assertIn("真实模型对话和目标机验证：未验证", install)

        main = install[install.index("main() {") :]
        self.assertLess(
            main.index("require_desktop_session_or_rehearsal"),
            main.index("validate_install_inputs"),
        )
        self.assertLess(
            main.index("validate_install_inputs"),
            main.index("require_admin_capability"),
        )
        self.assertLess(
            main.index("require_admin_capability"),
            main.index('set_stage "安装太极 Agent"'),
        )
        self.assertIn("不要使用 sudo bash", install)
        root_guard = install.index('if [ "$EUID" -eq 0 ]; then')
        self.assertLess(root_guard, install.index('mkdir -p "$LOG_DIR"'))
        self.assertLess(root_guard, install.index('exec > >(tee -a "$LOG_FILE") 2>&1'))
        verify = install[
            install.index("verify_installation() {") : install.index("main() {")
        ]
        self.assertNotIn('TAIJI_ALLOW_HEADLESS_REHEARSAL" != "1"', verify)

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

    def test_offline_builder_only_deletes_owned_dedicated_build_roots(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn("validate_safe_build_root_path", builder)
        self.assertIn('taiji-agent-build-*', builder)
        self.assertIn(".taiji-build-root-owner", builder)
        self.assertIn("require_owned_build_root", builder)
        self.assertIn('stat -c \'%u\' "$BUILD_ROOT"', builder)
        self.assertIn('stat -c \'%a\' "$BUILD_ROOT"', builder)
        self.assertIn('chmod 0700 "$BUILD_ROOT"', builder)

        reset = builder[
            builder.index("reset_build_root() {") :
            builder.index("repair_build_tree_permissions() {")
        ]
        self.assertLess(reset.index("require_owned_build_root"), reset.index('rm -rf -- "$BUILD_ROOT"'))

        cleanup = builder[
            builder.index("cleanup_temporary_build_root() {") :
            builder.index("apt_source_summary() {")
        ]
        self.assertLess(cleanup.index("require_owned_build_root"), cleanup.index('rm -rf -- "$BUILD_ROOT"'))

    def test_offline_builder_keeps_build_machine_logs_outside_the_delivery(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        docs = read_text("taijiagent 打包交付/操作说明.md")
        version_info = read_text("taijiagent 打包交付/版本信息.txt")

        self.assertNotRegex(builder, r'(?m)^LOG_DIR="\$SCRIPT_DIR/构建日志"$')
        self.assertIn("XDG_STATE_HOME", builder)
        self.assertIn("taiji-agent/build-logs", builder)
        self.assertIn('chmod 0700 "$LOG_DIR"', builder)
        self.assertIn('"$SCRIPT_DIR/构建日志"', builder)
        self.assertIn("交付目录残留旧构建日志", builder)
        self.assertIn("~/.local/state/taiji-agent/build-logs", docs)
        self.assertNotIn("会生成 `构建日志/失败诊断", docs)
        self.assertIn("XDG_STATE_HOME", version_info)
        self.assertIn("taiji-agent/build-logs", version_info)
        self.assertNotIn("制包失败会生成 构建日志/", version_info)

    def test_offline_builder_materializes_locked_portable_resvg_dependencies_before_docx_tests(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        materializer = read_text(
            "hermes-local-lab/sources/docx-engine-v2/scripts/materialize-portable-resvg-dependencies.js"
        )
        renderer = read_text("hermes-local-lab/sources/docx-engine-v2/src/rendering/render-docx.js")

        self.assertIn("materialize-portable-resvg-dependencies.js", builder)
        docx_build = builder[
            builder.index('cd "$(source_lab_dir)/sources/docx-engine-v2"') :
            builder.index('info "构建 DEB 安装包"')
        ]
        self.assertLess(
            docx_build.index("materialize-portable-resvg-dependencies.js"),
            docx_build.index("npm test"),
        )
        self.assertIn("mkdtempSync", renderer)
        self.assertIn("['TMPDIR', 'TMP', 'TEMP']", renderer)
        self.assertIn("process.env[key]", renderer)
        self.assertIn("process.once('exit'", renderer)
        for package_name in (
            "@resvg/resvg-js-linux-x64-gnu",
            "@resvg/resvg-js-linux-x64-musl",
            "@resvg/resvg-js-linux-arm64-gnu",
            "@resvg/resvg-js-linux-arm64-musl",
        ):
            self.assertIn(package_name, materializer)
        self.assertIn("sha512-", materializer)
        self.assertIn("npm", materializer)
        self.assertIn("pack", materializer)
        self.assertIn("--ignore-scripts", materializer)

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

    def test_desktop_payload_uses_an_explicit_runtime_file_allowlist(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        start = build.index('mkdir -p "$DESKTOP_RUNTIME/src"')
        end = build.index('install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent"')
        desktop_stage = build[start:end]

        self.assertIn('DESKTOP_RUNTIME="$INSTALL_ROOT/apps/taiji-desktop"', build)
        self.assertNotIn('"$APP_DIR" "$INSTALL_ROOT/apps/"', desktop_stage)
        self.assertIn(
            'install -m 0644 "$APP_DIR/package.json" "$DESKTOP_RUNTIME/package.json"',
            desktop_stage,
        )
        self.assertIn(
            'install -m 0644 "$APP_DIR/src/main.js" "$DESKTOP_RUNTIME/src/main.js"',
            desktop_stage,
        )
        self.assertIn(
            'install -m 0644 "$APP_DIR/src/preload.js" "$DESKTOP_RUNTIME/src/preload.js"',
            desktop_stage,
        )
        self.assertIn("stage-electron-runtime.py", build)
        self.assertIn('--source "$APP_DIR/node_modules/electron"', desktop_stage)
        self.assertIn('--destination "$DESKTOP_RUNTIME/node_modules/electron"', desktop_stage)
        self.assertIn("--require-linux-x86-64", desktop_stage)
        self.assertNotIn('"$APP_DIR/node_modules"/ "$DESKTOP_RUNTIME/node_modules"/', desktop_stage)
        self.assertNotIn('"$APP_DIR/package-lock.json"', desktop_stage)
        self.assertNotIn("--exclude '.package-lock.json'", desktop_stage)

    def test_offline_builder_omits_desktop_development_dependencies(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        start = builder.index('cd "$SRC_DIR/apps/taiji-desktop"')
        end = builder.index('info "准备 DOCX Engine V2 生产依赖并执行源码测试"')

        self.assertIn("npm_ci_with_network_fallback --omit=dev", builder[start:end])

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
