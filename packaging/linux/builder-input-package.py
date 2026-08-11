#!/usr/bin/env python3
"""Create the formal Linux builder-input archive from an exact file allowlist."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple


SCHEMA = "taiji-builder-input-package/v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STATIC_INPUT_NAMES = (
    "00_制包机_生成离线交付包.sh",
    "01_制包机_发布预检.sh",
    "02_目标终端_安装并验证.sh",
    "03_目标终端_导出诊断报告.sh",
    "04_目标终端_桌面App验收并导出证据.sh",
    "99_本机_准备制包输入包.sh",
    "SHA256SUMS.txt",
    "操作说明.md",
    "版本信息.txt",
)
SOURCE_HELPER_BASENAME = "source-archive-integrity.py"
BUILDER_HELPER_BASENAME = "builder-input-package.py"
INPUT_ROOT_BASENAME = "taijiagent 打包交付"
MAX_INPUT_FILE_BYTES = 2 * 1024 * 1024 * 1024
MANIFEST_KEYS = {
    "schema",
    "source_commit",
    "archive_basename",
    "archive_root_basename",
    "archive_size",
    "archive_sha256",
    "manifest_basename",
    "checksum_basename",
    "source_archive_basename",
    "source_archive_sha256",
    "source_inventory_basename",
    "source_inventory_sha256",
    "source_integrity_helper_sha256",
    "builder_input_helper_sha256",
    "members",
}
MEMBER_KEYS = {"basename", "mode", "sha256", "size"}


class BuilderInputError(RuntimeError):
    """The builder-input package did not satisfy the formal contract."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_regular(path: Path, label: str) -> Tuple[bytes, int]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise BuilderInputError("{} is missing".format(label)) from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise BuilderInputError("{} must be a regular non-symlink file".format(label))
    if before.st_uid != os.getuid() or before.st_nlink != 1:
        raise BuilderInputError("{} owner or hard-link count is unsafe".format(label))
    if stat.S_IMODE(before.st_mode) & 0o022:
        raise BuilderInputError("{} cannot be group/other writable".format(label))
    if before.st_size < 0 or before.st_size > MAX_INPUT_FILE_BYTES:
        raise BuilderInputError("{} size is outside the formal limit".format(label))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise BuilderInputError("{} cannot be opened safely".format(label)) from exc
    try:
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if opened_identity != identity:
            raise BuilderInputError("{} changed before it was read".format(label))
        chunks = []  # type: List[bytes]
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise BuilderInputError("{} ended before its declared size".format(label))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise BuilderInputError("{} grew while it was read".format(label))
        after = os.fstat(descriptor)
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if after_identity != opened_identity:
            raise BuilderInputError("{} changed while it was read".format(label))
        return b"".join(chunks), stat.S_IMODE(opened.st_mode)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes, mode: int = 0o644) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(str(path), flags, mode)
    except OSError as exc:
        raise BuilderInputError("output already exists or is unsafe: {}".format(path)) from exc
    failed = True
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise BuilderInputError("failed to write {}".format(path))
            view = view[written:]
        os.fsync(descriptor)
        failed = False
    finally:
        try:
            os.close(descriptor)
        finally:
            if failed:
                try:
                    path.unlink()
                except OSError:
                    pass


def _parse_source_checksums(
    payload: bytes,
    source_archive_name: str,
    source_inventory_name: str,
) -> Dict[str, str]:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise BuilderInputError("SHA256SUMS.txt must be ASCII") from exc
    if len(lines) != 2:
        raise BuilderInputError("SHA256SUMS.txt must contain exactly two entries")
    result = {}  # type: Dict[str, str]
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)", line)
        if match is None or match.group(2) in result:
            raise BuilderInputError("SHA256SUMS.txt contains an invalid entry")
        result[match.group(2)] = match.group(1)
    if set(result) != {source_archive_name, source_inventory_name}:
        raise BuilderInputError("SHA256SUMS.txt does not name the exact source inputs")
    return result


def _tar_payload(root_basename: str, members: Sequence[Dict[str, Any]]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        with tarfile.open(
            fileobj=compressed,
            mode="w|",
            format=tarfile.USTAR_FORMAT,
        ) as archive:
            for member in members:
                info = tarfile.TarInfo(
                    name=str(Path(root_basename) / str(member["basename"]))
                )
                payload = member["payload"]
                info.size = len(payload)
                info.mode = int(member["mode"])
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                info.type = tarfile.REGTYPE
                archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def _load_canonical_manifest(payload: bytes) -> Dict[str, Any]:
    def reject_duplicates(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result = {}  # type: Dict[str, Any]
        for key, value in pairs:
            if key in result:
                raise BuilderInputError("builder input manifest contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuilderInputError("builder input manifest is invalid JSON") from exc
    if type(value) is not dict or set(value) != MANIFEST_KEYS:
        raise BuilderInputError("builder input manifest fields are invalid")
    canonical = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if payload != canonical:
        raise BuilderInputError("builder input manifest is not canonical JSON")
    return value


def _parse_delivery_checksums(
    payload: bytes,
    archive_basename: str,
    manifest_basename: str,
) -> Dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BuilderInputError("builder input checksum must be UTF-8") from exc
    if len(lines) != 2:
        raise BuilderInputError("builder input checksum must contain exactly two entries")
    result = {}  # type: Dict[str, str]
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)", line)
        if match is None or match.group(2) in result:
            raise BuilderInputError("builder input checksum contains an invalid entry")
        result[match.group(2)] = match.group(1)
    if set(result) != {archive_basename, manifest_basename}:
        raise BuilderInputError("builder input checksum names are invalid")
    return result


def _validated_public_members(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_members = manifest.get("members")
    if type(raw_members) is not list or not raw_members:
        raise BuilderInputError("builder input manifest members are invalid")
    validated = []  # type: List[Dict[str, Any]]
    seen = set()
    for item in raw_members:
        if type(item) is not dict or set(item) != MEMBER_KEYS:
            raise BuilderInputError("builder input member fields are invalid")
        basename = item.get("basename")
        mode = item.get("mode")
        digest = item.get("sha256")
        size = item.get("size")
        if (
            type(basename) is not str
            or not basename
            or "/" in basename
            or "\\" in basename
            or basename in seen
            or type(mode) is not str
            or re.fullmatch(r"0[0-7]{3}", mode) is None
            or type(digest) is not str
            or SHA256_RE.fullmatch(digest) is None
            or type(size) is not int
            or type(size) is bool
            or size < 0
            or size > MAX_INPUT_FILE_BYTES
        ):
            raise BuilderInputError("builder input member identity is invalid")
        seen.add(basename)
        validated.append(item)
    if [item["basename"] for item in validated] != sorted(seen):
        raise BuilderInputError("builder input members are not canonically ordered")
    return validated


def _inspect_archive_members(
    archive_payload: bytes,
    root_basename: str,
    expected_members: Sequence[Dict[str, Any]],
) -> None:
    expected = {str(item["basename"]): item for item in expected_members}
    seen = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:gz") as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or len(path.parts) != 2
                    or path.parts[0] != root_basename
                    or path.parts[1] not in expected
                    or path.parts[1] in seen
                    or not member.isfile()
                    or member.islnk()
                    or member.issym()
                ):
                    raise BuilderInputError("builder input archive member is invalid")
                basename = path.parts[1]
                record = expected[basename]
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise BuilderInputError("builder input archive member cannot be read")
                payload = extracted.read(member.size + 1)
                if len(payload) != member.size:
                    raise BuilderInputError("builder input archive member size is invalid")
                if (
                    member.size != record["size"]
                    or format(member.mode & 0o777, "04o") != record["mode"]
                    or _sha256(payload) != record["sha256"]
                ):
                    raise BuilderInputError("builder input archive member differs from manifest")
                seen.add(basename)
    except (OSError, tarfile.TarError) as exc:
        raise BuilderInputError("builder input archive cannot be inspected") from exc
    if seen != set(expected):
        raise BuilderInputError("builder input archive member set is incomplete")


def _verify_extracted_members(
    extracted_dir: Path,
    expected_members: Sequence[Dict[str, Any]],
) -> None:
    try:
        metadata = extracted_dir.lstat()
    except OSError as exc:
        raise BuilderInputError("extracted builder input directory is missing") from exc
    if (
        extracted_dir.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise BuilderInputError("extracted builder input directory is unsafe")
    expected = {str(item["basename"]): item for item in expected_members}
    try:
        actual_names = {path.name for path in extracted_dir.iterdir()}
    except OSError as exc:
        raise BuilderInputError("extracted builder input directory cannot be listed") from exc
    if actual_names != set(expected):
        raise BuilderInputError("extracted builder input member set is invalid")
    for basename, record in expected.items():
        payload, mode = _read_regular(
            extracted_dir / basename,
            "extracted builder input {}".format(basename),
        )
        if (
            len(payload) != record["size"]
            or format(mode, "04o") != record["mode"]
            or _sha256(payload) != record["sha256"]
        ):
            raise BuilderInputError("extracted builder input member differs from manifest")


def verify_builder_input(
    *,
    archive_path: Path,
    manifest_path: Path,
    checksum_path: Path,
    extracted_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    archive_payload, _archive_mode = _read_regular(archive_path, "builder input archive")
    manifest_payload, _manifest_mode = _read_regular(manifest_path, "builder input manifest")
    checksum_payload, _checksum_mode = _read_regular(checksum_path, "builder input checksum")
    checksums = _parse_delivery_checksums(
        checksum_payload,
        archive_path.name,
        manifest_path.name,
    )
    if (
        checksums[archive_path.name] != _sha256(archive_payload)
        or checksums[manifest_path.name] != _sha256(manifest_payload)
    ):
        raise BuilderInputError("builder input checksum does not match the supplied files")
    expected_checksum_payload = (
        "{}  {}\n{}  {}\n".format(
            _sha256(archive_payload),
            archive_path.name,
            _sha256(manifest_payload),
            manifest_path.name,
        )
    ).encode("utf-8")
    if checksum_payload != expected_checksum_payload:
        raise BuilderInputError("builder input checksum is not canonical")
    manifest = _load_canonical_manifest(manifest_payload)
    if (
        manifest["schema"] != SCHEMA
        or type(manifest["source_commit"]) is not str
        or COMMIT_RE.fullmatch(manifest["source_commit"]) is None
        or manifest["archive_basename"] != archive_path.name
        or manifest["manifest_basename"] != manifest_path.name
        or manifest["checksum_basename"] != checksum_path.name
        or manifest["archive_root_basename"] != INPUT_ROOT_BASENAME
        or manifest["archive_size"] != len(archive_payload)
        or manifest["archive_sha256"] != _sha256(archive_payload)
    ):
        raise BuilderInputError("builder input manifest does not bind the supplied files")
    source_commit = manifest["source_commit"]
    expected_archive_name = "taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(
        source_commit
    )
    expected_inventory_name = expected_archive_name[:-7] + ".inventory.json"
    expected_input_name = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    if (
        archive_path.name != expected_input_name
        or manifest_path.name != "taijiagent-制包机输入-{}.manifest.json".format(source_commit)
        or checksum_path.name != expected_input_name + ".sha256"
        or manifest["source_archive_basename"] != expected_archive_name
        or manifest["source_inventory_basename"] != expected_inventory_name
    ):
        raise BuilderInputError("builder input filenames do not bind the source commit")
    members = _validated_public_members(manifest)
    expected_names = set(STATIC_INPUT_NAMES) | {
        expected_archive_name,
        expected_inventory_name,
        SOURCE_HELPER_BASENAME,
        BUILDER_HELPER_BASENAME,
    }
    if {item["basename"] for item in members} != expected_names:
        raise BuilderInputError("builder input manifest member set is invalid")
    by_name = {str(item["basename"]): item for item in members}
    if (
        manifest["source_archive_sha256"] != by_name[expected_archive_name]["sha256"]
        or manifest["source_inventory_sha256"] != by_name[expected_inventory_name]["sha256"]
        or manifest["source_integrity_helper_sha256"]
        != by_name[SOURCE_HELPER_BASENAME]["sha256"]
        or manifest["builder_input_helper_sha256"]
        != by_name[BUILDER_HELPER_BASENAME]["sha256"]
    ):
        raise BuilderInputError("builder input manifest member binding is invalid")
    _inspect_archive_members(
        archive_payload,
        INPUT_ROOT_BASENAME,
        members,
    )
    if extracted_dir is not None:
        _verify_extracted_members(extracted_dir, members)
    return manifest


def create_builder_input(
    *,
    source_dir: Path,
    source_integrity_helper: Path,
    output: Path,
    manifest_path: Path,
    checksum_path: Path,
    source_commit: str,
) -> Dict[str, Any]:
    if not COMMIT_RE.fullmatch(source_commit):
        raise BuilderInputError("source commit must be a full lowercase Git SHA")
    try:
        requested_source_metadata = source_dir.lstat()
    except OSError as exc:
        raise BuilderInputError("builder input source directory is unsafe") from exc
    if (
        source_dir.is_symlink()
        or not stat.S_ISDIR(requested_source_metadata.st_mode)
        or requested_source_metadata.st_uid != os.getuid()
        or stat.S_IMODE(requested_source_metadata.st_mode) & 0o022
    ):
        raise BuilderInputError("builder input source directory is unsafe")
    source_dir = source_dir.resolve(strict=True)
    if source_dir.name != INPUT_ROOT_BASENAME:
        raise BuilderInputError("builder input source directory basename is invalid")
    expected_archive_name = "taiji-agentv1.0-kylin-build-src-{}.tar.gz".format(
        source_commit
    )
    expected_inventory_name = expected_archive_name[:-7] + ".inventory.json"
    expected_output_name = "taijiagent-制包机输入-{}.tar.gz".format(source_commit)
    if output.name != expected_output_name:
        raise BuilderInputError("builder input archive basename does not match source commit")
    if manifest_path.name != "taijiagent-制包机输入-{}.manifest.json".format(source_commit):
        raise BuilderInputError("builder input manifest basename does not match source commit")
    if checksum_path.name != expected_output_name + ".sha256":
        raise BuilderInputError("builder input checksum basename is invalid")

    paths = [source_dir / name for name in STATIC_INPUT_NAMES]
    paths.extend(
        (
            source_dir / expected_archive_name,
            source_dir / expected_inventory_name,
        )
    )
    members = []  # type: List[Dict[str, Any]]
    for path in paths:
        payload, mode = _read_regular(path, "builder input {}".format(path.name))
        members.append(
            {
                "basename": path.name,
                "payload": payload,
                "mode": mode,
                "size": len(payload),
                "sha256": _sha256(payload),
            }
        )
    helper_payload, helper_mode = _read_regular(
        source_integrity_helper,
        "source archive integrity helper",
    )
    members.append(
        {
            "basename": SOURCE_HELPER_BASENAME,
            "payload": helper_payload,
            "mode": helper_mode,
            "size": len(helper_payload),
            "sha256": _sha256(helper_payload),
        }
    )
    builder_helper_payload, builder_helper_mode = _read_regular(
        Path(__file__),
        "builder input helper",
    )
    members.append(
        {
            "basename": BUILDER_HELPER_BASENAME,
            "payload": builder_helper_payload,
            "mode": builder_helper_mode,
            "size": len(builder_helper_payload),
            "sha256": _sha256(builder_helper_payload),
        }
    )
    if len({str(item["basename"]) for item in members}) != len(members):
        raise BuilderInputError("builder input allowlist contains duplicate basenames")
    members.sort(key=lambda item: str(item["basename"]))

    checksum_member = next(
        item for item in members if item["basename"] == "SHA256SUMS.txt"
    )
    source_checksums = _parse_source_checksums(
        checksum_member["payload"],
        expected_archive_name,
        expected_inventory_name,
    )
    by_name = {str(item["basename"]): item for item in members}
    for basename in (expected_archive_name, expected_inventory_name):
        if source_checksums[basename] != by_name[basename]["sha256"]:
            raise BuilderInputError("source checksum does not match {}".format(basename))

    archive_payload = _tar_payload(source_dir.name, members)
    archive_sha256 = _sha256(archive_payload)
    public_members = [
        {
            "basename": item["basename"],
            "mode": format(int(item["mode"]), "04o"),
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in members
    ]
    manifest = {
        "schema": SCHEMA,
        "source_commit": source_commit,
        "archive_basename": output.name,
        "archive_size": len(archive_payload),
        "archive_sha256": archive_sha256,
        "source_archive_basename": expected_archive_name,
        "source_archive_sha256": by_name[expected_archive_name]["sha256"],
        "source_inventory_basename": expected_inventory_name,
        "source_inventory_sha256": by_name[expected_inventory_name]["sha256"],
        "source_integrity_helper_sha256": _sha256(helper_payload),
        "builder_input_helper_sha256": _sha256(builder_helper_payload),
        "archive_root_basename": INPUT_ROOT_BASENAME,
        "manifest_basename": manifest_path.name,
        "checksum_basename": checksum_path.name,
        "members": public_members,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    checksum_payload = (
        "{}  {}\n{}  {}\n".format(
            archive_sha256,
            output.name,
            _sha256(manifest_payload),
            manifest_path.name,
        )
    ).encode("utf-8")

    published = []  # type: List[Path]
    try:
        _write_exclusive(output, archive_payload)
        published.append(output)
        _write_exclusive(manifest_path, manifest_payload)
        published.append(manifest_path)
        _write_exclusive(checksum_path, checksum_payload)
        published.append(checksum_path)
    except Exception:
        for path in reversed(published):
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return manifest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", allow_abbrev=False)
    create.add_argument("--source-dir", required=True, type=Path)
    create.add_argument("--source-integrity-helper", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--manifest", required=True, type=Path)
    create.add_argument("--checksum", required=True, type=Path)
    create.add_argument("--source-commit", required=True)
    verify = subparsers.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--archive", required=True, type=Path)
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--checksum", required=True, type=Path)
    verify.add_argument("--extracted-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_builder_input(
                source_dir=args.source_dir,
                source_integrity_helper=args.source_integrity_helper,
                output=args.output,
                manifest_path=args.manifest,
                checksum_path=args.checksum,
                source_commit=args.source_commit,
            )
            action = "created"
        else:
            manifest = verify_builder_input(
                archive_path=args.archive,
                manifest_path=args.manifest,
                checksum_path=args.checksum,
                extracted_dir=args.extracted_dir,
            )
            action = "verified"
    except BuilderInputError as exc:
        print("builder-input-package-failed\t{}".format(exc), file=sys.stderr)
        return 1
    print(
        "builder-input-package-{}\t{}\t{}".format(
            action, manifest["archive_basename"], manifest["archive_sha256"]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
