"""Shared helpers for built-in domestic image generation providers."""

from __future__ import annotations

import os
import re
from typing import Any, Iterable

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    error_response,
    resolve_aspect_ratio,
    save_url_image,
    success_response,
)
from agent.provider_credentials import auth_schema

SIZE_MAP_X = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}
SIZE_MAP_STAR = {
    "landscape": "1664*928",
    "square": "1328*1328",
    "portrait": "928*1664",
}
ASPECT_RATIO_MAP = {
    "landscape": "16:9",
    "square": "1:1",
    "portrait": "9:16",
}


def credential_field(
    *,
    name: str,
    env_var: str,
    label: str,
    required: bool = True,
    secret: bool = True,
    placeholder: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "env_var": env_var,
        "label": label,
        "required": required,
        "secret": secret,
        "placeholder": placeholder,
    }


def normalized_setup_contract(
    schema: dict[str, Any] | None,
    *,
    provider_family: str,
    capabilities: Iterable[str],
    auth_type: str = "api_key",
    transport: str,
    models: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Return the common public provider contract used by settings UI."""
    source = schema if isinstance(schema, dict) else {}
    auth = auth_schema(source.get("auth_type") or auth_type)
    raw_fields = source.get("credential_fields")
    fields = [dict(field) for field in raw_fields if isinstance(field, dict)] if isinstance(raw_fields, list) else []
    if not fields:
        fields = [dict(field) for field in auth["credential_fields"]]
    credentials = [
        field
        for field in fields
        if bool(field.get("credential")) or bool(field.get("secret", True))
    ]
    endpoints = [field for field in fields if field not in credentials]
    return {
        "provider_family": str(provider_family or "").strip(),
        "capabilities": [str(item) for item in capabilities if str(item).strip()],
        "auth_type": auth["auth_type"],
        "transport": str(source.get("transport") or transport or "").strip(),
        "credential_fields": credentials,
        "endpoint_fields": endpoints,
        "models": [dict(item) for item in models if isinstance(item, dict)],
        "auth_editable": bool(source.get("auth_implemented", auth["editable"])),
        "auth_message": str(source.get("auth_message") or auth["message"]),
    }


def env_value(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def missing_required(required_envs: Iterable[str]) -> list[str]:
    return [name for name in required_envs if not env_value(name)]


def auth_error(
    *,
    missing: Iterable[str],
    provider: str,
    model: str,
    prompt: str = "",
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> dict[str, Any]:
    names = ", ".join(missing)
    return error_response(
        error=f"{names} not set. Configure this image provider in model settings.",
        error_type="auth_required",
        provider=provider,
        model=model,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
    )


def validate_prompt(
    prompt: str,
    *,
    provider: str,
    model: str,
    aspect_ratio: str,
) -> tuple[str, dict[str, Any] | None]:
    text = (prompt or "").strip()
    if text:
        return text, None
    return "", error_response(
        error="Prompt is required and must be a non-empty string",
        error_type="invalid_argument",
        provider=provider,
        model=model,
        aspect_ratio=aspect_ratio,
    )


def redact_secrets(message: Any, secrets: Iterable[str]) -> str:
    text = str(message or "").strip()
    for secret in secrets:
        secret = str(secret or "").strip()
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]*secret[A-Za-z0-9_-]*)", "[redacted]", text)
    return text[:500]


def error_message_from_response(response: Any, secrets: Iterable[str]) -> str:
    parts: list[str] = []
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        for key in ("error", "message", "msg"):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("msg") or value.get("code")
            if value:
                parts.append(str(value))
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        parts.append(text[:300])
    return redact_secrets(" ".join(parts), secrets)


def post_json(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    provider: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    secrets: Iterable[str],
    request_post: Any | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        post = request_post or requests.post
        response = post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        return None, error_response(
            error=f"{provider} image generation request failed: {redact_secrets(exc, secrets)}",
            error_type="api_error",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    if getattr(response, "status_code", 200) >= 400:
        detail = error_message_from_response(response, secrets)
        return None, error_response(
            error=f"{provider} image generation failed: HTTP {response.status_code}{(': ' + detail) if detail else ''}",
            error_type="api_error",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    try:
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        return None, error_response(
            error=f"{provider} returned an invalid response: {redact_secrets(exc, secrets)}",
            error_type="invalid_response",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    if not isinstance(body, dict):
        return None, error_response(
            error=f"{provider} returned a non-object response.",
            error_type="invalid_response",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    return body, None


def first_url(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("url", "image", "image_url", "imageUrl"):
            url = value.get(key)
            if isinstance(url, str) and url.strip().startswith(("http://", "https://")):
                return url.strip()
        for nested in value.values():
            found = first_url(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = first_url(item)
            if found:
                return found
    elif isinstance(value, str) and value.strip().startswith(("http://", "https://")):
        return value.strip()
    return ""


def cached_success(
    *,
    image_url: str,
    cache_prefix: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    provider: str,
    extra: dict[str, Any] | None = None,
    save_image: Any | None = None,
) -> dict[str, Any]:
    try:
        saver = save_image or save_url_image
        image_ref = str(saver(image_url, prefix=cache_prefix))
    except Exception as exc:
        return error_response(
            error=f"{provider} image result download failed: {exc}",
            error_type="io_error",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    return success_response(
        image=image_ref,
        model=model,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        provider=provider,
        extra=extra,
    )


def normalized_aspect(value: str | None) -> str:
    return resolve_aspect_ratio(value)
