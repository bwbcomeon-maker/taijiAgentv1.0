"""Dynamic frozen-source contract tests for the formal local input producer."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Tuple


ROOT = Path(__file__).resolve().parents[1]
PREPARE_RELATIVE = Path("taijiagent 打包交付/99_本机_准备制包输入包.sh")
FIXTURE_PATHS = (
    Path(".gitignore"),
    Path("scripts/check-clean-worktree.sh"),
    Path("scripts/taiji-trusted-git"),
    Path("packaging/linux/source-archive-integrity.py"),
    Path("packaging/linux/builder-input-package.py"),
    Path("packaging/linux/verify-python-lock-contract.py"),
    Path("packaging/linux/deb/build-deb.sh"),
    Path("hermes-local-lab/scripts/setup-local.sh"),
    Path("hermes-local-lab/sources/hermes-webui/requirements.txt"),
    Path("hermes-local-lab/sources/hermes-agent/pyproject.toml"),
    Path("hermes-local-lab/sources/hermes-agent/uv.lock"),
    Path("taijiagent 打包交付/00_制包机_生成离线交付包.sh"),
    Path("taijiagent 打包交付/01_制包机_发布预检.sh"),
    Path("taijiagent 打包交付/02_目标终端_安装并验证.sh"),
    Path("taijiagent 打包交付/03_目标终端_导出诊断报告.sh"),
    Path("taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh"),
    PREPARE_RELATIVE,
    Path("taijiagent 打包交付/操作说明.md"),
    Path("taijiagent 打包交付/版本信息.txt"),
)


class FrozenSourcePrepareContractTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None or shutil.which("gzip") is None:
            self.skipTest("git and gzip are required")
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-frozen-prepare-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_primary_main(self, name: str) -> Tuple[Path, str]:
        repository = self.root / name
        repository.mkdir(mode=0o700)
        for relative in FIXTURE_PATHS:
            source = ROOT / relative
            destination = repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
        subprocess.run(["git", "init"], cwd=repository, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=repository,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=repository, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Taiji Test",
                "-c",
                "user.email=taiji-test@example.invalid",
                "commit",
                "-m",
                "frozen fixture",
            ],
            cwd=repository,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
        return repository, commit

    def run_prepare(self, repository: Path) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment["TMPDIR"] = str(self.root)
        return subprocess.run(
            ["/bin/bash", str(repository / PREPARE_RELATIVE)],
            cwd=repository,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )

    def test_clean_primary_main_publishes_one_verified_triplet(self) -> None:
        repository, commit = self.make_primary_main("success")

        result = self.run_prepare(repository)

        self.assertEqual(result.returncode, 0, result.stdout)
        expected = {
            repository / f"taijiagent-制包机输入-{commit}.tar.gz",
            repository / f"taijiagent-制包机输入-{commit}.manifest.json",
            repository / f"taijiagent-制包机输入-{commit}.tar.gz.sha256",
        }
        self.assertTrue(all(path.is_file() for path in expected), result.stdout)
        self.assertIn("role=archive", result.stdout)
        self.assertIn("role=manifest", result.stdout)
        self.assertIn("role=sidecar", result.stdout)

    def test_unsafe_tmpdir_parent_is_rejected_before_private_staging(self) -> None:
        repository, _commit = self.make_primary_main("unsafe-tmpdir")
        unsafe = self.root / "unsafe-tmp"
        unsafe.mkdir(mode=0o775)
        unsafe.chmod(0o775)
        environment = os.environ.copy()
        environment["TMPDIR"] = str(unsafe)

        result = subprocess.run(
            ["/bin/bash", str(repository / PREPARE_RELATIVE)],
            cwd=repository,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout, "TMPDIR|private staging|unsafe|\u4e0d安全")
        self.assertEqual(list(unsafe.glob("taiji-frozen-builder-input.*")), [])

    def test_prepare_cleanup_preserves_a_replacement_staging_directory(self) -> None:
        repository, _commit = self.make_primary_main("cleanup-race")
        environment = os.environ.copy()
        environment["TMPDIR"] = str(self.root)
        process = subprocess.Popen(
            ["/bin/bash", str(repository / PREPARE_RELATIVE)],
            cwd=repository,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        self.assertIsNotNone(process.stdout)
        lines = []
        candidate = None
        displaced = self.root / "displaced-frozen-staging"
        for line in process.stdout:
            lines.append(line)
            if "从冻结 commit 生成源码包" in line and candidate is None:
                created = list(self.root.glob("taiji-frozen-builder-input.*"))
                self.assertEqual(len(created), 1, "".join(lines))
                candidate = created[0]
                candidate.rename(displaced)
                # Keep the replacement's uid/mode indistinguishable from the
                # original so the test specifically proves dev/ino binding.
                candidate.mkdir(mode=0o700)
                (candidate / "foreign-marker").write_text("foreign\n", encoding="utf-8")
        process.stdout.close()
        returncode = process.wait(timeout=120)
        output = "".join(lines)

        self.assertIsNotNone(candidate, output)
        assert candidate is not None
        self.assertNotEqual(returncode, 0, output)
        self.assertEqual(
            (candidate / "foreign-marker").read_text(encoding="utf-8"),
            "foreign\n",
        )
        self.assertRegex(output, "清理不完整|identity|poison|foreign")

    def test_main_advancing_from_f1_to_f2_is_rejected_without_published_triplet(self) -> None:
        repository, frozen = self.make_primary_main("race")
        tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True).strip()
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": "Taiji Test",
                "GIT_AUTHOR_EMAIL": "taiji-test@example.invalid",
                "GIT_COMMITTER_NAME": "Taiji Test",
                "GIT_COMMITTER_EMAIL": "taiji-test@example.invalid",
            }
        )
        advanced = subprocess.check_output(
            ["git", "commit-tree", tree, "-p", frozen, "-m", "concurrent advance"],
            cwd=repository,
            env=environment,
            text=True,
        ).strip()
        environment["TMPDIR"] = str(self.root)
        process = subprocess.Popen(
            ["/bin/bash", str(repository / PREPARE_RELATIVE)],
            cwd=repository,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        self.assertIsNotNone(process.stdout)
        lines = []
        advanced_ref = False
        for line in process.stdout:
            lines.append(line)
            if "在私有暂存目录生成制包机输入包" in line and not advanced_ref:
                subprocess.run(
                    ["git", "update-ref", "refs/heads/main", advanced, frozen],
                    cwd=repository,
                    check=True,
                )
                advanced_ref = True
        process.stdout.close()
        returncode = process.wait(timeout=120)
        output = "".join(lines)

        self.assertTrue(advanced_ref, output)
        self.assertNotEqual(returncode, 0, output)
        self.assertIn("偏离冻结 source commit", output)
        self.assertEqual(list(repository.glob("taijiagent-制包机输入-*")), [], output)

    def test_post_gate_outer_script_and_helper_drift_cannot_publish(self) -> None:
        repository, _frozen = self.make_primary_main("post-gate-drift")
        environment = os.environ.copy()
        environment["TMPDIR"] = str(self.root)
        process = subprocess.Popen(
            ["/bin/bash", str(repository / PREPARE_RELATIVE)],
            cwd=repository,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        self.assertIsNotNone(process.stdout)
        lines = []
        drifted = False
        for line in process.stdout:
            lines.append(line)
            if "在私有暂存目录生成制包机输入包" in line and not drifted:
                for relative in (
                    Path("taijiagent 打包交付/00_制包机_生成离线交付包.sh"),
                    Path("packaging/linux/builder-input-package.py"),
                ):
                    target = repository / relative
                    mode = target.stat().st_mode & 0o777
                    target.write_bytes(target.read_bytes() + b"\n# post-gate drift\n")
                    target.chmod(mode)
                drifted = True
        process.stdout.close()
        returncode = process.wait(timeout=120)
        output = "".join(lines)

        self.assertTrue(drifted, output)
        self.assertNotEqual(returncode, 0, output)
        self.assertIn("冻结 commit", output)
        self.assertEqual(list(repository.glob("taijiagent-制包机输入-*")), [], output)

    def test_replace_ref_is_rejected_before_input_publication(self) -> None:
        repository, frozen = self.make_primary_main("replace-ref")
        original_gitignore = (repository / ".gitignore").read_bytes()
        replacement_marker = b"\n# replacement-tree-marker\n"
        (repository / ".gitignore").write_bytes(original_gitignore + replacement_marker)
        subprocess.run(["git", "add", ".gitignore"], cwd=repository, check=True)
        replacement_tree = subprocess.check_output(
            ["git", "write-tree"], cwd=repository, text=True
        ).strip()
        replacement_commit = subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Taiji Test",
                "-c",
                "user.email=taiji-test@example.invalid",
                "commit-tree",
                replacement_tree,
                "-m",
                "replacement tree",
            ],
            cwd=repository,
            text=True,
        ).strip()
        subprocess.run(["git", "reset", "--hard", frozen], cwd=repository, check=True)
        subprocess.run(
            ["git", "replace", frozen, replacement_commit], cwd=repository, check=True
        )
        subprocess.run(["git", "reset", "--hard", frozen], cwd=repository, check=True)
        self.assertIn(replacement_marker, (repository / ".gitignore").read_bytes())
        self.assertEqual(
            subprocess.check_output(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=repository,
                text=True,
            ),
            "",
        )

        result = self.run_prepare(repository)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout, "dirty|replace|替换|冻结")
        self.assertEqual(list(repository.glob("taijiagent-制包机输入-*")), [])

    def test_repo_info_export_ignore_cannot_remove_a_frozen_tracked_member(self) -> None:
        repository, frozen = self.make_primary_main("info-attributes")
        info_attributes = repository / ".git/info/attributes"
        info_attributes.write_text(".gitignore export-ignore\n", encoding="utf-8")

        result = self.run_prepare(repository)

        self.assertEqual(result.returncode, 0, result.stdout)
        input_archive = repository / f"taijiagent-制包机输入-{frozen}.tar.gz"
        source_basename = f"taiji-agentv1.0-kylin-build-src-{frozen}.tar.gz"
        with tarfile.open(input_archive, mode="r:gz") as bundle:
            source_member = bundle.extractfile(
                f"taijiagent 打包交付/{source_basename}"
            )
            self.assertIsNotNone(source_member)
            source_payload = source_member.read()
        with tarfile.open(fileobj=io.BytesIO(source_payload), mode="r:gz") as source:
            self.assertIn("taiji-agentv1.0/.gitignore", source.getnames())


if __name__ == "__main__":
    unittest.main()
