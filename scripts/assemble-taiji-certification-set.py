#!/usr/bin/env python3
"""Assemble one immutable, unsigned Taiji Linux certification set."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Tuple


ROOT = Path(__file__).resolve().parents[1]
MATRIX_SCHEMA = "taiji-linux-certification-matrix/v2"
SET_SCHEMA = "taiji-linux-certification-set/v1"
ENVIRONMENT_SCHEMA = "taiji-linux-environment-evidence/v2"
POLICY_ID = "taiji-linux-amd64-deb-v1"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
RECORD_BASENAME = "environment-evidence.json"
CURRENT_OFFLINE_REHEARSAL_ENVIRONMENT = "container-kylin-policy-fixture-v1"
MAX_OFFLINE_ATTACHMENT_BYTES = 1024 * 1024
MAX_PREVIOUS_RELEASE_DEB_BYTES = 2 * 1024 * 1024 * 1024
MAX_CERTIFICATION_ATTACHMENT_BYTES = 1024 * 1024
MAX_CERTIFICATION_PNG_BYTES = 32 * 1024 * 1024
LARGE_CERTIFICATION_PNG_BASENAMES = {
    "single-deb-graphical-installer.png",
    "desktop-app.png",
}
AttachmentCopySource = Tuple[Path, str, int]


class CertificationSetError(ValueError):
    """Raised when a certification set cannot be assembled safely."""


def _load_assembler():
    path = ROOT / "tools/taiji-desktop-acceptance/assemble-target-evidence.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("taiji_certification_set_assembler_contract", path)
    if spec is None or spec.loader is None:
        raise CertificationSetError("cannot load environment evidence contract")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_assembler()


def _load_release_validator():
    candidates = (
        Path(__file__).resolve().with_name("validate-taiji-release-evidence.py"),
        ROOT / "scripts/validate-taiji-release-evidence.py",
    )
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            "taiji_certification_set_release_validator",
            path,
        )
        if spec is None or spec.loader is None:
            raise CertificationSetError("cannot load release evidence validator")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(spec.name, None)
            raise
        return module
    return None


def _load_challenge_helper():
    candidates = (
        Path(__file__).resolve().with_name("taiji-challenge-envelope.py"),
        ROOT / "scripts/taiji-challenge-envelope.py",
    )
    for path in dict.fromkeys(candidates):
        if not path.is_file() or path.is_symlink():
            continue
        spec = importlib.util.spec_from_file_location(
            "taiji_certification_challenge_envelope",
            path,
        )
        if spec is None or spec.loader is None:
            raise CertificationSetError("cannot load challenge-envelope helper")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise CertificationSetError("challenge-envelope helper is missing")


def _strict_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, CertificationSetError) as exc:
        raise CertificationSetError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if type(value) is not dict:
        raise CertificationSetError(f"{label} must be a JSON object")
    return value


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CertificationSetError(f"JSON contains duplicate field: {key}")
        result[key] = value
    return result


def _safe_regular(
    path: Path,
    label: str,
    *,
    max_bytes: int = MAX_OFFLINE_ATTACHMENT_BYTES,
) -> bytes:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise CertificationSetError(f"{label} must be an absolute regular file")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be inspected: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise CertificationSetError(f"{label} must be exactly one regular single-link file")
    if metadata.st_size <= 0 or metadata.st_size > max_bytes:
        raise CertificationSetError(f"{label} has an invalid size")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be opened safely: {exc}") from exc
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    try:
        opened = os.fstat(descriptor)
        if identity(metadata) != identity(opened):
            raise CertificationSetError(f"{label} changed before it was opened")
        chunks = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise CertificationSetError(f"{label} was truncated while it was read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise CertificationSetError(f"{label} grew while it was read")
        after = os.fstat(descriptor)
        current = path.lstat()
        if identity(opened) != identity(after) or identity(opened) != identity(current):
            raise CertificationSetError(f"{label} changed while it was read")
        return b"".join(chunks)
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be read: {exc}") from exc
    finally:
        os.close(descriptor)


def _sha256_bounded_stable_regular_file(
    path: Path,
    label: str,
    *,
    max_bytes: int,
) -> tuple[str, int]:
    """Hash one bounded, stable single-link file without buffering its body."""

    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise CertificationSetError(f"{label} must be an absolute regular file")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be inspected: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise CertificationSetError(f"{label} must be exactly one regular single-link file")
    if metadata.st_size <= 0 or metadata.st_size > max_bytes:
        raise CertificationSetError(f"{label} has an invalid size")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be opened safely: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if _regular_identity(metadata) != _regular_identity(opened):
            raise CertificationSetError(f"{label} changed before it was opened")
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise CertificationSetError(f"{label} was truncated while it was hashed")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise CertificationSetError(f"{label} grew while it was hashed")
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            _regular_identity(opened) != _regular_identity(after)
            or _regular_identity(opened) != _regular_identity(current)
        ):
            raise CertificationSetError(f"{label} changed while it was hashed")
        return digest.hexdigest(), opened.st_size
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be hashed: {exc}") from exc
    finally:
        os.close(descriptor)


def _regular_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_nlink,
        file_stat.st_uid,
        file_stat.st_gid,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _stream_regular_snapshot(
    source: Path,
    destination: Path,
    label: str,
    *,
    max_bytes: int,
) -> tuple[str, int]:
    """Copy one stable single-link source snapshot without buffering it in memory."""

    if not source.is_absolute() or source.is_symlink():
        raise CertificationSetError(f"{label} must be an absolute regular file")
    try:
        before = source.lstat()
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be inspected: {exc}") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise CertificationSetError(f"{label} must be exactly one regular single-link file")
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise CertificationSetError(f"{label} has an invalid size")
    if (
        not destination.is_absolute()
        or destination.parent.is_symlink()
        or not destination.parent.is_dir()
        or os.path.lexists(destination)
    ):
        raise CertificationSetError(f"{label} snapshot destination must be a new file")

    read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    source_descriptor = -1
    destination_descriptor = -1
    succeeded = False
    try:
        source_descriptor = os.open(str(source), read_flags)
        opened = os.fstat(source_descriptor)
        if _regular_identity(before) != _regular_identity(opened):
            raise CertificationSetError(f"{label} changed before it was opened")

        destination_descriptor = os.open(str(destination), write_flags, 0o600)
        destination_opened = os.fstat(destination_descriptor)
        if not stat.S_ISREG(destination_opened.st_mode) or destination_opened.st_nlink != 1:
            raise CertificationSetError(f"{label} snapshot is not a single-link regular file")
        if destination_opened.st_mode & 0o077:
            raise CertificationSetError(f"{label} snapshot permissions are too broad")

        digest = hashlib.sha256()
        remaining = opened.st_size
        copied = 0
        while remaining:
            chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise CertificationSetError(f"{label} was truncated while it was copied")
            digest.update(chunk)
            copied += len(chunk)
            remaining -= len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise CertificationSetError(f"{label} snapshot could not be written")
                view = view[written:]
        if os.read(source_descriptor, 1):
            raise CertificationSetError(f"{label} grew while it was copied")
        os.fsync(destination_descriptor)

        after = os.fstat(source_descriptor)
        current = source.lstat()
        if (
            _regular_identity(opened) != _regular_identity(after)
            or _regular_identity(opened) != _regular_identity(current)
        ):
            raise CertificationSetError(f"{label} changed while it was copied")
        destination_after = os.fstat(destination_descriptor)
        destination_current = destination.lstat()
        if (
            not stat.S_ISREG(destination_after.st_mode)
            or destination_after.st_nlink != 1
            or destination_after.st_size != copied
            or destination_after.st_mode & 0o077
            or _regular_identity(destination_after) != _regular_identity(destination_current)
        ):
            raise CertificationSetError(f"{label} snapshot metadata is invalid")
        succeeded = True
        return digest.hexdigest(), copied
    except OSError as exc:
        raise CertificationSetError(f"{label} cannot be snapshotted: {exc}") from exc
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if not succeeded and os.path.lexists(destination):
            try:
                destination.unlink()
            except OSError:
                pass


def _safe_directory(path: Path, label: str) -> None:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise CertificationSetError(f"{label} must be an absolute real directory")
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise CertificationSetError(f"{label} must be a directory")


def _sha256(path: Path, label: str) -> str:
    payload = _safe_regular(path, label, max_bytes=1024 * 1024 * 1024)
    return hashlib.sha256(payload).hexdigest()


def _require_sha(value: Any, label: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise CertificationSetError(f"{label} must be a lowercase SHA256")
    return value


def _load_matrix(path: Path) -> dict[str, Any]:
    matrix_payload = _safe_regular(path, "certification matrix")
    matrix = _strict_json(matrix_payload, "certification matrix")
    if CONTRACT is not None:
        try:
            CONTRACT.validate_certification_matrix(matrix)
        except Exception as exc:  # module owns the stable contract error wording
            raise CertificationSetError(str(exc)) from exc
    elif matrix.get("schema") != MATRIX_SCHEMA:
        raise CertificationSetError("certification matrix has the wrong schema")
    return matrix


def _policy_identity(path: Path) -> tuple[str, str]:
    payload = _safe_regular(path, "compatibility policy")
    policy = _strict_json(payload, "compatibility policy")
    policy_id = policy.get("policy_id")
    if policy_id != POLICY_ID:
        raise CertificationSetError("compatibility policy id does not match the matrix")
    policy_sha = hashlib.sha256(payload).hexdigest()
    # The checked-in policy helper uses a canonical JSON representation.  Use
    # it when available, while retaining raw-byte support for isolated tests or
    # copied delivery directories that intentionally contain a small policy.
    helper_path = path.parent / "compatibility_policy.py"
    if not helper_path.is_file():
        helper_path = ROOT / "packaging/linux/compatibility_policy.py"
    if helper_path.is_file():
        try:
            spec = importlib.util.spec_from_file_location("taiji_certification_policy", helper_path)
            if spec is not None and spec.loader is not None:
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                loaded = helper.load_and_validate(path)
                policy_sha = helper.canonical_sha256(loaded)
        except (OSError, KeyError, TypeError, ValueError):
            # A test-only policy need not implement the production contract.
            pass
    return policy_id, policy_sha


def _validate_offline_evidence(
    path: Path,
    *,
    source_commit: str,
    version: str,
    deb_basename: str,
    deb_sha256: str,
    policy_id: str,
    policy_sha256: str,
    snapshot_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, bytes | None], list[dict[str, Any]], str]:
    if snapshot_root is None:
        with tempfile.TemporaryDirectory(prefix="offline-validation-snapshot-") as temporary:
            return _validate_offline_evidence(
                path,
                source_commit=source_commit,
                version=version,
                deb_basename=deb_basename,
                deb_sha256=deb_sha256,
                policy_id=policy_id,
                policy_sha256=policy_sha256,
                snapshot_root=Path(temporary),
            )

    _safe_directory(path, "offline rehearsal evidence directory")
    _safe_directory(snapshot_root, "offline rehearsal private snapshot directory")
    if any(snapshot_root.iterdir()):
        raise CertificationSetError("offline rehearsal private snapshot directory must be empty")

    evidence_basename = "offline-install-rehearsal.json"
    preliminary_payload = _safe_regular(
        path / evidence_basename,
        "offline rehearsal evidence",
    )
    preliminary = _strict_json(preliminary_payload, "offline rehearsal evidence")
    if preliminary.get("schema") != "taiji.offline-install-rehearsal.v1":
        raise CertificationSetError("offline rehearsal evidence schema is invalid")
    preliminary_previous = preliminary.get("previous_release")
    if type(preliminary_previous) is not dict:
        raise CertificationSetError("offline rehearsal evidence requires 完整 N-1 previous_release")
    previous_deb_basename = preliminary_previous.get("deb_basename")
    if (
        type(previous_deb_basename) is not str
        or not previous_deb_basename
        or previous_deb_basename in {".", ".."}
        or Path(previous_deb_basename).name != previous_deb_basename
        or "/" in previous_deb_basename
        or "\\" in previous_deb_basename
    ):
        raise CertificationSetError("offline rehearsal previous DEB basename is invalid")

    directory_before = path.lstat()
    entries = list(path.iterdir())
    if not entries:
        raise CertificationSetError("offline rehearsal evidence directory is empty")
    payloads: dict[str, bytes | None] = {}
    files: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: item.name):
        if (
            entry.name in {".", ".."}
            or Path(entry.name).name != entry.name
            or entry.is_symlink()
            or not entry.is_file()
        ):
            raise CertificationSetError(
                "offline rehearsal evidence directory must contain only root-level regular files"
            )
        is_previous_deb = entry.name == previous_deb_basename
        file_hash, file_size = _stream_regular_snapshot(
            entry,
            snapshot_root / entry.name,
            f"offline rehearsal file {entry.name}",
            max_bytes=(
                MAX_PREVIOUS_RELEASE_DEB_BYTES
                if is_previous_deb
                else MAX_OFFLINE_ATTACHMENT_BYTES
            ),
        )
        payloads[entry.name] = (
            None
            if is_previous_deb
            else _safe_regular(
                snapshot_root / entry.name,
                f"offline rehearsal snapshot file {entry.name}",
            )
        )
        files.append(
            {
                "basename": entry.name,
                "sha256": file_hash,
                "size": file_size,
            }
        )
    directory_after = path.lstat()
    if (
        {entry.name for entry in entries} != {entry.name for entry in path.iterdir()}
        or (
            directory_before.st_dev,
            directory_before.st_ino,
            directory_before.st_mode,
            directory_before.st_mtime_ns,
            directory_before.st_ctime_ns,
        )
        != (
            directory_after.st_dev,
            directory_after.st_ino,
            directory_after.st_mode,
            directory_after.st_mtime_ns,
            directory_after.st_ctime_ns,
        )
    ):
        raise CertificationSetError("offline rehearsal evidence directory changed during snapshot")
    if evidence_basename not in payloads:
        raise CertificationSetError("offline rehearsal evidence JSON is missing")
    payload = payloads[evidence_basename]
    if type(payload) is not bytes:
        raise CertificationSetError("offline rehearsal evidence JSON cannot be the previous DEB")
    data = _strict_json(payload, "offline rehearsal evidence")
    snapshot_previous = data.get("previous_release")
    if (
        type(snapshot_previous) is not dict
        or snapshot_previous.get("deb_basename") != previous_deb_basename
    ):
        raise CertificationSetError("offline rehearsal evidence changed during private snapshot")
    schema = data.get("schema")
    if schema != "taiji.offline-install-rehearsal.v1":
        raise CertificationSetError("offline rehearsal evidence schema is invalid")
    validator = _load_release_validator()
    if validator is None:
        raise CertificationSetError("current offline rehearsal evidence validator is missing")
    binding = validator.BuildBinding(
        source_commit=source_commit,
        version=version,
        architecture="amd64",
        deb_basename=deb_basename,
        deb_sha256=deb_sha256,
        compatibility_policy_id=policy_id,
        compatibility_policy_sha256=policy_sha256,
        electron_executable_sha256="0" * 64,
        desktop_entry_sha256="0" * 64,
    )
    validation_args = argparse.Namespace(
        challenge=data.get("challenge_nonce"),
        source_commit=source_commit,
        deb=Path(deb_basename),
    )
    try:
        validator.validate_offline_evidence_v1(
            data,
            snapshot_root / evidence_basename,
            validation_args,
            binding,
            require_lifecycle=True,
        )
    except Exception as exc:  # validator owns the current v1 contract wording
        raise CertificationSetError(str(exc)) from exc
    if (
        data.get("environment") != CURRENT_OFFLINE_REHEARSAL_ENVIRONMENT
        or data.get("os_id") != "ubuntu"
        or data.get("os_version") != "20.04"
    ):
        raise CertificationSetError(
            "current certification set requires "
            f"environment={CURRENT_OFFLINE_REHEARSAL_ENVIRONMENT}, "
            "os_id=ubuntu, os_version=20.04"
        )
    log_basename = data.get("log_basename")
    if type(log_basename) is not str or log_basename not in payloads:
        raise CertificationSetError("offline rehearsal declared session evidence is missing")
    log_payload = payloads[log_basename]
    if type(log_payload) is not bytes:
        raise CertificationSetError("offline rehearsal session evidence cannot be the previous DEB")
    if hashlib.sha256(log_payload).hexdigest() != data.get("log_sha256"):
        raise CertificationSetError("offline rehearsal declared session evidence hash does not match")
    inventory_sha256 = hashlib.sha256(
        json.dumps(files, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return data, payloads, files, inventory_sha256


def _certification_attachment_limit(basename: str) -> int:
    if basename in LARGE_CERTIFICATION_PNG_BASENAMES:
        return MAX_CERTIFICATION_PNG_BYTES
    return MAX_CERTIFICATION_ATTACHMENT_BYTES


def _validate_attachment(
    category_dir: Path,
    attachment: Any,
) -> tuple[str, str, bytes | AttachmentCopySource]:
    if type(attachment) is not dict or set(attachment) != {"basename", "sha256"}:
        raise CertificationSetError("environment evidence attachment fields are invalid")
    basename = attachment["basename"]
    if (
        type(basename) is not str
        or not basename
        or basename in {".", ".."}
        or Path(basename).name != basename
        or "/" in basename
        or "\\" in basename
    ):
        raise CertificationSetError("environment evidence attachment path escapes its category directory")
    expected = _require_sha(attachment["sha256"], "environment evidence attachment SHA256")
    path = category_dir / basename
    limit = _certification_attachment_limit(basename)
    if basename in LARGE_CERTIFICATION_PNG_BASENAMES:
        actual, _size = _sha256_bounded_stable_regular_file(
            path,
            "environment evidence attachment",
            max_bytes=limit,
        )
        payload_or_source: bytes | AttachmentCopySource = (path, actual, limit)
    else:
        payload_or_source = _safe_regular(
            path,
            "environment evidence attachment",
            max_bytes=limit,
        )
        actual = hashlib.sha256(payload_or_source).hexdigest()
    if actual != expected:
        raise CertificationSetError("environment evidence attachment hash does not match")
    return basename, actual, payload_or_source


def _read_records(
    records_dir: Path,
    matrix: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, bytes | AttachmentCopySource]]:
    _safe_directory(records_dir, "records directory")
    expected_categories = {
        item["id"]
        for item in matrix["positive_categories"] + matrix["negative_boundaries"]
    }
    entries = list(records_dir.iterdir())
    if any(item.is_symlink() for item in entries):
        raise CertificationSetError("records directory cannot contain symlink category entries")
    actual_categories = {item.name for item in entries if item.is_dir()}
    if actual_categories != expected_categories or len(entries) != len(expected_categories):
        missing = sorted(expected_categories - actual_categories)
        extra = sorted(actual_categories - expected_categories)
        raise CertificationSetError(f"certification category set is incomplete or has extras: missing={missing} extra={extra}")
    records: list[dict[str, Any]] = []
    copied_payloads: dict[str, bytes | AttachmentCopySource] = {}
    release_validator = (
        _load_release_validator()
        if matrix.get("schema") == "taiji-linux-certification-matrix/v2"
        else None
    )
    if matrix.get("schema") == "taiji-linux-certification-matrix/v2" and release_validator is None:
        raise CertificationSetError("current positive certification bundle validator is missing")
    for category_id in sorted(expected_categories):
        category_dir = records_dir / category_id
        _safe_directory(category_dir, f"category directory {category_id}")
        record_path = category_dir / RECORD_BASENAME
        payload = _safe_regular(record_path, f"category record {category_id}")
        record = _strict_json(payload, f"category record {category_id}")
        if record.get("category_id") != category_id:
            raise CertificationSetError("category record path and category_id do not match")
        attachments = record.get("attachments")
        if type(attachments) is not list:
            raise CertificationSetError("environment evidence attachments must be a list")
        allowed = {RECORD_BASENAME}
        category_attachment_payloads: dict[str, bytes] = {}
        for attachment in attachments:
            basename, digest, attachment_payload = _validate_attachment(category_dir, attachment)
            allowed.add(basename)
            copied_payloads[f"{category_id}/{basename}"] = attachment_payload
            if type(attachment_payload) is bytes:
                category_payload = attachment_payload
            else:
                source, expected_digest, limit = attachment_payload
                category_payload = _safe_regular(
                    source,
                    f"environment evidence attachment {basename}",
                    max_bytes=limit,
                )
                if hashlib.sha256(category_payload).hexdigest() != expected_digest:
                    raise CertificationSetError(
                        "environment evidence attachment changed before recursive validation"
                    )
            if hashlib.sha256(category_payload).hexdigest() != digest:
                raise CertificationSetError(
                    "environment evidence attachment changed before recursive validation"
                )
            category_attachment_payloads[basename] = category_payload
        directory_entries = {item.name for item in category_dir.iterdir()}
        if directory_entries != allowed:
            raise CertificationSetError(f"category {category_id} must contain exactly its record and declared attachments")
        if CONTRACT is None:
            raise CertificationSetError("current environment evidence contract is missing")
        try:
            CONTRACT.validate_environment_record(record, matrix)
            if record.get("category_kind") == "negative":
                CONTRACT.validate_negative_preflight_attachment(
                    record,
                    matrix,
                    category_attachment_payloads["preflight-result.json"],
                )
                CONTRACT.validate_negative_business_data_attachment(
                    record,
                    matrix,
                    category_attachment_payloads["business-data-inventory.json"],
                )
        except Exception as exc:
            raise CertificationSetError(str(exc)) from exc
        if record.get("category_kind") == "positive" and release_validator is not None:
            try:
                release_validator.validate_positive_certification_bundle(
                    record,
                    category_attachment_payloads,
                )
            except Exception as exc:
                raise CertificationSetError(str(exc)) from exc
        records.append(record)
        copied_payloads[f"{category_id}/{RECORD_BASENAME}"] = payload
    return records, copied_payloads


def _require_positive_pass(records: list[dict[str, Any]], matrix: dict[str, Any]) -> None:
    categories = {
        item["id"]: item for item in matrix["positive_categories"]
    }
    for record in records:
        category = categories.get(record["category_id"])
        if category is None:
            continue
        checks = record.get("checks")
        required = set(category["required_business_checks"]) | set(category["required_lifecycle_checks"])
        if type(checks) is not dict or any(checks.get(key) != "PASS" for key in required):
            raise CertificationSetError(
                f"positive category {record['category_id']} requires every check to be PASS"
            )


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_new_file(path: Path, payload: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CertificationSetError(f"failed to write {path}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_directory_noreplace(source: Path, destination: Path) -> None:
    if source.parent != destination.parent:
        raise CertificationSetError(
            "atomic certification publication requires one parent directory"
        )
    if os.path.lexists(destination):
        raise CertificationSetError("output path appeared during assembly; refusing overwrite")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(str(source.parent), flags)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform.startswith("linux"):
            primitive = getattr(libc, "renameat2", None)
            if primitive is None:
                raise CertificationSetError("renameat2 no-replace primitive is unavailable")
            primitive.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            primitive.restype = ctypes.c_int
            result = primitive(
                parent_fd,
                os.fsencode(source.name),
                parent_fd,
                os.fsencode(destination.name),
                1,
            )
        elif sys.platform == "darwin":
            primitive = getattr(libc, "renameatx_np", None)
            if primitive is None:
                raise CertificationSetError("renameatx_np no-replace primitive is unavailable")
            primitive.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            primitive.restype = ctypes.c_int
            result = primitive(
                parent_fd,
                os.fsencode(source.name),
                parent_fd,
                os.fsencode(destination.name),
                0x00000004,
            )
        else:
            raise CertificationSetError("no supported no-replace publication primitive")
        if result != 0:
            error = ctypes.get_errno()
            raise CertificationSetError(
                "output path appeared during assembly; refusing overwrite: {}".format(
                    os.strerror(error)
                )
            )
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def assemble(args: argparse.Namespace) -> Path:
    for path, label in (
        (args.matrix, "certification matrix"),
        (args.records_dir, "records directory"),
        (args.offline_evidence, "offline rehearsal evidence"),
        (args.deb, "candidate DEB"),
        (args.policy, "compatibility policy"),
        (args.challenge_envelope, "certification challenge envelope"),
    ):
        if not path.is_absolute():
            raise CertificationSetError(f"{label} path must be absolute")
    if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
        raise CertificationSetError("output path must be a new non-existing directory; refusing overwrite")
    if args.output.parent.is_symlink() or not args.output.parent.is_dir():
        raise CertificationSetError("output parent directory must be an existing real directory")

    matrix = _load_matrix(args.matrix)
    policy_id, policy_sha = _policy_identity(args.policy)
    deb_payload = _safe_regular(args.deb, "candidate DEB", max_bytes=1024 * 1024 * 1024)
    deb_sha = hashlib.sha256(deb_payload).hexdigest()
    deb_name = args.deb.name
    if not re.fullmatch(r"taiji-agent_[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}_amd64\.deb", deb_name):
        raise CertificationSetError("candidate DEB basename is invalid")
    version = deb_name[len("taiji-agent_") : -len("_amd64.deb")]
    if not VERSION_RE.fullmatch(version):
        raise CertificationSetError("candidate DEB version is invalid")
    records, payloads = _read_records(args.records_dir, matrix)
    if CONTRACT is not None:
        try:
            CONTRACT.validate_environment_records(records, matrix)
        except Exception as exc:
            raise CertificationSetError(str(exc)) from exc
    bindings = {
        "source_commit": records[0]["source_commit"],
        "version": records[0]["version"],
        "architecture": records[0]["architecture"],
        "deb_basename": records[0]["deb_basename"],
        "deb_sha256": records[0]["deb_sha256"],
        "compatibility_policy_id": records[0]["compatibility_policy_id"],
        "compatibility_policy_sha256": records[0]["compatibility_policy_sha256"],
    }
    if bindings["architecture"] != "amd64" or bindings["deb_basename"] != deb_name or bindings["deb_sha256"] != deb_sha:
        raise CertificationSetError("environment records do not bind the candidate DEB")
    if bindings["version"] != version or not COMMIT_RE.fullmatch(bindings["source_commit"]):
        raise CertificationSetError("environment records version or source commit is invalid")
    if bindings["compatibility_policy_id"] != policy_id or bindings["compatibility_policy_sha256"] != policy_sha:
        raise CertificationSetError("environment records do not bind the supplied compatibility policy")
    challenge_helper = _load_challenge_helper()
    challenge_envelope = challenge_helper.load_envelope_file(args.challenge_envelope)
    challenge = challenge_envelope.get("nonce")
    if any(record.get("challenge_nonce") != challenge for record in records):
        raise CertificationSetError("every environment record must bind the certification challenge")
    _require_positive_pass(records, matrix)
    with tempfile.TemporaryDirectory(
        prefix=".offline-certification-snapshot-",
        dir=args.output.parent,
    ) as snapshot_temporary:
        snapshot_container = Path(snapshot_temporary)
        os.chmod(snapshot_container, 0o700)
        offline_snapshot_root = snapshot_container / "offline-rehearsal"
        offline_snapshot_root.mkdir(mode=0o700)
        offline, offline_payloads, offline_files, offline_inventory_sha = _validate_offline_evidence(
            args.offline_evidence,
            source_commit=bindings["source_commit"],
            version=version,
            deb_basename=deb_name,
            deb_sha256=deb_sha,
            policy_id=policy_id,
            policy_sha256=policy_sha,
            snapshot_root=offline_snapshot_root,
        )
        if offline.get("challenge_nonce") != challenge:
            raise CertificationSetError(
                "offline rehearsal evidence must bind the certification challenge"
            )
        positive = [
            {
                "category_id": record["category_id"],
                "compatibility": "CERTIFIED",
                "record_basename": f"records/{record['category_id']}/{RECORD_BASENAME}",
                "record_sha256": hashlib.sha256(payloads[f"{record['category_id']}/{RECORD_BASENAME}"]).hexdigest(),
            }
            for record in sorted(records, key=lambda item: item["category_id"])
            if record["category_kind"] == "positive"
        ]
        negative = [
            {
                "category_id": record["category_id"],
                "compatibility": "BLOCKED",
                "record_basename": f"records/{record['category_id']}/{RECORD_BASENAME}",
                "record_sha256": hashlib.sha256(payloads[f"{record['category_id']}/{RECORD_BASENAME}"]).hexdigest(),
            }
            for record in sorted(records, key=lambda item: item["category_id"])
            if record["category_kind"] == "negative"
        ]
        if len(positive) != 6 or len(negative) != 6:
            raise CertificationSetError("certification set requires exactly six positive and six negative records")
        matrix_sha = hashlib.sha256(_safe_regular(args.matrix, "certification matrix")).hexdigest()
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        offline_evidence_payload = offline_payloads["offline-install-rehearsal.json"]
        if type(offline_evidence_payload) is not bytes:
            raise CertificationSetError("offline rehearsal evidence snapshot is invalid")
        challenge_helper.verify_envelope(
            challenge_envelope,
            purpose="certification",
            source_commit=bindings["source_commit"],
            deb_basename=deb_name,
            deb_sha256=deb_sha,
            require_active=True,
            evidence_times=tuple(
                value
                for value in (
                    generated_at,
                    offline.get("generated_at_utc"),
                    *(record.get("generated_at_utc") for record in records),
                )
                if type(value) is str
            ),
            evidence_not_after=generated_at,
        )
        certification_set = {
            "schema": SET_SCHEMA,
            "generated_at_utc": generated_at,
            "challenge_nonce": challenge,
            "challenge_envelope": challenge_envelope,
            **bindings,
            "certification_profile": {
                "matrix_schema": MATRIX_SCHEMA,
                "matrix_sha256": matrix_sha,
                "positive_category_count": 6,
                "negative_boundary_count": 6,
            },
            "offline_rehearsal": {
                "directory_basename": "offline-rehearsal",
                "evidence_basename": "offline-install-rehearsal.json",
                "evidence_sha256": hashlib.sha256(offline_evidence_payload).hexdigest(),
                "files": offline_files,
                "inventory_sha256": offline_inventory_sha,
                "status": "PASS",
            },
            "environments": positive,
            "negative_boundaries": negative,
        }
        output_payload = _canonical_json(certification_set)
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{args.output.name}.tmp-", dir=args.output.parent))
        try:
            os.chmod(temp_dir, 0o700)
            records_root = temp_dir / "records"
            records_root.mkdir(mode=0o700)
            for relative, payload in sorted(payloads.items()):
                destination = records_root / relative
                destination.parent.mkdir(mode=0o700, exist_ok=True)
                if type(payload) is bytes:
                    _write_new_file(destination, payload)
                else:
                    source, expected_digest, limit = payload
                    copied_digest, _size = _stream_regular_snapshot(
                        source,
                        destination,
                        f"certification attachment {relative}",
                        max_bytes=limit,
                    )
                    if copied_digest != expected_digest:
                        raise CertificationSetError(
                            f"certification attachment changed before publication: {relative}"
                        )
            os.rename(offline_snapshot_root, temp_dir / "offline-rehearsal")
            _write_new_file(temp_dir / "certification-set.json", output_payload)
            _publish_directory_noreplace(temp_dir, args.output)
            temp_dir = None  # type: ignore[assignment]
        except FileExistsError as exc:
            raise CertificationSetError("output path appeared during assembly; refusing overwrite") from exc
        finally:
            if temp_dir is not None and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
    if hashlib.sha256(_safe_regular(args.deb, "candidate DEB", max_bytes=1024 * 1024 * 1024)).hexdigest() != deb_sha:
        raise CertificationSetError("candidate DEB changed while assembling certification set")
    return args.output / "certification-set.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--records-dir", required=True, type=Path)
    parser.add_argument("--offline-evidence", required=True, type=Path)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--challenge-envelope", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        output = assemble(parse_args(argv))
    except (CertificationSetError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"certification-set-assembly-failed\t{exc}", file=sys.stderr)
        return 1
    print(f"certification-set-assembled\t{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
