import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_TREE_GATE = REPO_ROOT / "scripts" / "check-imported-source-tree.py"


def run(command, *, cwd, check=True, env_overrides=None):
    env = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
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


class ImportedSourceTreeGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "parent"
        self.nested = self.root / "nested"
        self.source_prefix = Path("sources") / "component"

        self.repo.mkdir()
        self.nested.mkdir()
        self._init_repo(self.repo)
        self._init_repo(self.nested)

        (self.nested / "kept.txt").write_text("kept\n", encoding="utf-8")
        (self.nested / "dist").mkdir()
        (self.nested / "dist" / "bundle.js").write_text(
            "console.log('tracked upstream');\n",
            encoding="utf-8",
        )
        run(["git", "add", "."], cwd=self.nested)
        run(["git", "commit", "-m", "nested source"], cwd=self.nested)

        source_root = self.repo / self.source_prefix
        source_root.parent.mkdir(parents=True)
        shutil.copytree(self.nested, source_root, ignore=shutil.ignore_patterns(".git"))
        (self.repo / ".gitignore").write_text("**/dist/\n", encoding="utf-8")
        run(["git", "add", ".gitignore", str(self.source_prefix / "kept.txt")], cwd=self.repo)
        run(["git", "commit", "-m", "partial import"], cwd=self.repo)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _init_repo(path):
        run(["git", "init", "-b", "main"], cwd=path)
        run(["git", "config", "user.name", "Taiji Test"], cwd=path)
        run(["git", "config", "user.email", "taiji@example.invalid"], cwd=path)

    def gate(self, *extra_args, env_overrides=None):
        return run(
            [
                "python3",
                str(SOURCE_TREE_GATE),
                "--repo-root",
                str(self.repo),
                "--source-prefix",
                self.source_prefix.as_posix(),
                "--source-git-dir",
                str(self.nested / ".git"),
                *extra_args,
            ],
            cwd=self.repo,
            check=False,
            env_overrides=env_overrides,
        )

    def force_add_ignored_source(self):
        run(
            ["git", "add", "-f", str(self.source_prefix / "dist" / "bundle.js")],
            cwd=self.repo,
        )

    def test_rejects_upstream_file_silently_omitted_by_parent_ignore_rules(self):
        result = self.gate()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("untracked_in_parent: 1", result.stdout)
        self.assertIn("sources/component/dist/bundle.js", result.stderr)

    def test_accepts_complete_import_after_ignored_upstream_file_is_force_added(self):
        self.force_add_ignored_source()

        result = self.gate("--require-content-match")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source_entries: 2", result.stdout)
        self.assertIn("imported source tree gate passed", result.stdout)

    def test_exact_import_mode_rejects_unstaged_physical_content_drift(self):
        self.force_add_ignored_source()
        (self.repo / self.source_prefix / "kept.txt").write_text(
            "locally changed\n",
            encoding="utf-8",
        )

        tracked_only = self.gate()
        exact = self.gate("--require-content-match")

        self.assertEqual(
            tracked_only.returncode,
            0,
            tracked_only.stdout + tracked_only.stderr,
        )
        self.assertNotEqual(exact.returncode, 0)
        self.assertIn("content_mismatch: 1", exact.stdout)
        self.assertIn("sources/component/kept.txt", exact.stderr)

    def test_exact_import_mode_rejects_staged_blob_different_from_source_commit(self):
        self.force_add_ignored_source()
        (self.repo / self.source_prefix / "kept.txt").write_text(
            "intentionally diverged\n",
            encoding="utf-8",
        )
        run(["git", "add", str(self.source_prefix / "kept.txt")], cwd=self.repo)

        result = self.gate("--require-content-match")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("content_mismatch: 1", result.stdout)
        self.assertIn("sources/component/kept.txt", result.stderr)

    def test_exact_import_mode_hashes_symlink_target_text_not_target_contents(self):
        (self.nested / "broken-link").symlink_to("missing-target")
        run(["git", "add", "broken-link"], cwd=self.nested)
        run(["git", "commit", "-m", "add source symlink"], cwd=self.nested)
        (self.repo / self.source_prefix / "broken-link").symlink_to("missing-target")
        run(["git", "add", str(self.source_prefix / "broken-link")], cwd=self.repo)
        self.force_add_ignored_source()

        result = self.gate("--require-content-match")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source_entries: 3", result.stdout)

    def test_rejects_gitlink_that_would_be_empty_in_parent_archive(self):
        child = self.root / "child"
        child.mkdir()
        self._init_repo(child)
        (child / "child.txt").write_text("child source\n", encoding="utf-8")
        run(["git", "add", "child.txt"], cwd=child)
        run(["git", "commit", "-m", "child source"], cwd=child)
        run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(child),
                "vendor/child",
            ],
            cwd=self.nested,
        )
        run(["git", "commit", "-am", "add nested gitlink"], cwd=self.nested)

        result = self.gate()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gitlink/submodule", result.stderr)
        self.assertIn("vendor/child", result.stderr)

    def test_exact_import_supports_different_parent_and_source_object_formats(self):
        sha256_parent = self.root / "sha256-parent"
        sha256_parent.mkdir()
        run(
            ["git", "init", "--object-format=sha256", "-b", "main"],
            cwd=sha256_parent,
        )
        run(["git", "config", "user.name", "Taiji Test"], cwd=sha256_parent)
        run(
            ["git", "config", "user.email", "taiji@example.invalid"],
            cwd=sha256_parent,
        )
        sha256_source = sha256_parent / self.source_prefix
        sha256_source.parent.mkdir(parents=True)
        shutil.copytree(
            self.nested,
            sha256_source,
            ignore=shutil.ignore_patterns(".git"),
        )
        run(["git", "add", "."], cwd=sha256_parent)

        result = run(
            [
                "python3",
                str(SOURCE_TREE_GATE),
                "--repo-root",
                str(sha256_parent),
                "--source-prefix",
                self.source_prefix.as_posix(),
                "--source-git-dir",
                str(self.nested / ".git"),
                "--require-content-match",
            ],
            cwd=sha256_parent,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("imported source tree gate passed", result.stdout)

    def test_exact_import_rejects_special_file_without_reading_it(self):
        self.force_add_ignored_source()
        kept = self.repo / self.source_prefix / "kept.txt"
        kept.unlink()
        os.mkfifo(kept)

        result = self.gate("--require-content-match")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("content_mismatch: 1", result.stdout)
        self.assertIn("mode_mismatch: 1", result.stdout)

    def test_rejects_tracked_source_file_missing_from_physical_tree(self):
        self.force_add_ignored_source()
        (self.repo / self.source_prefix / "kept.txt").unlink()

        result = self.gate()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing_on_disk: 1", result.stdout)
        self.assertIn("sources/component/kept.txt", result.stderr)

    def test_explicit_paths_override_poisoned_ambient_git_environment(self):
        self.force_add_ignored_source()
        ambient = self.root / "ambient"
        ambient.mkdir()
        self._init_repo(ambient)

        result = self.gate(
            "--require-content-match",
            env_overrides={
                "GIT_DIR": str(ambient / ".git"),
                "GIT_WORK_TREE": str(ambient),
                "GIT_INDEX_FILE": str(ambient / "poisoned-index"),
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"repo: {self.repo.resolve()}", result.stdout)

    def test_rejects_source_prefix_that_escapes_parent_repository(self):
        result = run(
            [
                "python3",
                str(SOURCE_TREE_GATE),
                "--repo-root",
                str(self.repo),
                "--source-prefix",
                "../escape",
                "--source-git-dir",
                str(self.nested / ".git"),
            ],
            cwd=self.repo,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source prefix must be a safe relative path", result.stderr)


if __name__ == "__main__":
    unittest.main()
