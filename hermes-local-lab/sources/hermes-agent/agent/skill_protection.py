"""Policy helpers for protecting packaged Skill source from user export paths.

Protected skills remain fully readable by internal skill loaders.  This module
only helps public API, UI, backup/export, and generic file tools decide whether
they should withhold raw Skill files from user-facing channels.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_config_path, get_hermes_home


PROTECTED_CONTENT_CODE = "protected_skill_content_unavailable"
PROTECTED_WRITE_CODE = "protected_skill_write_blocked"
PROTECTED_DELETE_CODE = "protected_skill_delete_blocked"
PROTECTED_FILE_READ_CODE = "protected_skill_file_read_blocked"


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, (set, tuple)):
        value = list(value)
    elif not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        from agent.skill_utils import yaml_load

        parsed = yaml_load(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def get_skill_protection_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Return normalized ``skills.protection`` policy.

    Protection is off unless config or ``TAIJI_SKILL_PROTECTION`` enables it.
    Product packaging can turn it on in the shipped config; development tests
    and user-owned skill workflows remain open by default.
    """
    cfg_path = Path(config_path) if config_path else get_config_path()
    cfg = _load_yaml_mapping(cfg_path)
    skills_cfg = cfg.get("skills") if isinstance(cfg, dict) else {}
    protection = skills_cfg.get("protection") if isinstance(skills_cfg, dict) else {}
    if not isinstance(protection, dict):
        protection = {}

    env_enabled = os.environ.get("TAIJI_SKILL_PROTECTION")
    enabled_default = _truthy(env_enabled, default=False) if env_enabled is not None else False
    enabled = _truthy(protection.get("enabled"), default=enabled_default)
    audit_enabled = _truthy(protection.get("audit_enabled"), default=enabled)
    protected_ids = _as_list(protection.get("protected_ids"))

    return {
        "enabled": enabled,
        "audit_enabled": audit_enabled,
        "protected_ids": protected_ids,
        "config_path": str(cfg_path),
    }


def is_skill_protection_enabled(config_path: str | Path | None = None) -> bool:
    return bool(get_skill_protection_config(config_path).get("enabled"))


def _parse_skill_frontmatter(skill_md: Path | None, content: str | None = None) -> dict[str, Any]:
    try:
        raw = content if content is not None else skill_md.read_text(encoding="utf-8")[:4000]
        from agent.skill_utils import parse_frontmatter

        frontmatter, _body = parse_frontmatter(raw)
        return frontmatter if isinstance(frontmatter, dict) else {}
    except Exception:
        return {}


def _metadata_marks_protected(frontmatter: dict[str, Any]) -> bool:
    direct_keys = ("protection", "skill_protection", "taiji_protection")
    for key in direct_keys:
        if str(frontmatter.get(key, "")).strip().lower() == "protected":
            return True

    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return False
    nested_candidates = [
        metadata.get("protection"),
        metadata.get("skill_protection"),
    ]
    for namespace in ("taiji", "hermes"):
        value = metadata.get(namespace)
        if isinstance(value, dict):
            nested_candidates.append(value.get("protection"))
            nested_candidates.append(value.get("skill_protection"))
    return any(str(item or "").strip().lower() == "protected" for item in nested_candidates)


def _skill_relative_ids(skill_dir: Path | None) -> set[str]:
    if not skill_dir:
        return set()
    ids = {skill_dir.name}
    try:
        parts = skill_dir.parts
        if "skills" in parts:
            idx = len(parts) - 1 - list(reversed(parts)).index("skills")
            rel_parts = parts[idx + 1 :]
            if rel_parts:
                ids.add("/".join(rel_parts))
            if len(rel_parts) >= 2:
                ids.add("/".join(rel_parts[-2:]))
    except Exception:
        pass
    try:
        from agent.skill_utils import get_all_skills_dirs

        resolved = skill_dir.resolve()
        for root in get_all_skills_dirs():
            try:
                rel = resolved.relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            rel_text = rel.as_posix().strip("/")
            if rel_text:
                ids.add(rel_text)
                parts = rel.parts
                if len(parts) >= 2:
                    ids.add("/".join(parts[-2:]))
    except Exception:
        pass
    return ids


def _candidate_ids(
    *,
    name: str | None,
    skill_dir: Path | None,
    category: str | None,
    frontmatter: dict[str, Any],
) -> set[str]:
    ids: set[str] = set()
    for value in (name, frontmatter.get("name"), skill_dir.name if skill_dir else None):
        if value:
            ids.add(str(value).strip())
    ids.update(_skill_relative_ids(skill_dir))
    if category:
        for value in list(ids):
            if "/" not in value:
                ids.add(f"{category}/{value}")
    return {item.strip().strip("/") for item in ids if item and item.strip()}


def _matches_rule(candidate_ids: Iterable[str], rules: Iterable[str]) -> bool:
    normalized_ids = {item.lower() for item in candidate_ids}
    for raw_rule in rules:
        rule = str(raw_rule).strip().strip("/").lower()
        if not rule:
            continue
        if rule in {"*", "all"}:
            return True
        if any(fnmatch.fnmatch(candidate, rule) for candidate in normalized_ids):
            return True
    return False


def is_skill_protected(
    *,
    name: str | None = None,
    skill_md: str | Path | None = None,
    skill_dir: str | Path | None = None,
    category: str | None = None,
    frontmatter: dict[str, Any] | None = None,
    content: str | None = None,
    config_path: str | Path | None = None,
) -> bool:
    """Return True when the skill should be hidden from public source channels."""
    cfg = get_skill_protection_config(config_path)
    if not cfg.get("enabled"):
        return False

    md_path = Path(skill_md) if skill_md else None
    dir_path = Path(skill_dir) if skill_dir else (md_path.parent if md_path else None)
    fm = frontmatter if isinstance(frontmatter, dict) else _parse_skill_frontmatter(md_path, content)

    if _metadata_marks_protected(fm):
        return True

    ids = _candidate_ids(name=name, skill_dir=dir_path, category=category, frontmatter=fm)
    return _matches_rule(ids, cfg.get("protected_ids") or [])


def protected_skill_public_payload(
    *,
    name: str | None = None,
    description: str = "",
    category: str | None = None,
    code: str = PROTECTED_CONTENT_CODE,
    action: str = "view",
) -> dict[str, Any]:
    message = (
        "This protected Skill can be used by Taiji Agent, but its source, "
        "linked files, scripts, and templates are not available for viewing, "
        "editing, export, backup, or file search."
    )
    return {
        "success": False,
        "error": message,
        "code": code,
        "protected": True,
        "content_available": False,
        "name": name,
        "description": description,
        "category": category,
        "action": action,
    }


def protected_skill_read_error(path: str | Path) -> str:
    return (
        f"Cannot read '{path}': protected Skill source is available only to "
        "the internal Agent loader, not to user-facing file, search, export, "
        "backup, or terminal channels."
    )


def _find_containing_skill(path: str | Path) -> tuple[Path | None, Path | None]:
    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        try:
            resolved = Path(path).expanduser().resolve(strict=False)
        except Exception:
            return None, None

    start = resolved if resolved.is_dir() else resolved.parent
    for parent in (start, *start.parents):
        skill_md = parent / "SKILL.md"
        if skill_md.exists() and skill_md.is_file():
            return parent, skill_md
    return None, None


def is_path_protected_skill(
    path: str | Path,
    *,
    config_path: str | Path | None = None,
) -> bool:
    """Return True when *path* is inside a protected Skill directory."""
    skill_dir, skill_md = _find_containing_skill(path)
    if not skill_md:
        return False
    return is_skill_protected(skill_dir=skill_dir, skill_md=skill_md, config_path=config_path)


def _request_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _machine_hash() -> str:
    material = "|".join(
        [
            platform.node() or "",
            platform.machine() or "",
            os.environ.get("TAIJI_LICENSE_ID", ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8", errors="ignore")).hexdigest()[:16]


def audit_skill_protection_event(
    event_type: str,
    *,
    skill_id: str | None = None,
    skill_version: str | None = None,
    path: str | Path | None = None,
    session_id: str | None = None,
    hit_rule: str | None = None,
    request_summary: str | None = None,
    source: str | None = None,
    config_path: str | Path | None = None,
) -> None:
    """Append a best-effort audit record without storing Skill source text."""
    cfg = get_skill_protection_config(config_path)
    if not (cfg.get("enabled") and cfg.get("audit_enabled")):
        return
    try:
        logs_dir = get_hermes_home() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "license_id": os.environ.get("TAIJI_LICENSE_ID") or None,
            "machine_hash": _machine_hash(),
            "session_id": session_id,
            "skill_id": skill_id,
            "skill_version": skill_version,
            "hit_rule": hit_rule,
            "source": source,
            "request_hash": _request_hash(request_summary),
        }
        if path:
            record["path_hash"] = _request_hash(str(path))
        audit_path = logs_dir / "skill_protection_audit.jsonl"
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return


_BLOCKED_TERMINAL_WORDS = re.compile(
    r"\b(cat|less|more|head|tail|grep|rg|ripgrep|awk|sed|cp|mv|tar|zip|7z|unzip|rsync)\b"
)


def terminal_command_may_export_protected_skill(
    command: str,
    *,
    workdir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> bool:
    """Best-effort guard for terminal commands targeting protected Skill files."""
    if not command or not _BLOCKED_TERMINAL_WORDS.search(command):
        return False
    candidates = re.findall(r"(?:(?:~|\.)?/)?[A-Za-z0-9_./:@%+=,-]*skills/[A-Za-z0-9_./:@%+=,-]+", command)
    base = Path(workdir).expanduser() if workdir else Path.cwd()
    for raw in candidates:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        if is_path_protected_skill(candidate, config_path=config_path):
            return True
    return False
