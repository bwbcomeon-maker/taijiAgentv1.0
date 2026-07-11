#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


NODE_VERSION = "22.23.1"
NODE_ARCHIVE_SHA256 = "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
PUBLIC_KEY_FINGERPRINT = "2dcff4f2b5e6f7a5e7e3f730e2f4446ad3265964431f614de7550265f7628b35"
ALLOWLIST_SCHEMA = "taiji-product-skills-allowlist/v1"
MANIFEST_SCHEMA = "taiji-product-skills/v1"
SKILL_PRODUCTIZATION = "skill-md-visible-branding-v1"
PRUNED_NAMES = {
    ".cache",
    ".coverage",
    ".ds_store",
    ".git",
    ".github",
    ".npm",
    ".nyc_output",
    ".pytest_cache",
    "__tests__",
    "__pycache__",
    "coverage",
    "docs",
    "test",
    "tests",
}
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".mjs", ".txt", ".yaml", ".yml"}
FORBIDDEN_SKILL_TEXT = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "/Users/",
    "/home/",
)


class StageError(RuntimeError):
    pass


def safe_relative(value: object, *, label: str) -> PurePosixPath:
    text = str(value or "")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StageError(f"{label} is not a safe relative path: {text}")
    return path


def inside(root: Path, candidate: Path, *, label: str) -> Path:
    root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    if os.path.commonpath((str(root), str(resolved))) != str(root):
        raise StageError(f"{label} escapes its declared root: {candidate}")
    return resolved


def source_path(repo_root: Path, relative: object, *, label: str) -> Path:
    rel = safe_relative(relative, label=label)
    current = repo_root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise StageError(f"{label} contains a symlinked path component: {rel.as_posix()}")
    if not current.exists():
        raise StageError(f"{label} is missing: {rel.as_posix()}")
    return inside(repo_root, current, label=label)


def assert_safe_symlinks(root: Path, *, label: str) -> None:
    resolved_root = root.resolve(strict=True)
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = Path(os.readlink(path))
        if target.is_absolute():
            raise StageError(f"{label} contains an absolute symlink: {path}")
        resolved = (path.parent / target).resolve(strict=False)
        if os.path.commonpath((str(resolved_root), str(resolved))) != str(resolved_root):
            raise StageError(f"{label} contains an escaping symlink: {path}")


def ignored_names(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lower = name.lower()
        if (
            lower in PRUNED_NAMES
            or name.startswith("._")
            or lower.endswith(".backup")
            or lower.endswith(".pyc")
            or re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", lower) is not None
            or (lower.endswith(".py") and (lower.startswith("test_") or lower.endswith("_test.py")))
        ):
            ignored.add(name)
    return ignored


def copy_filtered(source: Path, destination: Path) -> None:
    assert_safe_symlinks(source, label=str(source))
    shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True, ignore=ignored_names)


def remove_declared_excludes(root: Path, excludes: Iterable[object], *, skill_id: str) -> None:
    for raw in excludes:
        rel = safe_relative(raw, label=f"skill {skill_id} exclude")
        target = root
        for part in rel.parts:
            target = target / part
            if target.is_symlink():
                raise StageError(
                    f"skill {skill_id} exclude contains a symlinked path component: {rel.as_posix()}"
                )
        if target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)


def _frontmatter_end(lines: list[str], *, skill_path: Path) -> int:
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise StageError(f"product skill has no valid YAML frontmatter: {skill_path}")
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return index
    raise StageError(f"product skill has unterminated YAML frontmatter: {skill_path}")


def _without_internal_frontmatter(frontmatter: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        top_level = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*?))?\s*(?:\r?\n)?$", line)
        if top_level and top_level.group(1) in {"author", "version"}:
            if "hermes" in (top_level.group(2) or "").lower():
                index += 1
                continue
        if top_level and top_level.group(1) == "metadata":
            block_end = index + 1
            while block_end < len(frontmatter):
                candidate = frontmatter[block_end]
                if candidate.strip() and not candidate[0].isspace():
                    break
                block_end += 1
            filtered: list[str] = []
            block_index = index + 1
            while block_index < block_end:
                candidate = frontmatter[block_index]
                nested = re.match(r"^(\s+)([A-Za-z0-9_-]+):", candidate)
                if nested and len(nested.group(1).replace("\t", "  ")) == 2 and nested.group(2).lower() == "hermes":
                    nested_indent = len(nested.group(1).replace("\t", "  "))
                    block_index += 1
                    while block_index < block_end:
                        descendant = frontmatter[block_index]
                        if descendant.strip():
                            indentation = len(descendant) - len(descendant.lstrip(" \t"))
                            if indentation <= nested_indent:
                                break
                        block_index += 1
                    continue
                filtered.append(candidate)
                block_index += 1
            if any(item.strip() for item in filtered):
                result.append(line)
                result.extend(filtered)
            index = block_end
            continue
        result.append(line)
        index += 1
    return result


def productize_skill_markdown(skill_path: Path) -> dict[str, str]:
    try:
        source_text = skill_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise StageError(f"product skill SKILL.md is not UTF-8: {skill_path}") from exc
    source_lines = source_text.splitlines(keepends=True)
    end = _frontmatter_end(source_lines, skill_path=skill_path)
    source_frontmatter = "".join(source_lines[1:end])
    source_repository = re.search(r"(?m)^\s+source_repo:\s*(\S+)\s*$", source_frontmatter)
    source_revision = re.search(r"(?m)^\s+source_commit:\s*([0-9a-fA-F]+)\s*$", source_frontmatter)

    frontmatter = _without_internal_frontmatter(source_lines[1:end])
    product_text = "".join((source_lines[0], *frontmatter, source_lines[end], *source_lines[end + 1 :]))
    replacements = (
        (r"\bHERMES_", "TAIJI_"),
        (r"\bHERMES\b", "TAIJI"),
        (r"\bHermes\b", "Taiji"),
        (r"\bhermes\b", "taiji"),
    )
    for pattern, replacement in replacements:
        product_text = re.sub(pattern, replacement, product_text)
    if re.search(r"(?i)hermes", product_text):
        raise StageError(f"productized SKILL.md still contains internal brand text: {skill_path}")
    product_lines = product_text.splitlines(keepends=True)
    _frontmatter_end(product_lines, skill_path=skill_path)
    skill_path.write_text(product_text, encoding="utf-8")

    provenance: dict[str, str] = {}
    if source_repository and re.search(r"(?i)hermes", source_repository.group(1)) is None:
        provenance["source_repository"] = source_repository.group(1)
    if source_revision:
        provenance["source_revision"] = source_revision.group(1).lower()
    return provenance


def scan_skill_runtime_text(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in FORBIDDEN_SKILL_TEXT:
            if token in text:
                raise StageError(f"product skill contains forbidden runtime token {token}: {path.relative_to(root)}")


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8") + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
        elif path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
    return digest.hexdigest()


def validate_node_root(node_root: Path) -> Path:
    root = node_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise StageError(f"verified Node root is not a directory: {root}")
    assert_safe_symlinks(root, label="verified Node root")
    version = (root / ".taiji-node-version").read_text(encoding="utf-8").strip()
    archive_sha = (root / ".taiji-node-archive-sha256").read_text(encoding="utf-8").strip().lower()
    if version != NODE_VERSION:
        raise StageError(f"verified Node version must be {NODE_VERSION}, got {version}")
    if archive_sha != NODE_ARCHIVE_SHA256:
        raise StageError(f"verified Node archive SHA256 mismatch: {archive_sha}")
    node = root / "bin/node"
    if node.is_symlink() or not node.is_file():
        raise StageError("verified Node root is missing regular bin/node")
    header = node.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
        raise StageError("verified Node bin/node is not a 64-bit little-endian ELF")
    if int.from_bytes(header[18:20], "little") != 62:
        raise StageError("verified Node bin/node is not Linux x86_64")
    if stat.S_IMODE(node.stat().st_mode) & 0o111 == 0:
        raise StageError("verified Node bin/node is not executable")
    return root


def validate_docx_source(repo_root: Path) -> tuple[Path, dict[str, Any]]:
    source = source_path(
        repo_root,
        "hermes-local-lab/sources/docx-engine-v2",
        label="DOCX Engine source",
    )
    for relative in ("src", "node_modules", "templates", "package.json", "package-lock.json", "template-registry.json"):
        if not (source / relative).exists():
            raise StageError(f"DOCX Engine production input is missing: {relative}")
    registry = json.loads((source / "template-registry.json").read_text(encoding="utf-8"))
    if registry.get("version") != 1 or registry.get("installed") != []:
        raise StageError("DOCX Engine template registry must be version 1 with an empty installed array")
    builtin_ids = {item.get("templateId") for item in registry.get("builtin", []) if isinstance(item, dict)}
    if builtin_ids != {"general-proposal", "meeting-minutes"}:
        raise StageError(f"DOCX Engine builtin template seed mismatch: {sorted(builtin_ids)}")
    return source, registry


def load_allowlist(repo_root: Path) -> tuple[Path, list[dict[str, Any]]]:
    path = source_path(
        repo_root,
        "packaging/linux/product-skills.allowlist.json",
        label="product Skills allowlist",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != ALLOWLIST_SCHEMA or not isinstance(payload.get("skills"), list):
        raise StageError(f"product Skills allowlist must use {ALLOWLIST_SCHEMA}")
    skills: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in payload["skills"]:
        if not isinstance(item, dict):
            raise StageError("product Skills allowlist entry must be an object")
        skill_id = str(item.get("id") or "")
        category = str(item.get("category") or "")
        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill_id) is None:
            raise StageError(f"invalid product skill id: {skill_id}")
        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", category) is None:
            raise StageError(f"invalid product skill category: {category}")
        if (category, skill_id) in seen:
            raise StageError(f"duplicate product skill destination: {category}/{skill_id}")
        seen.add((category, skill_id))
        source = source_path(repo_root, item.get("source"), label=f"product skill {skill_id}")
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            raise StageError(f"product skill {skill_id} has no SKILL.md")
        skills.append({**item, "_source": source})
    return path, skills


def public_key_fingerprint(public_key: Path) -> str:
    try:
        completed = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", str(public_key), "-outform", "DER"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise StageError("public key fingerprint validation requires openssl") from exc
    if completed.returncode != 0:
        raise StageError("public key fingerprint validation failed: invalid PEM public key")
    return hashlib.sha256(completed.stdout).hexdigest()


def validate_public_key(repo_root: Path, expected_fingerprint: str) -> Path:
    public_key = source_path(
        repo_root,
        "tools/taiji-license-issuer/private/signing-public.pem",
        label="issuer signing public key",
    )
    actual = public_key_fingerprint(public_key)
    if actual != expected_fingerprint.lower():
        raise StageError(f"public key fingerprint mismatch: expected {expected_fingerprint}, got {actual}")
    if "PRIVATE KEY" in public_key.read_text(encoding="utf-8"):
        raise StageError("issuer signing public key file contains private key material")
    return public_key


def make_template_seed_read_only(engine_root: Path) -> None:
    registry = engine_root / "template-registry.json"
    registry.chmod(0o444)
    templates = engine_root / "templates"
    for path in sorted(templates.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() else 0o444)
    templates.chmod(0o555)


def stage_components(repo_root: Path, install_root: Path, node_root: Path, expected_fingerprint: str) -> dict[str, Any]:
    repo_root = repo_root.expanduser().resolve(strict=True)
    verified_node = validate_node_root(node_root)
    docx_source, _registry = validate_docx_source(repo_root)
    _allowlist_path, skills = load_allowlist(repo_root)
    public_key = validate_public_key(repo_root, expected_fingerprint)

    install_root.mkdir(parents=True, exist_ok=True)
    node_destination = install_root / "runtime/node"
    shutil.rmtree(node_destination, ignore_errors=True)
    (node_destination / "bin").mkdir(parents=True)
    shutil.copy2(verified_node / "bin/node", node_destination / "bin/node")
    shutil.copy2(verified_node / "LICENSE", node_destination / "LICENSE")
    (node_destination / "NODE_VERSION").write_text(NODE_VERSION + "\n", encoding="utf-8")
    (node_destination / "NODE_ARCHIVE_SHA256").write_text(
        NODE_ARCHIVE_SHA256 + "\n",
        encoding="utf-8",
    )
    (node_destination / "bin/node").chmod(0o755)
    (node_destination / "bin").chmod(0o755)
    (node_destination / "LICENSE").chmod(0o644)
    (node_destination / "NODE_VERSION").chmod(0o644)
    (node_destination / "NODE_ARCHIVE_SHA256").chmod(0o644)
    node_destination.chmod(0o755)

    engine_destination = install_root / "runtime/docx-engine-v2"
    shutil.rmtree(engine_destination, ignore_errors=True)
    engine_destination.mkdir(parents=True)
    for directory in ("src", "node_modules", "templates"):
        copy_filtered(docx_source / directory, engine_destination / directory)
    for filename in ("package.json", "package-lock.json", "template-registry.json"):
        shutil.copy2(docx_source / filename, engine_destination / filename)
    engine_destination.chmod(0o755)
    make_template_seed_read_only(engine_destination)

    skills_root = install_root / "runtime/agent/skills"
    shutil.rmtree(skills_root, ignore_errors=True)
    skills_root.mkdir(parents=True)
    manifest_entries: list[dict[str, str]] = []
    for item in skills:
        skill_id = str(item["id"])
        category = str(item["category"])
        destination = skills_root / category / skill_id
        source_sha256 = tree_sha256(item["_source"])
        copy_filtered(item["_source"], destination)
        remove_declared_excludes(destination, item.get("exclude", []), skill_id=skill_id)
        provenance = productize_skill_markdown(destination / "SKILL.md")
        scan_skill_runtime_text(destination)
        manifest_entries.append(
            {
                "id": skill_id,
                "path": f"{category}/{skill_id}",
                "source_sha256": source_sha256,
                "productization": SKILL_PRODUCTIZATION,
                "sha256": tree_sha256(destination),
                **provenance,
            }
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "skills": manifest_entries,
    }
    manifest_path = skills_root / "product-skills.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.chmod(0o644)
    skills_root.chmod(0o755)

    public_destination = install_root / "resources/license/signing-public.pem"
    public_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(public_key, public_destination)
    public_destination.chmod(0o644)
    public_destination.parent.chmod(0o755)
    public_destination.parent.parent.chmod(0o755)
    install_root.chmod(0o755)

    return {
        "ok": True,
        "node_version": NODE_VERSION,
        "docx_engine_version": json.loads((engine_destination / "package.json").read_text(encoding="utf-8"))["version"],
        "product_skills": [item["id"] for item in manifest_entries],
        "public_key_fingerprint": expected_fingerprint.lower(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage immutable Taiji Linux runtime components")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--node-root", required=True, help="verified offline Node 22.23.1 root")
    parser.add_argument("--public-key-fingerprint", default=PUBLIC_KEY_FINGERPRINT)
    args = parser.parse_args()
    try:
        result = stage_components(
            Path(args.repo_root),
            Path(args.install_root),
            Path(args.node_root),
            str(args.public_key_fingerprint),
        )
    except (OSError, ValueError, json.JSONDecodeError, StageError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
