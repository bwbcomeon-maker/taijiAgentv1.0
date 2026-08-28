"""Minimal cross-platform directory trust contract for Taiji approvals."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

PathAction = Literal["read", "create", "modify", "move", "delete", "workdir", "execute"]

_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x0400
_WINDOWS_INHERIT_ONLY_ACE = 0x08
_WINDOWS_ALLOW_ACE_TYPES = {0, 5, 9, 11}
_WINDOWS_TRUSTED_SYSTEM_SIDS = {"S-1-5-18", "S-1-5-32-544"}
_WINDOWS_WRITE_MASK = 0x40000000 | 0x10000000 | 0x00040000 | 0x00000100 | 0x00000002


@dataclass(frozen=True)
class PathDecision:
    outcome: str
    code: str
    message_zh: str
    target: str
    authorization_root: str | None = None


def posix_directory_security_reason(
    *, owner_uid: int, mode: int, current_uid: int, is_symlink: bool = False
) -> str | None:
    if is_symlink:
        return "posix_symlink_escape"
    if owner_uid != current_uid:
        return "posix_owner_untrusted"
    if mode & stat.S_IWOTH:
        return "posix_world_writable"
    return None


def posix_directory_is_usable(**kwargs) -> bool:
    return posix_directory_security_reason(**kwargs) is None


def windows_directory_security_reason(
    *,
    owner_sid: str,
    current_user_sid: str,
    entries: Iterable[tuple[int, int, int, str]],
    is_reparse_point: bool = False,
) -> str | None:
    if is_reparse_point:
        return "windows_reparse_escape"
    owner = owner_sid.upper()
    current = current_user_sid.upper()
    if owner != current:
        return "windows_owner_untrusted"
    trusted = {current, *_WINDOWS_TRUSTED_SYSTEM_SIDS}
    for ace_type, ace_flags, access_mask, sid in entries:
        if ace_flags & _WINDOWS_INHERIT_ONLY_ACE:
            continue
        if ace_type not in _WINDOWS_ALLOW_ACE_TYPES:
            continue
        if access_mask & _WINDOWS_WRITE_MASK and sid.upper() not in trusted:
            return "windows_acl_untrusted"
    return None


def windows_directory_is_usable(**kwargs) -> bool:
    return windows_directory_security_reason(**kwargs) is None


def decide_path(
    target: os.PathLike[str] | str,
    *,
    action: PathAction,
    authorized_roots: Iterable[os.PathLike[str] | str] = (),
) -> PathDecision:
    candidate = Path(target).expanduser()
    physical = candidate.resolve(strict=False)
    for root in authorized_roots:
        try:
            physical.relative_to(Path(root).expanduser().resolve(strict=False))
            return PathDecision("allow", "path_authorized", "目标位于已授权目录内。", str(physical))
        except ValueError:
            continue
    root = physical if physical.is_dir() else physical.parent
    if os.name == "posix":
        metadata = root.lstat()
        reason = posix_directory_security_reason(
            owner_uid=metadata.st_uid,
            mode=stat.S_IMODE(metadata.st_mode),
            current_uid=os.getuid(),
            is_symlink=root.is_symlink(),
        )
        if reason:
            return PathDecision("block", reason, "目录安全属性不符合要求。", str(physical))
    return PathDecision(
        "directory_approval",
        "path_directory_approval_required",
        "首次访问该目录需要用户授权。",
        str(physical),
        str(root),
    )
