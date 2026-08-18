"""RED contracts for mandatory live GitHub CI v2 revalidation."""

from __future__ import annotations

import importlib.util
import inspect
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.github_ci_v2_fixture import (
    REPOSITORY,
    RUN_ATTEMPT,
    RUN_ID,
    write_github_ci_v2_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/revalidate-taiji-github-ci-evidence.py"
SOURCE_COMMIT = "a" * 40
NOW = datetime(2026, 8, 11, 10, 30, 0, tzinfo=timezone.utc)
RUN_URL = f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{RUN_ID}"
JOBS_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{RUN_ID}"
    f"/attempts/{RUN_ATTEMPT}/jobs?per_page=100"
)


def load_revalidator():
    if not SCRIPT.is_file():
        raise AssertionError(f"live CI revalidator is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("taiji_ci_live_revalidation_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load live CI revalidator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        return self.responses[url]


class GitHubCiLiveRevalidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-ci-live-")
        self.root = Path(self.temporary.name).resolve()
        self.evidence = write_github_ci_v2_bundle(
            self.root, SOURCE_COMMIT, now=NOW
        )
        self.revalidator = load_revalidator()
        self.run_payload = (self.root / "github-ci-run-response.json").read_bytes()
        self.jobs_payload = (self.root / "github-ci-jobs-response.json").read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call(self, api: FakeApi):
        with patch.object(
            self.revalidator, "_github_fetch_bytes", side_effect=api
        ), patch.object(self.revalidator, "_utc_now", return_value=NOW):
            return self.revalidator.live_revalidate(
                self.evidence, SOURCE_COMMIT
            )

    def api(self, *, run=None, jobs=None):
        return FakeApi(
            {
                RUN_URL: (run or self.run_payload, RUN_URL),
                JOBS_URL: (jobs or self.jobs_payload, JOBS_URL),
            }
        )

    def test_exact_live_run_and_jobs_bytes_revalidate_fixed_identity(self) -> None:
        api = self.api()

        result = self.call(api)

        self.assertEqual(api.calls, [RUN_URL, JOBS_URL])
        self.assertEqual(result["source_commit"], SOURCE_COMMIT)
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["run_attempt"], RUN_ATTEMPT)

    def test_internally_consistent_handcrafted_trio_cannot_replace_live_api(self) -> None:
        forged_root = self.root / "forged"
        forged = write_github_ci_v2_bundle(
            forged_root, SOURCE_COMMIT, now=NOW
        )
        forged_run = json.loads(
            (forged_root / "github-ci-run-response.json").read_text()
        )
        forged_run["workflow_id"] += 1
        run_payload = (json.dumps(forged_run, sort_keys=True) + "\n").encode()
        (forged_root / "github-ci-run-response.json").write_bytes(run_payload)
        normalized = json.loads(forged.read_text())
        import hashlib

        normalized["workflow_id"] = forged_run["workflow_id"]
        normalized["raw_run_sha256"] = hashlib.sha256(run_payload).hexdigest()
        forged.write_text(json.dumps(normalized, sort_keys=True) + "\n")

        with patch.object(
            self.revalidator, "_github_fetch_bytes", side_effect=self.api()
        ), patch.object(self.revalidator, "_utc_now", return_value=NOW):
            with self.assertRaises(self.revalidator.LiveCiRevalidationError):
                self.revalidator.live_revalidate(forged, SOURCE_COMMIT)

    def test_semantically_equal_but_byte_different_live_response_is_rejected(self) -> None:
        run = json.loads(self.run_payload)
        reformatted = json.dumps(run, indent=2).encode("utf-8")

        with self.assertRaises(self.revalidator.LiveCiRevalidationError):
            self.call(self.api(run=reformatted))

    def test_redirect_or_local_mutation_during_fetch_is_rejected(self) -> None:
        redirected = self.api()
        redirected.responses[RUN_URL] = (
            self.run_payload,
            "https://example.invalid/forged",
        )
        with self.assertRaises(self.revalidator.LiveCiRevalidationError):
            self.call(redirected)

        def mutate_on_jobs(url):
            if url == JOBS_URL:
                with (self.root / "github-ci-jobs-response.json").open("ab") as stream:
                    stream.write(b" ")
                return self.jobs_payload, JOBS_URL
            return self.run_payload, RUN_URL

        with patch.object(
            self.revalidator,
            "_github_fetch_bytes",
            side_effect=mutate_on_jobs,
        ), patch.object(self.revalidator, "_utc_now", return_value=NOW):
            with self.assertRaises(self.revalidator.LiveCiRevalidationError):
                self.revalidator.live_revalidate(self.evidence, SOURCE_COMMIT)

    def test_production_api_has_no_repo_api_time_or_skip_override(self) -> None:
        self.assertEqual(
            list(inspect.signature(self.revalidator.live_revalidate).parameters),
            ["evidence_path", "source_commit"],
        )
        parser = self.revalidator.build_parser()
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertEqual(
            option_strings,
            {"-h", "--help", "--evidence", "--source-commit"},
        )
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "TAIJI_CI_SKIP",
            "TAIJI_CI_API_ORIGIN",
            "TAIJI_CI_REPOSITORY",
            "TAIJI_CI_NOW",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
