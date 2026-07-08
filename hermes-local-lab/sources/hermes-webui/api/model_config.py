"""Aggregated model configuration endpoints for Hermes WebUI.

This module keeps browser-driven model setup on the same config.yaml/.env
surface the Hermes CLI uses.  It deliberately returns credential status only,
never secret values.
"""

from __future__ import annotations

import importlib
import logging
import os
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
_IMAGE_GEN_FALLBACK_META: dict[str, dict[str, Any]] = {
    "doubao": {
        "name": "Doubao Seedream",
        "models": [
            {"id": "doubao-seedream-5-0-260128", "label": "Doubao Seedream 5.0 Lite"},
            {"id": "doubao-seedream-5-0-lite-260128", "label": "Doubao Seedream 5.0 Lite (alias)"},
        ],
        "default_model": "doubao-seedream-5-0-260128",
    },
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
    if not {"doubao", "fal", "openai", "openai-codex", "xai", "krea"}.issubset(registered):
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
        if pid == "openai-codex":
            key_status = {
                "configured": bool(available),
                "source": "taiji_auth",
                "env_var": "",
            }
            display_name = "OpenAI 图像生成"
            description = "通过太极智能体授权使用图像生成"
            badge = "授权"
        else:
            key_status = _key_status_for_env(env_var)
            if is_custom and key_status.get("configured") and default_model:
                available = True
            display_name = str(schema.get("name") or getattr(provider, "display_name", "") or pid)
            description = str(schema.get("tag") or "")
            badge = str(schema.get("badge") or "")
        if active and readiness:
            available = bool(readiness.get("available"))
            key_status["configured"] = bool(readiness.get("available"))
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
                "oauth_managed": pid == "openai-codex",
                "custom": is_custom,
            }
        )

    for pid, env_var in _IMAGE_GEN_KEY_ENV.items():
        if pid in seen:
            continue
        fallback = _IMAGE_GEN_FALLBACK_META.get(pid, {})
        rows.append(
            {
                "id": pid,
                "name": str(fallback.get("name") or pid.replace("-", " ").title()),
                "description": "",
                "badge": "",
                "available": bool(_key_status_for_env(env_var).get("configured")),
                "active": pid == active_provider,
                "requires_env": [env_var],
                "key_status": _key_status_for_env(env_var),
                "models": list(fallback.get("models") or []),
                "default_model": str(fallback.get("default_model") or ""),
                "oauth_managed": False,
            }
        )
    if "openai-codex" not in seen:
        active = active_provider == "openai-codex"
        available = bool(readiness.get("available")) if active else False
        rows.append(
            {
                "id": _public_image_gen_provider_id("openai-codex"),
                "name": "OpenAI 图像生成",
                "description": "通过太极智能体授权使用图像生成",
                "badge": "授权",
                "available": available,
                "active": active,
                "requires_env": [],
                "key_status": {"configured": available, "source": "taiji_auth", "env_var": ""},
                "reason_code": str(readiness.get("reason_code") or "") if active else "",
                "status_message": str(readiness.get("public_message") or "") if active else "",
                "models": [],
                "default_model": "",
                "oauth_managed": True,
            }
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
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "vision": {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_mode": api_mode,
            "key_status": _vision_key_status(provider),
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
    if not requested_provider_id:
        raise ValueError("provider is required")

    rows = _image_gen_provider_rows(provider_id)
    selected = next((row for row in rows if row.get("id") == requested_provider_id), None)
    if selected is None:
        raise ValueError(f"unknown image generation provider: {requested_provider_id}")
    if not model_id:
        model_id = str(selected.get("default_model") or "").strip()
        models = selected.get("models") if isinstance(selected.get("models"), list) else []
        if not model_id and models:
            model_id = str((models[0] or {}).get("id") or "").strip()

    env_var = _IMAGE_GEN_KEY_ENV.get(provider_id)
    if api_key is not None and str(api_key).strip():
        if not env_var:
            raise ValueError(f"{provider_id} does not accept an API key from WebUI")
        _write_env_file(_get_hermes_home() / ".env", {env_var: str(api_key).strip()})

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
