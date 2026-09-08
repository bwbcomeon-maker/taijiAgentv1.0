"""v3 provenance sidecar and independent-review binding primitives.

The runtime supplies an already trusted ``source_context`` snapshot (normally
verified with ``verify_source_context_snapshot``) and confirmed/user input
contexts.  This module re-computes source, segment, body-anchor and artifact
hashes; it never treats model-supplied hashes as evidence.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re


PROVENANCE_SCHEMA = "research-v3/provenance-sidecar/v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CHAPTER_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)


class ProvenanceError(ValueError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code if not message else f"{code}: {message}")
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else _canonical(value)).hexdigest()


def _id(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise ProvenanceError("invalid_id", field)
    return text


def _hash(value: object, field: str) -> str:
    text = str(value or "")
    if not _HEX64.fullmatch(text):
        raise ProvenanceError("invalid_hash", field)
    return text


def _canonical_document(canonical: object) -> dict:
    if not isinstance(canonical, dict) or set(canonical) - {"schema_version", "plan_sha256", "body", "chapter_ids", "chapter_headings", "chapter_kinds", "body_sha256"}:
        raise ProvenanceError("invalid_canonical")
    body, chapter_ids = canonical.get("body"), canonical.get("chapter_ids")
    if not isinstance(body, str) or not body.strip() or not isinstance(chapter_ids, list) or not chapter_ids:
        raise ProvenanceError("invalid_canonical")
    ids = [_id(value, "chapter_id") for value in chapter_ids]
    if len(ids) != len(set(ids)):
        raise ProvenanceError("duplicate_chapter")
    digest = _sha(body)
    if canonical.get("body_sha256") not in {None, digest}:
        raise ProvenanceError("canonical_hash_mismatch")
    if canonical.get("schema_version") not in {None, "research-v3/canonical-document/v1"}:
        raise ProvenanceError("invalid_canonical")
    if canonical.get("plan_sha256") is not None:
        _hash(canonical["plan_sha256"], "plan_sha256")
    headings = list(_CHAPTER_HEADING.finditer(body))
    planned_headings = canonical.get("chapter_headings")
    chapter_kinds = canonical.get("chapter_kinds")
    if chapter_kinds is not None and (
        not isinstance(chapter_kinds, list)
        or len(chapter_kinds) != len(ids)
        or any(item not in {"background", "analysis", "comparison", "recommendation", "other"} for item in chapter_kinds)
    ):
        raise ProvenanceError("invalid_canonical")
    if planned_headings is not None:
        if (
            not isinstance(planned_headings, list)
            or len(planned_headings) != len(ids)
            or any(not isinstance(value, str) or not value.strip() for value in planned_headings)
        ):
            raise ProvenanceError("invalid_canonical")
        anchors, cursor = [], 0
        for title in planned_headings:
            matched = next(
                (
                    heading
                    for heading in headings[cursor:]
                    if heading.group(1).strip() == title.strip()
                ),
                None,
            )
            if matched is None:
                raise ProvenanceError("chapter_anchor_unavailable")
            anchors.append({"title": title.strip(), "start": matched.start()})
            cursor = headings.index(matched) + 1
    else:
        # Historical canonical documents had no plan headings.  They remain
        # reviewable only when duplicate H2s are consecutive; a different extra
        # H2 continues to fail the anchor contract.
        anchors = []
        for heading in headings:
            title = heading.group(1).strip()
            if not anchors or anchors[-1]["title"] != title:
                anchors.append({"title": title, "start": heading.start()})
    if len(anchors) != len(ids):
        raise ProvenanceError("chapter_anchor_unavailable")
    chapter_ranges = {}
    for index, (chapter_id, anchor) in enumerate(zip(ids, anchors)):
        end = anchors[index + 1]["start"] if index + 1 < len(anchors) else len(body)
        chapter_ranges[chapter_id] = (anchor["start"], end)
    return {"body": body, "chapter_ids": ids, "body_sha256": digest, "chapter_ranges": chapter_ranges}


def _segment(source_id: str, row: object, source_text: str) -> dict:
    if not isinstance(row, dict) or set(row) - {"segment_id", "char_start", "char_end", "locator", "text", "text_sha256"}:
        raise ProvenanceError("invalid_segment")
    segment_id = _id(row.get("segment_id"), "segment_id")
    start, end, text = row.get("char_start"), row.get("char_end"), row.get("text")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source_text) or not isinstance(text, str):
        raise ProvenanceError("invalid_segment")
    if source_text[start:end] != text or _sha(text) != row.get("text_sha256"):
        raise ProvenanceError("segment_hash_mismatch")
    locator = row.get("locator") if isinstance(row.get("locator"), str) else ""
    return {"source_id": source_id, "segment_id": segment_id, "char_start": start, "char_end": end, "locator": locator, "text": text, "text_sha256": _sha(text)}


def _source_context_index(source_context: object) -> tuple[dict, str]:
    if not isinstance(source_context, dict) or not isinstance(source_context.get("sources"), list):
        raise ProvenanceError("source_context_required")
    index = {}
    origin_by_kind = {
        "approved_public": "public_web",
        "approved_internal": "local_knowledge",
        # An uploaded research attachment is a frozen user-provided record.
        # It is traceable and usable as evidence, but never an external
        # verification source.
        "attachment": "user_background",
    }
    for source in source_context["sources"]:
        if not isinstance(source, dict):
            raise ProvenanceError("invalid_source")
        source_id, kind, text = _id(source.get("source_id"), "source_id"), source.get("kind"), source.get("content_text")
        if source_id in index or kind not in origin_by_kind or not isinstance(text, str) or not text.strip():
            raise ProvenanceError("invalid_source")
        if _sha(text) != source.get("content_sha256"):
            raise ProvenanceError("source_hash_mismatch")
        segments = source.get("segments")
        if not isinstance(segments, list) or not segments:
            raise ProvenanceError("invalid_source")
        parsed = [_segment(source_id, value, text) for value in segments]
        if len({value["segment_id"] for value in parsed}) != len(parsed):
            raise ProvenanceError("duplicate_segment")
        index[source_id] = {
            "source_id": source_id,
            "origin_tier": origin_by_kind[kind],
            "external_verification": "not_performed" if kind == "attachment" else "bound_source_context",
            "content_sha256": _sha(text),
            "content_text": text, "segments": parsed,
        }
    return index, _sha(source_context)


def _background_index(context: object, run_id: str) -> dict:
    if context is None:
        return {}
    if not isinstance(context, list):
        raise ProvenanceError("user_background_binding")
    result = {}
    for row in context:
        if not isinstance(row, dict) or set(row) != {"source_id", "run_id", "input_ref", "revision", "content_text"}:
            raise ProvenanceError("user_background_binding")
        source_id, bound_run, input_ref, revision, text = _id(row["source_id"], "source_id"), row["run_id"], row["input_ref"], row["revision"], row["content_text"]
        if source_id in result or bound_run != run_id or not isinstance(input_ref, str) or not input_ref.strip() or type(revision) is not int or revision < 1 or not isinstance(text, str) or not text.strip():
            raise ProvenanceError("user_background_binding")
        result[source_id] = {
            "source_id": source_id, "origin_tier": "user_background", "external_verification": "not_performed",
            "input_ref": input_ref.strip(), "revision": revision, "content_sha256": _sha(text), "content_text": text,
            "segments": [{"source_id": source_id, "segment_id": f"{source_id}:INPUT", "char_start": 0, "char_end": len(text), "locator": input_ref.strip(), "text": text, "text_sha256": _sha(text)}],
        }
    return result


def _source_view(source: dict) -> dict:
    result = {key: source[key] for key in ("source_id", "origin_tier", "external_verification", "content_sha256")}
    # Confirmed-brief background has an input revision; a frozen attachment is
    # also user background but is identified by its immutable source snapshot.
    if source["origin_tier"] == "user_background" and "input_ref" in source and "revision" in source:
        result["input_ref"], result["revision"] = source["input_ref"], source["revision"]
    return result


def _usage_anchor(document: dict, chapter_id: str, excerpt: str) -> dict:
    if chapter_id not in document["chapter_ranges"]:
        raise ProvenanceError("unknown_chapter")
    start, end = document["chapter_ranges"][chapter_id]
    position = document["body"].find(excerpt, start, end)
    if position < 0:
        raise ProvenanceError("excerpt_not_in_body")
    return {"chapter_id": chapter_id, "char_start": position, "char_end": position + len(excerpt), "text_sha256": _sha(excerpt)}


def _approved_claim_index(approved_claims: object, trusted: dict) -> dict:
    """Normalize the already-approved evidence matrix; draft calls cannot register claims."""
    if not isinstance(approved_claims, list):
        raise ProvenanceError("approved_claims_required")
    result = {}
    fields = {"claim_id", "statement", "claim_type", "evidence", "status", "confidence", "notes", "origin_tier"}
    for row in approved_claims:
        if not isinstance(row, dict) or set(row) != fields:
            raise ProvenanceError("invalid_approved_claim")
        claim_id, statement, origin = _id(row["claim_id"], "claim_id"), row["statement"], row["origin_tier"]
        if claim_id in result or not isinstance(statement, str) or not statement.strip() or row["claim_type"] not in {"fact", "estimate", "analysis", "recommendation"} or row["status"] not in {"verified", "unverified", "contested", "blocked"} or row["confidence"] not in {"high", "medium", "low"} or not isinstance(row["notes"], str) or origin not in {"public_web", "local_knowledge", "user_background", "model_knowledge"} or not isinstance(row["evidence"], list):
            raise ProvenanceError("invalid_approved_claim")
        refs, evidence_refs, review_segments = [], [], []
        if origin == "model_knowledge":
            if row["evidence"] or row["status"] == "verified":
                raise ProvenanceError("model_knowledge_false_evidence")
        else:
            if not row["evidence"]:
                raise ProvenanceError("missing_source")
            for evidence in row["evidence"]:
                if not isinstance(evidence, dict) or set(evidence) != {"source_id", "segment_id", "segment_sha256", "locator", "relationship"}:
                    raise ProvenanceError("invalid_approved_claim")
                source_id, segment_id = _id(evidence["source_id"], "source_id"), _id(evidence["segment_id"], "segment_id")
                if source_id not in trusted:
                    raise ProvenanceError("unknown_source")
                source = trusted[source_id]
                if source["origin_tier"] != origin:
                    raise ProvenanceError("source_origin_mismatch")
                segment = next((item for item in source["segments"] if item["segment_id"] == segment_id), None)
                if segment is None or evidence["segment_sha256"] != segment["text_sha256"] or evidence["locator"] != segment["locator"] or evidence["relationship"] not in {"supports", "contradicts", "context"}:
                    raise ProvenanceError("source_hash_mismatch")
                refs.append(_source_view(source))
                evidence_refs.append({
                    "source_id": source_id, "segment_id": segment_id,
                    "segment_sha256": segment["text_sha256"], "locator": segment["locator"],
                    "relationship": evidence["relationship"],
                })
                review_segments.append({key: segment[key] for key in ("source_id", "segment_id", "text", "text_sha256")})
            if len({(item["source_id"], item["segment_id"]) for item in review_segments}) != len(review_segments):
                raise ProvenanceError("duplicate_evidence")
        result[claim_id] = {
            "claim_id": claim_id, "original_statement": statement.strip(), "origin_tier": origin,
            "external_verification": "not_performed" if origin in {"user_background", "model_knowledge"} else "bound_source_context",
            "claim_type": row["claim_type"], "status": row["status"], "confidence": row["confidence"], "notes": row["notes"],
            "source_refs": refs, "evidence_refs": evidence_refs, "review_segments": review_segments,
        }
    return result


def build_provenance_sidecar(canonical: object, source_context: object, claims: object, *, run_id: str, approved_claims: object, user_background_context: object = None) -> dict:
    """Build v3's separate trace sidecar from trusted source and brief contexts.

    Draft calls can supply only a pre-approved claim ID and visible excerpts.
    Original statements, sources, hashes and tiers are derived from the trusted
    evidence matrix and source/brief contexts.
    """
    document = _canonical_document(canonical)
    trusted, context_sha = _source_context_index(source_context)
    trusted.update(_background_index(user_background_context, _id(run_id, "run_id")))
    approved = _approved_claim_index(approved_claims, trusted)
    if not isinstance(claims, list):
        raise ProvenanceError("invalid_claims")
    claim_rows, usage_rows, seen_claims, seen_usages = [], [], set(), set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"claim_id", "usages"}:
            raise ProvenanceError("invalid_claim")
        claim_id = _id(claim["claim_id"], "claim_id")
        usages = claim["usages"]
        if claim_id not in approved:
            raise ProvenanceError("unknown_claim")
        if claim_id in seen_claims or not isinstance(usages, list) or not usages:
            raise ProvenanceError("invalid_claim")
        seen_claims.add(claim_id)
        claim_rows.append({key: deepcopy(value) for key, value in approved[claim_id].items() if key != "review_segments"})
        for usage in usages:
            if not isinstance(usage, dict) or set(usage) != {"usage_id", "chapter_id", "rendered_excerpt"}:
                raise ProvenanceError("invalid_usage")
            usage_id, chapter_id, excerpt = _id(usage["usage_id"], "usage_id"), _id(usage["chapter_id"], "chapter_id"), usage["rendered_excerpt"]
            if usage_id in seen_usages or not isinstance(excerpt, str) or not excerpt.strip():
                raise ProvenanceError("duplicate_usage" if usage_id in seen_usages else "invalid_usage")
            seen_usages.add(usage_id)
            usage_rows.append({"usage_id": usage_id, "claim_id": claim_id, "rendered_excerpt": excerpt, **_usage_anchor(document, chapter_id, excerpt)})
    review_source_excerpts, seen_review_segments = [], set()
    for claim_id in [claim["claim_id"] for claim in claim_rows]:
        for segment in approved[claim_id]["review_segments"]:
            key = (segment["source_id"], segment["segment_id"])
            if key not in seen_review_segments:
                review_source_excerpts.append(segment)
                seen_review_segments.add(key)
    sidecar_without_sha = {
        "schema_version": PROVENANCE_SCHEMA, "run_id": _id(run_id, "run_id"),
        "canonical_body_sha256": document["body_sha256"], "source_context_sha256": context_sha,
        "sources": sorted([_source_view(value) for value in trusted.values()], key=lambda row: row["source_id"]),
        "claims": claim_rows, "usages": usage_rows,
        "review_source_excerpts": review_source_excerpts,
    }
    return {**sidecar_without_sha, "sidecar_sha256": _sha(sidecar_without_sha)}


def _sidecar(sidecar: object, document: dict) -> dict:
    if not isinstance(sidecar, dict) or sidecar.get("schema_version") != PROVENANCE_SCHEMA:
        raise ProvenanceError("invalid_sidecar")
    rendered = {key: deepcopy(value) for key, value in sidecar.items() if key != "sidecar_sha256"}
    if _sha(rendered) != sidecar.get("sidecar_sha256"):
        raise ProvenanceError("sidecar_hash_mismatch")
    if sidecar.get("canonical_body_sha256") != document["body_sha256"]:
        raise ProvenanceError("canonical_hash_mismatch")
    usages = sidecar.get("usages")
    if not isinstance(usages, list):
        raise ProvenanceError("invalid_sidecar")
    claims = sidecar.get("claims")
    if not isinstance(claims, list):
        raise ProvenanceError("invalid_sidecar")
    claim_ids = {claim.get("claim_id") for claim in claims if isinstance(claim, dict)}
    seen = set()
    for usage in usages:
        if not isinstance(usage, dict) or set(usage) != {"usage_id", "claim_id", "rendered_excerpt", "chapter_id", "char_start", "char_end", "text_sha256"}:
            raise ProvenanceError("invalid_sidecar")
        if usage["usage_id"] in seen:
            raise ProvenanceError("duplicate_usage")
        seen.add(usage["usage_id"])
        if usage["claim_id"] not in claim_ids:
            raise ProvenanceError("unknown_claim")
        expected = _usage_anchor(document, usage["chapter_id"], usage["rendered_excerpt"])
        if any(usage[key] != expected[key] for key in ("chapter_id", "char_start", "char_end", "text_sha256")):
            raise ProvenanceError("usage_anchor_mismatch")
    excerpts = sidecar.get("review_source_excerpts")
    if not isinstance(excerpts, list):
        raise ProvenanceError("invalid_sidecar")
    seen_excerpts = set()
    for excerpt in excerpts:
        if not isinstance(excerpt, dict) or set(excerpt) != {"source_id", "segment_id", "text", "text_sha256"}:
            raise ProvenanceError("invalid_sidecar")
        key = (_id(excerpt["source_id"], "source_id"), _id(excerpt["segment_id"], "segment_id"))
        if key in seen_excerpts or not isinstance(excerpt["text"], str) or _sha(excerpt["text"]) != excerpt["text_sha256"]:
            raise ProvenanceError("invalid_sidecar")
        seen_excerpts.add(key)
    return deepcopy(sidecar)


def mechanical_binding_validation(canonical: object, sidecar: object) -> dict:
    """Return a mechanical report only; it intentionally makes no semantic claim."""
    document = _canonical_document(canonical)
    checked = _sidecar(sidecar, document)
    coverage = {chapter_id: [] for chapter_id in document["chapter_ids"]}
    for usage in checked["usages"]:
        coverage[usage["chapter_id"]].append(usage["usage_id"])
    return {
        "validation_kind": "mechanical_binding_validation", "passed": True,
        "semantic_equivalence_assessed": False, "canonical_body_sha256": document["body_sha256"],
        "sidecar_sha256": checked["sidecar_sha256"], "chapter_usage_coverage": coverage,
        "notice": "机械绑定通过只证明身份、锚点与引用片段存在，不证明原文语义支持正文判断。",
    }


def validate_independent_review(canonical: object, sidecar: object, review: object) -> dict:
    """Validate a separately produced reviewer assessment against current bytes and coverage."""
    document = _canonical_document(canonical)
    checked = _sidecar(sidecar, document)
    required = {"reviewer_assessed", "review_passed", "canonical_body_sha256", "sidecar_sha256", "chapter_ids", "usage_ids", "reviewed_source_refs", "findings"}
    if not isinstance(review, dict) or set(review) != required or review.get("reviewer_assessed") is not True or type(review.get("review_passed")) is not bool:
        raise ProvenanceError("invalid_review")
    if review["canonical_body_sha256"] != document["body_sha256"] or review["sidecar_sha256"] != checked["sidecar_sha256"]:
        raise ProvenanceError("review_hash_mismatch")
    if not isinstance(review["chapter_ids"], list) or not isinstance(review["usage_ids"], list) or set(review["chapter_ids"]) != set(document["chapter_ids"]) or len(review["chapter_ids"]) != len(set(review["chapter_ids"])):
        raise ProvenanceError("review_coverage_incomplete")
    usage_ids = [usage["usage_id"] for usage in checked["usages"]]
    if set(review["usage_ids"]) != set(usage_ids) or len(review["usage_ids"]) != len(set(review["usage_ids"])):
        raise ProvenanceError("review_coverage_incomplete")
    source_by_id = {source["source_id"]: source for source in checked["sources"]}
    for claim in checked["claims"]:
        for ref in claim["source_refs"]:
            # The full source text is intentionally not emitted in the sidecar.  The
            # runtime provides it to the reviewer, and reports exact excerpts here.
            if ref["source_id"] not in source_by_id:
                raise ProvenanceError("invalid_sidecar")
    if not isinstance(review["reviewed_source_refs"], list):
        raise ProvenanceError("invalid_review")
    expected_refs = [
        {key: excerpt[key] for key in ("source_id", "segment_id", "text_sha256")}
        for excerpt in checked["review_source_excerpts"]
    ]
    if review["reviewed_source_refs"] != expected_refs:
        raise ProvenanceError("review_source_excerpt_mismatch")
    if not isinstance(review["findings"], list):
        raise ProvenanceError("invalid_review")
    finding_ids, finding_chapters, finding_usages, revision_targets = set(), set(), set(), []
    usage_by_chapter = {chapter_id: set() for chapter_id in document["chapter_ids"]}
    for usage in checked["usages"]:
        usage_by_chapter[usage["chapter_id"]].add(usage["usage_id"])
    review_has_unresolved_issue = False
    for finding in review["findings"]:
        required_finding = {"finding_id", "chapter_id", "usage_ids", "verdict", "rationale", "revision_required"}
        if not isinstance(finding, dict) or set(finding) != required_finding:
            raise ProvenanceError("invalid_review")
        finding_id, chapter_id = _id(finding["finding_id"], "finding_id"), _id(finding["chapter_id"], "chapter_id")
        if finding_id in finding_ids or chapter_id in finding_chapters or finding["verdict"] not in {"supported", "concern", "blocked", "not_checked"} or not isinstance(finding["rationale"], str) or not finding["rationale"].strip() or type(finding["revision_required"]) is not bool or not isinstance(finding["usage_ids"], list):
            raise ProvenanceError("invalid_review")
        ids = [_id(value, "usage_id") for value in finding["usage_ids"]]
        if len(ids) != len(set(ids)) or set(ids) != usage_by_chapter.get(chapter_id):
            raise ProvenanceError("review_chapter_usage_mismatch")
        finding_ids.add(finding_id)
        finding_chapters.add(chapter_id)
        finding_usages.update(ids)
        if finding["revision_required"]:
            revision_targets.append(chapter_id)
        if finding["verdict"] in {"concern", "blocked", "not_checked"} or finding["revision_required"]:
            review_has_unresolved_issue = True
    if finding_chapters != set(document["chapter_ids"]) or finding_usages != set(usage_ids):
        raise ProvenanceError("review_coverage_incomplete")
    claim_blockers = any(
        claim.get("status") in {"contested", "blocked"}
        or any(ref.get("relationship") == "contradicts" for ref in claim.get("evidence_refs") or [])
        for claim in checked["claims"]
    )
    if review["review_passed"] and (review_has_unresolved_issue or claim_blockers):
        raise ProvenanceError("review_outcome_conflict")
    return {
        "review_status": "reviewer_assessed_passed" if review["review_passed"] else "reviewer_assessed_failed",
        "reviewer_assessed": True, "canonical_body_sha256": document["body_sha256"], "sidecar_sha256": checked["sidecar_sha256"],
        "chapter_ids": deepcopy(review["chapter_ids"]), "usage_ids": deepcopy(review["usage_ids"]),
        "reviewed_source_refs": deepcopy(review["reviewed_source_refs"]),
        "findings": deepcopy(review["findings"]), "revision_target_chapter_ids": revision_targets,
    }
