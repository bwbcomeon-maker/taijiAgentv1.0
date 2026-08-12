import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_GATE = REPO_ROOT / "scripts" / "check-clean-worktree.sh"


def run(command, *, cwd, check=True, env_overrides=None):
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    env.update(env_overrides or {})
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


class CanonicalMainSourceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        run(["git", "init", "-b", "main"], cwd=self.repo)
        run(["git", "config", "user.name", "Taiji Test"], cwd=self.repo)
        run(["git", "config", "user.email", "taiji@example.invalid"], cwd=self.repo)
        (self.repo / "README.md").write_text("canonical\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=self.repo)
        run(["git", "commit", "-m", "initial"], cwd=self.repo)

    def tearDown(self):
        self.temp_dir.cleanup()

    def gate(
        self,
        repo,
        *,
        mode="formal",
        source_root=None,
        dirty_policy=None,
        expect_head=None,
        env_overrides=None,
    ):
        source_root = source_root or repo
        command = [
            "bash",
            str(SOURCE_GATE),
            "--mode",
            mode,
            "--repo-root",
            str(repo),
            "--source-root",
            str(source_root),
        ]
        if dirty_policy:
            command.extend(["--dirty-policy", dirty_policy])
        if expect_head:
            command.extend(["--expect-head", expect_head])
        return run(
            command,
            cwd=repo,
            check=False,
            env_overrides=env_overrides,
        )

    def test_formal_mode_accepts_clean_main_in_primary_worktree(self):
        expected = run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        result = self.gate(self.repo, expect_head=expected)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mode: formal", result.stdout)
        self.assertIn("branch: main", result.stdout)
        self.assertIn("worktree: primary", result.stdout)
        self.assertIn("canonical main source gate passed", result.stdout)

    def test_formal_mode_rejects_expected_head_mismatch(self):
        result = self.gate(self.repo, expect_head="a" * 40)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected head", result.stderr.lower())

    def test_gate_uses_fixed_git_when_exported_function_is_hostile(self):
        expected = run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        result = self.gate(
            self.repo,
            expect_head=expected,
            env_overrides={
                "BASH_FUNC_git%%": "() { printf 'AMBIENT_GIT_FUNCTION_USED\\n' >&2; /usr/bin/git \"$@\"; }",
                "BASH_ENV": str(self.root / "does-not-exist"),
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("AMBIENT_GIT_FUNCTION_USED", result.stderr)

    def test_gate_disables_repo_local_fsmonitor_hooks(self):
        marker = self.root / "fsmonitor-ran"
        hook = self.root / "fsmonitor-hook.sh"
        hook.write_text(
            "#!/bin/sh\nprintf ran >> \"{}\"\nprintf '\\n'\n".format(marker),
            encoding="utf-8",
        )
        hook.chmod(0o755)
        run(["git", "config", "core.fsmonitor", str(hook)], cwd=self.repo)

        result = self.gate(self.repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "formal source gate executed core.fsmonitor")

    def test_gate_rejects_external_clean_filters_before_status(self):
        marker = self.root / "clean-filter-ran"
        hook = self.root / "clean-filter.sh"
        hook.write_text(
            "#!/bin/sh\nprintf ran >> \"{}\"\ncat\n".format(marker),
            encoding="utf-8",
        )
        hook.chmod(0o755)
        (self.repo / ".git/info/attributes").write_text(
            "*.txt filter=taiji-source-test\n",
            encoding="utf-8",
        )
        run(["git", "config", "filter.taiji-source-test.clean", str(hook)], cwd=self.repo)
        run(["git", "config", "filter.taiji-source-test.smudge", "cat"], cwd=self.repo)

        result = self.gate(self.repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe executable filter", result.stderr.lower())
        self.assertFalse(marker.exists(), "formal source gate executed a clean filter")

    def test_formal_mode_rejects_non_main_branch(self):
        run(["git", "switch", "-c", "feature/demo"], cwd=self.repo)

        result = self.gate(self.repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("formal source must be branch main", result.stderr)

    def test_formal_mode_rejects_a_sibling_gitdir_as_non_primary(self):
        canonical = self.repo / ".git"
        alternate = self.repo / ".git-alternate"
        canonical.rename(alternate)
        (alternate / "info/exclude").write_text(
            ".git-alternate/\n",
            encoding="utf-8",
        )
        canonical.write_text("gitdir: .git-alternate\n", encoding="utf-8")

        result = self.gate(self.repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("primary worktree", result.stderr.lower())

    def test_formal_mode_rejects_dirty_main(self):
        (self.repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")

        result = self.gate(self.repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("formal source worktree is dirty", result.stderr)

    def test_formal_mode_does_not_accept_a_replacement_commit_tree(self):
        original = run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        run(["git", "switch", "-c", "alternate-source"], cwd=self.repo)
        tracked = self.repo / "tracked.txt"
        tracked.write_text("replacement tree\n", encoding="utf-8")
        run(["git", "add", "tracked.txt"], cwd=self.repo)
        run(["git", "commit", "-m", "replacement tree"], cwd=self.repo)
        alternate = run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        run(["git", "switch", "main"], cwd=self.repo)
        run(["git", "replace", original, alternate], cwd=self.repo)
        run(["git", "reset", "--hard", original], cwd=self.repo)

        result = self.gate(self.repo, expect_head=original)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dirty", result.stderr.lower())

    def test_runtime_policy_allows_only_root_agent_instructions_to_be_dirty(self):
        (self.repo / "AGENTS.md").write_text("initial instructions\n", encoding="utf-8")
        run(["git", "add", "AGENTS.md"], cwd=self.repo)
        run(["git", "commit", "-m", "add agent instructions"], cwd=self.repo)
        (self.repo / "AGENTS.md").write_text("local instructions\n", encoding="utf-8")

        result = self.gate(self.repo, dirty_policy="runtime")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dirty: 1", result.stdout)
        self.assertIn("runtime_dirty: 0", result.stdout)
        self.assertIn("non_runtime_dirty: 1", result.stdout)
        self.assertIn("non-runtime source changes ignored for local runtime", result.stderr)
        self.assertIn("AGENTS.md", result.stderr)

    def test_runtime_policy_allows_agent_skill_metadata_but_rejects_runtime_files(self):
        agent_skill = self.repo / ".agents" / "skills" / "qa" / "SKILL.md"
        agent_skill.parent.mkdir(parents=True)
        agent_skill.write_text("local skill\n", encoding="utf-8")
        runtime_file = self.repo / "apps" / "runtime.js"
        runtime_file.parent.mkdir(parents=True)
        runtime_file.write_text("runtime change\n", encoding="utf-8")

        result = self.gate(self.repo, dirty_policy="runtime")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime_dirty: 1", result.stdout)
        self.assertIn("non_runtime_dirty: 1", result.stdout)
        self.assertIn("apps/runtime.js", result.stderr)
        self.assertIn("formal source has runtime-affecting changes", result.stderr)

    def test_runtime_policy_rejects_agent_metadata_renamed_into_runtime_tree(self):
        agent_payload = self.repo / ".agents" / "payload.js"
        agent_payload.parent.mkdir(parents=True)
        agent_payload.write_text("metadata\n", encoding="utf-8")
        run(["git", "add", ".agents/payload.js"], cwd=self.repo)
        run(["git", "commit", "-m", "add agent metadata"], cwd=self.repo)
        runtime_payload = self.repo / "apps" / "payload.js"
        runtime_payload.parent.mkdir(parents=True)
        run(["git", "mv", ".agents/payload.js", "apps/payload.js"], cwd=self.repo)

        result = self.gate(self.repo, dirty_policy="runtime")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime_dirty: 1", result.stdout)
        self.assertIn("apps/payload.js", result.stderr)

    def test_runtime_policy_handles_unicode_agent_metadata_paths(self):
        agent_skill = self.repo / ".agents" / "skills" / "质量检查.md"
        agent_skill.parent.mkdir(parents=True)
        agent_skill.write_text("local skill\n", encoding="utf-8")

        result = self.gate(self.repo, dirty_policy="runtime")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("runtime_dirty: 0", result.stdout)
        self.assertIn("质量检查.md", result.stderr)

    def test_strict_policy_still_rejects_agent_instruction_changes(self):
        (self.repo / "AGENTS.md").write_text("local instructions\n", encoding="utf-8")

        result = self.gate(self.repo, dirty_policy="strict")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("formal source worktree is dirty", result.stderr)

    def test_formal_mode_rejects_source_root_mismatch(self):
        other_source = self.root / "other-source"
        other_source.mkdir()

        result = self.gate(self.repo, source_root=other_source)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source root does not match git top-level", result.stderr)

    def test_gate_ignores_ambient_git_dir_and_work_tree_overrides(self):
        other_repo = self.root / "ambient-other"
        other_repo.mkdir()
        run(["git", "init", "-b", "other"], cwd=other_repo)

        result = self.gate(
            self.repo,
            env_overrides={
                "GIT_DIR": str(other_repo / ".git"),
                "GIT_WORK_TREE": str(other_repo),
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"repo: {self.repo.resolve()}", result.stdout)

    def test_formal_mode_rejects_main_checked_out_in_linked_worktree(self):
        run(["git", "switch", "-c", "feature/primary"], cwd=self.repo)
        linked = self.root / "linked-main"
        run(["git", "worktree", "add", str(linked), "main"], cwd=self.repo)

        result = self.gate(linked)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("formal source must use the primary worktree", result.stderr)

    def test_explicit_development_mode_allows_dirty_linked_worktree(self):
        linked = self.root / "linked-dev"
        run(
            ["git", "worktree", "add", "-b", "feature/linked", str(linked), "HEAD"],
            cwd=self.repo,
        )
        (linked / "local-only.txt").write_text("isolated development\n", encoding="utf-8")

        result = self.gate(linked, mode="development")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mode: development", result.stdout)
        self.assertIn("worktree: linked", result.stdout)
        self.assertIn("development source isolation gate passed", result.stdout)


class CanonicalMainGateWiringTests(unittest.TestCase):
    def read(self, relative_path):
        return (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    def test_release_and_packaging_entrypoints_invoke_formal_source_gate(self):
        for relative_path in (
            "scripts/taiji-release-check.sh",
            "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
            "taijiagent 打包交付/01_制包机_发布预检.sh",
            "taijiagent 打包交付/99_本机_准备制包输入包.sh",
        ):
            with self.subTest(path=relative_path):
                source = self.read(relative_path)
                self.assertIn("check-clean-worktree.sh", source)
                self.assertIn("--mode formal", source)
                self.assertIn("--repo-root", source)
                self.assertIn("--source-root", source)
                self.assertNotIn("--dirty-policy runtime", source)

    def test_browser_launcher_defaults_to_formal_but_supports_explicit_development_mode(self):
        source = self.read("hermes-local-lab/启动太极Agent.command")

        self.assertIn('TAIJI_SOURCE_MODE="${TAIJI_SOURCE_MODE:-formal}"', source)
        self.assertIn("check-clean-worktree.sh", source)
        self.assertIn('--mode "$TAIJI_SOURCE_MODE"', source)
        self.assertIn('--repo-root "$REPO_DIR"', source)
        self.assertIn('--source-root "$REPO_DIR"', source)
        self.assertIn('--dirty-policy runtime', source)

    def test_desktop_command_uses_the_shared_source_gate(self):
        source = self.read("hermes-local-lab/启动太极Agent桌面端.command")

        self.assertIn('if [ -z "${TAIJI_SOURCE_MODE:-}" ]; then', source)
        self.assertIn('if [ -d "$REPO_DIR/.git" ]; then', source)
        self.assertIn('TAIJI_SOURCE_MODE="formal"', source)
        self.assertIn('elif [ -f "$REPO_DIR/.git" ]; then', source)
        self.assertIn('TAIJI_SOURCE_MODE="development"', source)
        self.assertIn("check-clean-worktree.sh", source)
        self.assertIn('--mode "$TAIJI_SOURCE_MODE"', source)
        self.assertIn('--repo-root "$REPO_DIR"', source)
        self.assertIn('--source-root "$REPO_DIR"', source)
        self.assertIn('--dirty-policy runtime', source)
        self.assertIn("export TAIJI_SOURCE_MODE", source)

    def test_finder_desktop_launcher_delegates_source_mode_to_adjacent_command(self):
        source = self.read(
            "hermes-local-lab/启动太极Agent桌面端.app/Contents/MacOS/"
            "taiji-agent-desktop-launcher"
        )
        command_source = self.read("hermes-local-lab/启动太极Agent桌面端.command")

        self.assertIn('COMMAND_LAUNCHER="$LAB_DIR/启动太极Agent桌面端.command"', source)
        self.assertIn('/usr/bin/open -a Terminal "$COMMAND_LAUNCHER"', source)
        self.assertNotIn("TAIJI_SOURCE_MODE=", source)
        self.assertIn("export TAIJI_SOURCE_MODE", command_source)

    def test_persistent_credential_lock_is_excluded_from_source_status(self):
        ignore_lines = {
            line.strip()
            for line in self.read(
                "hermes-local-lab/sources/hermes-agent/.gitignore"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("/.taiji-credential-transaction.lock", ignore_lines)
        self.assertNotIn(".taiji-credential-*", ignore_lines)


if __name__ == "__main__":
    unittest.main()
