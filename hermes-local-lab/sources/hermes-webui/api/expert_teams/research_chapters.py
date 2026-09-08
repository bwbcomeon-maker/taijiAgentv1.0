"""Deterministic, JSON-safe v3 chapter planning and checkpoint primitives.

This module deliberately owns no storage or locks.  Runtime code must call these
pure transitions while holding its existing run-mutation lock, then persist the
returned ledger and immutable result references in the run.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re


CHAPTER_PLAN_SCHEMA = "research-v3/chapter-plan/v1"
CHAPTER_LEDGER_SCHEMA = "research-v3/chapter-ledger/v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MARKDOWN_LINK = re.compile(r"!?(?:\[[^\]]*\]\([^)]*\)|<https?://[^>]+>)")
_MARKDOWN_MARKS = re.compile(r"(?:^|\s)(?:#{1,6}|[-*+] |\d+\. |>`?\s*|\*{1,3}|_{1,3}|`+)")
_CITATION_ONLY = re.compile(r"^\s*(?:\[\^?[^\]]+\]|\([^)]*(?:来源|source|doi|http)[^)]*\))\s*$", re.I)
_APPENDIX_HEADING = re.compile(r"^#{1,6}\s*(?:附录|附件|来源(?:附件|列表)?|参考文献|目录)\b", re.I)
_WORD_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*|[\u3400-\u9fff]")
_FORBIDDEN_BODY_MARKERS = ("source_id", "claim_id", "冻结 Brief", "登记原声明")
_STRUCTURAL_HEADING = re.compile(r"^(?P<marks>#{1,6})\s*(?P<title>.*?)\s*$")
_EXCLUDED_STRUCTURAL_SECTIONS = {"封面", "摘要", "目录", "来源", "来源附件", "来源列表", "参考文献", "附录", "附件"}
_BODY_STRUCTURAL_SECTIONS = {"正文", "报告正文"}
_STREAM_UNSET = object()


class ChapterLedgerError(ValueError):
    """A caller attempted a malformed or stale chapter-ledger transition."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(code if not message else f"{code}: {message}")
        self.code = code


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: object) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value if isinstance(value, bytes) else _canonical(value)).hexdigest()


def _require_id(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise ChapterLedgerError("invalid_id", field)
    return text


def _require_hash(value: object, field: str) -> str:
    text = str(value or "")
    if not _HEX64.fullmatch(text):
        raise ChapterLedgerError("invalid_hash", field)
    return text


def _word_budget(contract: object) -> dict:
    if not isinstance(contract, dict) or contract.get("contract_version") != "research-report/v3":
        raise ChapterLedgerError("frozen_contract_required")
    budget = contract.get("word_budget")
    if not isinstance(budget, dict) or set(budget) != {
        "minimum", "target", "maximum", "chapter_unit_minimum", "chapter_unit_maximum"
    }:
        raise ChapterLedgerError("invalid_contract_budget")
    if any(type(budget[key]) is not int for key in budget):
        raise ChapterLedgerError("invalid_contract_budget")
    if not (0 < budget["minimum"] <= budget["target"] <= budget["maximum"]):
        raise ChapterLedgerError("invalid_contract_budget")
    if not (0 < budget["chapter_unit_minimum"] <= budget["chapter_unit_maximum"]):
        raise ChapterLedgerError("invalid_contract_budget")
    return deepcopy(budget)


def _outline_rows(outline: object) -> list[dict]:
    if not isinstance(outline, list) or not outline:
        raise ChapterLedgerError("outline_required")
    rows, seen = [], set()
    for index, row in enumerate(outline):
        if not isinstance(row, dict) or set(row) != {"chapter_id", "title", "kind"}:
            raise ChapterLedgerError("invalid_outline", str(index))
        chapter_id = _require_id(row["chapter_id"], f"outline.{index}.chapter_id")
        title = row["title"] if isinstance(row["title"], str) else ""
        kind = row["kind"] if isinstance(row["kind"], str) else ""
        if not title.strip() or kind not in {"background", "analysis", "comparison", "recommendation", "other"}:
            raise ChapterLedgerError("invalid_outline", str(index))
        if chapter_id in seen:
            raise ChapterLedgerError("duplicate_chapter")
        seen.add(chapter_id)
        rows.append({"chapter_id": chapter_id, "title": title.strip(), "kind": kind})
    return rows


def _weights(rows: list[dict]) -> list[int]:
    return [
        {"background": 1, "analysis": 4, "comparison": 4, "recommendation": 4, "other": 2}[row["kind"]]
        for row in rows
    ]


def _split_budget(total: int, count: int, minimum: int, maximum: int) -> list[int]:
    if count <= 0 or not minimum * count <= total <= maximum * count:
        raise ChapterLedgerError("unit_budget_unsatisfied")
    base, remainder = divmod(total, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def build_research_chapter_plan(contract: object, outline: object) -> dict:
    """Create a stable plan whose only writing budget comes from the frozen contract."""
    budget = _word_budget(contract)
    rows = _outline_rows(outline)
    target = budget["target"]
    unit_min, unit_max = budget["chapter_unit_minimum"], budget["chapter_unit_maximum"]
    # Aim below the hard upper bound so priority can be expressed without
    # overflowing a one-unit chapter.  The capacity check remains authoritative.
    unit_count = max(len(rows), math.ceil(target / 1600))
    if not unit_min * unit_count <= target <= unit_max * unit_count:
        raise ChapterLedgerError("unit_budget_unsatisfied")

    weights = _weights(rows)
    extra = unit_count - len(rows)
    extra_by_chapter = [0] * len(rows)
    ranked = sorted(range(len(rows)), key=lambda index: (-weights[index], index))
    for index in range(extra):
        extra_by_chapter[ranked[index % len(ranked)]] += 1
    unit_counts = [1 + value for value in extra_by_chapter]
    remaining = target - unit_min * unit_count
    capacities = [count * (unit_max - unit_min) for count in unit_counts]
    additions = [0] * len(rows)
    # Repeated capped apportionment keeps every chapter under its unit capacity.
    while remaining:
        eligible = [index for index, capacity in enumerate(capacities) if additions[index] < capacity]
        if not eligible:
            raise ChapterLedgerError("unit_budget_unsatisfied")
        denominator = sum(weights[index] for index in eligible)
        proposed = {index: min(capacities[index] - additions[index], remaining * weights[index] // denominator) for index in eligible}
        moved = sum(proposed.values())
        if moved:
            for index, value in proposed.items():
                additions[index] += value
            remaining -= moved
            continue
        for index in sorted(eligible, key=lambda item: (-weights[item], item)):
            if remaining == 0:
                break
            additions[index] += 1
            remaining -= 1
    chapter_budgets = [unit_min * count + addition for count, addition in zip(unit_counts, additions)]

    chapters, units = [], []
    for chapter_index, (row, chapter_budget, count) in enumerate(zip(rows, chapter_budgets, unit_counts), start=1):
        chunks = _split_budget(chapter_budget, count, unit_min, unit_max)
        chapter = {**row, "chapter_index": chapter_index, "budget": chapter_budget}
        chapters.append(chapter)
        for unit_index, unit_budget in enumerate(chunks, start=1):
            units.append({
                "unit_id": f"{row['chapter_id']}:U{unit_index:02d}",
                "chapter_id": row["chapter_id"], "chapter_index": chapter_index,
                "kind": "draft", "unit_index": unit_index, "budget": unit_budget,
            })
    plan_without_sha = {
        "schema_version": CHAPTER_PLAN_SCHEMA, "contract_version": contract["contract_version"],
        "target_word_count": target, "word_budget": budget, "chapters": chapters, "units": units,
    }
    return {**plan_without_sha, "plan_sha256": _sha(plan_without_sha)}


def _validate_plan(plan: object) -> dict:
    if not isinstance(plan, dict) or plan.get("schema_version") != CHAPTER_PLAN_SCHEMA:
        raise ChapterLedgerError("invalid_plan")
    rendered = {key: deepcopy(value) for key, value in plan.items() if key != "plan_sha256"}
    if _sha(rendered) != plan.get("plan_sha256"):
        raise ChapterLedgerError("plan_hash_mismatch")
    _require_hash(plan["plan_sha256"], "plan_sha256")
    _outline_rows([{key: item[key] for key in ("chapter_id", "title", "kind")} for item in plan.get("chapters") or []])
    return deepcopy(plan)


def _planned_unit_fields(unit: dict) -> dict:
    return {key: unit[key] for key in ("unit_id", "chapter_id", "chapter_index", "kind", "unit_index", "budget")}


def _expected_unit_input(parent_input: str, plan_sha256: str, planned: dict, revision_round: int) -> str:
    return _sha({"parent_input": parent_input, "plan_sha256": plan_sha256, "unit": planned, "revision_round": revision_round})


def create_chapter_ledger(parent: object, plan: object) -> dict:
    """Create a JSON-only checkpoint; runtime persists it inside its existing lock."""
    if not isinstance(parent, dict) or set(parent) != {"stage_id", "stage_attempt", "reservation_id", "input_binding_sha256"}:
        raise ChapterLedgerError("invalid_parent")
    stage_attempt = parent["stage_attempt"]
    if type(stage_attempt) is not int or stage_attempt < 1:
        raise ChapterLedgerError("invalid_parent")
    checked_plan = _validate_plan(plan)
    checked_parent = {
        "stage_id": _require_id(parent["stage_id"], "stage_id"), "stage_attempt": stage_attempt,
        "reservation_id": _require_id(parent["reservation_id"], "reservation_id"),
        "input_binding_sha256": _require_hash(parent["input_binding_sha256"], "input_binding_sha256"),
    }
    units = []
    for planned in checked_plan["units"]:
        unit_input = _expected_unit_input(checked_parent["input_binding_sha256"], checked_plan["plan_sha256"], planned, 0)
        units.append({
            **deepcopy(planned), "unit_attempt": 0, "revision_round": 0,
            "unit_input_sha256": unit_input, "status": "pending", "execution": None,
            "result": None, "receipt": None,
        })
    return {
        "schema_version": CHAPTER_LEDGER_SCHEMA, "parent": checked_parent,
        "plan_sha256": checked_plan["plan_sha256"], "plan": checked_plan,
        "quality_revision_round": 0, "quality_revision_history": [], "review_binding": None, "units": units,
    }


def _validate_ledger(ledger: object) -> dict:
    if not isinstance(ledger, dict) or ledger.get("schema_version") != CHAPTER_LEDGER_SCHEMA:
        raise ChapterLedgerError("invalid_ledger")
    checked = deepcopy(ledger)
    if checked.get("plan_sha256") != checked.get("plan", {}).get("plan_sha256"):
        raise ChapterLedgerError("plan_hash_mismatch")
    _validate_plan(checked.get("plan"))
    parent = checked.get("parent")
    if not isinstance(parent, dict):
        raise ChapterLedgerError("invalid_parent")
    _require_hash(parent.get("input_binding_sha256"), "input_binding_sha256")
    _require_id(parent.get("stage_id"), "stage_id")
    _require_id(parent.get("reservation_id"), "reservation_id")
    if type(parent.get("stage_attempt")) is not int or parent["stage_attempt"] < 1:
        raise ChapterLedgerError("invalid_parent")
    if not isinstance(checked.get("units"), list):
        raise ChapterLedgerError("invalid_ledger")
    planned_units = checked["plan"].get("units") or []
    if [unit.get("unit_id") for unit in checked["units"]] != [unit.get("unit_id") for unit in planned_units]:
        raise ChapterLedgerError("ledger_unit_set_mismatch")
    for unit, planned in zip(checked["units"], planned_units):
        if not isinstance(unit, dict) or _planned_unit_fields(unit) != planned:
            raise ChapterLedgerError("ledger_unit_set_mismatch")
        if type(unit.get("unit_attempt")) is not int or unit["unit_attempt"] < 0 or type(unit.get("revision_round")) is not int or unit["revision_round"] < 0:
            raise ChapterLedgerError("invalid_ledger")
        expected = _expected_unit_input(parent["input_binding_sha256"], checked["plan_sha256"], planned, unit["revision_round"])
        if unit.get("unit_input_sha256") != expected:
            raise ChapterLedgerError("input_binding_mismatch")
        if unit.get("status") not in {"pending", "running", "retryable", "completed"}:
            raise ChapterLedgerError("invalid_ledger")
    if checked.get("review_binding") is not None and (not isinstance(checked["review_binding"], dict) or checked["review_binding"].get("status") not in {"current", "stale"}):
        raise ChapterLedgerError("invalid_ledger")
    return checked


def pending_units(ledger: object) -> list[dict]:
    checked = _validate_ledger(ledger)
    return [deepcopy(unit) for unit in checked["units"] if unit.get("status") != "completed"]


def _unit(ledger: dict, unit_id: str) -> dict:
    target = _require_id(unit_id, "unit_id")
    for unit in ledger["units"]:
        if unit.get("unit_id") == target:
            return unit
    raise ChapterLedgerError("unknown_unit")


def begin_unit_execution(ledger: object, *, unit_id: str, execution_start_id: str) -> dict:
    """Reserve one unit attempt.  A network/start retry increments only unit_attempt."""
    result = _validate_ledger(ledger)
    unit = _unit(result, unit_id)
    if unit.get("status") == "completed":
        return {"decision": "completed", "ledger": result, "unit": deepcopy(unit)}
    if unit.get("status") == "running":
        raise ChapterLedgerError("unit_already_running")
    if unit.get("status") not in {"pending", "retryable"}:
        raise ChapterLedgerError("unit_not_retryable")
    start = _require_id(execution_start_id, "execution_start_id")
    unit["unit_attempt"] = int(unit.get("unit_attempt", 0)) + 1
    unit["status"] = "running"
    unit["execution"] = {"execution_start_id": start, "stream_id": None, "unit_input_sha256": unit["unit_input_sha256"]}
    return {"decision": "dispatch", "ledger": result, "unit": deepcopy(unit)}


def bind_unit_stream(ledger: object, *, unit_id: str, execution_start_id: str, stream_id: str) -> dict:
    """Bind the Provider-returned stream after the start identity is reserved."""
    result = _validate_ledger(ledger)
    unit = _unit(result, unit_id)
    start, stream = _require_id(execution_start_id, "execution_start_id"), _require_id(stream_id, "stream_id")
    execution = unit.get("execution") if isinstance(unit.get("execution"), dict) else {}
    if unit.get("status") != "running" or execution.get("execution_start_id") != start:
        raise ChapterLedgerError("execution_start_mismatch")
    if execution.get("stream_id") not in {None, stream}:
        raise ChapterLedgerError("stream_mismatch")
    unit["execution"]["stream_id"] = stream
    return {"decision": "bound", "ledger": result, "unit": deepcopy(unit)}


def mark_unit_retryable(
    ledger: object,
    *,
    unit_id: str,
    execution_start_id: str,
    reason: str,
    stream_id: object = _STREAM_UNSET,
) -> dict:
    """End a failed/terminated call without losing completed sibling units."""
    result = _validate_ledger(ledger)
    unit = _unit(result, unit_id)
    start = _require_id(execution_start_id, "execution_start_id")
    if (
        unit.get("status") != "running"
        or unit.get("execution", {}).get("execution_start_id") != start
        or (
            stream_id is not _STREAM_UNSET
            and unit.get("execution", {}).get("stream_id") != stream_id
        )
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ChapterLedgerError("execution_start_mismatch")
    unit["status"], unit["execution"] = "retryable", None
    unit["last_failure"] = reason.strip()
    return {"decision": "retryable", "ledger": result, "unit": deepcopy(unit)}


def complete_unit_execution(ledger: object, *, unit_id: str, execution_start_id: str, stream_id: str, result_packet: object, receipt: object) -> dict:
    """Consume a matching unit completion once, retaining an immutable result record."""
    result = _validate_ledger(ledger)
    unit = _unit(result, unit_id)
    if not isinstance(result_packet, dict) or set(result_packet) != {"body", "claim_usages"} or not isinstance(result_packet.get("body"), str) or not result_packet["body"].strip() or not isinstance(result_packet.get("claim_usages"), list) or not isinstance(receipt, dict) or set(receipt) != {"delivery_id"}:
        raise ChapterLedgerError("invalid_completion")
    body = result_packet["body"]
    packet = deepcopy(result_packet)
    delivery_id = _require_id(receipt["delivery_id"], "delivery_id")
    start, stream = _require_id(execution_start_id, "execution_start_id"), _require_id(stream_id, "stream_id")
    body_sha = _sha(body)
    packet_sha = _sha(packet)
    if unit.get("status") == "completed":
        same = unit.get("receipt") == {"delivery_id": delivery_id, "delivery_content_sha256": body_sha, "delivery_result_sha256": packet_sha, "execution_start_id": start, "stream_id": stream}
        if same and unit.get("result", {}).get("packet") == packet:
            return {"decision": "replay", "ledger": result, "unit": deepcopy(unit)}
        raise ChapterLedgerError("receipt_conflict")
    execution = unit.get("execution") if isinstance(unit.get("execution"), dict) else {}
    if execution.get("unit_input_sha256") != unit.get("unit_input_sha256"):
        raise ChapterLedgerError("input_binding_mismatch")
    if execution.get("execution_start_id") != start or execution.get("stream_id") != stream:
        raise ChapterLedgerError("stream_mismatch")
    # The unit hash is opaque, so always recompute it from the frozen parent and plan.
    planned = _planned_unit_fields(unit)
    expected = _expected_unit_input(result["parent"]["input_binding_sha256"], result["plan_sha256"], planned, unit["revision_round"])
    if expected != unit["unit_input_sha256"]:
        raise ChapterLedgerError("input_binding_mismatch")
    unit["result"] = {"body": body, "body_sha256": body_sha, "packet": packet, "packet_sha256": packet_sha}
    unit["receipt"] = {"delivery_id": delivery_id, "delivery_content_sha256": body_sha, "delivery_result_sha256": packet_sha, "execution_start_id": start, "stream_id": stream}
    unit["status"], unit["execution"] = "completed", None
    return {"decision": "completed", "ledger": result, "unit": deepcopy(unit)}


def assemble_canonical_document(plan: object, chapters: object) -> dict:
    """Order one current chapter result per planned chapter; never promote a last chapter alone."""
    checked = _validate_plan(plan)
    if not isinstance(chapters, list):
        raise ChapterLedgerError("chapter_set_mismatch")
    expected = [item["chapter_id"] for item in checked["chapters"]]
    found, rendered = [], {}
    for row in chapters:
        if not isinstance(row, dict) or set(row) != {"chapter_id", "body"}:
            raise ChapterLedgerError("invalid_chapter")
        chapter_id = _require_id(row["chapter_id"], "chapter_id")
        if chapter_id in rendered:
            raise ChapterLedgerError("duplicate_chapter")
        if not isinstance(row["body"], str) or not row["body"].strip():
            raise ChapterLedgerError("invalid_chapter")
        found.append(chapter_id)
        rendered[chapter_id] = row["body"]
    if set(found) != set(expected):
        raise ChapterLedgerError("chapter_set_mismatch")
    ordered = [rendered[chapter_id].strip() for chapter_id in expected]
    body = "\n\n".join(ordered)
    return {
        "schema_version": "research-v3/canonical-document/v1",
        "plan_sha256": checked["plan_sha256"],
        "chapter_ids": expected,
        # These immutable plan headings let provenance distinguish one
        # chapter's deliberate multi-unit H2s from the next chapter boundary.
        "chapter_headings": [row["title"] for row in checked["chapters"]],
        "chapter_kinds": [row["kind"] for row in checked["chapters"]],
        "body": body,
        "body_sha256": _sha(body),
    }


def assemble_canonical_from_ledger(ledger: object) -> dict:
    """Authoritative assembly path: only current, complete unit results may enter it."""
    checked = _validate_ledger(ledger)
    by_chapter = {chapter["chapter_id"]: [] for chapter in checked["plan"]["chapters"]}
    for unit in checked["units"]:
        planned = _planned_unit_fields(unit)
        expected = _expected_unit_input(checked["parent"]["input_binding_sha256"], checked["plan_sha256"], planned, unit["revision_round"])
        if expected != unit.get("unit_input_sha256"):
            raise ChapterLedgerError("input_binding_mismatch")
        if unit.get("status") != "completed" or not isinstance(unit.get("result"), dict) or _sha(unit["result"].get("body", "")) != unit["result"].get("body_sha256") or _sha(unit["result"].get("packet")) != unit["result"].get("packet_sha256") or unit["result"].get("packet", {}).get("body") != unit["result"].get("body"):
            raise ChapterLedgerError("chapter_incomplete")
        by_chapter[unit["chapter_id"]].append(unit)
    chapters = []
    for chapter in checked["plan"]["chapters"]:
        units = sorted(by_chapter[chapter["chapter_id"]], key=lambda item: item["unit_index"])
        if not units:
            raise ChapterLedgerError("chapter_incomplete")
        rendered_units = []
        for unit in units:
            body = unit["result"]["body"].strip()
            # The assembler owns the chapter H2.  A model may repeat exactly
            # that heading at the start of a unit; remove only this duplicate
            # boundary, never a different heading or prose within the unit.
            match = re.match(r"^##\s+(.+?)\s*(?:\n|$)", body)
            if match is not None and match.group(1).strip() == chapter["title"]:
                body = body[match.end():].lstrip()
            rendered_units.append(body)
        chapters.append({"chapter_id": chapter["chapter_id"], "body": f"## {chapter['title']}\n" + "\n\n".join(rendered_units)})
    return assemble_canonical_document(checked["plan"], chapters)


def begin_quality_revision(
    ledger: object,
    *,
    canonical_body_sha256: str,
    chapter_ids: object,
    unit_ids: object = None,
    revision_kind: str = "quality_review",
) -> dict:
    """Re-open targeted chapters while retaining an immutable revision record."""
    result = _validate_ledger(ledger)
    digest = _require_hash(canonical_body_sha256, "canonical_body_sha256")
    current_canonical = assemble_canonical_from_ledger(result)
    if digest != current_canonical["body_sha256"]:
        raise ChapterLedgerError("canonical_hash_mismatch")
    if not isinstance(chapter_ids, list) or not chapter_ids or len(chapter_ids) != len(set(chapter_ids)):
        raise ChapterLedgerError("invalid_revision_targets")
    expected = {chapter["chapter_id"] for chapter in result["plan"]["chapters"]}
    targets = [_require_id(item, "chapter_id") for item in chapter_ids]
    if not set(targets) <= expected:
        raise ChapterLedgerError("invalid_revision_targets")
    expected_units = {unit["unit_id"] for unit in result["units"]}
    if unit_ids is None:
        target_units = {
            unit["unit_id"] for unit in result["units"] if unit["chapter_id"] in targets
        }
    else:
        if not isinstance(unit_ids, list) or not unit_ids or len(unit_ids) != len(set(unit_ids)):
            raise ChapterLedgerError("invalid_revision_targets")
        target_units = {_require_id(item, "unit_id") for item in unit_ids}
        if not target_units <= expected_units or any(
            unit["unit_id"] in target_units and unit["chapter_id"] not in targets
            for unit in result["units"]
        ):
            raise ChapterLedgerError("invalid_revision_targets")
    if revision_kind not in {"quality_review", "delivery_feedback", "length_supplement"}:
        raise ChapterLedgerError("invalid_revision_kind")
    if revision_kind == "quality_review" and result.get("quality_revision_round", 0) >= 2:
        raise ChapterLedgerError("quality_revision_limit")
    delivery_feedback_round = int(result.get("delivery_feedback_revision_round") or 0)
    if revision_kind == "delivery_feedback" and delivery_feedback_round >= 2:
        raise ChapterLedgerError("delivery_feedback_revision_limit")
    if revision_kind == "length_supplement" and any(
        item.get("revision_kind") == "length_supplement"
        for item in result.get("quality_revision_history") or []
        if isinstance(item, dict)
    ):
        raise ChapterLedgerError("length_supplement_limit")
    result["quality_revision_round"] += 1
    revision_round = result["quality_revision_round"]
    if revision_kind == "delivery_feedback":
        result["delivery_feedback_revision_round"] = delivery_feedback_round + 1
    result["quality_revision_history"].append({
        "revision_round": revision_round,
        "revision_kind": revision_kind,
        "canonical_body_sha256": digest,
        "chapter_ids": targets,
        "unit_ids": sorted(target_units),
    })
    for unit in result["units"]:
        if unit["unit_id"] not in target_units:
            continue
        previous = unit.get("result")
        if previous is not None:
            unit.setdefault("superseded_results", []).append({"revision_round": unit["revision_round"], "result": previous, "receipt": unit.get("receipt")})
        unit["revision_round"] = revision_round
        unit["unit_attempt"] = 0
        unit["unit_input_sha256"] = _expected_unit_input(result["parent"]["input_binding_sha256"], result["plan_sha256"], _planned_unit_fields(unit), revision_round)
        unit["status"], unit["execution"], unit["result"], unit["receipt"] = "pending", None, None, None
        unit.pop("last_failure", None)
    result["review_binding"] = {"status": "stale", "invalidated_by_revision_round": revision_round, "previous_canonical_body_sha256": digest}
    return {"decision": "revise_target_chapters", "ledger": result, "chapter_ids": targets}


def restore_superseded_delivery_feedback_revision(
    ledger: object,
    *,
    chapter_ids: object,
) -> dict:
    """Undo only a duplicated delivery-feedback revision from immutable unit copies."""
    result = _validate_ledger(ledger)
    if not isinstance(chapter_ids, list) or not chapter_ids or len(chapter_ids) != len(set(chapter_ids)):
        raise ChapterLedgerError("invalid_revision_targets")
    targets = [_require_id(item, "chapter_id") for item in chapter_ids]
    history = result.get("quality_revision_history")
    if not isinstance(history, list) or len(history) < 2:
        raise ChapterLedgerError("delivery_feedback_revision_not_duplicated")
    previous, duplicate = history[-2], history[-1]
    expected_targets = set(targets)
    if (
        not isinstance(previous, dict)
        or not isinstance(duplicate, dict)
        or previous.get("revision_kind") != "delivery_feedback"
        or duplicate.get("revision_kind") != "delivery_feedback"
        or set(previous.get("chapter_ids") or []) != expected_targets
        or set(duplicate.get("chapter_ids") or []) != expected_targets
        or int(duplicate.get("revision_round") or 0) != int(previous.get("revision_round") or 0) + 1
        or int(result.get("quality_revision_round") or 0) != int(duplicate.get("revision_round") or 0)
    ):
        raise ChapterLedgerError("delivery_feedback_revision_not_duplicated")
    restore_round = int(previous["revision_round"])
    duplicate_round = int(duplicate["revision_round"])
    for unit in result["units"]:
        if unit.get("chapter_id") not in expected_targets:
            continue
        if unit.get("revision_round") != duplicate_round:
            raise ChapterLedgerError("delivery_feedback_revision_not_duplicated")
        restored = [
            item for item in unit.get("superseded_results") or []
            if isinstance(item, dict)
            and item.get("revision_round") == restore_round
            and isinstance(item.get("result"), dict)
            and isinstance(item.get("receipt"), dict)
        ]
        if len(restored) != 1:
            raise ChapterLedgerError("delivery_feedback_revision_restore_missing")
        item = restored[0]
        unit["revision_round"] = restore_round
        unit["unit_attempt"] = max(1, int(unit.get("unit_attempt") or 0))
        unit["unit_input_sha256"] = _expected_unit_input(
            result["parent"]["input_binding_sha256"],
            result["plan_sha256"],
            _planned_unit_fields(unit),
            restore_round,
        )
        unit["status"], unit["execution"] = "completed", None
        unit["result"], unit["receipt"] = deepcopy(item["result"]), deepcopy(item["receipt"])
        unit["superseded_results"] = [entry for entry in unit.get("superseded_results") or [] if entry is not item]
        unit.pop("last_failure", None)
    result["quality_revision_round"] = restore_round
    result["delivery_feedback_revision_round"] = max(
        0, int(result.get("delivery_feedback_revision_round") or 0) - 1
    )
    result["quality_revision_history"] = history[:-1]
    canonical = assemble_canonical_from_ledger(result)
    result["review_binding"] = {
        "status": "stale",
        "invalidated_by_revision_round": restore_round,
        "previous_canonical_body_sha256": canonical["body_sha256"],
    }
    return {
        "decision": "restore_superseded_delivery_feedback_revision",
        "ledger": result,
        "chapter_ids": targets,
        "revision_round": restore_round,
    }


def record_quality_review_binding(ledger: object, *, canonical_body_sha256: str, sidecar_sha256: str) -> dict:
    """Record the bytes reviewed by an independent reviewer before later revisions stale it."""
    result = _validate_ledger(ledger)
    canonical_digest = _require_hash(canonical_body_sha256, "canonical_body_sha256")
    current_canonical = assemble_canonical_from_ledger(result)
    if canonical_digest != current_canonical["body_sha256"]:
        raise ChapterLedgerError("canonical_hash_mismatch")
    result["review_binding"] = {
        "status": "current", "canonical_body_sha256": canonical_digest,
        "sidecar_sha256": _require_hash(sidecar_sha256, "sidecar_sha256"),
        "quality_revision_round": result["quality_revision_round"],
    }
    return {"decision": "review_bound", "ledger": result}


def _body_without_appendix(markdown: str) -> tuple[str, bool, str]:
    """Legacy markdown boundary detector; formal callers should pass body_scope."""
    kept, collecting, saw_structural, appendix = [], True, False, False
    for line in markdown.splitlines():
        match = _STRUCTURAL_HEADING.match(line)
        if match:
            title = re.sub(r"\s+", "", match.group("title")).strip("：:")
            if title in _EXCLUDED_STRUCTURAL_SECTIONS or _APPENDIX_HEADING.match(line):
                collecting, saw_structural, appendix = False, True, appendix or title in {"来源", "来源附件", "来源列表", "参考文献", "附录", "附件"}
                continue
            if title in _BODY_STRUCTURAL_SECTIONS:
                collecting, saw_structural = True, True
                continue
            # A subsequent normal chapter resumes a report only after an explicit
            # body boundary; otherwise cover/abstract/TOC remain excluded.
            if saw_structural and not collecting:
                continue
        if collecting:
            kept.append(line)
    return "\n".join(kept), appendix, "inferred_legacy" if saw_structural else "raw_body"


def _plain_body(markdown: str) -> str:
    markdown = re.sub(r"(?m)^#{1,6}\s+.*$", "", markdown)
    text = _MARKDOWN_LINK.sub("", markdown)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\[\^?[^\]]+\]", "", text)
    return _MARKDOWN_MARKS.sub(" ", text)


def evaluate_body_word_count(markdown: object, budget: object, *, body_scope: object = None) -> dict:
    """Count Chinese characters and contiguous Latin/numeric groups, excluding appendices."""
    if not isinstance(markdown, str) or not isinstance(budget, dict):
        raise ChapterLedgerError("invalid_word_count_input")
    if set(budget) != {"minimum", "target", "maximum"} or any(type(budget[key]) is not int for key in budget):
        raise ChapterLedgerError("invalid_word_count_input")
    if body_scope is None:
        body, appendix_excluded, boundary_mode = _body_without_appendix(markdown)
    elif isinstance(body_scope, dict) and set(body_scope) == {"body_markdown"} and isinstance(body_scope["body_markdown"], str):
        body, appendix_excluded, boundary_mode = body_scope["body_markdown"], False, "explicit_body_scope"
    else:
        raise ChapterLedgerError("invalid_body_scope")
    paragraphs = [re.sub(r"\s+", " ", _plain_body(item)).strip() for item in re.split(r"\n\s*\n", body)]
    meaningful = [item for item in paragraphs if item and not _CITATION_ONLY.fullmatch(item)]
    duplicates = sorted({item for item in meaningful if meaningful.count(item) > 1})
    de_duplicated = list(dict.fromkeys(meaningful))
    actual = len(_WORD_TOKEN.findall("\n\n".join(de_duplicated)))
    forbidden = [marker for marker in _FORBIDDEN_BODY_MARKERS if marker in body]
    passed = boundary_mode == "explicit_body_scope" and budget["minimum"] <= actual <= budget["maximum"] and not duplicates and not forbidden
    return {
        "actual_body_count": actual, "minimum": budget["minimum"], "target": budget["target"], "maximum": budget["maximum"],
        "gap_to_minimum": max(0, budget["minimum"] - actual), "gap_to_target": budget["target"] - actual,
        "appendix_excluded": appendix_excluded, "body_boundary_mode": boundary_mode, "duplicate_paragraphs": duplicates,
        "forbidden_body_markers": forbidden,
        "formal_word_count_passed": passed,
        "mechanical_notice": "字数与重复检查只提供机械证据，不证明内容深度或事实正确性。",
    }
