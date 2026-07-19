"""Shared helpers for built-in domestic image generation providers."""

from __future__ import annotations

import os
import re
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    error_response,
    resolve_aspect_ratio,
    save_url_image,
    success_response,
)
from agent.provider_credentials import (
    auth_schema,
    load_credential_config,
    provider_family,
    resolve_api_key,
)
from agent.safe_outbound_http import (
    SafeOutboundError,
    read_bounded_json,
    request_pinned_https,
)

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
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
_ERROR_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_BEARER_RE = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}",
    re.IGNORECASE,
)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"authorization|credentials?|jwt|token|sig(?:nature)?|password|passphrase|"
    r"api[_.-]?key|access[_.-]?(?:key(?:[_.-]?(?:id|secret))?|token)|"
    r"refresh[_.-]?token|id[_.-]?token|client[_.-]?secret|"
    r"secret[_.-]?access[_.-]?key|secret(?:[_.-]?key)?|"
    r"session[_.-]?(?:id|token)|security[_.-]?token|private[_.-]?key|cookie"
    r")(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")


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


def provider_api_key(
    provider: str,
    *,
    config_data: dict[str, Any] | None = None,
) -> str:
    """Resolve the active provider's named key without weakening legacy fallback."""
    data = load_credential_config() if config_data is None else config_data
    if not isinstance(data, dict):
        raise ValueError("image provider configuration must be a mapping")
    image_cfg = data.get("image_gen")
    if image_cfg is None:
        image_cfg = {}
    if not isinstance(image_cfg, dict):
        raise ValueError("image_gen configuration must be a mapping")

    raw_active_provider = image_cfg.get("provider", "")
    if raw_active_provider is None:
        raw_active_provider = ""
    if not isinstance(raw_active_provider, str):
        raise ValueError("image_gen provider must be a string")

    credential_ref = ""
    if provider_family(raw_active_provider) == provider_family(provider):
        raw_credential_ref = image_cfg.get("credential_ref", "")
        if raw_credential_ref is None:
            raw_credential_ref = ""
        if not isinstance(raw_credential_ref, str):
            raise ValueError("image_gen credential_ref must be a string")
        credential_ref = raw_credential_ref.strip()

    return resolve_api_key(
        provider,
        credential_ref,
        config_data=data,
    )


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


def _safe_error_url(match: re.Match[str]) -> str:
    candidate = match.group(0)
    suffix = ""
    opener = match.string[match.start() - 1] if match.start() else ""
    closer = {"(": ")", "[": "]", "{": "}", "'": "'"}.get(opener)
    if closer:
        probe = candidate
        trailing = ""
        while probe and probe[-1] in ".,;:!?":
            trailing = probe[-1] + trailing
            probe = probe[:-1]
        if probe.endswith(closer):
            candidate = probe[:-1]
            suffix = closer + trailing
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return "[redacted-url]" + suffix
        hostname = parsed.hostname
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = parsed.port
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        netloc = hostname if port in {None, default_port} else f"{hostname}:{port}"
        return urlunsplit(
            (
                parsed.scheme.lower(),
                netloc,
                "/[redacted-path]",
                "",
                "",
            )
        ) + suffix
    except (TypeError, ValueError):
        return "[redacted-url]" + suffix


def redact_secrets(message: Any, secrets: Iterable[str]) -> str:
    text = _CONTROL_CHAR_RE.sub(" ", str(message or "")).strip()
    for secret in secrets:
        secret = str(secret or "").strip()
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _ERROR_URL_RE.sub(_safe_error_url, text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _JWT_RE.sub("[redacted]", text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[redacted]",
        text,
    )
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[redacted]", text)
    return text[:500]


def error_message_from_body(body: Any, secrets: Iterable[str]) -> str:
    parts: list[str] = []
    if isinstance(body, dict):
        for key in ("error", "message", "msg"):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("msg") or value.get("code")
            if value:
                parts.append(str(value))
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
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        with request_pinned_https(
            method="POST",
            url=url,
            network_scope="public_direct",
            headers=headers,
            json_body=payload,
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            status_code = int(response.status_code)
            if 200 <= status_code < 300:
                body = read_bounded_json(
                    response,
                    max_bytes=MAX_API_RESPONSE_BYTES,
                )
            else:
                try:
                    body = read_bounded_json(
                        response,
                        max_bytes=MAX_API_RESPONSE_BYTES,
                    )
                except SafeOutboundError:
                    body = None
    except SafeOutboundError as exc:
        invalid_response = exc.reason_code.startswith("provider_response_")
        prefix = (
            f"{provider} returned an invalid response"
            if invalid_response
            else f"{provider} image generation request failed"
        )
        return None, error_response(
            error=f"{prefix}: {redact_secrets(exc, secrets)}",
            error_type="invalid_response" if invalid_response else "api_error",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    except Exception as exc:
        return None, error_response(
            error=f"{provider} image generation request failed: {redact_secrets(exc, secrets)}",
            error_type="api_error",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    if not 200 <= status_code < 300:
        detail = error_message_from_body(body, secrets)
        return None, error_response(
            error=f"{provider} image generation failed: HTTP {status_code}{(': ' + detail) if detail else ''}",
            error_type="api_error",
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


def _https_image_url(url: str) -> bool:
    try:
        parsed = urlsplit(str(url or "").strip())
        return bool(
            parsed.scheme.lower() == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        )
    except (TypeError, ValueError):
        return False


def first_url(
    value: Any,
    *,
    _depth: int = 0,
    max_depth: int = 8,
) -> str:
    if _depth >= max_depth:
        return ""
    if isinstance(value, dict):
        for key in ("url", "image", "image_url", "imageUrl"):
            url = value.get(key)
            if isinstance(url, str) and _https_image_url(url):
                return url.strip()
        for nested in value.values():
            found = first_url(
                nested,
                _depth=_depth + 1,
                max_depth=max_depth,
            )
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = first_url(
                item,
                _depth=_depth + 1,
                max_depth=max_depth,
            )
            if found:
                return found
    elif isinstance(value, str) and _https_image_url(value):
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
    if not _https_image_url(image_url):
        return error_response(
            error=f"{provider} returned an unsafe image URL.",
            error_type="invalid_response",
            provider=provider,
            model=model,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    try:
        saver = save_image or save_url_image
        image_ref = str(
            saver(
                image_url,
                prefix=cache_prefix,
                network_scope="public_direct",
                url_validator=_https_image_url,
            )
        )
    except Exception:
        return error_response(
            error=f"{provider} image result download failed.",
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
