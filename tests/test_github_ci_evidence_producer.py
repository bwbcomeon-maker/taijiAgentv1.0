"""TDD contracts for the trusted GitHub Actions CI evidence producer."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/produce-taiji-github-ci-evidence.py"
SOURCE_COMMIT = "a" * 40
RUN_ID = 123456789
RUN_ATTEMPT = 2
NOW = datetime(2026, 8, 11, 10, 30, 0, tzinfo=timezone.utc)
REPOSITORY = "bwbcomeon-maker/taijiAgentv1.0"
RUN_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{RUN_ID}"
)
JOBS_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{RUN_ID}"
    f"/attempts/{RUN_ATTEMPT}/jobs?per_page=100"
)


def load_producer():
    if not SCRIPT.is_file():
        raise AssertionError(f"trusted GitHub CI producer is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location(
        "taiji_github_ci_evidence_producer_test", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load producer: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def strict_json(path: Path):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AssertionError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)


class FakeGitHubApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected GitHub API URL: {url}")
        return self.responses[url], url


class GitHubCiEvidenceProducerTests(unittest.TestCase):
    def setUp(self):
        self.producer = load_producer()
        self.temporary = tempfile.TemporaryDirectory(
            prefix="taiji-github-ci-evidence-"
        )
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "ci-evidence"
        self.run = {
            "id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "workflow_id": 778899,
            "name": "Pull Request CI",
            "path": ".github/workflows/ci.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_sha": SOURCE_COMMIT,
            "head_branch": "main",
            "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}",
            "created_at": "2026-08-11T10:00:00Z",
            "updated_at": "2026-08-11T10:20:00Z",
            "repository": {"full_name": REPOSITORY},
            "head_repository": {"full_name": REPOSITORY},
        }
        self.job = {
            "id": 987654321,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "workflow_name": "Pull Request CI",
            "name": "CI Gate",
            "head_sha": SOURCE_COMMIT,
            "status": "completed",
            "conclusion": "success",
            "html_url": (
                f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}"
                "/job/987654321"
            ),
            "started_at": "2026-08-11T10:19:00Z",
            "completed_at": "2026-08-11T10:20:00Z",
            "steps": [
                {
                    "name": "Set up job",
                    "status": "completed",
                    "conclusion": "success",
                    "number": 1,
                },
                {
                    "name": "Require every selected job to pass",
                    "status": "completed",
                    "conclusion": "success",
                    "number": 2,
                },
                {
                    "name": "Complete job",
                    "status": "completed",
                    "conclusion": "success",
                    "number": 3,
                },
            ],
        }

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def encoded(value):
        return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")

    def api(self):
        return FakeGitHubApi(
            {
                RUN_URL: self.encoded(self.run),
                JOBS_URL: self.encoded({"total_count": 1, "jobs": [self.job]}),
            }
        )

    def call_producer(
        self,
        *,
        source_commit=SOURCE_COMMIT,
        run_id=RUN_ID,
        api=None,
        output=None,
    ):
        fake_api = api or self.api()
        with patch.object(
            self.producer, "_github_fetch_bytes", side_effect=fake_api
        ), patch.object(self.producer, "_utc_now", return_value=NOW):
            return self.producer.produce(
                source_commit=source_commit,
                run_id=run_id,
                output_dir=output or self.output,
            )

    def call_delivery_producer(
        self,
        delivery: Path,
        *,
        source_commit=SOURCE_COMMIT,
        run_id=RUN_ID,
        api=None,
    ):
        fake_api = api or self.api()
        with patch.object(
            self.producer, "_github_fetch_bytes", side_effect=fake_api
        ), patch.object(self.producer, "_utc_now", return_value=NOW):
            return self.producer.produce(
                source_commit=source_commit,
                run_id=run_id,
                output_dir=None,
                delivery_dir=delivery,
            )

    def produce(self, *, api=None, output=None):
        return self.call_producer(api=api, output=output)

    def assert_rejected(self, *, api=None):
        with self.assertRaises(self.producer.GitHubCiEvidenceError):
            self.produce(api=api)
        self.assertFalse(self.output.exists())

    def test_successful_fixed_main_push_ci_gate_produces_v2_and_two_raw_responses(self):
        api = self.api()

        result = self.produce(api=api)

        self.assertEqual(result, self.output / "github-ci-evidence.json")
        self.assertEqual(api.calls, [RUN_URL, JOBS_URL])
        self.assertEqual(
            {item.name for item in self.output.iterdir()},
            {
                "github-ci-evidence.json",
                "github-ci-run-response.json",
                "github-ci-jobs-response.json",
            },
        )
        self.assertEqual(
            (self.output / "github-ci-run-response.json").read_bytes(),
            self.encoded(self.run),
        )
        jobs_payload = self.encoded({"total_count": 1, "jobs": [self.job]})
        self.assertEqual(
            (self.output / "github-ci-jobs-response.json").read_bytes(),
            jobs_payload,
        )
        evidence = strict_json(result)
        self.assertEqual(
            set(evidence),
            {
                "schema",
                "provider",
                "api_version",
                "repository",
                "workflow_id",
                "workflow_name",
                "workflow_path",
                "event",
                "head_branch",
                "head_sha",
                "run_id",
                "run_attempt",
                "run_status",
                "run_conclusion",
                "run_html_url",
                "run_created_at_utc",
                "run_updated_at_utc",
                "required_job_id",
                "required_job_name",
                "required_job_status",
                "required_job_conclusion",
                "required_job_html_url",
                "required_job_started_at_utc",
                "required_job_completed_at_utc",
                "required_step_name",
                "required_step_status",
                "required_step_conclusion",
                "collected_at_utc",
                "raw_run_basename",
                "raw_run_sha256",
                "raw_jobs_basename",
                "raw_jobs_sha256",
            },
        )
        self.assertEqual(evidence["schema"], "taiji-github-ci-evidence/v2")
        self.assertEqual(evidence["provider"], "github-actions-rest-api")
        self.assertEqual(evidence["repository"], REPOSITORY)
        self.assertEqual(evidence["workflow_path"], ".github/workflows/ci.yml")
        self.assertEqual(evidence["event"], "push")
        self.assertEqual(evidence["head_branch"], "main")
        self.assertEqual(evidence["head_sha"], SOURCE_COMMIT)
        self.assertEqual(evidence["required_job_name"], "CI Gate")
        self.assertEqual(
            evidence["required_step_name"], "Require every selected job to pass"
        )
        self.assertEqual(
            evidence["raw_run_sha256"],
            hashlib.sha256(self.encoded(self.run)).hexdigest(),
        )
        self.assertEqual(
            evidence["raw_jobs_sha256"], hashlib.sha256(jobs_payload).hexdigest()
        )

    def test_fixed_repository_workflow_push_main_and_commit_cannot_be_substituted(self):
        original_run = deepcopy(self.run)
        mutations = (
            ("repository", {"full_name": "example/taiji-agent"}),
            ("head_repository", {"full_name": "example/taiji-agent"}),
            ("name", "Another workflow"),
            ("path", ".github/workflows/other.yml"),
            ("event", "pull_request"),
            ("event", "workflow_dispatch"),
            ("head_branch", "release"),
            ("head_sha", "b" * 40),
            ("status", "in_progress"),
            ("conclusion", "failure"),
            (
                "html_url",
                f"https://github.com/example/taiji-agent/actions/runs/{RUN_ID}",
            ),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                self.run = deepcopy(original_run)
                self.run[key] = value
                self.assert_rejected()
                self.output = self.root / f"ci-evidence-{key}-{len(str(value))}"

    def test_ci_gate_job_and_required_step_must_be_unique_bound_and_successful(self):
        job_mutations = (
            ("run_id", RUN_ID + 1),
            ("run_attempt", RUN_ATTEMPT + 1),
            ("workflow_name", "Other workflow"),
            ("head_sha", "b" * 40),
            ("status", "in_progress"),
            ("conclusion", "failure"),
            ("name", "Not CI Gate"),
        )
        for key, value in job_mutations:
            with self.subTest(key=key, value=value):
                original = deepcopy(self.job)
                self.job[key] = value
                self.assert_rejected()
                self.job = original
                self.output = self.root / f"ci-evidence-job-{key}"

        original_steps = deepcopy(self.job["steps"])
        for steps in (
            [
                item
                for item in original_steps
                if item["name"] != "Require every selected job to pass"
            ],
            original_steps
            + [
                {
                    "name": "Require every selected job to pass",
                    "status": "completed",
                    "conclusion": "success",
                    "number": 4,
                }
            ],
            [
                {
                    **item,
                    "conclusion": (
                        "failure"
                        if item["name"] == "Require every selected job to pass"
                        else item["conclusion"]
                    ),
                }
                for item in original_steps
            ],
        ):
            with self.subTest(steps=steps):
                self.job["steps"] = steps
                self.assert_rejected()
                self.output = self.root / (
                    "ci-evidence-step-" + hashlib.sha256(repr(steps).encode()).hexdigest()[:8]
                )
        self.job["steps"] = original_steps

        duplicate = deepcopy(self.job)
        jobs_payload = {"total_count": 2, "jobs": [self.job, duplicate]}
        api = FakeGitHubApi(
            {
                RUN_URL: self.encoded(self.run),
                JOBS_URL: self.encoded(jobs_payload),
            }
        )
        self.assert_rejected(api=api)

    def test_incomplete_paginated_or_malformed_jobs_response_fails_closed(self):
        payloads = (
            {"total_count": 2, "jobs": [self.job]},
            {"total_count": 101, "jobs": [self.job]},
            {"total_count": "1", "jobs": [self.job]},
            {"total_count": 1, "jobs": "not-a-list"},
        )
        for index, payload in enumerate(payloads):
            with self.subTest(payload=payload):
                api = FakeGitHubApi(
                    {
                        RUN_URL: self.encoded(self.run),
                        JOBS_URL: self.encoded(payload),
                    }
                )
                self.assert_rejected(api=api)
                self.output = self.root / f"ci-evidence-jobs-shape-{index}"

    def test_api_timeout_redirect_oversize_or_non_json_fails_without_output(self):
        def timeout(_url):
            raise TimeoutError("secret-token-must-not-escape")

        def redirected(url):
            self.assertEqual(url, RUN_URL)
            return (
                self.encoded(self.run),
                "https://example.invalid/forged-response",
            )

        cases = (
            timeout,
            redirected,
            FakeGitHubApi(
                {RUN_URL: (b"x" * (self.producer.MAX_RUN_BYTES + 1))}
            ),
            FakeGitHubApi({RUN_URL: b"not-json\n"}),
            FakeGitHubApi({RUN_URL: b'{"id":1,"id":2}\n'}),
        )
        for index, api in enumerate(cases):
            with self.subTest(index=index):
                self.assert_rejected(api=api)
                self.output = self.root / f"ci-evidence-api-{index}"

    def test_real_http_client_pins_origin_headers_timeout_and_fails_closed(self):
        producer = self.producer
        payload = self.encoded(self.run)

        class FakeResponse:
            status = 200
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return RUN_URL

            def read(self, _maximum):
                return payload

        class SuccessfulOpener:
            def __init__(self):
                self.request = None
                self.timeout = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        opener = SuccessfulOpener()
        with patch.object(producer, "build_opener", return_value=opener), patch.dict(
            producer.os.environ,
            {"GITHUB_TOKEN": "test-token-must-not-appear-in-output"},
            clear=True,
        ):
            actual, final_url = producer._github_fetch_bytes(RUN_URL)

        self.assertEqual(actual, payload)
        self.assertEqual(final_url, RUN_URL)
        self.assertEqual(opener.request.full_url, RUN_URL)
        self.assertEqual(opener.request.method, "GET")
        self.assertEqual(
            opener.request.get_header("X-github-api-version"),
            producer.API_VERSION,
        )
        self.assertEqual(
            opener.request.get_header("Authorization"),
            "Bearer test-token-must-not-appear-in-output",
        )
        self.assertEqual(opener.timeout, producer.HTTP_TIMEOUT_SECONDS)

        class FailingOpener:
            def __init__(self, exception):
                self.exception = exception

            def open(self, _request, timeout):
                self.timeout = timeout
                raise self.exception

        failures = (
            producer.HTTPError(RUN_URL, 302, "redirect", {}, None),
            producer.HTTPError(RUN_URL, 403, "forbidden", {}, None),
            producer.HTTPError(RUN_URL, 429, "rate limited", {}, None),
            producer.URLError("network unavailable"),
            TimeoutError("network timeout"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(
                    producer,
                    "build_opener",
                    return_value=FailingOpener(failure),
                ), patch.dict(
                    producer.os.environ,
                    {"GITHUB_TOKEN": "test-token-must-not-escape"},
                    clear=True,
                ):
                    with self.assertRaises(
                        producer.GitHubCiEvidenceError
                    ) as caught:
                        producer._github_fetch_bytes(RUN_URL)
                self.assertNotIn("test-token", str(caught.exception))

        with self.assertRaises(producer.GitHubCiEvidenceError):
            producer._github_fetch_bytes(
                "https://example.invalid/repos/forged/actions/runs/1"
            )

    def test_completed_ci_run_must_be_recent(self):
        self.run["created_at"] = "2026-07-01T10:00:00Z"
        self.run["updated_at"] = "2026-07-01T10:20:00Z"
        self.job["started_at"] = "2026-07-01T10:19:00Z"
        self.job["completed_at"] = "2026-07-01T10:20:00Z"

        self.assert_rejected()

    def test_output_write_failure_removes_the_owned_partial_directory(self):
        original_write = self.producer._write_new

        def fail_after_write(path, payload):
            original_write(path, payload)
            if path.name == "github-ci-jobs-response.json":
                raise OSError("simulated durable write failure")

        with patch.object(
            self.producer, "_write_new", side_effect=fail_after_write
        ):
            with self.assertRaises(OSError):
                self.produce()

        self.assertFalse(self.output.exists())

    def test_existing_delivery_mode_promotes_exact_trio_without_staging_residue(self):
        delivery = self.root / "review-delivery"
        delivery.mkdir(mode=0o700)

        result = self.call_delivery_producer(delivery)

        self.assertEqual(result, delivery / "github-ci-evidence.json")
        self.assertEqual(
            {item.name for item in delivery.iterdir()},
            {
                "github-ci-evidence.json",
                "github-ci-run-response.json",
                "github-ci-jobs-response.json",
            },
        )
        for path in delivery.iterdir():
            metadata = path.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)

    def test_delivery_mode_rejects_relative_symlink_and_writable_directory_before_network(self):
        real_delivery = self.root / "real-delivery"
        real_delivery.mkdir(mode=0o700)
        linked_delivery = self.root / "linked-delivery"
        linked_delivery.symlink_to(real_delivery, target_is_directory=True)
        writable_delivery = self.root / "writable-delivery"
        writable_delivery.mkdir(mode=0o770)
        writable_delivery.chmod(0o770)

        for delivery in (
            Path("relative-delivery"),
            linked_delivery,
            writable_delivery,
        ):
            with self.subTest(delivery=delivery):
                api = self.api()
                with self.assertRaises(self.producer.GitHubCiEvidenceError):
                    self.call_delivery_producer(delivery, api=api)
                self.assertEqual(api.calls, [])
        self.assertEqual(list(real_delivery.iterdir()), [])
        self.assertEqual(list(writable_delivery.iterdir()), [])

    def test_delivery_mode_rejects_untrusted_world_writable_ancestor_before_network(self):
        for mode in (0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                unsafe_ancestor = self.root / "unsafe-ancestor-{:o}".format(mode)
                unsafe_ancestor.mkdir(mode=0o700)
                unsafe_ancestor.chmod(mode)
                delivery = unsafe_ancestor / "review-delivery"
                delivery.mkdir(mode=0o700)
                api = self.api()

                with self.assertRaises(self.producer.GitHubCiEvidenceError):
                    self.call_delivery_producer(delivery, api=api)

                self.assertEqual(api.calls, [])
                self.assertEqual(list(delivery.iterdir()), [])

    def test_delivery_mode_allows_root_owned_exact_sticky_tmp_ancestor(self):
        with tempfile.TemporaryDirectory(
            prefix="taiji-github-ci-sticky-",
            dir="/tmp",
        ) as temporary:
            sticky_metadata = Path("/tmp").resolve().lstat()
            self.assertEqual(sticky_metadata.st_uid, 0)
            self.assertEqual(stat.S_IMODE(sticky_metadata.st_mode), 0o1777)
            delivery = Path(temporary).resolve() / "review-delivery"
            delivery.mkdir(mode=0o700)

            result = self.call_delivery_producer(delivery)

            self.assertEqual(result, delivery / "github-ci-evidence.json")
            self.assertEqual(
                {item.name for item in delivery.iterdir()},
                {
                    "github-ci-evidence.json",
                    "github-ci-run-response.json",
                    "github-ci-jobs-response.json",
                },
            )

    def test_delivery_mode_rejects_preexisting_regular_or_symlink_without_touching_it(self):
        for basename in (
            "github-ci-run-response.json",
            "github-ci-jobs-response.json",
            "github-ci-evidence.json",
        ):
            for kind in ("regular", "symlink"):
                with self.subTest(basename=basename, kind=kind):
                    delivery = self.root / f"preexisting-{basename}-{kind}"
                    delivery.mkdir(mode=0o700)
                    destination = delivery / basename
                    if kind == "regular":
                        destination.write_bytes(b"preexisting-must-survive")
                    else:
                        target = self.root / f"outside-{basename}-{kind}"
                        target.write_bytes(b"outside-must-survive")
                        destination.symlink_to(target)
                    before = destination.lstat()
                    api = self.api()

                    with self.assertRaises(self.producer.GitHubCiEvidenceError):
                        self.call_delivery_producer(delivery, api=api)

                    after = destination.lstat()
                    self.assertEqual(
                        (before.st_dev, before.st_ino, before.st_mode),
                        (after.st_dev, after.st_ino, after.st_mode),
                    )
                    if kind == "regular":
                        self.assertEqual(destination.read_bytes(), b"preexisting-must-survive")
                    else:
                        self.assertTrue(destination.is_symlink())
                        self.assertEqual(destination.resolve().read_bytes(), b"outside-must-survive")
                    self.assertEqual(api.calls, [])
                    self.assertFalse(
                        any(item.name.startswith(".taiji-github-ci-evidence.") for item in delivery.iterdir())
                    )

    def test_delivery_directory_swap_during_fetch_fails_closed_and_cleans_owned_staging(self):
        delivery = self.root / "delivery-to-swap"
        delivery.mkdir(mode=0o700)
        moved = self.root / "moved-original-delivery"
        base_api = self.api()

        def swapping_api(url):
            if not base_api.calls:
                delivery.rename(moved)
                delivery.mkdir(mode=0o700)
            return base_api(url)

        with self.assertRaises(self.producer.GitHubCiEvidenceError):
            self.call_delivery_producer(delivery, api=swapping_api)

        expected_names = {
            "github-ci-run-response.json",
            "github-ci-jobs-response.json",
            "github-ci-evidence.json",
        }
        self.assertTrue(expected_names.isdisjoint({item.name for item in delivery.iterdir()}))
        self.assertTrue(expected_names.isdisjoint({item.name for item in moved.iterdir()}))
        self.assertFalse(
            any(item.name.startswith(".taiji-github-ci-evidence.") for item in moved.iterdir())
        )

    def test_delivery_directory_swap_during_final_promotion_rolls_back_original_directory(self):
        delivery = self.root / "delivery-to-swap-late"
        delivery.mkdir(mode=0o700)
        moved = self.root / "moved-late-delivery"
        original_promote = self.producer._promote_staged_file

        def swap_after_last_promotion(*args, **kwargs):
            result = original_promote(*args, **kwargs)
            if args[2] == "github-ci-evidence.json":
                delivery.rename(moved)
                delivery.mkdir(mode=0o700)
            return result

        with patch.object(
            self.producer,
            "_promote_staged_file",
            side_effect=swap_after_last_promotion,
        ):
            with self.assertRaises(self.producer.GitHubCiEvidenceError):
                self.call_delivery_producer(delivery)

        expected_names = {
            "github-ci-run-response.json",
            "github-ci-jobs-response.json",
            "github-ci-evidence.json",
        }
        self.assertTrue(expected_names.isdisjoint({item.name for item in delivery.iterdir()}))
        self.assertTrue(expected_names.isdisjoint({item.name for item in moved.iterdir()}))
        self.assertFalse(
            any(item.name.startswith(".taiji-github-ci-evidence.") for item in moved.iterdir())
        )

    def test_delivery_directory_swap_during_staging_cleanup_fails_closed_and_rolls_back(self):
        delivery = self.root / "delivery-to-swap-during-cleanup"
        delivery.mkdir(mode=0o700)
        moved = self.root / "moved-cleanup-delivery"
        original_cleanup = self.producer._remove_staging_directory
        swapped = False

        def swap_before_cleanup(*args, **kwargs):
            nonlocal swapped
            if not swapped and args[1] is not None:
                delivery.rename(moved)
                delivery.mkdir(mode=0o700)
                swapped = True
            return original_cleanup(*args, **kwargs)

        with patch.object(
            self.producer,
            "_remove_staging_directory",
            side_effect=swap_before_cleanup,
        ):
            with self.assertRaises(self.producer.GitHubCiEvidenceError):
                self.call_delivery_producer(delivery)

        expected_names = {
            "github-ci-run-response.json",
            "github-ci-jobs-response.json",
            "github-ci-evidence.json",
        }
        self.assertTrue(expected_names.isdisjoint({item.name for item in delivery.iterdir()}))
        self.assertTrue(expected_names.isdisjoint({item.name for item in moved.iterdir()}))
        self.assertFalse(
            any(item.name.startswith(".taiji-github-ci-evidence.") for item in moved.iterdir())
        )

    def test_promotion_fstat_failure_after_exclusive_create_rolls_back_created_destination(self):
        delivery = self.root / "promotion-fstat-failure"
        staging = self.root / "promotion-fstat-staging"
        delivery.mkdir(mode=0o700)
        staging.mkdir(mode=0o700)
        staged_file = staging / "github-ci-run-response.json"
        staged_file.write_bytes(b"staged-payload")
        staged_file.chmod(0o600)
        directory_flags = (
            self.producer.os.O_RDONLY
            | getattr(self.producer.os, "O_DIRECTORY", 0)
            | getattr(self.producer.os, "O_CLOEXEC", 0)
            | getattr(self.producer.os, "O_NOFOLLOW", 0)
        )
        delivery_descriptor = self.producer.os.open(delivery, directory_flags)
        staging_descriptor = self.producer.os.open(staging, directory_flags)
        original_open = self.producer.os.open
        original_fstat = self.producer.os.fstat
        promoted_descriptors = set()
        injected = False

        def track_promoted_open(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            if (
                path == "github-ci-run-response.json"
                and kwargs.get("dir_fd") == delivery_descriptor
            ):
                promoted_descriptors.add(descriptor)
            return descriptor

        def fail_first_promoted_fstat(descriptor):
            nonlocal injected
            if not injected and descriptor in promoted_descriptors:
                injected = True
                raise OSError("simulated post-create fstat failure")
            return original_fstat(descriptor)

        try:
            with patch.object(
                self.producer.os,
                "open",
                side_effect=track_promoted_open,
            ), patch.object(
                self.producer.os,
                "fstat",
                side_effect=fail_first_promoted_fstat,
            ):
                with self.assertRaises(OSError):
                    self.producer._promote_staged_file(
                        staging_descriptor,
                        delivery_descriptor,
                        "github-ci-run-response.json",
                        [],
                    )
        finally:
            self.producer.os.close(staging_descriptor)
            self.producer.os.close(delivery_descriptor)

        self.assertTrue(injected)
        self.assertEqual(list(delivery.iterdir()), [])

    def test_promotion_registration_failure_after_exclusive_create_rolls_back_destination(self):
        delivery = self.root / "promotion-registration-failure"
        staging = self.root / "promotion-registration-staging"
        delivery.mkdir(mode=0o700)
        staging.mkdir(mode=0o700)
        staged_file = staging / "github-ci-run-response.json"
        staged_file.write_bytes(b"staged-payload")
        staged_file.chmod(0o600)
        directory_flags = (
            self.producer.os.O_RDONLY
            | getattr(self.producer.os, "O_DIRECTORY", 0)
            | getattr(self.producer.os, "O_CLOEXEC", 0)
            | getattr(self.producer.os, "O_NOFOLLOW", 0)
        )
        delivery_descriptor = self.producer.os.open(delivery, directory_flags)
        staging_descriptor = self.producer.os.open(staging, directory_flags)

        class FailingRegistration(list):
            def append(self, _record):
                raise OSError("simulated ownership registration failure")

        try:
            with self.assertRaises(OSError):
                self.producer._promote_staged_file(
                    staging_descriptor,
                    delivery_descriptor,
                    "github-ci-run-response.json",
                    FailingRegistration(),
                )
        finally:
            self.producer.os.close(staging_descriptor)
            self.producer.os.close(delivery_descriptor)

        self.assertEqual(list(delivery.iterdir()), [])

    def test_staging_cleanup_does_not_unlink_concurrently_replaced_file(self):
        delivery = self.root / "staging-replacement-cleanup"
        delivery.mkdir(mode=0o700)
        original_cleanup = self.producer._remove_staging_directory
        replacement_identity = None

        def replace_before_cleanup(*args, **kwargs):
            nonlocal replacement_identity
            if replacement_identity is None and args[1] is not None:
                replacement = delivery / args[1] / "github-ci-run-response.json"
                replacement.unlink()
                replacement.write_bytes(b"concurrent-replacement-must-survive")
                replacement.chmod(0o600)
                metadata = replacement.lstat()
                replacement_identity = (metadata.st_dev, metadata.st_ino)
            return original_cleanup(*args, **kwargs)

        with patch.object(
            self.producer,
            "_remove_staging_directory",
            side_effect=replace_before_cleanup,
        ):
            with self.assertRaises(
                self.producer.GitHubCiEvidenceError
            ) as caught:
                self.call_delivery_producer(delivery)

        self.assertIn("rollback could not remove", str(caught.exception))

        staging = [
            item
            for item in delivery.iterdir()
            if item.name.startswith(".taiji-github-ci-evidence.")
        ]
        self.assertEqual(len(staging), 1)
        replacement = staging[0] / "github-ci-run-response.json"
        metadata = replacement.lstat()
        self.assertEqual((metadata.st_dev, metadata.st_ino), replacement_identity)
        self.assertEqual(replacement.read_bytes(), b"concurrent-replacement-must-survive")
        self.assertTrue(
            {
                "github-ci-run-response.json",
                "github-ci-jobs-response.json",
                "github-ci-evidence.json",
            }.isdisjoint({item.name for item in delivery.iterdir()})
        )

    def test_transient_staging_unlink_failure_is_retried_without_residue(self):
        delivery = self.root / "staging-unlink-retry"
        delivery.mkdir(mode=0o700)
        original_unlink = self.producer.os.unlink
        injected = False
        failed_directory_descriptor = None
        staging_attempts = 0

        def fail_first_staging_unlink(path, *args, **kwargs):
            nonlocal injected, failed_directory_descriptor, staging_attempts
            if not injected and path == "github-ci-run-response.json":
                injected = True
                failed_directory_descriptor = kwargs.get("dir_fd")
                staging_attempts += 1
                raise OSError("simulated transient staging unlink failure")
            if (
                path == "github-ci-run-response.json"
                and kwargs.get("dir_fd") == failed_directory_descriptor
            ):
                staging_attempts += 1
            return original_unlink(path, *args, **kwargs)

        with patch.object(
            self.producer.os,
            "unlink",
            side_effect=fail_first_staging_unlink,
        ):
            with self.assertRaises(
                self.producer.GitHubCiEvidenceError
            ) as caught:
                self.call_delivery_producer(delivery)

        self.assertTrue(injected)
        self.assertEqual(staging_attempts, 2)
        self.assertIn("staging cleanup failed", str(caught.exception))
        self.assertEqual(list(delivery.iterdir()), [])

    def test_staging_directory_open_rejects_inode_replacement_without_deleting_replacement(self):
        delivery = self.root / "staging-directory-replacement"
        delivery.mkdir(mode=0o700)
        original_open = self.producer.os.open
        moved = self.root / "moved-created-staging"
        replacement = None

        def replace_before_open(path, flags, *args, **kwargs):
            nonlocal replacement
            if (
                replacement is None
                and isinstance(path, str)
                and path.startswith(".taiji-github-ci-evidence.")
            ):
                created = delivery / path
                created.rename(moved)
                replacement = delivery / path
                replacement.mkdir(mode=0o700)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(
            self.producer.os,
            "open",
            side_effect=replace_before_open,
        ):
            with self.assertRaises(self.producer.GitHubCiEvidenceError):
                self.call_delivery_producer(delivery)

        self.assertIsNotNone(replacement)
        self.assertTrue(replacement.is_dir())
        self.assertEqual(list(replacement.iterdir()), [])
        self.assertTrue(moved.is_dir())
        self.assertEqual(list(moved.iterdir()), [])

    def test_staging_directory_must_share_delivery_filesystem(self):
        delivery = self.root / "staging-cross-device"
        delivery.mkdir(mode=0o700)
        original_open = self.producer.os.open
        original_fstat = self.producer.os.fstat
        staging_descriptors = set()

        class CrossDeviceMetadata:
            def __init__(self, metadata):
                self._metadata = metadata
                self.st_dev = metadata.st_dev + 1

            def __getattr__(self, name):
                return getattr(self._metadata, name)

        def track_staging_open(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            if (
                isinstance(path, str)
                and path.startswith(".taiji-github-ci-evidence.")
            ):
                staging_descriptors.add(descriptor)
            return descriptor

        def report_cross_device(descriptor):
            metadata = original_fstat(descriptor)
            if descriptor in staging_descriptors:
                return CrossDeviceMetadata(metadata)
            return metadata

        with patch.object(
            self.producer.os,
            "open",
            side_effect=track_staging_open,
        ), patch.object(
            self.producer.os,
            "fstat",
            side_effect=report_cross_device,
        ):
            with self.assertRaises(self.producer.GitHubCiEvidenceError):
                self.call_delivery_producer(delivery)

        self.assertEqual(list(delivery.iterdir()), [])

    def test_second_or_third_delivery_promotion_failure_rolls_back_only_owned_nodes(self):
        for failing_basename in (
            "github-ci-jobs-response.json",
            "github-ci-evidence.json",
        ):
            with self.subTest(failing_basename=failing_basename):
                delivery = self.root / f"partial-{failing_basename}"
                delivery.mkdir(mode=0o700)
                sentinel = delivery / "keep-me.txt"
                sentinel.write_bytes(b"preexisting")
                original_promote = self.producer._promote_staged_file

                def fail_after_promotion(*args, **kwargs):
                    result = original_promote(*args, **kwargs)
                    basename = args[2]
                    if basename == failing_basename:
                        raise OSError("simulated promotion failure")
                    return result

                with patch.object(
                    self.producer,
                    "_promote_staged_file",
                    side_effect=fail_after_promotion,
                ):
                    with self.assertRaises(OSError):
                        self.call_delivery_producer(delivery)

                self.assertEqual(sentinel.read_bytes(), b"preexisting")
                self.assertEqual({item.name for item in delivery.iterdir()}, {sentinel.name})

    def test_output_is_private_new_and_cli_has_no_trust_target_override(self):
        result = self.produce()
        self.assertEqual(stat.S_IMODE(self.output.lstat().st_mode), 0o700)
        for child in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(child.lstat().st_mode), 0o600)

        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.producer.parse_args(
                    [
                        "--source-commit",
                        SOURCE_COMMIT,
                        "--run-id",
                        str(RUN_ID),
                        "--output-dir",
                        str(self.root / "other"),
                        "--repository",
                        "example/taiji-agent",
                    ]
                )
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "--repository",
            "--api-base-url",
            "--skip-online",
            "--offline",
            "GITHUB_REPOSITORY",
            "GH_HOST",
        ):
            self.assertNotIn(forbidden, source)

        with self.assertRaises(self.producer.GitHubCiEvidenceError):
            self.produce()

    def test_cli_requires_exactly_one_output_mode(self):
        output = str(self.root / "new-output")
        delivery = str(self.root / "existing-delivery")
        base = ["--source-commit", SOURCE_COMMIT, "--run-id", str(RUN_ID)]

        parsed_output = self.producer.parse_args(base + ["--output-dir", output])
        self.assertEqual(parsed_output.output_dir, Path(output))
        self.assertIsNone(parsed_output.delivery_dir)
        parsed_delivery = self.producer.parse_args(base + ["--delivery-dir", delivery])
        self.assertEqual(parsed_delivery.delivery_dir, Path(delivery))
        self.assertIsNone(parsed_delivery.output_dir)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.producer.parse_args(base)
            with self.assertRaises(SystemExit):
                self.producer.parse_args(
                    base
                    + [
                        "--output-dir",
                        output,
                        "--delivery-dir",
                        delivery,
                    ]
                )

    def test_production_api_has_no_network_or_clock_override(self):
        self.assertEqual(
            tuple(inspect.signature(self.producer.produce).parameters),
            ("source_commit", "run_id", "output_dir", "delivery_dir"),
        )

    def test_invalid_identity_inputs_fail_before_any_api_request(self):
        api = self.api()
        for commit, run_id in (
            ("A" * 40, RUN_ID),
            ("a" * 39, RUN_ID),
            (SOURCE_COMMIT, 0),
            (SOURCE_COMMIT, True),
        ):
            with self.subTest(commit=commit, run_id=run_id):
                with self.assertRaises(self.producer.GitHubCiEvidenceError):
                    self.call_producer(
                        source_commit=commit,
                        run_id=run_id,
                        output=self.root / (
                            "invalid-" + hashlib.sha256(repr((commit, run_id)).encode()).hexdigest()[:8]
                        ),
                        api=api,
                    )
        self.assertEqual(api.calls, [])


if __name__ == "__main__":
    unittest.main()
