"""Transport contract tests for the x86 Kylin candidate pipeline."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_taiji_package_candidate import (
    TARGET,
    implemented_result,
    make_doctor_repo,
    write_ssh_config,
)
from tests.taiji_package_fixtures import complete_v2_payload


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
    def __init__(self, *, glibc="ldd (GNU libc) 2.31", sudo="ready"):
        self.calls = []
        self.glibc = glibc
        self.sudo = sudo

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
                    "glibc={}".format(self.glibc),
                    "sudo={}".format(self.sudo),
                    "free_kib=20971520",
                    "free_inodes=200000",
                    "proc=ready",
                    "memfd=ready",
                    "remote_root=ready",
                ]
            ) + "\n"
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")


class SuccessfulPreflightRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, *, cwd, environment, timeout):
        self.calls.append(
            {
                "argv": list(argv),
                "cwd": str(cwd),
                "environment": dict(environment),
                "timeout": timeout,
            }
        )
        return subprocess.CompletedProcess(list(argv), 0, "candidate preflight passed\n", "")


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


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_candidate_review(
    repo: Path,
    plan,
    review_root: Path,
    *,
    corrupt_deb: bool = False,
) -> Path:
    delivery = review_root / "taijiagent 打包交付"
    output = delivery / "生成的安装包"
    output.mkdir(parents=True)
    shutil.copy2(
        repo / "taijiagent 打包交付/01_制包机_发布预检.sh",
        delivery / "01_制包机_发布预检.sh",
    )
    deb = output / "taiji-agent_1.2.3_amd64.deb"
    deb.write_bytes(b"candidate-deb-bytes\n")
    deb_sha = file_sha256(deb)
    (output / (deb.name + ".sha256")).write_text(
        "{}  {}\n".format(deb_sha, deb.name), encoding="utf-8"
    )
    formal_log = output / "formal-build-tests.log"
    formal_log.write_text("formal-build-tests/v2\noverall_status=pass\n", encoding="utf-8")
    policy_sha = plan.get("canonical_policy_sha256", "c" * 64)
    manifest = {
        "schema": "taiji-package-manifest/v3",
        "package": "taiji-agent",
        "version": "1.2.3",
        "architecture": "amd64",
        "source_commit": plan["source_commit"],
        "deb_basename": deb.name,
        "deb_sha256": deb_sha,
        "compatibility_policy_sha256": policy_sha,
        "formal_build_tests_status": "pass",
        "formal_build_tests_log_basename": formal_log.name,
        "formal_build_tests_log_sha256": file_sha256(formal_log),
    }
    (output / "taiji-package-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "构建报告.txt").write_text("候选构建报告\n", encoding="utf-8")
    (output / ".build-success").write_text(
        "\n".join(
            [
                "version=1.2.3",
                "source_commit={}".format(plan["source_commit"]),
                "deb={}".format(deb.name),
                "deb_sha256={}".format(deb_sha),
                "compatibility_policy_sha256={}".format(policy_sha),
                "formal_build_tests_status=pass",
                "formal_build_tests_log_basename={}".format(formal_log.name),
                "formal_build_tests_log_sha256={}".format(file_sha256(formal_log)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if corrupt_deb:
        deb.write_bytes(b"corrupted-after-manifest\n")
    return review_root


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

    def test_real_transport_preserves_both_failure_streams_and_success(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-real-transport-errors-") as temporary:
            root = Path(temporary)
            repo, target, ssh_config, _plan = make_plan(module, root)
            cases = (
                ("stdout detail\n", "stderr detail\n", ("stdout detail", "stderr detail")),
                ("stdout only\n", "", ("stdout only",)),
                ("", "stderr only\n", ("stderr only",)),
                ("", "", ("command returned non-zero",)),
            )
            for stdout, stderr, expected_fragments in cases:
                with self.subTest(stdout=stdout, stderr=stderr):
                    runner = lambda argv, *, cwd, environment, timeout: subprocess.CompletedProcess(
                        list(argv), 17, stdout, stderr
                    )
                    transport = module.RealSshTransport(
                        repo, target, ssh_config=ssh_config, command_runner=runner
                    )
                    with self.assertRaises(module.PipelineError) as raised:
                        transport._execute(["/usr/bin/ssh", "failure"], "BUILD_00_FAILED", 10)
                    self.assertEqual(raised.exception.category, "BUILD_00_FAILED")
                    for fragment in expected_fragments:
                        self.assertIn(fragment, str(raised.exception))

            success_runner = lambda argv, *, cwd, environment, timeout: subprocess.CompletedProcess(
                list(argv), 0, "stdout success\n", "stderr success\n"
            )
            transport = module.RealSshTransport(
                repo, target, ssh_config=ssh_config, command_runner=success_runner
            )
            self.assertIsNone(
                transport._execute(["/usr/bin/ssh", "success"], "BUILD_00_FAILED", 10)
            )

    def test_online_doctor_blocks_reachable_builder_below_policy_glibc(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-online-blocked-") as temporary:
            root = Path(temporary)
            repo, target, ssh_config, _plan = make_plan(module, root)
            transport = module.RealSshTransport(
                repo,
                target,
                ssh_config=ssh_config,
                command_runner=RecordingRunner(
                    glibc="ldd (GNU libc) 2.30", sudo="blocked"
                ),
            )

            result = transport.online_doctor()

            self.assertEqual(result["builder_status"], "BLOCKED")
            self.assertEqual(result["glibc_version"], "2.30")
            self.assertEqual(result["minimum_glibc"], "2.31")
            self.assertTrue(any("glibc" in blocker for blocker in result["blockers"]))
            self.assertTrue(any("sudo" in blocker for blocker in result["blockers"]))

    def test_online_doctor_remote_probe_is_valid_bash_and_reports_blocked_values(self):
        module = load_candidate()
        target = module.load_target(TARGET)
        script = module._online_doctor_script(target)

        result = subprocess.run(
            ["/bin/bash", "-n", "-c", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sudo_status=blocked", script)
        self.assertIn("remote_root_status=blocked", script)
        self.assertNotIn("set -Eeuo pipefail", script)

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

    def test_run_state_binds_successful_candidate_and_stage_timings(self):
        module = load_candidate()
        self.assertTrue(
            hasattr(module, "run_candidate_build"), "stateful candidate build is missing"
        )
        with tempfile.TemporaryDirectory(prefix="taiji-stateful-build-") as temporary:
            root = Path(temporary)
            repo, _target, _ssh_config, plan = make_plan(module, root)
            source_commit = plan["source_commit"]
            from tests.test_taiji_package_candidate import make_verified_triplet

            make_verified_triplet(repo, source_commit, root / "input-fixture")
            plan = module.build_candidate_plan(
                repo,
                module.load_target(TARGET),
                root / "state",
                run_id="stateful-success",
                ssh_config=write_ssh_config(root / "ssh_config-stateful"),
            )
            review_source = make_candidate_review(repo, plan, root / "remote-review")
            transport = module.FakeSshTransport(review_source=review_source)
            preflight = SuccessfulPreflightRunner()
            store = module.RunStateStore(root / "state")

            state = module.run_candidate_build(
                plan,
                store,
                transport,
                confirmed=True,
                review_validator=lambda active_plan, review, remote_log: module.validate_candidate_review(
                    active_plan,
                    review,
                    remote_log,
                    command_runner=preflight,
                ),
            )

            self.assertEqual(state["stage"], "CANDIDATE_BUILT")
            self.assertEqual(state["status_label"], "候选 DEB 已构建")
            self.assertTrue(state["remote_build_succeeded"])
            self.assertFalse(state["fetch_allowed"])
            self.assertEqual(state["source"]["commit"], source_commit)
            self.assertRegex(state["policy"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(state["artifact"]["sha256"], file_sha256(Path(state["artifact"]["path"])))
            self.assertEqual(state["deb"]["sha256"], file_sha256(Path(state["deb"]["path"])))
            self.assertEqual(state["lock"]["status"], "released")
            passed_stages = [item["stage"] for item in state["stage_history"]]
            self.assertEqual(
                passed_stages,
                [
                    "INPUT_VERIFIED",
                    "REMOTE_RUN_CREATED",
                    "INPUT_TRANSFERRED",
                    "REMOTE_INPUT_VERIFIED",
                    "REMOTE_BUILD_SUCCEEDED",
                    "REVIEW_FETCHED",
                    "LOCAL_REVIEW_VERIFIED",
                    "CANDIDATE_BUILT",
                ],
            )
            self.assertTrue(all(item["duration_seconds"] >= 0 for item in state["stage_history"]))
            self.assertTrue((store.run_dir("stateful-success") / "review").is_dir())
            self.assertTrue((store.run_dir("stateful-success") / "remote-build.log").is_file())
            self.assertEqual(len(preflight.calls), 1)
            self.assertEqual(preflight.calls[0]["argv"][0:2], ["/bin/bash", "-p"])

    def test_fetch_pending_retries_only_retrieval_and_local_verification(self):
        module = load_candidate()
        self.assertTrue(
            hasattr(module, "run_candidate_build"), "stateful candidate build is missing"
        )
        with tempfile.TemporaryDirectory(prefix="taiji-fetch-recovery-") as temporary:
            root = Path(temporary)
            repo, _target, _ssh_config, plan = make_plan(module, root)
            from tests.test_taiji_package_candidate import make_verified_triplet

            make_verified_triplet(repo, plan["source_commit"], root / "input-fixture")
            plan = module.build_candidate_plan(
                repo,
                module.load_target(TARGET),
                root / "state",
                run_id="fetch-pending",
                ssh_config=write_ssh_config(root / "ssh_config-pending"),
            )
            store = module.RunStateStore(root / "state")
            failed_transport = module.FakeSshTransport(fail_stage="fetch-review")
            with self.assertRaises(module.PipelineError):
                module.run_candidate_build(
                    plan, store, failed_transport, confirmed=True
                )
            pending = store.load("fetch-pending")
            self.assertEqual(pending["stage"], "FETCH_PENDING")
            self.assertTrue(pending["remote_build_succeeded"])
            self.assertTrue(pending["fetch_allowed"])

            review_source = make_candidate_review(repo, plan, root / "remote-review")
            retry_transport = module.FakeSshTransport(review_source=review_source)
            preflight = SuccessfulPreflightRunner()
            recovered = module.fetch_candidate(
                store,
                "fetch-pending",
                retry_transport,
                review_validator=lambda active_plan, review, remote_log: module.validate_candidate_review(
                    active_plan,
                    review,
                    remote_log,
                    command_runner=preflight,
                ),
            )

            self.assertEqual(retry_transport.calls, ["fetch-review", "fetch-log"])
            self.assertEqual(recovered["stage"], "CANDIDATE_BUILT")
            self.assertFalse(recovered["fetch_allowed"])

    def test_terminal_remote_build_failure_records_run_end_time(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-terminal-failure-") as temporary:
            root = Path(temporary)
            repo, _target, _ssh_config, plan = make_plan(module, root)
            from tests.test_taiji_package_candidate import make_verified_triplet

            make_verified_triplet(repo, plan["source_commit"], root / "input-fixture")
            plan = module.build_candidate_plan(
                repo,
                module.load_target(TARGET),
                root / "state",
                run_id="terminal-failure",
                ssh_config=write_ssh_config(root / "ssh_config-terminal"),
            )
            store = module.RunStateStore(root / "state")

            with self.assertRaises(module.PipelineError):
                module.run_candidate_build(
                    plan,
                    store,
                    module.FakeSshTransport(fail_stage="build-00"),
                    confirmed=True,
                )

            state = store.load("terminal-failure")
            self.assertEqual(state["stage"], "FAILED")
            self.assertEqual(state["failure"]["category"], "BUILD_00_FAILED")
            self.assertIsNotNone(state["finished_at"])

    def test_fetch_rejects_non_remote_success_sha_mismatch_and_occupied_output(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-fetch-negative-") as temporary:
            root = Path(temporary)
            repo, _target, _ssh_config, plan = make_plan(module, root)
            store = module.RunStateStore(root / "state")

            not_remote = complete_v2_payload(
                root,
                run_id="not-remote-success",
                input_status="MISSING",
                stage="FAILED",
                remote_build_succeeded=False,
                fetch_allowed=False,
            )
            not_remote["plan"] = dict(plan)
            not_remote["plan"]["input"] = not_remote["input"]
            store.create("not-remote-success", not_remote)
            untouched = module.FakeSshTransport()
            with self.assertRaises(module.PipelineError) as not_allowed:
                module.fetch_candidate(store, "not-remote-success", untouched)
            self.assertEqual(not_allowed.exception.category, "FETCH_NOT_ALLOWED")
            self.assertEqual(untouched.calls, [])

            pending_payload = complete_v2_payload(
                root,
                run_id="sha-mismatch",
                input_status="MISSING",
                stage="FETCH_PENDING",
                remote_build_succeeded=True,
                fetch_allowed=True,
            )
            pending_payload["plan"] = dict(plan)
            pending_payload["plan"]["input"] = pending_payload["input"]
            store.create("sha-mismatch", pending_payload)
            bad_review = make_candidate_review(
                repo, plan, root / "bad-review", corrupt_deb=True
            )
            bad_transport = module.FakeSshTransport(review_source=bad_review)
            with self.assertRaises(module.PipelineError) as mismatch:
                module.fetch_candidate(
                    store,
                    "sha-mismatch",
                    bad_transport,
                    review_validator=lambda active_plan, review, remote_log: module.validate_candidate_review(
                        active_plan,
                        review,
                        remote_log,
                        command_runner=SuccessfulPreflightRunner(),
                    ),
                )
            self.assertEqual(mismatch.exception.category, "ARTIFACT_SHA_MISMATCH")
            self.assertEqual(store.load("sha-mismatch")["stage"], "FETCH_PENDING")

            occupied_payload = complete_v2_payload(
                root,
                run_id="occupied",
                input_status="MISSING",
                stage="FETCH_PENDING",
                remote_build_succeeded=True,
                fetch_allowed=True,
            )
            occupied_payload["plan"] = dict(plan)
            occupied_payload["plan"]["input"] = occupied_payload["input"]
            store.create("occupied", occupied_payload)
            occupied_dir = store.run_dir("occupied") / "review"
            occupied_dir.mkdir()
            occupied_transport = module.FakeSshTransport(review_source=bad_review)
            with self.assertRaises(module.PipelineError) as occupied:
                module.fetch_candidate(store, "occupied", occupied_transport)
            self.assertEqual(occupied.exception.category, "LOCAL_OUTPUT_OCCUPIED")
            self.assertEqual(occupied_transport.calls, [])

    def test_local_review_verification_rejects_symlinked_roots_and_logs(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-review-links-") as temporary:
            root = Path(temporary)
            repo, _target, _ssh_config, plan = make_plan(module, root)
            review = make_candidate_review(repo, plan, root / "review")
            review_link = root / "review-link"
            review_link.symlink_to(review, target_is_directory=True)
            remote_log = root / "remote-build.log"
            remote_log.write_text("remote build log\n", encoding="utf-8")
            remote_log_link = root / "remote-build-link.log"
            remote_log_link.symlink_to(remote_log)

            with self.assertRaises(module.PipelineError) as linked_review:
                module.validate_candidate_review(
                    plan,
                    review_link,
                    remote_log,
                    command_runner=SuccessfulPreflightRunner(),
                )
            self.assertEqual(linked_review.exception.category, "LOCAL_REVIEW_INVALID")

            with self.assertRaises(module.PipelineError) as linked_log:
                module.validate_candidate_review(
                    plan,
                    review,
                    remote_log_link,
                    command_runner=SuccessfulPreflightRunner(),
                )
            self.assertEqual(linked_log.exception.category, "LOCAL_REVIEW_INVALID")

    def test_local_review_verification_rechecks_main_commit_and_clean_state(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-review-source-drift-") as temporary:
            root = Path(temporary)
            repo, _target, _ssh_config, plan = make_plan(module, root)
            review = make_candidate_review(repo, plan, root / "review")
            remote_log = root / "remote-build.log"
            remote_log.write_text("remote build log\n", encoding="utf-8")
            (repo / "drift.txt").write_text("untracked drift\n", encoding="utf-8")

            with self.assertRaises(module.PipelineError) as drift:
                module.validate_candidate_review(
                    plan,
                    review,
                    remote_log,
                    command_runner=SuccessfulPreflightRunner(),
                )

            self.assertEqual(drift.exception.category, "SOURCE_DRIFT")

    def test_build_cli_displays_plan_confirms_once_and_persists_result(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-build-cli-") as temporary:
            root = Path(temporary)
            repo, _target, ssh_config, seed_plan = make_plan(module, root)
            from tests.test_taiji_package_candidate import make_verified_triplet

            make_verified_triplet(repo, seed_plan["source_commit"], root / "input-fixture")
            seed_plan = module.build_candidate_plan(
                repo,
                module.load_target(TARGET),
                root / "state-seed",
                run_id="seed-plan",
                ssh_config=ssh_config,
            )
            review_source = make_candidate_review(repo, seed_plan, root / "remote-review")
            transport = module.FakeSshTransport(review_source=review_source)
            original_validator = module.validate_candidate_review
            preflight = SuccessfulPreflightRunner()

            def validator(active_plan, review, remote_log):
                return original_validator(
                    active_plan,
                    review,
                    remote_log,
                    command_runner=preflight,
                )

            def invoke_main():
                try:
                    return module.main(
                        [
                            "--repo",
                            str(repo),
                            "--target",
                            str(TARGET),
                            "--state-root",
                            str(root / "state"),
                            "--ssh-config",
                            str(ssh_config),
                            "build",
                        ]
                    )
                except SystemExit as exc:
                    raise AssertionError("build CLI arguments are not implemented") from exc

            output = io.StringIO()
            with mock.patch.object(
                module,
                "RealSshTransport",
                side_effect=lambda *args, **kwargs: transport,
            ), mock.patch.object(
                module, "validate_candidate_review", side_effect=validator
            ), mock.patch("builtins.input", return_value="BUILD"), contextlib.redirect_stdout(
                output
            ):
                return_code = implemented_result(invoke_main)

            self.assertEqual(return_code, 0)
            self.assertEqual(transport.calls.count("online-doctor"), 1)
            self.assertIn("taiji-package-candidate-plan/v1", output.getvalue())
            run_dirs = list((root / "state/runs").iterdir())
            self.assertEqual(len(run_dirs), 1)
            state = module.RunStateStore(root / "state").load(run_dirs[0].name)
            self.assertEqual(state["stage"], "CANDIDATE_BUILT")


if __name__ == "__main__":
    unittest.main()
