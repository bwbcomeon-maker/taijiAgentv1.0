"""Validated Alibaba Model Studio endpoint construction."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse

DEFAULT_REGION = "cn-beijing"
PUBLIC_ROOTS = {
    "cn-beijing": "https://dashscope.aliyuncs.com",
    "ap-southeast-1": "https://dashscope-intl.aliyuncs.com",
}
VISION_PATH = "/compatible-mode/v1"
IMAGE_GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"

_WORKSPACE_PREFIX_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_region(region: str | None) -> str:
    """Return a supported Alibaba region identifier."""
    normalized = str(region or DEFAULT_REGION).strip().lower()
    if normalized not in PUBLIC_ROOTS:
        supported = ", ".join(PUBLIC_ROOTS)
        raise ValueError(f"Unsupported Alibaba region {normalized!r}; expected one of: {supported}")
    return normalized


def validate_https_url(value: str | None) -> str:
    """Validate and normalize a query-free HTTPS endpoint URL."""
    candidate = str(value or "").strip()
    try:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Custom Alibaba endpoint contains an invalid host or port") from exc
    if parsed.scheme.lower() != "https" or not hostname:
        raise ValueError("Custom Alibaba endpoint must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Custom Alibaba endpoint must not contain userinfo")
    if parsed.query or parsed.fragment or parsed.params:
        raise ValueError("Custom Alibaba endpoint must not contain params, query, or fragment")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Custom Alibaba endpoint must not use a local host")
    if hostname.isdigit() and int(hostname, 10) > 0xFFFFFFFF:
        raise ValueError("Custom Alibaba endpoint contains an invalid numeric IP address")
    if hostname.startswith("0x"):
        try:
            if int(hostname, 16) > 0xFFFFFFFF:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Custom Alibaba endpoint contains an invalid numeric IP address") from exc
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(hostname))
        except OSError:
            address = None
    if address is None and (re.fullmatch(r"[0-9.]+", hostname) or hostname.startswith("0x")):
        raise ValueError("Custom Alibaba endpoint contains an invalid numeric IP address")
    if address and not address.is_global:
        raise ValueError("Custom Alibaba endpoint must not use a non-public IP address")
    normalized_path = parsed.path.rstrip("/")
    return urlunparse(("https", parsed.netloc, normalized_path, "", "", ""))


def validate_workspace_prefix(value: str | None) -> str:
    """Validate the DNS label used as an Alibaba workspace host prefix."""
    prefix = str(value or "").strip().lower()
    if not _WORKSPACE_PREFIX_RE.fullmatch(prefix):
        raise ValueError("Alibaba workspace prefix must be a non-empty DNS label")
    return prefix


def _normalize_mode(endpoint_mode: str | None) -> str:
    mode = str(endpoint_mode or "public").strip().lower()
    if mode not in {"public", "workspace", "custom"}:
        raise ValueError("Alibaba endpoint mode must be public, workspace, or custom")
    return mode


def build_vision_base_url(
    *,
    endpoint_mode: str = "public",
    region: str = DEFAULT_REGION,
    workspace_prefix: str = "",
    custom_url: str = "",
) -> str:
    """Build an OpenAI-compatible Alibaba vision base URL."""
    mode = _normalize_mode(endpoint_mode)
    if mode == "custom":
        return validate_https_url(custom_url)
    normalized_region = normalize_region(region)
    if mode == "public":
        return PUBLIC_ROOTS[normalized_region] + VISION_PATH
    prefix = validate_workspace_prefix(workspace_prefix)
    return f"https://{prefix}.{normalized_region}.maas.aliyuncs.com{VISION_PATH}"


def build_image_root_url(
    *,
    endpoint_mode: str,
    region: str = DEFAULT_REGION,
    workspace_prefix: str = "",
    custom_url: str = "",
) -> str:
    """Build a Qwen-Image root URL for supported endpoint modes."""
    mode = _normalize_mode(endpoint_mode)
    if mode == "public":
        raise ValueError("Qwen-Image public endpoint is not supported without an official contract")
    if mode == "custom":
        url = validate_https_url(custom_url)
        parsed = urlparse(url)
        if parsed.path not in {"", IMAGE_GENERATION_PATH}:
            raise ValueError("Custom Qwen-Image URL must be a root URL or the full generation URL")
        if parsed.path == IMAGE_GENERATION_PATH:
            return url[: -len(IMAGE_GENERATION_PATH)]
        return url
    normalized_region = normalize_region(region)
    prefix = validate_workspace_prefix(workspace_prefix)
    return f"https://{prefix}.{normalized_region}.maas.aliyuncs.com"


def build_image_generation_url(**kwargs: str) -> str:
    """Build the full Qwen-Image generation endpoint without duplicating its path."""
    return build_image_root_url(**kwargs) + IMAGE_GENERATION_PATH
