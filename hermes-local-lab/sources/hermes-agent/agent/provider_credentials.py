"""Named provider credential metadata and legacy API-key fallback."""

from __future__ import annotations

import copy
import ctypes
import errno
import hashlib
import json
import os as _os
import re
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

from hermes_constants import (
    get_config_path,
    get_hermes_config_path_override,
    get_hermes_home_override,
)

# Preserve the historical public module seam without using it for profile
# fallback resolution. Tests and downstream callers may monkeypatch ``os``.
os = _os

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt below.
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX has fcntl.
    _msvcrt = None


PROVIDER_FAMILY_ALIASES = {
    "alibaba": "alibaba_dashscope",
    "alibaba_dashscope": "alibaba_dashscope",
    "dashscope": "alibaba_dashscope",
    "zai": "zhipu",
    "zhipu": "zhipu",
    "zhipu-image": "zhipu",
    "ark": "doubao",
    "doubao": "doubao",
    "volcengine": "doubao",
    "baidu-qianfan": "qianfan",
    "qianfan": "qianfan",
    "minimax": "minimax",
    "minimax-image": "minimax",
    "custom": "custom",
    "custom-image": "custom",
}

LEGACY_API_KEY_ENV = {
    "alibaba_dashscope": ("DASHSCOPE_API_KEY",),
    "doubao": ("ARK_API_KEY",),
    "qianfan": ("QIANFAN_API_KEY",),
    "zhipu": ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
    "minimax": ("MINIMAX_API_KEY",),
}
AUTH_TYPES = (
    "api_key",
    "bearer_token",
    "access_key_secret",
    "service_account",
    "oauth",
    "no_auth",
)
_AUTH_TYPE_FIELDS: dict[str, tuple[dict[str, Any], ...]] = {
    "api_key": (
        {"name": "api_key", "label": "API Key", "secret": True, "required": True, "credential": True},
    ),
    "bearer_token": (
        {"name": "bearer_token", "label": "Bearer Token", "secret": True, "required": True, "credential": True},
    ),
    "access_key_secret": (
        {"name": "access_key_id", "label": "Access Key ID", "secret": False, "required": True, "credential": True},
        {"name": "access_key_secret", "label": "Access Key Secret", "secret": True, "required": True, "credential": True},
    ),
    "service_account": (
        {"name": "service_account_json", "label": "Service Account JSON", "secret": True, "required": True, "credential": True},
    ),
    "oauth": (),
    "no_auth": (),
}
_AUTH_TYPE_MESSAGES = {
    "api_key": "填写平台签发的 API Key；密钥只保存在本机。",
    "bearer_token": "填写平台签发的 Bearer Token；令牌只保存在本机。",
    "access_key_secret": "当前版本尚未实现 Access Key/Secret 签名适配器，不能在此配置。",
    "service_account": "当前版本尚未实现 Service Account 适配器，不能在此配置。",
    "oauth": "此 Provider 使用 OAuth 授权，请在对应平台完成授权后刷新状态。",
    "no_auth": "此 Provider 不需要认证，无需填写凭据。",
}
_CREDENTIAL_TRANSACTION_LOCK = threading.RLock()
_CREDENTIAL_TRANSACTION_STATE = threading.local()
_CREDENTIAL_ENV_PROJECTION_LOCK = threading.RLock()
_MISSING_ENV_VALUE = object()
_RUNTIME_ENV_BASELINES: dict[str, object] = {}
_RUNTIME_ENV_PROJECTIONS: dict[str, object] = {}
_CREDENTIAL_LOCK_NAME = ".taiji-credential-transaction.lock"
_CREDENTIAL_JOURNAL_NAME = ".taiji-credential-pair-intent.json"
_CREDENTIAL_ABORT_JOURNAL_NAME = ".taiji-credential-pair-abort.json"
_CREDENTIAL_JOURNAL_SCHEMA = "taiji-credential-pair-intent/v1"
_CREDENTIAL_GROUP_SHARED_JOURNAL_SCHEMA = (
    "taiji-credential-pair-intent/v2"
)
_CREDENTIAL_GROUP_SHARED_ENV = "HERMES_CREDENTIAL_GROUP_SHARED"
_MAX_CREDENTIAL_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_CREDENTIAL_ENV_BYTES = 1024 * 1024
_MAX_CREDENTIAL_JOURNAL_BYTES = 64 * 1024
_MAX_CREDENTIAL_STAGE_BYTES = _MAX_CREDENTIAL_CONFIG_BYTES
_CREDENTIAL_ORPHAN_STAGE_GRACE_SECONDS = 5 * 60
_LEGACY_PRIVATE_TARGET_MODES = frozenset({0o600, 0o640, 0o644})
_CREDENTIAL_ORPHAN_STAGE_RE = re.compile(
    r"^\.taiji-credential-.+-[0-9a-f]{32}\.stage$"
)
_ENV_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)


@dataclass(frozen=True, repr=False)
class CredentialSnapshot:
    """Exact credential-bearing disk state captured under one transaction."""

    config_path: Path
    env_path: Path
    config_exists: bool
    env_exists: bool
    config_sha256: str
    env_sha256: str
    config: dict[str, Any]
    env: dict[str, str]


class CredentialRecoveryError(RuntimeError):
    """A pending credential transaction cannot be proven safe to recover."""


class _CredentialCompareAndSwapError(CredentialRecoveryError):
    """A live CAS failed while every already-applied target is reversible."""


@dataclass(frozen=True)
class _CredentialAccessPolicy:
    group_shared: bool
    lock_mode: int
    data_mode: int
    artifact_mode: int


@dataclass(frozen=True)
class _CredentialTransactionSpec:
    logical_config_path: Path
    config_target: Path
    env_path: Path
    env_target: Path
    resource_roots: tuple[Path, ...]
    access_policy: _CredentialAccessPolicy


@dataclass(frozen=True)
class _CredentialResourceHandle:
    path: Path
    directory_fd: int
    device: int
    inode: int
    group_id: int


def _current_process_env_value(key: str) -> object:
    return _os.environ.get(key, _MISSING_ENV_VALUE)


def _project_process_env_unlocked(key: str, value: str | None) -> None:
    projected_value: object = (
        _MISSING_ENV_VALUE if value is None else value
    )
    current_value = _current_process_env_value(key)
    previous_projection = _RUNTIME_ENV_PROJECTIONS.get(
        key,
        _MISSING_ENV_VALUE,
    )
    if (
        key in _RUNTIME_ENV_PROJECTIONS
        and current_value is not previous_projection
        and current_value != previous_projection
    ):
        _RUNTIME_ENV_PROJECTIONS.pop(key, None)
        _RUNTIME_ENV_BASELINES.pop(key, None)
    if key not in _RUNTIME_ENV_BASELINES:
        _RUNTIME_ENV_BASELINES[key] = current_value
    if value is None:
        _os.environ.pop(key, None)
    else:
        _os.environ[key] = value
    _RUNTIME_ENV_PROJECTIONS[key] = projected_value


def _project_process_env_batch(
    updates: Mapping[str, str | None],
) -> None:
    """Project all keys atomically, including in-memory bookkeeping."""
    normalized_updates = dict(updates)
    with _CREDENTIAL_ENV_PROJECTION_LOCK:
        environ_before = {
            key: _current_process_env_value(key)
            for key in normalized_updates
        }
        baselines_before = dict(_RUNTIME_ENV_BASELINES)
        projections_before = dict(_RUNTIME_ENV_PROJECTIONS)
        try:
            for key, value in normalized_updates.items():
                _project_process_env_unlocked(key, value)
        except BaseException:
            rollback_error: BaseException | None = None
            for key, original_value in environ_before.items():
                try:
                    if original_value is _MISSING_ENV_VALUE:
                        _os.environ.pop(key, None)
                    else:
                        _os.environ[key] = str(original_value)
                except BaseException as exc:  # pragma: no cover - defensive.
                    rollback_error = exc
            _RUNTIME_ENV_BASELINES.clear()
            _RUNTIME_ENV_BASELINES.update(baselines_before)
            _RUNTIME_ENV_PROJECTIONS.clear()
            _RUNTIME_ENV_PROJECTIONS.update(projections_before)
            if rollback_error is not None:
                raise CredentialRecoveryError(
                    "process environment projection rollback failed"
                ) from rollback_error
            raise


def _project_process_env(key: str, value: str | None) -> None:
    """Project one key through the transactional batch primitive."""
    _project_process_env_batch({key: value})


def _process_env_fallback(key: str) -> str:
    """Return external baseline/override, never another profile projection."""
    with _CREDENTIAL_ENV_PROJECTION_LOCK:
        current_value = _current_process_env_value(key)
        if key not in _RUNTIME_ENV_PROJECTIONS:
            return (
                ""
                if current_value is _MISSING_ENV_VALUE
                else str(current_value)
            )
        projected_value = _RUNTIME_ENV_PROJECTIONS[key]
        if (
            current_value is not projected_value
            and current_value != projected_value
        ):
            _RUNTIME_ENV_PROJECTIONS.pop(key, None)
            _RUNTIME_ENV_BASELINES.pop(key, None)
            return (
                ""
                if current_value is _MISSING_ENV_VALUE
                else str(current_value)
            )
        baseline = _RUNTIME_ENV_BASELINES.get(
            key,
            _MISSING_ENV_VALUE,
        )
        return "" if baseline is _MISSING_ENV_VALUE else str(baseline)


def process_env_fallback_allowed(
    allow_process_fallback: bool | None = None,
) -> bool:
    """Allow ambient env only outside request-local profile scopes."""
    if allow_process_fallback is not None:
        return bool(allow_process_fallback)
    return bool(
        get_hermes_config_path_override() is None
        and get_hermes_home_override() is None
    )


def process_env_fallback_value(
    key: str,
    *,
    allow_process_fallback: bool | None = None,
) -> str:
    """Read an ambient baseline only when the current scope permits it."""
    if not process_env_fallback_allowed(allow_process_fallback):
        return ""
    return _process_env_fallback(key)


def _supports_exact_posix_modes() -> bool:
    return _os.name == "posix" and callable(getattr(_os, "fchmod", None))


def _credential_access_policy_from_env() -> _CredentialAccessPolicy:
    raw_value = _os.environ.get(_CREDENTIAL_GROUP_SHARED_ENV)
    if raw_value in {None, "", "0"}:
        return _CredentialAccessPolicy(
            group_shared=False,
            lock_mode=0o600,
            data_mode=0o600,
            artifact_mode=0o600,
        )
    if raw_value != "1":
        raise CredentialRecoveryError(
            f"{_CREDENTIAL_GROUP_SHARED_ENV} must be exactly 0 or 1"
        )
    if _os.name != "posix":
        raise CredentialRecoveryError(
            "group-shared credential transactions require POSIX"
        )
    return _CredentialAccessPolicy(
        group_shared=True,
        lock_mode=0o660,
        data_mode=0o640,
        artifact_mode=0o640,
    )


def _active_credential_access_policy() -> _CredentialAccessPolicy:
    policy = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "access_policy",
        None,
    )
    if isinstance(policy, _CredentialAccessPolicy):
        return policy
    return _credential_access_policy_from_env()


def _credential_lock_mode() -> int:
    return _active_credential_access_policy().lock_mode


def _credential_data_mode() -> int:
    return _active_credential_access_policy().data_mode


def _credential_artifact_mode() -> int:
    return _active_credential_access_policy().artifact_mode


def _credential_platform_name() -> str:
    return sys.platform


def _require_secure_pair_transaction_platform() -> None:
    if (
        _credential_platform_name() not in {"darwin", "linux"}
        or _fcntl is None
    ):
        raise CredentialRecoveryError(
            "durable credential transactions are not supported "
            "on this platform"
        )


def _set_fd_mode_if_supported(file_fd: int, mode: int) -> None:
    fchmod = getattr(_os, "fchmod", None)
    if callable(fchmod):
        current_mode = stat.S_IMODE(_os.fstat(file_fd).st_mode)
        if current_mode != mode:
            fchmod(file_fd, mode)


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: Any,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "duplicate mapping key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _credential_config_path(config_path: Path | None = None) -> Path:
    if config_path is not None:
        resolved = Path(config_path).expanduser()
    else:
        resolved = get_config_path()
    if not resolved.is_absolute():
        raise ValueError("credential config path must be absolute")
    _validate_credential_config_name(resolved)
    return resolved


def _validate_credential_config_name(path: Path) -> None:
    reserved_names = {
        ".env",
        _CREDENTIAL_LOCK_NAME,
        _CREDENTIAL_JOURNAL_NAME,
        _CREDENTIAL_ABORT_JOURNAL_NAME,
    }
    if (
        path.name in reserved_names
        or (
            path.name.startswith(".taiji-credential-")
            and path.name.endswith(".stage")
        )
    ):
        raise ValueError("credential config path uses a reserved name")


def _credential_lock_root(config_path: Path) -> Path:
    """Return the stable physical lock/intent root for a config alias."""
    return Path(_os.path.realpath(config_path)).parent


def _physical_credential_path(path: Path) -> Path:
    return Path(_os.path.realpath(path))


def _credential_transaction_spec(
    config_path: Path | None = None,
) -> _CredentialTransactionSpec:
    transaction_depth = int(
        getattr(_CREDENTIAL_TRANSACTION_STATE, "depth", 0)
    )
    if transaction_depth:
        _assert_active_resource_dirs_unchanged()
        access_policy = getattr(
            _CREDENTIAL_TRANSACTION_STATE,
            "access_policy",
            None,
        )
        if not isinstance(access_policy, _CredentialAccessPolicy):
            raise RuntimeError(
                "active credential transaction has no access policy"
            )
    else:
        access_policy = _credential_access_policy_from_env()
    logical_config_path = _credential_config_path(config_path)
    config_target = _physical_credential_path(logical_config_path)
    config_parent_existed = config_target.parent.exists()
    config_target.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=(0o2770 if access_policy.group_shared else 0o777),
    )
    if access_policy.group_shared and not config_parent_existed:
        config_target.parent.chmod(0o2770)
    env_path = config_target.parent / ".env"
    if _os.path.lexists(env_path) and not env_path.exists():
        raise ValueError("credential env cannot be read safely")
    env_target = _physical_credential_path(env_path)
    if config_target == env_target:
        raise ValueError("credential config and env resolve to the same target")
    try:
        config_stat = _os.stat(config_target, follow_symlinks=False)
        env_stat = _os.stat(env_target, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if (
            config_stat.st_dev,
            config_stat.st_ino,
        ) == (
            env_stat.st_dev,
            env_stat.st_ino,
        ):
            raise ValueError(
                "credential config and env resolve to the same target"
            )
    _validate_credential_config_name(config_target)
    resource_roots = tuple(
        sorted(
            {config_target.parent, env_target.parent},
            key=lambda path: str(path),
        )
    )
    return _CredentialTransactionSpec(
        logical_config_path=logical_config_path,
        config_target=config_target,
        env_path=env_path,
        env_target=env_target,
        resource_roots=resource_roots,
        access_policy=access_policy,
    )


def _credential_target_bindings(
    spec: _CredentialTransactionSpec,
) -> dict[Path, Path]:
    return {
        spec.logical_config_path: spec.config_target,
        spec.config_target: spec.config_target,
        spec.env_path: spec.env_target,
        spec.env_target: spec.env_target,
    }


def _validate_shared_credential_resource_root(
    resource_root: Path,
    root_stat: _os.stat_result,
    access_policy: _CredentialAccessPolicy,
) -> None:
    if not access_policy.group_shared:
        return
    root_mode = stat.S_IMODE(root_stat.st_mode)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_mode != 0o2770
    ):
        raise CredentialRecoveryError(
            "shared credential resource root must be a 2770 setgid directory"
        )
    effective_uid = (
        _os.geteuid()
        if callable(getattr(_os, "geteuid", None))
        else None
    )
    if effective_uid == 0:
        return
    trusted_groups = {
        group_id
        for group_id in (
            (
                _os.getegid()
                if callable(getattr(_os, "getegid", None))
                else None
            ),
            (
                _os.getgid()
                if callable(getattr(_os, "getgid", None))
                else None
            ),
        )
        if group_id is not None
    }
    getgroups = getattr(_os, "getgroups", None)
    if callable(getgroups):
        trusted_groups.update(getgroups())
    if root_stat.st_gid not in trusted_groups:
        raise CredentialRecoveryError(
            "shared credential resource root group is not trusted"
        )


def _open_credential_resource_handle(
    resource_root: Path,
    access_policy: _CredentialAccessPolicy,
) -> _CredentialResourceHandle:
    if _os.name != "posix":
        try:
            path_stat = _os.stat(resource_root, follow_symlinks=False)
        except OSError as exc:
            raise CredentialRecoveryError(
                "credential resource directory cannot be pinned"
            ) from exc
        if not stat.S_ISDIR(path_stat.st_mode):
            raise CredentialRecoveryError(
                "credential resource root is not a directory"
            )
        _validate_shared_credential_resource_root(
            resource_root,
            path_stat,
            access_policy,
        )
        return _CredentialResourceHandle(
            path=resource_root,
            directory_fd=-1,
            device=path_stat.st_dev,
            inode=path_stat.st_ino,
            group_id=path_stat.st_gid,
        )
    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_DIRECTORY", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    try:
        directory_fd = _os.open(resource_root, flags)
    except OSError as exc:
        raise CredentialRecoveryError(
            "credential resource directory cannot be pinned"
        ) from exc
    try:
        opened_stat = _os.fstat(directory_fd)
        path_stat = _os.stat(resource_root, follow_symlinks=False)
        identity = (opened_stat.st_dev, opened_stat.st_ino)
        if (
            not stat.S_ISDIR(opened_stat.st_mode)
            or not stat.S_ISDIR(path_stat.st_mode)
            or identity != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise CredentialRecoveryError(
                "credential resource directory changed while being pinned"
            )
        _validate_shared_credential_resource_root(
            resource_root,
            opened_stat,
            access_policy,
        )
        return _CredentialResourceHandle(
            path=resource_root,
            directory_fd=directory_fd,
            device=opened_stat.st_dev,
            inode=opened_stat.st_ino,
            group_id=opened_stat.st_gid,
        )
    except BaseException:
        _os.close(directory_fd)
        raise


def _assert_credential_resource_handle_unchanged(
    handle: _CredentialResourceHandle,
) -> None:
    try:
        path_stat = _os.stat(handle.path, follow_symlinks=False)
        opened_stat = (
            _os.fstat(handle.directory_fd)
            if handle.directory_fd >= 0
            else path_stat
        )
    except OSError as exc:
        raise CredentialRecoveryError(
            "credential resource directory changed after lock acquisition"
        ) from exc
    expected_identity = (handle.device, handle.inode)
    if (
        not stat.S_ISDIR(opened_stat.st_mode)
        or not stat.S_ISDIR(path_stat.st_mode)
        or (opened_stat.st_dev, opened_stat.st_ino) != expected_identity
        or (path_stat.st_dev, path_stat.st_ino) != expected_identity
    ):
        raise CredentialRecoveryError(
            "credential resource directory changed after lock acquisition"
        )
    access_policy = _active_credential_access_policy()
    _validate_shared_credential_resource_root(
        handle.path,
        opened_stat,
        access_policy,
    )
    if (
        access_policy.group_shared
        and (
            opened_stat.st_gid != handle.group_id
            or path_stat.st_gid != handle.group_id
        )
    ):
        raise CredentialRecoveryError(
            "shared credential resource root group changed"
        )


def _assert_active_resource_dirs_unchanged() -> None:
    handles = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "resource_handles",
        None,
    )
    if not isinstance(handles, dict):
        return
    for handle in handles.values():
        _assert_credential_resource_handle_unchanged(handle)


def _open_credential_lock_file(
    resource_root: Path,
    resource_handle: _CredentialResourceHandle,
    open_flags: int,
    lock_mode: int,
) -> int:
    """Open the pinned lock, tolerating one Darwin concurrent-create loser."""

    def open_lock(flags: int) -> int:
        if resource_handle.directory_fd >= 0:
            return _os.open(
                _CREDENTIAL_LOCK_NAME,
                flags,
                lock_mode,
                dir_fd=resource_handle.directory_fd,
            )
        return _os.open(
            resource_root / _CREDENTIAL_LOCK_NAME,
            flags,
            lock_mode,
        )

    try:
        return open_lock(open_flags)
    except FileNotFoundError:
        _assert_credential_resource_handle_unchanged(resource_handle)

    existing_flags = open_flags & ~_os.O_CREAT & ~getattr(_os, "O_EXCL", 0)
    try:
        return open_lock(existing_flags)
    except FileNotFoundError:
        _assert_credential_resource_handle_unchanged(resource_handle)

    exclusive_create_flags = (
        open_flags | _os.O_CREAT | getattr(_os, "O_EXCL", 0)
    )
    try:
        return open_lock(exclusive_create_flags)
    except FileExistsError:
        _assert_credential_resource_handle_unchanged(resource_handle)
        return open_lock(existing_flags)


def _active_resource_handle_for_target(
    target_path: Path,
) -> _CredentialResourceHandle | None:
    _assert_active_resource_dirs_unchanged()
    handles = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "resource_handles",
        None,
    )
    if not isinstance(handles, dict):
        return None
    return handles.get(Path(target_path).parent)


def _enforce_credential_fd_policy(
    file_fd: int,
    *,
    access_policy: _CredentialAccessPolicy,
    expected_group_id: int,
    expected_mode: int,
    label: str,
) -> None:
    if access_policy.group_shared:
        file_stat = _os.fstat(file_fd)
        if file_stat.st_gid != expected_group_id:
            fchown = getattr(_os, "fchown", None)
            if not callable(fchown):
                raise CredentialRecoveryError(
                    f"shared credential {label} group cannot be enforced"
                )
            try:
                fchown(file_fd, -1, expected_group_id)
            except OSError as exc:
                raise CredentialRecoveryError(
                    f"shared credential {label} group is not trusted"
                ) from exc
    try:
        _set_fd_mode_if_supported(file_fd, expected_mode)
    except OSError as exc:
        raise CredentialRecoveryError(
            f"credential {label} mode cannot be enforced"
        ) from exc
    if access_policy.group_shared:
        verified_stat = _os.fstat(file_fd)
        if (
            verified_stat.st_gid != expected_group_id
            or stat.S_IMODE(verified_stat.st_mode) != expected_mode
        ):
            raise CredentialRecoveryError(
                f"shared credential {label} policy was not applied"
            )


def _enforce_active_credential_fd_policy(
    file_fd: int,
    target_path: Path,
    *,
    expected_mode: int,
    label: str,
) -> None:
    access_policy = _active_credential_access_policy()
    handle = _active_resource_handle_for_target(target_path)
    if handle is None:
        if access_policy.group_shared:
            raise CredentialRecoveryError(
                f"shared credential {label} is outside the pinned roots"
            )
        _enforce_credential_fd_policy(
            file_fd,
            access_policy=access_policy,
            expected_group_id=_os.fstat(file_fd).st_gid,
            expected_mode=expected_mode,
            label=label,
        )
        return
    _enforce_credential_fd_policy(
        file_fd,
        access_policy=access_policy,
        expected_group_id=handle.group_id,
        expected_mode=expected_mode,
        label=label,
    )


def _open_active_target(
    target_path: Path,
    flags: int,
    mode: int | None = None,
) -> int:
    target = Path(target_path)
    handle = _active_resource_handle_for_target(target)
    if handle is None or handle.directory_fd < 0:
        if mode is None:
            return _os.open(target, flags)
        return _os.open(target, flags, mode)
    if mode is None:
        return _os.open(
            target.name,
            flags,
            dir_fd=handle.directory_fd,
        )
    return _os.open(
        target.name,
        flags,
        mode,
        dir_fd=handle.directory_fd,
    )


def _stat_active_target(
    target_path: Path,
    *,
    follow_symlinks: bool = False,
) -> _os.stat_result:
    target = Path(target_path)
    handle = _active_resource_handle_for_target(target)
    if handle is None or handle.directory_fd < 0:
        return _os.stat(target, follow_symlinks=follow_symlinks)
    return _os.stat(
        target.name,
        dir_fd=handle.directory_fd,
        follow_symlinks=follow_symlinks,
    )


def _unlink_active_target(
    target_path: Path,
    *,
    missing_ok: bool = False,
) -> None:
    target = Path(target_path)
    handle = _active_resource_handle_for_target(target)
    try:
        if handle is None or handle.directory_fd < 0:
            target.unlink()
        else:
            _os.unlink(target.name, dir_fd=handle.directory_fd)
    except FileNotFoundError:
        if not missing_ok:
            raise


def _active_credential_target(path: Path) -> Path:
    """Return a transaction-pinned target and reject alias retargeting."""
    logical_path = Path(path)
    _assert_active_resource_dirs_unchanged()
    bindings = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "target_bindings",
        None,
    )
    if not isinstance(bindings, dict) or logical_path not in bindings:
        return _physical_credential_path(logical_path)
    pinned_target = bindings[logical_path]
    if _physical_credential_path(logical_path) != pinned_target:
        raise CredentialRecoveryError(
            "credential target changed after transaction lock acquisition"
        )
    return pinned_target


def _assert_active_credential_targets_unchanged() -> None:
    _assert_active_resource_dirs_unchanged()
    bindings = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "target_bindings",
        None,
    )
    if not isinstance(bindings, dict):
        return
    for logical_path in bindings:
        _active_credential_target(logical_path)


def _lock_file_descriptor(lock_fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
        return
    if _msvcrt is not None:  # pragma: no cover - exercised on Windows.
        if _os.fstat(lock_fd).st_size < 1:
            _os.ftruncate(lock_fd, 1)
        _os.lseek(lock_fd, 0, _os.SEEK_SET)
        _msvcrt.locking(lock_fd, _msvcrt.LK_LOCK, 1)


def _unlock_file_descriptor(lock_fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:  # pragma: no cover - exercised on Windows.
        _os.lseek(lock_fd, 0, _os.SEEK_SET)
        _msvcrt.locking(lock_fd, _msvcrt.LK_UNLCK, 1)


@contextmanager
def credential_transaction(config_path: Path | None = None):
    """Serialize credential metadata and Secret projections across processes."""
    spec = _credential_transaction_spec(config_path)
    lock_root = spec.config_target.parent
    with _CREDENTIAL_TRANSACTION_LOCK:
        depth = int(getattr(_CREDENTIAL_TRANSACTION_STATE, "depth", 0))
        if depth:
            active_resource_roots = getattr(
                _CREDENTIAL_TRANSACTION_STATE,
                "resource_roots",
                None,
            )
            if active_resource_roots != spec.resource_roots:
                raise RuntimeError(
                    "nested credential transactions must target the same "
                    "credential resource set"
                )
            active_bindings = getattr(
                _CREDENTIAL_TRANSACTION_STATE,
                "target_bindings",
                None,
            )
            if not isinstance(active_bindings, dict):
                raise RuntimeError(
                    "nested credential transaction has no active target bindings"
                )
            nested_bindings = _credential_target_bindings(spec)
            for logical_path, pinned_target in nested_bindings.items():
                existing_target = active_bindings.get(logical_path)
                if (
                    existing_target is not None
                    and existing_target != pinned_target
                ):
                    raise CredentialRecoveryError(
                        "credential target changed after transaction "
                        "lock acquisition"
                    )
            combined_bindings = dict(active_bindings)
            combined_bindings.update(nested_bindings)
            _CREDENTIAL_TRANSACTION_STATE.target_bindings = (
                combined_bindings
            )
            active_spec = getattr(
                _CREDENTIAL_TRANSACTION_STATE,
                "spec",
                None,
            )
            _CREDENTIAL_TRANSACTION_STATE.spec = spec
            _CREDENTIAL_TRANSACTION_STATE.depth = depth + 1
            try:
                _assert_active_credential_targets_unchanged()
                yield spec
                _assert_active_credential_targets_unchanged()
            finally:
                _CREDENTIAL_TRANSACTION_STATE.depth = depth
                _CREDENTIAL_TRANSACTION_STATE.target_bindings = (
                    active_bindings
                )
                _CREDENTIAL_TRANSACTION_STATE.spec = active_spec
            return

        resource_handles: dict[Path, _CredentialResourceHandle] = {}
        lock_fds: list[int] = []
        try:
            for resource_root in spec.resource_roots:
                resource_handles[resource_root] = (
                    _open_credential_resource_handle(
                        resource_root,
                        spec.access_policy,
                    )
                )
            open_flags = _os.O_CREAT | _os.O_RDWR
            open_flags |= getattr(_os, "O_CLOEXEC", 0)
            open_flags |= getattr(_os, "O_NOFOLLOW", 0)
            for resource_root in spec.resource_roots:
                resource_handle = resource_handles[resource_root]
                lock_fd = _open_credential_lock_file(
                    resource_root,
                    resource_handle,
                    open_flags,
                    spec.access_policy.lock_mode,
                )
                try:
                    lock_stat = _os.fstat(lock_fd)
                    if (
                        not stat.S_ISREG(lock_stat.st_mode)
                        or lock_stat.st_nlink != 1
                    ):
                        raise OSError(
                            "credential transaction lock must be a regular file"
                        )
                    _enforce_credential_fd_policy(
                        lock_fd,
                        access_policy=spec.access_policy,
                        expected_group_id=resource_handle.group_id,
                        expected_mode=spec.access_policy.lock_mode,
                        label="transaction lock",
                    )
                    _lock_file_descriptor(lock_fd)
                except BaseException:
                    _os.close(lock_fd)
                    raise
                lock_fds.append(lock_fd)
            _CREDENTIAL_TRANSACTION_STATE.depth = 1
            _CREDENTIAL_TRANSACTION_STATE.resource_roots = (
                spec.resource_roots
            )
            _CREDENTIAL_TRANSACTION_STATE.resource_handles = (
                resource_handles
            )
            _CREDENTIAL_TRANSACTION_STATE.target_bindings = (
                _credential_target_bindings(spec)
            )
            _CREDENTIAL_TRANSACTION_STATE.spec = spec
            _CREDENTIAL_TRANSACTION_STATE.access_policy = (
                spec.access_policy
            )
            try:
                _assert_active_credential_targets_unchanged()
                _normalize_shared_credential_targets(spec)
                _CREDENTIAL_TRANSACTION_STATE.last_recovery = (
                    _recover_pending_transaction_unlocked(
                        lock_root,
                        logical_config_path=spec.logical_config_path,
                    )
                )
                _assert_active_credential_targets_unchanged()
                yield spec
                _assert_active_credential_targets_unchanged()
            finally:
                _CREDENTIAL_TRANSACTION_STATE.depth = 0
                _CREDENTIAL_TRANSACTION_STATE.resource_roots = None
                _CREDENTIAL_TRANSACTION_STATE.resource_handles = None
                _CREDENTIAL_TRANSACTION_STATE.target_bindings = None
                _CREDENTIAL_TRANSACTION_STATE.spec = None
                _CREDENTIAL_TRANSACTION_STATE.access_policy = None
                _CREDENTIAL_TRANSACTION_STATE.last_recovery = None
        finally:
            for lock_fd in reversed(lock_fds):
                try:
                    _unlock_file_descriptor(lock_fd)
                finally:
                    _os.close(lock_fd)
            for resource_handle in resource_handles.values():
                if resource_handle.directory_fd >= 0:
                    _os.close(resource_handle.directory_fd)


def normalize_credential_id(credential_id: object) -> str:
    """Return a stable, environment-variable-safe credential identifier."""
    raw = str(credential_id or "").strip().lower()
    normalized = re.sub(r"[\s_]+", "-", raw)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized):
        raise ValueError("credential id must contain only letters, numbers, spaces, '_' or '-'")
    return normalized


def provider_family(provider: object) -> str:
    """Map provider aliases used by capabilities to their credential family."""
    normalized = str(provider or "").strip().lower()
    if normalized.startswith("custom:"):
        return "custom"
    return PROVIDER_FAMILY_ALIASES.get(normalized, normalized)


def auth_schema(auth_type: object) -> dict[str, Any]:
    """Describe an auth shape without claiming an unimplemented adapter exists."""
    normalized = str(auth_type or "api_key").strip().lower()
    if normalized not in AUTH_TYPES:
        raise ValueError(f"unsupported auth_type: {normalized}")
    editable = normalized == "api_key"
    return {
        "auth_type": normalized,
        "credential_fields": [dict(field) for field in _AUTH_TYPE_FIELDS[normalized]],
        "editable": editable,
        "message": _AUTH_TYPE_MESSAGES[normalized],
    }


def credential_secret_env(credential_id: object) -> str:
    """Return the dedicated environment variable for a named credential."""
    normalized = normalize_credential_id(credential_id)
    return f"TAIJI_CREDENTIAL_{normalized.upper().replace('-', '_')}_API_KEY"


def _credential_secret_value(
    secret_env: str,
    config_path: Path | None = None,
    *,
    allow_process_fallback: bool = True,
    fallback_when_env_key_missing: bool = True,
) -> str:
    env_path = (
        _physical_credential_path(_credential_config_path(config_path)).parent
        / ".env"
    )
    if not env_path.exists() and _os.path.lexists(env_path):
        raise ValueError("credential env cannot be read safely")
    env_exists, env_payload = _read_optional_bytes(
        env_path,
        max_bytes=_MAX_CREDENTIAL_ENV_BYTES,
        label="env",
    )
    if not env_exists:
        return (
            _process_env_fallback(secret_env)
            if allow_process_fallback
            else ""
        )
    try:
        from dotenv import dotenv_values

        env_text = env_payload.decode("utf-8-sig")
        matching_keys = 0
        for raw_line in env_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            if line.split("=", 1)[0].strip() == secret_env:
                matching_keys += 1
        if matching_keys > 1:
            raise ValueError("credential env contains duplicate keys")
        value = str(
            dotenv_values(
                stream=StringIO(env_text),
                interpolate=False,
            ).get(secret_env)
            or ""
        )
        if (
            value
            or not allow_process_fallback
            or not fallback_when_env_key_missing
        ):
            return value
        return _process_env_fallback(secret_env)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("credential env cannot be read safely") from exc


def resolve_secret_env_value(
    secret_env: str,
    *,
    config_path: Path | None = None,
    allow_process_fallback: bool | None = None,
) -> str:
    """Resolve one exact secret env through the shared profile policy."""
    allowed = process_env_fallback_allowed(allow_process_fallback)
    return _credential_secret_value(
        str(secret_env or "").strip(),
        config_path,
        allow_process_fallback=allowed,
    ).strip()


def _parse_config_bytes(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8-sig")
        loaded = (
            yaml.load(
                text,
                Loader=_StrictSafeLoader,
            )
            or {}
        )
    except ConstructorError as exc:
        if exc.problem == "duplicate mapping key":
            raise ValueError("credential config contains duplicate mapping keys") from exc
        raise ValueError("credential config cannot be read safely") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("credential config cannot be read safely") from exc
    if not isinstance(loaded, dict):
        raise ValueError("credential config must be a mapping")
    return loaded


def _load_config_data(config_path: Path | None = None) -> dict[str, Any]:
    config_path = _credential_config_path(config_path)
    exists, payload = _read_optional_bytes(
        config_path,
        max_bytes=_MAX_CREDENTIAL_CONFIG_BYTES,
        label="config",
    )
    if not exists:
        return {}
    return _parse_config_bytes(payload)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_fd_bounded(
    file_fd: int,
    *,
    max_bytes: int,
    label: str,
    error_type: type[Exception],
) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = _os.read(file_fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        raise error_type(f"credential {label} exceeds maximum size")
    return payload


def _read_optional_bytes(
    path: Path,
    *,
    max_bytes: int | None = None,
    label: str | None = None,
    error_type: type[Exception] = ValueError,
) -> tuple[bool, bytes]:
    logical_path = Path(path)
    if max_bytes is None:
        if logical_path.name == ".env":
            max_bytes = _MAX_CREDENTIAL_ENV_BYTES
        else:
            max_bytes = _MAX_CREDENTIAL_CONFIG_BYTES
    if label is None:
        label = "env" if logical_path.name == ".env" else "config"
    real_path = _active_credential_target(logical_path)
    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    try:
        file_fd = _open_active_target(real_path, flags)
    except FileNotFoundError:
        return False, b""
    except OSError as exc:
        raise error_type(
            f"credential {label} cannot be read safely"
        ) from exc
    try:
        file_stat = _os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise error_type(
                f"credential {label} is not a regular file"
            )
        if file_stat.st_nlink != 1:
            raise error_type(f"credential {label} is hard-linked")
        if file_stat.st_size > max_bytes:
            raise error_type(
                f"credential {label} exceeds maximum size"
            )
        payload = _read_fd_bounded(
            file_fd,
            max_bytes=max_bytes,
            label=label,
            error_type=error_type,
        )
    finally:
        _os.close(file_fd)
    return True, payload


def _parse_env_bytes(payload: bytes) -> dict[str, str]:
    if b"\x00" in payload:
        raise ValueError("credential env cannot contain NUL bytes")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("credential env must be UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_ASSIGNMENT_RE.fullmatch(raw_line)
        if match is None:
            raise ValueError("credential env contains an invalid assignment")
        key = match.group("key")
        if key in values:
            raise ValueError("credential env contains duplicate keys")
        values[key] = _env_assignment_value(match.group("value"))
    return values


_AT_FDCWD = -2
_DARWIN_RENAME_SWAP = 0x2
_DARWIN_RENAME_EXCL = 0x4
_LINUX_RENAME_NOREPLACE = 0x1
_LINUX_RENAME_EXCHANGE = 0x2


def _rename_path_reference(path: Path) -> tuple[int, bytes]:
    target = Path(path)
    handle = _active_resource_handle_for_target(target)
    if handle is not None and handle.directory_fd >= 0:
        return handle.directory_fd, _os.fsencode(target.name)
    return _AT_FDCWD, _os.fsencode(str(target))


def _raise_rename_error(error_number: int, source: Path, target: Path) -> None:
    if error_number == errno.EEXIST:
        raise FileExistsError(
            error_number,
            _os.strerror(error_number),
            str(target),
        )
    if error_number == errno.ENOENT:
        raise FileNotFoundError(
            error_number,
            _os.strerror(error_number),
            str(source),
        )
    raise OSError(
        error_number,
        _os.strerror(error_number),
        f"{source} -> {target}",
    )


def _darwin_renameatx_np(
    source: Path,
    target: Path,
    flags: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = getattr(libc, "renameatx_np", None)
    if renameatx_np is None:
        raise OSError(
            errno.ENOSYS,
            "renameatx_np is unavailable",
        )
    renameatx_np.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameatx_np.restype = ctypes.c_int
    source_fd, source_name = _rename_path_reference(source)
    target_fd, target_name = _rename_path_reference(target)
    ctypes.set_errno(0)
    result = renameatx_np(
        source_fd,
        source_name,
        target_fd,
        target_name,
        flags,
    )
    if result != 0:
        _raise_rename_error(ctypes.get_errno(), source, target)


def _linux_renameat2(
    source: Path,
    target: Path,
    flags: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_fd, source_name = _rename_path_reference(source)
    target_fd, target_name = _rename_path_reference(target)
    renameat2 = getattr(libc, "renameat2", None)
    ctypes.set_errno(0)
    if renameat2 is not None:
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            source_fd,
            source_name,
            target_fd,
            target_name,
            flags,
        )
    else:
        machine = _os.uname().machine.lower()
        syscall_number = {
            "x86_64": 316,
            "amd64": 316,
            "aarch64": 276,
            "arm64": 276,
        }.get(machine)
        if syscall_number is None:
            raise OSError(
                errno.ENOSYS,
                f"renameat2 syscall is unknown for {machine}",
            )
        syscall = libc.syscall
        syscall.restype = ctypes.c_long
        result = syscall(
            ctypes.c_long(syscall_number),
            ctypes.c_int(source_fd),
            ctypes.c_char_p(source_name),
            ctypes.c_int(target_fd),
            ctypes.c_char_p(target_name),
            ctypes.c_uint(flags),
        )
    if result != 0:
        _raise_rename_error(ctypes.get_errno(), source, target)


def _atomic_rename_with_platform_flag(
    source: Path,
    target: Path,
    *,
    darwin_flag: int,
    linux_flag: int,
) -> None:
    _assert_active_resource_dirs_unchanged()
    platform_name = _credential_platform_name()
    try:
        if platform_name == "darwin":
            _darwin_renameatx_np(source, target, darwin_flag)
            return
        if platform_name == "linux":
            _linux_renameat2(source, target, linux_flag)
            return
    except OSError as exc:
        unsupported_errors = {
            errno.ENOSYS,
            errno.EINVAL,
            errno.EXDEV,
        }
        if hasattr(errno, "ENOTSUP"):
            unsupported_errors.add(errno.ENOTSUP)
        if hasattr(errno, "EOPNOTSUPP"):
            unsupported_errors.add(errno.EOPNOTSUPP)
        if exc.errno in unsupported_errors:
            raise CredentialRecoveryError(
                "atomic credential rename is not supported "
                "by this platform or filesystem"
            ) from exc
        raise
    raise CredentialRecoveryError(
        "atomic credential rename is not supported on this platform"
    )


def _atomic_exchange_entries(source: Path, target: Path) -> None:
    source = Path(source)
    target = Path(target)
    _atomic_rename_with_platform_flag(
        source,
        target,
        darwin_flag=_DARWIN_RENAME_SWAP,
        linux_flag=_LINUX_RENAME_EXCHANGE,
    )
    expectation = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "exchange_expectation",
        None,
    )
    if not (
        isinstance(expectation, tuple)
        and len(expectation) == 4
        and expectation[0] == source
        and expectation[1] == target
    ):
        return
    expected_source_identity = expectation[2]
    expected_target_identity = expectation[3]
    post_source = _stat_active_target(
        source,
        follow_symlinks=False,
    )
    post_target = _stat_active_target(
        target,
        follow_symlinks=False,
    )
    post_source_identity = (post_source.st_dev, post_source.st_ino)
    post_target_identity = (post_target.st_dev, post_target.st_ino)
    if (
        post_source_identity == expected_target_identity
        and post_target_identity == expected_source_identity
    ):
        return
    # The exchange itself is reversible even if one name was raced. Restore
    # the two entries to their observed post-exchange names before reporting
    # CAS failure. A second identity check is mandatory: a target writer can
    # retarget either name after the first check but before this exchange.
    _atomic_rename_with_platform_flag(
        source,
        target,
        darwin_flag=_DARWIN_RENAME_SWAP,
        linux_flag=_LINUX_RENAME_EXCHANGE,
    )
    reversed_source = _stat_active_target(
        source,
        follow_symlinks=False,
    )
    reversed_target = _stat_active_target(
        target,
        follow_symlinks=False,
    )
    if (
        (reversed_source.st_dev, reversed_source.st_ino)
        != post_target_identity
        or (reversed_target.st_dev, reversed_target.st_ino)
        != post_source_identity
    ):
        # Never downgrade this to a normal CAS failure. The durable commit
        # intent must remain so recovery cannot unlink a late external entry
        # that the reverse exchange moved under the stage name.
        raise CredentialRecoveryError(
            "credential entries changed during atomic exchange rollback; "
            "durable intent retained"
        )
    raise _CredentialCompareAndSwapError(
        "credential stage changed during atomic exchange"
    )


def _atomic_rename_noreplace(source: Path, target: Path) -> None:
    _atomic_rename_with_platform_flag(
        Path(source),
        Path(target),
        darwin_flag=_DARWIN_RENAME_EXCL,
        linux_flag=_LINUX_RENAME_NOREPLACE,
    )


def _resolved_write_target(path: Path) -> Path:
    return _active_credential_target(path)


def _fsync_directory(path: Path) -> None:
    handle = _active_resource_handle_for_target(Path(path) / "_")
    if handle is not None and handle.directory_fd >= 0:
        _os.fsync(handle.directory_fd)
        return
    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_DIRECTORY", 0)
    directory_fd: int | None = None
    try:
        directory_fd = _os.open(path, flags)
        _os.fsync(directory_fd)
    except OSError:
        # Windows does not expose POSIX-style durable directory handles.
        if _fcntl is not None:
            raise
    finally:
        if directory_fd is not None:
            _os.close(directory_fd)


def _existing_target_mode(path: Path, *, default: int = 0o600) -> int:
    real_path = _resolved_write_target(path)
    try:
        target_stat = _stat_active_target(
            real_path,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        access_policy = _active_credential_access_policy()
        return (
            access_policy.data_mode
            if access_policy.group_shared
            else default
        )
    except OSError as exc:
        raise ValueError(f"credential target cannot be inspected: {path.name}") from exc
    if not stat.S_ISREG(target_stat.st_mode):
        raise ValueError("credential target is not a regular file")
    if target_stat.st_nlink != 1:
        raise ValueError("credential target is hard-linked")
    target_mode = stat.S_IMODE(target_stat.st_mode)
    access_policy = _active_credential_access_policy()
    if access_policy.group_shared:
        if target_mode != access_policy.data_mode:
            raise CredentialRecoveryError(
                "shared credential target mode is inconsistent"
            )
        return access_policy.data_mode
    return target_mode


def _normalize_shared_credential_targets(
    spec: _CredentialTransactionSpec,
) -> None:
    if not spec.access_policy.group_shared:
        return
    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    for target_path in (spec.config_target, spec.env_target):
        try:
            target_fd = _open_active_target(target_path, flags)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CredentialRecoveryError(
                "shared credential target cannot be opened safely"
            ) from exc
        try:
            target_stat = _os.fstat(target_fd)
            if (
                not stat.S_ISREG(target_stat.st_mode)
                or target_stat.st_nlink != 1
            ):
                raise CredentialRecoveryError(
                    "shared credential target is not a private regular file"
                )
            _enforce_active_credential_fd_policy(
                target_fd,
                target_path,
                expected_mode=spec.access_policy.data_mode,
                label=target_path.name,
            )
        finally:
            _os.close(target_fd)


def _credential_payload_limit(path: Path) -> tuple[int, str]:
    name = Path(path).name
    if name == _CREDENTIAL_JOURNAL_NAME:
        return _MAX_CREDENTIAL_JOURNAL_BYTES, "intent"
    if name == ".env":
        return _MAX_CREDENTIAL_ENV_BYTES, "env"
    return _MAX_CREDENTIAL_CONFIG_BYTES, "config"


def _stage_credential_bytes(
    logical_path: Path,
    payload: bytes,
    *,
    transaction_id: str | None = None,
) -> tuple[Path, Path]:
    max_bytes, label = _credential_payload_limit(logical_path)
    if len(payload) > max_bytes:
        error_type: type[Exception] = (
            CredentialRecoveryError
            if label == "intent"
            else ValueError
        )
        raise error_type(f"credential {label} exceeds maximum size")
    real_target = _resolved_write_target(logical_path)
    if _active_resource_handle_for_target(real_target) is None:
        real_target.parent.mkdir(parents=True, exist_ok=True)
    token = transaction_id or uuid.uuid4().hex
    stage_path = (
        real_target.parent
        / f".taiji-credential-{real_target.name}-{token}.stage"
    )
    flags = _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL
    flags |= getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    stage_mode = _credential_artifact_mode()
    stage_fd = _open_active_target(stage_path, flags, stage_mode)
    try:
        _enforce_active_credential_fd_policy(
            stage_fd,
            stage_path,
            expected_mode=stage_mode,
            label="transaction stage",
        )
        view = memoryview(payload)
        while view:
            written = _os.write(stage_fd, view)
            if written <= 0:  # pragma: no cover - defensive kernel contract.
                raise OSError("credential stage write made no progress")
            view = view[written:]
        _os.fsync(stage_fd)
    except BaseException:
        _os.close(stage_fd)
        try:
            _unlink_active_target(stage_path, missing_ok=True)
        except OSError:
            pass
        raise
    else:
        _os.close(stage_fd)
    try:
        _fsync_directory(real_target.parent)
    except BaseException:
        try:
            _unlink_active_target(stage_path, missing_ok=True)
        except OSError:
            pass
        raise
    return stage_path, real_target


def _replace_credential_stage(
    stage_path: Path,
    *,
    logical_path: Path,
    real_target: Path,
    mode: int,
    expected_exists: bool | None = None,
    expected_sha256: str | None = None,
) -> None:
    if _resolved_write_target(logical_path) != real_target:
        raise CredentialRecoveryError(
            "credential target changed during transaction"
        )
    _assert_active_resource_dirs_unchanged()
    max_bytes, label = _credential_payload_limit(logical_path)
    stage_fd = _open_active_target(
        stage_path,
        _os.O_RDONLY
        | getattr(_os, "O_CLOEXEC", 0)
        | getattr(_os, "O_NOFOLLOW", 0),
    )
    target_fd: int | None = None
    try:
        stage_stat = _os.fstat(stage_fd)
        if not stat.S_ISREG(stage_stat.st_mode) or stage_stat.st_nlink != 1:
            raise CredentialRecoveryError(
                "credential stage must be a regular file"
            )
        stage_identity = (stage_stat.st_dev, stage_stat.st_ino)
        stage_payload = _read_fd_bounded(
            stage_fd,
            max_bytes=max_bytes,
            label="stage",
            error_type=CredentialRecoveryError,
        )
        staged_digest = _sha256_bytes(stage_payload)
        _enforce_active_credential_fd_policy(
            stage_fd,
            stage_path,
            expected_mode=mode,
            label="transaction stage",
        )
        _os.fsync(stage_fd)

        target_identity: tuple[int, int] | None = None
        target_flags = (
            _os.O_RDONLY
            | getattr(_os, "O_CLOEXEC", 0)
            | getattr(_os, "O_NOFOLLOW", 0)
        )
        try:
            target_fd = _open_active_target(real_target, target_flags)
        except FileNotFoundError:
            target_fd = None
        if target_fd is not None:
            target_stat = _os.fstat(target_fd)
            if (
                not stat.S_ISREG(target_stat.st_mode)
                or target_stat.st_nlink != 1
            ):
                raise CredentialRecoveryError(
                    "credential target changed before replace "
                    "(atomic CAS)"
                )
            target_identity = (target_stat.st_dev, target_stat.st_ino)
            before_payload = _read_fd_bounded(
                target_fd,
                max_bytes=max_bytes,
                label=label,
                error_type=CredentialRecoveryError,
            )
            if (
                expected_exists is False
                or expected_sha256 is None
                or _sha256_bytes(before_payload) != expected_sha256
            ):
                raise _CredentialCompareAndSwapError(
                    "credential target changed before replace "
                    "(atomic CAS)"
                )
            # If the process dies after the exchange, the before image is
            # itself a private recovery stage.
            _enforce_active_credential_fd_policy(
                target_fd,
                real_target,
                expected_mode=_credential_artifact_mode(),
                label="recovery stage",
            )
            _os.fsync(target_fd)
        elif expected_exists is not False:
            raise _CredentialCompareAndSwapError(
                "credential target changed before replace (atomic CAS)"
            )

        current_stage_stat = _stat_active_target(
            stage_path,
            follow_symlinks=False,
        )
        if (
            current_stage_stat.st_dev,
            current_stage_stat.st_ino,
        ) != stage_identity:
            raise _CredentialCompareAndSwapError(
                "credential stage changed before replace (atomic CAS)"
            )

        if target_identity is None:
            try:
                _atomic_rename_noreplace(stage_path, real_target)
            except FileExistsError as exc:
                raise _CredentialCompareAndSwapError(
                    "credential target changed before replace "
                    "(atomic CAS)"
                ) from exc
        else:
            previous_expectation = getattr(
                _CREDENTIAL_TRANSACTION_STATE,
                "exchange_expectation",
                None,
            )
            _CREDENTIAL_TRANSACTION_STATE.exchange_expectation = (
                Path(stage_path),
                Path(real_target),
                stage_identity,
                target_identity,
            )
            try:
                _atomic_exchange_entries(stage_path, real_target)
            finally:
                _CREDENTIAL_TRANSACTION_STATE.exchange_expectation = (
                    previous_expectation
                )

        try:
            post_target_stat = _stat_active_target(
                real_target,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            raise CredentialRecoveryError(
                "credential target changed after replace "
                "(changed after atomic replace)"
            ) from exc
        post_target_identity = (
            post_target_stat.st_dev,
            post_target_stat.st_ino,
        )
        expected_stage_identity = stage_identity
        try:
            post_stage_stat = _stat_active_target(
                stage_path,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            post_stage_identity = None
        else:
            post_stage_identity = (
                post_stage_stat.st_dev,
                post_stage_stat.st_ino,
            )

        expected_post_stage = target_identity
        if (
            post_target_identity != expected_stage_identity
            or post_stage_identity != expected_post_stage
        ):
            # If the staged image is still the target, the target was
            # retargeted immediately before the exchange. Swap back without
            # overwriting that newer entry, then let the durable abort path
            # unwind any earlier targets.
            if (
                target_identity is not None
                and post_target_identity == expected_stage_identity
                and post_stage_identity is not None
            ):
                _atomic_exchange_entries(stage_path, real_target)
                restored_target = _stat_active_target(
                    real_target,
                    follow_symlinks=False,
                )
                restored_stage = _stat_active_target(
                    stage_path,
                    follow_symlinks=False,
                )
                if (
                    (
                        restored_stage.st_dev,
                        restored_stage.st_ino,
                    )
                    == expected_stage_identity
                    and (
                        restored_target.st_dev,
                        restored_target.st_ino,
                    )
                    == post_stage_identity
                ):
                    raise _CredentialCompareAndSwapError(
                        "credential target changed before replace "
                        "(atomic CAS)"
                    )
            # An entry that appeared after the atomic operation is external
            # state. Never swap or overwrite it during automatic recovery.
            raise CredentialRecoveryError(
                "credential target or stage changed after replace "
                "(changed after atomic replace)"
            )

        verified_target_fd = _open_active_target(
            real_target,
            target_flags,
        )
        try:
            verified_stat = _os.fstat(verified_target_fd)
            if (
                not stat.S_ISREG(verified_stat.st_mode)
                or verified_stat.st_nlink != 1
                or (
                    verified_stat.st_dev,
                    verified_stat.st_ino,
                )
                != expected_stage_identity
            ):
                raise CredentialRecoveryError(
                    "credential target changed after replace "
                    "(changed after atomic replace)"
                )
            target_payload = _read_fd_bounded(
                verified_target_fd,
                max_bytes=max_bytes,
                label=label,
                error_type=CredentialRecoveryError,
            )
            if _sha256_bytes(target_payload) != staged_digest:
                raise CredentialRecoveryError(
                    "credential target changed after replace "
                    "(changed after atomic replace)"
                )
            _enforce_active_credential_fd_policy(
                verified_target_fd,
                real_target,
                expected_mode=mode,
                label=label,
            )
            _os.fsync(verified_target_fd)
        finally:
            _os.close(verified_target_fd)
    finally:
        if target_fd is not None:
            _os.close(target_fd)
        _os.close(stage_fd)
    _fsync_directory(real_target.parent)


def _atomic_write_credential_bytes(
    logical_path: Path,
    payload: bytes,
    *,
    mode: int,
    expected_exists: bool | None = None,
    expected_sha256: str | None = None,
    env_keys: list[str] | None = None,
) -> None:
    before_exists, before_payload = _read_optional_bytes(logical_path)
    if (
        expected_exists is not None
        and (
            before_exists != expected_exists
            or expected_sha256 is None
            or _sha256_bytes(before_payload) != expected_sha256
        )
    ):
        raise _CredentialCompareAndSwapError(
            "credential target changed before durable staging"
        )
    name = "env" if Path(logical_path).name == ".env" else "config"
    _commit_credential_targets(
        [
            {
                "name": name,
                "logical_path": Path(logical_path),
                "before_exists": before_exists,
                "before_payload": before_payload,
                "target_payload": payload,
                "mode": mode,
            }
        ],
        env_keys=list(env_keys or ()),
    )


def _credential_file_state(
    path: Path,
    *,
    max_bytes: int = _MAX_CREDENTIAL_CONFIG_BYTES,
    label: str = "target",
) -> tuple[bool, str]:
    exists, payload = _read_optional_bytes(
        path,
        max_bytes=max_bytes,
        label=label,
        error_type=CredentialRecoveryError,
    )
    if not exists:
        return False, _sha256_bytes(b"")
    return True, _sha256_bytes(payload)


def _read_stage_bytes(
    stage_path: Path,
    *,
    allowed_modes: set[int] | None = None,
    max_bytes: int | None = None,
) -> bytes:
    if max_bytes is None:
        max_bytes = _MAX_CREDENTIAL_STAGE_BYTES
    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    try:
        stage_fd = _open_active_target(stage_path, flags)
    except OSError as exc:
        raise CredentialRecoveryError(
            "credential transaction stage is missing or unsafe"
        ) from exc
    try:
        stage_stat = _os.fstat(stage_fd)
        access_policy = _active_credential_access_policy()
        effective_allowed_modes = (
            {access_policy.artifact_mode, 0o600}
            if access_policy.group_shared
            else (allowed_modes or {access_policy.artifact_mode})
        )
        if (
            not stat.S_ISREG(stage_stat.st_mode)
            or stage_stat.st_nlink != 1
            or (
                _supports_exact_posix_modes()
                and stat.S_IMODE(stage_stat.st_mode)
                not in effective_allowed_modes
            )
        ):
            raise CredentialRecoveryError(
                "credential transaction stage is not a private regular file"
            )
        _enforce_active_credential_fd_policy(
            stage_fd,
            stage_path,
            expected_mode=access_policy.artifact_mode,
            label="transaction stage",
        )
        if stage_stat.st_size > max_bytes:
            raise CredentialRecoveryError(
                "credential stage exceeds maximum size"
            )
        return _read_fd_bounded(
            stage_fd,
            max_bytes=max_bytes,
            label="stage",
            error_type=CredentialRecoveryError,
        )
    finally:
        _os.close(stage_fd)


def _credential_journal_path(lock_root: Path) -> Path:
    return lock_root / _CREDENTIAL_JOURNAL_NAME


def _credential_abort_journal_path(lock_root: Path) -> Path:
    return lock_root / _CREDENTIAL_ABORT_JOURNAL_NAME


def _write_credential_journal(
    lock_root: Path,
    manifest: dict[str, Any],
) -> None:
    journal_path = _credential_journal_path(lock_root)
    abort_path = _credential_abort_journal_path(lock_root)
    _assert_active_resource_dirs_unchanged()
    if _os.path.lexists(journal_path) or _os.path.lexists(abort_path):
        raise CredentialRecoveryError(
            "pending credential transaction must be recovered first"
        )
    payload = _credential_journal_payload(manifest)
    flags = _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL
    flags |= getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    journal_mode = _credential_artifact_mode()
    try:
        journal_fd = _open_active_target(
            journal_path,
            flags,
            journal_mode,
        )
    except OSError as exc:
        raise CredentialRecoveryError(
            "pending credential transaction must be recovered first"
        ) from exc
    try:
        journal_stat = _os.fstat(journal_fd)
        if (
            not stat.S_ISREG(journal_stat.st_mode)
            or journal_stat.st_nlink != 1
        ):
            raise CredentialRecoveryError(
                "credential transaction intent is unsafe"
            )
        _enforce_active_credential_fd_policy(
            journal_fd,
            journal_path,
            expected_mode=journal_mode,
            label="transaction intent",
        )
        view = memoryview(payload)
        while view:
            written = _os.write(journal_fd, view)
            if written <= 0:  # pragma: no cover - kernel contract.
                raise OSError(
                    "credential intent write made no progress"
                )
            view = view[written:]
        _os.fsync(journal_fd)
    except BaseException:
        _os.close(journal_fd)
        try:
            _unlink_active_target(journal_path, missing_ok=True)
        except OSError:
            pass
        raise
    else:
        _os.close(journal_fd)
    _fsync_directory(lock_root)


def _credential_journal_payload(manifest: Mapping[str, Any]) -> bytes:
    payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > _MAX_CREDENTIAL_JOURNAL_BYTES:
        raise CredentialRecoveryError(
            "credential intent exceeds maximum size"
        )
    return payload


def _read_credential_journal_path(
    journal_path: Path,
) -> tuple[
    dict[str, Any],
    bool,
    tuple[int, int],
    str,
] | None:
    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    try:
        journal_fd = _open_active_target(journal_path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CredentialRecoveryError(
            "credential transaction intent is unsafe"
        ) from exc
    try:
        journal_stat = _os.fstat(journal_fd)
        access_policy = _active_credential_access_policy()
        journal_mode = access_policy.artifact_mode
        observed_mode = stat.S_IMODE(journal_stat.st_mode)
        journal_identity = (journal_stat.st_dev, journal_stat.st_ino)
        legacy_private_mode = (
            access_policy.group_shared and observed_mode == 0o600
        )
        allowed_modes = {journal_mode}
        if access_policy.group_shared:
            allowed_modes.add(0o600)
        if (
            not stat.S_ISREG(journal_stat.st_mode)
            or journal_stat.st_nlink != 1
            or (
                _supports_exact_posix_modes()
                and observed_mode not in allowed_modes
            )
        ):
            raise CredentialRecoveryError(
                "credential transaction intent is not a private regular file"
            )
        _enforce_active_credential_fd_policy(
            journal_fd,
            journal_path,
            expected_mode=(
                observed_mode if legacy_private_mode else journal_mode
            ),
            label="transaction intent",
        )
        if journal_stat.st_size > _MAX_CREDENTIAL_JOURNAL_BYTES:
            raise CredentialRecoveryError(
                "credential intent exceeds maximum size"
            )
        payload = _read_fd_bounded(
            journal_fd,
            max_bytes=_MAX_CREDENTIAL_JOURNAL_BYTES,
            label="intent",
            error_type=CredentialRecoveryError,
        )
    finally:
        _os.close(journal_fd)
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialRecoveryError(
            "credential transaction intent is invalid"
        ) from exc
    if not isinstance(manifest, dict):
        raise CredentialRecoveryError(
            "credential transaction intent must be a mapping"
        )
    return (
        manifest,
        legacy_private_mode,
        journal_identity,
        _sha256_bytes(payload),
    )


def _read_credential_journal(lock_root: Path) -> dict[str, Any] | None:
    pending = _read_credential_journal_path(
        _credential_journal_path(lock_root)
    )
    return None if pending is None else pending[0]


def _read_pending_transaction_journal(
    lock_root: Path,
) -> tuple[
    str,
    Path,
    dict[str, Any],
    bool,
    tuple[int, int],
    str,
] | None:
    intent_path = _credential_journal_path(lock_root)
    abort_path = _credential_abort_journal_path(lock_root)
    intent = _read_credential_journal_path(intent_path)
    abort = _read_credential_journal_path(abort_path)
    if intent is not None and abort is not None:
        raise CredentialRecoveryError(
            "credential transaction has conflicting durable decisions"
        )
    if abort is not None:
        return "abort", abort_path, *abort
    if intent is not None:
        return "commit", intent_path, *intent
    return None


def _journal_migration_stage_path(
    journal_path: Path,
    transaction_id: str,
) -> Path:
    return (
        journal_path.parent
        / (
            f".taiji-credential-{journal_path.name}-"
            f"{transaction_id}.stage"
        )
    )


def _cleanup_journal_migration_stage(
    journal_path: Path,
    transaction_id: str,
) -> None:
    stage_path = _journal_migration_stage_path(
        journal_path,
        transaction_id,
    )
    try:
        stage_stat = _stat_active_target(
            stage_path,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(stage_stat.st_mode)
        or stage_stat.st_nlink != 1
        or stat.S_IMODE(stage_stat.st_mode) not in {0o600, 0o640}
    ):
        raise CredentialRecoveryError(
            "credential journal migration stage is unsafe"
        )
    _unlink_active_target(stage_path)
    _fsync_directory(stage_path.parent)


def _migrate_legacy_credential_journal(
    journal_path: Path,
    manifest: dict[str, Any],
    *,
    expected_identity: tuple[int, int],
    expected_sha256: str,
) -> None:
    transaction_id = str(manifest["transaction_id"])
    payload = _credential_journal_payload(manifest)
    stage_path = _journal_migration_stage_path(
        journal_path,
        transaction_id,
    )
    try:
        prepared_stage, real_target = _stage_credential_bytes(
            journal_path,
            payload,
            transaction_id=transaction_id,
        )
    except FileExistsError:
        prepared_stage = stage_path
        staged_payload = _read_stage_bytes(
            prepared_stage,
            max_bytes=_MAX_CREDENTIAL_JOURNAL_BYTES,
        )
        if staged_payload != payload:
            raise CredentialRecoveryError(
                "credential journal migration stage changed"
            )
        real_target = journal_path
    if prepared_stage != stage_path or real_target != journal_path:
        raise CredentialRecoveryError(
            "credential journal migration target changed"
        )

    flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
    flags |= getattr(_os, "O_NOFOLLOW", 0)
    journal_fd = _open_active_target(journal_path, flags)
    try:
        journal_stat = _os.fstat(journal_fd)
        current_payload = _read_fd_bounded(
            journal_fd,
            max_bytes=_MAX_CREDENTIAL_JOURNAL_BYTES,
            label="intent",
            error_type=CredentialRecoveryError,
        )
        if (
            not stat.S_ISREG(journal_stat.st_mode)
            or journal_stat.st_nlink != 1
            or stat.S_IMODE(journal_stat.st_mode) != 0o600
            or (journal_stat.st_dev, journal_stat.st_ino)
            != expected_identity
            or _sha256_bytes(current_payload) != expected_sha256
        ):
            raise CredentialRecoveryError(
                "credential journal changed during legacy migration"
            )
    finally:
        _os.close(journal_fd)

    stage_stat = _stat_active_target(
        stage_path,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(stage_stat.st_mode)
        or stage_stat.st_nlink != 1
        or stat.S_IMODE(stage_stat.st_mode) != 0o640
    ):
        raise CredentialRecoveryError(
            "credential journal migration stage is unsafe"
        )
    previous_expectation = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "exchange_expectation",
        None,
    )
    _CREDENTIAL_TRANSACTION_STATE.exchange_expectation = (
        stage_path,
        journal_path,
        (stage_stat.st_dev, stage_stat.st_ino),
        expected_identity,
    )
    try:
        _atomic_exchange_entries(stage_path, journal_path)
    finally:
        _CREDENTIAL_TRANSACTION_STATE.exchange_expectation = (
            previous_expectation
        )
    _fsync_directory(journal_path.parent)
    _cleanup_journal_migration_stage(
        journal_path,
        transaction_id,
    )


def _publish_abort_decision(lock_root: Path) -> Path:
    intent_path = _credential_journal_path(lock_root)
    abort_path = _credential_abort_journal_path(lock_root)
    if _active_entry_exists(abort_path):
        if _active_entry_exists(intent_path):
            raise CredentialRecoveryError(
                "credential transaction has conflicting durable decisions"
            )
        return abort_path
    try:
        _atomic_rename_noreplace(intent_path, abort_path)
    except FileExistsError as exc:
        raise CredentialRecoveryError(
            "credential transaction abort decision collided"
        ) from exc
    _fsync_directory(lock_root)
    return abort_path


def _validated_credential_manifest(
    lock_root: Path,
    manifest: dict[str, Any],
    *,
    legacy_private_mode: bool = False,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    expected_manifest_keys = {
        "schema",
        "transaction_id",
        "env_keys",
        "targets",
    }
    if set(manifest) != expected_manifest_keys:
        raise CredentialRecoveryError(
            "credential transaction intent has unknown fields"
        )
    access_policy = _active_credential_access_policy()
    supported_schemas = {_CREDENTIAL_JOURNAL_SCHEMA}
    if access_policy.group_shared and not legacy_private_mode:
        supported_schemas.add(
            _CREDENTIAL_GROUP_SHARED_JOURNAL_SCHEMA
        )
    if manifest["schema"] not in supported_schemas:
        raise CredentialRecoveryError(
            "credential transaction intent schema is unsupported"
        )
    transaction_id = manifest["transaction_id"]
    if not isinstance(transaction_id, str) or not re.fullmatch(
        r"[0-9a-f]{32}",
        transaction_id,
    ):
        raise CredentialRecoveryError(
            "credential transaction id is invalid"
        )
    env_keys = manifest["env_keys"]
    if (
        not isinstance(env_keys, list)
        or len(env_keys) != len(set(env_keys))
        or any(
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            for key in env_keys
        )
    ):
        raise CredentialRecoveryError(
            "credential transaction env key list is invalid"
        )
    targets = manifest["targets"]
    if not isinstance(targets, list) or len(targets) > 2:
        raise CredentialRecoveryError(
            "credential transaction target list is invalid"
        )
    expected_target_keys = {
        "name",
        "logical_path",
        "real_path",
        "stage_path",
        "before_exists",
        "before_sha256",
        "target_sha256",
        "mode",
    }
    validated: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw_target in targets:
        if (
            not isinstance(raw_target, dict)
            or set(raw_target) != expected_target_keys
        ):
            raise CredentialRecoveryError(
                "credential transaction target has unknown fields"
            )
        name = raw_target["name"]
        if name not in {"config", "env"} or name in names:
            raise CredentialRecoveryError(
                "credential transaction target name is invalid"
            )
        names.add(name)
        path_values = (
            raw_target["logical_path"],
            raw_target["real_path"],
            raw_target["stage_path"],
        )
        if any(
            not isinstance(value, str) or not Path(value).is_absolute()
            for value in path_values
        ):
            raise CredentialRecoveryError(
                "credential transaction target path is invalid"
            )
        logical_path = Path(raw_target["logical_path"])
        real_path = Path(raw_target["real_path"])
        stage_path = Path(raw_target["stage_path"])
        if _resolved_write_target(logical_path) != real_path:
            raise CredentialRecoveryError(
                "credential transaction target changed"
            )
        expected_stage_name = (
            f".taiji-credential-{real_path.name}-{transaction_id}.stage"
        )
        if (
            stage_path.parent != real_path.parent
            or stage_path.name != expected_stage_name
        ):
            raise CredentialRecoveryError(
                "credential transaction stage path is invalid"
            )
        before_exists = raw_target["before_exists"]
        before_sha256 = raw_target["before_sha256"]
        target_sha256 = raw_target["target_sha256"]
        mode = raw_target["mode"]
        if type(before_exists) is not bool:
            raise CredentialRecoveryError(
                "credential transaction existence state is invalid"
            )
        if any(
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in (before_sha256, target_sha256)
        ):
            raise CredentialRecoveryError(
                "credential transaction digest is invalid"
            )
        invalid_mode = (
            type(mode) is not int
            or (
                access_policy.group_shared
                and legacy_private_mode
                and mode not in _LEGACY_PRIVATE_TARGET_MODES
            )
            or (
                access_policy.group_shared
                and not legacy_private_mode
                and mode != access_policy.data_mode
            )
            or (
                not access_policy.group_shared
                and not 0 <= mode <= 0o777
            )
        )
        if invalid_mode:
            raise CredentialRecoveryError(
                "credential transaction target mode is invalid"
            )
        validated.append(
            {
                **raw_target,
                "mode": (
                    access_policy.data_mode
                    if access_policy.group_shared and legacy_private_mode
                    else mode
                ),
                "logical_path": logical_path,
                "real_path": real_path,
                "stage_path": stage_path,
            }
        )
    active_spec = getattr(_CREDENTIAL_TRANSACTION_STATE, "spec", None)
    if not isinstance(active_spec, _CredentialTransactionSpec):
        raise CredentialRecoveryError(
            "credential transaction targets do not match the locked resources"
        )
    if active_spec.config_target.parent != lock_root:
        raise CredentialRecoveryError(
            "credential config target escaped its physical lock root"
        )
    for target in validated:
        if target["name"] == "config":
            matches_locked_target = (
                target["logical_path"] == active_spec.config_target
                and target["real_path"] == active_spec.config_target
            )
        else:
            matches_locked_target = (
                target["logical_path"] == active_spec.env_path
                and target["real_path"] == active_spec.env_target
            )
        if not matches_locked_target:
            raise CredentialRecoveryError(
                "credential transaction targets do not match "
                "the locked resources"
            )
    return transaction_id, env_keys, validated


def _sync_recovered_process_env(
    env_path: Path,
    env_keys: list[str],
) -> None:
    _, payload = _read_optional_bytes(
        env_path,
        max_bytes=_MAX_CREDENTIAL_ENV_BYTES,
        label="env",
    )
    values = _parse_env_bytes(payload)
    _project_process_env_batch(
        {key: values.get(key) for key in env_keys}
    )


def _cleanup_stale_orphan_stages(
    lock_root: Path,
    *,
    logical_config_path: Path | None,
) -> None:
    """Delete only old private stages inside the active credential boundary."""
    if _credential_platform_name() not in {"darwin", "linux"}:
        return
    del logical_config_path
    _assert_active_resource_dirs_unchanged()
    active_handles = getattr(
        _CREDENTIAL_TRANSACTION_STATE,
        "resource_handles",
        None,
    )
    if isinstance(active_handles, dict):
        lock_handle = active_handles.get(lock_root)
        if lock_handle is None:
            raise CredentialRecoveryError(
                "credential journal root is not in the locked resource set"
            )
        # A distinct env root can be shared by many config roots. Its old
        # stages may belong to another config root's still-pending journal,
        # which this recovery cannot inspect. Only the journal-owning config
        # root has enough evidence for safe orphan deletion.
        roots_and_handles = [(lock_root, lock_handle)]
    else:
        resource_handle = _open_credential_resource_handle(
            lock_root,
            _active_credential_access_policy(),
        )
        roots_and_handles = [(lock_root, resource_handle)]
    now = time.time()
    owner_id = (
        _os.geteuid()
        if callable(getattr(_os, "geteuid", None))
        else None
    )
    access_policy = _active_credential_access_policy()
    expected_stage_mode = access_policy.artifact_mode
    try:
        for root, resource_handle in roots_and_handles:
            removed = False
            try:
                candidate_names = _os.listdir(
                    resource_handle.directory_fd
                )
            except OSError:
                continue
            for candidate_name in candidate_names:
                if (
                    _CREDENTIAL_ORPHAN_STAGE_RE.fullmatch(candidate_name)
                    is None
                ):
                    continue
                try:
                    initial_stat = _os.stat(
                        candidate_name,
                        dir_fd=resource_handle.directory_fd,
                        follow_symlinks=False,
                    )
                except OSError:
                    continue
                if (
                    not stat.S_ISREG(initial_stat.st_mode)
                    or initial_stat.st_nlink != 1
                    or stat.S_IMODE(initial_stat.st_mode)
                    != expected_stage_mode
                    or (
                        access_policy.group_shared
                        and initial_stat.st_gid
                        != resource_handle.group_id
                    )
                    or (
                        not access_policy.group_shared
                        and owner_id is not None
                        and initial_stat.st_uid != owner_id
                    )
                    or now - initial_stat.st_mtime
                    < _CREDENTIAL_ORPHAN_STAGE_GRACE_SECONDS
                ):
                    continue
                flags = _os.O_RDONLY | getattr(_os, "O_CLOEXEC", 0)
                flags |= getattr(_os, "O_NOFOLLOW", 0)
                try:
                    candidate_fd = _os.open(
                        candidate_name,
                        flags,
                        dir_fd=resource_handle.directory_fd,
                    )
                except OSError:
                    continue
                try:
                    opened_stat = _os.fstat(candidate_fd)
                    if (
                        not stat.S_ISREG(opened_stat.st_mode)
                        or opened_stat.st_nlink != 1
                        or stat.S_IMODE(opened_stat.st_mode)
                        != expected_stage_mode
                        or (
                            access_policy.group_shared
                            and opened_stat.st_gid
                            != resource_handle.group_id
                        )
                        or (
                            not access_policy.group_shared
                            and owner_id is not None
                            and opened_stat.st_uid != owner_id
                        )
                        or (
                            opened_stat.st_dev,
                            opened_stat.st_ino,
                        )
                        != (
                            initial_stat.st_dev,
                            initial_stat.st_ino,
                        )
                    ):
                        continue
                    quarantine_name = (
                        ".taiji-credential-orphan-"
                        f"{uuid.uuid4().hex}.stage"
                    )
                    try:
                        _os.rename(
                            candidate_name,
                            quarantine_name,
                            src_dir_fd=resource_handle.directory_fd,
                            dst_dir_fd=resource_handle.directory_fd,
                        )
                    except OSError:
                        continue
                    quarantined_stat = _os.stat(
                        quarantine_name,
                        dir_fd=resource_handle.directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        quarantined_stat.st_dev,
                        quarantined_stat.st_ino,
                    ) != (
                        opened_stat.st_dev,
                        opened_stat.st_ino,
                    ):
                        raise CredentialRecoveryError(
                            "credential orphan stage changed before cleanup"
                        )
                    _os.unlink(
                        quarantine_name,
                        dir_fd=resource_handle.directory_fd,
                    )
                    removed = True
                finally:
                    _os.close(candidate_fd)
            if removed:
                _os.fsync(resource_handle.directory_fd)
    finally:
        if not isinstance(active_handles, dict):
            _os.close(roots_and_handles[0][1].directory_fd)


def _active_entry_exists(path: Path) -> bool:
    try:
        _stat_active_target(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _recover_pending_transaction_unlocked(
    lock_root: Path,
    *,
    logical_config_path: Path | None = None,
) -> str:
    _assert_active_resource_dirs_unchanged()
    pending = _read_pending_transaction_journal(lock_root)
    if pending is None:
        _cleanup_stale_orphan_stages(
            lock_root,
            logical_config_path=logical_config_path,
        )
        return "not_needed"
    _require_secure_pair_transaction_platform()
    (
        decision,
        journal_path,
        manifest,
        legacy_private_mode,
        journal_identity,
        journal_sha256,
    ) = pending
    transaction_id, env_keys, targets = _validated_credential_manifest(
        lock_root,
        manifest,
        legacy_private_mode=legacy_private_mode,
    )
    if legacy_private_mode:
        migrated_manifest = copy.deepcopy(manifest)
        migrated_manifest["schema"] = (
            _CREDENTIAL_GROUP_SHARED_JOURNAL_SCHEMA
        )
        for target in migrated_manifest["targets"]:
            target["mode"] = _active_credential_access_policy().data_mode
        _migrate_legacy_credential_journal(
            journal_path,
            migrated_manifest,
            expected_identity=journal_identity,
            expected_sha256=journal_sha256,
        )
    elif (
        manifest["schema"]
        == _CREDENTIAL_GROUP_SHARED_JOURNAL_SCHEMA
    ):
        _cleanup_journal_migration_stage(
            journal_path,
            transaction_id,
        )
    if decision == "abort":
        _recover_abort_transaction_unlocked(
            lock_root,
            journal_path,
            targets,
        )
        return "recovered"

    classifications: list[tuple[dict[str, Any], str]] = []
    for target in targets:
        classifications.append(
            (target, _classify_commit_target_state(target))
        )

    try:
        for target, classification in classifications:
            if classification == "before":
                _replace_credential_stage(
                    target["stage_path"],
                    logical_path=target["logical_path"],
                    real_target=target["real_path"],
                    mode=target["mode"],
                    expected_exists=target["before_exists"],
                    expected_sha256=target["before_sha256"],
                )
    except _CredentialCompareAndSwapError as cas_error:
        abort_path = _publish_abort_decision(lock_root)
        try:
            _recover_abort_transaction_unlocked(
                lock_root,
                abort_path,
                targets,
            )
        except BaseException as rollback_error:
            raise CredentialRecoveryError(
                "credential transaction entered durable abort state; "
                "automatic rollback is incomplete"
            ) from rollback_error
        raise cas_error

    synced_directories: set[Path] = set()
    for target in targets:
        _assert_active_resource_dirs_unchanged()
        target_limit = _manifest_target_limit(target)
        exists, digest = _credential_file_state(
            target["real_path"],
            max_bytes=target_limit,
            label=target["name"],
        )
        if not exists or digest != target["target_sha256"]:
            raise CredentialRecoveryError(
                f"credential {target['name']} target verification failed"
            )
        target_fd = _open_active_target(
            target["real_path"],
            _os.O_RDONLY
            | getattr(_os, "O_CLOEXEC", 0)
            | getattr(_os, "O_NOFOLLOW", 0),
        )
        try:
            target_stat = _os.fstat(target_fd)
            if (
                not stat.S_ISREG(target_stat.st_mode)
                or target_stat.st_nlink != 1
            ):
                raise CredentialRecoveryError(
                    f"credential {target['name']} target is unsafe"
                )
            _enforce_active_credential_fd_policy(
                target_fd,
                Path(target["real_path"]),
                expected_mode=int(target["mode"]),
                label=str(target["name"]),
            )
            _os.fsync(target_fd)
        except (NotImplementedError, OSError) as exc:
            if _supports_exact_posix_modes():
                raise CredentialRecoveryError(
                    f"credential {target['name']} mode restoration failed"
                ) from exc
        finally:
            _os.close(target_fd)
        synced_directories.add(target["real_path"].parent)

    for directory in sorted(synced_directories, key=str):
        _fsync_directory(directory)

    active_spec = getattr(_CREDENTIAL_TRANSACTION_STATE, "spec", None)
    if not isinstance(active_spec, _CredentialTransactionSpec):
        raise CredentialRecoveryError(
            "credential transaction has no pinned environment target"
        )
    # Process projection is part of the durable commit.  A projection failure
    # intentionally leaves the intent in place for a new process to replay.
    _sync_recovered_process_env(
        active_spec.env_target,
        env_keys,
    )

    # Target bytes and runtime projection are now committed. Cleanup is
    # post-commit finalization: failures retain a replay marker when possible
    # and must not be reported as an ordinary retryable mutation failure.
    _finalize_committed_transaction(
        lock_root,
        journal_path,
        targets,
    )
    return "recovered"


def _manifest_target_limit(target: Mapping[str, Any]) -> int:
    return (
        _MAX_CREDENTIAL_CONFIG_BYTES
        if target["name"] == "config"
        else _MAX_CREDENTIAL_ENV_BYTES
    )


def _stage_digest_if_present(
    target: Mapping[str, Any],
) -> str | None:
    stage_path = Path(target["stage_path"])
    if not _active_entry_exists(stage_path):
        return None
    payload = _read_stage_bytes(
        stage_path,
        allowed_modes={
            _credential_artifact_mode(),
            int(target["mode"]),
        },
        max_bytes=min(
            _MAX_CREDENTIAL_STAGE_BYTES,
            _manifest_target_limit(target),
        ),
    )
    return _sha256_bytes(payload)


def _classify_commit_target_state(
    target: Mapping[str, Any],
) -> str:
    exists, digest = _credential_file_state(
        Path(target["real_path"]),
        max_bytes=_manifest_target_limit(target),
        label=str(target["name"]),
    )
    stage_digest = _stage_digest_if_present(target)
    if bool(target["before_exists"]):
        if (
            exists
            and digest == target["before_sha256"]
            and stage_digest == target["target_sha256"]
        ):
            return "before"
        if (
            exists
            and digest == target["target_sha256"]
            and stage_digest
            in {None, target["before_sha256"]}
        ):
            return "target"
    else:
        if (
            not exists
            and stage_digest == target["target_sha256"]
        ):
            return "before"
        if (
            exists
            and digest == target["target_sha256"]
            and stage_digest is None
        ):
            return "target"
    raise CredentialRecoveryError(
        f"credential {target['name']} target has an unknown state"
    )


def _classify_abort_target_state(
    target: Mapping[str, Any],
) -> str:
    exists, digest = _credential_file_state(
        Path(target["real_path"]),
        max_bytes=_manifest_target_limit(target),
        label=str(target["name"]),
    )
    stage_digest = _stage_digest_if_present(target)
    if (
        stage_digest == target["target_sha256"]
        and not (
            exists
            and digest == target["target_sha256"]
        )
    ):
        # The CAS never applied this stage. The current target may be a newer
        # external value and is deliberately left untouched.
        return "rolled_back"
    if bool(target["before_exists"]):
        if exists and digest == target["before_sha256"]:
            return "rolled_back"
        if (
            exists
            and digest == target["target_sha256"]
            and stage_digest == target["before_sha256"]
        ):
            return "applied"
    else:
        if not exists:
            return "rolled_back"
        if (
            exists
            and digest == target["target_sha256"]
            and stage_digest is None
        ):
            return "applied"
    raise CredentialRecoveryError(
        f"credential {target['name']} target has an unknown abort state"
    )


def _restore_target_mode_and_sync(
    target: Mapping[str, Any],
) -> None:
    target_fd = _open_active_target(
        Path(target["real_path"]),
        _os.O_RDONLY
        | getattr(_os, "O_CLOEXEC", 0)
        | getattr(_os, "O_NOFOLLOW", 0),
    )
    try:
        target_stat = _os.fstat(target_fd)
        if (
            not stat.S_ISREG(target_stat.st_mode)
            or target_stat.st_nlink != 1
        ):
            raise CredentialRecoveryError(
                f"credential {target['name']} rollback target is unsafe"
            )
        _enforce_active_credential_fd_policy(
            target_fd,
            Path(target["real_path"]),
            expected_mode=int(target["mode"]),
            label=str(target["name"]),
        )
        _os.fsync(target_fd)
    finally:
        _os.close(target_fd)


def _rollback_applied_target(target: Mapping[str, Any]) -> None:
    real_path = Path(target["real_path"])
    stage_path = Path(target["stage_path"])
    if bool(target["before_exists"]):
        _atomic_exchange_entries(stage_path, real_path)
        _restore_target_mode_and_sync(target)
    else:
        _atomic_rename_noreplace(real_path, stage_path)
    _fsync_directory(real_path.parent)
    if _classify_abort_target_state(target) != "rolled_back":
        raise CredentialRecoveryError(
            f"credential {target['name']} rollback verification failed"
        )


def _recover_abort_transaction_unlocked(
    lock_root: Path,
    abort_path: Path,
    targets: list[dict[str, Any]],
) -> None:
    for target in reversed(targets):
        classification = _classify_abort_target_state(target)
        if classification == "applied":
            _rollback_applied_target(target)

    cleanup_ok = True
    for target in targets:
        stage_path = Path(target["stage_path"])
        try:
            _unlink_active_target(stage_path, missing_ok=True)
            _fsync_directory(stage_path.parent)
        except OSError:
            cleanup_ok = False
            break
    if not cleanup_ok:
        raise CredentialRecoveryError(
            "credential abort stage cleanup is incomplete"
        )
    try:
        _unlink_active_target(abort_path)
        _fsync_directory(lock_root)
    except OSError as exc:
        raise CredentialRecoveryError(
            "credential abort decision cleanup is incomplete"
        ) from exc


def _finalize_committed_transaction(
    lock_root: Path,
    journal_path: Path,
    targets: list[dict[str, Any]],
) -> None:
    for target in targets:
        stage_path = Path(target["stage_path"])
        try:
            _unlink_active_target(stage_path, missing_ok=True)
            _fsync_directory(stage_path.parent)
        except OSError:
            return
    try:
        _unlink_active_target(journal_path)
    except OSError:
        return
    try:
        _fsync_directory(lock_root)
    except OSError:
        # The deletion may reappear after a crash, but the retained intent is
        # an idempotent committed-state replay. The current mutation succeeded.
        return


def recover_credential_transaction(
    config_path: Path | None = None,
) -> str:
    """Recover the durable pair intent, or report that none exists."""
    with credential_transaction(config_path) as spec:
        return str(
            getattr(
                _CREDENTIAL_TRANSACTION_STATE,
                "last_recovery",
                "not_needed",
            )
        )


def _prepare_pair_target(
    *,
    name: str,
    logical_path: Path,
    before_exists: bool,
    before_payload: bytes,
    target_payload: bytes,
    mode: int,
    transaction_id: str,
) -> dict[str, Any]:
    stage_path, real_path = _stage_credential_bytes(
        logical_path,
        target_payload,
        transaction_id=transaction_id,
    )
    return {
        "name": name,
        "logical_path": str(logical_path),
        "real_path": str(real_path),
        "stage_path": str(stage_path),
        "before_exists": before_exists,
        "before_sha256": _sha256_bytes(before_payload),
        "target_sha256": _sha256_bytes(target_payload),
        "mode": mode,
    }


def _commit_config_env_pair(
    *,
    config_path: Path,
    config_exists: bool,
    config_before: bytes,
    config_target: bytes,
    env_exists: bool,
    env_before: bytes,
    env_target: bytes,
    env_keys: list[str],
) -> None:
    active_spec = getattr(_CREDENTIAL_TRANSACTION_STATE, "spec", None)
    if not isinstance(active_spec, _CredentialTransactionSpec):
        raise RuntimeError(
            "credential pair commit requires an active transaction"
        )
    if Path(config_path) != active_spec.logical_config_path:
        raise CredentialRecoveryError(
            "credential pair commit does not match the pinned config"
        )
    _commit_credential_targets(
        [
            {
                "name": "config",
                "logical_path": active_spec.logical_config_path,
                "before_exists": config_exists,
                "before_payload": config_before,
                "target_payload": config_target,
                "mode": _existing_target_mode(
                    active_spec.config_target
                ),
            },
            {
                "name": "env",
                "logical_path": active_spec.env_path,
                "before_exists": env_exists,
                "before_payload": env_before,
                "target_payload": env_target,
                "mode": _credential_data_mode(),
            },
        ],
        env_keys=env_keys,
    )


def _commit_credential_targets(
    target_specs: list[dict[str, Any]],
    *,
    env_keys: list[str],
) -> None:
    _require_secure_pair_transaction_platform()
    active_spec = getattr(_CREDENTIAL_TRANSACTION_STATE, "spec", None)
    if not isinstance(active_spec, _CredentialTransactionSpec):
        raise RuntimeError(
            "credential commit requires an active transaction"
        )
    if not target_specs and not env_keys:
        return
    transaction_id = uuid.uuid4().hex
    lock_root = active_spec.config_target.parent
    prepared: list[dict[str, Any]] = []
    journal_written = False
    try:
        for target_spec in target_specs:
            target_name = str(target_spec["name"])
            pinned_logical_path = (
                active_spec.config_target
                if target_name == "config"
                else active_spec.env_path
            )
            prepared.append(
                _prepare_pair_target(
                    name=target_name,
                    logical_path=pinned_logical_path,
                    before_exists=bool(target_spec["before_exists"]),
                    before_payload=bytes(target_spec["before_payload"]),
                    target_payload=bytes(target_spec["target_payload"]),
                    mode=int(target_spec["mode"]),
                    transaction_id=transaction_id,
                )
            )
        _write_credential_journal(
            lock_root,
            {
                "schema": (
                    _CREDENTIAL_GROUP_SHARED_JOURNAL_SCHEMA
                    if _active_credential_access_policy().group_shared
                    else _CREDENTIAL_JOURNAL_SCHEMA
                ),
                "transaction_id": transaction_id,
                "env_keys": env_keys,
                "targets": prepared,
            },
        )
        journal_written = True
    except BaseException:
        if not journal_written:
            for target in prepared:
                try:
                    _unlink_active_target(
                        Path(target["stage_path"]),
                        missing_ok=True,
                    )
                except OSError:
                    pass
        raise
    _recover_pending_transaction_unlocked(
        lock_root,
        logical_config_path=active_spec.logical_config_path,
    )


def _validated_env_update(
    key: object,
    value: object,
) -> tuple[str, str | None]:
    if not isinstance(key, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*",
        key,
    ):
        raise ValueError("credential env key is invalid")
    if value is not None and not isinstance(value, str):
        raise ValueError("credential env value must be a string or None")
    if isinstance(value, str) and any(
        forbidden in value for forbidden in ("\n", "\r", "\x00")
    ):
        raise ValueError("credential env value cannot contain NUL or newlines")
    return key, value


def _env_assignment_value(raw_value: str) -> str:
    from dotenv import dotenv_values

    parsed = dotenv_values(
        stream=StringIO(f"TAIJI_VALUE={raw_value}\n"),
        interpolate=False,
    )
    if "TAIJI_VALUE" not in parsed or parsed["TAIJI_VALUE"] is None:
        raise ValueError("credential env contains an invalid value")
    value = str(parsed["TAIJI_VALUE"])
    if "\x00" in value:
        raise ValueError("credential env cannot contain NUL bytes")
    return value


def _encode_env_value(value: str) -> str:
    if not re.search(r"""[\s#'"\\$]""", value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _mutated_env_bytes(
    original: bytes,
    updates: Mapping[str, str | None],
    expected_values: Mapping[str, str | None] | None,
) -> tuple[bytes, dict[str, bool]]:
    if b"\x00" in original:
        raise ValueError("credential env cannot contain NUL bytes")
    try:
        text = original.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("credential env must be UTF-8") from exc
    normalized_updates = dict(
        _validated_env_update(key, value)
        for key, value in updates.items()
    )
    normalized_expected = (
        {
            _validated_env_update(key, value)[0]: value
            for key, value in expected_values.items()
        }
        if expected_values is not None
        else {}
    )
    unknown_expected = set(normalized_expected) - set(normalized_updates)
    if unknown_expected:
        raise ValueError("expected_values keys must also be updated")

    lines = text.splitlines(keepends=True)
    occurrences: dict[str, list[tuple[int, str]]] = {}
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_ASSIGNMENT_RE.fullmatch(raw_line.rstrip("\r\n"))
        if match is None:
            raise ValueError("credential env contains an invalid assignment")
        occurrences.setdefault(match.group("key"), []).append(
            (index, _env_assignment_value(match.group("value")))
        )

    applied: dict[str, bool] = {}
    for key in normalized_updates:
        values = [value for _, value in occurrences.get(key, [])]
        if key in normalized_expected:
            current = values[0] if len(values) == 1 else None
            applied[key] = (
                len(values) <= 1
                and current == normalized_expected[key]
            )
        else:
            applied[key] = True

    repairable_keys = {key for key, did_apply in applied.items() if did_apply}
    for key, key_occurrences in occurrences.items():
        if len(key_occurrences) > 1 and key not in repairable_keys:
            raise ValueError("credential env contains duplicate keys")

    replacement_lines: dict[int, str | None] = {}
    append_lines: list[str] = []
    for key, value in normalized_updates.items():
        if not applied[key]:
            continue
        key_occurrences = occurrences.get(key, [])
        rendered = (
            None
            if value is None
            else f"{key}={_encode_env_value(value)}\n"
        )
        if key_occurrences:
            replacement_lines[key_occurrences[0][0]] = rendered
            for duplicate_index, _ in key_occurrences[1:]:
                replacement_lines[duplicate_index] = None
        elif rendered is not None:
            append_lines.append(rendered)

    rendered_lines = [
        replacement_lines.get(index, line)
        for index, line in enumerate(lines)
        if replacement_lines.get(index, line) is not None
    ]
    rendered_text = "".join(rendered_lines)
    if append_lines and rendered_text and not rendered_text.endswith(("\n", "\r")):
        rendered_text += "\n"
    rendered_text += "".join(append_lines)
    return rendered_text.encode("utf-8"), applied


def _reject_managed_credential_write(
    action: str,
    config_path: Path | None = None,
) -> None:
    """Fail closed before low-level writers create transaction state."""
    from hermes_cli.config import (
        ManagedConfigurationError,
        get_managed_system_for_config,
    )

    managed_system = get_managed_system_for_config(config_path)
    if managed_system is not None:
        raise ManagedConfigurationError(action, managed_system)


def mutate_config_strict(
    mutator: Callable[[dict[str, Any]], Any],
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Strictly mutate config.yaml and atomically replace its real target."""
    _reject_managed_credential_write("modify configuration", config_path)
    with credential_transaction(config_path) as spec:
        original_exists, original_bytes = _read_optional_bytes(
            spec.config_target
        )
        current = (
            _parse_config_bytes(original_bytes)
            if original_exists
            else {}
        )
        mutated = copy.deepcopy(current)
        result = mutator(mutated)
        if result is not None:
            mutated = result
        if not isinstance(mutated, dict):
            raise ValueError("credential config mutation must produce a mapping")
        payload = yaml.safe_dump(
            mutated,
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
        config_changed = (
            payload != original_bytes
            if original_exists
            else bool(mutated)
        )
        if config_changed:
            mode = _existing_target_mode(spec.config_target)
            _atomic_write_credential_bytes(
                spec.logical_config_path,
                payload,
                mode=mode,
                expected_exists=original_exists,
                expected_sha256=_sha256_bytes(original_bytes),
            )
        return copy.deepcopy(mutated)


def seed_config_payload_strict(
    config_payload: bytes,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Create a missing config from validated bytes through the shared lock.

    This is intentionally limited to first-write seeding so callers can retain
    comments in packaged templates without introducing another read/modify/
    write path for existing user configuration.
    """
    _reject_managed_credential_write("seed configuration", config_path)
    if not isinstance(config_payload, bytes):
        raise ValueError("credential config payload must be bytes")
    if len(config_payload) > _MAX_CREDENTIAL_CONFIG_BYTES:
        raise ValueError("credential config exceeds maximum size")
    parsed = _parse_config_bytes(config_payload)

    with credential_transaction(config_path) as spec:
        config_exists, _config_before = _read_optional_bytes(
            spec.config_target
        )
        if config_exists:
            raise FileExistsError(spec.logical_config_path)
        if config_payload:
            _atomic_write_credential_bytes(
                spec.logical_config_path,
                config_payload,
                mode=_credential_data_mode(),
                expected_exists=False,
                expected_sha256=_sha256_bytes(b""),
            )
        return copy.deepcopy(parsed)


def mutate_env_unique(
    updates: Mapping[str, str | None],
    *,
    config_path: Path | None = None,
    expected_values: Mapping[str, str | None] | None = None,
) -> dict[str, bool]:
    """Atomically mutate selected .env keys while rejecting other duplicates."""
    _reject_managed_credential_write("modify credentials", config_path)
    with credential_transaction(config_path) as spec:
        env_exists, original = _read_optional_bytes(spec.env_target)
        payload, applied = _mutated_env_bytes(
            original,
            updates,
            expected_values,
        )
        projection_keys = [
            key
            for key, did_apply in applied.items()
            if did_apply
        ]
        if (not env_exists and payload) or payload != original:
            _atomic_write_credential_bytes(
                spec.env_path,
                payload,
                mode=_credential_data_mode(),
                expected_exists=env_exists,
                expected_sha256=_sha256_bytes(original),
                env_keys=projection_keys,
            )
        elif projection_keys:
            _commit_credential_targets(
                [],
                env_keys=projection_keys,
            )
        return applied


def mutate_config_env_strict(
    config_mutator: Callable[[dict[str, Any]], Any],
    env_updates: Mapping[str, str | None],
    *,
    config_path: Path | None = None,
    expected_env_values: Mapping[str, str | None] | None = None,
) -> CredentialSnapshot:
    """Mutate config and .env through one durable roll-forward intent.

    When ``expected_env_values`` is provided, every updated key must still
    match the caller's snapshot.  A same-key concurrent write aborts the whole
    config/.env transaction instead of being silently overwritten.
    """
    _reject_managed_credential_write(
        "modify configuration and credentials",
        config_path,
    )
    with credential_transaction(config_path) as spec:
        config_exists, config_before = _read_optional_bytes(
            spec.config_target
        )
        env_exists, env_before = _read_optional_bytes(spec.env_target)
        current_config = (
            _parse_config_bytes(config_before)
            if config_exists
            else {}
        )
        mutated_config = copy.deepcopy(current_config)
        mutation_result = config_mutator(mutated_config)
        if mutation_result is not None:
            mutated_config = mutation_result
        if not isinstance(mutated_config, dict):
            raise ValueError("credential config mutation must produce a mapping")
        config_target = yaml.safe_dump(
            mutated_config,
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
        env_target, applied = _mutated_env_bytes(
            env_before,
            env_updates,
            expected_env_values,
        )
        if any(not did_apply for did_apply in applied.values()):
            raise _CredentialCompareAndSwapError(
                "credential env changed before config/env transaction publish"
            )
        config_changed = (
            config_target != config_before
            if config_exists
            else bool(mutated_config)
        )
        env_changed = (
            (not env_exists and bool(env_target))
            or env_target != env_before
        )
        projection_keys = [
            key
            for key, did_apply in applied.items()
            if did_apply
        ]
        if config_changed and env_changed:
            _commit_config_env_pair(
                config_path=spec.logical_config_path,
                config_exists=config_exists,
                config_before=config_before,
                config_target=config_target,
                env_exists=env_exists,
                env_before=env_before,
                env_target=env_target,
                env_keys=projection_keys,
            )
        elif config_changed:
            _atomic_write_credential_bytes(
                spec.logical_config_path,
                config_target,
                mode=_existing_target_mode(spec.config_target),
                expected_exists=config_exists,
                expected_sha256=_sha256_bytes(config_before),
                env_keys=projection_keys,
            )
        elif env_changed:
            _atomic_write_credential_bytes(
                spec.env_path,
                env_target,
                mode=_credential_data_mode(),
                expected_exists=env_exists,
                expected_sha256=_sha256_bytes(env_before),
                env_keys=projection_keys,
            )
        elif projection_keys:
            _commit_credential_targets(
                [],
                env_keys=projection_keys,
            )
        return _load_credential_snapshot_unlocked(spec)


def replace_config_env_payload_strict(
    config_mutator: Callable[[dict[str, Any]], Any],
    env_payload: bytes,
    *,
    config_path: Path | None = None,
    env_keys: tuple[str, ...] = (),
) -> CredentialSnapshot:
    """Atomically replace a repaired env payload with its config mutation.

    This narrow primitive exists for legacy env repair. Ordinary credential
    writers should keep using :func:`mutate_config_env_strict`, which preserves
    unrelated formatting while changing selected keys.
    """
    _reject_managed_credential_write(
        "replace configuration and credentials",
        config_path,
    )
    if not isinstance(env_payload, bytes):
        raise ValueError("credential env payload must be bytes")
    if len(env_payload) > _MAX_CREDENTIAL_ENV_BYTES:
        raise ValueError("credential env exceeds maximum size")
    _parse_env_bytes(env_payload)
    projection_keys = tuple(
        _validated_env_update(key, "")[0]
        for key in env_keys
    )
    with credential_transaction(config_path) as spec:
        config_exists, config_before = _read_optional_bytes(
            spec.config_target
        )
        env_exists, env_before = _read_optional_bytes(spec.env_target)
        current_config = (
            _parse_config_bytes(config_before)
            if config_exists
            else {}
        )
        mutated_config = copy.deepcopy(current_config)
        mutation_result = config_mutator(mutated_config)
        if mutation_result is not None:
            mutated_config = mutation_result
        if not isinstance(mutated_config, dict):
            raise ValueError("credential config mutation must produce a mapping")
        config_target = yaml.safe_dump(
            mutated_config,
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
        config_changed = (
            config_target != config_before
            if config_exists
            else bool(mutated_config)
        )
        env_changed = (
            (not env_exists and bool(env_payload))
            or env_payload != env_before
        )
        if config_changed and env_changed:
            _commit_config_env_pair(
                config_path=spec.logical_config_path,
                config_exists=config_exists,
                config_before=config_before,
                config_target=config_target,
                env_exists=env_exists,
                env_before=env_before,
                env_target=env_payload,
                env_keys=list(projection_keys),
            )
        elif config_changed:
            _atomic_write_credential_bytes(
                spec.logical_config_path,
                config_target,
                mode=_existing_target_mode(spec.config_target),
                expected_exists=config_exists,
                expected_sha256=_sha256_bytes(config_before),
                env_keys=list(projection_keys),
            )
        elif env_changed:
            _atomic_write_credential_bytes(
                spec.env_path,
                env_payload,
                mode=_credential_data_mode(),
                expected_exists=env_exists,
                expected_sha256=_sha256_bytes(env_before),
                env_keys=list(projection_keys),
            )
        elif projection_keys:
            _commit_credential_targets(
                [],
                env_keys=list(projection_keys),
            )
        return _load_credential_snapshot_unlocked(spec)


def load_credential_snapshot(
    config_path: Path | None = None,
) -> CredentialSnapshot:
    """Read config and .env from disk while holding the credential lock."""
    with credential_transaction(config_path) as spec:
        return _load_credential_snapshot_unlocked(spec)


def _load_credential_snapshot_unlocked(
    spec: _CredentialTransactionSpec,
) -> CredentialSnapshot:
    config_exists, config_bytes = _read_optional_bytes(
        spec.config_target
    )
    env_exists, env_bytes = _read_optional_bytes(spec.env_target)
    config = (
        _parse_config_bytes(config_bytes)
        if config_exists
        else {}
    )
    env = _parse_env_bytes(env_bytes)
    return CredentialSnapshot(
        config_path=spec.logical_config_path,
        env_path=spec.env_path,
        config_exists=config_exists,
        env_exists=env_exists,
        config_sha256=_sha256_bytes(config_bytes),
        env_sha256=_sha256_bytes(env_bytes),
        config=config,
        env=env,
    )


def load_credential_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load an exact credential-bearing config with strict YAML validation."""
    with credential_transaction(config_path) as spec:
        return _load_config_data(spec.config_target)


def find_credential(
    config_data: dict[str, Any] | None,
    credential_ref: object,
) -> dict[str, Any] | None:
    """Find credential metadata by normalized ID without exposing its secret."""
    target = normalize_credential_id(credential_ref)
    data = (
        config_data
        if isinstance(config_data, dict)
        else load_credential_config()
    )
    rows = data.get("provider_credentials")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            row_id = normalize_credential_id(row.get("id"))
        except ValueError:
            continue
        if row_id == target:
            return row
    return None


def load_credential(
    credential_ref: object,
    *,
    config_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load credential metadata or fail without falling back to another secret."""
    row = find_credential(config_data, credential_ref)
    if row is None:
        raise ValueError("所选凭据不存在。")
    return row


def default_credential_ref(
    provider: object,
    *,
    config_data: dict[str, Any] | None = None,
    config_path: Path | None = None,
    allow_process_fallback: bool | None = None,
    _pinned_spec: _CredentialTransactionSpec | None = None,
    _fallback_when_env_key_missing: bool | None = None,
) -> str:
    """Return the unique explicitly marked family default without mutating config."""
    if _fallback_when_env_key_missing is None:
        _fallback_when_env_key_missing = config_path is None
    allow_process_fallback = process_env_fallback_allowed(
        allow_process_fallback
    )
    family = provider_family(provider)
    if _pinned_spec is None:
        with credential_transaction(config_path) as spec:
            return default_credential_ref(
                family,
                config_data=config_data,
                allow_process_fallback=allow_process_fallback,
                _pinned_spec=spec,
                _fallback_when_env_key_missing=_fallback_when_env_key_missing,
            )
    spec = _pinned_spec
    data = (
        config_data
        if isinstance(config_data, dict)
        else _load_config_data(spec.config_target)
    )
    if "provider_credentials" not in data:
        return ""
    rows = data["provider_credentials"]
    if not isinstance(rows, list):
        raise ValueError("provider_credentials must be a list")
    marked_defaults: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        default_value = row.get("default", False)
        if default_value is False:
            continue
        raw_family = row.get("provider_family")
        if not isinstance(raw_family, str):
            raise ValueError("默认凭据的 Provider 配置无效。")
        marked_family = provider_family(raw_family)
        if not marked_family:
            raise ValueError("默认凭据的 Provider 配置无效。")
        if marked_family != family:
            continue
        if not isinstance(default_value, bool):
            raise ValueError("默认凭据标记必须是布尔值。")
        raw_credential_id = row.get("id")
        if not isinstance(raw_credential_id, str):
            raise ValueError("默认凭据 ID 配置无效。")
        try:
            credential_id = normalize_credential_id(raw_credential_id)
        except ValueError as exc:
            raise ValueError("默认凭据 ID 配置无效。") from exc
        raw_auth_type = row.get("auth_type", "api_key")
        if (
            not isinstance(raw_auth_type, str)
            or raw_auth_type.strip().lower() != "api_key"
        ):
            raise ValueError("默认凭据的认证类型配置无效。")
        if str(row.get("secret_env") or "").strip() != credential_secret_env(
            credential_id
        ):
            raise ValueError("默认凭据的 Secret 环境变量配置无效。")
        marked_defaults.append(credential_id)
    if not marked_defaults:
        return ""
    if len(marked_defaults) > 1:
        raise ValueError("当前 Provider 配置了多个默认凭据，请保留一个。")
    credential_id = marked_defaults[0]
    if not _credential_secret_value(
        credential_secret_env(credential_id),
        spec.config_target,
        allow_process_fallback=allow_process_fallback,
        fallback_when_env_key_missing=_fallback_when_env_key_missing,
    ):
        return ""
    return credential_id


def resolve_api_key(
    provider: object,
    credential_ref: object = "",
    *,
    config_data: dict[str, Any] | None = None,
    config_path: Path | None = None,
    allow_process_fallback: bool | None = None,
) -> str:
    """Resolve a named API key, or lazily fall back to the legacy provider env."""
    explicit_config_path = config_path is not None
    fallback_when_env_key_missing = not explicit_config_path
    allow_process_fallback = process_env_fallback_allowed(
        allow_process_fallback
    )
    with credential_transaction(config_path) as spec:
        resolved_path = spec.config_target
        family = provider_family(provider)
        ref = str(credential_ref or "").strip()
        explicit_ref = bool(ref)
        data = (
            config_data
            if isinstance(config_data, dict)
            else _load_config_data(resolved_path)
        )
        if not ref:
            ref = default_credential_ref(
                family,
                config_data=data,
                allow_process_fallback=allow_process_fallback,
                _pinned_spec=spec,
                _fallback_when_env_key_missing=fallback_when_env_key_missing,
            )

        if ref:
            row = load_credential(ref, config_data=data)
            if provider_family(row.get("provider_family")) != family:
                raise ValueError("所选凭据不属于当前 Provider。")
            secret_env = str(row.get("secret_env") or "").strip()
            if secret_env != credential_secret_env(row.get("id")):
                raise ValueError("所选凭据的 Secret 环境变量配置无效。")
            value = _credential_secret_value(
                secret_env,
                resolved_path,
                allow_process_fallback=allow_process_fallback,
                fallback_when_env_key_missing=fallback_when_env_key_missing,
            )
            if value or explicit_ref:
                return value
        for legacy_env in LEGACY_API_KEY_ENV.get(family, ()):
            value = _credential_secret_value(
                legacy_env,
                resolved_path,
                allow_process_fallback=allow_process_fallback,
            ).strip()
            if value:
                return value
        return ""
