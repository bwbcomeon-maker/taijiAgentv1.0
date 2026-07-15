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
}

LEGACY_API_KEY_ENV = {
    "alibaba_dashscope": ("DASHSCOPE_API_KEY",),
    "zhipu": ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
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
    return PROVIDER_FAMILY_ALIASES.get(normalized, normalized)


def credential_secret_env(credential_id: object) -> str:
    """Return the dedicated environment variable for a named credential."""
    normalized = normalize_credential_id(credential_id)
    return f"TAIJI_CREDENTIAL_{normalized.upper().replace('-', '_')}_API_KEY"


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


def resolve_api_key(
    provider: object,
    credential_ref: object = "",
    *,
    config_data: dict[str, Any] | None = None,
) -> str:
    """Resolve a named API key, or lazily fall back to the legacy provider env."""
    family = provider_family(provider)
    ref = str(credential_ref or "").strip()
    if not ref:
        for legacy_env in LEGACY_API_KEY_ENV.get(family, ()):
            value = os.getenv(legacy_env, "").strip()
            if value:
                return value
        return ""

    row = load_credential(ref, config_data=config_data)
    if provider_family(row.get("provider_family")) != family:
        raise ValueError("所选凭据不属于当前 Provider。")
    secret_env = str(row.get("secret_env") or "").strip()
    if secret_env != credential_secret_env(row.get("id")):
        raise ValueError("所选凭据的 Secret 环境变量配置无效。")
    return os.getenv(secret_env, "").strip()
