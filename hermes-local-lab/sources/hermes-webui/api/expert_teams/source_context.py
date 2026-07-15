"""Build immutable, reproducible source context snapshots before model use."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


class SourceContextError(ValueError):
    pass


def _segments(source_id: str, text: str, size: int = 4000) -> list[dict]:
    rows = []
    for index, start in enumerate(range(0, len(text), size), start=1):
        content = text[start : start + size]
        rows.append(
            {
                "segment_id": f"{source_id}:SEG-{index:04d}",
                "source_id": source_id,
                "start_char": start,
                "end_char": start + len(content),
                "text": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return rows


def build_source_context_snapshot(
    workspace: Path,
    run_id: str,
    brief: dict,
    source_registry: dict,
    *,
    brief_sha256: str,
    brief_revision: int,
) -> dict:
    root = Path(workspace).expanduser().resolve()
    segments = []
    sources = []
    for ref in (brief.get("source_policy") or {}).get("source_refs") or []:
        source_id = str(ref.get("source_id") or "")
        entry = source_registry.get(source_id)
        if not isinstance(entry, dict) or entry.get("status") != "ready":
            raise SourceContextError(f"source is not ready: {source_id}")
        target = (root / str(entry.get("locator") or "")).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise SourceContextError(f"source escaped workspace: {source_id}") from exc
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.get("sha256"):
            raise SourceContextError(f"source hash changed: {source_id}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceContextError(f"source is not UTF-8 text: {source_id}") from exc
        if not text.strip():
            raise SourceContextError(f"source text is empty: {source_id}")
        source_segments = _segments(source_id, text)
        segments.extend(source_segments)
        sources.append(
            {
                "source_id": source_id,
                "kind": entry.get("kind"),
                "label": entry.get("label"),
                "sha256": digest,
                "segment_ids": [item["segment_id"] for item in source_segments],
            }
        )
    if not segments:
        raise SourceContextError("source snapshot has no segments")
    payload = {
        "schema_version": "source-context-snapshot/v1",
        "run_id": str(run_id),
        "brief_revision": int(brief_revision),
        "brief_sha256": str(brief_sha256),
        "sources": sources,
        "segments": segments,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()
    relative = Path(".taiji") / "expert-teams" / "source-context" / str(run_id) / f"{brief_sha256}.json"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if target.exists():
        if target.read_bytes() != rendered:
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
        "schema_version": payload["schema_version"],
        "path": relative.as_posix(),
        "sha256": payload["snapshot_sha256"],
        "brief_revision": int(brief_revision),
        "brief_sha256": str(brief_sha256),
    }
