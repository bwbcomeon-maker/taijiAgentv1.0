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
    "fal": "FAL_KEY",
    "openai": "OPENAI_API_KEY",
    "xai": "XAI_API_KEY",
    "krea": "KREA_API_KEY",
}
_BUILTIN_IMAGE_GEN_MODULES: tuple[str, ...] = (
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
    if {"fal", "openai", "openai-codex", "xai", "krea"}.issubset(registered):
        return
    ctx = _ImageGenRegisterContext()
    for module_name in _BUILTIN_IMAGE_GEN_MODULES:
        try:
            module = importlib.import_module(module_name)
            register = getattr(module, "register", None)
            if callable(register):
                register(ctx)
        except Exception:
            logger.debug("Failed to register image_gen plugin %s", module_name, exc_info=True)


def _image_gen_provider_rows(active_provider: str) -> list[dict[str, Any]]:
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

        env_vars = []
        for item in schema.get("env_vars") or []:
            if isinstance(item, dict) and item.get("key"):
                env_vars.append(str(item.get("key")).strip())
        env_var = _IMAGE_GEN_KEY_ENV.get(pid) or (env_vars[0] if env_vars else "")
        key_status = (
            {"configured": True, "source": "oauth", "env_var": ""}
            if pid == "openai-codex"
            else _key_status_for_env(env_var)
        )
        rows.append(
            {
                "id": pid,
                "name": str(schema.get("name") or getattr(provider, "display_name", "") or pid),
                "description": str(schema.get("tag") or ""),
                "badge": str(schema.get("badge") or ""),
                "available": available or bool(key_status.get("configured")) or pid == "openai-codex",
                "active": pid == active_provider,
                "requires_env": env_vars or ([env_var] if env_var else []),
                "key_status": key_status,
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
            }
        )

    for pid, env_var in _IMAGE_GEN_KEY_ENV.items():
        if pid in seen:
            continue
        rows.append(
            {
                "id": pid,
                "name": pid.replace("-", " ").title(),
                "description": "",
                "badge": "",
                "available": bool(_key_status_for_env(env_var).get("configured")),
                "active": pid == active_provider,
                "requires_env": [env_var],
                "key_status": _key_status_for_env(env_var),
                "models": [],
                "default_model": "",
                "oauth_managed": False,
            }
        )
    if "openai-codex" not in seen:
        rows.append(
            {
                "id": "openai-codex",
                "name": "OpenAI Codex",
                "description": "ChatGPT/Codex OAuth image generation",
                "badge": "oauth",
                "available": True,
                "active": active_provider == "openai-codex",
                "requires_env": [],
                "key_status": {"configured": True, "source": "oauth", "env_var": ""},
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
        "config_path": str(config_path),
        "image_gen": {
            "provider": active_provider,
            "model": active_model,
            "use_gateway": bool(image_cfg.get("use_gateway")),
        },
        "providers": _image_gen_provider_rows(active_provider),
    }


def set_image_gen_config(body: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(body.get("provider") or "").strip().lower()
    model_id = str(body.get("model") or "").strip()
    api_key = body.get("api_key")
    if not provider_id:
        raise ValueError("provider is required")

    rows = _image_gen_provider_rows(provider_id)
    selected = next((row for row in rows if row.get("id") == provider_id), None)
    if selected is None:
        raise ValueError(f"unknown image generation provider: {provider_id}")
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
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config_path": str(config_path),
        "main": {
            "provider": provider,
            "model": model,
            "base_url": str(model_cfg.get("base_url") or "").strip(),
            "key_env": key_env,
            "key_status": _key_status_for_env(key_env) if key_env else _provider_key_status(provider),
        },
        "providers": get_providers().get("providers", []),
        "auxiliary": get_auxiliary_models(),
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
        raise ValueError(f"{label} uses OAuth. Run `hermes model` or `hermes auth` in the terminal.")

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
