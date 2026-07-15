"""Config-driven OpenAI-compatible image generation providers."""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)

CUSTOM_PROVIDER_PREFIX = "custom:"
DEFAULT_SIZE_MAP = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_RESPONSE_FORMAT = "auto"

_REGISTERED_CUSTOM_PROVIDER_NAMES: set[str] = set()
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_MODEL_RE = re.compile(r"^[^\s]+$")


def normalize_custom_image_provider_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith(CUSTOM_PROVIDER_PREFIX):
        raw = raw[len(CUSTOM_PROVIDER_PREFIX):]
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    if not raw or not _ID_RE.match(raw):
        raise ValueError("外部图片模型 ID 只能包含小写字母、数字、短横线和下划线。")
    return raw


def custom_image_provider_name(provider_id: Any) -> str:
    return f"{CUSTOM_PROVIDER_PREFIX}{normalize_custom_image_provider_id(provider_id)}"


def custom_image_provider_env_var(provider_id: Any) -> str:
    normalized = normalize_custom_image_provider_id(provider_id)
    token = re.sub(r"[^A-Z0-9]+", "_", normalized.upper()).strip("_")
    return f"TAIJI_IMAGE_CUSTOM_{token}_API_KEY"


def _normalize_base_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("外部图片模型 Base URL 必须使用 HTTPS。")
    return url


def _normalize_models(value: Any, default_model: Any = "") -> List[str]:
    raw_items: Iterable[Any]
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace("\n", ",").split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    models: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        model = str(item or "").strip()
        if not model:
            continue
        if not _MODEL_RE.match(model):
            raise ValueError("外部图片模型 ID 不能包含空白字符。")
        if model not in seen:
            seen.add(model)
            models.append(model)
    default = str(default_model or "").strip()
    if default:
        if not _MODEL_RE.match(default):
            raise ValueError("默认图片模型 ID 不能包含空白字符。")
        if default not in seen:
            models.insert(0, default)
    if not models:
        raise ValueError("至少需要配置一个外部图片模型 ID。")
    return models


def _normalize_size_map(value: Any) -> dict[str, str]:
    size_map = dict(DEFAULT_SIZE_MAP)
    if isinstance(value, dict):
        for key in ("landscape", "square", "portrait"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                if not re.match(r"^\d{2,5}x\d{2,5}$", candidate):
                    raise ValueError("图片尺寸映射必须使用 WIDTHxHEIGHT 格式。")
                size_map[key] = candidate
    return size_map


def _normalize_response_format(value: Any) -> str:
    fmt = str(value or DEFAULT_RESPONSE_FORMAT).strip().lower()
    if fmt not in {"auto", "b64_json", "url"}:
        raise ValueError("response_format 只能是 auto、b64_json 或 url。")
    return fmt


def _normalize_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(5, min(timeout, 600))


def normalize_custom_image_provider_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("外部图片模型配置必须是对象。")
    provider_id = normalize_custom_image_provider_id(entry.get("id") or entry.get("provider_id"))
    models = _normalize_models(entry.get("models"), entry.get("default_model") or entry.get("model"))
    default_model = str(entry.get("default_model") or entry.get("model") or models[0]).strip()
    if default_model not in models:
        models.insert(0, default_model)
    return {
        "id": provider_id,
        "name": str(entry.get("name") or provider_id).strip()[:80],
        "base_url": _normalize_base_url(entry.get("base_url")),
        "api_key_env": str(entry.get("api_key_env") or custom_image_provider_env_var(provider_id)).strip(),
        "models": models,
        "default_model": default_model,
        "size_map": _normalize_size_map(entry.get("size_map")),
        "response_format": _normalize_response_format(entry.get("response_format")),
        "timeout_seconds": _normalize_timeout(entry.get("timeout_seconds")),
    }


def custom_image_provider_public_row(entry: dict[str, Any], *, active_provider: str = "") -> dict[str, Any]:
    normalized = normalize_custom_image_provider_entry(entry)
    provider_name = custom_image_provider_name(normalized["id"])
    configured = bool(os.getenv(normalized["api_key_env"]))
    return {
        "id": provider_name,
        "name": normalized["name"],
        "description": "OpenAI Images 兼容外部图片模型",
        "badge": "外部",
        # Field completeness is not proof that the remote endpoint works.
        "available": False,
        "configured": bool(configured and normalized["base_url"] and normalized["default_model"]),
        "verification_status": "configured_unverified" if configured else "not_configured",
        "active": provider_name == str(active_provider or "").strip(),
        "requires_env": [normalized["api_key_env"]],
        "key_status": {
            "configured": configured,
            "source": "env_var" if configured else "none",
            "env_var": normalized["api_key_env"],
        },
        "reason_code": "configured_unverified" if configured else "authorization_required",
        "status_message": "已配置，尚未验证。" if configured else "外部图片模型密钥未配置。",
        "models": [{"id": model, "label": model} for model in normalized["models"]],
        "default_model": normalized["default_model"],
        "oauth_managed": False,
        "custom": True,
        "base_url": normalized["base_url"],
        "size_map": dict(normalized["size_map"]),
        "base_url_configured": True,
        "response_format": normalized["response_format"],
        "timeout_seconds": normalized["timeout_seconds"],
    }


def load_custom_image_provider_entries(config_data: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    if config_data is None:
        try:
            from hermes_cli.config import load_config

            config_data = load_config()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not load custom image providers: %s", exc)
            return []
    raw_entries = config_data.get("custom_image_providers") if isinstance(config_data, dict) else None
    if not isinstance(raw_entries, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw_entries:
        try:
            entries.append(normalize_custom_image_provider_entry(item))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping invalid custom image provider: %s", exc)
    return entries


class ConfigurableOpenAIImageProvider(ImageGenProvider):
    """OpenAI Images compatible backend described by config.yaml."""

    def __init__(self, entry: dict[str, Any]) -> None:
        self._entry = normalize_custom_image_provider_entry(entry)

    @property
    def name(self) -> str:
        return custom_image_provider_name(self._entry["id"])

    @property
    def display_name(self) -> str:
        return self._entry["name"]

    def is_available(self) -> bool:
        return bool(
            self._entry.get("base_url")
            and self._entry.get("default_model")
            and os.getenv(str(self._entry.get("api_key_env") or ""))
        )

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model,
                "display": model,
                "speed": "",
                "strengths": "OpenAI Images 兼容接口",
                "price": "",
            }
            for model in self._entry["models"]
        ]

    def default_model(self) -> Optional[str]:
        return str(self._entry.get("default_model") or "").strip() or None

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "外部",
            "tag": "OpenAI Images 兼容外部图片模型",
            "env_vars": [
                {
                    "key": self._entry["api_key_env"],
                    "prompt": f"{self.display_name} API key",
                    "url": "",
                }
            ],
        }

    def _endpoint(self) -> str:
        base = self._entry["base_url"].rstrip("/")
        if base.endswith("/images/generations"):
            return base
        return f"{base}/images/generations"

    def _model(self, requested: Any = "") -> str:
        model = str(requested or "").strip()
        if model and model in self._entry["models"]:
            return model
        return self._entry["default_model"]

    def _cache_prefix(self, model: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", model).strip("._-")[:80] or "model"
        return f"custom_{self._entry['id']}_{token}"

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        model = self._model(kwargs.get("model"))
        provider = self.name
        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider=provider,
                model=model,
                aspect_ratio=aspect,
            )
        api_key = os.getenv(self._entry["api_key_env"], "").strip()
        if not api_key:
            return error_response(
                error="外部图片模型密钥未配置，请先在模型配置中保存密钥。",
                error_type="auth_required",
                provider=provider,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": self._entry["size_map"].get(aspect, DEFAULT_SIZE_MAP[aspect]),
            "n": 1,
        }
        response_format = self._entry.get("response_format") or DEFAULT_RESPONSE_FORMAT
        if response_format != "auto":
            payload["response_format"] = response_format

        try:
            response = requests.post(
                self._endpoint(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._entry["timeout_seconds"],
                allow_redirects=False,
            )
        except Exception:
            logger.debug("Custom image provider request failed", exc_info=True)
            return error_response(
                error="外部图片模型请求失败，请检查服务地址、网络和密钥配置。",
                error_type="api_error",
                provider=provider,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            body = response.json()
        except Exception:
            body = {}
        if response.status_code >= 400:
            message = _response_error_message(body)
            return error_response(
                error=f"外部图片模型请求失败：HTTP {response.status_code}{message}",
                error_type="api_error",
                provider=provider,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = _first_image_item(body)
        b64 = str(first.get("b64_json") or "").strip() if first else ""
        url = str(first.get("url") or "").strip() if first else ""
        revised_prompt = str(first.get("revised_prompt") or "").strip() if first else ""
        prefix = self._cache_prefix(model)
        if b64:
            try:
                path = save_b64_image(b64, prefix=prefix)
            except (ValueError, OSError, binascii.Error) as exc:
                return error_response(
                    error=f"外部图片生成结果保存失败：{exc}",
                    error_type="io_error",
                    provider=provider,
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            image_ref = str(path)
        elif url:
            try:
                image_ref = str(save_url_image(url, prefix=prefix))
            except Exception as exc:  # noqa: BLE001
                return error_response(
                    error=f"外部图片生成结果下载失败：{exc}",
                    error_type="io_error",
                    provider=provider,
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
        else:
            return error_response(
                error="外部图片模型响应中没有图片数据。",
                error_type="empty_response",
                provider=provider,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        extra = {"response_format": response_format}
        if revised_prompt:
            extra["revised_prompt"] = revised_prompt
        return success_response(
            image=image_ref,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            provider=provider,
            extra=extra,
        )


def _first_image_item(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if not isinstance(data, list) or not data:
        return {}
    first = data[0]
    return first if isinstance(first, dict) else {}


def _response_error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    err = body.get("error")
    message = ""
    if isinstance(err, dict):
        message = str(err.get("message") or "").strip()
    elif isinstance(err, str):
        message = err.strip()
    if not message:
        return ""
    redacted = re.sub(r"(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]*secret[A-Za-z0-9_-]*)", "[已隐藏]", message)
    return f"：{redacted[:240]}"


def register_configured_custom_image_providers(config_data: Optional[dict[str, Any]] = None) -> None:
    from agent.image_gen_registry import register_provider, unregister_provider

    global _REGISTERED_CUSTOM_PROVIDER_NAMES
    for name in list(_REGISTERED_CUSTOM_PROVIDER_NAMES):
        unregister_provider(name)
    _REGISTERED_CUSTOM_PROVIDER_NAMES = set()

    for entry in load_custom_image_provider_entries(config_data):
        provider = ConfigurableOpenAIImageProvider(entry)
        register_provider(provider)
        _REGISTERED_CUSTOM_PROVIDER_NAMES.add(provider.name)
