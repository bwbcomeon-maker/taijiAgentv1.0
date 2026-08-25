"""RED contract for moving runtime-dependent tests to the Kylin builder."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePath


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
PREFLIGHT = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
RELEASE_CHECK = ROOT / "scripts/taiji-release-check.sh"
RELEASE_TEST_RUNNER = ROOT / "scripts/run-taiji-release-python-tests.py"
EVIDENCE_VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"
FORMAL_BUILD_DRIVER = ROOT / "scripts/run-taiji-formal-build-tests.py"
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
FORMAL_TARGET_CONTRACT_SHA256 = (
    "5fdcd9335ac9c722b224c06b03d817bd505cff4abc514b09f8d9ba604c11953b"
)








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


def load_formal_build_driver():
    spec = importlib.util.spec_from_file_location(
        "taiji_formal_build_driver_contract",
        FORMAL_BUILD_DRIVER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load direct formal build driver")
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
    driver = load_formal_build_driver()
    lines = [
        "schema=taiji-formal-build-tests/v2",
        "source_commit=" + source_commit,
        "python_version=" + python_version,
        "python_executable_sha256=" + python_sha256,
        "node_version=" + node_version,
        "node_executable_sha256=" + node_sha256,
        "npm_version=" + npm_version,
        "npm_cli_sha256=" + npm_cli_sha256,
        "eslint_cli_sha256=" + ("6" * 64),
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
            for ordinal, record in enumerate(driver.FORMAL_TARGET_REGISTRY)
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
            (root / "pytest.ini").write_text("[pytest]\n")
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
        self.assertEqual(len(observed["plugins"]), 2)
        self.assertEqual(
            observed["argv"][:4],
            (
                str((root / "tests/test_one.py").resolve()),
                str((root / "tests/test_two.py").resolve()) + "::test_exact",
                "--rootdir=" + str(root.resolve()),
                "--confcutdir=" + str(root.resolve()),
            ),
        )
        self.assertEqual(
            observed["argv"][4:6],
            ("-c", str((root / "pytest.ini").resolve())),
        )
        self.assertIn("-p", observed["argv"])
        self.assertIn("pytest_asyncio.plugin", observed["argv"])
        self.assertIn("pytest_timeout", observed["argv"])
        runner_source = AGENT_PARALLEL_RUNNER.read_text(encoding="utf-8")
        self.assertIn('os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"', runner_source)
        session_source = inspect.getsource(runner._run_formal_pytest_session)
        self.assertIn("pytest_module.main", session_source)
        self.assertNotIn("subprocess", session_source)









































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
        driver = FORMAL_BUILD_DRIVER.read_text(encoding="utf-8")
        self.assertIn("--require-nonempty-explicit-files", driver)



    def test_formal_log_is_created_once_and_never_reopened_by_path_for_writes(self):
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn("exec {FORMAL_BUILD_TEST_LOG_FD}>", builder)
        self.assertIn("set -o noclobber", builder)
        self.assertIn("/proc/%s/fd/%s", builder)
        self.assertNotIn(': > "$FORMAL_BUILD_TEST_LOG"', builder)
        self.assertNotIn('>> "$FORMAL_BUILD_TEST_LOG"', builder)
        direct = builder[
            builder.index("run_formal_build_tests_direct() {") :
            builder.index("\n}\n\npending_build_marker_identity_matches", builder.index("run_formal_build_tests_direct() {"))
        ]
        self.assertIn('--log-fd "$FORMAL_BUILD_TEST_LOG_FD"', direct)
        self.assertIn('FORMAL_BUILD_TEST_LOG_READ_FD', builder)
        self.assertIn(
            'require_formal_build_test_log_identity "after direct formal-build-tests/v2"',
            builder,
        )

    def test_formal_manifest_is_published_once_after_tests_without_public_rewrite(self):
        builder = BUILDER.read_text(encoding="utf-8")
        collect_start = builder.index("collect_artifacts() {")
        collect_end = builder.index("\n}\n\nvalidate_formal_test_python_identity", collect_start)
        collect = builder[collect_start:collect_end]
        bind_start = builder.index("bind_formal_build_test_evidence_to_manifest() {")
        bind_end = builder.index("\n}\n\nrun_formal_build_tests_direct", bind_start)
        binding = builder[bind_start:bind_end]
        main = builder[builder.index("main() {") :]

        self.assertNotIn('cp -f "$manifest" "$MANIFEST_FILE"', collect)
        self.assertIn("SOURCE_PACKAGE_MANIFEST_FD", collect)
        self.assertLess(main.index("run_formal_build_tests_direct"), main.index("write_pending_build_marker"))
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
            ("bind_formal_build_test_evidence_to_manifest", "run_formal_build_tests_direct"),
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
        self.assertIn("FORMAL_BUILD_DRIVER", gate)
        self.assertIn("scripts/run-taiji-formal-build-tests.py", gate)
        self.assertIn('agent_runner["main"].__globals__', gate)
        self.assertIn('runtime["main"]()', gate)
        for source_path in (AGENT_PARALLEL_RUNNER, LINUX_PACKAGING_TESTS):
            source = source_path.read_text(encoding="utf-8")
            for forbidden in (".is_relative_to(", ".removeprefix(", ".removesuffix("):
                self.assertNotIn(forbidden, source, "{} uses {}".format(source_path, forbidden))


    def test_builder_runs_all_runtime_dependent_tests_with_verified_tools(self):
        builder = BUILDER.read_text(encoding="utf-8")
        main = builder[builder.rindex("\nmain() {") + 1 :]
        formal = builder[
            builder.index("prepare_formal_build_test_dependencies() {") :
            builder.index("pending_build_marker_identity_matches() {")
        ]

        for required in (
            "run_formal_build_tests_direct",
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
            main.index("run_formal_build_tests_direct"),
        )
        self.assertLess(
            main.index("run_formal_build_tests_direct"),
            main.index("write_pending_build_marker"),
        )

        driver = FORMAL_BUILD_DRIVER.read_text(encoding="utf-8")
        for suite in (
            "root-runtime",
            "desktop-evidence-node",
            "kylin-install-simulation",
            "agent",
            "webui-runtime-lint",
            "webui-python",
        ):
            self.assertTrue(
                suite in driver,
                "the direct driver is missing formal suite {!r}".format(suite),
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
            self.assertIn(test_path, driver)

        for required in (
            "readlink -f \"$agent_python\"",
            "readlink -f \"$PYTHON_BIN\"",
            "PYTHON_PINNED_EXECUTABLE_SHA256",
            "validate_formal_test_python_identity",
            "manifest_identity",
        ):
            self.assertIn(required, builder)

    def test_builder_prepares_formal_dependencies_after_artifacts_before_driver(self):
        builder = BUILDER.read_text(encoding="utf-8")
        main = builder[builder.rindex("\nmain() {") + 1 :]

        self.assertEqual(main.count("prepare_formal_build_test_dependencies"), 1)
        self.assertLess(
            main.index("collect_artifacts"),
            main.index("prepare_formal_build_test_dependencies"),
        )
        self.assertLess(
            main.index("prepare_formal_build_test_dependencies"),
            main.index("run_formal_build_tests_direct"),
        )
        self.assertLess(
            main.index("run_formal_build_tests_direct"),
            main.index("write_pending_build_marker"),
        )

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
            fake_python_launcher = root / "formal-python-launcher"
            fake_python_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_python_launcher.chmod(0o755)
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
                        'FORMAL_PYTHON_LAUNCHER_PATH="{}"'.format(fake_python_launcher),
                        'FORMAL_PYTHON_LAUNCHER_HELD_PATH="$FORMAL_PYTHON_LAUNCHER_PATH"',
                        'FORMAL_ESLINT_FD=""',
                        'FORMAL_NODE_IDENTITY=node-id',
                        'FORMAL_NODE_SHA256=node-sha',
                        'FORMAL_NPM_CLI_IDENTITY=npm-id',
                        'FORMAL_NPM_CLI_SHA256=npm-sha',
                        'FORMAL_PYTHON_IDENTITY=python-id',
                        'FORMAL_PYTHON_SHA256=python-sha',
                        'FORMAL_PYTHON_LAUNCHER_IDENTITY=python-launcher-id',
                        'FORMAL_PYTHON_LAUNCHER_SHA256=python-launcher-sha',
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
                        '    "$FORMAL_PYTHON_LAUNCHER_PATH") printf "python-launcher-id\\tpython-launcher-sha\\n" ;;',
                        '    *) return 1 ;;',
                        '  esac',
                        '}',
                        'run_held_node_script() { printf "10.9.8\\n"; }',
                        'run_held_python() {',
                        '  [ "$#" -eq 6 ] || return 1',
                        '  [ "$1" = "$FORMAL_PYTHON_PATH" ] || return 1',
                        '  [ "$2" = "$FORMAL_PYTHON_LAUNCHER_HELD_PATH" ] || return 1',
                        '  [ "$3" = -I ] || return 1',
                        '  [ "$4" = -B ] || return 1',
                        '  [ "$5" = -c ] || return 1',
                        "  [ \"$6\" = 'import platform, pytest; print(platform.python_version())' ] || return 1",
                        '  printf "3.11.15\\n"',
                        '}',
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
            "FORMAL_PYTHON_LAUNCHER_PATH",
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
        self.assertIn("--no-duration-cache", FORMAL_BUILD_DRIVER.read_text(encoding="utf-8"))
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
            "retired-supervisor-header": valid_log.replace(
                "source_commit=" + source_commit + "\n",
                "source_commit=" + source_commit + "\n"
                + "supervisor_source_sha256=" + ("7" * 64) + "\n",
                1,
            ),
            "retired-supervisor-closure": valid_log.replace(
                "eslint_cli_sha256=" + ("6" * 64) + "\n",
                "eslint_cli_sha256=" + ("6" * 64) + "\n"
                + "closure_sha256=" + ("8" * 64) + "\n"
                + "closure_file_count=321\n"
                + "closure_total_bytes=654321\n",
                1,
            ).replace(
                "source_commit=" + source_commit + "\n",
                "source_commit=" + source_commit + "\n"
                + "supervisor_source_sha256=" + ("7" * 64) + "\n",
                1,
            ),
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
