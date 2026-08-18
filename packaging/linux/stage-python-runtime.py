#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


_TRUSTED_READELF_CANDIDATES = (
    Path("/usr/bin/readelf"),
    Path("/bin/readelf"),
    Path("/usr/bin/x86_64-linux-gnu-readelf"),
    Path("/bin/x86_64-linux-gnu-readelf"),
)
_TRUSTED_READELF_DIRECTORIES = (Path("/usr/bin"), Path("/bin"))
_TRUSTED_TOOLS_MODULE = None


class PythonRuntimeStageError(RuntimeError):
    pass


def _trusted_tools_module():
    global _TRUSTED_TOOLS_MODULE
    if _TRUSTED_TOOLS_MODULE is not None:
        return _TRUSTED_TOOLS_MODULE
    module_path = Path(__file__).with_name("trusted_system_tools.py")
    spec = importlib.util.spec_from_file_location(
        "taiji_trusted_system_tools_for_python_stager",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise PythonRuntimeStageError(f"cannot load trusted system tool helper: {module_path}")
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
        raise PythonRuntimeStageError(str(exc)) from exc


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


def _remove_runtime_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _is_tcl_tk_library_name(name: str) -> bool:
    lower = name.lower()
    if lower.startswith(("tcl", "tk", "itcl", "tdbc", "libtcl", "libtk")):
        return True
    return lower.startswith("thread") and lower[len("thread") : len("thread") + 1].isdigit()


def prune_optional_tcl_tk_components(destination: Path, major_minor: str) -> None:
    stdlib = destination / "lib" / f"python{major_minor}"
    for relative in ("tkinter", "idlelib", "turtledemo", "turtle.py"):
        _remove_runtime_path(stdlib / relative)
    dynamic = stdlib / "lib-dynload"
    if dynamic.is_dir():
        for candidate in dynamic.glob("_tkinter.*"):
            _remove_runtime_path(candidate)

    for parent_name in ("lib", "share", "include"):
        parent = destination / parent_name
        if not parent.is_dir():
            continue
        for candidate in list(parent.iterdir()):
            if _is_tcl_tk_library_name(candidate.name):
                _remove_runtime_path(candidate)


def assert_no_optional_tcl_tk_components(destination: Path, major_minor: str) -> None:
    stdlib = destination / "lib" / f"python{major_minor}"
    forbidden: list[Path] = []
    for relative in ("tkinter", "idlelib", "turtledemo", "turtle.py"):
        candidate = stdlib / relative
        if candidate.exists() or candidate.is_symlink():
            forbidden.append(candidate)
    dynamic = stdlib / "lib-dynload"
    if dynamic.is_dir():
        forbidden.extend(dynamic.glob("_tkinter.*"))
    for parent_name in ("lib", "share", "include"):
        parent = destination / parent_name
        if not parent.is_dir():
            continue
        forbidden.extend(
            candidate for candidate in parent.iterdir() if _is_tcl_tk_library_name(candidate.name)
        )
    if forbidden:
        rendered = ", ".join(str(path.relative_to(destination)) for path in forbidden)
        raise PythonRuntimeStageError(f"staged Python runtime still contains Tcl/Tk components: {rendered}")


def _libpython_stub_candidates(destination: Path, major_minor: str) -> list[Path]:
    library_root = destination / "lib"
    if not library_root.is_dir():
        return []
    candidates: set[Path] = set()
    for pattern in ("libpython3.so*", f"libpython{major_minor}.so*"):
        candidates.update(
            path
            for path in library_root.glob(pattern)
            if path.is_file() or path.is_symlink()
        )
    return sorted(candidates, key=lambda path: path.name)


def _inspect_elf_needed_libraries(executable: Path) -> set[str]:
    try:
        completed = subprocess.run(
            [resolve_trusted_readelf(), "-d", str(executable)],
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as exc:
        raise PythonRuntimeStageError(
            f"trusted readelf could not execute for libpython inspection: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PythonRuntimeStageError(
            f"cannot inspect staged Python ELF dependencies: {detail or executable}"
        )
    return set(
        re.findall(
            r"\(NEEDED\)\s+Shared library:\s*\[([^\]]+)\]",
            completed.stdout,
        )
    )


def _staged_elf_consumers(destination: Path, excluded: set[Path]) -> list[Path]:
    consumers: list[Path] = []
    for candidate in destination.rglob("*"):
        if candidate in excluded or candidate.is_symlink() or not candidate.is_file():
            continue
        with candidate.open("rb") as handle:
            if handle.read(4) == b"\x7fELF":
                consumers.append(candidate)
    return sorted(consumers, key=lambda path: str(path.relative_to(destination)))


def prune_unneeded_libpython_stubs(destination: Path, major_minor: str) -> None:
    candidates = _libpython_stub_candidates(destination, major_minor)
    if not candidates:
        return
    executable = destination / "bin/python"
    if not executable.is_file() or executable.is_symlink():
        raise PythonRuntimeStageError(
            f"staged Python entrypoint is unavailable for libpython dependency inspection: {executable}"
        )
    consumers = _staged_elf_consumers(destination, set(candidates))
    if executable not in consumers:
        raise PythonRuntimeStageError(
            f"staged Python entrypoint is not an inspectable ELF consumer: {executable}"
        )
    dependent_consumers: list[str] = []
    for consumer in consumers:
        needed = _inspect_elf_needed_libraries(consumer)
        libpython_dependencies = sorted(
            name for name in needed if name.startswith("libpython") and ".so" in name
        )
        if libpython_dependencies:
            dependent_consumers.append(
                f"{consumer.relative_to(destination)} -> {', '.join(libpython_dependencies)}"
            )
    if dependent_consumers:
        raise PythonRuntimeStageError(
            "staged ELF consumer depends on libpython; refusing unsafe stub pruning: "
            + "; ".join(dependent_consumers)
        )
    for candidate in candidates:
        _remove_runtime_path(candidate)
    remaining = _libpython_stub_candidates(destination, major_minor)
    if remaining:
        rendered = ", ".join(str(path.relative_to(destination)) for path in remaining)
        raise PythonRuntimeStageError(f"staged Python runtime still contains libpython stubs: {rendered}")


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
        prune_unneeded_libpython_stubs(destination, info["major_minor"])
        prune_optional_tcl_tk_components(destination, info["major_minor"])
        assert_no_optional_tcl_tk_components(destination, info["major_minor"])
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
