"""Single public error policy for standalone expert-team workflows."""

from __future__ import annotations

from collections.abc import Mapping

from api.product_contract import attach_product_error


_PUBLIC_CODES = {
    "agent_unavailable",
    "artifact_generation_failed",
    "backend_unavailable",
    "delivery_copy_failed",
    "diagnostics_unavailable",
    "document_open_failed",
    "document_render_failed",
    "expert_team_content_blocked",
    "expert_team_evidence_required",
    "expert_team_in_progress",
    "expert_team_not_found",
    "expert_team_source_invalid",
    "expert_team_state_conflict",
    "gateway_unavailable",
    "license_blocked",
    "model_configuration_required",
    "model_output_invalid",
    "office_review_required",
    "permission_denied",
    "provider_authorization_failed",
    "provider_rate_limited",
    "provider_timeout",
    "unknown_error",
}

_EXACT_CODES = {
    "stage_attempt_in_progress": "expert_team_in_progress",
    "stage_artifact_blocked": "expert_team_content_blocked",
    "system_artifact_blocked": "expert_team_content_blocked",
    "delivery_quality_failed": "expert_team_content_blocked",
    "delivery_semantic_blocked": "expert_team_content_blocked",
    "validation_failed": "document_render_failed",
    "runtime_protocol_error": "model_output_invalid",
    "runtime_observation_limit": "model_output_invalid",
    "delivery_generation_failed": "document_render_failed",
    "system_stage_failed": "document_render_failed",
    "delivery_open_target_changed": "document_open_failed",
    "delivery_target_invalid": "document_open_failed",
    "delivery_document_missing": "document_open_failed",
    "delivery_copy_conflict": "delivery_copy_failed",
    "delivery_copy_destination_invalid": "delivery_copy_failed",
    "delivery_copy_destination_missing": "delivery_copy_failed",
    "delivery_copy_destination_reserved": "delivery_copy_failed",
    "source_context_invalid": "expert_team_source_invalid",
}


def expert_team_product_error_code(code: object, *, http_status: int | None = None) -> str:
    """Map internal status/exception codes to a stable product error category."""

    normalized = str(code or "").strip().lower()
    if normalized in _PUBLIC_CODES:
        return normalized
    if normalized in _EXACT_CODES:
        return _EXACT_CODES[normalized]
    if normalized.startswith("delivery_copy_"):
        return "delivery_copy_failed"
    if normalized.startswith("delivery_open_") or normalized.startswith("document_open_"):
        return "document_open_failed"
    if any(
        token in normalized
        for token in ("docx", "document_render", "delivery_generation", "delivery_render")
    ):
        return "document_render_failed"
    if any(token in normalized for token in ("protocol", "artifact_schema", "output_invalid")):
        return "model_output_invalid"
    if normalized.endswith("_blocked") or "blocking" in normalized:
        return "expert_team_content_blocked"

    status = int(http_status or 0)
    if status in {401, 403}:
        return "provider_authorization_failed"
    if status == 429:
        return "provider_rate_limited"
    if status in {408, 504} or "timeout" in normalized:
        return "provider_timeout"
    if normalized.startswith("stale_") or normalized.startswith("wrong_") or any(
        token in normalized
        for token in ("idempotency", "identity_mismatch", "binding_changed", "version_conflict")
    ):
        return "expert_team_state_conflict"
    if status >= 500:
        return "backend_unavailable"
    return "unknown_error"


def attach_expert_team_product_error(
    payload: Mapping[str, object] | None,
    code: object,
    *,
    http_status: int | None = None,
    incident_id: object = None,
) -> dict:
    """Attach a safe envelope and replace raw public error text."""

    product_code = expert_team_product_error_code(code, http_status=http_status)
    result = attach_product_error(
        payload,
        product_code,
        incident_id=incident_id,
    )
    result["error"] = result["product_error"]["message"]
    return result
