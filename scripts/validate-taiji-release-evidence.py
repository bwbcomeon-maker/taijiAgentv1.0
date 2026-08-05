#!/usr/bin/env python3
"""Validate Taiji release evidence against current, manifest-bound build artifacts."""

from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib.util
import json
import os
import re
import stat
import struct
import subprocess
import sys
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
PACKAGE_MANIFEST_SCHEMA_V3 = "taiji-package-manifest/v3"
RELEASE_EVIDENCE_SCHEMA_V3 = "taiji-release-evidence/v3"
CANONICAL_POLICY_ID = "taiji-linux-amd64-deb-v1"
# Fallback for a validator copied into a target delivery directory.  When the
# checked-in policy is available, ``canonical_policy_identity`` recomputes the
# value through compatibility_policy.py instead of trusting this constant.
CANONICAL_POLICY_SHA256 = "7956044933d4add977b3c84e24b607120cfd285e3e99f1ce38431e6bd2ee163e"
CANONICAL_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "packaging/linux/compatibility-policy.json"
)
CANONICAL_POLICY_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "packaging/linux/compatibility_policy.py"
)
ELECTRON_PATH = "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
DRIVER_RESULT_BASENAME = "desktop-driver-result.json"
SCREENSHOT_BASENAME = "desktop-app.png"
DIAGNOSTIC_BASENAME = "taiji-support-bundle.json"
INSTALL_OBSERVATION_BASENAME = "single-deb-install-observation.json"
INSTALL_METHOD_ATTESTATION_BASENAME = "single-deb-install-method-attestation.json"
GRAPHICAL_INSTALLER_EVIDENCE_BASENAME = "single-deb-graphical-installer.png"
TARGET_CHECK_KEYS = {
    "visible_first_configuration_completion",
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
    "desktop_auth_cookie",
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


def read_regular_bytes(path: Path, label: str, *, limit: int = MAX_JSON_BYTES) -> tuple[bytes, os.stat_result]:
    descriptor, file_stat = open_regular(path, label)
    try:
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
        return payload, file_stat
    finally:
        os.close(descriptor)


def sha256_regular_file(path: Path, label: str) -> tuple[str, os.stat_result]:
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
    finally:
        os.close(descriptor)
    if total != file_stat.st_size:
        raise EvidenceError(f"{label} 摘要计算期间发生变化: {path}")
    return digest.hexdigest(), file_stat


def delivery_inventory_sha256(delivery_dir: Path) -> str:
    excluded_top_level = {
        "offline-install-rehearsal",
        "target-verification",
        "构建日志",
        "诊断报告",
    }
    required_relative = {
        "00_制包机_生成离线交付包.sh",
        "01_制包机_发布预检.sh",
        "02_目标终端_安装并验证.sh",
        "03_目标终端_导出诊断报告.sh",
        "04_目标终端_桌面App验收并导出证据.sh",
        "99_本机_准备制包输入包.sh",
        "SHA256SUMS.txt",
        "操作说明.md",
        "版本信息.txt",
        "生成的安装包/.build-success",
        "生成的安装包/taiji-package-manifest.json",
        "生成的安装包/构建报告.txt",
        "离线依赖/Packages",
        "离线依赖/Packages.gz",
        "离线依赖/SHA256SUMS.txt",
        "离线依赖/runtime-dependencies.txt",
        "验收工具/run-installed-electron-acceptance.js",
        "验收工具/assemble-target-evidence.py",
        "验收工具/observe-single-deb-install.py",
        "验收工具/validate-taiji-release-evidence.py",
        "验收工具/signing-public.pem",
    }
    require_trusted_ancestor_chain(delivery_dir, "交付目录")
    try:
        root_mode = delivery_dir.lstat().st_mode
    except OSError as exc:
        raise EvidenceError(f"交付目录不可读取: {delivery_dir}: {exc}") from exc
    if not stat.S_ISDIR(root_mode) or delivery_dir.is_symlink():
        raise EvidenceError("交付目录必须是真实目录，不能是符号链接")
    root_permissions = stat.S_IMODE(root_mode)
    if root_permissions & 0o022:
        raise EvidenceError("交付目录不能允许 group/other 写入")

    file_inventory: list[tuple[str, int, str]] = []
    directory_inventory: list[tuple[str, int]] = [(".", root_permissions)]

    def walk_error(exc: OSError) -> None:
        raise EvidenceError(f"交付目录遍历失败: {exc}") from exc

    for current, directories, filenames in os.walk(
        delivery_dir,
        topdown=True,
        followlinks=False,
        onerror=walk_error,
    ):
        current_path = Path(current)
        if current_path == delivery_dir:
            directories[:] = [name for name in directories if name not in excluded_top_level]
        for directory in directories:
            directory_path = current_path / directory
            mode = directory_path.lstat().st_mode
            if not stat.S_ISDIR(mode) or directory_path.is_symlink():
                raise EvidenceError(f"交付目录含不安全目录节点: {directory_path}")
            permissions = stat.S_IMODE(mode)
            if permissions & 0o022:
                raise EvidenceError(f"交付目录节点不能允许 group/other 写入: {directory_path}")
            directory_inventory.append(
                (directory_path.relative_to(delivery_dir).as_posix(), permissions)
            )
        for filename in filenames:
            file_path = current_path / filename
            relative = file_path.relative_to(delivery_dir).as_posix()
            digest, file_stat = sha256_regular_file(file_path, f"交付文件 {relative}")
            permissions = stat.S_IMODE(file_stat.st_mode)
            if permissions & 0o022:
                raise EvidenceError(f"交付文件不能允许 group/other 写入: {file_path}")
            file_inventory.append((relative, permissions, digest))
    file_inventory.sort()
    directory_inventory.sort()
    paths = {relative for relative, _mode, _digest in file_inventory}
    missing = sorted(required_relative - paths)
    if missing:
        raise EvidenceError(f"交付清单缺少必需文件: {', '.join(missing)}")
    offline_debs = [
        relative
        for relative in paths
        if relative.startswith("离线依赖/") and relative.endswith(".deb")
    ]
    if not offline_debs:
        raise EvidenceError("交付清单未包含离线仓库 DEB")
    source_archives = [
        relative
        for relative in paths
        if re.fullmatch(r"taiji-agentv1\.0-kylin-build-src-[0-9a-f]{7,40}\.tar\.gz", relative)
    ]
    if len(source_archives) != 1:
        raise EvidenceError("交付清单必须且只能包含一个当前源码包")
    canonical = hashlib.sha256()
    records = [
        ("D", relative, "") for relative, _mode in directory_inventory
    ] + [
        ("F", relative, digest) for relative, _mode, digest in file_inventory
    ]
    for kind, relative, digest in sorted(records):
        canonical.update(kind.encode("ascii"))
        canonical.update(b"\0")
        canonical.update(relative.encode("utf-8"))
        canonical.update(b"\0")
        if kind == "F":
            canonical.update(digest.encode("ascii"))
            canonical.update(b"\0")
    return canonical.hexdigest()


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


def validate_attestation(args: argparse.Namespace, evidence_payload: bytes) -> None:
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
            ["openssl", "pkey", "-pubin", "-in", str(public_path), "-outform", "DER"],
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
                "openssl",
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


def parse_marker(path: Path) -> dict[str, str]:
    payload, _ = read_regular_bytes(path, "构建成功标记")
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


def _validate_checksum_sidecar(args: argparse.Namespace, deb_hash: str) -> None:
    checksum_path = getattr(args, "checksum", None)
    if checksum_path is None:
        return
    checksum_path = Path(checksum_path)
    checksum_payload, _ = read_regular_bytes(checksum_path, "DEB SHA256 sidecar")
    try:
        checksum_text = checksum_payload.decode("ascii")
    except UnicodeError as exc:
        raise EvidenceError("DEB SHA256 sidecar 必须是 ASCII") from exc
    match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)\n?", checksum_text)
    if not match or match.group(1) != deb_hash or match.group(2) != Path(args.deb).name:
        raise EvidenceError("DEB SHA256 sidecar 未准确绑定当前 DEB basename 和内容")


def _validate_v3_build_binding(args: argparse.Namespace) -> BuildBinding:
    source_commit = getattr(args, "source_commit", "")
    if not FULL_COMMIT_RE.fullmatch(source_commit):
        raise EvidenceError(f"当前源码 commit 格式不合法: {source_commit!r}")
    deb_path = Path(args.deb)
    deb_hash, _ = sha256_regular_file(deb_path, "当前 DEB")
    _validate_checksum_sidecar(args, deb_hash)

    manifest = load_json(Path(args.manifest), "发布 manifest")
    reject_target_baseline_fields(manifest, "发布 manifest")
    if manifest.get("schema") != PACKAGE_MANIFEST_SCHEMA_V3:
        if manifest.get("schema_version") == 2:
            raise EvidenceError(
                "当前发布入口只接受 taiji-package-manifest/v3；历史 v2 必须显式 --legacy-v2-read-only"
            )
        raise EvidenceError("销售发布门禁强制 manifest schema=taiji-package-manifest/v3")

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
    # The ABI report is part of the v3 manifest binding.  It is deliberately
    # checked even though it is not a BuildBinding field: the report hash must
    # be a well-formed immutable release input before later certification work.
    if "elf_abi_audit_sha256" in manifest:
        validate_sha256(manifest["elf_abi_audit_sha256"], "elf_abi_audit_sha256")
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

    manifest = load_json(Path(args.manifest), "发布 manifest")
    if manifest.get("schema") == PACKAGE_MANIFEST_SCHEMA_V3:
        if legacy_v2_read_only:
            raise EvidenceError("--legacy-v2-read-only 只能检查 manifest schema_version=2")
        return _validate_v3_build_binding(args)
    if manifest.get("schema_version") == 2:
        if not legacy_v2_read_only:
            raise EvidenceError(
                "当前发布入口拒绝 schema_version=2 v2 证据；历史 v2 只能显式使用 --legacy-v2-read-only"
            )
        return _validate_v2_read_only_binding(args)
    raise EvidenceError("发布 manifest 不是受支持的 v3 合同或显式 v2 历史合同")


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
        "schema": "taiji.desktop.acceptance-driver.v1",
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
    screenshot_path, screenshot_payload, screenshot_stat = validate_bound_file(
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
    graphical_path, graphical_payload, graphical_stat = validate_bound_file(
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
        hashlib.sha256(graphical_payload).hexdigest(),
        args,
    )
    validate_png(screenshot_payload)
    validate_png(graphical_payload)
    validate_support_bundle(diagnostic_payload)


RELEASE_EVIDENCE_V3_KEYS = {
    "schema",
    "evidence_type",
    "generated_at_utc",
    "challenge_nonce",
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
    "maintainer",
    "customer_filename",
    "customer_folder_contract",
    "signing_public_key_fingerprint",
    "formal_gates",
}


def validate_release_evidence_v3(
    data: dict[str, Any], args: argparse.Namespace, binding: BuildBinding
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
        "signing_public_key_fingerprint",
    ):
        validate_sha256(data[key], key)
    for key in (
        "certification_set_basename",
        "certification_set_signature_basename",
        "maintainer",
    ):
        require_nonempty_string(data, key)
    formal_gates = data["formal_gates"]
    if type(formal_gates) is not dict or not formal_gates:
        raise EvidenceError("release evidence v3 formal_gates 必须是非空 object")


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
    parser.add_argument("mode", choices=("offline", "target", "release"))
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        require_safe_parent(args.evidence, "证据 JSON")
        evidence_payload, _ = read_regular_bytes(args.evidence, "证据 JSON")
        data = parse_json_bytes(evidence_payload, "证据 JSON")
        manifest = load_json(Path(args.manifest), "发布 manifest")
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
            validate_release_evidence_v3(data, args, binding)
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
