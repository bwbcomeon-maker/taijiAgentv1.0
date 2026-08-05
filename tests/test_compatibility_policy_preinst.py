import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "packaging/linux/deb/render-preinst.py"
TEMPLATE = ROOT / "packaging/linux/deb/preinst"
POLICY = ROOT / "packaging/linux/compatibility-policy.json"


class CompatibilityPolicyPreinstTest(unittest.TestCase):
    def render(self, temp_root: Path) -> Path:
        output = temp_root / "build/DEBIAN/preinst"
        result = subprocess.run(
            [
                sys.executable,
                str(RENDERER),
                "--template",
                str(TEMPLATE),
                "--policy",
                str(POLICY),
                "--output",
                str(output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.exists())
        self.assertEqual(output.stat().st_mode & 0o777, 0o755)
        subprocess.run(["/bin/bash", "-n", str(output)], check=True)
        return output

    def make_root(self, temp_root: Path, *, os_id="kylin") -> tuple[Path, Path]:
        root = temp_root / "root"
        (root / "etc").mkdir(parents=True)
        (root / "usr/lib").mkdir(parents=True)
        (root / "usr/bin").mkdir(parents=True)
        (root / "usr/share/xsessions").mkdir(parents=True)
        (root / "sys/class/net/lo").mkdir(parents=True)
        (root / "opt").mkdir(parents=True)
        for command in ("apt-get", "dpkg", "systemctl"):
            path = root / "usr/bin" / command
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)
        (root / "usr/lib/os-release").write_text(
            f'ID="{os_id}"\nID_LIKE="debian"\nVERSION_ID="V10.9-test"\n',
            encoding="utf-8",
        )
        (root / "usr/lib/os-release").chmod(0o644)
        (root / "etc/os-release").symlink_to("../usr/lib/os-release")
        return root, root / "etc/os-release"

    def call_verifier(
        self,
        rendered: Path,
        root: Path,
        os_release: Path,
        *,
        arch="amd64",
        glibc="2.31",
        kernel="5.10.0",
        owner_uid=None,
        result_path=None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        if owner_uid is None:
            owner_uid = os.getuid()
        if result_path is None:
            result_path = root / "var/lib/taiji-agent/preflight.json"
        command = (
            "source \"$1\"; "
            "verify_compatibility \"$2\" \"$3\" \"$4\" \"$5\" \"$6\" \"$7\" \"$8\""
        )
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": "/nonexistent",
        }
        completed = subprocess.run(
            [
                "/bin/bash",
                "-c",
                command,
                "taiji-preinst-test",
                str(rendered),
                str(os_release),
                arch,
                glibc,
                kernel,
                str(root),
                str(owner_uid),
                str(result_path),
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertTrue(result_path.exists(), completed.stderr)
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        return completed, payload

    def assert_compatible(self, rendered: Path, root: Path, os_release: Path, **kwargs):
        completed, payload = self.call_verifier(rendered, root, os_release, **kwargs)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["schema"], "taiji-install-preflight/v1")
        self.assertEqual(payload["status"], "COMPATIBLE")
        self.assertEqual(payload["error_code"], "")
        self.assertEqual(payload["failed_capabilities"], [])
        self.assertEqual(payload["reason_zh"], "兼容能力预检通过")

    def assert_blocked(self, rendered: Path, root: Path, os_release: Path, code: str, **kwargs):
        completed, payload = self.call_verifier(rendered, root, os_release, **kwargs)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(payload["schema"], "taiji-install-preflight/v1")
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["error_code"], code)
        self.assertIn(code, payload["failed_capabilities"])
        self.assertNotIn("CERTIFIED", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("hostname", json.dumps(payload, ensure_ascii=False))
        return payload

    def test_all_three_families_accept_arbitrary_patch_strings(self):
        for os_id in ("kylin", "uos", "openkylin"):
            with self.subTest(os_id=os_id), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root, os_id=os_id)
                rendered = self.render(temp_root)
                self.assert_compatible(rendered, root, os_release)

    def test_newer_glibc_and_kernel_are_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            self.assert_compatible(rendered, root, os_release, glibc="2.39", kernel="6.8.0-31-generic")

    def test_arm_unknown_os_and_rpm_only_are_blocked(self):
        cases = (
            ("arm64", "kylin", "TAIJI-LINUX-E001-ARCH"),
            ("amd64", "fedora", "TAIJI-LINUX-E002-OS"),
        )
        for arch, os_id, code in cases:
            with self.subTest(arch=arch, os_id=os_id), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root, os_id=os_id)
                rendered = self.render(temp_root)
                self.assert_blocked(rendered, root, os_release, code, arch=arch)

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            (root / "usr/bin/dpkg").unlink()
            rendered = self.render(temp_root)
            self.assert_blocked(rendered, root, os_release, "TAIJI-LINUX-E003-DPKG")

    def test_old_glibc_and_kernel_have_stable_error_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E004-GLIBC", glibc="2.30"
            )

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E005-KERNEL", kernel="4.18.0"
            )

    def test_missing_desktop_systemd_loopback_opt_or_disk_is_blocked(self):
        cases = (
            ("desktop", "TAIJI-LINUX-E006-DESKTOP"),
            ("systemd", "TAIJI-LINUX-E007-SYSTEMD"),
            ("loopback", "TAIJI-LINUX-E008-LOOPBACK"),
            ("opt", "TAIJI-LINUX-E009-DISK"),
        )
        for missing, code in cases:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                if missing == "desktop":
                    (root / "usr/share/xsessions").rmdir()
                elif missing == "systemd":
                    (root / "usr/bin/systemctl").unlink()
                elif missing == "loopback":
                    (root / "sys/class/net/lo").rmdir()
                elif missing == "opt":
                    (root / "opt").rmdir()
                rendered = self.render(temp_root)
                self.assert_blocked(rendered, root, os_release, code)

    def test_opt_noexec_known_kysec_or_sandbox_denial_is_blocked_before_install(self):
        cases = (
            ("noexec", "TAIJI-LINUX-E012-OPT-NOEXEC"),
            ("kysec", "TAIJI-LINUX-E011-KYSEC"),
            ("sandbox", "TAIJI-LINUX-E013-SANDBOX"),
        )
        for marker, code in cases:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                if marker == "noexec":
                    (root / "opt/.taiji-noexec").touch()
                elif marker == "kysec":
                    (root / "etc/kysec").mkdir()
                else:
                    (root / "opt").chmod(0o555)
                rendered = self.render(temp_root)
                payload = self.assert_blocked(rendered, root, os_release, code)
                self.assertFalse((root / "opt/taiji-agent").exists())
                self.assertEqual(payload["status"], "BLOCKED")

    def test_os_release_symlink_owner_and_mode_are_hardened(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            os_release.unlink()
            os_release.symlink_to("../tmp/attacker-os-release")
            (root / "tmp").mkdir()
            (root / "tmp/attacker-os-release").write_text('ID="kylin"\n', encoding="utf-8")
            self.assert_blocked(rendered, root, os_release, "TAIJI-LINUX-E002-OS")

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            target = root / "usr/lib/os-release"
            target.chmod(0o666)
            self.assert_blocked(rendered, root, os_release, "TAIJI-LINUX-E002-OS")

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E010-PRIVILEGE", owner_uid=os.getuid() + 1
            )

    def test_result_never_contains_certified_or_machine_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            _, payload = self.call_verifier(rendered, root, os_release)
            serialized = json.dumps(payload, ensure_ascii=False).lower()
            for forbidden in ("certified", "hostname", "username", "ip", "mac", "serial", "machine"):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(set(payload), {
                "schema", "status", "policy_id", "compatibility_policy_sha256",
                "error_code", "reason_zh", "failed_capabilities",
            })

    def test_failure_creates_no_service_or_user_business_data(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            (root / "usr/bin/dpkg").unlink()
            self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E003-DPKG"
            )
            self.assertFalse((root / "etc/systemd/system").exists())
            self.assertFalse((root / "home").exists())
            self.assertFalse((root / "root/.config/taiji-agent").exists())
            self.assertFalse((root / "opt/taiji-agent").exists())


if __name__ == "__main__":
    unittest.main()
