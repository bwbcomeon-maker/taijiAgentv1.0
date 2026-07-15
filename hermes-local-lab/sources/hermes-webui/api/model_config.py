"""Aggregated model configuration endpoints for Hermes WebUI.

This module keeps browser-driven model setup on the same config.yaml/.env
surface the Hermes CLI uses.  It deliberately returns credential status only,
never secret values.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import logging
import os
import stat
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.provider_credentials import (
    credential_transaction,
    credential_secret_env,
    default_credential_ref,
    load_credential,
    normalize_credential_id,
    provider_family,
)
from plugins.image_gen.domestic_common import credential_field, normalized_setup_contract
from agent.alibaba_endpoints import build_vision_base_url
from agent.image_gen_verification import (
    active_custom_provider_identity,
    image_gen_fingerprint as shared_image_gen_fingerprint,
    image_gen_secret_env,
    verification_state_path,
    verification_status_from_state,
)

from api.config import (
    _cfg_lock,
    _get_config_path,
    _load_yaml_config_file,
    _save_yaml_config_file,
    get_auxiliary_models,
    invalidate_models_cache,
    reload_config,
)
from api.providers import (
    _OAUTH_PROVIDERS,
    _PROVIDER_DISPLAY,
    _PROVIDER_ENV_VAR,
    _get_hermes_home,
    _load_env_file,
    _provider_has_key,
    _provider_is_oauth,
    _write_env_file,
    get_providers,
    set_provider_key,
)

logger = logging.getLogger(__name__)

_CUSTOM_MODEL_KEY_ENV = "HERMES_CUSTOM_MODEL_API_KEY"
_IMAGE_GEN_KEY_ENV: dict[str, str] = {
    "doubao": "ARK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "qianfan": "QIANFAN_API_KEY",
    "zhipu-image": "GLM_API_KEY",
    "minimax-image": "MINIMAX_API_KEY",
    "fal": "FAL_KEY",
    "openai": "OPENAI_API_KEY",
    "xai": "XAI_API_KEY",
    "krea": "KREA_API_KEY",
}
_VISION_KEY_ENV: dict[str, str] = {
    "alibaba": "DASHSCOPE_API_KEY",
    "zai": "GLM_API_KEY",
    "custom": "AUXILIARY_VISION_API_KEY",
}
_VISION_PROVIDER_META: dict[str, dict[str, Any]] = {
    "alibaba": {
        "name": "阿里百炼 Qwen-VL",
        "description": "用于上传图片、截图和表格截图理解",
        "auth_type": "api_key",
        "transport": "dashscope_openai_compatible",
        "default_model": "qwen3-vl-plus",
        "models": [
            {"id": "qwen3-vl-plus", "label": "Qwen3 VL Plus"},
            {"id": "qwen3-vl-flash", "label": "Qwen3 VL Flash"},
            {"id": "qwen2.5-vl-72b-instruct", "label": "Qwen2.5 VL 72B"},
        ],
    },
    "zai": {
        "name": "智谱 GLM-V",
        "description": "用于图片理解和视觉问答",
        "auth_type": "api_key",
        "transport": "zhipu_openai_compatible",
        "default_model": "glm-5v-turbo",
        "models": [
            {"id": "glm-5v-turbo", "label": "GLM-5V Turbo"},
            {"id": "glm-4v-plus", "label": "GLM-4V Plus"},
            {"id": "glm-4v-flash", "label": "GLM-4V Flash"},
        ],
    },
    "custom": {
        "name": "自定义国产兼容端点",
        "description": "适合私有化或 OpenAI 兼容视觉接口",
        "auth_type": "api_key",
        "transport": "openai_chat_completions",
        "default_model": "",
        "models": [],
        "requires_base_url": True,
    },
}
def _validate_provider_credential_secret_env(row: dict[str, Any]) -> str:
    expected = credential_secret_env(row.get("id"))
    actual = str(row.get("secret_env") or "").strip()
    if actual != expected:
        raise ValueError("凭据的 Secret 环境变量配置无效。")
    return actual


def _provider_credential_row(
    config_data: dict[str, Any], credential_id: str
) -> tuple[int, dict[str, Any] | None]:
    rows = config_data.get("provider_credentials")
    if not isinstance(rows, list):
        return -1, None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            row_id = normalize_credential_id(row.get("id"))
        except ValueError:
            continue
        if row_id == credential_id:
            return index, row
    return -1, None


def _replace_provider_credential_row(
    config_data: dict[str, Any],
    credential_id: str,
    replacement: dict[str, Any] | None,
    *,
    preferred_index: int = -1,
) -> None:
    existing = config_data.get("provider_credentials")
    rows = list(existing) if isinstance(existing, list) else []
    updated: list[Any] = []
    found_index = -1
    for row in rows:
        if not isinstance(row, dict):
            updated.append(row)
            continue
        try:
            row_id = normalize_credential_id(row.get("id"))
        except ValueError:
            updated.append(row)
            continue
        if row_id == credential_id:
            if found_index < 0:
                found_index = len(updated)
            continue
        updated.append(row)
    if replacement is not None:
        insert_at = found_index if found_index >= 0 else preferred_index
        if insert_at < 0 or insert_at > len(updated):
            insert_at = len(updated)
        updated.insert(insert_at, replacement)
    config_data["provider_credentials"] = updated


def _credential_env_snapshot(env_path: Path, secret_env: str) -> tuple[bool, str, bool, str]:
    file_values = _load_env_file(env_path)
    return (
        secret_env in file_values,
        str(file_values.get(secret_env) or ""),
        secret_env in os.environ,
        str(os.environ.get(secret_env) or ""),
    )


def _restore_credential_env(
    env_path: Path,
    secret_env: str,
    snapshot: tuple[bool, str, bool, str],
) -> None:
    file_present, file_value, process_present, process_value = snapshot
    _write_env_file(env_path, {secret_env: file_value if file_present else None})
    if process_present:
        os.environ[secret_env] = process_value
    else:
        os.environ.pop(secret_env, None)


def _restore_provider_credential_metadata(
    config_path: Path,
    credential_id: str,
    previous_row: dict[str, Any] | None,
    previous_index: int,
) -> None:
    with _cfg_lock:
        current = _load_yaml_config_file(config_path)
        _replace_provider_credential_row(
            current,
            credential_id,
            previous_row,
            preferred_index=previous_index,
        )
        _save_yaml_config_file(config_path, current)


def _provider_credential_used_by(config_data: dict[str, Any], credential_id: str) -> list[str]:
    used_by: list[str] = []
    auxiliary = config_data.get("auxiliary")
    vision = auxiliary.get("vision") if isinstance(auxiliary, dict) else None
    image_gen = config_data.get("image_gen")
    for path, section in (("auxiliary.vision", vision), ("image_gen", image_gen)):
        if not isinstance(section, dict):
            continue
        raw_ref = section.get("credential_ref")
        try:
            ref = normalize_credential_id(raw_ref)
        except ValueError:
            continue
        if ref == credential_id:
            used_by.append(path)
    return used_by


def _public_provider_credential(
    row: dict[str, Any],
    *,
    config_data: dict[str, Any],
) -> dict[str, Any]:
    credential_id = normalize_credential_id(row.get("id"))
    family = provider_family(row.get("provider_family"))
    secret_env = _validate_provider_credential_secret_env(row)
    label = str(row.get("label") or "").strip() or credential_id
    return {
        "id": credential_id,
        "provider_family": family,
        "label": label,
        "auth_type": str(row.get("auth_type") or "api_key").strip(),
        "default": bool(row.get("default")),
        "configured": bool(secret_env and _key_status_for_env(secret_env).get("configured")),
        "used_by": _provider_credential_used_by(config_data, credential_id),
    }


def get_provider_credentials_config() -> dict[str, Any]:
    with credential_transaction():
        config_data = _load_yaml_config_file(_get_config_path())
        rows = config_data.get("provider_credentials")
        credentials: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    credentials.append(_public_provider_credential(row, config_data=config_data))
                except ValueError:
                    continue
        return {
            "ok": True,
            "profile": _active_profile_name(),
            "credentials": credentials,
        }


def upsert_provider_credential(body: dict[str, Any]) -> dict[str, Any]:
    credential_id = normalize_credential_id(body.get("id"))
    family = provider_family(body.get("provider_family") or body.get("provider"))
    if not family:
        raise ValueError("provider_family is required")
    auth_type = str(body.get("auth_type") or "api_key").strip().lower()
    if auth_type != "api_key":
        raise ValueError("only api_key credentials are supported")
    label = str(body.get("label") or "").strip() or credential_id
    secret_env = credential_secret_env(credential_id)
    secret = body.get("api_key")
    if secret is None:
        secret = body.get("secret")
    secret_value = str(secret or "").strip()
    requested_default = body.get("default") if "default" in body else None
    if requested_default is not None and not isinstance(requested_default, bool):
        raise ValueError("default must be a boolean")
    config_path = _get_config_path()
    env_path = _get_hermes_home() / ".env"
    with credential_transaction():
        with _cfg_lock:
            config_data = _load_yaml_config_file(config_path)
        previous_index, previous_row = _provider_credential_row(config_data, credential_id)
        if previous_row is not None:
            _validate_provider_credential_secret_env(previous_row)
            previous_family = provider_family(previous_row.get("provider_family"))
            if previous_family != family:
                raise ValueError("已有凭据 ID 属于不同 Provider，请使用新的凭据 ID。")
        default_value = (
            bool(requested_default)
            if requested_default is not None
            else bool(previous_row and previous_row.get("default"))
        )
        if default_value:
            rows = config_data.get("provider_credentials")
            for row in (rows if isinstance(rows, list) else []):
                if not isinstance(row, dict) or not bool(row.get("default")):
                    continue
                try:
                    row_id = normalize_credential_id(row.get("id"))
                except ValueError:
                    continue
                if row_id != credential_id and provider_family(row.get("provider_family")) == family:
                    raise ValueError("当前 Provider 已有默认凭据，请先取消原默认凭据。")
        stored = {
            "id": credential_id,
            "provider_family": family,
            "label": label,
            "auth_type": auth_type,
            "secret_env": secret_env,
        }
        if default_value:
            stored["default"] = True
        env_snapshot = _credential_env_snapshot(env_path, secret_env)
        env_touched = False
        metadata_touched = False
        try:
            if secret_value:
                env_touched = True
                _write_env_file(env_path, {secret_env: secret_value})
            with _cfg_lock:
                latest = _load_yaml_config_file(config_path)
                _replace_provider_credential_row(
                    latest,
                    credential_id,
                    stored,
                    preferred_index=previous_index,
                )
                metadata_touched = True
                _save_yaml_config_file(config_path, latest)
            reload_config()
            public_config = get_provider_credentials_config()
            credential = next(
                row for row in public_config["credentials"] if row["id"] == credential_id
            )
            if secret_value:
                used_by = credential.get("used_by") or []
                if "auxiliary.vision" in used_by:
                    _invalidate_vision_verification()
                if "image_gen" in used_by:
                    _invalidate_image_gen_verification()
            return {"ok": True, "credential": credential}
        except Exception:
            if metadata_touched:
                try:
                    _restore_provider_credential_metadata(
                        config_path,
                        credential_id,
                        previous_row,
                        previous_index,
                    )
                except Exception:
                    logger.exception("Failed to restore provider credential metadata")
            if env_touched:
                try:
                    _restore_credential_env(env_path, secret_env, env_snapshot)
                except Exception:
                    logger.exception("Failed to restore provider credential secret")
            raise


def delete_provider_credential(credential_id: str) -> dict[str, Any]:
    normalized = normalize_credential_id(credential_id)
    config_path = _get_config_path()
    env_path = _get_hermes_home() / ".env"
    with credential_transaction():
        with _cfg_lock:
            config_data = _load_yaml_config_file(config_path)
        used_by = _provider_credential_used_by(config_data, normalized)
        if used_by:
            raise ValueError("凭据正在使用，不能删除。")
        previous_index, previous_row = _provider_credential_row(config_data, normalized)
        if previous_row is None:
            raise ValueError("凭据不存在。")
        secret_env = _validate_provider_credential_secret_env(previous_row)
        env_snapshot = _credential_env_snapshot(env_path, secret_env)
        metadata_touched = False
        env_touched = False
        try:
            with _cfg_lock:
                latest = _load_yaml_config_file(config_path)
                _replace_provider_credential_row(latest, normalized, None)
                metadata_touched = True
                _save_yaml_config_file(config_path, latest)
            env_touched = True
            _write_env_file(env_path, {secret_env: None})
            reload_config()
            return get_provider_credentials_config()
        except Exception:
            if metadata_touched:
                try:
                    _restore_provider_credential_metadata(
                        config_path,
                        normalized,
                        previous_row,
                        previous_index,
                    )
                except Exception:
                    logger.exception("Failed to restore deleted provider credential metadata")
            if env_touched:
                try:
                    _restore_credential_env(env_path, secret_env, env_snapshot)
                except Exception:
                    logger.exception("Failed to restore deleted provider credential secret")
            raise
_VISION_PROBE_MARKER = "TAIJI-VISION-CHECK-7319"
_VISION_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAkAAAAA0CAAAAABH3dgUAAABPElEQVR42u3aYRKCIBAGUO9/6TpAyO4COYrv+1cpwvKasc3jIzKRQwkEIAFIABKARAASgAQgAUgEIAFIHgLoaKT1Web41jnR8T+Ta4xXmWf1+Oi60Vxb1101/8p8zsaPan/2Xm+vAAIIIIAAAmgHQNkCVhaUnei/51E5PwKVXU9v/NFxsrUbGXekvgABBBBAAAEE0F6AMht+NaCVeFbVFyCAAAIIIIB2BxQ1DGcA9Rpf5QUVGmWZDc40JHsbX2noReNkx19Vn1TzESCAAAIIIIA2BzT6+sqb6FXrmL3OHW6is38Az64TIIAAAggggABaDygq3tsAzXz5AAIIIIAAAgigdYAyD4dXGl+VhVYbdG96qH6kKZidC0AAAQQQQADtDEgk/SNHCQQgAUgAEoBEABKABCABSAQgAUjumy803ZPAu+g+xgAAAABJRU5ErkJggg=="
)
_VISION_VERIFICATION_PUBLIC_FIELDS = {
    "ok",
    "status",
    "checked_at",
    "provider",
    "model",
    "error_code",
    "message",
    "diagnostic_id",
}
_IMAGE_GEN_VERIFICATION_PUBLIC_FIELDS = _VISION_VERIFICATION_PUBLIC_FIELDS
_IMAGE_GEN_PROBE_PROMPT = "生成一张简洁的蓝色几何图形测试图，不包含人物、文字或品牌。"
_IMAGE_GEN_FALLBACK_META: dict[str, dict[str, Any]] = {
    "doubao": {
        "name": "Doubao Seedream",
        "models": [
            {"id": "doubao-seedream-5-0-260128", "label": "Doubao Seedream 5.0 Lite"},
            {"id": "doubao-seedream-5-0-lite-260128", "label": "Doubao Seedream 5.0 Lite (alias)"},
        ],
        "default_model": "doubao-seedream-5-0-260128",
    },
    "dashscope": {
        "name": "通义 Qwen-Image",
        "models": [
            {"id": "qwen-image-2.0-pro", "label": "Qwen Image 2.0 Pro"},
            {"id": "qwen-image", "label": "Qwen Image"},
        ],
        "default_model": "qwen-image-2.0-pro",
    },
    "qianfan": {
        "name": "百度千帆",
        "models": [{"id": "qwen-image", "label": "Qwen Image"}],
        "default_model": "qwen-image",
    },
    "zhipu-image": {
        "name": "智谱 GLM-Image",
        "models": [
            {"id": "glm-image", "label": "GLM-Image"},
            {"id": "cogview-4", "label": "CogView-4"},
        ],
        "default_model": "glm-image",
    },
    "minimax-image": {
        "name": "MiniMax Image",
        "models": [{"id": "image-01", "label": "MiniMax Image-01"}],
        "default_model": "image-01",
    },
}
_DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS = {
    "doubao",
    "dashscope",
    "qianfan",
    "zhipu-image",
    "minimax-image",
}
_BLOCKED_IMAGE_GEN_PROVIDER_LABELS = {
    "fal": "FAL",
    "openai": "OpenAI",
    "openai-codex": "OpenAI 图像生成",
    "xai": "xAI",
    "krea": "Krea",
    "taiji-image": "OpenAI 图像生成",
}
_IMAGE_GEN_PUBLIC_PROVIDER_IDS = {
    "openai-codex": "taiji-image",
}
_IMAGE_GEN_INTERNAL_PROVIDER_IDS = {
    public_id: internal_id
    for internal_id, public_id in _IMAGE_GEN_PUBLIC_PROVIDER_IDS.items()
}
_BUILTIN_IMAGE_GEN_MODULES: tuple[str, ...] = (
    "plugins.image_gen.doubao",
    "plugins.image_gen.dashscope",
    "plugins.image_gen.qianfan",
    "plugins.image_gen.zhipu_image",
    "plugins.image_gen.minimax_image",
    "plugins.image_gen.fal",
    "plugins.image_gen.openai",
    "plugins.image_gen.openai-codex",
    "plugins.image_gen.xai",
    "plugins.image_gen.krea",
)


def _active_profile_name() -> str:
    try:
        from api.profiles import get_active_profile_name

        profile = get_active_profile_name()
        return str(profile or "default")
    except Exception:
        return "default"


def _public_config_summary(config_path: Any) -> dict[str, Any]:
    try:
        exists = bool(config_path.exists())
    except AttributeError:
        exists = os.path.exists(str(config_path))
    return {
        "label": "本机配置",
        "exists": exists,
        "source": "active_profile",
    }


def _safe_model_cfg(config_data: dict[str, Any]) -> dict[str, Any]:
    model_cfg = config_data.get("model")
    return model_cfg if isinstance(model_cfg, dict) else {}


def _key_status_for_env(env_var: str | None) -> dict[str, Any]:
    if not env_var:
        return {"configured": False, "source": "none", "env_var": ""}
    env_path = _get_hermes_home() / ".env"
    env_values = _load_env_file(env_path)
    if str(env_values.get(env_var) or "").strip():
        return {"configured": True, "source": "env_file", "env_var": env_var}
    if str(os.getenv(env_var) or "").strip():
        return {"configured": True, "source": "env_var", "env_var": env_var}
    return {"configured": False, "source": "none", "env_var": env_var}


def _provider_key_status(provider_id: str) -> dict[str, Any]:
    provider = (provider_id or "").strip().lower()
    if not provider:
        return {"configured": False, "source": "none", "env_var": ""}
    env_var = _PROVIDER_ENV_VAR.get(provider)
    status = _key_status_for_env(env_var)
    if status.get("configured"):
        return status
    if _provider_has_key(provider):
        return {
            "configured": True,
            "source": "oauth" if _provider_is_oauth(provider) else "config_yaml",
            "env_var": env_var or "",
        }
    return {
        "configured": False,
        "source": "oauth" if _provider_is_oauth(provider) else "none",
        "env_var": env_var or "",
    }


class _ImageGenRegisterContext:
    def register_image_gen_provider(self, provider: Any) -> None:
        from agent.image_gen_registry import register_provider

        register_provider(provider)


def _ensure_image_gen_plugins_registered() -> None:
    """Load bundled image_gen plugins so the registry can expose catalogs."""
    from agent import image_gen_registry

    registered = {provider.name for provider in image_gen_registry.list_providers()}
    expected = {
        "doubao",
        "dashscope",
        "qianfan",
        "zhipu-image",
        "minimax-image",
        "fal",
        "openai",
        "openai-codex",
        "xai",
        "krea",
    }
    if not expected.issubset(registered):
        ctx = _ImageGenRegisterContext()
        for module_name in _BUILTIN_IMAGE_GEN_MODULES:
            try:
                module = importlib.import_module(module_name)
                register = getattr(module, "register", None)
                if callable(register):
                    register(ctx)
            except Exception:
                logger.debug("Failed to register image_gen plugin %s", module_name, exc_info=True)
    try:
        from agent.custom_image_providers import register_configured_custom_image_providers

        register_configured_custom_image_providers()
    except Exception:
        logger.debug("Failed to register custom image providers", exc_info=True)


def _public_image_gen_provider_id(provider_id: str) -> str:
    provider = str(provider_id or "").strip().lower()
    return _IMAGE_GEN_PUBLIC_PROVIDER_IDS.get(provider, provider)


def _internal_image_gen_provider_id(provider_id: str) -> str:
    provider = str(provider_id or "").strip().lower()
    return _IMAGE_GEN_INTERNAL_PROVIDER_IDS.get(provider, provider)


def _image_gen_credential_fields(
    *,
    schema: dict[str, Any],
    env_var: str,
    env_vars: list[str],
) -> list[dict[str, Any]]:
    raw_fields = schema.get("credential_fields")
    fields: list[dict[str, Any]] = []
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            field_env = str(item.get("env_var") or item.get("key") or "").strip()
            field_name = str(item.get("name") or field_env.lower() or "").strip()
            if not field_name and not field_env:
                continue
            fields.append(
                {
                    "name": field_name or field_env,
                    "env_var": field_env,
                    "label": str(item.get("label") or item.get("prompt") or field_env or field_name),
                    "required": bool(item.get("required", True)),
                    "secret": bool(item.get("secret", True)),
                    "placeholder": str(item.get("placeholder") or ""),
                }
            )
    if fields:
        return fields
    keys = env_vars or ([env_var] if env_var else [])
    for key in keys:
        if not key:
            continue
        fields.append(
            {
                "name": "api_key" if key.endswith("_API_KEY") or key.endswith("_KEY") else key.lower(),
                "env_var": key,
                "label": "API 密钥" if key.endswith("_API_KEY") or key.endswith("_KEY") else key,
                "required": True,
                "secret": True,
                "placeholder": "留空保留现有密钥",
            }
        )
    return fields


def _image_gen_options_for_active(active_provider: str) -> dict[str, Any]:
    try:
        config_data = _load_yaml_config_file(_get_config_path())
    except Exception:
        return {}
    image_cfg = config_data.get("image_gen") if isinstance(config_data, dict) else None
    if not isinstance(image_cfg, dict):
        return {}
    if str(image_cfg.get("provider") or "").strip().lower() != str(active_provider or "").strip().lower():
        return {}
    options = image_cfg.get("options")
    return options if isinstance(options, dict) else {}


def _image_gen_credential_status(
    fields: list[dict[str, Any]],
    *,
    active_options: dict[str, Any] | None = None,
    secret_status_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    statuses: list[dict[str, Any]] = []
    missing: list[str] = []
    options = active_options or {}
    for field in fields:
        env_var = str(field.get("env_var") or "").strip()
        name = str(field.get("name") or env_var or "").strip()
        required = bool(field.get("required", True))
        secret = bool(field.get("secret", True))
        status = _key_status_for_env(env_var) if env_var else {"configured": False, "source": "none", "env_var": ""}
        if secret and secret_status_override is not None:
            status = secret_status_override
        if not secret and name and str(options.get(name) or "").strip():
            status = {"configured": True, "source": "config_yaml", "env_var": env_var}
        configured = bool(status.get("configured"))
        statuses.append(
            {
                "name": name,
                "env_var": env_var,
                "configured": configured,
                "source": str(status.get("source") or "none"),
                "secret": secret,
                "required": required,
            }
        )
        if required and not configured:
            missing.append(env_var or name)
    return {
        "configured": not missing if fields else True,
        "missing": missing,
        "fields": statuses,
    }


def _image_gen_primary_key_status(
    fields: list[dict[str, Any]],
    credential_status: dict[str, Any],
) -> dict[str, Any]:
    field_statuses = credential_status.get("fields") if isinstance(credential_status, dict) else []
    if isinstance(field_statuses, list):
        for field in field_statuses:
            if isinstance(field, dict) and field.get("secret") and field.get("env_var"):
                return {
                    "configured": bool(field.get("configured")),
                    "source": str(field.get("source") or "none"),
                    "env_var": str(field.get("env_var") or ""),
                }
    for field in fields:
        env_var = str(field.get("env_var") or "").strip()
        if env_var:
            return _key_status_for_env(env_var)
    return {"configured": bool(credential_status.get("configured")), "source": "none", "env_var": ""}


def _image_gen_named_key_status(
    provider_id: str,
    *,
    active: bool,
) -> dict[str, Any] | None:
    if not active:
        return None
    config_data = _load_yaml_config_file(_get_config_path())
    image_cfg = config_data.get("image_gen")
    if not isinstance(image_cfg, dict):
        return None
    credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    if not credential_ref:
        return None
    try:
        row = load_credential(credential_ref, config_data=config_data)
        if provider_family(row.get("provider_family")) != provider_family(provider_id):
            return {"configured": False, "source": "none", "env_var": ""}
        secret_env = _validate_provider_credential_secret_env(row)
        configured = bool(_key_status_for_env(secret_env).get("configured"))
    except ValueError:
        configured = False
    return {
        "configured": configured,
        "source": "provider_credential" if configured else "none",
        "env_var": "",
    }


def _image_gen_policy_allowed(pid: str, schema: dict[str, Any], *, is_custom: bool) -> tuple[bool, bool, str]:
    if is_custom:
        return True, True, "custom"
    domestic = bool(schema.get("domestic")) if "domestic" in schema else pid in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
    status = str(schema.get("integration_status") or ("stable" if pid in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS else "external")).strip().lower()
    allowed = bool(domestic and status == "stable")
    return allowed, domestic, status


def _blocked_image_gen_row(
    *,
    provider_id: str,
    public_id: str | None = None,
    active: bool = True,
    reason_code: str = "domestic_policy_required",
    status_message: str = "当前配置不符合国产策略，请切换到中国可用的稳定生图 Provider。",
) -> dict[str, Any]:
    public = public_id or _public_image_gen_provider_id(provider_id)
    contract = normalized_setup_contract(
        {"auth_type": "oauth" if provider_id == "openai-codex" else "api_key"},
        provider_family=provider_family(public),
        capabilities=("image_generation",),
        transport="policy_blocked",
    )
    return {
        "id": public,
        "name": _BLOCKED_IMAGE_GEN_PROVIDER_LABELS.get(public, _BLOCKED_IMAGE_GEN_PROVIDER_LABELS.get(provider_id, public)),
        "description": "历史非国产图片生成配置，只读显示。",
        "badge": "已阻止",
        "available": False,
        "can_attempt": False,
        "active": active,
        "requires_env": [],
        "key_status": {"configured": False, "source": "policy_blocked", "env_var": ""},
        "credential_fields": [],
        "credential_status": {"configured": False, "missing": [], "fields": []},
        "reason_code": reason_code,
        "status_message": status_message,
        "models": [],
        "default_model": "",
        "oauth_managed": provider_id == "openai-codex",
        "custom": False,
        "domestic": False,
        "integration_status": "blocked",
        "policy_blocked": True,
        **contract,
    }


def _image_gen_provider_rows(active_provider: str) -> list[dict[str, Any]]:
    readiness: dict[str, Any] = {}
    try:
        from tools.image_generation_tool import get_image_generation_readiness

        readiness = get_image_generation_readiness()
    except Exception:
        readiness = {}

    try:
        _ensure_image_gen_plugins_registered()
        from agent import image_gen_registry

        providers = image_gen_registry.list_providers()
    except Exception:
        logger.debug("Failed to list image_gen providers", exc_info=True)
        providers = []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    active_options = _image_gen_options_for_active(active_provider)
    for provider in providers:
        pid = str(getattr(provider, "name", "") or "").strip()
        if not pid:
            continue
        seen.add(pid)
        schema = {}
        models = []
        default_model = ""
        available = False
        try:
            schema = provider.get_setup_schema() or {}
        except Exception:
            schema = {}
        try:
            models = provider.list_models() or []
        except Exception:
            models = []
        try:
            default_model = str(provider.default_model() or "").strip()
        except Exception:
            default_model = ""
        try:
            available = bool(provider.is_available())
        except Exception:
            available = False
        is_custom = pid.startswith("custom:")

        env_vars = []
        for item in schema.get("env_vars") or []:
            if isinstance(item, dict) and item.get("key"):
                env_vars.append(str(item.get("key")).strip())
        env_var = _IMAGE_GEN_KEY_ENV.get(pid) or (env_vars[0] if env_vars else "")
        active = pid == active_provider
        public_pid = _public_image_gen_provider_id(pid)
        allowed, domestic, integration_status = _image_gen_policy_allowed(pid, schema, is_custom=is_custom)
        if not allowed:
            if active:
                blocked = _blocked_image_gen_row(
                    provider_id=pid,
                    public_id=public_pid,
                    active=True,
                    reason_code=str(readiness.get("reason_code") or "domestic_policy_required"),
                    status_message=str(readiness.get("public_message") or "当前配置不符合国产策略，请切换到中国可用的稳定生图 Provider。"),
                )
                rows.append(blocked)
            continue

        raw_credential_fields = _image_gen_credential_fields(
            schema=schema,
            env_var=env_var,
            env_vars=env_vars,
        )
        transport = {
            "dashscope": "dashscope_native_image_generation",
            "doubao": "volcengine_ark_images",
            "qianfan": "qianfan_images",
            "zhipu-image": "zhipu_images",
            "minimax-image": "minimax_images",
        }.get(pid, "openai_images" if is_custom else f"{pid}_images")
        contract = normalized_setup_contract(
            schema | {"credential_fields": raw_credential_fields},
            provider_family=provider_family(pid),
            capabilities=("image_generation",),
            auth_type="api_key",
            transport=transport,
            models=[
                {
                    "id": str(item.get("id") or "").strip(),
                    "label": str(
                        item.get("display")
                        or item.get("label")
                        or item.get("id")
                        or ""
                    ).strip(),
                }
                for item in models
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ],
        )
        credential_fields = contract["credential_fields"]
        named_key_status = _image_gen_named_key_status(pid, active=active)
        credential_status = _image_gen_credential_status(
            raw_credential_fields,
            active_options=active_options if active else {},
            secret_status_override=named_key_status,
        )
        key_status = named_key_status or _image_gen_primary_key_status(
            credential_fields, credential_status
        )
        if is_custom and key_status.get("configured") and default_model:
            available = True
        display_name = str(schema.get("name") or getattr(provider, "display_name", "") or pid)
        description = str(schema.get("tag") or "")
        badge = str(schema.get("badge") or ("外部" if is_custom else "国产"))
        can_attempt = bool(available)
        if active:
            available = bool(readiness.get("available"))
            reason_code = str(readiness.get("reason_code") or "")
            status_message = str(readiness.get("public_message") or "")
        else:
            available = False
            reason_code = ""
            status_message = ""
        rows.append(
            {
                "id": public_pid,
                "name": display_name,
                "description": description,
                "badge": badge,
                "available": bool(available),
                "can_attempt": can_attempt,
                "active": active,
                "requires_env": env_vars or ([env_var] if env_var else []),
                "key_status": key_status,
                "credential_fields": credential_fields,
                "credential_status": credential_status,
                "reason_code": reason_code,
                "status_message": status_message,
                "models": [
                    {
                        "id": str(item.get("id") or "").strip(),
                        "label": str(item.get("display") or item.get("id") or "").strip(),
                    }
                    for item in models
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ],
                "default_model": default_model,
                "oauth_managed": False,
                "custom": is_custom,
                "domestic": domestic,
                "integration_status": integration_status,
                "policy_blocked": False,
                **contract,
            }
        )

    for pid, env_var in _IMAGE_GEN_KEY_ENV.items():
        if pid in seen:
            continue
        active = pid == active_provider
        if pid not in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS:
            if active:
                rows.append(_blocked_image_gen_row(provider_id=pid, active=True))
            continue
        fallback = _IMAGE_GEN_FALLBACK_META.get(pid, {})
        raw_credential_fields = _image_gen_credential_fields(schema={}, env_var=env_var, env_vars=[env_var])
        contract = normalized_setup_contract(
            {"credential_fields": raw_credential_fields},
            provider_family=provider_family(pid),
            capabilities=("image_generation",),
            auth_type="api_key",
            transport={
                "dashscope": "dashscope_native_image_generation",
                "doubao": "volcengine_ark_images",
                "qianfan": "qianfan_images",
                "zhipu-image": "zhipu_images",
                "minimax-image": "minimax_images",
            }.get(pid, f"{pid}_images"),
            models=fallback.get("models") or [],
        )
        credential_fields = contract["credential_fields"]
        credential_status = _image_gen_credential_status(
            credential_fields,
            active_options=active_options if active else {},
        )
        can_attempt = bool(_key_status_for_env(env_var).get("configured"))
        public_available = bool(active and readiness.get("available"))
        rows.append(
            {
                "id": pid,
                "name": str(fallback.get("name") or pid.replace("-", " ").title()),
                "description": "",
                "badge": "国产",
                "available": public_available,
                "can_attempt": can_attempt,
                "active": active,
                "requires_env": [env_var],
                "key_status": _image_gen_primary_key_status(credential_fields, credential_status),
                "credential_fields": credential_fields,
                "credential_status": credential_status,
                "models": list(fallback.get("models") or []),
                "default_model": str(fallback.get("default_model") or ""),
                "oauth_managed": False,
                "custom": False,
                "domestic": True,
                "integration_status": "stable",
                "policy_blocked": False,
                **contract,
            }
        )
    if "openai-codex" not in seen:
        active = active_provider == "openai-codex"
        if active:
            rows.append(
                _blocked_image_gen_row(
                    provider_id="openai-codex",
                    public_id=_public_image_gen_provider_id("openai-codex"),
                    active=True,
                    reason_code=str(readiness.get("reason_code") or "domestic_policy_required"),
                    status_message=str(readiness.get("public_message") or "当前配置不符合国产策略，请切换到中国可用的稳定生图 Provider。"),
                )
            )
    return sorted(rows, key=lambda row: (not row.get("active"), row.get("id") or ""))


def get_image_gen_config() -> dict[str, Any]:
    reload_config()
    config_path = _get_config_path()
    config_data = _load_yaml_config_file(config_path)
    image_cfg = config_data.get("image_gen")
    if not isinstance(image_cfg, dict):
        image_cfg = {}
    active_provider = str(image_cfg.get("provider") or "").strip()
    active_model = str(image_cfg.get("model") or "").strip()
    active_options = image_cfg.get("options")
    if not isinstance(active_options, dict):
        active_options = {}
    public_options = {
        key: str(active_options.get(key) or "").strip()
        for key in ("endpoint_mode", "workspace_id", "region", "base_url")
        if str(active_options.get(key) or "").strip()
    }
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "image_gen": {
            "provider": _public_image_gen_provider_id(active_provider),
            "model": active_model,
            "use_gateway": bool(image_cfg.get("use_gateway")),
            "credential_ref": str(image_cfg.get("credential_ref") or "").strip(),
            "options": public_options,
            "verification": _public_image_gen_verification(
                image_cfg,
                profile=_active_profile_name(),
            ),
        },
        "providers": _image_gen_provider_rows(active_provider),
        "custom_image_providers": get_custom_image_provider_configs().get("providers", []),
    }


def _vision_key_status(
    provider_id: str,
    vision_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = str(provider_id or "").strip().lower()
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            entry = find_custom_vision_provider_entry(
                provider,
                _load_yaml_config_file(_get_config_path()),
            )
        except ImportError:
            entry = None
        if entry is not None:
            return _key_status_for_env(entry["api_key_env"])
    credential_ref = str((vision_cfg or {}).get("credential_ref") or "").strip()
    if credential_ref:
        try:
            row = load_credential(
                credential_ref,
                config_data=_load_yaml_config_file(_get_config_path()),
            )
            if provider_family(row.get("provider_family")) != provider_family(provider):
                return {"configured": False, "source": "none", "env_var": ""}
            secret_env = credential_secret_env(row.get("id"))
            if str(row.get("secret_env") or "").strip() != secret_env:
                return {"configured": False, "source": "none", "env_var": ""}
            return _key_status_for_env(secret_env)
        except ValueError:
            return {"configured": False, "source": "none", "env_var": ""}
    env_var = _VISION_KEY_ENV.get(provider)
    if env_var:
        return _key_status_for_env(env_var)
    return _provider_key_status(provider)


_VISION_STATE_LOCKS_GUARD = threading.Lock()
_VISION_STATE_LOCKS: dict[str, threading.Lock] = {}
_VISION_PROBE_GENERATIONS: dict[str, int] = {}


@dataclass(frozen=True)
class _VisionConfigSnapshot:
    profile: str
    provider: str
    model: str
    base_url: str
    api_mode: str
    credential_ref: str
    endpoint_mode: str
    region: str
    workspace_id: str
    configured: bool
    fingerprint: str


def _vision_verification_state_root() -> Path:
    from api.config import STATE_DIR

    return Path(STATE_DIR) / "vision-verification"


def _vision_verification_state_path(profile: str | None = None) -> Path:
    profile_name = str(profile or _active_profile_name() or "default")
    profile_id = hashlib.sha256(profile_name.encode("utf-8")).hexdigest()[:24]
    return _vision_verification_state_root() / f"{profile_id}.json"


def _vision_probe_image_path(profile: str) -> Path:
    return _vision_verification_state_path(profile).with_name("vision-verification-probe.png")


def _vision_profile_lock(profile: str) -> threading.Lock:
    with _VISION_STATE_LOCKS_GUARD:
        lock = _VISION_STATE_LOCKS.get(profile)
        if lock is None:
            lock = threading.Lock()
            _VISION_STATE_LOCKS[profile] = lock
        return lock


def _begin_vision_probe(profile: str) -> int:
    with _vision_profile_lock(profile):
        generation = _VISION_PROBE_GENERATIONS.get(profile, 0) + 1
        _VISION_PROBE_GENERATIONS[profile] = generation
        return generation


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _read_vision_verification_state(profile: str) -> dict[str, Any]:
    with _vision_profile_lock(profile):
        try:
            data = json.loads(
                _vision_verification_state_path(profile).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}


def _vision_secret_digest(provider: str, credential_ref: str = "") -> str:
    env_var = ""
    if credential_ref:
        try:
            row = load_credential(
                credential_ref,
                config_data=_load_yaml_config_file(_get_config_path()),
            )
            if provider_family(row.get("provider_family")) == provider_family(provider):
                expected = credential_secret_env(row.get("id"))
                if str(row.get("secret_env") or "").strip() == expected:
                    env_var = expected
        except ValueError:
            pass
    elif str(provider or "").startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            entry = find_custom_vision_provider_entry(
                provider,
                _load_yaml_config_file(_get_config_path()),
            )
        except ImportError:
            entry = None
        env_var = str((entry or {}).get("api_key_env") or "")
    else:
        env_var = _VISION_KEY_ENV.get(provider) or _PROVIDER_ENV_VAR.get(provider) or ""
    if not env_var:
        return ""
    env_values = _load_env_file(_get_hermes_home() / ".env")
    secret = str(env_values.get(env_var) or os.getenv(env_var) or "").strip()
    return hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else ""


def _vision_config_fingerprint(
    vision_cfg: dict[str, Any],
    key_status: dict[str, Any],
    *,
    profile: str,
) -> str:
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    custom_entry: dict[str, Any] = {}
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            custom_entry = find_custom_vision_provider_entry(
                provider,
                _load_yaml_config_file(_get_config_path()),
            ) or {}
        except ImportError:
            pass
    material = {
        "profile": profile,
        "provider": provider,
        "model": str(vision_cfg.get("model") or "").strip(),
        "base_url": str(vision_cfg.get("base_url") or "").strip().rstrip("/"),
        "api_mode": str(vision_cfg.get("api_mode") or "").strip(),
        "credential_ref": str(vision_cfg.get("credential_ref") or "").strip(),
        "endpoint_mode": str(vision_cfg.get("endpoint_mode") or "").strip(),
        "region": str(vision_cfg.get("region") or "").strip(),
        "workspace_id": str(vision_cfg.get("workspace_id") or "").strip(),
        "key_configured": bool(key_status.get("configured")),
        "key_digest": _vision_secret_digest(
            provider,
            str(vision_cfg.get("credential_ref") or "").strip(),
        ),
        "custom_base_url": str(custom_entry.get("base_url") or "").rstrip("/"),
        "custom_transport": str(custom_entry.get("transport") or ""),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _capture_vision_config_snapshot() -> _VisionConfigSnapshot:
    profile = _active_profile_name()
    config_data = _load_yaml_config_file(_get_config_path())
    auxiliary = config_data.get("auxiliary")
    vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            entry = find_custom_vision_provider_entry(provider, config_data) or {}
        except ImportError:
            entry = {}
        vision_cfg = dict(vision_cfg)
        vision_cfg["base_url"] = str(entry.get("base_url") or "")
        vision_cfg["api_mode"] = (
            "anthropic_messages"
            if entry.get("transport") == "anthropic_messages"
            else "chat_completions"
        )
    key_status = _vision_key_status(provider, vision_cfg)
    return _VisionConfigSnapshot(
        profile=profile,
        provider=provider,
        model=str(vision_cfg.get("model") or "").strip(),
        base_url=str(vision_cfg.get("base_url") or "").strip().rstrip("/"),
        api_mode=str(vision_cfg.get("api_mode") or "").strip(),
        credential_ref=str(vision_cfg.get("credential_ref") or "").strip(),
        endpoint_mode=str(vision_cfg.get("endpoint_mode") or "").strip(),
        region=str(vision_cfg.get("region") or "").strip(),
        workspace_id=str(vision_cfg.get("workspace_id") or "").strip(),
        configured=_vision_is_configured(vision_cfg, key_status),
        fingerprint=_vision_config_fingerprint(
            vision_cfg,
            key_status,
            profile=profile,
        ),
    )


def _vision_is_configured(vision_cfg: dict[str, Any], key_status: dict[str, Any]) -> bool:
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    model = str(vision_cfg.get("model") or "").strip()
    meta = _VISION_PROVIDER_META.get(provider) or {}
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            entry = find_custom_vision_provider_entry(provider)
        except ImportError:
            entry = None
        return bool(entry and model in entry["models"] and key_status.get("configured"))
    base_url = str(vision_cfg.get("base_url") or "").strip()
    return bool(
        provider
        and model
        and key_status.get("configured")
        and (not meta.get("requires_base_url") or base_url)
    )


def _public_vision_verification(
    vision_cfg: dict[str, Any],
    key_status: dict[str, Any],
    *,
    profile: str,
) -> dict[str, Any]:
    if not _vision_is_configured(vision_cfg, key_status):
        return {
            "status": "unconfigured",
            "checked_at": "",
            "error_code": "vision_not_configured",
            "message": "请先保存完整的识图 Provider、模型和密钥配置。",
            "diagnostic_id": "",
        }
    state = _read_vision_verification_state(profile)
    fingerprint = _vision_config_fingerprint(vision_cfg, key_status, profile=profile)
    if state.get("fingerprint") == fingerprint and state.get("status") in {"verified", "failed"}:
        return {
            "status": str(state.get("status")),
            "checked_at": str(state.get("checked_at") or ""),
            "error_code": str(state.get("error_code") or ""),
            "message": str(state.get("message") or ""),
            "diagnostic_id": str(state.get("diagnostic_id") or ""),
        }
    return {
        "status": "configured_unverified",
        "checked_at": "",
        "error_code": "",
        "message": "识图配置已保存，但尚未通过真实图片验证。",
        "diagnostic_id": "",
    }


def _invalidate_vision_verification() -> None:
    profile = _active_profile_name()
    with _vision_profile_lock(profile):
        _VISION_PROBE_GENERATIONS[profile] = _VISION_PROBE_GENERATIONS.get(profile, 0) + 1
        path = _vision_verification_state_path(profile)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _atomic_write_json(path, {})


def _invalidate_image_gen_verification() -> None:
    profile = _active_profile_name()
    with _image_gen_profile_lock(profile):
        _IMAGE_GEN_PROBE_GENERATIONS[profile] = (
            _IMAGE_GEN_PROBE_GENERATIONS.get(profile, 0) + 1
        )
        path = _image_gen_verification_state_path(profile)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _atomic_write_json(path, {})

def _vision_test_response(
    *,
    ok: bool,
    status: str,
    checked_at: str,
    provider: str,
    model: str,
    error_code: str,
    message: str,
    diagnostic_id: str,
) -> dict[str, Any]:
    response = {
        "ok": bool(ok),
        "status": status,
        "checked_at": checked_at,
        "provider": provider,
        "model": model,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    return {key: response[key] for key in _VISION_VERIFICATION_PUBLIC_FIELDS}


def test_vision_config() -> dict[str, Any]:
    reload_config()
    snapshot = _capture_vision_config_snapshot()
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    diagnostic_id = uuid.uuid4().hex
    if not snapshot.configured:
        return _vision_test_response(
            ok=False,
            status="unconfigured",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="vision_not_configured",
            message="请先保存完整的识图 Provider、模型和密钥配置。",
            diagnostic_id=diagnostic_id,
        )

    generation = _begin_vision_probe(snapshot.profile)
    error_code = ""
    message = "识图验证通过，当前配置已完成真实图片探测。"
    ok = False
    try:
        from tools.vision_tools import vision_analyze_tool

        probe_path = _vision_probe_image_path(snapshot.profile)
        if not probe_path.exists() or probe_path.read_bytes() != _VISION_PROBE_PNG:
            _atomic_write_bytes(probe_path, _VISION_PROBE_PNG)
        prompt = (
            "请只识别图片中的大写英文、数字和连字符标记。"
            "不要猜测或补全，请在回复中完整包含你真实看到的标记。"
        )

        async def _run_probe() -> str:
            return await vision_analyze_tool(
                image_url=str(probe_path),
                user_prompt=prompt,
                model=snapshot.model,
                provider=snapshot.provider,
                strict_target=True,
            )

        result = json.loads(asyncio.run(_run_probe()))
        analysis = str(result.get("analysis") or "") if isinstance(result, dict) else ""
        ok = bool(
            isinstance(result, dict)
            and result.get("success")
            and result.get("resolved_provider") == snapshot.provider
            and result.get("resolved_model") == snapshot.model
            and _VISION_PROBE_MARKER in analysis
        )
        if not ok:
            error_code = "vision_probe_failed"
            message = "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"
    except Exception:
        logger.warning("Vision configuration probe failed (%s)", diagnostic_id)
        error_code = "vision_probe_failed"
        message = "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"

    status = "verified" if ok else "failed"
    state = {
        "fingerprint": snapshot.fingerprint,
        "status": status,
        "checked_at": checked_at,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    current_snapshot = _capture_vision_config_snapshot()
    with _vision_profile_lock(snapshot.profile):
        still_current = (
            _VISION_PROBE_GENERATIONS.get(snapshot.profile) == generation
            and current_snapshot == snapshot
        )
        if still_current:
            _atomic_write_json(
                _vision_verification_state_path(snapshot.profile),
                state,
            )
    if not still_current:
        return _vision_test_response(
            ok=False,
            status="configured_unverified",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="vision_probe_superseded",
            message="识图配置在验证期间已变更，本次结果已忽略，请重新测试。",
            diagnostic_id=diagnostic_id,
        )
    return _vision_test_response(
        ok=ok,
        status=status,
        checked_at=checked_at,
        provider=snapshot.provider,
        model=snapshot.model,
        error_code=error_code,
        message=message,
        diagnostic_id=diagnostic_id,
    )


_IMAGE_GEN_STATE_LOCKS_GUARD = threading.Lock()
_IMAGE_GEN_STATE_LOCKS: dict[str, threading.Lock] = {}
_IMAGE_GEN_PROBE_GENERATIONS: dict[str, int] = {}


@dataclass(frozen=True)
class _ImageGenConfigSnapshot:
    profile: str
    provider: str
    model: str
    credential_ref: str
    endpoint_mode: str
    region: str
    workspace_id: str
    base_url: str
    configured: bool
    fingerprint: str


def _image_gen_verification_state_root() -> Path:
    from api.config import STATE_DIR

    return Path(STATE_DIR) / "image-gen-verification"


def _image_gen_verification_state_path(profile: str | None = None) -> Path:
    profile_name = str(profile or _active_profile_name() or "default")
    return verification_state_path(_image_gen_verification_state_root(), profile_name)


def _image_gen_profile_lock(profile: str) -> threading.Lock:
    with _IMAGE_GEN_STATE_LOCKS_GUARD:
        lock = _IMAGE_GEN_STATE_LOCKS.get(profile)
        if lock is None:
            lock = threading.Lock()
            _IMAGE_GEN_STATE_LOCKS[profile] = lock
        return lock


def _begin_image_gen_probe(profile: str, state: dict[str, Any]) -> int:
    with _image_gen_profile_lock(profile):
        generation = _IMAGE_GEN_PROBE_GENERATIONS.get(profile, 0) + 1
        _IMAGE_GEN_PROBE_GENERATIONS[profile] = generation
        _atomic_write_json(_image_gen_verification_state_path(profile), state)
        return generation


def _read_image_gen_verification_state(profile: str) -> dict[str, Any]:
    with _image_gen_profile_lock(profile):
        try:
            data = json.loads(
                _image_gen_verification_state_path(profile).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}


def _image_gen_secret_value(
    provider: str,
    credential_ref: str,
    config_data: dict[str, Any],
) -> str:
    env_var = image_gen_secret_env(provider, credential_ref, config_data)
    if not env_var:
        return ""
    env_values = _load_env_file(_get_hermes_home() / ".env")
    return str(env_values.get(env_var) or os.getenv(env_var) or "").strip()


def _image_gen_config_fingerprint(
    image_cfg: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any] | None = None,
) -> str:
    data = config_data or _load_yaml_config_file(_get_config_path())
    provider = str(image_cfg.get("provider") or "").strip().lower()
    credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    return shared_image_gen_fingerprint(
        image_cfg,
        profile=profile,
        config_data=data,
        secret_value=_image_gen_secret_value(provider, credential_ref, data),
    )


def _capture_image_gen_config_snapshot() -> _ImageGenConfigSnapshot:
    profile = _active_profile_name()
    config_data = _load_yaml_config_file(_get_config_path())
    image_cfg = config_data.get("image_gen")
    if not isinstance(image_cfg, dict):
        image_cfg = {}
    provider = str(image_cfg.get("provider") or "").strip().lower()
    model = str(image_cfg.get("model") or "").strip()
    options = image_cfg.get("options")
    if not isinstance(options, dict):
        options = {}
    credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    secret_value = _image_gen_secret_value(provider, credential_ref, config_data)
    credential_required = provider in _IMAGE_GEN_KEY_ENV or provider.startswith("custom:")
    custom_complete = bool(
        not provider.startswith("custom:")
        or active_custom_provider_identity(provider, config_data)
    )
    endpoint_mode = str(options.get("endpoint_mode") or "").strip()
    endpoint_complete = bool(
        endpoint_mode not in {"workspace", "custom"}
        or (
            endpoint_mode == "workspace"
            and str(options.get("workspace_id") or "").strip()
        )
        or (
            endpoint_mode == "custom"
            and str(options.get("base_url") or "").strip()
        )
    )
    configured = bool(
        provider
        and model
        and custom_complete
        and endpoint_complete
        and (not credential_required or secret_value)
    )
    return _ImageGenConfigSnapshot(
        profile=profile,
        provider=provider,
        model=model,
        credential_ref=credential_ref,
        endpoint_mode=endpoint_mode,
        region=str(options.get("region") or "").strip(),
        workspace_id=str(options.get("workspace_id") or "").strip(),
        base_url=str(options.get("base_url") or "").strip().rstrip("/"),
        configured=configured,
        fingerprint=_image_gen_config_fingerprint(
            image_cfg,
            profile=profile,
            config_data=config_data,
        ),
    )


def _public_image_gen_verification(
    image_cfg: dict[str, Any],
    *,
    profile: str,
) -> dict[str, Any]:
    snapshot = _capture_image_gen_config_snapshot()
    if not snapshot.configured:
        return {
            "status": "unconfigured",
            "checked_at": "",
            "error_code": "image_gen_not_configured",
            "message": "请先保存完整的生图 Provider、模型和凭据配置。",
            "diagnostic_id": "",
        }
    state = _read_image_gen_verification_state(profile)
    fingerprint = _image_gen_config_fingerprint(image_cfg, profile=profile)
    persisted_status = verification_status_from_state(
        state, expected_fingerprint=fingerprint
    )
    if persisted_status in {"verifying", "verified", "failed"}:
        return {
            "status": persisted_status,
            "checked_at": str(state.get("checked_at") or ""),
            "error_code": str(state.get("error_code") or ""),
            "message": str(state.get("message") or ""),
            "diagnostic_id": str(state.get("diagnostic_id") or ""),
        }
    return {
        "status": "configured_unverified",
        "checked_at": "",
        "error_code": "",
        "message": "生图配置已保存，但尚未通过真实生成验证。",
        "diagnostic_id": "",
    }


def _image_gen_test_response(
    *,
    ok: bool,
    status: str,
    checked_at: str,
    provider: str,
    model: str,
    error_code: str,
    message: str,
    diagnostic_id: str,
) -> dict[str, Any]:
    response = {
        "ok": bool(ok),
        "status": status,
        "checked_at": checked_at,
        "provider": provider,
        "model": model,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    return {key: response[key] for key in _IMAGE_GEN_VERIFICATION_PUBLIC_FIELDS}


@dataclass(frozen=True)
class _ImageCacheSnapshot:
    root: Path
    lexical_root: Path
    paths: frozenset[Path]
    inodes: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class _OwnedProbeImage:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class _ProbeCleanupCandidate:
    path: Path
    device: int
    inode: int
    file_type: int


def _snapshot_image_cache() -> _ImageCacheSnapshot:
    lexical_root = Path(
        os.path.abspath(_get_hermes_home() / "cache" / "images")
    )
    root = lexical_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    inodes: set[tuple[int, int]] = set()
    for current_root, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(current_root) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            paths.add(path)
            inodes.add((info.st_dev, info.st_ino))
    return _ImageCacheSnapshot(
        root=root,
        lexical_root=lexical_root,
        paths=frozenset(paths),
        inodes=frozenset(inodes),
    )


def _probe_cleanup_candidate(
    raw_path: Any,
    before: _ImageCacheSnapshot,
) -> _ProbeCleanupCandidate | None:
    value = str(raw_path or "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None
    requested = Path(os.path.abspath(candidate))
    try:
        relative = requested.relative_to(before.lexical_root)
    except ValueError:
        try:
            relative = requested.relative_to(before.root)
        except ValueError:
            return None
    lexical = before.root / relative
    if lexical in before.paths:
        return None
    current = before.root
    try:
        for part in relative.parts[:-1]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return None
        info = lexical.lstat()
    except OSError:
        return None
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        return None
    return _ProbeCleanupCandidate(
        path=lexical,
        device=info.st_dev,
        inode=info.st_ino,
        file_type=stat.S_IFMT(info.st_mode),
    )


def _owned_probe_image(
    candidate: _ProbeCleanupCandidate | None,
    before: _ImageCacheSnapshot,
) -> _OwnedProbeImage | None:
    if candidate is None or candidate.file_type != stat.S_IFREG:
        return None
    try:
        info = candidate.path.lstat()
        resolved = candidate.path.resolve(strict=True)
        resolved.relative_to(before.root)
    except (OSError, RuntimeError, ValueError):
        return None
    identity = (info.st_dev, info.st_ino)
    if (
        identity != (candidate.device, candidate.inode)
        or identity in before.inodes
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        return None
    return _OwnedProbeImage(path=resolved, device=info.st_dev, inode=info.st_ino)


def _owned_probe_image_header(image: _OwnedProbeImage) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(image.path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (image.device, image.inode)
        ):
            return b""
        return os.read(descriptor, 12)
    finally:
        os.close(descriptor)


def _has_safe_image_header(image: _OwnedProbeImage) -> bool:
    try:
        header = _owned_probe_image_header(image)
    except OSError:
        return False
    return bool(
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def _remove_probe_cleanup_candidate(candidate: _ProbeCleanupCandidate) -> bool:
    try:
        info = candidate.path.lstat()
        if (
            (info.st_dev, info.st_ino) != (candidate.device, candidate.inode)
            or stat.S_IFMT(info.st_mode) != candidate.file_type
        ):
            return False
        candidate.path.unlink()
        return True
    except OSError:
        return False


def test_image_gen_config() -> dict[str, Any]:
    reload_config()
    snapshot = _capture_image_gen_config_snapshot()
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    diagnostic_id = uuid.uuid4().hex
    if not snapshot.configured:
        return _image_gen_test_response(
            ok=False,
            status="unconfigured",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="image_gen_not_configured",
            message="请先保存完整的生图 Provider、模型和凭据配置。",
            diagnostic_id=diagnostic_id,
        )

    verifying_state = {
        "fingerprint": snapshot.fingerprint,
        "status": "verifying",
        "checked_at": checked_at,
        "error_code": "",
        "message": "正在执行真实生图测试，可能产生少量费用。",
        "diagnostic_id": diagnostic_id,
    }
    generation = _begin_image_gen_probe(snapshot.profile, verifying_state)

    ok = False
    error_code = "image_gen_probe_failed"
    message = "生图验证失败，请检查网络、凭据、模型和账号状态后重试。"
    generated_image: _OwnedProbeImage | None = None
    cleanup_candidate: _ProbeCleanupCandidate | None = None
    try:
        cache_before = _snapshot_image_cache()
        from agent.image_gen_registry import get_provider

        selected = get_provider(snapshot.provider)
        can_attempt = bool(selected and selected.is_available())
        if not can_attempt:
            error_code = "image_gen_provider_unavailable"
            message = "生图配置已保存，但当前 Provider 或凭据暂不可用。"
            result = None
        else:
            result = selected.generate(
                prompt=_IMAGE_GEN_PROBE_PROMPT,
                aspect_ratio="square",
                num_images=1,
                model=snapshot.model,
            )
        if isinstance(result, dict):
            cleanup_candidate = _probe_cleanup_candidate(result.get("image"), cache_before)
            generated_image = _owned_probe_image(cleanup_candidate, cache_before)
            identity_ok = bool(
                result.get("success") is True
                and result.get("provider") == snapshot.provider
                and result.get("model") == snapshot.model
            )
            if identity_ok and generated_image is None:
                error_code = "image_gen_invalid_file"
            elif identity_ok and not _has_safe_image_header(generated_image):
                error_code = "image_gen_invalid_file"
            elif identity_ok:
                ok = True
                error_code = ""
                message = "生图验证通过，当前配置已完成真实生成探测。"
    except Exception:
        logger.warning("Image generation configuration probe failed (%s)", diagnostic_id)
    finally:
        if cleanup_candidate is not None:
            removed = _remove_probe_cleanup_candidate(cleanup_candidate)
            if not removed:
                logger.warning("Failed to remove image generation probe output (%s)", diagnostic_id)
                ok = False
                error_code = "image_gen_cleanup_failed"
                message = "生图验证未能安全清理测试图片，请检查本地文件权限后重试。"

    status = "verified" if ok else "failed"
    state = {
        "fingerprint": snapshot.fingerprint,
        "status": status,
        "checked_at": checked_at,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    current_snapshot = _capture_image_gen_config_snapshot()
    with _image_gen_profile_lock(snapshot.profile):
        still_current = (
            _IMAGE_GEN_PROBE_GENERATIONS.get(snapshot.profile) == generation
            and current_snapshot == snapshot
        )
        if still_current:
            _atomic_write_json(_image_gen_verification_state_path(snapshot.profile), state)
    if not still_current:
        return _image_gen_test_response(
            ok=False,
            status="configured_unverified",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="image_gen_probe_superseded",
            message="生图配置在验证期间已变更，本次结果已忽略，请重新测试。",
            diagnostic_id=diagnostic_id,
        )
    return _image_gen_test_response(
        ok=ok,
        status=status,
        checked_at=checked_at,
        provider=snapshot.provider,
        model=snapshot.model,
        error_code=error_code,
        message=message,
        diagnostic_id=diagnostic_id,
    )


def _vision_provider_rows(active_provider: str, vision_cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    active = str(active_provider or "").strip().lower()
    for pid, meta in _VISION_PROVIDER_META.items():
        key_status = _vision_key_status(pid, vision_cfg if pid == active else None)
        requires_base_url = bool(meta.get("requires_base_url"))
        active_base_url = str((vision_cfg or {}).get("base_url") or "").strip() if pid == active else ""
        fields = []
        env_var = _VISION_KEY_ENV.get(pid, "")
        if env_var:
            fields.append(
                credential_field(
                    name="api_key",
                    env_var=env_var,
                    label="API Key",
                )
            )
        endpoint_fields: list[dict[str, Any]] = []
        if pid == "alibaba":
            endpoint_fields = [
                credential_field(name="endpoint_mode", env_var="", label="接入方式", required=False, secret=False),
                credential_field(name="workspace_id", env_var="", label="Workspace ID", required=False, secret=False),
                credential_field(name="region", env_var="", label="地域", required=False, secret=False),
                credential_field(name="base_url", env_var="", label="Base URL", required=False, secret=False),
            ]
        elif requires_base_url:
            endpoint_fields = [
                credential_field(name="base_url", env_var="", label="Base URL", required=True, secret=False)
            ]
        contract = normalized_setup_contract(
            {
                "auth_type": meta.get("auth_type", "api_key"),
                "transport": meta.get("transport", ""),
                "credential_fields": fields + endpoint_fields,
            },
            provider_family=provider_family(pid),
            capabilities=("vision",),
            transport=str(meta.get("transport") or ""),
            models=meta.get("models") or [],
        )
        rows.append(
            {
                "id": pid,
                "name": str(meta.get("name") or pid),
                "description": str(meta.get("description") or ""),
                "active": pid == active,
                "available": bool(key_status.get("configured") and (not requires_base_url or active_base_url)),
                "key_status": key_status,
                "requires_env": [_VISION_KEY_ENV[pid]] if pid in _VISION_KEY_ENV else [],
                "requires_base_url": requires_base_url,
                "models": list(meta.get("models") or []),
                "default_model": str(meta.get("default_model") or ""),
                **contract,
            }
        )
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_public_row,
            load_custom_vision_provider_entries,
        )

        custom_rows = [
            custom_vision_provider_public_row(entry, active_provider=active)
            for entry in load_custom_vision_provider_entries(
                _load_yaml_config_file(_get_config_path())
            )
        ]
    except ImportError:
        custom_rows = []
    for row in custom_rows:
        row["key_status"] = _key_status_for_env(row["key_status"]["env_var"])
        row["available"] = bool(row["key_status"].get("configured"))
        custom_contract = normalized_setup_contract(
            {
                "auth_type": "api_key",
                "transport": row.get("transport") or "openai_chat_completions",
                "credential_fields": [
                    credential_field(
                        name="api_key",
                        env_var=str(row["key_status"].get("env_var") or ""),
                        label="API Key",
                    )
                ],
            },
            provider_family=str(row.get("id") or ""),
            capabilities=("vision",),
            transport=str(row.get("transport") or "openai_chat_completions"),
            models=row.get("models") or [],
        )
        row.update(custom_contract)
        rows.append(row)
    custom_ids = {str(row.get("id") or "") for row in custom_rows}
    if active and active not in _VISION_PROVIDER_META and active not in custom_ids and active != "auto":
        key_status = _vision_key_status(active, vision_cfg)
        legacy_contract = normalized_setup_contract(
            {"credential_fields": []},
            provider_family=provider_family(active),
            capabilities=("vision",),
            transport="legacy_vision",
        )
        rows.append(
            {
                "id": active,
                "name": _PROVIDER_DISPLAY.get(active, active),
                "description": "当前配置中的视觉模型",
                "active": True,
                "available": bool(key_status.get("configured")),
                "key_status": key_status,
                "requires_env": [key_status.get("env_var")] if key_status.get("env_var") else [],
                "requires_base_url": False,
                "models": [],
                "default_model": "",
                **legacy_contract,
            }
        )
    return rows


def get_vision_config() -> dict[str, Any]:
    reload_config()
    config_path = _get_config_path()
    config_data = _load_yaml_config_file(config_path)
    auxiliary = config_data.get("auxiliary")
    vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    model = str(vision_cfg.get("model") or "").strip()
    base_url = str(vision_cfg.get("base_url") or "").strip()
    api_mode = str(vision_cfg.get("api_mode") or "").strip()
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            entry = find_custom_vision_provider_entry(provider, config_data) or {}
        except ImportError:
            entry = {}
        base_url = str(entry.get("base_url") or "")
        api_mode = (
            "anthropic_messages"
            if entry.get("transport") == "anthropic_messages"
            else ("chat_completions" if entry else "")
        )
    key_status = _vision_key_status(provider, vision_cfg)
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "vision": {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_mode": api_mode,
            "credential_ref": str(vision_cfg.get("credential_ref") or "").strip(),
            "endpoint_mode": str(vision_cfg.get("endpoint_mode") or "").strip(),
            "region": str(vision_cfg.get("region") or "").strip(),
            "workspace_id": str(vision_cfg.get("workspace_id") or "").strip(),
            "key_status": key_status,
            "verification": _public_vision_verification(
                vision_cfg,
                key_status,
                profile=_active_profile_name(),
            ),
        },
        "providers": _vision_provider_rows(provider, vision_cfg),
    }


def set_vision_config(body: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(body.get("provider") or "").strip().lower()
    model_id = str(body.get("model") or "").strip()
    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    api_key = body.get("api_key")
    credential_ref = str(body.get("credential_ref") or "").strip()
    if not provider_id:
        raise ValueError("provider is required")
    named_custom_entry: dict[str, Any] | None = None
    if provider_id.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            named_custom_entry = find_custom_vision_provider_entry(provider_id)
        except ImportError:
            named_custom_entry = None
    if provider_id not in _VISION_PROVIDER_META and named_custom_entry is None:
        raise ValueError(f"unknown vision provider: {provider_id}")
    if (
        provider_id == "alibaba"
        and credential_ref
        and api_key is not None
        and str(api_key).strip()
    ):
        raise ValueError("credential_ref and api_key cannot be used together")

    meta = _VISION_PROVIDER_META.get(provider_id) or {
        "default_model": named_custom_entry["default_model"],
        "models": [{"id": item} for item in named_custom_entry["models"]],
    }
    if not model_id:
        model_id = str(meta.get("default_model") or "").strip()
        models = meta.get("models") if isinstance(meta.get("models"), list) else []
        if not model_id and models:
            model_id = str((models[0] or {}).get("id") or "").strip()
    if not model_id:
        raise ValueError("model is required")
    if provider_id == "alibaba":
        allowed_models = {
            str(row.get("id") or "").strip()
            for row in meta.get("models", [])
            if isinstance(row, dict)
        }
        if model_id not in allowed_models:
            raise ValueError(f"unknown Alibaba vision model: {model_id}")
        endpoint_mode = str(body.get("endpoint_mode") or "public").strip().lower()
        region = str(body.get("region") or "cn-beijing").strip().lower()
        workspace_id = str(body.get("workspace_id") or "").strip().lower()
        base_url = build_vision_base_url(
            endpoint_mode=endpoint_mode,
            region=region,
            workspace_prefix=workspace_id,
            custom_url=base_url,
        )
        if not credential_ref and not str(api_key or "").strip():
            credential_ref = default_credential_ref(
                provider_id,
                config_data=_load_yaml_config_file(_get_config_path()),
            )
        if credential_ref:
            credential_ref = normalize_credential_id(credential_ref)
    elif named_custom_entry is not None:
        if api_key is not None and str(api_key).strip():
            raise ValueError("命名式外部识图密钥请在 Provider 管理中更新。")
        if model_id not in named_custom_entry["models"]:
            raise ValueError(f"unknown custom vision model: {model_id}")
    if bool(meta.get("requires_base_url")) and not base_url:
        raise ValueError("base_url is required for custom vision provider")

    config_path = _get_config_path()
    with credential_transaction():
        with _cfg_lock:
            config_data = _load_yaml_config_file(config_path)
            if credential_ref:
                row = load_credential(credential_ref, config_data=config_data)
                if provider_family(row.get("provider_family")) != provider_family(provider_id):
                    raise ValueError("所选凭据不属于当前 Provider。")
            env_var = _VISION_KEY_ENV.get(provider_id)
            secret_value = str(api_key or "").strip()
            if secret_value and not env_var:
                raise ValueError(f"{provider_id} does not accept an API key from WebUI")
            env_path = _get_hermes_home() / ".env"
            env_snapshot = _credential_env_snapshot(env_path, env_var) if secret_value else None
            env_touched = False
            auxiliary = config_data.get("auxiliary")
            if not isinstance(auxiliary, dict):
                auxiliary = {}
            vision_cfg = auxiliary.get("vision")
            if not isinstance(vision_cfg, dict):
                vision_cfg = {}
            vision_cfg["provider"] = provider_id
            vision_cfg["model"] = model_id
            if provider_id == "alibaba":
                vision_cfg["credential_ref"] = credential_ref
                vision_cfg["endpoint_mode"] = endpoint_mode
                vision_cfg["region"] = region
                vision_cfg["workspace_id"] = workspace_id
                vision_cfg["base_url"] = base_url
                vision_cfg.pop("api_key", None)
                vision_cfg.pop("api_mode", None)
            elif provider_id == "custom":
                vision_cfg["base_url"] = base_url
                has_custom_key = (
                    (api_key is not None and str(api_key).strip())
                    or _key_status_for_env("AUXILIARY_VISION_API_KEY").get("configured")
                )
                if has_custom_key:
                    vision_cfg["api_key"] = "${AUXILIARY_VISION_API_KEY}"
                else:
                    vision_cfg.pop("api_key", None)
            else:
                vision_cfg.pop("base_url", None)
                vision_cfg.pop("api_key", None)
                vision_cfg.pop("api_mode", None)
            if provider_id != "alibaba":
                vision_cfg.pop("credential_ref", None)
                vision_cfg.pop("endpoint_mode", None)
                vision_cfg.pop("region", None)
                vision_cfg.pop("workspace_id", None)
            auxiliary["vision"] = vision_cfg
            config_data["auxiliary"] = auxiliary
            try:
                if secret_value and env_var:
                    env_touched = True
                    _write_env_file(env_path, {env_var: secret_value})
                _save_yaml_config_file(config_path, config_data)
            except Exception:
                if env_touched and env_var and env_snapshot is not None:
                    _restore_credential_env(env_path, env_var, env_snapshot)
                raise
    reload_config()
    invalidate_models_cache()
    _invalidate_vision_verification()
    return get_vision_config()


def get_custom_vision_provider_configs() -> dict[str, Any]:
    config_data = _load_yaml_config_file(_get_config_path())
    auxiliary = config_data.get("auxiliary")
    vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
    active_provider = str((vision_cfg or {}).get("provider") or "").strip().lower()
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_public_row,
            load_custom_vision_provider_entries,
        )
    except ImportError:
        return {"ok": True, "providers": []}
    rows = [
        custom_vision_provider_public_row(entry, active_provider=active_provider)
        for entry in load_custom_vision_provider_entries(config_data)
    ]
    for row in rows:
        row["key_status"] = _key_status_for_env(row["key_status"]["env_var"])
        row["available"] = bool(row["key_status"].get("configured"))
    return {"ok": True, "providers": rows}


def set_custom_vision_provider_config(body: dict[str, Any]) -> dict[str, Any]:
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_public_row,
            is_custom_vision_base_url_safe,
            normalize_custom_vision_provider_entry,
            normalize_custom_vision_provider_id,
        )
    except ImportError as exc:
        raise RuntimeError("custom vision provider support is unavailable") from exc

    requested_id = normalize_custom_vision_provider_id(body.get("id") or body.get("provider_id"))
    api_key = body.get("api_key")
    config_path = _get_config_path()
    env_path = _get_hermes_home() / ".env"
    with credential_transaction():
        with _cfg_lock:
            config_data = _load_yaml_config_file(config_path)
            existing_entries = config_data.get("custom_vision_providers")
            if not isinstance(existing_entries, list):
                existing_entries = []
            existing = {}
            for item in existing_entries:
                if not isinstance(item, dict):
                    continue
                try:
                    item_id = normalize_custom_vision_provider_id(item.get("id"))
                except ValueError:
                    continue
                if item_id == requested_id:
                    existing = item
                    break
            merged = dict(existing)
            merged.update({key: value for key, value in body.items() if key != "api_key"})
            merged["id"] = requested_id
            normalized = normalize_custom_vision_provider_entry(merged)
            if not is_custom_vision_base_url_safe(normalized["base_url"]):
                raise ValueError("外部识图 Base URL 无法通过公网安全校验。")
            env_snapshot = _credential_env_snapshot(env_path, normalized["api_key_env"])
            env_touched = False
            try:
                if api_key is not None and str(api_key).strip():
                    env_touched = True
                    _write_env_file(
                        env_path,
                        {normalized["api_key_env"]: str(api_key).strip()},
                    )
                updated = []
                for item in existing_entries:
                    if not isinstance(item, dict):
                        continue
                    try:
                        item_id = normalize_custom_vision_provider_id(item.get("id"))
                    except ValueError:
                        continue
                    if item_id != requested_id:
                        updated.append(item)
                updated.append(normalized)
                config_data["custom_vision_providers"] = updated
                _save_yaml_config_file(config_path, config_data)
            except Exception:
                if env_touched:
                    _restore_credential_env(
                        env_path,
                        normalized["api_key_env"],
                        env_snapshot,
                    )
                raise
    reload_config()
    invalidate_models_cache()
    _invalidate_vision_verification()
    row = custom_vision_provider_public_row(normalized)
    row["key_status"] = _key_status_for_env(normalized["api_key_env"])
    row["available"] = bool(row["key_status"].get("configured"))
    return {
        "ok": True,
        "provider": row,
        "providers": get_custom_vision_provider_configs()["providers"],
    }


def delete_custom_vision_provider_config(provider_id: str) -> dict[str, Any]:
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_env_var,
            custom_vision_provider_name,
            normalize_custom_vision_provider_id,
        )
    except ImportError as exc:
        raise RuntimeError("custom vision provider support is unavailable") from exc
    normalized_id = normalize_custom_vision_provider_id(provider_id)
    provider_name = custom_vision_provider_name(normalized_id)
    config_path = _get_config_path()
    env_path = _get_hermes_home() / ".env"
    secret_env = custom_vision_provider_env_var(normalized_id)
    with credential_transaction():
        with _cfg_lock:
            config_data = _load_yaml_config_file(config_path)
            auxiliary = config_data.get("auxiliary")
            vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
            if str((vision_cfg or {}).get("provider") or "").strip().lower() == provider_name:
                raise ValueError("该外部识图 Provider 正在使用，请先切换识图配置。")
            entries = config_data.get("custom_vision_providers")
            if not isinstance(entries, list):
                entries = []
            updated = []
            removed = False
            for item in entries:
                if not isinstance(item, dict):
                    continue
                try:
                    item_id = normalize_custom_vision_provider_id(item.get("id"))
                except ValueError:
                    continue
                if item_id == normalized_id:
                    removed = True
                else:
                    updated.append(item)
            if not removed:
                raise ValueError("外部识图 Provider 不存在。")
            env_snapshot = _credential_env_snapshot(env_path, secret_env)
            env_touched = False
            try:
                env_touched = True
                _write_env_file(env_path, {secret_env: None})
                config_data["custom_vision_providers"] = updated
                _save_yaml_config_file(config_path, config_data)
            except Exception:
                if env_touched:
                    _restore_credential_env(env_path, secret_env, env_snapshot)
                raise
    reload_config()
    invalidate_models_cache()
    return get_custom_vision_provider_configs()


def get_custom_image_provider_configs() -> dict[str, Any]:
    config_path = _get_config_path()
    config_data = _load_yaml_config_file(config_path)
    active_provider = ""
    image_cfg = config_data.get("image_gen")
    if isinstance(image_cfg, dict):
        active_provider = str(image_cfg.get("provider") or "").strip()
    try:
        from agent.custom_image_providers import (
            custom_image_provider_public_row,
            load_custom_image_provider_entries,
        )
    except Exception:
        return {"ok": True, "providers": []}
    rows = [
        custom_image_provider_public_row(entry, active_provider=active_provider)
        for entry in load_custom_image_provider_entries(config_data)
    ]
    for row in rows:
        key_status = _key_status_for_env((row.get("key_status") or {}).get("env_var"))
        row["key_status"] = key_status
        row["available"] = bool(key_status.get("configured") and row.get("base_url_configured") and row.get("default_model"))
    return {"ok": True, "providers": rows}


def set_custom_image_provider_config(body: dict[str, Any]) -> dict[str, Any]:
    try:
        from agent.custom_image_providers import (
            custom_image_provider_public_row,
            normalize_custom_image_provider_entry,
            normalize_custom_image_provider_id,
        )
    except Exception as exc:
        raise RuntimeError("custom image provider support is unavailable") from exc

    requested_id = normalize_custom_image_provider_id(body.get("id") or body.get("provider_id"))
    config_path = _get_config_path()
    api_key = body.get("api_key")
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        existing_entries = config_data.get("custom_image_providers")
        if not isinstance(existing_entries, list):
            existing_entries = []
        existing = {}
        for item in existing_entries:
            if not isinstance(item, dict):
                continue
            try:
                item_id = normalize_custom_image_provider_id(item.get("id"))
            except ValueError:
                continue
            if item_id == requested_id:
                existing = item
                break
        merged = dict(existing)
        for key, value in body.items():
            if key != "api_key":
                merged[key] = value
        merged["id"] = requested_id
        normalized = normalize_custom_image_provider_entry(merged)
        if api_key is not None and str(api_key).strip():
            _write_env_file(_get_hermes_home() / ".env", {normalized["api_key_env"]: str(api_key).strip()})

        updated = []
        for item in existing_entries:
            if not isinstance(item, dict):
                continue
            try:
                item_id = normalize_custom_image_provider_id(item.get("id"))
            except ValueError:
                continue
            if item_id != requested_id:
                updated.append(item)
        updated.append(normalized)
        config_data["custom_image_providers"] = updated
        _save_yaml_config_file(config_path, config_data)
    reload_config()
    invalidate_models_cache()
    _invalidate_image_gen_verification()
    row = custom_image_provider_public_row(normalized)
    return {"ok": True, "provider": row, "providers": get_custom_image_provider_configs().get("providers", [])}


def delete_custom_image_provider_config(provider_id: str) -> dict[str, Any]:
    try:
        from agent.custom_image_providers import normalize_custom_image_provider_id, custom_image_provider_name
    except Exception as exc:
        raise RuntimeError("custom image provider support is unavailable") from exc

    normalized_id = normalize_custom_image_provider_id(provider_id)
    provider_name = custom_image_provider_name(normalized_id)
    config_path = _get_config_path()
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        image_cfg = config_data.get("image_gen")
        if isinstance(image_cfg, dict) and str(image_cfg.get("provider") or "").strip() == provider_name:
            raise ValueError("该外部图片模型正在使用，请先切换到其他图片生成配置。")
        existing_entries = config_data.get("custom_image_providers")
        if not isinstance(existing_entries, list):
            existing_entries = []
        updated = []
        removed = False
        for item in existing_entries:
            if not isinstance(item, dict):
                continue
            try:
                item_id = normalize_custom_image_provider_id(item.get("id"))
            except ValueError:
                continue
            if item_id == normalized_id:
                removed = True
                continue
            updated.append(item)
        if not removed:
            raise ValueError("外部图片模型不存在。")
        config_data["custom_image_providers"] = updated
        _save_yaml_config_file(config_path, config_data)
    reload_config()
    invalidate_models_cache()
    return {"ok": True, "providers": get_custom_image_provider_configs().get("providers", [])}


def set_image_gen_config(body: dict[str, Any]) -> dict[str, Any]:
    requested_provider_id = str(body.get("provider") or "").strip().lower()
    provider_id = _internal_image_gen_provider_id(requested_provider_id)
    model_id = str(body.get("model") or "").strip()
    api_key = body.get("api_key")
    credential_ref = str(body.get("credential_ref") or "").strip()
    credentials = body.get("credentials")
    if not isinstance(credentials, dict):
        credentials = {}
    if not requested_provider_id:
        raise ValueError("provider is required")
    if credential_ref and provider_id != "dashscope":
        raise ValueError("credential_ref is only supported for DashScope image generation")

    rows = _image_gen_provider_rows(provider_id)
    selected = next((row for row in rows if row.get("id") == requested_provider_id), None)
    if selected is None:
        if requested_provider_id in _BLOCKED_IMAGE_GEN_PROVIDER_LABELS or provider_id in _BLOCKED_IMAGE_GEN_PROVIDER_LABELS:
            raise ValueError("生成图片主配置只支持中国可用的稳定 Provider，请切换到国产生图服务。")
        raise ValueError(f"unknown image generation provider: {requested_provider_id}")
    selected_custom = bool(selected.get("custom"))
    selected_domestic = bool(selected.get("domestic")) if "domestic" in selected else provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
    selected_status = str(
        selected.get("integration_status")
        or ("custom" if selected_custom else ("stable" if provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS else "external"))
    )
    if selected.get("policy_blocked") or (
        not selected_custom and (not selected_domestic or selected_status != "stable")
    ):
        raise ValueError("生成图片主配置只支持中国可用的稳定 Provider，请切换到国产生图服务。")
    if not model_id:
        model_id = str(selected.get("default_model") or "").strip()
        models = selected.get("models") if isinstance(selected.get("models"), list) else []
        if not model_id and models:
            model_id = str((models[0] or {}).get("id") or "").strip()

    credential_fields = selected.get("credential_fields") if isinstance(selected.get("credential_fields"), list) else []
    inline_secret_supplied = bool(api_key is not None and str(api_key).strip())
    for item in credential_fields:
        if not isinstance(item, dict) or not bool(item.get("secret", True)):
            continue
        name = str(item.get("name") or "").strip()
        env_var = str(item.get("env_var") or "").strip()
        raw_value = credentials.get(name) if name and name in credentials else None
        if raw_value is None and env_var and env_var in credentials:
            raw_value = credentials.get(env_var)
        if raw_value is not None and str(raw_value).strip():
            inline_secret_supplied = True
    if provider_id == "dashscope" and not credential_ref and not inline_secret_supplied:
        credential_ref = default_credential_ref(
            provider_id,
            config_data=_load_yaml_config_file(_get_config_path()),
        )
    if credential_ref and inline_secret_supplied:
        raise ValueError("credential_ref and api_key cannot be used together")
    if credential_ref:
        credential_ref = normalize_credential_id(credential_ref)
    env_updates: dict[str, str] = {}
    option_updates: dict[str, str] = {}
    legacy_api_key_consumed = False
    for item in credential_fields:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        env_var = str(item.get("env_var") or "").strip()
        secret = bool(item.get("secret", True))
        raw_value = None
        if name and name in credentials:
            raw_value = credentials.get(name)
        elif env_var and env_var in credentials:
            raw_value = credentials.get(env_var)
        elif api_key is not None and not legacy_api_key_consumed and secret:
            raw_value = api_key
            legacy_api_key_consumed = True
        value = str(raw_value or "").strip()
        if not value:
            continue
        if secret:
            if not env_var:
                raise ValueError(f"{provider_id} credential {name or 'api_key'} has no env_var")
            env_updates[env_var] = value
        elif name:
            option_updates[name] = value

    if api_key is not None and str(api_key).strip() and not legacy_api_key_consumed and not credential_fields:
        env_var = _IMAGE_GEN_KEY_ENV.get(provider_id)
        if not env_var:
            raise ValueError(f"{provider_id} does not accept an API key from WebUI")
        env_updates[env_var] = str(api_key).strip()

    config_path = _get_config_path()
    with credential_transaction():
        with _cfg_lock:
            config_data = _load_yaml_config_file(config_path)
            if credential_ref:
                row = load_credential(credential_ref, config_data=config_data)
                if provider_family(row.get("provider_family")) != provider_family(provider_id):
                    raise ValueError("所选凭据不属于当前 Provider。")
            env_path = _get_hermes_home() / ".env"
            env_snapshots = {
                env_var: _credential_env_snapshot(env_path, env_var)
                for env_var in env_updates
            }
            env_touched = False
            try:
                if env_updates:
                    env_touched = True
                    _write_env_file(env_path, env_updates)
                image_cfg = config_data.get("image_gen")
                if not isinstance(image_cfg, dict):
                    image_cfg = {}
                image_cfg["provider"] = provider_id
                if model_id:
                    image_cfg["model"] = model_id
                image_cfg["use_gateway"] = False
                if provider_id == "dashscope":
                    image_cfg["credential_ref"] = credential_ref
                else:
                    image_cfg.pop("credential_ref", None)
                image_cfg.pop("api_key", None)
                if option_updates:
                    options = image_cfg.get("options")
                    if not isinstance(options, dict):
                        options = {}
                    options.update(option_updates)
                    image_cfg["options"] = options
                config_data["image_gen"] = image_cfg
                _save_yaml_config_file(config_path, config_data)
            except Exception:
                if env_touched:
                    for env_var, snapshot in env_snapshots.items():
                        try:
                            _restore_credential_env(env_path, env_var, snapshot)
                        except Exception:
                            logger.exception(
                                "Failed to restore image generation credential %s",
                                env_var,
                            )
                raise
    reload_config()
    invalidate_models_cache()
    _invalidate_image_gen_verification()
    return get_image_gen_config()


def get_model_config() -> dict[str, Any]:
    reload_config()
    config_path = _get_config_path()
    config_data = _load_yaml_config_file(config_path)
    model_cfg = _safe_model_cfg(config_data)
    provider = str(model_cfg.get("provider") or "").strip()
    model = str(model_cfg.get("default") or model_cfg.get("model") or model_cfg.get("name") or "").strip()
    key_env = str(model_cfg.get("key_env") or model_cfg.get("api_key_env") or "").strip()
    image_gen_config = get_image_gen_config()
    vision_config = get_vision_config()
    provider_credentials = get_provider_credentials_config().get("credentials", [])
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "main": {
            "provider": provider,
            "model": model,
            "base_url": str(model_cfg.get("base_url") or "").strip(),
            "key_env": key_env,
            "key_status": _key_status_for_env(key_env) if key_env else _provider_key_status(provider),
        },
        "providers": get_providers().get("providers", []),
        "auxiliary": get_auxiliary_models(),
        "vision": vision_config.get("vision", {}),
        "vision_providers": vision_config.get("providers", []),
        "image_gen": image_gen_config.get("image_gen", {}),
        "image_gen_providers": image_gen_config.get("providers", []),
        "provider_credentials": provider_credentials,
        "custom": {"supported": True, "key_env": _CUSTOM_MODEL_KEY_ENV},
    }


def set_main_model_config(body: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(body.get("provider") or "").strip().lower()
    model_id = str(body.get("model") or "").strip()
    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    api_key = body.get("api_key")
    if not provider_id:
        raise ValueError("provider is required")
    if not model_id:
        raise ValueError("model is required")

    if provider_id in _OAUTH_PROVIDERS or _provider_is_oauth(provider_id):
        label = _PROVIDER_DISPLAY.get(provider_id, provider_id)
        raise ValueError(f"{label} 使用网页登录授权，请在太极智能体中完成授权。")

    if provider_id == "custom":
        if not base_url:
            raise ValueError("base_url is required for custom provider")
        if api_key is not None and str(api_key).strip():
            _write_env_file(_get_hermes_home() / ".env", {_CUSTOM_MODEL_KEY_ENV: str(api_key).strip()})
    elif api_key is not None and str(api_key).strip():
        result = set_provider_key(provider_id, str(api_key).strip())
        if not result.get("ok"):
            raise ValueError(str(result.get("error") or "failed to save provider key"))

    config_path = _get_config_path()
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        model_cfg = config_data.get("model")
        if not isinstance(model_cfg, dict):
            model_cfg = {}
        model_cfg["provider"] = provider_id
        model_cfg["default"] = model_id
        if provider_id == "custom":
            model_cfg["base_url"] = base_url
            model_cfg["key_env"] = _CUSTOM_MODEL_KEY_ENV
            model_cfg.pop("api_key", None)
        else:
            if base_url:
                model_cfg["base_url"] = base_url
            elif provider_id != "openai":
                model_cfg.pop("base_url", None)
            model_cfg.pop("key_env", None)
            model_cfg.pop("api_key_env", None)
        config_data["model"] = model_cfg
        _save_yaml_config_file(config_path, config_data)
    reload_config()
    invalidate_models_cache()
    return get_model_config()
