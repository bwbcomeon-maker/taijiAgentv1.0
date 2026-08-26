"""Safe terminal provider-error contract shared by the agent and Gateway."""

from __future__ import annotations

import re
import secrets
import socket
import ssl
from typing import Any, Iterable


_TRANSPORT_KINDS = {"timeout", "dns", "connect", "tls", "disconnect"}


def transport_kind_for_error(error: BaseException) -> str | None:
    """Classify transport failures without changing retry/failover semantics."""

    text = str(error or "").casefold()
    if isinstance(error, (TimeoutError, socket.timeout)) or "timed out" in text or "timeout" in text:
        return "timeout"
    if isinstance(error, socket.gaierror) or any(
        marker in text for marker in ("name or service not known", "nodename nor servname", "dns")
    ):
        return "dns"
    if isinstance(error, ssl.SSLError) or any(
        marker in text for marker in ("ssl", "tls", "certificate verify")
    ):
        return "tls"
    if isinstance(error, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)) or any(
        marker in text
        for marker in ("connection reset", "connection closed", "connection lost", "remote protocol")
    ):
        return "disconnect"
    if isinstance(error, (ConnectionRefusedError, ConnectionError)) or any(
        marker in text for marker in ("connection refused", "failed to connect", "cannot connect")
    ):
        return "connect"
    return None


def redact_provider_error(value: object, *, sensitive_values: Iterable[object] = ()) -> str:
    """Return a bounded diagnostic summary with credentials and local details removed."""

    text = str(value or "")
    for secret in sensitive_values:
        candidate = str(secret or "")
        if candidate:
            text = text.replace(candidate, "[REDACTED]")
    substitutions = (
        (r"(?i)authorization\s*:\s*(?:bearer|basic)\s+[^\s,;]+", "[REDACTED AUTH]"),
        (r"(?i)\bbearer\s+[^\s,;]+", "[REDACTED AUTH]"),
        (r"(?i)([?&](?:api[_-]?key|token|access[_-]?token|secret)=)[^&#\s]+", r"\1[REDACTED]"),
        (r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+", "[REDACTED SECRET]"),
        (r"\b[A-Z][A-Z0-9_]{2,}\s*=\s*[^\s,;]+", "[REDACTED ENV]"),
        (r"(?i)\bsk-[A-Za-z0-9_-]+", "[REDACTED KEY]"),
        (r"(?i)(?<![A-Za-z0-9_])[A-Z]:\\[^\n,;]+", "[REDACTED PATH]"),
        (r"(?<![A-Za-z0-9_:])/(?!/)[^\n,;]+", "[REDACTED PATH]"),
        (r"(?is)\btraceback\b.*", "[REDACTED TRACE]"),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)
    return " ".join(text.split())[:500]


def new_incident_id() -> str:
    return f"inc-{secrets.token_hex(6)}"


def build_terminal_failure(
    classified: Any,
    error: BaseException,
    *,
    incident_id: str | None = None,
    sensitive_values: Iterable[object] = (),
) -> dict[str, Any]:
    """Build the public-safe terminal result; raw provider bodies never cross it."""

    reason = getattr(getattr(classified, "reason", None), "value", None) or "unknown"
    kind = getattr(classified, "transport_kind", None) or transport_kind_for_error(error)
    if kind not in _TRANSPORT_KINDS:
        kind = None
    status = getattr(classified, "status_code", None)
    if not isinstance(status, int):
        status = None
    return {
        "failed": True,
        "error": "Provider request failed.",
        "error_code": str(reason),
        "error_status": status,
        "transport_kind": kind,
        "retryable": bool(getattr(classified, "retryable", False)),
        "incident_id": incident_id or new_incident_id(),
    }


def build_gateway_run_error(result: object) -> dict[str, Any]:
    """Project a terminal result onto the allowlisted Gateway v1 contract."""

    source = result if isinstance(result, dict) else {}
    status = source.get("error_status")
    if not isinstance(status, int):
        status = None
    kind = source.get("transport_kind")
    if kind not in _TRANSPORT_KINDS:
        kind = None
    incident_id = str(source.get("incident_id") or "")
    if not re.fullmatch(r"inc-[0-9a-f]{12,32}", incident_id):
        incident_id = new_incident_id()
    return {
        "schema": "taiji.gateway.run-error.v1",
        "source": "provider",
        "code": str(source.get("error_code") or source.get("code") or "unknown"),
        "message": "Provider request failed.",
        "status": status,
        "transport_kind": kind,
        "retryable": bool(source.get("retryable", False)),
        "incident_id": incident_id,
    }


def build_gateway_exception_error(*, incident_id: str | None = None) -> dict[str, Any]:
    """Build a safe Gateway-owned worker failure without exception text."""

    return {
        "schema": "taiji.gateway.run-error.v1",
        "source": "gateway",
        "code": "worker_exception",
        "message": "Gateway run failed.",
        "status": None,
        "transport_kind": None,
        "retryable": True,
        "incident_id": incident_id or new_incident_id(),
    }
