"""Sanitized Taiji security profile status and desktop profile switching."""

from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from pathlib import Path
from typing import Any


_TRUTHY = {"1", "true", "yes", "on", "y"}
_PROFILE_CHOICES = {"strict", "local_controlled"}
_EXTENSION_KEYS = {"unapproved_skill_scripts", "delegate_task"}
_WINDOWS_SETTINGS_NAME = "security-settings.json"
_WINDOWS_SETTINGS_SCHEMA = "taiji-security-settings/v1"
_WINDOWS_SETTINGS_MAX_BYTES = 4096
_WINDOWS_SETTINGS_LOCK = threading.RLock()
_WINDOWS_SETTINGS_INVALID = object()
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
    if any(local_capabilities):
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
    configured = _persisted_security_settings()
    persisted_profile = configured["profile"] if configured else None
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
    effective_extensions = {
        "unapproved_skill_scripts": scripts_allowed,
        "delegate_task": delegate_allowed,
    }
    configured = configured or {"profile": profile, "capabilities": effective_extensions}
    restart_required = configured != {"profile": profile, "capabilities": effective_extensions}
    return {
        "mode": mode,
        "profile": profile,
        "pending_profile": pending_profile,
        "restart_required": restart_required,
        "configured": configured,
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


def _windows_security_store_enabled() -> bool:
    return os.name == "nt" or os.environ.get("TAIJI_WINDOWS_CANDIDATE") == "1"


def _windows_settings_file() -> Path:
    return _runtime_home() / _WINDOWS_SETTINGS_NAME


def _read_windows_security_settings() -> dict[str, Any] | None | object:
    path = _windows_settings_file()
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > _WINDOWS_SETTINGS_MAX_BYTES
        ):
            return _WINDOWS_SETTINGS_INVALID
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return _WINDOWS_SETTINGS_INVALID
    if len(payload) > _WINDOWS_SETTINGS_MAX_BYTES:
        return _WINDOWS_SETTINGS_INVALID
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return _WINDOWS_SETTINGS_INVALID
    if not isinstance(parsed, dict) or set(parsed) != {"schema", "profile", "capabilities"}:
        return _WINDOWS_SETTINGS_INVALID
    capabilities = parsed.get("capabilities")
    if (
        parsed.get("schema") != _WINDOWS_SETTINGS_SCHEMA
        or parsed.get("profile") not in _PROFILE_CHOICES
        or not isinstance(capabilities, dict)
        or set(capabilities) != _EXTENSION_KEYS
        or any(type(capabilities[key]) is not bool for key in _EXTENSION_KEYS)
    ):
        return _WINDOWS_SETTINGS_INVALID
    return {
        "profile": parsed["profile"],
        "capabilities": {key: capabilities[key] for key in sorted(_EXTENSION_KEYS)},
    }


def _write_windows_security_settings(values: dict[str, str]) -> Path:
    path = _windows_settings_file()
    with _WINDOWS_SETTINGS_LOCK:
        existing = _read_windows_security_settings()
        if isinstance(existing, dict):
            capabilities = dict(existing["capabilities"])
        elif existing is _WINDOWS_SETTINGS_INVALID:
            capabilities = {key: False for key in _EXTENSION_KEYS}
        else:
            capabilities = {
                key: _env_flag(_CONTROLLED_ALLOW_VARS[key])
                for key in _EXTENSION_KEYS
            }
        for key in _EXTENSION_KEYS:
            allow_var = _CONTROLLED_ALLOW_VARS[key]
            if allow_var in values:
                capabilities[key] = values[allow_var] == "1"
        payload = (
            json.dumps(
                {
                    "schema": _WINDOWS_SETTINGS_SCHEMA,
                    "profile": values["TAIJI_SECURITY_PROFILE"],
                    "capabilities": {
                        key: capabilities[key]
                        for key in sorted(_EXTENSION_KEYS)
                    },
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("Windows security settings target is unsafe")
        stage = path.parent / f".{_WINDOWS_SETTINGS_NAME}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        file_descriptor = os.open(stage, flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(file_descriptor, view)
                if written <= 0:  # pragma: no cover - OS write contract.
                    raise OSError("Windows security settings write made no progress")
                view = view[written:]
            os.fsync(file_descriptor)
        except BaseException:
            os.close(file_descriptor)
            stage.unlink(missing_ok=True)
            raise
        else:
            os.close(file_descriptor)
        try:
            os.replace(stage, path)
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
    return path


def _persisted_security_settings() -> dict[str, Any] | None:
    """Return a canonical restart-pending profile without exposing env contents."""
    if os.environ.get("TAIJI_DESKTOP_ONLY") != "1":
        return None
    if _windows_security_store_enabled():
        settings = _read_windows_security_settings()
        if isinstance(settings, dict):
            return settings
        if settings is _WINDOWS_SETTINGS_INVALID:
            return {
                "profile": "strict",
                "capabilities": {
                    key: False for key in sorted(_EXTENSION_KEYS)
                },
            }
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
    return {
        "profile": selected,
        "capabilities": {
            key: str(values.get(_CONTROLLED_ALLOW_VARS[key], "")).strip().lower() in _TRUTHY
            for key in _EXTENSION_KEYS
        },
    }


def _write_env(values: dict[str, str]) -> Path:
    if _windows_security_store_enabled():
        return _write_windows_security_settings(values)
    from agent.provider_credentials import mutate_env_unique

    runtime_home = _runtime_home()
    env_path = _env_file()
    mutate_env_unique(
        values,
        config_path=runtime_home / "config.yaml",
        project_process_env=False,
    )
    return env_path


def set_security_profile(profile: str, *, capabilities: dict[str, bool] | None = None) -> dict[str, Any]:
    if os.environ.get("TAIJI_DESKTOP_ONLY") != "1" or _security_mode() == "full":
        raise PermissionError("security profile switching is only available in the desktop runtime")
    selected = str(profile or "").strip()
    if selected not in _PROFILE_CHOICES:
        raise ValueError("profile must be strict or local_controlled")
    if capabilities is not None and (
        not isinstance(capabilities, dict)
        or any(key not in _EXTENSION_KEYS or type(value) is not bool for key, value in capabilities.items())
    ):
        raise ValueError("capabilities must contain only boolean unapproved_skill_scripts or delegate_task")
    local_controlled = selected == "local_controlled"
    values = {
        "TAIJI_SECURITY_PROFILE": selected,
        "TAIJI_SECURITY_MODE": "restricted",
        "TAIJI_ALLOW_TERMINAL": "1" if local_controlled else "0",
        "TAIJI_ALLOW_EXECUTE_CODE": "1" if local_controlled else "0",
    }
    # Omitted keys are left untouched by the locked canonical writer. Reading
    # the old process env here would overwrite another saved, pending choice.
    values.update({_CONTROLLED_ALLOW_VARS[key]: "1" if enabled else "0"
                   for key, enabled in (capabilities or {}).items()})
    _write_env(values)
    status = build_security_status_payload()
    pending_profile = selected if selected != status["profile"] else None
    return {
        "ok": True,
        "profile": selected,
        "pending_profile": pending_profile,
        "restart_required": status["restart_required"] or pending_profile is not None,
        # This is the effective state of the currently running Agent process.
        # Persisted changes become effective only after the desktop app restarts.
        "status": status,
    }
