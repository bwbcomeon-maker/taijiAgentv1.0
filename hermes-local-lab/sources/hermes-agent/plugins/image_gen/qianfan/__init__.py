"""Baidu Qianfan image generation backend."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.image_gen_provider import DEFAULT_ASPECT_RATIO, ImageGenProvider, error_response
from plugins.image_gen.domestic_common import (
    SIZE_MAP_X,
    auth_error,
    cached_success,
    credential_field,
    first_url,
    normalized_aspect,
    post_json,
    provider_api_key,
    save_url_image,
    validate_prompt,
)

ENDPOINT = "https://qianfan.baidubce.com/v2/images/generations"
DEFAULT_MODEL = "qwen-image"
TIMEOUT_SECONDS = 180


class QianfanImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "qianfan"

    @property
    def display_name(self) -> str:
        return "百度千帆"

    def is_available(self) -> bool:
        try:
            return bool(provider_api_key(self.name))
        except ValueError:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "qwen-image",
                "display": "Qwen Image",
                "speed": "varies",
                "strengths": "千帆通用图像生成接口",
                "price": "Qianfan billing",
            }
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "百度千帆",
            "badge": "国产",
            "tag": "百度千帆通用图像生成 v2",
            "domestic": True,
            "integration_status": "stable",
            "call_mode": "sync",
            "supported_regions": ["cn"],
            "env_vars": [{"key": "QIANFAN_API_KEY", "prompt": "Qianfan API Key"}],
            "credential_fields": [
                credential_field(name="api_key", env_var="QIANFAN_API_KEY", label="API Key")
            ],
        }

    def _model(self, requested: Any = "") -> str:
        model = str(requested or "").strip()
        ids = {item["id"] for item in self.list_models()}
        if not model:
            return DEFAULT_MODEL
        if model not in ids:
            raise ValueError(f"Unsupported Qianfan image model: {model}")
        return model

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        aspect = normalized_aspect(aspect_ratio)
        requested_model = str(kwargs.get("model") or "").strip()
        try:
            model = self._model(requested_model)
        except ValueError:
            return error_response(
                error="Unsupported Qianfan image model.",
                error_type="invalid_argument",
                provider=self.name,
                model=requested_model,
                prompt=str(prompt or "").strip(),
                aspect_ratio=aspect,
            )
        prompt, prompt_error = validate_prompt(prompt, provider=self.name, model=model, aspect_ratio=aspect)
        if prompt_error:
            return prompt_error
        try:
            api_key = provider_api_key(self.name)
        except ValueError:
            return error_response(
                error="Qianfan credential configuration is invalid.",
                error_type="configuration_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        if not api_key:
            return auth_error(
                missing=("QIANFAN_API_KEY",),
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        payload = {
            "model": model,
            "prompt": prompt,
            "size": SIZE_MAP_X.get(aspect, SIZE_MAP_X["landscape"]),
            "n": 1,
        }
        body, error = post_json(
            url=ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload=payload,
            timeout=TIMEOUT_SECONDS,
            provider=self.name,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            secrets=(api_key,),
        )
        if error:
            return error
        image_url = first_url(body)
        if not image_url:
            return error_response(
                error="qianfan response contained no image URL.",
                error_type="empty_response",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        return cached_success(
            image_url=image_url,
            cache_prefix="qianfan_image",
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            provider=self.name,
            save_image=save_url_image,
        )


def register(ctx):
    ctx.register_image_gen_provider(QianfanImageGenProvider())
