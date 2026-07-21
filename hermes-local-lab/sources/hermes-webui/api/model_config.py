"""Aggregated model configuration endpoints for Hermes WebUI.

This module keeps browser-driven model setup on the same config.yaml/.env
surface the Hermes CLI uses.  It deliberately returns credential status only,
never secret values.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # pragma: no cover - Windows uses a named mutex below.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows.
    _fcntl = None

from agent.provider_credentials import (
    credential_transaction,
    credential_secret_env,
    default_credential_ref,
    load_credential,
    load_credential_config,
    load_credential_snapshot,
    mutate_config_env_strict,
    normalize_credential_id,
    provider_family,
    resolve_api_key,
    resolve_secret_env_value,
)
from agent.auxiliary_client import (
    VisionRequestBinding,
    authorize_vision_request_binding,
)
from plugins.image_gen.domestic_common import credential_field, normalized_setup_contract
from agent.alibaba_endpoints import build_vision_base_url
from agent.image_gen_verification import (
    CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    CAPABILITY_CONFIG_EPOCH_VISION,
    CAPABILITY_VERIFICATION_SCHEMA_VERSION,
    ImageGenRequestBinding,
    active_custom_provider_identity,
    authorize_image_gen_request_binding,
    bump_capability_config_epochs,
    build_image_gen_request_reauth_guard,
    capability_epochs_for_secret_env,
    expand_effective_config,
    image_gen_fingerprint as shared_image_gen_fingerprint,
    image_gen_fingerprint_from_material as shared_image_gen_fingerprint_from_material,
    image_gen_provider_target,
    image_gen_secret_value as shared_image_gen_secret_value,
    resolve_image_gen_material,
    verification_state_path,
    verification_status_from_state,
)
from agent.image_runtime import (
    VisionResolvedMaterial,
    resolve_vision_material,
    verification_authorization_generation,
    vision_fingerprint,
    vision_fingerprint_from_material,
)

from api.config import (
    _cfg_lock,
    _fsync_parent_directory,
    _get_config_path,
    get_auxiliary_models,
    invalidate_models_cache,
    reload_config,
)
from api.providers import (
    _OAUTH_PROVIDERS,
    _PROVIDER_DISPLAY,
    _PROVIDER_ENV_VAR,
    _get_hermes_home,
    _load_env_file,
    _provider_has_key,
    _provider_is_oauth,
    get_providers,
)

logger = logging.getLogger(__name__)

_DURABLE_MUTATION_REFRESH_WARNING = "durable_mutation_refresh_pending"
_UNSET_SECRET_VALUE = object()
_IMAGE_CAPABILITY_REVISION_NONCE_KEY = (
    "_taiji_image_capability_revision_nonce"
)
_IMAGE_CAPABILITY_REQUEST_CACHE_TTL_SECONDS = 10 * 60
_IMAGE_CAPABILITY_REQUEST_CACHE_CAPACITY = 512
_IMAGE_CAPABILITY_CREDENTIAL_MANAGER = "image-capability-center"


class ImageCapabilityCredentialError(ValueError):
    """A stable, machine-readable image-center credential safety error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _ImageCapabilityCachedError:
    """Secret-free replay descriptor; never retains an exception traceback."""

    kind: str
    code: str
    message: str


@dataclass
class _ImageCapabilityRequestEntry:
    payload_digest: str
    event: threading.Event = dataclass_field(
        default_factory=threading.Event
    )
    result: dict[str, Any] | None = None
    error: _ImageCapabilityCachedError | None = None
    touched_at: float = dataclass_field(default_factory=time.monotonic)


_IMAGE_CAPABILITY_REQUEST_LOCK = threading.RLock()
_IMAGE_CAPABILITY_REQUESTS: "OrderedDict[tuple[str, str], _ImageCapabilityRequestEntry]" = (
    OrderedDict()
)
_IMAGE_CAPABILITY_PROBE_LOCKS_GUARD = threading.Lock()
_IMAGE_CAPABILITY_PROBE_LOCKS: dict[
    tuple[str, str],
    Any,
] = {}


@dataclass(frozen=True)
class _VerificationInvalidationToken:
    """Ownership proof for replacing one capability state with a tombstone."""

    capability: str
    profile: str
    generation: int
    state_identity: str


def _deduplicated_warnings(warnings: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in warnings if str(item)))


def _required_invalidation_token(
    token: _VerificationInvalidationToken | None,
    *,
    capability: str,
) -> _VerificationInvalidationToken:
    if token is None or token.capability != capability:
        raise RuntimeError(
            f"missing {capability} verification invalidation token"
        )
    return token


def _run_durable_mutation_post_commit_hook(
    mutation: str,
    *,
    invalidate_vision: bool = False,
    invalidate_image: bool = False,
    vision_invalidation_token: _VerificationInvalidationToken | None = None,
    image_invalidation_token: _VerificationInvalidationToken | None = None,
) -> list[str]:
    """Refresh process-local capability state after one durable mutation.

    The caller must invoke this only after every persistence/profile lock has
    been released.  Each action is best-effort because the durable file/env or
    verification-state commit is already authoritative at this boundary.
    """
    actions: list[tuple[str, Any]] = [
        ("runtime_config_refresh_pending", reload_config),
        ("models_cache_refresh_pending", invalidate_models_cache),
    ]
    if invalidate_vision:
        actions.append(
            (
                "vision_verification_refresh_pending",
                lambda: _invalidate_vision_verification(
                    _required_invalidation_token(
                        vision_invalidation_token,
                        capability="vision",
                    )
                ),
            )
        )
    if invalidate_image:
        actions.append(
            (
                "image_gen_verification_refresh_pending",
                lambda: _invalidate_image_gen_verification(
                    _required_invalidation_token(
                        image_invalidation_token,
                        capability="image",
                    )
                ),
            )
        )

    warnings: list[str] = []
    for warning, action in actions:
        try:
            action()
        except Exception:
            logger.warning(
                "Durable model mutation post-commit refresh failed "
                "(mutation=%s, warning=%s)",
                mutation,
                warning,
                exc_info=True,
            )
            warnings.append(warning)
    return _deduplicated_warnings(warnings)


def _invoke_durable_mutation_post_commit(
    mutation: str,
    *,
    invalidate_vision: bool = False,
    invalidate_image: bool = False,
    vision_invalidation_token: _VerificationInvalidationToken | None = None,
    image_invalidation_token: _VerificationInvalidationToken | None = None,
) -> list[str]:
    """Invoke the single post-commit seam without reclassifying a saved write."""
    try:
        warnings = _run_durable_mutation_post_commit_hook(
            mutation,
            invalidate_vision=invalidate_vision,
            invalidate_image=invalidate_image,
            vision_invalidation_token=vision_invalidation_token,
            image_invalidation_token=image_invalidation_token,
        )
    except Exception:
        logger.warning(
            "Durable model mutation post-commit hook failed (mutation=%s)",
            mutation,
            exc_info=True,
        )
        return [_DURABLE_MUTATION_REFRESH_WARNING]
    return _deduplicated_warnings(list(warnings or []))


def _merge_post_commit_warnings(
    response: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    existing = response.get("warnings")
    combined = _deduplicated_warnings(
        [
            *(existing if isinstance(existing, list) else []),
            *warnings,
        ]
    )
    if combined:
        response["refresh_pending"] = True
        response["warnings"] = combined
    elif "refresh_pending" in response or "warnings" in response:
        response["refresh_pending"] = False
        response["warnings"] = []
    return response


def _project_successful_verification_invalidation(
    response: dict[str, Any],
    warnings: list[str],
    *,
    capability: str,
) -> dict[str, Any]:
    """Project a successful state-file invalidation onto a frozen response."""
    if capability == "vision":
        section = "vision"
        warning = "vision_verification_refresh_pending"
        message = "识图配置已保存，但尚未通过真实图片验证。"
    elif capability == "image":
        section = "image_gen"
        warning = "image_gen_verification_refresh_pending"
        message = "生图配置已保存，但尚未通过真实生成验证。"
    else:
        raise ValueError(f"unsupported verification capability: {capability}")
    if warning in warnings or _DURABLE_MUTATION_REFRESH_WARNING in warnings:
        return response
    capability_projection = response.get(section)
    if not isinstance(capability_projection, dict):
        return response
    verification = capability_projection.get("verification")
    if (
        not isinstance(verification, dict)
        or verification.get("status")
        not in {"verifying", "verified", "failed"}
    ):
        return response
    frozen = copy.deepcopy(response)
    frozen[section]["verification"] = {
        "status": "configured_unverified",
        "checked_at": "",
        "error_code": "",
        "message": message,
        "diagnostic_id": "",
    }
    return frozen


_CUSTOM_MODEL_KEY_ENV = "HERMES_CUSTOM_MODEL_API_KEY"
_IMAGE_GEN_KEY_ENV: dict[str, str] = {
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
_VISION_KEY_ENV: dict[str, str] = {
    "alibaba": "DASHSCOPE_API_KEY",
    "zai": "GLM_API_KEY",
    "custom": "AUXILIARY_VISION_API_KEY",
}
_ALIBABA_QUICK_CREDENTIAL_ID = "taiji-alibaba-quick"
_ALIBABA_QUICK_CREDENTIAL_ENV = credential_secret_env(
    _ALIBABA_QUICK_CREDENTIAL_ID
)
_ALIBABA_QUICK_CREDENTIAL_ROW = {
    "id": _ALIBABA_QUICK_CREDENTIAL_ID,
    "provider_family": "alibaba_dashscope",
    "label": "阿里百炼快速配置",
    "auth_type": "api_key",
    "secret_env": _ALIBABA_QUICK_CREDENTIAL_ENV,
}
_VISION_PROVIDER_META: dict[str, dict[str, Any]] = {
    "alibaba": {
        "name": "阿里百炼 Qwen-VL",
        "description": "用于上传图片、截图和表格截图理解",
        "auth_type": "api_key",
        "transport": "dashscope_openai_compatible",
        "endpoint_fields": [
            {
                "name": "endpoint_mode",
                "label": "接入方式",
                "required": True,
                "secret": False,
                "options": [
                    {"value": "public", "label": "公共端点"},
                    {"value": "workspace", "label": "业务空间专属端点"},
                    {"value": "custom", "label": "自定义 Base URL"},
                ],
                "description": "选择公共端点、业务空间端点或自定义 Base URL。",
            },
            {
                "name": "region",
                "label": "地域",
                "required": True,
                "secret": False,
                "options": [
                    {"value": "cn-beijing", "label": "华北 2（北京）"},
                    {"value": "ap-southeast-1", "label": "新加坡"},
                ],
                "description": "必须与百炼业务空间所在地域一致。",
            },
            {"name": "workspace_id", "label": "Workspace ID", "required": False, "secret": False, "placeholder": "例如：llm-demo", "description": "仅业务空间专属端点需要。"},
            {"name": "base_url", "label": "Base URL", "required": False, "secret": False, "type": "url", "placeholder": "https://api.example.com/v1", "description": "仅自定义接入方式需要。"},
        ],
        "default_model": "qwen3-vl-plus",
        "models": [
            {"id": "qwen3-vl-plus", "label": "Qwen3 VL Plus"},
            {"id": "qwen3-vl-flash", "label": "Qwen3 VL Flash"},
            {"id": "qwen2.5-vl-72b-instruct", "label": "Qwen2.5 VL 72B"},
        ],
    },
    "zai": {
        "name": "智谱 GLM-V",
        "description": "用于图片理解和视觉问答",
        "auth_type": "api_key",
        "transport": "zhipu_openai_compatible",
        "default_model": "glm-5v-turbo",
        "models": [
            {"id": "glm-5v-turbo", "label": "GLM-5V Turbo"},
            {"id": "glm-4v-plus", "label": "GLM-4V Plus"},
            {"id": "glm-4v-flash", "label": "GLM-4V Flash"},
        ],
    },
    "custom": {
        "name": "自定义国产兼容端点",
        "description": "适合私有化或 OpenAI 兼容视觉接口",
        "auth_type": "api_key",
        "transport": "openai_chat_completions",
        "default_model": "",
        "models": [],
        "allow_custom_model_id": True,
        "requires_base_url": True,
        "endpoint_fields": [
            {"name": "base_url", "label": "Base URL", "required": True, "secret": False, "type": "url", "placeholder": "https://api.example.com/v1", "description": "OpenAI 兼容视图端点。"}
        ],
    },
}


def _validate_provider_model_choice(
    provider_id: str,
    model_id: str,
    provider_meta: dict[str, Any],
    *,
    capability: str,
) -> str:
    """Validate a saved model against the provider's explicit model contract."""
    allowed_models = {
        str(row.get("id") or "").strip()
        for row in (provider_meta.get("models") or [])
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    if model_id in allowed_models or (
        model_id and provider_meta.get("allow_custom_model_id") is True
    ):
        return model_id
    if capability == "vision" and provider_id == "alibaba":
        raise ValueError(f"unknown Alibaba vision model: {model_id}")
    if capability == "vision" and provider_id.startswith("custom:"):
        raise ValueError(f"unknown custom vision model: {model_id}")
    if capability == "image generation" and provider_id.startswith("custom:"):
        raise ValueError(f"unknown custom image model: {model_id}")
    raise ValueError(f"unknown {capability} model for {provider_id}: {model_id}")


def _validate_provider_credential_secret_env(row: dict[str, Any]) -> str:
    expected = credential_secret_env(row.get("id"))
    actual = str(row.get("secret_env") or "").strip()
    if actual != expected:
        raise ValueError("凭据的 Secret 环境变量配置无效。")
    return actual


def _provider_credential_row(
    config_data: dict[str, Any], credential_id: str
) -> tuple[int, dict[str, Any] | None]:
    rows = config_data.get("provider_credentials")
    if not isinstance(rows, list):
        return -1, None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            row_id = normalize_credential_id(row.get("id"))
        except ValueError:
            continue
        if row_id == credential_id:
            return index, row
    return -1, None


def _replace_provider_credential_row(
    config_data: dict[str, Any],
    credential_id: str,
    replacement: dict[str, Any] | None,
    *,
    preferred_index: int = -1,
) -> None:
    existing = config_data.get("provider_credentials")
    rows = list(existing) if isinstance(existing, list) else []
    updated: list[Any] = []
    found_index = -1
    for row in rows:
        if not isinstance(row, dict):
            updated.append(row)
            continue
        try:
            row_id = normalize_credential_id(row.get("id"))
        except ValueError:
            updated.append(row)
            continue
        if row_id == credential_id:
            if found_index < 0:
                found_index = len(updated)
            continue
        updated.append(row)
    if replacement is not None:
        insert_at = found_index if found_index >= 0 else preferred_index
        if insert_at < 0 or insert_at > len(updated):
            insert_at = len(updated)
        updated.insert(insert_at, replacement)
    config_data["provider_credentials"] = updated


def _commit_expected_config_env(
    config_path: Path,
    *,
    expected_config: dict[str, Any],
    desired_config: dict[str, Any],
    env_updates: dict[str, str | None],
) -> None:
    """Commit one config/.env pair without overwriting a newer config state."""

    def replace_expected(current: dict[str, Any]) -> None:
        if current != expected_config:
            raise RuntimeError(
                "credential config changed before the paired update"
            )
        current.clear()
        current.update(copy.deepcopy(desired_config))

    mutate_config_env_strict(
        replace_expected,
        env_updates,
        config_path=config_path,
    )


def _image_capability_revision(
    config_data: dict[str, Any],
    *,
    env_sha256: str = hashlib.sha256(b"").hexdigest(),
) -> str:
    """Return an opaque digest of durable config and credential state."""
    projection = {
        "config": config_data,
        "env_sha256": str(env_sha256 or ""),
    }
    serialized = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _image_capability_request_digest(body: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in body.items()
        if key != "request_id"
    }
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _cacheable_image_capability_error(
    exc: BaseException,
) -> _ImageCapabilityCachedError:
    """Reduce an owner failure to a fixed, secret-free replay description."""
    if isinstance(exc, ImageCapabilityCredentialError):
        messages = {
            "image_capability_credential_shared": (
                "该凭据已被多个图片能力引用，图片能力中心不会覆盖共享凭据。"
            ),
            "image_capability_credential_collision": (
                "凭据 ID 已存在且不属于当前图片能力草稿，请刷新后重试。"
            ),
        }
        code = (
            exc.code
            if exc.code in messages
            else "image_capability_credential_rejected"
        )
        return _ImageCapabilityCachedError(
            kind="credential",
            code=code,
            message=messages.get(
                code,
                "图片能力凭据更新被安全策略拒绝，请刷新后重试。",
            ),
        )

    from hermes_cli.config import ConfigurationConflictError

    if isinstance(exc, ConfigurationConflictError):
        return _ImageCapabilityCachedError(
            kind="configuration_conflict",
            code="configuration_conflict",
            message="配置已被其他请求更新，请刷新后重试。",
        )
    if isinstance(exc, ValueError):
        return _ImageCapabilityCachedError(
            kind="validation",
            code="image_capability_validation_failed",
            message="图片能力请求校验失败，请修正后重试。",
        )
    if isinstance(exc, OSError):
        return _ImageCapabilityCachedError(
            kind="io",
            code="image_capability_io_failed",
            message="图片能力配置写入失败，请检查本机文件状态后重试。",
        )
    return _ImageCapabilityCachedError(
        kind="internal",
        code="image_capability_internal_error",
        message="图片能力请求执行失败，请稍后重试。",
    )


def _replayed_image_capability_error(
    cached: _ImageCapabilityCachedError,
) -> BaseException:
    """Create a fresh exception so waiters cannot inherit owner traceback."""
    if cached.kind == "credential":
        return ImageCapabilityCredentialError(
            cached.code,
            cached.message,
        )
    if cached.kind == "configuration_conflict":
        from hermes_cli.config import ConfigurationConflictError

        return ConfigurationConflictError(
            ("image_capabilities", "revision")
        )
    if cached.kind == "validation":
        return ValueError(cached.message)
    if cached.kind == "io":
        return OSError(cached.message)
    return RuntimeError(cached.message)


def _prune_image_capability_requests(now: float) -> None:
    expired = [
        key
        for key, entry in _IMAGE_CAPABILITY_REQUESTS.items()
        if entry.event.is_set()
        and now - entry.touched_at
        > _IMAGE_CAPABILITY_REQUEST_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _IMAGE_CAPABILITY_REQUESTS.pop(key, None)
    while (
        len(_IMAGE_CAPABILITY_REQUESTS)
        >= _IMAGE_CAPABILITY_REQUEST_CACHE_CAPACITY
    ):
        evictable = next(
            (
                key
                for key, entry in _IMAGE_CAPABILITY_REQUESTS.items()
                if entry.event.is_set()
            ),
            None,
        )
        if evictable is None:
            break
        _IMAGE_CAPABILITY_REQUESTS.pop(evictable, None)


def _provider_credential_used_by(config_data: dict[str, Any], credential_id: str) -> list[str]:
    used_by: list[str] = []
    _target_index, target_row = _provider_credential_row(
        config_data,
        credential_id,
    )
    target_family = (
        provider_family(target_row.get("provider_family"))
        if isinstance(target_row, dict)
        else ""
    )
    target_is_default = bool(
        isinstance(target_row, dict) and target_row.get("default")
    )
    auxiliary = config_data.get("auxiliary")
    vision = auxiliary.get("vision") if isinstance(auxiliary, dict) else None
    image_gen = config_data.get("image_gen")
    for path, section in (("auxiliary.vision", vision), ("image_gen", image_gen)):
        if not isinstance(section, dict):
            continue
        raw_ref = section.get("credential_ref")
        ref = str(raw_ref or "").strip()
        if ref:
            try:
                ref = normalize_credential_id(ref)
            except ValueError:
                continue
        if ref == credential_id:
            used_by.append(path)
            continue
        provider = str(section.get("provider") or "").strip().lower()
        if not ref and provider.startswith("custom:"):
            custom_key = (
                "custom_vision_providers"
                if path == "auxiliary.vision"
                else "custom_image_providers"
            )
            requested_id = provider.split(":", 1)[1]
            entries = config_data.get(custom_key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or str(entry.get("id") or "").strip().lower()
                    != requested_id
                ):
                    continue
                try:
                    entry_ref = normalize_credential_id(
                        entry.get("credential_ref")
                    )
                except ValueError:
                    entry_ref = ""
                if entry_ref == credential_id:
                    used_by.append(path)
                break
            continue
        if (
            not ref
            and target_is_default
            and target_family
            and target_family != "custom"
            and provider_family(provider) == target_family
        ):
            used_by.append(path)
    return used_by


_CUSTOM_PROVIDER_CREDENTIAL_MANAGER = "hermes-webui"


def _write_custom_provider_transaction(
    *,
    config_path: Path,
    env_path: Path,
    config_data: dict[str, Any],
    env_updates: dict[str, str | None],
) -> None:
    """Commit custom-provider metadata and secret through one durable intent."""
    desired = copy.deepcopy(config_data)

    def replace(current: dict[str, Any]) -> None:
        current.clear()
        current.update(copy.deepcopy(desired))

    with credential_transaction(config_path) as credential_spec:
        if Path(env_path) != credential_spec.env_target:
            raise ValueError(
                "custom provider Secret path is not the pinned env target"
            )
        mutate_config_env_strict(
            replace,
            env_updates,
            config_path=credential_spec.config_target,
        )


def _custom_provider_credential_binding(
    config_data: dict[str, Any],
    *,
    requested_ref: str,
    capability: str,
    provider_id: str,
    provider_label: str,
) -> tuple[str, str]:
    credential_ref = str(requested_ref or "").strip()
    managed = not credential_ref
    if managed:
        credential_ref = normalize_credential_id(
            f"webui-{capability}-{provider_id}-{uuid.uuid4().hex[:8]}"
        )
    else:
        credential_ref = normalize_credential_id(credential_ref)

    previous_index, previous_row = _provider_credential_row(config_data, credential_ref)
    if previous_row is not None:
        if provider_family(previous_row.get("provider_family")) != "custom":
            raise ValueError("所选凭据不属于自定义 Provider。")
        if str(previous_row.get("auth_type") or "api_key").strip().lower() != "api_key":
            raise ValueError("自定义 Provider 凭据必须使用 API Key。")
        _validate_provider_credential_secret_env(previous_row)
        if previous_row.get("managed_by"):
            if (
                previous_row.get("managed_by") != _CUSTOM_PROVIDER_CREDENTIAL_MANAGER
                or previous_row.get("source_capability") != capability
                or previous_row.get("source_provider_id") != provider_id
            ):
                raise ValueError("所选托管凭据属于其他自定义 Provider。")
        stored = dict(previous_row)
    else:
        stored = {
            "id": credential_ref,
            "provider_family": "custom",
            "label": provider_label or provider_id,
            "auth_type": "api_key",
            "secret_env": credential_secret_env(credential_ref),
        }
        if managed:
            stored.update(
                {
                    "managed_by": _CUSTOM_PROVIDER_CREDENTIAL_MANAGER,
                    "source_capability": capability,
                    "source_provider_id": provider_id,
                }
            )
    stored.update(
        {
            "id": credential_ref,
            "provider_family": "custom",
            "auth_type": "api_key",
            "secret_env": credential_secret_env(credential_ref),
        }
    )
    _replace_provider_credential_row(
        config_data,
        credential_ref,
        stored,
        preferred_index=previous_index,
    )
    return credential_ref, credential_secret_env(credential_ref)


def _remove_orphaned_managed_custom_credential(
    config_data: dict[str, Any],
    *,
    credential_ref: str,
    capability: str,
    provider_id: str,
) -> str:
    try:
        normalized_ref = normalize_credential_id(credential_ref)
    except ValueError:
        return ""
    _index, row = _provider_credential_row(config_data, normalized_ref)
    if row is None:
        return ""
    if (
        row.get("managed_by") != _CUSTOM_PROVIDER_CREDENTIAL_MANAGER
        or row.get("source_capability") != capability
        or row.get("source_provider_id") != provider_id
        or _provider_credential_used_by(config_data, normalized_ref)
    ):
        return ""
    secret_env = _validate_provider_credential_secret_env(row)
    _replace_provider_credential_row(config_data, normalized_ref, None)
    return secret_env


def _public_provider_credential(
    row: dict[str, Any],
    *,
    config_data: dict[str, Any],
) -> dict[str, Any]:
    credential_id = normalize_credential_id(row.get("id"))
    family = provider_family(row.get("provider_family"))
    secret_env = _validate_provider_credential_secret_env(row)
    label = str(row.get("label") or "").strip() or credential_id
    return {
        "id": credential_id,
        "provider_family": family,
        "label": label,
        "auth_type": str(row.get("auth_type") or "api_key").strip(),
        "default": bool(row.get("default")),
        "configured": bool(secret_env and _key_status_for_env(secret_env).get("configured")),
        "used_by": _provider_credential_used_by(config_data, credential_id),
    }


def _public_provider_credentials_config(
    config_data: dict[str, Any],
) -> dict[str, Any]:
    rows = config_data.get("provider_credentials")
    credentials: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                credentials.append(
                    _public_provider_credential(
                        row,
                        config_data=config_data,
                    )
                )
            except ValueError:
                continue
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "credentials": credentials,
    }


def get_provider_credentials_config() -> dict[str, Any]:
    with credential_transaction(_get_config_path()):
        config_data = load_credential_config(_get_config_path())
        return _public_provider_credentials_config(config_data)


def upsert_provider_credential(body: dict[str, Any]) -> dict[str, Any]:
    credential_id = normalize_credential_id(body.get("id"))
    family = provider_family(body.get("provider_family") or body.get("provider"))
    if not family:
        raise ValueError("provider_family is required")
    auth_type = str(body.get("auth_type") or "api_key").strip().lower()
    if auth_type != "api_key":
        raise ValueError("only api_key credentials are supported")
    label = str(body.get("label") or "").strip() or credential_id
    secret_env = credential_secret_env(credential_id)
    secret = body.get("api_key")
    if secret is None:
        secret = body.get("secret")
    secret_value = str(secret or "").strip()
    requested_default = body.get("default") if "default" in body else None
    if requested_default is not None and not isinstance(requested_default, bool):
        raise ValueError("default must be a boolean")
    config_path = Path(_get_config_path())
    with credential_transaction(config_path):
        with _cfg_lock:
            config_data = load_credential_config(config_path)
        previous_index, previous_row = _provider_credential_row(config_data, credential_id)
        if previous_row is not None:
            _validate_provider_credential_secret_env(previous_row)
            previous_family = provider_family(previous_row.get("provider_family"))
            if previous_family != family:
                raise ValueError("已有凭据 ID 属于不同 Provider，请使用新的凭据 ID。")
        default_value = (
            bool(requested_default)
            if requested_default is not None
            else bool(previous_row and previous_row.get("default"))
        )
        if default_value:
            rows = config_data.get("provider_credentials")
            for row in (rows if isinstance(rows, list) else []):
                if not isinstance(row, dict) or not bool(row.get("default")):
                    continue
                try:
                    row_id = normalize_credential_id(row.get("id"))
                except ValueError:
                    continue
                if row_id != credential_id and provider_family(row.get("provider_family")) == family:
                    raise ValueError("当前 Provider 已有默认凭据，请先取消原默认凭据。")
        stored = {
            "id": credential_id,
            "provider_family": family,
            "label": label,
            "auth_type": auth_type,
            "secret_env": secret_env,
        }
        if previous_row is not None:
            for lifecycle_key in (
                "managed_by",
                "source_capability",
                "source_provider_id",
            ):
                if lifecycle_key in previous_row:
                    stored[lifecycle_key] = previous_row[lifecycle_key]
        if default_value:
            stored["default"] = True
        env_updates = (
            {secret_env: secret_value}
            if secret_value
            else {}
        )
        committed_config = copy.deepcopy(config_data)
        _replace_provider_credential_row(
            committed_config,
            credential_id,
            copy.deepcopy(stored),
            preferred_index=previous_index,
        )
        used_by_before = _provider_credential_used_by(
            config_data,
            credential_id,
        )
        used_by_after = _provider_credential_used_by(
            committed_config,
            credential_id,
        )
        authorization_fields = (
            "provider_family",
            "auth_type",
            "secret_env",
            "default",
        )
        authorization_metadata_changed = previous_row is None or any(
            previous_row.get(field) != stored.get(field)
            for field in authorization_fields
        )
        authorization_changed = bool(
            secret_value or authorization_metadata_changed
        )
        used_by_union = set(used_by_before) | set(used_by_after)
        invalidate_vision = bool(
            authorization_changed
            and (
                "auxiliary.vision" in used_by_union
                or any(
                    str(path).startswith("custom_vision_providers.")
                    for path in used_by_union
                )
            )
        )
        invalidate_image = bool(
            authorization_changed
            and (
                "image_gen" in used_by_union
                or any(
                    str(path).startswith("custom_image_providers.")
                    for path in used_by_union
                )
            )
        )
        capability_epochs = (
            *(
                (CAPABILITY_CONFIG_EPOCH_VISION,)
                if invalidate_vision
                else ()
            ),
            *(
                (CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,)
                if invalidate_image
                else ()
            ),
        )

        def update_metadata(latest: dict[str, Any]) -> None:
            _replace_provider_credential_row(
                latest,
                credential_id,
                copy.deepcopy(stored),
                preferred_index=previous_index,
            )
            bump_capability_config_epochs(
                latest,
                *capability_epochs,
            )

        mutate_config_env_strict(
            update_metadata,
            env_updates,
            config_path=config_path,
        )
        bump_capability_config_epochs(
            committed_config,
            *capability_epochs,
        )
        vision_invalidation_token = (
            _capture_vision_verification_invalidation()
            if invalidate_vision
            else None
        )
        image_invalidation_token = (
            _capture_image_gen_verification_invalidation()
            if invalidate_image
            else None
        )
        credential = _public_provider_credential(
            stored,
            config_data=committed_config,
        )
    warnings = _invoke_durable_mutation_post_commit(
        "upsert_provider_credential",
        invalidate_vision=invalidate_vision,
        invalidate_image=invalidate_image,
        vision_invalidation_token=vision_invalidation_token,
        image_invalidation_token=image_invalidation_token,
    )
    return _merge_post_commit_warnings(
        {"ok": True, "credential": credential},
        warnings,
    )


def delete_provider_credential(credential_id: str) -> dict[str, Any]:
    normalized = normalize_credential_id(credential_id)
    config_path = Path(_get_config_path())
    with credential_transaction(config_path):
        with _cfg_lock:
            config_data = load_credential_config(config_path)
        used_by = _provider_credential_used_by(config_data, normalized)
        if used_by:
            raise ValueError("凭据正在使用，不能删除。")
        _previous_index, previous_row = _provider_credential_row(config_data, normalized)
        if previous_row is None:
            raise ValueError("凭据不存在。")
        secret_env = _validate_provider_credential_secret_env(previous_row)

        def remove_metadata(latest: dict[str, Any]) -> None:
            _replace_provider_credential_row(latest, normalized, None)

        mutate_config_env_strict(
            remove_metadata,
            {secret_env: None},
            config_path=config_path,
        )
        committed_config = copy.deepcopy(config_data)
        _replace_provider_credential_row(
            committed_config,
            normalized,
            None,
        )
        response = _public_provider_credentials_config(committed_config)
    warnings = _invoke_durable_mutation_post_commit(
        "delete_provider_credential"
    )
    return _merge_post_commit_warnings(
        response,
        warnings,
    )


_VISION_PROBE_MARKER = "TAIJI-VISION-CHECK-7319"
_VISION_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAkAAAAA0CAAAAABH3dgUAAABPElEQVR42u3aYRKCIBAGUO9/6TpAyO4COYrv+1cpwvKasc3jIzKRQwkEIAFIABKARAASgAQgAUgEIAFIHgLoaKT1Web41jnR8T+Ta4xXmWf1+Oi60Vxb1101/8p8zsaPan/2Xm+vAAIIIIAAAmgHQNkCVhaUnei/51E5PwKVXU9v/NFxsrUbGXekvgABBBBAAAEE0F6AMht+NaCVeFbVFyCAAAIIIIB2BxQ1DGcA9Rpf5QUVGmWZDc40JHsbX2noReNkx19Vn1TzESCAAAIIIIA2BzT6+sqb6FXrmL3OHW6is38Az64TIIAAAggggABaDygq3tsAzXz5AAIIIIAAAgigdYAyD4dXGl+VhVYbdG96qH6kKZidC0AAAQQQQADtDEgk/SNHCQQgAUgAEoBEABKABCABSAQgAUjumy803ZPAu+g+xgAAAABJRU5ErkJggg=="
)
_VISION_VERIFICATION_PUBLIC_FIELDS = {
    "ok",
    "status",
    "checked_at",
    "provider",
    "model",
    "error_code",
    "message",
    "diagnostic_id",
}
_IMAGE_GEN_VERIFICATION_PUBLIC_FIELDS = _VISION_VERIFICATION_PUBLIC_FIELDS
_IMAGE_GEN_PROBE_PROMPT = "生成一张简洁的蓝色几何图形测试图，不包含人物、文字或品牌。"
_IMAGE_GEN_FALLBACK_META: dict[str, dict[str, Any]] = {
    "doubao": {
        "name": "Doubao Seedream",
        "models": [
            {"id": "doubao-seedream-5-0-260128", "label": "Doubao Seedream 5.0 Lite"},
            {"id": "doubao-seedream-5-0-lite-260128", "label": "Doubao Seedream 5.0 Lite (alias)"},
        ],
        "default_model": "doubao-seedream-5-0-260128",
    },
    "dashscope": {
        "name": "通义 Qwen-Image",
        "models": [
            {"id": "qwen-image-2.0-pro", "label": "Qwen Image 2.0 Pro"},
            {"id": "qwen-image", "label": "Qwen Image"},
        ],
        "default_model": "qwen-image-2.0-pro",
    },
    "qianfan": {
        "name": "百度千帆",
        "models": [{"id": "qwen-image", "label": "Qwen Image"}],
        "default_model": "qwen-image",
    },
    "zhipu-image": {
        "name": "智谱 GLM-Image",
        "models": [
            {"id": "glm-image", "label": "GLM-Image"},
            {"id": "cogview-4", "label": "CogView-4"},
        ],
        "default_model": "glm-image",
    },
    "minimax-image": {
        "name": "MiniMax Image",
        "models": [{"id": "image-01", "label": "MiniMax Image-01"}],
        "default_model": "image-01",
    },
}
_DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS = {
    "doubao",
    "dashscope",
    "qianfan",
    "zhipu-image",
    "minimax-image",
}
_BLOCKED_IMAGE_GEN_PROVIDER_LABELS = {
    "fal": "FAL",
    "openai": "OpenAI",
    "openai-codex": "OpenAI 图像生成",
    "xai": "xAI",
    "krea": "Krea",
    "taiji-image": "OpenAI 图像生成",
}
_IMAGE_GEN_PUBLIC_PROVIDER_IDS = {
    "openai-codex": "taiji-image",
}
_IMAGE_GEN_INTERNAL_PROVIDER_IDS = {
    public_id: internal_id
    for internal_id, public_id in _IMAGE_GEN_PUBLIC_PROVIDER_IDS.items()
}
_BUILTIN_IMAGE_GEN_MODULES: tuple[str, ...] = (
    "plugins.image_gen.doubao",
    "plugins.image_gen.dashscope",
    "plugins.image_gen.qianfan",
    "plugins.image_gen.zhipu_image",
    "plugins.image_gen.minimax_image",
    "plugins.image_gen.fal",
    "plugins.image_gen.openai",
    "plugins.image_gen.openai-codex",
    "plugins.image_gen.xai",
    "plugins.image_gen.krea",
)


def _active_profile_name() -> str:
    try:
        from api.profiles import get_active_profile_name

        profile = get_active_profile_name()
        return str(profile or "default")
    except Exception:
        return "default"


def _public_config_summary(config_path: Any) -> dict[str, Any]:
    try:
        exists = bool(config_path.exists())
    except AttributeError:
        exists = os.path.exists(str(config_path))
    return {
        "label": "本机配置",
        "exists": exists,
        "source": "active_profile",
    }


def _safe_model_cfg(config_data: dict[str, Any]) -> dict[str, Any]:
    model_cfg = config_data.get("model")
    return model_cfg if isinstance(model_cfg, dict) else {}


def _key_status_for_env(
    env_var: str | None,
    *,
    env_path: Path | None = None,
) -> dict[str, Any]:
    if not env_var:
        return {"configured": False, "source": "none", "env_var": ""}
    target_env_path = Path(env_path) if env_path is not None else _get_hermes_home() / ".env"
    env_values = _load_env_file(target_env_path)
    if str(env_values.get(env_var) or "").strip():
        return {"configured": True, "source": "env_file", "env_var": env_var}
    if str(os.getenv(env_var) or "").strip():
        return {"configured": True, "source": "env_var", "env_var": env_var}
    return {"configured": False, "source": "none", "env_var": env_var}


def _provider_key_status(provider_id: str) -> dict[str, Any]:
    provider = (provider_id or "").strip().lower()
    if not provider:
        return {"configured": False, "source": "none", "env_var": ""}
    env_var = _PROVIDER_ENV_VAR.get(provider)
    status = _key_status_for_env(env_var)
    if status.get("configured"):
        return status
    if _provider_has_key(provider):
        return {
            "configured": True,
            "source": "oauth" if _provider_is_oauth(provider) else "config_yaml",
            "env_var": env_var or "",
        }
    return {
        "configured": False,
        "source": "oauth" if _provider_is_oauth(provider) else "none",
        "env_var": env_var or "",
    }


class _ImageGenRegisterContext:
    def register_image_gen_provider(self, provider: Any) -> None:
        from agent.image_gen_registry import register_provider

        register_provider(provider)


def _ensure_image_gen_plugins_registered(
    *,
    include_custom: bool = True,
) -> None:
    """Load bundled image_gen plugins so the registry can expose catalogs."""
    from agent import image_gen_registry

    registered = {provider.name for provider in image_gen_registry.list_providers()}
    expected = {
        "doubao",
        "dashscope",
        "qianfan",
        "zhipu-image",
        "minimax-image",
        "fal",
        "openai",
        "openai-codex",
        "xai",
        "krea",
    }
    if not expected.issubset(registered):
        ctx = _ImageGenRegisterContext()
        for module_name in _BUILTIN_IMAGE_GEN_MODULES:
            try:
                module = importlib.import_module(module_name)
                register = getattr(module, "register", None)
                if callable(register):
                    register(ctx)
            except Exception:
                logger.debug("Failed to register image_gen plugin %s", module_name, exc_info=True)
    if not include_custom:
        return
    try:
        from agent.custom_image_providers import register_configured_custom_image_providers

        register_configured_custom_image_providers()
    except Exception:
        logger.debug("Failed to register custom image providers", exc_info=True)


def _public_image_gen_provider_id(provider_id: str) -> str:
    provider = str(provider_id or "").strip().lower()
    return _IMAGE_GEN_PUBLIC_PROVIDER_IDS.get(provider, provider)


def _internal_image_gen_provider_id(provider_id: str) -> str:
    provider = str(provider_id or "").strip().lower()
    return _IMAGE_GEN_INTERNAL_PROVIDER_IDS.get(provider, provider)


def _image_gen_provider_model_contract(
    requested_provider_id: str,
    *,
    config_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the save-time model contract without reading secrets or readiness."""
    public_id = str(requested_provider_id or "").strip().lower()
    provider_id = _internal_image_gen_provider_id(public_id)
    if (
        public_id in _BLOCKED_IMAGE_GEN_PROVIDER_LABELS
        or provider_id in _BLOCKED_IMAGE_GEN_PROVIDER_LABELS
    ):
        raise ValueError(
            "生成图片主配置只支持中国可用的稳定 Provider，请切换到国产生图服务。"
        )
    if provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS:
        fallback = _IMAGE_GEN_FALLBACK_META.get(provider_id) or {}
        return {
            "id": public_id,
            "models": list(fallback.get("models") or []),
            "default_model": str(fallback.get("default_model") or "").strip(),
            "allow_custom_model_id": False,
            "custom": False,
            "domestic": True,
            "integration_status": "stable",
        }
    if public_id.startswith("custom:"):
        try:
            from agent.custom_image_providers import (
                custom_image_provider_name,
                load_custom_image_provider_entries,
            )
        except ImportError as exc:
            raise ValueError(
                f"unknown image generation provider: {public_id}"
            ) from exc
        data = (
            config_data
            if isinstance(config_data, dict)
            else load_credential_config(_get_config_path())
        )
        entry = next(
            (
                item
                for item in load_custom_image_provider_entries(data)
                if custom_image_provider_name(item.get("id")) == public_id
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"unknown image generation provider: {public_id}")
        return {
            "id": public_id,
            "models": [{"id": item, "label": item} for item in entry["models"]],
            "default_model": str(entry.get("default_model") or "").strip(),
            "allow_custom_model_id": (
                entry.get("allow_custom_model_id") is True
            ),
            "custom": True,
            "domestic": False,
            "integration_status": "custom",
        }
    raise ValueError(f"unknown image generation provider: {public_id}")


def _image_gen_credential_fields(
    *,
    schema: dict[str, Any],
    env_var: str,
    env_vars: list[str],
) -> list[dict[str, Any]]:
    raw_fields = schema.get("credential_fields")
    fields: list[dict[str, Any]] = []
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            field_env = str(item.get("env_var") or item.get("key") or "").strip()
            field_name = str(item.get("name") or field_env.lower() or "").strip()
            if not field_name and not field_env:
                continue
            normalized = dict(item)
            normalized.update(
                {
                    "name": field_name or field_env,
                    "env_var": field_env,
                    "label": str(item.get("label") or item.get("prompt") or field_env or field_name),
                    "required": bool(item.get("required", True)),
                    "secret": bool(item.get("secret", True)),
                    "placeholder": str(item.get("placeholder") or ""),
                }
            )
            fields.append(normalized)
    if fields:
        return fields
    keys = env_vars or ([env_var] if env_var else [])
    for key in keys:
        if not key:
            continue
        fields.append(
            {
                "name": "api_key" if key.endswith("_API_KEY") or key.endswith("_KEY") else key.lower(),
                "env_var": key,
                "label": "API 密钥" if key.endswith("_API_KEY") or key.endswith("_KEY") else key,
                "required": True,
                "secret": True,
                "placeholder": "留空保留现有密钥",
            }
        )
    return fields


def _image_gen_options_for_active(active_provider: str) -> dict[str, Any]:
    try:
        config_data = load_credential_config(_get_config_path())
    except Exception:
        return {}
    image_cfg = config_data.get("image_gen") if isinstance(config_data, dict) else None
    if not isinstance(image_cfg, dict):
        return {}
    if str(image_cfg.get("provider") or "").strip().lower() != str(active_provider or "").strip().lower():
        return {}
    options = image_cfg.get("options")
    return options if isinstance(options, dict) else {}


def _image_gen_credential_status(
    fields: list[dict[str, Any]],
    *,
    active_options: dict[str, Any] | None = None,
    secret_status_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    statuses: list[dict[str, Any]] = []
    missing: list[str] = []
    options = active_options or {}
    for field in fields:
        env_var = str(field.get("env_var") or "").strip()
        name = str(field.get("name") or env_var or "").strip()
        required = bool(field.get("required", True))
        secret = bool(field.get("secret", True))
        status = _key_status_for_env(env_var) if env_var else {"configured": False, "source": "none", "env_var": ""}
        if secret and secret_status_override is not None:
            status = secret_status_override
        if not secret and name and str(options.get(name) or "").strip():
            status = {"configured": True, "source": "config_yaml", "env_var": env_var}
        configured = bool(status.get("configured"))
        statuses.append(
            {
                "name": name,
                "env_var": env_var,
                "configured": configured,
                "source": str(status.get("source") or "none"),
                "secret": secret,
                "required": required,
            }
        )
        if required and not configured:
            missing.append(env_var or name)
    return {
        "configured": not missing if fields else True,
        "missing": missing,
        "fields": statuses,
    }


def _image_gen_primary_key_status(
    fields: list[dict[str, Any]],
    credential_status: dict[str, Any],
) -> dict[str, Any]:
    field_statuses = credential_status.get("fields") if isinstance(credential_status, dict) else []
    if isinstance(field_statuses, list):
        for field in field_statuses:
            if isinstance(field, dict) and field.get("secret") and field.get("env_var"):
                return {
                    "configured": bool(field.get("configured")),
                    "source": str(field.get("source") or "none"),
                    "env_var": str(field.get("env_var") or ""),
                }
    for field in fields:
        env_var = str(field.get("env_var") or "").strip()
        if env_var:
            return _key_status_for_env(env_var)
    return {"configured": bool(credential_status.get("configured")), "source": "none", "env_var": ""}


def _image_gen_named_key_status(
    provider_id: str,
    *,
    active: bool,
) -> dict[str, Any] | None:
    if not active:
        return None
    config_data = load_credential_config(_get_config_path())
    image_cfg = config_data.get("image_gen")
    if not isinstance(image_cfg, dict):
        return None
    credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    if not credential_ref:
        return None
    try:
        row = load_credential(credential_ref, config_data=config_data)
        if provider_family(row.get("provider_family")) != provider_family(provider_id):
            return {"configured": False, "source": "none", "env_var": ""}
        secret_env = _validate_provider_credential_secret_env(row)
        configured = bool(_key_status_for_env(secret_env).get("configured"))
    except ValueError:
        configured = False
    return {
        "configured": configured,
        "source": "provider_credential" if configured else "none",
        "env_var": "",
    }


def _image_gen_policy_allowed(pid: str, schema: dict[str, Any], *, is_custom: bool) -> tuple[bool, bool, str]:
    if is_custom:
        return True, True, "custom"
    domestic = bool(schema.get("domestic")) if "domestic" in schema else pid in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
    status = str(schema.get("integration_status") or ("stable" if pid in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS else "external")).strip().lower()
    allowed = bool(domestic and status == "stable")
    return allowed, domestic, status


def _blocked_image_gen_row(
    *,
    provider_id: str,
    public_id: str | None = None,
    active: bool = True,
    reason_code: str = "domestic_policy_required",
    status_message: str = "当前配置不符合国产策略，请切换到中国可用的稳定生图 Provider。",
) -> dict[str, Any]:
    public = public_id or _public_image_gen_provider_id(provider_id)
    contract = normalized_setup_contract(
        {"auth_type": "oauth" if provider_id == "openai-codex" else "api_key"},
        provider_family=provider_family(public),
        capabilities=("image_generation",),
        transport="policy_blocked",
    )
    return {
        "id": public,
        "name": _BLOCKED_IMAGE_GEN_PROVIDER_LABELS.get(public, _BLOCKED_IMAGE_GEN_PROVIDER_LABELS.get(provider_id, public)),
        "description": "历史非国产图片生成配置，只读显示。",
        "badge": "已阻止",
        "available": False,
        "can_attempt": False,
        "active": active,
        "requires_env": [],
        "key_status": {"configured": False, "source": "policy_blocked", "env_var": ""},
        "credential_fields": [],
        "credential_status": {"configured": False, "missing": [], "fields": []},
        "reason_code": reason_code,
        "status_message": status_message,
        "models": [],
        "default_model": "",
        "oauth_managed": provider_id == "openai-codex",
        "custom": False,
        "domestic": False,
        "integration_status": "blocked",
        "policy_blocked": True,
        **contract,
    }


def _image_gen_provider_rows(active_provider: str) -> list[dict[str, Any]]:
    readiness: dict[str, Any] = {}
    try:
        from tools.image_generation_tool import get_image_generation_readiness

        readiness = get_image_generation_readiness()
    except Exception:
        readiness = {}

    try:
        _ensure_image_gen_plugins_registered()
        from agent import image_gen_registry

        providers = image_gen_registry.list_providers()
    except Exception:
        logger.debug("Failed to list image_gen providers", exc_info=True)
        providers = []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    active_options = _image_gen_options_for_active(active_provider)
    for provider in providers:
        pid = str(getattr(provider, "name", "") or "").strip()
        if not pid:
            continue
        seen.add(pid)
        schema = {}
        models = []
        default_model = ""
        available = False
        try:
            schema = provider.get_setup_schema() or {}
        except Exception:
            schema = {}
        try:
            models = provider.list_models() or []
        except Exception:
            models = []
        try:
            default_model = str(provider.default_model() or "").strip()
        except Exception:
            default_model = ""
        try:
            available = bool(provider.is_available())
        except Exception:
            available = False
        is_custom = pid.startswith("custom:")

        env_vars = []
        for item in schema.get("env_vars") or []:
            if isinstance(item, dict) and item.get("key"):
                env_vars.append(str(item.get("key")).strip())
        env_var = _IMAGE_GEN_KEY_ENV.get(pid) or (env_vars[0] if env_vars else "")
        active = pid == active_provider
        public_pid = _public_image_gen_provider_id(pid)
        allowed, domestic, integration_status = _image_gen_policy_allowed(pid, schema, is_custom=is_custom)
        if not allowed:
            if active:
                blocked = _blocked_image_gen_row(
                    provider_id=pid,
                    public_id=public_pid,
                    active=True,
                    reason_code=str(readiness.get("reason_code") or "domestic_policy_required"),
                    status_message=str(readiness.get("public_message") or "当前配置不符合国产策略，请切换到中国可用的稳定生图 Provider。"),
                )
                rows.append(blocked)
            continue

        raw_credential_fields = _image_gen_credential_fields(
            schema=schema,
            env_var=env_var,
            env_vars=env_vars,
        )
        transport = {
            "dashscope": "dashscope_native_image_generation",
            "doubao": "volcengine_ark_images",
            "qianfan": "qianfan_images",
            "zhipu-image": "zhipu_images",
            "minimax-image": "minimax_images",
        }.get(pid, "openai_images" if is_custom else f"{pid}_images")
        contract = normalized_setup_contract(
            schema | {"credential_fields": raw_credential_fields},
            provider_family=provider_family(pid),
            capabilities=("image_generation",),
            auth_type="api_key",
            transport=transport,
            models=[
                {
                    "id": str(item.get("id") or "").strip(),
                    "label": str(
                        item.get("display")
                        or item.get("label")
                        or item.get("id")
                        or ""
                    ).strip(),
                }
                for item in models
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ],
        )
        credential_fields = contract["credential_fields"]
        named_key_status = _image_gen_named_key_status(pid, active=active)
        credential_status = _image_gen_credential_status(
            raw_credential_fields,
            active_options=active_options if active else {},
            secret_status_override=named_key_status,
        )
        key_status = named_key_status or _image_gen_primary_key_status(
            credential_fields, credential_status
        )
        if is_custom and key_status.get("configured") and default_model:
            available = True
        display_name = str(schema.get("name") or getattr(provider, "display_name", "") or pid)
        description = str(schema.get("tag") or "")
        badge = str(schema.get("badge") or ("外部" if is_custom else "国产"))
        can_attempt = bool(available)
        if active:
            available = bool(readiness.get("available"))
            reason_code = str(readiness.get("reason_code") or "")
            status_message = str(readiness.get("public_message") or "")
        else:
            available = False
            reason_code = ""
            status_message = ""
        if is_custom:
            # The generic readiness check only proves that local fields exist.
            # Keep public status unverified until the dedicated generation probe succeeds.
            available = False
            if can_attempt:
                reason_code = "configured_unverified"
                status_message = "已配置，尚未验证。"
        rows.append(
            {
                "id": public_pid,
                "name": display_name,
                "description": description,
                "badge": badge,
                "available": bool(available),
                "can_attempt": can_attempt,
                "active": active,
                "requires_env": env_vars or ([env_var] if env_var else []),
                "key_status": key_status,
                "credential_fields": credential_fields,
                "credential_status": credential_status,
                "reason_code": reason_code,
                "status_message": status_message,
                "models": [
                    {
                        "id": str(item.get("id") or "").strip(),
                        "label": str(item.get("display") or item.get("id") or "").strip(),
                    }
                    for item in models
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ],
                "default_model": default_model,
                "oauth_managed": False,
                "custom": is_custom,
                "domestic": domestic,
                "integration_status": integration_status,
                "policy_blocked": False,
                "allow_custom_model_id": (
                    schema.get("allow_custom_model_id") is True
                ),
                **contract,
            }
        )

    for pid, env_var in _IMAGE_GEN_KEY_ENV.items():
        if pid in seen:
            continue
        active = pid == active_provider
        if pid not in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS:
            if active:
                rows.append(_blocked_image_gen_row(provider_id=pid, active=True))
            continue
        fallback = _IMAGE_GEN_FALLBACK_META.get(pid, {})
        raw_credential_fields = _image_gen_credential_fields(schema={}, env_var=env_var, env_vars=[env_var])
        contract = normalized_setup_contract(
            {"credential_fields": raw_credential_fields},
            provider_family=provider_family(pid),
            capabilities=("image_generation",),
            auth_type="api_key",
            transport={
                "dashscope": "dashscope_native_image_generation",
                "doubao": "volcengine_ark_images",
                "qianfan": "qianfan_images",
                "zhipu-image": "zhipu_images",
                "minimax-image": "minimax_images",
            }.get(pid, f"{pid}_images"),
            models=fallback.get("models") or [],
        )
        credential_fields = contract["credential_fields"]
        credential_status = _image_gen_credential_status(
            credential_fields,
            active_options=active_options if active else {},
        )
        can_attempt = bool(_key_status_for_env(env_var).get("configured"))
        public_available = bool(active and readiness.get("available"))
        rows.append(
            {
                "id": pid,
                "name": str(fallback.get("name") or pid.replace("-", " ").title()),
                "description": "",
                "badge": "国产",
                "available": public_available,
                "can_attempt": can_attempt,
                "active": active,
                "requires_env": [env_var],
                "key_status": _image_gen_primary_key_status(credential_fields, credential_status),
                "credential_fields": credential_fields,
                "credential_status": credential_status,
                "models": list(fallback.get("models") or []),
                "default_model": str(fallback.get("default_model") or ""),
                "oauth_managed": False,
                "custom": False,
                "domestic": True,
                "integration_status": "stable",
                "policy_blocked": False,
                **contract,
            }
        )
    if "openai-codex" not in seen:
        active = active_provider == "openai-codex"
        if active:
            rows.append(
                _blocked_image_gen_row(
                    provider_id="openai-codex",
                    public_id=_public_image_gen_provider_id("openai-codex"),
                    active=True,
                    reason_code=str(readiness.get("reason_code") or "domestic_policy_required"),
                    status_message=str(readiness.get("public_message") or "当前配置不符合国产策略，请切换到中国可用的稳定生图 Provider。"),
                )
            )
    return sorted(rows, key=lambda row: (not row.get("active"), row.get("id") or ""))


def get_image_gen_config() -> dict[str, Any]:
    with credential_transaction(_get_config_path()):
        return _get_image_gen_config_unlocked()


def _get_image_gen_config_unlocked(
    *,
    refresh_runtime: bool = True,
    config_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if refresh_runtime:
        reload_config()
    config_path = _get_config_path()
    exact_config_data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config(config_path)
    )
    image_cfg = exact_config_data.get("image_gen")
    if not isinstance(image_cfg, dict):
        image_cfg = {}
    active_provider = str(image_cfg.get("provider") or "").strip()
    active_model = str(image_cfg.get("model") or "").strip()
    enabled = (
        image_cfg["enabled"]
        if isinstance(image_cfg.get("enabled"), bool)
        else bool(active_provider and active_model)
    )
    explicitly_disabled = image_cfg.get("enabled") is False
    active_options = image_cfg.get("options")
    if not isinstance(active_options, dict):
        active_options = {}
    provider_rows = _image_gen_provider_rows(active_provider)
    active_public_provider = _public_image_gen_provider_id(active_provider)
    active_row = next(
        (
            row
            for row in provider_rows
            if str(row.get("id") or "") == active_public_provider
        ),
        {},
    )
    endpoint_names = {
        str(field.get("name") or "").strip()
        for field in (active_row.get("endpoint_fields") or [])
        if isinstance(field, dict) and not bool(field.get("secret"))
    }
    if not active_row and active_provider == "dashscope":
        endpoint_names.update({"endpoint_mode", "workspace_id", "region", "base_url"})
    endpoint_names.discard("")
    endpoint_values = {
        name: str(active_options.get(name) or "").strip()
        for name in endpoint_names
        if name in active_options
    }
    verification = (
        _public_image_gen_verification(
            image_cfg,
            profile=_active_profile_name(),
            snapshot=_capture_image_gen_config_snapshot_unlocked(
                config_path=Path(config_path),
                config_data=exact_config_data,
            ),
        )
        if not explicitly_disabled
        else {
            "status": "disabled",
            "checked_at": "",
            "error_code": "",
            "message": "图片生成已停用，原配置和凭据仍保留。",
            "diagnostic_id": "",
        }
    )
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "image_gen": {
            "enabled": enabled,
            "provider": _public_image_gen_provider_id(active_provider),
            "model": active_model,
            "use_gateway": bool(image_cfg.get("use_gateway")),
            "credential_ref": str(image_cfg.get("credential_ref") or "").strip(),
            "options": dict(endpoint_values),
            "endpoint_values": endpoint_values,
            "verification": verification,
        },
        "providers": provider_rows,
        "custom_image_providers": get_custom_image_provider_configs().get(
            "providers", []
        ),
    }


def _custom_vision_entry_and_secret_env(
    provider_id: str,
    *,
    config_data: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    provider = str(provider_id or "").strip().lower()
    if not provider.startswith("custom:"):
        return {}, ""
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_secret_env,
            find_custom_vision_provider_entry,
        )
    except ImportError:
        return {}, ""
    data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config(_get_config_path())
    )
    entry = find_custom_vision_provider_entry(provider, data) or {}
    if not entry:
        return {}, ""
    credential_ref = str(entry.get("credential_ref") or "").strip()
    if credential_ref:
        try:
            row = load_credential(credential_ref, config_data=data)
            if provider_family(row.get("provider_family")) != "custom":
                return entry, ""
            expected = credential_secret_env(row.get("id"))
            if str(row.get("secret_env") or "").strip() != expected:
                return entry, ""
            return entry, expected
        except ValueError:
            return entry, ""
    try:
        return entry, custom_vision_provider_secret_env(entry)
    except ValueError:
        return entry, ""


def _vision_key_status(
    provider_id: str,
    vision_cfg: dict[str, Any] | None = None,
    *,
    config_data: dict[str, Any] | None = None,
    config_path: Path | None = None,
    allow_process_fallback: bool | None = None,
) -> dict[str, Any]:
    provider = str(provider_id or "").strip().lower()
    if allow_process_fallback is False:
        data = (
            config_data
            if isinstance(config_data, dict)
            else load_credential_config(config_path or _get_config_path())
        )
        exact_config_path = Path(config_path or _get_config_path())
        env_var = ""
        secret_value = ""
        try:
            if provider.startswith("custom:"):
                from agent.custom_vision_providers import (
                    custom_vision_provider_api_key,
                )

                entry, env_var = _custom_vision_entry_and_secret_env(
                    provider,
                    config_data=data,
                )
                if entry:
                    secret_value = custom_vision_provider_api_key(
                        entry,
                        config_path=exact_config_path,
                        allow_process_fallback=False,
                    )
            elif provider == "custom":
                env_var = _VISION_KEY_ENV["custom"]
                secret_value = resolve_secret_env_value(
                    env_var,
                    config_path=exact_config_path,
                    allow_process_fallback=False,
                )
            elif provider in {"alibaba", "zai"}:
                credential_ref = str(
                    (vision_cfg or {}).get("credential_ref") or ""
                ).strip()
                env_var = _VISION_KEY_ENV.get(provider, "")
                if credential_ref:
                    row = load_credential(
                        credential_ref,
                        config_data=data,
                    )
                    if (
                        provider_family(row.get("provider_family"))
                        == provider_family(provider)
                    ):
                        env_var = credential_secret_env(row.get("id"))
                secret_value = resolve_api_key(
                    provider,
                    credential_ref,
                    config_data=data,
                    config_path=exact_config_path,
                    allow_process_fallback=False,
                )
            else:
                env_var = (
                    _VISION_KEY_ENV.get(provider)
                    or _PROVIDER_ENV_VAR.get(provider)
                    or ""
                )
                if env_var:
                    secret_value = resolve_secret_env_value(
                        env_var,
                        config_path=exact_config_path,
                        allow_process_fallback=False,
                    )
        except (ImportError, ValueError):
            secret_value = ""
        return {
            "configured": bool(secret_value),
            "source": "env_file" if secret_value else "none",
            "env_var": env_var,
        }
    if provider.startswith("custom:"):
        _entry, env_var = _custom_vision_entry_and_secret_env(
            provider,
            config_data=config_data,
        )
        return (
            _key_status_for_env(env_var)
            if env_var
            else {"configured": False, "source": "none", "env_var": ""}
        )
    credential_ref = str((vision_cfg or {}).get("credential_ref") or "").strip()
    if credential_ref:
        try:
            data = (
                config_data
                if isinstance(config_data, dict)
                else load_credential_config(_get_config_path())
            )
            row = load_credential(
                credential_ref,
                config_data=data,
            )
            if provider_family(row.get("provider_family")) != provider_family(provider):
                return {"configured": False, "source": "none", "env_var": ""}
            secret_env = credential_secret_env(row.get("id"))
            if str(row.get("secret_env") or "").strip() != secret_env:
                return {"configured": False, "source": "none", "env_var": ""}
            return _key_status_for_env(secret_env)
        except ValueError:
            return {"configured": False, "source": "none", "env_var": ""}
    env_var = _VISION_KEY_ENV.get(provider)
    if env_var:
        return _key_status_for_env(env_var)
    return _provider_key_status(provider)


_VISION_STATE_LOCKS_GUARD = threading.Lock()
_VISION_STATE_LOCKS: dict[str, threading.Lock] = {}
_VISION_PROBE_GENERATIONS: dict[str, int] = {}


@dataclass(frozen=True)
class _VisionConfigSnapshot:
    config_path: Path
    runtime_home: Path
    profile: str
    provider: str
    model: str
    base_url: str
    api_mode: str
    credential_ref: str
    endpoint_mode: str
    region: str
    workspace_id: str
    configured: bool
    effective_config_resolved: bool
    fingerprint: str
    binding: VisionRequestBinding | None = dataclass_field(
        default=None,
        compare=False,
        repr=False,
    )


def _vision_verification_state_root() -> Path:
    from api.config import STATE_DIR

    return Path(STATE_DIR) / "vision-verification"


def _vision_verification_state_path(profile: str | None = None) -> Path:
    profile_name = str(profile or _active_profile_name() or "default")
    profile_id = hashlib.sha256(profile_name.encode("utf-8")).hexdigest()[:24]
    return _vision_verification_state_root() / f"{profile_id}.json"


def _vision_probe_image_path(profile: str) -> Path:
    return _vision_verification_state_path(profile).with_name("vision-verification-probe.png")


def _vision_profile_lock(profile: str) -> threading.Lock:
    with _VISION_STATE_LOCKS_GUARD:
        lock = _VISION_STATE_LOCKS.get(profile)
        if lock is None:
            lock = threading.Lock()
            _VISION_STATE_LOCKS[profile] = lock
        return lock


def _begin_vision_probe(profile: str, state: dict[str, Any]) -> int:
    with _vision_profile_lock(profile):
        state_path = _vision_verification_state_path(profile)
        with _verification_state_file_lock(state_path):
            disk_generation = _verification_state_generation(
                _read_verification_state_file(state_path)
            )
            generation = max(
                _VISION_PROBE_GENERATIONS.get(profile, 0),
                disk_generation,
            ) + 1
            _VISION_PROBE_GENERATIONS[profile] = generation
            generation_state = dict(state)
            generation_state["generation"] = generation
            _atomic_write_json(state_path, generation_state)
            return generation


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        _fsync_parent_directory(path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    _atomic_write_bytes(path, encoded)


@contextmanager
def _windows_verification_state_mutex(path: Path):
    """Use a logical-path mutex without opening attacker-controlled lockfiles."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
    ]
    create_mutex.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single_object.restype = ctypes.c_uint32
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = [ctypes.c_void_p]
    release_mutex.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    logical_path = os.path.normcase(
        os.path.abspath(os.fspath(Path(path)))
    )
    mutex_id = hashlib.sha256(
        logical_path.encode("utf-8")
    ).hexdigest()
    handle = create_mutex(
        None,
        False,
        f"Local\\TaijiVerificationState-{mutex_id}",
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        wait_result = wait_for_single_object(handle, 0xFFFFFFFF)
        if wait_result not in {0x00000000, 0x00000080}:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired and not release_mutex(handle):
            logger.error("Failed to release verification state mutex")
        close_handle(handle)


@contextmanager
def _verification_state_file_lock(path: Path):
    """Serialize one capability-state CAS across WebUI worker processes."""
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI.
        with _windows_verification_state_mutex(state_path):
            yield
        return

    lock_path = state_path.with_name(f".{state_path.name}.lock")
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    lock_fd = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise OSError(f"unsafe verification state lock: {lock_path}")
        if hasattr(os, "fchmod"):
            os.fchmod(lock_fd, 0o600)
        if _fcntl is not None:
            _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
        else:  # pragma: no cover - supported targets provide one primitive.
            raise RuntimeError("cross-process verification state locking unavailable")
        locked = True
        yield
    finally:
        try:
            if locked and _fcntl is not None:
                _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _read_verification_state_file(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _verification_state_generation(state: Any) -> int:
    generation = state.get("generation") if isinstance(state, dict) else None
    return generation if type(generation) is int and generation >= 0 else 0


def _verification_invalidation_state(generation: int) -> dict[str, Any]:
    """Persist a private tombstone so restart cannot reuse an old generation."""
    return {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "generation": generation,
        "fingerprint": "",
        "status": "configured_unverified",
        "checked_at": "",
        "error_code": "",
        "message": "",
        "diagnostic_id": "",
    }


def _owned_verifying_state(
    path: Path,
    *,
    generation: int,
    fingerprint: str,
    diagnostic_id: str,
) -> dict[str, Any]:
    """Return the exact in-flight state owned by one probe, if still current."""
    state = _read_verification_state_file(path)
    state_generation = state.get("generation")
    if (
        type(state_generation) is not int
        or state_generation != generation
        or str(state.get("status") or "") != "verifying"
        or str(state.get("fingerprint") or "") != fingerprint
        or str(state.get("diagnostic_id") or "") != diagnostic_id
    ):
        return {}
    return state


def _commit_owned_verification_result(
    path: Path,
    *,
    generation: int,
    current_generation: int,
    fingerprint: str,
    diagnostic_id: str,
    state: dict[str, Any],
) -> bool:
    """CAS one final probe result over only its own persisted in-flight state."""
    if current_generation != generation or not _owned_verifying_state(
        path,
        generation=generation,
        fingerprint=fingerprint,
        diagnostic_id=diagnostic_id,
    ):
        return False
    final_state = dict(state)
    final_state["generation"] = generation
    _atomic_write_json(path, final_state)
    return True


def _discard_owned_verifying_state(
    path: Path,
    *,
    generation: int,
    current_generation: int,
    fingerprint: str,
    diagnostic_id: str,
) -> bool:
    """Invalidate only the still-current in-flight state owned by one probe."""
    if current_generation != generation:
        return False
    if not _owned_verifying_state(
        path,
        generation=generation,
        fingerprint=fingerprint,
        diagnostic_id=diagnostic_id,
    ):
        return False
    try:
        _atomic_write_json(
            path,
            _verification_invalidation_state(generation + 1),
        )
    except OSError:
        logger.warning(
            "Failed to invalidate superseded verification state at %s",
            path,
        )
        return False
    return True


def _read_vision_verification_state(profile: str) -> dict[str, Any]:
    with _vision_profile_lock(profile):
        try:
            data = json.loads(
                _vision_verification_state_path(profile).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}


def _verification_state_file_identity(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"
    return hashlib.sha256(payload).hexdigest()


def _verification_probe_runtime_snapshot(
    state: dict[str, Any],
    *,
    generation: int,
    capability: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Freeze the private identity of the verifying state written to disk."""
    persisted_state = dict(state)
    persisted_state["generation"] = generation
    fingerprint = str(persisted_state.get("fingerprint") or "")
    return {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "status": "verifying",
        "available": False,
        "_authorization_generation": verification_authorization_generation(
            persisted_state,
            expected_fingerprint=fingerprint,
            capability=capability,
        ),
        "provider": str(provider or "").strip().lower(),
        "model": str(model or "").strip(),
    }


def _capture_vision_verification_invalidation(
    profile: str | None = None,
) -> _VerificationInvalidationToken:
    committed_profile = str(profile or _active_profile_name() or "default")
    with _vision_profile_lock(committed_profile):
        state_path = _vision_verification_state_path(committed_profile)
        with _verification_state_file_lock(state_path):
            disk_generation = _verification_state_generation(
                _read_verification_state_file(state_path)
            )
            generation = max(
                _VISION_PROBE_GENERATIONS.get(committed_profile, 0),
                disk_generation,
            )
            _VISION_PROBE_GENERATIONS[committed_profile] = generation
            return _VerificationInvalidationToken(
                capability="vision",
                profile=committed_profile,
                generation=generation,
                state_identity=_verification_state_file_identity(state_path),
            )


def _vision_secret_value(
    provider: str,
    credential_ref: str = "",
    *,
    config_data: dict[str, Any] | None = None,
    config_path: Path | None = None,
    allow_process_fallback: bool | None = None,
) -> str:
    env_var = ""
    if str(provider or "").startswith("custom:"):
        _entry, env_var = _custom_vision_entry_and_secret_env(
            provider,
            config_data=config_data,
        )
    elif credential_ref:
        try:
            data = (
                config_data
                if isinstance(config_data, dict)
                else load_credential_config(_get_config_path())
            )
            row = load_credential(
                credential_ref,
                config_data=data,
            )
            if provider_family(row.get("provider_family")) == provider_family(provider):
                expected = credential_secret_env(row.get("id"))
                if str(row.get("secret_env") or "").strip() == expected:
                    env_var = expected
        except ValueError:
            pass
    else:
        env_var = _VISION_KEY_ENV.get(provider) or _PROVIDER_ENV_VAR.get(provider) or ""
    if not env_var:
        return ""
    return resolve_secret_env_value(
        env_var,
        config_path=config_path or _get_config_path(),
        allow_process_fallback=allow_process_fallback,
    )


def _vision_config_fingerprint(
    vision_cfg: dict[str, Any],
    key_status: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any] | None = None,
    secret_value: str | None = None,
    resolved_material: VisionResolvedMaterial | None = None,
) -> str:
    data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config(_get_config_path())
    )
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    if secret_value is None:
        secret_value = _vision_secret_value(
            provider,
            str(vision_cfg.get("credential_ref") or "").strip(),
            config_data=data,
        )
    if resolved_material is not None:
        fingerprint, _resolved = vision_fingerprint_from_material(
            resolved_material,
            profile=profile,
            secret_value=secret_value,
            key_configured=bool(key_status.get("configured")),
        )
    else:
        fingerprint, _resolved = vision_fingerprint(
            vision_cfg,
            profile=profile,
            config_data=data,
            secret_value=secret_value,
            key_configured=bool(key_status.get("configured")),
        )
    return fingerprint


def _capture_vision_config_snapshot() -> _VisionConfigSnapshot:
    config_path = Path(_get_config_path())
    with credential_transaction(config_path):
        return _capture_vision_config_snapshot_unlocked(
            config_path=config_path
        )


def _capture_vision_config_snapshot_unlocked(
    *,
    config_path: Path | None = None,
    config_data: dict[str, Any] | None = None,
) -> _VisionConfigSnapshot:
    exact_config_path = Path(config_path or _get_config_path())
    profile = _active_profile_name()
    raw_config_data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config(exact_config_path)
    )
    raw_auxiliary = raw_config_data.get("auxiliary")
    raw_vision_cfg = (
        raw_auxiliary.get("vision") if isinstance(raw_auxiliary, dict) else {}
    )
    if not isinstance(raw_vision_cfg, dict):
        raw_vision_cfg = {}
    resolved_material = resolve_vision_material(
        raw_vision_cfg,
        raw_config_data,
    )
    vision_cfg = resolved_material.vision_cfg
    config_data = resolved_material.config_data
    config_resolved = bool(
        resolved_material.data_resolved
        and resolved_material.cfg_resolved
    )
    endpoint_resolved = resolved_material.endpoint_resolved
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    model = str(vision_cfg.get("model") or "").strip()
    credential_ref = str(vision_cfg.get("credential_ref") or "").strip()
    secret_value = ""
    binding: VisionRequestBinding | None = None
    base_url = str(vision_cfg.get("base_url") or "").strip().rstrip("/")
    api_mode = str(vision_cfg.get("api_mode") or "").strip()
    endpoint_mode = str(vision_cfg.get("endpoint_mode") or "").strip().lower()
    region = str(vision_cfg.get("region") or "").strip()
    workspace_id = str(vision_cfg.get("workspace_id") or "").strip()
    network_scope = (
        str(vision_cfg.get("network_scope") or "public_direct").strip()
        or "public_direct"
    )
    trusted_proxy_profile = str(
        vision_cfg.get("trusted_proxy_profile") or ""
    ).strip()
    config_complete = False
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import (
                custom_vision_provider_api_key,
                find_custom_vision_provider_entry,
            )

            entry = find_custom_vision_provider_entry(provider, config_data) or {}
            if entry:
                secret_value = custom_vision_provider_api_key(
                    entry,
                    config_path=exact_config_path,
                    allow_process_fallback=False,
                )
                config_complete = bool(
                    model
                    and model in (entry.get("models") or [])
                    and base_url
                    and secret_value
                )
        except (ImportError, ValueError):
            entry = {}
    elif provider == "alibaba":
        try:
            endpoint_mode = endpoint_mode or "public"
            region = region or "cn-beijing"
            secret_value = resolve_api_key(
                "alibaba",
                credential_ref,
                config_data=config_data,
                config_path=exact_config_path,
                allow_process_fallback=False,
            )
            config_complete = bool(
                endpoint_resolved and model and base_url and secret_value
            )
        except ValueError:
            config_complete = False
    elif provider == "zai":
        try:
            secret_value = resolve_api_key(
                "zai",
                credential_ref,
                config_data=config_data,
                config_path=exact_config_path,
                allow_process_fallback=False,
            )
            config_complete = bool(
                endpoint_resolved and model and base_url and secret_value
            )
        except ValueError:
            config_complete = False
    elif provider == "custom":
        try:
            secret_value = resolve_secret_env_value(
                _VISION_KEY_ENV["custom"],
                config_path=exact_config_path,
                allow_process_fallback=False,
            )
            config_complete = bool(
                endpoint_resolved and model and base_url and secret_value
            )
        except ValueError:
            config_complete = False
    key_status = {
        "configured": bool(secret_value),
        "source": "profile",
        "env_var": "",
    }
    fingerprint = _vision_config_fingerprint(
        vision_cfg,
        key_status,
        profile=profile,
        config_data=config_data,
        secret_value=secret_value,
        resolved_material=resolved_material,
    )
    if config_resolved and endpoint_resolved and config_complete:
        binding = VisionRequestBinding(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=secret_value,
            api_mode=api_mode,
            network_scope=network_scope,
            trusted_proxy_profile=trusted_proxy_profile,
            endpoint_mode=endpoint_mode,
        )
    return _VisionConfigSnapshot(
        config_path=exact_config_path,
        runtime_home=Path(_get_hermes_home()),
        profile=profile,
        provider=provider,
        model=model,
        base_url=base_url,
        api_mode=api_mode,
        credential_ref=credential_ref,
        endpoint_mode=endpoint_mode,
        region=region,
        workspace_id=workspace_id,
        configured=bool(
            config_resolved
            and endpoint_resolved
            and config_complete
            and binding is not None
        ),
        effective_config_resolved=bool(
            config_resolved and endpoint_resolved
        ),
        fingerprint=fingerprint,
        binding=binding,
    )


def _vision_is_configured(
    vision_cfg: dict[str, Any],
    key_status: dict[str, Any],
    *,
    config_data: dict[str, Any] | None = None,
) -> bool:
    provider = str(vision_cfg.get("provider") or "").strip().lower()
    model = str(vision_cfg.get("model") or "").strip()
    meta = _VISION_PROVIDER_META.get(provider) or {}
    if provider.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            entry = find_custom_vision_provider_entry(
                provider,
                config_data
                if isinstance(config_data, dict)
                else load_credential_config(_get_config_path()),
            )
        except ImportError:
            entry = None
        return bool(entry and model in entry["models"] and key_status.get("configured"))
    base_url = str(vision_cfg.get("base_url") or "").strip()
    return bool(
        provider
        and model
        and key_status.get("configured")
        and (not meta.get("requires_base_url") or base_url)
    )


def _public_vision_verification(
    vision_cfg: dict[str, Any],
    key_status: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any] | None = None,
    snapshot: _VisionConfigSnapshot | None = None,
) -> dict[str, Any]:
    if snapshot is not None:
        if not snapshot.effective_config_resolved:
            return {
                "status": "configured_unverified",
                "checked_at": "",
                "error_code": "unresolved_effective_config",
                "message": "识图配置包含未解析的环境变量，请修正后重新验证。",
                "diagnostic_id": "",
            }
        if not snapshot.configured:
            return {
                "status": "unconfigured",
                "checked_at": "",
                "error_code": "vision_not_configured",
                "message": "请先保存完整的识图 Provider、模型和密钥配置。",
                "diagnostic_id": "",
            }
        state = _read_vision_verification_state(snapshot.profile)
        persisted_status = verification_status_from_state(
            state,
            expected_fingerprint=snapshot.fingerprint,
        )
        if persisted_status in {"verifying", "verified", "failed"}:
            return {
                "status": persisted_status,
                "checked_at": str(state.get("checked_at") or ""),
                "error_code": str(state.get("error_code") or ""),
                "message": str(state.get("message") or ""),
                "diagnostic_id": str(state.get("diagnostic_id") or ""),
            }
        return {
            "status": "configured_unverified",
            "checked_at": "",
            "error_code": "",
            "message": "识图配置已保存，但尚未通过真实图片验证。",
            "diagnostic_id": "",
        }
    data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config(_get_config_path())
    )
    effective_data, data_resolved = expand_effective_config(data)
    effective_cfg, cfg_resolved = expand_effective_config(vision_cfg)
    if not isinstance(effective_data, dict):
        effective_data = {}
    if not isinstance(effective_cfg, dict):
        effective_cfg = {}
    if not (
        data_resolved
        and cfg_resolved
        and _vision_is_configured(
        effective_cfg,
        key_status,
        config_data=effective_data,
        )
    ):
        if not (data_resolved and cfg_resolved):
            return {
                "status": "configured_unverified",
                "checked_at": "",
                "error_code": "unresolved_effective_config",
                "message": "识图配置包含未解析的环境变量，请修正后重新验证。",
                "diagnostic_id": "",
            }
        return {
            "status": "unconfigured",
            "checked_at": "",
            "error_code": "vision_not_configured",
            "message": "请先保存完整的识图 Provider、模型和密钥配置。",
            "diagnostic_id": "",
        }
    state = _read_vision_verification_state(profile)
    fingerprint = _vision_config_fingerprint(
        effective_cfg,
        key_status,
        profile=profile,
        config_data=effective_data,
    )
    persisted_status = verification_status_from_state(
        state,
        expected_fingerprint=fingerprint,
    )
    if persisted_status in {"verifying", "verified", "failed"}:
        return {
            "status": persisted_status,
            "checked_at": str(state.get("checked_at") or ""),
            "error_code": str(state.get("error_code") or ""),
            "message": str(state.get("message") or ""),
            "diagnostic_id": str(state.get("diagnostic_id") or ""),
        }
    return {
        "status": "configured_unverified",
        "checked_at": "",
        "error_code": "",
        "message": "识图配置已保存，但尚未通过真实图片验证。",
        "diagnostic_id": "",
    }


def _invalidate_vision_verification(
    expected: _VerificationInvalidationToken | None = None,
) -> bool:
    profile = (
        expected.profile
        if expected is not None
        else _active_profile_name()
    )
    with _vision_profile_lock(profile):
        path = _vision_verification_state_path(profile)
        with _verification_state_file_lock(path):
            disk_generation = _verification_state_generation(
                _read_verification_state_file(path)
            )
            current_generation = max(
                _VISION_PROBE_GENERATIONS.get(profile, 0),
                disk_generation,
            )
            if expected is not None:
                if expected.capability != "vision":
                    raise ValueError("invalid vision verification token")
                if (
                    current_generation != expected.generation
                    or _verification_state_file_identity(path)
                    != expected.state_identity
                ):
                    return False
            next_generation = current_generation + 1
            _VISION_PROBE_GENERATIONS[profile] = next_generation
            _atomic_write_json(
                path,
                _verification_invalidation_state(next_generation),
            )
            return True


def _invalidate_image_gen_verification(
    expected: _VerificationInvalidationToken | None = None,
) -> bool:
    profile = (
        expected.profile
        if expected is not None
        else _active_profile_name()
    )
    with _image_gen_profile_lock(profile):
        path = _image_gen_verification_state_path(profile)
        with _verification_state_file_lock(path):
            disk_generation = _verification_state_generation(
                _read_verification_state_file(path)
            )
            current_generation = max(
                _IMAGE_GEN_PROBE_GENERATIONS.get(profile, 0),
                disk_generation,
            )
            if expected is not None:
                if expected.capability != "image":
                    raise ValueError("invalid image generation verification token")
                if (
                    current_generation != expected.generation
                    or _verification_state_file_identity(path)
                    != expected.state_identity
                ):
                    return False
            next_generation = current_generation + 1
            _IMAGE_GEN_PROBE_GENERATIONS[profile] = next_generation
            _atomic_write_json(
                path,
                _verification_invalidation_state(next_generation),
            )
            return True

def _vision_test_response(
    *,
    ok: bool,
    status: str,
    checked_at: str,
    provider: str,
    model: str,
    error_code: str,
    message: str,
    diagnostic_id: str,
) -> dict[str, Any]:
    response = {
        "ok": bool(ok),
        "status": status,
        "checked_at": checked_at,
        "provider": provider,
        "model": model,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    return {key: response[key] for key in _VISION_VERIFICATION_PUBLIC_FIELDS}


def test_vision_config(
    *,
    snapshot: _VisionConfigSnapshot | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        reload_config()
        config_path = Path(_get_config_path())
        with credential_transaction(config_path):
            config_data = load_credential_config(config_path)
            auxiliary = config_data.get("auxiliary")
            raw_vision_cfg = (
                auxiliary.get("vision")
                if isinstance(auxiliary, dict)
                else {}
            )
            if not isinstance(raw_vision_cfg, dict):
                raw_vision_cfg = {}
            explicitly_disabled = raw_vision_cfg.get("enabled") is False
            disabled_provider = str(
                raw_vision_cfg.get("provider") or ""
            ).strip().lower()
            disabled_model = str(raw_vision_cfg.get("model") or "").strip()
            snapshot = (
                None
                if explicitly_disabled
                else _capture_vision_config_snapshot()
            )
    else:
        explicitly_disabled = False
        disabled_provider = snapshot.provider
        disabled_model = snapshot.model
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    diagnostic_id = uuid.uuid4().hex
    if explicitly_disabled:
        return _vision_test_response(
            ok=False,
            status="disabled",
            checked_at=checked_at,
            provider=disabled_provider,
            model=disabled_model,
            error_code="capability_disabled",
            message="看图识别已停用，未执行真实图片探测。",
            diagnostic_id=diagnostic_id,
        )
    assert snapshot is not None
    if not snapshot.effective_config_resolved and snapshot.provider:
        return _vision_test_response(
            ok=False,
            status="configured_unverified",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="unresolved_effective_config",
            message="识图配置包含未解析或不受支持的运行时端点，请修正后重新验证。",
            diagnostic_id=diagnostic_id,
        )
    if not snapshot.configured or snapshot.binding is None:
        return _vision_test_response(
            ok=False,
            status="unconfigured",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="vision_not_configured",
            message="请先保存完整的识图 Provider、模型和密钥配置。",
            diagnostic_id=diagnostic_id,
        )

    verifying_state = {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "fingerprint": snapshot.fingerprint,
        "status": "verifying",
        "checked_at": checked_at,
        "error_code": "",
        "message": "正在执行真实识图测试。",
        "diagnostic_id": diagnostic_id,
    }
    generation = _begin_vision_probe(snapshot.profile, verifying_state)
    verifying_snapshot = _verification_probe_runtime_snapshot(
        verifying_state,
        generation=generation,
        capability="vision",
        provider=snapshot.provider,
        model=snapshot.model,
    )
    probe_binding = authorize_vision_request_binding(
        snapshot.binding,
        authorization_fingerprint=verifying_snapshot["fingerprint"],
        authorization_generation=verifying_snapshot[
            "_authorization_generation"
        ],
    )
    error_code = ""
    message = "识图验证通过，当前配置已完成真实图片探测。"
    ok = False
    from hermes_constants import (
        reset_hermes_config_path_override,
        reset_hermes_home_override,
        set_hermes_config_path_override,
        set_hermes_home_override,
    )

    home_token = set_hermes_home_override(snapshot.runtime_home)
    config_path_token = set_hermes_config_path_override(
        snapshot.config_path
    )
    try:
        try:
            from tools.vision_tools import vision_analyze_tool

            probe_path = _vision_probe_image_path(snapshot.profile)
            if not probe_path.exists() or probe_path.read_bytes() != _VISION_PROBE_PNG:
                _atomic_write_bytes(probe_path, _VISION_PROBE_PNG)
            prompt = (
                "请只识别图片中的大写英文、数字和连字符标记。"
                "不要猜测或补全，请在回复中完整包含你真实看到的标记。"
            )

            async def _run_probe() -> str:
                return await vision_analyze_tool(
                    image_url=str(probe_path),
                    user_prompt=prompt,
                    model=snapshot.model,
                    provider=snapshot.provider,
                    strict_target=True,
                    _runtime_binding=probe_binding,
                )

            result = json.loads(asyncio.run(_run_probe()))
            analysis = str(result.get("analysis") or "") if isinstance(result, dict) else ""
            ok = bool(
                isinstance(result, dict)
                and result.get("success")
                and result.get("resolved_provider") == snapshot.provider
                and result.get("resolved_model") == snapshot.model
                and _VISION_PROBE_MARKER in analysis
            )
            if not ok:
                error_code = "vision_probe_failed"
                message = "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"
        except Exception:
            logger.warning("Vision configuration probe failed (%s)", diagnostic_id)
            error_code = "vision_probe_failed"
            message = "识图验证失败，请检查网络、密钥、模型和账号状态后重试。"
    finally:
        reset_hermes_config_path_override(config_path_token)
        reset_hermes_home_override(home_token)

    status = "verified" if ok else "failed"
    state = {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "fingerprint": snapshot.fingerprint,
        "status": status,
        "checked_at": checked_at,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    with credential_transaction(snapshot.config_path):
        with _cfg_lock:
            active_config_path = Path(_get_config_path())
            current_snapshot = (
                _capture_vision_config_snapshot_unlocked(
                    config_path=snapshot.config_path
                )
                if active_config_path == snapshot.config_path
                else None
            )
            with _vision_profile_lock(snapshot.profile):
                state_path = _vision_verification_state_path(
                    snapshot.profile
                )
                current_generation = _VISION_PROBE_GENERATIONS.get(
                    snapshot.profile
                )
                with _verification_state_file_lock(state_path):
                    still_current = bool(
                        current_snapshot == snapshot
                        and _commit_owned_verification_result(
                            state_path,
                            generation=generation,
                            current_generation=current_generation or 0,
                            fingerprint=snapshot.fingerprint,
                            diagnostic_id=diagnostic_id,
                            state=state,
                        )
                    )
                    if not still_current:
                        _discard_owned_verifying_state(
                            state_path,
                            generation=generation,
                            current_generation=current_generation or 0,
                            fingerprint=snapshot.fingerprint,
                            diagnostic_id=diagnostic_id,
                        )
    if not still_current:
        return _vision_test_response(
            ok=False,
            status="configured_unverified",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="vision_probe_superseded",
            message="识图配置在验证期间已变更，本次结果已忽略，请重新测试。",
            diagnostic_id=diagnostic_id,
        )
    warnings = _invoke_durable_mutation_post_commit("test_vision_config")
    return _merge_post_commit_warnings(
        _vision_test_response(
            ok=ok,
            status=status,
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code=error_code,
            message=message,
            diagnostic_id=diagnostic_id,
        ),
        warnings,
    )


_IMAGE_GEN_STATE_LOCKS_GUARD = threading.Lock()
_IMAGE_GEN_STATE_LOCKS: dict[str, threading.Lock] = {}
_IMAGE_GEN_PROBE_GENERATIONS: dict[str, int] = {}


@dataclass(frozen=True)
class _ImageGenConfigSnapshot:
    config_path: Path
    runtime_home: Path
    profile: str
    provider: str
    model: str
    credential_ref: str
    endpoint_mode: str
    region: str
    workspace_id: str
    base_url: str
    configured: bool
    effective_config_resolved: bool
    fingerprint: str
    request_local_provider: Any | None = dataclass_field(
        default=None,
        compare=False,
        repr=False,
    )
    probe_binding: ImageGenRequestBinding | None = dataclass_field(
        default=None,
        compare=False,
        repr=False,
    )


def _image_gen_verification_state_root() -> Path:
    from api.config import STATE_DIR

    return Path(STATE_DIR) / "image-gen-verification"


def _image_gen_verification_state_path(profile: str | None = None) -> Path:
    profile_name = str(profile or _active_profile_name() or "default")
    return verification_state_path(_image_gen_verification_state_root(), profile_name)


def _image_gen_profile_lock(profile: str) -> threading.Lock:
    with _IMAGE_GEN_STATE_LOCKS_GUARD:
        lock = _IMAGE_GEN_STATE_LOCKS.get(profile)
        if lock is None:
            lock = threading.Lock()
            _IMAGE_GEN_STATE_LOCKS[profile] = lock
        return lock


def _begin_image_gen_probe(profile: str, state: dict[str, Any]) -> int:
    with _image_gen_profile_lock(profile):
        state_path = _image_gen_verification_state_path(profile)
        with _verification_state_file_lock(state_path):
            disk_generation = _verification_state_generation(
                _read_verification_state_file(state_path)
            )
            generation = max(
                _IMAGE_GEN_PROBE_GENERATIONS.get(profile, 0),
                disk_generation,
            ) + 1
            _IMAGE_GEN_PROBE_GENERATIONS[profile] = generation
            generation_state = dict(state)
            generation_state["generation"] = generation
            _atomic_write_json(state_path, generation_state)
            return generation


def _read_image_gen_verification_state(profile: str) -> dict[str, Any]:
    with _image_gen_profile_lock(profile):
        try:
            data = json.loads(
                _image_gen_verification_state_path(profile).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}


def _capture_image_gen_verification_invalidation(
    profile: str | None = None,
) -> _VerificationInvalidationToken:
    committed_profile = str(profile or _active_profile_name() or "default")
    with _image_gen_profile_lock(committed_profile):
        state_path = _image_gen_verification_state_path(committed_profile)
        with _verification_state_file_lock(state_path):
            disk_generation = _verification_state_generation(
                _read_verification_state_file(state_path)
            )
            generation = max(
                _IMAGE_GEN_PROBE_GENERATIONS.get(committed_profile, 0),
                disk_generation,
            )
            _IMAGE_GEN_PROBE_GENERATIONS[committed_profile] = generation
            return _VerificationInvalidationToken(
                capability="image",
                profile=committed_profile,
                generation=generation,
                state_identity=_verification_state_file_identity(state_path),
            )


def _image_gen_secret_value(
    provider: str,
    credential_ref: str,
    config_data: dict[str, Any],
    *,
    config_path: Path | None = None,
) -> str:
    return shared_image_gen_secret_value(
        provider,
        credential_ref,
        config_data,
        config_path=config_path or _get_config_path(),
        allow_process_fallback=False,
    )


def _image_gen_config_fingerprint(
    image_cfg: dict[str, Any],
    *,
    profile: str,
    config_data: dict[str, Any] | None = None,
    resolved_material: Any | None = None,
    config_path: Path | None = None,
    secret_value: str | object = _UNSET_SECRET_VALUE,
) -> str:
    exact_config_path = Path(config_path or _get_config_path())
    data = config_data or load_credential_config(exact_config_path)
    provider = str(image_cfg.get("provider") or "").strip().lower()
    credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    if secret_value is _UNSET_SECRET_VALUE:
        secret_value = _image_gen_secret_value(
            provider,
            credential_ref,
            data,
            config_path=exact_config_path,
        )
    exact_secret_value = str(secret_value or "")
    if resolved_material is not None:
        return shared_image_gen_fingerprint_from_material(
            resolved_material,
            profile=profile,
            secret_value=exact_secret_value,
        )
    return shared_image_gen_fingerprint(
        image_cfg,
        profile=profile,
        config_data=data,
        secret_value=exact_secret_value,
    )


def _capture_image_gen_config_snapshot() -> _ImageGenConfigSnapshot:
    config_path = Path(_get_config_path())
    with credential_transaction(config_path):
        return _capture_image_gen_config_snapshot_unlocked(
            config_path=config_path
        )


def _capture_image_gen_config_snapshot_unlocked(
    *,
    config_path: Path | None = None,
    config_data: dict[str, Any] | None = None,
) -> _ImageGenConfigSnapshot:
    exact_config_path = Path(config_path or _get_config_path())
    profile = _active_profile_name()
    raw_config_data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config(exact_config_path)
    )
    raw_image_cfg = raw_config_data.get("image_gen")
    if not isinstance(raw_image_cfg, dict):
        raw_image_cfg = {}
    resolved_material = resolve_image_gen_material(
        raw_image_cfg,
        config_data=raw_config_data,
    )
    config_data = resolved_material.config_data
    image_cfg = resolved_material.image_cfg
    data_resolved = resolved_material.data_resolved
    cfg_resolved = resolved_material.cfg_resolved
    provider = str(image_cfg.get("provider") or "").strip().lower()
    model = str(image_cfg.get("model") or "").strip()
    request_local_provider = None
    if provider.startswith("custom:"):
        try:
            from agent.custom_image_providers import (
                build_configured_custom_image_provider,
            )

            request_local_provider = (
                build_configured_custom_image_provider(
                    provider,
                    config_data,
                )
            )
        except (ImportError, ValueError):
            request_local_provider = None
    effective_config_resolved = resolved_material.effective_config_resolved
    options = image_cfg.get("options")
    if not isinstance(options, dict):
        options = {}
    credential_ref = str(image_cfg.get("credential_ref") or "").strip()
    secret_value = _image_gen_secret_value(
        provider,
        credential_ref,
        config_data,
        config_path=exact_config_path,
    )
    credential_required = provider in _IMAGE_GEN_KEY_ENV or provider.startswith("custom:")
    custom_complete = bool(
        not provider.startswith("custom:")
        or request_local_provider is not None
    )
    endpoint_mode = str(options.get("endpoint_mode") or "").strip()
    endpoint_complete = bool(
        endpoint_mode not in {"workspace", "custom"}
        or (
            endpoint_mode == "workspace"
            and str(options.get("workspace_id") or "").strip()
        )
        or (
            endpoint_mode == "custom"
            and str(options.get("base_url") or "").strip()
        )
    )
    configured = bool(
        data_resolved
        and cfg_resolved
        and provider
        and model
        and custom_complete
        and endpoint_complete
        and (not credential_required or secret_value)
    )
    fingerprint = _image_gen_config_fingerprint(
        image_cfg,
        profile=profile,
        config_data=config_data,
        resolved_material=resolved_material,
        config_path=exact_config_path,
        secret_value=secret_value,
    )
    provider_config = active_custom_provider_identity(
        provider,
        config_data,
    )
    probe_binding = (
        ImageGenRequestBinding(
            provider=provider,
            model=model,
            api_key=secret_value,
            runtime_identity=resolved_material.runtime_identity,
            _provider_config=provider_config,
        )
        if configured and effective_config_resolved
        else None
    )
    return _ImageGenConfigSnapshot(
        config_path=exact_config_path,
        runtime_home=Path(_get_hermes_home()),
        profile=profile,
        provider=provider,
        model=model,
        credential_ref=credential_ref,
        endpoint_mode=endpoint_mode,
        region=str(options.get("region") or "").strip(),
        workspace_id=str(options.get("workspace_id") or "").strip(),
        base_url=str(options.get("base_url") or "").strip().rstrip("/"),
        configured=configured,
        effective_config_resolved=effective_config_resolved,
        fingerprint=fingerprint,
        request_local_provider=request_local_provider,
        probe_binding=probe_binding,
    )


def _image_gen_preflight_failure(
    snapshot: _ImageGenConfigSnapshot,
) -> tuple[str, str, str] | None:
    if (
        image_gen_provider_target(snapshot.provider)
        and not snapshot.effective_config_resolved
    ):
        return (
            "configured_unverified",
            "unresolved_effective_config",
            "生图配置包含未解析或不受支持的运行时端点，请修正后重新验证。",
        )
    if not snapshot.configured:
        return (
            "unconfigured",
            "image_gen_not_configured",
            "请先保存完整的生图 Provider、模型和凭据配置。",
        )
    return None


def _public_image_gen_verification(
    image_cfg: dict[str, Any],
    *,
    profile: str,
    snapshot: _ImageGenConfigSnapshot | None = None,
) -> dict[str, Any]:
    exact_snapshot = snapshot or _capture_image_gen_config_snapshot()
    preflight_failure = _image_gen_preflight_failure(exact_snapshot)
    if preflight_failure is not None:
        status, error_code, message = preflight_failure
        return {
            "status": status,
            "checked_at": "",
            "error_code": error_code,
            "message": message,
            "diagnostic_id": "",
        }
    state = _read_image_gen_verification_state(profile)
    fingerprint = exact_snapshot.fingerprint
    persisted_status = verification_status_from_state(
        state, expected_fingerprint=fingerprint
    )
    if persisted_status in {"verifying", "verified", "failed"}:
        return {
            "status": persisted_status,
            "checked_at": str(state.get("checked_at") or ""),
            "error_code": str(state.get("error_code") or ""),
            "message": str(state.get("message") or ""),
            "diagnostic_id": str(state.get("diagnostic_id") or ""),
        }
    return {
        "status": "configured_unverified",
        "checked_at": "",
        "error_code": "",
        "message": "生图配置已保存，但尚未通过真实生成验证。",
        "diagnostic_id": "",
    }


def _image_gen_test_response(
    *,
    ok: bool,
    status: str,
    checked_at: str,
    provider: str,
    model: str,
    error_code: str,
    message: str,
    diagnostic_id: str,
) -> dict[str, Any]:
    response = {
        "ok": bool(ok),
        "status": status,
        "checked_at": checked_at,
        "provider": provider,
        "model": model,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    return {key: response[key] for key in _IMAGE_GEN_VERIFICATION_PUBLIC_FIELDS}


@dataclass(frozen=True)
class _ImageCacheSnapshot:
    root: Path
    lexical_root: Path
    paths: frozenset[Path]
    inodes: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class _OwnedProbeImage:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class _ProbeCleanupCandidate:
    path: Path
    device: int
    inode: int
    file_type: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _probe_cleanup_candidate_matches(
    candidate: _ProbeCleanupCandidate,
    info: os.stat_result,
) -> bool:
    return bool(
        (info.st_dev, info.st_ino) == (candidate.device, candidate.inode)
        and stat.S_IFMT(info.st_mode) == candidate.file_type
        and info.st_size == candidate.size
        and info.st_mtime_ns == candidate.mtime_ns
        and info.st_ctime_ns == candidate.ctime_ns
    )


def _snapshot_image_cache() -> _ImageCacheSnapshot:
    lexical_root = Path(
        os.path.abspath(_get_hermes_home() / "cache" / "images")
    )
    root = lexical_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    inodes: set[tuple[int, int]] = set()
    for current_root, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(current_root) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            paths.add(path)
            inodes.add((info.st_dev, info.st_ino))
    return _ImageCacheSnapshot(
        root=root,
        lexical_root=lexical_root,
        paths=frozenset(paths),
        inodes=frozenset(inodes),
    )


def _probe_cleanup_candidate(
    raw_path: Any,
    before: _ImageCacheSnapshot,
) -> _ProbeCleanupCandidate | None:
    value = str(raw_path or "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None
    requested = Path(os.path.abspath(candidate))
    try:
        relative = requested.relative_to(before.lexical_root)
    except ValueError:
        try:
            relative = requested.relative_to(before.root)
        except ValueError:
            return None
    lexical = before.root / relative
    if lexical in before.paths:
        return None
    current = before.root
    try:
        for part in relative.parts[:-1]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return None
        info = lexical.lstat()
    except OSError:
        return None
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        return None
    return _ProbeCleanupCandidate(
        path=lexical,
        device=info.st_dev,
        inode=info.st_ino,
        file_type=stat.S_IFMT(info.st_mode),
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
    )


def _owned_probe_image(
    candidate: _ProbeCleanupCandidate | None,
    before: _ImageCacheSnapshot,
) -> _OwnedProbeImage | None:
    if candidate is None or candidate.file_type != stat.S_IFREG:
        return None
    try:
        info = candidate.path.lstat()
        resolved = candidate.path.resolve(strict=True)
        resolved.relative_to(before.root)
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        not _probe_cleanup_candidate_matches(candidate, info)
        or (info.st_dev, info.st_ino) in before.inodes
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        return None
    return _OwnedProbeImage(path=resolved, device=info.st_dev, inode=info.st_ino)


def _owned_probe_image_header(image: _OwnedProbeImage) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(image.path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (image.device, image.inode)
        ):
            return b""
        return os.read(descriptor, 12)
    finally:
        os.close(descriptor)


def _has_safe_image_header(image: _OwnedProbeImage) -> bool:
    try:
        header = _owned_probe_image_header(image)
    except OSError:
        return False
    return bool(
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def _remove_probe_cleanup_candidate(candidate: _ProbeCleanupCandidate) -> bool:
    try:
        info = candidate.path.lstat()
        if not _probe_cleanup_candidate_matches(candidate, info):
            return False
        candidate.path.unlink()
        return True
    except OSError:
        return False


def _execute_image_gen_probe(
    snapshot: _ImageGenConfigSnapshot,
    *,
    diagnostic_id: str,
    reauth_guard: Any,
    probe_binding: ImageGenRequestBinding,
) -> tuple[bool, str, str]:
    ok = False
    error_code = "image_gen_probe_failed"
    message = "生图验证失败，请检查网络、凭据、模型和账号状态后重试。"
    generated_image: _OwnedProbeImage | None = None
    cleanup_candidate: _ProbeCleanupCandidate | None = None
    try:
        cache_before = _snapshot_image_cache()
        if snapshot.provider.startswith("custom:"):
            _ensure_image_gen_plugins_registered(include_custom=False)
            selected = snapshot.request_local_provider
        else:
            _ensure_image_gen_plugins_registered()
            from agent.image_gen_registry import get_provider

            selected = get_provider(snapshot.provider)
        binding_aware = bool(
            selected
            and getattr(
                selected,
                "_supports_pinned_image_request_binding",
                False,
            )
        )
        legacy_test_seam = bool(
            selected
            and getattr(
                selected,
                "_allow_legacy_image_probe_test_seam",
                False,
            )
        )
        can_attempt = bool(
            selected
            and (
                probe_binding is not None
                if binding_aware
                else (
                    selected.is_available()
                    if legacy_test_seam
                    else False
                )
            )
        )
        if not can_attempt:
            error_code = "image_gen_provider_unavailable"
            message = "生图配置已保存，但当前 Provider 或凭据暂不可用。"
            result = None
        else:
            probe_kwargs = {
                "prompt": _IMAGE_GEN_PROBE_PROMPT,
                "aspect_ratio": "square",
                "num_images": 1,
                "model": snapshot.model,
            }
            if binding_aware:
                probe_kwargs["_runtime_binding"] = probe_binding
                probe_kwargs["_reauth_guard"] = reauth_guard
            result = selected.generate(
                **probe_kwargs,
            )
        if isinstance(result, dict):
            cleanup_candidate = _probe_cleanup_candidate(
                result.get("image"),
                cache_before,
            )
            generated_image = _owned_probe_image(
                cleanup_candidate,
                cache_before,
            )
            provider_error = str(result.get("error") or "").lower()
            if (
                result.get("success") is False
                and str(result.get("error_type") or "").lower() == "io_error"
                and (
                    "failed safety validation" in provider_error
                    or "private/internal address" in provider_error
                )
            ):
                error_code = "image_gen_result_url_blocked"
                message = (
                    "生图服务已返回图片结果，但下载被本机网络安全策略拦截。"
                    "请检查代理或 DNS 是否把图片域名映射到保留网段后重试。"
                )
            identity_ok = bool(
                result.get("success") is True
                and result.get("provider") == snapshot.provider
                and result.get("model") == snapshot.model
            )
            if identity_ok and generated_image is None:
                error_code = "image_gen_invalid_file"
            elif identity_ok and not _has_safe_image_header(generated_image):
                error_code = "image_gen_invalid_file"
            elif identity_ok:
                ok = True
                error_code = ""
                message = "生图验证通过，当前配置已完成真实生成探测。"
    except Exception:
        logger.warning(
            "Image generation configuration probe failed (%s)",
            diagnostic_id,
        )
    finally:
        if cleanup_candidate is not None:
            removed = _remove_probe_cleanup_candidate(cleanup_candidate)
            if not removed:
                logger.warning(
                    "Failed to remove image generation probe output (%s)",
                    diagnostic_id,
                )
                ok = False
                error_code = "image_gen_cleanup_failed"
                message = "生图验证未能安全清理测试图片，请检查本地文件权限后重试。"
    return ok, error_code, message


def test_image_gen_config(
    *,
    snapshot: _ImageGenConfigSnapshot | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        reload_config()
        config_path = Path(_get_config_path())
        with credential_transaction(config_path):
            config_data = load_credential_config(config_path)
            raw_image_cfg = config_data.get("image_gen")
            if not isinstance(raw_image_cfg, dict):
                raw_image_cfg = {}
            explicitly_disabled = raw_image_cfg.get("enabled") is False
            disabled_provider = str(
                raw_image_cfg.get("provider") or ""
            ).strip().lower()
            disabled_model = str(raw_image_cfg.get("model") or "").strip()
            snapshot = (
                None
                if explicitly_disabled
                else _capture_image_gen_config_snapshot()
            )
    else:
        explicitly_disabled = False
        disabled_provider = snapshot.provider
        disabled_model = snapshot.model
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    diagnostic_id = uuid.uuid4().hex
    if explicitly_disabled:
        return _image_gen_test_response(
            ok=False,
            status="disabled",
            checked_at=checked_at,
            provider=disabled_provider,
            model=disabled_model,
            error_code="capability_disabled",
            message="图片生成已停用，未执行真实生成探测。",
            diagnostic_id=diagnostic_id,
        )
    assert snapshot is not None
    preflight_failure = _image_gen_preflight_failure(snapshot)
    if preflight_failure is None and snapshot.probe_binding is None:
        preflight_failure = (
            "configured_unverified",
            "image_gen_provider_unavailable",
            "生图配置已保存，但当前 Provider 或凭据暂不可用。",
        )
    if preflight_failure is not None:
        status, error_code, message = preflight_failure
        return _image_gen_test_response(
            ok=False,
            status=status,
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code=error_code,
            message=message,
            diagnostic_id=diagnostic_id,
        )

    verifying_state = {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "fingerprint": snapshot.fingerprint,
        "status": "verifying",
        "checked_at": checked_at,
        "error_code": "",
        "message": "正在执行真实生图测试，可能产生少量费用。",
        "diagnostic_id": diagnostic_id,
    }
    generation = _begin_image_gen_probe(snapshot.profile, verifying_state)
    verifying_snapshot = _verification_probe_runtime_snapshot(
        verifying_state,
        generation=generation,
        capability="image_generation",
        provider=snapshot.provider,
        model=snapshot.model,
    )
    probe_binding = authorize_image_gen_request_binding(
        snapshot.probe_binding,
        authorization_fingerprint=verifying_snapshot["fingerprint"],
        authorization_generation=verifying_snapshot[
            "_authorization_generation"
        ],
    )
    reauth_guard = build_image_gen_request_reauth_guard(
        probe_binding,
        expected_snapshot=verifying_snapshot,
    )

    from hermes_constants import (
        reset_hermes_config_path_override,
        reset_hermes_home_override,
        set_hermes_config_path_override,
        set_hermes_home_override,
    )

    home_token = set_hermes_home_override(snapshot.runtime_home)
    config_path_token = set_hermes_config_path_override(
        snapshot.config_path
    )
    try:
        ok, error_code, message = _execute_image_gen_probe(
            snapshot,
            diagnostic_id=diagnostic_id,
            reauth_guard=reauth_guard,
            probe_binding=probe_binding,
        )
    finally:
        reset_hermes_config_path_override(config_path_token)
        reset_hermes_home_override(home_token)

    status = "verified" if ok else "failed"
    state = {
        "schema_version": CAPABILITY_VERIFICATION_SCHEMA_VERSION,
        "fingerprint": snapshot.fingerprint,
        "status": status,
        "checked_at": checked_at,
        "error_code": error_code,
        "message": message,
        "diagnostic_id": diagnostic_id,
    }
    with credential_transaction(snapshot.config_path):
        with _cfg_lock:
            active_config_path = Path(_get_config_path())
            current_snapshot = (
                _capture_image_gen_config_snapshot_unlocked(
                    config_path=snapshot.config_path
                )
                if active_config_path == snapshot.config_path
                else None
            )
            with _image_gen_profile_lock(snapshot.profile):
                state_path = _image_gen_verification_state_path(
                    snapshot.profile
                )
                current_generation = _IMAGE_GEN_PROBE_GENERATIONS.get(
                    snapshot.profile
                )
                with _verification_state_file_lock(state_path):
                    still_current = bool(
                        current_snapshot == snapshot
                        and _commit_owned_verification_result(
                            state_path,
                            generation=generation,
                            current_generation=current_generation or 0,
                            fingerprint=snapshot.fingerprint,
                            diagnostic_id=diagnostic_id,
                            state=state,
                        )
                    )
                    if not still_current:
                        _discard_owned_verifying_state(
                            state_path,
                            generation=generation,
                            current_generation=current_generation or 0,
                            fingerprint=snapshot.fingerprint,
                            diagnostic_id=diagnostic_id,
                        )
    if not still_current:
        return _image_gen_test_response(
            ok=False,
            status="configured_unverified",
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code="image_gen_probe_superseded",
            message="生图配置在验证期间已变更，本次结果已忽略，请重新测试。",
            diagnostic_id=diagnostic_id,
        )
    warnings = _invoke_durable_mutation_post_commit(
        "test_image_gen_config"
    )
    return _merge_post_commit_warnings(
        _image_gen_test_response(
            ok=ok,
            status=status,
            checked_at=checked_at,
            provider=snapshot.provider,
            model=snapshot.model,
            error_code=error_code,
            message=message,
            diagnostic_id=diagnostic_id,
        ),
        warnings,
    )


def _vision_provider_rows(
    active_provider: str,
    vision_cfg: dict[str, Any] | None = None,
    *,
    config_data: dict[str, Any] | None = None,
    config_path: Path | None = None,
    allow_process_fallback: bool | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    active = str(active_provider or "").strip().lower()
    for pid, meta in _VISION_PROVIDER_META.items():
        key_status = _vision_key_status(
            pid,
            vision_cfg if pid == active else None,
            config_data=config_data,
            config_path=config_path,
            allow_process_fallback=allow_process_fallback,
        )
        requires_base_url = bool(meta.get("requires_base_url"))
        active_base_url = str((vision_cfg or {}).get("base_url") or "").strip() if pid == active else ""
        fields = []
        env_var = _VISION_KEY_ENV.get(pid, "")
        if env_var:
            fields.append(
                credential_field(
                    name="api_key",
                    env_var=env_var,
                    label="API Key",
                )
            )
        endpoint_fields = [
            dict(field)
            for field in (meta.get("endpoint_fields") or [])
            if isinstance(field, dict)
        ]
        contract = normalized_setup_contract(
            {
                "auth_type": meta.get("auth_type", "api_key"),
                "transport": meta.get("transport", ""),
                "credential_fields": fields + endpoint_fields,
            },
            provider_family=provider_family(pid),
            capabilities=("vision",),
            transport=str(meta.get("transport") or ""),
            models=meta.get("models") or [],
        )
        rows.append(
            {
                "id": pid,
                "name": str(meta.get("name") or pid),
                "description": str(meta.get("description") or ""),
                "active": pid == active,
                "available": bool(key_status.get("configured") and (not requires_base_url or active_base_url)),
                "key_status": key_status,
                "requires_env": [_VISION_KEY_ENV[pid]] if pid in _VISION_KEY_ENV else [],
                "requires_base_url": requires_base_url,
                "models": list(meta.get("models") or []),
                "default_model": str(meta.get("default_model") or ""),
                **contract,
            }
        )
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_public_row,
            load_custom_vision_provider_entries,
        )

        custom_rows = [
            custom_vision_provider_public_row(
                entry,
                active_provider=active,
                config_path=config_path,
                allow_process_fallback=allow_process_fallback,
            )
            for entry in load_custom_vision_provider_entries(
                config_data
                if isinstance(config_data, dict)
                else load_credential_config(_get_config_path())
            )
        ]
    except ImportError:
        custom_rows = []
    for row in custom_rows:
        row["key_status"] = _vision_key_status(
            str(row.get("id") or ""),
            vision_cfg if str(row.get("id") or "") == active else None,
            config_data=config_data,
            config_path=config_path,
            allow_process_fallback=allow_process_fallback,
        )
        row["available"] = bool(row["key_status"].get("configured"))
        custom_contract = normalized_setup_contract(
            {
                "auth_type": "api_key",
                "transport": row.get("transport") or "openai_chat_completions",
                "credential_fields": [
                    credential_field(
                        name="api_key",
                        env_var=str(row["key_status"].get("env_var") or ""),
                        label="API Key",
                    )
                ],
            },
            provider_family=str(row.get("id") or ""),
            capabilities=("vision",),
            transport=str(row.get("transport") or "openai_chat_completions"),
            models=row.get("models") or [],
        )
        row.update(custom_contract)
        rows.append(row)
    custom_ids = {str(row.get("id") or "") for row in custom_rows}
    if active and active not in _VISION_PROVIDER_META and active not in custom_ids and active != "auto":
        key_status = _vision_key_status(
            active,
            vision_cfg,
            config_data=config_data,
            config_path=config_path,
            allow_process_fallback=allow_process_fallback,
        )
        legacy_contract = normalized_setup_contract(
            {"credential_fields": []},
            provider_family=provider_family(active),
            capabilities=("vision",),
            transport="legacy_vision",
        )
        rows.append(
            {
                "id": active,
                "name": _PROVIDER_DISPLAY.get(active, active),
                "description": "当前配置中的视觉模型",
                "active": True,
                "available": bool(key_status.get("configured")),
                "key_status": key_status,
                "requires_env": [key_status.get("env_var")] if key_status.get("env_var") else [],
                "requires_base_url": False,
                "models": [],
                "default_model": "",
                **legacy_contract,
            }
        )
    return rows


def get_vision_config() -> dict[str, Any]:
    with credential_transaction(_get_config_path()):
        return _get_vision_config_unlocked()


def _get_vision_config_unlocked(
    *,
    refresh_runtime: bool = True,
    config_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if refresh_runtime:
        reload_config()
    config_path = Path(_get_config_path())
    exact_config_data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config(config_path)
    )
    auxiliary = exact_config_data.get("auxiliary")
    vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
    snapshot = _capture_vision_config_snapshot_unlocked(
        config_path=config_path,
        config_data=exact_config_data,
    )
    provider = snapshot.provider
    model = snapshot.model
    enabled = (
        vision_cfg["enabled"]
        if isinstance(vision_cfg.get("enabled"), bool)
        else bool(provider and model)
    )
    explicitly_disabled = vision_cfg.get("enabled") is False
    base_url = snapshot.base_url
    api_mode = snapshot.api_mode
    key_status = _vision_key_status(
        provider,
        vision_cfg,
        config_data=exact_config_data,
        config_path=config_path,
        allow_process_fallback=False,
    )
    meta = _VISION_PROVIDER_META.get(provider) or {}
    endpoint_values: dict[str, str] = {}
    for field in meta.get("endpoint_fields") or []:
        if not isinstance(field, dict) or bool(field.get("secret")):
            continue
        name = str(field.get("name") or "").strip()
        if name and name in vision_cfg:
            endpoint_values[name] = str(vision_cfg.get(name) or "").strip()
    verification = (
        _public_vision_verification(
            vision_cfg,
            key_status,
            profile=snapshot.profile,
            config_data=exact_config_data,
            snapshot=snapshot,
        )
        if not explicitly_disabled
        else {
            "status": "disabled",
            "checked_at": "",
            "error_code": "",
            "message": "看图识别已停用，原配置和凭据仍保留。",
            "diagnostic_id": "",
        }
    )
    return {
        "ok": True,
        "profile": snapshot.profile,
        "config": _public_config_summary(config_path),
        "vision": {
            "enabled": enabled,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_mode": api_mode,
            "credential_ref": str(vision_cfg.get("credential_ref") or "").strip(),
            "endpoint_mode": str(vision_cfg.get("endpoint_mode") or "").strip(),
            "region": str(vision_cfg.get("region") or "").strip(),
            "workspace_id": str(vision_cfg.get("workspace_id") or "").strip(),
            "endpoint_values": endpoint_values,
            "key_status": key_status,
            "verification": verification,
        },
        "providers": _vision_provider_rows(
            provider,
            vision_cfg,
            config_data=exact_config_data,
            config_path=config_path,
            allow_process_fallback=False,
        ),
    }


def set_vision_config(body: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(body.get("provider") or "").strip().lower()
    requested_model_id = str(body.get("model") or "").strip()
    model_id = requested_model_id
    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    api_key = body.get("api_key")
    credential_ref = str(body.get("credential_ref") or "").strip()
    requested_enabled = body.get("enabled") if "enabled" in body else None
    if requested_enabled is not None and not isinstance(requested_enabled, bool):
        raise ValueError("enabled must be a boolean")
    if not provider_id:
        raise ValueError("provider is required")
    named_custom_entry: dict[str, Any] | None = None
    if provider_id.startswith("custom:"):
        try:
            from agent.custom_vision_providers import find_custom_vision_provider_entry

            named_custom_entry = find_custom_vision_provider_entry(provider_id)
        except ImportError:
            named_custom_entry = None
    if provider_id not in _VISION_PROVIDER_META and named_custom_entry is None:
        raise ValueError(f"unknown vision provider: {provider_id}")
    if (
        provider_id == "alibaba"
        and credential_ref
        and api_key is not None
        and str(api_key).strip()
    ):
        raise ValueError("credential_ref and api_key cannot be used together")

    meta = _VISION_PROVIDER_META.get(provider_id) or {
        "default_model": named_custom_entry["default_model"],
        "models": [{"id": item} for item in named_custom_entry["models"]],
    }
    schema_endpoint_fields = [
        field
        for field in (meta.get("endpoint_fields") or [])
        if isinstance(field, dict) and not bool(field.get("secret"))
    ]
    endpoint_updates: dict[str, str] = {}
    for field in schema_endpoint_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        default_value = {"endpoint_mode": "public", "region": "cn-beijing"}.get(name, "")
        value = str(body.get(name) or default_value).strip()
        if bool(field.get("required")) and not value:
            raise ValueError(f"{name} is required")
        endpoint_updates[name] = value
    if not model_id:
        model_id = str(meta.get("default_model") or "").strip()
        models = meta.get("models") if isinstance(meta.get("models"), list) else []
        if not model_id and models:
            model_id = str((models[0] or {}).get("id") or "").strip()
    if not model_id:
        raise ValueError("model is required")
    _validate_provider_model_choice(
        provider_id,
        model_id,
        meta,
        capability="vision",
    )
    if provider_id == "alibaba":
        endpoint_mode = str(body.get("endpoint_mode") or "public").strip().lower()
        region = str(body.get("region") or "cn-beijing").strip().lower()
        workspace_id = str(body.get("workspace_id") or "").strip().lower()
        base_url = build_vision_base_url(
            endpoint_mode=endpoint_mode,
            region=region,
            workspace_prefix=workspace_id,
            custom_url=base_url,
        )
        canonical_endpoint_values = {
            "endpoint_mode": endpoint_mode,
            "region": region,
            "workspace_id": workspace_id,
            "base_url": base_url,
        }
        for name in tuple(endpoint_updates):
            if name in canonical_endpoint_values:
                endpoint_updates[name] = canonical_endpoint_values[name]
    elif named_custom_entry is not None:
        if api_key is not None and str(api_key).strip():
            raise ValueError("命名式外部识图密钥请在 Provider 管理中更新。")
    if bool(meta.get("requires_base_url")) and not base_url:
        raise ValueError("base_url is required for custom vision provider")

    config_path = _get_config_path()
    with credential_transaction(config_path):
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            if provider_id.startswith("custom:"):
                locked_custom_entry = find_custom_vision_provider_entry(
                    provider_id,
                    config_data,
                )
                if locked_custom_entry is None:
                    raise ValueError(f"unknown vision provider: {provider_id}")
                locked_meta = {
                    "default_model": locked_custom_entry["default_model"],
                    "models": [
                        {"id": item}
                        for item in locked_custom_entry["models"]
                    ],
                }
                locked_model_id = requested_model_id
                if not locked_model_id:
                    locked_model_id = str(
                        locked_meta.get("default_model") or ""
                    ).strip()
                    locked_models = (
                        locked_meta.get("models")
                        if isinstance(locked_meta.get("models"), list)
                        else []
                    )
                    if not locked_model_id and locked_models:
                        locked_model_id = str(
                            (locked_models[0] or {}).get("id") or ""
                        ).strip()
                if not locked_model_id:
                    raise ValueError("model is required")
                model_id = _validate_provider_model_choice(
                    provider_id,
                    locked_model_id,
                    locked_meta,
                    capability="vision",
                )
                named_custom_entry = locked_custom_entry
            original_config = copy.deepcopy(config_data)
            if provider_id == "alibaba" and not credential_ref and not str(api_key or "").strip():
                credential_ref = default_credential_ref(
                    provider_id,
                    config_data=config_data,
                    config_path=config_path,
                    allow_process_fallback=False,
                )
            if credential_ref:
                credential_ref = normalize_credential_id(credential_ref)
            if credential_ref:
                row = load_credential(credential_ref, config_data=config_data)
                if provider_family(row.get("provider_family")) != provider_family(provider_id):
                    raise ValueError("所选凭据不属于当前 Provider。")
            env_var = _VISION_KEY_ENV.get(provider_id)
            secret_value = str(api_key or "").strip()
            if secret_value and not env_var:
                raise ValueError(f"{provider_id} does not accept an API key from WebUI")
            auxiliary = config_data.get("auxiliary")
            if not isinstance(auxiliary, dict):
                auxiliary = {}
            vision_cfg = auxiliary.get("vision")
            if not isinstance(vision_cfg, dict):
                vision_cfg = {}
            previous_provider = str(vision_cfg.get("provider") or "").strip().lower()
            owned_names = {
                str(name).strip()
                for name in (vision_cfg.get("endpoint_field_names") or [])
                if str(name).strip()
            }
            previous_meta = _VISION_PROVIDER_META.get(previous_provider) or {}
            owned_names.update(
                str(field.get("name") or "").strip()
                for field in (previous_meta.get("endpoint_fields") or [])
                if isinstance(field, dict) and not bool(field.get("secret"))
            )
            owned_names.discard("")
            for name in owned_names:
                vision_cfg.pop(name, None)
            vision_cfg["provider"] = provider_id
            vision_cfg["model"] = model_id
            if requested_enabled is not None:
                vision_cfg["enabled"] = requested_enabled
            if provider_id == "alibaba":
                vision_cfg["credential_ref"] = credential_ref
                vision_cfg.pop("api_key", None)
                vision_cfg.pop("api_mode", None)
            elif provider_id == "custom":
                vision_cfg["base_url"] = base_url
                has_custom_key = (
                    (api_key is not None and str(api_key).strip())
                    or _key_status_for_env("AUXILIARY_VISION_API_KEY").get("configured")
                )
                if has_custom_key:
                    vision_cfg["api_key"] = "${AUXILIARY_VISION_API_KEY}"
                else:
                    vision_cfg.pop("api_key", None)
            else:
                vision_cfg.pop("base_url", None)
                vision_cfg.pop("api_key", None)
                vision_cfg.pop("api_mode", None)
            if provider_id != "alibaba":
                vision_cfg.pop("credential_ref", None)
            for name, value in endpoint_updates.items():
                if value:
                    vision_cfg[name] = value
                else:
                    vision_cfg.pop(name, None)
            if endpoint_updates:
                vision_cfg["endpoint_field_names"] = sorted(endpoint_updates)
            else:
                vision_cfg.pop("endpoint_field_names", None)
            auxiliary["vision"] = vision_cfg
            config_data["auxiliary"] = auxiliary
            bump_capability_config_epochs(
                config_data,
                CAPABILITY_CONFIG_EPOCH_VISION,
            )
            _commit_expected_config_env(
                config_path,
                expected_config=original_config,
                desired_config=config_data,
                env_updates=(
                    {env_var: secret_value}
                    if secret_value and env_var
                    else {}
                ),
            )
        vision_invalidation_token = (
            _capture_vision_verification_invalidation()
        )
        response = _get_vision_config_unlocked(refresh_runtime=False)
    warnings = _invoke_durable_mutation_post_commit(
        "set_vision_config",
        invalidate_vision=True,
        vision_invalidation_token=vision_invalidation_token,
    )
    response = _project_successful_verification_invalidation(
        response,
        warnings,
        capability="vision",
    )
    return _merge_post_commit_warnings(
        response,
        warnings,
    )


def set_alibaba_image_capabilities(body: dict[str, Any]) -> dict[str, Any]:
    """Configure Alibaba vision and image generation with one shared API key."""
    vision_model = str(body.get("vision_model") or "qwen3-vl-plus").strip()
    image_model = str(body.get("image_model") or "qwen-image-2.0-pro").strip()
    vision_models = {
        str(row.get("id") or "").strip()
        for row in _VISION_PROVIDER_META["alibaba"].get("models", [])
        if isinstance(row, dict)
    }
    image_models = {
        str(row.get("id") or "").strip()
        for row in _IMAGE_GEN_FALLBACK_META["dashscope"].get("models", [])
        if isinstance(row, dict)
    }
    if vision_model not in vision_models:
        raise ValueError(f"unknown Alibaba vision model: {vision_model}")
    if image_model not in image_models:
        raise ValueError(f"unknown Alibaba image model: {image_model}")

    requested_secret = str(body.get("api_key") or "").strip()
    config_path = _get_config_path()
    env_path = config_path.parent / ".env"
    with credential_transaction(config_path):
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            original_config = copy.deepcopy(config_data)
            previous_index, previous_row = _provider_credential_row(
                config_data, _ALIBABA_QUICK_CREDENTIAL_ID
            )
            if previous_row is not None:
                if provider_family(previous_row.get("provider_family")) != (
                    "alibaba_dashscope"
                ):
                    raise ValueError(
                        "快速配置保留凭据属于不同 Provider。"
                    )
                if (
                    str(previous_row.get("auth_type") or "api_key")
                    .strip()
                    .lower()
                    != "api_key"
                ):
                    raise ValueError("快速配置保留凭据必须使用 API Key。")
                _validate_provider_credential_secret_env(previous_row)

            env_values = _load_env_file(env_path)
            quick_secret = str(
                env_values.get(_ALIBABA_QUICK_CREDENTIAL_ENV)
                or os.getenv(_ALIBABA_QUICK_CREDENTIAL_ENV)
                or ""
            ).strip()
            legacy_secret = str(
                env_values.get("DASHSCOPE_API_KEY")
                or os.getenv("DASHSCOPE_API_KEY")
                or ""
            ).strip()
            secret_value = requested_secret or quick_secret or legacy_secret
            if not secret_value:
                raise ValueError("api_key is required")

            _replace_provider_credential_row(
                config_data,
                _ALIBABA_QUICK_CREDENTIAL_ID,
                dict(_ALIBABA_QUICK_CREDENTIAL_ROW),
                preferred_index=previous_index,
            )

            auxiliary = config_data.get("auxiliary")
            if not isinstance(auxiliary, dict):
                auxiliary = {}
            vision_cfg = auxiliary.get("vision")
            if not isinstance(vision_cfg, dict):
                vision_cfg = {}
            for name in (
                "credential_ref",
                "workspace_id",
                "api_key",
                "api_mode",
            ):
                vision_cfg.pop(name, None)
            vision_cfg.update(
                {
                    "provider": "alibaba",
                    "model": vision_model,
                    "credential_ref": _ALIBABA_QUICK_CREDENTIAL_ID,
                    "base_url": build_vision_base_url(
                        endpoint_mode="public", region="cn-beijing"
                    ),
                    "endpoint_mode": "public",
                    "region": "cn-beijing",
                    "endpoint_field_names": [
                        "base_url",
                        "endpoint_mode",
                        "region",
                    ],
                }
            )
            auxiliary["vision"] = vision_cfg
            config_data["auxiliary"] = auxiliary

            image_cfg = config_data.get("image_gen")
            if not isinstance(image_cfg, dict):
                image_cfg = {}
            image_cfg.pop("credential_ref", None)
            image_cfg.pop("api_key", None)
            options = image_cfg.get("options")
            if not isinstance(options, dict):
                options = {}
            options.pop("workspace_id", None)
            options.pop("base_url", None)
            options.update(
                {"endpoint_mode": "public", "region": "cn-beijing"}
            )
            image_cfg.update(
                {
                    "provider": "dashscope",
                    "model": image_model,
                    "use_gateway": False,
                    "credential_ref": _ALIBABA_QUICK_CREDENTIAL_ID,
                    "options": options,
                    "endpoint_field_names": ["endpoint_mode", "region"],
                }
            )
            config_data["image_gen"] = image_cfg
            bump_capability_config_epochs(
                config_data,
                CAPABILITY_CONFIG_EPOCH_VISION,
                CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
            )
            _commit_expected_config_env(
                config_path,
                expected_config=original_config,
                desired_config=config_data,
                env_updates={
                    _ALIBABA_QUICK_CREDENTIAL_ENV: secret_value,
                },
            )
            vision_invalidation_token = (
                _capture_vision_verification_invalidation()
            )
            image_invalidation_token = (
                _capture_image_gen_verification_invalidation()
            )
            key_status = copy.deepcopy(
                _key_status_for_env(_ALIBABA_QUICK_CREDENTIAL_ENV)
            )
            try:
                committed_credential_rows = copy.deepcopy(
                    _public_provider_credentials_config(config_data).get(
                        "credentials",
                        [],
                    )
                )
            except Exception:
                committed_credential_rows = None
            try:
                committed_vision_provider_rows = copy.deepcopy(
                    _vision_provider_rows(
                        "alibaba",
                        vision_cfg,
                        config_data=config_data,
                        config_path=Path(config_path),
                        allow_process_fallback=False,
                    )
                )
            except Exception:
                committed_vision_provider_rows = None
            try:
                committed_image_provider_rows = copy.deepcopy(
                    _image_gen_provider_rows("dashscope")
                )
            except Exception:
                committed_image_provider_rows = None

    warnings = _invoke_durable_mutation_post_commit(
        "set_alibaba_image_capabilities",
        invalidate_vision=True,
        invalidate_image=True,
        vision_invalidation_token=vision_invalidation_token,
        image_invalidation_token=image_invalidation_token,
    )

    response = {
        "ok": True,
        "refresh_pending": False,
        "warnings": [],
        "vision": {
            "provider": "alibaba",
            "model": vision_model,
            "credential_ref": _ALIBABA_QUICK_CREDENTIAL_ID,
            "base_url": build_vision_base_url(
                endpoint_mode="public", region="cn-beijing"
            ),
            "endpoint_mode": "public",
            "region": "cn-beijing",
            "key_status": key_status,
            "verification": {
                "status": "configured_unverified",
                "checked_at": "",
                "error_code": "",
                "message": "识图配置已保存，但尚未通过真实图片验证。",
                "diagnostic_id": "",
            },
        },
        "image_gen": {
            "provider": "dashscope",
            "model": image_model,
            "credential_ref": _ALIBABA_QUICK_CREDENTIAL_ID,
            "options": {
                "endpoint_mode": "public",
                "region": "cn-beijing",
            },
            "key_status": key_status,
            "verification": {
                "status": "configured_unverified",
                "checked_at": "",
                "error_code": "",
                "message": "生图配置已保存，但尚未通过真实生图验证。",
                "diagnostic_id": "",
            },
        },
    }

    fallback_credentials = [
        {
            "id": _ALIBABA_QUICK_CREDENTIAL_ID,
            "provider_family": "alibaba_dashscope",
            "label": "阿里百炼快速配置",
            "auth_type": "api_key",
            "default": False,
            "configured": bool(key_status.get("configured")),
            "used_by": ["auxiliary.vision", "image_gen"],
        }
    ]
    vision_meta = _VISION_PROVIDER_META["alibaba"]
    fallback_vision_providers = [
        {
            "id": "alibaba",
            "name": str(vision_meta.get("name") or "阿里百炼 Qwen-VL"),
            "description": str(vision_meta.get("description") or ""),
            "active": True,
            "available": False,
            "key_status": dict(key_status),
            "models": list(vision_meta.get("models") or []),
            "default_model": str(vision_meta.get("default_model") or ""),
        }
    ]
    image_meta = _IMAGE_GEN_FALLBACK_META["dashscope"]
    fallback_image_gen_providers = [
        {
            "id": "dashscope",
            "name": str(image_meta.get("name") or "通义 Qwen-Image"),
            "active": True,
            "available": False,
            "can_attempt": bool(key_status.get("configured")),
            "key_status": dict(key_status),
            "models": list(image_meta.get("models") or []),
            "default_model": str(image_meta.get("default_model") or ""),
        }
    ]

    try:
        if not isinstance(committed_credential_rows, list) or not any(
            isinstance(row, dict)
            and row.get("id") == _ALIBABA_QUICK_CREDENTIAL_ID
            for row in committed_credential_rows
        ):
            raise ValueError("reserved credential metadata is unavailable")
        response["provider_credentials"] = committed_credential_rows
    except Exception:
        logger.warning("Alibaba quick setup credential metadata refresh failed")
        warnings.append("provider_credentials_refresh_pending")
        response["provider_credentials"] = fallback_credentials

    try:
        if not isinstance(committed_vision_provider_rows, list) or not any(
            isinstance(row, dict) and row.get("id") == "alibaba"
            for row in committed_vision_provider_rows
        ):
            raise ValueError("Alibaba vision provider metadata is unavailable")
        response["vision_providers"] = committed_vision_provider_rows
    except Exception:
        logger.warning("Alibaba quick setup vision provider metadata refresh failed")
        warnings.append("vision_provider_metadata_refresh_pending")
        response["vision_providers"] = fallback_vision_providers

    try:
        if not isinstance(committed_image_provider_rows, list) or not any(
            isinstance(row, dict) and row.get("id") == "dashscope"
            for row in committed_image_provider_rows
        ):
            raise ValueError("DashScope image provider metadata is unavailable")
        response["image_gen_providers"] = committed_image_provider_rows
    except Exception:
        logger.warning("Alibaba quick setup image provider metadata refresh failed")
        warnings.append("image_gen_provider_metadata_refresh_pending")
        response["image_gen_providers"] = fallback_image_gen_providers

    return _merge_post_commit_warnings(response, warnings)


def _custom_vision_provider_rows_from_config(
    config_data: dict[str, Any],
    *,
    config_path: Path,
    env_path: Path,
) -> list[dict[str, Any]]:
    auxiliary = config_data.get("auxiliary")
    vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
    active_provider = str(
        (vision_cfg or {}).get("provider") or ""
    ).strip().lower()
    from agent.custom_vision_providers import (
        custom_vision_provider_public_row,
        load_custom_vision_provider_entries,
    )

    rows = [
        custom_vision_provider_public_row(
            entry,
            active_provider=active_provider,
            config_path=config_path,
            allow_process_fallback=False,
        )
        for entry in load_custom_vision_provider_entries(config_data)
    ]
    for row in rows:
        row["key_status"] = _key_status_for_env(
            row["key_status"]["env_var"],
            env_path=env_path,
        )
        row["available"] = bool(row["key_status"].get("configured"))
    return rows


def get_custom_vision_provider_configs() -> dict[str, Any]:
    config_path = Path(_get_config_path())
    with credential_transaction(config_path) as credential_spec:
        config_data = load_credential_config(credential_spec.config_target)
        try:
            rows = _custom_vision_provider_rows_from_config(
                config_data,
                config_path=credential_spec.config_target,
                env_path=credential_spec.env_target,
            )
        except ImportError:
            return {"ok": True, "providers": []}
        return {"ok": True, "providers": rows}


def set_custom_vision_provider_config(body: dict[str, Any]) -> dict[str, Any]:
    if "api_key_env" in body:
        raise ValueError(
            "外部识图 Provider 不允许配置 api_key_env，请使用 credential_ref。"
        )
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_env_var,
            custom_vision_provider_public_row,
            is_custom_vision_base_url_safe,
            normalize_custom_vision_provider_entry,
            normalize_custom_vision_provider_id,
        )
    except ImportError as exc:
        raise RuntimeError("custom vision provider support is unavailable") from exc

    requested_id = normalize_custom_vision_provider_id(
        body.get("id") or body.get("provider_id")
    )
    secret_value = str(body.get("api_key") or "").strip()
    config_path = Path(_get_config_path())
    with credential_transaction(config_path) as credential_spec:
        config_path = credential_spec.config_target
        env_path = credential_spec.env_target
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            existing_entries = config_data.get("custom_vision_providers")
            if not isinstance(existing_entries, list):
                existing_entries = []
            existing = {}
            for item in existing_entries:
                if not isinstance(item, dict):
                    continue
                try:
                    item_id = normalize_custom_vision_provider_id(item.get("id"))
                except ValueError:
                    continue
                if item_id == requested_id:
                    existing = item
                    break
            previous_ref = str(existing.get("credential_ref") or "").strip()
            legacy_env = ""
            if existing and not previous_ref:
                legacy_env = custom_vision_provider_env_var(requested_id)
                configured_env = str(existing.get("api_key_env") or "").strip()
                if configured_env and configured_env != legacy_env:
                    raise ValueError("外部识图 Provider 的旧密钥环境变量配置已损坏。")
            merged = dict(existing)
            merged.update(
                {
                    key: value
                    for key, value in body.items()
                    if key not in {"api_key", "api_key_env"}
                }
            )
            merged["id"] = requested_id
            credential_ref, secret_env = _custom_provider_credential_binding(
                config_data,
                requested_ref=str(merged.get("credential_ref") or ""),
                capability="custom_vision_provider",
                provider_id=requested_id,
                provider_label=str(merged.get("name") or requested_id).strip(),
            )
            merged["credential_ref"] = credential_ref
            merged.pop("api_key_env", None)
            normalized = normalize_custom_vision_provider_entry(merged)
            if (
                normalized["network_scope"] == "public_direct"
                and not is_custom_vision_base_url_safe(normalized["base_url"])
            ):
                raise ValueError("外部识图 Base URL 无法通过公网安全校验。")
            updated = []
            for item in existing_entries:
                if not isinstance(item, dict):
                    updated.append(item)
                    continue
                try:
                    item_id = normalize_custom_vision_provider_id(item.get("id"))
                except ValueError:
                    updated.append(item)
                    continue
                if item_id != requested_id:
                    updated.append(item)
            updated.append(normalized)
            config_data["custom_vision_providers"] = updated
            env_updates: dict[str, str | None] = {}
            migrated_secret = secret_value
            if legacy_env and not migrated_secret:
                migrated_secret = str(
                    _load_env_file(env_path).get(legacy_env)
                    or os.getenv(legacy_env)
                    or ""
                ).strip()
            if migrated_secret:
                env_updates[secret_env] = migrated_secret
            if legacy_env:
                env_updates[legacy_env] = None
            if previous_ref and previous_ref != credential_ref:
                orphaned_env = _remove_orphaned_managed_custom_credential(
                    config_data,
                    credential_ref=previous_ref,
                    capability="custom_vision_provider",
                    provider_id=requested_id,
                )
                if orphaned_env:
                    env_updates[orphaned_env] = None
            bump_capability_config_epochs(
                config_data,
                CAPABILITY_CONFIG_EPOCH_VISION,
            )
            _write_custom_provider_transaction(
                config_path=config_path,
                env_path=env_path,
                config_data=config_data,
                env_updates=env_updates,
            )
            row = custom_vision_provider_public_row(
                normalized,
                config_path=config_path,
                allow_process_fallback=False,
            )
            row["key_status"] = _key_status_for_env(
                secret_env,
                env_path=env_path,
            )
            row["available"] = bool(row["key_status"].get("configured"))
            response = {
                "ok": True,
                "provider": copy.deepcopy(row),
                "providers": _custom_vision_provider_rows_from_config(
                    config_data,
                    config_path=config_path,
                    env_path=env_path,
                ),
            }
            vision_invalidation_token = (
                _capture_vision_verification_invalidation()
            )
    warnings = _invoke_durable_mutation_post_commit(
        "set_custom_vision_provider_config",
        invalidate_vision=True,
        vision_invalidation_token=vision_invalidation_token,
    )
    return _merge_post_commit_warnings(
        response,
        warnings,
    )


def delete_custom_vision_provider_config(provider_id: str) -> dict[str, Any]:
    try:
        from agent.custom_vision_providers import (
            custom_vision_provider_env_var,
            custom_vision_provider_name,
            normalize_custom_vision_provider_id,
        )
    except ImportError as exc:
        raise RuntimeError("custom vision provider support is unavailable") from exc
    normalized_id = normalize_custom_vision_provider_id(provider_id)
    provider_name = custom_vision_provider_name(normalized_id)
    config_path = Path(_get_config_path())
    with credential_transaction(config_path) as credential_spec:
        config_path = credential_spec.config_target
        env_path = credential_spec.env_target
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            auxiliary = config_data.get("auxiliary")
            vision_cfg = auxiliary.get("vision") if isinstance(auxiliary, dict) else {}
            if str((vision_cfg or {}).get("provider") or "").strip().lower() == provider_name:
                raise ValueError("该外部识图 Provider 正在使用，请先切换识图配置。")
            entries = config_data.get("custom_vision_providers")
            if not isinstance(entries, list):
                entries = []
            updated = []
            removed = False
            removed_entry: dict[str, Any] = {}
            for item in entries:
                if not isinstance(item, dict):
                    updated.append(item)
                    continue
                try:
                    item_id = normalize_custom_vision_provider_id(item.get("id"))
                except ValueError:
                    updated.append(item)
                    continue
                if item_id == normalized_id:
                    removed = True
                    if not removed_entry:
                        removed_entry = item
                else:
                    updated.append(item)
            if not removed:
                raise ValueError("外部识图 Provider 不存在。")
            config_data["custom_vision_providers"] = updated
            legacy_env = ""
            if not str(removed_entry.get("credential_ref") or "").strip():
                legacy_env = custom_vision_provider_env_var(normalized_id)
            orphaned_env = _remove_orphaned_managed_custom_credential(
                config_data,
                credential_ref=str(removed_entry.get("credential_ref") or ""),
                capability="custom_vision_provider",
                provider_id=normalized_id,
            )
            bump_capability_config_epochs(
                config_data,
                CAPABILITY_CONFIG_EPOCH_VISION,
            )
            _write_custom_provider_transaction(
                config_path=config_path,
                env_path=env_path,
                config_data=config_data,
                env_updates={
                    env_var: None
                    for env_var in (orphaned_env, legacy_env)
                    if env_var
                },
            )
            response = {
                "ok": True,
                "providers": _custom_vision_provider_rows_from_config(
                    config_data,
                    config_path=config_path,
                    env_path=env_path,
                ),
            }
            vision_invalidation_token = (
                _capture_vision_verification_invalidation()
            )
    warnings = _invoke_durable_mutation_post_commit(
        "delete_custom_vision_provider_config",
        invalidate_vision=True,
        vision_invalidation_token=vision_invalidation_token,
    )
    return _merge_post_commit_warnings(
        response,
        warnings,
    )


def _custom_image_provider_rows_from_config(
    config_data: dict[str, Any],
    *,
    config_path: Path,
    env_path: Path,
) -> list[dict[str, Any]]:
    active_provider = ""
    image_cfg = config_data.get("image_gen")
    if isinstance(image_cfg, dict):
        active_provider = str(image_cfg.get("provider") or "").strip()
    from agent.custom_image_providers import (
        custom_image_provider_public_row,
        load_custom_image_provider_entries,
    )
    from hermes_constants import (
        reset_hermes_config_path_override,
        set_hermes_config_path_override,
    )

    config_path_token = set_hermes_config_path_override(config_path)
    try:
        rows = [
            custom_image_provider_public_row(
                entry,
                active_provider=active_provider,
            )
            for entry in load_custom_image_provider_entries(config_data)
        ]
    finally:
        reset_hermes_config_path_override(config_path_token)
    for row in rows:
        key_status = _key_status_for_env(
            (row.get("key_status") or {}).get("env_var"),
            env_path=env_path,
        )
        row["key_status"] = key_status
        configured = bool(
            key_status.get("configured")
            and row.get("base_url_configured")
            and row.get("default_model")
        )
        row["configured"] = configured
        row["available"] = False
        row["verification_status"] = (
            "configured_unverified" if configured else "not_configured"
        )
        row["reason_code"] = (
            "configured_unverified" if configured else "authorization_required"
        )
        row["status_message"] = (
            "已配置，尚未验证。"
            if configured
            else "外部图片模型密钥未配置。"
        )
    return rows


def get_custom_image_provider_configs() -> dict[str, Any]:
    config_path = Path(_get_config_path())
    with credential_transaction(config_path) as credential_spec:
        config_data = load_credential_config(credential_spec.config_target)
        try:
            rows = _custom_image_provider_rows_from_config(
                config_data,
                config_path=credential_spec.config_target,
                env_path=credential_spec.env_target,
            )
        except Exception:
            return {"ok": True, "providers": []}
        return {"ok": True, "providers": rows}


def set_custom_image_provider_config(body: dict[str, Any]) -> dict[str, Any]:
    if "api_key_env" in body:
        raise ValueError("外部图片模型不允许配置 api_key_env，请使用 credential_ref。")
    try:
        from agent.custom_image_providers import (
            custom_image_provider_env_var,
            normalize_custom_image_provider_entry,
            normalize_custom_image_provider_id,
        )
    except Exception as exc:
        raise RuntimeError("custom image provider support is unavailable") from exc

    requested_id = normalize_custom_image_provider_id(
        body.get("id") or body.get("provider_id")
    )
    config_path = Path(_get_config_path())
    secret_value = str(body.get("api_key") or "").strip()
    with credential_transaction(config_path) as credential_spec:
        config_path = credential_spec.config_target
        env_path = credential_spec.env_target
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            existing_entries = config_data.get("custom_image_providers")
            if not isinstance(existing_entries, list):
                existing_entries = []
            existing = {}
            for item in existing_entries:
                if not isinstance(item, dict):
                    continue
                try:
                    item_id = normalize_custom_image_provider_id(item.get("id"))
                except ValueError:
                    continue
                if item_id == requested_id:
                    existing = item
                    break
            previous_ref = str(existing.get("credential_ref") or "").strip()
            legacy_env = ""
            if existing and not previous_ref:
                legacy_env = custom_image_provider_env_var(requested_id)
                configured_env = str(existing.get("api_key_env") or "").strip()
                if configured_env and configured_env != legacy_env:
                    raise ValueError("外部图片模型的旧密钥环境变量配置已损坏。")
            merged = dict(existing)
            for key, value in body.items():
                if key not in {"api_key", "api_key_env"}:
                    merged[key] = value
            merged["id"] = requested_id
            credential_ref, secret_env = _custom_provider_credential_binding(
                config_data,
                requested_ref=str(merged.get("credential_ref") or ""),
                capability="custom_image_provider",
                provider_id=requested_id,
                provider_label=str(merged.get("name") or requested_id).strip(),
            )
            merged["credential_ref"] = credential_ref
            merged.pop("api_key_env", None)
            normalized = normalize_custom_image_provider_entry(merged)

            updated = []
            for item in existing_entries:
                if not isinstance(item, dict):
                    updated.append(item)
                    continue
                try:
                    item_id = normalize_custom_image_provider_id(item.get("id"))
                except ValueError:
                    updated.append(item)
                    continue
                if item_id != requested_id:
                    updated.append(item)
            updated.append(normalized)
            config_data["custom_image_providers"] = updated
            env_updates: dict[str, str | None] = {}
            migrated_secret = secret_value
            if legacy_env and not migrated_secret:
                migrated_secret = str(
                    _load_env_file(env_path).get(legacy_env)
                    or os.getenv(legacy_env)
                    or ""
                ).strip()
            if migrated_secret:
                env_updates[secret_env] = migrated_secret
            if legacy_env:
                env_updates[legacy_env] = None
            if previous_ref and previous_ref != credential_ref:
                orphaned_env = _remove_orphaned_managed_custom_credential(
                    config_data,
                    credential_ref=previous_ref,
                    capability="custom_image_provider",
                    provider_id=requested_id,
                )
                if orphaned_env:
                    env_updates[orphaned_env] = None
            bump_capability_config_epochs(
                config_data,
                CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
            )
            _write_custom_provider_transaction(
                config_path=config_path,
                env_path=env_path,
                config_data=config_data,
                env_updates=env_updates,
            )
            committed_providers = _custom_image_provider_rows_from_config(
                config_data,
                config_path=config_path,
                env_path=env_path,
            )
            row = next(
                item
                for item in committed_providers
                if item.get("id") == f"custom:{requested_id}"
            )
            response = {
                "ok": True,
                "provider": copy.deepcopy(row),
                "providers": committed_providers,
            }
            image_invalidation_token = (
                _capture_image_gen_verification_invalidation()
            )
    warnings = _invoke_durable_mutation_post_commit(
        "set_custom_image_provider_config",
        invalidate_image=True,
        image_invalidation_token=image_invalidation_token,
    )
    return _merge_post_commit_warnings(
        response,
        warnings,
    )


def delete_custom_image_provider_config(provider_id: str) -> dict[str, Any]:
    try:
        from agent.custom_image_providers import (
            custom_image_provider_env_var,
            custom_image_provider_name,
            normalize_custom_image_provider_id,
        )
    except Exception as exc:
        raise RuntimeError("custom image provider support is unavailable") from exc

    normalized_id = normalize_custom_image_provider_id(provider_id)
    provider_name = custom_image_provider_name(normalized_id)
    config_path = Path(_get_config_path())
    with credential_transaction(config_path) as credential_spec:
        config_path = credential_spec.config_target
        env_path = credential_spec.env_target
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            image_cfg = config_data.get("image_gen")
            if (
                isinstance(image_cfg, dict)
                and str(image_cfg.get("provider") or "").strip() == provider_name
            ):
                raise ValueError("该外部图片模型正在使用，请先切换到其他图片生成配置。")
            existing_entries = config_data.get("custom_image_providers")
            if not isinstance(existing_entries, list):
                existing_entries = []
            updated = []
            removed = False
            removed_entry: dict[str, Any] = {}
            for item in existing_entries:
                if not isinstance(item, dict):
                    updated.append(item)
                    continue
                try:
                    item_id = normalize_custom_image_provider_id(item.get("id"))
                except ValueError:
                    updated.append(item)
                    continue
                if item_id == normalized_id:
                    removed = True
                    if not removed_entry:
                        removed_entry = item
                    continue
                updated.append(item)
            if not removed:
                raise ValueError("外部图片模型不存在。")
            config_data["custom_image_providers"] = updated
            legacy_env = ""
            if not str(removed_entry.get("credential_ref") or "").strip():
                legacy_env = custom_image_provider_env_var(normalized_id)
            orphaned_env = _remove_orphaned_managed_custom_credential(
                config_data,
                credential_ref=str(removed_entry.get("credential_ref") or ""),
                capability="custom_image_provider",
                provider_id=normalized_id,
            )
            bump_capability_config_epochs(
                config_data,
                CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
            )
            _write_custom_provider_transaction(
                config_path=config_path,
                env_path=env_path,
                config_data=config_data,
                env_updates={
                    env_var: None
                    for env_var in (orphaned_env, legacy_env)
                    if env_var
                },
            )
            response = {
                "ok": True,
                "providers": _custom_image_provider_rows_from_config(
                    config_data,
                    config_path=config_path,
                    env_path=env_path,
                ),
            }
            image_invalidation_token = (
                _capture_image_gen_verification_invalidation()
            )
    warnings = _invoke_durable_mutation_post_commit(
        "delete_custom_image_provider_config",
        invalidate_image=True,
        image_invalidation_token=image_invalidation_token,
    )
    return _merge_post_commit_warnings(
        response,
        warnings,
    )


def set_image_gen_config(body: dict[str, Any]) -> dict[str, Any]:
    requested_provider_id = str(body.get("provider") or "").strip().lower()
    provider_id = _internal_image_gen_provider_id(requested_provider_id)
    requested_model_id = str(body.get("model") or "").strip()
    model_id = requested_model_id
    api_key = body.get("api_key")
    credential_ref = str(body.get("credential_ref") or "").strip()
    requested_enabled = body.get("enabled") if "enabled" in body else None
    if requested_enabled is not None and not isinstance(requested_enabled, bool):
        raise ValueError("enabled must be a boolean")
    credentials = body.get("credentials")
    if not isinstance(credentials, dict):
        credentials = {}
    if not requested_provider_id:
        raise ValueError("provider is required")
    if credential_ref and provider_id != "dashscope":
        raise ValueError("credential_ref is only supported for DashScope image generation")

    model_contract = _image_gen_provider_model_contract(requested_provider_id)
    if not model_id:
        model_id = str(model_contract.get("default_model") or "").strip()
        models = (
            model_contract.get("models")
            if isinstance(model_contract.get("models"), list)
            else []
        )
        if not model_id and models:
            model_id = str((models[0] or {}).get("id") or "").strip()
    if not model_id:
        raise ValueError("model is required")
    _validate_provider_model_choice(
        requested_provider_id,
        model_id,
        model_contract,
        capability="image generation",
    )

    rows = _image_gen_provider_rows(provider_id)
    selected = next((row for row in rows if row.get("id") == requested_provider_id), None)
    if selected is None:
        if requested_provider_id in _BLOCKED_IMAGE_GEN_PROVIDER_LABELS or provider_id in _BLOCKED_IMAGE_GEN_PROVIDER_LABELS:
            raise ValueError("生成图片主配置只支持中国可用的稳定 Provider，请切换到国产生图服务。")
        raise ValueError(f"unknown image generation provider: {requested_provider_id}")
    selected_custom = bool(selected.get("custom"))
    selected_domestic = bool(selected.get("domestic")) if "domestic" in selected else provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
    selected_status = str(
        selected.get("integration_status")
        or ("custom" if selected_custom else ("stable" if provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS else "external"))
    )
    if selected.get("policy_blocked") or (
        not selected_custom and (not selected_domestic or selected_status != "stable")
    ):
        raise ValueError("生成图片主配置只支持中国可用的稳定 Provider，请切换到国产生图服务。")

    credential_fields = selected.get("credential_fields") if isinstance(selected.get("credential_fields"), list) else []
    endpoint_fields = selected.get("endpoint_fields") if isinstance(selected.get("endpoint_fields"), list) else []
    inline_secret_supplied = bool(api_key is not None and str(api_key).strip())
    for item in credential_fields:
        if not isinstance(item, dict) or not bool(item.get("secret", True)):
            continue
        name = str(item.get("name") or "").strip()
        env_var = str(item.get("env_var") or "").strip()
        raw_value = credentials.get(name) if name and name in credentials else None
        if raw_value is None and env_var and env_var in credentials:
            raw_value = credentials.get(env_var)
        if raw_value is not None and str(raw_value).strip():
            inline_secret_supplied = True
    if credential_ref and inline_secret_supplied:
        raise ValueError("credential_ref and api_key cannot be used together")
    env_updates: dict[str, str] = {}
    option_updates: dict[str, str] = {}
    legacy_api_key_consumed = False
    for item in credential_fields + endpoint_fields:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        env_var = str(item.get("env_var") or "").strip()
        secret = bool(item.get("secret", True))
        raw_value = None
        if name and name in credentials:
            raw_value = credentials.get(name)
        elif env_var and env_var in credentials:
            raw_value = credentials.get(env_var)
        elif api_key is not None and not legacy_api_key_consumed and secret:
            raw_value = api_key
            legacy_api_key_consumed = True
        value = str(raw_value or "").strip()
        if not value and bool(item.get("required")) and item in endpoint_fields:
            raise ValueError(f"{name or env_var} is required")
        if item in endpoint_fields and name and raw_value is not None:
            option_updates[name] = value
            continue
        if not value:
            continue
        if secret:
            if not env_var:
                raise ValueError(f"{provider_id} credential {name or 'api_key'} has no env_var")
            env_updates[env_var] = value
        elif name:
            option_updates[name] = value

    if api_key is not None and str(api_key).strip() and not legacy_api_key_consumed and not credential_fields:
        env_var = _IMAGE_GEN_KEY_ENV.get(provider_id)
        if not env_var:
            raise ValueError(f"{provider_id} does not accept an API key from WebUI")
        env_updates[env_var] = str(api_key).strip()

    config_path = _get_config_path()
    with credential_transaction(config_path):
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            locked_model_contract = _image_gen_provider_model_contract(
                requested_provider_id,
                config_data=config_data,
            )
            locked_model_id = requested_model_id
            if not locked_model_id:
                locked_model_id = str(
                    locked_model_contract.get("default_model") or ""
                ).strip()
                locked_models = (
                    locked_model_contract.get("models")
                    if isinstance(locked_model_contract.get("models"), list)
                    else []
                )
                if not locked_model_id and locked_models:
                    locked_model_id = str(
                        (locked_models[0] or {}).get("id") or ""
                    ).strip()
            if not locked_model_id:
                raise ValueError("model is required")
            model_id = _validate_provider_model_choice(
                requested_provider_id,
                locked_model_id,
                locked_model_contract,
                capability="image generation",
            )
            original_config = copy.deepcopy(config_data)
            if provider_id == "dashscope" and not credential_ref and not inline_secret_supplied:
                credential_ref = default_credential_ref(
                    provider_id,
                    config_data=config_data,
                    config_path=config_path,
                    allow_process_fallback=False,
                )
            if credential_ref:
                credential_ref = normalize_credential_id(credential_ref)
            if credential_ref:
                row = load_credential(credential_ref, config_data=config_data)
                if provider_family(row.get("provider_family")) != provider_family(provider_id):
                    raise ValueError("所选凭据不属于当前 Provider。")
            image_cfg = config_data.get("image_gen")
            if not isinstance(image_cfg, dict):
                image_cfg = {}
            previous_provider = str(image_cfg.get("provider") or "").strip().lower()
            previous_public_provider = _public_image_gen_provider_id(previous_provider)
            previous_row = next(
                (row for row in rows if str(row.get("id") or "") == previous_public_provider),
                {},
            )
            owned_names = {
                str(name).strip()
                for name in (image_cfg.get("endpoint_field_names") or [])
                if str(name).strip()
            }
            owned_names.update(
                str(field.get("name") or "").strip()
                for field in (previous_row.get("endpoint_fields") or [])
                if isinstance(field, dict) and not bool(field.get("secret"))
            )
            if not previous_row and previous_provider == "dashscope":
                owned_names.update({"endpoint_mode", "workspace_id", "region", "base_url"})
            owned_names.discard("")
            image_cfg["provider"] = provider_id
            if model_id:
                image_cfg["model"] = model_id
            if requested_enabled is not None:
                image_cfg["enabled"] = requested_enabled
            image_cfg["use_gateway"] = False
            if provider_id == "dashscope":
                image_cfg["credential_ref"] = credential_ref
            else:
                image_cfg.pop("credential_ref", None)
            image_cfg.pop("api_key", None)
            options = image_cfg.get("options")
            if not isinstance(options, dict):
                options = {}
            for name in owned_names:
                options.pop(name, None)
            for name, value in option_updates.items():
                if value:
                    options[name] = value
                else:
                    options.pop(name, None)
            if options:
                image_cfg["options"] = options
            else:
                image_cfg.pop("options", None)
            current_endpoint_names = sorted(
                str(field.get("name") or "").strip()
                for field in endpoint_fields
                if isinstance(field, dict)
                and not bool(field.get("secret"))
                and str(field.get("name") or "").strip()
            )
            if current_endpoint_names:
                image_cfg["endpoint_field_names"] = current_endpoint_names
            else:
                image_cfg.pop("endpoint_field_names", None)
            config_data["image_gen"] = image_cfg
            bump_capability_config_epochs(
                config_data,
                CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
            )
            _commit_expected_config_env(
                config_path,
                expected_config=original_config,
                desired_config=config_data,
                env_updates=env_updates,
            )
        image_invalidation_token = (
            _capture_image_gen_verification_invalidation()
        )
        response = _get_image_gen_config_unlocked(
            refresh_runtime=False
        )
    warnings = _invoke_durable_mutation_post_commit(
        "set_image_gen_config",
        invalidate_image=True,
        image_invalidation_token=image_invalidation_token,
    )
    response = _project_successful_verification_invalidation(
        response,
        warnings,
        capability="image",
    )
    return _merge_post_commit_warnings(
        response,
        warnings,
    )


def _image_capability_provider_metadata(
    vision_rows: list[dict[str, Any]],
    image_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build one secret-free, server-driven Provider catalog for both cards."""
    merged: dict[str, dict[str, Any]] = {}

    def add(capability: str, row: dict[str, Any]) -> None:
        provider_id = str(row.get("id") or "").strip()
        if not provider_id:
            return
        family = provider_family(row.get("provider_family") or provider_id)
        auth_type = str(row.get("auth_type") or "api_key").strip().lower()
        auth_contract = normalized_setup_contract(
            {"auth_type": auth_type},
            provider_family=family,
            capabilities=(capability,),
            transport=str(row.get("transport") or ""),
        )
        auth_editable = bool(
            row.get("auth_editable", auth_contract["auth_editable"])
        )
        auth_message = str(
            row.get("auth_message") or auth_contract["auth_message"]
        )
        custom = bool(row.get("custom")) or provider_id.startswith("custom:")
        key = f"{capability}:{provider_id}" if custom else family
        default_named_credentials = bool(
            auth_type == "api_key"
            and auth_editable
            and (
                (
                    capability == "vision"
                    and provider_id in {"alibaba", "zai"}
                )
                or (
                    capability == "image_generation"
                    and _internal_image_gen_provider_id(provider_id)
                    in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
                )
            )
        )
        entry = merged.setdefault(
            key,
            {
                "provider_family": family,
                "label": str(row.get("name") or provider_id),
                "capabilities": [],
                "provider_ids": {},
                "auth_type": auth_type,
                "auth_editable": auth_editable,
                "auth_message": auth_message,
                "support_level": "compatible" if custom else "native",
                "supports_named_credentials": bool(
                    row.get(
                        "supports_named_credentials",
                        default_named_credentials,
                    )
                ),
                "models": {},
                "default_models": {},
                "credential_fields": {},
                "endpoint_fields": {},
                "selectable": True,
            },
        )
        if capability not in entry["capabilities"]:
            entry["capabilities"].append(capability)
        entry["provider_ids"][capability] = provider_id
        entry["models"][capability] = list(row.get("models") or [])
        entry["default_models"][capability] = str(
            row.get("default_model") or ""
        )
        entry["credential_fields"][capability] = list(
            row.get("credential_fields") or []
        )
        entry["endpoint_fields"][capability] = list(
            row.get("endpoint_fields") or []
        )
        entry["supports_named_credentials"] = bool(
            entry["supports_named_credentials"]
            and row.get(
                "supports_named_credentials",
                default_named_credentials,
            )
        )
        if not auth_editable and entry["auth_editable"]:
            entry["auth_editable"] = False
            entry["auth_message"] = auth_message
        if row.get("policy_blocked") or row.get("integration_status") in {
            "planned",
            "unsupported",
            "blocked",
        }:
            entry["selectable"] = False

    for row in vision_rows:
        if isinstance(row, dict):
            add("vision", row)
    for row in image_rows:
        if isinstance(row, dict):
            add("image_generation", row)
    return sorted(
        merged.values(),
        key=lambda item: (
            item["support_level"] != "native",
            item["label"],
        ),
    )


def _effective_vision_route(
    config_data: dict[str, Any],
    vision: dict[str, Any],
) -> dict[str, str]:
    """Describe the path an uploaded image will actually take."""
    provider = str(vision.get("provider") or "")
    model = str(vision.get("model") or "")
    model_cfg = _safe_model_cfg(config_data)
    main_provider = str(model_cfg.get("provider") or "").strip()
    main_model = str(
        model_cfg.get("default")
        or model_cfg.get("model")
        or model_cfg.get("name")
        or ""
    ).strip()
    try:
        from agent.image_routing import decide_image_input_mode

        main_routing_config = copy.deepcopy(config_data)
        auxiliary = main_routing_config.get("auxiliary")
        if isinstance(auxiliary, dict):
            auxiliary.pop("vision", None)
            if not auxiliary:
                main_routing_config.pop("auxiliary", None)
        mode = decide_image_input_mode(
            main_provider,
            main_model,
            main_routing_config,
        )
    except Exception:
        mode = "unknown"
    if mode == "native":
        return {
            "route": "main_model_vision",
            "provider": main_provider,
            "model": main_model,
        }
    if not vision.get("enabled"):
        return {
            "route": "disabled",
            "provider": provider,
            "model": model,
        }
    verification_status = str(
        (vision.get("verification") or {}).get("status") or ""
    )
    if mode == "text" and verification_status == "verified":
        return {
            "route": "auxiliary_vision",
            "provider": provider,
            "model": model,
        }
    return {
        "route": "unavailable",
        "provider": provider or main_provider,
        "model": model or main_model,
    }


def _effective_image_generation_route(
    image_generation: dict[str, Any],
) -> dict[str, str]:
    """Describe the route from the same fingerprint-checked public snapshot."""
    provider = str(image_generation.get("provider") or "")
    model = str(image_generation.get("model") or "")
    if not image_generation.get("enabled"):
        return {
            "route": "disabled",
            "provider": provider,
            "model": model,
        }
    verification = image_generation.get("verification")
    verification_status = str(
        verification.get("status")
        if isinstance(verification, dict)
        else ""
    )
    if verification_status == "verified":
        return {
            "route": "image_generation_provider",
            "provider": provider,
            "model": model,
        }
    return {
        "route": "unavailable",
        "provider": provider,
        "model": model,
    }


def _stage_image_capability_credential(
    config_data: dict[str, Any],
    body: dict[str, Any],
    env_updates: dict[str, str | None],
    selected_capabilities: dict[str, dict[str, Any]],
) -> tuple[bool, bool]:
    """Stage one named credential in the shared config/env transaction."""
    credential_id = normalize_credential_id(body.get("id"))
    family = provider_family(body.get("provider_family") or body.get("provider"))
    if not family:
        raise ValueError("provider_family is required")
    auth_type = str(body.get("auth_type") or "api_key").strip().lower()
    if auth_type != "api_key":
        raise ValueError("only api_key credentials are supported")
    operation = str(body.get("operation") or "").strip().lower()
    managed_by = str(body.get("managed_by") or "").strip()
    source_capability = str(body.get("source_capability") or "").strip()
    source_provider_id = str(body.get("source_provider_id") or "").strip().lower()
    if (
        operation != "create"
        or managed_by != _IMAGE_CAPABILITY_CREDENTIAL_MANAGER
        or source_capability not in {"vision", "image_generation"}
        or not source_provider_id
    ):
        raise ValueError(
            "new image capability credentials require explicit center ownership"
        )
    selected_source = selected_capabilities.get(source_capability)
    if not isinstance(selected_source, dict):
        raise ValueError(
            "credential source_capability must be included in capabilities"
        )
    selected_provider_id = str(
        selected_source.get("provider") or ""
    ).strip().lower()
    if selected_provider_id != source_provider_id:
        raise ValueError(
            "credential source_provider_id must match the selected Provider"
        )
    selected_ref = str(
        selected_source.get("credential_ref") or ""
    ).strip()
    if (
        not selected_ref
        or normalize_credential_id(selected_ref) != credential_id
    ):
        raise ValueError(
            "credential update must be explicitly selected by its capability"
        )
    if provider_family(selected_provider_id) != family:
        raise ValueError(
            "credential provider_family must match the selected Provider"
        )
    requested_default = body.get("default") if "default" in body else None
    if requested_default is not None and not isinstance(requested_default, bool):
        raise ValueError("default must be a boolean")
    previous_config = copy.deepcopy(config_data)
    previous_index, previous_row = _provider_credential_row(
        config_data,
        credential_id,
    )
    intended_uses = set(
        _provider_credential_used_by(config_data, credential_id)
    )
    capability_paths = {
        "vision": "auxiliary.vision",
        "image_generation": "image_gen",
    }
    for capability, selected in selected_capabilities.items():
        path = capability_paths[capability]
        intended_uses.discard(path)
        requested_ref = str(selected.get("credential_ref") or "").strip()
        if (
            requested_ref
            and normalize_credential_id(requested_ref) == credential_id
        ):
            intended_uses.add(path)
    if len(intended_uses) > 1:
        raise ImageCapabilityCredentialError(
            "image_capability_credential_shared",
            "该凭据已被多个图片能力引用，图片能力中心不会覆盖共享凭据。",
        )
    if previous_row is not None:
        _validate_provider_credential_secret_env(previous_row)
        if (
            previous_row.get("managed_by")
            != _IMAGE_CAPABILITY_CREDENTIAL_MANAGER
            or previous_row.get("source_capability") != source_capability
            or str(previous_row.get("source_provider_id") or "")
            .strip()
            .lower()
            != source_provider_id
            or provider_family(previous_row.get("provider_family")) != family
        ):
            raise ImageCapabilityCredentialError(
                "image_capability_credential_collision",
                "凭据 ID 已存在且不属于当前图片能力草稿，请刷新后重试。",
            )
    default_value = (
        requested_default
        if requested_default is not None
        else bool(previous_row and previous_row.get("default"))
    )
    if default_value:
        rows = config_data.get("provider_credentials")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("default"):
                continue
            try:
                row_id = normalize_credential_id(row.get("id"))
            except ValueError:
                continue
            if (
                row_id != credential_id
                and provider_family(row.get("provider_family")) == family
            ):
                raise ValueError(
                    "当前 Provider 已有默认凭据，请先取消原默认凭据。"
                )
    secret_env = credential_secret_env(credential_id)
    stored = {
        "id": credential_id,
        "provider_family": family,
        "label": str(body.get("label") or "").strip() or credential_id,
        "auth_type": auth_type,
        "secret_env": secret_env,
        "managed_by": _IMAGE_CAPABILITY_CREDENTIAL_MANAGER,
        "source_capability": source_capability,
        "source_provider_id": source_provider_id,
    }
    if default_value:
        stored["default"] = True
    _replace_provider_credential_row(
        config_data,
        credential_id,
        stored,
        preferred_index=previous_index,
    )
    secret = body.get("api_key")
    if secret is None:
        secret = body.get("secret")
    secret_value = str(secret or "").strip()
    if secret_value:
        env_updates[secret_env] = secret_value
    authorization_fields = (
        "provider_family",
        "auth_type",
        "secret_env",
        "default",
    )
    authorization_changed = bool(
        secret_value
        or previous_row is None
        or any(
            previous_row.get(field) != stored.get(field)
            for field in authorization_fields
        )
    )
    used_by = set(
        _provider_credential_used_by(previous_config, credential_id)
    ) | set(_provider_credential_used_by(config_data, credential_id))
    return (
        bool(authorization_changed and "auxiliary.vision" in used_by),
        bool(authorization_changed and "image_gen" in used_by),
    )


def _stage_vision_capability(
    config_data: dict[str, Any],
    body: dict[str, Any],
    *,
    config_path: Path,
) -> None:
    enabled = body["enabled"]
    provider_id = str(body.get("provider") or "").strip().lower()
    requested_model_id = str(body.get("model") or "").strip()
    auxiliary = config_data.get("auxiliary")
    if not isinstance(auxiliary, dict):
        auxiliary = {}
    vision_cfg = auxiliary.get("vision")
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
    if not enabled:
        vision_cfg["enabled"] = False
        auxiliary["vision"] = vision_cfg
        config_data["auxiliary"] = auxiliary
        bump_capability_config_epochs(
            config_data,
            CAPABILITY_CONFIG_EPOCH_VISION,
        )
        return
    named_custom_entry: dict[str, Any] | None = None
    if provider_id.startswith("custom:"):
        try:
            from agent.custom_vision_providers import (
                find_custom_vision_provider_entry,
            )

            named_custom_entry = find_custom_vision_provider_entry(
                provider_id,
                config_data,
            )
        except ImportError:
            named_custom_entry = None
    if provider_id not in _VISION_PROVIDER_META and named_custom_entry is None:
        raise ValueError(f"unknown vision provider: {provider_id}")
    meta = _VISION_PROVIDER_META.get(provider_id) or {
        "default_model": named_custom_entry["default_model"],
        "models": [{"id": item} for item in named_custom_entry["models"]],
    }
    model_id = requested_model_id or str(meta.get("default_model") or "").strip()
    if not model_id:
        models = meta.get("models") if isinstance(meta.get("models"), list) else []
        if models:
            model_id = str((models[0] or {}).get("id") or "").strip()
    if not model_id:
        raise ValueError("model is required")
    _validate_provider_model_choice(
        provider_id,
        model_id,
        meta,
        capability="vision",
    )
    endpoint_values = body.get("endpoint_values") or {}
    if not isinstance(endpoint_values, dict):
        raise ValueError("vision.endpoint_values must be an object")
    endpoint_updates: dict[str, str] = {}
    for field in meta.get("endpoint_fields") or []:
        if not isinstance(field, dict) or field.get("secret"):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        default_value = {
            "endpoint_mode": "public",
            "region": "cn-beijing",
        }.get(name, "")
        value = str(endpoint_values.get(name) or default_value).strip()
        if field.get("required") and not value:
            raise ValueError(f"{name} is required")
        endpoint_updates[name] = value
    base_url = str(endpoint_values.get("base_url") or "").strip().rstrip("/")
    if provider_id == "alibaba":
        endpoint_mode = str(
            endpoint_values.get("endpoint_mode") or "public"
        ).strip().lower()
        region = str(
            endpoint_values.get("region") or "cn-beijing"
        ).strip().lower()
        workspace_id = str(
            endpoint_values.get("workspace_id") or ""
        ).strip().lower()
        base_url = build_vision_base_url(
            endpoint_mode=endpoint_mode,
            region=region,
            workspace_prefix=workspace_id,
            custom_url=base_url,
        )
        canonical = {
            "endpoint_mode": endpoint_mode,
            "region": region,
            "workspace_id": workspace_id,
            "base_url": base_url,
        }
        for name in tuple(endpoint_updates):
            if name in canonical:
                endpoint_updates[name] = canonical[name]
    if meta.get("requires_base_url") and not base_url:
        raise ValueError("base_url is required for custom vision provider")
    credential_ref = str(body.get("credential_ref") or "").strip()
    supports_named_credentials = provider_id in {"alibaba", "zai"}
    if supports_named_credentials and not credential_ref:
        credential_ref = default_credential_ref(
            provider_id,
            config_data=config_data,
            config_path=config_path,
            allow_process_fallback=False,
        )
    if credential_ref:
        credential_ref = normalize_credential_id(credential_ref)
        row = load_credential(credential_ref, config_data=config_data)
        if provider_family(row.get("provider_family")) != provider_family(
            provider_id
        ):
            raise ValueError("所选凭据不属于当前 Provider。")
    if credential_ref and not supports_named_credentials:
        raise ValueError(
            "credential_ref is not supported for this vision Provider"
        )
    previous_provider = str(
        vision_cfg.get("provider") or ""
    ).strip().lower()
    owned_names = {
        str(name).strip()
        for name in (vision_cfg.get("endpoint_field_names") or [])
        if str(name).strip()
    }
    previous_meta = _VISION_PROVIDER_META.get(previous_provider) or {}
    owned_names.update(
        str(field.get("name") or "").strip()
        for field in (previous_meta.get("endpoint_fields") or [])
        if isinstance(field, dict) and not field.get("secret")
    )
    owned_names.discard("")
    for name in owned_names:
        vision_cfg.pop(name, None)
    vision_cfg["enabled"] = enabled
    vision_cfg["provider"] = provider_id
    vision_cfg["model"] = model_id
    vision_cfg.pop("api_key", None)
    vision_cfg.pop("api_mode", None)
    if supports_named_credentials:
        vision_cfg["credential_ref"] = credential_ref
    else:
        vision_cfg.pop("credential_ref", None)
        vision_cfg.pop("base_url", None)
    for name, value in endpoint_updates.items():
        if value:
            vision_cfg[name] = value
        else:
            vision_cfg.pop(name, None)
    if endpoint_updates:
        vision_cfg["endpoint_field_names"] = sorted(endpoint_updates)
    else:
        vision_cfg.pop("endpoint_field_names", None)
    auxiliary["vision"] = vision_cfg
    config_data["auxiliary"] = auxiliary
    bump_capability_config_epochs(
        config_data,
        CAPABILITY_CONFIG_EPOCH_VISION,
    )


def _stage_image_generation_capability(
    config_data: dict[str, Any],
    body: dict[str, Any],
    *,
    config_path: Path,
) -> None:
    enabled = body["enabled"]
    requested_provider_id = str(body.get("provider") or "").strip().lower()
    requested_model_id = str(body.get("model") or "").strip()
    image_cfg = config_data.get("image_gen")
    if not isinstance(image_cfg, dict):
        image_cfg = {}
    if not enabled:
        image_cfg["enabled"] = False
        config_data["image_gen"] = image_cfg
        bump_capability_config_epochs(
            config_data,
            CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
        )
        return
    provider_id = _internal_image_gen_provider_id(requested_provider_id)
    model_contract = _image_gen_provider_model_contract(
        requested_provider_id,
        config_data=config_data,
    )
    model_id = requested_model_id or str(
        model_contract.get("default_model") or ""
    ).strip()
    if not model_id:
        models = (
            model_contract.get("models")
            if isinstance(model_contract.get("models"), list)
            else []
        )
        if models:
            model_id = str((models[0] or {}).get("id") or "").strip()
    if not model_id:
        raise ValueError("model is required")
    _validate_provider_model_choice(
        requested_provider_id,
        model_id,
        model_contract,
        capability="image generation",
    )
    rows = _image_gen_provider_rows(provider_id)
    selected = next(
        (
            row
            for row in rows
            if str(row.get("id") or "") == requested_provider_id
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            f"unknown image generation provider: {requested_provider_id}"
        )
    selected_custom = bool(selected.get("custom"))
    selected_domestic = (
        bool(selected.get("domestic"))
        if "domestic" in selected
        else provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
    )
    selected_status = str(
        selected.get("integration_status")
        or (
            "custom"
            if selected_custom
            else (
                "stable"
                if provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
                else "external"
            )
        )
    )
    if selected.get("policy_blocked") or (
        not selected_custom
        and (not selected_domestic or selected_status != "stable")
    ):
        raise ValueError(
            "生成图片主配置只支持中国可用的稳定 Provider，请切换到国产生图服务。"
        )
    credential_ref = str(body.get("credential_ref") or "").strip()
    supports_named_credentials = (
        provider_id in _DOMESTIC_STABLE_IMAGE_GEN_PROVIDER_IDS
    )
    if credential_ref and not supports_named_credentials:
        raise ValueError(
            "credential_ref is not supported for this image generation Provider"
        )
    if supports_named_credentials and not credential_ref:
        credential_ref = default_credential_ref(
            provider_id,
            config_data=config_data,
            config_path=config_path,
            allow_process_fallback=False,
        )
    if credential_ref:
        credential_ref = normalize_credential_id(credential_ref)
        row = load_credential(credential_ref, config_data=config_data)
        if provider_family(row.get("provider_family")) != provider_family(
            provider_id
        ):
            raise ValueError("所选凭据不属于当前 Provider。")
    endpoint_values = body.get("endpoint_values") or {}
    if not isinstance(endpoint_values, dict):
        raise ValueError(
            "image_generation.endpoint_values must be an object"
        )
    endpoint_fields = (
        selected.get("endpoint_fields")
        if isinstance(selected.get("endpoint_fields"), list)
        else []
    )
    option_updates: dict[str, str] = {}
    for field in endpoint_fields:
        if not isinstance(field, dict) or field.get("secret"):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        value = str(endpoint_values.get(name) or "").strip()
        if field.get("required") and not value:
            raise ValueError(f"{name} is required")
        option_updates[name] = value
    previous_provider = str(
        image_cfg.get("provider") or ""
    ).strip().lower()
    previous_public_provider = _public_image_gen_provider_id(
        previous_provider
    )
    previous_row = next(
        (
            row
            for row in rows
            if str(row.get("id") or "") == previous_public_provider
        ),
        {},
    )
    owned_names = {
        str(name).strip()
        for name in (image_cfg.get("endpoint_field_names") or [])
        if str(name).strip()
    }
    owned_names.update(
        str(field.get("name") or "").strip()
        for field in (previous_row.get("endpoint_fields") or [])
        if isinstance(field, dict) and not field.get("secret")
    )
    if not previous_row and previous_provider == "dashscope":
        owned_names.update(
            {"endpoint_mode", "workspace_id", "region", "base_url"}
        )
    owned_names.discard("")
    options = image_cfg.get("options")
    if not isinstance(options, dict):
        options = {}
    for name in owned_names:
        options.pop(name, None)
    for name, value in option_updates.items():
        if value:
            options[name] = value
        else:
            options.pop(name, None)
    image_cfg["enabled"] = enabled
    image_cfg["provider"] = provider_id
    image_cfg["model"] = model_id
    image_cfg["use_gateway"] = False
    image_cfg.pop("api_key", None)
    if supports_named_credentials:
        image_cfg["credential_ref"] = credential_ref
    else:
        image_cfg.pop("credential_ref", None)
    if options:
        image_cfg["options"] = options
    else:
        image_cfg.pop("options", None)
    endpoint_names = sorted(
        str(field.get("name") or "").strip()
        for field in endpoint_fields
        if isinstance(field, dict)
        and not field.get("secret")
        and str(field.get("name") or "").strip()
    )
    if endpoint_names:
        image_cfg["endpoint_field_names"] = endpoint_names
    else:
        image_cfg.pop("endpoint_field_names", None)
    config_data["image_gen"] = image_cfg
    bump_capability_config_epochs(
        config_data,
        CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
    )


def get_image_capabilities() -> dict[str, Any]:
    """Return one canonical, secret-free picture capability snapshot."""
    config_path = Path(_get_config_path())
    with credential_transaction(config_path):
        reload_config()
        credential_snapshot = load_credential_snapshot(config_path)
        config_data = credential_snapshot.config
        vision_payload = _get_vision_config_unlocked(
            refresh_runtime=False,
            config_data=config_data,
        )
        image_payload = _get_image_gen_config_unlocked(
            refresh_runtime=False,
            config_data=config_data,
        )
        credentials_payload = _public_provider_credentials_config(config_data)
        revision = _image_capability_revision(
            config_data,
            env_sha256=credential_snapshot.env_sha256,
        )
        effective_vision_route = _effective_vision_route(
            config_data,
            dict(vision_payload.get("vision") or {}),
        )
        effective_image_generation_route = _effective_image_generation_route(
            dict(image_payload.get("image_gen") or {})
        )
    vision = dict(vision_payload.get("vision") or {})
    image_generation = dict(image_payload.get("image_gen") or {})
    vision_rows = list(vision_payload.get("providers") or [])
    image_rows = list(image_payload.get("providers") or [])
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "revision": revision,
        "capabilities": {
            "vision": vision,
            "image_generation": image_generation,
        },
        "providers": _image_capability_provider_metadata(
            vision_rows,
            image_rows,
        ),
        "vision_providers": vision_rows,
        "image_gen_providers": image_rows,
        "provider_credentials": list(credentials_payload.get("credentials") or []),
        "effective_route": {
            "vision": effective_vision_route,
            "image_generation": effective_image_generation_route,
        },
    }


def _image_capability_probe_lock(
    config_path: Path,
    profile: str,
) -> Any:
    key = (
        str(config_path.expanduser().resolve(strict=False)),
        str(profile or "default"),
    )
    with _IMAGE_CAPABILITY_PROBE_LOCKS_GUARD:
        lock = _IMAGE_CAPABILITY_PROBE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _IMAGE_CAPABILITY_PROBE_LOCKS[key] = lock
        return lock


def _superseded_image_capability_probe_result(
    capability: str,
    requested: dict[str, Any],
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    provider = str(requested.get("provider") or "").strip().lower()
    model = str(requested.get("model") or "").strip()
    diagnostic_id = uuid.uuid4().hex
    if capability == "vision":
        return _vision_test_response(
            ok=False,
            status="superseded",
            checked_at=checked_at,
            provider=provider,
            model=model,
            error_code="vision_probe_superseded",
            message=("识图配置在真实探测前已被更新，本次旧请求未调用 Provider。"),
            diagnostic_id=diagnostic_id,
        )
    return _image_gen_test_response(
        ok=False,
        status="superseded",
        checked_at=checked_at,
        provider=provider,
        model=model,
        error_code="image_gen_probe_superseded",
        message=("生图配置在真实探测前已被更新，本次旧请求未调用 Provider。"),
        diagnostic_id=diagnostic_id,
    )


def _run_committed_image_capability_probe(
    *,
    config_path: Path,
    profile: str,
    committed_revision: str,
    capability: str,
    requested: dict[str, Any],
    probe: Any,
    snapshot: Any,
) -> dict[str, Any]:
    """Probe an immutable commit-time target after one last revision check.

    The cross-process credential transaction is released before Provider I/O.
    A legacy writer may therefore commit immediately after this check, but it
    cannot retarget this request because the probe only consumes ``snapshot``.
    """
    with _image_capability_probe_lock(config_path, profile):
        with credential_transaction(config_path):
            current = load_credential_snapshot(config_path)
            current_revision = _image_capability_revision(
                current.config,
                env_sha256=current.env_sha256,
            )
            if current_revision != committed_revision:
                return _superseded_image_capability_probe_result(
                    capability,
                    requested,
                )
        return probe(snapshot=snapshot)


def _configure_image_capabilities_once(
    body: dict[str, Any],
) -> dict[str, Any]:
    """Save one revision-checked image capability request."""
    expected_revision = str(body["expected_revision"]).strip().lower()
    capabilities = body.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities is required")
    unknown_capabilities = set(capabilities) - {
        "vision",
        "image_generation",
    }
    if unknown_capabilities:
        raise ValueError("capabilities contains an unknown capability")
    credential_updates = body.get("credential_updates") or []
    if not isinstance(credential_updates, list) or not all(
        isinstance(item, dict) for item in credential_updates
    ):
        raise ValueError("credential_updates must be a list")
    verify = body.get("verify") or []
    if not isinstance(verify, list):
        raise ValueError("verify must be a list")
    verify_set = {str(item or "").strip() for item in verify}
    if verify_set - {"vision", "image_generation"}:
        raise ValueError("verify contains an unknown capability")
    selected: dict[str, dict[str, Any]] = {}
    for capability in ("vision", "image_generation"):
        raw = capabilities.get(capability)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"{capability} must be an object")
        if not isinstance(raw.get("enabled"), bool):
            raise ValueError(f"{capability}.enabled must be a boolean")
        if any(key in raw for key in ("api_key", "secret", "credentials")):
            raise ValueError(
                "capability secrets must be supplied through credential_updates"
            )
        selected[capability] = dict(raw)
    for capability in verify_set:
        if capability not in selected:
            raise ValueError(
                "verify requires the capability to be included in capabilities"
            )
        if not selected[capability].get("enabled"):
            raise ValueError("verify requires an enabled capability")
    if not selected and not credential_updates:
        raise ValueError("no image capability changes were supplied")
    config_path = Path(_get_config_path())
    invalidate_vision = "vision" in selected
    invalidate_image = "image_generation" in selected
    credential_invalidate_vision = False
    credential_invalidate_image = False
    committed_profile = _active_profile_name()
    committed_probe_snapshots: dict[str, Any] = {}
    with (
        _image_capability_probe_lock(
            config_path,
            committed_profile,
        ),
        credential_transaction(config_path),
    ):
        with _cfg_lock:
            credential_snapshot = load_credential_snapshot(config_path)
            original_config = credential_snapshot.config
            if (
                _image_capability_revision(
                    original_config,
                    env_sha256=credential_snapshot.env_sha256,
                )
                != expected_revision
            ):
                from hermes_cli.config import ConfigurationConflictError

                raise ConfigurationConflictError(("image_capabilities", "revision"))
            desired_config = copy.deepcopy(original_config)
            desired_config[_IMAGE_CAPABILITY_REVISION_NONCE_KEY] = uuid.uuid4().hex
            env_updates: dict[str, str | None] = {}
            for update in credential_updates:
                credential_vision, credential_image = (
                    _stage_image_capability_credential(
                        desired_config,
                        update,
                        env_updates,
                        selected,
                    )
                )
                credential_invalidate_vision = (
                    credential_invalidate_vision or credential_vision
                )
                credential_invalidate_image = (
                    credential_invalidate_image or credential_image
                )
                invalidate_vision = invalidate_vision or credential_vision
                invalidate_image = invalidate_image or credential_image
            if "vision" in selected:
                _stage_vision_capability(
                    desired_config,
                    selected["vision"],
                    config_path=config_path,
                )
            if "image_generation" in selected:
                _stage_image_generation_capability(
                    desired_config,
                    selected["image_generation"],
                    config_path=config_path,
                )
            if credential_invalidate_vision and "vision" not in selected:
                bump_capability_config_epochs(
                    desired_config,
                    CAPABILITY_CONFIG_EPOCH_VISION,
                )
            if credential_invalidate_image and "image_generation" not in selected:
                bump_capability_config_epochs(
                    desired_config,
                    CAPABILITY_CONFIG_EPOCH_IMAGE_GENERATION,
                )
            _commit_expected_config_env(
                config_path,
                expected_config=original_config,
                desired_config=desired_config,
                env_updates=env_updates,
            )
            committed_snapshot = load_credential_snapshot(config_path)
            committed_revision = _image_capability_revision(
                committed_snapshot.config,
                env_sha256=committed_snapshot.env_sha256,
            )
            if "vision" in verify_set:
                committed_probe_snapshots["vision"] = (
                    _capture_vision_config_snapshot_unlocked(
                        config_path=config_path,
                        config_data=committed_snapshot.config,
                    )
                )
            if "image_generation" in verify_set:
                committed_probe_snapshots["image_generation"] = (
                    _capture_image_gen_config_snapshot_unlocked(
                        config_path=config_path,
                        config_data=committed_snapshot.config,
                    )
                )
        vision_token = (
            _capture_vision_verification_invalidation() if invalidate_vision else None
        )
        image_token = (
            _capture_image_gen_verification_invalidation() if invalidate_image else None
        )
    warnings = list(
        _invoke_durable_mutation_post_commit(
            "configure_image_capabilities",
            invalidate_vision=invalidate_vision,
            invalidate_image=invalidate_image,
            vision_invalidation_token=vision_token,
            image_invalidation_token=image_token,
        )
    )
    verification_results: dict[str, Any] = {}
    verification_probes = {
        "vision": test_vision_config,
        "image_generation": test_image_gen_config,
    }
    for capability in ("vision", "image_generation"):
        if capability not in verify_set:
            continue
        try:
            verification_results[capability] = _run_committed_image_capability_probe(
                config_path=config_path,
                profile=committed_profile,
                committed_revision=committed_revision,
                capability=capability,
                requested=selected[capability],
                probe=verification_probes[capability],
                snapshot=committed_probe_snapshots[capability],
            )
        except Exception:
            diagnostic_id = uuid.uuid4().hex
            logger.warning(
                "Image capability verification failed after save "
                "(capability=%s, diagnostic_id=%s)",
                capability,
                diagnostic_id,
            )
            verification_results[capability] = {
                "ok": False,
                "status": "failed",
                "checked_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "provider": "",
                "model": "",
                "error_code": "verification_internal_error",
                "message": "配置已保存，但验证暂时无法执行，请稍后重试。",
                "diagnostic_id": diagnostic_id,
            }
            warnings.append(f"{capability}_verification_failed_after_save")
    result = get_image_capabilities()
    result["verification_results"] = verification_results
    result["committed_revision"] = committed_revision
    probe_was_superseded = any(
        str(item.get("status") or "") == "superseded"
        for item in verification_results.values()
        if isinstance(item, dict)
    )
    result["request_status"] = (
        "superseded"
        if probe_was_superseded
        or str(result.get("revision") or "") != committed_revision
        else "applied"
    )
    return _merge_post_commit_warnings(result, warnings)


def configure_image_capabilities(body: dict[str, Any]) -> dict[str, Any]:
    """Save once per request id and reject stale browser drafts."""
    expected_revision = str(body.get("expected_revision") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_revision):
        raise ValueError("expected_revision must be a 64-character revision")
    request_id = str(body.get("request_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", request_id):
        raise ValueError("request_id must be an 8-128 character identifier")

    digest = _image_capability_request_digest(body)
    config_scope = str(
        Path(_get_config_path()).expanduser().resolve(strict=False)
    )
    key = (config_scope, request_id)
    owner = False
    now = time.monotonic()
    with _IMAGE_CAPABILITY_REQUEST_LOCK:
        _prune_image_capability_requests(now)
        entry = _IMAGE_CAPABILITY_REQUESTS.get(key)
        if entry is None:
            if (
                len(_IMAGE_CAPABILITY_REQUESTS)
                >= _IMAGE_CAPABILITY_REQUEST_CACHE_CAPACITY
            ):
                raise RuntimeError(
                    "image capability request capacity is exhausted"
                )
            entry = _ImageCapabilityRequestEntry(
                payload_digest=digest,
                touched_at=now,
            )
            _IMAGE_CAPABILITY_REQUESTS[key] = entry
            owner = True
        elif entry.payload_digest != digest:
            raise ValueError(
                "request_id was already used for a different payload"
            )
        else:
            entry.touched_at = now
            _IMAGE_CAPABILITY_REQUESTS.move_to_end(key)

    if not owner:
        if not entry.event.wait(timeout=180):
            raise RuntimeError(
                "image capability request is still in progress"
            )
        with _IMAGE_CAPABILITY_REQUEST_LOCK:
            entry.touched_at = time.monotonic()
            if entry.error is not None:
                raise _replayed_image_capability_error(entry.error)
            if entry.result is None:
                raise RuntimeError(
                    "image capability request completed without a result"
                )
            return copy.deepcopy(entry.result)

    try:
        result = _configure_image_capabilities_once(body)
    except BaseException as exc:
        with _IMAGE_CAPABILITY_REQUEST_LOCK:
            entry.error = _cacheable_image_capability_error(exc)
            entry.touched_at = time.monotonic()
            entry.event.set()
        raise
    with _IMAGE_CAPABILITY_REQUEST_LOCK:
        entry.result = copy.deepcopy(result)
        entry.touched_at = time.monotonic()
        entry.event.set()
    return result


def get_model_config() -> dict[str, Any]:
    with credential_transaction(_get_config_path()):
        return _get_model_config_unlocked()


def _get_model_config_unlocked(
    *,
    refresh_runtime: bool = True,
) -> dict[str, Any]:
    if refresh_runtime:
        reload_config()
    config_path = _get_config_path()
    config_data = load_credential_config(config_path)
    model_cfg = _safe_model_cfg(config_data)
    provider = str(model_cfg.get("provider") or "").strip()
    model = str(model_cfg.get("default") or model_cfg.get("model") or model_cfg.get("name") or "").strip()
    key_env = str(model_cfg.get("key_env") or model_cfg.get("api_key_env") or "").strip()
    image_gen_config = _get_image_gen_config_unlocked(
        refresh_runtime=False
    )
    vision_config = _get_vision_config_unlocked(refresh_runtime=False)
    provider_credentials = get_provider_credentials_config().get("credentials", [])
    vision = dict(vision_config.get("vision") or {})
    vision_rows = list(vision_config.get("providers") or [])
    image_rows = list(image_gen_config.get("providers") or [])
    return {
        "ok": True,
        "profile": _active_profile_name(),
        "config": _public_config_summary(config_path),
        "main": {
            "provider": provider,
            "model": model,
            "base_url": str(model_cfg.get("base_url") or "").strip(),
            "key_env": key_env,
            "key_status": _key_status_for_env(key_env) if key_env else _provider_key_status(provider),
        },
        "providers": get_providers().get("providers", []),
        "auxiliary": get_auxiliary_models(),
        "vision": vision,
        "vision_providers": vision_rows,
        "image_gen": image_gen_config.get("image_gen", {}),
        "image_gen_providers": image_rows,
        "image_capability_providers": _image_capability_provider_metadata(
            vision_rows,
            image_rows,
        ),
        "effective_route": {
            "vision": _effective_vision_route(config_data, vision)
        },
        "provider_credentials": provider_credentials,
        "custom": {"supported": True, "key_env": _CUSTOM_MODEL_KEY_ENV},
    }


def set_main_model_config(body: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(body.get("provider") or "").strip().lower()
    model_id = str(body.get("model") or "").strip()
    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    api_key = body.get("api_key")
    if not provider_id:
        raise ValueError("provider is required")
    if not model_id:
        raise ValueError("model is required")

    if provider_id in _OAUTH_PROVIDERS or _provider_is_oauth(provider_id):
        label = _PROVIDER_DISPLAY.get(provider_id, provider_id)
        raise ValueError(f"{label} 使用网页登录授权，请在太极智能体中完成授权。")

    env_updates: dict[str, str | None] = {}
    secret_value = str(api_key or "").strip()
    if provider_id == "custom":
        if not base_url:
            raise ValueError("base_url is required for custom provider")
        if secret_value:
            env_updates[_CUSTOM_MODEL_KEY_ENV] = secret_value
    elif secret_value:
        env_var = _PROVIDER_ENV_VAR.get(provider_id)
        if not env_var:
            raise ValueError(
                f"Cannot configure API key for "
                f"'{_PROVIDER_DISPLAY.get(provider_id, provider_id)}'. "
                "This provider does not have a known env var mapping."
            )
        if "\n" in secret_value or "\r" in secret_value:
            raise ValueError("API key must not contain newline characters.")
        if len(secret_value) < 8:
            raise ValueError("API key appears too short.")
        env_updates[env_var] = secret_value

    config_path = _get_config_path()
    with credential_transaction(config_path):
        with _cfg_lock:
            config_data = load_credential_config(config_path)
            original_config = copy.deepcopy(config_data)
            model_cfg = config_data.get("model")
            if not isinstance(model_cfg, dict):
                model_cfg = {}
            model_cfg["provider"] = provider_id
            model_cfg["default"] = model_id
            if provider_id == "custom":
                model_cfg["base_url"] = base_url
                model_cfg["key_env"] = _CUSTOM_MODEL_KEY_ENV
                model_cfg.pop("api_key", None)
            else:
                if base_url:
                    model_cfg["base_url"] = base_url
                elif provider_id != "openai":
                    model_cfg.pop("base_url", None)
                model_cfg.pop("key_env", None)
                model_cfg.pop("api_key_env", None)
            config_data["model"] = model_cfg
            if env_updates:
                from agent.provider_credentials import (
                    load_credential_snapshot,
                )

                current_env = load_credential_snapshot(
                    config_path
                ).env
                changed_env_keys = tuple(
                    key
                    for key, value in env_updates.items()
                    if (
                        key in current_env
                        if value is None
                        else current_env.get(key) != value
                    )
                )
                capabilities = {
                    capability
                    for key in changed_env_keys
                    for capability in capability_epochs_for_secret_env(
                        original_config,
                        key,
                        env_values=current_env,
                    )
                }
                if capabilities:
                    bump_capability_config_epochs(
                        config_data,
                        *sorted(capabilities),
                    )
            _commit_expected_config_env(
                config_path,
                expected_config=original_config,
                desired_config=config_data,
                env_updates=env_updates,
            )
        response = _get_model_config_unlocked(refresh_runtime=False)
    warnings = _invoke_durable_mutation_post_commit(
        "set_main_model_config"
    )
    # The main provider/model participates in next-turn native-vs-text routing
    # and agent cache identity, but it does not change either auxiliary
    # capability fingerprint.  Refresh config/model caches without revoking a
    # still-valid vision or image-generation verification.
    return _merge_post_commit_warnings(
        response,
        warnings,
    )
