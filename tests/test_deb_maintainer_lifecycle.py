import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSTINST = ROOT / "packaging" / "linux" / "deb" / "postinst"
PRERM = ROOT / "packaging" / "linux" / "deb" / "prerm"
POSTRM = ROOT / "packaging" / "linux" / "deb" / "postrm"
INSTALLED_NATIVE_VERIFY_WRAPPER = ROOT / "packaging" / "linux" / "bin" / "taiji-native-verify"
RUNTIME_ENV = ROOT / "hermes-local-lab" / "scripts" / "runtime-env.sh"
NATIVE_VERIFY = ROOT / "hermes-local-lab" / "scripts" / "taiji-native-verify"


def write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class DebMaintainerLifecycleTest(unittest.TestCase):
    def run_postinst_sequence(
        self,
        *,
        native_verify_exits: tuple[int, ...],
        sandbox_chmod_exit: int = 0,
        retain_marker_after_first: bool = False,
    ) -> tuple[list[subprocess.CompletedProcess], str, int, bool]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            install_root = tmp_path / "opt" / "taiji-agent"
            state_root = tmp_path / "var" / "lib" / "taiji-agent"
            bin_dir = tmp_path / "bin"
            log_file = tmp_path / "postinst-verify.log"
            env_log = tmp_path / "verify-env.log"
            verify_count_file = tmp_path / "verify-count"
            retained_marker = state_root / "runtime-home" / "retained.marker"
            script = tmp_path / "postinst"
            bin_dir.mkdir()
            (install_root / "apps" / "taiji-desktop" / "node_modules" / "electron" / "dist").mkdir(
                parents=True
            )
            (install_root / "scripts").mkdir()
            (install_root / "bin").mkdir()

            for name in (
                "start-agent.sh",
                "start-webui.sh",
                "stop-all.sh",
                "runtime-env.sh",
                "taiji-agent-diagnose",
                "support_bundle.py",
                "sync-packaged-config.py",
            ):
                write_executable(install_root / "scripts" / name, "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                install_root / "scripts" / "taiji-native-verify",
                "#!/usr/bin/env bash\nexit 0\n",
            )
            exit_cases = "\n".join(
                f"  {index}) verify_exit={exit_code} ;;"
                for index, exit_code in enumerate(native_verify_exits, start=1)
            )
            write_executable(
                install_root / "bin" / "taiji-native-verify",
                f"""
                #!/usr/bin/env bash
                count=0
                [ ! -f "{verify_count_file}" ] || count="$(/bin/cat "{verify_count_file}")"
                count=$((count + 1))
                printf '%s\n' "$count" > "{verify_count_file}"
                printf 'user_dirs=%s\n' "${{TAIJI_AGENT_USE_USER_DIRS:-unset}}" > "{env_log}"
                printf 'desktop_smoke=%s\n' "${{TAIJI_VERIFY_DESKTOP_SMOKE:-unset}}" >> "{env_log}"
                printf 'runtime_home=%s\n' "${{TAIJI_RUNTIME_HOME:-unset}}" >> "{env_log}"
                printf 'workspace=%s\n' "${{TAIJI_WORKSPACE:-unset}}" >> "{env_log}"
                printf 'home=%s\n' "${{HOME:-unset}}" >> "{env_log}"
                printf 'mode=%s\n' "${{1:-unset}}" >> "{env_log}"
                verify_exit=99
                case "$count" in
                {exit_cases}
                  *) verify_exit=98 ;;
                esac
                exit "$verify_exit"
                """,
            )
            write_executable(tmp_path / "taiji-agent", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(tmp_path / "taiji", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(tmp_path / "taiji-agent-support", "#!/usr/bin/env bash\nexit 0\n")
            (install_root / "apps" / "taiji-desktop" / "node_modules" / "electron" / "dist" / "chrome-sandbox").write_text(
                "sandbox\n", encoding="utf-8"
            )

            source = POSTINST.read_text(encoding="utf-8")
            source = source.replace("/opt/taiji-agent", str(install_root))
            source = source.replace("/var/lib/taiji-agent", str(state_root))
            source = source.replace("/var/log/taiji-agent", str(tmp_path / "var" / "log" / "taiji-agent"))
            source = source.replace("/usr/bin/taiji-agent", str(tmp_path / "taiji-agent"))
            source = source.replace("/usr/bin/taiji", str(tmp_path / "taiji"))
            source = source.replace("/usr/bin/taiji-agent-support", str(tmp_path / "taiji-agent-support"))
            script.write_text(source, encoding="utf-8")
            script.chmod(0o755)

            write_executable(
                bin_dir / "chmod",
                f"""
                #!/usr/bin/env bash
                if [ "${{1:-}}" = "4755" ]; then
                  exit {sandbox_chmod_exit}
                fi
                exit 0
                """,
            )
            write_executable(bin_dir / "chown", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                bin_dir / "install",
                """
                #!/usr/bin/env bash
                args=()
                while [ "$#" -gt 0 ]; do
                  case "$1" in
                    -o|-g) shift 2 ;;
                    *) args+=("$1"); shift ;;
                  esac
                done
                exec /usr/bin/install "${args[@]}"
                """,
            )
            write_executable(
                bin_dir / "stat",
                """
                #!/usr/bin/env bash
                case "${2:-}" in
                  '%u:%g:%a') printf '0:0:4755\\n' ;;
                  *) exec /usr/bin/stat "$@" ;;
                esac
                """,
            )
            for name in ("update-desktop-database", "gtk-update-icon-cache"):
                write_executable(bin_dir / name, "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HOME"] = str(tmp_path / "customer-home")
            results = []
            for index in range(len(native_verify_exits)):
                results.append(
                    subprocess.run(
                        ["bash", str(script), "configure"],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=env,
                    )
                )
                if index == 0 and retain_marker_after_first:
                    retained_marker.write_text("keep\n", encoding="utf-8")

            env_text = env_log.read_text(encoding="utf-8") if env_log.exists() else ""
            env_text = env_text.replace(str(state_root), "/var/lib/taiji-agent")
            verify_count = (
                int(verify_count_file.read_text(encoding="utf-8"))
                if verify_count_file.exists()
                else 0
            )
            return results, env_text, verify_count, retained_marker.exists()

    def run_postinst(
        self,
        *,
        native_verify_exit: int = 0,
        sandbox_chmod_exit: int = 0,
    ) -> tuple[subprocess.CompletedProcess, str]:
        results, env_text, _, _ = self.run_postinst_sequence(
            native_verify_exits=(native_verify_exit,),
            sandbox_chmod_exit=sandbox_chmod_exit,
        )
        return results[0], env_text

    def test_postinst_returns_nonzero_when_native_verify_fails(self):
        result, _ = self.run_postinst(native_verify_exit=17)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_postinst_returns_nonzero_when_chrome_sandbox_hardening_fails(self):
        result, _ = self.run_postinst(sandbox_chmod_exit=19)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_postinst_delegates_system_only_verification_to_packaged_wrapper(self):
        result, env_text = self.run_postinst()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("user_dirs=unset", env_text)
        self.assertIn("desktop_smoke=unset", env_text)
        self.assertIn("runtime_home=unset", env_text)
        self.assertIn("workspace=unset", env_text)
        self.assertIn("home=unset", env_text)
        self.assertIn("mode=--system-only", env_text)

    def test_packaged_wrapper_system_only_mode_pins_system_paths_through_real_runtime_chain(self):
        with tempfile.TemporaryDirectory(prefix="taiji-system-verify-") as tmp:
            temp_root = Path(tmp).resolve()
            install_root = temp_root / "opt" / "taiji-agent"
            state_root = temp_root / "var" / "lib" / "taiji-agent"
            log_root = temp_root / "var" / "log" / "taiji-agent"
            wrapper = temp_root / "bin" / "taiji-native-verify"
            scripts_dir = install_root / "scripts"
            agent_dir = install_root / "runtime" / "agent"
            webui_dir = install_root / "runtime" / "web"
            web_static_dir = webui_dir / "static"
            python_path = agent_dir / "venv" / "bin" / "python"
            desktop_app = install_root / "apps" / "taiji-desktop"
            electron_path = desktop_app / "node_modules" / "electron" / "dist" / "electron"
            desktop_entry = temp_root / "usr" / "share" / "applications" / "taiji-agent.desktop"
            desktop_icon = temp_root / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps" / "taiji-agent.png"
            resource_icon = install_root / "resources" / "icons" / "taiji-agent.png"
            observed_env = temp_root / "observed-runtime-env.log"

            for directory in (
                wrapper.parent,
                scripts_dir,
                python_path.parent,
                web_static_dir,
                desktop_app / "src",
                electron_path.parent,
                desktop_entry.parent,
                desktop_icon.parent,
                resource_icon.parent,
            ):
                directory.mkdir(parents=True, exist_ok=True)

            wrapper_source = INSTALLED_NATIVE_VERIFY_WRAPPER.read_text(encoding="utf-8")
            wrapper_source = wrapper_source.replace("/opt/taiji-agent", str(install_root))
            wrapper_source = wrapper_source.replace("/var/lib/taiji-agent", str(state_root))
            wrapper_source = wrapper_source.replace("/var/log/taiji-agent", str(log_root))
            write_executable(wrapper, wrapper_source)

            runtime_source = RUNTIME_ENV.read_text(encoding="utf-8")
            runtime_source = runtime_source.replace("/opt/taiji-agent", str(install_root))
            runtime_source = runtime_source.replace("/var/lib/taiji-agent", str(state_root))
            runtime_source = runtime_source.replace("/var/log/taiji-agent", str(log_root))
            write_executable(scripts_dir / "runtime-env.sh", runtime_source)

            verifier_source = NATIVE_VERIFY.read_text(encoding="utf-8")
            verifier_source = verifier_source.replace("/opt/taiji-agent", str(install_root))
            verifier_source = verifier_source.replace(
                "/usr/share/applications/taiji-agent.desktop", str(desktop_entry)
            )
            verifier_source = verifier_source.replace(
                "/usr/share/icons/hicolor/512x512/apps/taiji-agent.png", str(desktop_icon)
            )
            write_executable(scripts_dir / "taiji-native-verify", verifier_source)

            for name in ("start-agent.sh", "start-webui.sh", "stop-all.sh"):
                write_executable(scripts_dir / name, "#!/bin/bash -p\nexit 0\n")
            write_executable(
                python_path,
                f"""
                #!/bin/bash -p
                {{
                  printf 'home=%s\n' "${{HOME:-unset}}"
                  printf 'user_dirs=%s\n' "${{TAIJI_AGENT_USE_USER_DIRS:-unset}}"
                  printf 'verify_mode=%s\n' "${{TAIJI_NATIVE_VERIFY_MODE:-unset}}"
                  printf 'sync_packaged_config=%s\n' "${{TAIJI_AGENT_SYNC_PACKAGED_CONFIG:-unset}}"
                  printf 'runtime_home=%s\n' "${{TAIJI_RUNTIME_HOME:-unset}}"
                  printf 'workspace=%s\n' "${{TAIJI_WORKSPACE:-unset}}"
                  printf 'config=%s\n' "${{TAIJI_AGENT_CONFIG_DIR:-unset}}"
                  printf 'data=%s\n' "${{TAIJI_AGENT_DATA_DIR:-unset}}"
                  printf 'state=%s\n' "${{TAIJI_AGENT_STATE_DIR:-unset}}"
                  printf 'log=%s\n' "${{TAIJI_AGENT_LOG_DIR:-unset}}"
                  printf 'tmp=%s\n' "${{TAIJI_AGENT_TMP_DIR:-unset}}"
                  printf 'account_home=%s\n' "${{TAIJI_ACCOUNT_HOME:-unset}}"
                  printf '%s\n' '---'
                }} >> "{observed_env}"
                exit 0
                """,
            )
            (agent_dir / "taiji_runtime").mkdir()
            (agent_dir / "taiji_runtime" / "main.py").write_text("# fixture\n", encoding="utf-8")
            (webui_dir / "server.py").write_text("# fixture\n", encoding="utf-8")
            (desktop_app / "package.json").write_text("{}\n", encoding="utf-8")
            (desktop_app / "src" / "main.js").write_text("// fixture\n", encoding="utf-8")

            elf_header = bytearray(64)
            elf_header[:16] = b"\x7fELF\x02\x01\x01" + (b"\x00" * 9)
            elf_header[16:20] = b"\x02\x00\x3e\x00"
            elf_header[20:24] = b"\x01\x00\x00\x00"
            electron_path.write_bytes(elf_header)
            electron_path.chmod(0o755)
            desktop_entry.write_text(
                "[Desktop Entry]\nExec=/usr/bin/taiji-agent\nIcon=taiji-agent\nTerminal=false\n"
                "StartupWMClass=taiji-agent\nX-GNOME-WMClass=taiji-agent\n",
                encoding="utf-8",
            )
            icon_bytes = (ROOT / "hermes-local-lab" / "sources" / "hermes-webui" / "static" / "favicon-512.png").read_bytes()
            desktop_icon.write_bytes(icon_bytes)
            resource_icon.write_bytes(icon_bytes)
            (web_static_dir / "favicon-512.png").write_bytes(icon_bytes)

            hostile_env = os.environ.copy()
            hostile_env.update(
                {
                    "HOME": "/tmp/evil-home",
                    "XDG_CONFIG_HOME": "/tmp/evil-config",
                    "XDG_DATA_HOME": "/tmp/evil-data",
                    "XDG_STATE_HOME": "/tmp/evil-state",
                    "TAIJI_AGENT_USE_USER_DIRS": "1",
                    "TAIJI_VERIFY_DESKTOP_SMOKE": "1",
                    "TAIJI_NATIVE_VERIFY_MODE": "interactive",
                    "TAIJI_RUNTIME_HOME": "/tmp/evil-runtime",
                    "TAIJI_WORKSPACE": "/tmp/evil-workspace",
                    "TAIJI_AGENT_CONFIG_DIR": "/tmp/evil-agent-config",
                    "TAIJI_AGENT_DATA_DIR": "/tmp/evil-agent-data",
                    "TAIJI_AGENT_STATE_DIR": "/tmp/evil-agent-state",
                    "TAIJI_AGENT_LOG_DIR": "/tmp/evil-agent-log",
                    "TAIJI_AGENT_TMP_DIR": "/tmp/evil-agent-tmp",
                    "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                    "TAIJI_TEST_OBSERVED_ENV": str(observed_env),
                }
            )

            result = subprocess.run(
                [str(wrapper), "--system-only"],
                text=True,
                capture_output=True,
                check=False,
                env=hostile_env,
            )

            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            observed = observed_env.read_text(encoding="utf-8")
            self.assertNotIn("/tmp/evil", observed)
            self.assertIn("home=/nonexistent", observed)
            self.assertIn("user_dirs=0", observed)
            self.assertIn("verify_mode=system-only", observed)
            self.assertIn("sync_packaged_config=0", observed)
            self.assertIn(f"runtime_home={state_root}/runtime-home", observed)
            self.assertIn(f"workspace={state_root}/workspace", observed)
            self.assertIn(f"config={state_root}/config", observed)
            self.assertIn(f"data={state_root}/data", observed)
            self.assertIn(f"state={state_root}/state", observed)
            self.assertIn(f"log={log_root}", observed)
            self.assertIn(f"tmp={state_root}/tmp", observed)
            self.assertIn("account_home=/nonexistent", observed)

            observed_env.unlink()
            exact_env = {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "C.UTF-8",
                "TAIJI_TEST_OBSERVED_ENV": str(observed_env),
            }
            exact_result = subprocess.run(
                [str(wrapper), "--system-only"],
                text=True,
                capture_output=True,
                check=False,
                env=exact_env,
            )

            exact_output = exact_result.stdout + exact_result.stderr
            self.assertEqual(exact_result.returncode, 0, exact_output)
            exact_observed = observed_env.read_text(encoding="utf-8")
            self.assertIn("home=/nonexistent", exact_observed)
            self.assertIn("account_home=/nonexistent", exact_observed)
            self.assertIn("user_dirs=0", exact_observed)
            self.assertIn("verify_mode=system-only", exact_observed)
            self.assertNotIn("unbound variable", exact_output)

    def test_postinst_configure_is_idempotent_in_the_same_system_state(self):
        results, _, verify_count, marker_exists = self.run_postinst_sequence(
            native_verify_exits=(0, 0),
            retain_marker_after_first=True,
        )

        self.assertEqual([result.returncode for result in results], [0, 0])
        self.assertEqual(verify_count, 2)
        self.assertTrue(marker_exists)

    def test_postinst_failed_configure_preserves_state_for_a_successful_retry(self):
        results, _, verify_count, marker_exists = self.run_postinst_sequence(
            native_verify_exits=(17, 0),
            retain_marker_after_first=True,
        )

        self.assertNotEqual(results[0].returncode, 0)
        self.assertEqual(results[1].returncode, 0, results[1].stdout + results[1].stderr)
        self.assertEqual(verify_count, 2)
        self.assertTrue(marker_exists)

    def run_prerm_dynamic(self, *, reuse_owned_pid_after_term: bool) -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            install_root = tmp_path / "opt" / "taiji-agent"
            proc_root = tmp_path / "proc"
            fake_bin = tmp_path / "bin"
            owned_executable = install_root / "bin" / "owned"
            unowned_executable = tmp_path / "usr" / "bin" / "unowned"
            kill_log = tmp_path / "kill.log"
            script = tmp_path / "prerm"
            harness = tmp_path / "run-prerm.sh"
            for path in (owned_executable, unowned_executable):
                path.parent.mkdir(parents=True, exist_ok=True)
                write_executable(path, "#!/usr/bin/env bash\nexit 0\n")
            fake_bin.mkdir()
            for pid, executable in ((101, owned_executable), (202, unowned_executable)):
                proc_dir = proc_root / str(pid)
                proc_dir.mkdir(parents=True)
                (proc_dir / "exe").symlink_to(executable)

            source = PRERM.read_text(encoding="utf-8")
            source = source.replace('INSTALL_ROOT="/opt/taiji-agent"', f'INSTALL_ROOT="{install_root}"')
            source = source.replace('PROC_ROOT="/proc"', f'PROC_ROOT="{proc_root}"')
            script.write_text(source, encoding="utf-8")
            script.chmod(0o755)
            write_executable(
                fake_bin / "readlink",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "-f" ]; then
                  shift
                  [ "${1:-}" != "--" ] || shift
                  exec python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
                fi
                exec /usr/bin/readlink "$@"
                """,
            )
            write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
            harness.write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env bash
                    set -euo pipefail
                    export PATH="{fake_bin}:$PATH"
                    export FAKE_KILL_LOG="{kill_log}"
                    export FAKE_PROC_ROOT="{proc_root}"
                    export FAKE_UNOWNED_EXE="{unowned_executable}"
                    export FAKE_REUSE_PID="{'101' if reuse_owned_pid_after_term else ''}"
                    kill() {{
                      local signal="${{1:-}}" pid="${{2:-}}"
                      printf '%s %s\n' "$signal" "$pid" >> "$FAKE_KILL_LOG"
                      if [ "$signal" = "-TERM" ] && [ "$pid" = "$FAKE_REUSE_PID" ]; then
                        rm -f -- "$FAKE_PROC_ROOT/$pid/exe"
                        ln -s -- "$FAKE_UNOWNED_EXE" "$FAKE_PROC_ROOT/$pid/exe"
                      fi
                      return 0
                    }}
                    source "{script}" remove
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            harness.chmod(0o755)
            result = subprocess.run(
                ["bash", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )
            return result, kill_log.read_text(encoding="utf-8") if kill_log.exists() else ""

    def test_prerm_uses_proc_executable_ownership_not_command_patterns(self):
        source = PRERM.read_text(encoding="utf-8")

        self.assertNotIn("pkill -f", source)
        self.assertNotIn("pgrep -f", source)
        self.assertIn("/proc", source)
        self.assertIn("/exe", source)
        self.assertRegex(source, r"readlink\s+-f")
        self.assertIn("kill -TERM", source)
        self.assertIn("kill -KILL", source)

    def test_prerm_revalidates_executable_before_sigkill(self):
        source = PRERM.read_text(encoding="utf-8")

        term_index = source.index("kill -TERM")
        kill_index = source.index("kill -KILL")
        self.assertLess(term_index, kill_index)
        self.assertGreater(source[:kill_index].count("/exe"), 1)

    def test_prerm_succeeds_when_no_owned_processes_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc_root = tmp_path / "proc"
            proc_root.mkdir()
            script = tmp_path / "prerm"
            source = PRERM.read_text(encoding="utf-8").replace(
                'PROC_ROOT="/proc"', f'PROC_ROOT="{proc_root}"'
            )
            script.write_text(source, encoding="utf-8")
            script.chmod(0o755)

            result = subprocess.run(
                ["bash", str(script), "remove"],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prerm_dynamically_signals_only_install_owned_executables(self):
        result, kill_log = self.run_prerm_dynamic(reuse_owned_pid_after_term=False)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("-TERM 101", kill_log)
        self.assertIn("-KILL 101", kill_log)
        self.assertNotIn("202", kill_log)

    def test_prerm_does_not_sigkill_a_reused_pid_with_a_new_identity(self):
        result, kill_log = self.run_prerm_dynamic(reuse_owned_pid_after_term=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("-TERM 101", kill_log)
        self.assertNotIn("-KILL 101", kill_log)
        self.assertNotIn("202", kill_log)

    def run_postrm_dynamic(self, scenario: str, action: str = "purge") -> tuple[subprocess.CompletedProcess, dict[str, bool]]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            var_lib = tmp_path / "var" / "lib" / "taiji-agent"
            var_log = tmp_path / "var" / "log" / "taiji-agent"
            install_root = tmp_path / "opt" / "taiji-agent"
            outside = tmp_path / "outside"
            script = tmp_path / "postrm"
            fake_bin.mkdir()
            outside.mkdir()
            nonroot_path = ""
            mount_path = ""

            if scenario in {"ordinary", "safe"}:
                for directory in (
                    var_lib,
                    var_log,
                    install_root / "runtime-home",
                    install_root / "workspace",
                    install_root / "logs",
                    install_root / "tmp",
                ):
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / "state.txt").write_text("state\n", encoding="utf-8")
            elif scenario == "symlink":
                var_lib.parent.mkdir(parents=True)
                var_lib.symlink_to(outside, target_is_directory=True)
            elif scenario == "nonroot":
                var_lib.mkdir(parents=True)
                nonroot_path = str(var_lib)
            elif scenario == "mountpoint":
                var_log.mkdir(parents=True)
                mount_path = str(var_log)
            elif scenario == "type-mismatch":
                install_root.mkdir(parents=True)
                (install_root / "runtime-home").write_text("not a directory\n", encoding="utf-8")
            elif scenario == "top-symlink":
                install_root.parent.mkdir(parents=True)
                install_root.symlink_to(outside, target_is_directory=True)
            elif scenario == "top-nonroot":
                install_root.mkdir(parents=True)
                nonroot_path = str(install_root)
            elif scenario == "top-mountpoint":
                install_root.mkdir(parents=True)
                mount_path = str(install_root)
            elif scenario == "top-type-mismatch":
                install_root.parent.mkdir(parents=True)
                install_root.write_text("not a directory\n", encoding="utf-8")
            else:
                self.fail(f"unknown postrm scenario: {scenario}")

            source = POSTRM.read_text(encoding="utf-8")
            source = source.replace("/var/lib/taiji-agent", str(var_lib))
            source = source.replace("/var/log/taiji-agent", str(var_log))
            source = source.replace("/opt/taiji-agent", str(install_root))
            source = source.replace("! -user root", f"! -user {os.getuid()}")
            source = source.replace('[ "$owner" != "0" ]', f'[ "$owner" != "{os.getuid()}" ]')
            script.write_text(source, encoding="utf-8")
            script.chmod(0o755)

            write_executable(
                fake_bin / "stat",
                f"""
                #!/usr/bin/env bash
                if [ "${{1:-}}" = "-c" ] && [ "${{2:-}}" = "%u" ]; then
                  path="${{@: -1}}"
                  if [ -n "${{FAKE_NONROOT_PATH:-}}" ] && [ "$path" = "$FAKE_NONROOT_PATH" ]; then
                    printf '{os.getuid() + 1}\\n'
                  else
                    printf '{os.getuid()}\\n'
                  fi
                  exit 0
                fi
                exit 2
                """,
            )
            write_executable(
                fake_bin / "mountpoint",
                """
                #!/usr/bin/env bash
                path="${@: -1}"
                [ -n "${FAKE_MOUNT_PATH:-}" ] && [ "$path" = "$FAKE_MOUNT_PATH" ]
                """,
            )
            for name in ("update-desktop-database", "gtk-update-icon-cache"):
                write_executable(fake_bin / name, "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["FAKE_NONROOT_PATH"] = nonroot_path
            env["FAKE_MOUNT_PATH"] = mount_path
            result = subprocess.run(
                ["bash", str(script), action],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            existence = {
                "var_lib": var_lib.exists() or var_lib.is_symlink(),
                "var_log": var_log.exists() or var_log.is_symlink(),
                "install_root": install_root.exists() or install_root.is_symlink(),
                "runtime_home": (install_root / "runtime-home").exists()
                if install_root.is_dir() and not install_root.is_symlink()
                else False,
            }
            return result, existence

    def test_postrm_purge_is_allowlisted_and_preserves_user_state(self):
        source = POSTRM.read_text(encoding="utf-8")

        self.assertNotIn("rm -rf /opt/taiji-agent", source)
        self.assertIn("/var/lib/taiji-agent", source)
        self.assertIn("/var/log/taiji-agent", source)
        self.assertIn("root", source)
        self.assertIn("-type l", source)
        self.assertIn("mountpoint", source)
        self.assertNotIn("XDG_", source)
        self.assertNotIn("$HOME", source)

    def test_postrm_dynamic_ordinary_remove_preserves_all_system_state(self):
        result, existence = self.run_postrm_dynamic("ordinary", action="remove")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(all(existence.values()), existence)

    def test_postrm_dynamic_purge_deletes_only_a_safe_root_owned_tree(self):
        result, existence = self.run_postrm_dynamic("safe")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(existence.values()), existence)

    def test_postrm_dynamic_purge_preserves_unsafe_allowlisted_entries(self):
        for scenario in ("symlink", "nonroot", "mountpoint", "type-mismatch"):
            with self.subTest(scenario=scenario):
                result, existence = self.run_postrm_dynamic(scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(
                    existence["var_lib"]
                    or existence["var_log"]
                    or existence["runtime_home"],
                    (scenario, existence, result.stdout, result.stderr),
                )
                self.assertIn("[WARN] preserving", result.stderr)

    def test_postrm_dynamic_install_root_is_subject_to_the_same_safety_gate(self):
        for scenario in ("top-symlink", "top-nonroot", "top-mountpoint", "top-type-mismatch"):
            with self.subTest(scenario=scenario):
                result, existence = self.run_postrm_dynamic(scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(existence["install_root"], (scenario, existence))
                self.assertIn("[WARN] preserving", result.stderr)


if __name__ == "__main__":
    unittest.main()
