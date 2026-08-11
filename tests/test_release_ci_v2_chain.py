"""RED contracts for the physical trusted GitHub CI v2 release chain."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.github_ci_v2_fixture import write_github_ci_v2_bundle


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"
SOURCE_COMMIT = "a" * 40


def load_validator():
    spec = importlib.util.spec_from_file_location("taiji_ci_v2_chain_test", VALIDATOR)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load release validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseCiV2ChainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = load_validator()
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-ci-v2-chain-")
        self.root = Path(self.temporary.name).resolve()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.evidence = write_github_ci_v2_bundle(
            self.root, SOURCE_COMMIT, now=self.now
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self):
        return self.validator.validate_github_ci_evidence_bundle(
            self.evidence,
            SOURCE_COMMIT,
            now=self.now,
        )

    def mutate_json(self, path: Path, key: str, value) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload[key] = value
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    def test_accepts_exact_fresh_v2_trio_and_returns_all_three_hashes(self) -> None:
        result = self.validate()

        self.assertEqual(result["evidence_basename"], "github-ci-evidence.json")
        self.assertEqual(result["raw_run_basename"], "github-ci-run-response.json")
        self.assertEqual(result["raw_jobs_basename"], "github-ci-jobs-response.json")
        self.assertEqual(result["source_commit"], SOURCE_COMMIT)

    def test_rejects_handwritten_v1_missing_raw_and_raw_hash_mismatch(self) -> None:
        original = json.loads(self.evidence.read_text(encoding="utf-8"))
        cases = (
            ("schema", "taiji-github-ci-evidence/v1"),
            ("raw_run_sha256", "0" * 64),
        )
        for key, value in cases:
            with self.subTest(key=key):
                self.evidence.write_text(
                    json.dumps({**original, key: value}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(self.validator.EvidenceError):
                    self.validate()
        self.evidence.write_text(json.dumps(original, sort_keys=True) + "\n")
        (self.root / "github-ci-jobs-response.json").unlink()
        with self.assertRaises(self.validator.EvidenceError):
            self.validate()

    def test_rejects_wrong_repo_workflow_head_job_step_or_stale_run(self) -> None:
        run_path = self.root / "github-ci-run-response.json"
        jobs_path = self.root / "github-ci-jobs-response.json"
        scenarios = (
            (run_path, "repository", {"full_name": "example/forged"}),
            (run_path, "path", ".github/workflows/other.yml"),
            (run_path, "head_sha", "b" * 40),
        )
        for path, key, value in scenarios:
            with self.subTest(key=key):
                write_github_ci_v2_bundle(self.root, SOURCE_COMMIT, now=self.now)
                self.mutate_json(path, key, value)
                with self.assertRaises(self.validator.EvidenceError):
                    self.validate()
        write_github_ci_v2_bundle(self.root, SOURCE_COMMIT, now=self.now)
        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs["jobs"][0]["name"] = "Forged Gate"
        jobs_path.write_text(json.dumps(jobs, sort_keys=True) + "\n")
        with self.assertRaises(self.validator.EvidenceError):
            self.validate()
        write_github_ci_v2_bundle(self.root, SOURCE_COMMIT, now=self.now)
        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs["jobs"][0]["steps"][1]["name"] = "Forged step"
        jobs_path.write_text(json.dumps(jobs, sort_keys=True) + "\n")
        with self.assertRaises(self.validator.EvidenceError):
            self.validate()
        write_github_ci_v2_bundle(
            self.root,
            SOURCE_COMMIT,
            now=self.now - timedelta(days=8),
        )
        with self.assertRaises(self.validator.EvidenceError):
            self.validate()

    def test_rejects_raw_path_escape_symlink_and_cross_file_toctou(self) -> None:
        self.mutate_json(self.evidence, "raw_run_basename", "../run.json")
        with self.assertRaises(self.validator.EvidenceError):
            self.validate()

        write_github_ci_v2_bundle(self.root, SOURCE_COMMIT, now=self.now)
        run_path = self.root / "github-ci-run-response.json"
        outside = self.root / "outside.json"
        outside.write_bytes(run_path.read_bytes())
        run_path.unlink()
        run_path.symlink_to(outside)
        with self.assertRaises(self.validator.EvidenceError):
            self.validate()

        run_path.unlink()
        write_github_ci_v2_bundle(self.root, SOURCE_COMMIT, now=self.now)
        original_reader = self.validator._read_ci_regular_with_identity
        calls = {"count": 0}

        def mutate_after_read(path, label, maximum):
            result = original_reader(path, label, maximum)
            calls["count"] += 1
            if calls["count"] == 3:
                with (self.root / "github-ci-run-response.json").open("ab") as stream:
                    stream.write(b" ")
            return result

        with patch.object(
            self.validator,
            "_read_ci_regular_with_identity",
            side_effect=mutate_after_read,
        ):
            with self.assertRaises(self.validator.EvidenceError):
                self.validate()


if __name__ == "__main__":
    unittest.main()
