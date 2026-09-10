"""Recoverable Windows config/env publication under the canonical store lock.

Only called inside provider_credentials.credential_transaction. Cooperating
readers recover the intent before observing either file. This is not a claim
that two filesystem renames are one atomic operation.
"""
from __future__ import annotations

import hashlib
import ctypes
import json
import os
import re
import stat
import uuid
from pathlib import Path

from agent.provider_credentials import WINDOWS_CREDENTIAL_INTENT_NAME

_INTENT = WINDOWS_CREDENTIAL_INTENT_NAME
_SCHEMA = "taiji-windows-credential-intent/v1"


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _safe(path, *, directory=False):
    metadata = path.lstat()
    if (getattr(metadata, "st_file_attributes", 0) & 0x400
            or stat.S_ISLNK(metadata.st_mode)
            or not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))
            or (not directory and metadata.st_nlink != 1)):
        raise ValueError("unsafe Windows credential path")
    return metadata


def _read(path, limit):
    try:
        before = _safe(path)
    except FileNotFoundError:
        return None
    if before.st_size > limit:
        raise ValueError("Windows credential file exceeds maximum size")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Windows credential file changed during read")
        payload = stream.read(limit + 1)
    after = _safe(path)
    if len(payload) > limit or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError("Windows credential file changed during read")
    return payload


def _context(store):
    spec = getattr(store._CREDENTIAL_TRANSACTION_STATE, "spec", None)
    if spec is None:
        raise store.CredentialRecoveryError("Windows credential transaction requires a lock")
    store._assert_active_credential_targets_unchanged()
    # Windows does not support alias targets for this backend. In particular,
    # never follow a junction/symlink and then bless its resolved destination.
    for path in (spec.logical_config_path, spec.env_path):
        for parent in path.parents:
            _safe(parent, directory=True)
        if path.exists() or path.is_symlink():
            _safe(path)
    if spec.logical_config_path != spec.config_target or spec.env_path != spec.env_target:
        raise store.CredentialRecoveryError("Windows credential aliases are unsupported")
    return spec


def _write_new(path, payload):
    fd = _open_private_new(path)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _replace(source, target):
    if os.name != "nt":  # Portable fault-injection harness; production uses Win32.
        os.replace(source, target)
        return
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    move = kernel.MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    if not move(str(source), str(target), 0x1 | 0x8):  # REPLACE_EXISTING | WRITE_THROUGH
        raise ctypes.WinError(ctypes.get_last_error())


def _open_private_new(path):
    """Create an empty stage with a protected owner/SYSTEM DACL before writing."""
    if os.name != "nt":
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    import msvcrt
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p),
                    ("inherit", wintypes.BOOL)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.POINTER(SecurityAttributes), wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    # OW is the owner-rights SID. Protected DACL prevents inherited broad read
    # access. Same-volume rename preserves this descriptor on config and .env.
    if not convert("D:P(A;;FA;;;OW)(A;;FA;;;SY)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        handle = create(str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
    finally:
        kernel.LocalFree(descriptor)


def _limit(store, name):
    return store._MAX_CREDENTIAL_CONFIG_BYTES if name == "config" else store._MAX_CREDENTIAL_ENV_BYTES


def recover(store):
    """Roll forward a validated intent, or leave it intact on conflict."""
    try:
        spec = _context(store)
        root = spec.config_target.parent
        # Do not reinterpret a POSIX recovery record as a Windows transaction.
        for name in (store._CREDENTIAL_JOURNAL_NAME, store._CREDENTIAL_ABORT_JOURNAL_NAME):
            if (root / name).exists():
                raise ValueError("foreign credential recovery record")
        journal = root / _INTENT
        raw = _read(journal, store._MAX_CREDENTIAL_JOURNAL_BYTES)
        if raw is None:
            return "not_needed"
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(manifest, dict) or set(manifest) != {"schema", "id", "targets", "env_keys"}:
            raise ValueError("invalid Windows credential intent")
        token = manifest["id"]
        if manifest["schema"] != _SCHEMA or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("invalid Windows credential intent identity")
        keys = manifest["env_keys"]
        if not isinstance(keys, list) or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in keys):
            raise ValueError("invalid Windows credential projection")
        targets = manifest["targets"]
        if not isinstance(targets, list) or len(targets) > 2:
            raise ValueError("invalid Windows credential targets")
        rows = []
        names = set()
        for row in targets:
            if not isinstance(row, dict) or set(row) != {"name", "before", "after", "stage", "target"}:
                raise ValueError("invalid Windows credential target")
            name = row["name"]
            if name not in {"config", "env"} or name in names:
                raise ValueError("duplicate Windows credential target")
            names.add(name)
            target = spec.config_target if name == "config" else spec.env_target
            stage = root / f".taiji-credential-win-{name}-{token}.stage"
            if row["target"] != str(target) or row["stage"] != stage.name:
                raise ValueError("Windows credential intent target mismatch")
            for field in ("before", "after"):
                value = row[field]
                if field == "before" and value is None:
                    continue
                if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                    raise ValueError("invalid Windows credential digest")
            current = _read(target, _limit(store, name))
            current_hash = None if current is None else _digest(current)
            if current_hash not in {row["before"], row["after"]}:
                raise ValueError("Windows credential target changed outside transaction")
            payload = _read(stage, _limit(store, name))
            if current_hash != row["after"] and (payload is None or _digest(payload) != row["after"]):
                raise ValueError("Windows credential recovery stage missing or changed")
            rows.append((row, target, stage))
        # Validate the whole pair before changing any target.
        for row, target, stage in rows:
            _context(store)
            current = _read(target, _limit(store, row["name"]))
            digest = None if current is None else _digest(current)
            if digest == row["after"]:
                continue
            if digest != row["before"]:
                raise ValueError("Windows credential target changed before publish")
            payload = _read(stage, _limit(store, row["name"]))
            if payload is None or _digest(payload) != row["after"]:
                raise ValueError("Windows credential stage changed before publish")
            _replace(stage, target)
        for row, target, _stage in rows:
            payload = _read(target, _limit(store, row["name"]))
            if payload is None or _digest(payload) != row["after"]:
                raise ValueError("Windows credential publication verification failed")
        store._sync_recovered_process_env(spec.env_target, keys)
        # Failure to remove a committed journal is safe: the next lock holder
        # verifies and replays the already-published values before any new write.
        try:
            for _row, _target, stage in rows:
                stage.unlink(missing_ok=True)
            journal.unlink()
        except OSError:
            pass
        return "recovered"
    except (OSError, ValueError, TypeError) as exc:
        raise store.WindowsCredentialRecoveryError("本机凭据保存结果待恢复，请解除文件占用后刷新状态；仍失败时请保留配置并联系支持。") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Windows credential intent field")
        result[key] = value
    return result


def commit(store, target_specs, env_keys):
    """Publish intent before either target; exceptions keep recovery evidence."""
    prepared = []
    published = False
    journal_stage = None
    try:
        spec = _context(store)
        root = spec.config_target.parent
        journal = root / _INTENT
        if journal.exists():
            raise ValueError("previous Windows credential intent still pending")
        token = uuid.uuid4().hex
        for target_spec in target_specs:
            name = target_spec["name"]
            target = spec.config_target if name == "config" else spec.env_target
            if Path(target_spec["logical_path"]) != target:
                raise ValueError("Windows credential commit target mismatch")
            before = _read(target, _limit(store, name))
            if (before is not None) != target_spec["before_exists"] or (before or b"") != target_spec["before_payload"]:
                raise store._CredentialCompareAndSwapError("Windows credential target changed before staging")
            payload = target_spec["target_payload"]
            if len(payload) > _limit(store, name):
                raise ValueError("Windows credential payload exceeds maximum size")
            stage = root / f".taiji-credential-win-{name}-{token}.stage"
            _write_new(stage, payload)
            prepared.append({"name": name, "target": str(target), "stage": stage.name,
                             "before": None if before is None else _digest(before), "after": _digest(payload)})
        manifest = {"schema": _SCHEMA, "id": token, "targets": prepared, "env_keys": env_keys}
        journal_stage = root / f".taiji-credential-win-intent-{token}.stage"
        _write_new(journal_stage, json.dumps(manifest, ensure_ascii=True).encode("utf-8"))
        _replace(journal_stage, journal)
        published = True
        recover(store)
    except (OSError, ValueError) as exc:
        raise store.WindowsCredentialStorageError("本机凭据未能提交，请检查配置文件占用及写入权限后重试。") from exc
    finally:
        if not published:
            for row in prepared:
                (root / row["stage"]).unlink(missing_ok=True)
            if journal_stage is not None:
                journal_stage.unlink(missing_ok=True)
