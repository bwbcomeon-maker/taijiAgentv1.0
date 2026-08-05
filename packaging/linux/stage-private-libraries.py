#!/usr/bin/env python3
"""Atomically stage policy-approved private ELF libraries into a payload root."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "taiji-elf-private-library-stage/v1"
_SONAME_RE = re.compile(r"\(SONAME\).*?\[([^\]]+)\]")


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


def readelf_soname(path: Path) -> str | None:
    completed = subprocess.run(
        ["readelf", "-d", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    match = _SONAME_RE.search(completed.stdout)
    return match.group(1) if match else None


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


def _iter_sources(sysroot: Path):
    if not sysroot.is_dir():
        raise StageError(f"private-library sysroot is not a directory: {sysroot}")
    for candidate in sorted(sysroot.rglob("*"), key=lambda item: item.as_posix()):
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
        except OSError as exc:
            raise StageError(f"cannot inspect private-library source: {candidate}") from exc
        yield candidate


def _copy_atomically(source: Path, destination: Path, *, uid: int, gid: int) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise StageError(f"cannot open private-library source safely: {source}") from exc
    temporary: Path | None = None
    try:
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
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return digest.hexdigest()
    except OSError as exc:
        raise StageError(f"atomic private-library staging failed: {source} -> {destination}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def stage_private_libraries(root: Path, policy: dict[str, Any], sysroot: Path) -> dict[str, Any]:
    destination_dir = _destination(root, policy)
    allowlisted = set(policy["elf"]["allowed_private_sonames"])
    candidates: dict[str, list[Path]] = {}
    for source in _iter_sources(sysroot):
        try:
            soname = readelf_soname(source)
        except OSError:
            soname = None
        if soname not in allowlisted:
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


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


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
