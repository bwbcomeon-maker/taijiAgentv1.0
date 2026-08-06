#!/usr/bin/env python3
"""Audit all Linux ELF payload files against the fixed Taiji ABI policy.

The audit deliberately uses ``readelf`` as its authority.  ``ldd`` may be used
by a later Linux smoke test, but it is not sufficient to establish a release
closure because it executes the loader and is host-dependent.
"""

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
from pathlib import Path
from typing import Any, Iterable


ELF_MAGIC = b"\x7fELF"
SCHEMA = "taiji-elf-abi-audit/v1"
_VERSION_RE = re.compile(r"\b(GLIBCXX|CXXABI|GLIBC)_([0-9]+(?:\.[0-9]+)*)\b")
_HOST_PATH_RE = re.compile(
    rb"/(?:Users|home|private/var|var/tmp|tmp|workspace|build|Volumes)/"
    rb"[A-Za-z0-9_.+@%~:/\\-]+"
)
_TAIJI_BUILD_PATH_RE = re.compile(
    rb"/(?:Users|home|private/var|var/tmp|tmp|workspace|build|Volumes)/"
    rb"[A-Za-z0-9_.+@%~:/\\-]*(?:taiji|hermes)[A-Za-z0-9_.+@%~:/\\-]*",
    re.IGNORECASE,
)
_KNOWN_EXTERNAL_SONAMES = {
    "ld-linux-x86-64.so.2",
    "libc.so.6",
    "libdl.so.2",
    "libm.so.6",
    "libpthread.so.0",
    "librt.so.1",
    "libgcc_s.so.1",
    "libstdc++.so.6",
    "libz.so.1",
    "libresolv.so.2",
    "libutil.so.1",
    "libcrypt.so.1",
    "libnss_compat.so.2",
    "libnss_dns.so.2",
    "libnss_files.so.2",
}
_TRUSTED_READELF_CANDIDATES = (
    Path("/usr/bin/readelf"),
    Path("/bin/readelf"),
    Path("/usr/bin/x86_64-linux-gnu-readelf"),
    Path("/bin/x86_64-linux-gnu-readelf"),
)
_TRUSTED_READELF_DIRECTORIES = (Path("/usr/bin"), Path("/bin"))
_TRUSTED_TOOLS_MODULE = None


class ElfAuditError(RuntimeError):
    """Raised when a payload ELF violates the compatibility closure."""


def load_policy(path: Path) -> dict[str, Any]:
    module_path = Path(__file__).with_name("compatibility_policy.py")
    spec = importlib.util.spec_from_file_location("taiji_compatibility_policy_for_elf", module_path)
    if spec is None or spec.loader is None:
        raise ElfAuditError(f"cannot load compatibility policy helper: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.load_and_validate(path)
    except Exception as exc:  # policy helper exposes a project-specific error type
        raise ElfAuditError(str(exc)) from exc


def canonical_policy_bytes(policy: dict[str, Any]) -> bytes:
    return (json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def policy_sha256(policy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_policy_bytes(policy)).hexdigest()


def _trusted_tools_module():
    global _TRUSTED_TOOLS_MODULE
    if _TRUSTED_TOOLS_MODULE is not None:
        return _TRUSTED_TOOLS_MODULE
    module_path = Path(__file__).with_name("trusted_system_tools.py")
    spec = importlib.util.spec_from_file_location("taiji_trusted_system_tools_for_elf", module_path)
    if spec is None or spec.loader is None:
        raise ElfAuditError(f"cannot load trusted system tool helper: {module_path}")
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
        raise ElfAuditError(str(exc)) from exc


def run_readelf(path: Path, option: str) -> str:
    try:
        completed = subprocess.run(
            [resolve_trusted_readelf(), option, str(path)],
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as exc:
        raise ElfAuditError(f"trusted readelf could not execute for {path}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ElfAuditError(f"readelf {option} failed for {path}: {detail}")
    return completed.stdout


def parse_readelf_header(text: str) -> str:
    class_match = re.search(r"^\s*Class:\s*(\S+)", text, re.MULTILINE)
    machine_match = re.search(r"^\s*Machine:\s*(.+?)\s*$", text, re.MULTILINE)
    machine_text = (machine_match.group(1).strip() if machine_match else "").lower()
    if class_match and class_match.group(1) != "ELF64":
        return machine_text or "non-ELF64"
    if "x86-64" in machine_text or "x86_64" in machine_text or "amd64" in machine_text:
        return "x86_64"
    if "aarch64" in machine_text or "arm64" in machine_text:
        return "aarch64"
    if "80386" in machine_text or machine_text == "i386":
        return "i386"
    return machine_text or "unknown"


def _bracket_value(line: str) -> str | None:
    match = re.search(r"\[([^\]]*)\]", line)
    return match.group(1) if match else None


def parse_readelf_dynamic(text: str) -> dict[str, Any]:
    needed: list[str] = []
    runpath: list[str] = []
    rpath: list[str] = []
    sonames: list[str] = []
    for line in text.splitlines():
        tag = re.search(r"\((NEEDED|RUNPATH|RPATH|SONAME)\)", line)
        if not tag:
            continue
        value = _bracket_value(line)
        if value is None or not value:
            raise ElfAuditError(f"malformed dynamic {tag.group(1)} entry: {line.strip()}")
        kind = tag.group(1)
        if kind == "NEEDED":
            needed.append(value)
        elif kind == "RUNPATH":
            segments = value.split(":")
            if any(not item for item in segments):
                raise ElfAuditError(f"malformed dynamic RUNPATH entry: {line.strip()}")
            runpath.extend(segments)
        elif kind == "RPATH":
            segments = value.split(":")
            if any(not item for item in segments):
                raise ElfAuditError(f"malformed dynamic RPATH entry: {line.strip()}")
            rpath.extend(segments)
        else:
            sonames.append(value)
    return {
        "needed": sorted(set(needed)),
        "runpath": runpath,
        "rpath": rpath,
        "sonames": sonames,
        "soname": sonames[0] if sonames else None,
    }


def parse_readelf_version_info(text: str) -> dict[str, list[str]]:
    versions: dict[str, set[str]] = {"GLIBC": set(), "GLIBCXX": set(), "CXXABI": set()}
    for namespace, version in _VERSION_RE.findall(text):
        versions[namespace].add(version)
    return {namespace: sorted(values, key=_version_key) for namespace, values in versions.items() if values}


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _iter_regular_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        raise ElfAuditError(f"ELF audit root is not a directory: {root}")
    resolved_root = root.resolve(strict=True)
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        try:
            if candidate.is_symlink():
                try:
                    resolved_target = candidate.resolve(strict=False)
                except (OSError, RuntimeError) as exc:
                    raise ElfAuditError(f"cannot resolve payload symlink safely: {candidate}") from exc
                try:
                    resolved_target.relative_to(resolved_root)
                except ValueError as exc:
                    raise ElfAuditError(f"payload symlink escapes audit root: {candidate}") from exc
                # Internal payload symlinks are audited through their regular
                # target path; the payload contract owns symlink integrity.
                continue
            if candidate.is_file():
                yield candidate
        except OSError as exc:
            raise ElfAuditError(f"cannot inspect payload path: {candidate}") from exc


def _looks_like_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == ELF_MAGIC
    except OSError as exc:
        raise ElfAuditError(f"cannot read payload ELF candidate: {path}") from exc


def _electron_elf_files(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return policy["elf"]["electron_distribution"]["elf_files"]


def _check_build_host_path_leak(
    path: Path,
    relative_path: str,
    policy: dict[str, Any],
) -> None:
    overlap = b""
    matches: set[bytes] = set()
    taiji_build_matches: set[bytes] = set()
    try:
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                window = overlap + block
                matches.update(match.group(0) for match in _HOST_PATH_RE.finditer(window))
                taiji_build_matches.update(
                    match.group(0) for match in _TAIJI_BUILD_PATH_RE.finditer(window)
                )
                overlap = window[-256:]
    except OSError as exc:
        raise ElfAuditError(f"cannot scan ELF bytes: {path}") from exc
    if not matches:
        return
    descriptor = _electron_elf_files(policy).get(relative_path)
    if descriptor is not None:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ElfAuditError(f"cannot hash Electron ELF: {path}") from exc
        if digest == descriptor["sha256"]:
            allowed = {
                literal.encode("utf-8")
                for literal in descriptor["allowed_host_path_literals"]
            }
            if matches <= allowed:
                return
        raise ElfAuditError(f"build-host absolute path leak in ELF: {path}")

    # Third-party Python, Node and native-wheel binaries legitimately retain
    # upstream compiler/debug literals such as /build/BUILD/gcc, /home/.cargo
    # and runtime templates under /tmp.  Treating every such literal as a
    # local build-host leak makes a clean payload impossible to package.  For
    # non-Electron ELF files, reject only paths carrying this product's own
    # Taiji/Hermes build identity.  Electron remains stricter above: its exact
    # archive-derived hashes and complete literal allowlist are both required.
    if not taiji_build_matches:
        return
    raise ElfAuditError(f"build-host absolute path leak in ELF: {path}")


def _validate_electron_distribution(
    records: list[dict[str, Any]],
    policy: dict[str, Any],
) -> set[str]:
    expected = _electron_elf_files(policy)
    expected_paths = set(expected)
    electron_directory = Path(next(iter(expected_paths))).parent.as_posix() + "/"
    observed = {
        record["relative_path"]: record
        for record in records
        if record["relative_path"].startswith(electron_directory)
    }
    if not observed:
        return set()
    missing = sorted(expected_paths - set(observed))
    if missing:
        raise ElfAuditError(f"missing pinned Electron ELF: {', '.join(missing)}")
    unexpected = sorted(set(observed) - expected_paths)
    if unexpected:
        raise ElfAuditError(f"unexpected Electron ELF: {', '.join(unexpected)}")

    companion_sonames: set[str] = set()
    for relative_path, descriptor in expected.items():
        record = observed[relative_path]
        if record["sha256"] != descriptor["sha256"]:
            raise ElfAuditError(f"Electron ELF hash mismatch: {relative_path}")
        if record["_soname"] != descriptor["soname"]:
            raise ElfAuditError(f"Electron ELF SONAME mismatch: {relative_path}")
        if descriptor["soname"]:
            companion_sonames.add(descriptor["soname"])
    return companion_sonames


def _allowed_runpath(path: str, policy: dict[str, Any]) -> bool:
    allowed = set(policy["elf"]["allowed_runpaths"])
    if path in allowed:
        return True
    # The contract only permits a literal $ORIGIN token.  In particular,
    # $ORIGIN/../../foo must not be treated as a safe relative path.
    return False


def _forbidden_soname(path: Path, soname: str | None, policy: dict[str, Any]) -> str | None:
    forbidden = set(policy["elf"]["forbidden_bundled_sonames"])
    if soname in forbidden:
        return soname
    if path.name in forbidden:
        return path.name
    return None


def _version_needs_above_policy(
    version_needs: dict[str, list[str]], policy: dict[str, Any]
) -> tuple[str, str, str] | None:
    maximum = policy["elf"]["maximum_symbol_versions"]
    for namespace, versions in version_needs.items():
        limit = maximum.get(namespace)
        if limit is None:
            continue
        for version in versions:
            if _version_key(version) > _version_key(limit):
                return namespace, version, limit
    return None


def _inspect_elf(path: Path) -> dict[str, Any]:
    header = parse_readelf_header(run_readelf(path, "-h"))
    dynamic = parse_readelf_dynamic(run_readelf(path, "-d"))
    version_needs = parse_readelf_version_info(run_readelf(path, "--version-info"))
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ElfAuditError(f"cannot hash ELF: {path}") from exc
    return {
        "relative_path": "",
        "sha256": digest,
        "machine": header,
        "needed": dynamic["needed"],
        "runpath": dynamic["runpath"],
        "version_needs": version_needs,
        "_rpath": dynamic["rpath"],
        "_soname": dynamic["soname"],
        "_sonames": dynamic["sonames"],
    }


def _sysroot_soname_candidates(sysroot: Path, soname: str) -> list[Path]:
    if not sysroot or not sysroot.is_dir():
        return []
    candidates: list[Path] = []
    seen_inodes: set[tuple[int, int]] = set()
    for candidate in _iter_regular_files(sysroot):
        if candidate.name != soname and not candidate.name.startswith(soname + "."):
            continue
        try:
            metadata = candidate.stat()
            inode_key = (metadata.st_dev, metadata.st_ino)
            if inode_key in seen_inodes:
                continue
            seen_inodes.add(inode_key)
            if _looks_like_elf(candidate):
                dynamic = parse_readelf_dynamic(run_readelf(candidate, "-d"))
                if dynamic["soname"] == soname:
                    candidates.append(candidate)
        except ElfAuditError:
            raise
    return candidates


def audit_root(root: Path, policy: dict[str, Any], sysroot: Path | None = None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    root_sonames: dict[str, list[str]] = {}
    private_allowed = set(policy["elf"]["allowed_private_sonames"])
    required_system = set(policy["elf"]["required_system_sonames"])
    electron_files = _electron_elf_files(policy)
    for path in _iter_regular_files(root):
        if not _looks_like_elf(path):
            continue
        relative_path = path.relative_to(root).as_posix()
        _check_build_host_path_leak(path, relative_path, policy)
        record = _inspect_elf(path)
        record["relative_path"] = relative_path
        if record["machine"] != "x86_64":
            raise ElfAuditError(
                f"ELF must be x86_64, got {record['machine']}: {record['relative_path']}"
            )
        above = _version_needs_above_policy(record["version_needs"], policy)
        if above:
            namespace, version, limit = above
            raise ElfAuditError(
                f"{namespace} symbol version {version} exceeds policy maximum {limit}: {record['relative_path']}"
            )
        for rpath in record["_rpath"]:
            if not _allowed_runpath(rpath, policy):
                raise ElfAuditError(f"unsafe RPATH {rpath!r}: {record['relative_path']}")
        for runpath in record["runpath"]:
            if not _allowed_runpath(runpath, policy):
                raise ElfAuditError(f"unsafe RUNPATH {runpath!r}: {record['relative_path']}")
        forbidden = _forbidden_soname(path, record["_soname"], policy)
        if forbidden:
            raise ElfAuditError(f"forbidden bundled SONAME {forbidden}: {record['relative_path']}")
        soname = record["_soname"]
        if len(record["_sonames"]) > 1:
            raise ElfAuditError(f"ambiguous SONAME declarations: {record['relative_path']}")
        expected_electron = electron_files.get(record["relative_path"])
        if soname and soname not in private_allowed and not (
            expected_electron is not None and soname == expected_electron["soname"]
        ):
            raise ElfAuditError(
                f"non-allowlisted bundled SONAME {soname}: {record['relative_path']}"
            )
        if soname:
            root_sonames.setdefault(soname, []).append(record["relative_path"])
        records.append(record)

    electron_companion_sonames = _validate_electron_distribution(records, policy)

    for soname, paths in root_sonames.items():
        if len(paths) > 1:
            raise ElfAuditError(f"ambiguous SONAME {soname}: {', '.join(sorted(paths))}")

    private_sonames: set[str] = set()
    external_sonames: set[str] = set()
    available_sonames = set(root_sonames)
    for record in records:
        for soname in record["needed"]:
            if soname in electron_companion_sonames:
                candidates = root_sonames.get(soname, [])
                if len(candidates) != 1:
                    raise ElfAuditError(
                        f"unresolved Electron companion SONAME {soname}: "
                        f"{record['relative_path']}"
                    )
                continue
            if soname in private_allowed:
                private_sonames.add(soname)
                candidates = root_sonames.get(soname, [])
                if not candidates:
                    candidates = [str(path) for path in _sysroot_soname_candidates(sysroot, soname)]
                if not candidates:
                    raise ElfAuditError(f"unresolved private SONAME {soname}: {record['relative_path']}")
                if len(candidates) > 1:
                    raise ElfAuditError(f"ambiguous private SONAME {soname}: {record['relative_path']}")
                continue
            external_sonames.add(soname)
            if soname in available_sonames:
                raise ElfAuditError(
                    f"external SONAME is bundled in payload and must remain system-provided {soname}: "
                    f"{record['relative_path']}"
                )
            if soname in required_system or soname in _KNOWN_EXTERNAL_SONAMES:
                continue
            candidates = _sysroot_soname_candidates(sysroot, soname)
            if len(candidates) == 0:
                raise ElfAuditError(f"unresolved SONAME {soname}: {record['relative_path']}")
            if len(candidates) > 1:
                raise ElfAuditError(f"ambiguous SONAME {soname}: {record['relative_path']}")

    files = [
        {
            "relative_path": record["relative_path"],
            "sha256": record["sha256"],
            "machine": record["machine"],
            "needed": record["needed"],
            "runpath": record["runpath"],
            "rpath": record["_rpath"],
            "version_needs": record["version_needs"],
        }
        for record in sorted(records, key=lambda item: item["relative_path"])
    ]
    return {
        "schema": SCHEMA,
        "policy_id": policy["policy_id"],
        "compatibility_policy_sha256": policy_sha256(policy),
        "max_required_versions": dict(policy["elf"]["maximum_symbol_versions"]),
        "external_sonames": sorted(external_sonames),
        "electron_companion_sonames": sorted(electron_companion_sonames),
        "private_sonames": sorted(private_sonames),
        "files": files,
    }


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
            raise ElfAuditError(f"cannot inspect report directory: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            if metadata.st_uid != 0:
                raise ElfAuditError(f"report directory contains untrusted symlink: {current}")
            try:
                current = current.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise ElfAuditError(f"cannot resolve report directory symlink: {current}") from exc


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
            raise ElfAuditError(f"report directory resolves through symlink: {path}")
        return os.open(
            canonical_path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except ElfAuditError:
        raise
    except OSError as exc:
        raise ElfAuditError(f"cannot open report directory safely: {path}") from exc


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
            raise ElfAuditError("cannot allocate a private report temporary file")
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
    except ElfAuditError:
        raise
    except OSError as exc:
        raise ElfAuditError(f"atomic report write failed: {path}") from exc
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
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sysroot", type=Path)
    args = parser.parse_args(argv)
    policy = load_policy(args.policy)
    report = audit_root(args.root, policy, args.sysroot)
    write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ElfAuditError as exc:
        print(f"ELF ABI audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
