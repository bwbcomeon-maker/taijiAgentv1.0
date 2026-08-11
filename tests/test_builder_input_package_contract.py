"""Strict builder-input package allowlist and identity contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging/linux/builder-input-package.py"
SOURCE_INTEGRITY_HELPER = ROOT / "packaging/linux/source-archive-integrity.py"
PREPARE = ROOT / "taijiagent 打包交付/99_本机_准备制包输入包.sh"
OPERATIONS = ROOT / "taijiagent 打包交付/操作说明.md"
RUNBOOK = ROOT / "docs/runbooks/taiji-kylin-uos-offline-delivery.md"
BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
PREFLIGHT = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
COMMIT = "a" * 40
STATIC_INPUT_NAMES = {
    "00_制包机_生成离线交付包.sh",
    "01_制包机_发布预检.sh",
    "02_目标终端_安装并验证.sh",
    "03_目标终端_导出诊断报告.sh",
    "04_目标终端_桌面App验收并导出证据.sh",
    "99_本机_准备制包输入包.sh",
    "操作说明.md",
    "版本信息.txt",
    "SHA256SUMS.txt",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_helper(path: Path = HELPER):
    spec = importlib.util.spec_from_file_location("taiji_builder_input_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load builder-input helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_source_integrity_helper():
    spec = importlib.util.spec_from_file_location(
        "taiji_source_integrity_contract",
        SOURCE_INTEGRITY_HELPER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load source-integrity helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuilderInputPackageContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-builder-input-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "taijiagent 打包交付"
        self.source.mkdir(mode=0o700)
        for name in STATIC_INPUT_NAMES - {"SHA256SUMS.txt"}:
            path = self.source / name
            path.write_text("fixture:" + name + "\n", encoding="utf-8")
            path.chmod(0o755 if name.endswith(".sh") else 0o644)
        self.source_integrity_helper = self.root / "source-archive-integrity.py"
        self.source_integrity_helper.write_bytes(SOURCE_INTEGRITY_HELPER.read_bytes())
        self.source_integrity_helper.chmod(SOURCE_INTEGRITY_HELPER.stat().st_mode & 0o777)
        self.source_archive = (
            self.source / f"taiji-agentv1.0-kylin-build-src-{COMMIT}.tar.gz"
        )
        with tarfile.open(self.source_archive, "w:gz") as archive:
            frozen_members = []
            for name in sorted(STATIC_INPUT_NAMES - {"SHA256SUMS.txt"}):
                path = self.source / name
                frozen_members.append(
                    (
                        f"taiji-agentv1.0/taijiagent 打包交付/{name}",
                        path.read_bytes(),
                        path.stat().st_mode & 0o777,
                    )
                )
            frozen_members.extend(
                (
                    (
                        "taiji-agentv1.0/packaging/linux/source-archive-integrity.py",
                        self.source_integrity_helper.read_bytes(),
                        0o644,
                    ),
                    (
                        "taiji-agentv1.0/packaging/linux/builder-input-package.py",
                        HELPER.read_bytes(),
                        HELPER.stat().st_mode & 0o777,
                    ),
                )
            )
            for member_name, payload, mode in frozen_members:
                info = tarfile.TarInfo(member_name)
                info.size = len(payload)
                info.mode = mode
                info.uid = os.getuid()
                info.gid = os.getgid()
                info.mtime = 0
                archive.addfile(info, io.BytesIO(payload))
        self.source_inventory = self.source / (
            f"taiji-agentv1.0-kylin-build-src-{COMMIT}.inventory.json"
        )
        source_helper = load_source_integrity_helper()
        inventory_payload = source_helper._canonical_bytes(
            source_helper.build_inventory(self.source_archive, COMMIT)
        )
        self.source_inventory.write_bytes(inventory_payload)
        (self.source / "SHA256SUMS.txt").write_text(
            f"{sha256(self.source_archive)}  {self.source_archive.name}\n"
            f"{sha256(self.source_inventory)}  {self.source_inventory.name}\n",
            encoding="ascii",
        )
        self.output = self.root / f"taijiagent-制包机输入-{COMMIT}.tar.gz"
        self.manifest = self.root / f"taijiagent-制包机输入-{COMMIT}.manifest.json"
        self.checksum = self.root / f"{self.output.name}.sha256"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self):
        return load_helper().create_builder_input(
            source_dir=self.source,
            source_integrity_helper=self.source_integrity_helper,
            output=self.output,
            manifest_path=self.manifest,
            checksum_path=self.checksum,
            source_commit=COMMIT,
        )

    def test_exact_allowlist_creates_bound_archive_manifest_and_sidecar(self):
        (self.source / "release-evidence.json").write_text("must not enter input\n")
        (self.source / "生成的安装包").mkdir()
        (self.source / "生成的安装包/old.deb").write_bytes(b"old")

        result = self.create()

        expected_names = STATIC_INPUT_NAMES | {
            self.source_archive.name,
            self.source_inventory.name,
            "source-archive-integrity.py",
            "builder-input-package.py",
        }
        with tarfile.open(self.output, "r:gz") as archive:
            members = archive.getmembers()
            self.assertEqual(
                {Path(member.name).name for member in members if member.isfile()},
                expected_names,
            )
            self.assertTrue(all(member.isfile() for member in members))
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "taiji-builder-input-package/v1")
        self.assertEqual(payload["source_commit"], COMMIT)
        self.assertEqual(payload["archive_basename"], self.output.name)
        self.assertEqual(payload["archive_size"], self.output.stat().st_size)
        self.assertEqual(payload["archive_sha256"], sha256(self.output))
        self.assertEqual(payload["source_archive_sha256"], sha256(self.source_archive))
        self.assertEqual(payload["source_inventory_sha256"], sha256(self.source_inventory))
        self.assertEqual(payload["source_integrity_helper_sha256"], sha256(self.source_integrity_helper))
        self.assertEqual(payload["builder_input_helper_sha256"], sha256(HELPER))
        self.assertEqual(payload["manifest_basename"], self.manifest.name)
        self.assertEqual(payload["checksum_basename"], self.checksum.name)
        self.assertEqual({item["basename"] for item in payload["members"]}, expected_names)
        self.assertEqual(
            self.checksum.read_text(encoding="utf-8"),
            f"{sha256(self.output)}  {self.output.name}\n"
            f"{sha256(self.manifest)}  {self.manifest.name}\n",
        )
        self.assertEqual(result["archive_sha256"], sha256(self.output))

    def test_verifier_binds_archive_manifest_sidecar_and_extracted_allowlist(self):
        helper = load_helper()
        expected = self.create()
        extracted_parent = self.root / "extracted"
        extracted_parent.mkdir()
        with tarfile.open(self.output, "r:gz") as archive:
            archive.extractall(extracted_parent)
        extracted = extracted_parent / self.source.name

        verified = helper.verify_builder_input(
            archive_path=self.output,
            manifest_path=self.manifest,
            checksum_path=self.checksum,
            extracted_dir=extracted,
        )

        self.assertEqual(verified, expected)

    def test_verifier_rejects_tampered_archive_manifest_sidecar_or_extracted_member(self):
        helper = load_helper()

        def prepare_case():
            for path in (self.output, self.manifest, self.checksum):
                if path.exists() or path.is_symlink():
                    path.unlink()
            self.create()
            extracted_parent = self.root / "verify-case"
            if extracted_parent.exists():
                import shutil

                shutil.rmtree(extracted_parent)
            extracted_parent.mkdir()
            with tarfile.open(self.output, "r:gz") as archive:
                archive.extractall(extracted_parent)
            return extracted_parent / self.source.name

        mutations = (
            ("archive", lambda extracted: self.output.write_bytes(self.output.read_bytes() + b"x")),
            ("manifest", lambda extracted: self.manifest.write_text("{}\n", encoding="utf-8")),
            ("sidecar", lambda extracted: self.checksum.write_text("0" * 64 + "  wrong\n", encoding="ascii")),
            (
                "sidecar-order",
                lambda extracted: self.checksum.write_text(
                    "".join(reversed(self.checksum.read_text(encoding="utf-8").splitlines(keepends=True))),
                    encoding="utf-8",
                ),
            ),
            (
                "extracted-member",
                lambda extracted: (extracted / "00_制包机_生成离线交付包.sh").write_text(
                    "tampered\n", encoding="utf-8"
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                extracted = prepare_case()
                mutate(extracted)
                with self.assertRaises(Exception):
                    helper.verify_builder_input(
                        archive_path=self.output,
                        manifest_path=self.manifest,
                        checksum_path=self.checksum,
                        extracted_dir=extracted,
                    )

    def test_missing_symlink_or_hardlinked_allowlisted_input_fails_without_partial_outputs(self):
        cases = []
        missing = self.source / "操作说明.md"
        missing.unlink()
        cases.append("missing")
        with self.subTest(case="missing"):
            with self.assertRaises(Exception):
                self.create()
            self.assertFalse(self.output.exists())
            self.assertFalse(self.manifest.exists())
            self.assertFalse(self.checksum.exists())

        missing.write_text("restored\n", encoding="utf-8")
        script = self.source / "00_制包机_生成离线交付包.sh"
        script.unlink()
        script.symlink_to(self.source / "01_制包机_发布预检.sh")
        cases.append("symlink")
        with self.subTest(case="symlink"):
            with self.assertRaises(Exception):
                self.create()
            self.assertFalse(self.output.exists())

        script.unlink()
        os.link(self.source / "01_制包机_发布预检.sh", script)
        cases.append("hardlink")
        with self.subTest(case="hardlink"):
            with self.assertRaises(Exception):
                self.create()
            self.assertFalse(self.output.exists())
        self.assertEqual(cases, ["missing", "symlink", "hardlink"])

    def test_symlinked_source_directory_is_rejected_without_partial_outputs(self):
        linked_source = self.root / "linked-builder-input"
        linked_source.symlink_to(self.source, target_is_directory=True)

        with self.assertRaisesRegex(Exception, "source directory is unsafe"):
            load_helper().create_builder_input(
                source_dir=linked_source,
                source_integrity_helper=self.source_integrity_helper,
                output=self.output,
                manifest_path=self.manifest,
                checksum_path=self.checksum,
                source_commit=COMMIT,
            )

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_group_or_other_writable_source_directory_is_rejected(self):
        self.source.chmod(0o777)

        with self.assertRaisesRegex(Exception, "source directory is unsafe"):
            self.create()

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_worktree_member_changed_after_frozen_archive_is_rejected(self):
        changed = self.source / "00_制包机_生成离线交付包.sh"
        changed.write_text("post-freeze mutation\n", encoding="utf-8")
        changed.chmod(0o755)

        with self.assertRaisesRegex(Exception, "differs from frozen source commit"):
            self.create()

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_matching_checksum_cannot_make_an_invalid_source_inventory_publishable(self):
        self.source_inventory.write_text('{"schema":"forged"}\n', encoding="utf-8")
        (self.source / "SHA256SUMS.txt").write_text(
            f"{sha256(self.source_archive)}  {self.source_archive.name}\n"
            f"{sha256(self.source_inventory)}  {self.source_inventory.name}\n",
            encoding="ascii",
        )

        with self.assertRaisesRegex(Exception, "source inventory|archive-derived"):
            self.create()

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_assume_unchanged_replacement_cannot_bypass_frozen_member_check(self):
        if shutil.which("git") is None:
            self.skipTest("git is required for the assume-unchanged regression")
        subprocess.run(
            ["git", "init"],
            cwd=self.root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Taiji Test",
                "-c",
                "user.email=taiji-test@example.invalid",
                "commit",
                "-m",
                "fixture",
            ],
            cwd=self.root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        relative = Path("taijiagent 打包交付/00_制包机_生成离线交付包.sh")
        subprocess.run(
            ["git", "update-index", "--assume-unchanged", str(relative)],
            cwd=self.root,
            check=True,
        )
        changed = self.root / relative
        changed.unlink()
        changed.write_text("hidden replacement\n", encoding="utf-8")
        changed.chmod(0o755)
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(status.stdout, "", "fixture must reproduce a Git-hidden drift")

        with self.assertRaisesRegex(Exception, "differs from frozen source commit"):
            self.create()

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_mode_drift_cannot_enter_frozen_bundle(self):
        changed = self.source / "00_制包机_生成离线交付包.sh"
        changed.chmod(0o644)

        with self.assertRaisesRegex(Exception, "differs from frozen source commit"):
            self.create()

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_builder_helper_changed_after_freeze_is_rejected(self):
        changed_helper = self.root / "builder-input-package.py"
        changed_helper.write_bytes(HELPER.read_bytes() + b"\n# post-freeze drift\n")
        changed_helper.chmod(HELPER.stat().st_mode & 0o777)
        changed_module = load_helper(changed_helper)

        with self.assertRaisesRegex(Exception, "helper differs from frozen source commit"):
            changed_module.create_builder_input(
                source_dir=self.source,
                source_integrity_helper=self.source_integrity_helper,
                output=self.output,
                manifest_path=self.manifest,
                checksum_path=self.checksum,
                source_commit=COMMIT,
            )

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_source_integrity_helper_changed_after_freeze_is_rejected(self):
        self.source_integrity_helper.write_text(
            "# post-freeze source helper drift\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            Exception,
            "source archive integrity helper differs from frozen source commit",
        ):
            self.create()

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_exclusive_writer_removes_partial_file_on_write_failure(self):
        helper = load_helper()
        partial = self.root / "partial-output"

        with mock.patch.object(helper.os, "write", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                helper._write_exclusive(partial, b"payload")

        self.assertFalse(partial.exists())

    def test_exclusive_writer_removes_owned_file_when_descriptor_close_fails(self):
        helper = load_helper()
        partial = self.root / "close-failed-output"
        original_close = helper.os.close

        def close_then_fail(descriptor):
            original_close(descriptor)
            raise OSError("fixture close failure")

        with mock.patch.object(helper.os, "close", side_effect=close_then_fail):
            with self.assertRaises(OSError):
                helper._write_exclusive(partial, b"payload")

        self.assertFalse(partial.exists())

    def test_rollback_never_unlinks_a_replacement_at_an_owned_output_path(self):
        helper = load_helper()
        original_write = helper._write_exclusive
        calls = 0

        def replace_then_fail(path, payload, mode=0o644, directory_fd=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_write(path, payload, mode, directory_fd)
            self.output.unlink()
            self.output.write_bytes(b"foreign replacement")
            raise helper.BuilderInputError("fixture second member failure")

        with mock.patch.object(helper, "_write_exclusive", side_effect=replace_then_fail):
            with self.assertRaisesRegex(Exception, "rollback|cleanup|poison"):
                helper.create_builder_input(
                    source_dir=self.source,
                    source_integrity_helper=self.source_integrity_helper,
                    output=self.output,
                    manifest_path=self.manifest,
                    checksum_path=self.checksum,
                    source_commit=COMMIT,
                )

        self.assertEqual(self.output.read_bytes(), b"foreign replacement")
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_publication_records_descriptor_identity_not_a_replacement_path_identity(self):
        helper = load_helper()
        original_write = helper._write_exclusive
        calls = 0

        def replace_before_return_then_fail(path, payload, mode=0o644, directory_fd=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                owned_identity = original_write(path, payload, mode, directory_fd)
                self.output.unlink()
                self.output.write_bytes(b"foreign replacement in return window")
                return owned_identity
            raise helper.BuilderInputError("fixture second member failure")

        with mock.patch.object(
            helper,
            "_write_exclusive",
            side_effect=replace_before_return_then_fail,
        ):
            with self.assertRaisesRegex(Exception, "rollback|cleanup|poison"):
                helper.create_builder_input(
                    source_dir=self.source,
                    source_integrity_helper=self.source_integrity_helper,
                    output=self.output,
                    manifest_path=self.manifest,
                    checksum_path=self.checksum,
                    source_commit=COMMIT,
                )

        self.assertEqual(
            self.output.read_bytes(),
            b"foreign replacement in return window",
        )
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_success_path_rejects_an_identical_payload_replacement_inode(self):
        helper = load_helper()
        original_write = helper._write_exclusive
        calls = 0
        replacement_payload = b""

        def replace_third_before_return(path, payload, mode=0o644, directory_fd=None):
            nonlocal calls, replacement_payload
            calls += 1
            owned_identity = original_write(path, payload, mode, directory_fd)
            if calls == 3:
                replacement_payload = bytes(payload)
                self.checksum.unlink()
                self.checksum.write_bytes(replacement_payload)
            return owned_identity

        with mock.patch.object(
            helper,
            "_write_exclusive",
            side_effect=replace_third_before_return,
        ):
            with self.assertRaisesRegex(Exception, "identity|rollback|cleanup|poison"):
                helper.create_builder_input(
                    source_dir=self.source,
                    source_integrity_helper=self.source_integrity_helper,
                    output=self.output,
                    manifest_path=self.manifest,
                    checksum_path=self.checksum,
                    source_commit=COMMIT,
                )

        self.assertEqual(self.checksum.read_bytes(), replacement_payload)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())

    def test_incomplete_rollback_is_reported_instead_of_silently_swallowed(self):
        helper = load_helper()
        original_write = helper._write_exclusive
        calls = 0

        def fail_second(path, payload, mode=0o644, directory_fd=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_write(path, payload, mode, directory_fd)
            raise helper.BuilderInputError("fixture second member failure")

        original_unlink = helper.os.unlink

        def fail_owned_unlink(path, *args, **kwargs):
            if path == self.output.name and kwargs.get("dir_fd") is not None:
                raise PermissionError("fixture unlink failure")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(helper, "_write_exclusive", side_effect=fail_second), mock.patch.object(
            helper.os,
            "unlink",
            side_effect=fail_owned_unlink,
        ):
            with self.assertRaisesRegex(Exception, "rollback incomplete|cleanup incomplete|poison"):
                helper.create_builder_input(
                    source_dir=self.source,
                    source_integrity_helper=self.source_integrity_helper,
                    output=self.output,
                    manifest_path=self.manifest,
                    checksum_path=self.checksum,
                    source_commit=COMMIT,
                )

        self.assertTrue(self.output.exists())

    def test_failed_publication_reports_incomplete_private_stage_cleanup(self):
        helper = load_helper()
        calls = 0

        def fail_first_final(path, payload, mode=0o644, directory_fd=None):
            nonlocal calls
            calls += 1
            raise helper.BuilderInputError("fixture publication failure")

        with mock.patch.object(helper, "_write_exclusive", side_effect=fail_first_final), mock.patch.object(
            helper,
            "_cleanup_private_stage",
            return_value=["fixture private stage cleanup failure"],
        ):
            with self.assertRaisesRegex(Exception, "staging cleanup.*incomplete|cleanup.*poison"):
                helper.create_builder_input(
                    source_dir=self.source,
                    source_integrity_helper=self.source_integrity_helper,
                    output=self.output,
                    manifest_path=self.manifest,
                    checksum_path=self.checksum,
                    source_commit=COMMIT,
                )

        self.assertEqual(calls, 1)

    def test_output_parent_symlink_is_not_a_controlled_publication_directory(self):
        real_parent = self.root / "real-publication"
        real_parent.mkdir(mode=0o700)
        linked_parent = self.root / "linked-publication"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        output = linked_parent / self.output.name
        manifest = linked_parent / self.manifest.name
        checksum = linked_parent / self.checksum.name

        with self.assertRaisesRegex(Exception, "output.*directory|publication.*directory|unsafe"):
            load_helper().create_builder_input(
                source_dir=self.source,
                source_integrity_helper=self.source_integrity_helper,
                output=output,
                manifest_path=manifest,
                checksum_path=checksum,
                source_commit=COMMIT,
            )

        self.assertEqual(list(real_parent.iterdir()), [])

    def test_publication_directory_replacement_is_detected_and_foreign_directory_is_preserved(self):
        helper = load_helper()
        publication = self.root / "publication"
        publication.mkdir(mode=0o700)
        displaced = self.root / "publication-displaced"
        output = publication / self.output.name
        manifest = publication / self.manifest.name
        checksum = publication / self.checksum.name
        original_write = helper._write_exclusive
        calls = 0

        def replace_parent_after_first_write(path, payload, mode=0o644, **kwargs):
            nonlocal calls
            calls += 1
            result = original_write(path, payload, mode, **kwargs)
            if calls == 1:
                publication.rename(displaced)
                publication.mkdir(mode=0o700)
                (publication / "foreign-marker").write_text("foreign\n", encoding="utf-8")
            return result

        with mock.patch.object(helper, "_write_exclusive", side_effect=replace_parent_after_first_write):
            with self.assertRaisesRegex(Exception, "directory.*changed|incomplete|poison"):
                helper.create_builder_input(
                    source_dir=self.source,
                    source_integrity_helper=self.source_integrity_helper,
                    output=output,
                    manifest_path=manifest,
                    checksum_path=checksum,
                    source_commit=COMMIT,
                )

        self.assertEqual((publication / "foreign-marker").read_text(encoding="utf-8"), "foreign\n")
        self.assertEqual(list(displaced.iterdir()), [])

    def test_verified_triplet_can_be_safely_withdrawn_without_half_outputs(self):
        helper = load_helper()
        self.create()

        helper.withdraw_builder_input(
            archive_path=self.output,
            manifest_path=self.manifest,
            checksum_path=self.checksum,
        )

        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.checksum.exists())

    def test_prepare_script_uses_pinned_helper_and_has_no_denylist_walk(self):
        source = PREPARE.read_text(encoding="utf-8")
        self.assertIn("builder-input-package.py", source)
        self.assertIn("BUILDER_INPUT_HELPER_SHA256", source)
        self.assertIn("taijiagent-制包机输入-$commit.manifest.json", source)
        self.assertIn("$output.sha256", source)
        self.assertNotIn("skip_dirs", source)
        self.assertNotIn("os.walk(source)", source)
        self.assertIn('"$FROZEN_BUILDER_INPUT_HELPER" verify', source)
        for role, variable in (
            ("archive", "$output"),
            ("manifest", "$manifest"),
            ("sidecar", "$checksum"),
        ):
            with self.subTest(role=role):
                self.assertIn(
                    f'record_triplet_member "{role}" "{variable}"',
                    source,
                )
        self.assertIn(
            "basename=%s bytes=%s sha256=%s",
            source,
        )
        create_call = source.index('"$FROZEN_BUILDER_INPUT_HELPER" create')
        verify_call = source.index('"$FROZEN_BUILDER_INPUT_HELPER" verify')
        self.assertLess(create_call, verify_call)
        for role in ("archive", "manifest", "sidecar"):
            self.assertLess(
                verify_call,
                source.index(f'record_triplet_member "{role}"'),
            )
        match = re.search(
            r'^BUILDER_INPUT_HELPER_SHA256="([0-9a-f]{64})"$',
            source,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), sha256(HELPER))

    def test_docs_do_not_present_the_input_sidecar_as_a_signature_trust_root(self):
        required = "不是 detached signature，也不是可对抗恶意替换者的签名信任根"
        for document in (OPERATIONS, RUNBOOK):
            with self.subTest(document=document.name):
                self.assertIn(required, document.read_text(encoding="utf-8"))

    def test_all_source_archives_use_one_explicit_frozen_commit_and_fixed_tar_umask(self):
        for script in (PREPARE, BUILDER, PREFLIGHT):
            source = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertIn("FROZEN_SOURCE_COMMIT", source)
                self.assertIn("-c tar.umask=0022 archive --format=tar", source)
                self.assertNotRegex(source, r"archive[^\n|]*\bHEAD\b")
                self.assertIn('"$FROZEN_SOURCE_COMMIT"', source)

    def test_prepare_uses_a_private_staging_triplet_and_rechecks_f_before_publication(self):
        source = PREPARE.read_text(encoding="utf-8")
        self.assertIn("capture_frozen_source_identity", source)
        self.assertIn("verify_frozen_source_identity", source)
        self.assertIn("stage_frozen_helpers", source)
        self.assertIn("BUILDER_INPUT_STAGE", source)
        self.assertIn('"$FROZEN_BUILDER_INPUT_HELPER" publish', source)
        self.assertLess(
            source.index("verify_frozen_source_identity", source.index("write_builder_input_package()")),
            source.index('"$FROZEN_BUILDER_INPUT_HELPER" publish'),
        )


if __name__ == "__main__":
    unittest.main()
