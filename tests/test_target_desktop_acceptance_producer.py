import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "taijiagent 打包交付"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def shell_region(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


class TargetDesktopAcceptanceProducerTest(unittest.TestCase):
    def run_build_output_archive_harness(
        self,
        root: Path,
        *,
        fake_mv_mode: str,
        fake_install_mode: str = "pass",
    ) -> subprocess.CompletedProcess[str]:
        builder = read_text(DELIVERY / "00_制包机_生成离线交付包.sh")
        functions = "\n".join(
            (
                shell_region(
                    builder,
                    "rollback_previous_build_outputs() {",
                    "rollback_target_acceptance_tools() {",
                ),
                shell_region(
                    builder,
                    "rollback_target_acceptance_tools() {",
                    "cleanup_transient_delivery() {",
                ),
                shell_region(
                    builder,
                    "cleanup_transient_delivery() {",
                    "on_signal() {",
                ),
                shell_region(
                    builder,
                    "on_signal() {",
                    "trap cleanup_transient_delivery EXIT",
                ),
                shell_region(
                    builder,
                    "archive_previous_build_outputs() {",
                    "install_build_dependencies() {",
                ),
            )
        )
        harness = root / "build-output-archive-harness.sh"
        harness.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -Eeuo pipefail
                SCRIPT_DIR="$1"
                OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
                OUTPUT_ARCHIVE_DIR=""
                OUTPUT_BACKUP=""
                OUTPUT_REPLACEMENT_PENDING=0
                ACCEPTANCE_STAGING=""
                ACCEPTANCE_TARGET=""
                ACCEPTANCE_ARCHIVE_DIR=""
                ACCEPTANCE_BACKUP=""
                warn() {{ printf '[WARN] %s\\n' "$*" >&2; }}
                ok() {{ printf '[OK] %s\\n' "$*"; }}
                fail() {{ printf '[FAIL] %s\\n' "$*" >&2; exit 1; }}
                write_failure_diagnostic() {{ :; }}
                {functions}
                trap cleanup_transient_delivery EXIT
                trap 'on_signal 130 INT' INT
                trap 'on_signal 143 TERM' TERM
                trap 'on_signal 129 HUP' HUP
                archive_previous_build_outputs
                """
            ),
            encoding="utf-8",
        )
        harness.chmod(0o755)

        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        fake_mv = fake_bin / "mv"
        fake_mv.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                count=0
                [ ! -f "$TAIJI_TEST_MV_COUNT" ] || count="$(tr -d '\\r\\n' < "$TAIJI_TEST_MV_COUNT")"
                count=$((count + 1))
                printf '%s\\n' "$count" > "$TAIJI_TEST_MV_COUNT"
                if [ "$count" = 1 ]; then
                  /bin/mv "$@"
                  case "$TAIJI_TEST_MV_MODE" in
                    move-then-fail) exit 73 ;;
                    move-then-term) kill -TERM "$PPID"; exit 0 ;;
                  esac
                  exit 0
                fi
                exec /bin/mv "$@"
                """
            ),
            encoding="utf-8",
        )
        fake_mv.chmod(0o755)
        fake_stat = fake_bin / "stat"
        fake_stat.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                if [ "$1" = -c ] && [ "$2" = %h ]; then
                  python3 -c 'import os,sys; print(os.lstat(sys.argv[1]).st_nlink)' "$3"
                  exit 0
                fi
                if [ "$1" = -c ] && [ "$2" = %u ]; then
                  python3 -c 'import os,sys; print(os.lstat(sys.argv[1]).st_uid)' "$3"
                  exit 0
                fi
                exec /usr/bin/stat "$@"
                """
            ),
            encoding="utf-8",
        )
        fake_stat.chmod(0o755)
        fake_install = fake_bin / "install"
        fake_install.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                target=""
                for argument in "$@"; do
                  target="$argument"
                done
                if [ "$target" = "$TAIJI_TEST_OUTPUT_DIR" ]; then
                  /usr/bin/install "$@"
                  case "$TAIJI_TEST_INSTALL_MODE" in
                    create-then-fail) exit 74 ;;
                    create-then-term) kill -TERM "$PPID"; exit 0 ;;
                    create-unknown-then-fail)
                      printf 'unknown\n' > "$target/unknown.txt"
                      exit 74
                      ;;
                  esac
                  exit 0
                fi
                exec /usr/bin/install "$@"
                """
            ),
            encoding="utf-8",
        )
        fake_install.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "TAIJI_TEST_MV_COUNT": str(root / "mv-count"),
                "TAIJI_TEST_MV_MODE": fake_mv_mode,
                "TAIJI_TEST_INSTALL_MODE": fake_install_mode,
                "TAIJI_TEST_OUTPUT_DIR": str(root / "生成的安装包"),
            }
        )
        return subprocess.run(
            ["bash", str(harness), str(root)],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def run_acceptance_publication_harness(
        self,
        root: Path,
        *,
        mode: str,
        fake_mv_mode: str = "pass",
    ) -> subprocess.CompletedProcess[str]:
        builder = read_text(DELIVERY / "00_制包机_生成离线交付包.sh")
        functions = "\n".join(
            (
                shell_region(
                    builder,
                    "rollback_previous_build_outputs() {",
                    "rollback_target_acceptance_tools() {",
                ),
                shell_region(
                    builder,
                    "rollback_target_acceptance_tools() {",
                    "cleanup_transient_delivery() {",
                ),
                shell_region(
                    builder,
                    "cleanup_transient_delivery() {",
                    "on_signal() {",
                ),
                shell_region(
                    builder,
                    "on_signal() {",
                    "trap cleanup_transient_delivery EXIT",
                ),
                shell_region(
                    builder,
                    "archive_stale_acceptance_staging() {",
                    "publish_target_acceptance_tools() {",
                ),
                shell_region(
                    builder,
                    "publish_target_acceptance_tools() {",
                    "stage_target_acceptance_tools() {",
                ),
            )
        )
        harness = root / "acceptance-publication-harness.sh"
        harness.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -Eeuo pipefail
                SCRIPT_DIR="$1"
                OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
                MODE="$2"
                OUTPUT_ARCHIVE_DIR=""
                OUTPUT_BACKUP=""
                ACCEPTANCE_STAGING=""
                ACCEPTANCE_TARGET=""
                ACCEPTANCE_ARCHIVE_DIR=""
                ACCEPTANCE_BACKUP=""
                warn() {{ printf '[WARN] %s\\n' "$*" >&2; }}
                ok() {{ printf '[OK] %s\\n' "$*"; }}
                fail() {{ printf '[FAIL] %s\\n' "$*" >&2; exit 1; }}
                write_failure_diagnostic() {{ :; }}
                {functions}
                trap cleanup_transient_delivery EXIT
                trap 'on_signal 130 INT' INT
                trap 'on_signal 143 TERM' TERM
                trap 'on_signal 129 HUP' HUP
                archive_stale_acceptance_staging
                if [ "$MODE" = archive-only ]; then
                  exit 0
                fi
                ACCEPTANCE_STAGING="$SCRIPT_DIR/.验收工具.tmp-$$"
                mkdir -m 0755 -- "$ACCEPTANCE_STAGING"
                printf 'new\\n' > "$ACCEPTANCE_STAGING/new.txt"
                publish_target_acceptance_tools "$SCRIPT_DIR/验收工具" "$ACCEPTANCE_STAGING"
                """
            ),
            encoding="utf-8",
        )
        harness.chmod(0o755)

        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        fake_mv = fake_bin / "mv"
        fake_mv.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                count=0
                [ ! -f "$TAIJI_TEST_MV_COUNT" ] || count="$(tr -d '\\r\\n' < "$TAIJI_TEST_MV_COUNT")"
                count=$((count + 1))
                printf '%s\\n' "$count" > "$TAIJI_TEST_MV_COUNT"
                if [ "$count" = 2 ]; then
                  case "$TAIJI_TEST_MV_MODE" in
                    fail-second) exit 73 ;;
                    term-second) kill -TERM "$PPID"; exit 0 ;;
                  esac
                fi
                exec /bin/mv "$@"
                """
            ),
            encoding="utf-8",
        )
        fake_mv.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "TAIJI_TEST_MV_COUNT": str(root / "mv-count"),
                "TAIJI_TEST_MV_MODE": fake_mv_mode,
            }
        )
        return subprocess.run(
            ["bash", str(harness), str(root), mode],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_target_script_runs_only_installed_electron_and_emits_unsigned_environment_evidence(self):
        script = read_text(DELIVERY / "04_目标终端_桌面App验收并导出证据.sh")

        self.assertIn("TAIJI_TARGET_ACCEPTANCE_CHALLENGE", script)
        self.assertIn("/opt/taiji-agent/runtime/node/bin/node", script)
        self.assertIn("/opt/taiji-agent/runtime/agent/venv/bin/python", script)
        self.assertIn("/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron", script)
        self.assertIn("/usr/share/applications/taiji-agent.desktop", script)
        self.assertIn("run-installed-electron-acceptance.js", script)
        self.assertIn("assemble-target-evidence.py", script)
        self.assertIn("observe-single-deb-install.py", script)
        self.assertIn("certification-matrix.json", script)
        self.assertIn("TAIJI_CERTIFICATION_CATEGORY_ID", script)
        self.assertIn("environment-evidence.json", script)
        self.assertIn("validate-taiji-release-evidence.py", script)
        self.assertIn('/opt/taiji-agent/bin/taiji-native-verify', script)
        self.assertIn('TAIJI_AGENT_ROOT="/opt/taiji-agent"', script)
        self.assertIn('-u TAIJI_AGENT_AGENT_DIR', script)
        self.assertIn('-u TAIJI_AGENT_WEBUI_DIR', script)
        self.assertIn('-u TAIJI_AGENT_PYTHON', script)
        self.assertIn('-u TAIJI_WEBUI_PYTHON', script)
        self.assertIn('-u TAIJI_AGENT_RUNTIME_ENV', script)
        self.assertIn('-u PYTHONPATH', script)
        self.assertIn('-u NODE_OPTIONS', script)
        self.assertNotIn('TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify', script)
        self.assertNotIn("--pre-sign", script)
        self.assertNotIn("TAIJI_LEGACY_TARGET_BASELINE_MODE", script)
        self.assertIn("target-verification.json", script)
        self.assertIn("driver-result.json", script)
        self.assertIn("desktop-app.png", script)
        self.assertIn("taiji-support-bundle.json", script)
        self.assertNotIn("playwright", script.lower())
        self.assertNotIn("mobile", script.lower())
        self.assertNotIn("sign-taiji-release-evidence", script)
        self.assertNotIn("PRIVATE_KEY", script)

    def test_target_script_fails_closed_on_platform_identity_and_existing_output(self):
        script = read_text(DELIVERY / "04_目标终端_桌面App验收并导出证据.sh")

        self.assertIn('if [ "$EUID" -eq 0 ]', script)
        self.assertIn('uname -s', script)
        self.assertIn('x86_64|amd64', script)
        self.assertIn('kylin|uos|openkylin', script)
        self.assertIn("DISPLAY", script)
        self.assertIn("WAYLAND_DISPLAY", script)
        self.assertIn("dpkg-query", script)
        self.assertIn("electron_executable_sha256", script)
        self.assertIn("desktop_entry_sha256", script)
        self.assertIn("validate_install_observation", script)
        self.assertIn("sha256sum", script)
        self.assertIn("证据输出目录已存在，拒绝覆盖", script)

    def test_target_script_rejects_non_v3_manifest_before_platform_and_long_input_checks(self):
        script = read_text(DELIVERY / "04_目标终端_桌面App验收并导出证据.sh")
        main = script[script.index("main() {") :]

        self.assertIn("validate_manifest_schema_v3", script)
        self.assertLess(main.index("validate_manifest_schema_v3"), main.index("validate_platform"))
        self.assertLess(main.index("validate_manifest_schema_v3"), main.index("validate_inputs"))
        self.assertNotIn("manifest schema_version=2 or", script)

    def test_target_script_consumes_machine_observation_and_human_method_attestation(self):
        script = read_text(DELIVERY / "04_目标终端_桌面App验收并导出证据.sh")

        for required in (
            "TAIJI_SINGLE_DEB_CUSTOMER_DIR",
            "TAIJI_SINGLE_DEB_INSTALL_OBSERVATION",
            "TAIJI_SINGLE_DEB_METHOD_ATTESTATION",
            "TAIJI_SINGLE_DEB_GRAPHICAL_INSTALLER_EVIDENCE",
            "TAIJI_CERTIFICATION_CATEGORY_ID",
            "--install-observation",
            "--install-method-attestation",
            "--graphical-installer-evidence",
            "--matrix",
            "--category-id",
            "--environment-record",
            "/usr/bin/python3 -B",
        ):
            self.assertIn(required, script)
        for removed_post_hoc_flag in (
            "TAIJI_TARGET_INSTALL_METHOD",
            "TAIJI_TARGET_INSTALL_NETWORK",
            "TAIJI_TARGET_DPKG_STATUS_BEFORE",
            "TAIJI_TARGET_FIRST_LAUNCH",
        ):
            self.assertNotIn(removed_post_hoc_flag, script)
        main = script[script.index("main() {") :]
        self.assertLess(main.index("validate_install_observation"), main.index("run_desktop_acceptance"))
        self.assertIn('entry_count="$(find "$SINGLE_DEB_CUSTOMER_DIR"', script)
        self.assertIn('customer_sha256="$(sha256sum "$CUSTOMER_DEB"', script)
        self.assertIn('"$customer_sha256" = "$EXPECTED_DEB_SHA256"', script)

    def test_builder_stages_and_preflight_requires_the_acceptance_toolchain(self):
        builder = read_text(DELIVERY / "00_制包机_生成离线交付包.sh")
        preflight = read_text(DELIVERY / "01_制包机_发布预检.sh")
        validator = read_text(ROOT / "scripts/validate-taiji-release-evidence.py")
        gitignore = read_text(ROOT / ".gitignore")

        for filename in (
            "run-installed-electron-acceptance.js",
            "assemble-target-evidence.py",
            "observe-single-deb-install.py",
            "certification-matrix.json",
            "assemble-taiji-certification-set.py",
            "validate-taiji-release-evidence.py",
            "signing-public.pem",
        ):
            self.assertIn(filename, builder)
            self.assertIn(filename, preflight)
            self.assertIn(f"验收工具/{filename}", validator)
        self.assertIn("stage_target_acceptance_tools", builder)
        self.assertIn("04_目标终端_桌面App验收并导出证据.sh", validator)
        self.assertIn("04_目标终端_桌面App验收并导出证据.sh", gitignore)
        self.assertNotIn('[ -x "$script" ]', preflight)
        self.assertIn('[ -f "$script" ] && [ ! -L "$script" ]', preflight)
        self.assertIn(
            'root_acceptance_script="$SCRIPT_DIR/04_目标终端_桌面App验收并导出证据.sh"',
            preflight,
        )
        tool_list = preflight[
            preflight.index("local -a files=(") : preflight.index(")", preflight.index("local -a files=("))
        ]
        self.assertNotIn("04_目标终端_桌面App验收并导出证据.sh", tool_list)

    def test_current_single_deb_target_acceptance_does_not_require_legacy_apt_repository(self):
        script = read_text(DELIVERY / "04_目标终端_桌面App验收并导出证据.sh")

        self.assertNotIn('OFFLINE_REPO="$SCRIPT_DIR/离线依赖"', script)
        self.assertNotIn('"$OFFLINE_REPO/Packages"', script)
        self.assertNotIn('"$OFFLINE_REPO/Packages.gz"', script)

    def test_builder_refreshes_acceptance_tools_without_deleting_unknown_content(self):
        builder = read_text(DELIVERY / "00_制包机_生成离线交付包.sh")
        refresh_body = builder[
            builder.index("archive_stale_acceptance_staging() {") : builder.index(
                "cleanup_delivery_build_cache() {"
            )
        ]

        self.assertIn(".验收工具.tmp-", refresh_body)
        self.assertIn("验收工具目录含未知", refresh_body)
        self.assertIn("旧版备份", refresh_body)
        self.assertNotIn('rm -rf -- "$target"', refresh_body)

    def test_builder_restores_previous_acceptance_tools_when_second_move_fails(self):
        with tempfile.TemporaryDirectory(prefix="taiji-acceptance-rollback-") as temp_dir:
            root = Path(temp_dir)
            target = root / "验收工具"
            target.mkdir()
            (target / "old.txt").write_text("old\n", encoding="utf-8")

            result = self.run_acceptance_publication_harness(
                root,
                mode="publish",
                fake_mv_mode="fail-second",
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse((target / "new.txt").exists())
            self.assertFalse(any(root.glob(".验收工具.tmp-*")))
            self.assertFalse(any((root / "旧版备份").glob("*/验收工具")))

    def test_builder_restores_previous_acceptance_tools_on_term_during_publication(self):
        with tempfile.TemporaryDirectory(prefix="taiji-acceptance-signal-") as temp_dir:
            root = Path(temp_dir)
            target = root / "验收工具"
            target.mkdir()
            (target / "old.txt").write_text("old\n", encoding="utf-8")

            result = self.run_acceptance_publication_harness(
                root,
                mode="publish",
                fake_mv_mode="term-second",
            )

            self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
            self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse((target / "new.txt").exists())
            self.assertFalse(any(root.glob(".验收工具.tmp-*")))

    def test_builder_restores_complete_previous_output_directory_when_atomic_move_reports_failure(self):
        with tempfile.TemporaryDirectory(prefix="taiji-output-rollback-") as temp_dir:
            root = Path(temp_dir)
            output = root / "生成的安装包"
            output.mkdir()
            (output / "taiji-agent_0.1.0_amd64.deb").write_bytes(b"old-deb")
            (output / "taiji-agent_0.1.0_amd64.deb.sha256").write_text(
                "0" * 64 + "  taiji-agent_0.1.0_amd64.deb\n",
                encoding="utf-8",
            )

            result = self.run_build_output_archive_harness(
                root,
                fake_mv_mode="move-then-fail",
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                (output / "taiji-agent_0.1.0_amd64.deb").read_bytes(),
                b"old-deb",
            )
            self.assertTrue((output / "taiji-agent_0.1.0_amd64.deb.sha256").is_file())
            self.assertFalse(any((root / "旧版备份").glob("*/生成的安装包")))

    def test_builder_restores_complete_previous_output_directory_on_term_after_atomic_move(self):
        with tempfile.TemporaryDirectory(prefix="taiji-output-signal-") as temp_dir:
            root = Path(temp_dir)
            output = root / "生成的安装包"
            output.mkdir()
            (output / ".build-success").write_text("old-marker\n", encoding="utf-8")
            (output / "构建报告.txt").write_text("old-report\n", encoding="utf-8")

            result = self.run_build_output_archive_harness(
                root,
                fake_mv_mode="move-then-term",
            )

            self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
            self.assertEqual(
                (output / ".build-success").read_text(encoding="utf-8"),
                "old-marker\n",
            )
            self.assertEqual(
                (output / "构建报告.txt").read_text(encoding="utf-8"),
                "old-report\n",
            )
            self.assertFalse(any((root / "旧版备份").glob("*/生成的安装包")))

    def test_builder_restores_previous_output_when_install_creates_empty_directory_then_fails(self):
        with tempfile.TemporaryDirectory(prefix="taiji-output-install-failure-") as temp_dir:
            root = Path(temp_dir)
            output = root / "生成的安装包"
            output.mkdir()
            (output / ".build-success").write_text("old-marker\n", encoding="utf-8")

            result = self.run_build_output_archive_harness(
                root,
                fake_mv_mode="pass",
                fake_install_mode="create-then-fail",
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                (output / ".build-success").read_text(encoding="utf-8"),
                "old-marker\n",
            )
            self.assertFalse(any((root / "旧版备份").glob("*/生成的安装包")))

    def test_builder_restores_previous_output_when_install_creates_empty_directory_then_term(self):
        with tempfile.TemporaryDirectory(prefix="taiji-output-install-term-") as temp_dir:
            root = Path(temp_dir)
            output = root / "生成的安装包"
            output.mkdir()
            (output / "构建报告.txt").write_text("old-report\n", encoding="utf-8")

            result = self.run_build_output_archive_harness(
                root,
                fake_mv_mode="pass",
                fake_install_mode="create-then-term",
            )

            self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
            self.assertEqual(
                (output / "构建报告.txt").read_text(encoding="utf-8"),
                "old-report\n",
            )
            self.assertFalse(any((root / "旧版备份").glob("*/生成的安装包")))

    def test_builder_does_not_overwrite_unknown_output_created_during_install_failure(self):
        with tempfile.TemporaryDirectory(prefix="taiji-output-install-unknown-") as temp_dir:
            root = Path(temp_dir)
            output = root / "生成的安装包"
            output.mkdir()
            (output / ".build-success").write_text("old-marker\n", encoding="utf-8")

            result = self.run_build_output_archive_harness(
                root,
                fake_mv_mode="pass",
                fake_install_mode="create-unknown-then-fail",
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((output / "unknown.txt").read_text(encoding="utf-8"), "unknown\n")
            archived = list((root / "旧版备份").glob("*/生成的安装包/.build-success"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), "old-marker\n")

    def test_builder_archives_safe_stale_acceptance_staging_automatically(self):
        with tempfile.TemporaryDirectory(prefix="taiji-acceptance-stale-") as temp_dir:
            root = Path(temp_dir)
            stale = root / ".验收工具.tmp-4242"
            stale.mkdir()
            (stale / "partial.txt").write_text("partial\n", encoding="utf-8")

            result = self.run_acceptance_publication_harness(root, mode="archive-only")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(stale.exists())
            archived = list((root / "旧版备份").glob("验收工具临时残留-*/*/partial.txt"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), "partial\n")

    def test_builder_fails_closed_on_unsafe_stale_acceptance_staging(self):
        with tempfile.TemporaryDirectory(prefix="taiji-acceptance-unsafe-") as temp_dir:
            root = Path(temp_dir)
            outside = root / "outside"
            outside.mkdir()
            stale = root / ".验收工具.tmp-5252"
            stale.symlink_to(outside, target_is_directory=True)

            result = self.run_acceptance_publication_harness(root, mode="archive-only")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(stale.is_symlink())
            self.assertTrue(outside.is_dir())


if __name__ == "__main__":
    unittest.main()
