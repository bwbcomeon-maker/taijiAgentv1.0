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

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    success_response,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
GENERATIONS_ENDPOINT = f"{BASE_URL}/images/generations"

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
    candidates: list[Any] = [explicit, os.environ.get("DOUBAO_IMAGE_MODEL")]
    cfg = _load_image_gen_config()
    doubao_cfg = cfg.get("doubao") if isinstance(cfg.get("doubao"), dict) else {}
    if isinstance(doubao_cfg, dict):
        candidates.append(doubao_cfg.get("model"))
    candidates.append(cfg.get("model"))

    for candidate in candidates:
        if isinstance(candidate, str):
            model_id = candidate.strip()
            if model_id in _MODELS:
                return model_id, _MODELS[model_id]
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

    @property
    def name(self) -> str:
        return "doubao"

    @property
    def display_name(self) -> str:
        return "Doubao Seedream"

    def is_available(self) -> bool:
        return bool(os.environ.get("ARK_API_KEY"))

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
        model_id, _meta = _resolve_model(kwargs.get("model"))

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="doubao",
                model=model_id,
                aspect_ratio=aspect,
            )

        api_key = str(os.environ.get("ARK_API_KEY") or "").strip()
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

        try:
            response = requests.post(
                GENERATIONS_ENDPOINT,
                headers=headers,
                json=request_payload,
                timeout=180,
            )
            response.raise_for_status()
            body = response.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            detail = ""
            if exc.response is not None:
                try:
                    detail = str(exc.response.json().get("error", exc.response.text[:300]))
                except Exception:
                    detail = exc.response.text[:300]
            return error_response(
                error=f"Doubao Seedream image generation failed ({status}): {detail or exc}",
                error_type="api_error",
                provider="doubao",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except requests.RequestException as exc:
            return error_response(
                error=f"Doubao Seedream image generation request failed: {exc}",
                error_type=type(exc).__name__,
                provider="doubao",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except ValueError as exc:
            return error_response(
                error=f"Doubao Seedream returned invalid JSON: {exc}",
                error_type="invalid_response",
                provider="doubao",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

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

        return success_response(
            image=image_url,
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="doubao",
            extra={"size": size},
        )


def register(ctx: Any) -> None:
    """Plugin entry point: register the Doubao provider."""
    ctx.register_image_gen_provider(DoubaoImageGenProvider())
