from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECEIPT_MODULE = ROOT / "packaging/linux/deployment_receipt.py"
SILENT_SCRIPT = ROOT / "packaging/linux/deb/taiji-silent-deploy.sh"
POLICY = ROOT / "packaging/linux/compatibility-policy.json"


class SilentDeploymentReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="taiji-silent-deploy-")
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _import_receipt(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("deployment_receipt_test", RECEIPT_MODULE)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module

    def _base_receipt(self, **overrides):
        receipt = {
            "schema": "taiji-linux-deployment-receipt/v1",
            "deployment_id": "dep-0123456789abcdef",
            "operation": "fresh_install",
            "result": "installed",
            "source_commit": "a" * 40,
            "version_before": None,
            "version_requested": "1.2.3",
            "version_after": "1.2.3",
            "architecture": "amd64",
            "deb_basename": "taiji-agent_1.2.3_amd64.deb",
            "deb_sha256": "b" * 64,
            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
            "compatibility_policy_sha256": "c" * 64,
            "preflight": "PASS",
            "dpkg_status_before": "not-installed",
            "dpkg_status_after": "installed",
            "native_verify": "PASS",
            "started_at_utc": "2026-08-05T01:02:03Z",
            "finished_at_utc": "2026-08-05T01:02:04Z",
            "error_stage": None,
            "error_code": None,
            "rollback_transaction_id": None,
        }
        receipt.update(overrides)
        return receipt

    def test_success_receipt_has_exact_schema_and_no_machine_identity(self):
        module = self._import_receipt()
        receipt = self._base_receipt()
        self.assertEqual(module.validate_receipt(receipt), receipt)
        self.assertEqual(set(receipt), module.RECEIPT_FIELDS)
        encoded = json.dumps(receipt, ensure_ascii=False)
        for forbidden in ("hostname", "username", "HOME", "127.0.0.1", "raw command", "password"):
            self.assertNotIn(forbidden, encoded)

    def test_failure_receipt_is_atomic_mode_0600_and_contains_stable_error_code(self):
        module = self._import_receipt()
        receipt = self._base_receipt(
            result="blocked",
            preflight="BLOCKED",
            dpkg_status_after="unchanged",
            native_verify="NOT_RUN",
            error_stage="preflight",
            error_code="DEB_SHA256_MISMATCH",
        )
        destination = self.root / "receipt.json"
        module.write_receipt_atomic(destination, receipt)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), receipt)
        self.assertFalse(list(self.root.glob(".receipt.json.*")))

    def test_fresh_reinstall_upgrade_and_explicit_rollback_have_stable_results(self):
        module = self._import_receipt()
        expected = {
            "fresh_install": "installed",
            "reinstall": "reinstalled",
            "upgrade": "upgraded",
            "rollback": "rolled_back",
        }
        for operation, result in expected.items():
            receipt = self._base_receipt(operation=operation, result=result)
            self.assertEqual(module.validate_receipt(receipt), receipt)

    def test_success_receipt_requires_native_verify_pass(self):
        module = self._import_receipt()
        for native_verify in ("FAIL", "NOT_RUN"):
            with self.subTest(native_verify=native_verify):
                with self.assertRaises(module.ReceiptError):
                    module.validate_receipt(
                        self._base_receipt(native_verify=native_verify)
                    )

    def test_upgrade_rollback_receipt_preserves_recovery_cause(self):
        module = self._import_receipt()
        receipt = self._base_receipt(
            operation="upgrade",
            result="rolled_back",
            version_before="1.2.2",
            version_after="1.2.2",
            dpkg_status_before="installed",
            dpkg_status_after="installed",
            native_verify="PASS",
            error_stage="dpkg",
            error_code="DPKG_INSTALL_FAILED_ROLLED_BACK",
            rollback_transaction_id="txn-abc",
        )
        self.assertEqual(module.validate_receipt(receipt), receipt)

    def test_receipt_rejects_extra_and_forbidden_fields(self):
        module = self._import_receipt()
        with self.assertRaises(module.ReceiptError):
            module.validate_receipt(self._base_receipt(hostname="not-allowed"))
        with self.assertRaises(module.ReceiptError):
            module.validate_receipt(self._base_receipt(error_code="rm -rf /"))

    def test_receipt_destination_symlink_and_hardlink_are_rejected(self):
        module = self._import_receipt()
        receipt = self._base_receipt()
        real = self.root / "real-receipt.json"
        real.write_text("old\n", encoding="utf-8")
        symlink = self.root / "symlink-receipt.json"
        symlink.symlink_to(real.name)
        with self.assertRaises(module.ReceiptError):
            module.write_receipt_atomic(symlink, receipt)
        dangling = self.root / "dangling-receipt.json"
        dangling.symlink_to("missing-target.json")
        with self.assertRaises(module.ReceiptError):
            module.write_receipt_atomic(dangling, receipt)
        redirected = self.root / "redirected"
        redirected.mkdir()
        nested_link = self.root / "nested-link"
        nested_link.symlink_to(redirected, target_is_directory=True)
        with self.assertRaises(module.ReceiptError):
            module.write_receipt_atomic(nested_link / "receipt.json", receipt)
        hardlink = self.root / "hardlink-receipt.json"
        os.link(real, hardlink)
        module.write_receipt_atomic(hardlink, receipt)
        self.assertEqual(json.loads(hardlink.read_text(encoding="utf-8"))["schema"], module.SCHEMA)

    def test_certification_admission_record_is_digest_only_and_atomic(self):
        module = self._import_receipt()
        challenge = "d" * 64
        record = {
            "schema": module.ADMISSION_RECORD_SCHEMA,
            "admission_mode": "certification",
            "challenge_digest": hashlib.sha256(challenge.encode()).hexdigest(),
            "source_commit": "a" * 40,
            "deb_basename": "taiji-agent_1.2.3_amd64.deb",
            "deb_sha256": "b" * 64,
            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
            "compatibility_policy_sha256": "c" * 64,
            "generated_at_utc": "2026-08-05T01:02:03Z",
        }
        destination = self.root / "deployment-admission.json"
        module.write_admission_record_atomic(destination, record)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), record)
        self.assertNotIn(challenge, destination.read_text(encoding="utf-8"))

    def _write_fake_payload(self, version="1.2.3"):
        deb = self.root / f"taiji-agent_{version}_amd64.deb"
        deb.write_bytes(b"fake-deb-bytes")
        sha = hashlib.sha256(deb.read_bytes()).hexdigest()
        deb.with_name(deb.name + ".sha256").write_text(f"{sha}  {deb.name}\n", encoding="utf-8")
        manifest = self.root / "taiji-package-manifest.json"
        policy_sha = hashlib.sha256(POLICY.read_bytes()).hexdigest()
        manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": version,
                    "architecture": "amd64",
                    "deb": deb.name,
                    "deb_sha256": sha,
                    "source_commit": "a" * 40,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": policy_sha,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return deb, sha, manifest

    def _run_script(self, *args, env=None):
        complete_env = os.environ.copy()
        complete_env.update(env or {})
        return subprocess.run(
            ["bash", str(SILENT_SCRIPT), *map(str, args)],
            text=True,
            capture_output=True,
            env=complete_env,
            check=False,
        )

    def test_hash_or_signature_failure_occurs_before_dpkg_mutation(self):
        deb, sha, manifest = self._write_fake_payload()
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        dpkg = fake_bin / "dpkg"
        dpkg.write_text("#!/bin/sh\ntouch \"$FAKE_DPKG_MUTATED\"\nexit 0\n", encoding="utf-8")
        dpkg.chmod(0o755)
        receipt = self.root / "receipt.json"
        result = self._run_script(
            "--deb",
            deb,
            "--expected-version",
            "1.2.3",
            "--expected-sha256",
            "0" * 64,
            "--admission-mode",
            "certification",
            "--operation",
            "fresh_install",
            "--receipt",
            receipt,
            "--build-manifest",
            manifest,
            "--policy",
            POLICY,
            "--certification-challenge",
            "d" * 64,
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_DPKG_MUTATED": str(self.root / "dpkg-mutated"),
                "EUID": "1000",
            },
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "dpkg-mutated").exists(), result.stdout + result.stderr)
        self.assertTrue(receipt.exists(), result.stdout + result.stderr)
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["result"], "blocked")

    def test_certification_admission_requires_build_binding_and_one_time_challenge(self):
        deb, sha, manifest = self._write_fake_payload()
        receipt = self.root / "receipt.json"
        result = self._run_script(
            "--deb",
            deb,
            "--expected-version",
            "1.2.3",
            "--expected-sha256",
            sha,
            "--admission-mode",
            "certification",
            "--operation",
            "fresh_install",
            "--receipt",
            receipt,
            "--build-manifest",
            manifest,
            "--policy",
            POLICY,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["error_code"], "CHALLENGE_REQUIRED")

    def test_malformed_option_does_not_loop_and_still_writes_blocked_receipt(self):
        receipt = self.root / "receipt.json"
        result = subprocess.run(
            ["bash", str(SILENT_SCRIPT), "--receipt", str(receipt), "--deb"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(receipt.exists(), result.stderr)
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(payload["result"], "blocked")
        self.assertEqual(payload["error_code"], "ARGUMENT_INVALID")

    def test_lock_conflict_and_interruption_leave_dpkg_state_unchanged(self):
        text = SILENT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/run/lock/taiji-agent-deploy.lock", text)
        self.assertRegex(text, r"flock")
        self.assertIn("dpkg --install", text)

    def test_silent_deploy_never_updates_sources_or_downloads(self):
        text = SILENT_SCRIPT.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"apt(?:-get)?\s+update")
        self.assertNotRegex(text, r"apt(?:-get)?\s+(?:install|download|get)")
        self.assertNotIn("ONLINE_OK", text)
        self.assertNotIn("apt-get", text)

    def test_certification_challenge_is_never_used_as_a_path_component(self):
        text = SILENT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("ADMISSION_CHALLENGE_DIGEST", text)
        self.assertIn('challenge_file="$challenge_dir/$ADMISSION_CHALLENGE_DIGEST"', text)
        self.assertNotIn('challenge_file="$challenge_dir/$CERTIFICATION_CHALLENGE"', text)

    def test_release_admission_binds_source_commit_and_rejects_target_baseline(self):
        text = SILENT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('--source-commit "$SOURCE_COMMIT"', text)
        self.assertIn('"target_baseline_profile_id" in evidence', text)
        self.assertIn('"target_baseline_sha256" in evidence', text)
        self.assertIn("RELEASE_PUBLIC_KEY_FINGERPRINT_MISMATCH", text)
        self.assertIn("openssl pkey -pubin", text)

    def test_release_maintainer_heredoc_compiles_as_python(self):
        text = SILENT_SCRIPT.read_text(encoding="utf-8")
        marker = 'python3 - "$RELEASE_EVIDENCE" "$POLICY_PATH" <<\'PY\''
        body = text.split(marker, 1)[1].split("\n", 1)[1].split("\nPY\n", 1)[0]
        compile(body, "release-maintainer-heredoc", "exec")

    def test_customer_directory_does_not_require_or_publish_management_script(self):
        wrapper = ROOT / "taijiagent 打包交付/02_目标终端_安装并验证.sh"
        text = wrapper.read_text(encoding="utf-8")
        self.assertIn("taiji-silent-deploy.sh", text)
        self.assertNotIn("离线依赖", text)
        self.assertNotIn("apt-get update", text)
        self.assertNotIn("ONLINE_OK", text)

    def test_copied_management_directory_is_self_contained_and_writes_receipt(self):
        """A delivery copy must not resolve helpers through the source checkout."""
        delivery = self.root / "delivery" / "验收工具" / "management"
        delivery.mkdir(parents=True)
        for source in (SILENT_SCRIPT, RECEIPT_MODULE, ROOT / "packaging/linux/compatibility_policy.py", POLICY):
            (delivery / source.name).write_bytes(source.read_bytes())
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        sha256sum = fake_bin / "sha256sum"
        sha256sum.write_text(
            "#!/bin/sh\nexec shasum -a 256 \"$@\"\n", encoding="utf-8"
        )
        sha256sum.chmod(0o755)
        deb = self.root / "taiji-agent_1.2.3_amd64.deb"
        deb.write_bytes(b"fake-deb-bytes")
        sha = hashlib.sha256(deb.read_bytes()).hexdigest()
        deb.with_name(deb.name + ".sha256").write_text(f"{sha}  {deb.name}\n", encoding="utf-8")
        policy_sha = hashlib.sha256(POLICY.read_bytes()).hexdigest()
        manifest = self.root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "deb_basename": deb.name,
                    "deb_sha256": sha,
                    "source_commit": "a" * 40,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": policy_sha,
                }
            ),
            encoding="utf-8",
        )
        receipt = self.root / "receipt.json"
        result = subprocess.run(
            [
                "bash",
                str(delivery / "taiji-silent-deploy.sh"),
                "--deb",
                str(deb),
                "--expected-version",
                "1.2.3",
                "--expected-sha256",
                sha,
                "--admission-mode",
                "certification",
                "--operation",
                "fresh_install",
                "--receipt",
                str(receipt),
                "--build-manifest",
                str(manifest),
                "--policy",
                str(delivery / POLICY.name),
            ],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertTrue(receipt.exists(), result.stderr)
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["error_code"], "CHALLENGE_REQUIRED")
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertNotIn("FileNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
