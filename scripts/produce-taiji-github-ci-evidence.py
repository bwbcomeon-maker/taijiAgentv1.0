#!/usr/bin/env python3
"""Collect fail-closed GitHub Actions CI Gate evidence for Taiji releases."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
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
FINAL_DESTINATION_ROLLBACK_ATTEMPTS = 3
FILE_DESCRIPTOR_CLOSE_ATTEMPTS = 3
STAGING_PREFIX = ".taiji-github-ci-evidence."
DELIVERY_BASENAMES = (
    RUN_BASENAME,
    JOBS_BASENAME,
    EVIDENCE_BASENAME,
)


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


def _file_identity(value: os.stat_result) -> Tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_anchor(value: os.stat_result) -> Tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
    )


def _assert_trusted_delivery_directory_entry(
    opened: os.stat_result,
    current: os.stat_result,
    *,
    is_leaf: bool,
) -> None:
    mode = stat.S_IMODE(opened.st_mode)
    trusted_sticky_ancestor = (
        not is_leaf
        and opened.st_uid == 0
        and mode == 0o1777
    )
    if (
        _directory_anchor(opened) != _directory_anchor(current)
        or not stat.S_ISDIR(opened.st_mode)
        or opened.st_uid not in {0, os.getuid()}
        or (mode & 0o022 and not trusted_sticky_ancestor)
        or (is_leaf and opened.st_uid != os.getuid())
    ):
        raise GitHubCiEvidenceError(
            "delivery directory ancestor chain is not trusted"
        )


def _open_delivery_directory(delivery_dir: Path) -> Tuple[int, Tuple[int, ...]]:
    if not delivery_dir.is_absolute():
        raise GitHubCiEvidenceError(
            "delivery directory must be an existing absolute real directory"
        )
    try:
        if delivery_dir.resolve(strict=True) != delivery_dir:
            raise GitHubCiEvidenceError(
                "delivery directory must be an existing absolute real directory"
            )
    except (OSError, RuntimeError) as exc:
        raise GitHubCiEvidenceError(
            "delivery directory must be an existing absolute real directory"
        ) from exc
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(delivery_dir.anchor, flags)
    except OSError as exc:
        raise GitHubCiEvidenceError(
            "delivery directory ancestor chain cannot be opened safely"
        ) from exc
    try:
        parts = delivery_dir.parts[1:]
        opened = os.fstat(descriptor)
        current = os.stat(
            delivery_dir.anchor,
            follow_symlinks=False,
        )
        _assert_trusted_delivery_directory_entry(
            opened,
            current,
            is_leaf=not parts,
        )
        for index, part in enumerate(parts):
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise GitHubCiEvidenceError(
                    "delivery directory ancestor chain contains an unsafe entry"
                ) from exc
            try:
                opened = os.fstat(child)
                current = os.stat(
                    part,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                is_leaf = index == len(parts) - 1
                _assert_trusted_delivery_directory_entry(
                    opened,
                    current,
                    is_leaf=is_leaf,
                )
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        opened = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _directory_anchor(opened)


def _assert_delivery_anchor(
    delivery_dir: Path,
    descriptor: int,
    expected: Tuple[int, ...],
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = delivery_dir.lstat()
    except OSError as exc:
        raise GitHubCiEvidenceError("delivery directory changed during collection") from exc
    if (
        _directory_anchor(opened) != expected
        or _directory_anchor(current) != expected
        or stat.S_ISLNK(current.st_mode)
        or delivery_dir.resolve() != delivery_dir
    ):
        raise GitHubCiEvidenceError("delivery directory changed during collection")


def _assert_delivery_destinations_absent(descriptor: int) -> None:
    for basename in DELIVERY_BASENAMES:
        try:
            os.stat(basename, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GitHubCiEvidenceError(
                "CI evidence destination cannot be inspected safely"
            ) from exc
        raise GitHubCiEvidenceError(
            "CI evidence destination already exists; refusing to overwrite"
        )


def _close_owned_file_descriptor(record: Dict[str, Any]) -> None:
    failures = []  # type: List[OSError]
    closed = set()
    for key in ("descriptor", "pending_descriptor"):
        descriptor = record.get(key)
        if descriptor is None or descriptor in closed:
            record[key] = None
            continue
        try:
            os.close(descriptor)
        except OSError as exc:
            failures.append(exc)
        else:
            record[key] = None
            closed.add(descriptor)
    if failures:
        raise failures[0]


def _remove_owned_file(
    directory_descriptor: int,
    record: Dict[str, Any],
) -> bool:
    if record.get("removed") is True:
        return True
    if record.get("blocked") is True:
        return False
    descriptor = record.get("descriptor")
    if descriptor is None:
        record["poisoned"] = True
        return False
    opened = None  # type: Optional[os.stat_result]
    try:
        opened = os.fstat(descriptor)
    except OSError:
        trusted_identity = record.get("identity")
        if trusted_identity is None:
            record["poisoned"] = True
            return False
        try:
            fcntl.fcntl(descriptor, fcntl.F_GETFD)
        except OSError:
            record["poisoned"] = True
            return False
        expected_identity = trusted_identity
    else:
        if not stat.S_ISREG(opened.st_mode):
            record["blocked"] = True
            return False
        recorded_device = record.get("device")
        recorded_inode = record.get("inode")
        if recorded_device is not None and (
            opened.st_dev,
            opened.st_ino,
        ) != (recorded_device, recorded_inode):
            record["blocked"] = True
            return False
        record["device"] = opened.st_dev
        record["inode"] = opened.st_ino
        expected_identity = _file_identity(opened)
    try:
        current = os.stat(
            record["basename"],
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        record["removed"] = True
        return True
    except OSError:
        return False
    if _file_identity(current) != expected_identity:
        record["blocked"] = True
        return False
    try:
        os.unlink(record["basename"], dir_fd=directory_descriptor)
    except FileNotFoundError:
        record["removed"] = True
        return True
    except OSError:
        return False
    record["removed"] = True
    return True


def _remove_owned_files(
    directory_descriptor: int,
    records: List[Dict[str, Any]],
) -> bool:
    for record in reversed(records):
        _remove_owned_file(directory_descriptor, record)
    return all(record.get("removed") is True for record in records)


def _close_owned_file_descriptors(records: List[Dict[str, Any]]) -> bool:
    for _attempt in range(FILE_DESCRIPTOR_CLOSE_ATTEMPTS):
        for record in records:
            try:
                _close_owned_file_descriptor(record)
            except OSError:
                pass
        if all(
            record.get("descriptor") is None
            and record.get("pending_descriptor") is None
            for record in records
        ):
            return True
    return False


def _retain_read_only_owned_file(
    directory_descriptor: int,
    record: Dict[str, Any],
) -> None:
    writer = record.get("descriptor")
    if writer is None:
        raise GitHubCiEvidenceError("owned CI evidence writer is unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    reader = os.open(record["basename"], flags, dir_fd=directory_descriptor)
    record["pending_descriptor"] = reader
    written = os.fstat(writer)
    retained = os.fstat(reader)
    current = os.stat(
        record["basename"],
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if (
        _file_identity(written) != _file_identity(retained)
        or _file_identity(current) != _file_identity(retained)
    ):
        raise GitHubCiEvidenceError(
            "owned CI evidence file changed before descriptor retention"
        )
    os.close(writer)
    record["descriptor"] = reader
    record["pending_descriptor"] = None
    record["device"] = retained.st_dev
    record["inode"] = retained.st_ino
    record["identity"] = _file_identity(retained)
    os.fsync(directory_descriptor)


def _list_directory_names(directory_descriptor: int) -> List[str]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    scan_descriptor = os.open(".", flags, dir_fd=directory_descriptor)
    try:
        return os.listdir(scan_descriptor)
    finally:
        os.close(scan_descriptor)


def _remove_owned_empty_directory(
    delivery_descriptor: int,
    name: str,
    identity: Tuple[int, ...],
    held_descriptor: Optional[int] = None,
) -> bool:
    if held_descriptor is not None:
        try:
            opened = os.fstat(held_descriptor)
        except OSError:
            return False
        if _file_identity(opened) != identity:
            return False
    try:
        current = os.stat(
            name,
            dir_fd=delivery_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if _file_identity(current) != identity:
        return False
    try:
        os.rmdir(name, dir_fd=delivery_descriptor)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _create_staging_directory(
    delivery_descriptor: int,
) -> Tuple[str, int, Tuple[int, ...]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _attempt in range(32):
        name = STAGING_PREFIX + secrets.token_hex(16)
        try:
            os.mkdir(name, 0o700, dir_fd=delivery_descriptor)
        except FileExistsError:
            continue
        created_identity = None  # type: Optional[Tuple[int, ...]]
        descriptor = None  # type: Optional[int]
        path_identity_verified = False
        try:
            created = os.lstat(name, dir_fd=delivery_descriptor)
            created_identity = _file_identity(created)
            delivery_metadata = os.fstat(delivery_descriptor)
            if (
                not stat.S_ISDIR(created.st_mode)
                or created.st_uid != os.getuid()
                or stat.S_IMODE(created.st_mode) != 0o700
                or created.st_dev != delivery_metadata.st_dev
            ):
                raise GitHubCiEvidenceError(
                    "CI evidence staging directory is unsafe"
                )
            descriptor = os.open(name, flags, dir_fd=delivery_descriptor)
            opened = os.fstat(descriptor)
            current = os.stat(
                name,
                dir_fd=delivery_descriptor,
                follow_symlinks=False,
            )
            path_identity_verified = (
                _file_identity(current) == _file_identity(created)
            )
            if (
                _file_identity(opened) != _file_identity(created)
                or not path_identity_verified
                or opened.st_dev != delivery_metadata.st_dev
            ):
                raise GitHubCiEvidenceError("CI evidence staging directory is unsafe")
            return name, descriptor, _directory_anchor(created)
        except BaseException as exc:
            cleaned = False
            if created_identity is not None:
                cleaned = _remove_owned_empty_directory(
                    delivery_descriptor,
                    name,
                    created_identity,
                    None if path_identity_verified else descriptor,
                )
            if descriptor is not None:
                os.close(descriptor)
            if created_identity is not None and not cleaned:
                raise GitHubCiEvidenceError(
                    "CI evidence staging directory rollback preserved an unowned replacement"
                ) from exc
            raise
    raise GitHubCiEvidenceError("cannot allocate a private CI evidence staging directory")


def _write_new_at(
    directory_descriptor: int,
    basename: str,
    payload: bytes,
    created_files: List[Dict[str, Any]],
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(basename, flags, 0o600, dir_fd=directory_descriptor)
    record = {
        "basename": basename,
        "descriptor": descriptor,
        "pending_descriptor": None,
        "device": None,
        "inode": None,
        "identity": None,
        "removed": False,
        "blocked": False,
        "poisoned": False,
    }
    try:
        created_files.append(record)
    except BaseException as exc:
        removed = _remove_owned_file(directory_descriptor, record)
        closed = _close_owned_file_descriptors([record])
        if not removed or not closed:
            raise GitHubCiEvidenceError(
                "CI evidence staging registration rollback was poisoned"
            ) from exc
        raise
    try:
        metadata = os.fstat(descriptor)
        record["device"] = metadata.st_dev
        record["inode"] = metadata.st_ino
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            raise GitHubCiEvidenceError("CI evidence staging file is unsafe")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GitHubCiEvidenceError("CI evidence staging write failed")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        record["identity"] = _file_identity(metadata)
        if (
            (metadata.st_dev, metadata.st_ino)
            != (record["device"], record["inode"])
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(payload)
        ):
            raise GitHubCiEvidenceError("CI evidence staging file is unsafe")
        _retain_read_only_owned_file(
            directory_descriptor,
            record,
        )
    except BaseException:
        if _remove_owned_file(directory_descriptor, record):
            if _close_owned_file_descriptors([record]):
                created_files.remove(record)
        raise


def _read_regular_at(
    directory_descriptor: int,
    basename: str,
    maximum: int,
) -> Tuple[bytes, os.stat_result]:
    try:
        before = os.stat(
            basename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise GitHubCiEvidenceError("CI evidence staging file is missing") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_size <= 0
        or before.st_size > maximum
    ):
        raise GitHubCiEvidenceError("CI evidence staging file is unsafe")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(basename, flags, dir_fd=directory_descriptor)
    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise GitHubCiEvidenceError("CI evidence staging file changed before read")
        remaining = opened.st_size
        chunks = []  # type: List[bytes]
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise GitHubCiEvidenceError("CI evidence staging file was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise GitHubCiEvidenceError("CI evidence staging file grew during read")
        after = os.fstat(descriptor)
        current = os.stat(
            basename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            _file_identity(after) != _file_identity(opened)
            or _file_identity(current) != _file_identity(opened)
        ):
            raise GitHubCiEvidenceError("CI evidence staging file changed during read")
        return b"".join(chunks), opened
    finally:
        os.close(descriptor)


def _validate_staged_bundle(
    staging_descriptor: int,
    payloads: Dict[str, bytes],
) -> None:
    if set(_list_directory_names(staging_descriptor)) != set(DELIVERY_BASENAMES):
        raise GitHubCiEvidenceError("CI evidence staging closure is not the exact trio")
    maximums = {
        RUN_BASENAME: MAX_RUN_BYTES,
        JOBS_BASENAME: MAX_JOBS_BYTES,
        EVIDENCE_BASENAME: MAX_RUN_BYTES,
    }
    for basename in DELIVERY_BASENAMES:
        observed, _metadata = _read_regular_at(
            staging_descriptor,
            basename,
            maximums[basename],
        )
        if observed != payloads[basename]:
            raise GitHubCiEvidenceError("CI evidence staging bytes changed")
    os.fsync(staging_descriptor)


def _validate_retained_delivery_bundle(
    delivery_dir: Path,
    delivery_descriptor: int,
    anchor: Tuple[int, ...],
    payloads: Dict[str, bytes],
    records: List[Dict[str, Any]],
) -> None:
    _assert_delivery_anchor(delivery_dir, delivery_descriptor, anchor)
    basenames = [record.get("basename") for record in records]
    if (
        len(basenames) != len(DELIVERY_BASENAMES)
        or set(basenames) != set(DELIVERY_BASENAMES)
    ):
        raise GitHubCiEvidenceError(
            "retained CI evidence bundle is not the exact canonical trio"
        )
    directory_names = set(_list_directory_names(delivery_descriptor))
    if not set(DELIVERY_BASENAMES).issubset(directory_names):
        raise GitHubCiEvidenceError(
            "retained CI evidence bundle is not the exact canonical trio"
        )
    for record in records:
        descriptor = record.get("descriptor")
        trusted_identity = record.get("identity")
        basename = record["basename"]
        if descriptor is None or trusted_identity is None:
            raise GitHubCiEvidenceError(
                "retained CI evidence ownership proof is unavailable"
            )
        before = os.fstat(descriptor)
        current = os.stat(
            basename,
            dir_fd=delivery_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or _file_identity(before) != trusted_identity
            or _file_identity(current) != trusted_identity
        ):
            raise GitHubCiEvidenceError(
                "retained CI evidence path changed before final publication"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = before.st_size
        chunks = []  # type: List[bytes]
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise GitHubCiEvidenceError(
                    "retained CI evidence was truncated before publication"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise GitHubCiEvidenceError(
                "retained CI evidence grew before publication"
            )
        after = os.fstat(descriptor)
        current_after = os.stat(
            basename,
            dir_fd=delivery_descriptor,
            follow_symlinks=False,
        )
        if (
            b"".join(chunks) != payloads[basename]
            or _file_identity(after) != trusted_identity
            or _file_identity(current_after) != trusted_identity
        ):
            raise GitHubCiEvidenceError(
                "retained CI evidence bytes or path changed before publication"
            )
    os.fsync(delivery_descriptor)
    _assert_delivery_anchor(delivery_dir, delivery_descriptor, anchor)


def _promote_staged_file(
    staging_descriptor: int,
    delivery_descriptor: int,
    basename: str,
    created_destinations: List[Dict[str, Any]],
) -> None:
    maximum = MAX_JOBS_BYTES if basename == JOBS_BASENAME else MAX_RUN_BYTES
    payload, source_metadata = _read_regular_at(
        staging_descriptor,
        basename,
        maximum,
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(basename, flags, 0o600, dir_fd=delivery_descriptor)
    record = {
        "basename": basename,
        "descriptor": descriptor,
        "pending_descriptor": None,
        "device": None,
        "inode": None,
        "identity": None,
        "removed": False,
        "blocked": False,
        "poisoned": False,
    }
    try:
        created_destinations.append(record)
    except BaseException as exc:
        removed = _remove_owned_file(delivery_descriptor, record)
        closed = _close_owned_file_descriptors([record])
        if not removed or not closed:
            raise GitHubCiEvidenceError(
                "CI evidence destination registration rollback was poisoned"
            ) from exc
        raise
    try:
        created_metadata = os.fstat(descriptor)
        record["device"] = created_metadata.st_dev
        record["inode"] = created_metadata.st_ino
        if (
            not stat.S_ISREG(created_metadata.st_mode)
            or created_metadata.st_uid != os.getuid()
            or created_metadata.st_nlink != 1
            or stat.S_IMODE(created_metadata.st_mode) != 0o600
            or created_metadata.st_size != 0
        ):
            raise GitHubCiEvidenceError("promoted CI evidence file is unsafe")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise GitHubCiEvidenceError("CI evidence promotion write failed")
            view = view[written:]
        os.fsync(descriptor)
        final = os.fstat(descriptor)
        record["identity"] = _file_identity(final)
        current = os.stat(
            basename,
            dir_fd=delivery_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final.st_mode)
            or final.st_uid != os.getuid()
            or final.st_nlink != 1
            or stat.S_IMODE(final.st_mode) != 0o600
            or final.st_size != len(payload)
            or _file_identity(final) != _file_identity(current)
        ):
            raise GitHubCiEvidenceError("promoted CI evidence file is unsafe")
        staged_now = os.stat(
            basename,
            dir_fd=staging_descriptor,
            follow_symlinks=False,
        )
        if _file_identity(staged_now) != _file_identity(source_metadata):
            raise GitHubCiEvidenceError("CI evidence staging file changed during promotion")
        _retain_read_only_owned_file(
            delivery_descriptor,
            record,
        )
    except BaseException:
        if _remove_owned_file(delivery_descriptor, record):
            if _close_owned_file_descriptors([record]):
                created_destinations.remove(record)
        raise


def _remove_owned_destinations(
    delivery_descriptor: int,
    created_destinations: List[Dict[str, Any]],
) -> bool:
    for _attempt in range(FINAL_DESTINATION_ROLLBACK_ATTEMPTS):
        if _remove_owned_files(delivery_descriptor, created_destinations):
            return True
    return False


def _remove_staging_directory(
    delivery_descriptor: int,
    staging_name: Optional[str],
    staging_descriptor: Optional[int],
    staging_identity: Optional[Tuple[int, ...]],
    staged_files: List[Dict[str, Any]],
) -> Tuple[bool, Optional[int]]:
    if staging_name is None:
        if staging_descriptor is not None:
            os.close(staging_descriptor)
        return True, None
    if staging_descriptor is None or staging_identity is None:
        return False, staging_descriptor
    files_clean = _remove_owned_files(staging_descriptor, staged_files)
    try:
        closure_empty = not _list_directory_names(staging_descriptor)
        os.fsync(staging_descriptor)
        opened = os.fstat(staging_descriptor)
        current = os.stat(
            staging_name,
            dir_fd=delivery_descriptor,
            follow_symlinks=False,
        )
        identity_matches = (
            _directory_anchor(opened) == staging_identity
            and _directory_anchor(current) == staging_identity
        )
    except OSError:
        return False, staging_descriptor
    if not files_clean or not closure_empty or not identity_matches:
        return False, staging_descriptor
    try:
        os.rmdir(staging_name, dir_fd=delivery_descriptor)
        os.fsync(delivery_descriptor)
    except OSError:
        return False, staging_descriptor
    os.close(staging_descriptor)
    return True, None


def _publish_into_delivery(
    delivery_dir: Path,
    delivery_descriptor: int,
    anchor: Tuple[int, ...],
    payloads: Dict[str, bytes],
) -> Path:
    staging_name = None  # type: Optional[str]
    staging_descriptor = None  # type: Optional[int]
    staging_identity = None  # type: Optional[Tuple[int, ...]]
    staged_files = []  # type: List[Dict[str, Any]]
    created = []  # type: List[Dict[str, Any]]
    succeeded = False
    try:
        _assert_delivery_anchor(delivery_dir, delivery_descriptor, anchor)
        _assert_delivery_destinations_absent(delivery_descriptor)
        (
            staging_name,
            staging_descriptor,
            staging_identity,
        ) = _create_staging_directory(delivery_descriptor)
        for basename in DELIVERY_BASENAMES:
            _write_new_at(
                staging_descriptor,
                basename,
                payloads[basename],
                staged_files,
            )
        _validate_staged_bundle(staging_descriptor, payloads)
        _assert_delivery_anchor(delivery_dir, delivery_descriptor, anchor)
        for basename in DELIVERY_BASENAMES:
            _promote_staged_file(
                staging_descriptor,
                delivery_descriptor,
                basename,
                created,
            )
        _assert_delivery_anchor(delivery_dir, delivery_descriptor, anchor)
        os.fsync(delivery_descriptor)
        for basename in DELIVERY_BASENAMES:
            observed, metadata = _read_regular_at(
                delivery_descriptor,
                basename,
                MAX_JOBS_BYTES if basename == JOBS_BASENAME else MAX_RUN_BYTES,
            )
            if observed != payloads[basename] or metadata.st_nlink != 1:
                raise GitHubCiEvidenceError("promoted CI evidence bundle changed")
        _assert_delivery_anchor(delivery_dir, delivery_descriptor, anchor)
        staging_clean, staging_descriptor = _remove_staging_directory(
            delivery_descriptor,
            staging_name,
            staging_descriptor,
            staging_identity,
            staged_files,
        )
        if not staging_clean:
            raise GitHubCiEvidenceError(
                "CI evidence staging cleanup failed; publication was rolled back"
            )
        staging_name = None
        staging_identity = None
        os.fsync(delivery_descriptor)
        if not _close_owned_file_descriptors(staged_files):
            raise GitHubCiEvidenceError(
                "CI evidence staging file descriptor cleanup failed"
            )
        _validate_retained_delivery_bundle(
            delivery_dir,
            delivery_descriptor,
            anchor,
            payloads,
            created,
        )
        if not _close_owned_file_descriptors(created):
            raise GitHubCiEvidenceError(
                "CI evidence owned file descriptor cleanup failed"
            )
        succeeded = True
        return delivery_dir / EVIDENCE_BASENAME
    finally:
        cleanup_failed = False
        if not succeeded:
            cleanup_failed = not _remove_owned_destinations(
                delivery_descriptor,
                created,
            )
        staging_clean, staging_descriptor = _remove_staging_directory(
            delivery_descriptor,
            staging_name,
            staging_descriptor,
            staging_identity,
            staged_files,
        )
        cleanup_failed = cleanup_failed or not staging_clean
        if staging_descriptor is not None:
            try:
                os.close(staging_descriptor)
            except OSError:
                cleanup_failed = True
            staging_descriptor = None
        cleanup_failed = (
            not _close_owned_file_descriptors(staged_files)
            or cleanup_failed
        )
        cleanup_failed = (
            not _close_owned_file_descriptors(created)
            or cleanup_failed
        )
        try:
            os.fsync(delivery_descriptor)
        except OSError:
            cleanup_failed = True
        if cleanup_failed:
            poisoned = any(
                record.get("poisoned") is True
                for record in staged_files + created
            )
            suffix = (
                "; an ownership proof was poisoned and its path was preserved"
                if poisoned
                else ""
            )
            raise GitHubCiEvidenceError(
                "CI evidence rollback could not remove every owned node safely"
                + suffix
            )


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
    output_dir: Optional[Path] = None,
    delivery_dir: Optional[Path] = None,
) -> Path:
    if (
        type(source_commit) is not str
        or not COMMIT_RE.fullmatch(source_commit)
    ):
        raise GitHubCiEvidenceError(
            "source_commit must be a full lowercase Git commit"
        )
    run_id = _require_positive_integer(run_id, "run_id")
    if (output_dir is None) == (delivery_dir is None):
        raise GitHubCiEvidenceError(
            "exactly one of output_dir or delivery_dir is required"
        )
    delivery_descriptor = None  # type: Optional[int]
    delivery_anchor = None  # type: Optional[Tuple[int, ...]]
    if output_dir is not None:
        output_dir = Path(output_dir)
        _prepare_output(output_dir)
    else:
        delivery_dir = Path(delivery_dir)
        delivery_descriptor, delivery_anchor = _open_delivery_directory(
            delivery_dir
        )
        try:
            _assert_delivery_destinations_absent(delivery_descriptor)
        finally:
            os.close(delivery_descriptor)
            delivery_descriptor = None
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

    try:
        payloads = {
            RUN_BASENAME: run_payload,
            JOBS_BASENAME: jobs_payload,
            EVIDENCE_BASENAME: evidence_payload,
        }
        if delivery_dir is not None:
            delivery_descriptor, current_anchor = _open_delivery_directory(
                delivery_dir
            )
            if current_anchor != delivery_anchor:
                os.close(delivery_descriptor)
                delivery_descriptor = None
                raise GitHubCiEvidenceError(
                    "delivery directory changed during collection"
                )
            _assert_delivery_destinations_absent(delivery_descriptor)
            return _publish_into_delivery(
                delivery_dir,
                delivery_descriptor,
                delivery_anchor,
                payloads,
            )

        os.mkdir(output_dir, 0o700)
        created = []  # type: List[Path]
        try:
            for basename in DELIVERY_BASENAMES:
                path = output_dir / basename
                created.append(path)
                _write_new(path, payloads[basename])
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
    finally:
        if delivery_descriptor is not None:
            os.close(delivery_descriptor)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, allow_abbrev=False
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output-dir", type=Path)
    output.add_argument("--delivery-dir", type=Path)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    try:
        args = parse_args(argv)
        output = produce(
            source_commit=args.source_commit,
            run_id=args.run_id,
            output_dir=args.output_dir,
            delivery_dir=args.delivery_dir,
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
