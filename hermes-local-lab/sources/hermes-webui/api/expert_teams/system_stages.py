"""Typed dispatcher boundary for expert-team stages that must never call a model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
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


_PRODUCTION_REGISTRY = SystemStageRegistry()


def get_system_stage_registry() -> SystemStageRegistry:
    return _PRODUCTION_REGISTRY
