#!/usr/bin/env python3
"""Classify a Git change into the smallest safe CI scope.

Unknown paths deliberately fall back to the root suite. Security, release,
dependency and workflow changes deliberately fan out to every automated suite.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable


SUITES = ("root", "desktop", "docx", "agent", "webui")
LINUX_PACKAGING_PREFIXES = (
    "packaging/linux/",
    "scripts/taiji-",
    "scripts/produce-taiji-",
    "scripts/assemble-taiji-",
    "scripts/sign-taiji-",
    "scripts/validate-taiji-",
    "taijiagent 打包交付/",
)
MODULE_PREFIXES = {
    "desktop": ("apps/taiji-desktop/",),
    "docx": ("hermes-local-lab/sources/docx-engine-v2/",),
    "agent": ("hermes-local-lab/sources/hermes-agent/",),
    "webui": ("hermes-local-lab/sources/hermes-webui/",),
}
HIGH_RISK_PREFIXES = (
    ".github/",
    "packaging/",
    "scripts/",
    "taijiagent 打包交付/",
    "tools/taiji-license-issuer/",
)
HIGH_RISK_ROOT_FILES = {".gitignore", "AGENTS.md", "VERSION"}
HIGH_RISK_GOVERNANCE_FILES = {
    "docs/runbooks/development-lifecycle.md",
    "docs/runbooks/solo-development-workflow.md",
}
HIGH_RISK_FILE_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}
HIGH_RISK_TERMS = (
    "auth",
    "credential",
    "license",
    "migration",
    "oauth",
    "provider",
    "release",
    "security",
)
GIT_LOCATOR_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


class LocalChangeError(RuntimeError):
    pass


def _normalise(path: str) -> str:
    value = path.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return str(PurePosixPath(value))


def _is_docs_only_path(path: str) -> bool:
    return path == "README.md" or path.startswith("docs/")


def _is_high_risk(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(path).name.lower()
    return (
        path in HIGH_RISK_ROOT_FILES
        or path in HIGH_RISK_GOVERNANCE_FILES
        or path.startswith(HIGH_RISK_PREFIXES)
        or name in HIGH_RISK_FILE_NAMES
        or name.startswith("requirements") and name.endswith(".txt")
        or any(term in lowered for term in HIGH_RISK_TERMS)
    )


def _is_linux_packaging(path: str) -> bool:
    return path.startswith(LINUX_PACKAGING_PREFIXES)


def classify_paths(paths: Iterable[str], labels: Iterable[str] = ()) -> dict[str, object]:
    changed = sorted({_normalise(path) for path in paths if path.strip()})
    label_set = {
        label.strip().lower()
        for value in labels
        for label in value.split(",")
        if label.strip()
    }
    result: dict[str, object] = {
        "risk": "normal",
        "docs_only": False,
        "run_linux_packaging": False,
        **{f"run_{suite}": False for suite in SUITES},
    }

    high_risk_paths = [path for path in changed if _is_high_risk(path)]
    if "full-ci" in label_set or high_risk_paths:
        result["risk"] = "high"
        result["run_linux_packaging"] = bool(
            "full-ci" in label_set
            or any(_is_linux_packaging(path) for path in high_risk_paths)
        )
        for suite in SUITES:
            result[f"run_{suite}"] = True
        result["reason"] = "full-ci label" if "full-ci" in label_set else "high-risk path"
        return result

    if changed and all(_is_docs_only_path(path) for path in changed):
        result["risk"] = "docs"
        result["docs_only"] = True
        result["reason"] = "documentation only"
        return result

    # Every code/configuration change keeps the monorepo contract tests in scope.
    result["run_root"] = True
    for suite, prefixes in MODULE_PREFIXES.items():
        result[f"run_{suite}"] = any(path.startswith(prefixes) for path in changed)
    result["reason"] = "affected modules" if changed else "empty diff fallback"
    return result


def _git_env() -> dict[str, str]:
    environment = os.environ.copy()
    for name in GIT_LOCATOR_ENV:
        environment.pop(name, None)
    return environment


def _git_nul(repo: Path, *arguments: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            env=_git_env(),
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise LocalChangeError("Git query could not start") from exc
    if completed.returncode != 0:
        raise LocalChangeError("Git query failed")
    return [
        item.decode("utf-8", "surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def changed_paths(base: str, head: str, repo: Path) -> list[str]:
    return _git_nul(
        repo,
        "diff",
        "--no-renames",
        "--name-only",
        "-z",
        f"{base}...{head}",
    )


def local_changed_paths(repo: Path) -> list[str]:
    collected: list[str] = []
    for arguments in (
        ("diff", "--no-renames", "--cached", "--name-only", "-z"),
        ("diff", "--no-renames", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        collected.extend(_git_nul(repo, *arguments))
    unmerged = _git_nul(repo, "ls-files", "-u", "-z")
    if unmerged:
        raise LocalChangeError("unmerged index entries")
    return sorted(set(collected))


def write_github_output(path: Path, result: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for key, value in result.items():
            if key == "reason":
                continue
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            stream.write(f"{key}={rendered}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--local-changes", action="store_true")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    paths = args.path
    try:
        if args.local_changes:
            if paths or args.base or args.head:
                parser.error("--local-changes cannot be combined with --path/--base/--head")
            paths = local_changed_paths(repo)
        elif not paths:
            if not args.base or not args.head:
                parser.error("provide --local-changes, --path, or both --base and --head")
            paths = changed_paths(args.base, args.head, repo)
    except LocalChangeError as exc:
        print(f"local change classification failed: {exc}", file=sys.stderr)
        return 2

    result = classify_paths(paths, args.label)
    if args.github_output:
        write_github_output(args.github_output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
