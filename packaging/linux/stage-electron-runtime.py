#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path


EXPECTED_ELECTRON_VERSION = "39.8.10"
PRUNED_DIRECTORY_NAMES = {"__tests__", "docs", "test", "tests"}


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


def validate_source(source: Path, *, require_linux_x86_64: bool) -> dict[str, object]:
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
    if package.get("version") != EXPECTED_ELECTRON_VERSION:
        raise ElectronRuntimeStageError(
            f"Electron version must be {EXPECTED_ELECTRON_VERSION}, got {package.get('version')}"
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


def stage_electron_runtime(
    source: Path,
    destination: Path,
    *,
    require_linux_x86_64: bool,
) -> dict[str, object]:
    source = source.expanduser().resolve(strict=True)
    package = validate_source(source, require_linux_x86_64=require_linux_x86_64)
    destination = destination.expanduser().absolute()
    shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
        shutil.copy2(source / "package.json", destination / "package.json")
        shutil.copytree(
            source / "dist",
            destination / "dist",
            symlinks=True,
            ignore=ignore_dev_only,
        )
        validate_staged_runtime(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {
        "ok": True,
        "electron_version": package["version"],
        "runtime_root": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage the audited Taiji Electron runtime")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--require-linux-x86-64", action="store_true")
    args = parser.parse_args()
    try:
        result = stage_electron_runtime(
            Path(args.source),
            Path(args.destination),
            require_linux_x86_64=bool(args.require_linux_x86_64),
        )
    except (OSError, ValueError, ElectronRuntimeStageError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
