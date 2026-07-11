#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


class PythonRuntimeStageError(RuntimeError):
    pass


def is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def inspect_source_python(source_python: Path) -> dict[str, str]:
    code = (
        "import json,platform,sys,sysconfig;"
        "print(json.dumps({"
        "'base_prefix':sys.base_prefix,'prefix':sys.prefix,'executable':sys.executable,"
        "'stdlib':sysconfig.get_path('stdlib'),'purelib':sysconfig.get_path('purelib'),"
        "'version':platform.python_version(),"
        "'major_minor':f'{sys.version_info.major}.{sys.version_info.minor}',"
        "'machine':platform.machine(),'platform':sys.platform}))"
    )
    completed = subprocess.run(
        [str(source_python), "-I", "-c", code],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise PythonRuntimeStageError(
            f"source Python inspection failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PythonRuntimeStageError("source Python inspection did not return JSON") from exc
    required = {
        "base_prefix",
        "prefix",
        "executable",
        "stdlib",
        "purelib",
        "version",
        "major_minor",
        "machine",
        "platform",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise PythonRuntimeStageError("source Python inspection is incomplete")
    return {key: str(payload[key]) for key in required}


def validate_managed_base(
    source_venv: Path,
    source_python: Path,
    info: dict[str, str],
    *,
    require_linux_x86_64: bool,
) -> tuple[Path, Path, Path]:
    resolved_venv = source_venv.resolve(strict=True)
    base_root = Path(info["base_prefix"]).expanduser().resolve(strict=True)
    stdlib = Path(info["stdlib"]).expanduser().resolve(strict=True)
    purelib = Path(info["purelib"]).expanduser().resolve(strict=True)
    resolved_python = source_python.resolve(strict=True)

    if not (base_root / "BUILD").is_file():
        raise PythonRuntimeStageError(
            f"Python base is not a uv-managed standalone runtime (missing BUILD): {base_root}"
        )
    if not is_within(base_root, resolved_python):
        raise PythonRuntimeStageError(f"source Python executable is outside its managed base: {resolved_python}")
    if not is_within(base_root, stdlib) or not (stdlib / "encodings/__init__.py").is_file():
        raise PythonRuntimeStageError(f"managed Python stdlib is incomplete or external: {stdlib}")
    if not is_within(resolved_venv, purelib) or not purelib.is_dir():
        raise PythonRuntimeStageError(f"source venv site-packages is outside the venv: {purelib}")
    if require_linux_x86_64:
        if info["platform"] != "linux" or info["machine"].lower() not in {"x86_64", "amd64"}:
            raise PythonRuntimeStageError(
                f"packaged Python must be Linux x86_64, got {info['platform']} {info['machine']}"
            )
        header = resolved_python.read_bytes()[:20]
        if (
            len(header) < 20
            or header[:4] != b"\x7fELF"
            or header[4] != 2
            or header[5] != 1
            or int.from_bytes(header[18:20], "little") != 62
        ):
            raise PythonRuntimeStageError("packaged Python is not a 64-bit little-endian x86_64 ELF")
    return base_root, purelib, resolved_venv


def assert_safe_symlinks(root: Path, *, label: str) -> None:
    resolved_root = root.resolve(strict=True)
    for candidate in root.rglob("*"):
        if not candidate.is_symlink():
            continue
        raw_target = Path(os.readlink(candidate))
        if raw_target.is_absolute():
            raise PythonRuntimeStageError(f"{label} contains an absolute symlink: {candidate}")
        resolved_target = (candidate.parent / raw_target).resolve(strict=False)
        if not is_within(resolved_root, resolved_target):
            raise PythonRuntimeStageError(f"{label} contains an escaping symlink: {candidate}")
        if not resolved_target.exists():
            raise PythonRuntimeStageError(f"{label} contains a dangling symlink: {candidate}")


def copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lower = name.lower()
        if (
            lower in {".ds_store", ".pytest_cache", "__pycache__"}
            or name.startswith("._")
            or lower.endswith(".pyc")
        ):
            ignored.add(name)
    return ignored


def remove_editable_metadata(site_packages: Path) -> None:
    for candidate in site_packages.iterdir():
        lower = candidate.name.lower()
        if candidate.name.startswith("__editable__") or "editable" in lower or "hermes" in lower:
            if candidate.is_dir() and not candidate.is_symlink():
                shutil.rmtree(candidate)
            else:
                candidate.unlink(missing_ok=True)


def make_python_entrypoint_regular(destination: Path, base_root: Path, source_python: Path) -> None:
    relative_binary = source_python.resolve(strict=True).relative_to(base_root)
    staged_binary = destination / relative_binary
    if staged_binary.is_symlink() or not staged_binary.is_file():
        raise PythonRuntimeStageError(f"staged versioned Python binary is missing: {staged_binary}")
    entrypoint = destination / "bin/python"
    if entrypoint.exists() or entrypoint.is_symlink():
        entrypoint.unlink()
    shutil.copy2(staged_binary, entrypoint, follow_symlinks=True)
    entrypoint.chmod(stat.S_IMODE(staged_binary.stat().st_mode) | 0o111)


def prune_base_command_scripts(destination: Path, major_minor: str) -> None:
    bin_dir = destination / "bin"
    allowed = {"python", "python3", f"python{major_minor}"}
    for candidate in bin_dir.iterdir():
        if candidate.name in allowed:
            continue
        if candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate)
        else:
            candidate.unlink(missing_ok=True)


def normalize_managed_base_paths(destination: Path, base_root: Path, source_platform: str) -> None:
    marker = str(base_root).encode("utf-8")
    installed_prefix = b"/opt/taiji-agent/runtime/agent/venv"
    for candidate in destination.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        content = candidate.read_bytes()
        if marker not in content:
            continue
        if b"\0" not in content:
            candidate.write_bytes(content.replace(marker, installed_prefix))
            continue
        if source_platform == "darwin" and candidate.suffix == ".dylib":
            completed = subprocess.run(
                ["install_name_tool", "-id", f"@rpath/{candidate.name}", str(candidate)],
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode == 0 and marker not in candidate.read_bytes():
                continue
        raise PythonRuntimeStageError(
            f"managed Python binary contains a non-relocatable build-machine path: {candidate}"
        )


def assert_no_source_paths(root: Path, forbidden_paths: Iterable[Path]) -> None:
    forbidden = {str(path).encode("utf-8") for path in forbidden_paths if str(path)}
    forbidden.discard(b"")
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        overlap = b""
        with candidate.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                window = overlap + block
                for marker in forbidden:
                    if marker in window:
                        raise PythonRuntimeStageError(
                            f"staged Python runtime contains a build-machine path: {candidate}"
                        )
                longest = max((len(marker) for marker in forbidden), default=1)
                overlap = window[-(longest - 1) :] if longest > 1 else b""


def run_relocation_smoke(
    destination: Path,
    *,
    smoke_imports: list[str],
    forbidden_paths: Iterable[Path],
) -> dict[str, Any]:
    smoke_parent = Path(
        tempfile.mkdtemp(prefix=".taiji-python-relocation-smoke-", dir=destination.parent)
    )
    relocated = smoke_parent / "python-runtime"
    destination.rename(relocated)
    try:
        import_lines = ";".join(f"import {name}" for name in smoke_imports)
        code = (
            f"{import_lines};" if import_lines else ""
        ) + (
            "import json,sys,sysconfig;"
            "print(json.dumps({'base_prefix':sys.base_prefix,'prefix':sys.prefix,"
            "'stdlib':sysconfig.get_path('stdlib'),'purelib':sysconfig.get_path('purelib'),"
            "'sys_path':sys.path}))"
        )
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"
        completed = subprocess.run(
            [str(relocated / "bin/python"), "-I", "-c", code],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            raise PythonRuntimeStageError(
                "relocated Python smoke test failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        payload = json.loads(completed.stdout)
        relocated_root = relocated.resolve(strict=True)
        for key in ("base_prefix", "prefix", "stdlib", "purelib"):
            value = Path(str(payload.get(key) or "")).resolve(strict=False)
            if value != relocated_root and not is_within(relocated_root, value):
                raise PythonRuntimeStageError(
                    f"relocated Python {key} escapes packaged runtime: {payload.get(key)}"
                )
        serialized = json.dumps(payload).encode("utf-8")
        for forbidden_path in forbidden_paths:
            if str(forbidden_path).encode("utf-8") in serialized:
                raise PythonRuntimeStageError(
                    f"relocated Python still exposes build-machine path: {forbidden_path}"
                )
        return payload
    finally:
        if relocated.exists() and not destination.exists():
            relocated.rename(destination)
        shutil.rmtree(smoke_parent, ignore_errors=True)


def stage_python_runtime(
    source_venv: Path,
    destination: Path,
    *,
    smoke_imports: list[str],
    require_linux_x86_64: bool,
) -> dict[str, Any]:
    source_venv_arg = source_venv.expanduser().absolute()
    source_python = source_venv_arg / "bin/python"
    if not source_python.exists():
        raise PythonRuntimeStageError(f"source venv Python is missing: {source_python}")
    info = inspect_source_python(source_python)
    base_root, source_purelib, resolved_venv = validate_managed_base(
        source_venv_arg,
        source_python,
        info,
        require_linux_x86_64=require_linux_x86_64,
    )
    assert_safe_symlinks(base_root, label="uv-managed Python base")
    assert_safe_symlinks(source_purelib, label="source venv site-packages")

    destination = destination.expanduser().absolute()
    shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(base_root, destination, symlinks=True, ignore=copy_ignore)
        staged_site_packages = destination / "lib" / f"python{info['major_minor']}" / "site-packages"
        staged_site_packages.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source_purelib,
            staged_site_packages,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=copy_ignore,
        )
        remove_editable_metadata(staged_site_packages)
        (destination / "pyvenv.cfg").unlink(missing_ok=True)
        make_python_entrypoint_regular(destination, base_root, source_python)
        prune_base_command_scripts(destination, info["major_minor"])
        normalize_managed_base_paths(destination, base_root, info["platform"])
        assert_safe_symlinks(destination, label="staged Python runtime")
        forbidden_paths = {
            base_root,
            source_venv_arg,
            resolved_venv,
            Path(info["prefix"]),
            Path(info["executable"]),
        }
        assert_no_source_paths(destination, forbidden_paths)
        smoke = run_relocation_smoke(
            destination,
            smoke_imports=smoke_imports,
            forbidden_paths=forbidden_paths,
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {
        "ok": True,
        "python_version": info["version"],
        "runtime_root": str(destination),
        "smoke_imports": smoke_imports,
        "relocation": smoke,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage a self-contained relocatable Taiji Python runtime")
    parser.add_argument("--source-venv", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--smoke-import", action="append", default=[])
    parser.add_argument("--require-linux-x86-64", action="store_true")
    args = parser.parse_args()
    try:
        result = stage_python_runtime(
            Path(args.source_venv),
            Path(args.destination),
            smoke_imports=[str(item) for item in args.smoke_import],
            require_linux_x86_64=bool(args.require_linux_x86_64),
        )
    except (OSError, ValueError, json.JSONDecodeError, PythonRuntimeStageError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
