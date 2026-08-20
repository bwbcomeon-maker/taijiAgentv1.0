"""RED/GREEN contracts for the local-only Windows candidate fake chain."""

import contextlib
import copy
import importlib
import inspect
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from packaging.pipeline.adapters.windows_x64 import WindowsX64Adapter
from packaging.pipeline.core.errors import PipelineError
from packaging.pipeline.core.orchestration import _publish_fetched_outputs
from packaging.pipeline.core.state import RunStateStore


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).with_name("windows_pipeline_fixtures.py")
PUBLIC_FIXTURE_API = (
    "sha256_bytes",
    "canonical_json_bytes",
    "write_regular",
    "make_minimal_amd64_pe",
    "make_windows_plan",
    "make_windows_review",
    "FakeArtifactInspector",
    "FakeWindowsTransport",
)
FULL_BUILD_EVENTS = [
    "online-doctor",
    "create-remote-run",
    "transfer-input",
    "remote-input-verify",
    "remote-candidate-build",
    "fetch-review",
    "fetch-log",
    "local-review-verify",
    "publish",
]


class FixtureControllerRunner:
    """Only the adapter's allowlisted local controller-Git calls are accepted."""

    def __call__(self, argv):
        command = [str(item) for item in argv]
        if command[:3] != ["/usr/bin/git", "-C", command[2]]:
            raise AssertionError("unexpected controller command: {}".format(command))
        if command[3:] == ["status", "--porcelain=v2", "--branch"]:
            stdout = "# branch.oid {}\n# branch.head main\n".format("a" * 40)
        elif command[3:] == ["rev-parse", "HEAD^{commit}"]:
            stdout = "{}\n".format("a" * 40)
        elif command[3:] == ["rev-parse", "HEAD^{tree}"]:
            stdout = "{}\n".format("b" * 40)
        elif command[3:] == ["show", "{}:VERSION".format("a" * 40)]:
            stdout = "1.0.4\n"
        elif command[3:] == ["show", "{}:apps/taiji-desktop/package.json".format("a" * 40)]:
            stdout = '{"name":"taiji-desktop","version":"1.0.4"}\n'
        else:
            raise AssertionError("unexpected controller Git command: {}".format(command))
        return subprocess.CompletedProcess(command, 0, stdout, "")


class ForbiddenExternalRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        raise AssertionError("fake Windows chain attempted an external command")


class EventRecordingWindowsAdapter(WindowsX64Adapter):
    def __init__(self, events, **kwargs):
        super().__init__(**kwargs)
        self.events = events

    def validate_review(self, plan, review, remote_log):
        artifact = super().validate_review(plan, review, remote_log)
        self.events.append("local-review-verify")
        return artifact


class TaijiPackageWindowsTransportTests(unittest.TestCase):
    def load_fixture_api(self):
        self.assertTrue(
            FIXTURE_PATH.is_file(),
            "shared Windows fixture file must exist before the transport tests import it",
        )
        fixtures = importlib.import_module("tests.windows_pipeline_fixtures")
        for name in PUBLIC_FIXTURE_API:
            self.assertTrue(hasattr(fixtures, name), "missing fixture API: {}".format(name))
        self.assertIn(
            "artifact_inspector",
            inspect.signature(WindowsX64Adapter).parameters,
            "Windows adapter must expose the injected artifact inspector seam",
        )
        return fixtures

    def make_adapter(self, fixtures, events, transport):
        return EventRecordingWindowsAdapter(
            events,
            transport_factory=lambda repo, target, ssh_config, command_runner: transport,
            artifact_inspector=fixtures.FakeArtifactInspector(),
            controller_runner=FixtureControllerRunner(),
        )

    def run_build(self, fixtures, root, *, failure_at=None, publisher=None):
        import packaging.pipeline.cli as cli

        plan = fixtures.make_windows_plan(root)
        events = []
        transport = fixtures.FakeWindowsTransport(
            fixtures.make_windows_review,
            failure_at=failure_at,
            events=events,
        )
        adapter = self.make_adapter(fixtures, events, transport)
        external_runner = ForbiddenExternalRunner()
        if publisher is None:
            def publisher(store, run_id, fetched, artifact):
                events.append("publish")
                paths = _publish_fetched_outputs(store, run_id, fetched)
                published = copy.deepcopy(artifact)
                published["path"] = str((Path(paths["review_path"]) / artifact["relative_path"]).resolve())
                return published

        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = cli.main(
                [
                    "--repo", str(Path(root) / "source"),
                    "--target", str(ROOT / "packaging/pipeline/targets/windows-x64.json"),
                    "--state-root", str(Path(root) / "state"),
                    "build",
                ],
                adapter_factory=lambda target_id: adapter,
                command_runner=external_runner,
                input_reader=lambda prompt: "BUILD",
                publisher=publisher,
            )
        state_files = sorted((Path(root) / "state" / "runs").glob("*/run-state.json")) if (Path(root) / "state" / "runs").exists() else []
        state = json.loads(state_files[0].read_text(encoding="utf-8")) if state_files else None
        return result, output.getvalue(), events, state, external_runner, plan, adapter, transport

    def test_fixture_api_is_explicit_and_review_factory_is_shared(self):
        fixtures = self.load_fixture_api()
        self.assertEqual(tuple(fixtures.__all__), PUBLIC_FIXTURE_API)

    def test_full_fake_build_has_exact_order_and_artifact_identity(self):
        fixtures = self.load_fixture_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-fake-success-") as temporary:
            result, output, events, state, external, _plan, _adapter, _transport = self.run_build(
                fixtures, Path(temporary)
            )
            self.assertEqual(result, 0, output)
            self.assertEqual(events, FULL_BUILD_EVENTS)
            self.assertEqual(state["stage"], "CANDIDATE_BUILT")
            self.assertFalse(external.calls)
            artifact = state["artifact"]
            artifact_path = Path(artifact["path"])
            self.assertEqual(
                set(artifact),
                {"kind", "basename", "bytes", "sha256", "path", "relative_path"},
            )
            self.assertEqual(artifact["kind"], "exe")
            self.assertEqual(artifact["basename"], "TaijiAgent-Setup-1.0.4-win-x64.exe")
            self.assertEqual(artifact["relative_path"], artifact["basename"])
            self.assertEqual(artifact["path"], str(artifact_path.resolve()))
            self.assertEqual(artifact["bytes"], artifact_path.stat().st_size)
            self.assertEqual(artifact["sha256"], fixtures.sha256_bytes(artifact_path.read_bytes()))
            self.assertNotIn("install", events)
            self.assertNotIn("interactive-acceptance", events)
            self.assertNotIn("license", events)
            self.assertNotIn("sign", events)
            self.assertNotIn("publish-to-customer", events)

    def test_builder_and_cache_gates_stop_before_remote_run(self):
        fixtures = self.load_fixture_api()
        for failure_at, category in (
            ("builder-unreachable", "BUILDER_UNREACHABLE"),
            ("cache-missing", "WINDOWS_CACHE_MISSING"),
        ):
            with self.subTest(failure_at=failure_at):
                with tempfile.TemporaryDirectory(prefix="taiji-windows-fake-gate-") as temporary:
                    result, output, events, state, _external, _plan, _adapter, _transport = self.run_build(
                        fixtures, Path(temporary), failure_at=failure_at
                    )
                    self.assertEqual(result, 2)
                    self.assertIn(category, output)
                    self.assertEqual(events, ["online-doctor"])
                    self.assertIsNone(state)

    def test_remote_stage_failures_record_their_categories(self):
        fixtures = self.load_fixture_api()
        expected = {
            "input-sha": ("INPUT_VERIFICATION_FAILED", ["online-doctor", "create-remote-run", "transfer-input", "remote-input-verify"]),
            "transfer": ("SCP_INTERRUPTED", ["online-doctor", "create-remote-run", "transfer-input"]),
            "payload": ("WINDOWS_PAYLOAD_FAILED", ["online-doctor", "create-remote-run", "transfer-input", "remote-input-verify", "remote-candidate-build"]),
            "inno": ("WINDOWS_INNO_FAILED", ["online-doctor", "create-remote-run", "transfer-input", "remote-input-verify", "remote-candidate-build"]),
        }
        for failure_at, (category, event_prefix) in expected.items():
            with self.subTest(failure_at=failure_at):
                with tempfile.TemporaryDirectory(prefix="taiji-windows-fake-remote-failure-") as temporary:
                    result, output, events, state, _external, _plan, _adapter, _transport = self.run_build(
                        fixtures, Path(temporary), failure_at=failure_at
                    )
                    self.assertEqual(result, 2)
                    self.assertIn(category, output)
                    self.assertEqual(events, event_prefix)
                    self.assertEqual(state["stage"], "FAILED")
                    self.assertFalse(state["remote_build_succeeded"])

    def test_fetch_review_and_log_failures_are_recoverable(self):
        fixtures = self.load_fixture_api()
        for failure_at, event_prefix in (
            (
                "fetch-review",
                ["online-doctor", "create-remote-run", "transfer-input", "remote-input-verify", "remote-candidate-build", "fetch-review"],
            ),
            (
                "fetch-log",
                ["online-doctor", "create-remote-run", "transfer-input", "remote-input-verify", "remote-candidate-build", "fetch-review", "fetch-log"],
            ),
        ):
            with self.subTest(failure_at=failure_at):
                with tempfile.TemporaryDirectory(prefix="taiji-windows-fake-fetch-failure-") as temporary:
                    result, output, events, state, _external, _plan, _adapter, _transport = self.run_build(
                        fixtures, Path(temporary), failure_at=failure_at
                    )
                    self.assertEqual(result, 2)
                    self.assertIn("SCP_INTERRUPTED", output)
                    self.assertEqual(events, event_prefix)
                    self.assertEqual(state["stage"], "FETCH_PENDING")
                    self.assertTrue(state["remote_build_succeeded"])
                    self.assertTrue(state["fetch_allowed"])

    def test_review_validator_rejects_each_declared_corruption(self):
        fixtures = self.load_fixture_api()
        corruptions = (
            "missing-review-file", "extra-review-file", "review-symlink", "sidecar-sha",
            "manifest-source", "manifest-input", "manifest-payload-sha", "artifact-sha",
            "pe-machine", "pe-optional-magic", "file-version", "product-version",
            "authenticode-status", "remote-state", "marker-sha", "noncanonical-json",
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                with tempfile.TemporaryDirectory(prefix="taiji-windows-review-") as temporary:
                    root = Path(temporary)
                    plan = fixtures.make_windows_plan(root)
                    review, remote_log, inspector = fixtures.make_windows_review(
                        root, plan, corruption=corruption
                    )
                    adapter = EventRecordingWindowsAdapter(
                        [],
                        artifact_inspector=inspector,
                        controller_runner=FixtureControllerRunner(),
                    )
                    with self.assertRaises(PipelineError) as context:
                        adapter.validate_review(plan, review, remote_log)
                    self.assertEqual(context.exception.category, "LOCAL_REVIEW_INVALID")

    def test_valid_review_cross_checks_all_bytes_and_returns_exact_artifact(self):
        fixtures = self.load_fixture_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-review-valid-") as temporary:
            root = Path(temporary)
            plan = fixtures.make_windows_plan(root)
            review, remote_log, inspector = fixtures.make_windows_review(root, plan)
            adapter = EventRecordingWindowsAdapter(
                [],
                artifact_inspector=inspector,
                controller_runner=FixtureControllerRunner(),
            )
            artifact = adapter.validate_review(plan, review, remote_log)
            path = review / artifact["basename"]
            self.assertEqual(
                set(artifact),
                {"kind", "basename", "bytes", "sha256", "path", "relative_path"},
            )
            self.assertEqual(artifact["path"], str(path.resolve()))
            self.assertEqual(artifact["bytes"], path.stat().st_size)
            self.assertEqual(artifact["sha256"], fixtures.sha256_bytes(path.read_bytes()))

    def test_publish_occupied_keeps_existing_bytes_and_pending_state(self):
        fixtures = self.load_fixture_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-publish-occupied-") as temporary:
            root = Path(temporary)
            events = []

            def occupied_publisher(store, run_id, fetched, artifact):
                final_review = store.run_dir(run_id) / "review"
                final_review.mkdir(mode=0o700)
                occupied = final_review / artifact["basename"]
                occupied.write_bytes(b"occupied")
                occupied.chmod(0o600)
                before = occupied.read_bytes()
                with self.assertRaises(PipelineError) as context:
                    _publish_fetched_outputs(store, run_id, fetched)
                self.assertEqual(context.exception.category, "LOCAL_OUTPUT_OCCUPIED")
                self.assertEqual(occupied.read_bytes(), before)
                raise context.exception

            result, output, events, state, _external, _plan, _adapter, _transport = self.run_build(
                fixtures, root, publisher=occupied_publisher
            )
            self.assertEqual(result, 2)
            self.assertIn("LOCAL_OUTPUT_OCCUPIED", output)
            self.assertEqual(state["stage"], "FETCH_PENDING")
            self.assertTrue(state["remote_build_succeeded"])
            self.assertTrue(state["fetch_allowed"])


if __name__ == "__main__":
    unittest.main()
