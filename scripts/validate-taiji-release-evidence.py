#!/usr/bin/env python3
"""Validate Taiji release evidence against current, manifest-bound build artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tempfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
INCIDENT_RE = re.compile(r"^inc-[0-9a-f]{12,32}$")
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
ELECTRON_PATH = "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
DRIVER_RESULT_BASENAME = "desktop-driver-result.json"
SCREENSHOT_BASENAME = "desktop-app.png"
DIAGNOSTIC_BASENAME = "taiji-support-bundle.json"
TARGET_CHECK_KEYS = {
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

OFFLINE_KEYS = {
    "schema_version",
    "evidence_type",
    "generated_at_utc",
    "rehearsal_session_id",
    "challenge_nonce",
    "release_artifacts_sha256",
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


class EvidenceError(ValueError):
    pass


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
    if set(query) != {"taiji_desktop", "taiji_desktop_token"}:
        raise EvidenceError("桌面驱动 app_url 含未知 query 数据")
    if query.get("taiji_desktop") != ["1"] or query.get("taiji_desktop_token") != ["<redacted>"]:
        raise EvidenceError("桌面驱动 app_url 未保留唯一桌面标记或 token 未脱敏")
    if origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost"}:
        raise EvidenceError("桌面驱动 webui_origin 必须是 HTTP loopback origin")
    if origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}:
        raise EvidenceError("桌面驱动 webui_origin 不能含认证、query、fragment 或 path")
    if f"{app.scheme}://{app.netloc}" != f"{origin.scheme}://{origin.netloc}":
        raise EvidenceError("桌面驱动 app_url 与 webui_origin 不是同一 App")


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


def validate_build_binding(args: argparse.Namespace) -> tuple[str, str, str, str, str]:
    if not COMMIT_RE.fullmatch(args.source_commit):
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
    required_manifest = {
        "schema_version": 1,
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
    )


def validate_artifact_binding(
    data: dict[str, Any], args: argparse.Namespace, deb_hash: str, release_artifacts_hash: str
) -> None:
    require_exact(data, "source_commit", args.source_commit)
    require_exact(data, "deb_basename", args.deb.name)
    require_exact(data, "deb_sha256", deb_hash)
    require_exact(data, "release_artifacts_sha256", release_artifacts_hash)


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
    release_artifacts_hash: str,
) -> None:
    require_exact_keys(data, OFFLINE_KEYS, evidence_path.name)
    for key, expected in {
        "schema_version": 1,
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
    validate_artifact_binding(data, args, deb_hash, release_artifacts_hash)
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
        "machine_fingerprint_sha256": data["machine_fingerprint_sha256"],
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
) -> None:
    require_exact_keys(data, TARGET_KEYS, evidence_path.name)
    for key, expected in {
        "schema_version": 1,
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
    validate_artifact_binding(data, args, deb_hash, release_artifacts_hash)
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
    if len(driver_payload) > MAX_JSON_BYTES:
        raise EvidenceError("桌面 App 驱动原始结果超过 JSON 大小上限")
    paths = {session_path.name, screenshot_path.name, diagnostic_path.name, driver_path.name}
    identities = {
        (session_stat.st_dev, session_stat.st_ino),
        (screenshot_stat.st_dev, screenshot_stat.st_ino),
        (diagnostic_stat.st_dev, diagnostic_stat.st_ino),
        (driver_stat.st_dev, driver_stat.st_ino),
    }
    if len(paths) != 4 or len(identities) != 4:
        raise EvidenceError("桌面验收会话、截图、诊断导出和驱动原始结果必须是四个不同文件")
    session = parse_json_bytes(session_payload, "桌面 App 验收会话")
    validate_target_session(data, session, args, version)
    validate_target_driver(
        data,
        session,
        parse_json_bytes(driver_payload, "桌面 App 驱动原始结果"),
    )
    validate_png(screenshot_payload)
    validate_support_bundle(diagnostic_payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("offline", "target"))
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--build-marker", required=True, type=Path)
    parser.add_argument("--source-archive", required=True, type=Path)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--packages-gz", required=True, type=Path)
    parser.add_argument("--delivery-dir", required=True, type=Path)
    parser.add_argument("--attestation-signature", type=Path)
    parser.add_argument("--attestation-public-key", required=True, type=Path)
    parser.add_argument("--attestation-public-key-fingerprint", required=True)
    parser.add_argument("--challenge", required=True)
    parser.add_argument("--pre-sign", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        require_safe_parent(args.evidence, "证据 JSON")
        evidence_payload, _ = read_regular_bytes(args.evidence, "证据 JSON")
        data = parse_json_bytes(evidence_payload, "证据 JSON")
        if not args.pre_sign:
            if args.attestation_signature is None:
                raise EvidenceError("发布证据缺少 detached signature")
            validate_attestation(args, evidence_payload)
        (
            deb_hash,
            version,
            release_artifacts_hash,
            electron_executable_hash,
            desktop_entry_hash,
        ) = validate_build_binding(args)
        if args.mode == "offline":
            validate_offline(data, args.evidence, args, deb_hash, release_artifacts_hash)
        else:
            validate_target(
                data,
                args.evidence,
                args,
                deb_hash,
                version,
                release_artifacts_hash,
                electron_executable_hash,
                desktop_entry_hash,
            )
    except (EvidenceError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"release-evidence-invalid: {exc}", file=sys.stderr)
        return 1
    status = "release-evidence-pre-sign-valid" if args.pre_sign else "release-evidence-valid"
    print(f"{status}\t{args.mode}\t{args.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
