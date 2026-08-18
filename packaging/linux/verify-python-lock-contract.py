#!/usr/bin/env python3
"""Validate the packaged WebUI Python dependency subset and installed venv."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set


EXACT_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)
IMPORT_NAMES = {
    "cryptography": "cryptography",
    "pypdf": "pypdf",
    "pyyaml": "yaml",
}


class ContractError(ValueError):
    pass


def normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def parse_exact_requirements(path: Path, label: str) -> Dict[str, str]:
    result = {}  # type: Dict[str, str]
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = EXACT_REQUIREMENT_RE.fullmatch(line)
        if match is None:
            raise ContractError(
                f"{label} line {line_number} must be one exact name==version requirement"
            )
        name = normalize_name(match.group(1))
        if name in result:
            raise ContractError(f"{label} contains duplicate requirement: {name}")
        result[name] = match.group(2)
    if not result:
        raise ContractError(f"{label} must contain at least one exact requirement")
    return result


def direct_dependencies(pyproject: Path) -> Dict[str, str]:
    lines = pyproject.read_text(encoding="utf-8").splitlines()
    in_project = False
    in_dependencies = False
    dependencies = []  # type: List[str]
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            in_dependencies = False
            continue
        if not in_project:
            continue
        if not in_dependencies:
            inline = re.fullmatch(r"dependencies\s*=\s*\[(.*)\]", line)
            if inline is not None:
                dependencies.extend(re.findall(r'"([^"\\]+)"', inline.group(1)))
                break
            if re.fullmatch(r"dependencies\s*=\s*\[", line):
                in_dependencies = True
            continue
        if line == "]":
            in_dependencies = False
            break
        match = re.match(r'^"([^"\\]+)"\s*,?', line)
        if match is None:
            if not line or line.startswith("#"):
                continue
            raise ContractError("pyproject dependencies contains an unsupported TOML entry")
        dependencies.append(match.group(1))
    if in_dependencies or not dependencies:
        raise ContractError("pyproject project.dependencies list is missing or incomplete")
    result = {}  # type: Dict[str, str]
    for dependency in dependencies:
        match = EXACT_REQUIREMENT_RE.fullmatch(dependency)
        if match is None:
            continue
        name = normalize_name(match.group(1))
        if name in result and result[name] != match.group(2):
            raise ContractError(f"pyproject has conflicting direct dependency: {name}")
        result[name] = match.group(2)
    return result


def locked_packages(lock: Path) -> Dict[str, Set[str]]:
    result = {}  # type: Dict[str, Set[str]]
    current_name = None  # type: Optional[str]
    current_version = None  # type: Optional[str]

    def finish_package() -> None:
        if current_name is not None and current_version is not None:
            result.setdefault(normalize_name(current_name), set()).add(current_version)

    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "[[package]]":
            finish_package()
            current_name = None
            current_version = None
            continue
        match = re.fullmatch(r'name\s*=\s*"([^"]+)"', line)
        if match is not None and current_name is None:
            current_name = match.group(1)
            continue
        match = re.fullmatch(r'version\s*=\s*"([^"]+)"', line)
        if match is not None and current_version is None:
            current_version = match.group(1)
    finish_package()
    if not result:
        raise ContractError("uv.lock package tables are missing")
    return result


def validate_subset(pyproject: Path, lock: Path, requirements: Path) -> Dict[str, str]:
    requested = parse_exact_requirements(requirements, "WebUI requirements")
    direct = direct_dependencies(pyproject)
    locked = locked_packages(lock)
    for name, version in requested.items():
        if direct.get(name) != version:
            raise ContractError(
                f"WebUI requirement {name}=={version} is not the same exact Agent direct dependency"
            )
        if locked.get(name) != {version}:
            raise ContractError(
                f"WebUI requirement {name}=={version} is not uniquely fixed by uv.lock"
            )
        if name not in IMPORT_NAMES:
            raise ContractError(f"WebUI requirement has no import verification mapping: {name}")
    return requested


def verify_installed(python: Path, requested: Dict[str, str]) -> None:
    if not python.is_file():
        raise ContractError(f"installed Python must resolve to a regular file: {python}")
    code = """
import importlib
import importlib.metadata
import json
import sys

requirements = json.loads(sys.argv[1])
imports = json.loads(sys.argv[2])
installed = {}
for name, expected in requirements.items():
    actual = importlib.metadata.version(name)
    if actual != expected:
        raise SystemExit(f"installed version mismatch for {name}: {actual} != {expected}")
    importlib.import_module(imports[name])
    installed[name] = actual
print(json.dumps(installed, sort_keys=True))
"""
    result = subprocess.run(
        [
            str(python),
            "-c",
            code,
            json.dumps(requested, sort_keys=True),
            json.dumps({name: IMPORT_NAMES[name] for name in requested}, sort_keys=True),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown import failure"
        raise ContractError(f"installed WebUI dependency verification failed: {detail}")
    try:
        installed = json.loads(result.stdout)
    except ValueError as exc:
        raise ContractError("installed verification returned invalid JSON") from exc
    if installed != requested:
        raise ContractError("installed dependency identity differs from the lock subset")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--verify-installed", action="store_true")
    parser.add_argument("--python", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        requested = validate_subset(args.pyproject, args.lock, args.requirements)
        if args.verify_installed:
            if args.python is None:
                raise ContractError("--verify-installed requires --python")
            verify_installed(args.python, requested)
    except (ContractError, OSError, UnicodeError) as exc:
        print(f"python-lock-contract-failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "PASS", "requirements": requested}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
