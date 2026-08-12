import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts/run-taiji-formal-build-tests.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("taiji_formal_driver", DRIVER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load formal driver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
