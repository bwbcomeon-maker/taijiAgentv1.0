"""Clean expert-team state machine."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from .catalog import CONTENT_CREATOR_TEAM_ID, get_template
from .documents import (
    FinalDocumentDeliveryError,
    build_final_document_delivery,
    is_final_delivery_stage,
)
from .delivery_integrity import (
    BINDING_SCHEMA_VERSION,
    DeliveryIntegrityError,
    binding_manifest_path,
    canonical_attempt_root,
    canonical_delivery_dir,
    delivery_attempt_lock,
    delivery_digest_set,
    path_contains_symlink,
    read_binding_manifest,
    read_wps_acceptance_manifest,
    sha256_file,
    validate_canonical_wps_evidence,
    wps_acceptance_manifest_path,
    workspace_relative_path,
)
from .materials import (
    business_context_for_run,
    stage_result_from_output,
    structured_output_from_delivery,
    validate_stage_output,
)
from .rich_draft import (
    RichDraftPackagingError,
    build_rich_draft_package,
    is_rich_draft_required,
    verify_rich_draft_package,
)
from .storage import latest_run_for_session, read_run, run_file_lock, run_path, write_run
from .view import expert_team_run_view


TERMINAL_STATES = {"completed", "failed", "cancelled"}
_FINAL_DELIVERY_ARTIFACT_KINDS = {"final_document", "delivery_package", "quality_report"}
_ACTION_JOURNAL_LIMIT = 128
_OBSERVATION_LEDGER_MAX_EVENTS = 4096
_OBSERVATION_LEDGER_MAX_BYTES = 8 * 1024 * 1024
_OBSERVATION_EVENT_ID_MAX_BYTES = 1024
_OBSERVATION_CURSOR_MAX_BYTES = 4096
_START_RESERVATION_LEASE_SECONDS = 30.0
_CONTROL_RETRY_DELAY_SECONDS = 5.0
_CONTROL_RETRY_DEADLINE_SECONDS = 300.0
_BASE_WPS_VISUAL_CHECKS = {
    "document_opened",
    "layout_reviewed",
    "content_order_reviewed",
}
_START_LOCKS: dict[str, threading.RLock] = {}
_START_LOCKS_GUARD = threading.Lock()
_RUN_FILE_LOCK_DEPTH = threading.local()
_EXPECTED_CURSOR_UNSET = object()


class ExpertTeamStateConflict(ValueError):
    """A caller attempted to mutate a stale or differently owned run."""

    def __init__(self, code: str, message: str, run: dict | None = None):
        super().__init__(message)
        self.code = str(code)
        self.run = deepcopy(run) if isinstance(run, dict) else None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _duration(started_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        return max(0, int(time.time() - datetime.fromisoformat(started_at).timestamp()))
    except Exception:
        return 0


def _with_view(run: dict) -> dict:
    run["duration_seconds"] = _duration(str(run.get("created_at") or ""))
    run["view"] = expert_team_run_view(run)
    return run


def _rich_draft_artifact(package: dict, *, stage: str, attempt: int) -> dict:
    return {
        "id": f"{stage}:{attempt}:rich_draft",
        "kind": "rich_draft",
        "label": "富内容初稿",
        "title": str(package.get("title") or "富内容初稿"),
        "path": str(package.get("draft_path") or ""),
        "manifest_path": str(package.get("manifest_path") or ""),
        "assets": list(package.get("assets") or []),
        "table_count": int(package.get("table_count") or 0),
        "figure_count": int(package.get("figure_count") or 0),
        "exists": True,
        "attempt": attempt,
        "stage": stage,
        "status": "ready",
        "created_at": str(package.get("created_at") or _now()),
    }


def _chat_artifact(output: dict, *, stage: str, attempt: int) -> dict:
    return {
        "id": f"{stage}:{attempt}:chat",
        "kind": "chat",
        "label": "结果已写入对话",
        "title": str(output.get("visible_title") or output.get("title") or "阶段结果"),
        "path": "",
        "exists": True,
        "attempt": attempt,
        "stage": stage,
        "status": "ready",
        "created_at": _now(),
    }


def _upsert_artifact(run: dict, artifact: dict) -> None:
    artifact_id = str(artifact.get("id") or "")
    if not artifact_id:
        raise ValueError("expert team artifact id is required")
    rows = [deepcopy(item) for item in run.get("artifacts") or [] if isinstance(item, dict)]
    for index, item in enumerate(rows):
        if str(item.get("id") or "") == artifact_id:
            rows[index] = deepcopy(artifact)
            run["artifacts"] = rows
            return
    rows.append(deepcopy(artifact))
    run["artifacts"] = rows


def _refresh_final_delivery_artifacts(workspace: Path, run: dict, stage_id: str) -> list[str]:
    material_type = str(business_context_for_run(run).get("material_type") or "office_material")
    required = {"final_document", "delivery_package", "quality_report"}
    if material_type != "meeting_minutes":
        required.add("final_rich_draft")
    rows = [deepcopy(item) for item in run.get("artifacts") or [] if isinstance(item, dict)]
    attempts = [
        int(item.get("attempt") or 0)
        for item in rows
        if str(item.get("stage") or "") == stage_id and str(item.get("kind") or "") in required
    ]
    latest_attempt = max(attempts, default=0)
    found: set[str] = set()
    missing: list[str] = []
    root = Path(workspace).expanduser().resolve()
    for item in rows:
        kind = str(item.get("kind") or "")
        if (
            str(item.get("stage") or "") != stage_id
            or int(item.get("attempt") or 0) != latest_attempt
            or kind not in required
        ):
            continue
        found.add(kind)
        raw_path = str(item.get("path") or "").strip()
        target = Path(raw_path).expanduser() if raw_path else Path()
        if raw_path and not target.is_absolute():
            target = root / target
        exists = bool(
            raw_path
            and (
                target.is_dir()
                if kind == "delivery_package"
                else target.is_file()
            )
        )
        item["exists"] = exists
        if not exists:
            item["status"] = "missing"
            missing.append(kind)
    missing.extend(sorted(required - found))
    run["artifacts"] = rows
    return sorted(set(missing))


def _authoritative_delivery_attempt(run: dict, stage_id: str) -> tuple[int, dict]:
    outputs = [
        output for output in run.get("stage_outputs") or []
        if isinstance(output, dict)
        and str(output.get("task_id") or output.get("stage_id") or "") == stage_id
    ]
    if not outputs:
        raise DeliveryIntegrityError("final delivery has no authoritative stage output")
    expected_attempt = len(outputs)
    latest_output = outputs[-1]
    try:
        recorded_attempt = int(latest_output.get("stage_attempt"))
    except (TypeError, ValueError) as exc:
        raise DeliveryIntegrityError("final delivery stage output attempt is missing") from exc
    if recorded_attempt != expected_attempt:
        raise DeliveryIntegrityError("final delivery stage output attempt is inconsistent")
    document_delivery = latest_output.get("document_delivery")
    if not isinstance(document_delivery, dict) or int(document_delivery.get("attempt") or 0) != expected_attempt:
        raise DeliveryIntegrityError("final delivery metadata is missing from the authoritative stage output")
    return expected_attempt, document_delivery


def _canonical_final_delivery_context(workspace: Path, run: dict, stage_id: str) -> dict:
    attempt, document_delivery = _authoritative_delivery_attempt(run, stage_id)
    root = Path(workspace).expanduser().resolve()
    run_id = str(run.get("run_id") or "").strip()
    session_id = str(run.get("session_id") or "").strip()
    attempt_root = canonical_attempt_root(root, run_id, stage_id, attempt)
    delivery_dir = canonical_delivery_dir(root, run_id, stage_id, attempt)
    source_path = attempt_root / "final.md"
    document_path = delivery_dir / "document.docx"
    quality_report_path = delivery_dir / "quality-report.json"
    manifest_path = binding_manifest_path(root, run_id, stage_id, attempt)
    expected_paths = {
        "final_document": workspace_relative_path(root, document_path),
        "delivery_package": workspace_relative_path(root, delivery_dir),
        "quality_report": workspace_relative_path(root, quality_report_path),
    }
    expected_manifest_path = workspace_relative_path(root, manifest_path)
    rows = [item for item in run.get("artifacts") or [] if isinstance(item, dict)]
    relevant_rows = [
        item for item in rows
        if str(item.get("stage") or "") == stage_id
        and str(item.get("kind") or "") in _FINAL_DELIVERY_ARTIFACT_KINDS
    ]
    artifacts: dict[str, dict] = {}
    for kind in sorted(_FINAL_DELIVERY_ARTIFACT_KINDS):
        matches = [
            item for item in relevant_rows
            if str(item.get("kind") or "") == kind
            and int(item.get("attempt") or 0) == attempt
        ]
        if len(matches) != 1:
            raise DeliveryIntegrityError(f"final delivery artifact {kind} is missing or ambiguous")
        artifacts[kind] = matches[0]
    if any(int(item.get("attempt") or 0) > attempt for item in relevant_rows):
        raise DeliveryIntegrityError("final delivery artifacts contain a future attempt")
    for kind, artifact in artifacts.items():
        raw_path = str(artifact.get("path") or "").strip()
        raw = Path(raw_path)
        if raw.is_absolute() or ".." in raw.parts or raw.as_posix() != expected_paths[kind]:
            raise DeliveryIntegrityError(f"final delivery artifact {kind} path is not canonical")
        if str(artifact.get("id") or "") != f"{stage_id}:{attempt}:{kind}":
            raise DeliveryIntegrityError(f"final delivery artifact {kind} id is not canonical")
        if (
            str(artifact.get("run_id") or "") != run_id
            or str(artifact.get("session_id") or "") != session_id
            or str(artifact.get("stage") or "") != stage_id
            or int(artifact.get("attempt") or 0) != attempt
        ):
            raise DeliveryIntegrityError(f"final delivery artifact {kind} identity does not match the run")
        if str(artifact.get("binding_manifest_path") or "") != expected_manifest_path:
            raise DeliveryIntegrityError(f"final delivery artifact {kind} binding manifest is not canonical")
        target = root / raw
        if path_contains_symlink(root, target):
            raise DeliveryIntegrityError(f"final delivery artifact {kind} contains a symlink")
        exists = target.is_dir() if kind == "delivery_package" else target.is_file()
        if not exists:
            raise DeliveryIntegrityError(f"final delivery artifact {kind} is missing")
    if not source_path.is_file() or path_contains_symlink(root, source_path):
        raise DeliveryIntegrityError("final delivery source is missing or noncanonical")
    if not manifest_path.is_file() or path_contains_symlink(root, manifest_path):
        raise DeliveryIntegrityError("final delivery binding manifest is missing or noncanonical")
    source_sha256 = sha256_file(source_path)
    document_sha256 = sha256_file(document_path)
    expected_binding = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": session_id,
        "stage_id": stage_id,
        "attempt": attempt,
        "source_path": workspace_relative_path(root, source_path),
        "source_sha256": source_sha256,
        "document_path": workspace_relative_path(root, document_path),
        "document_sha256": document_sha256,
        "delivery_dir": workspace_relative_path(root, delivery_dir),
    }
    stored_binding = read_binding_manifest(manifest_path)
    if "rich_package" in stored_binding:
        rich_package = stored_binding.get("rich_package")
        if not isinstance(rich_package, dict) or not rich_package:
            raise DeliveryIntegrityError("final rich package binding is invalid")
        expected_binding["rich_package"] = rich_package
    if stored_binding != expected_binding:
        raise DeliveryIntegrityError("final delivery binding manifest does not match current files")
    for kind, artifact in artifacts.items():
        if (
            str(artifact.get("source_sha256") or "") != source_sha256
            or str(artifact.get("document_sha256") or "") != document_sha256
        ):
            raise DeliveryIntegrityError(f"final delivery artifact {kind} digest binding is stale")
    if (
        str(document_delivery.get("binding_manifest_path") or "") != expected_manifest_path
        or str(document_delivery.get("source_sha256") or "") != source_sha256
        or str(document_delivery.get("document_sha256") or "") != document_sha256
        or str(document_delivery.get("delivery_dir") or "") != expected_paths["delivery_package"]
        or document_delivery.get("rich_package", {}) != expected_binding.get("rich_package", {})
    ):
        raise DeliveryIntegrityError("authoritative stage output delivery metadata is stale")
    if str(document_delivery.get("template_id") or "") == "general-proposal":
        _verified_final_rich_package(
            root,
            run,
            stage_id,
            attempt,
            document_delivery,
            expected_binding,
        )
    return {
        "attempt": attempt,
        "attempt_root": attempt_root,
        "delivery_dir": delivery_dir,
        "source_path": source_path,
        "document_path": document_path,
        "quality_report_path": quality_report_path,
        "binding_manifest_path": manifest_path,
        "binding": expected_binding,
        "artifacts": artifacts,
    }


def _verified_final_rich_package(
    workspace: Path,
    run: dict,
    stage_id: str,
    attempt: int,
    document_delivery: dict,
    binding: dict,
) -> dict:
    frozen = binding.get("rich_package")
    if not isinstance(frozen, dict) or not frozen:
        raise DeliveryIntegrityError("final rich package is not bound to the rendered delivery")
    rows = [
        item
        for item in run.get("artifacts") or []
        if isinstance(item, dict)
        and str(item.get("stage") or "") == stage_id
        and int(item.get("attempt") or 0) == attempt
        and str(item.get("kind") or "") == "final_rich_draft"
    ]
    if len(rows) != 1:
        raise DeliveryIntegrityError("final rich draft artifact is missing or ambiguous")
    artifact = rows[0]
    root = Path(workspace).expanduser().resolve()
    expected_attempt_root = (
        root
        / ".taiji"
        / "rich-drafts"
        / str(run.get("run_id") or "")
        / stage_id
        / f"attempt-{attempt}"
    )
    package_root = expected_attempt_root / "package"
    source_path = expected_attempt_root / "draft.md"
    if (
        str(frozen.get("package_dir") or "") != workspace_relative_path(root, package_root)
        or str(frozen.get("source_path") or "") != workspace_relative_path(root, source_path)
    ):
        raise DeliveryIntegrityError("final rich package binding paths are not canonical")
    try:
        verified = verify_rich_draft_package(
            root,
            package_root,
            source_path=source_path,
            title=str(artifact.get("title") or ""),
        )
    except RichDraftPackagingError as exc:
        raise DeliveryIntegrityError(str(exc)) from exc
    if verified.get("package_binding") != frozen:
        raise DeliveryIntegrityError("final rich package changed after DOCX rendering")
    expected_artifact = {
        "path": verified.get("draft_path"),
        "manifest_path": verified.get("manifest_path"),
        "image_list_path": verified.get("image_list_path"),
        "package_dir": verified.get("package_dir"),
        "rich_source_path": verified.get("rich_source_path"),
        "rich_source_sha256": verified.get("rich_source_sha256"),
        "package_files": verified.get("package_files"),
        "package_binding": verified.get("package_binding"),
        "assets": verified.get("assets"),
    }
    if any(artifact.get(key) != value for key, value in expected_artifact.items()):
        raise DeliveryIntegrityError("final rich draft artifact metadata is stale")
    if document_delivery.get("rich_package") != frozen:
        raise DeliveryIntegrityError("final document delivery rich-package binding is stale")
    return verified


def _final_delivery_snapshot_roots(
    workspace: Path,
    run: dict,
    stage_id: str,
    attempt: int,
) -> list[Path]:
    _recorded_attempt, document_delivery = _authoritative_delivery_attempt(run, stage_id)
    if str(document_delivery.get("template_id") or "") != "general-proposal":
        return []
    root = Path(workspace).expanduser().resolve()
    verified = _verified_final_rich_package(
        root,
        run,
        stage_id,
        attempt,
        document_delivery,
        {"rich_package": document_delivery.get("rich_package")},
    )
    return [
        root / str(verified.get("package_dir") or ""),
        root / str(verified.get("rich_source_path") or ""),
    ]


def _delivery_validation_report(payload: dict) -> dict:
    report = payload.get("quality_report")
    return deepcopy(report) if isinstance(report, dict) else {}


def _failed_delivery_checks(report: dict) -> list[dict]:
    return [
        deepcopy(check)
        for check in report.get("checks") or []
        if isinstance(check, dict) and str(check.get("status") or "") == "failed"
    ]


def _office_only_delivery_failure(payload: dict, report: dict) -> bool:
    failed_checks = _failed_delivery_checks(report)
    if not failed_checks or {
        str(check.get("id") or "") for check in failed_checks
    } != {"wps_visual"}:
        return False
    wps_messages = {
        str(check.get("message") or "").strip()
        for check in failed_checks
        if str(check.get("message") or "").strip()
    }
    recorded_failures = {
        str(item).strip()
        for item in (payload.get("failures") or report.get("failures") or [])
        if str(item or "").strip()
    }
    return not recorded_failures or (bool(wps_messages) and recorded_failures <= wps_messages)


def _wps_visual_evidence_complete(report: dict, workspace: Path, context: dict) -> bool:
    checks = [check for check in report.get("checks") or [] if isinstance(check, dict)]
    wps_check = next((check for check in checks if str(check.get("id") or "") == "wps_visual"), None)
    if not isinstance(wps_check, dict):
        return False
    if str(wps_check.get("status") or "") not in {"passed", "passed_with_warnings"}:
        return False
    visual_checks = {
        str(item).strip()
        for item in wps_check.get("visualChecks") or []
        if str(item or "").strip()
    }
    if not _BASE_WPS_VISUAL_CHECKS.issubset(visual_checks):
        return False
    evidence = [item for item in wps_check.get("visualEvidence") or [] if isinstance(item, dict)]
    if not evidence or any(not str(item.get("path") or "").strip() or not str(item.get("sha256") or "").strip() for item in evidence):
        return False
    if not all(
        str(wps_check.get(field) or "").strip()
        for field in ("reviewedAt", "reviewedBy", "documentSha256")
    ):
        return False
    binding = context.get("binding") if isinstance(context.get("binding"), dict) else {}
    if str(wps_check.get("documentSha256") or "") != str(binding.get("document_sha256") or ""):
        return False
    try:
        verified_evidence = validate_canonical_wps_evidence(
            workspace,
            context.get("delivery_dir"),
            evidence,
        )
    except (DeliveryIntegrityError, OSError, TypeError, ValueError):
        return False
    if verified_evidence != evidence:
        return False
    reviewer = str(wps_check.get("reviewedBy") or "").strip()
    if not _real_wps_reviewer(reviewer):
        return False
    try:
        sidecar_path = wps_acceptance_manifest_path(
            workspace,
            str(binding.get("run_id") or ""),
            str(binding.get("stage_id") or ""),
            int(binding.get("attempt") or 0),
        )
        if path_contains_symlink(workspace, sidecar_path) or not sidecar_path.is_file():
            return False
        sidecar = read_wps_acceptance_manifest(sidecar_path)
    except (DeliveryIntegrityError, OSError, TypeError, ValueError):
        return False
    expected_identity = {
        "schema_version": 1,
        "run_id": str(binding.get("run_id") or ""),
        "session_id": str(binding.get("session_id") or ""),
        "stage_id": str(binding.get("stage_id") or ""),
        "attempt": int(binding.get("attempt") or 0),
        "document_sha256": str(binding.get("document_sha256") or ""),
    }
    if any(sidecar.get(key) != value for key, value in expected_identity.items()):
        return False
    note = str(sidecar.get("note") or "").strip()
    if sidecar.get("reviewer") != reviewer or not _semantic_wps_note(note):
        return False
    sidecar_checks = [str(item).strip() for item in sidecar.get("visual_checks") or []]
    report_checks = [str(item).strip() for item in wps_check.get("visualChecks") or []]
    if sidecar_checks != report_checks:
        return False
    if sidecar.get("visual_evidence") != evidence:
        return False
    office_review = sidecar.get("office_review") if isinstance(sidecar.get("office_review"), dict) else {}
    token_hash = str(office_review.get("token_hash") or "")
    if (
        len(token_hash) != 64
        or any(character not in "0123456789abcdef" for character in token_hash)
        or not str(office_review.get("opened_at") or "").strip()
        or not str(office_review.get("evidence_dir") or "").strip()
        or office_review.get("attested_actual_office_review") is not True
    ):
        return False
    if str(sidecar.get("reviewed_at") or "") != str(wps_check.get("reviewedAt") or ""):
        return False
    try:
        from .office_review import validate_consumed_review_provenance

        validate_consumed_review_provenance(
            workspace,
            binding=binding,
            sidecar=sidecar,
            delivery_dir=context.get("delivery_dir"),
        )
    except (DeliveryIntegrityError, OSError, TypeError, ValueError):
        return False
    return True


def _real_wps_reviewer(reviewer: str) -> bool:
    normalized = str(reviewer or "").strip()
    return len(normalized) >= 2 and normalized.lower() not in {
        "user",
        "reviewer",
        "test",
        "integration-test",
        "unknown",
        "anonymous",
        "匿名",
        "系统",
        "审核人",
        "用户",
    }


def _semantic_wps_note(note: str) -> bool:
    normalized = str(note or "").strip().lower()
    return (
        len(normalized) >= 10
        and any(token in normalized for token in ("wps", "word"))
        and any(token in normalized for token in ("打开", "页面", "逐页", "分页", "导出"))
        and any(
            token in normalized
            for token in ("版式", "布局", "目录", "图表", "图片", "表格", "页眉", "页脚", "字体")
        )
    )


def _delivery_gate_snapshot(
    payload: dict,
    *,
    status: str,
    required_action: str,
) -> dict:
    report = _delivery_validation_report(payload)
    return {
        "status": status,
        "required_action": required_action,
        "checked_at": _now(),
        "validator_ok": payload.get("ok") is True,
        "validator_code": str(payload.get("code") or ""),
        "delivery_dir": str(payload.get("delivery_dir") or ""),
        "quality_report_path": str(payload.get("quality_report_path") or ""),
        "quality_report": report,
        "failures": [str(item) for item in payload.get("failures") or report.get("failures") or []],
    }


def _set_delivery_gate_validation(run: dict, gate: dict, message: str) -> None:
    failed_checks = _failed_delivery_checks(gate.get("quality_report") or {})
    failed_ids = [str(check.get("id") or "delivery_validation") for check in failed_checks]
    if not failed_ids:
        failed_ids = [str(item) for item in gate.get("failures") or [] if str(item or "").strip()]
    validation = {
        "status": str(gate.get("status") or "regeneration_required"),
        "action": str(gate.get("required_action") or "regenerate_delivery"),
        "violations": failed_ids,
        "missing_sections": failed_ids,
        "message": message,
    }
    run["delivery_gate"] = gate
    run["validation"] = validation
    run["last_validation_error"] = message
    if isinstance(run.get("stage_result"), dict):
        run["stage_result"]["validation"] = deepcopy(validation)


def _delivery_failure_message(payload: dict) -> str:
    report = _delivery_validation_report(payload)
    details = [
        str(check.get("message") or check.get("id") or "").strip()
        for check in _failed_delivery_checks(report)
    ]
    if not details:
        details = [str(item).strip() for item in payload.get("failures") or []]
    if not details and str(payload.get("message") or "").strip():
        details = [str(payload.get("message") or "").strip()]
    suffix = "；".join(item for item in details[:3] if item)
    message = "最终交付包校验失败，请重新生成 DOCX 交付包后再复核。"
    return f"{message} {suffix}" if suffix else message


def _validate_latest_final_delivery(
    workspace: Path,
    context: dict,
    *,
    write_report: bool,
) -> tuple[str, dict, str]:
    from api import docx_engine_v2

    delivery_dir = workspace_relative_path(workspace, context["delivery_dir"])
    try:
        payload, _status = docx_engine_v2.validate_delivery(
            {"delivery_dir": delivery_dir, "write_report": write_report},
            workspace,
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        payload = {
            "ok": False,
            "code": "delivery_validation_unavailable",
            "message": str(exc),
            "delivery_dir": delivery_dir,
            "quality_report": {},
            "failures": [str(exc)],
        }
    report = _delivery_validation_report(payload)
    office_only_failure = _office_only_delivery_failure(payload, report)
    if office_only_failure or (
        payload.get("ok") is True
        and not _wps_visual_evidence_complete(report, workspace, context)
    ):
        gate = _delivery_gate_snapshot(
            payload,
            status="office_acceptance_required",
            required_action="complete_office_acceptance",
        )
        return (
            "office_acceptance_required",
            gate,
            "最终交付包的自动校验已完成，请先在 WPS/Word 中完成视觉验收并补齐完整检查项和截图/PDF 证据，再确认完成任务。",
        )
    if (
        payload.get("ok") is not True
        or str(report.get("status") or "") not in {"passed", "passed_with_warnings"}
        or bool(_failed_delivery_checks(report))
    ):
        gate = _delivery_gate_snapshot(
            payload,
            status="regeneration_required",
            required_action="regenerate_delivery",
        )
        return "regeneration_required", gate, _delivery_failure_message(payload)
    gate = _delivery_gate_snapshot(
        payload,
        status="passed",
        required_action="none",
    )
    return "passed", gate, "最终 DOCX 交付包和 WPS/Word 验收证据均已通过校验。"


def _current_final_delivery_snapshot(
    workspace: Path,
    run: dict,
    stage_id: str,
    attempt: int,
    original_context: dict,
) -> dict:
    refreshed_context = _canonical_final_delivery_context(workspace, run, stage_id)
    if refreshed_context["binding"] != original_context["binding"]:
        raise DeliveryIntegrityError("final delivery binding changed during validation")
    return delivery_digest_set(
        workspace,
        run_id=str(run.get("run_id") or ""),
        session_id=str(run.get("session_id") or ""),
        stage_id=stage_id,
        attempt=attempt,
        workspace_roots=_final_delivery_snapshot_roots(
            workspace,
            run,
            stage_id,
            attempt,
        ),
    )


def _changed_delivery_gate(workspace: Path, context: dict, previous_gate: dict, exc: Exception) -> tuple[str, dict, str]:
    message = f"最终交付包在校验期间发生变化，请重新生成后再复核。 {exc}"
    gate = {
        "status": "regeneration_required",
        "required_action": "regenerate_delivery",
        "checked_at": _now(),
        "validator_ok": False,
        "validator_code": "delivery_changed_during_validation",
        "delivery_dir": workspace_relative_path(workspace, context["delivery_dir"]),
        "quality_report_path": workspace_relative_path(workspace, context["quality_report_path"]),
        "quality_report": previous_gate.get("quality_report") or {},
        "failures": [str(exc)],
    }
    return "regeneration_required", gate, message


def _refresh_artifact_existence(workspace: Path, run: dict) -> dict:
    root = Path(workspace).expanduser().resolve()
    rows = [deepcopy(item) for item in run.get("artifacts") or [] if isinstance(item, dict)]
    for item in rows:
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        target = Path(raw_path).expanduser()
        if not target.is_absolute():
            target = root / target
        kind = str(item.get("kind") or "")
        exists = target.is_dir() if kind == "delivery_package" else target.is_file()
        item["exists"] = exists
        if not exists:
            item["status"] = "missing"
        elif str(item.get("status") or "") == "missing":
            item["status"] = "ready"
    run["artifacts"] = rows
    return run


def _completion_integrity_for_read(workspace: Path, run: dict) -> dict:
    if str(run.get("workflow_state") or "") != "completed":
        return run
    checked_at = _now()
    gate = run.get("delivery_gate") if isinstance(run.get("delivery_gate"), dict) else {}
    expected = gate.get("digest_set") if isinstance(gate.get("digest_set"), dict) else None
    if expected is None:
        run["completion_integrity"] = {
            "status": "unverified",
            "checked_at": checked_at,
            "message": "已完成交付缺少可用的全量摘要快照，不能确认当前文件未被替换。",
        }
        return run
    try:
        run_id = str(expected.get("run_id") or "")
        session_id = str(expected.get("session_id") or "")
        stage_id = str(expected.get("stage_id") or "")
        attempt = int(expected.get("attempt") or 0)
        if run_id != str(run.get("run_id") or "") or session_id != str(run.get("session_id") or ""):
            raise DeliveryIntegrityError("stored completion digest identity does not match the run")
        with delivery_attempt_lock(workspace, run_id, stage_id, attempt):
            _canonical_final_delivery_context(workspace, run, stage_id)
            current = delivery_digest_set(
                workspace,
                run_id=run_id,
                session_id=session_id,
                stage_id=stage_id,
                attempt=attempt,
                workspace_roots=_final_delivery_snapshot_roots(
                    workspace,
                    run,
                    stage_id,
                    attempt,
                ),
            )
        if current != expected:
            raise DeliveryIntegrityError("completed delivery digest set no longer matches current files")
    except (DeliveryIntegrityError, FileNotFoundError, OSError, TypeError, ValueError) as exc:
        run["completion_integrity"] = {
            "status": "drifted",
            "checked_at": checked_at,
            "message": f"已完成交付文件缺失或摘要变化：{exc}",
        }
        return run
    run["completion_integrity"] = {
        "status": "valid",
        "checked_at": checked_at,
        "message": "已完成交付与审批时的全量摘要快照一致。",
    }
    return run


def _task_statuses(tasks: list[dict], index: int, state: str) -> list[dict]:
    rows = []
    for idx, task in enumerate(tasks):
        item = deepcopy(task)
        if idx < index:
            item["status"] = "done"
        elif idx == index:
            item["status"] = {
                "collecting_required": "pending",
                "collecting_optional": "pending",
                "ready_to_generate": "pending",
                "starting": "starting",
                "start_failed": "error",
                "generation_failed": "error",
                "result_unverified": "awaiting_input",
                "generating": "running",
                "cancelling": "running",
                "awaiting_stage_input": "awaiting_input",
                "generated_invalid": "error",
                "awaiting_review": "awaiting_review",
                "revising": "running",
                "completed": "done",
                "failed": "error",
                "cancelled": "cancelled",
            }.get(state, "pending")
        else:
            item["status"] = "pending"
        return_label = {
            "done": "完成",
            "pending": "待执行",
            "running": "执行中",
            "starting": "启动中",
            "awaiting_review": "待复核",
            "awaiting_input": "等待确认",
            "error": "需重试",
            "cancelled": "已取消",
        }.get(str(item.get("status")), str(item.get("status")))
        item["status_label"] = return_label
        rows.append(item)
    return rows


def _members(template: dict) -> list[dict]:
    return [{**deepcopy(member), "status": "待命"} for member in template.get("members") or []]


def _initial_timeline_events(template: dict) -> list[dict]:
    now = _now()
    events = [
        {
            "type": "team_created",
            "title": f"{template.get('title') or '专家团'}已创建",
            "detail": "等待需求确认后开始协作。",
            "member_id": "director",
            "at": now,
        }
    ]
    for member in template.get("members") or []:
        events.append(
            {
                "type": "member_joined",
                "title": f"{member.get('name') or '专家'}已加入",
                "detail": str(member.get("role") or "专家协作"),
                "member_id": str(member.get("id") or ""),
                "at": now,
            }
        )
    events.append(
        {
            "type": "intake_requested",
            "title": "等待需求确认",
            "detail": "请先补充必填需求，可选补充需要提交或跳过。",
            "member_id": "director",
            "at": now,
        }
    )
    events.append(
        {
            "type": "phase_plan_created",
            "title": "已生成专家团阶段计划",
            "detail": "将按流程安排、素材整理、初稿撰写、审稿打磨、交付确认推进。",
            "member_id": "director",
            "at": now,
        }
    )
    return events


def _questions(template: dict, prompt: str) -> list[dict]:
    rows = []
    for question in template.get("questions") or []:
        item = deepcopy(question)
        item["status"] = "pending"
        item["answer"] = ""
        item["confirmation_group"] = "intake_required" if item.get("required") else "intake_optional"
        rows.append(item)
    return rows


def _current_stage(run: dict) -> dict:
    tasks = (
        run.get("_tasks_template")
        if isinstance(run.get("_tasks_template"), list) and run.get("_tasks_template")
        else run.get("tasks") if isinstance(run.get("tasks"), list) else []
    )
    index = int(run.get("current_stage_index") or 0)
    if not tasks:
        return {}
    index = min(max(index, 0), len(tasks) - 1)
    task = deepcopy(tasks[index])
    return {
        "index": index,
        "id": task.get("id"),
        "task_id": task.get("id"),
        "title": task.get("title"),
        "phase": task.get("phase"),
        "worker_id": task.get("worker_id"),
        "worker_name": task.get("worker_name"),
        "status": str(task.get("status") or "pending"),
    }


def _authoritative_stage_for_mutation(run: dict) -> dict:
    tasks = run.get("_tasks_template")
    if not isinstance(tasks, list) or not tasks:
        raise ExpertTeamStateConflict(
            "corrupt_run_state",
            "expert team task template is missing",
            run,
        )
    try:
        index = int(run.get("current_stage_index"))
    except (TypeError, ValueError) as exc:
        raise ExpertTeamStateConflict(
            "corrupt_run_state",
            "expert team current_stage_index is invalid",
            run,
        ) from exc
    if index < 0 or index >= len(tasks) or not isinstance(tasks[index], dict):
        raise ExpertTeamStateConflict(
            "corrupt_run_state",
            "expert team current_stage_index is outside the task template",
            run,
        )
    task = tasks[index]
    stage_id = str(task.get("id") or task.get("task_id") or "").strip()
    if not stage_id:
        raise ExpertTeamStateConflict(
            "corrupt_run_state",
            "expert team authoritative stage id is missing",
            run,
        )
    persisted = run.get("current_stage")
    if not isinstance(persisted, dict):
        raise ExpertTeamStateConflict(
            "corrupt_run_state",
            "expert team persisted current_stage is missing",
            run,
        )
    persisted_id = str(persisted.get("task_id") or persisted.get("id") or "").strip()
    try:
        persisted_index = int(persisted.get("index"))
    except (TypeError, ValueError) as exc:
        raise ExpertTeamStateConflict(
            "corrupt_run_state",
            "expert team persisted current_stage index is invalid",
            run,
        ) from exc
    if persisted_id != stage_id or persisted_index != index:
        raise ExpertTeamStateConflict(
            "corrupt_run_state",
            "expert team persisted current_stage does not match the authoritative task template",
            run,
        )
    return {
        "index": index,
        "id": stage_id,
        "task_id": stage_id,
        "title": task.get("title"),
        "phase": task.get("phase"),
        "worker_id": task.get("worker_id"),
        "worker_name": task.get("worker_name"),
        "status": str(persisted.get("status") or task.get("status") or "pending"),
    }


def _request_fingerprint(body: dict, action: str) -> str:
    fingerprint_body = {
        str(key): value
        for key, value in body.items()
        if key not in {"expected_version", "idempotency_key"}
    }
    raw = json.dumps(
        {"action": action, "body": fingerprint_body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _start_lock(workspace: Path, run_id: str) -> threading.RLock:
    key = str(run_path(workspace, run_id).resolve())
    with _START_LOCKS_GUARD:
        return _START_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _run_mutation_lock(workspace: Path, run_id: str):
    """Serialize one run across both threads and independent WebUI processes."""
    key = str(run_path(workspace, run_id).resolve())
    with _start_lock(workspace, run_id):
        depths = getattr(_RUN_FILE_LOCK_DEPTH, "depths", None)
        if depths is None:
            depths = {}
            _RUN_FILE_LOCK_DEPTH.depths = depths
        depth = int(depths.get(key) or 0)
        if depth:
            depths[key] = depth + 1
            try:
                yield
            finally:
                remaining = int(depths.get(key) or 1) - 1
                if remaining:
                    depths[key] = remaining
                else:
                    depths.pop(key, None)
            return
        with run_file_lock(workspace, run_id):
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)


def _serialized_body_mutation(function):
    @wraps(function)
    def wrapped(workspace: Path, body: dict, *args, **kwargs):
        run_id = str((body or {}).get("run_id") or "")
        with _run_mutation_lock(workspace, run_id):
            return function(workspace, body, *args, **kwargs)

    return wrapped


def _serialized_run_mutation(function):
    @wraps(function)
    def wrapped(workspace: Path, run_id: str, *args, **kwargs):
        with _run_mutation_lock(workspace, run_id):
            return function(workspace, run_id, *args, **kwargs)

    return wrapped


def _require_mutable_v2(run: dict) -> None:
    if int(run.get("schema_version") or 0) < 2:
        raise ExpertTeamStateConflict(
            "legacy_read_only",
            "legacy expert team runs are read-only",
            run,
        )


def _idempotency_result(
    workspace: Path,
    run: dict,
    body: dict,
    action: str,
    stage_id: str,
    fingerprint: str,
) -> dict | None:
    key = str(body.get("idempotency_key") or "").strip()
    if not key:
        return None
    for entry in run.get("action_journal") or []:
        if not isinstance(entry, dict) or str(entry.get("idempotency_key") or "") != key:
            continue
        if (
            str(entry.get("action") or "") != action
            or str(entry.get("stage_id") or "") != stage_id
            or str(entry.get("request_fingerprint") or "") != fingerprint
        ):
            raise ExpertTeamStateConflict(
                "idempotency_key_reused",
                "idempotency_key was already used for another expert team action",
                run,
            )
        replay = _refresh_artifact_existence(workspace, deepcopy(run))
        replay = _completion_integrity_for_read(workspace, replay)
        return _sync_derived(replay)
    return None


def _prepare_mutation(
    workspace: Path,
    body: dict,
    action: str,
    *,
    skip_idempotency_result: bool = False,
) -> tuple[dict, dict | None]:
    run = read_run(workspace, str(body.get("run_id") or ""))
    _require_mutable_v2(run)
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required for expert team v2 mutations")
    if session_id and session_id != str(run.get("session_id") or "").strip():
        raise ExpertTeamStateConflict(
            "wrong_session",
            "expert team run does not belong to this session",
            run,
        )

    for field in ("expected_version", "stage_id", "idempotency_key"):
        if body.get(field) is None or (isinstance(body.get(field), str) and not str(body.get(field)).strip()):
            raise ValueError(f"{field} is required for expert team v2 mutations")

    requested_stage_id = str(body.get("stage_id") or "").strip()
    fingerprint = _request_fingerprint(body, action)
    duplicate = (
        None
        if skip_idempotency_result
        else _idempotency_result(workspace, run, body, action, requested_stage_id, fingerprint)
    )
    if duplicate is not None:
        return run, duplicate

    authoritative_stage = _authoritative_stage_for_mutation(run)

    if body.get("expected_version") is not None:
        try:
            expected_version = int(body.get("expected_version"))
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_version must be an integer") from exc
        if expected_version != int(run.get("version") or 0):
            raise ExpertTeamStateConflict(
                "version_conflict",
                f"expert team run version changed: expected {expected_version}, current {int(run.get('version') or 0)}",
                run,
            )

    if requested_stage_id:
        current_stage_id = str(authoritative_stage.get("task_id") or authoritative_stage.get("id") or "")
        if requested_stage_id != current_stage_id:
            raise ExpertTeamStateConflict(
                "stale_stage",
                f"expert team stage changed: expected {requested_stage_id}, current {current_stage_id}",
                run,
            )
    return run, None


def _record_action(run: dict, body: dict, action: str) -> None:
    key = str(body.get("idempotency_key") or "").strip()
    if not key:
        return
    journal = [deepcopy(entry) for entry in run.get("action_journal") or [] if isinstance(entry, dict)]
    stage_id = str(body.get("stage_id") or "").strip()
    journal.append(
        {
            "idempotency_key": key,
            "action": action,
            "stage_id": stage_id,
            "request_fingerprint": _request_fingerprint(body, action),
            "version_before": int(run.get("version") or 0),
            "at": _now(),
        }
    )
    run["action_journal"] = journal[-_ACTION_JOURNAL_LIMIT:]


def _clear_execution_patch() -> dict:
    return {
        "execution_started_at": "",
        "execution_stream_id": "",
        "execution_turn_id": "",
        "execution_runtime_run_id": "",
        "execution_runtime_adapter": "",
        "execution_stage_id": "",
        "execution_start_id": "",
        "execution_start_reserved_at": "",
        "execution_start_deadline_at": 0,
        "execution_start_dispatch_state": "",
        "execution_start_dispatching_at": "",
        "execution_message_start_index": None,
        "pending_user_message": "",
        "execution_cursor": "",
        "execution_last_event_id": "",
        "execution_delivered_last_event_id": "",
        "execution_delivered_through_sequence": None,
        "execution_public_output_buffer": "",
        "execution_public_observations": [],
        "execution_seen_event_ids": [],
        "orphan_runtime_run_id": "",
        "orphan_runtime_adapter": "",
        "execution_cleanup_status": "",
        "execution_cleanup_error": "",
        "execution_cleanup_retry_count": 0,
        "execution_cleanup_next_retry_at": 0,
        "execution_cleanup_deadline_at": 0,
        "cancel_request_id": "",
        "cancel_request_fingerprint": "",
        "cancel_requested_at": "",
        "cancel_previous_state": "",
        "cancel_runtime_accepted": False,
        "cancel_outcome": "",
        "cancel_retry_count": 0,
        "cancel_next_retry_at": 0,
        "cancel_deadline_at": 0,
        "last_execution_error": "",
    }


def _sync_derived(run: dict) -> dict:
    state = str(run.get("workflow_state") or "collecting_required")
    if state == "generating" and not str(run.get("execution_stream_id") or "").strip():
        state = "start_failed"
        run["workflow_state"] = state
        run.setdefault("last_execution_error", "生成连接缺少执行标识，请重新尝试。")
    tasks_template = [deepcopy(task) for task in run.get("_tasks_template") or run.get("tasks") or []]
    if tasks_template:
        run["_tasks_template"] = tasks_template
        run["tasks"] = _task_statuses(tasks_template, int(run.get("current_stage_index") or 0), state)
    run["current_stage"] = _current_stage(run)
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    run["phase"] = str(current.get("phase") or "需求确认")
    current_worker_id = str(current.get("worker_id") or "")
    current_worker = str(current.get("worker_name") or "")
    member_status = []
    for member in run.get("members") or []:
        item = deepcopy(member) if isinstance(member, dict) else {}
        name = str(item.get("name") or "")
        member_id = str(item.get("id") or "")
        if state in {"collecting_required", "collecting_optional"} and item.get("id") == "director":
            item["status"] = "等待确认"
        elif state == "ready_to_generate" and (member_id == current_worker_id or name == current_worker):
            item["status"] = "待启动"
        elif state == "starting" and (member_id == current_worker_id or name == current_worker):
            item["status"] = "启动中"
        elif state == "start_failed" and (member_id == current_worker_id or name == current_worker):
            item["status"] = "需重试"
        elif state == "generation_failed" and (member_id == current_worker_id or name == current_worker):
            item["status"] = "生成失败"
        elif state == "result_unverified" and (member_id == current_worker_id or name == current_worker):
            item["status"] = "等待核验"
        elif state in {"generating", "revising", "cancelling"} and (
            member_id == current_worker_id or name == current_worker
        ):
            item["status"] = "执行中"
        elif state == "awaiting_stage_input" and (member_id == current_worker_id or name == current_worker):
            item["status"] = "等待确认"
        elif state in {"awaiting_review", "generated_invalid"} and (member_id == current_worker_id or name == current_worker):
            item["status"] = "待复核"
        elif state == "completed":
            item["status"] = "已完成"
        elif member_id != current_worker_id:
            item["status"] = "待命"
        member_status.append(item)
    if member_status:
        run["members"] = member_status
    run["status"] = {
        "collecting_required": "awaiting_user",
        "collecting_optional": "awaiting_user",
        "ready_to_generate": "awaiting_user",
        "starting": "starting",
        "start_failed": "error",
        "generation_failed": "error",
        "result_unverified": "awaiting_user",
        "generating": "running",
        "cancelling": "running",
        "awaiting_stage_input": "awaiting_user",
        "generated_invalid": "awaiting_user",
        "awaiting_review": "awaiting_user",
        "revising": "running",
        "completed": "done",
        "failed": "error",
        "cancelled": "cancelled",
    }.get(state, "awaiting_user")
    run["execution_status"] = {
        "ready_to_generate": "idle",
        "starting": "starting",
        "start_failed": "error",
        "generation_failed": "error",
        "result_unverified": "awaiting_result",
        "generating": "running",
        "cancelling": "cancelling",
        "awaiting_stage_input": "paused",
        "revising": "running",
        "completed": "done",
        "failed": "error",
        "cancelled": "cancelled",
    }.get(state, "idle")
    completion_integrity = (
        run.get("completion_integrity")
        if isinstance(run.get("completion_integrity"), dict)
        else {}
    )
    if state == "completed" and str(completion_integrity.get("status") or "") not in {"", "valid"}:
        run["status"] = "error"
        run["execution_status"] = "error"
    return _with_view(run)


def _transition(workspace: Path, run: dict, state: str, event: str, patch: dict | None = None) -> dict:
    previous = str(run.get("workflow_state") or "")
    if previous in TERMINAL_STATES:
        raise ValueError(f"Cannot transition terminal expert team run from {previous} to {state}")
    next_run = deepcopy(run)
    next_run["workflow_state"] = state
    next_run["updated_at"] = _now()
    next_run["version"] = int(run.get("version") or 0) + 1
    if patch:
        next_run.update(patch)
    events = list(next_run.get("events") or [])
    events.append({"type": event, "from": previous, "to": state, "at": next_run["updated_at"]})
    next_run["events"] = events
    timeline = list(next_run.get("timeline_events") or [])
    timeline_title = {
        "questions_answered": "需求信息已更新",
        "generation_start_reserved": "正在启动当前阶段",
        "generation_started": "专家开始执行当前阶段",
        "generation_start_failed": "当前阶段启动失败，可重新尝试",
        "generation_start_expired": "当前阶段启动超时，可重新尝试",
        "stage_input_requested": "当前专家请求确认",
        "stage_input_answered": "阶段确认已提交",
        "generation_completed": "阶段成果已生成",
        "generation_invalid": "草稿未通过办公材料校验",
        "delivery_validation_failed": "最终交付包未通过校验",
        "office_acceptance_required": "需完成 WPS/Word 验收",
        "stage_approved": "阶段成果已确认",
        "stage_revision_requested": "已收到修改意见",
        "generation_resumed": "准备重新生成当前阶段",
        "generation_failed": "生成失败",
        "generation_result_unverified": "生成结束，结果等待核验",
        "generation_cancelled": "本轮生成已停止",
        "generation_cancel_requested": "正在停止本轮生成",
        "generation_cancel_accepted": "已受理停止请求",
        "generation_cancel_rejected": "停止请求未被执行",
    }.get(event)
    if timeline_title:
        current = _current_stage(next_run)
        timeline.append(
            {
                "type": event,
                "title": timeline_title,
                "detail": str(current.get("phase") or next_run.get("phase") or ""),
                "member_id": str(current.get("worker_id") or "director"),
                "at": next_run["updated_at"],
            }
        )
        next_run["timeline_events"] = timeline
    return write_run(workspace, _sync_derived(next_run))


def start_expert_team(workspace: Path, body: dict) -> dict:
    template = get_template(str(body.get("team_id") or CONTENT_CREATOR_TEAM_ID))
    prompt = str(body.get("prompt") or body.get("message") or "").strip()
    if not prompt:
        prompt = "请起草一份办公材料。"
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required to start an expert team")
    run = {
        "schema_version": 2,
        "version": 1,
        "run_id": "et-" + uuid.uuid4().hex[:16],
        "session_id": session_id,
        "team_id": template["id"],
        "team_title": template["title"],
        "team_image": template.get("image") or "",
        "title": prompt[:120],
        "prompt": prompt,
        "created_at": _now(),
        "updated_at": _now(),
        "workflow_state": "collecting_required",
        "current_stage_index": 0,
        "questions": _questions(template, prompt),
        "answers": [],
        "members": _members(template),
        "_tasks_template": deepcopy(template.get("tasks") or []),
        "tasks": deepcopy(template.get("tasks") or []),
        "artifacts": [],
        "stage_outputs": [],
        "review_items": [],
        "action_journal": [],
        "events": [{"type": "team_created", "to": "collecting_required", "at": _now()}],
        "timeline_events": _initial_timeline_events(template),
    }
    return write_run(workspace, _sync_derived(run))


def read_expert_team_run(workspace: Path, run_id: str) -> dict:
    run = _refresh_artifact_existence(workspace, read_run(workspace, run_id))
    return _sync_derived(_completion_integrity_for_read(workspace, run))


def latest_expert_team_run_for_session(workspace: Path, session_id: str) -> dict:
    run = _refresh_artifact_existence(workspace, latest_run_for_session(workspace, session_id))
    return _sync_derived(_completion_integrity_for_read(workspace, run))


def _apply_answers(run: dict, answers: dict, skip_optional: bool) -> dict:
    rows = []
    answer_rows = list(run.get("answers") or [])
    for question in run.get("questions") or []:
        item = deepcopy(question)
        qid = str(item.get("id") or "")
        if qid in answers:
            raw = "" if answers.get(qid) is None else str(answers.get(qid)).strip()
            if item.get("required") and not raw:
                rows.append(item)
                continue
            if not item.get("required") and not raw and skip_optional:
                item["status"] = "skipped"
                item["answer"] = ""
            elif not item.get("required") and not raw:
                rows.append(item)
                continue
            else:
                item["status"] = "answered"
                item["answer"] = raw
            answer_rows = [row for row in answer_rows if not isinstance(row, dict) or row.get("question_id") != qid]
            answer_rows.append({"question_id": qid, "answer": item.get("answer") or "", "status": item["status"]})
        rows.append(item)
    run["questions"] = rows
    run["answers"] = answer_rows
    return run


def _intake_state(run: dict) -> str:
    questions = [q for q in run.get("questions") or [] if isinstance(q, dict)]
    if any(q.get("required") and q.get("status") == "pending" for q in questions):
        return "collecting_required"
    if any((not q.get("required")) and q.get("status") == "pending" for q in questions):
        return "collecting_optional"
    return "ready_to_generate"


def _execution_start_reservation_patch(runtime_adapter: str) -> dict:
    """Build one durable start reservation.

    ``reserved`` proves a new reservation committed before its dispatch helper
    was entered. Historical persisted rows may still have an empty marker, but
    no newly created reservation is allowed to omit this evidence.
    """
    adapter_name = str(runtime_adapter or "").strip()
    now = time.time()
    return {
        **_clear_execution_patch(),
        "execution_start_id": "start-" + uuid.uuid4().hex[:16],
        "execution_start_reserved_at": _now(),
        "execution_start_deadline_at": now + _START_RESERVATION_LEASE_SECONDS,
        "execution_start_dispatch_state": "reserved",
        "execution_start_dispatching_at": "",
        "execution_runtime_adapter": adapter_name,
    }


@_serialized_body_mutation
def answer_expert_team(workspace: Path, body: dict) -> dict:
    run, duplicate = _prepare_mutation(workspace, body, "answer")
    if duplicate is not None:
        return duplicate
    if str(run.get("workflow_state") or "") not in {"collecting_required", "collecting_optional", "ready_to_generate"}:
        raise ValueError("Expert team is not collecting requirements")
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    run = _apply_answers(run, answers, bool(body.get("skip_optional")))
    _record_action(run, body, "answer")
    return _transition(workspace, run, _intake_state(run), "questions_answered")


@_serialized_body_mutation
def answer_and_reserve_expert_team_execution_start(
    workspace: Path,
    body: dict,
    *,
    runtime_adapter: str,
) -> tuple[dict, bool]:
    """Submit intake and, when complete, durably reserve start in the same write.

    The boolean is true only for the caller that created the reservation. Exact
    idempotent retries receive the authoritative run without dispatching a
    second remote start.
    """
    adapter_name = str(runtime_adapter or "").strip()
    run, duplicate = _prepare_mutation(workspace, body, "answer")
    if duplicate is not None:
        if (
            str(duplicate.get("workflow_state") or "") == "ready_to_generate"
            and not str(duplicate.get("execution_start_id") or "").strip()
        ):
            if not adapter_name:
                raise ValueError("runtime_adapter is required to reserve expert team execution")
            reserved = _transition(
                workspace,
                duplicate,
                "starting",
                "generation_start_reserved",
                _execution_start_reservation_patch(adapter_name),
            )
            return reserved, True
        return duplicate, False
    if str(run.get("workflow_state") or "") not in {
        "collecting_required",
        "collecting_optional",
        "ready_to_generate",
    }:
        raise ValueError("Expert team is not collecting requirements")
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    run = _apply_answers(run, answers, bool(body.get("skip_optional")))
    _record_action(run, body, "answer")
    next_state = _intake_state(run)
    if next_state != "ready_to_generate":
        return _transition(workspace, run, next_state, "questions_answered"), False
    if not adapter_name:
        raise ValueError("runtime_adapter is required to reserve expert team execution")
    reserved = _transition(
        workspace,
        run,
        "starting",
        "generation_start_reserved",
        _execution_start_reservation_patch(adapter_name),
    )
    return reserved, True


def reserve_expert_team_execution_start(
    workspace: Path,
    run_id: str,
    *,
    expected_version: int | None = None,
    runtime_adapter: str = "",
) -> dict:
    """Atomically reserve a ready stage and its planned runtime boundary."""
    with _run_mutation_lock(workspace, run_id):
        run = read_run(workspace, run_id)
        _require_mutable_v2(run)
        if expected_version is not None and int(run.get("version") or 0) != int(expected_version):
            raise ExpertTeamStateConflict(
                "version_conflict",
                f"expert team run version changed: expected {expected_version}, current {int(run.get('version') or 0)}",
                run,
            )
        state = str(run.get("workflow_state") or "")
        if state != "ready_to_generate":
            code = "start_in_progress" if state == "starting" else "stale_state"
            raise ExpertTeamStateConflict(code, "expert team stage is not ready to start", run)
        return _transition(
            workspace,
            run,
            "starting",
            "generation_start_reserved",
            _execution_start_reservation_patch(runtime_adapter),
        )


def mark_expert_team_execution_start_adapter(
    workspace: Path,
    run_id: str,
    *,
    execution_start_id: str,
    runtime_adapter: str,
) -> dict:
    """Persist the selected runtime boundary before any remote side effect."""
    with _run_mutation_lock(workspace, run_id):
        run = read_run(workspace, run_id)
        _require_mutable_v2(run)
        if str(run.get("workflow_state") or "") != "starting":
            raise ExpertTeamStateConflict("stale_state", "expert team start is no longer reserved", run)
        if str(run.get("execution_start_id") or "") != str(execution_start_id or ""):
            raise ExpertTeamStateConflict("stale_start", "expert team start reservation changed", run)
        next_run = deepcopy(run)
        next_run["execution_runtime_adapter"] = str(runtime_adapter or "")
        next_run["updated_at"] = _now()
        return write_run(workspace, _sync_derived(next_run))


@_serialized_run_mutation
def mark_expert_team_execution_start_dispatching(
    workspace: Path,
    run_id: str,
    *,
    execution_start_id: str,
) -> dict:
    """Persist dispatch intent immediately before the first remote side effect."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "starting":
        raise ExpertTeamStateConflict("stale_state", "expert team start is no longer reserved", run)
    if str(run.get("execution_start_id") or "") != str(execution_start_id or ""):
        raise ExpertTeamStateConflict("stale_start", "expert team start reservation changed", run)
    dispatch_state = str(run.get("execution_start_dispatch_state") or "reserved")
    if dispatch_state != "reserved":
        raise ExpertTeamStateConflict(
            "start_dispatch_in_progress",
            "expert team start dispatch was already attempted",
            run,
        )
    next_run = deepcopy(run)
    next_run["execution_start_dispatch_state"] = "dispatching"
    next_run["execution_start_dispatching_at"] = _now()
    next_run["runtime_revision"] = int(next_run.get("runtime_revision") or 0) + 1
    next_run["updated_at"] = _now()
    return write_run(workspace, _sync_derived(next_run))


def mark_expert_team_execution_start_unknown(
    workspace: Path,
    run_id: str,
    *,
    execution_start_id: str,
    message: str,
) -> dict:
    """Keep an ambiguous remote start reserved for same-key reconciliation."""
    with _run_mutation_lock(workspace, run_id):
        run = read_run(workspace, run_id)
        _require_mutable_v2(run)
        if str(run.get("workflow_state") or "") != "starting":
            raise ExpertTeamStateConflict("stale_state", "expert team start is no longer reserved", run)
        if str(run.get("execution_start_id") or "") != str(execution_start_id or ""):
            raise ExpertTeamStateConflict("stale_start", "expert team start reservation changed", run)
        return _transition(
            workspace,
            run,
            "starting",
            "generation_start_unknown",
            {
                "execution_start_deadline_at": 0,
                "last_execution_error": str(message or "启动结果暂未确认，正在与执行侧对账。"),
            },
        )


def mark_expert_team_execution_started(workspace: Path, run_id: str, stream_response: dict | None = None) -> dict:
    with _run_mutation_lock(workspace, run_id):
        run = read_run(workspace, run_id)
        _require_mutable_v2(run)
        response = stream_response or {}
        stream_id = str(response.get("stream_id") or "").strip()
        if not stream_id:
            raise ValueError("Expert team execution cannot start without a stream_id")
        state = str(run.get("workflow_state") or "")
        expected_start_id = str(run.get("execution_start_id") or "")
        provided_start_id = str(response.get("execution_start_id") or "")
        provided_runtime_run_id = str(response.get("runtime_run_id") or stream_id)
        stored_runtime_run_id = str(run.get("execution_runtime_run_id") or "")
        stored_stream_id = str(run.get("execution_stream_id") or "")
        stored_adapter = str(run.get("execution_runtime_adapter") or "")
        provided_adapter = str(response.get("runtime_adapter") or "")
        already_started = (
            state in {"generating", "awaiting_review", "completed"}
            and bool(expected_start_id)
            and provided_start_id == expected_start_id
            and bool(stored_runtime_run_id)
            and provided_runtime_run_id == stored_runtime_run_id
            and (not stored_adapter or not provided_adapter or provided_adapter == stored_adapter)
            and (
                not stored_stream_id
                or stored_stream_id == stream_id
                or stored_stream_id == stored_runtime_run_id
            )
        )
        if already_started:
            # A lease reconciler may bind the same remote run while the original
            # start call is still returning.  Treat that late commit as the same
            # successful side effect and only fill metadata the reconciler could
            # not observe; never increment the execution attempt a second time.
            next_run = deepcopy(run)
            changed = False
            metadata = {
                "execution_stream_id": stream_id,
                "execution_turn_id": str(response.get("turn_id") or ""),
                "execution_runtime_adapter": provided_adapter,
                "execution_message_start_index": response.get("execution_message_start_index"),
                "pending_user_message": str(response.get("pending_user_message") or ""),
            }
            for field, value in metadata.items():
                current = next_run.get(field)
                if value not in (None, "") and current in (None, "", stored_runtime_run_id):
                    if current != value:
                        next_run[field] = value
                        changed = True
            if changed:
                next_run["updated_at"] = _now()
                return write_run(workspace, _sync_derived(next_run))
            return _sync_derived(run)
        if state != "starting":
            raise ExpertTeamStateConflict(
                "stale_state",
                "expert team is no longer reserved for this execution",
                run,
            )
        if not expected_start_id or provided_start_id != expected_start_id:
            raise ExpertTeamStateConflict(
                "stale_start",
                "expert team execution start reservation changed",
                run,
            )
        current = _current_stage(_sync_derived(deepcopy(run)))
        patch = {
            "execution_started_at": _now(),
            "execution_stream_id": stream_id,
            "execution_turn_id": str(response.get("turn_id") or ""),
            "execution_runtime_run_id": provided_runtime_run_id,
            "execution_runtime_adapter": provided_adapter,
            "execution_stage_id": str(current.get("task_id") or current.get("id") or ""),
            "execution_attempt": int(run.get("execution_attempt") or 0) + 1,
            "execution_start_dispatch_state": "started",
            "execution_message_start_index": response.get("execution_message_start_index"),
            "pending_user_message": str(response.get("pending_user_message") or ""),
            "last_execution_error": "",
        }
        return _transition(workspace, run, "generating", "generation_started", patch)


def mark_expert_team_execution_start_failed(
    workspace: Path,
    run_id: str,
    message: str,
    *,
    execution_start_id: str,
    orphan_runtime_run_id: str = "",
    orphan_runtime_adapter: str = "",
    execution_cleanup_status: str = "",
    execution_cleanup_error: str = "",
) -> dict:
    with _run_mutation_lock(workspace, run_id):
        run = read_run(workspace, run_id)
        _require_mutable_v2(run)
        if not str(execution_start_id or "").strip():
            raise ValueError("execution_start_id is required to fail an expert team start")
        if str(run.get("workflow_state") or "") != "starting":
            raise ExpertTeamStateConflict(
                "stale_state",
                "expert team is no longer reserved for this execution start",
                run,
            )
        if str(run.get("execution_start_id") or "") != str(execution_start_id):
            raise ExpertTeamStateConflict(
                "stale_start",
                "expert team execution start reservation changed",
                run,
            )
        has_orphan = bool(str(orphan_runtime_run_id or "").strip())
        cleanup_deadline = time.time() + _CONTROL_RETRY_DEADLINE_SECONDS if has_orphan else 0
        return _transition(
            workspace,
            run,
            "start_failed",
            "generation_start_failed",
            {
                **_clear_execution_patch(),
                "orphan_runtime_run_id": str(orphan_runtime_run_id or ""),
                "orphan_runtime_adapter": str(orphan_runtime_adapter or ""),
                "execution_cleanup_status": str(execution_cleanup_status or ""),
                "execution_cleanup_error": str(execution_cleanup_error or ""),
                "execution_cleanup_retry_count": 0,
                "execution_cleanup_next_retry_at": 0,
                "execution_cleanup_deadline_at": cleanup_deadline,
                "last_execution_error": str(message or "当前阶段启动失败，请重新尝试。"),
            },
        )


@_serialized_run_mutation
def complete_expert_team_orphan_cleanup(
    workspace: Path,
    run_id: str,
    *,
    orphan_runtime_run_id: str,
    outcome: str,
) -> dict:
    """Close a persisted orphan cleanup only after observing terminal truth."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    expected = str(orphan_runtime_run_id or "").strip()
    if not expected or str(run.get("orphan_runtime_run_id") or "").strip() != expected:
        raise ExpertTeamStateConflict(
            "stale_orphan_cleanup",
            "expert team orphan cleanup identity changed",
            run,
        )
    next_run = deepcopy(run)
    next_run.update(
        {
            "orphan_runtime_run_id": "",
            "orphan_runtime_adapter": "",
            "execution_cleanup_status": str(outcome or "confirmed"),
            "execution_cleanup_error": "",
            "execution_cleanup_retry_count": int(run.get("execution_cleanup_retry_count") or 0),
            "execution_cleanup_next_retry_at": 0,
            "execution_cleanup_deadline_at": 0,
            "updated_at": _now(),
        }
    )
    return write_run(workspace, _sync_derived(next_run))


@_serialized_run_mutation
def record_expert_team_orphan_cleanup_attempt(
    workspace: Path,
    run_id: str,
    *,
    orphan_runtime_run_id: str,
    message: str = "",
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("orphan_runtime_run_id") or "") != str(orphan_runtime_run_id or ""):
        raise ExpertTeamStateConflict("stale_orphan_cleanup", "expert team orphan cleanup changed", run)
    now = time.time()
    deadline = float(run.get("execution_cleanup_deadline_at") or 0)
    if deadline <= now:
        deadline = now + _CONTROL_RETRY_DEADLINE_SECONDS
    next_run = deepcopy(run)
    next_run.update(
        {
            "execution_cleanup_status": "cancel_requested",
            "execution_cleanup_error": str(message or ""),
            "execution_cleanup_retry_count": int(run.get("execution_cleanup_retry_count") or 0) + 1,
            "execution_cleanup_next_retry_at": now + _CONTROL_RETRY_DELAY_SECONDS,
            "execution_cleanup_deadline_at": deadline,
            "runtime_revision": int(run.get("runtime_revision") or 0) + 1,
            "updated_at": _now(),
        }
    )
    return write_run(workspace, _sync_derived(next_run))


@_serialized_run_mutation
def require_expert_team_orphan_cleanup_retry(
    workspace: Path,
    run_id: str,
    *,
    orphan_runtime_run_id: str,
    message: str,
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("orphan_runtime_run_id") or "") != str(orphan_runtime_run_id or ""):
        raise ExpertTeamStateConflict("stale_orphan_cleanup", "expert team orphan cleanup changed", run)
    next_run = deepcopy(run)
    next_run.update(
        {
            "execution_cleanup_status": "retry_required",
            "execution_cleanup_error": str(message or "远程清理尚未确认，请手动重试。"),
            "execution_cleanup_next_retry_at": 0,
            "runtime_revision": int(run.get("runtime_revision") or 0) + 1,
            "updated_at": _now(),
        }
    )
    return write_run(workspace, _sync_derived(next_run))


@_serialized_run_mutation
def reconcile_expert_team_run(workspace: Path, run_id: str) -> dict:
    """Persist recoverable states whose external transition lost its final write."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    return _sync_derived(_refresh_artifact_existence(workspace, run))


@_serialized_run_mutation
def finalize_expert_team_cancellation(
    workspace: Path,
    run_id: str,
    *,
    cancel_request_id: str,
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "cancelling":
        raise ExpertTeamStateConflict("stale_state", "expert team is not cancelling", run)
    if str(run.get("cancel_request_id") or "") != str(cancel_request_id or ""):
        raise ExpertTeamStateConflict("stale_cancel", "expert team cancellation request changed", run)
    return _transition(workspace, run, "cancelled", "generation_cancelled")


@_serialized_run_mutation
def fail_expert_team_cancellation(
    workspace: Path,
    run_id: str,
    *,
    cancel_request_id: str,
    message: str,
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "cancelling":
        raise ExpertTeamStateConflict("stale_state", "expert team is not cancelling", run)
    if str(run.get("cancel_request_id") or "") != str(cancel_request_id or ""):
        raise ExpertTeamStateConflict("stale_cancel", "expert team cancellation request changed", run)
    return _transition(
        workspace,
        run,
        "failed",
        "generation_failed",
        {"last_execution_error": str(message or "远程专家团执行失败。")},
    )


def _cancel_retry_schedule(run: dict) -> dict:
    now = time.time()
    deadline = float(run.get("cancel_deadline_at") or 0)
    if deadline <= now:
        deadline = now + _CONTROL_RETRY_DEADLINE_SECONDS
    return {
        "cancel_retry_count": int(run.get("cancel_retry_count") or 0) + 1,
        "cancel_next_retry_at": now + _CONTROL_RETRY_DELAY_SECONDS,
        "cancel_deadline_at": deadline,
    }


@_serialized_run_mutation
def require_expert_team_cancellation_retry(
    workspace: Path,
    run_id: str,
    *,
    cancel_request_id: str,
    message: str,
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "cancelling":
        raise ExpertTeamStateConflict("stale_state", "expert team is not cancelling", run)
    if str(run.get("cancel_request_id") or "") != str(cancel_request_id or ""):
        raise ExpertTeamStateConflict("stale_cancel", "expert team cancellation request changed", run)
    return _transition(
        workspace,
        run,
        "cancelling",
        "generation_cancel_unknown",
        {
            "cancel_runtime_accepted": False,
            "cancel_outcome": "unknown",
            "cancel_next_retry_at": 0,
            "last_execution_error": str(message or "停止尚未确认，请手动重试。"),
        },
    )


@_serialized_run_mutation
def bind_expert_team_cancellation_runtime(
    workspace: Path,
    run_id: str,
    *,
    cancel_request_id: str,
    execution_start_id: str,
    runtime_run_id: str,
    runtime_adapter: str,
) -> dict:
    """Attach a remotely discovered start to its pending cancellation intent."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "cancelling":
        raise ExpertTeamStateConflict("stale_state", "expert team is not cancelling", run)
    if str(run.get("cancel_request_id") or "") != str(cancel_request_id or ""):
        raise ExpertTeamStateConflict("stale_cancel", "expert team cancellation request changed", run)
    if str(run.get("execution_start_id") or "") != str(execution_start_id or ""):
        raise ExpertTeamStateConflict("stale_start", "expert team start reservation changed", run)
    remote_id = str(runtime_run_id or "").strip()
    if not remote_id:
        raise ValueError("runtime_run_id is required to bind expert team cancellation")
    current_remote_id = str(run.get("execution_runtime_run_id") or "").strip()
    if current_remote_id and current_remote_id != remote_id:
        raise ExpertTeamStateConflict("runtime_identity_mismatch", "expert team runtime run changed", run)
    current = _current_stage(_sync_derived(deepcopy(run)))
    next_run = deepcopy(run)
    next_run.update(
        {
            "execution_started_at": str(run.get("execution_started_at") or _now()),
            "execution_stream_id": str(run.get("execution_stream_id") or remote_id),
            "execution_runtime_run_id": remote_id,
            "execution_runtime_adapter": str(runtime_adapter or run.get("execution_runtime_adapter") or ""),
            "execution_stage_id": str(
                run.get("execution_stage_id") or current.get("task_id") or current.get("id") or ""
            ),
            "execution_attempt": int(run.get("execution_attempt") or 0) + (0 if current_remote_id else 1),
            "cancel_next_retry_at": 0 if not current_remote_id else run.get("cancel_next_retry_at", 0),
            "runtime_revision": int(run.get("runtime_revision") or 0) + 1,
            "updated_at": _now(),
        }
    )
    return write_run(workspace, _sync_derived(next_run))


@_serialized_run_mutation
def reconcile_expert_team_cancellation(
    workspace: Path,
    run_id: str,
    *,
    cancel_request_id: str,
    outcome: str,
    message: str = "",
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "cancelling":
        raise ExpertTeamStateConflict("stale_state", "expert team is not cancelling", run)
    if str(run.get("cancel_request_id") or "") != str(cancel_request_id or ""):
        raise ExpertTeamStateConflict("stale_cancel", "expert team cancellation request changed", run)
    normalized = str(outcome or "unknown").lower()
    if normalized == "accepted":
        return _transition(
            workspace,
            run,
            "cancelling",
            "generation_cancel_accepted",
            {
                "cancel_runtime_accepted": True,
                "cancel_outcome": "accepted",
                **_cancel_retry_schedule(run),
            },
        )
    if normalized == "rejected":
        previous_state = str(run.get("cancel_previous_state") or "generating")
        return _transition(
            workspace,
            run,
            previous_state,
            "generation_cancel_rejected",
            {
                "cancel_request_id": "",
                "cancel_request_fingerprint": "",
                "cancel_requested_at": "",
                "cancel_previous_state": "",
                "cancel_runtime_accepted": False,
                "cancel_outcome": "rejected",
                "cancel_retry_count": 0,
                "cancel_next_retry_at": 0,
                "cancel_deadline_at": 0,
                "last_execution_error": str(message or "runtime rejected expert team cancellation"),
            },
        )
    return _transition(
        workspace,
        run,
        "cancelling",
        "generation_cancel_unknown",
        {
            "cancel_runtime_accepted": False,
            "cancel_outcome": "unknown",
            **_cancel_retry_schedule(run),
            "last_execution_error": str(message or "停止请求状态暂未确认，正在与执行侧对账。"),
        },
    )


@_serialized_run_mutation
def restore_expert_team_after_cancel_completion(
    workspace: Path,
    run_id: str,
    *,
    cancel_request_id: str,
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "cancelling":
        raise ExpertTeamStateConflict("stale_state", "expert team is not cancelling", run)
    if str(run.get("cancel_request_id") or "") != str(cancel_request_id or ""):
        raise ExpertTeamStateConflict("stale_cancel", "expert team cancellation request changed", run)
    previous_state = str(run.get("cancel_previous_state") or "generating")
    if previous_state == "starting" and str(run.get("execution_runtime_run_id") or "").strip():
        previous_state = "generating"
    return _transition(
        workspace,
        run,
        previous_state,
        "generation_cancel_superseded_by_completion",
        {
            "cancel_request_id": "",
            "cancel_request_fingerprint": "",
            "cancel_requested_at": "",
            "cancel_previous_state": "",
            "cancel_runtime_accepted": False,
            "cancel_outcome": "completed",
            "cancel_retry_count": 0,
            "cancel_next_retry_at": 0,
            "cancel_deadline_at": 0,
            "last_execution_error": "",
        },
    )


@_serialized_body_mutation
def request_expert_team_stage_input(workspace: Path, body: dict) -> dict:
    run, duplicate = _prepare_mutation(workspace, body, "request_stage_input")
    if duplicate is not None:
        return duplicate
    if str(run.get("workflow_state") or "") not in {"ready_to_generate", "generating", "revising"}:
        raise ValueError("Expert team cannot request stage input in current state")
    synced = _sync_derived(deepcopy(run))
    current = synced.get("current_stage") if isinstance(synced.get("current_stage"), dict) else {}
    pending_input = {
        "id": str(body.get("input_id") or ("stage-input-" + uuid.uuid4().hex[:8])),
        "question": str(body.get("question") or "当前阶段需要你确认后继续生成。").strip(),
        "description": str(body.get("description") or "").strip(),
        "options": [str(option) for option in body.get("options") or []],
        "required": body.get("required", True) is not False,
        "stage_id": str(current.get("task_id") or current.get("id") or ""),
        "worker_id": str(current.get("worker_id") or ""),
        "created_at": _now(),
    }
    if not pending_input["question"]:
        pending_input["question"] = "当前阶段需要你确认后继续生成。"
    _record_action(run, body, "request_stage_input")
    return _transition(
        workspace,
        run,
        "awaiting_stage_input",
        "stage_input_requested",
        {
            "pending_input": pending_input,
            "execution_status": "paused",
            "last_execution_error": "",
        },
    )


@_serialized_body_mutation
def submit_expert_team_stage_input(workspace: Path, body: dict) -> dict:
    run, duplicate = _prepare_mutation(workspace, body, "submit_stage_input")
    if duplicate is not None:
        return duplicate
    if str(run.get("workflow_state") or "") != "awaiting_stage_input":
        raise ValueError("Expert team is not awaiting stage input")
    pending = run.get("pending_input") if isinstance(run.get("pending_input"), dict) else {}
    input_id = str(body.get("input_id") or "").strip()
    if int(run.get("schema_version") or 0) >= 2 and not input_id:
        raise ValueError("input_id is required for expert team stage input")
    if input_id and input_id != str(pending.get("id") or ""):
        raise ExpertTeamStateConflict("stale_input", "expert team stage input changed", run)
    answer = str(body.get("answer") or "").strip()
    note = str(body.get("note") or "").strip()
    selected_option = str(body.get("selected_option") or "").strip()
    if not answer and selected_option:
        answer = selected_option
    if pending.get("required", True) is not False and not answer and not note:
        raise ValueError("Stage input answer is required")
    rows = [deepcopy(row) for row in run.get("stage_inputs") or [] if isinstance(row, dict)]
    rows.append(
        {
            "input_id": str(pending.get("id") or ""),
            "stage_id": str(pending.get("stage_id") or ""),
            "worker_id": str(pending.get("worker_id") or ""),
            "question": str(pending.get("question") or ""),
            "answer": answer,
            "note": note,
            "answered_at": _now(),
        }
    )
    _record_action(run, body, "submit_stage_input")
    return _transition(
        workspace,
        run,
        "ready_to_generate",
        "stage_input_answered",
        {
            **_clear_execution_patch(),
            "pending_input": {},
            "stage_inputs": rows,
        },
    )


@_serialized_run_mutation
def mark_expert_team_execution_complete(workspace: Path, run_id: str, delivery: dict | None = None) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    state = str(run.get("workflow_state") or "")
    expected_stream_id = str(run.get("execution_stream_id") or "").strip()
    delivered_stream_id = str(
        (delivery or {}).get("stream_id") or (delivery or {}).get("execution_stream_id") or ""
    ).strip()
    expected_stage_id = str(run.get("execution_stage_id") or "").strip()
    delivered_stage_id = str((delivery or {}).get("stage_id") or "").strip()
    expected_attempt = int(run.get("execution_attempt") or 0)
    try:
        delivered_attempt = int((delivery or {}).get("attempt"))
    except (TypeError, ValueError):
        delivered_attempt = -1
    if state not in {"generating", "result_unverified"}:
        code = "missing_stream" if state == "ready_to_generate" and not expected_stream_id else "stale_state"
        raise ExpertTeamStateConflict(code, "expert team execution is not generating", run)
    if not expected_stream_id:
        raise ExpertTeamStateConflict("missing_stream", "expert team execution has no active stream", run)
    if delivered_stream_id != expected_stream_id:
        code = "stale_stream" if delivered_stream_id else "missing_stream"
        raise ExpertTeamStateConflict(
            code,
            "expert team result does not belong to the active execution stream",
            run,
        )
    if not delivered_stage_id or delivered_stage_id != expected_stage_id:
        raise ExpertTeamStateConflict(
            "stale_stage" if delivered_stage_id else "missing_stage",
            "expert team result does not belong to the active stage",
            run,
        )
    if delivered_attempt != expected_attempt:
        raise ExpertTeamStateConflict(
            "stale_attempt" if delivered_attempt >= 0 else "missing_attempt",
            "expert team result does not belong to the active attempt",
            run,
        )
    business_context = business_context_for_run(run)
    output = structured_output_from_delivery(delivery or {}, business_context)
    current = _current_stage(_sync_derived(deepcopy(run)))
    output["task_id"] = current.get("task_id") or ""
    output["stage_id"] = current.get("task_id") or ""
    output["worker_id"] = current.get("worker_id") or ""
    output["phase"] = current.get("phase") or ""
    output["worker_name"] = current.get("worker_name") or ""
    output["stream_id"] = delivered_stream_id
    output["attempt"] = delivered_attempt
    if str(current.get("task_id") or "") == "plan":
        output["title"] = current.get("title") or "专家团计划"
        output["visible_title"] = current.get("title") or "专家团计划"
    material_type = str(business_context.get("material_type") or "office_material")
    task_id = str(current.get("task_id") or "")
    stage_attempt = 1 + sum(
        1
        for item in run.get("stage_outputs") or []
        if isinstance(item, dict)
        and str(item.get("task_id") or item.get("stage_id") or "") == task_id
    )
    output["stage_attempt"] = stage_attempt
    validation = validate_stage_output(output.get("content") or "", material_type, task_id, str(run.get("team_id") or ""))
    stage_result = stage_result_from_output(output, validation)
    run.setdefault("stage_outputs", [])
    run["stage_outputs"].append(output)
    run.setdefault("stage_results", [])
    run["stage_results"].append(stage_result)
    run["stage_result"] = stage_result
    run["review_items"] = stage_result.get("review_items") or []
    run.setdefault("artifacts", [])
    if output.get("kind") == "chat":
        _upsert_artifact(
            run,
            _chat_artifact(output, stage=task_id, attempt=stage_attempt),
        )
    if validation.get("status") != "pass":
        run["last_validation_error"] = str(validation.get("message") or "草稿未通过校验")
        run["validation"] = validation
        return _transition(workspace, run, "generated_invalid", "generation_invalid")
    if is_rich_draft_required(material_type, task_id, str(run.get("team_id") or "")):
        try:
            package = build_rich_draft_package(workspace, run, output)
        except (RichDraftPackagingError, FileNotFoundError, OSError, ValueError) as exc:
            validation = {
                "status": "rewrite_required",
                "violations": [],
                "missing_sections": ["可追溯富内容初稿包"],
                "message": f"富内容初稿打包失败：{exc}",
            }
            stage_result["validation"] = validation
            run["validation"] = validation
            run["last_validation_error"] = validation["message"]
            return _transition(workspace, run, "generated_invalid", "generation_invalid")
        output["rich_draft"] = package
        stage_result["rich_draft"] = package
        _upsert_artifact(
            run,
            _rich_draft_artifact(package, stage=task_id, attempt=stage_attempt),
        )
    if is_final_delivery_stage(run, task_id):
        try:
            with delivery_attempt_lock(
                workspace,
                str(run.get("run_id") or ""),
                task_id,
                stage_attempt,
            ):
                document_delivery = build_final_document_delivery(
                    workspace,
                    run,
                    output,
                    material_type=material_type,
                )
        except (FinalDocumentDeliveryError, FileNotFoundError, OSError, ValueError) as exc:
            validation = {
                "status": "rewrite_required",
                "violations": [],
                "missing_sections": ["可打开最终 DOCX"],
                "message": f"DOCX 交付生成失败：{exc}",
            }
            stage_result["validation"] = validation
            run["validation"] = validation
            run["last_validation_error"] = validation["message"]
            return _transition(workspace, run, "generated_invalid", "generation_invalid")
        output["document_delivery"] = document_delivery
        stage_result["document_delivery"] = document_delivery
        for artifact in document_delivery.get("artifacts") or []:
            if isinstance(artifact, dict):
                _upsert_artifact(run, artifact)
    run["last_validation_error"] = ""
    run["validation"] = validation
    return _transition(workspace, run, "awaiting_review", "generation_completed")


def mark_content_expert_team_execution_complete(workspace: Path, run_id: str, delivery: dict | None = None) -> dict:
    return mark_expert_team_execution_complete(workspace, run_id, delivery)


@_serialized_run_mutation
def record_expert_team_execution_observation(
    workspace: Path,
    run_id: str,
    *,
    runtime_run_id: str,
    stream_id: str,
    stage_id: str,
    attempt: int,
    cursor: str | None,
    observations: list[dict],
    last_event_id: str | None = None,
    delivered_last_event_id: str | None = None,
    delivered_through_sequence: int | None = None,
    expected_cursor=_EXPECTED_CURSOR_UNSET,
) -> dict:
    """Durably append identity-checked public Runner output without changing user CAS version."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") not in {"generating", "cancelling"}:
        raise ExpertTeamStateConflict("stale_state", "expert team execution is no longer observable", run)
    if str(run.get("execution_runtime_run_id") or "") != str(runtime_run_id or ""):
        raise ExpertTeamStateConflict("runtime_identity_mismatch", "expert team runtime run changed", run)
    if str(run.get("execution_stream_id") or "") != str(stream_id or ""):
        raise ExpertTeamStateConflict("stale_stream", "expert team execution stream changed", run)
    if str(run.get("execution_stage_id") or "") != str(stage_id or ""):
        raise ExpertTeamStateConflict("stale_stage", "expert team execution stage changed", run)
    if int(run.get("execution_attempt") or 0) != int(attempt):
        raise ExpertTeamStateConflict("stale_attempt", "expert team execution attempt changed", run)
    cursor_values = [run.get("execution_cursor"), cursor]
    if expected_cursor is not _EXPECTED_CURSOR_UNSET:
        cursor_values.append(expected_cursor)
    if any(
        len(str(value or "").encode("utf-8")) > int(_OBSERVATION_CURSOR_MAX_BYTES)
        for value in cursor_values
    ):
        raise ExpertTeamStateConflict(
            "runtime_observation_limit",
            "runtime observation cursor exceeded the durable metadata limit",
            run,
        )
    if expected_cursor is not _EXPECTED_CURSOR_UNSET and str(run.get("execution_cursor") or "") != str(
        expected_cursor or ""
    ):
        raise ExpertTeamStateConflict(
            "stale_observation_page",
            "expert team observation page was requested from a stale cursor",
            run,
        )
    for marker in (last_event_id, delivered_last_event_id):
        if len(str(marker or "").strip().encode("utf-8")) > int(
            _OBSERVATION_EVENT_ID_MAX_BYTES
        ):
            raise ExpertTeamStateConflict(
                "runtime_observation_limit",
                "runtime delivery marker exceeded the durable observation limit",
                run,
            )

    next_run = deepcopy(run)
    seen = [str(item) for item in next_run.get("execution_seen_event_ids") or [] if str(item)]
    seen_set = set(seen)
    ledger = [
        deepcopy(item)
        for item in next_run.get("execution_public_observations") or []
        if isinstance(item, dict)
    ]
    if not ledger and str(next_run.get("execution_public_output_buffer") or ""):
        ledger.append(
            {
                "event_id": "",
                "sequence": None,
                "kind": "delta",
                "text": str(next_run.get("execution_public_output_buffer") or ""),
                "arrival": -1,
                "legacy": True,
            }
        )
    arrival = max((int(item.get("arrival") or 0) for item in ledger), default=0)
    sequence_index = {
        int(item.get("sequence")): item
        for item in ledger
        if item.get("sequence") is not None
    }
    event_id_index = {}
    for item in ledger:
        item_event_ids = [str(item.get("event_id") or "").strip()]
        item_event_ids.extend(
            str(value or "").strip()
            for value in item.get("event_ids") or []
        )
        for item_event_id in item_event_ids:
            if item_event_id:
                event_id_index[item_event_id] = item
    indexed_observations = list(enumerate(observations or []))

    def _observation_order(item):
        index, observation = item
        try:
            return (0, int((observation or {}).get("sequence")))
        except (TypeError, ValueError):
            return (1, index)

    for _index, observation in sorted(indexed_observations, key=_observation_order):
        if not isinstance(observation, dict):
            continue
        event_id = str(observation.get("event_id") or "").strip()
        text = str(observation.get("text") or "")
        kind = str(observation.get("kind") or "delta")
        try:
            sequence = int(observation.get("sequence"))
        except (TypeError, ValueError):
            sequence = None
        if not event_id and sequence is None:
            raise ExpertTeamStateConflict(
                "runtime_protocol_error",
                "public runtime events require an event_id or sequence",
                run,
            )
        if len(event_id.encode("utf-8")) > int(_OBSERVATION_EVENT_ID_MAX_BYTES):
            raise ExpertTeamStateConflict(
                "runtime_observation_limit",
                "runtime event identity exceeded the durable observation limit",
                run,
            )
        if event_id and event_id in seen_set:
            existing = event_id_index.get(event_id)
            if existing is None:
                raise ExpertTeamStateConflict(
                    "runtime_protocol_error",
                    "runtime reused an event identity whose content cannot be verified",
                    run,
                )
            try:
                existing_sequence = int(existing.get("sequence"))
            except (TypeError, ValueError):
                existing_sequence = None
            if (
                existing_sequence != sequence
                or str(existing.get("kind") or "delta") != kind
                or str(existing.get("text") or "") != text
            ):
                raise ExpertTeamStateConflict(
                    "runtime_protocol_error",
                    "runtime reused an event identity with different public content",
                    run,
                )
            continue
        if sequence is not None and sequence in sequence_index:
            existing = sequence_index[sequence]
            if str(existing.get("kind") or "delta") != kind or str(existing.get("text") or "") != text:
                raise ExpertTeamStateConflict(
                    "runtime_protocol_error",
                    "runtime reused an event sequence with different public content",
                    run,
                )
            if event_id:
                aliases = [str(value or "").strip() for value in existing.get("event_ids") or []]
                if event_id != str(existing.get("event_id") or "").strip() and event_id not in aliases:
                    aliases.append(event_id)
                    existing["event_ids"] = aliases
                seen.append(event_id)
                seen_set.add(event_id)
                event_id_index[event_id] = existing
            continue
        arrival += 1
        if text:
            ledger_item = {
                "event_id": event_id,
                "sequence": sequence,
                "kind": kind,
                "text": text,
                "arrival": arrival,
            }
            ledger.append(ledger_item)
            if sequence is not None:
                sequence_index[sequence] = ledger_item
            if event_id:
                event_id_index[event_id] = ledger_item
        if event_id:
            seen.append(event_id)
            seen_set.add(event_id)

    durable_ledger_bytes = len(
        json.dumps(
            {
                "observations": ledger,
                "seen_event_ids": seen,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    if max(len(ledger), len(seen)) > int(
        _OBSERVATION_LEDGER_MAX_EVENTS
    ) or durable_ledger_bytes > int(_OBSERVATION_LEDGER_MAX_BYTES):
        raise ExpertTeamStateConflict(
            "runtime_observation_limit",
            "runtime public output exceeded the durable observation limit",
            run,
        )

    def _ledger_order(item):
        if item.get("legacy"):
            return (-1, -1, int(item.get("arrival") or -1))
        sequence = item.get("sequence")
        if sequence is None:
            return (1, 0, int(item.get("arrival") or 0))
        return (0, int(sequence), int(item.get("arrival") or 0))

    buffer = ""
    for item in sorted(ledger, key=_ledger_order):
        text = str(item.get("text") or "")
        if not text:
            continue
        if str(item.get("kind") or "delta") == "final":
            buffer = text.strip()
        else:
            buffer += text

    page_sequences = [
        int(item.get("sequence"))
        for item in observations or []
        if isinstance(item, dict) and item.get("sequence") is not None
    ]
    try:
        page_delivered_sequence = int(delivered_through_sequence)
    except (TypeError, ValueError):
        page_delivered_sequence = max(page_sequences) if page_sequences else None
    try:
        current_delivered_sequence = int(next_run.get("execution_delivered_through_sequence"))
    except (TypeError, ValueError):
        current_delivered_sequence = None
    page_is_forward = (
        page_delivered_sequence is None
        or current_delivered_sequence is None
        or page_delivered_sequence >= current_delivered_sequence
    )
    if page_is_forward:
        if cursor is not None:
            next_run["execution_cursor"] = str(cursor)
        delivered_id = (
            str(delivered_last_event_id or "").strip()
            or str(last_event_id or "").strip()
        )
        if delivered_id:
            next_run["execution_delivered_last_event_id"] = delivered_id
            next_run["execution_last_event_id"] = delivered_id
        if page_delivered_sequence is not None:
            next_run["execution_delivered_through_sequence"] = page_delivered_sequence
    next_run["execution_public_output_buffer"] = buffer
    next_run["execution_public_observations"] = ledger
    next_run["execution_seen_event_ids"] = seen
    next_run["runtime_revision"] = int(next_run.get("runtime_revision") or 0) + 1
    next_run["updated_at"] = _now()
    return write_run(workspace, _sync_derived(next_run))


@_serialized_body_mutation
def approve_expert_team_stage(workspace: Path, body: dict) -> dict:
    run, duplicate = _prepare_mutation(workspace, body, "approve_stage")
    if duplicate is not None:
        return duplicate
    run = _refresh_artifact_existence(workspace, run)
    if str(run.get("workflow_state") or "") != "awaiting_review":
        raise ValueError("Expert team stage is not awaiting review")
    index = int(run.get("current_stage_index") or 0)
    total = len(run.get("_tasks_template") or [])
    authoritative_stage = _authoritative_stage_for_mutation(run)
    current_task_id = str(authoritative_stage.get("task_id") or "")
    if is_final_delivery_stage(run, current_task_id):
        try:
            delivery_attempt, _document_delivery = _authoritative_delivery_attempt(run, current_task_id)
        except (DeliveryIntegrityError, OSError, ValueError) as exc:
            message = f"最终交付包身份、路径或摘要绑定异常，请重新生成 DOCX 交付包后再复核。 {exc}"
            gate = {
                "status": "regeneration_required",
                "required_action": "regenerate_delivery",
                "checked_at": _now(),
                "validator_ok": False,
                "validator_code": "corrupt_delivery_artifacts",
                "delivery_dir": "",
                "quality_report_path": "",
                "quality_report": {},
                "failures": [str(exc)],
            }
            _set_delivery_gate_validation(run, gate, message)
            return _transition(workspace, run, "generated_invalid", "delivery_validation_failed")
        with delivery_attempt_lock(
            workspace,
            str(run.get("run_id") or ""),
            current_task_id,
            delivery_attempt,
        ):
            try:
                delivery_context = _canonical_final_delivery_context(workspace, run, current_task_id)
            except (DeliveryIntegrityError, OSError, ValueError) as exc:
                message = f"最终交付包身份、路径或摘要绑定异常，请重新生成 DOCX 交付包后再复核。 {exc}"
                gate = {
                    "status": "regeneration_required",
                    "required_action": "regenerate_delivery",
                    "checked_at": _now(),
                    "validator_ok": False,
                    "validator_code": "corrupt_delivery_artifacts",
                    "delivery_dir": "",
                    "quality_report_path": "",
                    "quality_report": {},
                    "failures": [str(exc)],
                }
                _set_delivery_gate_validation(run, gate, message)
                return _transition(workspace, run, "generated_invalid", "delivery_validation_failed")
            gate_status, gate, message = _validate_latest_final_delivery(
                workspace,
                delivery_context,
                write_report=True,
            )
            if gate_status == "passed":
                try:
                    snapshot_after_refresh = _current_final_delivery_snapshot(
                        workspace,
                        run,
                        current_task_id,
                        delivery_attempt,
                        delivery_context,
                    )
                except (DeliveryIntegrityError, OSError, ValueError) as exc:
                    gate_status, gate, message = _changed_delivery_gate(
                        workspace,
                        delivery_context,
                        gate,
                        exc,
                    )
                else:
                    second_status, second_gate, second_message = _validate_latest_final_delivery(
                        workspace,
                        delivery_context,
                        write_report=False,
                    )
                    gate_status, gate, message = second_status, second_gate, second_message
                    if gate_status == "passed":
                        try:
                            snapshot_after_readonly_validation = _current_final_delivery_snapshot(
                                workspace,
                                run,
                                current_task_id,
                                delivery_attempt,
                                delivery_context,
                            )
                            if snapshot_after_readonly_validation != snapshot_after_refresh:
                                raise DeliveryIntegrityError(
                                    "final delivery snapshot changed between refresh and read-only validation"
                                )
                        except (DeliveryIntegrityError, OSError, ValueError) as exc:
                            gate_status, gate, message = _changed_delivery_gate(
                                workspace,
                                delivery_context,
                                gate,
                                exc,
                            )
                        else:
                            gate["digest_set"] = snapshot_after_readonly_validation
            _set_delivery_gate_validation(run, gate, message)
            if gate_status == "office_acceptance_required":
                return _transition(workspace, run, "awaiting_review", "office_acceptance_required")
            if gate_status != "passed":
                return _transition(workspace, run, "generated_invalid", "delivery_validation_failed")
            try:
                snapshot_before_completion = _current_final_delivery_snapshot(
                    workspace,
                    run,
                    current_task_id,
                    delivery_attempt,
                    delivery_context,
                )
                if snapshot_before_completion != gate.get("digest_set"):
                    raise DeliveryIntegrityError("final delivery changed before completion was persisted")
            except (DeliveryIntegrityError, OSError, ValueError) as exc:
                gate_status, gate, message = _changed_delivery_gate(
                    workspace,
                    delivery_context,
                    gate,
                    exc,
                )
                _set_delivery_gate_validation(run, gate, message)
                return _transition(workspace, run, "generated_invalid", "delivery_validation_failed")
            run["last_validation_error"] = ""
            run["completion_integrity"] = {
                "status": "valid",
                "checked_at": _now(),
                "message": "已完成交付与审批时的全量摘要快照一致。",
            }
            outputs = [deepcopy(output) for output in run.get("stage_outputs") or [] if isinstance(output, dict)]
            for output in reversed(outputs):
                if not current_task_id or str(output.get("task_id") or "") == current_task_id:
                    output["status"] = "approved"
                    output["approved_at"] = _now()
                    break
            run["stage_outputs"] = outputs
            run["current_stage_index"] = index + 1
            _record_action(run, body, "approve_stage")
            return _transition(workspace, run, "completed", "stage_approved")
    outputs = [deepcopy(output) for output in run.get("stage_outputs") or [] if isinstance(output, dict)]
    for output in reversed(outputs):
        if not current_task_id or str(output.get("task_id") or "") == current_task_id:
            output["status"] = "approved"
            output["approved_at"] = _now()
            break
    run["stage_outputs"] = outputs
    run["current_stage_index"] = index + 1
    _record_action(run, body, "approve_stage")
    if index + 1 >= total:
        return _transition(workspace, run, "completed", "stage_approved")
    return _transition(workspace, run, "ready_to_generate", "stage_approved", _clear_execution_patch())


@_serialized_body_mutation
def request_expert_team_stage_revision(workspace: Path, body: dict) -> dict:
    run, duplicate = _prepare_mutation(workspace, body, "revise_stage")
    if duplicate is not None:
        return duplicate
    if str(run.get("workflow_state") or "") != "awaiting_review":
        raise ExpertTeamStateConflict("stale_state", "expert team stage is not awaiting review", run)
    feedback = str(body.get("feedback") or "").strip()
    if not feedback:
        raise ValueError("Expert team revision feedback is required")
    current = _current_stage(_sync_derived(deepcopy(run)))
    stage_id = str(current.get("task_id") or current.get("id") or "")
    feedback_entry = {"stage_id": stage_id, "feedback": feedback, "at": _now()}
    run.setdefault("revision_feedback", []).append(feedback_entry)
    outputs = [deepcopy(output) for output in run.get("stage_outputs") or [] if isinstance(output, dict)]
    for output in reversed(outputs):
        if str(output.get("task_id") or output.get("stage_id") or "") == stage_id:
            output.setdefault("feedback_history", []).append(deepcopy(feedback_entry))
            break
    run["stage_outputs"] = outputs
    _record_action(run, body, "revise_stage")
    return _transition(
        workspace,
        run,
        "ready_to_generate",
        "stage_revision_requested",
        _clear_execution_patch(),
    )


@_serialized_body_mutation
def resume_expert_team(workspace: Path, body: dict) -> dict:
    run, duplicate = _prepare_mutation(workspace, body, "resume")
    if duplicate is not None:
        return duplicate
    state = str(run.get("workflow_state") or "")
    if state in TERMINAL_STATES:
        raise ExpertTeamStateConflict("terminal_state", "terminal expert team runs cannot resume", run)
    if str(run.get("orphan_runtime_run_id") or "").strip() and str(
        run.get("execution_cleanup_status") or ""
    ).strip().lower() in {"pending", "unknown", "cancel_requested", "retry_required"}:
        raise ExpertTeamStateConflict(
            "orphan_cleanup_pending",
            "previous expert team runtime cleanup is not yet confirmed",
            run,
        )
    if state not in {"ready_to_generate", "start_failed", "generation_failed", "result_unverified", "generated_invalid"}:
        raise ExpertTeamStateConflict("stale_state", "expert team run is not resumable", run)
    _record_action(run, body, "resume")
    return _transition(workspace, run, "ready_to_generate", "generation_resumed", _clear_execution_patch())


@_serialized_run_mutation
def fail_expert_team_execution(
    workspace: Path,
    run_id: str,
    message: str,
    *,
    stream_id: str,
) -> dict:
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "generating":
        raise ExpertTeamStateConflict("stale_state", "expert team execution is not generating", run)
    expected_stream_id = str(run.get("execution_stream_id") or "")
    if not stream_id or str(stream_id) != expected_stream_id:
        raise ExpertTeamStateConflict("stale_stream", "expert team execution stream changed", run)
    return _transition(
        workspace,
        run,
        "generation_failed",
        "generation_failed",
        {
            **_clear_execution_patch(),
            "last_execution_error": str(message or "未检测到生成结果，请重新尝试。"),
        },
    )


@_serialized_run_mutation
def mark_expert_team_result_unverified(
    workspace: Path,
    run_id: str,
    message: str,
    *,
    stream_id: str,
) -> dict:
    """Keep execution identity when a completed stream cannot yet be bound."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "generating":
        raise ExpertTeamStateConflict("stale_state", "expert team execution is not generating", run)
    expected_stream_id = str(run.get("execution_stream_id") or "")
    if not stream_id or str(stream_id) != expected_stream_id:
        raise ExpertTeamStateConflict("stale_stream", "expert team execution stream changed", run)
    return _transition(
        workspace,
        run,
        "result_unverified",
        "generation_result_unverified",
        {
            "last_execution_error": str(message or "结果绑定证据尚未闭环，请重新核验。"),
        },
    )


@_serialized_run_mutation
def mark_expert_team_execution_cancelled(
    workspace: Path,
    run_id: str,
    message: str,
    *,
    stream_id: str,
) -> dict:
    """Persist terminal runtime cancellation without exposing a retry action."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") != "generating":
        raise ExpertTeamStateConflict("stale_state", "expert team execution is not generating", run)
    expected_stream_id = str(run.get("execution_stream_id") or "")
    if not stream_id or str(stream_id) != expected_stream_id:
        raise ExpertTeamStateConflict("stale_stream", "expert team execution stream changed", run)
    return _transition(
        workspace,
        run,
        "cancelled",
        "generation_cancelled",
        {
            **_clear_execution_patch(),
            "last_execution_error": str(message or "远程专家团执行已取消。"),
        },
    )


@_serialized_run_mutation
def fail_expert_team_execution_protocol(
    workspace: Path,
    run_id: str,
    message: str,
    *,
    stream_id: str,
) -> dict:
    """Stop delivery safely while retaining the remote cleanup gate."""
    run = read_run(workspace, run_id)
    _require_mutable_v2(run)
    if str(run.get("workflow_state") or "") not in {"generating", "cancelling"}:
        raise ExpertTeamStateConflict("stale_state", "expert team execution is not active", run)
    expected_stream_id = str(run.get("execution_stream_id") or "")
    if not stream_id or str(stream_id) != expected_stream_id:
        raise ExpertTeamStateConflict("stale_stream", "expert team execution stream changed", run)
    runtime_run_id = str(run.get("execution_runtime_run_id") or stream_id)
    runtime_adapter = str(run.get("execution_runtime_adapter") or "")
    error = str(message or "运行时事件协议异常，请重新尝试。")
    return _transition(
        workspace,
        run,
        "generated_invalid",
        "generation_invalid",
        {
            **_clear_execution_patch(),
            "orphan_runtime_run_id": runtime_run_id,
            "orphan_runtime_adapter": runtime_adapter,
            "execution_cleanup_status": "pending",
            "execution_cleanup_error": error,
            "execution_cleanup_retry_count": 0,
            "execution_cleanup_next_retry_at": 0,
            "execution_cleanup_deadline_at": time.time() + _CONTROL_RETRY_DEADLINE_SECONDS,
            "last_execution_error": error,
            "last_validation_error": error,
        },
    )


def cancel_expert_team(workspace: Path, body: dict, *, cancel_callback=None) -> dict:
    run_id = str(body.get("run_id") or "")
    cancel_request_id = str(body.get("idempotency_key") or "").strip()
    request_fingerprint = _request_fingerprint(body, "cancel")

    # Phase 1: durably record the cancellation intent under the run CAS lock.
    # The external runtime callback must execute after this scope is released.
    with _run_mutation_lock(workspace, run_id):
        current = read_run(workspace, run_id)
        retrying_unknown = (
            str(current.get("workflow_state") or "") == "cancelling"
            and str(current.get("cancel_outcome") or "").strip().lower()
            in {"unknown", "retry_required"}
            and str(current.get("cancel_request_id") or "").strip() == cancel_request_id
            and str(current.get("cancel_request_fingerprint") or "") == request_fingerprint
        )
        prepared_body = (
            {**body, "expected_version": int(current.get("version") or 0)}
            if retrying_unknown
            else body
        )
        run, duplicate = _prepare_mutation(
            workspace,
            prepared_body,
            "cancel",
            skip_idempotency_result=retrying_unknown,
        )
        if duplicate is not None:
            if (
                str(duplicate.get("workflow_state") or "") == "cancelling"
                and duplicate.get("cancel_runtime_accepted")
            ):
                return reconcile_expert_team_run(workspace, str(duplicate.get("run_id") or ""))
            return duplicate
        if str(run.get("workflow_state") or "") in TERMINAL_STATES:
            raise ExpertTeamStateConflict(
                "terminal_state",
                "terminal expert team runs cannot be cancelled",
                run,
            )
        previous_state = str(
            run.get("cancel_previous_state") if retrying_unknown else run.get("workflow_state") or ""
        )
        if retrying_unknown:
            cancelling = run
        else:
            cancelling = _transition(
                workspace,
                run,
                "cancelling",
                "generation_cancel_requested",
                {
                    "cancel_request_id": cancel_request_id,
                    "cancel_request_fingerprint": request_fingerprint,
                    "cancel_requested_at": _now(),
                    "cancel_previous_state": previous_state,
                    "cancel_runtime_accepted": False,
                    "cancel_outcome": "pending",
                    "cancel_retry_count": 0,
                    "cancel_next_retry_at": 0,
                    "cancel_deadline_at": time.time() + _CONTROL_RETRY_DEADLINE_SECONDS,
                    "last_execution_error": "",
                },
            )

    # Phase 2: remote I/O intentionally runs with neither the thread lock nor
    # the OS file lock held. A crash here leaves the durable intent recoverable.
    result = True
    unknown = False
    result_status = ""
    try:
        if cancel_callback is not None:
            result = cancel_callback(deepcopy(cancelling))
        accepted = bool(
            getattr(result, "accepted", result.get("ok", False) if isinstance(result, dict) else result)
        )
        message = str(
            getattr(
                result,
                "safe_message",
                result.get("message") if isinstance(result, dict) else None,
            )
            or "runtime rejected expert team cancellation"
        )
        result_status = str(
            getattr(
                result,
                "status",
                result.get("status", "") if isinstance(result, dict) else "",
            )
            or ""
        ).strip().lower()
        if not accepted and result_status in {"unknown", "timeout", "pending", "unavailable"}:
            unknown = True
    except Exception as exc:
        accepted = False
        unknown = True
        message = str(exc) or "runtime rejected expert team cancellation"

    # Phase 3: commit the observed outcome only if the same intent still owns
    # the run. This is the compare-and-swap half of the two-phase protocol.
    with _run_mutation_lock(workspace, run_id):
        cancelling = read_run(workspace, run_id)
        if str(cancelling.get("workflow_state") or "") in TERMINAL_STATES:
            return _sync_derived(cancelling)
        if str(cancelling.get("workflow_state") or "") != "cancelling":
            raise ExpertTeamStateConflict(
                "stale_state",
                "expert team cancellation intent no longer owns the run",
                cancelling,
            )
        if str(cancelling.get("cancel_request_id") or "") != cancel_request_id:
            raise ExpertTeamStateConflict(
                "stale_cancel",
                "expert team cancellation request changed",
                cancelling,
            )
        if unknown:
            return _transition(
                workspace,
                cancelling,
                "cancelling",
                "generation_cancel_unknown",
                {
                    "cancel_runtime_accepted": False,
                    "cancel_outcome": "unknown",
                    **_cancel_retry_schedule(cancelling),
                    "last_execution_error": message,
                },
            )
        if not accepted:
            rollback_state = str(cancelling.get("cancel_previous_state") or previous_state)
            rolled_back = _transition(
                workspace,
                cancelling,
                rollback_state,
                "generation_cancel_rejected",
                {
                    "cancel_request_id": "",
                    "cancel_request_fingerprint": "",
                    "cancel_requested_at": "",
                    "cancel_previous_state": "",
                    "cancel_runtime_accepted": False,
                    "cancel_outcome": "rejected",
                    "cancel_retry_count": 0,
                    "cancel_next_retry_at": 0,
                    "cancel_deadline_at": 0,
                    "last_execution_error": message,
                },
            )
            raise ExpertTeamStateConflict("cancel_rejected", message, rolled_back)
        _record_action(cancelling, body, "cancel")
        accepted_run = _transition(
            workspace,
            cancelling,
            "cancelling",
            "generation_cancel_accepted",
            {
                "cancel_runtime_accepted": True,
                "cancel_outcome": "accepted",
                **_cancel_retry_schedule(cancelling),
            },
        )
        if result_status in {"not_found", "no-runtime-started", "missing", "absent"}:
            return _transition(workspace, accepted_run, "cancelled", "generation_cancelled")
        return accepted_run


def _business_context_for_view(run: dict) -> dict:
    return business_context_for_run(run)
