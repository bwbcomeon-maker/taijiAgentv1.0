"""Local contracts for the gated Windows SSH transport."""

import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from packaging.pipeline.adapters import windows_ssh
from packaging.pipeline.core.errors import PipelineError
from packaging.pipeline.core.models import (
    canonical_json_sha256,
    new_run_state,
    validate_v2_state,
)
from packaging.pipeline.core.state import RunStateStore, recorded_stage
from tests import windows_pipeline_fixtures


POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
TARGET = {
    "host_alias": "windows-direct",
    "powershell": POWERSHELL,
    "remote_root": r"D:\tw\taiji-builds",
    "cache_root": r"D:\tw\cache",
    "minimum_free_gib": 20,
}


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, b"{}", b"")


class FailOnCallRunner:
    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        returncode = 1 if len(self.calls) == self.fail_on else 0
        return subprocess.CompletedProcess(argv, returncode, b"{}", b"injected failure")


def _decode_stdin_command(call):
    argv, kwargs = call
    if "-Command -" not in argv[6]:
        raise AssertionError("runner call does not use PowerShell stdin")
    return kwargs["input"].decode("utf-8")


def _decode_loader(argv):
    remote = argv[6]
    marker = "-EncodedCommand "
    index = remote.index(marker) + len(marker)
    encoded = remote[index:].strip().strip('"')
    return base64.b64decode(encoded).decode("utf-16le")


def _write_canonical_state(plan, *, stage, remote_build_succeeded, fetch_allowed):
    run_dir = Path(plan["local_run_dir"])
    state_root = run_dir.parent.parent
    runs_root = run_dir.parent
    for directory in (state_root, runs_root, run_dir):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    run_dir.chmod(0o700)

    class StateAdapter:
        not_built_label = "Windows adapter 已实现，真实 Windows 未验证，候选 EXE 未构建"

        @staticmethod
        def initial_state_patch(frozen_plan, online):
            del online
            return {
                "identity": {
                    "asset_provenance_sha256": frozen_plan["asset_provenance_sha256"],
                    "cache_requirements_sha256": frozen_plan["cache_requirements_sha256"],
                    "cache_observation_sha256": frozen_plan["cache_observation_sha256"],
                },
                "policy": None,
            }

    state = new_run_state(
        plan,
        {"host_facts_sha256": plan["host_facts_sha256"]},
        StateAdapter(),
    )
    state.update({
        "stage": stage,
        "status_label": "Windows remote build succeeded; fetch is recoverable",
        "remote_build_succeeded": remote_build_succeeded,
        "fetch_allowed": fetch_allowed,
    })
    validate_v2_state(state)
    path = run_dir / "run-state.json"
    path.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


class WindowsRealTransportTests(unittest.TestCase):
    def test_runner_type_error_is_not_swallowed_and_retried_without_required_kwargs(self):
        calls = []

        def failing_runner(argv, *, cwd, environment=None, timeout=10):
            calls.append((list(argv), cwd, environment, timeout))
            raise TypeError("injected runner body failure")

        with self.assertRaisesRegex(TypeError, "injected runner body failure"):
            windows_ssh._invoke_runner(failing_runner, ["command"])
        self.assertEqual(len(calls), 1)

    def test_windows_runner_requests_binary_output_from_unified_command_runner(self):
        observed = {}

        def binary_runner(
            argv, *, cwd, environment=None, timeout=10, text=True, input=None
        ):
            observed.update(
                argv=list(argv), cwd=cwd, environment=environment,
                timeout=timeout, text=text, input=input,
            )
            return subprocess.CompletedProcess(argv, 0, b"{}", b"\xc3")

        result = windows_ssh._invoke_runner(binary_runner, ["command"])
        self.assertEqual(result.returncode, 0)
        self.assertFalse(observed["text"])
        self.assertEqual(observed["timeout"], 3600)

    def test_encoded_command_uses_target_absolute_powershell(self):
        argv = windows_ssh.powershell_argv("windows-direct", POWERSHELL, "$env:PROCESSOR_ARCHITECTURE")
        self.assertEqual(argv[:6], [
            "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "windows-direct",
        ])
        self.assertIn(POWERSHELL, argv[6])
        loader = _decode_loader(argv)
        self.assertIn("System.IO.Compression.GzipStream", loader)
        self.assertIn("EncodedCommand", argv[6])
        self.assertNotIn("Start-Process", loader)

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

    def test_builder_probe_normalizes_zip_backslashes_before_identity_checks(self):
        script = windows_ssh.builder_probe_script(TARGET)
        self.assertIn("function Normalize-ZipMemberName", script)
        self.assertIn("return $Name.Replace('\\', '/')", script)
        self.assertNotIn("Normalize([System.Text.NormalizationForm]::FormC).Replace('\\', '/')", script)
        normalize_index = script.index("function Normalize-ZipMemberName")
        safe_index = script.index("function Test-SafeZipMemberName")
        self.assertLess(normalize_index, safe_index)
        self.assertIn("$normalizedName = Normalize-ZipMemberName $zipEntry.FullName", script)
        self.assertIn("$requiredNormalized = Normalize-ZipMemberName ([string]$requiredMember)", script)
        self.assertIn("Get-PathIdentity $normalizedName", script)
        self.assertIn("$Path.Normalize([System.Text.NormalizationForm]::FormC) -cne $Path", script)

    def test_builder_probe_hashes_fresh_non_seekable_zip_member_stream(self):
        script = windows_ssh.builder_probe_script(TARGET)
        self.assertIn("if ($Stream.CanSeek) { $Stream.Position = 0 }", script)

    def test_builder_probe_keeps_zip_members_as_a_flat_list(self):
        script = windows_ssh.builder_probe_script(TARGET)
        self.assertIn("$entry.members = Sort-MembersByUtf8 $zipMembers", script)
        self.assertNotIn("$entry.members = @(Sort-MembersByUtf8 $zipMembers)", script)

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

    def test_long_powershell_probe_uses_compressed_loader_with_safe_length(self):
        script = "Write-Output 'probe'\n" + ("x" * 24000)
        argv = windows_ssh.powershell_argv("windows-direct", POWERSHELL, script)
        loader = _decode_loader(argv)
        self.assertLess(len(argv[6]), 32767)
        self.assertIn("System.IO.Compression.GzipStream", loader)
        self.assertIn("IO.MemoryStream", loader)
        self.assertNotIn("cmd.exe", loader)
        self.assertNotIn("Start-Process", loader)

    def test_read_only_probe_uses_short_stdin_command_below_windows_cmd_limit(self):
        observed = {}

        def runner(argv, **kwargs):
            observed.update(argv=list(argv), kwargs=kwargs)
            return subprocess.CompletedProcess(argv, 0, b"{}", b"")

        transport = windows_ssh.WindowsSshTransport(
            TARGET, ssh_config=None, command_runner=runner
        )
        script = "Write-Output 'probe'\n" + ("x" * 24000)
        transport._run_powershell(script)
        self.assertLess(len(observed["argv"][6]), 8191)
        self.assertIn("-Command -", observed["argv"][6])
        self.assertIn(script.encode("utf-8"), observed["kwargs"]["input"])

    def test_online_doctor_uses_nlogn_utf8_member_sort(self):
        script = windows_ssh.builder_probe_script(TARGET)
        self.assertIn("utf8_path = [System.Text.Encoding]::UTF8.GetBytes", script)
        self.assertIn("$decorated.Sort($comparison)", script)
        self.assertNotIn("$sorted.Insert", script)

    def test_remote_stage_uses_short_stdin_command_below_windows_cmd_limit(self):
        observed = {}

        def runner(argv, **kwargs):
            observed.update(argv=list(argv), kwargs=kwargs)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        transport = windows_ssh.WindowsSshTransport(
            TARGET, ssh_config=None, command_runner=runner
        )
        script = "Write-Output 'stage'\n" + ("x" * 24000)
        transport._run_remote_stage(script, "INPUT_VERIFICATION_FAILED")
        self.assertLess(len(observed["argv"][6]), 8191)
        self.assertIn("-ExecutionPolicy Bypass", observed["argv"][6])
        self.assertIn("-Command -", observed["argv"][6])
        self.assertIn(script.encode("utf-8"), observed["kwargs"]["input"])

    def test_real_stage_contracts_are_exact_and_fetch_pending_is_fetch_only(self):
        self.assertEqual(
            windows_ssh.REAL_BUILD_STAGES,
            [
                "online-doctor",
                "create-remote-run",
                "transfer-input",
                "remote-input-verify",
                "remote-candidate-build",
                "fetch-review",
                "fetch-log",
                "local-review-verify",
                "publish",
            ],
        )
        self.assertEqual(
            windows_ssh.REAL_FETCH_STAGES,
            [
                "fetch-review",
                "fetch-log",
                "local-review-verify",
                "publish",
            ],
        )

    def test_transport_exposes_five_real_execution_methods_and_no_legacy_build_method(self):
        transport = windows_ssh.WindowsSshTransport(
            TARGET, ssh_config=None, command_runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b"{}", b"")
        )
        for name in (
            "create_remote_run",
            "transfer_input",
            "verify_remote_input",
            "build_remote_candidate",
            "fetch",
        ):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(transport, name, None)))
        self.assertFalse(hasattr(transport, "build"))

    def test_create_remote_run_fails_when_root_already_exists(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-create-run-") as temporary:
            runner = RecordingRunner()
            transport = windows_ssh.WindowsSshTransport(
                TARGET, ssh_config=None, command_runner=runner
            )
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                Path(temporary) / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            transport.create_remote_run(plan)
        script = _decode_stdin_command(runner.calls[0])
        self.assertIn("REMOTE_RUN_CONFLICT", script)
        self.assertIn("Test-Path -LiteralPath", script)
        self.assertEqual(script.count("New-Item -ItemType Directory"), 6)
        self.assertLess(script.index("REMOTE_RUN_CONFLICT"), script.index("New-Item -ItemType Directory"))

    def test_transfer_uses_finalized_safe_tar_and_transfers_taijiagent_iss(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-transfer-") as temporary:
            root = Path(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                root / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            runner = RecordingRunner()
            transport = windows_ssh.WindowsSshTransport(
                TARGET, ssh_config=None, command_runner=runner
            )
            transport.create_remote_run(plan)
            transport.transfer_input(plan)
        scp_calls = [argv for argv, _kwargs in runner.calls if argv[0] == "/usr/bin/scp"]
        transfers = {(call[-2], call[-1]) for call in scp_calls}
        self.assertIn(
            (
                str(safe_tar),
                "windows-direct:" + plan["remote_run_dir"].replace("\\", "/") + "/input/controller-safe-tar.py",
            ),
            transfers,
        )
        taiji_iss_transfers = [
            pair for pair in transfers
            if pair[1] == "windows-direct:" + plan["remote_run_dir"].replace("\\", "/") + "/scripts/TaijiAgent.iss"
        ]
        self.assertEqual(len(taiji_iss_transfers), 1)
        self.assertTrue(taiji_iss_transfers[0][0].endswith("TaijiAgent.iss"))
        self.assertFalse(any(call[-1].endswith(r"safe-tar.exe") for call in scp_calls))
        self.assertTrue(all("\\" not in call[-1].split(":", 1)[1] for call in scp_calls))

    def test_build_stage_and_inno_failures_keep_distinct_categories(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-build-categories-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                Path(temporary) / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            for fail_on, expected in ((1, "WINDOWS_PAYLOAD_FAILED"), (2, "WINDOWS_INNO_FAILED")):
                with self.subTest(fail_on=fail_on):
                    runner = FailOnCallRunner(fail_on)
                    transport = windows_ssh.WindowsSshTransport(
                        plan["target_config"], ssh_config=None, command_runner=runner
                    )
                    with self.assertRaises(PipelineError) as context:
                        transport.build_remote_candidate(plan)
                    self.assertEqual(context.exception.category, expected)
                    self.assertEqual(len(runner.calls), fail_on)

    def test_stage_and_inno_persist_execution_results_before_returning(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-stage-results-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                Path(temporary) / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            runner = RecordingRunner()
            transport = windows_ssh.WindowsSshTransport(
                plan["target_config"], ssh_config=None, command_runner=runner
            )
            transport.build_remote_candidate(plan)

        self.assertEqual(len(runner.calls), 2)
        stage_script = _decode_stdin_command(runner.calls[0])
        inno_script = _decode_stdin_command(runner.calls[1])
        for script, prefix, failure_stage in (
            (stage_script, "payload", "WINDOWS_PAYLOAD_FAILED"),
            (inno_script, "inno", "WINDOWS_INNO_FAILED"),
        ):
            with self.subTest(prefix=prefix):
                for literal in (
                    "{}.stdout.log".format(prefix),
                    "{}.stderr.log".format(prefix),
                    "{}-result.json".format(prefix),
                    "started_at",
                    "finished_at",
                    "exit_code",
                    "failure_stage",
                    "status = 'RUNNING'",
                    "status = 'PASS'",
                    "status = 'FAIL'",
                    failure_stage,
                    "[Text.UTF8Encoding]::new($false)",
                    "[IO.File]::AppendAllText",
                ):
                    self.assertIn(literal, script)
                self.assertLess(
                    script.index("status = 'RUNNING'"),
                    script.index("-SessionPath"),
                )

    def test_scp_retry_exhaustion_has_stable_category(self):
        runner = FailOnCallRunner(1)

        def always_fail(argv, **kwargs):
            runner.calls.append((list(argv), dict(kwargs)))
            return subprocess.CompletedProcess(argv, 1, b"", b"injected scp failure")

        transport = windows_ssh.WindowsSshTransport(
            TARGET, ssh_config=None, command_runner=always_fail
        )
        with self.assertRaises(PipelineError) as context:
            transport._run_scp("local", "windows-direct:D:/remote")
        self.assertEqual(context.exception.category, "SCP_INTERRUPTED")
        self.assertEqual(len(runner.calls), 2)

    def test_verify_remote_input_extracts_with_plan_python_before_initialize(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-verify-") as temporary:
            root = Path(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                root / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            runner = RecordingRunner()
            transport = windows_ssh.WindowsSshTransport(
                TARGET, ssh_config=None, command_runner=runner
            )
            transport.create_remote_run(plan)
            transport.transfer_input(plan)
            transport.verify_remote_input(plan)
        script = _decode_stdin_command(runner.calls[-1])
        checkout = plan["remote_run_dir"] + r"\source\checkout"
        self.assertIn(r"\input\controller-safe-tar.py", script)
        self.assertIn(plan["target_config"]["python"], script)
        self.assertIn("cache-observation.json", script)
        self.assertIn("host-facts-sha256.txt", script)
        self.assertIn(checkout, script)
        self.assertIn("-I -B", script)
        self.assertIn("controller safe tar destination must not exist before extract", script)
        self.assertNotIn("New-Item -ItemType Directory -Path '{}'".format(checkout), script)
        extract_index = script.index("controller-safe-tar.py")
        initialize_index = script.index("Initialize-CandidateSession.ps1")
        self.assertLess(extract_index, initialize_index)
        self.assertIn("--archive", script)
        self.assertIn("--destination", script)
        self.assertIn("--manifest", script)
        self.assertIn("-SourceRoot '\\\\?\\{}'".format(checkout), script)
        files = plan["input"]["files"]
        expected_sidecar = "{}  {}\n{}  {}\n".format(
            files["archive"]["sha256"],
            files["archive"]["basename"],
            files["manifest"]["sha256"],
            files["manifest"]["basename"],
        )
        self.assertIn(expected_sidecar, script)
        observation_index = script.index("cache observation identity drifted before extract")
        safe_extract_index = script.index(" -I -B ", observation_index)
        self.assertLess(observation_index, safe_extract_index)

    def test_fetch_uses_persisted_run_state_gate_and_allows_fresh_transport_recovery(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-fetch-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                Path(temporary) / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            for stage in ("REMOTE_BUILD_SUCCEEDED", "FETCH_PENDING"):
                with self.subTest(stage=stage):
                    _write_canonical_state(
                        plan,
                        stage=stage,
                        remote_build_succeeded=True,
                        fetch_allowed=True,
                    )
                    staging_dir = Path(temporary) / ("staging-" + stage.lower())
                    runner = RecordingRunner()
                    transport = windows_ssh.WindowsSshTransport(
                        plan["target_config"], ssh_config=None, command_runner=runner
                    )
                    result = transport.fetch(plan, staging_dir)
                    scp_calls = [argv for argv, _kwargs in runner.calls if argv[0] == "/usr/bin/scp"]
                    self.assertEqual(result["review_path"], str((staging_dir / "review").resolve()))
                    self.assertEqual(result["remote_log_path"], str((staging_dir / "remote-build.log").resolve()))
                    self.assertEqual(len(scp_calls), 2)
                    self.assertIn("-r", scp_calls[0])
                    self.assertNotIn("-r", scp_calls[1])
                    self.assertTrue(scp_calls[0][-1].endswith(str((staging_dir / "review").resolve())))

    def test_recorded_review_fetch_stage_allows_transport_to_reach_scp(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-recorded-fetch-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                Path(temporary) / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            _write_canonical_state(
                plan,
                stage="FETCH_PENDING",
                remote_build_succeeded=True,
                fetch_allowed=True,
            )
            store = RunStateStore(Path(plan["local_run_dir"]).parent.parent)
            runner = RecordingRunner()
            transport = windows_ssh.WindowsSshTransport(
                plan["target_config"], ssh_config=None, command_runner=runner
            )
            staging = Path(temporary) / "recorded-fetch"
            recorded_stage(
                store,
                plan["run_id"],
                "REVIEW_FETCHED",
                lambda: transport.fetch(plan, staging),
            )
            scp_calls = [argv for argv, _kwargs in runner.calls if argv[0] == "/usr/bin/scp"]
            self.assertEqual(len(scp_calls), 2)

    def test_fetch_rejects_missing_or_drifted_persisted_state(self):
        with tempfile.TemporaryDirectory(prefix="windows-transport-fetch-reject-") as temporary:
            plan = windows_pipeline_fixtures.make_windows_plan(temporary)
            safe_tar = windows_pipeline_fixtures.write_regular(
                Path(temporary) / "controller-safe-tar.py",
                b"print('safe tar helper')\n",
            )
            plan["controller_bootstrap"] = {
                "safe_tar": {
                    "source_path": str(safe_tar),
                    "remote_path": r"input\controller-safe-tar.py",
                    "bytes": safe_tar.stat().st_size,
                    "sha256": windows_pipeline_fixtures.sha256_bytes(safe_tar.read_bytes()),
                    "python_path": plan["target_config"]["python"],
                }
            }
            transport = windows_ssh.WindowsSshTransport(
                plan["target_config"], ssh_config=None, command_runner=RecordingRunner()
            )
            with self.assertRaises(PipelineError) as missing:
                transport.fetch(plan, Path(temporary) / "missing-state")
            self.assertEqual(missing.exception.category, "FETCH_NOT_ALLOWED")
            incomplete_path = _write_canonical_state(
                plan,
                stage="FETCH_PENDING",
                remote_build_succeeded=True,
                fetch_allowed=True,
            )
            incomplete = json.loads(incomplete_path.read_text(encoding="utf-8"))
            incomplete.pop("identity")
            incomplete_path.write_text(
                json.dumps(incomplete, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            incomplete_path.chmod(0o600)
            with self.assertRaises(PipelineError) as invalid_v2:
                transport.fetch(plan, Path(temporary) / "incomplete-state")
            self.assertEqual(invalid_v2.exception.category, "FETCH_NOT_ALLOWED")
            state_path = _write_canonical_state(
                plan,
                stage="FETCH_PENDING",
                remote_build_succeeded=True,
                fetch_allowed=True,
            )
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["run_id"] = "wrong-run"
            state_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            state_path.chmod(0o600)
            with self.assertRaises(PipelineError) as drifted:
                transport.fetch(plan, Path(temporary) / "drifted-state")
            self.assertEqual(drifted.exception.category, "FETCH_NOT_ALLOWED")
            _write_canonical_state(
                plan,
                stage="FETCH_PENDING",
                remote_build_succeeded=True,
                fetch_allowed=True,
            )
            existing = Path(temporary) / "existing-staging"
            existing.mkdir()
            with self.assertRaises(PipelineError) as occupied:
                transport.fetch(plan, existing)
            self.assertEqual(occupied.exception.category, "LOCAL_OUTPUT_OCCUPIED")


if __name__ == "__main__":
    unittest.main()
