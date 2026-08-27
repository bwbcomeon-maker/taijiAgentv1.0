#!/usr/bin/env python3
"""Fail closed on unsafe material in the current local Git change set."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_CHANGE_ENTRIES = 1024
GIT_LOCATOR_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

OUTPUT_BASENAMES = {".DS_Store", ".coverage"}
OUTPUT_COMPONENTS = {"__pycache__", "coverage"}
OUTPUT_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".log",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".zip",
    ".7z",
    ".rar",
    ".exe",
    ".msi",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".appimage",
)
PRIVATE_KEY_PATTERN = re.compile(
    rb"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----"
)
CREDENTIAL_NAME_EXPRESSION = (
    r"[A-Z0-9_.-]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|"
    r"CLIENT[_-]?SECRET|ACCESS[_-]?KEY)[A-Z0-9_.-]*"
)
CREDENTIAL_NAME_PATTERN = re.compile(
    rf"^{CREDENTIAL_NAME_EXPRESSION}$", re.IGNORECASE
)
ASSIGNMENT_PATTERN = re.compile(
    rf"(?im)(?:^|[{{,])[ \t]*(?:export[ \t]+)?"
    rf"(?P<name_quote>['\"]?)(?P<name>{CREDENTIAL_NAME_EXPRESSION})(?P=name_quote)"
    r"[ \t]*[:=][ \t]*['\"]?"
    r"(?P<value>[^\s'\",;#]+)"
)
HIGH_CONFIDENCE_VALUES = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)
PLACEHOLDER_PATTERNS = (
    re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}"),
    re.compile(r"\{\{[^{}\r\n]+\}\}"),
    re.compile(r"<[A-Za-z0-9_.-]*placeholder[A-Za-z0-9_.-]*>", re.IGNORECASE),
    re.compile(r"self\.[A-Za-z_][A-Za-z0-9_.]*(?:\(\))?", re.IGNORECASE),
    re.compile(r"(?:TEST_ONLY|TEST-ONLY)[A-Za-z0-9_.-]*", re.IGNORECASE),
    re.compile(r"(?:FAKE|FIXTURE|EXAMPLE)[A-Za-z0-9_.-]*", re.IGNORECASE),
)


class GitQueryError(RuntimeError):
    pass


def _git_env() -> dict[str, str]:
    environment = os.environ.copy()
    for name in GIT_LOCATOR_ENV:
        environment.pop(name, None)
    return environment


def _git_bytes(*arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            env=_git_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise GitQueryError("Git change-set query could not start") from exc
    if completed.returncode != 0:
        raise GitQueryError("Git change-set query failed")
    return completed.stdout


def _git_paths(*arguments: str) -> list[str]:
    return [
        item.decode("utf-8", "surrogateescape")
        for item in _git_bytes(*arguments).split(b"\0")
        if item
    ]


def _index_entries() -> tuple[tuple[str, str, str, int], ...]:
    entries: list[tuple[str, str, str, int]] = []
    for record in _git_bytes("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split()
            path = encoded_path.decode("utf-8", "surrogateescape")
            entries.append((path, mode, oid, int(stage)))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitQueryError("Git index manifest was malformed") from exc
    return tuple(sorted(entries))


def _tree_entries(treeish: str) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for record in _git_bytes("ls-tree", "-r", "-z", treeish).split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            if kind != "blob":
                continue
            path = encoded_path.decode("utf-8", "surrogateescape")
            entries[path] = (mode, oid)
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitQueryError("Git tree manifest was malformed") from exc
    return entries


def _git_invisible_special_paths() -> list[str]:
    """Find non-ignored FIFOs/sockets/devices that Git omits from untracked output."""
    ignored = set(
        _git_paths(
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "-z",
        )
    )
    ignored_prefixes = tuple(item for item in ignored if item.endswith("/"))

    def is_ignored(relative: str) -> bool:
        return relative in ignored or any(relative.startswith(prefix) for prefix in ignored_prefixes)

    special: list[str] = []
    pending = [ROOT]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise GitQueryError("working-tree type scan failed") from exc
        for entry in entries:
            absolute = Path(entry.path)
            relative = absolute.relative_to(ROOT).as_posix()
            if relative == ".git" or relative.startswith(".git/") or is_ignored(relative):
                continue
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise GitQueryError("working-tree type scan failed") from exc
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(absolute)
            elif not stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                special.append(relative)
    return special


def _is_valid_relative_path(path: str) -> bool:
    candidate = PurePosixPath(path.replace("\\", "/"))
    return bool(path) and not candidate.is_absolute() and ".." not in candidate.parts


def _stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_mode,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _worktree_signature(path: str) -> tuple[object, ...]:
    if not _is_valid_relative_path(path):
        return ("invalid",)
    absolute = ROOT / path
    try:
        metadata = absolute.lstat()
    except OSError:
        return ("missing",)
    signature: tuple[object, ...] = ("entry", *_stat_signature(metadata))
    if stat.S_ISLNK(metadata.st_mode):
        try:
            return (*signature, os.readlink(absolute))
        except OSError:
            return ("raced",)
    return signature


def _capture_change_set() -> dict[str, tuple[object, ...]]:
    staged_paths = tuple(
        sorted(set(_git_paths("diff", "--no-renames", "--cached", "--name-only", "--diff-filter=ACMRT", "-z")))
    )
    staged_deleted = tuple(
        sorted(set(_git_paths("diff", "--no-renames", "--cached", "--name-only", "--diff-filter=D", "-z")))
    )
    unstaged_paths = tuple(
        sorted(set(_git_paths("diff", "--no-renames", "--name-only", "--diff-filter=ACMRT", "-z")))
    )
    unstaged_deleted = tuple(
        sorted(set(_git_paths("diff", "--no-renames", "--name-only", "--diff-filter=D", "-z")))
    )
    untracked_paths = tuple(sorted(set(_git_paths("ls-files", "--others", "--exclude-standard", "-z"))))
    special_paths = tuple(sorted(set(_git_invisible_special_paths())))
    worktree_paths = tuple(sorted(set(unstaged_paths + untracked_paths + special_paths)))
    worktree_manifest = tuple((path, _worktree_signature(path)) for path in worktree_paths)
    return {
        "staged_paths": staged_paths,
        "staged_deleted": staged_deleted,
        "unstaged_paths": unstaged_paths,
        "unstaged_deleted": unstaged_deleted,
        "untracked_paths": untracked_paths,
        "special_paths": special_paths,
        "worktree_paths": worktree_paths,
        "worktree_manifest": worktree_manifest,
        "index_entries": _index_entries(),
        "head_oid": (_git_bytes("rev-parse", "--verify", "HEAD").strip().decode("ascii"),),
    }


def _output_finding(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    lowered = normalized.lower()
    if pure.name in OUTPUT_BASENAMES:
        return "transient-output"
    if any(component.lower() in OUTPUT_COMPONENTS for component in pure.parts):
        return "transient-output"
    if pure.name.lower() == "coverage.json":
        return "transient-output"
    for suffix in OUTPUT_SUFFIXES:
        if lowered.endswith(suffix):
            if suffix in (".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage"):
                return "package-output"
            return "transient-output"
    return None


def _placeholder(value: str) -> bool:
    if not value or value.lower() in {"none", "null", "unset"}:
        return True
    return any(pattern.fullmatch(value) for pattern in PLACEHOLDER_PATTERNS)


def _assignment_target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        parents = _assignment_target_names(target.value)
        return [f"{parent}.{target.attr}" for parent in parents]
    if isinstance(target, (ast.List, ast.Tuple)):
        return [
            name
            for element in target.elts
            for name in _assignment_target_names(element)
        ]
    if isinstance(target, ast.Starred):
        return _assignment_target_names(target.value)
    return []


def _credential_value_is_sensitive(value: str | bytes) -> bool:
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
        length = len(value)
    else:
        text = value
        length = len(value)
    if _placeholder(text):
        return False
    return length >= 16 or any(
        pattern.fullmatch(text) for pattern in HIGH_CONFIDENCE_VALUES
    )


def _python_sensitive_literals(value: ast.expr) -> list[str | bytes]:
    if isinstance(value, ast.Constant) and isinstance(value.value, (str, bytes)):
        return [value.value] if _credential_value_is_sensitive(value.value) else []
    if isinstance(value, ast.JoinedStr):
        return [
            part.value
            for part in value.values
            if isinstance(part, ast.Constant)
            and isinstance(part.value, (str, bytes))
            and _credential_value_is_sensitive(part.value)
        ]
    return []


def _credential_fingerprint(name: str, value: str | bytes) -> str:
    encoded = value if isinstance(value, bytes) else value.encode("utf-8", "surrogatepass")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{name.casefold()}:{digest}"


def _python_assignment_findings(
    path: str, text: str
) -> tuple[set[tuple[int, str]], Counter[str], bool]:
    if PurePosixPath(path).suffix.lower() != ".py":
        return set(), Counter(), False
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError):
        return set(), Counter(), True

    handled: set[tuple[int, str]] = set()
    sensitive: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            for name in _assignment_target_names(target):
                if not CREDENTIAL_NAME_PATTERN.fullmatch(name):
                    continue
                handled.add((node.lineno, name.casefold()))
                for literal in _python_sensitive_literals(value):
                    sensitive[_credential_fingerprint(name, literal)] += 1
    return handled, sensitive, False


def _credential_assignment_fingerprints(path: str, text: str) -> tuple[Counter[str], bool]:
    handled, fingerprints, parse_failed = _python_assignment_findings(path, text)
    for match in ASSIGNMENT_PATTERN.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        name = match.group("name")
        if (line, name.casefold()) in handled:
            continue
        value = match.group("value")
        if _credential_value_is_sensitive(value):
            fingerprints[_credential_fingerprint(name, value)] += 1
    return fingerprints, parse_failed


def _content_findings(path: str, content: bytes, baseline: bytes = b"") -> list[str]:
    findings: list[str] = []
    if PRIVATE_KEY_PATTERN.search(content):
        findings.append("private-key")
    text = content.decode("utf-8", "replace")
    if any(pattern.search(text) for pattern in HIGH_CONFIDENCE_VALUES):
        findings.append("high-confidence-token")
    credential_fingerprints, parse_failed = _credential_assignment_fingerprints(path, text)
    if parse_failed:
        findings.append("python-parse-error")
    baseline_fingerprints, _ = _credential_assignment_fingerprints(
        path, baseline.decode("utf-8", "replace")
    )
    if credential_fingerprints - baseline_fingerprints:
        findings.append("credential-assignment")
    return findings


def _read_index_blob(oid: str) -> tuple[int, bytes]:
    try:
        size = int(_git_bytes("cat-file", "-s", oid).strip())
    except ValueError as exc:
        raise GitQueryError("Git blob size was malformed") from exc
    if size > MAX_FILE_BYTES:
        return size, b""
    content = _git_bytes("cat-file", "blob", oid)
    if len(content) != size:
        raise GitQueryError("Git blob changed while reading")
    return size, content


def _baseline_content(entry: tuple[str, str] | None) -> bytes:
    if entry is None or entry[0] not in {"100644", "100755"}:
        return b""
    size, content = _read_index_blob(entry[1])
    return content if size <= MAX_FILE_BYTES else b""


def _read_worktree_file(
    path: str, expected: tuple[object, ...]
) -> tuple[bytes | None, str | None]:
    if not expected or expected[0] != "entry":
        return None, "change-set-raced"
    expected_stat = expected[1:6]
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    try:
        descriptor = os.open(ROOT / path, flags)
        before = os.fstat(descriptor)
        if _stat_signature(before) != expected_stat or not stat.S_ISREG(before.st_mode):
            return None, "change-set-raced"
        content = bytearray()
        while len(content) < MAX_FILE_BYTES + 1:
            chunk = os.read(descriptor, min(64 * 1024, MAX_FILE_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        if _stat_signature(after) != expected_stat:
            return None, "change-set-raced"
        if len(content) > MAX_FILE_BYTES:
            return None, "file-size-limit"
        return bytes(content), None
    except OSError:
        return None, "change-set-raced"
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _scan_capture(capture: dict[str, tuple[object, ...]]) -> list[tuple[str, str]]:
    staged_paths = tuple(str(path) for path in capture["staged_paths"])
    worktree_paths = tuple(str(path) for path in capture["worktree_paths"])
    index_entries = tuple(capture["index_entries"])
    changed_entries = set(staged_paths) | set(worktree_paths)
    changed_entries.update(str(path) for path in capture["staged_deleted"])
    changed_entries.update(str(path) for path in capture["unstaged_deleted"])
    unmerged_paths = {str(entry[0]) for entry in index_entries if int(entry[3]) != 0}
    changed_entries.update(unmerged_paths)
    if len(changed_entries) > MAX_CHANGE_ENTRIES:
        return [("<change-set>", "change-set-entry-limit")]

    findings: list[tuple[str, str]] = []
    for path in sorted(unmerged_paths):
        findings.append((path, "unmerged-index-entry"))

    stage_zero = {
        str(path): (str(mode), str(oid))
        for path, mode, oid, stage in index_entries
        if int(stage) == 0
    }
    head_oid = str(tuple(capture["head_oid"])[0])
    head_entries = _tree_entries(head_oid)
    contents: list[tuple[str, bytes, bytes]] = []
    total = 0

    for path in staged_paths:
        if not _is_valid_relative_path(path):
            findings.append((path, "invalid-path"))
            continue
        entry = stage_zero.get(path)
        if entry is None:
            findings.append((path, "missing-index-entry"))
            continue
        mode, oid = entry
        if mode not in {"100644", "100755"}:
            findings.append((path, "unsupported-index-mode"))
            continue
        output_kind = _output_finding(path)
        if output_kind:
            findings.append((path, output_kind))
        size, content = _read_index_blob(oid)
        if size > MAX_FILE_BYTES:
            findings.append((path, "file-size-limit"))
            continue
        total += size
        contents.append((path, content, _baseline_content(head_entries.get(path))))

    worktree_manifest = dict(capture["worktree_manifest"])
    for path in worktree_paths:
        if not _is_valid_relative_path(path):
            findings.append((path, "invalid-path"))
            continue
        signature = tuple(worktree_manifest[path])
        if not signature or signature[0] != "entry":
            findings.append((path, "change-set-raced"))
            continue
        mode = int(signature[1])
        if not stat.S_ISREG(mode):
            findings.append((path, "non-regular-file"))
            continue
        output_kind = _output_finding(path)
        if output_kind:
            findings.append((path, output_kind))
        size = int(signature[4])
        if size > MAX_FILE_BYTES:
            findings.append((path, "file-size-limit"))
            continue
        content, error = _read_worktree_file(path, signature)
        if error:
            findings.append((path, error))
            continue
        assert content is not None
        total += len(content)
        contents.append((path, content, _baseline_content(stage_zero.get(path))))

    if total > MAX_TOTAL_BYTES:
        findings.append(("<change-set>", "total-size-limit"))
        return findings

    for path, content, baseline in contents:
        for finding in _content_findings(path, content, baseline):
            findings.append((path, finding))
    return findings


def scan() -> list[tuple[str, str]]:
    initial = _capture_change_set()
    findings = _scan_capture(initial)
    final = _capture_change_set()
    if initial != final:
        findings.append(("<change-set>", "change-set-raced"))
    return sorted(set(findings))


def main() -> int:
    try:
        findings = scan()
    except GitQueryError:
        findings = [("<git-change-set>", "git-query-error")]
    if findings:
        for path, finding in findings:
            print(f"finding: {path}: {finding}")
        print(f"local change safety: FAIL ({len(findings)} findings)")
        return 1
    print("local change safety: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
