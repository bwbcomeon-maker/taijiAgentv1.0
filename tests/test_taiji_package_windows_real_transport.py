"""Local contracts for the gated Windows SSH transport."""

import base64
import json
import subprocess
import unittest
from unittest import mock

from packaging.pipeline.adapters import windows_ssh
from packaging.pipeline.core.errors import PipelineError
from packaging.pipeline.core.models import canonical_json_sha256


POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
TARGET = {
    "host_alias": "windows-direct",
    "powershell": POWERSHELL,
    "remote_root": r"D:\tw\taiji-builds",
    "cache_root": r"D:\tw\cache",
    "minimum_free_gib": 20,
}


class WindowsRealTransportTests(unittest.TestCase):
    def test_encoded_command_uses_target_absolute_powershell(self):
        encoded = base64.b64encode("$env:PROCESSOR_ARCHITECTURE".encode("utf-16le")).decode("ascii")
        argv = windows_ssh.powershell_argv("windows-direct", POWERSHELL, "$env:PROCESSOR_ARCHITECTURE")
        self.assertEqual(argv[:6], [
            "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "windows-direct",
        ])
        expected = subprocess.list2cmdline([
            POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
        ])
        self.assertEqual(argv[6], expected)
        self.assertEqual(base64.b64decode(encoded).decode("utf-16le"), "$env:PROCESSOR_ARCHITECTURE")

    def test_builder_doctor_never_reads_product_repo(self):
        script = windows_ssh.builder_probe_script(TARGET)
        self.assertNotIn(r"D:\tw\source\taijiAgentv1.0", script)
        self.assertNotIn("git bundle", script)

    def test_builder_probe_reads_filesystem_format_from_drive_info(self):
        script = windows_ssh.builder_probe_script(TARGET)
        self.assertIn("DriveInfo", script)
        self.assertIn("DriveFormat", script)

    def test_builder_probe_contains_full_online_identity_contract(self):
        script = windows_ssh.builder_probe_script(TARGET)
        for field in (
            "cache_requirements_sha256",
            "cache_observation",
            "cache_observation_sha256",
            "host_facts",
            "host_facts_sha256",
            "observed_at",
        ):
            self.assertIn(field, script)
        self.assertNotIn("D:\\tw\\source\\taijiAgentv1.0", script)
        self.assertNotIn("New-Item", script)

    def test_product_probe_never_checks_or_mutates_builder_run(self):
        script = windows_ssh.product_probe_script(
            r"D:\tw\source\taijiAgentv1.0",
            "codex/windows-local",
            "89954e96d23cf43f266197813eb283475d5ff7e1",
            "5364233e1297e5f2837382823d4e35a0d114aba7",
        )
        self.assertNotIn(r"D:\tw\taiji-builds", script)
        for forbidden in ("New-Item", "Set-Content", "Remove-Item", "git bundle create"):
            self.assertNotIn(forbidden, script)

    def test_cache_missing_is_parsed_without_build(self):
        payload = json.dumps({
            "schema": "taiji-windows-builder-doctor/v1",
            "architecture": "AMD64",
            "filesystem": "NTFS",
            "free_bytes": 30 * 1024 * 1024 * 1024,
            "cache_checks": [{"name": "electron", "present": False}],
        })
        result = windows_ssh.parse_builder_probe(payload)
        self.assertEqual(result["builder_status"], "BLOCKED")
        self.assertEqual(result["failure_categories"], ["WINDOWS_CACHE_MISSING"])

    def test_parse_builder_probe_rejects_observation_hash_drift(self):
        requirements = json.loads(
            windows_ssh.CACHE_REQUIREMENTS_PATH.read_text(encoding="utf-8")
        )
        requirements_sha = canonical_json_sha256(requirements)
        observation = {
            "schema": "taiji-windows-cache-observation/v1",
            "target_id": "windows-x64",
            "requirements_sha256": requirements_sha,
            "cache_root": r"D:\tw\cache",
            "entries": [],
            "observed_at": "2026-08-20T12:00:00.000Z",
        }
        host_facts = {
            "schema": "taiji-windows-host-facts/v1",
            "host_alias": "WIN-TEST",
            "os": "Windows",
            "os_version": "10.0",
            "architecture": "AMD64",
            "filesystem": "NTFS",
            "powershell_version": "5.1",
        }
        payload = {
            "schema": "taiji-package-online-doctor/v2",
            "builder_status": "BLOCKED",
            "host_alias": "WIN-TEST",
            "os": "Windows",
            "os_version": "10.0",
            "architecture": "AMD64",
            "powershell_version": "5.1",
            "git_path": r"C:\git.exe",
            "tar_path": r"C:\tar.exe",
            "node_path": r"C:\node.exe",
            "npm_path": r"C:\npm.cmd",
            "python_path": r"D:\python.exe",
            "iscc_path": r"C:\iscc.exe",
            "filesystem": "NTFS",
            "free_bytes": 30 * 1024 * 1024 * 1024,
            "cache_root": r"D:\tw\cache",
            "cache_checks": [],
            "cache_requirements_sha256": requirements_sha,
            "cache_observation": observation,
            "cache_observation_sha256": "f" * 64,
            "host_facts": host_facts,
            "host_facts_sha256": canonical_json_sha256(host_facts),
            "remote_root_parent_exists": True,
            "blockers": ["WINDOWS_CACHE_MISSING"],
            "failure_categories": ["WINDOWS_CACHE_MISSING"],
        }
        with self.assertRaises(PipelineError) as context:
            windows_ssh.parse_builder_probe(json.dumps(payload))
        self.assertEqual(context.exception.category, "ONLINE_DOCTOR_BLOCKED")

    def test_transport_uses_injected_runner_without_external_call(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "schema": "taiji-windows-builder-doctor/v1",
                "architecture": "AMD64",
                "filesystem": "NTFS",
                "free_bytes": 30 * 1024 * 1024 * 1024,
                "cache_checks": [],
                "blockers": [],
            }), "")

        result = windows_ssh.WindowsSshTransport(
            TARGET, ssh_config=None, command_runner=runner
        ).online_doctor()
        self.assertEqual(result["builder_status"], "BUILDER_READY")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/usr/bin/ssh")

    def test_real_runner_keeps_windows_stderr_as_bytes(self):
        payload = json.dumps({
            "schema": "taiji-windows-builder-doctor/v1",
            "architecture": "AMD64",
            "filesystem": "NTFS",
            "free_bytes": 30 * 1024 * 1024 * 1024,
            "cache_checks": [],
            "blockers": [],
        }).encode("ascii")
        completed = subprocess.CompletedProcess(
            ["/usr/bin/ssh"], 0, stdout=payload, stderr=b"\xd5\xce Windows warning"
        )
        with mock.patch.object(
            windows_ssh.subprocess, "run", return_value=completed
        ) as run:
            result = windows_ssh.WindowsSshTransport(
                TARGET, ssh_config=None, command_runner=None
            ).online_doctor()
        self.assertEqual(result["builder_status"], "BUILDER_READY")
        self.assertFalse(run.call_args.kwargs["text"])

    def test_long_powershell_probe_uses_stdin_command_boundary(self):
        calls = {}

        def runner(argv, input=None):
            calls["argv"] = argv
            calls["input"] = input
            return subprocess.CompletedProcess(argv, 0, "{}", b"")

        transport = windows_ssh.WindowsSshTransport(
            TARGET, ssh_config=None, command_runner=runner
        )
        transport._run_powershell("Write-Output '{}'\n" + ("x" * 6000))
        self.assertTrue(calls["argv"][-1].endswith("-Command -"))
        self.assertTrue(calls["input"].endswith(b"\nWrite-Output ''\n"))


if __name__ == "__main__":
    unittest.main()
