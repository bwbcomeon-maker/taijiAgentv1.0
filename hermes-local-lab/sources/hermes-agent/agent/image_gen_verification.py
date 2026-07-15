"""Shared image-generation verification contract for Agent and WebUI runtimes."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERIFYING_TTL_SECONDS = 15 * 60
VALID_PERSISTED_STATUSES = {"verifying", "verified", "failed"}
IMAGE_GEN_KEY_ENV = {
    "doubao": "ARK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "qianfan": "QIANFAN_API_KEY",
    "zhipu-image": "GLM_API_KEY",
    "minimax-image": "MINIMAX_API_KEY",
    "fal": "FAL_KEY",
    "openai": "OPENAI_API_KEY",
    "xai": "XAI_API_KEY",
    "krea": "KREA_API_KEY",
}


def verification_state_root() -> Path:
    override = os.getenv("TAIJI_WEBUI_STATE_DIR") or os.getenv("HERMES_WEBUI_STATE_DIR")
    if override:
        return Path(override).expanduser() / "image-gen-verification"
    runtime_home = str(os.getenv("TAIJI_RUNTIME_HOME") or "").strip()
    if runtime_home:
        return Path(runtime_home).expanduser() / "web" / "image-gen-verification"
    if os.name == "nt" and str(os.getenv("LOCALAPPDATA") or "").strip():
        base = Path(str(os.getenv("LOCALAPPDATA"))) / "hermes"
    else:
        base = Path.home() / ".hermes"
    return Path(base) / "webui" / "image-gen-verification"


def active_profile_name() -> str:
    if str(os.getenv("TAIJI_RUNTIME_HOME") or "").strip():
        return "default"
    explicit = str(os.getenv("HERMES_PROFILE_NAME") or "").strip()
    if explicit:
        return explicit
    home = Path(os.getenv("HERMES_HOME") or "~/.hermes").expanduser()
    if home.parent.name == "profiles" and home.name:
        return home.name
    try:
        sticky = home / "active_profile"
        value = sticky.read_text(encoding="utf-8").strip()
        return value or "default"
    except OSError:
        return str(os.getenv("HERMES_PROFILE") or "default").strip() or "default"


def verification_state_path(state_root: Path | None, profile: str) -> Path:
    root = Path(state_root) if state_root is not None else verification_state_root()
    profile_id = hashlib.sha256(str(profile or "default").encode("utf-8")).hexdigest()[:24]
    return root / f"{profile_id}.json"


def active_custom_provider_identity(
    provider: str, config_data: dict[str, Any]
) -> dict[str, Any]:
    if not provider.startswith("custom:"):
        return {}
    requested_id = provider.split(":", 1)[1]
    entries = config_data.get("custom_image_providers")
    if not isinstance(entries, list):
        return {}
    try:
        from agent.custom_image_providers import (
            normalize_custom_image_provider_entry,
            normalize_custom_image_provider_id,
        )
    except Exception:
        return {}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        try:
            if normalize_custom_image_provider_id(raw_entry.get("id")) != requested_id:
                continue
            normalized = normalize_custom_image_provider_entry(raw_entry)
        except ValueError:
            continue
        return {
            "id": normalized["id"],
            "name": normalized["name"],
            "base_url": normalized["base_url"],
            "api_key_env": normalized["api_key_env"],
            "models": list(normalized["models"]),
            "default_model": normalized["default_model"],
            "size_map": dict(normalized["size_map"]),
            "response_format": normalized["response_format"],
            "timeout_seconds": normalized["timeout_seconds"],
            "transport": str(raw_entry.get("transport") or "openai_images").strip(),
        }
    return {}


def image_gen_secret_env(
    provider: str, credential_ref: str, config_data: dict[str, Any]
) -> str:
    if credential_ref:
        try:
            from agent.provider_credentials import (
                credential_secret_env,
                load_credential,
                provider_family,
            )

            row = load_credential(credential_ref, config_data=config_data)
            if provider_family(row.get("provider_family")) == provider_family(provider):
                expected = credential_secret_env(row.get("id"))
                if str(row.get("secret_env") or "").strip() == expected:
                    return expected
        except ValueError:
            return ""
        return ""
    if provider.startswith("custom:"):
        return str(active_custom_provider_identity(provider, config_data).get("api_key_env") or "")
    return IMAGE_GEN_KEY_ENV.get(provider, "")


def image_gen_fingerprint(
    image_cfg: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any],
    secret_value: str,
) -> str:
    options = image_cfg.get("options")
    if not isinstance(options, dict):
        options = {}
    provider = str(image_cfg.get("provider") or "").strip().lower()
    material = {
        "profile": profile,
        "provider": provider,
        "model": str(image_cfg.get("model") or "").strip(),
        "credential_ref": str(image_cfg.get("credential_ref") or "").strip(),
        "endpoint_mode": str(options.get("endpoint_mode") or "").strip(),
        "region": str(options.get("region") or "").strip(),
        "workspace_id": str(options.get("workspace_id") or "").strip(),
        "base_url": str(options.get("base_url") or "").strip().rstrip("/"),
        "custom_provider": active_custom_provider_identity(provider, config_data),
        "key_digest": hashlib.sha256(secret_value.encode("utf-8")).hexdigest()
        if secret_value
        else "",
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _is_stale_verifying(checked_at: Any, *, now: datetime | None = None) -> bool:
    try:
        parsed = datetime.fromisoformat(str(checked_at or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    current = now or datetime.now(timezone.utc)
    return (current - parsed).total_seconds() > VERIFYING_TTL_SECONDS


def verification_status_from_state(
    state: Any,
    *,
    expected_fingerprint: str,
    now: datetime | None = None,
) -> str:
    if not isinstance(state, dict) or str(state.get("fingerprint") or "") != expected_fingerprint:
        return "configured_unverified"
    status = str(state.get("status") or "")
    if status not in VALID_PERSISTED_STATUSES:
        return "configured_unverified"
    if status == "verifying" and _is_stale_verifying(state.get("checked_at"), now=now):
        return "configured_unverified"
    return status


def read_image_gen_verification_status(
    image_cfg: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any],
    secret_value: str,
    state_root: Path | None = None,
) -> str:
    expected = image_gen_fingerprint(
        image_cfg,
        profile=profile,
        config_data=config_data,
        secret_value=secret_value,
    )
    try:
        state = json.loads(
            verification_state_path(state_root, profile).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return "configured_unverified"
    return verification_status_from_state(state, expected_fingerprint=expected)
