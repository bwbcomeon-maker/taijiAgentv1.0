"""Transport contract tests for the x86 Kylin candidate pipeline."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_taiji_package_candidate import (
    TARGET,
    implemented_result,
    make_doctor_repo,
    write_ssh_config,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "scripts/taiji-package-candidate.py"


def load_candidate():
    spec = importlib.util.spec_from_file_location("taiji_package_transport", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Taiji candidate transport")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, *, cwd, environment, timeout):
        call = {
            "argv": list(argv),
            "cwd": str(cwd),
            "environment": dict(environment),
            "timeout": timeout,
        }
        self.calls.append(call)
        stdout = ""
        if argv[0] == "/usr/bin/ssh" and "taiji-online-doctor-v1" in argv[-1]:
            stdout = "\n".join(
                [
                    "schema=taiji-online-doctor-v1",
                    "kernel=Linux",
                    "machine=x86_64",
                    "dpkg_arch=amd64",
                    "apt=/usr/bin/apt-get",
                    "dpkg=/usr/bin/dpkg",
                    "glibc=ldd (GNU libc) 2.31",
                    "sudo=ready",
                    "free_kib=20971520",
                    "free_inodes=200000",
                    "proc=ready",
                    "memfd=ready",
                    "remote_root=ready",
                ]
            ) + "\n"
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")


def make_plan(module, root: Path):
    repo = make_doctor_repo(root)
    target = module.load_target(TARGET)
    ssh_config = write_ssh_config(root / "ssh_config")
    plan = module.build_candidate_plan(
        repo,
        target,
        root / "state",
        run_id="transport-run",
        ssh_config=ssh_config,
    )
    return repo, target, ssh_config, plan


class CandidateTransportContractTests(unittest.TestCase):
    def test_real_ssh_transport_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(hasattr(module, "RealSshTransport"))

    def test_fake_ssh_transport_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(hasattr(module, "FakeSshTransport"))

    def test_real_transport_uses_fixed_argv_and_parses_online_capabilities(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-real-transport-") as temporary:
            root = Path(temporary)
            repo, target, ssh_config, plan = make_plan(module, root)
            runner = RecordingRunner()
            transport = implemented_result(
                lambda: module.RealSshTransport(
                    repo, target, ssh_config=ssh_config, command_runner=runner
                )
            )

            online = transport.online_doctor()
            transport.create_remote_run(plan)
            transport.transfer_input(plan)
            transport.verify_remote_input(plan)
            transport.build_remote_candidate(plan)
            transport.fetch(plan, root / "fetch-staging")

            self.assertEqual(online["builder_status"], "BUILDER_READY")
            self.assertTrue(online["online_checked"])
            self.assertEqual(online["architecture"], "amd64")
            self.assertGreaterEqual(online["free_kib"], 12 * 1024 * 1024)
            self.assertGreaterEqual(online["free_inodes"], 100000)
            self.assertEqual(len(runner.calls), 7)
            for call in runner.calls:
                self.assertIsInstance(call["argv"], list)
                self.assertIn(call["argv"][0], {"/usr/bin/ssh", "/usr/bin/scp"})
                self.assertNotIn("shell", call)
            ssh_payloads = [
                call["argv"][-1]
                for call in runner.calls
                if call["argv"][0] == "/usr/bin/ssh"
            ]
            self.assertTrue(any("/bin/bash -p" in payload for payload in ssh_payloads))
            self.assertTrue(any("/usr/bin/python3 -I -B" in payload for payload in ssh_payloads))
            rendered = json.dumps(runner.calls, ensure_ascii=False)
            for forbidden in (
                "02_目标终端_安装并验证.sh",
                "04_目标终端_桌面App验收并导出证据.sh",
                "sign-taiji",
                "publish-single-deb",
            ):
                self.assertNotIn(forbidden, rendered)

    def test_fake_transport_runs_complete_candidate_chain_without_external_commands(self):
        module = load_candidate()
        self.assertTrue(
            hasattr(module, "execute_candidate_transport"),
            "candidate transport executor is missing",
        )
        with tempfile.TemporaryDirectory(prefix="taiji-fake-transport-") as temporary:
            root = Path(temporary)
            _repo, _target, _ssh_config, plan = make_plan(module, root)
            prepared = []
            transport = module.FakeSshTransport()

            result = module.execute_candidate_transport(
                plan,
                transport,
                root / "fetch-staging",
                confirmed=True,
                prepare_input=lambda: prepared.append("99"),
            )

            self.assertEqual(prepared, ["99"])
            self.assertEqual(
                transport.calls,
                [
                    "online-doctor",
                    "create-remote-run",
                    "transfer-input",
                    "remote-input-verify",
                    "remote-candidate-build",
                    "fetch-review",
                    "fetch-log",
                ],
            )
            self.assertTrue(result["remote_build_succeeded"])
            self.assertTrue(Path(result["review_path"]).is_dir())
            self.assertTrue(Path(result["remote_log_path"]).is_file())

    def test_builder_unreachable_stops_before_confirmation_input_or_transport(self):
        module = load_candidate()
        self.assertTrue(
            hasattr(module, "execute_candidate_transport"),
            "candidate transport executor is missing",
        )
        with tempfile.TemporaryDirectory(prefix="taiji-unreachable-") as temporary:
            root = Path(temporary)
            _repo, _target, _ssh_config, plan = make_plan(module, root)
            prepared = []
            transport = module.FakeSshTransport(builder_status="BUILDER_UNREACHABLE")

            with self.assertRaises(module.PipelineError) as raised:
                module.execute_candidate_transport(
                    plan,
                    transport,
                    root / "fetch-staging",
                    confirmed=True,
                    prepare_input=lambda: prepared.append("99"),
                )

            self.assertEqual(raised.exception.category, "BUILDER_UNREACHABLE")
            self.assertEqual(prepared, [])
            self.assertEqual(transport.calls, ["online-doctor"])
            self.assertFalse((root / "fetch-staging").exists())

    def test_fake_transport_classifies_each_remote_failure(self):
        module = load_candidate()
        self.assertTrue(
            hasattr(module, "execute_candidate_transport"),
            "candidate transport executor is missing",
        )
        expected = {
            "create-remote-run": "SSH_FAILED",
            "transfer-input": "SCP_INTERRUPTED",
            "remote-input-verify": "REMOTE_VERIFY_FAILED",
            "build-00": "BUILD_00_FAILED",
            "build-01": "BUILD_01_FAILED",
            "fetch-review": "SCP_INTERRUPTED",
            "fetch-log": "SCP_INTERRUPTED",
        }
        with tempfile.TemporaryDirectory(prefix="taiji-fake-failures-") as temporary:
            root = Path(temporary)
            _repo, _target, _ssh_config, plan = make_plan(module, root)
            plan["input"]["status"] = "REUSABLE"
            plan["input"]["prepare_required"] = False
            for stage, category in expected.items():
                with self.subTest(stage=stage):
                    transport = module.FakeSshTransport(fail_stage=stage)
                    with self.assertRaises(module.PipelineError) as raised:
                        module.execute_candidate_transport(
                            plan,
                            transport,
                            root / ("fetch-" + stage),
                            confirmed=True,
                        )
                    self.assertEqual(raised.exception.category, category)

    def test_candidate_transport_requires_one_explicit_confirmation(self):
        module = load_candidate()
        self.assertTrue(
            hasattr(module, "execute_candidate_transport"),
            "candidate transport executor is missing",
        )
        with tempfile.TemporaryDirectory(prefix="taiji-confirmation-") as temporary:
            root = Path(temporary)
            _repo, _target, _ssh_config, plan = make_plan(module, root)
            transport = module.FakeSshTransport()

            with self.assertRaises(module.PipelineError) as raised:
                module.execute_candidate_transport(
                    plan, transport, root / "fetch-staging", confirmed=False
                )

            self.assertEqual(raised.exception.category, "CONFIRMATION_REQUIRED")
            self.assertEqual(transport.calls, ["online-doctor"])


if __name__ == "__main__":
    unittest.main()
