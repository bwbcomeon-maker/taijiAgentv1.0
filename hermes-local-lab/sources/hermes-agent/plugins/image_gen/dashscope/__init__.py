"""DashScope Qwen-Image generation backend."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from agent.image_gen_provider import DEFAULT_ASPECT_RATIO, ImageGenProvider, error_response
from plugins.image_gen.domestic_common import (
    SIZE_MAP_STAR,
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

DEFAULT_MODEL = "qwen-image-2.0-pro"
DEFAULT_REGION = "cn-beijing"
TIMEOUT_SECONDS = 180


def _load_options() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        image_cfg = cfg.get("image_gen") if isinstance(cfg, dict) else None
        options = image_cfg.get("options") if isinstance(image_cfg, dict) else None
        return options if isinstance(options, dict) else {}
    except Exception:
        return {}


def _option_value(name: str, env_var: str) -> str:
    value = env_value(env_var)
    if value:
        return value
    return str(_load_options().get(name) or "").strip()


class DashScopeQwenImageProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "dashscope"

    @property
    def display_name(self) -> str:
        return "通义 Qwen-Image"

    def is_available(self) -> bool:
        return bool(env_value("DASHSCOPE_API_KEY") and _option_value("workspace_id", "DASHSCOPE_WORKSPACE_ID"))

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "qwen-image-2.0-pro",
                "display": "Qwen Image 2.0 Pro",
                "speed": "varies",
                "strengths": "中文提示、文字渲染、通义视觉生成",
                "price": "Model Studio billing",
            },
            {
                "id": "qwen-image",
                "display": "Qwen Image",
                "speed": "varies",
                "strengths": "中文提示、通用文生图",
                "price": "Model Studio billing",
            },
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "通义 Qwen-Image",
            "badge": "国产",
            "tag": "阿里百炼 / DashScope Qwen-Image 文生图",
            "domestic": True,
            "integration_status": "stable",
            "call_mode": "sync",
            "supported_regions": ["cn-beijing", "cn-shanghai"],
            "env_vars": [{"key": "DASHSCOPE_API_KEY", "prompt": "DashScope API Key"}],
            "credential_fields": [
                credential_field(name="api_key", env_var="DASHSCOPE_API_KEY", label="API Key"),
                credential_field(
                    name="workspace_id",
                    env_var="DASHSCOPE_WORKSPACE_ID",
                    label="Workspace ID",
                    secret=False,
                    placeholder="ws-xxxxxxxx",
                ),
                credential_field(
                    name="region",
                    env_var="DASHSCOPE_REGION",
                    label="Region",
                    required=False,
                    secret=False,
                    placeholder=DEFAULT_REGION,
                ),
            ],
        }

    def _model(self, requested: Any = "") -> str:
        model = str(requested or "").strip()
        ids = {item["id"] for item in self.list_models()}
        return model if model in ids else DEFAULT_MODEL

    def _endpoint(self) -> str:
        workspace = _option_value("workspace_id", "DASHSCOPE_WORKSPACE_ID")
        region = _option_value("region", "DASHSCOPE_REGION") or DEFAULT_REGION
        return f"https://{workspace}.{region}.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

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
        missing = []
        if not env_value("DASHSCOPE_API_KEY"):
            missing.append("DASHSCOPE_API_KEY")
        if not _option_value("workspace_id", "DASHSCOPE_WORKSPACE_ID"):
            missing.append("DASHSCOPE_WORKSPACE_ID")
        if missing:
            return auth_error(missing=missing, provider=self.name, model=model, prompt=prompt, aspect_ratio=aspect)

        api_key = env_value("DASHSCOPE_API_KEY")
        payload = {
            "model": model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": {
                "size": SIZE_MAP_STAR.get(aspect, SIZE_MAP_STAR["landscape"]),
            },
        }
        body, error = post_json(
            url=self._endpoint(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload=payload,
            timeout=TIMEOUT_SECONDS,
            provider=self.name,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            secrets=(api_key, _option_value("workspace_id", "DASHSCOPE_WORKSPACE_ID")),
            request_post=requests.post,
        )
        if error:
            return error
        image_url = first_url(body)
        if not image_url:
            return error_response(
                error="dashscope response contained no image URL.",
                error_type="empty_response",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        return cached_success(
            image_url=image_url,
            cache_prefix="dashscope_qwen_image",
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            provider=self.name,
            save_image=save_url_image,
        )


def register(ctx):
    ctx.register_image_gen_provider(DashScopeQwenImageProvider())
