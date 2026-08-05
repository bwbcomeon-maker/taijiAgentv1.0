import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "packaging/linux/support_bundle.py"
SUPPORT_ENTRYPOINT = ROOT / "packaging/linux/bin/taiji-agent-support"
INSTALLED_DIAGNOSE = ROOT / "packaging/linux/bin/taiji-agent-diagnose"
SOURCE_DIAGNOSE = ROOT / "hermes-local-lab/scripts/taiji-agent-diagnose"
DELIVERY_DIAGNOSE = ROOT / "taijiagent 打包交付/03_目标终端_导出诊断报告.sh"
BUILD = ROOT / "packaging/linux/deb/build-deb.sh"
PAYLOAD = ROOT / "packaging/linux/payload-contract.json"


class LinuxSupportBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="taiji-support-bundle-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "collector-input"
        self.source.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self._write_fixture()

    def tearDown(self):
        self.temp.cleanup()

    def _write_fixture(self):
        (self.source / "deployment-receipt.json").write_text(
            json.dumps(
                {
                    "schema": "taiji-linux-deployment-receipt/v1",
                    "operation": "upgrade",
                    "result": "upgraded",
                    "source_commit": "a" * 40,
                    "version_requested": "1.2.3",
                    "version_after": "1.2.3",
                    "architecture": "amd64",
                    "deb_basename": "taiji-agent_1.2.3_amd64.deb",
                    "deb_sha256": "b" * 64,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "c" * 64,
                    "preflight": "PASS",
                    "dpkg_status_before": "installed",
                    "dpkg_status_after": "installed",
                    "native_verify": "PASS",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.source / "support-bundle.json").write_text(
            json.dumps(
                {
                    "schema": "taiji.product.support-bundle.v1",
                    "product_version": "1.2.3",
                    "deb_sha256": "b" * 64,
                    "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
                    "compatibility_policy_sha256": "c" * 64,
                    "os": {"id": "kylin", "version": "V10", "architecture": "amd64"},
                    "dependencies": {"dpkg": "installed", "systemd": "installed"},
                    "forbidden_api_key": "sk-live-123456",
                    "host_name": "workstation-17",
                    "home": "/home/alice",
                    "raw_log": "Traceback: token=password123",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.source / "attachment.txt").write_text("customer attachment text\n", encoding="utf-8")
        (self.source / "browser-session.sqlite").write_bytes(b"sqlite session database\n")
        (self.source / "raw.log").write_text("Authorization: Bearer secret-token\n", encoding="utf-8")

    def run_bundle(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(SUPPORT),
                "--output-dir",
                str(self.output),
                "--source-dir",
                str(self.source),
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def bundle_paths(self):
        return sorted(self.output.glob("taiji-agent-support-*.tar.gz"))

    def test_support_bundle_contains_only_allowlisted_files_and_fields(self):
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        bundles = self.bundle_paths()
        self.assertEqual(len(bundles), 1)
        with tarfile.open(bundles[0], "r:gz") as archive:
            self.assertEqual(
                sorted(member.name for member in archive.getmembers()),
                [
                    "bundle-manifest.json",
                    "collection-errors.txt",
                    "deployment-receipt.json",
                    "support-bundle.json",
                ],
            )
            manifest = json.load(archive.extractfile("bundle-manifest.json"))
        self.assertEqual(manifest["schema"], "taiji-agent-support-bundle-manifest/v1")
        self.assertEqual(manifest["deb_sha256"], "b" * 64)
        self.assertEqual(manifest["compatibility_policy_id"], "taiji-linux-amd64-deb-v1")

    def test_bundle_omits_keys_tokens_passwords_user_host_ip_mac_serial_and_paths(self):
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = b"".join(path.read_bytes() for path in self.output.iterdir())
        forbidden = (
            b"sk-live-123456",
            b"secret-token",
            b"password123",
            b"alice",
            b"workstation-17",
            b"192.0.2.10",
            b"AA:BB:CC:DD:EE:FF",
            b"SERIAL-001",
            b"/home/",
            b"/opt/taiji-agent",
            b"Traceback",
            b"Bearer",
        )
        for sentinel in forbidden:
            self.assertNotIn(sentinel, payload)

    def test_bundle_never_contains_attachment_text_database_or_browser_session(self):
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(self.bundle_paths()[0], "r:gz") as archive:
            names = {member.name for member in archive.getmembers()}
            data = b"".join(archive.extractfile(name).read() for name in names)
        self.assertNotIn("attachment.txt", names)
        self.assertNotIn("browser-session.sqlite", names)
        self.assertNotIn(b"customer attachment text", data)
        self.assertNotIn(b"sqlite session database", data)

    def test_collection_failure_is_best_effort_and_uses_stable_codes(self):
        result = self.run_bundle("--fail-collector", "network")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(self.bundle_paths()[0], "r:gz") as archive:
            errors = archive.extractfile("collection-errors.txt").read().decode("utf-8")
        self.assertIn("collector=network code=NETWORK_UNAVAILABLE", errors)
        self.assertNotIn("Traceback", errors)
        self.assertNotIn("simulated", errors)

    def test_missing_collector_still_emits_minimal_bundle_with_stable_codes(self):
        (self.source / "deployment-receipt.json").unlink()
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(self.bundle_paths()[0], "r:gz") as archive:
            errors = archive.extractfile("collection-errors.txt").read().decode("utf-8")
            receipt = json.load(archive.extractfile("deployment-receipt.json"))
        self.assertIn("collector=deployment code=DEPLOYMENT_RECEIPT_UNAVAILABLE", errors)
        self.assertEqual(receipt["schema"], "taiji-linux-deployment-receipt/v1")

    def test_public_fields_never_allow_arbitrary_path_values(self):
        data = json.loads((self.source / "support-bundle.json").read_text(encoding="utf-8"))
        data["os"]["kernel"] = "/var/lib/taiji-agent"
        (self.source / "support-bundle.json").write_text(json.dumps(data), encoding="utf-8")
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tarfile.open(self.bundle_paths()[0], "r:gz") as archive:
            support = json.load(archive.extractfile("support-bundle.json"))
        self.assertNotIn("kernel", support.get("os", {}))

    def test_bundle_and_sidecar_are_mode_0600_with_basename_checksum(self):
        result = self.run_bundle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        bundle = self.bundle_paths()[0]
        sidecar = Path(f"{bundle}.sha256")
        self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        self.assertEqual(sidecar.read_text(encoding="ascii"), f"{hashlib.sha256(bundle.read_bytes()).hexdigest()}  {bundle.name}\n")
        self.assertNotIn(str(self.root).encode(), sidecar.read_bytes())

    def test_symlink_hardlink_fifo_and_oversize_inputs_are_rejected(self):
        cases = ("symlink", "hardlink", "fifo", "oversize")
        for case in cases:
            with self.subTest(case=case):
                target = self.source / "deployment-receipt.json"
                original = target.read_bytes()
                target.unlink()
                if case == "symlink":
                    target.symlink_to(self.source / "support-bundle.json")
                elif case == "hardlink":
                    target.hardlink_to(self.source / "support-bundle.json")
                elif case == "fifo":
                    os.mkfifo(target)
                else:
                    target.write_bytes(b"x" * (2 * 1024 * 1024))
                result = self.run_bundle()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.bundle_paths(), [])
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.write_bytes(original)

    def test_installed_diagnose_no_longer_emits_key_suffix_base_url_or_raw_logs(self):
        source = SOURCE_DIAGNOSE.read_text(encoding="utf-8")
        installed = INSTALLED_DIAGNOSE.read_text(encoding="utf-8")
        for text in (source, installed):
            for forbidden in (
                "deepseek_key.canonical.suffix",
                "base_url=",
                "pgrep -af",
                "tail -120",
                "user=",
                "/home/",
                "TAIJI_DESKTOP_ACCESS_TOKEN",
            ):
                self.assertNotIn(forbidden, text)

    def test_staged_final_and_installed_privacy_scans_share_forbidden_sentinels(self):
        runtime_paths = (
            SOURCE_DIAGNOSE,
            INSTALLED_DIAGNOSE,
            SUPPORT_ENTRYPOINT,
            DELIVERY_DIAGNOSE,
        )
        forbidden = re.compile(r"(?i)(api[_ -]?key|password|bearer|/home/|pgrep\s+-af|tail\s+-120|raw[_ -]?log)")
        for path in runtime_paths:
            self.assertTrue(path.is_file(), path)
            self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")), path)
        # Build metadata may mention the names of fields it actively rejects;
        # it must not embed runtime diagnostics, credentials, or local paths.
        for path in (BUILD, PAYLOAD):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("TAIJI_DESKTOP_ACCESS_TOKEN", text)
            self.assertNotIn("pgrep -af", text)
            self.assertNotIn("tail -120", text)


if __name__ == "__main__":
    unittest.main()
