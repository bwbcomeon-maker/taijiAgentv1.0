"""Config-driven OpenAI-compatible image generation providers."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, cast
from urllib.parse import unquote, urlsplit

from agent.image_gen_verification import require_image_gen_request_binding
from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)
from agent.provider_credentials import (
    credential_secret_env,
    normalize_credential_id,
    process_env_fallback_allowed,
    resolve_api_key,
    resolve_secret_env_value,
)
from agent.safe_outbound_http import (
    NetworkScope,
    SafeOutboundError,
    _canonical_origin_host,
    _require_https_url,
    _validate_address,
    normalize_network_scope,
    read_bounded_json,
    request_pinned_https,
    request_via_trusted_proxy,
    resolve_trusted_proxy_profile,
)

logger = logging.getLogger(__name__)

CUSTOM_PROVIDER_PREFIX = "custom:"
OPENAI_IMAGES_TRANSPORT = "openai_images"
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
_LEGACY_API_KEY_ENV_MARKER_KEY = "_legacy_api_key_env_read_compat"
_LEGACY_API_KEY_ENV_MARKER = object()
_MISSING = object()


def _path_has_unsafe_endpoint_shape(path: str) -> bool:
    """Reject traversal and request-smuggling shapes, including nested encoding."""
    candidate = path
    for _ in range(len(path) + 1):
        if "\\" in candidate or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in candidate
        ):
            return True
        if any(segment in {".", ".."} for segment in candidate.split("/")):
            return True
        decoded = unquote(candidate)
        if decoded == candidate:
            return False
        candidate = decoded
    return True


def _normalize_https_endpoint_url(value: Any, *, label: str) -> str:
    """Normalize a credential-bearing HTTPS endpoint without classifying its IP."""
    raw = str(value or "")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
        raise ValueError(f"{label} Base URL 含有非法控制字符。")
    url = raw.strip().rstrip("/")
    if (
        not url
        or "\\" in url
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in url)
    ):
        raise ValueError(f"{label} Base URL 格式无效。")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} Base URL 的端口必须在 1 到 65535 之间。") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label} Base URL 的端口必须在 1 到 65535 之间。")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in url
        or "#" in url
        or parsed.query
        or parsed.fragment
        or _path_has_unsafe_endpoint_shape(parsed.path)
    ):
        raise ValueError(
            f"{label} Base URL 必须是不含账号、查询参数、片段或路径穿越的完整 HTTPS 地址。"
        )

    try:
        from agent.safe_outbound_http import _canonical_origin_host

        _canonical_origin_host(parsed.hostname)
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} Base URL 的主机名格式无效。") from exc
    return url


def normalize_custom_image_provider_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith(CUSTOM_PROVIDER_PREFIX):
        raw = raw[len(CUSTOM_PROVIDER_PREFIX) :]
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
    return _normalize_https_endpoint_url(value, label="外部图片模型")


def openai_images_generation_endpoint(base_url: Any) -> str:
    """Normalize the exact request endpoint used by custom image Providers."""
    base = _normalize_base_url(base_url).rstrip("/")
    if base.endswith("/images/generations"):
        return base
    return f"{base}/images/generations"


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


def _normalize_credential_ref(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("外部图片模型 credential_ref 必须是字符串。")
    return normalize_credential_id(value)


def _normalize_network_config(entry: dict[str, Any]) -> tuple[str, str]:
    try:
        scope = normalize_network_scope(
            entry.get("network_scope"),
            default=NetworkScope.PUBLIC_DIRECT,
        )
    except SafeOutboundError as exc:
        raise ValueError("network_scope 配置无效。") from exc
    raw_profile = entry.get("trusted_proxy_profile")
    if raw_profile is None:
        profile = ""
    elif isinstance(raw_profile, str):
        profile = raw_profile.strip()
    else:
        raise ValueError("trusted_proxy_profile 必须是字符串。")
    if profile and (
        len(profile) > 64
        or not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?",
            profile,
        )
    ):
        raise ValueError("trusted_proxy_profile 格式无效。")
    if scope is NetworkScope.TRUSTED_PROXY:
        if not profile:
            raise ValueError("trusted_proxy 必须引用已批准的 named profile。")
    elif profile:
        raise ValueError("仅 trusted_proxy 可配置 trusted_proxy_profile。")
    return scope.value, profile


def normalize_custom_image_provider_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("外部图片模型配置必须是对象。")
    if "api_key_env" in entry:
        raise ValueError("外部图片模型不允许配置 api_key_env，请使用 credential_ref。")
    provider_id = normalize_custom_image_provider_id(
        entry.get("id") or entry.get("provider_id")
    )
    models = _normalize_models(
        entry.get("models"), entry.get("default_model") or entry.get("model")
    )
    default_model = str(
        entry.get("default_model") or entry.get("model") or models[0]
    ).strip()
    if default_model not in models:
        models.insert(0, default_model)
    network_scope, trusted_proxy_profile = _normalize_network_config(entry)
    return {
        "id": provider_id,
        "name": str(entry.get("name") or provider_id).strip()[:80],
        "base_url": _normalize_base_url(entry.get("base_url")),
        "credential_ref": _normalize_credential_ref(entry.get("credential_ref")),
        "allow_custom_model_id": entry.get("allow_custom_model_id") is True,
        "models": models,
        "default_model": default_model,
        "size_map": _normalize_size_map(entry.get("size_map")),
        "response_format": _normalize_response_format(entry.get("response_format")),
        "timeout_seconds": _normalize_timeout(entry.get("timeout_seconds")),
        "network_scope": network_scope,
        "trusted_proxy_profile": trusted_proxy_profile,
    }


def _normalize_loaded_custom_image_provider_entry(
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Preserve an unforgeable marker added only by the persisted-config loader."""
    if not isinstance(entry, dict):
        raise ValueError("外部图片模型配置必须是对象。")
    marker = entry.get(_LEGACY_API_KEY_ENV_MARKER_KEY, _MISSING)
    cleaned = dict(entry)
    cleaned.pop(_LEGACY_API_KEY_ENV_MARKER_KEY, None)
    normalized = normalize_custom_image_provider_entry(cleaned)
    if marker is _MISSING:
        return normalized
    if marker is not _LEGACY_API_KEY_ENV_MARKER or normalized.get("credential_ref"):
        raise ValueError("旧版外部图片模型密钥引用无效。")
    normalized[_LEGACY_API_KEY_ENV_MARKER_KEY] = _LEGACY_API_KEY_ENV_MARKER
    return normalized


def _normalize_persisted_custom_image_provider_entry(
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Read one exact legacy env binding without accepting caller-selected envs."""
    if not isinstance(entry, dict):
        raise ValueError("外部图片模型配置必须是对象。")
    if "api_key_env" not in entry:
        return normalize_custom_image_provider_entry(entry)
    if "credential_ref" in entry:
        raise ValueError("旧版 api_key_env 不能与 credential_ref 同时存在。")

    provider_id = normalize_custom_image_provider_id(
        entry.get("id") or entry.get("provider_id")
    )
    expected_env = custom_image_provider_env_var(provider_id)
    if (
        not isinstance(entry.get("api_key_env"), str)
        or entry["api_key_env"] != expected_env
    ):
        raise ValueError("旧版 api_key_env 必须与 Provider ID 的固定环境变量一致。")

    cleaned = dict(entry)
    cleaned.pop("api_key_env", None)
    normalized = normalize_custom_image_provider_entry(cleaned)
    if normalized.get("credential_ref"):
        raise ValueError("旧版 api_key_env 不能与 credential_ref 同时存在。")
    normalized[_LEGACY_API_KEY_ENV_MARKER_KEY] = _LEGACY_API_KEY_ENV_MARKER
    return normalized


def custom_image_provider_secret_env(entry: dict[str, Any]) -> str:
    """Return the exact secret env bound by one normalized custom entry."""
    normalized = _normalize_loaded_custom_image_provider_entry(entry)
    credential_ref = str(normalized.get("credential_ref") or "")
    if credential_ref:
        return credential_secret_env(credential_ref)
    if (
        normalized.get(_LEGACY_API_KEY_ENV_MARKER_KEY)
        is _LEGACY_API_KEY_ENV_MARKER
    ):
        return custom_image_provider_env_var(normalized.get("id"))
    return ""


def _entry_api_key(
    entry: dict[str, Any],
    *,
    allow_process_fallback: bool | None = None,
) -> str:
    from hermes_constants import get_config_path

    config_path = get_config_path()
    allow_process_fallback = process_env_fallback_allowed(
        allow_process_fallback
    )
    credential_ref = str(entry.get("credential_ref") or "")
    if credential_ref:
        return resolve_api_key(
            "custom",
            credential_ref,
            config_path=config_path,
            allow_process_fallback=allow_process_fallback,
        ).strip()
    if entry.get(_LEGACY_API_KEY_ENV_MARKER_KEY) is _LEGACY_API_KEY_ENV_MARKER:
        secret_env = custom_image_provider_env_var(entry.get("id"))
        return resolve_secret_env_value(
            secret_env,
            config_path=config_path,
            allow_process_fallback=allow_process_fallback,
        )
    return ""


def custom_image_provider_public_row(
    entry: dict[str, Any], *, active_provider: str = ""
) -> dict[str, Any]:
    try:
        normalized = _normalize_loaded_custom_image_provider_entry(entry)
    except ValueError:
        if not isinstance(entry, dict) or "api_key_env" not in entry:
            raise
        normalized = _normalize_persisted_custom_image_provider_entry(entry)
    provider_name = custom_image_provider_name(normalized["id"])
    credential_ref = str(normalized.get("credential_ref") or "")
    secret_env = custom_image_provider_secret_env(normalized)
    legacy_env = bool(
        normalized.get(_LEGACY_API_KEY_ENV_MARKER_KEY) is _LEGACY_API_KEY_ENV_MARKER
    )
    try:
        configured = bool(_entry_api_key(normalized))
    except ValueError:
        configured = False
    return {
        "id": provider_name,
        "name": normalized["name"],
        "description": "OpenAI Images 兼容外部图片模型",
        "badge": "外部",
        # Field completeness is not proof that the remote endpoint works.
        "available": False,
        "configured": bool(
            configured and normalized["base_url"] and normalized["default_model"]
        ),
        "verification_status": "configured_unverified"
        if configured
        else "not_configured",
        "active": provider_name == str(active_provider or "").strip(),
        "requires_env": [secret_env] if secret_env else [],
        "key_status": {
            "configured": configured,
            "source": (
                "legacy_env_var"
                if configured and legacy_env
                else "credential_ref"
                if configured
                else "none"
            ),
            "credential_ref": credential_ref,
            "env_var": secret_env,
        },
        "reason_code": "configured_unverified"
        if configured
        else "authorization_required",
        "status_message": "已配置，尚未验证。"
        if configured
        else "外部图片模型密钥未配置。",
        "models": [{"id": model, "label": model} for model in normalized["models"]],
        "default_model": normalized["default_model"],
        "oauth_managed": False,
        "custom": True,
        "base_url": normalized["base_url"],
        "size_map": dict(normalized["size_map"]),
        "base_url_configured": True,
        "response_format": normalized["response_format"],
        "timeout_seconds": normalized["timeout_seconds"],
        "allow_custom_model_id": normalized["allow_custom_model_id"],
        "network_scope": normalized["network_scope"],
        "trusted_proxy_profile": normalized["trusted_proxy_profile"],
    }


def load_custom_image_provider_entries(
    config_data: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    if config_data is None:
        try:
            from hermes_cli.config import load_config

            config_data = load_config()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not load custom image providers: %s", exc)
            return []
    raw_entries = (
        config_data.get("custom_image_providers")
        if isinstance(config_data, dict)
        else None
    )
    if not isinstance(raw_entries, list):
        return []
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_entries:
        try:
            normalized = _normalize_persisted_custom_image_provider_entry(item)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping invalid custom image provider: %s", exc)
            continue
        provider_id = normalized["id"]
        if provider_id in seen_ids:
            raise ValueError(f"外部图片模型配置包含重复 Provider ID：{provider_id}")
        seen_ids.add(provider_id)
        entries.append(normalized)
    return entries


class ConfigurableOpenAIImageProvider(ImageGenProvider):
    """OpenAI Images compatible backend described by config.yaml."""

    _supports_pinned_image_request_binding = True

    def __init__(self, entry: dict[str, Any]) -> None:
        self._entry = _normalize_loaded_custom_image_provider_entry(entry)

    @property
    def name(self) -> str:
        return custom_image_provider_name(self._entry["id"])

    @property
    def display_name(self) -> str:
        return self._entry["name"]

    def is_available(self) -> bool:
        try:
            api_key = _entry_api_key(self._entry)
        except ValueError:
            return False
        return bool(
            self._entry.get("base_url") and self._entry.get("default_model") and api_key
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
        secret_env = custom_image_provider_secret_env(self._entry)
        return {
            "name": self.display_name,
            "badge": "外部",
            "tag": "OpenAI Images 兼容外部图片模型",
            "env_vars": (
                [
                    {
                        "key": secret_env,
                        "prompt": f"{self.display_name} API key",
                        "url": "",
                    }
                ]
                if secret_env
                else []
            ),
            "allow_custom_model_id": self._entry["allow_custom_model_id"],
        }

    def _endpoint(self) -> str:
        return openai_images_generation_endpoint(self._entry["base_url"])

    def _model(self, requested: Any = "") -> str:
        model = str(requested or "").strip()
        if not model:
            return self._entry["default_model"]
        if (
            model not in self._entry["models"]
            and not self._entry["allow_custom_model_id"]
        ):
            raise ValueError(f"Unsupported custom image model: {model}")
        return model

    def _cache_prefix(self, model: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", model).strip("._-")[:80] or "model"
        return f"custom_{self._entry['id']}_{token}"

    def _network_route(self) -> tuple[NetworkScope, str]:
        scope = normalize_network_scope(self._entry.get("network_scope"))
        profile_name = str(self._entry.get("trusted_proxy_profile") or "")
        if scope is not NetworkScope.TRUSTED_PROXY:
            if profile_name:
                raise SafeOutboundError("safe_transport_unavailable")
            return scope, ""

        profile = cast(Any, resolve_trusted_proxy_profile(profile_name))
        if isinstance(profile, dict):
            approved = profile.get("approved")
            capabilities = profile.get("capabilities")
        else:
            approved = getattr(profile, "approved", None)
            capabilities = getattr(profile, "capabilities", None)
        if (
            approved is not True
            or not isinstance(capabilities, (list, tuple, set, frozenset))
            or not {
                "public_egress",
                "dns_ip_classification",
            }.issubset(set(capabilities))
        ):
            raise SafeOutboundError("trusted_proxy_unavailable")

        endpoint = self._endpoint()
        _require_https_url(endpoint)
        parsed = urlsplit(endpoint)
        _normalized_host, literal = _canonical_origin_host(str(parsed.hostname or ""))
        if literal is not None:
            _validate_address(literal, NetworkScope.PUBLIC_DIRECT)
        return scope, profile_name

    @staticmethod
    def _transport_error_reason(error: Exception) -> str:
        if isinstance(error, SafeOutboundError):
            return error.reason_code
        if str(error) == "trusted_proxy_origin_blocked":
            return "trusted_proxy_origin_blocked"
        return "safe_transport_unavailable"

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        provider = self.name
        requested_model = str(kwargs.get("model") or "").strip()
        try:
            model = self._model(requested_model)
        except ValueError:
            return error_response(
                error="Unsupported custom image model.",
                error_type="invalid_argument",
                provider=provider,
                model=requested_model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider=provider,
                model=model,
                aspect_ratio=aspect,
            )
        try:
            network_scope, trusted_proxy_profile = self._network_route()
        except Exception as exc:  # noqa: BLE001
            reason = self._transport_error_reason(exc)
            return error_response(
                error=f"外部图片模型请求失败：{reason}",
                error_type="api_error",
                provider=provider,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        raw_binding = kwargs.get("_runtime_binding")
        try:
            api_key = (
                require_image_gen_request_binding(
                    raw_binding,
                    provider=provider,
                    model=model,
                ).api_key
                if raw_binding is not None
                else _entry_api_key(self._entry)
            )
        except ValueError:
            api_key = ""
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
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            if network_scope is NetworkScope.TRUSTED_PROXY:
                response_context = request_via_trusted_proxy(
                    method="POST",
                    url=self._endpoint(),
                    trusted_proxy_profile=trusted_proxy_profile,
                    headers=headers,
                    json_body=payload,
                    timeout=self._entry["timeout_seconds"],
                    follow_redirects=False,
                )
            else:
                response_context = request_pinned_https(
                    method="POST",
                    url=self._endpoint(),
                    network_scope=network_scope.value,
                    headers=headers,
                    json_body=payload,
                    timeout=self._entry["timeout_seconds"],
                    follow_redirects=False,
                )
            with response_context as response:
                status_code = int(response.status_code)
                body = read_bounded_json(response)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Custom image provider request failed", exc_info=True)
            reason = self._transport_error_reason(exc)
            return error_response(
                error=f"外部图片模型请求失败：{reason}",
                error_type="api_error",
                provider=provider,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not 200 <= status_code < 300:
            message = _response_error_message(body, secret=api_key)
            return error_response(
                error=f"外部图片模型请求失败：HTTP {status_code}{message}",
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
                image_ref = str(
                    save_url_image(
                        url,
                        prefix=prefix,
                        network_scope=network_scope.value,
                        trusted_proxy_profile=trusted_proxy_profile or None,
                    )
                )
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


def _response_error_message(body: Any, *, secret: str = "") -> str:
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
    if secret:
        message = message.replace(secret, "[已隐藏]")
    redacted = re.sub(
        r"(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]*secret[A-Za-z0-9_-]*)",
        "[已隐藏]",
        message,
    )
    return f"：{redacted[:240]}"


def build_configured_custom_image_provider(
    provider_name: str,
    config_data: dict[str, Any],
) -> ConfigurableOpenAIImageProvider | None:
    """Build one request-local provider from one already-loaded config."""
    try:
        requested = custom_image_provider_name(provider_name)
    except ValueError:
        return None
    for entry in load_custom_image_provider_entries(config_data):
        provider = ConfigurableOpenAIImageProvider(entry)
        if provider.name == requested:
            return provider
    return None


def register_configured_custom_image_providers(
    config_data: Optional[dict[str, Any]] = None,
) -> None:
    from agent.image_gen_registry import register_provider, unregister_provider

    global _REGISTERED_CUSTOM_PROVIDER_NAMES
    for name in list(_REGISTERED_CUSTOM_PROVIDER_NAMES):
        unregister_provider(name)
    _REGISTERED_CUSTOM_PROVIDER_NAMES = set()

    for entry in load_custom_image_provider_entries(config_data):
        provider = ConfigurableOpenAIImageProvider(entry)
        register_provider(provider)
        _REGISTERED_CUSTOM_PROVIDER_NAMES.add(provider.name)
