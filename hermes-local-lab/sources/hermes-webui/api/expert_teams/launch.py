"""Public contract helpers for atomic standalone expert-team launch."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy

from .contracts import ContractError


_LAUNCH_FIELDS = frozenset(
    {"launch_profile_id", "prompt", "idempotency_key", "session_options"}
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
}
_IDEMPOTENCY_PATTERN = re.compile(r"[A-Za-z0-9:._-]+")


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
    return {
        "launch_profile_id": launch_profile_id,
        "prompt": prompt,
        "idempotency_key": idempotency_key,
        "session_options": options,
    }


def launch_request_fingerprint(validated: dict) -> str:
    """Bind one idempotency key to the exact normalized launch request."""
    canonical = {
        "contract": "expert-team-standalone-launch/v1",
        "launch_profile_id": str(validated["launch_profile_id"]),
        "prompt": str(validated["prompt"]),
        "session_options": deepcopy(validated.get("session_options") or {}),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
