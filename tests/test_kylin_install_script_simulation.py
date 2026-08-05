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

    def test_silent_deployer_declares_noninteractive_local_dpkg_contract(self):
        text = SILENT.read_text(encoding="utf-8")
        self.assertIn("DEBIAN_FRONTEND=noninteractive", text)
        self.assertIn("NEEDRESTART_MODE=a", text)
        self.assertIn("dpkg --install", text)
        self.assertIn("/run/lock/taiji-agent-deploy.lock", text)
        self.assertNotIn("apt-get", text)
        self.assertNotIn("apt update", text)
        self.assertNotIn("ONLINE_OK", text)

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
