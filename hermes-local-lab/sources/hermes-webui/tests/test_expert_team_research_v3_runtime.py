import copy

import pytest


def _v3_profile():
    from api.expert_teams.launch_profiles import build_research_v3_candidate_profile

    return build_research_v3_candidate_profile()


@pytest.mark.parametrize("depth", ["standard", "deep"])
@pytest.mark.parametrize("stage_id", ["direction", "research", "evidence", "outline", "draft", "review"])
def test_v3_provider_messages_receive_frozen_writing_contract_and_unit_budget(monkeypatch, tmp_path, depth, stage_id):
    import json
    from api import expert_teams
    from api.expert_teams import prompts, runtime
    from api.expert_teams.research_contract import formal_report_writing_contract
    from api.expert_teams.research_chapters import build_research_chapter_plan, create_chapter_ledger
    from api.expert_teams.research_v3_runtime import outline_rows_from_run

    run = _draft_run(expert_teams, runtime, tmp_path)
    contract = formal_report_writing_contract(writing_style="government", depth=depth)
    run["research_writing_contract"] = copy.deepcopy(contract)
    plan = build_research_chapter_plan(contract, outline_rows_from_run(run))
    run["research_v3_chapter_ledger"] = create_chapter_ledger({
        "stage_id": "draft", "stage_attempt": 1, "reservation_id": "prompt-budget",
        "input_binding_sha256": "a" * 64,
    }, plan)
    unit = run["research_v3_chapter_ledger"]["units"][0]
    run["research_v3_active_unit"] = {
        "unit_id": unit["unit_id"], "chapter_id": unit["chapter_id"],
        "unit_attempt": 1, "unit_input_sha256": unit["unit_input_sha256"],
        "execution_start_id": "prompt-start",
    }
    run["research_v3_active_review_unit"] = {
        **run["research_v3_active_unit"], "unit_id": unit["chapter_id"] + ":R01",
    }
    run["research_v3_review_material"] = {"canonical": {}, "sidecar": {}}
    # Dependency validation is covered separately; inspect the actual serialized
    # Provider messages, not an internal projection helper.
    monkeypatch.setattr(prompts, "approved_inputs_for_stage", lambda *_: [])
    stage = next(item for item in run["_tasks_template"] if item["id"] == stage_id)
    request = prompts.build_stage_gateway_request(
        run, stage,
        source_context=expert_teams.verified_source_context_for_execution(tmp_path, run),
    )
    envelope = json.loads(request["messages"][1]["content"])
    assert envelope.get("writing_contract") == contract
    if stage_id == "draft":
        assert envelope["research_v3_unit"]["budget"] == unit["budget"]
        assert str(unit["budget"]) in request["messages"][0]["content"]


def test_v3_pasted_background_evidence_reaches_approved_artifact(monkeypatch, tmp_path):
    import hashlib
    import json
    from api import expert_teams
    from api.expert_teams import runtime
    from tests.test_expert_team_research_auto_advance import _research_stage_run

    run = _research_stage_run(expert_teams, runtime, tmp_path, "evidence", launch_profile_snapshot=_v3_profile())
    source_ref = run["source_context_snapshot_ref"]
    refs = [{"ref_type": "source_context", "snapshot_id": source_ref["snapshot_id"], "sha256": source_ref["sha256"]}]
    run["current_stage_attempt_reservation"]["input_refs"] = refs
    run["stage_attempt_reservations"][-1]["input_refs"] = copy.deepcopy(refs)
    _memory_storage(monkeypatch, runtime, run)
    parsed = {
        "artifact_type": "evidence_matrix", "summary": "用户材料证据",
        "blocking_issues": [],
        "payload": {"claims": [{
            "claim_id": "C01", "statement": "本次评估关注企业本地优先助理的边界。",
            "claim_type": "analysis", "status": "unverified", "confidence": "low", "notes": "仅来自用户材料",
            "origin_tier": "user_background",
            "evidence": [{"source_id": "brief-confirmed", "segment_id": "brief-confirmed:INPUT", "relationship": "context"}],
        }], "contradictions": [], "gaps": []},
    }
    completed = runtime._complete_enterprise_stage_artifact(
        tmp_path, run,
        {"content": "<<<TAIJI_META_V1>>>\n" + json.dumps(parsed, ensure_ascii=False) + "\n<<<TAIJI_META_END>>>"},
        task_id="evidence",
    )
    assert completed["workflow_state"] == "ready_to_generate", completed["stage_outputs"][-1]
    evidence = completed["stage_outputs"][-1]["artifact"]["payload"]["claims"][0]["evidence"][0]
    brief = completed["document_brief"]
    assert evidence["segment_sha256"] == hashlib.sha256(brief["original_request"].encode()).hexdigest()
    assert evidence["locator"] == f"brief:confirmed:{brief['confirmed_revision']}:{brief['confirmed_sha256']}"
    assert evidence["relationship"] == "context"


def _memory_storage(monkeypatch, runtime, initial):
    cell = {"run": copy.deepcopy(initial)}
    monkeypatch.setattr(
        runtime,
        "read_run",
        lambda _workspace, _run_id: copy.deepcopy(cell["run"]),
    )

    def write_run(_workspace, value):
        cell["run"] = copy.deepcopy(value)
        return copy.deepcopy(value)

    monkeypatch.setattr(runtime, "write_run", write_run)
    return cell


def _draft_run(expert_teams, runtime, workspace):
    from api.expert_teams.stage_artifacts import artifact_digest

    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": "research-v3-draft-runtime",
            "prompt": "研究人工智能辅助办公的治理与推广条件",
            "idempotency_key": "research-v3-draft-runtime-start",
        },
        run_id="research-v3-draft-runtime",
        launch_profile_snapshot=_v3_profile(),
    )
    run = expert_teams.bind_initial_standalone_source_context(workspace, run)
    draft_index = next(
        index
        for index, stage in enumerate(run["_tasks_template"])
        if stage["id"] == "draft"
    )
    run["current_stage_index"] = draft_index
    sections = [
        ("background", "调研背景与目的", "background"),
        ("current", "基本情况", "analysis"),
        ("problem", "主要问题及原因", "analysis"),
        ("options", "方案比较与适用条件", "comparison"),
        ("judgment", "分析研判", "analysis"),
        ("advice", "对策建议", "recommendation"),
        ("delivery", "推进保障与需研究事项", "other"),
    ]
    evidence = {
        "artifact_id": "evidence:1",
        "stage_id": "evidence",
        "artifact_type": "evidence_matrix",
        "stage_attempt": 1,
        "input_refs": [],
        "payload": {"claims": []},
        "blocking_issues": [],
        "validation_status": "valid",
    }
    evidence["sha256"] = artifact_digest(evidence)
    outline = {
        "artifact_id": "outline:1",
        "stage_id": "outline",
        "artifact_type": "research_outline",
        "stage_attempt": 1,
        "input_refs": [
            {
                "ref_type": "stage_artifact",
                "artifact_id": evidence["artifact_id"],
                "sha256": evidence["sha256"],
            }
        ],
        "payload": {
            "sections": [
                {
                    "section_id": section_id,
                    "heading": heading,
                    "thesis": "围绕办公决策形成可核查判断",
                    "claim_ids": [],
                    "source_ids": [],
                    "open_questions": [],
                    "chapter_kind": kind,
                }
                for section_id, heading, kind in sections
            ],
            "conclusion_boundaries": ["不将未核验信息写成确定事实"],
        },
        "blocking_issues": [],
        "validation_status": "valid",
    }
    outline["sha256"] = artifact_digest(outline)
    run["stage_artifacts"] = [evidence, outline]
    run["approved_stage_artifact_refs"] = {
        "evidence": {"artifact_id": evidence["artifact_id"], "sha256": evidence["sha256"]},
        "outline": {"artifact_id": outline["artifact_id"], "sha256": outline["sha256"]},
    }
    return runtime._sync_derived(run)


def _complete_draft_to_review(runtime, workspace, initial):
    current = initial
    for index in range(7):
        reserved = runtime.reserve_expert_team_execution_start(
            workspace,
            initial["run_id"],
            expected_version=current["version"],
            runtime_adapter="MockRuntimeAdapter",
            input_refs=[],
        )
        stream_id = f"research-v3-review-prep-stream-{index}"
        started = runtime.mark_expert_team_execution_started(
            workspace,
            initial["run_id"],
            {
                "stream_id": stream_id,
                "runtime_run_id": f"research-v3-review-prep-runtime-{index}",
                "execution_start_id": reserved["execution_start_id"],
                "runtime_adapter": "MockRuntimeAdapter",
            },
        )
        packet = {"body": f"第{index + 1}个章节单元的完整分析。", "claim_usages": []}
        current = runtime.mark_expert_team_execution_complete(
            workspace,
            initial["run_id"],
            {
                "id": f"research-v3-review-prep-delivery-{index}",
                "stream_id": stream_id,
                "stage_id": "draft",
                "attempt": started["execution_attempt"],
                "content": "<<<TAIJI_RESEARCH_V3_UNIT>>>\n"
                + __import__("json").dumps(packet, ensure_ascii=False)
                + "\n<<<TAIJI_RESEARCH_V3_UNIT_END>>>",
            },
        )
    assert current["current_stage"]["task_id"] == "review"
    return current


def _review_delivery(
    run,
    delivery_id,
    *,
    verdict="supported",
    rationale="已审查该章节的论证范围与已知限制。",
    wrapped=True,
):
    material = run["research_v3_review_material"]
    active = run["research_v3_active_review_unit"]
    packet = {
        "reviewer_assessed": True,
        "canonical_body_sha256": material["canonical"]["body_sha256"],
        "sidecar_sha256": material["sidecar"]["sidecar_sha256"],
        "chapter_id": active["chapter_id"],
        "usage_ids": [],
        "reviewed_source_refs": [],
        "findings": [
            {
                "finding_id": f"review-{active['chapter_id']}",
                "verdict": verdict,
                "rationale": rationale,
                "revision_required": False,
            }
        ],
        "quality_assessment": {
            "formal_style": "passed",
            "analysis_depth": "passed",
            "recommendation_applicability": "not_applicable",
            "issues": [],
        },
    }
    content = __import__("json").dumps(packet, ensure_ascii=False)
    if wrapped:
        content = "<<<TAIJI_RESEARCH_V3_REVIEW>>>\n" + content + "\n<<<TAIJI_RESEARCH_V3_REVIEW_END>>>"
    return {
        "id": delivery_id,
        "stream_id": run["execution_stream_id"],
        "stage_id": "review",
        "attempt": run["execution_attempt"],
        "content": content,
    }


def test_v3_draft_unit_uses_runtime_start_complete_checkpoint_and_replay(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, initial)
    reserved = runtime.reserve_expert_team_execution_start(
        tmp_path,
        initial["run_id"],
        expected_version=initial["version"],
        runtime_adapter="MockRuntimeAdapter",
        input_refs=[],
    )
    parent = reserved["current_stage_attempt_reservation"]
    assert parent["stage_attempt"] == 1
    assert reserved["research_v3_chapter_ledger"]["parent"]["reservation_id"] == parent["reservation_id"]
    unit_id = reserved["research_v3_active_unit"]["unit_id"]

    started = runtime.mark_expert_team_execution_started(
        tmp_path,
        initial["run_id"],
        {
            "stream_id": "research-v3-unit-stream-1",
            "runtime_run_id": "research-v3-unit-runtime-1",
            "execution_start_id": reserved["execution_start_id"],
            "runtime_adapter": "MockRuntimeAdapter",
        },
    )
    running = next(
        unit for unit in started["research_v3_chapter_ledger"]["units"] if unit["unit_id"] == unit_id
    )
    assert running["status"] == "running"
    assert running["unit_attempt"] == 1

    packet = {
        "body": "本节围绕现有办公流程的风险控制与推广条件展开分析。",
        "claim_usages": [],
    }
    completed = runtime.mark_expert_team_execution_complete(
        tmp_path,
        initial["run_id"],
        {
            "id": "research-v3-unit-delivery-1",
            "stream_id": "research-v3-unit-stream-1",
            "stage_id": "draft",
            "attempt": 1,
            "content": "<<<TAIJI_RESEARCH_V3_UNIT>>>\n" + __import__("json").dumps(packet, ensure_ascii=False) + "\n<<<TAIJI_RESEARCH_V3_UNIT_END>>>",
        },
    )
    checkpoint = next(
        unit for unit in completed["research_v3_chapter_ledger"]["units"] if unit["unit_id"] == unit_id
    )
    assert completed["workflow_state"] == "ready_to_generate"
    assert checkpoint["status"] == "completed"
    assert completed["current_stage_attempt_reservation"]["stage_attempt"] == 1
    assert "draft" not in completed.get("approved_stage_artifact_refs", {})

    replayed = runtime.mark_expert_team_execution_complete(
        tmp_path,
        initial["run_id"],
        {
            "id": "research-v3-unit-delivery-1",
            "stream_id": "research-v3-unit-stream-1",
            "stage_id": "draft",
            "attempt": 1,
            "content": "<<<TAIJI_RESEARCH_V3_UNIT>>>\n" + __import__("json").dumps(packet, ensure_ascii=False) + "\n<<<TAIJI_RESEARCH_V3_UNIT_END>>>",
        },
    )
    assert replayed == completed
    assert cell["run"] == completed

    resumed = runtime.resume_expert_team(
        tmp_path,
        {
            "run_id": initial["run_id"],
            "session_id": initial["session_id"],
            "stage_id": "draft",
            "expected_version": completed["version"],
            "idempotency_key": "research-v3-next-unit-resume",
        },
    )
    next_reserved = runtime.reserve_expert_team_execution_start(
        tmp_path,
        initial["run_id"],
        expected_version=resumed["version"],
        runtime_adapter="MockRuntimeAdapter",
        input_refs=[],
    )
    assert next_reserved["current_stage_attempt_reservation"]["stage_attempt"] == 1
    assert next_reserved["research_v3_active_unit"]["unit_id"] != unit_id
    assert next_reserved["research_v3_active_unit"]["unit_attempt"] == 1


def test_v3_draft_unit_accepts_a_strict_bare_json_packet(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    _memory_storage(monkeypatch, runtime, initial)
    reserved = runtime.reserve_expert_team_execution_start(
        tmp_path,
        initial["run_id"],
        expected_version=initial["version"],
        runtime_adapter="MockRuntimeAdapter",
        input_refs=[],
    )
    started = runtime.mark_expert_team_execution_started(
        tmp_path,
        initial["run_id"],
        {
            "stream_id": "research-v3-bare-json-stream",
            "runtime_run_id": "research-v3-bare-json-runtime",
            "execution_start_id": reserved["execution_start_id"],
            "runtime_adapter": "MockRuntimeAdapter",
        },
    )
    packet = {
        "body": "本节围绕现有办公流程的风险控制与推广条件展开分析。",
        "claim_usages": [],
    }
    completed = runtime.mark_expert_team_execution_complete(
        tmp_path,
        initial["run_id"],
        {
            "id": "research-v3-bare-json-delivery",
            "stream_id": "research-v3-bare-json-stream",
            "stage_id": "draft",
            "attempt": started["execution_attempt"],
            "content": __import__("json").dumps(packet, ensure_ascii=False),
        },
    )
    unit = next(
        item for item in completed["research_v3_chapter_ledger"]["units"]
        if item["unit_id"] == started["research_v3_active_unit"]["unit_id"]
    )
    assert completed["workflow_state"] == "ready_to_generate"
    assert unit["status"] == "completed"


def test_v3_draft_provider_failure_marks_only_active_unit_retryable(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    _memory_storage(monkeypatch, runtime, initial)
    reserved = runtime.reserve_expert_team_execution_start(
        tmp_path,
        initial["run_id"],
        expected_version=initial["version"],
        runtime_adapter="MockRuntimeAdapter",
        input_refs=[],
    )
    started = runtime.mark_expert_team_execution_started(
        tmp_path,
        initial["run_id"],
        {
            "stream_id": "research-v3-failure-stream-1",
            "runtime_run_id": "research-v3-failure-runtime-1",
            "execution_start_id": reserved["execution_start_id"],
            "runtime_adapter": "MockRuntimeAdapter",
        },
    )
    unit_id = started["research_v3_active_unit"]["unit_id"]
    recovered = runtime.fail_expert_team_execution(
        tmp_path,
        initial["run_id"],
        "mock provider timeout",
        stream_id="research-v3-failure-stream-1",
        error_code="provider_timeout",
    )
    unit = next(
        item for item in recovered["research_v3_chapter_ledger"]["units"] if item["unit_id"] == unit_id
    )
    assert recovered["workflow_state"] == "ready_to_generate"
    assert recovered["current_stage_attempt_reservation"]["stage_attempt"] == 1
    assert unit["status"] == "retryable"
    assert recovered["research_v3_active_unit"] is None


def test_v3_draft_nonretryable_failure_requires_explicit_resume(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    _memory_storage(monkeypatch, runtime, initial)
    reserved = runtime.reserve_expert_team_execution_start(
        tmp_path,
        initial["run_id"],
        expected_version=initial["version"],
        runtime_adapter="MockRuntimeAdapter",
        input_refs=[],
    )
    started = runtime.mark_expert_team_execution_started(
        tmp_path,
        initial["run_id"],
        {
            "stream_id": "research-v3-auth-failure-stream",
            "runtime_run_id": "research-v3-auth-failure-runtime",
            "execution_start_id": reserved["execution_start_id"],
            "runtime_adapter": "MockRuntimeAdapter",
        },
    )
    unit_id = started["research_v3_active_unit"]["unit_id"]
    blocked = runtime.fail_expert_team_execution(
        tmp_path,
        initial["run_id"],
        "provider authorization failed",
        stream_id="research-v3-auth-failure-stream",
        error_code="provider_authorization_failed",
    )
    unit = next(item for item in blocked["research_v3_chapter_ledger"]["units"] if item["unit_id"] == unit_id)
    assert blocked["workflow_state"] == "generation_failed"
    assert blocked["current_stage_attempt_reservation"]["status"] == "failed"
    assert unit["status"] == "retryable"
    assert blocked["research_v3_active_unit"] is None
    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.reserve_expert_team_execution_start(
            tmp_path,
            initial["run_id"],
            expected_version=blocked["version"],
            runtime_adapter="MockRuntimeAdapter",
            input_refs=[],
        )
    assert error.value.code == "stale_state"
    resumed = runtime.resume_expert_team(
        tmp_path,
        {
            "run_id": initial["run_id"],
            "session_id": initial["session_id"],
            "stage_id": "draft",
            "expected_version": blocked["version"],
            "idempotency_key": "research-v3-auth-failure-resume",
        },
    )
    assert resumed["workflow_state"] == "ready_to_generate"
    assert resumed["research_v3_active_unit"] is None


def test_v3_draft_input_change_invalidates_checkpoint_and_review_bindings(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    _memory_storage(monkeypatch, runtime, initial)
    reserved = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=initial["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
    started = runtime.mark_expert_team_execution_started(tmp_path, initial["run_id"], {"stream_id": "research-v3-input-stream", "runtime_run_id": "research-v3-input-runtime", "execution_start_id": reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"})
    checkpoint = runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], {"id": "research-v3-input-delivery", "stream_id": "research-v3-input-stream", "stage_id": "draft", "attempt": started["execution_attempt"], "content": "<<<TAIJI_RESEARCH_V3_UNIT>>>\n{\"body\": \"已完成章节。\", \"claim_usages\": []}\n<<<TAIJI_RESEARCH_V3_UNIT_END>>>"})
    awaiting = runtime.request_expert_team_stage_input(tmp_path, {"run_id": initial["run_id"], "session_id": initial["session_id"], "stage_id": "draft", "expected_version": checkpoint["version"], "idempotency_key": "research-v3-input-request", "input_id": "research-v3-input", "question": "请确认适用范围", "required": True})
    updated = runtime.submit_expert_team_stage_input(tmp_path, {"run_id": initial["run_id"], "session_id": initial["session_id"], "stage_id": "draft", "expected_version": awaiting["version"], "idempotency_key": "research-v3-input-answer", "input_id": "research-v3-input", "answer": "仅限内部办公场景"})
    assert updated["workflow_state"] == "ready_to_generate"
    assert updated["research_v3_chapter_ledger"] is None
    assert updated["research_v3_canonical_document"] is None
    assert updated["research_v3_independent_review"] is None


def test_v3_review_rejects_heading_comment_and_empty_table_as_chapter_body():
    from api.expert_teams.research_review import _substantive_chapter_body

    assert not _substantive_chapter_body("\n### 仅有小节标题\n<!-- 注释 -->\n| 列一 | 列二 |\n| --- | --- |\n")
    assert _substantive_chapter_body("\n### 分析\n- 已明确适用边界。\n")


@pytest.mark.parametrize("bounded", [True, False])
def test_v3_review_is_a_separate_runtime_packet_bound_to_full_canonical(monkeypatch, tmp_path, bounded):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, initial)
    draft_complete = _complete_draft_to_review(runtime, tmp_path, initial)
    if not bounded:
        draft_complete["research_writing_contract"].pop("execution_policy", None)
        cell["run"] = copy.deepcopy(draft_complete)
    completed = draft_complete
    reviewed_chapters = []
    for index in range(7):
        reserved = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=completed["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
        started = runtime.mark_expert_team_execution_started(tmp_path, initial["run_id"], {"stream_id": f"research-v3-review-stream-{index}", "runtime_run_id": f"research-v3-review-runtime-{index}", "execution_start_id": reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"})
        material = started["research_v3_review_material"]
        active = started["research_v3_active_review_unit"]
        reviewed_chapters.append(active["chapter_id"])
        review = {"reviewer_assessed": True, "canonical_body_sha256": material["canonical"]["body_sha256"], "sidecar_sha256": material["sidecar"]["sidecar_sha256"], "chapter_id": active["chapter_id"], "usage_ids": [], "reviewed_source_refs": [], "findings": [{"finding_id": f"review-{active['chapter_id']}", "verdict": "supported", "rationale": "已审查该章节的论证范围与已知限制。", "revision_required": False}], "quality_assessment": {"formal_style": "passed", "analysis_depth": "passed", "recommendation_applicability": "not_applicable", "issues": []}}
        completed = runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], {"id": f"research-v3-review-delivery-{index}", "stream_id": f"research-v3-review-stream-{index}", "stage_id": "review", "attempt": started["execution_attempt"], "content": "<<<TAIJI_RESEARCH_V3_REVIEW>>>\n" + __import__("json").dumps(review, ensure_ascii=False) + "\n<<<TAIJI_RESEARCH_V3_REVIEW_END>>>"})
    assert reviewed_chapters == completed["research_v3_canonical_document"]["chapter_ids"]
    assert completed["workflow_state"] == ("delivery_validation_required" if bounded else "generated_invalid")
    assert completed["research_v3_independent_review"]["review_status"] == "reviewer_assessed_passed"
    assert completed["research_v3_mechanical_binding"]["semantic_equivalence_assessed"] is False
    assert completed["research_v3_chapter_ledger"]["review_binding"]["status"] == "current"
    assert completed["research_v3_result_grade"] == "quality_review_required"


def test_v3_review_accepts_strict_bare_json_and_releases_malformed_packet(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, initial)
    reviewed = _complete_draft_to_review(runtime, tmp_path, initial)
    reserved = runtime.reserve_expert_team_execution_start(
        tmp_path, initial["run_id"], expected_version=reviewed["version"],
        runtime_adapter="MockRuntimeAdapter", input_refs=[],
    )
    started = runtime.mark_expert_team_execution_started(
        tmp_path, initial["run_id"],
        {
            "stream_id": "research-v3-bare-review-stream",
            "runtime_run_id": "research-v3-bare-review-runtime",
            "execution_start_id": reserved["execution_start_id"],
            "runtime_adapter": "MockRuntimeAdapter",
        },
    )
    accepted = runtime.mark_expert_team_execution_complete(
        tmp_path,
        initial["run_id"],
        _review_delivery(started, "research-v3-bare-review-delivery", wrapped=False),
    )
    unit_id = started["research_v3_active_review_unit"]["unit_id"]
    accepted_unit = next(
        item for item in accepted["research_v3_review_ledger"]["units"]
        if item["unit_id"] == unit_id
    )
    assert accepted_unit["status"] == "completed"

    retry_initial = _draft_run(expert_teams, runtime, tmp_path)
    cell["run"] = retry_initial
    retry_review = _complete_draft_to_review(runtime, tmp_path, retry_initial)
    retry_reserved = runtime.reserve_expert_team_execution_start(
        tmp_path, retry_initial["run_id"], expected_version=retry_review["version"],
        runtime_adapter="MockRuntimeAdapter", input_refs=[],
    )
    retry_started = runtime.mark_expert_team_execution_started(
        tmp_path, retry_initial["run_id"],
        {
            "stream_id": "research-v3-bad-review-stream",
            "runtime_run_id": "research-v3-bad-review-runtime",
            "execution_start_id": retry_reserved["execution_start_id"],
            "runtime_adapter": "MockRuntimeAdapter",
        },
    )
    bad = _review_delivery(retry_started, "research-v3-bad-review-delivery", wrapped=False)
    packet = __import__("json").loads(bad["content"])
    packet["reviewed_source_refs"] = ["ATT-not-an-object"]
    bad["content"] = __import__("json").dumps(packet, ensure_ascii=False)
    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.mark_expert_team_execution_complete(tmp_path, retry_initial["run_id"], bad)
    assert error.value.code == "review_protocol_invalid"
    failed = runtime.fail_expert_team_execution(
        tmp_path,
        retry_initial["run_id"],
        "独立审核结果格式或冻结引用无效，请重新审核当前章节。",
        stream_id=retry_started["execution_stream_id"],
        error_code=error.value.code,
    )
    retry_unit = next(
        item for item in failed["research_v3_review_ledger"]["units"]
        if item["unit_id"] == retry_started["research_v3_active_review_unit"]["unit_id"]
    )
    assert failed["workflow_state"] == "generation_failed"
    assert failed["research_v3_active_review_unit"] is None
    assert retry_unit["status"] == "retryable"
    resumed = runtime.resume_expert_team(
        tmp_path,
        {
            "run_id": retry_initial["run_id"],
            "session_id": retry_initial["session_id"],
            "stage_id": "review",
            "expected_version": failed["version"],
            "idempotency_key": "research-v3-bad-review-resume",
        },
    )
    assert resumed["workflow_state"] == "ready_to_generate"


def test_v3_review_prompt_uses_frozen_sidecar_usage_rows_and_source_objects():
    from api.expert_teams.prompts import _research_v3_unit_system_message

    usage_id = "S01:U01:C01-001"
    source_id = "ATT-82966b60b0a236858c5002ab"
    segment_id = source_id + ":SEG-0001"
    source_hash = "a" * 64
    message = _research_v3_unit_system_message(
        "review",
        {"chapter_id": "S01", "unit_id": "S01:R01"},
        {
            "canonical": {"body_sha256": "b" * 64},
            "sidecar": {
                "sidecar_sha256": "c" * 64,
                "usages": [
                    {"chapter_id": "S01", "usage_id": usage_id},
                    {"chapter_id": "S02", "usage_id": "S02:U01:C02-001"},
                ],
                "review_source_excerpts": [
                    {
                        "source_id": source_id,
                        "segment_id": segment_id,
                        "text_sha256": source_hash,
                    }
                ],
            },
        },
    )
    assert usage_id in message
    assert "S02:U01:C02-001" not in message
    assert source_id in message and segment_id in message and source_hash in message


def test_v3_review_parser_accepts_only_the_known_truncated_opening_marker():
    from api.expert_teams.research_v3_runtime import (
        ResearchV3RuntimeError,
        parse_independent_review_packet,
    )

    payload = '{"reviewer_assessed":true}'
    accepted = (
        "<<<TAIJI_RESEARCH_V3_REVIEW>>\n"
        + payload
        + "\n<<<TAIJI_RESEARCH_V3_REVIEW_END>>>"
    )
    assert parse_independent_review_packet(accepted) == {"reviewer_assessed": True}
    with pytest.raises(ResearchV3RuntimeError):
        parse_independent_review_packet(
            "note " + accepted
        )


def test_v3_review_quality_concern_is_retained_only_with_a_concrete_issue():
    from api.expert_teams.research_review import ReviewLedgerError, _packet

    packet = {
        "reviewer_assessed": True,
        "canonical_body_sha256": "b" * 64,
        "sidecar_sha256": "c" * 64,
        "chapter_id": "S03",
        "usage_ids": [],
        "reviewed_source_refs": [],
        "findings": [{
            "finding_id": "S03-R01-F01",
            "verdict": "supported",
            "rationale": "事实引用准确，但存在结构性冗余。",
            "revision_required": False,
        }],
        "quality_assessment": {
            "formal_style": "concern",
            "analysis_depth": "passed",
            "recommendation_applicability": "passed",
            "issues": ["同一分析在两个小节中重复呈现。"],
        },
    }
    accepted = _packet(
        __import__("json").dumps(packet, ensure_ascii=False),
        {"body_sha256": "b" * 64},
        {"sha256": "c" * 64, "usage_ids": {"S03": []}},
        "S03",
    )
    assert accepted["quality_assessment"]["formal_style"] == "concern"
    packet["quality_assessment"]["issues"] = []
    with pytest.raises(ReviewLedgerError) as error:
        _packet(
            __import__("json").dumps(packet, ensure_ascii=False),
            {"body_sha256": "b" * 64},
            {"sha256": "c" * 64, "usage_ids": {"S03": []}},
            "S03",
        )
    assert error.value.code == "review_protocol_invalid"


def test_v3_last_draft_unit_assembles_full_canonical_and_advances_once(monkeypatch, tmp_path):
    """The last unit must promote the ordered checkpoint, never its own text."""
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    _memory_storage(monkeypatch, runtime, initial)
    current = initial
    completed_unit_ids = []
    final_delivery = None
    for index in range(7):
        reserved = runtime.reserve_expert_team_execution_start(
            tmp_path,
            initial["run_id"],
            expected_version=current["version"],
            runtime_adapter="MockRuntimeAdapter",
            input_refs=[],
        )
        unit_id = reserved["research_v3_active_unit"]["unit_id"]
        completed_unit_ids.append(unit_id)
        stream_id = f"research-v3-final-stream-{index}"
        started = runtime.mark_expert_team_execution_started(
            tmp_path,
            initial["run_id"],
            {
                "stream_id": stream_id,
                "runtime_run_id": f"research-v3-final-runtime-{index}",
                "execution_start_id": reserved["execution_start_id"],
                "runtime_adapter": "MockRuntimeAdapter",
            },
        )
        packet = {"body": f"第{index + 1}个章节单元的完整分析。", "claim_usages": []}
        delivery = {
            "id": f"research-v3-final-delivery-{index}",
            "stream_id": stream_id,
            "stage_id": "draft",
            "attempt": started["execution_attempt"],
            "content": "<<<TAIJI_RESEARCH_V3_UNIT>>>\n"
            + __import__("json").dumps(packet, ensure_ascii=False)
            + "\n<<<TAIJI_RESEARCH_V3_UNIT_END>>>",
        }
        current = runtime.mark_expert_team_execution_complete(
            tmp_path,
            initial["run_id"],
            delivery,
        )
        if index == 6:
            final_delivery = delivery
        if index < 6:
            assert current["workflow_state"] == "ready_to_generate"

    assert len(completed_unit_ids) == len(set(completed_unit_ids)) == 7
    assert current["current_stage"]["task_id"] == "review"
    canonical = current["research_v3_canonical_document"]
    assert canonical["chapter_ids"] == [
            "background", "current", "problem", "options", "judgment", "advice", "delivery"
    ]
    assert canonical["body"].count("## ") == 7
    assert "第7个章节单元的完整分析。" in canonical["body"]
    assert "第1个章节单元的完整分析。" in canonical["body"]
    assert current["research_v3_body_count"]["body_boundary_mode"] == "explicit_body_scope"
    assert current["research_v3_body_count"]["canonical_body_sha256"] == canonical["body_sha256"]
    assert current["research_v3_result_grade_before_review"] == "quality_review_required"
    assert current["research_v3_checkpoint_progress"] == {
        "completed_units": 7,
        "total_units": 7,
        "last_unit_id": completed_unit_ids[-1],
        "last_chapter_id": "delivery",
    }
    from api.expert_teams.view import expert_team_run_view

    stale_checkpoint = copy.deepcopy(current)
    stale_checkpoint["research_v3_checkpoint_progress"]["completed_units"] = 6
    assert expert_team_run_view(stale_checkpoint)["research_v3"].get("completed_units") == 7
    assert current["approved_stage_artifact_refs"]["draft"]["artifact_id"] == "research-v3-draft:1"
    from api.expert_teams import prompts

    monkeypatch.setattr(
        prompts,
        "get_template",
        lambda _team_id: {"tasks": [{"id": "review", "depends_on": ["draft"]}]},
    )
    review_inputs = prompts.approved_inputs_for_stage(current, "review")
    assert review_inputs == [current["stage_outputs"][-1]["artifact"]]
    assert current["stage_outputs"][-1]["status"] == "approved"
    assert runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], final_delivery) == current
    conflicting = copy.deepcopy(final_delivery)
    conflicting["content"] = conflicting["content"].replace("第7个", "篡改的第七个")
    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], conflicting)
    assert error.value.code == "research_v3_unit_receipt_conflict"


def test_v3_candidate_start_freezes_default_writing_contract_and_confirmed_brief():
    from api import expert_teams

    profile = _v3_profile()
    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": "research-v3-freeze",
            "prompt": "请根据附件研究人工智能辅助办公的治理与推广条件",
            "idempotency_key": "research-v3-freeze-start",
        },
        run_id="research-v3-freeze",
        launch_profile_snapshot=profile,
    )

    assert run["launch_profile_snapshot"]["research_contract_version"] == "research-report/v3"
    assert run["research_writing_contract"]["writing_style"]["id"] == "central_enterprise"
    assert run["research_writing_contract"]["depth"] == "standard"
    assert run["research_writing_contract"]["word_budget"]["target"] == 10000
    assert run["document_brief"]["status"] == "confirmed"
    assert run["document_brief"]["exact_title"] == "人工智能辅助办公的治理与推广条件调研报告"
    assert run["document_brief"]["audience"] == "央国企内部专题调研与决策参考"
    assert "请根据附件" not in run["document_brief"]["exact_title"]
    assert run["workflow_state"] == "ready_to_generate"
    frozen = copy.deepcopy(run["research_writing_contract"])
    profile["research_contract_version"] = "research-report/v2"
    assert run["research_writing_contract"] == frozen


def test_v3_candidate_start_freezes_government_deep_and_rejects_unknown_snapshot_version():
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    profile = _v3_profile()
    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": "research-v3-government-deep",
            "prompt": "研究政务智能办公的实施条件",
            "idempotency_key": "research-v3-government-deep-start",
            "writing_style": "government",
            "depth": "deep",
        },
        run_id="research-v3-government-deep",
        launch_profile_snapshot=profile,
    )
    assert run["research_writing_contract"]["depth"] == "deep"
    assert run["research_writing_contract"]["word_budget"]["target"] == 17500
    assert run["document_brief"]["audience"] == "政府内部专题调研与决策参考"

    unknown = copy.deepcopy(profile)
    unknown["research_contract_version"] = "research-report/v999"
    with pytest.raises(ContractError) as error:
        expert_teams.build_standalone_expert_team_run(
            {
                "launch_profile_id": "research-report",
                "session_id": "research-v3-unknown-version",
                "prompt": "研究任务",
                "idempotency_key": "research-v3-unknown-version-start",
            },
            run_id="research-v3-unknown-version",
            launch_profile_snapshot=unknown,
        )
    assert error.value.code == "research_contract_version_unsupported"


@pytest.mark.parametrize("review,started", [(False, False), (True, False), (True, True)])
def test_v3_failed_reserved_or_streamed_unit_is_released_for_exact_retry(monkeypatch, tmp_path, review, started):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    _memory_storage(monkeypatch, runtime, initial)
    current = _complete_draft_to_review(runtime, tmp_path, initial) if review else initial
    reserved = runtime.reserve_expert_team_execution_start(
        tmp_path, initial["run_id"], expected_version=current["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[]
    )
    ledger_key = "research_v3_review_ledger" if review else "research_v3_chapter_ledger"
    active_key = "research_v3_active_review_unit" if review else "research_v3_active_unit"
    active = reserved[active_key]
    if started:
        current = runtime.mark_expert_team_execution_started(
            tmp_path,
            initial["run_id"],
            {"stream_id": "retry-stream", "runtime_run_id": "retry-runtime", "execution_start_id": reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"},
        )
        failed = runtime.fail_expert_team_execution(
            tmp_path, initial["run_id"], "mock provider timeout", stream_id="retry-stream", error_code="provider_timeout"
        )
    else:
        failed = runtime.mark_expert_team_execution_start_failed(
            tmp_path, initial["run_id"], "mock provider unavailable", execution_start_id=reserved["execution_start_id"], error_code="provider_timeout"
        )
    unit = next(item for item in failed[ledger_key]["units"] if item["unit_id"] == active["unit_id"])
    assert unit["status"] == "retryable"
    assert failed[active_key] is None
    resumed = runtime.resume_expert_team(
        tmp_path,
        {"run_id": initial["run_id"], "session_id": initial["session_id"], "stage_id": failed["current_stage"]["task_id"], "expected_version": failed["version"], "idempotency_key": f"retry-{review}-{started}"},
    )
    retried = runtime.reserve_expert_team_execution_start(
        tmp_path, initial["run_id"], expected_version=resumed["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[]
    )
    assert retried[active_key]["unit_id"] == active["unit_id"]
    assert retried[active_key]["unit_attempt"] == 2
    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.mark_expert_team_execution_start_failed(
            tmp_path, initial["run_id"], "late old failure", execution_start_id=reserved["execution_start_id"], error_code="provider_timeout"
        )
    assert error.value.code == "stale_start"


def test_v3_review_input_change_rewinds_to_draft_and_rejects_old_receipts(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    _memory_storage(monkeypatch, runtime, initial)
    current = _complete_draft_to_review(runtime, tmp_path, initial)
    reserved = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=current["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
    started = runtime.mark_expert_team_execution_started(tmp_path, initial["run_id"], {"stream_id": "input-review-stream", "runtime_run_id": "input-review-runtime", "execution_start_id": reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"})
    reviewed = runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], _review_delivery(started, "input-review-delivery"))
    old_draft_delivery = {
        "id": "research-v3-review-prep-delivery-0", "stream_id": "research-v3-review-prep-stream-0", "stage_id": "draft", "attempt": 1,
        "content": "<<<TAIJI_RESEARCH_V3_UNIT>>>\n{\"body\": \"第1个章节单元的完整分析。\", \"claim_usages\": []}\n<<<TAIJI_RESEARCH_V3_UNIT_END>>>",
    }
    awaiting = runtime.request_expert_team_stage_input(tmp_path, {"run_id": initial["run_id"], "session_id": initial["session_id"], "stage_id": "review", "expected_version": reviewed["version"], "idempotency_key": "review-scope-request", "input_id": "review-scope", "question": "确认研究范围", "required": True})
    updated = runtime.submit_expert_team_stage_input(tmp_path, {"run_id": initial["run_id"], "session_id": initial["session_id"], "stage_id": "review", "expected_version": awaiting["version"], "idempotency_key": "review-scope-answer", "input_id": "review-scope", "answer": "仅适用政府政务场景"})
    assert updated["current_stage"]["task_id"] == "draft"
    for key in ("research_v3_canonical_document", "research_v3_chapter_ledger", "research_v3_review_ledger", "research_v3_provenance_sidecar", "research_v3_independent_review", "research_v3_result_grade"):
        assert updated.get(key) is None
    assert "draft" not in updated.get("approved_stage_artifact_refs", {})
    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], old_draft_delivery)
    assert error.value.code == "missing_stream"
    rebuilt = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=updated["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
    assert rebuilt["current_stage"]["task_id"] == "draft"
    assert rebuilt["research_v3_canonical_document"] is None


def test_v3_review_receipt_replay_is_immutable_across_progress_and_replacement(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, initial)
    current = _complete_draft_to_review(runtime, tmp_path, initial)
    deliveries = []
    for index in range(7):
        reserved = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=current["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
        started = runtime.mark_expert_team_execution_started(tmp_path, initial["run_id"], {"stream_id": f"replay-stream-{index}", "runtime_run_id": f"replay-runtime-{index}", "execution_start_id": reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"})
        delivery = _review_delivery(started, f"replay-delivery-{index}")
        current = runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], delivery)
        deliveries.append(delivery)
        if index == 0:
            assert runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], delivery) == current
            assert cell["run"] == current
    assert runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], deliveries[-1]) == current
    altered = copy.deepcopy(deliveries[-1])
    altered["content"] = altered["content"].replace("已审查", "篡改审查")
    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], altered)
    assert error.value.code == "research_v3_review_receipt_conflict"
    for field, value in (("stage_id", "draft"), ("attempt", 999)):
        altered = copy.deepcopy(deliveries[-1])
        altered[field] = value
        with pytest.raises(runtime.ExpertTeamStateConflict) as error:
            runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], altered)
        assert error.value.code == "research_v3_review_receipt_conflict"


def test_v3_delayed_review_receipt_does_not_replace_a_new_running_unit(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, initial)
    current = _complete_draft_to_review(runtime, tmp_path, initial)
    first_reserved = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=current["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
    first_started = runtime.mark_expert_team_execution_started(tmp_path, initial["run_id"], {"stream_id": "delayed-first-stream", "runtime_run_id": "delayed-first-runtime", "execution_start_id": first_reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"})
    first_delivery = _review_delivery(first_started, "delayed-first-delivery")
    checkpointed = runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], first_delivery)
    second_reserved = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=checkpointed["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
    second_started = runtime.mark_expert_team_execution_started(tmp_path, initial["run_id"], {"stream_id": "delayed-second-stream", "runtime_run_id": "delayed-second-runtime", "execution_start_id": second_reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"})
    assert runtime.mark_expert_team_execution_complete(tmp_path, initial["run_id"], first_delivery) == second_started
    assert cell["run"] == second_started
    assert second_started["research_v3_active_review_unit"]["unit_id"] != first_started["research_v3_active_review_unit"]["unit_id"]


def test_v3_blocked_review_finding_beats_insufficient_evidence_downgrade(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    initial = _draft_run(expert_teams, runtime, tmp_path)
    cell = _memory_storage(monkeypatch, runtime, initial)
    current = _complete_draft_to_review(runtime, tmp_path, initial)
    current["research_v3_evidence_status"] = "insufficient_after_authorized_retrieval"
    cell["run"] = copy.deepcopy(current)
    for index in range(7):
        reserved = runtime.reserve_expert_team_execution_start(tmp_path, initial["run_id"], expected_version=current["version"], runtime_adapter="MockRuntimeAdapter", input_refs=[])
        started = runtime.mark_expert_team_execution_started(tmp_path, initial["run_id"], {"stream_id": f"blocked-stream-{index}", "runtime_run_id": f"blocked-runtime-{index}", "execution_start_id": reserved["execution_start_id"], "runtime_adapter": "MockRuntimeAdapter"})
        current = runtime.mark_expert_team_execution_complete(
            tmp_path,
            initial["run_id"],
            _review_delivery(started, f"blocked-delivery-{index}", verdict="blocked" if index == 0 else "supported", rationale="正文含虚构政策引用，事实阻断。" if index == 0 else "已审查该章节的论证范围与已知限制。"),
        )
    assert current["research_v3_independent_review"]["findings"][0]["verdict"] == "blocked"
    assert current["research_v3_result_grade"] == "blocked"
    from api.expert_teams.documents import prepare_canonical_delivery_inputs

    prepared = prepare_canonical_delivery_inputs(
        tmp_path, current, stage_id="delivery", delivery_attempt=1,
    )
    assert prepared["semantic_gates"]["status"] == "passed", prepared["semantic_gates"]["issues"]
    assert prepared["semantic_gates"]["research_result_grade"] == "blocked"
    markdown = prepared["artifact"]["deliverable_markdown"]
    assert "工作稿" in prepared["semantic_gates"]["research_working_draft"]["label"]
    assert "正文含虚构政策引用，事实阻断。" in prepared["semantic_gates"]["research_working_draft"]["notes"]
    assert current["research_v3_canonical_document"]["body"] in markdown
    assert prepared["artifact"]["payload"]["review_report"]["checks"]["unsupported_claims"] == "failed"
    assert any(not item.get("completion_blocking", True) for item in prepared["semantic_gates"]["issues"])
    from api.expert_teams.documents import build_render_input_binding
    binding = build_render_input_binding(
        brief=prepared["brief"], artifact=prepared["artifact"],
        canonical_document_path=prepared["paths"]["document"],
        asset_manifest_path=prepared["paths"]["asset_manifest"],
        semantic_gates_path=prepared["paths"]["semantic_gates"],
        template={"id": "standalone-research-report", "version": "1", "package_sha256": "a" * 64},
        renderer={"name": "test", "version": "1", "build_sha256": "a" * 64, "profile_id": "standalone-default", "profile_sha256": "b" * 64},
    )
    assert binding["researchWorkingDraft"] == prepared["semantic_gates"]["research_working_draft"]
    from api.expert_teams.documents import _validate_research_working_draft_document, FinalDocumentDeliveryError
    import zipfile
    from xml.sax.saxutils import escape
    draft = binding["researchWorkingDraft"]
    document = tmp_path / "draft-notice.docx"
    paragraphs = [draft["label"], "原正文保留", "工作稿审阅意见（待核实）", *draft["notes"]]
    for omit in (False, True):
        texts = paragraphs[1:] if omit else paragraphs
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + ''.join(f'<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>' for text in texts) + '<w:sectPr/></w:body></w:document>'
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", xml)
        if omit:
            with pytest.raises(FinalDocumentDeliveryError, match="notice failed"):
                _validate_research_working_draft_document(document, binding)
        else:
            _validate_research_working_draft_document(document, binding)
    for attempt, field in enumerate(("research_writing_contract", "research_v3_independent_review", "research_v3_provenance_sidecar", "research_v3_review_ledger", "research_v3_canonical_document"), start=2):
        corrupt = copy.deepcopy(current)
        if field == "research_writing_contract":
            corrupt[field].pop("execution_policy")
        elif field == "research_v3_independent_review":
            corrupt[field]["canonical_body_sha256"] = "0" * 64
        elif field == "research_v3_provenance_sidecar":
            corrupt[field]["sidecar_sha256"] = "0" * 64
        elif field == "research_v3_review_ledger":
            corrupt[field]["units"][0]["receipt"] = None
        else:
            corrupt[field]["body"] = "## 缺章\n不完整正文"
        rejected = prepare_canonical_delivery_inputs(tmp_path, corrupt, stage_id="delivery", delivery_attempt=attempt)
        assert rejected["semantic_gates"]["status"] == "failed", field
