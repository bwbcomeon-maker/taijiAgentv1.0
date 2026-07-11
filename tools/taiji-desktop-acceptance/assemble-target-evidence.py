#!/usr/bin/env python3
"""Assemble unsigned, manifest-bound Taiji target desktop acceptance evidence."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, urlsplit


ELECTRON_PATH = "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
SESSION_BASENAME = "desktop-acceptance-session.json"
EVIDENCE_BASENAME = "target-verification.json"
DRIVER_RESULT_BASENAME = "desktop-driver-result.json"
SCREENSHOT_BASENAME = "desktop-app.png"
DIAGNOSTIC_BASENAME = "taiji-support-bundle.json"
MAX_JSON_BYTES = 1024 * 1024
MAX_SCREENSHOT_BYTES = 32 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
EXPECTED_CHECKS = {
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "window_close_exit",
    "diagnostic_export",
}
DRIVER_KEYS = {
    "schema",
    "acceptance_session_id",
    "challenge_nonce",
    "electron_pid",
    "electron_executable",
    "electron_executable_sha256",
    "desktop_entry_sha256",
    "app_url",
    "webui_origin",
    "model",
    "attachment_probe_sha256",
    "agent_pid",
    "web_pid",
    "screenshot_basename",
    "diagnostic_basename",
    "checks",
    "js_error_count",
    "unexpected_http_failures",
    "electron_exit_code",
}
TARGET_SESSION_KEYS = {
    "schema",
    "application",
    "generated_at_utc",
    "acceptance_session_id",
    "challenge_nonce",
    "source_commit",
    "deb_sha256",
    "platform",
    "os_id",
    "os_version",
    "desktop_environment",
    "machine_fingerprint_sha256",
    "electron_pid",
    "electron_executable",
    "electron_executable_sha256",
    "desktop_entry_sha256",
    "installed_package_version",
    "transport",
    "desktop_token_present",
    "web_fallback_used",
    "checks",
    "js_error_count",
    "unexpected_http_failures",
}
TARGET_KEYS = {
    "schema_version",
    "evidence_type",
    "application",
    "generated_at_utc",
    "acceptance_session_id",
    "challenge_nonce",
    "machine_fingerprint_sha256",
    "release_artifacts_sha256",
    "electron_executable_sha256",
    "desktop_entry_sha256",
    "installed_package_version",
    "source_commit",
    "deb_basename",
    "deb_sha256",
    "platform",
    "os_id",
    "os_version",
    "desktop_environment",
    "target_verified",
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "window_close_exit",
    "diagnostic_export",
    "session_log_basename",
    "session_log_sha256",
    "screenshot_basename",
    "screenshot_sha256",
    "diagnostic_basename",
    "diagnostic_sha256",
    "driver_result_basename",
    "driver_result_sha256",
}


class AssemblyError(ValueError):
    """Raised when an input cannot produce trustworthy target evidence."""


def require_exact_keys(data: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - data.keys())
    extra = sorted(data.keys() - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"unknown: {', '.join(extra)}")
        raise AssemblyError(f"{label} has an invalid field set ({'; '.join(details)})")


def object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssemblyError(f"JSON contains a duplicate field: {key}")
        result[key] = value
    return result


def require_trusted_ancestor_chain(directory: Path, label: str) -> None:
    current = Path(os.path.abspath(directory))
    while True:
        try:
            file_stat = current.lstat()
        except OSError as exc:
            raise AssemblyError(f"{label} ancestor is unreadable: {current}: {exc}") from exc
        if stat.S_ISLNK(file_stat.st_mode):
            if file_stat.st_uid != 0:
                raise AssemblyError(f"{label} crosses a non-root-owned symlink: {current}")
        elif not stat.S_ISDIR(file_stat.st_mode):
            raise AssemblyError(f"{label} ancestor is not a directory: {current}")
        if current == current.parent:
            return
        current = current.parent


def require_safe_parent(path: Path, label: str) -> None:
    require_trusted_ancestor_chain(path.parent, label)
    try:
        parent_stat = path.parent.lstat()
    except OSError as exc:
        raise AssemblyError(f"{label} parent is unreadable: {path.parent}: {exc}") from exc
    if not stat.S_ISDIR(parent_stat.st_mode) or path.parent.is_symlink():
        raise AssemblyError(f"{label} parent must be a real directory: {path.parent}")


def _stable_identity(file_stat: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_nlink,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def open_regular(path: Path, label: str) -> tuple[int, os.stat_result]:
    if not path.is_absolute():
        raise AssemblyError(f"{label} path must be absolute")
    require_safe_parent(path, label)
    parent_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(path.parent, parent_flags)
    except OSError as exc:
        raise AssemblyError(f"{label} parent cannot be opened safely: {path.parent}: {exc}") from exc
    try:
        descriptor = os.open(path.name, file_flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise AssemblyError(f"{label} cannot be opened safely: {path}: {exc}") from exc
    finally:
        os.close(parent_descriptor)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise AssemblyError(f"{label} must be a regular file")
        if file_stat.st_size <= 0:
            raise AssemblyError(f"{label} must not be empty")
        if file_stat.st_nlink != 1:
            raise AssemblyError(f"{label} must have exactly one hard link")
        return descriptor, file_stat
    except Exception:
        os.close(descriptor)
        raise


def _verify_unchanged(descriptor: int, before: os.stat_result, label: str) -> None:
    after = os.fstat(descriptor)
    if _stable_identity(after) != _stable_identity(before):
        raise AssemblyError(f"{label} changed while it was being read")


def read_regular_bytes(path: Path, label: str, *, limit: int = MAX_JSON_BYTES) -> bytes:
    descriptor, file_stat = open_regular(path, label)
    try:
        if file_stat.st_size > limit:
            raise AssemblyError(f"{label} exceeds the {limit}-byte limit")
        chunks: list[bytes] = []
        total = 0
        while total < file_stat.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, file_stat.st_size - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total != file_stat.st_size:
            raise AssemblyError(f"{label} was truncated while being read")
        _verify_unchanged(descriptor, file_stat, label)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def sha256_regular_file(path: Path, label: str) -> str:
    descriptor, file_stat = open_regular(path, label)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        if total != file_stat.st_size:
            raise AssemblyError(f"{label} was truncated while hashing")
        _verify_unchanged(descriptor, file_stat, label)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def parse_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=object_without_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, AssemblyError) as exc:
        raise AssemblyError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if type(value) is not dict:
        raise AssemblyError(f"{label} top level must be an object")
    return value


def load_json(path: Path, label: str) -> dict[str, Any]:
    return parse_json_bytes(read_regular_bytes(path, label), label)


def require_sha256(value: Any, label: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise AssemblyError(f"{label} must be a lowercase 64-character SHA256")
    return value


def require_pid(value: Any, label: str) -> int:
    if type(value) is not int or value <= 1:
        raise AssemblyError(f"{label} must be an integer greater than one")
    return value


def require_visible_text(value: Any, label: str, *, maximum: int = 128) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise AssemblyError(f"{label} must be a non-empty string no longer than {maximum} characters")
    if value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AssemblyError(f"{label} contains surrounding whitespace or control characters")
    return value


def validate_redacted_app_url(app_url: Any, webui_origin: Any) -> None:
    if type(app_url) is not str or type(webui_origin) is not str:
        raise AssemblyError("driver App URLs must be strings")
    try:
        app = urlsplit(app_url)
        origin = urlsplit(webui_origin)
        query = parse_qs(app.query, strict_parsing=True, keep_blank_values=True)
    except ValueError as exc:
        raise AssemblyError("driver App URLs are malformed") from exc
    if app.scheme != "http" or app.hostname not in {"127.0.0.1", "localhost"}:
        raise AssemblyError("driver app_url must be an HTTP loopback URL")
    if app.username or app.password or app.fragment:
        raise AssemblyError("driver app_url contains forbidden authority or fragment data")
    if set(query) != {"taiji_desktop", "taiji_desktop_token"}:
        raise AssemblyError("driver app_url contains unexpected query data")
    if query.get("taiji_desktop") != ["1"] or query.get("taiji_desktop_token") != ["<redacted>"]:
        raise AssemblyError("driver app_url must contain one desktop marker and a redacted token")
    if origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost"}:
        raise AssemblyError("driver webui_origin must be an HTTP loopback origin")
    if origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}:
        raise AssemblyError("driver webui_origin must not contain credentials, query, fragment, or path")
    app_origin = f"{app.scheme}://{app.netloc}"
    expected_origin = f"{origin.scheme}://{origin.netloc}"
    if app_origin != expected_origin:
        raise AssemblyError("driver app_url and webui_origin do not identify the same App")


def validate_driver_result(driver: dict[str, Any], challenge: str) -> None:
    require_exact_keys(driver, DRIVER_KEYS, "driver-result.json")
    if driver["schema"] != "taiji.desktop.acceptance-driver.v1":
        raise AssemblyError("driver-result.json has the wrong schema")
    if type(driver["acceptance_session_id"]) is not str or not SESSION_RE.fullmatch(
        driver["acceptance_session_id"]
    ):
        raise AssemblyError("driver acceptance_session_id is invalid")
    if driver["challenge_nonce"] != challenge:
        raise AssemblyError("driver challenge does not match this target acceptance run")
    if driver["electron_executable"] != ELECTRON_PATH:
        raise AssemblyError("driver did not use the fixed installed Electron executable")
    for key in ("electron_pid", "agent_pid", "web_pid"):
        require_pid(driver[key], f"driver {key}")
    for key in (
        "electron_executable_sha256",
        "desktop_entry_sha256",
        "attachment_probe_sha256",
    ):
        require_sha256(driver[key], f"driver {key}")
    require_visible_text(driver["model"], "driver model", maximum=256)
    validate_redacted_app_url(driver["app_url"], driver["webui_origin"])
    if driver["screenshot_basename"] != SCREENSHOT_BASENAME:
        raise AssemblyError("driver screenshot basename is not the fixed acceptance filename")
    if driver["diagnostic_basename"] != DIAGNOSTIC_BASENAME:
        raise AssemblyError("driver diagnostic basename is not the fixed support-bundle filename")
    checks = driver["checks"]
    if type(checks) is not dict:
        raise AssemblyError("driver checks must be an object")
    require_exact_keys(checks, EXPECTED_CHECKS, "driver checks")
    if any(type(checks[key]) is not bool or checks[key] is not True for key in EXPECTED_CHECKS):
        raise AssemblyError("all driver checks must be true")
    for key in ("js_error_count", "unexpected_http_failures", "electron_exit_code"):
        if type(driver[key]) is not int or driver[key] != 0:
            raise AssemblyError(f"driver {key} must be integer zero")


def validate_manifest(
    manifest: dict[str, Any],
    *,
    deb: Path,
    deb_sha256: str,
    electron_sha256: str,
    desktop_entry_sha256: str,
    installed_version: str,
) -> tuple[str, str]:
    expected = {
        "schema_version": 1,
        "package": "taiji-agent",
        "build_arch": "x86_64",
        "dpkg_arch": "amd64",
        "deb": deb.name,
        "deb_sha256": deb_sha256,
        "electron_executable_sha256": electron_sha256,
        "desktop_entry_sha256": desktop_entry_sha256,
    }
    for key, value in expected.items():
        if key not in manifest or type(manifest[key]) is not type(value) or manifest[key] != value:
            raise AssemblyError(f"release manifest {key} does not match the current installed artifact")
    source_commit = manifest.get("source_commit")
    if type(source_commit) is not str or not COMMIT_RE.fullmatch(source_commit):
        raise AssemblyError("release manifest source_commit is invalid")
    version = manifest.get("version")
    if type(version) is not str or not VERSION_RE.fullmatch(version):
        raise AssemblyError("release manifest version is invalid")
    if version != installed_version:
        raise AssemblyError("installed package version does not match the release manifest")
    if deb.name != f"taiji-agent_{version}_amd64.deb":
        raise AssemblyError("current DEB basename does not match the installed package version")
    return source_commit, version


def write_exclusive(path: Path, payload: bytes) -> str:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    digest = hashlib.sha256()
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AssemblyError(f"failed to write {path.name}")
            digest.update(view[:written])
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def copy_regular_file(
    source: Path,
    destination: Path,
    label: str,
    *,
    limit: int,
    required_prefix: bytes | None = None,
) -> str:
    source_descriptor, source_stat = open_regular(source, label)
    if source_stat.st_size > limit:
        os.close(source_descriptor)
        raise AssemblyError(f"{label} exceeds the {limit}-byte limit")
    try:
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except Exception:
        os.close(source_descriptor)
        raise
    digest = hashlib.sha256()
    total = 0
    prefix = bytearray()
    try:
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            if required_prefix is not None and len(prefix) < len(required_prefix):
                needed = len(required_prefix) - len(prefix)
                prefix.extend(chunk[:needed])
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise AssemblyError(f"failed to copy {label}")
                view = view[written:]
            total += len(chunk)
            digest.update(chunk)
        if total != source_stat.st_size:
            raise AssemblyError(f"{label} was truncated while being copied")
        if required_prefix is not None and bytes(prefix) != required_prefix:
            raise AssemblyError(f"{label} has an invalid file signature")
        _verify_unchanged(source_descriptor, source_stat, label)
        os.fsync(destination_descriptor)
        return digest.hexdigest()
    finally:
        os.close(source_descriptor)
        os.close(destination_descriptor)


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _rename_noreplace(source: Path, destination: Path) -> None:
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100,
                os.fsencode(source),
                -100,
                os.fsencode(destination),
                1,
            )
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number != errno.ENOSYS:
                raise OSError(error_number, os.strerror(error_number), destination)
    if os.path.lexists(destination):
        raise FileExistsError(errno.EEXIST, "target evidence output already exists", destination)
    os.rename(source, destination)


def publish_atomically(output_dir: Path, producer: Callable[[Path], None]) -> None:
    if not output_dir.is_absolute():
        raise AssemblyError("output directory path must be absolute")
    require_safe_parent(output_dir, "output directory")
    parent_stat = output_dir.parent.stat()
    if stat.S_IMODE(parent_stat.st_mode) & 0o022:
        raise AssemblyError("output parent directory must not be group/other writable")
    if os.path.lexists(output_dir):
        raise AssemblyError("output directory already exists; refusing to overwrite evidence")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent)
    )
    os.chmod(temporary, 0o700)
    published = False
    try:
        producer(temporary)
        directory_descriptor = os.open(
            temporary, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        _rename_noreplace(temporary, output_dir)
        published = True
        parent_descriptor = os.open(
            output_dir.parent,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except Exception:
        if published and output_dir.is_dir() and not output_dir.is_symlink():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise
    finally:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary, ignore_errors=True)


def assemble(args: argparse.Namespace) -> None:
    challenge = args.challenge
    if not CHALLENGE_RE.fullmatch(challenge or ""):
        raise AssemblyError("challenge must be 64-128 lowercase hexadecimal characters")
    release_artifacts_sha256 = require_sha256(
        args.release_artifacts_sha256, "release_artifacts_sha256"
    )
    machine_fingerprint_sha256 = require_sha256(
        args.machine_fingerprint_sha256, "machine_fingerprint_sha256"
    )
    installed_version = args.installed_package_version
    if not VERSION_RE.fullmatch(installed_version or ""):
        raise AssemblyError("installed package version is invalid")
    if args.os_id not in {"kylin", "uos", "openkylin"}:
        raise AssemblyError("os_id must be kylin, uos, or openkylin")
    os_version = require_visible_text(args.os_version, "os_version")
    desktop_environment = require_visible_text(args.desktop_environment, "desktop_environment")

    driver_payload = read_regular_bytes(args.driver_result, "driver-result.json")
    driver = parse_json_bytes(driver_payload, "driver-result.json")
    validate_driver_result(driver, challenge)
    manifest = load_json(args.manifest, "release manifest")
    deb_sha256 = sha256_regular_file(args.deb, "current DEB")
    electron_sha256 = sha256_regular_file(args.electron_executable, "installed Electron executable")
    desktop_entry_sha256 = sha256_regular_file(args.desktop_entry, "installed desktop entry")
    if electron_sha256 != driver["electron_executable_sha256"]:
        raise AssemblyError("installed Electron hash does not match the desktop acceptance driver")
    if desktop_entry_sha256 != driver["desktop_entry_sha256"]:
        raise AssemblyError("installed desktop entry hash does not match the desktop acceptance driver")
    source_commit, version = validate_manifest(
        manifest,
        deb=args.deb,
        deb_sha256=deb_sha256,
        electron_sha256=electron_sha256,
        desktop_entry_sha256=desktop_entry_sha256,
        installed_version=installed_version,
    )
    if args.screenshot.name != driver["screenshot_basename"]:
        raise AssemblyError("screenshot input basename does not match the driver result")
    if args.diagnostic.name != driver["diagnostic_basename"]:
        raise AssemblyError("diagnostic input basename does not match the driver result")
    diagnostic_payload = read_regular_bytes(args.diagnostic, "App diagnostic export")
    try:
        diagnostic_json = json.loads(
            diagnostic_payload.decode("utf-8"), object_pairs_hook=object_without_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError, AssemblyError) as exc:
        raise AssemblyError(f"App diagnostic export is not strict UTF-8 JSON: {exc}") from exc
    if type(diagnostic_json) is not dict or set(diagnostic_json) != {"schema", "manifest", "diagnostics"}:
        raise AssemblyError("App diagnostic export has an invalid top-level field set")
    if diagnostic_json["schema"] != "taiji.product.support-bundle.v1":
        raise AssemblyError("App diagnostic export has the wrong schema")

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    checks = {key: True for key in sorted(EXPECTED_CHECKS)}

    def produce(temporary: Path) -> None:
        screenshot_hash = copy_regular_file(
            args.screenshot,
            temporary / SCREENSHOT_BASENAME,
            "desktop App screenshot",
            limit=MAX_SCREENSHOT_BYTES,
            required_prefix=b"\x89PNG\r\n\x1a\n",
        )
        diagnostic_hash = write_exclusive(
            temporary / DIAGNOSTIC_BASENAME, diagnostic_payload
        )
        driver_result_hash = write_exclusive(
            temporary / DRIVER_RESULT_BASENAME, driver_payload
        )
        session = {
            "schema": "taiji.desktop.acceptance.v1",
            "application": "taiji-electron-desktop",
            "generated_at_utc": generated_at,
            "acceptance_session_id": driver["acceptance_session_id"],
            "challenge_nonce": challenge,
            "source_commit": source_commit,
            "deb_sha256": deb_sha256,
            "platform": "linux/amd64",
            "os_id": args.os_id,
            "os_version": os_version,
            "desktop_environment": desktop_environment,
            "machine_fingerprint_sha256": machine_fingerprint_sha256,
            "electron_pid": driver["electron_pid"],
            "electron_executable": ELECTRON_PATH,
            "electron_executable_sha256": electron_sha256,
            "desktop_entry_sha256": desktop_entry_sha256,
            "installed_package_version": version,
            "transport": "electron-cdp",
            "desktop_token_present": True,
            "web_fallback_used": False,
            "checks": checks,
            "js_error_count": 0,
            "unexpected_http_failures": 0,
        }
        require_exact_keys(session, TARGET_SESSION_KEYS, "assembled target session")
        session_hash = write_exclusive(temporary / SESSION_BASENAME, json_bytes(session))
        evidence = {
            "schema_version": 1,
            "evidence_type": "target-desktop-verification",
            "application": "taiji-electron-desktop",
            "generated_at_utc": generated_at,
            "acceptance_session_id": driver["acceptance_session_id"],
            "challenge_nonce": challenge,
            "machine_fingerprint_sha256": machine_fingerprint_sha256,
            "release_artifacts_sha256": release_artifacts_sha256,
            "electron_executable_sha256": electron_sha256,
            "desktop_entry_sha256": desktop_entry_sha256,
            "installed_package_version": version,
            "source_commit": source_commit,
            "deb_basename": args.deb.name,
            "deb_sha256": deb_sha256,
            "platform": "linux/amd64",
            "os_id": args.os_id,
            "os_version": os_version,
            "desktop_environment": desktop_environment,
            "target_verified": True,
            "desktop_launch": True,
            "real_model_conversation": True,
            "attachment_flow": True,
            "window_close_exit": True,
            "diagnostic_export": True,
            "session_log_basename": SESSION_BASENAME,
            "session_log_sha256": session_hash,
            "screenshot_basename": SCREENSHOT_BASENAME,
            "screenshot_sha256": screenshot_hash,
            "diagnostic_basename": DIAGNOSTIC_BASENAME,
            "diagnostic_sha256": diagnostic_hash,
            "driver_result_basename": DRIVER_RESULT_BASENAME,
            "driver_result_sha256": driver_result_hash,
        }
        require_exact_keys(evidence, TARGET_KEYS, "assembled target evidence")
        write_exclusive(temporary / EVIDENCE_BASENAME, json_bytes(evidence))

    publish_atomically(args.output_dir, produce)


def reject_duplicate_options(argv: Sequence[str]) -> None:
    options = [item for item in argv if item.startswith("--")]
    duplicates = sorted({item for item in options if options.count(item) > 1})
    if duplicates:
        raise AssemblyError(f"duplicate command-line option: {', '.join(duplicates)}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    reject_duplicate_options(raw)
    parser = argparse.ArgumentParser(
        description="Assemble unsigned target Electron acceptance evidence for validator --pre-sign.",
        allow_abbrev=False,
    )
    parser.add_argument("--driver-result", required=True, type=Path)
    parser.add_argument("--screenshot", required=True, type=Path)
    parser.add_argument("--diagnostic", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--electron-executable", required=True, type=Path)
    parser.add_argument("--desktop-entry", required=True, type=Path)
    parser.add_argument("--release-artifacts-sha256", required=True)
    parser.add_argument("--machine-fingerprint-sha256", required=True)
    parser.add_argument("--installed-package-version", required=True)
    parser.add_argument("--challenge", required=True)
    parser.add_argument("--os-id", required=True)
    parser.add_argument("--os-version", required=True)
    parser.add_argument("--desktop-environment", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(raw)
    for name in (
        "driver_result",
        "screenshot",
        "diagnostic",
        "manifest",
        "deb",
        "electron_executable",
        "desktop_entry",
        "output_dir",
    ):
        if not getattr(args, name).is_absolute():
            parser.error(f"--{name.replace('_', '-')} must be an absolute path")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    try:
        args = parse_args(argv)
        assemble(args)
    except (AssemblyError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"target-evidence-assembly-failed\t{exc}", file=sys.stderr)
        return 1
    print(f"target-evidence-assembled\t{args.output_dir / EVIDENCE_BASENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
