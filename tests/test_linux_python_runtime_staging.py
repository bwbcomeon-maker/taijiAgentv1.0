from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "packaging/linux/stage-python-runtime.py"
SOURCE_PYTHON = ROOT / "hermes-local-lab/sources/hermes-agent/venv/bin/python"


class LinuxPythonRuntimeStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="taiji-python-runtime-stage-"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.source_venv = self.temp_dir / "source-venv"
        self.destination = self.temp_dir / "payload/runtime/agent/venv"

        source_python = SOURCE_PYTHON.resolve(strict=True)
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

    def test_build_uses_the_portable_python_stager_instead_of_copying_the_uv_venv_tree(self) -> None:
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        stage_body = build[build.index("stage_python_runtime() {") : build.index("scan_product_privacy() {")]

        self.assertIn("stage-python-runtime.py", build)
        self.assertIn("--require-linux-x86-64", stage_body)
        self.assertIn("--smoke-import yaml", stage_body)
        self.assertNotIn('"$SOURCE_AGENT_DIR/venv"/ "$AGENT_RUNTIME/venv"/', stage_body)


if __name__ == "__main__":
    unittest.main()
