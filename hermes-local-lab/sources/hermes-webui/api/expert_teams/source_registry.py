"""Resolve opaque document sources inside a run's trusted workspace boundary."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path


_MAX_SOURCE_BYTES = 10 * 1024 * 1024
_ALLOWED_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}


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


def _materialized_provided_text(root: Path, run_id: str, source_id: str, locator: object) -> Path:
    target = _workspace_file(root, str(locator or ""), source_id)
    expected = root / ".taiji" / "expert-teams" / "sources" / str(run_id) / f"{source_id}.txt"
    if target != expected.resolve(strict=False):
        raise SourceRegistryError("source_unresolved", source_id, "用户提供文本未绑定到当前任务的固化资料")
    return target


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
        if kind == "provided_text":
            if "text" in raw_ref:
                target = _write_provided_text(root, str(run_id), source_id, raw_ref.get("text"))
            else:
                target = _materialized_provided_text(root, str(run_id), source_id, raw_ref.get("locator"))
        elif kind in {"local_file", "attachment"}:
            target = _workspace_file(root, str(raw_ref.get("locator") or ""), source_id)
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
        }
        client_hash = str(raw_ref.get("sha256") or "").strip()
        if client_hash and client_hash != digest:
            raise SourceRegistryError("source_hash_conflict", source_id, "客户端资料摘要与原始字节不一致")
        resolved_refs.append(sanitized)
        registry[source_id] = {**deepcopy(sanitized), "status": "ready", "size_bytes": len(data)}
    return resolved_refs, registry
