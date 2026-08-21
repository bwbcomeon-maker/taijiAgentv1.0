"""Freeze and verify the Windows builder-input triplet."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packaging.pipeline.core.errors import PipelineError  # noqa: E402


COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
MANIFEST_SCHEMA = "taiji-windows-builder-input/v1"
TARGET_CONFIG_RELATIVE = Path("packaging/pipeline/targets/windows-x64.json")
ASSET_PROVENANCE_RELATIVE = Path("packaging/windows/asset-provenance.json")
TARGET_CONFIG_KEYS = {
    "allowed_source_branches",
    "architecture",
    "cache_requirements",
    "cache_root",
    "git",
    "host_alias",
    "iscc",
    "minimum_free_gib",
    "node",
    "npm",
    "powershell",
    "python",
    "remote_root",
    "schema",
    "target_id",
    "tar",
}
ASSET_PROVENANCE_KEYS = {
    "assets",
    "schema",
    "source_commit",
    "source_repository",
}
ASSET_ENTRY_KEYS = {
    "blob",
    "bytes",
    "decision",
    "mode",
    "sha256",
    "snapshot_path",
    "source_path",
}


def _pipeline_error(message: str, category: str) -> None:
    raise PipelineError(message, category=category)


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_env() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
    )
    return environment


def _input_paths(repo: Path, source_commit: str) -> dict[str, Path]:
    _require_commit(source_commit)
    stem = "taijiagent-windows-builder-input-{}".format(source_commit)
    repo = Path(repo).resolve()
    return {
        "archive": repo / (stem + ".tar.gz"),
        "manifest": repo / (stem + ".manifest.json"),
        "checksum": repo / (stem + ".tar.gz.sha256"),
    }


def _require_commit(source_commit: str) -> str:
    if not isinstance(source_commit, str) or COMMIT_RE.fullmatch(source_commit) is None:
        _pipeline_error("source commit must be a full lowercase commit SHA", "SOURCE_COMMIT_INVALID")
    return source_commit


def _run_git(repo: Path, args: list[str], *, text: bool):
    command = ["/usr/bin/git", "-C", str(Path(repo).resolve())] + list(args)
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
        env=_git_env(),
    )


def _git_text(repo: Path, args: list[str], category: str) -> str:
    result = _run_git(repo, args, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or "git command failed"
        _pipeline_error(detail, category)
    return str(result.stdout)


def _git_bytes(repo: Path, args: list[str], category: str) -> bytes:
    result = _run_git(repo, args, text=False)
    if result.returncode != 0:
        if isinstance(result.stderr, bytes):
            detail = result.stderr.decode("utf-8", errors="replace").strip()
        else:
            detail = str(result.stderr).strip()
        _pipeline_error(detail or "git command failed", category)
    return bytes(result.stdout)


def _strict_utf8_json(raw: bytes, label: str, category: str) -> dict[str, object]:
    if raw.startswith(b"\xef\xbb\xbf"):
        _pipeline_error("{} contains a BOM".format(label), category)
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        _pipeline_error("{} is not valid UTF-8 JSON: {}".format(label, exc), category)
    if not isinstance(payload, dict):
        _pipeline_error("{} must be a JSON object".format(label), category)
    return payload


def _strict_regular_bytes(path: Path, label: str, category: str, *, mode: int | None = None) -> bytes:
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        _pipeline_error("{} is unavailable: {}".format(label, exc), category)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
    ):
        _pipeline_error("{} is not a private regular file".format(label), category)
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        _pipeline_error("{} mode is not {:04o}".format(label, mode), category)
    try:
        return path.read_bytes()
    except OSError as exc:
        _pipeline_error("{} cannot be read: {}".format(label, exc), category)


def _repo_identity(repo: Path) -> dict[str, str]:
    repo = Path(repo).resolve()
    branch = _git_text(repo, ["branch", "--show-current"], "REPO_IDENTITY_MISMATCH").strip()
    head = _git_text(repo, ["rev-parse", "HEAD"], "REPO_IDENTITY_MISMATCH").strip()
    tree = _git_text(repo, ["rev-parse", "HEAD^{tree}"], "REPO_IDENTITY_MISMATCH").strip()
    status = _git_text(repo, ["status", "--porcelain"], "REPO_IDENTITY_MISMATCH")
    if branch != "main":
        _pipeline_error("builder input can only be created from main", "BRANCH_NOT_MAIN")
    if status.strip():
        _pipeline_error("builder input requires a clean worktree", "WORKTREE_NOT_CLEAN")
    if COMMIT_RE.fullmatch(head) is None or COMMIT_RE.fullmatch(tree) is None:
        _pipeline_error("repository identity is invalid", "REPO_IDENTITY_MISMATCH")
    return {"branch": branch, "head": head, "tree": tree}


def _commit_tree(repo: Path, source_commit: str) -> str:
    commit = _git_text(repo, ["rev-parse", "{}^{{commit}}".format(source_commit)], "SOURCE_COMMIT_INVALID").strip()
    if commit != source_commit:
        _pipeline_error("source commit must be full and exact", "SOURCE_COMMIT_INVALID")
    tree = _git_text(repo, ["rev-parse", "{}^{{tree}}".format(source_commit)], "SOURCE_COMMIT_INVALID").strip()
    if COMMIT_RE.fullmatch(tree) is None:
        _pipeline_error("source tree is invalid", "SOURCE_COMMIT_INVALID")
    return tree


def _version_from_commit(repo: Path, source_commit: str) -> str:
    version_raw = _git_bytes(repo, ["show", "{}:VERSION".format(source_commit)], "REPO_IDENTITY_MISMATCH")
    package_text = _git_text(
        repo,
        ["show", "{}:apps/taiji-desktop/package.json".format(source_commit)],
        "REPO_IDENTITY_MISMATCH",
    )
    if (
        version_raw.startswith(b"\xef\xbb\xbf")
        or not version_raw.endswith(b"\n")
        or version_raw.count(b"\n") != 1
        or version_raw.endswith(b"\r\n")
        or b"\r" in version_raw
    ):
        _pipeline_error("VERSION must be exact committed X.Y.Z LF bytes", "REPO_IDENTITY_MISMATCH")
    try:
        version = version_raw[:-1].decode("utf-8", errors="strict")
    except UnicodeError as exc:
        _pipeline_error("VERSION is not valid UTF-8: {}".format(exc), "REPO_IDENTITY_MISMATCH")
    if VERSION_RE.fullmatch(version) is None:
        _pipeline_error("VERSION must contain exactly one X.Y.Z line", "REPO_IDENTITY_MISMATCH")
    try:
        package_payload = json.loads(package_text)
    except (TypeError, ValueError) as exc:
        _pipeline_error("desktop package.json is invalid: {}".format(exc), "REPO_IDENTITY_MISMATCH")
    if not isinstance(package_payload, dict) or package_payload.get("version") != version:
        _pipeline_error("VERSION and desktop package.json version differ", "REPO_IDENTITY_MISMATCH")
    return version


def _validate_target_config(payload: dict[str, object], label: str, category: str) -> None:
    if set(payload) != TARGET_CONFIG_KEYS:
        _pipeline_error("{} fields are not exact".format(label), category)
    if payload.get("schema") != "taiji-package-target/v2" or payload.get("target_id") != "windows-x64":
        _pipeline_error("{} identity is invalid".format(label), category)


def _validate_asset_provenance(payload: dict[str, object], label: str, category: str) -> None:
    if set(payload) != ASSET_PROVENANCE_KEYS:
        _pipeline_error("{} fields are not exact".format(label), category)
    if payload.get("schema") != "taiji-windows-legacy-asset-provenance/v1":
        _pipeline_error("{} schema is invalid".format(label), category)
    if not isinstance(payload.get("source_commit"), str) or COMMIT_RE.fullmatch(payload["source_commit"]) is None:
        _pipeline_error("{} source_commit is invalid".format(label), category)
    if not isinstance(payload.get("source_repository"), str) or not payload["source_repository"]:
        _pipeline_error("{} source_repository is invalid".format(label), category)
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        _pipeline_error("{} assets are invalid".format(label), category)
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict) or set(asset) != ASSET_ENTRY_KEYS:
            _pipeline_error("{} asset {} fields are invalid".format(label, index), category)
        if (
            not isinstance(asset["blob"], str)
            or COMMIT_RE.fullmatch(asset["blob"]) is None
            or type(asset["bytes"]) is not int
            or asset["bytes"] <= 0
            or not isinstance(asset["decision"], str)
            or not isinstance(asset["mode"], str)
            or SHA256_RE.fullmatch(str(asset["sha256"])) is None
            or not isinstance(asset["snapshot_path"], str)
            or not isinstance(asset["source_path"], str)
        ):
            _pipeline_error("{} asset {} identity is invalid".format(label, index), category)


def _bound_json_payload(
    repo: Path,
    source_commit: str,
    provided_path: Path | str,
    relative_path: Path,
    label: str,
    validator,
    *,
    path_category: str = "PLAN_INVALID",
    validation_category: str = "PLAN_INVALID",
    drift_category: str = "REPO_IDENTITY_MISMATCH",
) -> tuple[dict[str, object], str]:
    repo = Path(repo).resolve()
    provided_path = Path(provided_path).resolve()
    expected_path = (repo / relative_path).resolve()
    if provided_path != expected_path:
        _pipeline_error("{} must use {}".format(label, expected_path), path_category)
    worktree_raw = _strict_regular_bytes(provided_path, label, validation_category)
    committed_raw = _git_bytes(
        repo,
        ["show", "{}:{}".format(source_commit, relative_path.as_posix())],
        validation_category,
    )
    if worktree_raw != committed_raw:
        _pipeline_error("{} worktree bytes drifted from source commit".format(label), drift_category)
    payload = _strict_utf8_json(worktree_raw, label, validation_category)
    validator(payload, label, validation_category)
    return payload, _sha256_bytes(_canonical_json_bytes(payload))


def _expected_archive_bytes(repo: Path, source_commit: str) -> bytes:
    common_dir_raw = _git_text(
        repo,
        ["rev-parse", "--git-common-dir"],
        "INPUT_VERIFICATION_FAILED",
    ).strip()
    common_dir = Path(common_dir_raw)
    if not common_dir.is_absolute():
        common_dir = Path(repo) / common_dir
    object_dir = (common_dir.resolve() / "objects").resolve()
    try:
        object_metadata = object_dir.lstat()
    except OSError as exc:
        _pipeline_error("Git object directory is unavailable: {}".format(exc), "INPUT_VERIFICATION_FAILED")
    if not stat.S_ISDIR(object_metadata.st_mode) or stat.S_ISLNK(object_metadata.st_mode):
        _pipeline_error("Git object directory is invalid", "INPUT_VERIFICATION_FAILED")

    # A throwaway bare Git view keeps tracked attributes while excluding the
    # controller repository's config, replace refs, grafts, and info/attributes.
    with tempfile.TemporaryDirectory(prefix="taiji-windows-archive-git-") as temporary:
        isolated_git_dir = Path(temporary) / "git"
        alternates_path = isolated_git_dir / "objects/info/alternates"
        alternates_path.parent.mkdir(mode=0o700, parents=True)
        (isolated_git_dir / "refs").mkdir(mode=0o700)
        _write_private(isolated_git_dir / "HEAD", b"ref: refs/heads/main\n")
        _write_private(alternates_path, (str(object_dir) + "\n").encode("utf-8"))
        archive_tar = _git_bytes(
            repo,
            [
                "--git-dir",
                str(isolated_git_dir),
                "-c",
                "core.attributesFile={}".format(os.devnull),
                "-c",
                "tar.umask=0000",
                "archive",
                "--format=tar",
                source_commit,
            ],
            "INPUT_VERIFICATION_FAILED",
        )
    archive_buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=archive_buffer, mtime=0) as handle:
        handle.write(archive_tar)
    return archive_buffer.getvalue()


def _read_manifest(path: Path) -> dict[str, object]:
    payload = _strict_regular_bytes(path, "input manifest", "INPUT_VERIFICATION_FAILED", mode=0o600)
    if payload.startswith(b"\xef\xbb\xbf") or not payload.endswith(b"\n") or payload[:-1].endswith(b"\n"):
        _pipeline_error("input manifest is not canonical JSON", "INPUT_VERIFICATION_FAILED")
    try:
        value = json.loads(payload[:-1].decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        _pipeline_error("input manifest is invalid JSON: {}".format(exc), "INPUT_VERIFICATION_FAILED")
    if _canonical_json_bytes(value) + b"\n" != payload:
        _pipeline_error("input manifest is not canonical JSON", "INPUT_VERIFICATION_FAILED")
    if not isinstance(value, dict):
        _pipeline_error("input manifest must be an object", "INPUT_VERIFICATION_FAILED")
    return value


def _verify_paths_exist(repo: Path, source_commit: str):
    paths = _input_paths(repo, source_commit)
    present = {name: (path.exists() or path.is_symlink()) for name, path in paths.items()}
    if not any(present.values()):
        return None
    if not all(present.values()):
        _pipeline_error("builder input triplet is partial", "INPUT_TRIPLET_PARTIAL")
    return paths


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:
        handle.write(payload)
    os.chmod(path, 0o600)


def _temporary_path(final_path: Path) -> Path:
    return final_path.parent / ".{}.{}.tmp".format(final_path.name, uuid.uuid4().hex)


def _publish_triplet(temporary_paths: dict[str, Path], final_paths: dict[str, Path]) -> None:
    created = []
    try:
        for name in ("archive", "manifest", "checksum"):
            os.link(str(temporary_paths[name]), str(final_paths[name]))
            created.append(final_paths[name])
            os.chmod(final_paths[name], 0o600)
    except FileExistsError as exc:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        _pipeline_error("builder input output already exists: {}".format(exc), "INPUT_ALREADY_EXISTS")
    except OSError as exc:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        _pipeline_error("cannot publish builder input triplet: {}".format(exc), "INPUT_CREATION_FAILED")
    finally:
        for path in temporary_paths.values():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _reusable_result(
    source_commit: str,
    source_tree: str,
    version: str,
    paths: dict[str, Path],
    archive_payload: bytes,
    manifest_payload: bytes,
    checksum_payload: bytes,
) -> dict[str, object]:
    return {
        "status": "REUSABLE",
        "source_commit": source_commit,
        "source_tree": source_tree,
        "version": version,
        "files": {
            "archive": {
                "path": str(paths["archive"]),
                "basename": paths["archive"].name,
                "bytes": len(archive_payload),
                "sha256": _sha256_bytes(archive_payload),
            },
            "manifest": {
                "path": str(paths["manifest"]),
                "basename": paths["manifest"].name,
                "bytes": len(manifest_payload),
                "sha256": _sha256_bytes(manifest_payload),
            },
            "checksum": {
                "path": str(paths["checksum"]),
                "basename": paths["checksum"].name,
                "bytes": len(checksum_payload),
                "sha256": _sha256_bytes(checksum_payload),
            },
        },
    }


def inspect_input(repo: Path | str, source_commit: str) -> dict[str, object]:
    repo = Path(repo).resolve()
    source_commit = _require_commit(source_commit)
    paths = _verify_paths_exist(repo, source_commit)
    if paths is None:
        return {"status": "MISSING", "files": {}}
    return verify_input(repo, source_commit)


def verify_input(repo: Path | str, source_commit: str) -> dict[str, object]:
    repo = Path(repo).resolve()
    source_commit = _require_commit(source_commit)
    paths = _verify_paths_exist(repo, source_commit)
    if paths is None:
        return {"status": "MISSING", "files": {}}

    expected_tree = _commit_tree(repo, source_commit)
    expected_version = _version_from_commit(repo, source_commit)
    _target_payload, expected_target_sha = _bound_json_payload(
        repo,
        source_commit,
        repo / TARGET_CONFIG_RELATIVE,
        TARGET_CONFIG_RELATIVE,
        "target config",
        _validate_target_config,
        path_category="INPUT_VERIFICATION_FAILED",
        validation_category="INPUT_VERIFICATION_FAILED",
        drift_category="INPUT_VERIFICATION_FAILED",
    )
    _asset_payload, expected_asset_sha = _bound_json_payload(
        repo,
        source_commit,
        repo / ASSET_PROVENANCE_RELATIVE,
        ASSET_PROVENANCE_RELATIVE,
        "asset provenance",
        _validate_asset_provenance,
        path_category="INPUT_VERIFICATION_FAILED",
        validation_category="INPUT_VERIFICATION_FAILED",
        drift_category="INPUT_VERIFICATION_FAILED",
    )
    expected_archive_payload = _expected_archive_bytes(repo, source_commit)

    archive_payload = _strict_regular_bytes(paths["archive"], "input archive", "INPUT_VERIFICATION_FAILED", mode=0o600)
    manifest_payload = _strict_regular_bytes(paths["manifest"], "input manifest", "INPUT_VERIFICATION_FAILED", mode=0o600)
    checksum_payload = _strict_regular_bytes(paths["checksum"], "input checksum", "INPUT_VERIFICATION_FAILED", mode=0o600)
    manifest = _read_manifest(paths["manifest"])

    expected_keys = {
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
    if set(manifest) != expected_keys:
        _pipeline_error("input manifest fields are not exact", "INPUT_VERIFICATION_FAILED")
    if (
        manifest["schema"] != MANIFEST_SCHEMA
        or manifest["source_commit"] != source_commit
        or manifest["source_tree"] != expected_tree
        or manifest["version"] != expected_version
        or manifest["source_branch"] != "main"
        or manifest["archive_basename"] != paths["archive"].name
        or manifest["archive_bytes"] != len(expected_archive_payload)
        or manifest["archive_sha256"] != _sha256_bytes(expected_archive_payload)
        or manifest["target_config_sha256"] != expected_target_sha
        or manifest["asset_provenance_sha256"] != expected_asset_sha
    ):
        _pipeline_error("input manifest identity drifted", "INPUT_VERIFICATION_FAILED")
    if not isinstance(manifest["created_at"], str) or UTC_RE.fullmatch(manifest["created_at"]) is None:
        _pipeline_error("input manifest created_at is invalid", "INPUT_VERIFICATION_FAILED")
    for key in ("archive_sha256", "target_config_sha256", "asset_provenance_sha256"):
        if SHA256_RE.fullmatch(str(manifest[key])) is None:
            _pipeline_error("input manifest {} is invalid".format(key), "INPUT_VERIFICATION_FAILED")

    if archive_payload != expected_archive_payload:
        _pipeline_error("input archive bytes do not match source commit", "INPUT_VERIFICATION_FAILED")

    expected_checksum = (
        "{}  {}\n{}  {}\n".format(
            _sha256_bytes(expected_archive_payload),
            paths["archive"].name,
            _sha256_bytes(manifest_payload),
            paths["manifest"].name,
        )
    ).encode("utf-8")
    if checksum_payload != expected_checksum:
        _pipeline_error("input checksum does not match archive and manifest", "INPUT_VERIFICATION_FAILED")

    return _reusable_result(
        source_commit,
        expected_tree,
        expected_version,
        paths,
        archive_payload,
        manifest_payload,
        checksum_payload,
    )


def create_input(
    repo: Path | str,
    source_commit: str,
    target_config_path: Path | str,
    asset_provenance_path: Path | str,
) -> dict[str, object]:
    repo = Path(repo).resolve()
    source_commit = _require_commit(source_commit)
    identity = _repo_identity(repo)
    if identity["head"] != source_commit:
        _pipeline_error("source commit drifted from clean main HEAD", "SOURCE_COMMIT_DRIFTED")
    source_tree = _commit_tree(repo, source_commit)
    version = _version_from_commit(repo, source_commit)
    _target_payload, target_config_sha = _bound_json_payload(
        repo,
        source_commit,
        target_config_path,
        TARGET_CONFIG_RELATIVE,
        "target config",
        _validate_target_config,
    )
    _asset_payload, asset_provenance_sha = _bound_json_payload(
        repo,
        source_commit,
        asset_provenance_path,
        ASSET_PROVENANCE_RELATIVE,
        "asset provenance",
        _validate_asset_provenance,
    )
    paths = _input_paths(repo, source_commit)
    if any(path.exists() or path.is_symlink() for path in paths.values()):
        _pipeline_error("builder input output already exists", "INPUT_ALREADY_EXISTS")

    archive_payload = _expected_archive_bytes(repo, source_commit)
    archive_sha = _sha256_bytes(archive_payload)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "version": version,
        "source_branch": "main",
        "archive_basename": paths["archive"].name,
        "archive_bytes": len(archive_payload),
        "archive_sha256": archive_sha,
        "target_config_sha256": target_config_sha,
        "asset_provenance_sha256": asset_provenance_sha,
        "created_at": _utc_now(),
    }
    manifest_payload = _canonical_json_bytes(manifest) + b"\n"
    checksum_payload = (
        "{}  {}\n{}  {}\n".format(
            archive_sha,
            paths["archive"].name,
            _sha256_bytes(manifest_payload),
            paths["manifest"].name,
        )
    ).encode("utf-8")

    temporary_paths = {name: _temporary_path(path) for name, path in paths.items()}
    try:
        _write_private(temporary_paths["archive"], archive_payload)
        _write_private(temporary_paths["manifest"], manifest_payload)
        _write_private(temporary_paths["checksum"], checksum_payload)
        _publish_triplet(temporary_paths, paths)
    finally:
        for path in temporary_paths.values():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return _reusable_result(
        source_commit,
        source_tree,
        version,
        paths,
        archive_payload,
        manifest_payload,
        checksum_payload,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    for command in ("inspect", "verify"):
        item = subparsers.add_parser(command)
        item.add_argument("--repo", required=True)
        item.add_argument("--source-commit", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--repo", required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--target-config", required=True)
    create.add_argument("--asset-provenance", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    try:
        if arguments.command == "inspect":
            result = inspect_input(arguments.repo, arguments.source_commit)
        elif arguments.command == "verify":
            result = verify_input(arguments.repo, arguments.source_commit)
        elif arguments.command == "create":
            result = create_input(
                arguments.repo,
                arguments.source_commit,
                arguments.target_config,
                arguments.asset_provenance,
            )
        else:
            parser.error("unknown command")
        json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except PipelineError as exc:
        sys.stderr.write("{}: {}\n".format(exc.category, exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
