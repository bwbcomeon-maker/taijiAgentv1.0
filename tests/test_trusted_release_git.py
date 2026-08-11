import os
import shutil
import subprocess
import tarfile
import tempfile
import time
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

    def _loose_object_path(self, repository: Path, object_id: str) -> Path:
        objects = Path(
            run(
                ["/usr/bin/git", "rev-parse", "--git-path", "objects"],
                cwd=repository,
            ).stdout.strip()
        )
        if not objects.is_absolute():
            objects = repository / objects
        objects = objects.resolve()
        return objects / object_id[:2] / object_id[2:]

    def _replace_loose_blob_bytes(
        self,
        *,
        repository: Path,
        commit: str,
        member: str,
        replacement: bytes,
    ) -> None:
        original_id = run(
            ["/usr/bin/git", "rev-parse", "{}:{}".format(commit, member)],
            cwd=repository,
        ).stdout.strip()
        replacement_file = repository / "replacement-object-payload"
        replacement_file.write_bytes(replacement)
        replacement_id = run(
            ["/usr/bin/git", "hash-object", "-w", str(replacement_file)],
            cwd=repository,
        ).stdout.strip()
        original_path = self._loose_object_path(repository, original_id)
        replacement_path = self._loose_object_path(repository, replacement_id)
        self.assertTrue(original_path.is_file())
        self.assertTrue(replacement_path.is_file())
        original_path.chmod(0o644)
        shutil.copyfile(replacement_path, original_path)
        original_path.chmod(0o444)

    def _trusted_archive(self, repository: Path, commit: str, archive: Path):
        with archive.open("wb") as output:
            return subprocess.run(
                [
                    str(TRUSTED_GIT),
                    "-C",
                    str(repository),
                    "-c",
                    "tar.umask=0022",
                    "archive",
                    "--format=tar",
                    "--prefix=taiji-agentv1.0/",
                    commit,
                ],
                check=False,
                text=True,
                stderr=subprocess.PIPE,
                stdout=output,
            )

    def test_trusted_archive_rejects_a_loose_blob_replaced_under_its_recorded_oid(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-corrupt-object-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            commit = self._create_repo(repository, "original")
            self._replace_loose_blob_bytes(
                repository=repository,
                commit=commit,
                member="original.txt",
                replacement=b"foreign object payload\n",
            )

            result = self._trusted_archive(repository, commit, root / "trusted.tar")

            self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_trusted_archive_rejects_a_replaced_blob_in_a_shared_clone_alternate_store(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-corrupt-alternate-") as temporary:
            root = Path(temporary)
            donor = root / "donor"
            commit = self._create_repo(donor, "original")
            repository = root / "shared"
            run(
                ["/usr/bin/git", "clone", "-q", "--shared", str(donor), str(repository)]
            )
            self.assertTrue((repository / ".git/objects/info/alternates").is_file())
            self._replace_loose_blob_bytes(
                repository=donor,
                commit=commit,
                member="original.txt",
                replacement=b"foreign alternate payload\n",
            )

            result = self._trusted_archive(repository, commit, root / "trusted.tar")

            self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_trusted_archive_rejects_an_incomplete_reachable_object_closure(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-incomplete-closure-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            commit = self._create_repo(repository, "original")
            blob = run(
                ["/usr/bin/git", "rev-parse", "{}:original.txt".format(commit)],
                cwd=repository,
            ).stdout.strip()
            self._loose_object_path(repository, blob).unlink()

            result = self._trusted_archive(repository, commit, root / "trusted.tar")

            self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_trusted_archive_does_not_lazy_fetch_a_missing_promisor_blob(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-promisor-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            commit = self._create_repo(repository, "original")
            remote = root / "remote.git"
            run(["/usr/bin/git", "clone", "-q", "--bare", str(repository), str(remote)])
            run(["/usr/bin/git", "remote", "add", "origin", str(remote)], cwd=repository)
            run(["/usr/bin/git", "config", "extensions.partialClone", "origin"], cwd=repository)
            run(["/usr/bin/git", "config", "remote.origin.promisor", "true"], cwd=repository)
            run(
                ["/usr/bin/git", "config", "remote.origin.partialCloneFilter", "blob:none"],
                cwd=repository,
            )
            blob = run(
                ["/usr/bin/git", "rev-parse", "{}:original.txt".format(commit)],
                cwd=repository,
            ).stdout.strip()
            blob_path = self._loose_object_path(repository, blob)
            blob_path.unlink()

            result = self._trusted_archive(repository, commit, root / "trusted.tar")

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse(blob_path.exists(), "missing promisor object was fetched implicitly")

    def test_trusted_archive_cleanup_preserves_a_replacement_staging_directory(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-cleanup-race-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            commit = self._create_repo(repository, "original")
            (repository / "large.bin").write_bytes(os.urandom(2 * 1024 * 1024))
            run(["/usr/bin/git", "add", "large.bin"], cwd=repository)
            run(["/usr/bin/git", "commit", "-q", "--amend", "--no-edit"], cwd=repository)
            commit = run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=repository).stdout.strip()
            command = [
                str(TRUSTED_GIT),
                "-C",
                str(repository),
                "-c",
                "tar.umask=0022",
                "archive",
                "--format=tar",
                "--prefix=taiji-agentv1.0/",
                commit,
            ]
            existing = set(Path("/tmp").glob("taiji-trusted-git-archive.*"))
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            candidate = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process.poll() is None:
                created = set(Path("/tmp").glob("taiji-trusted-git-archive.*")) - existing
                if created:
                    candidate = next(iter(created))
                    break
                time.sleep(0.01)
            self.assertIsNotNone(candidate)
            assert candidate is not None
            while time.monotonic() < deadline and process.poll() is None:
                if list((candidate / "repository.git/objects/pack").glob("*.pack")):
                    break
                time.sleep(0.01)
            self.assertTrue(
                list((candidate / "repository.git/objects/pack").glob("*.pack")),
                "private closure was not materialized before the cleanup race",
            )
            displaced = root / "displaced-private-object-view"
            candidate.rename(displaced)
            candidate.mkdir(mode=0o700)
            marker = candidate / "foreign-marker"
            marker.write_text("foreign\n", encoding="utf-8")
            try:
                _stdout, stderr = process.communicate(timeout=30)
                self.assertNotEqual(process.returncode, 0, stderr.decode("utf-8", "replace"))
                self.assertEqual(marker.read_text(encoding="utf-8"), "foreign\n")
                self.assertRegex(
                    stderr.decode("utf-8", "replace"),
                    "cleanup|identity|private|poison|foreign",
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                if candidate.exists() and candidate.is_dir():
                    shutil.rmtree(candidate)

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
                    [
                        str(TRUSTED_GIT),
                        "-C",
                        str(expected_repo),
                        "-c",
                        "tar.umask=0022",
                        "archive",
                        "--format=tar",
                        "--prefix=taiji-agentv1.0/",
                        expected_commit,
                    ],
                    env=hostile,
                    check=True,
                    stdout=archive,
                )
            with tarfile.open(archive_path) as archive:
                members = set(archive.getnames())
            self.assertIn("taiji-agentv1.0/expected.txt", members)
            self.assertNotIn("taiji-agentv1.0/hostile.txt", members)

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
                        "-c",
                        "tar.umask=0022",
                        "archive",
                        "--format=tar",
                        "--prefix=taiji-agentv1.0/",
                        original_commit,
                    ],
                    check=True,
                    stdout=archive,
                )
            with tarfile.open(archive_path) as archive:
                archived_payload = archive.extractfile("taiji-agentv1.0/original.txt")
                self.assertIsNotNone(archived_payload)
                self.assertEqual(archived_payload.read(), b"original\n")

    def test_trusted_git_ignores_user_global_archive_attributes(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-config-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            expected_commit = self._create_repo(repository, "expected")
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
                    [
                        str(TRUSTED_GIT),
                        "-C",
                        str(repository),
                        "-c",
                        "tar.umask=0022",
                        "archive",
                        "--format=tar",
                        "--prefix=taiji-agentv1.0/",
                        expected_commit,
                    ],
                    env=hostile,
                    check=True,
                    stdout=archive,
                )
            with tarfile.open(archive_path) as archive:
                self.assertIn("taiji-agentv1.0/expected.txt", archive.getnames())

    def test_trusted_archive_ignores_repo_info_and_worktree_attribute_swaps(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-attributes-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            original_commit = self._create_repo(repository, "original")
            tracked_attributes = repository / ".gitattributes"
            tracked_attributes.write_text("# frozen attributes\n", encoding="utf-8")
            run(["/usr/bin/git", "add", tracked_attributes.name], cwd=repository)
            run(
                ["/usr/bin/git", "commit", "-q", "--amend", "--no-edit"],
                cwd=repository,
            )
            original_commit = run(
                ["/usr/bin/git", "rev-parse", "HEAD"], cwd=repository
            ).stdout.strip()
            run(
                [
                    "/usr/bin/git",
                    "update-index",
                    "--assume-unchanged",
                    tracked_attributes.name,
                ],
                cwd=repository,
            )
            tracked_attributes.write_text(
                "original.txt export-ignore\n", encoding="utf-8"
            )
            info_attributes = repository / ".git/info/attributes"
            info_attributes.write_text(
                "original.txt export-ignore\n", encoding="utf-8"
            )
            local_attributes = repository / ".git/local.attributes"
            local_attributes.write_text(
                "original.txt export-ignore\n", encoding="utf-8"
            )
            run(
                [
                    "/usr/bin/git",
                    "config",
                    "--local",
                    "core.attributesFile",
                    str(local_attributes),
                ],
                cwd=repository,
            )
            self.assertEqual(
                run(
                    ["/usr/bin/git", "status", "--porcelain=v1"], cwd=repository
                ).stdout,
                "",
            )

            archive_path = root / "trusted.tar"
            with archive_path.open("wb") as archive:
                subprocess.run(
                    [
                        str(TRUSTED_GIT),
                        "-C",
                        str(repository),
                        "-c",
                        "tar.umask=0022",
                        "archive",
                        "--format=tar",
                        "--prefix=taiji-agentv1.0/",
                        original_commit,
                    ],
                    check=True,
                    stdout=archive,
                )
            with tarfile.open(archive_path) as archive:
                self.assertIn("taiji-agentv1.0/original.txt", archive.getnames())

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
        self.assertIn('GIT_NO_LAZY_FETCH="1"', source)
        self.assertIn("pack-objects --stdout --revs", source)
        self.assertIn("index-pack --stdin --strict", source)
        self.assertIn("fsck --strict --full", source)
        self.assertNotIn(
            '> "$_taiji_private_git/objects/info/alternates"',
            source,
        )

    def test_trusted_archive_rejects_symbolic_or_unfixed_invocations(self):
        with tempfile.TemporaryDirectory(prefix="taiji-trusted-git-shape-") as temporary:
            repository = Path(temporary) / "repository"
            self._create_repo(repository, "expected")
            symbolic = subprocess.run(
                [
                    str(TRUSTED_GIT),
                    "-C",
                    str(repository),
                    "-c",
                    "tar.umask=0022",
                    "archive",
                    "--format=tar",
                    "--prefix=taiji-agentv1.0/",
                    "HEAD",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(symbolic.returncode, 0)
            self.assertIn("explicit full lowercase commit F", symbolic.stderr)

            unfixed = subprocess.run(
                [str(TRUSTED_GIT), "-C", str(repository), "archive", "HEAD"],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(unfixed.returncode, 0)
            self.assertIn("outside the fixed frozen-object contract", unfixed.stderr)

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
