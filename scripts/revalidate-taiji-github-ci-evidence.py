#!/usr/bin/env python3
"""Re-fetch fixed GitHub CI run/jobs and revalidate the local v2 trio."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PRODUCER_PATH = ROOT / "scripts/produce-taiji-github-ci-evidence.py"
VALIDATOR_PATH = ROOT / "scripts/validate-taiji-release-evidence.py"


class LiveCiRevalidationError(ValueError):
    """Raised when live GitHub cannot prove the exact local CI bundle."""


def _load_module(path: Path, name: str) -> Any:
    if not path.is_file() or path.is_symlink():
        raise LiveCiRevalidationError(f"source-controlled helper is unavailable: {path.name}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LiveCiRevalidationError(f"cannot load source-controlled helper: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_PRODUCER = _load_module(PRODUCER_PATH, "taiji_ci_live_producer_contract")
_VALIDATOR = _load_module(VALIDATOR_PATH, "taiji_ci_live_release_validator")


def _github_fetch_bytes(url: str):
    """Production-only fixed GitHub fetch seam; tests may monkeypatch it."""

    return _PRODUCER._github_fetch_bytes(url)


def _utc_now() -> datetime:
    """Production clock seam; tests may monkeypatch it without a CLI override."""

    return datetime.now(timezone.utc)


def _read_local_bundle(evidence_path: Path) -> tuple[bytes, bytes, bytes]:
    parent = evidence_path.parent
    evidence, _ = _VALIDATOR.read_regular_bytes(
        evidence_path,
        "GitHub CI normalized evidence",
        limit=_VALIDATOR.MAX_JSON_BYTES,
    )
    run, _ = _VALIDATOR.read_regular_bytes(
        parent / _VALIDATOR.CI_RAW_RUN_BASENAME,
        "GitHub CI raw run response",
        limit=_VALIDATOR.CI_MAX_RUN_BYTES,
    )
    jobs, _ = _VALIDATOR.read_regular_bytes(
        parent / _VALIDATOR.CI_RAW_JOBS_BASENAME,
        "GitHub CI raw jobs response",
        limit=_VALIDATOR.CI_MAX_JOBS_BYTES,
    )
    return evidence, run, jobs


def live_revalidate(evidence_path: Path, source_commit: str) -> dict[str, Any]:
    """Live-revalidate a physical v2 trio against immutable GitHub endpoints.

    This production API intentionally accepts only the local evidence path and
    frozen source commit. Repository, API origin, workflow, branch, job, step,
    time source, response limits and freshness are source-controlled constants.
    """

    evidence_path = Path(evidence_path)
    now = _utc_now()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise LiveCiRevalidationError("live CI clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    try:
        before = _VALIDATOR.validate_github_ci_evidence_bundle(
            evidence_path,
            source_commit,
            now=now,
        )
        local_evidence, local_run, local_jobs = _read_local_bundle(evidence_path)
        normalized = _VALIDATOR.parse_json_bytes(
            local_evidence, "GitHub CI normalized evidence"
        )
        run_id = normalized["run_id"]
        run_attempt = normalized["run_attempt"]
        run_url = _PRODUCER._expected_run_url(run_id)
        jobs_url = _PRODUCER._expected_jobs_url(run_id, run_attempt)
        live_run = _PRODUCER._fetch_exact(
            _github_fetch_bytes,
            run_url,
            _PRODUCER.MAX_RUN_BYTES,
            "GitHub workflow run live revalidation",
        )
        live_jobs = _PRODUCER._fetch_exact(
            _github_fetch_bytes,
            jobs_url,
            _PRODUCER.MAX_JOBS_BYTES,
            "GitHub workflow jobs live revalidation",
        )
        run = _PRODUCER._parse_json(live_run, "live GitHub workflow run")
        run_contract = _PRODUCER._validate_run(run, source_commit, run_id, now)
        jobs = _PRODUCER._parse_json(live_jobs, "live GitHub workflow jobs")
        _PRODUCER._validate_jobs(
            jobs,
            source_commit,
            run_id,
            run_contract["run_attempt"],
            now,
        )
        if run_contract["run_attempt"] != run_attempt:
            raise LiveCiRevalidationError(
                "live GitHub run_attempt differs from normalized evidence"
            )
        if live_run != local_run or live_jobs != local_jobs:
            raise LiveCiRevalidationError(
                "live GitHub raw responses differ byte-for-byte from the local trio"
            )
        after = _VALIDATOR.validate_github_ci_evidence_bundle(
            evidence_path,
            source_commit,
            now=now,
        )
        after_evidence, after_run, after_jobs = _read_local_bundle(evidence_path)
        if (
            before != after
            or local_evidence != after_evidence
            or local_run != after_run
            or local_jobs != after_jobs
        ):
            raise LiveCiRevalidationError(
                "local GitHub CI trio changed during live revalidation"
            )
    except LiveCiRevalidationError:
        raise
    except Exception as exc:
        raise LiveCiRevalidationError(f"live GitHub CI revalidation failed: {exc}") from exc
    return {
        "source_commit": source_commit,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "evidence_sha256": before["evidence_sha256"],
        "raw_run_sha256": before["raw_run_sha256"],
        "raw_jobs_sha256": before["raw_jobs_sha256"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = live_revalidate(args.evidence, args.source_commit)
    except (LiveCiRevalidationError, OSError, TypeError, ValueError) as exc:
        print(f"github-ci-live-revalidation-failed\t{exc}", file=sys.stderr)
        return 1
    print(
        "github-ci-live-revalidation-valid\t"
        f"{result['source_commit']}\t{result['run_id']}\t{result['run_attempt']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
