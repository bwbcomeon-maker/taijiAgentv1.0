"""Versioned enterprise input contract for expert-team document runs."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from copy import deepcopy

from .data_egress import validate_model_policy_reference


EXPERT_TEAM_CONTRACT_V1 = "expert-team-contract/v1"
DOCUMENT_BRIEF_V1 = "document-brief/v1"
_MISSING = object()
_LIFECYCLE_FIELDS = {"revision", "status", "confirmed_revision", "confirmed_at", "confirmed_sha256"}
_RENDER_TEMPLATES = {
    "work_report": "enterprise-work-report",
    "research_report": "enterprise-research-report",
}
_RELEASED_DOCUMENT_TYPES = frozenset(_RENDER_TEMPLATES)
_TASK_MODES = {"create", "polish"}
_SOURCE_MODES = {"provided_only", "approved_internal", "approved_public"}
_SOURCE_KINDS = {"attachment", "local_file", "provided_text", "approved_internal", "approved_public"}
_CITATION_STYLES = {"none", "source_id", "footnote"}
_UNKNOWN_FACT_ACTIONS = {"block_final", "allow_labeled_placeholder"}
_CLASSIFICATIONS = {"public", "internal", "restricted", "custom"}
_SEED_FIELDS = {
    "task_mode", "original_request", "exact_title", "purpose", "audience", "usage_scenario",
    "source_policy", "data_handling", "document_control", "content_constraints", "details",
    "approval", "additional_context",
}
_PATCH_FIELDS = _SEED_FIELDS - {"task_mode"}


class ContractError(ValueError):
    def __init__(self, code: str, field: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.field = field
        self.message = message or code

    def as_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "message": self.message}


def _text(value) -> str:
    normalized = unicodedata.normalize("NFC", "" if value is None else str(value))
    return normalized.replace("\r\n", "\n").replace("\r", "\n").strip()


def _error(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def classify_contract_version(mapping: dict) -> str:
    requested = mapping.get("contract_version", _MISSING)
    if requested is _MISSING:
        return "legacy"
    if requested == EXPERT_TEAM_CONTRACT_V1:
        return EXPERT_TEAM_CONTRACT_V1
    raise ContractError("unsupported_contract_version", "contract_version", "不支持的专家团合同版本")


def _mapping(value) -> dict:
    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value) -> list:
    return deepcopy(value) if isinstance(value, list) else []


def build_document_brief(team_id, payload, *, now) -> dict:
    del now  # reserved for future deterministic defaults; no hidden time-derived business fields
    document_type = _text(payload.get("document_type"))
    if not document_type:
        raise ContractError("document_type_required", "document_type", "请明确选择业务文种")
    if document_type not in _RELEASED_DOCUMENT_TYPES:
        raise ContractError("document_type_not_released", "document_type", "当前文种尚未进入企业合同试点")
    original_request = _text(payload.get("prompt"))
    if not original_request:
        raise ContractError("original_request_required", "prompt", "请填写原始诉求")

    seed = _mapping(payload.get("document_brief_seed"))
    unknown = sorted(set(seed) - _SEED_FIELDS)
    if unknown:
        raise ContractError("unknown_brief_field", unknown[0], "规格中包含不支持的字段")
    seeded_original = seed.get("original_request", _MISSING)
    if seeded_original is not _MISSING and _text(seeded_original) != original_request:
        raise ContractError("original_request_conflict", "document_brief_seed.original_request", "原始诉求与顶层 prompt 不一致")

    control = _mapping(seed.get("document_control"))
    expected_template = _RENDER_TEMPLATES[document_type]
    render_template_id = _text(control.get("render_template_id")) or expected_template
    if render_template_id != expected_template:
        raise ContractError("render_template_mismatch", "document_control.render_template_id", "文种与交付模板不兼容")
    control["render_template_id"] = render_template_id

    task_mode = _text(seed.get("task_mode")) or "create"
    source_policy = _mapping(seed.get("source_policy"))
    source_policy["source_refs"] = _list(source_policy.get("source_refs"))
    constraints = _mapping(seed.get("content_constraints"))
    for key in ("required_sections", "must_include", "must_avoid"):
        constraints[key] = _list(constraints.get(key))
    approval = _mapping(seed.get("approval"))
    approval["approver_roles"] = _list(approval.get("approver_roles"))

    return normalize_document_brief(
        {
            "schema_version": DOCUMENT_BRIEF_V1,
            "revision": 1,
            "status": "draft",
            "team_id": _text(team_id),
            "task_mode": task_mode,
            "original_request": original_request,
            "document_type": document_type,
            "intake_example_id": _text(payload.get("intake_example_id") or payload.get("template_id")),
            "exact_title": _text(seed.get("exact_title")),
            "purpose": _text(seed.get("purpose")),
            "audience": _text(seed.get("audience")),
            "usage_scenario": _text(seed.get("usage_scenario")),
            "source_policy": source_policy,
            "data_handling": _mapping(seed.get("data_handling")),
            "document_control": control,
            "content_constraints": constraints,
            "details": _mapping(seed.get("details")),
            "approval": approval,
            "additional_context": _text(seed.get("additional_context")),
            "confirmed_revision": None,
            "confirmed_at": None,
            "confirmed_sha256": None,
        }
    )


def normalize_document_brief(brief) -> dict:
    result = deepcopy(brief)
    for key in ("team_id", "task_mode", "original_request", "document_type", "intake_example_id", "exact_title", "purpose", "audience", "usage_scenario", "additional_context"):
        result[key] = _text(result.get(key))
    source_policy = _mapping(result.get("source_policy"))
    normalized_refs = []
    seen = set()
    for raw in source_policy.get("source_refs") or []:
        if not isinstance(raw, dict):
            continue
        source_id = _text(raw.get("source_id"))
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        allowed_fields = {"source_id", "kind", "label", "locator", "sha256"}
        if _text(raw.get("kind")) == "provided_text" and "text" in raw:
            allowed_fields.add("text")
        normalized_refs.append({k: deepcopy(v) for k, v in raw.items() if k in allowed_fields})
        normalized_refs[-1]["source_id"] = source_id
        normalized_refs[-1]["kind"] = _text(raw.get("kind"))
        normalized_refs[-1]["label"] = _text(raw.get("label"))
    source_policy["source_refs"] = normalized_refs
    result["source_policy"] = source_policy
    result["schema_version"] = DOCUMENT_BRIEF_V1
    return result


def brief_digest(brief) -> str:
    normalized = normalize_document_brief(brief)
    business = {key: value for key, value in normalized.items() if key not in _LIFECYCLE_FIELDS}
    encoded = json.dumps(business, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def confirm_document_brief(brief, *, now) -> dict:
    confirmed = normalize_document_brief(brief)
    if confirmed.get("status") == "confirmed":
        return confirmed
    confirmed["status"] = "confirmed"
    confirmed["confirmed_revision"] = int(confirmed.get("revision") or 0)
    confirmed["confirmed_at"] = str(now)
    confirmed["confirmed_sha256"] = brief_digest(confirmed)
    return confirmed


def patch_document_brief(brief, patch, *, expected_revision, stage_started) -> dict:
    if stage_started:
        raise ContractError("brief_frozen_new_run_required", "document_brief", "已开始生成；修改规格需基于当前规格新建任务")
    current_revision = int(brief.get("revision") or 0)
    if int(expected_revision) != current_revision:
        raise ContractError("brief_revision_conflict", "expected_brief_revision", "规格已被更新，请保留草稿并刷新后重试")
    if not isinstance(patch, dict):
        raise ContractError("invalid_brief_patch", "patch", "规格更新必须是字段对象")
    unknown = sorted(set(patch) - _PATCH_FIELDS)
    if unknown:
        raise ContractError("unknown_brief_field", unknown[0], "规格更新包含不支持的字段")
    updated = deepcopy(brief)
    for key, value in patch.items():
        updated[key] = deepcopy(value)
    updated["revision"] = current_revision + 1
    updated["status"] = "draft"
    updated["confirmed_revision"] = None
    updated["confirmed_at"] = None
    updated["confirmed_sha256"] = None
    return normalize_document_brief(updated)


def _required(brief: dict, fields: list[tuple[str, object]]) -> list[dict]:
    errors = []
    for field, value in fields:
        if value is None or value == "" or value == []:
            errors.append(_error(field, "required", f"请填写{field}"))
    return errors


def validate_document_brief(brief, *, runtime_capabilities, source_registry, model_policy_registry, now="") -> dict:
    normalized = normalize_document_brief(brief)
    errors = []
    document_type = normalized.get("document_type")
    if normalized.get("task_mode") not in _TASK_MODES:
        errors.append(_error("task_mode", "invalid_enum", "任务模式无效"))
    if document_type not in _RELEASED_DOCUMENT_TYPES:
        errors.append(_error("document_type", "document_type_not_released", "当前文种尚未放行"))
    source_policy = normalized.get("source_policy") or {}
    if source_policy.get("mode") not in _SOURCE_MODES:
        errors.append(_error("source_policy.mode", "invalid_enum", "资料模式无效"))
    if source_policy.get("citation_style") not in _CITATION_STYLES:
        errors.append(_error("source_policy.citation_style", "invalid_enum", "引用样式无效"))
    if source_policy.get("unknown_fact_action") not in _UNKNOWN_FACT_ACTIONS:
        errors.append(_error("source_policy.unknown_fact_action", "invalid_enum", "未知事实处理方式无效"))
    control = normalized.get("document_control") or {}
    if control.get("classification") not in _CLASSIFICATIONS:
        errors.append(_error("document_control.classification", "invalid_enum", "密级无效"))
    if control.get("classification") == "custom" and not _text(control.get("classification_label")):
        errors.append(_error("document_control.classification_label", "required", "请填写自定义密级标签"))
    if control.get("render_template_id") != _RENDER_TEMPLATES.get(document_type):
        errors.append(_error("document_control.render_template_id", "render_template_mismatch", "文种与模板不兼容"))

    details = normalized.get("details") or {}
    common = [
        ("exact_title", normalized.get("exact_title")),
        ("purpose", normalized.get("purpose")),
        ("audience", normalized.get("audience")),
        ("usage_scenario", normalized.get("usage_scenario")),
    ]
    if document_type == "work_report":
        errors.extend(_required(normalized, common + [("details.reporting_period", details.get("reporting_period")), ("details.reporting_unit", details.get("reporting_unit"))]))
    elif document_type == "research_report":
        time_range = details.get("time_range") if isinstance(details.get("time_range"), dict) else {}
        errors.extend(_required(normalized, common + [("details.core_question", details.get("core_question")), ("details.time_range.start", time_range.get("start")), ("details.time_range.end", time_range.get("end")), ("source_policy.as_of_date", source_policy.get("as_of_date"))]))
        if source_policy.get("citation_style") == "none":
            errors.append(_error("source_policy.citation_style", "citation_style_required", "研究报告必须保留可追溯引用"))

    if source_policy.get("mode") == "approved_public" and not bool((runtime_capabilities or {}).get("approved_public_search")):
        errors.append(_error("source_policy.mode", "approved_public_unavailable", "当前任务未获得批准的公开检索能力"))

    ready_count = 0
    for index, source_ref in enumerate(source_policy.get("source_refs") or []):
        source_id = source_ref.get("source_id")
        authoritative = source_registry.get(source_id) if isinstance(source_registry, dict) else None
        if not isinstance(authoritative, dict) or authoritative.get("status") != "ready" or not authoritative.get("sha256"):
            errors.append(_error(f"source_policy.source_refs.{index}", "source_unresolved", "资料尚未由服务端解析和固化"))
            continue
        if source_ref.get("kind") not in _SOURCE_KINDS or source_ref.get("kind") != authoritative.get("kind"):
            errors.append(_error(f"source_policy.source_refs.{index}.kind", "source_kind_mismatch", "资料类型与服务端记录不一致"))
            continue
        client_hash = source_ref.get("sha256")
        if client_hash and client_hash != authoritative.get("sha256"):
            errors.append(_error(f"source_policy.source_refs.{index}.sha256", "source_hash_conflict", "资料摘要与服务端原始字节不一致"))
            continue
        ready_count += 1
    if ready_count == 0 and not any(item["code"] == "source_unresolved" for item in errors):
        errors.append(_error("source_policy.source_refs", "source_unresolved", "正式文稿至少需要一项可核对资料"))

    policy_result = validate_model_policy_reference(normalized, model_policy_registry=model_policy_registry, now=now)
    errors.extend(policy_result["field_errors"])
    return {
        "valid_for_confirmation": not errors,
        "field_errors": errors,
        "release_candidate": document_type in _RELEASED_DOCUMENT_TYPES,
        "enterprise_released": False,
        "model_policy": {"policy_id": policy_result["policy_id"], "label": policy_result["label"], "authorized": policy_result["authorized"]},
    }


def brief_summary(brief) -> dict:
    normalized = normalize_document_brief(brief)
    digest = normalized.get("confirmed_sha256") or brief_digest(normalized)
    return {
        "revision": int(normalized.get("revision") or 0),
        "status": normalized.get("status"),
        "original_request": normalized.get("original_request"),
        "exact_title": normalized.get("exact_title"),
        "document_type": normalized.get("document_type"),
        "render_template_id": (normalized.get("document_control") or {}).get("render_template_id"),
        "confirmed_sha256_short": digest[:12] if normalized.get("status") == "confirmed" else "",
    }
