"""Named provider credential metadata and legacy API-key fallback."""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from hermes_constants import get_hermes_home


PROVIDER_FAMILY_ALIASES = {
    "alibaba": "alibaba_dashscope",
    "alibaba_dashscope": "alibaba_dashscope",
    "dashscope": "alibaba_dashscope",
    "zai": "zhipu",
    "zhipu": "zhipu",
    "zhipu-image": "zhipu",
    "ark": "doubao",
    "doubao": "doubao",
    "volcengine": "doubao",
    "baidu-qianfan": "qianfan",
    "qianfan": "qianfan",
    "minimax": "minimax",
    "minimax-image": "minimax",
    "custom": "custom",
    "custom-image": "custom",
}

LEGACY_API_KEY_ENV = {
    "alibaba_dashscope": ("DASHSCOPE_API_KEY",),
    "zhipu": ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
}
AUTH_TYPES = (
    "api_key",
    "bearer_token",
    "access_key_secret",
    "service_account",
    "oauth",
    "no_auth",
)
_AUTH_TYPE_FIELDS: dict[str, tuple[dict[str, Any], ...]] = {
    "api_key": (
        {"name": "api_key", "label": "API Key", "secret": True, "required": True, "credential": True},
    ),
    "bearer_token": (
        {"name": "bearer_token", "label": "Bearer Token", "secret": True, "required": True, "credential": True},
    ),
    "access_key_secret": (
        {"name": "access_key_id", "label": "Access Key ID", "secret": False, "required": True, "credential": True},
        {"name": "access_key_secret", "label": "Access Key Secret", "secret": True, "required": True, "credential": True},
    ),
    "service_account": (
        {"name": "service_account_json", "label": "Service Account JSON", "secret": True, "required": True, "credential": True},
    ),
    "oauth": (),
    "no_auth": (),
}
_AUTH_TYPE_MESSAGES = {
    "api_key": "填写平台签发的 API Key；密钥只保存在本机。",
    "bearer_token": "填写平台签发的 Bearer Token；令牌只保存在本机。",
    "access_key_secret": "当前版本尚未实现 Access Key/Secret 签名适配器，不能在此配置。",
    "service_account": "当前版本尚未实现 Service Account 适配器，不能在此配置。",
    "oauth": "此 Provider 使用 OAuth 授权，请在对应平台完成授权后刷新状态。",
    "no_auth": "此 Provider 不需要认证，无需填写凭据。",
}
_CREDENTIAL_TRANSACTION_LOCK = threading.RLock()


@contextmanager
def credential_transaction():
    """Serialize credential metadata checks with reference mutations."""
    with _CREDENTIAL_TRANSACTION_LOCK:
        yield


def normalize_credential_id(credential_id: object) -> str:
    """Return a stable, environment-variable-safe credential identifier."""
    raw = str(credential_id or "").strip().lower()
    normalized = re.sub(r"[\s_]+", "-", raw)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized):
        raise ValueError("credential id must contain only letters, numbers, spaces, '_' or '-'")
    return normalized


def provider_family(provider: object) -> str:
    """Map provider aliases used by capabilities to their credential family."""
    normalized = str(provider or "").strip().lower()
    if normalized.startswith("custom:"):
        return "custom"
    return PROVIDER_FAMILY_ALIASES.get(normalized, normalized)


def auth_schema(auth_type: object) -> dict[str, Any]:
    """Describe an auth shape without claiming an unimplemented adapter exists."""
    normalized = str(auth_type or "api_key").strip().lower()
    if normalized not in AUTH_TYPES:
        raise ValueError(f"unsupported auth_type: {normalized}")
    editable = normalized == "api_key"
    return {
        "auth_type": normalized,
        "credential_fields": [dict(field) for field in _AUTH_TYPE_FIELDS[normalized]],
        "editable": editable,
        "message": _AUTH_TYPE_MESSAGES[normalized],
    }


def credential_secret_env(credential_id: object) -> str:
    """Return the dedicated environment variable for a named credential."""
    normalized = normalize_credential_id(credential_id)
    return f"TAIJI_CREDENTIAL_{normalized.upper().replace('-', '_')}_API_KEY"


def _credential_secret_value(secret_env: str) -> str:
    value = os.getenv(secret_env, "").strip()
    if value:
        return value
    env_path = get_hermes_home() / ".env"
    if not env_path.exists():
        return ""
    try:
        from dotenv import dotenv_values

        return str(dotenv_values(env_path).get(secret_env) or "").strip()
    except Exception:
        return ""


def _load_config_data() -> dict[str, Any]:
    configured_path = str(os.getenv("HERMES_CONFIG_PATH") or "").strip()
    config_path = Path(configured_path) if configured_path else get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def find_credential(
    config_data: dict[str, Any] | None,
    credential_ref: object,
) -> dict[str, Any] | None:
    """Find credential metadata by normalized ID without exposing its secret."""
    target = normalize_credential_id(credential_ref)
    data = config_data if isinstance(config_data, dict) else _load_config_data()
    rows = data.get("provider_credentials")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_id = normalize_credential_id(row.get("id"))
        except ValueError:
            continue
        if row_id == target:
            return row
    return None


def load_credential(
    credential_ref: object,
    *,
    config_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load credential metadata or fail without falling back to another secret."""
    row = find_credential(config_data, credential_ref)
    if row is None:
        raise ValueError("所选凭据不存在。")
    return row


def default_credential_ref(
    provider: object,
    *,
    config_data: dict[str, Any] | None = None,
) -> str:
    """Return the unique explicitly marked family default without mutating config."""
    family = provider_family(provider)
    data = config_data if isinstance(config_data, dict) else _load_config_data()
    rows = data.get("provider_credentials")
    if not isinstance(rows, list):
        return ""
    marked_defaults: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or provider_family(row.get("provider_family")) != family:
            continue
        if str(row.get("auth_type") or "api_key").strip().lower() != "api_key":
            continue
        try:
            credential_id = normalize_credential_id(row.get("id"))
        except ValueError:
            continue
        if str(row.get("secret_env") or "").strip() != credential_secret_env(credential_id):
            continue
        if bool(row.get("default")):
            marked_defaults.append(credential_id)
    if not marked_defaults:
        return ""
    if len(marked_defaults) > 1:
        raise ValueError("当前 Provider 配置了多个默认凭据，请保留一个。")
    credential_id = marked_defaults[0]
    if not _credential_secret_value(credential_secret_env(credential_id)):
        return ""
    return credential_id


def resolve_api_key(
    provider: object,
    credential_ref: object = "",
    *,
    config_data: dict[str, Any] | None = None,
) -> str:
    """Resolve a named API key, or lazily fall back to the legacy provider env."""
    family = provider_family(provider)
    ref = str(credential_ref or "").strip()
    explicit_ref = bool(ref)
    data = config_data if isinstance(config_data, dict) else _load_config_data()
    if not ref:
        ref = default_credential_ref(family, config_data=data)

    if ref:
        row = load_credential(ref, config_data=data)
        if provider_family(row.get("provider_family")) != family:
            raise ValueError("所选凭据不属于当前 Provider。")
        secret_env = str(row.get("secret_env") or "").strip()
        if secret_env != credential_secret_env(row.get("id")):
            raise ValueError("所选凭据的 Secret 环境变量配置无效。")
        value = _credential_secret_value(secret_env)
        if value or explicit_ref:
            return value
    for legacy_env in LEGACY_API_KEY_ENV.get(family, ()):
        value = os.getenv(legacy_env, "").strip()
        if value:
            return value
    return ""
