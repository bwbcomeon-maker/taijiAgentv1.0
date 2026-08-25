"""Deterministic physical GitHub CI v2 fixture helpers for release tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPOSITORY = "bwbcomeon-maker/taijiAgentv1.0"
RUN_ID = 123456789
RUN_ATTEMPT = 2
JOB_ID = 987654321


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


def write_github_ci_v2_bundle(
    root: Path,
    source_commit: str,
    *,
    now: datetime | None = None,
) -> Path:
    """Write the exact producer v2 trio and return normalized evidence path."""

    current = now or datetime.now(timezone.utc)
    created = current - timedelta(minutes=30)
    completed = current - timedelta(minutes=10)
    run = {
        "id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "workflow_id": 778899,
        "name": "Main Validation",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": source_commit,
        "head_branch": "main",
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}",
        "created_at": _utc(created),
        "updated_at": _utc(completed),
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
    }
    job = {
        "id": JOB_ID,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "workflow_name": "Main Validation",
        "name": "CI Gate",
        "head_sha": source_commit,
        "status": "completed",
        "conclusion": "success",
        "html_url": (
            f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}/job/{JOB_ID}"
        ),
        "started_at": _utc(completed - timedelta(minutes=1)),
        "completed_at": _utc(completed),
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
        ],
    }
    jobs = {"total_count": 1, "jobs": [job]}
    run_payload = _json_bytes(run)
    jobs_payload = _json_bytes(jobs)
    evidence = {
        "schema": "taiji-github-ci-evidence/v2",
        "provider": "github-actions-rest-api",
        "api_version": "2022-11-28",
        "repository": REPOSITORY,
        "workflow_id": 778899,
        "workflow_name": "Main Validation",
        "workflow_path": ".github/workflows/ci.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": source_commit,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "run_status": "completed",
        "run_conclusion": "success",
        "run_html_url": f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}",
        "run_created_at_utc": _utc(created),
        "run_updated_at_utc": _utc(completed),
        "required_job_id": JOB_ID,
        "required_job_name": "CI Gate",
        "required_job_status": "completed",
        "required_job_conclusion": "success",
        "required_job_html_url": (
            f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}/job/{JOB_ID}"
        ),
        "required_job_started_at_utc": _utc(completed - timedelta(minutes=1)),
        "required_job_completed_at_utc": _utc(completed),
        "required_step_name": "Require every selected job to pass",
        "required_step_status": "completed",
        "required_step_conclusion": "success",
        "collected_at_utc": _utc(current),
        "raw_run_basename": "github-ci-run-response.json",
        "raw_run_sha256": hashlib.sha256(run_payload).hexdigest(),
        "raw_jobs_basename": "github-ci-jobs-response.json",
        "raw_jobs_sha256": hashlib.sha256(jobs_payload).hexdigest(),
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "github-ci-run-response.json").write_bytes(run_payload)
    (root / "github-ci-jobs-response.json").write_bytes(jobs_payload)
    evidence_path = root / "github-ci-evidence.json"
    evidence_path.write_bytes(_json_bytes(evidence))
    return evidence_path
