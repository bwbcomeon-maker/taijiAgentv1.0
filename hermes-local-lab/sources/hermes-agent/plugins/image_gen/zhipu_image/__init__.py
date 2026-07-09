"""Zhipu GLM-Image generation backend."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from agent.image_gen_provider import DEFAULT_ASPECT_RATIO, ImageGenProvider, error_response
from plugins.image_gen.domestic_common import (
    SIZE_MAP_X,
    auth_error,
    cached_success,
    credential_field,
    env_value,
    first_url,
    missing_required,
    normalized_aspect,
    post_json,
    save_url_image,
    validate_prompt,
)

ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/images/generations"
DEFAULT_MODEL = "glm-image"
TIMEOUT_SECONDS = 180


class ZhipuImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "zhipu-image"

    @property
    def display_name(self) -> str:
        return "智谱 GLM-Image"

    def is_available(self) -> bool:
        return bool(env_value("GLM_API_KEY"))

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "glm-image",
                "display": "GLM-Image",
                "speed": "varies",
                "strengths": "智谱图像生成",
                "price": "BigModel billing",
            },
            {
                "id": "cogview-4",
                "display": "CogView-4",
                "speed": "varies",
                "strengths": "CogView image generation",
                "price": "BigModel billing",
            },
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "智谱 GLM-Image",
            "badge": "国产",
            "tag": "智谱开放平台图像生成",
            "domestic": True,
            "integration_status": "stable",
            "call_mode": "sync",
            "supported_regions": ["cn"],
            "env_vars": [{"key": "GLM_API_KEY", "prompt": "智谱 API Key"}],
            "credential_fields": [
                credential_field(name="api_key", env_var="GLM_API_KEY", label="API Key")
            ],
        }

    def _model(self, requested: Any = "") -> str:
        model = str(requested or "").strip()
        ids = {item["id"] for item in self.list_models()}
        return model if model in ids else DEFAULT_MODEL

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        aspect = normalized_aspect(aspect_ratio)
        model = self._model(kwargs.get("model"))
        prompt, prompt_error = validate_prompt(prompt, provider=self.name, model=model, aspect_ratio=aspect)
        if prompt_error:
            return prompt_error
        missing = missing_required(("GLM_API_KEY",))
        if missing:
            return auth_error(missing=missing, provider=self.name, model=model, prompt=prompt, aspect_ratio=aspect)

        api_key = env_value("GLM_API_KEY")
        payload = {
            "model": model,
            "prompt": prompt,
            "size": SIZE_MAP_X.get(aspect, SIZE_MAP_X["landscape"]),
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
            request_post=requests.post,
        )
        if error:
            return error
        image_url = first_url(body)
        if not image_url:
            return error_response(
                error="zhipu-image response contained no image URL.",
                error_type="empty_response",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        return cached_success(
            image_url=image_url,
            cache_prefix="zhipu_image",
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            provider=self.name,
            save_image=save_url_image,
        )


def register(ctx):
    ctx.register_image_gen_provider(ZhipuImageGenProvider())
