#!/usr/bin/env python3
"""Create the fixed, reproducible taiji-kylin-packaging Skill bundle."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


SKILL_NAME = "taiji-kylin-packaging"
ARTIFACT_NAME = SKILL_NAME + ".skill"
INVENTORY_SCHEMA = "taiji-skill-package-inventory/v1"
ALLOWED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/agent-installation.md",
    "references/deb-offline-delivery.md",
    "references/failure-playbook.md",
    "references/kylin-deb-version-history.md",
    "references/privacy-surface-gate.md",
    "references/release-gates.md",
    "scripts/doctor.py",
)
SOURCE_FILES = set(ALLOWED_FILES) | {"evals/evals.json"}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
TEXT_MODE = 0o644
SCRIPT_MODE = 0o755
SENSITIVE_CONTENT_PATTERNS = (
    re.compile(br"-----BEGIN(?: [A-Z0-9]+){0,4} PRIVATE KEY-----"),
    re.compile(br"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(br"/(?:Users|home)/[A-Za-z0-9._-]+(?:/|$)"),
)


class PackageError(RuntimeError):
    """The Skill source or generated archive violated its allowlist."""


def _reject_sensitive_content(payload: bytes, relative: str) -> None:
    if any(pattern.search(payload) for pattern in SENSITIVE_CONTENT_PATTERNS):
        raise PackageError("Skill member contains sensitive content: {}".format(relative))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _existing_directory(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise PackageError("{} must be an absolute path".format(label))
    try:
        metadata = path.lstat()
        path.resolve(strict=True)
    except OSError as exc:
        raise PackageError("{} is missing".format(label)) from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise PackageError("{} must be a real canonical directory".format(label))
    return path


def _new_output_directory(path: Path, skill_root: Path) -> Path:
    if not path.is_absolute():
        raise PackageError("output directory must be an absolute path")
    parent = _existing_directory(path.parent, "output directory parent")
    candidate = parent.resolve(strict=True) / path.name
    try:
        candidate.relative_to(skill_root.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise PackageError("output directory must be outside the Skill source tree")
    if os.path.lexists(str(candidate)):
        raise PackageError("output directory must not already exist")
    try:
        os.mkdir(str(candidate), 0o700)
    except OSError as exc:
        raise PackageError("output directory cannot be created") from exc
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise PackageError("output directory disappeared after creation") from exc
    if candidate.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise PackageError("output directory creation was unsafe")
    return candidate


def _source_paths(root: Path) -> Dict[str, Path]:
    try:
        entries = list(root.rglob("*"))
    except OSError as exc:
        raise PackageError("Skill source cannot be enumerated") from exc
    actual = set()
    folded = set()
    paths = {}  # type: Dict[str, Path]
    for path in entries:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise PackageError("Skill source contains a symlink: {}".format(relative))
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise PackageError("Skill source changed while enumerated") from exc
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise PackageError("Skill source contains a special file: {}".format(relative))
        if metadata.st_nlink != 1:
            raise PackageError("Skill source contains a hard-linked file: {}".format(relative))
        folded_name = relative.casefold()
        if folded_name in folded:
            raise PackageError("Skill source contains a case-colliding path")
        folded.add(folded_name)
        actual.add(relative)
        paths[relative] = path
    if actual != SOURCE_FILES:
        unexpected = sorted(actual - SOURCE_FILES)
        missing = sorted(SOURCE_FILES - actual)
        raise PackageError("Skill source allowlist mismatch; unexpected={}; missing={}".format(unexpected, missing))
    return paths


def _read_member(path: Path, relative: str) -> Tuple[bytes, int]:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_nlink != 1
        or before.st_size < 0
        or before.st_size > MAX_FILE_BYTES
    ):
        raise PackageError("Skill member is unsafe: {}".format(relative))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise PackageError("Skill member cannot be opened: {}".format(relative)) from exc
    try:
        opened = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if before_identity != opened_identity:
            raise PackageError("Skill member changed before read: {}".format(relative))
        remaining = opened.st_size
        chunks = []  # type: List[bytes]
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise PackageError("Skill member ended early: {}".format(relative))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise PackageError("Skill member grew during read: {}".format(relative))
        after = os.fstat(descriptor)
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if after_identity != opened_identity:
            raise PackageError("Skill member changed during read: {}".format(relative))
        payload = b"".join(chunks)
        _reject_sensitive_content(payload, relative)
        return payload, SCRIPT_MODE if relative == "scripts/doctor.py" else TEXT_MODE
    finally:
        os.close(descriptor)


def _exclusive_write(path: Path, payload: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags, mode)
    except OSError as exc:
        raise PackageError("output is already reserved or unsafe: {}".format(path.name)) from exc
    failed = True
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PackageError("cannot write output: {}".format(path.name))
            view = view[written:]
        os.fsync(descriptor)
        failed = False
    finally:
        os.close(descriptor)
        if failed:
            try:
                path.unlink()
            except OSError:
                pass


def _build_archive(members: Sequence[Dict[str, Any]]) -> bytes:
    import io

    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED, allowZip64=False) as bundle:
        for member in members:
            info = zipfile.ZipInfo(str(member["path"]), date_time=FIXED_DATE)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | int(member["mode"])) << 16
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            bundle.writestr(info, member["payload"])
    return output.getvalue()


def _verify_archive(payload: bytes, members: Sequence[Dict[str, Any]]) -> None:
    import io

    expected = {str(member["path"]): member for member in members}
    try:
        with zipfile.ZipFile(io.BytesIO(payload), mode="r") as bundle:
            infos = bundle.infolist()
            if [info.filename for info in infos] != [member["path"] for member in members]:
                raise PackageError("generated ZIP member order or allowlist is invalid")
            for info in infos:
                member = expected[info.filename]
                extracted = bundle.read(info)
                mode = (info.external_attr >> 16) & 0o777
                if (
                    info.compress_type != zipfile.ZIP_STORED
                    or info.date_time != FIXED_DATE
                    or mode != member["mode"]
                    or info.file_size != member["bytes"]
                    or info.CRC != member["crc32"]
                    or _sha256(extracted) != member["sha256"]
                ):
                    raise PackageError("generated ZIP member verification failed: {}".format(info.filename))
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError("generated ZIP cannot be verified") from exc


def package(skill_root: Path, output_dir: Path) -> Dict[str, Any]:
    skill_root = _existing_directory(skill_root, "skill root")
    if skill_root.name != SKILL_NAME:
        raise PackageError("skill root basename must be {}".format(SKILL_NAME))
    output_dir = _new_output_directory(output_dir, skill_root)
    sources = _source_paths(skill_root)
    members = []  # type: List[Dict[str, Any]]
    total = 0
    for relative in sorted(ALLOWED_FILES):
        payload, mode = _read_member(sources[relative], relative)
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise PackageError("Skill package exceeds the total size limit")
        members.append(
            {
                "path": "{}/{}".format(SKILL_NAME, relative),
                "mode": mode,
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "crc32": binascii.crc32(payload) & 0xFFFFFFFF,
                "payload": payload,
            }
        )
    archive_payload = _build_archive(members)
    _verify_archive(archive_payload, members)
    archive_path = output_dir / ARTIFACT_NAME
    sidecar_path = output_dir / (ARTIFACT_NAME + ".sha256")
    inventory_path = output_dir / (ARTIFACT_NAME + ".inventory.json")
    for output in (archive_path, sidecar_path, inventory_path):
        if os.path.lexists(str(output)):
            raise PackageError("output is already reserved: {}".format(output.name))
    archive_sha = _sha256(archive_payload)
    inventory = {
        "schema": INVENTORY_SCHEMA,
        "artifact": {
            "basename": ARTIFACT_NAME,
            "bytes": len(archive_payload),
            "sha256": archive_sha,
        },
        "members": [
            {
                "path": member["path"],
                "mode": format(member["mode"], "04o"),
                "bytes": member["bytes"],
                "sha256": member["sha256"],
            }
            for member in members
        ],
    }
    inventory_payload = (json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    sidecar_payload = "{}  {}\n".format(archive_sha, ARTIFACT_NAME).encode("ascii")
    published = []  # type: List[Path]
    try:
        _exclusive_write(archive_path, archive_payload, 0o644)
        published.append(archive_path)
        _exclusive_write(sidecar_path, sidecar_payload, 0o644)
        published.append(sidecar_path)
        _exclusive_write(inventory_path, inventory_payload, 0o644)
        published.append(inventory_path)
    except Exception:
        for path in reversed(published):
            try:
                path.unlink()
            except OSError:
                pass
        raise
    if archive_path.read_bytes() != archive_payload:
        raise PackageError("published archive changed after write")
    _verify_archive(archive_path.read_bytes(), members)
    return inventory


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--skill-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        inventory = package(args.skill_root, args.output_dir)
    except (PackageError, OSError, ValueError) as exc:
        sys.stderr.write("taiji-skill-package-failed\t{}\n".format(exc))
        return 1
    sys.stdout.write(json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
