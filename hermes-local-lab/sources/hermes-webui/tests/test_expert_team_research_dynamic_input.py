import copy
import hashlib
import json

import pytest


def _research_run(expert_teams, workspace):
    from api.expert_teams import runtime

    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": "research-dynamic-input",
            "prompt": "评估本地优先 AI 助理的落地边界",
            "idempotency_key": "research-dynamic-input-start",
        },
        run_id="et-research-dynamic-input",
    )
    run = expert_teams.bind_initial_standalone_source_context(workspace, run)
    direction = {
        "artifact_id": "ART-DIRECTION-DYNAMIC",
        "sha256": "d" * 64,
        "stage_id": "direction",
        "artifact_type": "research_charter",
        "stage_attempt": 1,
        "payload": {
            "core_question": "本地优先 AI 助理如何落地",
            "subquestions": ["对象范围和统计口径如何确定"],
        },
        "validation_status": "valid",
        "blocking_issues": [],
    }
    run.update(
        {
            "current_stage_index": 1,
            "workflow_state": "ready_to_generate",
            "stage_artifacts": [direction],
            "stage_outputs": [
                {"task_id": "direction", "status": "confirmed", "artifact": copy.deepcopy(direction)}
            ],
            "approved_stage_artifact_refs": {
                "direction": {"artifact_id": direction["artifact_id"], "sha256": direction["sha256"]}
            },
            "local_stage_confirmations": [
                {
                    "stage_id": "direction",
                    "artifact_id": direction["artifact_id"],
                    "artifact_sha256": direction["sha256"],
                }
            ],
        }
    )
    return runtime._sync_derived(run)


def _memory_storage(monkeypatch, runtime, initial):
    cell = {"run": copy.deepcopy(initial)}
    monkeypatch.setattr(runtime, "read_run", lambda _workspace, _run_id: copy.deepcopy(cell["run"]))

    def write_run(_workspace, value):
        cell["run"] = copy.deepcopy(value)
        return copy.deepcopy(value)

    monkeypatch.setattr(runtime, "write_run", write_run)
    return cell


def _control(run, key, **extra):
    return {
        "run_id": run["run_id"],
        "session_id": run["session_id"],
        "expected_version": run["version"],
        "stage_id": run["current_stage"]["task_id"],
        "idempotency_key": key,
        **extra,
    }


def _request(
    expert_teams,
    workspace,
    run,
    key,
    *,
    category="scope",
    input_id="conclusion-input",
    conservative_assumption="按单一部门试点口径作保守结论。",
):
    return expert_teams.request_expert_team_stage_input(
        workspace,
        _control(
            run,
            key,
            input_id=input_id,
            scope="conclusion",
            blocking=True,
            category=category,
            reason="core_conclusion_ambiguity",
            question="核心结论应以单一部门还是全公司为研究对象？",
            impact="对象范围会改变成本结论和推广建议。",
            options=["单一部门", "全公司"],
            conservative_assumption=conservative_assumption,
        ),
    )


def _submit(expert_teams, workspace, paused, key, answer, *, input_id="conclusion-input"):
    body = _control(
        paused,
        key,
        input_id=input_id,
        answer=answer,
        note="回答仅用于当前研究阶段结论口径。",
    )
    return expert_teams.submit_expert_team_stage_input(workspace, body), body


def _at_stage(runtime, run, stage_id):
    staged = copy.deepcopy(run)
    staged["current_stage_index"] = next(
        index
        for index, stage in enumerate(staged["_tasks_template"])
        if stage["id"] == stage_id
    )
    return runtime._sync_derived(staged)


def _generating_reservation(run, *, input_refs, attempt=1):
    stage_id = run["current_stage"]["task_id"]
    authoritative_stage = next(
        stage
        for stage in run["_tasks_template"]
        if stage["id"] == stage_id
    )
    reservation = {
        "reservation_id": f"stage-attempt-{attempt}",
        "stage_id": stage_id,
        "stage_attempt": attempt,
        "executor": "model",
        "artifact_type": authoritative_stage["artifact_type"],
        "input_refs": copy.deepcopy(input_refs),
        "input_binding_sha256": hashlib.sha256(
            json.dumps(input_refs, sort_keys=True).encode()
        ).hexdigest(),
        "idempotency_key": f"attempt-{attempt}",
        "status": "generating",
        "created_at": "2026-08-03T10:00:00+08:00",
    }
    run["workflow_state"] = "generating"
    run["stage_attempt_counters"] = {stage_id: attempt}
    run["stage_attempt_reservations"] = [copy.deepcopy(reservation)]
    run["current_stage_attempt_reservation"] = copy.deepcopy(reservation)
    run["execution_stream_id"] = "stream-active"
    run["execution_runtime_run_id"] = "runtime-active"
    run["execution_stage_id"] = stage_id
    run["execution_attempt"] = attempt
    return run


@pytest.mark.parametrize(
    "stage_id",
    ["direction", "research", "evidence", "outline", "draft", "review"],
)
def test_v2_dynamic_input_gate_and_count_apply_to_every_research_stage(
    tmp_path, monkeypatch, stage_id
):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _at_stage(runtime, _research_run(expert_teams, tmp_path), stage_id)
    _memory_storage(monkeypatch, runtime, run)

    paused = _request(expert_teams, tmp_path, run, f"request-{stage_id}")

    assert paused["workflow_state"] == "awaiting_stage_input"
    assert paused["research_dynamic_input_count"] == 1
    assert paused["pending_input"]["scope"] == "conclusion"


@pytest.mark.parametrize(
    "stage_id",
    ["direction", "research", "evidence", "outline", "draft", "review"],
)
def test_v2_forbidden_dynamic_input_category_is_rejected_at_every_stage(
    tmp_path, monkeypatch, stage_id
):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _at_stage(runtime, _research_run(expert_teams, tmp_path), stage_id)
    _memory_storage(monkeypatch, runtime, run)

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as rejected:
        _request(
            expert_teams,
            tmp_path,
            run,
            f"reject-{stage_id}",
            category="network_failure",
        )
    assert rejected.value.code == "research_stage_input_not_allowed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("question", "断网了，请选择要使用哪个本地资料来源？"),
        ("question", "The network failed; which source should be selected?"),
        ("description", "网络失败后请选择本地资料来源。"),
        ("description", "Select a local source after the network failure."),
        ("impact", "证据不足，需要用户确定引用格式和报告日期。"),
        ("impact", "Evidence is insufficient and local knowledge selection is required."),
        ("options", ["继续", "选择本地资料来源"]),
        ("options", ["Continue", "Select local source after network failure"]),
        ("conservative_assumption", "假设当前标题、日期和引用格式已由用户确认。"),
        ("conservative_assumption", "Assume today's citation format after a network failure."),
    ],
)
def test_v2_dynamic_input_rejects_forbidden_semantics_in_all_server_owned_text_fields(
    tmp_path, monkeypatch, field, value
):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _at_stage(runtime, _research_run(expert_teams, tmp_path), "outline")
    if field == "conservative_assumption":
        run["research_dynamic_input_count"] = 2
    _memory_storage(monkeypatch, runtime, run)
    body = _control(
        run,
        f"semantic-reject-{field}",
        input_id=f"semantic-{field}",
        scope="conclusion",
        blocking=True,
        category="scope",
        reason="core_conclusion_ambiguity",
        question="核心结论的对象范围如何确定？",
        impact="对象范围会改变核心结论。",
        conservative_assumption="按单一部门试点口径作保守结论。",
        options=["单一部门", "全公司"],
    )
    body[field] = value

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as rejected:
        expert_teams.request_expert_team_stage_input(tmp_path, body)

    assert rejected.value.code == "research_stage_input_not_allowed"


@pytest.mark.parametrize("existing_kind", ["pending", "answered"])
def test_v2_stage_input_id_is_globally_unique_at_request_time(
    tmp_path, monkeypatch, existing_kind
):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path)
    duplicate_id = "global-stage-input-id"
    if existing_kind == "pending":
        run["pending_input"] = {
            "id": duplicate_id,
            "stage_id": "direction",
            "question": "历史待回答问题",
        }
    else:
        run["stage_inputs"] = [
            {
                "input_id": duplicate_id,
                "stage_id": "direction",
                "question": "历史已回答问题",
                "answer": "单一部门",
            }
        ]
    _memory_storage(monkeypatch, runtime, run)

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as rejected:
        _request(
            expert_teams,
            tmp_path,
            run,
            f"duplicate-{existing_kind}",
            input_id=duplicate_id,
        )

    assert rejected.value.code == "stage_input_id_conflict"


def test_v2_research_pending_input_persists_and_projects_conclusion_contract(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_run(expert_teams, tmp_path)
    _memory_storage(monkeypatch, runtime, run)
    paused = _request(expert_teams, tmp_path, run, "request-conclusion")

    assert paused["workflow_state"] == "awaiting_stage_input"
    assert paused["research_dynamic_input_count"] == 1
    assert {
        "scope": paused["pending_input"]["scope"],
        "blocking": paused["pending_input"]["blocking"],
        "question": paused["pending_input"]["question"],
        "impact": paused["pending_input"]["impact"],
        "options": paused["pending_input"]["options"],
    } == {
        "scope": "conclusion",
        "blocking": True,
        "question": "核心结论应以单一部门还是全公司为研究对象？",
        "impact": "对象范围会改变成本结论和推广建议。",
        "options": ["单一部门", "全公司"],
    }
    assert {
        key: paused["view"]["pending_input"][key]
        for key in ("scope", "blocking", "question", "impact", "options")
    } == {
        key: paused["pending_input"][key]
        for key in ("scope", "blocking", "question", "impact", "options")
    }


@pytest.mark.parametrize(
    "category",
    ["network_failure", "evidence_gap", "title", "date", "citation_format", "source_selection"],
)
def test_v2_research_rejects_non_conclusion_dynamic_input_categories(tmp_path, monkeypatch, category):
    from api import expert_teams
    from api.expert_teams import runtime

    workspace = tmp_path / category
    run = _research_run(expert_teams, workspace)
    _memory_storage(monkeypatch, runtime, run)

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as rejected:
        _request(expert_teams, workspace, run, f"reject-{category}", category=category)
    assert rejected.value.code == "research_stage_input_not_allowed"


def test_v2_research_third_question_continues_with_consumable_boundary_assumption(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    run = _research_run(expert_teams, tmp_path)
    _memory_storage(monkeypatch, runtime, run)
    first = _request(expert_teams, tmp_path, run, "request-one", input_id="conclusion-one")
    after_first, _ = _submit(
        expert_teams, tmp_path, first, "submit-one", "单一部门", input_id="conclusion-one"
    )
    second = _request(expert_teams, tmp_path, after_first, "request-two", category="object", input_id="conclusion-two")
    after_second, _ = _submit(
        expert_teams, tmp_path, second, "submit-two", "全公司", input_id="conclusion-two"
    )
    continued = _request(
        expert_teams,
        tmp_path,
        after_second,
        "request-three",
        category="caliber",
        input_id="conclusion-three",
    )

    assert continued["workflow_state"] == "ready_to_generate"
    assert continued.get("pending_input") in ({}, None)
    assert continued["research_dynamic_input_count"] == 2
    assert continued["research_boundary_assumptions"][-1] == {
        "scope": "conclusion",
        "stage_id": "research",
        "assumption": "按单一部门试点口径作保守结论。",
        "impact": "对象范围会改变成本结论和推广建议。",
    }
    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, continued)
    request = build_stage_gateway_request(
        continued,
        {"id": "research", "executor": "model", "artifact_type": "source_register", "depends_on": ["direction"]},
        source_context=snapshot,
    )
    envelope = json.loads(request["messages"][1]["content"])
    assert envelope["research_boundary_assumptions"] == continued["research_boundary_assumptions"]


def test_v2_question_limit_consumption_is_durable_unique_and_idempotent(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    run = _research_run(expert_teams, tmp_path)
    run["research_dynamic_input_count"] = 2
    stage = next(item for item in run["_tasks_template"] if item["id"] == "research")
    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, run)
    before_request = build_stage_gateway_request(
        run,
        stage,
        source_context=snapshot,
    )
    before_binding = runtime._stage_input_binding_sha256(
        run,
        stage,
        before_request["input_refs"],
    )
    _memory_storage(monkeypatch, runtime, run)
    body = _control(
        run,
        "limit-consumption-first",
        input_id="limit-consumption-1",
        scope="conclusion",
        blocking=True,
        category="scope",
        reason="core_conclusion_ambiguity",
        question="核心结论的对象范围如何确定？",
        impact="对象范围会改变核心结论。",
        options=["单一部门", "全公司"],
        conservative_assumption="按单一部门试点口径作保守结论。",
    )

    consumed = expert_teams.request_expert_team_stage_input(tmp_path, body)
    replay = expert_teams.request_expert_team_stage_input(tmp_path, body)

    assert consumed["workflow_state"] == "ready_to_generate"
    assert consumed.get("pending_input") in ({}, None)
    assert consumed["research_dynamic_input_count"] == 2
    assert consumed["research_boundary_assumptions"] == [
        {
            "scope": "conclusion",
            "stage_id": "research",
            "assumption": "按单一部门试点口径作保守结论。",
            "impact": "对象范围会改变核心结论。",
        }
    ]
    assert consumed["research_input_consumptions"] == [
        {
            "input_id": "limit-consumption-1",
            "stage_id": "research",
            "disposition": "conservative_assumption",
            "assumption": "按单一部门试点口径作保守结论。",
            "impact": "对象范围会改变核心结论。",
            "consumed_at": consumed["research_input_consumptions"][0]["consumed_at"],
        }
    ]
    assert replay == consumed
    assert replay["version"] == consumed["version"]
    after_request = build_stage_gateway_request(
        consumed,
        stage,
        source_context=expert_teams.verified_source_context_for_execution(
            tmp_path,
            consumed,
        ),
    )
    envelope = json.loads(after_request["messages"][1]["content"])
    assert envelope["research_input_consumptions"] == [
        {
            "input_id": "limit-consumption-1",
            "stage_id": "research",
            "disposition": "conservative_assumption",
        }
    ]
    assert after_request["data_envelope_sha256"] != before_request[
        "data_envelope_sha256"
    ]
    assert runtime._stage_input_binding_sha256(
        consumed,
        stage,
        after_request["input_refs"],
    ) != before_binding

    conflicting_body = copy.deepcopy(body)
    conflicting_body["idempotency_key"] = "limit-consumption-conflict"
    conflicting_body["expected_version"] = consumed["version"]
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as rejected:
        expert_teams.request_expert_team_stage_input(tmp_path, conflicting_body)

    assert rejected.value.code == "stage_input_id_conflict"


def test_v2_third_question_requires_non_empty_conservative_assumption(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _at_stage(runtime, _research_run(expert_teams, tmp_path), "outline")
    run["research_dynamic_input_count"] = 2
    _memory_storage(monkeypatch, runtime, run)

    with pytest.raises(ValueError, match="conservative_assumption"):
        _request(
            expert_teams,
            tmp_path,
            run,
            "request-third-empty",
            conservative_assumption="   ",
        )


def test_v2_third_question_supersedes_active_attempt_and_changes_same_stage_envelope(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    run = _research_run(expert_teams, tmp_path)
    run["research_dynamic_input_count"] = 2
    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, run)
    stage = {
        "id": "research",
        "executor": "model",
        "artifact_type": "source_register",
        "depends_on": ["direction"],
    }
    before_request = build_stage_gateway_request(run, stage, source_context=snapshot)
    run = _generating_reservation(run, input_refs=before_request["input_refs"])
    cell = _memory_storage(monkeypatch, runtime, run)

    continued = _request(expert_teams, tmp_path, run, "request-third-generating")

    assert continued["workflow_state"] == "ready_to_generate"
    assert continued.get("current_stage_attempt_reservation") in ({}, None)
    assert continued["stage_attempt_reservations"][-1]["status"] == "superseded_by_input"
    assert continued["execution_stream_id"] == ""
    assert continued["execution_runtime_run_id"] == ""
    after_request = build_stage_gateway_request(
        continued,
        stage,
        source_context=expert_teams.verified_source_context_for_execution(tmp_path, continued),
    )
    assert after_request["data_envelope_sha256"] != before_request["data_envelope_sha256"]
    reserved, reservation, created = expert_teams.reserve_stage_attempt(
        tmp_path,
        continued["run_id"],
        stage_id="research",
        executor="model",
        input_refs=after_request["input_refs"],
        idempotency_key="attempt-after-boundary-assumption",
    )
    assert created is True
    assert reservation["stage_attempt"] == 2
    assert reserved["current_stage_attempt_reservation"] == reservation
    assert cell["run"]["current_stage_attempt_reservation"] == reservation


def _answered_request(expert_teams, runtime, workspace, monkeypatch, answer):
    run = _research_run(expert_teams, workspace)
    _memory_storage(monkeypatch, runtime, run)
    paused = _request(expert_teams, workspace, run, "request-binding")
    answered, submit_body = _submit(expert_teams, workspace, paused, "submit-binding", answer)
    replay = expert_teams.submit_expert_team_stage_input(workspace, submit_body)
    assert replay["version"] == answered["version"]
    assert len(replay["stage_inputs"]) == 1
    return replay


def test_answer_is_immutable_stage_input_ref_in_envelope_and_attempt_binding(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    first_workspace = tmp_path / "first"
    first = _answered_request(expert_teams, runtime, first_workspace, monkeypatch, "单一部门")
    first_snapshot = expert_teams.verified_source_context_for_execution(first_workspace, first)
    first_request = build_stage_gateway_request(
        first,
        {"id": "research", "executor": "model", "artifact_type": "source_register", "depends_on": ["direction"]},
        source_context=first_snapshot,
    )
    first_envelope = json.loads(first_request["messages"][1]["content"])
    stage_input_ref = next(item for item in first_request["input_refs"] if item["ref_type"] == "stage_input")
    assert stage_input_ref == first["stage_inputs"][0]["ref"]
    assert set(stage_input_ref) == {"ref_type", "input_id", "stage_id", "sha256"}
    assert stage_input_ref["stage_id"] == "research"
    assert first_envelope["stage_inputs"] == first["stage_inputs"]
    _, first_reservation, _ = expert_teams.reserve_stage_attempt(
        first_workspace,
        first["run_id"],
        stage_id="research",
        executor="model",
        input_refs=first_request["input_refs"],
        idempotency_key="attempt-binding",
    )

    second_workspace = tmp_path / "second"
    second = _answered_request(expert_teams, runtime, second_workspace, monkeypatch, "全公司")
    second_snapshot = expert_teams.verified_source_context_for_execution(second_workspace, second)
    second_request = build_stage_gateway_request(
        second,
        {"id": "research", "executor": "model", "artifact_type": "source_register", "depends_on": ["direction"]},
        source_context=second_snapshot,
    )
    _, second_reservation, _ = expert_teams.reserve_stage_attempt(
        second_workspace,
        second["run_id"],
        stage_id="research",
        executor="model",
        input_refs=second_request["input_refs"],
        idempotency_key="attempt-binding",
    )

    assert first_request["input_refs"] != second_request["input_refs"]
    assert first_reservation["input_binding_sha256"] != second_reservation["input_binding_sha256"]


def test_submitting_answer_supersedes_old_attempt_and_next_reservation_increments(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    run = _research_run(expert_teams, tmp_path)
    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, run)
    stage = {
        "id": "research",
        "executor": "model",
        "artifact_type": "source_register",
        "depends_on": ["direction"],
    }
    original_request = build_stage_gateway_request(run, stage, source_context=snapshot)
    run = _generating_reservation(run, input_refs=original_request["input_refs"])
    _memory_storage(monkeypatch, runtime, run)
    paused = _request(expert_teams, tmp_path, run, "request-during-attempt")

    answered, _ = _submit(expert_teams, tmp_path, paused, "submit-during-attempt", "单一部门")

    assert answered.get("current_stage_attempt_reservation") in ({}, None)
    assert answered["stage_attempt_reservations"][-1]["status"] == "superseded_by_input"
    next_request = build_stage_gateway_request(
        answered,
        stage,
        source_context=expert_teams.verified_source_context_for_execution(tmp_path, answered),
    )
    reserved, reservation, created = expert_teams.reserve_stage_attempt(
        tmp_path,
        answered["run_id"],
        stage_id="research",
        executor="model",
        input_refs=next_request["input_refs"],
        idempotency_key="attempt-after-stage-input",
    )
    assert created is True
    assert reservation["stage_attempt"] == 2
    assert reserved["current_stage_attempt_reservation"] == reservation


def _direction_response(run):
    brief = run["document_brief"]
    payload = {
        "core_question": brief["details"]["core_question"],
        "decision_to_support": "支撑本地优先 AI 助理的落地决策",
        "scope_in": ["企业内部办公"],
        "scope_out": ["未经核验的实时市场数据"],
        "time_range": copy.deepcopy(brief["details"]["time_range"]),
        "source_policy": {
            key: brief["source_policy"][key]
            for key in ("mode", "as_of_date", "citation_style")
        },
        "subquestions": ["对象范围和统计口径如何确定"],
        "evaluation_criteria": ["结论边界可追溯"],
        "stop_conditions": ["无法核验时停止外推"],
    }
    meta = {
        "artifact_type": "research_charter",
        "summary": "研究方向已明确",
        "payload": payload,
        "blocking_issues": [],
    }
    return (
        "<<<TAIJI_META_V1>>>\n"
        + json.dumps(meta, ensure_ascii=False)
        + "\n<<<TAIJI_META_END>>>"
    )


def test_stage_input_ref_survives_real_build_and_completion_round_trip(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    run = _at_stage(runtime, _research_run(expert_teams, tmp_path), "direction")
    run.update(
        {
            "stage_artifacts": [],
            "stage_outputs": [],
            "approved_stage_artifact_refs": {},
            "local_stage_confirmations": [],
        }
    )
    cell = _memory_storage(monkeypatch, runtime, run)
    paused = _request(expert_teams, tmp_path, run, "request-direction-input")
    answered, _ = _submit(
        expert_teams,
        tmp_path,
        paused,
        "submit-direction-input",
        "单一部门",
    )
    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, answered)
    stage = {
        "id": "direction",
        "executor": "model",
        "artifact_type": "research_charter",
        "depends_on": [],
    }
    gateway = build_stage_gateway_request(answered, stage, source_context=snapshot)
    reserved, reservation, created = expert_teams.reserve_stage_attempt(
        tmp_path,
        answered["run_id"],
        stage_id="direction",
        executor="model",
        input_refs=gateway["input_refs"],
        idempotency_key="direction-attempt-after-input",
    )
    assert created is True
    reserved["workflow_state"] = "generating"
    reserved["current_stage_attempt_reservation"]["status"] = "generating"
    reserved["stage_attempt_reservations"][-1]["status"] = "generating"
    cell["run"] = copy.deepcopy(reserved)

    completed = runtime._complete_enterprise_stage_artifact(
        tmp_path,
        reserved,
        {"content": _direction_response(reserved)},
        task_id="direction",
    )

    assert completed["workflow_state"] == "ready_to_generate"
    assert completed["approved_stage_artifact_refs"]["direction"]["artifact_id"] == "direction:1"
    artifact = completed["stage_artifacts"][-1]
    assert artifact["stage_attempt"] == reservation["stage_attempt"]
    assert artifact["input_refs"] == gateway["input_refs"]
    assert next(ref for ref in artifact["input_refs"] if ref["ref_type"] == "stage_input") == answered["stage_inputs"][-1]["ref"]


@pytest.mark.parametrize(
    ("ref", "expected_code"),
    [
        (
            {"ref_type": "stage_input", "input_id": "input-1", "sha256": "a" * 64},
            "required_field_missing",
        ),
        (
            {
                "ref_type": "stage_input",
                "input_id": "input-1",
                "stage_id": "research",
                "sha256": "a" * 64,
            },
            "stage_input_stage_mismatch",
        ),
        (
            {
                "ref_type": "stage_input",
                "input_id": "input-1",
                "stage_id": "direction",
                "sha256": "not-a-sha",
            },
            "invalid_sha256",
        ),
    ],
)
def test_stage_artifact_strictly_validates_stage_input_refs(tmp_path, ref, expected_code):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.stage_artifacts import StageArtifactError, build_stage_artifact, parse_stage_response

    run = _at_stage(runtime, _research_run(expert_teams, tmp_path), "direction")
    parsed = parse_stage_response(
        _direction_response(run),
        artifact_type="research_charter",
        requires_document=False,
    )
    with pytest.raises(StageArtifactError) as rejected:
        build_stage_artifact(
            parsed,
            stage_id="direction",
            stage_attempt=1,
            brief=run["document_brief"],
            input_refs=[ref],
            now="2026-08-03T10:00:00+08:00",
        )
    assert rejected.value.code == expected_code


def test_historical_content_stage_input_contract_remains_available(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "historical-input", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    run["workflow_state"] = "ready_to_generate"
    from api.expert_teams.storage import write_run

    write_run(tmp_path, run)
    paused = expert_teams.request_expert_team_stage_input(
        tmp_path,
        _control(
            run,
            "historical-request",
            input_id="historical-input",
            question="确认历史内容口径？",
            description="保持原有阶段输入行为。",
        ),
    )
    assert paused["workflow_state"] == "awaiting_stage_input"
    assert paused["pending_input"]["question"] == "确认历史内容口径？"
