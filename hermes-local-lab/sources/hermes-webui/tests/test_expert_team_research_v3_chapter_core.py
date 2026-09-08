import copy
import hashlib
import json

import pytest


def _contract(depth="standard"):
    from api.expert_teams.research_contract import formal_report_writing_contract

    return formal_report_writing_contract(
        writing_style="central_enterprise", depth=depth
    )


def _outline():
    return [
        {"chapter_id": "background", "title": "背景、目的与范围", "kind": "background"},
        {"chapter_id": "current", "title": "现状", "kind": "analysis"},
        {"chapter_id": "problems", "title": "问题与原因", "kind": "analysis"},
        {"chapter_id": "options", "title": "方案比较与适用条件", "kind": "comparison"},
        {"chapter_id": "advice", "title": "判断与建议", "kind": "recommendation"},
    ]


def _parent():
    return {
        "stage_id": "draft",
        "stage_attempt": 4,
        "reservation_id": "stage-reservation-4",
        "input_binding_sha256": "a" * 64,
    }


def _complete_pending_units(ledger):
    from api.expert_teams.research_chapters import (
        begin_unit_execution,
        bind_unit_stream,
        complete_unit_execution,
    )

    for unit in list(ledger["units"]):
        if unit["status"] == "completed":
            continue
        start = f"start-{unit['unit_id']}-{unit['revision_round']}"
        stream = f"stream-{unit['unit_id']}-{unit['revision_round']}"
        reserved = begin_unit_execution(ledger, unit_id=unit["unit_id"], execution_start_id=start)
        bound = bind_unit_stream(reserved["ledger"], unit_id=unit["unit_id"], execution_start_id=start, stream_id=stream)
        ledger = complete_unit_execution(
            bound["ledger"], unit_id=unit["unit_id"], execution_start_id=start, stream_id=stream,
            result_packet={"body": f"正文 {unit['unit_id']} 第{unit['revision_round']}轮", "claim_usages": []},
            receipt={"delivery_id": f"delivery-{unit['unit_id']}-{unit['revision_round']}"},
        )["ledger"]
    return ledger


def test_deterministic_chapter_plan_obeys_frozen_budget_and_prioritizes_analysis():
    from api.expert_teams.research_chapters import build_research_chapter_plan

    plan = build_research_chapter_plan(_contract(), _outline())

    assert plan["schema_version"] == "research-v3/chapter-plan/v1"
    assert plan["target_word_count"] == 10000
    assert sum(unit["budget"] for unit in plan["units"]) == 10000
    assert all(1200 <= unit["budget"] <= 2000 for unit in plan["units"])
    assert [chapter["chapter_id"] for chapter in plan["chapters"]] == [
        row["chapter_id"] for row in _outline()
    ]
    by_chapter = {
        chapter["chapter_id"]: chapter["budget"] for chapter in plan["chapters"]
    }
    assert by_chapter["background"] < by_chapter["current"]
    assert by_chapter["background"] < by_chapter["advice"]
    assert plan == build_research_chapter_plan(_contract(), _outline())


def test_deep_plan_uses_multiple_units_and_keeps_each_call_in_contract_range():
    from api.expert_teams.research_chapters import build_research_chapter_plan

    plan = build_research_chapter_plan(_contract("deep"), _outline())

    assert plan["target_word_count"] == 17500
    assert sum(unit["budget"] for unit in plan["units"]) == 17500
    assert len(plan["units"]) > len(plan["chapters"])
    assert all(1200 <= unit["budget"] <= 2000 for unit in plan["units"])


def test_ledger_retries_one_unit_under_same_parent_and_json_resume_finds_pending_unit():
    from api.expert_teams.research_chapters import (
        bind_unit_stream,
        begin_unit_execution,
        build_research_chapter_plan,
        create_chapter_ledger,
        mark_unit_retryable,
        pending_units,
    )

    plan = build_research_chapter_plan(_contract(), _outline())
    ledger = create_chapter_ledger(_parent(), plan)
    first_unit = ledger["units"][0]
    started = begin_unit_execution(
        ledger,
        unit_id=first_unit["unit_id"],
        execution_start_id="start-1",
    )
    assert started["decision"] == "dispatch"
    with pytest.raises(Exception, match="unit_already_running"):
        begin_unit_execution(
            started["ledger"], unit_id=first_unit["unit_id"], execution_start_id="start-1"
        )
    bound = bind_unit_stream(started["ledger"], unit_id=first_unit["unit_id"], execution_start_id="start-1", stream_id="stream-1")
    assert bound["ledger"]["units"][0]["execution"]["stream_id"] == "stream-1"
    assert started["ledger"]["units"][0]["unit_attempt"] == 1
    retryable = mark_unit_retryable(bound["ledger"], unit_id=first_unit["unit_id"], execution_start_id="start-1", reason="provider_timeout")
    retried = begin_unit_execution(
        retryable["ledger"],
        unit_id=first_unit["unit_id"],
        execution_start_id="start-2",
    )
    assert retried["ledger"]["units"][0]["unit_attempt"] == 2
    assert retried["ledger"]["units"][0]["revision_round"] == 0
    recovered = copy.deepcopy(retried["ledger"])
    assert pending_units(recovered)[0]["unit_id"] == first_unit["unit_id"]


def test_completion_is_idempotent_but_rejects_changed_bytes_or_old_input_or_stream():
    from api.expert_teams.research_chapters import (
        ChapterLedgerError,
        assemble_canonical_from_ledger,
        bind_unit_stream,
        begin_unit_execution,
        build_research_chapter_plan,
        complete_unit_execution,
        create_chapter_ledger,
    )

    ledger = create_chapter_ledger(_parent(), build_research_chapter_plan(_contract(), _outline()))
    unit_id = ledger["units"][0]["unit_id"]
    started = begin_unit_execution(ledger, unit_id=unit_id, execution_start_id="start")
    started = bind_unit_stream(started["ledger"], unit_id=unit_id, execution_start_id="start", stream_id="stream")
    completed = complete_unit_execution(
        started["ledger"], unit_id=unit_id, execution_start_id="start", stream_id="stream",
        result_packet={"body": "第一段正文", "claim_usages": []}, receipt={"delivery_id": "delivery-1"},
    )
    replay = complete_unit_execution(
        completed["ledger"], unit_id=unit_id, execution_start_id="start", stream_id="stream",
        result_packet={"body": "第一段正文", "claim_usages": []}, receipt={"delivery_id": "delivery-1"},
    )
    assert replay["decision"] == "replay"
    with pytest.raises(ChapterLedgerError, match="receipt_conflict"):
        complete_unit_execution(
            completed["ledger"], unit_id=unit_id, execution_start_id="start", stream_id="stream",
            result_packet={"body": "被替换的正文", "claim_usages": []}, receipt={"delivery_id": "delivery-1"},
        )
    stale = copy.deepcopy(started["ledger"])
    stale["parent"]["input_binding_sha256"] = "b" * 64
    with pytest.raises(ChapterLedgerError, match="input_binding_mismatch"):
        complete_unit_execution(stale, unit_id=unit_id, execution_start_id="start", stream_id="stream", result_packet={"body": "第一段正文", "claim_usages": []}, receipt={"delivery_id": "delivery-1"})
    with pytest.raises(ChapterLedgerError, match="stream_mismatch"):
        complete_unit_execution(started["ledger"], unit_id=unit_id, execution_start_id="start", stream_id="other", result_packet={"body": "第一段正文", "claim_usages": []}, receipt={"delivery_id": "delivery-1"})
    with pytest.raises(ChapterLedgerError, match="receipt_conflict"):
        complete_unit_execution(
            completed["ledger"], unit_id=unit_id, execution_start_id="start", stream_id="stream",
            result_packet={"body": "第一段正文", "claim_usages": [{"usage_id": "changed"}]}, receipt={"delivery_id": "delivery-1"},
        )


def test_assembly_requires_every_current_chapter_once_in_plan_order():
    from api.expert_teams.research_chapters import (
        ChapterLedgerError,
        assemble_canonical_document,
        build_research_chapter_plan,
    )

    plan = build_research_chapter_plan(_contract(), _outline())
    chapters = [
        {"chapter_id": row["chapter_id"], "body": f"## {row['title']}\n正文 {row['chapter_id']}"}
        for row in reversed(plan["chapters"])
    ]
    document = assemble_canonical_document(plan, chapters)
    assert document["chapter_ids"] == [row["chapter_id"] for row in plan["chapters"]]
    assert document["body"].index("背景、目的与范围") < document["body"].index("判断与建议")
    with pytest.raises(ChapterLedgerError, match="chapter_set_mismatch"):
        assemble_canonical_document(plan, chapters[:-1])
    with pytest.raises(ChapterLedgerError, match="duplicate_chapter"):
        assemble_canonical_document(plan, chapters + [chapters[0]])


def test_authoritative_ledger_assembly_requires_current_complete_units_and_rejects_stale_unit_input():
    from api.expert_teams.research_chapters import (
        ChapterLedgerError,
        assemble_canonical_from_ledger,
        begin_unit_execution,
        bind_unit_stream,
        build_research_chapter_plan,
        complete_unit_execution,
        create_chapter_ledger,
    )

    ledger = create_chapter_ledger(_parent(), build_research_chapter_plan(_contract(), _outline()))
    with pytest.raises(ChapterLedgerError, match="chapter_incomplete"):
        assemble_canonical_from_ledger(ledger)
    missing_unit = copy.deepcopy(ledger)
    missing_unit["units"].pop()
    with pytest.raises(ChapterLedgerError, match="ledger_unit_set_mismatch"):
        assemble_canonical_from_ledger(missing_unit)
    duplicate_unit = copy.deepcopy(ledger)
    duplicate_unit["units"].append(copy.deepcopy(duplicate_unit["units"][-1]))
    with pytest.raises(ChapterLedgerError, match="ledger_unit_set_mismatch"):
        assemble_canonical_from_ledger(duplicate_unit)
    for unit in list(ledger["units"]):
        started = begin_unit_execution(ledger, unit_id=unit["unit_id"], execution_start_id=f"start-{unit['unit_index']}-{unit['chapter_index']}")
        bound = bind_unit_stream(started["ledger"], unit_id=unit["unit_id"], execution_start_id=f"start-{unit['unit_index']}-{unit['chapter_index']}", stream_id=f"stream-{unit['unit_index']}-{unit['chapter_index']}")
        completed = complete_unit_execution(bound["ledger"], unit_id=unit["unit_id"], execution_start_id=f"start-{unit['unit_index']}-{unit['chapter_index']}", stream_id=f"stream-{unit['unit_index']}-{unit['chapter_index']}", result_packet={"body": f"正文 {unit['unit_id']}", "claim_usages": []}, receipt={"delivery_id": f"delivery-{unit['unit_id']}"})
        ledger = completed["ledger"]
    canonical = assemble_canonical_from_ledger(ledger)
    assert canonical["chapter_ids"] == [row["chapter_id"] for row in _outline()]
    stale = copy.deepcopy(ledger)
    stale["units"][0]["unit_input_sha256"] = "0" * 64
    with pytest.raises(ChapterLedgerError, match="input_binding_mismatch"):
        assemble_canonical_from_ledger(stale)


def test_quality_revision_is_bounded_to_target_chapters_and_does_not_consume_network_attempts():
    from api.expert_teams.research_chapters import (
        ChapterLedgerError,
        assemble_canonical_from_ledger,
        begin_quality_revision,
        build_research_chapter_plan,
        create_chapter_ledger,
        record_quality_review_binding,
    )

    ledger = _complete_pending_units(
        create_chapter_ledger(_parent(), build_research_chapter_plan(_contract(), _outline()))
    )
    canonical = assemble_canonical_from_ledger(ledger)
    reviewed = record_quality_review_binding(
        ledger, canonical_body_sha256=canonical["body_sha256"], sidecar_sha256="d" * 64
    )
    first = begin_quality_revision(reviewed["ledger"], canonical_body_sha256=canonical["body_sha256"], chapter_ids=["current"])
    assert first["ledger"]["quality_revision_round"] == 1
    assert first["ledger"]["units"][0]["revision_round"] == 0
    revised = [unit for unit in first["ledger"]["units"] if unit["chapter_id"] == "current"]
    assert revised and all(unit["revision_round"] == 1 and unit["status"] == "pending" for unit in revised)
    assert first["ledger"]["review_binding"]["status"] == "stale"
    with pytest.raises(ChapterLedgerError, match="chapter_incomplete"):
        record_quality_review_binding(
            first["ledger"], canonical_body_sha256=canonical["body_sha256"], sidecar_sha256="d" * 64
        )
    with pytest.raises(ChapterLedgerError, match="chapter_incomplete"):
        begin_quality_revision(first["ledger"], canonical_body_sha256=canonical["body_sha256"], chapter_ids=["advice"])
    assert first["ledger"]["quality_revision_round"] == 1
    rebuilt = _complete_pending_units(first["ledger"])
    revised_canonical = assemble_canonical_from_ledger(rebuilt)
    second = begin_quality_revision(rebuilt, canonical_body_sha256=revised_canonical["body_sha256"], chapter_ids=["advice"])
    assert second["ledger"]["quality_revision_round"] == 2
    completed_second = _complete_pending_units(second["ledger"])
    completed_second_canonical = assemble_canonical_from_ledger(completed_second)
    with pytest.raises(ChapterLedgerError, match="quality_revision_limit"):
        begin_quality_revision(completed_second, canonical_body_sha256=completed_second_canonical["body_sha256"], chapter_ids=["background"])


def test_body_count_excludes_appendix_and_markdown_and_flags_repeated_paragraphs():
    from api.expert_teams.research_chapters import evaluate_body_word_count

    body = "## 现状\n中国 AI 2026。\n\n中国 AI 2026。\n\n## 来源附件\n[1] this appendix 999"
    counted = evaluate_body_word_count(body, {"minimum": 5, "target": 9, "maximum": 12})
    assert counted["actual_body_count"] == 4
    assert counted["appendix_excluded"] is True
    assert counted["duplicate_paragraphs"]
    assert counted["formal_word_count_passed"] is False


def test_body_purity_blocks_internal_provenance_markers_without_banning_normal_business_words():
    from api.expert_teams.research_chapters import evaluate_body_word_count

    counted = evaluate_body_word_count(
        "## 判断与建议\n业务建议应明确责任、节奏和保障。 source_id=PUB-1",
        {"minimum": 8, "target": 10, "maximum": 30},
    )
    assert counted["forbidden_body_markers"] == ["source_id"]
    assert counted["formal_word_count_passed"] is False


def test_body_count_excludes_cover_abstract_and_toc_but_resumes_at_explicit_body_section():
    from api.expert_teams.research_chapters import evaluate_body_word_count

    counted = evaluate_body_word_count(
        "# 封面\n封面文字\n\n## 摘要\n摘要内容\n\n## 目录\n目录内容\n\n## 正文\n正式内容",
        {"minimum": 4, "target": 4, "maximum": 10},
    )
    assert counted["actual_body_count"] == 4
    assert counted["body_boundary_mode"] == "inferred_legacy"
    assert counted["formal_word_count_passed"] is False


def _source_row(source_id, kind, text):
    return {
        "source_id": source_id,
        "kind": kind,
        "content_text": text,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "segments": [{
            "segment_id": f"{source_id}:SEG-0001", "char_start": 0,
            "char_end": len(text), "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }],
    }


def _source_context():
    return {"sources": [
        _source_row("pub-1", "approved_public", "公开资料原文：企业推进人工智能需要治理与培训。"),
        _source_row("local-1", "approved_internal", "内部资料原文：组织已建立协同机制。"),
    ]}


def _background_context(run_id="run-1"):
    text = "用户背景原文：本单位已建立专项工作组。"
    return [{
        "source_id": "brief-1", "run_id": run_id,
        "input_ref": "brief:confirmed:2", "revision": 2,
        "content_text": text,
    }]


def _approved_claims(include_background=False):
    public_text = "公开资料原文：企业推进人工智能需要治理与培训。"
    rows = [{
        "claim_id": "claim-1", "statement": "企业推进人工智能需要治理与培训。",
        "claim_type": "fact", "status": "verified", "confidence": "high", "notes": "",
        "origin_tier": "public_web",
        "evidence": [{
            "source_id": "pub-1", "segment_id": "pub-1:SEG-0001",
            "segment_sha256": hashlib.sha256(public_text.encode("utf-8")).hexdigest(),
            "locator": "", "relationship": "supports",
        }],
    }]
    if include_background:
        background_text = "用户背景原文：本单位已建立专项工作组。"
        rows.append({
            "claim_id": "claim-2", "statement": "本单位已建立专项工作组。",
            "claim_type": "fact", "status": "unverified", "confidence": "low", "notes": "用户背景，未外部核验",
            "origin_tier": "user_background",
            "evidence": [{
                "source_id": "brief-1", "segment_id": "brief-1:INPUT",
                "segment_sha256": hashlib.sha256(background_text.encode("utf-8")).hexdigest(),
                "locator": "brief:confirmed:2", "relationship": "context",
            }],
        })
    return rows


def _canonical():
    return {
        "schema_version": "research-v3/canonical-document/v1",
        "plan_sha256": "a" * 64,
        "body": "# 专题调研报告\n\n## 现状\n### 现状说明\n企业推进人工智能需要治理与培训。\n\n## 判断与建议\n本单位已建立专项工作组。",
        "chapter_ids": ["current", "advice"],
    }


def test_provenance_allows_same_claim_multiple_usages_but_rejects_duplicate_usage_and_untrusted_user_background():
    from api.expert_teams.research_provenance import (
        ProvenanceError,
        build_provenance_sidecar,
    )

    claims = [
        {
            "claim_id": "claim-1",
            "usages": [
                {"usage_id": "usage-1", "chapter_id": "current", "rendered_excerpt": "企业推进人工智能需要治理与培训。"},
                {"usage_id": "usage-2", "chapter_id": "current", "rendered_excerpt": "企业推进人工智能需要治理与培训。"},
            ],
        }
    ]
    sidecar = build_provenance_sidecar(_canonical(), _source_context(), claims, run_id="run-1", approved_claims=_approved_claims())
    assert [usage["usage_id"] for usage in sidecar["usages"]] == ["usage-1", "usage-2"]
    duplicate = copy.deepcopy(claims)
    duplicate[0]["usages"][1]["usage_id"] = "usage-1"
    with pytest.raises(ProvenanceError, match="duplicate_usage"):
        build_provenance_sidecar(_canonical(), _source_context(), duplicate, run_id="run-1", approved_claims=_approved_claims())
    bad_context = _background_context("run-other")
    with pytest.raises(ProvenanceError, match="user_background_binding"):
        build_provenance_sidecar(
            _canonical(), _source_context(), claims, run_id="run-1",
            user_background_context=bad_context, approved_claims=_approved_claims(),
        )


def test_provenance_uses_plan_headings_for_split_chapter_h2_anchors():
    from api.expert_teams.research_provenance import _canonical_document

    body = (
        "## 研究问题\n正文一\n\n"
        "## 证据\n正文二\n\n"
        "## 证据\n正文三\n\n"
        "## 分析\n正文四\n\n"
        "## 分析\n正文五\n\n"
        "## 结论边界\n正文六\n\n"
        "## 引用\n正文七"
    )
    canonical = _canonical_document({
        "body": body,
        "chapter_ids": ["S01", "S02", "S03", "S04", "S05"],
        "chapter_headings": ["研究问题", "证据", "分析", "结论边界", "引用"],
    })
    assert canonical["chapter_ranges"]["S02"] == (body.index("## 证据"), body.index("## 分析"))
    assert canonical["chapter_ranges"]["S03"] == (body.index("## 分析"), body.index("## 结论边界"))

    with pytest.raises(Exception, match="chapter_anchor_unavailable"):
        _canonical_document({"body": "## 研究问题\n正文", "chapter_ids": ["S01"], "chapter_headings": ["缺失标题"]})


def test_provenance_classifies_frozen_attachment_as_unverified_user_background():
    from api.expert_teams.research_provenance import _source_context_index

    text = "附件中的业务记录。"
    trusted, _ = _source_context_index({"sources": [_source_row("ATT-1", "attachment", text)]})
    assert trusted["ATT-1"]["origin_tier"] == "user_background"
    assert trusted["ATT-1"]["external_verification"] == "not_performed"


def test_independent_review_uses_plan_headings_for_split_chapter_bodies():
    from api.expert_teams.research_review import _canonical

    body = "## 研究问题\n正文一\n\n## 证据\n正文二\n\n## 证据\n正文三\n\n## 分析\n正文四"
    document = _canonical({
        "body": body,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "chapter_ids": ["S01", "S02", "S03"],
        "chapter_headings": ["研究问题", "证据", "分析"],
    })
    assert document["bodies"]["S02"] == "\n正文二\n\n## 证据\n正文三\n\n"


def test_provenance_recomputes_anchors_and_rejects_unknown_claim_source_or_excerpt_mismatch():
    from api.expert_teams.research_provenance import (
        ProvenanceError,
        build_provenance_sidecar,
    )

    claims = [{
        "claim_id": "claim-1",
        "usages": [{"usage_id": "usage-1", "chapter_id": "current", "rendered_excerpt": "企业推进人工智能需要治理与培训。"}],
    }]
    approved = _approved_claims()
    sidecar = build_provenance_sidecar(_canonical(), _source_context(), claims, run_id="run-1", approved_claims=approved)
    assert sidecar["usages"][0]["text_sha256"]
    unapproved = copy.deepcopy(claims)
    unapproved[0]["claim_id"] = "model-invented-claim"
    with pytest.raises(ProvenanceError, match="unknown_claim"):
        build_provenance_sidecar(_canonical(), _source_context(), unapproved, run_id="run-1", approved_claims=approved)
    bad_hash = _source_context()
    bad_hash["sources"][0]["content_sha256"] = "0" * 64
    with pytest.raises(ProvenanceError, match="source_hash_mismatch"):
        build_provenance_sidecar(_canonical(), bad_hash, claims, run_id="run-1", approved_claims=approved)
    unknown = copy.deepcopy(approved)
    unknown[0]["evidence"][0]["source_id"] = "missing"
    with pytest.raises(ProvenanceError, match="unknown_source"):
        build_provenance_sidecar(_canonical(), _source_context(), claims, run_id="run-1", approved_claims=unknown)
    wrong = copy.deepcopy(claims)
    wrong[0]["usages"][0]["rendered_excerpt"] = "模型凭空写出的结论"
    with pytest.raises(ProvenanceError, match="excerpt_not_in_body"):
        build_provenance_sidecar(_canonical(), _source_context(), wrong, run_id="run-1", approved_claims=approved)
    model_with_fake_evidence = copy.deepcopy(approved)
    model_with_fake_evidence[0]["origin_tier"] = "model_knowledge"
    with pytest.raises(ProvenanceError, match="model_knowledge_false_evidence"):
        build_provenance_sidecar(_canonical(), _source_context(), claims, run_id="run-1", approved_claims=model_with_fake_evidence)
    forged = copy.deepcopy(sidecar)
    forged["usages"][0]["claim_id"] = "unknown-claim"
    unsigned = {key: value for key, value in forged.items() if key != "sidecar_sha256"}
    forged["sidecar_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    from api.expert_teams.research_provenance import mechanical_binding_validation
    with pytest.raises(ProvenanceError, match="unknown_claim"):
        mechanical_binding_validation(_canonical(), forged)


def test_mechanical_binding_does_not_claim_semantic_review_and_review_requires_current_hash_and_full_coverage():
    from api.expert_teams.research_provenance import (
        ProvenanceError,
        build_provenance_sidecar,
        mechanical_binding_validation,
        validate_independent_review,
    )

    claims = [
        {"claim_id": "claim-1", "usages": [{"usage_id": "usage-1", "chapter_id": "current", "rendered_excerpt": "企业推进人工智能需要治理与培训。"}]},
        {"claim_id": "claim-2", "usages": [{"usage_id": "usage-2", "chapter_id": "advice", "rendered_excerpt": "本单位已建立专项工作组。"}]},
    ]
    canonical = _canonical()
    sidecar = build_provenance_sidecar(
        canonical, _source_context(), claims, run_id="run-1",
        user_background_context=_background_context(), approved_claims=_approved_claims(True),
    )
    assert sidecar["claims"][0]["status"] == "verified"
    assert sidecar["claims"][0]["confidence"] == "high"
    assert sidecar["claims"][0]["evidence_refs"][0]["relationship"] == "supports"
    mechanical = mechanical_binding_validation(canonical, sidecar)
    assert mechanical["validation_kind"] == "mechanical_binding_validation"
    assert mechanical["semantic_equivalence_assessed"] is False
    review = {
        "reviewer_assessed": True, "review_passed": True,
        "canonical_body_sha256": mechanical["canonical_body_sha256"],
        "sidecar_sha256": mechanical["sidecar_sha256"],
        "chapter_ids": ["current", "advice"], "usage_ids": ["usage-1", "usage-2"],
        "reviewed_source_refs": [
            {key: value[key] for key in ("source_id", "segment_id", "text_sha256")}
            for value in sidecar["review_source_excerpts"]
        ],
        "findings": [
            {"finding_id": "finding-1", "chapter_id": "current", "usage_ids": ["usage-1"], "verdict": "supported", "rationale": "审查结论", "revision_required": False},
            {"finding_id": "finding-2", "chapter_id": "advice", "usage_ids": ["usage-2"], "verdict": "supported", "rationale": "审查结论", "revision_required": False},
        ],
    }
    validated = validate_independent_review(canonical, sidecar, review)
    assert validated["review_status"] == "reviewer_assessed_passed"
    blocked_finding = copy.deepcopy(review)
    blocked_finding["findings"][0]["verdict"] = "blocked"
    with pytest.raises(ProvenanceError, match="review_outcome_conflict"):
        validate_independent_review(canonical, sidecar, blocked_finding)
    revision_finding = copy.deepcopy(review)
    revision_finding["findings"][0]["revision_required"] = True
    with pytest.raises(ProvenanceError, match="review_outcome_conflict"):
        validate_independent_review(canonical, sidecar, revision_finding)
    swapped_usage = copy.deepcopy(review)
    swapped_usage["findings"][0]["usage_ids"] = ["usage-2"]
    swapped_usage["findings"][1]["usage_ids"] = ["usage-1"]
    with pytest.raises(ProvenanceError, match="review_chapter_usage_mismatch"):
        validate_independent_review(canonical, sidecar, swapped_usage)
    stale = copy.deepcopy(review)
    stale["canonical_body_sha256"] = "0" * 64
    with pytest.raises(ProvenanceError, match="review_hash_mismatch"):
        validate_independent_review(canonical, sidecar, stale)
    uncovered = copy.deepcopy(review)
    uncovered["usage_ids"] = ["usage-1"]
    with pytest.raises(ProvenanceError, match="review_coverage_incomplete"):
        validate_independent_review(canonical, sidecar, uncovered)
    forged_excerpt = copy.deepcopy(review)
    forged_excerpt["reviewed_source_refs"][0]["text_sha256"] = "0" * 64
    with pytest.raises(ProvenanceError, match="review_source_excerpt_mismatch"):
        validate_independent_review(canonical, sidecar, forged_excerpt)
    blocked_approved = _approved_claims(True)
    blocked_approved[0]["status"] = "blocked"
    blocked_sidecar = build_provenance_sidecar(
        canonical, _source_context(), claims, run_id="run-1",
        user_background_context=_background_context(), approved_claims=blocked_approved,
    )
    blocked_review = copy.deepcopy(review)
    blocked_review["sidecar_sha256"] = blocked_sidecar["sidecar_sha256"]
    blocked_review["reviewed_source_refs"] = [
        {key: value[key] for key in ("source_id", "segment_id", "text_sha256")}
        for value in blocked_sidecar["review_source_excerpts"]
    ]
    with pytest.raises(ProvenanceError, match="review_outcome_conflict"):
        validate_independent_review(canonical, blocked_sidecar, blocked_review)
    contradictory_approved = _approved_claims(True)
    contradictory_approved[0]["evidence"][0]["relationship"] = "contradicts"
    contradictory_sidecar = build_provenance_sidecar(
        canonical, _source_context(), claims, run_id="run-1",
        user_background_context=_background_context(), approved_claims=contradictory_approved,
    )
    contradictory_review = copy.deepcopy(blocked_review)
    contradictory_review["sidecar_sha256"] = contradictory_sidecar["sidecar_sha256"]
    with pytest.raises(ProvenanceError, match="review_outcome_conflict"):
        validate_independent_review(canonical, contradictory_sidecar, contradictory_review)


def test_shared_approved_segment_is_deduplicated_for_review_but_retained_per_claim():
    from api.expert_teams.research_provenance import (
        build_provenance_sidecar,
        mechanical_binding_validation,
    )

    approved = _approved_claims()
    shared = copy.deepcopy(approved[0])
    shared["claim_id"] = "claim-3"
    shared["statement"] = "同一原文可支撑另一已批准论断。"
    approved.append(shared)
    claims = [
        {"claim_id": "claim-1", "usages": [{"usage_id": "usage-1", "chapter_id": "current", "rendered_excerpt": "企业推进人工智能需要治理与培训。"}]},
        {"claim_id": "claim-3", "usages": [{"usage_id": "usage-3", "chapter_id": "current", "rendered_excerpt": "企业推进人工智能需要治理与培训。"}]},
    ]
    sidecar = build_provenance_sidecar(
        _canonical(), _source_context(), claims, run_id="run-1", approved_claims=approved,
    )
    assert len(sidecar["review_source_excerpts"]) == 1
    assert len(sidecar["claims"][0]["evidence_refs"]) == 1
    assert len(sidecar["claims"][1]["evidence_refs"]) == 1
    assert mechanical_binding_validation(_canonical(), sidecar)["passed"] is True
