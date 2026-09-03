"""Fail-closed execution-environment contract for the formal release path."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "scripts/taiji-linux-golden-orchestrator.py"
RELEASE_TEST_RUNNER = ROOT / "scripts/run-taiji-release-python-tests.py"
PYTHON38_GATE = ROOT / "tests/python38_linux_packaging_gate.py"
RUNBOOK = ROOT / "docs/runbooks/taiji-kylin-uos-offline-delivery.md"
OPERATOR_GUIDE = ROOT / "taijiagent 打包交付/操作说明.md"
BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
PREPARE = ROOT / "taijiagent 打包交付/99_本机_准备制包输入包.sh"
SETUP_LOCAL = ROOT / "hermes-local-lab/scripts/setup-local.sh"
BUILD_DEB = ROOT / "packaging/linux/deb/build-deb.sh"
HARDENED_RELEASE_SHELLS = (
    PREPARE,
    ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh",
    ROOT / "scripts/sign-taiji-release-evidence.sh",
    ROOT / "scripts/taiji-release-check.sh",
    ROOT / "packaging/linux/deb/publish-single-deb.sh",
)


class ReleaseExecutionEnvironmentContractTests(unittest.TestCase):
    def _dpkg_resolver_program(self):
        source = (ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh").read_text()
        match = re.search(r"resolve_release_dpkg_deb\(\) \{\n  /usr/bin/python3 -I -B - <<'PY'\n(.*?)\nPY\n\}", source, re.S)
        self.assertIsNotNone(match, "release preflight needs an explicit dpkg-deb resolver")
        return match.group(1)

    def test_release_dpkg_resolver_uses_fixed_paths_and_rejects_unsafe_tools(self):
        program = self._dpkg_resolver_program()
        for case in ("system", "brew-symlink", "admin-directory", "group-writable-directory", "linux-no-brew", "missing", "escape", "escape-return", "writable-tool", "writable-prefix"):
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="taiji-dpkg-resolver-") as tmp:
                base = Path(tmp).resolve()
                system, brew, intel = [base / name for name in ("usr", "brew", "intel")]
                for prefix in (system, brew, intel):
                    (prefix / "bin").mkdir(parents=True)
                candidate = system / "bin/dpkg-deb" if case == "system" else brew / "bin/dpkg-deb"
                target = candidate
                if case == "brew-symlink":
                    target = brew / "Cellar/dpkg/1/bin/dpkg-deb"
                    target.parent.mkdir(parents=True)
                    candidate.symlink_to(target)
                if case == "escape":
                    target = base / "outside-dpkg"
                    candidate.symlink_to(target)
                if case == "escape-return":
                    target = brew / "Cellar/dpkg/1/bin/dpkg-deb"
                    target.parent.mkdir(parents=True)
                    hop = base / "outside-hop"
                    hop.symlink_to(target)
                    candidate.symlink_to(hop)
                if case != "missing":
                    target.write_text("#!/bin/sh\nexit 0\n")
                    target.chmod(0o777 if case == "writable-tool" else 0o755)
                if case == "writable-prefix":
                    brew.chmod(0o777)
                if case in ("admin-directory", "group-writable-directory"):
                    (brew / "bin").chmod(0o775)
                hostile = base / "hostile"
                hostile.mkdir()
                (hostile / "dpkg-deb").write_text("#!/bin/sh\nexit 0\n")
                (hostile / "dpkg-deb").chmod(0o755)
                fixture = program.replace('"/usr"', json.dumps(str(system))).replace('"/opt/homebrew"', json.dumps(str(brew))).replace('"/usr/local"', json.dumps(str(intel)))
                fixture = fixture.replace('grp.getgrnam("admin").gr_gid', str(os.getgid() if case == "admin-directory" else -1))
                fixture = "import sys\nsys.platform = " + repr("linux" if case in ("system", "linux-no-brew") else "darwin") + "\n" + fixture
                result = subprocess.run([sys.executable, "-I", "-B", "-c", fixture], env={"PATH": str(hostile)}, text=True, capture_output=True, check=False)
                if case in ("system", "brew-symlink", "admin-directory"):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), str(target))
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_release_shells_use_privileged_bash_and_reset_injection_environment(self):
        for path in (BUILDER, *HARDENED_RELEASE_SHELLS):
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text(encoding="utf-8")
                self.assertEqual(source.splitlines()[0], "#!/bin/bash -p")
                self.assertIn("PATH=/usr/bin:/bin", source[:1600])
                self.assertIn("export PATH", source[:1600])
                for name in (
                    "BASH_ENV",
                    "ENV",
                    "PYTHONHOME",
                    "PYTHONPATH",
                    "PYTHONSTARTUP",
                    "LD_PRELOAD",
                    "LD_LIBRARY_PATH",
                ):
                    self.assertIn(name, source[:2200])

    def test_formal_commands_never_reenter_unprivileged_bash(self):
        prepare = PREPARE.read_text(encoding="utf-8")
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn('/bin/bash -p "$SCRIPT_DIR/01_制包机_发布预检.sh"', prepare)
        self.assertIn('/bin/bash -p ./00_制包机_生成离线交付包.sh', prepare)
        self.assertNotRegex(prepare, r"(?m)(?<!/)\bbash[ \t]")
        self.assertNotRegex(builder, r"(?m)(?<!/)\bbash[ \t]")
        self.assertIn('/bin/bash -p ./scripts/setup-local.sh', builder)
        self.assertIn('/bin/bash -p ./packaging/linux/deb/build-deb.sh', builder)
        for child in (SETUP_LOCAL, BUILD_DEB):
            source = child.read_text(encoding="utf-8")
            self.assertEqual(source.splitlines()[0], "#!/bin/bash -p")
            self.assertIn("unset BASH_ENV ENV CDPATH GLOBIGNORE", source[:1000])
        for path in (RUNBOOK, OPERATOR_GUIDE):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"(?m)^(?:[A-Z_][A-Z0-9_]*=[^\n]*[ \t]+)?bash[ \t]+(?:\./|scripts/|\"taijiagent)",
            )

    def test_privileged_nested_bash_does_not_reimport_exported_functions(self):
        with tempfile.TemporaryDirectory(prefix="taiji-hostile-bash-func-") as temp:
            marker = Path(temp) / "function-ran"
            environment = os.environ.copy()
            environment[
                "BASH_FUNC_sha256sum%%"
            ] = "() {{ /usr/bin/touch {!r}; }}".format(str(marker))
            environment[
                "BASH_FUNC_python3%%"
            ] = "() {{ /usr/bin/touch {!r}; }}".format(str(marker))
            probe = subprocess.run(
                [
                    "/bin/bash",
                    "-p",
                    "-c",
                    "/bin/bash -p -c 'type -t sha256sum; type -t python3'",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            self.assertNotIn("function", probe.stdout.splitlines())
            self.assertFalse(marker.exists())

    def test_release_shells_have_no_bare_python_or_unprivileged_nested_bash(self):
        bare_python = re.compile(r"(?<![/A-Za-z0-9_.-])python3(?=[ \t])")
        for path in HARDENED_RELEASE_SHELLS:
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text(encoding="utf-8")
                self.assertIsNone(bare_python.search(source))
                self.assertIn("/usr/bin/python3 -I -B", source)
        release_check = (ROOT / "scripts/taiji-release-check.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('/bin/bash -p "$DELIVERY_DIR/01_制包机_发布预检.sh"', release_check)
        self.assertNotRegex(release_check, r"(?m)(?<!/)bash \"")

    def test_direct_release_entrypoints_ignore_bash_env_functions_and_pythonpath(self):
        signer = ROOT / "scripts/sign-taiji-release-evidence.sh"
        publisher = ROOT / "packaging/linux/deb/publish-single-deb.sh"
        with tempfile.TemporaryDirectory(prefix="taiji-hostile-release-env-") as temp:
            root = Path(temp)
            bash_marker = root / "bash-env-ran"
            function_marker = root / "python-function-ran"
            python_marker = root / "pythonpath-ran"
            bash_env = root / "hostile-bash-env.sh"
            bash_env.write_text(
                "/usr/bin/touch {!r}\n".format(str(bash_marker)),
                encoding="utf-8",
            )
            hostile_python = root / "hostile-python"
            hostile_python.mkdir()
            (hostile_python / "sitecustomize.py").write_text(
                "from pathlib import Path\nPath({!r}).touch()\n".format(
                    str(python_marker)
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "BASH_ENV": str(bash_env),
                    "ENV": str(bash_env),
                    "PYTHONPATH": str(hostile_python),
                    "BASH_FUNC_python3%%": "() {{ /usr/bin/touch {!r}; }}".format(
                        str(function_marker)
                    ),
                }
            )
            for path in (signer, publisher):
                result = subprocess.run(
                    [str(path)],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
            self.assertFalse(bash_marker.exists())
            self.assertFalse(function_marker.exists())
            self.assertFalse(python_marker.exists())

    def test_prepare_entrypoint_rejects_arguments_before_work_under_hostile_bash_env(self):
        with tempfile.TemporaryDirectory(prefix="taiji-hostile-prepare-env-") as temp:
            root = Path(temp)
            marker = root / "bash-env-ran"
            bash_env = root / "hostile-bash-env.sh"
            bash_env.write_text(
                "/usr/bin/touch {!r}\n".format(str(marker)), encoding="utf-8"
            )
            environment = os.environ.copy()
            environment["BASH_ENV"] = str(bash_env)
            result = subprocess.run(
                [str(PREPARE), "--unexpected-argument"],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("不接受命令行参数", result.stderr)
            self.assertFalse(marker.exists())

    def test_release_check_uses_one_fixed_isolated_python_test_runner(self):
        self.assertTrue(RELEASE_TEST_RUNNER.is_file())
        source = RELEASE_TEST_RUNNER.read_text(encoding="utf-8")
        self.assertIn("sys.flags.isolated", source)
        self.assertIn("sys.dont_write_bytecode", source)
        self.assertIn("tests.test_linux_desktop_packaging_static", source)
        self.assertIn(
            "tools/taiji-desktop-acceptance/test_assemble_target_evidence.py",
            source,
        )
        release_check = (ROOT / "scripts/taiji-release-check.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("/usr/bin/env -i", release_check)
        self.assertIn("/usr/bin/mktemp -d /tmp/taiji-release-python.XXXXXX", release_check)
        self.assertIn('HOME="$isolated_root/home"', release_check)
        self.assertIn('TMPDIR="$isolated_root/tmp"', release_check)
        self.assertIn("PYTHONNOUSERSITE=1", release_check)
        self.assertIn('/usr/bin/python3 -I -B "$RELEASE_TEST_RUNNER"', release_check)
        self.assertNotIn("python3 -m unittest", release_check)
        orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
        self.assertIn('"scripts/run-taiji-release-python-tests.py",', orchestrator)
        gate = PYTHON38_GATE.read_text(encoding="utf-8")
        self.assertIn(
            'RELEASE_TEST_RUNNER = ROOT / "scripts/run-taiji-release-python-tests.py"',
            gate,
        )
        self.assertIn("RELEASE_TEST_RUNNER,", gate)

    def test_release_test_environment_blocks_hostile_user_site_in_nested_python(self):
        with tempfile.TemporaryDirectory(prefix="taiji-hostile-user-site-") as temp:
            root = Path(temp)
            hostile_home = root / "hostile-home"
            safe_home = root / "safe-home"
            safe_tmp = root / "safe-tmp"
            safe_home.mkdir(mode=0o700)
            safe_tmp.mkdir(mode=0o700)
            sentinel = root / "sitecustomize-ran"
            version = "{}.{}".format(sys.version_info[0], sys.version_info[1])
            user_sites = (
                hostile_home / ".local/lib" / ("python" + version) / "site-packages",
                hostile_home
                / "Library/Python"
                / version
                / "lib/python/site-packages",
            )
            for user_site in user_sites:
                user_site.mkdir(parents=True, exist_ok=True)
                (user_site / "sitecustomize.py").write_text(
                    "from pathlib import Path\n"
                    "Path({!r}).touch()\n".format(str(sentinel)),
                    encoding="utf-8",
                )
            probe = root / "nested-python-probe.py"
            probe.write_text(
                "import subprocess, sys\n"
                "raise SystemExit(subprocess.run([sys.executable, '-c', 'pass']).returncode)\n",
                encoding="utf-8",
            )
            hostile_environment = os.environ.copy()
            hostile_environment.update(
                {
                    "HOME": str(hostile_home),
                    "PYTHONPATH": str(user_sites[0]),
                    "PYTHONUSERBASE": str(hostile_home / "user-base"),
                }
            )
            result = subprocess.run(
                [
                    "/usr/bin/env",
                    "-i",
                    "HOME=" + str(safe_home),
                    "TMPDIR=" + str(safe_tmp),
                    "PATH=/usr/bin:/bin",
                    "LANG=C.UTF-8",
                    "LC_ALL=C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "PYTHONNOUSERSITE=1",
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(probe),
                ],
                cwd=ROOT,
                env=hostile_environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(sentinel.exists())

    def test_fixed_release_runner_fails_closed_on_every_skip(self):
        spec = importlib.util.spec_from_file_location(
            "taiji_release_runner_skip_contract",
            RELEASE_TEST_RUNNER,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)

        class Result:
            skipped = [(object(), "dependency unavailable")]

            @staticmethod
            def wasSuccessful():
                return True

        self.assertEqual(runner._result_exit_code(Result()), 1)

    def test_fixed_release_runner_rejects_zero_tests_for_each_declared_target(self):
        spec = importlib.util.spec_from_file_location(
            "taiji_release_runner_zero_collection_contract",
            RELEASE_TEST_RUNNER,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)

        one_test = lambda: unittest.TestSuite((unittest.FunctionTestCase(lambda: None),))

        class Loader:
            def __init__(self, empty_target):
                self.empty_target = empty_target

            def loadTestsFromName(self, name):
                if name == self.empty_target:
                    return unittest.TestSuite()
                return one_test()

            def loadTestsFromNames(self, names):
                suite = unittest.TestSuite()
                for name in names:
                    suite.addTests(self.loadTestsFromName(name))
                return suite

        original_loader = runner.unittest.defaultTestLoader
        original_path_loader = runner._load_path_tests
        try:
            for target in runner.FIXED_TEST_MODULES:
                runner.unittest.defaultTestLoader = Loader(target)
                runner._load_path_tests = lambda loader, path: one_test()
                with self.subTest(target=target):
                    with self.assertRaisesRegex(RuntimeError, re.escape(target)):
                        runner.build_suite()

            runner.unittest.defaultTestLoader = Loader(None)
            runner._load_path_tests = lambda loader, path: unittest.TestSuite()
            with self.assertRaisesRegex(
                RuntimeError,
                re.escape(str(runner.DESKTOP_EVIDENCE_TEST)),
            ):
                runner.build_suite()
        finally:
            runner.unittest.defaultTestLoader = original_loader
            runner._load_path_tests = original_path_loader

    def test_publisher_is_python38_safe_and_never_clones_the_ambient_environment(self):
        publisher = HARDENED_RELEASE_SHELLS[-1].read_text(encoding="utf-8")
        embedded = publisher.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        ast.parse(embedded, filename=str(HARDENED_RELEASE_SHELLS[-1]), feature_version=(3, 8))
        self.assertTrue(embedded.startswith("from __future__ import annotations\n"))
        self.assertNotIn("os.environ.copy()", embedded)
        self.assertNotIn("sys.executable", embedded)
        self.assertIn('["/usr/bin/python3", "-I", "-B"]', embedded)
        self.assertIn('["/bin/bash", "-p",', embedded)
        self.assertIn('"PATH": "/usr/bin:/bin"', embedded)
        self.assertIn("GITHUB_TOKEN", embedded)
        gate = PYTHON38_GATE.read_text(encoding="utf-8")
        self.assertIn(
            'SINGLE_DEB_PUBLISHER = ROOT / "packaging/linux/deb/publish-single-deb.sh"',
            gate,
        )
        self.assertIn("extract_single_deb_publisher_python", gate)

    def test_formal_builder_does_not_restore_user_controlled_path_entries(self):
        source = BUILDER.read_text(encoding="utf-8")
        self.assertNotIn("$HOME/.local/bin", source)
        self.assertNotIn("/usr/local/bin:$PATH", source)
        self.assertNotIn('export PATH="$NODE_ROOT/current/bin:$PATH"', source)
        self.assertIn(
            'export PATH="$NODE_ROOT/current/bin:/usr/bin:/bin"', source
        )

    def test_docs_define_v5_execve_replacement_and_human_session_boundary(self):
        for path in (RUNBOOK, OPERATOR_GUIDE):
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text(encoding="utf-8")
                self.assertIn("taiji-linux-golden-orchestrator-config/v5", source)
                self.assertIn("env_mode=replace", source)
                self.assertIn("env_passthrough", source)
                self.assertIn("env_sensitive", source)
                self.assertIn("execve", source)
                self.assertIn("human-session", source)
                self.assertIn("GITHUB_TOKEN", source)
                self.assertIn("SSH_AUTH_SOCK", source)
                self.assertIn("used-nonces", source)
                self.assertIn("DISPLAY", source)
                self.assertIn("DBUS_SESSION_BUS_ADDRESS", source)


if __name__ == "__main__":
    unittest.main()
