#!/usr/bin/env python3
"""Validate Taiji release evidence against current, manifest-bound build artifacts."""

from __future__ import annotations

import argparse
import base64
import binascii
import builtins
import contextvars
import hashlib
import importlib.util
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TARGET_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
POLICY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,127}$")
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
INCIDENT_RE = re.compile(r"^inc-[0-9a-f]{12,32}$")
OPERATOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
UNSAFE_VERSION_RE = re.compile(
    r"(?i)(?:hermes|password|passwd|passphrase|secret|token|bearer|(?:^|[-_.])sk-|(?:^|[-_.])key(?:[-_.]|$))"
)
PUBLIC_VERSION_RE = re.compile(
    r"^(?:"
    r"v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"|[0-9a-f]{7,40}(?:-dirty(?:\.[0-9a-f]{7,40})?)?"
    r")$"
)
MAX_JSON_BYTES = 1024 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_PREVIOUS_RELEASE_DEB_BYTES = 2 * 1024 * 1024 * 1024
CERTIFICATION_LARGE_PNG_BASENAMES = {
    "single-deb-graphical-installer.png",
    "desktop-app.png",
}
PACKAGE_MANIFEST_SCHEMA_V3 = "taiji-package-manifest/v3"
RELEASE_EVIDENCE_SCHEMA_V3 = "taiji-release-evidence/v3"
OFFLINE_EVIDENCE_SCHEMA_V1 = "taiji.offline-install-rehearsal.v1"
OFFLINE_REHEARSAL_ENVIRONMENT = "container-kylin-policy-fixture-v1"
CANONICAL_POLICY_ID = "taiji-linux-amd64-deb-v1"
# Fallback for a validator copied into a target delivery directory.  When the
# checked-in policy is available, ``canonical_policy_identity`` recomputes the
# value through compatibility_policy.py instead of trusting this constant.
CANONICAL_POLICY_SHA256 = "05b6fd042104ad096a8545ddd9fd6607622efcc748bc10102314447fcff2fa4b"
CANONICAL_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "packaging/linux/compatibility-policy.json"
)
CANONICAL_POLICY_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "packaging/linux/compatibility_policy.py"
)
PINNED_UV_VERSION = "0.12.2"
PINNED_UV_ARCHIVE_SHA256 = "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4"
PINNED_UV_EXECUTABLE_SHA256 = "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2"
PINNED_NODE_VERSION = "22.23.1"
PINNED_NODE_ARCHIVE_SHA256 = "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
PINNED_NODE_EXECUTABLE_SHA256 = "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068"
PINNED_NPM_VERSION = "10.9.8"
PINNED_NPM_CLI_SHA256 = "8e5f6f3429f8cdbe693cdc29904e9d5a7b127a494bd15c804bd54c7403bfcbe7"
PINNED_PYTHON_VERSION = "3.11.15"
PINNED_PYTHON_ARCHIVE_SHA256 = "2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a"
PINNED_PYTHON_EXECUTABLE_SHA256 = "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba"
PINNED_ELECTRON_VERSION = "39.8.10"
PINNED_ELECTRON_ARCHIVE_SHA256 = "92e8b031fa5327c78a972279fd75fc8503fcd1773401809f4557e4de583eabd1"
PINNED_ELECTRON_EXECUTABLE_SHA256 = "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d"
PINNED_SOURCE_INTEGRITY_HELPER_SHA256 = "eaebadbe2f86d76d09f19ed210ad407e5926a242c46f53fb89e26253db8d8d7a"
PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
PINNED_RELEASE_PUBLIC_KEY_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools/taiji-release-evidence/signing-public.pem"
)
TRUSTED_OPENSSL = Path("/usr/bin/openssl")
TRUSTED_SYSTEM_PYTHON = Path("/usr/bin/python3")
TOOLCHAIN_MANIFEST_FIELDS = {
    "python_dependency_lock_status",
    "python_lock_basename",
    "python_lock_sha256",
    "python_version",
    "python_archive_sha256",
    "python_executable_sha256",
    "uv_version",
    "uv_archive_sha256",
    "uv_executable_sha256",
    "node_version",
    "node_archive_sha256",
    "node_executable_sha256",
    "electron_version",
    "electron_archive_sha256",
    "electron_executable_sha256",
}
ACCEPTANCE_MANIFEST_FIELDS = {
    "acceptance_binding_sha256",
    "acceptance_tools_manifest_sha256",
    "acceptance_entrypoint_sha256",
    "installed_release_manifest_sha256",
}
FORMAL_BUILD_TEST_LOG_BASENAME = "formal-build-tests.log"
FORMAL_BUILD_TEST_LOG_SCHEMA = "taiji-formal-build-tests/v2"
FORMAL_BUILD_TEST_TARGET_COUNT = 20
FORMAL_BUILD_TEST_TARGET_CONTRACT_BYTES = 1864
FORMAL_BUILD_TEST_TARGET_CONTRACT_SHA256 = (
    "5fdcd9335ac9c722b224c06b03d817bd505cff4abc514b09f8d9ba604c11953b"
)
FORMAL_BUILD_TEST_FIELDS = {
    "formal_build_tests_status",
    "formal_build_tests_log_basename",
    "formal_build_tests_log_sha256",
}
FORMAL_BUILD_TEST_SUITES = (
    "root-runtime",
    "desktop-evidence-node",
    "kylin-install-simulation",
    "agent",
    "webui-runtime-lint",
    "webui-python",
)
FORMAL_BUILD_TEST_RUNNERS = frozenset({"unittest", "node-test", "pytest", "eslint"})
FORMAL_BUILD_TEST_COUNT_FIELD_COUNT = 7
DELIVERY_INVENTORY_EXCLUDED_TOP_LEVEL = frozenset(
    {
        "certification",
        "offline-install-rehearsal",
        "target-verification",
        "构建日志",
        "诊断报告",
        "旧版备份",
    }
)
DELIVERY_INVENTORY_EXCLUDED_ROOT_FILES = frozenset(
    {"release-evidence.json", "release-evidence.json.sig"}
)
PACKAGE_MANIFEST_V3_EXACT_FIELDS = {
    "schema",
    "package",
    "version",
    "architecture",
    "source_commit",
    "source_archive_basename",
    "source_archive_sha256",
    "source_inventory_basename",
    "source_inventory_sha256",
    "deb_basename",
    "deb_sha256",
    "acceptance_binding_sha256",
    "acceptance_tools_manifest_sha256",
    "acceptance_entrypoint_sha256",
    "installed_release_manifest_sha256",
    "maintainer",
    "compatibility_policy_id",
    "compatibility_policy_sha256",
    "upgrade_data_contract_id",
    "upgrade_data_contract_sha256",
    "elf_abi_audit_basename",
    "elf_abi_audit_sha256",
    "python_dependency_lock_status",
    "python_lock_basename",
    "python_lock_sha256",
    "python_version",
    "python_archive_sha256",
    "python_executable_sha256",
    "uv_version",
    "uv_archive_sha256",
    "uv_executable_sha256",
    "node_version",
    "node_archive_sha256",
    "node_executable_sha256",
    "electron_version",
    "electron_archive_sha256",
    "electron_executable_sha256",
    "desktop_entry_sha256",
    "icon_set_sha256",
    "built_at_utc",
    "formal_build_tests_status",
    "formal_build_tests_log_basename",
    "formal_build_tests_log_sha256",
}
ELECTRON_PATH = "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
DRIVER_RESULT_BASENAME = "desktop-driver-result.json"
SCREENSHOT_BASENAME = "desktop-app.png"
DIAGNOSTIC_BASENAME = "taiji-support-bundle.json"
INSTALL_OBSERVATION_BASENAME = "single-deb-install-observation.json"
INSTALL_METHOD_ATTESTATION_BASENAME = "single-deb-install-method-attestation.json"
GRAPHICAL_INSTALLER_EVIDENCE_BASENAME = "single-deb-graphical-installer.png"
_CHALLENGE_TIME_WINDOW = contextvars.ContextVar(
    "taiji_challenge_time_window",
    default=None,
)
_SIGNED_EVIDENCE_REFERENCE_TIME = contextvars.ContextVar(
    "taiji_signed_evidence_reference_time",
    default=None,
)
_CHALLENGE_HELPER = None
TARGET_CHECK_KEYS = {
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
CANONICAL_ENVIRONMENT_OBSERVATION_KEYS = {
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
CANONICAL_INSTALL_OBSERVATION_KEYS = {
    "schema",
    "generated_at_utc",
    "started_at_utc",
    "completed_at_utc",
    "challenge_nonce",
    "machine_identity_commitment_sha256",
    "machine_fingerprint_sha256",
    "boot_fingerprint_sha256",
    "target_uid",
    "canonical_home_fingerprint_sha256",
    "user_state_paths_fingerprint_sha256",
    "source_commit",
    "manifest_sha256",
    "deb_observed_basename",
    "deb_sha256",
    "candidate_file_count",
    "additional_install_files_observed",
    "package_status_before",
    "package_status_after",
    "package_status_transitions",
    "network_observation",
    "network_sample_interval_ms",
    "network_sample_count",
    "user_state_before",
    "user_state_after_install_before_first_launch",
    "first_launch_eligible",
    "installation_method_machine_observed",
    "observation_process_continuous",
}
POSITIVE_CERTIFICATION_ATTACHMENT_BASENAMES = {
    "target-verification.json",
    "environment-observation.json",
    "single-deb-install-observation.json",
    "single-deb-install-method-attestation.json",
    "single-deb-graphical-installer.png",
    "desktop-driver-result.json",
    "desktop-app.png",
    "taiji-support-bundle.json",
}
DESKTOP_AUTH_COOKIE_KEYS = {
    "name",
    "present",
    "http_only",
    "same_site",
    "path",
    "value_format",
}

OFFLINE_KEYS = {
    "schema_version",
    "evidence_type",
    "generated_at_utc",
    "rehearsal_session_id",
    "challenge_nonce",
    "release_artifacts_sha256",
    "target_baseline_profile_id",
    "target_baseline_sha256",
    "source_commit",
    "deb_basename",
    "deb_sha256",
    "platform",
    "environment",
    "os_id",
    "os_version",
    "network",
    "install",
    "uninstall",
    "reinstall",
    "desktop_app_verified",
    "target_verified",
    "log_basename",
    "log_sha256",
}

OFFLINE_V1_KEYS = {
    "schema",
    "status",
    "generated_at_utc",
    "rehearsal_session_id",
    "challenge_nonce",
    "source_commit",
    "version",
    "architecture",
    "deb_basename",
    "deb_sha256",
    "compatibility_policy_id",
    "compatibility_policy_sha256",
    "delivery_inventory_sha256",
    "platform",
    "environment",
    "os_id",
    "os_version",
    "network",
    "checks",
    "desktop_app_verified",
    "target_verified",
    "log_basename",
    "log_sha256",
}
OFFLINE_V1_LIFECYCLE_KEYS = {
    "steps",
    "receipts",
    "data_manifests",
    "journal",
    "package_actions",
    "previous_release",
    "previous_deb_basename",
    "previous_deb_sha256",
    "previous_version",
    "previous_signature_basename",
    "previous_signature_sha256",
    "previous_signature_verification",
    "lifecycle_log_basename",
    "lifecycle_log_sha256",
}
OFFLINE_PREVIOUS_RELEASE_KEYS = {
    "source_commit",
    "version",
    "deb_basename",
    "deb_sha256",
    "checksum_basename",
    "checksum_sha256",
    "signature_basename",
    "signature_sha256",
    "signature_verification",
    "manifest_basename",
    "manifest_sha256",
    "compatibility_policy_id",
    "compatibility_policy_sha256",
}
OFFLINE_LIFECYCLE_STEPS = [
    "fresh_install_n",
    "same_version_reinstall_n",
    "seed_n_minus_one",
    "upgrade_n_minus_one_to_n",
    "data_manifest_after_upgrade",
    "inject_postinst_failure_same_candidate",
    "automatic_rollback_to_n_minus_one",
    "upgrade_n_again",
    "remove_preserves_user_data",
    "purge_clears_root_state_only",
]

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

OFFLINE_SESSION_KEYS = {
    "schema",
    "generated_at_utc",
    "rehearsal_session_id",
    "challenge_nonce",
    "source_commit",
    "deb_basename",
    "deb_sha256",
    "platform",
    "environment",
    "os_id",
    "os_version",
    "network",
    "checks",
    "desktop_app_verified",
    "target_verified",
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


class EvidenceError(ValueError):
    pass


def _load_challenge_helper() -> Any:
    global _CHALLENGE_HELPER
    if _CHALLENGE_HELPER is not None:
        return _CHALLENGE_HELPER
    candidates = (
        Path(__file__).resolve().with_name("taiji-challenge-envelope.py"),
        Path(__file__).resolve().parents[1] / "scripts/taiji-challenge-envelope.py",
    )
    for path in dict.fromkeys(candidates):
        if not path.is_file() or path.is_symlink():
            continue
        spec = importlib.util.spec_from_file_location(
            "taiji_release_challenge_envelope",
            path,
        )
        if spec is None or spec.loader is None:
            raise EvidenceError("cannot load challenge-envelope helper")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CHALLENGE_HELPER = module
        return module
    raise EvidenceError("challenge-envelope helper is missing")


@dataclass(frozen=True)
class BuildBinding:
    """Immutable v3 identity of the exact candidate DEB under review."""

    # Qualify the built-in type names so this module remains importable by
    # legacy importlib callers that do not register the module in sys.modules.
    # This matters on Python 3.14 when postponed annotations are inspected by
    # dataclasses; typing.get_type_hints still resolves these to ``str``.
    source_commit: builtins.str
    version: builtins.str
    architecture: builtins.str
    deb_basename: builtins.str
    deb_sha256: builtins.str
    compatibility_policy_id: builtins.str
    compatibility_policy_sha256: builtins.str
    electron_executable_sha256: builtins.str
    desktop_entry_sha256: builtins.str
    python_dependency_lock_status: builtins.str = ""
    python_lock_basename: builtins.str = ""
    python_lock_sha256: builtins.str = ""
    python_version: builtins.str = ""
    python_archive_sha256: builtins.str = ""
    python_executable_sha256: builtins.str = ""
    uv_version: builtins.str = ""
    uv_archive_sha256: builtins.str = ""
    uv_executable_sha256: builtins.str = ""
    node_version: builtins.str = ""
    node_archive_sha256: builtins.str = ""
    node_executable_sha256: builtins.str = ""
    electron_version: builtins.str = ""
    electron_archive_sha256: builtins.str = ""
    source_archive_basename: builtins.str = ""
    source_archive_sha256: builtins.str = ""
    source_inventory_basename: builtins.str = ""
    source_inventory_sha256: builtins.str = ""
    source_checksums_sha256: builtins.str = ""
    build_marker_sha256: builtins.str = ""
    delivery_inventory_sha256: builtins.str = ""


class DeliveryInventorySnapshot:
    """One internally consistent, path-bound view of an offline delivery."""

    __slots__ = (
        "digest",
        "paths",
        "file_hashes",
        "file_identities",
        "directory_identities",
        "directory_entries",
        "control_payloads",
        "manifest",
        "marker",
        "formal_build_test_digest",
    )

    def __init__(
        self,
        *,
        digest: str,
        paths: set[str],
        file_hashes: dict[str, str],
        file_identities: dict[str, tuple[int, ...]],
        directory_identities: dict[str, tuple[int, ...]],
        directory_entries: dict[str, tuple[str, ...]],
        control_payloads: dict[str, bytes],
        manifest: dict[str, Any],
        marker: dict[str, str],
        formal_build_test_digest: str,
    ) -> None:
        self.digest = digest
        self.paths = frozenset(paths)
        self.file_hashes = dict(file_hashes)
        self.file_identities = dict(file_identities)
        self.directory_identities = dict(directory_identities)
        self.directory_entries = dict(directory_entries)
        self.control_payloads = dict(control_payloads)
        self.manifest = dict(manifest)
        self.marker = dict(marker)
        self.formal_build_test_digest = formal_build_test_digest


@dataclass(frozen=True)
class ValidatedPngEvidence:
    """Digest and semantic identity produced by one stable PNG descriptor."""

    sha256: builtins.str
    size: builtins.int
    width: builtins.int
    height: builtins.int
    color_type: builtins.int


def canonical_policy_identity() -> tuple[str, str]:
    """Return the checked-in policy identity, with a delivery-copy fallback.

    The validator is copied into the offline delivery directory for target
    verification, where the source checkout is intentionally absent.  In the
    source checkout we always use the canonical policy helper and bytes; the
    fallback is the immutable identity of that same checked-in contract.
    """

    if CANONICAL_POLICY_PATH.is_file() and CANONICAL_POLICY_HELPER_PATH.is_file():
        try:
            spec = importlib.util.spec_from_file_location(
                "taiji_release_evidence_compatibility_policy",
                CANONICAL_POLICY_HELPER_PATH,
            )
            if spec is None or spec.loader is None:
                raise EvidenceError("无法加载 canonical compatibility policy helper")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            policy = helper.load_and_validate(CANONICAL_POLICY_PATH)
            return policy["policy_id"], helper.canonical_sha256(policy)
        except (EvidenceError, OSError, KeyError, TypeError, ValueError) as exc:
            raise EvidenceError(f"canonical compatibility policy 无法验证: {exc}") from exc
    return CANONICAL_POLICY_ID, CANONICAL_POLICY_SHA256


def validate_manifest_toolchain_identity(manifest: dict[str, Any]) -> dict[str, str]:
    missing = sorted(TOOLCHAIN_MANIFEST_FIELDS - manifest.keys())
    if missing:
        raise EvidenceError(
            "当前 v3 manifest 缺少正式工具链身份字段: " + ", ".join(missing)
        )
    expected = {
        "python_dependency_lock_status": "strict-locked",
        "python_lock_basename": "uv.lock",
        "python_version": PINNED_PYTHON_VERSION,
        "python_archive_sha256": PINNED_PYTHON_ARCHIVE_SHA256,
        "python_executable_sha256": PINNED_PYTHON_EXECUTABLE_SHA256,
        "uv_version": PINNED_UV_VERSION,
        "uv_archive_sha256": PINNED_UV_ARCHIVE_SHA256,
        "uv_executable_sha256": PINNED_UV_EXECUTABLE_SHA256,
        "node_version": PINNED_NODE_VERSION,
        "node_archive_sha256": PINNED_NODE_ARCHIVE_SHA256,
        "node_executable_sha256": PINNED_NODE_EXECUTABLE_SHA256,
        "electron_version": PINNED_ELECTRON_VERSION,
        "electron_archive_sha256": PINNED_ELECTRON_ARCHIVE_SHA256,
        "electron_executable_sha256": PINNED_ELECTRON_EXECUTABLE_SHA256,
    }
    for key, value in expected.items():
        require_exact(manifest, key, value)
    for key in (
        "python_lock_sha256",
        "python_archive_sha256",
        "python_executable_sha256",
        "uv_archive_sha256",
        "uv_executable_sha256",
        "node_archive_sha256",
        "node_executable_sha256",
        "electron_archive_sha256",
        "electron_executable_sha256",
    ):
        validate_sha256(manifest.get(key), key)
    return {key: manifest[key] for key in TOOLCHAIN_MANIFEST_FIELDS}


def validate_manifest_acceptance_identity(manifest: dict[str, Any]) -> dict[str, str]:
    missing = sorted(ACCEPTANCE_MANIFEST_FIELDS - manifest.keys())
    if missing:
        raise EvidenceError(
            "当前 v3 manifest 缺少安装态验收信任根字段: " + ", ".join(missing)
        )
    result = {}
    for key in ACCEPTANCE_MANIFEST_FIELDS:
        result[key] = validate_sha256(manifest.get(key), key)
    return result


def reject_target_baseline_fields(data: dict[str, Any], label: str) -> None:
    forbidden = {"target_baseline_profile_id", "target_baseline_sha256"}
    present = sorted(forbidden.intersection(data))
    if present:
        raise EvidenceError(
            f"{label} 属于当前 v3 发布路径，禁止 target baseline 字段: {', '.join(present)}"
        )


def require_trusted_ancestor_chain(directory: Path, label: str) -> None:
    current = Path(os.path.abspath(directory))
    while True:
        try:
            current_stat = current.lstat()
        except OSError as exc:
            raise EvidenceError(f"{label} 祖先目录不可读取: {current}: {exc}") from exc
        if stat.S_ISLNK(current_stat.st_mode):
            if current_stat.st_uid != 0:
                raise EvidenceError(f"{label} 不能经过非 root 所有的祖先符号链接: {current}")
        elif not stat.S_ISDIR(current_stat.st_mode):
            raise EvidenceError(f"{label} 祖先路径不是目录: {current}")
        if current == current.parent:
            break
        current = current.parent


def object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"JSON 含重复字段: {key}")
        result[key] = value
    return result


def require_safe_parent(path: Path, label: str) -> None:
    parent = path.parent
    require_trusted_ancestor_chain(parent, label)
    try:
        parent_mode = parent.lstat().st_mode
    except OSError as exc:
        raise EvidenceError(f"{label} 父目录不可读取: {parent}: {exc}") from exc
    if not stat.S_ISDIR(parent_mode) or parent.is_symlink():
        raise EvidenceError(f"{label} 父目录必须是真实目录，不能是符号链接: {parent}")


def open_regular(path: Path, label: str, *, single_link: bool = True) -> tuple[int, os.stat_result]:
    require_safe_parent(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        parent_descriptor = os.open(path.parent, parent_flags)
    except OSError as exc:
        raise EvidenceError(f"{label} 父目录不可安全打开: {path.parent}: {exc}") from exc
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise EvidenceError(f"{label} 不可安全打开: {path}: {exc}") from exc
    finally:
        os.close(parent_descriptor)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise EvidenceError(f"{label} 必须是普通文件: {path}")
        if file_stat.st_size <= 0:
            raise EvidenceError(f"{label} 不能为空: {path}")
        if single_link and file_stat.st_nlink != 1:
            raise EvidenceError(f"{label} 不能是硬链接文件: {path}")
        return descriptor, file_stat
    except Exception:
        os.close(descriptor)
        raise


def regular_file_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    """Return the metadata that must stay stable for one accepted file snapshot."""

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


def resolve_trusted_openssl() -> str:
    """Resolve only the root-managed fixed OpenSSL verification executable."""

    for directory in (Path("/usr"), Path("/usr/bin")):
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise EvidenceError(f"可信 openssl 目录不可读取: {directory}: {exc}") from exc
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
        ):
            raise EvidenceError(f"可信 openssl 目录不是 root 管理的只读系统目录: {directory}")

    try:
        alias = TRUSTED_OPENSSL.lstat()
    except OSError as exc:
        raise EvidenceError(f"缺少固定可信 openssl: {TRUSTED_OPENSSL}: {exc}") from exc
    if alias.st_uid != 0 or alias.st_nlink != 1:
        raise EvidenceError(f"固定 openssl 必须是 root-owned 单链接入口: {TRUSTED_OPENSSL}")
    if not (stat.S_ISREG(alias.st_mode) or stat.S_ISLNK(alias.st_mode)):
        raise EvidenceError(f"固定 openssl 入口类型不可信: {TRUSTED_OPENSSL}")
    if stat.S_ISREG(alias.st_mode) and alias.st_mode & 0o022:
        raise EvidenceError(f"固定 openssl 可被组或其他用户写入: {TRUSTED_OPENSSL}")
    try:
        resolved = TRUSTED_OPENSSL.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except (OSError, RuntimeError) as exc:
        raise EvidenceError(f"固定 openssl 无法解析到可信实体: {exc}") from exc
    if resolved.parent != Path("/usr/bin"):
        raise EvidenceError(f"固定 openssl 解析后逃离 /usr/bin: {resolved}")
    if (
        resolved.is_symlink()
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or resolved_metadata.st_nlink != 1
        or not resolved_metadata.st_mode & 0o111
        or resolved_metadata.st_mode & 0o022
    ):
        raise EvidenceError(f"固定 openssl 实体不是 root 管理的单链接可执行普通文件: {resolved}")
    return str(resolved)


def resolve_trusted_system_python() -> str:
    """Resolve the root-managed system Python used by offline helper subprocesses."""

    try:
        alias = TRUSTED_SYSTEM_PYTHON.lstat()
        resolved = TRUSTED_SYSTEM_PYTHON.resolve(strict=True)
        resolved_stat = resolved.lstat()
    except (OSError, RuntimeError) as exc:
        raise EvidenceError(f"缺少固定可信系统 Python: {exc}") from exc
    if (
        alias.st_uid != 0
        or alias.st_mode & 0o022
        or not (stat.S_ISREG(alias.st_mode) or stat.S_ISLNK(alias.st_mode))
        or resolved.parent != Path("/usr/bin")
        or resolved.is_symlink()
        or not stat.S_ISREG(resolved_stat.st_mode)
        or resolved_stat.st_uid != 0
        or resolved_stat.st_mode & 0o022
        or not resolved_stat.st_mode & 0o111
    ):
        raise EvidenceError("固定系统 Python 不是 /usr/bin 下 root 管理的可执行实体")
    return str(TRUSTED_SYSTEM_PYTHON)


def open_snapshot_regular(
    path: Path,
    label: str,
    expected_sha256: str,
    expected_identity: tuple[int, ...],
) -> tuple[int, os.stat_result]:
    """Open and hash one file while keeping its verified descriptor alive."""

    descriptor, opened = open_regular(path, label)
    try:
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise EvidenceError(f"{label} 在 held-FD 摘要期间被截断")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise EvidenceError(f"{label} 在 held-FD 摘要期间增长")
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            regular_file_identity(opened) != expected_identity
            or regular_file_identity(current) != expected_identity
            or regular_file_identity(opened) != regular_file_identity(after)
            or digest.hexdigest() != expected_sha256
        ):
            raise EvidenceError(f"{label} 与交付清单初始快照不一致")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def inherited_descriptor_path(descriptor: int) -> str:
    if Path("/proc/self/fd").is_dir():
        return f"/proc/self/fd/{descriptor}"
    return f"/dev/fd/{descriptor}"


def run_source_integrity_helper_snapshot(
    delivery_dir: Path,
    source_relative: str,
    inventory_relative: str,
    file_hashes: dict[str, str],
    file_identities: dict[str, tuple[int, ...]],
) -> None:
    """Run the reviewed helper against the same held files hashed by inventory."""

    members = (
        ("source-archive-integrity.py", "源码归档完整性工具"),
        (source_relative, "当前源码包"),
        (inventory_relative, "当前源码成员清单"),
    )
    descriptors: list[int] = []
    try:
        for relative, label in members:
            descriptor, _ = open_snapshot_regular(
                delivery_dir / relative,
                label,
                file_hashes[relative],
                file_identities[relative],
            )
            descriptors.append(descriptor)
        helper_path = inherited_descriptor_path(descriptors[0])
        python = resolve_trusted_system_python()
        with tempfile.TemporaryDirectory(prefix="taiji-validator-helper-", dir="/tmp") as home:
            os.chmod(home, 0o700)
            helper_result = subprocess.run(
                [
                    python,
                    "-I",
                    "-B",
                    helper_path,
                    "verify",
                    "--archive-fd",
                    str(descriptors[1]),
                    "--archive-basename",
                    Path(source_relative).name,
                    "--inventory-fd",
                    str(descriptors[2]),
                ],
                cwd=home,
                env={
                    "HOME": home,
                    "TMPDIR": home,
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                },
                pass_fds=tuple(descriptors),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        if helper_result.returncode != 0:
            detail = (helper_result.stderr or helper_result.stdout).strip()
            raise EvidenceError(
                "源码包与 archive-derived 成员清单不一致"
                + (f": {detail}" if detail else "")
            )
        for descriptor, (relative, label) in zip(descriptors, members):
            opened = os.fstat(descriptor)
            current = (delivery_dir / relative).lstat()
            if (
                regular_file_identity(opened) != file_identities[relative]
                or regular_file_identity(current) != file_identities[relative]
            ):
                raise EvidenceError(f"{label} 在隔离 helper 执行期间发生变化")
    finally:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass


def read_regular_bytes(path: Path, label: str, *, limit: int = MAX_JSON_BYTES) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"{label} 不可读取: {path}: {exc}") from exc
    descriptor, file_stat = open_regular(path, label)
    try:
        if regular_file_identity(before) != regular_file_identity(file_stat):
            raise EvidenceError(f"{label} 在打开前发生变化: {path}")
        if file_stat.st_size > limit:
            raise EvidenceError(f"{label} 超过大小上限 {limit}: {path}")
        chunks: list[bytes] = []
        remaining = file_stat.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != file_stat.st_size:
            raise EvidenceError(f"{label} 读取期间发生变化: {path}")
        if os.read(descriptor, 1):
            raise EvidenceError(f"{label} 读取期间增长: {path}")
        after = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError as exc:
            raise EvidenceError(f"{label} 读回期间丢失: {path}: {exc}") from exc
        if (
            regular_file_identity(file_stat) != regular_file_identity(after)
            or regular_file_identity(file_stat) != regular_file_identity(current)
        ):
            raise EvidenceError(f"{label} 读取期间身份发生变化: {path}")
        return payload, file_stat
    except OSError as exc:
        raise EvidenceError(f"{label} 读取失败: {path}: {exc}") from exc
    finally:
        os.close(descriptor)


def sha256_regular_file(path: Path, label: str) -> tuple[str, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"{label} 不可读取: {path}: {exc}") from exc
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
            raise EvidenceError(f"{label} 摘要计算期间发生变化: {path}")
        after = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError as exc:
            raise EvidenceError(f"{label} 摘要期间丢失: {path}: {exc}") from exc
        if (
            regular_file_identity(before) != regular_file_identity(file_stat)
            or regular_file_identity(file_stat) != regular_file_identity(after)
            or regular_file_identity(file_stat) != regular_file_identity(current)
        ):
            raise EvidenceError(f"{label} 摘要计算期间身份发生变化: {path}")
        return digest.hexdigest(), file_stat
    finally:
        os.close(descriptor)


def sha256_bounded_stable_regular_file(
    path: Path,
    label: str,
    *,
    limit: int,
) -> tuple[str, os.stat_result]:
    """Hash one bounded file through a single descriptor and stable identity."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"{label} 不可读取: {path}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise EvidenceError(f"{label} 必须是单链接普通文件: {path}")

    descriptor, opened = open_regular(path, label)
    try:
        if regular_file_identity(before) != regular_file_identity(opened):
            raise EvidenceError(f"{label} 在打开前发生变化: {path}")
        if opened.st_size > limit:
            raise EvidenceError(f"{label} 超过大小上限 {limit}: {path}")

        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise EvidenceError(f"{label} 在摘要计算期间被截断: {path}")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise EvidenceError(f"{label} 在摘要计算期间增长: {path}")

        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            regular_file_identity(opened) != regular_file_identity(after)
            or regular_file_identity(opened) != regular_file_identity(current)
        ):
            raise EvidenceError(f"{label} 在摘要计算期间发生变化: {path}")
        return digest.hexdigest(), opened
    except OSError as exc:
        raise EvidenceError(f"{label} 流式摘要失败: {path}: {exc}") from exc
    finally:
        os.close(descriptor)


def sha256_regular_tar_member(
    path: Path,
    member_name: str,
    label: str,
    *,
    expected_sha256: str = "",
    expected_identity: tuple[int, ...] = (),
) -> str:
    if expected_sha256 and expected_identity:
        descriptor, before = open_snapshot_regular(
            path,
            label,
            expected_sha256,
            expected_identity,
        )
    else:
        descriptor, before = open_regular(path, label)
    digest = hashlib.sha256()
    total = 0
    try:
        try:
            with os.fdopen(os.dup(descriptor), "rb") as archive_file:
                with tarfile.open(fileobj=archive_file, mode="r:gz") as archive:
                    matches = [member for member in archive.getmembers() if member.name == member_name]
                    if len(matches) != 1:
                        raise EvidenceError(f"{label} 必须且只能包含一个 {member_name}")
                    member = matches[0]
                    if not member.isfile() or member.size <= 0 or member.size > MAX_EVIDENCE_BYTES:
                        raise EvidenceError(f"{label} 中的 {member_name} 不是安全普通文件")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise EvidenceError(f"{label} 中的 {member_name} 无法读取")
                    while True:
                        chunk = extracted.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > member.size:
                            raise EvidenceError(f"{label} 中的 {member_name} 读取越界")
                        digest.update(chunk)
                    if total != member.size:
                        raise EvidenceError(f"{label} 中的 {member_name} 读取不完整")
        except tarfile.TarError as exc:
            raise EvidenceError(f"{label} 不是可验证的 tar.gz 归档: {exc}") from exc
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        try:
            current = path.lstat()
        except OSError as exc:
            raise EvidenceError(f"{label} 在成员摘要计算期间丢失: {exc}") from exc
        if (
            identity_before != identity_after
            or (
                expected_identity
                and regular_file_identity(after) != expected_identity
            )
            or regular_file_identity(after) != regular_file_identity(current)
        ):
            raise EvidenceError(f"{label} 在成员摘要计算期间发生变化")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def canonical_delivery_inventory_digest(
    directory_identities: dict[str, tuple[int, ...]],
    file_hashes: dict[str, str],
) -> str:
    canonical = hashlib.sha256()
    records = [("D", relative, "") for relative in directory_identities]
    records.extend(("F", relative, digest) for relative, digest in file_hashes.items())
    for kind, relative, digest in sorted(records):
        canonical.update(kind.encode("ascii"))
        canonical.update(b"\0")
        canonical.update(relative.encode("utf-8"))
        canonical.update(b"\0")
        if kind == "F":
            canonical.update(digest.encode("ascii"))
            canonical.update(b"\0")
    return canonical.hexdigest()


def _revalidate_delivery_snapshot(
    delivery_dir: Path,
    snapshot: DeliveryInventorySnapshot,
) -> None:
    """Reject every path, identity, entry-set, or content drift from a snapshot."""

    require_trusted_ancestor_chain(delivery_dir, "交付目录")
    for relative, expected_identity in snapshot.directory_identities.items():
        directory = delivery_dir if relative == "." else delivery_dir / relative
        try:
            current = directory.lstat()
        except OSError as exc:
            raise EvidenceError(f"交付目录快照目录丢失: {relative}: {exc}") from exc
        if (
            not stat.S_ISDIR(current.st_mode)
            or directory.is_symlink()
            or regular_file_identity(current) != expected_identity
        ):
            raise EvidenceError(f"交付目录快照目录身份发生变化: {relative}")
        try:
            with os.scandir(directory) as entries:
                current_entries = [entry.name for entry in entries]
        except OSError as exc:
            raise EvidenceError(f"交付目录快照无法复核目录项: {relative}: {exc}") from exc
        if relative == ".":
            current_entries = [
                name
                for name in current_entries
                if name not in DELIVERY_INVENTORY_EXCLUDED_TOP_LEVEL
                and name not in DELIVERY_INVENTORY_EXCLUDED_ROOT_FILES
            ]
        if tuple(sorted(current_entries)) != snapshot.directory_entries[relative]:
            raise EvidenceError(f"交付目录快照的精确目录项发生变化: {relative}")

    for relative, expected_identity in snapshot.file_identities.items():
        digest, current = sha256_regular_file(
            delivery_dir / relative,
            f"交付目录快照文件 {relative}",
        )
        if (
            regular_file_identity(current) != expected_identity
            or digest != snapshot.file_hashes[relative]
        ):
            raise EvidenceError(f"交付目录快照文件身份或内容发生变化: {relative}")

    if set(snapshot.file_identities) != set(snapshot.paths):
        raise EvidenceError("交付目录快照文件集合内部不一致")
    try:
        final_root = delivery_dir.lstat()
    except OSError as exc:
        raise EvidenceError(f"交付目录快照 root 最终复核失败: {exc}") from exc
    if (
        not stat.S_ISDIR(final_root.st_mode)
        or delivery_dir.is_symlink()
        or regular_file_identity(final_root) != snapshot.directory_identities["."]
    ):
        raise EvidenceError("交付目录快照 root 在闭包期间发生变化")
    if (
        canonical_delivery_inventory_digest(
            snapshot.directory_identities,
            snapshot.file_hashes,
        )
        != snapshot.digest
    ):
        raise EvidenceError("交付目录快照 canonical 摘要内部不一致")


def _delivery_inventory_snapshot(delivery_dir: Path) -> DeliveryInventorySnapshot:
    required_relative = {
        "00_制包机_生成离线交付包.sh",
        "01_制包机_发布预检.sh",
        "02_目标终端_安装并验证.sh",
        "03_目标终端_导出诊断报告.sh",
        "04_目标终端_桌面App验收并导出证据.sh",
        "99_本机_准备制包输入包.sh",
        "SHA256SUMS.txt",
        "source-archive-integrity.py",
        "操作说明.md",
        "版本信息.txt",
        "生成的安装包/.build-success",
        "生成的安装包/formal-build-tests.log",
        "生成的安装包/taiji-package-manifest.json",
        "生成的安装包/构建报告.txt",
        "验收工具/run-installed-electron-acceptance.js",
        "验收工具/assemble-target-evidence.py",
        "验收工具/observe-single-deb-install.py",
        "验收工具/certification-matrix.json",
        "验收工具/assemble-taiji-certification-set.py",
        "验收工具/validate-taiji-release-evidence.py",
        "验收工具/taiji-challenge-envelope.py",
        "验收工具/signing-public.pem",
    }
    require_trusted_ancestor_chain(delivery_dir, "交付目录")
    try:
        root_stat = delivery_dir.lstat()
    except OSError as exc:
        raise EvidenceError(f"交付目录不可读取: {delivery_dir}: {exc}") from exc
    root_mode = root_stat.st_mode
    if not stat.S_ISDIR(root_mode) or delivery_dir.is_symlink():
        raise EvidenceError("交付目录必须是真实目录，不能是符号链接")
    root_permissions = stat.S_IMODE(root_mode)
    if root_permissions & 0o022:
        raise EvidenceError("交付目录不能允许 group/other 写入")

    file_inventory: list[tuple[str, int, str]] = []
    directory_inventory: list[tuple[str, int]] = [(".", root_permissions)]
    file_identities: dict[str, tuple[int, ...]] = {}
    directory_identities: dict[str, tuple[int, ...]] = {
        ".": regular_file_identity(root_stat)
    }
    directory_entries: dict[str, tuple[str, ...]] = {}

    def walk_error(exc: OSError) -> None:
        raise EvidenceError(f"交付目录遍历失败: {exc}") from exc

    for current, directories, filenames in os.walk(
        delivery_dir,
        topdown=True,
        followlinks=False,
        onerror=walk_error,
    ):
        current_path = Path(current)
        current_relative = (
            "." if current_path == delivery_dir else current_path.relative_to(delivery_dir).as_posix()
        )
        if current_path == delivery_dir:
            directories[:] = [
                name
                for name in directories
                if name not in DELIVERY_INVENTORY_EXCLUDED_TOP_LEVEL
            ]
            filenames[:] = [
                name
                for name in filenames
                if name not in DELIVERY_INVENTORY_EXCLUDED_ROOT_FILES
            ]
        directory_entries[current_relative] = tuple(sorted(directories + filenames))
        for directory in directories:
            directory_path = current_path / directory
            directory_stat = directory_path.lstat()
            mode = directory_stat.st_mode
            if not stat.S_ISDIR(mode) or directory_path.is_symlink():
                raise EvidenceError(f"交付目录含不安全目录节点: {directory_path}")
            permissions = stat.S_IMODE(mode)
            if permissions & 0o022:
                raise EvidenceError(f"交付目录节点不能允许 group/other 写入: {directory_path}")
            relative_directory = directory_path.relative_to(delivery_dir).as_posix()
            directory_inventory.append((relative_directory, permissions))
            directory_identities[relative_directory] = regular_file_identity(directory_stat)
        for filename in filenames:
            file_path = current_path / filename
            relative = file_path.relative_to(delivery_dir).as_posix()
            digest, file_stat = sha256_regular_file(file_path, f"交付文件 {relative}")
            permissions = stat.S_IMODE(file_stat.st_mode)
            if permissions & 0o022:
                raise EvidenceError(f"交付文件不能允许 group/other 写入: {file_path}")
            file_inventory.append((relative, permissions, digest))
            file_identities[relative] = regular_file_identity(file_stat)
    file_inventory.sort()
    directory_inventory.sort()
    paths = {relative for relative, _mode, _digest in file_inventory}
    file_hashes = {relative: digest for relative, _mode, digest in file_inventory}
    control_payloads: dict[str, bytes] = {}

    def read_inventory_payload(relative: str, label: str, *, limit: int = MAX_JSON_BYTES) -> bytes:
        payload, opened = read_regular_bytes(delivery_dir / relative, label, limit=limit)
        if (
            relative not in file_identities
            or regular_file_identity(opened) != file_identities[relative]
            or hashlib.sha256(payload).hexdigest() != file_hashes.get(relative)
        ):
            raise EvidenceError(f"{label} 与交付清单初始快照不一致")
        control_payloads[relative] = payload
        return payload
    missing = sorted(required_relative - paths)
    if missing:
        raise EvidenceError(f"交付清单缺少必需文件: {', '.join(missing)}")
    manifest_path = delivery_dir / "生成的安装包/taiji-package-manifest.json"
    manifest_bytes = read_inventory_payload(
        "生成的安装包/taiji-package-manifest.json",
        "交付清单 package manifest",
    )
    manifest = parse_json_bytes(manifest_bytes, "交付清单 package manifest")

    marker: dict[str, str] = {}
    formal_build_test_digest = ""
    if manifest.get("schema") == "taiji-package-manifest/v3":
        reject_target_baseline_fields(manifest, "发布 manifest")
        expected_policy_id, expected_policy_sha256 = canonical_policy_identity()
        if (
            manifest.get("compatibility_policy_id") != expected_policy_id
            or manifest.get("compatibility_policy_sha256") != expected_policy_sha256
        ):
            raise EvidenceError(
                "发布 manifest compatibility policy 与当前 canonical policy 不一致"
            )
        toolchain = validate_manifest_toolchain_identity(manifest)
        acceptance = validate_manifest_acceptance_identity(manifest)
        marker_payload = read_inventory_payload(
            "生成的安装包/.build-success",
            "构建成功标记",
        )
        marker = parse_marker_bytes(marker_payload)
        formal_log_payload = read_inventory_payload(
            "生成的安装包/formal-build-tests.log",
            "正式构建测试日志",
            limit=MAX_EVIDENCE_BYTES,
        )
        formal_build_test_digest = validate_formal_build_test_payloads(
            manifest,
            marker,
            formal_log_payload,
        )
        source_commit = manifest.get("source_commit")
        if not isinstance(source_commit, str) or not FULL_COMMIT_RE.fullmatch(source_commit):
            raise EvidenceError("v3 交付清单 manifest source_commit 不合法")
        expected_source_name = f"taiji-agentv1.0-kylin-build-src-{source_commit}.tar.gz"
        expected_inventory_name = (
            f"taiji-agentv1.0-kylin-build-src-{source_commit}.inventory.json"
        )
        source_candidates = sorted(
            relative
            for relative in paths
            if "/" not in relative
            and relative.startswith("taiji-agentv1.0-kylin-build-src-")
            and relative.endswith(".tar.gz")
        )
        if source_candidates != [expected_source_name]:
            raise EvidenceError(
                "v3 交付清单必须且只能包含 manifest source_commit 命名的源码包"
            )
        inventory_candidates = sorted(
            relative
            for relative in paths
            if "/" not in relative
            and relative.startswith("taiji-agentv1.0-kylin-build-src-")
            and relative.endswith(".inventory.json")
        )
        if inventory_candidates != [expected_inventory_name]:
            raise EvidenceError(
                "v3 交付清单必须且只能包含 manifest source_commit 命名的源码成员清单"
            )
        source_hash = file_hashes[expected_source_name]
        source_inventory_hash = file_hashes[expected_inventory_name]
        source_lock_hash = sha256_regular_tar_member(
            delivery_dir / expected_source_name,
            "taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/uv.lock",
            "当前源码包",
            expected_sha256=file_hashes[expected_source_name],
            expected_identity=file_identities[expected_source_name],
        )
        if source_lock_hash != toolchain["python_lock_sha256"]:
            raise EvidenceError("当前源码包 uv.lock SHA256 与正式工具链身份不一致")
        source_sums_payload = read_inventory_payload(
            "SHA256SUMS.txt",
            "根 SHA256SUMS",
        )
        try:
            source_sums_text = source_sums_payload.decode("ascii")
        except UnicodeError as exc:
            raise EvidenceError("根 SHA256SUMS 必须是 ASCII") from exc
        checksum_records: dict[str, str] = {}
        for line in source_sums_text.splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)", line)
            if match is None or match.group(2) in checksum_records:
                raise EvidenceError("根 SHA256SUMS 必须是无重复的两条精确摘要记录")
            checksum_records[match.group(2)] = match.group(1)
        if checksum_records != {
            expected_source_name: source_hash,
            expected_inventory_name: source_inventory_hash,
        }:
            raise EvidenceError("根 SHA256SUMS 未精确绑定唯一源码包与 archive-derived 成员清单")
        helper_path = delivery_dir / "source-archive-integrity.py"
        if file_hashes["source-archive-integrity.py"] != PINNED_SOURCE_INTEGRITY_HELPER_SHA256:
            raise EvidenceError("交付目录源码归档完整性工具不是固定审查版本")
        run_source_integrity_helper_snapshot(
            delivery_dir,
            expected_source_name,
            expected_inventory_name,
            file_hashes,
            file_identities,
        )
        for key, expected in {
            "source_archive_basename": expected_source_name,
            "source_archive_sha256": source_hash,
            "source_inventory_basename": expected_inventory_name,
            "source_inventory_sha256": source_inventory_hash,
        }.items():
            if manifest.get(key) != expected:
                raise EvidenceError(f"v3 交付清单 manifest {key} 与源码归档实物不一致")
        deb_name = manifest.get("deb_basename")
        if not isinstance(deb_name, str) or not re.fullmatch(
            r"taiji-agent_(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)_amd64\.deb",
            deb_name,
        ):
            raise EvidenceError("v3 交付清单 manifest 的 deb_basename 不合法")
        required_v3 = {
            f"生成的安装包/{deb_name}",
            f"生成的安装包/{deb_name}.sha256",
        }
        missing_v3 = sorted(required_v3 - paths)
        if missing_v3:
            raise EvidenceError(f"v3 单 DEB 交付清单缺少文件: {', '.join(missing_v3)}")
        output_debs = sorted(
            relative
            for relative in paths
            if relative.startswith("生成的安装包/") and relative.endswith("_amd64.deb")
        )
        if output_debs != [f"生成的安装包/{deb_name}"]:
            raise EvidenceError("v3 交付清单必须且只能包含 manifest 绑定的单一 DEB")
        deb_hash = file_hashes[f"生成的安装包/{deb_name}"]
        if manifest.get("deb_sha256") != deb_hash:
            raise EvidenceError("v3 交付清单 manifest deb_sha256 与唯一 DEB 不一致")
        version = manifest.get("version")
        if not isinstance(version, str) or deb_name != f"taiji-agent_{version}_amd64.deb":
            raise EvidenceError("v3 交付清单 manifest version 与 DEB basename 不一致")
        read_inventory_payload(
            f"生成的安装包/{deb_name}.sha256",
            "DEB SHA256 sidecar",
        )
        marker_expected = {
            "version": version,
            "source_archive": expected_source_name,
            "source_sha256": source_hash,
            "source_inventory": expected_inventory_name,
            "source_inventory_sha256": source_inventory_hash,
            "source_commit": source_commit,
            "deb": deb_name,
            "deb_sha256": deb_hash,
            "checksum": f"{deb_name}.sha256",
            "manifest": "taiji-package-manifest.json",
            "compatibility_policy_id": manifest.get("compatibility_policy_id"),
            "compatibility_policy_sha256": manifest.get("compatibility_policy_sha256"),
            "elf_abi_audit_sha256": manifest.get("elf_abi_audit_sha256"),
            "icon_set_sha256": manifest.get("icon_set_sha256"),
            "maintainer": manifest.get("maintainer"),
            "formal_build_tests_status": "pass",
            "formal_build_tests_log_basename": FORMAL_BUILD_TEST_LOG_BASENAME,
            "formal_build_tests_log_sha256": formal_build_test_digest,
            **toolchain,
            **acceptance,
        }
        require_exact_keys(marker, set(marker_expected) | {"built_at_utc"}, "构建成功标记")
        if not marker["built_at_utc"].strip():
            raise EvidenceError("构建成功标记 built_at_utc 不能为空")
        for key, expected in marker_expected.items():
            if not isinstance(expected, str) or not expected or marker[key] != expected:
                raise EvidenceError(f"构建成功标记 {key} 与 v3 manifest/当前交付物不一致")
        legacy_entries = sorted(relative for relative in paths if relative.startswith("离线依赖/"))
        if legacy_entries:
            raise EvidenceError("v3 单 DEB 交付清单不能混入遗留离线 APT 仓库")
    else:
        legacy_required = {
            "离线依赖/Packages",
            "离线依赖/Packages.gz",
            "离线依赖/SHA256SUMS.txt",
            "离线依赖/runtime-dependencies.txt",
        }
        missing_legacy = sorted(legacy_required - paths)
        if missing_legacy:
            raise EvidenceError(f"legacy v2 交付清单缺少文件: {', '.join(missing_legacy)}")
        offline_debs = [
            relative
            for relative in paths
            if relative.startswith("离线依赖/") and relative.endswith(".deb")
        ]
        if not offline_debs:
            raise EvidenceError("legacy v2 交付清单未包含离线仓库 DEB")
    source_archives = [
        relative
        for relative in paths
        if re.fullmatch(r"taiji-agentv1\.0-kylin-build-src-[0-9a-f]{7,40}\.tar\.gz", relative)
    ]
    if len(source_archives) != 1:
        raise EvidenceError("交付清单必须且只能包含一个当前源码包")
    snapshot = DeliveryInventorySnapshot(
        digest=canonical_delivery_inventory_digest(directory_identities, file_hashes),
        paths=paths,
        file_hashes=file_hashes,
        file_identities=file_identities,
        directory_identities=directory_identities,
        directory_entries=directory_entries,
        control_payloads=control_payloads,
        manifest=manifest,
        marker=marker,
        formal_build_test_digest=formal_build_test_digest,
    )
    _revalidate_delivery_snapshot(delivery_dir, snapshot)
    return snapshot


def delivery_inventory_sha256(delivery_dir: Path) -> str:
    """Return the canonical digest of one fully closed delivery snapshot."""

    return _delivery_inventory_snapshot(delivery_dir).digest


def parse_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=object_without_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, EvidenceError) as exc:
        raise EvidenceError(f"{label} 无法解析: {exc}") from exc
    if type(data) is not dict:
        raise EvidenceError(f"{label} 顶层必须是 JSON object")
    return data


def load_json(path: Path, label: str) -> dict[str, Any]:
    payload, _ = read_regular_bytes(path, label)
    return parse_json_bytes(payload, label)


def require_exact_keys(data: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - data.keys())
    extra = sorted(data.keys() - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"缺少字段: {', '.join(missing)}")
        if extra:
            details.append(f"未知字段: {', '.join(extra)}")
        raise EvidenceError(f"{label} 字段集合不合法；{'；'.join(details)}")


def require_exact(data: dict[str, Any], key: str, expected: Any) -> None:
    value = data[key]
    if type(value) is not type(expected) or value != expected:
        raise EvidenceError(f"字段 {key} 必须是 {expected!r}")


def require_nonempty_string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if type(value) is not str or not value.strip():
        raise EvidenceError(f"字段 {key} 必须是非空字符串")
    return value


def require_choice(data: dict[str, Any], key: str, choices: set[str]) -> None:
    value = data[key]
    if type(value) is not str or value not in choices:
        raise EvidenceError(f"字段 {key} 只能是 {', '.join(sorted(choices))}")


def validate_sha256(value: Any, key: str) -> str:
    if type(value) is not str or not SHA256_RE.fullmatch(value):
        raise EvidenceError(f"字段 {key} 必须是小写 64 位 SHA256")
    return value


def validate_fresh_timestamp(value: Any, key: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        raise EvidenceError(f"字段 {key} 必须是 UTC ISO8601 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError(f"字段 {key} 必须是 UTC ISO8601 时间") from exc
    challenge_window = _CHALLENGE_TIME_WINDOW.get()
    if challenge_window is not None:
        issued, expires = challenge_window
        if parsed < issued or parsed > expires:
            raise EvidenceError(
                f"字段 {key} 必须落在已签名 challenge envelope 时间窗内"
            )
        reference_time = _SIGNED_EVIDENCE_REFERENCE_TIME.get()
        if reference_time is not None and parsed > reference_time:
            raise EvidenceError(f"字段 {key} 不得晚于顶层签名证据时间")
        return value
    now = datetime.now(timezone.utc)
    if parsed > now + timedelta(minutes=5) or parsed < now - timedelta(days=7):
        raise EvidenceError(f"字段 {key} 必须是最近 7 天内生成的当前证据")
    return value


def validate_session_id(value: Any, key: str) -> str:
    if type(value) is not str or not SESSION_RE.fullmatch(value):
        raise EvidenceError(f"字段 {key} 必须是 32 位小写十六进制会话 ID")
    return value


def validate_challenge(value: Any, expected: str) -> str:
    if not CHALLENGE_RE.fullmatch(expected or ""):
        raise EvidenceError("发布门禁必须提供 64-128 位小写十六进制 challenge")
    if type(value) is not str or value != expected:
        raise EvidenceError("证据 challenge_nonce 与本次发布门禁 challenge 不一致")
    return value


def validate_driver_visible_text(value: Any, label: str, *, maximum: int = 128) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise EvidenceError(f"{label} 必须是长度不超过 {maximum} 的非空字符串")
    if value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EvidenceError(f"{label} 含前后空白或控制字符")
    return value


def validate_driver_pid(value: Any, label: str) -> int:
    if type(value) is not int or value <= 1:
        raise EvidenceError(f"{label} 必须是大于 1 的整数")
    return value


def validate_driver_app_urls(app_url: Any, webui_origin: Any) -> None:
    if type(app_url) is not str or type(webui_origin) is not str:
        raise EvidenceError("桌面驱动 App URL 必须是字符串")
    try:
        app = urlsplit(app_url)
        origin = urlsplit(webui_origin)
        query = parse_qs(app.query, strict_parsing=True, keep_blank_values=True)
    except ValueError as exc:
        raise EvidenceError("桌面驱动 App URL 格式不合法") from exc
    if app.scheme != "http" or app.hostname not in {"127.0.0.1", "localhost"}:
        raise EvidenceError("桌面驱动 app_url 必须是 HTTP loopback URL")
    if app.username or app.password or app.fragment:
        raise EvidenceError("桌面驱动 app_url 含禁止的授权或 fragment 数据")
    if set(query) != {"taiji_desktop"}:
        raise EvidenceError("桌面驱动 app_url 含未知 query 数据")
    if query.get("taiji_desktop") != ["1"]:
        raise EvidenceError("桌面驱动 app_url 未保留唯一桌面标记")
    if origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost"}:
        raise EvidenceError("桌面驱动 webui_origin 必须是 HTTP loopback origin")
    if origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}:
        raise EvidenceError("桌面驱动 webui_origin 不能含认证、query、fragment 或 path")
    if f"{app.scheme}://{app.netloc}" != f"{origin.scheme}://{origin.netloc}":
        raise EvidenceError("桌面驱动 app_url 与 webui_origin 不是同一 App")


def validate_driver_desktop_auth_cookie(cookie: Any) -> None:
    if type(cookie) is not dict:
        raise EvidenceError("桌面驱动 desktop_auth_cookie 必须是 object")
    require_exact_keys(cookie, DESKTOP_AUTH_COOKIE_KEYS, "桌面驱动 desktop_auth_cookie")
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
            raise EvidenceError(f"桌面驱动 desktop_auth_cookie.{key} 不合法")


def validate_target_driver_v2_observations(driver: dict[str, Any]) -> None:
    restart_rounds = driver["restart_rounds"]
    if type(restart_rounds) is not list or len(restart_rounds) != 3:
        raise EvidenceError("桌面 App 驱动 restart_rounds 必须恰好包含三轮")
    for index, round_record in enumerate(restart_rounds, start=1):
        if type(round_record) is not dict:
            raise EvidenceError(f"桌面 App 驱动第 {index} 轮重启记录必须是 object")
        require_exact_keys(
            round_record,
            DRIVER_RESTART_ROUND_KEYS,
            f"桌面 App 驱动第 {index} 轮重启记录",
        )
        if type(round_record["round"]) is not int or round_record["round"] != index:
            raise EvidenceError("桌面 App 驱动重启轮次顺序不合法")
        for key in ("electron_pid", "agent_pid", "web_pid", "secondary_pid"):
            validate_driver_pid(round_record[key], f"driver.restart_rounds[{index}].{key}")
        for key in ("cdp_port", "webui_port"):
            value = round_record[key]
            if type(value) is not int or value < 1024 or value > 65535:
                raise EvidenceError(f"桌面 App 驱动第 {index} 轮 {key} 不合法")
        for key in ("second_instance_exit_code", "electron_exit_code"):
            if type(round_record[key]) is not int or round_record[key] != 0:
                raise EvidenceError(f"桌面 App 驱动第 {index} 轮 {key} 必须是整数零")
        for key in (
            "ready",
            "restored_and_focused",
            "page_close_sent",
            "pidfiles_absent",
            "model_config_observed",
            "profile_continuity_observed",
        ):
            if type(round_record[key]) is not bool or round_record[key] is not True:
                raise EvidenceError(f"桌面 App 驱动第 {index} 轮 {key} 必须为 true")
        process_gone = round_record["process_identities_gone"]
        if type(process_gone) is not dict:
            raise EvidenceError(f"桌面 App 驱动第 {index} 轮进程退出证据不合法")
        require_exact_keys(
            process_gone,
            {"electron", "agent", "webui", "secondary"},
            f"桌面 App 驱动第 {index} 轮进程退出证据",
        )
        if any(type(value) is not bool or value is not True for value in process_gone.values()):
            raise EvidenceError(f"桌面 App 驱动第 {index} 轮遗留了进程身份")
        ports_closed = round_record["ports_closed"]
        if type(ports_closed) is not dict:
            raise EvidenceError(f"桌面 App 驱动第 {index} 轮端口关闭证据不合法")
        require_exact_keys(
            ports_closed,
            {"cdp", "webui"},
            f"桌面 App 驱动第 {index} 轮端口关闭证据",
        )
        if any(type(value) is not bool or value is not True for value in ports_closed.values()):
            raise EvidenceError(f"桌面 App 驱动第 {index} 轮遗留了端口")

    round_one = restart_rounds[0]
    for driver_key, round_key in (
        ("electron_pid", "electron_pid"),
        ("agent_pid", "agent_pid"),
        ("web_pid", "web_pid"),
        ("electron_exit_code", "electron_exit_code"),
    ):
        if driver[driver_key] != round_one[round_key]:
            raise EvidenceError(f"桌面 App 驱动 {driver_key} 不是第一轮的严格别名")
    if urlsplit(driver["app_url"]).port != round_one["webui_port"]:
        raise EvidenceError("桌面 App 驱动 URL 端口与第一轮 WebUI 端口不一致")

    persistent = driver["persistent_user_data"]
    if type(persistent) is not dict:
        raise EvidenceError("桌面 App 驱动 persistent_user_data 必须是 object")
    require_exact_keys(
        persistent,
        DRIVER_PERSISTENT_USER_DATA_KEYS,
        "桌面 App 驱动 persistent_user_data",
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
            raise EvidenceError(f"桌面 App 驱动 persistent_user_data.{key} 不合法")
    validate_sha256(persistent["continuity_token"], "driver.persistent_user_data.continuity_token")

    core = driver["core_observation"]
    if type(core) is not dict:
        raise EvidenceError("桌面 App 驱动 core_observation 必须是 object")
    require_exact_keys(core, DRIVER_CORE_OBSERVATION_KEYS, "桌面 App 驱动 core_observation")
    if core["status"] != "verified" or core["mechanism"] != "journalctl-json-user-electron":
        raise EvidenceError("桌面 App 驱动 Core 观测未完成验证")
    if type(core["baseline_entry_count"]) is not int or core["baseline_entry_count"] < 0:
        raise EvidenceError("桌面 App 驱动 Core 观测基线数量不合法")
    validate_sha256(core["baseline_cursor_set_token"], "driver.core_observation.baseline_cursor_set_token")
    core_rounds = core["rounds"]
    if type(core_rounds) is not list or len(core_rounds) != 3:
        raise EvidenceError("桌面 App 驱动 Core 观测必须覆盖三轮")
    for index, core_round in enumerate(core_rounds, start=1):
        if type(core_round) is not dict:
            raise EvidenceError(f"桌面 App 驱动 Core 第 {index} 轮观测必须是 object")
        require_exact_keys(core_round, DRIVER_CORE_ROUND_KEYS, f"桌面 App 驱动 Core 第 {index} 轮观测")
        if (
            type(core_round["round"]) is not int
            or core_round["round"] != index
            or core_round["status"] != "verified"
            or type(core_round["added_entry_count"]) is not int
            or core_round["added_entry_count"] != 0
        ):
            raise EvidenceError(f"桌面 App 驱动 Core 第 {index} 轮观测不合法")
        validate_sha256(core_round["cursor_set_token"], f"driver.core_observation.rounds[{index}].cursor_set_token")

    model_observation = driver["model_config_observation"]
    if type(model_observation) is not dict:
        raise EvidenceError("桌面 App 驱动 model_config_observation 必须是 object")
    require_exact_keys(
        model_observation,
        DRIVER_MODEL_CONFIG_OBSERVATION_KEYS,
        "桌面 App 驱动 model_config_observation",
    )
    if (
        type(model_observation["observed_rounds"]) is not int
        or model_observation["observed_rounds"] != 3
        or type(model_observation["consistent"]) is not bool
        or model_observation["consistent"] is not True
    ):
        raise EvidenceError("桌面 App 驱动模型配置观测不一致")
    validate_sha256(
        model_observation["public_projection_token"],
        "driver.model_config_observation.public_projection_token",
    )


def validate_attestation(args: argparse.Namespace, evidence_payload: bytes) -> None:
    openssl = resolve_trusted_openssl()
    public_payload, _ = read_regular_bytes(
        args.attestation_public_key,
        "发布证据验签公钥",
        limit=64 * 1024,
    )
    signature_payload, _ = read_regular_bytes(
        args.attestation_signature,
        "发布证据签名",
        limit=64 * 1024,
    )
    expected_fingerprint = validate_sha256(
        args.attestation_public_key_fingerprint,
        "attestation_public_key_fingerprint",
    )
    with tempfile.TemporaryDirectory(prefix="taiji-evidence-verify-") as temp:
        temp_root = Path(temp)
        public_path = temp_root / "public.pem"
        signature_path = temp_root / "evidence.sig"
        public_path.write_bytes(public_payload)
        signature_path.write_bytes(signature_payload)
        derived = subprocess.run(
            [openssl, "pkey", "-pubin", "-in", str(public_path), "-outform", "DER"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if derived.returncode != 0:
            raise EvidenceError("发布证据验签公钥不是有效 PEM 公钥")
        actual_fingerprint = hashlib.sha256(derived.stdout).hexdigest()
        if actual_fingerprint != expected_fingerprint:
            raise EvidenceError("发布证据验签公钥 fingerprint 与产品信任锚不一致")
        verified = subprocess.run(
            [
                openssl,
                "dgst",
                "-sha256",
                "-verify",
                str(public_path),
                "-signature",
                str(signature_path),
            ],
            input=evidence_payload,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if verified.returncode != 0:
            raise EvidenceError("发布证据签名无效；必须由离线发布私钥复核签署")


def verify_with_pinned_release_public_key(
    payload_descriptor: int,
    signature_payload: bytes,
    label: str,
) -> None:
    """Verify a release detached signature from the caller's stable open descriptor."""

    openssl = resolve_trusted_openssl()
    source_key = PINNED_RELEASE_PUBLIC_KEY_PATH
    if not source_key.is_file():
        # The validator is also copied beside the tracked public key in the
        # offline acceptance tools.  The fingerprint below keeps that copy
        # pinned to the same product trust root.
        source_key = Path(__file__).resolve().with_name("signing-public.pem")
    public_payload, _ = read_regular_bytes(
        source_key,
        "固定发布验签公钥",
        limit=64 * 1024,
    )
    with tempfile.TemporaryDirectory(prefix="taiji-previous-release-verify-") as temporary:
        temporary_root = Path(temporary)
        public_path = temporary_root / "signing-public.pem"
        signature_path = temporary_root / "previous-release.deb.sig"
        public_path.write_bytes(public_payload)
        signature_path.write_bytes(signature_payload)
        public_path.chmod(0o600)
        signature_path.chmod(0o600)
        derived = subprocess.run(
            [openssl, "pkey", "-pubin", "-in", str(public_path), "-outform", "DER"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if derived.returncode != 0:
            raise EvidenceError("固定发布验签公钥不是有效 PEM 公钥")
        actual_fingerprint = hashlib.sha256(derived.stdout).hexdigest()
        if actual_fingerprint != PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT:
            raise EvidenceError("固定发布验签公钥 fingerprint 与产品信任锚不一致")
        verified = subprocess.run(
            [
                openssl,
                "dgst",
                "-sha256",
                "-verify",
                str(public_path),
                "-signature",
                str(signature_path),
            ],
            stdin=payload_descriptor,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if verified.returncode != 0:
            raise EvidenceError(f"{label} detached signature 未通过固定发布公钥验证")


def parse_marker_bytes(payload: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise EvidenceError("构建成功标记不是 UTF-8") from exc
    for line in lines:
        if not line or "=" not in line:
            raise EvidenceError(f"构建成功标记含非法行: {line!r}")
        key, value = line.split("=", 1)
        if key in result:
            raise EvidenceError(f"构建成功标记含重复字段: {key}")
        result[key] = value
    return result


def parse_marker(path: Path) -> dict[str, str]:
    payload, _ = read_regular_bytes(path, "构建成功标记")
    return parse_marker_bytes(payload)


def validate_formal_build_test_payloads(
    manifest: dict[str, Any],
    marker: dict[str, str],
    payload: bytes,
) -> str:
    """Validate one already-captured manifest/marker/log snapshot."""

    require_exact_keys(
        manifest,
        PACKAGE_MANIFEST_V3_EXACT_FIELDS,
        "当前 v3 package manifest",
    )
    for field, expected in {
        "schema": PACKAGE_MANIFEST_SCHEMA_V3,
        "package": "taiji-agent",
        "architecture": "amd64",
    }.items():
        require_exact(manifest, field, expected)
    expected_binding = {
        "formal_build_tests_status": "pass",
        "formal_build_tests_log_basename": FORMAL_BUILD_TEST_LOG_BASENAME,
    }
    for field, expected in expected_binding.items():
        if manifest.get(field) != expected or marker.get(field) != expected:
            raise EvidenceError(f"正式构建测试字段 {field} 未精确绑定 PASS 日志")
    digest = validate_sha256(
        manifest.get("formal_build_tests_log_sha256"),
        "formal_build_tests_log_sha256",
    )
    if marker.get("formal_build_tests_log_sha256") != digest:
        raise EvidenceError("正式构建测试日志 SHA256 在 manifest/marker 间不一致")
    if hashlib.sha256(payload).hexdigest() != digest:
        raise EvidenceError("正式构建测试日志内容与绑定 SHA256 不一致")
    if not payload.endswith(b"\n") or b"\r" in payload or b"\0" in payload:
        raise EvidenceError("正式构建测试日志必须是 LF 结尾且不含 CR/NUL 的 canonical 文本")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise EvidenceError("正式构建测试日志不是 UTF-8") from exc
    if not lines or lines[0] != "schema=" + FORMAL_BUILD_TEST_LOG_SCHEMA:
        if lines and lines[0] == "schema=taiji-formal-build-tests/v1":
            raise EvidenceError("正式构建测试日志 v1 是已拒绝的 downgrade")
        raise EvidenceError("正式构建测试日志 schema 不是 exact v2 合同")

    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str) or not FULL_COMMIT_RE.fullmatch(source_commit):
        raise EvidenceError("正式构建测试日志缺少完整 source commit 身份")
    for field, expected in {
        "python_version": PINNED_PYTHON_VERSION,
        "python_executable_sha256": PINNED_PYTHON_EXECUTABLE_SHA256,
        "node_version": PINNED_NODE_VERSION,
        "node_executable_sha256": PINNED_NODE_EXECUTABLE_SHA256,
    }.items():
        if manifest.get(field) != expected:
            raise EvidenceError(f"正式构建测试日志的 {field} 未绑定固定工具链")
    expected_fixed_header = [
        "schema=" + FORMAL_BUILD_TEST_LOG_SCHEMA,
        "source_commit=" + source_commit,
    ]
    if lines[:2] != expected_fixed_header:
        raise EvidenceError("正式构建测试日志 v2 source header 身份不一致")
    index = 2

    def require_header_pattern(pattern: str, label: str) -> None:
        nonlocal index
        if index >= len(lines) or re.fullmatch(pattern, lines[index]) is None:
            raise EvidenceError(f"正式构建测试日志 v2 {label} 不合法")
        index += 1

    legacy_supervisor_header = index < len(lines) and lines[index].startswith(
        "supervisor_source_sha256="
    )
    if legacy_supervisor_header:
        require_header_pattern(
            r"supervisor_source_sha256=[0-9a-f]{64}",
            "supervisor source 身份",
        )
    expected_toolchain_header = [
        "python_version=" + PINNED_PYTHON_VERSION,
        "python_executable_sha256=" + PINNED_PYTHON_EXECUTABLE_SHA256,
        "node_version=" + PINNED_NODE_VERSION,
        "node_executable_sha256=" + PINNED_NODE_EXECUTABLE_SHA256,
        "npm_version=" + PINNED_NPM_VERSION,
        "npm_cli_sha256=" + PINNED_NPM_CLI_SHA256,
    ]
    if lines[index : index + len(expected_toolchain_header)] != expected_toolchain_header:
        raise EvidenceError("正式构建测试日志 v2 固定工具链 header 不一致")
    index += len(expected_toolchain_header)
    require_header_pattern(r"eslint_cli_sha256=[0-9a-f]{64}", "eslint CLI 身份")
    if legacy_supervisor_header:
        require_header_pattern(r"closure_sha256=[0-9a-f]{64}", "closure 身份")
        require_header_pattern(r"closure_file_count=[1-9][0-9]*", "closure 文件数")
        require_header_pattern(r"closure_total_bytes=[1-9][0-9]*", "closure 字节数")
    exact_target_header = [
        "target_count=" + str(FORMAL_BUILD_TEST_TARGET_COUNT),
        "target_contract_sha256=" + FORMAL_BUILD_TEST_TARGET_CONTRACT_SHA256,
    ]
    if lines[index : index + len(exact_target_header)] != exact_target_header:
        raise EvidenceError("正式构建测试日志 v2 target contract header 不一致")
    index += len(exact_target_header)

    def parse_count(value: str, label: str) -> int:
        if re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is None:
            raise EvidenceError(f"正式构建测试 {label} 不是 canonical 非负整数")
        return int(value)

    serialized_targets = bytearray()
    next_ordinal = 0
    for suite in FORMAL_BUILD_TEST_SUITES:
        if index >= len(lines) or lines[index] != "suite_begin=" + suite:
            raise EvidenceError(f"正式构建测试日志缺少有序 suite begin: {suite}")
        index += 1

        seen_channels: set[str] = set()
        last_channel_index = -1
        while index < len(lines) and lines[index].startswith("child_output="):
            parts = lines[index].split("\t")
            if len(parts) != 3 or parts[0] != "child_output=" + suite:
                raise EvidenceError(f"正式构建测试 {suite} 子输出记录身份不合法")
            channel = parts[1]
            if channel not in ("stdout", "stderr"):
                raise EvidenceError(f"正式构建测试 {suite} 子输出通道不合法")
            channel_index = ("stdout", "stderr").index(channel)
            if channel in seen_channels or channel_index <= last_channel_index:
                raise EvidenceError(f"正式构建测试 {suite} 子输出通道重复或乱序")
            encoded = parts[2]
            if not encoded:
                raise EvidenceError(f"正式构建测试 {suite} 含空子输出记录")
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError, UnicodeError) as exc:
                raise EvidenceError(f"正式构建测试 {suite} 子输出不是 canonical base64") from exc
            if (
                not decoded
                or len(decoded) > 1024 * 1024
                or base64.b64encode(decoded).decode("ascii") != encoded
            ):
                raise EvidenceError(f"正式构建测试 {suite} 子输出越界或不 canonical")
            seen_channels.add(channel)
            last_channel_index = channel_index
            index += 1

        suite_totals = [0] * FORMAL_BUILD_TEST_COUNT_FIELD_COUNT
        suite_target_count = 0
        while index < len(lines) and lines[index].startswith("target_result="):
            if index >= len(lines):
                raise EvidenceError(f"正式构建测试 {suite} 缺少 target result")
            parts = lines[index].split("\t")
            if len(parts) != 4 + FORMAL_BUILD_TEST_COUNT_FIELD_COUNT:
                raise EvidenceError(f"正式构建测试 {suite} target result 字段数不精确")
            if parts[0] != "target_result=" + str(next_ordinal):
                raise EvidenceError(f"正式构建测试 target ordinal 缺失、重复或乱序")
            target_suite, runner, target = parts[1:4]
            if target_suite != suite or runner not in FORMAL_BUILD_TEST_RUNNERS:
                raise EvidenceError(f"正式构建测试 target suite/runner 身份不一致")
            path_part = target.split("::", 1)[0]
            path_components = path_part.split("/")
            if (
                not target
                or target.startswith("/")
                or "\\" in target
                or any(component in ("", ".", "..") for component in path_components)
                or any(ord(character) < 32 or ord(character) == 127 for character in target)
            ):
                raise EvidenceError("正式构建测试 target 不是完整 canonical repo-relative 标识")
            counts = [
                parse_count(value, "target count") for value in parts[4:]
            ]
            collected, deselected, executed, passed, failed, errors, skipped = counts
            if (
                collected <= 0
                or deselected != 0
                or executed != collected
                or passed != collected
                or failed != 0
                or errors != 0
                or skipped != 0
            ):
                raise EvidenceError("正式构建测试 target 未完整执行并全部通过")
            serialized_targets.extend(
                (target_suite + "\t" + runner + "\t" + target + "\n").encode("utf-8")
            )
            suite_totals = [
                total + value for total, value in zip(suite_totals, counts)
            ]
            next_ordinal += 1
            suite_target_count += 1
            index += 1

        if index >= len(lines):
            raise EvidenceError(f"正式构建测试 {suite} 缺少 suite counts")
        summary = lines[index].split("\t")
        if len(summary) != 2 + FORMAL_BUILD_TEST_COUNT_FIELD_COUNT:
            raise EvidenceError(f"正式构建测试 {suite} suite counts 字段数不精确")
        if summary[0] != "suite_counts=" + suite:
            raise EvidenceError(f"正式构建测试 {suite} suite counts 身份不一致")
        observed_target_count = parse_count(summary[1], "suite target count")
        observed_totals = [
            parse_count(value, "suite aggregate count") for value in summary[2:]
        ]
        if suite_target_count <= 0:
            raise EvidenceError(f"正式构建测试 {suite} 缺少 target result")
        if observed_target_count != suite_target_count or observed_totals != suite_totals:
            raise EvidenceError(f"正式构建测试 {suite} suite counts 与 targets 不一致")
        index += 1
        if index >= len(lines) or lines[index] != "suite_status=" + suite + ":pass":
            raise EvidenceError(f"正式构建测试 suite 未唯一通过: {suite}")
        index += 1

    if next_ordinal != FORMAL_BUILD_TEST_TARGET_COUNT:
        raise EvidenceError("正式构建测试 target 总数不是 exact 20")
    if (
        len(serialized_targets) != FORMAL_BUILD_TEST_TARGET_CONTRACT_BYTES
        or hashlib.sha256(serialized_targets).hexdigest()
        != FORMAL_BUILD_TEST_TARGET_CONTRACT_SHA256
    ):
        raise EvidenceError("正式构建测试 observed target registry 身份不一致")
    if index != len(lines) - 1 or lines[index] != "overall_status=pass":
        raise EvidenceError("正式构建测试日志没有唯一末行 overall PASS 闭包")
    return digest


def validate_formal_build_test_log_binding(
    manifest_path: Path,
    marker_path: Path,
    log_path: Path,
    pending_marker_parent: Path = None,
) -> str:
    """Validate the canonical builder-test log, its semantics, and both bindings."""

    canonical_parent = Path(os.path.abspath(manifest_path.parent))
    marker_parent = Path(os.path.abspath(marker_path.parent))
    if (
        manifest_path.name != "taiji-package-manifest.json"
        or log_path.name != FORMAL_BUILD_TEST_LOG_BASENAME
        or Path(os.path.abspath(log_path.parent)) != canonical_parent
    ):
        raise EvidenceError("正式构建测试证据必须来自同一 canonical 产物目录")
    if marker_path.name == ".build-success":
        if marker_parent != canonical_parent or pending_marker_parent is not None:
            raise EvidenceError("已发布正式构建测试 marker 路径不 canonical")
    elif marker_path.name == ".build-success.pending":
        if (
            pending_marker_parent is None
            or not pending_marker_parent.is_absolute()
            or marker_parent != Path(os.path.abspath(pending_marker_parent))
        ):
            raise EvidenceError("待发布正式构建测试 marker 未绑定受控构建根")
    else:
        raise EvidenceError("正式构建测试 marker basename 不 canonical")

    manifest_payload, manifest_opened = read_regular_bytes(
        manifest_path,
        "package manifest",
    )
    manifest = parse_json_bytes(manifest_payload, "package manifest")
    marker_payload, marker_opened = read_regular_bytes(
        marker_path,
        "构建成功标记",
    )
    marker = parse_marker_bytes(marker_payload)
    payload, opened = read_regular_bytes(
        log_path,
        "正式构建测试日志",
        limit=MAX_EVIDENCE_BYTES,
    )
    try:
        current = log_path.lstat()
    except OSError as exc:
        raise EvidenceError(f"正式构建测试日志读回失败: {exc}") from exc
    if regular_file_identity(opened) != regular_file_identity(current):
        raise EvidenceError("正式构建测试日志在读取期间发生变化")
    digest = validate_formal_build_test_payloads(manifest, marker, payload)
    for path, label, initial_identity, initial_payload in (
        (manifest_path, "package manifest", manifest_opened, manifest_payload),
        (marker_path, "构建成功标记", marker_opened, marker_payload),
        (log_path, "正式构建测试日志", opened, payload),
    ):
        current_digest, current_identity = sha256_regular_file(path, label)
        if (
            regular_file_identity(current_identity)
            != regular_file_identity(initial_identity)
            or current_digest != hashlib.sha256(initial_payload).hexdigest()
        ):
            raise EvidenceError(f"{label} 在正式构建测试快照闭包前发生变化")
    return digest


def _validate_checksum_sidecar_payload(
    checksum_payload: bytes,
    deb_hash: str,
    deb_basename: str,
) -> None:
    try:
        checksum_text = checksum_payload.decode("ascii")
    except UnicodeError as exc:
        raise EvidenceError("DEB SHA256 sidecar 必须是 ASCII") from exc
    match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)\n?", checksum_text)
    if not match or match.group(1) != deb_hash or match.group(2) != deb_basename:
        raise EvidenceError("DEB SHA256 sidecar 未准确绑定当前 DEB basename 和内容")


def _validate_checksum_sidecar(args: argparse.Namespace, deb_hash: str) -> None:
    checksum_path = getattr(args, "checksum", None)
    if checksum_path is None:
        return
    checksum_path = Path(checksum_path)
    checksum_payload, _ = read_regular_bytes(checksum_path, "DEB SHA256 sidecar")
    _validate_checksum_sidecar_payload(checksum_payload, deb_hash, Path(args.deb).name)


def _validate_v3_build_binding(args: argparse.Namespace) -> BuildBinding:
    source_commit = getattr(args, "source_commit", "")
    if not FULL_COMMIT_RE.fullmatch(source_commit):
        raise EvidenceError(f"当前源码 commit 格式不合法: {source_commit!r}")
    required_delivery_inputs = {
        "checksum": getattr(args, "checksum", None),
        "build_marker": getattr(args, "build_marker", None),
        "source_archive": getattr(args, "source_archive", None),
        "delivery_dir": getattr(args, "delivery_dir", None),
    }
    missing_delivery_inputs = sorted(
        name for name, value in required_delivery_inputs.items() if value is None
    )
    if missing_delivery_inputs:
        raise EvidenceError(
            "v3 BuildBinding 缺少完整交付身份输入: " + ", ".join(missing_delivery_inputs)
        )
    deb_path = Path(args.deb)
    delivery_dir = Path(required_delivery_inputs["delivery_dir"])
    expected_source_archive = (
        delivery_dir / f"taiji-agentv1.0-kylin-build-src-{source_commit}.tar.gz"
    )
    expected_source_inventory = delivery_dir / (
        f"taiji-agentv1.0-kylin-build-src-{source_commit}.inventory.json"
    )
    expected_paths = {
        "deb": delivery_dir / "生成的安装包" / deb_path.name,
        "checksum": delivery_dir / "生成的安装包" / f"{deb_path.name}.sha256",
        "manifest": delivery_dir / "生成的安装包" / "taiji-package-manifest.json",
        "build_marker": delivery_dir / "生成的安装包" / ".build-success",
        "formal_build_test_log": delivery_dir / "生成的安装包" / FORMAL_BUILD_TEST_LOG_BASENAME,
        "source_archive": expected_source_archive,
        "source_inventory": expected_source_inventory,
    }
    actual_paths = {
        "deb": deb_path,
        "checksum": Path(required_delivery_inputs["checksum"]),
        "manifest": Path(args.manifest),
        "build_marker": Path(required_delivery_inputs["build_marker"]),
        "formal_build_test_log": delivery_dir / "生成的安装包" / FORMAL_BUILD_TEST_LOG_BASENAME,
        "source_archive": Path(required_delivery_inputs["source_archive"]),
        "source_inventory": expected_source_inventory,
    }
    for name, expected_path in expected_paths.items():
        if Path(os.path.abspath(actual_paths[name])) != Path(os.path.abspath(expected_path)):
            raise EvidenceError(f"v3 BuildBinding {name} 必须来自同一交付目录的 canonical 路径")

    snapshot = _delivery_inventory_snapshot(delivery_dir)
    manifest = snapshot.manifest
    reject_target_baseline_fields(manifest, "发布 manifest")
    if manifest.get("schema") != PACKAGE_MANIFEST_SCHEMA_V3:
        if manifest.get("schema_version") == 2:
            raise EvidenceError(
                "当前发布入口只接受 taiji-package-manifest/v3；历史 v2 必须显式 --legacy-v2-read-only"
            )
        raise EvidenceError("销售发布门禁强制 manifest schema=taiji-package-manifest/v3")

    deb_relative = f"生成的安装包/{deb_path.name}"
    checksum_relative = f"生成的安装包/{deb_path.name}.sha256"
    try:
        deb_hash = snapshot.file_hashes[deb_relative]
        checksum_payload = snapshot.control_payloads[checksum_relative]
    except KeyError as exc:
        raise EvidenceError("v3 BuildBinding 快照缺少唯一 DEB 或 sidecar") from exc
    _validate_checksum_sidecar_payload(checksum_payload, deb_hash, deb_path.name)

    required = {
        "schema": PACKAGE_MANIFEST_SCHEMA_V3,
        "package": "taiji-agent",
        "source_commit": source_commit,
        "deb_basename": deb_path.name,
        "deb_sha256": deb_hash,
        "architecture": "amd64",
    }
    for key, expected in required.items():
        if key not in manifest:
            raise EvidenceError(f"发布 manifest 缺少字段: {key}")
        require_exact(manifest, key, expected)
    version = require_nonempty_string(manifest, "version")
    if deb_path.name != f"taiji-agent_{version}_amd64.deb":
        raise EvidenceError("发布 manifest version 与 DEB basename 不一致")
    policy_id = require_nonempty_string(manifest, "compatibility_policy_id")
    if not POLICY_ID_RE.fullmatch(policy_id):
        raise EvidenceError("发布 manifest compatibility_policy_id 格式不合法")
    policy_sha256 = validate_sha256(
        manifest.get("compatibility_policy_sha256"),
        "compatibility_policy_sha256",
    )
    expected_policy_id, expected_policy_sha256 = canonical_policy_identity()
    if policy_id != expected_policy_id:
        raise EvidenceError(
            "发布 manifest compatibility_policy_id 与当前 canonical policy 不一致"
        )
    if policy_sha256 != expected_policy_sha256:
        raise EvidenceError(
            "发布 manifest compatibility_policy_sha256 与当前 canonical policy 不一致"
        )
    electron_hash = validate_sha256(
        manifest.get("electron_executable_sha256"),
        "electron_executable_sha256",
    )
    desktop_hash = validate_sha256(
        manifest.get("desktop_entry_sha256"),
        "desktop_entry_sha256",
    )
    toolchain = validate_manifest_toolchain_identity(manifest)
    validate_manifest_acceptance_identity(manifest)
    if "elf_abi_audit_sha256" in manifest:
        validate_sha256(manifest["elf_abi_audit_sha256"], "elf_abi_audit_sha256")

    try:
        source_hash = snapshot.file_hashes[expected_source_archive.name]
        source_inventory_hash = snapshot.file_hashes[expected_source_inventory.name]
    except KeyError as exc:
        raise EvidenceError("v3 BuildBinding 快照缺少当前源码包或成员清单") from exc
    for key, expected in {
        "source_archive_basename": expected_source_archive.name,
        "source_archive_sha256": source_hash,
        "source_inventory_basename": expected_source_inventory.name,
        "source_inventory_sha256": source_inventory_hash,
    }.items():
        require_exact(manifest, key, expected)
    marker = snapshot.marker
    for key, expected in {
        "source_archive": expected_source_archive.name,
        "source_sha256": source_hash,
        "source_inventory": expected_source_inventory.name,
        "source_inventory_sha256": source_inventory_hash,
    }.items():
        if marker.get(key) != expected:
            raise EvidenceError(f"构建成功标记 {key} 与 source inventory 不一致")
    source_checksums_hash = snapshot.file_hashes["SHA256SUMS.txt"]
    build_marker_hash = snapshot.file_hashes["生成的安装包/.build-success"]
    _revalidate_delivery_snapshot(delivery_dir, snapshot)
    return BuildBinding(
        source_commit=source_commit,
        version=version,
        architecture="amd64",
        deb_basename=deb_path.name,
        deb_sha256=deb_hash,
        compatibility_policy_id=policy_id,
        compatibility_policy_sha256=policy_sha256,
        electron_executable_sha256=electron_hash,
        desktop_entry_sha256=desktop_hash,
        **{field: value for field, value in toolchain.items() if field != "electron_executable_sha256"},
        source_archive_basename=expected_source_archive.name,
        source_archive_sha256=source_hash,
        source_inventory_basename=expected_source_inventory.name,
        source_inventory_sha256=source_inventory_hash,
        source_checksums_sha256=source_checksums_hash,
        build_marker_sha256=build_marker_hash,
        delivery_inventory_sha256=snapshot.digest,
    )


def _validate_v2_build_binding(
    args: argparse.Namespace,
) -> tuple[str, str, str, str, str, str, str]:
    if not FULL_COMMIT_RE.fullmatch(args.source_commit):
        raise EvidenceError(f"当前源码 commit 格式不合法: {args.source_commit!r}")
    deb_hash, _ = sha256_regular_file(args.deb, "当前 DEB")
    source_hash, _ = sha256_regular_file(args.source_archive, "当前源码包")
    packages_hash, _ = sha256_regular_file(args.packages, "离线 Packages")
    packages_gz_hash, _ = sha256_regular_file(args.packages_gz, "离线 Packages.gz")
    checksum_payload, _ = read_regular_bytes(args.checksum, "DEB SHA256 sidecar")
    try:
        checksum_text = checksum_payload.decode("ascii")
    except UnicodeError as exc:
        raise EvidenceError("DEB SHA256 sidecar 必须是 ASCII") from exc
    match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)\n?", checksum_text)
    if not match or match.group(1) != deb_hash or match.group(2) != args.deb.name:
        raise EvidenceError("DEB SHA256 sidecar 未准确绑定当前 DEB basename 和内容")

    manifest = load_json(args.manifest, "发布 manifest")
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version != 2:
        raise EvidenceError("销售发布门禁强制 manifest schema_version=2")
    required_manifest = {
        "schema_version": schema_version,
        "package": "taiji-agent",
        "build_arch": "x86_64",
        "dpkg_arch": "amd64",
        "source_commit": args.source_commit,
        "source_archive": args.source_archive.name,
        "source_sha256": source_hash,
        "deb": args.deb.name,
        "deb_sha256": deb_hash,
        "checksum": args.checksum.name,
        "packages_sha256": packages_hash,
        "packages_gz_sha256": packages_gz_hash,
    }
    for key, expected in required_manifest.items():
        if key not in manifest:
            raise EvidenceError(f"发布 manifest 缺少字段: {key}")
        require_exact(manifest, key, expected)
    version = require_nonempty_string(manifest, "version")
    if args.deb.name != f"taiji-agent_{version}_amd64.deb":
        raise EvidenceError("发布 manifest version 与 DEB basename 不一致")
    require_nonempty_string(manifest, "built_at")
    electron_executable_hash = validate_sha256(
        manifest.get("electron_executable_sha256"), "electron_executable_sha256"
    )
    desktop_entry_hash = validate_sha256(manifest.get("desktop_entry_sha256"), "desktop_entry_sha256")
    target_baseline_profile_id = require_nonempty_string(
        manifest, "target_baseline_profile_id"
    )
    if not TARGET_PROFILE_ID_RE.fullmatch(target_baseline_profile_id):
        raise EvidenceError("发布 manifest target_baseline_profile_id 格式不合法")
    target_baseline_sha256 = validate_sha256(
        manifest.get("target_baseline_sha256"), "target_baseline_sha256"
    )

    marker = parse_marker(args.build_marker)
    expected_marker = {
        "version": version,
        "source_archive": args.source_archive.name,
        "source_sha256": source_hash,
        "deb": args.deb.name,
        "deb_sha256": deb_hash,
        "checksum": args.checksum.name,
        "manifest": args.manifest.name,
        "packages_sha256": packages_hash,
        "packages_gz_sha256": packages_gz_hash,
        "target_baseline_profile_id": target_baseline_profile_id,
        "target_baseline_sha256": target_baseline_sha256,
    }
    require_exact_keys(marker, set(expected_marker) | {"built_at"}, "构建成功标记")
    if not marker["built_at"].strip():
        raise EvidenceError("构建成功标记 built_at 不能为空")
    for key, expected in expected_marker.items():
        if marker[key] != expected:
            raise EvidenceError(f"构建成功标记 {key} 与当前产物不一致")
    return (
        deb_hash,
        version,
        delivery_inventory_sha256(args.delivery_dir),
        electron_executable_hash,
        desktop_entry_hash,
        target_baseline_profile_id,
        target_baseline_sha256,
    )


def _validate_v2_read_only_binding(
    args: argparse.Namespace,
) -> tuple[str, str, str, str, str, str, str]:
    """Small, self-contained v2 history binding used by the read-only CLI."""

    # When the historical delivery still carries the complete v2 binding
    # inputs, retain the old source/marker/offline-repository checks.  Minimal
    # callers may inspect a copied historical manifest without those files,
    # but they still receive the explicit read-only result below.
    legacy_paths = (
        "checksum",
        "build_marker",
        "source_archive",
        "packages",
        "packages_gz",
        "delivery_dir",
    )
    if all(getattr(args, name, None) is not None for name in legacy_paths):
        return _validate_v2_build_binding(args)

    source_commit = getattr(args, "source_commit", "")
    if not FULL_COMMIT_RE.fullmatch(source_commit):
        raise EvidenceError(f"历史 v2 source commit 格式不合法: {source_commit!r}")
    deb_path = Path(args.deb)
    deb_hash, _ = sha256_regular_file(deb_path, "当前 DEB")
    _validate_checksum_sidecar(args, deb_hash)
    manifest = load_json(Path(args.manifest), "历史 v2 发布 manifest")
    if manifest.get("schema_version") != 2:
        raise EvidenceError("历史 v2 manifest 必须是 schema_version=2")
    required = {
        "source_commit": source_commit,
        "deb": deb_path.name,
        "deb_sha256": deb_hash,
    }
    for key, expected in required.items():
        if key not in manifest:
            raise EvidenceError(f"历史 v2 manifest 缺少字段: {key}")
        require_exact(manifest, key, expected)
    version = require_nonempty_string(manifest, "version")
    if deb_path.name != f"taiji-agent_{version}_amd64.deb":
        raise EvidenceError("历史 v2 manifest version 与 DEB basename 不一致")
    profile_id = require_nonempty_string(manifest, "target_baseline_profile_id")
    if not TARGET_PROFILE_ID_RE.fullmatch(profile_id):
        raise EvidenceError("历史 v2 target_baseline_profile_id 格式不合法")
    profile_sha = validate_sha256(
        manifest.get("target_baseline_sha256"), "target_baseline_sha256"
    )
    electron_hash = validate_sha256(
        manifest.get("electron_executable_sha256"),
        "electron_executable_sha256",
    )
    desktop_hash = validate_sha256(
        manifest.get("desktop_entry_sha256"),
        "desktop_entry_sha256",
    )
    return (
        deb_hash,
        version,
        "legacy-v2-read-only",
        electron_hash,
        desktop_hash,
        profile_id,
        profile_sha,
    )


def validate_build_binding(
    args: argparse.Namespace,
    *,
    legacy_v2_read_only: bool = False,
) -> BuildBinding | tuple[str, str, str, str, str, str, str]:
    """Validate the immutable build identity.

    v3 is the only default/current path and returns the named frozen
    ``BuildBinding`` contract.  The old tuple is retained solely for callers
    that explicitly request historical v2 read-only inspection.
    """

    if not legacy_v2_read_only:
        return _validate_v3_build_binding(args)
    manifest = load_json(Path(args.manifest), "历史 v2 发布 manifest")
    if manifest.get("schema") == PACKAGE_MANIFEST_SCHEMA_V3:
        raise EvidenceError("--legacy-v2-read-only 只能检查 manifest schema_version=2")
    if manifest.get("schema_version") == 2:
        return _validate_v2_read_only_binding(args)
    raise EvidenceError("发布 manifest 不是显式 v2 历史合同")


def validate_artifact_binding(
    data: dict[str, Any],
    args: argparse.Namespace,
    deb_hash: str,
    release_artifacts_hash: str,
    target_baseline_profile_id: str,
    target_baseline_sha256: str,
) -> None:
    require_exact(data, "source_commit", args.source_commit)
    require_exact(data, "deb_basename", args.deb.name)
    require_exact(data, "deb_sha256", deb_hash)
    require_exact(data, "release_artifacts_sha256", release_artifacts_hash)
    require_exact(data, "target_baseline_profile_id", target_baseline_profile_id)
    require_exact(data, "target_baseline_sha256", target_baseline_sha256)


def validate_bound_file(
    data: dict[str, Any], evidence_path: Path, basename_key: str, hash_key: str, label: str
) -> tuple[Path, bytes, os.stat_result]:
    basename = data[basename_key]
    if type(basename) is not str or not basename or Path(basename).name != basename:
        raise EvidenceError(f"字段 {basename_key} 必须是同目录文件 basename")
    bound_path = evidence_path.parent / basename
    payload, file_stat = read_regular_bytes(bound_path, label, limit=MAX_EVIDENCE_BYTES)
    recorded_hash = validate_sha256(data[hash_key], hash_key)
    actual_hash = hashlib.sha256(payload).hexdigest()
    if recorded_hash != actual_hash:
        raise EvidenceError(f"{hash_key} 与 {basename} 内容不一致")
    return bound_path, payload, file_stat


def validate_bound_png(
    data: dict[str, Any],
    evidence_path: Path,
    basename_key: str,
    hash_key: str,
    label: str,
) -> tuple[Path, ValidatedPngEvidence, os.stat_result]:
    """Validate one bound PNG without materializing or reopening its bytes."""

    basename = data[basename_key]
    if type(basename) is not str or not basename or Path(basename).name != basename:
        raise EvidenceError(f"字段 {basename_key} 必须是同目录文件 basename")
    bound_path = evidence_path.parent / basename
    try:
        before = bound_path.lstat()
    except OSError as exc:
        raise EvidenceError(f"{label} 不可读取: {bound_path}: {exc}") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise EvidenceError(f"{label} 必须是普通单链接 PNG: {bound_path}")
    descriptor, opened = open_regular(bound_path, label)
    try:
        if regular_file_identity(before) != regular_file_identity(opened):
            raise EvidenceError(f"{label} 在打开前发生变化: {bound_path}")
        png = validate_png_descriptor(descriptor, opened, label)
        recorded_hash = validate_sha256(data[hash_key], hash_key)
        if recorded_hash != png.sha256:
            raise EvidenceError(f"{hash_key} 与 {basename} 内容不一致")
        try:
            after = os.fstat(descriptor)
            current = bound_path.lstat()
        except OSError as exc:
            raise EvidenceError(
                f"{label} PNG 读取后身份无法验证: {bound_path}: {exc}"
            ) from exc
        if (
            regular_file_identity(opened) != regular_file_identity(after)
            or regular_file_identity(opened) != regular_file_identity(current)
        ):
            raise EvidenceError(f"{label} PNG identity changed after validation: {bound_path}")
        return bound_path, png, opened
    finally:
        os.close(descriptor)


def validate_streamed_signed_previous_deb(
    data: dict[str, Any],
    evidence_path: Path,
    signature_payload: bytes,
) -> tuple[Path, str, os.stat_result]:
    """Hash and verify the archived previous DEB through one stable open descriptor."""

    basename = data["deb_basename"]
    if type(basename) is not str or not basename or Path(basename).name != basename:
        raise EvidenceError("字段 deb_basename 必须是同目录文件 basename")
    bound_path = evidence_path.parent / basename
    try:
        before = bound_path.lstat()
    except OSError as exc:
        raise EvidenceError(f"N-1 DEB 不可读取: {bound_path}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode) or bound_path.is_symlink() or before.st_nlink != 1:
        raise EvidenceError(f"N-1 DEB 必须是单链接普通文件: {bound_path}")

    descriptor, opened = open_regular(bound_path, "N-1 DEB")
    try:
        if regular_file_identity(before) != regular_file_identity(opened):
            raise EvidenceError(f"N-1 DEB 在打开前发生变化: {bound_path}")
        if opened.st_size > MAX_PREVIOUS_RELEASE_DEB_BYTES:
            raise EvidenceError(
                f"N-1 DEB 超过大文件大小上限 {MAX_PREVIOUS_RELEASE_DEB_BYTES}: {bound_path}"
            )

        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise EvidenceError(f"N-1 DEB 在摘要计算期间被截断: {bound_path}")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise EvidenceError(f"N-1 DEB 在摘要计算期间增长: {bound_path}")

        actual_hash = digest.hexdigest()
        recorded_hash = validate_sha256(data["deb_sha256"], "deb_sha256")
        if recorded_hash != actual_hash:
            raise EvidenceError(f"deb_sha256 与 {basename} 内容不一致")

        os.lseek(descriptor, 0, os.SEEK_SET)
        verify_with_pinned_release_public_key(
            descriptor,
            signature_payload,
            "previous release DEB",
        )
        after = os.fstat(descriptor)
        current = bound_path.lstat()
        if (
            regular_file_identity(opened) != regular_file_identity(after)
            or regular_file_identity(opened) != regular_file_identity(current)
        ):
            raise EvidenceError(f"N-1 DEB 在流式摘要或验签期间发生变化: {bound_path}")
        return bound_path, actual_hash, opened
    except OSError as exc:
        raise EvidenceError(f"N-1 DEB 流式校验失败: {bound_path}: {exc}") from exc
    finally:
        os.close(descriptor)


def validate_offline_session(data: dict[str, Any], session: dict[str, Any], args: argparse.Namespace) -> None:
    require_exact_keys(session, OFFLINE_SESSION_KEYS, "离线演练会话")
    comparisons = {
        "schema": "taiji.offline-install-rehearsal.v1",
        "generated_at_utc": data["generated_at_utc"],
        "rehearsal_session_id": data["rehearsal_session_id"],
        "challenge_nonce": data["challenge_nonce"],
        "source_commit": args.source_commit,
        "deb_basename": args.deb.name,
        "deb_sha256": data["deb_sha256"],
        "platform": "linux/amd64",
        "environment": data["environment"],
        "os_id": data["os_id"],
        "os_version": data["os_version"],
        "network": "none",
        "desktop_app_verified": False,
        "target_verified": False,
    }
    for key, expected in comparisons.items():
        require_exact(session, key, expected)
    checks = session["checks"]
    if type(checks) is not dict:
        raise EvidenceError("离线演练会话 checks 必须是 object")
    require_exact_keys(checks, {"install", "uninstall", "reinstall"}, "离线演练 checks")
    for key in checks:
        require_exact(checks, key, True)


def _split_debian_version(version: str) -> tuple[int, str, str]:
    if type(version) is not str or not version:
        raise EvidenceError("Debian version 不能为空")
    epoch = 0
    remainder = version
    if ":" in remainder:
        epoch_text, remainder = remainder.split(":", 1)
        if not epoch_text.isdigit() or not remainder:
            raise EvidenceError("Debian version epoch 不合法")
        epoch = int(epoch_text, 10)
    if re.fullmatch(r"[0-9A-Za-z.+~:-]+", version) is None:
        raise EvidenceError("Debian version 包含不合法字符")
    if "-" in remainder:
        upstream, revision = remainder.rsplit("-", 1)
        if not revision:
            raise EvidenceError("Debian version revision 不合法")
    else:
        upstream, revision = remainder, "0"
    if not upstream or not upstream[0].isdigit():
        raise EvidenceError("Debian upstream version 不合法")
    return epoch, upstream, revision


def _debian_character_order(character: str) -> int:
    if character == "~":
        return -1
    if not character:
        return 0
    if character.isalpha():
        return ord(character)
    return ord(character) + 256


def _compare_debian_part(left: str, right: str) -> int:
    left_index = 0
    right_index = 0
    while left_index < len(left) or right_index < len(right):
        while (
            (left_index < len(left) and not left[left_index].isdigit())
            or (right_index < len(right) and not right[right_index].isdigit())
        ):
            left_character = (
                left[left_index]
                if left_index < len(left) and not left[left_index].isdigit()
                else ""
            )
            right_character = (
                right[right_index]
                if right_index < len(right) and not right[right_index].isdigit()
                else ""
            )
            left_order = _debian_character_order(left_character)
            right_order = _debian_character_order(right_character)
            if left_order != right_order:
                return -1 if left_order < right_order else 1
            if left_character:
                left_index += 1
            if right_character:
                right_index += 1
        while left_index < len(left) and left[left_index] == "0":
            left_index += 1
        while right_index < len(right) and right[right_index] == "0":
            right_index += 1
        left_end = left_index
        right_end = right_index
        while left_end < len(left) and left[left_end].isdigit():
            left_end += 1
        while right_end < len(right) and right[right_end].isdigit():
            right_end += 1
        left_digits = left[left_index:left_end]
        right_digits = right[right_index:right_end]
        if len(left_digits) != len(right_digits):
            return -1 if len(left_digits) < len(right_digits) else 1
        if left_digits != right_digits:
            return -1 if left_digits < right_digits else 1
        left_index = left_end
        right_index = right_end
    return 0


def compare_debian_versions(left: str, right: str) -> int:
    left_epoch, left_upstream, left_revision = _split_debian_version(left)
    right_epoch, right_upstream, right_revision = _split_debian_version(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    upstream_order = _compare_debian_part(left_upstream, right_upstream)
    if upstream_order:
        return upstream_order
    return _compare_debian_part(left_revision, right_revision)


def validate_offline_lifecycle_extensions(
    data: dict[str, Any],
    binding: BuildBinding,
    evidence_path: Path,
    *,
    require_lifecycle: bool = False,
) -> None:
    present = OFFLINE_V1_LIFECYCLE_KEYS.intersection(data)
    if not present:
        if require_lifecycle:
            raise EvidenceError("正式认证集必须包含完整 N-1 离线生命周期证据")
        return
    if present != OFFLINE_V1_LIFECYCLE_KEYS:
        missing = sorted(OFFLINE_V1_LIFECYCLE_KEYS - present)
        raise EvidenceError(f"扩展离线生命周期证据缺少字段: {', '.join(missing)}")
    if data["steps"] != OFFLINE_LIFECYCLE_STEPS:
        raise EvidenceError("扩展离线生命周期 steps 不完整或顺序错误")

    previous_version = require_nonempty_string(data, "previous_version")
    require_exact(
        data,
        "previous_deb_basename",
        f"taiji-agent_{previous_version}_amd64.deb",
    )
    validate_sha256(data["previous_deb_sha256"], "previous_deb_sha256")
    require_exact(
        data,
        "previous_signature_basename",
        f"{data['previous_deb_basename']}.sig",
    )
    validate_sha256(data["previous_signature_sha256"], "previous_signature_sha256")
    require_exact(data, "previous_signature_verification", "PASS")
    if compare_debian_versions(previous_version, binding.version) >= 0:
        raise EvidenceError("N-1 previous version 必须按 Debian 版本规则严格早于 candidate")

    require_exact(data, "lifecycle_log_basename", "offline-install-rehearsal-lifecycle.json")
    _, lifecycle_payload, _ = validate_bound_file(
        data,
        evidence_path,
        "lifecycle_log_basename",
        "lifecycle_log_sha256",
        "扩展离线生命周期原始证据",
    )
    lifecycle = parse_json_bytes(lifecycle_payload, "扩展离线生命周期原始证据")
    lifecycle_keys = OFFLINE_SESSION_KEYS | {
        "compatibility_policy_id",
        "compatibility_policy_sha256",
        "previous_deb_basename",
        "previous_deb_sha256",
        "previous_version",
        "previous_signature_basename",
        "previous_signature_sha256",
        "previous_signature_verification",
        "steps",
        "receipts",
        "data_manifests",
        "journal",
        "package_actions",
    }
    require_exact_keys(lifecycle, lifecycle_keys, "扩展离线生命周期原始证据")
    for key in OFFLINE_SESSION_KEYS - {"checks"}:
        if lifecycle.get(key) != data.get(key):
            raise EvidenceError(f"扩展离线生命周期原始证据 {key} 与主证据不一致")
    if lifecycle.get("checks") != {"install": True, "uninstall": True, "reinstall": True}:
        raise EvidenceError("扩展离线生命周期原始证据 checks 不完整")
    for key in (
        "previous_deb_basename",
        "previous_deb_sha256",
        "previous_version",
        "previous_signature_basename",
        "previous_signature_sha256",
        "previous_signature_verification",
        "steps",
        "receipts",
        "data_manifests",
        "journal",
        "package_actions",
    ):
        if lifecycle.get(key) != data.get(key):
            raise EvidenceError(f"扩展离线生命周期原始证据 {key} 与主证据不一致")
    for key, expected in {
        "compatibility_policy_id": binding.compatibility_policy_id,
        "compatibility_policy_sha256": binding.compatibility_policy_sha256,
    }.items():
        if lifecycle.get(key) != expected:
            raise EvidenceError(f"扩展离线生命周期原始证据 {key} 与候选制品不一致")

    receipts = data["receipts"]
    if type(receipts) is not list or len(receipts) != 5:
        raise EvidenceError("扩展离线生命周期 receipts 不完整")
    operations: list[str] = []
    receipt_keys = {
        "operation",
        "result",
        "state",
        "transaction_id",
        "deb_sha256",
        "compatibility_policy_id",
        "compatibility_policy_sha256",
        "network",
    }
    for index, receipt in enumerate(receipts):
        if type(receipt) is not dict:
            raise EvidenceError(f"扩展离线生命周期 receipt[{index}] 必须是 object")
        require_exact_keys(receipt, receipt_keys, f"扩展离线生命周期 receipt[{index}]")
        operation = require_nonempty_string(receipt, "operation")
        operations.append(operation)
        for key in ("result", "state", "transaction_id"):
            require_nonempty_string(receipt, key)
        require_exact(receipt, "deb_sha256", binding.deb_sha256)
        require_exact(receipt, "compatibility_policy_id", binding.compatibility_policy_id)
        require_exact(
            receipt,
            "compatibility_policy_sha256",
            binding.compatibility_policy_sha256,
        )
        require_exact(receipt, "network", "none")
    if operations != [
        "fresh_install",
        "reinstall",
        "upgrade",
        "rollback",
        "upgrade_again",
    ]:
        raise EvidenceError("扩展离线生命周期 receipt operation 顺序不完整")

    manifests = data["data_manifests"]
    manifest_keys = {
        "before_upgrade",
        "after_upgrade",
        "after_rollback",
        "after_remove",
        "after_purge",
    }
    if type(manifests) is not dict:
        raise EvidenceError("扩展离线生命周期 data_manifests 必须是 object")
    require_exact_keys(manifests, manifest_keys, "扩展离线生命周期 data_manifests")
    for key in manifest_keys:
        validate_sha256(manifests[key], f"data_manifests.{key}")
    if len(set(manifests.values())) != 1:
        raise EvidenceError("扩展离线生命周期未证明升级/回滚/卸载保留同一用户数据")

    journal = data["journal"]
    journal_keys = {
        "upgrade_transaction_id",
        "rollback_transaction_id",
        "second_upgrade_transaction_id",
        "resume",
        "power_loss_resume_checked",
        "partial_journal_treated_as_committed",
        "partial_journal_result",
        "manual_recovery_required",
    }
    if type(journal) is not dict:
        raise EvidenceError("扩展离线生命周期 journal 必须是 object")
    require_exact_keys(journal, journal_keys, "扩展离线生命周期 journal")
    for key in (
        "upgrade_transaction_id",
        "rollback_transaction_id",
        "second_upgrade_transaction_id",
        "resume",
    ):
        require_nonempty_string(journal, key)
    require_exact(journal, "power_loss_resume_checked", True)
    require_exact(journal, "partial_journal_treated_as_committed", False)
    require_exact(journal, "partial_journal_result", "manual_recovery_required")
    require_exact(journal, "manual_recovery_required", False)

    actions = data["package_actions"]
    if type(actions) is not list or not actions:
        raise EvidenceError("扩展离线生命周期 package_actions 必须是非空 list")
    action_keys = {"command", "package", "network", "download"}
    commands: set[str] = set()
    for index, action in enumerate(actions):
        if type(action) is not dict:
            raise EvidenceError(f"扩展离线生命周期 package_actions[{index}] 必须是 object")
        require_exact_keys(action, action_keys, f"扩展离线生命周期 package_actions[{index}]")
        require_choice(action, "command", {"dpkg --install", "dpkg --remove", "dpkg --purge"})
        commands.add(action["command"])
        require_nonempty_string(action, "package")
        require_exact(action, "network", "none")
        require_exact(action, "download", False)
    if commands != {"dpkg --install", "dpkg --remove", "dpkg --purge"}:
        raise EvidenceError("扩展离线生命周期 package_actions 未覆盖 install/remove/purge")

    previous = data["previous_release"]
    if type(previous) is not dict:
        raise EvidenceError("扩展离线生命周期 previous_release 必须是 object")
    require_exact_keys(
        previous,
        OFFLINE_PREVIOUS_RELEASE_KEYS,
        "扩展离线生命周期 previous_release",
    )
    previous_source_commit = require_nonempty_string(previous, "source_commit")
    if not re.fullmatch(r"[0-9a-f]{40}", previous_source_commit):
        raise EvidenceError("previous_release.source_commit 必须是 40 位小写 Git commit")
    previous_version = require_nonempty_string(previous, "version")
    if compare_debian_versions(previous_version, binding.version) >= 0:
        raise EvidenceError("previous_release.version 必须按 Debian 版本规则严格早于当前候选")
    previous_policy_id = require_nonempty_string(previous, "compatibility_policy_id")
    previous_policy_sha256 = validate_sha256(
        previous["compatibility_policy_sha256"],
        "previous_release.compatibility_policy_sha256",
    )
    require_exact(previous, "signature_verification", "PASS")

    previous_signature_path, previous_signature_payload, _ = validate_bound_file(
        previous,
        evidence_path,
        "signature_basename",
        "signature_sha256",
        "previous release DEB detached signature",
    )
    if (
        previous_signature_path.name != f"{previous['deb_basename']}.sig"
        or previous_signature_path.name != data["previous_signature_basename"]
        or previous["signature_sha256"] != data["previous_signature_sha256"]
        or previous["signature_verification"] != data["previous_signature_verification"]
    ):
        raise EvidenceError("previous_release detached signature 与生命周期声明不一致")
    previous_deb_path, previous_deb_sha256, _ = validate_streamed_signed_previous_deb(
        previous,
        evidence_path,
        previous_signature_payload,
    )
    if (
        previous_version != data["previous_version"]
        or previous_deb_path.name != data["previous_deb_basename"]
        or previous_deb_sha256 != data["previous_deb_sha256"]
    ):
        raise EvidenceError("previous_release 与扩展生命周期 N-1 身份不一致")
    _, checksum_payload, _ = validate_bound_file(
        previous,
        evidence_path,
        "checksum_basename",
        "checksum_sha256",
        "N-1 DEB SHA256 sidecar",
    )
    try:
        checksum_text = checksum_payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EvidenceError("N-1 DEB SHA256 sidecar 必须是 ASCII") from exc
    checksum_match = re.fullmatch(
        r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)\n?",
        checksum_text,
    )
    if (
        checksum_match is None
        or checksum_match.group(1) != previous_deb_sha256
        or checksum_match.group(2) != previous_deb_path.name
    ):
        raise EvidenceError("N-1 DEB SHA256 sidecar 未准确绑定归档 DEB")

    _, manifest_payload, _ = validate_bound_file(
        previous,
        evidence_path,
        "manifest_basename",
        "manifest_sha256",
        "N-1 发布 manifest",
    )
    manifest = parse_json_bytes(manifest_payload, "N-1 发布 manifest")
    for key, expected in {
        "schema": PACKAGE_MANIFEST_SCHEMA_V3,
        "package": "taiji-agent",
        "version": previous_version,
        "architecture": "amd64",
        "source_commit": previous_source_commit,
        "deb_basename": previous_deb_path.name,
        "deb_sha256": previous_deb_sha256,
        "compatibility_policy_id": previous_policy_id,
        "compatibility_policy_sha256": previous_policy_sha256,
    }.items():
        require_exact(manifest, key, expected)


def validate_offline_evidence_v1(
    data: dict[str, Any],
    evidence_path: Path,
    args: argparse.Namespace,
    binding: BuildBinding,
    *,
    require_lifecycle: bool = False,
) -> None:
    reject_target_baseline_fields(data, "offline rehearsal evidence v1")
    missing = sorted(OFFLINE_V1_KEYS - data.keys())
    extra = sorted(data.keys() - OFFLINE_V1_KEYS - OFFLINE_V1_LIFECYCLE_KEYS)
    if missing or extra:
        details = []
        if missing:
            details.append(f"缺少字段: {', '.join(missing)}")
        if extra:
            details.append(f"未知字段: {', '.join(extra)}")
        raise EvidenceError(
            f"{evidence_path.name} 字段集合不合法；{'；'.join(details)}"
        )
    for key, expected in {
        "schema": OFFLINE_EVIDENCE_SCHEMA_V1,
        "status": "PASS",
        "source_commit": binding.source_commit,
        "version": binding.version,
        "architecture": binding.architecture,
        "deb_basename": binding.deb_basename,
        "deb_sha256": binding.deb_sha256,
        "compatibility_policy_id": binding.compatibility_policy_id,
        "compatibility_policy_sha256": binding.compatibility_policy_sha256,
        "platform": "linux/amd64",
        "network": "none",
        "desktop_app_verified": False,
        "target_verified": False,
        "log_basename": "offline-install-rehearsal-session.json",
    }.items():
        require_exact(data, key, expected)
    if binding.delivery_inventory_sha256:
        require_exact(
            data,
            "delivery_inventory_sha256",
            binding.delivery_inventory_sha256,
        )
    validate_fresh_timestamp(data["generated_at_utc"], "generated_at_utc")
    validate_session_id(data["rehearsal_session_id"], "rehearsal_session_id")
    validate_challenge(data["challenge_nonce"], args.challenge)
    require_choice(
        data,
        "environment",
        {"container", "vm", "chroot", OFFLINE_REHEARSAL_ENVIRONMENT},
    )
    require_nonempty_string(data, "os_id")
    require_nonempty_string(data, "os_version")
    validate_sha256(data["deb_sha256"], "deb_sha256")
    validate_sha256(data["compatibility_policy_sha256"], "compatibility_policy_sha256")
    validate_sha256(data["delivery_inventory_sha256"], "delivery_inventory_sha256")
    expected_policy_id, expected_policy_sha256 = canonical_policy_identity()
    require_exact(data, "compatibility_policy_id", expected_policy_id)
    require_exact(data, "compatibility_policy_sha256", expected_policy_sha256)

    checks = data["checks"]
    if type(checks) is not dict:
        raise EvidenceError("offline rehearsal evidence v1 checks 必须是 object")
    require_exact_keys(checks, {"install", "uninstall", "reinstall"}, "offline rehearsal checks")
    for key in checks:
        require_exact(checks, key, "PASS")

    _, log_payload, _ = validate_bound_file(
        data,
        evidence_path,
        "log_basename",
        "log_sha256",
        "离线演练结构化会话",
    )
    validate_offline_session(
        data,
        parse_json_bytes(log_payload, "离线演练结构化会话"),
        args,
    )
    validate_offline_lifecycle_extensions(
        data,
        binding,
        evidence_path,
        require_lifecycle=require_lifecycle,
    )


def validate_offline(
    data: dict[str, Any], evidence_path: Path, args: argparse.Namespace, deb_hash: str,
    release_artifacts_hash: str, target_baseline_profile_id: str,
    target_baseline_sha256: str,
) -> None:
    require_exact_keys(data, OFFLINE_KEYS, evidence_path.name)
    for key, expected in {
        "schema_version": 2,
        "evidence_type": "offline-install-rehearsal",
        "platform": "linux/amd64",
        "network": "none",
        "install": True,
        "uninstall": True,
        "reinstall": True,
        "desktop_app_verified": False,
        "target_verified": False,
    }.items():
        require_exact(data, key, expected)
    validate_fresh_timestamp(data["generated_at_utc"], "generated_at_utc")
    validate_session_id(data["rehearsal_session_id"], "rehearsal_session_id")
    validate_challenge(data["challenge_nonce"], args.challenge)
    require_choice(data, "environment", {"container", "vm", "chroot"})
    require_nonempty_string(data, "os_id")
    require_nonempty_string(data, "os_version")
    validate_artifact_binding(
        data,
        args,
        deb_hash,
        release_artifacts_hash,
        target_baseline_profile_id,
        target_baseline_sha256,
    )
    _, log_payload, _ = validate_bound_file(
        data, evidence_path, "log_basename", "log_sha256", "离线演练结构化会话"
    )
    validate_offline_session(data, parse_json_bytes(log_payload, "离线演练结构化会话"), args)


def validate_png(payload: bytes) -> None:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise EvidenceError("桌面 App 截图不是 PNG")
    offset = 8
    chunks: list[tuple[bytes, bytes]] = []
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            raise EvidenceError("桌面 App PNG chunk 被截断")
        chunk_data = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length : end])[0]
        if zlib.crc32(kind + chunk_data) != expected_crc:
            raise EvidenceError("桌面 App PNG CRC 不合法")
        chunks.append((kind, chunk_data))
        offset = end
        if kind == b"IEND":
            break
    if offset != len(payload) or not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise EvidenceError("桌面 App PNG 结构不完整")
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        raise EvidenceError("桌面 App PNG IHDR 不合法")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", ihdr)
    if (
        width < 800
        or height < 600
        or width > 7680
        or height > 4320
        or bit_depth != 8
        or color_type not in {2, 6}
        or compression
        or filtering
        or interlace
    ):
        raise EvidenceError("桌面 App PNG 必须是 800x600 至 7680x4320 的非交错 RGB8/RGBA8 截图")
    compressed = b"".join(chunk for kind, chunk in chunks if kind == b"IDAT")
    bytes_per_pixel = 3 if color_type == 2 else 4
    row_payload_bytes = width * bytes_per_pixel
    row_bytes = row_payload_bytes + 1
    expected_size = row_bytes * height
    try:
        decompressor = zlib.decompressobj()
        pixels = decompressor.decompress(compressed, expected_size + 1)
    except zlib.error as exc:
        raise EvidenceError("桌面 App PNG 像素数据无法解压") from exc
    if (
        len(pixels) != expected_size
        or not decompressor.eof
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or any(pixels[index] > 4 for index in range(0, len(pixels), row_bytes))
    ):
        raise EvidenceError("桌面 App PNG 像素数据不完整")

    def paeth(left: int, above: int, upper_left: int) -> int:
        estimate = left + above - upper_left
        left_distance = abs(estimate - left)
        above_distance = abs(estimate - above)
        upper_left_distance = abs(estimate - upper_left)
        if left_distance <= above_distance and left_distance <= upper_left_distance:
            return left
        if above_distance <= upper_left_distance:
            return above
        return upper_left

    previous = bytearray(row_payload_bytes)
    colors: set[bytes] = set()
    any_visible_alpha = color_type == 2
    for row_index in range(height):
        offset = row_index * row_bytes
        filter_type = pixels[offset]
        encoded = pixels[offset + 1 : offset + row_bytes]
        decoded = bytearray(row_payload_bytes)
        for index, value in enumerate(encoded):
            left = decoded[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                predictor = paeth(left, above, upper_left)
            decoded[index] = (value + predictor) & 0xFF
        if len(colors) < 33:
            for pixel_offset in range(0, row_payload_bytes, bytes_per_pixel):
                colors.add(bytes(decoded[pixel_offset : pixel_offset + 3]))
                if len(colors) >= 33:
                    break
        if color_type == 6 and not any_visible_alpha:
            any_visible_alpha = any(decoded[index] != 0 for index in range(3, row_payload_bytes, 4))
        previous = decoded
    if len(colors) < 16 or not any_visible_alpha:
        raise EvidenceError("桌面 App PNG 缺少足够的可见界面像素变化")


def validate_png_descriptor(
    descriptor: int,
    file_stat: os.stat_result,
    label: str,
    *,
    snapshot_descriptor: int | None = None,
) -> ValidatedPngEvidence:
    """Stream-validate one PNG from a single stable descriptor.

    The encoded file is never assembled into one bytes value.  If a private
    snapshot descriptor is supplied, each accepted byte is written to it in
    the same pass used for the digest, CRC, PNG and pixel checks.
    """
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_nlink != 1
        or file_stat.st_size <= 0
        or file_stat.st_size > MAX_EVIDENCE_BYTES
    ):
        raise EvidenceError(f"{label} 必须是 32MiB 内的普通单链接 PNG")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise EvidenceError(f"{label} 无法定位到 PNG 起始位置") from exc
    digest = hashlib.sha256()
    total = 0

    def consume_encoded(size: int) -> bytes:
        nonlocal total
        pieces: list[bytes] = []
        remaining = size
        while remaining:
            try:
                piece = os.read(descriptor, min(1024 * 1024, remaining))
            except OSError as exc:
                raise EvidenceError(f"{label} PNG 无法读取") from exc
            if not piece:
                raise EvidenceError(f"{label} PNG 被截断")
            digest.update(piece)
            total += len(piece)
            if snapshot_descriptor is not None:
                view = memoryview(piece)
                while view:
                    try:
                        written = os.write(snapshot_descriptor, view)
                    except OSError as exc:
                        raise EvidenceError(f"{label} PNG 私有快照写入失败") from exc
                    if written <= 0:
                        raise EvidenceError(f"{label} PNG 私有快照写入失败")
                    view = view[written:]
            pieces.append(piece)
            remaining -= len(piece)
        return b"".join(pieces)

    signature = consume_encoded(8)
    if signature != b"\x89PNG\r\n\x1a\n":
        raise EvidenceError(f"{label} 不是 PNG")

    decompressor = zlib.decompressobj()
    decoded_pending = bytearray()
    previous = bytearray()
    colors: set[bytes] = set()
    any_visible_alpha = False
    decoded_total = 0
    row_index = 0
    width = height = color_type = bytes_per_pixel = row_payload_bytes = row_bytes = 0
    expected_decoded = 0

    def paeth(left: int, above: int, upper_left: int) -> int:
        estimate = left + above - upper_left
        distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
        if distances[0] <= distances[1] and distances[0] <= distances[2]:
            return left
        if distances[1] <= distances[2]:
            return above
        return upper_left

    def consume_decoded(payload: bytes) -> None:
        nonlocal decoded_total, row_index, previous, any_visible_alpha
        decoded_total += len(payload)
        if decoded_total > expected_decoded:
            raise EvidenceError(f"{label} PNG 像素数据超出 IHDR 范围")
        decoded_pending.extend(payload)
        while row_bytes and len(decoded_pending) >= row_bytes:
            if row_index >= height:
                raise EvidenceError(f"{label} PNG 像素行过多")
            filter_type = decoded_pending[0]
            if filter_type > 4:
                raise EvidenceError(f"{label} PNG 使用未知过滤器")
            encoded = bytes(decoded_pending[1:row_bytes])
            del decoded_pending[:row_bytes]
            decoded = bytearray(row_payload_bytes)
            for index, value in enumerate(encoded):
                left = decoded[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                above = previous[index] if previous else 0
                upper_left = (
                    previous[index - bytes_per_pixel]
                    if previous and index >= bytes_per_pixel
                    else 0
                )
                if filter_type == 0:
                    predictor = 0
                elif filter_type == 1:
                    predictor = left
                elif filter_type == 2:
                    predictor = above
                elif filter_type == 3:
                    predictor = (left + above) // 2
                else:
                    predictor = paeth(left, above, upper_left)
                decoded[index] = (value + predictor) & 0xFF
            if len(colors) < 33:
                for offset in range(0, row_payload_bytes, bytes_per_pixel):
                    colors.add(bytes(decoded[offset : offset + 3]))
                    if len(colors) >= 33:
                        break
            if color_type == 6 and not any_visible_alpha:
                any_visible_alpha = any(
                    decoded[offset] != 0 for offset in range(3, row_payload_bytes, 4)
                )
            previous = decoded
            row_index += 1

    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    chunk_index = 0
    while total < file_stat.st_size:
        header = consume_encoded(8)
        length = struct.unpack(">I", header[:4])[0]
        kind = header[4:]
        if chunk_index == 0 and (kind != b"IHDR" or length != 13):
            raise EvidenceError(f"{label} PNG IHDR 不合法")
        if chunk_index != 0 and kind == b"IHDR":
            raise EvidenceError(f"{label} PNG 包含重复 IHDR")
        if length > file_stat.st_size - total - 4:
            raise EvidenceError(f"{label} PNG chunk 被截断")
        crc = zlib.crc32(kind)
        remaining = length
        ihdr = bytearray()
        while remaining:
            piece = consume_encoded(min(1024 * 1024, remaining))
            crc = zlib.crc32(piece, crc)
            if kind == b"IHDR":
                ihdr.extend(piece)
            if kind == b"IDAT":
                compressed = piece
                while compressed:
                    budget = min(1024 * 1024, expected_decoded - decoded_total + 1)
                    try:
                        decoded = decompressor.decompress(compressed, max(1, budget))
                    except zlib.error as exc:
                        raise EvidenceError(f"{label} PNG 像素数据无法解压") from exc
                    consume_decoded(decoded)
                    compressed = decompressor.unconsumed_tail
            remaining -= len(piece)
        expected_crc = struct.unpack(">I", consume_encoded(4))[0]
        if (crc & 0xFFFFFFFF) != expected_crc:
            raise EvidenceError(f"{label} PNG CRC 不合法")
        if chunk_index == 0:
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", bytes(ihdr)
            )
            if (
                width < 800
                or height < 600
                or width > 7680
                or height > 4320
                or bit_depth != 8
                or color_type not in {2, 6}
                or compression
                or filtering
                or interlace
            ):
                raise EvidenceError(
                    f"{label} 必须是 800x600 至 7680x4320 的非交错 RGB8/RGBA8 PNG"
                )
            bytes_per_pixel = 3 if color_type == 2 else 4
            row_payload_bytes = width * bytes_per_pixel
            row_bytes = row_payload_bytes + 1
            expected_decoded = row_bytes * height
            previous = bytearray(row_payload_bytes)
            any_visible_alpha = color_type == 2
            saw_ihdr = True
        if kind == b"IDAT":
            if not saw_ihdr:
                raise EvidenceError(f"{label} PNG IDAT 早于 IHDR")
            saw_idat = True
        if kind == b"IEND":
            if length != 0 or total != file_stat.st_size:
                raise EvidenceError(f"{label} PNG IEND 或尾随数据不合法")
            saw_iend = True
            break
        chunk_index += 1
    try:
        trailing = os.read(descriptor, 1)
    except OSError as exc:
        raise EvidenceError(f"{label} PNG 无法完成读取") from exc
    if trailing or total != file_stat.st_size or not saw_ihdr or not saw_idat or not saw_iend:
        raise EvidenceError(f"{label} PNG 结构不完整或含尾随数据")
    if (
        not decompressor.eof
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or decoded_total != expected_decoded
        or decoded_pending
        or row_index != height
    ):
        raise EvidenceError(f"{label} PNG 像素数据不完整")
    if len(colors) < 16 or not any_visible_alpha:
        raise EvidenceError(f"{label} PNG 缺少足够的可见界面像素变化")
    if regular_file_identity(os.fstat(descriptor)) != regular_file_identity(file_stat):
        raise EvidenceError(f"{label} PNG 在读取期间发生变化")
    return ValidatedPngEvidence(
        sha256=digest.hexdigest(),
        size=file_stat.st_size,
        width=width,
        height=height,
        color_type=color_type,
    )


def validate_support_bundle(payload: bytes) -> None:
    bundle = parse_json_bytes(payload, "桌面 App 诊断导出")
    require_exact_keys(bundle, {"schema", "manifest", "diagnostics"}, "桌面 App 诊断导出")
    require_exact(bundle, "schema", "taiji.product.support-bundle.v1")
    manifest = bundle["manifest"]
    if type(manifest) is not dict:
        raise EvidenceError("诊断导出 manifest 必须是 object")
    require_exact_keys(
        manifest,
        {"redacted", "logs_included", "paths_included", "secrets_included"},
        "诊断导出 manifest",
    )
    for key, expected in {
        "redacted": True,
        "logs_included": False,
        "paths_included": False,
        "secrets_included": False,
    }.items():
        require_exact(manifest, key, expected)
    diagnostics = bundle["diagnostics"]
    if type(diagnostics) is not dict:
        raise EvidenceError("诊断导出 diagnostics 必须是 object")
    require_exact_keys(
        diagnostics,
        {"schema", "generated_at", "incident_id", "overall", "components"},
        "诊断导出 diagnostics",
    )
    require_exact(diagnostics, "schema", "taiji.product.diagnostics.v1")
    validate_fresh_timestamp(diagnostics["generated_at"], "diagnostics.generated_at")
    incident_id = require_nonempty_string(diagnostics, "incident_id")
    if not INCIDENT_RE.fullmatch(incident_id):
        raise EvidenceError("诊断导出 incident_id 格式不合法")
    require_exact(diagnostics, "overall", "ready")
    components = diagnostics.get("components")
    if type(components) is not list:
        raise EvidenceError("诊断导出 components 必须是 array")
    expected_labels = {
        "webui": "桌面界面",
        "agent": "智能体服务",
        "gateway": "本地任务服务",
        "license": "授权状态",
        "docx": "文档引擎",
        "skills": "专家能力",
        "node": "运行环境",
    }
    expected_component_ids = list(expected_labels)
    component_ids = [item.get("id") for item in components if type(item) is dict]
    if component_ids != expected_component_ids:
        raise EvidenceError("诊断导出缺少完整产品组件状态")
    statuses: dict[str, str] = {}
    allowed_statuses = {"ready", "degraded", "blocked", "not_applicable", "unknown"}
    for component in components:
        allowed_keys = {"id", "label", "status", "version"}
        extra = set(component) - allowed_keys
        missing = {"id", "label", "status"} - set(component)
        if extra or missing:
            raise EvidenceError("诊断导出组件字段集合不合法")
        require_exact(component, "label", expected_labels[component["id"]])
        require_choice(component, "status", allowed_statuses)
        if "version" in component:
            version = require_nonempty_string(component, "version")
            if not PUBLIC_VERSION_RE.fullmatch(version) or UNSAFE_VERSION_RE.search(version):
                raise EvidenceError("诊断导出组件 version 不符合公开安全格式")
        statuses[component["id"]] = component["status"]

    required = {"webui", "agent", "gateway", "license"}
    if any(statuses[component_id] == "blocked" for component_id in required):
        calculated_overall = "blocked"
    elif any(
        status in {"blocked", "degraded", "unknown"}
        for status in statuses.values()
        if status != "not_applicable"
    ):
        calculated_overall = "degraded"
    else:
        calculated_overall = "ready"
    if diagnostics["overall"] != calculated_overall:
        raise EvidenceError("诊断导出 overall 与组件状态不一致")


def _parsed_fresh_timestamp(value: Any, key: str) -> datetime:
    validate_fresh_timestamp(value, key)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def validate_install_observation(
    data: dict[str, Any],
    observation: dict[str, Any],
    args: argparse.Namespace,
    deb_hash: str,
    target_baseline_profile_id: str,
    target_baseline_sha256: str,
) -> None:
    require_exact_keys(observation, INSTALL_OBSERVATION_KEYS, "单 DEB 安装观察记录")
    manifest_payload, _ = read_regular_bytes(args.manifest, "发布 manifest")
    expected = {
        "schema": "taiji.single-deb-install-observation.v1",
        "challenge_nonce": args.challenge,
        "machine_fingerprint_sha256": data["machine_fingerprint_sha256"],
        "source_commit": args.source_commit,
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "deb_observed_basename": args.deb.name,
        "deb_sha256": deb_hash,
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
    for key, expected_value in expected.items():
        require_exact(observation, key, expected_value)
    validate_sha256(observation["boot_fingerprint_sha256"], "observer.boot_fingerprint_sha256")
    if type(observation["target_uid"]) is not int or observation["target_uid"] <= 0:
        raise EvidenceError("单 DEB 安装观察记录 target_uid 不合法")
    validate_sha256(
        observation["canonical_home_fingerprint_sha256"],
        "observer.canonical_home_fingerprint_sha256",
    )
    validate_sha256(
        observation["user_state_paths_fingerprint_sha256"],
        "observer.user_state_paths_fingerprint_sha256",
    )
    started = _parsed_fresh_timestamp(observation["started_at_utc"], "observer.started_at_utc")
    completed = _parsed_fresh_timestamp(observation["completed_at_utc"], "observer.completed_at_utc")
    generated = _parsed_fresh_timestamp(observation["generated_at_utc"], "observer.generated_at_utc")
    if not started <= completed <= generated <= datetime.now(timezone.utc) + timedelta(minutes=5):
        raise EvidenceError("单 DEB 安装观察记录时间顺序不合法")
    transitions = observation["package_status_transitions"]
    if (
        type(transitions) is not list
        or not transitions
        or any(type(value) is not str for value in transitions)
        or transitions[0] != "not-installed"
        or transitions[-1] != "install ok installed"
    ):
        raise EvidenceError("单 DEB 安装观察记录未证明 absent 到 installed 状态迁移")
    if type(observation["network_sample_interval_ms"]) is not int or observation["network_sample_interval_ms"] <= 0:
        raise EvidenceError("单 DEB 安装观察记录网络采样间隔不合法")
    if type(observation["network_sample_count"]) is not int or observation["network_sample_count"] < 2:
        raise EvidenceError("单 DEB 安装观察记录网络采样数量不足")


def validate_install_method_attestation(
    data: dict[str, Any],
    observation: dict[str, Any],
    observation_hash: str,
    attestation: dict[str, Any],
    graphical_evidence_hash: str,
    args: argparse.Namespace,
) -> None:
    require_exact_keys(
        attestation,
        INSTALL_METHOD_ATTESTATION_KEYS,
        "桌面双击安装人工见证",
    )
    expected = {
        "schema": "taiji.single-deb-install-method-attestation.v1",
        "observation_basename": INSTALL_OBSERVATION_BASENAME,
        "observation_sha256": observation_hash,
        "challenge_nonce": args.challenge,
        "machine_fingerprint_sha256": data["machine_fingerprint_sha256"],
        "boot_fingerprint_sha256": observation["boot_fingerprint_sha256"],
        "deb_sha256": data["deb_sha256"],
        "installation_method_attested": "desktop-double-click",
        "installation_method_machine_observed": False,
        "attestation_scope": "human-observed-system-graphical-installer",
        "confirmation": True,
        "graphical_installer_evidence_basename": GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
        "graphical_installer_evidence_sha256": graphical_evidence_hash,
    }
    for key, expected_value in expected.items():
        require_exact(attestation, key, expected_value)
    if type(attestation["operator_id"]) is not str or not OPERATOR_ID_RE.fullmatch(attestation["operator_id"]):
        raise EvidenceError("人工见证 operator_id 格式不合法")
    attested = _parsed_fresh_timestamp(attestation["generated_at_utc"], "attestation.generated_at_utc")
    completed = _parsed_fresh_timestamp(observation["completed_at_utc"], "observer.completed_at_utc")
    if attested < completed:
        raise EvidenceError("桌面双击安装人工见证早于机器安装观察完成时间")


def validate_target_session(
    data: dict[str, Any], session: dict[str, Any], args: argparse.Namespace, version: str
) -> None:
    require_exact_keys(session, TARGET_SESSION_KEYS, "桌面 App 验收会话")
    comparisons = {
        "schema": "taiji.desktop.acceptance.v1",
        "application": "taiji-electron-desktop",
        "generated_at_utc": data["generated_at_utc"],
        "acceptance_session_id": data["acceptance_session_id"],
        "challenge_nonce": data["challenge_nonce"],
        "source_commit": args.source_commit,
        "deb_sha256": data["deb_sha256"],
        "platform": "linux/amd64",
        "os_id": data["os_id"],
        "os_version": data["os_version"],
        "desktop_environment": data["desktop_environment"],
        "target_baseline_profile_id": data["target_baseline_profile_id"],
        "target_baseline_sha256": data["target_baseline_sha256"],
        "installation_method": "desktop-double-click",
        "installation_method_evidence": "human-attestation",
        "installation_method_machine_observed": False,
        "installation_network": "continuous-process-sampling-no-non-loopback-up",
        "installation_file_count": 1,
        "additional_install_files": False,
        "dpkg_status_before": "not-installed",
        "dpkg_status_after": "install ok installed",
        "first_configuration_cycle_completed": True,
        "machine_fingerprint_sha256": data["machine_fingerprint_sha256"],
        "install_observation_basename": data["install_observation_basename"],
        "install_observation_sha256": data["install_observation_sha256"],
        "install_method_attestation_basename": data["install_method_attestation_basename"],
        "install_method_attestation_sha256": data["install_method_attestation_sha256"],
        "graphical_installer_evidence_basename": data["graphical_installer_evidence_basename"],
        "graphical_installer_evidence_sha256": data["graphical_installer_evidence_sha256"],
        "electron_executable_sha256": data["electron_executable_sha256"],
        "desktop_entry_sha256": data["desktop_entry_sha256"],
        "installed_package_version": version,
        "transport": "electron-cdp",
        "desktop_token_present": True,
        "web_fallback_used": False,
        "js_error_count": 0,
        "unexpected_http_failures": 0,
    }
    for key, expected in comparisons.items():
        require_exact(session, key, expected)
    if type(session["electron_pid"]) is not int or session["electron_pid"] <= 1:
        raise EvidenceError("桌面 App 验收会话 electron_pid 不合法")
    executable = require_nonempty_string(session, "electron_executable")
    if executable != "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron":
        raise EvidenceError("桌面 App 验收会话未记录安装态 Electron executable")
    checks = session["checks"]
    if type(checks) is not dict:
        raise EvidenceError("桌面 App 验收会话 checks 必须是 object")
    require_exact_keys(checks, TARGET_CHECK_KEYS, "桌面 App 验收 checks")
    for key in checks:
        require_exact(checks, key, True)


def validate_target_driver(
    data: dict[str, Any], session: dict[str, Any], driver: dict[str, Any]
) -> None:
    require_exact_keys(driver, DRIVER_KEYS, "桌面 App 驱动原始结果")
    comparisons = {
        "schema": "taiji.desktop.acceptance-driver.v2",
        "acceptance_session_id": data["acceptance_session_id"],
        "challenge_nonce": data["challenge_nonce"],
        "electron_pid": session["electron_pid"],
        "electron_executable": ELECTRON_PATH,
        "electron_executable_sha256": data["electron_executable_sha256"],
        "desktop_entry_sha256": data["desktop_entry_sha256"],
        "screenshot_basename": data["screenshot_basename"],
        "diagnostic_basename": data["diagnostic_basename"],
        "js_error_count": session["js_error_count"],
        "unexpected_http_failures": session["unexpected_http_failures"],
        "electron_exit_code": 0,
    }
    for key, expected in comparisons.items():
        require_exact(driver, key, expected)
    validate_session_id(driver["acceptance_session_id"], "driver.acceptance_session_id")
    for key in ("electron_pid", "agent_pid", "web_pid"):
        validate_driver_pid(driver[key], f"driver.{key}")
    for key in (
        "electron_executable_sha256",
        "desktop_entry_sha256",
        "attachment_probe_sha256",
    ):
        validate_sha256(driver[key], f"driver.{key}")
    validate_driver_visible_text(driver["model"], "driver.model", maximum=256)
    validate_driver_app_urls(driver["app_url"], driver["webui_origin"])
    validate_driver_desktop_auth_cookie(driver["desktop_auth_cookie"])
    validate_target_driver_v2_observations(driver)
    if driver["screenshot_basename"] != SCREENSHOT_BASENAME:
        raise EvidenceError("桌面 App 驱动截图必须使用固定 basename")
    if driver["diagnostic_basename"] != DIAGNOSTIC_BASENAME:
        raise EvidenceError("桌面 App 驱动诊断导出必须使用固定 basename")
    checks = driver["checks"]
    if type(checks) is not dict:
        raise EvidenceError("桌面 App 驱动 checks 必须是 object")
    require_exact_keys(checks, TARGET_CHECK_KEYS, "桌面 App 驱动 checks")
    for key in TARGET_CHECK_KEYS:
        require_exact(checks, key, True)
        require_exact(session["checks"], key, True)
        require_exact(data, key, True)


def certification_attachment_limit(basename: str) -> int:
    """Return the fixed recursive-validation cap for one evidence basename."""

    if basename in CERTIFICATION_LARGE_PNG_BASENAMES:
        return MAX_EVIDENCE_BYTES
    return MAX_JSON_BYTES


def validate_positive_certification_bundle(
    record: dict[str, Any],
    json_attachment_payloads: dict[str, bytes],
    png_evidence: dict[str, ValidatedPngEvidence],
    *,
    expected_release_artifacts_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
    expected_electron_executable_sha256: str | None = None,
    expected_desktop_entry_sha256: str | None = None,
) -> None:
    """Recursively validate one archived positive target-evidence bundle.

    The environment record and its attachment hashes are not trusted as a
    semantic verifier.  This function independently parses every JSON
    attachment, binds all identities across the tree, and checks that both PNG
    artifacts are physical images rather than arbitrary hash-matching bytes.
    """

    if type(record) is not dict:
        raise EvidenceError("正向认证环境记录必须是 object")
    for key, expected in {
        "schema": "taiji-linux-environment-evidence/v2",
        "category_kind": "positive",
        "compatibility": "COMPATIBLE",
    }.items():
        if record.get(key) != expected:
            raise EvidenceError(f"正向认证环境记录 {key} 不合法")
    challenge = record.get("challenge_nonce")
    if type(challenge) is not str or not CHALLENGE_RE.fullmatch(challenge):
        raise EvidenceError("正向认证环境记录 challenge 不合法")
    session_id = validate_session_id(
        record.get("acceptance_session_id"),
        "正向认证 acceptance_session_id",
    )
    commitment = validate_sha256(
        record.get("machine_identity_commitment_sha256"),
        "正向认证 machine_identity_commitment_sha256",
    )
    fingerprint = validate_sha256(
        record.get("machine_fingerprint_sha256"),
        "正向认证 machine_fingerprint_sha256",
    )
    derived_fingerprint = hashlib.sha256(
        (challenge + "\0" + commitment).encode("utf-8")
    ).hexdigest()
    if fingerprint != derived_fingerprint:
        raise EvidenceError("正向认证机器 fingerprint 与 commitment 不一致")

    expected_pngs = CERTIFICATION_LARGE_PNG_BASENAMES
    expected_json = POSITIVE_CERTIFICATION_ATTACHMENT_BASENAMES - expected_pngs
    if type(json_attachment_payloads) is not dict or set(json_attachment_payloads) != expected_json:
        raise EvidenceError("正向认证 JSON 附件必须完整且封闭")
    if type(png_evidence) is not dict or set(png_evidence) != expected_pngs:
        raise EvidenceError("正向认证 PNG 附件必须由单 FD 流式验证且完整封闭")
    attachments = record.get("attachments")
    if type(attachments) is not list or len(attachments) != len(POSITIVE_CERTIFICATION_ATTACHMENT_BASENAMES):
        raise EvidenceError("正向认证附件清单不完整")
    declared_hashes: dict[str, str] = {}
    for attachment in attachments:
        if type(attachment) is not dict or set(attachment) != {"basename", "sha256"}:
            raise EvidenceError("正向认证 attachment 字段集合不合法")
        basename = attachment["basename"]
        if (
            type(basename) is not str
            or basename not in POSITIVE_CERTIFICATION_ATTACHMENT_BASENAMES
            or basename in declared_hashes
        ):
            raise EvidenceError("正向认证 attachment basename 不安全、未知或重复")
        digest = validate_sha256(attachment["sha256"], f"正向认证 {basename} SHA256")
        if basename in expected_pngs:
            evidence = png_evidence[basename]
            if not isinstance(evidence, ValidatedPngEvidence) or evidence.size > MAX_EVIDENCE_BYTES:
                raise EvidenceError(f"正向认证 {basename} PNG 验证元数据不合法")
            actual_digest = evidence.sha256
        else:
            payload = json_attachment_payloads[basename]
            if type(payload) is not bytes or not payload or len(payload) > MAX_JSON_BYTES:
                raise EvidenceError("正向认证 JSON attachment 实物不合法")
            actual_digest = hashlib.sha256(payload).hexdigest()
        if digest != actual_digest:
            raise EvidenceError(f"正向认证 {basename} 摘要与实物不一致")
        declared_hashes[basename] = digest
    if set(declared_hashes) != POSITIVE_CERTIFICATION_ATTACHMENT_BASENAMES:
        raise EvidenceError("正向认证附件清单未覆盖完整实物")

    target = parse_json_bytes(
        json_attachment_payloads["target-verification.json"],
        "正向认证 target-verification",
    )
    require_exact_keys(target, CANONICAL_TARGET_EVIDENCE_KEYS, "正向认证 target-verification")
    target_generated = _parsed_fresh_timestamp(
        target["generated_at_utc"],
        "target-verification.generated_at_utc",
    )
    identity_fields = (
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
        "machine_fingerprint_sha256",
        "os_id",
        "os_version",
        "desktop_environment",
    )
    for key in identity_fields:
        if target.get(key) != record.get(key):
            raise EvidenceError(f"target-verification.{key} 与环境记录不一致")
    for key, expected in {
        "schema": "taiji-linux-target-verification/v2",
        "evidence_type": "target-desktop-environment",
        "acceptance_session_id": session_id,
        "challenge_nonce": challenge,
        "installation_method": "desktop-double-click",
        "installation_method_evidence": "human-attestation",
        "installation_method_machine_observed": False,
    }.items():
        require_exact(target, key, expected)
    release_artifacts_sha256 = validate_sha256(
        target["release_artifacts_sha256"],
        "target-verification.release_artifacts_sha256",
    )
    if (
        expected_release_artifacts_sha256 is not None
        and release_artifacts_sha256 != validate_sha256(
            expected_release_artifacts_sha256,
            "expected release_artifacts_sha256",
        )
    ):
        raise EvidenceError("target-verification 未绑定当前交付清单")
    target_checks = target["checks"]
    if type(target_checks) is not dict:
        raise EvidenceError("target-verification checks 必须是 object")
    require_exact_keys(target_checks, TARGET_CHECK_KEYS, "target-verification checks")
    for key in TARGET_CHECK_KEYS:
        require_exact(target_checks, key, True)
    expected_record_checks = {"preflight", "install"} | TARGET_CHECK_KEYS
    record_checks = record.get("checks")
    if type(record_checks) is not dict:
        raise EvidenceError("正向认证环境记录 checks 必须是 object")
    require_exact_keys(record_checks, expected_record_checks, "正向认证环境记录 checks")
    if any(record_checks[key] != "PASS" for key in expected_record_checks):
        raise EvidenceError("正向认证环境记录未证明全部门禁 PASS")

    pointer_basenames = {
        "environment_observation": "environment-observation.json",
        "install_observation": "single-deb-install-observation.json",
        "install_method_attestation": "single-deb-install-method-attestation.json",
        "graphical_installer_evidence": "single-deb-graphical-installer.png",
        "driver_result": "desktop-driver-result.json",
        "screenshot": "desktop-app.png",
        "diagnostic": "taiji-support-bundle.json",
    }
    for field, basename in pointer_basenames.items():
        require_exact(target, field + "_basename", basename)
        require_exact(target, field + "_sha256", declared_hashes[basename])

    environment = parse_json_bytes(
        json_attachment_payloads["environment-observation.json"],
        "正向认证 environment-observation",
    )
    require_exact_keys(
        environment,
        CANONICAL_ENVIRONMENT_OBSERVATION_KEYS,
        "正向认证 environment-observation",
    )
    require_exact(environment, "schema", "taiji-linux-environment-observation/v1")
    for key in identity_fields:
        if key == "machine_fingerprint_sha256":
            continue
        if environment.get(key) != record.get(key):
            raise EvidenceError(f"environment-observation.{key} 与环境记录不一致")
    if environment["security_facts"] != record.get("security_facts"):
        raise EvidenceError("environment-observation security_facts 与环境记录不一致")
    if environment["checks"] != {"preflight": "PASS", "install": "PASS"}:
        raise EvidenceError("environment-observation 必须只声明 preflight/install PASS")
    if environment["attachments"] != []:
        raise EvidenceError("environment-observation seed 不得自行声明最终附件")

    observation_payload = json_attachment_payloads["single-deb-install-observation.json"]
    observation = parse_json_bytes(observation_payload, "正向认证 install observation")
    require_exact_keys(
        observation,
        CANONICAL_INSTALL_OBSERVATION_KEYS,
        "正向认证 install observation",
    )
    for key, expected in {
        "schema": "taiji.single-deb-install-observation/v2",
        "challenge_nonce": challenge,
        "machine_identity_commitment_sha256": commitment,
        "machine_fingerprint_sha256": fingerprint,
        "source_commit": record["source_commit"],
        "deb_observed_basename": record["deb_basename"],
        "deb_sha256": record["deb_sha256"],
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
        require_exact(observation, key, expected)
    manifest_sha256 = validate_sha256(
        observation["manifest_sha256"],
        "install observation manifest_sha256",
    )
    if (
        expected_manifest_sha256 is not None
        and manifest_sha256 != validate_sha256(expected_manifest_sha256, "expected manifest_sha256")
    ):
        raise EvidenceError("install observation 未绑定当前 manifest")
    for key in (
        "boot_fingerprint_sha256",
        "canonical_home_fingerprint_sha256",
        "user_state_paths_fingerprint_sha256",
    ):
        validate_sha256(observation[key], f"install observation {key}")
    if type(observation["target_uid"]) is not int or observation["target_uid"] < 0:
        raise EvidenceError("install observation target_uid 不合法")
    started = _parsed_fresh_timestamp(observation["started_at_utc"], "install observation started_at_utc")
    completed = _parsed_fresh_timestamp(observation["completed_at_utc"], "install observation completed_at_utc")
    generated = _parsed_fresh_timestamp(observation["generated_at_utc"], "install observation generated_at_utc")
    if not started <= completed <= generated <= target_generated:
        raise EvidenceError("正向认证安装观测与目标证据时间顺序不合法")
    transitions = observation["package_status_transitions"]
    if (
        type(transitions) is not list
        or not transitions
        or any(type(value) is not str for value in transitions)
        or transitions[0] != "not-installed"
        or transitions[-1] != "install ok installed"
    ):
        raise EvidenceError("正向认证安装状态迁移不合法")
    if type(observation["network_sample_interval_ms"]) is not int or observation["network_sample_interval_ms"] <= 0:
        raise EvidenceError("正向认证网络采样间隔不合法")
    if type(observation["network_sample_count"]) is not int or observation["network_sample_count"] < 2:
        raise EvidenceError("正向认证网络采样数不足")

    attestation = parse_json_bytes(
        json_attachment_payloads["single-deb-install-method-attestation.json"],
        "正向认证 install attestation",
    )
    attestation_args = argparse.Namespace(challenge=challenge)
    validate_install_method_attestation(
        target,
        observation,
        hashlib.sha256(observation_payload).hexdigest(),
        attestation,
        png_evidence["single-deb-graphical-installer.png"].sha256,
        attestation_args,
    )
    attested = _parsed_fresh_timestamp(attestation["generated_at_utc"], "install attestation generated_at_utc")
    if attested > target_generated:
        raise EvidenceError("正向认证目标证据早于安装人工见证")

    driver = parse_json_bytes(
        json_attachment_payloads["desktop-driver-result.json"],
        "正向认证 desktop driver",
    )
    driver_data = dict(target)
    driver_data.update(target_checks)
    driver_data["electron_executable_sha256"] = driver.get("electron_executable_sha256")
    driver_data["desktop_entry_sha256"] = driver.get("desktop_entry_sha256")
    if (
        expected_electron_executable_sha256 is not None
        and driver_data["electron_executable_sha256"]
        != validate_sha256(
            expected_electron_executable_sha256,
            "expected electron_executable_sha256",
        )
    ):
        raise EvidenceError("正向认证 driver 未绑定当前 Electron 可执行体")
    if (
        expected_desktop_entry_sha256 is not None
        and driver_data["desktop_entry_sha256"]
        != validate_sha256(expected_desktop_entry_sha256, "expected desktop_entry_sha256")
    ):
        raise EvidenceError("正向认证 driver 未绑定当前 desktop entry")
    driver_session = {
        "electron_pid": driver.get("electron_pid"),
        "js_error_count": driver.get("js_error_count"),
        "unexpected_http_failures": driver.get("unexpected_http_failures"),
        "checks": dict(target_checks),
    }
    validate_target_driver(driver_data, driver_session, driver)

    validate_support_bundle(json_attachment_payloads["taiji-support-bundle.json"])


def validate_target(
    data: dict[str, Any], evidence_path: Path, args: argparse.Namespace, deb_hash: str, version: str,
    release_artifacts_hash: str, electron_executable_hash: str, desktop_entry_hash: str,
    target_baseline_profile_id: str, target_baseline_sha256: str,
) -> None:
    require_exact_keys(data, TARGET_KEYS, evidence_path.name)
    for key, expected in {
        "schema_version": 2,
        "evidence_type": "target-desktop-verification",
        "application": "taiji-electron-desktop",
        "platform": "linux/amd64",
        "target_verified": True,
        "desktop_launch": True,
        "real_model_conversation": True,
        "attachment_flow": True,
        "window_close_exit": True,
        "diagnostic_export": True,
        "installed_package_version": version,
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
        "three_restart_cycles": True,
        "second_instance_focus": True,
        "model_configuration_state_consistent": True,
        "no_new_electron_core": True,
    }.items():
        require_exact(data, key, expected)
    validate_fresh_timestamp(data["generated_at_utc"], "generated_at_utc")
    validate_session_id(data["acceptance_session_id"], "acceptance_session_id")
    validate_challenge(data["challenge_nonce"], args.challenge)
    validate_sha256(data["machine_fingerprint_sha256"], "machine_fingerprint_sha256")
    require_exact(data, "electron_executable_sha256", electron_executable_hash)
    require_exact(data, "desktop_entry_sha256", desktop_entry_hash)
    require_choice(data, "os_id", {"kylin", "uos", "openkylin"})
    require_nonempty_string(data, "os_version")
    require_nonempty_string(data, "desktop_environment")
    validate_artifact_binding(
        data,
        args,
        deb_hash,
        release_artifacts_hash,
        target_baseline_profile_id,
        target_baseline_sha256,
    )
    session_path, session_payload, session_stat = validate_bound_file(
        data, evidence_path, "session_log_basename", "session_log_sha256", "桌面验收结构化会话"
    )
    screenshot_path, screenshot_png, screenshot_stat = validate_bound_png(
        data, evidence_path, "screenshot_basename", "screenshot_sha256", "桌面 App 截图"
    )
    diagnostic_path, diagnostic_payload, diagnostic_stat = validate_bound_file(
        data, evidence_path, "diagnostic_basename", "diagnostic_sha256", "桌面 App 诊断导出"
    )
    require_exact(data, "driver_result_basename", DRIVER_RESULT_BASENAME)
    driver_path, driver_payload, driver_stat = validate_bound_file(
        data,
        evidence_path,
        "driver_result_basename",
        "driver_result_sha256",
        "桌面 App 驱动原始结果",
    )
    require_exact(data, "install_observation_basename", INSTALL_OBSERVATION_BASENAME)
    observation_path, observation_payload, observation_stat = validate_bound_file(
        data,
        evidence_path,
        "install_observation_basename",
        "install_observation_sha256",
        "单 DEB 安装观察记录",
    )
    require_exact(
        data,
        "install_method_attestation_basename",
        INSTALL_METHOD_ATTESTATION_BASENAME,
    )
    attestation_path, attestation_payload, attestation_stat = validate_bound_file(
        data,
        evidence_path,
        "install_method_attestation_basename",
        "install_method_attestation_sha256",
        "桌面双击安装人工见证",
    )
    require_exact(
        data,
        "graphical_installer_evidence_basename",
        GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
    )
    graphical_path, graphical_png, graphical_stat = validate_bound_png(
        data,
        evidence_path,
        "graphical_installer_evidence_basename",
        "graphical_installer_evidence_sha256",
        "系统图形安装器证据截图",
    )
    if len(observation_payload) > MAX_JSON_BYTES or len(attestation_payload) > MAX_JSON_BYTES:
        raise EvidenceError("安装观察记录或人工见证超过 JSON 大小上限")
    if len(driver_payload) > MAX_JSON_BYTES:
        raise EvidenceError("桌面 App 驱动原始结果超过 JSON 大小上限")
    paths = {
        session_path.name,
        screenshot_path.name,
        diagnostic_path.name,
        driver_path.name,
        observation_path.name,
        attestation_path.name,
        graphical_path.name,
    }
    identities = {
        (session_stat.st_dev, session_stat.st_ino),
        (screenshot_stat.st_dev, screenshot_stat.st_ino),
        (diagnostic_stat.st_dev, diagnostic_stat.st_ino),
        (driver_stat.st_dev, driver_stat.st_ino),
        (observation_stat.st_dev, observation_stat.st_ino),
        (attestation_stat.st_dev, attestation_stat.st_ino),
        (graphical_stat.st_dev, graphical_stat.st_ino),
    }
    if len(paths) != 7 or len(identities) != 7:
        raise EvidenceError("桌面验收七个绑定证据必须是彼此独立的普通文件")
    session = parse_json_bytes(session_payload, "桌面 App 验收会话")
    validate_target_session(data, session, args, version)
    validate_target_driver(
        data,
        session,
        parse_json_bytes(driver_payload, "桌面 App 驱动原始结果"),
    )
    observation = parse_json_bytes(observation_payload, "单 DEB 安装观察记录")
    validate_install_observation(
        data,
        observation,
        args,
        deb_hash,
        target_baseline_profile_id,
        target_baseline_sha256,
    )
    validate_install_method_attestation(
        data,
        observation,
        hashlib.sha256(observation_payload).hexdigest(),
        parse_json_bytes(attestation_payload, "桌面双击安装人工见证"),
        graphical_png.sha256,
        args,
    )
    validate_support_bundle(diagnostic_payload)


RELEASE_EVIDENCE_V3_KEYS = {
    "schema",
    "evidence_type",
    "generated_at_utc",
    "challenge_nonce",
    "challenge_envelope",
    "source_commit",
    "version",
    "architecture",
    "deb_basename",
    "deb_sha256",
    "compatibility_policy_id",
    "compatibility_policy_sha256",
    "certification_set_basename",
    "certification_set_sha256",
    "certification_set_signature_basename",
    "certification_set_signature_sha256",
    "ci_evidence_basename",
    "ci_evidence_sha256",
    "maintainer",
    "customer_filename",
    "customer_folder_contract",
    "signing_public_key_fingerprint",
    "formal_gates",
} | TOOLCHAIN_MANIFEST_FIELDS
CI_EVIDENCE_KEYS = {
    "schema",
    "provider",
    "api_version",
    "repository",
    "workflow_id",
    "workflow_name",
    "workflow_path",
    "event",
    "head_branch",
    "head_sha",
    "run_id",
    "run_attempt",
    "run_status",
    "run_conclusion",
    "run_html_url",
    "run_created_at_utc",
    "run_updated_at_utc",
    "required_job_id",
    "required_job_name",
    "required_job_status",
    "required_job_conclusion",
    "required_job_html_url",
    "required_job_started_at_utc",
    "required_job_completed_at_utc",
    "required_step_name",
    "required_step_status",
    "required_step_conclusion",
    "collected_at_utc",
    "raw_run_basename",
    "raw_run_sha256",
    "raw_jobs_basename",
    "raw_jobs_sha256",
}
CI_SCHEMA_V2 = "taiji-github-ci-evidence/v2"
CI_PROVIDER = "github-actions-rest-api"
CI_API_VERSION = "2022-11-28"
CI_REPOSITORY = "bwbcomeon-maker/taijiAgentv1.0"
CI_WORKFLOW_NAME = "Pull Request CI"
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
CI_EVENT = "push"
CI_BRANCH = "main"
CI_JOB_NAME = "CI Gate"
CI_STEP_NAME = "Require every selected job to pass"
CI_EVIDENCE_BASENAME = "github-ci-evidence.json"
CI_RAW_RUN_BASENAME = "github-ci-run-response.json"
CI_RAW_JOBS_BASENAME = "github-ci-jobs-response.json"
CI_MAX_RUN_BYTES = 2 * 1024 * 1024
CI_MAX_JOBS_BYTES = 8 * 1024 * 1024
CI_MAX_AGE = timedelta(days=7)
RELEASE_FORMAL_GATES = {
    "candidate_deb_unchanged": "PASS",
    "canonical_policy": "PASS",
    "certification_set": "PASS",
    "certification_signature": "PASS",
    "github_ci_gate": "PASS",
    "manifest_binding": "PASS",
}

CERTIFICATION_SET_SCHEMA_V1 = "taiji-linux-certification-set/v1"
CERTIFICATION_SET_KEYS_V1 = {
    "schema",
    "generated_at_utc",
    "challenge_nonce",
    "challenge_envelope",
    "source_commit",
    "version",
    "architecture",
    "deb_basename",
    "deb_sha256",
    "compatibility_policy_id",
    "compatibility_policy_sha256",
    "certification_profile",
    "offline_rehearsal",
    "environments",
    "negative_boundaries",
}


def _load_environment_contract_for_certification() -> Any:
    contract_path = Path(__file__).resolve().parents[1] / "tools/taiji-desktop-acceptance/assemble-target-evidence.py"
    if not contract_path.is_file():
        contract_path = Path(__file__).resolve().with_name("assemble-target-evidence.py")
    if not contract_path.is_file():
        raise EvidenceError("认证集验证缺少环境证据 contract")
    spec = importlib.util.spec_from_file_location("taiji_release_environment_contract", contract_path)
    if spec is None or spec.loader is None:
        raise EvidenceError("无法加载认证集环境证据 contract")
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    return contract


@dataclass
class _OpenedCertificationCategory:
    record_root_fd: builtins.int
    category_id: builtins.str
    category_fd: builtins.int
    category_stat: builtins.object
    record: builtins.dict
    record_payload: builtins.bytes
    json_payloads: builtins.dict
    png_evidence: builtins.dict
    expected_entries: builtins.set
    held_files: builtins.list

    def verify(self) -> None:
        if set(os.listdir(self.category_fd)) != self.expected_entries:
            raise EvidenceError("认证集环境目录必须且只能包含记录声明的 attachments")
        for descriptor, before, basename in self.held_files:
            if regular_file_identity(os.fstat(descriptor)) != regular_file_identity(before):
                raise EvidenceError(f"认证集环境 attachment {basename} 读取期间发生变化")
            current = os.stat(
                basename,
                dir_fd=self.category_fd,
                follow_symlinks=False,
            )
            if regular_file_identity(current) != regular_file_identity(before):
                raise EvidenceError(f"认证集环境 attachment {basename} identity changed")
        current_category = os.stat(
            self.category_id,
            dir_fd=self.record_root_fd,
            follow_symlinks=False,
        )
        if regular_file_identity(current_category) != regular_file_identity(self.category_stat):
            raise EvidenceError("认证集环境目录 identity changed")
        if regular_file_identity(os.fstat(self.category_fd)) != regular_file_identity(
            self.category_stat
        ):
            raise EvidenceError("认证集环境目录读取期间发生变化")

    def close(self) -> None:
        for descriptor, _before, _basename in self.held_files:
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.held_files.clear()
        try:
            os.close(self.category_fd)
        except OSError:
            pass

    def run(self, callback: Any) -> dict[str, Any]:
        try:
            result = callback(self)
            self.verify()
            return result
        finally:
            self.close()


def _read_descriptor_bytes(
    descriptor: int,
    file_stat: os.stat_result,
    label: str,
    *,
    limit: int,
) -> bytes:
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_nlink != 1
        or file_stat.st_size <= 0
        or file_stat.st_size > limit
    ):
        raise EvidenceError(f"{label} 实物或大小不合法")
    chunks = []
    remaining = file_stat.st_size
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            raise EvidenceError(f"{label} 读取期间被截断")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise EvidenceError(f"{label} 读取期间增长")
    return b"".join(chunks)


def _open_certification_category(
    record_root_fd: int,
    category_id: str,
) -> _OpenedCertificationCategory:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        category_fd = os.open(category_id, directory_flags, dir_fd=record_root_fd)
    except OSError as exc:
        raise EvidenceError("认证集环境目录无法安全打开") from exc
    category_stat = os.fstat(category_fd)
    held_files: list[tuple[int, os.stat_result, str]] = []
    try:
        record_fd = os.open("environment-evidence.json", file_flags, dir_fd=category_fd)
        record_stat = os.fstat(record_fd)
        held_files.append((record_fd, record_stat, "environment-evidence.json"))
        record_payload = _read_descriptor_bytes(
            record_fd,
            record_stat,
            "认证集环境记录",
            limit=MAX_JSON_BYTES,
        )
        record = parse_json_bytes(record_payload, "认证集环境记录")
        attachments = record.get("attachments")
        if type(attachments) is not list:
            raise EvidenceError("认证集环境记录 attachments 必须是数组")
        expected_entries = {"environment-evidence.json"}
        json_payloads: dict[str, bytes] = {}
        png_evidence: dict[str, ValidatedPngEvidence] = {}
        for attachment in attachments:
            if type(attachment) is not dict or set(attachment) != {"basename", "sha256"}:
                raise EvidenceError("认证集环境 attachment 字段集合不合法")
            basename = attachment["basename"]
            if (
                type(basename) is not str
                or not basename
                or Path(basename).name != basename
                or "/" in basename
                or "\\" in basename
                or basename in expected_entries
            ):
                raise EvidenceError("认证集环境 attachment basename 路径不安全或重复")
            expected_entries.add(basename)
            try:
                descriptor = os.open(basename, file_flags, dir_fd=category_fd)
            except OSError as exc:
                raise EvidenceError("认证集环境 attachment 无法安全打开") from exc
            file_stat = os.fstat(descriptor)
            held_files.append((descriptor, file_stat, basename))
            if basename in CERTIFICATION_LARGE_PNG_BASENAMES:
                metadata = validate_png_descriptor(
                    descriptor,
                    file_stat,
                    f"认证集环境 {basename}",
                )
                actual_digest = metadata.sha256
                png_evidence[basename] = metadata
            else:
                payload = _read_descriptor_bytes(
                    descriptor,
                    file_stat,
                    "认证集环境 attachment",
                    limit=MAX_JSON_BYTES,
                )
                actual_digest = hashlib.sha256(payload).hexdigest()
                json_payloads[basename] = payload
            declared_digest = validate_sha256(
                attachment["sha256"],
                "认证集环境 attachment SHA256",
            )
            if declared_digest != actual_digest:
                raise EvidenceError("认证集环境 attachment 摘要与实物不一致")
        return _OpenedCertificationCategory(
            record_root_fd=record_root_fd,
            category_id=category_id,
            category_fd=category_fd,
            category_stat=category_stat,
            record=record,
            record_payload=record_payload,
            json_payloads=json_payloads,
            png_evidence=png_evidence,
            expected_entries=expected_entries,
            held_files=held_files,
        )
    except Exception:
        for descriptor, _before, _basename in held_files:
            try:
                os.close(descriptor)
            except OSError:
                pass
        os.close(category_fd)
        raise


def _validate_certification_set_v1_inner(
    data: dict[str, Any],
    evidence_path: Path,
    args: argparse.Namespace,
    binding: BuildBinding,
) -> None:
    """Validate the unsigned, closed six-positive/six-negative certification set."""
    require_exact_keys(data, CERTIFICATION_SET_KEYS_V1, "认证集")
    require_exact(data, "schema", CERTIFICATION_SET_SCHEMA_V1)
    validate_fresh_timestamp(data["generated_at_utc"], "认证集 generated_at_utc")
    validate_challenge(data["challenge_nonce"], args.challenge)
    for key, expected in {
        "source_commit": binding.source_commit,
        "version": binding.version,
        "architecture": "amd64",
        "deb_basename": binding.deb_basename,
        "deb_sha256": binding.deb_sha256,
        "compatibility_policy_id": binding.compatibility_policy_id,
        "compatibility_policy_sha256": binding.compatibility_policy_sha256,
    }.items():
        require_exact(data, key, expected)
    validate_sha256(data["deb_sha256"], "认证集 deb_sha256")
    validate_sha256(data["compatibility_policy_sha256"], "认证集 compatibility_policy_sha256")
    expected_policy_id, expected_policy_sha = canonical_policy_identity()
    require_exact(data, "compatibility_policy_id", expected_policy_id)
    require_exact(data, "compatibility_policy_sha256", expected_policy_sha)
    profile = data["certification_profile"]
    if type(profile) is not dict or set(profile) != {
        "matrix_schema", "matrix_sha256", "positive_category_count", "negative_boundary_count"
    }:
        raise EvidenceError("认证集 certification_profile 字段集合不合法")
    require_exact(profile, "matrix_schema", "taiji-linux-certification-matrix/v2")
    validate_sha256(profile["matrix_sha256"], "认证集 matrix_sha256")
    if profile["positive_category_count"] != 6 or profile["negative_boundary_count"] != 6:
        raise EvidenceError("认证集 certification_profile 必须覆盖六正六负类别")
    matrix_path = getattr(args, "matrix", None)
    if matrix_path is None:
        raise EvidenceError("认证集验证需要 --matrix")
    matrix_payload, _ = read_regular_bytes(Path(matrix_path), "认证矩阵")
    if hashlib.sha256(matrix_payload).hexdigest() != profile["matrix_sha256"]:
        raise EvidenceError("认证集 matrix_sha256 与当前认证矩阵不一致")
    matrix = parse_json_bytes(matrix_payload, "认证矩阵")
    contract = _load_environment_contract_for_certification()
    contract.validate_certification_matrix(matrix)
    categories = {
        item["id"]: item for item in matrix["positive_categories"] + matrix["negative_boundaries"]
    }
    environments = data["environments"]
    negative = data["negative_boundaries"]
    if type(environments) is not list or type(negative) is not list:
        raise EvidenceError("认证集 environments/negative_boundaries 必须是数组")
    if len(environments) != 6 or len(negative) != 6:
        raise EvidenceError("认证集必须包含六个正向和六个负向类别")
    if {item.get("category_id") for item in environments} != set(matrix["minimum_application_only_categories"]) | {
        "kylin-min-ukui", "kylin-hardened", "uos-min-dde"
    }:
        raise EvidenceError("认证集正向类别集合不完整")
    if {item.get("category_id") for item in negative} != {
        item["id"] for item in matrix["negative_boundaries"]
    }:
        raise EvidenceError("认证集负向边界集合不完整")
    record_root = evidence_path.parent / "records"
    _safe_record_root = record_root
    if not _safe_record_root.is_dir() or _safe_record_root.is_symlink():
        raise EvidenceError("认证集 records 目录缺失或不安全")
    manifest_sha256: str | None = None
    manifest_path = getattr(args, "manifest", None)
    if manifest_path is not None:
        manifest_payload, _ = read_regular_bytes(Path(manifest_path), "认证集当前 manifest")
        manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()

    expected_record_root_entries = {
        item["id"] for item in matrix["positive_categories"] + matrix["negative_boundaries"]
    }

    def validate_all_records() -> list[dict[str, Any]]:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            record_root_fd = os.open(str(record_root), directory_flags)
        except OSError as exc:
            raise EvidenceError("认证集 records 根目录无法安全打开") from exc
        root_stat = os.fstat(record_root_fd)

        def validate_summary(
            item: Any,
            expected_kind: str,
            expected_compatibility: str,
        ) -> dict[str, Any]:
            if type(item) is not dict or set(item) != {
                "category_id", "compatibility", "record_basename", "record_sha256"
            }:
                raise EvidenceError("认证集类别摘要字段集合不合法")
            category_id = item["category_id"]
            if category_id not in categories or categories[category_id]["kind"] != expected_kind:
                raise EvidenceError("认证集类别摘要 category_id 不在矩阵中")
            require_exact(item, "compatibility", expected_compatibility)
            basename = item["record_basename"]
            expected_basename = f"records/{category_id}/environment-evidence.json"
            if basename != expected_basename:
                raise EvidenceError("认证集记录 basename 发生路径逃逸")
            bundle = _open_certification_category(record_root_fd, category_id)

            def validate_opened(opened: _OpenedCertificationCategory) -> dict[str, Any]:
                require_exact(
                    item,
                    "record_sha256",
                    hashlib.sha256(opened.record_payload).hexdigest(),
                )
                record = opened.record
                contract.validate_environment_record(record, matrix)
                require_exact(record, "category_id", category_id)
                require_exact(record, "category_kind", expected_kind)
                require_exact(record, "challenge_nonce", data["challenge_nonce"])
                for key, expected in {
                    "source_commit": binding.source_commit,
                    "version": binding.version,
                    "architecture": binding.architecture,
                    "deb_basename": binding.deb_basename,
                    "deb_sha256": binding.deb_sha256,
                    "compatibility_policy_id": binding.compatibility_policy_id,
                    "compatibility_policy_sha256": binding.compatibility_policy_sha256,
                }.items():
                    if record.get(key) != expected:
                        raise EvidenceError(
                            f"认证集环境记录 {key} 与顶层 BuildBinding 不一致"
                        )
                if expected_kind == "positive":
                    required = set(categories[category_id]["required_business_checks"]) | set(
                        categories[category_id]["required_lifecycle_checks"]
                    )
                    if any(record["checks"].get(key) != "PASS" for key in required):
                        raise EvidenceError("认证集正向记录必须所有检查 PASS")
                    expected_delivery_hash = binding.delivery_inventory_sha256 or None
                    expected_electron_hash = (
                        binding.electron_executable_sha256
                        if binding.electron_executable_sha256 != "0" * 64
                        else None
                    )
                    expected_desktop_hash = (
                        binding.desktop_entry_sha256
                        if binding.desktop_entry_sha256 != "0" * 64
                        else None
                    )
                    validate_positive_certification_bundle(
                        record,
                        opened.json_payloads,
                        opened.png_evidence,
                        expected_release_artifacts_sha256=expected_delivery_hash,
                        expected_manifest_sha256=manifest_sha256,
                        expected_electron_executable_sha256=expected_electron_hash,
                        expected_desktop_entry_sha256=expected_desktop_hash,
                    )
                else:
                    try:
                        contract.validate_negative_preflight_attachment(
                            record,
                            matrix,
                            opened.json_payloads["preflight-result.json"],
                        )
                        contract.validate_negative_business_data_attachment(
                            record,
                            matrix,
                            opened.json_payloads["business-data-inventory.json"],
                        )
                    except Exception as exc:
                        raise EvidenceError(str(exc)) from exc
                return record

            return bundle.run(validate_opened)

        try:
            if set(os.listdir(record_root_fd)) != expected_record_root_entries:
                raise EvidenceError("认证集 records 根目录必须严格封闭")
            validated = [
                validate_summary(item, "positive", "CERTIFIED") for item in environments
            ] + [
                validate_summary(item, "negative", "BLOCKED") for item in negative
            ]
            try:
                contract.validate_environment_records(validated, matrix)
            except Exception as exc:
                raise EvidenceError(str(exc)) from exc
            if set(os.listdir(record_root_fd)) != expected_record_root_entries:
                raise EvidenceError("认证集 records 根目录 closure changed")
            current_root = record_root.lstat()
            if (
                regular_file_identity(os.fstat(record_root_fd))
                != regular_file_identity(root_stat)
                or regular_file_identity(current_root) != regular_file_identity(root_stat)
            ):
                raise EvidenceError("认证集 records 根目录 identity changed")
            return validated
        finally:
            os.close(record_root_fd)

    validated_records = validate_all_records()
    offline = data["offline_rehearsal"]
    if type(offline) is not dict or set(offline) != {
        "directory_basename",
        "evidence_basename",
        "evidence_sha256",
        "files",
        "inventory_sha256",
        "status",
    }:
        raise EvidenceError("认证集 offline_rehearsal 字段集合不合法")
    require_exact(offline, "status", "PASS")
    require_exact(offline, "directory_basename", "offline-rehearsal")
    require_exact(offline, "evidence_basename", "offline-install-rehearsal.json")
    validate_sha256(offline["evidence_sha256"], "认证集 offline evidence SHA256")
    validate_sha256(offline["inventory_sha256"], "认证集 offline inventory SHA256")
    offline_files = offline["files"]
    if type(offline_files) is not list or len(offline_files) < 2:
        raise EvidenceError("认证集 offline files 必须覆盖完整离线演练实物")
    offline_root = evidence_path.parent / "offline-rehearsal"
    if not offline_root.is_dir() or offline_root.is_symlink():
        raise EvidenceError("认证集 offline 目录缺失或不安全")

    offline_evidence_path = offline_root / offline["evidence_basename"]
    offline_payload, _ = read_regular_bytes(
        offline_evidence_path,
        "认证集 offline 主证据",
    )
    require_exact(
        offline,
        "evidence_sha256",
        hashlib.sha256(offline_payload).hexdigest(),
    )
    offline_data = parse_json_bytes(offline_payload, "认证集 offline 主证据")
    previous_release = offline_data.get("previous_release")
    if type(previous_release) is not dict:
        raise EvidenceError("认证集 offline 主证据缺少 previous_release")
    previous_deb_basename = previous_release.get("deb_basename")
    if (
        type(previous_deb_basename) is not str
        or not previous_deb_basename
        or Path(previous_deb_basename).name != previous_deb_basename
        or "/" in previous_deb_basename
        or "\\" in previous_deb_basename
        or offline_data.get("previous_deb_basename") != previous_deb_basename
    ):
        raise EvidenceError("认证集 offline 主证据 previous DEB basename 不唯一或不安全")

    expected_offline_names: set[str] = set()
    for index, item in enumerate(offline_files):
        if type(item) is not dict or set(item) != {"basename", "sha256", "size"}:
            raise EvidenceError(f"认证集 offline files[{index}] 字段集合不合法")
        basename = item["basename"]
        if (
            type(basename) is not str
            or not basename
            or Path(basename).name != basename
            or "/" in basename
            or "\\" in basename
            or basename in expected_offline_names
        ):
            raise EvidenceError("认证集 offline 文件名不安全或重复")
        expected_offline_names.add(basename)
        offline_file = offline_root / basename
        if basename == previous_deb_basename:
            actual_sha256, file_stat = sha256_bounded_stable_regular_file(
                offline_file,
                f"认证集 offline N-1 DEB {basename}",
                limit=MAX_PREVIOUS_RELEASE_DEB_BYTES,
            )
        else:
            payload, file_stat = read_regular_bytes(
                offline_file,
                f"认证集 offline 实物 {basename}",
                limit=MAX_EVIDENCE_BYTES,
            )
            actual_sha256 = hashlib.sha256(payload).hexdigest()
        if type(item["size"]) is not int or item["size"] != file_stat.st_size:
            raise EvidenceError("认证集 offline 实物 size 不一致")
        require_exact(item, "sha256", actual_sha256)
    if previous_deb_basename not in expected_offline_names:
        raise EvidenceError("认证集 offline 目录缺少 previous DEB 实物")
    try:
        actual_offline_names = {entry.name for entry in offline_root.iterdir()}
    except OSError as exc:
        raise EvidenceError("认证集 offline 目录无法遍历") from exc
    if actual_offline_names != expected_offline_names:
        raise EvidenceError("认证集 offline 目录与归档清单不一致")
    inventory_payload = json.dumps(
        offline_files,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    require_exact(
        offline,
        "inventory_sha256",
        hashlib.sha256(inventory_payload).hexdigest(),
    )
    offline_args = argparse.Namespace(
        challenge=args.challenge,
        source_commit=binding.source_commit,
        deb=Path(binding.deb_basename),
    )
    validate_offline_evidence_v1(
        offline_data,
        offline_evidence_path,
        offline_args,
        binding,
        require_lifecycle=True,
    )


def validate_certification_set_v1(
    data: dict[str, Any],
    evidence_path: Path,
    args: argparse.Namespace,
    binding: BuildBinding,
) -> None:
    """Validate a certification set in its signed challenge time domain."""

    helper = _load_challenge_helper()
    envelope = data.get("challenge_envelope")
    if type(envelope) is not dict:
        raise EvidenceError("认证集缺少 canonical challenge_envelope")
    try:
        helper.verify_envelope(
            envelope,
            purpose="certification",
            source_commit=binding.source_commit,
            deb_basename=binding.deb_basename,
            deb_sha256=binding.deb_sha256,
            require_active=bool(
                getattr(args, "pre_sign", False)
                or getattr(args, "require_active_challenge", False)
            ),
            evidence_times=(data.get("generated_at_utc"),),
        )
        issued, expires = helper.validate_structure(envelope)
        reference_time = helper._parse_utc(
            data.get("generated_at_utc"),
            "generated_at_utc",
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise EvidenceError(str(exc)) from exc
    if data.get("challenge_nonce") != envelope["nonce"]:
        raise EvidenceError("认证集 challenge_nonce 与 challenge_envelope 不一致")
    supplied = getattr(args, "challenge", "")
    if supplied and supplied != envelope["nonce"]:
        raise EvidenceError("认证集外部 challenge 与 challenge_envelope 不一致")
    values = vars(args).copy()
    values["challenge"] = envelope["nonce"]
    normalized_args = argparse.Namespace(**values)
    token = _CHALLENGE_TIME_WINDOW.set((issued, expires))
    reference_token = _SIGNED_EVIDENCE_REFERENCE_TIME.set(reference_time)
    try:
        _validate_certification_set_v1_inner(
            data,
            evidence_path,
            normalized_args,
            binding,
        )
    finally:
        _SIGNED_EVIDENCE_REFERENCE_TIME.reset(reference_token)
        _CHALLENGE_TIME_WINDOW.reset(token)


def _ci_file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_ci_regular_with_identity(
    path: Path,
    label: str,
    maximum: int,
) -> tuple[bytes, tuple[int, ...]]:
    payload, opened = read_regular_bytes(path, label, limit=maximum)
    try:
        current = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"{label} 读取后不可用") from exc
    if _ci_file_identity(opened) != _ci_file_identity(current):
        raise EvidenceError(f"{label} 读取期间发生变化")
    return payload, _ci_file_identity(opened)


def _parse_ci_timestamp_at(value: Any, label: str, now: datetime) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise EvidenceError(f"GitHub CI {label} 必须是 UTC ISO8601 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError(f"GitHub CI {label} 必须是 UTC ISO8601 时间") from exc
    if parsed.tzinfo is None or parsed > now or now - parsed > CI_MAX_AGE:
        raise EvidenceError(f"GitHub CI {label} 必须是最近 7 天内的当前证据")
    return parsed


def _require_ci_positive_integer(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise EvidenceError(f"GitHub CI {label} 必须是正整数")
    return value


def _require_ci_repository(value: Any, label: str) -> None:
    if type(value) is not dict or value.get("full_name") != CI_REPOSITORY:
        raise EvidenceError(f"GitHub CI {label} 不是固定仓库")


def validate_github_ci_evidence_bundle(
    evidence_path: Path,
    source_commit: str,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Validate one immutable normalized/raw/raw GitHub CI v2 trio.

    All three files are opened without following links, their identities are
    retained across the complete cross-file validation, and the raw GitHub
    responses are re-derived into the normalized exact contract.
    """

    evidence_path = Path(evidence_path)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise EvidenceError("GitHub CI 当前时间必须包含时区")
    current_time = current_time.astimezone(timezone.utc)
    if type(source_commit) is not str or FULL_COMMIT_RE.fullmatch(source_commit) is None:
        raise EvidenceError("GitHub CI source commit 必须是完整小写 commit")
    if not evidence_path.is_absolute() or evidence_path.name != CI_EVIDENCE_BASENAME:
        raise EvidenceError("GitHub CI 证据必须是绝对路径 github-ci-evidence.json")
    parent = evidence_path.parent
    require_safe_parent(evidence_path, "GitHub CI 证据")

    paths = {
        "evidence": evidence_path,
        "run": parent / CI_RAW_RUN_BASENAME,
        "jobs": parent / CI_RAW_JOBS_BASENAME,
    }
    payloads: dict[str, bytes] = {}
    identities: dict[str, tuple[int, ...]] = {}
    for key, label, maximum in (
        ("evidence", "GitHub CI normalized evidence", MAX_JSON_BYTES),
        ("run", "GitHub CI raw run response", CI_MAX_RUN_BYTES),
        ("jobs", "GitHub CI raw jobs response", CI_MAX_JOBS_BYTES),
    ):
        payloads[key], identities[key] = _read_ci_regular_with_identity(
            paths[key], label, maximum
        )

    evidence = parse_json_bytes(payloads["evidence"], "GitHub CI normalized evidence")
    run = parse_json_bytes(payloads["run"], "GitHub CI raw run response")
    jobs_payload = parse_json_bytes(payloads["jobs"], "GitHub CI raw jobs response")
    require_exact_keys(evidence, CI_EVIDENCE_KEYS, "GitHub CI normalized evidence")
    if evidence.get("raw_run_basename") != CI_RAW_RUN_BASENAME:
        raise EvidenceError("GitHub CI raw run basename 发生路径逃逸或替换")
    if evidence.get("raw_jobs_basename") != CI_RAW_JOBS_BASENAME:
        raise EvidenceError("GitHub CI raw jobs basename 发生路径逃逸或替换")
    raw_run_hash = hashlib.sha256(payloads["run"]).hexdigest()
    raw_jobs_hash = hashlib.sha256(payloads["jobs"]).hexdigest()
    if evidence.get("raw_run_sha256") != raw_run_hash:
        raise EvidenceError("GitHub CI raw run SHA256 与实物不一致")
    if evidence.get("raw_jobs_sha256") != raw_jobs_hash:
        raise EvidenceError("GitHub CI raw jobs SHA256 与实物不一致")

    run_id = _require_ci_positive_integer(run.get("id"), "run id")
    expected_run = {
        "name": CI_WORKFLOW_NAME,
        "path": CI_WORKFLOW_PATH,
        "event": CI_EVENT,
        "status": "completed",
        "conclusion": "success",
        "head_sha": source_commit,
        "head_branch": CI_BRANCH,
        "html_url": f"https://github.com/{CI_REPOSITORY}/actions/runs/{run_id}",
    }
    for key, expected in expected_run.items():
        if run.get(key) != expected:
            raise EvidenceError(f"GitHub CI raw run {key} 与固定发布合同不一致")
    _require_ci_repository(run.get("repository"), "repository")
    _require_ci_repository(run.get("head_repository"), "head_repository")
    run_attempt = _require_ci_positive_integer(run.get("run_attempt"), "run_attempt")
    workflow_id = _require_ci_positive_integer(run.get("workflow_id"), "workflow_id")
    run_created = _parse_ci_timestamp_at(run.get("created_at"), "run created_at", current_time)
    run_updated = _parse_ci_timestamp_at(run.get("updated_at"), "run updated_at", current_time)
    if run_updated < run_created:
        raise EvidenceError("GitHub CI run updated_at 早于 created_at")

    jobs = jobs_payload.get("jobs")
    total_count = jobs_payload.get("total_count")
    if (
        type(total_count) is not int
        or total_count < 0
        or type(jobs) is not list
        or total_count != len(jobs)
        or total_count > 100
    ):
        raise EvidenceError("GitHub CI raw jobs 不完整或已分页")
    required_jobs = [
        item for item in jobs
        if type(item) is dict and item.get("name") == CI_JOB_NAME
    ]
    if len(required_jobs) != 1:
        raise EvidenceError("GitHub CI 必须且只能存在一个 CI Gate job")
    job = required_jobs[0]
    expected_job = {
        "run_id": run_id,
        "run_attempt": run_attempt,
        "workflow_name": CI_WORKFLOW_NAME,
        "name": CI_JOB_NAME,
        "head_sha": source_commit,
        "status": "completed",
        "conclusion": "success",
    }
    for key, expected in expected_job.items():
        if job.get(key) != expected:
            raise EvidenceError(f"GitHub CI Gate job {key} 与固定发布合同不一致")
    job_id = _require_ci_positive_integer(job.get("id"), "required job id")
    job_url = f"https://github.com/{CI_REPOSITORY}/actions/runs/{run_id}/job/{job_id}"
    if job.get("html_url") != job_url:
        raise EvidenceError("GitHub CI Gate job URL 与固定发布合同不一致")
    job_started = _parse_ci_timestamp_at(job.get("started_at"), "job started_at", current_time)
    job_completed = _parse_ci_timestamp_at(job.get("completed_at"), "job completed_at", current_time)
    if job_completed < job_started:
        raise EvidenceError("GitHub CI Gate completed_at 早于 started_at")
    steps = job.get("steps")
    if type(steps) is not list:
        raise EvidenceError("GitHub CI Gate steps 不可用")
    required_steps = [
        item for item in steps
        if type(item) is dict and item.get("name") == CI_STEP_NAME
    ]
    if len(required_steps) != 1:
        raise EvidenceError("GitHub CI Gate 必须且只能存在固定合同 step")
    step = required_steps[0]
    if step.get("status") != "completed" or step.get("conclusion") != "success":
        raise EvidenceError("GitHub CI Gate 固定合同 step 未成功")

    collected = _parse_ci_timestamp_at(
        evidence.get("collected_at_utc"), "collected_at_utc", current_time
    )
    if collected < max(run_updated, job_completed):
        raise EvidenceError("GitHub CI collected_at_utc 早于 run/job 完成时间")
    expected_evidence = {
        "schema": CI_SCHEMA_V2,
        "provider": CI_PROVIDER,
        "api_version": CI_API_VERSION,
        "repository": CI_REPOSITORY,
        "workflow_id": workflow_id,
        "workflow_name": CI_WORKFLOW_NAME,
        "workflow_path": CI_WORKFLOW_PATH,
        "event": CI_EVENT,
        "head_branch": CI_BRANCH,
        "head_sha": source_commit,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_status": "completed",
        "run_conclusion": "success",
        "run_html_url": expected_run["html_url"],
        "run_created_at_utc": run["created_at"],
        "run_updated_at_utc": run["updated_at"],
        "required_job_id": job_id,
        "required_job_name": CI_JOB_NAME,
        "required_job_status": "completed",
        "required_job_conclusion": "success",
        "required_job_html_url": job_url,
        "required_job_started_at_utc": job["started_at"],
        "required_job_completed_at_utc": job["completed_at"],
        "required_step_name": CI_STEP_NAME,
        "required_step_status": "completed",
        "required_step_conclusion": "success",
        "collected_at_utc": evidence["collected_at_utc"],
        "raw_run_basename": CI_RAW_RUN_BASENAME,
        "raw_run_sha256": raw_run_hash,
        "raw_jobs_basename": CI_RAW_JOBS_BASENAME,
        "raw_jobs_sha256": raw_jobs_hash,
    }
    if evidence != expected_evidence:
        raise EvidenceError("GitHub CI normalized v2 无法由两个 raw response 精确重建")

    for key, path in paths.items():
        try:
            current = path.lstat()
        except OSError as exc:
            raise EvidenceError("GitHub CI 三件套在验证期间消失") from exc
        if _ci_file_identity(current) != identities[key]:
            raise EvidenceError("GitHub CI 三件套在跨文件验证期间发生变化")
    return {
        "evidence_basename": CI_EVIDENCE_BASENAME,
        "evidence_sha256": hashlib.sha256(payloads["evidence"]).hexdigest(),
        "raw_run_basename": CI_RAW_RUN_BASENAME,
        "raw_run_sha256": raw_run_hash,
        "raw_jobs_basename": CI_RAW_JOBS_BASENAME,
        "raw_jobs_sha256": raw_jobs_hash,
        "source_commit": source_commit,
    }


def validate_ci_evidence_binding(
    data: dict[str, Any],
    evidence_path: Path,
    binding: BuildBinding,
) -> None:
    ci_basename = data.get("ci_evidence_basename")
    if ci_basename != CI_EVIDENCE_BASENAME:
        raise EvidenceError("release evidence 必须绑定固定 GitHub CI v2 basename")
    ci_path = evidence_path.parent / CI_EVIDENCE_BASENAME
    result = validate_github_ci_evidence_bundle(
        ci_path,
        binding.source_commit,
        now=_SIGNED_EVIDENCE_REFERENCE_TIME.get(),
    )
    if data.get("ci_evidence_sha256") != result["evidence_sha256"]:
        raise EvidenceError("release evidence GitHub CI v2 SHA256 绑定不一致")


def _validate_release_evidence_v3_inner(
    data: dict[str, Any],
    evidence_path: Path,
    args: argparse.Namespace,
    binding: BuildBinding,
) -> None:
    """Validate the current publication evidence envelope.

    Certification-set semantics are intentionally left to the later
    certification task.  This gate nevertheless freezes every candidate DEB
    and policy identity and rejects all target-bound v2 fields now.
    """

    require_exact_keys(data, RELEASE_EVIDENCE_V3_KEYS, "release evidence v3")
    reject_target_baseline_fields(data, "release evidence v3")
    require_exact(data, "schema", RELEASE_EVIDENCE_SCHEMA_V3)
    require_exact(data, "evidence_type", "single-deb-publication")
    validate_fresh_timestamp(data["generated_at_utc"], "generated_at_utc")
    validate_challenge(data["challenge_nonce"], args.challenge or "")
    comparisons = {
        "source_commit": binding.source_commit,
        "version": binding.version,
        "architecture": binding.architecture,
        "deb_basename": binding.deb_basename,
        "deb_sha256": binding.deb_sha256,
        "compatibility_policy_id": binding.compatibility_policy_id,
        "compatibility_policy_sha256": binding.compatibility_policy_sha256,
        "customer_filename": binding.deb_basename,
        "customer_folder_contract": "exactly-one-deb",
        **{field: getattr(binding, field) for field in TOOLCHAIN_MANIFEST_FIELDS},
    }
    for key, expected in comparisons.items():
        require_exact(data, key, expected)
    validate_sha256(data["deb_sha256"], "deb_sha256")
    validate_sha256(data["compatibility_policy_sha256"], "compatibility_policy_sha256")
    if not POLICY_ID_RE.fullmatch(data["compatibility_policy_id"]):
        raise EvidenceError("release evidence v3 compatibility_policy_id 格式不合法")
    expected_policy_id, expected_policy_sha256 = canonical_policy_identity()
    require_exact(data, "compatibility_policy_id", expected_policy_id)
    require_exact(data, "compatibility_policy_sha256", expected_policy_sha256)
    for key in (
        "certification_set_sha256",
        "certification_set_signature_sha256",
        "ci_evidence_sha256",
        "signing_public_key_fingerprint",
    ):
        validate_sha256(data[key], key)
    require_exact(
        data,
        "signing_public_key_fingerprint",
        PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT,
    )
    for key in (
        "certification_set_basename",
        "certification_set_signature_basename",
        "ci_evidence_basename",
        "maintainer",
    ):
        require_nonempty_string(data, key)
    if data["formal_gates"] != RELEASE_FORMAL_GATES:
        raise EvidenceError("release evidence v3 formal_gates 必须是固定且全部 PASS 的正式门禁")
    validate_ci_evidence_binding(data, evidence_path, binding)


def validate_release_evidence_v3(
    data: dict[str, Any],
    evidence_path: Path,
    args: argparse.Namespace,
    binding: BuildBinding,
) -> None:
    """Validate publication evidence against its signed challenge window."""

    helper = _load_challenge_helper()
    envelope = data.get("challenge_envelope")
    if type(envelope) is not dict:
        raise EvidenceError("release evidence 缺少 canonical challenge_envelope")
    try:
        helper.verify_envelope(
            envelope,
            purpose="publication",
            source_commit=binding.source_commit,
            deb_basename=binding.deb_basename,
            deb_sha256=binding.deb_sha256,
            require_active=bool(
                getattr(args, "pre_sign", False)
                or getattr(args, "require_active_challenge", False)
            ),
            evidence_times=(data.get("generated_at_utc"),),
        )
        issued, expires = helper.validate_structure(envelope)
        reference_time = helper._parse_utc(
            data.get("generated_at_utc"),
            "generated_at_utc",
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise EvidenceError(str(exc)) from exc
    if data.get("challenge_nonce") != envelope["nonce"]:
        raise EvidenceError(
            "release evidence challenge_nonce 与 challenge_envelope 不一致"
        )
    supplied = getattr(args, "challenge", "")
    if supplied and supplied != envelope["nonce"]:
        raise EvidenceError(
            "release evidence 外部 challenge 与 challenge_envelope 不一致"
        )
    values = vars(args).copy()
    values["challenge"] = envelope["nonce"]
    normalized_args = argparse.Namespace(**values)
    window_token = _CHALLENGE_TIME_WINDOW.set((issued, expires))
    reference_token = _SIGNED_EVIDENCE_REFERENCE_TIME.set(reference_time)
    try:
        _validate_release_evidence_v3_inner(
            data,
            evidence_path,
            normalized_args,
            binding,
        )
    finally:
        _SIGNED_EVIDENCE_REFERENCE_TIME.reset(reference_token)
        _CHALLENGE_TIME_WINDOW.reset(window_token)


def validate_legacy_v2_read_only(
    data: dict[str, Any],
    args: argparse.Namespace,
    binding: tuple[str, str, str, str, str, str, str],
) -> None:
    """Perform a deliberately isolated, non-publishing v2 history check."""

    if data.get("schema_version") != 2:
        raise EvidenceError("--legacy-v2-read-only 只接受 schema_version=2 历史证据")
    required = {
        "source_commit",
        "deb_basename",
        "deb_sha256",
        "target_baseline_profile_id",
        "target_baseline_sha256",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise EvidenceError(
            f"历史 v2 证据缺少绑定字段: {', '.join(missing)}"
        )
    if data["source_commit"] != args.source_commit:
        raise EvidenceError("历史 v2 证据 source_commit 与当前输入不一致")
    if data["deb_basename"] != Path(args.deb).name:
        raise EvidenceError("历史 v2 证据 deb_basename 与当前 DEB 不一致")
    deb_hash, _ = sha256_regular_file(Path(args.deb), "当前 DEB")
    require_exact(data, "deb_sha256", deb_hash)
    if type(binding) is not tuple or len(binding) != 7:
        raise EvidenceError("历史 v2 build binding 返回结构不合法")
    expected_profile_id = binding[5]
    expected_profile_sha256 = binding[6]
    require_exact(data, "target_baseline_profile_id", expected_profile_id)
    require_exact(data, "target_baseline_sha256", expected_profile_sha256)
    validate_sha256(data["target_baseline_sha256"], "target_baseline_sha256")
    profile_id = data["target_baseline_profile_id"]
    if type(profile_id) is not str or not TARGET_PROFILE_ID_RE.fullmatch(profile_id):
        raise EvidenceError("历史 v2 target_baseline_profile_id 格式不合法")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("offline", "target", "release", "certification"))
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--checksum", type=Path)
    parser.add_argument("--build-marker", type=Path)
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--packages", type=Path)
    parser.add_argument("--packages-gz", type=Path)
    parser.add_argument("--delivery-dir", type=Path)
    parser.add_argument("--attestation-signature", type=Path)
    parser.add_argument("--attestation-public-key", type=Path)
    parser.add_argument("--attestation-public-key-fingerprint")
    parser.add_argument("--challenge", default="")
    parser.add_argument("--pre-sign", action="store_true")
    parser.add_argument("--legacy-v2-read-only", action="store_true")
    parser.add_argument("--matrix", type=Path)
    return parser.parse_args()


def validate_formal_build_test_log_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="validate-taiji-release-evidence.py formal-build-test-log")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--build-marker", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--pending-marker-parent", type=Path)
    args = parser.parse_args(argv)
    try:
        digest = validate_formal_build_test_log_binding(
            args.manifest,
            args.build_marker,
            args.log,
            args.pending_marker_parent,
        )
    except (EvidenceError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"formal-build-test-log-invalid: {exc}", file=sys.stderr)
        return 1
    print(f"formal-build-test-log-valid\t{digest}\t{args.log}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "formal-build-test-log":
        return validate_formal_build_test_log_cli(sys.argv[2:])
    args = parse_args()
    try:
        require_safe_parent(args.evidence, "证据 JSON")
        evidence_payload, _ = read_regular_bytes(args.evidence, "证据 JSON")
        data = parse_json_bytes(evidence_payload, "证据 JSON")
        manifest = load_json(Path(args.manifest), "发布 manifest")
        if args.mode == "certification":
            if args.legacy_v2_read_only or args.pre_sign:
                raise EvidenceError("认证集不能使用历史 v2 或 --pre-sign 模式")
            binding = validate_build_binding(args)
            if not isinstance(binding, BuildBinding):
                raise EvidenceError("认证集验证未获得 v3 BuildBinding")
            validate_certification_set_v1(data, args.evidence, args, binding)
            print(f"certification-set-valid\t{args.evidence}")
            return 0
        if args.legacy_v2_read_only:
            if args.pre_sign:
                raise EvidenceError(
                    "--legacy-v2-read-only 与 --pre-sign 互斥；历史 v2 不能进入签名前门禁"
                )
            if manifest.get("schema_version") != 2:
                raise EvidenceError("--legacy-v2-read-only 只接受 manifest schema_version=2")
            legacy_binding = validate_build_binding(args, legacy_v2_read_only=True)
            validate_legacy_v2_read_only(data, args, legacy_binding)
            if args.attestation_signature is not None:
                if args.attestation_public_key is None or not args.attestation_public_key_fingerprint:
                    raise EvidenceError("历史 v2 detached signature 缺少验签公钥参数")
                validate_attestation(args, evidence_payload)
            print(f"LEGACY_READ_ONLY\t{args.mode}\t{args.evidence}")
            return 0

        if data.get("schema_version") == 2 or manifest.get("schema_version") == 2:
            raise EvidenceError(
                "当前验证入口正式只接受 release evidence schema v3；v2 只能显式 --legacy-v2-read-only"
            )
        if args.mode == "offline":
            if args.pre_sign:
                raise EvidenceError("当前 offline rehearsal v1 不使用 release --pre-sign 模式")
            if data.get("schema") != OFFLINE_EVIDENCE_SCHEMA_V1:
                raise EvidenceError(
                    "offline mode 只接受 taiji.offline-install-rehearsal.v1"
                )
            binding = validate_build_binding(args)
            if not isinstance(binding, BuildBinding):
                raise EvidenceError("offline rehearsal v1 未获得 v3 BuildBinding")
            if args.attestation_signature is not None:
                if args.attestation_public_key is None or not args.attestation_public_key_fingerprint:
                    raise EvidenceError("offline rehearsal detached signature 缺少验签公钥参数")
                validate_attestation(args, evidence_payload)
            validate_offline_evidence_v1(data, args.evidence, args, binding)
            print(f"offline-rehearsal-valid\t{args.evidence}")
            return 0
        if not args.pre_sign:
            if args.attestation_signature is None:
                raise EvidenceError("发布证据缺少 detached signature")
            if args.attestation_public_key is None or not args.attestation_public_key_fingerprint:
                raise EvidenceError("发布证据 detached signature 缺少验签公钥参数")
            validate_attestation(args, evidence_payload)
        binding = validate_build_binding(args)
        if not isinstance(binding, BuildBinding):
            raise EvidenceError("当前发布路径未返回 v3 BuildBinding")
        if data.get("schema") == RELEASE_EVIDENCE_SCHEMA_V3 or args.mode == "release":
            validate_release_evidence_v3(data, args.evidence, args, binding)
        else:
            raise EvidenceError("当前验证入口只接受 release evidence schema v3")
    except (EvidenceError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"release-evidence-invalid: {exc}", file=sys.stderr)
        return 1
    status = "release-evidence-pre-sign-valid" if args.pre_sign else "release-evidence-valid"
    print(f"{status}\t{args.mode}\t{args.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
