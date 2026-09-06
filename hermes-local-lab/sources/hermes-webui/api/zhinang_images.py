"""Fail-soft validation for locally bundled Zhinang role illustrations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct


IMAGE_SCHEMA = "taiji-zhinang-role-images/v1"
ALLOWED_STATES = {"draft", "review", "approved", "active"}
ASSET_PREFIX = "static/assets/zhinang/roles/"

_MAX_IMAGE_BYTES = 160 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def load_role_images(
    manifest_path: Path,
    catalog_version: str,
    *,
    include_review: bool = False,
) -> dict[str, str]:
    """Return trusted site-relative paths, omitting damaged image files."""
    if not _supports_dir_fd_safety():
        loaded = _load_manifest_fallback(Path(manifest_path), catalog_version)
        if loaded is None:
            return {}
        manifest, root = loaded
        permitted_states = {"active"}
        if include_review:
            permitted_states.update({"review", "approved"})
        return {
            record["role_id"]: record["path"]
            for record in manifest
            if record["state"] in permitted_states and _valid_image_fallback(root, record)
        }

    loaded = _load_manifest(Path(manifest_path), catalog_version)
    if loaded is None:
        return {}
    manifest, root_fd = loaded
    permitted_states = {"active"}
    if include_review:
        permitted_states.update({"review", "approved"})

    images: dict[str, str] = {}
    try:
        for record in manifest:
            if record["state"] not in permitted_states:
                continue
            if _valid_image(root_fd, record):
                images[record["role_id"]] = record["path"]
        return images
    finally:
        os.close(root_fd)


def _load_manifest(manifest_path: Path, catalog_version: str) -> tuple[list[dict], int] | None:
    root = _manifest_root(manifest_path)
    if root is None:
        return None
    root_fd = _open_directory(root)
    if root_fd is None:
        return None
    keep_root = False
    try:
        manifest_fd = _open_directory_chain(root_fd, ("static", "assets", "zhinang"))
        if manifest_fd is None:
            return None
        try:
            payload = _read_regular_at(manifest_fd, "role-images.json", _MAX_MANIFEST_BYTES)
        finally:
            os.close(manifest_fd)
        if payload is None:
            return None
        records = _parse_manifest(payload, catalog_version)
        if records is None:
            return None
        keep_root = True
        return records, root_fd
    except (OSError, RecursionError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        if not keep_root:
            os.close(root_fd)


def _load_manifest_fallback(manifest_path: Path, catalog_version: str) -> tuple[list[dict], Path] | None:
    root = _manifest_root(manifest_path)
    if root is None:
        return None
    payload = _read_regular_path(manifest_path, _MAX_MANIFEST_BYTES, root)
    if payload is None:
        return None
    records = _parse_manifest(payload, catalog_version)
    return None if records is None else (records, root)


def _parse_manifest(payload: bytes, catalog_version: str) -> list[dict] | None:
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != IMAGE_SCHEMA
        or manifest.get("catalog_version") != catalog_version
        or not isinstance(manifest.get("images"), list)
    ):
        return None

    records: list[dict] = []
    role_ids: set[str] = set()
    paths: set[str] = set()
    for value in manifest["images"]:
        record = _validate_record(value)
        if record is None or record["role_id"] in role_ids or record["path"] in paths:
            return None
        role_ids.add(record["role_id"])
        paths.add(record["path"])
        records.append(record)
    return records


def _validate_record(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    role_id = value.get("role_id")
    path = _normalized_asset_path(value.get("path"))
    digest = value.get("sha256")
    state = value.get("state")
    byte_count = value.get("bytes")
    width = value.get("width")
    height = value.get("height")
    if (
        not isinstance(role_id, str)
        or not role_id
        or path is None
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
        or not isinstance(state, str)
        or state not in ALLOWED_STATES
        or not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or not 1 <= byte_count <= _MAX_IMAGE_BYTES
        or width != 512
        or height != 512
        or isinstance(width, bool)
        or isinstance(height, bool)
    ):
        return None
    return {
        "role_id": role_id,
        "path": path,
        "sha256": digest,
        "state": state,
        "bytes": byte_count,
    }


def _normalized_asset_path(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value.startswith(ASSET_PREFIX)
        or "\\" in value
        or "%" in value
        or "?" in value
        or "#" in value
    ):
        return None
    path = PurePosixPath(value)
    normalized = path.as_posix()
    if (
        path.is_absolute()
        or path.suffix != ".webp"
        or ".." in value.split("/")
        or not normalized.startswith(ASSET_PREFIX)
        or len(path.parts) <= len(PurePosixPath(ASSET_PREFIX).parts)
    ):
        return None
    return normalized


def _manifest_root(manifest_path: Path) -> Path | None:
    path = manifest_path if manifest_path.is_absolute() else Path.cwd() / manifest_path
    try:
        root = path.parents[3]
        expected_path = root / "static" / "assets" / "zhinang" / "role-images.json"
        if path != expected_path or _has_symlink_from_anchor(path):
            return None
        directories = (root, root / "static", root / "static" / "assets", root / "static" / "assets" / "zhinang")
        if not all(stat.S_ISDIR(candidate.lstat().st_mode) for candidate in directories):
            return None
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        return root
    except (OSError, IndexError, RuntimeError):
        return None


def _has_symlink_from_anchor(path: Path) -> bool:
    anchor = Path(path.anchor)
    current = anchor
    for part in path.parts[len(anchor.parts) :]:
        current /= part
        if stat.S_ISLNK(current.lstat().st_mode):
            return True
    return False


def _supports_dir_fd_safety() -> bool:
    return hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")


def _open_directory(path: Path) -> int | None:
    return _open_directory_at(None, str(path))


def _open_directory_chain(root_fd: int, parts: tuple[str, ...]) -> int | None:
    descriptor = -1
    try:
        descriptor = os.dup(root_fd)
        for part in parts:
            child = _open_directory_at(descriptor, part)
            os.close(descriptor)
            if child is None:
                return None
            descriptor = child
        return descriptor
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        return None


def _open_directory_at(directory_fd: int | None, path: str) -> int | None:
    if not _supports_dir_fd_safety():
        return None
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return None
        return descriptor
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        return None


def _valid_image(root_fd: int, record: dict) -> bool:
    relative = PurePosixPath(record["path"])
    prefix_parts = PurePosixPath(ASSET_PREFIX).parts
    path_parts = relative.parts[len(prefix_parts) :]
    if not path_parts:
        return False
    directory = _open_directory_chain(root_fd, ("static", "assets", "zhinang", "roles", *path_parts[:-1]))
    if directory is None:
        return False
    try:
        payload = _read_regular_at(directory, path_parts[-1], _MAX_IMAGE_BYTES)
    finally:
        os.close(directory)
    if payload is None or len(payload) != record["bytes"]:
        return False
    if hashlib.sha256(payload).hexdigest() != record["sha256"]:
        return False
    return _webp_dimensions(payload) == (512, 512)


def _read_regular_at(directory_fd: int, name: str, maximum: int) -> bytes | None:
    if not hasattr(os, "O_NOFOLLOW"):
        return None
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum:
            return None
        payload = _read_limited(descriptor, maximum)
        if payload is None:
            return None
        after = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None
        return payload
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_limited(descriptor: int, maximum: int) -> bytes | None:
    payload = bytearray()
    while len(payload) <= maximum:
        chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - len(payload)))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
    return None


def _valid_image_fallback(root: Path, record: dict) -> bool:
    candidate = root.joinpath(*PurePosixPath(record["path"]).parts)
    roles_root = root / "static" / "assets" / "zhinang" / "roles"
    payload = _read_regular_path(candidate, _MAX_IMAGE_BYTES, roles_root)
    if payload is None or len(payload) != record["bytes"]:
        return False
    if hashlib.sha256(payload).hexdigest() != record["sha256"]:
        return False
    return _webp_dimensions(payload) == (512, 512)


def _read_regular_path(path: Path, maximum: int, containment_root: Path) -> bytes | None:
    try:
        if _has_symlink_from_anchor(path):
            return None
        resolved_root = containment_root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            return None
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ):
                return None
            payload = _read_limited(stream.fileno(), maximum)
            after = os.fstat(stream.fileno())
        if payload is None or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None
        final = path.lstat()
        if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            return None
        if _has_symlink_from_anchor(path):
            return None
        path.resolve(strict=True).relative_to(resolved_root)
        return payload
    except (OSError, RuntimeError, ValueError):
        return None


def _webp_dimensions(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
        return None
    if struct.unpack_from("<I", payload, 4)[0] + 8 != len(payload):
        return None
    offset = 12
    dimensions = None
    while offset < len(payload):
        if offset + 8 > len(payload):
            return None
        chunk_type = payload[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", payload, offset + 4)[0]
        data_start = offset + 8
        data_end = data_start + chunk_size
        if data_end > len(payload):
            return None
        candidate = _chunk_dimensions(chunk_type, payload[data_start:data_end])
        if dimensions is None and candidate is not None:
            dimensions = candidate
        offset = data_end + (chunk_size % 2)
        if offset > len(payload):
            return None
    return dimensions if offset == len(payload) else None


def _chunk_dimensions(chunk_type: bytes, payload: bytes) -> tuple[int, int] | None:
    if chunk_type == b"VP8X" and len(payload) >= 10:
        return (
            int.from_bytes(payload[4:7], "little") + 1,
            int.from_bytes(payload[7:10], "little") + 1,
        )
    if chunk_type == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
        return (
            struct.unpack_from("<H", payload, 6)[0] & 0x3FFF,
            struct.unpack_from("<H", payload, 8)[0] & 0x3FFF,
        )
    if chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
        packed = int.from_bytes(payload[1:5], "little")
        if packed >> 29:
            return None
        return ((packed & 0x3FFF) + 1, ((packed >> 14) & 0x3FFF) + 1)
    return None
