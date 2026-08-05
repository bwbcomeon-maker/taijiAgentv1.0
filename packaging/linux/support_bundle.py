#!/usr/bin/env python3
"""Create a small, allowlist-only Taiji Agent support bundle.

The collector is intentionally boring: it reads two bounded JSON records,
projects them onto a closed public schema, records stable collector failures,
and writes a four-member tarball.  It never walks a user's home directory,
reads logs, inspects processes, or copies attachments/databases.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Mapping


SUPPORT_SCHEMA = "taiji.product.support-bundle.v1"
MANIFEST_SCHEMA = "taiji-agent-support-bundle-manifest/v1"
MAX_INPUT_BYTES = 1024 * 1024
TIMESTAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,127}$")
POLICY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")
DEB_RE = re.compile(r"^taiji-agent_[A-Za-z0-9.+:~_-]+_amd64\.deb$")
# Public fields are identifiers/statuses, never filesystem locations or URLs.
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+@%(),= -]{1,256}$")

ALLOWED_MEMBER_NAMES = frozenset(
    {
        "bundle-manifest.json",
        "deployment-receipt.json",
        "support-bundle.json",
        "collection-errors.txt",
    }
)
STABLE_ERROR_CODES = {
    "deployment": "DEPLOYMENT_RECEIPT_UNAVAILABLE",
    "support": "SUPPORT_DATA_UNAVAILABLE",
    "network": "NETWORK_UNAVAILABLE",
    "desktop": "DESKTOP_STATE_UNAVAILABLE",
    "dependencies": "DEPENDENCY_STATE_UNAVAILABLE",
}
FORBIDDEN_TEXT_RE = re.compile(
    r"(?i)(?:api[_ -]?key|token|password|passwd|passphrase|secret|bearer|private[_ -]?key|"
    r"/home/|/root/|/opt/|[A-Za-z]:[\\/]|127\.0\.0\.1|localhost|hostname|username|"
    r"mac[_ -]?address|serial(?:[_ -]?number)?|traceback|exception|pgrep|tail\s+-[0-9]+)"
)


class SupportBundleError(ValueError):
    """Raised for unsafe input or an output contract violation."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SupportBundleError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> dict[str, Any]:
    payload = _read_bounded(path, label)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, SupportBundleError) as exc:
        raise SupportBundleError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise SupportBundleError(f"{label} must be a JSON object")
    return value


def _read_bounded(path: Path, label: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SupportBundleError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise SupportBundleError(f"{label} must be a regular file")
    if info.st_nlink != 1:
        raise SupportBundleError(f"{label} must not be a hardlink")
    if info.st_size > MAX_INPUT_BYTES:
        raise SupportBundleError(f"{label} exceeds the size limit")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SupportBundleError(f"{label} cannot be read") from exc
    if len(data) != info.st_size:
        raise SupportBundleError(f"{label} changed while reading")
    return data


def _safe_scalar(value: Any, *, pattern: re.Pattern[str] | None = None) -> Any:
    if type(value) is bool or value is None:
        return value
    if type(value) is int and not isinstance(value, bool):
        return value if 0 <= value <= 10**9 else None
    if not isinstance(value, str) or len(value) > 256:
        return None
    if FORBIDDEN_TEXT_RE.search(value):
        return None
    if pattern is not None and not pattern.fullmatch(value):
        return None
    if not SAFE_TOKEN_RE.fullmatch(value):
        return None
    return value


def _safe_mapping(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in sorted(allowed):
        if key not in value:
            continue
        child = value[key]
        if isinstance(child, Mapping):
            nested = _safe_mapping(child, allowed)
            if nested:
                result[key] = nested
        elif isinstance(child, list):
            safe_list = [_safe_scalar(item) for item in child]
            result[key] = [item for item in safe_list if item is not None]
        else:
            safe = _safe_scalar(child)
            if safe is not None:
                result[key] = safe
    return result


def _project_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema",
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
        "error_stage",
        "error_code",
        "rollback_transaction_id",
        "started_at_utc",
        "finished_at_utc",
    }
    projected = _safe_mapping(value, allowed)
    projected.setdefault("schema", "taiji-linux-deployment-receipt/v1")
    return projected


def _project_support(value: Mapping[str, Any]) -> dict[str, Any]:
    top_allowed = {
        "schema",
        "product_version",
        "deb_sha256",
        "compatibility_policy_id",
        "compatibility_policy_sha256",
        "os",
        "dependencies",
        "collectors",
        "status",
        "error_stage",
        "error_code",
        "generated_at_utc",
    }
    projected = _safe_mapping(value, top_allowed)
    projected["schema"] = SUPPORT_SCHEMA
    if isinstance(value.get("os"), Mapping):
        projected["os"] = _safe_mapping(value["os"], {"id", "version", "architecture", "kernel", "glibc"})
    if isinstance(value.get("dependencies"), Mapping):
        projected["dependencies"] = _safe_mapping(value["dependencies"], {"dpkg", "systemd", "apt"})
    if isinstance(value.get("collectors"), Mapping):
        collectors: dict[str, Any] = {}
        for key in sorted(value["collectors"]):
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key):
                continue
            child = value["collectors"][key]
            collectors[key] = _safe_mapping(child, {"status", "code", "version", "configured", "available"})
        projected["collectors"] = collectors
    return projected


def _utc_timestamp() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, mode)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _tar_member(name: str, payload: bytes) -> tarfile.TarInfo:
    if name not in ALLOWED_MEMBER_NAMES:
        raise SupportBundleError(f"unsupported bundle member: {name}")
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def _validate_output_dir(path: Path) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SupportBundleError("output directory is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise SupportBundleError("output directory must be a real directory")
    return path.resolve()


def _error_line(collector_id: str) -> str:
    code = STABLE_ERROR_CODES.get(collector_id, "COLLECTOR_UNAVAILABLE")
    return f"collector={collector_id} code={code}\n"


def create_bundle(
    output_dir: Path,
    *,
    deployment_receipt: Mapping[str, Any] | None = None,
    support_data: Mapping[str, Any] | None = None,
    failed_collectors: list[str] | None = None,
    timestamp: str | None = None,
) -> tuple[Path, Path]:
    """Create and return ``(tarball, sidecar)`` using only public fields."""

    output = _validate_output_dir(Path(output_dir))
    stamp = timestamp or _utc_timestamp()
    if not TIMESTAMP_RE.fullmatch(stamp):
        raise SupportBundleError("timestamp has invalid format")
    receipt = _project_receipt(deployment_receipt or {})
    support = _project_support(support_data or {})
    failed = sorted(set(failed_collectors or []))
    errors = "".join(_error_line(item) for item in failed if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", item))
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generated_at_utc": stamp,
        "product_version": support.get("product_version", receipt.get("version_after", "unknown")),
        "deb_sha256": support.get("deb_sha256", receipt.get("deb_sha256", "unknown")),
        "compatibility_policy_id": support.get(
            "compatibility_policy_id", receipt.get("compatibility_policy_id", "unknown")
        ),
        "compatibility_policy_sha256": support.get(
            "compatibility_policy_sha256", receipt.get("compatibility_policy_sha256", "unknown")
        ),
        "os": support.get("os", {"architecture": "amd64"}),
        "dependencies": support.get("dependencies", {}),
        "collectors": {item: {"status": "failed", "code": STABLE_ERROR_CODES.get(item, "COLLECTOR_UNAVAILABLE")} for item in failed},
    }
    members = {
        "bundle-manifest.json": _canonical_json(manifest),
        "deployment-receipt.json": _canonical_json(receipt),
        "support-bundle.json": _canonical_json(support),
        "collection-errors.txt": errors.encode("utf-8"),
    }
    bundle_name = f"taiji-agent-support-{stamp}.tar.gz"
    bundle = output / bundle_name
    sidecar = Path(f"{bundle}.sha256")
    if bundle.exists() or sidecar.exists():
        raise SupportBundleError("bundle output already exists")
    with tarfile.open(bundle, mode="w:gz") as archive:
        for name in sorted(members):
            payload = members[name]
            archive.addfile(_tar_member(name, payload), io.BytesIO(payload))
    os.chmod(bundle, 0o600)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    _write_bytes(sidecar, f"{digest}  {bundle.name}\n".encode("ascii"), mode=0o600)
    return bundle, sidecar


def create_from_source(
    output_dir: Path,
    source_dir: Path,
    *,
    failed_collectors: list[str] | None = None,
    timestamp: str | None = None,
) -> tuple[Path, Path]:
    source = _validate_output_dir(Path(source_dir))
    receipt_path = source / "deployment-receipt.json"
    support_path = source / "support-bundle.json"
    failed = list(failed_collectors or [])

    def load_collector(path: Path, label: str, collector_id: str) -> dict[str, Any]:
        try:
            path.lstat()
        except FileNotFoundError:
            failed.append(collector_id)
            return {}
        try:
            return _load_json(path, label)
        except SupportBundleError:
            # A present but unsafe input is a hard failure: silently ignoring a
            # symlink, hardlink, FIFO, or oversized record would weaken the
            # collection boundary.  Ordinary parse/read failures remain
            # best-effort and are represented by a stable collector code.
            info = path.lstat()
            unsafe = path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_INPUT_BYTES
            if unsafe:
                raise
            failed.append(collector_id)
            return {}

    receipt = load_collector(receipt_path, "deployment receipt", "deployment")
    support = load_collector(support_path, "support data", "support")
    return create_bundle(
        output_dir,
        deployment_receipt=receipt,
        support_data=support,
        failed_collectors=failed,
        timestamp=timestamp,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--fail-collector", action="append", default=[])
    parser.add_argument("--timestamp")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.source_dir is None:
            result = create_bundle(
                args.output_dir,
                support_data={"schema": SUPPORT_SCHEMA, "os": {"architecture": "amd64"}},
                failed_collectors=args.fail_collector,
                timestamp=args.timestamp,
            )
        else:
            result = create_from_source(
                args.output_dir,
                args.source_dir,
                failed_collectors=args.fail_collector,
                timestamp=args.timestamp,
            )
    except (OSError, SupportBundleError, ValueError, TypeError) as exc:
        print(f"support-bundle-failed\t{exc}", file=sys.stderr)
        return 1
    print(f"support-bundle-created\t{result[0]}")
    print(f"support-bundle-checksum\t{result[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
