#!/usr/bin/python3
"""Run the fixed formal-release Python suite from an isolated interpreter."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
FIXED_TEST_MODULES = (
    "tests.test_linux_desktop_packaging_static",
    "tests.test_target_desktop_acceptance_producer",
    "tests.test_github_ci_live_revalidation",
    "tests.test_release_evidence_signer_guards",
    "tests.test_certification_set_v1",
    "tests.test_release_evidence_assembler_v3",
    "tests.test_release_check_v3",
    "tests.test_formal_build_test_evidence_contract",
    "tests.test_release_execution_environment_contract",
    "tests.test_linux_golden_orchestrator",
    "tests.test_golden_source_identity_v4",
)
DESKTOP_EVIDENCE_TEST = (
    ROOT / "tools/taiji-desktop-acceptance/test_assemble_target_evidence.py"
)


def _load_path_tests(loader: unittest.TestLoader, path: Path) -> unittest.TestSuite:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("fixed release test is unavailable: {}".format(path))
    spec = importlib.util.spec_from_file_location(
        "taiji_fixed_desktop_evidence_tests", str(path)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("fixed release test cannot be loaded: {}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)


def build_suite() -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for target in FIXED_TEST_MODULES:
        target_suite = loader.loadTestsFromName(target)
        if target_suite.countTestCases() <= 0:
            raise RuntimeError(
                "fixed release test target collected zero tests: {}".format(target)
            )
        suite.addTests(target_suite)
    path_suite = _load_path_tests(loader, DESKTOP_EVIDENCE_TEST)
    if path_suite.countTestCases() <= 0:
        raise RuntimeError(
            "fixed release test target collected zero tests: {}".format(
                DESKTOP_EVIDENCE_TEST
            )
        )
    suite.addTests(path_suite)
    return suite


def _result_exit_code(result: unittest.TestResult) -> int:
    skipped = list(getattr(result, "skipped", ()))
    if skipped:
        for test, reason in skipped:
            print(
                "taiji-release-python-tests-unexpected-skip\t{}\t{}".format(
                    test,
                    reason,
                ),
                file=sys.stderr,
            )
        return 1
    return 0 if result.wasSuccessful() else 1


def main(argv: Sequence[str] = ()) -> int:
    if argv:
        raise RuntimeError("the formal release test list is source-controlled")
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise RuntimeError("formal release tests require /usr/bin/python3 -I -B")
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    result = unittest.TextTestRunner(verbosity=2).run(build_suite())
    return _result_exit_code(result)


if __name__ == "__main__":
    try:
        raise SystemExit(main(tuple(sys.argv[1:])))
    except RuntimeError as exc:
        print("taiji-release-python-tests-failed\t{}".format(exc), file=sys.stderr)
        raise SystemExit(2)
