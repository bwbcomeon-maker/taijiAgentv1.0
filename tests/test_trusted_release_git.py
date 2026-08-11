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

    def test_trusted_git_ignores_replace_ref_after_reset_to_the_replacement_tree(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-replace-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            original_commit = self._create_repo(repository, "original")
            original_tree = run(
                ["/usr/bin/git", "rev-parse", f"{original_commit}^{{tree}}"],
                cwd=repository,
            ).stdout.strip()

            payload = repository / "original.txt"
            payload.write_text("replacement\n", encoding="utf-8")
            run(["/usr/bin/git", "add", payload.name], cwd=repository)
            replacement_tree = run(
                ["/usr/bin/git", "write-tree"], cwd=repository
            ).stdout.strip()
            replacement_commit = run(
                ["/usr/bin/git", "commit-tree", replacement_tree, "-m", "replacement"],
                cwd=repository,
            ).stdout.strip()
            run(["/usr/bin/git", "reset", "--hard", original_commit], cwd=repository)
            run(
                ["/usr/bin/git", "replace", original_commit, replacement_commit],
                cwd=repository,
            )
            run(["/usr/bin/git", "reset", "--hard", original_commit], cwd=repository)

            self.assertEqual(payload.read_text(encoding="utf-8"), "replacement\n")
            self.assertEqual(
                run(
                    ["/usr/bin/git", "status", "--porcelain=v1"], cwd=repository
                ).stdout,
                "",
            )
            self.assertEqual(
                run(
                    ["/usr/bin/git", "rev-parse", f"{original_commit}^{{tree}}"],
                    cwd=repository,
                ).stdout.strip(),
                replacement_tree,
            )

            resolved_tree = run(
                [
                    str(TRUSTED_GIT),
                    "-C",
                    str(repository),
                    "rev-parse",
                    f"{original_commit}^{{tree}}",
                ]
            ).stdout.strip()
            self.assertEqual(resolved_tree, original_tree)

            archive_path = root / "trusted.tar"
            with archive_path.open("wb") as archive:
                subprocess.run(
                    [
                        str(TRUSTED_GIT),
                        "-C",
                        str(repository),
                        "archive",
                        original_commit,
                    ],
                    check=True,
                    stdout=archive,
                )
            with tarfile.open(archive_path) as archive:
                archived_payload = archive.extractfile("original.txt")
                self.assertIsNotNone(archived_payload)
                self.assertEqual(archived_payload.read(), b"original\n")

    def test_trusted_git_ignores_user_global_archive_attributes(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-config-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            self._create_repo(repository, "expected")
            hostile_home = root / "hostile-home"
            hostile_home.mkdir()
            attributes = hostile_home / "archive.attributes"
            attributes.write_text("expected.txt export-ignore\n", encoding="utf-8")
            (hostile_home / ".gitconfig").write_text(
                "[core]\n\tattributesfile = {}\n".format(attributes),
                encoding="utf-8",
            )
            hostile = os.environ.copy()
            hostile["HOME"] = str(hostile_home)
            hostile["XDG_CONFIG_HOME"] = str(hostile_home / "xdg")

            archive_path = root / "trusted.tar"
            with archive_path.open("wb") as archive:
                subprocess.run(
                    [str(TRUSTED_GIT), "-C", str(repository), "archive", "HEAD"],
                    env=hostile,
                    check=True,
                    stdout=archive,
                )
            with tarfile.open(archive_path) as archive:
                self.assertIn("expected.txt", archive.getnames())

    def test_trusted_git_sets_a_closed_release_environment_after_unset(self):
        source = TRUSTED_GIT.read_text(encoding="utf-8")
        for assignment in (
            'GIT_NO_REPLACE_OBJECTS="1"',
            'GIT_CONFIG_GLOBAL="/dev/null"',
            'GIT_CONFIG_SYSTEM="/dev/null"',
            'GIT_CONFIG_NOSYSTEM="1"',
            'HOME="/dev/null"',
            'XDG_CONFIG_HOME="/dev/null"',
        ):
            self.assertIn(assignment, source)
        self.assertLess(
            source.index("unset _taiji_git_env_entry _taiji_git_env_name"),
            source.index('GIT_NO_REPLACE_OBJECTS="1"'),
        )

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
