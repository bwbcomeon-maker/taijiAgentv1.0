#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import BinaryIO, Iterator, Optional, Tuple


EXPECTED_ELECTRON_VERSION = "39.8.10"
PRUNED_DIRECTORY_NAMES = {"__tests__", "docs", "test", "tests"}
MAX_ELECTRON_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ELECTRON_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


class ElectronRuntimeStageError(RuntimeError):
    pass


def is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def assert_safe_symlinks(root: Path, *, label: str) -> None:
    resolved_root = root.resolve(strict=True)
    for candidate in root.rglob("*"):
        if not candidate.is_symlink():
            continue
        raw_target = Path(os.readlink(candidate))
        if raw_target.is_absolute():
            raise ElectronRuntimeStageError(f"{label} contains an absolute symlink: {candidate}")
        resolved_target = (candidate.parent / raw_target).resolve(strict=False)
        if not is_within(resolved_root, resolved_target):
            raise ElectronRuntimeStageError(f"{label} contains an escaping symlink: {candidate}")
        if not resolved_target.exists():
            raise ElectronRuntimeStageError(f"{label} contains a dangling symlink: {candidate}")


def is_dev_only_name(name: str) -> bool:
    lower = name.lower()
    return (
        lower in PRUNED_DIRECTORY_NAMES
        or lower.startswith("readme")
        or lower.endswith(".d.ts")
        or lower.endswith(".map")
        or name.startswith("._")
        or lower in {".ds_store", ".npmignore"}
    )


def ignore_dev_only(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if is_dev_only_name(name)}


def load_electron_contract(policy_path: Path) -> tuple[str, str, str]:
    helper_path = Path(__file__).with_name("compatibility_policy.py")
    spec = importlib.util.spec_from_file_location(
        "taiji_electron_runtime_compatibility_policy",
        helper_path,
    )
    if spec is None or spec.loader is None:
        raise ElectronRuntimeStageError("cannot load compatibility policy helper")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        policy = module.load_and_validate(policy_path)
    except Exception as exc:
        raise ElectronRuntimeStageError(f"compatibility policy is invalid: {exc}") from exc
    distribution = policy["elf"]["electron_distribution"]
    executable = distribution["elf_files"].get(
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
    )
    if type(executable) is not dict or type(executable.get("sha256")) is not str:
        raise ElectronRuntimeStageError(
            "compatibility policy is missing the packaged Electron executable identity"
        )
    return distribution["version"], distribution["archive_sha256"], executable["sha256"]


def _sha256_file_digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def sha256_regular_file(path: Path, label: str) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ElectronRuntimeStageError(f"{label} cannot be inspected: {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ElectronRuntimeStageError(f"{label} must be a single-link regular file: {path}")
    if metadata.st_size <= 0 or metadata.st_size > MAX_ELECTRON_ARCHIVE_BYTES:
        raise ElectronRuntimeStageError(f"{label} size is invalid: {metadata.st_size}")
    return _sha256_file_digest(path).hex()


def _sha256_open_file(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


@contextmanager
def verified_archive_snapshot(
    path: Path,
    *,
    expected_sha256: str,
    directory: Path,
) -> Iterator[Tuple[BinaryIO, str]]:
    """Copy one safely opened archive into an anonymous immutable-by-path snapshot."""
    try:
        before = path.lstat()
    except OSError as exc:
        raise ElectronRuntimeStageError(f"Electron archive cannot be inspected: {path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ElectronRuntimeStageError(
            f"Electron archive must be a single-link regular file: {path}"
        )
    if before.st_size <= 0 or before.st_size > MAX_ELECTRON_ARCHIVE_BYTES:
        raise ElectronRuntimeStageError(f"Electron archive size is invalid: {before.st_size}")

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ElectronRuntimeStageError(f"Electron archive cannot be opened safely: {exc}") from exc

    try:
        with os.fdopen(descriptor, "rb", closefd=True) as source_handle, tempfile.TemporaryFile(
            mode="w+b",
            prefix=".taiji-electron-archive-",
            dir=str(directory),
        ) as snapshot:
            descriptor = -1
            opened = os.fstat(source_handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise ElectronRuntimeStageError("Electron archive identity changed while opening")

            digest = hashlib.sha256()
            copied = 0
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                copied += len(chunk)
                if copied > MAX_ELECTRON_ARCHIVE_BYTES:
                    raise ElectronRuntimeStageError("Electron archive size is excessive")
                digest.update(chunk)
                snapshot.write(chunk)
            after_open = os.fstat(source_handle.fileno())
            try:
                after_path = path.lstat()
            except OSError as exc:
                raise ElectronRuntimeStageError(
                    "Electron archive path changed while creating the private snapshot"
                ) from exc
            if (
                copied != opened.st_size
                or (after_open.st_dev, after_open.st_ino, after_open.st_size)
                != (opened.st_dev, opened.st_ino, opened.st_size)
                or (after_path.st_dev, after_path.st_ino, after_path.st_size)
                != (opened.st_dev, opened.st_ino, opened.st_size)
            ):
                raise ElectronRuntimeStageError(
                    "Electron archive changed while creating the private snapshot"
                )
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise ElectronRuntimeStageError(
                    "Electron archive SHA256 does not match the canonical compatibility policy"
                )
            snapshot.flush()
            snapshot.seek(0)
            yield snapshot, actual_sha256
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def validate_archive_fd_identity(descriptor: int, info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise ElectronRuntimeStageError("Electron archive FD is not a regular file")
    if info.st_size <= 0 or info.st_size > MAX_ELECTRON_ARCHIVE_BYTES:
        raise ElectronRuntimeStageError("Electron archive FD size is invalid")
    if info.st_nlink == 1:
        return
    if info.st_nlink != 0:
        raise ElectronRuntimeStageError(
            "Electron archive FD is neither single-link nor a fully sealed memfd"
        )
    try:
        required_seals = (
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL
        )
        actual_seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
    except (AttributeError, OSError) as exc:
        raise ElectronRuntimeStageError(
            "Electron archive FD is not a fully sealed memfd"
        ) from exc
    if actual_seals & required_seals != required_seals:
        raise ElectronRuntimeStageError(
            "Electron archive FD is not a fully sealed memfd"
        )


@contextmanager
def verified_archive_fd_snapshot(
    descriptor: int,
    *,
    basename: str,
    expected_sha256: str,
    directory: Path,
) -> Iterator[Tuple[BinaryIO, str]]:
    """Adopt an already-fixed archive descriptor without reopening a path."""
    if not isinstance(descriptor, int) or descriptor < 3 or not basename:
        raise ElectronRuntimeStageError("Electron archive FD contract is invalid")
    source_descriptor = -1
    try:
        source_descriptor = os.dup(descriptor)
        with os.fdopen(source_descriptor, "rb", closefd=True) as source, tempfile.TemporaryFile(
            mode="w+b", prefix=".taiji-electron-archive-", dir=str(directory)
        ) as snapshot:
            source_descriptor = -1
            info = os.fstat(source.fileno())
            validate_archive_fd_identity(source.fileno(), info)
            digest = hashlib.sha256()
            source.seek(0)
            copied = 0
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                copied += len(chunk)
                if copied > MAX_ELECTRON_ARCHIVE_BYTES:
                    raise ElectronRuntimeStageError("Electron archive size is excessive")
                digest.update(chunk)
                snapshot.write(chunk)
            actual = digest.hexdigest()
            if actual != expected_sha256:
                raise ElectronRuntimeStageError("Electron archive SHA256 does not match policy")
            snapshot.flush()
            snapshot.seek(0)
            yield snapshot, actual
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)


def _runtime_file_inventory(dist: Path) -> dict[str, Path]:
    inventory: dict[str, Path] = {}
    for candidate in sorted(dist.rglob("*")):
        relative = candidate.relative_to(dist)
        if any(is_dev_only_name(part) for part in relative.parts):
            continue
        if candidate.is_symlink() or (candidate.exists() and not candidate.is_file() and not candidate.is_dir()):
            raise ElectronRuntimeStageError(
                f"installed Electron dist contains an unsupported node: {relative.as_posix()}"
            )
        if candidate.is_file():
            metadata = candidate.stat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ElectronRuntimeStageError(
                    f"installed Electron dist file is not a single-link regular file: {relative.as_posix()}"
                )
            inventory[relative.as_posix()] = candidate
    return inventory


def _safe_archive_member_name(info: zipfile.ZipInfo) -> str:
    raw = info.filename
    path = PurePosixPath(raw.rstrip("/"))
    if (
        not raw
        or "\\" in raw
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ElectronRuntimeStageError(f"Electron archive contains an unsafe path: {raw!r}")
    return path.as_posix()


def validate_archive_matches_dist(
    source: Path,
    archive_basename: str,
    archive: BinaryIO,
    *,
    expected_version: str,
    expected_archive_sha256: str,
) -> str:
    expected_basename = f"electron-v{expected_version}-linux-x64.zip"
    if archive_basename != expected_basename:
        raise ElectronRuntimeStageError(
            f"Electron archive basename must be {expected_basename}, got {archive_basename}"
        )
    actual_sha256 = _sha256_open_file(archive)
    if actual_sha256 != expected_archive_sha256:
        raise ElectronRuntimeStageError(
            "Electron archive SHA256 does not match the canonical compatibility policy"
        )

    source_files = _runtime_file_inventory(source / "dist")
    archive_files: dict[str, zipfile.ZipInfo] = {}
    uncompressed_bytes = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                relative = _safe_archive_member_name(info)
                if info.is_dir() or any(is_dev_only_name(part) for part in PurePosixPath(relative).parts):
                    continue
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ElectronRuntimeStageError(
                        f"Electron archive contains an unsupported symlink: {relative}"
                    )
                if relative in archive_files:
                    raise ElectronRuntimeStageError(
                        f"Electron archive contains a duplicate member: {relative}"
                    )
                uncompressed_bytes += info.file_size
                if uncompressed_bytes > MAX_ELECTRON_UNCOMPRESSED_BYTES:
                    raise ElectronRuntimeStageError("Electron archive uncompressed size is excessive")
                archive_files[relative] = info

            if set(source_files) != set(archive_files):
                missing = sorted(set(archive_files) - set(source_files))
                extra = sorted(set(source_files) - set(archive_files))
                detail = []
                if missing:
                    detail.append("missing=" + ",".join(missing[:5]))
                if extra:
                    detail.append("extra=" + ",".join(extra[:5]))
                raise ElectronRuntimeStageError(
                    "installed Electron dist inventory differs from the fixed archive: "
                    + "; ".join(detail)
                )

            for relative, source_file in source_files.items():
                source_digest = _sha256_file_digest(source_file)
                archive_digest = hashlib.sha256()
                with bundle.open(archive_files[relative], "r") as archived:
                    for chunk in iter(lambda: archived.read(1024 * 1024), b""):
                        archive_digest.update(chunk)
                if source_digest != archive_digest.digest():
                    raise ElectronRuntimeStageError(
                        f"archive member differs from installed Electron dist: {relative}"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ElectronRuntimeStageError(f"Electron archive cannot be validated: {exc}") from exc
    return actual_sha256


def validate_source(
    source: Path,
    *,
    expected_version: str,
    require_linux_x86_64: bool,
) -> dict[str, object]:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ElectronRuntimeStageError(f"Electron source is not a directory: {source}")
    assert_safe_symlinks(source, label="Electron source")
    package_path = source / "package.json"
    if package_path.is_symlink() or not package_path.is_file():
        raise ElectronRuntimeStageError("Electron source is missing regular package.json")
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ElectronRuntimeStageError("Electron package.json is invalid") from exc
    if package.get("version") != expected_version:
        raise ElectronRuntimeStageError(
            f"Electron version must be {expected_version}, got {package.get('version')}"
        )
    dist = source / "dist"
    if dist.is_symlink() or not dist.is_dir():
        raise ElectronRuntimeStageError("Electron source is missing regular dist directory")
    electron = dist / "electron"
    if electron.is_symlink() or not electron.is_file():
        raise ElectronRuntimeStageError("Electron source is missing regular dist/electron")
    if stat.S_IMODE(electron.stat().st_mode) & 0o111 == 0:
        raise ElectronRuntimeStageError("Electron dist/electron is not executable")
    if require_linux_x86_64:
        header = electron.read_bytes()[:20]
        if (
            len(header) < 20
            or header[:4] != b"\x7fELF"
            or header[4] != 2
            or header[5] != 1
            or int.from_bytes(header[18:20], "little") != 62
        ):
            raise ElectronRuntimeStageError("Electron dist/electron is not Linux x86_64 ELF")
    return package


def validate_staged_runtime(destination: Path) -> None:
    required_files = (
        "package.json",
        "dist/electron",
        "dist/icudtl.dat",
        "dist/resources.pak",
        "dist/snapshot_blob.bin",
        "dist/v8_context_snapshot.bin",
        "dist/resources/default_app.asar",
    )
    for relative in required_files:
        candidate = destination / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise ElectronRuntimeStageError(f"staged Electron runtime is missing {relative}")
    locales = destination / "dist/locales"
    if locales.is_symlink() or not locales.is_dir() or not any(locales.glob("*.pak")):
        raise ElectronRuntimeStageError("staged Electron runtime has no locale .pak files")
    for candidate in destination.rglob("*"):
        if is_dev_only_name(candidate.name):
            raise ElectronRuntimeStageError(
                f"staged Electron runtime contains a development-only path: {candidate}"
            )
    assert_safe_symlinks(destination, label="staged Electron runtime")
    (destination / "dist/electron").chmod(0o755)


def extract_fixed_archive(archive: BinaryIO, destination_dist: Path) -> None:
    """Extract the audited ZIP without trusting zipfile.extract path handling."""
    extracted_bytes = 0
    archive.seek(0)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            relative = _safe_archive_member_name(info)
            if any(is_dev_only_name(part) for part in PurePosixPath(relative).parts):
                continue
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ElectronRuntimeStageError(
                    f"Electron archive contains an unsupported symlink: {relative}"
                )
            target = destination_dist.joinpath(*PurePosixPath(relative).parts)
            if info.is_dir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            if mode and not stat.S_ISREG(mode):
                raise ElectronRuntimeStageError(
                    f"Electron archive contains an unsupported member type: {relative}"
                )
            extracted_bytes += info.file_size
            if extracted_bytes > MAX_ELECTRON_UNCOMPRESSED_BYTES:
                raise ElectronRuntimeStageError("Electron archive uncompressed size is excessive")
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            try:
                with bundle.open(info, "r") as source_handle, target.open("xb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            except FileExistsError as exc:
                raise ElectronRuntimeStageError(
                    f"Electron archive contains a duplicate member: {relative}"
                ) from exc
            target.chmod(0o755 if mode & 0o111 else 0o644)


def stage_electron_runtime(
    source: Path,
    destination: Path,
    *,
    archive: Optional[Path],
    archive_fd: Optional[int] = None,
    archive_basename: Optional[str] = None,
    expected_version: str,
    expected_archive_sha256: str,
    expected_executable_sha256: str,
    require_linux_x86_64: bool,
) -> dict[str, object]:
    source = source.expanduser().resolve(strict=True)
    package = validate_source(
        source,
        expected_version=expected_version,
        require_linux_x86_64=require_linux_x86_64,
    )
    if (archive is None) == (archive_fd is None):
        raise ElectronRuntimeStageError("exactly one Electron archive source is required")
    if archive_fd is None:
        archive = archive.expanduser().absolute()
    destination = destination.expanduser().absolute()
    shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot_context = (
        verified_archive_fd_snapshot(
            archive_fd,
            basename=archive_basename or "",
            expected_sha256=expected_archive_sha256,
            directory=destination.parent,
        )
        if archive_fd is not None
        else verified_archive_snapshot(
            archive,
            expected_sha256=expected_archive_sha256,
            directory=destination.parent,
        )
    )
    with snapshot_context as (archive_snapshot, archive_sha256):
        archive_name = archive_basename if archive_fd is not None else archive.name
        archive_sha256 = validate_archive_matches_dist(
            source,
            archive_name,
            archive_snapshot,
            expected_version=expected_version,
            expected_archive_sha256=expected_archive_sha256,
        )
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
        )
        try:
            (temporary / "package.json").write_text(
                json.dumps(
                    {"name": "electron", "version": expected_version},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            (temporary / "package.json").chmod(0o644)
            (temporary / "dist").mkdir(mode=0o755)
            extract_fixed_archive(archive_snapshot, temporary / "dist")
            validate_staged_runtime(temporary)
            executable_sha256 = _sha256_file_digest(temporary / "dist/electron").hex()
            if executable_sha256 != expected_executable_sha256:
                raise ElectronRuntimeStageError(
                    "Electron executable SHA256 does not match the canonical compatibility policy"
                )
            if _sha256_open_file(archive_snapshot) != archive_sha256:
                raise ElectronRuntimeStageError(
                    "private Electron archive snapshot changed during staging"
                )
            os.rename(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    return {
        "ok": True,
        "electron_version": package["version"],
        "electron_archive_sha256": archive_sha256,
        "electron_executable_sha256": executable_sha256,
        "runtime_root": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage the audited Taiji Electron runtime")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    archive_group = parser.add_mutually_exclusive_group(required=True)
    archive_group.add_argument("--archive")
    archive_group.add_argument("--archive-fd", type=int)
    parser.add_argument("--archive-basename")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--require-linux-x86-64", action="store_true")
    args = parser.parse_args()
    try:
        (
            expected_version,
            expected_archive_sha256,
            expected_executable_sha256,
        ) = load_electron_contract(Path(args.policy))
        result = stage_electron_runtime(
            Path(args.source),
            Path(args.destination),
            archive=Path(args.archive) if args.archive else None,
            archive_fd=args.archive_fd,
            archive_basename=args.archive_basename,
            expected_version=expected_version,
            expected_archive_sha256=expected_archive_sha256,
            expected_executable_sha256=expected_executable_sha256,
            require_linux_x86_64=bool(args.require_linux_x86_64),
        )
    except (OSError, ValueError, ElectronRuntimeStageError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
