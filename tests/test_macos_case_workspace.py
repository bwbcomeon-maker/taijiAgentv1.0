"""Lifecycle tests for the Mac physical-DEB verification workspace."""
import importlib.util
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "packaging/linux/macos_case_workspace.py"


class MacCaseWorkspaceTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("macos_case_workspace", SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.calls = []
        self.attached = False
        self.image = None
        self.mount = None

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        action = argv[1]
        if action == "create":
            self.image = Path(argv[-1])
            self.image.write_bytes(b"image")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if action == "attach":
            self.mount = Path(argv[argv.index("-mountpoint") + 1])
            self.attached = True
            payload = {"system-entities": [{"dev-entry": "/dev/disk999s1", "mount-point": str(self.mount)}]}
        elif action == "info":
            payload = {"images": ([{"image-path": str(self.image), "system-entities": [{"dev-entry": "/dev/disk999s1", "mount-point": str(self.mount)}]}] if self.attached else [])}
        elif action == "detach":
            self.attached = False
            if (self.mount / "tmp").exists():
                (self.mount / "tmp").rmdir()
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        else:
            self.fail("unexpected disk operation: " + repr(argv))
        return subprocess.CompletedProcess(argv, 0, plistlib.dumps(payload), b"")

    def test_probe_distinguishes_names_and_cleans_its_files(self):
        result = self.module.case_sensitive(self.root)
        self.assertIsInstance(result, bool)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_preflight_rejects_tampered_helper_before_execution(self):
        preflight = SOURCE.parents[2] / "taijiagent 打包交付/01_制包机_发布预检.sh"
        source = preflight.read_text()
        self.assertIn(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), source)
        block = source[source.index('if [ "$(uname -s)" = Darwin ]'):source.index('trap cleanup_release_temp_artifacts EXIT')]
        block = block.replace('"$(uname -s)"', '"Darwin"')
        helper = self.root / "packaging/linux/macos_case_workspace.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("raise SystemExit('TAMPERED_HELPER_EXECUTED')\n")
        sh = "set -eu\nfail() { echo \"$*\" >&2; exit 1; }\nsha256sum() { " + ("/usr/bin/shasum -a 256" if sys.platform == "darwin" else "/usr/bin/sha256sum") + ' "$@"; }\n' + block
        result = subprocess.run(["/bin/bash", "-p", "-c", sh], env={"PATH": "/usr/bin:/bin", "REPO_ROOT": str(self.root), "REQUIRE_ARTIFACTS": "1"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("摘要不一致", result.stderr)
        self.assertNotIn("TAMPERED_HELPER_EXECUTED", result.stderr)

    def test_symlink_and_writable_parent_are_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(self.module.WorkspaceError):
            self.module.validate_parent(link)
        self.root.chmod(0o777)
        with self.assertRaises(self.module.WorkspaceError):
            self.module.validate_parent(self.root)

    def test_workspace_is_case_checked_and_detached_before_removal(self):
        with patch.object(self.module, "case_sensitive", return_value=True):
            with self.module.workspace(self.root, runner=self.runner) as temporary:
                self.assertEqual(temporary, self.mount / "tmp")
                self.assertEqual(temporary.stat().st_mode & 0o777, 0o700)
                self.assertTrue(self.image.is_file())
            self.assertFalse(self.attached)
            self.assertEqual(list(self.root.iterdir()), [])
        self.assertIn("-nobrowse", self.calls[1])
        self.assertIn("-noautoopen", self.calls[1])
        self.assertIn("Case-sensitive APFS", self.calls[0])

    def test_failed_case_probe_never_yields_workspace(self):
        with patch.object(self.module, "case_sensitive", return_value=False):
            with self.assertRaises(self.module.WorkspaceError):
                with self.module.workspace(self.root, runner=self.runner):
                    self.fail("must not run verifier")
        self.assertFalse(self.attached)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_command_failure_still_detaches(self):
        with patch.object(self.module, "case_sensitive", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "verifier failed"):
                with self.module.workspace(self.root, runner=self.runner):
                    raise RuntimeError("verifier failed")
        self.assertFalse(self.attached)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_partial_attach_failure_is_recovered_by_image_identity(self):
        def fail_attach(argv, **kwargs):
            result = self.runner(argv, **kwargs)
            if argv[1] == "attach":
                raise subprocess.TimeoutExpired(argv, 1)
            return result
        with self.assertRaises(subprocess.TimeoutExpired):
            with self.module.workspace(self.root, runner=fail_attach):
                self.fail("must not run verifier")
        self.assertFalse(self.attached)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_detach_retains_image_and_fails_closed(self):
        def fail_detach(argv, **kwargs):
            if argv[1] == "detach":
                raise subprocess.CalledProcessError(1, argv)
            return self.runner(argv, **kwargs)
        with patch.object(self.module, "case_sensitive", return_value=True):
            with self.assertRaises(self.module.WorkspaceError):
                with self.module.workspace(self.root, runner=fail_detach):
                    pass
        self.assertTrue(self.image.exists())
        self.assertTrue(self.attached)

    def test_low_host_space_prevents_image_creation(self):
        with patch.object(self.module.shutil, "disk_usage", return_value=type("Usage", (), {"free": 1})()):
            with self.assertRaises(self.module.WorkspaceError):
                with self.module.workspace(self.root, runner=self.runner):
                    self.fail("must not allocate image")
        self.assertEqual(self.calls, [])

    def test_create_failure_leaves_no_temporary_workspace(self):
        def fail_create(argv, **kwargs):
            if argv[1] == "create":
                raise subprocess.CalledProcessError(1, argv)
            return self.runner(argv, **kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            with self.module.workspace(self.root, runner=fail_create):
                self.fail("must not run verifier")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_foreign_mount_point_is_not_detached_or_deleted(self):
        def foreign(argv, **kwargs):
            result = self.runner(argv, **kwargs)
            if argv[1] == "info" and self.attached:
                payload = plistlib.loads(result.stdout)
                payload["images"][0]["system-entities"][0]["mount-point"] = "/unrelated"
                result.stdout = plistlib.dumps(payload)
            return result
        with self.assertRaises(self.module.WorkspaceError):
            with self.module.workspace(self.root, runner=foreign):
                self.fail("must reject identity mismatch")
        self.assertTrue(self.image.exists())
        self.assertFalse(any(call[1] == "detach" for call in self.calls))

    def test_child_exit_and_tmpdir_are_preserved(self):
        result = self.module.run_child([sys.executable, "-c", "import os,sys; sys.exit(7 if os.environ['TMPDIR']==os.environ['TEMP']==os.environ['TMP'] else 9)"], self.root)
        self.assertEqual(result, 7)

    def test_interruption_stops_child_group_before_cleanup(self):
        with patch.object(self.module.subprocess, "Popen") as popen, patch.object(self.module.os, "killpg") as killpg:
            child = popen.return_value
            child.pid = 12345
            child.poll.return_value = None
            child.wait.side_effect = [InterruptedError(signal.SIGTERM), 0]
            with self.assertRaises(InterruptedError):
                self.module.run_child(["/usr/bin/true"], self.root)
            killpg.assert_called_once_with(12345, signal.SIGTERM)
            self.assertEqual(child.wait.call_count, 2)

    def test_linux_refuses_image_execution(self):
        with patch.object(self.module.sys, "platform", "linux"), patch.object(self.module.sys, "argv", [str(SOURCE), "--parent", str(self.root), "--", "/usr/bin/true"]), patch.object(self.module, "workspace") as allocate:
            with self.assertRaises(SystemExit) as result:
                self.module.main()
            self.assertEqual(result.exception.code, 2)
            allocate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
