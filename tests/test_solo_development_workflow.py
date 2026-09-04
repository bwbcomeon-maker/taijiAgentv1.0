from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
SAFETY = ROOT / "scripts" / "check-local-change-safety.py"
VERIFY = ROOT / "scripts" / "verify.sh"
RELEASE_CHECK = ROOT / "scripts" / "release-check.sh"
CLASSIFIER = ROOT / "scripts" / "classify-ci-scope.py"
OLD_RUNBOOK = ROOT / "docs" / "runbooks" / "github-pr-ci-workflow.md"
LIFECYCLE = ROOT / "docs" / "runbooks" / "development-lifecycle.md"
SOLO_RUNBOOK = ROOT / "docs" / "runbooks" / "solo-development-workflow.md"
TAIJI_RELEASE_CHECK = ROOT / "scripts" / "taiji-release-check.sh"

MAX_SAFETY_FILE_BYTES = 1024 * 1024
MAX_SAFETY_TOTAL_BYTES = 4 * 1024 * 1024
MAX_SAFETY_CHANGE_ENTRIES = 1024
TAIJI_RELEASE_CHECK_SHA256 = "321ef6555afc8fb56500331b05e3778a690353864afcf76316e3ef9f0cd69b15"

BASELINE_SHELL_FILES = (
    "packaging/linux/deb/preinst",
    "packaging/linux/deb/postinst",
    "packaging/linux/deb/prerm",
    "packaging/linux/deb/postrm",
    "packaging/linux/deb/publish-single-deb.sh",
    "packaging/linux/bin/taiji-agent-acceptance",
    "scripts/sign-taiji-release-evidence.sh",
    "scripts/taiji-release-check.sh",
    "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
    "taijiagent 打包交付/01_制包机_发布预检.sh",
    "taijiagent 打包交付/02_目标终端_安装并验证.sh",
    "taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh",
    "scripts/verify.sh",
)

ACTIVE_RULE_FILES = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "docs" / "runbooks" / "development-lifecycle.md",
    SOLO_RUNBOOK,
    ROOT / "hermes-local-lab" / "sources" / "hermes-agent" / "AGENTS.md",
    ROOT / "hermes-local-lab" / "sources" / "hermes-webui" / "AGENTS.md",
)

GIT_LOCATOR_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _clean_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for name in GIT_LOCATOR_ENV:
        env.pop(name, None)
    env.pop("TAIJI_AGENT_PYTHON", None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env.update(overrides or {})
    return env


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=_clean_env(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            exc.stdout or "",
            (exc.stderr or "") + "\ncommand timed out after 30 seconds",
        )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], cwd=repo)


def _assert_command_ok(test: unittest.TestCase, result: subprocess.CompletedProcess[str]) -> None:
    test.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    result = _git(repo, "init", "-b", "main")
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    for key, value in (
        ("user.name", "Taiji Workflow Test"),
        ("user.email", "taiji-workflow@example.invalid"),
    ):
        result = _git(repo, "config", key, value)
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
    (repo / "README.md").write_text("fixture repository\n", encoding="utf-8")
    for args in (("add", "README.md"), ("commit", "-m", "initial fixture")):
        result = _git(repo, *args)
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _snapshot_worktree(repo: Path) -> dict[str, tuple[str, int, str | None]]:
    snapshot: dict[str, tuple[str, int, str | None]] = {}
    for path in sorted(repo.rglob("*")):
        relative = path.relative_to(repo)
        if relative.parts and relative.parts[0] == ".git":
            continue
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode):
            kind = "file"
            identity = hashlib.sha256(path.read_bytes()).hexdigest()
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            identity = None
        elif stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            identity = os.readlink(path)
        elif stat.S_ISFIFO(metadata.st_mode):
            kind = "fifo"
            identity = None
        else:
            kind = "other"
            identity = None
        snapshot[str(relative)] = (kind, mode, identity)
    return snapshot


class SoloDevelopmentWorkflowContracts(unittest.TestCase):
    maxDiff = None

    def _read_required(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"required file is missing: {path.relative_to(ROOT)}")
        return path.read_text(encoding="utf-8")

    def _require_script(self, path: Path) -> None:
        self.assertTrue(path.is_file(), f"required script is missing: {path.relative_to(ROOT)}")

    def _install_safety_scanner(self, repo: Path) -> Path:
        self._require_script(SAFETY)
        target = repo / "scripts" / "check-local-change-safety.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(SAFETY.read_bytes())
        target.chmod(0o755)
        _assert_command_ok(self, _git(repo, "add", "scripts/check-local-change-safety.py"))
        _assert_command_ok(self, _git(repo, "commit", "-m", "install safety scanner"))
        return target

    def _run_safety(
        self,
        repo: Path,
        scanner: Path,
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return _run([sys.executable, str(scanner)], cwd=repo, env=env)

    def _install_verify_fixture(self, repo: Path, *, browser_smoke: bool = False) -> Path:
        self._require_script(VERIFY)
        self._require_script(CLASSIFIER)
        scripts = repo / "scripts"
        scripts.mkdir(exist_ok=True)
        verify = scripts / "verify.sh"
        verify.write_bytes(VERIFY.read_bytes())
        verify.chmod(0o755)
        classifier = scripts / "classify-ci-scope.py"
        classifier.write_bytes(CLASSIFIER.read_bytes())
        safety = scripts / "check-local-change-safety.py"
        safety.write_text("print('local change safety: PASS')\n", encoding="utf-8")
        for relative in BASELINE_SHELL_FILES:
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text("#!/bin/sh\n", encoding="utf-8")
        if browser_smoke:
            smoke = repo / "hermes-local-lab" / "sources" / "hermes-webui" / "tests" / "browser_smoke.py"
            smoke.parent.mkdir(parents=True)
            smoke.write_text("raise SystemExit(99)\n", encoding="utf-8")
        _assert_command_ok(self, _git(repo, "add", "--all"))
        _assert_command_ok(self, _git(repo, "commit", "-m", "install verify fixture"))
        return verify

    @staticmethod
    def _secret_token() -> str:
        return "gh" + "p_" + ("Z" * 40)

    def _make_release_repo(
        self,
        base: Path,
        *,
        tag: str,
        version: str = "1.2.3",
        desktop_version: str = "1.2.3",
        annotated: bool = True,
        empty_notes: bool = False,
        dirty: bool = False,
        branch: str = "main",
        advance_after_tag: bool = False,
        verify_exit: int = 0,
        ignored_artifacts: bool = False,
    ) -> tuple[Path, Path, dict[str, str]]:
        self._require_script(RELEASE_CHECK)
        repo = base / "repo"
        _init_repo(repo)
        (repo / "apps" / "taiji-desktop").mkdir(parents=True)
        (repo / "scripts").mkdir(exist_ok=True)
        (repo / "VERSION").write_text(version + "\n", encoding="utf-8")
        (repo / "apps" / "taiji-desktop" / "package.json").write_text(
            json.dumps({"name": "fixture-desktop", "version": desktop_version}) + "\n",
            encoding="utf-8",
        )
        notes = repo / "release-notes.md"
        notes.write_text("   \n" if empty_notes else "Release notes for the fixture.\n", encoding="utf-8")
        (repo / "scripts" / "release-check.sh").write_bytes(RELEASE_CHECK.read_bytes())
        (repo / "scripts" / "release-check.sh").chmod(0o755)
        (repo / "scripts" / "verify.sh").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$VERIFY_LOG\"\n"
            "exit \"${VERIFY_EXIT:-0}\"\n",
            encoding="utf-8",
        )
        (repo / "scripts" / "verify.sh").chmod(0o755)
        (repo / ".gitignore").write_text("ignored-state/\n", encoding="utf-8")
        _assert_command_ok(
            self,
            _git(repo, "add", "VERSION", "apps", "scripts", "release-notes.md", ".gitignore"),
        )
        _assert_command_ok(self, _git(repo, "commit", "-m", "release fixture"))
        tag_args = ["tag"]
        if annotated:
            tag_args.extend(["-a", tag, "-m", f"fixture {tag}"])
        else:
            tag_args.append(tag)
        _assert_command_ok(self, _git(repo, *tag_args))
        if advance_after_tag:
            (repo / "after-tag.txt").write_text("new head\n", encoding="utf-8")
            _assert_command_ok(self, _git(repo, "add", "after-tag.txt"))
            _assert_command_ok(self, _git(repo, "commit", "-m", "advance after tag"))
        if branch != "main":
            _assert_command_ok(self, _git(repo, "switch", "-c", branch))
        if dirty:
            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        if ignored_artifacts:
            ignored = repo / "ignored-state"
            ignored.mkdir()
            (ignored / "payload.bin").write_bytes(b"ignored fixture payload\n")
            (ignored / "payload-link").symlink_to("payload.bin")
        verify_log = base / "verify.log"
        env = {
            "VERIFY_LOG": str(verify_log),
            "VERIFY_EXIT": str(verify_exit),
        }
        return repo, notes, env

    def _make_hotfix_release_worktree(
        self,
        base: Path,
        *,
        baseline_tag: str = "v1.2.3",
        candidate_tag: str = "v1.2.4",
        baseline_annotated: bool = True,
        create_baseline_tag: bool = True,
        candidate_from_baseline: bool = True,
    ) -> tuple[Path, Path, Path, dict[str, str]]:
        self._require_script(RELEASE_CHECK)
        primary = base / "primary"
        _init_repo(primary)
        baseline_version = baseline_tag.removeprefix("v").split("-rc.", 1)[0]
        candidate_version = candidate_tag.removeprefix("v").split("-rc.", 1)[0]
        (primary / "apps" / "taiji-desktop").mkdir(parents=True)
        (primary / "scripts").mkdir(exist_ok=True)
        (primary / "VERSION").write_text(baseline_version + "\n", encoding="utf-8")
        (primary / "apps" / "taiji-desktop" / "package.json").write_text(
            json.dumps({"name": "fixture-desktop", "version": baseline_version}) + "\n",
            encoding="utf-8",
        )
        (primary / "release-notes.md").write_text(
            "Published baseline release notes.\n", encoding="utf-8"
        )
        (primary / "scripts" / "release-check.sh").write_bytes(RELEASE_CHECK.read_bytes())
        (primary / "scripts" / "release-check.sh").chmod(0o755)
        (primary / "scripts" / "verify.sh").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$VERIFY_LOG\"\n"
            "exit \"${VERIFY_EXIT:-0}\"\n",
            encoding="utf-8",
        )
        (primary / "scripts" / "verify.sh").chmod(0o755)
        _assert_command_ok(self, _git(primary, "add", "VERSION", "apps", "scripts", "release-notes.md"))
        _assert_command_ok(self, _git(primary, "commit", "-m", "published baseline fixture"))
        if create_baseline_tag:
            tag_args = ["tag"]
            if baseline_annotated:
                tag_args.extend(["-a", baseline_tag, "-m", f"published {baseline_tag}"])
            else:
                tag_args.append(baseline_tag)
            _assert_command_ok(self, _git(primary, *tag_args))

        start_ref = baseline_tag if create_baseline_tag and candidate_from_baseline else "HEAD^"
        if not candidate_from_baseline and start_ref == "HEAD^":
            # The initial fixture commit deliberately excludes the release baseline.
            pass
        elif not create_baseline_tag:
            start_ref = "HEAD"
        linked = base / "hotfix-worktree"
        _assert_command_ok(
            self,
            _git(primary, "worktree", "add", "-b", "hotfix/fixture", str(linked), start_ref),
        )
        (linked / "apps" / "taiji-desktop").mkdir(parents=True, exist_ok=True)
        (linked / "scripts").mkdir(exist_ok=True)
        (linked / "VERSION").write_text(candidate_version + "\n", encoding="utf-8")
        (linked / "apps" / "taiji-desktop" / "package.json").write_text(
            json.dumps({"name": "fixture-desktop", "version": candidate_version}) + "\n",
            encoding="utf-8",
        )
        notes = linked / "release-notes.md"
        notes.write_text("Hotfix release notes.\n", encoding="utf-8")
        (linked / "scripts" / "release-check.sh").write_bytes(RELEASE_CHECK.read_bytes())
        (linked / "scripts" / "release-check.sh").chmod(0o755)
        (linked / "scripts" / "verify.sh").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$VERIFY_LOG\"\n"
            "exit \"${VERIFY_EXIT:-0}\"\n",
            encoding="utf-8",
        )
        (linked / "scripts" / "verify.sh").chmod(0o755)
        (linked / "hotfix.txt").write_text("single hotfix fixture\n", encoding="utf-8")
        _assert_command_ok(self, _git(linked, "add", "--all"))
        _assert_command_ok(self, _git(linked, "commit", "-m", "hotfix candidate fixture"))
        _assert_command_ok(
            self,
            _git(linked, "tag", "-a", candidate_tag, "-m", f"candidate {candidate_tag}"),
        )
        env = {
            "VERIFY_LOG": str(base / "verify.log"),
            "VERIFY_EXIT": "0",
        }
        return primary, linked, notes, env

    def _run_release_check(
        self,
        repo: Path,
        notes: Path,
        tag: str,
        env: dict[str, str],
        *,
        hotfix_from: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "/bin/bash",
            str(repo / "scripts" / "release-check.sh"),
            "--tag",
            tag,
            "--release-notes",
            str(notes.resolve()),
        ]
        if hotfix_from is not None:
            command.extend(["--hotfix-from", hotfix_from])
        return _run(command, cwd=repo, env=env)

    def test_active_rules_define_direct_main_and_release_identity(self):
        texts = [self._read_required(path) for path in ACTIVE_RULE_FILES[:4]]
        combined = "\n".join(texts)
        self.assertRegex(combined, r"main.{0,40}(日常开发主线|daily development line)")
        for text in texts:
            self.assertNotRegex(text, r"最新本地验证的开发版本|latest locally verified development line")
        self.assertRegex(combined, r"验证结论.{0,60}(提交|commit).{0,60}工作树")
        self.assertRegex(combined, r"main.{0,80}(不等同|not equivalent).{0,80}(稳定|stable)")
        self.assertIn("vX.Y.Z-rc.N", combined)
        self.assertIn("vX.Y.Z", combined)
        self.assertRegex(combined, r"(?i)annotated\s+tag|附注标签")
        self.assertRegex(combined, r"(?i)GitHub Release.{0,80}(正式|stable).{0,80}(tag|标签)")
        self.assertRegex(
            combined,
            r"(?is)(未明确限定|unless.{0,30}explicitly limits).{0,160}(local-only).{0,300}(commit).{0,200}(fetch|刷新远端).{0,200}(push).{0,40}main",
        )
        self.assertRegex(combined, r"(?is)按标准收尾.{0,160}(快捷|shortcut).{0,100}(不是|not).{0,80}(额外|additional).{0,60}(授权|permission)")
        for command in (
            "git status --short",
            "git diff",
            "git diff --cached --name-status",
            "git diff --cached --check",
            "git diff --cached",
        ):
            with self.subTest(command=command):
                self.assertIn(command, combined)
        self.assertRegex(combined, r"(?is)(staged bytes|暂存字节).{0,120}(变化|change).{0,120}(重新审核|fresh review|重审)")

    def test_governance_routes_analysis_and_shared_writer_without_expanding_authority(self):
        for path in (ROOT / "AGENTS.md", LIFECYCLE, SOLO_RUNBOOK):
            text = self._read_required(path)
            with self.subTest(path=path):
                self.assertRegex(text, r"仅分析.{0,80}(不触发|不执行).{0,40}commit/push")
                self.assertRegex(text, r"共享工作树.{0,60}Git index.{0,100}(唯一|一个).{0,20}写入")
                self.assertRegex(text, r"(轮次|次数)上限.{0,100}停止提交")
        lifecycle = self._read_required(LIFECYCLE)
        self.assertRegex(lifecycle, r"根因未确认.{0,100}两次")
        self.assertRegex(lifecycle, r"旧记忆.{0,80}(不能|不得).{0,40}覆盖")
        self.assertRegex(lifecycle, r"Sol.{0,80}(不可用|无法获得).{0,80}提交前")

    def test_project_entry_routes_to_existing_project_skills_and_platform_runbooks(self):
        path = ROOT / "AGENTS.md"
        text = self._read_required(path)
        targets = re.findall(r"\]\(([^)]+)\)", text)
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target):
                self.assertNotIn("://", target, "project rule entry must use repository-owned references")
                self.assertTrue((path.parent / unquote(target.split("#", 1)[0])).is_file())
        for skill in ("taiji-kylin-packaging", "frontend-ux-qa"):
            self.assertIn(f".agents/skills/{skill}/SKILL.md", targets)
        self.assertIn("docs/runbooks/taiji-windows-candidate-pipeline.md", targets)
        self.assertRegex(text, r"同名全局 Skill.{0,80}(不|不得).{0,20}替代")

    def test_frontend_contract_scope_preserves_ui_regression_severity(self):
        skill = ROOT / ".agents/skills/frontend-ux-qa"
        for relative in (
            "SKILL.md", "references/feature-contract-template.md",
            "references/frontend-ux-rubric.md", "references/subagent-review-template.md",
        ):
            text = self._read_required(skill / relative)
            with self.subTest(file=relative):
                for concept in ("功能契约", "内部 API", "CLI", "授权角色", "既有产品回归", "P0", "P1"):
                    self.assertIn(concept, text)
                self.assertNotIn("每个用户可感知能力必须", text)
                self.assertNotIn("如果代码存在某个 action", text)
        entry = self._read_required(skill / "SKILL.md")
        self.assertRegex(entry, r"纯规则文档.{0,60}不触发")
        self.assertIn("真实浏览器测试：未验证", entry)
        self.assertIn("中文《前端 UX QA 报告》", entry)

    def test_active_rules_have_no_mandatory_daily_branch_worktree_pr_or_ci(self):
        texts = {path: self._read_required(path) for path in ACTIVE_RULE_FILES}
        combined = "\n".join(texts.values())
        forbidden = (
            "正常开发不得直接在 `main`",
            "需要修改任何仓库文件 | 修改前从最新 `main` 创建",
            "新建一个分支、一个 worktree，最终对应一个 PR",
            "每个独立成果拥有自己的分支、worktree",
            "正常流程中，PR 的唯一合并门禁",
            "不得直接 push 远端 `main`",
            "Keep one logical change per PR",
        )
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, combined)
        root_scope = "\n".join(texts[path] for path in ACTIVE_RULE_FILES[:4])
        self.assertRegex(root_scope, r"main.{0,40}(直接开发|direct development)")
        self.assertRegex(root_scope, r"(?i)(CI|Main Validation).{0,100}(非强制|non-required|异步|asynchronous)")

    def test_branch_worktree_exceptions_require_explicit_user_approval(self):
        for path in (LIFECYCLE, SOLO_RUNBOOK):
            text = self._read_required(path)
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertRegex(
                    text,
                    r"(?is)(分支/worktree|branch/worktree).{0,180}"
                    r"(事先|开始前).{0,60}用户.{0,60}(明确授权|明确批准)",
                )
                self.assertRegex(
                    text,
                    r"(?is)(列举|符合).{0,100}(不构成|不等于).{0,80}(授权|批准)",
                )

    def test_stable_hotfix_starts_from_released_tag_and_closes_back_to_main(self):
        sections = {
            LIFECYCLE: self._read_required(LIFECYCLE).split("## 8.", 1)[1].split("## 9.", 1)[0],
            SOLO_RUNBOOK: self._read_required(SOLO_RUNBOOK).split("## 8.", 1)[1].split("## 9.", 1)[0],
        }
        ordered_steps = (
            "从已发布稳定 Tag 创建临时 branch/worktree",
            "只实施单一 hotfix",
            "完成完整验证和 Sol 审核",
            "另行授权创建修订版 annotated Tag",
            "另行授权创建对应 GitHub Release",
            "将相同修复同步回 `main`",
            "审计后删除临时 branch/worktree",
        )
        for path, section in sections.items():
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertRegex(
                    section,
                    r"(?is)hotfix.{0,200}已发布稳定 Tag.{0,160}"
                    r"(不得|不能).{0,80}`origin/main`",
                )
                self.assertRegex(
                    section,
                    r"(?is)不得.{0,100}`main`.{0,100}(未发布|尚未发布).{0,80}(功能|改动)",
                )
                positions = [section.index(step) for step in ordered_steps]
                self.assertEqual(positions, sorted(positions))

    def test_github_release_identity_notes_and_assets_are_immutable(self):
        for path in (LIFECYCLE, SOLO_RUNBOOK):
            text = self._read_required(path)
            with self.subTest(path=path.relative_to(ROOT)):
                for contract in (
                    "GitHub Release 的 name 必须与稳定 Tag 完全一致",
                    "Release Notes 以及安装或升级说明",
                    "资产必须来自该 Tag",
                    "SHA256",
                    "已发布资产不得静默覆盖",
                    "发布新的修订版本",
                ):
                    self.assertIn(contract, text)

    def test_tag_authorization_and_release_preflight_have_non_circular_order(self):
        lifecycle = self._read_required(LIFECYCLE)
        lifecycle_state6 = lifecycle.split("### 状态 6：", 1)[1].split("## 3.", 1)[0]
        solo_state7 = self._read_required(SOLO_RUNBOOK).split("## 7.", 1)[1].split("## 8.", 1)[0]
        ordered_steps = (
            "Tag 前候选检查",
            "取得具体 Tag 的明确授权",
            "创建 annotated Tag",
            "Tag 创建后",
            "scripts/release-check.sh",
            "推送 Tag",
            "创建 GitHub Release",
        )
        for name, section in (
            ("development lifecycle", lifecycle_state6),
            ("solo runbook", solo_state7),
        ):
            with self.subTest(document=name):
                positions = [section.index(step) for step in ordered_steps]
                self.assertEqual(positions, sorted(positions))
                self.assertIn("clean main", section)
                self.assertIn("scripts/verify.sh --full", section)
                self.assertRegex(
                    section,
                    r"(?is)Tag 前候选检查.{0,500}"
                    r"scripts/verify\.sh --full.{0,500}"
                    r"Tag 创建后.{0,500}scripts/release-check\.sh",
                )

        authorization = lifecycle.split("## 3.", 1)[1].split("## 4.", 1)[0]
        self.assertIn("已完成 Tag 前候选检查", authorization)
        self.assertIn("已通过 Tag 后 `scripts/release-check.sh` 复验", authorization)
        self.assertNotIn("为已通过发布预检的明确 commit", authorization)
        self.assertNotIn("授权前只运行只读预检", solo_state7)

    def test_old_pr_runbook_is_replaced_one_for_one(self):
        self.assertFalse(OLD_RUNBOOK.exists(), "old PR/CI runbook still exists")
        self.assertTrue(SOLO_RUNBOOK.is_file(), "solo-development runbook is missing")

    def test_nested_agent_rules_defer_to_taiji_root_scope(self):
        agent = self._read_required(ACTIVE_RULE_FILES[4])
        webui = self._read_required(ACTIVE_RULE_FILES[5])
        for name, text in (("agent", agent), ("webui", webui)):
            with self.subTest(component=name):
                self.assertIn("Taiji", text)
                self.assertIn("AGENTS.md", text)
                self.assertIn("docs/runbooks/development-lifecycle.md", text)
                self.assertRegex(text, r"(?i)(优先|wins|takes precedence).{0,100}(Git|workflow|流程)")
        self.assertNotIn("git reset --hard", agent)
        self.assertNotIn("Keep one logical change per PR", webui)

    def test_change_safety_scans_staged_unstaged_and_untracked(self):
        secret = self._secret_token()
        for state in ("staged", "unstaged", "untracked"):
            with self.subTest(state=state), tempfile.TemporaryDirectory(
                prefix=f"taiji-safety-{state}-"
            ) as temp_dir:
                repo = Path(temp_dir) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                target = repo / f"{state}.env"
                if state == "unstaged":
                    target.write_text("GITHUB_TOKEN=fixture-safe\n", encoding="utf-8")
                    _assert_command_ok(self, _git(repo, "add", target.name))
                    _assert_command_ok(self, _git(repo, "commit", "-m", "tracked safe value"))
                target.write_text(f"GITHUB_TOKEN={secret}\n", encoding="utf-8")
                if state == "staged":
                    _assert_command_ok(self, _git(repo, "add", target.name))
                result = self._run_safety(repo, scanner)
                output = _combined_output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(target.name, output)
                self.assertNotIn(secret, output)

        for deletion_state in ("unstaged", "staged"):
            with self.subTest(deletion=deletion_state), tempfile.TemporaryDirectory(
                prefix=f"taiji-safety-deletion-{deletion_state}-"
            ) as temp_dir:
                repo = Path(temp_dir) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                old = repo / "docs" / "runbooks" / "github-pr-ci-workflow.md"
                old.parent.mkdir(parents=True)
                old.write_text("old workflow\n", encoding="utf-8")
                relative = str(old.relative_to(repo))
                _assert_command_ok(self, _git(repo, "add", relative))
                _assert_command_ok(self, _git(repo, "commit", "-m", "add old runbook"))
                old.unlink()
                if deletion_state == "staged":
                    _assert_command_ok(self, _git(repo, "add", "-u", "--", relative))
                result = self._run_safety(repo, scanner)
                self.assertEqual(result.returncode, 0, _combined_output(result))
                self.assertIn("PASS", _combined_output(result))

    def test_change_safety_detects_embedded_credentials_in_all_change_views(self):
        assignment_value = "live_" + ("T" * 36)
        high_confidence_value = self._secret_token()
        encrypted_marker = "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----"
        cases = (
            (
                "staged-json",
                "staged",
                "quoted.json",
                json.dumps({"api_" + "token": assignment_value}) + "\n",
                "credential-assignment",
                assignment_value,
            ),
            (
                "unstaged-export",
                "unstaged",
                "export.env",
                "export API_" + "TOKEN='" + assignment_value + "'\n",
                "credential-assignment",
                assignment_value,
            ),
            (
                "untracked-yaml",
                "untracked",
                "quoted.yaml",
                "'" + "client_" + "secret': '" + assignment_value + "'\n",
                "credential-assignment",
                assignment_value,
            ),
            (
                "untracked-url",
                "untracked",
                "callback.txt",
                "https://example.invalid/callback?access=" + high_confidence_value + "\n",
                "high-confidence-token",
                high_confidence_value,
            ),
            (
                "untracked-encrypted-pem",
                "untracked",
                "encrypted.pem",
                encrypted_marker + "\n" + ("A" * 64) + "\n",
                "private-key",
                encrypted_marker,
            ),
        )
        for name, state, relative, content, finding, sensitive_value in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(
                prefix=f"taiji-safety-embedded-{name}-"
            ) as temp_dir:
                repo = Path(temp_dir) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                target = repo / relative
                if state == "unstaged":
                    target.write_text("fixture-safe\n", encoding="utf-8")
                    _assert_command_ok(self, _git(repo, "add", "--", relative))
                    _assert_command_ok(self, _git(repo, "commit", "-m", "track safe fixture"))
                target.write_text(content, encoding="utf-8")
                if state == "staged":
                    _assert_command_ok(self, _git(repo, "add", "--", relative))
                safe = repo / "safe-template.env"
                safe.write_text(
                    "export API_" + "TOKEN=${TOKEN_NAME}\n"
                    "callback=https://example.invalid/?value=ghp_short\n",
                    encoding="utf-8",
                )

                result = self._run_safety(repo, scanner)
                output = _combined_output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(relative, output)
                self.assertIn(finding, output)
                self.assertNotIn(sensitive_value, output)
                self.assertNotIn(safe.name, output)

    def test_change_safety_grandfathers_only_unchanged_tracked_credential_values(self):
        baseline_value = "historical_" + ("B" * 32)
        new_value = "newly_added_" + ("N" * 32)
        with tempfile.TemporaryDirectory(prefix="taiji-safety-baseline-credential-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            target = repo / "config.py"
            target.write_text(
                f'API_KEY = "{baseline_value}"\nSETTING = "before"\n',
                encoding="utf-8",
            )
            _assert_command_ok(self, _git(repo, "add", target.name))
            _assert_command_ok(self, _git(repo, "commit", "-m", "track historical fixture"))

            target.write_text(
                f'API_KEY = "{baseline_value}"\nSETTING = "after"\n',
                encoding="utf-8",
            )
            unchanged_baseline = self._run_safety(repo, scanner)
            self.assertEqual(
                unchanged_baseline.returncode,
                0,
                _combined_output(unchanged_baseline),
            )
            self.assertIn("PASS", _combined_output(unchanged_baseline))

            target.write_text(
                f'API_KEY = "{baseline_value}"\n'
                f'CLIENT_SECRET = "{new_value}"\n'
                'SETTING = "after"\n',
                encoding="utf-8",
            )
            newly_added = self._run_safety(repo, scanner)
            output = _combined_output(newly_added)
            self.assertNotEqual(newly_added.returncode, 0, output)
            self.assertIn("credential-assignment", output)
            self.assertNotIn(baseline_value, output)
            self.assertNotIn(new_value, output)

            target.write_text(
                f'API_KEY = "{new_value}"\nSETTING = "after"\n',
                encoding="utf-8",
            )
            replaced = self._run_safety(repo, scanner)
            output = _combined_output(replaced)
            self.assertNotEqual(replaced.returncode, 0, output)
            self.assertIn("credential-assignment", output)
            self.assertNotIn(new_value, output)

    def test_change_safety_keeps_index_and_worktree_views_separate(self):
        secret = self._secret_token()
        cases = (
            "staged-secret-worktree-safe",
            "staged-secret-worktree-deleted",
            "staged-deletion-untracked-secret",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                prefix=f"taiji-safety-two-view-{case}-"
            ) as temp_dir:
                repo = Path(temp_dir) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                target = repo / "two-view.env"
                if case == "staged-deletion-untracked-secret":
                    target.write_text("GITHUB_TOKEN=fixture-safe\n", encoding="utf-8")
                    _assert_command_ok(self, _git(repo, "add", target.name))
                    _assert_command_ok(self, _git(repo, "commit", "-m", "track safe fixture"))
                    target.unlink()
                    _assert_command_ok(self, _git(repo, "add", "-u", "--", target.name))
                    target.write_text(f"GITHUB_TOKEN={secret}\n", encoding="utf-8")
                else:
                    target.write_text(f"GITHUB_TOKEN={secret}\n", encoding="utf-8")
                    _assert_command_ok(self, _git(repo, "add", target.name))
                    if case == "staged-secret-worktree-safe":
                        target.write_text("GITHUB_TOKEN=fixture-safe\n", encoding="utf-8")
                    else:
                        target.unlink()
                result = self._run_safety(repo, scanner)
                output = _combined_output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(target.name, output)
                self.assertNotIn(secret, output)

    def test_change_safety_scans_large_tracked_source_through_end(self):
        padding = "# " + "x" * (MAX_SAFETY_FILE_BYTES + 64) + "\n"
        historic = "historical_" + "B" * 32
        for staged in (False, True):
            with self.subTest(staged=staged), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                target = repo / "large.py"
                baseline = padding + f'API_KEY = "{historic}"\n'
                target.write_text(baseline)
                _assert_command_ok(self, _git(repo, "add", target.name))
                _assert_command_ok(self, _git(repo, "commit", "-m", "existing large source"))
                target.write_text(baseline + "VALUE = 2\n")
                if staged:
                    _assert_command_ok(self, _git(repo, "add", target.name))
                result = self._run_safety(repo, scanner)
                self.assertEqual(result.returncode, 0, _combined_output(result))
                for suffix, finding in (
                    ('NEW_TOKEN = "' + "live_" + "N" * 32 + '"\n', "credential-assignment"),
                    ('# ' + self._secret_token() + '\n', "high-confidence-token"),
                    ('# -----BEGIN ' + 'PRIVATE KEY-----\n', "private-key"),
                    ('def broken(\n', "python-parse-error"),
                ):
                    target.write_text(baseline + suffix)
                    if staged:
                        _assert_command_ok(self, _git(repo, "add", target.name))
                        # A clean worktree must not hide unsafe staged bytes.
                        target.write_text(baseline + "VALUE = 3\n")
                    result = self._run_safety(repo, scanner)
                    self.assertNotEqual(result.returncode, 0, _combined_output(result))
                    self.assertIn(finding, _combined_output(result))
                    self.assertNotIn(historic, _combined_output(result))

    def test_change_safety_large_source_keeps_new_file_and_total_bounds(self):
        for kind in ("untracked", "new-staged", "tracked-nonsource", "tracked-oversize", "tracked-total"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                repo = Path(temp) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                target = repo / ("large.txt" if kind == "tracked-nonsource" else "large.py")
                target.write_text("# initial\n")
                if kind.startswith("tracked"):
                    _assert_command_ok(self, _git(repo, "add", target.name))
                    _assert_command_ok(self, _git(repo, "commit", "-m", "track fixture"))
                size = MAX_SAFETY_TOTAL_BYTES + 1 if kind == "tracked-oversize" else MAX_SAFETY_FILE_BYTES + 64
                target.write_text("#" + "x" * size + "\n")
                if kind == "new-staged":
                    _assert_command_ok(self, _git(repo, "add", target.name))
                if kind == "tracked-total":
                    target.write_text("#" + "x" * (MAX_SAFETY_TOTAL_BYTES // 2) + "\n")
                    _assert_command_ok(self, _git(repo, "add", target.name))
                    target.write_text(target.read_text() + "# changed\n")
                result = self._run_safety(repo, scanner)
                self.assertNotEqual(result.returncode, 0, _combined_output(result))
                expected = "total-size-limit" if kind == "tracked-total" else "file-size-limit"
                self.assertIn(expected, _combined_output(result))

    def test_change_safety_rejects_non_stage_zero_symlink_and_gitlink_index_entries(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-index-mode-") as temp_dir:
            base = Path(temp_dir)

            symlink_repo = base / "symlink"
            _init_repo(symlink_repo)
            scanner = self._install_safety_scanner(symlink_repo)
            link = symlink_repo / "staged-link"
            link.symlink_to("README.md")
            _assert_command_ok(self, _git(symlink_repo, "add", link.name))
            link.unlink()
            link.write_text("safe worktree replacement\n", encoding="utf-8")
            result = self._run_safety(symlink_repo, scanner)
            self.assertNotEqual(result.returncode, 0, _combined_output(result))
            self.assertIn(link.name, _combined_output(result))
            self.assertIn("unsupported-index-mode", _combined_output(result))

            gitlink_repo = base / "gitlink"
            _init_repo(gitlink_repo)
            scanner = self._install_safety_scanner(gitlink_repo)
            oid = _git(gitlink_repo, "rev-parse", "HEAD").stdout.strip()
            _assert_command_ok(
                self,
                _git(
                    gitlink_repo,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"160000,{oid},gitlink-entry",
                ),
            )
            result = self._run_safety(gitlink_repo, scanner)
            self.assertNotEqual(result.returncode, 0, _combined_output(result))
            self.assertIn("gitlink-entry", _combined_output(result))
            self.assertIn("unsupported-index-mode", _combined_output(result))

            conflict_repo = base / "conflict"
            _init_repo(conflict_repo)
            scanner = self._install_safety_scanner(conflict_repo)
            conflict = conflict_repo / "conflict.txt"
            conflict.write_text("base\n", encoding="utf-8")
            _assert_command_ok(self, _git(conflict_repo, "add", conflict.name))
            _assert_command_ok(self, _git(conflict_repo, "commit", "-m", "conflict base"))
            _assert_command_ok(self, _git(conflict_repo, "switch", "-c", "conflict-side"))
            conflict.write_text("side\n", encoding="utf-8")
            _assert_command_ok(self, _git(conflict_repo, "commit", "-am", "side"))
            _assert_command_ok(self, _git(conflict_repo, "switch", "main"))
            conflict.write_text("main\n", encoding="utf-8")
            _assert_command_ok(self, _git(conflict_repo, "commit", "-am", "main"))
            merge = _git(conflict_repo, "merge", "conflict-side")
            self.assertNotEqual(merge.returncode, 0, _combined_output(merge))
            result = self._run_safety(conflict_repo, scanner)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn(conflict.name, output)
            self.assertIn("unmerged-index-entry", output)

    def test_change_safety_uses_bounded_race_checked_reads_and_caps_entries(self):
        source = self._read_required(SAFETY)
        for token in (
            "MAX_CHANGE_ENTRIES",
            "limit + 1",
            "os.O_NOFOLLOW",
            "os.open",
            "os.fstat",
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "cat-file",
            "change-set-raced",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertGreaterEqual(source.count('"--no-renames"'), 4, source)

        with tempfile.TemporaryDirectory(prefix="taiji-safety-entry-limit-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            for index in range(MAX_SAFETY_CHANGE_ENTRIES + 1):
                (repo / f"entry-{index:04d}.txt").touch()
            result = self._run_safety(repo, scanner)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("change-set-entry-limit", output)

    def test_change_safety_entry_limit_counts_staged_and_unstaged_deletions(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-deletion-entry-limit-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            tracked = repo / "tracked"
            tracked.mkdir()
            paths = []
            for index in range(MAX_SAFETY_CHANGE_ENTRIES + 1):
                path = tracked / f"entry-{index:04d}.txt"
                path.write_text("safe tracked fixture\n", encoding="utf-8")
                paths.append(path)
            _assert_command_ok(self, _git(repo, "add", "--", "tracked"))
            _assert_command_ok(self, _git(repo, "commit", "-m", "track deletion fixtures"))

            midpoint = MAX_SAFETY_CHANGE_ENTRIES // 2
            for path in paths[:midpoint]:
                path.unlink()
            _assert_command_ok(self, _git(repo, "add", "-u", "--", "tracked"))
            for path in paths[midpoint:MAX_SAFETY_CHANGE_ENTRIES]:
                path.unlink()

            boundary = self._run_safety(repo, scanner)
            self.assertEqual(boundary.returncode, 0, _combined_output(boundary))
            self.assertIn("PASS", _combined_output(boundary))

            paths[MAX_SAFETY_CHANGE_ENTRIES].unlink()
            overflow = self._run_safety(repo, scanner)
            output = _combined_output(overflow)
            self.assertNotEqual(overflow.returncode, 0, output)
            self.assertIn("change-set-entry-limit", output)

    def test_change_safety_ignores_ambient_git_locators(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-git-env-") as temp_dir:
            base = Path(temp_dir)
            repo = base / "target"
            decoy = base / "decoy"
            _init_repo(repo)
            _init_repo(decoy)
            scanner = self._install_safety_scanner(repo)
            secret = self._secret_token()
            target = repo / "must-scan.env"
            target.write_text(f"GITHUB_TOKEN={secret}\n", encoding="utf-8")
            result = self._run_safety(
                repo,
                scanner,
                env={
                    "GIT_DIR": str(decoy / ".git"),
                    "GIT_WORK_TREE": str(decoy),
                    "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
                },
            )
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn(target.name, output)
            self.assertNotIn(secret, output)

    def test_change_safety_handles_ignored_forced_special_and_size_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-ignored-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            (repo / ".gitignore").write_text("ignored-state/\n", encoding="utf-8")
            _assert_command_ok(self, _git(repo, "add", ".gitignore"))
            _assert_command_ok(self, _git(repo, "commit", "-m", "ignore fixture state"))
            secret = self._secret_token()
            ignored = repo / "ignored-state" / "credential.env"
            ignored.parent.mkdir(parents=True)
            ignored.write_text(f"GITHUB_TOKEN={secret}\n", encoding="utf-8")
            ignored_result = self._run_safety(repo, scanner)
            self.assertEqual(ignored_result.returncode, 0, _combined_output(ignored_result))
            self.assertNotIn(ignored.name, _combined_output(ignored_result))
            _assert_command_ok(self, _git(repo, "add", "-f", "--", str(ignored.relative_to(repo))))
            forced_result = self._run_safety(repo, scanner)
            forced_output = _combined_output(forced_result)
            self.assertNotEqual(forced_result.returncode, 0, forced_output)
            self.assertIn(str(ignored.relative_to(repo)), forced_output)
            self.assertNotIn(secret, forced_output)

        for special in ("symlink", "fifo"):
            with self.subTest(special=special), tempfile.TemporaryDirectory(
                prefix=f"taiji-safety-{special}-"
            ) as temp_dir:
                repo = Path(temp_dir) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                target = repo / f"untracked-{special}"
                if special == "symlink":
                    target.symlink_to("README.md")
                else:
                    os.mkfifo(target)
                result = self._run_safety(repo, scanner)
                output = _combined_output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(target.name, output)

        with tempfile.TemporaryDirectory(prefix="taiji-safety-single-limit-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            secret = self._secret_token()
            oversized = repo / "oversized.txt"
            with oversized.open("wb") as handle:
                handle.write(secret.encode("ascii"))
                handle.truncate(MAX_SAFETY_FILE_BYTES + 1)
            result = self._run_safety(repo, scanner)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("file-size-limit", output)
            self.assertNotIn(secret, output)

        with tempfile.TemporaryDirectory(prefix="taiji-safety-total-limit-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            for index in range(4):
                with (repo / f"sparse-{index}.txt").open("wb") as handle:
                    handle.truncate(MAX_SAFETY_FILE_BYTES)
            with (repo / "sparse-overflow.txt").open("wb") as handle:
                handle.truncate(1)
            result = self._run_safety(repo, scanner)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("total-size-limit", output)

    def test_change_safety_rejects_private_material_and_obvious_outputs(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-reject-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            secret = self._secret_token()
            private_key = "-----BEGIN " + "PRIVATE KEY-----\n" + ("A" * 64) + "\n"
            parenthesized_password = "correct(" + "horse)battery-staple-long"
            braced_password = "correct{" + "horse}battery-staple-long"
            files = {
                "private.pem": private_key,
                "token.env": f"GITHUB_TOKEN={secret}\n",
                "parenthesized.env": f"PASSWORD={parenthesized_password}\n",
                "braced.env": f"PASSWORD={braced_password}\n",
                ".DS_Store": "finder metadata",
                "__pycache__/module.pyc": "bytecode",
                "runtime.log": "runtime output",
                "coverage/coverage.json": "{}",
                "bundle.tar.gz": "archive",
                "installer.exe": "installer",
                "candidate.deb": "package",
            }
            for relative, content in files.items():
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            result = self._run_safety(repo, scanner)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            for relative in files:
                with self.subTest(path=relative):
                    self.assertIn(relative, output)
            self.assertNotIn(secret, output)
            self.assertNotIn(private_key.strip(), output)
            self.assertNotIn(parenthesized_password, output)
            self.assertNotIn(braced_password, output)

    def test_change_safety_distinguishes_python_references_from_literal_credentials(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-python-assignment-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            references = repo / "context_tokens.py"
            references.write_text(
                "from contextvars import ContextVar\n"
                "\n"
                "_CHALLENGE_TIME_WINDOW = ContextVar('challenge_time_window')\n"
                "token = _CHALLENGE_TIME_WINDOW.set(window_seconds)\n"
                "reference_token = _CHALLENGE_TIME_WINDOW\n"
                "window_token = resolve_context_window_token()\n"
                "token = f\"{runtime_token}\"\n",
                encoding="utf-8",
            )
            allowed = self._run_safety(repo, scanner)
            self.assertEqual(allowed.returncode, 0, _combined_output(allowed))
            self.assertIn("PASS", _combined_output(allowed))

            secret = self._secret_token()
            parenthesized_password = "correct(" + "horse)battery-staple-long"
            braced_password = "correct{" + "horse}battery-staple-long"
            staged_bytes = repo / "staged_bytes.py"
            staged_bytes.write_text(f'token = b"{secret}"\n', encoding="utf-8")
            _assert_command_ok(self, _git(repo, "add", staged_bytes.name))
            staged_bytes.write_text("token = runtime_token\n", encoding="utf-8")

            literal_credentials = {
                "literal_token.py": (f'token = "{secret}"\n', secret),
                "raw_token.py": ('token = r"raw-password-value-long"\n', "raw-password-value-long"),
                "unicode_token.py": (
                    'token = u"unicode-password-value-long"\n',
                    "unicode-password-value-long",
                ),
                "triple_token.py": (
                    'token = """triple-password-value-long"""\n',
                    "triple-password-value-long",
                ),
                "annotated_token.py": (
                    'token: str = "annotated-password-value-long"\n',
                    "annotated-password-value-long",
                ),
                "static_f_token.py": (f'token = f"{secret}"\n', secret),
                "chained_token.py": (
                    'token = reference_token = "chained-password-value-long"\n',
                    "chained-password-value-long",
                ),
                "parenthesized_password.py": (
                    f'PASSWORD = "{parenthesized_password}"\n',
                    parenthesized_password,
                ),
                "braced_password.py": (
                    f'PASSWORD = "{braced_password}"\n',
                    braced_password,
                ),
                "literal.env": (f"token={secret}\n", secret),
            }
            for relative, (content, _value) in literal_credentials.items():
                (repo / relative).write_text(content, encoding="utf-8")
            broken = repo / "broken_token.py"
            broken.write_text("token = unresolved_runtime_reference(\n", encoding="utf-8")

            rejected = self._run_safety(repo, scanner)
            output = _combined_output(rejected)
            self.assertNotEqual(rejected.returncode, 0, output)
            self.assertIn(staged_bytes.name, output)
            self.assertNotIn(secret, output)
            for relative, (_content, value) in literal_credentials.items():
                with self.subTest(path=relative):
                    self.assertIn(relative, output)
                    self.assertNotIn(value, output)
            self.assertIn(broken.name, output)
            self.assertIn("python-parse-error", output)

    def test_change_safety_allows_public_keys_and_test_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-allow-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            (repo / "public.pem").write_text(
                "-----BEGIN PUBLIC KEY-----\n" + ("A" * 64) + "\n-----END PUBLIC KEY-----\n",
                encoding="utf-8",
            )
            fixture = repo / "tests" / "fixtures" / "fake.env"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(
                "GITHUB_TOKEN=" + "TEST_ONLY_" + "FAKE_CREDENTIAL\n",
                encoding="utf-8",
            )
            quoted_fixture = repo / "quoted-test-marker.json"
            quoted_fixture.write_text(
                json.dumps(
                    {
                        "github_" + "token": "test-only-"
                        + "token-must-not-appear-in-output"
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            template = repo / "credential-template.env"
            template.write_text(
                "API_TOKEN=${TOKEN_NAME}\n"
                "CLIENT_SECRET={{VAULT_SECRET}}\n"
                "PASSWORD=<placeholder>\n"
                "ACCESS_KEY=self.fixture_access_key()\n",
                encoding="utf-8",
            )
            result = self._run_safety(repo, scanner)
            self.assertEqual(result.returncode, 0, _combined_output(result))
            self.assertIn("PASS", _combined_output(result))

    def test_change_safety_requires_explicit_test_only_placeholder_markers(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-explicit-test-only-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            explicit_values = {
                "underscore.env": "TE" + "ST_ONLY_" + ("U" * 32),
                "hyphen.env": "TE" + "ST-ONLY-" + ("H" * 32),
            }
            for relative, value in explicit_values.items():
                (repo / relative).write_text(f"PASSWORD={value}\n", encoding="utf-8")

            allowed = self._run_safety(repo, scanner)
            self.assertEqual(allowed.returncode, 0, _combined_output(allowed))
            self.assertIn("PASS", _combined_output(allowed))

            unsafe_values = {
                "bare-test.env": "TE" + "ST_" + ("S" * 32),
                "bare-testing.env": "TE" + "STING_" + ("G" * 32),
            }
            for relative, value in unsafe_values.items():
                (repo / relative).write_text(f"CLIENT_SECRET={value}\n", encoding="utf-8")

            rejected = self._run_safety(repo, scanner)
            output = _combined_output(rejected)
            self.assertNotEqual(rejected.returncode, 0, output)
            for relative, value in unsafe_values.items():
                with self.subTest(path=relative):
                    self.assertIn(relative, output)
                    self.assertNotIn(value, output)

    def test_change_safety_does_not_exempt_real_secrets_under_test_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="taiji-safety-real-fixture-secret-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            scanner = self._install_safety_scanner(repo)
            values = {
                "github.env": ("GITHUB_TOKEN", self._secret_token()),
                "token.env": ("API_TOKEN", "live_" + ("T" * 36)),
                "private.env": ("PRIVATE_KEY", "private_" + ("K" * 36)),
            }
            fixture_root = repo / "tests" / "fixtures"
            fixture_root.mkdir(parents=True)
            for name, (key, value) in values.items():
                (fixture_root / name).write_text(f"{key}={value}\n", encoding="utf-8")
            result = self._run_safety(repo, scanner)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            for name, (_key, value) in values.items():
                self.assertIn(f"tests/fixtures/{name}", output)
                self.assertNotIn(value, output)

    def test_change_safety_allows_deleting_tracked_package_output(self):
        for state in ("unstaged", "staged"):
            with self.subTest(state=state), tempfile.TemporaryDirectory(
                prefix=f"taiji-safety-package-delete-{state}-"
            ) as temp_dir:
                repo = Path(temp_dir) / "repo"
                _init_repo(repo)
                scanner = self._install_safety_scanner(repo)
                package = repo / "miscommitted.deb"
                package.write_bytes(b"old package output")
                _assert_command_ok(self, _git(repo, "add", package.name))
                _assert_command_ok(self, _git(repo, "commit", "-m", "track old package"))
                package.unlink()
                if state == "staged":
                    _assert_command_ok(self, _git(repo, "add", "-u", "--", package.name))
                result = self._run_safety(repo, scanner)
                self.assertEqual(result.returncode, 0, _combined_output(result))
                self.assertIn("PASS", _combined_output(result))

    def test_verify_help_plan_and_real_suite_contracts(self):
        self._require_script(VERIFY)
        expected_python = ROOT / "hermes-local-lab" / "sources" / "hermes-agent" / "venv" / "bin" / "python"
        self.assertTrue(expected_python.is_file(), f"canonical prepared Python missing: {expected_python}")
        help_result = _run(["/bin/bash", str(VERIFY), "--help"], cwd=ROOT)
        self.assertEqual(help_result.returncode, 0, _combined_output(help_result))
        help_output = _combined_output(help_result).lower()
        for token in (
            "default",
            "--full",
            "--plan",
            "--browser-smoke",
            "all registered taiji local gates",
            "root",
            "desktop",
            "docx",
            "agent",
            "webui",
            "branding",
            "bootstrap",
            "coexistence",
            "local-change-safety",
            "hermes-local-lab/sources/hermes-webui/tests/browser_smoke.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, help_output)

        plan_result = _run(["/bin/bash", str(VERIFY), "--plan", "--full"], cwd=ROOT)
        self.assertEqual(plan_result.returncode, 0, _combined_output(plan_result))
        plan_output = _combined_output(plan_result)
        self.assertIn(f"INTERPRETER\troot-webui-browser={expected_python}", plan_output)
        agent_root = ROOT / "hermes-local-lab" / "sources" / "hermes-agent"
        self.assertIn(f"RUNNER\tagent={agent_root / 'scripts' / 'run_tests.sh'} (self-resolving)", plan_output)
        python = str(expected_python)
        baseline_shell_files = list(BASELINE_SHELL_FILES)
        if RELEASE_CHECK.is_file():
            baseline_shell_files.append("scripts/release-check.sh")
        expected_plan = [
            f"PLAN\tlocal-change-safety\tcwd=.\targv={python} scripts/check-local-change-safety.py",
            "PLAN\tbaseline-diff-unstaged\tcwd=.\targv=git diff --check",
            "PLAN\tbaseline-diff-staged\tcwd=.\targv=git diff --cached --check",
            f"PLAN\tbaseline-shell\tcwd=.\targv=/bin/bash -n {' '.join(baseline_shell_files)}",
            f"PLAN\troot\tcwd=.\targv={python} -m unittest discover -s tests -p test_*.py",
            "PLAN\tdesktop-check\tcwd=apps/taiji-desktop\targv=npm run check",
            "PLAN\tdesktop-node\tcwd=apps/taiji-desktop\targv=node --test tests/*.test.js",
            "PLAN\tdocx\tcwd=hermes-local-lab/sources/docx-engine-v2\targv=npm test",
            "PLAN\tagent\tcwd=hermes-local-lab/sources/hermes-agent\targv=scripts/run_tests.sh tests/tools/test_taiji_security_mode.py tests/test_taiji_license.py tests/gateway/test_api_server_license.py tests/gateway/test_session_api.py tests/tools/test_image_generation_readiness.py tests/tools/test_public_chat_brand_guard.py",
            "PLAN\twebui-lint\tcwd=hermes-local-lab/sources/hermes-webui\targv=npm run lint:runtime",
            f"PLAN\twebui-tests\tcwd=hermes-local-lab/sources/hermes-webui\targv={python} -m pytest -q tests/test_brand_privacy.py tests/test_model_config_api.py tests/test_model_config_frontend.py tests/test_model_config_refresh.py tests/test_main_model_verification.py tests/test_approval_queue.py tests/test_approval_sse.py tests/test_pr1350_sse_notify_correctness.py tests/test_expert_team_frontend.py tests/test_expert_team_frontend_v2.py tests/test_expert_team_frontend_v3.py tests/test_managed_dialog_static.py tests/test_onboarding_static.py tests/test_ui_visibility_config.py tests/test_issue1800_file_html_interactions.py tests/test_writeflow_frontend.py::test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell tests/test_issue1116_composer_placeholder.py",
            "PLAN\tbranding-agent\tcwd=hermes-local-lab/sources/hermes-agent\targv=scripts/run_tests.sh tests/test_cli_skin_integration.py tests/cli/test_cli_skin_integration.py",
            "PLAN\tbootstrap-agent\tcwd=hermes-local-lab/sources/hermes-agent\targv=scripts/run_tests.sh tests/test_hermes_bootstrap.py",
            f"PLAN\tbootstrap-webui\tcwd=hermes-local-lab/sources/hermes-webui\targv={python} -m pytest -q tests/test_bootstrap_discover_agent.py tests/test_bootstrap_dotenv.py tests/test_bootstrap_foreground.py tests/test_bootstrap_python_selection.py",
            f"PLAN\tcoexistence-webui\tcwd=hermes-local-lab/sources/hermes-webui\targv={python} -m pytest -q tests/test_taiji_single_runtime_profiles.py",
        ]
        plan_lines = [line for line in plan_output.splitlines() if line.startswith("PLAN\t")]
        self.assertEqual(plan_lines, expected_plan)

        with tempfile.TemporaryDirectory(prefix="taiji-verify-clean-plan-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            fixture_verify = self._install_verify_fixture(repo)
            before = _snapshot_worktree(repo)
            fallback = _run(["/bin/bash", str(fixture_verify), "--plan"], cwd=repo)
            self.assertEqual(fallback.returncode, 0, _combined_output(fallback))
            self.assertEqual(_snapshot_worktree(repo), before, "--plan changed the fixture worktree")
            labels = [
                line.split("\t", 2)[1]
                for line in _combined_output(fallback).splitlines()
                if line.startswith("PLAN\t")
            ]
            self.assertEqual(
                labels,
                [
                    "local-change-safety",
                    "baseline-diff-unstaged",
                    "baseline-diff-staged",
                    "baseline-shell",
                    "root",
                ],
            )

        for component, relative in (
            ("agent", "hermes-local-lab/sources/hermes-agent/local-change.py"),
            ("webui", "hermes-local-lab/sources/hermes-webui/local-change.py"),
        ):
            with self.subTest(default_component=component), tempfile.TemporaryDirectory(
                prefix=f"taiji-verify-default-{component}-"
            ) as temp_dir:
                repo = Path(temp_dir) / "repo"
                _init_repo(repo)
                fixture_verify = self._install_verify_fixture(repo)
                changed = repo / relative
                changed.parent.mkdir(parents=True, exist_ok=True)
                changed.write_text("local change\n", encoding="utf-8")
                result = _run(["/bin/bash", str(fixture_verify), "--plan"], cwd=repo)
                output = _combined_output(result)
                self.assertEqual(result.returncode, 0, output)
                labels = [
                    line.split("\t", 2)[1]
                    for line in output.splitlines()
                    if line.startswith("PLAN\t")
                ]
                selected_label = "agent" if component == "agent" else "webui-lint"
                self.assertIn(selected_label, labels)
                for extra in (
                    "branding-agent",
                    "bootstrap-agent",
                    "bootstrap-webui",
                    "coexistence-webui",
                ):
                    self.assertIn(extra, labels)

    def test_verify_offline_modes_are_bash32_and_do_not_mutate_environment(self):
        self._require_script(VERIFY)
        source = VERIFY.read_text(encoding="utf-8")
        executable_lines = []
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("printf"):
                continue
            executable_lines.append(stripped)
        executable = "\n".join(executable_lines)
        forbidden = (
            r"\bnpm\s+(?:install|ci)\b",
            r"\b(?:pip|pip3)\s+install\b",
            r"\buv\s+sync\b",
            r"\b(?:curl|wget|ssh|scp)\b",
            r"\b(?:launchctl|systemctl)\b",
            r"(?:^|[;&|]\s*)[^#\n]*(?:start-agent|start-webui|health-check)\.sh\b",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, executable), executable)
        bash32_forbidden = (
            r"\bdeclare\s+-A\b",
            r"\bmapfile\b",
            r"\breadarray\b",
            r"\$\{[^}\n]+(?:,,|\^\^)[^}\n]*\}",
            r"\|&",
        )
        for pattern in bash32_forbidden:
            with self.subTest(bash32=pattern):
                self.assertIsNone(re.search(pattern, source), source)
        self.assertIn("--local-changes", source)
        self.assertIn('parser.add_argument("--path"', self._read_required(CLASSIFIER))
        self.assertEqual(source.count("hermes-local-lab/sources/hermes-webui/tests/browser_smoke.py"), 1)
        for arguments in (
            ("-n", str(VERIFY)),
            (str(VERIFY), "--help"),
            (str(VERIFY), "--plan", "--full"),
        ):
            result = _run(["/bin/bash", *arguments], cwd=ROOT)
            self.assertEqual(result.returncode, 0, _combined_output(result))

    def test_verify_baseline_is_safety_first_and_missing_files_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="taiji-verify-baseline-missing-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            _init_repo(repo)
            fixture_verify = self._install_verify_fixture(repo)
            missing = repo / BASELINE_SHELL_FILES[0]
            missing.unlink()
            result = _run(
                ["/bin/bash", str(fixture_verify)],
                cwd=repo,
                env={"TAIJI_AGENT_PYTHON": sys.executable},
            )
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("local change safety: PASS", output)
            self.assertIn("verification prerequisite missing: baseline shell file", output)
            self.assertIn(BASELINE_SHELL_FILES[0], output)
            self.assertNotIn("verification: PASS", output)

        with tempfile.TemporaryDirectory(prefix="taiji-verify-classifier-fail-") as temp_dir:
            repo = Path(temp_dir) / "not-a-repo"
            scripts = repo / "scripts"
            scripts.mkdir(parents=True)
            fixture_verify = scripts / "verify.sh"
            fixture_verify.write_bytes(VERIFY.read_bytes())
            fixture_verify.chmod(0o755)
            (scripts / "classify-ci-scope.py").write_bytes(CLASSIFIER.read_bytes())
            (scripts / "check-local-change-safety.py").write_text(
                "print('local change safety: PASS')\n", encoding="utf-8"
            )
            result = _run(["/bin/bash", str(fixture_verify), "--plan"], cwd=repo)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("local change scope classification failed", output.lower())
            self.assertFalse(any(line.startswith("PLAN\t") for line in output.splitlines()), output)

    def test_verify_preflights_all_selected_suites_before_running_any_suite(self):
        source = self._read_required(VERIFY)
        self.assertIn(
            'require_directory "$ROOT/$DESKTOP_REL/node_modules/acorn" "Desktop acorn module"',
            source,
        )
        for dependency in (
            "tests/tools/test_public_chat_brand_guard.py",
            "tests/test_brand_privacy.py",
            "tests/test_bootstrap_python_selection.py",
            "tests/test_taiji_single_runtime_profiles.py",
        ):
            with self.subTest(preflight_dependency=dependency):
                self.assertGreaterEqual(source.count(dependency), 3, source)

        with tempfile.TemporaryDirectory(prefix="taiji-verify-preflight-") as temp_dir:
            base = Path(temp_dir)
            repo = base / "repo"
            _init_repo(repo)
            fixture_verify = self._install_verify_fixture(repo)
            (repo / "tests").mkdir()
            desktop = repo / "apps/taiji-desktop"
            (desktop / "node_modules").mkdir(parents=True)
            (desktop / "package.json").write_text("{}\n", encoding="utf-8")
            python_log = base / "python-calls.log"
            python_stub = base / "python-stub"
            python_stub.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = \"-m\" ] && [ \"${2:-}\" = \"unittest\" ]; then\n"
                "  printf '%s\\n' \"$*\" >> \"$PYTHON_CALL_LOG\"\n"
                "fi\n"
                f"exec {shlex_quote(sys.executable)} \"$@\"\n",
                encoding="utf-8",
            )
            python_stub.chmod(0o755)
            result = _run(
                ["/bin/bash", str(fixture_verify), "--full"],
                cwd=repo,
                env={
                    "TAIJI_AGENT_PYTHON": str(python_stub),
                    "PYTHON_CALL_LOG": str(python_log),
                },
            )
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("local change safety: PASS", output)
            self.assertIn("verification prerequisite missing: Desktop acorn module", output)
            self.assertIn("apps/taiji-desktop/node_modules/acorn", output)
            self.assertFalse(python_log.exists(), "root suite ran before selected-suite preflight")

    def test_verify_rejects_incompatible_node_before_test_execution(self):
        with tempfile.TemporaryDirectory(prefix="taiji-node-version-") as temp_dir:
            base = Path(temp_dir)
            repo = base / "repo"
            _init_repo(repo)
            verify = self._install_verify_fixture(repo)
            (repo / "tests").mkdir()
            binaries = base / "bin"
            binaries.mkdir()
            node = binaries / "node"
            node.write_text("#!/bin/sh\nprintf 'v26.8.1\\n'\n", encoding="utf-8")
            node.chmod(0o755)
            result = _run(
                ["/bin/bash", str(verify), "--full"], cwd=repo,
                env={"TAIJI_AGENT_PYTHON": sys.executable, "PATH": str(binaries) + os.pathsep + os.environ["PATH"]},
            )
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("unsupported Node.js version", output)
            self.assertIn("v26.8.1", output)
            self.assertNotIn("verification: PASS", output)

    def test_verify_reports_separate_python_and_agent_runner_resolution(self):
        self._require_script(VERIFY)
        source = VERIFY.read_text(encoding="utf-8")
        venv_position = source.find("hermes-agent/venv/bin/python")
        dot_venv_position = source.find("hermes-agent/.venv/bin/python")
        self.assertGreaterEqual(venv_position, 0, source)
        self.assertGreater(dot_venv_position, venv_position, source)
        self.assertIn("TAIJI_AGENT_PYTHON", source)
        self.assertIn("scripts/run_tests.sh", source)
        self.assertNotRegex(source, r"TAIJI_AGENT_PYTHON=.*run_tests\.sh")
        with tempfile.TemporaryDirectory(prefix="taiji-verify-python-override-") as temp_dir:
            override = Path(temp_dir) / "explicit-python"
            override.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            override.chmod(0o755)
            result = _run(
                ["/bin/bash", str(VERIFY), "--plan", "--full"],
                cwd=ROOT,
                env={"TAIJI_AGENT_PYTHON": str(override)},
            )
            output = _combined_output(result)
            self.assertEqual(result.returncode, 0, output)
            self.assertIn(f"INTERPRETER\troot-webui-browser={override}", output)
            self.assertIn("RUNNER\tagent=", output)
            branding_lines = [
                line for line in output.splitlines() if line.startswith("PLAN\tbranding-agent\t")
            ]
            self.assertEqual(len(branding_lines), 1, output)
            self.assertNotIn(str(override), branding_lines[0])

    def test_verify_binds_every_webui_pytest_to_repository_agent_runtime(self):
        source = self._read_required(VERIFY)
        webui_start = source.index("run_webui() {")
        extra_start = source.index("run_extra() {", webui_start)
        browser_start = source.index("run_browser_smoke() {", extra_start)
        run_webui = source[webui_start:extra_start]
        run_extra = source[extra_start:browser_start]
        binding = re.compile(
            r'HERMES_WEBUI_AGENT_DIR="\$ROOT/\$AGENT_REL"\s+\\\n\s*'
            r'HERMES_WEBUI_PYTHON="\$ROOT_PYTHON"\s+\\\n\s*'
            r'"\$ROOT_PYTHON" -m pytest'
        )

        self.assertEqual(1, run_webui.count('"$ROOT_PYTHON" -m pytest'))
        self.assertEqual(1, len(binding.findall(run_webui)), run_webui)
        self.assertEqual(2, run_extra.count('"$ROOT_PYTHON" -m pytest'))
        self.assertEqual(2, len(binding.findall(run_extra)), run_extra)

    def test_browser_smoke_missing_playwright_and_exit_passthrough(self):
        self._require_script(VERIFY)
        with tempfile.TemporaryDirectory(prefix="taiji-browser-smoke-contract-") as temp_dir:
            base = Path(temp_dir)
            repo = base / "repo"
            _init_repo(repo)
            fixture_verify = self._install_verify_fixture(repo, browser_smoke=True)
            smoke = repo / "hermes-local-lab" / "sources" / "hermes-webui" / "tests" / "browser_smoke.py"
            call_log = base / "smoke-calls.log"
            stub = base / "python-stub"
            stub.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    if [ "$1" = "-c" ] && [ "$2" = "import playwright" ]; then
                      exit "${{PLAYWRIGHT_IMPORT_EXIT:-0}}"
                    fi
                    if [ "$1" = "$SMOKE_PATH" ]; then
                      printf '%s\\n' "$*" >> "$SMOKE_CALL_LOG"
                      exit "${{SMOKE_EXIT:-0}}"
                    fi
                    exec {shlex_quote(sys.executable)} "$@"
                    """
                ),
                encoding="utf-8",
            )
            stub.chmod(0o755)
            common_env = {
                "TAIJI_AGENT_PYTHON": str(stub),
                "SMOKE_PATH": str(smoke),
                "SMOKE_CALL_LOG": str(call_log),
            }

            missing = _run(
                ["/bin/bash", str(fixture_verify), "--browser-smoke"],
                cwd=repo,
                env={**common_env, "PLAYWRIGHT_IMPORT_EXIT": "1"},
            )
            missing_output = _combined_output(missing)
            self.assertEqual(missing.returncode, 3, missing_output)
            self.assertIn("browser smoke prerequisite missing: Python Playwright", missing_output)
            self.assertFalse(call_log.exists(), "smoke ran despite missing Playwright")
            self.assertNotIn("BROWSER SMOKE PASSED", missing_output)
            self.assertNotIn("verification: PASS", missing_output)

            for expected_exit in (0, 1, 2):
                with self.subTest(smoke_exit=expected_exit):
                    if call_log.exists():
                        call_log.unlink()
                    result = _run(
                        ["/bin/bash", str(fixture_verify), "--browser-smoke"],
                        cwd=repo,
                        env={**common_env, "SMOKE_EXIT": str(expected_exit)},
                    )
                    output = _combined_output(result)
                    self.assertEqual(result.returncode, expected_exit, output)
                    calls = call_log.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(calls, [str(smoke)])
                    if expected_exit:
                        self.assertNotIn("BROWSER SMOKE PASSED", output)
                        self.assertNotIn("verification: PASS", output)

    def test_release_check_requires_clean_main_annotated_tag_versions_notes_and_full_verify(self):
        self._require_script(RELEASE_CHECK)
        with tempfile.TemporaryDirectory(prefix="taiji-release-help-") as temp_dir:
            help_result = _run(["/bin/bash", str(RELEASE_CHECK), "--help"], cwd=Path(temp_dir))
            self.assertEqual(help_result.returncode, 0, _combined_output(help_result))

        positive = (("stable", "v1.2.3"), ("rc", "v1.2.3-rc.1"))
        for name, tag in positive:
            with self.subTest(case=name), tempfile.TemporaryDirectory(
                prefix=f"taiji-release-{name}-"
            ) as temp_dir:
                repo, notes, env = self._make_release_repo(Path(temp_dir), tag=tag)
                result = self._run_release_check(repo, notes, tag, env)
                self.assertEqual(result.returncode, 0, _combined_output(result))
                verify_log = Path(env["VERIFY_LOG"])
                self.assertEqual(verify_log.read_text(encoding="utf-8").splitlines(), ["--full"])

        negative = (
            ("lightweight", {"tag": "v1.2.3", "annotated": False}),
            ("stable-version-mismatch", {"tag": "v1.2.4"}),
            ("rc-base-version-mismatch", {"tag": "v1.2.4-rc.1"}),
            ("desktop-version-mismatch", {"tag": "v1.2.3", "desktop_version": "1.2.4"}),
            ("dirty", {"tag": "v1.2.3", "dirty": True}),
            ("non-main", {"tag": "v1.2.3", "branch": "release-test"}),
            ("moved-tag", {"tag": "v1.2.3", "advance_after_tag": True}),
            ("empty-notes", {"tag": "v1.2.3", "empty_notes": True}),
            ("invalid-rc-zero", {"tag": "v1.2.3-rc.0"}),
            ("failed-full-verify", {"tag": "v1.2.3", "verify_exit": 9}),
        )
        for name, options in negative:
            with self.subTest(case=name), tempfile.TemporaryDirectory(
                prefix=f"taiji-release-negative-{name}-"
            ) as temp_dir:
                tag = str(options["tag"])
                repo, notes, env = self._make_release_repo(Path(temp_dir), **options)
                result = self._run_release_check(repo, notes, tag, env)
                output = _combined_output(result)
                verify_log = Path(env["VERIFY_LOG"])
                if name == "failed-full-verify":
                    self.assertEqual(result.returncode, 9, output)
                    self.assertEqual(verify_log.read_text(encoding="utf-8").splitlines(), ["--full"])
                else:
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertFalse(verify_log.exists(), f"verify ran before {name} preflight failed")

    def test_release_check_accepts_declared_hotfix_in_real_linked_worktree(self):
        with tempfile.TemporaryDirectory(prefix="taiji-release-hotfix-positive-") as temp_dir:
            _primary, linked, notes, env = self._make_hotfix_release_worktree(Path(temp_dir))
            help_result = _run(
                ["/bin/bash", str(linked / "scripts" / "release-check.sh"), "--help"],
                cwd=linked,
            )
            self.assertEqual(help_result.returncode, 0, _combined_output(help_result))
            self.assertIn("--hotfix-from", _combined_output(help_result))

            result = self._run_release_check(
                linked,
                notes,
                "v1.2.4",
                env,
                hotfix_from="v1.2.3",
            )
            output = _combined_output(result)
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("hotfix", output.lower())
            self.assertEqual(Path(env["VERIFY_LOG"]).read_text(encoding="utf-8").splitlines(), ["--full"])

    def test_release_check_rejects_undeclared_hotfix_worktree(self):
        with tempfile.TemporaryDirectory(prefix="taiji-release-hotfix-undeclared-") as temp_dir:
            _primary, linked, notes, env = self._make_hotfix_release_worktree(Path(temp_dir))
            result = self._run_release_check(linked, notes, "v1.2.4", env)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("primary repository checkout", output)
            self.assertFalse(Path(env["VERIFY_LOG"]).exists(), "verify ran for undeclared hotfix")

    def test_release_check_rejects_invalid_hotfix_baseline_and_candidate_relationships(self):
        cases = (
            (
                "rc-baseline",
                {"baseline_tag": "v1.2.3-rc.1"},
                "v1.2.3-rc.1",
                "baseline tag must be a stable semantic version",
            ),
            (
                "missing-baseline",
                {"create_baseline_tag": False},
                "v1.2.3",
                "baseline tag does not exist",
            ),
            (
                "lightweight-baseline",
                {"baseline_annotated": False},
                "v1.2.3",
                "baseline tag must be annotated",
            ),
            (
                "unrelated-candidate",
                {"candidate_from_baseline": False},
                "v1.2.3",
                "baseline tag must be an ancestor of HEAD",
            ),
            (
                "older-candidate",
                {"candidate_tag": "v1.2.2"},
                "v1.2.3",
                "candidate tag must be a newer patch version",
            ),
            (
                "rc-candidate",
                {"candidate_tag": "v1.2.4-rc.1"},
                "v1.2.3",
                "hotfix candidate tag must be a stable semantic version",
            ),
        )
        for name, options, baseline_tag, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(
                prefix=f"taiji-release-hotfix-negative-{name}-"
            ) as temp_dir:
                _primary, linked, notes, env = self._make_hotfix_release_worktree(
                    Path(temp_dir), **options
                )
                candidate_tag = str(options.get("candidate_tag", "v1.2.4"))
                result = self._run_release_check(
                    linked,
                    notes,
                    candidate_tag,
                    env,
                    hotfix_from=baseline_tag,
                )
                output = _combined_output(result)
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn(expected, output)
                self.assertFalse(Path(env["VERIFY_LOG"]).exists(), f"verify ran for {name}")

        with tempfile.TemporaryDirectory(prefix="taiji-release-hotfix-negative-dirty-") as temp_dir:
            _primary, linked, notes, env = self._make_hotfix_release_worktree(Path(temp_dir))
            (linked / "dirty.txt").write_text("dirty hotfix\n", encoding="utf-8")
            result = self._run_release_check(
                linked,
                notes,
                "v1.2.4",
                env,
                hotfix_from="v1.2.3",
            )
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("hotfix worktree must be clean", output)
            self.assertFalse(Path(env["VERIFY_LOG"]).exists(), "verify ran for dirty hotfix")

    def test_release_check_rejects_linked_worktree_before_verification(self):
        with tempfile.TemporaryDirectory(prefix="taiji-release-linked-worktree-") as temp_dir:
            base = Path(temp_dir)
            primary, _notes, env = self._make_release_repo(base, tag="v1.2.3")
            _assert_command_ok(self, _git(primary, "switch", "-c", "parking"))
            linked = base / "linked-main"
            _assert_command_ok(self, _git(primary, "worktree", "add", str(linked), "main"))
            notes = linked / "release-notes.md"

            result = self._run_release_check(linked, notes, "v1.2.3", env)
            output = _combined_output(result)
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("primary repository checkout", output)
            self.assertFalse(Path(env["VERIFY_LOG"]).exists(), "verify ran in a linked worktree")

    def test_release_check_is_read_only(self):
        with tempfile.TemporaryDirectory(prefix="taiji-release-readonly-") as temp_dir:
            repo, notes, env = self._make_release_repo(
                Path(temp_dir), tag="v1.2.3", ignored_artifacts=True
            )
            before_refs = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout
            before_head = _git(repo, "rev-parse", "HEAD").stdout
            before_status = _git(repo, "status", "--porcelain=v1", "-z").stdout
            before_tree = _snapshot_worktree(repo)
            self.assertEqual(before_tree["ignored-state/payload.bin"][0], "file")
            self.assertEqual(before_tree["ignored-state/payload-link"][0], "symlink")
            self.assertEqual(before_tree["ignored-state/payload-link"][2], "payload.bin")
            result = self._run_release_check(repo, notes, "v1.2.3", env)
            self.assertEqual(result.returncode, 0, _combined_output(result))
            self.assertEqual(_git(repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout, before_refs)
            self.assertEqual(_git(repo, "rev-parse", "HEAD").stdout, before_head)
            self.assertEqual(_git(repo, "status", "--porcelain=v1", "-z").stdout, before_status)
            self.assertEqual(_snapshot_worktree(repo), before_tree)

        source = self._read_required(RELEASE_CHECK)
        executable = "\n".join(
            line.strip()
            for line in source.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "printf"))
        )
        forbidden = (
            r"\bgit\s+(?:fetch|pull|push|tag|add|commit|reset)\b",
            r"(?:^|[;&|]\s*)\bgh\b",
            r"\b(?:curl|wget|ssh|scp)\b",
            r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:build|package|publish|install)\b",
            r"\b(?:pip|pip3|uv|apt|apt-get|brew)\s+install\b",
            r"\b(?:dpkg-buildpackage|electron-builder|codesign|cosign|gpg)\b",
            r"(?:^|[;&|]\s*)(?:build|package|install|sign|publish)\b",
            r"(?:^|[;&|]\s*)(?:touch|mkdir|cp|mv|rm|tee)\b",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, executable), executable)
        self.assertIn("GIT_OPTIONAL_LOCKS=0", source)

    def test_existing_linux_release_gate_is_byte_for_byte_preserved(self):
        self.assertTrue(TAIJI_RELEASE_CHECK.is_file())
        actual = hashlib.sha256(TAIJI_RELEASE_CHECK.read_bytes()).hexdigest()
        self.assertEqual(actual, TAIJI_RELEASE_CHECK_SHA256)

    def test_main_validation_workflow_contract(self):
        workflow = self._read_required(ROOT / ".github" / "workflows" / "ci.yml")
        self.assertRegex(workflow, r"(?m)^name:\s*Main Validation\s*$")
        trigger_block = workflow[workflow.index("on:") : workflow.index("permissions:")]
        self.assertEqual(
            re.findall(r"(?m)^  ([a-z_]+):\s*(?:\n|$)", trigger_block),
            ["push", "workflow_dispatch"],
        )
        self.assertRegex(workflow, r"(?m)^\s*push:\s*$")
        self.assertRegex(workflow, r"(?m)^\s*branches:\s*\[main\]\s*$")
        self.assertRegex(workflow, r"(?m)^\s*workflow_dispatch:\s*$")
        for obsolete in (
            "pull_request",
            "github.event.pull_request",
            "PR_BASE",
            "LABELS",
            "--label",
        ):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, workflow)
        self.assertIn(
            "group: ci-${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}",
            workflow,
        )
        scope = workflow[workflow.index("      - id: scope") : workflow.index("\n\n  baseline:")]
        for contract in (
            'EVENT_NAME: ${{ github.event_name }}',
            'BEFORE: ${{ github.event.before }}',
            'case "$EVENT_NAME" in',
            "push)",
            '[[ -z "$base" || "$base" =~ ^0+$ ]]',
            'git cat-file -e "$base^{commit}"',
            "push event requires a non-empty, non-zero, resolvable github.event.before",
            "workflow_dispatch)",
            'base="$(git rev-parse --verify "$HEAD_SHA^" 2>/dev/null)"',
            "workflow_dispatch requires a resolvable HEAD^ comparison base",
            "unsupported event for change classification",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, scope)
        push_branch = scope[scope.index("    push)") : scope.index("    workflow_dispatch)")]
        dispatch_branch = scope[
            scope.index("    workflow_dispatch)") : scope.index("    *)")
        ]
        self.assertNotIn('git rev-parse --verify "$HEAD_SHA^"', push_branch)
        self.assertIn('git rev-parse --verify "$HEAD_SHA^"', dispatch_branch)
        self.assertNotIn('base="$HEAD_SHA"', scope)
        self.assertEqual(3, scope.count("exit 1"))
        self.assertRegex(workflow, r"(?m)^\s*name:\s*CI Gate\s*$")
        self.assertIn("- name: Require every selected job to pass", workflow)

    def test_release_evidence_contract_uses_main_validation_push(self):
        paths = (
            ROOT / "scripts" / "produce-taiji-github-ci-evidence.py",
            ROOT / "scripts" / "validate-taiji-release-evidence.py",
            ROOT / "tests" / "github_ci_v2_fixture.py",
            ROOT / "tests" / "test_github_ci_evidence_producer.py",
        )
        texts = {path: self._read_required(path) for path in paths}
        for path, text in texts.items():
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn("Main Validation", text)
                self.assertNotIn("Pull Request CI", text)
        combined = "\n".join(texts.values())
        for token in ('"push"', '"main"', '"CI Gate"', "Require every selected job to pass"):
            with self.subTest(token=token):
                self.assertIn(token, combined)
        self.assertIn('("event", "pull_request")', texts[paths[3]])


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    unittest.main()
