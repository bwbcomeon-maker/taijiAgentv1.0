from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "packaging/linux/stage-python-runtime.py"
SOURCE_PYTHON = ROOT / "hermes-local-lab/sources/hermes-agent/venv/bin/python"
if not SOURCE_PYTHON.exists() and ROOT.parent.name == ".worktrees":
    SOURCE_PYTHON = (
        ROOT.parent.parent / "hermes-local-lab/sources/hermes-agent/venv/bin/python"
    )


def source_python_for_staging() -> Path:
    override = os.environ.get("TAIJI_AGENT_PYTHON")
    if override:
        return Path(override)
    return SOURCE_PYTHON


class LinuxPythonRuntimeStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="taiji-python-runtime-stage-"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.source_venv = self.temp_dir / "source-venv"
        self.destination = self.temp_dir / "payload/runtime/agent/venv"

        source_python = source_python_for_staging().resolve(strict=True)
        info = json.loads(
            subprocess.check_output(
                [
                    str(source_python),
                    "-c",
                    (
                        "import json,platform,sys;"
                        "print(json.dumps({'base_prefix':sys.base_prefix,"
                        "'version':platform.python_version(),"
                        "'major_minor':f'{sys.version_info.major}.{sys.version_info.minor}'}))"
                    ),
                ],
                text=True,
            )
        )
        self.base_root = Path(info["base_prefix"]).resolve(strict=True)
        self.major_minor = info["major_minor"]
        self.version = info["version"]
        self.assertTrue((self.base_root / "BUILD").is_file(), "fixture requires uv-managed standalone Python")

        (self.source_venv / "bin").mkdir(parents=True)
        site_packages = self.source_venv / "lib" / f"python{self.major_minor}" / "site-packages"
        site_packages.mkdir(parents=True)
        os.symlink(source_python, self.source_venv / "bin/python")
        os.symlink("python", self.source_venv / "bin/python3")
        (self.source_venv / "pyvenv.cfg").write_text(
            "\n".join(
                (
                    f"home = {self.base_root / 'bin'}",
                    "implementation = CPython",
                    f"version_info = {self.version}",
                    "include-system-site-packages = false",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (site_packages / "portable_fixture.py").write_text("VALUE = 'portable-ok'\n", encoding="utf-8")

    def test_staging_uses_taiji_agent_python_override(self) -> None:
        override = self.temp_dir / "ci-agent-python"
        with mock.patch.dict(os.environ, {"TAIJI_AGENT_PYTHON": str(override)}):
            self.assertEqual(override, source_python_for_staging())

    def test_absolute_uv_python_symlink_becomes_a_self_contained_relocatable_runtime(self) -> None:
        self.assertTrue((self.source_venv / "bin/python").is_symlink())
        self.assertTrue(Path(os.readlink(self.source_venv / "bin/python")).is_absolute())

        completed = subprocess.run(
            [
                "python3",
                str(STAGER),
                "--source-venv",
                str(self.source_venv),
                "--destination",
                str(self.destination),
                "--smoke-import",
                "portable_fixture",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        packaged_python = self.destination / "bin/python"
        self.assertTrue(packaged_python.is_file())
        self.assertFalse(packaged_python.is_symlink())
        self.assertTrue((self.destination / "lib" / f"python{self.major_minor}" / "encodings/__init__.py").is_file())
        self.assertTrue(
            (self.destination / "lib" / f"python{self.major_minor}" / "site-packages/portable_fixture.py").is_file()
        )
        for path in self.destination.rglob("*"):
            if path.is_symlink():
                self.assertFalse(Path(os.readlink(path)).is_absolute(), path)

        relocated = self.temp_dir / "moved-to-another-prefix/python-runtime"
        relocated.parent.mkdir(parents=True)
        self.destination.rename(relocated)
        smoke = subprocess.run(
            [
                str(relocated / "bin/python"),
                "-I",
                "-c",
                (
                    "import json,portable_fixture,sys,sysconfig;"
                    "print(json.dumps({'value':portable_fixture.VALUE,"
                    "'base_prefix':sys.base_prefix,'prefix':sys.prefix,"
                    "'stdlib':sysconfig.get_path('stdlib'),"
                    "'purelib':sysconfig.get_path('purelib'),'sys_path':sys.path}))"
                ),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(smoke.returncode, 0, smoke.stdout + smoke.stderr)
        payload = json.loads(smoke.stdout)
        self.assertEqual(payload["value"], "portable-ok")
        relocated_root = str(relocated.resolve())
        for key in ("base_prefix", "prefix", "stdlib", "purelib"):
            self.assertTrue(str(payload[key]).startswith(relocated_root), (key, payload[key]))
        serialized = json.dumps(payload)
        self.assertNotIn(str(self.base_root), serialized)
        self.assertNotIn(str(self.source_venv), serialized)

    def test_libpython_stubs_are_removed_only_after_python_dependency_inspection(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(STAGER.read_text(encoding="utf-8"), str(STAGER), "exec"), namespace)
        prune = namespace["prune_unneeded_libpython_stubs"]

        runtime = self.temp_dir / "libpython-prune-runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "lib").mkdir()
        python = runtime / "bin/python"
        python.write_bytes(b"\x7fELF" + b"fake-python")
        python.chmod(0o755)
        versioned = runtime / "lib" / f"libpython{self.major_minor}.so.1.0"
        versioned.write_bytes(b"unused-stub")
        os.symlink(versioned.name, runtime / "lib" / f"libpython{self.major_minor}.so")
        os.symlink(f"libpython{self.major_minor}.so", runtime / "lib/libpython3.so")

        fake_bin = self.temp_dir / "fake-readelf-no-libpython"
        fake_bin.mkdir()
        readelf = fake_bin / "readelf"
        readelf.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' ' 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]'\n",
            encoding="utf-8",
        )
        readelf.chmod(0o755)
        with mock.patch.dict(
            prune.__globals__,
            {"resolve_trusted_readelf": lambda: str(readelf)},
        ):
            prune(runtime, self.major_minor)

        self.assertEqual(list((runtime / "lib").glob("libpython*.so*")), [])

    def test_libpython_stubs_are_preserved_and_staging_fails_if_python_needs_them(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(STAGER.read_text(encoding="utf-8"), str(STAGER), "exec"), namespace)
        prune = namespace["prune_unneeded_libpython_stubs"]
        error_type = namespace["PythonRuntimeStageError"]

        runtime = self.temp_dir / "libpython-required-runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "lib").mkdir()
        python = runtime / "bin/python"
        python.write_bytes(b"\x7fELF" + b"fake-python")
        python.chmod(0o755)
        required = runtime / "lib" / f"libpython{self.major_minor}.so.1.0"
        required.write_bytes(b"required-runtime-library")

        fake_bin = self.temp_dir / "fake-readelf-libpython-required"
        fake_bin.mkdir()
        readelf = fake_bin / "readelf"
        readelf.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' ' 0x0000000000000001 (NEEDED) Shared library: [libpython{self.major_minor}.so.1.0]'\n",
            encoding="utf-8",
        )
        readelf.chmod(0o755)
        with mock.patch.dict(
            prune.__globals__,
            {"resolve_trusted_readelf": lambda: str(readelf)},
        ):
            with self.assertRaisesRegex(error_type, "depends on libpython"):
                prune(runtime, self.major_minor)

        self.assertTrue(required.is_file(), "dependency guard must run before deletion")

    def test_libpython_guard_checks_every_staged_elf_consumer_before_pruning(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(STAGER.read_text(encoding="utf-8"), str(STAGER), "exec"), namespace)
        prune = namespace["prune_unneeded_libpython_stubs"]
        error_type = namespace["PythonRuntimeStageError"]

        runtime = self.temp_dir / "libpython-extension-consumer-runtime"
        (runtime / "bin").mkdir(parents=True)
        site_packages = runtime / "lib" / f"python{self.major_minor}" / "site-packages"
        site_packages.mkdir(parents=True)
        python = runtime / "bin/python"
        python.write_bytes(b"\x7fELF" + b"fake-python")
        python.chmod(0o755)
        native_consumer = site_packages / "native_consumer.so"
        native_consumer.write_bytes(b"\x7fELF" + b"fake-extension")
        required = runtime / "lib" / f"libpython{self.major_minor}.so.1.0"
        required.write_bytes(b"\x7fELF" + b"required-runtime-library")

        fake_bin = self.temp_dir / "fake-readelf-extension-consumer"
        fake_bin.mkdir()
        readelf = fake_bin / "readelf"
        readelf.write_text(
            "#!/usr/bin/env bash\n"
            "case \"$2\" in\n"
            f"  *native_consumer.so) printf '%s\\n' ' 0x0000000000000001 (NEEDED) Shared library: [libpython{self.major_minor}.so.1.0]' ;;\n"
            "  *) printf '%s\\n' ' 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        readelf.chmod(0o755)
        with mock.patch.dict(
            prune.__globals__,
            {"resolve_trusted_readelf": lambda: str(readelf)},
        ):
            with self.assertRaisesRegex(error_type, "native_consumer.so"):
                prune(runtime, self.major_minor)

        self.assertTrue(required.is_file(), "all staged ELF consumers must be checked before deletion")

    def test_libpython_guard_never_executes_readelf_from_hostile_path(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(STAGER.read_text(encoding="utf-8"), str(STAGER), "exec"), namespace)
        prune = namespace["prune_unneeded_libpython_stubs"]
        error_type = namespace["PythonRuntimeStageError"]

        runtime = self.temp_dir / "libpython-hostile-path-runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "lib").mkdir()
        python = runtime / "bin/python"
        python.write_bytes(b"\x7fELF" + b"fake-python")
        python.chmod(0o755)
        required = runtime / "lib" / f"libpython{self.major_minor}.so.1.0"
        required.write_bytes(b"must-not-be-pruned")

        fake_bin = self.temp_dir / "hostile-readelf-bin"
        fake_bin.mkdir()
        marker = self.temp_dir / "hostile-readelf-was-called"
        readelf = fake_bin / "readelf"
        readelf.write_text(
            "#!/usr/bin/env bash\n"
            f"touch '{marker}'\n"
            "printf '%s\\n' ' 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]'\n",
            encoding="utf-8",
        )
        readelf.chmod(0o755)
        resolver = mock.Mock(
            side_effect=error_type("trusted readelf unavailable for libpython inspection")
        )
        with mock.patch.dict(
            os.environ,
            {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        ), mock.patch.dict(
            prune.__globals__,
            {"resolve_trusted_readelf": resolver},
        ):
            with self.assertRaisesRegex(error_type, "trusted readelf"):
                prune(runtime, self.major_minor)

        resolver.assert_called_once_with()
        self.assertFalse(marker.exists(), "hostile PATH readelf must never execute")
        self.assertTrue(required.is_file(), "failed trusted lookup must preserve libpython")

    def test_packaging_runtime_stagers_avoid_python39_only_string_helpers(self) -> None:
        for relative in (
            "packaging/linux/stage-python-runtime.py",
            "packaging/linux/stage-runtime-components.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(".removeprefix(", source, relative)
            self.assertNotIn(".removesuffix(", source, relative)

    def test_staged_runtime_prunes_tcl_tk_but_keeps_core_stdlib(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                str(STAGER),
                "--source-venv",
                str(self.source_venv),
                "--destination",
                str(self.destination),
                "--smoke-import",
                "portable_fixture",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        stdlib = self.destination / "lib" / f"python{self.major_minor}"
        for relative in ("tkinter", "idlelib", "turtledemo", "turtle.py"):
            self.assertFalse((stdlib / relative).exists(), relative)
        self.assertEqual(list((stdlib / "lib-dynload").glob("_tkinter.*")), [])
        self.assertTrue((stdlib / "threading.py").is_file())

        lib_root = self.destination / "lib"
        forbidden_prefixes = ("tcl", "tk", "itcl", "tdbc")
        forbidden = [
            path.name
            for path in lib_root.iterdir()
            if path.name.lower().startswith(forbidden_prefixes)
            or path.name.lower().startswith(("libtcl", "libtk"))
            or re.fullmatch(r"thread\d.*", path.name.lower())
        ]
        self.assertEqual(forbidden, [])

        smoke = subprocess.run(
            [
                str(self.destination / "bin/python"),
                "-I",
                "-c",
                (
                    "import importlib.util,json,ssl,threading;"
                    "assert importlib.util.find_spec('tkinter') is None;"
                    "assert importlib.util.find_spec('turtle') is None;"
                    "print(json.dumps({'ok': True}))"
                ),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(smoke.returncode, 0, smoke.stdout + smoke.stderr)

    def test_dependency_profiles_keep_dev_tools_for_source_but_not_for_production(self) -> None:
        setup = ROOT / "hermes-local-lab/scripts/setup-local.sh"

        def run_setup(profile: str | None) -> subprocess.CompletedProcess[str]:
            fake_root = self.temp_dir / f"fake-uv-{profile or 'default'}"
            fake_bin = fake_root / "bin"
            fake_bin.mkdir(parents=True)
            test_venv = ROOT / "hermes-local-lab/sources/hermes-agent/venv"
            self.assertFalse(test_venv.exists(), "isolated worktree must not have a real venv")
            (test_venv / "bin").mkdir(parents=True)
            fake_python = test_venv / "bin/python"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "if [ \"${1:-}\" = -c ]; then printf '%s\\n' \"$3\"; exit 0; fi\n"
                "exec python3 \"$@\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            uv_log = fake_root / "uv.log"
            fake_uv = fake_bin / "uv"
            fake_uv.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$TAIJI_TEST_UV_LOG\"\n",
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "TAIJI_TEST_UV_LOG": str(uv_log),
                "TAIJI_UV_LOCK_MODE": "strict",
                "TAIJI_USER_BIN": str(fake_root / "user-bin"),
            }
            if profile is not None:
                env["TAIJI_DEPENDENCY_PROFILE"] = profile
            if profile == "production":
                env["TAIJI_PYTHON_EXECUTABLE"] = str(fake_python.resolve())
                env["TAIJI_UV_EXECUTABLE"] = str(fake_uv.resolve())
            try:
                completed = subprocess.run(
                    ["bash", str(setup)],
                    text=True,
                    capture_output=True,
                    check=False,
                    env=env,
                )
            finally:
                shutil.rmtree(test_venv)
            completed.uv_log = uv_log.read_text(encoding="utf-8") if uv_log.exists() else ""  # type: ignore[attr-defined]
            return completed

        development = run_setup(None)
        self.assertEqual(development.returncode, 0, development.stdout + development.stderr)
        self.assertIn("sync --extra all --extra dev --locked", development.uv_log)  # type: ignore[attr-defined]

        production = run_setup("production")
        self.assertEqual(production.returncode, 0, production.stdout + production.stderr)
        self.assertIn("sync --extra all --locked", production.uv_log)  # type: ignore[attr-defined]
        self.assertNotIn("--extra dev", production.uv_log)  # type: ignore[attr-defined]

        invalid = run_setup("unknown")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("TAIJI_DEPENDENCY_PROFILE", invalid.stderr)

        project = tomllib.loads(
            (ROOT / "hermes-local-lab/sources/hermes-agent/pyproject.toml").read_text(
                encoding="utf-8"
            )
        )
        extras = project["project"]["optional-dependencies"]
        self.assertNotIn("hermes-agent[dev]", extras["all"])
        self.assertIn("debugpy==1.8.20", extras["dev"])
        self.assertIn("pytest==9.0.2", extras["dev"])

    def test_build_uses_the_portable_python_stager_instead_of_copying_the_uv_venv_tree(self) -> None:
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        stage_body = build[build.index("stage_python_runtime() {") : build.index("scan_product_privacy() {")]

        self.assertIn("stage-python-runtime.py", build)
        self.assertIn("--require-linux-x86-64", stage_body)
        self.assertIn("--smoke-import yaml", stage_body)
        self.assertNotIn('"$SOURCE_AGENT_DIR/venv"/ "$AGENT_RUNTIME/venv"/', stage_body)

    def test_build_rejects_development_distributions_from_the_staged_runtime(self) -> None:
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        start = build.index("assert_no_development_distributions() {")
        end = build.index("\n}\n\nscan_private_key_material()", start) + len("\n}")
        function_source = build[start:end]
        stage_body = build[build.index("stage_python_runtime() {") : build.index("scan_product_privacy() {")]
        self.assertLess(
            stage_body.index('python3 "$PYTHON_RUNTIME_STAGER"'),
            stage_body.index("assert_no_development_distributions"),
        )

        runtime = self.temp_dir / "distribution-gate/runtime/agent"
        site_packages = runtime / "venv/lib/python3.11/site-packages"
        site_packages.mkdir(parents=True)

        def write_distribution(directory: str, name: str) -> None:
            metadata_dir = site_packages / directory
            metadata_dir.mkdir()
            (metadata_dir / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n",
                encoding="utf-8",
            )

        write_distribution("fastapi-1.0.dist-info", "FastAPI")
        harness = (
            "set -euo pipefail\n"
            "AGENT_RUNTIME=$1\n"
            "fail() { printf '%s\\n' \"$*\" >&2; return 1; }\n"
            f"{function_source}\n"
            "assert_no_development_distributions\n"
        )

        clean = subprocess.run(
            ["bash", "-s", "--", str(runtime)],
            input=harness,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

        write_distribution("pytest-9.0.dist-info", "pytest")
        leaked = subprocess.run(
            ["bash", "-s", "--", str(runtime)],
            input=harness,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(leaked.returncode, 0)
        self.assertIn("pytest", leaked.stderr.lower())


if __name__ == "__main__":
    unittest.main()
