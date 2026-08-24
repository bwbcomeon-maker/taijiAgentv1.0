from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "packaging/linux/stage-electron-runtime.py"


def load_stager():
    spec = importlib.util.spec_from_file_location("taiji_electron_runtime_stager_test", STAGER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Electron runtime stager")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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

        self.archive = self.temp_dir / "electron-v39.8.10-linux-x64.zip"
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for path in sorted((self.source / "dist").rglob("*")):
                relative = path.relative_to(self.source / "dist")
                if path.is_file() and not any(
                    part == "tests" or part.endswith(".map") for part in relative.parts
                ):
                    bundle.write(path, relative.as_posix())
        self.archive_sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.stager = load_stager()

    def stage(self):
        electron_sha256 = hashlib.sha256(
            (self.source / "dist/electron").read_bytes()
        ).hexdigest()
        return self.stager.stage_electron_runtime(
            self.source,
            self.destination,
            archive=self.archive,
            expected_version="39.8.10",
            expected_archive_sha256=self.archive_sha256,
            expected_executable_sha256=electron_sha256,
            require_linux_x86_64=True,
        )

    def test_stages_only_audited_electron_runtime_files(self) -> None:
        result = self.stage()

        self.assertTrue(result["ok"])
        self.assertEqual(result["electron_archive_sha256"], self.archive_sha256)
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

    def test_rejects_tampered_non_elf_resource_even_when_elf_is_unchanged(self) -> None:
        (self.source / "dist/resources.pak").write_bytes(b"attacker-controlled-resource")

        with self.assertRaisesRegex(
            self.stager.ElectronRuntimeStageError,
            "archive member differs from installed Electron dist",
        ):
            self.stage()

    def test_rejects_archive_that_does_not_match_fixed_policy_sha256(self) -> None:
        with self.archive.open("ab") as handle:
            handle.write(b"tampered")

        with self.assertRaisesRegex(
            self.stager.ElectronRuntimeStageError,
            "archive SHA256",
        ):
            self.stage()

    def test_archive_fd_contract_accepts_only_fully_sealed_unlinked_memfd(self) -> None:
        metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o400,
            st_nlink=0,
            st_size=self.archive.stat().st_size,
        )
        seal_constants = {
            "F_GET_SEALS": 1034,
            "F_SEAL_SEAL": 0x0001,
            "F_SEAL_SHRINK": 0x0002,
            "F_SEAL_GROW": 0x0004,
            "F_SEAL_WRITE": 0x0008,
        }
        required_seals = 0x000F

        with mock.patch.multiple(
            self.stager.fcntl,
            create=True,
            **seal_constants,
        ):
            with mock.patch.object(
                self.stager.fcntl,
                "fcntl",
                return_value=required_seals,
            ) as get_seals:
                self.stager.validate_archive_fd_identity(15, metadata)
            get_seals.assert_called_once_with(15, seal_constants["F_GET_SEALS"])

            with mock.patch.object(
                self.stager.fcntl,
                "fcntl",
                return_value=seal_constants["F_SEAL_WRITE"],
            ):
                with self.assertRaisesRegex(
                    self.stager.ElectronRuntimeStageError,
                    "fully sealed memfd",
                ):
                    self.stager.validate_archive_fd_identity(15, metadata)

    def test_staged_runtime_is_extracted_from_archive_and_binds_the_electron_elf(self) -> None:
        electron_sha256 = hashlib.sha256(
            (self.source / "dist/electron").read_bytes()
        ).hexdigest()
        # The source tree is writable and therefore cannot be the byte/mode
        # origin of the packaged runtime.  The fixed archive was created while
        # this member was 0644; changing only the source mode must not affect
        # the staged artifact.
        (self.source / "dist/resources.pak").chmod(0o600)

        result = self.stager.stage_electron_runtime(
            self.source,
            self.destination,
            archive=self.archive,
            expected_version="39.8.10",
            expected_archive_sha256=self.archive_sha256,
            expected_executable_sha256=electron_sha256,
            require_linux_x86_64=True,
        )

        self.assertEqual(result["electron_executable_sha256"], electron_sha256)
        self.assertEqual(
            stat.S_IMODE((self.destination / "dist/resources.pak").stat().st_mode),
            0o644,
        )

    def test_archive_path_replacement_cannot_change_bytes_after_fixed_hash_validation(self) -> None:
        original_resource = (self.source / "dist/resources.pak").read_bytes()
        tampered_archive = self.temp_dir / "tampered-electron.zip"
        with zipfile.ZipFile(self.archive, "r") as source_bundle, zipfile.ZipFile(
            tampered_archive,
            "w",
            compression=zipfile.ZIP_STORED,
        ) as tampered_bundle:
            for info in source_bundle.infolist():
                payload = source_bundle.read(info)
                if info.filename == "resources.pak":
                    payload = b"attacker-controlled-after-validation"
                tampered_bundle.writestr(info, payload)

        original_extract = self.stager.extract_fixed_archive
        preserved_archive = self.temp_dir / "preserved-electron.zip"

        def replace_path_during_extract(archive_source, destination_dist):
            os.replace(self.archive, preserved_archive)
            os.replace(tampered_archive, self.archive)
            try:
                original_extract(archive_source, destination_dist)
            finally:
                os.replace(self.archive, tampered_archive)
                os.replace(preserved_archive, self.archive)

        self.stager.extract_fixed_archive = replace_path_during_extract
        self.addCleanup(setattr, self.stager, "extract_fixed_archive", original_extract)

        result = self.stage()

        self.assertTrue(result["ok"])
        self.assertEqual(
            (self.destination / "dist/resources.pak").read_bytes(),
            original_resource,
        )

    def test_rejects_fixed_archive_when_its_electron_elf_identity_is_not_canonical(self) -> None:
        with self.assertRaisesRegex(
            self.stager.ElectronRuntimeStageError,
            "Electron executable SHA256",
        ):
            self.stager.stage_electron_runtime(
                self.source,
                self.destination,
                archive=self.archive,
                expected_version="39.8.10",
                expected_archive_sha256=self.archive_sha256,
                expected_executable_sha256="0" * 64,
                require_linux_x86_64=True,
            )

    def test_build_does_not_copy_the_complete_desktop_node_modules_tree(self) -> None:
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        start = build.index('mkdir -p "$DESKTOP_RUNTIME/src"')
        end = build.index('install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent"')
        desktop_stage = build[start:end]

        self.assertIn("stage-electron-runtime.py", build)
        self.assertIn("--require-linux-x86-64", desktop_stage)
        self.assertIn('--archive "$ELECTRON_ARCHIVE"', desktop_stage)
        self.assertIn('--policy "$POLICY_FILE"', desktop_stage)
        self.assertNotIn('"$APP_DIR/node_modules"/', desktop_stage)
        self.assertNotIn('"$DESKTOP_RUNTIME/node_modules"/', desktop_stage)

    def test_builder_uses_a_private_electron_cache_and_binds_the_downloaded_archive(self) -> None:
        builder = (
            ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('electron_config_cache="$BUILD_ROOT/electron-cache"', builder)
        self.assertIn('electron-v${ELECTRON_VERSION}-linux-x64.zip', builder)
        self.assertIn('ELECTRON_ARCHIVE_SHA256', builder)
        self.assertIn('adopt_sealed_snapshot "$ELECTRON_ARCHIVE" "$ELECTRON_ARCHIVE_SHA256" electron', builder)
        self.assertIn('TAIJI_ELECTRON_ARCHIVE="${ELECTRON_ARCHIVE_FD:+}"', builder)
        self.assertIn('TAIJI_ELECTRON_ARCHIVE_FD="$ELECTRON_ARCHIVE_FD"', builder)
        self.assertIn('TAIJI_ELECTRON_ARCHIVE_BASENAME="$ELECTRON_ARCHIVE_BASENAME"', builder)


if __name__ == "__main__":
    unittest.main()
