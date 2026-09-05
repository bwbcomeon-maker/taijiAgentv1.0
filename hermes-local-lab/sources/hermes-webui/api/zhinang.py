"""Read-only, checksum-bound source catalog for Taiji Zhinang roles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping


AGENCY_AGENTS_COMMIT = "af128a92888fd7d7c389b6cb37f1820be1b3cd9d"
AGENCY_AGENTS_REPOSITORY = "https://github.com/msitarzewski/agency-agents"
CATALOG_VERSION = "agency-agents-af128a92888f-source-v1"
DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "zhinang"

_MANIFEST_NAME = "source-manifest.json"
_MAX_CONTROL_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 1024 * 1024
_MAX_ROLE_COUNT = 2000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FRONTMATTER_FIELD_RE = {
    field: re.compile(rf"^{field}\s*:\s*\S", re.MULTILINE)
    for field in ("name", "description", "color")
}


class CatalogResourceError(RuntimeError):
    """A safe, user-readable failure for damaged or unavailable catalog data."""

    _MESSAGES = {
        "catalog_missing": "智囊目录资源缺失，请重试或联系维护人员。",
        "manifest_invalid": "智囊目录清单无效，请重试或联系维护人员。",
        "catalog_version_mismatch": "智囊目录版本不匹配，请刷新后重试。",
        "source_missing": "智囊角色资源缺失，请重试或联系维护人员。",
        "source_invalid": "智囊角色资源格式无效，请重试或联系维护人员。",
        "source_digest_mismatch": "智囊角色资源校验失败，请重试或联系维护人员。",
        "control_digest_mismatch": "智囊目录控制资源校验失败，请重试或联系维护人员。",
        "source_inventory_mismatch": "智囊角色资源清单不完整，请重试或联系维护人员。",
        "role_not_found": "未找到指定智囊角色。",
    }

    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(self._MESSAGES.get(code, self._MESSAGES["manifest_invalid"]))


@dataclass(frozen=True)
class SourceRoleRecord:
    role_id: str
    division: str
    source_path: str
    source_bytes: int
    source_sha256: str


@dataclass(frozen=True)
class SourceRole:
    role_id: str
    division: str
    source_path: str
    source_bytes: int
    source_sha256: str
    effective_prompt_sha256: str
    raw_source: str


@dataclass(frozen=True)
class SourceCatalogSnapshot:
    catalog_version: str
    upstream_commit: str
    role_count: int
    roles: Mapping[str, SourceRoleRecord]


def _safe_relative_path(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or len(candidate.parts) < 2
        or any(part in ("", ".", "..") for part in candidate.parts)
        or candidate.suffix != ".md"
    ):
        return None
    return candidate


def _read_regular(path: Path, *, maximum: int, missing_code: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise CatalogResourceError(missing_code, detail=type(error).__name__) from error
    if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise CatalogResourceError(missing_code, detail="not_regular")
    if before.st_size > maximum:
        raise CatalogResourceError("source_invalid", detail="size_limit")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != before.st_size:
            raise CatalogResourceError("source_invalid", detail="changed_before_read")
        chunks = bytearray()
        while len(chunks) <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        after = os.fstat(descriptor)
        if len(chunks) > maximum:
            raise CatalogResourceError("source_invalid", detail="size_limit")
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise CatalogResourceError("source_invalid", detail="changed_during_read")
        return bytes(chunks)
    except CatalogResourceError:
        raise
    except OSError as error:
        raise CatalogResourceError(missing_code, detail=type(error).__name__) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_json_object(payload: bytes) -> dict:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogResourceError("manifest_invalid", detail=type(error).__name__) from error
    if not isinstance(value, dict):
        raise CatalogResourceError("manifest_invalid", detail="root_not_object")
    return value


def _validate_frontmatter(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CatalogResourceError("source_invalid", detail="not_utf8") from error
    if "\r" in text:
        raise CatalogResourceError("source_invalid", detail="crlf")
    if not text.startswith("---\n"):
        raise CatalogResourceError("source_invalid", detail="frontmatter_missing")
    boundary = text.find("\n---\n", 4)
    if boundary < 0:
        raise CatalogResourceError("source_invalid", detail="frontmatter_unclosed")
    frontmatter = text[4:boundary]
    if any(not pattern.search(frontmatter) for pattern in _FRONTMATTER_FIELD_RE.values()):
        raise CatalogResourceError("source_invalid", detail="frontmatter_required_field")


def _validate_control_digest(manifest: dict, prefix: str, payload: bytes) -> None:
    expected_bytes = manifest.get(f"{prefix}_bytes")
    expected_digest = manifest.get(f"{prefix}_sha256")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 1
        or expected_bytes > _MAX_CONTROL_BYTES
        or not isinstance(expected_digest, str)
        or _SHA256_RE.fullmatch(expected_digest) is None
    ):
        raise CatalogResourceError("manifest_invalid", detail=f"{prefix}_record")
    if len(payload) != expected_bytes:
        raise CatalogResourceError("control_digest_mismatch", detail=f"{prefix}_bytes")
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise CatalogResourceError("control_digest_mismatch", detail=f"{prefix}_sha256")


class ZhinangSourceCatalog:
    """Load only files named by a fixed, checksum-bound built-in manifest."""

    def __init__(
        self,
        data_root: str | os.PathLike[str] | Path = DATA_ROOT,
        *,
        expected_commit: str = AGENCY_AGENTS_COMMIT,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.expected_commit = expected_commit
        self._snapshot: SourceCatalogSnapshot | None = None

    def _manifest(self) -> dict:
        payload = _read_regular(
            self.data_root / _MANIFEST_NAME,
            maximum=_MAX_CONTROL_BYTES,
            missing_code="catalog_missing",
        )
        return _parse_json_object(payload)

    def _control_path(self, value: object) -> Path:
        if not isinstance(value, str) or not value or "/" in value or "\\" in value:
            raise CatalogResourceError("manifest_invalid", detail="unsafe_control_path")
        path = self.data_root / value
        try:
            path.resolve().relative_to(self.data_root)
        except (OSError, ValueError) as error:
            raise CatalogResourceError("manifest_invalid", detail="unsafe_control_path") from error
        return path

    def validate(self) -> SourceCatalogSnapshot:
        manifest = self._manifest()
        if manifest.get("schema_version") != 1:
            raise CatalogResourceError("manifest_invalid", detail="schema_version")
        if manifest.get("catalog_version") != CATALOG_VERSION:
            raise CatalogResourceError("catalog_version_mismatch", detail="catalog_version")
        if manifest.get("upstream_repository") != AGENCY_AGENTS_REPOSITORY:
            raise CatalogResourceError("manifest_invalid", detail="repository")
        if manifest.get("upstream_commit") != self.expected_commit:
            raise CatalogResourceError("catalog_version_mismatch", detail="upstream_commit")

        divisions_path = self._control_path(manifest.get("divisions_path"))
        license_path = self._control_path(manifest.get("license_path"))
        divisions_payload = _read_regular(
            divisions_path,
            maximum=_MAX_CONTROL_BYTES,
            missing_code="catalog_missing",
        )
        _validate_control_digest(manifest, "divisions", divisions_payload)
        divisions_document = _parse_json_object(divisions_payload)
        divisions = divisions_document.get("divisions")
        if not isinstance(divisions, dict) or not divisions:
            raise CatalogResourceError("manifest_invalid", detail="divisions")
        license_payload = _read_regular(
            license_path,
            maximum=_MAX_CONTROL_BYTES,
            missing_code="catalog_missing",
        )
        _validate_control_digest(manifest, "license", license_payload)

        source_root_value = manifest.get("source_root")
        if source_root_value != "upstream/agency-agents":
            raise CatalogResourceError("manifest_invalid", detail="source_root")
        source_root = self.data_root / "upstream" / "agency-agents"
        try:
            source_root.resolve().relative_to(self.data_root)
        except (OSError, ValueError) as error:
            raise CatalogResourceError("manifest_invalid", detail="source_root") from error

        roles_value = manifest.get("roles")
        role_count = manifest.get("role_count")
        if (
            not isinstance(roles_value, list)
            or not isinstance(role_count, int)
            or isinstance(role_count, bool)
            or role_count < 1
            or role_count > _MAX_ROLE_COUNT
            or len(roles_value) != role_count
        ):
            raise CatalogResourceError("manifest_invalid", detail="role_count")

        records: dict[str, SourceRoleRecord] = {}
        expected_paths: set[str] = set()
        for item in roles_value:
            if not isinstance(item, dict):
                raise CatalogResourceError("manifest_invalid", detail="role_not_object")
            relative = _safe_relative_path(item.get("source_path"))
            if relative is None:
                raise CatalogResourceError("manifest_invalid", detail="source_path")
            source_path = relative.as_posix()
            division = item.get("division")
            role_id = item.get("role_id")
            digest = item.get("source_sha256")
            source_bytes = item.get("source_bytes")
            expected_role_id = f"agency:{source_path.removesuffix('.md')}"
            if (
                not isinstance(division, str)
                or division not in divisions
                or division != relative.parts[0]
                or role_id != expected_role_id
                or not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None
                or not isinstance(source_bytes, int)
                or isinstance(source_bytes, bool)
                or source_bytes < 1
                or source_bytes > _MAX_SOURCE_BYTES
                or role_id in records
                or source_path in expected_paths
            ):
                raise CatalogResourceError("manifest_invalid", detail="role_record")
            record = SourceRoleRecord(
                role_id=role_id,
                division=division,
                source_path=source_path,
                source_bytes=source_bytes,
                source_sha256=digest,
            )
            self._read_record(source_root, record)
            records[role_id] = record
            expected_paths.add(source_path)

        actual_paths: set[str] = set()
        try:
            candidates = source_root.rglob("*.md")
            for candidate in candidates:
                if candidate.is_symlink() or not candidate.is_file():
                    raise CatalogResourceError("source_inventory_mismatch", detail="non_regular")
                relative = candidate.relative_to(source_root).as_posix()
                actual_paths.add(relative)
        except CatalogResourceError:
            raise
        except (OSError, ValueError) as error:
            raise CatalogResourceError(
                "source_inventory_mismatch", detail=type(error).__name__
            ) from error
        if actual_paths != expected_paths:
            raise CatalogResourceError("source_inventory_mismatch", detail="path_set")

        snapshot = SourceCatalogSnapshot(
            catalog_version=CATALOG_VERSION,
            upstream_commit=self.expected_commit,
            role_count=role_count,
            roles=MappingProxyType(records.copy()),
        )
        self._snapshot = snapshot
        return snapshot

    def _read_record(self, source_root: Path, record: SourceRoleRecord) -> bytes:
        candidate = source_root.joinpath(*PurePosixPath(record.source_path).parts)
        try:
            candidate.resolve().relative_to(source_root.resolve())
        except (OSError, ValueError) as error:
            raise CatalogResourceError("manifest_invalid", detail="source_escape") from error
        payload = _read_regular(
            candidate,
            maximum=_MAX_SOURCE_BYTES,
            missing_code="source_missing",
        )
        if len(payload) != record.source_bytes:
            raise CatalogResourceError("source_digest_mismatch", detail="source_bytes")
        if hashlib.sha256(payload).hexdigest() != record.source_sha256:
            raise CatalogResourceError("source_digest_mismatch", detail="sha256")
        _validate_frontmatter(payload)
        return payload

    def read_role(self, role_id: str) -> SourceRole:
        snapshot = self._snapshot or self.validate()
        record = snapshot.roles.get(role_id)
        if record is None:
            raise CatalogResourceError("role_not_found", detail="unknown_role_id")
        source_root = self.data_root / "upstream" / "agency-agents"
        payload = self._read_record(source_root, record)
        try:
            raw_source = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CatalogResourceError("source_invalid", detail="not_utf8") from error
        return SourceRole(
            role_id=record.role_id,
            division=record.division,
            source_path=record.source_path,
            source_bytes=record.source_bytes,
            source_sha256=record.source_sha256,
            effective_prompt_sha256=record.source_sha256,
            raw_source=raw_source,
        )
