"""Centralized security-mode gates for Taiji product deployments."""

from __future__ import annotations

import os


_TRUTHY = {"1", "true", "yes", "on", "y"}


def env_flag_enabled(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


def security_mode() -> str:
    mode = str(os.environ.get("TAIJI_SECURITY_MODE", "full")).strip().lower()
    if mode not in {"restricted", "full"}:
        return "full"
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


def blocked_message(capability: str, allow_var: str) -> str:
    return (
        f"{capability} is disabled because TAIJI_SECURITY_MODE=restricted. "
        f"Switch to TAIJI_SECURITY_MODE=full only after customer IT approval, "
        f"or explicitly set {allow_var}=1 for this controlled deployment."
    )
