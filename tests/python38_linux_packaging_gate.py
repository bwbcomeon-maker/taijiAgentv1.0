#!/usr/bin/env python3
"""Run the Linux packaging helpers under the Kylin build-host Python baseline."""

from __future__ import annotations

import py_compile
import runpy
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_STAGER = ROOT / "packaging/linux/stage-python-runtime.py"
COMPONENT_STAGER = ROOT / "packaging/linux/stage-runtime-components.py"
PYTHON38_ENTRYPOINTS = (
    ROOT / "packaging/linux/compatibility_policy.py",
    ROOT / "packaging/linux/trusted_system_tools.py",
    ROOT / "packaging/linux/stage-private-libraries.py",
    ROOT / "packaging/linux/audit-elf-closure.py",
    ROOT / "packaging/linux/validate_icon_assets.py",
    ROOT / "packaging/linux/stage-electron-runtime.py",
    PYTHON_STAGER,
    COMPONENT_STAGER,
    ROOT / "scripts/produce-taiji-offline-rehearsal.py",
    ROOT / "scripts/assemble-taiji-certification-set.py",
    ROOT / "scripts/validate-taiji-release-evidence.py",
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="taiji-python38-gate-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, entrypoint in enumerate(PYTHON38_ENTRYPOINTS):
            py_compile.compile(
                str(entrypoint),
                cfile=str(temp_root / f"entrypoint-{index}.pyc"),
                doraise=True,
            )

        loaded_entrypoints = {
            entrypoint: runpy.run_path(str(entrypoint))
            for entrypoint in PYTHON38_ENTRYPOINTS
        }

        python_stager = loaded_entrypoints[PYTHON_STAGER]
        is_tcl_tk = python_stager["_is_tcl_tk_library_name"]
        assert is_tcl_tk("thread3.1")
        assert not is_tcl_tk("threading.py")

        runtime = temp_root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        site_packages = runtime / "lib/python3.11/site-packages"
        site_packages.mkdir(parents=True)
        python = runtime / "bin/python"
        python.write_bytes(b"\x7fELFpython")
        consumer = site_packages / "native_consumer.so"
        consumer.write_bytes(b"\x7fELFconsumer")
        libpython = runtime / "lib/libpython3.11.so.1.0"
        libpython.write_bytes(b"\x7fELFlibpython")

        inspected = []

        def inspect_needed(path):
            inspected.append(path.relative_to(runtime).as_posix())
            if path == consumer:
                return {"libpython3.11.so.1.0"}
            return {"libc.so.6"}

        prune_libpython = python_stager["prune_unneeded_libpython_stubs"]
        prune_libpython.__globals__["_inspect_elf_needed_libraries"] = inspect_needed
        try:
            prune_libpython(runtime, "3.11")
        except python_stager["PythonRuntimeStageError"] as exc:
            assert "native_consumer.so" in str(exc)
        else:
            raise AssertionError("libpython dependency guard did not fail closed")
        assert libpython.is_file()
        assert set(inspected) == {"bin/python", "lib/python3.11/site-packages/native_consumer.so"}

        component_stager = loaded_entrypoints[COMPONENT_STAGER]
        ignored = component_stager["docx_node_modules_ignored"](
            "/tmp/node_modules/@resvg",
            [
                "resvg-js",
                "resvg-js-linux-x64-gnu",
                "resvg-js-linux-arm64-gnu",
                "resvg-js-darwin-arm64",
            ],
        )
        assert "resvg-js-linux-x64-gnu" not in ignored
        assert "resvg-js-linux-arm64-gnu" in ignored
        assert "resvg-js-darwin-arm64" in ignored

    print("python38-linux-packaging-gate-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
