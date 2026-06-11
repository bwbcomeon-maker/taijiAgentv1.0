import hashlib
import os
import re
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
        self.tmp_path = Path(self.tmp.name)
        self.fake_bin = self.tmp_path / "bin"
        self.fake_bin.mkdir()
        self.fake_state = self.tmp_path / "state"
        self.fake_state.mkdir()
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
        source = re.sub(r'\nmain "\$@"\s*\Z', "\n", source)
        self.import_script.write_text(source, encoding="utf-8")

    def _write_current_deb(self) -> None:
        output_dir = self.tmp_path / "生成的安装包"
        output_dir.mkdir(exist_ok=True)
        deb = output_dir / "taiji-agent_0.1.0_amd64.deb"
        checksum = output_dir / "taiji-agent_0.1.0_amd64.deb.sha256"
        deb.write_bytes(b"fake deb\n")
        sha = hashlib.sha256(deb.read_bytes()).hexdigest()
        checksum.write_text(f"{sha}  {deb.name}\n", encoding="utf-8")
        (output_dir / ".build-success").write_text(
            "\n".join(
                [
                    f"deb={deb.name}",
                    f"checksum={checksum.name}",
                    f"deb_sha256={sha}",
                    "version=0.1.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_fake_commands(self) -> None:
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

    def run_install_package(
        self,
        *,
        apt_purge_fails: bool = False,
        dpkg_persists: bool = False,
        lsof_mode: str = "none",
        pgrep_mode: str = "none",
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
                export HOME="{self.fake_home}"
                export FAKE_APT_PURGE_FAIL="{1 if apt_purge_fails else 0}"
                export FAKE_DPKG_PERSIST="{1 if dpkg_persists else 0}"
                export FAKE_LSOF_MODE="{lsof_mode}"
                export FAKE_PGREP_MODE="{pgrep_mode}"
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

    def test_clean_reinstall_removes_legacy_without_backup_before_installing(self):
        result = self.run_install_package()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertNotIn("sudo tar -C / -czf", log)
        self.assertLess(log.index("sudo systemctl stop taiji-agent-webui.service"), log.index("sudo apt-mark unhold taiji-agent"))
        self.assertLess(log.index("sudo apt-mark unhold taiji-agent"), log.index("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent"))
        self.assertLess(log.index("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent"), log.index("sudo rm -rf -- /opt/taiji-agent"))
        self.assertLess(log.index("sudo rm -rf -- /opt/taiji-agent"), log.index("sudo apt-get install -y --reinstall --allow-downgrades --allow-change-held-packages"))
        self.assertIn("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent", log)
        self.assertIn("sudo rm -rf -- /opt/taiji-agent", log)
        self.assertIn("sudo apt-get install -y --reinstall --allow-downgrades --allow-change-held-packages", log)

    def test_dpkg_purge_fallback_allows_install_when_apt_purge_fails(self):
        result = self.run_install_package(apt_purge_fails=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertIn("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent", log)
        self.assertIn("sudo dpkg --remove --force-remove-reinstreq taiji-agent", log)
        self.assertIn("sudo dpkg --purge --force-all taiji-agent", log)
        self.assertIn("sudo apt-get install -y --reinstall", log)

    def test_persistent_dpkg_state_stops_before_file_removal_and_install(self):
        result = self.run_install_package(apt_purge_fails=True, dpkg_persists=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertIn("sudo dpkg --purge --force-all taiji-agent", log)
        self.assertNotIn("sudo rm -rf -- /opt/taiji-agent", log)
        self.assertNotIn("sudo apt-get install -y --reinstall", log)

    def test_legacy_opt_process_is_killed_before_package_purge(self):
        result = self.run_install_package(pgrep_mode="legacy")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertIn("sudo kill 9999", log)
        self.assertLess(log.index("sudo kill 9999"), log.index("sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent"))
        self.assertIn("sudo apt-get install -y --reinstall", log)

    def test_non_taiji_port_conflict_is_reported_without_blocking_install(self):
        result = self.run_install_package(lsof_mode="non_taiji")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        log = self.fake_log_text()
        self.assertNotIn("sudo kill 43210", log)
        self.assertIn("apt-get purge -y taiji-agent", log)
        self.assertIn("sudo rm -rf -- /opt/taiji-agent", log)
        self.assertIn("sudo apt-get install -y --reinstall", log)

    def test_adjacent_license_is_installed_to_user_config_with_owner_only_mode(self):
        (self.tmp_path / "license.jwt").write_text("signed-license-token\n", encoding="utf-8")

        result = self.run_install_package()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        target = self.fake_home / ".config" / "taiji-agent" / "license.jwt"
        self.assertEqual(target.read_text(encoding="utf-8"), "signed-license-token\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertIn("license.jwt", self.fake_log_text() + result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
