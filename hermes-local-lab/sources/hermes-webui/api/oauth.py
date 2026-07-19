"""In-app OAuth flow implementations for onboarding.

The browser receives only WebUI-local flow metadata (flow_id, user_code,
verification_uri, high-level status). Provider device/auth codes and OAuth
tokens stay server-side and are persisted to the active Hermes profile's
``auth.json`` credential_pool.
"""

from __future__ import annotations

import json
import logging
import errno
import os
import stat
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is guarded below
    fcntl = None

logger = logging.getLogger(__name__)

# Compatibility for older helper tests and self-heal code that import these.
AUTH_JSON_PATH = Path.home() / ".hermes" / "auth.json"

CODEX_ISSUER = "https://auth.openai.com"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_VERIFICATION_URI = f"{CODEX_ISSUER}/codex/device"
CODEX_USER_CODE_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/token"
CODEX_TOKEN_URL = f"{CODEX_ISSUER}/oauth/token"
CODEX_REDIRECT_URI = f"{CODEX_ISSUER}/deviceauth/callback"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_FLOW_MAX_WAIT_SECONDS = 15 * 60

_ALLOWED_ONBOARDING_OAUTH_PROVIDERS = {"openai-codex", "anthropic", "claude", "claude-code"}
_ANTHROPIC_PROVIDER_ALIASES = {"anthropic", "claude", "claude-code"}
_REJECTED_ONBOARDING_OAUTH_PROVIDERS = {
    "nous",
    "qwen-oauth",
    "gemini-cli",
    "google-gemini-cli",
    "minimax",
    "minimax-oauth",
    "copilot",
    "copilot-acp",
}

ANTHROPIC_CREDENTIAL_POLL_SECONDS = 5
ANTHROPIC_FLOW_MAX_WAIT_SECONDS = 15 * 60
ANTHROPIC_PUBLIC_LINK_ERROR = "Claude Code 凭据关联失败，请查看服务器日志。"
CODEX_PUBLIC_OAUTH_ERROR = "Codex OAuth failed. Check server logs."
_AUTH_JSON_MAX_BYTES = 1024 * 1024

_OAUTH_FLOWS: dict[str, dict[str, Any]] = {}
_OAUTH_FLOWS_LOCK = threading.Lock()
_ANTHROPIC_ENV_KEYS = ("ANTHROPIC_TOKEN", "ANTHROPIC_API_KEY")
_ANTHROPIC_LINK_PENDING_SOURCE = "claude_code_link_pending"
_ANTHROPIC_LINKED_SOURCE = "claude_code_linked"
_ANTHROPIC_LINK_STAGE_CONTEXT = threading.local()
_ANTHROPIC_OWNED_STATUSES = frozenset({"linking", "committing"})
_AUTH_JSON_THREAD_LOCKS_GUARD = threading.Lock()
_AUTH_JSON_THREAD_LOCKS: dict[tuple[int, int, str], Any] = {}


class _AuthJsonPostReplaceSyncError(OSError):
    """The auth.json replace is visible but its directory sync was uncertain."""


def _clear_process_anthropic_env_values() -> None:
    """Clear Anthropic process env fallbacks under the streaming env lock."""
    from api.streaming import _ENV_LOCK

    with _ENV_LOCK:
        for key in _ANTHROPIC_ENV_KEYS:
            os.environ.pop(key, None)


def resolve_runtime_provider_with_anthropic_env_lock(resolver, *args, **kwargs):
    """Resolve runtime credentials under the Anthropic onboarding env lock.

    Request paths must resolve Anthropic env fallbacks per outbound request,
    not cache ANTHROPIC_TOKEN or ANTHROPIC_API_KEY across onboarding. Sharing
    the process-env lock prevents a chat stream from observing one stale
    Anthropic env value while onboarding has already cleared the other.
    """
    from agent.provider_credentials import credential_transaction
    from api.streaming import _ENV_LOCK
    from hermes_constants import get_config_path

    with credential_transaction(get_config_path()):
        with _ENV_LOCK:
            return resolver(*args, **kwargs)


def _normalize_onboarding_oauth_provider(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    if provider in _ANTHROPIC_PROVIDER_ALIASES:
        return "anthropic"
    return provider or "openai-codex"


def _get_active_hermes_home() -> Path:
    try:
        from api.profiles import get_active_hermes_home

        return Path(get_active_hermes_home())
    except Exception as exc:
        # Per Opus advisor on stage-296: log the silent fallback so a corrupt
        # profile state ending up writing tokens to ~/.hermes (instead of the
        # active profile) is observable in logs rather than failing silently.
        logger.warning(
            "Falling back to ~/.hermes for OAuth credential storage: "
            "active-profile resolution failed: %s",
            exc,
        )
        return Path.home() / ".hermes"


def _get_active_config_path() -> Path:
    """Capture the exact active config identity for one OAuth flow."""
    try:
        from agent.provider_credentials import credential_transaction
        from api.config import _get_config_path

        with credential_transaction(
            Path(_get_config_path())
        ) as credential_spec:
            return credential_spec.config_target
    except Exception as exc:
        logger.error(
            "Active OAuth config resolution failed; refusing fallback",
            exc_info=True,
        )
        raise RuntimeError(
            "Active OAuth configuration is unavailable."
        ) from exc


# ── legacy auth.json helpers ────────────────────────────────────────────────

def _auth_json_open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _assert_auth_directory_identity(
    parent: Path,
    expected: tuple[int, int],
) -> None:
    try:
        current = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise OSError(
            errno.ESTALE,
            "auth.json directory changed during transaction",
            str(parent),
        ) from exc
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != expected
    ):
        raise OSError(
            errno.ESTALE,
            "auth.json directory changed during transaction",
            str(parent),
        )


def _auth_target_identity(directory_fd: int, name: str) -> tuple[int, int] | None:
    try:
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
        raise OSError(errno.ELOOP, "unsafe auth.json target", name)
    return current.st_dev, current.st_ino


@contextmanager
def _locked_auth_json(auth_path: Path):
    """Pin one auth.json parent and lock its exact basename across processes."""
    path = Path(auth_path).expanduser()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(parent, _auth_json_open_flags(directory=True))
    lock_fd = -1
    thread_lock = None
    try:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise OSError(errno.ENOTDIR, "auth.json parent is not a directory", str(parent))
        directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
        lock_key = (*directory_identity, path.name)
        with _AUTH_JSON_THREAD_LOCKS_GUARD:
            thread_lock = _AUTH_JSON_THREAD_LOCKS.setdefault(
                lock_key,
                threading.RLock(),
            )
        thread_lock.acquire()
        _assert_auth_directory_identity(parent, directory_identity)

        lock_name = f".{path.name}.lock"
        lock_flags = os.O_RDWR | os.O_CREAT
        lock_flags |= getattr(os, "O_CLOEXEC", 0)
        lock_flags |= getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(
            lock_name,
            lock_flags,
            stat.S_IRUSR | stat.S_IWUSR,
            dir_fd=directory_fd,
        )
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise OSError(errno.ELOOP, "unsafe auth.json lock target", lock_name)
        os.fchmod(lock_fd, stat.S_IRUSR | stat.S_IWUSR)
        if fcntl is None:
            raise OSError(errno.ENOTSUP, "cross-process auth.json locking is unavailable")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _assert_auth_directory_identity(parent, directory_identity)
        yield path, parent, directory_fd, directory_identity
    finally:
        if lock_fd >= 0:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        if thread_lock is not None:
            thread_lock.release()
        os.close(directory_fd)


def _read_auth_json_locked(
    directory_fd: int,
    name: str,
    *,
    strict: bool,
) -> tuple[dict[str, Any], tuple[int, int] | None]:
    target_identity = _auth_target_identity(directory_fd, name)
    if target_identity is None:
        return {}, None
    fd = os.open(name, _auth_json_open_flags(), dir_fd=directory_fd)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != target_identity
        ):
            raise OSError(errno.ESTALE, "auth.json target changed during read", name)
        if opened.st_size > _AUTH_JSON_MAX_BYTES:
            if strict:
                raise ValueError("auth.json exceeds the maximum allowed size")
            logger.warning("auth.json exceeds the maximum allowed size")
            return {}, target_identity
        chunks: list[bytes] = []
        remaining = _AUTH_JSON_MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw_bytes = b"".join(chunks)
        if len(raw_bytes) > _AUTH_JSON_MAX_BYTES:
            if strict:
                raise ValueError("auth.json exceeds the maximum allowed size")
            logger.warning("auth.json exceeds the maximum allowed size")
            return {}, target_identity
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            if strict:
                raise ValueError("auth.json is not valid UTF-8") from exc
            logger.warning("auth.json is not valid UTF-8: %s", exc)
            return {}, target_identity
    finally:
        os.close(fd)
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError(f"Failed to parse auth.json: {exc}") from exc
        logger.warning("Failed to parse auth.json: %s", exc)
        return {}, target_identity
    if not isinstance(loaded, dict):
        if strict:
            raise ValueError("auth.json must contain a JSON object")
        return {}, target_identity
    return loaded, target_identity


def _write_auth_json_locked(
    data: dict[str, Any],
    *,
    path: Path,
    parent: Path,
    directory_fd: int,
    directory_identity: tuple[int, int],
    expected_target: tuple[int, int] | None,
) -> Path:
    _assert_auth_directory_identity(parent, directory_identity)
    if _auth_target_identity(directory_fd, path.name) != expected_target:
        raise OSError(errno.ESTALE, "auth.json target changed during transaction", path.name)

    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp_name = f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    tmp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    tmp_flags |= getattr(os, "O_CLOEXEC", 0)
    tmp_flags |= getattr(os, "O_NOFOLLOW", 0)
    tmp_fd = os.open(
        tmp_name,
        tmp_flags,
        stat.S_IRUSR | stat.S_IWUSR,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(tmp_fd, payload[offset:])
        os.fsync(tmp_fd)
        os.fchmod(tmp_fd, stat.S_IRUSR | stat.S_IWUSR)
        _assert_auth_directory_identity(parent, directory_identity)
        if _auth_target_identity(directory_fd, path.name) != expected_target:
            raise OSError(errno.ESTALE, "auth.json target changed before replace", path.name)
        os.replace(
            tmp_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            raise _AuthJsonPostReplaceSyncError(str(exc)) from exc
        _assert_auth_directory_identity(parent, directory_identity)
        written = os.stat(
            path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(written.st_mode) or written.st_nlink != 1:
            raise OSError(errno.EIO, "auth.json replace produced an unsafe target", path.name)
        return path
    finally:
        os.close(tmp_fd)
        try:
            os.unlink(tmp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _mutate_auth_json(auth_path: Path, mutator) -> Path:
    """Reread, merge and durably replace one exact auth.json under its lock."""
    path = Path(auth_path).expanduser()
    with _locked_auth_json(path) as (
        locked_path,
        parent,
        directory_fd,
        directory_identity,
    ):
        auth, target_identity = _read_auth_json_locked(
            directory_fd,
            locked_path.name,
            strict=True,
        )
        replacement = mutator(auth)
        if replacement is not None:
            if not isinstance(replacement, dict):
                raise TypeError("auth.json mutator must return a dict or None")
            auth = replacement
        return _write_auth_json_locked(
            auth,
            path=locked_path,
            parent=parent,
            directory_fd=directory_fd,
            directory_identity=directory_identity,
            expected_target=target_identity,
        )


def _read_auth_json(auth_path: Path | None = None) -> dict[str, Any]:
    """Read auth.json without following a substituted target."""
    path = Path(auth_path or AUTH_JSON_PATH)
    try:
        with _locked_auth_json(path) as (
            locked_path,
            _parent,
            directory_fd,
            _directory_identity,
        ):
            loaded, _identity = _read_auth_json_locked(
                directory_fd,
                locked_path.name,
                strict=False,
            )
            return loaded
    except OSError as exc:
        logger.warning("Failed to safely read %s: %s", path, exc)
        return {}


def read_auth_json():
    """Public wrapper for streaming credential self-heal code."""
    return _read_auth_json()


def _write_auth_json(data: dict[str, Any], auth_path: Path | None = None) -> Path:
    """Atomically replace auth.json under the exact-path transaction."""
    desired = dict(data)

    def replace(current: dict[str, Any]) -> None:
        current.clear()
        current.update(desired)

    return _mutate_auth_json(Path(auth_path or AUTH_JSON_PATH), replace)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _persist_codex_credentials(hermes_home: Path, token_data: dict[str, Any]) -> Path:
    """Persist Codex OAuth credentials to active-profile auth.json."""
    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip()
    if not access_token:
        raise RuntimeError("Codex token exchange did not return an access_token")

    auth_path = Path(hermes_home) / "auth.json"
    now = _now_iso()

    def persist(auth: dict[str, Any]) -> None:
        auth.setdefault("version", 1)
        pool = auth.setdefault("credential_pool", {})
        if not isinstance(pool, dict):
            pool = {}
            auth["credential_pool"] = pool
        entries = pool.setdefault("openai-codex", [])
        if not isinstance(entries, list):
            entries = []
            pool["openai-codex"] = entries

        entry = None
        # Accept the legacy source so an existing entry is updated in-place
        # rather than leaving a stale duplicate in the credential pool.
        accepted_sources = {"manual:device_code", "oauth_device"}
        for candidate in entries:
            if (
                isinstance(candidate, dict)
                and candidate.get("source") in accepted_sources
            ):
                entry = candidate
                break
        if entry is None:
            entry = {
                "id": "codex-oauth-" + uuid.uuid4().hex[:12],
                "label": "Codex OAuth",
                "auth_type": "oauth",
                "priority": 0,
                "source": "manual:device_code",
                "base_url": CODEX_BASE_URL,
                "created_at": now,
            }
            entries.insert(0, entry)

        entry.update(
            {
                "label": "Codex OAuth",
                "auth_type": "oauth",
                "priority": 0,
                "source": "manual:device_code",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "base_url": CODEX_BASE_URL,
                "last_refresh": now,
                "updated_at": now,
            }
        )
        auth["updated_at"] = now

    path = _mutate_auth_json(auth_path, persist)

    try:
        from api.config import invalidate_credential_pool_cache

        invalidate_credential_pool_cache("openai-codex")
    except Exception:
        logger.debug("Failed to invalidate openai-codex credential cache", exc_info=True)

    return path


# Backward-compatible wrapper used by older code/tests.
def _save_codex_credentials(token_data):
    return _persist_codex_credentials(_get_active_hermes_home(), token_data)


# ── Anthropic / Claude Code credential linking ─────────────────────────────

def _read_claude_code_credentials() -> dict[str, Any] | None:
    """Read Claude Code OAuth credentials from the host without exposing them.

    Delegates to the agent adapter which knows about ~/.claude/.credentials.json
    and macOS Keychain. Returns the credential dict or None.
    """
    try:
        from agent.anthropic_adapter import (
            is_claude_code_token_valid,
            read_claude_code_credentials,
        )

        creds = read_claude_code_credentials()
        if creds and (
            is_claude_code_token_valid(creds) or bool(creds.get("refreshToken"))
        ):
            return creds
    except Exception as exc:
        logger.debug("Could not read Claude Code credentials: %s", exc)
    return None


def _clear_anthropic_env_values(config_path: Path) -> None:
    """Clear Anthropic API/setup-token env values in the active profile only.

    The canonical .env writer clears os.environ only after its durable disk
    mutation succeeds. Propagate any failure so auth.json is never marked as
    linked while stale API-key fallbacks are still active.
    """
    config_path = Path(config_path)
    env_path = config_path.parent / ".env"
    from api.providers import _write_env_file

    updates = getattr(_ANTHROPIC_LINK_STAGE_CONTEXT, "updates", None)
    if not isinstance(updates, dict):
        updates = {key: None for key in _ANTHROPIC_ENV_KEYS}
    _write_env_file(
        env_path,
        updates,
        config_path=config_path,
    )


def _load_anthropic_link_dependencies() -> tuple[Any, Any]:
    """Load modules with config side effects before taking an exact-path lock."""
    from api.providers import _write_env_file
    from api.streaming import _ENV_LOCK

    return _write_env_file, _ENV_LOCK


def _anthropic_link_backup_env_keys(intent_id: str) -> dict[str, str]:
    text = str(intent_id or "").strip().lower()
    try:
        normalized = uuid.UUID(hex=text).hex
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("invalid Anthropic link intent id") from exc
    if normalized != text:
        raise RuntimeError("invalid Anthropic link intent id")
    suffix = normalized.upper()
    return {
        "ANTHROPIC_TOKEN": (
            f"TAIJI_ANTHROPIC_LINK_BACKUP_{suffix}_TOKEN"
        ),
        "ANTHROPIC_API_KEY": (
            f"TAIJI_ANTHROPIC_LINK_BACKUP_{suffix}_API_KEY"
        ),
    }


def _anthropic_pool_entries(auth: dict[str, Any]) -> list[Any]:
    auth.setdefault("version", 1)
    pool = auth.setdefault("credential_pool", {})
    if not isinstance(pool, dict):
        pool = {}
        auth["credential_pool"] = pool
    entries = pool.setdefault("anthropic", [])
    if not isinstance(entries, list):
        entries = []
        pool["anthropic"] = entries
    return entries


def _prepare_anthropic_link_marker(
    auth_path: Path,
    intent_id: str,
    now: str,
) -> None:
    def prepare(auth: dict[str, Any]) -> None:
        entries = _anthropic_pool_entries(auth)
        if any(
            isinstance(entry, dict)
            and entry.get("source") == _ANTHROPIC_LINK_PENDING_SOURCE
            for entry in entries
        ):
            raise RuntimeError(
                "an unresolved Anthropic credential-link intent remains"
            )
        entries.insert(
            0,
            {
                "id": "anthropic-link-intent-" + intent_id[:12],
                "label": "Claude Code (link preparing)",
                "auth_type": "oauth",
                "priority": 0,
                "source": _ANTHROPIC_LINK_PENDING_SOURCE,
                "intent_id": intent_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        auth["updated_at"] = now

    _mutate_auth_json(auth_path, prepare)


def _commit_anthropic_link_marker(
    auth_path: Path,
    intent_id: str,
    now: str,
) -> None:
    def commit(auth: dict[str, Any]) -> None:
        entries = _anthropic_pool_entries(auth)
        pending = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("source") == _ANTHROPIC_LINK_PENDING_SOURCE
                and entry.get("intent_id") == intent_id
            ),
            None,
        )
        if pending is None:
            raise RuntimeError("Anthropic credential-link intent is missing")
        linked = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("source") == _ANTHROPIC_LINKED_SOURCE
            ),
            None,
        )
        marker = linked if linked is not None else pending
        marker.setdefault(
            "id",
            "anthropic-claude-code-" + uuid.uuid4().hex[:12],
        )
        marker.setdefault("created_at", now)
        marker.update(
            {
                "label": "Claude Code (linked)",
                "auth_type": "oauth",
                "priority": 0,
                "source": _ANTHROPIC_LINKED_SOURCE,
                "intent_id": intent_id,
                "updated_at": now,
            }
        )
        kept = [
            entry
            for entry in entries
            if entry is not pending
            and not (
                isinstance(entry, dict)
                and entry.get("source") == _ANTHROPIC_LINK_PENDING_SOURCE
                and entry.get("intent_id") == intent_id
            )
        ]
        if linked is None:
            kept.insert(0, marker)
        pool = auth["credential_pool"]
        pool["anthropic"] = kept
        auth["updated_at"] = now

    _mutate_auth_json(auth_path, commit)


def _remove_anthropic_pending_marker(
    auth_path: Path,
    intent_id: str,
) -> None:
    def remove(auth: dict[str, Any]) -> None:
        pool = auth.get("credential_pool")
        if not isinstance(pool, dict):
            return
        entries = pool.get("anthropic")
        if not isinstance(entries, list):
            return
        kept = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and entry.get("source") == _ANTHROPIC_LINK_PENDING_SOURCE
                and entry.get("intent_id") == intent_id
            )
        ]
        if len(kept) == len(entries):
            return
        if kept:
            pool["anthropic"] = kept
        else:
            pool.pop("anthropic", None)
        auth["updated_at"] = _now_iso()

    _mutate_auth_json(auth_path, remove)


def _capture_anthropic_env_state(
    config_path: Path,
) -> tuple[dict[str, str], dict[str, tuple[bool, str | None]]]:
    from agent.provider_credentials import load_credential_snapshot

    snapshot = load_credential_snapshot(config_path)
    disk_values = {
        key: snapshot.env[key]
        for key in _ANTHROPIC_ENV_KEYS
        if key in snapshot.env
    }
    process_values = {
        key: (key in os.environ, os.environ.get(key))
        for key in _ANTHROPIC_ENV_KEYS
    }
    return disk_values, process_values


def _stage_anthropic_env_clear(
    config_path: Path,
    intent_id: str,
    disk_values: dict[str, str],
) -> None:
    backup_keys = _anthropic_link_backup_env_keys(intent_id)
    updates: dict[str, str | None] = {
        key: None for key in _ANTHROPIC_ENV_KEYS
    }
    for source_key, backup_key in backup_keys.items():
        updates[backup_key] = disk_values.get(source_key)
    _ANTHROPIC_LINK_STAGE_CONTEXT.updates = updates
    try:
        # Keep this public helper as the single clear point so the runtime
        # provider-read lock contract and existing instrumentation remain true.
        _clear_anthropic_env_values(config_path)
    finally:
        try:
            del _ANTHROPIC_LINK_STAGE_CONTEXT.updates
        except AttributeError:
            pass


def _restore_process_anthropic_env_state(
    process_values: dict[str, tuple[bool, str | None]],
    backup_keys: dict[str, str],
) -> None:
    from api.streaming import _ENV_LOCK

    with _ENV_LOCK:
        for key, (was_present, value) in process_values.items():
            if was_present:
                os.environ[key] = str(value or "")
            else:
                os.environ.pop(key, None)
        for backup_key in backup_keys.values():
            os.environ.pop(backup_key, None)


def _rollback_anthropic_env_stage(
    config_path: Path,
    intent_id: str,
    process_values: dict[str, tuple[bool, str | None]] | None = None,
) -> None:
    from agent.provider_credentials import load_credential_snapshot
    from api.providers import _write_env_file

    backup_keys = _anthropic_link_backup_env_keys(intent_id)
    snapshot = load_credential_snapshot(config_path)
    backup_present = any(
        backup_key in snapshot.env
        for backup_key in backup_keys.values()
    )
    if not backup_present:
        if process_values is not None:
            _restore_process_anthropic_env_state(
                process_values,
                backup_keys,
            )
        return
    updates: dict[str, str | None] = {
        backup_key: None for backup_key in backup_keys.values()
    }
    for source_key, backup_key in backup_keys.items():
        updates[source_key] = snapshot.env.get(backup_key)
    _write_env_file(
        config_path.parent / ".env",
        updates,
        config_path=config_path,
    )
    if process_values is not None:
        _restore_process_anthropic_env_state(
            process_values,
            backup_keys,
        )


def _cleanup_anthropic_env_backup(
    config_path: Path,
    intent_id: str,
) -> None:
    from api.providers import _write_env_file

    backup_keys = _anthropic_link_backup_env_keys(intent_id)
    _write_env_file(
        config_path.parent / ".env",
        {backup_key: None for backup_key in backup_keys.values()},
        config_path=config_path,
    )


def _recover_anthropic_link_intents(
    config_path: Path,
    auth_path: Path,
) -> None:
    auth = _read_auth_json(auth_path)
    pool = auth.get("credential_pool")
    entries = pool.get("anthropic") if isinstance(pool, dict) else None
    if not isinstance(entries, list):
        return
    linked_intents: list[str] = []
    pending_intents: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        intent_id = str(entry.get("intent_id") or "").strip().lower()
        if not intent_id:
            continue
        _anthropic_link_backup_env_keys(intent_id)
        if source == _ANTHROPIC_LINKED_SOURCE:
            linked_intents.append(intent_id)
        elif source == _ANTHROPIC_LINK_PENDING_SOURCE:
            pending_intents.append(intent_id)
    for intent_id in linked_intents:
        _cleanup_anthropic_env_backup(config_path, intent_id)
    for intent_id in pending_intents:
        _rollback_anthropic_env_stage(config_path, intent_id)
        _remove_anthropic_pending_marker(auth_path, intent_id)


def _link_anthropic_credentials(config_path: Path) -> None:
    """Link Hermes to use Claude Code's credential store.

    Clears ANTHROPIC_TOKEN and ANTHROPIC_API_KEY from the Hermes .env so
    that resolve_anthropic_token() falls through to reading Claude Code's
    ~/.claude/.credentials.json directly — the same thing the CLI's
    ``use_anthropic_claude_code_credentials()`` does.

    Also writes a marker entry in auth.json credential_pool so that
    ``_provider_oauth_authenticated("anthropic", ...)`` can detect the
    linked state without touching the actual credential files.
    """
    from agent.provider_credentials import credential_transaction

    config_path = Path(config_path)
    _load_anthropic_link_dependencies()
    with credential_transaction(config_path) as credential_spec:
        config_path = credential_spec.config_target
        auth_path = config_path.parent / "auth.json"
        _recover_anthropic_link_intents(config_path, auth_path)
        disk_values, process_values = _capture_anthropic_env_state(
            config_path
        )
        intent_id = uuid.uuid4().hex
        now = _now_iso()
        _prepare_anthropic_link_marker(auth_path, intent_id, now)
        try:
            _stage_anthropic_env_clear(
                config_path,
                intent_id,
                disk_values,
            )
            _commit_anthropic_link_marker(
                auth_path,
                intent_id,
                now,
            )
        except _AuthJsonPostReplaceSyncError:
            # os.replace has already made the linked marker visible. Restoring
            # the old Anthropic Secret here would create two conflicting
            # credential sources. Keep the staged backup for deterministic
            # recovery on the next transaction instead.
            logger.error(
                "Anthropic link marker replace completed but directory sync "
                "was uncertain; leaving the staged Secret backup for recovery",
                exc_info=True,
            )
            raise
        except Exception:
            try:
                _rollback_anthropic_env_stage(
                    config_path,
                    intent_id,
                    process_values,
                )
            except Exception:
                logger.exception(
                    "Anthropic link compensation failed; the pending "
                    "intent will be recovered on the next attempt"
                )
                raise
            _remove_anthropic_pending_marker(auth_path, intent_id)
            raise
        try:
            _cleanup_anthropic_env_backup(config_path, intent_id)
        except Exception:
            # The linked marker retains the non-secret intent id, so a later
            # call can safely finish this cleanup without changing auth state.
            logger.exception(
                "Anthropic link committed but backup cleanup is pending"
            )

    try:
        from api.config import invalidate_credential_pool_cache
        invalidate_credential_pool_cache("anthropic")
    except Exception:
        logger.debug("Failed to invalidate anthropic credential cache", exc_info=True)


def _anthropic_public_start_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "provider": "anthropic",
        "flow_id": flow_id,
        "status": flow.get("status", "pending"),
        "poll_interval_seconds": flow.get("poll_interval_seconds", ANTHROPIC_CREDENTIAL_POLL_SECONDS),
    }
    if flow.get("status") == "pending":
        payload["action_required"] = (
            "服务器上未找到 Claude Code 凭据。"
            "请在主机终端运行 'claude login' 或 'claude setup-token'，"
            "然后返回此处；本页面会自动检测凭据。"
        )
    if flow.get("expires_at"):
        payload["expires_at"] = flow["expires_at"]
    return payload


def _anthropic_public_status_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "provider": "anthropic",
        "flow_id": flow_id,
        "status": flow.get("status", "error"),
    }
    if flow.get("status") == "error" and flow.get("error"):
        payload["error"] = ANTHROPIC_PUBLIC_LINK_ERROR
    return payload


def _spawn_anthropic_credential_worker(flow_id: str) -> None:
    worker = threading.Thread(
        target=_run_anthropic_credential_worker, args=(flow_id,), daemon=True,
    )
    worker.start()


def _run_anthropic_credential_worker(flow_id: str) -> None:
    """Poll for Claude Code credential appearance until found, cancelled, or expired."""
    while True:
        with _OAUTH_FLOWS_LOCK:
            flow = dict(_OAUTH_FLOWS.get(flow_id) or {})
        if not flow:
            return
        if flow.get("status") != "pending":
            return
        if float(flow.get("expires_at") or 0) <= time.time():
            _set_flow_status(flow_id, "expired")
            return

        time.sleep(max(1, int(flow.get("poll_interval_seconds") or ANTHROPIC_CREDENTIAL_POLL_SECONDS)))

        # Re-check status under lock (cancel may have arrived during sleep)
        with _OAUTH_FLOWS_LOCK:
            live = _OAUTH_FLOWS.get(flow_id)
            if not live or live.get("status") != "pending":
                return

        try:
            creds = _read_claude_code_credentials()
            if creds is None:
                continue

            # This is the final cancellable boundary. Once credentials have
            # been observed, the server owns the commit and cancel returns the
            # live progress state instead of claiming side effects rolled back.
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if not current or current.get("status") != "pending":
                    return
                current["status"] = "linking"
                current["updated_at"] = time.time()

            config_path = Path(flow["config_path"])
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if not current or current.get("status") != "linking":
                    return
                current["status"] = "committing"
                current["updated_at"] = time.time()
            _link_anthropic_credentials(config_path)
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if not current or current.get("status") != "committing":
                    return
                current["status"] = "success"
                current["updated_at"] = time.time()
                _drop_sensitive_flow_fields(current)
            return
        except Exception as exc:
            logger.warning("Anthropic credential polling failed: %s", exc)
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if current and current.get("status") in (
                    {"pending"} | _ANTHROPIC_OWNED_STATUSES
                ):
                    current["status"] = "error"
                    current["updated_at"] = time.time()
                    current["error"] = str(exc)
                    _drop_sensitive_flow_fields(current)
            return


def _remove_anthropic_link_marker(hermes_home: Path) -> None:
    """Remove the secret-free Claude Code linked marker transactionally."""
    auth_path = Path(hermes_home) / "auth.json"
    changed = False

    def remove(auth: dict[str, Any]) -> None:
        nonlocal changed
        pool = auth.get("credential_pool")
        if not isinstance(pool, dict):
            return
        entries = pool.get("anthropic")
        if not isinstance(entries, list):
            return
        kept = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and entry.get("source") == "claude_code_linked"
            )
        ]
        if len(kept) == len(entries):
            return
        changed = True
        if kept:
            pool["anthropic"] = kept
        else:
            pool.pop("anthropic", None)
        auth["updated_at"] = _now_iso()

    _mutate_auth_json(auth_path, remove)
    if not changed:
        return
    try:
        from api.config import invalidate_credential_pool_cache
        invalidate_credential_pool_cache("anthropic")
    except Exception:
        logger.debug("Failed to invalidate anthropic credential cache", exc_info=True)


# ── Codex protocol ──────────────────────────────────────────────────────────

def _json_request(url: str, payload: dict[str, Any], *, form: bool = False) -> dict[str, Any]:
    if form:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": content_type, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _request_codex_user_code() -> dict[str, Any]:
    return _json_request(CODEX_USER_CODE_URL, {"client_id": CODEX_CLIENT_ID})


def _poll_codex_authorization(device_auth_id: str, user_code: str) -> dict[str, Any] | None:
    try:
        return _json_request(
            CODEX_DEVICE_TOKEN_URL,
            {"device_auth_id": device_auth_id, "user_code": user_code},
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise


def _exchange_codex_authorization(authorization_code: str, code_verifier: str) -> dict[str, Any]:
    return _json_request(
        CODEX_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": CODEX_REDIRECT_URI,
            "client_id": CODEX_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        form=True,
    )


def _codex_public_start_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "provider": "openai-codex",
        "flow_id": flow_id,
        "status": flow.get("status", "pending"),
        "verification_uri": CODEX_VERIFICATION_URI,
        "user_code": flow.get("user_code", ""),
        "expires_at": flow.get("expires_at"),
        "poll_interval_seconds": flow.get("poll_interval_seconds", 5),
    }


def _codex_public_status_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "ok": True,
        "provider": "openai-codex",
        "flow_id": flow_id,
        "status": flow.get("status", "error"),
    }
    if flow.get("status") == "error" and flow.get("error"):
        payload["error"] = CODEX_PUBLIC_OAUTH_ERROR
    return payload


def _public_start_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    provider = flow.get("provider", "openai-codex")
    if provider == "anthropic":
        return _anthropic_public_start_payload(flow_id, flow)
    return _codex_public_start_payload(flow_id, flow)


def _public_status_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    provider = flow.get("provider", "openai-codex")
    if provider == "anthropic":
        return _anthropic_public_status_payload(flow_id, flow)
    return _codex_public_status_payload(flow_id, flow)


def _drop_sensitive_flow_fields(flow: dict[str, Any]) -> None:
    for key in (
        "device_auth_id",
        "authorization_code",
        "code_verifier",
        "access_token",
        "refresh_token",
        "token_data",
    ):
        flow.pop(key, None)


def _cleanup_oauth_flows(now: float | None = None) -> None:
    now = now or time.time()
    cutoff = now - 300
    with _OAUTH_FLOWS_LOCK:
        for fid, flow in list(_OAUTH_FLOWS.items()):
            status = flow.get("status")
            if status == "pending" and float(flow.get("expires_at") or 0) <= now:
                flow["status"] = "expired"
                _drop_sensitive_flow_fields(flow)
            if status in {"success", "expired", "cancelled", "error"} and float(flow.get("updated_at") or 0) < cutoff:
                _OAUTH_FLOWS.pop(fid, None)


def _spawn_codex_oauth_worker(flow_id: str) -> None:
    worker = threading.Thread(target=_run_codex_oauth_worker, args=(flow_id,), daemon=True)
    worker.start()


def _set_flow_status(flow_id: str, status: str, **fields: Any) -> None:
    with _OAUTH_FLOWS_LOCK:
        flow = _OAUTH_FLOWS.get(flow_id)
        if not flow:
            return
        flow["status"] = status
        flow["updated_at"] = time.time()
        flow.update(fields)
        if status in {"success", "expired", "cancelled", "error"}:
            _drop_sensitive_flow_fields(flow)


def _run_codex_oauth_worker(flow_id: str) -> None:
    while True:
        with _OAUTH_FLOWS_LOCK:
            flow = dict(_OAUTH_FLOWS.get(flow_id) or {})
        if not flow:
            return
        status = flow.get("status")
        if status != "pending":
            return
        if float(flow.get("expires_at") or 0) <= time.time():
            _set_flow_status(flow_id, "expired")
            return

        time.sleep(max(1, int(flow.get("poll_interval_seconds") or 5)))

        with _OAUTH_FLOWS_LOCK:
            live = dict(_OAUTH_FLOWS.get(flow_id) or {})
        if live.get("status") != "pending":
            return
        try:
            code_resp = _poll_codex_authorization(
                str(live.get("device_auth_id") or ""),
                str(live.get("user_code") or ""),
            )
            if code_resp is None:
                continue
            authorization_code = str(code_resp.get("authorization_code") or "").strip()
            code_verifier = str(code_resp.get("code_verifier") or "").strip()
            if not authorization_code or not code_verifier:
                raise RuntimeError("Device auth response missing authorization_code or code_verifier")
            tokens = _exchange_codex_authorization(authorization_code, code_verifier)
            # Re-check status under lock before persisting: a cancel/expire that
            # raced with the device-token + token-exchange network calls must
            # win, so we don't persist credentials the user explicitly aborted.
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if not current or current.get("status") != "pending":
                    return
            _persist_codex_credentials(Path(live["hermes_home"]), tokens)
            _set_flow_status(flow_id, "success")
            return
        except Exception as exc:
            logger.warning("Codex OAuth onboarding flow failed: %s", exc)
            _set_flow_status(flow_id, "error", error=str(exc))
            return


def _start_anthropic_flow(config_path: Path) -> dict[str, Any]:
    """Start or immediately complete the Anthropic credential-linking flow."""
    config_path = Path(config_path)
    hermes_home = config_path.parent
    creds = _read_claude_code_credentials()
    flow_id = uuid.uuid4().hex

    if creds:
        # Credentials already exist — link and return success immediately.
        _link_anthropic_credentials(config_path)
        flow = {
            "provider": "anthropic",
            "status": "success",
            "hermes_home": str(hermes_home),
            "config_path": str(config_path),
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        with _OAUTH_FLOWS_LOCK:
            _OAUTH_FLOWS[flow_id] = flow
        return _public_start_payload(flow_id, flow)

    # No credentials found — create a pending flow that polls for them.
    expires_at = time.time() + ANTHROPIC_FLOW_MAX_WAIT_SECONDS
    flow = {
        "provider": "anthropic",
        "status": "pending",
        "expires_at": expires_at,
        "poll_interval_seconds": ANTHROPIC_CREDENTIAL_POLL_SECONDS,
        "hermes_home": str(hermes_home),
        "config_path": str(config_path),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with _OAUTH_FLOWS_LOCK:
        _OAUTH_FLOWS[flow_id] = flow
    _spawn_anthropic_credential_worker(flow_id)
    return _public_start_payload(flow_id, flow)


def start_onboarding_oauth_flow(body: dict[str, Any] | None) -> dict[str, Any]:
    """Start the supported onboarding OAuth flow.

    Supports OpenAI Codex (device-code flow) and Anthropic/Claude Code
    (credential-linking flow). Other providers are rejected.
    """
    _cleanup_oauth_flows()
    provider = str((body or {}).get("provider") or "").strip().lower()
    if provider not in _ALLOWED_ONBOARDING_OAUTH_PROVIDERS:
        if provider in _REJECTED_ONBOARDING_OAUTH_PROVIDERS or provider:
            raise ValueError(
                "Only OpenAI Codex and Anthropic/Claude OAuth are supported "
                "in WebUI onboarding right now"
            )
        raise ValueError("provider is required")

    config_path = _get_active_config_path()
    hermes_home = config_path.parent

    # Normalize Claude aliases to canonical "anthropic"
    if provider in _ANTHROPIC_PROVIDER_ALIASES:
        return _start_anthropic_flow(config_path)

    # Codex flow
    try:
        device = _request_codex_user_code()
    except Exception as exc:
        logger.exception("Failed to start Codex OAuth")
        raise RuntimeError(CODEX_PUBLIC_OAUTH_ERROR) from exc

    user_code = str(device.get("user_code") or "").strip()
    device_auth_id = str(device.get("device_auth_id") or "").strip()
    if not user_code or not device_auth_id:
        raise RuntimeError("Device code response missing required fields")

    interval = max(3, int(device.get("interval") or 5))
    expires_in = int(device.get("expires_in") or CODEX_FLOW_MAX_WAIT_SECONDS)
    expires_at = time.time() + min(max(expires_in, 60), CODEX_FLOW_MAX_WAIT_SECONDS)
    flow_id = uuid.uuid4().hex
    flow = {
        "provider": "openai-codex",
        "status": "pending",
        "device_auth_id": device_auth_id,
        "user_code": user_code,
        "expires_at": expires_at,
        "poll_interval_seconds": interval,
        "hermes_home": str(hermes_home),
        "config_path": str(config_path),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with _OAUTH_FLOWS_LOCK:
        _OAUTH_FLOWS[flow_id] = flow
    _spawn_codex_oauth_worker(flow_id)
    return _public_start_payload(flow_id, flow)


def poll_onboarding_oauth_flow(flow_id: str) -> dict[str, Any]:
    _cleanup_oauth_flows()
    fid = str(flow_id or "").strip()
    if not fid:
        raise ValueError("flow_id is required")
    with _OAUTH_FLOWS_LOCK:
        flow = _OAUTH_FLOWS.get(fid)
        if not flow:
            raise KeyError("OAuth flow not found")
        if flow.get("status") == "pending" and float(flow.get("expires_at") or 0) <= time.time():
            flow["status"] = "expired"
            flow["updated_at"] = time.time()
            _drop_sensitive_flow_fields(flow)
        return _public_status_payload(fid, dict(flow))


def cancel_onboarding_oauth_flow(body: dict[str, Any] | None) -> dict[str, Any]:
    fid = str((body or {}).get("flow_id") or "").strip()
    if not fid:
        raise ValueError("flow_id is required")
    requested_provider = _normalize_onboarding_oauth_provider(str((body or {}).get("provider") or ""))
    if requested_provider not in {"openai-codex", "anthropic"}:
        requested_provider = "openai-codex"
    with _OAUTH_FLOWS_LOCK:
        flow = _OAUTH_FLOWS.get(fid)
        if not flow:
            return {"ok": True, "provider": requested_provider, "flow_id": fid, "status": "cancelled"}
        if flow.get("status") == "pending":
            flow["status"] = "cancelled"
            flow["updated_at"] = time.time()
            _drop_sensitive_flow_fields(flow)
        result = _public_status_payload(fid, dict(flow))
    return result


# Backward-compatible names from the abandoned spike. They intentionally do not
# expose provider device secrets to callers anymore.
def start_codex_device_code():
    return start_onboarding_oauth_flow({"provider": "openai-codex"})


def poll_codex_token(device_code, interval=5):
    yield {"status": "error", "error": "Use /api/onboarding/oauth/poll with flow_id"}
