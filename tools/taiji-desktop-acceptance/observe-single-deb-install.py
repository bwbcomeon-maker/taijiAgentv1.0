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
ENVIRONMENT_RECORD_SCHEMA = "taiji-linux-environment-evidence/v1"
CERTIFICATION_MATRIX_SCHEMA = "taiji-linux-certification-matrix/v1"
CERTIFICATION_POLICY_ID = "taiji-linux-amd64-deb-v1"
OBSERVATION_BASENAME = "single-deb-install-observation.json"
ATTESTATION_BASENAME = "single-deb-install-method-attestation.json"
ENVIRONMENT_RECORD_BASENAME = "environment-evidence.json"
GRAPHICAL_EVIDENCE_BASENAME = "single-deb-graphical-installer.png"
PACKAGE_NAME = "taiji-agent"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,127}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")

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
    try:
        raw = Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ObservationError("cannot read %s JSON: %s" % (label, exc)) from exc
    if not isinstance(value, dict):
        raise ObservationError("%s must be a JSON object" % label)
    return value


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
    os_id,
    os_version,
    desktop_environment,
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
    if not isinstance(os_id, str) or not re.fullmatch(r"[a-z0-9._-]{2,32}", os_id):
        raise ObservationError("canonical environment os_id is invalid")
    if os_id not in category.get("os_ids", []):
        raise ObservationError("canonical environment os_id does not match the selected category")
    if not isinstance(os_version, str) or not os_version.strip() or len(os_version) > 128:
        raise ObservationError("canonical environment os_version is invalid")
    if not isinstance(desktop_environment, str) or not desktop_environment.strip() or len(desktop_environment) > 128:
        raise ObservationError("canonical environment desktop_environment is invalid")
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
        "os_id": os_id,
        "os_version": os_version.strip(),
        "desktop_environment": desktop_environment.strip(),
        "security_facts": {
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
        machine_path = next((Path(item) for item in ("/etc/machine-id", "/var/lib/dbus/machine-id") if Path(item).is_file()), None)
        boot_path = Path("/proc/sys/kernel/random/boot_id")
        if machine_path is None or not boot_path.is_file():
            raise ObservationError("target machine and boot identity are unavailable")
        try:
            machine_id = machine_path.read_text(encoding="ascii").strip()
            boot_id = boot_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise ObservationError("cannot read target machine identity: %s" % exc) from exc
        if not re.fullmatch(r"[0-9a-fA-F-]{16,64}", machine_id) or not re.fullmatch(r"[0-9a-fA-F-]{16,64}", boot_id):
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
    os_id,
    os_version,
    desktop_environment,
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
    observation = {
        "schema": "taiji.single-deb-install-observation/v2",
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
        os_id=os_id,
        os_version=os_version,
        desktop_environment=desktop_environment,
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
}

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
    observation = _load_json(observation_file, "install observation")
    machine_fingerprint, boot_fingerprint = _validate_observation_identity(
        observation,
        challenge,
        runtime,
        user_state_paths=user_state_paths,
    )
    if runtime.package_status() != "install ok installed":
        raise ObservationError("taiji-agent is not installed while method is being attested")
    evidence = _validate_png_evidence(graphical_evidence_path)
    return {
        "schema": ATTESTATION_SCHEMA,
        "generated_at_utc": _utc_text(runtime.utc_now()),
        "observation_basename": observation_file.name,
        "observation_sha256": _sha256_path(observation_file),
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
    environment_record_path,
    matrix_path,
    category_id,
    manifest_path,
    deb_path,
    challenge,
    runtime,
    max_age_seconds=86400,
    user_state_paths=None,
):
    """Verify the canonical no-target-baseline observation and record."""
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
    record = _load_json(environment_record_path, "environment evidence record")
    expected = _canonical_environment_record(
        category_id=category_id,
        matrix=matrix,
        manifest=manifest,
        observation=observation,
        os_id=record.get("os_id"),
        os_version=record.get("os_version"),
        desktop_environment=record.get("desktop_environment"),
    )
    _require_exact_keys(record, expected.keys(), "environment evidence record")
    if record != expected:
        raise ObservationError("environment evidence record does not match canonical observation facts")
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
    observe.add_argument("--os-id")
    observe.add_argument("--os-version")
    observe.add_argument("--desktop-environment")
    observe.add_argument("--timeout-seconds", type=int, default=900)
    observe.add_argument("--poll-interval-ms", type=int, default=250)

    attest = subparsers.add_parser("attest", help="record the operator's desktop-double-click attestation")
    attest.add_argument("--observation", required=True)
    attest.add_argument("--graphical-evidence", required=True)
    attest.add_argument("--challenge", required=True)
    attest.add_argument("--operator-id", required=True)
    attest.add_argument("--confirmation", required=True)
    attest.add_argument("--output-dir", required=True)

    verify = subparsers.add_parser("verify", help="verify an observation on the same target and boot")
    verify.add_argument("--observation", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--deb", required=True)
    verify.add_argument("--attestation", required=True)
    verify.add_argument("--graphical-evidence", required=True)
    verify.add_argument("--challenge", required=True)
    verify.add_argument("--matrix")
    verify.add_argument("--category-id")
    verify.add_argument("--environment-record")
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
            if not args.os_id or not args.os_version or not args.desktop_environment:
                raise ObservationError("canonical mode requires --os-id, --os-version, and --desktop-environment")
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
                os_id=args.os_id,
                os_version=args.os_version,
                desktop_environment=args.desktop_environment,
            )
            output = output_dir / OBSERVATION_BASENAME
            record_output = output_dir / ENVIRONMENT_RECORD_BASENAME
            _atomic_json(output, observation)
            _atomic_json(record_output, record)
            print(json.dumps({
                "status": "taiji-linux-environment-observed",
                "observation": str(output),
                "environment_record": str(record_output),
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
    canonical = bool(args.matrix or args.category_id or args.environment_record)
    if canonical and (not args.matrix or not args.category_id or not args.environment_record):
        raise ObservationError(
            "canonical verify requires --matrix, --category-id, and --environment-record"
        )
    if canonical:
        observation = verify_environment_observation(
            Path(args.observation),
            environment_record_path=Path(args.environment_record),
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
