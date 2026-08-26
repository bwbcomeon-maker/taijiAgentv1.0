"""Layered, secret-free verification state for the configured main model."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import socket
import ssl
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


SCHEMA = "taiji.model.verification.v1"
SUCCESS_TTL_SECONDS = 300
TRANSIENT_TTL_SECONDS = 60
MAX_RESPONSE_BYTES = 256 * 1024
logger = logging.getLogger(__name__)
_STATE_WRITE_LOCK = threading.RLock()


def _incident_id() -> str:
    return f"inc-{secrets.token_hex(6)}"


def configuration_fingerprint(material: dict[str, Any]) -> str:
    source = "\0".join(
        str(material.get(key) or "").strip()
        for key in ("profile", "provider", "model", "base_url", "api_key")
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def configured_unverified() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "state": "configured_unverified",
        "level": "configured",
        "code": None,
        "checked_at": None,
        "expires_at": None,
        "incident_id": None,
        "retryable": False,
    }


def unconfigured() -> dict[str, Any]:
    result = configured_unverified()
    result.update({"state": "unconfigured", "level": "none", "code": "model_configuration_required"})
    return result


def _configuration_missing(material: dict[str, Any]) -> bool:
    provider = str(material.get("provider") or "").strip().lower()
    model = str(material.get("model") or "").strip()
    auth_type = str(material.get("auth_type") or "api_key").strip().lower()
    if not provider or not model:
        return True
    return provider not in {"ollama", "lmstudio"} and auth_type == "api_key" and not material.get("api_key")


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    with _STATE_WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def verification_for_material(material: dict[str, Any], state_path: Path, *, now: float | None = None) -> dict[str, Any]:
    if _configuration_missing(material):
        return unconfigured()
    fingerprint = configuration_fingerprint(material)
    stored = _read_state(state_path)
    if stored.get("fingerprint") != fingerprint:
        return configured_unverified()
    result = {key: stored.get(key) for key in configured_unverified()}
    result["schema"] = SCHEMA
    expires_at = result.get("expires_at")
    now_value = time.time() if now is None else now
    if isinstance(expires_at, (int, float)) and expires_at <= now_value:
        return configured_unverified()
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _probe_target(material: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    provider = str(material.get("provider") or "").strip().lower()
    base_url = str(material.get("base_url") or "").strip().rstrip("/")
    api_key = str(material.get("api_key") or "")
    if provider in {"ollama", "lmstudio"}:
        if provider == "ollama":
            return (f"{base_url}/api/tags", {})
        return (f"{base_url}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {})
    if provider in {"anthropic", "minimax", "minimax-cn"}:
        versioned_base = base_url if base_url.endswith("/v1") else f"{base_url}/v1"
        return (
            f"{versioned_base}/models",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    if str(material.get("auth_type") or "api_key") != "api_key":
        return None
    return (f"{base_url}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {})


def _classify_probe_exception(exc: BaseException) -> tuple[str, bool]:
    status = getattr(exc, "code", None)
    if status in {401, 403}:
        return "provider_authorization_failed", False
    if status == 402:
        return "provider_account_unavailable", False
    if status == 404:
        return "provider_model_unavailable", False
    if status == 429:
        return "provider_rate_limited", True
    if isinstance(status, int) and status >= 500:
        return "provider_service_unavailable", True
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "provider_timeout", True
    if isinstance(exc, (ssl.SSLError, ConnectionError, OSError, urllib.error.URLError)):
        return "provider_network_unavailable", True
    return "unknown_error", True


def check_connection(
    material: dict[str, Any],
    state_path: Path,
    *,
    opener: Callable[..., Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if _configuration_missing(material):
        return unconfigured()
    target = _probe_target(material)
    checked_at = time.time() if now is None else now
    fingerprint = configuration_fingerprint(material)
    if target is None:
        result = configured_unverified()
        result.update({"state": "unsupported", "code": "connection_check_unsupported", "checked_at": checked_at})
    else:
        url, headers = target
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            result = configured_unverified()
            result.update({"state": "failed", "code": "provider_network_unavailable", "checked_at": checked_at, "expires_at": checked_at + TRANSIENT_TTL_SECONDS, "incident_id": _incident_id(), "retryable": True})
        else:
            request = urllib.request.Request(url, headers={**headers, "Accept": "application/json"}, method="GET")
            try:
                open_call = opener or urllib.request.build_opener(_NoRedirect()).open
                with open_call(request, timeout=5) as response:
                    data = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise OSError("provider response exceeded safe limit")
                result = configured_unverified()
                result.update({"state": "connection_verified", "level": "connection", "checked_at": checked_at, "expires_at": checked_at + SUCCESS_TTL_SECONDS})
            except Exception as exc:
                code, retryable = _classify_probe_exception(exc)
                result = configured_unverified()
                result.update({"state": "failed", "code": code, "checked_at": checked_at, "incident_id": _incident_id(), "retryable": retryable})
                if retryable:
                    result["expires_at"] = checked_at + TRANSIENT_TTL_SECONDS
    stored = {**result, "fingerprint": fingerprint}
    if result.get("state") == "failed":
        logger.warning(
            "main_model_verification_failed code=%s incident_id=%s retryable=%s",
            result.get("code"),
            result.get("incident_id"),
            result.get("retryable"),
        )
    _write_state(state_path, stored)
    return result


def record_chat_result(
    material: dict[str, Any],
    state_path: Path,
    *,
    success: bool,
    product_code: str | None = None,
    incident_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    checked_at = time.time() if now is None else now
    if success:
        result = configured_unverified()
        result.update({"state": "chat_verified", "level": "chat", "checked_at": checked_at, "expires_at": checked_at + SUCCESS_TTL_SECONDS})
    else:
        retryable = product_code in {
            "provider_rate_limited", "provider_network_unavailable", "provider_timeout", "provider_service_unavailable"
        }
        result = configured_unverified()
        result.update({"state": "failed", "code": product_code or "unknown_error", "checked_at": checked_at, "incident_id": incident_id, "retryable": retryable})
        if retryable:
            result["expires_at"] = checked_at + TRANSIENT_TTL_SECONDS
    _write_state(state_path, {**result, "fingerprint": configuration_fingerprint(material)})
    return result
