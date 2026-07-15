"""Named custom vision providers with isolated credentials.

Only the two wire protocols already supported by the auxiliary vision client
are accepted. Secrets are referenced by a deterministic environment variable
and never stored in the provider metadata.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

CUSTOM_VISION_PROVIDER_PREFIX = "custom:"
ALLOWED_CUSTOM_VISION_TRANSPORTS = {
    "openai_chat_completions",
    "anthropic_messages",
}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_MODEL_RE = re.compile(r"^[^\s]+$")


def normalize_custom_vision_provider_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith(CUSTOM_VISION_PROVIDER_PREFIX):
        raw = raw[len(CUSTOM_VISION_PROVIDER_PREFIX):]
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    if not raw or not _ID_RE.match(raw):
        raise ValueError("外部识图 Provider ID 只能包含小写字母、数字、短横线和下划线。")
    return raw


def custom_vision_provider_name(provider_id: Any) -> str:
    return f"{CUSTOM_VISION_PROVIDER_PREFIX}{normalize_custom_vision_provider_id(provider_id)}"


def custom_vision_provider_env_var(provider_id: Any) -> str:
    normalized = normalize_custom_vision_provider_id(provider_id)
    token = re.sub(r"[^A-Z0-9]+", "_", normalized.upper()).strip("_")
    return f"TAIJI_VISION_CUSTOM_{token}_API_KEY"


def _normalize_base_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("外部识图 Base URL 必须是不含账号、查询参数和片段的完整 HTTPS 地址。")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("外部识图 Base URL 不得指向本机、内网或链路本地地址。")
    return url


def is_custom_vision_base_url_safe(value: Any) -> bool:
    """Apply the shared DNS-aware SSRF guard at save and request time."""
    try:
        url = _normalize_base_url(value)
    except ValueError:
        return False
    from tools.url_safety import is_safe_url

    return bool(is_safe_url(url))


def _normalize_models(value: Any, default_model: Any = "") -> list[str]:
    raw_items: Iterable[Any]
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace("\n", ",").split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    models: list[str] = []
    for item in raw_items:
        model = str(item or "").strip()
        if not model:
            continue
        if not _MODEL_RE.match(model):
            raise ValueError("外部识图模型 ID 不能包含空白字符。")
        if model not in models:
            models.append(model)
    default = str(default_model or "").strip()
    if default:
        if not _MODEL_RE.match(default):
            raise ValueError("默认识图模型 ID 不能包含空白字符。")
        if default not in models:
            models.insert(0, default)
    if not models:
        raise ValueError("至少需要配置一个外部识图模型 ID。")
    return models


def normalize_custom_vision_provider_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("外部识图 Provider 配置必须是对象。")
    provider_id = normalize_custom_vision_provider_id(entry.get("id") or entry.get("provider_id"))
    transport = str(entry.get("transport") or "openai_chat_completions").strip().lower()
    if transport not in ALLOWED_CUSTOM_VISION_TRANSPORTS:
        raise ValueError("transport 只能是 openai_chat_completions 或 anthropic_messages。")
    models = _normalize_models(entry.get("models"), entry.get("default_model") or entry.get("model"))
    default_model = str(entry.get("default_model") or entry.get("model") or models[0]).strip()
    if default_model not in models:
        models.insert(0, default_model)
    return {
        "id": provider_id,
        "name": str(entry.get("name") or provider_id).strip()[:80] or provider_id,
        "base_url": _normalize_base_url(entry.get("base_url")),
        "api_key_env": custom_vision_provider_env_var(provider_id),
        "models": models,
        "default_model": default_model,
        "transport": transport,
    }


def load_custom_vision_provider_entries(
    config_data: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    if config_data is None:
        try:
            from hermes_cli.config import load_config

            config_data = load_config()
        except Exception:
            return []
    raw_entries = config_data.get("custom_vision_providers") if isinstance(config_data, dict) else None
    if not isinstance(raw_entries, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw_entries:
        try:
            entries.append(normalize_custom_vision_provider_entry(item))
        except ValueError:
            continue
    return entries


def find_custom_vision_provider_entry(
    provider_id: Any,
    config_data: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    try:
        normalized = normalize_custom_vision_provider_id(provider_id)
    except ValueError:
        return None
    return next(
        (entry for entry in load_custom_vision_provider_entries(config_data) if entry["id"] == normalized),
        None,
    )


def custom_vision_provider_public_row(
    entry: dict[str, Any],
    *,
    active_provider: str = "",
) -> dict[str, Any]:
    normalized = normalize_custom_vision_provider_entry(entry)
    provider_name = custom_vision_provider_name(normalized["id"])
    configured = bool(os.getenv(normalized["api_key_env"], "").strip())
    transport_label = (
        "OpenAI Chat Completions"
        if normalized["transport"] == "openai_chat_completions"
        else "Anthropic Messages"
    )
    return {
        "id": provider_name,
        "name": normalized["name"],
        "description": f"{transport_label} 兼容识图端点",
        "active": provider_name == str(active_provider or "").strip().lower(),
        "available": configured,
        "key_status": {
            "configured": configured,
            "source": "env_var" if configured else "none",
            "env_var": normalized["api_key_env"],
        },
        "requires_env": [normalized["api_key_env"]],
        "requires_base_url": False,
        "models": [{"id": model, "label": model} for model in normalized["models"]],
        "default_model": normalized["default_model"],
        "custom": True,
        "base_url": normalized["base_url"],
        "transport": normalized["transport"],
        "transport_label": transport_label,
    }
