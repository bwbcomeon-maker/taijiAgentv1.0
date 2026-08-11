#!/usr/bin/env python3
"""Assemble unsigned, manifest-bound Taiji target desktop acceptance evidence."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import pwd
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
INSTALL_OBSERVATION_BASENAME = "single-deb-install-observation.json"
INSTALL_METHOD_ATTESTATION_BASENAME = "single-deb-install-method-attestation.json"
GRAPHICAL_INSTALLER_EVIDENCE_BASENAME = "single-deb-graphical-installer.png"
ENVIRONMENT_OBSERVATION_BASENAME = "environment-observation.json"
ENVIRONMENT_EVIDENCE_BASENAME = "environment-evidence.json"
CANONICAL_OBSERVATION_SCHEMA = "taiji.single-deb-install-observation/v2"
CANONICAL_TARGET_EVIDENCE_SCHEMA = "taiji-linux-target-verification/v2"
MAX_JSON_BYTES = 1024 * 1024
MAX_SCREENSHOT_BYTES = 32 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MACHINE_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TARGET_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
OPERATOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
SYSTEM_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")
NON_LINUX_TEST_IDENTITY_ENV = "TAIJI_ASSEMBLER_NON_LINUX_TEST_IDENTITY"
MACHINE_IDENTITY_COMMITMENT_DOMAIN = "taiji-machine-identity-v1"
EXPECTED_CHECKS = {
    "visible_first_configuration_completion",
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "window_close_exit",
    "diagnostic_export",
    "three_restart_cycles",
    "second_instance_focus",
    "model_configuration_state_consistent",
    "no_new_electron_core",
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
    "desktop_auth_cookie",
    "model",
    "attachment_probe_sha256",
    "agent_pid",
    "web_pid",
    "screenshot_basename",
    "diagnostic_basename",
    "restart_rounds",
    "persistent_user_data",
    "core_observation",
    "model_config_observation",
    "checks",
    "js_error_count",
    "unexpected_http_failures",
    "electron_exit_code",
}
DRIVER_RESTART_ROUND_KEYS = {
    "round",
    "ready",
    "electron_pid",
    "agent_pid",
    "web_pid",
    "secondary_pid",
    "cdp_port",
    "webui_port",
    "second_instance_exit_code",
    "electron_exit_code",
    "restored_and_focused",
    "page_close_sent",
    "process_identities_gone",
    "ports_closed",
    "pidfiles_absent",
    "model_config_observed",
    "profile_continuity_observed",
}
DRIVER_PERSISTENT_USER_DATA_KEYS = {
    "mode",
    "restart_rounds",
    "user_data_override",
    "profile_reset",
    "environment_reused",
    "continuity_observed_rounds",
    "continuity_token",
}
DRIVER_CORE_OBSERVATION_KEYS = {
    "status",
    "mechanism",
    "baseline_entry_count",
    "baseline_cursor_set_token",
    "rounds",
}
DRIVER_CORE_ROUND_KEYS = {
    "round",
    "status",
    "added_entry_count",
    "cursor_set_token",
}
DRIVER_MODEL_CONFIG_OBSERVATION_KEYS = {
    "observed_rounds",
    "consistent",
    "public_projection_token",
}
DESKTOP_AUTH_COOKIE_KEYS = {
    "name",
    "present",
    "http_only",
    "same_site",
    "path",
    "value_format",
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
    "target_baseline_profile_id",
    "target_baseline_sha256",
    "installation_method",
    "installation_method_evidence",
    "installation_method_machine_observed",
    "installation_network",
    "installation_file_count",
    "additional_install_files",
    "dpkg_status_before",
    "dpkg_status_after",
    "first_configuration_cycle_completed",
    "machine_fingerprint_sha256",
    "install_observation_basename",
    "install_observation_sha256",
    "install_method_attestation_basename",
    "install_method_attestation_sha256",
    "graphical_installer_evidence_basename",
    "graphical_installer_evidence_sha256",
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
    "target_baseline_profile_id",
    "target_baseline_sha256",
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
    "installation_method",
    "installation_method_evidence",
    "installation_method_machine_observed",
    "installation_network",
    "installation_file_count",
    "additional_install_files",
    "dpkg_status_before",
    "dpkg_status_after",
    "first_configuration_cycle_completed",
    "visible_first_configuration_completion",
    "target_verified",
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "window_close_exit",
    "diagnostic_export",
    "three_restart_cycles",
    "second_instance_focus",
    "model_configuration_state_consistent",
    "no_new_electron_core",
    "session_log_basename",
    "session_log_sha256",
    "screenshot_basename",
    "screenshot_sha256",
    "diagnostic_basename",
    "diagnostic_sha256",
    "driver_result_basename",
    "driver_result_sha256",
    "install_observation_basename",
    "install_observation_sha256",
    "install_method_attestation_basename",
    "install_method_attestation_sha256",
    "graphical_installer_evidence_basename",
    "graphical_installer_evidence_sha256",
}

CANONICAL_TARGET_EVIDENCE_KEYS = {
    "schema",
    "evidence_type",
    "generated_at_utc",
    "acceptance_session_id",
    "challenge_nonce",
    "machine_identity_commitment_sha256",
    "machine_fingerprint_sha256",
    "release_artifacts_sha256",
    "category_id",
    "category_kind",
    "compatibility",
    "source_commit",
    "version",
    "architecture",
    "deb_basename",
    "deb_sha256",
    "compatibility_policy_id",
    "compatibility_policy_sha256",
    "os_id",
    "os_version",
    "desktop_environment",
    "installation_method",
    "installation_method_evidence",
    "installation_method_machine_observed",
    "checks",
    "environment_observation_basename",
    "environment_observation_sha256",
    "install_observation_basename",
    "install_observation_sha256",
    "install_method_attestation_basename",
    "install_method_attestation_sha256",
    "graphical_installer_evidence_basename",
    "graphical_installer_evidence_sha256",
    "driver_result_basename",
    "driver_result_sha256",
    "screenshot_basename",
    "screenshot_sha256",
    "diagnostic_basename",
    "diagnostic_sha256",
}

INSTALL_OBSERVATION_KEYS = {
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
INSTALL_METHOD_ATTESTATION_KEYS = {
    "schema", "generated_at_utc", "observation_basename", "observation_sha256",
    "challenge_nonce", "machine_fingerprint_sha256", "boot_fingerprint_sha256",
    "deb_sha256", "installation_method_attested", "installation_method_machine_observed",
    "attestation_scope", "operator_id", "confirmation",
    "graphical_installer_evidence_basename", "graphical_installer_evidence_sha256",
}


class AssemblyError(ValueError):
    """Raised when an input cannot produce trustworthy target evidence."""


def _current_target_identities() -> tuple[str, str]:
    """Read canonical machine and boot identities from the current target.

    The production path is deliberately Linux-only and reads the same kernel
    identities as the pre-install observer.  A separately named environment
    variable exists only so the subprocess contract tests can run on the macOS
    development host; it is ignored on Linux.
    """
    if sys.platform.startswith("linux"):
        machine_path = next(
            (
                Path(candidate)
                for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id")
                if Path(candidate).is_file()
            ),
            None,
        )
        boot_path = Path("/proc/sys/kernel/random/boot_id")
        if machine_path is None or not boot_path.is_file():
            raise AssemblyError("current Linux machine or boot identity is unavailable")
        try:
            machine_id = machine_path.read_text(encoding="ascii").strip()
            boot_id = boot_path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise AssemblyError("current Linux machine or boot identity is unreadable") from exc
        if not MACHINE_ID_RE.fullmatch(machine_id) or not SYSTEM_ID_RE.fullmatch(boot_id):
            raise AssemblyError("current Linux machine or boot identity is invalid")
        machine_identity = machine_id.lower()
        boot_identity = boot_id.lower()
    else:
        test_identity = os.environ.get(NON_LINUX_TEST_IDENTITY_ENV, "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,128}", test_identity):
            raise AssemblyError("target evidence assembly is Linux-only outside contract tests")
        machine_identity = f"non-linux-contract-test-machine:{test_identity}"
        boot_identity = f"non-linux-contract-test-boot:{test_identity}"

    return machine_identity, boot_identity


def _machine_identity_commitment(machine_identity: str) -> str:
    return hashlib.sha256(
        (MACHINE_IDENTITY_COMMITMENT_DOMAIN + "\0" + machine_identity).encode("utf-8")
    ).hexdigest()


def _machine_fingerprint_from_commitment(challenge: str, commitment: str) -> str:
    if not CHALLENGE_RE.fullmatch(challenge or ""):
        raise AssemblyError("challenge must be 64-128 lowercase hexadecimal characters")
    require_sha256(commitment, "machine identity commitment")
    return hashlib.sha256((challenge + "\0" + commitment).encode("utf-8")).hexdigest()


def current_target_identity_binding(challenge: str) -> tuple[str, str, str]:
    """Return stable commitment plus challenge-bound machine and boot fingerprints."""
    machine_identity, boot_identity = _current_target_identities()
    commitment = _machine_identity_commitment(machine_identity)
    machine_fingerprint = _machine_fingerprint_from_commitment(challenge, commitment)
    boot_fingerprint = hashlib.sha256(
        (challenge + "\0" + boot_identity).encode("utf-8")
    ).hexdigest()
    return commitment, machine_fingerprint, boot_fingerprint


def current_target_fingerprints(challenge: str) -> tuple[str, str]:
    """Derive legacy challenge-bound machine and boot fingerprints."""
    if not CHALLENGE_RE.fullmatch(challenge or ""):
        raise AssemblyError("challenge must be 64-128 lowercase hexadecimal characters")
    machine_identity, boot_identity = _current_target_identities()

    def fingerprint(identity: str) -> str:
        return hashlib.sha256((challenge + "\0" + identity).encode("utf-8")).hexdigest()

    return fingerprint(machine_identity), fingerprint(boot_identity)


def current_user_context_fingerprints(challenge: str) -> tuple[int, str, str]:
    """Bind current uid, canonical account home, and the fixed Taiji XDG paths."""
    if not CHALLENGE_RE.fullmatch(challenge or ""):
        raise AssemblyError("challenge must be 64-128 lowercase hexadecimal characters")
    uid = os.getuid()
    try:
        canonical_home = os.path.realpath(pwd.getpwuid(uid).pw_dir)
    except (KeyError, OSError) as exc:
        raise AssemblyError("cannot resolve the canonical account home") from exc
    if not os.path.isabs(canonical_home):
        raise AssemblyError("canonical account home must be absolute")
    environment_home = Path(os.path.expanduser("~")).resolve()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or environment_home / ".config")
    data = Path(os.environ.get("XDG_DATA_HOME") or environment_home / ".local" / "share")
    state = Path(os.environ.get("XDG_STATE_HOME") or environment_home / ".local" / "state")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or environment_home / ".cache")
    paths = [
        config / "taiji-agent",
        config / "taiji-agent-desktop",
        config / "太极 Agent",
        data / "taiji-agent",
        data / "taiji-agent-desktop",
        state / "taiji-agent",
        cache / "taiji-agent",
        cache / "taiji-agent-desktop",
    ]
    normalized_paths = []
    for item in paths:
        raw = os.fspath(item)
        if not os.path.isabs(raw):
            raise AssemblyError("current user state paths must be absolute")
        normalized_paths.append(os.path.normpath(os.path.abspath(raw)))
    if len(normalized_paths) != len(set(normalized_paths)):
        raise AssemblyError("current user state paths must be unique")

    def fingerprint(identity: str) -> str:
        return hashlib.sha256((challenge + "\0" + identity).encode("utf-8")).hexdigest()

    return (
        uid,
        fingerprint("canonical-account-home\0%d\0%s" % (uid, canonical_home)),
        fingerprint("taiji-user-state-paths\0%d\0%s" % (uid, "\0".join(normalized_paths))),
    )


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


CERTIFICATION_MATRIX_SCHEMA = "taiji-linux-certification-matrix/v2"
ENVIRONMENT_OBSERVATION_SCHEMA = "taiji-linux-environment-observation/v1"
ENVIRONMENT_EVIDENCE_SCHEMA = "taiji-linux-environment-evidence/v2"
CERTIFICATION_POLICY_ID = "taiji-linux-amd64-deb-v1"
CERTIFICATION_POSITIVE_IDS = {
    "kylin-min-ukui",
    "kylin-current-standard",
    "kylin-hardened",
    "uos-min-dde",
    "uos-current-or-hardened",
    "openkylin-current",
}
CERTIFICATION_NEGATIVE_IDS = {
    "arm-blocked",
    "rpm-only-blocked",
    "glibc-below-min-blocked",
    "missing-core-capability-blocked",
    "no-admin-blocked",
    "no-graphical-desktop-blocked",
}
CERTIFICATION_BUSINESS_CHECKS = {
    "visible_first_configuration_completion",
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "diagnostic_export",
    "model_configuration_state_consistent",
}
CERTIFICATION_LIFECYCLE_CHECKS = {
    "install",
    "window_close_exit",
    "three_restart_cycles",
    "second_instance_focus",
    "no_new_electron_core",
}
CERTIFICATION_MATRIX_KEYS = {
    "schema",
    "architecture",
    "compatibility_policy_id",
    "coverage_rules",
    "minimum_application_only_categories",
    "positive_categories",
    "negative_boundaries",
}
CERTIFICATION_POSITIVE_CATEGORY_KEYS = {
    "id",
    "kind",
    "label",
    "expected_compatibility",
    "os_ids",
    "desktop_environments",
    "platform_profile",
    "required_business_checks",
    "required_lifecycle_checks",
}
CERTIFICATION_NEGATIVE_CATEGORY_KEYS = {
    "id",
    "kind",
    "boundary",
    "expected_compatibility",
    "stable_error_code",
    "block_before_business_data_mutation",
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
ENVIRONMENT_OBSERVATION_KEYS = {
    "schema",
    "category_id",
    "category_kind",
    "compatibility",
    "source_commit",
    "version",
    "architecture",
    "deb_basename",
    "deb_sha256",
    "compatibility_policy_id",
    "compatibility_policy_sha256",
    "machine_identity_commitment_sha256",
    "os_id",
    "os_version",
    "desktop_environment",
    "security_facts",
    "checks",
    "attachments",
}
ENVIRONMENT_RECORD_KEYS = (ENVIRONMENT_OBSERVATION_KEYS - {
    "machine_identity_commitment_sha256",
}) | {
    "challenge_nonce",
    "acceptance_session_id",
    "machine_fingerprint_sha256",
}
POSITIVE_ENVIRONMENT_RECORD_KEYS = ENVIRONMENT_RECORD_KEYS | {
    "machine_identity_commitment_sha256",
}
POSITIVE_ENVIRONMENT_ATTACHMENT_BASENAMES = {
    EVIDENCE_BASENAME,
    ENVIRONMENT_OBSERVATION_BASENAME,
    INSTALL_OBSERVATION_BASENAME,
    INSTALL_METHOD_ATTESTATION_BASENAME,
    GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
    DRIVER_RESULT_BASENAME,
    SCREENSHOT_BASENAME,
    DIAGNOSTIC_BASENAME,
}
NEGATIVE_ENVIRONMENT_ATTACHMENT_BASENAMES = {
    "preflight-result.json",
    "business-data-inventory.json",
}
NEGATIVE_SECURITY_FACT_KEYS = {
    "business_data_mutation",
    "business_data_before_sha256",
    "business_data_after_sha256",
    "business_data_scope_id",
    "business_data_inventory_sha256",
    "boundary",
    "observed_value",
    "stable_error_code",
    "execution_environment",
    "preflight_result_sha256",
}
POSITIVE_SECURITY_FACT_KEYS = {
    "administrator_available",
    "business_data_mutation",
    "graphical_desktop",
    "network_observation",
    "package_manager",
    "security_profile",
    "kysec_detected",
    "kysec_enabled",
    "kysec_exec_control",
    "os_release_sha256",
    "os_version_sha256",
}
BUSINESS_DATA_SCOPE_ID = "taiji-user-and-install-state-v1"
PROTECTED_BUSINESS_PATHS = (
    "home/customer/.config/taiji-agent",
    "home/customer/.config/taiji-agent-desktop",
    "home/customer/.local/share/taiji-agent",
    "home/customer/.local/share/taiji-agent-desktop",
    "home/customer/.local/state/taiji-agent",
    "home/customer/.local/state/taiji-agent-desktop",
    "home/customer/.cache/taiji-agent",
    "home/customer/.cache/taiji-agent-desktop",
    "opt/taiji-agent",
)
NEGATIVE_PREFLIGHT_KEYS = {
    "schema",
    "status",
    "policy_id",
    "compatibility_policy_sha256",
    "error_code",
    "reason_zh",
    "failed_capabilities",
}


def _require_string_set(value: Any, expected: set[str], label: str) -> None:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise AssemblyError(f"{label} must be a list of strings")
    if set(value) != expected or len(value) != len(expected):
        raise AssemblyError(f"{label} does not match the required certification checks")


def validate_certification_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    """Validate the release-owned representative environment matrix.

    The matrix is policy input, not an acceptance result.  Its category IDs
    are intentionally closed so a release cannot silently reduce or rename
    the representative coverage set.
    """
    if type(matrix) is not dict:
        raise AssemblyError("certification matrix must be an object")
    require_exact_keys(matrix, CERTIFICATION_MATRIX_KEYS, "certification matrix")
    if matrix["schema"] != CERTIFICATION_MATRIX_SCHEMA:
        raise AssemblyError("certification matrix has the wrong schema")
    if matrix["architecture"] != "amd64":
        raise AssemblyError("certification matrix architecture must be amd64")
    if matrix["compatibility_policy_id"] != CERTIFICATION_POLICY_ID:
        raise AssemblyError("certification matrix compatibility policy is invalid")
    categories = matrix["positive_categories"]
    boundaries = matrix["negative_boundaries"]
    if type(categories) is not list or type(boundaries) is not list:
        raise AssemblyError("certification matrix categories must be lists")
    positive_ids = [item.get("id") for item in categories if type(item) is dict]
    negative_ids = [item.get("id") for item in boundaries if type(item) is dict]
    if len(positive_ids) != len(set(positive_ids)) or len(negative_ids) != len(set(negative_ids)):
        raise AssemblyError("certification matrix contains duplicate category IDs")
    if set(positive_ids) != CERTIFICATION_POSITIVE_IDS or len(categories) != 6:
        raise AssemblyError("certification matrix positive categories are incomplete")
    if set(negative_ids) != CERTIFICATION_NEGATIVE_IDS or len(boundaries) != 6:
        raise AssemblyError("certification matrix negative boundaries are incomplete")
    if set(positive_ids) & set(negative_ids):
        raise AssemblyError("certification matrix contains duplicate category IDs")
    for category in categories:
        if type(category) is not dict:
            raise AssemblyError("certification matrix positive category must be an object")
        require_exact_keys(
            category,
            CERTIFICATION_POSITIVE_CATEGORY_KEYS,
            f"certification matrix positive category {category.get('id')}",
        )
        if category.get("kind") != "positive":
            raise AssemblyError(f"certification matrix category {category.get('id')} kind is invalid")
        if category.get("expected_compatibility") != "COMPATIBLE":
            raise AssemblyError(f"certification matrix category {category.get('id')} compatibility is invalid")
        _require_string_set(
            category.get("required_business_checks"),
            CERTIFICATION_BUSINESS_CHECKS,
            f"certification matrix category {category.get('id')} business checks",
        )
        _require_string_set(
            category.get("required_lifecycle_checks"),
            CERTIFICATION_LIFECYCLE_CHECKS,
            f"certification matrix category {category.get('id')} lifecycle checks",
        )
        if type(category.get("os_ids")) is not list or not category["os_ids"]:
            raise AssemblyError(f"certification matrix category {category.get('id')} OS IDs are invalid")
        if type(category.get("desktop_environments")) is not list or not category["desktop_environments"]:
            raise AssemblyError(
                f"certification matrix category {category.get('id')} desktop environments are invalid"
            )
        profile = category.get("platform_profile")
        if type(profile) is not dict:
            raise AssemblyError(
                f"certification matrix category {category.get('id')} platform profile is invalid"
            )
        require_exact_keys(
            profile,
            PLATFORM_PROFILE_KEYS,
            f"certification matrix category {category.get('id')} platform profile",
        )
        if profile != CANONICAL_PLATFORM_PROFILES[category["id"]]:
            raise AssemblyError(
                f"certification matrix category {category.get('id')} platform profile is not canonical"
            )
        if category["os_ids"] != [profile["os_id"]]:
            raise AssemblyError(
                f"certification matrix category {category.get('id')} OS IDs do not match its platform profile"
            )
        if category["desktop_environments"] != profile["desktop_environments"]:
            raise AssemblyError(
                f"certification matrix category {category.get('id')} desktop environments do not match its platform profile"
            )
    for boundary in boundaries:
        if type(boundary) is not dict:
            raise AssemblyError("certification matrix negative boundary must be an object")
        require_exact_keys(
            boundary,
            CERTIFICATION_NEGATIVE_CATEGORY_KEYS,
            f"certification matrix negative boundary {boundary.get('id')}",
        )
        if boundary.get("kind") != "negative":
            raise AssemblyError(f"certification matrix boundary {boundary.get('id')} kind is invalid")
        if boundary.get("expected_compatibility") != "BLOCKED":
            raise AssemblyError(f"certification matrix boundary {boundary.get('id')} compatibility is invalid")
        if boundary.get("block_before_business_data_mutation") is not True:
            raise AssemblyError(
                f"certification matrix boundary {boundary.get('id')} must block before business data mutation"
            )
        if not isinstance(boundary.get("stable_error_code"), str) or not boundary["stable_error_code"]:
            raise AssemblyError(f"certification matrix boundary {boundary.get('id')} error code is invalid")
    coverage = matrix["coverage_rules"]
    if type(coverage) is not dict or set(coverage) != {
        "runtime_policy_or_lifecycle_change",
        "application_only_change",
    }:
        raise AssemblyError("certification matrix coverage rules are invalid")
    if coverage["runtime_policy_or_lifecycle_change"] != "all-positive-and-negative":
        raise AssemblyError("certification matrix runtime coverage rule is invalid")
    if coverage["application_only_change"] != "three-family-core-path":
        raise AssemblyError("certification matrix application coverage rule is invalid")
    minimum = matrix["minimum_application_only_categories"]
    if type(minimum) is not list or set(minimum) != {
        "kylin-current-standard",
        "uos-current-or-hardened",
        "openkylin-current",
    } or len(minimum) != 3:
        raise AssemblyError("certification matrix application-only minimum is invalid")
    return matrix


def _validate_environment_identity(
    record: dict[str, Any], matrix: dict[str, Any], *, label: str
) -> dict[str, Any]:
    validate_certification_matrix(matrix)
    if type(record) is not dict:
        raise AssemblyError(f"{label} must be an object")
    if "CERTIFIED" in json.dumps(record, ensure_ascii=False, sort_keys=True):
        raise AssemblyError(f"{label} must not claim CERTIFIED")
    category_id = record["category_id"]
    if type(category_id) is not str:
        raise AssemblyError(f"{label} category_id is invalid")
    all_categories = {
        item["id"]: item
        for item in matrix["positive_categories"] + matrix["negative_boundaries"]
    }
    category = all_categories.get(category_id)
    if category is None:
        raise AssemblyError(f"{label} category_id is not in the certification matrix")
    expected_kind = category["kind"]
    if record["category_kind"] != expected_kind:
        raise AssemblyError(f"{label} category_kind does not match the matrix")
    if record["compatibility"] != category["expected_compatibility"]:
        raise AssemblyError(f"{label} compatibility does not match the matrix")
    source_commit = record["source_commit"]
    if type(source_commit) is not str or not FULL_COMMIT_RE.fullmatch(source_commit):
        raise AssemblyError(f"{label} source_commit is invalid")
    version = record["version"]
    if type(version) is not str or not VERSION_RE.fullmatch(version):
        raise AssemblyError(f"{label} version is invalid")
    if record["architecture"] != matrix["architecture"]:
        raise AssemblyError(f"{label} architecture does not match the matrix")
    expected_deb = f"taiji-agent_{version}_amd64.deb"
    if record["deb_basename"] != expected_deb:
        raise AssemblyError(f"{label} DEB basename is invalid")
    require_sha256(record["deb_sha256"], f"{label} DEB hash")
    if record["compatibility_policy_id"] != matrix["compatibility_policy_id"]:
        raise AssemblyError(f"{label} compatibility policy does not match the matrix")
    require_sha256(
        record["compatibility_policy_sha256"],
        f"{label} compatibility policy hash",
    )
    for key in ("os_id", "os_version", "desktop_environment"):
        require_visible_text(record[key], f"{label} {key}")
    if type(record["security_facts"]) is not dict:
        raise AssemblyError(f"{label} security_facts must be an object")
    if type(record["checks"]) is not dict:
        raise AssemblyError(f"{label} checks must be an object")
    if type(record["attachments"]) is not list:
        raise AssemblyError(f"{label} attachments must be a list")
    if category["kind"] == "positive":
        profile = category["platform_profile"]
        facts = record["security_facts"]
        require_sha256(
            record.get("machine_identity_commitment_sha256"),
            f"{label} machine identity commitment",
        )
        if set(facts) != POSITIVE_SECURITY_FACT_KEYS:
            raise AssemblyError(f"{label} positive security facts have an invalid field set")
        if (
            facts["administrator_available"] is not True
            or facts["business_data_mutation"] is not False
            or facts["graphical_desktop"] is not True
            or facts["network_observation"]
            != "continuous-process-sampling-no-non-loopback-up"
            or facts["package_manager"] != "dpkg"
            or type(facts["kysec_detected"]) is not bool
            or type(facts["kysec_enabled"]) is not bool
            or facts["kysec_exec_control"] not in {"off", "not-present"}
        ):
            raise AssemblyError(f"{label} positive security facts are unsafe or inconsistent")
        if not facts["kysec_detected"] and (
            facts["kysec_enabled"] or facts["kysec_exec_control"] != "not-present"
        ):
            raise AssemblyError(f"{label} positive security facts contain an inconsistent absent Kysec state")
        if facts["kysec_detected"] and facts["kysec_exec_control"] != "off":
            raise AssemblyError(f"{label} detected Kysec does not prove exec control off")
        require_sha256(facts["os_release_sha256"], f"{label} os-release hash")
        if facts["os_version_sha256"] != "not-present":
            require_sha256(facts["os_version_sha256"], f"{label} os-version hash")
        if record["os_id"] != profile["os_id"]:
            raise AssemblyError(f"{label} OS does not match the category platform profile")
        version_parts = record["os_version"].split("/", 1)
        if len(version_parts) != 2 or version_parts[0] != profile["version_id"]:
            raise AssemblyError(f"{label} OS version does not match the category platform profile")
        release_id = version_parts[1]
        try:
            release_matches = re.fullmatch(profile["release_id_pattern"], release_id) is not None
        except re.error as exc:
            raise AssemblyError(f"{label} category release pattern is invalid") from exc
        if not release_matches:
            raise AssemblyError(f"{label} OS release does not match the category platform profile")
        if record["desktop_environment"] not in profile["desktop_environments"]:
            raise AssemblyError(f"{label} desktop does not match the category platform profile")
        if facts["security_profile"] != profile["security_profile"]:
            raise AssemblyError(f"{label} security profile does not match the category platform profile")
        if profile["security_profile"] == "kysec-enabled-exec-control-off" and any(
            facts.get(key) != expected
            for key, expected in {
                "kysec_detected": True,
                "kysec_enabled": True,
                "kysec_exec_control": "off",
            }.items()
        ):
            raise AssemblyError(f"{label} hardened security facts do not prove Kysec enabled with exec control off")
    return category


def _validate_environment_attachments(attachments: list[Any]) -> set[str]:
    basenames: set[str] = set()
    for attachment in attachments:
        if type(attachment) is not dict:
            raise AssemblyError("environment evidence attachment must be an object")
        require_exact_keys(attachment, {"basename", "sha256"}, "environment evidence attachment")
        basename = attachment["basename"]
        if (
            type(basename) is not str
            or not basename
            or basename in {".", ".."}
            or Path(basename).name != basename
            or "/" in basename
            or "\\" in basename
        ):
            raise AssemblyError("environment evidence attachment basename is invalid")
        if basename in basenames:
            raise AssemblyError("environment evidence contains duplicate attachment basenames")
        require_sha256(attachment["sha256"], "environment evidence attachment hash")
        basenames.add(basename)
    return basenames


def validate_environment_observation(
    observation: dict[str, Any], matrix: dict[str, Any]
) -> dict[str, Any]:
    """Validate the install-time seed without treating it as final evidence."""
    require_exact_keys(observation, ENVIRONMENT_OBSERVATION_KEYS, "environment observation")
    if observation["schema"] != ENVIRONMENT_OBSERVATION_SCHEMA:
        raise AssemblyError("environment observation has the wrong schema")
    category = _validate_environment_identity(observation, matrix, label="environment observation")
    if category["kind"] != "positive":
        raise AssemblyError("desktop installation observation requires a positive category")
    if observation["checks"] != {"preflight": "PASS", "install": "PASS"}:
        raise AssemblyError("environment observation must contain only successful preflight and install checks")
    if observation["attachments"] != []:
        raise AssemblyError("environment observation must not claim final evidence attachments")
    return observation


def validate_environment_record(
    record: dict[str, Any], matrix: dict[str, Any]
) -> dict[str, Any]:
    """Validate one final machine-fact record without allowing certification claims."""
    if type(record) is dict and "CERTIFIED" in json.dumps(record, ensure_ascii=False, sort_keys=True):
        raise AssemblyError("environment evidence record must not claim CERTIFIED")
    expected_keys = (
        POSITIVE_ENVIRONMENT_RECORD_KEYS
        if type(record) is dict and record.get("category_kind") == "positive"
        else ENVIRONMENT_RECORD_KEYS
    )
    require_exact_keys(record, expected_keys, "environment evidence record")
    if record["schema"] != ENVIRONMENT_EVIDENCE_SCHEMA:
        raise AssemblyError("environment evidence record has the wrong schema")
    category = _validate_environment_identity(record, matrix, label="environment evidence")
    if not CHALLENGE_RE.fullmatch(record["challenge_nonce"] or ""):
        raise AssemblyError("environment evidence challenge_nonce is invalid")
    if not SESSION_RE.fullmatch(record["acceptance_session_id"] or ""):
        raise AssemblyError("environment evidence acceptance_session_id is invalid")
    machine_fingerprint = require_sha256(
        record["machine_fingerprint_sha256"],
        "environment evidence machine fingerprint",
    )
    if category["kind"] == "positive":
        commitment = require_sha256(
            record["machine_identity_commitment_sha256"],
            "environment evidence machine identity commitment",
        )
        expected_fingerprint = _machine_fingerprint_from_commitment(
            record["challenge_nonce"], commitment
        )
        if machine_fingerprint != expected_fingerprint:
            raise AssemblyError(
                "environment evidence machine fingerprint does not match its identity commitment"
            )
    attachment_basenames = _validate_environment_attachments(record["attachments"])
    if category["kind"] == "positive":
        required_checks = {
            "preflight",
            *category["required_business_checks"],
            *category["required_lifecycle_checks"],
        }
        if set(record["checks"]) != required_checks or any(
            record["checks"].get(key) != "PASS" for key in required_checks
        ):
            raise AssemblyError("positive environment evidence requires the exact successful target checks")
        if attachment_basenames != POSITIVE_ENVIRONMENT_ATTACHMENT_BASENAMES:
            raise AssemblyError("positive environment evidence attachments are incomplete or contain extras")
    else:
        if record["checks"] != {"preflight": "BLOCKED"}:
            raise AssemblyError("negative environment evidence must block during preflight")
        facts = record["security_facts"]
        if set(facts) != NEGATIVE_SECURITY_FACT_KEYS:
            raise AssemblyError("negative environment evidence has an invalid security fact set")
        if facts.get("business_data_mutation") is not False:
            raise AssemblyError("negative environment evidence must prove no business data mutation")
        before_hash = require_sha256(
            facts.get("business_data_before_sha256"),
            "negative environment evidence business data before hash",
        )
        after_hash = require_sha256(
            facts.get("business_data_after_sha256"),
            "negative environment evidence business data after hash",
        )
        if before_hash != after_hash:
            raise AssemblyError("negative environment evidence business data changed during preflight")
        if facts.get("business_data_scope_id") != BUSINESS_DATA_SCOPE_ID:
            raise AssemblyError("negative environment evidence business data scope is invalid")
        require_sha256(
            facts.get("business_data_inventory_sha256"),
            "negative environment evidence business data inventory hash",
        )
        if facts.get("boundary") != category["boundary"]:
            raise AssemblyError("negative environment evidence boundary does not match the matrix")
        if facts.get("stable_error_code") != category["stable_error_code"]:
            raise AssemblyError("negative environment evidence stable error code does not match the matrix")
        if facts.get("execution_environment") != "controlled-root-fixture-v1":
            raise AssemblyError("negative environment evidence execution environment is invalid")
        require_visible_text(facts.get("observed_value"), "negative environment evidence observed value")
        preflight_hash = require_sha256(
            facts.get("preflight_result_sha256"),
            "negative environment evidence preflight result hash",
        )
        if attachment_basenames != NEGATIVE_ENVIRONMENT_ATTACHMENT_BASENAMES:
            raise AssemblyError("negative environment evidence must contain exactly the raw preflight result")
        attachment_hash = next(
            item["sha256"]
            for item in record["attachments"]
            if item["basename"] == "preflight-result.json"
        )
        if attachment_hash != preflight_hash:
            raise AssemblyError("negative environment evidence preflight attachment hash is inconsistent")
    return record


def validate_negative_preflight_attachment(
    record: dict[str, Any],
    matrix: dict[str, Any],
    payload: bytes,
) -> dict[str, Any]:
    """Validate the raw preinst result bound by a negative record."""
    validate_environment_record(record, matrix)
    category = next(
        item
        for item in matrix["negative_boundaries"]
        if item["id"] == record["category_id"]
    )
    if category["kind"] != "negative":
        raise AssemblyError("raw negative preflight attachment requires a negative category")
    preflight = parse_json_bytes(payload, "negative preflight result")
    require_exact_keys(preflight, NEGATIVE_PREFLIGHT_KEYS, "negative preflight result")
    if preflight["schema"] != "taiji-install-preflight/v1":
        raise AssemblyError("negative preflight result schema is invalid")
    if preflight["status"] != "BLOCKED":
        raise AssemblyError("negative preflight result must be BLOCKED")
    if preflight["policy_id"] != record["compatibility_policy_id"]:
        raise AssemblyError("negative preflight result policy ID does not match the candidate")
    if preflight["compatibility_policy_sha256"] != record["compatibility_policy_sha256"]:
        raise AssemblyError("negative preflight result policy hash does not match the candidate")
    expected_code = category["stable_error_code"]
    if preflight["error_code"] != expected_code:
        raise AssemblyError("negative preflight result error code does not match the matrix")
    if preflight["failed_capabilities"] != [expected_code]:
        raise AssemblyError("negative preflight result must isolate exactly one matrix boundary")
    require_visible_text(preflight["reason_zh"], "negative preflight result reason", maximum=256)
    actual_hash = hashlib.sha256(payload).hexdigest()
    if record["security_facts"]["preflight_result_sha256"] != actual_hash:
        raise AssemblyError("negative preflight result hash does not match the environment record")
    return preflight


def validate_negative_business_data_attachment(
    record: dict[str, Any],
    matrix: dict[str, Any],
    payload: bytes,
) -> dict[str, Any]:
    """Validate the closed before/after inventory for all protected Taiji paths."""
    validate_environment_record(record, matrix)
    inventory = parse_json_bytes(payload, "negative business data inventory")
    require_exact_keys(
        inventory,
        {"schema", "scope_id", "protected_paths", "before", "after", "unchanged"},
        "negative business data inventory",
    )
    if inventory["schema"] != "taiji-business-data-inventory/v1":
        raise AssemblyError("negative business data inventory schema is invalid")
    if inventory["scope_id"] != BUSINESS_DATA_SCOPE_ID:
        raise AssemblyError("negative business data inventory scope is invalid")
    if inventory["protected_paths"] != list(PROTECTED_BUSINESS_PATHS):
        raise AssemblyError("negative business data inventory protected path set is invalid")
    if inventory["unchanged"] is not True:
        raise AssemblyError("negative business data inventory must prove unchanged state")

    def validate_entries(value: Any, label: str) -> list[dict[str, str]]:
        if type(value) is not list or len(value) != len(PROTECTED_BUSINESS_PATHS):
            raise AssemblyError(f"{label} entry set is incomplete")
        result: list[dict[str, str]] = []
        for index, expected_path in enumerate(PROTECTED_BUSINESS_PATHS):
            item = value[index]
            if type(item) is not dict:
                raise AssemblyError(f"{label} entry must be an object")
            require_exact_keys(item, {"path", "sha256"}, f"{label} entry")
            if item["path"] != expected_path:
                raise AssemblyError(f"{label} protected path order or value is invalid")
            require_sha256(item["sha256"], f"{label} protected path hash")
            result.append(item)
        return result

    before = validate_entries(inventory["before"], "negative business data before inventory")
    after = validate_entries(inventory["after"], "negative business data after inventory")
    if before != after:
        raise AssemblyError("negative business data inventory changed during preflight")
    digest = hashlib.sha256(payload).hexdigest()
    facts = record["security_facts"]
    if facts["business_data_inventory_sha256"] != digest:
        raise AssemblyError("negative business data inventory hash does not match the environment record")
    aggregate = hashlib.sha256(
        json_bytes({"entries": before})
    ).hexdigest()
    if facts["business_data_before_sha256"] != aggregate or facts["business_data_after_sha256"] != aggregate:
        raise AssemblyError("negative business data aggregate hash does not match the inventory")
    attachment_hash = next(
        item["sha256"]
        for item in record["attachments"]
        if item["basename"] == "business-data-inventory.json"
    )
    if attachment_hash != digest:
        raise AssemblyError("negative business data attachment hash is inconsistent")
    return inventory


def build_positive_environment_evidence(
    observation: dict[str, Any],
    *,
    matrix: dict[str, Any],
    driver_checks: dict[str, Any],
    challenge: str,
    acceptance_session_id: str,
    attachment_hashes: dict[str, str],
) -> dict[str, Any]:
    validate_certification_matrix(matrix)
    validate_environment_observation(observation, matrix)
    if set(driver_checks) != EXPECTED_CHECKS or any(driver_checks.get(key) is not True for key in EXPECTED_CHECKS):
        raise AssemblyError("desktop driver checks are incomplete or failed")
    if set(attachment_hashes) != POSITIVE_ENVIRONMENT_ATTACHMENT_BASENAMES:
        raise AssemblyError("positive environment evidence attachment set is incomplete or contains extras")
    for basename, digest in attachment_hashes.items():
        if Path(basename).name != basename:
            raise AssemblyError("positive environment evidence attachment basename is invalid")
        require_sha256(digest, "positive environment evidence attachment hash")
    commitment = require_sha256(
        observation["machine_identity_commitment_sha256"],
        "positive environment machine identity commitment",
    )
    record = {
        **observation,
        "schema": ENVIRONMENT_EVIDENCE_SCHEMA,
        "challenge_nonce": challenge,
        "acceptance_session_id": acceptance_session_id,
        "machine_fingerprint_sha256": _machine_fingerprint_from_commitment(
            challenge, commitment
        ),
        "checks": {
            "preflight": "PASS",
            "install": "PASS",
            **{key: "PASS" for key in sorted(driver_checks)},
        },
        "attachments": [
            {"basename": basename, "sha256": attachment_hashes[basename]}
            for basename in sorted(attachment_hashes)
        ],
    }
    return validate_environment_record(record, matrix)


def validate_environment_records(
    records: list[dict[str, Any]], matrix: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validate records and bind every record to one immutable DEB/policy tuple."""
    validate_certification_matrix(matrix)
    if type(records) is not list or not records:
        raise AssemblyError("environment evidence records must be a non-empty list")
    validated = [validate_environment_record(record, matrix) for record in records]
    categories = [record["category_id"] for record in validated]
    if len(categories) != len(set(categories)):
        raise AssemblyError("environment evidence contains duplicate category IDs")
    binding_keys = ("source_commit", "version", "architecture", "deb_basename", "deb_sha256", "compatibility_policy_id", "compatibility_policy_sha256")
    first = validated[0]
    for record in validated[1:]:
        for key in binding_keys:
            if record[key] != first[key]:
                if key == "deb_sha256":
                    raise AssemblyError("environment evidence records must use one DEB hash")
                if key == "compatibility_policy_sha256":
                    raise AssemblyError("environment evidence records must use one compatibility policy hash")
                raise AssemblyError(f"environment evidence records have mixed {key}")
    challenges = {record["challenge_nonce"] for record in validated}
    if len(challenges) != 1:
        raise AssemblyError("environment evidence records must use one certification challenge")
    positive = [record for record in validated if record["category_kind"] == "positive"]
    if len({record["machine_identity_commitment_sha256"] for record in positive}) != len(positive):
        raise AssemblyError(
            "positive environment evidence must use distinct machine identity commitments"
        )
    if len({record["acceptance_session_id"] for record in positive}) != len(positive):
        raise AssemblyError("positive environment evidence must use distinct acceptance sessions")
    return validated


def validate_canonical_manifest(
    manifest: dict[str, Any],
    *,
    deb: Path,
    deb_sha256: str,
    electron_sha256: str,
    desktop_entry_sha256: str,
    installed_version: str,
) -> tuple[str, str, str, str]:
    """Validate the v3 release manifest used by the unified DEB path."""
    if manifest.get("schema") != "taiji-package-manifest/v3":
        raise AssemblyError("canonical target evidence requires manifest schema taiji-package-manifest/v3")
    expected = {
        "package": "taiji-agent",
        "architecture": "amd64",
        "deb_basename": deb.name,
        "deb_sha256": deb_sha256,
        "electron_executable_sha256": electron_sha256,
        "desktop_entry_sha256": desktop_entry_sha256,
        "compatibility_policy_id": CERTIFICATION_POLICY_ID,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise AssemblyError(f"canonical release manifest {key} does not match the current installed artifact")
    source_commit = manifest.get("source_commit")
    if type(source_commit) is not str or not FULL_COMMIT_RE.fullmatch(source_commit):
        raise AssemblyError("canonical release manifest source_commit is invalid")
    version = manifest.get("version")
    if type(version) is not str or not VERSION_RE.fullmatch(version):
        raise AssemblyError("canonical release manifest version is invalid")
    if version != installed_version or deb.name != f"taiji-agent_{version}_amd64.deb":
        raise AssemblyError("canonical release manifest version and DEB basename do not match")
    policy_sha = require_sha256(
        manifest.get("compatibility_policy_sha256"),
        "canonical release manifest compatibility_policy_sha256",
    )
    return source_commit, version, manifest["compatibility_policy_id"], policy_sha


def validate_canonical_install_observation(
    observation: dict[str, Any],
    *,
    challenge: str,
    manifest_sha256: str,
    deb: Path,
    deb_sha256: str,
    source_commit: str,
) -> str:
    """Validate the no-target-baseline install observation emitted by v3 mode."""
    require_exact_keys(observation, {
        "schema", "generated_at_utc", "started_at_utc", "completed_at_utc", "challenge_nonce",
        "machine_identity_commitment_sha256", "machine_fingerprint_sha256",
        "boot_fingerprint_sha256", "target_uid",
        "canonical_home_fingerprint_sha256", "user_state_paths_fingerprint_sha256",
        "source_commit", "manifest_sha256", "deb_observed_basename", "deb_sha256",
        "candidate_file_count", "additional_install_files_observed", "package_status_before",
        "package_status_after", "package_status_transitions", "network_observation",
        "network_sample_interval_ms", "network_sample_count", "user_state_before",
        "user_state_after_install_before_first_launch", "first_launch_eligible",
        "installation_method_machine_observed", "observation_process_continuous",
    }, "canonical single-DEB install observation")
    expected = {
        "schema": CANONICAL_OBSERVATION_SCHEMA,
        "challenge_nonce": challenge,
        "source_commit": source_commit,
        "manifest_sha256": manifest_sha256,
        "deb_observed_basename": deb.name,
        "deb_sha256": deb_sha256,
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
    for key, value in expected.items():
        if observation.get(key) != value:
            raise AssemblyError(f"canonical install observation {key} is invalid")
    machine_identity_commitment = require_sha256(
        observation.get("machine_identity_commitment_sha256"),
        "canonical install observation machine_identity_commitment_sha256",
    )
    machine_fingerprint = require_sha256(
        observation.get("machine_fingerprint_sha256"),
        "canonical install observation machine_fingerprint_sha256",
    )
    boot_fingerprint = require_sha256(
        observation.get("boot_fingerprint_sha256"),
        "canonical install observation boot_fingerprint_sha256",
    )
    expected_machine_fingerprint = _machine_fingerprint_from_commitment(
        challenge, machine_identity_commitment
    )
    if machine_fingerprint != expected_machine_fingerprint:
        raise AssemblyError(
            "canonical install observation machine fingerprint does not match its identity commitment"
        )
    current_commitment, current_machine, current_boot = current_target_identity_binding(
        challenge
    )
    if (
        machine_identity_commitment != current_commitment
        or machine_fingerprint != current_machine
        or boot_fingerprint != current_boot
    ):
        raise AssemblyError("canonical install observation target identity does not match the current target")
    current_uid, current_home, current_paths = current_user_context_fingerprints(challenge)
    if observation.get("target_uid") != current_uid:
        raise AssemblyError("canonical install observation uid does not match the current user")
    if observation.get("canonical_home_fingerprint_sha256") != current_home:
        raise AssemblyError("canonical install observation canonical home does not match the current user")
    if observation.get("user_state_paths_fingerprint_sha256") != current_paths:
        raise AssemblyError("canonical install observation user state paths do not match the current user")
    started = require_utc_timestamp(observation["started_at_utc"], "canonical install observation start")
    completed = require_utc_timestamp(observation["completed_at_utc"], "canonical install observation completion")
    generated = require_utc_timestamp(observation["generated_at_utc"], "canonical install observation generation")
    if not started <= completed <= generated:
        raise AssemblyError("canonical install observation timestamps are not ordered")
    transitions = observation["package_status_transitions"]
    if type(transitions) is not list or not transitions or transitions[0] != "not-installed" or transitions[-1] != "install ok installed":
        raise AssemblyError("canonical install observation package transitions are invalid")
    if type(observation["network_sample_interval_ms"]) is not int or observation["network_sample_interval_ms"] <= 0:
        raise AssemblyError("canonical install observation sample interval is invalid")
    if type(observation["network_sample_count"]) is not int or observation["network_sample_count"] < 2:
        raise AssemblyError("canonical install observation sample count is invalid")
    return machine_identity_commitment, machine_fingerprint


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


def sha256_regular_file(
    path: Path,
    label: str,
    *,
    limit: int | None = None,
    required_prefix: bytes | None = None,
) -> str:
    descriptor, file_stat = open_regular(path, label)
    digest = hashlib.sha256()
    total = 0
    prefix = bytearray()
    try:
        if limit is not None and file_stat.st_size > limit:
            raise AssemblyError(f"{label} exceeds the {limit}-byte limit")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if required_prefix is not None and len(prefix) < len(required_prefix):
                needed = len(required_prefix) - len(prefix)
                prefix.extend(chunk[:needed])
            total += len(chunk)
            digest.update(chunk)
        if total != file_stat.st_size:
            raise AssemblyError(f"{label} was truncated while hashing")
        if required_prefix is not None and bytes(prefix) != required_prefix:
            raise AssemblyError(f"{label} has an invalid file signature")
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
    if set(query) != {"taiji_desktop"}:
        raise AssemblyError("driver app_url contains unexpected query data")
    if query.get("taiji_desktop") != ["1"]:
        raise AssemblyError("driver app_url must contain exactly one desktop marker")
    if origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost"}:
        raise AssemblyError("driver webui_origin must be an HTTP loopback origin")
    if origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}:
        raise AssemblyError("driver webui_origin must not contain credentials, query, fragment, or path")
    app_origin = f"{app.scheme}://{app.netloc}"
    expected_origin = f"{origin.scheme}://{origin.netloc}"
    if app_origin != expected_origin:
        raise AssemblyError("driver app_url and webui_origin do not identify the same App")


def validate_desktop_auth_cookie(cookie: Any) -> None:
    if type(cookie) is not dict:
        raise AssemblyError("driver desktop_auth_cookie must be an object")
    require_exact_keys(cookie, DESKTOP_AUTH_COOKIE_KEYS, "driver desktop_auth_cookie")
    expected = {
        "name": "taiji_desktop_token",
        "present": True,
        "http_only": True,
        "same_site": "Strict",
        "path": "/",
        "value_format": "lowercase-hex-64",
    }
    for key, value in expected.items():
        if type(cookie[key]) is not type(value) or cookie[key] != value:
            raise AssemblyError(f"driver desktop_auth_cookie {key} is invalid")


def validate_driver_result(driver: dict[str, Any], challenge: str) -> None:
    require_exact_keys(driver, DRIVER_KEYS, "driver-result.json")
    if driver["schema"] != "taiji.desktop.acceptance-driver.v2":
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
    validate_desktop_auth_cookie(driver["desktop_auth_cookie"])
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

    restart_rounds = driver["restart_rounds"]
    if type(restart_rounds) is not list or len(restart_rounds) != 3:
        raise AssemblyError("driver restart_rounds must contain exactly three rounds")
    for index, round_record in enumerate(restart_rounds, start=1):
        if type(round_record) is not dict:
            raise AssemblyError(f"driver restart round {index} must be an object")
        require_exact_keys(
            round_record,
            DRIVER_RESTART_ROUND_KEYS,
            f"driver restart round {index}",
        )
        if type(round_record["round"]) is not int or round_record["round"] != index:
            raise AssemblyError("driver restart rounds are out of order")
        for key in ("electron_pid", "agent_pid", "web_pid", "secondary_pid"):
            require_pid(round_record[key], f"driver restart round {index} {key}")
        for key in ("cdp_port", "webui_port"):
            value = round_record[key]
            if type(value) is not int or value < 1024 or value > 65535:
                raise AssemblyError(f"driver restart round {index} {key} is invalid")
        for key in ("second_instance_exit_code", "electron_exit_code"):
            if type(round_record[key]) is not int or round_record[key] != 0:
                raise AssemblyError(f"driver restart round {index} {key} must be integer zero")
        for key in (
            "ready",
            "restored_and_focused",
            "page_close_sent",
            "pidfiles_absent",
            "model_config_observed",
            "profile_continuity_observed",
        ):
            if type(round_record[key]) is not bool or round_record[key] is not True:
                raise AssemblyError(f"driver restart round {index} {key} must be true")
        process_gone = round_record["process_identities_gone"]
        if type(process_gone) is not dict:
            raise AssemblyError(f"driver restart round {index} process identity evidence is invalid")
        require_exact_keys(
            process_gone,
            {"electron", "agent", "webui", "secondary"},
            f"driver restart round {index} process identity evidence",
        )
        if any(type(value) is not bool or value is not True for value in process_gone.values()):
            raise AssemblyError(f"driver restart round {index} left a process identity behind")
        ports_closed = round_record["ports_closed"]
        if type(ports_closed) is not dict:
            raise AssemblyError(f"driver restart round {index} port evidence is invalid")
        require_exact_keys(
            ports_closed,
            {"cdp", "webui"},
            f"driver restart round {index} port evidence",
        )
        if any(type(value) is not bool or value is not True for value in ports_closed.values()):
            raise AssemblyError(f"driver restart round {index} left a port open")

    round_one = restart_rounds[0]
    for driver_key, round_key in (
        ("electron_pid", "electron_pid"),
        ("agent_pid", "agent_pid"),
        ("web_pid", "web_pid"),
        ("electron_exit_code", "electron_exit_code"),
    ):
        if driver[driver_key] != round_one[round_key]:
            raise AssemblyError(f"driver {driver_key} is not a strict restart round one alias")
    app_port = urlsplit(driver["app_url"]).port
    if app_port != round_one["webui_port"]:
        raise AssemblyError("driver App URL port is not the first restart WebUI port")

    persistent = driver["persistent_user_data"]
    if type(persistent) is not dict:
        raise AssemblyError("driver persistent_user_data must be an object")
    require_exact_keys(
        persistent,
        DRIVER_PERSISTENT_USER_DATA_KEYS,
        "driver persistent_user_data",
    )
    expected_persistent = {
        "mode": "electron-default-persistent",
        "restart_rounds": 3,
        "user_data_override": False,
        "profile_reset": False,
        "environment_reused": True,
        "continuity_observed_rounds": 3,
    }
    for key, expected in expected_persistent.items():
        if type(persistent[key]) is not type(expected) or persistent[key] != expected:
            raise AssemblyError(f"driver persistent_user_data {key} is invalid")
    require_sha256(persistent["continuity_token"], "driver persistent profile token")

    core = driver["core_observation"]
    if type(core) is not dict:
        raise AssemblyError("driver core_observation must be an object")
    require_exact_keys(core, DRIVER_CORE_OBSERVATION_KEYS, "driver core_observation")
    if core["status"] != "verified" or core["mechanism"] != "journalctl-json-user-electron":
        raise AssemblyError("driver core observation was not verified")
    if type(core["baseline_entry_count"]) is not int or core["baseline_entry_count"] < 0:
        raise AssemblyError("driver core observation baseline count is invalid")
    require_sha256(core["baseline_cursor_set_token"], "driver core baseline cursor token")
    core_rounds = core["rounds"]
    if type(core_rounds) is not list or len(core_rounds) != 3:
        raise AssemblyError("driver core observation must cover exactly three rounds")
    for index, core_round in enumerate(core_rounds, start=1):
        if type(core_round) is not dict:
            raise AssemblyError(f"driver core observation round {index} must be an object")
        require_exact_keys(core_round, DRIVER_CORE_ROUND_KEYS, f"driver core observation round {index}")
        if (
            type(core_round["round"]) is not int
            or core_round["round"] != index
            or core_round["status"] != "verified"
            or type(core_round["added_entry_count"]) is not int
            or core_round["added_entry_count"] != 0
        ):
            raise AssemblyError(f"driver core observation round {index} is invalid")
        require_sha256(core_round["cursor_set_token"], f"driver core observation round {index} cursor token")

    model_observation = driver["model_config_observation"]
    if type(model_observation) is not dict:
        raise AssemblyError("driver model_config_observation must be an object")
    require_exact_keys(
        model_observation,
        DRIVER_MODEL_CONFIG_OBSERVATION_KEYS,
        "driver model_config_observation",
    )
    if (
        type(model_observation["observed_rounds"]) is not int
        or model_observation["observed_rounds"] != 3
        or type(model_observation["consistent"]) is not bool
        or model_observation["consistent"] is not True
    ):
        raise AssemblyError("driver model configuration observation is inconsistent")
    require_sha256(
        model_observation["public_projection_token"],
        "driver model configuration projection token",
    )
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
) -> tuple[str, str, str, str]:
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version != 2:
        raise AssemblyError("sales target evidence requires release manifest schema_version=2")
    expected = {
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
    if type(source_commit) is not str or not FULL_COMMIT_RE.fullmatch(source_commit):
        raise AssemblyError("release manifest source_commit is invalid")
    version = manifest.get("version")
    if type(version) is not str or not VERSION_RE.fullmatch(version):
        raise AssemblyError("release manifest version is invalid")
    if version != installed_version:
        raise AssemblyError("installed package version does not match the release manifest")
    if deb.name != f"taiji-agent_{version}_amd64.deb":
        raise AssemblyError("current DEB basename does not match the installed package version")
    target_baseline_profile_id = manifest.get("target_baseline_profile_id")
    if (
        type(target_baseline_profile_id) is not str
        or not TARGET_PROFILE_ID_RE.fullmatch(target_baseline_profile_id)
    ):
        raise AssemblyError("release manifest target_baseline_profile_id is invalid")
    target_baseline_sha256 = require_sha256(
        manifest.get("target_baseline_sha256"),
        "release manifest target_baseline_sha256",
    )
    return source_commit, version, target_baseline_profile_id, target_baseline_sha256


def require_utc_timestamp(value: Any, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise AssemblyError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AssemblyError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise AssemblyError(f"{label} must include a UTC timezone")
    return parsed


def validate_install_observation(
    observation: dict[str, Any],
    *,
    challenge: str,
    manifest_sha256: str,
    deb: Path,
    deb_sha256: str,
    source_commit: str,
    target_baseline_profile_id: str,
    target_baseline_sha256: str,
) -> str:
    require_exact_keys(observation, INSTALL_OBSERVATION_KEYS, "single-DEB install observation")
    expected = {
        "schema": "taiji.single-deb-install-observation.v1",
        "challenge_nonce": challenge,
        "source_commit": source_commit,
        "manifest_sha256": manifest_sha256,
        "deb_observed_basename": deb.name,
        "deb_sha256": deb_sha256,
        "target_baseline_profile_id": target_baseline_profile_id,
        "target_baseline_sha256": target_baseline_sha256,
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
    for key, value in expected.items():
        if type(observation[key]) is not type(value) or observation[key] != value:
            raise AssemblyError(f"single-DEB install observation {key} is invalid")
    machine_fingerprint = require_sha256(
        observation["machine_fingerprint_sha256"],
        "single-DEB install observation machine_fingerprint_sha256",
    )
    boot_fingerprint = require_sha256(
        observation["boot_fingerprint_sha256"],
        "single-DEB install observation boot_fingerprint_sha256",
    )
    current_machine_fingerprint, current_boot_fingerprint = current_target_fingerprints(challenge)
    if machine_fingerprint != current_machine_fingerprint:
        raise AssemblyError(
            "single-DEB install observation machine does not match the current target"
        )
    if boot_fingerprint != current_boot_fingerprint:
        raise AssemblyError(
            "single-DEB install observation boot identity does not match the current target"
        )
    current_uid, current_home_fingerprint, current_paths_fingerprint = (
        current_user_context_fingerprints(challenge)
    )
    if type(observation["target_uid"]) is not int or observation["target_uid"] != current_uid:
        raise AssemblyError("single-DEB install observation uid does not match the current user")
    home_fingerprint = require_sha256(
        observation["canonical_home_fingerprint_sha256"],
        "single-DEB install observation canonical_home_fingerprint_sha256",
    )
    if home_fingerprint != current_home_fingerprint:
        raise AssemblyError(
            "single-DEB install observation canonical home does not match the current user"
        )
    paths_fingerprint = require_sha256(
        observation["user_state_paths_fingerprint_sha256"],
        "single-DEB install observation user_state_paths_fingerprint_sha256",
    )
    if paths_fingerprint != current_paths_fingerprint:
        raise AssemblyError(
            "single-DEB install observation user state paths do not match the current user"
        )
    started = require_utc_timestamp(observation["started_at_utc"], "install observation start")
    completed = require_utc_timestamp(observation["completed_at_utc"], "install observation completion")
    generated = require_utc_timestamp(observation["generated_at_utc"], "install observation generation")
    if not started <= completed <= generated:
        raise AssemblyError("single-DEB install observation timestamps are not ordered")
    transitions = observation["package_status_transitions"]
    if (
        type(transitions) is not list
        or not transitions
        or any(type(value) is not str for value in transitions)
        or transitions[0] != "not-installed"
        or transitions[-1] != "install ok installed"
    ):
        raise AssemblyError("single-DEB install observation package transitions are invalid")
    if type(observation["network_sample_interval_ms"]) is not int or observation["network_sample_interval_ms"] <= 0:
        raise AssemblyError("single-DEB install observation sample interval is invalid")
    if type(observation["network_sample_count"]) is not int or observation["network_sample_count"] < 2:
        raise AssemblyError("single-DEB install observation sample count is invalid")
    return machine_fingerprint


def validate_install_method_attestation(
    attestation: dict[str, Any],
    *,
    observation_sha256: str,
    observation: dict[str, Any],
    graphical_evidence_sha256: str,
    challenge: str,
) -> None:
    require_exact_keys(
        attestation,
        INSTALL_METHOD_ATTESTATION_KEYS,
        "single-DEB install method attestation",
    )
    expected = {
        "schema": "taiji.single-deb-install-method-attestation.v1",
        "observation_basename": INSTALL_OBSERVATION_BASENAME,
        "observation_sha256": observation_sha256,
        "challenge_nonce": challenge,
        "machine_fingerprint_sha256": observation["machine_fingerprint_sha256"],
        "boot_fingerprint_sha256": observation["boot_fingerprint_sha256"],
        "deb_sha256": observation["deb_sha256"],
        "installation_method_attested": "desktop-double-click",
        "installation_method_machine_observed": False,
        "attestation_scope": "human-observed-system-graphical-installer",
        "confirmation": True,
        "graphical_installer_evidence_basename": GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
        "graphical_installer_evidence_sha256": graphical_evidence_sha256,
    }
    for key, value in expected.items():
        if type(attestation[key]) is not type(value) or attestation[key] != value:
            raise AssemblyError(f"single-DEB install method attestation {key} is invalid")
    if type(attestation["operator_id"]) is not str or not OPERATOR_ID_RE.fullmatch(attestation["operator_id"]):
        raise AssemblyError("install method attestation operator_id is invalid")
    attested_at = require_utc_timestamp(
        attestation["generated_at_utc"],
        "install method attestation generation",
    )
    completed_at = require_utc_timestamp(
        observation["completed_at_utc"],
        "install observation completion",
    )
    if attested_at < completed_at:
        raise AssemblyError("install method attestation predates the observed installation")


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


def assemble_canonical(args: argparse.Namespace) -> None:
    """Assemble one category-bound v3 environment evidence directory.

    This mode intentionally emits an evidence envelope, never a certification
    claim.  The complete six-positive/six-negative decision is deferred to the
    certification-set validator.
    """
    challenge = args.challenge
    if not CHALLENGE_RE.fullmatch(challenge or ""):
        raise AssemblyError("challenge must be 64-128 lowercase hexadecimal characters")
    release_artifacts_sha256 = require_sha256(
        args.release_artifacts_sha256, "release_artifacts_sha256"
    )
    if args.environment_observation.name != ENVIRONMENT_OBSERVATION_BASENAME:
        raise AssemblyError("environment observation input must use the fixed observation basename")
    matrix = validate_certification_matrix(load_json(Path(args.matrix), "certification matrix"))
    category_map = {
        item["id"]: item
        for item in matrix["positive_categories"] + matrix["negative_boundaries"]
    }
    category = category_map.get(args.category_id)
    if category is None:
        raise AssemblyError("category_id is not in the certification matrix")
    driver_payload = read_regular_bytes(args.driver_result, "driver-result.json")
    driver = parse_json_bytes(driver_payload, "driver-result.json")
    validate_driver_result(driver, challenge)
    manifest = load_json(args.manifest, "release manifest")
    deb_sha256 = sha256_regular_file(args.deb, "current DEB")
    electron_sha256 = sha256_regular_file(args.electron_executable, "installed Electron executable")
    desktop_entry_sha256 = sha256_regular_file(args.desktop_entry, "installed desktop entry")
    source_commit, version, policy_id, policy_sha = validate_canonical_manifest(
        manifest,
        deb=args.deb,
        deb_sha256=deb_sha256,
        electron_sha256=electron_sha256,
        desktop_entry_sha256=desktop_entry_sha256,
        installed_version=args.installed_package_version,
    )
    if electron_sha256 != driver["electron_executable_sha256"]:
        raise AssemblyError("installed Electron hash does not match the desktop acceptance driver")
    if desktop_entry_sha256 != driver["desktop_entry_sha256"]:
        raise AssemblyError("installed desktop entry hash does not match the desktop acceptance driver")
    observation_payload = read_regular_bytes(args.install_observation, "canonical install observation")
    observation = parse_json_bytes(observation_payload, "canonical install observation")
    observation_hash = hashlib.sha256(observation_payload).hexdigest()
    machine_identity_commitment, machine_fingerprint = validate_canonical_install_observation(
        observation,
        challenge=challenge,
        manifest_sha256=sha256_regular_file(args.manifest, "release manifest"),
        deb=args.deb,
        deb_sha256=deb_sha256,
        source_commit=source_commit,
    )
    environment_payload = read_regular_bytes(args.environment_observation, "environment observation")
    environment_observation = parse_json_bytes(environment_payload, "environment observation")
    validate_environment_observation(environment_observation, matrix)
    for key, value in {
        "category_id": args.category_id,
        "source_commit": source_commit,
        "version": version,
        "architecture": "amd64",
        "deb_basename": args.deb.name,
        "deb_sha256": deb_sha256,
        "compatibility_policy_id": policy_id,
        "compatibility_policy_sha256": policy_sha,
        "machine_identity_commitment_sha256": machine_identity_commitment,
        "os_id": args.os_id,
        "os_version": args.os_version,
        "desktop_environment": args.desktop_environment,
    }.items():
        if environment_observation.get(key) != value:
            raise AssemblyError(f"environment observation {key} does not match the current release or target")
    graphical_evidence_hash = sha256_regular_file(
        args.graphical_installer_evidence,
        "graphical installer evidence",
        limit=MAX_SCREENSHOT_BYTES,
        required_prefix=b"\x89PNG\r\n\x1a\n",
    )
    attestation_payload = read_regular_bytes(
        args.install_method_attestation,
        "single-DEB install method attestation",
    )
    attestation = parse_json_bytes(attestation_payload, "single-DEB install method attestation")
    validate_install_method_attestation(
        attestation,
        observation_sha256=observation_hash,
        observation=observation,
        graphical_evidence_sha256=graphical_evidence_hash,
        challenge=challenge,
    )
    if args.screenshot.name != driver["screenshot_basename"]:
        raise AssemblyError("screenshot input basename does not match the driver result")
    if args.diagnostic.name != driver["diagnostic_basename"]:
        raise AssemblyError("diagnostic input basename does not match the driver result")
    screenshot_hash = sha256_regular_file(
        args.screenshot,
        "desktop App screenshot",
        limit=MAX_SCREENSHOT_BYTES,
        required_prefix=b"\x89PNG\r\n\x1a\n",
    )
    diagnostic_payload = read_regular_bytes(args.diagnostic, "App diagnostic export")
    diagnostic_json = parse_json_bytes(diagnostic_payload, "App diagnostic export")
    if set(diagnostic_json) != {"schema", "manifest", "diagnostics"} or diagnostic_json["schema"] != "taiji.product.support-bundle.v1":
        raise AssemblyError("App diagnostic export has an invalid schema")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    diagnostic_hash = hashlib.sha256(diagnostic_payload).hexdigest()
    graphical_name = GRAPHICAL_INSTALLER_EVIDENCE_BASENAME

    def produce(temporary: Path) -> None:
        copied_screenshot_hash = copy_regular_file(
            args.screenshot,
            temporary / SCREENSHOT_BASENAME,
            "desktop App screenshot",
            limit=MAX_SCREENSHOT_BYTES,
            required_prefix=b"\x89PNG\r\n\x1a\n",
        )
        copied_environment_observation_hash = write_exclusive(
            temporary / ENVIRONMENT_OBSERVATION_BASENAME,
            environment_payload,
        )
        copied_observation_hash = write_exclusive(
            temporary / INSTALL_OBSERVATION_BASENAME,
            observation_payload,
        )
        copied_attestation_hash = write_exclusive(
            temporary / INSTALL_METHOD_ATTESTATION_BASENAME,
            attestation_payload,
        )
        copied_graphical_hash = copy_regular_file(
            args.graphical_installer_evidence,
            temporary / graphical_name,
            "graphical installer evidence",
            limit=MAX_SCREENSHOT_BYTES,
            required_prefix=b"\x89PNG\r\n\x1a\n",
        )
        copied_driver_hash = write_exclusive(temporary / DRIVER_RESULT_BASENAME, driver_payload)
        copied_diagnostic_hash = write_exclusive(temporary / DIAGNOSTIC_BASENAME, diagnostic_payload)
        if (
            copied_screenshot_hash != screenshot_hash
            or copied_graphical_hash != graphical_evidence_hash
            or copied_environment_observation_hash
            != hashlib.sha256(environment_payload).hexdigest()
        ):
            raise AssemblyError("canonical evidence changed while it was copied")
        target_evidence = {
            "schema": CANONICAL_TARGET_EVIDENCE_SCHEMA,
            "evidence_type": "target-desktop-environment",
            "generated_at_utc": generated_at,
            "acceptance_session_id": driver["acceptance_session_id"],
            "challenge_nonce": challenge,
            "machine_identity_commitment_sha256": machine_identity_commitment,
            "machine_fingerprint_sha256": machine_fingerprint,
            "release_artifacts_sha256": release_artifacts_sha256,
            "category_id": args.category_id,
            "category_kind": environment_observation["category_kind"],
            "compatibility": environment_observation["compatibility"],
            "source_commit": source_commit,
            "version": version,
            "architecture": "amd64",
            "deb_basename": args.deb.name,
            "deb_sha256": deb_sha256,
            "compatibility_policy_id": policy_id,
            "compatibility_policy_sha256": policy_sha,
            "os_id": args.os_id,
            "os_version": args.os_version,
            "desktop_environment": args.desktop_environment,
            "installation_method": "desktop-double-click",
            "installation_method_evidence": "human-attestation",
            "installation_method_machine_observed": False,
            "checks": driver["checks"],
            "environment_observation_basename": ENVIRONMENT_OBSERVATION_BASENAME,
            "environment_observation_sha256": copied_environment_observation_hash,
            "install_observation_basename": INSTALL_OBSERVATION_BASENAME,
            "install_observation_sha256": copied_observation_hash,
            "install_method_attestation_basename": INSTALL_METHOD_ATTESTATION_BASENAME,
            "install_method_attestation_sha256": copied_attestation_hash,
            "graphical_installer_evidence_basename": graphical_name,
            "graphical_installer_evidence_sha256": copied_graphical_hash,
            "driver_result_basename": DRIVER_RESULT_BASENAME,
            "driver_result_sha256": copied_driver_hash,
            "screenshot_basename": SCREENSHOT_BASENAME,
            "screenshot_sha256": copied_screenshot_hash,
            "diagnostic_basename": DIAGNOSTIC_BASENAME,
            "diagnostic_sha256": copied_diagnostic_hash,
        }
        require_exact_keys(
            target_evidence,
            CANONICAL_TARGET_EVIDENCE_KEYS,
            "canonical target evidence",
        )
        target_evidence_hash = write_exclusive(
            temporary / EVIDENCE_BASENAME,
            json_bytes(target_evidence),
        )
        final_environment_record = build_positive_environment_evidence(
            environment_observation,
            matrix=matrix,
            driver_checks=driver["checks"],
            challenge=challenge,
            acceptance_session_id=driver["acceptance_session_id"],
            attachment_hashes={
                EVIDENCE_BASENAME: target_evidence_hash,
                ENVIRONMENT_OBSERVATION_BASENAME: copied_environment_observation_hash,
                INSTALL_OBSERVATION_BASENAME: copied_observation_hash,
                INSTALL_METHOD_ATTESTATION_BASENAME: copied_attestation_hash,
                graphical_name: copied_graphical_hash,
                DRIVER_RESULT_BASENAME: copied_driver_hash,
                SCREENSHOT_BASENAME: copied_screenshot_hash,
                DIAGNOSTIC_BASENAME: copied_diagnostic_hash,
            },
        )
        write_exclusive(
            temporary / ENVIRONMENT_EVIDENCE_BASENAME,
            json_bytes(final_environment_record),
        )

    publish_atomically(args.output_dir, produce)


def assemble(args: argparse.Namespace) -> None:
    if args.matrix or args.category_id or args.environment_observation:
        if not args.matrix or not args.category_id or not args.environment_observation:
            raise AssemblyError("canonical mode requires --matrix, --category-id, and --environment-observation")
        assemble_canonical(args)
        return
    challenge = args.challenge
    if not CHALLENGE_RE.fullmatch(challenge or ""):
        raise AssemblyError("challenge must be 64-128 lowercase hexadecimal characters")
    release_artifacts_sha256 = require_sha256(
        args.release_artifacts_sha256, "release_artifacts_sha256"
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
    (
        source_commit,
        version,
        target_baseline_profile_id,
        target_baseline_sha256,
    ) = validate_manifest(
        manifest,
        deb=args.deb,
        deb_sha256=deb_sha256,
        electron_sha256=electron_sha256,
        desktop_entry_sha256=desktop_entry_sha256,
        installed_version=installed_version,
    )
    if args.install_observation.name != INSTALL_OBSERVATION_BASENAME:
        raise AssemblyError("install observation input must use the fixed evidence basename")
    if args.install_method_attestation.name != INSTALL_METHOD_ATTESTATION_BASENAME:
        raise AssemblyError("install method attestation input must use the fixed evidence basename")
    if args.graphical_installer_evidence.name != GRAPHICAL_INSTALLER_EVIDENCE_BASENAME:
        raise AssemblyError("graphical installer evidence input must use the fixed evidence basename")
    observation_payload = read_regular_bytes(
        args.install_observation,
        "single-DEB install observation",
    )
    observation = parse_json_bytes(observation_payload, "single-DEB install observation")
    observation_hash = hashlib.sha256(observation_payload).hexdigest()
    manifest_hash = sha256_regular_file(args.manifest, "release manifest")
    machine_fingerprint_sha256 = validate_install_observation(
        observation,
        challenge=challenge,
        manifest_sha256=manifest_hash,
        deb=args.deb,
        deb_sha256=deb_sha256,
        source_commit=source_commit,
        target_baseline_profile_id=target_baseline_profile_id,
        target_baseline_sha256=target_baseline_sha256,
    )
    graphical_evidence_hash = sha256_regular_file(
        args.graphical_installer_evidence,
        "graphical installer evidence",
        limit=MAX_SCREENSHOT_BYTES,
        required_prefix=b"\x89PNG\r\n\x1a\n",
    )
    attestation_payload = read_regular_bytes(
        args.install_method_attestation,
        "single-DEB install method attestation",
    )
    attestation = parse_json_bytes(
        attestation_payload,
        "single-DEB install method attestation",
    )
    validate_install_method_attestation(
        attestation,
        observation_sha256=observation_hash,
        observation=observation,
        graphical_evidence_sha256=graphical_evidence_hash,
        challenge=challenge,
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
        install_observation_hash = write_exclusive(
            temporary / INSTALL_OBSERVATION_BASENAME,
            observation_payload,
        )
        install_method_attestation_hash = write_exclusive(
            temporary / INSTALL_METHOD_ATTESTATION_BASENAME,
            attestation_payload,
        )
        copied_graphical_evidence_hash = copy_regular_file(
            args.graphical_installer_evidence,
            temporary / GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
            "graphical installer evidence",
            limit=MAX_SCREENSHOT_BYTES,
            required_prefix=b"\x89PNG\r\n\x1a\n",
        )
        if install_observation_hash != observation_hash:
            raise AssemblyError("copied install observation hash changed")
        if copied_graphical_evidence_hash != graphical_evidence_hash:
            raise AssemblyError("copied graphical installer evidence hash changed")
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
            "target_baseline_profile_id": target_baseline_profile_id,
            "target_baseline_sha256": target_baseline_sha256,
            "installation_method": "desktop-double-click",
            "installation_method_evidence": "human-attestation",
            "installation_method_machine_observed": False,
            "installation_network": "continuous-process-sampling-no-non-loopback-up",
            "installation_file_count": 1,
            "additional_install_files": False,
            "dpkg_status_before": "not-installed",
            "dpkg_status_after": "install ok installed",
            "first_configuration_cycle_completed": True,
            "machine_fingerprint_sha256": machine_fingerprint_sha256,
            "install_observation_basename": INSTALL_OBSERVATION_BASENAME,
            "install_observation_sha256": install_observation_hash,
            "install_method_attestation_basename": INSTALL_METHOD_ATTESTATION_BASENAME,
            "install_method_attestation_sha256": install_method_attestation_hash,
            "graphical_installer_evidence_basename": GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
            "graphical_installer_evidence_sha256": copied_graphical_evidence_hash,
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
            "schema_version": 2,
            "evidence_type": "target-desktop-verification",
            "application": "taiji-electron-desktop",
            "generated_at_utc": generated_at,
            "acceptance_session_id": driver["acceptance_session_id"],
            "challenge_nonce": challenge,
            "machine_fingerprint_sha256": machine_fingerprint_sha256,
            "release_artifacts_sha256": release_artifacts_sha256,
            "target_baseline_profile_id": target_baseline_profile_id,
            "target_baseline_sha256": target_baseline_sha256,
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
            "installation_method": "desktop-double-click",
            "installation_method_evidence": "human-attestation",
            "installation_method_machine_observed": False,
            "installation_network": "continuous-process-sampling-no-non-loopback-up",
            "installation_file_count": 1,
            "additional_install_files": False,
            "dpkg_status_before": "not-installed",
            "dpkg_status_after": "install ok installed",
            "first_configuration_cycle_completed": True,
            "visible_first_configuration_completion": True,
            "target_verified": True,
            "desktop_launch": True,
            "real_model_conversation": True,
            "attachment_flow": True,
            "window_close_exit": True,
            "diagnostic_export": True,
            "three_restart_cycles": checks["three_restart_cycles"],
            "second_instance_focus": checks["second_instance_focus"],
            "model_configuration_state_consistent": checks[
                "model_configuration_state_consistent"
            ],
            "no_new_electron_core": checks["no_new_electron_core"],
            "session_log_basename": SESSION_BASENAME,
            "session_log_sha256": session_hash,
            "screenshot_basename": SCREENSHOT_BASENAME,
            "screenshot_sha256": screenshot_hash,
            "diagnostic_basename": DIAGNOSTIC_BASENAME,
            "diagnostic_sha256": diagnostic_hash,
            "driver_result_basename": DRIVER_RESULT_BASENAME,
            "driver_result_sha256": driver_result_hash,
            "install_observation_basename": INSTALL_OBSERVATION_BASENAME,
            "install_observation_sha256": install_observation_hash,
            "install_method_attestation_basename": INSTALL_METHOD_ATTESTATION_BASENAME,
            "install_method_attestation_sha256": install_method_attestation_hash,
            "graphical_installer_evidence_basename": GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
            "graphical_installer_evidence_sha256": copied_graphical_evidence_hash,
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
    parser.add_argument("--install-observation", required=True, type=Path)
    parser.add_argument("--install-method-attestation", required=True, type=Path)
    parser.add_argument("--graphical-installer-evidence", required=True, type=Path)
    parser.add_argument("--release-artifacts-sha256", required=True)
    parser.add_argument("--installed-package-version", required=True)
    parser.add_argument("--challenge", required=True)
    parser.add_argument("--os-id", required=True)
    parser.add_argument("--os-version", required=True)
    parser.add_argument("--desktop-environment", required=True)
    parser.add_argument("--matrix")
    parser.add_argument("--category-id")
    parser.add_argument("--environment-observation", type=Path)
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
        "install_observation",
        "install_method_attestation",
        "graphical_installer_evidence",
        "output_dir",
    ):
        value = getattr(args, name)
        if value is None:
            continue
        if not value.is_absolute():
            parser.error(f"--{name.replace('_', '-')} must be an absolute path")
    if args.matrix is not None and not Path(args.matrix).is_absolute():
        parser.error("--matrix must be an absolute path")
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
