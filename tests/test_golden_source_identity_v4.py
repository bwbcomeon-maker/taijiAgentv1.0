"""Fail-closed provenance tests for the golden Linux orchestrator."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "scripts/taiji-linux-golden-orchestrator.py"


def load_orchestrator(path: Path = ORCHESTRATOR):
    spec = importlib.util.spec_from_file_location(
        "taiji_linux_golden_orchestrator_source_identity_test",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load golden orchestrator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoldenSourceIdentityV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_orchestrator()
        self.temporary = tempfile.TemporaryDirectory(
            prefix="taiji-golden-source-identity-v4-"
        )
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir(mode=0o700)
        trust_paths = getattr(self.module, "SOURCE_TRUST_PATHS", ())
        self.assertTrue(trust_paths, "orchestrator must declare a fixed source trust set")
        for relative in trust_paths:
            source = ROOT / relative
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        readme = self.repo / "README.md"
        readme.write_text("formal source fixture\n", encoding="utf-8")
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Taiji Source Identity Test")
        self.git("config", "user.email", "taiji-source@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "formal source fixture")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.script = self.repo / "scripts/taiji-linux-golden-orchestrator.py"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/usr/bin/git", *arguments],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
        )

    def capture(self, commit: str | None = None):
        with mock.patch.object(self.module, "__file__", str(self.script)):
            return self.module._capture_formal_source_identity(
                {
                    "repo_root": str(self.repo),
                    "source_commit": commit or self.commit,
                }
            )

    def test_capture_requires_v4_schema_and_exact_tracked_trust_entries(self):
        identity = self.capture()

        self.assertEqual(
            self.module.CONFIG_SCHEMA,
            "taiji-linux-golden-orchestrator-config/v5",
        )
        self.assertEqual(
            self.module.STATE_SCHEMA,
            "taiji-linux-golden-orchestrator-state/v5",
        )
        self.assertEqual(
            self.module.PLAN_SCHEMA,
            "taiji-linux-golden-orchestrator-plan/v5",
        )
        self.assertEqual(identity["schema"], "taiji-formal-source-identity/v1")
        self.assertEqual(identity["repo_root"], str(self.repo))
        self.assertEqual(identity["source_commit"], self.commit)
        self.assertEqual(identity["branch"], "main")
        self.assertEqual(identity["worktree"], "primary")
        self.assertEqual(set(identity["entries"]), set(self.module.SOURCE_TRUST_PATHS))
        for relative, record in identity["entries"].items():
            self.assertEqual(
                set(record),
                {"git_mode", "git_object", "size", "sha256"},
                relative,
            )

    def test_capture_rejects_config_commit_that_is_not_actual_head_and_main(self):
        with self.assertRaisesRegex(self.module.OrchestratorError, "HEAD|main|commit"):
            self.capture("a" * 40)

    def test_capture_rejects_an_ignored_alternate_orchestrator_entrypoint(self):
        ignored = self.repo / "__pycache__"
        (self.repo / ".git/info/exclude").write_text(
            "**/__pycache__/\n",
            encoding="utf-8",
        )
        ignored.mkdir()
        alternate_path = ignored / "taiji-linux-golden-orchestrator.py"
        shutil.copy2(self.script, alternate_path)
        alternate_path.write_bytes(
            alternate_path.read_bytes() + b"\n# ignored alternate entrypoint\n"
        )
        self.assertEqual(self.git("status", "--porcelain=v1").stdout, "")
        alternate = load_orchestrator(alternate_path)

        with self.assertRaisesRegex(
            alternate.OrchestratorError,
            "entrypoint|tracked.*source|orchestrator.*path",
        ):
            alternate._capture_formal_source_identity(
                {
                    "repo_root": str(self.repo),
                    "source_commit": self.commit,
                }
            )

    def test_capture_rejects_a_sibling_gitdir_masquerading_as_primary(self):
        canonical = self.repo / ".git"
        alternate = self.repo / ".git-alternate"
        canonical.rename(alternate)
        (alternate / "info/exclude").write_text(
            ".git-alternate/\n",
            encoding="utf-8",
        )
        canonical.write_text("gitdir: .git-alternate\n", encoding="utf-8")

        with self.assertRaisesRegex(
            self.module.OrchestratorError,
            r"canonical.*git|primary[- ]worktree|common directory|trusted.*directory|directory \.git",
        ):
            self.capture()

    def test_capture_rejects_dirty_and_untracked_source(self):
        (self.repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(self.module.OrchestratorError, "dirty"):
            self.capture()

    def test_capture_rejects_assume_unchanged_even_when_status_is_clean(self):
        relative = self.module.SOURCE_TRUST_PATHS[0]
        target = self.repo / relative
        self.git("update-index", "--assume-unchanged", relative)
        target.write_bytes(target.read_bytes() + b"\n# hidden drift\n")
        self.assertEqual(self.git("status", "--porcelain=v1").stdout, "")

        with self.assertRaisesRegex(
            self.module.OrchestratorError,
            "assume-unchanged|skip-worktree|index flag",
        ):
            self.capture()

    def test_capture_ignores_local_replace_refs_that_relabel_another_tree(self):
        original = self.commit
        relative = self.module.SOURCE_TRUST_PATHS[0]
        target = self.repo / relative
        self.git("switch", "-c", "alternate-source")
        target.write_bytes(target.read_bytes() + b"\n# alternate source tree\n")
        self.git("add", relative)
        self.git("commit", "-m", "alternate source tree")
        alternate = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("switch", "main")
        self.git("replace", original, alternate)
        self.git("reset", "--hard", original)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), original)
        self.assertEqual(self.git("status", "--porcelain=v1").stdout, "")

        with self.assertRaisesRegex(
            self.module.OrchestratorError,
            "dirty|bytes|git object|replace",
        ):
            self.capture()

    def test_capture_rejects_noncritical_assume_unchanged_index_flag(self):
        self.git("update-index", "--assume-unchanged", "README.md")
        with self.assertRaisesRegex(
            self.module.OrchestratorError,
            "assume-unchanged|skip-worktree|index flag",
        ):
            self.capture()

    def test_capture_rejects_tracked_mode_or_bytes_different_from_git_object(self):
        relative = self.module.SOURCE_TRUST_PATHS[0]
        target = self.repo / relative
        original_mode = target.stat().st_mode & 0o777
        target.chmod(original_mode ^ 0o111)
        with self.assertRaisesRegex(self.module.OrchestratorError, "dirty|mode|Git object"):
            self.capture()

    def test_capture_rejects_writable_source_or_git_metadata_parents(self):
        unsafe_paths = (
            self.repo / "scripts",
            self.repo / ".git/refs",
            self.repo / ".git/index",
        )
        for path in unsafe_paths:
            with self.subTest(path=path):
                original_mode = path.stat().st_mode & 0o777
                path.chmod(original_mode | 0o022)
                try:
                    with self.assertRaisesRegex(
                        self.module.OrchestratorError,
                        "writable|trusted|permission|metadata|directory",
                    ):
                        self.capture()
                finally:
                    path.chmod(original_mode)

    def test_capture_does_not_execute_repo_local_clean_filters(self):
        marker = self.root / "clean-filter-ran"
        hook = self.root / "clean-filter.sh"
        hook.write_text(
            "#!/bin/sh\nprintf ran >> \"{}\"\ncat\n".format(marker),
            encoding="utf-8",
        )
        hook.chmod(0o755)
        (self.repo / ".git/info/attributes").write_text(
            "scripts/*.py filter=taiji-source-test\n",
            encoding="utf-8",
        )
        self.git("config", "filter.taiji-source-test.clean", str(hook))
        self.git("config", "filter.taiji-source-test.smudge", "cat")

        with self.assertRaisesRegex(
            self.module.OrchestratorError,
            "filter|git config|attributes|unsafe",
        ):
            self.capture()
        self.assertFalse(marker.exists(), "formal source verification executed a clean filter")

    def test_stored_source_identity_must_match_fresh_capture_exactly(self):
        identity = self.capture()
        tampered = dict(identity)
        tampered["extra"] = True
        with mock.patch.object(self.module, "__file__", str(self.script)):
            with self.assertRaisesRegex(
                self.module.OrchestratorError,
                "source identity|keys|drift",
            ):
                self.module._revalidate_formal_source_identity(
                    {"repo_root": str(self.repo), "source_commit": self.commit},
                    tampered,
                )


if __name__ == "__main__":
    unittest.main()
