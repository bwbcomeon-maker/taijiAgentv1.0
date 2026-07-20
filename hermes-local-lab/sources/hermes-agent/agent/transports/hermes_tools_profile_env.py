"""Fail-closed profile environment boundary for the Codex MCP callback.

Codex itself is allowed to run shell commands, so profile credentials must not
be inherited by the app-server process.  The trusted ``hermes-tools`` MCP
child receives only non-secret profile path selectors, reloads that profile's
``.env`` itself, and clears every known ambient Hermes credential before doing
so.  A profile with a missing key therefore cannot fall back to the WebUI
process's default account or to another concurrent profile.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERMES_TOOLS_PROFILE_ENV_REQUIRED = "HERMES_TOOLS_PROFILE_ENV_REQUIRED"

_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_ENV_NAME_PATTERN = re.compile(
    r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)",
    re.IGNORECASE,
)
_SECRET_ENV_NAME_SUFFIXES = (
    "_KEY",
    "_PRIVATE_KEY",
    "_ACCESS_KEY",
)
_SCALAR_ENV_REFERENCE_FIELDS = frozenset(
    {
        "api_key_env",
        "key_env",
        "secret_env",
        "env_var",
    }
)
_MULTI_ENV_REFERENCE_FIELDS = frozenset(
    {
        "requires_env",
        "env_vars",
        "docker_forward_env",
    }
)
_PROFILE_SELECTOR_ENV_NAMES = frozenset(
    {
        "HERMES_HOME",
        "HERMES_CONFIG_PATH",
        "TAIJI_RUNTIME_HOME",
        HERMES_TOOLS_PROFILE_ENV_REQUIRED,
    }
)
_PROFILE_ENV_RESERVED_NAMES = frozenset(
    {
        *_PROFILE_SELECTOR_ENV_NAMES,
        "HOME",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "CODEX_HOME",
        "RUST_LOG",
        "SHELL",
        "TMP",
        "TEMP",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "HERMES_IMAGE_GENERATION_GATE_BRIDGE",
        "HERMES_IMAGE_GENERATION_GATE_BRIDGE_ID",
        "HERMES_IMAGE_GENERATION_GATE_PUBLIC_KEY",
    }
)
_TERMINAL_ENV_MAPPINGS = {
    "backend": "TERMINAL_ENV",
    "env_type": "TERMINAL_ENV",
    "cwd": "TERMINAL_CWD",
    "timeout": "TERMINAL_TIMEOUT",
    "lifetime_seconds": "TERMINAL_LIFETIME_SECONDS",
    "modal_mode": "TERMINAL_MODAL_MODE",
    "docker_image": "TERMINAL_DOCKER_IMAGE",
    "docker_forward_env": "TERMINAL_DOCKER_FORWARD_ENV",
    "docker_env": "TERMINAL_DOCKER_ENV",
    "docker_mount_cwd_to_workspace": (
        "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"
    ),
    "docker_run_as_host_user": "TERMINAL_DOCKER_RUN_AS_HOST_USER",
    "singularity_image": "TERMINAL_SINGULARITY_IMAGE",
    "modal_image": "TERMINAL_MODAL_IMAGE",
    "daytona_image": "TERMINAL_DAYTONA_IMAGE",
    "container_cpu": "TERMINAL_CONTAINER_CPU",
    "container_memory": "TERMINAL_CONTAINER_MEMORY",
    "container_disk": "TERMINAL_CONTAINER_DISK",
    "container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
    "docker_volumes": "TERMINAL_DOCKER_VOLUMES",
    "persistent_shell": "TERMINAL_PERSISTENT_SHELL",
    "ssh_host": "TERMINAL_SSH_HOST",
    "ssh_user": "TERMINAL_SSH_USER",
    "ssh_port": "TERMINAL_SSH_PORT",
    "ssh_key": "TERMINAL_SSH_KEY",
    "ssh_persistent": "TERMINAL_SSH_PERSISTENT",
    "local_persistent": "TERMINAL_LOCAL_PERSISTENT",
}


@dataclass(frozen=True)
class CodexProfileEnvironment:
    """One atomic profile snapshot for app-server and hermes-tools."""

    codex_env: dict[str, str]
    hermes_tools_env: dict[str, str]
    profile_env: dict[str, str]


def _stringify_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def _safe_profile_env(
    *,
    config_path: Path,
) -> dict[str, str]:
    """Read the selected profile without mutating process-global env."""
    from hermes_cli.config import load_env, read_raw_config

    result: dict[str, str] = {}
    for raw_name, raw_value in load_env().items():
        name = str(raw_name or "").strip()
        if (
            not _ENV_NAME_PATTERN.fullmatch(name)
            or name in _PROFILE_ENV_RESERVED_NAMES
            or raw_value is None
        ):
            continue
        result[name] = str(raw_value)

    try:
        config = read_raw_config(config_path=config_path)
    except TypeError:
        # Compatibility with older Hermes builds where read_raw_config() has
        # no explicit path parameter but still honors the active ContextVar.
        config = read_raw_config()
    except Exception:
        config = {}
    if not isinstance(config, dict):
        config = {}
    terminal = config.get("terminal")
    if isinstance(terminal, dict):
        for config_key, env_name in _TERMINAL_ENV_MAPPINGS.items():
            if config_key not in terminal or terminal[config_key] is None:
                continue
            value = terminal[config_key]
            if config_key == "cwd" and str(value) in {".", "auto", "cwd"}:
                continue
            result[env_name] = _stringify_env_value(value)
    return result


def _looks_secret_env_name(name: str) -> bool:
    normalized = str(name or "").strip().upper()
    return bool(
        _SECRET_ENV_NAME_PATTERN.search(normalized)
        or normalized.endswith(_SECRET_ENV_NAME_SUFFIXES)
        or normalized.startswith("TAIJI_CREDENTIAL_")
    )


def _config_referenced_env_names(config: Any) -> set[str]:
    """Collect exact environment names referenced by one raw profile config.

    Secret variables are user-defined identifiers, not necessarily names such
    as ``*_API_KEY``.  In particular custom providers support arbitrary
    ``key_env`` / ``api_key_env`` values.  Collect those declarations and
    every ``${NAME}`` template before environment expansion so a selected
    profile with a missing value cannot inherit the same name from its parent.
    """
    names: set[str] = set()

    def add_name(value: Any) -> None:
        if not isinstance(value, str):
            return
        candidate = value.strip()
        if _ENV_NAME_PATTERN.fullmatch(candidate):
            names.add(candidate)

    def collect_declared_names(value: Any) -> None:
        if isinstance(value, str):
            for candidate in re.split(r"[\s,]+", value.strip()):
                add_name(candidate)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    add_name(item.get("name"))
                    add_name(item.get("env"))
                    add_name(item.get("env_var"))
                else:
                    collect_declared_names(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                add_name(key)
                if isinstance(item, dict):
                    add_name(item.get("name"))
                    add_name(item.get("env"))
                    add_name(item.get("env_var"))

    def collect(value: Any, *, field_name: str = "") -> None:
        if isinstance(value, str):
            for match in re.finditer(r"\${([^}]+)}", value):
                add_name(match.group(1))
            if field_name in _SCALAR_ENV_REFERENCE_FIELDS:
                add_name(value)
            elif field_name in _MULTI_ENV_REFERENCE_FIELDS:
                collect_declared_names(value)
            return
        if isinstance(value, dict):
            for raw_key, item in value.items():
                normalized_key = re.sub(
                    r"(?<!^)(?=[A-Z])",
                    "_",
                    str(raw_key),
                ).lower()
                if normalized_key in _MULTI_ENV_REFERENCE_FIELDS:
                    collect_declared_names(item)
                collect(item, field_name=normalized_key)
            return
        if isinstance(value, list):
            if field_name in _MULTI_ENV_REFERENCE_FIELDS:
                collect_declared_names(value)
            for item in value:
                collect(item, field_name=field_name)

    collect(config)
    return names


def _profile_referenced_env_names(config_path: Path) -> set[str]:
    """Read env references from this exact profile's unexpanded YAML."""
    from hermes_cli.config import read_raw_config

    try:
        config = read_raw_config(config_path=config_path)
    except TypeError:
        config = read_raw_config()
    except Exception:
        return set()
    return _config_referenced_env_names(config)


def _known_managed_env_names(
    profile_env: dict[str, str] | None = None,
    referenced_env_names: set[str] | None = None,
) -> set[str]:
    """Return names that must never ambient-fallback across profiles."""
    names = set(profile_env or {})
    names.update(referenced_env_names or ())
    names.update(_PROFILE_SELECTOR_ENV_NAMES)
    names.update(_TERMINAL_ENV_MAPPINGS.values())
    try:
        from hermes_cli.config import OPTIONAL_ENV_VARS, _EXTRA_ENV_KEYS

        names.update(OPTIONAL_ENV_VARS)
        names.update(_EXTRA_ENV_KEYS)
    except Exception:
        pass
    try:
        from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST

        names.update(_HERMES_PROVIDER_ENV_BLOCKLIST)
    except Exception:
        pass
    names.update(
        name
        for name in os.environ
        if _looks_secret_env_name(name)
    )
    return {
        name
        for name in names
        if _ENV_NAME_PATTERN.fullmatch(str(name or ""))
        and name not in _PROFILE_ENV_RESERVED_NAMES
    } | {
        "HERMES_HOME",
        "HERMES_CONFIG_PATH",
        "TAIJI_RUNTIME_HOME",
    }


def capture_codex_profile_environment() -> CodexProfileEnvironment:
    """Capture a profile once without exposing its secrets to Codex shell."""
    from hermes_constants import get_config_path, get_hermes_home

    profile_home = Path(get_hermes_home()).expanduser()
    config_path = Path(get_config_path()).expanduser()
    profile_env = _safe_profile_env(config_path=config_path)
    referenced_env_names = _profile_referenced_env_names(config_path)
    codex_env = {
        name: ""
        for name in sorted(
            _known_managed_env_names(
                profile_env,
                referenced_env_names,
            )
        )
    }
    hermes_tools_env = {
        "HERMES_HOME": str(profile_home),
        "HERMES_CONFIG_PATH": str(config_path),
        # A fresh child gives TAIJI_RUNTIME_HOME precedence. Neutralize it so
        # the exact profile selectors above remain authoritative.
        "TAIJI_RUNTIME_HOME": "",
        HERMES_TOOLS_PROFILE_ENV_REQUIRED: "1",
    }
    return CodexProfileEnvironment(
        codex_env=codex_env,
        hermes_tools_env=hermes_tools_env,
        profile_env=profile_env,
    )


def capture_codex_profile_execution_env() -> dict[str, str]:
    """Backward-compatible app-server-only view of the profile snapshot."""
    return capture_codex_profile_environment().codex_env


def apply_hermes_tools_profile_env() -> bool:
    """Apply the selected profile inside the trusted MCP child only.

    Known credentials are cleared before validation and before profile values
    are applied.  Missing/invalid selectors therefore fail closed without
    retaining an ambient default account.
    """
    initial_known = _known_managed_env_names()
    for name in initial_known:
        if name not in _PROFILE_SELECTOR_ENV_NAMES:
            os.environ.pop(name, None)

    if os.environ.get(HERMES_TOOLS_PROFILE_ENV_REQUIRED) != "1":
        return False
    if os.environ.get("TAIJI_RUNTIME_HOME", "").strip():
        return False
    raw_home = os.environ.get("HERMES_HOME", "").strip()
    raw_config = os.environ.get("HERMES_CONFIG_PATH", "").strip()
    if not raw_home or not raw_config:
        return False
    home = Path(raw_home).expanduser()
    config_path = Path(raw_config).expanduser()
    if not home.is_absolute() or not config_path.is_absolute():
        return False
    try:
        if config_path.parent.resolve() != home.resolve():
            return False
    except OSError:
        return False
    if not home.is_dir():
        return False

    referenced_env_names = _profile_referenced_env_names(config_path)
    for name in _known_managed_env_names(
        referenced_env_names=referenced_env_names,
    ):
        if name not in _PROFILE_SELECTOR_ENV_NAMES:
            os.environ.pop(name, None)
    try:
        profile_env = _safe_profile_env(config_path=config_path)
    except Exception:
        return False
    for name in _known_managed_env_names(
        profile_env,
        referenced_env_names,
    ):
        if name not in _PROFILE_SELECTOR_ENV_NAMES:
            os.environ.pop(name, None)
    os.environ.update(profile_env)
    return True
