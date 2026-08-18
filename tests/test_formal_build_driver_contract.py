import argparse
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts/run-taiji-formal-build-tests.py"
AGENT_RUNNER = (
    ROOT
    / "hermes-local-lab/sources/hermes-agent/scripts/run_tests_parallel.py"
)
EVIDENCE_VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("taiji_formal_driver", DRIVER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load formal driver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_agent_runner():
    spec = importlib.util.spec_from_file_location(
        "taiji_formal_agent_runner_contract", AGENT_RUNNER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load formal Agent runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_evidence_validator():
    spec = importlib.util.spec_from_file_location(
        "taiji_formal_driver_real_evidence_validator", EVIDENCE_VALIDATOR
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load formal evidence validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def live_process_group_members(process_group, proc_root=Path("/proc")):
    if not proc_root.is_dir():
        return None
    live = []
    for stat_path in proc_root.glob("[0-9]*/stat"):
        try:
            payload = stat_path.read_text(encoding="ascii")
            closing = payload.rfind(")")
            fields = payload[closing + 2:].split()
            state = fields[0]
            member_group = int(fields[2])
            pid = int(stat_path.parent.name)
        except (OSError, ValueError, IndexError):
            continue
        if member_group == process_group and state not in {"Z", "X"}:
            live.append((pid, state))
    return live


class FormalBuildDriverContractTests(unittest.TestCase):
    def test_driver_exposes_exact_registry_and_contract(self):
        driver = load_driver()
        self.assertEqual(len(driver.FORMAL_TARGET_REGISTRY), 20)
        self.assertEqual(driver.FORMAL_TARGET_CONTRACT_BYTES, 1864)
        self.assertEqual(
            driver.FORMAL_TARGET_CONTRACT_SHA256,
            "5fdcd9335ac9c722b224c06b03d817bd505cff4abc514b09f8d9ba604c11953b",
        )
        self.assertEqual(
            len(driver.serialize_target_registry(driver.FORMAL_TARGET_REGISTRY)),
            1864,
        )
        self.assertEqual(
            driver.target_contract_sha256(driver.FORMAL_TARGET_REGISTRY),
            driver.FORMAL_TARGET_CONTRACT_SHA256,
        )

    def test_parser_requires_only_fixed_fd_cli_and_rejects_hash_inputs(self):
        driver = load_driver()
        parser = driver.build_parser()
        args = parser.parse_args(
            [
                "--source-root",
                "/src",
                "--source-commit",
                "a" * 40,
                "--work-root",
                "/work",
                "--python-fd",
                "11",
                "--node-fd",
                "12",
                "--npm-cli-fd",
                "13",
                "--eslint-fd",
                "14",
                "--log-fd",
                "15",
            ]
        )
        self.assertEqual(args.source_commit, "a" * 40)
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--source-root",
                    "/src",
                    "--source-commit",
                    "a" * 40,
                    "--work-root",
                    "/work",
                    "--python-fd",
                    "11",
                    "--node-fd",
                    "12",
                    "--npm-cli-fd",
                    "13",
                    "--eslint-fd",
                    "14",
                    "--log-fd",
                    "15",
                    "--python-sha256",
                    "0" * 64,
                ]
            )

    def test_result_records_are_canonical_and_zero_or_skip_fails_closed(self):
        driver = load_driver()
        good = {
            "ordinal": 0,
            "collected": 1,
            "deselected": 0,
            "executed": 1,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
        }
        self.assertEqual(driver.validate_target_record(good, 0), good)
        for key, value in (("collected", 0), ("skipped", 1), ("passed", 0)):
            bad = dict(good)
            bad[key] = value
            with self.assertRaises(ValueError):
                driver.validate_target_record(bad, 0)

    def test_unittest_expected_and_unexpected_successes_fail_closed(self):
        driver = load_driver()
        for field in ("expectedFailures", "unexpectedSuccesses"):
            result = types.SimpleNamespace(
                testsRun=1,
                failures=[],
                errors=[],
                skipped=[],
                expectedFailures=[],
                unexpectedSuccesses=[],
            )
            setattr(result, field, [("case", "detail")])
            namespace = {"result": result, "collected": 1, "ordinal": 0}
            exec(driver.UNITTEST_RESULT_RECORD_SOURCE, {}, namespace)
            record = namespace["record"]
            self.assertEqual(record["passed"], 0)
            self.assertEqual(record["failed"], 1)
            with self.assertRaises(ValueError):
                driver.validate_target_record(record, 0)

    def test_tool_versions_are_normalized_to_bare_pinned_values(self):
        driver = load_driver()
        self.assertEqual(
            driver._normalize_tool_version("python", "Python 3.11.15"),
            "3.11.15",
        )
        self.assertEqual(
            driver._normalize_tool_version("node", "v22.23.1"),
            "22.23.1",
        )
        self.assertEqual(
            driver._normalize_tool_version("npm", "10.9.2"),
            "10.9.2",
        )
        for kind, value in (
            ("python", "Python unknown"),
            ("node", "22.23.1 extra"),
            ("npm", "10.9"),
            ("npm", "0"),
        ):
            with self.assertRaises(ValueError):
                driver._normalize_tool_version(kind, value)

    def test_npm_logical_path_is_fixed_from_work_root_without_proc_readlink(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="taiji-fixed-npm-path-") as td:
            build_root = Path(td) / "build"
            work = build_root / "formal-build-tests-direct"
            node_root = build_root / ".build-tools/node/current"
            node = node_root / "bin/node"
            npm = node_root / "lib/node_modules/npm/bin/npm-cli.js"
            work.mkdir(parents=True)
            node.parent.mkdir(parents=True)
            npm.parent.mkdir(parents=True)
            node.write_bytes(b"held-node")
            node.chmod(0o700)
            npm.write_bytes(b"held-npm")
            node_fd = os.open(str(node), os.O_RDONLY)
            npm_fd = os.open(str(npm), os.O_RDONLY)
            try:
                self.assertEqual(
                    driver._npm_logical_path(work, node_fd, npm_fd),
                    str(npm.resolve()),
                )
                with self.assertRaises(ValueError):
                    driver._npm_logical_path(work, npm_fd, node_fd)
            finally:
                os.close(npm_fd)
                os.close(node_fd)
        source = __import__("inspect").getsource(driver._npm_logical_path)
        self.assertNotIn("os.readlink", source)

    def test_held_commonjs_command_reads_fd_and_compiles_at_logical_filename(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="taiji-held-commonjs-") as td:
            node_fd = os.open(str(Path(td) / "node"), os.O_RDWR | os.O_CREAT, 0o700)
            script_fd = os.open(
                str(Path(td) / "npm-cli.js"), os.O_RDWR | os.O_CREAT, 0o600
            )
            try:
                argv, pass_fds = driver._held_commonjs_command(
                    node_fd=node_fd,
                    script_fd=script_fd,
                    logical_path="/opt/node/lib/node_modules/npm/bin/npm-cli.js",
                    script_args=("--version",),
                )
            finally:
                os.close(script_fd)
                os.close(node_fd)
        self.assertEqual(argv[0], "/proc/self/fd/{}".format(node_fd))
        self.assertEqual(argv[-3:], [
            "/proc/self/fd/{}".format(script_fd),
            "/opt/node/lib/node_modules/npm/bin/npm-cli.js",
            "--version",
        ])
        self.assertEqual(pass_fds, tuple(sorted((node_fd, script_fd))))
        loader = argv[2]
        self.assertIn("readFileSync(heldPath", loader)
        self.assertIn("scriptModule._compile(source, canonicalPath)", loader)
        self.assertIn("Module._nodeModulePaths(path.dirname(canonicalPath))", loader)

    def test_eslint_adapter_executes_held_cli_with_json_result_channel(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="taiji-held-eslint-") as td:
            root = Path(td) / "source"
            work = Path(td) / "work"
            webui = root / "hermes-local-lab/sources/hermes-webui"
            eslint = webui / "node_modules/eslint/bin/eslint.js"
            eslint.parent.mkdir(parents=True)
            (webui / "static").mkdir()
            work.mkdir(parents=True)
            eslint.write_text("throw new Error('must use held bytes');\n", encoding="utf-8")
            (webui / "eslint.runtime-guard.config.mjs").write_text(
                "export default [];\n", encoding="utf-8"
            )
            javascript = webui / "static/app.js"
            javascript.write_text("const value = 1;\n", encoding="utf-8")
            node = Path(td) / "node"
            node.write_bytes(b"held-node")
            node_fd = os.open(str(node), os.O_RDONLY)
            eslint_fd = os.open(str(eslint), os.O_RDONLY)
            descriptors = {
                "python": node_fd,
                "node": node_fd,
                "npm": node_fd,
                "eslint": eslint_fd,
            }
            held_calls = []

            def fake_held(node_fd_arg, script_fd, logical_path, script_args):
                held_calls.append(
                    (node_fd_arg, script_fd, logical_path, tuple(script_args))
                )
                return ["held-eslint"], tuple(sorted((node_fd_arg, script_fd)))

            lint_result = json.dumps(
                [
                    {
                        "filePath": str(javascript),
                        "messages": [],
                        "errorCount": 0,
                        "fatalErrorCount": 0,
                        "warningCount": 0,
                    }
                ],
                separators=(",", ":"),
            ).encode("utf-8")

            class FakeProc:
                returncode = 0

                @staticmethod
                def poll():
                    return 0

            def fake_collect(
                proc,
                result_fd,
                deadline,
                stdout_limit,
                stderr_limit,
                result_limit,
            ):
                del proc, deadline, stdout_limit, stderr_limit, result_limit
                os.close(result_fd)
                return b"", b"", lint_result

            try:
                with (
                    mock.patch.object(
                        driver, "_held_commonjs_command", side_effect=fake_held
                    ),
                    mock.patch.object(
                        driver, "_collect_process", side_effect=fake_collect
                    ),
                    mock.patch.object(
                        driver.subprocess, "Popen", return_value=FakeProc()
                    ) as popen,
                ):
                    record, stdout, stderr = driver._run_target(
                        "eslint",
                        "hermes-local-lab/sources/hermes-webui/static/**/*.js",
                        root,
                        descriptors,
                        8,
                        work,
                        deadline=time.monotonic() + 30,
                        stdout_limit=driver.MAX_OUTPUT,
                        stderr_limit=driver.MAX_OUTPUT,
                    )
            finally:
                os.close(eslint_fd)
                os.close(node_fd)

        self.assertEqual((record["collected"], record["passed"]), (1, 1))
        self.assertEqual((stdout, stderr), (b"", b""))
        self.assertEqual(
            held_calls[0][:3], (node_fd, eslint_fd, str(eslint.resolve()))
        )
        eslint_args = held_calls[0][3]
        self.assertIn("--no-config-lookup", eslint_args)
        self.assertIn("--format", eslint_args)
        self.assertIn("json", eslint_args)
        self.assertIn("--output-file", eslint_args)
        popen_kwargs = popen.call_args.kwargs
        self.assertEqual(popen_kwargs["cwd"], str(webui))
        self.assertTrue(popen_kwargs["start_new_session"])
        self.assertEqual(
            set(popen_kwargs["pass_fds"]),
            {node_fd, eslint_fd, max(popen_kwargs["pass_fds"])},
        )
        self.assertNotIn("GIT_DIR", popen_kwargs["env"])

    def test_agent_and_webui_pytest_share_the_agent_runner_authority(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="taiji-formal-webui-runner-") as td:
            root = Path(td) / "source"
            build_root = Path(td) / "build"
            work = build_root / "formal-build-tests-direct"
            agent = root / "hermes-local-lab/sources/hermes-agent"
            webui = root / "hermes-local-lab/sources/hermes-webui"
            canonical_runner = agent / "scripts/run_tests_parallel.py"
            canonical_runner.parent.mkdir(parents=True)
            (webui / "tests").mkdir(parents=True)
            work.mkdir(parents=True)
            canonical_runner.write_text("raise SystemExit(99)\n", encoding="utf-8")
            (webui / "tests/test_one.py").write_text(
                "def test_one(): pass\n", encoding="utf-8"
            )
            python_path = build_root / "formal-agent-venv/bin/python"
            node_path = build_root / ".build-tools/node/current/bin/node"
            python_path.parent.mkdir(parents=True)
            node_path.parent.mkdir(parents=True)
            python_path.write_bytes(b"held-python")
            node_path.write_bytes(b"held-node")
            python_path.chmod(0o700)
            node_path.chmod(0o700)
            python_fd = os.open(str(python_path), os.O_RDONLY)
            node_fd = os.open(str(node_path), os.O_RDONLY)
            descriptors = {
                "python": python_fd,
                "node": node_fd,
                "npm": python_fd,
                "eslint": python_fd,
            }
            payload = (
                json.dumps(
                    {
                        "ordinal": 9,
                        "collected": 1,
                        "deselected": 0,
                        "executed": 1,
                        "passed": 1,
                        "failed": 0,
                        "errors": 0,
                        "skipped": 0,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("ascii")

            class FakeProc:
                returncode = 0

                @staticmethod
                def poll():
                    return 0

            def fake_collect(
                proc,
                result_fd,
                deadline,
                stdout_limit,
                stderr_limit,
                result_limit,
            ):
                del proc, deadline, stdout_limit, stderr_limit, result_limit
                os.close(result_fd)
                return b"", b"", payload

            try:
                with (
                    mock.patch.object(
                        driver, "_collect_process", side_effect=fake_collect
                    ),
                    mock.patch.object(
                        driver.subprocess, "Popen", return_value=FakeProc()
                    ) as popen,
                ):
                    record, _stdout, _stderr = driver._run_target(
                        "pytest",
                        "hermes-local-lab/sources/hermes-webui/tests/test_one.py",
                        root,
                        descriptors,
                        9,
                        work,
                        deadline=time.monotonic() + 30,
                    )
                    controlled_node_which = shutil.which(
                        "node", path=popen.call_args.kwargs["env"]["PATH"]
                    )
            finally:
                os.close(node_fd)
                os.close(python_fd)

        argv = popen.call_args.args[0]
        self.assertEqual(argv[3], str(canonical_runner))
        formal_root_index = argv.index("--formal-test-root")
        self.assertEqual(argv[formal_root_index + 1], str(webui))
        self.assertEqual(argv[-1], "tests/test_one.py")
        self.assertEqual(record["passed"], 1)
        popen_kwargs = popen.call_args.kwargs
        expected_path = str(node_path.resolve().parent) + ":/usr/bin:/bin"
        self.assertEqual(popen_kwargs["env"]["PATH"], expected_path)
        self.assertEqual(
            controlled_node_which,
            str(node_path.resolve()),
        )
        self.assertEqual(
            popen_kwargs["env"]["HERMES_WEBUI_AGENT_DIR"], str(agent.resolve())
        )
        self.assertEqual(
            popen_kwargs["env"]["HERMES_WEBUI_PYTHON"],
            str(python_path.resolve()),
        )
        self.assertEqual(set(popen_kwargs["pass_fds"]), {python_fd, max(popen_kwargs["pass_fds"])})

    def test_fixed_python_and_node_paths_fail_closed_on_held_identity_mismatch(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="taiji-formal-tool-env-") as td:
            build_root = Path(td) / "build"
            work = build_root / "formal-build-tests-direct"
            python_path = build_root / "formal-agent-venv/bin/python"
            node_path = build_root / ".build-tools/node/current/bin/node"
            replacement = Path(td) / "replacement"
            work.mkdir(parents=True)
            python_path.parent.mkdir(parents=True)
            node_path.parent.mkdir(parents=True)
            python_path.write_bytes(b"held-python")
            node_path.write_bytes(b"held-node")
            replacement.write_bytes(b"replacement")
            for executable in (python_path, node_path, replacement):
                executable.chmod(0o700)
            python_fd = os.open(str(python_path), os.O_RDONLY)
            node_fd = os.open(str(node_path), os.O_RDONLY)
            replacement_fd = os.open(str(replacement), os.O_RDONLY)
            try:
                self.assertEqual(
                    driver._python_logical_path(work, python_fd),
                    str(python_path.resolve()),
                )
                self.assertEqual(
                    driver._node_logical_path(work, node_fd),
                    str(node_path.resolve()),
                )
                with self.assertRaises(ValueError):
                    driver._python_logical_path(work, replacement_fd)
                with self.assertRaises(ValueError):
                    driver._node_logical_path(work, replacement_fd)
                node_path.chmod(0o600)
                with self.assertRaises(ValueError):
                    driver._node_logical_path(work, node_fd)
            finally:
                os.close(replacement_fd)
                os.close(node_fd)
                os.close(python_fd)

    def test_root_runtime_receives_only_fixed_nested_tool_paths(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="taiji-formal-root-env-") as td:
            root = Path(td) / "source"
            build_root = Path(td) / "build"
            work = build_root / "formal-build-tests-direct"
            target = root / "tests/test_taiji_license_issuer_gui.py"
            python_path = build_root / "formal-agent-venv/bin/python"
            node_path = build_root / ".build-tools/node/current/bin/node"
            target.parent.mkdir(parents=True)
            work.mkdir(parents=True)
            python_path.parent.mkdir(parents=True)
            node_path.parent.mkdir(parents=True)
            target.write_text("pass\n", encoding="utf-8")
            python_path.write_bytes(b"held-python")
            node_path.write_bytes(b"held-node")
            python_path.chmod(0o700)
            node_path.chmod(0o700)
            python_fd = os.open(str(python_path), os.O_RDONLY)
            node_fd = os.open(str(node_path), os.O_RDONLY)
            descriptors = {
                "python": python_fd,
                "node": node_fd,
                "npm": python_fd,
                "eslint": python_fd,
            }
            payload = (
                '{"ordinal":0,"collected":1,"deselected":0,"executed":1,'
                '"passed":1,"failed":0,"errors":0,"skipped":0}\n'
            ).encode("ascii")

            class FakeProc:
                returncode = 0

                @staticmethod
                def poll():
                    return 0

            def fake_collect(proc, result_fd, *args, **kwargs):
                del proc, args, kwargs
                os.close(result_fd)
                return b"", b"", payload

            try:
                with (
                    mock.patch.object(driver, "_collect_process", side_effect=fake_collect),
                    mock.patch.object(driver.subprocess, "Popen", return_value=FakeProc()) as popen,
                ):
                    driver._run_target(
                        "unittest",
                        "tests/test_taiji_license_issuer_gui.py",
                        root,
                        descriptors,
                        0,
                        work,
                    )
            finally:
                os.close(node_fd)
                os.close(python_fd)

        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["TAIJI_AGENT_PYTHON"], str(python_path.resolve()))
        self.assertEqual(environment["TAIJI_TEST_NODE"], str(node_path.resolve()))
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")

    def test_run_aggregates_suite_output_before_results_and_shares_deadline(self):
        driver = load_driver()
        registry = (
            ("root-runtime", "unittest", "tests/one.py"),
            ("root-runtime", "unittest", "tests/two.py"),
        )
        observed_kwargs = []

        def fake_target(*args, **kwargs):
            ordinal = args[4]
            observed_kwargs.append(kwargs)
            record = {
                "ordinal": ordinal,
                "collected": 1,
                "deselected": 0,
                "executed": 1,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            }
            return record, ("out{}".format(ordinal)).encode(), (
                "err{}".format(ordinal)
            ).encode()

        with tempfile.TemporaryDirectory(prefix="taiji-formal-producer-") as td:
            root = Path(td) / "source"
            work = Path(td) / "work"
            root.mkdir()
            work.mkdir()
            tool = Path(td) / "tool"
            tool.write_bytes(b"held-tool")
            descriptors = {
                name: os.open(str(tool), os.O_RDONLY)
                for name in ("python", "node", "npm", "eslint")
            }
            log_path = Path(td) / "formal.log"
            log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT, 0o600)
            args = argparse.Namespace(
                source_root=str(root),
                source_commit="a" * 40,
                work_root=str(work),
                python_fd=descriptors["python"],
                node_fd=descriptors["node"],
                npm_cli_fd=descriptors["npm"],
                eslint_fd=descriptors["eslint"],
                log_fd=log_fd,
            )
            try:
                with (
                    mock.patch.object(driver, "FORMAL_TARGET_REGISTRY", registry),
                    mock.patch.object(
                        driver,
                        "target_contract_sha256",
                        return_value=driver.FORMAL_TARGET_CONTRACT_SHA256,
                    ),
                    mock.patch.object(
                        driver,
                        "_npm_logical_path",
                        return_value="/opt/node/lib/node_modules/npm/bin/npm-cli.js",
                    ),
                    mock.patch.object(
                        driver,
                        "_tool_version",
                        side_effect=("3.11.15", "22.23.1", "10.9.2"),
                    ),
                    mock.patch.object(driver, "_run_target", side_effect=fake_target),
                ):
                    self.assertEqual(driver.run(args), 0)
            finally:
                os.close(log_fd)
                for descriptor in descriptors.values():
                    os.close(descriptor)

            lines = log_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("python_version=3.11.15", lines)
        self.assertIn("node_version=22.23.1", lines)
        begin = lines.index("suite_begin=root-runtime")
        stdout_line = lines[begin + 1]
        stderr_line = lines[begin + 2]
        self.assertTrue(stdout_line.startswith("child_output=root-runtime\tstdout\t"))
        self.assertTrue(stderr_line.startswith("child_output=root-runtime\tstderr\t"))
        self.assertEqual(
            base64.b64decode(stdout_line.rsplit("\t", 1)[1]), b"out0out1"
        )
        self.assertEqual(
            base64.b64decode(stderr_line.rsplit("\t", 1)[1]), b"err0err1"
        )
        self.assertTrue(lines[begin + 3].startswith("target_result=0\t"))
        self.assertTrue(lines[begin + 4].startswith("target_result=1\t"))
        self.assertEqual(len(observed_kwargs), 2)
        self.assertIs(observed_kwargs[0]["deadline"], observed_kwargs[1]["deadline"])
        self.assertEqual(observed_kwargs[0]["stdout_limit"], driver.MAX_OUTPUT)
        self.assertEqual(observed_kwargs[1]["stdout_limit"], driver.MAX_OUTPUT - 4)

    def test_full_registry_producer_log_is_accepted_by_real_validator(self):
        driver = load_driver()
        validator = load_evidence_validator()
        source_commit = "a" * 40

        def fake_target(runner, target, root, fd_map, ordinal, work, **kwargs):
            del runner, target, root, fd_map, work, kwargs
            record = {
                "ordinal": ordinal,
                "collected": 1,
                "deselected": 0,
                "executed": 1,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            }
            return (
                record,
                "stdout-{}\n".format(ordinal).encode("ascii"),
                "stderr-{}\n".format(ordinal).encode("ascii"),
            )

        with tempfile.TemporaryDirectory(prefix="taiji-formal-real-validator-") as td:
            root = Path(td) / "source"
            work = Path(td) / "work"
            tool = Path(td) / "tool"
            log_path = Path(td) / "formal-build-tests.log"
            root.mkdir()
            work.mkdir()
            tool.write_bytes(b"held-tool")
            descriptors = {
                name: os.open(str(tool), os.O_RDONLY)
                for name in ("python", "node", "npm", "eslint")
            }
            log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT, 0o600)
            args = argparse.Namespace(
                source_root=str(root),
                source_commit=source_commit,
                work_root=str(work),
                python_fd=descriptors["python"],
                node_fd=descriptors["node"],
                npm_cli_fd=descriptors["npm"],
                eslint_fd=descriptors["eslint"],
                log_fd=log_fd,
            )
            try:
                with (
                    mock.patch.object(
                        driver,
                        "_hash_fd",
                        side_effect=(
                            validator.PINNED_PYTHON_EXECUTABLE_SHA256,
                            validator.PINNED_NODE_EXECUTABLE_SHA256,
                            validator.PINNED_NPM_CLI_SHA256,
                            "6" * 64,
                        ),
                    ),
                    mock.patch.object(
                        driver,
                        "_npm_logical_path",
                        return_value="/fixed/npm-cli.js",
                    ),
                    mock.patch.object(
                        driver,
                        "_tool_version",
                        side_effect=(
                            validator.PINNED_PYTHON_VERSION,
                            validator.PINNED_NODE_VERSION,
                            validator.PINNED_NPM_VERSION,
                        ),
                    ),
                    mock.patch.object(driver, "_run_target", side_effect=fake_target),
                ):
                    self.assertEqual(driver.run(args), 0)
            finally:
                os.close(log_fd)
                for descriptor in descriptors.values():
                    os.close(descriptor)
            payload = log_path.read_bytes()

        lines = payload.decode("utf-8").splitlines()
        for channel in ("stdout", "stderr"):
            prefix = "child_output=agent\t{}\t".format(channel)
            encoded = [line[len(prefix):] for line in lines if line.startswith(prefix)]
            self.assertEqual(len(encoded), 1)
            self.assertEqual(
                base64.b64decode(encoded[0]),
                b"".join(
                    "{}-{}\n".format(channel, ordinal).encode("ascii")
                    for ordinal in range(3, 8)
                ),
            )
        digest = hashlib.sha256(payload).hexdigest()
        manifest = {
            key: "fixture" for key in validator.PACKAGE_MANIFEST_V3_EXACT_FIELDS
        }
        manifest.update(
            {
                "schema": validator.PACKAGE_MANIFEST_SCHEMA_V3,
                "package": "taiji-agent",
                "architecture": "amd64",
                "source_commit": source_commit,
                "python_version": validator.PINNED_PYTHON_VERSION,
                "python_executable_sha256": (
                    validator.PINNED_PYTHON_EXECUTABLE_SHA256
                ),
                "node_version": validator.PINNED_NODE_VERSION,
                "node_executable_sha256": validator.PINNED_NODE_EXECUTABLE_SHA256,
                "formal_build_tests_status": "pass",
                "formal_build_tests_log_basename": (
                    validator.FORMAL_BUILD_TEST_LOG_BASENAME
                ),
                "formal_build_tests_log_sha256": digest,
            }
        )
        marker = {
            "formal_build_tests_status": "pass",
            "formal_build_tests_log_basename": (
                validator.FORMAL_BUILD_TEST_LOG_BASENAME
            ),
            "formal_build_tests_log_sha256": digest,
        }
        self.assertEqual(
            validator.validate_formal_build_test_payloads(
                manifest, marker, payload
            ),
            digest,
        )

    def test_bounded_collector_reaps_process_and_closes_result_fd_on_overflow(self):
        driver = load_driver()
        result_read, result_write = os.pipe()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,sys; os.write(int(sys.argv[1]), b'{}\\n'); "
                "sys.stdout.buffer.write(b'01234567890')".format(
                    json.dumps(
                        {
                            "ordinal": 0,
                            "collected": 1,
                            "deselected": 0,
                            "executed": 1,
                            "passed": 1,
                            "failed": 0,
                            "errors": 0,
                            "skipped": 0,
                        },
                        separators=(",", ":"),
                    ).replace("'", "\\'")
                ),
                str(result_write),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(result_write,),
            start_new_session=True,
        )
        os.close(result_write)
        with self.assertRaisesRegex(RuntimeError, "output exceeded bound"):
            driver._collect_process(
                proc,
                result_read,
                deadline=time.monotonic() + 5,
                stdout_limit=10,
                stderr_limit=10,
                result_limit=driver.MAX_RESULT,
            )
        self.assertIsNotNone(proc.poll())
        with self.assertRaises(OSError):
            os.fstat(result_read)

    def test_bounded_collector_reaps_process_and_closes_result_fd_on_timeout(self):
        driver = load_driver()
        result_read, result_write = os.pipe()
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(result_write,),
            start_new_session=True,
        )
        os.close(result_write)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "suite deadline exceeded"):
            driver._collect_process(
                proc,
                result_read,
                deadline=time.monotonic() + 0.05,
                stdout_limit=driver.MAX_OUTPUT,
                stderr_limit=driver.MAX_OUTPUT,
                result_limit=driver.MAX_RESULT,
            )
        self.assertLess(time.monotonic() - started, 1)
        self.assertIsNotNone(proc.poll())
        with self.assertRaises((ProcessLookupError, PermissionError)):
            os.killpg(proc.pid, 0)
        with self.assertRaises(OSError):
            os.fstat(result_read)

    @unittest.skipUnless(
        hasattr(os, "waitid") and hasattr(os, "WNOWAIT"),
        "safe no-reap process-group cleanup requires waitid WNOWAIT",
    )
    def test_bounded_collector_cleans_background_group_after_successful_leader(self):
        driver = load_driver()
        result_read, result_write = os.pipe()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,subprocess,sys; fd=int(sys.argv[1]); "
                "subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(10)'], pass_fds=(fd,)); "
                "os.write(fd, b'{\\\"status\\\":\\\"pass\\\"}'); "
                "print('leader-done')",
                str(result_write),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(result_write,),
            start_new_session=True,
        )
        os.close(result_write)
        started = time.monotonic()
        stdout, stderr, payload = driver._collect_process(
            proc,
            result_read,
            deadline=time.monotonic() + 0.3,
            stdout_limit=driver.MAX_OUTPUT,
            stderr_limit=driver.MAX_OUTPUT,
            result_limit=driver.MAX_RESULT,
        )
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(stdout, b"leader-done\n")
        self.assertEqual(stderr, b"")
        self.assertEqual(payload, b'{"status":"pass"}')
        deadline = time.monotonic() + 1.0
        while True:
            live_members = live_process_group_members(proc.pid)
            if live_members is None:
                with self.assertRaises((ProcessLookupError, PermissionError)):
                    os.killpg(proc.pid, 0)
                break
            if not live_members:
                break
            if time.monotonic() >= deadline:
                self.fail(f"process group still has live members: {live_members}")
            time.sleep(0.01)

    def test_live_process_group_members_filters_zombies(self):
        with tempfile.TemporaryDirectory(prefix="proc-live-members-") as td:
            proc = Path(td)
            (proc / "333").mkdir(parents=True)
            (proc / "111").mkdir(parents=True)
            (proc / "222").mkdir(parents=True)
            (proc / "333/stat").write_text(
                "333 (python run) Z 0 4242 4242\n", encoding="ascii"
            )
            (proc / "111/stat").write_text(
                "111 (python run) S 0 4242 4242\n", encoding="ascii"
            )
            (proc / "222/stat").write_text(
                "222 (bash -c) R 0 9999 9999\n", encoding="ascii"
            )

            self.assertEqual(
                live_process_group_members(4242, proc_root=proc),
                [(111, "S")],
            )

    def test_formal_pytest_starts_in_scratch_then_switches_to_suite_root(self):
        runner = load_agent_runner()
        with tempfile.TemporaryDirectory(prefix="taiji-formal-pytest-cwd-") as td:
            outer = Path(td)
            root = outer / "source"
            scratch = outer / "scratch"
            (root / "tests").mkdir(parents=True)
            (root / "static").mkdir()
            scratch.mkdir()
            (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            (root / "static/sentinel.txt").write_text("ok\n", encoding="utf-8")
            (root / "tests/test_one.py").write_text(
                "def test_one(): pass\n", encoding="utf-8"
            )
            observed = {}

            class Item:
                nodeid = "tests/test_one.py::test_one"

            class Report:
                nodeid = Item.nodeid
                when = "call"
                skipped = False
                passed = True
                failed = False

            class FakePytest:
                @staticmethod
                def main(argv, plugins):
                    observed["initial_cwd"] = Path.cwd()
                    observed["argv"] = tuple(argv)
                    cache = Path.cwd() / ".pytest-cache"
                    cache.mkdir()
                    (cache / "gateway-marker").write_text("loaded\n", encoding="utf-8")
                    config = types.SimpleNamespace(
                        rootpath=root,
                        inipath=root / "pytest.ini",
                    )
                    cwd_plugin = plugins[1]
                    cwd_plugin.pytest_configure(config)
                    observed["configured_cwd"] = Path.cwd()
                    observed["relative_static"] = Path(
                        "static/sentinel.txt"
                    ).read_text(encoding="utf-8")
                    counter = plugins[0]
                    counter.pytest_itemcollected(Item())
                    counter.pytest_runtest_logstart(Item.nodeid, None)
                    counter.pytest_runtest_logreport(Report())
                    cwd_plugin.pytest_unconfigure(config)
                    observed["restored_cwd"] = Path.cwd()
                    return 0

            previous = Path.cwd()
            try:
                os.chdir(scratch)
                records = runner._run_formal_pytest_session(
                    root,
                    ("tests/test_one.py",),
                    first_ordinal=3,
                    pytest_module=FakePytest,
                )
            finally:
                os.chdir(previous)

            self.assertEqual(observed["initial_cwd"].resolve(), scratch.resolve())
            self.assertEqual(observed["configured_cwd"].resolve(), root.resolve())
            self.assertEqual(observed["restored_cwd"].resolve(), scratch.resolve())
            self.assertEqual(observed["relative_static"], "ok\n")
            self.assertTrue((scratch / ".pytest-cache/gateway-marker").is_file())
            self.assertFalse((root / ".pytest-cache").exists())
            self.assertEqual(
                observed["argv"][0], str(root.resolve() / "tests/test_one.py")
            )
            self.assertIn("--rootdir=" + str(root.resolve()), observed["argv"])
            self.assertIn("--confcutdir=" + str(root.resolve()), observed["argv"])
            self.assertIn(str(root.resolve() / "pytest.ini"), observed["argv"])
            self.assertEqual(records[0]["passed"], 1)

    def test_formal_pytest_requires_one_canonical_config_file(self):
        runner = load_agent_runner()
        with tempfile.TemporaryDirectory(prefix="taiji-formal-no-config-") as td:
            root = Path(td)
            with self.assertRaisesRegex(RuntimeError, "config"):
                runner._formal_pytest_config(root)

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "formal driver executes on Linux procfs")
    def test_unittest_adapter_executes_a_file_target_not_a_module_name(self):
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="taiji-formal-driver-") as temp_dir:
            root = Path(temp_dir) / "source"
            work = Path(temp_dir) / "work"
            root.mkdir()
            (work / "home").mkdir(parents=True)
            (work / "tmp").mkdir()
            (root / "driver_helper.py").write_text("VALUE = 2\n", encoding="utf-8")
            target = root / "sample_formal_target.py"
            target.write_text(
                "import unittest\n"
                "from driver_helper import VALUE\n"
                "class Sample(unittest.TestCase):\n"
                "    def test_one(self):\n"
                "        self.assertEqual(VALUE, 1 + 1)\n",
                encoding="utf-8",
            )
            descriptors = {
                name: os.open(os.sys.executable, os.O_RDONLY)
                for name in ("python", "node", "npm", "eslint")
            }
            try:
                record, _stdout, _stderr = driver._run_target(
                    "unittest",
                    "sample_formal_target.py",
                    root,
                    descriptors,
                    0,
                    work,
                )
            finally:
                for descriptor in descriptors.values():
                    os.close(descriptor)
            self.assertEqual(record["collected"], 1)
            self.assertEqual(record["executed"], 1)
            self.assertEqual(record["passed"], 1)

    def test_unittest_adapter_source_loads_explicit_file_targets(self):
        driver = load_driver()
        source = __import__("inspect").getsource(driver._run_target)
        self.assertIn("spec_from_file_location", source)
        self.assertIn("loadTestsFromModule", source)

    def test_log_state_machine_rejects_duplicate_or_early_overall(self):
        driver = load_driver()
        with self.assertRaises(ValueError):
            driver.validate_log_lines(["overall_status=pass"])
        with self.assertRaises(ValueError):
                driver.validate_log_lines(["overall_status=pass", "overall_status=pass"])

    def test_builder_calls_direct_driver_without_privileged_supervisor(self):
        builder = (ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh").read_text(encoding="utf-8")
        self.assertIn("run_formal_build_tests_direct", builder)
        main = builder[builder.rfind("main() {"):]
        self.assertIn("run_formal_build_tests_direct", main)
        self.assertNotIn("run_formal_build_tests()", main)
        self.assertNotIn("/usr/bin/sudo -n -- /usr/bin/python3 -I -B -c", main)

    def test_builder_removes_the_retired_privileged_formal_test_implementation(self):
        builder = (ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh").read_text(encoding="utf-8")
        for retired in (
            "formal_build_root_supervisor_python_source()",
            "formal_build_supervisor_bootstrap_python_source()",
            "formal_build_supervisor_log_relay_python_source()",
            "seal_formal_build_supervisor()",
            "run_formal_test_step()",
            "run_formal_build_tests()",
        ):
            self.assertNotIn(retired, builder)

    def test_formal_consumers_expose_fd_and_basename_contract(self):
        build_deb = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        stager = (ROOT / "packaging/linux/stage-electron-runtime.py").read_text(encoding="utf-8")
        builder = (ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh").read_text(encoding="utf-8")
        for token in (
            "TAIJI_SOURCE_ARCHIVE_FD",
            "TAIJI_SOURCE_ARCHIVE_BASENAME",
            "TAIJI_SOURCE_INVENTORY_FD",
            "TAIJI_SOURCE_INVENTORY_BASENAME",
            "TAIJI_ELECTRON_ARCHIVE_FD",
            "TAIJI_ELECTRON_ARCHIVE_BASENAME",
        ):
            self.assertIn(token, build_deb)
        self.assertIn('archive_group.add_argument("--archive-fd"', stager)
        self.assertIn('archive_group.add_argument("--archive")', stager)
        self.assertIn("verified_archive_fd_snapshot", stager)
        self.assertIn('adopt_sealed_snapshot "$SOURCE_INVENTORY" "$inventory_hash" inventory', builder)
        self.assertIn('adopt_sealed_snapshot "$ELECTRON_ARCHIVE" "$ELECTRON_ARCHIVE_SHA256" electron', builder)


if __name__ == "__main__":
    unittest.main()
