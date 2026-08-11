#!/usr/bin/env python3
"""Create and verify the immutable Taiji formal source-archive inventory."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Dict, Iterable, Iterator, List, Optional, Set, Tuple


SCHEMA = "taiji-source-archive-inventory/v1"
ROOT_PREFIX = "taiji-agentv1.0"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
ARCHIVE_RE = re.compile(
    r"taiji-agentv1\.0-kylin-build-src-([0-9a-f]{40})\.tar\.gz"
)
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEMBERS = 1_000_000
MAX_INVENTORY_BYTES = 256 * 1024 * 1024


class SourceIntegrityError(RuntimeError):
    pass


def _stable_identity(metadata: os.stat_result) -> Tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@contextlib.contextmanager
def _stable_regular_file(
    path: Path, label: str, max_bytes: int
) -> Iterator[Tuple[BinaryIO, os.stat_result]]:
    before = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SourceIntegrityError("{} must be a single-link regular file".format(label))
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise SourceIntegrityError("{} has an invalid size".format(label))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise SourceIntegrityError("{} cannot be opened safely".format(label)) from exc
    handle = os.fdopen(descriptor, "rb", closefd=True)
    try:
        opened = os.fstat(handle.fileno())
        if _stable_identity(before) != _stable_identity(opened):
            raise SourceIntegrityError("{} changed before it was opened".format(label))
        yield handle, opened
        after = os.fstat(handle.fileno())
        current = path.lstat()
        if (
            _stable_identity(opened) != _stable_identity(after)
            or _stable_identity(opened) != _stable_identity(current)
        ):
            raise SourceIntegrityError("{} changed while it was read".format(label))
    finally:
        handle.close()


def _canonical_bytes(value: Dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _safe_member_name(raw: str) -> Tuple[str, str]:
    if not raw or "\\" in raw or "\x00" in raw:
        raise SourceIntegrityError("source archive contains an unsafe member path")
    path = PurePosixPath(raw.rstrip("/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise SourceIntegrityError("source archive contains an unsafe member path: {}".format(raw))
    if not path.parts or path.parts[0] != ROOT_PREFIX:
        raise SourceIntegrityError("source archive member is outside the fixed root prefix: {}".format(raw))
    relative = PurePosixPath(*path.parts[1:]).as_posix() if len(path.parts) > 1 else ""
    return path.as_posix(), relative


def _normalized_relative_target(parent: PurePosixPath, target: str) -> PurePosixPath:
    raw = PurePosixPath(target)
    if not target or "\\" in target or "\x00" in target or raw.is_absolute():
        raise SourceIntegrityError("source archive contains an unsafe symlink target")
    stack = list(parent.parts)
    for part in raw.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                raise SourceIntegrityError("source archive symlink escapes the source root")
            stack.pop()
        else:
            stack.append(part)
    return PurePosixPath(*stack)


def _member_record(bundle: tarfile.TarFile, member: tarfile.TarInfo, relative: str) -> Dict[str, Any]:
    mode = member.mode & 0o777
    if member.isdir():
        return {"mode": mode, "path": relative, "type": "directory"}
    if member.issym():
        _normalized_relative_target(PurePosixPath(relative).parent, member.linkname)
        return {
            "mode": mode,
            "path": relative,
            "target": member.linkname,
            "type": "symlink",
        }
    if not member.isfile() or member.islnk():
        raise SourceIntegrityError(
            "source archive contains an unsupported member type: {}".format(member.name)
        )
    if member.size < 0 or member.size > MAX_MEMBER_BYTES:
        raise SourceIntegrityError("source archive member size is excessive: {}".format(member.name))
    extracted = bundle.extractfile(member)
    if extracted is None:
        raise SourceIntegrityError("source archive member cannot be read: {}".format(member.name))
    digest = hashlib.sha256()
    remaining = member.size
    while remaining:
        chunk = extracted.read(min(1024 * 1024, remaining))
        if not chunk:
            raise SourceIntegrityError("source archive member is truncated: {}".format(member.name))
        digest.update(chunk)
        remaining -= len(chunk)
    if extracted.read(1):
        raise SourceIntegrityError("source archive member exceeds its declared size: {}".format(member.name))
    return {
        "mode": mode,
        "path": relative,
        "sha256": digest.hexdigest(),
        "size": member.size,
        "type": "file",
    }


def build_inventory(archive: Path, source_commit: str) -> Dict[str, Any]:
    if not COMMIT_RE.fullmatch(source_commit):
        raise SourceIntegrityError("source commit must be a full lowercase SHA")
    match = ARCHIVE_RE.fullmatch(archive.name)
    if match is None or match.group(1) != source_commit:
        raise SourceIntegrityError("source archive basename does not bind the source commit")
    records: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    total_file_bytes = 0
    try:
        with _stable_regular_file(archive, "source archive", MAX_ARCHIVE_BYTES) as opened:
            archive_handle, archive_metadata = opened
            digest = hashlib.sha256()
            while True:
                chunk = archive_handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            if archive_handle.tell() != archive_metadata.st_size:
                raise SourceIntegrityError("source archive changed while being hashed")
            archive_sha256 = digest.hexdigest()
            archive_handle.seek(0)
            with tarfile.open(fileobj=archive_handle, mode="r:gz") as bundle:
                for member in bundle:
                    _full, relative = _safe_member_name(member.name)
                    if relative == "":
                        if not member.isdir():
                            raise SourceIntegrityError("source archive root prefix must be a directory")
                        continue
                    if relative in seen:
                        raise SourceIntegrityError(
                            "source archive contains a duplicate member: {}".format(relative)
                        )
                    seen.add(relative)
                    if len(seen) > MAX_MEMBERS:
                        raise SourceIntegrityError("source archive contains too many members")
                    record = _member_record(bundle, member, relative)
                    if record["type"] == "file":
                        total_file_bytes += record["size"]
                        if total_file_bytes > MAX_TOTAL_FILE_BYTES:
                            raise SourceIntegrityError("source archive total file size is excessive")
                    records.append(record)
    except (OSError, tarfile.TarError) as exc:
        raise SourceIntegrityError("source archive cannot be inspected: {}".format(exc))
    records.sort(key=lambda item: item["path"])
    members_sha256 = hashlib.sha256(
        _canonical_bytes({"members": records})
    ).hexdigest()
    return {
        "archive_basename": archive.name,
        "archive_sha256": archive_sha256,
        "members": records,
        "members_sha256": members_sha256,
        "root_prefix": ROOT_PREFIX,
        "schema": SCHEMA,
        "source_commit": source_commit,
    }


def _load_inventory(path: Path) -> Dict[str, Any]:
    try:
        pairs_seen: List[str] = []

        def reject_duplicates(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise SourceIntegrityError("source inventory contains duplicate JSON keys")
                result[key] = value
            pairs_seen.extend(result)
            return result

        with _stable_regular_file(path, "source inventory", MAX_INVENTORY_BYTES) as opened:
            inventory_handle, inventory_metadata = opened
            payload = inventory_handle.read(inventory_metadata.st_size + 1)
            if len(payload) != inventory_metadata.st_size:
                raise SourceIntegrityError("source inventory changed while it was read")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceIntegrityError("source inventory is invalid JSON: {}".format(exc))
    if type(value) is not dict:
        raise SourceIntegrityError("source inventory root must be an object")
    return value


def _safe_extra_prefix(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise SourceIntegrityError("allow-extra-prefix is unsafe: {}".format(raw))
    return path


def _is_allowed_extra(relative: PurePosixPath, prefixes: Iterable[PurePosixPath]) -> bool:
    for prefix in prefixes:
        if relative == prefix or prefix in relative.parents or relative in prefix.parents:
            return True
    return False


def _tree_file_record(path: Path, relative: str) -> Dict[str, Any]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SourceIntegrityError(
            "extracted source file must be a single-link regular file: {}".format(relative)
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise SourceIntegrityError(
            "extracted source file cannot be opened safely: {}".format(relative)
        ) from exc
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            _stable_identity(before) != _stable_identity(after)
            or _stable_identity(after) != _stable_identity(current)
            or size != after.st_size
        ):
            raise SourceIntegrityError(
                "extracted source file changed while being verified: {}".format(relative)
            )
        return {
            "mode": stat.S_IMODE(after.st_mode),
            "path": relative,
            "sha256": digest.hexdigest(),
            "size": after.st_size,
            "type": "file",
        }
    finally:
        os.close(descriptor)


def _tree_inventory(root: Path) -> Dict[str, Dict[str, Any]]:
    metadata = root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise SourceIntegrityError("extracted source root must be a real directory")
    actual: Dict[str, Dict[str, Any]] = {}
    directory_identities: Dict[str, Tuple[int, int, int, int, int, int, int]] = {
        "": _stable_identity(metadata)
    }
    for current_raw, directories, files in os.walk(str(root), topdown=True, followlinks=False):
        current = Path(current_raw)
        current_relative = current.relative_to(root).as_posix()
        if current_relative == ".":
            current_relative = ""
        current_metadata = current.lstat()
        if current.is_symlink() or not stat.S_ISDIR(current_metadata.st_mode):
            raise SourceIntegrityError(
                "extracted source directory changed while being verified: {}".format(
                    current_relative or "."
                )
            )
        expected_directory_identity = directory_identities.get(current_relative)
        if (
            expected_directory_identity is not None
            and expected_directory_identity != _stable_identity(current_metadata)
        ):
            raise SourceIntegrityError(
                "extracted source directory changed while being verified: {}".format(
                    current_relative or "."
                )
            )
        candidates = list(directories) + list(files)
        for name in candidates:
            candidate = current / name
            relative = candidate.relative_to(root).as_posix()
            info = candidate.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(str(candidate))
                _normalized_relative_target(PurePosixPath(relative).parent, target)
                if _stable_identity(info) != _stable_identity(candidate.lstat()):
                    raise SourceIntegrityError(
                        "extracted source symlink changed while being verified: {}".format(relative)
                    )
                actual[relative] = {
                    "mode": mode,
                    "path": relative,
                    "target": target,
                    "type": "symlink",
                }
                if name in directories:
                    directories.remove(name)
            elif stat.S_ISDIR(info.st_mode):
                directory_identities[relative] = _stable_identity(info)
                actual[relative] = {"mode": mode, "path": relative, "type": "directory"}
            elif stat.S_ISREG(info.st_mode):
                actual[relative] = _tree_file_record(candidate, relative)
            else:
                raise SourceIntegrityError("extracted source contains an unsupported node: {}".format(relative))
    for relative, expected_identity in directory_identities.items():
        directory = root if not relative else root / relative
        current = directory.lstat()
        if directory.is_symlink() or _stable_identity(current) != expected_identity:
            raise SourceIntegrityError(
                "extracted source directory changed while being verified: {}".format(
                    relative or "."
                )
            )
    return actual


def verify_tree(root: Path, expected: Dict[str, Any], allowed_prefixes: List[str]) -> None:
    expected_records = {item["path"]: item for item in expected["members"]}
    actual = _tree_inventory(root)
    for path, record in expected_records.items():
        if actual.get(path) != record:
            raise SourceIntegrityError("source tree member drift: {}".format(path))
    prefixes = [_safe_extra_prefix(value) for value in allowed_prefixes]
    for path in sorted(set(actual) - set(expected_records)):
        if not _is_allowed_extra(PurePosixPath(path), prefixes):
            raise SourceIntegrityError("unexpected source tree member: {}".format(path))


def create_inventory(archive: Path, inventory: Path, source_commit: str) -> None:
    if inventory.exists() or inventory.is_symlink():
        raise SourceIntegrityError("source inventory output already exists")
    payload = _canonical_bytes(build_inventory(archive, source_commit))
    inventory.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".source-inventory.tmp-", dir=str(inventory.parent)
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, str(inventory), follow_symlinks=False)
        except FileExistsError as exc:
            raise SourceIntegrityError("source inventory output already exists") from exc
        os.unlink(temporary_name)
        temporary_name = ""
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            if temporary_name:
                os.unlink(temporary_name)
        except OSError:
            pass
        raise


def verify(archive: Path, inventory: Path, root: Optional[Path], allowed_prefixes: List[str]) -> None:
    supplied = _load_inventory(inventory)
    commit = supplied.get("source_commit")
    if type(commit) is not str:
        raise SourceIntegrityError("source inventory source_commit is invalid")
    expected = build_inventory(archive, commit)
    if supplied != expected:
        raise SourceIntegrityError("source inventory differs from the archive-derived inventory")
    if root is not None:
        verify_tree(root, expected, allowed_prefixes)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", allow_abbrev=False)
    create.add_argument("--archive", required=True, type=Path)
    create.add_argument("--inventory", required=True, type=Path)
    create.add_argument("--source-commit", required=True)
    check = subparsers.add_parser("verify", allow_abbrev=False)
    check.add_argument("--archive", required=True, type=Path)
    check.add_argument("--inventory", required=True, type=Path)
    check.add_argument("--root", type=Path)
    check.add_argument("--allow-extra-prefix", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "create":
            create_inventory(args.archive, args.inventory, args.source_commit)
        else:
            verify(args.archive, args.inventory, args.root, args.allow_extra_prefix)
    except (OSError, SourceIntegrityError) as exc:
        print("[FAIL] {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
