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
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        "default_model": "",
        "models": [],
        "requires_base_url": True,
    },
}
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
        from api.profiles import get_active_profile

        profile = get_active_profile()
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
    return {
        "id": public,
        "name": _BLOCKED_IMAGE_GEN_PROVIDER_LABELS.get(public, _BLOCKED_IMAGE_GEN_PROVIDER_LABELS.get(provider_id, public)),
        "description": "历史非国产图片生成配置，只读显示。",
        "badge": "已阻止",
        "available": False,
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

        credential_fields = _image_gen_credential_fields(
            schema=schema,
            env_var=env_var,
            env_vars=env_vars,
        )
        credential_status = _image_gen_credential_status(
            credential_fields,
            active_options=active_options if active else {},
        )
        key_status = _image_gen_primary_key_status(credential_fields, credential_status)
        if is_custom and key_status.get("configured") and default_model:
            available = True
        display_name = str(schema.get("name") or getattr(provider, "display_name", "") or pid)
        description = str(schema.get("tag") or "")
        badge = str(schema.get("badge") or ("外部" if is_custom else "国产"))
        if active and readiness:
            available = bool(readiness.get("available"))
            reason_code = str(readiness.get("reason_code") or "")
            status_message = str(readiness.get("public_message") or "")
        else:
            reason_code = "ready" if available else ""
            status_message = ""
        rows.append(
            {
                "id": public_pid,
                "name": display_name,
                "description": description,
                "badge": badge,
                "available": bool(available),
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
        credential_fields = _image_gen_credential_fields(schema={}, env_var=env_var, env_vars=[env_var])
        credential_status = _image_gen_credential_status(
            credential_fields,
            active_options=active_options if active else {},
        )
        rows.append(
            {
                "id": pid,
                "name": str(fallback.get("name") or pid.replace("-", " ").title()),
                "description": "",
                "badge": "国产",
                "available": bool(_key_status_for_env(env_var).get("configured")),
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
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "image_gen": {
            "provider": _public_image_gen_provider_id(active_provider),
            "model": active_model,
            "use_gateway": bool(image_cfg.get("use_gateway")),
        },
        "providers": _image_gen_provider_rows(active_provider),
        "custom_image_providers": get_custom_image_provider_configs().get("providers", []),
    }


def _vision_key_status(provider_id: str) -> dict[str, Any]:
    provider = str(provider_id or "").strip().lower()
    env_var = _VISION_KEY_ENV.get(provider)
    if env_var:
        return _key_status_for_env(env_var)
    return _provider_key_status(provider)


def _vision_verification_state_path() -> Path:
    from api.config import STATE_DIR

    return Path(STATE_DIR) / "vision-verification.json"


def _vision_probe_image_path() -> Path:
    return _vision_verification_state_path().with_name("vision-verification-probe.png")


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


def _read_vision_verification_state() -> dict[str, Any]:
    try:
        data = json.loads(_vision_verification_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _vision_secret_digest(provider: str) -> str:
    env_var = _VISION_KEY_ENV.get(provider) or _PROVIDER_ENV_VAR.get(provider) or ""
    if not env_var:
        return ""
    env_values = _load_env_file(_get_hermes_home() / ".env")
    secret = str(env_values.get(env_var) or os.getenv(env_var) or "").strip()
    return hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else ""


def _vision_config_fingerprint(vision_cfg: dict[str, Any], key_status: dict[str, Any]) -> str:
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    material = {
        "profile": _active_profile_name(),
        "provider": provider,
        "model": str(vision_cfg.get("model") or "").strip(),
        "base_url": str(vision_cfg.get("base_url") or "").strip().rstrip("/"),
        "api_mode": str(vision_cfg.get("api_mode") or "").strip(),
        "key_configured": bool(key_status.get("configured")),
        "key_digest": _vision_secret_digest(provider),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _vision_is_configured(vision_cfg: dict[str, Any], key_status: dict[str, Any]) -> bool:
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    model = str(vision_cfg.get("model") or "").strip()
    meta = _VISION_PROVIDER_META.get(provider) or {}
    base_url = str(vision_cfg.get("base_url") or "").strip()
    return bool(
        provider
        and model
        and key_status.get("configured")
        and (not meta.get("requires_base_url") or base_url)
    )


def _public_vision_verification(vision_cfg: dict[str, Any], key_status: dict[str, Any]) -> dict[str, Any]:
    if not _vision_is_configured(vision_cfg, key_status):
        return {
            "status": "unconfigured",
            "checked_at": "",
            "error_code": "vision_not_configured",
            "message": "请先保存完整的识图 Provider、模型和密钥配置。",
            "diagnostic_id": "",
        }
    state = _read_vision_verification_state()
    fingerprint = _vision_config_fingerprint(vision_cfg, key_status)
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
    path = _vision_verification_state_path()
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
    config_data = _load_yaml_config_file(_get_config_path())
    auxiliary = config_data.get("auxiliary")
    vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    model = str(vision_cfg.get("model") or "").strip()
    key_status = _vision_key_status(provider)
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    diagnostic_id = uuid.uuid4().hex
    if not _vision_is_configured(vision_cfg, key_status):
        return _vision_test_response(
            ok=False,
            status="unconfigured",
            checked_at=checked_at,
            provider=provider,
            model=model,
            error_code="vision_not_configured",
            message="请先保存完整的识图 Provider、模型和密钥配置。",
            diagnostic_id=diagnostic_id,
        )

    error_code = ""
    message = "识图验证通过，当前配置已完成真实图片探测。"
    ok = False
    try:
        from tools.vision_tools import vision_analyze_tool

        probe_path = _vision_probe_image_path()
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
                model=model,
            )

        result = json.loads(asyncio.run(_run_probe()))
        analysis = str(result.get("analysis") or "") if isinstance(result, dict) else ""
        ok = bool(isinstance(result, dict) and result.get("success") and _VISION_PROBE_MARKER in analysis)
        if not ok:
            error_code = "vision_probe_failed"
            message = "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"
    except Exception:
        logger.warning("Vision configuration probe failed (%s)", diagnostic_id)
        error_code = "vision_probe_failed"
        message = "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"

    status = "verified" if ok else "failed"
    state = {
        "fingerprint": _vision_config_fingerprint(vision_cfg, key_status),
        "status": status,
        "checked_at": checked_at,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    _atomic_write_json(_vision_verification_state_path(), state)
    return _vision_test_response(
        ok=ok,
        status=status,
        checked_at=checked_at,
        provider=provider,
        model=model,
        error_code=error_code,
        message=message,
        diagnostic_id=diagnostic_id,
    )


def _vision_provider_rows(active_provider: str, vision_cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    active = str(active_provider or "").strip().lower()
    for pid, meta in _VISION_PROVIDER_META.items():
        key_status = _vision_key_status(pid)
        requires_base_url = bool(meta.get("requires_base_url"))
        active_base_url = str((vision_cfg or {}).get("base_url") or "").strip() if pid == active else ""
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
            }
        )
    if active and active not in _VISION_PROVIDER_META and active != "auto":
        key_status = _vision_key_status(active)
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
    key_status = _vision_key_status(provider)
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "vision": {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_mode": api_mode,
            "key_status": key_status,
            "verification": _public_vision_verification(vision_cfg, key_status),
        },
        "providers": _vision_provider_rows(provider, vision_cfg),
    }


def set_vision_config(body: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(body.get("provider") or "").strip().lower()
    model_id = str(body.get("model") or "").strip()
    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    api_key = body.get("api_key")
    if not provider_id:
        raise ValueError("provider is required")
    if provider_id not in _VISION_PROVIDER_META:
        raise ValueError(f"unknown vision provider: {provider_id}")

    meta = _VISION_PROVIDER_META[provider_id]
    if not model_id:
        model_id = str(meta.get("default_model") or "").strip()
        models = meta.get("models") if isinstance(meta.get("models"), list) else []
        if not model_id and models:
            model_id = str((models[0] or {}).get("id") or "").strip()
    if not model_id:
        raise ValueError("model is required")
    if bool(meta.get("requires_base_url")) and not base_url:
        raise ValueError("base_url is required for custom vision provider")

    env_var = _VISION_KEY_ENV.get(provider_id)
    if api_key is not None and str(api_key).strip():
        if not env_var:
            raise ValueError(f"{provider_id} does not accept an API key from WebUI")
        _write_env_file(_get_hermes_home() / ".env", {env_var: str(api_key).strip()})

    config_path = _get_config_path()
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        auxiliary = config_data.get("auxiliary")
        if not isinstance(auxiliary, dict):
            auxiliary = {}
        vision_cfg = auxiliary.get("vision")
        if not isinstance(vision_cfg, dict):
            vision_cfg = {}
        vision_cfg["provider"] = provider_id
        vision_cfg["model"] = model_id
        if provider_id == "custom":
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
        auxiliary["vision"] = vision_cfg
        config_data["auxiliary"] = auxiliary
        _save_yaml_config_file(config_path, config_data)
    reload_config()
    invalidate_models_cache()
    _invalidate_vision_verification()
    return get_vision_config()


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
    credentials = body.get("credentials")
    if not isinstance(credentials, dict):
        credentials = {}
    if not requested_provider_id:
        raise ValueError("provider is required")

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

    if env_updates:
        _write_env_file(_get_hermes_home() / ".env", env_updates)

    config_path = _get_config_path()
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        image_cfg = config_data.get("image_gen")
        if not isinstance(image_cfg, dict):
            image_cfg = {}
        image_cfg["provider"] = provider_id
        if model_id:
            image_cfg["model"] = model_id
        image_cfg["use_gateway"] = False
        if option_updates:
            options = image_cfg.get("options")
            if not isinstance(options, dict):
                options = {}
            options.update(option_updates)
            image_cfg["options"] = options
        config_data["image_gen"] = image_cfg
        _save_yaml_config_file(config_path, config_data)
    reload_config()
    invalidate_models_cache()
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
