"""Pure, secret-free endpoint projections shared by WebUI API surfaces.

The Agent owns endpoint policy.  This module only adapts an already-loaded
candidate into a safe representation suitable for a browser response.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from hermes_cli.providers import resolve_provider_endpoint


def _normalize_url(value: object) -> str:
    return str(value or "").strip().rstrip("/")


def _safe_url(value: object) -> tuple[str, bool]:
    """Return ``(safe_url, had_unsafe_parts)`` without exposing credentials."""
    raw = _normalize_url(value)
    if not raw:
        return "", False
    had_control_chars = any(ch in raw for ch in ("\n", "\r"))
    if had_control_chars:
        # Keep only the first line.  Deleting the separator would concatenate
        # an attacker-controlled header or key into the visible path.
        raw = raw[: min(index for index, char in enumerate(raw) if char in "\n\r")]
    try:
        parsed = urlsplit(raw)
        if not parsed.scheme or not parsed.hostname:
            return "", True
        # Rebuild netloc from parsed host/port, intentionally dropping
        # username/password.  Accessing port can raise for malformed values.
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        safe = urlunsplit((parsed.scheme, netloc, parsed.path, "", "")).rstrip("/")
        unsafe = bool(
            had_control_chars
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        )
        return safe, unsafe
    except (TypeError, ValueError):
        return "", True


def public_endpoint(
    provider_id: object,
    *,
    configured_url: object = "",
    runtime_url: object = "",
    candidate_source: str = "managed",
    runtime_selector_unresolved: bool = False,
    stored_main_override_present: bool = False,
    candidate_override_present: bool = False,
) -> dict[str, Any]:
    """Return the safe public endpoint contract for one Provider.

    All inputs are supplied by the caller from an already-loaded local
    snapshot.  No configuration, environment, credential, network, or model
    catalog lookup occurs here.
    """
    raw_provider = str(provider_id or "").strip().lower()
    effective_candidate_source = (
        "custom" if raw_provider == "custom" and candidate_source == "managed"
        else candidate_source
    )
    resolution = resolve_provider_endpoint(
        str(provider_id or ""),
        configured_url=_normalize_url(configured_url),
        runtime_url=_normalize_url(runtime_url),
        candidate_source=effective_candidate_source,
        candidate_override_present=candidate_override_present,
    )
    # Runtime-managed providers own the address selection.  When no runtime
    # candidate is available, the resolver intentionally returns an empty
    # effective URL; never resurrect the caller's registry/config candidate.
    safe_url, unsafe_saved_value = _safe_url(resolution.effective_url)
    status = "resolved" if safe_url else "missing"
    if resolution.policy == "runtime_managed" and not safe_url:
        status = "runtime_managed"
    elif runtime_selector_unresolved and resolution.policy == "configurable":
        safe_url = ""
        status = "runtime_unresolved"
    elif unsafe_saved_value and (
        raw_provider == "custom" or raw_provider.startswith("custom:")
    ):
        status = "invalid_saved_value"

    editable = bool(resolution.editable and not unsafe_saved_value and status == "resolved")
    source = (
        "runtime"
        if runtime_selector_unresolved and resolution.policy == "configurable"
        else resolution.source
    )
    # Residue is deliberately caller-owned and only represents the active
    # fixed model's raw field, never a transient candidate rejection.
    override_ignored = bool(
        stored_main_override_present and resolution.policy == "fixed"
    )
    return {
        "display_url": safe_url or None,
        "policy": resolution.policy,
        "source": source,
        "editable": editable,
        "status": status,
        "override_ignored": override_ignored,
    }


__all__ = ["public_endpoint"]
