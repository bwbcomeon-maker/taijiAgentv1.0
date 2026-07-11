"""Build-controlled runtime profile for source and installed Taiji runtimes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROFILE_SCHEMA_VERSION = "taiji-runtime-profile/v1"
INSTALLED_PRODUCTION_PROFILE = "installed-production"
_PROFILE_PATH = Path(__file__).with_name("taiji-runtime-profile.json")


def _read_profile() -> dict[str, Any] | None:
    try:
        payload = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
        return None
    profile = payload.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        return None
    return payload


def _is_trusted_source_checkout() -> bool:
    module_path = Path(__file__).resolve()
    relative = Path("hermes-local-lab/sources/hermes-agent/taiji_runtime_profile.py")
    for candidate in module_path.parents:
        if not (candidate / ".git").exists():
            continue
        try:
            if (candidate / relative).resolve(strict=True) == module_path:
                return True
        except OSError:
            return False
    return False


def installation_profile() -> str:
    """Return a build-owned profile; malformed non-checkout installs fail closed."""
    payload = _read_profile()
    if payload is not None and payload["profile"] == INSTALLED_PRODUCTION_PROFILE:
        return INSTALLED_PRODUCTION_PROFILE
    if payload is not None and _is_trusted_source_checkout():
        return str(payload["profile"])
    return INSTALLED_PRODUCTION_PROFILE


def is_installed_production() -> bool:
    return installation_profile() == INSTALLED_PRODUCTION_PROFILE
