"""Centralized security-mode gates for Taiji product deployments."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


_TRUTHY = {"1", "true", "yes", "on", "y"}
_CONTROLLED_ALLOW_VARS = {
    "terminal": "TAIJI_ALLOW_TERMINAL",
    "execute_code": "TAIJI_ALLOW_EXECUTE_CODE",
    "unapproved_skill_scripts": "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
    "delegate_task": "TAIJI_ALLOW_DELEGATE_TASK",
}


def env_flag_enabled(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


def security_mode() -> str:
    mode = str(os.environ.get("TAIJI_SECURITY_MODE", "restricted")).strip().lower()
    if mode not in {"restricted", "full"}:
        return "restricted"
    return mode


def is_restricted_mode() -> bool:
    return security_mode() == "restricted"


def _allowed_in_mode(allow_var: str) -> bool:
    if not is_restricted_mode():
        return True
    return env_flag_enabled(allow_var)


def is_terminal_allowed() -> bool:
    return _allowed_in_mode("TAIJI_ALLOW_TERMINAL")


def is_execute_code_allowed() -> bool:
    return _allowed_in_mode("TAIJI_ALLOW_EXECUTE_CODE")


def is_cron_script_allowed() -> bool:
    return _allowed_in_mode("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS")


def is_delegate_task_allowed() -> bool:
    return _allowed_in_mode("TAIJI_ALLOW_DELEGATE_TASK")


def security_profile() -> str:
    mode = security_mode()
    if mode == "full":
        return "full"
    if all(env_flag_enabled(var) for var in _CONTROLLED_ALLOW_VARS.values()):
        return "local_controlled"
    if any(env_flag_enabled(var) for var in _CONTROLLED_ALLOW_VARS.values()):
        return "custom_restricted"
    return "strict"


def _runtime_home() -> Path:
    configured = os.environ.get("TAIJI_RUNTIME_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "taiji-agent" / "runtime-home"


def _env_file() -> Path:
    return _runtime_home() / ".env"


def _set_env_lines(existing: str, values: dict[str, str]) -> str:
    lines = existing.splitlines()
    remaining = dict(values)
    updated: list[str] = []
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")
    for line in lines:
        match = pattern.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    if updated and updated[-1].strip():
        updated.append("")
    for key, value in remaining.items():
        updated.append(f"{key}={value}")
    return "\n".join(updated).rstrip() + "\n"


def enable_capability_env(allow_var: str) -> dict[str, Any]:
    """Persist one controlled capability for desktop runtimes only."""
    if allow_var not in _CONTROLLED_ALLOW_VARS.values():
        raise ValueError(f"unsupported Taiji capability variable: {allow_var}")
    if os.environ.get("TAIJI_DESKTOP_ONLY") != "1":
        return {
            "persisted": False,
            "reason": "persistent capability approval is only available in the desktop runtime",
        }
    values = {
        "TAIJI_SECURITY_MODE": "restricted",
        allow_var: "1",
    }
    env_path = _env_file()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text(_set_env_lines(current, values), encoding="utf-8")
    tmp.replace(env_path)
    os.environ.update(values)
    return {"persisted": True, "env_file": str(env_path)}


def _capability_reason(name: str, allow_var: str | None, allowed: bool, approval_applicable: bool) -> str:
    if allowed:
        return "capability enabled"
    if allow_var:
        if approval_applicable:
            return f"{name} requires approval or {allow_var}=1 while TAIJI_SECURITY_MODE=restricted"
        return f"{name} is disabled while TAIJI_SECURITY_MODE=restricted; set {allow_var}=1 to enable it"
    return "capability unavailable"


def _capability_entry(name: str, allow_var: str | None, allowed: bool, approval_applicable: bool) -> dict[str, Any]:
    approval_required = bool(approval_applicable and not allowed)
    return {
        "name": name,
        "allowed": bool(allowed),
        "enabled": bool(allowed),
        "allow_var": allow_var,
        "approval_applicable": bool(approval_applicable),
        "approval_required": approval_required,
        "reason": _capability_reason(name, allow_var, bool(allowed), bool(approval_applicable)),
        "restart_required": False,
    }


def build_security_status() -> dict[str, Any]:
    terminal_allowed = is_terminal_allowed()
    execute_code_allowed = is_execute_code_allowed()
    cron_allowed = is_cron_script_allowed()
    delegate_allowed = is_delegate_task_allowed()
    return {
        "mode": security_mode(),
        "profile": security_profile(),
        "approval_available": True,
        "approval_applies_when": "capability_request_or_command_requires_confirmation",
        "capabilities": {
            "terminal": _capability_entry(
                "terminal",
                "TAIJI_ALLOW_TERMINAL",
                terminal_allowed,
                True,
            ),
            "execute_code": _capability_entry(
                "execute_code",
                "TAIJI_ALLOW_EXECUTE_CODE",
                execute_code_allowed,
                True,
            ),
            "unapproved_skill_scripts": _capability_entry(
                "unapproved_skill_scripts",
                "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS",
                cron_allowed,
                False,
            ),
            "delegate_task": _capability_entry(
                "delegate_task",
                "TAIJI_ALLOW_DELEGATE_TASK",
                delegate_allowed,
                False,
            ),
            "document_read": _capability_entry(
                "document_read",
                None,
                True,
                False,
            ),
        },
    }


def blocked_message(capability: str, allow_var: str) -> str:
    return (
        f"{capability} is disabled because TAIJI_SECURITY_MODE=restricted. "
        f"Approve this capability in the desktop prompt, or explicitly set "
        f"{allow_var}=1 for this controlled deployment."
    )


def capability_blocked_payload(
    capability: str,
    allow_var: str,
    *,
    output: str = "",
    exit_code: int | None = None,
    approval_applicable: bool = False,
    approval_outcome: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "capability_blocked",
        "capability": capability,
        "approval_applicable": bool(approval_applicable),
        "mode": security_mode(),
        "profile": security_profile(),
        "allow_var": allow_var,
        "error": error or blocked_message(capability, allow_var),
        "output": output,
    }
    if approval_outcome:
        payload["approval_outcome"] = approval_outcome
    if exit_code is not None:
        payload["exit_code"] = exit_code
    return payload
