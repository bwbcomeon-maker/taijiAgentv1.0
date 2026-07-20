#!/usr/bin/env python3
"""Reject incomplete nested-source imports before old Git metadata is removed."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath


GIT_LOCATION_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


class GateError(RuntimeError):
    pass


def clean_git_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in GIT_LOCATION_VARIABLES:
        env.pop(name, None)
    return env


def run_git(
    arguments: list[str],
    *,
    env: dict[str, str],
    input_data: bytes | None = None,
) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        env=env,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout


def resolve_repo(repo_root_input: str, *, env: dict[str, str]) -> Path:
    repo_root = Path(repo_root_input).expanduser().resolve()
    if not repo_root.is_dir():
        raise GateError(f"repository root does not exist: {repo_root}")
    top = Path(
        os.fsdecode(
            run_git(
                ["-C", os.fspath(repo_root), "rev-parse", "--show-toplevel"],
                env=env,
            )
        ).strip()
    ).resolve()
    if top != repo_root:
        raise GateError(
            f"repository root does not match Git top-level: repo={repo_root} git={top}"
        )
    return repo_root


def resolve_source_prefix(repo_root: Path, source_prefix_input: str) -> tuple[str, Path]:
    prefix = PurePosixPath(source_prefix_input)
    if (
        prefix.is_absolute()
        or not prefix.parts
        or any(part in ("", ".", "..") for part in prefix.parts)
    ):
        raise GateError(
            f"source prefix must be a safe relative path: {source_prefix_input}"
        )
    normalized = prefix.as_posix()
    source_root = (repo_root / Path(*prefix.parts)).resolve()
    try:
        source_root.relative_to(repo_root)
    except ValueError as error:
        raise GateError(
            f"source prefix must stay inside repository: {source_prefix_input}"
        ) from error
    if not source_root.is_dir():
        raise GateError(f"source tree does not exist: {source_root}")
    return normalized, source_root


def source_entries(
    source_git_dir: Path,
    source_commit: str,
    *,
    env: dict[str, str],
) -> tuple[str, dict[str, tuple[str, str]]]:
    if not source_git_dir.is_dir():
        raise GateError(f"source Git directory does not exist: {source_git_dir}")
    git_dir = os.fspath(source_git_dir)
    resolved_commit = os.fsdecode(
        run_git(
            [
                f"--git-dir={git_dir}",
                "rev-parse",
                "--verify",
                f"{source_commit}^{{commit}}",
            ],
            env=env,
        )
    ).strip()
    raw_tree = run_git(
        [
            f"--git-dir={git_dir}",
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            resolved_commit,
        ],
        env=env,
    )
    entries: dict[str, tuple[str, str]] = {}
    for record in raw_tree.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            raw_mode, object_type, raw_oid = header.split(b" ", 2)
        except ValueError as error:
            raise GateError("could not parse source Git tree") from error
        relative_path = os.fsdecode(raw_path)
        if object_type == b"commit":
            raise GateError(
                "source tree contains a gitlink/submodule instead of directly "
                f"imported source: {relative_path}"
            )
        if object_type != b"blob":
            continue
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in ("", ".", "..") for part in relative.parts)
        ):
            raise GateError(f"source tree contains an unsafe path: {relative_path}")
        entries[relative_path] = (
            raw_mode.decode("ascii"),
            raw_oid.decode("ascii"),
        )
    if not entries:
        raise GateError(f"source commit contains no importable entries: {resolved_commit}")
    return resolved_commit, entries


def parent_index(
    repo_root: Path,
    source_prefix: str,
    *,
    env: dict[str, str],
) -> dict[str, tuple[str, str]]:
    raw_index = run_git(
        [
            "-C",
            os.fspath(repo_root),
            "ls-files",
            "-s",
            "-z",
            "--",
            source_prefix,
        ],
        env=env,
    )
    entries: dict[str, tuple[str, str]] = {}
    for record in raw_index.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            raw_mode, raw_oid, raw_stage = header.split(b" ", 2)
        except ValueError as error:
            raise GateError("could not parse parent Git index") from error
        if raw_stage != b"0":
            raise GateError(
                f"parent index contains an unresolved stage: {os.fsdecode(raw_path)}"
            )
        entries[os.fsdecode(raw_path)] = (
            raw_mode.decode("ascii"),
            raw_oid.decode("ascii"),
        )
    return entries


def physical_blob_oid(
    source_git_dir: Path,
    physical_path: Path,
    *,
    env: dict[str, str],
) -> str:
    git_dir_argument = f"--git-dir={os.fspath(source_git_dir)}"
    if physical_path.is_symlink():
        return os.fsdecode(
            run_git(
                [
                    git_dir_argument,
                    "hash-object",
                    "--stdin",
                ],
                env=env,
                input_data=os.fsencode(os.readlink(physical_path)),
            )
        ).strip()
    return os.fsdecode(
        run_git(
            [
                git_dir_argument,
                "hash-object",
                "--no-filters",
                "--",
                os.fspath(physical_path),
            ],
            env=env,
        )
    ).strip()


def physical_mode(path: Path) -> str:
    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode):
        return "120000"
    if stat.S_ISREG(file_stat.st_mode):
        return "100755" if file_stat.st_mode & stat.S_IXUSR else "100644"
    if stat.S_ISDIR(file_stat.st_mode):
        return "160000"
    return "unsupported"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that every path tracked by a nested source commit is present "
            "on disk and tracked under the parent monorepo prefix."
        )
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--source-git-dir", required=True)
    parser.add_argument("--source-commit", default="HEAD")
    parser.add_argument(
        "--require-content-match",
        action="store_true",
        help=(
            "also require physical file blobs and executable modes to match the "
            "nested source commit; use this immediately after an import"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = clean_git_environment()
    repo_root = resolve_repo(args.repo_root, env=env)
    source_prefix, source_root = resolve_source_prefix(
        repo_root,
        args.source_prefix,
    )
    source_git_dir = Path(args.source_git_dir).expanduser().resolve()
    resolved_commit, nested_entries = source_entries(
        source_git_dir,
        args.source_commit,
        env=env,
    )
    tracked_entries = parent_index(repo_root, source_prefix, env=env)

    missing_on_disk: list[str] = []
    untracked_in_parent: list[str] = []
    content_mismatch: list[str] = []
    mode_mismatch: list[str] = []

    for relative_path, (nested_mode, nested_oid) in sorted(nested_entries.items()):
        parent_path = f"{source_prefix}/{relative_path}"
        physical_path = source_root / Path(*PurePosixPath(relative_path).parts)
        if not os.path.lexists(physical_path):
            missing_on_disk.append(parent_path)
        if parent_path not in tracked_entries:
            untracked_in_parent.append(parent_path)
        if args.require_content_match and os.path.lexists(physical_path):
            parent_entry = tracked_entries.get(parent_path)
            current_mode = physical_mode(physical_path)
            parent_mode = parent_entry[0] if parent_entry else ""
            if current_mode != nested_mode or (
                parent_entry is not None and parent_mode != nested_mode
            ):
                mode_mismatch.append(parent_path)
            if current_mode not in ("100644", "100755", "120000"):
                content_mismatch.append(parent_path)
                continue
            physical_oid = physical_blob_oid(
                source_git_dir,
                physical_path,
                env=env,
            )
            parent_content_matches = True
            if parent_entry is not None:
                source_blob = run_git(
                    [
                        f"--git-dir={os.fspath(source_git_dir)}",
                        "cat-file",
                        "blob",
                        nested_oid,
                    ],
                    env=env,
                )
                parent_blob = run_git(
                    [
                        "-C",
                        os.fspath(repo_root),
                        "cat-file",
                        "blob",
                        parent_entry[1],
                    ],
                    env=env,
                )
                parent_content_matches = parent_blob == source_blob
            if (
                physical_oid != nested_oid
                or not parent_content_matches
            ):
                content_mismatch.append(parent_path)

    print(f"repo: {repo_root}")
    print(f"source_prefix: {source_prefix}")
    print(f"source_git_dir: {source_git_dir}")
    print(f"source_commit: {resolved_commit}")
    print(f"source_entries: {len(nested_entries)}")
    print(f"tracked_in_parent: {len(nested_entries) - len(untracked_in_parent)}")
    print(f"missing_on_disk: {len(missing_on_disk)}")
    print(f"untracked_in_parent: {len(untracked_in_parent)}")
    print(f"content_mismatch: {len(content_mismatch)}")
    print(f"mode_mismatch: {len(mode_mismatch)}")

    failures = (
        ("missing_on_disk", missing_on_disk),
        ("untracked_in_parent", untracked_in_parent),
        ("content_mismatch", content_mismatch),
        ("mode_mismatch", mode_mismatch),
    )
    if any(paths for _, paths in failures):
        print(
            "[FAIL] imported source tree is incomplete or does not match its "
            "declared source commit",
            file=sys.stderr,
        )
        for category, paths in failures:
            for path in paths:
                print(f"{category}: {path}", file=sys.stderr)
        return 1

    print("imported source tree gate passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        raise SystemExit(2)
