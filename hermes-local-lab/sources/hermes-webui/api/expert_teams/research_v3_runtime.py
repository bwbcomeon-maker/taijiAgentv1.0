"""Pure runtime adapters for the persisted research-report/v3 chapter ledger.

The outer runtime owns locks, state transitions and Provider calls.  This
module only validates frozen run data and applies the chapter-core transitions,
so checkpoint behavior stays small, deterministic and separately testable.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .research_chapters import (
    ChapterLedgerError,
    begin_unit_execution,
    bind_unit_stream,
    build_research_chapter_plan,
    complete_unit_execution,
    create_chapter_ledger,
    assemble_canonical_from_ledger,
    evaluate_body_word_count,
    mark_unit_retryable,
    pending_units,
    record_quality_review_binding,
)
from .research_contract import determine_research_result_grade, is_research_v3_run
from .research_provenance import (
    ProvenanceError,
    build_provenance_sidecar,
    mechanical_binding_validation,
    validate_independent_review,
)


_UNIT_OPEN = "<<<TAIJI_RESEARCH_V3_UNIT>>>"
_UNIT_CLOSE = "<<<TAIJI_RESEARCH_V3_UNIT_END>>>"
_REVIEW_OPEN = "<<<TAIJI_RESEARCH_V3_REVIEW>>>"
_REVIEW_CLOSE = "<<<TAIJI_RESEARCH_V3_REVIEW_END>>>"
_KIND_BY_HEADING = (
    ("背景", "background"),
    ("现状", "analysis"),
    ("问题", "analysis"),
    ("原因", "analysis"),
    ("比较", "comparison"),
    ("方案", "comparison"),
    ("建议", "recommendation"),
    ("判断", "recommendation"),
)
_STREAM_UNSET = object()


class ResearchV3RuntimeError(ValueError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def is_research_v3_draft_stage(run: object) -> bool:
    current = run.get("current_stage") if isinstance(run, dict) else None
    stage_id = str((current or {}).get("task_id") or (current or {}).get("id") or "")
    return is_research_v3_run(run) and stage_id == "draft"


def _approved_outline(run: dict) -> dict:
    refs = run.get("approved_stage_artifact_refs")
    ref = refs.get("outline") if isinstance(refs, dict) else None
    if not isinstance(ref, dict):
        raise ResearchV3RuntimeError("research_v3_outline_missing")
    artifact = next(
        (
            item
            for item in run.get("stage_artifacts") or []
            if isinstance(item, dict)
            and item.get("artifact_id") == ref.get("artifact_id")
            and item.get("sha256") == ref.get("sha256")
            and item.get("artifact_type") == "research_outline"
        ),
        None,
    )
    if not isinstance(artifact, dict):
        raise ResearchV3RuntimeError("research_v3_outline_binding_missing")
    from .stage_artifacts import artifact_digest

    if artifact.get("sha256") != artifact_digest(artifact):
        raise ResearchV3RuntimeError("research_v3_outline_hash_mismatch")
    evidence_ref = refs.get("evidence")
    if not isinstance(evidence_ref, dict) or not any(
        isinstance(item, dict)
        and item == {
            "ref_type": "stage_artifact",
            "artifact_id": evidence_ref.get("artifact_id"),
            "sha256": evidence_ref.get("sha256"),
        }
        for item in artifact.get("input_refs") or []
    ):
        raise ResearchV3RuntimeError("research_v3_outline_input_unverified")
    return artifact


def _chapter_kind(row: dict) -> str:
    declared = str(row.get("chapter_kind") or "").strip()
    if declared in {"background", "analysis", "comparison", "recommendation", "other"}:
        return declared
    heading = str(row.get("heading") or "")
    return next((kind for token, kind in _KIND_BY_HEADING if token in heading), "other")


def outline_rows_from_run(run: object) -> list[dict]:
    if not is_research_v3_draft_stage(run):
        raise ResearchV3RuntimeError("research_v3_draft_not_authoritative")
    payload = _approved_outline(run).get("payload")
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, list) or not sections:
        raise ResearchV3RuntimeError("research_v3_outline_invalid")
    rows = []
    for section in sections:
        if not isinstance(section, dict):
            raise ResearchV3RuntimeError("research_v3_outline_invalid")
        chapter_id = str(section.get("chapter_id") or section.get("section_id") or "").strip()
        title = str(section.get("heading") or section.get("title") or "").strip()
        if not chapter_id or not title:
            raise ResearchV3RuntimeError("research_v3_outline_invalid")
        rows.append({"chapter_id": chapter_id, "title": title, "kind": _chapter_kind(section)})
    return rows


def ensure_draft_ledger(run: object, parent: object) -> dict:
    if not is_research_v3_draft_stage(run):
        raise ResearchV3RuntimeError("research_v3_draft_not_authoritative")
    if not isinstance(parent, dict):
        raise ResearchV3RuntimeError("research_v3_parent_missing")
    ledger = run.get("research_v3_chapter_ledger") if isinstance(run, dict) else None
    if isinstance(ledger, dict):
        try:
            pending_units(ledger)
        except ChapterLedgerError as exc:
            raise ResearchV3RuntimeError(exc.code) from exc
        expected = {
            key: parent.get(key)
            for key in ("stage_id", "stage_attempt", "reservation_id", "input_binding_sha256")
        }
        if ledger.get("parent") != expected:
            raise ResearchV3RuntimeError("research_v3_parent_binding_mismatch")
        return deepcopy(ledger)
    contract = run.get("research_writing_contract") if isinstance(run, dict) else None
    try:
        plan = build_research_chapter_plan(contract, outline_rows_from_run(run))
        parent_identity = {
            key: parent.get(key)
            for key in (
                "stage_id",
                "stage_attempt",
                "reservation_id",
                "input_binding_sha256",
            )
        }
        return create_chapter_ledger(parent_identity, plan)
    except ChapterLedgerError as exc:
        raise ResearchV3RuntimeError(exc.code) from exc


def reserve_next_draft_unit(ledger: object, *, execution_start_id: str) -> dict:
    try:
        candidates = pending_units(ledger)
        if not candidates:
            raise ResearchV3RuntimeError("research_v3_draft_complete")
        return begin_unit_execution(
            ledger,
            unit_id=candidates[0]["unit_id"],
            execution_start_id=execution_start_id,
        )
    except ChapterLedgerError as exc:
        raise ResearchV3RuntimeError(exc.code) from exc


def bind_draft_unit_stream(
    ledger: object, *, unit_id: str, execution_start_id: str, stream_id: str
) -> dict:
    try:
        return bind_unit_stream(
            ledger,
            unit_id=unit_id,
            execution_start_id=execution_start_id,
            stream_id=stream_id,
        )
    except ChapterLedgerError as exc:
        raise ResearchV3RuntimeError(exc.code) from exc


def parse_draft_unit_packet(content: object) -> dict:
    if not isinstance(content, str):
        raise ResearchV3RuntimeError("research_v3_unit_protocol_invalid")
    match = re.fullmatch(
        rf"\s*{re.escape(_UNIT_OPEN)}\s*(.*?)\s*{re.escape(_UNIT_CLOSE)}\s*",
        content,
        flags=re.S,
    )
    try:
        # The ledger owns unit/start/stream identity.  A transport may preserve
        # the same strict JSON packet while dropping presentation delimiters;
        # accept that exact bare object but never surrounding prose or a
        # different schema.
        packet = json.loads(match.group(1) if match is not None else content.strip())
    except json.JSONDecodeError as exc:
        raise ResearchV3RuntimeError("research_v3_unit_protocol_invalid") from exc
    if (
        not isinstance(packet, dict)
        or set(packet) != {"body", "claim_usages"}
        or not isinstance(packet["body"], str)
        or not packet["body"].strip()
        or not isinstance(packet["claim_usages"], list)
    ):
        raise ResearchV3RuntimeError("research_v3_unit_protocol_invalid")
    return packet


def complete_draft_unit(
    ledger: object,
    *,
    unit_id: str,
    execution_start_id: str,
    stream_id: str,
    delivery_id: str,
    content: object,
) -> dict:
    packet = parse_draft_unit_packet(content)
    units = ledger.get("units") if isinstance(ledger, dict) else None
    unit = next(
        (
            item
            for item in units or []
            if isinstance(item, dict) and str(item.get("unit_id") or "") == str(unit_id or "")
        ),
        None,
    )
    chapter_id = str(unit.get("chapter_id") or "") if isinstance(unit, dict) else ""
    if not chapter_id:
        raise ResearchV3RuntimeError("research_v3_unit_missing")
    _validate_draft_unit_claim_usages(packet, chapter_id=chapter_id)
    try:
        return complete_unit_execution(
            ledger,
            unit_id=unit_id,
            execution_start_id=execution_start_id,
            stream_id=stream_id,
            result_packet=packet,
            receipt={"delivery_id": delivery_id},
        )
    except ChapterLedgerError as exc:
        raise ResearchV3RuntimeError(exc.code) from exc


def _validate_draft_unit_claim_usages(packet: dict, *, chapter_id: str) -> None:
    """Keep claim excerpts inside their producing chapter before persistence."""
    for claim in packet["claim_usages"]:
        if not isinstance(claim, dict) or set(claim) != {"claim_id", "usages"}:
            raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
        usages = claim.get("usages")
        if not isinstance(usages, list):
            raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
        for usage in usages:
            if not isinstance(usage, dict) or set(usage) != {
                "usage_id",
                "chapter_id",
                "rendered_excerpt",
            }:
                raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
            if usage.get("chapter_id") != chapter_id:
                raise ResearchV3RuntimeError("research_v3_unit_usage_chapter_mismatch")
            excerpt = usage.get("rendered_excerpt")
            if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in packet["body"]:
                raise ResearchV3RuntimeError("research_v3_unit_excerpt_not_in_body")


def invalid_draft_unit_claim_usage(ledger: object) -> dict | None:
    """Return the first persisted unit whose local provenance cannot reach review."""
    units = ledger.get("units") if isinstance(ledger, dict) else None
    if not isinstance(units, list):
        raise ResearchV3RuntimeError("research_v3_claim_ledger_required")
    for unit in units:
        packet = (
            unit.get("result", {}).get("packet")
            if isinstance(unit, dict) and isinstance(unit.get("result"), dict)
            else None
        )
        if not isinstance(packet, dict) or not isinstance(packet.get("claim_usages"), list):
            raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
        chapter_id = str(unit.get("chapter_id") or "")
        try:
            _validate_draft_unit_claim_usages(packet, chapter_id=chapter_id)
        except ResearchV3RuntimeError as exc:
            if exc.code in {
                "research_v3_unit_excerpt_not_in_body",
                "research_v3_unit_usage_chapter_mismatch",
            }:
                return {
                    "unit_id": str(unit.get("unit_id") or ""),
                    "chapter_id": chapter_id,
                    "code": exc.code,
                }
            raise
    return None


def mark_draft_unit_retryable(
    ledger: object,
    *,
    unit_id: str,
    execution_start_id: str,
    reason: str,
    stream_id: object = _STREAM_UNSET,
) -> dict:
    try:
        args = {
            "unit_id": unit_id,
            "execution_start_id": execution_start_id,
            "reason": reason,
        }
        if stream_id is not _STREAM_UNSET:
            args["stream_id"] = stream_id
        return mark_unit_retryable(ledger, **args)
    except ChapterLedgerError as exc:
        raise ResearchV3RuntimeError(exc.code) from exc


def parse_independent_review_packet(content: object) -> dict:
    """Accept one review-only packet; it can never masquerade as draft prose."""
    if not isinstance(content, str):
        raise ResearchV3RuntimeError("research_v3_review_protocol_invalid")
    match = re.fullmatch(
        rf"\s*(?:{re.escape(_REVIEW_OPEN)}|{re.escape(_REVIEW_OPEN[:-1])})\s*(.*?)\s*{re.escape(_REVIEW_CLOSE)}\s*",
        content,
        flags=re.S,
    )
    try:
        # The gateway may preserve the exact strict JSON response while
        # dropping its presentation delimiters.  This accepts only that bare
        # object, never prose surrounding it or a draft-shaped packet.
        review = json.loads(match.group(1) if match is not None else content.strip())
    except json.JSONDecodeError as exc:
        raise ResearchV3RuntimeError("research_v3_review_protocol_invalid") from exc
    if not isinstance(review, dict):
        raise ResearchV3RuntimeError("research_v3_review_protocol_invalid")
    return review


def build_independent_review_context(
    canonical: object,
    source_context: object,
    *,
    ledger: object,
    run_id: str,
    approved_claims: object,
    user_background_context: object,
) -> dict:
    """Build the immutable, separately stored material a reviewer must bind."""
    try:
        claim_usages = draft_claim_usages_from_ledger(ledger, approved_claims)
        sidecar = build_provenance_sidecar(
            canonical,
            source_context,
            claim_usages,
            run_id=run_id,
            approved_claims=approved_claims,
            user_background_context=user_background_context,
        )
        # There may be no drafted factual claim usages in a sparse-source
        # preliminary report.  The reviewer still receives every chapter and
        # must return a complete, reviewer-assessed verdict.
        mechanical = mechanical_binding_validation(canonical, sidecar)
    except ProvenanceError as exc:
        raise ResearchV3RuntimeError(exc.code) from exc
    return {"sidecar": sidecar, "mechanical_binding": mechanical}


def draft_claim_usages_from_ledger(ledger: object, approved_claims: object) -> list[dict]:
    """Collect only strict claim usages returned by completed draft units.

    ``canonical`` is deliberately accepted as the ledger-shaped object here so
    callers cannot add a claim independently of a unit result.  The public
    name documents the ownership boundary; the validation below enforces the
    packet shape before provenance resolves it against the evidence matrix.
    """
    if not isinstance(ledger, dict) or not isinstance(ledger.get("units"), list):
        # Runtime supplies its complete ledger.  Keeping this error distinct
        # prevents a review caller from passing a body and registering claims.
        raise ResearchV3RuntimeError("research_v3_claim_ledger_required")
    if not isinstance(approved_claims, list):
        raise ResearchV3RuntimeError("approved_claims_required")
    grouped: dict[str, list[dict]] = {}
    seen_usage_ids: set[str] = set()
    for unit in ledger["units"]:
        packet = (
            unit.get("result", {}).get("packet")
            if isinstance(unit, dict) and isinstance(unit.get("result"), dict)
            else None
        )
        if not isinstance(packet, dict) or not isinstance(packet.get("claim_usages"), list):
            raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
        for row in packet["claim_usages"]:
            if not isinstance(row, dict) or set(row) != {"claim_id", "usages"}:
                raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
            claim_id = row.get("claim_id")
            usages = row.get("usages")
            if not isinstance(claim_id, str) or not claim_id or not isinstance(usages, list) or not usages:
                raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
            target = grouped.setdefault(claim_id, [])
            for usage in usages:
                if not isinstance(usage, dict) or set(usage) != {"usage_id", "chapter_id", "rendered_excerpt"}:
                    raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
                usage_id = usage.get("usage_id")
                if not isinstance(usage_id, str) or usage_id in seen_usage_ids:
                    raise ResearchV3RuntimeError("research_v3_claim_packet_invalid")
                seen_usage_ids.add(usage_id)
                target.append(deepcopy(usage))
    return [{"claim_id": claim_id, "usages": usages} for claim_id, usages in grouped.items()]


def validate_independent_review_packet(
    ledger: object,
    canonical: object,
    sidecar: object,
    content: object,
) -> dict:
    """Validate a review packet and bind it to the exact current H2 body."""
    review = parse_independent_review_packet(content)
    try:
        validated = validate_independent_review(canonical, sidecar, review)
        binding = record_quality_review_binding(
            ledger,
            canonical_body_sha256=validated["canonical_body_sha256"],
            sidecar_sha256=validated["sidecar_sha256"],
        )
    except (ChapterLedgerError, ProvenanceError) as exc:
        raise ResearchV3RuntimeError(getattr(exc, "code", "research_v3_review_invalid")) from exc
    review_sha256 = hashlib.sha256(
        json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "review": validated,
        "review_sha256": review_sha256,
        "ledger": binding["ledger"],
    }


def finalize_draft_checkpoint(
    ledger: object,
    *,
    writing_contract: object,
    evidence_status: str,
) -> dict:
    """Assemble only the ledger's full ordered body and calculate its grade."""
    if not isinstance(writing_contract, dict):
        raise ResearchV3RuntimeError("research_v3_contract_missing")
    budget = writing_contract.get("word_budget")
    if not isinstance(budget, dict):
        raise ResearchV3RuntimeError("research_v3_contract_missing")
    try:
        canonical = assemble_canonical_from_ledger(ledger)
        count = evaluate_body_word_count(
            canonical["body"],
            {key: budget[key] for key in ("minimum", "target", "maximum")},
            body_scope={"body_markdown": canonical["body"]},
        )
        # Persist the identity of the exact H2 ledger body that was counted.
        # The count itself deliberately does not duplicate body text.
        count["canonical_body_sha256"] = canonical["body_sha256"]
    except (ChapterLedgerError, KeyError) as exc:
        raise ResearchV3RuntimeError(getattr(exc, "code", "research_v3_canonical_invalid")) from exc
    grade = determine_research_result_grade(
        evidence_status=evidence_status,
        factual_blockers=[],
        word_count_passed=count["formal_word_count_passed"],
        structure_quality_passed=True,
        human_or_model_review_passed=False,
    )
    return {
        "canonical": canonical,
        "body_count": count,
        "result_grade_before_review": grade,
        "canonical_sha256": hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
