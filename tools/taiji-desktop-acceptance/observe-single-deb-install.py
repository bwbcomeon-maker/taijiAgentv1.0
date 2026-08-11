#!/usr/bin/env python3
"""Observe and bind a first, offline, single-DEB installation on the target.

The observer intentionally uses only the Python standard library so that it can
run with the target operating system's ``/usr/bin/python3`` before Taiji Agent
is installed.  It must be started before the graphical installer is opened and
must remain alive until dpkg reports the package as installed.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import pwd
import re
import secrets
import shlex
import stat
import subprocess
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path


OBSERVATION_SCHEMA = "taiji.single-deb-install-observation.v1"
ATTESTATION_SCHEMA = "taiji.single-deb-install-method-attestation.v1"
ENVIRONMENT_RECORD_SCHEMA = "taiji-linux-environment-observation/v1"
CERTIFICATION_MATRIX_SCHEMA = "taiji-linux-certification-matrix/v2"
CERTIFICATION_POLICY_ID = "taiji-linux-amd64-deb-v1"
OBSERVATION_BASENAME = "single-deb-install-observation.json"
ATTESTATION_BASENAME = "single-deb-install-method-attestation.json"
ENVIRONMENT_RECORD_BASENAME = "environment-observation.json"
GRAPHICAL_EVIDENCE_BASENAME = "single-deb-graphical-installer.png"
PACKAGE_NAME = "taiji-agent"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
MACHINE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,127}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
MACHINE_IDENTITY_COMMITMENT_DOMAIN = "taiji-machine-identity-v1"

POSITIVE_CATEGORY_IDS = {
    "kylin-min-ukui",
    "kylin-current-standard",
    "kylin-hardened",
    "uos-min-dde",
    "uos-current-or-hardened",
    "openkylin-current",
}
NEGATIVE_CATEGORY_IDS = {
    "arm-blocked",
    "rpm-only-blocked",
    "glibc-below-min-blocked",
    "missing-core-capability-blocked",
    "no-admin-blocked",
    "no-graphical-desktop-blocked",
}
PLATFORM_PROFILE_KEYS = {
    "os_id",
    "version_id",
    "release_id_pattern",
    "desktop_environments",
    "security_profile",
}
CANONICAL_PLATFORM_PROFILES = {
    "kylin-min-ukui": {
        "os_id": "kylin",
        "version_id": "v10",
        "release_id_pattern": "2403",
        "desktop_environments": ["UKUI"],
        "security_profile": "supported-default",
    },
    "kylin-current-standard": {
        "os_id": "kylin",
        "version_id": "v10",
        "release_id_pattern": "2503",
        "desktop_environments": ["UKUI"],
        "security_profile": "supported-default",
    },
    "kylin-hardened": {
        "os_id": "kylin",
        "version_id": "v10",
        "release_id_pattern": "2503",
        "desktop_environments": ["UKUI"],
        "security_profile": "kysec-enabled-exec-control-off",
    },
    "uos-min-dde": {
        "os_id": "uos",
        "version_id": "20",
        "release_id_pattern": r"1070(?:u2)?",
        "desktop_environments": ["DDE"],
        "security_profile": "supported-default",
    },
    "uos-current-or-hardened": {
        "os_id": "uos",
        "version_id": "25",
        "release_id_pattern": r"25[0-9A-Za-z._-]*",
        "desktop_environments": ["DDE"],
        "security_profile": "supported-default",
    },
    "openkylin-current": {
        "os_id": "openkylin",
        "version_id": "2.0",
        "release_id_pattern": r"2\.0-SP2",
        "desktop_environments": ["UKUI", "GNOME"],
        "security_profile": "supported-default",
    },
}


class ObservationError(RuntimeError):
    """Raised when the installation observation cannot prove the contract."""


def _utc_text(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value, label):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ObservationError("%s is not a UTC timestamp" % label)
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ObservationError("%s is not a valid timestamp" % label) from exc


def _sha256_path(path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ObservationError("cannot safely open %s: %s" % (path, exc)) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ObservationError("%s must be a regular single-link file" % path)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = os.fstat(descriptor)
        if _file_stat_identity(metadata) != _file_stat_identity(after):
            raise ObservationError("%s changed while it was being hashed" % path)
    except OSError as exc:
        raise ObservationError("cannot read %s: %s" % (path, exc)) from exc
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _file_stat_identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _hash_descriptor(descriptor, metadata, label):
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    if _file_stat_identity(metadata) != _file_stat_identity(os.fstat(descriptor)):
        raise ObservationError("%s changed while it was being hashed" % label)
    return digest.hexdigest()


def _load_json(path, label):
    value, _digest = _load_json_snapshot(path, label)
    return value


def _json_object_without_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ObservationError("JSON contains duplicate field: %s" % key)
        value[key] = item
    return value


def _load_json_snapshot(path, label, limit=1024 * 1024):
    """Read one bounded strict JSON object from one stable file descriptor."""
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ObservationError("%s must be an absolute JSON file" % label)
    try:
        before = candidate.lstat()
    except OSError as exc:
        raise ObservationError("cannot inspect %s JSON: %s" % (label, exc)) from exc
    if (
        candidate.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > limit
    ):
        raise ObservationError("%s must be a bounded regular single-link JSON file" % label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(candidate), flags)
    except OSError as exc:
        raise ObservationError("cannot safely open %s JSON: %s" % (label, exc)) from exc
    try:
        opened = os.fstat(descriptor)
        if _file_stat_identity(before) != _file_stat_identity(opened):
            raise ObservationError("%s JSON changed before it was opened" % label)
        chunks = []
        remaining = opened.st_size
        digest = hashlib.sha256()
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ObservationError("%s JSON was truncated while it was read" % label)
            chunks.append(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ObservationError("%s JSON grew while it was read" % label)
        after = os.fstat(descriptor)
        current = candidate.lstat()
        if (
            _file_stat_identity(opened) != _file_stat_identity(after)
            or _file_stat_identity(opened) != _file_stat_identity(current)
        ):
            raise ObservationError("%s JSON identity changed while it was read" % label)
        payload = b"".join(chunks)
    except OSError as exc:
        raise ObservationError("cannot read %s JSON: %s" % (label, exc)) from exc
    finally:
        os.close(descriptor)
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ObservationError) as exc:
        raise ObservationError("cannot read %s JSON: %s" % (label, exc)) from exc
    if not isinstance(value, dict):
        raise ObservationError("%s must be a JSON object" % label)
    return value, digest.hexdigest()


def _require_trusted_directory_chain(directory, trusted_roots, expected_owner_uid, label):
    roots = tuple(Path(os.path.abspath(str(root))) for root in trusted_roots)
    candidate = Path(os.path.abspath(str(directory)))
    containing_roots = [
        root
        for root in roots
        if os.path.commonpath((str(candidate), str(root))) == str(root)
    ]
    if not containing_roots:
        candidate = candidate.resolve()
        roots = tuple(root.resolve() for root in roots)
        containing_roots = [
            root
            for root in roots
            if os.path.commonpath((str(candidate), str(root))) == str(root)
        ]
    if not containing_roots:
        raise ObservationError("%s directory is outside trusted system roots" % label)
    containing_root = max(containing_roots, key=lambda item: len(str(item)))
    current = candidate
    while True:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ObservationError("%s directory cannot be inspected" % label) from exc
        if (
            current.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != expected_owner_uid
            or metadata.st_mode & 0o022
        ):
            raise ObservationError("%s directory chain is not trusted" % label)
        if current == containing_root:
            return containing_root
        current = current.parent


def _read_trusted_system_file(
    path,
    required=True,
    *,
    trusted_roots=None,
    expected_owner_uid=0,
):
    """Read a bounded root-owned system identity file without following an untrusted link."""
    if trusted_roots is None:
        trusted_roots = (Path("/etc"), Path("/usr/lib"))
    trusted_roots = tuple(Path(os.path.abspath(str(root))) for root in trusted_roots)

    def is_within_trusted_root(candidate):
        candidate_text = str(Path(candidate).resolve())
        return any(
            os.path.commonpath((candidate_text, str(root.resolve()))) == str(root.resolve())
            for root in trusted_roots
        )

    requested = Path(path)
    try:
        requested_stat = requested.lstat()
    except FileNotFoundError:
        if required:
            raise ObservationError("required platform identity file is missing: %s" % requested)
        return None
    except OSError as exc:
        raise ObservationError("cannot inspect platform identity file %s: %s" % (requested, exc)) from exc
    target = requested
    if stat.S_ISLNK(requested_stat.st_mode):
        if requested_stat.st_uid != expected_owner_uid:
            raise ObservationError("platform identity symlink is not trusted: %s" % requested)
        _require_trusted_directory_chain(
            requested.parent,
            trusted_roots,
            expected_owner_uid,
            "platform identity symlink parent",
        )
        try:
            target = requested.resolve(strict=True)
        except OSError as exc:
            raise ObservationError("platform identity symlink cannot be resolved: %s" % requested) from exc
        if not is_within_trusted_root(target):
            raise ObservationError("platform identity symlink escapes trusted system directories")
    _require_trusted_directory_chain(
        target.parent,
        trusted_roots,
        expected_owner_uid,
        "platform identity file parent",
    )
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise ObservationError("cannot inspect platform identity target %s: %s" % (target, exc)) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_owner_uid
        or metadata.st_mode & 0o022
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > 128 * 1024
    ):
        raise ObservationError("platform identity file is not a trusted root-owned regular file: %s" % target)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(target), flags)
    except OSError as exc:
        raise ObservationError("cannot safely open platform identity file %s: %s" % (target, exc)) from exc
    try:
        opened = os.fstat(descriptor)
        if _file_stat_identity(opened) != _file_stat_identity(metadata):
            raise ObservationError("platform identity file changed before it was opened")
        payload = bytearray()
        while len(payload) < metadata.st_size:
            chunk = os.read(descriptor, metadata.st_size - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) != metadata.st_size or _file_stat_identity(os.fstat(descriptor)) != _file_stat_identity(metadata):
            raise ObservationError("platform identity file changed while it was read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _parse_assignment_payload(payload, label, allow_sections=False):
    try:
        text = payload.decode("utf-8")
    except (AttributeError, UnicodeError) as exc:
        raise ObservationError("%s is not UTF-8 text" % label) from exc
    fields = {}
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if allow_sections and stripped.startswith("[") and stripped.endswith("]"):
            continue
        if "=" not in stripped:
            raise ObservationError("%s contains a malformed identity line" % label)
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        key_pattern = (
            r"[A-Za-z][A-Za-z0-9_]*(?:\[[A-Za-z0-9_.@-]+\])?"
            if allow_sections
            else r"[A-Za-z][A-Za-z0-9_]*"
        )
        if not re.fullmatch(key_pattern, key) or key in fields:
            raise ObservationError("%s contains an invalid or duplicate identity field" % label)
        if allow_sections:
            value = raw_value.strip()
            if value[:1] in {"\"", "'"} or value[-1:] in {"\"", "'"}:
                try:
                    parsed = shlex.split(value, posix=True)
                except ValueError as exc:
                    raise ObservationError("%s contains invalid quoting" % label) from exc
                if len(parsed) != 1:
                    raise ObservationError("%s identity value is ambiguous" % label)
                value = parsed[0]
        else:
            try:
                parsed = shlex.split(raw_value, posix=True)
            except ValueError as exc:
                raise ObservationError("%s contains invalid shell quoting" % label) from exc
            if len(parsed) > 1:
                raise ObservationError("%s identity value is ambiguous" % label)
            value = parsed[0] if parsed else ""
        if len(value) > 256 or any(character in value for character in "\r\n\t\0"):
            raise ObservationError("%s contains an invalid identity value" % label)
        fields[key] = value
    return fields


def _desktop_family_from_label(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ObservationError("%s desktop value is missing or invalid" % label)
    tokens = [token.strip().lower() for token in re.split(r"[:;,]", value) if token.strip()]
    families = set()
    for token in tokens:
        if token == "ukui" or token.startswith("ukui-"):
            families.add("UKUI")
        elif token in {"dde", "deepin"} or token.startswith("deepin-"):
            families.add("DDE")
        elif token == "gnome" or token.startswith("gnome-"):
            families.add("GNOME")
    if len(families) != 1:
        raise ObservationError("%s desktop value is unknown or ambiguous" % label)
    return families.pop()


def _trusted_path_is_within(path, roots):
    candidate = str(Path(path).resolve())
    return any(
        os.path.commonpath((candidate, str(Path(root).resolve()))) == str(Path(root).resolve())
        for root in roots
    )


def _require_trusted_executable(path, roots, expected_owner_uid, label):
    candidate = Path(path)
    roots = tuple(Path(os.path.abspath(str(root))) for root in roots)
    try:
        requested_metadata = candidate.lstat()
    except OSError as exc:
        raise ObservationError("%s executable is unavailable" % label) from exc
    if stat.S_ISLNK(requested_metadata.st_mode) and requested_metadata.st_uid != expected_owner_uid:
        raise ObservationError("%s executable symlink is not trusted" % label)
    _require_trusted_directory_chain(
        candidate.parent,
        roots,
        expected_owner_uid,
        "%s executable parent" % label,
    )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ObservationError("%s executable is unavailable" % label) from exc
    if not _trusted_path_is_within(resolved, roots):
        raise ObservationError("%s executable is outside trusted system roots" % label)
    _require_trusted_directory_chain(
        resolved.parent,
        roots,
        expected_owner_uid,
        "%s executable target" % label,
    )
    try:
        metadata = resolved.lstat()
    except OSError as exc:
        raise ObservationError("%s executable cannot be inspected" % label) from exc
    if (
        resolved.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_owner_uid
        or metadata.st_mode & 0o022
        or metadata.st_nlink != 1
        or not metadata.st_mode & 0o111
    ):
        raise ObservationError("%s executable is not a trusted system executable" % label)
    return resolved


def _read_bounded_proc_text(path, limit=64 * 1024):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ObservationError("desktop session process facts cannot be opened") from exc
    try:
        payload = bytearray()
        while len(payload) <= limit:
            chunk = os.read(descriptor, min(8192, limit + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > limit:
            raise ObservationError("desktop session process facts are too large")
        return bytes(payload).decode("utf-8")
    except UnicodeError as exc:
        raise ObservationError("desktop session process facts are not UTF-8") from exc
    finally:
        os.close(descriptor)


def _read_proc_start_time(path):
    text = _read_bounded_proc_text(path, limit=16 * 1024).strip()
    closing = text.rfind(")")
    if closing < 1:
        raise ObservationError("desktop session process stat is malformed")
    fields = text[closing + 1 :].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise ObservationError("desktop session process start time is malformed")
    return fields[19]


def _read_proc_uids(path):
    payload = _read_bounded_proc_text(path, limit=64 * 1024)
    matches = re.findall(r"(?m)^Uid:\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s*$", payload)
    if len(matches) != 1:
        raise ObservationError("desktop manager UID facts are missing or ambiguous")
    return tuple(int(value) for value in matches[0])


def _cgroup_contains_scope(payload, scope):
    for line in payload.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            raise ObservationError("desktop session cgroup facts are malformed")
        components = [component for component in fields[2].split("/") if component]
        if scope in components:
            return True
    return False


def _desktop_family_from_executable_name(name):
    return {
        "ukui-session": "UKUI",
        "startdde": "DDE",
        "dde-session": "DDE",
        "gnome-session-binary": "GNOME",
    }.get(name.lower())


def _executable_stat_identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
    )


def _read_proc_executable_snapshot(exe_path, roots, expected_owner_uid, label):
    try:
        link_target = os.readlink(str(exe_path))
    except OSError as exc:
        raise ObservationError("%s executable link cannot be read" % label) from exc
    if not os.path.isabs(link_target) or len(link_target) > 4096:
        raise ObservationError("%s executable link is invalid" % label)
    resolved = _require_trusted_executable(
        link_target,
        roots,
        expected_owner_uid,
        label,
    )
    try:
        process_metadata = os.stat(str(exe_path), follow_symlinks=True)
        resolved_metadata = resolved.stat()
    except OSError as exc:
        raise ObservationError("%s executable identity cannot be inspected" % label) from exc
    process_identity = _executable_stat_identity(process_metadata)
    resolved_identity = _executable_stat_identity(resolved_metadata)
    if process_identity != resolved_identity:
        raise ObservationError("%s executable path and running inode disagree" % label)
    return str(resolved), resolved_identity


def _desktop_family_from_session_processes(
    scope,
    leader,
    session_uid,
    proc_root=Path("/proc"),
    trusted_executable_roots=None,
    expected_owner_uid=0,
):
    if not re.fullmatch(r"session-[A-Za-z0-9_.-]{1,64}\.scope", scope or ""):
        raise ObservationError("desktop session scope is invalid")
    if not isinstance(leader, int) or leader <= 0:
        raise ObservationError("desktop session leader is invalid")
    if type(session_uid) is not int or session_uid < 0:
        raise ObservationError("desktop session user is invalid")
    roots = trusted_executable_roots or (
        Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64")
    )
    proc_root = Path(proc_root)
    families = set()
    trusted_leader = False
    try:
        process_directories = sorted(
            (item for item in proc_root.iterdir() if item.name.isdigit()),
            key=lambda item: int(item.name),
        )
    except OSError as exc:
        raise ObservationError("desktop session process table cannot be inspected") from exc
    if len(process_directories) > 1024 * 1024:
        raise ObservationError("desktop session process table is unexpectedly large")
    for process in process_directories:
        cgroup_path = process / "cgroup"
        try:
            cgroup_before = _read_bounded_proc_text(cgroup_path)
        except ObservationError:
            continue
        if not _cgroup_contains_scope(cgroup_before, scope):
            continue
        try:
            start_before = _read_proc_start_time(process / "stat")
            executable_link = os.readlink(str(process / "exe"))
        except (OSError, ObservationError):
            if int(process.name) == leader:
                raise ObservationError("desktop session leader changed while it was inspected")
            continue
        executable_name = Path(executable_link).name
        family = _desktop_family_from_executable_name(executable_name)
        pid = int(process.name)
        if family is None and pid != leader:
            continue
        executable_before = _read_proc_executable_snapshot(
            process / "exe",
            roots,
            expected_owner_uid,
            "desktop session",
        )
        if family is not None and _desktop_family_from_executable_name(Path(executable_before[0]).name) != family:
            raise ObservationError("desktop session executable identity changed")
        uids_before = _read_proc_uids(process / "status") if family is not None else None
        if uids_before is not None and any(value != session_uid for value in uids_before):
            raise ObservationError("desktop manager UID does not match the selected session user")
        start_after = _read_proc_start_time(process / "stat")
        cgroup_after = _read_bounded_proc_text(cgroup_path)
        executable_after = _read_proc_executable_snapshot(
            process / "exe",
            roots,
            expected_owner_uid,
            "desktop session",
        )
        uids_after = _read_proc_uids(process / "status") if family is not None else None
        if (
            start_before != start_after
            or cgroup_before != cgroup_after
            or executable_before != executable_after
            or uids_before != uids_after
        ):
            raise ObservationError("desktop session process changed while it was inspected")
        if pid == leader:
            trusted_leader = True
        if family is not None:
            families.add(family)
    if not trusted_leader:
        raise ObservationError("desktop session leader is not a trusted process in the session scope")
    if len(families) != 1:
        raise ObservationError("desktop session process family is missing or ambiguous")
    return families.pop()


def _parse_loginctl_properties(payload, expected, label):
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > 64 * 1024:
        raise ObservationError("%s output is invalid" % label)
    result = {}
    for line in payload.splitlines():
        if "=" not in line:
            raise ObservationError("%s output is malformed" % label)
        key, value = line.split("=", 1)
        if key not in expected or key in result or any(character in value for character in "\r\n\0"):
            raise ObservationError("%s output is ambiguous" % label)
        result[key] = value
    if set(result) != set(expected):
        raise ObservationError("%s output is incomplete" % label)
    return result


def _probe_trusted_desktop_session(
    environment=None,
    loginctl_path=Path("/usr/bin/loginctl"),
    proc_root=Path("/proc"),
    current_process_cgroup_path=Path("/proc/self/cgroup"),
    trusted_executable_roots=None,
    trusted_loginctl_roots=None,
    expected_owner_uid=0,
    uid=None,
    command_runner=None,
):
    """Prove one local graphical desktop using logind and its cgroup processes."""
    environment = dict(os.environ if environment is None else environment)
    current_uid = os.getuid() if uid is None else uid
    if type(current_uid) is not int or current_uid < 0:
        raise ObservationError("desktop session uid is invalid")
    loginctl_roots = (
        (
            (Path("/usr"),)
            if Path(loginctl_path) == Path("/usr/bin/loginctl")
            else (Path(loginctl_path).parent,)
        )
        if trusted_loginctl_roots is None
        else tuple(Path(item) for item in trusted_loginctl_roots)
    )
    loginctl = _require_trusted_executable(
        loginctl_path,
        loginctl_roots,
        expected_owner_uid,
        "loginctl",
    )
    run = subprocess.run if command_runner is None else command_runner
    clean_environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}

    def invoke(argv, label):
        try:
            result = run(
                argv,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                env=clean_environment,
            )
        except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
            raise ObservationError("%s loginctl query failed" % label) from exc
        if (
            result.returncode != 0
            or not isinstance(result.stdout, str)
            or not isinstance(result.stderr, str)
            or len(result.stdout.encode("utf-8")) > 64 * 1024
            or len(result.stderr.encode("utf-8")) > 64 * 1024
            or bool(result.stderr.strip())
        ):
            raise ObservationError("%s loginctl query returned an invalid result" % label)
        return result.stdout

    user = _parse_loginctl_properties(
        invoke(
            [
                str(loginctl),
                "show-user",
                str(current_uid),
                "--property=Display",
                "--property=Sessions",
                "--no-pager",
            ],
            "desktop user",
        ),
        {"Display", "Sessions"},
        "desktop user",
    )
    display_session = user["Display"]
    sessions = user["Sessions"].split()
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", display_session or "")
        or not sessions
        or len(sessions) != len(set(sessions))
        or len(sessions) > 64
        or any(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", item) is None for item in sessions)
        or display_session not in sessions
    ):
        raise ObservationError("desktop display session is missing or ambiguous")
    session_keys = {
        "User", "Seat", "Display", "Remote", "Desktop", "Leader", "Type",
        "Class", "Active", "State", "Scope",
    }

    def query_session(session_id):
        return _parse_loginctl_properties(
            invoke(
                [str(loginctl), "show-session", session_id]
                + ["--property=%s" % key for key in sorted(session_keys)]
                + ["--no-pager"],
                "desktop session",
            ),
            session_keys,
            "desktop session",
        )

    graphical = []
    selected = None
    for session_id in sessions:
        facts = query_session(session_id)
        if session_id == display_session:
            selected = facts
        is_graphical = (
            facts["User"] == str(current_uid)
            and facts["Remote"] == "no"
            and facts["Class"] == "user"
            and facts["Type"] in {"x11", "wayland"}
            and facts["Active"] == "yes"
            and facts["State"] == "active"
        )
        if is_graphical:
            graphical.append((session_id, facts))
    if selected is None or len(graphical) != 1 or graphical[0][0] != display_session:
        raise ObservationError("desktop display session is not one unique active local graphical session")
    facts = selected
    if not re.fullmatch(r"seat[0-9A-Za-z_.-]{1,64}", facts["Seat"] or ""):
        raise ObservationError("desktop session seat is invalid")
    if len(facts["Display"]) > 256 or any(character in facts["Display"] for character in "\r\n\0"):
        raise ObservationError("desktop session display is invalid")
    expected_scope = "session-%s.scope" % display_session
    if facts["Scope"] != expected_scope or not facts["Leader"].isdigit() or int(facts["Leader"]) <= 0:
        raise ObservationError("desktop session leader or scope is invalid")
    executor_cgroup_before = _read_bounded_proc_text(current_process_cgroup_path)
    if not _cgroup_contains_scope(executor_cgroup_before, expected_scope):
        raise ObservationError("current evidence process is outside the selected desktop session scope")
    process_family = _desktop_family_from_session_processes(
        expected_scope,
        int(facts["Leader"]),
        current_uid,
        proc_root=proc_root,
        trusted_executable_roots=trusted_executable_roots,
        expected_owner_uid=expected_owner_uid,
    )
    executor_cgroup_after = _read_bounded_proc_text(current_process_cgroup_path)
    if (
        executor_cgroup_before != executor_cgroup_after
        or not _cgroup_contains_scope(executor_cgroup_after, expected_scope)
    ):
        raise ObservationError("current evidence process changed desktop session scope")
    refreshed_facts = query_session(display_session)
    if refreshed_facts != facts:
        raise ObservationError("selected logind desktop session changed during process inspection")
    if facts["Desktop"]:
        logind_family = _desktop_family_from_label(facts["Desktop"], "logind")
        if logind_family != process_family:
            raise ObservationError("logind desktop and trusted session process family disagree")
    for key in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"):
        if environment.get(key):
            if _desktop_family_from_label(environment[key], key) != process_family:
                raise ObservationError("%s disagrees with the trusted desktop session" % key)
    if environment.get("XDG_SESSION_TYPE") and environment["XDG_SESSION_TYPE"].strip().lower() != facts["Type"]:
        raise ObservationError("XDG_SESSION_TYPE disagrees with the trusted desktop session")
    return process_family


def _probe_kysec_status(tool=None, markers=None, expected_owner_uid=0):
    tool = Path("/usr/sbin/getstatus") if tool is None else Path(tool)
    if markers is None:
        markers = (
            Path("/etc/kysec"),
            Path("/etc/kysec.conf"),
            Path("/etc/security/kysec"),
            tool,
            Path("/usr/sbin/kysec"),
            Path("/usr/bin/kysec"),
            Path("/usr/lib/kysec"),
            Path("/usr/libexec/kysec"),
        )
    else:
        markers = tuple(Path(marker) for marker in markers)
    detected = False
    for marker in markers:
        try:
            marker.lstat()
            detected = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ObservationError("Kysec marker could not be inspected") from exc
    if not detected:
        return {"detected": False, "enabled": False, "exec_control": "not-present"}
    for directory in (tool.parent.parent, tool.parent):
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise ObservationError("Kysec status tool parent directory is missing") from exc
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != expected_owner_uid
            or metadata.st_mode & 0o022
        ):
            raise ObservationError("Kysec status tool parent directory is not trusted")
    try:
        metadata = tool.lstat()
    except OSError as exc:
        raise ObservationError("Kysec status tool is missing or unreadable") from exc
    if (
        tool.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_owner_uid
        or metadata.st_mode & 0o022
        or metadata.st_nlink != 1
        or not os.access(str(tool), os.X_OK)
    ):
        raise ObservationError("Kysec status tool is not trusted")
    try:
        result = subprocess.run(
            [str(tool)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ObservationError("Kysec status tool could not be executed") from exc
    if result.returncode != 0 or len(result.stdout) > 64 * 1024 or len(result.stderr) > 64 * 1024:
        raise ObservationError("Kysec status tool returned an invalid result")
    try:
        output = result.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise ObservationError("Kysec status output is not UTF-8") from exc
    status = re.findall(r"(?im)^\s*Kysec\s+status\s*:\s*(enabled|disabled)\s*$", output)
    exec_control = re.findall(r"(?im)^\s*exec\s+control\s*:\s*(off|on)\s*$", output)
    if len(status) != 1 or len(exec_control) != 1:
        raise ObservationError("Kysec status output does not contain one unambiguous status")
    return {
        "detected": True,
        "enabled": status[0].lower() == "enabled",
        "exec_control": exec_control[0].lower(),
    }


def collect_platform_identity(
    matrix,
    category_id,
    read_system_file=_read_trusted_system_file,
    environment=None,
    kysec_probe=_probe_kysec_status,
    desktop_probe=_probe_trusted_desktop_session,
):
    """Derive the selected certification identity from target-owned facts, not CLI claims."""
    categories = {item["id"]: item for item in matrix["positive_categories"]}
    category = categories.get(category_id)
    if category is None:
        raise ObservationError("platform category is not a positive certification category")
    profile = category.get("platform_profile")
    if profile != CANONICAL_PLATFORM_PROFILES.get(category_id):
        raise ObservationError("platform category profile is not canonical")
    os_release_payload = read_system_file(Path("/etc/os-release"), required=True)
    if not isinstance(os_release_payload, (bytes, bytearray)):
        raise ObservationError("platform os-release evidence is missing")
    os_release_payload = bytes(os_release_payload)
    os_release = _parse_assignment_payload(os_release_payload, "/etc/os-release")
    os_id = os_release.get("ID", "").lower()
    version_id = os_release.get("VERSION_ID", "")
    os_version_payload = read_system_file(Path("/etc/os-version"), required=False)
    if os_id == "kylin":
        release_id = os_release.get("KYLIN_RELEASE_ID", "")
    elif os_id == "uos":
        if not isinstance(os_version_payload, (bytes, bytearray)):
            raise ObservationError("UOS platform identity requires /etc/os-version")
        os_version_fields = _parse_assignment_payload(bytes(os_version_payload), "/etc/os-version", allow_sections=True)
        major = os_version_fields.get("MajorVersion", "")
        minor = os_version_fields.get("MinorVersion", "")
        if major and version_id and major != version_id:
            raise ObservationError("UOS version sources disagree")
        version_id = major or version_id
        release_id = minor or major
    elif os_id == "openkylin":
        descriptive = " ".join(
            value for value in (os_release.get("VERSION", ""), os_release.get("PRETTY_NAME", "")) if value
        )
        service_pack = re.findall(r"(?i)\bSP([0-9]+)\b", descriptive)
        if len(set(service_pack)) != 1:
            raise ObservationError("openKylin service-pack identity is missing or ambiguous")
        release_id = "%s-SP%s" % (version_id, service_pack[0])
    else:
        raise ObservationError("platform OS is outside the certified domestic Linux families")
    desktop = desktop_probe(environment=os.environ if environment is None else environment)
    kysec = kysec_probe()
    if type(kysec) is not dict or set(kysec) != {"detected", "enabled", "exec_control"}:
        raise ObservationError("platform Kysec probe returned an invalid fact set")
    if type(kysec["detected"]) is not bool or type(kysec["enabled"]) is not bool:
        raise ObservationError("platform Kysec probe returned invalid booleans")
    if kysec["exec_control"] not in {"off", "on", "not-present"}:
        raise ObservationError("platform Kysec execution-control fact is invalid")
    if kysec["exec_control"] == "on":
        raise ObservationError("Kysec exec control on is not a compatible positive platform state")
    if not kysec["detected"] and (
        kysec["enabled"] or kysec["exec_control"] != "not-present"
    ):
        raise ObservationError("platform Kysec probe returned an inconsistent absent state")
    if kysec["detected"] and kysec["exec_control"] != "off":
        raise ObservationError("detected Kysec must prove exec control off")
    expected_security = profile["security_profile"]
    if expected_security == "kysec-enabled-exec-control-off":
        if not (kysec["detected"] and kysec["enabled"] and kysec["exec_control"] == "off"):
            raise ObservationError("platform security profile does not prove Kysec enabled with exec control off")
    elif expected_security != "supported-default":
        raise ObservationError("platform security profile is not supported")
    normalized_version = "%s/%s" % (version_id, release_id)
    if (
        os_id != profile["os_id"]
        or version_id != profile["version_id"]
        or re.fullmatch(profile["release_id_pattern"], release_id or "") is None
        or desktop not in profile["desktop_environments"]
    ):
        raise ObservationError("platform OS release or desktop does not match the selected certification category")
    return {
        "os_id": os_id,
        "os_version": normalized_version,
        "desktop_environment": desktop,
        "security_facts": {
            "security_profile": expected_security,
            "kysec_detected": kysec["detected"],
            "kysec_enabled": kysec["enabled"],
            "kysec_exec_control": kysec["exec_control"],
            "os_release_sha256": hashlib.sha256(os_release_payload).hexdigest(),
            "os_version_sha256": (
                hashlib.sha256(bytes(os_version_payload)).hexdigest()
                if isinstance(os_version_payload, (bytes, bytearray))
                else "not-present"
            ),
        },
    }


def _require_exact_keys(value, expected, label):
    actual = set(value)
    expected_set = set(expected)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        raise ObservationError("%s fields mismatch: missing=%s extra=%s" % (label, missing, extra))


def _validate_challenge(challenge):
    if not isinstance(challenge, str) or not CHALLENGE_RE.fullmatch(challenge):
        raise ObservationError("challenge must be 64-128 lowercase hexadecimal characters")


def _fingerprint(challenge, identity):
    return hashlib.sha256((challenge + "\0" + identity).encode("utf-8")).hexdigest()


def _machine_identity_commitment(machine_id):
    if not isinstance(machine_id, str) or not MACHINE_ID_RE.fullmatch(machine_id):
        raise ObservationError("target machine identity is not a canonical machine-id")
    return hashlib.sha256(
        (
            MACHINE_IDENTITY_COMMITMENT_DOMAIN
            + "\0"
            + machine_id.lower()
        ).encode("utf-8")
    ).hexdigest()


def _machine_fingerprint_from_commitment(challenge, commitment):
    _validate_challenge(challenge)
    if not isinstance(commitment, str) or not HEX64_RE.fullmatch(commitment):
        raise ObservationError("machine identity commitment is invalid")
    return hashlib.sha256((challenge + "\0" + commitment).encode("utf-8")).hexdigest()


def _read_manifest(path):
    manifest_path = Path(path)
    if not manifest_path.is_absolute() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise ObservationError("manifest must be an absolute regular file")
    value = _load_json(manifest_path, "package manifest")
    required = {
        "schema_version",
        "source_commit",
        "version",
        "deb",
        "deb_sha256",
        "target_baseline_profile_id",
        "target_baseline_sha256",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ObservationError("package manifest is missing fields: %s" % missing)
    if value["schema_version"] != 2:
        raise ObservationError("package manifest schema_version must be 2")
    if not isinstance(value["source_commit"], str) or not COMMIT_RE.fullmatch(value["source_commit"]):
        raise ObservationError("package manifest source_commit is invalid")
    if not isinstance(value["deb_sha256"], str) or not HEX64_RE.fullmatch(value["deb_sha256"]):
        raise ObservationError("package manifest deb_sha256 is invalid")
    if (
        not isinstance(value["deb"], str)
        or Path(value["deb"]).name != value["deb"]
        or not value["deb"].endswith("_amd64.deb")
    ):
        raise ObservationError("package manifest deb basename is invalid")
    if not isinstance(value["target_baseline_sha256"], str) or not HEX64_RE.fullmatch(value["target_baseline_sha256"]):
        raise ObservationError("package manifest target_baseline_sha256 is invalid")
    if not isinstance(value["target_baseline_profile_id"], str) or not PROFILE_RE.fullmatch(value["target_baseline_profile_id"]):
        raise ObservationError("package manifest target_baseline_profile_id is invalid")
    return value


def _read_certification_matrix(path):
    """Load the release-owned closed category matrix without external modules."""
    matrix = _load_json(path, "certification matrix")
    return _validate_certification_matrix(matrix)


def _validate_certification_matrix(matrix):
    if matrix.get("schema") != CERTIFICATION_MATRIX_SCHEMA:
        raise ObservationError("certification matrix schema is invalid")
    if matrix.get("architecture") != "amd64":
        raise ObservationError("certification matrix architecture must be amd64")
    if matrix.get("compatibility_policy_id") != CERTIFICATION_POLICY_ID:
        raise ObservationError("certification matrix compatibility policy is invalid")
    positives = matrix.get("positive_categories")
    negatives = matrix.get("negative_boundaries")
    if not isinstance(positives, list) or not isinstance(negatives, list):
        raise ObservationError("certification matrix category lists are invalid")
    positive_ids = [item.get("id") for item in positives if isinstance(item, dict)]
    negative_ids = [item.get("id") for item in negatives if isinstance(item, dict)]
    if len(positive_ids) != 6 or set(positive_ids) != POSITIVE_CATEGORY_IDS:
        raise ObservationError("certification matrix positive categories are incomplete")
    if len(negative_ids) != 6 or set(negative_ids) != NEGATIVE_CATEGORY_IDS:
        raise ObservationError("certification matrix negative boundaries are incomplete")
    for category in positives:
        if not isinstance(category, dict):
            raise ObservationError("certification matrix positive category is invalid")
        expected_profile = CANONICAL_PLATFORM_PROFILES.get(category.get("id"))
        if category.get("platform_profile") != expected_profile:
            raise ObservationError("certification matrix platform profile is not canonical")
        if category.get("os_ids") != [expected_profile["os_id"]]:
            raise ObservationError("certification matrix OS IDs do not match the platform profile")
        if category.get("desktop_environments") != expected_profile["desktop_environments"]:
            raise ObservationError("certification matrix desktops do not match the platform profile")
    return matrix


def _read_canonical_manifest(path):
    manifest_path = Path(path)
    if not manifest_path.is_absolute() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise ObservationError("manifest must be an absolute regular file")
    value = _load_json(manifest_path, "package manifest")
    if value.get("schema") != "taiji-package-manifest/v3":
        raise ObservationError("canonical target acceptance requires manifest schema taiji-package-manifest/v3")
    required = {
        "package", "version", "architecture", "source_commit", "deb_basename", "deb_sha256",
        "compatibility_policy_id", "compatibility_policy_sha256",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ObservationError("package manifest is missing canonical fields: %s" % missing)
    if value.get("package") != PACKAGE_NAME or value.get("architecture") != "amd64":
        raise ObservationError("package manifest package or architecture is invalid")
    if not isinstance(value["source_commit"], str) or not COMMIT_RE.fullmatch(value["source_commit"]):
        raise ObservationError("package manifest source_commit is invalid")
    if not isinstance(value["version"], str) or not VERSION_RE.fullmatch(value["version"]):
        raise ObservationError("package manifest version is invalid")
    expected_deb = "taiji-agent_%s_amd64.deb" % value["version"]
    if value["deb_basename"] != expected_deb:
        raise ObservationError("package manifest deb/version mismatch")
    for key in ("deb_sha256", "compatibility_policy_sha256"):
        if not isinstance(value[key], str) or not HEX64_RE.fullmatch(value[key]):
            raise ObservationError("package manifest %s is invalid" % key)
    if value["compatibility_policy_id"] != CERTIFICATION_POLICY_ID:
        raise ObservationError("package manifest compatibility policy is invalid")
    return value


def _canonical_environment_record(
    *,
    category_id,
    matrix,
    manifest,
    observation,
    platform_identity,
):
    categories = {
        item["id"]: item
        for item in matrix["positive_categories"] + matrix["negative_boundaries"]
    }
    category = categories.get(category_id)
    if category is None:
        raise ObservationError("category_id is not in the certification matrix")
    if category["kind"] != "positive":
        raise ObservationError("single-DEB installation observation cannot certify a negative boundary")
    if type(platform_identity) is not dict or set(platform_identity) != {
        "os_id", "os_version", "desktop_environment", "security_facts"
    }:
        raise ObservationError("canonical platform identity has an invalid field set")
    os_id = platform_identity["os_id"]
    os_version = platform_identity["os_version"]
    desktop_environment = platform_identity["desktop_environment"]
    security_identity = platform_identity["security_facts"]
    if not isinstance(os_id, str) or not re.fullmatch(r"[a-z0-9._-]{2,32}", os_id):
        raise ObservationError("canonical environment os_id is invalid")
    if os_id not in category.get("os_ids", []):
        raise ObservationError("canonical environment os_id does not match the selected category")
    if not isinstance(os_version, str) or not os_version.strip() or len(os_version) > 128:
        raise ObservationError("canonical environment os_version is invalid")
    if not isinstance(desktop_environment, str) or not desktop_environment.strip() or len(desktop_environment) > 128:
        raise ObservationError("canonical environment desktop_environment is invalid")
    if type(security_identity) is not dict:
        raise ObservationError("canonical environment security identity is invalid")
    if not observation.get("first_launch_eligible"):
        raise ObservationError("canonical environment is not eligible for first launch")
    return {
        "schema": ENVIRONMENT_RECORD_SCHEMA,
        "category_id": category_id,
        "category_kind": "positive",
        "compatibility": "COMPATIBLE",
        "source_commit": manifest["source_commit"],
        "version": manifest["version"],
        "architecture": "amd64",
        "deb_basename": manifest["deb_basename"],
        "deb_sha256": manifest["deb_sha256"],
        "compatibility_policy_id": manifest["compatibility_policy_id"],
        "compatibility_policy_sha256": manifest["compatibility_policy_sha256"],
        "machine_identity_commitment_sha256": observation[
            "machine_identity_commitment_sha256"
        ],
        "os_id": os_id,
        "os_version": os_version.strip(),
        "desktop_environment": desktop_environment.strip(),
        "security_facts": {
            **security_identity,
            "administrator_available": bool(os.geteuid() == 0 or any(
                Path(command).is_file() for command in ("/usr/bin/pkexec", "/usr/bin/sudo", "/bin/sudo")
            )),
            "business_data_mutation": False,
            "graphical_desktop": True,
            "network_observation": observation["network_observation"],
            "package_manager": "dpkg",
        },
        "checks": {
            "preflight": "PASS",
            "install": "PASS",
        },
        "attachments": [],
    }


def _open_candidate_directory(customer_dir):
    root = Path(customer_dir)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ObservationError("candidate directory must be an absolute real directory")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(root), flags)
    except OSError as exc:
        raise ObservationError("cannot safely open candidate directory: %s" % exc) from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise ObservationError("candidate directory must be a real directory")
    identity = (metadata.st_dev, metadata.st_ino, metadata.st_mode)
    return root, descriptor, identity


def _read_candidate_from_directory(root, directory_descriptor, expected_basename, expected_sha256):
    try:
        entries = os.listdir(directory_descriptor)
    except OSError as exc:
        raise ObservationError("cannot inspect candidate directory: %s" % exc) from exc
    if len(entries) != 1:
        raise ObservationError("candidate directory must contain exactly one file")
    candidate_name = entries[0]
    if candidate_name != expected_basename:
        raise ObservationError("candidate DEB does not match the manifest DEB basename")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        candidate_descriptor = os.open(candidate_name, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise ObservationError("candidate DEB must be a safely openable regular single-link file: %s" % exc) from exc
    try:
        metadata = os.fstat(candidate_descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ObservationError("candidate directory must contain one regular single-link amd64 DEB")
        digest = _hash_descriptor(candidate_descriptor, metadata, "candidate DEB")
    finally:
        os.close(candidate_descriptor)
    candidate = root / candidate_name
    if not candidate_name.endswith("_amd64.deb"):
        raise ObservationError("candidate directory must contain one regular single-link amd64 DEB")
    if digest != expected_sha256:
        raise ObservationError("candidate DEB hash does not match the package manifest")
    return candidate, metadata, digest


def _single_candidate(customer_dir, expected_basename, expected_sha256):
    root, directory_descriptor, directory_identity = _open_candidate_directory(customer_dir)
    try:
        candidate, metadata, digest = _read_candidate_from_directory(
            root,
            directory_descriptor,
            expected_basename,
            expected_sha256,
        )
    finally:
        os.close(directory_descriptor)
    return candidate, metadata, digest, directory_identity


def _candidate_identity(path, metadata, digest):
    return {
        "path": str(path.resolve()),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "links": metadata.st_nlink,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "sha256": digest,
    }


def _assert_candidate_unchanged(customer_dir, expected_basename, expected_directory_identity, expected_identity):
    try:
        root, directory_descriptor, directory_identity = _open_candidate_directory(customer_dir)
    except ObservationError as exc:
        raise ObservationError("candidate directory changed during observation: %s" % exc) from exc
    if directory_identity != expected_directory_identity:
        os.close(directory_descriptor)
        raise ObservationError("candidate directory changed during observation")
    try:
        candidate, metadata, digest = _read_candidate_from_directory(
            root,
            directory_descriptor,
            expected_basename,
            expected_identity["sha256"],
        )
    except ObservationError as exc:
        raise ObservationError("candidate DEB changed during observation: %s" % exc) from exc
    finally:
        os.close(directory_descriptor)
    current = _candidate_identity(candidate, metadata, digest)
    if current != expected_identity:
        raise ObservationError("candidate DEB changed during observation")


def _assert_user_state_absent(paths):
    existing = [str(Path(item)) for item in paths if os.path.lexists(str(item))]
    if existing:
        raise ObservationError("Taiji user state already exists before first installation: %s" % existing)


def default_user_state_paths(home=None, environ=None):
    environment = dict(os.environ if environ is None else environ)
    account_home = Path(home or os.path.expanduser("~")).resolve()
    config = Path(environment.get("XDG_CONFIG_HOME") or account_home / ".config")
    data = Path(environment.get("XDG_DATA_HOME") or account_home / ".local" / "share")
    state = Path(environment.get("XDG_STATE_HOME") or account_home / ".local" / "state")
    cache = Path(environment.get("XDG_CACHE_HOME") or account_home / ".cache")
    candidates = [
        config / "taiji-agent",
        config / "taiji-agent-desktop",
        config / "太极 Agent",
        data / "taiji-agent",
        data / "taiji-agent-desktop",
        state / "taiji-agent",
        cache / "taiji-agent",
        cache / "taiji-agent-desktop",
    ]
    return [Path(item) for item in candidates]


def user_context_fingerprints(challenge, user_state_paths):
    """Bind the OS account and exact Taiji state locations without disclosing paths."""
    _validate_challenge(challenge)
    uid = os.getuid()
    try:
        canonical_home = os.path.realpath(pwd.getpwuid(uid).pw_dir)
    except (KeyError, OSError) as exc:
        raise ObservationError("cannot resolve the canonical account home") from exc
    if not os.path.isabs(canonical_home):
        raise ObservationError("canonical account home must be absolute")
    normalized_paths = []
    for item in user_state_paths:
        raw = os.fspath(item)
        if not os.path.isabs(raw):
            raise ObservationError("user state paths must be absolute")
        normalized_paths.append(os.path.normpath(os.path.abspath(raw)))
    if not normalized_paths or len(normalized_paths) != len(set(normalized_paths)):
        raise ObservationError("user state paths must be non-empty and unique")
    canonical_home_fingerprint = _fingerprint(
        challenge,
        "canonical-account-home\0%d\0%s" % (uid, canonical_home),
    )
    user_state_paths_fingerprint = _fingerprint(
        challenge,
        "taiji-user-state-paths\0%d\0%s" % (uid, "\0".join(normalized_paths)),
    )
    return uid, canonical_home_fingerprint, user_state_paths_fingerprint


def _read_trusted_machine_id(
    paths=None,
    trusted_roots=None,
    expected_owner_uid=0,
):
    """Read one unambiguous machine-id through the trusted system-file reader."""
    candidates = (
        (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))
        if paths is None
        else tuple(Path(item) for item in paths)
    )
    roots = (
        (Path("/etc"), Path("/var/lib/dbus"))
        if trusted_roots is None
        else tuple(Path(item) for item in trusted_roots)
    )
    identities = []
    for candidate in candidates:
        payload = _read_trusted_system_file(
            candidate,
            required=False,
            trusted_roots=roots,
            expected_owner_uid=expected_owner_uid,
        )
        if payload is None:
            continue
        try:
            value = payload.decode("ascii").strip()
        except (AttributeError, UnicodeError) as exc:
            raise ObservationError("target machine identity is not ASCII") from exc
        if not MACHINE_ID_RE.fullmatch(value):
            raise ObservationError("target machine identity is malformed")
        identities.append(value.lower())
    if not identities:
        raise ObservationError("target machine identity is unavailable")
    if len(set(identities)) != 1:
        raise ObservationError("target machine identity files disagree")
    return identities[0]


class SystemRuntime:
    """Small fixed-command adapter used by the pre-install observer."""

    def __init__(self):
        self.dpkg_query = Path("/usr/bin/dpkg-query")
        self.ip = next((Path(item) for item in ("/usr/sbin/ip", "/usr/bin/ip") if Path(item).is_file()), None)
        if not self.dpkg_query.is_file():
            raise ObservationError("/usr/bin/dpkg-query is required")
        if self.ip is None:
            raise ObservationError("the system ip command is required to observe network state")

    @staticmethod
    def _run(argv):
        try:
            return subprocess.run(argv, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ObservationError("cannot run fixed system command: %s" % exc) from exc

    def package_status(self):
        result = self._run([str(self.dpkg_query), "-W", "-f=${db:Status-Abbrev}\t${Status}\n", PACKAGE_NAME])
        if result.returncode != 0:
            return None
        line = result.stdout.strip()
        if "\t" not in line:
            raise ObservationError("dpkg-query returned an unexpected package status")
        abbreviation, status_text = line.split("\t", 1)
        if not abbreviation.startswith("ii") and status_text == "install ok installed":
            raise ObservationError("dpkg-query returned inconsistent installed status")
        return status_text.strip()

    def network_is_offline(self):
        commands = (
            [str(self.ip), "-o", "link", "show", "up"],
            [str(self.ip), "-o", "addr", "show", "scope", "global"],
            [str(self.ip), "route", "show"],
            [str(self.ip), "-6", "route", "show"],
        )
        outputs = []
        for argv in commands:
            result = self._run(argv)
            if result.returncode != 0:
                raise ObservationError("cannot inspect target network interfaces, addresses, and routes")
            outputs.append([line.strip() for line in result.stdout.splitlines() if line.strip()])
        return network_outputs_are_offline(
            link_lines=outputs[0],
            global_address_lines=outputs[1],
            ipv4_route_lines=outputs[2],
            ipv6_route_lines=outputs[3],
        )

    def identity(self):
        boot_path = Path("/proc/sys/kernel/random/boot_id")
        if not boot_path.is_file():
            raise ObservationError("target machine and boot identity are unavailable")
        try:
            machine_id = _read_trusted_machine_id()
            boot_id = boot_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise ObservationError("cannot read target machine identity: %s" % exc) from exc
        if not MACHINE_ID_RE.fullmatch(machine_id) or not re.fullmatch(r"[0-9a-fA-F-]{16,64}", boot_id):
            raise ObservationError("target machine or boot identity is malformed")
        return machine_id.lower(), boot_id.lower()

    @staticmethod
    def monotonic():
        return time.monotonic()

    @staticmethod
    def utc_now():
        return datetime.now(timezone.utc)

    @staticmethod
    def sleep(seconds):
        time.sleep(seconds)


def _route_is_loopback_only(line):
    fields = line.split()
    if not fields:
        return True
    if "dev" not in fields:
        return False
    dev_index = fields.index("dev")
    if dev_index + 1 >= len(fields) or fields[dev_index + 1] != "lo":
        return False
    destination_index = 1 if fields[0] == "local" and len(fields) > 1 else 0
    destination = fields[destination_index]
    try:
        network = ipaddress.ip_network(destination, strict=False)
    except ValueError:
        return False
    loopback = ipaddress.ip_network("127.0.0.0/8") if network.version == 4 else ipaddress.ip_network("::1/128")
    return network.subnet_of(loopback)


def network_outputs_are_offline(link_lines, global_address_lines, ipv4_route_lines, ipv6_route_lines):
    """Return true only when parsed ``ip`` output contains loopback state."""
    for line in link_lines:
        fields = line.split(":", 2)
        if len(fields) < 2 or fields[1].strip().split("@", 1)[0] != "lo":
            return False
    if global_address_lines:
        return False
    return all(
        _route_is_loopback_only(line)
        for line in list(ipv4_route_lines) + list(ipv6_route_lines)
    )


def observe_install(customer_dir, manifest_path, challenge, user_state_paths, runtime, timeout_seconds, poll_interval_seconds):
    """Observe a complete absent-to-installed transition and return its record."""
    _validate_challenge(challenge)
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ObservationError("timeout_seconds must be positive")
    if not isinstance(poll_interval_seconds, (int, float)) or poll_interval_seconds <= 0:
        raise ObservationError("poll_interval_seconds must be positive")
    manifest = _read_manifest(manifest_path)
    candidate, metadata, digest, candidate_directory_identity = _single_candidate(
        customer_dir,
        manifest["deb"],
        manifest["deb_sha256"],
    )
    identity = _candidate_identity(candidate, metadata, digest)
    target_uid, canonical_home_fingerprint, user_state_paths_fingerprint = (
        user_context_fingerprints(challenge, user_state_paths)
    )
    _assert_user_state_absent(user_state_paths)
    machine_id, boot_id = runtime.identity()
    status_before = runtime.package_status()
    if status_before is not None:
        raise ObservationError("observer must start before the package is installed and before any dpkg package record exists")
    if not runtime.network_is_offline():
        raise ObservationError("non-loopback network is available before installation")

    started_utc = runtime.utc_now()
    deadline = runtime.monotonic() + timeout_seconds
    samples = 1
    transitions = ["not-installed"]
    current_status = status_before
    while current_status != "install ok installed":
        if runtime.monotonic() >= deadline:
            raise ObservationError("timed out before dpkg reported install ok installed")
        runtime.sleep(poll_interval_seconds)
        current_machine, current_boot = runtime.identity()
        if (current_machine, current_boot) != (machine_id, boot_id):
            raise ObservationError("machine or boot identity changed during observation")
        _assert_user_state_absent(user_state_paths)
        _assert_candidate_unchanged(
            customer_dir,
            manifest["deb"],
            candidate_directory_identity,
            identity,
        )
        if not runtime.network_is_offline():
            raise ObservationError("non-loopback network became available during installation")
        samples += 1
        current_status = runtime.package_status()
        status_label = current_status if current_status is not None else "not-installed"
        if status_label != transitions[-1]:
            transitions.append(status_label)

    _assert_user_state_absent(user_state_paths)
    _assert_candidate_unchanged(
        customer_dir,
        manifest["deb"],
        candidate_directory_identity,
        identity,
    )
    final_machine, final_boot = runtime.identity()
    if (final_machine, final_boot) != (machine_id, boot_id):
        raise ObservationError("machine or boot identity changed at installation completion")
    if not runtime.network_is_offline():
        raise ObservationError("non-loopback network became available at installation completion")
    if user_context_fingerprints(challenge, user_state_paths) != (
        target_uid,
        canonical_home_fingerprint,
        user_state_paths_fingerprint,
    ):
        raise ObservationError("uid, canonical home, or user state paths changed during observation")
    samples += 1
    if _sha256_path(candidate) != manifest["deb_sha256"]:
        raise ObservationError("candidate DEB changed during observation")
    completed_utc = runtime.utc_now()
    return {
        "schema": OBSERVATION_SCHEMA,
        "generated_at_utc": _utc_text(completed_utc),
        "started_at_utc": _utc_text(started_utc),
        "completed_at_utc": _utc_text(completed_utc),
        "challenge_nonce": challenge,
        "machine_fingerprint_sha256": _fingerprint(challenge, machine_id),
        "boot_fingerprint_sha256": _fingerprint(challenge, boot_id),
        "target_uid": target_uid,
        "canonical_home_fingerprint_sha256": canonical_home_fingerprint,
        "user_state_paths_fingerprint_sha256": user_state_paths_fingerprint,
        "source_commit": manifest["source_commit"],
        "manifest_sha256": _sha256_path(manifest_path),
        "deb_observed_basename": candidate.name,
        "deb_sha256": digest,
        "target_baseline_profile_id": manifest["target_baseline_profile_id"],
        "target_baseline_sha256": manifest["target_baseline_sha256"],
        "candidate_file_count": 1,
        "additional_install_files_observed": False,
        "package_status_before": "not-installed",
        "package_status_after": "install ok installed",
        "package_status_transitions": transitions,
        "network_observation": "continuous-process-sampling-no-non-loopback-up",
        "network_sample_interval_ms": int(round(poll_interval_seconds * 1000)),
        "network_sample_count": samples,
        "user_state_before": "absent",
        "user_state_after_install_before_first_launch": "absent",
        "first_launch_eligible": True,
        "installation_method_machine_observed": False,
        "observation_process_continuous": True,
    }


def observe_environment_install(
    customer_dir,
    manifest_path,
    matrix_path,
    category_id,
    challenge,
    user_state_paths,
    runtime,
    timeout_seconds,
    poll_interval_seconds,
    platform_identity=None,
):
    """Observe a canonical v3 installation and emit one category-bound record.

    This deliberately reuses the same continuous absent-to-installed checks as
    the legacy observer, but its output has no target-baseline/profile fields.
    A local record reports compatibility facts only; certification is decided
    later by the complete certification-set validator.
    """
    _validate_challenge(challenge)
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ObservationError("timeout_seconds must be positive")
    if not isinstance(poll_interval_seconds, (int, float)) or poll_interval_seconds <= 0:
        raise ObservationError("poll_interval_seconds must be positive")
    matrix = _read_certification_matrix(matrix_path)
    live_platform_identity = platform_identity is None
    if platform_identity is None:
        platform_identity = collect_platform_identity(matrix, category_id)
    manifest = _read_canonical_manifest(manifest_path)
    candidate, metadata, digest, candidate_directory_identity = _single_candidate(
        customer_dir,
        manifest["deb_basename"],
        manifest["deb_sha256"],
    )
    identity = _candidate_identity(candidate, metadata, digest)
    target_uid, canonical_home_fingerprint, user_state_paths_fingerprint = (
        user_context_fingerprints(challenge, user_state_paths)
    )
    _assert_user_state_absent(user_state_paths)
    machine_id, boot_id = runtime.identity()
    machine_identity_commitment = _machine_identity_commitment(machine_id)
    status_before = runtime.package_status()
    if status_before is not None:
        raise ObservationError("observer must start before the package is installed and before any dpkg package record exists")
    if not runtime.network_is_offline():
        raise ObservationError("non-loopback network is available before installation")
    started_utc = runtime.utc_now()
    deadline = runtime.monotonic() + timeout_seconds
    samples = 1
    transitions = ["not-installed"]
    current_status = status_before
    while current_status != "install ok installed":
        if runtime.monotonic() >= deadline:
            raise ObservationError("timed out before dpkg reported install ok installed")
        runtime.sleep(poll_interval_seconds)
        current_machine, current_boot = runtime.identity()
        if (current_machine, current_boot) != (machine_id, boot_id):
            raise ObservationError("machine or boot identity changed during observation")
        _assert_user_state_absent(user_state_paths)
        _assert_candidate_unchanged(customer_dir, manifest["deb_basename"], candidate_directory_identity, identity)
        if not runtime.network_is_offline():
            raise ObservationError("non-loopback network became available during installation")
        samples += 1
        current_status = runtime.package_status()
        status_label = current_status if current_status is not None else "not-installed"
        if status_label != transitions[-1]:
            transitions.append(status_label)
    _assert_user_state_absent(user_state_paths)
    _assert_candidate_unchanged(customer_dir, manifest["deb_basename"], candidate_directory_identity, identity)
    final_machine, final_boot = runtime.identity()
    if (final_machine, final_boot) != (machine_id, boot_id):
        raise ObservationError("machine or boot identity changed at installation completion")
    if not runtime.network_is_offline():
        raise ObservationError("non-loopback network became available at installation completion")
    if user_context_fingerprints(challenge, user_state_paths) != (
        target_uid,
        canonical_home_fingerprint,
        user_state_paths_fingerprint,
    ):
        raise ObservationError("uid, canonical home, or user state paths changed during observation")
    samples += 1
    if _sha256_path(candidate) != manifest["deb_sha256"]:
        raise ObservationError("candidate DEB changed during observation")
    completed_utc = runtime.utc_now()
    if live_platform_identity and collect_platform_identity(matrix, category_id) != platform_identity:
        raise ObservationError("platform identity changed during installation observation")
    observation = {
        "schema": "taiji.single-deb-install-observation/v2",
        "generated_at_utc": _utc_text(completed_utc),
        "started_at_utc": _utc_text(started_utc),
        "completed_at_utc": _utc_text(completed_utc),
        "challenge_nonce": challenge,
        "machine_identity_commitment_sha256": machine_identity_commitment,
        "machine_fingerprint_sha256": _machine_fingerprint_from_commitment(
            challenge, machine_identity_commitment
        ),
        "boot_fingerprint_sha256": _fingerprint(challenge, boot_id),
        "target_uid": target_uid,
        "canonical_home_fingerprint_sha256": canonical_home_fingerprint,
        "user_state_paths_fingerprint_sha256": user_state_paths_fingerprint,
        "source_commit": manifest["source_commit"],
        "manifest_sha256": _sha256_path(manifest_path),
        "deb_observed_basename": candidate.name,
        "deb_sha256": digest,
        "candidate_file_count": 1,
        "additional_install_files_observed": False,
        "package_status_before": "not-installed",
        "package_status_after": "install ok installed",
        "package_status_transitions": transitions,
        "network_observation": "continuous-process-sampling-no-non-loopback-up",
        "network_sample_interval_ms": int(round(poll_interval_seconds * 1000)),
        "network_sample_count": samples,
        "user_state_before": "absent",
        "user_state_after_install_before_first_launch": "absent",
        "first_launch_eligible": True,
        "installation_method_machine_observed": False,
        "observation_process_continuous": True,
    }
    record = _canonical_environment_record(
        category_id=category_id,
        matrix=matrix,
        manifest=manifest,
        observation=observation,
        platform_identity=platform_identity,
    )
    return observation, record


OBSERVATION_KEYS = {
    "schema", "generated_at_utc", "started_at_utc", "completed_at_utc", "challenge_nonce",
    "machine_fingerprint_sha256", "boot_fingerprint_sha256", "source_commit", "manifest_sha256",
    "target_uid", "canonical_home_fingerprint_sha256", "user_state_paths_fingerprint_sha256",
    "deb_observed_basename", "deb_sha256", "target_baseline_profile_id", "target_baseline_sha256",
    "candidate_file_count", "additional_install_files_observed", "package_status_before",
    "package_status_after", "package_status_transitions", "network_observation",
    "network_sample_interval_ms", "network_sample_count", "user_state_before",
    "user_state_after_install_before_first_launch", "first_launch_eligible",
    "installation_method_machine_observed", "observation_process_continuous",
}

CANONICAL_OBSERVATION_KEYS = OBSERVATION_KEYS - {
    "target_baseline_profile_id", "target_baseline_sha256",
} | {"machine_identity_commitment_sha256"}

ATTESTATION_KEYS = {
    "schema", "generated_at_utc", "observation_basename", "observation_sha256",
    "challenge_nonce", "machine_fingerprint_sha256", "boot_fingerprint_sha256",
    "deb_sha256", "installation_method_attested", "installation_method_machine_observed",
    "attestation_scope", "operator_id", "confirmation",
    "graphical_installer_evidence_basename", "graphical_installer_evidence_sha256",
}


def _validate_png_evidence(path):
    evidence = Path(path)
    if not evidence.is_absolute() or evidence.is_symlink() or not evidence.is_file():
        raise ObservationError("graphical installer evidence must be an absolute regular PNG file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(evidence), flags)
    except OSError as exc:
        raise ObservationError("cannot safely open graphical installer evidence: %s" % exc) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ObservationError("graphical installer evidence must be a regular single-link PNG file")
        if metadata.st_size < 57 or metadata.st_size > 20 * 1024 * 1024:
            raise ObservationError("graphical installer evidence must be a bounded PNG file")
        payload = bytearray()
        while len(payload) < metadata.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, metadata.st_size - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) != metadata.st_size or _file_stat_identity(metadata) != _file_stat_identity(os.fstat(descriptor)):
            raise ObservationError("graphical installer PNG changed while it was read")
    except OSError as exc:
        raise ObservationError("cannot read graphical installer evidence PNG: %s" % exc) from exc
    finally:
        os.close(descriptor)
    _validate_png_structure(bytes(payload))
    return evidence


def _validate_png_structure(payload):
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ObservationError("graphical installer evidence has an invalid PNG signature")
    offset = 8
    chunk_index = 0
    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ObservationError("graphical installer evidence PNG is truncated")
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        kind = payload[offset + 4:offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(payload):
            raise ObservationError("graphical installer evidence PNG chunk is truncated")
        chunk_payload = payload[offset + 8:offset + 8 + length]
        recorded_crc = struct.unpack(">I", payload[offset + 8 + length:chunk_end])[0]
        actual_crc = zlib.crc32(kind + chunk_payload) & 0xFFFFFFFF
        if recorded_crc != actual_crc:
            raise ObservationError("graphical installer evidence PNG chunk CRC is invalid")
        if chunk_index == 0:
            if kind != b"IHDR" or length != 13:
                raise ObservationError("graphical installer evidence PNG must start with a valid IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk_payload
            )
            if width < 800 or height < 600 or width > 8192 or height > 8192:
                raise ObservationError("graphical installer evidence PNG must be at least 800x600 with bounded dimensions")
            if bit_depth not in {1, 2, 4, 8, 16} or color_type not in {0, 2, 3, 4, 6}:
                raise ObservationError("graphical installer evidence PNG IHDR pixel format is invalid")
            if compression != 0 or filtering != 0 or interlace not in {0, 1}:
                raise ObservationError("graphical installer evidence PNG IHDR encoding is invalid")
            saw_ihdr = True
        elif kind == b"IHDR":
            raise ObservationError("graphical installer evidence PNG contains duplicate IHDR")
        if kind == b"IDAT":
            saw_idat = True
        if kind == b"IEND":
            if length != 0 or chunk_end != len(payload):
                raise ObservationError("graphical installer evidence PNG has an invalid IEND or trailing data")
            saw_iend = True
            offset = chunk_end
            break
        offset = chunk_end
        chunk_index += 1
    if not saw_ihdr or not saw_idat or not saw_iend or offset != len(payload):
        raise ObservationError("graphical installer evidence PNG is incomplete")


def _validate_observation_identity(observation, challenge, runtime, user_state_paths=None, canonical=False):
    expected_keys = CANONICAL_OBSERVATION_KEYS if canonical else OBSERVATION_KEYS
    _require_exact_keys(observation, expected_keys, "install observation")
    expected_schema = "taiji.single-deb-install-observation/v2" if canonical else OBSERVATION_SCHEMA
    if observation.get("schema") != expected_schema:
        raise ObservationError("install observation schema is invalid")
    if observation.get("challenge_nonce") != challenge:
        raise ObservationError("install observation challenge does not match")
    machine_id, boot_id = runtime.identity()
    if canonical:
        expected_commitment = _machine_identity_commitment(machine_id)
        if observation.get("machine_identity_commitment_sha256") != expected_commitment:
            raise ObservationError(
                "install observation machine identity commitment does not match the current target"
            )
        expected_machine = _machine_fingerprint_from_commitment(
            challenge, expected_commitment
        )
    else:
        expected_machine = _fingerprint(challenge, machine_id)
    expected_boot = _fingerprint(challenge, boot_id)
    if observation.get("machine_fingerprint_sha256") != expected_machine:
        raise ObservationError("install observation machine does not match the current target")
    if observation.get("boot_fingerprint_sha256") != expected_boot:
        raise ObservationError("install observation boot identity does not match the current target")
    current_uid, current_home_fingerprint, current_paths_fingerprint = user_context_fingerprints(
        challenge,
        default_user_state_paths() if user_state_paths is None else user_state_paths,
    )
    if observation.get("target_uid") != current_uid:
        raise ObservationError("install observation uid does not match the current target user")
    if observation.get("canonical_home_fingerprint_sha256") != current_home_fingerprint:
        raise ObservationError("install observation canonical home does not match the current target user")
    if observation.get("user_state_paths_fingerprint_sha256") != current_paths_fingerprint:
        raise ObservationError("install observation user state paths do not match the current target user")
    return expected_machine, expected_boot


def create_method_attestation(
    observation_path,
    graphical_evidence_path,
    challenge,
    operator_id,
    runtime,
    user_state_paths=None,
    matrix_path=None,
    category_id=None,
    environment_observation_path=None,
):
    """Create an explicit human attestation bound to machine evidence.

    The machine does not claim that it can infer a double click.  It records the
    operator's narrowly scoped statement and binds that statement to the
    observer record and a screenshot of the system graphical installer.
    """
    _validate_challenge(challenge)
    if not isinstance(operator_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}", operator_id):
        raise ObservationError("operator_id must be a stable 3-64 character identifier")
    observation_file = Path(observation_path)
    if not observation_file.is_absolute() or observation_file.is_symlink() or not observation_file.is_file():
        raise ObservationError("install observation must be an absolute regular file")
    observation, observation_digest = _load_json_snapshot(
        observation_file, "install observation"
    )
    schema = observation.get("schema")
    canonical_values = (matrix_path, category_id, environment_observation_path)
    if schema == "taiji.single-deb-install-observation/v2":
        if any(value is None for value in canonical_values):
            raise ObservationError(
                "canonical install attestation requires matrix, category, and environment observation"
            )
        matrix_file = Path(matrix_path)
        environment_file = Path(environment_observation_path)
        matrix, _matrix_digest = _load_json_snapshot(
            matrix_file, "certification matrix"
        )
        matrix = _validate_certification_matrix(matrix)
        environment_record, _environment_digest = _load_json_snapshot(
            environment_file, "environment observation"
        )
        machine_fingerprint, boot_fingerprint = _validate_canonical_attestation_context(
            observation=observation,
            observation_file=observation_file,
            challenge=challenge,
            runtime=runtime,
            user_state_paths=user_state_paths,
            matrix_path=matrix_file,
            matrix=matrix,
            category_id=category_id,
            environment_observation_path=environment_file,
            environment_record=environment_record,
        )
    elif schema == OBSERVATION_SCHEMA:
        if any(value is not None for value in canonical_values):
            raise ObservationError("legacy install attestation does not accept canonical evidence inputs")
        machine_fingerprint, boot_fingerprint = _validate_observation_identity(
            observation,
            challenge,
            runtime,
            user_state_paths=user_state_paths,
        )
    else:
        raise ObservationError("install observation schema is invalid")
    if runtime.package_status() != "install ok installed":
        raise ObservationError("taiji-agent is not installed while method is being attested")
    evidence = _validate_png_evidence(graphical_evidence_path)
    return {
        "schema": ATTESTATION_SCHEMA,
        "generated_at_utc": _utc_text(runtime.utc_now()),
        "observation_basename": observation_file.name,
        "observation_sha256": observation_digest,
        "challenge_nonce": challenge,
        "machine_fingerprint_sha256": machine_fingerprint,
        "boot_fingerprint_sha256": boot_fingerprint,
        "deb_sha256": observation["deb_sha256"],
        "installation_method_attested": "desktop-double-click",
        "installation_method_machine_observed": False,
        "attestation_scope": "human-observed-system-graphical-installer",
        "operator_id": operator_id,
        "confirmation": True,
        "graphical_installer_evidence_basename": evidence.name,
        "graphical_installer_evidence_sha256": _sha256_path(evidence),
    }


def _validate_canonical_attestation_context(
    *,
    observation,
    observation_file,
    challenge,
    runtime,
    user_state_paths,
    matrix_path,
    matrix,
    category_id,
    environment_observation_path,
    environment_record,
):
    if (
        not matrix_path.is_absolute()
        or matrix_path.is_symlink()
        or not matrix_path.is_file()
    ):
        raise ObservationError("canonical attestation matrix must be an absolute regular file")
    if (
        not environment_observation_path.is_absolute()
        or environment_observation_path.is_symlink()
        or not environment_observation_path.is_file()
        or environment_observation_path.name != ENVIRONMENT_RECORD_BASENAME
        or environment_observation_path.parent != observation_file.parent
    ):
        raise ObservationError(
            "canonical attestation environment observation must be the fixed peer of the install observation"
        )
    machine_fingerprint, boot_fingerprint = _validate_observation_identity(
        observation,
        challenge,
        runtime,
        user_state_paths=user_state_paths,
        canonical=True,
    )
    record = environment_record
    if record.get("schema") != ENVIRONMENT_RECORD_SCHEMA:
        raise ObservationError("environment observation schema is invalid")
    if record.get("category_id") != category_id:
        raise ObservationError("environment observation category does not match")
    if record.get("source_commit") != observation.get("source_commit"):
        raise ObservationError("environment observation source commit does not match")
    if record.get("deb_sha256") != observation.get("deb_sha256"):
        raise ObservationError("environment observation DEB hash does not match")
    if record.get("deb_basename") != observation.get("deb_observed_basename"):
        raise ObservationError("environment observation DEB basename does not match")
    if record.get("machine_identity_commitment_sha256") != observation.get(
        "machine_identity_commitment_sha256"
    ):
        raise ObservationError("environment observation machine commitment does not match")
    version = record.get("version")
    deb_basename = record.get("deb_basename")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ObservationError("environment observation version is invalid")
    if deb_basename != "taiji-agent_%s_amd64.deb" % version:
        raise ObservationError("environment observation DEB basename is invalid")
    if record.get("architecture") != "amd64":
        raise ObservationError("environment observation architecture is invalid")
    if record.get("compatibility_policy_id") != CERTIFICATION_POLICY_ID:
        raise ObservationError("environment observation compatibility policy is invalid")
    policy_sha = record.get("compatibility_policy_sha256")
    if not isinstance(policy_sha, str) or not HEX64_RE.fullmatch(policy_sha):
        raise ObservationError("environment observation compatibility policy hash is invalid")
    platform_identity = collect_platform_identity(matrix, category_id)
    expected = _canonical_environment_record(
        category_id=category_id,
        matrix=matrix,
        manifest={
            "source_commit": record["source_commit"],
            "version": version,
            "deb_basename": deb_basename,
            "deb_sha256": record["deb_sha256"],
            "compatibility_policy_id": record["compatibility_policy_id"],
            "compatibility_policy_sha256": policy_sha,
        },
        observation=observation,
        platform_identity=platform_identity,
    )
    _require_exact_keys(record, expected.keys(), "environment observation")
    if record != expected:
        raise ObservationError(
            "environment observation does not match the canonical install facts"
        )
    return machine_fingerprint, boot_fingerprint


def verify_method_attestation(
    attestation_path,
    observation_path,
    graphical_evidence_path,
    challenge,
    runtime,
    user_state_paths=None,
    canonical=False,
):
    _validate_challenge(challenge)
    attestation = _load_json(attestation_path, "install method attestation")
    _require_exact_keys(attestation, ATTESTATION_KEYS, "install method attestation")
    observation = _load_json(observation_path, "install observation")
    machine_fingerprint, boot_fingerprint = _validate_observation_identity(
        observation,
        challenge,
        runtime,
        user_state_paths=user_state_paths,
        canonical=canonical,
    )
    evidence = _validate_png_evidence(graphical_evidence_path)
    fixed = {
        "schema": ATTESTATION_SCHEMA,
        "observation_basename": Path(observation_path).name,
        "observation_sha256": _sha256_path(observation_path),
        "challenge_nonce": challenge,
        "machine_fingerprint_sha256": machine_fingerprint,
        "boot_fingerprint_sha256": boot_fingerprint,
        "deb_sha256": observation["deb_sha256"],
        "installation_method_attested": "desktop-double-click",
        "installation_method_machine_observed": False,
        "attestation_scope": "human-observed-system-graphical-installer",
        "confirmation": True,
        "graphical_installer_evidence_basename": evidence.name,
        "graphical_installer_evidence_sha256": _sha256_path(evidence),
    }
    for key, expected in fixed.items():
        if attestation.get(key) != expected:
            if key == "graphical_installer_evidence_sha256":
                raise ObservationError("graphical installer evidence hash does not match the method attestation")
            raise ObservationError("install method attestation %s does not match" % key)
    if not isinstance(attestation.get("operator_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}", attestation["operator_id"]):
        raise ObservationError("install method attestation operator_id is invalid")
    generated = _parse_utc(attestation.get("generated_at_utc"), "install method attestation generated_at_utc")
    completed = _parse_utc(observation.get("completed_at_utc"), "install observation completed_at_utc")
    now = runtime.utc_now()
    if not completed <= generated <= now:
        raise ObservationError("install method attestation timestamp is not ordered")
    return attestation


def verify_observation(
    observation_path,
    manifest_path,
    deb_path,
    challenge,
    runtime,
    max_age_seconds=86400,
    user_state_paths=None,
):
    """Verify an observation against the current target, challenge, and bytes."""
    _validate_challenge(challenge)
    observation = _load_json(observation_path, "install observation")
    _require_exact_keys(observation, OBSERVATION_KEYS, "install observation")
    manifest = _read_manifest(manifest_path)
    if observation["schema"] != OBSERVATION_SCHEMA:
        raise ObservationError("install observation schema is invalid")
    if observation["challenge_nonce"] != challenge:
        raise ObservationError("install observation challenge does not match")
    machine_id, boot_id = runtime.identity()
    if observation["machine_fingerprint_sha256"] != _fingerprint(challenge, machine_id):
        raise ObservationError("install observation machine does not match the current target")
    if observation["boot_fingerprint_sha256"] != _fingerprint(challenge, boot_id):
        raise ObservationError("install observation boot identity does not match the current target")
    current_uid, current_home_fingerprint, current_paths_fingerprint = user_context_fingerprints(
        challenge,
        default_user_state_paths() if user_state_paths is None else user_state_paths,
    )
    if observation["target_uid"] != current_uid:
        raise ObservationError("install observation uid does not match the current target user")
    if observation["canonical_home_fingerprint_sha256"] != current_home_fingerprint:
        raise ObservationError("install observation canonical home does not match the current target user")
    if observation["user_state_paths_fingerprint_sha256"] != current_paths_fingerprint:
        raise ObservationError("install observation user state paths do not match the current target user")
    started = _parse_utc(observation["started_at_utc"], "install observation started_at_utc")
    completed = _parse_utc(observation["completed_at_utc"], "install observation completed_at_utc")
    generated = _parse_utc(observation["generated_at_utc"], "install observation generated_at_utc")
    now = runtime.utc_now()
    if not started <= completed <= generated <= now:
        raise ObservationError("install observation timestamps are not ordered")
    if (now - completed).total_seconds() > max_age_seconds:
        raise ObservationError("install observation is too old")
    if runtime.package_status() != "install ok installed":
        raise ObservationError("taiji-agent is not currently installed")
    deb = Path(deb_path)
    if not deb.is_absolute() or deb.is_symlink() or not deb.is_file():
        raise ObservationError("candidate DEB must remain an absolute regular file")
    if deb.name != manifest["deb"]:
        raise ObservationError("current candidate does not match the manifest DEB basename")
    deb_hash = _sha256_path(deb)
    fixed_values = {
        "source_commit": manifest["source_commit"],
        "manifest_sha256": _sha256_path(manifest_path),
        "deb_sha256": manifest["deb_sha256"],
        "target_baseline_profile_id": manifest["target_baseline_profile_id"],
        "target_baseline_sha256": manifest["target_baseline_sha256"],
        "candidate_file_count": 1,
        "additional_install_files_observed": False,
        "package_status_before": "not-installed",
        "package_status_after": "install ok installed",
        "network_observation": "continuous-process-sampling-no-non-loopback-up",
        "user_state_before": "absent",
        "user_state_after_install_before_first_launch": "absent",
        "first_launch_eligible": True,
        "installation_method_machine_observed": False,
        "observation_process_continuous": True,
    }
    for key, expected in fixed_values.items():
        if observation.get(key) != expected:
            raise ObservationError("install observation %s does not match the required value" % key)
    if observation["deb_sha256"] != deb_hash:
        raise ObservationError("install observation DEB hash does not match the current candidate")
    transitions = observation["package_status_transitions"]
    if not isinstance(transitions, list) or not transitions or transitions[0] != "not-installed" or transitions[-1] != "install ok installed":
        raise ObservationError("install observation package status transitions are invalid")
    if type(observation["network_sample_interval_ms"]) is not int or observation["network_sample_interval_ms"] <= 0:
        raise ObservationError("install observation network sample interval is invalid")
    if type(observation["network_sample_count"]) is not int or observation["network_sample_count"] < 2:
        raise ObservationError("install observation network sample count is invalid")
    if observation["deb_observed_basename"] != manifest["deb"]:
        raise ObservationError("install observation candidate basename is invalid")
    return observation


def verify_environment_observation(
    observation_path,
    environment_observation_path,
    matrix_path,
    category_id,
    manifest_path,
    deb_path,
    challenge,
    runtime,
    max_age_seconds=86400,
    user_state_paths=None,
):
    """Verify the canonical install observation and its non-final seed."""
    _validate_challenge(challenge)
    matrix = _read_certification_matrix(matrix_path)
    manifest = _read_canonical_manifest(manifest_path)
    observation = _load_json(observation_path, "canonical install observation")
    _validate_observation_identity(
        observation,
        challenge,
        runtime,
        user_state_paths=user_state_paths,
        canonical=True,
    )
    record = _load_json(environment_observation_path, "environment observation")
    current_platform_identity = collect_platform_identity(matrix, category_id)
    expected = _canonical_environment_record(
        category_id=category_id,
        matrix=matrix,
        manifest=manifest,
        observation=observation,
        platform_identity=current_platform_identity,
    )
    _require_exact_keys(record, expected.keys(), "environment observation")
    if record != expected:
        raise ObservationError("environment observation does not match canonical install facts")
    started = _parse_utc(observation["started_at_utc"], "install observation started_at_utc")
    completed = _parse_utc(observation["completed_at_utc"], "install observation completed_at_utc")
    generated = _parse_utc(observation["generated_at_utc"], "install observation generated_at_utc")
    now = runtime.utc_now()
    if not started <= completed <= generated <= now:
        raise ObservationError("canonical install observation timestamps are not ordered")
    if (now - completed).total_seconds() > max_age_seconds:
        raise ObservationError("canonical install observation is too old")
    if runtime.package_status() != "install ok installed":
        raise ObservationError("taiji-agent is not currently installed")
    deb = Path(deb_path)
    if not deb.is_absolute() or deb.is_symlink() or not deb.is_file():
        raise ObservationError("candidate DEB must remain an absolute regular file")
    if deb.name != manifest["deb_basename"]:
        raise ObservationError("current candidate does not match the canonical manifest DEB basename")
    deb_hash = _sha256_path(deb)
    for key, expected_value in {
        "source_commit": manifest["source_commit"],
        "manifest_sha256": _sha256_path(manifest_path),
        "deb_sha256": manifest["deb_sha256"],
        "candidate_file_count": 1,
        "additional_install_files_observed": False,
        "package_status_before": "not-installed",
        "package_status_after": "install ok installed",
        "network_observation": "continuous-process-sampling-no-non-loopback-up",
        "user_state_before": "absent",
        "user_state_after_install_before_first_launch": "absent",
        "first_launch_eligible": True,
        "installation_method_machine_observed": False,
        "observation_process_continuous": True,
    }.items():
        if observation.get(key) != expected_value:
            raise ObservationError("canonical install observation %s does not match" % key)
    if observation["deb_sha256"] != deb_hash or record["deb_sha256"] != deb_hash:
        raise ObservationError("canonical install observation DEB hash does not match current candidate")
    transitions = observation["package_status_transitions"]
    if not isinstance(transitions, list) or not transitions or transitions[0] != "not-installed" or transitions[-1] != "install ok installed":
        raise ObservationError("canonical install observation package transitions are invalid")
    return record


def _atomic_json(output_path, value):
    output_path = Path(output_path)
    parent_descriptor = _open_safe_output_parent(output_path.parent)
    temporary_name = ".%s.%s" % (output_path.name, secrets.token_hex(12))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            os.stat(output_path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ObservationError("refusing to overwrite existing evidence: %s" % output_path)
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            output_path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    except BaseException:
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except OSError:
            pass
        raise
    finally:
        os.close(parent_descriptor)


def _open_safe_output_parent(parent):
    directory = Path(parent)
    if not directory.is_absolute():
        raise ObservationError("output parent must be an absolute directory")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory.anchor, flags)
    except OSError as exc:
        raise ObservationError("output parent root cannot be opened safely: %s" % exc) from exc
    current = Path(directory.anchor)
    for component in directory.parts[1:]:
        current = current / component
        try:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
        except OSError as exc:
            os.close(descriptor)
            raise ObservationError("output parent component must be a real directory: %s" % current) from exc
        os.close(descriptor)
        descriptor = next_descriptor
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ObservationError("output parent must be a real directory")
    return descriptor


def _require_empty_safe_output_directory(directory):
    descriptor = _open_safe_output_parent(directory)
    try:
        if os.listdir(descriptor):
            raise ObservationError("observation output directory must be empty")
    finally:
        os.close(descriptor)


def _require_observation_only_output_directory(directory, observation_path):
    if Path(os.path.abspath(str(observation_path.parent))) != Path(os.path.abspath(str(directory))):
        raise ObservationError("attestation output directory must contain the observation")
    descriptor = _open_safe_output_parent(directory)
    try:
        allowed = {OBSERVATION_BASENAME}
        if Path(directory, ENVIRONMENT_RECORD_BASENAME).is_file():
            allowed.add(ENVIRONMENT_RECORD_BASENAME)
        if set(os.listdir(descriptor)) != allowed:
            raise ObservationError("attestation output directory must contain only the fixed observation file")
    finally:
        os.close(descriptor)


def _exclusive_copy(source, destination):
    source_path = Path(source)
    destination_path = Path(destination)
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_descriptor = os.open(str(source_path), source_flags)
    except OSError as exc:
        raise ObservationError("source evidence cannot be opened safely: %s" % exc) from exc
    source_stat = os.fstat(source_descriptor)
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
        os.close(source_descriptor)
        raise ObservationError("source evidence must be a regular single-link file")
    parent_descriptor = _open_safe_output_parent(destination_path.parent)
    temporary_name = ".%s.%s" % (destination_path.name, secrets.token_hex(12))
    output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            os.stat(destination_path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ObservationError("refusing to overwrite existing evidence: %s" % destination_path)
        output_descriptor = os.open(temporary_name, output_flags, 0o600, dir_fd=parent_descriptor)
        with os.fdopen(output_descriptor, "wb") as output_handle:
            while True:
                chunk = os.read(source_descriptor, 1024 * 1024)
                if not chunk:
                    break
                output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if _file_stat_identity(source_stat) != _file_stat_identity(os.fstat(source_descriptor)):
            raise ObservationError("source evidence changed while it was copied")
        os.replace(
            temporary_name,
            destination_path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    except BaseException:
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except OSError:
            pass
        raise
    finally:
        os.close(source_descriptor)
        os.close(parent_descriptor)


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    observe = subparsers.add_parser("observe", help="observe the offline graphical installation")
    observe.add_argument("--customer-dir", required=True)
    observe.add_argument("--manifest", required=True)
    observe.add_argument("--challenge", required=True)
    observe.add_argument("--output-dir", required=True)
    observe.add_argument("--matrix")
    observe.add_argument("--category-id")
    observe.add_argument("--timeout-seconds", type=int, default=900)
    observe.add_argument("--poll-interval-ms", type=int, default=250)

    attest = subparsers.add_parser("attest", help="record the operator's desktop-double-click attestation")
    attest.add_argument("--observation", required=True)
    attest.add_argument("--graphical-evidence", required=True)
    attest.add_argument("--challenge", required=True)
    attest.add_argument("--operator-id", required=True)
    attest.add_argument("--confirmation", required=True)
    attest.add_argument("--output-dir", required=True)
    attest.add_argument("--matrix")
    attest.add_argument("--category-id")
    attest.add_argument("--environment-observation")

    verify = subparsers.add_parser("verify", help="verify an observation on the same target and boot")
    verify.add_argument("--observation", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--deb", required=True)
    verify.add_argument("--attestation", required=True)
    verify.add_argument("--graphical-evidence", required=True)
    verify.add_argument("--challenge", required=True)
    verify.add_argument("--matrix")
    verify.add_argument("--category-id")
    verify.add_argument("--environment-observation")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    runtime = SystemRuntime()
    if args.command == "observe":
        customer_dir = Path(args.customer_dir)
        manifest = Path(args.manifest)
        output_dir = Path(args.output_dir)
        if not customer_dir.is_absolute() or not manifest.is_absolute() or not output_dir.is_absolute():
            raise ObservationError("all paths must be absolute")
        if args.timeout_seconds < 60 or args.timeout_seconds > 3600:
            raise ObservationError("--timeout-seconds must be between 60 and 3600")
        if args.poll_interval_ms < 100 or args.poll_interval_ms > 1000:
            raise ObservationError("--poll-interval-ms must be between 100 and 1000")
        _require_empty_safe_output_directory(output_dir)
        canonical = bool(args.matrix or args.category_id)
        if canonical and (not args.matrix or not args.category_id):
            raise ObservationError("--matrix and --category-id must be supplied together for canonical mode")
        if canonical:
            observation, record = observe_environment_install(
                customer_dir=customer_dir,
                manifest_path=manifest,
                matrix_path=Path(args.matrix),
                category_id=args.category_id,
                challenge=args.challenge,
                user_state_paths=default_user_state_paths(),
                runtime=runtime,
                timeout_seconds=args.timeout_seconds,
                poll_interval_seconds=args.poll_interval_ms / 1000.0,
            )
            output = output_dir / OBSERVATION_BASENAME
            record_output = output_dir / ENVIRONMENT_RECORD_BASENAME
            _atomic_json(output, observation)
            _atomic_json(record_output, record)
            print(json.dumps({
                "status": "taiji-linux-environment-observed",
                "observation": str(output),
                "environment_observation": str(record_output),
                "category_id": args.category_id,
            }, sort_keys=True))
            return 0
        payload = observe_install(
            customer_dir=customer_dir,
            manifest_path=manifest,
            challenge=args.challenge,
            user_state_paths=default_user_state_paths(),
            runtime=runtime,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_ms / 1000.0,
        )
        output = output_dir / OBSERVATION_BASENAME
        _atomic_json(output, payload)
        print(json.dumps({"status": "taiji-single-deb-install-observed", "observation": str(output)}, sort_keys=True))
        return 0
    if args.command == "attest":
        if args.confirmation != "I-observed-desktop-double-click-and-system-installer":
            raise ObservationError("--confirmation must explicitly attest desktop double click and the system installer")
        observation_path = Path(args.observation)
        source_evidence = Path(args.graphical_evidence)
        output_dir = Path(args.output_dir)
        if not observation_path.is_absolute() or not source_evidence.is_absolute() or not output_dir.is_absolute():
            raise ObservationError("all paths must be absolute")
        _require_observation_only_output_directory(output_dir, observation_path)
        _validate_png_evidence(source_evidence)
        copied_evidence = output_dir / GRAPHICAL_EVIDENCE_BASENAME
        _exclusive_copy(source_evidence, copied_evidence)
        try:
            attestation = create_method_attestation(
                observation_path=observation_path,
                graphical_evidence_path=copied_evidence,
                challenge=args.challenge,
                operator_id=args.operator_id,
                runtime=runtime,
                matrix_path=Path(args.matrix) if args.matrix else None,
                category_id=args.category_id,
                environment_observation_path=(
                    Path(args.environment_observation)
                    if args.environment_observation
                    else None
                ),
            )
            output = output_dir / ATTESTATION_BASENAME
            _atomic_json(output, attestation)
        except BaseException:
            try:
                copied_evidence.unlink()
            except OSError:
                pass
            raise
        print(json.dumps({
            "status": "taiji-single-deb-install-method-attested",
            "attestation": str(output),
            "graphical_installer_evidence": str(copied_evidence),
        }, sort_keys=True))
        return 0
    canonical = bool(args.matrix or args.category_id or args.environment_observation)
    if canonical and (not args.matrix or not args.category_id or not args.environment_observation):
        raise ObservationError(
            "canonical verify requires --matrix, --category-id, and --environment-observation"
        )
    if canonical:
        observation = verify_environment_observation(
            Path(args.observation),
            environment_observation_path=Path(args.environment_observation),
            matrix_path=Path(args.matrix),
            category_id=args.category_id,
            manifest_path=Path(args.manifest),
            deb_path=Path(args.deb),
            challenge=args.challenge,
            runtime=runtime,
        )
    else:
        observation = verify_observation(
            Path(args.observation),
            manifest_path=Path(args.manifest),
            deb_path=Path(args.deb),
            challenge=args.challenge,
            runtime=runtime,
        )
    verify_method_attestation(
        attestation_path=Path(args.attestation),
        observation_path=Path(args.observation),
        graphical_evidence_path=Path(args.graphical_evidence),
        challenge=args.challenge,
        runtime=runtime,
        canonical=canonical,
    )
    print(json.dumps({
        "status": "taiji-single-deb-install-evidence-valid",
        "observation_sha256": _sha256_path(args.observation),
        "attestation_sha256": _sha256_path(args.attestation),
        "graphical_installer_evidence_sha256": _sha256_path(args.graphical_evidence),
        "deb_sha256": observation["deb_sha256"],
        "machine_fingerprint_sha256": observation["machine_fingerprint_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ObservationError, OSError) as exc:
        print("taiji-single-deb-install-observation-failed\t%s" % exc, file=sys.stderr)
        raise SystemExit(1)
