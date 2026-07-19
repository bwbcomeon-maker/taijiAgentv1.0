"""Doubao Seedream image generation backend.

Routes Hermes' unified ``image_generate`` tool to Volcengine Ark's Images API.
This first integration intentionally exposes only text-to-image generation so
the existing tool schema stays stable: prompt + abstract aspect ratio in,
single image URL out.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from agent.image_gen_verification import require_image_gen_request_binding
from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_url_image,
)
from agent.image_gen_runtime_contracts import builtin_image_runtime_contract
from plugins.image_gen.domestic_common import (
    cached_success,
    post_json,
    provider_api_key,
)

logger = logging.getLogger(__name__)

_RUNTIME_CONTRACT = builtin_image_runtime_contract("doubao")
RUNTIME_TRANSPORT = _RUNTIME_CONTRACT["transport"]
GENERATIONS_ENDPOINT = _RUNTIME_CONTRACT["endpoint"]
BASE_URL = GENERATIONS_ENDPOINT.rsplit("/images/generations", 1)[0]

DEFAULT_MODEL = "doubao-seedream-5-0-260128"
ALIAS_MODEL = "doubao-seedream-5-0-lite-260128"

_MODELS: Dict[str, Dict[str, Any]] = {
    DEFAULT_MODEL: {
        "display": "Doubao Seedream 5.0 Lite",
        "speed": "varies",
        "strengths": "Chinese/English prompts, text-to-image, high-resolution PNG",
        "price": "Ark billing",
    },
    ALIAS_MODEL: {
        "display": "Doubao Seedream 5.0 Lite (alias)",
        "speed": "varies",
        "strengths": "Alias for Seedream 5.0 Lite",
        "price": "Ark billing",
    },
}

_ASPECT_SIZE_MAP = {
    "landscape": "2560x1440",
    "square": "2048x2048",
    "portrait": "1440x2560",
}


def _load_image_gen_config() -> Dict[str, Any]:
    """Read ``image_gen`` from config.yaml, returning {} on failure."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _resolve_model(explicit: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Resolve the Doubao model id from explicit input, env, config, or default."""
    explicit_model = str(explicit or "").strip()
    if explicit_model:
        if explicit_model not in _MODELS:
            raise ValueError(f"Unsupported Doubao image model: {explicit_model}")
        return explicit_model, _MODELS[explicit_model]

    candidates: list[Any] = [os.environ.get("DOUBAO_IMAGE_MODEL")]
    cfg = _load_image_gen_config()
    doubao_cfg = cfg.get("doubao") if isinstance(cfg.get("doubao"), dict) else {}
    if isinstance(doubao_cfg, dict):
        candidates.append(doubao_cfg.get("model"))
    candidates.append(cfg.get("model"))

    for candidate in candidates:
        if isinstance(candidate, str):
            model_id = candidate.strip()
            if not model_id:
                continue
            if model_id in _MODELS:
                return model_id, _MODELS[model_id]
            raise ValueError(f"Unsupported Doubao image model: {model_id}")
    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _extract_image_url(payload: Any) -> Optional[str]:
    """Extract the first URL from Ark Images API response payload."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


class DoubaoImageGenProvider(ImageGenProvider):
    """Doubao Seedream 5.0 Lite backend via Volcengine Ark."""

    _supports_pinned_image_request_binding = True

    @property
    def name(self) -> str:
        return "doubao"

    @property
    def display_name(self) -> str:
        return "Doubao Seedream"

    def is_available(self) -> bool:
        try:
            return bool(provider_api_key(self.name))
        except ValueError:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta["price"],
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Doubao Seedream",
            "badge": "paid",
            "tag": "Seedream 5.0 Lite via Volcengine Ark Images API",
            "env_vars": [
                {
                    "key": "ARK_API_KEY",
                    "prompt": "Volcengine Ark API key",
                    "url": "https://console.volcengine.com/ark/region:ark+cn-beijing/apikey",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        requested_model = str(kwargs.get("model") or "").strip()
        try:
            model_id, _meta = _resolve_model(requested_model)
        except ValueError:
            return error_response(
                error="Unsupported Doubao image model.",
                error_type="invalid_argument",
                provider=self.name,
                model=requested_model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="doubao",
                model=model_id,
                aspect_ratio=aspect,
            )

        raw_binding = kwargs.get("_runtime_binding")
        reauth_guard = kwargs.get("_reauth_guard")
        if raw_binding is not None and not callable(reauth_guard):
            return error_response(
                error="Doubao request authorization guard is missing.",
                error_type="configuration_error",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        try:
            api_key = (
                require_image_gen_request_binding(
                    raw_binding,
                    provider=self.name,
                    model=model_id,
                ).api_key
                if raw_binding is not None
                else provider_api_key(self.name)
            )
        except ValueError:
            return error_response(
                error="Doubao credential configuration is invalid.",
                error_type="configuration_error",
                provider=self.name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        if not api_key:
            return error_response(
                error="ARK_API_KEY not set. Configure Doubao Seedream in image generation settings.",
                error_type="auth_required",
                provider="doubao",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        size = _ASPECT_SIZE_MAP.get(aspect, _ASPECT_SIZE_MAP[DEFAULT_ASPECT_RATIO])
        request_payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "size": size,
            "output_format": "png",
            "response_format": "url",
            "watermark": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        body, request_error = post_json(
            url=GENERATIONS_ENDPOINT,
            headers=headers,
            payload=request_payload,
            timeout=180,
            provider=self.name,
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            secrets=(api_key,),
            reauth_guard=reauth_guard,
        )
        if request_error:
            return request_error

        image_url = _extract_image_url(body)
        if not image_url:
            return error_response(
                error="Doubao Seedream response contained no image URL",
                error_type="empty_response",
                provider="doubao",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return cached_success(
            image_url=image_url,
            cache_prefix="doubao_image",
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="doubao",
            extra={"size": size},
            save_image=save_url_image,
            reauth_guard=reauth_guard,
        )


def register(ctx: Any) -> None:
    """Plugin entry point: register the Doubao provider."""
    ctx.register_image_gen_provider(DoubaoImageGenProvider())
