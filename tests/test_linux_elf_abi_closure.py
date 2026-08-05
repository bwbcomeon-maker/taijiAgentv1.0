import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "packaging/linux/compatibility-policy.json"
AUDIT_PATH = ROOT / "packaging/linux/audit-elf-closure.py"
STAGER_PATH = ROOT / "packaging/linux/stage-private-libraries.py"
FIXTURE_ROOT = ROOT / "tests/fixtures/elf-audit"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LinuxElfAbiClosureTest(unittest.TestCase):
    def setUp(self):
        self.audit = load_module(AUDIT_PATH, "taiji_elf_audit")
        self.stager = load_module(STAGER_PATH, "taiji_private_stager")
        self.policy = self.audit.load_policy(POLICY_PATH)

    def fixture(self, name: str) -> str:
        return (FIXTURE_ROOT / name).read_text(encoding="utf-8")

    def fake_elf(self, root: Path, relative: str, *, dynamic: str | None = None,
                 versions: str | None = None, header: str | None = None,
                 body: bytes = b"") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x7fELF\x02\x01\x01" + body)
        outputs = {
            "-h": header or self.fixture("readelf-header-x86_64.txt"),
            "-d": dynamic or self.fixture("readelf-dynamic-safe.txt"),
            "--version-info": versions or self.fixture("readelf-version-info-safe.txt"),
        }
        self.readelf_outputs[path] = outputs
        return path

    def install_readelf_stub(self):
        def readelf(path, *args):
            option = args[0]
            return self.readelf_outputs[Path(path)][option]

        return mock.patch.object(self.audit, "run_readelf", side_effect=readelf)

    def test_scans_every_elf_native_wheel_electron_node_and_python(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.readelf_outputs = {}
            paths = [
                "runtime/agent/venv/lib/python3.11/site-packages/native/_core.so",
                "apps/taiji-desktop/node_modules/electron/dist/electron",
                "apps/taiji-desktop/node_modules/node/bin/node",
                "runtime/agent/venv/bin/python",
                "apps/taiji-desktop/resources/app.asar.unpacked/addon.node",
            ]
            for relative in paths:
                self.fake_elf(root, relative)
            with self.install_readelf_stub():
                report = self.audit.audit_root(root, self.policy)
            self.assertEqual(report["schema"], "taiji-elf-abi-audit/v1")
            self.assertEqual(
                [item["relative_path"] for item in report["files"]], sorted(paths)
            )
            self.assertEqual(len(report["files"]), len(paths))

    def test_rejects_non_x86_64_and_symbol_version_above_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.readelf_outputs = {}
            self.fake_elf(
                root, "runtime/agent/bin/arm-helper",
                header=self.fixture("readelf-header-aarch64.txt")
                if (FIXTURE_ROOT / "readelf-header-aarch64.txt").exists()
                else "Class: ELF64\n  Machine: AArch64\n",
            )
            with self.install_readelf_stub():
                with self.assertRaisesRegex(self.audit.ElfAuditError, "x86_64"):
                    self.audit.audit_root(root, self.policy)

            root = Path(temp_dir) / "new"
            root.mkdir()
            self.readelf_outputs = {}
            self.fake_elf(
                root, "runtime/agent/bin/python",
                versions=self.fixture("readelf-version-info-too-new.txt"),
            )
            with self.install_readelf_stub():
                with self.assertRaisesRegex(self.audit.ElfAuditError, "GLIBC"):
                    self.audit.audit_root(root, self.policy)

    def test_rejects_dt_rpath_absolute_or_escaping_runpath(self):
        dynamic_cases = [
            "0x000000000000000f (RPATH)              Library rpath: [$ORIGIN]",
            "0x000000000000001d (RUNPATH)            Library runpath: [/usr/local/lib]",
            "0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN/../../escape]",
        ]
        for dynamic in dynamic_cases:
            with self.subTest(dynamic=dynamic), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self.readelf_outputs = {}
                self.fake_elf(root, "runtime/agent/bin/python", dynamic=dynamic)
                with self.install_readelf_stub():
                    with self.assertRaises(self.audit.ElfAuditError):
                        self.audit.audit_root(root, self.policy)

    def test_rejects_unresolved_or_ambiguous_soname(self):
        unresolved = "0x0000000000000001 (NEEDED)             Shared library: [libmissing.so.1]"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.readelf_outputs = {}
            self.fake_elf(root, "runtime/agent/bin/python", dynamic=unresolved)
            with self.install_readelf_stub():
                with self.assertRaisesRegex(self.audit.ElfAuditError, "unresolved"):
                    self.audit.audit_root(root, self.policy)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.readelf_outputs = {}
            private = self.policy["elf"]["allowed_private_sonames"][0]
            dynamic = f"0x000000000000000e (SONAME)             Library soname: [{private}]"
            self.fake_elf(root, "runtime/lib/a.so", dynamic=dynamic)
            self.fake_elf(root, "runtime/lib/b.so", dynamic=dynamic)
            self.fake_elf(root, "runtime/agent/bin/python", dynamic=f"0x1 (NEEDED) Shared library: [{private}]")
            with self.install_readelf_stub():
                with self.assertRaisesRegex(self.audit.ElfAuditError, "ambiguous"):
                    self.audit.audit_root(root, self.policy)

    def test_rejects_bundled_glibc_loader_pam_systemd_dbus_and_driver_core(self):
        forbidden = self.policy["elf"]["forbidden_bundled_sonames"]
        for soname in forbidden:
            with self.subTest(soname=soname), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self.readelf_outputs = {}
                self.fake_elf(
                    root, f"runtime/lib/{soname}",
                    dynamic=f"0x000000000000000e (SONAME) Library soname: [{soname}]",
                )
                with self.install_readelf_stub():
                    with self.assertRaisesRegex(self.audit.ElfAuditError, "forbidden"):
                        self.audit.audit_root(root, self.policy)

    def test_rejects_build_host_absolute_path_leak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.readelf_outputs = {}
            self.fake_elf(root, "runtime/agent/bin/python", body=b"/Users/buildbot/src/taiji\0")
            with self.install_readelf_stub():
                with self.assertRaisesRegex(self.audit.ElfAuditError, "build-host"):
                    self.audit.audit_root(root, self.policy)

    def test_allows_origin_runpath_and_policy_private_sonames(self):
        private = self.policy["elf"]["allowed_private_sonames"][0]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.readelf_outputs = {}
            self.fake_elf(
                root, f"runtime/lib/{private}",
                dynamic=f"0x000000000000000e (SONAME) Library soname: [{private}]",
            )
            self.fake_elf(
                root, "runtime/agent/bin/python",
                dynamic=(
                    "0x0000000000000001 (NEEDED) Shared library: "
                    f"[{private}]\n0x000000000000001d (RUNPATH) "
                    "Library runpath: [$ORIGIN/../lib]"
                ),
            )
            with self.install_readelf_stub():
                report = self.audit.audit_root(root, self.policy)
            self.assertIn(private, report["private_sonames"])
            self.assertNotIn(private, report["external_sonames"])

    def test_stager_rejects_symlink_hardlink_wrong_owner_and_non_allowlisted_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "payload"
            sysroot = Path(temp_dir) / "sysroot"
            root.mkdir()
            sysroot.mkdir()
            source = sysroot / "liballowed.so.1"
            source.write_bytes(b"private")
            allowed = self.policy["elf"]["allowed_private_sonames"][0]

            def fake_soname(path, *args):
                return allowed if Path(path).name == source.name else None

            def fake_copy(source_path, destination, *, uid, gid):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source_path.read_bytes())
                return hashlib.sha256(destination.read_bytes()).hexdigest()

            with mock.patch.object(self.stager, "readelf_soname", side_effect=fake_soname), \
                    mock.patch.object(self.stager, "source_metadata", return_value=(stat.S_IFREG | 0o644, 0, 1)), \
                    mock.patch.object(self.stager, "_copy_atomically", side_effect=fake_copy), \
                    mock.patch.object(self.stager, "_ensure_private_directory"):
                report = self.stager.stage_private_libraries(root, self.policy, sysroot)
            staged = root / "opt/taiji-agent/runtime/lib" / allowed
            self.assertTrue(staged.is_file())
            self.assertEqual(report["files"][0]["soname"], allowed)
            self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), report["files"][0]["sha256"])

            for label, setup in (
                ("symlink", lambda candidate: candidate.symlink_to(source)),
                ("hardlink", lambda candidate: os.link(source, candidate)),
            ):
                candidate = sysroot / f"{label}.so.1"
                setup(candidate)
                with mock.patch.object(self.stager, "readelf_soname", return_value=allowed):
                    with self.assertRaises(self.stager.StageError):
                        self.stager.validate_source(candidate, self.policy)
                if label == "symlink":
                    stage_root = Path(temp_dir) / "symlink-payload"
                    stage_sysroot = Path(temp_dir) / "symlink-sysroot"
                    stage_root.mkdir()
                    stage_sysroot.mkdir()
                    target = stage_sysroot / "z-target.so.1"
                    target.write_bytes(b"private")
                    link = stage_sysroot / "a-link.so.1"
                    link.symlink_to(target)

                    def stage_soname(path, *args):
                        return allowed if Path(path).name in {link.name, target.name} else None

                    def stage_metadata(path):
                        if Path(path).is_symlink():
                            return stat.S_IFLNK | 0o777, 0, 1
                        return stat.S_IFREG | 0o644, 0, 1

                    with mock.patch.object(self.stager, "readelf_soname", side_effect=stage_soname), \
                            mock.patch.object(self.stager, "source_metadata", side_effect=stage_metadata), \
                            mock.patch.object(self.stager, "_copy_atomically", return_value="0" * 64):
                        with self.assertRaises(self.stager.StageError):
                            self.stager.stage_private_libraries(stage_root, self.policy, stage_sysroot)
                candidate.unlink()

            wrong_owner = sysroot / "wrong-owner.so.1"
            wrong_owner.write_bytes(b"private")
            with mock.patch.object(self.stager, "readelf_soname", return_value=allowed), \
                    mock.patch.object(self.stager, "source_metadata", return_value=(stat.S_IFREG | 0o644, 1000, 1)):
                with self.assertRaises(self.stager.StageError):
                    self.stager.validate_source(wrong_owner, self.policy)

            non_allowlisted = sysroot / "libc.so.6"
            non_allowlisted.write_bytes(b"system")
            with mock.patch.object(self.stager, "readelf_soname", return_value="libc.so.6"), \
                    mock.patch.object(self.stager, "source_metadata", return_value=(stat.S_IFREG | 0o644, 0, 1)):
                with self.assertRaises(self.stager.StageError):
                    self.stager.validate_source(non_allowlisted, self.policy)

    def test_source_native_verifier_reinjects_private_loader_path_after_runtime_env(self):
        source_verifier = (ROOT / "hermes-local-lab/scripts/taiji-native-verify").read_text(encoding="utf-8")
        runtime_env = (ROOT / "hermes-local-lab/scripts/runtime-env.sh").read_text(encoding="utf-8")
        installed_wrapper = (ROOT / "packaging/linux/bin/taiji-native-verify").read_text(encoding="utf-8")
        source_boundary = source_verifier.index('source "$SCRIPT_DIR/runtime-env.sh"')
        reinjection = source_verifier.index('LD_LIBRARY_PATH="$TAIJI_PRIVATE_LIBRARY_DIR"')
        self.assertLess(source_boundary, reinjection)
        self.assertIn('TAIJI_PRIVATE_LIBRARY_DIR="$APP_ROOT/runtime/lib"', installed_wrapper)
        self.assertIn('export TAIJI_PRIVATE_LIBRARY_DIR', installed_wrapper)
        self.assertIn('[ ! -L "$TAIJI_PRIVATE_LIBRARY_DIR" ]', source_verifier)
        self.assertIn("TAIJI_PRIVATE_LIBRARY_DIR", runtime_env)
        self.assertNotIn("/etc/ld.so.conf", source_verifier)

    def test_sysroot_candidate_requires_matching_authoritative_soname(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "payload"
            sysroot = Path(temp_dir) / "sysroot"
            root.mkdir()
            sysroot.mkdir()
            self.readelf_outputs = {}
            self.fake_elf(
                root,
                "runtime/agent/bin/python",
                dynamic=(
                    "0x0000000000000001 (NEEDED) Shared library: "
                    "[libasound.so.2]"
                ),
            )
            self.fake_elf(
                sysroot,
                "libasound.so.2",
                dynamic=(
                    "0x000000000000000e (SONAME) Library soname: "
                    "[libother.so.1]"
                ),
            )
            with self.install_readelf_stub():
                with self.assertRaisesRegex(self.audit.ElfAuditError, "unresolved"):
                    self.audit.audit_root(root, self.policy, sysroot)

    def test_rejects_unknown_bundled_soname_even_when_unreferenced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.readelf_outputs = {}
            self.fake_elf(
                root,
                "runtime/lib/libmystery.so.1",
                dynamic=(
                    "0x000000000000000e (SONAME) Library soname: "
                    "[libmystery.so.1]"
                ),
            )
            with self.install_readelf_stub():
                with self.assertRaisesRegex(self.audit.ElfAuditError, "non-allowlisted"):
                    self.audit.audit_root(root, self.policy)

    def test_rejects_malformed_dynamic_tags_and_empty_values(self):
        malformed = (
            "0x0000000000000001 (NEEDED) Shared library: libmissing.so.1\n"
            "0x000000000000001d (RUNPATH) Library runpath: []"
        )
        with self.assertRaisesRegex(self.audit.ElfAuditError, "malformed"):
            self.audit.parse_readelf_dynamic(malformed)
        for runpath in ("$ORIGIN:", ":$ORIGIN", "$ORIGIN::foo"):
            with self.subTest(runpath=runpath), self.assertRaisesRegex(self.audit.ElfAuditError, "malformed"):
                self.audit.parse_readelf_dynamic(
                    f"0x000000000000001d (RUNPATH) Library runpath: [{runpath}]"
                )

    def test_report_writes_are_private_and_leave_no_predictable_temp_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            for module in (self.audit, self.stager):
                report_path = directory / f"{module.__name__}.json"
                module.write_report(report_path, {"schema": "fixture"})
                self.assertEqual(stat.S_IMODE(report_path.stat().st_mode), 0o600)
                self.assertEqual(list(directory.glob(f".{report_path.name}.tmp-*")), [])
                self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["schema"], "fixture")
            outside = directory / "outside"
            outside.mkdir()
            linked_parent = directory / "linked-reports"
            linked_parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(self.audit.ElfAuditError, "report directory"):
                self.audit.write_report(linked_parent / "audit.json", {"schema": "fixture"})
            with self.assertRaisesRegex(self.stager.StageError, "report directory"):
                self.stager.write_report(linked_parent / "stage.json", {"schema": "fixture"})
            self.assertFalse((outside / "audit.json").exists())
            self.assertFalse((outside / "stage.json").exists())

    def test_rejects_payload_symlink_that_escapes_audit_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "payload"
            outside = base / "outside.elf"
            root.mkdir()
            outside.write_bytes(b"\x7fELF\x02\x01\x01")
            link = root / "runtime/agent/bin/python"
            link.parent.mkdir(parents=True)
            link.symlink_to(outside)
            with self.assertRaisesRegex(self.audit.ElfAuditError, "symlink.*escape"):
                self.audit.audit_root(root, self.policy)

    def test_readelf_command_never_comes_from_hostile_user_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            marker = directory / "executed"
            evil = directory / "readelf"
            evil.write_text(
                "#!/bin/sh\n"
                f"touch '{marker}'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            evil.chmod(0o755)
            candidate = directory / "candidate.elf"
            candidate.write_bytes(b"\x7fELF")
            with mock.patch.dict(os.environ, {"PATH": str(directory)}, clear=False):
                with self.assertRaises(self.audit.ElfAuditError):
                    self.audit.run_readelf(candidate, "-h")
                try:
                    self.stager.readelf_soname(candidate)
                except self.stager.StageError:
                    pass
            self.assertFalse(marker.exists())

    def test_stager_rejects_allowlisted_basename_without_authoritative_soname(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "payload"
            sysroot = Path(temp_dir) / "sysroot"
            root.mkdir()
            sysroot.mkdir()
            allowed = self.policy["elf"]["allowed_private_sonames"][0]
            source = sysroot / allowed
            source.write_bytes(b"not-an-elf")
            with mock.patch.object(self.stager, "readelf_soname", return_value=None), \
                    mock.patch.object(self.stager, "source_metadata", return_value=(stat.S_IFREG | 0o644, 0, 1)):
                with self.assertRaisesRegex(self.stager.StageError, "SONAME"):
                    self.stager.stage_private_libraries(root, self.policy, sysroot)
            with mock.patch.object(self.stager, "readelf_soname", return_value="libother.so.1"), \
                    mock.patch.object(self.stager, "source_metadata", return_value=(stat.S_IFREG | 0o644, 0, 1)):
                with self.assertRaisesRegex(self.stager.StageError, "mismatched SONAME"):
                    self.stager.stage_private_libraries(root, self.policy, sysroot)

    def test_stager_private_library_directories_are_0755_under_umask_0002(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "payload"
            root.mkdir()
            destination = root / "opt/taiji-agent/runtime/lib"
            real_lstat = os.lstat

            def root_owned_lstat(path):
                metadata = real_lstat(path)
                path = Path(path)
                if path != root and root in path.parents:
                    values = list(metadata)
                    values[4] = 0
                    values[5] = 0
                    metadata = os.stat_result(values)
                return metadata

            old_umask = os.umask(0o002)
            try:
                with mock.patch.object(self.stager.os, "geteuid", return_value=501), \
                        mock.patch.object(self.stager.os, "lstat", side_effect=root_owned_lstat):
                    self.stager._ensure_private_directory(destination, root)
            finally:
                os.umask(old_umask)
            for relative in (
                "opt",
                "opt/taiji-agent",
                "opt/taiji-agent/runtime",
                "opt/taiji-agent/runtime/lib",
            ):
                self.assertEqual(
                    stat.S_IMODE((root / relative).stat().st_mode),
                    0o755,
                    relative,
                )


if __name__ == "__main__":
    unittest.main()
