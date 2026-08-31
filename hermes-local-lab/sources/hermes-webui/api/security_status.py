"""Sanitized Taiji security profile status and desktop profile switching."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_TRUTHY = {"1", "true", "yes", "on", "y"}
_PROFILE_CHOICES = {"strict", "local_controlled"}
_CONTROLLED_ALLOW_VARS = {
    "terminal": "TAIJI_ALLOW_TERMINAL",
    "execute_code": "TAIJI_ALLOW_EXECUTE_CODE",
    "unapproved_skill_scripts": "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
    "delegate_task": "TAIJI_ALLOW_DELEGATE_TASK",
}


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


def _security_mode() -> str:
    mode = str(os.environ.get("TAIJI_SECURITY_MODE", "restricted")).strip().lower()
    return mode if mode in {"restricted", "full"} else "restricted"


def _effective_profile() -> str:
    mode = _security_mode()
    if mode == "full":
        return "full"
    local_capabilities = (
        _env_flag("TAIJI_ALLOW_TERMINAL"),
        _env_flag("TAIJI_ALLOW_EXECUTE_CODE"),
    )
    if all(local_capabilities):
        return "local_controlled"
    if any(_env_flag(var) for var in _CONTROLLED_ALLOW_VARS.values()):
        return "custom_restricted"
    return "strict"


def _capability_reason(name: str, allow_var: str | None, allowed: bool, approval_applicable: bool) -> str:
    if allowed:
        return "capability enabled"
    if allow_var:
        if approval_applicable:
            return f"{name} requires approval or {allow_var}=1 while TAIJI_SECURITY_MODE=restricted"
        return f"{name} is disabled while TAIJI_SECURITY_MODE=restricted; set {allow_var}=1 to enable it"
    return "capability unavailable"


def _capability(name: str, allow_var: str | None, allowed: bool, approval_applicable: bool) -> dict[str, Any]:
    approval_required = bool(approval_applicable and not allowed)
    return {
        "name": name,
        "allow_var": allow_var,
        "allowed": bool(allowed),
        "enabled": bool(allowed),
        "approval_applicable": bool(approval_applicable),
        "approval_required": approval_required,
        "reason": _capability_reason(name, allow_var, bool(allowed), bool(approval_applicable)),
        "restart_required": False,
    }


def build_security_status_payload() -> dict[str, Any]:
    mode = _security_mode()
    profile = _effective_profile()
    persisted_profile = _persisted_security_profile()
    pending_profile = (
        persisted_profile
        if profile in _PROFILE_CHOICES and persisted_profile != profile
        else None
    )
    restricted = mode == "restricted"
    terminal_allowed = (not restricted) or _env_flag("TAIJI_ALLOW_TERMINAL")
    execute_allowed = (not restricted) or _env_flag("TAIJI_ALLOW_EXECUTE_CODE")
    scripts_allowed = (not restricted) or _env_flag("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS")
    delegate_allowed = (not restricted) or _env_flag("TAIJI_ALLOW_DELEGATE_TASK")
    return {
        "mode": mode,
        "profile": profile,
        "pending_profile": pending_profile,
        "restart_required": pending_profile is not None,
        "profile_choices": sorted(_PROFILE_CHOICES),
        "desktop_profile_write_enabled": os.environ.get("TAIJI_DESKTOP_ONLY") == "1",
        "approval_available": True,
        "approval_applies_when": "capability_request_or_command_requires_confirmation",
        "capabilities": {
            "terminal": _capability("terminal", "TAIJI_ALLOW_TERMINAL", terminal_allowed, restricted),
            "execute_code": _capability("execute_code", "TAIJI_ALLOW_EXECUTE_CODE", execute_allowed, restricted),
            "unapproved_skill_scripts": _capability(
                "unapproved_skill_scripts",
                "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
                scripts_allowed,
                False,
            ),
            "delegate_task": _capability("delegate_task", "TAIJI_ALLOW_DELEGATE_TASK", delegate_allowed, False),
            "document_read": _capability("document_read", None, True, False),
        },
    }


def _runtime_home() -> Path:
    configured = os.environ.get("TAIJI_RUNTIME_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "taiji-agent" / "runtime-home"


def _env_file() -> Path:
    return _runtime_home() / ".env"


def _persisted_security_profile() -> str | None:
    """Return a canonical restart-pending profile without exposing env contents."""
    if os.environ.get("TAIJI_DESKTOP_ONLY") != "1":
        return None
    try:
        from agent.provider_credentials import load_credential_snapshot

        snapshot = load_credential_snapshot(_runtime_home() / "config.yaml")
        values = snapshot.env
    except Exception:
        # Pending metadata is optional; an unreadable or invalid credential
        # store must not hide the effective security status.
        return None
    selected = str(values.get("TAIJI_SECURITY_PROFILE") or "").strip()
    if selected not in _PROFILE_CHOICES:
        return None
    if str(values.get("TAIJI_SECURITY_MODE") or "").strip() != "restricted":
        return None
    terminal = str(values.get("TAIJI_ALLOW_TERMINAL") or "").strip()
    execute_code = str(values.get("TAIJI_ALLOW_EXECUTE_CODE") or "").strip()
    if selected == "strict" and (terminal, execute_code) != ("0", "0"):
        return None
    if selected == "local_controlled" and (terminal, execute_code) != ("1", "1"):
        return None
    return selected


def _write_env(values: dict[str, str]) -> Path:
    from agent.provider_credentials import mutate_env_unique

    runtime_home = _runtime_home()
    env_path = _env_file()
    mutate_env_unique(
        values,
        config_path=runtime_home / "config.yaml",
        project_process_env=False,
    )
    return env_path


def set_security_profile(profile: str) -> dict[str, Any]:
    if os.environ.get("TAIJI_DESKTOP_ONLY") != "1":
        raise PermissionError("security profile switching is only available in the desktop runtime")
    selected = str(profile or "").strip()
    if selected not in _PROFILE_CHOICES:
        raise ValueError("profile must be strict or local_controlled")
    local_controlled = selected == "local_controlled"
    values = {
        "TAIJI_SECURITY_PROFILE": selected,
        "TAIJI_SECURITY_MODE": "restricted",
        "TAIJI_ALLOW_TERMINAL": "1" if local_controlled else "0",
        "TAIJI_ALLOW_EXECUTE_CODE": "1" if local_controlled else "0",
        "TAIJI_ALLOW_DELEGATE_TASK": (
            "1" if local_controlled and _env_flag("TAIJI_ALLOW_DELEGATE_TASK") else "0"
        ),
        "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS": (
            "1"
            if local_controlled and _env_flag("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS")
            else "0"
        ),
    }
    _write_env(values)
    status = build_security_status_payload()
    pending_profile = selected if selected != status["profile"] else None
    return {
        "ok": True,
        "profile": selected,
        "pending_profile": pending_profile,
        "restart_required": pending_profile is not None,
        # This is the effective state of the currently running Agent process.
        # Persisted changes become effective only after the desktop app restarts.
        "status": status,
    }
