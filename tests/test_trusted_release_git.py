import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRUSTED_GIT = ROOT / "scripts" / "taiji-trusted-git"


def run(command, *, cwd=None, env=None):
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


class TrustedReleaseGitTest(unittest.TestCase):
    def _create_repo(self, root: Path, marker: str) -> str:
        root.mkdir()
        run(["/usr/bin/git", "init", "-q"], cwd=root)
        run(["/usr/bin/git", "config", "user.name", "Taiji Test"], cwd=root)
        run(["/usr/bin/git", "config", "user.email", "taiji-test@invalid.local"], cwd=root)
        (root / f"{marker}.txt").write_text(f"{marker}\n", encoding="utf-8")
        run(["/usr/bin/git", "add", f"{marker}.txt"], cwd=root)
        run(["/usr/bin/git", "commit", "-q", "-m", marker], cwd=root)
        return run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=root).stdout.strip()

    def test_trusted_git_ignores_every_ambient_git_selector_for_head_and_archive(self):
        self.assertTrue(TRUSTED_GIT.is_file())
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-") as temporary:
            root = Path(temporary)
            expected_repo = root / "expected"
            hostile_repo = root / "hostile"
            expected_commit = self._create_repo(expected_repo, "expected")
            self._create_repo(hostile_repo, "hostile")

            hostile = os.environ.copy()
            hostile.update(
                {
                    "GIT_DIR": str(hostile_repo / ".git"),
                    "GIT_WORK_TREE": str(hostile_repo),
                    "GIT_COMMON_DIR": str(hostile_repo / ".git"),
                    "GIT_INDEX_FILE": str(hostile_repo / ".git" / "index"),
                    "GIT_OBJECT_DIRECTORY": str(hostile_repo / ".git" / "objects"),
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(expected_repo / ".git" / "objects"),
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.worktree",
                    "GIT_CONFIG_VALUE_0": str(hostile_repo),
                }
            )

            resolved = run(
                [str(TRUSTED_GIT), "-C", str(expected_repo), "rev-parse", "HEAD"],
                env=hostile,
            ).stdout.strip()
            self.assertEqual(resolved, expected_commit)

            archive_path = root / "expected.tar"
            with archive_path.open("wb") as archive:
                subprocess.run(
                    [str(TRUSTED_GIT), "-C", str(expected_repo), "archive", "HEAD"],
                    env=hostile,
                    check=True,
                    stdout=archive,
                )
            with tarfile.open(archive_path) as archive:
                members = set(archive.getnames())
            self.assertIn("expected.txt", members)
            self.assertNotIn("hostile.txt", members)

    def test_every_release_identity_caller_uses_the_trusted_git_boundary(self):
        callers = (
            "scripts/produce-taiji-offline-rehearsal.py",
            "scripts/sign-taiji-release-evidence.sh",
            "scripts/taiji-release-check.sh",
            "packaging/linux/deb/build-deb.sh",
            "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
            "taijiagent 打包交付/01_制包机_发布预检.sh",
            "taijiagent 打包交付/99_本机_准备制包输入包.sh",
        )
        for relative in callers:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("taiji-trusted-git", source, relative)


if __name__ == "__main__":
    unittest.main()
