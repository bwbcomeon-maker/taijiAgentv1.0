"""Typed dispatcher boundary for expert-team stages that must never call a model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Callable


SYSTEM_STAGE_REQUEST_SCHEMA = "system-stage-request/v1"


class SystemStageError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SystemStageRequestV1:
    schema_version: str
    session_id: str
    run_id: str
    stage_id: str
    stage_attempt: int
    descriptor: dict[str, Any]
    brief_ref: dict[str, Any]
    canonical_document_ref: dict[str, Any]
    approved_input_refs: list[dict[str, Any]]

    def to_dict(self) -> dict:
        return asdict(self)


def build_system_stage_request(run: dict, descriptor: dict, reservation: dict) -> SystemStageRequestV1:
    if descriptor.get("executor") != "system":
        raise SystemStageError("stage_executor_mismatch", "system dispatcher received a model stage")
    stage_id = str(descriptor.get("id") or "")
    if reservation.get("stage_id") != stage_id or reservation.get("executor") != "system":
        raise SystemStageError("stage_attempt_identity_mismatch", "system reservation does not match its descriptor")
    brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
    canonical = run.get("canonical_document_ref")
    if not isinstance(canonical, dict) or not canonical.get("artifact_id") or not canonical.get("sha256"):
        raise SystemStageError("canonical_document_required", "system delivery requires an approved canonical document")
    expected_canonical_keys = {"artifact_id", "sha256", "brief_revision", "brief_sha256"}
    if set(canonical) != expected_canonical_keys:
        raise SystemStageError("canonical_document_ref_invalid", "canonical document reference has unknown fields")
    if (
        int(canonical.get("brief_revision") or 0) != int(brief.get("confirmed_revision") or 0)
        or canonical.get("brief_sha256") != brief.get("confirmed_sha256")
    ):
        raise SystemStageError("canonical_document_ref_invalid", "canonical document is bound to another brief")
    descriptor_copy = deepcopy(descriptor)
    allowed_descriptor_keys = {
        "id", "title", "phase", "worker_id", "worker_name", "executor", "artifact_type",
        "depends_on", "trigger", "visible_progress",
    }
    if set(descriptor_copy) - allowed_descriptor_keys:
        raise SystemStageError("system_stage_descriptor_invalid", "system descriptor has unknown fields")
    return SystemStageRequestV1(
        schema_version=SYSTEM_STAGE_REQUEST_SCHEMA,
        session_id=str(run.get("session_id") or ""),
        run_id=str(run.get("run_id") or ""),
        stage_id=stage_id,
        stage_attempt=int(reservation.get("stage_attempt") or 0),
        descriptor=descriptor_copy,
        brief_ref={
            "revision": int(brief.get("confirmed_revision") or 0),
            "sha256": str(brief.get("confirmed_sha256") or ""),
        },
        canonical_document_ref=deepcopy(canonical),
        approved_input_refs=deepcopy(reservation.get("input_refs") or []),
    )


class SystemStageRegistry:
    def __init__(self, executors: dict[str, Callable[[dict], dict]] | None = None):
        self._executors = dict(executors or {})

    def executor_for(self, stage_id: str):
        return self._executors.get(str(stage_id or ""))


def dispatch_system_stage(
    run: dict,
    descriptor: dict,
    reservation: dict,
    *,
    registry: SystemStageRegistry,
) -> dict:
    request = build_system_stage_request(run, descriptor, reservation)
    executor = registry.executor_for(request.stage_id)
    if executor is None:
        code = "delivery_contract_unavailable" if request.stage_id == "delivery" else "system_stage_unavailable"
        raise SystemStageError(code, "系统交付合同尚未注册，未生成伪交付物")
    result = executor(request.to_dict())
    if not isinstance(result, dict) or set(result) != {"artifact"} or not isinstance(result.get("artifact"), dict):
        raise SystemStageError("system_stage_result_invalid", "system executor returned an invalid typed result")
    return {"request": request.to_dict(), "artifact": deepcopy(result["artifact"])}


def _template_identity(template_id: str) -> dict:
    from api import docx_engine_v2

    package = docx_engine_v2.engine_root() / "templates" / str(template_id)
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        raise SystemStageError("template_identity_unavailable", "企业模板身份不可用")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    for file_path in sorted(item for item in package.rglob("*") if item.is_file()):
        digest.update(file_path.relative_to(package).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return {
        "id": str(manifest.get("id") or template_id),
        "version": str(manifest.get("version") or ""),
        "package_sha256": digest.hexdigest(),
    }


def _renderer_identities() -> tuple[dict, dict]:
    from api import docx_engine_v2

    camel = docx_engine_v2.describe_renderer_identity("enterprise-default")
    snake = {
        "name": str(camel.get("name") or ""),
        "version": str(camel.get("version") or ""),
        "build_sha256": str(camel.get("buildSha256") or ""),
        "profile_id": str(camel.get("profileId") or ""),
        "profile_sha256": str(camel.get("profileSha256") or ""),
    }
    return snake, camel


def _document_metadata(brief: dict) -> dict:
    control = brief.get("document_control") if isinstance(brief.get("document_control"), dict) else {}
    return {
        "title": str(brief.get("exact_title") or ""),
        "documentType": str(brief.get("document_type") or ""),
        "client": str(control.get("client") or ""),
        "issuer": str(control.get("issuer") or ""),
        "compiler": str(control.get("compiler") or ""),
        "versionLabel": str(control.get("version_label") or ""),
        "classification": str(control.get("classification") or ""),
        "classificationLabel": str(control.get("classification_label") or ""),
        "documentDate": str(control.get("document_date") or ""),
    }


def _validate_delivery_binding_files(
    attempt_root: Path,
    binding: dict,
    *,
    request: dict,
    delivery_reservation: dict,
    template: dict,
    renderer: dict,
) -> None:
    from .delivery_integrity import sha256_file

    expected_identity = {
        "session_id": str(request["session_id"]),
        "run_id": str(request["run_id"]),
        "stage_id": str(request["stage_id"]),
        "stage_attempt": int(request["stage_attempt"]),
        "delivery_attempt": int(delivery_reservation["delivery_attempt"]),
        "document_revision": int(delivery_reservation["document_revision"]),
        "render_input_fingerprint": str(delivery_reservation["render_input_fingerprint"]),
        "template": template,
        "renderer": renderer,
    }
    for field, expected in expected_identity.items():
        if binding.get(field) != expected:
            raise SystemStageError("delivery_binding_changed", f"交付绑定字段已变化: {field}")
    canonical = request.get("canonical_document_ref") or {}
    if binding.get("canonical_artifact") != {
        "artifact_id": canonical.get("artifact_id"),
        "sha256": canonical.get("sha256"),
    }:
        raise SystemStageError("delivery_binding_changed", "交付绑定正文身份已变化")
    for field in (
        "canonical_markdown", "asset_manifest", "semantic_gates", "document",
        "automatic_quality_report", "layered_quality_report",
    ):
        ref = binding.get(field)
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
            raise SystemStageError("delivery_binding_changed", f"交付绑定摘要缺失: {field}")
        relative = Path(str(ref.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemStageError("delivery_binding_changed", f"交付绑定路径不安全: {field}")
        target = (attempt_root / relative).resolve()
        if attempt_root.resolve() not in target.parents or not target.is_file() or sha256_file(target) != ref["sha256"]:
            raise SystemStageError("delivery_binding_changed", f"交付绑定镜像摘要不一致: {field}")


def _execute_delivery_stage(workspace: Path, request: dict) -> dict:
    from api import docx_engine_v2
    from .documents import (
        FinalDocumentDeliveryError,
        build_delivery_binding_v2,
        build_delivery_manifest_from_binding,
        build_render_input_binding,
        prepare_canonical_delivery_inputs,
    )
    from .delivery_integrity import canonical_attempt_root, sha256_file, workspace_relative_path
    from .runtime import reserve_document_revision_and_delivery_attempt
    from .stage_artifacts import build_stage_artifact
    from .storage import read_run

    root = Path(workspace).expanduser().resolve()
    run = read_run(root, str(request.get("run_id") or ""))
    if (
        str(run.get("session_id") or "") != str(request.get("session_id") or "")
        or run.get("canonical_document_ref") != request.get("canonical_document_ref")
    ):
        raise SystemStageError("system_stage_request_stale", "系统交付请求已失效")
    brief = run.get("document_brief") if isinstance(run.get("document_brief"), dict) else {}
    template_id = str((brief.get("document_control") or {}).get("render_template_id") or "")
    template = _template_identity(template_id)
    renderer, renderer_camel = _renderer_identities()

    with tempfile.TemporaryDirectory(prefix="taiji-delivery-preview-") as preview:
        preview_inputs = prepare_canonical_delivery_inputs(
            Path(preview), run, stage_id=str(request["stage_id"]), delivery_attempt=1
        )
        preview_binding = build_render_input_binding(
            brief=brief,
            artifact=preview_inputs["artifact"],
            canonical_document_path=preview_inputs["paths"]["document"],
            asset_manifest_path=preview_inputs["paths"]["asset_manifest"],
            semantic_gates_path=preview_inputs["paths"]["semantic_gates"],
            template=template,
            renderer=renderer,
        )
    fingerprint = preview_binding["render_input_fingerprint"]
    lineage = f"system-delivery:{request['stage_attempt']}:{fingerprint}"
    reserved_run, delivery_reservation, _created = reserve_document_revision_and_delivery_attempt(
        root,
        str(request["run_id"]),
        canonical_ref=request["canonical_document_ref"],
        render_input_fingerprint=fingerprint,
        idempotency_key=lineage,
    )
    delivery_attempt = int(delivery_reservation["delivery_attempt"])
    attempt_root = canonical_attempt_root(root, str(request["run_id"]), str(request["stage_id"]), delivery_attempt)
    binding_path = attempt_root / "expert-team-delivery.json"
    quality_path = attempt_root / "delivery" / "quality-report.json"
    if binding_path.is_file():
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    else:
        prepared = prepare_canonical_delivery_inputs(
            root,
            reserved_run,
            stage_id=str(request["stage_id"]),
            delivery_attempt=delivery_attempt,
        )
        render_input = build_render_input_binding(
            brief=brief,
            artifact=prepared["artifact"],
            canonical_document_path=prepared["paths"]["document"],
            asset_manifest_path=prepared["paths"]["asset_manifest"],
            semantic_gates_path=prepared["paths"]["semantic_gates"],
            template=template,
            renderer=renderer,
        )
        if render_input["render_input_fingerprint"] != fingerprint:
            raise SystemStageError("render_input_fingerprint_changed", "交付输入在预约后发生变化")
        engine_binding = {key: value for key, value in render_input.items() if key != "render_input_fingerprint"}
        canonical_ref = request["canonical_document_ref"]
        result, status = docx_engine_v2._create_expert_delivery_job(
            {
                "template_id": template_id,
                "source_path": str(prepared["paths"]["document"]),
                "source_type": "markdown",
                "asset_dir": str(prepared["paths"]["asset_manifest"].parent),
                "asset_manifest_path": str(prepared["paths"]["asset_manifest"]),
                "out_dir": str(attempt_root / "delivery"),
                "document_metadata": _document_metadata(brief),
                "canonical_binding": {
                    "artifactId": canonical_ref["artifact_id"],
                    "artifactSha256": canonical_ref["sha256"],
                    "briefRevision": int(canonical_ref["brief_revision"]),
                    "briefSha256": canonical_ref["brief_sha256"],
                },
                "renderer_identity": renderer_camel,
                "render_input_binding": engine_binding,
                "render_input_fingerprint": fingerprint,
            },
            root,
            run_id=str(request["run_id"]),
            stage_id=str(request["stage_id"]),
            attempt=delivery_attempt,
        )
        if status != 200 or not result.get("ok"):
            raise SystemStageError(str(result.get("code") or "delivery_render_failed"), str(result.get("message") or "DOCX 交付生成失败"))
        binding = build_delivery_binding_v2(
            attempt_root,
            session_id=str(request["session_id"]),
            run_id=str(request["run_id"]),
            stage_id=str(request["stage_id"]),
            stage_attempt=int(request["stage_attempt"]),
            delivery_attempt=delivery_attempt,
            document_revision=int(delivery_reservation["document_revision"]),
            brief=brief,
            artifact=prepared["artifact"],
            assets=prepared["paths"]["asset_manifest"],
            semantic_gates=prepared["semantic_gates"],
            template=template,
            renderer=renderer,
            render_input_fingerprint=fingerprint,
            document=attempt_root / "delivery" / "document.docx",
            quality=quality_path,
        )

    _validate_delivery_binding_files(
        attempt_root,
        binding,
        request=request,
        delivery_reservation=delivery_reservation,
        template=template,
        renderer=renderer,
    )

    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    projected_binding = {
        **binding,
        "_binding_path": workspace_relative_path(root, binding_path),
        "_binding_sha256": sha256_file(binding_path),
        "_quality_report_sha256": sha256_file(quality_path),
    }
    manifest = build_delivery_manifest_from_binding(projected_binding, quality)
    failed = [
        item for item in quality.get("checks") or []
        if isinstance(item, dict) and item.get("id") != "wps_visual" and item.get("status") == "failed"
    ]
    issues = [
        {
            "issue_id": f"delivery:{item.get('id')}:{index}",
            "severity": "blocking",
            "category": "render",
            "field_path": f"quality.checks.{item.get('id')}",
            "message": str(item.get("message") or item.get("id") or "automatic check failed"),
            "suggested_action": "修复对应自动检查后重新生成交付包",
        }
        for index, item in enumerate(failed, 1)
    ]
    artifact = build_stage_artifact(
        {
            "artifact_type": "delivery_manifest",
            "summary": "企业 DOCX 已按批准正文生成，等待 Office 视觉验收。",
            "payload": manifest,
            "blocking_issues": issues,
            "deliverable_markdown": None,
        },
        stage_id=str(request["stage_id"]),
        stage_attempt=int(request["stage_attempt"]),
        brief=brief,
        input_refs=deepcopy(request.get("approved_input_refs") or []),
        now=str(delivery_reservation.get("created_at") or datetime.now(timezone.utc).isoformat()),
    )
    return {"artifact": artifact}


def get_system_stage_registry(workspace: Path | None = None) -> SystemStageRegistry:
    if workspace is None:
        return SystemStageRegistry()
    root = Path(workspace).expanduser().resolve()

    def execute(request: dict) -> dict:
        from .runtime import ExpertTeamStateConflict

        try:
            return _execute_delivery_stage(root, request)
        except (SystemStageError, ExpertTeamStateConflict):
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise SystemStageError("delivery_generation_failed", str(exc) or "企业交付生成失败") from exc

    return SystemStageRegistry({"delivery": execute})
