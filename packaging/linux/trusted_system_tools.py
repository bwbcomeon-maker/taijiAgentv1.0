#!/usr/bin/env python3
"""Resolve build tools only from root-managed, non-writable system paths."""

from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path
from typing import Iterable


TRUSTED_SYSTEM_DIRECTORIES = (Path("/usr/bin"), Path("/bin"))
TRUSTED_READELF_CANDIDATES = (
    Path("/usr/bin/readelf"),
    Path("/bin/readelf"),
    Path("/usr/bin/x86_64-linux-gnu-readelf"),
    Path("/bin/x86_64-linux-gnu-readelf"),
)


class TrustedSystemToolError(RuntimeError):
    """Raised when a required system tool cannot be resolved safely."""


def _metadata(path: Path, *, follow_symlinks: bool):
    return path.stat() if follow_symlinks else path.lstat()


def _trusted_directories(directories: Iterable[Path]) -> set[Path]:
    trusted: set[Path] = set()
    for directory in directories:
        try:
            resolved = directory.resolve(strict=True)
            metadata = _metadata(resolved, follow_symlinks=False)
        except (OSError, RuntimeError):
            continue
        if (
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == 0
            and not metadata.st_mode & 0o022
        ):
            trusted.add(resolved)
    return trusted


def resolve_trusted_system_tool(
    command: str,
    *,
    candidates: Iterable[Path],
    trusted_directories: Iterable[Path] = TRUSTED_SYSTEM_DIRECTORIES,
    allowed_resolved_names: Iterable[str] | None = None,
) -> str:
    """Return a canonical executable path without trusting the caller's PATH.

    A normal Debian-family alias such as ``/usr/bin/readelf ->
    x86_64-linux-gnu-readelf`` is accepted only after the alias directory and
    resolved executable are both proven root-managed and non-writable by
    group/other users.  The returned path is the canonical regular file, so a
    later exec does not traverse the alias again.
    """

    allowed_names = set(allowed_resolved_names or (command,))
    trusted = _trusted_directories(trusted_directories)
    if not trusted:
        raise TrustedSystemToolError(
            f"trusted {command} unavailable: no safe system command directory"
        )

    reasons: list[str] = []
    for raw_candidate in candidates:
        candidate = Path(raw_candidate)
        try:
            parent = candidate.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            reasons.append(f"{candidate}: parent cannot resolve ({exc})")
            continue
        if parent not in trusted:
            reasons.append(f"{candidate}: parent is outside trusted system directories")
            continue
        try:
            alias_metadata = _metadata(candidate, follow_symlinks=False)
        except FileNotFoundError:
            reasons.append(f"{candidate}: missing")
            continue
        except OSError as exc:
            reasons.append(f"{candidate}: cannot inspect ({exc})")
            continue
        if alias_metadata.st_uid != 0:
            reasons.append(f"{candidate}: alias is not root-owned")
            continue
        if not (stat.S_ISREG(alias_metadata.st_mode) or stat.S_ISLNK(alias_metadata.st_mode)):
            reasons.append(f"{candidate}: alias is neither a regular file nor a symlink")
            continue
        if stat.S_ISREG(alias_metadata.st_mode) and alias_metadata.st_mode & 0o022:
            reasons.append(f"{candidate}: executable is group/other writable")
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved_metadata = _metadata(resolved, follow_symlinks=False)
        except (OSError, RuntimeError) as exc:
            reasons.append(f"{candidate}: broken or unresolvable link ({exc})")
            continue
        if resolved.parent not in trusted:
            reasons.append(f"{candidate}: resolved target escapes trusted system directories")
            continue
        if resolved.name not in allowed_names:
            reasons.append(f"{candidate}: unexpected resolved executable {resolved.name}")
            continue
        if not stat.S_ISREG(resolved_metadata.st_mode):
            reasons.append(f"{candidate}: resolved target is not a regular file")
            continue
        if resolved_metadata.st_uid != 0:
            reasons.append(f"{candidate}: resolved target is not root-owned")
            continue
        if not resolved_metadata.st_mode & 0o111:
            reasons.append(f"{candidate}: resolved target is not executable")
            continue
        if resolved_metadata.st_mode & 0o022:
            reasons.append(f"{candidate}: resolved target is group/other writable")
            continue
        return str(resolved)

    detail = "; ".join(reasons) if reasons else "no candidates"
    raise TrustedSystemToolError(f"trusted {command} unavailable: {detail}")


def resolve_trusted_readelf() -> str:
    return resolve_trusted_system_tool(
        "readelf",
        candidates=TRUSTED_READELF_CANDIDATES,
        allowed_resolved_names=("readelf", "x86_64-linux-gnu-readelf"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("readelf",))
    args = parser.parse_args(argv)
    if args.command == "readelf":
        print(resolve_trusted_readelf())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrustedSystemToolError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
