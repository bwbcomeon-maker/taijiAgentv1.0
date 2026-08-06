import gzip
import hashlib
import importlib.util
import json
import os
import py_compile
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def build_function_source(name: str, next_name: str) -> str:
    build = read_text("packaging/linux/deb/build-deb.sh")
    start = build.index(f"{name}() {{")
    end = build.index(f"\n}}\n\n{next_name}", start) + len("\n}")
    return build[start:end]



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




class LinuxDesktopPackagingStaticTest(unittest.TestCase):
    def test_sync_packaged_config_loads_sourceless_packaged_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            packaged_lab = temp_root / "taiji-lab"
            scripts_dir = packaged_lab / "scripts"
            scripts_dir.mkdir(parents=True)
            sync_script = scripts_dir / "sync-packaged-config.py"
            shutil.copy2(
                ROOT / "hermes-local-lab/scripts/sync-packaged-config.py",
                sync_script,
            )
            (scripts_dir / "yaml.py").write_text(
                "def safe_load(text):\n"
                "    if 'template' in text:\n"
                "        return {'model': {'provider': 'deepseek'}}\n"
                "    return {}\n",
                encoding="utf-8",
            )

            package_dir = packaged_lab / "runtime" / "agent" / "agent"
            package_dir.mkdir(parents=True)
            module_sources = {
                "__init__.py": "",
                "image_gen_verification.py": (
                    "def reconcile_capability_config_epochs(current, target):\n"
                    "    return None\n"
                ),
                "provider_credentials.py": (
                    "from pathlib import Path\n"
                    "def seed_config_payload_strict(payload, *, config_path):\n"
                    "    Path(config_path).write_bytes(payload)\n"
                    "def mutate_config_strict(mutator, *, config_path):\n"
                    "    current = {}\n"
                    "    mutator(current)\n"
                    "    Path(config_path).write_text('mutated\\n', encoding='utf-8')\n"
                ),
            }
            for basename, source in module_sources.items():
                source_path = package_dir / basename
                source_path.write_text(source, encoding="utf-8")
                py_compile.compile(
                    str(source_path),
                    cfile=str(source_path.with_suffix(".pyc")),
                    doraise=True,
                )
                source_path.unlink()

            template = temp_root / "template.yaml"
            target = temp_root / "user" / "config.yaml"
            template.write_text("template\n", encoding="utf-8")
            target.parent.mkdir(parents=True)
            target.write_text("existing\n", encoding="utf-8")
            clean_env = os.environ.copy()
            clean_env.pop("PYTHONPATH", None)
            clean_env.pop("TAIJI_AGENT_AGENT_DIR", None)

            result = subprocess.run(
                [sys.executable, "-S", str(sync_script), str(template), str(target)],
                text=True,
                capture_output=True,
                check=False,
                cwd=temp_root,
                env=clean_env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "mutated\n")

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

    def test_deb_stages_desktop_dependencies_and_release_identity_manifest(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        offline_builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        input_builder = read_text("taijiagent 打包交付/99_本机_准备制包输入包.sh")
        release_preflight = read_text("taijiagent 打包交付/01_制包机_发布预检.sh")
        target_acceptance = read_text("taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh")
        release_check = read_text("scripts/taiji-release-check.sh")
        release_signer = read_text("scripts/sign-taiji-release-evidence.sh")
        rehearsal_producer = read_text("scripts/produce-taiji-offline-rehearsal.py")
        rehearsal_runner = read_text("tools/taiji-offline-rehearsal/run-lifecycle.sh")
        evidence_validator = read_text("scripts/validate-taiji-release-evidence.py")

        self.assertIn(
            'node "$DESKTOP_JS_STAGER"',
            build,
        )
        self.assertIn('--entry main.js', build)
        self.assertIn('--entry preload.js', build)
        self.assertIn('POLICY_FILE="$REPO_ROOT/packaging/linux/compatibility-policy.json"', build)
        self.assertIn('POLICY_INSTALL_PATH="$INSTALL_ROOT/resources/linux-compatibility-policy.json"', build)
        self.assertIn('LAUNCH_MANIFEST_PATH="$INSTALL_ROOT/resources/taiji-release-manifest.json"', build)
        self.assertIn('ABI_REPORT_PATH="$INSTALL_ROOT/resources/elf-abi-audit.json"', build)
        self.assertIn('write_launch_manifest', build)
        self.assertIn('write_package_manifest', build)
        for field in (
            '"schema": "taiji-package-manifest/v3"',
            '"package": "$TAIJI_PACKAGE_NAME"',
            '"architecture": "$TAIJI_PACKAGE_ARCHITECTURE"',
            '"source_commit": "$SOURCE_COMMIT"',
            '"compatibility_policy_id": "$POLICY_ID"',
            '"compatibility_policy_sha256": "$POLICY_SHA256"',
            '"elf_abi_audit_basename": "elf-abi-audit.json"',
        ):
            self.assertIn(field, build)
        self.assertIn('TAIJI_SOURCE_COMMIT="$source_commit"', offline_builder)
        self.assertIn('POLICY_FILE="$SRC_DIR/packaging/linux/compatibility-policy.json"', offline_builder)
        self.assertIn("load_source_controlled_policy", offline_builder)
        self.assertIn("CANDIDATE_DEB_FIXED=1", offline_builder)
        self.assertNotIn('"schema_version": 2', offline_builder)
        self.assertNotIn("target_baseline", offline_builder)
        self.assertNotIn("target-baseline", offline_builder)
        self.assertIn("^[0-9a-f]{40}$", build)
        self.assertIn('dpkg-deb --root-owner-group', build)
        self.assertIn("^[0-9a-f]{40}$", offline_builder)
        for source in (offline_builder, input_builder, release_preflight):
            self.assertIn("rev-parse HEAD", source)
            self.assertNotIn("rev-parse --short=8 HEAD", source)
        for source in (release_check, release_signer, rehearsal_producer):
            self.assertNotIn("rev-parse --short=8 HEAD", source)
        self.assertIn(
            'if type(schema_version) is not int or schema_version != 2:',
            target_acceptance,
        )
        self.assertIn(
            'if not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]):',
            target_acceptance,
        )
        self.assertIn('FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")', evidence_validator)
        self.assertGreaterEqual(evidence_validator.count('"schema_version": 2'), 2)
        self.assertIn("^[0-9a-f]{40}$", rehearsal_runner)

    def test_deb_declares_electron_runtime_libraries_from_canonical_contract(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        policy = json.loads(read_text("packaging/linux/compatibility-policy.json"))
        self.assertEqual(policy["debian"]["depends"], ["ca-certificates", "libc6 (>= 2.31)"])
        self.assertIn('POLICY_HELPER="$REPO_ROOT/packaging/linux/compatibility_policy.py"', build)
        self.assertIn('eval "$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-shell)"', build)
        self.assertIn('Depends: $TAIJI_DEBIAN_DEPENDS', build)
        self.assertNotIn("runtime-depends.txt", build)
        self.assertNotIn("render-depends", build)

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
        if importlib.util.find_spec("yaml") is None:
            self.skipTest("PyYAML is not installed in this test environment")
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

    def test_all_installed_entrypoints_pin_opt_profile_and_sanitize_code_environment(self):
        installed_cli = read_text("packaging/linux/bin/taiji")
        installed_desktop = read_text("packaging/linux/bin/taiji-agent")
        installed_diagnose = read_text("packaging/linux/bin/taiji-agent-diagnose")
        installed_native_verify = read_text("packaging/linux/bin/taiji-native-verify")
        source_cli = read_text("hermes-local-lab/scripts/taiji")

        for entrypoint in (
            installed_cli,
            installed_desktop,
            installed_diagnose,
            installed_native_verify,
        ):
            self.assertTrue(entrypoint.startswith("#!/bin/bash -p\n"))
            self.assertIn('APP_ROOT="/opt/taiji-agent"', entrypoint)
            self.assertNotIn("${TAIJI_AGENT_ROOT:-/opt/taiji-agent}", entrypoint)
            self.assertIn('TAIJI_LAUNCH_PROFILE="installed-production"', entrypoint)
            self.assertIn('TAIJI_AGENT_ROOT="$APP_ROOT"', entrypoint)
            self.assertIn('TAIJI_AGENT_USE_USER_DIRS="1"', entrypoint)
            self.assertIn('/usr/bin/env -0', entrypoint)
            self.assertIn('/usr/bin/env "${_taiji_unset_args[@]}"', entrypoint)
            self.assertIn('/bin/bash --noprofile --norc -p "$0" "$@"', entrypoint)
            for selector in (
                "PYTHON*", "NODE_*", "LD_*", "DYLD_*", "BASH_FUNC_*",
                "BASH_ENV", "ENV", "ELECTRON_RUN_AS_NODE",
                "TAIJI_AGENT_PYTHON", "TAIJI_WEBUI_PYTHON", "TAIJI_PYTHON",
                "TAIJI_SOURCE_*", "TAIJI_LAUNCH_PROFILE",
            ):
                self.assertIn(selector, entrypoint)

        self.assertIn('TAIJI_AGENT_ROOT="$LAB_DIR"', source_cli)
        self.assertNotIn('TAIJI_LAUNCH_PROFILE="installed-production"', source_cli)

    def test_installed_entrypoints_reject_inherited_code_and_path_selectors(self):
        with tempfile.TemporaryDirectory(prefix="taiji-installed-entrypoint-") as tmp:
            fixture_root = Path(tmp).resolve()
            app_root = fixture_root / "opt" / "taiji-agent"
            wrapper_dir = fixture_root / "bin"
            runtime_env = app_root / "scripts" / "runtime-env.sh"
            python_entry = app_root / "runtime" / "agent" / "venv" / "bin" / "python"
            diagnose_entry = app_root / "scripts" / "taiji-agent-diagnose"
            native_verify_entry = app_root / "scripts" / "taiji-native-verify"
            electron_entry = (
                app_root / "apps" / "taiji-desktop" / "node_modules" /
                "electron" / "dist" / "electron"
            )
            wrapper_dir.mkdir(parents=True)
            python_entry.parent.mkdir(parents=True)
            electron_entry.parent.mkdir(parents=True)
            runtime_env.parent.mkdir(parents=True, exist_ok=True)

            runtime_env.write_text(
                'AGENT_DIR="$TAIJI_AGENT_ROOT/runtime/agent"\n'
                'TAIJI_AGENT_AGENT_DIR="$AGENT_DIR"\n'
                'TAIJI_AGENT_WEBUI_DIR="$TAIJI_AGENT_ROOT/runtime/web"\n'
                'TAIJI_AGENT_PYTHON="$AGENT_DIR/venv/bin/python"\n'
                'TAIJI_WEBUI_PYTHON="$TAIJI_AGENT_PYTHON"\n'
                'TAIJI_WEBUI_AGENT_DIR="$AGENT_DIR"\n'
                'export AGENT_DIR TAIJI_AGENT_AGENT_DIR TAIJI_AGENT_WEBUI_DIR\n'
                'export TAIJI_AGENT_PYTHON TAIJI_WEBUI_PYTHON TAIJI_WEBUI_AGENT_DIR\n',
                encoding="utf-8",
            )
            python_entry.write_text(
                "#!/bin/bash -p\n/usr/bin/env\n",
                encoding="utf-8",
            )
            diagnose_entry.write_text(
                "#!/bin/bash -p\n"
                "source \"$TAIJI_AGENT_ROOT/scripts/runtime-env.sh\"\n"
                "/usr/bin/env\n",
                encoding="utf-8",
            )
            native_verify_entry.write_text(
                "#!/bin/bash -p\n/usr/bin/env\n",
                encoding="utf-8",
            )
            electron_entry.write_text(
                "#!/bin/bash -p\n/usr/bin/env\n",
                encoding="utf-8",
            )
            python_entry.chmod(0o755)
            diagnose_entry.chmod(0o755)
            native_verify_entry.chmod(0o755)
            electron_entry.chmod(0o755)

            wrappers = []
            for basename in (
                "taiji",
                "taiji-agent",
                "taiji-agent-diagnose",
                "taiji-native-verify",
            ):
                source = read_text(f"packaging/linux/bin/{basename}")
                staged = wrapper_dir / basename
                staged.write_text(
                    source.replace("/opt/taiji-agent", str(app_root)),
                    encoding="utf-8",
                )
                staged.chmod(0o755)
                wrappers.append(staged)

            bash_env_marker = fixture_root / "bash-env-executed"
            bash_env = fixture_root / "hostile-bash-env.sh"
            bash_env.write_text(
                f"/usr/bin/touch {bash_env_marker}\n",
                encoding="utf-8",
            )
            hostile = os.environ.copy()
            hostile.update(
                {
                    "TAIJI_AGENT_ROOT": "/tmp/evil-root",
                    "TAIJI_AGENT_AGENT_DIR": "/tmp/evil-agent",
                    "TAIJI_AGENT_PYTHON": "/tmp/evil-python",
                    "TAIJI_LAUNCH_PROFILE": "source",
                    "TAIJI_SOURCE_ROOT": "/tmp/evil-source",
                    "TAIJI_SOURCE_COMMIT": "evil",
                    "TAIJI_RELEASE_COMMIT": "evil",
                    "PYTHONPATH": "/tmp/evil-pythonpath",
                    "NODE_OPTIONS": "--require=/tmp/evil-node.js",
                    "LD_FAKE_SELECTOR": "/tmp/evil-loader",
                    "BASH_ENV": str(bash_env),
                    "ELECTRON_RUN_AS_NODE": "1",
                    "BASH_FUNC_taiji_probe%%": "() { /usr/bin/touch /tmp/evil-function; }",
                }
            )

            for wrapper in wrappers:
                result = subprocess.run(
                    [str(wrapper), "probe"],
                    text=True,
                    capture_output=True,
                    check=False,
                    env=hostile,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertIn(f"TAIJI_AGENT_ROOT={app_root}", output)
                self.assertIn("TAIJI_LAUNCH_PROFILE=installed-production", output)
                if wrapper.name != "taiji-native-verify":
                    self.assertIn(f"TAIJI_AGENT_PYTHON={python_entry}", output)
                for removed in (
                    "TAIJI_SOURCE_ROOT=", "TAIJI_SOURCE_COMMIT=", "TAIJI_RELEASE_COMMIT=",
                    "PYTHONPATH=", "NODE_OPTIONS=", "LD_FAKE_SELECTOR=", "BASH_ENV=",
                    "ELECTRON_RUN_AS_NODE=", "BASH_FUNC_taiji_probe%%=",
                ):
                    self.assertNotIn(removed, output)
                self.assertNotIn("/tmp/evil", output)
            self.assertFalse(bash_env_marker.exists())

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
        diagnose = read_text("hermes-local-lab/scripts/taiji-agent-diagnose")
        self.assertIn("runtime.source=installed-payload", diagnose)
        self.assertIn("network.mode=offline-safe", diagnose)
        self.assertNotIn("deepseek_key.canonical.suffix", diagnose)
        self.assertNotIn("base_url=", diagnose)
        self.assertNotIn("pgrep -af", diagnose)
        self.assertNotIn("tail -120", diagnose)
        self.assertIn("env.TAIJI_RUNTIME_HOME", main_js)
        self.assertIn('path.join(userDataDir(), "runtime-home")', main_js)

    def test_taiji_runtime_defaults_to_restricted_security_and_local_tmp(self):
        runtime_env = read_text("hermes-local-lab/scripts/runtime-env.sh")
        start_agent = read_text("hermes-local-lab/scripts/start-agent.sh")
        start_webui = read_text("hermes-local-lab/scripts/start-webui.sh")
        main_js = read_text("apps/taiji-desktop/src/main.js")
        launch_profile = read_text("apps/taiji-desktop/src/launch-profile.js")

        self.assertIn('TAIJI_SECURITY_MODE="${TAIJI_SECURITY_MODE:-restricted}"', runtime_env)
        self.assertIn('"$TAIJI_RUNTIME_HOME/skills"', runtime_env)
        self.assertIn('"$TAIJI_RUNTIME_HOME/scripts"', runtime_env)
        for var in ("TMPDIR", "TMP", "TEMP"):
            self.assertIn(f'export {var}="$TMP_DIR"', runtime_env)
        self.assertIn("TAIJI_SECURITY_MODE", start_agent)
        self.assertIn("TAIJI_SECURITY_MODE", start_webui)
        self.assertIn("applySecurityProfile", main_js)
        self.assertIn('profile.name === "local_controlled"', launch_profile)
        self.assertIn('runtimeEnv.TAIJI_SECURITY_MODE = sourceEnv.TAIJI_SECURITY_MODE || profile.mode', launch_profile)
        self.assertIn('runtimeEnv.TAIJI_SECURITY_PROFILE = "strict"', launch_profile)
        self.assertIn('runtimeEnv.TAIJI_SECURITY_MODE = "restricted"', launch_profile)
        for var in (
            "TAIJI_ALLOW_TERMINAL",
            "TAIJI_ALLOW_EXECUTE_CODE",
            "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
            "TAIJI_ALLOW_DELEGATE_TASK",
        ):
            self.assertIn(var, launch_profile)

    def test_installed_runtime_reasserts_strict_after_user_dotenv(self):
        source_runtime_env = ROOT / "hermes-local-lab/scripts/runtime-env.sh"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir).resolve()
            install_root = temp_root / "opt/taiji-agent"
            runtime_env = install_root / "scripts/runtime-env.sh"
            python_path = install_root / "runtime/agent/venv/bin/python"
            (install_root / "scripts").mkdir(parents=True)
            python_path.parent.mkdir(parents=True)
            (install_root / "runtime/web").mkdir(parents=True)
            python_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python_path.chmod(0o755)
            runtime_env.write_text(
                source_runtime_env.read_text(encoding="utf-8").replace(
                    "/opt/taiji-agent", str(install_root)
                ),
                encoding="utf-8",
            )
            runtime_home = temp_root / "runtime-home"
            runtime_home.mkdir()
            (runtime_home / ".env").write_text(
                "TAIJI_SECURITY_PROFILE=full\n"
                "TAIJI_SECURITY_MODE=full\n"
                "TAIJI_ALLOW_TERMINAL=1\n"
                "TAIJI_ALLOW_FUTURE_RELAXATION=true\n"
                "TAIJI_RELEASE_VERSION=forged-version\n"
                "TAIJI_RELEASE_COMMIT=forged-commit\n"
                "TAIJI_SOURCE_ROOT=/tmp/forged-source\n"
                "TAIJI_SOURCE_COMMIT=forged-source-commit\n"
                "TAIJI_SOURCE_DIRTY=1\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"; printf "%s\\n" '
                    '"$TAIJI_SECURITY_PROFILE" "$TAIJI_SECURITY_MODE" '
                    '"$TAIJI_ALLOW_TERMINAL" "$TAIJI_ALLOW_FUTURE_RELAXATION" '
                    '"$TAIJI_RELEASE_VERSION" "$TAIJI_RELEASE_COMMIT" '
                    '"${TAIJI_SOURCE_ROOT:-unset}" "${TAIJI_SOURCE_COMMIT:-unset}" '
                    '"${TAIJI_SOURCE_DIRTY:-unset}"',
                    "bash",
                    str(runtime_env),
                ],
                env={
                    **os.environ,
                    "TAIJI_LAUNCH_PROFILE": "installed-production",
                    "TAIJI_RELEASE_VERSION": "1.2.3",
                    "TAIJI_RELEASE_COMMIT": "0123456789abcdef0123456789abcdef01234567",
                    "TAIJI_RUNTIME_HOME": str(runtime_home),
                    "XDG_STATE_HOME": str(temp_root / "state"),
                    "XDG_DATA_HOME": str(temp_root / "data"),
                    "XDG_CONFIG_HOME": str(temp_root / "config"),
                    "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "strict",
                    "restricted",
                    "0",
                    "0",
                    "1.2.3",
                    "0123456789abcdef0123456789abcdef01234567",
                    "unset",
                    "unset",
                    "unset",
                ],
            )

    def _run_installed_runtime_path_attack(self, *, inherited, dotenv):
        source_runtime_env = ROOT / "hermes-local-lab/scripts/runtime-env.sh"
        with tempfile.TemporaryDirectory() as temp_dir:
            physical_temp_root = Path(temp_dir).resolve()
            install_root = physical_temp_root / "opt/taiji-agent"
            scripts_dir = install_root / "scripts"
            agent_dir = install_root / "runtime/agent"
            webui_dir = install_root / "runtime/web"
            python_path = agent_dir / "venv/bin/python"
            runtime_home = physical_temp_root / "runtime-home"
            scripts_dir.mkdir(parents=True)
            python_path.parent.mkdir(parents=True)
            webui_dir.mkdir(parents=True)
            runtime_home.mkdir(parents=True)
            python_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python_path.chmod(0o755)
            runtime_env = scripts_dir / "runtime-env.sh"
            runtime_env.write_text(
                source_runtime_env.read_text(encoding="utf-8").replace(
                    "/opt/taiji-agent", str(install_root)
                ),
                encoding="utf-8",
            )
            (runtime_home / ".env").write_text(dotenv, encoding="utf-8")
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"; printf "%s\\n" '
                    '"$LAB_DIR" "$AGENT_DIR" "$WEBUI_DIR" '
                    '"$TAIJI_AGENT_ROOT" "$TAIJI_AGENT_AGENT_DIR" '
                    '"$TAIJI_AGENT_WEBUI_DIR" "$TAIJI_AGENT_PYTHON" '
                    '"$TAIJI_WEBUI_PYTHON" "$TAIJI_WEBUI_AGENT_DIR" '
                    '"${PYTHONPATH:-unset}" "${NODE_OPTIONS:-unset}" '
                    '"${ELECTRON_RUN_AS_NODE:-unset}" "$PATH"',
                    "bash",
                    str(runtime_env),
                ],
                env={
                    **os.environ,
                    **inherited,
                    "TAIJI_LAUNCH_PROFILE": "installed-production",
                    "TAIJI_RELEASE_VERSION": "1.2.3",
                    "TAIJI_RELEASE_COMMIT": "0123456789abcdef0123456789abcdef01234567",
                    "TAIJI_RUNTIME_HOME": str(runtime_home),
                    "XDG_STATE_HOME": str(physical_temp_root / "state"),
                    "XDG_DATA_HOME": str(physical_temp_root / "data"),
                    "XDG_CONFIG_HOME": str(physical_temp_root / "config"),
                    "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str(install_root),
                    str(agent_dir),
                    str(webui_dir),
                    str(install_root),
                    str(agent_dir),
                    str(webui_dir),
                    str(python_path),
                    str(python_path),
                    str(agent_dir),
                    "unset",
                    "unset",
                    "unset",
                    "/usr/bin:/bin:/usr/sbin:/sbin",
                ],
            )

    def test_installed_runtime_rejects_inherited_code_path_selectors(self):
        attack = {
            "TAIJI_AGENT_ROOT": "/tmp/taiji-evil-root",
            "TAIJI_AGENT_AGENT_DIR": "/tmp/taiji-evil-agent",
            "TAIJI_AGENT_WEBUI_DIR": "/tmp/taiji-evil-web",
            "TAIJI_AGENT_PYTHON": "/tmp/taiji-evil-python",
            "TAIJI_WEBUI_PYTHON": "/tmp/taiji-evil-web-python",
            "TAIJI_WEBUI_AGENT_DIR": "/tmp/taiji-evil-web-agent",
            "PYTHONPATH": "/tmp/taiji-evil-pythonpath",
            "NODE_OPTIONS": "--require=/tmp/taiji-evil-node.js",
            "ELECTRON_RUN_AS_NODE": "1",
            "PATH": "/tmp/taiji-evil-bin",
        }
        self._run_installed_runtime_path_attack(inherited=attack, dotenv="")

    def test_installed_runtime_rejects_dotenv_code_path_selectors(self):
        dotenv = (
            "TAIJI_AGENT_ROOT=/tmp/taiji-dotenv-root\n"
            "TAIJI_AGENT_AGENT_DIR=/tmp/taiji-dotenv-agent\n"
            "TAIJI_AGENT_WEBUI_DIR=/tmp/taiji-dotenv-web\n"
            "TAIJI_AGENT_PYTHON=/tmp/taiji-dotenv-python\n"
            "TAIJI_WEBUI_PYTHON=/tmp/taiji-dotenv-web-python\n"
            "TAIJI_WEBUI_AGENT_DIR=/tmp/taiji-dotenv-web-agent\n"
            "PYTHONPATH=/tmp/taiji-dotenv-pythonpath\n"
            "NODE_OPTIONS=--require=/tmp/taiji-dotenv-node.js\n"
            "ELECTRON_RUN_AS_NODE=1\n"
            "PATH=/tmp/taiji-dotenv-bin\n"
        )
        self._run_installed_runtime_path_attack(inherited={}, dotenv=dotenv)

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
        self.assertIn("taiji_tmp_policy=managed", diagnose)

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

    def test_root_release_check_gate_exists_and_requires_signed_certification(self):
        release_check = read_text("scripts/taiji-release-check.sh")
        docs = read_text("docs/taiji-sale-readiness.md")

        self.assertIn("run_root_tests", release_check)
        self.assertIn("run_agent_tests", release_check)
        self.assertIn("run_webui_tests", release_check)
        self.assertIn("tests/test_issue1800_file_html_interactions.py", release_check)
        self.assertIn("check_delivery_artifacts", release_check)
        self.assertIn("run_delivery_preflight", release_check)
        self.assertIn("TAIJI_RELEASE_REQUIRE_ARTIFACTS=1", release_check)
        self.assertIn("CERTIFICATION_SET", release_check)
        self.assertIn("release-evidence.json", release_check)
        self.assertIn("认证矩阵", docs)
        self.assertIn("x86_64/amd64", docs)

    def test_root_release_check_runs_all_release_evidence_tool_tests(self):
        release_check = read_text("scripts/taiji-release-check.sh")

        self.assertIn("tests.test_target_desktop_acceptance_producer", release_check)
        self.assertIn("tests.test_certification_set_v1", release_check)
        self.assertIn("tests.test_release_evidence_assembler_v3", release_check)
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

    def test_release_check_uses_signed_certification_gate(self):
        release_check = read_text("scripts/taiji-release-check.sh")
        docs = read_text("docs/taiji-sale-readiness.md")

        self.assertIn("check_certification_and_publication", release_check)
        self.assertIn("TAIJI_CERTIFICATION_CHALLENGE", release_check)
        self.assertIn("TAIJI_PUBLICATION_CHALLENGE", release_check)
        self.assertIn("python3", release_check)
        main = release_check[release_check.index("main() {") :]
        self.assertNotIn("check_offline_install_rehearsal", main)
        self.assertNotIn("check_target_verification", main)
        self.assertIn("certification-set.json", docs)
        self.assertIn("observe-single-deb-install.py", docs)
        self.assertIn("人工见证", docs)
        self.assertIn("不能被表述为机器自动识别", docs)

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
                        f'export TAIJI_CERTIFICATION_SET="{tmp_path / "certification-set.json"}"',
                        f'export TAIJI_RELEASE_EVIDENCE="{tmp_path / "release-evidence.json"}"',
                        f'source "{ROOT / "scripts/taiji-release-check.sh"}"',
                        "check_canonical_source() { :; }",
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
        self.assertIn("check_certification_and_publication", output)
        self.assertIn("1 项失败", output)

    def test_release_evidence_signer_uses_fixed_offline_trust_anchor(self):
        signer = read_text("scripts/sign-taiji-release-evidence.sh")
        release_check = read_text("scripts/taiji-release-check.sh")

        self.assertIn("tools/taiji-release-evidence/signing-public.pem", signer)
        self.assertIn("839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da", signer)
        self.assertIn("openssl dgst -sha256 -sign", signer)
        self.assertIn("openssl dgst -sha256 -verify", signer)
        self.assertIn('SIGNATURE="${EVIDENCE}.sig"', signer)
        self.assertIn('--attestation-signature "$RELEASE_SIGNATURE"', release_check)
        self.assertIn("EVIDENCE_ATTESTATION_EXPECTED_FINGERPRINT", release_check)
        self.assertIn("TAIJI_CERTIFICATION_CHALLENGE", signer)
        self.assertIn("TAIJI_PUBLICATION_CHALLENGE", signer)
        self.assertIn("TAIJI_RELEASE_SKIP_GIT_CHECK=0", release_check)
        self.assertIn("st_nlink", signer)
        self.assertIn("stat.S_IMODE", signer)
        self.assertIn("O_EXCL", signer)
        self.assertIn("used-challenges", signer)
        self.assertNotIn("TAIJI_OFFLINE_REHEARSAL_CHALLENGE", signer)
        self.assertNotIn("TAIJI_TARGET_ACCEPTANCE_CHALLENGE", signer)
        self.assertIn('"$CHALLENGE" = "$EXPECTED_CHALLENGE"', signer)
        self.assertIn("st_size > 1024 * 1024", signer)
        self.assertIn("O_NOFOLLOW", signer)
        self.assertIn("os.fsync(state_fd)", signer)
        self.assertIn('os.link(source, destination)', signer)
        self.assertIn('os.unlink(source)', signer)
        self.assertIn('exit 0', signer)

    def test_release_preflight_accepts_same_git_archive_from_a_different_gzip_encoder(self):
        if not all(shutil.which(command) for command in ("git", "gzip", "sha256sum")):
            self.skipTest("git, gzip, and sha256sum are required by release preflight")

        source_script = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            delivery = repo_root / "taijiagent 打包交付"
            delivery.mkdir(parents=True)
            script = delivery / source_script.name
            shutil.copy2(source_script, script)
            gate_dir = repo_root / "scripts"
            gate_dir.mkdir()
            shutil.copy2(ROOT / "scripts/check-clean-worktree.sh", gate_dir)
            shutil.copy2(ROOT / "scripts/taiji-trusted-git", gate_dir)
            (repo_root / ".gitignore").write_text(
                "/taijiagent 打包交付/taiji-agentv1.0-kylin-build-src-*.tar.gz\n"
                "/taijiagent 打包交付/SHA256SUMS.txt\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo_root)], check=True)
            subprocess.run(
                ["git", "-C", str(repo_root), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_root), "config", "user.name", "Release Test"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo_root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo_root), "commit", "-q", "-m", "fixture"],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            tar_payload = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "archive",
                    "--format=tar",
                    "--prefix=taiji-agentv1.0/",
                    "HEAD",
                ],
                check=True,
                capture_output=True,
            ).stdout
            alternate_gzip = gzip.compress(tar_payload, compresslevel=1, mtime=0)
            local_gzip = subprocess.run(
                ["gzip", "-n", "-6", "-c"],
                input=tar_payload,
                check=True,
                capture_output=True,
            ).stdout
            self.assertNotEqual(alternate_gzip, local_gzip)

            archive = delivery / f"taiji-agentv1.0-kylin-build-src-{commit}.tar.gz"
            archive.write_bytes(alternate_gzip)
            digest = hashlib.sha256(alternate_gzip).hexdigest()
            (delivery / "SHA256SUMS.txt").write_text(
                f"{digest}  {archive.name}\n",
                encoding="utf-8",
            )

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
            self.assertIn("源码包归档与当前 Git HEAD 一致", result.stdout)

            tampered_payload = gzip.compress(
                tar_payload + b"not part of git archive\n",
                compresslevel=1,
                mtime=0,
            )
            archive.write_bytes(tampered_payload)
            tampered_digest = hashlib.sha256(tampered_payload).hexdigest()
            (delivery / "SHA256SUMS.txt").write_text(
                f"{tampered_digest}  {archive.name}\n",
                encoding="utf-8",
            )
            rejected = subprocess.run(
                ["bash", str(script)],
                cwd=delivery,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("源码包归档内容与当前 Git HEAD 不一致", rejected.stderr)

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
        desktop_access_py = read_text(
            "hermes-local-lab/sources/hermes-webui/api/desktop_access.py"
        )

        self.assertIn("TAIJI_DESKTOP_ONLY", main_js)
        self.assertIn("TAIJI_DESKTOP_ACCESS_TOKEN", main_js)
        self.assertIn("taiji_desktop_token", main_js)
        self.assertIn("webContents.session.cookies.set", main_js)
        self.assertIn("httpOnly: true", main_js)
        self.assertIn('sameSite: "strict"', main_js)
        self.assertNotIn('searchParams.set("taiji_desktop_token"', main_js)
        self.assertIn('appendDesktopLog(desktopLog, "loading desktop workspace")', main_js)
        self.assertNotIn("loading ${target.toString()}", main_js)

        self.assertIn("desktop_access_required as _desktop_access_required", server_py)
        self.assertIn("enforce_desktop_access as _enforce_desktop_access", server_py)
        self.assertIn("def desktop_access_required", desktop_access_py)
        self.assertIn("def request_has_desktop_access", desktop_access_py)
        self.assertIn("def enforce_desktop_access", desktop_access_py)
        self.assertIn("请从桌面应用启动太极 Agent", desktop_access_py)
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
        self.assertNotIn("TAIJI_LICENSE_PRIVATE_KEY", build)
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

    def test_packaged_runtime_renames_both_legacy_transport_modules(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        start = build.index("rename_internal_agent_modules() {")
        end = build.index("\n}\n\nrewrite_product_text_tokens()", start) + len("\n}")
        rename_function = build[start:end]

        with tempfile.TemporaryDirectory(prefix="taiji-transport-rename.") as temp_dir:
            agent_runtime = Path(temp_dir)
            transports = agent_runtime / "agent/transports"
            transports.mkdir(parents=True)
            for name in ("mcp_server", "profile_env"):
                (transports / f"hermes_tools_{name}.py").write_text(name, encoding="utf-8")

            result = subprocess.run(
                ["bash", "-c", f"set -euo pipefail\n{rename_function}\nrename_internal_agent_modules"],
                env={**os.environ, "AGENT_RUNTIME": str(agent_runtime)},
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("mcp_server", "profile_env"):
                self.assertFalse((transports / f"hermes_tools_{name}.py").exists())
                self.assertEqual(
                    (transports / f"taiji_tools_{name}.py").read_text(encoding="utf-8"),
                    name,
                )

    def test_packaged_support_scripts_are_rewritten_before_privacy_scan(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        install_sync = build.index(
            'install -m 0644 "$LAB_DIR/scripts/sync-packaged-config.py" '
            '"$INSTALL_ROOT/scripts/sync-packaged-config.py"'
        )
        rewrite_scripts = build.index(
            'rewrite_product_text_tokens "$INSTALL_ROOT/scripts"',
            install_sync,
        )
        privacy_scan = build.index("scan_package_tree", rewrite_scripts)

        self.assertLess(install_sync, rewrite_scripts)
        self.assertLess(rewrite_scripts, privacy_scan)

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
        self.assertNotIn("xargs -0 -r grep", build)
        self.assertNotIn('strings "$OUT_DEB" | grep', build)
        self.assertNotIn("grep -q .", build)
        self.assertIn("Cannot inspect package key file", build)
        self.assertIn("Cannot inspect package text file", build)
        self.assertIn("Cannot inspect DEB archive metadata marker", build)

    def test_security_scans_fail_closed_for_first_hit_and_large_batches(self):
        private_scan = build_function_source("scan_private_key_material", "scan_product_privacy")
        privacy_scan = build_function_source("scan_product_privacy", "scan_package_tree")

        def run_scan(function_source: str, root: Path, call: str, tmp_dir: Path):
            script = [
                "set -euo pipefail",
                'fail() { printf \'%s\\n\' "$*" >&2; exit 42; }',
            ]
            script.extend([function_source, call])
            env = {
                **os.environ,
                "INSTALL_ROOT": str(root),
                "AGENT_RUNTIME": str(root / "runtime/agent"),
                "BUILD_ROOT": str(root / "build"),
                "PKG_ROOT": str(root),
                "TMPDIR": str(tmp_dir),
            }
            return subprocess.run(
                ["bash", "-c", "\n".join(script)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        with tempfile.TemporaryDirectory(prefix="taiji-security-scan-") as temp_dir:
            temp_root = Path(temp_dir)
            install_root = temp_root / "install"
            scan_tmp = temp_root / "tmp"
            install_root.mkdir()
            scan_tmp.mkdir()
            (install_root / "0000-public.pem").write_text(
                "-----BEGIN PRIVATE KEY-----\n", encoding="utf-8"
            )
            for index in range(3000):
                (install_root / f"{index + 1000:04d}-public.pem").write_text(
                    "public certificate\n", encoding="utf-8"
                )
            private_hit = run_scan(private_scan, install_root, "scan_private_key_material", scan_tmp)
            self.assertNotEqual(private_hit.returncode, 0)
            self.assertIn("private key", private_hit.stderr.lower())

            (install_root / "0000-public.pem").write_text("public certificate\n", encoding="utf-8")
            (install_root / ".env").write_text("TOKEN=fixture\n", encoding="utf-8")
            secret_hit = run_scan(private_scan, install_root, "scan_private_key_material", scan_tmp)
            self.assertNotEqual(secret_hit.returncode, 0)
            self.assertIn("secret-shaped", secret_hit.stderr.lower())

            (install_root / ".env").unlink()
            (install_root / "0000-text.txt").write_text("hermes legacy marker\n", encoding="utf-8")
            for index in range(3000):
                (install_root / f"{index + 1000:04d}-text.txt").write_text(
                    "taiji product text\n", encoding="utf-8"
                )
            privacy_hit = run_scan(privacy_scan, install_root, "scan_product_privacy", scan_tmp)
            self.assertNotEqual(privacy_hit.returncode, 0)
            self.assertIn("legacy product", privacy_hit.stderr.lower())

    def test_security_scans_fail_closed_on_reader_errors(self):
        private_scan = build_function_source("scan_private_key_material", "scan_product_privacy")
        privacy_scan = build_function_source("scan_product_privacy", "scan_package_tree")

        def run_scan(function_source: str, root: Path, call: str, tmp_dir: Path):
            script = "\n".join(
                [
                    "set -euo pipefail",
                    'fail() { printf \'%s\\n\' "$*" >&2; exit 42; }',
                    "grep() { return 2; }",
                    function_source,
                    call,
                ]
            )
            return subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "INSTALL_ROOT": str(root),
                    "AGENT_RUNTIME": str(root / "runtime/agent"),
                    "BUILD_ROOT": str(root / "build"),
                    "PKG_ROOT": str(root),
                    "TMPDIR": str(tmp_dir),
                },
                text=True,
                capture_output=True,
                check=False,
            )

        with tempfile.TemporaryDirectory(prefix="taiji-security-scan-errors-") as temp_dir:
            temp_root = Path(temp_dir)
            install_root = temp_root / "install"
            scan_tmp = temp_root / "tmp"
            install_root.mkdir()
            scan_tmp.mkdir()
            (install_root / "public.pem").write_text("public certificate\n", encoding="utf-8")
            private_error = run_scan(private_scan, install_root, "scan_private_key_material", scan_tmp)
            self.assertNotEqual(private_error.returncode, 0)
            self.assertIn("cannot inspect package key", private_error.stderr.lower())

            (install_root / "public.pem").unlink()
            (install_root / "text.txt").write_text("taiji product text\n", encoding="utf-8")
            privacy_error = run_scan(privacy_scan, install_root, "scan_product_privacy", scan_tmp)
            self.assertNotEqual(privacy_error.returncode, 0)
            self.assertIn("cannot inspect package text", privacy_error.stderr.lower())

    def test_write_launch_manifest_is_nounset_safe_and_uses_fixed_install_root(self):
        manifest_function = build_function_source("write_launch_manifest", "if [ \"$(uname -s)\"")
        with tempfile.TemporaryDirectory(prefix="taiji-launch-manifest-shell-") as temp_dir:
            temp_root = Path(temp_dir)
            install_root = temp_root / "staging/opt/taiji-agent"
            manifest_path = temp_root / "resources/taiji-release-manifest.json"
            install_root.mkdir(parents=True)
            manifest_path.parent.mkdir(parents=True)
            script = "\n".join(
                [
                    "set -euo pipefail",
                    'fail() { printf \'%s\\n\' "$*" >&2; exit 42; }',
                    manifest_function,
                    "write_launch_manifest",
                ]
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "INSTALL_ROOT": str(install_root),
                    "LAUNCH_MANIFEST_PATH": str(manifest_path),
                    "TAIJI_PACKAGE_ARCHITECTURE": "amd64",
                    "VERSION": "1.0.0",
                    "SOURCE_COMMIT": "0123456789abcdef0123456789abcdef01234567",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(manifest_path.stat().st_mode & 0o777, 0o644)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["installRoot"], "/opt/taiji-agent")
            self.assertEqual(manifest["arch"], "amd64")
            self.assertEqual(manifest["version"], "1.0.0")

    def test_electron_runtime_gate_distinguishes_ldd_failures_from_missing_libraries(self):
        electron_gate = build_function_source("verify_linux_electron_runtime", "validate_packaged_config_template")

        def run_gate(mode: str):
            script = "\n".join(
                [
                    "set -euo pipefail",
                    'fail() { printf \'%s\\n\' "$*" >&2; exit 42; }',
                    "file() { printf '%s\\n' 'ELF 64-bit LSB pie executable, x86-64'; }",
                    "ldd() {",
                    "  case \"$MODE\" in",
                    "    ok) printf '%s\\n' 'linux-vdso.so.1 => [kernel]'; return 0 ;;",
                    "    missing) printf '%s\\n' 'libfixture.so => not found'; return 0 ;;",
                    "    error) printf '%s\\n' 'ldd fixture failed'; return 7 ;;",
                    "  esac",
                    "}",
                    electron_gate,
                    "verify_linux_electron_runtime",
                ]
            )
            return subprocess.run(
                ["bash", "-c", script],
                env={**os.environ, "ELECTRON_BIN": "/bin/sh", "MODE": mode},
                text=True,
                capture_output=True,
                check=False,
            )

        healthy = run_gate("ok")
        self.assertEqual(healthy.returncode, 0, healthy.stderr)
        missing = run_gate("missing")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("missing shared libraries", missing.stderr.lower())
        failed = run_gate("error")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("cannot inspect electron", failed.stderr.lower())

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

    def test_delivery_install_script_uses_native_managed_upgrade_and_allowlisted_legacy_migration(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn("taiji-silent-deploy.sh", install)
        self.assertIn("TAIJI_OPERATION", install)
        self.assertIn("TAIJI_PREVIOUS_VERSION", install)
        self.assertIn("TAIJI_PREVIOUS_MANIFEST", install)
        self.assertNotIn("apt-get", install)
        self.assertNotIn("ONLINE_OK", install)
        self.assertNotIn("taiji-agent-webui.service", install)
        self.assertNotIn("taiji-agent-gateway.service", install)

    def test_builder_preserves_noninteractive_apt_environment_across_sudo(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn(
            "sudo env DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get update",
            builder,
        )
        self.assertIn(
            "sudo env DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get install -y",
            builder,
        )

    def test_builder_installs_declared_deb_runtime_dependencies_for_ldd_audit(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        deb_builder = read_text("packaging/linux/deb/build-deb.sh")
        policy = json.loads(read_text("packaging/linux/compatibility-policy.json"))
        install_body = builder[
            builder.index("install_build_dependencies() {") : builder.index("source_lab_dir() {")
        ]

        self.assertEqual(
            policy["debian"]["depends"],
            ["ca-certificates", "libc6 (>= 2.31)"],
        )
        self.assertIn(
            'POLICY_HELPER="$REPO_ROOT/packaging/linux/compatibility_policy.py"',
            deb_builder,
        )
        self.assertIn(
            'eval "$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-shell)"',
            deb_builder,
        )
        self.assertIn("Depends: $TAIJI_DEBIAN_DEPENDS", deb_builder)
        self.assertNotIn("runtime-depends.txt", deb_builder)
        self.assertNotIn("render-depends", deb_builder)
        self.assertNotIn('DEB_DEPENDS="$(awk ', deb_builder)
        self.assertNotRegex(
            deb_builder, re.compile(r'^DEB_DEPENDS="libc6,', re.MULTILINE)
        )

        for package in ("dpkg-dev", "python3", "rsync"):
            with self.subTest(package=package):
                self.assertIn(package, install_body)

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
        self.assertIn("check-clean-worktree.sh", preflight)
        self.assertIn("--mode formal", preflight)
        self.assertIn('--repo-root "$REPO_ROOT"', preflight)
        self.assertIn('--source-root "$SOURCE_TREE_ROOT"', preflight)
        self.assertIn("taiji-agentv1.0-kylin-build-src-*.tar.gz", preflight)
        self.assertIn("SHA256SUMS.txt", preflight)
        self.assertIn("生成的安装包", preflight)
        self.assertIn("canonical compatibility policy", preflight)
        self.assertIn("verify_marker_and_manifest", preflight)
        self.assertIn("verify_deb_payload", preflight)
        self.assertIn("verify_package_output_allowlist", preflight)
        self.assertNotIn("Packages.gz", preflight)
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

        for script in (builder,):
            self.assertIn("write_failure_diagnostic", script)
            self.assertIn("failure_next_steps", script)
            self.assertIn("write_environment_snapshot", script)
            self.assertIn("失败诊断-", script)
            self.assertIn("CURRENT_STAGE", script)
            self.assertIn("require_admin_capability", script)
            self.assertIn("sudo -v", script)
            self.assertIn("sudo -n true", script)

        self.assertIn("require_file", install)
        self.assertIn("TAIJI_RECEIPT_PATH", install)
        self.assertIn("安装回执", install)

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

        self.assertIn("MANIFEST_FILE", builder)
        self.assertIn("taiji-package-manifest.json", builder)
        self.assertIn("collect_artifacts", builder)
        self.assertIn("compatibility_policy_id", builder)
        self.assertIn("compatibility_policy_sha256", builder)
        self.assertIn("elf_abi_audit_sha256", builder)
        self.assertIn("CANDIDATE_DEB_FIXED", builder)
        self.assertIn("TAIJI_ALLOW_UV_LOCK_REFRESH", builder)
        self.assertIn('uv_lock_mode="${TAIJI_UV_LOCK_MODE:-auto}"', builder)
        self.assertIn('run_setup_local "$uv_lock_mode"', builder)
        self.assertIn('TAIJI_UV_LOCK_MODE="$uv_lock_mode" ./scripts/setup-local.sh', builder)
        self.assertIn("Python 依赖 lock 漂移", builder)
        self.assertNotIn("TAIJI_UV_LOCK_MODE=strict ./scripts/setup-local.sh", builder)
        self.assertNotIn("\n  uv lock\n", builder)
        self.assertIn('printf \'%s  %s\\n\' "$deb_sha" "$deb_name"', builder)
        self.assertNotIn("write_release_manifest", builder)
        self.assertNotIn("packages_gz_sha256", builder)
        self.assertNotIn("dpkg-scanpackages", builder)

    def test_install_script_requires_explicit_headless_rehearsal_mode(self):
        install = read_text("taijiagent 打包交付/02_目标终端_安装并验证.sh")

        self.assertIn('ADMISSION_MODE="${TAIJI_ADMISSION_MODE:-certification}"', install)
        self.assertIn("TAIJI_CERTIFICATION_CHALLENGE", install)
        self.assertIn("TAIJI_RECEIPT_PATH", install)
        self.assertNotIn("TAIJI_ALLOW_HEADLESS_REHEARSAL", install)
        self.assertNotIn("apt-get", install)
        self.assertNotIn("ONLINE_OK", install)

        main = install[install.index("main() {") :]
        self.assertLess(main.index('require_file "$SILENT_DEPLOY"'), main.index("select_deb"))
        self.assertLess(main.index("select_deb"), main.index("build_args"))

    def test_offline_builder_uses_ascii_tmp_build_root_and_repairs_source_permissions(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn('BUILD_ROOT="${TAIJI_BUILD_ROOT:-}"', builder)
        self.assertNotIn('DEFAULT_BUILD_ROOT="/tmp/', builder)
        self.assertIn("build_root_candidates()", builder)
        self.assertIn("select_build_root()", builder)
        self.assertIn("configure_build_tmp()", builder)
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

    def test_offline_builder_does_not_hardcode_tmp_as_default_build_root(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertNotIn('DEFAULT_BUILD_ROOT="/tmp/taiji-agent-build-', builder)
        self.assertIn('XDG_CACHE_HOME', builder)
        self.assertIn('home_cache="$HOME/.cache"', builder)
        self.assertIn('/var/tmp/taiji-agent-build-', builder)
        self.assertIn('TAIJI_BUILD_ROOT', builder)

    def test_offline_builder_checks_exec_and_shared_library_mapping_before_unpack(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn("probe_build_root()", builder)
        self.assertIn('cc "$probe_dir/probe.c" -o "$probe_dir/probe-exec"', builder)
        self.assertIn('"$probe_dir/probe-exec"', builder)
        self.assertIn('cc -shared -fPIC "$probe_dir/probe.c" -o "$probe_dir/probe.so"', builder)
        self.assertIn('ctypes.CDLL(sys.argv[1])', builder)
        main = builder[builder.index("main() {") :]
        self.assertLess(main.index("install_build_dependencies"), main.index("select_build_root"))
        self.assertLess(main.index("select_build_root"), main.index("prepare_source_release"))

    def test_offline_builder_exports_tmpdir_tmp_temp_under_selected_root(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")

        self.assertIn('BUILD_TMP_DIR="$BUILD_ROOT/tmp"', builder)
        self.assertIn('TOOL_ROOT="$BUILD_ROOT/.build-tools"', builder)
        self.assertIn('NODE_ROOT="$TOOL_ROOT/node"', builder)
        self.assertIn('export TMPDIR="$BUILD_TMP_DIR" TMP="$BUILD_TMP_DIR" TEMP="$BUILD_TMP_DIR"', builder)
        self.assertLess(builder.index("configure_build_tmp"), builder.index("prepare_source_release"))

    def test_offline_builder_honors_explicit_root_and_fails_closed_when_probe_fails(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        self.assertIn('if [ -n "${TAIJI_BUILD_ROOT:-}" ]; then', builder)
        self.assertIn('显式 TAIJI_BUILD_ROOT 探针失败', builder)
        self.assertIn('probe_build_root "$candidate"', builder)

        harness = "\n".join(
            [
                "set -u",
                'BUILD_ROOT=""',
                'BUILD_TMP_DIR=""',
                'TOOL_ROOT=""',
                'NODE_ROOT=""',
                'BUILD_ROOT_PROBE_RESULTS=""',
                'warn() { :; }',
                'info() { :; }',
                'fail() { printf "FAIL:%s\\n" "$*"; exit 23; }',
                'create_owned_build_root() { :; }',
                'configure_build_tmp() { BUILD_TMP_DIR="$BUILD_ROOT/tmp"; TOOL_ROOT="$BUILD_ROOT/.build-tools"; NODE_ROOT="$TOOL_ROOT/node"; printf "selected=%s TMPDIR=%s TMP=%s TEMP=%s\\n" "$BUILD_ROOT" "$BUILD_TMP_DIR" "$BUILD_TMP_DIR" "$BUILD_TMP_DIR"; }',
                'validate_candidate_build_root() { return 0; }',
                'probe_build_root() { printf "probe:%s\\n" "$1" >> "$PROBE_LOG"; case "$1" in *second*) return 0;; *) return 1;; esac; }',
                re.search(r"(?ms)^build_root_candidates\(\) \{.*?^\}", builder).group(0),
                re.search(r"(?ms)^select_build_root\(\) \{.*?^\}", builder).group(0),
                'select_build_root',
            ]
        )
        with tempfile.TemporaryDirectory(prefix="taiji-build-root-contract-") as temp_dir:
            root = Path(temp_dir)
            probe_log = root / "probe.log"
            env = os.environ.copy()
            env.update(
                {
                    "TAIJI_BUILD_ROOT": "",
                    "XDG_CACHE_HOME": str(root / "first"),
                    "HOME": str(root / "second-home"),
                    "PROBE_LOG": str(probe_log),
                }
            )
            auto = subprocess.run(
                ["bash", "-c", harness],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(auto.returncode, 0, auto.stderr)
            selected_root = root / "second-home" / ".cache" / f"taiji-agent-build-{os.getuid()}"
            self.assertIn(str(selected_root), auto.stdout)
            self.assertIn("TMPDIR=", auto.stdout)
            self.assertEqual(probe_log.read_text(encoding="utf-8").count("probe:"), 2)

            explicit_env = env | {
                "TAIJI_BUILD_ROOT": str(root / "explicit" / f"taiji-agent-build-{os.getuid()}"),
                "PROBE_LOG": str(root / "explicit-probe.log"),
            }
            explicit = subprocess.run(
                ["bash", "-c", harness],
                text=True,
                capture_output=True,
                check=False,
                env=explicit_env,
            )
            self.assertEqual(explicit.returncode, 23, explicit.stdout)
            self.assertIn("FAIL:", explicit.stdout)
            self.assertEqual((root / "explicit-probe.log").read_text(encoding="utf-8").count("probe:"), 1)

    def test_offline_builder_candidate_order_uses_xdg_cache_home_home_cache_then_var_tmp(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        candidates = builder[builder.index("build_root_candidates() {") : builder.index("validate_candidate_build_root() {")]
        self.assertLess(candidates.index("XDG_CACHE_HOME"), candidates.index('home_cache="$HOME/.cache"'))
        self.assertLess(candidates.index('home_cache="$HOME/.cache"'), candidates.index("/var/tmp/taiji-agent-build-"))

    def test_offline_builder_records_findmnt_and_probe_results_in_failure_diagnostic(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        self.assertIn('findmnt -T "$candidate"', builder)
        self.assertIn("BUILD_ROOT_PROBE_RESULTS", builder)
        self.assertIn("findmnt", builder)

    def test_offline_builder_moves_node_tool_root_under_selected_build_root(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        self.assertIn('TOOL_ROOT="$BUILD_ROOT/.build-tools"', builder)
        self.assertNotIn('TOOL_ROOT="$SCRIPT_DIR/.构建工具"', builder)

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
        self.assertLess(
            reset.index("require_owned_build_root"),
            reset.index("restore_owned_build_root_directory_writes"),
        )
        self.assertLess(
            reset.index("restore_owned_build_root_directory_writes"),
            reset.index('rm -rf -- "$BUILD_ROOT"'),
        )

        cleanup = builder[
            builder.index("cleanup_temporary_build_root() {") :
            builder.index("apt_source_summary() {")
        ]
        self.assertLess(cleanup.index("require_owned_build_root"), cleanup.index('rm -rf -- "$BUILD_ROOT"'))
        self.assertLess(
            cleanup.index("require_owned_build_root"),
            cleanup.index("restore_owned_build_root_directory_writes"),
        )
        self.assertLess(
            cleanup.index("restore_owned_build_root_directory_writes"),
            cleanup.index('rm -rf -- "$BUILD_ROOT"'),
        )

    def test_offline_builder_restores_directory_writes_before_retry_cleanup(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        helper = builder[
            builder.index("restore_owned_build_root_directory_writes() {") :
            builder.index("reset_build_root() {")
        ]

        with tempfile.TemporaryDirectory(prefix="taiji-build-retry-test.", dir="/tmp") as temp_dir:
            build_root = Path(temp_dir) / "taiji-agent-build-test"
            readonly_dir = build_root / "payload" / "builtin-templates"
            readonly_dir.mkdir(parents=True)
            (build_root / ".taiji-build-root-owner").write_text(
                "taiji-agent-offline-builder-v1\n",
                encoding="utf-8",
            )
            (readonly_dir / "template.json").write_text("{}\n", encoding="utf-8")
            (build_root / ".taiji-build-root-owner").chmod(0o600)
            readonly_dir.chmod(0o555)
            readonly_dir.parent.chmod(0o555)
            build_root.chmod(0o700)

            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    "set -e\n"
                    "fail() { printf '%s\\n' \"$*\" >&2; exit 1; }\n"
                    f"{helper}\n"
                    "restore_owned_build_root_directory_writes\n"
                    "rm -rf \"$BUILD_ROOT\"\n",
                ],
                env={**os.environ, "BUILD_ROOT": str(build_root)},
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(build_root.exists())

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
        self.assertIn('node "$DESKTOP_JS_STAGER"', desktop_stage)
        self.assertIn('--source "$APP_DIR/src"', desktop_stage)
        self.assertIn('--destination "$DESKTOP_RUNTIME/src"', desktop_stage)
        self.assertIn('--entry main.js', desktop_stage)
        self.assertIn('--entry preload.js', desktop_stage)
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

    def test_docx_engine_lock_uses_patched_fast_uri(self):
        lock = json.loads(
            read_text("hermes-local-lab/sources/docx-engine-v2/package-lock.json")
        )
        version = tuple(
            int(part)
            for part in lock["packages"]["node_modules/fast-uri"]["version"].split(".")
        )

        self.assertGreaterEqual(version, (3, 1, 4))
        self.assertLess(version, (4, 0, 0))

    def test_offline_builder_blocks_high_severity_docx_dependencies(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        start = builder.index('info "准备 DOCX Engine V2 生产依赖并执行源码测试"')
        end = builder.index('info "构建 DEB 安装包"', start)
        docx_build = builder[start:end]

        install = "npm_ci_with_network_fallback --omit=dev"
        audit = "npm_audit_fail_closed"
        self.assertIn(audit, docx_build)
        self.assertLess(docx_build.index(install), docx_build.index(audit))
        self.assertLess(docx_build.index(audit), docx_build.index("npm test"))

    def test_offline_builder_does_not_send_audit_to_install_only_npm_mirror(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        marker = "npm_audit_fail_closed() {"
        self.assertIn(marker, builder)
        start = builder.index(marker)
        end = builder.index("\n}\n\nrun_setup_local()", start) + len("\n}")
        audit_function = builder[start:end]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            capture = temp_root / "npm-call.txt"
            npm = fake_bin / "npm"
            npm.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >> "$TAIJI_TEST_CAPTURE"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            npm.chmod(0o755)
            harness = temp_root / "audit-registry.sh"
            harness.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "info() { :; }\n"
                "warn() { :; }\n"
                "fail() { printf '%s\\n' \"$*\" >&2; exit 91; }\n"
                f"{audit_function}\n"
                f'export PATH="{fake_bin}:$PATH"\n'
                'export NPM_CONFIG_REGISTRY="https://registry.npmmirror.com"\n'
                f'export TAIJI_TEST_CAPTURE="{capture}"\n'
                "unset TAIJI_NPM_AUDIT_REGISTRY\n"
                "npm_audit_fail_closed\n"
                'export TAIJI_NPM_AUDIT_REGISTRY="https://audit.example.invalid/npm"\n'
                "npm_audit_fail_closed\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["bash", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            invocations = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                invocations,
                [
                    "audit --omit=dev --audit-level=high "
                    "--registry=https://registry.npmjs.org",
                    "audit --omit=dev --audit-level=high "
                    "--registry=https://audit.example.invalid/npm",
                ],
            )

    def test_offline_builder_rejects_credentialed_npm_audit_registry(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        marker = "npm_audit_fail_closed() {"
        self.assertIn(marker, builder)
        start = builder.index(marker)
        end = builder.index("\n}\n\nrun_setup_local()", start) + len("\n}")
        audit_function = builder[start:end]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            python_capture = temp_root / "python-call.txt"
            python = fake_bin / "python3"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "{\n"
                '  printf "argv=%s\\n" "$*"\n'
                '  printf "audit_env=%s\\n" "${TAIJI_NPM_AUDIT_REGISTRY:-}"\n'
                '} > "$TAIJI_TEST_PYTHON_CAPTURE"\n'
                f'exec "{sys.executable}" "$@"\n',
                encoding="utf-8",
            )
            python.chmod(0o755)
            npm = fake_bin / "npm"
            npm.write_text(
                "#!/usr/bin/env bash\n"
                'printf "npm must not run\\n" >&2\n'
                "exit 0\n",
                encoding="utf-8",
            )
            npm.chmod(0o755)
            harness = temp_root / "audit-registry.sh"
            harness.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "info() { printf '%s\\n' \"$*\"; }\n"
                "warn() { :; }\n"
                "fail() { printf '%s\\n' \"$*\" >&2; exit 91; }\n"
                f"{audit_function}\n"
                f'export PATH="{fake_bin}:$PATH"\n'
                f'export TAIJI_TEST_PYTHON_CAPTURE="{python_capture}"\n'
                'export TAIJI_NPM_AUDIT_REGISTRY="https://token@example.invalid/npm"\n'
                "npm_audit_fail_closed\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["bash", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 91, result.stdout + result.stderr)
            self.assertIn("不得包含凭据", result.stderr)
            combined_output = result.stdout + result.stderr
            self.assertNotIn("token@example.invalid", combined_output)
            self.assertNotIn("npm must not run", result.stderr)
            self.assertNotIn(
                "token@example.invalid",
                python_capture.read_text(encoding="utf-8"),
            )

    def test_offline_builder_fails_closed_when_npm_audit_fails(self):
        builder = read_text("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        marker = "npm_audit_fail_closed() {"
        self.assertIn(marker, builder)
        start = builder.index(marker)
        end = builder.index("\n}\n\nrun_setup_local()", start) + len("\n}")
        audit_function = builder[start:end]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            after_marker = temp_root / "after-audit.txt"
            npm = fake_bin / "npm"
            npm.write_text(
                "#!/usr/bin/env bash\n"
                "exit 7\n",
                encoding="utf-8",
            )
            npm.chmod(0o755)
            harness = temp_root / "audit-failure.sh"
            harness.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "info() { :; }\n"
                "warn() { :; }\n"
                "fail() { printf '%s\\n' \"$*\" >&2; exit 91; }\n"
                f"{audit_function}\n"
                f'export PATH="{fake_bin}:$PATH"\n'
                "unset TAIJI_NPM_AUDIT_REGISTRY\n"
                "npm_audit_fail_closed\n"
                f'printf "unexpected\\n" > "{after_marker}"\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                ["bash", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 91, result.stdout + result.stderr)
            self.assertIn("npm audit 不可用", result.stderr)
            self.assertFalse(after_marker.exists())

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
        self.assertIn("TAIJI_NPM_AUDIT_REGISTRY", builder)
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

        self.assertNotIn("prepare_legacy_replacement", install)
        self.assertNotIn("clean_reinstall_legacy_package", install)
        self.assertNotIn("dpkg --purge", install)
        self.assertIn("taiji-silent-deploy.sh", install)
        self.assertIn("deployment-admission", read_text("packaging/linux/deployment_receipt.py"))

    def test_diagnose_entrypoints_are_packaged_and_delivery_script_exists(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        launcher = read_text("packaging/linux/bin/taiji-agent-diagnose")
        support_entrypoint = read_text("packaging/linux/bin/taiji-agent-support")
        diagnose = read_text("hermes-local-lab/scripts/taiji-agent-diagnose")
        delivery = read_text("taijiagent 打包交付/03_目标终端_导出诊断报告.sh")

        self.assertIn("taiji-agent-diagnose", build)
        self.assertIn("taiji-agent-support", build)
        self.assertIn("support_bundle.py", build)
        self.assertIn("scripts/taiji-agent-diagnose", launcher)
        self.assertIn("TAIJI_AGENT_USE_USER_DIRS", launcher)
        self.assertIn("--security", diagnose)
        self.assertIn("--allowlist", diagnose)
        self.assertIn("support_bundle.py", support_entrypoint)
        self.assertNotIn("/api/model-config", diagnose)
        self.assertNotIn("TAIJI_DESKTOP_ACCESS_TOKEN", diagnose)
        self.assertNotIn("pgrep -af", diagnose)
        self.assertNotIn("tail -120", diagnose)
        self.assertIn("诊断报告", delivery)
        self.assertIn("taiji-agent-support", delivery)

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
