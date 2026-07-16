"""Portable, public-only WebUI session bundles.

The ZIP is a transport container, never an authority.  Every member is read
under strict limits, message fields are projected through the public contract,
and imported images are validated and registered again under a fresh session.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from api.artifacts import (
    ArtifactRegistry,
    PUBLIC_ARTIFACT_FIELDS,
    public_artifact_projection,
    validate_image_bytes,
)
from api.brand_privacy import (
    public_message_projection,
    scrub_public_export_payload,
)


class BundleValidationError(ValueError):
    pass


class BundleImportRollbackError(RuntimeError):
    """An unpublished import was quarantined after cleanup could not prove itself."""

    code = "rollback_incomplete"
    rollback_incomplete = True

    def __init__(self, session_id: str, failed_stages: list[str]):
        super().__init__("bundle import rollback was incomplete")
        self.session_id = session_id
        self.failed_stages = tuple(sorted(set(failed_stages)))


@dataclass(frozen=True)
class BundleLimits:
    max_files: int = 1_000
    max_total_uncompressed: int = 100 * 1024 * 1024
    max_single_file: int = 25 * 1024 * 1024
    max_compression_ratio: float = 200.0


@dataclass(frozen=True)
class InspectedBundle:
    session: dict
    manifest: dict
    artifact_bytes: dict[str, bytes]


_SESSION_FIELDS = {
    "export_schema_version", "session_id", "title", "model", "model_provider",
    "created_at", "updated_at", "pinned", "archived", "project_id", "profile",
    "personality", "messages", "tool_calls",
}
_MESSAGE_FIELDS = {
    "role", "content", "timestamp", "_ts", "type", "message_id", "id",
    "name", "duration_seconds", "_error", "error_type", "is_error",
    "reasoning", "reasoning_content", "provider_details",
    "provider_details_label", "preview", "snippet", "text", "tool_calls",
    "artifacts", "artifact_errors", "event_type", "tool_call_id", "tool_use_id",
    "status", "summary", "done", "tid", "assistant_msg_idx", "duration",
}
_PUBLIC_TOOL_FIELDS = {
    "event_type", "name", "status", "duration", "summary", "is_error",
    "tid", "assistant_msg_idx", "done",
}
_MANIFEST_FIELDS = {
    "bundle_schema_version", "original_session_id", "session_sha256",
    "messages_sha256", "artifacts",
}
_MANIFEST_ARTIFACT_FIELDS = {
    "artifact_id", "kind", "mime", "name", "size", "sha256", "status",
    "path", "source_turn_id", "source_tool_call_id",
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _artifact_extension(mime: str) -> str:
    if mime == "image/png":
        return "png"
    if mime == "image/jpeg":
        return "jpg"
    raise BundleValidationError("unsupported artifact image MIME")


def _canonical_public_message(message: Any, *, session_id: str) -> dict:
    """Return the one portable message shape accepted by this bundle schema."""
    projected = public_message_projection(message, session_id=session_id)
    return {
        key: copy.deepcopy(projected[key])
        for key in _MESSAGE_FIELDS
        if key in projected
    }


def _validate_public_message_types(message: dict) -> None:
    role = message.get("role")
    if role is not None and (
        not isinstance(role, str)
        or role.strip().lower() not in {"user", "assistant", "tool"}
    ):
        raise BundleValidationError(
            "session message does not match canonical public projection"
        )
    content = message.get("content")
    if content is not None and not isinstance(content, (str, list)):
        raise BundleValidationError(
            "session message does not match canonical public projection"
        )
    if isinstance(content, list) and any(
        not isinstance(item, (str, dict)) for item in content
    ):
        raise BundleValidationError(
            "session message does not match canonical public projection"
        )
    for key in (
        "reasoning", "reasoning_content", "provider_details",
        "provider_details_label", "preview", "snippet", "text", "summary",
    ):
        if key in message and not isinstance(message.get(key), str):
            raise BundleValidationError(
                "session message does not match canonical public projection"
            )
    if "artifact_errors" in message and (
        not isinstance(message.get("artifact_errors"), list)
        or any(not isinstance(item, str) for item in message["artifact_errors"])
    ):
        raise BundleValidationError(
            "session message does not match canonical public projection"
        )
    for key in ("tool_calls", "artifacts"):
        if key in message and (
            not isinstance(message.get(key), list)
            or any(not isinstance(item, dict) for item in message[key])
        ):
            raise BundleValidationError(
                "session message does not match canonical public projection"
            )


def _canonical_public_tool_event(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    projected = public_message_projection({
        "role": "assistant", "tool_calls": [value]
    }).get("tool_calls") or []
    if not projected or not isinstance(projected[0], dict):
        return {}
    return {
        key: copy.deepcopy(projected[0][key])
        for key in _PUBLIC_TOOL_FIELDS
        if key in projected[0]
    }


def _bundle_messages(session: Any) -> tuple[dict, list[dict]]:
    source = dict(getattr(session, "__dict__", {}) or {})
    safe = scrub_public_export_payload(source)
    safe["export_schema_version"] = 2
    original_messages = [item for item in source.get("messages") or [] if isinstance(item, dict)]
    safe["messages"] = [
        _canonical_public_message(
            original, session_id=str(source.get("session_id") or "")
        )
        for original in original_messages
    ]
    safe["tool_calls"] = [
        projected for projected in (
            _canonical_public_tool_event(item)
            for item in source.get("tool_calls") or []
        ) if projected
    ]
    return safe, original_messages


def build_session_bundle(
    session: Any,
    registry: ArtifactRegistry,
    *,
    limits: BundleLimits | None = None,
) -> bytes:
    """Return an in-memory ZIP whose files all come from verified descriptors."""
    limits = limits or BundleLimits()
    session_id = str(getattr(session, "session_id", "") or "").strip()
    if not session_id:
        raise BundleValidationError("session_id is required")
    safe_session, _original_messages = _bundle_messages(session)
    session_bytes = _canonical_json(safe_session)
    if len(session_bytes) > max(1, int(limits.max_single_file)):
        raise BundleValidationError("bundle exceeds single file size limit")
    artifacts: dict[str, dict] = {}
    declared_artifact_total = 0
    for message in safe_session.get("messages") or []:
        for descriptor in message.get("artifacts") or []:
            public = public_artifact_projection(descriptor)
            artifact_id = str(public.get("artifact_id") or "").strip()
            if not artifact_id:
                raise BundleValidationError("artifact id is missing")
            previous = artifacts.get(artifact_id)
            if previous is not None and previous != public:
                raise BundleValidationError("conflicting artifact descriptor")
            if previous is not None:
                continue
            if len(artifacts) + 3 > max(1, int(limits.max_files)):
                raise BundleValidationError("bundle exceeds file count limit")
            declared_size = int(public.get("size") or -1)
            if declared_size < 0 or declared_size > max(1, int(limits.max_single_file)):
                raise BundleValidationError("bundle exceeds single file size limit")
            declared_artifact_total += declared_size
            if len(session_bytes) + declared_artifact_total > max(
                1, int(limits.max_total_uncompressed)
            ):
                raise BundleValidationError("bundle exceeds total size limit")
            try:
                authorized = registry.authorize(session_id, artifact_id)
            except Exception as exc:
                raise BundleValidationError("artifact is unavailable") from exc
            data = authorized.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if (
                public.get("status") != "ready"
                or public.get("mime") != authorized.mime
                or int(public.get("size") or -1) != len(data)
                or str(public.get("sha256") or "") != digest
            ):
                raise BundleValidationError("artifact descriptor does not match stored image")
            extension = _artifact_extension(authorized.mime)
            member_path = f"artifacts/{artifact_id}.{extension}"
            artifacts[artifact_id] = public

    messages_bytes = _canonical_json(safe_session.get("messages") or [])
    manifest_artifacts = []
    for artifact_id, public in artifacts.items():
        extension = _artifact_extension(str(public.get("mime") or ""))
        member_path = f"artifacts/{artifact_id}.{extension}"
        manifest_artifacts.append({
            **public,
            "path": member_path,
            "source_turn_id": f"bundle:{artifact_id}",
            "source_tool_call_id": f"bundle:{artifact_id}",
        })
    manifest = {
        "bundle_schema_version": 1,
        "original_session_id": session_id,
        "session_sha256": hashlib.sha256(session_bytes).hexdigest(),
        "messages_sha256": hashlib.sha256(messages_bytes).hexdigest(),
        "artifacts": manifest_artifacts,
    }
    manifest_bytes = _canonical_json(manifest)
    if len(manifest_bytes) > max(1, int(limits.max_single_file)):
        raise BundleValidationError("bundle exceeds single file size limit")
    total_uncompressed = (
        len(session_bytes) + len(manifest_bytes)
        + sum(int(item.get("size") or 0) for item in artifacts.values())
    )
    if total_uncompressed > max(1, int(limits.max_total_uncompressed)):
        raise BundleValidationError("bundle exceeds total size limit")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("session.json", session_bytes)
        archive.writestr("manifest.json", manifest_bytes)
        for artifact_id, public in artifacts.items():
            extension = _artifact_extension(str(public.get("mime") or ""))
            authorized = registry.authorize(session_id, artifact_id)
            archive.writestr(
                f"artifacts/{artifact_id}.{extension}", authorized.read_bytes()
            )
    return out.getvalue()


def _validate_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise BundleValidationError("bundle contains a non-portable path")
    pure = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise BundleValidationError("bundle contains path traversal")
    if pure.as_posix() != name or ":" in pure.parts[0]:
        raise BundleValidationError("bundle contains a non-portable path")
    if name in {"session.json", "manifest.json"}:
        return
    if len(pure.parts) != 2 or pure.parts[0] != "artifacts" or not pure.parts[1]:
        raise BundleValidationError("bundle contains an unexpected file")


def _read_zip(raw: bytes, limits: BundleLimits) -> dict[str, bytes]:
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise BundleValidationError("bundle is empty")
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(raw)), "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleValidationError("bundle is not a valid ZIP") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > max(1, int(limits.max_files)):
            raise BundleValidationError("bundle exceeds file count limit")
        names: set[str] = set()
        total = 0
        for info in infos:
            _validate_member_name(info.filename)
            folded = info.filename.casefold()
            if folded in names:
                raise BundleValidationError("bundle contains a duplicate file name")
            names.add(folded)
            if info.is_dir():
                raise BundleValidationError("bundle directories are not allowed")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise BundleValidationError("bundle contains a symbolic link")
            if mode and stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise BundleValidationError("bundle contains a non-regular file")
            if info.flag_bits & 0x1:
                raise BundleValidationError("encrypted ZIP members are not supported")
            if info.file_size > max(1, int(limits.max_single_file)):
                raise BundleValidationError("bundle exceeds single file size limit")
            total += info.file_size
            if total > max(1, int(limits.max_total_uncompressed)):
                raise BundleValidationError("bundle exceeds total size limit")
            ratio = (
                float("inf") if info.file_size and not info.compress_size
                else info.file_size / max(1, info.compress_size)
            )
            if ratio > max(1.0, float(limits.max_compression_ratio)):
                raise BundleValidationError("bundle exceeds compression ratio limit")
        payloads: dict[str, bytes] = {}
        for info in infos:
            try:
                data = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise BundleValidationError("bundle member could not be read") from exc
            if len(data) != info.file_size:
                raise BundleValidationError("bundle member size changed while reading")
            payloads[info.filename] = data
    return payloads


def _json_object(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BundleValidationError(f"{label} must be an object")
    return value


def inspect_session_bundle(
    raw: bytes, *, limits: BundleLimits | None = None
) -> InspectedBundle:
    limits = limits or BundleLimits()
    payloads = _read_zip(raw, limits)
    if "session.json" not in payloads or "manifest.json" not in payloads:
        raise BundleValidationError("bundle is missing required metadata")
    session = _json_object(payloads["session.json"], "session.json")
    manifest = _json_object(payloads["manifest.json"], "manifest.json")
    if set(session) - _SESSION_FIELDS:
        raise BundleValidationError("session.json contains non-public fields")
    if set(manifest) - _MANIFEST_FIELDS:
        raise BundleValidationError("manifest contains non-public fields")
    if session.get("export_schema_version") != 2:
        raise BundleValidationError("unsupported session export schema")
    messages = session.get("messages")
    if not isinstance(messages, list):
        raise BundleValidationError("session messages must be a list")
    if manifest.get("bundle_schema_version") != 1:
        raise BundleValidationError("unsupported bundle schema")
    original_session_id = str(manifest.get("original_session_id") or "")
    if not original_session_id or original_session_id != str(session.get("session_id") or ""):
        raise BundleValidationError("bundle session identity mismatch")
    if hashlib.sha256(_canonical_json(session)).hexdigest() != manifest.get("session_sha256"):
        raise BundleValidationError("session hash mismatch")
    if hashlib.sha256(_canonical_json(messages)).hexdigest() != manifest.get("messages_sha256"):
        raise BundleValidationError("messages hash mismatch")

    artifact_rows = manifest.get("artifacts")
    if not isinstance(artifact_rows, list):
        raise BundleValidationError("artifact manifest must be a list")
    by_id: dict[str, dict] = {}
    artifact_bytes: dict[str, bytes] = {}
    declared_paths: set[str] = set()
    for row in artifact_rows:
        if not isinstance(row, dict):
            raise BundleValidationError("artifact manifest row is invalid")
        if set(row) - _MANIFEST_ARTIFACT_FIELDS:
            raise BundleValidationError("artifact manifest contains non-public fields")
        artifact_id = str(row.get("artifact_id") or "")
        member_path = str(row.get("path") or "")
        if not artifact_id or artifact_id in by_id:
            raise BundleValidationError("artifact id is missing or duplicate")
        _validate_member_name(member_path)
        if not member_path.startswith("artifacts/") or member_path in declared_paths:
            raise BundleValidationError("artifact path is invalid or duplicate")
        declared_paths.add(member_path)
        data = payloads.get(member_path)
        if data is None:
            raise BundleValidationError("artifact file is missing")
        public = public_artifact_projection(row)
        if set(public) != set(PUBLIC_ARTIFACT_FIELDS):
            raise BundleValidationError("artifact descriptor is incomplete")
        if public.get("kind") != "image" or public.get("status") != "ready":
            raise BundleValidationError("artifact descriptor is invalid")
        mime = str(public.get("mime") or "")
        expected_extension = _artifact_extension(mime)
        actual_extension = PurePosixPath(member_path).suffix.lower().lstrip(".")
        if actual_extension not in {
            expected_extension,
            "jpeg" if expected_extension == "jpg" else expected_extension,
        }:
            raise BundleValidationError("artifact path extension does not match MIME")
        name = str(public.get("name") or "")
        if not name or Path(name).name != name or name in {".", ".."}:
            raise BundleValidationError("artifact name is invalid")
        if len(data) != int(row.get("size") or -1):
            raise BundleValidationError("artifact size mismatch")
        if hashlib.sha256(data).hexdigest() != str(row.get("sha256") or ""):
            raise BundleValidationError("artifact hash mismatch")
        try:
            validate_image_bytes(data, declared_mime=str(row.get("mime") or ""))
        except Exception as exc:
            raise BundleValidationError("artifact image is invalid") from exc
        if public.get("artifact_id") != artifact_id:
            raise BundleValidationError("artifact descriptor is invalid")
        by_id[artifact_id] = row
        artifact_bytes[artifact_id] = data

    archive_artifact_paths = {
        name for name in payloads if name.startswith("artifacts/")
    }
    if archive_artifact_paths != declared_paths:
        raise BundleValidationError("bundle contains undeclared artifact files")
    referenced: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            raise BundleValidationError("session message is invalid")
        _validate_public_message_types(message)
        canonical = _canonical_public_message(
            message, session_id=original_session_id
        )
        if message != canonical:
            raise BundleValidationError(
                "session message does not match canonical public projection"
            )
        for raw_descriptor in message.get("artifacts") or []:
            if not isinstance(raw_descriptor, dict):
                raise BundleValidationError("message artifact descriptor is invalid")
            public = public_artifact_projection(raw_descriptor)
            if public != raw_descriptor or set(public) != set(PUBLIC_ARTIFACT_FIELDS):
                raise BundleValidationError("message artifact contains non-public fields")
            artifact_id = str(public.get("artifact_id") or "")
            row = by_id.get(artifact_id)
            if row is None:
                raise BundleValidationError("message references an unknown artifact")
            for key, value in public.items():
                if row.get(key) != value:
                    raise BundleValidationError("message artifact metadata mismatch")
            referenced.add(artifact_id)
    if referenced != set(by_id):
        raise BundleValidationError("manifest contains an unreferenced artifact")
    top_level_tools = session.get("tool_calls")
    if top_level_tools is not None:
        if not isinstance(top_level_tools, list) or any(
            not isinstance(call, dict) or call != _canonical_public_tool_event(call)
            for call in top_level_tools
        ):
            raise BundleValidationError(
                "session tool event does not match canonical public projection"
            )
    return InspectedBundle(session=session, manifest=manifest, artifact_bytes=artifact_bytes)


def import_session_bundle(
    raw: bytes,
    registry: ArtifactRegistry,
    *,
    workspace: Path | str,
    profile: str,
    persist_session: Callable[[Any], Any],
    limits: BundleLimits | None = None,
):
    """Validate then publish a fresh Session; failures remove all new state."""
    inspected = inspect_session_bundle(raw, limits=limits)
    from api.models import Session

    source = inspected.session
    messages = copy.deepcopy(source.get("messages") or [])
    session = Session(
        title=str(source.get("title") or "Imported session"),
        workspace=str(workspace),
        model=source.get("model"),
        model_provider=source.get("model_provider"),
        profile=profile,
        messages=messages,
        tool_calls=copy.deepcopy(source.get("tool_calls") or []),
        pinned=bool(source.get("pinned", False)),
        archived=False,
        personality=source.get("personality"),
    )
    created = False
    try:
        id_map: dict[str, dict] = {}
        rows = {str(row.get("artifact_id")): row for row in inspected.manifest["artifacts"]}
        for old_id, data in inspected.artifact_bytes.items():
            row = rows[old_id]
            identity = hashlib.sha256(
                f"{old_id}\0{row.get('path')}".encode("utf-8")
            ).hexdigest()[:24]
            public = registry.register_image_bytes(
                session.session_id,
                f"bundle-turn:{identity}",
                f"bundle-tool:{identity}",
                data,
                mime=str(row.get("mime") or ""),
                name=str(row.get("name") or "generated-image"),
            )
            id_map[old_id] = public
        for message in session.messages:
            if not isinstance(message, dict) or not isinstance(message.get("artifacts"), list):
                continue
            message["artifacts"] = [
                copy.deepcopy(id_map[str(item.get("artifact_id"))])
                for item in message["artifacts"]
            ]
        persist_session(session)
        created = True
        return session
    except Exception as cause:
        failed_stages = _rollback_failed_bundle_import(session, registry)
        if failed_stages:
            raise BundleImportRollbackError(
                session.session_id, failed_stages
            ) from cause
        raise


def _atomic_index_without_session(index_path: Path, session_id: str) -> None:
    if not index_path.is_file():
        return
    payload = json.loads(index_path.read_text("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("session index must be a list")
    filtered = [
        row for row in payload
        if not isinstance(row, dict) or str(row.get("session_id") or "") != session_id
    ]
    temporary = index_path.with_name(
        f".{index_path.name}.bundle-rollback-{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(filtered, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, index_path)
    finally:
        temporary.unlink(missing_ok=True)


def _index_contains_session(index_path: Path, session_id: str) -> bool:
    if not index_path.is_file():
        return False
    payload = json.loads(index_path.read_text("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("session index must be a list")
    return any(
        isinstance(row, dict)
        and str(row.get("session_id") or "") == session_id
        for row in payload
    )


def _quarantine_bundle_sidecars(session, failed_stages: list[str]) -> None:
    residue = [
        path for path in (session.path, session.path.with_suffix(".json.bak"))
        if path.exists()
    ]
    if not residue:
        return
    quarantine = session.path.parent / "_quarantine" / (
        f"bundle-import-{session.session_id}-{uuid.uuid4().hex[:12]}"
    )
    quarantine.mkdir(parents=True, exist_ok=False)
    for path in residue:
        os.replace(path, quarantine / path.name)
    (quarantine / "quarantine.json").write_text(json.dumps({
        "schema_version": 1,
        "reason": "bundle_import_rollback_incomplete",
        "failed_stages": sorted(set(failed_stages)),
    }, ensure_ascii=False, indent=2), "utf-8")


def _rollback_failed_bundle_import(session, registry: ArtifactRegistry) -> list[str]:
    """Clean every formal publication layer, verify it, and quarantine residue."""
    from api import models

    failed: list[str] = []
    try:
        registry.discard_unpublished_session(session.session_id)
    except Exception:
        failed.append("artifact_cleanup")

    for path in (session.path, session.path.with_suffix(".json.bak")):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            failed.append("sidecar_cleanup")

    try:
        models.prune_session_from_index(session.session_id)
    except Exception:
        failed.append("index_cleanup")
        try:
            _atomic_index_without_session(models.SESSION_INDEX_FILE, session.session_id)
        except Exception:
            failed.append("index_fallback")

    formal_artifact_dir = registry.root / session.session_id
    if formal_artifact_dir.exists():
        try:
            registry.quarantine_unpublished_session(
                session.session_id, failed_stages=failed or ["artifact_verification"]
            )
        except Exception:
            failed.append("artifact_quarantine")
    try:
        _quarantine_bundle_sidecars(session, failed or ["sidecar_verification"])
    except Exception:
        failed.append("sidecar_quarantine")
    try:
        if _index_contains_session(models.SESSION_INDEX_FILE, session.session_id):
            failed.append("index_verification")
            _atomic_index_without_session(models.SESSION_INDEX_FILE, session.session_id)
    except Exception:
        failed.append("index_verification")

    if failed:
        # Even when fallback cleanup removed every formal path, preserve a
        # private receipt so operators can distinguish a verified rollback
        # from one that required quarantine/fallback handling.
        try:
            if not (registry.root / session.session_id).exists():
                registry.quarantine_unpublished_session(
                    session.session_id, failed_stages=failed
                )
        except Exception:
            failed.append("quarantine_receipt")

    # Formal namespaces must never retain a failed import.  Verification
    # failures are explicit and cannot be downgraded to the original error.
    if (registry.root / session.session_id).exists():
        failed.append("artifact_residue")
    if session.path.exists() or session.path.with_suffix(".json.bak").exists():
        failed.append("sidecar_residue")
    try:
        if _index_contains_session(models.SESSION_INDEX_FILE, session.session_id):
            failed.append("index_residue")
    except Exception:
        failed.append("index_residue")
    return sorted(set(failed))
