#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "taiji-payload-contract/v1"
EMBEDDED_CONTRACT = "opt/taiji-agent/resources/payload-contract.json"
TRUSTED_CONTRACT_SHA256 = "266c7bf6dbf6b4268827f58258cd42b484e0a6de15681995c606cf4a272e6e50"
FORBIDDEN_PROFILE_MARKER = b"source-development"
READ_CHUNK_SIZE = 1024 * 1024
SYMLINK_POLICY = "relative-internal-existing-targets-without-symlink-components"


class PayloadContractError(RuntimeError):
    pass


def safe_relative_path(value: object) -> PurePosixPath:
    text = str(value or "")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PayloadContractError(f"unsafe relative path: {text}")
    return path


def payload_path(root: Path, relative: object, *, label: str) -> Path:
    rel = safe_relative_path(relative)
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise PayloadContractError(f"{label} is a symlink: {rel.as_posix()}")
    try:
        common = os.path.commonpath((str(root), str(current.resolve(strict=False))))
    except ValueError as exc:
        raise PayloadContractError(f"{label} escapes payload root: {rel.as_posix()}") from exc
    if common != str(root):
        raise PayloadContractError(f"{label} escapes payload root: {rel.as_posix()}")
    return current


def read_text(path: Path, *, label: str) -> str:
    if not path.is_file():
        raise PayloadContractError(f"{label} is missing: {path}")
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise PayloadContractError(f"cannot read {label}: {path}") from exc


def read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(read_text(path, label=label))
    except json.JSONDecodeError as exc:
        raise PayloadContractError(f"{label} is not valid JSON: {path}") from exc


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_node_metadata(relative: Path, metadata: Any) -> str:
    label = relative.as_posix()
    mode = metadata.st_mode
    if stat.S_ISREG(mode):
        kind = "regular file"
        if metadata.st_nlink != 1:
            raise PayloadContractError(
                f"payload regular file has unexpected hardlink count {metadata.st_nlink}: {label}"
            )
    elif stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISLNK(mode):
        return "symlink"
    elif stat.S_ISFIFO(mode):
        raise PayloadContractError(f"payload contains forbidden FIFO node: {label}")
    elif stat.S_ISSOCK(mode):
        raise PayloadContractError(f"payload contains forbidden socket node: {label}")
    elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        raise PayloadContractError(f"payload contains forbidden device node: {label}")
    else:
        raise PayloadContractError(f"payload contains unsupported filesystem node: {label}")

    if mode & stat.S_ISUID:
        raise PayloadContractError(f"payload {kind} has forbidden setuid bit: {label}")
    if mode & stat.S_ISGID:
        raise PayloadContractError(f"payload {kind} has forbidden setgid bit: {label}")
    if stat.S_IMODE(mode) & stat.S_IWOTH:
        raise PayloadContractError(f"payload {kind} is world-writable: {label}")
    return kind


def _collect_payload_nodes(root: Path) -> dict[str, tuple[Path, os.stat_result, str]]:
    try:
        root_metadata = os.lstat(root)
    except OSError as exc:
        raise PayloadContractError(f"cannot lstat payload root: {root}") from exc
    if verify_node_metadata(Path("."), root_metadata) != "directory":
        raise PayloadContractError(f"payload root is not a directory: {root}")

    nodes: dict[str, tuple[Path, os.stat_result, str]] = {}
    pending: list[tuple[Path, str]] = [(root, "")]
    while pending:
        directory, parent_relative = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            label = parent_relative or "."
            raise PayloadContractError(f"cannot read payload directory: {label}") from exc
        child_directories: list[tuple[Path, str]] = []
        for child in children:
            relative = f"{parent_relative}/{child.name}" if parent_relative else child.name
            path = directory / child.name
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise PayloadContractError(f"cannot lstat payload node: {relative}") from exc
            kind = verify_node_metadata(Path(relative), metadata)
            nodes[relative] = (path, metadata, kind)
            if kind == "directory":
                child_directories.append((path, relative))
        pending.extend(reversed(child_directories))
    return nodes


def _symlink_target_key(relative: str, target: str) -> str:
    if posixpath.isabs(target):
        raise PayloadContractError(f"payload contains absolute symlink: {relative} -> {target}")
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
    if normalized == ".." or normalized.startswith("../"):
        raise PayloadContractError(f"payload contains escaping symlink: {relative} -> {target}")
    return "" if normalized == "." else normalized


def _verify_symlink_target(
    relative: str,
    target: str,
    nodes: dict[str, tuple[Path, os.stat_result, str]],
) -> None:
    target_key = _symlink_target_key(relative, target)
    if not target_key:
        return
    parts = target_key.split("/")
    for index in range(len(parts)):
        prefix = "/".join(parts[: index + 1])
        node = nodes.get(prefix)
        if node is None:
            raise PayloadContractError(f"payload contains dangling symlink: {relative} -> {target}")
        kind = node[2]
        if kind == "symlink":
            raise PayloadContractError(
                f"payload symlink target contains another symlink component: {relative} -> {target}"
            )
        if index < len(parts) - 1 and kind != "directory":
            raise PayloadContractError(f"payload contains dangling symlink: {relative} -> {target}")


def _stream_regular_file(
    path: Path,
    relative: str,
    metadata: os.stat_result,
    digest: Any,
) -> int:
    if stat.S_IMODE(metadata.st_mode) & 0o444 == 0:
        raise PayloadContractError(f"cannot read payload regular file: {relative}")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PayloadContractError(f"cannot read payload regular file: {relative}") from exc
    byte_count = 0
    overlap = b""
    try:
        opened_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or opened_metadata.st_dev != metadata.st_dev
            or opened_metadata.st_ino != metadata.st_ino
        ):
            raise PayloadContractError(f"payload regular file changed during verification: {relative}")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            while chunk := handle.read(READ_CHUNK_SIZE):
                byte_count += len(chunk)
                digest.update(chunk)
                marker_window = overlap + chunk
                if FORBIDDEN_PROFILE_MARKER in marker_window:
                    raise PayloadContractError(
                        f"payload contains forbidden source-development profile marker: {relative}"
                    )
                overlap = marker_window[-(len(FORBIDDEN_PROFILE_MARKER) - 1) :]
    except OSError as exc:
        raise PayloadContractError(f"cannot read payload regular file: {relative}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if byte_count != metadata.st_size:
        raise PayloadContractError(f"payload regular file changed size during verification: {relative}")
    return byte_count


def audit_payload_tree(root: Path) -> dict[str, Any]:
    nodes = _collect_payload_nodes(root)
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    symlink_count = 0
    for relative in sorted(nodes):
        path, metadata, kind = nodes[relative]
        mode = stat.S_IMODE(metadata.st_mode)
        encoded_relative = relative.encode("utf-8", errors="surrogateescape")
        if kind == "directory":
            digest.update(b"D\0" + encoded_relative + b"\0" + f"{mode:04o}".encode("ascii") + b"\0")
        elif kind == "symlink":
            try:
                target = os.readlink(path)
            except OSError as exc:
                raise PayloadContractError(f"cannot read payload symlink: {relative}") from exc
            _verify_symlink_target(relative, target, nodes)
            digest.update(
                b"L\0"
                + encoded_relative
                + b"\0"
                + target.encode("utf-8", errors="surrogateescape")
                + b"\0"
            )
            symlink_count += 1
        else:
            digest.update(b"F\0" + encoded_relative + b"\0" + f"{mode:04o}".encode("ascii") + b"\0")
            byte_count += _stream_regular_file(path, relative, metadata, digest)
            digest.update(b"\0")
            file_count += 1
    return {
        "sha256": digest.hexdigest(),
        "entry_count": len(nodes),
        "file_count": file_count,
        "byte_count": byte_count,
        "symlink_count": symlink_count,
        "symlink_policy": SYMLINK_POLICY,
    }


def json_field(value: Any, field: object, *, label: str) -> Any:
    current = value
    for part in str(field or "").split("."):
        if not part or not isinstance(current, dict) or part not in current:
            raise PayloadContractError(f"{label} has no JSON field {field}")
        current = current[part]
    return current


def verify_type_and_mode(root: Path, component: dict[str, Any]) -> None:
    component_id = str(component.get("id") or "")
    target = payload_path(root, component.get("path"), label=f"component {component_id}")
    if not target.exists():
        raise PayloadContractError(f"missing component {component_id}: {component.get('path')}")
    expected_type = component.get("type")
    if expected_type == "file" and not target.is_file():
        raise PayloadContractError(f"component {component_id} must be a regular file")
    if expected_type == "directory" and not target.is_dir():
        raise PayloadContractError(f"component {component_id} must be a directory")
    if expected_type not in {"file", "directory"}:
        raise PayloadContractError(f"component {component_id} has unsupported type {expected_type}")
    expected_mode = str(component.get("mode") or "")
    if not re.fullmatch(r"0[0-7]{3}", expected_mode):
        raise PayloadContractError(f"component {component_id} has invalid mode {expected_mode}")
    actual_mode = stat.S_IMODE(target.lstat().st_mode)
    if actual_mode != int(expected_mode, 8):
        raise PayloadContractError(
            f"component {component_id} mode {actual_mode:04o} does not match {expected_mode}"
        )


def resolve_component_version(root: Path, version: dict[str, Any], product_version: str, component_id: str) -> Any:
    kind = version.get("kind")
    if kind == "product":
        actual: Any = product_version
    elif kind == "literal":
        actual = version.get("value")
    elif kind == "text":
        source = payload_path(root, version.get("path"), label=f"component {component_id} version source")
        actual = read_text(source, label=f"component {component_id} version source")
    elif kind == "json":
        source = payload_path(root, version.get("path"), label=f"component {component_id} version source")
        actual = json_field(
            read_json(source, label=f"component {component_id} version source"),
            version.get("field"),
            label=f"component {component_id} version source",
        )
    elif kind == "spki_sha256":
        source = payload_path(root, version.get("path"), label=f"component {component_id} version source")
        try:
            completed = subprocess.run(
                ["openssl", "pkey", "-pubin", "-in", str(source), "-outform", "DER"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            raise PayloadContractError(
                f"component {component_id} SPKI verification requires openssl"
            ) from exc
        if completed.returncode != 0:
            raise PayloadContractError(
                f"component {component_id} is not a valid public key"
            )
        actual = hashlib.sha256(completed.stdout).hexdigest()
    else:
        raise PayloadContractError(f"component {component_id} has unsupported version kind {kind}")

    if "expected" in version and actual != version["expected"]:
        raise PayloadContractError(
            f"component {component_id} version {actual} does not match expected {version['expected']}"
        )
    if version.get("equals_product") is True and actual != product_version:
        raise PayloadContractError(
            f"{component_id} version {actual} does not match product version {product_version}"
        )
    pattern = version.get("pattern")
    if pattern and re.fullmatch(str(pattern), str(actual)) is None:
        raise PayloadContractError(f"component {component_id} version {actual} does not match {pattern}")
    return actual


def verify_payload(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    try:
        root_metadata = os.lstat(root)
    except OSError as exc:
        raise PayloadContractError(f"cannot lstat payload root: {root}") from exc
    if stat.S_ISLNK(root_metadata.st_mode):
        raise PayloadContractError(f"payload root must not be a symlink: {root}")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise PayloadContractError(f"payload root is not a directory: {root}")

    contract_path = payload_path(root, EMBEDDED_CONTRACT, label="embedded payload contract")
    if not contract_path.is_file():
        raise PayloadContractError(f"embedded payload contract is missing: {EMBEDDED_CONTRACT}")
    contract = read_json(contract_path, label="embedded payload contract")
    if not isinstance(contract, dict):
        raise PayloadContractError("embedded payload contract must be an object")
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise PayloadContractError(f"unsupported payload contract schema: {contract.get('schema_version')}")
    if contract.get("contract_path") != EMBEDDED_CONTRACT:
        raise PayloadContractError(f"payload contract path must be {EMBEDDED_CONTRACT}")

    product_rule = contract.get("product_version")
    if not isinstance(product_rule, dict):
        raise PayloadContractError("payload contract product_version must be an object")
    version_path = payload_path(root, product_rule.get("source"), label="product version source")
    product_version = read_text(version_path, label="product version source")
    if re.fullmatch(str(product_rule.get("pattern") or ""), product_version) is None:
        raise PayloadContractError(f"invalid product version: {product_version}")

    components = contract.get("components")
    if not isinstance(components, list) or not components:
        raise PayloadContractError("payload contract components must be a non-empty array")
    checked: list[str] = []
    seen: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise PayloadContractError("payload contract component must be an object")
        component_id = str(component.get("id") or "")
        if not component_id or component_id in seen:
            raise PayloadContractError(f"duplicate or empty component id: {component_id}")
        seen.add(component_id)
        verify_type_and_mode(root, component)
        version = component.get("version")
        if not isinstance(version, dict):
            raise PayloadContractError(f"component {component_id} version must be an object")
        resolve_component_version(root, version, product_version, component_id)
        checked.append(component_id)

    if canonical_json_sha256(contract) != TRUSTED_CONTRACT_SHA256:
        raise PayloadContractError("embedded payload contract does not match trusted payload contract")

    payload_tree = audit_payload_tree(root)

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "product_version": product_version,
        "checked_components": checked,
        "payload_tree": payload_tree,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one assembled Taiji Linux payload root")
    parser.add_argument("--root", required=True, help="assembled package filesystem root")
    args = parser.parse_args()
    try:
        result = verify_payload(Path(args.root))
    except (OSError, PayloadContractError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
