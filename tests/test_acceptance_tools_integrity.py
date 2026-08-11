import hashlib
import importlib.util
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging/linux/acceptance_tools_manifest.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("acceptance_tools_manifest", HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AcceptanceToolsIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_helper()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.staged = self.root / "delivery" / "验收工具"
        self.launcher = self.staged.parent / "04_目标终端_桌面App验收并导出证据.sh"
        self.manifest = self.staged / "acceptance-tools-manifest.json"
        self.source_commit = "a" * 40
        for entry in self.module.CANONICAL_FILES:
            source = self.repo / entry["source_path"]
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes((entry["delivery_path"] + "\n").encode("utf-8"))
            source.chmod(entry.get("source_mode", entry["mode"]))
        launcher_source = self.repo / self.module.CANONICAL_LAUNCHER["source_path"]
        launcher_source.parent.mkdir(parents=True, exist_ok=True)
        launcher_source.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        launcher_source.chmod(self.module.CANONICAL_LAUNCHER["mode"])

    def tearDown(self):
        self.temporary.cleanup()

    def create_and_stage(self):
        payload = self.module.create_manifest(self.repo, self.source_commit)
        self.staged.mkdir(parents=True)
        for directory in self.module.CANONICAL_DIRECTORIES:
            target = self.staged / directory
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
        for entry in self.module.CANONICAL_FILES:
            source = self.repo / entry["source_path"]
            target = self.staged / entry["delivery_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            target.chmod(entry["mode"])
        self.launcher.write_bytes(
            (self.repo / self.module.CANONICAL_LAUNCHER["source_path"]).read_bytes()
        )
        self.launcher.chmod(self.module.CANONICAL_LAUNCHER["mode"])
        self.module.write_manifest_exclusive(self.manifest, payload)
        return payload

    def verify_staged(self, expected_source_commit=None, expected_manifest_sha256=None):
        if expected_source_commit is None:
            expected_source_commit = self.source_commit
        if expected_manifest_sha256 is None:
            expected_manifest_sha256 = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        return self.module.verify_staged(
            self.staged,
            expected_source_commit,
            expected_manifest_sha256,
            os.getuid(),
        )

    def test_manifest_is_deterministic_exact_and_binds_launcher_and_every_tool(self):
        first = self.module.create_manifest(self.repo, self.source_commit)
        second = self.module.create_manifest(self.repo, self.source_commit)
        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {"schema", "source_commit", "launcher", "directories", "files"},
        )
        self.assertEqual(first["schema"], "taiji-acceptance-tools-manifest/v1")
        self.assertEqual(first["source_commit"], self.source_commit)
        self.assertEqual(first["directories"], [])
        self.assertEqual(
            {entry["delivery_path"] for entry in first["files"]},
            {
                "run-installed-electron-acceptance.js",
                "assemble-target-evidence.py",
                "observe-single-deb-install.py",
                "certification-matrix.json",
                "validate-taiji-release-evidence.py",
                "signing-public.pem",
            },
        )
        self.assertEqual(
            [entry["delivery_path"] for entry in first["files"]],
            sorted(entry["delivery_path"] for entry in self.module.CANONICAL_FILES),
        )
        self.assertEqual(
            first["launcher"]["sha256"],
            hashlib.sha256(
                (self.repo / self.module.CANONICAL_LAUNCHER["source_path"]).read_bytes()
            ).hexdigest(),
        )

    def test_real_repository_sources_match_the_canonical_source_contract(self):
        payload = self.module.create_manifest(ROOT, self.source_commit)
        self.module.verify_source(ROOT, payload, self.source_commit)

    def test_source_verification_rejects_changed_symlink_or_hardlinked_inputs(self):
        payload = self.module.create_manifest(self.repo, self.source_commit)
        target = self.repo / self.module.CANONICAL_FILES[0]["source_path"]
        original = target.read_bytes()
        target.write_bytes(original + b"tampered")
        with self.assertRaisesRegex(self.module.ManifestError, "digest|changed"):
            self.module.verify_source(self.repo, payload, self.source_commit)
        target.write_bytes(original)
        target.unlink()
        target.symlink_to(self.repo / self.module.CANONICAL_FILES[1]["source_path"])
        with self.assertRaisesRegex(self.module.ManifestError, "regular|symlink|safely"):
            self.module.verify_source(self.repo, payload, self.source_commit)

    def test_staged_verification_rejects_unknown_missing_modified_or_linked_files(self):
        payload = self.create_and_stage()
        self.verify_staged()
        unknown = self.staged / "unknown.txt"
        unknown.write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(self.module.ManifestError, "unknown|closure"):
            self.verify_staged()
        unknown.unlink()
        changed = self.staged / payload["files"][0]["delivery_path"]
        changed.write_bytes(changed.read_bytes() + b"x")
        with self.assertRaisesRegex(self.module.ManifestError, "digest|changed"):
            self.verify_staged()

    def test_staged_verification_rejects_writable_symlink_and_hardlink_nodes(self):
        payload = self.create_and_stage()
        target = self.staged / payload["files"][0]["delivery_path"]
        target.chmod(0o666)
        with self.assertRaisesRegex(self.module.ManifestError, "mode|writable"):
            self.verify_staged()
        target.chmod(payload["files"][0]["mode"])
        original = target.read_bytes()
        target.unlink()
        target.symlink_to(self.staged / payload["files"][1]["delivery_path"])
        with self.assertRaisesRegex(self.module.ManifestError, "regular|symlink"):
            self.verify_staged()
        target.unlink()
        target.write_bytes(original)
        target.chmod(payload["files"][0]["mode"])
        hardlink = self.staged / "hardlink-copy"
        os.link(target, hardlink)
        with self.assertRaisesRegex(self.module.ManifestError, "hard link|closure"):
            self.verify_staged()

    def test_staged_verification_binds_external_commit_manifest_and_fixed_layout(self):
        self.create_and_stage()
        with self.assertRaisesRegex(self.module.ManifestError, "commit"):
            self.verify_staged(expected_source_commit="b" * 40)

        canonical_raw = self.manifest.read_bytes()
        expected_sha = hashlib.sha256(canonical_raw).hexdigest()
        external = self.root / "external-manifest.json"
        external.write_bytes(canonical_raw)
        external.chmod(0o644)
        self.manifest.write_text("not-json\n", encoding="utf-8")
        with self.assertRaisesRegex(self.module.ManifestError, "manifest|digest|JSON"):
            self.verify_staged(expected_manifest_sha256=expected_sha)

        self.manifest.write_bytes(canonical_raw)
        self.launcher.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.launcher.chmod(0o755)
        rogue = self.root / "rogue" / self.launcher.name
        rogue.parent.mkdir()
        rogue.write_bytes(
            (self.repo / self.module.CANONICAL_LAUNCHER["source_path"]).read_bytes()
        )
        rogue.chmod(0o755)
        with self.assertRaisesRegex(self.module.ManifestError, "launcher|digest"):
            self.verify_staged()

    def test_read_rejects_same_size_in_place_mutation_after_a_chunk_was_read(self):
        target = self.repo / self.module.CANONICAL_FILES[0]["source_path"]
        target.write_bytes(b"A" * (2 * 1024 * 1024))
        target.chmod(self.module.CANONICAL_FILES[0].get("source_mode", 0o644))
        real_read = self.module.os.read
        mutated = False

        def racing_read(descriptor, amount):
            nonlocal mutated
            chunk = real_read(descriptor, amount)
            if (
                chunk
                and not mutated
                and os.fstat(descriptor).st_ino == target.stat().st_ino
            ):
                mutated = True
                with target.open("r+b") as stream:
                    stream.write(b"B" * len(chunk))
                    stream.flush()
                    os.fsync(stream.fileno())
            return chunk

        with mock.patch.object(self.module.os, "read", side_effect=racing_read):
            with self.assertRaisesRegex(self.module.ManifestError, "changed"):
                self.module.create_manifest(self.repo, self.source_commit)

    def test_source_and_staged_directory_chains_reject_symlink_ancestors(self):
        real_tools = self.root / "real-source-tools"
        (self.repo / "tools").rename(real_tools)
        (self.repo / "tools").symlink_to(real_tools, target_is_directory=True)
        with self.assertRaisesRegex(self.module.ManifestError, "directory|symlink|path"):
            self.module.create_manifest(self.repo, self.source_commit)

        (self.repo / "tools").unlink()
        real_tools.rename(self.repo / "tools")
        self.create_and_stage()
        real_delivery = self.root / "real-delivery"
        self.staged.parent.rename(real_delivery)
        self.staged.parent.symlink_to(real_delivery, target_is_directory=True)
        with self.assertRaisesRegex(self.module.ManifestError, "directory|symlink|path"):
            self.verify_staged()

    def test_staged_verification_rechecks_closure_after_digest_reads(self):
        self.create_and_stage()
        real_read = self.module._read_regular_file
        injected = False

        def racing_read(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                (self.staged / "late-unknown.txt").write_text("late", encoding="utf-8")
            return real_read(*args, **kwargs)

        with mock.patch.object(self.module, "_read_regular_file", side_effect=racing_read):
            with self.assertRaisesRegex(self.module.ManifestError, "unknown|closure|changed"):
                self.verify_staged()

    def test_staged_verification_rechecks_the_sibling_launcher_after_tool_reads(self):
        self.create_and_stage()
        real_read = self.module._read_regular_file
        mutated = False

        def racing_read(*args, **kwargs):
            nonlocal mutated
            payload = real_read(*args, **kwargs)
            label = args[3] if len(args) > 3 else kwargs.get("label")
            if label == "acceptance launcher" and not mutated:
                mutated = True
                self.launcher.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
                self.launcher.chmod(0o755)
            return payload

        with mock.patch.object(self.module, "_read_regular_file", side_effect=racing_read):
            with self.assertRaisesRegex(self.module.ManifestError, "launcher|changed|digest"):
                self.verify_staged()

    def test_manifest_creation_rejects_repo_root_replacement_during_source_reads(self):
        alternate = self.root / "alternate-repo"
        shutil.copytree(self.repo, alternate)
        alternate_launcher = alternate / self.module.CANONICAL_LAUNCHER["source_path"]
        alternate_launcher.write_text("#!/bin/sh\nexit 44\n", encoding="utf-8")
        alternate_launcher.chmod(self.module.CANONICAL_LAUNCHER["mode"])
        original_read = self.module._read_regular_file_at
        swapped = False

        def racing_read(*args, **kwargs):
            nonlocal swapped
            payload = original_read(*args, **kwargs)
            label = args[4] if len(args) > 4 else kwargs.get("label")
            if label == "acceptance launcher source" and not swapped:
                swapped = True
                self.repo.rename(self.root / "original-repo")
                alternate.rename(self.repo)
            return payload

        with mock.patch.object(self.module, "_read_regular_file_at", side_effect=racing_read):
            with self.assertRaisesRegex(self.module.ManifestError, "root|changed|identity"):
                self.module.create_manifest(self.repo, self.source_commit)

    def test_manifest_creation_rejects_source_mutation_after_a_safe_read_returns(self):
        launcher = self.repo / self.module.CANONICAL_LAUNCHER["source_path"]
        real_read = self.module._read_regular_file_at
        mutated = False

        def racing_read(*args, **kwargs):
            nonlocal mutated
            payload = real_read(*args, **kwargs)
            label = args[4] if len(args) > 4 else kwargs.get("label")
            if label == "acceptance launcher source" and not mutated:
                mutated = True
                launcher.write_text("#!/bin/sh\nexit 45\n", encoding="utf-8")
                launcher.chmod(self.module.CANONICAL_LAUNCHER["mode"])
            return payload

        with mock.patch.object(self.module, "_read_regular_file_at", side_effect=racing_read):
            with self.assertRaisesRegex(self.module.ManifestError, "source|changed|identity"):
                self.module.create_manifest(self.repo, self.source_commit)

    def test_manifest_creation_rejects_nested_source_directory_replacement(self):
        source_directory = self.repo / "tools/taiji-desktop-acceptance"
        alternate = self.root / "alternate-desktop-tools"
        shutil.copytree(source_directory, alternate)
        changed = alternate / "run-installed-electron-acceptance.js"
        changed.write_text("throw new Error('alternate');\n", encoding="utf-8")
        changed.chmod(0o644)
        real_read = self.module._read_regular_file_at
        swapped = False

        def racing_read(*args, **kwargs):
            nonlocal swapped
            payload = real_read(*args, **kwargs)
            label = args[4] if len(args) > 4 else kwargs.get("label")
            if label == "acceptance launcher source" and not swapped:
                swapped = True
                source_directory.rename(self.root / "original-desktop-tools")
                alternate.rename(source_directory)
            return payload

        with mock.patch.object(self.module, "_read_regular_file_at", side_effect=racing_read):
            with self.assertRaisesRegex(self.module.ManifestError, "source|changed|identity"):
                self.module.create_manifest(self.repo, self.source_commit)

    def test_directory_chain_allows_a_trusted_sticky_ancestor_but_not_a_writable_leaf(self):
        sticky = self.root / "sticky"
        leaf = sticky / "owner-root"
        sticky.mkdir()
        sticky.chmod(0o1777)
        leaf.mkdir()
        leaf.chmod(0o700)
        descriptor = self.module._open_directory_chain(leaf, os.getuid(), "sticky fixture")
        os.close(descriptor)
        leaf.chmod(0o777)
        with self.assertRaisesRegex(self.module.ManifestError, "trusted"):
            self.module._open_directory_chain(leaf, os.getuid(), "writable leaf")

    def test_canonical_staged_file_with_external_hardlink_is_rejected(self):
        payload = self.create_and_stage()
        canonical = self.staged / payload["files"][0]["delivery_path"]
        os.link(canonical, self.root / "external-hardlink")
        with self.assertRaisesRegex(self.module.ManifestError, "hard link"):
            self.verify_staged()

    def test_strict_loader_rejects_duplicate_unknown_and_noncanonical_manifest(self):
        with self.assertRaisesRegex(self.module.ManifestError, "duplicate"):
            self.module.parse_manifest_bytes(b'{"schema":"x","schema":"y"}\n')
        payload = self.module.create_manifest(self.repo, self.source_commit)
        payload["unknown"] = True
        with self.assertRaisesRegex(self.module.ManifestError, "field"):
            self.module.validate_manifest(payload, self.source_commit)
        payload.pop("unknown")
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        with self.assertRaisesRegex(self.module.ManifestError, "canonical"):
            self.module.parse_manifest_bytes(raw)

    def test_exclusive_writer_never_overwrites_or_follows_existing_manifest(self):
        payload = self.module.create_manifest(self.repo, self.source_commit)
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(self.module.ManifestError, "exists|publish"):
            self.module.write_manifest_exclusive(self.manifest, payload)

    def test_exclusive_writer_rejects_temporary_path_replacement_before_publish(self):
        payload = self.module.create_manifest(self.repo, self.source_commit)
        self.manifest.parent.mkdir(parents=True)
        real_link = self.module.os.link
        replaced = False

        def racing_link(*args, **kwargs):
            nonlocal replaced
            if not replaced:
                replaced = True
                temporary = next(self.manifest.parent.glob(".acceptance-tools-manifest.json.tmp-*"))
                temporary.unlink()
                temporary.write_bytes(b"ATTACKER-CONTROLLED\n")
                temporary.chmod(0o644)
            return real_link(*args, **kwargs)

        with mock.patch.object(self.module.os, "link", side_effect=racing_link):
            with self.assertRaisesRegex(self.module.ManifestError, "changed|publish|identity"):
                self.module.write_manifest_exclusive(self.manifest, payload)
        self.assertFalse(self.manifest.exists(), "failed publication must not leave a manifest")

    def test_cli_verify_source_rejects_a_symlink_manifest(self):
        payload = self.module.create_manifest(self.repo, self.source_commit)
        real_manifest = self.root / "real-manifest.json"
        self.module.write_manifest_exclusive(real_manifest, payload)
        manifest_link = self.root / "manifest-link.json"
        manifest_link.symlink_to(real_manifest)
        with self.assertRaisesRegex(self.module.ManifestError, "regular|symlink|safely"):
            self.module._main(
                [
                    "verify-source",
                    "--repo-root",
                    str(self.repo),
                    "--source-commit",
                    self.source_commit,
                    "--manifest",
                    str(manifest_link),
                ]
            )


if __name__ == "__main__":
    unittest.main()
