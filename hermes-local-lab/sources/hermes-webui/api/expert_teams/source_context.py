"""Build and re-verify immutable source context snapshots before model use."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from api.expert_teams.contracts import brief_digest


SOURCE_CONTEXT_SCHEMA = "expert-source-context/v1"
DEFAULT_EXTRACTOR_IDENTITY = {
    "extractor_id": "taiji-utf8-text",
    "extractor_version": "1",
    "config_sha256": hashlib.sha256(b"utf8-text:size=4000:no-normalization").hexdigest(),
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SourceContextError(ValueError):
    pass


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _safe_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise SourceContextError(f"unsafe {label}")
    return text


def _segments(source_id: str, locator: str, text: str, size: int = 4000) -> list[dict]:
    rows = []
    for index, start in enumerate(range(0, len(text), size), start=1):
        content = text[start : start + size]
        rows.append(
            {
                "segment_id": f"{source_id}:SEG-{index:04d}",
                "char_start": start,
                "char_end": start + len(content),
                "locator": f"{locator}#chars={start}-{start + len(content)}",
                "text": content,
                "text_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return rows


def _snapshot_digest(payload: dict) -> str:
    return hashlib.sha256(_canonical_bytes({key: value for key, value in payload.items() if key != "snapshot_sha256"})).hexdigest()


def _snapshot_relative_path(run_id: str, snapshot_id: str) -> Path:
    return Path(".taiji") / "expert-teams" / "source-context" / run_id / f"{snapshot_id}.json"


def _path_contains_symlink(root: Path, target: Path) -> bool:
    try:
        relative = target.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def build_source_context_snapshot(
    workspace: Path,
    run_id: str,
    brief: dict,
    source_registry: dict,
    *,
    brief_sha256: str,
    brief_revision: int,
    extractor_identity: dict | None = None,
) -> dict:
    root = Path(workspace).expanduser().resolve()
    safe_run_id = _safe_id(run_id, "run id")
    identity = deepcopy(extractor_identity or DEFAULT_EXTRACTOR_IDENTITY)
    if set(identity) != {"extractor_id", "extractor_version", "config_sha256"}:
        raise SourceContextError("invalid extractor identity")
    _safe_id(identity.get("extractor_id"), "extractor id")
    _safe_id(identity.get("extractor_version"), "extractor version")
    if not re.fullmatch(r"[0-9a-f]{64}", str(identity.get("config_sha256") or "")):
        raise SourceContextError("invalid extractor identity")

    sources = []
    for ref in (brief.get("source_policy") or {}).get("source_refs") or []:
        source_id = _safe_id(ref.get("source_id"), "source id")
        entry = source_registry.get(source_id)
        if not isinstance(entry, dict) or entry.get("status") != "ready":
            raise SourceContextError(f"source is not ready: {source_id}")
        locator = str(entry.get("locator") or "").strip()
        unresolved = root / locator
        if unresolved.is_symlink():
            raise SourceContextError(f"source symlink is forbidden: {source_id}")
        target = unresolved.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise SourceContextError(f"source escaped workspace: {source_id}") from exc
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.get("sha256") or digest != ref.get("sha256"):
            raise SourceContextError(f"source hash changed: {source_id}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceContextError(f"source is not UTF-8 text: {source_id}") from exc
        if not text.strip():
            raise SourceContextError(f"source text is empty: {source_id}")
        sources.append(
            {
                "source_id": source_id,
                "kind": str(entry.get("kind") or ""),
                "label": str(entry.get("label") or ""),
                "locator": locator,
                "content_sha256": digest,
                "content_text": text,
                "segments": _segments(source_id, locator, text),
            }
        )
    if not sources:
        raise SourceContextError("source snapshot has no sources")

    snapshot_id = f"source-context-{int(brief_revision):04d}"
    payload = {
        "schema_version": SOURCE_CONTEXT_SCHEMA,
        "snapshot_id": snapshot_id,
        "run_id": safe_run_id,
        "brief_revision": int(brief_revision),
        "brief_sha256": str(brief_sha256),
        "extractor_identity": identity,
        "sources": sources,
    }
    payload["snapshot_sha256"] = _snapshot_digest(payload)
    relative = _snapshot_relative_path(safe_run_id, snapshot_id)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if _path_contains_symlink(root, target):
        raise SourceContextError("source snapshot storage path is unsafe")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if target.exists():
        if target.is_symlink() or target.read_bytes() != rendered:
            raise SourceContextError("immutable source snapshot conflicts with existing content")
    else:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".snapshot.", delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            temp_path = None
            target.chmod(0o400)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    return {
        "snapshot_id": snapshot_id,
        "sha256": payload["snapshot_sha256"],
        "relative_path": relative.as_posix(),
        "brief_revision": int(brief_revision),
        "brief_sha256": str(brief_sha256),
    }


def read_source_context_snapshot(workspace: Path, run_id: str, snapshot_ref: dict) -> dict:
    root = Path(workspace).expanduser().resolve()
    safe_run_id = _safe_id(run_id, "run id")
    snapshot_id = _safe_id(snapshot_ref.get("snapshot_id"), "snapshot id")
    expected_relative = _snapshot_relative_path(safe_run_id, snapshot_id).as_posix()
    if str(snapshot_ref.get("relative_path") or "") != expected_relative:
        raise SourceContextError("source snapshot path does not match its identity")
    target = root / expected_relative
    if _path_contains_symlink(root, target) or not target.is_file():
        raise SourceContextError("source snapshot is missing or unsafe")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceContextError("source snapshot is unreadable") from exc
    if not isinstance(payload, dict):
        raise SourceContextError("source snapshot payload is invalid")
    digest = _snapshot_digest(payload)
    if digest != payload.get("snapshot_sha256") or digest != snapshot_ref.get("sha256"):
        raise SourceContextError("source snapshot hash changed")
    if payload.get("schema_version") != SOURCE_CONTEXT_SCHEMA or payload.get("run_id") != safe_run_id or payload.get("snapshot_id") != snapshot_id:
        raise SourceContextError("source snapshot identity changed")
    return payload


def verify_source_context_snapshot(
    workspace: Path,
    run: dict,
    *,
    extractor_identity: dict | None = None,
) -> dict:
    ref = run.get("source_context_snapshot_ref")
    if not isinstance(ref, dict):
        raise SourceContextError("source snapshot reference is missing")
    payload = read_source_context_snapshot(workspace, str(run.get("run_id") or ""), ref)
    brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
    expected_identity = extractor_identity or DEFAULT_EXTRACTOR_IDENTITY
    checks = (
        payload.get("extractor_identity") == expected_identity,
        int(payload.get("brief_revision") or 0) == int(ref.get("brief_revision") or 0) == int(brief.get("confirmed_revision") or 0),
        str(payload.get("brief_sha256") or "") == str(ref.get("brief_sha256") or "") == str(brief.get("confirmed_sha256") or ""),
        str(brief.get("confirmed_sha256") or "") == brief_digest(brief),
    )
    if not all(checks):
        raise SourceContextError("source snapshot binding changed; create a new run")
    for source in payload.get("sources") or []:
        text = source.get("content_text")
        if not isinstance(text, str) or hashlib.sha256(text.encode("utf-8")).hexdigest() != source.get("content_sha256"):
            raise SourceContextError("source snapshot content hash changed")
        for segment in source.get("segments") or []:
            start, end = segment.get("char_start"), segment.get("char_end")
            value = segment.get("text")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(value, str) or text[start:end] != value:
                raise SourceContextError("source snapshot segment changed")
            if hashlib.sha256(value.encode("utf-8")).hexdigest() != segment.get("text_sha256"):
                raise SourceContextError("source snapshot segment hash changed")
    return payload
