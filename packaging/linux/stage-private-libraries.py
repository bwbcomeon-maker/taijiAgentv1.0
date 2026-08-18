#!/usr/bin/env python3
"""Atomically stage policy-approved private ELF libraries into a payload root."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "taiji-elf-private-library-stage/v1"
_SONAME_RE = re.compile(r"\(SONAME\).*?\[([^\]]+)\]")
_TRUSTED_READELF_CANDIDATES = (
    Path("/usr/bin/readelf"),
    Path("/bin/readelf"),
    Path("/usr/bin/x86_64-linux-gnu-readelf"),
    Path("/bin/x86_64-linux-gnu-readelf"),
)
_TRUSTED_READELF_DIRECTORIES = (Path("/usr/bin"), Path("/bin"))
_DEBIAN_AMD64_LIBRARY_DIRECTORIES = (
    Path("usr/lib/x86_64-linux-gnu"),
    Path("usr/lib64"),
)
_TRUSTED_TOOLS_MODULE = None


class StageError(RuntimeError):
    """Raised when a private-library staging input is unsafe."""


def load_policy(path: Path) -> dict[str, Any]:
    module_path = Path(__file__).with_name("compatibility_policy.py")
    spec = importlib.util.spec_from_file_location("taiji_compatibility_policy_for_stager", module_path)
    if spec is None or spec.loader is None:
        raise StageError(f"cannot load compatibility policy helper: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.load_and_validate(path)
    except Exception as exc:
        raise StageError(str(exc)) from exc


def canonical_policy_bytes(policy: dict[str, Any]) -> bytes:
    return (json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def policy_sha256(policy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_policy_bytes(policy)).hexdigest()


def source_metadata(path: Path) -> tuple[int, int, int]:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise StageError(f"cannot lstat private-library source: {path}") from exc
    return metadata.st_mode, metadata.st_uid, metadata.st_nlink


def _trusted_tools_module():
    global _TRUSTED_TOOLS_MODULE
    if _TRUSTED_TOOLS_MODULE is not None:
        return _TRUSTED_TOOLS_MODULE
    module_path = Path(__file__).with_name("trusted_system_tools.py")
    spec = importlib.util.spec_from_file_location("taiji_trusted_system_tools_for_stager", module_path)
    if spec is None or spec.loader is None:
        raise StageError(f"cannot load trusted system tool helper: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _TRUSTED_TOOLS_MODULE = module
    return module


def resolve_trusted_readelf() -> str:
    module = _trusted_tools_module()
    try:
        return module.resolve_trusted_system_tool(
            "readelf",
            candidates=_TRUSTED_READELF_CANDIDATES,
            trusted_directories=_TRUSTED_READELF_DIRECTORIES,
            allowed_resolved_names=("readelf", "x86_64-linux-gnu-readelf"),
        )
    except module.TrustedSystemToolError as exc:
        raise StageError(str(exc)) from exc


def readelf_soname(path: Path) -> str | None:
    try:
        completed = subprocess.run(
            [resolve_trusted_readelf(), "-d", str(path)],
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as exc:
        raise StageError(f"trusted readelf could not execute for {path}: {exc}") from exc
    if completed.returncode != 0:
        return None
    if "(SONAME)" in completed.stdout and not _SONAME_RE.search(completed.stdout):
        raise StageError(f"malformed SONAME entry: {path}")
    match = _SONAME_RE.search(completed.stdout)
    soname = match.group(1) if match else None
    if match and not soname:
        raise StageError(f"malformed SONAME entry: {path}")
    return soname


def validate_source(path: Path, policy: dict[str, Any]) -> str:
    mode, uid, nlink = source_metadata(path)
    if stat.S_ISLNK(mode):
        raise StageError(f"private-library source must not be a symlink: {path}")
    if not stat.S_ISREG(mode):
        raise StageError(f"private-library source must be a regular file: {path}")
    if uid != 0:
        raise StageError(f"private-library source must be root-owned: {path} (uid {uid})")
    if nlink != 1:
        raise StageError(f"private-library source must have one hard link: {path} (nlink {nlink})")
    soname = readelf_soname(path)
    if not soname:
        raise StageError(f"private-library source has no authoritative SONAME: {path}")
    elf = policy["elf"]
    if soname in set(elf["required_system_sonames"]):
        raise StageError(f"required-system SONAME cannot be private-staged: {soname}")
    if soname in set(elf["forbidden_bundled_sonames"]):
        raise StageError(f"forbidden bundled SONAME cannot be private-staged: {soname}")
    if soname not in set(elf["allowed_private_sonames"]):
        raise StageError(f"non-allowlisted private SONAME: {soname}")
    return soname


def _destination(root: Path, policy: dict[str, Any]) -> Path:
    install_root = Path(policy["package"]["install_root"])
    if root.resolve(strict=False).name == install_root.name:
        return root / Path(policy["elf"]["private_library_dir"]).relative_to(install_root)
    return root / install_root.relative_to(Path("/")) / Path(
        policy["elf"]["private_library_dir"]
    ).relative_to(install_root)


def _ensure_private_directory(destination: Path, root: Path) -> None:
    try:
        relative_parts = destination.relative_to(root).parts
    except ValueError as exc:
        raise StageError(f"private-library destination escapes staging root: {destination}") from exc
    current = root
    for part in relative_parts:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o755)
            except OSError as exc:
                raise StageError(f"cannot create private-library directory: {current}") from exc
            os.chmod(current, 0o755)
            metadata = os.lstat(current)
        except OSError as exc:
            raise StageError(f"cannot inspect private-library directory: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise StageError(f"private-library directory is not a real directory: {current}")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            try:
                os.chown(current, 0, 0)
            except OSError as exc:
                raise StageError(f"cannot root-own private-library directory: {current}") from exc
            metadata = os.lstat(current)
        if stat.S_IMODE(metadata.st_mode) != 0o755 or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise StageError(
                f"private-library directory must be mode 0755 and not group/other writable: {current}"
            )


def _open_secure_directory(path: Path) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise StageError(f"cannot open private-library directory safely: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o755
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        raise StageError(f"private-library directory is not mode 0755 and private: {path}")
    return descriptor, metadata


def _basename_matches_allowlisted(path: Path, allowlisted: set[str]) -> bool:
    return path.name in allowlisted or any(path.name.startswith(f"{soname}.") for soname in allowlisted)


def _candidate_source_directories(sysroot: Path) -> tuple[Path, ...]:
    if (
        sysroot.name == "x86_64-linux-gnu"
        and sysroot.parent.name == "lib"
        and not sysroot.is_symlink()
    ) or (sysroot.name == "lib64" and not sysroot.is_symlink()):
        return (sysroot,)
    directories = tuple(
        directory
        for relative in _DEBIAN_AMD64_LIBRARY_DIRECTORIES
        if (directory := sysroot / relative).is_dir() and not directory.is_symlink()
    )
    return directories


def _iter_sources(sysroot: Path, policy: dict[str, Any]):
    if not sysroot.is_dir():
        raise StageError(f"private-library sysroot is not a directory: {sysroot}")
    allowlisted = set(policy["elf"]["allowed_private_sonames"])
    source_directories = _candidate_source_directories(sysroot)
    if source_directories:
        # Debian-family amd64 runtime libraries are selected from the standard
        # multiarch directories.  Do not recurse into vendor GPU subdirectories
        # that are not part of the system linker's selected runtime closure.
        candidates = (
            candidate
            for directory in source_directories
            for candidate in directory.iterdir()
        )
    else:
        # Retain generic sysroot support for isolated fixture/build roots that
        # do not expose the Debian amd64 directory layout.
        candidates = sysroot.rglob("*")
    for candidate in sorted(candidates, key=lambda item: item.as_posix()):
        try:
            if candidate.is_symlink():
                # Debian-family SONAME aliases normally point at the real,
                # versioned ELF beside them.  Discovery ignores the alias and
                # validates/copies only the root-owned regular target found by
                # the same deterministic scan.
                continue
            if not candidate.is_file():
                continue
        except OSError as exc:
            raise StageError(f"cannot inspect private-library source: {candidate}") from exc
        yield candidate


def _copy_atomically(source: Path, destination: Path, *, uid: int, gid: int) -> str:
    parent_descriptor, parent_snapshot = _open_secure_directory(destination.parent)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != 0:
            raise StageError(f"private-library source changed during staging: {source}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=f".{destination.name}.tmp-", delete=False
            ) as output:
                temporary = Path(output.name)
                while block := handle.read(1024 * 1024):
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(temporary, uid, gid)
        current_parent = os.fstat(parent_descriptor)
        if (current_parent.st_dev, current_parent.st_ino) != (
            parent_snapshot.st_dev,
            parent_snapshot.st_ino,
        ):
            raise StageError(f"private-library destination directory changed: {destination.parent}")
        os.replace(temporary, destination)
        final_parent = os.fstat(parent_descriptor)
        if (final_parent.st_dev, final_parent.st_ino) != (
            parent_snapshot.st_dev,
            parent_snapshot.st_ino,
        ):
            raise StageError(f"private-library destination directory changed after copy: {destination.parent}")
        os.fsync(parent_descriptor)
        return digest.hexdigest()
    except OSError as exc:
        raise StageError(f"atomic private-library staging failed: {source} -> {destination}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def stage_private_libraries(root: Path, policy: dict[str, Any], sysroot: Path) -> dict[str, Any]:
    destination_dir = _destination(root, policy)
    allowlisted = set(policy["elf"]["allowed_private_sonames"])
    candidates: dict[str, list[Path]] = {}
    for source in _iter_sources(sysroot, policy):
        try:
            soname = readelf_soname(source)
        except OSError:
            soname = None
        if soname is None and _basename_matches_allowlisted(source, allowlisted):
            raise StageError(f"allowlisted private-library source has no SONAME: {source}")
        if soname not in allowlisted:
            if _basename_matches_allowlisted(source, allowlisted):
                raise StageError(
                    f"allowlisted private-library basename has mismatched SONAME {soname}: {source}"
                )
            continue
        # Validate again after selecting by SONAME so owner/link and symlink
        # checks cannot be bypassed by a path-shaped source.
        validate_source(source, policy)
        candidates.setdefault(soname, []).append(source)

    files: list[dict[str, Any]] = []
    for soname in sorted(candidates):
        matches = candidates[soname]
        if len(matches) != 1:
            raise StageError(f"ambiguous private-library source for SONAME {soname}")
        source = matches[0]
        destination = destination_dir / soname
        _ensure_private_directory(destination.parent, root)
        digest = _copy_atomically(source, destination, uid=0, gid=0)
        files.append({"soname": soname, "relative_path": destination.relative_to(root).as_posix(), "sha256": digest})

    report = {
        "schema": SCHEMA,
        "policy_id": policy["policy_id"],
        "compatibility_policy_sha256": policy_sha256(policy),
        "private_library_dir": policy["elf"]["private_library_dir"],
        "files": files,
    }
    return report


def _assert_report_parent_safe(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StageError(f"cannot inspect report directory: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            if metadata.st_uid != 0:
                raise StageError(f"report directory contains untrusted symlink: {current}")
            try:
                current = current.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise StageError(f"cannot resolve report directory symlink: {current}") from exc


def _open_report_directory(path: Path) -> int:
    try:
        path.mkdir(parents=True, exist_ok=True)
        _assert_report_parent_safe(path)
        raw_path = Path(os.path.abspath(path))
        canonical_path = Path(os.path.realpath(raw_path))
        if raw_path != canonical_path and not (
            str(raw_path).startswith("/var/")
            and str(canonical_path).startswith("/private/var/")
        ):
            raise StageError(f"report directory resolves through symlink: {path}")
        return os.open(
            canonical_path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except StageError:
        raise
    except OSError as exc:
        raise StageError(f"cannot open report directory safely: {path}") from exc


def write_report(path: Path, report: dict[str, Any]) -> None:
    directory_fd = _open_report_directory(path.parent)
    descriptor = -1
    temporary_name: str | None = None
    try:
        for _ in range(8):
            candidate = f".{path.name}.{secrets.token_hex(12)}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if descriptor < 0 or temporary_name is None:
            raise StageError("cannot allocate a private report temporary file")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary_name = None
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
    except StageError:
        raise
    except OSError as exc:
        raise StageError(f"atomic report write failed: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--sysroot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    report = stage_private_libraries(args.root, policy, args.sysroot)
    write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StageError as exc:
        print(f"Private-library staging failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
