#!/usr/bin/env python3
"""Create and verify the immutable Taiji target-acceptance tool inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


SCHEMA = "taiji-acceptance-tools-manifest/v1"
MANIFEST_BASENAME = "acceptance-tools-manifest.json"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE_BYTES = 16 * 1024 * 1024
CANONICAL_DIRECTORIES = ()  # type: Tuple[str, ...]
CANONICAL_LAUNCHER = {
    "delivery_basename": "04_目标终端_桌面App验收并导出证据.sh",
    "source_path": "taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh",
    "mode": 0o755,
}
CANONICAL_FILES = (
    {
        "delivery_path": "run-installed-electron-acceptance.js",
        "source_path": "tools/taiji-desktop-acceptance/run-installed-electron-acceptance.js",
        "mode": 0o644,
    },
    {
        "delivery_path": "assemble-target-evidence.py",
        "source_path": "tools/taiji-desktop-acceptance/assemble-target-evidence.py",
        "mode": 0o644,
    },
    {
        "delivery_path": "observe-single-deb-install.py",
        "source_path": "tools/taiji-desktop-acceptance/observe-single-deb-install.py",
        "mode": 0o644,
    },
    {
        "delivery_path": "certification-matrix.json",
        "source_path": "packaging/linux/certification-matrix.json",
        "mode": 0o644,
    },
    {
        "delivery_path": "validate-taiji-release-evidence.py",
        "source_path": "scripts/validate-taiji-release-evidence.py",
        "mode": 0o644,
    },
    {
        "delivery_path": "signing-public.pem",
        "source_path": "tools/taiji-release-evidence/signing-public.pem",
        "mode": 0o644,
    },
)


class ManifestError(ValueError):
    """Raised when an acceptance tool or its inventory is not trustworthy."""


def _strict_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise ManifestError("acceptance tools manifest contains a duplicate field")
        result[key] = value
    return result


def _canonical_bytes(payload: Dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _stat_identity(metadata: os.stat_result) -> Tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_directory_chain(path: Path, expected_leaf_owner_uid: int, label: str) -> int:
    absolute = Path(os.path.abspath(str(path)))
    if not absolute.is_absolute():
        raise ManifestError("%s path is not absolute" % label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute.anchor, flags)
    except OSError as exc:
        raise ManifestError("%s directory chain cannot be opened" % label) from exc
    try:
        parts = absolute.parts[1:]
        for index, part in enumerate(parts):
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise ManifestError("%s directory chain contains a symlink or unreadable node" % label) from exc
            try:
                opened = os.fstat(child)
                current = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                allowed_owners = {0, expected_leaf_owner_uid}
                mode = stat.S_IMODE(opened.st_mode)
                is_leaf = index == len(parts) - 1
                trusted_sticky_ancestor = (
                    not is_leaf
                    and mode == 0o1777
                    and opened.st_uid in allowed_owners
                )
                if (
                    _stat_identity(opened) != _stat_identity(current)
                    or not stat.S_ISDIR(opened.st_mode)
                    or opened.st_uid not in allowed_owners
                    or (mode & 0o022 and not trusted_sticky_ancestor)
                    or (is_leaf and opened.st_uid != expected_leaf_owner_uid)
                ):
                    raise ManifestError("%s directory chain is not trusted" % label)
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_relative_parent(
    root_descriptor: int,
    relative_path: str,
    expected_owner_uid: int,
    label: str,
) -> Tuple[int, str]:
    canonical = _validate_relative_path(relative_path, label)
    parts = Path(canonical).parts
    descriptor = os.dup(root_descriptor)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise ManifestError("%s parent directory contains a symlink or unreadable node" % label) from exc
            try:
                opened = os.fstat(child)
                current = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                if (
                    _stat_identity(opened) != _stat_identity(current)
                    or not stat.S_ISDIR(opened.st_mode)
                    or opened.st_uid != expected_owner_uid
                    or stat.S_IMODE(opened.st_mode) & 0o022
                ):
                    raise ManifestError("%s parent directory is not owner-controlled" % label)
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_file_at(
    root_descriptor: int,
    relative_path: str,
    expected_owner_uid: int,
    expected_mode: int,
    label: str,
) -> bytes:
    parent_descriptor, basename = _open_relative_parent(
        root_descriptor,
        relative_path,
        expected_owner_uid,
        label,
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            before = os.stat(basename, dir_fd=parent_descriptor, follow_symlinks=False)
            descriptor = os.open(basename, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise ManifestError("%s is missing or cannot be opened safely" % label) from exc
        try:
            opened = os.fstat(descriptor)
            if (
                _stat_identity(opened) != _stat_identity(before)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != expected_owner_uid
                or opened.st_nlink != 1
                or stat.S_IMODE(opened.st_mode) != expected_mode
                or opened.st_size <= 0
                or opened.st_size > MAX_FILE_BYTES
            ):
                raise ManifestError(
                    "%s must be one owner-controlled regular file with the exact mode; "
                    "symlink, hard link, or writable mode is forbidden" % label
                )
            chunks = []  # type: List[bytes]
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            current = os.stat(basename, dir_fd=parent_descriptor, follow_symlinks=False)
            if (
                len(payload) != opened.st_size
                or _stat_identity(os.fstat(descriptor)) != _stat_identity(opened)
                or _stat_identity(current) != _stat_identity(opened)
            ):
                raise ManifestError("%s changed while it was read" % label)
            return payload
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def _read_regular_file(
    path: Path,
    expected_owner_uid: int,
    expected_mode: int,
    label: str,
) -> bytes:
    path = Path(os.path.abspath(str(path)))
    parent_descriptor = _open_directory_chain(path.parent, expected_owner_uid, "%s parent" % label)
    try:
        return _read_regular_file_at(
            parent_descriptor,
            path.name,
            expected_owner_uid,
            expected_mode,
            label,
        )
    finally:
        os.close(parent_descriptor)


def _validate_relative_path(value: Any, label: str) -> str:
    if type(value) is not str or not value or len(value) > 512 or "\\" in value:
        raise ManifestError("%s is invalid" % label)
    path = Path(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManifestError("%s is not a canonical relative path" % label)
    return value


def _canonical_file_specs() -> List[Dict[str, Any]]:
    return sorted(
        [dict(entry) for entry in CANONICAL_FILES],
        key=lambda entry: entry["delivery_path"],
    )


def _source_identity_snapshot(
    root_descriptor: int,
    specs: List[Dict[str, Any]],
    expected_owner_uid: int,
) -> Dict[str, Tuple[int, ...]]:
    snapshot = {}  # type: Dict[str, Tuple[int, ...]]
    for spec in specs:
        source_path = spec["source_path"]
        parent_descriptor, basename = _open_relative_parent(
            root_descriptor,
            source_path,
            expected_owner_uid,
            "acceptance source %s" % source_path,
        )
        try:
            try:
                before = os.stat(basename, dir_fd=parent_descriptor, follow_symlinks=False)
                descriptor = os.open(
                    basename,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise ManifestError("acceptance source identity cannot be opened safely") from exc
            try:
                opened = os.fstat(descriptor)
                if (
                    _stat_identity(opened) != _stat_identity(before)
                    or not stat.S_ISREG(opened.st_mode)
                    or opened.st_uid != expected_owner_uid
                    or opened.st_nlink != 1
                    or stat.S_IMODE(opened.st_mode) != spec["source_mode"]
                    or opened.st_size <= 0
                    or opened.st_size > MAX_FILE_BYTES
                ):
                    raise ManifestError("acceptance source identity is not trusted")
                snapshot[source_path] = _stat_identity(opened)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_descriptor)
    return snapshot


def create_manifest(repo_root: Path, source_commit: str) -> Dict[str, Any]:
    repo_root = Path(os.path.abspath(str(repo_root)))
    if not COMMIT_RE.fullmatch(source_commit or ""):
        raise ManifestError("source commit must be one full lowercase Git SHA")
    try:
        root_metadata = repo_root.lstat()
    except OSError as exc:
        raise ManifestError("repository root is unavailable") from exc
    if repo_root.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
        raise ManifestError("repository root must be a real directory")
    expected_owner_uid = root_metadata.st_uid
    root_descriptor = _open_directory_chain(
        repo_root,
        expected_owner_uid,
        "repository root",
    )
    held_root_identity = _stat_identity(os.fstat(root_descriptor))
    source_specs = [
        {
            "source_path": CANONICAL_LAUNCHER["source_path"],
            "source_mode": CANONICAL_LAUNCHER["mode"],
        }
    ]
    source_specs.extend(
        {
            "source_path": entry["source_path"],
            "source_mode": entry.get("source_mode", entry["mode"]),
        }
        for entry in _canonical_file_specs()
    )
    try:
        initial_source_identities = _source_identity_snapshot(
            root_descriptor,
            source_specs,
            expected_owner_uid,
        )
        launcher_payload = _read_regular_file_at(
            root_descriptor,
            CANONICAL_LAUNCHER["source_path"],
            expected_owner_uid,
            CANONICAL_LAUNCHER["mode"],
            "acceptance launcher source",
        )
        launcher = {
            "delivery_basename": CANONICAL_LAUNCHER["delivery_basename"],
            "source_path": CANONICAL_LAUNCHER["source_path"],
            "mode": CANONICAL_LAUNCHER["mode"],
            "sha256": hashlib.sha256(launcher_payload).hexdigest(),
        }
        files = []  # type: List[Dict[str, Any]]
        for entry in _canonical_file_specs():
            payload = _read_regular_file_at(
                root_descriptor,
                entry["source_path"],
                expected_owner_uid,
                entry.get("source_mode", entry["mode"]),
                "acceptance tool source %s" % entry["source_path"],
            )
            files.append(
                {
                    "delivery_path": entry["delivery_path"],
                    "source_path": entry["source_path"],
                    "mode": entry["mode"],
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        if (
            _source_identity_snapshot(root_descriptor, source_specs, expected_owner_uid)
            != initial_source_identities
        ):
            raise ManifestError("acceptance source identity changed while manifest was created")
        final_launcher_payload = _read_regular_file_at(
            root_descriptor,
            CANONICAL_LAUNCHER["source_path"],
            expected_owner_uid,
            CANONICAL_LAUNCHER["mode"],
            "acceptance launcher source final recheck",
        )
        if final_launcher_payload != launcher_payload:
            raise ManifestError("acceptance launcher source changed while manifest was created")
        first_payloads = {item["source_path"]: item["sha256"] for item in files}
        for entry in _canonical_file_specs():
            final_payload = _read_regular_file_at(
                root_descriptor,
                entry["source_path"],
                expected_owner_uid,
                entry.get("source_mode", entry["mode"]),
                "acceptance tool source final recheck %s" % entry["source_path"],
            )
            if hashlib.sha256(final_payload).hexdigest() != first_payloads[entry["source_path"]]:
                raise ManifestError("acceptance tool source changed while manifest was created")
        if (
            _source_identity_snapshot(root_descriptor, source_specs, expected_owner_uid)
            != initial_source_identities
        ):
            raise ManifestError("acceptance source identity changed during final recheck")
        if _stat_identity(os.fstat(root_descriptor)) != held_root_identity:
            raise ManifestError("repository root changed while acceptance tools were read")
        current_descriptor = _open_directory_chain(
            repo_root,
            expected_owner_uid,
            "repository root recheck",
        )
        try:
            if _stat_identity(os.fstat(current_descriptor)) != held_root_identity:
                raise ManifestError("repository root identity changed while acceptance tools were read")
        finally:
            os.close(current_descriptor)
    finally:
        os.close(root_descriptor)
    result = {
        "schema": SCHEMA,
        "source_commit": source_commit,
        "launcher": launcher,
        "directories": list(CANONICAL_DIRECTORIES),
        "files": files,
    }
    validate_manifest(result, source_commit)
    return result


def validate_manifest(payload: Any, expected_source_commit: str) -> Dict[str, Any]:
    if type(payload) is not dict or set(payload) != {
        "schema", "source_commit", "launcher", "directories", "files"
    }:
        raise ManifestError("acceptance tools manifest has an invalid exact field set")
    if payload["schema"] != SCHEMA or payload["source_commit"] != expected_source_commit:
        raise ManifestError("acceptance tools manifest schema or source commit is invalid")
    if not COMMIT_RE.fullmatch(payload["source_commit"]):
        raise ManifestError("acceptance tools manifest source commit is invalid")
    if payload["directories"] != list(CANONICAL_DIRECTORIES):
        raise ManifestError("acceptance tools manifest directory closure is invalid")
    launcher = payload["launcher"]
    if type(launcher) is not dict or set(launcher) != {
        "delivery_basename", "source_path", "mode", "sha256"
    }:
        raise ManifestError("acceptance tools launcher fields are invalid")
    for key in ("delivery_basename", "source_path"):
        _validate_relative_path(launcher[key], "launcher %s" % key)
    if (
        launcher["delivery_basename"] != CANONICAL_LAUNCHER["delivery_basename"]
        or launcher["source_path"] != CANONICAL_LAUNCHER["source_path"]
        or launcher["mode"] != CANONICAL_LAUNCHER["mode"]
        or type(launcher["mode"]) is not int
        or not SHA256_RE.fullmatch(launcher["sha256"] or "")
    ):
        raise ManifestError("acceptance tools launcher identity is invalid")
    files = payload["files"]
    if type(files) is not list or len(files) != len(CANONICAL_FILES):
        raise ManifestError("acceptance tools file closure is invalid")
    expected_specs = _canonical_file_specs()
    observed_paths = []  # type: List[str]
    for index, item in enumerate(files):
        if type(item) is not dict or set(item) != {"delivery_path", "source_path", "mode", "sha256"}:
            raise ManifestError("acceptance tools file fields are invalid")
        expected = expected_specs[index]
        _validate_relative_path(item["delivery_path"], "tool delivery path")
        _validate_relative_path(item["source_path"], "tool source path")
        if (
            item["delivery_path"] != expected["delivery_path"]
            or item["source_path"] != expected["source_path"]
            or type(item["mode"]) is not int
            or item["mode"] != expected["mode"]
            or not SHA256_RE.fullmatch(item["sha256"] or "")
        ):
            raise ManifestError("acceptance tools file identity is invalid")
        observed_paths.append(item["delivery_path"])
    if observed_paths != sorted(observed_paths) or len(observed_paths) != len(set(observed_paths)):
        raise ManifestError("acceptance tools file paths are not canonical and unique")
    return payload


def parse_manifest_bytes(raw: bytes) -> Dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("acceptance tools manifest is not strict UTF-8 JSON") from exc
    if type(payload) is not dict:
        raise ManifestError("acceptance tools manifest must be one JSON object")
    source_commit = payload.get("source_commit")
    validate_manifest(payload, source_commit)
    if raw != _canonical_bytes(payload):
        raise ManifestError("acceptance tools manifest is not canonical JSON")
    return payload


def verify_source(repo_root: Path, payload: Dict[str, Any], source_commit: str) -> None:
    validate_manifest(payload, source_commit)
    expected = create_manifest(repo_root, source_commit)
    if expected != payload:
        raise ManifestError("acceptance tool source digest changed")


def _verify_directory(path: Path, owner_uid: int, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError("%s directory is missing" % label) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) != 0o755
    ):
        raise ManifestError("%s directory is not an owner-controlled 0755 directory" % label)


def _directory_tree_snapshot(tools_root: Path, owner_uid: int) -> Dict[str, Any]:
    root_descriptor = _open_directory_chain(
        tools_root,
        owner_uid,
        "acceptance tools root",
    )
    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

    def scan(descriptor: int, prefix: str) -> Tuple[Dict[str, Tuple[int, ...]], Dict[str, Tuple[int, ...]]]:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(before.st_mode)
            or before.st_uid != owner_uid
            or stat.S_IMODE(before.st_mode) != 0o755
        ):
            raise ManifestError("acceptance tools directory is not an owner-controlled 0755 directory")
        try:
            entries = sorted(list(os.scandir(descriptor)), key=lambda entry: entry.name)
        except OSError as exc:
            raise ManifestError("acceptance tools directory closure cannot be scanned") from exc
        directories = {}  # type: Dict[str, Tuple[int, ...]]
        files = {}  # type: Dict[str, Tuple[int, ...]]
        observed_names = []  # type: List[str]
        for entry in entries:
            name = entry.name
            observed_names.append(name)
            relative = "%s/%s" % (prefix, name) if prefix else name
            try:
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise ManifestError("acceptance tools closure changed while scanned") from exc
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    child = os.open(name, directory_flags, dir_fd=descriptor)
                except OSError as exc:
                    raise ManifestError("acceptance tools closure contains a symlink directory") from exc
                try:
                    opened = os.fstat(child)
                    if (
                        _stat_identity(opened) != _stat_identity(metadata)
                        or opened.st_uid != owner_uid
                        or stat.S_IMODE(opened.st_mode) != 0o755
                    ):
                        raise ManifestError("acceptance tools directory identity is not trusted")
                    directories[relative] = _stat_identity(opened)
                    child_directories, child_files = scan(child, relative)
                    directories.update(child_directories)
                    files.update(child_files)
                    current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if _stat_identity(current) != _stat_identity(os.fstat(child)):
                        raise ManifestError("acceptance tools directory changed while scanned")
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                files[relative] = _stat_identity(metadata)
            else:
                raise ManifestError("acceptance tools closure contains a symlink or special node")
        try:
            names_after = sorted(entry.name for entry in os.scandir(descriptor))
        except OSError as exc:
            raise ManifestError("acceptance tools directory closure cannot be rescanned") from exc
        if names_after != observed_names or _stat_identity(os.fstat(descriptor)) != _stat_identity(before):
            raise ManifestError("acceptance tools directory closure changed while scanned")
        return directories, files

    try:
        root_identity = _stat_identity(os.fstat(root_descriptor))
        directories, files = scan(root_descriptor, "")
        if _stat_identity(os.fstat(root_descriptor)) != root_identity:
            raise ManifestError("acceptance tools root changed while scanned")
        return {"root": root_identity, "directories": directories, "files": files}
    finally:
        os.close(root_descriptor)


def verify_staged(
    tools_root: Path,
    expected_source_commit: str,
    expected_manifest_sha256: str,
    expected_owner_uid: int,
) -> Dict[str, Any]:
    if not COMMIT_RE.fullmatch(expected_source_commit or ""):
        raise ManifestError("expected source commit is invalid")
    if not SHA256_RE.fullmatch(expected_manifest_sha256 or ""):
        raise ManifestError("expected acceptance tools manifest digest is invalid")
    tools_root = Path(os.path.abspath(str(tools_root)))
    manifest_path = tools_root / MANIFEST_BASENAME
    launcher_path = tools_root.parent / CANONICAL_LAUNCHER["delivery_basename"]
    initial_snapshot = _directory_tree_snapshot(tools_root, expected_owner_uid)

    manifest_raw = _read_regular_file(
        manifest_path,
        expected_owner_uid,
        0o644,
        "acceptance tools manifest",
    )
    if hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256:
        raise ManifestError("acceptance tools manifest digest changed")
    payload = parse_manifest_bytes(manifest_raw)
    validate_manifest(payload, expected_source_commit)

    expected_directories = set(payload["directories"])
    expected_files = {item["delivery_path"] for item in payload["files"]} | {MANIFEST_BASENAME}
    if (
        set(initial_snapshot["directories"]) != expected_directories
        or set(initial_snapshot["files"]) != expected_files
    ):
        raise ManifestError("acceptance tools directory closure has an unknown or missing node")

    launcher_raw = _read_regular_file(
        launcher_path,
        expected_owner_uid,
        payload["launcher"]["mode"],
        "acceptance launcher",
    )
    if launcher_path.name != payload["launcher"]["delivery_basename"] or hashlib.sha256(launcher_raw).hexdigest() != payload["launcher"]["sha256"]:
        raise ManifestError("acceptance launcher digest changed")
    for item in payload["files"]:
        raw = _read_regular_file(
            tools_root / item["delivery_path"],
            expected_owner_uid,
            item["mode"],
            "acceptance tool %s" % item["delivery_path"],
        )
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ManifestError("acceptance tool digest changed: %s" % item["delivery_path"])
    final_snapshot = _directory_tree_snapshot(tools_root, expected_owner_uid)
    if final_snapshot != initial_snapshot:
        raise ManifestError("acceptance tools directory closure changed during verification")
    final_launcher_raw = _read_regular_file(
        launcher_path,
        expected_owner_uid,
        payload["launcher"]["mode"],
        "acceptance launcher final recheck",
    )
    if (
        final_launcher_raw != launcher_raw
        or hashlib.sha256(final_launcher_raw).hexdigest() != payload["launcher"]["sha256"]
    ):
        raise ManifestError("acceptance launcher changed during verification")
    return payload


def write_manifest_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    validate_manifest(payload, payload.get("source_commit"))
    path = Path(os.path.abspath(str(path)))
    owner_uid = os.geteuid()
    parent_descriptor = _open_directory_chain(
        path.parent,
        owner_uid,
        "acceptance tools manifest parent",
    )
    temporary_name = ".%s.tmp-%s-%s" % (path.name, os.getpid(), secrets.token_hex(8))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    published_descriptor = None
    destination_created = False
    try:
        try:
            os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ManifestError("acceptance tools manifest destination cannot be inspected") from exc
        else:
            raise ManifestError("acceptance tools manifest already exists; refusing to publish over it")
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_descriptor)
        raw = _canonical_bytes(payload)
        written = 0
        while written < len(raw):
            count = os.write(descriptor, raw[written:])
            if count <= 0:
                raise ManifestError("acceptance tools manifest write made no progress")
            written += count
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
        staged = os.fstat(descriptor)
        if (
            not stat.S_ISREG(staged.st_mode)
            or staged.st_uid != owner_uid
            or staged.st_nlink != 1
            or stat.S_IMODE(staged.st_mode) != 0o644
            or staged.st_size != len(raw)
        ):
            raise ManifestError("acceptance tools manifest temporary identity is invalid")
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        destination_created = True
        published_descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        published = os.fstat(published_descriptor)
        if (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino):
            raise ManifestError("acceptance tools manifest publish identity changed")
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        current = os.fstat(published_descriptor)
        current_path = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            _stat_identity(current) != _stat_identity(current_path)
            or current.st_uid != owner_uid
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) != 0o644
            or current.st_size != len(raw)
        ):
            raise ManifestError("acceptance tools manifest final identity changed")
        os.lseek(published_descriptor, 0, os.SEEK_SET)
        observed = b""
        while len(observed) < len(raw):
            chunk = os.read(published_descriptor, len(raw) - len(observed))
            if not chunk:
                break
            observed += chunk
        if observed != raw or _stat_identity(os.fstat(published_descriptor)) != _stat_identity(current):
            raise ManifestError("acceptance tools manifest published bytes changed")
        os.fsync(published_descriptor)
        os.fsync(parent_descriptor)
        destination_created = False
    except (OSError, ManifestError) as exc:
        if destination_created:
            try:
                os.unlink(path.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("acceptance tools manifest could not be published exclusively") from exc
    finally:
        if published_descriptor is not None:
            os.close(published_descriptor)
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        os.close(parent_descriptor)


def _main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--repo-root", type=Path, required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify-source")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "create":
        write_manifest_exclusive(
            args.output,
            create_manifest(args.repo_root, args.source_commit),
        )
    else:
        raw = _read_regular_file(
            args.manifest,
            os.geteuid(),
            0o644,
            "acceptance tools manifest input",
        )
        payload = parse_manifest_bytes(raw)
        verify_source(args.repo_root, payload, args.source_commit)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except ManifestError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        raise SystemExit(1)
