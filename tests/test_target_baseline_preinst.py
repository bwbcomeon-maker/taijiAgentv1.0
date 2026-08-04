import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE_MODULE = ROOT / "packaging/linux/target_baseline.py"
RENDERER = ROOT / "packaging/linux/deb/render-preinst.py"
PREINST = ROOT / "packaging/linux/deb/preinst"


def load_baseline_module():
    spec = importlib.util.spec_from_file_location("taiji_target_baseline", BASELINE_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TargetBaselinePreinstTest(unittest.TestCase):
    def make_profile(self, temp_root: Path):
        dependencies = temp_root / "runtime-depends.txt"
        dependencies.write_text("ca-certificates\nlibc6\n", encoding="utf-8")
        profile = {
            "schema": "taiji-target-baseline/v1",
            "capture_tool_version": 1,
            "captured_at_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "profile_id": "pending",
            "os_release": {
                "id": "kylin",
                "id_like": ["debian"],
                "version_id": "V10",
                "variant_id": "professional",
                "build_id": "sp1",
            },
            "architecture": {"uname_machine": "x86_64", "dpkg": "amd64"},
            "glibc": {"version": "2.31", "banner": "ldd (GLIBC) 2.31"},
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
                "contract_sha256": hashlib.sha256(dependencies.read_bytes()).hexdigest(),
                "packages": [
                    {
                        "name": "ca-certificates",
                        "status": "install ok installed",
                        "version": "20240203",
                        "architecture": "all",
                    },
                    {
                        "name": "libc6",
                        "status": "install ok installed",
                        "version": "2.31-0kylin",
                        "architecture": "amd64",
                    },
                ],
            },
        }
        module = load_baseline_module()
        profile["profile_id"] = module.compute_profile_id(profile)
        profile_path = temp_root / "target-baseline.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        return dependencies, profile_path, profile

    def render(self, temp_root: Path):
        dependencies, profile_path, profile = self.make_profile(temp_root)
        output = temp_root / "preinst"
        result = subprocess.run(
            [
                sys.executable,
                str(RENDERER),
                "--template",
                str(PREINST),
                "--profile",
                str(profile_path),
                "--depends-file",
                str(dependencies),
                "--output",
                str(output),
                "--max-age-days",
                "30",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output, profile

    def call_verifier_path(
        self,
        rendered: Path,
        os_release: Path,
        arch: str,
        glibc: str,
        canonical_usr_lib_os_release=None,
        expected_owner_uid=None,
    ):
        if canonical_usr_lib_os_release is None:
            canonical_usr_lib_os_release = rendered.parent / "usr/lib/os-release"
        if expected_owner_uid is None:
            expected_owner_uid = os.getuid()
        command = (
            "source \"$1\"; "
            "if verify_target_baseline \"$2\" \"$3\" \"$4\" \"$5\" \"$6\"; then "
            "exit 0; else exit $?; fi"
        )
        return subprocess.run(
            [
                "/bin/bash",
                "-c",
                command,
                "taiji-preinst-test",
                str(rendered),
                str(os_release),
                arch,
                glibc,
                str(canonical_usr_lib_os_release),
                str(expected_owner_uid),
            ],
            text=True,
            capture_output=True,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )

    def call_verifier(self, rendered: Path, os_release_text: str, arch: str, glibc: str):
        os_release = rendered.parent / "os-release"
        os_release.write_text(os_release_text, encoding="utf-8")
        os_release.chmod(0o644)
        return self.call_verifier_path(rendered, os_release, arch, glibc)

    def test_renderer_binds_profile_without_leaving_placeholders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered, profile = self.render(Path(temp_dir))
            text = rendered.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#!/bin/bash -p\n"))
            self.assertIn(profile["profile_id"], text)
            self.assertNotIn("@@TAIJI_BASELINE_", text)
            self.assertEqual(rendered.stat().st_mode & 0o777, 0o755)
            subprocess.run(["/bin/bash", "-n", str(rendered)], check=True)

    def test_rendered_verifier_accepts_exact_os_and_newer_compatible_glibc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered, _ = self.render(Path(temp_dir))
            result = self.call_verifier(
                rendered,
                'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="V10"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                "amd64",
                "2.35",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Target baseline matched", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_rendered_verifier_accepts_safe_usr_lib_os_release_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            rendered, _ = self.render(temp_root)
            expected_target = temp_root / "usr/lib/os-release"
            expected_target.parent.mkdir(parents=True)
            expected_target.write_text(
                'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="V10"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                encoding="utf-8",
            )
            expected_target.chmod(0o644)
            os_release = temp_root / "etc/os-release"
            os_release.parent.mkdir()
            os_release.symlink_to("../usr/lib/os-release")

            result = self.call_verifier_path(
                rendered,
                os_release,
                "amd64",
                "2.31",
                expected_target,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Target baseline matched", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_rendered_verifier_rejects_other_os_release_symlink_even_when_contents_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            rendered, _ = self.render(temp_root)
            expected_target = temp_root / "usr/lib/os-release"
            expected_target.parent.mkdir(parents=True)
            expected_target.write_text("unused\n", encoding="utf-8")
            expected_target.chmod(0o644)
            attacker_target = temp_root / "tmp/attacker-os-release"
            attacker_target.parent.mkdir()
            attacker_target.write_text(
                'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="V10"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                encoding="utf-8",
            )
            attacker_target.chmod(0o644)
            os_release = temp_root / "etc/os-release"
            os_release.parent.mkdir()
            os_release.symlink_to(attacker_target)

            result = self.call_verifier_path(
                rendered,
                os_release,
                "amd64",
                "2.31",
                expected_target,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("unsafe os-release symlink target", result.stderr)
            self.assertNotIn("Target baseline matched", result.stdout)

    def test_rendered_verifier_rejects_writable_os_release_object(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            rendered, _ = self.render(temp_root)
            os_release = temp_root / "os-release"
            os_release.write_text(
                'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="V10"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                encoding="utf-8",
            )
            os_release.chmod(0o666)

            result = self.call_verifier_path(rendered, os_release, "amd64", "2.31")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("os-release file is group/other writable", result.stderr)
            self.assertNotIn("Target baseline matched", result.stdout)

    def test_rendered_verifier_rejects_os_release_with_unexpected_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            rendered, _ = self.render(temp_root)
            os_release = temp_root / "os-release"
            os_release.write_text(
                'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="V10"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                encoding="utf-8",
            )
            os_release.chmod(0o644)

            result = self.call_verifier_path(
                rendered,
                os_release,
                "amd64",
                "2.31",
                expected_owner_uid=os.getuid() + 1,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("os-release file owner is not trusted", result.stderr)
            self.assertNotIn("Target baseline matched", result.stdout)

    def test_rendered_verifier_rejects_other_distribution_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered, _ = self.render(Path(temp_dir))
            result = self.call_verifier(
                rendered,
                'ID="uos"\nID_LIKE="debian"\nVERSION_ID="20"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                "amd64",
                "2.31",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OS ID mismatch", result.stderr)

    def test_rendered_verifier_rejects_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered, _ = self.render(Path(temp_dir))
            result = self.call_verifier(
                rendered,
                'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="V10"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                "arm64",
                "2.31",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("architecture mismatch", result.stderr)

    def test_rendered_verifier_rejects_older_glibc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered, _ = self.render(Path(temp_dir))
            result = self.call_verifier(
                rendered,
                'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="V10"\n'
                'VARIANT_ID="professional"\nBUILD_ID="sp1"\n',
                "amd64",
                "2.28",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("glibc is older", result.stderr)


if __name__ == "__main__":
    unittest.main()
