"""Contract tests for the thin x86 Kylin candidate pipeline."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "taiji-package"
CANDIDATE = ROOT / "scripts/taiji-package-candidate.py"
TARGET = ROOT / "packaging/pipeline/targets/kylin-amd64.json"


def git_environment():
    environment = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Taiji Candidate Test",
            "GIT_AUTHOR_EMAIL": "candidate@example.invalid",
            "GIT_COMMITTER_NAME": "Taiji Candidate Test",
            "GIT_COMMITTER_EMAIL": "candidate@example.invalid",
            "LC_ALL": "C",
        }
    )
    return environment


def git(repo: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=repo,
        env=git_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def make_doctor_repo(parent: Path) -> Path:
    repo = parent / "repo"
    (repo / "packaging/linux").mkdir(parents=True)
    (repo / "taijiagent 打包交付").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    interface = {
        "build_host_entry": "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
        "builder_input_entry": "taijiagent 打包交付/99_本机_准备制包输入包.sh",
        "canonical_runbook": "docs/runbooks/taiji-kylin-uos-offline-delivery.md",
        "orchestrator": {
            "path": "scripts/taiji-linux-golden-orchestrator.py",
            "plan_schema": "taiji-linux-golden-orchestrator-plan/v5",
        },
        "repository_id": "taiji-agentv1.0",
        "schema": "taiji-packaging-interface/v1",
    }
    (repo / "packaging/linux/taiji-packaging-interface.json").write_text(
        json.dumps(interface, sort_keys=True) + "\n", encoding="utf-8"
    )
    for relative in (
        "taijiagent 打包交付/99_本机_准备制包输入包.sh",
        "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
        "taijiagent 打包交付/01_制包机_发布预检.sh",
        "packaging/linux/builder-input-package.py",
        "packaging/linux/compatibility-policy.json",
        "scripts/taiji-linux-golden-orchestrator.py",
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture:{}\n".format(relative), encoding="utf-8")
    git(repo, "init", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "doctor fixture")
    return repo


def write_ssh_config(path: Path, *, include_kylin: bool = True) -> Path:
    host = "kylin" if include_kylin else "another-builder"
    path.write_text(
        "Host {}\n  HostName 192.0.2.10\n  User kylin\n".format(host),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def implemented_result(callback):
    try:
        return callback()
    except Exception as exc:  # The RED assertion reports the missing behavior as a failure.
        raise AssertionError("candidate behavior is not implemented: {}".format(exc)) from exc


def load_candidate():
    spec = importlib.util.spec_from_file_location("taiji_package_candidate", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Taiji candidate pipeline")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CandidatePipelineContractTests(unittest.TestCase):
    def test_unified_cli_and_target_adapter_exist(self):
        self.assertTrue(ENTRYPOINT.is_file(), "unified taiji-package CLI is missing")
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        self.assertTrue(TARGET.is_file(), "kylin-amd64 target adapter is missing")

    def test_local_doctor_and_plan_contracts_exist(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(callable(module.local_doctor))
        self.assertTrue(callable(module.build_candidate_plan))

    def test_run_state_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(hasattr(module, "RunStateStore"))

    def test_fetch_recovery_contract_exists(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        self.assertTrue(callable(module.fetch_candidate))

    def test_target_adapter_is_non_sensitive_and_python38_compatible(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        self.assertTrue(TARGET.is_file(), "kylin-amd64 target adapter is missing")
        module = load_candidate()

        target = module.load_target(TARGET)

        self.assertEqual(target["schema"], "taiji-package-target/v1")
        self.assertEqual(target["target_id"], "kylin-amd64")
        self.assertEqual(target["host_alias"], "kylin")
        self.assertEqual(target["remote_user"], "kylin")
        self.assertEqual(target["remote_account_home"], "/home/kylin")
        self.assertEqual(target["remote_root"], "/home/kylin/taiji-builds")
        self.assertEqual(target["architecture"], "amd64")
        self.assertEqual(target["minimum_free_gib"], 12)
        self.assertEqual(target["minimum_free_inodes"], 100000)
        serialized = json.dumps(target, ensure_ascii=False)
        for secret_name in ("password", "private_key", "token", "credential"):
            self.assertNotIn(secret_name, serialized.lower())

    def test_parser_exposes_only_candidate_lifecycle_commands(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()

        doctor = module.parse_args(["doctor", "--online"])
        plan = module.parse_args(["plan"])
        build = module.parse_args(["build"])
        status = module.parse_args(["status", "--run", "run-1"])
        fetch = module.parse_args(["fetch", "--run", "run-1"])

        self.assertEqual((doctor.command, doctor.online), ("doctor", True))
        self.assertEqual(plan.command, "plan")
        self.assertEqual(build.command, "build")
        self.assertEqual((status.command, status.run_id), ("status", "run-1"))
        self.assertEqual((fetch.command, fetch.run_id), ("fetch", "run-1"))

    def test_run_state_store_creates_updates_and_refuses_overwrite(self):
        self.assertTrue(CANDIDATE.is_file(), "candidate pipeline module is missing")
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-package-state-") as temporary:
            state_root = Path(temporary) / "state"
            store = module.RunStateStore(state_root)

            created = store.create(
                "run-1",
                {
                    "source_commit": "a" * 40,
                    "stage": "CREATED",
                    "host_alias": "kylin",
                },
            )
            loaded = store.load("run-1")

            self.assertEqual(created, loaded)
            self.assertEqual(loaded["schema"], "taiji-package-run-state/v1")
            self.assertEqual(loaded["run_id"], "run-1")
            self.assertEqual(loaded["source_commit"], "a" * 40)
            self.assertEqual(loaded["stage"], "CREATED")
            run_dir = state_root / "runs/run-1"
            self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((run_dir / "run-state.json").stat().st_mode), 0o600
            )

            updated = store.update("run-1", {"stage": "FETCH_PENDING"})
            self.assertEqual(updated["stage"], "FETCH_PENDING")
            self.assertEqual(store.load("run-1")["stage"], "FETCH_PENDING")
            with self.assertRaises(module.PipelineError):
                store.create("run-1", {"stage": "CREATED"})
            with self.assertRaises(module.PipelineError):
                store.load("../escape")

    def test_local_doctor_accepts_only_clean_main_with_declared_interface(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-doctor-") as temporary:
            root = Path(temporary)
            repo = make_doctor_repo(root)
            ssh_config = write_ssh_config(root / "ssh_config")

            result = implemented_result(
                lambda: module.local_doctor(
                    repo,
                    module.load_target(TARGET),
                    root / "state",
                    ssh_config=ssh_config,
                )
            )

            self.assertEqual(result["schema"], "taiji-package-doctor/v1")
            self.assertEqual(result["controller_status"], "CONTROLLER_READY")
            self.assertEqual(result["builder_status"], "BUILDER_UNREACHABLE")
            self.assertFalse(result["online_checked"])
            self.assertEqual(result["branch"], "main")
            self.assertRegex(result["source_commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(result["blockers"], [])

    def test_local_doctor_blocks_dirty_non_main_missing_interface_and_alias(self):
        module = load_candidate()
        target = module.load_target(TARGET)
        with tempfile.TemporaryDirectory(prefix="taiji-doctor-negative-") as temporary:
            root = Path(temporary)
            ssh_config = write_ssh_config(root / "ssh_config")

            dirty_repo = make_doctor_repo(root / "dirty")
            (dirty_repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            dirty = implemented_result(
                lambda: module.local_doctor(
                    dirty_repo, target, root / "state-dirty", ssh_config=ssh_config
                )
            )
            self.assertEqual(dirty["controller_status"], "BLOCKED")
            self.assertIn("WORKTREE_NOT_CLEAN", dirty["failure_categories"])

            branch_repo = make_doctor_repo(root / "branch")
            git(branch_repo, "switch", "-c", "feature")
            branch = implemented_result(
                lambda: module.local_doctor(
                    branch_repo, target, root / "state-branch", ssh_config=ssh_config
                )
            )
            self.assertIn("BRANCH_NOT_MAIN", branch["failure_categories"])

            interface_repo = make_doctor_repo(root / "interface")
            (interface_repo / "packaging/linux/taiji-packaging-interface.json").unlink()
            interface = implemented_result(
                lambda: module.local_doctor(
                    interface_repo, target, root / "state-interface", ssh_config=ssh_config
                )
            )
            self.assertIn("PACKAGING_INTERFACE_INVALID", interface["failure_categories"])

            alias_repo = make_doctor_repo(root / "alias")
            missing_alias = write_ssh_config(root / "ssh_config_missing", include_kylin=False)
            alias = implemented_result(
                lambda: module.local_doctor(
                    alias_repo, target, root / "state-alias", ssh_config=missing_alias
                )
            )
            self.assertIn("SSH_ALIAS_MISSING", alias["failure_categories"])

    def test_explicit_repo_wins_over_polluted_git_environment(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-doctor-git-env-") as temporary:
            root = Path(temporary)
            repo = make_doctor_repo(root / "explicit")
            other = make_doctor_repo(root / "other")
            ssh_config = write_ssh_config(root / "ssh_config")
            previous = os.environ.get("GIT_DIR")
            os.environ["GIT_DIR"] = str(other / ".git")
            try:
                result = implemented_result(
                    lambda: module.local_doctor(
                        repo,
                        module.load_target(TARGET),
                        root / "state",
                        ssh_config=ssh_config,
                    )
                )
            finally:
                if previous is None:
                    os.environ.pop("GIT_DIR", None)
                else:
                    os.environ["GIT_DIR"] = previous

            self.assertEqual(result["controller_status"], "CONTROLLER_READY")
            self.assertEqual(result["repo_root"], str(repo.resolve()))
            self.assertEqual(result["source_commit"], git(repo, "rev-parse", "HEAD").stdout.strip())

    def test_candidate_plan_is_read_only_and_shows_exact_boundaries(self):
        module = load_candidate()
        with tempfile.TemporaryDirectory(prefix="taiji-plan-") as temporary:
            root = Path(temporary)
            repo = make_doctor_repo(root)
            state_root = root / "state"
            ssh_config = write_ssh_config(root / "ssh_config")
            before = git(repo, "status", "--porcelain").stdout

            plan = implemented_result(
                lambda: module.build_candidate_plan(
                    repo,
                    module.load_target(TARGET),
                    state_root,
                    run_id="run-plan-test",
                    ssh_config=ssh_config,
                )
            )

            self.assertEqual(plan["schema"], "taiji-package-candidate-plan/v1")
            self.assertEqual(plan["run_id"], "run-plan-test")
            self.assertEqual(plan["input"]["status"], "MISSING")
            self.assertTrue(plan["input"]["prepare_required"])
            self.assertEqual(plan["host_alias"], "kylin")
            self.assertTrue(plan["remote_run_dir"].endswith("/run-plan-test"))
            self.assertEqual(
                plan["local_run_dir"], str(state_root.resolve() / "runs/run-plan-test")
            )
            rendered = json.dumps(plan, ensure_ascii=False)
            self.assertIn("99_本机_准备制包输入包.sh", rendered)
            self.assertIn("00_制包机_生成离线交付包.sh", rendered)
            for forbidden in (
                "02_目标终端_安装并验证.sh",
                "04_目标终端_桌面App验收并导出证据.sh",
                "sign-taiji",
                "publish-single-deb",
            ):
                self.assertNotIn(forbidden, rendered)
            self.assertEqual(git(repo, "status", "--porcelain").stdout, before)
            self.assertFalse(state_root.exists())
            self.assertFalse((repo / "taijiagent-制包机输入-unknown.tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
