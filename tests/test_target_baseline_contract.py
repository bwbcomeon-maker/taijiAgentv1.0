import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "packaging/linux/target_baseline.py"
CAPTURE_SCRIPT = ROOT / "packaging/linux/capture-target-baseline.sh"


def load_module():
    spec = importlib.util.spec_from_file_location("taiji_target_baseline", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TargetBaselineContractTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.module = load_module()
        self.dependency_text = "ca-certificates\nlibc6\nlibgtk-3-0\n"
        self.dependency_hash = hashlib.sha256(
            self.dependency_text.encode("utf-8")
        ).hexdigest()
        self.profile = {
            "schema": "taiji-target-baseline/v1",
            "capture_tool_version": 1,
            "captured_at_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "profile_id": "kylin-v10-amd64-placeholder",
            "os_release": {
                "id": "kylin",
                "id_like": ["debian"],
                "version_id": "V10",
                "variant_id": "professional",
                "build_id": "sp1",
            },
            "architecture": {
                "uname_machine": "x86_64",
                "dpkg": "amd64",
            },
            "glibc": {
                "version": "2.31",
                "banner": "ldd (Ubuntu GLIBC 2.31-0kylin) 2.31",
            },
            "package_manager": {
                "format": "deb",
                "commands": {
                    "apt-get": True,
                    "apt-cache": True,
                    "dpkg": True,
                    "systemctl": True,
                },
            },
            "runtime_dependencies": {
                "contract_sha256": self.dependency_hash,
                "packages": [
                    {
                        "name": "libc6",
                        "status": "install ok installed",
                        "version": "2.31-0kylin",
                        "architecture": "amd64",
                    },
                    {
                        "name": "libgtk-3-0",
                        "status": "install ok installed",
                        "version": "3.24.20-0kylin",
                        "architecture": "amd64",
                    },
                    {
                        "name": "ca-certificates",
                        "status": "install ok installed",
                        "version": "20240203",
                        "architecture": "all",
                    },
                ],
            },
        }
        self.profile["profile_id"] = self.module.compute_profile_id(self.profile)

    def write_fixture(self, directory: Path):
        dependency_path = directory / "runtime-depends.txt"
        dependency_path.write_text(self.dependency_text, encoding="utf-8")
        profile_path = directory / "target-baseline.json"
        profile_path.write_text(
            json.dumps(self.profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return dependency_path, profile_path

    def assert_rejected(self, profile, expected_message):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            dependency_path = temp_root / "runtime-depends.txt"
            dependency_path.write_text(self.dependency_text, encoding="utf-8")
            profile_path = temp_root / "target-baseline.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "validate",
                    "--profile",
                    str(profile_path),
                    "--depends-file",
                    str(dependency_path),
                    "--max-age-days",
                    "30",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected_message, result.stderr)

    def test_valid_exact_debian_baseline_is_accepted_and_prints_safe_exports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dependency_path, profile_path = self.write_fixture(Path(temp_dir))
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "validate",
                    "--profile",
                    str(profile_path),
                    "--depends-file",
                    str(dependency_path),
                    "--max-age-days",
                    "30",
                    "--print-shell",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TAIJI_BASELINE_PROFILE_ID=", result.stdout)
        self.assertIn("TAIJI_BASELINE_OS_ID=kylin", result.stdout)
        self.assertIn("TAIJI_BASELINE_OS_VERSION_ID=V10", result.stdout)
        self.assertIn("TAIJI_BASELINE_GLIBC_MIN=2.31", result.stdout)
        self.assertNotIn("hostname", result.stdout.lower())

    def test_render_depends_outputs_sorted_version_floors_from_validated_profile(self):
        self.profile["runtime_dependencies"]["packages"][0][
            "version"
        ] = "2:2.31~rc1+vendor-3"
        self.profile["profile_id"] = self.module.compute_profile_id(self.profile)
        with tempfile.TemporaryDirectory() as temp_dir:
            dependency_path, profile_path = self.write_fixture(Path(temp_dir))
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "render-depends",
                    "--profile",
                    str(profile_path),
                    "--depends-file",
                    str(dependency_path),
                    "--max-age-days",
                    "30",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "ca-certificates (>= 20240203), "
            "libc6 (>= 2:2.31~rc1+vendor-3), "
            "libgtk-3-0 (>= 3.24.20-0kylin)\n",
        )

    def test_dependency_versions_reject_control_separator_and_relation_injection(self):
        malicious_versions = (
            "1.0\nBreaks: taiji-agent",
            "1.0, evil-package",
            "1.0 (<< 9)",
            "1.0)",
            "1.0 ",
        )
        for version in malicious_versions:
            with self.subTest(version=repr(version)):
                profile = copy.deepcopy(self.profile)
                profile["runtime_dependencies"]["packages"][0]["version"] = version
                profile["profile_id"] = self.module.compute_profile_id(profile)
                self.assert_rejected(profile, "version")

    def test_missing_declared_dependency_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["runtime_dependencies"]["packages"].pop()
        profile["profile_id"] = self.module.compute_profile_id(profile)
        self.assert_rejected(profile, "dependency set does not match")

    def test_uninstalled_dependency_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["runtime_dependencies"]["packages"][1]["status"] = "not-installed"
        profile["profile_id"] = self.module.compute_profile_id(profile)
        self.assert_rejected(profile, "is not installed")

    def test_non_amd64_profile_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["architecture"] = {
            "uname_machine": "aarch64",
            "dpkg": "arm64",
        }
        profile["profile_id"] = self.module.compute_profile_id(profile)
        self.assert_rejected(profile, "amd64/x86_64")

    def test_contract_hash_drift_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["runtime_dependencies"]["contract_sha256"] = "0" * 64
        profile["profile_id"] = self.module.compute_profile_id(profile)
        self.assert_rejected(profile, "dependency contract hash")

    def test_profile_id_tampering_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["profile_id"] = "kylin-v10-amd64-deadbeef0000"
        self.assert_rejected(profile, "profile_id does not match")

    def test_stale_capture_is_rejected_for_release_build(self):
        profile = copy.deepcopy(self.profile)
        profile["captured_at_utc"] = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        profile["profile_id"] = self.module.compute_profile_id(profile)
        self.assert_rejected(profile, "older than 30 days")

    def test_unknown_or_sensitive_fields_are_rejected(self):
        for field in ("hostname", "username", "serial_number", "ip_address"):
            with self.subTest(field=field):
                profile = copy.deepcopy(self.profile)
                profile[field] = "must-not-leak"
                self.assert_rejected(profile, "unknown fields")

    def test_invalid_types_are_not_coerced_to_strings(self):
        profile = copy.deepcopy(self.profile)
        profile["os_release"]["id"] = {"value": "kylin"}
        self.assert_rejected(profile, "os_release.id must be a string")

    def test_repository_dependency_contract_is_sorted_unique_and_complete(self):
        dependency_path = ROOT / "packaging/linux/deb/runtime-depends.txt"
        names = self.module.load_dependency_names(dependency_path)
        self.assertEqual(names, sorted(set(names)))
        for required in (
            "libc6",
            "libgtk-3-0",
            "libnss3",
            "libx11-6",
            "libgbm1",
            "xdg-utils",
            "ca-certificates",
        ):
            self.assertIn(required, names)

    def test_capture_script_ignores_hostile_path_and_shell_startup_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "fake-bin"
            fake_bin.mkdir()
            marker = temp_root / "hostile-command-ran"
            output_path = temp_root / "target-baseline.json"
            fake_bash = fake_bin / "bash"
            fake_bash.write_text(
                "#!/bin/sh\n"
                f"printf 'fake bash executed\\n' > {str(marker)!r}\n"
                "[ \"$#\" -lt 2 ] || printf '{\"forged\":true}\\n' > \"$2\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_bash.chmod(0o755)
            bash_env = temp_root / "bash-env"
            bash_env.write_text(
                f"printf 'BASH_ENV executed\\n' > {str(marker)!r}\n",
                encoding="utf-8",
            )
            python_shadow = temp_root / "python-shadow"
            python_shadow.mkdir()
            (python_shadow / "hashlib.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('PYTHONPATH executed\\n')\n"
                "raise RuntimeError('hostile hashlib shadow loaded')\n",
                encoding="utf-8",
            )
            hostile_env = os.environ.copy()
            hostile_env.update(
                {
                    "PATH": str(fake_bin),
                    "BASH_ENV": str(bash_env),
                    "ENV": str(bash_env),
                    "PYTHONPATH": str(python_shadow),
                    "DPKG_ROOT": str(temp_root / "fake-dpkg-root"),
                }
            )

            subprocess.run(
                [str(CAPTURE_SCRIPT), str(output_path)],
                text=True,
                capture_output=True,
                check=False,
                env=hostile_env,
                timeout=30,
            )

            self.assertFalse(marker.exists(), "hostile PATH/BASH_ENV command executed")
            if output_path.exists():
                self.assertNotIn("forged", output_path.read_text(encoding="utf-8"))

    def test_capture_profile_never_executes_path_shadow_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "fake-bin"
            fake_bin.mkdir()
            marker = temp_root / "path-shadow-ran"
            dependency_path = temp_root / "runtime-depends.txt"
            dependency_path.write_text("ca-certificates\n", encoding="utf-8")
            os_release_path = temp_root / "os-release"
            os_release_path.write_text(
                "ID=kylin\nVERSION_ID=V10\nID_LIKE=debian\n",
                encoding="utf-8",
            )

            command_outputs = {
                "dpkg": "amd64",
                "dpkg-query": "install ok installed\\t1.0\\tamd64",
                "ldd": "ldd (Fake GLIBC) 2.31",
                "apt-get": "",
                "apt-cache": "",
                "systemctl": "",
                "uname": "x86_64",
            }
            for name, stdout in command_outputs.items():
                command = fake_bin / name
                command.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' {name!r} >> {str(marker)!r}\n"
                    f"printf '%b\\n' {stdout!r}\n",
                    encoding="utf-8",
                )
                command.chmod(0o755)

            with mock.patch.dict(
                os.environ,
                {
                    "PATH": str(fake_bin),
                    "DPKG_ROOT": str(temp_root / "fake-dpkg-root"),
                    "DPKG_ADMINDIR": str(temp_root / "fake-dpkg-admin"),
                    "LD_LIBRARY_PATH": str(temp_root / "fake-libraries"),
                    "PYTHONPATH": str(temp_root / "python-shadow"),
                },
                clear=False,
            ):
                with self.assertRaises(self.module.BaselineError) as command_error:
                    self.module.run_text(["dpkg", "--print-architecture"])
                self.assertIn("absolute trusted system path", str(command_error.exception))
                try:
                    self.module.capture_profile(dependency_path, os_release_path)
                except self.module.BaselineError:
                    pass

            self.assertFalse(marker.exists(), "capture used a command resolved from PATH")

    def test_capture_subprocess_receives_only_the_fixed_minimal_environment(self):
        hostile_values = {
            "PATH": "/tmp/path-shadow",
            "DPKG_ROOT": "/tmp/fake-dpkg-root",
            "DPKG_ADMINDIR": "/tmp/fake-dpkg-admin",
            "LD_LIBRARY_PATH": "/tmp/fake-libraries",
            "PYTHONPATH": "/tmp/python-shadow",
            "BASH_ENV": "/tmp/fake-bash-env",
        }
        with mock.patch.dict(os.environ, hostile_values, clear=False):
            output = self.module.run_text(["/usr/bin/env"])

        self.assertEqual(
            set(output.splitlines()),
            {"LANG=C", "LC_ALL=C", "PATH=/usr/sbin:/usr/bin:/sbin:/bin"},
        )

    def test_checksum_sidecar_replaces_symlink_without_overwriting_its_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            output_path = temp_root / "target-baseline.json"
            sidecar = temp_root / "target-baseline.json.sha256"
            victim = temp_root / "must-not-change"
            victim.write_text("original\n", encoding="utf-8")
            sidecar.symlink_to(victim)

            self.module.write_profile_atomic(self.profile, output_path)

            self.assertEqual(victim.read_text(encoding="utf-8"), "original\n")
            self.assertFalse(sidecar.is_symlink())
            self.assertRegex(
                sidecar.read_text(encoding="ascii"),
                r"^[0-9a-f]{64}  target-baseline\.json\n$",
            )


if __name__ == "__main__":
    unittest.main()
