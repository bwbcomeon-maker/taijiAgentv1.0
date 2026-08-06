from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "taijiagent 打包交付/02_目标终端_安装并验证.sh"
SILENT = ROOT / "packaging/linux/deb/taiji-silent-deploy.sh"


class KylinInstallScriptSimulationTest(unittest.TestCase):
    def test_wrapper_is_management_only_and_points_to_silent_deployer(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("taiji-silent-deploy.sh", text)
        self.assertIn("TAIJI_ADMISSION_MODE", text)
        self.assertIn("TAIJI_CERTIFICATION_CHALLENGE", text)
        self.assertNotIn("apt-get update", text)
        self.assertNotIn("ONLINE_OK", text)
        self.assertNotIn("离线依赖", text)

    def test_wrapper_does_not_copy_management_script_into_output(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertNotIn("cp ", text)
        # The wrapper now copies the management plane only into a root-owned
        # /var/tmp staging directory before elevation; it must never copy
        # management files into the customer output directory.
        self.assertIn("mktemp -d /var/tmp/taiji-agent-management.XXXXXX", text)
        self.assertIn("stage_regular_file", text)
        self.assertIn("O_NOFOLLOW", text)
        self.assertIn("sudo env -i", text)
        self.assertIn("PATH=/usr/sbin:/usr/bin:/sbin:/bin", text)
        self.assertIn('"$stage/build-manifest.json"', text)
        self.assertIn('"$stage/previous.deb.sig"', text)
        self.assertNotIn("$OUTPUT_DIR/management", text)
        self.assertNotIn("离线仓库", text)

    def test_root_staging_preserves_deb_basename_bound_by_sha256_sidecar(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("stage_deb_with_sidecar", text)
        self.assertIn('target="$stage/$(basename -- "$source")"', text)
        self.assertIn('stage_regular_file "$source.sha256" "$target.sha256" 0600', text)
        self.assertIn('STAGED_DEB_PATH="$target"', text)
        self.assertIn('args[index + 1]="$STAGED_DEB_PATH"', text)
        self.assertNotIn('="$(stage_deb_with_sidecar', text)
        self.assertNotIn('"$stage/candidate.deb"', text)
        self.assertNotIn('"$stage/previous.deb"', text)

    def test_root_staging_cleanup_covers_hidden_files_without_recursive_delete(self):
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('find "$stage" -mindepth 1 -maxdepth 1 -type f -delete', text)
        self.assertNotIn('rm -f -- "$stage"/*', text)

    def test_root_staging_disables_python_bytecode_before_any_helper_runs(self):
        text = WRAPPER.read_text(encoding="utf-8")
        staged = text[
            text.index("<<'ROOT_STAGED_SCRIPT'") : text.index("ROOT_STAGED_SCRIPT\n}")
        ]
        assignment = staged.index('PYTHONDONTWRITEBYTECODE="1"')
        exported = staged.index("export PYTHONDONTWRITEBYTECODE")
        first_helper = staged.index("stage_regular_file() {")
        self.assertLess(assignment, exported)
        self.assertLess(exported, first_helper)

    def test_silent_deployer_declares_noninteractive_local_dpkg_contract(self):
        text = SILENT.read_text(encoding="utf-8")
        self.assertIn("DEBIAN_FRONTEND=noninteractive", text)
        self.assertIn("NEEDRESTART_MODE=a", text)
        self.assertIn("dpkg --install", text)
        self.assertIn("/run/lock/taiji-agent-deploy.lock", text)
        self.assertNotIn("apt-get", text)
        self.assertNotIn("apt update", text)
        self.assertNotIn("ONLINE_OK", text)

    def test_silent_deployer_surfaces_dpkg_maintainer_failure_details(self):
        text = SILENT.read_text(encoding="utf-8")
        install_body = text[
            text.index("install_local_deb() {") : text.index("\nmain() {", text.index("install_local_deb() {"))
        ]

        self.assertIn("DPKG_LOG_PATH", install_body)
        self.assertIn('tail -n 80 -- "$DPKG_LOG_PATH"', install_body)
        self.assertIn("DPKG_INSTALL_FAILED", install_body)
        self.assertNotIn('dpkg --install --force-confold -- "$DEB_PATH" >/dev/null 2>&1', install_body)

    def test_silent_deployer_uses_system_only_native_verify_after_install_and_rollback(self):
        text = SILENT.read_text(encoding="utf-8")
        rollback_install_body = text[
            text.index("rollback_previous_package() {") : text.index(
                "\nverify_rollback_package() {", text.index("rollback_previous_package() {")
            )
        ]
        rollback_body = text[
            text.index("verify_rollback_package() {") : text.index(
                "\nstage_candidate_for_install() {", text.index("verify_rollback_package() {")
            )
        ]
        install_body = text[
            text.index("install_local_deb() {") : text.index(
                "\nmain() {", text.index("install_local_deb() {")
            )
        ]

        for body in (rollback_body, install_body):
            self.assertIn("env -i", body)
            self.assertIn("PATH=/usr/sbin:/usr/bin:/sbin:/bin", body)
            self.assertIn("LANG=C.UTF-8", body)
            self.assertIn('"$verifier" --system-only', body)
            self.assertNotIn('"$verifier" >/dev/null 2>&1', body)
            self.assertNotIn("TAIJI_NATIVE_VERIFY_MODE=system-only", body)

        self.assertIn("TAIJI_AGENT_SYNC_PACKAGED_CONFIG=0", rollback_body)
        self.assertNotIn("TAIJI_AGENT_SYNC_PACKAGED_CONFIG=0", install_body)
        self.assertIn("ROLLBACK_DPKG_LOG_PATH", rollback_install_body)
        self.assertIn("ROLLBACK_VERIFY_LOG_PATH", rollback_body)
        self.assertIn('tail -n 80 -- "$ROLLBACK_DPKG_LOG_PATH"', rollback_install_body)
        self.assertIn('tail -n 80 -- "$ROLLBACK_VERIFY_LOG_PATH"', rollback_body)
        self.assertIn('if ! rm -f -- "$ROLLBACK_DPKG_LOG_PATH"', rollback_install_body)
        self.assertIn('if ! rm -f -- "$ROLLBACK_VERIFY_LOG_PATH"', rollback_body)
        self.assertIn("NATIVE_VERIFY_LOG_PATH", install_body)
        self.assertIn('tail -n 80 -- "$NATIVE_VERIFY_LOG_PATH"', install_body)
        self.assertIn("NATIVE_VERIFY_UNAVAILABLE", install_body)

    def test_silent_deployer_dynamically_tails_and_cleans_private_dpkg_log(self):
        with tempfile.TemporaryDirectory(prefix="taiji-dpkg-log-") as temporary:
            root = Path(temporary)
            library = root / "taiji-silent-deploy-library.sh"
            source = SILENT.read_text(encoding="utf-8")
            self.assertTrue(source.rstrip().endswith('main "$@"'))
            library.write_text(source.rsplit('main "$@"', 1)[0], encoding="utf-8")
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            deb = root / "candidate.deb"
            deb.write_bytes(b"fake")
            result_file = root / "harness-result.txt"
            harness = r'''
source "$1"
chmod() {
  local mode="$1"
  shift
  if [ "${1:-}" = "--" ]; then shift; fi
  command chmod "$mode" "$@"
}
dpkg() {
  local index
  for index in $(seq 1 90); do
    printf 'maintainer-line-%03d\n' "$index"
  done
  return 42
}
read_dpkg_status() { printf 'half-configured\n'; }
read_dpkg_version() { printf '\n'; }
manual_recovery() {
  local mode
  mode="$(stat -c '%a' "$DPKG_LOG_PATH" 2>/dev/null || stat -f '%Lp' "$DPKG_LOG_PATH")"
  printf 'mode=%s\nerror_stage=%s\nerror_code=%s\nlog_path=%s\n' \
    "$mode" "$1" "$2" "$DPKG_LOG_PATH" > "$HARNESS_RESULT"
  cleanup_staged_deb
  exit "$3"
}
STAGING_DIR="$HARNESS_STAGE"
DEB_PATH="$HARNESS_DEB"
OPERATION=fresh_install
install_local_deb
'''
            completed = subprocess.run(
                ["/bin/bash", "-c", harness, "taiji-dpkg-log-test", str(library)],
                env={
                    **os.environ,
                    "HARNESS_STAGE": str(stage),
                    "HARNESS_DEB": str(deb),
                    "HARNESS_RESULT": str(result_file),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 42, completed.stderr)
            self.assertIn("DPKG_INSTALL_FAILED", completed.stderr)
            self.assertIn("maintainer-line-090", completed.stderr)
            self.assertNotIn("maintainer-line-001", completed.stderr)
            result = result_file.read_text(encoding="utf-8")
            self.assertIn("mode=600", result)
            self.assertIn("error_stage=dpkg", result)
            self.assertIn("error_code=DPKG_INSTALL_FAILED", result)
            log_path = Path(result.split("log_path=", 1)[1].strip())
            self.assertFalse(log_path.exists())
            self.assertFalse(stage.exists())

    def _run_post_install_native_verify_harness(
        self, verifier_source: str | None
    ) -> tuple[subprocess.CompletedProcess, str, str, bool]:
        with tempfile.TemporaryDirectory(prefix="taiji-native-verify-") as temporary:
            root = Path(temporary)
            library = root / "taiji-silent-deploy-library.sh"
            verifier = root / "taiji-native-verify"
            observed = root / "observed.txt"
            result_file = root / "result.txt"
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            deb = root / "candidate.deb"
            deb.write_bytes(b"fake")

            source = SILENT.read_text(encoding="utf-8")
            source = source.replace(
                "/opt/taiji-agent/bin/taiji-native-verify", str(verifier)
            )
            library.write_text(source.rsplit('main "$@"', 1)[0], encoding="utf-8")
            if verifier_source is not None:
                verifier.write_text(
                    verifier_source.replace("__OBSERVED__", str(observed)),
                    encoding="utf-8",
                )
                verifier.chmod(0o755)

            harness = r'''
source "$1"
chmod() {
  local mode="$1"
  shift
  if [ "${1:-}" = "--" ]; then shift; fi
  command chmod "$mode" "$@"
}
dpkg() { printf 'dpkg-ok\n'; return 0; }
read_dpkg_status() { printf 'installed\n'; }
read_dpkg_version() { printf '1.0.0\n'; }
finish() {
  printf 'finish=%s\nnative=%s\n' "$1" "$NATIVE_VERIFY" > "$HARNESS_RESULT"
  cleanup_staged_deb
  exit "$1"
}
manual_recovery() {
  local mode="missing"
  if [ -n "${NATIVE_VERIFY_LOG_PATH:-}" ] && [ -e "$NATIVE_VERIFY_LOG_PATH" ]; then
    mode="$(stat -c '%a' "$NATIVE_VERIFY_LOG_PATH" 2>/dev/null || stat -f '%Lp' "$NATIVE_VERIFY_LOG_PATH")"
  fi
  printf 'manual=%s\nerror=%s\nnative=%s\nmode=%s\n' \
    "$1" "$2" "$NATIVE_VERIFY" "$mode" > "$HARNESS_RESULT"
  cleanup_staged_deb
  exit "$3"
}
STAGING_DIR="$HARNESS_STAGE"
DEB_PATH="$HARNESS_DEB"
STAGED_DEB_PATH=""
STAGED_PREVIOUS_DEB_PATH=""
STAGED_PREVIOUS_SIGNATURE_PATH=""
OPERATION=fresh_install
UPGRADE_TRANSACTION_ID=""
install_local_deb
'''
            completed = subprocess.run(
                ["/bin/bash", "-c", harness, "taiji-native-test", str(library)],
                env={
                    **os.environ,
                    "HARNESS_STAGE": str(stage),
                    "HARNESS_DEB": str(deb),
                    "HARNESS_RESULT": str(result_file),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            result = result_file.read_text(encoding="utf-8") if result_file.exists() else ""
            observation = observed.read_text(encoding="utf-8") if observed.exists() else ""
            return completed, result, observation, stage.exists()

    def test_silent_deployer_runs_post_install_verify_in_exact_system_only_env(self):
        completed, result, observed, stage_exists = self._run_post_install_native_verify_harness(
            """#!/usr/bin/env bash
printf 'home=%s\nmode=%s\n' "${HOME:-unset}" "${1:-unset}" > '__OBSERVED__'
exit 0
"""
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("finish=0", result)
        self.assertIn("native=PASS", result)
        self.assertIn("home=unset", observed)
        self.assertIn("mode=--system-only", observed)
        self.assertFalse(stage_exists)

    def test_silent_deployer_fails_closed_when_native_verifier_is_unavailable(self):
        completed, result, _, stage_exists = self._run_post_install_native_verify_harness(None)

        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("manual=native_verify", result)
        self.assertIn("error=NATIVE_VERIFY_UNAVAILABLE", result)
        self.assertIn("native=NOT_RUN", result)
        self.assertFalse(stage_exists)

    def test_silent_deployer_tails_and_cleans_private_native_verify_log(self):
        completed, result, _, stage_exists = self._run_post_install_native_verify_harness(
            """#!/usr/bin/env bash
for index in $(seq 1 90); do printf 'native-line-%03d\n' "$index"; done
exit 17
"""
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("native-line-090", completed.stderr)
        self.assertNotIn("native-line-001", completed.stderr)
        self.assertIn("error=NATIVE_VERIFY_FAILED", result)
        self.assertIn("native=FAIL", result)
        self.assertIn("mode=600", result)
        self.assertFalse(stage_exists)

    def _run_recovery_verify_harness(
        self, *, dpkg_exit: int, verifier_source: str
    ) -> tuple[subprocess.CompletedProcess, str, str, bool]:
        with tempfile.TemporaryDirectory(prefix="taiji-recovery-verify-") as temporary:
            root = Path(temporary)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            previous = stage / "previous.deb"
            previous.write_bytes(b"previous")
            verifier = root / "taiji-native-verify"
            observed = root / "observed.txt"
            result_file = root / "result.txt"
            library = root / "taiji-silent-deploy-library.sh"

            source = SILENT.read_text(encoding="utf-8").replace(
                "/opt/taiji-agent/bin/taiji-native-verify", str(verifier)
            )
            library.write_text(source.rsplit('main "$@"', 1)[0], encoding="utf-8")
            verifier.write_text(
                verifier_source.replace("__OBSERVED__", str(observed)),
                encoding="utf-8",
            )
            verifier.chmod(0o755)

            harness = r'''
source "$1"
chmod() {
  local mode="$1"
  shift
  if [ "${1:-}" = "--" ]; then shift; fi
  command chmod "$mode" "$@"
}
dpkg() {
  local index
  for index in $(seq 1 90); do printf 'recovery-dpkg-line-%03d\n' "$index"; done
  return "$HARNESS_DPKG_EXIT"
}
refresh_dpkg_state_after_recovery() {
  DPKG_STATUS_AFTER=installed
  VERSION_AFTER=1.0.0
}
OPERATION=upgrade
STAGING_DIR="$HARNESS_STAGE"
STAGED_DEB_PATH=""
STAGED_PREVIOUS_DEB_PATH="$HARNESS_PREVIOUS"
STAGED_PREVIOUS_SIGNATURE_PATH=""
PREVIOUS_VERSION=1.0.0
dpkg_rc=0
verify_rc=not-run
if rollback_previous_package; then
  if verify_rollback_package; then verify_rc=0; else verify_rc=$?; fi
else
  dpkg_rc=$?
fi
active_log="${ROLLBACK_DPKG_LOG_PATH:-${ROLLBACK_VERIFY_LOG_PATH:-}}"
mode=missing
if [ -n "$active_log" ] && [ -e "$active_log" ]; then
  mode="$(stat -c '%a' "$active_log" 2>/dev/null || stat -f '%Lp' "$active_log")"
fi
printf 'dpkg=%s\nverify=%s\nnative=%s\nmode=%s\n' \
  "$dpkg_rc" "$verify_rc" "$NATIVE_VERIFY" "$mode" > "$HARNESS_RESULT"
cleanup_staged_deb
if [ "$dpkg_rc" -ne 0 ]; then exit "$dpkg_rc"; fi
if [ "$verify_rc" != 0 ]; then exit "$verify_rc"; fi
exit 0
'''
            completed = subprocess.run(
                ["/bin/bash", "-c", harness, "taiji-recovery-test", str(library)],
                env={
                    **os.environ,
                    "HARNESS_STAGE": str(stage),
                    "HARNESS_PREVIOUS": str(previous),
                    "HARNESS_DPKG_EXIT": str(dpkg_exit),
                    "HARNESS_RESULT": str(result_file),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            result = result_file.read_text(encoding="utf-8")
            observation = observed.read_text(encoding="utf-8") if observed.exists() else ""
            return completed, result, observation, stage.exists()

    def test_recovery_verify_uses_exact_system_only_env_without_rewriting_config(self):
        completed, result, observed, stage_exists = self._run_recovery_verify_harness(
            dpkg_exit=0,
            verifier_source="""#!/usr/bin/env bash
printf 'home=%s\nmode=%s\nsync=%s\n' \
  "${HOME:-unset}" "${1:-unset}" "${TAIJI_AGENT_SYNC_PACKAGED_CONFIG:-unset}" \
  > '__OBSERVED__'
exit 0
""",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("verify=0", result)
        self.assertIn("native=PASS", result)
        self.assertIn("home=unset", observed)
        self.assertIn("mode=--system-only", observed)
        self.assertIn("sync=0", observed)
        self.assertFalse(stage_exists)

    def test_recovery_dpkg_and_verify_failures_surface_private_log_tail(self):
        dpkg_failed, dpkg_result, _, dpkg_stage_exists = self._run_recovery_verify_harness(
            dpkg_exit=42,
            verifier_source="#!/usr/bin/env bash\nexit 0\n",
        )
        self.assertEqual(dpkg_failed.returncode, 42)
        self.assertIn("recovery-dpkg-line-090", dpkg_failed.stderr)
        self.assertNotIn("recovery-dpkg-line-001", dpkg_failed.stderr)
        self.assertIn("mode=600", dpkg_result)
        self.assertFalse(dpkg_stage_exists)

        verify_failed, verify_result, _, verify_stage_exists = self._run_recovery_verify_harness(
            dpkg_exit=0,
            verifier_source="""#!/usr/bin/env bash
for index in $(seq 1 90); do printf 'recovery-verify-line-%03d\n' "$index"; done
exit 17
""",
        )
        self.assertEqual(verify_failed.returncode, 17)
        self.assertIn("recovery-verify-line-090", verify_failed.stderr)
        self.assertNotIn("recovery-verify-line-001", verify_failed.stderr)
        self.assertIn("native=FAIL", verify_result)
        self.assertIn("mode=600", verify_result)
        self.assertFalse(verify_stage_exists)

    def test_dpkg_receives_only_root_owned_staged_copy_after_second_hash(self):
        text = SILENT.read_text(encoding="utf-8")
        self.assertIn("stage_candidate_for_install", text)
        self.assertIn("stage_previous_for_rollback", text)
        self.assertIn("mktemp -d /var/tmp/taiji-agent-deploy.XXXXXX", text)
        self.assertIn("install -o 0 -g 0 -m 0600", text)
        self.assertIn("STAGED_DEB_SHA256_MISMATCH", text)
        self.assertIn("STAGED_PREVIOUS_DEB_SHA256_MISMATCH", text)
        self.assertIn('dpkg --install --force-confold -- "$STAGED_PREVIOUS_DEB_PATH"', text)
        main_body = text[text.index("main()") :]
        self.assertLess(main_body.index("stage_candidate_for_install"), main_body.index("install_local_deb"))
        self.assertLess(main_body.index("stage_previous_for_rollback"), main_body.index("prepare_upgrade_transaction"))
        self.assertRegex(text, r"actual=\"\$\(sha256sum -- \"\$STAGED_DEB_PATH\"")

    def test_wrapper_requires_explicit_challenge_for_certification(self):
        with tempfile.TemporaryDirectory(prefix="taiji-wrapper-") as temporary:
            root = Path(temporary)
            output = root / "生成的安装包"
            output.mkdir()
            deb = output / "taiji-agent_1.2.3_amd64.deb"
            deb.write_bytes(b"fake")
            import hashlib

            sha = hashlib.sha256(deb.read_bytes()).hexdigest()
            (deb.with_name(deb.name + ".sha256")).write_text(f"{sha}  {deb.name}\n", encoding="utf-8")
            policy = ROOT / "packaging/linux/compatibility-policy.json"
            policy_sha = hashlib.sha256(policy.read_bytes()).hexdigest()
            (output / "taiji-package-manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "taiji-package-manifest/v3",
                        "package": "taiji-agent",
                        "version": "1.2.3",
                        "architecture": "amd64",
                        "source_commit": "a" * 40,
                        "deb_basename": deb.name,
                        "deb_sha256": sha,
                        "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                        "compatibility_policy_sha256": policy_sha,
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(WRAPPER)],
                env={
                    **os.environ,
                    "TAIJI_OUTPUT_DIR": str(output),
                    "TAIJI_BUILD_MANIFEST": str(output / "taiji-package-manifest.json"),
                    "TAIJI_POLICY_PATH": str(policy),
                    "TAIJI_RECEIPT_PATH": str(root / "receipt.json"),
                    "PATH": os.environ.get("PATH", ""),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("一次性", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
