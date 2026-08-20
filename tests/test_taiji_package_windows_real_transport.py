"""Local contracts for the gated Windows SSH transport."""

import base64
import json
import subprocess
import unittest

from packaging.pipeline.adapters import windows_ssh


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


if __name__ == "__main__":
    unittest.main()
