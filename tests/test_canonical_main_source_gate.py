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

    def gate(self, repo, *, mode="formal", source_root=None, env_overrides=None):
        source_root = source_root or repo
        return run(
            [
                "bash",
                str(SOURCE_GATE),
                "--mode",
                mode,
                "--repo-root",
                str(repo),
                "--source-root",
                str(source_root),
            ],
            cwd=repo,
            check=False,
            env_overrides=env_overrides,
        )

    def test_formal_mode_accepts_clean_main_in_primary_worktree(self):
        result = self.gate(self.repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mode: formal", result.stdout)
        self.assertIn("branch: main", result.stdout)
        self.assertIn("worktree: primary", result.stdout)
        self.assertIn("canonical main source gate passed", result.stdout)

    def test_formal_mode_rejects_non_main_branch(self):
        run(["git", "switch", "-c", "feature/demo"], cwd=self.repo)

        result = self.gate(self.repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("formal source must be branch main", result.stderr)

    def test_formal_mode_rejects_dirty_main(self):
        (self.repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")

        result = self.gate(self.repo)

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

    def test_browser_launcher_defaults_to_formal_but_supports_explicit_development_mode(self):
        source = self.read("hermes-local-lab/启动太极Agent.command")

        self.assertIn('TAIJI_SOURCE_MODE="${TAIJI_SOURCE_MODE:-formal}"', source)
        self.assertIn("check-clean-worktree.sh", source)
        self.assertIn('--mode "$TAIJI_SOURCE_MODE"', source)
        self.assertIn('--repo-root "$REPO_DIR"', source)
        self.assertIn('--source-root "$REPO_DIR"', source)

    def test_desktop_command_uses_the_shared_source_gate(self):
        source = self.read("hermes-local-lab/启动太极Agent桌面端.command")

        self.assertIn('TAIJI_SOURCE_MODE="${TAIJI_SOURCE_MODE:-formal}"', source)
        self.assertIn("check-clean-worktree.sh", source)
        self.assertIn('--mode "$TAIJI_SOURCE_MODE"', source)
        self.assertIn('--repo-root "$REPO_DIR"', source)
        self.assertIn('--source-root "$REPO_DIR"', source)

    def test_finder_desktop_runner_invokes_the_shared_source_gate(self):
        source = self.read(
            "hermes-local-lab/启动太极Agent桌面端.app/Contents/MacOS/"
            "taiji-agent-desktop-launcher"
        )

        self.assertIn('TAIJI_SOURCE_MODE="${TAIJI_SOURCE_MODE:-formal}"', source)
        self.assertIn("SOURCE_GATE=", source)
        self.assertIn("check-clean-worktree.sh", source)
        self.assertIn('if ! "$SOURCE_GATE"', source)
        self.assertIn('--mode "$TAIJI_SOURCE_MODE"', source)
        self.assertIn('--repo-root "$REPO_DIR"', source)
        self.assertIn('--source-root "$REPO_DIR"', source)

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
