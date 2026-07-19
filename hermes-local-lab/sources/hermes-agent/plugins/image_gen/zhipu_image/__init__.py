"""Zhipu GLM-Image generation backend."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.image_gen_verification import require_image_gen_request_binding
from agent.image_gen_provider import DEFAULT_ASPECT_RATIO, ImageGenProvider, error_response
from agent.image_gen_runtime_contracts import builtin_image_runtime_contract
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

_RUNTIME_CONTRACT = builtin_image_runtime_contract("zhipu-image")
RUNTIME_TRANSPORT = _RUNTIME_CONTRACT["transport"]
ENDPOINT = _RUNTIME_CONTRACT["endpoint"]
DEFAULT_MODEL = "glm-image"
TIMEOUT_SECONDS = 180


class ZhipuImageGenProvider(ImageGenProvider):
    _supports_pinned_image_request_binding = True

    @property
    def name(self) -> str:
        return "zhipu-image"

    @property
    def display_name(self) -> str:
        return "智谱 GLM-Image"

    def is_available(self) -> bool:
        try:
            return bool(provider_api_key(self.name))
        except ValueError:
            return False

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
        if not model:
            return DEFAULT_MODEL
        if model not in ids:
            raise ValueError(f"Unsupported Zhipu image model: {model}")
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
                error="Unsupported Zhipu image model.",
                error_type="invalid_argument",
                provider=self.name,
                model=requested_model,
                prompt=str(prompt or "").strip(),
                aspect_ratio=aspect,
            )
        prompt, prompt_error = validate_prompt(prompt, provider=self.name, model=model, aspect_ratio=aspect)
        if prompt_error:
            return prompt_error
        raw_binding = kwargs.get("_runtime_binding")
        reauth_guard = kwargs.get("_reauth_guard")
        if raw_binding is not None and not callable(reauth_guard):
            return error_response(
                error="Zhipu request authorization guard is missing.",
                error_type="configuration_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        try:
            api_key = (
                require_image_gen_request_binding(
                    raw_binding,
                    provider=self.name,
                    model=model,
                ).api_key
                if raw_binding is not None
                else provider_api_key(self.name)
            )
        except ValueError:
            return error_response(
                error="Zhipu credential configuration is invalid.",
                error_type="configuration_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        if not api_key:
            return auth_error(
                missing=("GLM_API_KEY",),
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
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
            reauth_guard=reauth_guard,
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
            reauth_guard=reauth_guard,
        )


def register(ctx):
    ctx.register_image_gen_provider(ZhipuImageGenProvider())
