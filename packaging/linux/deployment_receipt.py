#!/usr/bin/env python3
"""Strict, privacy-safe deployment receipt handling.

The receipt is deliberately a small, immutable contract.  It contains the
identity of the candidate DEB and stable lifecycle/error states, but never
machine identity, user identity, raw commands, credentials, or exception
text.  Writes are committed with a same-directory temporary file, fsync and
``os.replace`` so a power loss cannot expose a partially written JSON file.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "taiji-linux-deployment-receipt/v1"
ADMISSION_RECORD_SCHEMA = "taiji-linux-deployment-admission/v1"
ADMISSION_RECORD_FIELDS = frozenset(
    {
        "schema",
        "admission_mode",
        "challenge_digest",
        "source_commit",
        "deb_basename",
        "deb_sha256",
        "compatibility_policy_id",
        "compatibility_policy_sha256",
        "generated_at_utc",
    }
)
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "deployment_id",
        "operation",
        "result",
        "source_commit",
        "version_before",
        "version_requested",
        "version_after",
        "architecture",
        "deb_basename",
        "deb_sha256",
        "compatibility_policy_id",
        "compatibility_policy_sha256",
        "preflight",
        "dpkg_status_before",
        "dpkg_status_after",
        "native_verify",
        "started_at_utc",
        "finished_at_utc",
        "error_stage",
        "error_code",
        "rollback_transaction_id",
    }
)
OPERATIONS = frozenset({"fresh_install", "reinstall", "upgrade", "rollback"})
RESULTS = frozenset(
    {
        "installed",
        "reinstalled",
        "upgraded",
        "rolled_back",
        "blocked",
        "manual_recovery_required",
    }
)
SUCCESS_RESULT_FOR_OPERATION = {
    "fresh_install": "installed",
    "reinstall": "reinstalled",
    "upgrade": "upgraded",
    "rollback": "rolled_back",
}
ERROR_STAGES = frozenset(
    {
        "preflight",
        "admission",
        "lock",
        "verification",
        "staging",
        "dpkg",
        "native_verify",
        "rollback",
        "transaction",
        "internal",
    }
)
PREFLIGHT_VALUES = frozenset({"PASS", "BLOCKED"})
NATIVE_VERIFY_VALUES = frozenset({"PASS", "FAIL", "NOT_RUN"})
STATUS_RE = re.compile(
    r"^(?:not-installed|installed|half-installed|half-configured|unpacked|config-files|"
    r"triggers-awaited|triggers-pending|unknown|unchanged|failed|removed)$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
DEPLOYMENT_ID_RE = re.compile(r"^dep-[0-9a-f]{16,64}$")
TXN_ID_RE = re.compile(r"^txn-[a-z0-9][a-z0-9-]{2,127}$")
POLICY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")
DEB_RE = re.compile(r"^taiji-agent_([A-Za-z0-9.+:~_-]+)_amd64\.deb$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,127}$")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# Reject accidental leakage even if a caller tries to smuggle it into an
# otherwise schema-valid string.  The contract intentionally errs on the
# side of blocking rather than retaining raw diagnostics.
FORBIDDEN_VALUE_RE = re.compile(
    r"(?i)(?:hostname|username|user[_ -]?name|home=|/home/|/root/|127\.0\.0\.1|"
    r"\b(?:password|passwd|passphrase|secret|token|bearer|api[_ -]?key|private[_ -]?key)\b|"
    r"(?:^|\s)(?:sudo|apt(?:-get)?|dpkg(?:-query)?|rm|sh|bash|python3)(?:\s|$)|"
    r"(?:traceback|exception|command not found|no such file or directory))"
)


class ReceiptError(ValueError):
    """Raised when a receipt violates the public v1 contract."""


def utc_now() -> str:
    """Return a compact UTC timestamp accepted by the receipt schema."""

    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_string(value: Any, field: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value:
        raise ReceiptError(f"{field} must be a non-empty string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ReceiptError(f"{field} has invalid format")
    if FORBIDDEN_VALUE_RE.search(value):
        raise ReceiptError(f"{field} contains forbidden diagnostic content")
    return value


def _optional_string(value: Any, field: str, pattern: re.Pattern[str] | None = None) -> str | None:
    if value is None:
        return None
    return _require_string(value, field, pattern)


def _walk_forbidden(value: Any, path: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ReceiptError(f"{path} contains non-string key")
            _walk_forbidden(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden(child, f"{path}[{index}]")
        return
    # ``error_stage`` is a closed enum.  The literal stage ``dpkg`` is safe
    # once enum validation accepts it, even though the generic scanner rejects
    # command tokens in free-form diagnostic strings.
    if path.endswith(".error_stage") and isinstance(value, str) and value in ERROR_STAGES:
        return
    if isinstance(value, str) and FORBIDDEN_VALUE_RE.search(value):
        raise ReceiptError(f"{path} contains forbidden diagnostic content")


def validate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached copy of a receipt.

    The returned mapping is JSON-safe and has the exact v1 key set.  No
    normalisation is performed; callers can compare it byte-for-byte after
    canonical JSON encoding if desired.
    """

    if not isinstance(receipt, Mapping):
        raise ReceiptError("receipt must be an object")
    actual_fields = set(receipt)
    if actual_fields != set(RECEIPT_FIELDS):
        missing = sorted(RECEIPT_FIELDS - actual_fields)
        extra = sorted(actual_fields - RECEIPT_FIELDS)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ReceiptError("receipt fields are not exact: " + " ".join(details))
    _walk_forbidden(receipt)

    _require_string(receipt["schema"], "schema")
    if receipt["schema"] != SCHEMA:
        raise ReceiptError("unsupported receipt schema")
    _require_string(receipt["deployment_id"], "deployment_id", DEPLOYMENT_ID_RE)
    operation = _require_string(receipt["operation"], "operation")
    if operation not in OPERATIONS:
        raise ReceiptError("unsupported operation")
    result = _require_string(receipt["result"], "result")
    if result not in RESULTS:
        raise ReceiptError("unsupported result")
    expected_success = SUCCESS_RESULT_FOR_OPERATION[operation]
    allowed_successes = {expected_success}
    # An upgrade may fail after dpkg mutation and close through the explicit
    # N-1 recovery path.  That is a successful recovery outcome, but it still
    # carries the original failure stage/code for support automation.
    if operation == "upgrade":
        allowed_successes.add("rolled_back")
    if result in {"installed", "reinstalled", "upgraded", "rolled_back"} and result not in allowed_successes:
        raise ReceiptError("operation/result combination is invalid")

    _require_string(receipt["source_commit"], "source_commit", COMMIT_RE)
    for field in ("version_before", "version_requested", "version_after"):
        value = receipt[field]
        if field == "version_requested":
            _require_string(value, field, VERSION_RE)
        else:
            _optional_string(value, field, VERSION_RE)
    _require_string(receipt["architecture"], "architecture")
    if receipt["architecture"] != "amd64":
        raise ReceiptError("architecture must be amd64")
    _require_string(receipt["deb_basename"], "deb_basename", DEB_RE)
    _require_string(receipt["deb_sha256"], "deb_sha256", SHA256_RE)
    _require_string(receipt["compatibility_policy_id"], "compatibility_policy_id", POLICY_ID_RE)
    _require_string(receipt["compatibility_policy_sha256"], "compatibility_policy_sha256", SHA256_RE)
    _require_string(receipt["preflight"], "preflight")
    if receipt["preflight"] not in PREFLIGHT_VALUES:
        raise ReceiptError("invalid preflight value")
    for field in ("dpkg_status_before", "dpkg_status_after"):
        _require_string(receipt[field], field, STATUS_RE)
    _require_string(receipt["native_verify"], "native_verify")
    if receipt["native_verify"] not in NATIVE_VERIFY_VALUES:
        raise ReceiptError("invalid native_verify value")
    for field in ("started_at_utc", "finished_at_utc"):
        _require_string(receipt[field], field, TIMESTAMP_RE)
    # Validate the closed enum before the generic diagnostic scanner.  Values
    # such as the legitimate stage ``dpkg`` intentionally match the scanner's
    # command-token guard but are safe because they cannot carry free text.
    error_stage = receipt["error_stage"]
    if error_stage is not None:
        if type(error_stage) is not str or error_stage not in ERROR_STAGES:
            raise ReceiptError("invalid error_stage value")
    _optional_string(receipt["error_code"], "error_code", ERROR_CODE_RE)
    _optional_string(receipt["rollback_transaction_id"], "rollback_transaction_id", TXN_ID_RE)

    # Successful records must not carry an unexplained failure code; blocked
    # and recovery records must carry one stable code for support automation.
    if result in {"blocked", "manual_recovery_required"}:
        if receipt["error_stage"] is None or receipt["error_code"] is None:
            raise ReceiptError("failure receipts require error_stage and error_code")
    elif result == "rolled_back" and operation == "upgrade":
        if receipt["error_stage"] is None or receipt["error_code"] is None:
            raise ReceiptError("rolled-back upgrade receipts require recovery cause")
    elif receipt["error_stage"] is not None or receipt["error_code"] is not None:
        raise ReceiptError("successful receipts cannot carry an error")

    return dict(receipt)


def validate_admission_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the same-directory, privacy-safe admission record."""

    if not isinstance(record, Mapping) or set(record) != set(ADMISSION_RECORD_FIELDS):
        raise ReceiptError("admission record fields are not exact")
    _walk_forbidden(record)
    _require_string(record["schema"], "schema")
    if record["schema"] != ADMISSION_RECORD_SCHEMA:
        raise ReceiptError("unsupported admission record schema")
    mode = _require_string(record["admission_mode"], "admission_mode")
    if mode not in {"certification", "release"}:
        raise ReceiptError("invalid admission_mode")
    _require_string(record["challenge_digest"], "challenge_digest", SHA256_RE)
    _require_string(record["source_commit"], "source_commit", COMMIT_RE)
    _require_string(record["deb_basename"], "deb_basename", DEB_RE)
    _require_string(record["deb_sha256"], "deb_sha256", SHA256_RE)
    _require_string(record["compatibility_policy_id"], "compatibility_policy_id", POLICY_ID_RE)
    _require_string(record["compatibility_policy_sha256"], "compatibility_policy_sha256", SHA256_RE)
    _require_string(record["generated_at_utc"], "generated_at_utc", TIMESTAMP_RE)
    return dict(record)


def _validated_parent(path: Path, label: str) -> Path:
    """Return an absolute parent whose existing components are real dirs.

    Checking only ``path.parent.is_symlink()`` misses a nested symlink such as
    ``trusted-link/subdir/receipt.json``.  Receipt and admission records are
    management evidence, so fail closed rather than allowing that path to
    redirect through an unreviewed ancestor.
    """

    directory = Path(os.path.abspath(os.fspath(path)))
    if not directory.exists() or not directory.is_dir():
        raise ReceiptError(f"{label} parent must be a real directory")
    current = Path(directory.anchor)
    for component in directory.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ReceiptError(f"{label} parent cannot be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode):
            # System paths such as macOS /var may be root-owned symlinks.  A
            # root-owned component is an explicit system layout choice; an
            # untrusted owner is not allowed to redirect evidence writes.
            if metadata.st_uid != 0:
                raise ReceiptError(f"{label} parent cannot contain untrusted symlink components")
            current = Path(os.path.realpath(current))
            metadata = current.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ReceiptError(f"{label} parent must contain only directories")
    return directory


def write_admission_record_atomic(path: str | os.PathLike[str], record: Mapping[str, Any]) -> Path:
    """Atomically write an admission record with the same 0600 guarantees."""

    validated = validate_admission_record(record)
    requested = Path(path)
    if requested.name in {"", ".", ".."}:
        raise ReceiptError("admission record path must be a file")
    directory = _validated_parent(requested.parent, "admission record")
    destination = directory / requested.name
    if destination.is_symlink():
        raise ReceiptError("admission record destination cannot be a symlink")
    encoded = (json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary: Path | None = None
    fd: int | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=directory)
        temporary = Path(temp_name)
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
        _fsync_directory(directory)
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return destination


def _fsync_directory(directory: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, os.O_RDONLY | flags)
    except OSError:
        # Some platforms (notably macOS test hosts) do not permit opening a
        # directory as an fsync target.  The file fsync and atomic replace are
        # still performed; Linux target installs take this branch normally.
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_receipt_atomic(path: str | os.PathLike[str], receipt: Mapping[str, Any]) -> Path:
    """Atomically write a validated receipt with mode 0600.

    The temporary file is created in the destination directory, flushed and
    fsynced, then replaced.  A final chmod protects against a pre-existing
    destination with a permissive mode.
    """

    requested = Path(path)
    if requested.name in {"", ".", ".."}:
        raise ReceiptError("receipt path must be a file")
    directory = _validated_parent(requested.parent, "receipt")
    destination = directory / requested.name
    if destination.is_symlink():
        raise ReceiptError("receipt destination cannot be a symlink")
    validated = validate_receipt(receipt)
    encoded = (json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

    temporary: Path | None = None
    fd: int | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=directory)
        temporary = Path(temp_name)
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
        _fsync_directory(directory)
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return destination


def _parse_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt JSON cannot be read") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "write"))
    parser.add_argument("path", type=Path, help="receipt JSON path")
    args = parser.parse_args(argv)
    try:
        payload = _parse_json(args.path)
        validate_receipt(payload)
        if args.command == "write":
            write_receipt_atomic(args.path, payload)
        print("deployment-receipt-valid")
        return 0
    except ReceiptError as exc:
        print(f"deployment-receipt-invalid:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
