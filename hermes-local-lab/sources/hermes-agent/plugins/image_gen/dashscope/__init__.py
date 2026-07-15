"""DashScope Qwen-Image generation backend."""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

from agent.alibaba_endpoints import (
    DEFAULT_REGION,
    PUBLIC_ROOTS,
    build_image_generation_url,
)
from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    save_b64_image,
)
from agent.provider_credentials import resolve_api_key
from plugins.image_gen.domestic_common import (
    SIZE_MAP_STAR,
    auth_error,
    cached_success,
    credential_field,
    env_value,
    first_url,
    normalized_aspect,
    post_json,
    validate_prompt,
)
from tools.url_safety import is_safe_url

DEFAULT_MODEL = "qwen-image-2.0-pro"
TIMEOUT_SECONDS = 180
DOWNLOAD_TIMEOUT_SECONDS = 60
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_REDIRECTS = 3
_IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class DashScopeConfigurationError(RuntimeError):
    """Raised when the saved runtime configuration cannot be loaded safely."""


def _post_without_redirects(url: str, **kwargs: Any):
    return requests.post(url, allow_redirects=False, **kwargs)


def _save_safe_image_url(
    url: str,
    *,
    prefix: str = "dashscope_qwen_image",
) -> Any:
    """Download a DashScope image with per-hop SSRF checks."""
    current_url = str(url or "").strip()
    for redirect_count in range(MAX_IMAGE_REDIRECTS + 1):
        # Standard DNS preflight cannot pin the request's DNS result; a
        # rebinding TOCTOU window remains between this check and connect.
        if not is_safe_url(current_url):
            raise ValueError("DashScope image URL failed safety validation")
        response = requests.get(
            current_url,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            stream=True,
            allow_redirects=False,
        )
        try:
            if response.status_code in _REDIRECT_STATUSES:
                if redirect_count >= MAX_IMAGE_REDIRECTS:
                    raise ValueError("DashScope image URL exceeded redirect limit")
                location = str(response.headers.get("Location") or "").strip()
                if not location:
                    raise ValueError("DashScope image redirect omitted Location")
                current_url = urljoin(current_url, location)
                continue

            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            extension = _IMAGE_EXTENSIONS.get(content_type)
            if extension is None:
                raise ValueError("DashScope image response has an unsupported content type")
            content_length = str(response.headers.get("Content-Length") or "").strip()
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = None
                if declared_size is not None and declared_size > MAX_IMAGE_BYTES:
                    raise ValueError("DashScope image response exceeds 25MB limit")

            image_bytes = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                image_bytes.extend(chunk)
                if len(image_bytes) > MAX_IMAGE_BYTES:
                    raise ValueError("DashScope image response exceeds 25MB limit")
            if not image_bytes:
                raise ValueError("DashScope image response was empty")
            return save_b64_image(
                base64.b64encode(bytes(image_bytes)).decode("ascii"),
                prefix=prefix,
                extension=extension,
            )
        finally:
            response.close()
    raise ValueError("DashScope image URL exceeded redirect limit")


def _load_config_data() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        raise DashScopeConfigurationError("DashScope configuration could not be loaded") from exc


def _load_image_config(config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config_data if isinstance(config_data, dict) else _load_config_data()
    image_cfg = cfg.get("image_gen") if isinstance(cfg, dict) else None
    return image_cfg if isinstance(image_cfg, dict) else {}


def _resolve_api_key(
    config_data: dict[str, Any] | None = None,
    image_cfg: dict[str, Any] | None = None,
) -> str:
    config_data = config_data if isinstance(config_data, dict) else _load_config_data()
    image_cfg = image_cfg if isinstance(image_cfg, dict) else _load_image_config(config_data)
    credential_ref = ""
    if str(image_cfg.get("provider") or "").strip().lower() == "dashscope":
        credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    try:
        return resolve_api_key(
            "dashscope",
            credential_ref,
            config_data=config_data,
        )
    except ValueError:
        return ""


def _option_value(
    name: str,
    env_var: str,
    *,
    image_cfg: dict[str, Any] | None = None,
) -> str:
    image_cfg = image_cfg if isinstance(image_cfg, dict) else _load_image_config()
    credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    options = image_cfg.get("options")
    if not isinstance(options, dict):
        options = {}
    if credential_ref:
        return str(options.get(name) or "").strip()
    value = env_value(env_var)
    if value:
        return value
    return str(options.get(name) or "").strip()


class DashScopeQwenImageProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "dashscope"

    @property
    def display_name(self) -> str:
        return "通义 Qwen-Image"

    def is_available(self) -> bool:
        try:
            config_data = _load_config_data()
            image_cfg = _load_image_config(config_data)
            if not _resolve_api_key(config_data, image_cfg):
                return False
            endpoint = self._endpoint(image_cfg=image_cfg)
        except (DashScopeConfigurationError, ValueError):
            return False
        endpoint_mode = (
            _option_value(
                "endpoint_mode",
                "DASHSCOPE_ENDPOINT_MODE",
                image_cfg=image_cfg,
            )
            or "workspace"
        )
        if endpoint_mode.strip().lower() == "custom":
            return is_safe_url(endpoint)
        return True

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
            "supported_regions": list(PUBLIC_ROOTS),
            "env_vars": [{"key": "DASHSCOPE_API_KEY", "prompt": "DashScope API Key"}],
            "credential_fields": [
                credential_field(name="api_key", env_var="DASHSCOPE_API_KEY", label="API Key"),
                credential_field(
                    name="endpoint_mode",
                    env_var="DASHSCOPE_ENDPOINT_MODE",
                    label="Endpoint Mode",
                    required=False,
                    secret=False,
                    placeholder="workspace",
                )
                | {"options": ["workspace", "custom"]},
                credential_field(
                    name="workspace_id",
                    env_var="DASHSCOPE_WORKSPACE_ID",
                    label="Workspace ID",
                    required=False,
                    secret=False,
                    placeholder="llm-demo",
                ),
                credential_field(
                    name="region",
                    env_var="DASHSCOPE_REGION",
                    label="Region",
                    required=False,
                    secret=False,
                    placeholder=DEFAULT_REGION,
                )
                | {"options": list(PUBLIC_ROOTS)},
                credential_field(
                    name="base_url",
                    env_var="DASHSCOPE_BASE_URL",
                    label="Custom Base URL",
                    required=False,
                    secret=False,
                    placeholder="https://gateway.example.com",
                ),
            ],
        }

    def _model(self, requested: Any = "") -> str:
        model = str(requested or "").strip()
        ids = {item["id"] for item in self.list_models()}
        if not model:
            return DEFAULT_MODEL
        if model not in ids:
            raise ValueError(f"Unsupported DashScope image model: {model}")
        return model

    def _endpoint(self, *, image_cfg: dict[str, Any] | None = None) -> str:
        return build_image_generation_url(
            endpoint_mode=_option_value(
                "endpoint_mode", "DASHSCOPE_ENDPOINT_MODE", image_cfg=image_cfg
            )
            or "workspace",
            workspace_prefix=_option_value(
                "workspace_id", "DASHSCOPE_WORKSPACE_ID", image_cfg=image_cfg
            ),
            region=_option_value(
                "region", "DASHSCOPE_REGION", image_cfg=image_cfg
            )
            or DEFAULT_REGION,
            custom_url=_option_value(
                "base_url", "DASHSCOPE_BASE_URL", image_cfg=image_cfg
            ),
        )

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
                error="Unsupported DashScope image model.",
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
            config_data = _load_config_data()
            image_cfg = _load_image_config(config_data)
            api_key = _resolve_api_key(config_data, image_cfg)
        except DashScopeConfigurationError:
            return error_response(
                error="DashScope configuration could not be loaded.",
                error_type="configuration_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        missing = []
        if not api_key:
            missing.append("DASHSCOPE_API_KEY")
        endpoint_mode = (
            _option_value(
                "endpoint_mode",
                "DASHSCOPE_ENDPOINT_MODE",
                image_cfg=image_cfg,
            )
            or "workspace"
        )
        if endpoint_mode == "workspace" and not _option_value(
            "workspace_id", "DASHSCOPE_WORKSPACE_ID", image_cfg=image_cfg
        ):
            missing.append("DASHSCOPE_WORKSPACE_ID")
        if endpoint_mode == "custom" and not _option_value(
            "base_url", "DASHSCOPE_BASE_URL", image_cfg=image_cfg
        ):
            missing.append("DASHSCOPE_BASE_URL")
        if missing:
            return auth_error(missing=missing, provider=self.name, model=model, prompt=prompt, aspect_ratio=aspect)

        try:
            endpoint = self._endpoint(image_cfg=image_cfg)
        except ValueError:
            return error_response(
                error="DashScope endpoint configuration is invalid.",
                error_type="endpoint_invalid",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        if endpoint_mode.strip().lower() == "custom" and not is_safe_url(endpoint):
            # This is the repository-standard DNS preflight. It cannot pin the
            # connection's DNS answer; disabling redirects below closes the
            # redirect hop, but the documented DNS-rebinding window remains.
            return error_response(
                error="DashScope custom endpoint failed URL safety validation.",
                error_type="endpoint_invalid",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
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
            url=endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload=payload,
            timeout=TIMEOUT_SECONDS,
            provider=self.name,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect,
            secrets=(
                api_key,
                _option_value(
                    "workspace_id",
                    "DASHSCOPE_WORKSPACE_ID",
                    image_cfg=image_cfg,
                ),
            ),
            request_post=_post_without_redirects,
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
            save_image=_save_safe_image_url,
        )


def register(ctx):
    ctx.register_image_gen_provider(DashScopeQwenImageProvider())
