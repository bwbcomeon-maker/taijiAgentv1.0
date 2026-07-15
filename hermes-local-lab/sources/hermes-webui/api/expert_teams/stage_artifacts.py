"""Strict StageArtifactV1 parsing, enrichment and validation."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy


STAGE_ARTIFACT_V1 = "expert-stage-artifact/v1"
META_START = "<<<TAIJI_META_V1>>>"
META_END = "<<<TAIJI_META_END>>>"
DOCUMENT_START = "<<<TAIJI_DOCUMENT_V1>>>"
DOCUMENT_END = "<<<TAIJI_DOCUMENT_END>>>"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DOCUMENT_TYPES = {
    "document_draft",
    "reviewed_document",
    "research_document_draft",
    "reviewed_research_document",
}
_SOURCE_BOUND_TYPES = {"material_ledger", "source_register", "evidence_matrix"}
_PURITY_PATTERNS = (
    (re.compile(r"负责专家", re.I), "internal_expert_label"),
    (re.compile(r"\bStage\s*\d+", re.I), "internal_stage_label"),
    (re.compile(r"复核交付", re.I), "internal_delivery_label"),
    (re.compile(r"本阶段", re.I), "internal_stage_narration"),
    (re.compile(r"可直接生成\s*DOCX", re.I), "internal_tool_instruction"),
)


class StageArtifactError(ValueError):
    def __init__(self, code: str, field: str = "", message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.field = field


def _exact(mapping, fields, *, path):
    if not isinstance(mapping, dict):
        raise StageArtifactError("invalid_type", path, f"{path} must be an object")
    allowed = set(fields)
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise StageArtifactError("unknown_field", f"{path}.{unknown[0]}", "unknown field")
    missing = [field for field in fields if field not in mapping]
    if missing:
        raise StageArtifactError("required_field_missing", f"{path}.{missing[0]}", "required field missing")


def _string(value, path, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise StageArtifactError("invalid_type", path, "non-empty string required")


def _string_list(value, path, *, unique=False):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise StageArtifactError("invalid_type", path, "string list required")
    if unique and len(value) != len(set(value)):
        raise StageArtifactError("duplicate_id", path, "values must be unique")


def _enum(value, allowed, path):
    if value not in allowed:
        raise StageArtifactError("invalid_enum", path, "invalid enum")


def _issues(value, path="blocking_issues"):
    if not isinstance(value, list):
        raise StageArtifactError("invalid_type", path, "issue list required")
    seen = set()
    for index, issue in enumerate(value):
        item_path = f"{path}.{index}"
        _exact(
            issue,
            ("issue_id", "severity", "category", "field_path", "message", "suggested_action"),
            path=item_path,
        )
        _string(issue["issue_id"], f"{item_path}.issue_id")
        if issue["issue_id"] in seen:
            raise StageArtifactError("duplicate_id", f"{item_path}.issue_id")
        seen.add(issue["issue_id"])
        _enum(issue["severity"], {"blocking", "error", "warning", "info"}, f"{item_path}.severity")
        _enum(issue["category"], {"brief", "evidence", "structure", "purity", "security", "asset", "render"}, f"{item_path}.category")
        _string(issue["field_path"], f"{item_path}.field_path", nullable=True)
        _string(issue["message"], f"{item_path}.message")
        _string(issue["suggested_action"], f"{item_path}.suggested_action")


def parse_stage_response(raw_text, *, artifact_type, requires_document):
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise StageArtifactError("empty_response", "raw_text")
    if len(raw_text.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise StageArtifactError("response_too_large", "raw_text")
    marker_counts = {
        marker: raw_text.count(marker)
        for marker in (META_START, META_END, DOCUMENT_START, DOCUMENT_END)
    }
    if marker_counts[META_START] != 1 or marker_counts[META_END] != 1:
        raise StageArtifactError("invalid_block_count", "meta")
    expected_document_count = 1 if requires_document else 0
    if marker_counts[DOCUMENT_START] != expected_document_count or marker_counts[DOCUMENT_END] != expected_document_count:
        raise StageArtifactError("invalid_block_count", "document")
    if requires_document:
        pattern = re.compile(
            rf"^\s*{re.escape(META_START)}\s*\n(?P<meta>.*?)\n{re.escape(META_END)}\s*\n"
            rf"{re.escape(DOCUMENT_START)}\s*\n(?P<document>.*?)\n{re.escape(DOCUMENT_END)}\s*$",
            re.S,
        )
    else:
        pattern = re.compile(
            rf"^\s*{re.escape(META_START)}\s*\n(?P<meta>.*?)\n{re.escape(META_END)}\s*$",
            re.S,
        )
    match = pattern.fullmatch(raw_text)
    if not match:
        raise StageArtifactError("invalid_block_layout", "raw_text")
    try:
        meta = json.loads(match.group("meta"))
    except json.JSONDecodeError as exc:
        raise StageArtifactError("invalid_meta_json", "meta") from exc
    _exact(meta, ("artifact_type", "summary", "payload", "blocking_issues"), path="meta")
    if meta["artifact_type"] != artifact_type:
        raise StageArtifactError("artifact_type_mismatch", "meta.artifact_type")
    _string(meta["summary"], "meta.summary")
    if not isinstance(meta["payload"], dict):
        raise StageArtifactError("invalid_type", "meta.payload")
    _issues(meta["blocking_issues"])
    return {
        "artifact_type": artifact_type,
        "summary": meta["summary"].strip(),
        "payload": deepcopy(meta["payload"]),
        "blocking_issues": deepcopy(meta["blocking_issues"]),
        "deliverable_markdown": match.group("document").strip() if requires_document else None,
    }


def _unique_rows(rows, field, path):
    if not isinstance(rows, list):
        raise StageArtifactError("invalid_type", path)
    values = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise StageArtifactError("invalid_type", f"{path}.{index}")
        _string(row.get(field), f"{path}.{index}.{field}")
        values.append(row[field])
    if len(values) != len(set(values)):
        raise StageArtifactError("duplicate_id", path)
    return rows


def _validate_writing_plan(payload, brief):
    _exact(payload, ("objective", "document_type", "section_plan", "fact_requirements", "assumptions", "acceptance_checks"), path="payload")
    _string(payload["objective"], "payload.objective")
    if payload["document_type"] != brief.get("document_type"):
        raise StageArtifactError("document_type_mismatch", "payload.document_type")
    sections = _unique_rows(payload["section_plan"], "section_id", "payload.section_plan")
    facts = _unique_rows(payload["fact_requirements"], "fact_id", "payload.fact_requirements")
    fact_ids = {item["fact_id"] for item in facts}
    headings = set()
    for index, row in enumerate(sections):
        _exact(row, ("section_id", "heading", "purpose", "required_fact_ids"), path=f"payload.section_plan.{index}")
        _string(row["heading"], f"payload.section_plan.{index}.heading")
        _string(row["purpose"], f"payload.section_plan.{index}.purpose")
        _string_list(row["required_fact_ids"], f"payload.section_plan.{index}.required_fact_ids", unique=True)
        if not set(row["required_fact_ids"]) <= fact_ids:
            raise StageArtifactError("unknown_fact_id", f"payload.section_plan.{index}.required_fact_ids")
        headings.add(row["heading"])
    required = set((brief.get("content_constraints") or {}).get("required_sections") or [])
    if not required <= headings:
        raise StageArtifactError("required_section_missing", "payload.section_plan")
    for index, row in enumerate(facts):
        _exact(row, ("fact_id", "description", "required", "source_requirement"), path=f"payload.fact_requirements.{index}")
        _string(row["description"], f"payload.fact_requirements.{index}.description")
        if not isinstance(row["required"], bool):
            raise StageArtifactError("invalid_type", f"payload.fact_requirements.{index}.required")
        _enum(row["source_requirement"], {"provided_source", "approved_source", "no_external_source"}, f"payload.fact_requirements.{index}.source_requirement")
    _string_list(payload["assumptions"], "payload.assumptions")
    _string_list(payload["acceptance_checks"], "payload.acceptance_checks")


def _validate_section_map(value, path):
    rows = _unique_rows(value, "section_id", path)
    for index, row in enumerate(rows):
        _exact(row, ("section_id", "heading"), path=f"{path}.{index}")
        _string(row["heading"], f"{path}.{index}.heading")


def _validate_usage(value, id_field, path):
    if not isinstance(value, list):
        raise StageArtifactError("invalid_type", path)
    for index, row in enumerate(value):
        fields = (id_field, "section_id") if id_field == "fact_id" else (id_field, "section_id", "citation_marker")
        _exact(row, fields, path=f"{path}.{index}")
        for field in fields:
            _string(row[field], f"{path}.{index}.{field}")


def _validate_asset_requests(value, path):
    rows = _unique_rows(value, "asset_request_id", path)
    for index, row in enumerate(rows):
        _exact(row, ("asset_request_id", "kind", "purpose", "source_refs"), path=f"{path}.{index}")
        _enum(row["kind"], {"table", "image", "diagram"}, f"{path}.{index}.kind")
        _string(row["purpose"], f"{path}.{index}.purpose")
        _string_list(row["source_refs"], f"{path}.{index}.source_refs", unique=True)


def _review_issues(value, path):
    rows = _unique_rows(value, "issue_id", path)
    for index, row in enumerate(rows):
        _exact(row, ("issue_id", "severity", "category", "section_id", "description", "resolution", "status"), path=f"{path}.{index}")
        _enum(row["severity"], {"blocking", "error", "warning", "info"}, f"{path}.{index}.severity")
        _enum(row["category"], {"brief", "evidence", "structure", "purity", "security", "asset", "render"}, f"{path}.{index}.category")
        _string(row["section_id"], f"{path}.{index}.section_id", nullable=True)
        _string(row["description"], f"{path}.{index}.description")
        _string(row["resolution"], f"{path}.{index}.resolution", nullable=True)
        _enum(row["status"], {"open", "resolved"}, f"{path}.{index}.status")


def _validate_document_payload(payload, brief, *, reviewed=False, research=False):
    base = ["title", "section_map", "open_issues"]
    if not research:
        base.extend(["document_type", "fact_usage", "asset_requests"])
    else:
        base.append("claim_usage")
    if reviewed:
        base.append("review_report")
    _exact(payload, tuple(base), path="payload")
    if payload["title"] != brief.get("exact_title"):
        raise StageArtifactError("title_mismatch", "payload.title")
    if not research and payload["document_type"] != brief.get("document_type"):
        raise StageArtifactError("document_type_mismatch", "payload.document_type")
    _validate_section_map(payload["section_map"], "payload.section_map")
    _validate_usage(payload["claim_usage" if research else "fact_usage"], "claim_id" if research else "fact_id", "payload.claim_usage" if research else "payload.fact_usage")
    if not research:
        _validate_asset_requests(payload["asset_requests"], "payload.asset_requests")
    _review_issues(payload["open_issues"], "payload.open_issues")
    if reviewed:
        _validate_review_report(payload["review_report"], research=research)


def _validate_review_report(report, *, research):
    if research:
        fields = ("schema_version", "checks", "issues", "unsupported_claim_ids", "unresolved_contradiction_ids", "change_summary", "unresolved_issue_ids")
        check_keys = {"brief_alignment", "citation_completeness", "unsupported_claims", "unresolved_contradictions", "as_of_date_compliance", "document_purity", "confidentiality"}
        expected_version = "research-review-report/v1"
    else:
        fields = ("schema_version", "checks", "issues", "change_summary", "unresolved_issue_ids")
        check_keys = {"brief_alignment", "fact_traceability", "document_purity", "confidentiality", "document_structure"}
        expected_version = "content-review-report/v1"
    _exact(report, fields, path="payload.review_report")
    if report["schema_version"] != expected_version:
        raise StageArtifactError("schema_version_mismatch", "payload.review_report.schema_version")
    if not isinstance(report["checks"], dict) or set(report["checks"]) != check_keys:
        raise StageArtifactError("review_checks_mismatch", "payload.review_report.checks")
    for key, value in report["checks"].items():
        _enum(value, {"passed", "failed", "not_applicable"}, f"payload.review_report.checks.{key}")
    _review_issues(report["issues"], "payload.review_report.issues")
    open_ids = {item["issue_id"] for item in report["issues"] if item["status"] == "open"}
    _string_list(report["unresolved_issue_ids"], "payload.review_report.unresolved_issue_ids", unique=True)
    if set(report["unresolved_issue_ids"]) != open_ids:
        raise StageArtifactError("unresolved_issue_mismatch", "payload.review_report.unresolved_issue_ids")
    _string_list(report["change_summary"], "payload.review_report.change_summary")
    if research:
        _string_list(report["unsupported_claim_ids"], "payload.review_report.unsupported_claim_ids", unique=True)
        _string_list(report["unresolved_contradiction_ids"], "payload.review_report.unresolved_contradiction_ids", unique=True)


def _snapshot_index(snapshot):
    if not isinstance(snapshot, dict):
        raise StageArtifactError("source_snapshot_required", "source_snapshot")
    sources = {}
    segments = {}
    for source in snapshot.get("sources") or []:
        source_id = str(source.get("source_id") or "")
        if not source_id:
            raise StageArtifactError("invalid_source_snapshot", "source_snapshot.sources")
        sources[source_id] = source
        content = source.get("content_text")
        if not isinstance(content, str):
            raise StageArtifactError("invalid_source_snapshot", f"source_snapshot.sources.{source_id}.content_text")
        for segment in source.get("segments") or []:
            segment_id = str(segment.get("segment_id") or "")
            start = segment.get("char_start")
            end = segment.get("char_end")
            text = segment.get("text")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(text, str) or content[start:end] != text:
                raise StageArtifactError("segment_recompute_failed", f"source_snapshot.segments.{segment_id}")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest != segment.get("text_sha256"):
                raise StageArtifactError("segment_recompute_failed", f"source_snapshot.segments.{segment_id}.text_sha256")
            segments[(source_id, segment_id)] = segment
    return sources, segments


def _evidence_ref(raw, sources, segments, path):
    _exact(raw, ("source_id", "segment_id", "relationship"), path=path)
    source_id = raw["source_id"]
    segment_id = raw["segment_id"]
    if source_id not in sources:
        raise StageArtifactError("unknown_source_id", f"{path}.source_id")
    segment = segments.get((source_id, segment_id))
    if segment is None:
        raise StageArtifactError("unknown_segment_id", f"{path}.segment_id")
    _enum(raw["relationship"], {"supports", "contradicts", "context"}, f"{path}.relationship")
    return {
        "source_id": source_id,
        "segment_id": segment_id,
        "segment_sha256": segment["text_sha256"],
        "locator": segment["locator"],
        "relationship": raw["relationship"],
    }


def _canonicalize_material_ledger(payload, snapshot):
    sources, segments = _snapshot_index(snapshot)
    result = deepcopy(payload)
    result["sources"] = _trusted_sources(snapshot)
    for fact_index, fact in enumerate(result.get("facts") or []):
        fact["evidence_refs"] = [
            _evidence_ref(ref, sources, segments, f"payload.facts.{fact_index}.evidence_refs.{index}")
            for index, ref in enumerate(fact.get("evidence_refs") or [])
        ]
    return result


def canonicalize_trusted_payload(parsed, *, artifact_type, source_snapshot=None):
    result = deepcopy(parsed)
    if artifact_type == "material_ledger":
        result["payload"] = _canonicalize_material_ledger(result["payload"], source_snapshot)
    elif artifact_type == "source_register":
        _snapshot_index(source_snapshot)
        result["payload"]["sources"] = _trusted_sources(source_snapshot)
    elif artifact_type == "evidence_matrix":
        sources, segments = _snapshot_index(source_snapshot)
        for claim_index, claim in enumerate(result["payload"].get("claims") or []):
            claim["evidence"] = [
                _evidence_ref(ref, sources, segments, f"payload.claims.{claim_index}.evidence.{index}")
                for index, ref in enumerate(claim.get("evidence") or [])
            ]
    return result


def _trusted_sources(snapshot):
    rows = []
    for source in (snapshot or {}).get("sources") or []:
        rows.append(
            {
                "source_id": source.get("source_id"),
                "kind": source.get("kind"),
                "label": source.get("label"),
                "locator": source.get("locator"),
                "source_sha256": source.get("source_sha256"),
                "content_sha256": source.get("content_sha256"),
            }
        )
    return rows


def _validate_material_ledger(payload, snapshot):
    _exact(payload, ("source_assessments", "facts", "gaps", "sources"), path="payload")
    sources, _ = _snapshot_index(snapshot)
    _validate_trusted_sources(payload["sources"], sources, "payload.sources")
    assessments = _unique_rows(payload["source_assessments"], "source_id", "payload.source_assessments")
    for index, row in enumerate(assessments):
        _exact(row, ("source_id", "evidence_grade", "applicability", "status", "exclusion_reason"), path=f"payload.source_assessments.{index}")
        if row["source_id"] not in sources:
            raise StageArtifactError("unknown_source_id", f"payload.source_assessments.{index}.source_id")
        _enum(row["evidence_grade"], {"A", "B", "C"}, f"payload.source_assessments.{index}.evidence_grade")
        _string(row["applicability"], f"payload.source_assessments.{index}.applicability")
        _enum(row["status"], {"included", "excluded"}, f"payload.source_assessments.{index}.status")
        _string(row["exclusion_reason"], f"payload.source_assessments.{index}.exclusion_reason", nullable=True)
    facts = _unique_rows(payload["facts"], "fact_id", "payload.facts")
    for index, row in enumerate(facts):
        _exact(row, ("fact_id", "statement", "evidence_refs", "status", "usable"), path=f"payload.facts.{index}")
        _string(row["statement"], f"payload.facts.{index}.statement")
        _enum(row["status"], {"verified", "provided_unverified", "missing", "conflicted"}, f"payload.facts.{index}.status")
        if not isinstance(row["usable"], bool) or not isinstance(row["evidence_refs"], list):
            raise StageArtifactError("invalid_type", f"payload.facts.{index}")
        if row["status"] == "verified" and not row["evidence_refs"]:
            raise StageArtifactError("verified_without_evidence", f"payload.facts.{index}.evidence_refs")
        for ref_index, ref in enumerate(row["evidence_refs"]):
            _exact(ref, ("source_id", "segment_id", "segment_sha256", "locator", "relationship"), path=f"payload.facts.{index}.evidence_refs.{ref_index}")
    if not isinstance(payload["gaps"], list):
        raise StageArtifactError("invalid_type", "payload.gaps")
    for index, row in enumerate(payload["gaps"]):
        _exact(row, ("gap_id", "description", "blocks_final", "resolution"), path=f"payload.gaps.{index}")
        _string(row["gap_id"], f"payload.gaps.{index}.gap_id")
        _string(row["description"], f"payload.gaps.{index}.description")
        if not isinstance(row["blocks_final"], bool):
            raise StageArtifactError("invalid_type", f"payload.gaps.{index}.blocks_final")
        _string(row["resolution"], f"payload.gaps.{index}.resolution", nullable=True)


def _validate_trusted_sources(value, source_index, path):
    rows = _unique_rows(value, "source_id", path)
    if set(row["source_id"] for row in rows) != set(source_index):
        raise StageArtifactError("source_set_mismatch", path)
    for index, row in enumerate(rows):
        _exact(row, ("source_id", "kind", "label", "locator", "source_sha256", "content_sha256"), path=f"{path}.{index}")
        authoritative = source_index[row["source_id"]]
        for field in ("kind", "label", "locator", "source_sha256", "content_sha256"):
            if row[field] != authoritative.get(field):
                raise StageArtifactError("source_binding_mismatch", f"{path}.{index}.{field}")


def _validate_search_gaps(value, path):
    rows = _unique_rows(value, "gap_id", path)
    for index, row in enumerate(rows):
        item_path = f"{path}.{index}"
        _exact(row, ("gap_id", "question", "required", "blocks_final", "reason", "resolution_status", "source_ids"), path=item_path)
        _string(row["question"], f"{item_path}.question")
        if not isinstance(row["required"], bool) or not isinstance(row["blocks_final"], bool):
            raise StageArtifactError("invalid_type", item_path)
        _string(row["reason"], f"{item_path}.reason")
        _enum(row["resolution_status"], {"open", "covered_by_provided_sources", "accepted_out_of_scope"}, f"{item_path}.resolution_status")
        _string_list(row["source_ids"], f"{item_path}.source_ids", unique=True)


def _validate_source_assessments(value, source_index, path):
    rows = _unique_rows(value, "source_id", path)
    for index, row in enumerate(rows):
        item_path = f"{path}.{index}"
        _exact(row, ("source_id", "evidence_grade", "applicability", "status", "exclusion_reason"), path=item_path)
        if row["source_id"] not in source_index:
            raise StageArtifactError("unknown_source_id", f"{item_path}.source_id")
        _enum(row["evidence_grade"], {"A", "B", "C"}, f"{item_path}.evidence_grade")
        _string(row["applicability"], f"{item_path}.applicability")
        _enum(row["status"], {"included", "excluded"}, f"{item_path}.status")
        _string(row["exclusion_reason"], f"{item_path}.exclusion_reason", nullable=True)


def _validate_research_charter(payload, brief):
    _exact(payload, ("core_question", "decision_to_support", "scope_in", "scope_out", "time_range", "source_policy", "subquestions", "evaluation_criteria", "stop_conditions"), path="payload")
    _string(payload["core_question"], "payload.core_question")
    if payload["core_question"] != (brief.get("details") or {}).get("core_question"):
        raise StageArtifactError("brief_alignment_failed", "payload.core_question")
    _string(payload["decision_to_support"], "payload.decision_to_support")
    for field in ("scope_in", "scope_out", "subquestions", "evaluation_criteria", "stop_conditions"):
        _string_list(payload[field], f"payload.{field}")
    _exact(payload["time_range"], ("start", "end"), path="payload.time_range")
    if payload["time_range"] != (brief.get("details") or {}).get("time_range"):
        raise StageArtifactError("brief_alignment_failed", "payload.time_range")
    _exact(payload["source_policy"], ("mode", "as_of_date", "citation_style"), path="payload.source_policy")
    brief_policy = brief.get("source_policy") or {}
    if payload["source_policy"] != {key: brief_policy.get(key) for key in ("mode", "as_of_date", "citation_style")}:
        raise StageArtifactError("brief_alignment_failed", "payload.source_policy")


def _validate_source_register(payload, snapshot):
    _exact(payload, ("source_assessments", "search_gaps", "sources"), path="payload")
    source_index, _ = _snapshot_index(snapshot)
    _validate_source_assessments(payload["source_assessments"], source_index, "payload.source_assessments")
    _validate_search_gaps(payload["search_gaps"], "payload.search_gaps")
    _validate_trusted_sources(payload["sources"], source_index, "payload.sources")


def _validate_contradictions(value, path):
    rows = _unique_rows(value, "contradiction_id", path)
    for index, row in enumerate(rows):
        item_path = f"{path}.{index}"
        _exact(row, ("contradiction_id", "claim_id", "source_ids", "description", "resolution_status", "resolution", "chosen_source_ids"), path=item_path)
        _string(row["claim_id"], f"{item_path}.claim_id")
        _string_list(row["source_ids"], f"{item_path}.source_ids", unique=True)
        if len(row["source_ids"]) < 2:
            raise StageArtifactError("contradiction_sources_required", f"{item_path}.source_ids")
        _string(row["description"], f"{item_path}.description")
        _enum(row["resolution_status"], {"open", "resolved"}, f"{item_path}.resolution_status")
        _string(row["resolution"], f"{item_path}.resolution", nullable=True)
        _string_list(row["chosen_source_ids"], f"{item_path}.chosen_source_ids", unique=True)


def _validate_evidence_matrix(payload, snapshot):
    _exact(payload, ("claims", "contradictions", "gaps"), path="payload")
    claims = _unique_rows(payload["claims"], "claim_id", "payload.claims")
    for index, row in enumerate(claims):
        item_path = f"payload.claims.{index}"
        _exact(row, ("claim_id", "statement", "claim_type", "evidence", "status", "confidence", "notes"), path=item_path)
        _string(row["statement"], f"{item_path}.statement")
        _enum(row["claim_type"], {"fact", "estimate", "judgment"}, f"{item_path}.claim_type")
        if not isinstance(row["evidence"], list):
            raise StageArtifactError("invalid_type", f"{item_path}.evidence")
        for ref_index, ref in enumerate(row["evidence"]):
            _exact(ref, ("source_id", "segment_id", "segment_sha256", "locator", "relationship"), path=f"{item_path}.evidence.{ref_index}")
        _enum(row["status"], {"verified", "conflicted", "insufficient"}, f"{item_path}.status")
        _enum(row["confidence"], {"high", "medium", "low"}, f"{item_path}.confidence")
        _string(row["notes"], f"{item_path}.notes")
        if row["claim_type"] in {"fact", "estimate"} and row["status"] == "verified" and not row["evidence"]:
            raise StageArtifactError("verified_without_evidence", f"{item_path}.evidence")
    _validate_contradictions(payload["contradictions"], "payload.contradictions")
    _validate_search_gaps(payload["gaps"], "payload.gaps")
    _snapshot_index(snapshot)


def _validate_research_outline(payload):
    _exact(payload, ("sections", "conclusion_boundaries"), path="payload")
    rows = _unique_rows(payload["sections"], "section_id", "payload.sections")
    for index, row in enumerate(rows):
        item_path = f"payload.sections.{index}"
        _exact(row, ("section_id", "heading", "thesis", "claim_ids", "source_ids", "open_questions"), path=item_path)
        _string(row["heading"], f"{item_path}.heading")
        _string(row["thesis"], f"{item_path}.thesis")
        for field in ("claim_ids", "source_ids", "open_questions"):
            _string_list(row[field], f"{item_path}.{field}", unique=True)
    _string_list(payload["conclusion_boundaries"], "payload.conclusion_boundaries")


def _validate_delivery_manifest(payload):
    _exact(payload, ("schema_version", "delivery_binding_path", "delivery_binding_sha256", "render_input_fingerprint", "delivery_attempt", "document_revision", "automatic_check_summary", "office_review_required"), path="payload")
    if payload["schema_version"] != "delivery-manifest/v1":
        raise StageArtifactError("schema_version_mismatch", "payload.schema_version")
    path = payload["delivery_binding_path"]
    _string(path, "payload.delivery_binding_path")
    if path.startswith("/") or ".." in path.split("/"):
        raise StageArtifactError("unsafe_path", "payload.delivery_binding_path")
    for field in ("delivery_binding_sha256", "render_input_fingerprint"):
        if not _HEX64.fullmatch(str(payload[field])):
            raise StageArtifactError("invalid_sha256", f"payload.{field}")
    for field in ("delivery_attempt", "document_revision"):
        if not isinstance(payload[field], int) or payload[field] <= 0:
            raise StageArtifactError("invalid_type", f"payload.{field}")
    summary = payload["automatic_check_summary"]
    _exact(summary, ("status", "passed_count", "failed_count", "warning_count", "blocking_count"), path="payload.automatic_check_summary")
    _enum(summary["status"], {"passed", "failed"}, "payload.automatic_check_summary.status")
    for field in ("passed_count", "failed_count", "warning_count", "blocking_count"):
        if not isinstance(summary[field], int) or summary[field] < 0:
            raise StageArtifactError("invalid_type", f"payload.automatic_check_summary.{field}")
    if payload["office_review_required"] is not True:
        raise StageArtifactError("office_review_required", "payload.office_review_required")


def _validate_payload(artifact_type, payload, brief, source_snapshot):
    if artifact_type == "writing_plan":
        _validate_writing_plan(payload, brief)
    elif artifact_type == "material_ledger":
        _validate_material_ledger(payload, source_snapshot)
    elif artifact_type == "research_charter":
        _validate_research_charter(payload, brief)
    elif artifact_type == "source_register":
        _validate_source_register(payload, source_snapshot)
    elif artifact_type == "evidence_matrix":
        _validate_evidence_matrix(payload, source_snapshot)
    elif artifact_type == "research_outline":
        _validate_research_outline(payload)
    elif artifact_type == "document_draft":
        _validate_document_payload(payload, brief)
    elif artifact_type == "reviewed_document":
        _validate_document_payload(payload, brief, reviewed=True)
    elif artifact_type == "research_document_draft":
        _validate_document_payload(payload, brief, research=True)
    elif artifact_type == "reviewed_research_document":
        _validate_document_payload(payload, brief, reviewed=True, research=True)
    elif artifact_type == "delivery_manifest":
        _validate_delivery_manifest(payload)
    else:
        raise StageArtifactError("unsupported_artifact_type", "artifact_type")


def _input_refs(value):
    if not isinstance(value, list):
        raise StageArtifactError("invalid_type", "input_refs")
    identities = set()
    for index, ref in enumerate(value):
        path = f"input_refs.{index}"
        if not isinstance(ref, dict):
            raise StageArtifactError("invalid_type", path)
        if ref.get("ref_type") == "stage_artifact":
            _exact(ref, ("ref_type", "artifact_id", "sha256"), path=path)
            identity = ("stage_artifact", ref["artifact_id"])
        elif ref.get("ref_type") == "source_context":
            _exact(ref, ("ref_type", "snapshot_id", "sha256"), path=path)
            identity = ("source_context", ref["snapshot_id"])
        else:
            raise StageArtifactError("invalid_ref_type", f"{path}.ref_type")
        if not _HEX64.fullmatch(str(ref.get("sha256") or "")):
            raise StageArtifactError("invalid_sha256", f"{path}.sha256")
        if identity in identities:
            raise StageArtifactError("duplicate_id", path)
        identities.add(identity)


def document_purity_issues(markdown):
    if not isinstance(markdown, str):
        return [{"severity": "blocking", "code": "document_missing", "message": "正文缺失"}]
    issues = []
    for pattern, code in _PURITY_PATTERNS:
        if pattern.search(markdown):
            issues.append({"severity": "blocking", "code": code, "message": "正文包含内部流程或工具语言"})
    return issues


def unresolved_quality_issues(artifact):
    """Project model-produced stage issues into stable, non-waivable upstream targets."""

    result = []
    for issue in artifact.get("blocking_issues") or []:
        if not isinstance(issue, dict) or issue.get("severity") not in {"blocking", "error", "warning"}:
            continue
        result.append(
            {
                "code": "upstream_stage_issue",
                "target_id": f"stage-issue:{issue.get('issue_id') or 'unknown'}",
                "message": str(issue.get("message") or "阶段产物仍有未解决问题"),
            }
        )
    return result


def artifact_digest(artifact):
    payload = {key: value for key, value in artifact.items() if key != "sha256"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_stage_artifact(parsed, *, stage_id, stage_attempt, brief, input_refs, source_snapshot=None, now):
    if not isinstance(stage_attempt, int) or stage_attempt <= 0:
        raise StageArtifactError("invalid_stage_attempt", "stage_attempt")
    _string(stage_id, "stage_id")
    _input_refs(input_refs)
    if brief.get("status") != "confirmed" or not _HEX64.fullmatch(str(brief.get("confirmed_sha256") or "")):
        raise StageArtifactError("confirmed_brief_required", "brief")
    artifact_type = parsed.get("artifact_type")
    if artifact_type in _SOURCE_BOUND_TYPES:
        if not isinstance(source_snapshot, dict):
            raise StageArtifactError("source_snapshot_required", "source_snapshot")
        expected_snapshot_id = source_snapshot.get("snapshot_id")
        expected_sha = source_snapshot.get("snapshot_sha256") or source_snapshot.get("sha256")
        if not any(
            ref.get("ref_type") == "source_context"
            and ref.get("snapshot_id") == expected_snapshot_id
            and ref.get("sha256") == expected_sha
            for ref in input_refs
        ):
            raise StageArtifactError("source_snapshot_binding_mismatch", "input_refs")
    trusted = canonicalize_trusted_payload(parsed, artifact_type=artifact_type, source_snapshot=source_snapshot)
    _validate_payload(artifact_type, trusted["payload"], brief, source_snapshot)
    markdown = trusted.get("deliverable_markdown")
    if artifact_type in _DOCUMENT_TYPES:
        headings = re.findall(r"(?m)^#\s+(.+?)\s*$", markdown or "")
        if len(headings) != 1 or headings[0] != brief.get("exact_title"):
            raise StageArtifactError("title_mismatch", "deliverable_markdown")
        purity = document_purity_issues(markdown)
        if purity:
            raise StageArtifactError("document_purity_failed", "deliverable_markdown", purity[0]["message"])
    artifact = {
        "schema_version": STAGE_ARTIFACT_V1,
        "artifact_id": f"{stage_id}:{stage_attempt}",
        "artifact_type": artifact_type,
        "stage_id": stage_id,
        "stage_attempt": stage_attempt,
        "brief_revision": int(brief.get("confirmed_revision") or brief.get("revision") or 0),
        "brief_sha256": brief["confirmed_sha256"],
        "input_refs": deepcopy(input_refs),
        "summary": trusted["summary"],
        "payload": trusted["payload"],
        "deliverable_markdown": markdown,
        "blocking_issues": trusted["blocking_issues"],
        "created_at": str(now),
        "validation_status": "valid",
    }
    artifact["sha256"] = artifact_digest(artifact)
    validate_stage_artifact(artifact, brief=brief, approved_inputs=input_refs)
    return artifact


def validate_stage_artifact(artifact, *, brief, approved_inputs):
    del approved_inputs
    _exact(
        artifact,
        (
            "schema_version", "artifact_id", "artifact_type", "stage_id", "stage_attempt",
            "brief_revision", "brief_sha256", "input_refs", "summary", "payload",
            "deliverable_markdown", "blocking_issues", "created_at", "validation_status", "sha256",
        ),
        path="artifact",
    )
    if artifact.get("schema_version") != STAGE_ARTIFACT_V1:
        raise StageArtifactError("schema_version_mismatch", "schema_version")
    if artifact.get("sha256") != artifact_digest(artifact):
        raise StageArtifactError("artifact_hash_mismatch", "sha256")
    if artifact.get("artifact_id") != f"{artifact.get('stage_id')}:{artifact.get('stage_attempt')}":
        raise StageArtifactError("artifact_id_mismatch", "artifact_id")
    if artifact.get("brief_sha256") != brief.get("confirmed_sha256") or int(artifact.get("brief_revision") or 0) != int(brief.get("confirmed_revision") or brief.get("revision") or 0):
        raise StageArtifactError("brief_binding_mismatch", "brief_sha256")
    _input_refs(artifact.get("input_refs"))
    _issues(artifact.get("blocking_issues"))
    _enum(artifact.get("validation_status"), {"valid", "invalid"}, "validation_status")
    return {"valid": True, "blocking_count": sum(1 for issue in artifact["blocking_issues"] if issue["severity"] in {"blocking", "error", "warning"})}
