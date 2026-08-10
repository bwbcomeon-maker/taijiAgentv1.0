import json
import os
import shlex
import shutil
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

    def test_ldconfig_parser_accepts_only_x86_64_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered = self.render(Path(temp_dir))
            command = 'source "$1"; ldconfig_cache_has_amd64_soname "$2"'
            i386_only = subprocess.run(
                ["/bin/bash", "-c", command, "taiji-preinst-test", str(rendered), "libdbus-1.so.3"],
                input="libdbus-1.so.3 (libc6) => /lib/i386-linux-gnu/libdbus-1.so.3\n",
                text=True,
                capture_output=True,
                check=False,
            )
            amd64 = subprocess.run(
                ["/bin/bash", "-c", command, "taiji-preinst-test", str(rendered), "libdbus-1.so.3"],
                input=(
                    "libdbus-1.so.3 (libc6,x86-64, OS ABI: Linux 3.2.0) "
                    "=> /lib/x86_64-linux-gnu/libdbus-1.so.3\n"
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(i386_only.returncode, 0)
            self.assertEqual(amd64.returncode, 0, amd64.stderr)

    def make_root(self, temp_root: Path, *, os_id="kylin") -> tuple[Path, Path]:
        root = temp_root / "root"
        (root / "etc").mkdir(parents=True)
        (root / "usr/lib").mkdir(parents=True)
        (root / "usr/bin").mkdir(parents=True)
        (root / "usr/share/xsessions").mkdir(parents=True)
        (root / "usr/lib/x86_64-linux-gnu").mkdir(parents=True)
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
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        for soname in policy["elf"]["required_system_sonames"]:
            (root / "usr/lib/x86_64-linux-gnu" / soname).touch()
        return root, root / "etc/os-release"

    def add_kysec_getstatus(
        self,
        root: Path,
        *,
        output="KySec status: enabled\n\nexec control : off\n",
        exit_code=0,
        marker=True,
    ) -> Path:
        if marker:
            (root / "etc/kysec").mkdir(exist_ok=True)
        tool_dir = root / "usr/sbin"
        tool_dir.mkdir(parents=True, exist_ok=True)
        tool = tool_dir / "getstatus"
        tool.write_text(
            "#!/bin/sh\n"
            f"printf '%s' {shlex.quote(output)}\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
        return tool

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
        effective_uid=None,
        result_path=None,
        predictable_temp_target=None,
        fake_mktemp=False,
        fake_mv=False,
        trace_canary=False,
        probe_owner_mismatch_path=None,
        opt_path_override=None,
        path_prefix=None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        if owner_uid is None:
            owner_uid = os.getuid()
        if effective_uid is None:
            effective_uid = owner_uid
        if result_path is None:
            result_path = root / "var/lib/taiji-agent/preflight.json"
        fake_bin = str(path_prefix or "")
        if fake_mktemp or fake_mv or trace_canary or probe_owner_mismatch_path:
            fake_bin_path = root / ".fake-bin"
            fake_bin_path.mkdir(parents=True, exist_ok=True)
            if fake_mktemp or trace_canary:
                real_mktemp = shutil.which("mktemp") or "/usr/bin/mktemp"
                fake_mktemp_path = fake_bin_path / "mktemp"
                if fake_mktemp:
                    fake_mktemp_path.write_text(
                        "#!/bin/sh\n"
                        f"state={shlex.quote(str(root / '.fake-mktemp-used'))}\n"
                        "if [ ! -e \"$state\" ]; then : > \"$state\"; exit 1; fi\n"
                        f"exec {shlex.quote(real_mktemp)} \"$@\"\n",
                        encoding="utf-8",
                    )
                else:
                    fake_mktemp_path.write_text(
                        "#!/bin/sh\n"
                        f"trace={shlex.quote(str(root / '.canary-called'))}\n"
                        "case \"${1:-}\" in */.taiji-preflight.*) : > \"$trace\" ;; esac\n"
                        f"exec {shlex.quote(real_mktemp)} \"$@\"\n",
                        encoding="utf-8",
                    )
                fake_mktemp_path.chmod(0o755)
            if fake_mv:
                fake_mv_path = fake_bin_path / "mv"
                fake_mv_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                fake_mv_path.chmod(0o755)
            if probe_owner_mismatch_path:
                real_stat = shutil.which("stat") or "/usr/bin/stat"
                fake_stat_path = fake_bin_path / "stat"
                fake_stat_path.write_text(
                    "#!/bin/sh\n"
                    "last=''\n"
                    "for argument in \"$@\"; do last=\"$argument\"; done\n"
                    f"probe={shlex.quote(str(probe_owner_mismatch_path))}\n"
                    "if [ \"$last\" = \"$probe\" ]; then printf '99999:755:2'; exit 0; fi\n"
                    f"exec {shlex.quote(real_stat)} \"$@\"\n",
                    encoding="utf-8",
                )
                fake_stat_path.chmod(0o755)
            fake_bin = str(fake_bin_path)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        command = (
            "TAIJI_TEST_EFFECTIVE_UID=\"$9\"; "
            "id() { if [ \"${1:-}\" = \"-u\" ]; then printf '%s' \"$TAIJI_TEST_EFFECTIVE_UID\"; else command id \"$@\"; fi; }; "
            "if [ -n \"${10:-}\" ]; then ln -s \"${10}\" \"${8}.tmp.$$\"; fi; "
            "source \"$1\"; "
            "if [ -n \"${11:-}\" ]; then PATH=\"${11}:$PATH\"; export PATH; fi; "
            "if [ -n \"${12:-}\" ]; then "
            "TAIJI_TEST_OPT_PATH=\"${12}\"; "
            "root_path() { "
            "local test_root=\"$1\" test_path=\"$2\"; "
            "if [ \"$test_path\" = \"$TAIJI_INSTALL_ROOT_PARENT\" ]; then "
            "printf '%s' \"$TAIJI_TEST_OPT_PATH\"; return; fi; "
            "if [ \"$test_root\" = / ]; then printf '%s' \"$test_path\"; "
            "elif [ \"$test_path\" = \"$test_root\" ] || [[ \"$test_path\" == \"$test_root\"/* ]]; then "
            "printf '%s' \"$test_path\"; "
            "elif [[ \"$test_path\" == /* ]]; then printf '%s%s' \"$test_root\" \"$test_path\"; "
            "else printf '%s/%s' \"${test_root%/}\" \"$test_path\"; fi; "
            "}; fi; "
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
                str(effective_uid),
                str(predictable_temp_target or ""),
                fake_bin,
                str(opt_path_override or ""),
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertTrue(result_path.exists(), completed.stderr)
        self.assertEqual(result_path.stat().st_mode & 0o777, 0o600)
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

    def test_missing_desktop_systemd_loopback_or_disk_is_blocked(self):
        cases = (
            ("desktop", "TAIJI-LINUX-E006-DESKTOP"),
            ("systemd", "TAIJI-LINUX-E007-SYSTEMD"),
            ("loopback", "TAIJI-LINUX-E008-LOOPBACK"),
            ("disk", "TAIJI-LINUX-E009-DISK"),
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
                elif missing == "disk":
                    (root / ".taiji-disk-headroom-mib").write_text("0\n", encoding="utf-8")
                rendered = self.render(temp_root)
                self.assert_blocked(rendered, root, os_release, code)

    def test_missing_opt_parent_uses_nearest_existing_trusted_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            (root / "opt").rmdir()
            (root / ".taiji-disk-headroom-mib").write_text(
                "8192\n", encoding="utf-8"
            )
            rendered = self.render(temp_root)

            self.assert_compatible(rendered, root, os_release)
            self.assertFalse((root / "opt").exists())

    def test_opt_parent_symlink_or_non_directory_is_blocked(self):
        for unsafe_type in ("symlink", "broken_symlink", "file"):
            with self.subTest(unsafe_type=unsafe_type), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                (root / "opt").rmdir()
                if unsafe_type == "symlink":
                    (root / "opt").symlink_to("usr")
                elif unsafe_type == "broken_symlink":
                    (root / "opt").symlink_to("missing-opt-target")
                else:
                    (root / "opt").write_text("not a directory\n", encoding="utf-8")
                rendered = self.render(temp_root)

                self.assert_blocked(
                    rendered, root, os_release, "TAIJI-LINUX-E009-DISK"
                )

    def test_missing_opt_still_enforces_ancestor_preflight_guards(self):
        cases = (
            ("disk", "TAIJI-LINUX-E009-DISK"),
            ("noexec", "TAIJI-LINUX-E012-OPT-NOEXEC"),
            ("sandbox", "TAIJI-LINUX-E013-SANDBOX"),
            ("unsafe_mode", "TAIJI-LINUX-E009-DISK"),
        )
        for guard, code in cases:
            with self.subTest(guard=guard), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                (root / "opt").rmdir()
                if guard == "disk":
                    (root / ".taiji-disk-headroom-mib").write_text(
                        "0\n", encoding="utf-8"
                    )
                elif guard == "noexec":
                    (root / ".taiji-noexec").touch()
                elif guard == "unsafe_mode":
                    root.chmod(0o777)
                rendered = self.render(temp_root)

                self.assert_blocked(
                    rendered,
                    root,
                    os_release,
                    code,
                    fake_mktemp=(guard == "sandbox"),
                )

    def test_untrusted_existing_opt_never_receives_a_canary(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            (root / "opt").chmod(0o777)
            rendered = self.render(temp_root)

            self.assert_blocked(
                rendered,
                root,
                os_release,
                "TAIJI-LINUX-E009-DISK",
                trace_canary=True,
            )
            self.assertFalse((root / ".canary-called").exists())

    def test_probe_owner_mismatch_is_blocked_before_any_canary(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)

            self.assert_blocked(
                rendered,
                root,
                os_release,
                "TAIJI-LINUX-E009-DISK",
                trace_canary=True,
                probe_owner_mismatch_path=root / "opt",
            )
            self.assertFalse((root / ".canary-called").exists())

    def test_probe_cannot_escape_root_prefix_or_receive_a_canary(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            outside = temp_root / "outside"
            outside.mkdir(mode=0o755)
            rendered = self.render(temp_root)

            self.assert_blocked(
                rendered,
                root,
                os_release,
                "TAIJI-LINUX-E009-DISK",
                trace_canary=True,
                opt_path_override=outside,
            )
            self.assertFalse((root / ".canary-called").exists())

    def test_missing_policy_required_system_soname_is_blocked_before_unpack(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            missing = policy["elf"]["required_system_sonames"][0]
            (root / "usr/lib/x86_64-linux-gnu" / missing).unlink()
            rendered = self.render(temp_root)
            payload = self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E014-RUNTIME"
            )
            self.assertIn("TAIJI-LINUX-E014-RUNTIME", payload["failed_capabilities"])

    def test_opt_noexec_or_sandbox_denial_is_blocked_before_install(self):
        cases = (
            ("noexec", "TAIJI-LINUX-E012-OPT-NOEXEC"),
            ("sandbox", "TAIJI-LINUX-E013-SANDBOX"),
        )
        for marker, code in cases:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                if marker == "noexec":
                    (root / "opt/.taiji-noexec").touch()
                rendered = self.render(temp_root)
                payload = self.assert_blocked(
                    rendered, root, os_release, code, fake_mktemp=(marker == "sandbox")
                )
                self.assertFalse((root / "opt/taiji-agent").exists())
                self.assertEqual(payload["status"], "BLOCKED")

    def test_trusted_kysec_exec_control_off_is_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            self.add_kysec_getstatus(root)
            rendered = self.render(temp_root)

            self.assert_compatible(rendered, root, os_release)

    def test_getstatus_itself_is_a_kysec_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            self.add_kysec_getstatus(root, marker=False)
            rendered = self.render(temp_root)

            self.assert_compatible(rendered, root, os_release)

    def test_broken_getstatus_symlink_is_an_untrusted_kysec_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            tool = self.add_kysec_getstatus(root, marker=False)
            tool.unlink()
            tool.symlink_to("missing-getstatus")
            rendered = self.render(temp_root)

            self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E011-KYSEC"
            )

    def test_kysec_exec_control_on_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            self.add_kysec_getstatus(
                root,
                output="KySec status: enabled\nexec control : on\n",
            )
            rendered = self.render(temp_root)

            payload = self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E011-KYSEC"
            )
            self.assertEqual(payload["reason_zh"], "Kysec exec control 已开启")

    def test_kysec_unknown_or_untrusted_state_fails_closed(self):
        cases = (
            ("missing_tool", None, 0),
            ("nonzero", "exec control : off\n", 7),
            ("missing_line", "KySec status: enabled\n", 0),
            ("duplicate", "exec control : off\nexec control : off\n", 0),
            ("unknown", "exec control : audit\n", 0),
            ("substring", "note: exec control : off but uncertain\n", 0),
        )
        for case, output, exit_code in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                if output is None:
                    (root / "etc/kysec").mkdir()
                else:
                    self.add_kysec_getstatus(
                        root,
                        output=output,
                        exit_code=exit_code,
                    )
                rendered = self.render(temp_root)

                payload = self.assert_blocked(
                    rendered, root, os_release, "TAIJI-LINUX-E011-KYSEC"
                )
                self.assertEqual(
                    payload["reason_zh"],
                    "无法可信确认 Kysec exec control 已关闭",
                )

    def test_kysec_status_allows_controlled_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            self.add_kysec_getstatus(
                root,
                output="KySec status: enabled\n  exec   control  :  off  \n",
            )
            rendered = self.render(temp_root)

            self.assert_compatible(rendered, root, os_release)

    def test_untrusted_getstatus_files_fail_closed(self):
        cases = (
            "symlink",
            "broken_symlink",
            "not_executable",
            "writable",
            "hardlink",
            "owner",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                tool = self.add_kysec_getstatus(root)
                owner_mismatch = None
                if case == "symlink":
                    tool.unlink()
                    tool.symlink_to("/bin/true")
                elif case == "broken_symlink":
                    tool.unlink()
                    tool.symlink_to("missing-getstatus")
                elif case == "not_executable":
                    tool.chmod(0o644)
                elif case == "writable":
                    tool.chmod(0o775)
                elif case == "hardlink":
                    os.link(tool, tool.with_name("getstatus-copy"))
                elif case == "owner":
                    owner_mismatch = tool
                rendered = self.render(temp_root)

                self.assert_blocked(
                    rendered,
                    root,
                    os_release,
                    "TAIJI-LINUX-E011-KYSEC",
                    probe_owner_mismatch_path=owner_mismatch,
                )

    def test_untrusted_getstatus_parent_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            self.add_kysec_getstatus(root)
            (root / "usr/sbin").chmod(0o777)
            rendered = self.render(temp_root)

            self.assert_blocked(
                rendered, root, os_release, "TAIJI-LINUX-E011-KYSEC"
            )

    def test_kysec_ignores_path_injected_getstatus(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            self.add_kysec_getstatus(root)
            fake_bin = root / ".fake-bin"
            fake_bin.mkdir()
            fake_tool = fake_bin / "getstatus"
            fake_tool.write_text(
                "#!/bin/sh\nprintf 'exec control : on\\n'\n",
                encoding="utf-8",
            )
            fake_tool.chmod(0o755)
            rendered = self.render(temp_root)

            self.assert_compatible(
                rendered,
                root,
                os_release,
                path_prefix=fake_bin,
            )

    def test_kysec_off_does_not_mask_other_preflight_failures(self):
        cases = (
            ("noexec", "TAIJI-LINUX-E012-OPT-NOEXEC"),
            ("sandbox", "TAIJI-LINUX-E013-SANDBOX"),
            ("runtime", "TAIJI-LINUX-E014-RUNTIME"),
        )
        for case, code in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                temp_root = Path(directory)
                root, os_release = self.make_root(temp_root)
                self.add_kysec_getstatus(root)
                fake_mktemp = case == "sandbox"
                if case == "noexec":
                    (root / "opt/.taiji-noexec").touch()
                elif case == "runtime":
                    policy = json.loads(POLICY.read_text(encoding="utf-8"))
                    missing = policy["elf"]["required_system_sonames"][0]
                    (root / "usr/lib/x86_64-linux-gnu" / missing).unlink()
                rendered = self.render(temp_root)

                payload = self.assert_blocked(
                    rendered,
                    root,
                    os_release,
                    code,
                    fake_mktemp=fake_mktemp,
                )
                self.assertNotIn("TAIJI-LINUX-E011-KYSEC", payload["failed_capabilities"])

    def test_os_release_symlink_owner_and_mode_are_hardened(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            os_release.unlink()
            os_release.symlink_to("/usr/lib/os-release")
            rendered = self.render(temp_root)
            self.assert_compatible(rendered, root, os_release)

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
            os_release.unlink()
            os_release.write_text('ID="kylin"\n', encoding="utf-8")
            os_release.chmod(0o644)
            self.assert_blocked(
                rendered,
                root,
                os_release,
                "TAIJI-LINUX-E002-OS",
                owner_uid=os.getuid() + 1,
                effective_uid=os.getuid() + 1,
            )

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            self.assert_blocked(
                rendered,
                root,
                os_release,
                "TAIJI-LINUX-E010-PRIVILEGE",
                owner_uid=os.getuid(),
                effective_uid=os.getuid() + 1,
            )

    def test_result_never_contains_certified_or_machine_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            root, os_release = self.make_root(temp_root)
            rendered = self.render(temp_root)
            self.assertIn(
                'verify_compatibility /etc/os-release "$arch" "$glibc_version" "$kernel_version" / 0 /var/lib/taiji-agent/preflight.json',
                rendered.read_text(encoding="utf-8"),
            )
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

            victim = root / "victim.txt"
            victim.write_text("keep-me\n", encoding="utf-8")
            result_path = root / "var/lib/taiji-agent/preflight.json"
            self.assert_blocked(
                rendered,
                root,
                os_release,
                "TAIJI-LINUX-E003-DPKG",
                result_path=result_path,
                predictable_temp_target=victim,
            )
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep-me\n")
            temp_candidates = list(result_path.parent.glob(result_path.name + ".tmp.*"))
            self.assertTrue(temp_candidates)
            self.assertTrue(all(path.is_symlink() for path in temp_candidates))
            for path in temp_candidates:
                path.unlink()

            old_content = result_path.read_bytes()
            completed, _ = self.call_verifier(
                rendered,
                root,
                os_release,
                result_path=result_path,
                fake_mv=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(result_path.read_bytes(), old_content)
            remaining_temps = list(result_path.parent.glob(result_path.name + ".tmp.*"))
            self.assertFalse(remaining_temps)


if __name__ == "__main__":
    unittest.main()
