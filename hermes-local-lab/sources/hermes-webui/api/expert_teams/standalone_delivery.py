"""Fail-closed read boundary for standalone expert-team deliveries."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from .delivery_integrity import (
    DeliveryIntegrityError,
    binding_manifest_path,
    canonical_attempt_root,
    classify_delivery_binding,
    path_contains_symlink,
    read_binding_manifest,
    sha256_file,
    validate_standalone_delivery_binding,
    workspace_relative_path,
)
from .storage import safe_run_id


_HEX64 = re.compile(r"[a-f0-9]{64}")


class StandaloneDeliveryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _positive_int(value: object, *, code: str, message: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise StandaloneDeliveryError(code, message)
    return value


def _error_from_integrity(exc: DeliveryIntegrityError) -> StandaloneDeliveryError:
    message = str(exc)
    if "symlink" in message:
        return StandaloneDeliveryError("delivery_path_symlink", message)
    if "document hash is stale" in message:
        return StandaloneDeliveryError("delivery_document_hash_mismatch", message)
    if "quality" in message and ("hash" in message or "stale" in message):
        return StandaloneDeliveryError("delivery_quality_invalid", message)
    return StandaloneDeliveryError("delivery_binding_invalid", message)


def _parse_canonical_ref_path(workspace: Path, run_id: str, ref: dict) -> tuple[Path, str, int, str]:
    """Parse only the one canonical binding path shape."""

    raw = str(ref.get("delivery_binding_path") or "").strip().replace("\\", "/")
    relative = PurePosixPath(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise StandaloneDeliveryError("delivery_binding_path_invalid", "交付绑定路径不安全")
    if (
        len(relative.parts) != 6
        or tuple(part.casefold() for part in relative.parts[:2]) != (".taiji", "expert-team-deliveries")
        or relative.parts[5] != "expert-team-delivery.json"
        or not relative.parts[4].startswith("attempt-")
    ):
        raise StandaloneDeliveryError("delivery_binding_path_invalid", "交付绑定路径不符合规范")
    path_run_id, stage_id = relative.parts[2], relative.parts[3]
    try:
        attempt = int(relative.parts[4].removeprefix("attempt-"))
    except ValueError as exc:
        raise StandaloneDeliveryError("delivery_binding_path_invalid", "交付 attempt 路径无效") from exc
    if path_run_id != safe_run_id(run_id):
        raise StandaloneDeliveryError("delivery_binding_cross_run", "交付绑定属于另一个 Run")
    expected = binding_manifest_path(workspace, path_run_id, stage_id, attempt)
    expected_relative = workspace_relative_path(workspace, expected)
    if raw != expected_relative:
        raise StandaloneDeliveryError("delivery_binding_path_invalid", "交付绑定不是规范路径")
    return expected, stage_id, attempt, expected_relative


def validate_standalone_delivery_context(workspace: Path, run: dict) -> dict:
    """Resolve the current standalone delivery exclusively from server-owned state."""

    root = Path(workspace).expanduser().resolve()
    if not isinstance(run, dict) or run.get("product_mode") != "standalone":
        raise StandaloneDeliveryError("standalone_delivery_required", "当前任务不是单机专家团交付")
    try:
        run_id = safe_run_id(str(run.get("run_id") or ""))
    except (TypeError, ValueError) as exc:
        raise StandaloneDeliveryError("delivery_run_invalid", "交付 Run 身份无效") from exc
    session_id = str(run.get("session_id") or "").strip()
    if not session_id:
        raise StandaloneDeliveryError("delivery_session_missing", "交付会话身份缺失")
    ref = run.get("current_delivery_manifest_ref")
    if not isinstance(ref, dict):
        raise StandaloneDeliveryError("delivery_binding_missing", "当前没有可确认的交付绑定")
    expected_ref_fields = {
        "artifact_id", "sha256", "stage_attempt", "delivery_attempt",
        "delivery_binding_path", "delivery_binding_sha256",
    }
    if set(ref) != expected_ref_fields:
        raise StandaloneDeliveryError("delivery_manifest_ref_invalid", "当前交付引用不完整")
    manifest_artifact_id = str(ref.get("artifact_id") or "").strip()
    manifest_artifact_sha256 = str(ref.get("sha256") or "").strip()
    manifest_stage_attempt = _positive_int(
        ref.get("stage_attempt"),
        code="delivery_manifest_ref_invalid",
        message="当前交付阶段 attempt 无效",
    )
    if (
        not manifest_artifact_id
        or not _HEX64.fullmatch(manifest_artifact_sha256)
        or manifest_stage_attempt <= 0
    ):
        raise StandaloneDeliveryError("delivery_manifest_ref_invalid", "当前交付产物引用无效")
    binding_path, stage_id, delivery_attempt, binding_display = _parse_canonical_ref_path(root, run_id, ref)
    expected_attempt = _positive_int(
        ref.get("delivery_attempt"),
        code="delivery_attempt_stale",
        message="交付 attempt 无效",
    )
    if expected_attempt != delivery_attempt:
        raise StandaloneDeliveryError("delivery_attempt_stale", "交付 attempt 已变更")
    expected_binding_sha256 = str(ref.get("delivery_binding_sha256") or "")
    if not _HEX64.fullmatch(expected_binding_sha256):
        raise StandaloneDeliveryError("delivery_binding_hash_invalid", "交付绑定摘要无效")
    if path_contains_symlink(root, binding_path):
        raise StandaloneDeliveryError("delivery_path_symlink", "交付绑定路径包含符号链接")
    if not binding_path.is_file():
        raise StandaloneDeliveryError("delivery_binding_missing", "交付绑定文件不存在")
    actual_binding_sha256 = sha256_file(binding_path)
    if actual_binding_sha256 != expected_binding_sha256:
        raise StandaloneDeliveryError("delivery_binding_hash_mismatch", "交付绑定摘要已变化")
    try:
        binding = read_binding_manifest(binding_path)
    except DeliveryIntegrityError as exc:
        raise _error_from_integrity(exc) from exc
    if classify_delivery_binding(binding) != "standalone_pre_confirmation":
        raise StandaloneDeliveryError("delivery_binding_schema_invalid", "当前绑定不是单机交付合同")
    if binding.get("session_id") != session_id:
        raise StandaloneDeliveryError("delivery_binding_cross_session", "交付绑定属于另一个会话")
    try:
        validated = validate_standalone_delivery_binding(
            root,
            {"run_id": run_id, "stage_id": stage_id, "attempt": delivery_attempt},
            binding,
        )
    except DeliveryIntegrityError as exc:
        raise _error_from_integrity(exc) from exc
    reservation = run.get("current_delivery_attempt_reservation")
    workflow_state = str(run.get("workflow_state") or "")
    if workflow_state not in {"awaiting_review", "completed"}:
        raise StandaloneDeliveryError("delivery_state_stale", "当前状态不能使用这份交付")
    expected_reservation_status = "confirmed" if workflow_state == "completed" else "generated_valid"
    if not isinstance(reservation, dict):
        raise StandaloneDeliveryError("delivery_reservation_stale", "交付预约与当前文档不一致")
    reservation_attempt = _positive_int(
        reservation.get("delivery_attempt"),
        code="delivery_reservation_stale",
        message="交付预约与当前文档不一致",
    )
    reservation_revision = _positive_int(
        reservation.get("document_revision"),
        code="delivery_reservation_stale",
        message="交付预约与当前文档不一致",
    )
    if (
        not str(reservation.get("reservation_id") or "").strip()
        or reservation_attempt != delivery_attempt
        or reservation_revision != int(validated.get("document_revision") or 0)
        or reservation.get("render_input_fingerprint") != validated.get("render_input_fingerprint")
        or reservation.get("status") != expected_reservation_status
    ):
        raise StandaloneDeliveryError("delivery_reservation_stale", "交付预约与当前文档不一致")
    ledger_matches = [
        item
        for item in run.get("delivery_attempt_reservations") or []
        if isinstance(item, dict)
        and item.get("reservation_id") == reservation.get("reservation_id")
        and item == reservation
    ]
    if len(ledger_matches) != 1:
        raise StandaloneDeliveryError("delivery_reservation_stale", "交付预约账本缺失或存在歧义")
    canonical = validated["canonical_artifact"]
    if manifest_stage_attempt != int(validated["stage_attempt"]):
        raise StandaloneDeliveryError("delivery_stage_attempt_stale", "交付阶段 attempt 已变更")
    manifest_artifacts = [
        item
        for item in run.get("stage_artifacts") or []
        if isinstance(item, dict)
        and item.get("artifact_id") == manifest_artifact_id
        and item.get("sha256") == manifest_artifact_sha256
    ]
    if len(manifest_artifacts) != 1:
        raise StandaloneDeliveryError("delivery_manifest_artifact_stale", "交付产物已变更或存在歧义")
    manifest_artifact = manifest_artifacts[0]
    manifest_payload = manifest_artifact.get("payload") if isinstance(manifest_artifact.get("payload"), dict) else {}
    if (
        manifest_artifact.get("artifact_type") != "delivery_manifest"
        or not isinstance(manifest_artifact.get("stage_attempt"), int)
        or manifest_artifact.get("stage_attempt") != manifest_stage_attempt
        or manifest_payload.get("schema_version") != "delivery-manifest/v2"
        or not isinstance(manifest_payload.get("delivery_attempt"), int)
        or manifest_payload.get("delivery_attempt") != delivery_attempt
        or manifest_payload.get("delivery_binding_path") != binding_display
        or manifest_payload.get("delivery_binding_sha256") != actual_binding_sha256
        or manifest_payload.get("document_sha256") != validated["document"]["sha256"]
    ):
        raise StandaloneDeliveryError("delivery_manifest_artifact_stale", "交付产物与当前文档不一致")
    attempt_root = canonical_attempt_root(root, run_id, stage_id, delivery_attempt)
    document_path = attempt_root / validated["document"]["path"]
    delivery_dir = attempt_root / "delivery"
    quality_path = attempt_root / validated["standalone_quality_report"]["path"]
    if path_contains_symlink(root, document_path) or path_contains_symlink(root, delivery_dir):
        raise StandaloneDeliveryError("delivery_path_symlink", "交付路径包含符号链接")
    document_sha256 = sha256_file(document_path)
    if document_sha256 != validated["document"]["sha256"]:
        raise StandaloneDeliveryError("delivery_document_hash_mismatch", "DOCX 文件摘要已变化")
    try:
        quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StandaloneDeliveryError("delivery_quality_invalid", "单机质量报告无效") from exc
    return {
        "session_id": session_id,
        "run_id": run_id,
        "stage_id": stage_id,
        "stage_attempt": int(validated["stage_attempt"]),
        "artifact_id": manifest_artifact_id,
        "artifact_sha256": manifest_artifact_sha256,
        "canonical_artifact_id": str(canonical["artifact_id"]),
        "canonical_artifact_sha256": str(canonical["sha256"]),
        "delivery_attempt": delivery_attempt,
        "document_revision": int(validated["document_revision"]),
        "render_input_fingerprint": str(validated["render_input_fingerprint"]),
        "delivery_binding_path": binding_display,
        "delivery_binding_sha256": actual_binding_sha256,
        "binding_path": binding_path,
        "binding": validated,
        "attempt_root": attempt_root,
        "document_path": document_path,
        "document_sha256": document_sha256,
        "delivery_dir": delivery_dir,
        "quality_path": quality_path,
        "quality_report": quality_report,
    }


def load_current_standalone_delivery(workspace: Path, run: dict) -> dict:
    """Compatibility name for callers that need the validated current delivery."""

    return validate_standalone_delivery_context(workspace, run)


def resolve_standalone_open_target(workspace: Path, run: dict, target: str) -> Path:
    """Resolve a server-owned open target; callers can never submit a filesystem path."""

    normalized = str(target or "").strip()
    if normalized not in {"document", "folder"}:
        raise StandaloneDeliveryError("delivery_target_invalid", "只能打开当前文档或所在文件夹")
    context = validate_standalone_delivery_context(workspace, run)
    return context["document_path" if normalized == "document" else "delivery_dir"]
