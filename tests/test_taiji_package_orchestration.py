"""RED contracts for the platform-neutral candidate orchestration order."""

import contextlib
import importlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tests.taiji_package_fixtures import (
    ForbiddenExternalRunner,
    RecordingAdapter,
    RecordingPublisher,
    complete_target,
    complete_v1_fetch_pending,
    complete_v2_payload,
    write_secure_v1_state,
)


def load_cli():
    try:
        return importlib.import_module("packaging.pipeline.cli")
    except (ImportError, AttributeError) as exc:
        raise AssertionError("common candidate CLI is not implemented: {}".format(exc))


def write_target(root):
    path = Path(root) / "target.json"
    path.write_text(
        json.dumps(complete_target(), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def run_build(root, adapter, events, input_reader):
    cli = load_cli()
    main = getattr(cli, "main", None)
    if not callable(main):
        raise AssertionError("common candidate CLI main is missing")
    target = write_target(root)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = main(
            [
                "--repo", str(Path(root) / "repo"),
                "--target", str(target),
                "--state-root", str(Path(root) / "state"),
                "build",
            ],
            adapter_factory=lambda target_id: adapter,
            command_runner=ForbiddenExternalRunner(),
            input_reader=input_reader,
            publisher=RecordingPublisher(events),
        )
    return result, output.getvalue()


def run_fetch(root, adapter, events, *, target=None, repo=None):
    cli = load_cli()
    if isinstance(target, (str, Path)):
        target_path = Path(target)
    else:
        target_path = write_target(root) if target else None
    argv = [
        "--repo", str(repo or (Path(root) / "cli-repo")),
        "--state-root", str(Path(root) / "state"),
    ]
    if target_path is not None:
        argv.extend(["--target", str(target_path)])
    argv.extend(["fetch", "--run", "run-1"])
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = cli.main(
            argv,
            adapter_factory=lambda target_id: adapter,
            command_runner=ForbiddenExternalRunner(),
            input_reader=lambda prompt: "BUILD",
            publisher=RecordingPublisher(events),
        )
    return result, output.getvalue()


def seed_v2_fetch_pending(root):
    state_module = importlib.import_module("packaging.pipeline.core.state")
    store = state_module.RunStateStore(Path(root) / "state")
    store.create(
        "run-1",
        complete_v2_payload(
            root,
            run_id="run-1",
            stage="FETCH_PENDING",
            remote_build_succeeded=True,
            fetch_allowed=True,
        ),
    )
    return store


class TaijiPackageOrchestrationTests(unittest.TestCase):
    def test_reusable_build_has_exact_stage_order(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-reusable-") as temporary:
            root = Path(temporary)
            events = []
            adapter = RecordingAdapter(root, events, input_status="REUSABLE")
            result, output = run_build(root, adapter, events, lambda prompt: "BUILD")

            self.assertEqual(result, 0, output)
            self.assertEqual(
                events,
                [
                    "validate_target", "local_doctor", "build_plan", "create_transport",
                    "online_doctor", "bind_online_plan", "initial_state_patch", "inspect_input",
                    "create_remote_run", "transfer_input", "verify_remote_input",
                    "build_remote_candidate", "fetch-review", "fetch-log",
                    "validate_review", "publish", "success_state_patch",
                ],
            )
            store = importlib.import_module("packaging.pipeline.core.state").RunStateStore(
                root / "state"
            )
            state = store.load("run-1")
            self.assertEqual(state["stage"], "CANDIDATE_BUILT")
            self.assertEqual(
                [item["stage"] for item in state["stage_history"]][-4:],
                [
                    "REMOTE_BUILD_SUCCEEDED", "REVIEW_FETCHED",
                    "LOCAL_REVIEW_VERIFIED", "CANDIDATE_BUILT",
                ],
            )

    def test_missing_build_prepares_only_after_online_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-missing-") as temporary:
            root = Path(temporary)
            events = []
            adapter = RecordingAdapter(root, events, input_status="MISSING")
            reads = []

            def input_reader(prompt):
                reads.append(prompt)
                return "BUILD"

            result, output = run_build(root, adapter, events, input_reader)

            self.assertEqual(result, 0, output)
            self.assertEqual(len(reads), 1)
            self.assertEqual(
                events,
                [
                    "validate_target", "local_doctor", "build_plan", "create_transport",
                    "online_doctor", "bind_online_plan", "initial_state_patch", "prepare_input",
                    "inspect_input", "create_remote_run", "transfer_input",
                    "verify_remote_input", "build_remote_candidate", "fetch-review", "fetch-log",
                    "validate_review", "publish", "success_state_patch",
                ],
            )
            self.assertTrue((root / "state/runs/run-1/run-state.json").is_file())

    def test_unreachable_build_stops_before_confirmation_state_and_input(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-unreachable-") as temporary:
            root = Path(temporary)
            events = []
            adapter = RecordingAdapter(
                root, events, input_status="MISSING", builder_status="BUILDER_UNREACHABLE"
            )
            reads = []
            result, output = run_build(
                root, adapter, events, lambda prompt: reads.append(prompt) or "BUILD"
            )

            self.assertEqual(result, 2)
            self.assertIn("BUILDER_UNREACHABLE", output)
            self.assertEqual(
                events,
                [
                    "validate_target", "local_doctor", "build_plan", "create_transport",
                    "online_doctor",
                ],
            )
            self.assertEqual(reads, [])
            self.assertFalse((root / "state").exists())

    def test_v2_fetch_without_target_uses_frozen_target(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-v2-") as temporary:
            root = Path(temporary)
            events = []
            store = seed_v2_fetch_pending(root)
            adapter = RecordingAdapter(root, events)
            result, output = run_fetch(root, adapter, events)

            self.assertEqual(result, 0, output)
            self.assertEqual(
                events,
                ["create_transport", "fetch-review", "fetch-log", "validate_review", "publish", "success_state_patch"],
            )
            self.assertEqual(store.load("run-1")["stage"], "CANDIDATE_BUILT")

    def test_v2_fetch_with_matching_explicit_target_succeeds(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-explicit-") as temporary:
            root = Path(temporary)
            events = []
            seed_v2_fetch_pending(root)
            adapter = RecordingAdapter(root, events)
            result, output = run_fetch(root, adapter, events, target=True)

            self.assertEqual(result, 0, output)
            self.assertEqual(events[0], "validate_target")
            self.assertEqual(
                events[1:],
                ["create_transport", "fetch-review", "fetch-log", "validate_review", "publish", "success_state_patch"],
            )

    def test_explicit_fetch_rejects_non_id_target_drift_before_transport(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-drift-") as temporary:
            root = Path(temporary)
            events = []
            seed_v2_fetch_pending(root)
            adapter = RecordingAdapter(root, events)
            target_path = write_target(root)
            payload = json.loads(target_path.read_text(encoding="utf-8"))
            payload["host_alias"] = "other-builder"
            target_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            result, output = run_fetch(root, adapter, events, target=target_path)

            self.assertEqual(result, 2)
            self.assertIn("PLAN_INVALID", output)
            self.assertEqual(events, ["validate_target"])

    def test_explicit_fetch_rejects_target_id_drift_before_transport(self):
        class PermissiveRecordingAdapter(RecordingAdapter):
            def validate_target(self, payload):
                self.events.append("validate_target")
                return deepcopy(payload)

        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-id-drift-") as temporary:
            root = Path(temporary)
            events = []
            seed_v2_fetch_pending(root)
            adapter = PermissiveRecordingAdapter(root, events)
            target_path = write_target(root)
            payload = json.loads(target_path.read_text(encoding="utf-8"))
            payload["target_id"] = "windows-x64"
            target_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            result, output = run_fetch(root, adapter, events, target=target_path)

            self.assertEqual(result, 2)
            self.assertIn("PLAN_INVALID", output)
            self.assertEqual(events, ["validate_target"])

    def test_fetch_uses_frozen_repo_root_not_cli_repo(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-repo-") as temporary:
            root = Path(temporary)
            events = []
            seed_v2_fetch_pending(root)
            adapter = RecordingAdapter(root, events)
            result, output = run_fetch(root, adapter, events, repo=root / "another-repo")

            self.assertEqual(result, 0, output)
            self.assertEqual(adapter.transport_repo, str((root / "repo").resolve()))

    def test_v1_fetch_uses_only_kylin_normalizer_and_preserves_file_schema(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-v1-") as temporary:
            root = Path(temporary)
            events = []
            state_module = importlib.import_module("packaging.pipeline.core.state")
            store = state_module.RunStateStore(root / "state")
            write_secure_v1_state(
                root / "state", "run-1", complete_v1_fetch_pending(root, run_id="run-1")
            )
            adapter = RecordingAdapter(root, events)
            result, output = run_fetch(root, adapter, events)

            self.assertEqual(result, 0, output)
            self.assertEqual(
                events,
                ["normalize_legacy_state", "create_transport", "fetch-review", "fetch-log", "validate_review", "publish", "success_state_patch"],
            )
            self.assertEqual(store.load("run-1")["schema"], "taiji-package-run-state/v1")

    def test_fetch_pending_never_repeats_online_prepare_or_build(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-only-") as temporary:
            root = Path(temporary)
            events = []
            seed_v2_fetch_pending(root)
            adapter = RecordingAdapter(root, events)
            result, output = run_fetch(root, adapter, events)

            self.assertEqual(result, 0, output)
            for forbidden in (
                "online_doctor", "prepare_input", "inspect_input", "bind_online_plan",
                "create_remote_run", "transfer_input", "verify_remote_input", "build_remote_candidate",
            ):
                self.assertNotIn(forbidden, events)

    def test_non_fetch_pending_is_rejected_before_adapter_factory(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-reject-") as temporary:
            root = Path(temporary)
            state_module = importlib.import_module("packaging.pipeline.core.state")
            store = state_module.RunStateStore(root / "state")
            store.create("run-1", complete_v2_payload(root, run_id="run-1"))
            events = []

            def forbidden_factory(target_id):
                raise AssertionError("adapter factory must not run")

            cli = load_cli()
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result = cli.main(
                    ["--state-root", str(root / "state"), "fetch", "--run", "run-1"],
                    adapter_factory=forbidden_factory,
                    command_runner=ForbiddenExternalRunner(),
                    input_reader=lambda prompt: "BUILD",
                    publisher=RecordingPublisher(events),
                )
            self.assertEqual(result, 2)
            self.assertIn("FETCH_NOT_ALLOWED", output.getvalue())
            self.assertEqual(events, [])

    def test_status_never_resolves_implicit_or_explicit_target(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-status-") as temporary:
            root = Path(temporary)
            state_module = importlib.import_module("packaging.pipeline.core.state")
            store = state_module.RunStateStore(root / "state")
            store.create("run-1", complete_v2_payload(root, run_id="run-1"))

            def forbidden_factory(target_id):
                raise AssertionError("status must not create an adapter")

            cli = load_cli()
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result = cli.main(
                    [
                        "--target", str(root / "missing-target.json"),
                        "--state-root", str(root / "state"),
                        "status", "--run", "run-1",
                    ],
                    adapter_factory=forbidden_factory,
                    command_runner=ForbiddenExternalRunner(),
                    input_reader=lambda prompt: "BUILD",
                    publisher=RecordingPublisher([]),
                )
            self.assertEqual(result, 0, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["run_id"], "run-1")

    def test_publish_is_identity_idempotent_and_rejects_changed_existing_output(self):
        with tempfile.TemporaryDirectory(prefix="taiji-orchestration-publish-") as temporary:
            root = Path(temporary)
            state_module = importlib.import_module("packaging.pipeline.core.state")
            orchestration = importlib.import_module("packaging.pipeline.core.orchestration")
            store = state_module.RunStateStore(root / "state")
            store.create("run-1", complete_v2_payload(root, run_id="run-1"))

            def staging(name, payload=b"candidate"):
                directory = store.run_dir("run-1") / name
                review = directory / "review"
                review.mkdir(parents=True, mode=0o700)
                (review / "candidate.bin").write_bytes(payload)
                (review / "candidate.bin").chmod(0o600)
                log = directory / "remote-build.log"
                log.write_text("log\n", encoding="utf-8")
                log.chmod(0o600)
                return {"review_path": str(review), "remote_log_path": str(log)}

            first = orchestration._publish_fetched_outputs(store, "run-1", staging("first"))
            self.assertTrue(Path(first["review_path"]).is_dir())
            second = orchestration._publish_fetched_outputs(store, "run-1", staging("second"))
            self.assertEqual(second, first)

            changed = staging("changed", payload=b"different")
            pipeline_error = importlib.import_module("packaging.pipeline.core.errors").PipelineError
            with self.assertRaises(pipeline_error) as context:
                orchestration._publish_fetched_outputs(store, "run-1", changed)
            self.assertEqual(context.exception.category, "LOCAL_OUTPUT_OCCUPIED")

    def test_fetch_failures_remain_recoverable_without_rebuild(self):
        from packaging.pipeline.core.errors import PipelineError

        class FailingFetchTransport:
            def __init__(self, events):
                self.events = events

            def fetch(self, plan, staging_dir):
                del plan, staging_dir
                self.events.append("fetch-review")
                raise PipelineError("fetch log interrupted", category="SCP_INTERRUPTED")

        class FailingReviewAdapter(RecordingAdapter):
            def validate_review(self, plan, review, remote_log):
                del plan, review, remote_log
                self.events.append("validate_review")
                raise PipelineError("review invalid", category="LOCAL_REVIEW_INVALID")

        class FailingPublisher:
            def __call__(self, store, run_id, fetched, artifact):
                del store, run_id, fetched, artifact
                raise PipelineError("publish failed", category="LOCAL_PUBLISH_FAILED")

        for failure_kind in ("fetch", "review", "publish"):
            with self.subTest(failure_kind=failure_kind):
                with tempfile.TemporaryDirectory(prefix="taiji-orchestration-fetch-failure-") as temporary:
                    root = Path(temporary)
                    events = []
                    store = seed_v2_fetch_pending(root)
                    adapter = (
                        FailingReviewAdapter(root, events)
                        if failure_kind == "review" else RecordingAdapter(root, events)
                    )
                    transport = (
                        FailingFetchTransport(events)
                        if failure_kind == "fetch" else adapter.transport
                    )
                    publisher = (
                        FailingPublisher()
                        if failure_kind == "publish" else RecordingPublisher(events)
                    )
                    orchestration = importlib.import_module("packaging.pipeline.core.orchestration")
                    with self.assertRaises(PipelineError):
                        orchestration.execute_fetch(
                            store.load("run-1"),
                            store.load("run-1")["plan"],
                            adapter,
                            transport,
                            store,
                            publisher=publisher,
                        )
                    pending = store.load("run-1")
                    self.assertEqual(pending["stage"], "FETCH_PENDING")
                    self.assertTrue(pending["fetch_allowed"])
                    self.assertTrue(pending["remote_build_succeeded"])


if __name__ == "__main__":
    unittest.main()
