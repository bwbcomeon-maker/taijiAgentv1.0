#!/usr/bin/env python3
"""Verify and launch the root-owned installed Taiji target-acceptance toolchain."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple


TRUSTED_OWNER_UID = 0
INSTALL_ROOT = Path("/opt/taiji-agent")
RELEASE_MANIFEST_PATH = INSTALL_ROOT / "resources/taiji-release-manifest.json"
BINDING_PATH = INSTALL_ROOT / "resources/taiji-acceptance-binding.json"
CODE_ROOT = INSTALL_ROOT / "libexec/target-acceptance"
TOOLS_ROOT = CODE_ROOT / "验收工具"
LAUNCHER_BASENAME = "04_目标终端_桌面App验收并导出证据.sh"
LAUNCHER_PATH = CODE_ROOT / LAUNCHER_BASENAME
HELPER_PATH = CODE_ROOT / "acceptance_tools_manifest.py"
RUNNER_PATH = CODE_ROOT / "acceptance-runner.py"
ENTRYPOINT_PATH = Path("/usr/bin/taiji-agent-acceptance")
INSTALLED_PYTHON = INSTALL_ROOT / "runtime/agent/venv/bin/python"
BASH_PATH = Path("/bin/bash")
PACKAGE_MANIFEST_RELATIVE = Path("生成的安装包/taiji-package-manifest.json")
MAX_JSON_BYTES = 2 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")

BINDING_SCHEMA = "taiji-installed-acceptance-binding/v1"
BINDING_KEYS = {
    "schema",
    "version",
    "source_commit",
    "release_manifest_sha256",
    "acceptance_tools_manifest_path",
    "acceptance_tools_manifest_sha256",
    "launcher_path",
    "launcher_sha256",
    "helper_path",
    "helper_sha256",
    "runner_path",
    "runner_sha256",
    "entrypoint_path",
    "entrypoint_sha256",
}
RELEASE_MANIFEST_KEYS = {"schema", "platform", "arch", "version", "commit", "installRoot"}

CANONICAL_BINDING_PATHS = {
    "acceptance_tools_manifest_path": "/opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json",
    "launcher_path": "/opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh",
    "helper_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py",
    "runner_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py",
    "entrypoint_path": "/usr/bin/taiji-agent-acceptance",
}


class AcceptanceRunnerError(ValueError):
    """Raised when the installed acceptance trust chain is invalid."""


class AcceptanceArguments(NamedTuple):
    delivery_dir: Path
    customer_dir: Path
    install_observation: Path
    method_attestation: Path
    installer_screenshot: Path
    category_id: str
    challenge: str
    environment_observation: Path
    target_dir: Path
    timeout_ms: int


def _strict_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise AcceptanceRunnerError("JSON contains a duplicate field")
        result[key] = value
    return result


def _identity(metadata: os.stat_result) -> Tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_directory_chain(path: Path, leaf_owner_uid: int, label: str) -> int:
    absolute = Path(os.path.abspath(str(path)))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute.anchor, flags)
    except OSError as exc:
        raise AcceptanceRunnerError("%s directory chain cannot be opened" % label) from exc
    try:
        parts = absolute.parts[1:]
        for index, part in enumerate(parts):
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise AcceptanceRunnerError("%s directory chain contains a symlink" % label) from exc
            try:
                opened = os.fstat(child)
                current = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                mode = stat.S_IMODE(opened.st_mode)
                is_leaf = index == len(parts) - 1
                allowed_owners = {0, leaf_owner_uid}
                sticky_ancestor = (
                    not is_leaf and mode == 0o1777 and opened.st_uid in allowed_owners
                )
                if (
                    _identity(opened) != _identity(current)
                    or not stat.S_ISDIR(opened.st_mode)
                    or opened.st_uid not in allowed_owners
                    or (mode & 0o022 and not sticky_ancestor)
                    or (is_leaf and opened.st_uid != leaf_owner_uid)
                ):
                    raise AcceptanceRunnerError("%s directory is not trusted" % label)
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_regular(path: Path, owner_uid: int, mode: int, label: str) -> bytes:
    absolute = Path(os.path.abspath(str(path)))
    parent_descriptor = _open_directory_chain(absolute.parent, owner_uid, "%s parent" % label)
    descriptor = None  # type: Optional[int]
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            before = os.stat(absolute.name, dir_fd=parent_descriptor, follow_symlinks=False)
            descriptor = os.open(absolute.name, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise AcceptanceRunnerError("%s cannot be opened safely" % label) from exc
        opened = os.fstat(descriptor)
        if (
            _identity(opened) != _identity(before)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != owner_uid
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != mode
            or opened.st_size <= 0
            or opened.st_size > MAX_JSON_BYTES
        ):
            raise AcceptanceRunnerError("%s is not one trusted regular file" % label)
        chunks = []  # type: List[bytes]
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        current = os.stat(absolute.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            len(payload) != opened.st_size
            or _identity(os.fstat(descriptor)) != _identity(opened)
            or _identity(current) != _identity(opened)
        ):
            raise AcceptanceRunnerError("%s changed while read" % label)
        return payload
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _load_json(raw: bytes, label: str, canonical: bool) -> Dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, AcceptanceRunnerError) as exc:
        if isinstance(exc, AcceptanceRunnerError):
            raise
        raise AcceptanceRunnerError("%s is not strict UTF-8 JSON" % label) from exc
    if type(payload) is not dict:
        raise AcceptanceRunnerError("%s must be one JSON object" % label)
    if canonical:
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if encoded != raw:
            raise AcceptanceRunnerError("%s is not canonical JSON" % label)
    return payload


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_hashes(payload: Mapping[str, Any], names: Iterable[str], label: str) -> None:
    for name in names:
        value = payload.get(name)
        if type(value) is not str or not SHA256_RE.fullmatch(value):
            raise AcceptanceRunnerError("%s has an invalid digest field: %s" % (label, name))


def _load_helper(helper_raw: bytes):
    if _sha256(helper_raw) == "0" * 64:
        raise AcceptanceRunnerError("acceptance helper digest is invalid")
    spec = importlib.util.spec_from_file_location("taiji_installed_acceptance_tools", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise AcceptanceRunnerError("installed acceptance helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_delivery_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise AcceptanceRunnerError("delivery directory must be absolute")
    if ".." in path.parts:
        raise AcceptanceRunnerError("delivery directory must be a canonical path without ..")
    absolute = Path(os.path.abspath(str(path)))
    descriptor = _open_directory_chain(absolute, os.getuid(), "delivery directory")
    try:
        metadata = os.fstat(descriptor)
        if stat.S_IMODE(metadata.st_mode) not in {0o700, 0o750, 0o755}:
            raise AcceptanceRunnerError("delivery directory is writable by an untrusted user")
    finally:
        os.close(descriptor)
    return absolute


def verify_installed_acceptance(delivery_dir: Path) -> Dict[str, str]:
    delivery = _validate_delivery_directory(Path(delivery_dir))
    binding_raw = _read_regular(BINDING_PATH, TRUSTED_OWNER_UID, 0o644, "acceptance binding")
    binding = _load_json(binding_raw, "acceptance binding", canonical=True)
    if set(binding) != BINDING_KEYS or binding.get("schema") != BINDING_SCHEMA:
        raise AcceptanceRunnerError("acceptance binding has an invalid exact field set")
    if not COMMIT_RE.fullmatch(binding.get("source_commit", "")):
        raise AcceptanceRunnerError("acceptance binding source commit is invalid")
    if not VERSION_RE.fullmatch(binding.get("version", "")):
        raise AcceptanceRunnerError("acceptance binding version is invalid")
    for name, expected in CANONICAL_BINDING_PATHS.items():
        if binding.get(name) != expected:
            raise AcceptanceRunnerError("acceptance binding path is not canonical: %s" % name)
    _require_hashes(
        binding,
        (
            "release_manifest_sha256",
            "acceptance_tools_manifest_sha256",
            "launcher_sha256",
            "helper_sha256",
            "runner_sha256",
            "entrypoint_sha256",
        ),
        "acceptance binding",
    )

    release_raw = _read_regular(
        RELEASE_MANIFEST_PATH, TRUSTED_OWNER_UID, 0o644, "installed release manifest"
    )
    if _sha256(release_raw) != binding["release_manifest_sha256"]:
        raise AcceptanceRunnerError("installed release manifest digest does not match binding")
    release = _load_json(release_raw, "installed release manifest", canonical=True)
    if set(release) != RELEASE_MANIFEST_KEYS or release != {
        "schema": "taiji-release-manifest/v1",
        "platform": "linux",
        "arch": "amd64",
        "version": binding["version"],
        "commit": binding["source_commit"],
        "installRoot": "/opt/taiji-agent",
    }:
        raise AcceptanceRunnerError("installed release manifest does not match acceptance binding")

    fixed_files = (
        (ENTRYPOINT_PATH, 0o755, "entrypoint_sha256", "installed acceptance entrypoint"),
        (RUNNER_PATH, 0o644, "runner_sha256", "installed acceptance runner"),
        (HELPER_PATH, 0o644, "helper_sha256", "installed acceptance helper"),
        (LAUNCHER_PATH, 0o755, "launcher_sha256", "installed acceptance launcher"),
    )
    observed = {}  # type: Dict[str, bytes]
    for path, mode, hash_key, label in fixed_files:
        raw = _read_regular(path, TRUSTED_OWNER_UID, mode, label)
        if _sha256(raw) != binding[hash_key]:
            raise AcceptanceRunnerError("%s digest does not match binding" % label)
        observed[hash_key] = raw

    helper = _load_helper(observed["helper_sha256"])
    helper.verify_staged(
        TOOLS_ROOT,
        binding["source_commit"],
        binding["acceptance_tools_manifest_sha256"],
        TRUSTED_OWNER_UID,
    )

    package_manifest_path = delivery / PACKAGE_MANIFEST_RELATIVE
    package_raw = _read_regular(
        package_manifest_path,
        os.getuid(),
        0o644,
        "external package manifest",
    )
    package = _load_json(package_raw, "external package manifest", canonical=False)
    expected_package_fields = {
        "schema": "taiji-package-manifest/v3",
        "source_commit": binding["source_commit"],
        "version": binding["version"],
        "acceptance_binding_sha256": _sha256(binding_raw),
        "acceptance_tools_manifest_sha256": binding["acceptance_tools_manifest_sha256"],
        "acceptance_entrypoint_sha256": binding["entrypoint_sha256"],
        "installed_release_manifest_sha256": binding["release_manifest_sha256"],
    }
    for name, expected in expected_package_fields.items():
        if package.get(name) != expected:
            raise AcceptanceRunnerError("external package manifest does not match installed binding: %s" % name)
    return {
        "source_commit": binding["source_commit"],
        "version": binding["version"],
        "delivery_dir": str(delivery),
        "launcher_path": str(LAUNCHER_PATH),
        "binding_sha256": _sha256(binding_raw),
    }


def _absolute_argument(path: Path, label: str) -> str:
    if not path.is_absolute() or ".." in path.parts:
        raise AcceptanceRunnerError("%s must be an absolute canonical path" % label)
    return str(Path(os.path.abspath(str(path))))


def build_acceptance_environment(
    arguments: AcceptanceArguments,
    ambient: Mapping[str, str],
) -> Dict[str, str]:
    result = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    for name in (
        "HOME",
        "USER",
        "LOGNAME",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_ID",
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_TYPE",
        "XDG_SESSION_DESKTOP",
        "DESKTOP_SESSION",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
    ):
        value = ambient.get(name)
        if type(value) is str and value and "\x00" not in value:
            result[name] = value
    for name, value in ambient.items():
        if (
            type(name) is str
            and name.startswith("LC_")
            and type(value) is str
            and value
            and "\x00" not in value
        ):
            result[name] = value
    home = result.get("HOME")
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        value = result.get(name)
        if value is None:
            continue
        if home is None:
            raise AcceptanceRunnerError("%s requires one canonical HOME" % name)
        normalized = os.path.abspath(value)
        if value != normalized or (value != home and not value.startswith(home + os.sep)):
            raise AcceptanceRunnerError("%s must be a canonical path inside HOME" % name)
    if not CATEGORY_RE.fullmatch(arguments.category_id):
        raise AcceptanceRunnerError("certification category id is invalid")
    if not CHALLENGE_RE.fullmatch(arguments.challenge):
        raise AcceptanceRunnerError("acceptance challenge is invalid")
    if type(arguments.timeout_ms) is not int or not 30000 <= arguments.timeout_ms <= 1800000:
        raise AcceptanceRunnerError("acceptance timeout is invalid")
    result.update(
        {
            "TAIJI_TARGET_DELIVERY_DIR": _absolute_argument(arguments.delivery_dir, "delivery directory"),
            "TAIJI_SINGLE_DEB_CUSTOMER_DIR": _absolute_argument(arguments.customer_dir, "customer directory"),
            "TAIJI_SINGLE_DEB_INSTALL_OBSERVATION": _absolute_argument(arguments.install_observation, "install observation"),
            "TAIJI_SINGLE_DEB_METHOD_ATTESTATION": _absolute_argument(arguments.method_attestation, "method attestation"),
            "TAIJI_SINGLE_DEB_GRAPHICAL_INSTALLER_EVIDENCE": _absolute_argument(arguments.installer_screenshot, "installer screenshot"),
            "TAIJI_CERTIFICATION_CATEGORY_ID": arguments.category_id,
            "TAIJI_TARGET_ACCEPTANCE_CHALLENGE": arguments.challenge,
            "TAIJI_LINUX_ENVIRONMENT_OBSERVATION": _absolute_argument(arguments.environment_observation, "environment observation"),
            "TAIJI_TARGET_VERIFICATION_DIR": _absolute_argument(arguments.target_dir, "target directory"),
            "TAIJI_TARGET_ACCEPTANCE_TIMEOUT_MS": str(arguments.timeout_ms),
        }
    )
    return result


def _parse_arguments(argv: List[str]) -> AcceptanceArguments:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-dir", type=Path, required=True)
    parser.add_argument("--customer-dir", type=Path, required=True)
    parser.add_argument("--install-observation", type=Path, required=True)
    parser.add_argument("--method-attestation", type=Path, required=True)
    parser.add_argument("--installer-screenshot", type=Path, required=True)
    parser.add_argument("--category-id", required=True)
    parser.add_argument("--challenge", required=True)
    parser.add_argument("--environment-observation", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=900000)
    values = parser.parse_args(argv)
    return AcceptanceArguments(
        delivery_dir=values.delivery_dir,
        customer_dir=values.customer_dir,
        install_observation=values.install_observation,
        method_attestation=values.method_attestation,
        installer_screenshot=values.installer_screenshot,
        category_id=values.category_id,
        challenge=values.challenge,
        environment_observation=values.environment_observation,
        target_dir=values.target_dir,
        timeout_ms=values.timeout_ms,
    )


def _main(argv: List[str]) -> int:
    arguments = _parse_arguments(argv)
    verify_installed_acceptance(arguments.delivery_dir)
    environment = build_acceptance_environment(arguments, os.environ)
    os.execve(str(BASH_PATH), [str(BASH_PATH), str(LAUNCHER_PATH)], environment)
    raise AssertionError("execve returned unexpectedly")


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except AcceptanceRunnerError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        raise SystemExit(1)
