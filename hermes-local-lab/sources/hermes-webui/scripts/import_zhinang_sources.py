#!/usr/bin/env python3
"""Import the fixed Agency Agents role corpus as checksum-bound product data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


UPSTREAM_REPOSITORY = "https://github.com/msitarzewski/agency-agents"
UPSTREAM_COMMIT = "af128a92888fd7d7c389b6cb37f1820be1b3cd9d"
CATALOG_VERSION = "agency-agents-af128a92888f-source-v1"
WEBUI_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = WEBUI_ROOT / "data" / "zhinang"
GIT_LOCATION_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


class ImportFailure(RuntimeError):
    pass


def _clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in GIT_LOCATION_VARIABLES:
        environment.pop(name, None)
    return environment


def _git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), *arguments],
        env=_clean_git_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ImportFailure(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout


def _read_regular(path: Path) -> bytes:
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise ImportFailure(f"required source is unavailable: {path.name}") from error
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size <= 0:
        raise ImportFailure(f"required source is not a non-empty regular file: {path.name}")
    return path.read_bytes()


def _resolve_upstream(root_input: str) -> Path:
    root = Path(root_input).expanduser().resolve()
    if not root.is_dir():
        raise ImportFailure("upstream root does not exist")
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if top != root:
        raise ImportFailure("upstream root does not match its Git top-level")
    commit = os.fsdecode(_git(root, "rev-parse", "HEAD")).strip()
    if commit != UPSTREAM_COMMIT:
        raise ImportFailure(
            f"upstream HEAD mismatch: expected {UPSTREAM_COMMIT}, received {commit}"
        )
    status = os.fsdecode(_git(root, "status", "--short")).strip()
    if status:
        raise ImportFailure("upstream worktree must be clean")
    return root


def _divisions(upstream_root: Path) -> tuple[bytes, tuple[str, ...]]:
    payload = _read_regular(upstream_root / "divisions.json")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImportFailure("upstream divisions.json is invalid") from error
    divisions = document.get("divisions") if isinstance(document, dict) else None
    if not isinstance(divisions, dict) or not divisions:
        raise ImportFailure("upstream divisions.json has no divisions")
    names = tuple(sorted(divisions))
    if any(not isinstance(name, str) or not name for name in names):
        raise ImportFailure("upstream division name is invalid")
    return payload, names


def _tracked_roles(upstream_root: Path, divisions: tuple[str, ...]) -> tuple[str, ...]:
    output = _git(upstream_root, "ls-files", "-z", "--", *divisions)
    roles: list[str] = []
    for item in output.split(b"\0"):
        if not item:
            continue
        source_path = os.fsdecode(item)
        relative = PurePosixPath(source_path)
        if relative.suffix == ".md" and relative.parts[0] in divisions:
            roles.append(relative.as_posix())
    if not roles:
        raise ImportFailure("fixed upstream commit contains no role Markdown")
    return tuple(sorted(roles))


def _source_record(upstream_root: Path, source_path: str) -> tuple[dict, bytes]:
    relative = PurePosixPath(source_path)
    payload = _read_regular(upstream_root.joinpath(*relative.parts))
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ImportFailure(f"role source is not UTF-8: {source_path}") from error
    if "\r" in text or not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ImportFailure(f"role source has invalid frontmatter: {source_path}")
    return (
        {
            "role_id": f"agency:{source_path.removesuffix('.md')}",
            "division": relative.parts[0],
            "source_path": source_path,
            "source_bytes": len(payload),
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
        payload,
    )


def build_import(upstream_root: Path, output_root: Path) -> dict:
    if output_root.exists():
        raise ImportFailure("output root already exists; refusing to overwrite it")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    divisions_payload, divisions = _divisions(upstream_root)
    license_payload = _read_regular(upstream_root / "LICENSE")
    role_paths = _tracked_roles(upstream_root, divisions)
    stage = Path(
        tempfile.mkdtemp(prefix=".zhinang-import-", dir=os.fspath(output_root.parent))
    )
    try:
        records = []
        source_root = stage / "upstream" / "agency-agents"
        for source_path in role_paths:
            record, payload = _source_record(upstream_root, source_path)
            target = source_root.joinpath(*PurePosixPath(source_path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            records.append(record)
        (stage / "LICENSE.agency-agents").write_bytes(license_payload)
        (stage / "divisions.json").write_bytes(divisions_payload)
        manifest = {
            "schema_version": 1,
            "catalog_version": CATALOG_VERSION,
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_commit": UPSTREAM_COMMIT,
            "source_root": "upstream/agency-agents",
            "license_path": "LICENSE.agency-agents",
            "license_bytes": len(license_payload),
            "license_sha256": hashlib.sha256(license_payload).hexdigest(),
            "divisions_path": "divisions.json",
            "divisions_bytes": len(divisions_payload),
            "divisions_sha256": hashlib.sha256(divisions_payload).hexdigest(),
            "role_count": len(records),
            "roles": records,
        }
        (stage / "source-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(stage, output_root)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return manifest


def check_import(upstream_root: Path, output_root: Path) -> dict:
    if not output_root.is_dir():
        raise ImportFailure("output root does not exist")
    try:
        sys.path.insert(0, os.fspath(WEBUI_ROOT))
        from api.zhinang import ZhinangSourceCatalog
    except ImportError as error:
        raise ImportFailure("could not import the product catalog validator") from error

    snapshot = ZhinangSourceCatalog(output_root).validate()
    divisions_payload, divisions = _divisions(upstream_root)
    license_payload = _read_regular(upstream_root / "LICENSE")
    if _read_regular(output_root / "divisions.json") != divisions_payload:
        raise ImportFailure("imported divisions.json does not match fixed upstream")
    if _read_regular(output_root / "LICENSE.agency-agents") != license_payload:
        raise ImportFailure("imported license does not match fixed upstream")
    role_paths = _tracked_roles(upstream_root, divisions)
    if snapshot.role_count != len(role_paths):
        raise ImportFailure("imported role count does not match fixed upstream")
    for source_path in role_paths:
        role_id = f"agency:{source_path.removesuffix('.md')}"
        source_role = snapshot.roles.get(role_id)
        if source_role is None:
            raise ImportFailure("imported manifest is missing a fixed upstream role")
        upstream_payload = _read_regular(
            upstream_root.joinpath(*PurePosixPath(source_path).parts)
        )
        if source_role.source_sha256 != hashlib.sha256(upstream_payload).hexdigest():
            raise ImportFailure("imported role digest does not match fixed upstream")
    return {
        "catalog_version": snapshot.catalog_version,
        "upstream_commit": snapshot.upstream_commit,
        "role_count": snapshot.role_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-root", required=True)
    parser.add_argument("--output-root", default=os.fspath(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        upstream_root = _resolve_upstream(arguments.upstream_root)
        output_root = Path(arguments.output_root).expanduser().resolve()
        result = (
            check_import(upstream_root, output_root)
            if arguments.check
            else build_import(upstream_root, output_root)
        )
    except ImportFailure as error:
        print(f"zhinang source import: FAIL: {error}")
        return 1
    print(
        "zhinang source import: PASS "
        f"commit={result['upstream_commit']} roles={result['role_count']} "
        f"catalog={result['catalog_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
