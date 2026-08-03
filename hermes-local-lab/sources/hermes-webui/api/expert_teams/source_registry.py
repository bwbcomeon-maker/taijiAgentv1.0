"""Resolve opaque document sources inside a run's trusted workspace boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


_MAX_SOURCE_BYTES = 10 * 1024 * 1024
_ALLOWED_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}
_APPROVED_SOURCE_KINDS = {"approved_public", "approved_internal"}
_MATERIALIZED_SOURCE_SCHEMA = "expert-materialized-source/v1"


class SourceRegistryError(ValueError):
    def __init__(self, code: str, source_id: str, message: str):
        super().__init__(message)
        self.code = code
        self.source_id = source_id


def _safe_id(value: str) -> str:
    source_id = str(value or "").strip()
    if not source_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", source_id):
        raise SourceRegistryError("source_unresolved", source_id, "资料 ID 无效")
    return source_id


def _safe_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if not run_id or run_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise SourceRegistryError("source_unresolved", "", "任务 ID 无效")
    return run_id


def _canonical_origin_locator(kind: str, value: str, source_id: str = "") -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if kind == "approved_public":
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise SourceRegistryError("source_unresolved", source_id, "公共资料来源地址无效")
        try:
            port = parsed.port
        except ValueError as exc:
            raise SourceRegistryError("source_unresolved", source_id, "公共资料来源端口无效") from exc
        host = parsed.hostname.lower()
        netloc = host
        scheme = parsed.scheme.lower()
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            netloc = f"{host}:{port}"
        return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    if kind == "approved_internal":
        if parsed.scheme != "local" or not parsed.netloc or not parsed.path or parsed.query or parsed.fragment:
            raise SourceRegistryError("source_unresolved", source_id, "内部资料来源地址无效")
        if any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:]):
            raise SourceRegistryError("source_unresolved", source_id, "内部资料来源路径无效")
        return urlunsplit(("local", parsed.netloc, parsed.path, "", ""))
    raise SourceRegistryError("source_unresolved", source_id, "资料来源类型无效")


def _trusted_workspace_root(workspace: Path) -> Path:
    raw = Path(workspace).expanduser().absolute()
    if not raw.is_dir():
        raise SourceRegistryError("source_unresolved", "", "工作区目录不存在")
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise SourceRegistryError("source_unresolved", "", "工作区路径包含不可信符号链接")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise SourceRegistryError("source_unresolved", "", "工作区目录无效")
    return root


def _open_trusted_directory(root: Path, relative_parts: tuple[str, ...], *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, flags)
    try:
        for part in relative_parts:
            if not part or part in {".", ".."} or "/" in part:
                raise SourceRegistryError("source_unresolved", "", "固化资料路径无效")
            try:
                child_fd = os.open(part, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                child_fd = os.open(part, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise SourceRegistryError("source_unresolved", "", "固化资料目录包含不可信符号链接") from exc
            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def _assert_trusted_directory(root: Path, relative_parts: tuple[str, ...], directory_fd: int) -> None:
    candidate = root.joinpath(*relative_parts)
    if _is_symlink_path(root, candidate):
        raise SourceRegistryError("source_unresolved", "", "固化资料目录包含不可信符号链接")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SourceRegistryError("source_unresolved", "", "固化资料目录越过工作区边界") from exc
    actual = os.fstat(directory_fd)
    expected = resolved.stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise SourceRegistryError("source_unresolved", "", "固化资料目录在写入期间发生变化")


def _read_trusted_bytes(root: Path, relative: Path) -> bytes:
    directory_fd = _open_trusted_directory(root, relative.parts[:-1], create=False)
    try:
        _assert_trusted_directory(root, relative.parts[:-1], directory_fd)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(relative.name, flags, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise SourceRegistryError("source_unresolved", "", "固化资料不是普通文件")
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                return handle.read()
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)


def _write_trusted_immutable_bytes(
    root: Path,
    relative: Path,
    data: bytes,
    *,
    source_id: str,
    allow_existing_metadata: bool = False,
) -> bool:
    directory_fd = _open_trusted_directory(root, relative.parts[:-1], create=True)
    temp_name = f".{relative.name}.{secrets.token_hex(8)}"
    created = False
    try:
        _assert_trusted_directory(root, relative.parts[:-1], directory_fd)
        try:
            existing = _read_trusted_bytes(root, relative)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if existing != data and not allow_existing_metadata:
                raise SourceRegistryError("source_conflict", source_id, "固化资料内容发生冲突")
            return False

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(file_fd, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(file_fd)
        _assert_trusted_directory(root, relative.parts[:-1], directory_fd)
        try:
            os.link(
                temp_name,
                relative.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            created = True
        except FileExistsError:
            created = False
        if not created:
            existing = _read_trusted_bytes(root, relative)
            if existing != data and not allow_existing_metadata:
                raise SourceRegistryError("source_conflict", source_id, "固化资料内容发生冲突")
        _assert_trusted_directory(root, relative.parts[:-1], directory_fd)
        if created:
            target_fd = os.open(relative.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            try:
                os.fchmod(target_fd, 0o400)
            finally:
                os.close(target_fd)
        _assert_trusted_directory(root, relative.parts[:-1], directory_fd)
        return created
    except Exception:
        if created:
            try:
                os.unlink(relative.name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _is_symlink_path(root: Path, target: Path) -> bool:
    current = root
    try:
        relative = target.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _workspace_file(root: Path, locator: str, source_id: str) -> Path:
    raw = Path(str(locator or ""))
    if raw.is_absolute() or not str(locator or "").strip():
        raise SourceRegistryError("source_unresolved", source_id, "资料路径必须是工作区内的相对路径")
    candidate = root / raw
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SourceRegistryError("source_unresolved", source_id, "资料路径越过工作区边界") from exc
    if _is_symlink_path(root, candidate) or not resolved.is_file():
        raise SourceRegistryError("source_unresolved", source_id, "资料不存在或包含不可信符号链接")
    return resolved


def _write_provided_text(root: Path, run_id: str, source_id: str, text: object) -> Path:
    content = str(text or "")
    if not content.strip():
        raise SourceRegistryError("source_unresolved", source_id, "用户提供文本不能为空")
    data = content.encode("utf-8")
    if len(data) > _MAX_SOURCE_BYTES:
        raise SourceRegistryError("source_too_large", source_id, "单份资料不能超过 10MB")
    target = root / ".taiji" / "expert-teams" / "sources" / run_id / f"{source_id}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise SourceRegistryError("source_conflict", source_id, "同一资料 ID 已固化为不同内容")
        return target
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{source_id}.", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
        target.chmod(0o400)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return target


def _validated_retrieved_at(value: str, source_id: str = "") -> str:
    retrieved_at = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceRegistryError("source_unresolved", source_id, "已审批资料获取时间无效") from exc
    if parsed.tzinfo is None:
        raise SourceRegistryError("source_unresolved", source_id, "已审批资料获取时间必须包含时区")
    return retrieved_at


def materialize_approved_source(
    workspace: Path,
    run_id: str,
    *,
    kind: str,
    label: str,
    origin_locator: str,
    content: str,
    retrieved_at: str,
    content_sha256: str,
) -> dict:
    """Persist an adapter-produced source inside the current run's trusted boundary."""
    if kind not in _APPROVED_SOURCE_KINDS:
        raise SourceRegistryError("source_unresolved", "", "只允许固化已审批的公共或内部资料")
    data = str(content or "").encode("utf-8")
    if not data or len(data) > _MAX_SOURCE_BYTES or b"\x00" in data:
        raise SourceRegistryError("source_unresolved", "", "固化资料为空、过大或不是文本")
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(content_sha256 or ""):
        raise SourceRegistryError("source_hash_conflict", "", "适配器资料摘要与原始字节不一致")

    trusted_run_id = _safe_run_id(run_id)
    canonical_origin = _canonical_origin_locator(kind, origin_locator)
    trusted_retrieved_at = _validated_retrieved_at(retrieved_at)
    identity = json.dumps(
        {"content_sha256": digest, "kind": kind, "origin_locator": canonical_origin},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    prefix = "PUB" if kind == "approved_public" else "INT"
    source_id = f"{prefix}-{hashlib.sha256(identity).hexdigest()[:24]}"
    root = _trusted_workspace_root(workspace)
    source_relative = Path(".taiji") / "expert-teams" / "sources" / trusted_run_id
    target_relative = source_relative / f"{source_id}.txt"
    manifest_relative = source_relative / f"{source_id}.source.json"
    _write_trusted_immutable_bytes(root, target_relative, data, source_id=source_id)

    proposed_manifest = {
        "schema_version": _MATERIALIZED_SOURCE_SCHEMA,
        "source_id": source_id,
        "kind": kind,
        "label": str(label or source_id).strip()[:200] or source_id,
        "origin_locator": canonical_origin,
        "retrieved_at": trusted_retrieved_at,
        "content_sha256": digest,
    }
    manifest_data = json.dumps(proposed_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    _write_trusted_immutable_bytes(
        root,
        manifest_relative,
        manifest_data,
        source_id=source_id,
        allow_existing_metadata=True,
    )
    try:
        manifest = json.loads(_read_trusted_bytes(root, manifest_relative).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SourceRegistryError("source_conflict", source_id, "固化资料清单损坏") from exc
    expected_identity = (kind, source_id, digest, canonical_origin)
    actual_identity = (
        manifest.get("kind"),
        manifest.get("source_id"),
        manifest.get("content_sha256"),
        manifest.get("origin_locator"),
    )
    if actual_identity != expected_identity or manifest.get("schema_version") != _MATERIALIZED_SOURCE_SCHEMA:
        raise SourceRegistryError("source_conflict", source_id, "固化资料身份发生冲突")

    return {
        "source_id": source_id,
        "kind": kind,
        "label": manifest.get("label") or source_id,
        "locator": target_relative.as_posix(),
        "sha256": digest,
    }


def _materialized_provided_text(root: Path, run_id: str, source_id: str, locator: object) -> Path:
    target = _workspace_file(root, str(locator or ""), source_id)
    expected = root / ".taiji" / "expert-teams" / "sources" / str(run_id) / f"{source_id}.txt"
    if target != expected.resolve(strict=False):
        raise SourceRegistryError("source_unresolved", source_id, "用户提供文本未绑定到当前任务的固化资料")
    return target


def _materialized_approved_source(
    root: Path,
    run_id: str,
    source_id: str,
    kind: str,
    raw_ref: dict,
) -> tuple[Path, dict]:
    if "text" in raw_ref:
        raise SourceRegistryError("source_unresolved", source_id, "已审批资料不能由客户端文本直接声明")
    target = _workspace_file(root, str(raw_ref.get("locator") or ""), source_id)
    expected = root / ".taiji" / "expert-teams" / "sources" / _safe_run_id(run_id) / f"{source_id}.txt"
    if target != expected.resolve(strict=False):
        raise SourceRegistryError("source_unresolved", source_id, "已审批资料未绑定到当前任务的服务端固化区")
    manifest_path = expected.with_suffix(".source.json")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SourceRegistryError("source_unresolved", source_id, "已审批资料缺少服务端固化清单")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceRegistryError("source_unresolved", source_id, "已审批资料固化清单无效") from exc
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if (
        manifest.get("schema_version") != _MATERIALIZED_SOURCE_SCHEMA
        or manifest.get("source_id") != source_id
        or manifest.get("kind") != kind
        or manifest.get("content_sha256") != digest
    ):
        raise SourceRegistryError("source_hash_conflict", source_id, "已审批资料固化清单与原始字节不一致")
    origin_locator = _canonical_origin_locator(kind, manifest.get("origin_locator"), source_id)
    retrieved_at = _validated_retrieved_at(manifest.get("retrieved_at"), source_id)
    identity = json.dumps(
        {"content_sha256": digest, "kind": kind, "origin_locator": origin_locator},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    prefix = "PUB" if kind == "approved_public" else "INT"
    if source_id != f"{prefix}-{hashlib.sha256(identity).hexdigest()[:24]}":
        raise SourceRegistryError("source_hash_conflict", source_id, "已审批资料身份摘要不一致")
    return target, {"origin_locator": origin_locator, "retrieved_at": retrieved_at}


def _validated_text_bytes(target: Path, source_id: str) -> bytes:
    if target.suffix.lower() not in _ALLOWED_TEXT_SUFFIXES:
        raise SourceRegistryError("source_type_not_allowed", source_id, "首版仅支持 TXT、Markdown、CSV 和 JSON 文本资料")
    size = target.stat().st_size
    if size <= 0:
        raise SourceRegistryError("source_unresolved", source_id, "资料不能为空")
    if size > _MAX_SOURCE_BYTES:
        raise SourceRegistryError("source_too_large", source_id, "单份资料不能超过 10MB")
    data = target.read_bytes()
    if b"\x00" in data:
        raise SourceRegistryError("source_binary_not_allowed", source_id, "资料包含二进制内容")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceRegistryError("source_invalid_utf8", source_id, "资料必须使用 UTF-8 编码") from exc
    return data


def resolve_source_registry(workspace: Path, run_id: str, source_refs: list[dict]) -> tuple[list[dict], dict]:
    root = Path(workspace).expanduser().resolve()
    resolved_refs = []
    registry = {}
    seen = set()
    for raw_ref in source_refs or []:
        if not isinstance(raw_ref, dict):
            raise SourceRegistryError("source_unresolved", "", "资料引用格式无效")
        source_id = _safe_id(raw_ref.get("source_id"))
        if source_id in seen:
            raise SourceRegistryError("source_duplicate", source_id, "资料 ID 不能重复")
        seen.add(source_id)
        kind = str(raw_ref.get("kind") or "").strip()
        trusted_trace = {}
        if kind == "provided_text":
            if "text" in raw_ref:
                target = _write_provided_text(root, str(run_id), source_id, raw_ref.get("text"))
            else:
                target = _materialized_provided_text(root, str(run_id), source_id, raw_ref.get("locator"))
        elif kind in {"local_file", "attachment"}:
            target = _workspace_file(root, str(raw_ref.get("locator") or ""), source_id)
        elif kind in _APPROVED_SOURCE_KINDS:
            target, trusted_trace = _materialized_approved_source(root, str(run_id), source_id, kind, raw_ref)
        else:
            raise SourceRegistryError("source_unresolved", source_id, "当前资料类型尚未接入受信解析链")
        data = _validated_text_bytes(target, source_id)
        relative = target.relative_to(root).as_posix()
        digest = hashlib.sha256(data).hexdigest()
        sanitized = {
            "source_id": source_id,
            "kind": kind,
            "label": str(raw_ref.get("label") or source_id).strip(),
            "locator": relative,
            "sha256": digest,
            **trusted_trace,
        }
        client_hash = str(raw_ref.get("sha256") or "").strip()
        if client_hash and client_hash != digest:
            raise SourceRegistryError("source_hash_conflict", source_id, "客户端资料摘要与原始字节不一致")
        resolved_refs.append(sanitized)
        registry[source_id] = {**deepcopy(sanitized), "status": "ready", "size_bytes": len(data)}
    return resolved_refs, registry
