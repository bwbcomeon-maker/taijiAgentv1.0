"""Canonical identity and digest helpers for expert-team final deliveries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .storage import safe_run_id

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX only
    msvcrt = None


BINDING_SCHEMA_VERSION = 1
BINDING_MANIFEST_NAME = "expert-team-delivery.json"
WPS_ACCEPTANCE_MANIFEST_NAME = "expert-team-wps-acceptance.json"
OFFICE_REVIEW_PROOF_NAME = "expert-team-office-review-proof.json"
_STAGE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_ATTEMPT_LOCKS: dict[str, threading.RLock] = {}
_ATTEMPT_LOCKS_GUARD = threading.Lock()
_ATTEMPT_LOCK_DEPTH = threading.local()


class DeliveryIntegrityError(ValueError):
    """A final delivery does not match its canonical expert-team identity."""


def classify_delivery_binding(binding: dict) -> str:
    """Classify evidence without granting enterprise trust to legacy sidecars."""

    if isinstance(binding, dict) and binding.get("schema_version") == "expert-delivery-binding/v2":
        return "enterprise_pre_office"
    return "legacy_unverified"


def safe_stage_id(value: str) -> str:
    stage_id = str(value or "").strip()
    if not stage_id or not _STAGE_ID_PATTERN.fullmatch(stage_id):
        raise DeliveryIntegrityError("invalid expert-team delivery stage id")
    return stage_id


def canonical_attempt_root(workspace: Path, run_id: str, stage_id: str, attempt: int) -> Path:
    try:
        attempt_number = int(attempt)
    except (TypeError, ValueError) as exc:
        raise DeliveryIntegrityError("invalid expert-team delivery attempt") from exc
    if attempt_number < 1:
        raise DeliveryIntegrityError("invalid expert-team delivery attempt")
    return (
        Path(workspace).expanduser().resolve()
        / ".taiji"
        / "expert-team-deliveries"
        / safe_run_id(run_id)
        / safe_stage_id(stage_id)
        / f"attempt-{attempt_number}"
    )


def canonical_delivery_dir(workspace: Path, run_id: str, stage_id: str, attempt: int) -> Path:
    return canonical_attempt_root(workspace, run_id, stage_id, attempt) / "delivery"


def binding_manifest_path(workspace: Path, run_id: str, stage_id: str, attempt: int) -> Path:
    return canonical_attempt_root(workspace, run_id, stage_id, attempt) / BINDING_MANIFEST_NAME


def workspace_relative_path(workspace: Path, path: Path) -> str:
    root = Path(workspace).expanduser().resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise DeliveryIntegrityError(f"expert-team delivery path escapes workspace: {path}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_contains_symlink(workspace: Path, path: Path) -> bool:
    root = Path(workspace).expanduser().resolve()
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = root / raw
    try:
        relative = raw.absolute().relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def path_is_within_filesystem(target: Path, root: Path) -> bool:
    """Containment that stays fail-closed across case-insensitive aliases."""

    resolved_target = Path(target).expanduser().resolve()
    resolved_root = Path(root).expanduser().resolve()
    try:
        resolved_target.relative_to(resolved_root)
        return True
    except ValueError:
        pass
    if resolved_root.exists():
        current = resolved_target
        while True:
            if current.exists():
                try:
                    if os.path.samefile(current, resolved_root):
                        return True
                except OSError:
                    pass
            if current.parent == current:
                break
            current = current.parent
    return False


def path_has_expert_delivery_semantics(path: Path | str) -> bool:
    parts = tuple(part.casefold() for part in Path(path).expanduser().parts)
    return any(
        parts[index] == ".taiji" and parts[index + 1] == "expert-team-deliveries"
        for index in range(max(0, len(parts) - 1))
    )


def write_binding_manifest(
    workspace: Path,
    *,
    run_id: str,
    session_id: str,
    stage_id: str,
    attempt: int,
    source_path: Path,
    document_path: Path,
    delivery_dir: Path,
    rich_package: dict | None = None,
) -> tuple[Path, dict]:
    root = canonical_attempt_root(workspace, run_id, stage_id, attempt)
    expected_delivery = root / "delivery"
    expected_source = root / "final.md"
    expected_document = expected_delivery / "document.docx"
    if source_path.resolve() != expected_source.resolve():
        raise DeliveryIntegrityError("expert-team source path is not canonical")
    if document_path.resolve() != expected_document.resolve():
        raise DeliveryIntegrityError("expert-team document path is not canonical")
    if delivery_dir.resolve() != expected_delivery.resolve():
        raise DeliveryIntegrityError("expert-team delivery directory is not canonical")
    for path in (source_path, document_path, delivery_dir):
        if path_contains_symlink(workspace, path):
            raise DeliveryIntegrityError(f"expert-team delivery path contains a symlink: {path}")
    manifest = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "run_id": safe_run_id(run_id),
        "session_id": str(session_id or "").strip(),
        "stage_id": safe_stage_id(stage_id),
        "attempt": int(attempt),
        "source_path": workspace_relative_path(workspace, source_path),
        "source_sha256": sha256_file(source_path),
        "document_path": workspace_relative_path(workspace, document_path),
        "document_sha256": sha256_file(document_path),
        "delivery_dir": workspace_relative_path(workspace, delivery_dir),
    }
    if not manifest["session_id"]:
        raise DeliveryIntegrityError("expert-team delivery session id is missing")
    if rich_package is not None:
        if not isinstance(rich_package, dict) or not rich_package:
            raise DeliveryIntegrityError("expert-team rich package binding is invalid")
        manifest["rich_package"] = json.loads(json.dumps(rich_package))
    path = root / BINDING_MANIFEST_NAME
    _atomic_write_json(path, manifest)
    return path, manifest


def read_binding_manifest(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryIntegrityError(f"invalid expert-team delivery binding manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise DeliveryIntegrityError(f"invalid expert-team delivery binding manifest: {path}")
    return payload


def path_targets_expert_delivery_tree(workspace: Path, raw_path: str) -> bool:
    root = Path(workspace).expanduser().resolve()
    requested = Path(str(raw_path or "")).expanduser()
    lexical = requested if requested.is_absolute() else root / requested
    expert_root = root / ".taiji" / "expert-team-deliveries"
    return (
        path_has_expert_delivery_semantics(requested)
        or path_has_expert_delivery_semantics(lexical)
        or path_is_within_filesystem(lexical, expert_root)
    )


def _relative_parts_casefold(target: Path, root: Path) -> tuple[str, ...] | None:
    if not path_is_within_filesystem(target, root):
        return None
    target_parts = target.resolve().parts
    root_parts = root.resolve().parts
    if len(target_parts) < len(root_parts):
        return None
    if tuple(part.casefold() for part in target_parts[: len(root_parts)]) != tuple(
        part.casefold() for part in root_parts
    ):
        return None
    return target_parts[len(root_parts) :]


def delivery_identity_from_directory(workspace: Path, delivery_dir: Path) -> dict | None:
    root = Path(workspace).expanduser().resolve()
    expert_root = root / ".taiji" / "expert-team-deliveries"
    target = Path(delivery_dir).expanduser().resolve()
    parts = _relative_parts_casefold(target, expert_root)
    if parts is None:
        return None
    if len(parts) != 4 or parts[3] != "delivery" or not parts[2].startswith("attempt-"):
        raise DeliveryIntegrityError("expert-team delivery directory shape is not canonical")
    run_id, stage_id = parts[0], parts[1]
    try:
        attempt = int(parts[2].removeprefix("attempt-"))
    except ValueError as exc:
        raise DeliveryIntegrityError("expert-team delivery attempt path is invalid") from exc
    expected = canonical_delivery_dir(root, run_id, stage_id, attempt)
    if target != expected.absolute() or path_contains_symlink(root, expected):
        raise DeliveryIntegrityError("expert-team delivery directory is not canonical")
    return {
        "run_id": safe_run_id(run_id),
        "stage_id": safe_stage_id(stage_id),
        "attempt": attempt,
        "delivery_dir": expected,
        "attempt_root": expected.parent,
    }


def delivery_identity_from_path(workspace: Path, path: Path) -> dict | None:
    root = Path(workspace).expanduser().resolve()
    expert_root = root / ".taiji" / "expert-team-deliveries"
    raw = Path(path).expanduser()
    target = raw if raw.is_absolute() else root / raw
    parts = _relative_parts_casefold(target, expert_root)
    if parts is None:
        return None
    if len(parts) < 3 or not parts[2].startswith("attempt-"):
        raise DeliveryIntegrityError("expert-team delivery path shape is not canonical")
    run_id, stage_id = parts[0], parts[1]
    try:
        attempt = int(parts[2].removeprefix("attempt-"))
    except ValueError as exc:
        raise DeliveryIntegrityError("expert-team delivery attempt path is invalid") from exc
    attempt_root = canonical_attempt_root(root, run_id, stage_id, attempt)
    try:
        target.resolve().relative_to(attempt_root)
    except ValueError as exc:
        raise DeliveryIntegrityError("expert-team delivery path escapes its attempt root") from exc
    if path_contains_symlink(root, target):
        raise DeliveryIntegrityError("expert-team delivery path contains a symlink")
    return {
        "run_id": safe_run_id(run_id),
        "stage_id": safe_stage_id(stage_id),
        "attempt": attempt,
        "delivery_dir": attempt_root / "delivery",
        "attempt_root": attempt_root,
    }


def validated_binding_for_identity(workspace: Path, identity: dict) -> dict:
    root = Path(workspace).expanduser().resolve()
    run_id = safe_run_id(str(identity.get("run_id") or ""))
    stage_id = safe_stage_id(str(identity.get("stage_id") or ""))
    attempt = int(identity.get("attempt") or 0)
    attempt_root = canonical_attempt_root(root, run_id, stage_id, attempt)
    delivery_dir = attempt_root / "delivery"
    document_path = delivery_dir / "document.docx"
    manifest_path = attempt_root / BINDING_MANIFEST_NAME
    for path in (attempt_root, delivery_dir, document_path, manifest_path):
        if path_contains_symlink(root, path):
            raise DeliveryIntegrityError(f"expert-team delivery binding contains a symlink: {path}")
    if not document_path.is_file() or not manifest_path.is_file():
        raise DeliveryIntegrityError("expert-team delivery binding inputs are missing")
    manifest = read_binding_manifest(manifest_path)
    if classify_delivery_binding(manifest) == "enterprise_pre_office":
        if (
            manifest.get("run_id") != run_id
            or manifest.get("stage_id") != stage_id
            or int(manifest.get("delivery_attempt") or 0) != attempt
            or not str(manifest.get("session_id") or "").strip()
        ):
            raise DeliveryIntegrityError("enterprise delivery binding identity is stale")
        references = (
            ("canonical_markdown", "canonical/document.md"),
            ("asset_manifest", "assets/asset-manifest.json"),
            ("semantic_gates", "reviews/semantic-gates.json"),
            ("layered_quality_report", "reviews/enterprise-quality-report.json"),
            ("document", "delivery/document.docx"),
            ("automatic_quality_report", "delivery/quality-report.json"),
        )
        for field, expected_relative in references:
            reference = manifest.get(field)
            if not isinstance(reference, dict) or reference.get("path") != expected_relative:
                raise DeliveryIntegrityError(f"enterprise delivery binding {field} path is stale")
            target = attempt_root / expected_relative
            if path_contains_symlink(root, target) or not target.is_file():
                raise DeliveryIntegrityError(f"enterprise delivery binding {field} input is missing")
            if reference.get("sha256") != sha256_file(target):
                raise DeliveryIntegrityError(f"enterprise delivery binding {field} hash is stale")
        return manifest
    source_path = attempt_root / "final.md"
    if path_contains_symlink(root, source_path) or not source_path.is_file():
        raise DeliveryIntegrityError("expert-team delivery binding inputs are missing")
    expected = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": str(manifest.get("session_id") or "").strip(),
        "stage_id": stage_id,
        "attempt": attempt,
        "source_path": workspace_relative_path(root, source_path),
        "source_sha256": sha256_file(source_path),
        "document_path": workspace_relative_path(root, document_path),
        "document_sha256": sha256_file(document_path),
        "delivery_dir": workspace_relative_path(root, delivery_dir),
    }
    if "rich_package" in manifest:
        rich_package = manifest.get("rich_package")
        if not isinstance(rich_package, dict) or not rich_package:
            raise DeliveryIntegrityError("expert-team rich package binding is invalid")
        expected["rich_package"] = rich_package
    if not expected["session_id"] or manifest != expected:
        raise DeliveryIntegrityError("expert-team delivery binding manifest is stale")
    return expected


def wps_acceptance_manifest_path(workspace: Path, run_id: str, stage_id: str, attempt: int) -> Path:
    return canonical_attempt_root(workspace, run_id, stage_id, attempt) / WPS_ACCEPTANCE_MANIFEST_NAME


def write_wps_acceptance_manifest(
    workspace: Path,
    *,
    binding: dict,
    reviewer: str,
    note: str,
    visual_checks: list[str],
    wps_check: dict,
    office_review: dict | None = None,
) -> tuple[Path, dict]:
    path = wps_acceptance_manifest_path(
        workspace,
        str(binding.get("run_id") or ""),
        str(binding.get("stage_id") or ""),
        int(binding.get("attempt") or 0),
    )
    payload = {
        "schema_version": 1,
        "run_id": str(binding.get("run_id") or ""),
        "session_id": str(binding.get("session_id") or ""),
        "stage_id": str(binding.get("stage_id") or ""),
        "attempt": int(binding.get("attempt") or 0),
        "document_sha256": str(binding.get("document_sha256") or ""),
        "reviewer": str(reviewer or "").strip(),
        "note": str(note or "").strip(),
        "visual_checks": list(visual_checks),
        "visual_evidence": [
            dict(item) for item in wps_check.get("visualEvidence") or [] if isinstance(item, dict)
        ],
        "reviewed_at": str(wps_check.get("reviewedAt") or ""),
        "office_review": {
            "token_hash": str((office_review or {}).get("token_hash") or ""),
            "opened_at": str((office_review or {}).get("opened_at") or ""),
            "evidence_dir": str((office_review or {}).get("evidence_dir") or ""),
            "attested_actual_office_review": (office_review or {}).get("attested_actual_office_review") is True,
        },
    }
    _atomic_write_json(path, payload)
    return path, payload


def read_wps_acceptance_manifest(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryIntegrityError(f"invalid expert-team WPS acceptance manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise DeliveryIntegrityError(f"invalid expert-team WPS acceptance manifest: {path}")
    return payload


def validate_canonical_wps_evidence(
    workspace: Path,
    delivery_dir: Path,
    evidence: list[dict],
) -> list[dict]:
    root = Path(workspace).expanduser().resolve()
    canonical_delivery = Path(delivery_dir).expanduser().absolute()
    if path_contains_symlink(root, canonical_delivery) or not canonical_delivery.is_dir():
        raise DeliveryIntegrityError("WPS evidence delivery directory is not canonical")
    if not evidence:
        raise DeliveryIntegrityError("WPS visual evidence is missing")
    verified: list[dict] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise DeliveryIntegrityError("WPS visual evidence metadata is invalid")
        raw = str(item.get("path") or "").strip().replace("\\", "/")
        relative = PurePosixPath(raw)
        if (
            not raw
            or relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) < 3
            or tuple(part.casefold() for part in relative.parts[:2]) != ("evidence", "wps-visual")
        ):
            raise DeliveryIntegrityError("WPS visual evidence path is not canonical")
        target = canonical_delivery.joinpath(*relative.parts)
        try:
            target.resolve().relative_to(canonical_delivery.resolve())
        except (OSError, ValueError) as exc:
            raise DeliveryIntegrityError("WPS visual evidence escapes the delivery directory") from exc
        if path_contains_symlink(root, target) or not target.is_file():
            raise DeliveryIntegrityError("WPS visual evidence is missing or contains a symlink")
        expected_hash = str(item.get("sha256") or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected_hash) or sha256_file(target) != expected_hash:
            raise DeliveryIntegrityError("WPS visual evidence digest does not match its report")
        size = target.stat().st_size
        if size <= 0 or size > 50 * 1024 * 1024:
            raise DeliveryIntegrityError("WPS visual evidence size is invalid")
        with target.open("rb") as handle:
            header = handle.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif header.startswith(b"\xff\xd8\xff"):
            media_type = "image/jpeg"
        elif header.startswith(b"%PDF-"):
            media_type = "application/pdf"
        else:
            raise DeliveryIntegrityError("WPS visual evidence type is invalid")
        declared_size = item.get("sizeBytes")
        declared_type = str(item.get("mediaType") or "").strip()
        if declared_size is not None and int(declared_size) != size:
            raise DeliveryIntegrityError("WPS visual evidence size does not match its report")
        if declared_type and declared_type != media_type:
            raise DeliveryIntegrityError("WPS visual evidence type does not match its report")
        verified.append(
            {
                "path": relative.as_posix(),
                "sha256": expected_hash,
                "sizeBytes": size,
                "mediaType": media_type,
            }
        )
    return verified


@contextmanager
def delivery_attempt_lock(workspace: Path, run_id: str, stage_id: str, attempt: int):
    root = Path(workspace).expanduser().resolve()
    safe_run = safe_run_id(run_id)
    safe_stage = safe_stage_id(stage_id)
    attempt_number = int(attempt)
    lock_path = (
        root
        / ".taiji"
        / "expert-team-deliveries"
        / ".locks"
        / f"{safe_run}.{safe_stage}.attempt-{attempt_number}.lock"
    )
    key = str(lock_path)
    with _ATTEMPT_LOCKS_GUARD:
        thread_lock = _ATTEMPT_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        depths = getattr(_ATTEMPT_LOCK_DEPTH, "values", None)
        if not isinstance(depths, dict):
            depths = {}
            _ATTEMPT_LOCK_DEPTH.values = depths
        if int(depths.get(key) or 0) > 0:
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        depths[key] = 1
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            elif msvcrt is not None:  # pragma: no cover - Windows only
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                    os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            else:  # pragma: no cover - unsupported platform
                raise DeliveryIntegrityError("no supported delivery attempt lock is available")
            yield
        finally:
            try:
                if locked and fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif locked and msvcrt is not None:  # pragma: no cover - Windows only
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)
                depths.pop(key, None)


def delivery_digest_set(
    workspace: Path,
    *,
    run_id: str,
    session_id: str,
    stage_id: str,
    attempt: int,
    workspace_roots: list[Path] | None = None,
) -> dict:
    root = Path(workspace).expanduser().resolve()
    attempt_root = canonical_attempt_root(root, run_id, stage_id, attempt)
    delivery_dir = attempt_root / "delivery"
    manifest_path = attempt_root / BINDING_MANIFEST_NAME
    binding = read_binding_manifest(manifest_path) if manifest_path.is_file() else {}
    enterprise = classify_delivery_binding(binding) == "enterprise_pre_office"
    source_path = attempt_root / ("canonical/document.md" if enterprise else "final.md")
    if not source_path.is_file() or not delivery_dir.is_dir():
        raise DeliveryIntegrityError("expert-team delivery snapshot inputs are missing")
    candidates = [source_path]
    for sidecar_name in (
        BINDING_MANIFEST_NAME,
        WPS_ACCEPTANCE_MANIFEST_NAME,
        OFFICE_REVIEW_PROOF_NAME,
    ):
        sidecar = attempt_root / sidecar_name
        if sidecar.exists():
            if sidecar.is_symlink() or not sidecar.is_file():
                raise DeliveryIntegrityError(f"expert-team delivery snapshot contains an invalid sidecar: {sidecar}")
            candidates.append(sidecar)
    for current_root, dirnames, filenames in os.walk(delivery_dir, followlinks=False):
        current = Path(current_root)
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                raise DeliveryIntegrityError(f"expert-team delivery snapshot contains a symlink: {child}")
        for name in filenames:
            child = current / name
            if child.is_symlink() or not child.is_file():
                raise DeliveryIntegrityError(f"expert-team delivery snapshot contains a non-regular file: {child}")
            candidates.append(child)
    if enterprise:
        for relative in (
            "brief.json",
            "canonical/artifact.json",
            "assets/asset-manifest.json",
            "reviews/semantic-gates.json",
            "reviews/enterprise-quality-report.json",
        ):
            child = attempt_root / relative
            if path_contains_symlink(root, child) or not child.is_file():
                raise DeliveryIntegrityError(f"enterprise delivery snapshot is missing {relative}")
            candidates.append(child)
    files = {
        path.relative_to(attempt_root).as_posix(): sha256_file(path)
        for path in sorted(candidates, key=lambda item: item.as_posix())
    }
    document_key = "delivery/document.docx"
    if document_key not in files:
        raise DeliveryIntegrityError("expert-team delivery snapshot is missing document.docx")
    extra_root_names: list[str] = []
    extra_candidates: list[Path] = []
    for extra in workspace_roots or []:
        candidate = Path(extra).expanduser().resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise DeliveryIntegrityError(f"expert-team snapshot root escapes workspace: {candidate}") from exc
        if path_contains_symlink(root, candidate) or not candidate.exists():
            raise DeliveryIntegrityError(f"expert-team snapshot root is missing or contains a symlink: {candidate}")
        extra_root_names.append(relative.as_posix())
        if candidate.is_file():
            extra_candidates.append(candidate)
            continue
        if not candidate.is_dir():
            raise DeliveryIntegrityError(f"expert-team snapshot root is not regular: {candidate}")
        for current_root, dirnames, filenames in os.walk(candidate, followlinks=False):
            current = Path(current_root)
            for name in dirnames:
                child = current / name
                if child.is_symlink():
                    raise DeliveryIntegrityError(f"expert-team snapshot root contains a symlink: {child}")
            for name in filenames:
                child = current / name
                if child.is_symlink() or not child.is_file():
                    raise DeliveryIntegrityError(f"expert-team snapshot root contains a non-regular file: {child}")
                extra_candidates.append(child)
    workspace_files = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(set(extra_candidates), key=lambda item: item.as_posix())
    }
    return {
        "run_id": safe_run_id(run_id),
        "session_id": str(session_id or "").strip(),
        "stage_id": safe_stage_id(stage_id),
        "attempt": int(attempt),
        "source_sha256": files["canonical/document.md" if enterprise else "final.md"],
        "document_sha256": files[document_key],
        "files": files,
        "workspace_roots": sorted(set(extra_root_names)),
        "workspace_files": workspace_files,
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
