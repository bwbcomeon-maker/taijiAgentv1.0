import copy
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


def _request(expert_teams, workspace, run, key, *, category="scope", input_id="conclusion-input"):
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
            conservative_assumption="按单一部门试点口径作保守结论。",
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
