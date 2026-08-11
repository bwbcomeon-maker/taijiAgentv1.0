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
PREPARE = ROOT / "taijiagent 打包交付/99_本机_准备制包输入包.sh"
OPERATIONS = ROOT / "taijiagent 打包交付/操作说明.md"
RUNBOOK = ROOT / "docs/runbooks/taiji-kylin-uos-offline-delivery.md"
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
        self.source_integrity_helper.write_text("# pinned helper\n", encoding="utf-8")
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
        self.source_inventory.write_text('{"schema":"fixture"}\n', encoding="utf-8")
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

    def test_prepare_script_uses_pinned_helper_and_has_no_denylist_walk(self):
        source = PREPARE.read_text(encoding="utf-8")
        self.assertIn("builder-input-package.py", source)
        self.assertIn("BUILDER_INPUT_HELPER_SHA256", source)
        self.assertIn("taijiagent-制包机输入-$commit.manifest.json", source)
        self.assertIn("$output.sha256", source)
        self.assertNotIn("skip_dirs", source)
        self.assertNotIn("os.walk(source)", source)
        self.assertIn('"$BUILDER_INPUT_HELPER" verify', source)
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
        create_call = source.index('"$BUILDER_INPUT_HELPER" create')
        verify_call = source.index('"$BUILDER_INPUT_HELPER" verify')
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


if __name__ == "__main__":
    unittest.main()
