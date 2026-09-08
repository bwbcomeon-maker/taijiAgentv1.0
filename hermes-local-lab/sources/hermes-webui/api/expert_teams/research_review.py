"""Checkpoint primitives for independent research-report/v3 chapter reviews."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .research_provenance import ProvenanceError, validate_independent_review


REVIEW_LEDGER_SCHEMA = "research-v3/review-ledger/v1"
_OPEN = "<<<TAIJI_RESEARCH_V3_REVIEW>>>"
_CLOSE = "<<<TAIJI_RESEARCH_V3_REVIEW_END>>>"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_HEADINGS = re.compile(r"^##\s+(.+?)\s*$", re.M)
_CORE_ROLE_BY_TITLE = {
    "背景、目的与范围": "background",
    "现状": "current",
    "问题与原因": "problem",
    "判断与建议": "recommendation",
}


class ReviewLedgerError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else _json(value)).hexdigest()


def _id(value: object) -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise ReviewLedgerError("invalid_id")
    return text


def _hash(value: object) -> str:
    text = str(value or "")
    if not _HEX.fullmatch(text):
        raise ReviewLedgerError("invalid_hash")
    return text


def _canonical(canonical: object) -> dict:
    if not isinstance(canonical, dict):
        raise ReviewLedgerError("invalid_canonical")
    body = canonical.get("body")
    ids = canonical.get("chapter_ids")
    if not isinstance(body, str) or not body.strip() or not isinstance(ids, list):
        raise ReviewLedgerError("invalid_canonical")
    chapter_ids = [_id(value) for value in ids]
    if not chapter_ids or len(chapter_ids) != len(set(chapter_ids)) or canonical.get("body_sha256") != _sha(body):
        raise ReviewLedgerError("invalid_canonical")
    headings = list(_HEADINGS.finditer(body))
    planned_headings = canonical.get("chapter_headings")
    chapter_kinds = canonical.get("chapter_kinds")
    if chapter_kinds is not None and (
        not isinstance(chapter_kinds, list)
        or len(chapter_kinds) != len(chapter_ids)
        or any(kind not in {"background", "analysis", "comparison", "recommendation", "other"} for kind in chapter_kinds)
    ):
        raise ReviewLedgerError("invalid_canonical")
    if planned_headings is not None:
        if (
            not isinstance(planned_headings, list)
            or len(planned_headings) != len(chapter_ids)
            or any(not isinstance(value, str) or not value.strip() for value in planned_headings)
        ):
            raise ReviewLedgerError("invalid_canonical")
        anchors, cursor = [], 0
        for title in planned_headings:
            matched = next(
                (heading for heading in headings[cursor:] if heading.group(1).strip() == title.strip()),
                None,
            )
            if matched is None:
                raise ReviewLedgerError("invalid_canonical")
            anchors.append(matched)
            cursor = headings.index(matched) + 1
    else:
        if len(headings) != len(chapter_ids):
            raise ReviewLedgerError("invalid_canonical")
        anchors = headings
    bodies, roles = {}, {}
    for index, chapter_id in enumerate(chapter_ids):
        # A heading alone is structural evidence only.  Quality gates require
        # actual chapter prose after the H2 line.
        start = anchors[index].end()
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(body)
        bodies[chapter_id] = body[start:end]
        title = anchors[index].group(1).strip()
        roles[chapter_id] = (
            chapter_kinds[index]
            if chapter_kinds is not None
            else _CORE_ROLE_BY_TITLE.get(title, "other")
        )
    return {"body": body, "body_sha256": canonical["body_sha256"], "chapter_ids": chapter_ids, "bodies": bodies, "roles": roles}


def _sidecar(sidecar: object, document: dict) -> dict:
    if not isinstance(sidecar, dict) or sidecar.get("canonical_body_sha256") != document["body_sha256"]:
        raise ReviewLedgerError("sidecar_binding_mismatch")
    digest = _hash(sidecar.get("sidecar_sha256"))
    usages = sidecar.get("usages")
    excerpts = sidecar.get("review_source_excerpts")
    if not isinstance(usages, list) or not isinstance(excerpts, list):
        raise ReviewLedgerError("invalid_sidecar")
    by_chapter = {chapter_id: [] for chapter_id in document["chapter_ids"]}
    for usage in usages:
        if not isinstance(usage, dict) or usage.get("chapter_id") not in by_chapter:
            raise ReviewLedgerError("invalid_sidecar")
        by_chapter[usage["chapter_id"]].append(_id(usage.get("usage_id")))
    refs = []
    for ref in excerpts:
        if not isinstance(ref, dict) or set(ref) != {"source_id", "segment_id", "text", "text_sha256"}:
            raise ReviewLedgerError("invalid_sidecar")
        refs.append({key: ref[key] for key in ("source_id", "segment_id", "text_sha256")})
    return {"sha256": digest, "usage_ids": by_chapter, "reviewed_source_refs": refs}


def _parent(parent: object) -> dict:
    if not isinstance(parent, dict) or set(parent) != {"stage_id", "stage_attempt", "reservation_id", "input_binding_sha256"}:
        raise ReviewLedgerError("invalid_parent")
    if _id(parent["stage_id"]) != "review" or type(parent["stage_attempt"]) is not int or parent["stage_attempt"] < 1:
        raise ReviewLedgerError("invalid_parent")
    return {"stage_id": "review", "stage_attempt": parent["stage_attempt"], "reservation_id": _id(parent["reservation_id"]), "input_binding_sha256": _hash(parent["input_binding_sha256"])}


def _input_sha(parent: dict, document: dict, sidecar: dict, chapter_id: str) -> str:
    return _sha({"parent_input": parent["input_binding_sha256"], "canonical_body_sha256": document["body_sha256"], "sidecar_sha256": sidecar["sha256"], "chapter_id": chapter_id})


def _substantive_chapter_body(body: str) -> bool:
    """Match the established document gate: prose/list/table cells, never headings."""
    stripped = re.sub(r"(?m)^#{3,6}[ \t]+.*$", "", body)
    stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.S)
    lines = stripped.splitlines()
    for index, line in enumerate(lines):
        candidate = line.strip()
        if not candidate or re.fullmatch(r"\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?", candidate):
            continue
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if "|" in candidate and re.fullmatch(r"\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?", following):
            continue
        candidate = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", candidate)
        candidate = re.sub(r"[|*_`\[\]()<>]", "", candidate).strip()
        if candidate:
            return True
    return False


def create_review_ledger(parent: object, canonical: object, sidecar: object) -> dict:
    checked_parent, document = _parent(parent), _canonical(canonical)
    checked_sidecar = _sidecar(sidecar, document)
    units = [
        {
            "unit_id": f"{chapter_id}:R01", "chapter_id": chapter_id,
            "unit_input_sha256": _input_sha(checked_parent, document, checked_sidecar, chapter_id),
            "unit_attempt": 0, "status": "pending", "execution": None, "result": None, "receipt": None,
        }
        for chapter_id in document["chapter_ids"]
    ]
    return {"schema_version": REVIEW_LEDGER_SCHEMA, "parent": checked_parent, "canonical_body_sha256": document["body_sha256"], "sidecar_sha256": checked_sidecar["sha256"], "chapter_ids": document["chapter_ids"], "units": units}


def _ledger(ledger: object) -> dict:
    if not isinstance(ledger, dict) or ledger.get("schema_version") != REVIEW_LEDGER_SCHEMA:
        raise ReviewLedgerError("invalid_review_ledger")
    result = deepcopy(ledger)
    parent, body_sha, sidecar_sha = _parent(result.get("parent")), _hash(result.get("canonical_body_sha256")), _hash(result.get("sidecar_sha256"))
    ids = [_id(value) for value in result.get("chapter_ids") or []]
    if not ids or len(ids) != len(set(ids)) or not isinstance(result.get("units"), list) or len(result["units"]) != len(ids):
        raise ReviewLedgerError("invalid_review_ledger")
    for unit, chapter_id in zip(result["units"], ids):
        if not isinstance(unit, dict) or set(unit) != {"unit_id", "chapter_id", "unit_input_sha256", "unit_attempt", "status", "execution", "result", "receipt"}:
            raise ReviewLedgerError("invalid_review_ledger")
        if _id(unit["unit_id"]) != f"{chapter_id}:R01" or _id(unit["chapter_id"]) != chapter_id or _hash(unit["unit_input_sha256"]) != _input_sha(parent, {"body_sha256": body_sha}, {"sha256": sidecar_sha}, chapter_id):
            raise ReviewLedgerError("review_input_binding_mismatch")
        if type(unit["unit_attempt"]) is not int or unit["unit_attempt"] < 0 or unit["status"] not in {"pending", "running", "retryable", "completed"}:
            raise ReviewLedgerError("invalid_review_ledger")
    return result


def pending_review_units(ledger: object) -> list[dict]:
    return [deepcopy(unit) for unit in _ledger(ledger)["units"] if unit["status"] != "completed"]


def begin_review_unit(ledger: object, *, execution_start_id: str) -> dict:
    result = _ledger(ledger)
    unit = next((item for item in result["units"] if item["status"] != "completed"), None)
    if unit is None:
        raise ReviewLedgerError("review_complete")
    if unit["status"] == "running":
        raise ReviewLedgerError("review_unit_running")
    start = _id(execution_start_id)
    unit["unit_attempt"] += 1
    unit["status"], unit["execution"] = "running", {"execution_start_id": start, "stream_id": None}
    return {"ledger": result, "unit": deepcopy(unit)}


def bind_review_unit_stream(ledger: object, *, unit_id: str, execution_start_id: str, stream_id: str) -> dict:
    result = _ledger(ledger)
    unit = next((item for item in result["units"] if item["unit_id"] == _id(unit_id)), None)
    if unit is None or unit["status"] != "running" or unit["execution"] != {"execution_start_id": _id(execution_start_id), "stream_id": None}:
        raise ReviewLedgerError("review_stream_mismatch")
    unit["execution"]["stream_id"] = _id(stream_id)
    return {"ledger": result, "unit": deepcopy(unit)}


def mark_review_unit_retryable(
    ledger: object,
    *,
    unit_id: str,
    execution_start_id: str,
    reason: str,
    stream_id: str | None = None,
) -> dict:
    """Release only the exact review call that has reached a known terminal failure."""
    result = _ledger(ledger)
    unit = next((item for item in result["units"] if item["unit_id"] == _id(unit_id)), None)
    if (
        unit is None
        or unit["status"] != "running"
        or unit["execution"] != {"execution_start_id": _id(execution_start_id), "stream_id": stream_id}
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ReviewLedgerError("review_stream_mismatch")
    unit["status"], unit["execution"] = "retryable", None
    return {"ledger": result, "unit": deepcopy(unit)}


def _packet(content: object, document: dict, sidecar: dict, chapter_id: str) -> dict:
    if not isinstance(content, str):
        raise ReviewLedgerError("review_protocol_invalid")
    match = re.fullmatch(
        rf"\s*(?:{re.escape(_OPEN)}|{re.escape(_OPEN[:-1])})\s*(.*?)\s*{re.escape(_CLOSE)}\s*",
        content,
        re.S,
    )
    try:
        # A transport may strip only the review delimiters.  Preserve the
        # strict packet contract by accepting the complete bare JSON object,
        # while json.loads still rejects any surrounding explanation.
        packet = json.loads(match.group(1) if match is not None else content.strip())
    except json.JSONDecodeError as exc:
        raise ReviewLedgerError("review_protocol_invalid") from exc
    expected = {"reviewer_assessed", "canonical_body_sha256", "sidecar_sha256", "chapter_id", "usage_ids", "reviewed_source_refs", "findings", "quality_assessment"}
    quality = packet.get("quality_assessment") if isinstance(packet, dict) else None
    if not isinstance(packet, dict) or set(packet) != expected or packet.get("reviewer_assessed") is not True or packet.get("canonical_body_sha256") != document["body_sha256"] or packet.get("sidecar_sha256") != sidecar["sha256"] or packet.get("chapter_id") != chapter_id or not isinstance(packet.get("usage_ids"), list) or set(packet["usage_ids"]) != set(sidecar["usage_ids"][chapter_id]) or len(packet["usage_ids"]) != len(set(packet["usage_ids"])) or not isinstance(packet.get("reviewed_source_refs"), list) or any(not isinstance(ref, dict) or set(ref) != {"source_id", "segment_id", "text_sha256"} or not _ID.fullmatch(str(ref.get("source_id") or "")) or not _ID.fullmatch(str(ref.get("segment_id") or "")) or not _HEX.fullmatch(str(ref.get("text_sha256") or "")) for ref in packet["reviewed_source_refs"]) or not isinstance(packet.get("findings"), list) or len(packet["findings"]) != 1 or not isinstance(quality, dict) or set(quality) != {"formal_style", "analysis_depth", "recommendation_applicability", "issues"} or any(quality.get(key) not in {"passed", "concern", "failed", "not_applicable"} for key in ("formal_style", "analysis_depth", "recommendation_applicability")) or not isinstance(quality.get("issues"), list) or any(not isinstance(issue, str) or not issue.strip() for issue in quality["issues"]) or (any(quality.get(key) == "concern" for key in ("formal_style", "analysis_depth", "recommendation_applicability")) and not quality["issues"]):
        raise ReviewLedgerError("review_protocol_invalid")
    finding = packet["findings"][0]
    if not isinstance(finding, dict) or set(finding) != {"finding_id", "verdict", "rationale", "revision_required"} or not isinstance(finding.get("finding_id"), str) or finding["verdict"] not in {"supported", "concern", "blocked", "not_checked"} or not isinstance(finding.get("rationale"), str) or not finding["rationale"].strip() or type(finding.get("revision_required")) is not bool:
        raise ReviewLedgerError("review_protocol_invalid")
    if any(quality[key] == "failed" for key in ("formal_style", "analysis_depth", "recommendation_applicability")) and not finding["revision_required"]:
        raise ReviewLedgerError("review_quality_outcome_conflict")
    return packet


def complete_review_unit(
    ledger: object,
    *,
    canonical: object,
    sidecar: object,
    unit_id: str,
    execution_start_id: str,
    stream_id: str,
    delivery_id: str,
    stage_id: str,
    execution_attempt: int,
    content: object,
) -> dict:
    result, document = _ledger(ledger), _canonical(canonical)
    checked_sidecar = _sidecar(sidecar, document)
    if result["canonical_body_sha256"] != document["body_sha256"] or result["sidecar_sha256"] != checked_sidecar["sha256"]:
        raise ReviewLedgerError("review_input_binding_mismatch")
    unit = next((item for item in result["units"] if item["unit_id"] == _id(unit_id)), None)
    if unit is None or unit["status"] != "running" or unit["execution"] != {"execution_start_id": _id(execution_start_id), "stream_id": _id(stream_id)}:
        raise ReviewLedgerError("review_stream_mismatch")
    packet = _packet(content, document, checked_sidecar, unit["chapter_id"])
    if _id(stage_id) != "review" or type(execution_attempt) is not int or execution_attempt < 1:
        raise ReviewLedgerError("review_receipt_invalid")
    receipt = {
        "delivery_id": _id(delivery_id),
        "stage_id": "review",
        "execution_attempt": execution_attempt,
        "content_sha256": _sha(content),
        "packet_sha256": _sha(packet),
        "execution_start_id": _id(execution_start_id),
        "stream_id": _id(stream_id),
    }
    unit["status"], unit["execution"], unit["result"], unit["receipt"] = "completed", None, packet, receipt
    return {"ledger": result, "unit": deepcopy(unit)}


def aggregate_review_ledger(ledger: object, *, canonical: object, sidecar: object) -> dict:
    result, document = _ledger(ledger), _canonical(canonical)
    checked_sidecar = _sidecar(sidecar, document)
    if result["canonical_body_sha256"] != document["body_sha256"] or result["sidecar_sha256"] != checked_sidecar["sha256"] or any(unit["status"] != "completed" for unit in result["units"]):
        raise ReviewLedgerError("review_incomplete")
    findings, seen_refs, quality_rows = [], set(), []
    for unit in result["units"]:
        packet, finding = unit["result"], unit["result"]["findings"][0]
        for ref in packet["reviewed_source_refs"]:
            if not isinstance(ref, dict) or set(ref) != {"source_id", "segment_id", "text_sha256"}:
                raise ReviewLedgerError("review_protocol_invalid")
            seen_refs.add((ref["source_id"], ref["segment_id"], ref["text_sha256"]))
        findings.append({"finding_id": finding["finding_id"], "chapter_id": unit["chapter_id"], "usage_ids": packet["usage_ids"], "verdict": finding["verdict"], "rationale": finding["rationale"], "revision_required": finding["revision_required"]})
        quality_rows.append({"chapter_id": unit["chapter_id"], **deepcopy(packet["quality_assessment"])})
    expected_refs = checked_sidecar["reviewed_source_refs"]
    if seen_refs != {(ref["source_id"], ref["segment_id"], ref["text_sha256"]) for ref in expected_refs}:
        raise ReviewLedgerError("review_source_coverage_incomplete")
    review = {"reviewer_assessed": True, "review_passed": all(item["verdict"] == "supported" and not item["revision_required"] for item in findings), "canonical_body_sha256": document["body_sha256"], "sidecar_sha256": checked_sidecar["sha256"], "chapter_ids": document["chapter_ids"], "usage_ids": [usage for chapter_id in document["chapter_ids"] for usage in checked_sidecar["usage_ids"][chapter_id]], "reviewed_source_refs": expected_refs, "findings": findings}
    try:
        validated = validate_independent_review(canonical, sidecar, review)
    except ProvenanceError as exc:
        raise ReviewLedgerError(exc.code) from exc
    structure = {
        "core_chapters_present": (
            {"background", "analysis", "comparison"}.issubset(set(document["roles"].values()))
            and (
                "recommendation" in set(document["roles"].values())
                or any("建议" in body for body in document["bodies"].values())
            )
        ),
        "all_chapter_bodies_present": all(_substantive_chapter_body(document["bodies"][chapter_id]) for chapter_id in document["chapter_ids"]),
    }
    structure["passed"] = all(structure.values())
    reviewer_quality_passed = all(
        row["formal_style"] == "passed"
        and row["analysis_depth"] == "passed"
        and (
            row["recommendation_applicability"] == "passed"
            if document["roles"].get(row["chapter_id"]) == "recommendation"
            else row["recommendation_applicability"] in {"passed", "not_applicable"}
        )
        and not row["issues"]
        for row in quality_rows
    )
    return {"ledger": result, "review": validated, "structure": structure, "chapter_quality": quality_rows, "reviewer_quality_passed": reviewer_quality_passed}
