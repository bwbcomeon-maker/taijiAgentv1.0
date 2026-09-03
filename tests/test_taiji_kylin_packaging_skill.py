from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / ".agents/skills/taiji-kylin-packaging"
DOCTOR = SKILL_ROOT / "scripts/doctor.py"
PACKAGER = REPO_ROOT / "scripts/package-taiji-kylin-packaging-skill.py"
INTERFACE = REPO_ROOT / "packaging/linux/taiji-packaging-interface.json"
RUNBOOK = REPO_ROOT / "docs/runbooks/taiji-kylin-uos-offline-delivery.md"
COMMIT = "a" * 40

SOURCE_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "scripts/doctor.py",
    "references/deb-offline-delivery.md",
    "references/failure-playbook.md",
    "references/kylin-deb-version-history.md",
    "references/privacy-surface-gate.md",
    "references/release-gates.md",
    "references/agent-installation.md",
    "evals/evals.json",
}
PACKAGE_FILES = SOURCE_FILES - {"evals/evals.json"}

EXPECTED_INTERFACE = {
    "schema": "taiji-packaging-interface/v1",
    "repository_id": "taiji-agentv1.0",
    "orchestrator": {
        "path": "scripts/taiji-linux-golden-orchestrator.py",
        "plan_schema": "taiji-linux-golden-orchestrator-plan/v5",
    },
    "build_host_entry": "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
    "builder_input_entry": "taijiagent 打包交付/99_本机_准备制包输入包.sh",
    "canonical_runbook": "docs/runbooks/taiji-kylin-uos-offline-delivery.md",
}


def run_python(
    script: Path,
    *arguments: str,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    command_env = os.environ.copy()
    command_env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        command_env.update(env)
    return subprocess.run(
        [sys.executable, "-I", "-B", str(script), *arguments],
        cwd=REPO_ROOT,
        env=command_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def parse_report(result: subprocess.CompletedProcess) -> Dict[str, object]:
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"doctor stdout is not one JSON document: {result.stdout!r}; stderr={result.stderr!r}"
        ) from exc
    if not isinstance(report, dict):
        raise AssertionError("doctor report must be an object")
    return report


def git(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/usr/bin/git", "-c", "user.name=Taiji Test", "-c", "user.email=taiji@example.invalid", *arguments],
        cwd=root,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def make_repo_fixture(parent: Path) -> Tuple[Path, Path]:
    parent = parent.resolve(strict=True)
    root = parent / "taiji-agentv1.0"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")

    interface = root / "packaging/linux/taiji-packaging-interface.json"
    interface.parent.mkdir(parents=True)
    interface.write_text(
        json.dumps(EXPECTED_INTERFACE, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    sentinel = root / "orchestrator-executed"
    orchestrator = root / EXPECTED_INTERFACE["orchestrator"]["path"]
    orchestrator.parent.mkdir(parents=True)
    orchestrator.write_text(
        "from pathlib import Path\nPath(%r).write_text('executed')\n" % str(sentinel),
        encoding="utf-8",
    )
    build_entry = root / EXPECTED_INTERFACE["build_host_entry"]
    build_entry.parent.mkdir(parents=True)
    build_entry.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
    builder_input_entry = root / EXPECTED_INTERFACE["builder_input_entry"]
    builder_input_entry.parent.mkdir(parents=True, exist_ok=True)
    builder_input_entry.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
    runbook = root / EXPECTED_INTERFACE["canonical_runbook"]
    runbook.parent.mkdir(parents=True)
    runbook.write_text("# Fixture\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "fixture")
    return root, sentinel


def make_input_fixture(root: Path, *, duplicate_manifest_key: bool = False) -> Path:
    root = root.parent.resolve(strict=True) / root.name
    root.mkdir()
    archive_name = f"taijiagent-制包机输入-{COMMIT}.tar.gz"
    manifest_name = f"taijiagent-制包机输入-{COMMIT}.manifest.json"
    checksum_name = archive_name + ".sha256"
    archive = root / archive_name
    archive.write_bytes(b"fixture archive")
    payload = {
        "schema": "taiji-builder-input-package/v1",
        "source_commit": COMMIT,
        "archive_basename": archive_name,
        "archive_size": len(b"fixture archive"),
        "archive_sha256": hashlib.sha256(b"fixture archive").hexdigest(),
        "source_archive_basename": "taiji-agent-source.tar.gz",
        "source_archive_sha256": "a" * 64,
        "source_inventory_basename": "source-inventory.json",
        "source_inventory_sha256": "b" * 64,
        "source_integrity_helper_sha256": "c" * 64,
        "builder_input_helper_sha256": "d" * 64,
        "archive_root_basename": "taijiagent-builder-input",
        "manifest_basename": manifest_name,
        "checksum_basename": checksum_name,
        "members": [],
    }
    manifest_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if duplicate_manifest_key:
        manifest_text = manifest_text.replace(
            '"schema":"taiji-builder-input-package/v1"',
            '"schema":"taiji-builder-input-package/v1","schema":"taiji-builder-input-package/v1"',
        )
    manifest = root / manifest_name
    manifest.write_text(manifest_text, encoding="utf-8")
    checksum = root / checksum_name
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive_name}\n"
        f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  {manifest_name}\n",
        encoding="utf-8",
    )
    return root


class SkillSourceContractTests(unittest.TestCase):
    def test_repo_owned_skill_has_exact_source_file_set(self) -> None:
        actual = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, SOURCE_FILES)

    def test_interface_is_the_exact_non_executable_contract(self) -> None:
        self.assertEqual(json.loads(INTERFACE.read_text(encoding="utf-8")), EXPECTED_INTERFACE)

    def test_skill_is_self_contained_and_carries_authorization_boundary(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for forbidden in (
            "subagent-driven-development",
            "executing-plans",
            "using-superpowers",
            "test-driven-development",
        ):
            self.assertNotIn(forbidden, content)
        for required in (
            "兼容检查通过不等于已获执行授权",
            "SSH",
            "sudo",
            "实际 DEB 制包",
            "签名",
            "发布",
            "客户目录恰好只有一个 DEB",
            "python3 -I -B scripts/doctor.py --repo <operator-supplied-path>",
            "恰好一个同一 commit 的 `tar.gz`、`manifest.json` 与 `tar.gz.sha256` 三件套",
            "`/usr/bin/taiji-agent-acceptance`",
            "当“继续”夹带尚未授权的外部或特权阶段",
            "只提供只读诊断或计划",
            "`待授权阶段`",
            "`精确身份`",
            "`影响范围`",
            "`回滚与停止条件`",
            "只对操作员明确提供的路径运行 `doctor.py --repo PATH`，不得扫描其它目录",
            "`.skill` 是 Codex 的便利安装包",
            "其它 Agent 产品只有经过实际测试后才能标记为已验证",
        ):
            self.assertIn(required, content)

    def test_skill_distinguishes_builder_input_from_build_host_entry(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for required in (
            "`builder_input_entry`",
            "`taijiagent 打包交付/99_本机_准备制包输入包.sh`",
            "`build_host_entry`",
            "`taijiagent 打包交付/00_制包机_生成离线交付包.sh`",
            "Doctor 的 repo 模式只能把 99 报告为下一步",
        ):
            self.assertIn(required, content)

    def test_candidate_only_pipeline_is_documented_with_narrow_authorization(self) -> None:
        for path in (SKILL_ROOT / "SKILL.md", RUNBOOK):
            content = path.read_text(encoding="utf-8")
            for required in (
                "./taiji-package doctor",
                "./taiji-package doctor --online",
                "./taiji-package plan",
                "./taiji-package build",
                "./taiji-package status --run <run-id>",
                "./taiji-package fetch --run <run-id>",
                "CONTROLLER_READY",
                "BUILDER_UNREACHABLE",
                "FETCH_PENDING",
                "SSH 与传输",
                "依赖与网络",
                "候选构建",
                "候选 DEB 已构建",
                "不安装、不验收、不签名、不发布",
                "黄金编排器",
            ):
                self.assertIn(required, content, "{} missing {!r}".format(path, required))
        runbook = RUNBOOK.read_text(encoding="utf-8")
        for local_status in (
            "已实现，本地模拟通过",
            "真实麒麟连接未验证",
            "候选 DEB 未构建",
        ):
            self.assertIn(local_status, runbook)

    def test_evals_use_current_object_schema_and_cover_pressure_cases(self) -> None:
        payload = json.loads((SKILL_ROOT / "evals/evals.json").read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"skill_name", "evals"})
        self.assertEqual(payload["skill_name"], "taiji-kylin-packaging")
        ids = [entry["id"] for entry in payload["evals"]]
        self.assertTrue(ids)
        self.assertTrue(all(isinstance(value, int) and value > 0 for value in ids))
        self.assertEqual(ids, sorted(set(ids)))
        for entry in payload["evals"]:
            self.assertEqual(
                set(entry),
                {"id", "prompt", "expected_output", "files", "expectations"},
            )
            self.assertTrue(entry["expectations"])
            self.assertTrue(all(isinstance(item, str) and item.strip() for item in entry["expectations"]))
        rendered = json.dumps(payload, ensure_ascii=False)
        for topic in ("dirty", "frozen", "继续", "一个 DEB", "/usr/bin/taiji-agent-acceptance", "历史 v2", "私有"):
            self.assertIn(topic, rendered)
        for scenario in ("只读 SSH", "同一对象", "hotfix", "模型对话", "新建用户"):
            self.assertIn(scenario, rendered)

    def test_authorization_continuity_does_not_authorize_new_stages(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("a historical approval, or the user's word", content)
        self.assertNotIn("| SSH or file transfer | Stop |", content)
        self.assertRegex(content, r"只读 SSH.{0,100}无需重复")
        self.assertRegex(content, r"同一对象.{0,80}阶段.{0,80}影响范围.{0,100}延续")
        self.assertRegex(content, r"(对象|摘要).{0,80}变化.{0,100}授权")
        self.assertIn("A build approval does not authorize installation", content)
        self.assertIn("`BUILD`", content)
        self.assertIn("黄金编排器", content)

    def test_hotfix_and_target_labels_match_the_current_linux_boundary(self) -> None:
        for path in (RUNBOOK, SKILL_ROOT / "SKILL.md", SKILL_ROOT / "references/deb-offline-delivery.md"):
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertRegex(content, r"Linux.{0,80}hotfix.{0,80}(不支持|尚未支持)")
                self.assertIn("clean `main`", content)
        for path in (RUNBOOK, SKILL_ROOT / "references/release-gates.md"):
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("指定主机安装与桌面检查通过", content)
                for label in ("制包输入已准备", "候选 DEB 已构建", "离线安装已演练", "目标机已验证", "发布前证据门禁已闭合", "客户单 DEB 已发布"):
                    self.assertIn(label, content)
                target_row = next(line for line in content.splitlines() if line.startswith("| 目标机已验证 |"))
                self.assertIn("模型对话", target_row)
                self.assertIn("附件", target_row)
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertRegex(runbook, r"新建用户.{0,80}(不能|不足以).{0,60}首次安装")
        self.assertIn("系统安装基线", runbook)
        self.assertIn("用户配置基线", runbook)

    def test_active_skill_does_not_recommend_stale_tmp_build_root(self) -> None:
        active_files = SOURCE_FILES - {"references/kylin-deb-version-history.md", "evals/evals.json"}
        for relative in sorted(active_files):
            content = (SKILL_ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("/tmp/taiji-agent-build-", content, relative)


class DoctorContractTests(unittest.TestCase):
    def test_repo_mode_uses_operator_path_ignores_git_pollution_and_never_executes_repo_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, sentinel = make_repo_fixture(Path(temporary))
            result = run_python(
                DOCTOR,
                "--repo",
                str(repo),
                env={"GIT_DIR": "/definitely/not/the/fixture", "GIT_WORK_TREE": "/wrong"},
            )
            report = parse_report(result)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(report["schema"], "taiji-kylin-packaging-doctor/v1")
            self.assertEqual(report["mode"], "repo")
            self.assertEqual(report["compatibility_status"], "pass")
            self.assertEqual(report["blockers"], [])
            self.assertIn("prepare-builder-input", report["approval_required"])
            self.assertEqual(
                report["next_action"],
                {
                    "action": "prepare-builder-input",
                    "cwd": str(repo),
                    "argv": [
                        "/bin/bash",
                        "-p",
                        "taijiagent 打包交付/99_本机_准备制包输入包.sh",
                    ],
                },
            )
            self.assertNotIn("00_制包机", json.dumps(report["next_action"], ensure_ascii=False))
            self.assertFalse(sentinel.exists(), "doctor executed an operator-supplied repository script")

    def test_repo_mode_disables_repository_fsmonitor_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary).resolve(strict=True) / "parent with space"
            temporary_root.mkdir()
            repo, _sentinel = make_repo_fixture(temporary_root)
            fsmonitor_sentinel = temporary_root / "fsmonitor-executed"
            fsmonitor_hook = temporary_root / "fsmonitor-hook.sh"
            fsmonitor_hook.write_text(
                "#!/bin/sh\nprintf invoked > "
                + shlex.quote(str(fsmonitor_sentinel))
                + "\nprintf 'token\\n'\n",
                encoding="utf-8",
            )
            fsmonitor_hook.chmod(0o700)
            git(repo, "config", "core.fsmonitor", str(fsmonitor_hook))

            result = run_python(DOCTOR, "--repo", str(repo))
            report = parse_report(result)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(report["compatibility_status"], "pass")
            self.assertFalse(fsmonitor_sentinel.exists(), "doctor executed repository core.fsmonitor")

    def test_dirty_repo_is_blocked_without_running_repo_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, sentinel = make_repo_fixture(Path(temporary))
            (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
            result = run_python(DOCTOR, "--repo", str(repo))
            report = parse_report(result)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["compatibility_status"], "blocked")
            self.assertIn("REPO_DIRTY", {item["code"] for item in report["blockers"]})
            self.assertFalse(sentinel.exists())

    def test_clean_non_main_repo_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, _sentinel = make_repo_fixture(Path(temporary))
            git(repo, "checkout", "-q", "-b", "feature")
            result = run_python(DOCTOR, "--repo", str(repo))
            report = parse_report(result)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["compatibility_status"], "blocked")
            self.assertIn("REPO_NOT_MAIN", {item["code"] for item in report["blockers"]})

    def test_clean_detached_repo_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, _sentinel = make_repo_fixture(Path(temporary))
            git(repo, "checkout", "-q", "--detach")
            result = run_python(DOCTOR, "--repo", str(repo))
            report = parse_report(result)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["compatibility_status"], "blocked")
            self.assertIn("REPO_NOT_MAIN", {item["code"] for item in report["blockers"]})

    def test_repo_authority_must_be_tracked_by_head_not_only_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, _sentinel = make_repo_fixture(Path(temporary))
            relative = EXPECTED_INTERFACE["build_host_entry"]
            git(repo, "rm", "-q", "--", relative)
            git(repo, "commit", "-q", "-m", "remove authority from HEAD")
            entry = repo / relative
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
            git(repo, "add", "--", relative)

            result = run_python(DOCTOR, "--repo", str(repo))
            report = parse_report(result)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["compatibility_status"], "unsupported")
            self.assertIn("REPO_UNSUPPORTED", {item["code"] for item in report["blockers"]})
            self.assertIn("not tracked in HEAD", report["blockers"][0]["message"])

    def test_input_dir_recognizes_exact_no_git_trio_without_claiming_formal_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_dir = make_input_fixture(Path(temporary) / "input")
            result = run_python(DOCTOR, "--input-dir", str(input_dir))
            report = parse_report(result)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(report["mode"], "input-dir")
            self.assertEqual(report["compatibility_status"], "pass")
            self.assertIn("formal-integrity-not-run", report["unverified"])
            self.assertEqual(report["next_action"]["argv"][:3], ["sha256sum", "-c", "--"])
            self.assertEqual(report["approval_required"], [])

    def test_input_dir_rejects_duplicate_json_keys_and_extra_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            duplicate = make_input_fixture(Path(temporary) / "duplicate", duplicate_manifest_key=True)
            duplicate_result = run_python(DOCTOR, "--input-dir", str(duplicate))
            duplicate_report = parse_report(duplicate_result)
            self.assertEqual(duplicate_result.returncode, 2)
            self.assertIn("INPUT_MANIFEST_INVALID", {item["code"] for item in duplicate_report["blockers"]})

            extra = make_input_fixture(Path(temporary) / "extra")
            (extra / "old-package.deb").write_bytes(b"stale")
            extra_result = run_python(DOCTOR, "--input-dir", str(extra))
            extra_report = parse_report(extra_result)
            self.assertEqual(extra_result.returncode, 2)
            self.assertIn("INPUT_SET_INVALID", {item["code"] for item in extra_report["blockers"]})

    def test_input_dir_rejects_noncanonical_sidecar_entries(self) -> None:
        mutations = {
            "third-line": lambda text: text + ("0" * 64) + "  stale.deb\n",
            "path-entry": lambda text: text.replace("  taijiagent-", "  ../taijiagent-", 1),
            "duplicate-basename": lambda text: "\n".join([text.splitlines()[0], text.splitlines()[0]]) + "\n",
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                input_dir = make_input_fixture(Path(temporary) / "input")
                checksum = next(input_dir.glob("*.sha256"))
                checksum.write_text(mutate(checksum.read_text(encoding="utf-8")), encoding="utf-8")
                result = run_python(DOCTOR, "--input-dir", str(input_dir))
                report = parse_report(result)
                self.assertEqual(result.returncode, 2)
                self.assertIn("INPUT_CHECKSUM_INVALID", {item["code"] for item in report["blockers"]})

    def test_selftest_is_isolated_and_passes(self) -> None:
        result = run_python(DOCTOR, "--selftest")
        report = parse_report(result)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["mode"], "selftest")
        self.assertEqual(report["compatibility_status"], "pass")
        self.assertEqual(report["blockers"], [])

    def test_invalid_cli_still_returns_public_json(self) -> None:
        result = run_python(DOCTOR)
        report = parse_report(result)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["mode"], "unknown")
        self.assertEqual(report["compatibility_status"], "unsupported")
        self.assertIn("INVALID_ARGUMENTS", {item["code"] for item in report["blockers"]})

    def test_invalid_operator_path_is_a_public_unsupported_result(self) -> None:
        result = run_python(DOCTOR, "--repo", ".")
        report = parse_report(result)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["mode"], "repo")
        self.assertEqual(report["compatibility_status"], "unsupported")
        self.assertIn("REPO_UNSUPPORTED", {item["code"] for item in report["blockers"]})

    def test_repo_rejects_noncanonical_and_symlink_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary).resolve(strict=True)
            real_parent = temporary_root / "real"
            real_parent.mkdir()
            repo, _sentinel = make_repo_fixture(real_parent)
            detour = real_parent / "detour"
            detour.mkdir()
            parent_link = temporary_root / "parent-link"
            parent_link.symlink_to(real_parent, target_is_directory=True)
            candidates = {
                "dot-dot": detour / ".." / repo.name,
                "symlink-parent": parent_link / repo.name,
            }
            for name, candidate in candidates.items():
                with self.subTest(name=name):
                    result = run_python(DOCTOR, "--repo", str(candidate))
                    report = parse_report(result)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(report["mode"], "repo")
                    self.assertEqual(report["compatibility_status"], "unsupported")
                    self.assertIn("REPO_UNSUPPORTED", {item["code"] for item in report["blockers"]})
                    self.assertEqual(
                        result.stdout,
                        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                    )

    def test_input_dir_rejects_noncanonical_and_symlink_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary).resolve(strict=True)
            real_parent = temporary_root / "real"
            real_parent.mkdir()
            input_dir = make_input_fixture(real_parent / "input")
            detour = real_parent / "detour"
            detour.mkdir()
            parent_link = temporary_root / "parent-link"
            parent_link.symlink_to(real_parent, target_is_directory=True)
            candidates = {
                "dot-dot": detour / ".." / input_dir.name,
                "symlink-parent": parent_link / input_dir.name,
            }
            for name, candidate in candidates.items():
                with self.subTest(name=name):
                    result = run_python(DOCTOR, "--input-dir", str(candidate))
                    report = parse_report(result)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(report["mode"], "input-dir")
                    self.assertEqual(report["compatibility_status"], "blocked")
                    self.assertIn("INPUT_SET_INVALID", {item["code"] for item in report["blockers"]})
                    self.assertEqual(
                        result.stdout,
                        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                    )

    def test_stdout_is_one_canonical_json_document_and_stderr_is_empty(self) -> None:
        result = run_python(DOCTOR, "--selftest")
        report = parse_report(result)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        )

    def test_every_report_has_exact_public_fields(self) -> None:
        result = run_python(DOCTOR, "--selftest")
        report = parse_report(result)
        self.assertEqual(
            set(report),
            {
                "schema",
                "mode",
                "compatibility_status",
                "evidence_scope",
                "blockers",
                "next_action",
                "approval_required",
                "unverified",
            },
        )


class SkillPackagerContractTests(unittest.TestCase):
    def test_package_is_exact_verified_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first"
            second = Path(temporary) / "second"
            first_result = run_python(PACKAGER, "--skill-root", str(SKILL_ROOT), "--output-dir", str(first))
            second_result = run_python(PACKAGER, "--skill-root", str(SKILL_ROOT), "--output-dir", str(second))
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)

            artifact = first / "taiji-kylin-packaging.skill"
            second_artifact = second / artifact.name
            self.assertEqual(artifact.read_bytes(), second_artifact.read_bytes())
            expected_names = [f"taiji-kylin-packaging/{name}" for name in sorted(PACKAGE_FILES)]
            with zipfile.ZipFile(artifact) as bundle:
                self.assertEqual(bundle.namelist(), expected_names)
                self.assertTrue(all(item.compress_type == zipfile.ZIP_STORED for item in bundle.infolist()))
                self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in bundle.infolist()))

            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            self.assertEqual(
                (first / f"{artifact.name}.sha256").read_text(encoding="ascii"),
                f"{digest}  {artifact.name}\n",
            )
            inventory = json.loads(
                (first / f"{artifact.name}.inventory.json").read_text(encoding="utf-8")
            )
            self.assertEqual(inventory["schema"], "taiji-skill-package-inventory/v1")
            self.assertEqual(inventory["artifact"]["sha256"], digest)
            self.assertEqual(
                [entry["path"] for entry in inventory["members"]],
                expected_names,
            )

    def test_packager_rejects_extra_secret_symlink_hardlink_and_existing_output(self) -> None:
        mutations = {
            "extra-secret": lambda root, outside: (root / ".env").write_text("TOKEN=secret", encoding="utf-8"),
            "symlink": lambda root, outside: (root / "references/release-gates.md").symlink_to(outside),
            "hardlink": lambda root, outside: os.link(outside, root / "references/release-gates.md"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                temporary_root = Path(temporary)
                copied = temporary_root / "taiji-kylin-packaging"
                shutil.copytree(SKILL_ROOT, copied)
                outside = temporary_root / "outside.txt"
                outside.write_text("outside", encoding="utf-8")
                if name in {"symlink", "hardlink"}:
                    (copied / "references/release-gates.md").unlink()
                mutate(copied, outside)
                output = temporary_root / "output"
                result = run_python(PACKAGER, "--skill-root", str(copied), "--output-dir", str(output))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((output / "taiji-kylin-packaging.skill").exists())

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "reserved-output"
            output.mkdir()
            result = run_python(PACKAGER, "--skill-root", str(SKILL_ROOT), "--output-dir", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output directory must not already exist", result.stderr)
            self.assertEqual(list(output.iterdir()), [])

    def test_packager_rejects_private_key_and_development_path_leaks(self) -> None:
        mutations = {
            "private-key": "\n-----BEGIN " + "PRIVATE KEY-----\n",
            "development-path": "\n/Users/example/private-worktree\n",
        }
        for name, leaked_text in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                temporary_root = Path(temporary)
                copied = temporary_root / "taiji-kylin-packaging"
                shutil.copytree(SKILL_ROOT, copied)
                skill = copied / "SKILL.md"
                skill.write_text(skill.read_text(encoding="utf-8") + leaked_text, encoding="utf-8")
                output = temporary_root / "output"
                result = run_python(PACKAGER, "--skill-root", str(copied), "--output-dir", str(output))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("sensitive content", result.stderr)
                self.assertFalse((output / "taiji-kylin-packaging.skill").exists())

    def test_packager_rejects_output_inside_skill_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "taiji-kylin-packaging"
            shutil.copytree(SKILL_ROOT, copied)
            output = copied / "dist"
            output.mkdir()
            result = run_python(PACKAGER, "--skill-root", str(copied), "--output-dir", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside the Skill source tree", result.stderr)
            self.assertFalse((output / "taiji-kylin-packaging.skill").exists())


if __name__ == "__main__":
    unittest.main()
