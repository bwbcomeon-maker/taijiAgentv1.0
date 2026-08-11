from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging/linux/source-archive-integrity.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("taiji_source_integrity_test", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {HELPER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SourceArchiveIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-source-integrity-")
        self.root = Path(self.temporary.name)
        self.input = self.root / "input"
        self.source = self.input / "taiji-agentv1.0"
        (self.source / "bin").mkdir(parents=True)
        (self.source / "file.txt").write_text("canonical\n", encoding="utf-8")
        script = self.source / "bin/run.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        os.symlink("file.txt", self.source / "link.txt")
        self.archive = self.root / ("taiji-agentv1.0-kylin-build-src-" + "a" * 40 + ".tar.gz")
        with tarfile.open(self.archive, "w:gz") as bundle:
            bundle.add(self.source, arcname="taiji-agentv1.0", recursive=True)
        self.inventory = self.root / "source-archive-inventory.json"
        self.extracted = self.root / "extracted"
        self.extracted.mkdir()
        with tarfile.open(self.archive, "r:gz") as bundle:
            bundle.extractall(self.extracted)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_helper(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(HELPER), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def create_inventory(self) -> subprocess.CompletedProcess[str]:
        return self.run_helper(
            "create",
            "--archive", str(self.archive),
            "--inventory", str(self.inventory),
            "--source-commit", "a" * 40,
        )

    def verify_tree(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.run_helper(
            "verify",
            "--archive", str(self.archive),
            "--inventory", str(self.inventory),
            "--root", str(self.extracted / "taiji-agentv1.0"),
            *extra,
        )

    def test_archive_inventory_binds_exact_members_and_extracted_tree(self) -> None:
        created = self.create_inventory()
        self.assertEqual(created.returncode, 0, created.stderr)
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "taiji-source-archive-inventory/v1")
        self.assertEqual(payload["source_commit"], "a" * 40)
        self.assertEqual(
            payload["archive_sha256"],
            hashlib.sha256(self.archive.read_bytes()).hexdigest(),
        )
        verified = self.verify_tree()
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_rejects_original_member_content_or_mode_drift(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        (self.extracted / "taiji-agentv1.0/file.txt").write_text(
            "tampered\n", encoding="utf-8"
        )
        result = self.verify_tree()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("file.txt", result.stderr)

        shutil.rmtree(self.extracted)
        self.extracted.mkdir()
        with tarfile.open(self.archive, "r:gz") as bundle:
            bundle.extractall(self.extracted)
        (self.extracted / "taiji-agentv1.0/bin/run.sh").chmod(0o644)
        result = self.verify_tree()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run.sh", result.stderr)

    def test_rejects_unapproved_extra_but_accepts_explicit_generated_prefix(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        generated = self.extracted / "taiji-agentv1.0/runtime/package-build/output.bin"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"generated")

        rejected = self.verify_tree()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unexpected", rejected.stderr.lower())

        accepted = self.verify_tree("--allow-extra-prefix", "runtime/package-build")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_rejects_a_same_content_hardlinked_source_member(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        member = self.extracted / "taiji-agentv1.0/file.txt"
        replacement = self.root / "same-content-hardlink"
        member.unlink()
        os.link(self.source / "file.txt", member)
        os.link(member, replacement)

        result = self.verify_tree()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("single-link", result.stderr.lower())

    def test_rejects_forged_inventory_and_duplicate_archive_members(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        payload["members"][0]["mode"] = 0o777
        self.inventory.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = self.verify_tree()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inventory", result.stderr.lower())

        duplicate = self.root / ("taiji-agentv1.0-kylin-build-src-" + "b" * 40 + ".tar.gz")
        with tarfile.open(duplicate, "w:gz") as bundle:
            info = tarfile.TarInfo("taiji-agentv1.0/file.txt")
            info.size = 1
            bundle.addfile(info, __import__("io").BytesIO(b"a"))
            info = tarfile.TarInfo("taiji-agentv1.0/file.txt")
            info.size = 1
            bundle.addfile(info, __import__("io").BytesIO(b"b"))
        result = self.run_helper(
            "create",
            "--archive", str(duplicate),
            "--inventory", str(self.root / "duplicate.json"),
            "--source-commit", "b" * 40,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr.lower())

    def test_archive_hash_members_and_inventory_json_share_single_safe_file_reads(self) -> None:
        source = HELPER.read_text(encoding="utf-8")

        self.assertNotIn('tarfile.open(archive, "r:gz")', source)
        self.assertIn('tarfile.open(fileobj=archive_handle, mode="r:gz")', source)
        self.assertNotIn('path.read_text(encoding="utf-8")', source)
        self.assertNotIn("os.rename(temporary_name, str(inventory))", source)
        self.assertIn("os.link(temporary_name, str(inventory), follow_symlinks=False)", source)

    def test_rejects_path_replacement_after_open_inode_was_hashed(self) -> None:
        helper = load_helper()
        member = self.extracted / "taiji-agentv1.0/file.txt"
        replacement = self.root / "replacement.txt"
        replacement.write_text("malicious\n", encoding="utf-8")
        original_fstat = os.fstat
        replaced = False

        def replace_path_after_hash(descriptor):
            nonlocal replaced
            metadata = original_fstat(descriptor)
            if not replaced:
                replacement.replace(member)
                replaced = True
            return metadata

        with mock.patch.object(helper.os, "fstat", side_effect=replace_path_after_hash):
            with self.assertRaises(helper.SourceIntegrityError):
                helper._tree_file_record(member, "file.txt")


if __name__ == "__main__":
    unittest.main()
