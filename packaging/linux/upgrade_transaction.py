#!/usr/bin/env python3
"""Small, fail-closed transaction layer for Taiji Linux package upgrades.

This module deliberately uses only the Python standard library.  It is used by
the management plane and by offline rehearsal tests; the DEB maintainer
scripts do not resolve a user's HOME or run package-manager commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


MODULE_DIR = Path(__file__).resolve().parent
CONTRACT_PATH = MODULE_DIR / "upgrade-data-contract.json"
CONTRACT_SCHEMA = "taiji-linux-upgrade-data-contract/v1"
SNAPSHOT_SCHEMA = "taiji-linux-upgrade-snapshot/v1"
JOURNAL_SCHEMA = "taiji-linux-upgrade-journal/v1"

STATES = (
    "preflight",
    "trusted_staging",
    "stopped",
    "snapshotted",
    "package_changed",
    "migrated",
    "verified",
    "committed",
)
FAILURE_STATES = ("rolling_back", "rolled_back", "manual_recovery_required")
TERMINAL_STATES = {"committed", "rolled_back", "manual_recovery_required"}

_NEXT_STATES = {
    "preflight": {"trusted_staging", "rolling_back"},
    "trusted_staging": {"stopped", "rolling_back"},
    "stopped": {"snapshotted", "rolling_back"},
    "snapshotted": {"package_changed", "rolling_back"},
    "package_changed": {"migrated", "rolling_back"},
    "migrated": {"verified", "rolling_back"},
    "verified": {"committed", "rolling_back"},
    "committed": {"rolling_back"},
    "rolling_back": {"rolled_back", "manual_recovery_required"},
    "rolled_back": set(),
    "manual_recovery_required": set(),
}


class UpgradeError(RuntimeError):
    """Base class for deterministic, user-safe transaction failures."""


class UnsafeDataError(UpgradeError):
    """Input data violates the ownership/path safety contract."""


class InvalidTransition(UpgradeError):
    """A journal transition is not legal for the current state."""


class PreviousPackageError(UpgradeError):
    """The N-1 package required for a reversible upgrade is not trusted."""


class ManualRecoveryRequired(UpgradeError):
    """Automatic rollback could not close the recovery contract."""


@dataclass(frozen=True)
class AccountIdentity:
    """Canonical account identity obtained from an explicit account record.

    ``verified`` is intentionally explicit.  Callers that accept a login must
    use :func:`resolve_account` (which invokes ``getent passwd``); tests and
    an already persisted installation record may construct this value only
    after independently verifying its fields.
    """

    username: str
    uid: int
    gid: int
    home: Path
    verified: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "home", Path(self.home))

    @property
    def config_dir(self) -> Path:
        return self.home / ".config" / "taiji-agent"

    @property
    def data_dir(self) -> Path:
        return self.home / ".local" / "share" / "taiji-agent"

    @property
    def state_dir(self) -> Path:
        return self.home / ".local" / "state" / "taiji-agent"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpgradeError("invalid_json") from exc
    if not isinstance(value, dict):
        raise UpgradeError("json_object_required")
    return value


def load_contract(path: Path | str | None = None) -> dict[str, Any]:
    contract = _read_json(Path(path) if path is not None else CONTRACT_PATH)
    validate_contract(contract)
    return contract


def validate_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise UpgradeError("unsupported_contract_schema")
    if contract.get("package") != "taiji-agent" or contract.get("architecture") != "amd64":
        raise UpgradeError("unsupported_contract_target")
    roots = contract.get("user_roots")
    artifacts = contract.get("artifacts")
    if not isinstance(roots, list) or not isinstance(artifacts, list):
        raise UpgradeError("contract_roots_required")
    root_ids = {item.get("id") for item in roots if isinstance(item, Mapping)}
    if not {"config", "data", "state"}.issubset(root_ids):
        raise UpgradeError("canonical_user_roots_required")
    categories = {
        item.get("category") for item in artifacts if isinstance(item, Mapping)
    }
    required = {
        "config",
        "license",
        "device_identity",
        "anti_rollback",
        "sessions",
        "attachments",
        "workspace",
        "skills",
        "templates",
    }
    if not required.issubset(categories):
        raise UpgradeError("required_data_categories_missing")
    sqlite_spec = contract.get("sqlite")
    if not isinstance(sqlite_spec, Mapping) or sqlite_spec.get("backup_api") != "sqlite3.Connection.backup":
        raise UpgradeError("sqlite_backup_api_required")
    state_machine = contract.get("state_machine")
    if not isinstance(state_machine, Mapping):
        raise UpgradeError("state_machine_required")
    if tuple(state_machine.get("states", ())) != STATES:
        raise UpgradeError("state_machine_order_invalid")
    if tuple(state_machine.get("failure_states", ())) != FAILURE_STATES:
        raise UpgradeError("failure_states_invalid")
    if contract.get("operations") != ["fresh_install", "reinstall", "upgrade", "rollback"]:
        raise UpgradeError("operations_contract_invalid")
    if contract.get("reinstall") != {"replace_user_data": False}:
        raise UpgradeError("reinstall_data_contract_invalid")
    if contract.get("upgrade") != {
        "requires_previous_deb": True,
        "requires_previous_sha256": True,
        "requires_backward_compatible_contract": True,
        "recovery_failure": "manual_recovery_required",
    }:
        raise UpgradeError("upgrade_requirements_invalid")
    if contract.get("security") != {
        "account_source": "getent passwd",
        "reject_symlinks": True,
        "reject_mountpoints": True,
        "reject_hardlinks": True,
        "require_owner_account": True,
        "journal_mode": "0600",
        "private_directory_mode": "0700",
    }:
        raise UpgradeError("security_contract_invalid")


def _parse_getent(line: str, username: str) -> AccountIdentity:
    fields = line.rstrip("\n").split(":")
    if len(fields) < 7 or fields[0] != username:
        raise UnsafeDataError("account_record_mismatch")
    try:
        uid = int(fields[2])
        gid = int(fields[3])
    except ValueError as exc:
        raise UnsafeDataError("account_record_invalid") from exc
    return AccountIdentity(username, uid, gid, Path(fields[5]), verified=True)


def resolve_account(username: str, *, getent: str = "getent") -> AccountIdentity:
    """Resolve one explicit login through the system account database."""

    if not username or username in {"root", ".", ".."} or "/" in username:
        raise UnsafeDataError("account_name_invalid")
    try:
        completed = subprocess.run(
            [getent, "passwd", username],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError) as exc:
        raise UnsafeDataError("getent_unavailable") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise UnsafeDataError("unknown_account")
    account = _parse_getent(completed.stdout.splitlines()[0], username)
    validate_account(account)
    return account


def validate_account(account: AccountIdentity) -> AccountIdentity:
    if not isinstance(account, AccountIdentity) or not account.verified:
        raise UnsafeDataError("account_not_verified")
    if not account.username or account.username in {"root", ".", ".."}:
        raise UnsafeDataError("account_name_invalid")
    if account.uid <= 0 or account.gid < 0:
        raise UnsafeDataError("account_id_invalid")
    home = account.home
    if not home.is_absolute() or home.is_symlink() or not home.is_dir():
        raise UnsafeDataError("account_home_invalid")
    _reject_symlink_ancestors(home, stop_at=home)
    home_stat = os.lstat(home)
    if home_stat.st_uid != account.uid or home_stat.st_gid != account.gid:
        raise UnsafeDataError("account_home_owner_mismatch")
    if os.path.ismount(home):
        raise UnsafeDataError("account_home_mountpoint")
    return account


def _lstat_safe(path: Path, *, account: AccountIdentity | None = None, allow_missing: bool = False) -> os.stat_result | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise UnsafeDataError("path_missing")
    except OSError as exc:
        raise UnsafeDataError("path_unreadable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise UnsafeDataError("symlink_not_allowed")
    # Directories normally have link count 2 (the ``.`` entry).  Only a
    # regular file with extra links is ambiguous and therefore rejected.
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise UnsafeDataError("hardlink_not_allowed")
    if account is not None and (info.st_uid != account.uid or info.st_gid != account.gid):
        raise UnsafeDataError("owner_mismatch")
    return info


def ensure_not_mountpoint(path: Path | str, *, stop_at: Path | str | None = None) -> None:
    path = Path(path)
    boundary = Path(stop_at) if stop_at is not None else None
    current = path
    while True:
        if boundary is not None and current == boundary:
            break
        if current.exists() and os.path.ismount(current):
            raise UnsafeDataError("mountpoint_not_allowed")
        if current == current.parent:
            break
        current = current.parent


def _validate_tree(path: Path, account: AccountIdentity) -> None:
    # ``Path.exists()`` is false for a broken symlink.  Check the directory
    # entry itself first so a canonical XDG root can never be silently treated
    # as absent after an attacker replaces it with a dangling link.
    _reject_symlink_ancestors(path, stop_at=account.home)
    if path.is_symlink():
        raise UnsafeDataError("symlink_not_allowed")
    if not path.exists():
        return
    info = _lstat_safe(path, account=account)
    if info is None:
        return
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeDataError("directory_required")
    ensure_not_mountpoint(path, stop_at=account.home)
    for entry in sorted(path.iterdir(), key=lambda item: item.name):
        entry_info = _lstat_safe(entry, account=account)
        if stat.S_ISDIR(entry_info.st_mode):
            _validate_tree(entry, account)
        elif not stat.S_ISREG(entry_info.st_mode):
            raise UnsafeDataError("unsupported_data_type")


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise UpgradeError("directory_fsync_failed") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise UpgradeError("directory_fsync_failed") from exc
    finally:
        os.close(fd)


def _reject_symlink_ancestors(path: Path, *, stop_at: Path | None = None) -> None:
    """Reject a private transaction path that crosses a symlinked parent."""

    current = path
    missing: list[Path] = []
    while not current.exists() and not current.is_symlink() and current != current.parent:
        missing.append(current)
        current = current.parent
    while True:
        if stop_at is not None and current == stop_at:
            break
        if current.is_symlink():
            link_info = os.lstat(current)
            if stop_at is not None:
                raise UnsafeDataError("symlink_ancestor_not_allowed")
            try:
                target = current.resolve(strict=True)
                target_info = os.lstat(target)
            except OSError as exc:
                raise UnsafeDataError("symlink_ancestor_not_allowed") from exc
            # Distribution images occasionally expose /var or /tmp through a
            # root-owned compatibility symlink.  That system indirection is
            # safe; a user-owned link is never accepted as an account boundary.
            if link_info.st_uid != 0 or target_info.st_uid != 0:
                raise UnsafeDataError("symlink_ancestor_not_allowed")
        if current == current.parent:
            break
        current = current.parent


def _ensure_private_directory(path: Path) -> None:
    _reject_symlink_ancestors(path)
    missing: list[Path] = []
    current = path
    while not current.exists() and not current.is_symlink() and current != current.parent:
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    if path.exists() or path.is_symlink():
        _validate_private_directory(path)
    os.chmod(path, 0o700)


def _validate_private_directory(path: Path) -> os.stat_result:
    """Require a journal/backup directory to be private and owned by caller."""

    _reject_symlink_ancestors(path)
    info = _lstat_safe(path)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise UnsafeDataError("private_directory_invalid")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise UnsafeDataError("private_directory_mode_invalid")
    if info.st_uid != os.geteuid() or info.st_gid != os.getegid():
        raise UnsafeDataError("private_directory_owner_invalid")
    ensure_not_mountpoint(path, stop_at=Path(path.anchor))
    return info


def _validate_private_file(path: Path, *, mode: int = 0o600) -> os.stat_result:
    info = _lstat_safe(path)
    if info is None or not stat.S_ISREG(info.st_mode):
        raise UnsafeDataError("private_file_invalid")
    if stat.S_IMODE(info.st_mode) != mode:
        raise UnsafeDataError("private_file_mode_invalid")
    if info.st_uid != os.geteuid() or info.st_gid != os.getegid():
        raise UnsafeDataError("private_file_owner_invalid")
    return info


def _atomic_write_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    _ensure_private_directory(path.parent)
    if path.exists() or path.is_symlink():
        existing = _lstat_safe(path)
        if existing is None or not stat.S_ISREG(existing.st_mode):
            raise UnsafeDataError("atomic_destination_invalid")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, mode)
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_category(relative: str) -> str:
    if relative.startswith(".config/taiji-agent/licenses/") or relative in {
        ".config/taiji-agent/licenses/active-license.jwt",
    }:
        return "license"
    if relative == ".config/taiji-agent/license-device.json":
        return "device_identity"
    if relative == ".local/state/taiji-agent/license-state.json":
        return "license"
    if relative == ".local/state/taiji-agent/anti-rollback.json":
        return "anti_rollback"
    for category in ("sessions", "attachments", "workspace", "skills"):
        if relative.startswith(f".local/share/taiji-agent/{category}/"):
            return category
    if relative.startswith(".local/share/taiji-agent/docx-engine-v2/installed/"):
        return "templates"
    if relative.endswith((".db", ".sqlite", ".sqlite3")):
        return "databases"
    if relative.startswith(".config/taiji-agent/"):
        return "config"
    return "state"


def _iter_files(account: AccountIdentity) -> list[tuple[Path, str, str]]:
    roots = (account.config_dir, account.data_dir, account.state_dir)
    for root in roots:
        _validate_tree(root, account)
    found: list[tuple[Path, str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            info = _lstat_safe(path, account=account)
            if info is None:
                continue
            if stat.S_ISDIR(info.st_mode):
                ensure_not_mountpoint(path, stop_at=account.home)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise UnsafeDataError("unsupported_data_type")
            relative = path.relative_to(account.home).as_posix()
            found.append((path, relative, _relative_category(relative)))
    return found


def _copy_regular(source: Path, destination: Path, account: AccountIdentity) -> None:
    source_fd = _open_verified_source_fd(source, account=account)
    try:
        _copy_fd_to_destination(source_fd, destination)
    finally:
        os.close(source_fd)


def _open_verified_source_fd(source: Path, *, account: AccountIdentity | None = None) -> int:
    """Open a regular source without following a final symlink and bind its inode."""

    info = _lstat_safe(source, account=account)
    if info is None or not stat.S_ISREG(info.st_mode):
        raise UnsafeDataError("regular_file_required")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise UnsafeDataError("source_open_failed") from exc
    try:
        bound = os.fstat(fd)
        if (
            bound.st_dev != info.st_dev
            or bound.st_ino != info.st_ino
            or bound.st_uid != info.st_uid
            or bound.st_gid != info.st_gid
            or bound.st_nlink != info.st_nlink
            or not stat.S_ISREG(bound.st_mode)
        ):
            raise UnsafeDataError("source_changed")
        if account is not None and (bound.st_uid != account.uid or bound.st_gid != account.gid):
            raise UnsafeDataError("owner_mismatch")
        return fd
    except Exception:
        os.close(fd)
        raise


def _copy_fd_to_destination(
    source_fd: int,
    destination: Path,
    *,
    mode: int = 0o600,
    private_parent: bool = True,
) -> None:
    if private_parent:
        _ensure_private_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        _lstat_safe(destination)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, mode)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise UnsafeDataError("destination_write_failed")
                view = view[written:]
        os.fsync(fd)
        os.close(fd)
        os.replace(temporary_path, destination)
        os.chmod(destination, mode)
        _fsync_directory(destination.parent)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def sqlite_backup(
    source: Path | str,
    destination: Path | str,
    *,
    account: AccountIdentity | None = None,
) -> None:
    """Make a consistent SQLite copy using the SQLite backup API.

    Copying a ``.db`` byte stream is unsafe when a WAL is active.  The source
    is opened read-only and ``Connection.backup`` copies logical pages into a
    temporary destination before it is atomically installed.
    """

    source = Path(source)
    destination = Path(destination)
    source_fd = _open_verified_source_fd(source, account=account)
    if destination.exists() or destination.is_symlink():
        _lstat_safe(destination)
    _ensure_private_directory(destination.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        fd_bound_path = Path(f"/proc/self/fd/{source_fd}")
        sqlite_source = fd_bound_path if fd_bound_path.exists() else source
        with closing(sqlite3.connect(f"file:{sqlite_source}?mode=ro", uri=True)) as source_connection:
            with closing(sqlite3.connect(temporary_path)) as destination_connection:
                _sqlite_backup_call(source_connection, destination_connection)
                destination_connection.commit()
                destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                destination_connection.commit()
        current_info = os.lstat(source)
        bound_info = os.fstat(source_fd)
        if current_info.st_dev != bound_info.st_dev or current_info.st_ino != bound_info.st_ino:
            raise UnsafeDataError("sqlite_source_changed")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination)
        _fsync_directory(destination.parent)
    except sqlite3.Error as exc:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise UpgradeError("sqlite_backup_failed") from exc
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(source_fd)


def _sqlite_backup_call(source_connection: sqlite3.Connection, destination_connection: sqlite3.Connection) -> None:
    """One narrow seam for tests while retaining the native backup API call."""

    source_connection.backup(destination_connection)


def _copy_file_for_snapshot(source: Path, destination: Path, account: AccountIdentity) -> None:
    suffix = source.suffix.lower()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        sqlite_backup(source, destination, account=account)
    else:
        _copy_regular(source, destination, account)


def _ensure_account_directory(path: Path, account: AccountIdentity) -> None:
    """Create missing restore parents without leaving root-owned user dirs."""

    home = account.home
    try:
        relative = path.relative_to(home)
    except ValueError as exc:
        raise UnsafeDataError("restore_path_outside_home") from exc
    current = home
    for component in relative.parts:
        current = current / component
        info = _lstat_safe(current, allow_missing=True)
        if info is None:
            ensure_not_mountpoint(current.parent, stop_at=home)
            current.mkdir(mode=0o700)
            try:
                os.chown(current, account.uid, account.gid)
            except OSError as exc:
                raise UnsafeDataError("restore_owner_failed") from exc
            os.chmod(current, 0o700)
        elif not stat.S_ISDIR(info.st_mode):
            raise UnsafeDataError("restore_parent_not_directory")


class UpgradeTransaction:
    def __init__(
        self,
        transaction_dir: Path,
        backup_dir: Path,
        account: AccountIdentity,
        *,
        operation: str = "upgrade",
        transaction_id: str,
        journal: dict[str, Any],
    ) -> None:
        self.transaction_dir = transaction_dir
        self.backup_dir = backup_dir
        self.journal_path = transaction_dir / "journal.json"
        self.account = validate_account(account)
        self.operation = operation
        self.transaction_id = transaction_id
        self._journal = journal

    @property
    def state(self) -> str:
        return str(self._journal["state"])

    @classmethod
    def create(
        cls,
        upgrades_root: Path | str,
        *,
        account: AccountIdentity,
        operation: str = "upgrade",
        transaction_id: str | None = None,
    ) -> "UpgradeTransaction":
        validate_account(account)
        if operation not in {"fresh_install", "reinstall", "upgrade", "rollback"}:
            raise UpgradeError("operation_invalid")
        root = Path(upgrades_root)
        transaction_id = transaction_id or f"txn-{int(time.time())}-{os.getpid()}"
        if not transaction_id.replace("-", "").replace("_", "").isalnum():
            raise UpgradeError("transaction_id_invalid")
        if root.exists() or root.is_symlink():
            _validate_private_directory(root)
        else:
            _ensure_private_directory(root)
        backup_root = root.parent / "backups"
        if backup_root.exists() or backup_root.is_symlink():
            _validate_private_directory(backup_root)
        else:
            _ensure_private_directory(backup_root)
        transaction_dir = root / transaction_id
        backup_dir = backup_root / transaction_id
        transaction_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        os.chmod(transaction_dir, 0o700)
        os.chmod(backup_dir, 0o700)
        journal = {
            "schema": JOURNAL_SCHEMA,
            "transaction_id": transaction_id,
            "operation": operation,
            "state": "preflight",
            "account": {"uid": account.uid, "gid": account.gid},
            "history": [{"state": "preflight", "at": int(time.time())}],
            "backup_relative": f"backups/{transaction_id}",
        }
        _atomic_write_json(transaction_dir / "journal.json", journal)
        return cls(transaction_dir, backup_dir, account, operation=operation, transaction_id=transaction_id, journal=journal)

    @classmethod
    def resume(
        cls,
        journal_path: Path | str,
        account: AccountIdentity | None = None,
    ) -> "UpgradeTransaction":
        journal_path = Path(journal_path)
        journal = _read_json(journal_path)
        if journal.get("schema") != JOURNAL_SCHEMA:
            raise UpgradeError("journal_schema_invalid")
        account_record = journal.get("account")
        if not isinstance(account_record, Mapping):
            raise UpgradeError("journal_account_missing")
        # A resumed journal still needs an explicit account.  The journal stores
        # only uid/gid to avoid making a user-facing artifact contain a login.
        if account is None:
            raise UpgradeError("resume_requires_explicit_account")
        return cls.resume_for_account(journal_path, account)

    @classmethod
    def resume_for_account(cls, journal_path: Path | str, account: AccountIdentity) -> "UpgradeTransaction":
        journal_path = Path(journal_path)
        _validate_private_file(journal_path)
        if journal_path.name != "journal.json":
            raise UnsafeDataError("journal_filename_invalid")
        transaction_dir = journal_path.parent
        _validate_private_directory(transaction_dir.parent)
        _validate_private_directory(transaction_dir)
        journal = _read_json(journal_path)
        if journal.get("schema") != JOURNAL_SCHEMA:
            raise UpgradeError("journal_schema_invalid")
        transaction_id = journal.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id.replace("-", "").replace("_", "").isalnum():
            raise UpgradeError("journal_transaction_id_invalid")
        if transaction_dir.name != transaction_id:
            raise UnsafeDataError("journal_transaction_path_mismatch")
        operation = journal.get("operation")
        if operation not in {"fresh_install", "reinstall", "upgrade", "rollback"}:
            raise UpgradeError("journal_operation_invalid")
        state = journal.get("state")
        if state not in set(STATES) | set(FAILURE_STATES):
            raise UpgradeError("journal_state_invalid")
        history = journal.get("history")
        if (
            not isinstance(history, list)
            or not history
            or not isinstance(history[-1], Mapping)
            or history[-1].get("state") != state
        ):
            raise UpgradeError("journal_history_invalid")
        for entry in history:
            if not isinstance(entry, Mapping) or entry.get("state") not in set(STATES) | set(FAILURE_STATES):
                raise UpgradeError("journal_history_invalid")
        for previous_entry, current_entry in zip(history, history[1:]):
            if current_entry["state"] not in _NEXT_STATES.get(previous_entry["state"], set()):
                raise UpgradeError("journal_history_transition_invalid")
        candidate_package = journal.get("candidate_package")
        previous_package = journal.get("previous_package")
        if operation in {"upgrade", "rollback"}:
            if not isinstance(candidate_package, Mapping) or not isinstance(previous_package, Mapping):
                raise UpgradeError("journal_package_binding_required")
            if candidate_package.get("relative") != "artifacts/candidate.deb" or previous_package.get("relative") != "artifacts/previous.deb":
                raise UnsafeDataError("journal_package_path_invalid")
            if previous_package.get("signature_relative") != "artifacts/previous.deb.sig":
                raise UnsafeDataError("journal_signature_path_invalid")
            artifacts_dir = transaction_dir / "artifacts"
            _validate_private_directory(artifacts_dir)
            for artifact, expected, field in (
                (artifacts_dir / "candidate.deb", candidate_package.get("sha256"), "candidate_sha256"),
                (artifacts_dir / "previous.deb", previous_package.get("sha256"), "previous_sha256"),
                (artifacts_dir / "previous.deb.sig", previous_package.get("signature_sha256"), "signature_sha256"),
            ):
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise UpgradeError(f"journal_{field}_invalid")
                _validate_private_file(artifact)
                if _sha256(artifact) != expected:
                    raise UpgradeError("journal_artifact_hash_mismatch")
        elif candidate_package is not None or previous_package is not None:
            raise UpgradeError("journal_package_binding_unexpected")
        if "snapshot_manifest" in journal and journal["snapshot_manifest"] != f"backups/{transaction_id}/manifest.json":
            raise UnsafeDataError("journal_snapshot_path_invalid")
        if journal.get("account") != {"uid": account.uid, "gid": account.gid}:
            raise UnsafeDataError("journal_account_mismatch")
        backup_relative = journal.get("backup_relative")
        if not isinstance(backup_relative, str):
            raise UpgradeError("journal_backup_missing")
        backup_parts = Path(backup_relative).parts
        if (
            Path(backup_relative).is_absolute()
            or ".." in backup_parts
            or backup_parts[:1] != ("backups",)
            or backup_parts[-1:] != (str(journal["transaction_id"]),)
        ):
            raise UnsafeDataError("journal_backup_path_invalid")
        backup_dir = transaction_dir.parent.parent / backup_relative
        _validate_private_directory(backup_dir.parent)
        _validate_private_directory(backup_dir)
        return cls(
            transaction_dir,
            backup_dir,
            account,
            operation=str(journal.get("operation", "upgrade")),
            transaction_id=str(journal["transaction_id"]),
            journal=journal,
        )

    def _save(self) -> None:
        _validate_private_file(self.journal_path)
        _atomic_write_json(self.journal_path, self._journal)

    def transition(self, state: str, *, details: Mapping[str, Any] | None = None) -> None:
        if state not in _NEXT_STATES.get(self.state, set()):
            raise InvalidTransition(f"{self.state}->{state}")
        self._journal["state"] = state
        self._journal.setdefault("history", []).append({"state": state, "at": int(time.time())})
        if details:
            safe_details = {str(key): value for key, value in details.items() if key in {"reason", "manifest"}}
            if safe_details:
                self._journal.setdefault("details", {}).update(safe_details)
        self._save()

    def snapshot_user_data(self) -> dict[str, Any]:
        validate_account(self.account)
        files = []
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.backup_dir, 0o700)
        for source, relative, category in _iter_files(self.account):
            destination = self.backup_dir / "files" / relative
            _copy_file_for_snapshot(source, destination, self.account)
            source_info = os.lstat(source)
            files.append(
                {
                    "relative": relative,
                    "category": category,
                    "sha256": _sha256(destination),
                    "size": source_info.st_size,
                    "mode": stat.S_IMODE(source_info.st_mode),
                }
            )
        categories = sorted({item["category"] for item in files})
        manifest = {
            "schema": SNAPSHOT_SCHEMA,
            "transaction_id": self.transaction_id,
            "categories": categories,
            "files": files,
            "account": {"uid": self.account.uid, "gid": self.account.gid},
        }
        _atomic_write_json(self.backup_dir / "manifest.json", manifest)
        self._journal["snapshot_manifest"] = f"backups/{self.transaction_id}/manifest.json"
        if self.state == "stopped":
            self.transition("snapshotted", details={"manifest": "manifest.json"})
        return manifest

    def prepare_before_package_change(self) -> dict[str, Any]:
        """Create the durable N-1 snapshot immediately before dpkg mutation."""

        if self.state == "preflight":
            self.transition("trusted_staging")
        if self.state == "trusted_staging":
            self.transition("stopped")
        if self.state == "stopped":
            return self.snapshot_user_data()
        if self.state == "snapshotted":
            return _read_json(self.backup_dir / "manifest.json")
        raise InvalidTransition(f"prepare:{self.state}")

    def commit_after_package_change(self) -> None:
        """Close the journal after dpkg/postinst and native verification pass."""

        if self.state == "snapshotted":
            self.transition("package_changed")
        if self.state == "package_changed":
            self.transition("migrated")
        if self.state == "migrated":
            self.transition("verified")
        if self.state == "verified":
            self.transition("committed")
        if self.state != "committed":
            raise InvalidTransition(f"commit:{self.state}")

    def restore_snapshot(self) -> None:
        manifest_path = self.backup_dir / "manifest.json"
        _validate_private_file(manifest_path)
        manifest = _read_json(manifest_path)
        if manifest.get("schema") != SNAPSHOT_SCHEMA:
            raise UpgradeError("snapshot_schema_invalid")
        if manifest.get("account") != {"uid": self.account.uid, "gid": self.account.gid}:
            raise UnsafeDataError("snapshot_account_mismatch")
        expected = {
            item.get("relative")
            for item in manifest.get("files", [])
            if isinstance(item, Mapping) and isinstance(item.get("relative"), str)
        }
        # A failed migration must not leave package-created files behind.
        # The walk is bounded to the three canonical XDG roots and is
        # fail-closed for symlinks, hardlinks, mounts, or owner changes.
        for current, relative, _category in _iter_files(self.account):
            if relative not in expected:
                _lstat_safe(current, account=self.account)
                current.unlink()
        for item in manifest.get("files", []):
            relative = item.get("relative")
            if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
                raise UnsafeDataError("snapshot_relative_path_invalid")
            source = self.backup_dir / "files" / relative
            destination = self.account.home / relative
            _lstat_safe(source)
            if destination.exists() or destination.is_symlink():
                destination_info = _lstat_safe(destination, account=self.account)
                if destination_info is not None and stat.S_ISDIR(destination_info.st_mode):
                    raise UnsafeDataError("restore_file_over_directory")
            _ensure_account_directory(destination.parent, self.account)
            source_fd = _open_verified_source_fd(source)
            try:
                _copy_fd_to_destination(
                    source_fd,
                    destination,
                    mode=int(item.get("mode", 0o600)) & 0o777,
                    private_parent=False,
                )
            finally:
                os.close(source_fd)
            os.chmod(destination, int(item.get("mode", 0o600)) & 0o777)
            info = os.lstat(destination)
            if info.st_uid != self.account.uid or info.st_gid != self.account.gid:
                try:
                    os.chown(destination, self.account.uid, self.account.gid)
                except OSError as exc:
                    raise UnsafeDataError("restore_owner_failed") from exc
            if _sha256(destination) != item.get("sha256"):
                raise UpgradeError("restore_hash_mismatch")
        _fsync_directory(self.account.home)

    def _preflight_previous(self, previous_deb: Path, previous_sha256: str | None, previous_signature: Path | None) -> None:
        info = _lstat_safe(previous_deb)
        if info is None or not stat.S_ISREG(info.st_mode):
            raise PreviousPackageError("previous_deb_invalid")
        if not isinstance(previous_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_sha256):
            raise PreviousPackageError("previous_deb_sha256_required")
        if _sha256(previous_deb) != previous_sha256.lower():
            raise PreviousPackageError("previous_deb_hash_mismatch")
        if previous_signature is None:
            raise PreviousPackageError("previous_signature_required")
        signature_info = _lstat_safe(previous_signature)
        if signature_info is None or not stat.S_ISREG(signature_info.st_mode):
            raise PreviousPackageError("previous_signature_invalid")
        load_contract()

    def bind_package_artifacts(
        self,
        *,
        candidate_deb: Path | str,
        previous_deb: Path | str,
        previous_sha256: str,
        previous_signature: Path | str,
    ) -> None:
        """Persist candidate/N-1 identities before any service stop or dpkg mutation."""

        candidate_deb = Path(candidate_deb)
        previous_deb = Path(previous_deb)
        previous_signature = Path(previous_signature)
        candidate_info = _lstat_safe(candidate_deb)
        if candidate_info is None or not stat.S_ISREG(candidate_info.st_mode):
            raise UpgradeError("candidate_deb_invalid")
        self._preflight_previous(previous_deb, previous_sha256, previous_signature)
        artifact_dir = self.transaction_dir / "artifacts"
        _ensure_private_directory(artifact_dir)
        artifact_paths = {
            "candidate": (candidate_deb, artifact_dir / "candidate.deb"),
            "previous": (previous_deb, artifact_dir / "previous.deb"),
            "signature": (previous_signature, artifact_dir / "previous.deb.sig"),
        }
        for source, destination in artifact_paths.values():
            source_fd = _open_verified_source_fd(source)
            try:
                _copy_fd_to_destination(source_fd, destination)
            finally:
                os.close(source_fd)
        self._journal["candidate_package"] = {
            "relative": "artifacts/candidate.deb",
            "basename": candidate_deb.name,
            "sha256": _sha256(artifact_dir / "candidate.deb"),
        }
        self._journal["previous_package"] = {
            "relative": "artifacts/previous.deb",
            "basename": previous_deb.name,
            "sha256": previous_sha256,
            "signature_sha256": _sha256(artifact_dir / "previous.deb.sig"),
            "signature_relative": "artifacts/previous.deb.sig",
            "signature_basename": previous_signature.name,
        }
        self._save()

    def run_upgrade(
        self,
        *,
        candidate_deb: Path | str,
        previous_deb: Path | str,
        previous_sha256: str | None = None,
        previous_signature: Path | str | None = None,
        stop_fn: Callable[[], Any] | None = None,
        install_fn: Callable[[Path], Any] | None = None,
        migrate_fn: Callable[[], Any] | None = None,
        verify_fn: Callable[[], Any] | None = None,
        rollback_install_fn: Callable[[Path], Any] | None = None,
    ) -> dict[str, Any]:
        candidate_deb = Path(candidate_deb)
        previous_deb = Path(previous_deb)
        previous_signature_path = Path(previous_signature) if previous_signature is not None else None
        try:
            self.bind_package_artifacts(
                candidate_deb=candidate_deb,
                previous_deb=previous_deb,
                previous_sha256=previous_sha256 or "",
                previous_signature=previous_signature_path or Path(""),
            )
            candidate_artifact = self.transaction_dir / "artifacts" / "candidate.deb"
            if install_fn is None:
                raise UpgradeError("candidate_install_required")
            self.transition("trusted_staging")
            if stop_fn is not None:
                if stop_fn() is False:
                    raise UpgradeError("stop_failed")
            self.transition("stopped")
            self.snapshot_user_data()
            # Enter the mutation state before invoking the package callback.
            # A callback may partially install a package and then return False
            # or raise; recovery must therefore require an explicit rollback
            # callback rather than treating ``snapshotted`` as untouched.
            self.transition("package_changed")
            if install_fn is not None and install_fn(candidate_artifact) is False:
                raise UpgradeError("candidate_install_failed")
            if migrate_fn is not None:
                migrate_fn()
            self.transition("migrated")
            if verify_fn is not None and verify_fn() is False:
                raise UpgradeError("postinst_verify_failed")
            self.transition("verified")
            self.transition("committed")
            return {"result": "upgraded", "state": self.state, "transaction_id": self.transaction_id}
        except Exception as error:
            if self.state in {"preflight", "trusted_staging"}:
                return {"result": "blocked", "state": self.state, "error_code": _error_code(error)}
            return self._recover(
                self.transaction_dir / "artifacts" / "previous.deb",
                rollback_install_fn=rollback_install_fn,
                error=error,
                package_restored=self.state in {"trusted_staging", "stopped", "snapshotted"},
            )

    def _recover(
        self,
        previous_deb: Path,
        *,
        rollback_install_fn: Callable[[Path], Any] | None,
        error: Exception,
        package_restored: bool = False,
    ) -> dict[str, Any]:
        try:
            if self.state != "rolling_back":
                self.transition("rolling_back", details={"reason": _error_code(error)})
            if self.operation in {"upgrade", "rollback"} and not package_restored and rollback_install_fn is None:
                raise ManualRecoveryRequired("rollback_package_required")
            if not package_restored and rollback_install_fn is not None and rollback_install_fn(previous_deb) is False:
                raise ManualRecoveryRequired("rollback_package_failed")
            self.restore_snapshot()
            self.transition("rolled_back")
            return {"result": "rolled_back", "state": self.state, "transaction_id": self.transaction_id}
        except Exception as rollback_error:
            if self.state != "manual_recovery_required":
                if self.state != "rolling_back":
                    self.transition("rolling_back", details={"reason": _error_code(error)})
                self.transition("manual_recovery_required", details={"reason": _error_code(rollback_error)})
            return {
                "result": "manual_recovery_required",
                "state": self.state,
                "error_code": _error_code(rollback_error),
                "transaction_id": self.transaction_id,
            }

    def rollback(
        self,
        *,
        previous_deb: Path | str | None = None,
        previous_sha256: str | None = None,
        previous_signature: Path | str | None = None,
        rollback_install_fn: Callable[[Path], Any] | None = None,
    ) -> dict[str, Any]:
        if self.state not in {"committed", "verified", "migrated", "package_changed", "snapshotted"}:
            if self.state == "rolled_back":
                return {"result": "rolled_back", "state": self.state, "transaction_id": self.transaction_id}
            return {"result": "manual_recovery_required", "state": self.state, "transaction_id": self.transaction_id}
        if previous_deb is None or previous_sha256 is None or previous_signature is None:
            return self._mark_manual_recovery(UpgradeError("previous_package_required"))
        try:
            previous_deb = Path(previous_deb)
            previous_signature = Path(previous_signature)
            self._preflight_previous(previous_deb, previous_sha256, previous_signature)
        except Exception as error:
            if self.state != "manual_recovery_required":
                if self.state != "rolling_back":
                    self.transition("rolling_back", details={"reason": _error_code(error)})
                self.transition("manual_recovery_required", details={"reason": _error_code(error)})
            return {
                "result": "manual_recovery_required",
                "state": self.state,
                "error_code": _error_code(error),
                "transaction_id": self.transaction_id,
            }
        if rollback_install_fn is None:
            return self._mark_manual_recovery(UpgradeError("rollback_install_required"))
        previous_artifact = self.transaction_dir / "artifacts" / "previous.deb"
        if self.operation in {"upgrade", "rollback"}:
            try:
                _validate_private_file(previous_artifact)
                expected = self._journal.get("previous_package", {}).get("sha256")
                if not isinstance(expected, str) or _sha256(previous_artifact) != expected:
                    raise UpgradeError("journal_artifact_hash_mismatch")
                previous_deb = previous_artifact
            except Exception as error:
                return self._mark_manual_recovery(error)
        return self._recover(
            previous_deb,
            rollback_install_fn=rollback_install_fn,
            error=UpgradeError("manual_rollback"),
        )

    def _mark_manual_recovery(self, error: Exception) -> dict[str, Any]:
        if self.state != "manual_recovery_required":
            if self.state != "rolling_back":
                self.transition("rolling_back", details={"reason": _error_code(error)})
            self.transition("manual_recovery_required", details={"reason": _error_code(error)})
        return {
            "result": "manual_recovery_required",
            "state": self.state,
            "error_code": _error_code(error),
            "transaction_id": self.transaction_id,
        }

    def run_reinstall(self, *, version: str, current_version: str) -> dict[str, Any]:
        if version != current_version:
            return {"result": "blocked", "state": self.state, "error_code": "version_mismatch"}
        # Reinstall never snapshots/replaces user files: dpkg owns only the
        # package payload and maintainer scripts.  Keeping this operation
        # explicit makes same-version deployment idempotent and auditable.
        return {"result": "reinstalled", "state": self.state, "transaction_id": self.transaction_id}


def _error_code(error: Exception) -> str:
    text = str(error)
    if isinstance(error, PreviousPackageError):
        return text or "previous_package_invalid"
    if isinstance(error, ManualRecoveryRequired):
        return text or "manual_recovery_required"
    return error.__class__.__name__.lower() or "upgrade_failed"


__all__ = [
    "AccountIdentity",
    "CONTRACT_PATH",
    "CONTRACT_SCHEMA",
    "FAILURE_STATES",
    "InvalidTransition",
    "ManualRecoveryRequired",
    "PreviousPackageError",
    "STATES",
    "UnsafeDataError",
    "UpgradeError",
    "UpgradeTransaction",
    "ensure_not_mountpoint",
    "load_contract",
    "resolve_account",
    "sqlite_backup",
    "validate_account",
    "validate_contract",
]
