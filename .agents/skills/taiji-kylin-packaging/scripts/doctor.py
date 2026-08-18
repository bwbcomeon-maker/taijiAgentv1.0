#!/usr/bin/env python3
"""Read-only environment doctor for the Taiji Kylin packaging Skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


REPORT_SCHEMA = "taiji-kylin-packaging-doctor/v1"
INTERFACE_SCHEMA = "taiji-packaging-interface/v1"
INPUT_SCHEMA = "taiji-builder-input-package/v1"
REPOSITORY_ID = "taiji-agentv1.0"
INTERFACE = "packaging/linux/taiji-packaging-interface.json"
BUILDER_INPUT_ENTRY = "taijiagent 打包交付/99_本机_准备制包输入包.sh"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ARCHIVE_RE = re.compile(r"^taijiagent-制包机输入-([0-9a-f]{40})\.tar\.gz$")
MAX_CONTROL_BYTES = 2 * 1024 * 1024
GIT_ENVIRONMENT_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_NAMESPACE",
    "GIT_SHALLOW_FILE",
)
EXPECTED_INTERFACE = {
    "schema": INTERFACE_SCHEMA,
    "repository_id": REPOSITORY_ID,
    "orchestrator": {
        "path": "scripts/taiji-linux-golden-orchestrator.py",
        "plan_schema": "taiji-linux-golden-orchestrator-plan/v5",
    },
    "build_host_entry": "taijiagent 打包交付/00_制包机_生成离线交付包.sh",
    "builder_input_entry": BUILDER_INPUT_ENTRY,
    "canonical_runbook": "docs/runbooks/taiji-kylin-uos-offline-delivery.md",
}

MANIFEST_KEYS = frozenset(
    {
        "schema",
        "source_commit",
        "archive_basename",
        "archive_size",
        "archive_sha256",
        "source_archive_basename",
        "source_archive_sha256",
        "source_inventory_basename",
        "source_inventory_sha256",
        "source_integrity_helper_sha256",
        "builder_input_helper_sha256",
        "archive_root_basename",
        "manifest_basename",
        "checksum_basename",
        "members",
    }
)


class DoctorError(RuntimeError):
    """The supplied input could not be safely classified."""


class DoctorArgumentError(DoctorError):
    """CLI syntax was invalid; still emit the public JSON contract."""


def _strict_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise DoctorError("JSON contains a duplicate key: {}".format(key))
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    metadata = _regular_file(path, label, MAX_CONTROL_BYTES)
    try:
        with path.open("rb") as source:
            payload = source.read(metadata.st_size + 1)
    except OSError as exc:
        raise DoctorError("{} cannot be read".format(label)) from exc
    if len(payload) != metadata.st_size:
        raise DoctorError("{} changed while it was read".format(label))
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DoctorError("{} is not valid strict JSON".format(label)) from exc
    if type(value) is not dict:
        raise DoctorError("{} must be a JSON object".format(label))
    return value


def _regular_file(path: Path, label: str, maximum: Optional[int] = None) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DoctorError("{} is missing".format(label)) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or (maximum is not None and (metadata.st_size < 0 or metadata.st_size > maximum))
    ):
        raise DoctorError("{} must be a single-link regular file within limits".format(label))
    return metadata


def _directory(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise DoctorError("{} must be an absolute path".format(label))
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DoctorError("{} is missing".format(label)) from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise DoctorError("{} must be a real canonical directory".format(label))
    if resolved != path:
        raise DoctorError("{} must use its canonical absolute path".format(label))
    return path


def _relative_interface_path(root: Path, value: Any, label: str) -> Path:
    if type(value) is not str or not value or "\\" in value:
        raise DoctorError("{} is invalid".format(label))
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value:
        raise DoctorError("{} must be a canonical repository-relative path".format(label))
    target = root / relative
    _regular_file(target, label, MAX_CONTROL_BYTES)
    try:
        target.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise DoctorError("{} escapes the repository".format(label)) from exc
    if target.is_symlink():
        raise DoctorError("{} cannot be a symlink".format(label))
    return target


def _git_environment(root: Path) -> Dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        if key in GIT_ENVIRONMENT_KEYS or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            environment.pop(key, None)
    environment.update(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    return environment


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "core.quotePath=false",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                *arguments,
            ],
            env=_git_environment(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
    except OSError as exc:
        raise DoctorError("trusted /usr/bin/git is unavailable") from exc
    if result.returncode != 0:
        raise DoctorError("repository identity cannot be read with trusted Git")
    return result.stdout


def _report(
    mode: str,
    status: str,
    scope: Sequence[str],
    blockers: Sequence[Dict[str, str]],
    next_action: Optional[Dict[str, Any]],
    approval: Sequence[str],
    unverified: Sequence[str],
) -> Dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "mode": mode,
        "compatibility_status": status,
        "evidence_scope": list(scope),
        "blockers": list(blockers),
        "next_action": next_action,
        "approval_required": list(approval),
        "unverified": list(unverified),
    }


def inspect_repo(root: Path) -> Tuple[Dict[str, Any], int]:
    try:
        root = _directory(root, "repository")
        interface_path = root / "packaging/linux/taiji-packaging-interface.json"
        interface = _load_json(interface_path, "packaging interface")
        if interface != EXPECTED_INTERFACE:
            raise DoctorError("packaging interface does not match the supported v1 contract")
        _relative_interface_path(root, interface["orchestrator"]["path"], "orchestrator")
        _relative_interface_path(root, interface["build_host_entry"], "build-host entry")
        _relative_interface_path(root, interface["builder_input_entry"], "builder-input entry")
        _relative_interface_path(root, interface["canonical_runbook"], "canonical runbook")
        for relative in (
            INTERFACE,
            interface["orchestrator"]["path"],
            interface["build_host_entry"],
            interface["builder_input_entry"],
            interface["canonical_runbook"],
        ):
            tracked = _git(root, "ls-tree", "--name-only", "HEAD", "--", relative).strip()
            if tracked != relative:
                raise DoctorError("interface authority is not tracked in HEAD: " + relative)
        top = Path(_git(root, "rev-parse", "--show-toplevel").strip())
        if top.resolve(strict=True) != root.resolve(strict=True):
            raise DoctorError("operator-supplied path is not the Git repository top level")
        head = _git(root, "rev-parse", "HEAD").strip()
        if COMMIT_RE.fullmatch(head) is None:
            raise DoctorError("repository HEAD is not a full commit")
        branch = _git(root, "branch", "--show-current").strip()
        dirty = bool(
            _git(
                root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=all",
            )
        )
    except DoctorError as exc:
        return (
            _report(
                "repo",
                "unsupported",
                ["operator-supplied-repository-path"],
                [{"code": "REPO_UNSUPPORTED", "message": str(exc)}],
                None,
                [],
                ["repository-interface", "repository-state", "formal-source-gate"],
            ),
            2,
        )
    scope = [
        "operator-supplied-repository-path",
        "declarative-interface-v1",
        "git-head:{}".format(head),
        "git-branch:{}".format(branch or "detached"),
    ]
    if dirty:
        return (
            _report(
                "repo",
                "blocked",
                scope,
                [{"code": "REPO_DIRTY", "message": "repository has tracked or untracked changes"}],
                None,
                [],
                ["formal-source-gate", "builder-input", "candidate-deb", "target-acceptance", "publication"],
            ),
            2,
        )
    if branch != "main":
        return (
            _report(
                "repo",
                "blocked",
                scope,
                [{"code": "REPO_NOT_MAIN", "message": "repository must be on the formal main branch"}],
                None,
                [],
                ["formal-source-gate", "builder-input", "candidate-deb", "target-acceptance", "publication"],
            ),
            2,
        )
    next_action = {
        "action": "prepare-builder-input",
        "cwd": str(root),
        "argv": ["/bin/bash", "-p", BUILDER_INPUT_ENTRY],
    }
    return (
        _report(
            "repo",
            "pass",
            scope + ["git-worktree-clean"],
            [],
            next_action,
            ["prepare-builder-input"],
            ["formal-source-gate-not-run", "builder-input-not-created", "candidate-deb", "target-acceptance", "publication"],
        ),
        0,
    )


def _input_names(root: Path) -> Tuple[Path, Path, Path, str]:
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise DoctorError("input directory cannot be listed") from exc
    if len(entries) != 3:
        raise DoctorError("input directory must contain exactly the frozen input trio")
    by_name = {entry.name: entry for entry in entries}
    if len(by_name) != 3:
        raise DoctorError("input directory entries are ambiguous")
    archives = [name for name in by_name if ARCHIVE_RE.fullmatch(name)]
    if len(archives) != 1:
        raise DoctorError("input directory must contain one canonical builder archive")
    archive_name = archives[0]
    match = ARCHIVE_RE.fullmatch(archive_name)
    if match is None:
        raise DoctorError("builder archive basename is invalid")
    commit = match.group(1)
    manifest_name = "taijiagent-制包机输入-{}.manifest.json".format(commit)
    checksum_name = archive_name + ".sha256"
    if set(by_name) != {archive_name, manifest_name, checksum_name}:
        raise DoctorError("input directory basenames do not form one same-commit trio")
    archive, manifest, checksum = by_name[archive_name], by_name[manifest_name], by_name[checksum_name]
    _regular_file(archive, "builder input archive")
    _regular_file(manifest, "builder input manifest", MAX_CONTROL_BYTES)
    _regular_file(checksum, "builder input checksum", MAX_CONTROL_BYTES)
    return archive, manifest, checksum, commit


def inspect_input(root: Path) -> Tuple[Dict[str, Any], int]:
    try:
        root = _directory(root, "input directory")
        archive, manifest_path, checksum, commit = _input_names(root)
    except DoctorError as exc:
        return (
            _report(
                "input-dir",
                "blocked",
                ["operator-supplied-input-directory"],
                [{"code": "INPUT_SET_INVALID", "message": str(exc)}],
                None,
                [],
                ["formal-integrity-not-run", "candidate-deb", "target-acceptance", "publication"],
            ),
            2,
        )
    try:
        sidecar_lines = checksum.read_text(encoding="utf-8").splitlines()
        if len(sidecar_lines) != 2:
            raise DoctorError("builder input checksum must contain exactly archive and manifest entries")
        observed = []
        for line in sidecar_lines:
            match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\\r\n]+)", line)
            if match is None:
                raise DoctorError("builder input checksum contains a non-canonical entry")
            observed.append(match.group(2))
        if set(observed) != {archive.name, manifest_path.name} or len(set(observed)) != 2:
            raise DoctorError("builder input checksum basenames do not bind the frozen trio")
    except (OSError, UnicodeError, DoctorError) as exc:
        return (
            _report(
                "input-dir",
                "blocked",
                ["operator-supplied-input-directory", "exact-trio-basenames"],
                [{"code": "INPUT_CHECKSUM_INVALID", "message": str(exc)}],
                None,
                [],
                ["formal-integrity-not-run", "candidate-deb", "target-acceptance", "publication"],
            ),
            2,
        )
    try:
        manifest = _load_json(manifest_path, "builder input manifest")
        if set(manifest) != MANIFEST_KEYS:
            raise DoctorError("builder input manifest must contain the exact v1 field set")
        required = {
            "schema": INPUT_SCHEMA,
            "source_commit": commit,
            "archive_basename": archive.name,
            "manifest_basename": manifest_path.name,
            "checksum_basename": checksum.name,
        }
        for key, expected in required.items():
            if manifest.get(key) != expected:
                raise DoctorError("builder input manifest {} does not bind the trio".format(key))
        if type(manifest["archive_size"]) is not int or manifest["archive_size"] < 0:
            raise DoctorError("builder input manifest archive_size is invalid")
        for key in (
            "archive_sha256",
            "source_archive_sha256",
            "source_inventory_sha256",
            "source_integrity_helper_sha256",
            "builder_input_helper_sha256",
        ):
            if type(manifest[key]) is not str or SHA256_RE.fullmatch(manifest[key]) is None:
                raise DoctorError("builder input manifest {} is invalid".format(key))
        for key in (
            "source_archive_basename",
            "source_inventory_basename",
            "archive_root_basename",
        ):
            value = manifest[key]
            if type(value) is not str or not value or Path(value).name != value or "/" in value or "\\" in value:
                raise DoctorError("builder input manifest {} is invalid".format(key))
        if type(manifest["members"]) is not list:
            raise DoctorError("builder input manifest members is invalid")
    except DoctorError as exc:
        return (
            _report(
                "input-dir",
                "blocked",
                ["operator-supplied-input-directory", "exact-trio-basenames"],
                [{"code": "INPUT_MANIFEST_INVALID", "message": str(exc)}],
                None,
                [],
                ["formal-integrity-not-run", "candidate-deb", "target-acceptance", "publication"],
            ),
            2,
        )
    next_action = {
        "action": "verify-builder-input-sidecar",
        "cwd": str(root),
        "argv": ["sha256sum", "-c", "--", checksum.name],
    }
    return (
        _report(
            "input-dir",
            "pass",
            [
                "operator-supplied-input-directory",
                "exact-trio-basenames",
                "manifest-schema:{}".format(INPUT_SCHEMA),
                "source-commit:{}".format(commit),
            ],
            [],
            next_action,
            [],
            ["formal-integrity-not-run", "sidecar-content-not-run", "candidate-deb", "target-acceptance", "publication"],
        ),
        0,
    )


def selftest() -> Tuple[Dict[str, Any], int]:
    try:
        with tempfile.TemporaryDirectory(prefix="taiji-packaging-doctor-") as temporary:
            root = Path(temporary)
            if stat.S_IMODE(root.lstat().st_mode) & 0o077:
                raise DoctorError("selftest directory is not owner-only")
            payload = b"taiji-doctor-selftest"
            sample = root / "sample"
            sample.write_bytes(payload)
            if hashlib.sha256(sample.read_bytes()).hexdigest() != hashlib.sha256(payload).hexdigest():
                raise DoctorError("selftest digest mismatch")
            sample.unlink()
    except (DoctorError, OSError) as exc:
        return (
            _report(
                "selftest",
                "blocked",
                ["isolated-temporary-directory"],
                [{"code": "SELFTEST_FAILED", "message": str(exc)}],
                None,
                [],
                ["doctor-runtime"],
            ),
            2,
        )
    return (
        _report(
            "selftest",
            "pass",
            ["python-standard-library", "isolated-temporary-directory", "sha256-roundtrip"],
            [],
            None,
            [],
            ["repository-not-inspected", "builder-input-not-inspected", "candidate-deb", "target-acceptance", "publication"],
        ),
        0,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    class _Parser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise DoctorArgumentError(message)

    parser = _Parser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--repo", type=Path)
    modes.add_argument("--input-dir", type=Path)
    modes.add_argument("--selftest", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        if args.repo is not None:
            report, exit_code = inspect_repo(args.repo)
        elif args.input_dir is not None:
            report, exit_code = inspect_input(args.input_dir)
        else:
            report, exit_code = selftest()
    except DoctorArgumentError as exc:
        report = _report(
            "unknown",
            "unsupported",
            [],
            [{"code": "INVALID_ARGUMENTS", "message": str(exc)}],
            None,
            [],
            ["doctor-runtime"],
        )
        exit_code = 2
    except (DoctorError, OSError, ValueError) as exc:
        report = _report(
            "unknown",
            "blocked",
            [],
            [{"code": "DOCTOR_INTERNAL_ERROR", "message": str(exc)}],
            None,
            [],
            ["doctor-runtime"],
        )
        exit_code = 1
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
