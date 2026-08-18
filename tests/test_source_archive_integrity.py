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

    def test_verify_accepts_inherited_descriptors_with_a_canonical_archive_name(self) -> None:
        created = self.create_inventory()
        self.assertEqual(created.returncode, 0, created.stderr)
        archive_fd = os.open(str(self.archive), os.O_RDONLY)
        inventory_fd = os.open(str(self.inventory), os.O_RDONLY)
        try:
            verified = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(HELPER),
                    "verify",
                    "--archive-fd",
                    str(archive_fd),
                    "--archive-basename",
                    self.archive.name,
                    "--inventory-fd",
                    str(inventory_fd),
                ],
                pass_fds=(archive_fd, inventory_fd),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        finally:
            os.close(archive_fd)
            os.close(inventory_fd)

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

    def test_exact_extra_symlink_contract_accepts_only_the_bound_path_and_target(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        relative = "hermes-local-lab/sources/hermes-agent/venv/bin/python"
        target = "/opt/taiji-python/bin/python3.11"
        generated = self.extracted / "taiji-agentv1.0" / relative
        generated.parent.mkdir(parents=True)
        os.symlink(target, generated)

        missing_contract = self.verify_tree(
            "--allow-extra-prefix", "hermes-local-lab/sources/hermes-agent/venv"
        )
        self.assertNotEqual(missing_contract.returncode, 0)
        self.assertIn("unsafe symlink target", missing_contract.stderr.lower())

        wrong_target = self.verify_tree(
            "--allow-extra-prefix", "hermes-local-lab/sources/hermes-agent/venv",
            "--allow-extra-symlink", relative, "/opt/other-python/bin/python3.11",
        )
        self.assertNotEqual(wrong_target.returncode, 0)
        self.assertIn("unsafe symlink target", wrong_target.stderr.lower())

        accepted = self.verify_tree(
            "--allow-extra-prefix", "hermes-local-lab/sources/hermes-agent/venv",
            "--allow-extra-symlink", relative, target,
        )

        self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_unlisted_allowed_extra_absolute_and_escaping_symlinks_stay_strict(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        generated = self.extracted / "taiji-agentv1.0/runtime/package-build"
        generated.mkdir(parents=True)
        absolute = generated / "absolute-link"
        os.symlink("/etc/passwd", absolute)

        rejected_absolute = self.verify_tree(
            "--allow-extra-prefix", "runtime/package-build"
        )
        self.assertNotEqual(rejected_absolute.returncode, 0)
        self.assertIn("unsafe symlink target", rejected_absolute.stderr.lower())

        absolute.unlink()
        os.symlink("../../../../outside-source", generated / "escaping-link")
        rejected_escape = self.verify_tree(
            "--allow-extra-prefix", "runtime/package-build"
        )
        self.assertNotEqual(rejected_escape.returncode, 0)
        self.assertIn("escapes the source root", rejected_escape.stderr.lower())

    def test_declared_symlink_stays_strict_even_if_its_path_is_allowed(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        declared = self.extracted / "taiji-agentv1.0/link.txt"
        declared.unlink()
        os.symlink("/etc/passwd", declared)

        rejected = self.verify_tree(
            "--allow-extra-prefix", "link.txt",
            "--allow-extra-symlink", "link.txt", "/etc/passwd",
        )

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unsafe symlink target", rejected.stderr.lower())

    def test_allowed_prefix_ancestor_and_nonallowed_symlinks_stay_strict(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)
        source_root = self.extracted / "taiji-agentv1.0"

        os.symlink("/opt/runtime", source_root / "runtime")
        ancestor = self.verify_tree("--allow-extra-prefix", "runtime/package-build")
        self.assertNotEqual(ancestor.returncode, 0)
        self.assertIn("unsafe symlink target", ancestor.stderr.lower())

        (source_root / "runtime").unlink()
        os.symlink("/opt/unapproved", source_root / "unapproved")
        nonallowed = self.verify_tree("--allow-extra-prefix", "runtime/package-build")
        self.assertNotEqual(nonallowed.returncode, 0)
        self.assertIn("unsafe symlink target", nonallowed.stderr.lower())

    def test_extra_symlink_contract_path_must_be_within_an_allowed_prefix(self) -> None:
        self.assertEqual(self.create_inventory().returncode, 0)

        rejected = self.verify_tree(
            "--allow-extra-prefix", "runtime/package-build",
            "--allow-extra-symlink", "unapproved/python", "/opt/python/bin/python",
        )

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("allow-extra-symlink path", rejected.stderr.lower())

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
