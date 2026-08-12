"""RED contract for moving runtime-dependent tests to the Kylin builder."""

from __future__ import annotations

import ast
import base64
import io
import hashlib
import importlib.util
import inspect
import json
import os
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePath


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
PREFLIGHT = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
RELEASE_CHECK = ROOT / "scripts/taiji-release-check.sh"
RELEASE_TEST_RUNNER = ROOT / "scripts/run-taiji-release-python-tests.py"
EVIDENCE_VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"
BUILD_DEB = ROOT / "packaging/linux/deb/build-deb.sh"
PYTHON38_GATE = ROOT / "tests/python38_linux_packaging_gate.py"
LINUX_PACKAGING_TESTS = ROOT / "tests/test_linux_desktop_packaging_static.py"
AGENT_PARALLEL_RUNNER = (
    ROOT / "hermes-local-lab/sources/hermes-agent/scripts/run_tests_parallel.py"
)
FORMAL_SUITES = (
    "root-runtime",
    "desktop-evidence-node",
    "kylin-install-simulation",
    "agent",
    "webui-runtime-lint",
    "webui-python",
)
SEALED_ARCHIVE_NAMES = ("source", "uv", "python", "node")
SEALED_FRAME_NAMES = ("supervisor", "source", "uv", "python", "node")
FORMAL_TARGET_CONTRACT_SHA256 = (
    "5fdcd9335ac9c722b224c06b03d817bd505cff4abc514b09f8d9ba604c11953b"
)
FORMAL_TARGET_REGISTRY = (
    ("root-runtime", "unittest", "tests/test_taiji_license_issuer_gui.py"),
    (
        "desktop-evidence-node",
        "node-test",
        "tools/taiji-desktop-acceptance/run-installed-electron-acceptance.test.js",
    ),
    (
        "kylin-install-simulation",
        "unittest",
        "tests/test_kylin_install_script_simulation.py",
    ),
    (
        "agent",
        "pytest",
        "hermes-local-lab/sources/hermes-agent/tests/tools/test_taiji_security_mode.py",
    ),
    (
        "agent",
        "pytest",
        "hermes-local-lab/sources/hermes-agent/tests/test_taiji_license.py",
    ),
    (
        "agent",
        "pytest",
        "hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_license.py",
    ),
    (
        "agent",
        "pytest",
        "hermes-local-lab/sources/hermes-agent/tests/gateway/test_session_api.py",
    ),
    (
        "agent",
        "pytest",
        "hermes-local-lab/sources/hermes-agent/tests/tools/test_image_generation_readiness.py",
    ),
    (
        "webui-runtime-lint",
        "eslint",
        "hermes-local-lab/sources/hermes-webui/static/**/*.js",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_brand_privacy.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_model_config_api.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_approval_queue.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_approval_sse.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_pr1350_sse_notify_correctness.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_issue1800_file_html_interactions.py",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_writeflow_frontend.py::"
        "test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell",
    ),
    (
        "webui-python",
        "pytest",
        "hermes-local-lab/sources/hermes-webui/tests/test_issue1116_composer_placeholder.py",
    ),
)


def embedded_shell_python_source(function_name):
    builder = BUILDER.read_text(encoding="utf-8")
    marker = function_name + "() {"
    if marker not in builder:
        raise AssertionError("builder is missing embedded Python: " + function_name)
    start = builder.index(marker)
    heredoc_start = builder.index("/usr/bin/cat <<'PY'\n", start)
    payload_start = heredoc_start + len("/usr/bin/cat <<'PY'\n")
    payload_end = builder.index("\nPY\n}", payload_start)
    return builder[payload_start:payload_end]


def formal_supervisor_source():
    return embedded_shell_python_source("formal_build_root_supervisor_python_source")


def load_formal_supervisor():
    source = formal_supervisor_source()
    namespace = {"__name__": "taiji_formal_supervisor_contract"}
    exec(compile(source, "formal-build-root-supervisor.py", "exec"), namespace)
    return source, namespace


def load_evidence_validator():
    spec = importlib.util.spec_from_file_location(
        "taiji_formal_build_evidence_validator",
        EVIDENCE_VALIDATOR,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load evidence validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def formal_log_binding_fixture(validator, payload, source_commit="a" * 40):
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        key: "fixture" for key in validator.PACKAGE_MANIFEST_V3_EXACT_FIELDS
    }
    manifest.update(
        {
            "schema": "taiji-package-manifest/v3",
            "package": "taiji-agent",
            "architecture": "amd64",
            "source_commit": source_commit,
            "python_version": validator.PINNED_PYTHON_VERSION,
            "python_executable_sha256": validator.PINNED_PYTHON_EXECUTABLE_SHA256,
            "node_version": validator.PINNED_NODE_VERSION,
            "node_executable_sha256": validator.PINNED_NODE_EXECUTABLE_SHA256,
            "formal_build_tests_status": "pass",
            "formal_build_tests_log_basename": "formal-build-tests.log",
            "formal_build_tests_log_sha256": digest,
        }
    )
    marker = {
        "formal_build_tests_status": "pass",
        "formal_build_tests_log_basename": "formal-build-tests.log",
        "formal_build_tests_log_sha256": digest,
    }
    return manifest, marker


def canonical_formal_v2_log(
    source_commit,
    python_version,
    python_sha256,
    node_version,
    node_sha256,
    npm_version,
    npm_cli_sha256,
):
    lines = [
        "schema=taiji-formal-build-tests/v2",
        "source_commit=" + source_commit,
        "supervisor_source_sha256=" + ("7" * 64),
        "python_version=" + python_version,
        "python_executable_sha256=" + python_sha256,
        "node_version=" + node_version,
        "node_executable_sha256=" + node_sha256,
        "npm_version=" + npm_version,
        "npm_cli_sha256=" + npm_cli_sha256,
        "eslint_cli_sha256=" + ("6" * 64),
        "closure_sha256=" + ("8" * 64),
        "closure_file_count=321",
        "closure_total_bytes=654321",
        "target_count=20",
        "target_contract_sha256=" + FORMAL_TARGET_CONTRACT_SHA256,
    ]
    for suite in FORMAL_SUITES:
        lines.append("suite_begin=" + suite)
        if suite == "root-runtime":
            lines.append(
                "child_output=root-runtime\tstdout\t"
                + base64.b64encode(b"fixture output\n").decode("ascii")
            )
        suite_records = [
            (ordinal, record)
            for ordinal, record in enumerate(FORMAL_TARGET_REGISTRY)
            if record[0] == suite
        ]
        for ordinal, (target_suite, runner, target) in suite_records:
            lines.append(
                "target_result={}\t{}\t{}\t{}\t1\t0\t1\t1\t0\t0\t0".format(
                    ordinal,
                    target_suite,
                    runner,
                    target,
                )
            )
        target_count = len(suite_records)
        lines.append(
            "suite_counts={}\t{}\t{}\t0\t{}\t{}\t0\t0\t0".format(
                suite,
                target_count,
                target_count,
                target_count,
                target_count,
            )
        )
        lines.append("suite_status=" + suite + ":pass")
    lines.append("overall_status=pass")
    return ("\n".join(lines) + "\n").encode("utf-8")


class FormalBuildTestEvidenceContractTests(unittest.TestCase):
    def test_sealed_supervisor_owns_exact_twenty_target_registry_and_commands(self):
        source, namespace = load_formal_supervisor()
        registry = tuple(namespace["FORMAL_TARGET_REGISTRY"])
        self.assertEqual(registry, FORMAL_TARGET_REGISTRY)
        serialized = namespace["serialize_formal_target_registry"](registry)
        self.assertEqual(len(serialized), 1864)
        self.assertEqual(
            hashlib.sha256(serialized).hexdigest(),
            FORMAL_TARGET_CONTRACT_SHA256,
        )
        self.assertEqual(
            namespace["validate_formal_target_registry"](registry),
            FORMAL_TARGET_CONTRACT_SHA256,
        )

        closure = "/var/tmp/taiji-formal-tests.fixture/closure"
        commands = {
            suite: argv
            for suite, _cwd, argv, _environment in namespace[
                "formal_suite_commands"
            ](closure, "/formal-home", "/formal-tmp")
        }
        source_root = closure + "/source/"
        agent_prefix = "hermes-local-lab/sources/hermes-agent/"
        webui_prefix = "hermes-local-lab/sources/hermes-webui/"
        for suite, _runner, target in registry:
            if suite in (
                "root-runtime",
                "desktop-evidence-node",
                "kylin-install-simulation",
            ):
                command_target = source_root + target
            elif suite == "agent":
                self.assertTrue(target.startswith(agent_prefix))
                command_target = target[len(agent_prefix) :]
            else:
                self.assertTrue(target.startswith(webui_prefix))
                command_target = target[len(webui_prefix) :]
            self.assertIn(command_target, commands[suite])
            self.assertEqual(source.count('"' + target + '"'), 1, target)

        validator = load_evidence_validator()
        self.assertEqual(validator.FORMAL_BUILD_TEST_TARGET_COUNT, 20)
        self.assertEqual(
            validator.FORMAL_BUILD_TEST_TARGET_CONTRACT_SHA256,
            FORMAL_TARGET_CONTRACT_SHA256,
        )
        validator_source = EVIDENCE_VALIDATOR.read_text(encoding="utf-8")
        for _suite, _runner, target in registry:
            self.assertNotIn(target, validator_source)

    def test_formal_build_test_v1_log_is_an_explicit_downgrade_failure(self):
        validator = load_evidence_validator()
        source_commit = "a" * 40
        lines = [
            "schema=taiji-formal-build-tests/v1",
            "source_commit=" + source_commit,
            "python_version=" + validator.PINNED_PYTHON_VERSION,
            "python_executable_sha256="
            + validator.PINNED_PYTHON_EXECUTABLE_SHA256,
            "node_version=" + validator.PINNED_NODE_VERSION,
            "node_executable_sha256=" + validator.PINNED_NODE_EXECUTABLE_SHA256,
            "npm_version=" + validator.PINNED_NPM_VERSION,
            "npm_cli_sha256=" + validator.PINNED_NPM_CLI_SHA256,
            "eslint_cli_sha256=" + "6" * 64,
        ]
        for suite in FORMAL_SUITES:
            lines.extend(("suite_begin=" + suite, "suite_status=" + suite + ":pass"))
        lines.append("overall_status=pass")
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        manifest, marker = formal_log_binding_fixture(
            validator, payload, source_commit
        )
        with self.assertRaisesRegex(validator.EvidenceError, "v2|downgrade|降级"):
            validator.validate_formal_build_test_payloads(manifest, marker, payload)

    def test_unittest_adapter_uses_real_result_callbacks_and_fails_closed(self):
        _source, namespace = load_formal_supervisor()
        collect = namespace["collect_unittest_suite_counts"]
        require_complete = namespace["require_complete_target_counts"]

        class Passing(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass

        passing = collect(unittest.defaultTestLoader.loadTestsFromTestCase(Passing))
        self.assertEqual(
            passing,
            {
                "collected": 2,
                "deselected": 0,
                "executed": 2,
                "passed": 2,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            },
        )
        self.assertEqual(require_complete(dict(passing)), passing)

        class Mixed(unittest.TestCase):
            def test_failure(self):
                self.fail("fixture")

            def test_error(self):
                raise RuntimeError("fixture")

            @unittest.skip("fixture")
            def test_skip(self):
                pass

            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail("fixture")

            @unittest.expectedFailure
            def test_unexpected_success(self):
                pass

        mixed = collect(unittest.defaultTestLoader.loadTestsFromTestCase(Mixed))
        self.assertEqual(mixed["collected"], 5)
        self.assertEqual(mixed["executed"], 5)
        self.assertEqual(mixed["failed"], 2)
        self.assertEqual(mixed["errors"], 1)
        self.assertEqual(mixed["skipped"], 2)
        with self.assertRaises(Exception):
            require_complete(mixed)
        with self.assertRaises(Exception):
            require_complete(
                {
                    "collected": 0,
                    "deselected": 0,
                    "executed": 0,
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                }
            )

    def test_formal_pytest_counter_owns_each_selector_and_rejects_nonexecution(self):
        spec = importlib.util.spec_from_file_location(
            "taiji_formal_pytest_counter_contract",
            AGENT_PARALLEL_RUNNER,
        )
        if spec is None or spec.loader is None:
            self.fail("cannot load Agent parallel test runner")
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        counter_class = runner._FormalPytestCounter

        class Item:
            def __init__(self, nodeid):
                self.nodeid = nodeid

        class Report:
            def __init__(
                self,
                nodeid,
                *,
                when="call",
                passed=False,
                failed=False,
                skipped=False,
                wasxfail=None,
            ):
                self.nodeid = nodeid
                self.when = when
                self.passed = passed
                self.failed = failed
                self.skipped = skipped
                if wasxfail is not None:
                    self.wasxfail = wasxfail

        selectors = ("tests/test_one.py", "tests/test_two.py::test_exact")
        counter = counter_class(selectors, first_ordinal=3)
        nodeids = ("tests/test_one.py::test_a", "tests/test_two.py::test_exact")
        for nodeid in nodeids:
            counter.pytest_itemcollected(Item(nodeid))
            counter.pytest_runtest_logstart(nodeid, ("fixture", 1, "fixture"))
            counter.pytest_runtest_logreport(Report(nodeid, passed=True))
        self.assertEqual(
            counter.validated_records(),
            (
                {
                    "ordinal": 3,
                    "collected": 1,
                    "deselected": 0,
                    "executed": 1,
                    "passed": 1,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                },
                {
                    "ordinal": 4,
                    "collected": 1,
                    "deselected": 0,
                    "executed": 1,
                    "passed": 1,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                },
            ),
        )

        empty = counter_class(("tests/test_empty.py",), first_ordinal=7)
        with self.assertRaises(Exception):
            empty.validated_records()

        deselected = counter_class(("tests/test_deselected.py",), first_ordinal=7)
        item = Item("tests/test_deselected.py::test_one")
        deselected.pytest_itemcollected(item)
        deselected.pytest_deselected([item])
        with self.assertRaises(Exception):
            deselected.validated_records()

        for label, report in (
            (
                "xfail",
                Report(
                    "tests/test_marked.py::test_one",
                    skipped=True,
                    wasxfail="fixture",
                ),
            ),
            (
                "xpass",
                Report(
                    "tests/test_marked.py::test_one",
                    passed=True,
                    wasxfail="fixture",
                ),
            ),
        ):
            with self.subTest(label=label):
                marked = counter_class(("tests/test_marked.py",), first_ordinal=7)
                item = Item("tests/test_marked.py::test_one")
                marked.pytest_itemcollected(item)
                marked.pytest_runtest_logstart(item.nodeid, ("fixture", 1, "fixture"))
                marked.pytest_runtest_logreport(report)
                with self.assertRaises(Exception):
                    marked.validated_records()

    def test_node_and_eslint_adapters_use_programmatic_result_apis(self):
        _source, namespace = load_formal_supervisor()
        node_adapter = namespace["formal_node_test_adapter_javascript_source"]()
        eslint_adapter = namespace["formal_eslint_adapter_javascript_source"]()

        self.assertIn('require("node:test")', node_adapter)
        self.assertIn("test:summary", node_adapter)
        self.assertIn("counts.tests", node_adapter)
        self.assertIn('isolation: "none"', node_adapter)
        for forbidden in ("--test", "TAP", "spawn(", "exec("):
            self.assertNotIn(forbidden, node_adapter)

        self.assertIn("ESLint", eslint_adapter)
        self.assertIn("lintFiles", eslint_adapter)
        self.assertIn("results.length", eslint_adapter)
        self.assertIn("fatalErrorCount", eslint_adapter)
        for forbidden in ("child_process", "spawn(", "exec("):
            self.assertNotIn(forbidden, eslint_adapter)

    def test_formal_pytest_mode_runs_one_hooked_session_for_exact_selectors(self):
        spec = importlib.util.spec_from_file_location(
            "taiji_formal_pytest_session_contract",
            AGENT_PARALLEL_RUNNER,
        )
        if spec is None or spec.loader is None:
            self.fail("cannot load Agent parallel test runner")
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)

        class Item:
            def __init__(self, nodeid):
                self.nodeid = nodeid

        class Report:
            when = "call"
            passed = True
            failed = False
            skipped = False

            def __init__(self, nodeid):
                self.nodeid = nodeid

        observed = {}

        class FakePytest:
            @staticmethod
            def main(argv, plugins):
                observed["argv"] = tuple(argv)
                observed["plugins"] = tuple(plugins)
                counter = plugins[0]
                for selector in ("tests/test_one.py", "tests/test_two.py::test_exact"):
                    nodeid = selector + ("::test_case" if "::" not in selector else "")
                    counter.pytest_itemcollected(Item(nodeid))
                    counter.pytest_runtest_logstart(nodeid, ("fixture", 1, "fixture"))
                    counter.pytest_runtest_logreport(Report(nodeid))
                return 0

        with tempfile.TemporaryDirectory(prefix="taiji-formal-pytest-session-") as td:
            root = Path(td)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_one.py").write_text("def test_case(): pass\n")
            (tests / "test_two.py").write_text("def test_exact(): pass\n")
            records = runner._run_formal_pytest_session(
                root,
                ("tests/test_one.py", "tests/test_two.py::test_exact"),
                first_ordinal=3,
                pytest_module=FakePytest,
            )
            with self.assertRaises(Exception):
                runner._run_formal_pytest_session(
                    root,
                    ("../escape.py",),
                    first_ordinal=3,
                    pytest_module=FakePytest,
                )

        self.assertEqual(tuple(record["ordinal"] for record in records), (3, 4))
        self.assertEqual(len(observed["plugins"]), 1)
        self.assertIn("-p", observed["argv"])
        self.assertIn("pytest_asyncio.plugin", observed["argv"])
        self.assertIn("pytest_timeout", observed["argv"])
        runner_source = AGENT_PARALLEL_RUNNER.read_text(encoding="utf-8")
        self.assertIn('os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"', runner_source)
        session_source = inspect.getsource(runner._run_formal_pytest_session)
        self.assertIn("pytest_module.main", session_source)
        self.assertNotIn("subprocess", session_source)

    def test_formal_suite_argv_uses_counting_adapters_and_global_ordinals(self):
        _source, namespace = load_formal_supervisor()
        closure = "/var/tmp/taiji-formal-tests.fixture/closure"
        commands = {
            suite: argv
            for suite, _cwd, argv, _environment in namespace[
                "formal_suite_commands"
            ](closure, "/formal-home", "/formal-tmp", result_fd=19)
        }

        for suite, ordinal in (
            ("root-runtime", 0),
            ("kylin-install-simulation", 2),
        ):
            argv = commands[suite]
            self.assertEqual(argv[1:4], ["-I", "-B", "-c"])
            self.assertIn("unittest.TestResult", argv[4])
            self.assertEqual(argv[5:7], ["19", str(ordinal)])

        node_argv = commands["desktop-evidence-node"]
        self.assertEqual(node_argv[1], "-e")
        self.assertIn('require("node:test")', node_argv[2])
        self.assertEqual(node_argv[3:5], ["19", "1"])
        self.assertNotIn("--test", node_argv)

        agent_argv = commands["agent"]
        webui_argv = commands["webui-python"]
        for argv, ordinal in ((agent_argv, 3), (webui_argv, 9)):
            self.assertIn("--formal-results-fd", argv)
            self.assertEqual(argv[argv.index("--formal-results-fd") + 1], "19")
            self.assertEqual(
                argv[argv.index("--formal-first-ordinal") + 1], str(ordinal)
            )
            self.assertIn("--formal-test-root", argv)

        eslint_argv = commands["webui-runtime-lint"]
        self.assertEqual(eslint_argv[1], "-e")
        self.assertIn("lintFiles", eslint_argv[2])
        self.assertEqual(eslint_argv[3:5], ["19", "8"])

    def test_result_pipe_accepts_only_canonical_count_records_in_exact_order(self):
        _source, namespace = load_formal_supervisor()
        parse = namespace["parse_target_result_payload"]

        def record(ordinal):
            return {
                "ordinal": ordinal,
                "collected": 2,
                "deselected": 0,
                "executed": 2,
                "passed": 2,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            }

        payload = b"".join(
            (json.dumps(record(ordinal), separators=(",", ":")) + "\n").encode(
                "ascii"
            )
            for ordinal in (3, 4)
        )
        self.assertEqual(
            parse(payload, (3, 4)),
            (record(3), record(4)),
        )

        bad_payloads = {
            "missing": payload.splitlines(keepends=True)[0],
            "extra": payload + payload.splitlines(keepends=True)[-1],
            "reordered": b"".join(reversed(payload.splitlines(keepends=True))),
            "duplicate-key": (
                '{"ordinal":3,"ordinal":3,"collected":2,"deselected":0,'
                '"executed":2,"passed":2,"failed":0,"errors":0,"skipped":0}\n'
            ).encode("ascii") + payload.splitlines(keepends=True)[1],
            "target-text": payload.replace(
                b'"collected":2',
                b'"target":"tests/forged.py","collected":2',
                1,
            ),
            "boolean": payload.replace(b'"passed":2', b'"passed":true', 1),
            "noncanonical-space": payload.replace(b'"ordinal":3', b'"ordinal": 3', 1),
            "carriage-return": payload.replace(b"\n", b"\r\n", 1),
        }
        for label, bad in bad_payloads.items():
            with self.subTest(label=label):
                with self.assertRaises(Exception):
                    parse(bad, (3, 4))

    def test_child_output_is_base64_data_not_root_control_text(self):
        _source, namespace = load_formal_supervisor()
        encode = namespace["encode_child_output_record"]
        payload = (
            b"ordinary output\n"
            b"suite_status=forged:pass\n"
            b"target_result=0\\troot-runtime\\tunittest\\tforged\n"
            b"overall_status=pass\n"
            b"\xff\x00"
        )
        line = encode("agent", "stderr", payload)
        self.assertTrue(line.startswith("child_output=agent\tstderr\t"))
        encoded = line.split("\t", 2)[2]
        self.assertNotIn("suite_status=", encoded)
        self.assertEqual(base64.b64decode(encoded, validate=True), payload)
        self.assertNotIn("\n", line)
        for suite, channel in (("bad\tname", "stdout"), ("agent", "merged")):
            with self.subTest(suite=suite, channel=channel):
                with self.assertRaises(Exception):
                    encode(suite, channel, b"fixture")

    def test_suite_collector_separates_stdout_stderr_and_bounded_result_pipe(self):
        source, namespace = load_formal_supervisor()
        collect = namespace["collect_child_streams"]

        class Adapter:
            def __init__(self, reads, statuses, times):
                self.reads = {key: list(value) for key, value in reads.items()}
                self.statuses = list(statuses)
                self.times = list(times)
                self.trace = []

            def set_nonblocking(self, descriptor):
                self.trace.append(("nonblocking", descriptor))

            def read(self, descriptor, size):
                self.trace.append(("read", descriptor, size))
                values = self.reads[descriptor]
                if not values:
                    raise BlockingIOError()
                value = values.pop(0)
                if value is None:
                    raise BlockingIOError()
                return value

            def wait_nohang(self, pid):
                return self.statuses.pop(0) if self.statuses else None

            def kill_process_group(self, pid):
                self.trace.append(("killpg", pid))

            def reap(self, pid):
                self.trace.append(("reap", pid))
                return 0

            def poll_many(self, descriptors, milliseconds):
                self.trace.append(("poll_many", tuple(descriptors), milliseconds))

            def close(self, descriptor):
                self.trace.append(("close", descriptor))

            def monotonic(self):
                return self.times.pop(0) if self.times else 99.0

        adapter = Adapter(
            {10: [b"stdout", b""], 11: [b"stderr", b""], 12: [b"result\n", b""]},
            [None, 0],
            [0.0, 0.1, 0.2],
        )
        status, streams = collect(
            adapter,
            77,
            {"stdout": 10, "stderr": 11, "result": 12},
            timeout=5.0,
            limits={"stdout": 16, "stderr": 16, "result": 16},
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            streams,
            {"stdout": b"stdout", "stderr": b"stderr", "result": b"result\n"},
        )
        self.assertIn(("killpg", 77), adapter.trace)
        for descriptor in (10, 11, 12):
            self.assertIn(("close", descriptor), adapter.trace)

        overflow = Adapter(
            {10: [b""], 11: [b""], 12: [b"012345", b""]},
            [None],
            [0.0, 0.1],
        )
        with self.assertRaises(Exception):
            collect(
                overflow,
                77,
                {"stdout": 10, "stderr": 11, "result": 12},
                timeout=5.0,
                limits={"stdout": 16, "stderr": 16, "result": 4},
            )
        self.assertIn(("killpg", 77), overflow.trace)
        self.assertIn(("reap", 77), overflow.trace)

        setup_failure = Adapter(
            {10: [], 11: [], 12: []},
            [],
            [0.0],
        )
        original_set_nonblocking = setup_failure.set_nonblocking

        def fail_second_descriptor(descriptor):
            original_set_nonblocking(descriptor)
            if descriptor == 11:
                raise OSError("fixture setup failure")

        setup_failure.set_nonblocking = fail_second_descriptor
        with self.assertRaises(Exception):
            collect(
                setup_failure,
                77,
                {"stdout": 10, "stderr": 11, "result": 12},
                timeout=5.0,
                limits={"stdout": 16, "stderr": 16, "result": 16},
            )
        self.assertIn(("killpg", 77), setup_failure.trace)
        self.assertIn(("reap", 77), setup_failure.trace)
        for descriptor in (10, 11, 12):
            self.assertIn(("close", descriptor), setup_failure.trace)

        run_start = source.index("def run_dropped_command(")
        run_end = source.index("\n\ndef ", run_start)
        run_source = source[run_start:run_end]
        self.assertIn("stdout_read_fd", run_source)
        self.assertIn("stderr_read_fd", run_source)
        self.assertIn("result_read_fd", run_source)
        self.assertIn("os.dup2(result_write_fd, result_fd)", run_source)

    def test_supervisor_consumes_result_pipe_and_encodes_both_child_streams(self):
        source, namespace = load_formal_supervisor()
        ordinals = namespace["formal_suite_target_ordinals"]
        self.assertEqual(ordinals("root-runtime"), (0,))
        self.assertEqual(ordinals("agent"), (3, 4, 5, 6, 7))
        self.assertEqual(ordinals("webui-python"), tuple(range(9, 20)))

        supervise = source[source.index("def supervise_formal_build(") :]
        self.assertIn(
            "formal_suite_commands(closure, home, tmp, FORMAL_TARGET_RESULT_FD)",
            supervise,
        )
        self.assertIn(
            "run_dropped_command(\n                argv, cwd, environment, uid, gid, FORMAL_TARGET_RESULT_FD\n            )",
            supervise,
        )
        self.assertIn("target_records = parse_target_result_payload(", supervise)
        self.assertIn(
            'streams["result"], formal_suite_target_ordinals(suite)',
            supervise,
        )
        self.assertIn(
            'encode_child_output_record(suite, channel, streams[channel])',
            supervise,
        )
        self.assertLess(
            supervise.index("if status != 0:"),
            supervise.index("parse_target_result_payload("),
        )
        self.assertNotIn("_emit_payload(output)", supervise)

    def test_root_v2_state_machine_formats_targets_and_suite_aggregate(self):
        source, namespace = load_formal_supervisor()
        format_suite = namespace["formal_suite_result_lines"]

        records = tuple(
            {
                "ordinal": ordinal,
                "collected": ordinal - 1,
                "deselected": 0,
                "executed": ordinal - 1,
                "passed": ordinal - 1,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            }
            for ordinal in range(3, 8)
        )
        lines = format_suite("agent", records)
        self.assertEqual(len(lines), 6)
        for offset, line in enumerate(lines[:-1]):
            ordinal = offset + 3
            suite, runner, target = FORMAL_TARGET_REGISTRY[ordinal]
            counts = [ordinal - 1, 0, ordinal - 1, ordinal - 1, 0, 0, 0]
            self.assertEqual(
                line,
                "target_result={}\t{}\t{}\t{}\t{}".format(
                    ordinal,
                    suite,
                    runner,
                    target,
                    "\t".join(str(value) for value in counts),
                ),
            )
        self.assertEqual(
            lines[-1],
            "suite_counts=agent\t5\t20\t0\t20\t20\t0\t0\t0",
        )

        forged = dict(records[0], ordinal=9)
        with self.assertRaises(Exception):
            format_suite("agent", (forged,) + records[1:])

        closure_header_start = source.index("def _closure_header(")
        closure_header_end = source.index("\n\ndef supervise_formal_build(")
        closure_header = source[closure_header_start:closure_header_end]
        for expected in (
            '"schema=taiji-formal-build-tests/v2"',
            '"supervisor_source_sha256="',
            '"closure_sha256="',
            '"closure_file_count="',
            '"closure_total_bytes="',
            '"target_count=20"',
            '"target_contract_sha256=" + FORMAL_TARGET_CONTRACT_SHA256',
        ):
            self.assertIn(expected, closure_header)

        supervise = source[source.index("def supervise_formal_build(") :]
        self.assertLess(
            supervise.index("for line in _closure_header("),
            supervise.index('print("suite_begin="'),
        )
        self.assertLess(
            supervise.index("formal_suite_result_lines("),
            supervise.index('print("suite_status=" + suite + ":pass"'),
        )
        self.assertLess(
            supervise.index('print("suite_status=" + suite + ":pass"'),
            supervise.index('print("overall_status=pass"'),
        )

    def test_formal_tests_use_one_fail_closed_root_supervisor(self):
        builder = BUILDER.read_text(encoding="utf-8")
        run_start = builder.index("run_formal_build_tests() {")
        run_end = builder.index("\n}\n\npending_build_marker_identity_matches", run_start)
        formal_run = builder[run_start:run_end]

        self.assertEqual(
            formal_run.count(
                "/usr/bin/sudo -n -- /usr/bin/python3 -I -B -c"
            ),
            1,
        )
        self.assertNotIn("run_formal_test_step ", formal_run)
        self.assertNotIn("shell=True", formal_run)
        self.assertIn("formal_build_root_supervisor_python_source", formal_run)
        self.assertIn("SOURCE_ARCHIVE_SNAPSHOT_PATH", formal_run)
        self.assertIn("UV_ARCHIVE_PATH", formal_run)
        self.assertIn("PYTHON_ARCHIVE_PATH", formal_run)
        self.assertIn("NODE_ARCHIVE_PATH", formal_run)

    def test_supervisor_is_python38_and_rebuilds_a_frozen_unregistered_uid_closure(self):
        source = formal_supervisor_source()
        ast.parse(source, filename="formal-build-root-supervisor.py", feature_version=(3, 8))

        for required in (
            '"taiji-formal-tests."',
            "os.setgroups([])",
            "os.setresgid(gid, gid, gid)",
            "os.setresuid(uid, uid, uid)",
            "PR_SET_NO_NEW_PRIVS",
            "os.fork()",
            "os.waitpid(",
            "safe_extract_archive",
            '"sync", "--extra", "all", "--extra", "dev", "--locked"',
            '"ci", "--ignore-scripts"',
            "getpwuid(uid_t uid)",
            "getpwuid_r(uid_t uid",
            "LD_PRELOAD",
            "pyvenv.cfg",
            ".pth",
            ".egg-link",
            "shebang",
            "RPATH",
            "RUNPATH",
            "freeze_closure",
            "closure_sha256=",
            "closure_file_count=",
            "closure_total_bytes=",
        ):
            self.assertIn(required, source)

        self.assertNotIn("shutil.copytree", source)
        self.assertNotIn("os.system(", source)
        self.assertNotIn("shell=True", source)
        self.assertLess(
            source.index("os.setgroups([])"), source.index("os.setresgid(gid, gid, gid)")
        )
        self.assertLess(
            source.index("os.setresgid(gid, gid, gid)"),
            source.index("os.setresuid(uid, uid, uid)"),
        )
        self.assertLess(
            source.index("os.setresuid(uid, uid, uid)"),
            source.index("PR_SET_NO_NEW_PRIVS"),
        )

    def test_supervisor_has_fixed_six_suite_snapshot_argv_and_no_root_repo_execution(self):
        source, namespace = load_formal_supervisor()

        self.assertEqual(tuple(namespace["FORMAL_SUITE_NAMES"]), FORMAL_SUITES)
        commands = namespace["formal_suite_commands"](
            "/var/tmp/taiji-formal-tests.fixture/closure",
            "/var/tmp/taiji-formal-tests.fixture/home",
            "/var/tmp/taiji-formal-tests.fixture/tmp",
        )
        self.assertEqual(tuple(item[0] for item in commands), FORMAL_SUITES)
        for suite, _cwd, argv, environment in commands:
            with self.subTest(suite=suite):
                self.assertTrue(argv)
                self.assertTrue(all(isinstance(value, str) and value for value in argv))
                self.assertTrue(
                    argv[0].startswith(
                        "/var/tmp/taiji-formal-tests.fixture/closure/"
                    )
                )
                self.assertEqual(
                    environment["LD_PRELOAD"],
                    "/var/tmp/taiji-formal-tests.fixture/closure/support/"
                    "libtaiji-formal-passwd.so",
                )
                self.assertTrue(
                    environment["PATH"].startswith(
                        "/var/tmp/taiji-formal-tests.fixture/closure/node/bin:"
                    )
                )

        tree = ast.parse(source)
        main_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        main_text = ast.get_source_segment(source, main_function) or ""
        self.assertNotIn("subprocess", main_text)
        self.assertNotIn("exec", main_text)

    def test_drop_privileges_has_exact_order_and_stops_at_each_failure(self):
        _source, namespace = load_formal_supervisor()
        drop = namespace["drop_privileges"]

        class Adapter:
            def __init__(self, fail_at=None):
                self.fail_at = fail_at
                self.trace = []

            def _call(self, name, *args):
                self.trace.append((name, args))
                if self.fail_at == name:
                    raise OSError("injected " + name)

            def setgroups(self, value):
                self._call("setgroups", value)

            def setresgid(self, real, effective, saved):
                self._call("setresgid", real, effective, saved)

            def setresuid(self, real, effective, saved):
                self._call("setresuid", real, effective, saved)

            def no_new_privs(self):
                self._call("no_new_privs")

        expected = [
            ("setgroups", ([],)),
            ("setresgid", (63001, 63001, 63001)),
            ("setresuid", (63000, 63000, 63000)),
            ("no_new_privs", ()),
        ]
        adapter = Adapter()
        drop(adapter, 63000, 63001)
        self.assertEqual(adapter.trace, expected)
        for failed_index, (failure, _args) in enumerate(expected):
            with self.subTest(failure=failure):
                adapter = Adapter(failure)
                with self.assertRaises(OSError):
                    drop(adapter, 63000, 63001)
                self.assertEqual(adapter.trace, expected[: failed_index + 1])

    def test_safe_extract_archive_accepts_files_and_rejects_unsafe_members(self):
        _source, namespace = load_formal_supervisor()
        extract = namespace["safe_extract_archive"]

        def make_archive(member, payload=b"payload"):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                archive.addfile(member, io.BytesIO(payload) if member.isfile() else None)
            output.seek(0)
            return output

        with tempfile.TemporaryDirectory(prefix="taiji-supervisor-extract-") as td:
            root = Path(td)
            good = tarfile.TarInfo("tree/file.txt")
            good.size = len(b"payload")
            extract(make_archive(good), str(root / "good"), "source")
            self.assertEqual((root / "good/tree/file.txt").read_bytes(), b"payload")

            fixtures = {}
            absolute = tarfile.TarInfo("/absolute")
            absolute.size = len(b"payload")
            fixtures["absolute"] = absolute
            dotdot = tarfile.TarInfo("tree/../escape")
            dotdot.size = len(b"payload")
            fixtures["dotdot"] = dotdot
            escaping_symlink = tarfile.TarInfo("tree/link")
            escaping_symlink.type = tarfile.SYMTYPE
            escaping_symlink.linkname = "../../escape"
            fixtures["escaping-symlink"] = escaping_symlink
            dangling_symlink = tarfile.TarInfo("tree/dangling")
            dangling_symlink.type = tarfile.SYMTYPE
            dangling_symlink.linkname = "missing"
            fixtures["dangling-symlink"] = dangling_symlink
            escaping_hardlink = tarfile.TarInfo("tree/hard")
            escaping_hardlink.type = tarfile.LNKTYPE
            escaping_hardlink.linkname = "../escape"
            fixtures["escaping-hardlink"] = escaping_hardlink
            fifo = tarfile.TarInfo("tree/fifo")
            fifo.type = tarfile.FIFOTYPE
            fixtures["fifo"] = fifo
            device = tarfile.TarInfo("tree/device")
            device.type = tarfile.CHRTYPE
            fixtures["device"] = device
            for label, member in fixtures.items():
                with self.subTest(member=label):
                    destination = root / label
                    with self.assertRaises(Exception):
                        extract(make_archive(member), str(destination), label)
                    self.assertFalse(destination.exists())

            duplicate = io.BytesIO()
            first = tarfile.TarInfo("tree/same")
            first.size = 1
            second = tarfile.TarInfo("tree/same")
            second.size = 1
            with tarfile.open(fileobj=duplicate, mode="w:gz") as archive:
                archive.addfile(first, io.BytesIO(b"a"))
                archive.addfile(second, io.BytesIO(b"b"))
            duplicate.seek(0)
            with self.assertRaises(Exception):
                extract(duplicate, str(root / "duplicate"), "duplicate")
            self.assertFalse((root / "duplicate").exists())

            linked = io.BytesIO()
            target = tarfile.TarInfo("tree/target")
            target.size = 4
            symlink = tarfile.TarInfo("tree/link")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = "target"
            hardlink = tarfile.TarInfo("tree/hard")
            hardlink.type = tarfile.LNKTYPE
            hardlink.linkname = "tree/target"
            with tarfile.open(fileobj=linked, mode="w:gz") as archive:
                archive.addfile(target, io.BytesIO(b"GOOD"))
                archive.addfile(symlink)
                archive.addfile(hardlink)
            linked.seek(0)
            extract(linked, str(root / "linked"), "linked")
            self.assertEqual((root / "linked/tree/link").read_bytes(), b"GOOD")
            self.assertEqual((root / "linked/tree/hard").read_bytes(), b"GOOD")
            self.assertNotEqual(
                (root / "linked/tree/target").stat().st_ino,
                (root / "linked/tree/hard").stat().st_ino,
            )

    def test_archive_adoption_requires_exact_four_sealed_inputs(self):
        source, namespace = load_formal_supervisor()
        adopt = namespace["adopt_archive_descriptors"]

        class Adapter:
            def __init__(self):
                self.adopted = []
                self.closed = []

            def adopt(self, name, descriptor, digest):
                self.adopted.append((name, descriptor, digest))
                return descriptor + 100

            def verify(self, descriptor, digest):
                return descriptor >= 100 and len(digest) == 64

            def close(self, descriptor):
                self.closed.append(descriptor)

        inputs = [
            (name, index + 10, format(index + 1, "064x"))
            for index, name in enumerate(SEALED_ARCHIVE_NAMES)
        ]
        adapter = Adapter()
        adopted = adopt(adapter, inputs)
        self.assertEqual(tuple(adopted), SEALED_ARCHIVE_NAMES)
        self.assertEqual(adapter.closed, [])

        for bad in (inputs[:-1], inputs + [inputs[-1]], list(reversed(inputs))):
            with self.subTest(bad=[item[0] for item in bad]):
                adapter = Adapter()
                with self.assertRaises(Exception):
                    adopt(adapter, bad)
                self.assertEqual(
                    adapter.closed,
                    [item[1] + 100 for item in adapter.adopted],
                )
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn("exec 13<", builder)
        self.assertIn("close_retained_formal_archive_snapshots", builder)

    def test_root_framed_reader_reseals_exact_five_frames_and_fails_closed(self):
        _source, namespace = load_formal_supervisor()
        read_frames = namespace["read_and_reseal_frames"]

        class Adapter:
            def __init__(self, fail_verify=None):
                self.payloads = {}
                self.sealed = set()
                self.closed = []
                self.fail_verify = fail_verify
                self.next_fd = 100

            def create(self, name):
                descriptor = self.next_fd
                self.next_fd += 1
                self.payloads[descriptor] = bytearray()
                return descriptor

            def write(self, descriptor, payload):
                self.payloads[descriptor].extend(payload)

            def seal(self, descriptor):
                self.sealed.add(descriptor)

            def verify(self, descriptor, size, digest):
                payload = bytes(self.payloads[descriptor])
                return (
                    descriptor in self.sealed
                    and len(payload) == size
                    and hashlib.sha256(payload).hexdigest() == digest
                    and self.fail_verify != descriptor
                )

            def close(self, descriptor):
                self.closed.append(descriptor)

        payloads = {
            name: (name + "-sealed-payload").encode("ascii")
            for name in SEALED_FRAME_NAMES
        }
        specs = [
            (name, len(payloads[name]), hashlib.sha256(payloads[name]).hexdigest())
            for name in SEALED_FRAME_NAMES
        ]
        adapter = Adapter()
        frames = read_frames(
            adapter,
            io.BytesIO(b"".join(payloads[name] for name in SEALED_FRAME_NAMES)),
            specs,
            max_frame_size=1024,
            max_total_size=4096,
        )
        self.assertEqual(tuple(frames), SEALED_FRAME_NAMES)
        self.assertEqual(len(adapter.sealed), 5)
        self.assertEqual(adapter.closed, [])

        bad_specs = (
            specs[:-1],
            list(reversed(specs)),
            [(name, size + (1 if name == "uv" else 0), digest) for name, size, digest in specs],
            [(name, size, ("0" * 64 if name == "python" else digest)) for name, size, digest in specs],
        )
        for bad in bad_specs:
            adapter = Adapter()
            with self.assertRaises(Exception):
                read_frames(
                    adapter,
                    io.BytesIO(b"".join(payloads[name] for name in SEALED_FRAME_NAMES)),
                    bad,
                    max_frame_size=1024,
                    max_total_size=4096,
                )
            self.assertEqual(sorted(adapter.closed), sorted(adapter.payloads))

        for stream, frame_limit, total_limit in (
            (io.BytesIO(b"".join(payloads[name] for name in SEALED_FRAME_NAMES)[:-1]), 1024, 4096),
            (io.BytesIO(b"".join(payloads[name] for name in SEALED_FRAME_NAMES) + b"X"), 1024, 4096),
            (io.BytesIO(b"".join(payloads[name] for name in SEALED_FRAME_NAMES)), 4, 4096),
            (io.BytesIO(b"".join(payloads[name] for name in SEALED_FRAME_NAMES)), 1024, 4),
        ):
            adapter = Adapter()
            with self.assertRaises(Exception):
                read_frames(adapter, stream, specs, frame_limit, total_limit)
            self.assertEqual(sorted(adapter.closed), sorted(adapter.payloads))

        bootstrap = BUILDER.read_text(encoding="utf-8").split(
            "formal_build_supervisor_bootstrap_python_source() {", 1
        )[1].split("\n}\n", 1)[0]
        self.assertNotIn("open(", bootstrap)
        self.assertNotIn("/proc/", bootstrap)

    def test_root_framed_reader_streams_each_frame_with_bounded_chunks(self):
        _source, namespace = load_formal_supervisor()
        read_frames = namespace["read_and_reseal_frames"]

        class Adapter:
            def __init__(self):
                self.payloads = {}
                self.max_write = 0
                self.next_fd = 200

            def create(self, name):
                descriptor = self.next_fd
                self.next_fd += 1
                self.payloads[descriptor] = bytearray()
                return descriptor

            def write(self, descriptor, payload):
                self.max_write = max(self.max_write, len(payload))
                self.payloads[descriptor].extend(payload)

            def seal(self, descriptor):
                pass

            def verify(self, descriptor, size, digest):
                payload = bytes(self.payloads[descriptor])
                return len(payload) == size and hashlib.sha256(payload).hexdigest() == digest

            def close(self, descriptor):
                pass

        payloads = {
            name: ((name + "-").encode("ascii") * 80)[:257]
            for name in SEALED_FRAME_NAMES
        }
        specs = [
            (name, len(payloads[name]), hashlib.sha256(payloads[name]).hexdigest())
            for name in SEALED_FRAME_NAMES
        ]
        adapter = Adapter()
        read_frames(
            adapter,
            io.BytesIO(b"".join(payloads[name] for name in SEALED_FRAME_NAMES)),
            specs,
            max_frame_size=1024,
            max_total_size=8192,
            chunk_size=17,
        )
        self.assertLessEqual(adapter.max_write, 17)

    def test_root_transcript_relay_attests_held_log_out_of_band(self):
        relay = embedded_shell_python_source(
            "formal_build_supervisor_log_relay_python_source"
        )
        payload = b"schema=taiji-formal-build-tests/v1\noverall_status=pass\n"
        digest = hashlib.sha256(payload).hexdigest()
        protocol = payload + ("root_transcript_sha256=" + digest + "\n").encode("ascii")
        with tempfile.TemporaryDirectory(prefix="taiji-transcript-relay-") as td:
            path = Path(td) / "formal.log"
            write_fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            read_fd = os.open(str(path), os.O_RDONLY)
            try:
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", relay, str(write_fd), str(read_fd)],
                    input=protocol,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    pass_fds=(write_fd, read_fd),
                    check=False,
                )
            finally:
                os.close(write_fd)
                os.close(read_fd)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            self.assertEqual(result.stdout, ("ATTESTED\t" + digest + "\n").encode("ascii"))
            self.assertEqual(path.read_bytes(), payload)

            bad = subprocess.run(
                [sys.executable, "-I", "-B", "-c", relay, "1", "0"],
                input=payload + b"root_transcript_sha256=" + (b"0" * 64) + b"\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(bad.returncode, 0)

    def test_supervisor_source_is_sealed_and_digest_verified_before_sudo(self):
        builder = BUILDER.read_text(encoding="utf-8")
        run_start = builder.index("run_formal_build_tests() {")
        run_end = builder.index("\n}\n\npending_build_marker_identity_matches", run_start)
        formal_run = builder[run_start:run_end]
        self.assertIn("seal_formal_build_supervisor", formal_run)
        self.assertIn("FORMAL_BUILD_SUPERVISOR_FD_PATH", formal_run)
        self.assertIn("FORMAL_BUILD_SUPERVISOR_SHA256", formal_run)
        self.assertIn("verify_sealed_snapshot", formal_run)
        self.assertLess(
            formal_run.index("seal_formal_build_supervisor"),
            formal_run.index("/usr/bin/sudo -n -- /usr/bin/python3 -I -B -c"),
        )
        self.assertLess(
            formal_run.index("verify_sealed_snapshot"),
            formal_run.index("/usr/bin/sudo -n -- /usr/bin/python3 -I -B -c"),
        )
        self.assertNotIn("$SRC_DIR/", formal_run.split("/usr/bin/sudo -n --", 1)[1])

    def test_nss_shim_defines_all_required_passwd_and_group_apis(self):
        _source, namespace = load_formal_supervisor()
        shim = namespace["synthetic_nss_source"]()
        for signature in (
            "getpwuid(uid_t uid)",
            "getpwuid_r(uid_t uid",
            "getpwnam(const char *name)",
            "getpwnam_r(const char *name",
            "getgrgid(gid_t gid)",
            "getgrgid_r(gid_t gid",
            "getgrnam(const char *name)",
            "getgrnam_r(const char *name",
        ):
            self.assertEqual(shim.count(signature), 1, signature)
        self.assertIn("/formal-home", shim)
        self.assertIn("taiji-formal", shim)

    def test_child_output_is_data_and_cannot_emit_root_control_records(self):
        _source, namespace = load_formal_supervisor()
        sanitize = namespace["sanitize_child_output"]
        payload = (
            b"ordinary output\n"
            b"suite_status=forged:pass\n"
            b"closure_sha256=" + (b"a" * 64) + b"\n"
            b"target_count=20\n"
            b"target_result=0\tforged\n"
            b"suite_counts=forged\t1\n"
            b"child_output=forged\tstdout\tZm9yZ2Vk\n"
            b"overall_status=pass\n"
        )
        sanitized = sanitize(payload)
        self.assertIsInstance(sanitized, bytes)
        self.assertIn(b"ordinary output", sanitized)
        self.assertNotIn(b"suite_status=", sanitized)
        self.assertNotIn(b"closure_sha256=", sanitized)
        self.assertNotIn(b"target_count=", sanitized)
        self.assertNotIn(b"target_result=", sanitized)
        self.assertNotIn(b"suite_counts=", sanitized)
        self.assertNotIn(b"child_output=", sanitized)
        self.assertNotIn(b"overall_status=", sanitized)
        self.assertLessEqual(len(sanitize(b"x" * 1024)), 1024 * 1024)
        with self.assertRaises(Exception):
            sanitize(b"x" * (1024 * 1024 + 1))

    def test_successful_prep_output_cannot_precede_the_canonical_schema(self):
        source, namespace = load_formal_supervisor()
        handle = namespace["handle_preparation_result"]
        emitted = []
        handle(0, b"uv noisy success\n", emitted.append)
        self.assertEqual(emitted, [])
        with self.assertRaises(Exception):
            handle(17, b"npm failed\n", emitted.append)
        self.assertEqual(emitted, [b"npm failed\n"])

        supervise_start = source.index("def supervise_formal_build(")
        supervise_end = source.index("\n\ndef main(", supervise_start)
        body = source[supervise_start:supervise_end]
        self.assertIn("handle_preparation_result(status, output", body)
        self.assertLess(
            body.index("for line in _closure_header("),
            body.index('print("suite_begin="'),
        )

    def test_child_collector_kills_and_reaps_on_exit_timeout_and_output_overflow(self):
        _source, namespace = load_formal_supervisor()
        collect = namespace["collect_child_process"]

        class Adapter:
            def __init__(self, reads, statuses, times):
                self.reads = list(reads)
                self.statuses = list(statuses)
                self.times = list(times)
                self.trace = []

            def set_nonblocking(self, descriptor):
                self.trace.append(("nonblocking", descriptor))

            def read(self, descriptor, size):
                self.trace.append(("read", descriptor, size))
                if not self.reads:
                    raise BlockingIOError()
                value = self.reads.pop(0)
                if value is None:
                    raise BlockingIOError()
                return value

            def wait_nohang(self, pid):
                self.trace.append(("wait_nohang", pid))
                return self.statuses.pop(0) if self.statuses else None

            def kill_process_group(self, pid):
                self.trace.append(("killpg", pid))

            def reap(self, pid):
                self.trace.append(("reap", pid))
                return 0

            def poll(self, descriptor, milliseconds):
                self.trace.append(("poll", descriptor, milliseconds))

            def close(self, descriptor):
                self.trace.append(("close", descriptor))

            def monotonic(self):
                return self.times.pop(0) if self.times else 99.0

        exited = Adapter([b"ok", None], [0], [0.0, 0.1])
        status, output = collect(exited, 77, 88, timeout=5.0, maximum=16)
        self.assertEqual((status, output), (0, b"ok"))
        self.assertIn(("killpg", 77), exited.trace)
        self.assertIn(("close", 88), exited.trace)

        timeout = Adapter([None], [None], [0.0, 6.0])
        with self.assertRaises(Exception):
            collect(timeout, 77, 88, timeout=5.0, maximum=16)
        self.assertIn(("killpg", 77), timeout.trace)
        self.assertIn(("reap", 77), timeout.trace)

        overflow = Adapter([b"0123456789"], [None], [0.0, 0.1])
        with self.assertRaises(Exception):
            collect(overflow, 77, 88, timeout=5.0, maximum=4)
        self.assertIn(("killpg", 77), overflow.trace)
        self.assertIn(("reap", 77), overflow.trace)

    def test_all_descriptor_writes_retry_short_writes_and_fail_closed(self):
        _source, namespace = load_formal_supervisor()
        write_all = namespace["write_all_fd"]

        class Adapter:
            def __init__(self, writes):
                self.writes = list(writes)
                self.payloads = []

            def write(self, descriptor, payload):
                self.payloads.append(bytes(payload))
                return self.writes.pop(0)

        adapter = Adapter([2, 1, 3])
        write_all(adapter, 9, b"abcdef")
        self.assertEqual(adapter.payloads, [b"abcdef", b"cdef", b"def"])
        with self.assertRaises(Exception):
            write_all(Adapter([0]), 9, b"x")

    def test_locked_dependency_commands_ignore_ambient_configuration(self):
        _source, namespace = load_formal_supervisor()
        commands = namespace["locked_dependency_commands"](
            "/closure/uv/uv",
            "/closure/python/bin/python3.11",
            "/closure/node/bin/node",
            "/closure/node/lib/node_modules/npm/bin/npm-cli.js",
            "/closure/source/agent",
            "/closure/source/webui",
            "/closure/venv",
            "/session/home",
            "/session/tmp",
            "/closure/support/libtaiji-formal-passwd.so",
            {"uv_index": "https://pypi.example/simple", "npm_registry": "https://npm.example"},
        )
        uv_argv, _uv_cwd, uv_env = commands["uv"]
        npm_argv, _npm_cwd, npm_env = commands["npm"]
        self.assertEqual(uv_argv[-7:], [
            "--no-config", "sync", "--extra", "all", "--extra", "dev", "--locked",
        ])
        self.assertEqual(uv_env["UV_NO_CONFIG"], "1")
        self.assertEqual(uv_env["UV_LINK_MODE"], "copy")
        self.assertEqual(uv_env["UV_PYTHON_DOWNLOADS"], "never")
        self.assertEqual(uv_env["UV_PYTHON"], "/closure/python/bin/python3.11")
        self.assertEqual(npm_argv[-5:], [
            "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--userconfig=/dev/null",
        ])
        self.assertEqual(npm_env["npm_config_userconfig"], "/dev/null")
        self.assertEqual(npm_env["npm_config_globalconfig"], "/dev/null")
        self.assertNotIn("PATH", {key for key in npm_env if key.startswith("NPM_CONFIG_")})

    def test_memfd_capabilities_are_required_without_numeric_fallbacks(self):
        source, namespace = load_formal_supervisor()
        require = namespace["require_memfd_seal_capabilities"]

        class CompleteOS:
            memfd_create = object()
            MFD_CLOEXEC = 1
            MFD_ALLOW_SEALING = 2

        class CompleteFcntl:
            F_ADD_SEALS = 1
            F_GET_SEALS = 2
            F_SEAL_WRITE = 4
            F_SEAL_GROW = 8
            F_SEAL_SHRINK = 16
            F_SEAL_SEAL = 32

        self.assertEqual(require(CompleteOS, CompleteFcntl), 60)
        for owner, name in ((CompleteOS, "MFD_ALLOW_SEALING"), (CompleteFcntl, "F_GET_SEALS")):
            original = getattr(owner, name)
            delattr(owner, name)
            try:
                with self.assertRaises(Exception):
                    require(CompleteOS, CompleteFcntl)
            finally:
                setattr(owner, name, original)
        self.assertNotIn('getattr(os, "MFD_', source)
        self.assertNotIn('getattr(fcntl, "F_SEAL_', source)

    def test_supervisor_config_requires_exact_typed_injection_safe_contract(self):
        _source, namespace = load_formal_supervisor()
        validate = namespace["validate_supervisor_config"]
        good = {
            "source_commit": "a" * 40,
            "python_version": "3.11.15",
            "node_version": "22.23.1",
            "uv_index": "https://pypi.example/simple",
            "npm_registry": "https://npm.example",
            "caller_uid": 501,
            "caller_gids": [20, 501],
            "forbidden_roots": ["/private/build", "/tmp/source"],
        }
        self.assertEqual(validate(dict(good)), good)
        bad_values = [
            dict(good, extra=True),
            dict(good, source_commit="not-a-commit\nsource_commit=" + "b" * 40),
            dict(good, python_version="3.11"),
            dict(good, node_version="v22.23.1"),
            dict(good, uv_index="http://pypi.example/simple"),
            dict(good, npm_registry="https://user:pass@npm.example"),
            dict(good, npm_registry="https://npm.example/path?x=1"),
            dict(good, caller_uid=True),
            dict(good, caller_gids=[20, "501"]),
            dict(good, forbidden_roots=["relative/path"]),
        ]
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    validate(value)

    def test_inventory_records_identity_and_rejects_all_extended_metadata(self):
        _source, namespace = load_formal_supervisor()
        validate = namespace["validate_node_metadata"]
        inventory = namespace["inventory_tree"]
        info = type("Info", (), {
            "st_uid": os.getuid(),
            "st_gid": os.getgid(),
            "st_nlink": 1,
            "st_mode": stat.S_IFREG | 0o644,
        })()
        validate(info, ())
        for names in (("user.note",), ("security.capability",), ("system.posix_acl_access",)):
            with self.subTest(names=names):
                with self.assertRaises(Exception):
                    validate(info, names)
        with tempfile.TemporaryDirectory(prefix="taiji-inventory-identity-") as td:
            path = Path(td) / "file"
            path.write_bytes(b"x")
            records = inventory(td)
            record = next(item for item in records if item[1] == "file")
            self.assertEqual(record[4:7], (os.getuid(), os.getgid(), 1))

    def test_freeze_uses_held_nofollow_fd_when_path_chmod_flags_are_unsupported(self):
        source, namespace = load_formal_supervisor()
        freeze_node = namespace["freeze_nonsymlink_node"]

        class Info:
            def __init__(self, mode, uid=501, gid=20):
                self.st_dev = 1
                self.st_ino = 2
                self.st_mode = mode
                self.st_uid = uid
                self.st_gid = gid
                self.st_nlink = 1

        class Adapter:
            def __init__(self):
                self.info = Info(stat.S_IFREG | 0o700)
                self.trace = []

            def open_nofollow(self, path, is_directory):
                self.trace.append(("open_nofollow", path, is_directory))
                return 44

            def fstat(self, descriptor):
                self.trace.append(("fstat", descriptor))
                return self.info

            def fchown(self, descriptor, uid, gid):
                self.trace.append(("fchown", descriptor, uid, gid))
                self.info.st_uid = uid
                self.info.st_gid = gid

            def fchmod(self, descriptor, mode):
                self.trace.append(("fchmod", descriptor, mode))
                self.info.st_mode = stat.S_IFREG | mode

            def close(self, descriptor):
                self.trace.append(("close", descriptor))

            def chmod(self, *args, **kwargs):
                raise NotImplementedError("follow_symlinks unsupported")

        adapter = Adapter()
        original = Info(stat.S_IFREG | 0o700)
        freeze_node(adapter, "/closure/tool", original, 0, 0, 0o555, True)
        self.assertEqual(
            adapter.trace,
            [
                ("open_nofollow", "/closure/tool", False),
                ("fstat", 44),
                ("fchown", 44, 0, 0),
                ("fchmod", 44, 0o555),
                ("fstat", 44),
                ("close", 44),
            ],
        )
        self.assertNotIn("os.chmod(path, mode, follow_symlinks=False)", source)

    def test_overall_pass_is_emitted_only_after_cleanup_and_final_inventory(self):
        source = formal_supervisor_source()
        supervise_start = source.index("def supervise_formal_build(")
        supervise_end = source.index("\n\ndef main(", supervise_start)
        body = source[supervise_start:supervise_end]
        self.assertLess(body.index("inventory_tree(closure)"), body.rindex("cleanup_workspace("))
        self.assertLess(
            body.rindex("cleanup_workspace("),
            body.rindex('print("overall_status=pass"'),
        )

    def test_only_home_and_tmp_remain_writable_before_suites(self):
        source, namespace = load_formal_supervisor()
        audit = namespace["audit_session_writable_intent"]
        with tempfile.TemporaryDirectory(prefix="taiji-writable-intent-") as td:
            session = Path(td) / "session"
            closure = session / "closure"
            home = session / "home"
            tmp = session / "tmp"
            session.mkdir(mode=0o700)
            (session / ".supervisor-token").write_text("fixture\n")
            (session / ".supervisor-token").chmod(0o400)
            closure.mkdir(mode=0o700)
            (closure / "data").write_text("x")
            (closure / "data").chmod(0o444)
            closure.chmod(0o555)
            home.mkdir(mode=0o700)
            tmp.mkdir(mode=0o700)
            session.chmod(0o555)
            self.assertEqual(
                audit(str(session), str(home), str(tmp), os.getuid()),
                {str(home), str(tmp)},
            )
            staging = session / "staging"
            session.chmod(0o700)
            staging.mkdir(mode=0o700)
            session.chmod(0o555)
            with self.assertRaises(Exception):
                audit(str(session), str(home), str(tmp), os.getuid())
        prep_end = source.index("def run_preparation_child(")
        supervise = source[source.index("def supervise_formal_build("):]
        self.assertIn("_remove_tree_path(staging)", supervise)
        self.assertLess(
            supervise.index("_remove_tree_path(staging)"),
            supervise.index("formal_suite_commands("),
        )

    def test_session_audit_allows_frozen_closure_links_but_rejects_other_links(self):
        _source, namespace = load_formal_supervisor()
        audit = namespace["audit_session_writable_intent"]
        with tempfile.TemporaryDirectory(prefix="taiji-session-links-") as td:
            session = Path(td) / "session"
            closure = session / "closure"
            home = session / "home"
            tmp = session / "tmp"
            session.mkdir(mode=0o700)
            token = session / ".supervisor-token"
            token.write_text("fixture\n")
            token.chmod(0o400)
            closure.mkdir(mode=0o700)
            target = closure / "target"
            target.write_text("safe\n")
            target.chmod(0o444)
            (closure / "internal-link").symlink_to("target")
            closure.chmod(0o555)
            home.mkdir(mode=0o700)
            tmp.mkdir(mode=0o700)
            session.chmod(0o555)
            self.assertEqual(
                audit(str(session), str(home), str(tmp), os.getuid()),
                {str(home), str(tmp)},
            )
            session.chmod(0o700)
            (session / "external-link").symlink_to("/tmp")
            session.chmod(0o555)
            with self.assertRaises(Exception):
                audit(str(session), str(home), str(tmp), os.getuid())

    def test_every_command_child_establishes_verified_process_group(self):
        source, namespace = load_formal_supervisor()
        establish = namespace["establish_process_group"]

        class Adapter:
            def __init__(self, error=None, group=77):
                self.error = error
                self.group = group
                self.trace = []

            def setpgid(self, pid, group):
                self.trace.append(("setpgid", pid, group))
                if self.error is not None:
                    raise OSError(self.error, "injected")

            def getpgid(self, pid):
                self.trace.append(("getpgid", pid))
                return self.group

            def getpid(self):
                return 77

        child = Adapter(group=77)
        establish(child, 0, child_side=True)
        self.assertEqual(child.trace, [("setpgid", 0, 0), ("getpgid", 0)])
        parent = Adapter(group=77)
        establish(parent, 77, child_side=False)
        self.assertEqual(parent.trace, [("setpgid", 77, 77), ("getpgid", 77)])
        recovered = Adapter(error=getattr(__import__("errno"), "EACCES"), group=77)
        establish(recovered, 77, child_side=False)
        self.assertEqual(recovered.trace[-1], ("getpgid", 77))
        with self.assertRaises(Exception):
            establish(Adapter(error=getattr(__import__("errno"), "EACCES"), group=12), 77, False)

        for function_name in (
            "run_current_identity_command",
            "run_dropped_command",
            "run_preparation_child",
        ):
            start = source.index("def " + function_name + "(")
            end = source.index("\n\ndef ", start + 5)
            function_source = source[start:end]
            self.assertGreaterEqual(function_source.count("establish_process_group("), 2)

    def test_all_supervisor_children_arm_parent_death_before_drop_or_exec(self):
        source, namespace = load_formal_supervisor()
        arm = namespace["arm_parent_death_signal"]

        class Adapter:
            def __init__(self, parent=4242, fail=False):
                self.parent = parent
                self.fail = fail
                self.trace = []

            def set_parent_death_signal(self, value):
                self.trace.append(("set_parent_death_signal", value))
                if self.fail:
                    raise OSError("injected prctl failure")

            def getppid(self):
                self.trace.append(("getppid",))
                return self.parent

        adapter = Adapter()
        arm(adapter, 4242)
        self.assertEqual(
            adapter.trace,
            [("set_parent_death_signal", signal.SIGKILL), ("getppid",)],
        )
        with self.assertRaises(Exception):
            arm(Adapter(parent=1), 4242)
        failed = Adapter(fail=True)
        with self.assertRaises(Exception):
            arm(failed, 4242)
        self.assertEqual(failed.trace, [("set_parent_death_signal", signal.SIGKILL)])

        for function_name in (
            "run_current_identity_command",
            "run_dropped_command",
            "run_preparation_child",
        ):
            start = source.index("def " + function_name + "(")
            end = source.index("\n\ndef ", start + 5)
            function_source = source[start:end]
            self.assertIn("parent_pid = os.getpid()", function_source)
            self.assertIn("arm_parent_death_signal(", function_source)
            self.assertLess(
                function_source.index("arm_parent_death_signal("),
                min(
                    position
                    for marker in ("drop_privileges(", "os.execve(")
                    for position in [function_source.find(marker)]
                    if position >= 0
                ),
            )
            expected_arm_count = 1 if function_name == "run_current_identity_command" else 2
            self.assertEqual(
                function_source.count("arm_parent_death_signal("),
                expected_arm_count,
            )
            if expected_arm_count == 2:
                self.assertLess(
                    function_source.index("drop_privileges("),
                    function_source.rindex("arm_parent_death_signal("),
                )
                later_markers = (
                    "prepare_closure(" if function_name == "run_preparation_child" else "os.chdir("
                )
                self.assertLess(
                    function_source.rindex("arm_parent_death_signal("),
                    function_source.index(later_markers),
                )

    def test_root_bootstrap_has_total_deadline_output_bound_and_group_reaping(self):
        bootstrap = embedded_shell_python_source(
            "formal_build_supervisor_bootstrap_python_source"
        )
        for required in (
            "TRANSCRIPT_MAX_BYTES",
            "TRANSCRIPT_DEADLINE_SECONDS",
            "time.monotonic()",
            "select.poll()",
            "os.WNOHANG",
            "os.setpgid(",
            "os.killpg(",
            "bootstrap_parent_pid = os.getpid()",
            "libc.prctl(1, signal.SIGKILL",
            "os.getppid() != bootstrap_parent_pid",
        ):
            self.assertIn(required, bootstrap)
        self.assertLess(
            bootstrap.index("TRANSCRIPT_MAX_BYTES"),
            bootstrap.index("transcript_pid = os.fork()"),
        )

    def test_pipeline_legs_retain_only_the_descriptors_each_role_needs(self):
        builder = BUILDER.read_text(encoding="utf-8")
        start = builder.index("run_formal_build_tests() {")
        end = builder.index("\n}\n\npending_build_marker_identity_matches", start)
        formal_run = builder[start:end]
        sudo_position = formal_run.index(
            "/usr/bin/sudo -n -- /usr/bin/python3 -I -B -c"
        )
        sudo_leg = formal_run[formal_run.rfind("(\n", 0, sudo_position):sudo_position]
        self.assertIn("exec {FORMAL_BUILD_TEST_LOG_FD}>&-", sudo_leg)
        self.assertIn("exec {FORMAL_BUILD_TEST_LOG_READ_FD}<&-", sudo_leg)
        relay_leg = formal_run[sudo_position:]
        self.assertIn(
            '"$FORMAL_BUILD_TEST_LOG_FD" "$FORMAL_BUILD_TEST_LOG_READ_FD"',
            relay_leg,
        )
        for descriptor in (6, 10, 11, 12, 13):
            close = "exec %d<&-" % descriptor
            self.assertIn(close, sudo_leg)
            self.assertIn(close, relay_leg)
        self.assertNotIn("sudo -C", formal_run)

    def test_inventory_detects_mutation_leaks_and_freeze_normalizes_modes(self):
        _source, namespace = load_formal_supervisor()
        inventory = namespace["inventory_tree"]
        freeze = namespace["freeze_closure"]
        validate_paths = namespace["validate_relocation_paths"]

        with tempfile.TemporaryDirectory(prefix="taiji-supervisor-inventory-") as td:
            root = Path(td) / "closure"
            (root / "bin").mkdir(parents=True)
            executable = root / "bin/tool"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            data = root / "data.txt"
            data.write_text("GOOD\n", encoding="utf-8")
            before = inventory(str(root))
            data.write_text("EVIL\n", encoding="utf-8")
            self.assertNotEqual(before, inventory(str(root)))
            data.write_text("/old/build/root/leak\n", encoding="utf-8")
            with self.assertRaises(Exception):
                validate_paths(str(root), ("/old/build/root",))
            data.write_text("GOOD\n", encoding="utf-8")
            freeze(str(root), os.getuid(), os.getgid(), chown=False)
            self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE(data.stat().st_mode), 0o444)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o555)

    def test_safe_extract_archive_enforces_preflight_resource_limits(self):
        _source, namespace = load_formal_supervisor()
        extract = namespace["safe_extract_archive"]

        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
            for name, payload in (("tree/a", b"1234"), ("tree/b", b"5678")):
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        archive_bytes.seek(0)
        limits = {
            "max_members": 1,
            "max_file_size": 4,
            "max_total_size": 8,
            "max_path_length": 64,
            "max_path_depth": 4,
        }
        with tempfile.TemporaryDirectory(prefix="taiji-extract-limits-") as td:
            destination = Path(td) / "limited"
            with self.assertRaises(Exception):
                extract(archive_bytes, str(destination), "source", limits=limits)
            self.assertFalse(destination.exists())

    def test_safe_extract_archive_bounds_tar_headers_before_creating_destination(self):
        source, namespace = load_formal_supervisor()
        collect = namespace["collect_bounded_tar_members"]
        extract = namespace["safe_extract_archive"]
        limits = {
            "max_members": 3,
            "max_file_size": 4,
            "max_total_size": 12,
            "max_path_length": 64,
            "max_path_depth": 4,
        }

        class CountingMembers:
            def __init__(self):
                self.calls = 0

            def __iter__(self):
                return self

            def __next__(self):
                self.calls += 1
                member = tarfile.TarInfo("tree/file-%d" % self.calls)
                member.size = 0
                return member

        members = CountingMembers()
        with self.assertRaises(Exception):
            collect(members, limits)
        self.assertEqual(members.calls, limits["max_members"] + 1)
        extract_start = source.index("def safe_extract_archive(")
        extract_end = source.index("\ndef ", extract_start + 1)
        self.assertNotIn(".getmembers()", source[extract_start:extract_end])

        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w:") as archive:
            for index in range(4):
                member = tarfile.TarInfo("tree/file-%d" % index)
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
        archive_bytes.seek(0)
        with tempfile.TemporaryDirectory(prefix="taiji-bounded-headers-") as td:
            destination = Path(td) / "limited"
            with self.assertRaises(Exception):
                extract(archive_bytes, str(destination), "source", limits=limits)
            self.assertFalse(destination.exists())

            sparse = io.BytesIO()
            member = tarfile.TarInfo("tree/sparse")
            member.size = 1
            member.pax_headers = {"GNU.sparse.map": "0,1"}
            with tarfile.open(fileobj=sparse, mode="w:") as archive:
                archive.addfile(member, io.BytesIO(b"x"))
            sparse.seek(0)
            with self.assertRaises(Exception):
                extract(sparse, str(destination), "source", limits={
                    "max_members": 4,
                    "max_file_size": 4,
                    "max_total_size": 4,
                    "max_path_length": 64,
                    "max_path_depth": 4,
                })
            self.assertFalse(destination.exists())

    def test_cleanup_requires_token_dirfd_and_devino_identity(self):
        _source, namespace = load_formal_supervisor()
        cleanup = namespace["cleanup_workspace"]
        inspect.signature(cleanup).bind(
            10,
            11,
            "taiji-formal-tests.fixture",
            "token-a",
            (1, 2),
            (1, 3),
            expected_uid=os.getuid(),
        )
        with tempfile.TemporaryDirectory(prefix="taiji-supervisor-cleanup-parent-") as td:
            parent = Path(td)
            basename = "taiji-formal-tests.fixture"
            workspace = parent / basename
            workspace.mkdir()
            (workspace / ".supervisor-token").write_text("token-a\n", encoding="ascii")
            parent_identity = parent.stat()
            identity = workspace.stat()
            parent_descriptor = os.open(str(parent), os.O_RDONLY)
            descriptor = os.open(basename, os.O_RDONLY, dir_fd=parent_descriptor)
            try:
                cleanup(
                    parent_descriptor,
                    descriptor,
                    basename,
                    "token-a",
                    (parent_identity.st_dev, parent_identity.st_ino),
                    (identity.st_dev, identity.st_ino),
                    expected_uid=os.getuid(),
                )
            finally:
                os.close(descriptor)
                os.close(parent_descriptor)
            self.assertFalse(workspace.exists())

            for label in ("token", "owner", "devino", "pathname-swap"):
                workspace.mkdir()
                (workspace / ".supervisor-token").write_text("token-a\n")
                parent_identity = parent.stat()
                identity = workspace.stat()
                parent_descriptor = os.open(str(parent), os.O_RDONLY)
                descriptor = os.open(basename, os.O_RDONLY, dir_fd=parent_descriptor)
                expected_devino = (identity.st_dev, identity.st_ino)
                expected_uid = os.getuid()
                if label == "devino":
                    expected_devino = (identity.st_dev, identity.st_ino + 1)
                if label == "owner":
                    expected_uid += 1
                foreign = None
                held = None
                if label == "token":
                    (workspace / ".supervisor-token").write_text("wrong\n")
                if label == "pathname-swap":
                    held = parent / (basename + ".held")
                    workspace.rename(held)
                    workspace.mkdir()
                    foreign = workspace / "foreign"
                    foreign.write_text("KEEP\n")
                try:
                    with self.assertRaises(Exception):
                        cleanup(
                            parent_descriptor,
                            descriptor,
                            basename,
                            "token-a",
                            (parent_identity.st_dev, parent_identity.st_ino),
                            expected_devino,
                            expected_uid=expected_uid,
                        )
                finally:
                    os.close(descriptor)
                    os.close(parent_descriptor)
                self.assertTrue(workspace.is_dir())
                if foreign is not None:
                    self.assertEqual(foreign.read_text(), "KEEP\n")
                for child in workspace.iterdir():
                    child.unlink()
                workspace.rmdir()
                if held is not None:
                    for child in held.iterdir():
                        child.unlink()
                    held.rmdir()

    def test_workspace_parent_chain_and_session_identity_fail_closed(self):
        _source, namespace = load_formal_supervisor()
        validate = namespace["validate_workspace_parent_chain"]
        make_session = namespace["create_workspace_session"]

        class Node:
            def __init__(self, dev, ino, mode, uid=0):
                self.st_dev = dev
                self.st_ino = ino
                self.st_mode = mode
                self.st_uid = uid
                self.st_gid = 0

        class Adapter:
            def __init__(self, overrides=None, acl=False):
                self.nodes = {
                    "/": Node(1, 1, stat.S_IFDIR | 0o755),
                    "/var": Node(1, 2, stat.S_IFDIR | 0o755),
                    "/var/tmp": Node(1, 3, stat.S_IFDIR | 0o1777),
                }
                self.nodes.update(overrides or {})
                self.acl = acl
                self.trace = []

            def lstat(self, path):
                self.trace.append(("lstat", path))
                return self.nodes[path]

            def has_extended_acl(self, path):
                self.trace.append(("acl", path))
                return self.acl

            def open_directory(self, path):
                self.trace.append(("open", path))
                return {"/var/tmp": 30}[path]

            def fstat(self, descriptor):
                self.trace.append(("fstat", descriptor))
                if descriptor == 30:
                    return self.nodes["/var/tmp"]
                return self.nodes["session"]

            def mkdtemp_at(self, parent_fd, prefix, mode):
                self.trace.append(("mkdtemp", parent_fd, prefix, mode))
                self.nodes["session"] = Node(1, 44, stat.S_IFDIR | 0o700)
                return "taiji-formal-tests.random", 31

            def close(self, descriptor):
                self.trace.append(("close", descriptor))

        adapter = Adapter()
        parent_fd, parent_identity = validate(adapter)
        session = make_session(adapter, parent_fd, parent_identity)
        self.assertEqual(session[0], "taiji-formal-tests.random")
        self.assertEqual(session[1], 31)
        self.assertEqual(session[2], (1, 44))
        self.assertIn(("mkdtemp", 30, "taiji-formal-tests.", 0o700), adapter.trace)

        cases = {
            "root-owner": {"/": Node(1, 1, stat.S_IFDIR | 0o755, uid=1)},
            "var-symlink": {"/var": Node(1, 2, stat.S_IFLNK | 0o777)},
            "var-writable": {"/var": Node(1, 2, stat.S_IFDIR | 0o775)},
            "tmp-owner": {"/var/tmp": Node(1, 3, stat.S_IFDIR | 0o1777, uid=2)},
            "tmp-no-sticky": {"/var/tmp": Node(1, 3, stat.S_IFDIR | 0o777)},
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(Exception):
                    validate(Adapter(overrides))
        with self.assertRaises(Exception):
            validate(Adapter(acl=True))

        adapter = Adapter()
        parent_fd, parent_identity = validate(adapter)
        adapter.nodes["/var/tmp"] = Node(1, 99, stat.S_IFDIR | 0o1777)
        with self.assertRaises(Exception):
            make_session(adapter, parent_fd, parent_identity)

    def test_temporary_identity_rejects_registered_calling_and_live_ids(self):
        _source, namespace = load_formal_supervisor()
        choose = namespace["choose_temporary_identity"]

        class Adapter:
            def __init__(self):
                self.passwd = set()
                self.groups = set()
                self.live_uids = set()
                self.live_gids = set()
                self.caller_uid = 64000
                self.caller_gids = {64000, 64001}

            def passwd_registered(self, value):
                return value in self.passwd

            def group_registered(self, value):
                return value in self.groups

            def live_process_ids(self):
                return self.live_uids, self.live_gids

        adapter = Adapter()
        adapter.passwd.add(65000)
        adapter.groups.add(65001)
        adapter.live_uids.add(65002)
        adapter.live_gids.add(65003)
        uid, gid = choose(adapter, range(65000, 65010))
        self.assertNotIn(uid, {65000, 65002, adapter.caller_uid})
        self.assertNotIn(gid, {65001, 65003, *adapter.caller_gids})
        self.assertFalse(adapter.passwd_registered(uid))
        self.assertFalse(adapter.group_registered(gid))

        adapter.passwd.update(range(65000, 65010))
        with self.assertRaises(Exception):
            choose(adapter, range(65000, 65010))

    def test_temporary_identity_rejects_proc_supplementary_group_collisions(self):
        source, namespace = load_formal_supervisor()
        parse = namespace["parse_proc_status_identity_lines"]
        choose = namespace["choose_temporary_identity"]
        live_uids, live_gids = parse([
            "Name:\tfixture\n",
            "Uid:\t64000\t64000\t64000\t64000\n",
            "Gid:\t64000\t64000\t64000\t64000\n",
            "Groups:\t65001 65007\n",
        ])
        self.assertEqual(live_uids, {64000})
        self.assertEqual(live_gids, {64000, 65001, 65007})

        class Adapter:
            caller_uid = 64000
            caller_gids = {64000}

            def passwd_registered(self, _value):
                return False

            def group_registered(self, _value):
                return False

            def live_process_ids(self):
                return live_uids, live_gids

        self.assertEqual(choose(Adapter(), range(65000, 65004)), (65000, 65002))
        self.assertIn('"/task/"', source)

    def test_agent_parallel_runner_is_python38_compatible_and_preserves_pytest_args(self):
        spec = importlib.util.spec_from_file_location(
            "taiji_formal_agent_parallel_runner",
            AGENT_PARALLEL_RUNNER,
        )
        if spec is None or spec.loader is None:
            self.fail("cannot load Agent parallel test runner")
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        fake_test = AGENT_PARALLEL_RUNNER.parents[1] / "tests/test_formal_fake.py"
        observed = {"duration_saves": 0, "pytest_args": None}
        runner._discover_files = lambda roots: [fake_test]
        runner._count_tests = lambda files, repo_root, pytest_args: {fake_test: 1}

        def fake_run_one_file(file, pytest_args, repo_root, file_timeout):
            observed["pytest_args"] = list(pytest_args)
            return file, 0, "1 passed in 0.01s\n", {"passed": 1}, 0.01

        def forbidden_duration_save(file_times, repo_root):
            observed["duration_saves"] += 1

        runner._run_one_file = fake_run_one_file
        runner._save_durations = forbidden_duration_save
        original_argv = sys.argv[:]
        is_relative_to = getattr(PurePath, "is_relative_to", None)
        try:
            if is_relative_to is not None:
                delattr(PurePath, "is_relative_to")
            sys.argv = [
                str(AGENT_PARALLEL_RUNNER),
                "--no-duration-cache",
                "tests/test_formal_fake.py",
                "--",
                "-p",
                "no:cacheprovider",
            ]
            result = runner.main()
        finally:
            sys.argv = original_argv
            if is_relative_to is not None:
                setattr(PurePath, "is_relative_to", is_relative_to)

        self.assertEqual(result, 0)
        self.assertEqual(observed["duration_saves"], 0)
        self.assertEqual(observed["pytest_args"], ["-p", "no:cacheprovider"])

    def test_agent_parallel_runner_reuses_the_formal_held_python_launcher(self):
        spec = importlib.util.spec_from_file_location(
            "taiji_formal_agent_parallel_runner_held",
            AGENT_PARALLEL_RUNNER,
        )
        if spec is None or spec.loader is None:
            self.fail("cannot load Agent parallel test runner")
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        executable = "/proc/4242/fd/17"
        site_packages = "/private/build/formal-agent-venv/lib/python3.11/site-packages"
        old_executable = runner.os.environ.get("TAIJI_FORMAL_PYTHON_EXECUTABLE")
        old_site = runner.os.environ.get("TAIJI_FORMAL_SITE_PACKAGES")
        try:
            runner.os.environ["TAIJI_FORMAL_PYTHON_EXECUTABLE"] = executable
            runner.os.environ["TAIJI_FORMAL_SITE_PACKAGES"] = site_packages
            command = runner._pytest_command(["--co", "-q", "tests/test_example.py"])
        finally:
            if old_executable is None:
                runner.os.environ.pop("TAIJI_FORMAL_PYTHON_EXECUTABLE", None)
            else:
                runner.os.environ["TAIJI_FORMAL_PYTHON_EXECUTABLE"] = old_executable
            if old_site is None:
                runner.os.environ.pop("TAIJI_FORMAL_SITE_PACKAGES", None)
            else:
                runner.os.environ["TAIJI_FORMAL_SITE_PACKAGES"] = old_site

        self.assertEqual(command[0], executable)
        self.assertEqual(command[1:4], ["-I", "-B", "-c"])
        self.assertIn(site_packages, command)
        self.assertNotIn(sys.executable, command)

    def test_formal_agent_runner_rejects_each_explicit_file_with_zero_collection(self):
        spec = importlib.util.spec_from_file_location(
            "taiji_formal_agent_parallel_runner_zero_collection",
            AGENT_PARALLEL_RUNNER,
        )
        if spec is None or spec.loader is None:
            self.fail("cannot load Agent parallel test runner")
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        first = AGENT_PARALLEL_RUNNER.parents[1] / "tests/test_formal_one.py"
        empty = AGENT_PARALLEL_RUNNER.parents[1] / "tests/test_formal_empty.py"
        runner._discover_files = lambda roots: [first, empty]
        runner._count_tests = lambda files, repo_root, pytest_args: {first: 1}
        runner._run_one_file = lambda file, pytest_args, repo_root, timeout: (
            file,
            0,
            "1 passed in 0.01s\n",
            {"passed": 1},
            0.01,
        )
        runner._save_durations = lambda file_times, repo_root: None
        original_argv = sys.argv[:]
        old_executable = runner.os.environ.get("TAIJI_FORMAL_PYTHON_EXECUTABLE")
        old_site = runner.os.environ.get("TAIJI_FORMAL_SITE_PACKAGES")
        try:
            runner.os.environ["TAIJI_FORMAL_PYTHON_EXECUTABLE"] = "/proc/4242/fd/17"
            runner.os.environ["TAIJI_FORMAL_SITE_PACKAGES"] = "/private/formal/site-packages"
            sys.argv = [
                str(AGENT_PARALLEL_RUNNER),
                "--no-duration-cache",
                "--require-nonempty-explicit-files",
                "tests/test_formal_one.py",
                "tests/test_formal_empty.py",
                "--",
                "-p",
                "no:cacheprovider",
            ]
            result = runner.main()
        finally:
            sys.argv = original_argv
            if old_executable is None:
                runner.os.environ.pop("TAIJI_FORMAL_PYTHON_EXECUTABLE", None)
            else:
                runner.os.environ["TAIJI_FORMAL_PYTHON_EXECUTABLE"] = old_executable
            if old_site is None:
                runner.os.environ.pop("TAIJI_FORMAL_SITE_PACKAGES", None)
            else:
                runner.os.environ["TAIJI_FORMAL_SITE_PACKAGES"] = old_site

        self.assertEqual(result, 1)
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn("--require-nonempty-explicit-files", builder)

    def test_failed_suite_records_the_real_exit_status_and_never_passes(self):
        builder = BUILDER.read_text(encoding="utf-8")
        start = builder.index("run_formal_test_step() {")
        end = builder.index("\n}\n\nbind_formal_build_test_evidence_to_manifest", start) + 2
        function_source = builder[start:end]
        with tempfile.TemporaryDirectory(prefix="taiji-formal-step-") as temp_dir:
            root = Path(temp_dir)
            log = root / "formal-build-tests.log"
            harness = root / "test-formal-step.sh"
            harness.write_text(
                "\n".join(
                    (
                        "#!/bin/bash",
                        "set -u",
                        'FORMAL_BUILD_TEST_LOG="{}"'.format(log),
                        "FORMAL_BUILD_TEST_LOG_FD=8",
                        "FORMAL_BUILD_TEST_LOG_READ_FD=9",
                        "exec 8> \"$FORMAL_BUILD_TEST_LOG\"",
                        "exec 9< \"$FORMAL_BUILD_TEST_LOG\"",
                        'fail() { printf "[FAIL] %s\\n" "$*" >&2; return 1; }',
                        'validate_formal_test_runtime_identity() { :; }',
                        'require_formal_build_test_log_identity() { :; }',
                        'formal_build_test_log_fd_path() { printf "/dev/fd/%s\\n" "$1"; }',
                        function_source,
                        'run_formal_test_step injected "{}" /bin/sh -c "exit 7"'.format(root),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/bash", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )
            payload = log.read_text(encoding="utf-8")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("suite_status=injected:fail:7", payload)
        self.assertNotIn("suite_status=injected:pass", payload)

    def test_held_log_fd_detects_path_replacement_and_preserves_foreign_file(self):
        builder = BUILDER.read_text(encoding="utf-8")
        start = builder.index("run_formal_test_step() {")
        end = builder.index("\n}\n\nbind_formal_build_test_evidence_to_manifest", start) + 2
        function_source = builder[start:end]
        with tempfile.TemporaryDirectory(prefix="taiji-formal-log-swap-") as temp_dir:
            root = Path(temp_dir)
            log = root / "formal-build-tests.log"
            foreign = root / "foreign-valid.log"
            poison = root / ".formal-build-tests.poisoned"
            foreign_payload = (
                "schema=taiji-formal-build-tests/v1\n"
                "suite_begin=injected\n"
                "suite_status=injected:pass\n"
                "overall_status=pass\n"
            )
            foreign.write_text(foreign_payload, encoding="utf-8")
            harness = root / "swap-formal-log.sh"
            harness.write_text(
                "\n".join(
                    (
                        "#!/bin/bash",
                        "set -u",
                        'FORMAL_BUILD_TEST_LOG="{}"'.format(log),
                        'FORMAL_BUILD_TEST_LOG_POISON="{}"'.format(poison),
                        "FORMAL_BUILD_TEST_LOG_FD=8",
                        "FORMAL_BUILD_TEST_LOG_READ_FD=9",
                        "exec 8> \"$FORMAL_BUILD_TEST_LOG\"",
                        "exec 9< \"$FORMAL_BUILD_TEST_LOG\"",
                        'fail() { printf "[FAIL] %s\\n" "$*" >&2; exit 1; }',
                        'validate_formal_test_runtime_identity() { :; }',
                        'formal_build_test_log_fd_path() { printf "/dev/fd/%s\\n" "$1"; }',
                        'poison_formal_build_test_log() { printf "poisoned\\n" > "$FORMAL_BUILD_TEST_LOG_POISON"; }',
                        'require_formal_build_test_log_identity() { '
                        '[ "$(stat -f %i "$FORMAL_BUILD_TEST_LOG")" = '
                        '"$(stat -Lf %i "/dev/fd/$FORMAL_BUILD_TEST_LOG_FD")" ] '
                        '|| { poison_formal_build_test_log "$1"; fail "identity drift"; }; }',
                        function_source,
                        'run_formal_test_step injected "{}" /bin/mv -f "{}" "{}"'.format(
                            root, foreign, log
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/bash", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("identity drift", result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8"), foreign_payload)
            self.assertTrue(poison.is_file())

    def test_formal_log_is_created_once_and_never_reopened_by_path_for_writes(self):
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn("exec {FORMAL_BUILD_TEST_LOG_FD}>", builder)
        self.assertIn("set -o noclobber", builder)
        self.assertIn("/proc/%s/fd/%s", builder)
        self.assertNotIn(': > "$FORMAL_BUILD_TEST_LOG"', builder)
        self.assertNotIn('>> "$FORMAL_BUILD_TEST_LOG"', builder)
        self.assertIn('>&"$FORMAL_BUILD_TEST_LOG_FD"', builder)
        self.assertIn('FORMAL_BUILD_TEST_LOG_READ_FD', builder)
        self.assertIn('require_formal_build_test_log_identity "after held-FD hash"', builder)

    def test_formal_manifest_is_published_once_after_tests_without_public_rewrite(self):
        builder = BUILDER.read_text(encoding="utf-8")
        collect_start = builder.index("collect_artifacts() {")
        collect_end = builder.index("\n}\n\nvalidate_formal_test_python_identity", collect_start)
        collect = builder[collect_start:collect_end]
        bind_start = builder.index("bind_formal_build_test_evidence_to_manifest() {")
        bind_end = builder.index("\n}\n\nrun_formal_build_tests", bind_start)
        binding = builder[bind_start:bind_end]
        main = builder[builder.index("main() {") :]

        self.assertNotIn('cp -f "$manifest" "$MANIFEST_FILE"', collect)
        self.assertIn("SOURCE_PACKAGE_MANIFEST_FD", collect)
        self.assertLess(main.index("run_formal_build_tests"), main.index("write_pending_build_marker"))
        self.assertIn('exec {FORMAL_PACKAGE_MANIFEST_FD}> "$MANIFEST_FILE"', binding)
        self.assertIn("set -o noclobber", binding)
        self.assertIn("FORMAL_PACKAGE_MANIFEST_READ_FD", binding)
        self.assertIn("require_formal_package_manifest_identity", binding)
        self.assertNotIn("manifest_backup", binding)
        self.assertNotIn('mv -Tn -- "$MANIFEST_FILE"', binding)
        self.assertNotIn('rm -f -- "$manifest_tmp"', binding)
        self.assertNotIn('rm -f -- "$manifest_backup"', binding)
        self.assertIn(
            'require_formal_package_manifest_identity "main after publish"', main
        )

    def test_every_public_build_output_uses_single_no_clobber_publication(self):
        builder = BUILDER.read_text(encoding="utf-8")
        slices = {}
        for name, next_name in (
            ("collect_artifacts", "validate_formal_test_python_identity"),
            ("open_formal_build_test_log", "close_formal_build_test_log_fds"),
            ("bind_formal_build_test_evidence_to_manifest", "run_formal_build_tests"),
            ("write_build_report", "archive_stale_acceptance_staging"),
        ):
            start = builder.index(name + "() {")
            end = builder.index("\n" + next_name + "() {", start)
            slices[name] = builder[start:end]

        for name in (
            "collect_artifacts",
            "open_formal_build_test_log",
            "bind_formal_build_test_evidence_to_manifest",
            "write_build_report",
        ):
            self.assertIn("noclobber", slices[name], name)
        for forbidden in (
            'rm -f -- "$BUILD_MARKER"',
            'rm -f -- "$BUILD_REPORT"',
            'rm -f -- "$FORMAL_BUILD_TEST_LOG"',
            'rm -f -- "$MANIFEST_FILE"',
            'cp -f "$deb"',
            'cp -f "$manifest"',
            '} > "$BUILD_REPORT"',
            ': > "$FORMAL_BUILD_TEST_LOG"',
            'mv -- "$MANIFEST_FILE"',
        ):
            self.assertNotIn(forbidden, builder)
        marker_publish = builder[
            builder.index("publish_build_success_marker() {") :
            builder.index("stage_pending_build_marker_for_publication() {")
        ]
        pending_stage = builder[
            builder.index("stage_pending_build_marker_for_publication() {") :
            builder.index("poison_candidate_artifacts() {")
        ]
        self.assertIn("os.O_EXCL", marker_publish)
        self.assertIn("os.O_EXCL", pending_stage)
        self.assertNotIn("os.unlink(destination)", marker_publish)
        self.assertNotIn("os.unlink(destination)", pending_stage)

    def test_foreign_report_and_private_pending_symlink_are_preserved(self):
        builder = BUILDER.read_text(encoding="utf-8")
        report_start = builder.index("write_build_report() {")
        report_end = builder.index("\n}\n\n\narchive_stale_acceptance_staging", report_start) + 2
        report_function = builder[report_start:report_end]
        pending_start = builder.index("write_pending_build_marker() {")
        pending_end = builder.index("\n}\n\npoison_published_build_marker", pending_start) + 2
        pending_function = builder[pending_start:pending_end]

        with tempfile.TemporaryDirectory(prefix="taiji-public-no-clobber-") as temp_dir:
            root = Path(temp_dir)
            output = root / "output"
            output.mkdir()
            source = root / "source.tar.gz"
            source.write_bytes(b"source")
            deb = output / "taiji-agent_1.2.3_amd64.deb"
            deb.write_bytes(b"deb")
            report = output / "build-report.txt"
            report.write_text("FOREIGN_REPORT\n", encoding="utf-8")
            report_harness = root / "report.sh"
            report_harness.write_text(
                "\n".join(
                    (
                        "#!/bin/bash",
                        "set -euo pipefail",
                        'SCRIPT_DIR="{}"'.format(root),
                        'OUTPUT_DIR="{}"'.format(output),
                        'SRC_ARCHIVE="{}"'.format(source),
                        'BUILD_REPORT="{}"'.format(report),
                        'VERSION=1.2.3',
                        'require_candidate_deb_fixed() { :; }',
                        'poison_candidate_artifacts() {{ printf "poison\\n" > "{}/report.poison"; }}'.format(root),
                        'sha256sum() { /usr/bin/shasum -a 256 "$@"; }',
                        'fail() { printf "FAIL:%s\\n" "$*" >&2; exit 23; }',
                        report_function,
                        "write_build_report",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            report_result = subprocess.run(
                ["/bin/bash", str(report_harness)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(report_result.returncode, 23, report_result.stderr)
            self.assertEqual(report.read_text(encoding="utf-8"), "FOREIGN_REPORT\n")
            self.assertTrue((root / "report.poison").is_file())

            build_root = root / "build"
            build_root.mkdir()
            target = root / "protected-target"
            target.write_text("PROTECTED\n", encoding="utf-8")
            pending = build_root / ".build-success.pending"
            pending.symlink_to(target)
            pending_harness = root / "pending.sh"
            pending_harness.write_text(
                "\n".join(
                    (
                        "#!/bin/bash",
                        "set -euo pipefail",
                        'BUILD_ROOT="{}"'.format(build_root),
                        'PENDING_BUILD_MARKER="{}"'.format(pending),
                        'FORMAL_BUILD_TESTS_STATUS=pass',
                        'FORMAL_BUILD_TESTS_LOG_BASENAME=formal-build-tests.log',
                        'FORMAL_BUILD_TESTS_LOG_SHA256="{}"'.format("a" * 64),
                        'require_candidate_deb_fixed() { :; }',
                        'require_formal_package_manifest_identity() { :; }',
                        'require_formal_build_test_log_identity() { :; }',
                        'poison_pending_build_marker() { printf "poison\\n" > "$PENDING_BUILD_MARKER_POISON"; }',
                        'fail() { printf "FAIL:%s\\n" "$*" >&2; exit 23; }',
                        pending_function,
                        "write_pending_build_marker",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            pending_result = subprocess.run(
                ["/bin/bash", str(pending_harness)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(pending_result.returncode, 23, pending_result.stderr)
            self.assertTrue(pending.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "PROTECTED\n")
            self.assertTrue((build_root / ".build-success.pending.poisoned").is_file())

    def test_held_manifest_and_log_checks_bind_full_identity_and_content_digest(self):
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn("held_file_identity_and_sha256", builder)
        self.assertIn("st_mtime_ns", builder)
        self.assertIn("st_ctime_ns", builder)
        self.assertIn("SOURCE_PACKAGE_MANIFEST_SHA256", builder)
        self.assertIn("FORMAL_PACKAGE_MANIFEST_SHA256", builder)
        self.assertIn("FORMAL_BUILD_TESTS_LOG_SHA256", builder)
        source_check_start = builder.index("source_package_manifest_identity_matches() {")
        source_check_end = builder.index("\n}\n", source_check_start)
        source_check = builder[source_check_start:source_check_end]
        formal_check_start = builder.index("formal_package_manifest_identity_matches() {")
        formal_check_end = builder.index("\n}\n", formal_check_start)
        formal_check = builder[formal_check_start:formal_check_end]
        log_check_start = builder.index("formal_build_test_log_identity_matches() {")
        log_check_end = builder.index("\n}\n", log_check_start)
        log_check = builder[log_check_start:log_check_end]
        self.assertIn("SOURCE_PACKAGE_MANIFEST_SHA256", source_check)
        self.assertIn("FORMAL_PACKAGE_MANIFEST_SHA256", formal_check)
        self.assertIn("FORMAL_BUILD_TESTS_LOG_SHA256", log_check)

    def test_fixed_log_digest_rejects_same_inode_overwrite_and_truncate(self):
        builder = BUILDER.read_text(encoding="utf-8")
        start = builder.index("held_file_identity_and_sha256() {")
        end = builder.index("\n}\n\npoison_formal_build_test_log", start) + 2
        functions = builder[start:end]
        with tempfile.TemporaryDirectory(prefix="taiji-held-log-content-") as temp_dir:
            root = Path(temp_dir)
            for mutation in ("same-size", "truncate"):
                with self.subTest(mutation=mutation):
                    log = root / (mutation + ".log")
                    log.write_text("AAAA\n", encoding="utf-8")
                    harness = root / (mutation + ".sh")
                    mutation_command = (
                        'printf "BBBB\\n" > "$FORMAL_BUILD_TEST_LOG"'
                        if mutation == "same-size"
                        else ': > "$FORMAL_BUILD_TEST_LOG"'
                    )
                    harness.write_text(
                        "\n".join(
                            (
                                "#!/bin/bash",
                                "set -u",
                                'FORMAL_BUILD_TEST_LOG="{}"'.format(log),
                                "FORMAL_BUILD_TEST_LOG_FD=8",
                                "FORMAL_BUILD_TEST_LOG_READ_FD=9",
                                'FORMAL_BUILD_TEST_LOG_IDENTITY="bootstrap"',
                                'FORMAL_BUILD_TESTS_LOG_SHA256=""',
                                'exec 8<> "$FORMAL_BUILD_TEST_LOG"',
                                'exec 9< "$FORMAL_BUILD_TEST_LOG"',
                                functions,
                                'formal_build_test_log_fd_path() { printf "/dev/fd/%s\\n" "$1"; }',
                                "formal_build_test_log_identity_matches || exit 31",
                                'FORMAL_BUILD_TESTS_LOG_SHA256="$(/usr/bin/shasum -a 256 "$FORMAL_BUILD_TEST_LOG" | awk \'{print $1}\')"',
                                "formal_build_test_log_identity_matches || exit 32",
                                mutation_command,
                                "if formal_build_test_log_identity_matches; then exit 33; fi",
                            )
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    result = subprocess.run(
                        ["/bin/bash", str(harness)],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_local_release_gate_has_no_node_or_gitignored_venv_dependency(self):
        runner = RELEASE_TEST_RUNNER.read_text(encoding="utf-8")
        release_check = RELEASE_CHECK.read_text(encoding="utf-8")

        self.assertTrue(
            "tests.test_taiji_license_issuer_gui" not in runner,
            "the local runner still executes Node/agent-runtime tests",
        )
        self.assertTrue(
            "tests.test_kylin_install_script_simulation" not in runner,
            "the local runner still executes Linux-only shell simulations",
        )
        for forbidden in (
            "node --test",
            "npm run",
            "run_agent_tests",
            "run_webui_tests",
            "hermes-agent/venv",
        ):
            self.assertTrue(
                forbidden not in release_check,
                "the local release gate still depends on {!r}".format(forbidden),
            )
        self.assertTrue(
            "verify_formal_build_test_evidence" in release_check,
            "the local release gate does not reverify builder test evidence",
        )

    def test_builder_allows_only_the_exact_pinned_venv_python_symlink(self):
        builder = BUILDER.read_text(encoding="utf-8")
        build_deb = BUILD_DEB.read_text(encoding="utf-8")
        symlink_path = "hermes-local-lab/sources/hermes-agent/venv/bin/python"

        self.assertIn(
            'readonly AGENT_PYTHON_SYMLINK_TARGET="$python_symlink_target"',
            builder,
        )
        self.assertIn('readlink -f "$python_bin"', builder)
        self.assertIn('readlink -f "$PYTHON_BIN"', builder)
        self.assertIn(
            '"{}"\n      "$AGENT_PYTHON_SYMLINK_TARGET"'.format(symlink_path),
            builder,
        )
        self.assertIn('TAIJI_PYTHON_EXECUTABLE="$PYTHON_BIN"', builder)
        self.assertIn(
            'TAIJI_AGENT_PYTHON_SYMLINK_TARGET="$AGENT_PYTHON_SYMLINK_TARGET"',
            builder,
        )
        self.assertIn(
            'EXPECTED_PYTHON_EXECUTABLE="${TAIJI_PYTHON_EXECUTABLE:-}"',
            build_deb,
        )
        self.assertIn(
            'EXPECTED_AGENT_PYTHON_SYMLINK_TARGET="${TAIJI_AGENT_PYTHON_SYMLINK_TARGET:-}"',
            build_deb,
        )
        self.assertIn(
            '--allow-extra-symlink "{}" "$EXPECTED_AGENT_PYTHON_SYMLINK_TARGET"'.format(
                symlink_path
            ),
            build_deb,
        )
        self.assertIn('rm -rf -- "$agent_venv"', builder)

    def test_build_deb_binds_uv_raw_target_after_realpath_canonicalization(self):
        build_deb = BUILD_DEB.read_text(encoding="utf-8")
        start = build_deb.index("validate_source_archive_integrity() {")
        end = build_deb.index("\n}\n", start) + 2
        function_source = build_deb[start:end]

        with tempfile.TemporaryDirectory(prefix="taiji-uv-raw-target-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            agent = repo / "hermes-local-lab/sources/hermes-agent"
            agent_python = agent / "venv/bin/python"
            agent_python.parent.mkdir(parents=True)
            runtime = root / "python-runtime"
            real_python = runtime / "bin/python3.11"
            real_python.parent.mkdir(parents=True)
            real_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            real_python.chmod(0o755)
            alias = root / "python-current"
            alias.symlink_to(runtime, target_is_directory=True)
            expected_python = alias / "bin/python3.11"
            agent_python.symlink_to(real_python)

            other_runtime = root / "other-python-runtime"
            other_python = other_runtime / "bin/python3.11"
            other_python.parent.mkdir(parents=True)
            other_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            other_python.chmod(0o755)
            other_alias = root / "other-python-current"
            other_alias.symlink_to(other_runtime, target_is_directory=True)

            helper = root / "source-integrity.py"
            archive = root / "source.tar.gz"
            inventory = root / "source.inventory.json"
            for path in (helper, archive, inventory):
                path.write_text("fixture\n", encoding="utf-8")
            capture = root / "captured-args"

            def run(expected_target, expected_executable):
                harness = """
set -euo pipefail
fail() {{ printf '%s\n' "$*" >&2; exit 1; }}
sha256sum() {{ printf 'fixture  %s\n' "$1"; }}
python3() {{ printf '%s\n' "$@" > {capture!r}; }}
SOURCE_INTEGRITY_HELPER={helper!r}
PINNED_SOURCE_INTEGRITY_HELPER_SHA256=fixture
SOURCE_ARCHIVE_PATH={archive!r}
SOURCE_INVENTORY_PATH={inventory!r}
SOURCE_INVENTORY_SHA256=fixture
SOURCE_AGENT_DIR={agent!r}
REPO_ROOT={repo!r}
EXPECTED_AGENT_PYTHON_SYMLINK_TARGET={target!r}
EXPECTED_PYTHON_EXECUTABLE={executable!r}
{function_source}
validate_source_archive_integrity
""".format(
                    capture=str(capture),
                    helper=str(helper),
                    archive=str(archive),
                    inventory=str(inventory),
                    agent=str(agent),
                    repo=str(repo),
                    target=str(expected_target),
                    executable=str(expected_executable),
                    function_source=function_source,
                )
                return subprocess.run(
                    ["/bin/bash", "-c", harness],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            accepted = run(real_python, expected_python)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            captured = capture.read_text(encoding="utf-8")
            self.assertIn(str(real_python), captured)
            self.assertNotIn(str(expected_python), captured)

            wrong_raw_target = run(other_python, expected_python)
            self.assertNotEqual(wrong_raw_target.returncode, 0)
            self.assertIn("raw symlink target", wrong_raw_target.stderr)

            wrong_realpath = run(real_python, other_alias / "bin/python3.11")
            self.assertNotEqual(wrong_realpath.returncode, 0)
            self.assertIn("resolve to different files", wrong_realpath.stderr)

    def test_python38_gate_executes_agent_runner_and_fixed_sources_avoid_new_apis(self):
        gate = PYTHON38_GATE.read_text(encoding="utf-8")
        self.assertIn("AGENT_PARALLEL_RUNNER", gate)
        self.assertIn('agent_runner["main"]()', gate)
        for source_path in (AGENT_PARALLEL_RUNNER, LINUX_PACKAGING_TESTS):
            source = source_path.read_text(encoding="utf-8")
            for forbidden in (".is_relative_to(", ".removeprefix(", ".removesuffix("):
                self.assertNotIn(forbidden, source, "{} uses {}".format(source_path, forbidden))

    def test_python38_gate_compiles_supervisor_and_exercises_formal_pytest_path(self):
        gate = PYTHON38_GATE.read_text(encoding="utf-8")
        for required in (
            "extract_formal_builder_embedded_python",
            '"formal_build_root_supervisor_python_source"',
            '"formal_build_supervisor_bootstrap_python_source"',
            '"formal_build_supervisor_log_relay_python_source"',
            "exercise_formal_agent_parallel_runner",
            'agent_runner["_run_formal_pytest_session"]',
        ):
            self.assertIn(required, gate)

    def test_builder_runs_all_runtime_dependent_tests_with_verified_tools(self):
        builder = BUILDER.read_text(encoding="utf-8")
        main = builder[builder.rindex("\nmain() {") + 1 :]
        formal = builder[
            builder.index("prepare_formal_build_test_dependencies() {") :
            builder.index("pending_build_marker_identity_matches() {")
        ]

        for required in (
            "run_formal_build_tests",
            '"$PYTHON_BIN" -I -B -m venv --copies "$FORMAL_AGENT_VENV"',
            'UV_PROJECT_ENVIRONMENT="$FORMAL_AGENT_VENV"',
            '"$FORMAL_NODE_HELD_PATH"',
            '"$FORMAL_PYTHON_HELD_PATH"',
            '"$FORMAL_NPM_CLI_HELD_PATH"',
            '"$FORMAL_ESLINT_HELD_PATH"',
            "/usr/bin/env -i",
            '--allow-extra-prefix "hermes-local-lab/sources/hermes-webui/node_modules"',
        ):
            self.assertTrue(
                required in builder,
                "the builder is missing verified test runtime {!r}".format(required),
            )
        self.assertNotIn('"$NODE_ROOT/current/bin/npm"', formal)
        self.assertNotIn('"$NODE_ROOT/current/bin/node" --test', formal)
        self.assertNotIn('agent_python="$(source_agent_dir)/venv/bin/python"', formal)
        self.assertIn("NODE_NPM_CLI_ARCHIVE_SHA256", builder)
        self.assertIn("FORMAL_TEST_RUNTIME_SEALED=1", formal)
        self.assertIn("validate_formal_test_runtime_identity", formal)
        self.assertIn("run_held_node_script", formal)
        self.assertLess(
            main.index("build_runtime_and_deb"),
            main.index("run_formal_build_tests"),
        )
        self.assertLess(
            main.index("run_formal_build_tests"),
            main.index("write_pending_build_marker"),
        )

        for suite in (
            "root-runtime",
            "desktop-evidence-node",
            "kylin-install-simulation",
            "agent",
            "webui-runtime-lint",
            "webui-python",
        ):
            self.assertTrue(
                suite in builder,
                "the builder is missing formal suite {!r}".format(suite),
            )
        for test_path in (
            "tests/tools/test_taiji_security_mode.py",
            "tests/test_taiji_license.py",
            "tests/gateway/test_api_server_license.py",
            "tests/gateway/test_session_api.py",
            "tests/tools/test_image_generation_readiness.py",
            "tests/test_brand_privacy.py",
            "tests/test_model_config_api.py",
            "tests/test_model_config_frontend.py",
            "tests/test_approval_queue.py",
            "tests/test_approval_sse.py",
            "tests/test_pr1350_sse_notify_correctness.py",
            "tests/test_expert_team_frontend.py",
            "tests/test_ui_visibility_config.py",
            "tests/test_issue1800_file_html_interactions.py",
            "tests/test_writeflow_frontend.py::test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell",
            "tests/test_issue1116_composer_placeholder.py",
        ):
            self.assertIn(test_path, builder)

        for required in (
            "readlink -f \"$agent_python\"",
            "readlink -f \"$PYTHON_BIN\"",
            "PYTHON_PINNED_EXECUTABLE_SHA256",
            "validate_formal_test_python_identity",
            "manifest_identity",
        ):
            self.assertIn(required, builder)

    def test_formal_runtime_rejects_node_current_retarget_and_seals_every_argv(self):
        builder = BUILDER.read_text(encoding="utf-8")
        symlink_start = builder.index("symlink_identity_and_target() {")
        symlink_end = builder.index("\n}\n\nseal_formal_test_node_runtime", symlink_start) + 2
        validate_start = builder.index("validate_formal_test_runtime_identity() {")
        validate_end = builder.index("\n}\n\nclose_formal_test_runtime_fds", validate_start) + 2
        functions = builder[symlink_start:symlink_end] + "\n" + builder[validate_start:validate_end]

        with tempfile.TemporaryDirectory(prefix="taiji-formal-node-retarget-") as temp_dir:
            root = Path(temp_dir)
            runtime_a = root / "node-a"
            runtime_b = root / "node-b"
            for runtime in (runtime_a, runtime_b):
                (runtime / "bin").mkdir(parents=True)
                node = runtime / "bin/node"
                node.write_text("#!/bin/sh\nprintf 'v22.23.1\\n'\n", encoding="utf-8")
                node.chmod(0o755)
            current = root / "current"
            current.symlink_to(runtime_a, target_is_directory=True)
            fake_python = root / "formal-python"
            fake_python.write_text("#!/bin/sh\nprintf '3.11.15\\n'\n", encoding="utf-8")
            fake_python.chmod(0o755)
            harness = root / "retarget.sh"
            harness.write_text(
                "\n".join(
                    (
                        "#!/bin/bash",
                        "set -euo pipefail",
                        'BUILD_ROOT="{}"'.format(root),
                        'BUILD_TMP_DIR="{}"'.format(root),
                        'FORMAL_NODE_CURRENT_PATH="{}"'.format(current),
                        'FORMAL_NODE_PATH="$(readlink -f "$FORMAL_NODE_CURRENT_PATH/bin/node")"',
                        'FORMAL_NODE_HELD_PATH="$FORMAL_NODE_PATH"',
                        'FORMAL_NPM_CLI_PATH="{}/npm-cli.js"'.format(root),
                        'FORMAL_NPM_CLI_HELD_PATH="$FORMAL_NPM_CLI_PATH"',
                        'FORMAL_PYTHON_PATH="{}"'.format(fake_python),
                        'FORMAL_PYTHON_HELD_PATH="$FORMAL_PYTHON_PATH"',
                        'FORMAL_ESLINT_FD=""',
                        'FORMAL_NODE_IDENTITY=node-id',
                        'FORMAL_NODE_SHA256=node-sha',
                        'FORMAL_NPM_CLI_IDENTITY=npm-id',
                        'FORMAL_NPM_CLI_SHA256=npm-sha',
                        'FORMAL_PYTHON_IDENTITY=python-id',
                        'FORMAL_PYTHON_SHA256=python-sha',
                        'FORMAL_TEST_RUNTIME_SEALED=1',
                        'NODE_VERSION=22.23.1',
                        'NODE_NPM_VERSION_ARCHIVE=10.9.8',
                        'PYTHON_VERSION_PINNED=3.11.15',
                        'printf npm > "$FORMAL_NPM_CLI_PATH"',
                        functions,
                        'result="$(symlink_identity_and_target "$FORMAL_NODE_CURRENT_PATH")"',
                        "IFS=$'\\t' read -r FORMAL_NODE_CURRENT_IDENTITY FORMAL_NODE_CURRENT_RAW_TARGET <<< \"$result\"",
                        'held_file_identity_and_sha256() {',
                        '  case "$2" in',
                        '    "$FORMAL_NODE_PATH") printf "node-id\\tnode-sha\\n" ;;',
                        '    "$FORMAL_NPM_CLI_PATH") printf "npm-id\\tnpm-sha\\n" ;;',
                        '    "$FORMAL_PYTHON_PATH") printf "python-id\\tpython-sha\\n" ;;',
                        '    *) return 1 ;;',
                        '  esac',
                        '}',
                        'run_held_node_script() { printf "10.9.8\\n"; }',
                        'validate_formal_test_runtime_identity',
                        'ln -sfn "{}" "$FORMAL_NODE_CURRENT_PATH"'.format(runtime_b),
                        'if validate_formal_test_runtime_identity; then exit 41; fi',
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/bash", str(harness)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for canonical in (
            "FORMAL_NODE_PATH",
            "FORMAL_NPM_CLI_PATH",
            "FORMAL_PYTHON_PATH",
            "FORMAL_ESLINT_PATH",
        ):
            self.assertIn(canonical, builder[validate_start:validate_end])
        self.assertIn('"$FIXED_TOOL_ARCHIVE_FD_PATH" "node-v${NODE_VERSION}-linux-x64"', builder)
        self.assertIn('cli_name = expected_root + "/lib/node_modules/npm/bin/npm-cli.js"', builder)
        self.assertIn("hashlib.sha256(cli_payload).hexdigest()", builder)

    def test_marker_manifest_and_preflight_bind_one_pass_log(self):
        sources = {
            "builder": BUILDER.read_text(encoding="utf-8"),
            "preflight": PREFLIGHT.read_text(encoding="utf-8"),
            "release-check": RELEASE_CHECK.read_text(encoding="utf-8"),
            "evidence-validator": EVIDENCE_VALIDATOR.read_text(encoding="utf-8"),
        }
        required_fields = (
            "formal_build_tests_status",
            "formal_build_tests_log_basename",
            "formal_build_tests_log_sha256",
        )

        for label, source in sources.items():
            with self.subTest(source=label):
                for field in required_fields:
                    self.assertTrue(
                        field in source,
                        "{} does not bind {}".format(label, field),
                    )
                self.assertTrue(
                    "formal-build-tests.log" in source,
                    "{} does not bind the canonical test log".format(label),
                )
        for label in ("builder", "preflight", "release-check", "evidence-validator"):
            self.assertTrue(
                "pass" in sources[label],
                "{} does not require a PASS result".format(label),
            )

    def test_webui_test_dependencies_are_builder_only_and_never_enter_the_deb(self):
        builder = BUILDER.read_text(encoding="utf-8")
        preflight = PREFLIGHT.read_text(encoding="utf-8")
        build_deb = BUILD_DEB.read_text(encoding="utf-8")
        runner = AGENT_PARALLEL_RUNNER.read_text(encoding="utf-8")
        allow_prefix = (
            '--allow-extra-prefix '
            '"hermes-local-lab/sources/hermes-webui/node_modules"'
        )
        self.assertIn(allow_prefix, builder)
        self.assertIn(allow_prefix, preflight)
        self.assertIn("--exclude 'node_modules'", build_deb)
        self.assertIn("--no-duration-cache", builder)
        self.assertIn('os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"', runner)
        for plugin in ("no:cacheprovider", "pytest_asyncio.plugin", "pytest_timeout"):
            self.assertEqual(runner.count('"' + plugin + '"'), 1)
        self.assertNotIn('--allow-extra-prefix ".pytest_cache"', builder)
        self.assertNotIn('--allow-extra-prefix "test_durations.json"', builder)

    def test_log_semantics_reject_tampering_even_when_digest_is_rebound(self):
        validator = load_evidence_validator()
        source_commit = "a" * 40
        python_sha = validator.PINNED_PYTHON_EXECUTABLE_SHA256
        node_sha = validator.PINNED_NODE_EXECUTABLE_SHA256
        valid_log = canonical_formal_v2_log(
            source_commit,
            validator.PINNED_PYTHON_VERSION,
            python_sha,
            validator.PINNED_NODE_VERSION,
            node_sha,
            validator.PINNED_NPM_VERSION,
            validator.PINNED_NPM_CLI_SHA256,
        ).decode("utf-8")
        target_lines = [
            line for line in valid_log.splitlines() if line.startswith("target_result=")
        ]
        target_zero = target_lines[0]
        target_three = target_lines[3]
        target_four = target_lines[4]

        mutations = {
            "missing-suite": valid_log.replace("suite_begin=webui-python\n", "", 1),
            "duplicate-pass": valid_log.replace(
                "suite_status=agent:pass\n",
                "suite_status=agent:pass\nsuite_status=agent:pass\n",
                1,
            ),
            "failed-suite": valid_log.replace(
                "suite_status=root-runtime:pass",
                "suite_status=root-runtime:fail:7",
                1,
            ),
            "appended-content": valid_log + "unexpected-after-close\n",
            "wrong-header-identity": valid_log.replace(
                "python_executable_sha256=" + python_sha,
                "python_executable_sha256=" + ("d" * 64),
                1,
            ),
            "wrong-npm-cli": valid_log.replace(
                "npm_cli_sha256=" + validator.PINNED_NPM_CLI_SHA256,
                "npm_cli_sha256=" + ("e" * 64),
                1,
            ),
            "missing-eslint-identity": valid_log.replace(
                "eslint_cli_sha256=" + ("6" * 64) + "\n", "", 1
            ),
            "wrong-target-count-header": valid_log.replace(
                "target_count=20", "target_count=19", 1
            ),
            "wrong-target-contract": valid_log.replace(
                FORMAL_TARGET_CONTRACT_SHA256, "f" * 64, 1
            ),
            "missing-target": valid_log.replace(target_zero + "\n", "", 1),
            "duplicate-target": valid_log.replace(
                target_zero + "\n", target_zero + "\n" + target_zero + "\n", 1
            ),
            "reordered-targets": valid_log.replace(
                target_three + "\n" + target_four + "\n",
                target_four + "\n" + target_three + "\n",
                1,
            ),
            "forged-target": valid_log.replace(
                target_zero,
                target_zero.replace(
                    "tests/test_taiji_license_issuer_gui.py",
                    "tests/forged.py",
                ),
                1,
            ),
            "forged-runner": valid_log.replace(
                target_zero,
                target_zero.replace("\tunittest\t", "\tpytest\t"),
                1,
            ),
            "zero-collected": valid_log.replace(
                target_zero, target_zero.rsplit("\t", 7)[0] + "\t0\t0\t0\t0\t0\t0\t0", 1
            ),
            "skipped-target": valid_log.replace(
                target_zero, target_zero.rsplit("\t", 7)[0] + "\t1\t0\t1\t0\t0\t0\t1", 1
            ),
            "wrong-suite-counts": valid_log.replace(
                "suite_counts=root-runtime\t1\t1\t0\t1\t1\t0\t0\t0",
                "suite_counts=root-runtime\t1\t2\t0\t2\t2\t0\t0\t0",
                1,
            ),
            "invalid-child-base64": valid_log.replace(
                "child_output=root-runtime\tstdout\tZml4dHVyZSBvdXRwdXQK",
                "child_output=root-runtime\tstdout\t***",
                1,
            ),
            "duplicate-child-channel": valid_log.replace(
                "child_output=root-runtime\tstdout\tZml4dHVyZSBvdXRwdXQK\n",
                "child_output=root-runtime\tstdout\tZml4dHVyZSBvdXRwdXQK\n"
                "child_output=root-runtime\tstdout\tZHVwbGljYXRlCg==\n",
                1,
            ),
            "child-control-injection": valid_log.replace(
                "child_output=root-runtime\tstdout\tZml4dHVyZSBvdXRwdXQK\n",
                "child_output=root-runtime\tstdout\tZml4dHVyZSBvdXRwdXQK\n"
                "overall_status=pass\n",
                1,
            ),
        }

        with tempfile.TemporaryDirectory(prefix="taiji-formal-log-") as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "taiji-package-manifest.json"
            marker_path = root / ".build-success"
            log_path = root / "formal-build-tests.log"

            def write_contract(payload):
                log_path.write_text(payload, encoding="utf-8")
                digest = hashlib.sha256(log_path.read_bytes()).hexdigest()
                common = {
                    "formal_build_tests_status": "pass",
                    "formal_build_tests_log_basename": "formal-build-tests.log",
                    "formal_build_tests_log_sha256": digest,
                }
                manifest = {
                    key: "fixture"
                    for key in validator.PACKAGE_MANIFEST_V3_EXACT_FIELDS
                }
                manifest.update({
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "architecture": "amd64",
                    "source_commit": source_commit,
                    "python_version": "3.11.15",
                    "python_executable_sha256": python_sha,
                    "node_version": "22.23.1",
                    "node_executable_sha256": node_sha,
                    **common,
                })
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                marker_path.write_text(
                    "".join("{}={}\n".format(key, value) for key, value in common.items()),
                    encoding="utf-8",
                )

            write_contract(valid_log)
            validator.validate_formal_build_test_log_binding(
                manifest_path,
                marker_path,
                log_path,
            )
            pending_root = root / "pending"
            pending_root.mkdir()
            pending_marker = pending_root / ".build-success.pending"
            pending_marker.write_bytes(marker_path.read_bytes())
            validator.validate_formal_build_test_log_binding(
                manifest_path,
                pending_marker,
                log_path,
                pending_root,
            )
            for label, payload in mutations.items():
                with self.subTest(mutation=label):
                    write_contract(payload)
                    with self.assertRaises(validator.EvidenceError):
                        validator.validate_formal_build_test_log_binding(
                            manifest_path,
                            marker_path,
                            log_path,
                        )

            write_contract(valid_log)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["unexpected_field"] = "forbidden"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(validator.EvidenceError):
                validator.validate_formal_build_test_log_binding(
                    manifest_path,
                    marker_path,
                    log_path,
                )

            write_contract(valid_log)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("formal_build_tests_status")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(validator.EvidenceError):
                validator.validate_formal_build_test_log_binding(
                    manifest_path,
                    marker_path,
                    log_path,
                )


if __name__ == "__main__":
    unittest.main()
