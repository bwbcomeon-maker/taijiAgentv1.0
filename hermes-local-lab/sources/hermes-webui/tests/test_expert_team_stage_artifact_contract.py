import hashlib
import json

import pytest


META_START = "<<<TAIJI_META_V1>>>"
META_END = "<<<TAIJI_META_END>>>"
DOC_START = "<<<TAIJI_DOCUMENT_V1>>>"
DOC_END = "<<<TAIJI_DOCUMENT_END>>>"


def _issue(issue_id="ISS-1", severity="info"):
    return {
        "issue_id": issue_id,
        "severity": severity,
        "category": "brief",
        "field_path": None,
        "message": "需人工确认",
        "suggested_action": "核对原始资料",
    }


def _raw(artifact_type, payload, *, document=None, blocking_issues=None):
    meta = {
        "artifact_type": artifact_type,
        "summary": "阶段产物摘要",
        "payload": payload,
        "blocking_issues": blocking_issues or [],
    }
    text = f"{META_START}\n{json.dumps(meta, ensure_ascii=False)}\n{META_END}"
    if document is not None:
        text += f"\n{DOC_START}\n{document}\n{DOC_END}"
    return text


def _brief():
    return {
        "schema_version": "document-brief/v1",
        "revision": 3,
        "status": "confirmed",
        "confirmed_revision": 3,
        "confirmed_sha256": "b" * 64,
        "exact_title": "迎峰度夏保供电重点工作月度汇报",
        "document_type": "work_report",
        "content_constraints": {
            "required_sections": ["工作开展情况", "存在问题", "下一步工安排"],
            "must_include": [],
            "must_avoid": [],
        },
    }


def _research_brief():
    brief = _brief()
    brief.update(
        {
            "exact_title": "企业本地优先 AI 助理落地研究报告",
            "document_type": "research_report",
            "purpose": "支撑企业内部技术路线决策",
            "source_policy": {"mode": "provided_only", "as_of_date": "2026-07-15", "citation_style": "source_id"},
            "details": {"core_question": "如何落地", "time_range": {"start": "2025-01-01", "end": "2026-07-15"}},
        }
    )
    return brief


def _writing_plan_payload():
    return {
        "objective": "形成面向管理层的月度汇报",
        "document_type": "work_report",
        "section_plan": [
            {
                "section_id": "SEC-1",
                "heading": "工作开展情况",
                "purpose": "汇报进展",
                "required_fact_ids": ["FACT-1"],
            },
            {
                "section_id": "SEC-2",
                "heading": "存在问题",
                "purpose": "说明问题",
                "required_fact_ids": [],
            },
            {
                "section_id": "SEC-3",
                "heading": "下一步工安排",
                "purpose": "明确后续安排",
                "required_fact_ids": [],
            },
        ],
        "fact_requirements": [
            {
                "fact_id": "FACT-1",
                "description": "重点指标完成情况",
                "required": True,
                "source_requirement": "provided_source",
            }
        ],
        "assumptions": [],
        "acceptance_checks": ["标题与规格一致"],
    }


def _snapshot():
    text = "重点指标完成率98.7%"
    return {
        "schema_version": "expert-source-context/v1",
        "snapshot_id": "source-context:1",
        "sha256": "c" * 64,
        "sources": [
            {
                "source_id": "SRC-001",
                "kind": "local_file",
                "label": "月度数据",
                "locator": "source:SRC-001",
                "source_sha256": "a" * 64,
                "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "content_text": text,
                "segments": [
                    {
                        "segment_id": "SRC-001:S0001",
                        "locator": "chars:0-13",
                        "char_start": 0,
                        "char_end": len(text),
                        "text": text,
                        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    }
                ],
            }
        ],
    }


def test_parser_accepts_exact_meta_and_document_blocks():
    from api.expert_teams.stage_artifacts import parse_stage_response

    parsed = parse_stage_response(
        _raw(
            "document_draft",
            {
                "title": _brief()["exact_title"],
                "document_type": "work_report",
                "section_map": [{"section_id": "SEC-1", "heading": "工作开展情况"}],
                "fact_usage": [{"fact_id": "FACT-1", "section_id": "SEC-1"}],
                "asset_requests": [],
                "open_issues": [],
            },
            document=f"# {_brief()['exact_title']}\n\n## 工作开展情况\n已完成重点任务。",
        ),
        artifact_type="document_draft",
        requires_document=True,
    )
    assert parsed["artifact_type"] == "document_draft"
    assert parsed["deliverable_markdown"].startswith("# ")


@pytest.mark.parametrize(
    "raw",
    [
        "前置说明\n" + _raw("writing_plan", _writing_plan_payload()),
        _raw("writing_plan", _writing_plan_payload()) + "\n后置说明",
        _raw("writing_plan", _writing_plan_payload()) + "\n" + _raw("writing_plan", _writing_plan_payload()),
        f"{META_START}\n{{not-json}}\n{META_END}",
        _raw("document_draft", {}, document=None),
        _raw("writing_plan", _writing_plan_payload(), document="# 不应出现"),
    ],
)
def test_parser_rejects_duplicate_missing_or_extraneous_blocks(raw):
    from api.expert_teams.stage_artifacts import StageArtifactError, parse_stage_response

    requires_document = "document_draft" in raw
    artifact_type = "document_draft" if requires_document else "writing_plan"
    with pytest.raises(StageArtifactError):
        parse_stage_response(raw, artifact_type=artifact_type, requires_document=requires_document)


@pytest.mark.parametrize("phrase", ["负责专家", "Stage 4", "复核交付", "本阶段", "可直接生成 DOCX"])
def test_document_purity_rejects_internal_workflow_language(phrase):
    from api.expert_teams.stage_artifacts import document_purity_issues

    markdown = f"# {_brief()['exact_title']}\n\n## 工作开展情况\n{phrase}已完成检查。"
    issues = document_purity_issues(markdown)
    assert issues and issues[0]["severity"] == "blocking"


def test_writing_plan_schema_rejects_unknown_fields_and_missing_required_sections():
    from api.expert_teams.stage_artifacts import StageArtifactError, build_stage_artifact, parse_stage_response

    payload = _writing_plan_payload()
    payload["unknown"] = "must fail"
    parsed = parse_stage_response(_raw("writing_plan", payload), artifact_type="writing_plan", requires_document=False)
    with pytest.raises(StageArtifactError) as unknown:
        build_stage_artifact(
            parsed,
            stage_id="plan",
            stage_attempt=1,
            brief=_brief(),
            input_refs=[],
            now="2026-07-15T10:00:00+08:00",
        )
    assert unknown.value.code == "unknown_field"

    payload = _writing_plan_payload()
    payload["section_plan"] = payload["section_plan"][:1]
    parsed = parse_stage_response(_raw("writing_plan", payload), artifact_type="writing_plan", requires_document=False)
    with pytest.raises(StageArtifactError) as missing:
        build_stage_artifact(parsed, stage_id="plan", stage_attempt=1, brief=_brief(), input_refs=[], now="2026-07-15T10:00:00+08:00")
    assert missing.value.code == "required_section_missing"


def test_material_ledger_enriches_evidence_only_from_bound_snapshot():
    from api.expert_teams.stage_artifacts import build_stage_artifact, parse_stage_response

    payload = {
        "source_assessments": [
            {"source_id": "SRC-001", "evidence_grade": "A", "applicability": "本月指标", "status": "included", "exclusion_reason": None}
        ],
        "facts": [
            {
                "fact_id": "FACT-1",
                "statement": "重点指标完成率98.7%",
                "evidence_refs": [{"source_id": "SRC-001", "segment_id": "SRC-001:S0001", "relationship": "supports"}],
                "status": "verified",
                "usable": True,
            }
        ],
        "gaps": [],
    }
    parsed = parse_stage_response(_raw("material_ledger", payload), artifact_type="material_ledger", requires_document=False)
    artifact = build_stage_artifact(
        parsed,
        stage_id="materials",
        stage_attempt=1,
        brief=_brief(),
        input_refs=[{"ref_type": "source_context", "snapshot_id": "source-context:1", "sha256": "c" * 64}],
        source_snapshot=_snapshot(),
        now="2026-07-15T10:00:00+08:00",
    )
    evidence = artifact["payload"]["facts"][0]["evidence_refs"][0]
    assert evidence["segment_sha256"] == _snapshot()["sources"][0]["segments"][0]["text_sha256"]
    assert evidence["locator"] == "chars:0-13"
    assert artifact["validation_status"] == "valid"


@pytest.mark.parametrize("source_id,segment_id", [("SRC-404", "SRC-001:S0001"), ("SRC-001", "SRC-001:S9999")])
def test_material_ledger_rejects_unknown_source_or_segment(source_id, segment_id):
    from api.expert_teams.stage_artifacts import StageArtifactError, build_stage_artifact, parse_stage_response

    payload = {
        "source_assessments": [{"source_id": source_id, "evidence_grade": "A", "applicability": "x", "status": "included", "exclusion_reason": None}],
        "facts": [{"fact_id": "FACT-1", "statement": "x", "evidence_refs": [{"source_id": source_id, "segment_id": segment_id, "relationship": "supports"}], "status": "verified", "usable": True}],
        "gaps": [],
    }
    parsed = parse_stage_response(_raw("material_ledger", payload), artifact_type="material_ledger", requires_document=False)
    with pytest.raises(StageArtifactError) as error:
        build_stage_artifact(parsed, stage_id="materials", stage_attempt=1, brief=_brief(), input_refs=[{"ref_type": "source_context", "snapshot_id": "source-context:1", "sha256": "c" * 64}], source_snapshot=_snapshot(), now="2026-07-15T10:00:00+08:00")
    assert error.value.code in {"unknown_source_id", "unknown_segment_id"}


def test_artifact_digest_detects_title_drift_and_tampering():
    from api.expert_teams.stage_artifacts import StageArtifactError, artifact_digest, build_stage_artifact, validate_stage_artifact, parse_stage_response

    payload = {
        "title": "错误标题",
        "document_type": "work_report",
        "section_map": [{"section_id": "SEC-1", "heading": "工作开展情况"}],
        "fact_usage": [{"fact_id": "FACT-1", "section_id": "SEC-1"}],
        "asset_requests": [],
        "open_issues": [],
    }
    parsed = parse_stage_response(_raw("document_draft", payload, document="# 错误标题\n\n正文"), artifact_type="document_draft", requires_document=True)
    with pytest.raises(StageArtifactError) as drift:
        build_stage_artifact(parsed, stage_id="draft", stage_attempt=1, brief=_brief(), input_refs=[], now="2026-07-15T10:00:00+08:00")
    assert drift.value.code == "title_mismatch"

    parsed = parse_stage_response(_raw("writing_plan", _writing_plan_payload()), artifact_type="writing_plan", requires_document=False)
    artifact = build_stage_artifact(parsed, stage_id="plan", stage_attempt=1, brief=_brief(), input_refs=[], now="2026-07-15T10:00:00+08:00")
    assert artifact["sha256"] == artifact_digest(artifact)
    artifact["summary"] = "tampered"
    with pytest.raises(StageArtifactError) as tampered:
        validate_stage_artifact(artifact, brief=_brief(), approved_inputs=[])
    assert tampered.value.code == "artifact_hash_mismatch"


def test_research_charter_and_outline_use_exact_nested_schemas():
    from api.expert_teams.stage_artifacts import build_stage_artifact, parse_stage_response

    charter = {
        "core_question": "如何落地",
        "decision_to_support": "技术路线决策",
        "scope_in": ["内部办公"],
        "scope_out": ["对外营销"],
        "time_range": {"start": "2025-01-01", "end": "2026-07-15"},
        "source_policy": {"mode": "provided_only", "as_of_date": "2026-07-15", "citation_style": "source_id"},
        "subquestions": ["数据边界是什么"],
        "evaluation_criteria": ["安全合规"],
        "stop_conditions": ["资料不足时停止"],
    }
    parsed = parse_stage_response(_raw("research_charter", charter), artifact_type="research_charter", requires_document=False)
    artifact = build_stage_artifact(parsed, stage_id="direction", stage_attempt=1, brief=_research_brief(), input_refs=[], now="2026-07-15T10:00:00+08:00")
    assert artifact["validation_status"] == "valid"

    outline = {
        "sections": [{"section_id": "SEC-1", "heading": "研究结论", "thesis": "先建受控数据边界", "claim_ids": ["CLM-1"], "source_ids": ["SRC-001"], "open_questions": []}],
        "conclusion_boundaries": ["仅基于已提供资料"],
    }
    parsed = parse_stage_response(_raw("research_outline", outline), artifact_type="research_outline", requires_document=False)
    artifact = build_stage_artifact(parsed, stage_id="outline", stage_attempt=1, brief=_research_brief(), input_refs=[{"ref_type": "stage_artifact", "artifact_id": "evidence:1", "sha256": "d" * 64}], now="2026-07-15T10:00:00+08:00")
    assert artifact["payload"]["sections"][0]["claim_ids"] == ["CLM-1"]


def test_source_register_and_evidence_matrix_are_enriched_from_snapshot():
    from api.expert_teams.stage_artifacts import build_stage_artifact, parse_stage_response

    source_ref = [{"ref_type": "source_context", "snapshot_id": "source-context:1", "sha256": "c" * 64}]
    register = {
        "source_assessments": [{"source_id": "SRC-001", "evidence_grade": "A", "applicability": "企业办公", "status": "included", "exclusion_reason": None}],
        "search_gaps": [{"gap_id": "GAP-1", "question": "缺少长期数据吗", "required": False, "blocks_final": False, "reason": "当前仅月度数据", "resolution_status": "accepted_out_of_scope", "source_ids": []}],
    }
    parsed = parse_stage_response(_raw("source_register", register), artifact_type="source_register", requires_document=False)
    artifact = build_stage_artifact(parsed, stage_id="research", stage_attempt=1, brief=_research_brief(), input_refs=source_ref, source_snapshot=_snapshot(), now="2026-07-15T10:00:00+08:00")
    assert artifact["payload"]["sources"][0]["source_id"] == "SRC-001"
    assert artifact["payload"]["sources"][0]["source_sha256"] == "a" * 64

    matrix = {
        "claims": [{"claim_id": "CLM-1", "statement": "重点指标完成率98.7%", "claim_type": "fact", "evidence": [{"source_id": "SRC-001", "segment_id": "SRC-001:S0001", "relationship": "supports"}], "status": "verified", "confidence": "high", "notes": "来自月度数据"}],
        "contradictions": [],
        "gaps": [],
    }
    parsed = parse_stage_response(_raw("evidence_matrix", matrix), artifact_type="evidence_matrix", requires_document=False)
    artifact = build_stage_artifact(parsed, stage_id="evidence", stage_attempt=1, brief=_research_brief(), input_refs=source_ref, source_snapshot=_snapshot(), now="2026-07-15T10:00:00+08:00")
    evidence = artifact["payload"]["claims"][0]["evidence"][0]
    assert evidence["segment_sha256"] == _snapshot()["sources"][0]["segments"][0]["text_sha256"]


def test_review_report_requires_exact_check_keys_and_open_issue_ids():
    from api.expert_teams.stage_artifacts import StageArtifactError, build_stage_artifact, parse_stage_response

    payload = {
        "title": _brief()["exact_title"],
        "document_type": "work_report",
        "section_map": [{"section_id": "SEC-1", "heading": "工作开展情况"}],
        "fact_usage": [],
        "asset_requests": [],
        "review_report": {
            "schema_version": "content-review-report/v1",
            "checks": {"brief_alignment": "passed"},
            "issues": [],
            "change_summary": [],
            "unresolved_issue_ids": [],
        },
        "open_issues": [],
    }
    parsed = parse_stage_response(_raw("reviewed_document", payload, document=f"# {_brief()['exact_title']}\n\n正文"), artifact_type="reviewed_document", requires_document=True)
    with pytest.raises(StageArtifactError) as error:
        build_stage_artifact(parsed, stage_id="polish", stage_attempt=1, brief=_brief(), input_refs=[], now="2026-07-15T10:00:00+08:00")
    assert error.value.code == "review_checks_mismatch"


def test_delivery_manifest_rejects_unknown_fields_and_path_escape():
    from api.expert_teams.stage_artifacts import StageArtifactError, build_stage_artifact, parse_stage_response

    manifest = {
        "schema_version": "delivery-manifest/v1",
        "delivery_binding_path": "attempt-1/expert-team-delivery.json",
        "delivery_binding_sha256": "d" * 64,
        "render_input_fingerprint": "e" * 64,
        "delivery_attempt": 1,
        "document_revision": 1,
        "automatic_check_summary": {"status": "passed", "passed_count": 5, "failed_count": 0, "warning_count": 0, "blocking_count": 0},
        "office_review_required": True,
    }
    parsed = parse_stage_response(_raw("delivery_manifest", manifest), artifact_type="delivery_manifest", requires_document=False)
    artifact = build_stage_artifact(parsed, stage_id="delivery", stage_attempt=1, brief=_brief(), input_refs=[{"ref_type": "stage_artifact", "artifact_id": "polish:1", "sha256": "f" * 64}], now="2026-07-15T10:00:00+08:00")
    assert artifact["payload"]["office_review_required"] is True

    for mutation in ({"unknown": True}, {"delivery_binding_path": "../escape.json"}):
        bad = {**manifest, **mutation}
        parsed = parse_stage_response(_raw("delivery_manifest", bad), artifact_type="delivery_manifest", requires_document=False)
        with pytest.raises(StageArtifactError):
            build_stage_artifact(parsed, stage_id="delivery", stage_attempt=1, brief=_brief(), input_refs=[], now="2026-07-15T10:00:00+08:00")
