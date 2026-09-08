import pytest


def test_quality_revision_feedback_uses_current_attachment_terms_not_fixture_examples():
    from api.expert_teams.runtime import _research_v3_quality_revision_plan

    run = {
        "research_v3_chapter_ledger": {
            "plan": {
                "chapters": [
                    {"chapter_id": "sec-research-question"},
                    {"chapter_id": "sec-conclusion-boundaries"},
                ]
            },
            "units": [
                {
                    "chapter_id": "sec-research-question",
                    "unit_index": 1,
                    "result": {"body": "附件内部标识 ATT-current-source 不得出现在正文。"},
                },
                {
                    "chapter_id": "sec-conclusion-boundaries",
                    "unit_index": 1,
                    "result": {"body": "### 试点观察\n第一段。"},
                },
                {
                    "chapter_id": "sec-conclusion-boundaries",
                    "unit_index": 2,
                    "result": {"body": "### 试点观察\n第二段。"},
                },
            ],
        }
    }

    targets, feedback = _research_v3_quality_revision_plan(run)

    assert targets == ["sec-research-question", "sec-conclusion-boundaries"]
    reader_feedback = "\n".join(feedback["sec-research-question"])
    duplicate_feedback = "\n".join(feedback["sec-conclusion-boundaries"])
    assert "当前附件中实际使用的原始段落号" in reader_feedback
    assert "E01-P01" not in reader_feedback
    assert "当前材料中的路径适用条件、实际资源约束及冲突" in duplicate_feedback
    assert "每周14人时" not in duplicate_feedback


def test_completed_quality_revision_releases_only_its_orphaned_prior_review_attempt():
    from api.expert_teams.runtime import (
        _completed_quality_revision_review_reservation_id,
        _stage_reservation_status_patch,
    )

    old_draft_sha = "a" * 64
    current_draft_sha = "b" * 64
    run = {
        "workflow_state": "ready_to_generate",
        "execution_status": "idle",
        "execution_start_id": "",
        "execution_stream_id": "",
        "current_stage": {"task_id": "review"},
        "research_v3_review_ledger": None,
        "research_v3_active_review_unit": None,
        "research_v3_review_revisions": {
            "reviewed_canonical_body_sha256": "c" * 64,
        },
        "research_v3_canonical_document": {"body_sha256": "d" * 64},
        "research_v3_chapter_ledger": {"quality_revision_round": 1},
        "approved_stage_artifact_refs": {
            "draft": {"artifact_id": "research-v3-draft:1", "sha256": current_draft_sha}
        },
        "current_stage_attempt_reservation": {
            "reservation_id": "draft-current", "stage_id": "draft", "status": "approved"
        },
        "stage_attempt_reservations": [
            {"reservation_id": "draft-current", "stage_id": "draft", "status": "approved"},
            {
                "reservation_id": "review-old",
                "stage_id": "review",
                "status": "generating",
                "input_refs": [
                    {"artifact_id": "research-v3-draft:1", "sha256": old_draft_sha}
                ],
            },
        ],
    }

    reservation_id = _completed_quality_revision_review_reservation_id(run)

    assert reservation_id == "review-old"
    released = _stage_reservation_status_patch(run, "failed", reservation_id=reservation_id)
    assert "current_stage_attempt_reservation" not in released
    assert released["stage_attempt_reservations"][1]["status"] == "failed"

    active = {**run, "execution_stream_id": "stream-still-running"}
    assert _completed_quality_revision_review_reservation_id(active) is None
    same_draft = {**run, "approved_stage_artifact_refs": {"draft": {"artifact_id": "research-v3-draft:1", "sha256": old_draft_sha}}}
    assert _completed_quality_revision_review_reservation_id(same_draft) is None


def test_research_v3_start_spec_normalizes_and_routes_without_reclassifying_v2():
    from api.expert_teams.research_contract import (
        RESEARCH_REPORT_V2,
        RESEARCH_REPORT_V3,
        RESEARCH_ROUTE_V2,
        RESEARCH_ROUTE_V3,
        normalize_research_v3_start_spec,
        research_contract_route,
    )

    assert normalize_research_v3_start_spec(
        {"writing_style": " central_enterprise ", "depth": "deep"}
    ) == {"writing_style": "central_enterprise", "depth": "deep"}
    assert research_contract_route(
        {"research_contract_version": RESEARCH_REPORT_V2}
    ) == RESEARCH_ROUTE_V2
    assert research_contract_route(
        {"research_contract_version": RESEARCH_REPORT_V3}
    ) == RESEARCH_ROUTE_V3
    assert research_contract_route(
        {"research_contract_version": "research-report/v4"}
    ) is None


def test_research_v3_run_predicate_requires_matching_frozen_profile_and_confirmed_brief():
    from api.expert_teams.research_contract import is_research_v3_run

    run = {
        "schema_version": 3,
        "launch_profile_id": "research-report",
        "team_id": "deep-research-team",
        "product_mode": "standalone",
        "launch_profile_snapshot": {
            "id": "research-report",
            "team_id": "deep-research-team",
            "document_type": "research_report",
            "research_contract_version": "research-report/v3",
        },
        "document_brief": {
            "status": "confirmed",
            "product_mode": "standalone",
            "document_type": "research_report",
        },
    }

    assert is_research_v3_run(run) is True
    run["launch_profile_id"] = "research-report-other"
    assert is_research_v3_run(run) is False
    run["launch_profile_id"] = "research-report"
    run["schema_version"] = 2
    assert is_research_v3_run(run) is False
    run["schema_version"] = 3
    run["launch_profile_snapshot"]["research_contract_version"] = "research-report/v4"
    assert is_research_v3_run(run) is False
    run["launch_profile_snapshot"]["research_contract_version"] = "research-report/v3"
    run["document_brief"]["status"] = "draft"
    assert is_research_v3_run(run) is False


def test_research_v3_formal_writing_contract_preserves_report_budget_and_core_sections():
    from api.expert_teams.research_contract import formal_report_writing_contract

    contract = formal_report_writing_contract(
        writing_style="central_enterprise",
        depth="standard",
    )

    assert contract["target_result_grade"] == "formal_research_report"
    assert contract["writing_style"]["label"] == "央国企"
    assert contract["word_budget"] == {
        "minimum": 8000,
        "target": 10000,
        "maximum": 12000,
        "chapter_unit_minimum": 1200,
        "chapter_unit_maximum": 2000,
    }
    assert contract["outline_budget"]["priority"] == [
        "主要问题及原因",
        "分析研判",
        "对策建议",
    ]
    assert contract["sections"]["required_core"] == [
        "调研背景与目的",
        "基本情况",
        "主要问题及原因",
        "分析研判",
        "对策建议",
    ]
    assert contract["sections"]["conditional"] == {
        "实践借鉴": "仅在存在可信案例时作为分析研判的子节纳入",
        "方案比较与适用条件": "按题目作为分析研判的子节纳入",
    }
    assert "source_id" in contract["body_forbidden_tokens"]
    assert contract["evidence_trace_policy"]["user_background_origin"] == "user_background"


def test_research_v3_government_deep_and_insufficient_evidence_are_explicitly_graded():
    from api.expert_teams.research_contract import (
        determine_research_result_grade,
        formal_report_writing_contract,
    )

    contract = formal_report_writing_contract(
        writing_style="government",
        depth="deep",
    )
    assert contract["word_budget"]["target"] == 17500
    assert contract["word_budget"]["minimum"] == 15000
    assert contract["word_budget"]["maximum"] == 20000
    assert contract["writing_style"]["id"] == "government"
    assert contract["execution_policy"] == "bounded_report_v1"
    assert contract["automatic_content_revision_limit"] == 0
    assert contract["length_supplement_limit"] == 1
    assert determine_research_result_grade(
        evidence_status="insufficient_after_authorized_retrieval",
        factual_blockers=[],
    ) == "preliminary_research_draft"
    assert determine_research_result_grade(
        evidence_status="sufficient",
        word_count_passed=True,
        structure_quality_passed=True,
        human_or_model_review_passed=True,
        factual_blockers=[],
    ) == "formal_research_report"
    assert determine_research_result_grade(
        evidence_status="sufficient",
        word_count_passed=False,
        structure_quality_passed=True,
        human_or_model_review_passed=True,
        factual_blockers=[],
    ) == "quality_review_required"
    assert determine_research_result_grade(
        evidence_status="insufficient_after_authorized_retrieval",
        factual_blockers=["citation_mismatch"],
    ) == "blocked"
    assert determine_research_result_grade(
        evidence_status="sufficient",
        word_count_passed=True,
        structure_quality_passed=True,
        human_or_model_review_passed=True,
        factual_blockers=None,
    ) == "quality_review_required"
    assert determine_research_result_grade(
        evidence_status="sufficient",
        word_count_passed=1,
        structure_quality_passed=True,
        human_or_model_review_passed=True,
        factual_blockers=[],
    ) == "quality_review_required"


@pytest.mark.parametrize(
    "factual_blockers",
    [False, 0, "", {}, [" "], [1], [{"code": "citation_mismatch"}]],
)
def test_research_v3_result_grade_does_not_treat_invalid_blocker_inputs_as_clear(
    factual_blockers,
):
    from api.expert_teams.research_contract import determine_research_result_grade

    assert determine_research_result_grade(
        evidence_status="sufficient",
        word_count_passed=True,
        structure_quality_passed=True,
        human_or_model_review_passed=True,
        factual_blockers=factual_blockers,
    ) == "quality_review_required"


def test_research_v3_start_fields_are_normalized_and_active_profile_freezes_them():
    from api import expert_teams

    payload = {
        "session_id": "research-v3-contract-session",
        "launch_profile_id": "research-report",
        "prompt": "研究企业人工智能办公应用的推进策略",
        "idempotency_key": "research-v3-contract-launch",
        "writing_style": " government ",
        "depth": "standard",
    }
    validated = expert_teams.validate_standalone_start_request(payload)
    assert validated["writing_style"] == "government"
    assert validated["depth"] == "standard"

    run = expert_teams.build_standalone_expert_team_run(
        payload,
        run_id="et-research-v3-contract",
    )
    assert run["research_writing_contract"]["writing_style"]["id"] == "government"
    assert run["research_writing_contract"]["depth"] == "standard"


def test_research_v3_start_keeps_source_material_out_of_title_and_core_question(monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.launch_profiles import build_research_v3_candidate_profile

    monkeypatch.setattr(runtime, "_now", lambda: "2026-09-07T08:30:00+08:00")
    prompt = (
        "【虚构验收测试资料】\n"
        "请根据下方完整粘贴的资料，研究“澄岳能源集团综合管理部人工智能辅助办公实施路径”。\n"
        "## E01｜现状记录\n[E01-P01] 这是必须保留的用户原始资料。"
    )
    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "research-v3-topic-source-separation",
            "launch_profile_id": "research-report",
            "prompt": prompt,
            "idempotency_key": "research-v3-topic-source-separation",
            "writing_style": "central_enterprise",
            "depth": "standard",
        },
        run_id="et-research-v3-topic-source-separation",
        launch_profile_snapshot=build_research_v3_candidate_profile(),
    )

    brief = run["document_brief"]
    assert brief["original_request"] == prompt
    assert brief["exact_title"] == "澄岳能源集团综合管理部人工智能辅助办公实施路径调研报告"
    assert brief["details"]["core_question"] == "澄岳能源集团综合管理部人工智能辅助办公实施路径"
    assert run["title"] == brief["exact_title"]
