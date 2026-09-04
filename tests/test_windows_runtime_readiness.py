"""Runtime preflight must reject unusable tools before scanning shared caches."""

import json
import unittest
from unittest import mock

from packaging.pipeline.adapters import windows_ssh
from packaging.pipeline.core.errors import PipelineError


def ready_runtime():
    return {
        "schema": "taiji-windows-runtime-probe/v1",
        "node": {"exit_code": 0, "output": "v22.23.1,x64"},
        "npm": {"exit_code": 0, "output": "10.9.4"},
        "python": {"exit_code": 0, "output": json.dumps({
            "version": [3, 11, 9], "bits": 64,
            "imports": {name: True for name in (
                "aiohttp", "fastapi", "uvicorn", "yaml", "cryptography", "psutil", "pypdf"
            )},
        })},
        "iscc": {"exit_code": 0, "output": "Inno Setup 6 Command-Line Compiler"},
    }


class WindowsRuntimeReadinessTests(unittest.TestCase):
    def test_supported_runtime_is_accepted(self):
        self.assertEqual(windows_ssh.parse_runtime_probe(json.dumps(ready_runtime())), ready_runtime())

    def test_inno_help_exit_one_with_expected_banner_is_not_a_compile_failure(self):
        payload = ready_runtime()
        payload["iscc"] = {"exit_code": 1, "output": "Inno Setup 6 Command-Line Compiler\nUsage:  iscc [options] scriptfile.iss"}
        self.assertEqual(windows_ssh.parse_runtime_probe(json.dumps(payload)), payload)

    def test_unsupported_node_version_or_architecture_is_rejected(self):
        for output in ("v20.20.0,x64", "v26.0.0,x64", "v22.23.1,arm64", "", "v22.23.1,x64\nnoise"):
            payload = ready_runtime()
            payload["node"]["output"] = output
            with self.subTest(output=output), self.assertRaises(PipelineError) as raised:
                windows_ssh.parse_runtime_probe(json.dumps(payload))
            self.assertEqual(raised.exception.category, "WINDOWS_RUNTIME_NOT_READY")

    def test_python_version_bitness_and_actual_imports_are_required(self):
        for update in ({"version": [3, 10, 9]}, {"bits": 32}, {"imports": {}}, {"imports": {"pypdf": False}}):
            payload = ready_runtime()
            facts = json.loads(payload["python"]["output"])
            facts.update(update)
            payload["python"]["output"] = json.dumps(facts)
            with self.subTest(update=update), self.assertRaises(PipelineError):
                windows_ssh.parse_runtime_probe(json.dumps(payload))

    def test_missing_malformed_and_failed_tools_fail_closed(self):
        for tool in ("node", "npm", "python", "iscc"):
            for mutation in ({"exit_code": 1}, {"exit_code": None}, {"output": ""}):
                payload = ready_runtime()
                payload[tool].update(mutation)
                with self.subTest(tool=tool, mutation=mutation), self.assertRaises(PipelineError):
                    windows_ssh.parse_runtime_probe(json.dumps(payload))
        for payload in ({}, [], {**ready_runtime(), "extra": True}):
            with self.assertRaises(PipelineError):
                windows_ssh.parse_runtime_probe(json.dumps(payload))

    def test_runtime_failure_stops_before_cache_scan(self):
        payload = ready_runtime()
        payload["node"]["output"] = "v20.20.0,x64"
        target = {"host_alias": "fake", "powershell": r"C:\powershell.exe", "minimum_free_gib": 20}
        transport = windows_ssh.WindowsSshTransport(target, ssh_config=None, command_runner=None)
        with mock.patch.object(transport, "_run_powershell", return_value=json.dumps(payload)) as run:
            with self.assertRaises(PipelineError) as raised:
                transport.online_doctor()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(raised.exception.category, "WINDOWS_RUNTIME_NOT_READY")

    def test_probe_uses_pinned_paths_and_does_not_install_or_write(self):
        target = {name: "D:\\isolated tools\\" + name for name in ("node", "npm", "python", "iscc")}
        script = windows_ssh.runtime_probe_script(target)
        for value in target.values():
            self.assertIn(value, script)
        self.assertIn("-I", script)
        self.assertIn("-B", script)
        self.assertIn("b64decode", script)
        self.assertIn("$global:LASTEXITCODE = $null", script)
        self.assertIn("$code = $global:LASTEXITCODE", script)
        for forbidden in ("New-Item", "Set-Content", "Invoke-WebRequest", "pip install", "npm install"):
            self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
