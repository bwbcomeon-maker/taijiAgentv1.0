"""Versioned runtime identity and long-lived Agent refresh for image capabilities."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.image_gen_verification import (
    CAPABILITY_VERIFICATION_SCHEMA_VERSION,
    _contains_unresolved_env,
    active_profile_name,
    verification_status_from_state,
)

_VISION_KEY_ENV = {
    "alibaba": "DASHSCOPE_API_KEY",
    "zai": "GLM_API_KEY",
    "custom": "AUXILIARY_VISION_API_KEY",
}
_VISION_TRANSPORT = {
    "alibaba": "dashscope_openai_compatible",
    "zai": "openai_chat_completions",
    "custom": "openai_chat_completions",
}


@dataclass(frozen=True)
class VisionResolvedMaterial:
    """One environment generation of the effective vision runtime identity."""

    config_data: dict[str, Any]
    vision_cfg: dict[str, Any]
    data_resolved: bool
    cfg_resolved: bool
    endpoint_resolved: bool

    @property
    def effective_config_resolved(self) -> bool:
        return bool(
            self.data_resolved
            and self.cfg_resolved
            and self.endpoint_resolved
        )


def _stable_fingerprint(material: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _custom_vision_identity(
    provider: str,
    config_data: dict[str, Any],
) -> dict[str, Any]:
    if not provider.startswith("custom:"):
        return {}
    try:
        from agent.custom_vision_providers import find_custom_vision_provider_entry

        entry = find_custom_vision_provider_entry(provider, config_data) or {}
    except (ImportError, ValueError):
        entry = {}
    if not isinstance(entry, dict):
        return {}
    return {
        "id": str(entry.get("id") or ""),
        "base_url": str(entry.get("base_url") or "").strip().rstrip("/"),
        "models": list(entry.get("models") or []),
        "default_model": str(entry.get("default_model") or ""),
        "transport": str(entry.get("transport") or ""),
        "credential_ref": str(entry.get("credential_ref") or ""),
        "network_scope": str(entry.get("network_scope") or ""),
        "trusted_proxy_profile": str(entry.get("trusted_proxy_profile") or ""),
    }


def resolve_vision_material(
    vision_cfg: dict[str, Any],
    config_data: dict[str, Any],
) -> VisionResolvedMaterial:
    """Resolve vision config and endpoint from one captured environment."""
    raw_data = config_data if isinstance(config_data, dict) else {}
    raw_cfg = vision_cfg if isinstance(vision_cfg, dict) else {}
    combined = {
        "config_data": raw_data,
        "vision_cfg": raw_cfg,
    }
    expansion_ok = True
    try:
        from hermes_cli.config import (
            _expand_env_vars,
            _referenced_env_snapshot,
        )

        env_snapshot = _referenced_env_snapshot(combined)
        expanded = _expand_env_vars(
            combined,
            env_snapshot=env_snapshot,
        )
    except Exception:
        expanded = combined
        expansion_ok = False
    if not isinstance(expanded, dict):
        expanded = {}
        expansion_ok = False
    expanded_data = expanded.get("config_data")
    if not isinstance(expanded_data, dict):
        expanded_data = {}
        expansion_ok = False
    expanded_cfg = expanded.get("vision_cfg")
    if not isinstance(expanded_cfg, dict):
        expanded_cfg = {}
        expansion_ok = False
    data_resolved = bool(
        expansion_ok and not _contains_unresolved_env(expanded_data)
    )
    cfg_resolved = bool(
        expansion_ok and not _contains_unresolved_env(expanded_cfg)
    )
    effective_cfg = dict(expanded_cfg)
    provider = str(effective_cfg.get("provider") or "").strip().lower()
    endpoint_resolved = True

    if provider.startswith("custom:"):
        custom_identity = _custom_vision_identity(provider, expanded_data)
        if custom_identity:
            effective_cfg["base_url"] = custom_identity["base_url"]
            effective_cfg["api_mode"] = (
                "anthropic_messages"
                if custom_identity["transport"] == "anthropic_messages"
                else "chat_completions"
            )
            effective_cfg["network_scope"] = (
                custom_identity.get("network_scope") or "public_direct"
            )
            effective_cfg["trusted_proxy_profile"] = (
                custom_identity.get("trusted_proxy_profile") or ""
            )
        else:
            effective_cfg["base_url"] = ""
            effective_cfg["api_mode"] = ""
            endpoint_resolved = False
    elif provider == "alibaba":
        try:
            from agent.alibaba_endpoints import build_vision_base_url

            effective_cfg["base_url"] = build_vision_base_url(
                endpoint_mode=str(
                    effective_cfg.get("endpoint_mode") or "public"
                ),
                region=str(effective_cfg.get("region") or "cn-beijing"),
                workspace_prefix=str(
                    effective_cfg.get("workspace_id") or ""
                ),
                custom_url=str(effective_cfg.get("base_url") or ""),
            )
            effective_cfg["api_mode"] = "chat_completions"
        except (ImportError, ValueError):
            effective_cfg["base_url"] = ""
            effective_cfg["api_mode"] = "chat_completions"
            endpoint_resolved = False
    elif provider == "zai":
        effective_cfg["base_url"] = (
            "https://open.bigmodel.cn/api/paas/v4"
        )
        effective_cfg["api_mode"] = "chat_completions"
    elif provider == "custom":
        effective_cfg["base_url"] = str(
            effective_cfg.get("base_url") or ""
        ).strip().rstrip("/")
        effective_cfg["api_mode"] = str(
            effective_cfg.get("api_mode") or "chat_completions"
        ).strip()
        endpoint_resolved = bool(effective_cfg["base_url"])

    return VisionResolvedMaterial(
        config_data=expanded_data,
        vision_cfg=effective_cfg,
        data_resolved=data_resolved,
        cfg_resolved=cfg_resolved,
        endpoint_resolved=endpoint_resolved,
    )


def resolve_effective_vision_config(
    vision_cfg: dict[str, Any],
    config_data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    """Compatibility tuple for callers that do not need to reuse material."""
    resolved = resolve_vision_material(vision_cfg, config_data)
    return (
        resolved.vision_cfg,
        resolved.config_data,
        bool(resolved.data_resolved and resolved.cfg_resolved),
        resolved.endpoint_resolved,
    )


def vision_fingerprint_from_material(
    resolved: VisionResolvedMaterial,
    *,
    profile: str,
    secret_value: str,
    key_configured: bool,
) -> tuple[str, bool]:
    """Fingerprint one already-resolved vision runtime material."""
    expanded_cfg = resolved.vision_cfg
    expanded_data = resolved.config_data
    provider = str(expanded_cfg.get("provider") or "").strip().lower()
    custom_identity = _custom_vision_identity(provider, expanded_data)
    base_url = str(
        custom_identity.get("base_url")
        if custom_identity
        else expanded_cfg.get("base_url")
        or ""
    ).strip().rstrip("/")
    transport = str(
        custom_identity.get("transport")
        or expanded_cfg.get("api_mode")
        or _VISION_TRANSPORT.get(provider)
        or ""
    ).strip()
    credential_ref = str(
        expanded_cfg.get("credential_ref")
        or custom_identity.get("credential_ref")
        or ""
    ).strip()
    try:
        from agent.provider_credentials import provider_family

        family = provider_family(provider)
    except Exception:
        family = provider
    config_resolved = bool(resolved.data_resolved and resolved.cfg_resolved)
    runtime_resolved = bool(config_resolved and resolved.endpoint_resolved)
    material = {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "capability": "vision",
        "effective_config_resolved": runtime_resolved,
        "profile": str(profile or "default"),
        "provider": provider,
        "provider_family": family,
        "model": str(expanded_cfg.get("model") or "").strip(),
        "base_url": base_url,
        "transport": transport,
        "credential_ref": credential_ref,
        "endpoint_mode": str(expanded_cfg.get("endpoint_mode") or "").strip(),
        "region": str(expanded_cfg.get("region") or "").strip(),
        "workspace_id": str(expanded_cfg.get("workspace_id") or "").strip(),
        "key_configured": bool(key_configured),
        "key_digest": hashlib.sha256(secret_value.encode("utf-8")).hexdigest()
        if secret_value
        else "",
        "custom_provider": custom_identity,
    }
    return _stable_fingerprint(material), runtime_resolved


def vision_fingerprint(
    vision_cfg: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any],
    secret_value: str,
    key_configured: bool,
) -> tuple[str, bool]:
    """Fingerprint the effective runtime vision target without exposing secrets."""
    return vision_fingerprint_from_material(
        resolve_vision_material(vision_cfg, config_data),
        profile=profile,
        secret_value=secret_value,
        key_configured=key_configured,
    )


def _vision_secret_env(
    provider: str,
    vision_cfg: dict[str, Any],
    config_data: dict[str, Any],
) -> str:
    credential_ref = str(vision_cfg.get("credential_ref") or "").strip()
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import (
                custom_vision_provider_secret_env,
                find_custom_vision_provider_entry,
            )

            entry = find_custom_vision_provider_entry(provider, config_data) or {}
            credential_ref = str(entry.get("credential_ref") or "").strip()
            if not credential_ref:
                return custom_vision_provider_secret_env(entry)
        except (ImportError, ValueError):
            return ""
    if credential_ref:
        try:
            from agent.provider_credentials import (
                credential_secret_env,
                load_credential,
                provider_family,
            )

            row = load_credential(credential_ref, config_data=config_data)
            if provider_family(row.get("provider_family")) != provider_family(provider):
                return ""
            expected = credential_secret_env(row.get("id"))
            if str(row.get("secret_env") or "").strip() != expected:
                return ""
            return expected
        except (ImportError, ValueError):
            return ""
    return _VISION_KEY_ENV.get(provider, "")


def _vision_state_root() -> Path:
    override = os.getenv("TAIJI_WEBUI_STATE_DIR") or os.getenv(
        "HERMES_WEBUI_STATE_DIR"
    )
    if override:
        return Path(override).expanduser() / "vision-verification"
    runtime_home = str(os.getenv("TAIJI_RUNTIME_HOME") or "").strip()
    if runtime_home:
        return Path(runtime_home).expanduser() / "web" / "vision-verification"
    return Path.home() / ".hermes" / "webui" / "vision-verification"


def vision_verification_state_path(profile: str) -> Path:
    profile_id = hashlib.sha256(
        str(profile or "default").encode("utf-8")
    ).hexdigest()[:24]
    return _vision_state_root() / f"{profile_id}.json"


def current_vision_runtime_snapshot() -> dict[str, Any]:
    """Read the effective config and its current versioned verification state."""
    try:
        from hermes_cli.config import load_config, load_env

        config_data = load_config()
        env_values = load_env()
    except Exception:
        config_data = {}
        env_values = {}
    if not isinstance(config_data, dict):
        config_data = {}
    auxiliary = config_data.get("auxiliary")
    vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
    resolved_material = resolve_vision_material(vision_cfg, config_data)
    effective_cfg = resolved_material.vision_cfg
    effective_config_data = resolved_material.config_data
    config_resolved = bool(
        resolved_material.data_resolved
        and resolved_material.cfg_resolved
    )
    endpoint_resolved = resolved_material.endpoint_resolved
    provider = str(effective_cfg.get("provider") or "").strip().lower()
    custom_identity = _custom_vision_identity(
        provider,
        effective_config_data,
    )
    secret_env = _vision_secret_env(
        provider,
        effective_cfg,
        effective_config_data,
    )
    try:
        from agent.image_gen_verification import image_gen_runtime_context
        from agent.provider_credentials import resolve_secret_env_value

        secret_value = (
            resolve_secret_env_value(
                secret_env,
                config_path=image_gen_runtime_context().config_path,
            )
            if secret_env
            else ""
        )
    except ValueError:
        secret_value = ""
    except ImportError:
        secret_value = str(
            (
                env_values.get(secret_env)
                if isinstance(env_values, dict) and secret_env
                else ""
            )
            or ""
        ).strip()
    profile = active_profile_name()
    fingerprint, resolved = vision_fingerprint_from_material(
        resolved_material,
        profile=profile,
        secret_value=secret_value,
        key_configured=bool(secret_value),
    )
    model = str(effective_cfg.get("model") or "").strip()
    custom_complete = bool(
        not provider.startswith("custom:")
        or (
            custom_identity
            and model in set(custom_identity.get("models") or [])
        )
    )
    configured = bool(
        provider
        and model
        and secret_value
        and custom_complete
        and config_resolved
        and endpoint_resolved
        and resolved
    )
    try:
        state = json.loads(
            vision_verification_state_path(profile).read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError):
        state = {}
    status = (
        verification_status_from_state(
            state,
            expected_fingerprint=fingerprint,
        )
        if configured
        else "unconfigured"
    )
    reason_code = ""
    if not resolved:
        status = "configured_unverified"
        reason_code = "unresolved_effective_config"
    elif not configured:
        reason_code = "vision_not_configured"
    elif status != "verified":
        reason_code = "verification_required"
    return {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "status": status,
        "available": bool(configured and status == "verified"),
        "reason_code": reason_code,
        "configured": configured,
        "provider": provider,
        "model": model,
        "base_url": str(effective_cfg.get("base_url") or "").strip(),
        "transport": str(effective_cfg.get("api_mode") or "").strip(),
    }


def current_image_runtime_snapshot() -> dict[str, Any]:
    """Normalize the image readiness identity used by cache, refresh, and gates."""
    try:
        from tools.image_generation_tool import get_image_generation_readiness

        readiness = get_image_generation_readiness()
    except Exception:
        readiness = {}
    if not isinstance(readiness, dict):
        readiness = {}
    raw_schema_version = readiness.get("verification_schema_version")
    schema_version = raw_schema_version if type(raw_schema_version) is int else 0
    schema_valid = (
        type(raw_schema_version) is int
        and raw_schema_version == CAPABILITY_VERIFICATION_SCHEMA_VERSION
    )
    status = str(readiness.get("verification_status") or "configured_unverified")
    available = bool(readiness.get("available"))
    reason_code = str(readiness.get("reason_code") or "")
    if not schema_valid:
        status = "configured_unverified"
        available = False
        reason_code = "verification_schema_mismatch"
    fingerprint = str(
        readiness.get("capability_fingerprint")
        or readiness.get("runtime_fingerprint")
        or readiness.get("verification_fingerprint")
        or ""
    )
    if not fingerprint:
        fingerprint = _stable_fingerprint(
            {
                "schema_version": schema_version,
                "status": status,
                "provider": str(readiness.get("provider") or ""),
                "model": str(readiness.get("model") or ""),
                "available": available,
                "reason_code": reason_code,
            }
        )
    return {
        "schema_version": schema_version,
        "fingerprint": fingerprint,
        "status": status,
        "available": available,
        "provider": str(readiness.get("provider") or ""),
        "model": str(readiness.get("model") or ""),
        "reason_code": reason_code,
    }


def _drifted_snapshot(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    capability: str,
) -> dict[str, Any]:
    """Return a stable failed-closed identity when config changes mid-read."""
    fingerprint = _stable_fingerprint(
        {
            "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
            "capability": capability,
            "reason_code": "runtime_config_changed_during_snapshot",
            "before": str(before.get("fingerprint") or ""),
            "after": str(after.get("fingerprint") or ""),
        }
    )
    result = dict(after)
    result.update(
        {
            "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "status": "configured_unverified",
            "available": False,
            "reason_code": "runtime_config_changed_during_snapshot",
        }
    )
    return result


def verification_runtime_snapshot(
    capability: str = "image_generation",
) -> dict[str, Any]:
    """Capture one immutable runtime authorization identity.

    Reading twice brackets the persisted state read. If config identity changes
    between A and B, the result cannot authorize a Provider call.
    """
    normalized = str(capability or "image_generation").strip().lower()
    reader = (
        current_vision_runtime_snapshot
        if normalized in {"vision", "image_analysis"}
        else current_image_runtime_snapshot
    )
    before = reader()
    after = reader()
    if (
        type(before.get("schema_version")) is not type(after.get("schema_version"))
        or before.get("schema_version") != after.get("schema_version")
        or str(before.get("fingerprint") or "")
        != str(after.get("fingerprint") or "")
    ):
        return _drifted_snapshot(before, after, capability=normalized)
    snapshot = dict(after)
    schema_version = snapshot.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != CAPABILITY_VERIFICATION_SCHEMA_VERSION
    ):
        snapshot.update(
            {
                "status": "configured_unverified",
                "available": False,
                "reason_code": "verification_schema_mismatch",
            }
        )
    return snapshot


def _tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "")


def refresh_agent_image_runtime(
    agent: Any,
    *,
    definitions_loader: Any = None,
) -> bool:
    """Atomically replace registry schemas while preserving injected tools."""
    lock = getattr(agent, "_image_runtime_lock", None)
    if lock is None:
        lock = threading.RLock()
        agent._image_runtime_lock = lock

    snapshot = verification_runtime_snapshot("image_generation")
    fingerprint = str(snapshot.get("fingerprint") or "")
    runtime_identity = (
        snapshot.get("schema_version"),
        fingerprint,
        str(snapshot.get("status") or ""),
        bool(snapshot.get("available")),
    )
    with lock:
        previous_runtime_identity = getattr(
            agent,
            "_image_runtime_identity",
            None,
        )
        if runtime_identity == previous_runtime_identity:
            return False
        previous_registry_names = set(
            getattr(agent, "_registry_tool_names", set()) or set()
        )
        previous_tools = list(getattr(agent, "tools", None) or [])
    try:
        if definitions_loader is None:
            from model_tools import get_tool_definitions

            definitions_loader = get_tool_definitions
        definitions = definitions_loader(
            enabled_toolsets=getattr(agent, "enabled_toolsets", None),
            disabled_toolsets=getattr(agent, "disabled_toolsets", None),
            quiet_mode=getattr(agent, "quiet_mode", True),
        )
    except Exception:
        return False

    final_snapshot = verification_runtime_snapshot("image_generation")
    final_identity = (
        final_snapshot.get("schema_version"),
        str(final_snapshot.get("fingerprint") or ""),
        str(final_snapshot.get("status") or ""),
        bool(final_snapshot.get("available")),
    )
    if final_identity != runtime_identity:
        return False

    registry_names = {_tool_name(item) for item in (definitions or [])}
    registry_names.discard("")
    merged = list(definitions or [])
    merged_names = set(registry_names)
    for item in previous_tools:
        name = _tool_name(item)
        if name in previous_registry_names or name in merged_names:
            continue
        merged.append(item)
        if name:
            merged_names.add(name)

    with lock:
        # Another turn may have refreshed this instance while definitions were
        # being built. Discard this candidate instead of publishing a mixed
        # generation.
        if (
            getattr(agent, "_image_runtime_identity", None)
            != previous_runtime_identity
        ):
            return False
        agent.tools = merged
        agent.valid_tool_names = merged_names
        agent._registry_tool_names = registry_names
        agent._image_capability_fingerprint = fingerprint
        agent._image_runtime_identity = runtime_identity
        if hasattr(agent, "_cached_system_prompt"):
            agent._cached_system_prompt = None
            agent._force_system_prompt_rebuild = True
    return True
