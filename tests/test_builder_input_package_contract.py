"""Strict builder-input package allowlist and identity contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging/linux/builder-input-package.py"
PREPARE = ROOT / "taijiagent 打包交付/99_本机_准备制包输入包.sh"
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


def load_helper():
    spec = importlib.util.spec_from_file_location("taiji_builder_input_contract", HELPER)
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
        self.source_archive = (
            self.source / f"taiji-agentv1.0-kylin-build-src-{COMMIT}.tar.gz"
        )
        self.source_archive.write_bytes(b"canonical source archive\n")
        self.source_inventory = self.source / (
            f"taiji-agentv1.0-kylin-build-src-{COMMIT}.inventory.json"
        )
        self.source_inventory.write_text('{"schema":"fixture"}\n', encoding="utf-8")
        (self.source / "SHA256SUMS.txt").write_text(
            f"{sha256(self.source_archive)}  {self.source_archive.name}\n"
            f"{sha256(self.source_inventory)}  {self.source_inventory.name}\n",
            encoding="ascii",
        )
        self.source_integrity_helper = self.root / "source-archive-integrity.py"
        self.source_integrity_helper.write_text("# pinned helper\n", encoding="utf-8")
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
        self.assertIn("制包机输入 manifest 字节数", source)
        self.assertIn("制包机输入 manifest SHA256", source)
        self.assertIn("制包机输入 sidecar 字节数", source)
        self.assertIn("制包机输入 sidecar SHA256", source)
        create_call = source.index('"$BUILDER_INPUT_HELPER" create')
        verify_call = source.index('"$BUILDER_INPUT_HELPER" verify')
        self.assertLess(create_call, verify_call)
        match = re.search(
            r'^BUILDER_INPUT_HELPER_SHA256="([0-9a-f]{64})"$',
            source,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), sha256(HELPER))


if __name__ == "__main__":
    unittest.main()
