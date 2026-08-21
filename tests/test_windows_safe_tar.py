"""RED/GREEN contract tests for the Windows safe tar extractor."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import hashlib
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "packaging/windows/safe_tar.py"


def run_checkout_bound_help(path: Path):
    with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-help-") as temporary:
        return subprocess.run(
            ["/usr/bin/python3", "-I", "-B", str(path.resolve()), "--help"],
            cwd=temporary,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )


def load_helper():
    spec = importlib.util.spec_from_file_location("taiji_windows_safe_tar_test", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load safe_tar helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def builder_manifest_bytes(archive: Path, **overrides: object) -> bytes:
    payload = {
        "schema": "taiji-windows-builder-input/v1",
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "version": "1.2.3",
        "source_branch": "main",
        "archive_basename": archive.name,
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "target_config_sha256": "c" * 64,
        "asset_provenance_sha256": "d" * 64,
        "created_at": "2026-08-21T12:00:00Z",
    }
    payload.update(overrides)
    return canonical_json_bytes(payload) + b"\n"


def build_tar(path: Path, members: list[tuple[str, bytes | None, int, str]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload, mode, kind in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
            info.uid = 123
            info.gid = 456
            info.mtime = 111
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif kind == "file":
                payload = payload or b""
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "target"
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = "target"
                archive.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                archive.addfile(info)
            elif kind == "char":
                info.type = tarfile.CHRTYPE
                info.devmajor = 1
                info.devminor = 3
                archive.addfile(info)
            elif kind == "block":
                info.type = tarfile.BLKTYPE
                info.devmajor = 8
                info.devminor = 0
                archive.addfile(info)
            else:
                raise AssertionError("unknown tar kind: {}".format(kind))
    os.chmod(path, 0o600)


def write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    os.chmod(path, 0o600)


def make_run_tree(root: Path) -> Path:
    run_root = root / ("a" * 40) / "run-20260821T120000Z"
    run_root.mkdir(parents=True, exist_ok=True)
    os.chmod(run_root, 0o700)
    source_dir = run_root / "source"
    source_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(source_dir, 0o700)
    return source_dir


class WindowsSafeTarTests(unittest.TestCase):
    def assert_helper_help_and_api(self):
        self.assertTrue(HELPER_PATH.is_file(), "missing helper: {}".format(HELPER_PATH))
        result = run_checkout_bound_help(HELPER_PATH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())
        module = load_helper()
        self.assertTrue(callable(getattr(module, "extract_tar", None)), "extract_tar is missing")
        self.assertTrue(callable(getattr(module, "main", None)), "main is missing")
        return module

    def test_checkout_bound_help_and_public_callables_exist(self):
        self.assert_helper_help_and_api()

    def test_valid_extract_uses_run_source_subdir_and_does_not_inherit_mode(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-valid-") as temporary:
            root = Path(temporary)
            archive = root / "input.tar.gz"
            build_tar(
                archive,
                [
                    ("bundle", None, 0o777, "dir"),
                    ("bundle/目录", None, 0o777, "dir"),
                    ("bundle/目录/file.txt", b"payload", 0o777, "file"),
                ],
            )
            manifest = root / "manifest.json"
            write_private(manifest, builder_manifest_bytes(archive))
            target = make_run_tree(root) / "checkout"
            extracted = helper.extract_tar(archive, target, manifest)
            self.assertEqual(extracted, target.resolve())
            file_path = target / "bundle/目录/file.txt"
            self.assertEqual(file_path.read_bytes(), b"payload")
            self.assertNotEqual(stat.S_IMODE(file_path.stat().st_mode), 0o777)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(file_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((target / "bundle").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((target / "bundle/目录").stat().st_mode), 0o700)

    def test_target_must_be_missing_and_inside_run_source(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-target-") as temporary:
            root = Path(temporary)
            archive = root / "input.tar.gz"
            build_tar(archive, [("bundle/file.txt", b"x", 0o644, "file")])
            manifest = root / "manifest.json"
            write_private(manifest, builder_manifest_bytes(archive))
            bad = root / ("a" * 40) / "elsewhere" / "checkout"
            bad.parent.mkdir(parents=True)
            with self.assertRaises(helper.SafeTarError) as raised:
                helper.extract_tar(archive, bad, manifest)
            self.assertEqual(raised.exception.category, "SAFE_TAR_TARGET_INVALID")
            target = make_run_tree(root) / "checkout"
            target.mkdir()
            with self.assertRaises(helper.SafeTarError) as raised:
                helper.extract_tar(archive, target, manifest)
            self.assertEqual(raised.exception.category, "SAFE_TAR_TARGET_INVALID")

    def test_target_accepts_real_run_id_shape_and_rejects_outside_source_or_deeper_paths(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-run-shape-") as temporary:
            root = Path(temporary)
            archive = root / "input.tar.gz"
            build_tar(archive, [("bundle/file.txt", b"x", 0o644, "file")])
            manifest = root / "manifest.json"
            write_private(manifest, builder_manifest_bytes(archive))
            source_dir = make_run_tree(root)

            valid = source_dir / "checkout"
            extracted = helper.extract_tar(archive, valid, manifest)
            self.assertEqual(extracted, valid.resolve())

            outside = root / ("a" * 40) / "run-20260821T120000Z" / "other" / "checkout"
            outside.parent.mkdir(parents=True)
            with self.assertRaises(helper.SafeTarError) as raised:
                helper.extract_tar(archive, outside, manifest)
            self.assertEqual(raised.exception.category, "SAFE_TAR_TARGET_INVALID")

            deeper = source_dir / "nested" / "checkout"
            deeper.parent.mkdir(parents=True)
            with self.assertRaises(helper.SafeTarError) as raised:
                helper.extract_tar(archive, deeper, manifest)
            self.assertEqual(raised.exception.category, "SAFE_TAR_TARGET_INVALID")

    def test_rejects_path_traversal_casefold_collisions_and_parent_conflicts_before_create(self):
        helper = self.assert_helper_help_and_api()
        cases = [
            ("absolute", [("/abs.txt", b"x", 0o644, "file")]),
            ("dotdot", [("../escape.txt", b"x", 0o644, "file")]),
            ("backslash", [("dir\\evil.txt", b"x", 0o644, "file")]),
            ("colon", [("dir:evil.txt", b"x", 0o644, "file")]),
            ("drive", [("C:/evil.txt", b"x", 0o644, "file")]),
            ("unc", [("//server/share.txt", b"x", 0o644, "file")]),
            ("reserved", [("CON.txt", b"x", 0o644, "file")]),
            ("trailing-dot", [("bad./x", b"x", 0o644, "file")]),
            ("trailing-space", [("bad /x", b"x", 0o644, "file")]),
            ("casefold", [("Dir/File.txt", b"x", 0o644, "file"), ("dir/file.TXT", b"y", 0o644, "file")]),
            ("parent-conflict", [("bundle", b"x", 0o644, "file"), ("bundle/file.txt", b"y", 0o644, "file")]),
            ("child-first-parent-file", [("bundle/file.txt", b"y", 0o644, "file"), ("bundle", b"x", 0o644, "file")]),
        ]
        for label, members in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="taiji-safe-tar-case-") as temporary:
                root = Path(temporary)
                archive = root / "input.tar.gz"
                build_tar(archive, members)
                manifest = root / "manifest.json"
                write_private(manifest, builder_manifest_bytes(archive))
                target = make_run_tree(root) / "checkout"
                with self.assertRaises(helper.SafeTarError) as raised:
                    helper.extract_tar(archive, target, manifest)
                self.assertEqual(raised.exception.category, "SAFE_TAR_MEMBER_INVALID")
                self.assertFalse(target.exists())

    def test_rejects_symlink_hardlink_and_fifo_members(self):
        helper = self.assert_helper_help_and_api()
        cases = {
            "symlink": [("bundle/link", None, 0o777, "symlink")],
            "hardlink": [("bundle/link", None, 0o777, "hardlink")],
            "fifo": [("bundle/fifo", None, 0o777, "fifo")],
            "char": [("bundle/char", None, 0o777, "char")],
            "block": [("bundle/block", None, 0o777, "block")],
        }
        for label, members in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="taiji-safe-tar-kind-") as temporary:
                root = Path(temporary)
                archive = root / "input.tar.gz"
                build_tar(archive, members)
                manifest = root / "manifest.json"
                write_private(manifest, builder_manifest_bytes(archive))
                target = make_run_tree(root) / "checkout"
                with self.assertRaises(helper.SafeTarError) as raised:
                    helper.extract_tar(archive, target, manifest)
                self.assertEqual(raised.exception.category, "SAFE_TAR_MEMBER_INVALID")
                self.assertFalse(target.exists())

    def test_rejects_nul_member_name_directly_when_tar_api_cannot_represent_it(self):
        helper = self.assert_helper_help_and_api()
        with self.assertRaises(helper.SafeTarError) as raised:
            helper._normalized_parts("bundle/\x00bad.txt")
        self.assertEqual(raised.exception.category, "SAFE_TAR_MEMBER_INVALID")

    def test_inputs_and_run_directories_must_be_private_owned_regular_single_links(self):
        helper = self.assert_helper_help_and_api()

        def exercise(label, mutate, expected_category):
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="taiji-safe-tar-private-") as temporary:
                root = Path(temporary)
                archive = root / "input.tar.gz"
                build_tar(archive, [("bundle/file.txt", b"payload", 0o644, "file")])
                manifest = root / "manifest.json"
                write_private(manifest, builder_manifest_bytes(archive))
                target = make_run_tree(root) / "checkout"
                context = mutate(archive, manifest, target)
                if context is None:
                    context = contextlib.nullcontext()
                with context:
                    with self.assertRaises(helper.SafeTarError) as raised:
                        helper.extract_tar(archive, target, manifest)
                self.assertEqual(raised.exception.category, expected_category)
                self.assertFalse(target.exists())

        def archive_symlink(archive, _manifest, _target):
            original = archive.with_suffix(".original")
            archive.rename(original)
            os.symlink(original, archive)

        def archive_hardlink(archive, _manifest, _target):
            os.link(archive, archive.with_suffix(".second-link"))

        def archive_mode(archive, _manifest, _target):
            os.chmod(archive, 0o644)

        def manifest_symlink(_archive, manifest, _target):
            original = manifest.with_suffix(".original")
            manifest.rename(original)
            os.symlink(original, manifest)

        def manifest_hardlink(_archive, manifest, _target):
            os.link(manifest, manifest.with_suffix(".second-link"))

        def manifest_mode(_archive, manifest, _target):
            os.chmod(manifest, 0o644)

        def source_mode(_archive, _manifest, target):
            os.chmod(target.parent, 0o755)

        def run_mode(_archive, _manifest, target):
            os.chmod(target.parent.parent, 0o755)

        def wrong_owner(_archive, _manifest, _target):
            current = helper._current_uid()
            if current is None:
                self.skipTest("owner identity is unavailable on this platform")
            return mock.patch.object(
                helper,
                "_current_uid",
                side_effect=(current, current, current + 1),
            )

        cases = (
            ("archive-symlink", archive_symlink, "SAFE_TAR_ARCHIVE_INVALID"),
            ("archive-hardlink", archive_hardlink, "SAFE_TAR_ARCHIVE_INVALID"),
            ("archive-mode", archive_mode, "SAFE_TAR_ARCHIVE_INVALID"),
            ("manifest-symlink", manifest_symlink, "SAFE_TAR_MANIFEST_INVALID"),
            ("manifest-hardlink", manifest_hardlink, "SAFE_TAR_MANIFEST_INVALID"),
            ("manifest-mode", manifest_mode, "SAFE_TAR_MANIFEST_INVALID"),
            ("source-mode", source_mode, "SAFE_TAR_TARGET_INVALID"),
            ("run-mode", run_mode, "SAFE_TAR_TARGET_INVALID"),
            ("owner", wrong_owner, "SAFE_TAR_ARCHIVE_INVALID"),
        )
        for label, mutate, category in cases:
            exercise(label, mutate, category)

    def test_archive_path_swap_after_validation_cannot_change_extracted_bytes(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-snapshot-") as temporary:
            root = Path(temporary)
            archive = root / "input.tar.gz"
            replacement = root / "replacement.tar.gz"
            build_tar(archive, [("bundle/file.txt", b"ORIGINAL", 0o644, "file")])
            build_tar(replacement, [("bundle/file.txt", b"EVIL!!!!", 0o644, "file")])
            manifest = root / "manifest.json"
            write_private(manifest, builder_manifest_bytes(archive))
            target = make_run_tree(root) / "checkout"
            real_collect = helper._collect_members

            def swap_path_after_member_validation(open_archive):
                members = real_collect(open_archive)
                archive.unlink()
                replacement.rename(archive)
                return members

            with mock.patch.object(helper, "_collect_members", side_effect=swap_path_after_member_validation):
                helper.extract_tar(archive, target, manifest)
            self.assertEqual((target / "bundle/file.txt").read_bytes(), b"ORIGINAL")

    def test_cli_io_failures_are_categorized_without_traceback(self):
        self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-cli-io-") as temporary:
            root = Path(temporary)
            archive = root / "input.tar.gz"
            build_tar(archive, [("bundle/file.txt", b"payload", 0o644, "file")])
            manifest = root / "manifest.json"
            write_private(manifest, builder_manifest_bytes(archive))
            corrupt_archive = root / "corrupt.tar.gz"
            write_private(corrupt_archive, b"this is not a tar archive")
            corrupt_manifest = root / "corrupt-manifest.json"
            write_private(corrupt_manifest, builder_manifest_bytes(corrupt_archive))

            cases = (
                (archive, root / "missing-manifest.json", "SAFE_TAR_MANIFEST_INVALID"),
                (root / "missing-archive.tar.gz", manifest, "SAFE_TAR_ARCHIVE_INVALID"),
                (corrupt_archive, corrupt_manifest, "SAFE_TAR_ARCHIVE_INVALID"),
            )
            for index, (archive_arg, manifest_arg, category) in enumerate(cases):
                destination = make_run_tree(root / str(index)) / "checkout"
                with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-cli-io-cwd-") as cwd:
                    result = subprocess.run(
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-B",
                            str(HELPER_PATH.resolve()),
                            "extract",
                            "--archive",
                            str(archive_arg),
                            "--destination",
                            str(destination),
                            "--manifest",
                            str(manifest_arg),
                        ],
                        cwd=cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(category, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(destination.exists())

    def test_cli_extract_requires_manifest_and_validates_before_creating_destination(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-cli-") as temporary:
            root = Path(temporary)
            archive = root / "input.tar.gz"
            build_tar(archive, [("bundle/file.txt", b"x", 0o644, "file")])
            manifest = root / "manifest.json"
            write_private(manifest, builder_manifest_bytes(archive))
            destination = make_run_tree(root) / "checkout"
            with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-cwd-") as cwd:
                result = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        str(HELPER_PATH.resolve()),
                        "extract",
                        "--archive",
                        str(archive),
                        "--destination",
                        str(destination),
                        "--manifest",
                        str(manifest),
                    ],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destination.resolve(), Path(result.stdout.strip()))

            bad_destination = make_run_tree(root) / "bad"
            bad_manifest = root / "bad-manifest.json"
            bad_manifest.write_text(
                json.dumps(
                    {
                        "schema": "taiji-windows-builder-input/v1",
                        "source_commit": "a" * 40,
                        "source_tree": "b" * 40,
                        "version": "1.2.3",
                        "source_branch": "main",
                        "archive_basename": archive.name,
                        "archive_bytes": archive.stat().st_size,
                        "archive_sha256": "0" * 64,
                        "target_config_sha256": "c" * 64,
                        "asset_provenance_sha256": "d" * 64,
                        "created_at": "2026-08-21T12:00:00Z",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(bad_manifest, 0o600)
            with tempfile.TemporaryDirectory(prefix="taiji-safe-tar-cwd-bad-") as cwd:
                result = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        str(HELPER_PATH.resolve()),
                        "extract",
                        "--archive",
                        str(archive),
                        "--destination",
                        str(bad_destination),
                        "--manifest",
                        str(bad_manifest),
                    ],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
            self.assertEqual(result.returncode, 2)
            self.assertIn("SAFE_TAR_MANIFEST_INVALID", result.stderr)
            self.assertFalse(bad_destination.exists())


if __name__ == "__main__":
    unittest.main()
