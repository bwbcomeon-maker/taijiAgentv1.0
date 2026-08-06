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
