"""Public contract helpers for atomic standalone expert-team launch."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy

from .contracts import ContractError
from .research_contract import (
    RESEARCH_V3_START_FIELDS,
    ResearchV3SpecError,
    normalize_research_v3_start_spec,
)


_LAUNCH_FIELDS = frozenset(
    {
        "launch_profile_id", "prompt", "idempotency_key", "session_options",
        "source_session_id", "source_attachments", *RESEARCH_V3_START_FIELDS,
    }
)
_SESSION_OPTION_FIELDS = frozenset(
    {"workspace", "profile", "project_id", "model", "model_provider"}
)
_MAX_LENGTHS = {
    "launch_profile_id": 128,
    "prompt": 20_000,
    "idempotency_key": 240,
    "workspace": 4096,
    "profile": 128,
    "project_id": 240,
    "model": 512,
    "model_provider": 128,
    "source_session_id": 240,
}
_IDEMPOTENCY_PATTERN = re.compile(r"[A-Za-z0-9:._-]+")
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,240}")
_MAX_SOURCE_ATTACHMENTS = 8
_MAX_SOURCE_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _nfc_text(value: object) -> str:
    return unicodedata.normalize("NFC", str(value)).strip()


def _required_text(body: dict, field: str) -> str:
    if field not in body:
        raise ContractError(f"{field}_required", field, f"{field} 为必填项")
    value = body[field]
    if type(value) is not str:
        raise ContractError(
            f"{field}_invalid_type",
            field,
            f"{field} 必须是字符串",
        )
    normalized = _nfc_text(value)
    if not normalized:
        raise ContractError(f"{field}_required", field, f"{field} 不能为空")
    if len(normalized) > _MAX_LENGTHS[field]:
        raise ContractError(
            f"{field}_too_long",
            field,
            f"{field} 超出长度限制",
        )
    return normalized


def _optional_session_text(options: dict, field: str) -> str | None:
    if field not in options or options[field] is None:
        return None
    value = options[field]
    if type(value) is not str:
        raise ContractError(
            "invalid_session_option_type",
            f"session_options.{field}",
            f"{field} 必须是字符串",
        )
    normalized = _nfc_text(value)
    if not normalized:
        return None
    if len(normalized) > _MAX_LENGTHS[field]:
        raise ContractError(
            "session_option_too_long",
            f"session_options.{field}",
            f"{field} 超出长度限制",
        )
    if any(ord(character) < 32 for character in normalized):
        raise ContractError(
            "invalid_session_option",
            f"session_options.{field}",
            f"{field} 包含不支持的控制字符",
        )
    return normalized


def _source_attachments(body: dict) -> tuple[str, list[dict]]:
    raw = body.get("source_attachments", [])
    if raw is None:
        raw = []
    if type(raw) is not list or len(raw) > _MAX_SOURCE_ATTACHMENTS:
        raise ContractError("source_attachments_invalid", "source_attachments", "研究资料附件数量无效")
    source_session_id = _nfc_text(body.get("source_session_id", ""))
    if raw and _SAFE_SESSION_ID.fullmatch(source_session_id) is None:
        raise ContractError("source_session_invalid", "source_session_id", "资料来源会话无效")
    if not raw and source_session_id:
        raise ContractError("source_attachments_required", "source_attachments", "资料来源会话未携带附件")
    normalized = []
    for index, item in enumerate(raw):
        if type(item) is not dict or set(item) != {"name", "ref", "mime", "size"}:
            raise ContractError("source_attachment_invalid", f"source_attachments.{index}", "研究资料附件格式无效")
        name = _nfc_text(item.get("name", ""))
        ref = _nfc_text(item.get("ref", ""))
        mime = _nfc_text(item.get("mime", ""))
        size = item.get("size")
        safe_upload_name = lambda value: bool(
            value and len(value) <= 200 and value not in {".", ".."}
            and "/" not in value and "\\" not in value and "\x00" not in value
        )
        if not safe_upload_name(name) or not safe_upload_name(ref):
            raise ContractError("source_attachment_invalid", f"source_attachments.{index}", "研究资料附件名称无效")
        if type(size) is not int or isinstance(size, bool) or not 0 < size <= _MAX_SOURCE_ATTACHMENT_BYTES:
            raise ContractError("source_attachment_invalid", f"source_attachments.{index}.size", "研究资料附件大小无效")
        if len(mime) > 128:
            raise ContractError("source_attachment_invalid", f"source_attachments.{index}.mime", "研究资料附件类型无效")
        normalized.append({"name": name, "ref": ref, "mime": mime, "size": size})
    return source_session_id, normalized


def _research_v3_start_spec(body: dict) -> dict:
    """Validate the two explicit v3 writing choices when the caller sent them."""
    requested = {
        field: body[field]
        for field in RESEARCH_V3_START_FIELDS
        if field in body
    }
    if not requested:
        return {}
    try:
        return normalize_research_v3_start_spec(requested)
    except ResearchV3SpecError as exc:
        raise ContractError(exc.code, exc.field, exc.message) from exc


def validate_standalone_launch_request(body: dict) -> dict:
    """Validate the one-request portal launch contract without side effects."""
    if type(body) is not dict:
        raise ContractError(
            "launch_request_invalid_type",
            "request",
            "发起请求必须是对象",
        )
    unknown = sorted(set(body) - _LAUNCH_FIELDS)
    if unknown:
        raise ContractError(
            "server_owned_launch_field",
            unknown[0],
            "团队、流程和会话标识由服务端创建",
        )

    launch_profile_id = _required_text(body, "launch_profile_id")
    prompt = _required_text(body, "prompt")
    idempotency_key = _required_text(body, "idempotency_key")
    if len(idempotency_key) < 8:
        raise ContractError(
            "idempotency_key_too_short",
            "idempotency_key",
            "幂等键至少需要 8 个字符",
        )
    if _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
        raise ContractError(
            "idempotency_key_invalid_format",
            "idempotency_key",
            "幂等键只能包含英文字母、数字、冒号、点、下划线和连字符",
        )
    raw_options = body.get("session_options", {})
    if raw_options is None:
        raw_options = {}
    if type(raw_options) is not dict:
        raise ContractError(
            "session_options_invalid_type",
            "session_options",
            "会话选项必须是对象",
        )
    unknown_options = sorted(set(raw_options) - _SESSION_OPTION_FIELDS)
    if unknown_options:
        field = unknown_options[0]
        raise ContractError(
            "unsupported_session_option",
            f"session_options.{field}",
            "该会话选项不允许用于专家团发起",
        )
    options = {
        field: value
        for field in _SESSION_OPTION_FIELDS
        if (value := _optional_session_text(raw_options, field)) is not None
    }
    source_session_id, source_attachments = _source_attachments(body)
    return {
        "launch_profile_id": launch_profile_id,
        "prompt": prompt,
        "idempotency_key": idempotency_key,
        "session_options": options,
        "source_session_id": source_session_id,
        "source_attachments": source_attachments,
        **_research_v3_start_spec(body),
    }


def launch_request_fingerprint(validated: dict) -> str:
    """Bind one idempotency key to the exact normalized launch request."""
    canonical = {
        "contract": "expert-team-standalone-launch/v1",
        "launch_profile_id": str(validated["launch_profile_id"]),
        "prompt": str(validated["prompt"]),
        "session_options": deepcopy(validated.get("session_options") or {}),
        "source_session_id": str(validated.get("source_session_id") or ""),
        "source_attachments": deepcopy(validated.get("source_attachments") or []),
        **{
            field: str(validated[field])
            for field in RESEARCH_V3_START_FIELDS
            if field in validated
        },
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
