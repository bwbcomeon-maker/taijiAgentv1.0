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

    def test_production_api_has_no_network_or_clock_override(self):
        self.assertEqual(
            tuple(inspect.signature(self.producer.produce).parameters),
            ("source_commit", "run_id", "output_dir"),
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
