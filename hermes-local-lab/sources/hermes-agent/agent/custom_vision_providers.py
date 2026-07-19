"""Named custom vision providers with isolated credentials.

Only the two wire protocols already supported by the auxiliary vision client
are accepted. New entries bind secrets through ``credential_ref``; deterministic
legacy env names are accepted only while loading persisted pre-migration rows.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from agent.custom_image_providers import _normalize_https_endpoint_url
from agent.provider_credentials import (
    credential_secret_env,
    normalize_credential_id,
    resolve_api_key,
    resolve_secret_env_value,
)
from agent.safe_outbound_http import (
    NetworkScope,
    SafeOutboundError,
    _validate_address,
    normalize_network_scope,
)

CUSTOM_VISION_PROVIDER_PREFIX = "custom:"
ALLOWED_CUSTOM_VISION_TRANSPORTS = {
    "openai_chat_completions",
    "anthropic_messages",
}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_MODEL_RE = re.compile(r"^[^\s]+$")
_LEGACY_API_KEY_ENV_MARKER_KEY = "_legacy_api_key_env_read_compat"
_LEGACY_API_KEY_ENV_MARKER = object()
_MISSING = object()


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


def _normalize_base_url(
    value: Any,
    *,
    network_scope: NetworkScope = NetworkScope.PUBLIC_DIRECT,
) -> str:
    url = _normalize_https_endpoint_url(value, label="外部识图")
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("外部识图 Base URL 的主机名格式无效。")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        literal_scope = (
            NetworkScope.PRIVATE_DIRECT
            if network_scope is NetworkScope.PRIVATE_DIRECT
            else NetworkScope.PUBLIC_DIRECT
        )
        try:
            _validate_address(address, literal_scope)
        except SafeOutboundError as exc:
            raise ValueError(
                "外部识图 Base URL 不得指向当前网络范围禁止的地址。"
            ) from exc
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
    if "api_key_env" in entry:
        raise ValueError("外部识图 Provider 不允许配置 api_key_env，请使用 credential_ref。")
    provider_id = normalize_custom_vision_provider_id(entry.get("id") or entry.get("provider_id"))
    credential_ref = str(entry.get("credential_ref") or "").strip()
    if credential_ref:
        credential_ref = normalize_credential_id(credential_ref)
    transport = str(entry.get("transport") or "openai_chat_completions").strip().lower()
    if transport not in ALLOWED_CUSTOM_VISION_TRANSPORTS:
        raise ValueError("transport 只能是 openai_chat_completions 或 anthropic_messages。")
    try:
        network_scope = normalize_network_scope(
            entry.get("network_scope"),
            default=NetworkScope.PUBLIC_DIRECT,
        )
    except SafeOutboundError as exc:
        raise ValueError("network_scope 配置无效。") from exc
    trusted_proxy_profile = str(entry.get("trusted_proxy_profile") or "").strip()
    if network_scope is NetworkScope.TRUSTED_PROXY:
        if not trusted_proxy_profile:
            raise ValueError("trusted_proxy 必须引用已批准的代理配置。")
    elif trusted_proxy_profile:
        raise ValueError("只有 trusted_proxy 可配置 trusted_proxy_profile。")
    models = _normalize_models(entry.get("models"), entry.get("default_model") or entry.get("model"))
    default_model = str(entry.get("default_model") or entry.get("model") or models[0]).strip()
    if default_model not in models:
        models.insert(0, default_model)
    normalized = {
        "id": provider_id,
        "name": str(entry.get("name") or provider_id).strip()[:80] or provider_id,
        "base_url": _normalize_base_url(
            entry.get("base_url"),
            network_scope=network_scope,
        ),
        "models": models,
        "default_model": default_model,
        "transport": transport,
        "network_scope": network_scope.value,
        "trusted_proxy_profile": trusted_proxy_profile,
    }
    if credential_ref:
        normalized["credential_ref"] = credential_ref
    return normalized


def _normalize_loaded_custom_vision_provider_entry(
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Preserve the loader-only legacy credential marker by object identity."""
    if not isinstance(entry, dict):
        raise ValueError("外部识图 Provider 配置必须是对象。")
    marker = entry.get(_LEGACY_API_KEY_ENV_MARKER_KEY, _MISSING)
    cleaned = dict(entry)
    cleaned.pop(_LEGACY_API_KEY_ENV_MARKER_KEY, None)
    normalized = normalize_custom_vision_provider_entry(cleaned)
    if marker is _MISSING:
        return normalized
    if marker is not _LEGACY_API_KEY_ENV_MARKER or normalized.get("credential_ref"):
        raise ValueError("旧版外部识图 Provider 密钥引用无效。")
    normalized[_LEGACY_API_KEY_ENV_MARKER_KEY] = _LEGACY_API_KEY_ENV_MARKER
    return normalized


def _normalize_persisted_custom_vision_provider_entry(
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Accept only the deterministic legacy env stored in persisted config."""
    if not isinstance(entry, dict):
        raise ValueError("外部识图 Provider 配置必须是对象。")
    if "api_key_env" not in entry:
        return normalize_custom_vision_provider_entry(entry)
    if "credential_ref" in entry:
        raise ValueError("旧版 api_key_env 不能与 credential_ref 同时存在。")

    provider_id = normalize_custom_vision_provider_id(
        entry.get("id") or entry.get("provider_id")
    )
    expected_env = custom_vision_provider_env_var(provider_id)
    if (
        not isinstance(entry.get("api_key_env"), str)
        or entry["api_key_env"] != expected_env
    ):
        raise ValueError("旧版 api_key_env 必须与 Provider ID 的固定环境变量一致。")

    cleaned = dict(entry)
    cleaned.pop("api_key_env", None)
    normalized = normalize_custom_vision_provider_entry(cleaned)
    if normalized.get("credential_ref"):
        raise ValueError("旧版 api_key_env 不能与 credential_ref 同时存在。")
    normalized[_LEGACY_API_KEY_ENV_MARKER_KEY] = _LEGACY_API_KEY_ENV_MARKER
    return normalized


def custom_vision_provider_secret_env(entry: dict[str, Any]) -> str:
    """Return the env bound by a credential_ref or loader-authenticated legacy row."""
    normalized = _normalize_loaded_custom_vision_provider_entry(entry)
    credential_ref = str(normalized.get("credential_ref") or "")
    if credential_ref:
        return credential_secret_env(credential_ref)
    if (
        normalized.get(_LEGACY_API_KEY_ENV_MARKER_KEY)
        is _LEGACY_API_KEY_ENV_MARKER
    ):
        return custom_vision_provider_env_var(normalized.get("id"))
    return ""


def custom_vision_provider_api_key(
    entry: dict[str, Any],
    *,
    config_path: Any = None,
    allow_process_fallback: bool | None = None,
) -> str:
    """Resolve only the credential explicitly bound to this provider entry."""
    normalized = _normalize_loaded_custom_vision_provider_entry(entry)
    credential_ref = str(normalized.get("credential_ref") or "").strip()
    if credential_ref:
        return resolve_api_key(
            "custom",
            credential_ref,
            config_path=config_path,
            allow_process_fallback=allow_process_fallback,
        )
    if (
        normalized.get(_LEGACY_API_KEY_ENV_MARKER_KEY)
        is _LEGACY_API_KEY_ENV_MARKER
    ):
        return resolve_secret_env_value(
            custom_vision_provider_env_var(normalized.get("id")),
            config_path=config_path,
            allow_process_fallback=allow_process_fallback,
        )
    return ""


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
    seen_ids: set[str] = set()
    for item in raw_entries:
        try:
            normalized = _normalize_persisted_custom_vision_provider_entry(item)
        except ValueError:
            continue
        provider_id = normalized["id"]
        if provider_id in seen_ids:
            raise ValueError(f"外部识图 Provider 配置包含重复 ID：{provider_id}")
        seen_ids.add(provider_id)
        entries.append(normalized)
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
    config_path: Any = None,
    allow_process_fallback: bool | None = None,
) -> dict[str, Any]:
    normalized = _normalize_loaded_custom_vision_provider_entry(entry)
    provider_name = custom_vision_provider_name(normalized["id"])
    credential_ref = str(normalized.get("credential_ref") or "").strip()
    env_var = custom_vision_provider_secret_env(normalized)
    legacy_env = bool(
        normalized.get(_LEGACY_API_KEY_ENV_MARKER_KEY)
        is _LEGACY_API_KEY_ENV_MARKER
    )
    try:
        configured = bool(
            custom_vision_provider_api_key(
                normalized,
                config_path=config_path,
                allow_process_fallback=allow_process_fallback,
            )
        )
    except ValueError:
        configured = False
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
            "source": (
                "legacy_env_var"
                if configured and legacy_env
                else "credential_ref"
                if configured
                else "none"
            ),
            "env_var": env_var,
        },
        "requires_env": [env_var] if env_var else [],
        "requires_base_url": False,
        "models": [{"id": model, "label": model} for model in normalized["models"]],
        "default_model": normalized["default_model"],
        "custom": True,
        "base_url": normalized["base_url"],
        "transport": normalized["transport"],
        "transport_label": transport_label,
        "credential_ref": credential_ref,
        "network_scope": normalized["network_scope"],
        "trusted_proxy_profile": normalized["trusted_proxy_profile"],
    }
