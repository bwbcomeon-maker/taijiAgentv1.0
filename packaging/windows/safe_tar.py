"""Safely extract a UTF-8 tarball into a run/source subdirectory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import unicodedata
from pathlib import Path


DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CLOCK$"} | {
    "COM{}".format(index) for index in range(1, 10)
} | {
    "LPT{}".format(index) for index in range(1, 10)
}
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
MANIFEST_SCHEMA = "taiji-windows-builder-input/v1"
MANIFEST_KEYS = {
    "schema",
    "source_commit",
    "source_tree",
    "version",
    "source_branch",
    "archive_basename",
    "archive_bytes",
    "archive_sha256",
    "target_config_sha256",
    "asset_provenance_sha256",
    "created_at",
}


class SafeTarError(RuntimeError):
    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


def _raise(message: str, category: str) -> None:
    raise SafeTarError(message, category=category)


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _current_uid() -> int | None:
    getter = getattr(os, "getuid", None)
    if getter is None:
        return None
    return int(getter())


def _require_private_dir(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        _raise("{} is unavailable: {}".format(label, exc), "SAFE_TAR_TARGET_INVALID")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _raise("{} must be an existing non-link directory".format(label), "SAFE_TAR_TARGET_INVALID")
    owner = _current_uid()
    if owner is not None and metadata.st_uid != owner:
        _raise("{} owner is invalid".format(label), "SAFE_TAR_TARGET_INVALID")
    if owner is not None and stat.S_IMODE(metadata.st_mode) != 0o700:
        _raise("{} mode is not 0700".format(label), "SAFE_TAR_TARGET_INVALID")


def _absolute_path(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _windows_extended_path_text(path: Path | str) -> str:
    text = os.fspath(path)
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\" + text


def _open_private_regular(path: Path, label: str, category: str):
    path = _absolute_path(path)
    descriptor = None
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _raise("{} must be a private regular file".format(label), category)
        flags = os.O_RDONLY
        for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW"):
            flags |= int(getattr(os, flag_name, 0))
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        owner = _current_uid()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)
            or (owner is not None and metadata.st_uid != owner)
            or (owner is not None and stat.S_IMODE(metadata.st_mode) != 0o600)
        ):
            _raise("{} must be an owned 0600 regular single-link file".format(label), category)
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        return stream
    except SafeTarError:
        raise
    except OSError as exc:
        _raise("{} is unavailable: {}".format(label, exc), category)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _copy_frozen_snapshot(source, destination) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    source.seek(0)
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        destination.write(chunk)
        total += len(chunk)
    destination.flush()
    destination.seek(0)
    return total, digest.hexdigest()


def _target_root(target_dir: Path) -> Path:
    target_dir = Path(target_dir)
    if target_dir.exists() or target_dir.is_symlink():
        _raise("target must not exist", "SAFE_TAR_TARGET_INVALID")
    source_dir = target_dir.parent
    if source_dir.name != "source":
        _raise("target must be a direct child of source", "SAFE_TAR_TARGET_INVALID")
    _require_private_dir(source_dir, "source directory")
    run_root = source_dir.parent
    _require_private_dir(run_root, "run root")
    resolved = target_dir.resolve(strict=False)
    if os.name == "nt":
        return Path(_windows_extended_path_text(resolved))
    return resolved


def _read_manifest(
    raw: bytes,
    archive_path: Path,
    archive_bytes: int,
    archive_sha256: str,
) -> dict[str, object]:
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n") or raw[:-1].endswith(b"\n"):
        _raise("manifest must be canonical UTF-8 JSON with one LF", "SAFE_TAR_MANIFEST_INVALID")
    try:
        payload = json.loads(raw[:-1].decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        _raise("manifest is invalid JSON: {}".format(exc), "SAFE_TAR_MANIFEST_INVALID")
    if not isinstance(payload, dict) or set(payload) != MANIFEST_KEYS:
        _raise("manifest fields are not exact", "SAFE_TAR_MANIFEST_INVALID")
    if _canonical_json_bytes(payload) + b"\n" != raw:
        _raise("manifest is not canonical JSON", "SAFE_TAR_MANIFEST_INVALID")
    if (
        payload.get("schema") != MANIFEST_SCHEMA
        or not isinstance(payload.get("source_commit"), str)
        or COMMIT_RE.fullmatch(payload["source_commit"]) is None
        or not isinstance(payload.get("source_tree"), str)
        or COMMIT_RE.fullmatch(payload["source_tree"]) is None
        or not isinstance(payload.get("version"), str)
        or VERSION_RE.fullmatch(payload["version"]) is None
        or payload.get("source_branch") != "main"
        or not isinstance(payload.get("target_config_sha256"), str)
        or SHA256_RE.fullmatch(payload["target_config_sha256"]) is None
        or not isinstance(payload.get("asset_provenance_sha256"), str)
        or SHA256_RE.fullmatch(payload["asset_provenance_sha256"]) is None
        or not isinstance(payload.get("created_at"), str)
        or UTC_RE.fullmatch(payload["created_at"]) is None
        or payload.get("archive_basename") != archive_path.name
        or payload.get("archive_bytes") != archive_bytes
        or payload.get("archive_sha256") != archive_sha256
    ):
        _raise("manifest archive identity drifted", "SAFE_TAR_MANIFEST_INVALID")
    return payload


def _normalized_parts(name: str) -> tuple[list[str], str]:
    if not isinstance(name, str) or not name:
        _raise("tar member name is empty", "SAFE_TAR_MEMBER_INVALID")
    if name != unicodedata.normalize("NFC", name):
        _raise("tar member name is not NFC", "SAFE_TAR_MEMBER_INVALID")
    try:
        encoded = name.encode("utf-8", errors="strict")
        encoded.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        _raise("tar member name is not UTF-8: {}".format(exc), "SAFE_TAR_MEMBER_INVALID")
    normalized = name.rstrip("/")
    if (
        not normalized
        or "\x00" in normalized
        or "\\" in normalized
        or ":" in normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or re.fullmatch(r"[A-Za-z]:.*", normalized) is not None
        or normalized.startswith("\\\\")
    ):
        _raise("tar member path is unsafe", "SAFE_TAR_MEMBER_INVALID")
    parts = normalized.split("/")
    for part in parts:
        if (
            not part
            or part in (".", "..")
            or part.endswith(".")
            or part.endswith(" ")
        ):
            _raise("tar member path escapes its root", "SAFE_TAR_MEMBER_INVALID")
        if part.split(".", 1)[0].upper() in DEVICE_NAMES:
            _raise("tar member path uses a Windows device name", "SAFE_TAR_MEMBER_INVALID")
    return parts, "/".join(parts)


def _collect_members(archive: tarfile.TarFile):
    collected = []
    file_paths = set()
    all_paths = {}
    for member in archive.getmembers():
        if member.issym() or member.islnk() or member.isdev():
            _raise("tar member type is forbidden", "SAFE_TAR_MEMBER_INVALID")
        if not member.isdir() and not member.isfile():
            _raise("tar member type is forbidden", "SAFE_TAR_MEMBER_INVALID")
        parts, normalized = _normalized_parts(member.name)
        identity = normalized.casefold()
        if identity in all_paths:
            _raise("tar contains a case-fold duplicate path", "SAFE_TAR_MEMBER_INVALID")
        all_paths[identity] = (member.isfile(), parts)
        if member.isfile():
            file_paths.add(identity)
        collected.append((member, parts))
    for identity, (_is_file, parts) in all_paths.items():
        ancestors = ["/".join(parts[:index]).casefold() for index in range(1, len(parts))]
        if any(ancestor in file_paths for ancestor in ancestors):
            _raise("tar member conflicts with a file parent", "SAFE_TAR_MEMBER_INVALID")
    return collected


def _chmod_tree(target_dir: Path) -> None:
    os.chmod(target_dir, 0o700)
    for entry in sorted(target_dir.rglob("*")):
        if entry.is_dir():
            os.chmod(entry, 0o700)
        else:
            os.chmod(entry, 0o600)


def extract_tar(
    archive_path: Path | str,
    destination_dir: Path | str,
    manifest_path: Path | str | None = None,
) -> Path:
    archive_path = _absolute_path(archive_path)
    destination_dir = _target_root(Path(destination_dir))
    if manifest_path is None:
        _raise("manifest is required", "SAFE_TAR_MANIFEST_INVALID")
    manifest_path = _absolute_path(manifest_path)

    with _open_private_regular(archive_path, "archive", "SAFE_TAR_ARCHIVE_INVALID") as archive_stream:
        with _open_private_regular(manifest_path, "manifest", "SAFE_TAR_MANIFEST_INVALID") as manifest_stream:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as frozen_archive:
                try:
                    archive_metadata = os.fstat(archive_stream.fileno())
                    archive_bytes, archive_sha256 = _copy_frozen_snapshot(archive_stream, frozen_archive)
                    manifest_raw = manifest_stream.read()
                except OSError as exc:
                    _raise("cannot read frozen input snapshot: {}".format(exc), "SAFE_TAR_ARCHIVE_INVALID")
                if archive_bytes != archive_metadata.st_size:
                    _raise("archive changed while freezing its snapshot", "SAFE_TAR_ARCHIVE_INVALID")
                _read_manifest(
                    manifest_raw,
                    archive_path,
                    archive_bytes,
                    archive_sha256,
                )

                archive = None
                try:
                    archive = tarfile.open(fileobj=frozen_archive, mode="r:*")
                    members = _collect_members(archive)
                except SafeTarError:
                    if archive is not None:
                        archive.close()
                    raise
                except (OSError, EOFError, tarfile.TarError) as exc:
                    if archive is not None:
                        archive.close()
                    _raise("archive cannot be safely read: {}".format(exc), "SAFE_TAR_ARCHIVE_INVALID")

                try:
                    destination_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
                    os.chmod(destination_dir, 0o700)
                    for member, parts in members:
                        destination = destination_dir.joinpath(*parts)
                        if member.isdir():
                            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                            continue
                        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            _raise("tar file member cannot be extracted", "SAFE_TAR_MEMBER_INVALID")
                        with open(destination, "xb") as handle:
                            while True:
                                chunk = extracted.read(1024 * 1024)
                                if not chunk:
                                    break
                                handle.write(chunk)
                    _chmod_tree(destination_dir)
                except SafeTarError:
                    if destination_dir.exists():
                        shutil.rmtree(destination_dir, ignore_errors=True)
                    raise
                except (OSError, EOFError, tarfile.TarError) as exc:
                    if destination_dir.exists():
                        shutil.rmtree(destination_dir, ignore_errors=True)
                    _raise("archive extraction failed: {}".format(exc), "SAFE_TAR_EXTRACTION_FAILED")
                finally:
                    archive.close()
    return destination_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    extract = subparsers.add_parser("extract")
    extract.add_argument("--archive", required=True)
    extract.add_argument("--destination", required=True)
    extract.add_argument("--manifest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    try:
        if arguments.command != "extract":
            parser.error("unknown command")
        target = extract_tar(arguments.archive, arguments.destination, arguments.manifest)
        print(target)
        return 0
    except SafeTarError as exc:
        print("{}: {}".format(exc.category, exc), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
