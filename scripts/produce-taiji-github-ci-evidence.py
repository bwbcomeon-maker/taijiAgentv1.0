#!/usr/bin/env python3
"""Collect fail-closed GitHub Actions CI Gate evidence for Taiji releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


SCHEMA = "taiji-github-ci-evidence/v2"
PROVIDER = "github-actions-rest-api"
API_VERSION = "2022-11-28"
API_ORIGIN = "https://api.github.com"
WEB_ORIGIN = "https://github.com"
REPOSITORY = "bwbcomeon-maker/taijiAgentv1.0"
WORKFLOW_NAME = "Pull Request CI"
WORKFLOW_PATH = ".github/workflows/ci.yml"
RELEASE_EVENT = "push"
RELEASE_BRANCH = "main"
REQUIRED_JOB_NAME = "CI Gate"
REQUIRED_STEP_NAME = "Require every selected job to pass"
RUN_BASENAME = "github-ci-run-response.json"
JOBS_BASENAME = "github-ci-jobs-response.json"
EVIDENCE_BASENAME = "github-ci-evidence.json"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_RUN_BYTES = 2 * 1024 * 1024
MAX_JOBS_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 20
MAX_RUN_AGE = timedelta(days=7)


class GitHubCiEvidenceError(ValueError):
    """Raised when GitHub cannot prove the exact release CI contract."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GitHubCiEvidenceError("GitHub API JSON contains a duplicate field")
        result[key] = value
    return result


def _parse_json(payload: bytes, label: str) -> dict:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, GitHubCiEvidenceError) as exc:
        raise GitHubCiEvidenceError(f"{label} is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise GitHubCiEvidenceError(f"{label} must be a JSON object")
    return value


def _require_text(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(character in value for character in "\r\n\t")
    ):
        raise GitHubCiEvidenceError(f"{label} is invalid")
    return value


def _require_positive_integer(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise GitHubCiEvidenceError(f"{label} must be a positive integer")
    return value


def _require_exact(data: dict, key: str, expected: Any, label: str) -> None:
    if data.get(key) != expected:
        raise GitHubCiEvidenceError(f"{label} {key} does not match the fixed contract")


def _require_timestamp(
    value: Any, label: str, now: datetime
) -> Tuple[str, datetime]:
    text = _require_text(value, label)
    if not text.endswith("Z"):
        raise GitHubCiEvidenceError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise GitHubCiEvidenceError(f"{label} must be a UTC timestamp") from exc
    if parsed.tzinfo is None or parsed > now:
        raise GitHubCiEvidenceError(f"{label} is in the future")
    return text, parsed


def _require_repository(data: dict, key: str) -> None:
    repository = data.get(key)
    if type(repository) is not dict or repository.get("full_name") != REPOSITORY:
        raise GitHubCiEvidenceError(f"GitHub run {key} is not the fixed repository")


def _expected_run_url(run_id: int) -> str:
    return f"{API_ORIGIN}/repos/{REPOSITORY}/actions/runs/{run_id}"


def _expected_jobs_url(run_id: int, run_attempt: int) -> str:
    return (
        f"{API_ORIGIN}/repos/{REPOSITORY}/actions/runs/{run_id}"
        f"/attempts/{run_attempt}/jobs?per_page=100"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_bounded(response, maximum: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise GitHubCiEvidenceError(
                "GitHub API returned an invalid Content-Length"
            ) from exc
        if declared <= 0 or declared > maximum:
            raise GitHubCiEvidenceError(
                "GitHub API response size is outside the allowed bound"
            )
    payload = response.read(maximum + 1)
    if not payload or len(payload) > maximum:
        raise GitHubCiEvidenceError(
            "GitHub API response size is outside the allowed bound"
        )
    if content_length is not None and len(payload) != declared:
        raise GitHubCiEvidenceError("GitHub API response was truncated")
    return payload


def _github_fetch_bytes(url: str) -> Tuple[bytes, str]:
    if not url.startswith(API_ORIGIN + "/"):
        raise GitHubCiEvidenceError("GitHub API URL escaped the fixed API origin")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "taiji-agent-release-evidence/1",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        if any(character in token for character in "\r\n"):
            raise GitHubCiEvidenceError(
                "GITHUB_TOKEN contains an invalid character"
            )
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method="GET")
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise GitHubCiEvidenceError(
                    f"GitHub API returned HTTP {response.status}"
                )
            final_url = response.geturl()
            maximum = MAX_JOBS_BYTES if "/jobs?" in url else MAX_RUN_BYTES
            payload = _read_bounded(response, maximum)
    except HTTPError as exc:
        raise GitHubCiEvidenceError(
            f"GitHub API returned HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise GitHubCiEvidenceError("GitHub API request failed") from exc
    if final_url != url:
        raise GitHubCiEvidenceError(
            "GitHub API redirected away from the exact request"
        )
    return payload, final_url


def _fetch_exact(
    fetch_bytes: Callable[[str], Tuple[bytes, str]],
    url: str,
    maximum: int,
    label: str,
) -> bytes:
    try:
        result = fetch_bytes(url)
    except GitHubCiEvidenceError:
        raise
    except Exception as exc:
        raise GitHubCiEvidenceError(f"{label} request failed") from exc
    if type(result) is not tuple or len(result) != 2:
        raise GitHubCiEvidenceError(
            f"{label} fetcher returned an invalid response"
        )
    payload, final_url = result
    if type(payload) is not bytes or not payload or len(payload) > maximum:
        raise GitHubCiEvidenceError(
            f"{label} response size is outside the allowed bound"
        )
    if final_url != url:
        raise GitHubCiEvidenceError(
            f"{label} response URL does not match the fixed API request"
        )
    return payload


def _validate_run(
    run: dict, source_commit: str, run_id: int, now: datetime
) -> dict:
    expected = {
        "id": run_id,
        "name": WORKFLOW_NAME,
        "path": WORKFLOW_PATH,
        "event": RELEASE_EVENT,
        "status": "completed",
        "conclusion": "success",
        "head_sha": source_commit,
        "head_branch": RELEASE_BRANCH,
        "html_url": f"{WEB_ORIGIN}/{REPOSITORY}/actions/runs/{run_id}",
    }
    for key, value in expected.items():
        _require_exact(run, key, value, "GitHub run")
    run_attempt = _require_positive_integer(
        run.get("run_attempt"), "GitHub run_attempt"
    )
    workflow_id = _require_positive_integer(
        run.get("workflow_id"), "GitHub workflow_id"
    )
    _require_repository(run, "repository")
    _require_repository(run, "head_repository")
    created, created_value = _require_timestamp(
        run.get("created_at"), "GitHub run created_at", now
    )
    updated, updated_value = _require_timestamp(
        run.get("updated_at"), "GitHub run updated_at", now
    )
    if updated_value < created_value:
        raise GitHubCiEvidenceError(
            "GitHub run updated_at precedes created_at"
        )
    if now - updated_value > MAX_RUN_AGE:
        raise GitHubCiEvidenceError(
            "GitHub run is too old for current release evidence"
        )
    return {
        "run_attempt": run_attempt,
        "workflow_id": workflow_id,
        "created_at": created,
        "updated_at": updated,
    }


def _validate_jobs(
    jobs_payload: dict,
    source_commit: str,
    run_id: int,
    run_attempt: int,
    now: datetime,
) -> dict:
    total_count = jobs_payload.get("total_count")
    jobs = jobs_payload.get("jobs")
    if (
        type(total_count) is not int
        or total_count < 0
        or type(jobs) is not list
    ):
        raise GitHubCiEvidenceError(
            "GitHub jobs response has an invalid shape"
        )
    if total_count != len(jobs) or total_count > 100:
        raise GitHubCiEvidenceError(
            "GitHub jobs response is incomplete or paginated"
        )
    required_jobs = [
        item
        for item in jobs
        if type(item) is dict and item.get("name") == REQUIRED_JOB_NAME
    ]
    if len(required_jobs) != 1:
        raise GitHubCiEvidenceError(
            "GitHub run must contain exactly one CI Gate job"
        )
    job = required_jobs[0]
    expected = {
        "run_id": run_id,
        "run_attempt": run_attempt,
        "workflow_name": WORKFLOW_NAME,
        "name": REQUIRED_JOB_NAME,
        "head_sha": source_commit,
        "status": "completed",
        "conclusion": "success",
    }
    for key, value in expected.items():
        _require_exact(job, key, value, "GitHub CI Gate job")
    job_id = _require_positive_integer(
        job.get("id"), "GitHub CI Gate job id"
    )
    expected_html_url = (
        f"{WEB_ORIGIN}/{REPOSITORY}/actions/runs/{run_id}/job/{job_id}"
    )
    _require_exact(
        job, "html_url", expected_html_url, "GitHub CI Gate job"
    )
    started, started_value = _require_timestamp(
        job.get("started_at"), "GitHub CI Gate started_at", now
    )
    completed, completed_value = _require_timestamp(
        job.get("completed_at"), "GitHub CI Gate completed_at", now
    )
    if completed_value < started_value:
        raise GitHubCiEvidenceError(
            "GitHub CI Gate completed_at precedes started_at"
        )
    if now - completed_value > MAX_RUN_AGE:
        raise GitHubCiEvidenceError(
            "GitHub CI Gate is too old for current release evidence"
        )
    steps = job.get("steps")
    if type(steps) is not list:
        raise GitHubCiEvidenceError("GitHub CI Gate steps are unavailable")
    required_steps = [
        item
        for item in steps
        if type(item) is dict and item.get("name") == REQUIRED_STEP_NAME
    ]
    if len(required_steps) != 1:
        raise GitHubCiEvidenceError(
            "GitHub CI Gate must contain exactly one required contract step"
        )
    step = required_steps[0]
    _require_exact(
        step, "status", "completed", "GitHub CI Gate required step"
    )
    _require_exact(
        step, "conclusion", "success", "GitHub CI Gate required step"
    )
    return {
        "id": job_id,
        "html_url": expected_html_url,
        "started_at": started,
        "completed_at": completed,
    }


def _write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GitHubCiEvidenceError("CI evidence write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_output(output_dir: Path) -> None:
    if (
        not output_dir.is_absolute()
        or output_dir.exists()
        or output_dir.is_symlink()
    ):
        raise GitHubCiEvidenceError(
            "output directory must be a new absolute path"
        )
    parent = output_dir.parent
    if (
        not parent.is_dir()
        or parent.is_symlink()
        or parent.resolve() != parent
    ):
        raise GitHubCiEvidenceError(
            "output parent must be an existing real directory"
        )
    metadata = parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise GitHubCiEvidenceError("output parent must be a directory")


def produce(
    *,
    source_commit: str,
    run_id: int,
    output_dir: Path,
) -> Path:
    if (
        type(source_commit) is not str
        or not COMMIT_RE.fullmatch(source_commit)
    ):
        raise GitHubCiEvidenceError(
            "source_commit must be a full lowercase Git commit"
        )
    run_id = _require_positive_integer(run_id, "run_id")
    output_dir = Path(output_dir)
    _prepare_output(output_dir)
    current_time = _utc_now()
    if current_time.tzinfo is None:
        raise GitHubCiEvidenceError("current time must be timezone-aware")

    run_url = _expected_run_url(run_id)
    run_payload = _fetch_exact(
        _github_fetch_bytes,
        run_url,
        MAX_RUN_BYTES,
        "GitHub workflow run",
    )
    run = _parse_json(run_payload, "GitHub workflow run")
    run_contract = _validate_run(
        run, source_commit, run_id, current_time
    )

    jobs_url = _expected_jobs_url(
        run_id, run_contract["run_attempt"]
    )
    jobs_payload = _fetch_exact(
        _github_fetch_bytes,
        jobs_url,
        MAX_JOBS_BYTES,
        "GitHub workflow jobs",
    )
    jobs = _parse_json(jobs_payload, "GitHub workflow jobs")
    job_contract = _validate_jobs(
        jobs,
        source_commit,
        run_id,
        run_contract["run_attempt"],
        current_time,
    )

    evidence = {
        "schema": SCHEMA,
        "provider": PROVIDER,
        "api_version": API_VERSION,
        "repository": REPOSITORY,
        "workflow_id": run_contract["workflow_id"],
        "workflow_name": WORKFLOW_NAME,
        "workflow_path": WORKFLOW_PATH,
        "event": RELEASE_EVENT,
        "head_branch": RELEASE_BRANCH,
        "head_sha": source_commit,
        "run_id": run_id,
        "run_attempt": run_contract["run_attempt"],
        "run_status": "completed",
        "run_conclusion": "success",
        "run_html_url": (
            f"{WEB_ORIGIN}/{REPOSITORY}/actions/runs/{run_id}"
        ),
        "run_created_at_utc": run_contract["created_at"],
        "run_updated_at_utc": run_contract["updated_at"],
        "required_job_id": job_contract["id"],
        "required_job_name": REQUIRED_JOB_NAME,
        "required_job_status": "completed",
        "required_job_conclusion": "success",
        "required_job_html_url": job_contract["html_url"],
        "required_job_started_at_utc": job_contract["started_at"],
        "required_job_completed_at_utc": job_contract["completed_at"],
        "required_step_name": REQUIRED_STEP_NAME,
        "required_step_status": "completed",
        "required_step_conclusion": "success",
        "collected_at_utc": current_time.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "raw_run_basename": RUN_BASENAME,
        "raw_run_sha256": hashlib.sha256(run_payload).hexdigest(),
        "raw_jobs_basename": JOBS_BASENAME,
        "raw_jobs_sha256": hashlib.sha256(jobs_payload).hexdigest(),
    }
    evidence_payload = (
        json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n"
    ).encode("utf-8")

    os.mkdir(output_dir, 0o700)
    created = []
    try:
        for basename, payload in (
            (RUN_BASENAME, run_payload),
            (JOBS_BASENAME, jobs_payload),
            (EVIDENCE_BASENAME, evidence_payload),
        ):
            path = output_dir / basename
            created.append(path)
            _write_new(path, payload)
        directory_fd = os.open(
            output_dir,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise
    return output_dir / EVIDENCE_BASENAME


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, allow_abbrev=False
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    try:
        args = parse_args(argv)
        output = produce(
            source_commit=args.source_commit,
            run_id=args.run_id,
            output_dir=args.output_dir,
        )
    except (
        GitHubCiEvidenceError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"github-ci-evidence-failed\t{exc}", file=sys.stderr)
        return 1
    print(f"github-ci-evidence-produced\t{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
