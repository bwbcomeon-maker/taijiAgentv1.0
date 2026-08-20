#!/usr/bin/env python3
"""Run the Linux packaging helpers under the Kylin build-host Python baseline."""

from __future__ import annotations

import fcntl
import hashlib
import os
import runpy
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_STAGER = ROOT / "packaging/linux/stage-python-runtime.py"
COMPONENT_STAGER = ROOT / "packaging/linux/stage-runtime-components.py"
PAYLOAD_VERIFIER = ROOT / "packaging/linux/verify-payload.py"
PREINST_RENDERER = ROOT / "packaging/linux/deb/render-preinst.py"
DEPLOYMENT_RECEIPT = ROOT / "packaging/linux/deployment_receipt.py"
UPGRADE_TRANSACTION = ROOT / "packaging/linux/upgrade_transaction.py"
ACCEPTANCE_TOOLS_MANIFEST = ROOT / "packaging/linux/acceptance_tools_manifest.py"
ACCEPTANCE_RUNNER = ROOT / "packaging/linux/acceptance_runner.py"
TARGET_EVIDENCE_ASSEMBLER = (
    ROOT / "tools/taiji-desktop-acceptance/assemble-target-evidence.py"
)
INSTALL_OBSERVER = (
    ROOT / "tools/taiji-desktop-acceptance/observe-single-deb-install.py"
)
GOLDEN_ORCHESTRATOR = ROOT / "scripts/taiji-linux-golden-orchestrator.py"
CANDIDATE_PIPELINE = ROOT / "scripts/taiji-package-candidate.py"
CHALLENGE_ENVELOPE_HELPER = ROOT / "scripts/taiji-challenge-envelope.py"
RELEASE_TEST_RUNNER = ROOT / "scripts/run-taiji-release-python-tests.py"
FORMAL_BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
FORMAL_BUILD_DRIVER = ROOT / "scripts/run-taiji-formal-build-tests.py"
AGENT_PARALLEL_RUNNER = (
    ROOT / "hermes-local-lab/sources/hermes-agent/scripts/run_tests_parallel.py"
)
SINGLE_DEB_PUBLISHER = ROOT / "packaging/linux/deb/publish-single-deb.sh"
RELEASE_EVIDENCE_SIGNER = ROOT / "scripts/sign-taiji-release-evidence.sh"
PUBLICATION_TRUST_HELPER_BEGIN = (
    "# TAIJI_PYTHON38_PUBLICATION_TRUST_HELPER_BEGIN"
)
PUBLICATION_TRUST_HELPER_END = "# TAIJI_PYTHON38_PUBLICATION_TRUST_HELPER_END"
CI_EVIDENCE_PRODUCER = ROOT / "scripts/produce-taiji-github-ci-evidence.py"
PYTHON38_ENTRYPOINTS = (
    ROOT / "packaging/linux/compatibility_policy.py",
    ROOT / "packaging/linux/trusted_system_tools.py",
    ROOT / "packaging/linux/stage-private-libraries.py",
    ROOT / "packaging/linux/audit-elf-closure.py",
    ROOT / "packaging/linux/validate_icon_assets.py",
    ROOT / "packaging/linux/stage-electron-runtime.py",
    ROOT / "packaging/linux/verify-python-lock-contract.py",
    ROOT / "packaging/linux/source-archive-integrity.py",
    ROOT / "packaging/linux/builder-input-package.py",
    PYTHON_STAGER,
    COMPONENT_STAGER,
    PAYLOAD_VERIFIER,
    PREINST_RENDERER,
    DEPLOYMENT_RECEIPT,
    UPGRADE_TRANSACTION,
    ACCEPTANCE_TOOLS_MANIFEST,
    ACCEPTANCE_RUNNER,
    TARGET_EVIDENCE_ASSEMBLER,
    INSTALL_OBSERVER,
    CI_EVIDENCE_PRODUCER,
    ROOT / "scripts/revalidate-taiji-github-ci-evidence.py",
    ROOT / "scripts/produce-taiji-offline-rehearsal.py",
    ROOT / "scripts/produce-taiji-negative-boundary-evidence.py",
    ROOT / "scripts/assemble-taiji-certification-set.py",
    ROOT / "scripts/assemble-taiji-release-evidence.py",
    ROOT / "scripts/validate-taiji-release-evidence.py",
    GOLDEN_ORCHESTRATOR,
    CANDIDATE_PIPELINE,
    CHALLENGE_ENVELOPE_HELPER,
    RELEASE_TEST_RUNNER,
    FORMAL_BUILD_DRIVER,
    AGENT_PARALLEL_RUNNER,
)
PYTHON38_RUNTIME_SOURCES = (
    AGENT_PARALLEL_RUNNER,
    ROOT / "tests/test_linux_desktop_packaging_static.py",
)
CANDIDATE_CORE_GRAMMAR_ENTRYPOINTS = (
    ROOT / "scripts/taiji-package-candidate.py",
    ROOT / "packaging/pipeline/__init__.py",
    ROOT / "packaging/pipeline/cli.py",
    ROOT / "packaging/pipeline/core/__init__.py",
    ROOT / "packaging/pipeline/core/errors.py",
    ROOT / "packaging/pipeline/core/models.py",
    ROOT / "packaging/pipeline/core/orchestration.py",
    ROOT / "packaging/pipeline/core/registry.py",
    ROOT / "packaging/pipeline/core/state.py",
    ROOT / "packaging/pipeline/adapters/__init__.py",
    ROOT / "packaging/pipeline/adapters/base.py",
    ROOT / "packaging/pipeline/adapters/kylin_amd64.py",
)
PYTHON38_GRAMMAR_ENTRYPOINTS = PYTHON38_ENTRYPOINTS + CANDIDATE_CORE_GRAMMAR_ENTRYPOINTS


def extract_single_deb_publisher_python() -> str:
    source = SINGLE_DEB_PUBLISHER.read_text(encoding="utf-8")
    marker = "/usr/bin/python3 -I -B - \"$@\" <<'PY'\n"
    assert source.count(marker) == 1
    embedded = source.split(marker, 1)[1].rsplit("\nPY", 1)[0]
    assert embedded.startswith("from __future__ import annotations\n")
    return embedded


def extract_sealed_snapshot_python() -> str:
    source = FORMAL_BUILDER.read_text(encoding="utf-8")
    marker = "sealed_snapshot_python_source() {\n  /usr/bin/cat <<'PY'\n"
    assert source.count(marker) == 1
    embedded = source.split(marker, 1)[1].split("\nPY\n}", 1)[0]
    assert embedded.startswith("from __future__ import annotations\n")
    return embedded


def exercise_sealed_snapshot_python(temp_root: Path) -> None:
    source = extract_sealed_snapshot_python()
    compile(source, "{}:sealed-snapshot".format(FORMAL_BUILDER), "exec")
    payload = b"canonical sealed snapshot payload\n"
    candidate = temp_root / "sealed-snapshot-input.bin"
    candidate.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            source,
            "create",
            str(candidate),
            expected,
            "0400",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    ready = process.stdout.readline().decode("utf-8").rstrip("\n")
    fields = ready.split("\t")
    if len(fields) != 5 or fields[0] != "READY":
        process.stdin.close()
        process.wait(timeout=10)
        raise AssertionError(
            "sealed snapshot holder did not become ready: {} {}".format(
                ready, process.stderr.read().decode("utf-8", errors="replace")
            )
        )
    holder_descriptor = int(fields[1])
    assert fields[2] == expected
    adopted = os.open(
        "/proc/{}/fd/{}".format(process.pid, holder_descriptor),
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        process.stdin.write(b"A")
        process.stdin.flush()
        process.stdin.close()
        assert process.wait(timeout=10) == 0, process.stderr.read().decode(
            "utf-8", errors="replace"
        )
        with candidate.open("r+b") as handle:
            handle.write(b"X" * len(payload))
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(0)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.lseek(adopted, 0, os.SEEK_SET)
        assert os.read(adopted, len(payload) + 1) == payload
        required = (
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL
        )
        assert fcntl.fcntl(adopted, fcntl.F_GET_SEALS) & required == required
    finally:
        os.close(adopted)


def extract_publication_delivery_trust_helper() -> str:
    source = RELEASE_EVIDENCE_SIGNER.read_text(encoding="utf-8")
    assert source.count(PUBLICATION_TRUST_HELPER_BEGIN) == 1
    assert source.count(PUBLICATION_TRUST_HELPER_END) == 1
    start = source.index(PUBLICATION_TRUST_HELPER_BEGIN)
    start += len(PUBLICATION_TRUST_HELPER_BEGIN)
    end = source.index(PUBLICATION_TRUST_HELPER_END, start)
    helper_source = source[start:end].strip() + "\n"
    assert "def identity(value):" in helper_source
    assert "def validate_publication_delivery_root(" in helper_source
    return helper_source


def exercise_publication_delivery_trust_helper(temp_root: Path) -> None:
    helper_source = extract_publication_delivery_trust_helper()
    namespace = {"os": os, "stat": stat}
    exec(
        compile(
            helper_source,
            "{}:publication-trust-helper".format(RELEASE_EVIDENCE_SIGNER),
            "exec",
        ),
        namespace,
    )
    validate_root = namespace["validate_publication_delivery_root"]

    controlled_parent = temp_root / "publication-trust"
    controlled_parent.mkdir(mode=0o700)
    trusted_root = controlled_parent / "taijiagent 打包交付"
    trusted_root.mkdir(mode=0o700)
    trusted_stat = trusted_root.lstat()
    assert trusted_stat.st_uid == os.getuid()
    assert stat.S_IMODE(trusted_stat.st_mode) == 0o700
    validate_root(trusted_root.resolve())

    unsafe_root = controlled_parent / "unsafe-delivery"
    unsafe_root.mkdir(mode=0o700)
    unsafe_root.chmod(0o777)
    try:
        validate_root(unsafe_root.resolve())
    except SystemExit as exc:
        assert "current-user-owned and not group/other writable" in str(exc)
    else:
        raise AssertionError("publication trust helper accepted unsafe mode")
    finally:
        unsafe_root.chmod(0o700)


def exercise_agent_parallel_runner(agent_runner) -> None:
    fake_test = AGENT_PARALLEL_RUNNER.parents[1] / "tests/test_python38_fake.py"
    observed = {"duration_saves": 0, "pytest_args": None}
    runtime = agent_runner["main"].__globals__
    runtime["_discover_files"] = lambda roots: [fake_test]
    runtime["_count_tests"] = lambda files, repo_root, pytest_args: {
        fake_test: 1
    }

    def fake_run_one_file(file, pytest_args, repo_root, file_timeout):
        observed["pytest_args"] = list(pytest_args)
        return file, 0, "1 passed in 0.01s\n", {"passed": 1}, 0.01

    def forbidden_duration_save(file_times, repo_root):
        observed["duration_saves"] += 1

    runtime["_run_one_file"] = fake_run_one_file
    runtime["_save_durations"] = forbidden_duration_save
    original_argv = sys.argv[:]
    try:
        sys.argv = [
            str(AGENT_PARALLEL_RUNNER),
            "--no-duration-cache",
            "tests/test_python38_fake.py",
            "--",
            "-p",
            "no:cacheprovider",
        ]
        result = runtime["main"]()
    finally:
        sys.argv = original_argv
    assert result == 0
    assert observed["duration_saves"] == 0
    assert observed["pytest_args"] == ["-p", "no:cacheprovider"]


def exercise_formal_agent_parallel_runner(agent_runner, temp_root: Path) -> None:
    formal_root = temp_root / "formal-agent-runner"
    tests_root = formal_root / "tests"
    tests_root.mkdir(parents=True)
    (formal_root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tests_root / "one.py").write_text("def test_one(): pass\n", encoding="utf-8")
    (tests_root / "two.py").write_text("def test_two(): pass\n", encoding="utf-8")
    selectors = ("tests/one.py", "tests/two.py::test_two")
    nodeids = ("tests/one.py::test_one", "tests/two.py::test_two")

    class Item:
        def __init__(self, nodeid):
            self.nodeid = nodeid

    class Report:
        def __init__(self, nodeid):
            self.nodeid = nodeid
            self.when = "call"
            self.skipped = False
            self.passed = True
            self.failed = False

    class FakePytest:
        @staticmethod
        def main(arguments, plugins):
            assert arguments == [
                str((formal_root / "tests/one.py").resolve()),
                str((formal_root / "tests/two.py").resolve()) + "::test_two",
                "--rootdir=" + str(formal_root.resolve()),
                "--confcutdir=" + str(formal_root.resolve()),
                "-c",
                str((formal_root / "pytest.ini").resolve()),
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "pytest_asyncio.plugin",
                "-p",
                "pytest_timeout",
            ]
            assert len(plugins) == 2
            counter = plugins[0]
            for nodeid in nodeids:
                counter.pytest_itemcollected(Item(nodeid))
                counter.pytest_runtest_logstart(nodeid, None)
                counter.pytest_runtest_logreport(Report(nodeid))
            return 0

    run_formal = agent_runner["_run_formal_pytest_session"]
    records = run_formal(formal_root, selectors, 11, FakePytest)
    assert records == (
        {
            "ordinal": 11,
            "collected": 1,
            "deselected": 0,
            "executed": 1,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
        },
        {
            "ordinal": 12,
            "collected": 1,
            "deselected": 0,
            "executed": 1,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
        },
    )


def assert_runtime_sources_avoid_python39_apis() -> None:
    for path in PYTHON38_RUNTIME_SOURCES:
        source = path.read_text(encoding="utf-8")
        for forbidden in (".is_relative_to(", ".removeprefix(", ".removesuffix("):
            assert forbidden not in source, "{} uses {}".format(path, forbidden)


def main() -> int:
    assert sys.version_info[:2] == (3, 8), (
        "this compatibility gate must run on Python 3.8, got {}.{}".format(
            sys.version_info.major, sys.version_info.minor
        )
    )
    with tempfile.TemporaryDirectory(prefix="taiji-python38-gate-") as temp_dir:
        temp_root = Path(temp_dir)
        exercise_publication_delivery_trust_helper(temp_root)
        compile(
            extract_single_deb_publisher_python(),
            "{}:embedded-python".format(SINGLE_DEB_PUBLISHER),
            "exec",
        )
        exercise_sealed_snapshot_python(temp_root)
        for entrypoint in PYTHON38_GRAMMAR_ENTRYPOINTS:
            compile(entrypoint.read_bytes(), str(entrypoint), "exec")

        loaded_entrypoints = {
            entrypoint: runpy.run_path(str(entrypoint))
            for entrypoint in PYTHON38_ENTRYPOINTS
        }
        assert_runtime_sources_avoid_python39_apis()
        agent_runner = loaded_entrypoints[AGENT_PARALLEL_RUNNER]
        exercise_agent_parallel_runner(agent_runner)
        exercise_formal_agent_parallel_runner(agent_runner, temp_root)

        python_stager = loaded_entrypoints[PYTHON_STAGER]
        is_tcl_tk = python_stager["_is_tcl_tk_library_name"]
        assert is_tcl_tk("thread3.1")
        assert not is_tcl_tk("threading.py")

        runtime = temp_root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        site_packages = runtime / "lib/python3.11/site-packages"
        site_packages.mkdir(parents=True)
        python = runtime / "bin/python"
        python.write_bytes(b"\x7fELFpython")
        consumer = site_packages / "native_consumer.so"
        consumer.write_bytes(b"\x7fELFconsumer")
        libpython = runtime / "lib/libpython3.11.so.1.0"
        libpython.write_bytes(b"\x7fELFlibpython")

        inspected = []

        def inspect_needed(path):
            inspected.append(path.relative_to(runtime).as_posix())
            if path == consumer:
                return {"libpython3.11.so.1.0"}
            return {"libc.so.6"}

        prune_libpython = python_stager["prune_unneeded_libpython_stubs"]
        prune_libpython.__globals__["_inspect_elf_needed_libraries"] = inspect_needed
        try:
            prune_libpython(runtime, "3.11")
        except python_stager["PythonRuntimeStageError"] as exc:
            assert "native_consumer.so" in str(exc)
        else:
            raise AssertionError("libpython dependency guard did not fail closed")
        assert libpython.is_file()
        assert set(inspected) == {"bin/python", "lib/python3.11/site-packages/native_consumer.so"}

        component_stager = loaded_entrypoints[COMPONENT_STAGER]
        ignored = component_stager["docx_node_modules_ignored"](
            "/tmp/node_modules/@resvg",
            [
                "resvg-js",
                "resvg-js-linux-x64-gnu",
                "resvg-js-linux-arm64-gnu",
                "resvg-js-darwin-arm64",
            ],
        )
        assert "resvg-js-linux-x64-gnu" not in ignored
        assert "resvg-js-linux-arm64-gnu" in ignored
        assert "resvg-js-darwin-arm64" in ignored

    print("python38-linux-packaging-gate-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
