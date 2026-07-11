from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "packaging/linux/stage-electron-runtime.py"


class LinuxElectronRuntimeStagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="taiji-electron-runtime-stage-"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))
        self.source = self.temp_dir / "source-node-modules/electron"
        self.destination = self.temp_dir / "payload/node_modules/electron"
        (self.source / "dist/resources").mkdir(parents=True)
        (self.source / "dist/locales").mkdir(parents=True)
        (self.source / "package.json").write_text(
            json.dumps({"name": "electron", "version": "39.8.10"}) + "\n",
            encoding="utf-8",
        )
        electron = self.source / "dist/electron"
        header = bytearray(64)
        header[0:4] = b"\x7fELF"
        header[4] = 2
        header[5] = 1
        header[6] = 1
        header[16:18] = (2).to_bytes(2, "little")
        header[18:20] = (62).to_bytes(2, "little")
        electron.write_bytes(bytes(header) + b"electron-runtime-bytes")
        electron.chmod(0o755)
        for relative in (
            "dist/icudtl.dat",
            "dist/resources.pak",
            "dist/snapshot_blob.bin",
            "dist/v8_context_snapshot.bin",
            "dist/resources/default_app.asar",
            "dist/locales/en-US.pak",
            "dist/chrome-sandbox",
            "dist/libffmpeg.so",
        ):
            target = self.source / relative
            target.write_bytes(f"runtime:{relative}\n".encode())
        (self.source / "dist/chrome-sandbox").chmod(0o755)

        (self.source / "README.md").write_text("development readme\n", encoding="utf-8")
        (self.source / "index.d.ts").write_text("export {};\n", encoding="utf-8")
        (self.source / "dist/runtime.js.map").write_text("{}\n", encoding="utf-8")
        (self.source / "dist/tests").mkdir()
        (self.source / "dist/tests/leak.js").write_text("throw new Error();\n", encoding="utf-8")

    def test_stages_only_audited_electron_runtime_files(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                str(STAGER),
                "--source",
                str(self.source),
                "--destination",
                str(self.destination),
                "--require-linux-x86-64",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(
            (self.destination / "dist/electron").read_bytes(),
            (self.source / "dist/electron").read_bytes(),
        )
        for relative in (
            "package.json",
            "dist/electron",
            "dist/icudtl.dat",
            "dist/resources.pak",
            "dist/snapshot_blob.bin",
            "dist/v8_context_snapshot.bin",
            "dist/resources/default_app.asar",
            "dist/locales/en-US.pak",
            "dist/chrome-sandbox",
            "dist/libffmpeg.so",
        ):
            self.assertTrue((self.destination / relative).is_file(), relative)
        staged_paths = [path.relative_to(self.destination).as_posix() for path in self.destination.rglob("*")]
        for relative in staged_paths:
            name = Path(relative).name.lower()
            self.assertNotIn("tests", Path(relative).parts, relative)
            self.assertFalse(name.startswith("readme"), relative)
            self.assertFalse(name.endswith(".d.ts"), relative)
            self.assertFalse(name.endswith(".map"), relative)

    def test_build_does_not_copy_the_complete_desktop_node_modules_tree(self) -> None:
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        start = build.index('mkdir -p "$DESKTOP_RUNTIME/src"')
        end = build.index('install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent"')
        desktop_stage = build[start:end]

        self.assertIn("stage-electron-runtime.py", build)
        self.assertIn("--require-linux-x86-64", desktop_stage)
        self.assertNotIn('"$APP_DIR/node_modules"/', desktop_stage)
        self.assertNotIn('"$DESKTOP_RUNTIME/node_modules"/', desktop_stage)


if __name__ == "__main__":
    unittest.main()
