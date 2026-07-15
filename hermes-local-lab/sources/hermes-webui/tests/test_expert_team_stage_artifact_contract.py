import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture(autouse=True)
def _enable_contract_pilot_for_contract_tests(monkeypatch):
    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")


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
        "document_control": {
            "render_template_id": "enterprise-work-report",
            "client": "国家电网有限公司",
            "issuer": "办公室",
            "compiler": "信息化工作组",
            "version_label": "V1.0",
            "classification": "internal",
            "classification_label": "内部资料",
            "document_date": "2026-07-15",
        },
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


def test_catalog_declares_executor_artifact_dependencies_and_hidden_research_delivery():
    from api import expert_teams

    catalog = {team["id"]: team for team in expert_teams.expert_team_catalog()["teams"]}
    content = {stage["id"]: stage for stage in catalog["content-creator-team"]["tasks"]}
    research = {stage["id"]: stage for stage in catalog["deep-research-team"]["tasks"]}

    assert content["plan"] == {**content["plan"], "executor": "model", "artifact_type": "writing_plan", "depends_on": []}
    assert content["materials"]["depends_on"] == ["plan"]
    assert content["draft"]["depends_on"] == ["plan", "materials"]
    assert content["polish"]["depends_on"] == ["materials", "draft"]
    assert content["delivery"]["executor"] == "system"
    assert content["delivery"]["artifact_type"] == "delivery_manifest"
    assert content["delivery"]["depends_on"] == ["polish"]

    assert research["direction"]["artifact_type"] == "research_charter"
    assert research["evidence"]["artifact_type"] == "evidence_matrix"
    assert research["review"]["artifact_type"] == "reviewed_research_document"
    assert len(research) == 6
    hidden = catalog["deep-research-team"]["post_approval_system_steps"]
    assert hidden == [
        {
            "id": "delivery",
            "executor": "system",
            "artifact_type": "delivery_manifest",
            "depends_on": ["review"],
            "trigger": "canonical_approved",
            "visible_progress": False,
        }
    ]


def _contract_run_ready_for_attempt(tmp_path, *, stage_index=0):
    from api import expert_teams
    from api.expert_teams.storage import read_run, write_run

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "contract_version": "expert-team-contract/v1",
            "session_id": "sid-attempt",
            "team_id": "content-creator-team",
            "document_type": "work_report",
            "template_id": "work_report",
            "prompt": "起草工作汇报",
            "document_brief_seed": {
                "document_control": {"render_template_id": "enterprise-work-report"}
            },
        },
    )
    stored = read_run(tmp_path, run["run_id"])
    stored["workflow_state"] = "ready_to_generate"
    stored["document_brief"] = _brief()
    stored["current_stage_index"] = stage_index
    task = stored["_tasks_template"][stage_index]
    stored["current_stage"] = {
        "index": stage_index,
        "id": task["id"],
        "task_id": task["id"],
        "status": "pending",
    }
    return write_run(tmp_path, stored)


def test_stage_attempt_reservation_is_monotonic_idempotent_and_not_output_count_based(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import read_run, write_run

    run = _contract_run_ready_for_attempt(tmp_path)
    first_run, first, created = expert_teams.reserve_stage_attempt(
        tmp_path,
        run["run_id"],
        stage_id="plan",
        executor="model",
        input_refs=[],
        idempotency_key="lineage-1",
    )
    assert created is True
    assert first["stage_attempt"] == 1
    replay_run, replay, replay_created = expert_teams.reserve_stage_attempt(
        tmp_path,
        run["run_id"],
        stage_id="plan",
        executor="model",
        input_refs=[],
        idempotency_key="lineage-1",
    )
    assert replay_created is False
    assert replay == first
    assert replay_run["version"] == first_run["version"]

    stored = read_run(tmp_path, run["run_id"])
    stored["stage_outputs"] = [{"task_id": "plan"}] * 99
    stored["stage_attempt_reservations"][-1]["status"] = "failed"
    stored["current_stage_attempt_reservation"] = {}
    write_run(tmp_path, stored)
    _, second, second_created = expert_teams.reserve_stage_attempt(
        tmp_path,
        run["run_id"],
        stage_id="plan",
        executor="model",
        input_refs=[],
        idempotency_key="lineage-2",
    )
    assert second_created is True
    assert second["stage_attempt"] == 2


def test_concurrent_stage_attempt_reserve_creates_only_one_authoritative_attempt(tmp_path):
    from api import expert_teams

    run = _contract_run_ready_for_attempt(tmp_path)

    def reserve(key):
        try:
            return expert_teams.reserve_stage_attempt(
                tmp_path,
                run["run_id"],
                stage_id="plan",
                executor="model",
                input_refs=[],
                idempotency_key=key,
            )[1]["stage_attempt"]
        except expert_teams.ExpertTeamStateConflict as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ["concurrent-a", "concurrent-b"]))
    assert sorted(map(str, results)) == ["1", "stage_attempt_in_progress"]


def test_system_executor_uses_the_same_stage_attempt_allocator(tmp_path):
    from api import expert_teams

    run = _contract_run_ready_for_attempt(tmp_path, stage_index=4)
    _, reservation, created = expert_teams.reserve_stage_attempt(
        tmp_path,
        run["run_id"],
        stage_id="delivery",
        executor="system",
        input_refs=[{"ref_type": "stage_artifact", "artifact_id": "polish:1", "sha256": "f" * 64}],
        idempotency_key="system-delivery-1",
    )
    assert created is True
    assert reservation["stage_attempt"] == 1
    assert reservation["executor"] == "system"
    assert reservation["artifact_type"] == "delivery_manifest"


def _generated_contract_plan(expert_teams, tmp_path):
    run = _contract_run_ready_for_attempt(tmp_path)
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        run["run_id"],
        expected_version=run["version"],
        runtime_adapter="RunnerRuntimeAdapter",
        input_refs=[],
    )
    generating = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        run["run_id"],
        {
            "stream_id": "stream-contract-1",
            "runtime_run_id": "runner-contract-1",
            "runtime_adapter": "RunnerRuntimeAdapter",
            "execution_start_id": reserved["execution_start_id"],
        },
    )
    raw = _raw("writing_plan", _writing_plan_payload())
    reviewed = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        run["run_id"],
        {
            "stream_id": generating["execution_stream_id"],
            "stage_id": "plan",
            "attempt": generating["execution_attempt"],
            "id": "output-contract-1",
            "kind": "chat",
            "content": raw,
        },
    )
    return reviewed, raw


def test_contract_model_result_persists_raw_and_immutable_structured_artifact(tmp_path):
    from api import expert_teams

    reviewed, raw = _generated_contract_plan(expert_teams, tmp_path)
    assert reviewed["workflow_state"] == "awaiting_review"
    assert reviewed["stage_outputs"][-1]["content"] == raw
    artifact = reviewed["stage_artifacts"][-1]
    assert artifact["artifact_id"] == "plan:1"
    assert artifact["artifact_type"] == "writing_plan"
    assert reviewed["current_stage_artifact_ref"] == {
        "artifact_id": "plan:1",
        "sha256": artifact["sha256"],
        "stage_attempt": 1,
    }
    assert reviewed["current_stage_attempt_reservation"]["status"] == "generated_valid"
    view_json = json.dumps(reviewed["view"], ensure_ascii=False)
    assert "<<<TAIJI_META_V1>>>" not in view_json
    assert reviewed["view"]["stage_result"]["artifact_type"] == "writing_plan"


def test_contract_approval_requires_trusted_identity_and_records_safe_snapshot(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import trusted_identity

    reviewed, _ = _generated_contract_plan(expert_teams, tmp_path)
    body = {
        "session_id": reviewed["session_id"],
        "run_id": reviewed["run_id"],
        "stage_id": "plan",
        "expected_version": reviewed["version"],
        "idempotency_key": "approve-plan-1",
        "trusted_identity_session_id": "missing",
    }
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as error:
        expert_teams.approve_expert_team_stage(tmp_path, body)
    assert error.value.code == "trusted_identity_provider_required"

    resolver = trusted_identity.TrustedIdentityResolver({"enabled": False}, production=False)
    resolver._config = {"enabled": True}
    identity_session = resolver.install_test_principal(
        {
            "subject": "approver-001",
            "display_name": "张三",
            "roles": ["document-approver"],
            "issuer": "test",
            "audience": "test",
            "authenticated_at": 1,
            "credential_jti_sha256": "a" * 64,
            "key_fingerprint": "b" * 64,
            "auth_method": "test",
        }
    )
    monkeypatch.setattr(trusted_identity, "get_trusted_identity_resolver", lambda: resolver)
    body["trusted_identity_session_id"] = identity_session
    approved = expert_teams.approve_expert_team_stage(tmp_path, body)
    assert approved["workflow_state"] == "ready_to_generate"
    assert approved["stage_outputs"][-1]["status"] == "approved"
    approval = approved["stage_approvals"][-1]
    assert approval["artifact_id"] == "plan:1"
    assert approval["approved_principal"]["subject"] == "approver-001"
    serialized = json.dumps(approved, ensure_ascii=False)
    assert identity_session not in serialized
    replay = expert_teams.approve_expert_team_stage(tmp_path, body)
    assert replay == approved
    assert len(replay["stage_approvals"]) == 1


def test_invalid_contract_result_keeps_raw_but_never_creates_artifact(tmp_path):
    from api import expert_teams

    run = _contract_run_ready_for_attempt(tmp_path)
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        run["run_id"],
        expected_version=run["version"],
        runtime_adapter="RunnerRuntimeAdapter",
        input_refs=[],
    )
    generating = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        run["run_id"],
        {"stream_id": "stream-invalid", "execution_start_id": reserved["execution_start_id"]},
    )
    invalid = "负责专家：写作总导演\nStage 1 已完成"
    result = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        run["run_id"],
        {
            "stream_id": generating["execution_stream_id"],
            "stage_id": "plan",
            "attempt": generating["execution_attempt"],
            "id": "output-invalid",
            "kind": "chat",
            "content": invalid,
        },
    )
    assert result["workflow_state"] == "generated_invalid"
    assert result["stage_outputs"][-1]["content"] == invalid
    assert result["stage_outputs"][-1]["status"] == "invalid"
    assert result["stage_artifacts"] == []
    assert result["current_stage_attempt_reservation"]["status"] == "generated_invalid"


def test_approved_reviewed_document_alone_sets_canonical_pointer_and_waits_for_delivery(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import trusted_identity

    run = _contract_run_ready_for_attempt(tmp_path, stage_index=3)
    input_refs = [
        {"ref_type": "stage_artifact", "artifact_id": "materials:1", "sha256": "1" * 64},
        {"ref_type": "stage_artifact", "artifact_id": "draft:1", "sha256": "2" * 64},
    ]
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        run["run_id"],
        expected_version=run["version"],
        runtime_adapter="RunnerRuntimeAdapter",
        input_refs=input_refs,
    )
    generating = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        run["run_id"],
        {"stream_id": "stream-review", "execution_start_id": reserved["execution_start_id"]},
    )
    checks = {
        "brief_alignment": "passed",
        "fact_traceability": "passed",
        "document_purity": "passed",
        "confidentiality": "passed",
        "document_structure": "passed",
    }
    payload = {
        "title": _brief()["exact_title"],
        "document_type": "work_report",
        "section_map": [{"section_id": "SEC-1", "heading": "工作开展情况"}],
        "fact_usage": [],
        "asset_requests": [],
        "review_report": {
            "schema_version": "content-review-report/v1",
            "checks": checks,
            "issues": [],
            "change_summary": ["完成语义复核"],
            "unresolved_issue_ids": [],
        },
        "open_issues": [],
    }
    raw = _raw(
        "reviewed_document",
        payload,
        document=f"# {_brief()['exact_title']}\n\n## 工作开展情况\n\n重点任务按计划推进。",
    )
    reviewed = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        run["run_id"],
        {
            "stream_id": generating["execution_stream_id"],
            "stage_id": "polish",
            "attempt": generating["execution_attempt"],
            "id": "output-review",
            "kind": "chat",
            "content": raw,
        },
    )
    resolver = trusted_identity.TrustedIdentityResolver({"enabled": False}, production=False)
    resolver._config = {"enabled": True}
    identity_session = resolver.install_test_principal(
        {
            "subject": "approver-review",
            "display_name": "李四",
            "roles": ["document-approver"],
            "expires_at": int(time.time()) + 3600,
        }
    )
    monkeypatch.setattr(trusted_identity, "get_trusted_identity_resolver", lambda: resolver)
    approved = expert_teams.approve_expert_team_stage(
        tmp_path,
        {
            "session_id": reviewed["session_id"],
            "run_id": reviewed["run_id"],
            "stage_id": "polish",
            "expected_version": reviewed["version"],
            "idempotency_key": "approve-review",
            "trusted_identity_session_id": identity_session,
        },
    )
    artifact = reviewed["stage_artifacts"][-1]
    assert approved["canonical_document_ref"] == {
        "artifact_id": artifact["artifact_id"],
        "sha256": artifact["sha256"],
        "brief_revision": _brief()["confirmed_revision"],
        "brief_sha256": _brief()["confirmed_sha256"],
    }
    assert approved["workflow_state"] == "delivery_validation_required"
    assert approved.get("completion_integrity") is None
    assert approved["pending_system_stage"] == {
        "id": "delivery",
        "title": "交付确认",
        "phase": "交付确认",
        "worker_id": "delivery",
        "worker_name": "交付复核专家",
        "executor": "system",
        "artifact_type": "delivery_manifest",
        "depends_on": ["polish"],
    }


def test_system_delivery_dispatch_never_uses_gateway_and_production_adapter_is_canonical(monkeypatch, tmp_path):
    from api import expert_teams
    from api import routes
    from api.expert_teams import trusted_identity
    from api.expert_teams.stage_artifacts import build_stage_artifact, parse_stage_response
    from api.expert_teams.system_stages import SystemStageError, SystemStageRegistry, dispatch_system_stage, get_system_stage_registry

    # Reuse the semantic-review path to create a real canonical pointer and pending descriptor.
    run = _contract_run_ready_for_attempt(tmp_path, stage_index=3)
    input_refs = [
        {"ref_type": "stage_artifact", "artifact_id": "materials:1", "sha256": "1" * 64},
        {"ref_type": "stage_artifact", "artifact_id": "draft:1", "sha256": "2" * 64},
    ]
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path, run["run_id"], expected_version=run["version"], runtime_adapter="RunnerRuntimeAdapter", input_refs=input_refs
    )
    generating = expert_teams.mark_expert_team_execution_started(
        tmp_path, run["run_id"], {"stream_id": "stream-system-pre", "execution_start_id": reserved["execution_start_id"]}
    )
    payload = {
        "title": _brief()["exact_title"], "document_type": "work_report",
        "section_map": [{"section_id": "SEC-1", "heading": "工作开展情况"}],
        "fact_usage": [], "asset_requests": [],
        "review_report": {
            "schema_version": "content-review-report/v1",
            "checks": {key: "passed" for key in ("brief_alignment", "fact_traceability", "document_purity", "confidentiality", "document_structure")},
            "issues": [], "change_summary": ["通过"], "unresolved_issue_ids": [],
        },
        "open_issues": [],
    }
    reviewed = expert_teams.mark_expert_team_execution_complete(
        tmp_path, run["run_id"],
        {"stream_id": generating["execution_stream_id"], "stage_id": "polish", "attempt": generating["execution_attempt"], "id": "review-system", "kind": "chat", "content": _raw("reviewed_document", payload, document=f"# {_brief()['exact_title']}\n\n## 工作开展情况\n\n正文。")},
    )
    resolver = trusted_identity.TrustedIdentityResolver({"enabled": False}, production=False)
    resolver._config = {"enabled": True}
    identity_session = resolver.install_test_principal(
        {"subject": "approver", "display_name": "审批人", "roles": ["document-approver"], "expires_at": int(time.time()) + 3600}
    )
    monkeypatch.setattr(trusted_identity, "get_trusted_identity_resolver", lambda: resolver)
    approved = expert_teams.approve_expert_team_stage(
        tmp_path,
        {"session_id": reviewed["session_id"], "run_id": reviewed["run_id"], "stage_id": "polish", "expected_version": reviewed["version"], "idempotency_key": "approve-system", "trusted_identity_session_id": identity_session},
    )
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Gateway/model resolution must stay at zero")),
    )
    delivered_payload, delivered_status = routes._start_expert_team_execution(tmp_path, approved, {})
    assert delivered_status == 200, delivered_payload
    assert delivered_payload["ok"] is True
    reserved_run = delivered_payload["run"]
    descriptor = reserved_run["pending_system_stage"]
    system_reservation = reserved_run["current_stage_attempt_reservation"]
    with pytest.raises(SystemStageError) as unavailable:
        dispatch_system_stage(reserved_run, descriptor, system_reservation, registry=SystemStageRegistry())
    assert unavailable.value.code == "delivery_contract_unavailable"
    manifest = reserved_run["stage_artifacts"][-1]
    assert manifest["artifact_type"] == "delivery_manifest"
    assert manifest["payload"]["automatic_check_summary"]["status"] == "passed"
    assert reserved_run["current_delivery_manifest_ref"]["sha256"] == manifest["sha256"]
    assert reserved_run["current_delivery_attempt_reservation"]["status"] == "generated_valid"
    assert reserved_run["workflow_state"] == "awaiting_review"
    replay = dispatch_system_stage(
        reserved_run,
        descriptor,
        system_reservation,
        registry=get_system_stage_registry(tmp_path),
    )
    assert replay["artifact"] == manifest
    assert reserved_run["delivery_attempt_counter"] == 1
    binding_path = tmp_path / manifest["payload"]["delivery_binding_path"]
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    for field in (
        "canonical_markdown", "asset_manifest", "semantic_gates", "document",
        "automatic_quality_report", "layered_quality_report",
    ):
        target = binding_path.parent / binding[field]["path"]
        original = target.read_bytes()
        target.write_bytes(original + b"tamper")
        with pytest.raises(SystemStageError) as changed:
            dispatch_system_stage(
                reserved_run,
                descriptor,
                system_reservation,
                registry=get_system_stage_registry(tmp_path),
            )
        assert changed.value.code == "delivery_binding_changed"
        target.write_bytes(original)


def test_research_hidden_delivery_descriptor_reserves_system_attempt_without_changing_six_step_progress(tmp_path):
    from api import expert_teams
    from api.expert_teams.catalog import get_template
    from api.expert_teams.storage import read_run, write_run
    from api.expert_teams.stage_artifacts import artifact_digest
    from api.expert_teams.system_stages import get_system_stage_registry, dispatch_system_stage

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "contract_version": "expert-team-contract/v1",
            "session_id": "sid-research-system",
            "team_id": "deep-research-team",
            "document_type": "research_report",
            "template_id": "research_report",
            "prompt": "研究企业 AI 办公落地",
            "document_brief_seed": {
                "document_control": {"render_template_id": "enterprise-research-report"}
            },
        },
    )
    stored = read_run(tmp_path, run["run_id"])
    brief = _research_brief()
    brief["document_control"] = {
        "render_template_id": "enterprise-research-report",
        "client": "国家电网有限公司",
        "issuer": "研究中心",
        "compiler": "信息化研究组",
        "version_label": "V1.0",
        "classification": "internal",
        "classification_label": "内部资料",
        "document_date": "2026-07-15",
    }
    artifact = {
        "schema_version": "expert-stage-artifact/v1",
        "artifact_id": "review:1",
        "artifact_type": "reviewed_research_document",
        "stage_id": "review",
        "stage_attempt": 1,
        "brief_revision": brief["confirmed_revision"],
        "brief_sha256": brief["confirmed_sha256"],
        "input_refs": [],
        "summary": "研究报告已复核",
        "payload": {
            "title": brief["exact_title"],
            "section_map": [{"section_id": "SEC-1", "heading": "研究结论"}],
            "claim_usage": [],
            "review_report": {
                "schema_version": "research-review-report/v1",
                "checks": {key: "passed" for key in (
                    "brief_alignment", "citation_completeness", "unsupported_claims",
                    "unresolved_contradictions", "as_of_date_compliance", "document_purity", "confidentiality",
                )},
                "issues": [], "unsupported_claim_ids": [], "unresolved_contradiction_ids": [],
                "change_summary": ["通过"], "unresolved_issue_ids": [],
            },
            "open_issues": [],
        },
        "deliverable_markdown": f"# {brief['exact_title']}\n\n## 研究结论\n\n本报告形成受控研究结论。",
        "blocking_issues": [],
        "created_at": "2026-07-15T10:00:00+08:00",
        "validation_status": "valid",
    }
    artifact["sha256"] = artifact_digest(artifact)
    stored.update(
        {
            "workflow_state": "delivery_validation_required",
            "document_brief": brief,
            "current_stage_index": 6,
            "pending_system_stage": get_template("deep-research-team")["post_approval_system_steps"][0],
            "stage_artifacts": [artifact],
            "approved_stage_artifact_refs": {"review": {"artifact_id": "review:1", "sha256": artifact["sha256"]}},
            "canonical_document_ref": {
                "artifact_id": "review:1",
                "sha256": artifact["sha256"],
                "brief_revision": brief["confirmed_revision"],
                "brief_sha256": brief["confirmed_sha256"],
            },
        }
    )
    stored = write_run(tmp_path, stored)
    reserved_run, descriptor, reservation, created = expert_teams.reserve_system_stage_attempt(
        tmp_path, stored["run_id"], idempotency_key="research-hidden-delivery"
    )
    assert created is True
    assert len(reserved_run["tasks"]) == 6
    assert descriptor["visible_progress"] is False
    assert reservation["stage_attempt"] == 1
    result = dispatch_system_stage(
        reserved_run, descriptor, reservation, registry=get_system_stage_registry(tmp_path)
    )
    completed = expert_teams.complete_system_stage_attempt(
        tmp_path,
        stored["run_id"],
        reservation_id=reservation["reservation_id"],
        artifact=result["artifact"],
    )
    assert len(completed["tasks"]) == 6
    assert completed["view"]["workflow"]["progress"]["done"] == 6
    assert completed["view"]["workflow"]["progress"]["total"] == 6
    assert completed["stage_artifacts"][-1]["artifact_type"] == "delivery_manifest"
