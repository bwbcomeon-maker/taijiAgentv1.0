"""Shared image-generation verification contract for Agent and WebUI runtimes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from agent.image_gen_runtime_contracts import (
    VERIFIABLE_BUILTIN_IMAGE_PROVIDERS,
    builtin_image_runtime_contract,
)

VERIFYING_TTL_SECONDS = 15 * 60
VALID_PERSISTED_STATUSES = {"verifying", "verified", "failed"}
CAPABILITY_VERIFICATION_SCHEMA_VERSION = 1
# Compatibility alias for existing callers. New code should use the canonical
# public name above so the persisted contract is unambiguous across runtimes.
VERIFICATION_STATE_SCHEMA_VERSION = CAPABILITY_VERIFICATION_SCHEMA_VERSION
CAPABILITY_CONFIG_EPOCHS_KEY = "_taiji_capability_epochs"
CAPABILITY_CONFIG_EPOCH_VISION = "vision"
CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION = "image_generation"
CAPABILITY_PROFILE_INCARNATION_KEY = "_taiji_profile_incarnation"
_CAPABILITY_CONFIG_EPOCH_NAMES = frozenset(
    {
        CAPABILITY_CONFIG_EPOCH_VISION,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    }
)
_UNRESOLVED_ENV_TOKEN = re.compile(r"\${[^}]+}")
_DASHSCOPE_RUNTIME_ENV = (
    "DASHSCOPE_ENDPOINT_MODE",
    "DASHSCOPE_REGION",
    "DASHSCOPE_WORKSPACE_ID",
    "DASHSCOPE_BASE_URL",
)
_DISABLED_IMAGE_GEN_PROVIDERS = frozenset(
    {"none", "disabled", "off", "false", "0"}
)
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
_IMAGE_BINDING_SEAL_KEY = os.urandom(32)


def capability_config_epoch(
    config_data: Any,
    capability: str,
) -> int:
    """Read one durable capability generation from config.yaml."""
    normalized = str(capability or "").strip().lower()
    if normalized not in _CAPABILITY_CONFIG_EPOCH_NAMES:
        raise ValueError(f"unsupported capability config epoch: {capability}")
    epochs = (
        config_data.get(CAPABILITY_CONFIG_EPOCHS_KEY)
        if isinstance(config_data, Mapping)
        else None
    )
    value = epochs.get(normalized) if isinstance(epochs, Mapping) else None
    return value if type(value) is int and value >= 0 else 0


def capability_profile_incarnation(config_data: Any) -> str:
    """Read the non-secret identity of one profile filesystem incarnation."""
    if not isinstance(config_data, Mapping):
        return ""
    return str(
        config_data.get(CAPABILITY_PROFILE_INCARNATION_KEY) or ""
    ).strip()


def bump_capability_config_epochs(
    config_data: dict[str, Any],
    *capabilities: str,
) -> dict[str, int]:
    """Advance capability generations inside the same durable config commit."""
    if not isinstance(config_data, dict):
        raise ValueError("capability config epoch target must be a mapping")
    epochs = config_data.get(CAPABILITY_CONFIG_EPOCHS_KEY)
    updated = dict(epochs) if isinstance(epochs, Mapping) else {}
    result: dict[str, int] = {}
    for capability in capabilities:
        normalized = str(capability or "").strip().lower()
        if normalized not in _CAPABILITY_CONFIG_EPOCH_NAMES:
            raise ValueError(
                f"unsupported capability config epoch: {capability}"
            )
        current = capability_config_epoch(config_data, normalized)
        next_epoch = current + 1
        updated[normalized] = next_epoch
        result[normalized] = next_epoch
    if result:
        config_data[CAPABILITY_CONFIG_EPOCHS_KEY] = updated
    return result


def _capability_authorization_config(
    config_data: Mapping[str, Any],
    capability: str,
) -> dict[str, Any]:
    """Project config fields that can change one capability authorization."""
    def credential_projection(
        provider: str,
        credential_ref: Any,
    ) -> list[dict[str, Any]]:
        try:
            from agent.provider_credentials import (
                normalize_credential_id,
                provider_family,
            )
        except ImportError:
            return []
        ref = str(credential_ref or "").strip()
        family = provider_family(provider)
        rows = config_data.get("provider_credentials")
        if not isinstance(rows, list):
            return []
        projected: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                row_id = normalize_credential_id(row.get("id"))
            except ValueError:
                continue
            if ref:
                try:
                    if row_id != normalize_credential_id(ref):
                        continue
                except ValueError:
                    continue
            elif (
                row.get("default") is not True
                or provider_family(row.get("provider_family")) != family
            ):
                continue
            projected.append(
                {
                    "id": row_id,
                    "provider_family": provider_family(
                        row.get("provider_family")
                    ),
                    "auth_type": str(
                        row.get("auth_type") or "api_key"
                    ).strip().lower(),
                    "secret_env": str(
                        row.get("secret_env") or ""
                    ).strip(),
                    "default": row.get("default") is True,
                }
            )
        return projected

    if capability == CAPABILITY_CONFIG_EPOCH_VISION:
        auxiliary = config_data.get("auxiliary")
        vision = (
            auxiliary.get("vision")
            if isinstance(auxiliary, Mapping)
            else None
        )
        vision_cfg = vision if isinstance(vision, Mapping) else {}
        provider = str(
            vision_cfg.get("provider") or ""
        ).strip().lower()
        custom_identity: dict[str, Any] = {}
        credential_ref = vision_cfg.get("credential_ref")
        if provider.startswith("custom:"):
            try:
                from agent.custom_vision_providers import (
                    find_custom_vision_provider_entry,
                )

                entry = find_custom_vision_provider_entry(
                    provider,
                    dict(config_data),
                )
                if isinstance(entry, Mapping):
                    custom_identity = {
                        str(key): value
                        for key, value in entry.items()
                        if key != "name"
                    }
                    credential_ref = entry.get("credential_ref")
            except (ImportError, ValueError):
                custom_identity = {}
        return {
            "vision": vision,
            "custom_provider": custom_identity,
            "provider_credentials": credential_projection(
                provider,
                credential_ref,
            ),
        }
    if capability == CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION:
        image_gen = config_data.get("image_gen")
        image_cfg = (
            image_gen if isinstance(image_gen, Mapping) else {}
        )
        provider = str(
            image_cfg.get("provider") or ""
        ).strip().lower()
        custom_identity = active_custom_provider_identity(
            provider,
            dict(config_data),
        )
        credential_ref = (
            custom_identity.get("credential_ref")
            if custom_identity
            else image_cfg.get("credential_ref")
        )
        return {
            "image_gen": image_gen,
            "custom_provider": custom_identity,
            "provider_credentials": credential_projection(
                provider,
                credential_ref,
            ),
        }
    raise ValueError(f"unsupported capability config epoch: {capability}")


def reconcile_capability_config_epochs(
    previous: Mapping[str, Any],
    desired: dict[str, Any],
) -> dict[str, int]:
    """Preserve monotonic epochs across whole-config saves and restores."""
    if not isinstance(desired, dict):
        raise ValueError("capability config epoch target must be a mapping")
    old = previous if isinstance(previous, Mapping) else {}
    previous_incarnation = capability_profile_incarnation(old)
    if previous_incarnation:
        desired[CAPABILITY_PROFILE_INCARNATION_KEY] = previous_incarnation
    incoming_epochs = desired.get(CAPABILITY_CONFIG_EPOCHS_KEY)
    merged = (
        dict(incoming_epochs)
        if isinstance(incoming_epochs, Mapping)
        else {}
    )
    result: dict[str, int] = {}
    for capability in (
        CAPABILITY_CONFIG_EPOCH_VISION,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    ):
        baseline = max(
            capability_config_epoch(old, capability),
            capability_config_epoch(desired, capability),
        )
        changed = (
            _capability_authorization_config(old, capability)
            != _capability_authorization_config(desired, capability)
        )
        next_epoch = baseline + 1 if changed else baseline
        if next_epoch:
            merged[capability] = next_epoch
            result[capability] = next_epoch
    if merged:
        desired[CAPABILITY_CONFIG_EPOCHS_KEY] = merged
    else:
        desired.pop(CAPABILITY_CONFIG_EPOCHS_KEY, None)
    return result


def _configured_default_secret_env(
    config_data: Mapping[str, Any],
    provider: str,
) -> str:
    """Resolve default credential metadata without depending on secret value."""
    try:
        from agent.provider_credentials import (
            credential_secret_env,
            provider_family,
        )
    except ImportError:
        return ""
    family = provider_family(provider)
    rows = config_data.get("provider_credentials")
    if not isinstance(rows, list):
        return ""
    matches: list[str] = []
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or row.get("default") is not True
            or provider_family(row.get("provider_family")) != family
        ):
            continue
        try:
            expected = credential_secret_env(row.get("id"))
        except ValueError:
            continue
        if str(row.get("secret_env") or "").strip() == expected:
            matches.append(expected)
    return matches[0] if len(matches) == 1 else ""


def _referenced_credential_secret_env(
    config_data: Mapping[str, Any],
    provider: str,
    credential_ref: Any,
) -> str:
    """Resolve configured credential metadata without reading its secret."""
    ref = str(credential_ref or "").strip()
    if not ref:
        return _configured_default_secret_env(config_data, provider)
    try:
        from agent.provider_credentials import (
            credential_secret_env,
            find_credential,
            provider_family,
        )

        row = find_credential(dict(config_data), ref)
        if (
            not isinstance(row, Mapping)
            or provider_family(row.get("provider_family"))
            != provider_family(provider)
        ):
            return ""
        expected = credential_secret_env(row.get("id"))
        if str(row.get("secret_env") or "").strip() != expected:
            return ""
        return expected
    except (ImportError, ValueError):
        return ""


def capability_epochs_for_secret_env(
    config_data: Mapping[str, Any],
    secret_env: str,
    *,
    env_values: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return active capabilities whose effective secret env is changing."""
    key = str(secret_env or "").strip()
    if not key or not isinstance(config_data, Mapping):
        return ()
    capabilities: list[str] = []
    auxiliary = config_data.get("auxiliary")
    vision = (
        auxiliary.get("vision")
        if isinstance(auxiliary, Mapping)
        else None
    )
    if isinstance(vision, Mapping):
        provider = str(vision.get("provider") or "").strip().lower()
        resolved_env = ""
        if provider.startswith("custom:"):
            try:
                from agent.custom_vision_providers import (
                    custom_vision_provider_secret_env,
                    find_custom_vision_provider_entry,
                )

                entry = (
                    find_custom_vision_provider_entry(
                        provider,
                        dict(config_data),
                    )
                    or {}
                )
                entry_ref = str(
                    entry.get("credential_ref") or ""
                ).strip()
                if entry_ref:
                    resolved_env = _referenced_credential_secret_env(
                        config_data,
                        "custom",
                        entry_ref,
                    )
                else:
                    resolved_env = (
                        custom_vision_provider_secret_env(entry)
                    )
            except (ImportError, ValueError):
                resolved_env = ""
        else:
            raw_ref = str(
                vision.get("credential_ref") or ""
            ).strip()
            resolved_env = _referenced_credential_secret_env(
                config_data,
                provider,
                raw_ref,
            )
            if (
                not raw_ref
                and resolved_env
                and env_values is not None
                and key != resolved_env
                and not str(env_values.get(resolved_env) or "").strip()
            ):
                resolved_env = ""
        if not resolved_env:
            resolved_env = {
                "alibaba": "DASHSCOPE_API_KEY",
                "zai": "GLM_API_KEY",
                "custom": "AUXILIARY_VISION_API_KEY",
            }.get(provider, "")
        if resolved_env == key:
            capabilities.append(CAPABILITY_CONFIG_EPOCH_VISION)
    image_gen = config_data.get("image_gen")
    if isinstance(image_gen, Mapping):
        provider = str(image_gen.get("provider") or "").strip().lower()
        raw_ref = str(
            image_gen.get("credential_ref") or ""
        ).strip()
        if provider.startswith("custom:"):
            custom_identity = active_custom_provider_identity(
                provider,
                dict(config_data),
            )
            entry_ref = str(
                custom_identity.get("credential_ref") or ""
            ).strip()
            if entry_ref:
                resolved_env = _referenced_credential_secret_env(
                    config_data,
                    "custom",
                    entry_ref,
                )
            else:
                resolved_env = str(
                    custom_identity.get("secret_env") or ""
                ).strip()
        else:
            resolved_env = _referenced_credential_secret_env(
                config_data,
                provider,
                raw_ref,
            )
            if (
                not raw_ref
                and resolved_env
                and env_values is not None
                and key != resolved_env
                and not str(env_values.get(resolved_env) or "").strip()
            ):
                resolved_env = ""
        if not resolved_env:
            resolved_env = IMAGE_GEN_KEY_ENV.get(provider, "")
        if resolved_env == key:
            capabilities.append(
                CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION
            )
    return tuple(capabilities)


def image_gen_provider_target(provider: Any) -> bool:
    """Whether image config selects a real Provider target."""
    normalized = str(provider or "").strip().lower()
    return bool(
        normalized
        and normalized not in _DISABLED_IMAGE_GEN_PROVIDERS
    )


def _contains_unresolved_env(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_UNRESOLVED_ENV_TOKEN.search(value))
    if isinstance(value, dict):
        return any(
            _contains_unresolved_env(key) or _contains_unresolved_env(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_unresolved_env(child) for child in value)
    return False


def expand_effective_config(value: Any) -> tuple[Any, bool]:
    """Apply the runtime config expander and report unresolved env tokens."""
    try:
        from hermes_cli.config import _expand_env_vars

        expanded = _expand_env_vars(value)
    except Exception:
        return value, False
    return expanded, not _contains_unresolved_env(expanded)


@dataclass(frozen=True)
class ImageGenResolvedMaterial:
    """One env generation of all inputs used by image capability verification."""

    config_data: dict[str, Any]
    image_cfg: dict[str, Any]
    data_resolved: bool
    cfg_resolved: bool
    provider: str
    runtime_identity: dict[str, Any]
    effective_config_resolved: bool


@dataclass(frozen=True)
class ImageGenRequestBinding:
    """Private request-local image target captured before a verification probe."""

    provider: str
    model: str
    api_key: str = field(repr=False, compare=False)
    runtime_identity: Mapping[str, Any] = field(
        repr=False,
        compare=False,
    )
    _authorization_fingerprint: str = field(
        default="",
        repr=False,
        compare=False,
    )
    _authorization_generation: str = field(
        default="",
        repr=False,
        compare=False,
    )
    _provider_config: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
        compare=False,
    )
    _authorization_seal: str = field(
        default="",
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider",
            str(self.provider or "").strip().lower(),
        )
        object.__setattr__(
            self,
            "model",
            str(self.model or "").strip(),
        )
        object.__setattr__(
            self,
            "api_key",
            str(self.api_key or "").strip(),
        )
        object.__setattr__(
            self,
            "runtime_identity",
            _freeze_private_binding_value(self.runtime_identity or {}),
        )
        object.__setattr__(
            self,
            "_authorization_fingerprint",
            str(self._authorization_fingerprint or "").strip(),
        )
        object.__setattr__(
            self,
            "_authorization_generation",
            str(self._authorization_generation or "").strip(),
        )
        object.__setattr__(
            self,
            "_provider_config",
            _freeze_private_binding_value(self._provider_config or {}),
        )
        object.__setattr__(self, "_authorization_seal", "")

    @property
    def authorization_fingerprint(self) -> str:
        """Private fingerprint tying material, secret and profile together."""
        return self._authorization_fingerprint

    @property
    def authorization_generation(self) -> str:
        """Private persisted verification-state generation."""
        return self._authorization_generation

    @property
    def provider_config(self) -> Mapping[str, Any]:
        """Deeply immutable request-local custom Provider configuration."""
        return self._provider_config


def _freeze_private_binding_value(value: Any) -> Any:
    """Recursively freeze private request material before crossing a boundary."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze_private_binding_value(child)
                for key, child in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_private_binding_value(child)
            for child in value
        )
    if isinstance(value, (set, frozenset)):
        return frozenset(
            _freeze_private_binding_value(child)
            for child in value
        )
    return value


def _binding_seal_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _binding_seal_json_value(child)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_binding_seal_json_value(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_binding_seal_json_value(child) for child in value),
            key=lambda child: json.dumps(
                child,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("unsupported private image binding value")


def _image_binding_seal_value(binding: ImageGenRequestBinding) -> str:
    material = {
        "authorization_fingerprint": binding.authorization_fingerprint,
        "authorization_generation": binding.authorization_generation,
        "provider": binding.provider,
        "model": binding.model,
        "api_key_digest": hashlib.sha256(
            binding.api_key.encode("utf-8")
        ).hexdigest(),
        "runtime_identity": _binding_seal_json_value(
            binding.runtime_identity
        ),
        "provider_config": _binding_seal_json_value(
            binding.provider_config
        ),
    }
    return hmac.new(
        _IMAGE_BINDING_SEAL_KEY,
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def authorize_image_gen_request_binding(
    binding: ImageGenRequestBinding,
    *,
    authorization_fingerprint: str,
    authorization_generation: str,
) -> ImageGenRequestBinding:
    """Seal one trusted request-local image binding to persisted state."""
    if not isinstance(binding, ImageGenRequestBinding):
        raise TypeError("binding must be ImageGenRequestBinding")
    fingerprint = str(authorization_fingerprint or "").strip()
    generation = str(authorization_generation or "").strip()
    if not fingerprint or not generation:
        raise ValueError(
            "image authorization fingerprint and generation are required"
        )
    object.__setattr__(
        binding,
        "_authorization_fingerprint",
        fingerprint,
    )
    object.__setattr__(
        binding,
        "_authorization_generation",
        generation,
    )
    object.__setattr__(
        binding,
        "_authorization_seal",
        _image_binding_seal_value(binding),
    )
    return binding


def image_gen_request_binding_matches_authorization(
    binding: Any,
    *,
    authorization_fingerprint: str,
    authorization_generation: str,
) -> bool:
    """Match generation identity and all sealed private request material."""
    if not isinstance(binding, ImageGenRequestBinding):
        return False
    expected_fingerprint = str(
        authorization_fingerprint or ""
    ).strip()
    expected_generation = str(
        authorization_generation or ""
    ).strip()
    if (
        not expected_fingerprint
        or not expected_generation
        or binding.authorization_fingerprint != expected_fingerprint
        or binding.authorization_generation != expected_generation
        or not binding._authorization_seal
    ):
        return False
    try:
        expected_seal = _image_binding_seal_value(binding)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(binding._authorization_seal, expected_seal)


class ImageGenRequestAuthorizationError(RuntimeError):
    """Stable fail-closed signal for request-local Provider I/O guards."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = str(error_code or "capability_caller_stale")


def build_image_gen_request_reauth_guard(
    binding: ImageGenRequestBinding,
    *,
    expected_snapshot: Mapping[str, Any],
) -> Callable[[], None]:
    """Build a reusable guard for verified calls and verifying probes."""
    expected = dict(expected_snapshot)
    fingerprint = str(expected.get("fingerprint") or "").strip()
    generation = str(
        expected.get("_authorization_generation") or ""
    ).strip()
    status = str(expected.get("status") or "").strip()
    provider = str(expected.get("provider") or "").strip().lower()
    model = str(expected.get("model") or "").strip()
    if (
        expected.get("schema_version")
        != CAPABILITY_VERIFICATION_SCHEMA_VERSION
        or status not in {"verified", "verifying"}
        or not provider
        or not model
        or binding.provider != provider
        or binding.model != model
        or not image_gen_request_binding_matches_authorization(
            binding,
            authorization_fingerprint=fingerprint,
            authorization_generation=generation,
        )
    ):
        raise ImageGenRequestAuthorizationError(
            "capability_binding_mismatch"
        )

    def _guard() -> None:
        from agent.image_runtime import verification_runtime_snapshot

        current = verification_runtime_snapshot("image_generation")
        authorized = bool(
            current.get("schema_version")
            == CAPABILITY_VERIFICATION_SCHEMA_VERSION
            and str(current.get("fingerprint") or "") == fingerprint
            and str(current.get("_authorization_generation") or "")
            == generation
            and str(current.get("status") or "") == status
            and str(current.get("provider") or "").strip().lower()
            == provider
            and str(current.get("model") or "").strip() == model
            and (status == "verifying" or bool(current.get("available")))
            and image_gen_request_binding_matches_authorization(
                binding,
                authorization_fingerprint=fingerprint,
                authorization_generation=generation,
            )
        )
        if not authorized:
            raise ImageGenRequestAuthorizationError(
                "capability_caller_stale"
            )

    return _guard


def require_image_gen_request_binding(
    value: Any,
    *,
    provider: str,
    model: str,
) -> ImageGenRequestBinding:
    """Validate one private probe binding without falling back to live config."""
    if not isinstance(value, ImageGenRequestBinding):
        raise ValueError("invalid pinned image request binding")
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip()
    if (
        value.provider != normalized_provider
        or value.model != normalized_model
        or not value.api_key
        or not value.authorization_fingerprint
        or not value.authorization_generation
        or not image_gen_request_binding_matches_authorization(
            value,
            authorization_fingerprint=value.authorization_fingerprint,
            authorization_generation=value.authorization_generation,
        )
        or not value.runtime_identity.get("identity_supported")
        or not value.runtime_identity.get("endpoint_resolved")
    ):
        raise ValueError("pinned image request binding does not match target")
    return value


@dataclass(frozen=True)
class ImageGenRuntimeContext:
    """Config path and verification profile resolved from one runtime scope."""

    config_path: Path
    profile: str


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


def image_gen_runtime_context() -> ImageGenRuntimeContext:
    """Resolve the active image config path and state profile together."""
    from hermes_constants import (
        get_config_path,
        get_hermes_home_override,
    )

    config_path = get_config_path()
    if str(os.getenv("TAIJI_RUNTIME_HOME") or "").strip():
        return ImageGenRuntimeContext(
            config_path=config_path,
            profile="default",
        )
    context_home = get_hermes_home_override()
    if context_home:
        scoped_home = Path(context_home).expanduser()
        if scoped_home.parent.name == "profiles" and scoped_home.name:
            return ImageGenRuntimeContext(
                config_path=config_path,
                profile=scoped_home.name,
            )
    explicit = str(os.getenv("HERMES_PROFILE_NAME") or "").strip()
    if explicit:
        return ImageGenRuntimeContext(
            config_path=config_path,
            profile=explicit,
        )
    home = Path(os.getenv("HERMES_HOME") or "~/.hermes").expanduser()
    if home.parent.name == "profiles" and home.name:
        return ImageGenRuntimeContext(
            config_path=config_path,
            profile=home.name,
        )
    try:
        sticky = home / "active_profile"
        value = sticky.read_text(encoding="utf-8").strip()
        profile = value or "default"
    except OSError:
        profile = (
            str(os.getenv("HERMES_PROFILE") or "default").strip()
            or "default"
        )
    return ImageGenRuntimeContext(
        config_path=config_path,
        profile=profile,
    )


def active_profile_name() -> str:
    return image_gen_runtime_context().profile


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
            OPENAI_IMAGES_TRANSPORT,
            custom_image_provider_secret_env,
            load_custom_image_provider_entries,
            normalize_custom_image_provider_id,
            openai_images_generation_endpoint,
        )
    except Exception:
        return {}
    try:
        requested_id = normalize_custom_image_provider_id(requested_id)
    except ValueError:
        return {}
    for normalized in load_custom_image_provider_entries(config_data):
        if normalized.get("id") != requested_id:
            continue
        try:
            secret_env = custom_image_provider_secret_env(normalized)
        except ValueError:
            return {}
        return {
            "id": normalized["id"],
            "base_url": normalized["base_url"],
            "credential_ref": normalized["credential_ref"],
            "secret_env": secret_env,
            "models": list(normalized["models"]),
            "default_model": normalized["default_model"],
            "allow_custom_model_id": normalized["allow_custom_model_id"],
            "size_map": dict(normalized["size_map"]),
            "response_format": normalized["response_format"],
            "timeout_seconds": normalized["timeout_seconds"],
            "network_scope": normalized["network_scope"],
            "trusted_proxy_profile": normalized["trusted_proxy_profile"],
            "transport": OPENAI_IMAGES_TRANSPORT,
            "endpoint": openai_images_generation_endpoint(
                normalized["base_url"]
            ),
        }
    return {}


def image_gen_runtime_identity(
    provider: str,
    image_cfg: dict[str, Any],
    *,
    config_data: dict[str, Any] | None = None,
    config_is_expanded: bool = False,
    env_snapshot: dict[str, tuple[bool, str]] | None = None,
) -> dict[str, Any]:
    """Resolve the transport and endpoint the selected image Provider will use."""
    normalized_provider = str(provider or "").strip().lower()
    cfg = image_cfg if isinstance(image_cfg, dict) else {}
    if not config_is_expanded:
        expanded_cfg, _cfg_resolved = expand_effective_config(cfg)
        cfg = expanded_cfg if isinstance(expanded_cfg, dict) else {}
    if normalized_provider.startswith("custom:"):
        custom_identity = active_custom_provider_identity(
            normalized_provider,
            config_data if isinstance(config_data, dict) else {},
        )
        return {
            "transport": str(custom_identity.get("transport") or ""),
            "endpoint": str(custom_identity.get("endpoint") or ""),
            "identity_supported": True,
            "endpoint_resolved": bool(custom_identity.get("endpoint")),
        }

    builtin_contract = builtin_image_runtime_contract(normalized_provider)
    transport = str(builtin_contract.get("transport") or "")
    if normalized_provider == "dashscope":
        options = cfg.get("options")
        if not isinstance(options, dict):
            options = {}
        credential_ref = str(cfg.get("credential_ref") or "").strip()

        def option(name: str, env_var: str) -> str:
            if not credential_ref:
                if env_snapshot is None:
                    env_value = str(os.getenv(env_var) or "").strip()
                else:
                    present, captured = env_snapshot.get(
                        env_var,
                        (False, ""),
                    )
                    env_value = str(captured or "").strip() if present else ""
                if env_value:
                    return env_value
            return str(options.get(name) or "").strip()

        endpoint_mode = option(
            "endpoint_mode",
            "DASHSCOPE_ENDPOINT_MODE",
        ).lower()
        workspace_id = option(
            "workspace_id",
            "DASHSCOPE_WORKSPACE_ID",
        )
        if not endpoint_mode:
            endpoint_mode = "workspace" if workspace_id else "public"
        region = option("region", "DASHSCOPE_REGION")
        base_url = option("base_url", "DASHSCOPE_BASE_URL")
        endpoint_inputs = {
            "endpoint_mode": endpoint_mode,
            "region": region,
            "workspace_id": workspace_id,
            "base_url": base_url,
        }
        try:
            from agent.alibaba_endpoints import (
                DEFAULT_REGION,
                build_image_generation_url,
            )

            endpoint = build_image_generation_url(
                endpoint_mode=endpoint_mode,
                workspace_prefix=workspace_id,
                region=region or DEFAULT_REGION,
                custom_url=base_url,
            )
            endpoint_resolved = True
        except (ImportError, ValueError):
            endpoint = ""
            endpoint_resolved = False
        return {
            "transport": transport,
            "endpoint": endpoint,
            "identity_supported": True,
            "endpoint_mode": endpoint_mode,
            "region": region,
            "workspace_id": workspace_id,
            "base_url": base_url,
            "endpoint_resolved": endpoint_resolved,
            "invalid_endpoint_digest": (
                ""
                if endpoint_resolved
                else hashlib.sha256(
                    json.dumps(
                        endpoint_inputs,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            ),
        }

    endpoint = str(builtin_contract.get("endpoint") or "")
    provider_options = cfg.get(normalized_provider)
    if not isinstance(provider_options, dict):
        provider_options = {}
    return {
        "transport": transport,
        "endpoint": endpoint,
        "identity_supported": bool(transport and endpoint),
        "endpoint_resolved": bool(endpoint or transport),
        "unsupported_config_digest": (
            ""
            if transport and endpoint
            else hashlib.sha256(
                json.dumps(
                    provider_options,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        ),
    }


def _runtime_fingerprint_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields that can change the selected runtime call path."""
    return {
        "transport": str(identity.get("transport") or ""),
        "endpoint": str(identity.get("endpoint") or ""),
        "identity_supported": bool(identity.get("identity_supported")),
        "endpoint_mode": str(identity.get("endpoint_mode") or ""),
        "endpoint_resolved": bool(identity.get("endpoint_resolved")),
        "invalid_endpoint_digest": str(
            identity.get("invalid_endpoint_digest") or ""
        ),
        "unsupported_config_digest": str(
            identity.get("unsupported_config_digest") or ""
        ),
    }


def image_gen_runtime_config_resolved(identity: dict[str, Any]) -> bool:
    """Only identities that match an implemented runtime call path are resolved."""
    return bool(
        identity.get("identity_supported")
        and identity.get("endpoint_resolved")
    )


def resolve_image_gen_material(
    image_cfg: dict[str, Any],
    *,
    config_data: dict[str, Any],
) -> ImageGenResolvedMaterial:
    """Capture and expand the complete image config from one env snapshot."""
    raw_data = config_data if isinstance(config_data, dict) else {}
    raw_cfg = image_cfg if isinstance(image_cfg, dict) else {}
    combined = {
        "config_data": raw_data,
        "image_cfg": raw_cfg,
    }
    expansion_ok = True
    try:
        from hermes_cli.config import (
            _expand_env_vars,
            _referenced_env_snapshot,
        )

        capture_source = {
            "material": combined,
            "implicit_runtime_env": [
                f"${{{name}}}" for name in _DASHSCOPE_RUNTIME_ENV
            ],
        }
        env_snapshot = _referenced_env_snapshot(capture_source)
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
    expanded_cfg = expanded.get("image_cfg")
    if not isinstance(expanded_cfg, dict):
        expanded_cfg = {}
        expansion_ok = False
    data_resolved = bool(
        expansion_ok and not _contains_unresolved_env(expanded_data)
    )
    cfg_resolved = bool(
        expansion_ok and not _contains_unresolved_env(expanded_cfg)
    )
    provider = str(expanded_cfg.get("provider") or "").strip().lower()
    runtime_identity = image_gen_runtime_identity(
        provider,
        expanded_cfg,
        config_data=expanded_data,
        config_is_expanded=True,
        env_snapshot=env_snapshot if expansion_ok else None,
    )
    effective_config_resolved = bool(
        data_resolved
        and cfg_resolved
        and image_gen_runtime_config_resolved(runtime_identity)
    )
    return ImageGenResolvedMaterial(
        config_data=expanded_data,
        image_cfg=expanded_cfg,
        data_resolved=data_resolved,
        cfg_resolved=cfg_resolved,
        provider=provider,
        runtime_identity=runtime_identity,
        effective_config_resolved=effective_config_resolved,
    )


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
        return str(active_custom_provider_identity(provider, config_data).get("secret_env") or "")
    return IMAGE_GEN_KEY_ENV.get(provider, "")


def image_gen_secret_value(
    provider: str,
    credential_ref: str,
    config_data: dict[str, Any],
    *,
    config_path: Path | None = None,
    allow_process_fallback: bool | None = None,
) -> str:
    """Resolve the exact secret value the selected image Provider will use."""
    normalized_provider = str(provider or "").strip().lower()
    data = config_data if isinstance(config_data, dict) else {}
    ref = str(credential_ref or "").strip()
    if normalized_provider.startswith("custom:"):
        custom_identity = active_custom_provider_identity(
            normalized_provider,
            data,
        )
        if not custom_identity:
            return ""
        ref = str(custom_identity.get("credential_ref") or "").strip()
        if not ref:
            secret_env = str(custom_identity.get("secret_env") or "").strip()
            if not secret_env:
                return ""
            if config_path is not None:
                try:
                    from agent.provider_credentials import (
                        load_credential_snapshot,
                    )

                    credential_snapshot = load_credential_snapshot(config_path)
                except Exception:
                    return ""
                if (
                    credential_snapshot.config_exists
                    or credential_snapshot.env_exists
                ):
                    return str(
                        credential_snapshot.env.get(secret_env) or ""
                    ).strip()
            from agent.provider_credentials import (
                process_env_fallback_allowed,
            )

            if not process_env_fallback_allowed(
                allow_process_fallback
            ):
                return ""
            return str(os.getenv(secret_env) or "").strip()
        resolver_provider = "custom"
    elif normalized_provider in VERIFIABLE_BUILTIN_IMAGE_PROVIDERS:
        resolver_provider = normalized_provider
    else:
        secret_env = IMAGE_GEN_KEY_ENV.get(normalized_provider, "")
        return str(os.getenv(secret_env) or "").strip() if secret_env else ""

    try:
        from agent.provider_credentials import resolve_api_key

        return str(
            resolve_api_key(
                resolver_provider,
                ref,
                config_data=data,
                config_path=config_path,
                allow_process_fallback=allow_process_fallback,
            )
            or ""
        ).strip()
    except (ImportError, ValueError):
        return ""


def image_gen_fingerprint_from_material(
    resolved: ImageGenResolvedMaterial,
    *,
    profile: str,
    secret_value: str,
) -> str:
    """Hash one already-resolved image runtime material."""
    expanded_data = resolved.config_data
    expanded_cfg = resolved.image_cfg
    provider = resolved.provider
    custom_identity = active_custom_provider_identity(provider, expanded_data)
    try:
        from agent.provider_credentials import provider_family

        canonical_provider_family = provider_family(provider)
    except Exception:
        canonical_provider_family = provider
    material = {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "capability": "image_generation",
        "effective_config_resolved": resolved.effective_config_resolved,
        "profile": profile,
        "provider": provider,
        "provider_family": canonical_provider_family,
        "model": str(expanded_cfg.get("model") or "").strip(),
        "credential_ref": str(expanded_cfg.get("credential_ref") or "").strip(),
        "runtime_identity": _runtime_fingerprint_identity(
            resolved.runtime_identity
        ),
        "custom_provider": custom_identity,
        "key_digest": hashlib.sha256(secret_value.encode("utf-8")).hexdigest()
        if secret_value
        else "",
    }
    profile_incarnation = capability_profile_incarnation(expanded_data)
    if profile_incarnation:
        material["profile_incarnation"] = profile_incarnation
    config_epoch = capability_config_epoch(
        expanded_data,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    )
    if config_epoch:
        material["config_epoch"] = config_epoch
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def image_gen_fingerprint(
    image_cfg: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any],
    secret_value: str,
) -> str:
    resolved = resolve_image_gen_material(
        image_cfg,
        config_data=config_data,
    )
    return image_gen_fingerprint_from_material(
        resolved,
        profile=profile,
        secret_value=secret_value,
    )


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
    schema_version = state.get("schema_version") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or type(schema_version) is not int
        or schema_version != CAPABILITY_VERIFICATION_SCHEMA_VERSION
        or str(state.get("fingerprint") or "") != expected_fingerprint
    ):
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
    return str(
        read_image_gen_verification_snapshot(
            image_cfg,
            profile=profile,
            config_data=config_data,
            secret_value=secret_value,
            state_root=state_root,
        ).get("status")
        or "configured_unverified"
    )


def read_image_gen_verification_snapshot(
    image_cfg: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any],
    secret_value: str,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Return the versioned identity used by readiness, cache, and call gates."""
    resolved = resolve_image_gen_material(
        image_cfg,
        config_data=config_data,
    )
    expected = image_gen_fingerprint_from_material(
        resolved,
        profile=profile,
        secret_value=secret_value,
    )
    try:
        state = json.loads(
            verification_state_path(state_root, profile).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        state = {}
    effective_config_resolved = resolved.effective_config_resolved
    status = verification_status_from_state(
        state,
        expected_fingerprint=expected,
    )
    if not image_gen_provider_target(resolved.provider):
        status = "unconfigured"
    elif not effective_config_resolved:
        status = "configured_unverified"
    return {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "fingerprint": expected,
        "effective_config_resolved": effective_config_resolved,
        "status": status,
    }
