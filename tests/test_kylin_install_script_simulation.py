import hashlib
import gzip
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = ROOT / "taijiagent 打包交付" / "02_目标终端_安装并验证.sh"


def write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class KylinInstallScriptSimulationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name) / "taijiagent 打包交付"
        self.tmp_path.mkdir()
        self.fake_bin = self.tmp_path / "bin"
        self.fake_bin.mkdir()
        self.fake_state = self.tmp_path / "state"
        self.fake_state.mkdir()
        self.fake_root_tmp = Path(self.tmp.name) / "root-tmp"
        self.fake_root_tmp.mkdir()
        self.fake_home = self.tmp_path / "home"
        self.fake_home.mkdir()
        self.fake_log = self.tmp_path / "fake.log"
        self.import_script = self.tmp_path / "install_import.sh"
        self._write_import_script()
        self._write_fake_commands()
        self._write_current_deb()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_import_script(self) -> None:
        source = INSTALL_SCRIPT.read_text(encoding="utf-8")
        source = source.replace('exec > >(tee -a "$LOG_FILE") 2>&1\n', "")
        verifier = str(self.fake_bin / "taiji-native-verify")
        source = source.replace(
            "[ -x /opt/taiji-agent/bin/taiji-native-verify ]",
            f'[ -x "{verifier}" ]',
        )
        source = source.replace(
            "\n  /opt/taiji-agent/bin/taiji-native-verify\n",
            f'\n  "{verifier}"\n',
        )
        source = source.replace(
            "TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify",
            f'TAIJI_VERIFY_DESKTOP_SMOKE=1 "{verifier}"',
        )
        source = re.sub(r'\nmain "\$@"\s*\Z', "\n", source)
        self.import_script.write_text(source, encoding="utf-8")

    def _write_current_deb(self) -> None:
        output_dir = self.tmp_path / "生成的安装包"
        output_dir.mkdir(exist_ok=True)
        repo_dir = self.tmp_path / "离线依赖"
        repo_dir.mkdir(exist_ok=True)
        packages = repo_dir / "Packages"
        packages.write_bytes(b"fake packages\n")
        packages_gz = repo_dir / "Packages.gz"
        packages_gz.write_bytes(gzip.compress(packages.read_bytes()))
        (repo_dir / "dependency_1.0_amd64.deb").write_bytes(b"fake dependency\n")
        packages_sha = hashlib.sha256(packages.read_bytes()).hexdigest()
        packages_gz_sha = hashlib.sha256(packages_gz.read_bytes()).hexdigest()
        deb = output_dir / "taiji-agent_0.1.0_amd64.deb"
        checksum = output_dir / "taiji-agent_0.1.0_amd64.deb.sha256"
        manifest = output_dir / "taiji-package-manifest.json"
        deb.write_bytes(b"fake deb\n")
        sha = hashlib.sha256(deb.read_bytes()).hexdigest()
        checksum.write_text(f"{sha}  {deb.name}\n", encoding="utf-8")
        manifest.write_text(
            textwrap.dedent(
                f"""
                {{
                  "schema_version": 1,
                  "version": "0.1.0",
                  "source_archive": "taiji-agentv1.0-kylin-build-src-test.tar.gz",
                  "source_sha256": "{'0' * 64}",
                  "deb": "{deb.name}",
                  "deb_sha256": "{sha}",
                  "checksum": "{checksum.name}",
                  "packages_sha256": "{packages_sha}",
                  "packages_gz_sha256": "{packages_gz_sha}",
                  "target_matrix": ["Debian-like x86_64/amd64 desktop Linux"],
                  "support_boundary": {{
                    "supported": ["Debian-like x86_64/amd64 desktop Linux"],
                    "unsupported": ["RPM-only Linux terminals"]
                  }}
                }}
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        (output_dir / ".build-success").write_text(
            "\n".join(
                [
                    f"deb={deb.name}",
                    f"checksum={checksum.name}",
                    f"deb_sha256={sha}",
                    f"manifest={manifest.name}",
                    f"packages_sha256={packages_sha}",
                    f"packages_gz_sha256={packages_gz_sha}",
                    "version=0.1.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_fake_commands(self) -> None:
        write_executable(
            self.fake_bin / "getent",
            r'''
            #!/usr/bin/env bash
            set -euo pipefail
            if [ "${1:-}" = "passwd" ]; then
              printf 'taiji:x:%s:%s::%s:/bin/bash\n' "$(id -u)" "$(id -g)" "$HOME"
              exit 0
            fi
            exit 1
            ''',
        )
        write_executable(
            self.fake_bin / "sudo",
            r'''
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'sudo %s\n' "$*" >> "$FAKE_LOG"
            if [ "${1:-}" = "env" ]; then
              shift
              while [ "$#" -gt 0 ] && [[ "${1:-}" == *=* ]]; do
                shift
              done
            fi
            cmd="${1:-}"
            shift || true
            case "$cmd" in
              rm)
                /bin/rm "$@"
                ;;
              chmod)
                /bin/chmod "$@"
                ;;
              chown)
                :
                ;;
              mktemp)
                if [ "$*" = "-d /var/tmp/taiji-agent-install.XXXXXX" ]; then
                  staging="$(/usr/bin/mktemp -d "$FAKE_ROOT_TMP/taiji-agent-install.XXXXXX")"
                  printf '%s\n' "$staging" > "$FAKE_STATE/root_staging_path"
                  printf '%s\n' "$staging"
                else
                  /usr/bin/mktemp "$@"
                fi
                ;;
              install)
                destination="${@: -1}"
                /usr/bin/install "$@"
                if [ "${FAKE_TAMPER_STAGED_PACKAGES:-0}" = "1" ] && [[ "$destination" = */repo/Packages.gz ]]; then
                  printf 'tampered staged packages gzip\n' > "$destination"
                fi
                ;;
              systemctl)
                op="${1:-}"
                svc="${2:-}"
                case "$op" in
                  stop) touch "$FAKE_STATE/stopped_$svc" ;;
                  start) rm -f "$FAKE_STATE/stopped_$svc" ;;
                  disable) touch "$FAKE_STATE/disabled_$svc" ;;
                  reset-failed) : ;;
                esac
                ;;
              kill)
                touch "$FAKE_STATE/killed_${1#-}"
                :
                ;;
              apt-get)
                case "${1:-}" in
                  purge)
                    if [ "${FAKE_APT_PURGE_FAIL:-0}" = "1" ]; then
                      exit 1
                    fi
                    touch "$FAKE_STATE/purged"
                    ;;
                  install) touch "$FAKE_STATE/installed" ;;
                esac
                ;;
              dpkg)
                if [ "${FAKE_DPKG_PERSIST:-0}" = "1" ]; then
                  exit 1
                fi
                touch "$FAKE_STATE/purged"
                ;;
              *)
                "$cmd" "$@"
                ;;
            esac
            ''',
        )
        write_executable(
            self.fake_bin / "systemctl",
            r'''
            #!/usr/bin/env bash
            set -euo pipefail
            svc="${2:-}"
            case "${1:-}" in
              is-active)
                [ ! -f "$FAKE_STATE/stopped_$svc" ]
                ;;
              is-enabled)
                [ ! -f "$FAKE_STATE/disabled_$svc" ]
                ;;
              list-unit-files|status)
                exit 0
                ;;
              *)
                exit 0
                ;;
            esac
            ''',
        )
        write_executable(
            self.fake_bin / "dpkg-query",
            r'''
            #!/usr/bin/env bash
            set -euo pipefail
            [ ! -f "$FAKE_STATE/purged" ] || exit 1
            case "$*" in
              *'${db:Status-Abbrev}'*) printf 'ii ' ;;
              *'${Version}'*) printf '0.1.0-1kylin9' ;;
            esac
            ''',
        )
        write_executable(
            self.fake_bin / "apt-mark",
            r'''
            #!/usr/bin/env bash
            exit 0
            ''',
        )
        write_executable(
            self.fake_bin / "pgrep",
            r'''
            #!/usr/bin/env bash
            if [ "${FAKE_PGREP_MODE:-none}" = "legacy" ]; then
              printf '9999\n'
              exit 0
            fi
            exit 1
            ''',
        )
        write_executable(
            self.fake_bin / "lsof",
            r'''
            #!/usr/bin/env bash
            if [ "${FAKE_LSOF_MODE:-none}" = "non_taiji" ]; then
              printf '43210\n'
              exit 0
            fi
            exit 1
            ''',
        )
        write_executable(
            self.fake_bin / "ps",
            r'''
            #!/usr/bin/env bash
            if [ "$*" = "-p 43210 -o args=" ]; then
              printf '/usr/bin/other-app --port 8787\n'
              exit 0
            fi
            if [ "$*" = "-p 9999 -o args=" ]; then
              printf '/opt/taiji-agent/src/hermes-webui/server.py\n'
              exit 0
            fi
            exit 1
            ''',
        )
        write_executable(
            self.fake_bin / "taiji-native-verify",
            r'''
            #!/usr/bin/env bash
            printf 'verify desktop_smoke=%s\n' "${TAIJI_VERIFY_DESKTOP_SMOKE:-0}" >> "$FAKE_LOG"
            ''',
        )
        write_executable(
            self.fake_bin / "taiji",
            r'''
            #!/usr/bin/env bash
            [ "${1:-}" = "--help" ]
            ''',
        )
        write_executable(
            self.fake_bin / "taiji-agent",
            r'''
            #!/usr/bin/env bash
            exit 0
            ''',
        )

    def run_install_package(
        self,
        *,
        apt_purge_fails: bool = False,
        dpkg_persists: bool = False,
        lsof_mode: str = "none",
        pgrep_mode: str = "none",
        online_ok: bool = False,
        tamper_staged_packages: bool = False,
        xdg_config_home: Path | None = None,
    ) -> subprocess.CompletedProcess:
        harness = self.tmp_path / "run.sh"
        harness.write_text(
            textwrap.dedent(
                f"""
                #!/usr/bin/env bash
                set -Eeuo pipefail
                export PATH="{self.fake_bin}:$PATH"
                export FAKE_STATE="{self.fake_state}"
                export FAKE_LOG="{self.fake_log}"
                export FAKE_ROOT_TMP="{self.fake_root_tmp}"
                export HOME="{self.fake_home}"
                export XDG_CONFIG_HOME="{xdg_config_home or ''}"
                export FAKE_APT_PURGE_FAIL="{1 if apt_purge_fails else 0}"
                export FAKE_DPKG_PERSIST="{1 if dpkg_persists else 0}"
                export FAKE_LSOF_MODE="{lsof_mode}"
                export FAKE_PGREP_MODE="{pgrep_mode}"
                export FAKE_TAMPER_STAGED_PACKAGES="{1 if tamper_staged_packages else 0}"
                export ONLINE_OK="{1 if online_ok else 0}"
                source "{self.import_script}"
                path_exists() {{
                  case "$1" in
                    /opt/taiji-agent|\\
                    /etc/default/taiji-agent|\\
                    /lib/systemd/system/taiji-agent-webui.service|\\
                    /lib/systemd/system/taiji-agent-gateway.service|\\
                    /usr/bin/taiji|\\
                    /usr/bin/taiji-agent|\\
                    /usr/share/applications/taiji-agent.desktop)
                      return 0
                      ;;
                  esac
                  [ -e "$1" ] || [ -L "$1" ]
                }}
                launcher_owned_by_taiji() {{
                  case "$1" in
                    /usr/bin/taiji|/usr/bin/taiji-agent|/usr/local/bin/taiji)
                      return 0
                      ;;
                    *)
                      return 1
                      ;;
                  esac
                }}
                install_package
                if [ -n "${{OFFLINE_APT_REPO_SOURCE:-}}" ]; then
                  [ -f "$OFFLINE_APT_REPO_SOURCE/Packages" ] && cp "$OFFLINE_APT_REPO_SOURCE/Packages" "$FAKE_STATE/offline_Packages"
                  [ -f "$OFFLINE_APT_REPO_SOURCE/Packages.gz" ] && touch "$FAKE_STATE/offline_Packages_gz"
                fi
                if [ -n "${{OFFLINE_APT_SOURCE_FILE:-}}" ]; then
                  cp "$OFFLINE_APT_SOURCE_FILE" "$FAKE_STATE/offline_source.list"
                fi
                """
            ).lstrip(),
            encoding="utf-8",
        )
        harness.chmod(0o755)
        return subprocess.run(
            ["bash", str(harness)],
            cwd=self.tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

    def fake_log_text(self) -> str:
        return self.fake_log.read_text(encoding="utf-8") if self.fake_log.exists() else ""

    def run_main_for_verification(
        self, *, allow_headless_rehearsal: bool
    ) -> subprocess.CompletedProcess:
        harness = self.tmp_path / "run-verification.sh"
        harness.write_text(
            textwrap.dedent(
                f"""
                #!/usr/bin/env bash
                set -Eeuo pipefail
                export PATH="{self.fake_bin}:$PATH"
                export FAKE_STATE="{self.fake_state}"
                export FAKE_LOG="{self.fake_log}"
                export HOME="{self.fake_home}"
                export DISPLAY=""
                export WAYLAND_DISPLAY=""
                export TAIJI_ALLOW_HEADLESS_REHEARSAL="{1 if allow_headless_rehearsal else 0}"
                source "{self.import_script}"
                preflight() {{ :; }}
                install_package() {{ :; }}
                main
                """
            ).lstrip(),
            encoding="utf-8",
        )
        harness.chmod(0o755)
        return subprocess.run(
            ["bash", str(harness)],
            cwd=self.tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_main_with_install_sentinel(
        self, *, allow_headless_rehearsal: bool
    ) -> subprocess.CompletedProcess:
        harness = self.tmp_path / "run-main-order.sh"
        harness.write_text(
            textwrap.dedent(
                f"""
                #!/usr/bin/env bash
                set -Eeuo pipefail
                export PATH="{self.fake_bin}:$PATH"
                export FAKE_STATE="{self.fake_state}"
                export FAKE_LOG="{self.fake_log}"
                export HOME="{self.fake_home}"
                export DISPLAY=""
                export WAYLAND_DISPLAY=""
                export TAIJI_ALLOW_HEADLESS_REHEARSAL="{1 if allow_headless_rehearsal else 0}"
                source "{self.import_script}"
                uname() {{
                  case "${{1:-}}" in
                    -s) printf 'Linux\n' ;;
                    -m) printf 'x86_64\n' ;;
                    *) command uname "$@" ;;
                  esac
                }}
                have() {{ return 0; }}
                require_admin_capability() {{ :; }}
                install_package() {{ touch "$FAKE_STATE/install_called"; }}
                main
                """
            ).lstrip(),
            encoding="utf-8",
        )
        harness.chmod(0o755)
        return subprocess.run(
            ["bash", str(harness)],
            cwd=self.tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_cross_machine_absolute_deb_checksum_path_is_tolerated(self):
        deb = self.tmp_path / "生成的安装包" / "taiji-agent_0.1.0_amd64.deb"
        checksum = self.tmp_path / "生成的安装包" / "taiji-agent_0.1.0_amd64.deb.sha256"
        sha = hashlib.sha256(deb.read_bytes()).hexdigest()
        checksum.write_text(
            f"{sha}  /home/user2/桌面/taijiagent 打包交付/构建工作区/taiji-agentv1.0/packages/麒麟操作系统安装包/{deb.name}\n",
            encoding="utf-8",
        )

        result = self.run_install_package()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("安装包 SHA256 校验通过", result.stdout + result.stderr)

    def test_root_owned_staging_precedes_purge_and_is_cleaned_on_exit(self):
        result = self.run_install_package()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        log = self.fake_log_text()
        staging_path_file = self.fake_state / "root_staging_path"
        self.assertTrue(staging_path_file.is_file(), log)
        staging = staging_path_file.read_text(encoding="utf-8").strip()
        self.assertIn("sudo mktemp -d /var/tmp/taiji-agent-install.XXXXXX", log)
        self.assertIn(
            f"sudo install -m 0644 {self.tmp_path / '离线依赖' / 'dependency_1.0_amd64.deb'} {staging}/repo/dependency_1.0_amd64.deb",
            log,
        )
        self.assertLess(
            log.index("sudo mktemp -d /var/tmp/taiji-agent-install.XXXXXX"),
            log.index("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent"),
        )
        self.assertIn(f"Dir::Etc::sourcelist={staging}/taiji-agent-offline.list", log)
        self.assertIn(f"install -y --reinstall --allow-downgrades --allow-change-held-packages {staging}/package/taiji-agent_0.1.0_amd64.deb", log)
        self.assertNotIn(f"install -y --reinstall --allow-downgrades --allow-change-held-packages {self.tmp_path / '生成的安装包'}", log)
        self.assertIn(f"sudo rm -rf -- {staging}", log)
        self.assertFalse(Path(staging).exists())

    def test_staged_repo_hash_mismatch_stops_before_purge(self):
        result = self.run_install_package(tamper_staged_packages=True)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("root staging 中 Packages.gz", result.stdout + result.stderr)
        log = self.fake_log_text()
        self.assertNotIn("apt-get purge -y taiji-agent", log)
        self.assertNotIn(" install -y --reinstall", log)

    def test_headless_verification_fails_by_default_without_success_claim(self):
        result = self.run_main_for_verification(allow_headless_rehearsal=False)

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("图形桌面会话", output)
        self.assertIn("TAIJI_ALLOW_HEADLESS_REHEARSAL=1", output)
        self.assertNotIn("[OK] 安装验证命令已执行完毕", output)

    def test_main_rejects_headless_before_install_package(self):
        result = self.run_main_with_install_sentinel(
            allow_headless_rehearsal=False
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("图形桌面会话", output)
        self.assertFalse((self.fake_state / "install_called").exists(), output)
        self.assertNotIn("[INFO] 阶段：安装太极 Agent", output)

    def test_explicit_headless_rehearsal_reports_partial_non_target_result(self):
        result = self.run_main_with_install_sentinel(
            allow_headless_rehearsal=True
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertTrue((self.fake_state / "install_called").is_file(), output)
        self.assertIn("仅离线安装演练，不是桌面 App/目标机验证", output)
        self.assertIn("真实模型对话和目标机验证：未验证", output)
        self.assertIn("taiji 命令可用", output)
        self.assertNotIn("[OK] 安装验证命令已执行完毕", output)

    def test_tampered_plain_packages_index_stops_before_install(self):
        (self.tmp_path / "离线依赖" / "Packages").write_bytes(b"tampered packages\n")

        result = self.run_install_package()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Packages 与 manifest 不匹配", result.stdout + result.stderr)
        self.assertFalse((self.fake_state / "installed").exists())

    def test_tampered_gzip_packages_index_stops_before_install(self):
        (self.tmp_path / "离线依赖" / "Packages.gz").write_bytes(b"tampered gzip\n")

        result = self.run_install_package()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Packages.gz 与 manifest 不匹配", result.stdout + result.stderr)
        self.assertFalse((self.fake_state / "installed").exists())

    def test_missing_plain_packages_hash_binding_stops_before_install(self):
        marker = self.tmp_path / "生成的安装包" / ".build-success"
        marker.write_text(
            "\n".join(
                line
                for line in marker.read_text(encoding="utf-8").splitlines()
                if not line.startswith("packages_sha256=")
            )
            + "\n",
            encoding="utf-8",
        )
        manifest = self.tmp_path / "生成的安装包" / "taiji-package-manifest.json"
        manifest.write_text(
            "\n".join(
                line
                for line in manifest.read_text(encoding="utf-8").splitlines()
                if '"packages_sha256"' not in line
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.run_install_package()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("缺少 packages_sha256", result.stdout + result.stderr)
        self.assertFalse((self.fake_state / "installed").exists())

    def test_clean_reinstall_removes_legacy_without_backup_before_installing(self):
        result = self.run_install_package()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertNotIn("sudo tar -C / -czf", log)
        self.assertLess(log.index("sudo systemctl stop taiji-agent-webui.service"), log.index("sudo apt-mark unhold taiji-agent"))
        self.assertLess(log.index("sudo apt-mark unhold taiji-agent"), log.index("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent"))
        self.assertLess(log.index("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent"), log.index("sudo rm -rf -- /opt/taiji-agent"))
        self.assertLess(log.index("sudo rm -rf -- /opt/taiji-agent"), log.index(" install -y --reinstall --allow-downgrades --allow-change-held-packages"))
        self.assertIn("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent", log)
        self.assertIn("sudo rm -rf -- /opt/taiji-agent", log)
        self.assertIn(" install -y --reinstall --allow-downgrades --allow-change-held-packages", log)

    def test_dpkg_purge_fallback_allows_install_when_apt_purge_fails(self):
        result = self.run_install_package(apt_purge_fails=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertIn("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent", log)
        self.assertIn("sudo dpkg --remove --force-remove-reinstreq taiji-agent", log)
        self.assertIn("sudo dpkg --purge --force-all taiji-agent", log)
        self.assertIn(" install -y --reinstall", log)

    def test_persistent_dpkg_state_stops_before_file_removal_and_install(self):
        result = self.run_install_package(apt_purge_fails=True, dpkg_persists=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertIn("sudo dpkg --purge --force-all taiji-agent", log)
        self.assertNotIn("sudo rm -rf -- /opt/taiji-agent", log)
        self.assertNotIn(" install -y --reinstall", log)

    def test_legacy_opt_process_is_killed_before_package_purge(self):
        result = self.run_install_package(pgrep_mode="legacy")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertIn("sudo kill 9999", log)
        self.assertLess(log.index("sudo kill 9999"), log.index("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent"))
        self.assertIn(" install -y --reinstall", log)

    def test_non_taiji_port_conflict_is_reported_without_blocking_install(self):
        result = self.run_install_package(lsof_mode="non_taiji")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertNotIn("sudo kill 43210", log)
        self.assertIn("apt-get purge -y taiji-agent", log)
        self.assertIn("sudo rm -rf -- /opt/taiji-agent", log)
        self.assertIn(" install -y --reinstall", log)

    def test_missing_offline_repo_blocks_default_install(self):
        shutil.rmtree(self.tmp_path / "离线依赖")

        result = self.run_install_package()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

        output = result.stdout + result.stderr
        self.assertIn("缺少离线依赖仓库", output)
        self.assertNotIn(" install -y --reinstall", self.fake_log_text())
        diagnostics = sorted((self.tmp_path / "构建日志").glob("失败诊断-*.txt"))
        self.assertTrue(diagnostics, output)
        diagnostic = diagnostics[-1].read_text(encoding="utf-8")
        self.assertIn("缺少离线依赖仓库", diagnostic)
        self.assertIn("ONLINE_OK=0", diagnostic)
        self.assertIn(
            "next=完全离线安装必须同时包含 离线依赖/Packages 与 Packages.gz",
            diagnostic,
        )

    def test_online_ok_allows_explicit_fallback_without_offline_repo(self):
        shutil.rmtree(self.tmp_path / "离线依赖")

        result = self.run_install_package(online_ok=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        output = result.stdout + result.stderr
        self.assertIn("ONLINE_OK=1", output)
        log = self.fake_log_text()
        staging_path_file = self.fake_state / "root_staging_path"
        self.assertTrue(staging_path_file.is_file(), log)
        staging = staging_path_file.read_text(encoding="utf-8").strip()
        self.assertIn(f" install -y --reinstall --allow-downgrades --allow-change-held-packages {staging}/package/taiji-agent_0.1.0_amd64.deb", log)
        self.assertNotIn(f" install -y --reinstall --allow-downgrades --allow-change-held-packages {self.tmp_path / '生成的安装包'}", log)

    def test_offline_repo_under_spaced_delivery_dir_uses_no_space_apt_source(self):
        repo = self.tmp_path / "离线依赖"

        result = self.run_install_package()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        source_file = self.fake_state / "offline_source.list"
        source = source_file.read_text(encoding="utf-8").strip()
        staging = (self.fake_state / "root_staging_path").read_text(encoding="utf-8").strip()
        self.assertEqual(source, f"deb [trusted=yes] file:{staging}/repo ./")
        apt_uri = source.split("file:", 1)[1].split(" ", 1)[0]
        self.assertNotIn(" ", apt_uri)
        self.assertNotIn(str(repo), source)
        self.assertEqual((self.fake_state / "offline_Packages").read_text(encoding="utf-8"), "fake packages\n")
        self.assertTrue((self.fake_state / "offline_Packages_gz").is_file())

    def test_adjacent_license_is_installed_to_user_config_with_owner_only_mode(self):
        (self.tmp_path / "license.jwt").write_text("signed-license-token\n", encoding="utf-8")

        result = self.run_install_package()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        target = self.fake_home / ".config" / "taiji-agent" / "licenses" / "active-license.jwt"
        self.assertEqual(target.read_text(encoding="utf-8"), "signed-license-token\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertIn("license.jwt", self.fake_log_text() + result.stdout + result.stderr)

    def test_adjacent_descriptive_license_is_installed_to_user_config(self):
        source = self.tmp_path / "taiji-license-测试客户-一号终端-aaaaaaaaaaaa-20260612-000000Z-20260712-000000Z.jwt"
        source.write_text("signed-license-token\n", encoding="utf-8")

        result = self.run_install_package()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        target = self.fake_home / ".config" / "taiji-agent" / "licenses" / "active-license.jwt"
        self.assertEqual(target.read_text(encoding="utf-8"), "signed-license-token\n")
        self.assertIn(source.name, self.fake_log_text() + result.stdout + result.stderr)

    def test_license_install_ignores_xdg_redirect_and_uses_account_home(self):
        (self.tmp_path / "license.jwt").write_text("signed-license-token\n", encoding="utf-8")
        redirected = self.tmp_path / "redirected-config"

        result = self.run_install_package(xdg_config_home=redirected)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        canonical = (
            self.fake_home
            / ".config"
            / "taiji-agent"
            / "licenses"
            / "active-license.jwt"
        )
        self.assertTrue(canonical.is_file())
        self.assertFalse((redirected / "taiji-agent/licenses/active-license.jwt").exists())

    def test_multiple_adjacent_descriptive_licenses_require_explicit_source(self):
        (self.tmp_path / "taiji-license-客户A-一号-aaaaaaaaaaaa-20260612-000000Z-20260712-000000Z.jwt").write_text(
            "a\n",
            encoding="utf-8",
        )
        (self.tmp_path / "taiji-license-客户B-二号-bbbbbbbbbbbb-20260612-000000Z-20260712-000000Z.jwt").write_text(
            "b\n",
            encoding="utf-8",
        )

        result = self.run_install_package()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("检测到多个候选授权文件", result.stdout + result.stderr)
        diagnostics = sorted((self.tmp_path / "构建日志").glob("失败诊断-*.txt"))
        self.assertTrue(diagnostics, result.stdout + result.stderr)
        diagnostic = diagnostics[-1].read_text(encoding="utf-8")
        self.assertIn("检测到多个候选授权文件", diagnostic)


if __name__ == "__main__":
    unittest.main()
