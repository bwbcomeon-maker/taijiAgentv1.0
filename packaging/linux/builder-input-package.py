#!/usr/bin/env python3
"""Create the formal Linux builder-input archive from an exact file allowlist."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import types
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple


SCHEMA = "taiji-builder-input-package/v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STATIC_INPUT_NAMES = (
    "00_制包机_生成离线交付包.sh",
    "01_制包机_发布预检.sh",
    "02_目标终端_安装并验证.sh",
    "03_目标终端_导出诊断报告.sh",
    "04_目标终端_桌面App验收并导出证据.sh",
    "99_本机_准备制包输入包.sh",
    "SHA256SUMS.txt",
    "操作说明.md",
    "版本信息.txt",
)
SOURCE_HELPER_BASENAME = "source-archive-integrity.py"
BUILDER_HELPER_BASENAME = "builder-input-package.py"
INPUT_ROOT_BASENAME = "taijiagent 打包交付"
MAX_INPUT_FILE_BYTES = 2 * 1024 * 1024 * 1024
MANIFEST_KEYS = {
    "schema",
    "source_commit",
    "archive_basename",
    "archive_root_basename",
    "archive_size",
    "archive_sha256",
    "manifest_basename",
    "checksum_basename",
    "source_archive_basename",
    "source_archive_sha256",
    "source_inventory_basename",
    "source_inventory_sha256",
    "source_integrity_helper_sha256",
    "builder_input_helper_sha256",
    "members",
}
MEMBER_KEYS = {"basename", "mode", "sha256", "size"}
FileIdentity = Tuple[int, int, int, int, int, int, int, int]
DirectoryIdentity = Tuple[int, int, int, int]


class BuilderInputError(RuntimeError):
    """The builder-input package did not satisfy the formal contract."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_regular(path: Path, label: str) -> Tuple[bytes, int]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise BuilderInputError("{} is missing".format(label)) from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise BuilderInputError("{} must be a regular non-symlink file".format(label))
    if before.st_uid != os.getuid() or before.st_nlink != 1:
        raise BuilderInputError("{} owner or hard-link count is unsafe".format(label))
    if stat.S_IMODE(before.st_mode) & 0o022:
        raise BuilderInputError("{} cannot be group/other writable".format(label))
    if before.st_size < 0 or before.st_size > MAX_INPUT_FILE_BYTES:
        raise BuilderInputError("{} size is outside the formal limit".format(label))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise BuilderInputError("{} cannot be opened safely".format(label)) from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if opened_identity != identity:
            raise BuilderInputError("{} changed before it was read".format(label))
        chunks = []  # type: List[bytes]
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise BuilderInputError("{} ended before its declared size".format(label))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise BuilderInputError("{} grew while it was read".format(label))
        after = os.fstat(descriptor)
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if after_identity != opened_identity:
            raise BuilderInputError("{} changed while it was read".format(label))
        return b"".join(chunks), stat.S_IMODE(opened.st_mode)
    finally:
        os.close(descriptor)


def _file_identity(metadata: os.stat_result) -> FileIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> DirectoryIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
    )


def _safe_unlink_owned(path: Path, owned_identity: FileIdentity) -> Optional[str]:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "cannot inspect owned path {}: {}".format(path, exc)
    if _file_identity(current) != owned_identity:
        return "path identity was replaced; foreign inode preserved: {}".format(path)
    try:
        path.unlink()
    except OSError as exc:
        return "cannot unlink owned path {}: {}".format(path, exc)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "cannot confirm owned path removal {}: {}".format(path, exc)
    return "owned path still exists after unlink: {}".format(path)


def _safe_unlink_owned_at(
    directory_fd: int,
    path: Path,
    owned_identity: FileIdentity,
) -> Optional[str]:
    try:
        current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "cannot inspect owned publication {}: {}".format(path, exc)
    if _file_identity(current) != owned_identity:
        return "publication identity was replaced; foreign inode preserved: {}".format(path)
    try:
        os.unlink(path.name, dir_fd=directory_fd)
    except OSError as exc:
        return "cannot unlink owned publication {}: {}".format(path, exc)
    try:
        os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "cannot confirm publication removal {}: {}".format(path, exc)
    return "owned publication still exists after unlink: {}".format(path)


def _check_owned_publications_at(
    directory_fd: int,
    owned_files: Sequence[Tuple[Path, FileIdentity]],
) -> None:
    for path, owned_identity in owned_files:
        try:
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise BuilderInputError(
                "published path identity is unavailable: {}".format(path)
            ) from exc
        if _file_identity(current) != owned_identity:
            raise BuilderInputError(
                "published path identity was replaced: {}".format(path)
            )


def _write_exclusive_impl(
    path: Path,
    payload: bytes,
    mode: int = 0o644,
    directory_fd: Optional[int] = None,
    retain_descriptor: bool = False,
) -> Any:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        if directory_fd is None:
            descriptor = os.open(str(path), flags, mode)
        else:
            descriptor = os.open(path.name, flags, mode, dir_fd=directory_fd)
    except OSError as exc:
        raise BuilderInputError("output already exists or is unsafe: {}".format(path)) from exc
    owned_identity = None  # type: Optional[FileIdentity]
    original_error = None  # type: Optional[BaseException]
    try:
        opened = os.fstat(descriptor)
        owned_identity = _file_identity(opened)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise BuilderInputError("failed to write {}".format(path))
            view = view[written:]
        os.fsync(descriptor)
        owned_identity = _file_identity(os.fstat(descriptor))
    except BaseException as exc:
        original_error = exc
        try:
            owned_identity = _file_identity(os.fstat(descriptor))
        except OSError:
            pass
    if original_error is None and retain_descriptor:
        if owned_identity is None:
            original_error = BuilderInputError(
                "output identity is unavailable after write: {}".format(path)
            )
        else:
            return owned_identity, descriptor
    try:
        os.close(descriptor)
    except BaseException as exc:
        if original_error is None:
            original_error = exc
    if original_error is not None:
        cleanup_error = None  # type: Optional[str]
        if owned_identity is not None:
            if directory_fd is None:
                cleanup_error = _safe_unlink_owned(path, owned_identity)
            else:
                cleanup_error = _safe_unlink_owned_at(directory_fd, path, owned_identity)
        if cleanup_error is not None:
            raise BuilderInputError(
                "write failed and rollback is incomplete/poisoned for {}: {}; original error: {}".format(
                    path, cleanup_error, original_error
                )
            ) from original_error
        raise original_error
    if owned_identity is None:
        raise BuilderInputError("output identity is unavailable after write: {}".format(path))
    return owned_identity


def _write_exclusive(
    path: Path,
    payload: bytes,
    mode: int = 0o644,
    directory_fd: Optional[int] = None,
) -> FileIdentity:
    return _write_exclusive_impl(path, payload, mode, directory_fd)


def _write_exclusive_held(
    path: Path,
    payload: bytes,
    mode: int = 0o644,
    directory_fd: Optional[int] = None,
) -> Tuple[FileIdentity, int]:
    return _write_exclusive_impl(
        path,
        payload,
        mode,
        directory_fd,
        retain_descriptor=True,
    )


def _validate_publication_directory(
    paths: Sequence[Path],
) -> Tuple[Path, int, DirectoryIdentity]:
    if not paths:
        raise BuilderInputError("publication path set is empty")
    absolute_parents = [Path(os.path.abspath(str(path.parent))) for path in paths]
    try:
        requested_metadata = [parent.lstat() for parent in absolute_parents]
        resolved_parents = [parent.resolve(strict=True) for parent in absolute_parents]
    except OSError as exc:
        raise BuilderInputError("output publication directory is unsafe") from exc
    if any(parent.is_symlink() for parent in absolute_parents):
        raise BuilderInputError("output publication directory is unsafe")
    if len({str(parent) for parent in resolved_parents}) != 1:
        raise BuilderInputError("output publication files must share one directory")
    parent = resolved_parents[0]
    metadata = parent.lstat()
    if (
        any(not stat.S_ISDIR(item.st_mode) for item in requested_metadata)
        or parent.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuilderInputError("output publication directory is unsafe")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(parent), flags)
    except OSError as exc:
        raise BuilderInputError("output publication directory cannot be opened safely") from exc
    try:
        identity = _directory_identity(metadata)
        if _directory_identity(os.fstat(descriptor)) != identity:
            raise BuilderInputError("output publication directory changed before use")
    except BaseException:
        os.close(descriptor)
        raise
    return parent, descriptor, identity


def _check_publication_directory(
    parent: Path,
    descriptor: int,
    identity: DirectoryIdentity,
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = parent.lstat()
    except OSError as exc:
        raise BuilderInputError("output publication directory changed during publication") from exc
    if _directory_identity(opened) != identity or _directory_identity(current) != identity:
        raise BuilderInputError("output publication directory changed during publication")


def _cleanup_private_stage(
    stage: Path,
    stage_identity: DirectoryIdentity,
    owned_files: Sequence[Tuple[Path, FileIdentity]],
) -> List[str]:
    errors = []  # type: List[str]
    for path, identity in reversed(list(owned_files)):
        error = _safe_unlink_owned(path, identity)
        if error is not None:
            errors.append(error)
    try:
        current = stage.lstat()
    except FileNotFoundError:
        return errors
    except OSError as exc:
        errors.append("cannot inspect private staging directory {}: {}".format(stage, exc))
        return errors
    if _directory_identity(current) != stage_identity:
        errors.append("private staging directory identity was replaced: {}".format(stage))
        return errors
    try:
        stage.rmdir()
    except OSError as exc:
        errors.append("cannot remove private staging directory {}: {}".format(stage, exc))
    return errors


def _publish_triplet(
    *,
    archive_payload: bytes,
    manifest_payload: bytes,
    checksum_payload: bytes,
    output: Path,
    manifest_path: Path,
    checksum_path: Path,
) -> None:
    parent, parent_fd, parent_identity = _validate_publication_directory(
        (output, manifest_path, checksum_path)
    )
    try:
        _publish_triplet_controlled(
            archive_payload=archive_payload,
            manifest_payload=manifest_payload,
            checksum_payload=checksum_payload,
            output=output,
            manifest_path=manifest_path,
            checksum_path=checksum_path,
            parent=parent,
            parent_fd=parent_fd,
            parent_identity=parent_identity,
        )
    finally:
        os.close(parent_fd)


def _publish_triplet_controlled(
    *,
    archive_payload: bytes,
    manifest_payload: bytes,
    checksum_payload: bytes,
    output: Path,
    manifest_path: Path,
    checksum_path: Path,
    parent: Path,
    parent_fd: int,
    parent_identity: DirectoryIdentity,
) -> None:
    _check_publication_directory(parent, parent_fd, parent_identity)
    destinations = (
        (Path(os.path.abspath(str(output))), archive_payload),
        (Path(os.path.abspath(str(manifest_path))), manifest_payload),
        (Path(os.path.abspath(str(checksum_path))), checksum_payload),
    )
    if len({path.name for path, _payload in destinations}) != 3:
        raise BuilderInputError("publication filenames must be distinct")
    for path, _payload in destinations:
        if path.parent.resolve(strict=True) != parent:
            raise BuilderInputError("publication path escaped the controlled directory")
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BuilderInputError("publication path cannot be inspected: {}".format(path)) from exc
        raise BuilderInputError("output already exists or is unsafe: {}".format(path))

    stage = Path(tempfile.mkdtemp(prefix="taiji-builder-input-stage-"))
    stage.chmod(0o700)
    stage_identity = _directory_identity(stage.lstat())
    stage_owned = []  # type: List[Tuple[Path, FileIdentity]]
    published = []  # type: List[Tuple[Path, FileIdentity]]
    published_descriptors = []  # type: List[int]
    primary_error = None  # type: Optional[BaseException]
    rollback_errors = []  # type: List[str]
    descriptor_close_errors = []  # type: List[str]
    try:
        staged_paths = []  # type: List[Path]
        for destination, payload in destinations:
            staged = stage / destination.name
            identity = _write_exclusive_impl(staged, payload)
            stage_owned.append((staged, identity))
            staged_paths.append(staged)
        verify_builder_input(
            archive_path=staged_paths[0],
            manifest_path=staged_paths[1],
            checksum_path=staged_paths[2],
        )
        frozen_payloads = [
            _read_regular(path, "staged builder input {}".format(path.name))[0]
            for path in staged_paths
        ]
        for (destination, _payload), frozen_payload in zip(destinations, frozen_payloads):
            _check_publication_directory(parent, parent_fd, parent_identity)
            owned_identity, held_descriptor = _write_exclusive_held(
                destination,
                frozen_payload,
                directory_fd=parent_fd,
            )
            published.append((destination, owned_identity))
            published_descriptors.append(held_descriptor)
            _check_owned_publications_at(parent_fd, published)
            _check_publication_directory(parent, parent_fd, parent_identity)
        _check_publication_directory(parent, parent_fd, parent_identity)
        _check_owned_publications_at(parent_fd, published)
        verify_builder_input(
            archive_path=destinations[0][0],
            manifest_path=destinations[1][0],
            checksum_path=destinations[2][0],
        )
        _check_owned_publications_at(parent_fd, published)
        _check_publication_directory(parent, parent_fd, parent_identity)
    except BaseException as exc:
        primary_error = exc
        for path, identity in reversed(published):
            error = _safe_unlink_owned_at(parent_fd, path, identity)
            if error is not None:
                rollback_errors.append(error)
    finally:
        cleanup_errors = _cleanup_private_stage(stage, stage_identity, stage_owned)
        if cleanup_errors and primary_error is None:
            for path, identity in reversed(published):
                error = _safe_unlink_owned_at(parent_fd, path, identity)
                if error is not None:
                    rollback_errors.append(error)
        for descriptor in reversed(published_descriptors):
            try:
                os.close(descriptor)
            except OSError as exc:
                descriptor_close_errors.append(str(exc))
    if primary_error is not None:
        if rollback_errors or cleanup_errors or descriptor_close_errors:
            raise BuilderInputError(
                "publication failed and cleanup is incomplete/poisoned: {}{}{}; original error: {}".format(
                    "publication rollback: {}; ".format("; ".join(rollback_errors))
                    if rollback_errors
                    else "",
                    "private staging cleanup: {}; ".format("; ".join(cleanup_errors))
                    if cleanup_errors
                    else "",
                    "publication descriptor close: {}".format(
                        "; ".join(descriptor_close_errors)
                    )
                    if descriptor_close_errors
                    else "",
                    primary_error,
                )
            ) from primary_error
        raise primary_error
    if cleanup_errors or descriptor_close_errors:
        raise BuilderInputError(
            "publication cleanup is incomplete/poisoned: {}{}{}".format(
                "private staging: {}; ".format("; ".join(cleanup_errors))
                if cleanup_errors
                else "",
                "descriptor close: {}; ".format("; ".join(descriptor_close_errors))
                if descriptor_close_errors
                else "",
                "; publication rollback incomplete/poisoned: {}".format(
                    "; ".join(rollback_errors)
                )
                if rollback_errors
                else "",
            )
        )


def _verify_source_inventory_from_frozen_helper(
    *,
    source_archive_payload: bytes,
    source_inventory_payload: bytes,
    source_helper_payload: bytes,
    source_archive_name: str,
    source_inventory_name: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="taiji-source-integrity-") as temporary:
        root = Path(temporary)
        archive = root / source_archive_name
        inventory = root / source_inventory_name
        _write_exclusive_impl(archive, source_archive_payload, 0o600)
        _write_exclusive_impl(inventory, source_inventory_payload, 0o600)
        module = types.ModuleType("taiji_frozen_source_archive_integrity")
        module.__file__ = "<frozen-source-archive-integrity.py>"
        try:
            code = compile(source_helper_payload, module.__file__, "exec")
            exec(code, module.__dict__)
            verifier = module.__dict__.get("verify")
            if not callable(verifier):
                raise BuilderInputError("frozen source inventory verifier is missing")
            verifier(archive, inventory, None, [])
        except BuilderInputError:
            raise
        except BaseException as exc:
            raise BuilderInputError(
                "source inventory is not archive-derived under the frozen source helper: {}".format(
                    exc
                )
            ) from exc


def _parse_source_checksums(
    payload: bytes,
    source_archive_name: str,
    source_inventory_name: str,
) -> Dict[str, str]:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise BuilderInputError("SHA256SUMS.txt must be ASCII") from exc
    if len(lines) != 2:
        raise BuilderInputError("SHA256SUMS.txt must contain exactly two entries")
    result = {}  # type: Dict[str, str]
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)", line)
        if match is None or match.group(2) in result:
            raise BuilderInputError("SHA256SUMS.txt contains an invalid entry")
        result[match.group(2)] = match.group(1)
    if set(result) != {source_archive_name, source_inventory_name}:
        raise BuilderInputError("SHA256SUMS.txt does not name the exact source inputs")
    return result


def _tar_payload(root_basename: str, members: Sequence[Dict[str, Any]]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        with tarfile.open(
            fileobj=compressed,
            mode="w|",
            format=tarfile.USTAR_FORMAT,
        ) as archive:
            for member in members:
                info = tarfile.TarInfo(
                    name=str(Path(root_basename) / str(member["basename"]))
                )
                payload = member["payload"]
                info.size = len(payload)
                info.mode = int(member["mode"])
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                info.type = tarfile.REGTYPE
                archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def _load_canonical_manifest(payload: bytes) -> Dict[str, Any]:
    def reject_duplicates(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result = {}  # type: Dict[str, Any]
        for key, value in pairs:
            if key in result:
                raise BuilderInputError("builder input manifest contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuilderInputError("builder input manifest is invalid JSON") from exc
    if type(value) is not dict or set(value) != MANIFEST_KEYS:
        raise BuilderInputError("builder input manifest fields are invalid")
    canonical = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if payload != canonical:
        raise BuilderInputError("builder input manifest is not canonical JSON")
    return value


def _parse_delivery_checksums(
    payload: bytes,
    archive_basename: str,
    manifest_basename: str,
) -> Dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BuilderInputError("builder input checksum must be UTF-8") from exc
    if len(lines) != 2:
        raise BuilderInputError("builder input checksum must contain exactly two entries")
    result = {}  # type: Dict[str, str]
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)", line)
        if match is None or match.group(2) in result:
            raise BuilderInputError("builder input checksum contains an invalid entry")
        result[match.group(2)] = match.group(1)
    if set(result) != {archive_basename, manifest_basename}:
        raise BuilderInputError("builder input checksum names are invalid")
    return result


def _validated_public_members(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_members = manifest.get("members")
    if type(raw_members) is not list or not raw_members:
        raise BuilderInputError("builder input manifest members are invalid")
    validated = []  # type: List[Dict[str, Any]]
    seen = set()
    for item in raw_members:
        if type(item) is not dict or set(item) != MEMBER_KEYS:
            raise BuilderInputError("builder input member fields are invalid")
        basename = item.get("basename")
        mode = item.get("mode")
        digest = item.get("sha256")
        size = item.get("size")
        if (
            type(basename) is not str
            or not basename
            or "/" in basename
            or "\\" in basename
            or basename in seen
            or type(mode) is not str
            or re.fullmatch(r"0[0-7]{3}", mode) is None
            or type(digest) is not str
            or SHA256_RE.fullmatch(digest) is None
            or type(size) is not int
            or type(size) is bool
            or size < 0
            or size > MAX_INPUT_FILE_BYTES
        ):
            raise BuilderInputError("builder input member identity is invalid")
        seen.add(basename)
        validated.append(item)
    if [item["basename"] for item in validated] != sorted(seen):
        raise BuilderInputError("builder input members are not canonically ordered")
    return validated


def _inspect_archive_members(
    archive_payload: bytes,
    root_basename: str,
    expected_members: Sequence[Dict[str, Any]],
) -> None:
    expected = {str(item["basename"]): item for item in expected_members}
    seen = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or len(path.parts) != 2
                    or path.parts[0] != root_basename
                    or path.parts[1] not in expected
                    or path.parts[1] in seen
                    or not member.isfile()
                    or member.islnk()
                    or member.issym()
                ):
                    raise BuilderInputError("builder input archive member is invalid")
                basename = path.parts[1]
                record = expected[basename]
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise BuilderInputError("builder input archive member cannot be read")
                payload = extracted.read(member.size + 1)
                if len(payload) != member.size:
                    raise BuilderInputError("builder input archive member size is invalid")
                if (
                    member.size != record["size"]
                    or format(member.mode & 0o777, "04o") != record["mode"]
                    or _sha256(payload) != record["sha256"]
                ):
                    raise BuilderInputError("builder input archive member differs from manifest")
                seen.add(basename)
    except (OSError, tarfile.TarError) as exc:
        raise BuilderInputError("builder input archive cannot be inspected") from exc
    if seen != set(expected):
        raise BuilderInputError("builder input archive member set is incomplete")


def _verify_extracted_members(
    extracted_dir: Path,
    expected_members: Sequence[Dict[str, Any]],
) -> None:
    try:
        metadata = extracted_dir.lstat()
    except OSError as exc:
        raise BuilderInputError("extracted builder input directory is missing") from exc
    if (
        extracted_dir.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuilderInputError("extracted builder input directory is unsafe")
    expected = {str(item["basename"]): item for item in expected_members}
    try:
        actual_names = {path.name for path in extracted_dir.iterdir()}
    except OSError as exc:
        raise BuilderInputError("extracted builder input directory cannot be listed") from exc
    if actual_names != set(expected):
        raise BuilderInputError("extracted builder input member set is invalid")
    for basename, record in expected.items():
        payload, mode = _read_regular(
            extracted_dir / basename,
            "extracted builder input {}".format(basename),
        )
        if (
            len(payload) != record["size"]
            or format(mode, "04o") != record["mode"]
            or _sha256(payload) != record["sha256"]
        ):
            raise BuilderInputError("extracted builder input member differs from manifest")


def _read_frozen_tracked_members(source_archive_payload: bytes) -> Dict[str, Tuple[bytes, int]]:
    expected_paths = {
        name: "taiji-agentv1.0/{}/{}".format(INPUT_ROOT_BASENAME, name)
        for name in STATIC_INPUT_NAMES
        if name != "SHA256SUMS.txt"
    }
    expected_paths[SOURCE_HELPER_BASENAME] = (
        "taiji-agentv1.0/packaging/linux/source-archive-integrity.py"
    )
    expected_paths[BUILDER_HELPER_BASENAME] = (
        "taiji-agentv1.0/packaging/linux/builder-input-package.py"
    )
    by_archive_path = {path: basename for basename, path in expected_paths.items()}
    result = {}  # type: Dict[str, Tuple[bytes, int]]
    try:
        with tarfile.open(fileobj=io.BytesIO(source_archive_payload), mode="r:gz") as archive:
            for member in archive:
                basename = by_archive_path.get(member.name)
                if basename is None:
                    continue
                if (
                    basename in result
                    or not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or member.size < 0
                    or member.size > MAX_INPUT_FILE_BYTES
                    or stat.S_IMODE(member.mode) not in (0o644, 0o755)
                ):
                    raise BuilderInputError("frozen source archive member is invalid")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise BuilderInputError("frozen source archive member cannot be read")
                payload = extracted.read(member.size + 1)
                if len(payload) != member.size:
                    raise BuilderInputError("frozen source archive member size is invalid")
                result[basename] = (payload, stat.S_IMODE(member.mode))
    except (OSError, tarfile.TarError) as exc:
        raise BuilderInputError("frozen source archive cannot be inspected") from exc
    if set(result) != set(expected_paths):
        raise BuilderInputError("frozen source archive lacks formal builder inputs")
    return result


def verify_builder_input(
    *,
    archive_path: Path,
    manifest_path: Path,
    checksum_path: Path,
    extracted_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    archive_payload, _archive_mode = _read_regular(archive_path, "builder input archive")
    manifest_payload, _manifest_mode = _read_regular(manifest_path, "builder input manifest")
    checksum_payload, _checksum_mode = _read_regular(checksum_path, "builder input checksum")
    checksums = _parse_delivery_checksums(
        checksum_payload,
        archive_path.name,
        manifest_path.name,
    )
    if (
        checksums[archive_path.name] != _sha256(archive_payload)
        or checksums[manifest_path.name] != _sha256(manifest_payload)
    ):
        raise BuilderInputError("builder input checksum does not match the supplied files")
    expected_checksum_payload = (
        "{}  {}\n{}  {}\n".format(
            _sha256(archive_payload),
            archive_path.name,
            _sha256(manifest_payload),
            manifest_path.name,
        )
    ).encode("utf-8")
    if checksum_payload != expected_checksum_payload:
        raise BuilderInputError("builder input checksum is not canonical")
    manifest = _load_canonical_manifest(manifest_payload)
    if (
        manifest["schema"] != SCHEMA
        or type(manifest["source_commit"]) is not str
        or COMMIT_RE.fullmatch(manifest["source_commit"]) is None
        or manifest["archive_basename"] != archive_path.name
        or manifest["manifest_basename"] != manifest_path.name
        or manifest["checksum_basename"] != checksum_path.name
        or manifest["archive_root_basename"] != INPUT_ROOT_BASENAME
        or manifest["archive_size"] != len(archive_payload)
        or manifest["archive_sha256"] != _sha256(archive_payload)
    ):
        raise BuilderInputError("builder input manifest does not bind the supplied files")
    source_commit = manifest["source_commit"]
    expected_archive_name = "taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(
        source_commit
    )
    expected_inventory_name = expected_archive_name[:-7] + ".inventory.json"
    expected_input_name = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    if (
        archive_path.name != expected_input_name
        or manifest_path.name != "taijiagent-制包机输入-{}.manifest.json".format(source_commit)
        or checksum_path.name != expected_input_name + ".sha256"
        or manifest["source_archive_basename"] != expected_archive_name
        or manifest["source_inventory_basename"] != expected_inventory_name
    ):
        raise BuilderInputError("builder input filenames do not bind the source commit")
    members = _validated_public_members(manifest)
    expected_names = set(STATIC_INPUT_NAMES) | {
        expected_archive_name,
        expected_inventory_name,
        SOURCE_HELPER_BASENAME,
        BUILDER_HELPER_BASENAME,
    }
    if {item["basename"] for item in members} != expected_names:
        raise BuilderInputError("builder input manifest member set is invalid")
    by_name = {str(item["basename"]): item for item in members}
    if (
        manifest["source_archive_sha256"] != by_name[expected_archive_name]["sha256"]
        or manifest["source_inventory_sha256"] != by_name[expected_inventory_name]["sha256"]
        or manifest["source_integrity_helper_sha256"]
        != by_name[SOURCE_HELPER_BASENAME]["sha256"]
        or manifest["builder_input_helper_sha256"]
        != by_name[BUILDER_HELPER_BASENAME]["sha256"]
    ):
        raise BuilderInputError("builder input manifest member binding is invalid")
    _inspect_archive_members(
        archive_payload,
        INPUT_ROOT_BASENAME,
        members,
    )
    if extracted_dir is not None:
        _verify_extracted_members(extracted_dir, members)
    return manifest


def create_builder_input(
    *,
    source_dir: Path,
    source_integrity_helper: Path,
    output: Path,
    manifest_path: Path,
    checksum_path: Path,
    source_commit: str,
) -> Dict[str, Any]:
    if not COMMIT_RE.fullmatch(source_commit):
        raise BuilderInputError("source commit must be a full lowercase Git SHA")
    try:
        requested_source_metadata = source_dir.lstat()
    except OSError as exc:
        raise BuilderInputError("builder input source directory is unsafe") from exc
    if (
        source_dir.is_symlink()
        or not stat.S_ISDIR(requested_source_metadata.st_mode)
        or requested_source_metadata.st_uid != os.getuid()
        or stat.S_IMODE(requested_source_metadata.st_mode) & 0o022
    ):
        raise BuilderInputError("builder input source directory is unsafe")
    source_dir = source_dir.resolve(strict=True)
    if source_dir.name != INPUT_ROOT_BASENAME:
        raise BuilderInputError("builder input source directory basename is invalid")
    expected_archive_name = "taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(
        source_commit
    )
    expected_inventory_name = expected_archive_name[:-7] + ".inventory.json"
    expected_output_name = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    if output.name != expected_output_name:
        raise BuilderInputError("builder input archive basename does not match source commit")
    if manifest_path.name != "taijiagent-制包机输入-{}.manifest.json".format(source_commit):
        raise BuilderInputError("builder input manifest basename does not match source commit")
    if checksum_path.name != expected_output_name + ".sha256":
        raise BuilderInputError("builder input checksum basename is invalid")

    source_archive_path = source_dir / expected_archive_name
    source_inventory_path = source_dir / expected_inventory_name
    source_checksum_path = source_dir / "SHA256SUMS.txt"
    source_archive_payload, source_archive_mode = _read_regular(
        source_archive_path,
        "builder input {}".format(expected_archive_name),
    )
    source_inventory_payload, source_inventory_mode = _read_regular(
        source_inventory_path,
        "builder input {}".format(expected_inventory_name),
    )
    source_checksum_payload, source_checksum_mode = _read_regular(
        source_checksum_path,
        "builder input SHA256SUMS.txt",
    )
    source_checksums = _parse_source_checksums(
        source_checksum_payload,
        expected_archive_name,
        expected_inventory_name,
    )
    if (
        source_checksums[expected_archive_name] != _sha256(source_archive_payload)
        or source_checksums[expected_inventory_name] != _sha256(source_inventory_payload)
    ):
        raise BuilderInputError("source checksum does not match frozen source inputs")
    frozen = _read_frozen_tracked_members(source_archive_payload)

    members = []  # type: List[Dict[str, Any]]
    for name in STATIC_INPUT_NAMES:
        if name == "SHA256SUMS.txt":
            payload, mode = source_checksum_payload, source_checksum_mode
        else:
            current_payload, current_mode = _read_regular(
                source_dir / name,
                "builder input {}".format(name),
            )
            payload, mode = frozen[name]
            if current_payload != payload or current_mode != mode:
                raise BuilderInputError(
                    "builder input {} differs from frozen source commit".format(name)
                )
        members.append(
            {
                "basename": name,
                "payload": payload,
                "mode": mode,
                "size": len(payload),
                "sha256": _sha256(payload),
            }
        )
    members.extend(
        (
            {
                "basename": expected_archive_name,
                "payload": source_archive_payload,
                "mode": source_archive_mode,
                "size": len(source_archive_payload),
                "sha256": _sha256(source_archive_payload),
            },
            {
                "basename": expected_inventory_name,
                "payload": source_inventory_payload,
                "mode": source_inventory_mode,
                "size": len(source_inventory_payload),
                "sha256": _sha256(source_inventory_payload),
            },
        )
    )
    current_helper_payload, current_helper_mode = _read_regular(
        source_integrity_helper,
        "source archive integrity helper",
    )
    helper_payload, helper_mode = frozen[SOURCE_HELPER_BASENAME]
    if current_helper_payload != helper_payload or current_helper_mode != helper_mode:
        raise BuilderInputError("source archive integrity helper differs from frozen source commit")
    _verify_source_inventory_from_frozen_helper(
        source_archive_payload=source_archive_payload,
        source_inventory_payload=source_inventory_payload,
        source_helper_payload=helper_payload,
        source_archive_name=expected_archive_name,
        source_inventory_name=expected_inventory_name,
    )
    members.append(
        {
            "basename": SOURCE_HELPER_BASENAME,
            "payload": helper_payload,
            "mode": helper_mode,
            "size": len(helper_payload),
            "sha256": _sha256(helper_payload),
        }
    )
    current_builder_helper_payload, current_builder_helper_mode = _read_regular(
        Path(__file__),
        "builder input helper",
    )
    builder_helper_payload, builder_helper_mode = frozen[BUILDER_HELPER_BASENAME]
    if (
        current_builder_helper_payload != builder_helper_payload
        or current_builder_helper_mode != builder_helper_mode
    ):
        raise BuilderInputError("builder input helper differs from frozen source commit")
    members.append(
        {
            "basename": BUILDER_HELPER_BASENAME,
            "payload": builder_helper_payload,
            "mode": builder_helper_mode,
            "size": len(builder_helper_payload),
            "sha256": _sha256(builder_helper_payload),
        }
    )
    if len({str(item["basename"]) for item in members}) != len(members):
        raise BuilderInputError("builder input allowlist contains duplicate basenames")
    members.sort(key=lambda item: str(item["basename"]))

    by_name = {str(item["basename"]): item for item in members}

    archive_payload = _tar_payload(source_dir.name, members)
    archive_sha256 = _sha256(archive_payload)
    public_members = [
        {
            "basename": item["basename"],
            "mode": format(int(item["mode"]), "04o"),
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in members
    ]
    manifest = {
        "schema": SCHEMA,
        "source_commit": source_commit,
        "archive_basename": output.name,
        "archive_size": len(archive_payload),
        "archive_sha256": archive_sha256,
        "source_archive_basename": expected_archive_name,
        "source_archive_sha256": by_name[expected_archive_name]["sha256"],
        "source_inventory_basename": expected_inventory_name,
        "source_inventory_sha256": by_name[expected_inventory_name]["sha256"],
        "source_integrity_helper_sha256": _sha256(helper_payload),
        "builder_input_helper_sha256": _sha256(builder_helper_payload),
        "archive_root_basename": INPUT_ROOT_BASENAME,
        "manifest_basename": manifest_path.name,
        "checksum_basename": checksum_path.name,
        "members": public_members,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    checksum_payload = (
        "{}  {}\n{}  {}\n".format(
            archive_sha256,
            output.name,
            _sha256(manifest_payload),
            manifest_path.name,
        )
    ).encode("utf-8")

    _publish_triplet(
        archive_payload=archive_payload,
        manifest_payload=manifest_payload,
        checksum_payload=checksum_payload,
        output=output,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
    )
    return manifest


def publish_builder_input(
    *,
    archive_path: Path,
    manifest_path: Path,
    checksum_path: Path,
    output: Path,
    output_manifest: Path,
    output_checksum: Path,
) -> Dict[str, Any]:
    manifest = verify_builder_input(
        archive_path=archive_path,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
    )
    if (
        output.name != archive_path.name
        or output_manifest.name != manifest_path.name
        or output_checksum.name != checksum_path.name
    ):
        raise BuilderInputError("publication basenames must preserve the verified triplet identity")
    archive_payload, _archive_mode = _read_regular(archive_path, "staged builder input archive")
    manifest_payload, _manifest_mode = _read_regular(manifest_path, "staged builder input manifest")
    checksum_payload, _checksum_mode = _read_regular(checksum_path, "staged builder input checksum")
    _publish_triplet(
        archive_payload=archive_payload,
        manifest_payload=manifest_payload,
        checksum_payload=checksum_payload,
        output=output,
        manifest_path=output_manifest,
        checksum_path=output_checksum,
    )
    return manifest


def withdraw_builder_input(
    *,
    archive_path: Path,
    manifest_path: Path,
    checksum_path: Path,
) -> Dict[str, Any]:
    manifest = verify_builder_input(
        archive_path=archive_path,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
    )
    owned = []  # type: List[Tuple[Path, FileIdentity]]
    for path in (archive_path, manifest_path, checksum_path):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise BuilderInputError("withdrawal path is no longer a regular file: {}".format(path))
        owned.append((path, _file_identity(metadata)))
    errors = []  # type: List[str]
    for path, identity in reversed(owned):
        error = _safe_unlink_owned(path, identity)
        if error is not None:
            errors.append(error)
    if errors:
        raise BuilderInputError(
            "triplet withdrawal is incomplete/poisoned: {}".format("; ".join(errors))
        )
    return manifest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", allow_abbrev=False)
    create.add_argument("--source-dir", required=True, type=Path)
    create.add_argument("--source-integrity-helper", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--manifest", required=True, type=Path)
    create.add_argument("--checksum", required=True, type=Path)
    create.add_argument("--source-commit", required=True)
    verify = subparsers.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--archive", required=True, type=Path)
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--checksum", required=True, type=Path)
    verify.add_argument("--extracted-dir", type=Path)
    publish = subparsers.add_parser("publish", allow_abbrev=False)
    publish.add_argument("--archive", required=True, type=Path)
    publish.add_argument("--manifest", required=True, type=Path)
    publish.add_argument("--checksum", required=True, type=Path)
    publish.add_argument("--output", required=True, type=Path)
    publish.add_argument("--output-manifest", required=True, type=Path)
    publish.add_argument("--output-checksum", required=True, type=Path)
    withdraw = subparsers.add_parser("withdraw", allow_abbrev=False)
    withdraw.add_argument("--archive", required=True, type=Path)
    withdraw.add_argument("--manifest", required=True, type=Path)
    withdraw.add_argument("--checksum", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_builder_input(
                source_dir=args.source_dir,
                source_integrity_helper=args.source_integrity_helper,
                output=args.output,
                manifest_path=args.manifest,
                checksum_path=args.checksum,
                source_commit=args.source_commit,
            )
            action = "created"
        elif args.command == "verify":
            manifest = verify_builder_input(
                archive_path=args.archive,
                manifest_path=args.manifest,
                checksum_path=args.checksum,
                extracted_dir=args.extracted_dir,
            )
            action = "verified"
        elif args.command == "publish":
            manifest = publish_builder_input(
                archive_path=args.archive,
                manifest_path=args.manifest,
                checksum_path=args.checksum,
                output=args.output,
                output_manifest=args.output_manifest,
                output_checksum=args.output_checksum,
            )
            action = "published"
        else:
            manifest = withdraw_builder_input(
                archive_path=args.archive,
                manifest_path=args.manifest,
                checksum_path=args.checksum,
            )
            action = "withdrawn"
    except BuilderInputError as exc:
        print("builder-input-package-failed\t{}".format(exc), file=sys.stderr)
        return 1
    print(
        "builder-input-package-{}\t{}\t{}".format(
            action, manifest["archive_basename"], manifest["archive_sha256"]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
