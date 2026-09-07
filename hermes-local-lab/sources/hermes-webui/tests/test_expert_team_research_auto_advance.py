import copy
import hashlib

import pytest


@pytest.fixture(autouse=True)
def _enable_contract_pilot(monkeypatch):
    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")


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


def _research_stage_run(expert_teams, runtime, workspace, stage_id):
    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "research-report",
            "session_id": f"auto-{stage_id}",
            "prompt": "评估企业本地优先 AI 助理的落地边界",
            "idempotency_key": f"auto-{stage_id}-start",
        },
        run_id=f"et-auto-{stage_id}",
    )
    run = expert_teams.bind_initial_standalone_source_context(workspace, run)
    index = next(
        index
        for index, stage in enumerate(run["_tasks_template"])
        if stage["id"] == stage_id
    )
    run["current_stage_index"] = index
    run = runtime._sync_derived(run)
    stage = run["current_stage"]
    stage_descriptor = run["_tasks_template"][index]
    input_refs = []
    reservation = {
        "reservation_id": f"reservation-{stage_id}-1",
        "stage_id": stage_id,
        "stage_attempt": 1,
        "executor": "model",
        "artifact_type": stage_descriptor["artifact_type"],
        "input_refs": input_refs,
        "input_binding_sha256": hashlib.sha256(b"[]").hexdigest(),
        "idempotency_key": f"generate-{stage_id}-1",
        "status": "generating",
        "created_at": "2026-08-03T10:00:00+08:00",
    }
    prior_refs = {}
    prior_artifacts = []
    for prior in run["_tasks_template"][:index]:
        prior_id = prior["id"]
        prior_artifact = {
            "artifact_id": f"{prior_id}:1",
            "sha256": hashlib.sha256(prior_id.encode()).hexdigest(),
            "stage_id": prior_id,
            "artifact_type": prior["artifact_type"],
            "stage_attempt": 1,
            "input_refs": [],
            "payload": {},
            "blocking_issues": [],
            "validation_status": "valid",
        }
        prior_artifacts.append(prior_artifact)
        prior_refs[prior_id] = {
            "artifact_id": prior_artifact["artifact_id"],
            "sha256": prior_artifact["sha256"],
        }
    run.update(
        {
            "workflow_state": "generating",
            "stage_artifacts": prior_artifacts,
            "approved_stage_artifact_refs": prior_refs,
            "stage_attempt_counters": {stage_id: 1},
            "stage_attempt_reservations": [copy.deepcopy(reservation)],
            "current_stage_attempt_reservation": copy.deepcopy(reservation),
            "execution_stream_id": f"stream-{stage_id}-1",
            "execution_runtime_run_id": f"runtime-{stage_id}-1",
            "execution_stage_id": stage_id,
            "execution_attempt": 1,
        }
    )
    return runtime._sync_derived(run)


def _artifact_for(run, *, blocking=False):
    stage = run["current_stage"]
    stage_id = stage["task_id"]
    stage_descriptor = next(
        item for item in run["_tasks_template"] if item["id"] == stage_id
    )
    digest = hashlib.sha256(f"{stage_id}:1:auto".encode()).hexdigest()
    return {
        "artifact_id": f"{stage_id}:1",
        "sha256": digest,
        "stage_id": stage_id,
        "artifact_type": stage_descriptor["artifact_type"],
        "stage_attempt": 1,
        "input_refs": copy.deepcopy(
            run["current_stage_attempt_reservation"]["input_refs"]
        ),
        "payload": {"title": "企业本地优先 AI 助理研究报告"},
        "deliverable_markdown": (
            "# 企业本地优先 AI 助理研究报告\n\n"
            "## 研究问题\n本地优先 AI 助理如何落地。\n\n"
            "## 证据\n已按冻结资料核验。\n\n"
            "## 分析\n建议分阶段试点。\n\n"
            "## 结论边界\n不外推未核验事实。\n\n"
            "## 引用\n暂无可用外部引用。"
        ),
        "blocking_issues": (
            [
                {
                    "issue_id": "BLOCK-1",
                    "severity": "blocking",
                    "category": "evidence",
                    "field_path": None,
                    "message": "核心结论缺少证据",
                    "suggested_action": "重试当前阶段",
                }
            ]
            if blocking
            else []
        ),
        "validation_status": "valid",
    }


def _complete_with_artifact(monkeypatch, runtime, workspace, run, artifact):
    from api.expert_teams import stage_artifacts

    monkeypatch.setattr(stage_artifacts, "parse_stage_response", lambda *_a, **_k: {})
    monkeypatch.setattr(
        stage_artifacts,
        "build_stage_artifact",
        lambda *_a, **_k: copy.deepcopy(artifact),
    )
    return runtime._complete_enterprise_stage_artifact(
        workspace,
        run,
        {"content": "contract-valid-model-output"},
        task_id=run["current_stage"]["task_id"],
    )


@pytest.mark.parametrize(
    ("stage_id", "expected_state", "expected_next_index"),
    [
        ("direction", "ready_to_generate", 1),
        ("research", "ready_to_generate", 2),
        ("evidence", "ready_to_generate", 3),
        ("outline", "ready_to_generate", 4),
        ("draft", "ready_to_generate", 5),
        ("review", "delivery_validation_required", 6),
    ],
)
def test_research_v2_valid_model_stage_auto_approves_and_advances(
    tmp_path,
    monkeypatch,
    stage_id,
    expected_state,
    expected_next_index,
):
    from api import expert_teams
    from api.expert_teams import runtime

    workspace = tmp_path / stage_id
    run = _research_stage_run(expert_teams, runtime, workspace, stage_id)
    _memory_storage(monkeypatch, runtime, run)
    artifact = _artifact_for(run)

    completed = _complete_with_artifact(
        monkeypatch, runtime, workspace, run, artifact
    )

    assert completed["workflow_state"] == expected_state
    assert completed["workflow_state"] != "awaiting_review"
    assert completed["current_stage_index"] == expected_next_index
    assert completed["approved_stage_artifact_refs"][stage_id] == {
        "artifact_id": artifact["artifact_id"],
        "sha256": artifact["sha256"],
    }
    assert completed["stage_outputs"][-1]["status"] == "approved"
    assert completed["current_stage_attempt_reservation"]["status"] == "approved"
    assert completed.get("local_stage_confirmations") in (None, [])
    if stage_id == "review":
        assert completed["canonical_document_ref"]["artifact_id"] == artifact["artifact_id"]
        assert completed["pending_system_stage"]["id"] == "delivery"


def test_research_v2_blocking_artifact_retries_only_current_stage_and_keeps_snapshot(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_stage_run(expert_teams, runtime, tmp_path, "evidence")
    snapshot_ref = copy.deepcopy(run["source_context_snapshot_ref"])
    prior_refs = copy.deepcopy(run["approved_stage_artifact_refs"])
    _memory_storage(monkeypatch, runtime, run)

    blocked = _complete_with_artifact(
        monkeypatch,
        runtime,
        tmp_path,
        run,
        _artifact_for(run, blocking=True),
    )

    assert blocked["workflow_state"] == "generated_invalid"
    assert blocked["current_stage"]["task_id"] == "evidence"
    assert blocked["current_stage_index"] == 2
    assert blocked["source_context_snapshot_ref"] == snapshot_ref
    assert blocked["approved_stage_artifact_refs"] == prior_refs
    assert "evidence" not in blocked["approved_stage_artifact_refs"]
    assert blocked["current_stage_attempt_reservation"]["status"] == "generated_invalid"


def test_research_v2_provider_failure_never_auto_approves_or_builds_report(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_stage_run(expert_teams, runtime, tmp_path, "draft")
    snapshot_ref = copy.deepcopy(run["source_context_snapshot_ref"])
    prior_artifact_count = len(run["stage_artifacts"])
    _memory_storage(monkeypatch, runtime, run)

    failed = expert_teams.fail_expert_team_execution(
        tmp_path,
        run["run_id"],
        "Provider timeout",
        stream_id=run["execution_stream_id"],
        error_code="provider_timeout",
    )

    assert failed["workflow_state"] == "generation_failed"
    assert failed["current_stage"]["task_id"] == "draft"
    assert failed["source_context_snapshot_ref"] == snapshot_ref
    assert len(failed["stage_artifacts"]) == prior_artifact_count
    assert "draft" not in failed["approved_stage_artifact_refs"]
    assert failed.get("canonical_document_ref") in (None, {})
    assert failed.get("pending_system_stage") in (None, {})


def test_research_v2_duplicate_model_completion_is_read_only_replay(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams import stage_artifacts

    run = _research_stage_run(expert_teams, runtime, tmp_path, "direction")
    cell = _memory_storage(monkeypatch, runtime, run)
    artifact = _artifact_for(run)
    monkeypatch.setattr(stage_artifacts, "parse_stage_response", lambda *_a, **_k: {})
    monkeypatch.setattr(
        stage_artifacts,
        "build_stage_artifact",
        lambda *_a, **_k: copy.deepcopy(artifact),
    )
    delivery = {
        "stream_id": run["execution_stream_id"],
        "stage_id": "direction",
        "attempt": 1,
        "id": "direction-result",
        "kind": "chat",
        "content": "contract-valid-model-output",
    }

    first = expert_teams.mark_expert_team_execution_complete(
        tmp_path, run["run_id"], delivery
    )
    replay = expert_teams.mark_expert_team_execution_complete(
        tmp_path, run["run_id"], delivery
    )

    assert replay == first
    assert replay["version"] == first["version"]
    assert len(replay["stage_artifacts"]) == len(run["stage_artifacts"]) + 1
    assert len(replay.get("automatic_stage_approvals") or []) == 1
    assert replay["automatic_stage_approvals"][0]["delivery_id"] == delivery["id"]
    assert replay["automatic_stage_approvals"][0]["delivery_content_sha256"] == hashlib.sha256(
        delivery["content"].encode("utf-8")
    ).hexdigest()
    assert cell["run"] == first


@pytest.mark.parametrize(
    "delivery_override",
    [
        {"id": "direction-result", "content": "different-model-output"},
        {"id": "direction-result-2", "content": "contract-valid-model-output"},
    ],
)
def test_research_v2_conflicting_completion_replay_is_immutable(
    tmp_path, monkeypatch, delivery_override
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams import stage_artifacts

    run = _research_stage_run(expert_teams, runtime, tmp_path, "direction")
    _memory_storage(monkeypatch, runtime, run)
    artifact = _artifact_for(run)
    monkeypatch.setattr(stage_artifacts, "parse_stage_response", lambda *_a, **_k: {})
    monkeypatch.setattr(
        stage_artifacts,
        "build_stage_artifact",
        lambda *_a, **_k: copy.deepcopy(artifact),
    )
    delivery = {
        "stream_id": run["execution_stream_id"],
        "stage_id": "direction",
        "attempt": 1,
        "id": "direction-result",
        "kind": "chat",
        "content": "contract-valid-model-output",
    }
    expert_teams.mark_expert_team_execution_complete(
        tmp_path, run["run_id"], delivery
    )
    conflicting = {**delivery, **delivery_override}

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as rejected:
        expert_teams.mark_expert_team_execution_complete(
            tmp_path, run["run_id"], conflicting
        )

    assert rejected.value.code == "stage_completion_immutable_conflict"


def test_non_research_standalone_stage_keeps_manual_confirmation_behavior(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams import stage_artifacts

    run = expert_teams.build_standalone_expert_team_run(
        {
            "launch_profile_id": "content-work-report",
            "session_id": "manual-content",
            "prompt": "起草工作汇报",
            "idempotency_key": "manual-content-start",
        },
        run_id="et-manual-content",
    )
    run = expert_teams.bind_initial_standalone_source_context(tmp_path, run)
    run = runtime._sync_derived(run)
    stage = run["current_stage"]
    stage_descriptor = run["_tasks_template"][0]
    reservation = {
        "reservation_id": "manual-content-attempt",
        "stage_id": stage["task_id"],
        "stage_attempt": 1,
        "executor": "model",
        "artifact_type": stage_descriptor["artifact_type"],
        "input_refs": [],
        "input_binding_sha256": hashlib.sha256(b"[]").hexdigest(),
        "idempotency_key": "manual-content-generate",
        "status": "generating",
    }
    run.update(
        {
            "workflow_state": "generating",
            "stage_attempt_reservations": [copy.deepcopy(reservation)],
            "current_stage_attempt_reservation": copy.deepcopy(reservation),
        }
    )
    artifact = {
        "artifact_id": f"{stage['task_id']}:1",
        "sha256": "a" * 64,
        "stage_id": stage["task_id"],
            "artifact_type": stage_descriptor["artifact_type"],
        "stage_attempt": 1,
        "input_refs": [],
        "payload": {},
        "blocking_issues": [],
        "validation_status": "valid",
    }
    _memory_storage(monkeypatch, runtime, run)
    monkeypatch.setattr(stage_artifacts, "parse_stage_response", lambda *_a, **_k: {})
    monkeypatch.setattr(
        stage_artifacts, "build_stage_artifact", lambda *_a, **_k: artifact
    )

    completed = runtime._complete_enterprise_stage_artifact(
        tmp_path,
        run,
        {"content": "contract-valid-model-output"},
        task_id=stage["task_id"],
    )

    assert completed["workflow_state"] == "awaiting_review"
    assert completed["current_stage_index"] == 0
    assert completed.get("approved_stage_artifact_refs", {}) == {}


def test_research_v2_intermediate_view_never_exposes_manual_approve_action(tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    run = _research_stage_run(expert_teams, runtime, tmp_path, "outline")
    run["workflow_state"] = "awaiting_review"
    run["current_stage_attempt_reservation"]["status"] = "generated_valid"
    run["stage_attempt_reservations"][-1]["status"] = "generated_valid"
    run["current_stage_artifact_ref"] = {
        "artifact_id": "outline:1",
        "sha256": "a" * 64,
        "stage_attempt": 1,
    }

    view = expert_teams.expert_team_run_view(runtime._sync_derived(run))

    assert view["actions"]["can_approve_stage"] is False
    assert "approve_stage" not in view["allowed_actions"]


@pytest.mark.parametrize(
    "workflow_state",
    ["ready_to_generate", "delivery_validation_required"],
)
def test_get_reconciliation_never_dispatches_research_v2_external_side_effect(
    tmp_path, monkeypatch, workflow_state
):
    from api import expert_teams, routes
    from api.expert_teams import runtime

    stage_id = "review" if workflow_state == "delivery_validation_required" else "outline"
    run = _research_stage_run(expert_teams, runtime, tmp_path, stage_id)
    run["workflow_state"] = workflow_state
    if workflow_state == "delivery_validation_required":
        run["pending_system_stage"] = copy.deepcopy(
            run["launch_profile_snapshot"]["post_approval_system_steps"][0]
        )
        run["canonical_document_ref"] = {
            "artifact_id": "review:1",
            "sha256": "r" * 64,
        }
    run = runtime._sync_derived(run)
    starts = []
    monkeypatch.setattr(
        expert_teams,
        "reconcile_expert_team_run",
        lambda _workspace, _run_id: copy.deepcopy(run),
    )
    monkeypatch.setattr(routes, "_reconcile_expired_expert_team_start", lambda _w, value: value)
    monkeypatch.setattr(routes, "_reconcile_expert_team_orphan_cleanup", lambda _w, value: value)
    monkeypatch.setattr(
        routes, "_reconcile_expert_team_cancelling_unknown_start", lambda _w, value: value
    )

    def start(_workspace, value, _body):
        starts.append((value["run_id"], value["workflow_state"]))
        started = copy.deepcopy(value)
        started["workflow_state"] = "generating"
        return {"ok": True, "run": started}, 200

    monkeypatch.setattr(routes, "_start_expert_team_execution", start)

    result = routes._expert_team_run_with_execution_truth(tmp_path, run)

    assert starts == []
    assert result["workflow_state"] == workflow_state


def test_research_v2_system_delivery_can_continue_through_resume_mutation():
    from api import routes

    assert routes._expert_team_resume_requires_execution(
        {
            "workflow_state": "delivery_validation_required",
            "pending_system_stage": {"id": "delivery", "executor": "system"},
        }
    ) is True


def test_research_v2_delivery_projection_routes_the_authoritative_system_stage(
    tmp_path, monkeypatch
):
    from api import expert_teams, routes
    from api.expert_teams import runtime

    workspace = tmp_path / "delivery-projection"
    run = _research_stage_run(expert_teams, runtime, workspace, "review")
    _memory_storage(monkeypatch, runtime, run)
    completed = _complete_with_artifact(
        monkeypatch, runtime, workspace, run, _artifact_for(run)
    )
    authoritative = runtime._sync_derived(copy.deepcopy(completed))
    stale_display = copy.deepcopy(authoritative)
    stale_display["current_stage"] = copy.deepcopy(
        stale_display["_tasks_template"][5]
    )
    stale_display["current_stage"].update({"task_id": "review", "index": 5})

    projected = expert_teams.expert_team_run_view(stale_display)
    assert projected["workflow"]["current_stage"]["task_id"] == "delivery"
    assert projected["workspace"]["current_stage"]["id"] == "delivery"
    assert projected["presentation"]["current_stage"]["id"] == "delivery"

    cell = _memory_storage(monkeypatch, runtime, authoritative)
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale:
        runtime.resume_expert_team(
            workspace,
            {
                "run_id": authoritative["run_id"],
                "session_id": authoritative["session_id"],
                "stage_id": "review",
                "expected_version": authoritative["version"],
                "idempotency_key": "resume-stale-review",
            },
        )
    assert stale.value.code == "stale_stage"
    resumed = runtime.resume_expert_team(
        workspace,
        {
            "run_id": authoritative["run_id"],
            "session_id": authoritative["session_id"],
            "stage_id": projected["workflow"]["current_stage"]["task_id"],
            "expected_version": authoritative["version"],
            "idempotency_key": "resume-delivery",
        },
    )

    assert resumed["workflow_state"] == "delivery_validation_required"
    assert cell["run"]["current_stage"]["task_id"] == "review"
    assert routes._expert_team_resume_requires_execution(resumed) is True

    malformed = copy.deepcopy(authoritative)
    malformed["current_stage_index"] = 5
    malformed["current_stage"] = copy.deepcopy(malformed["_tasks_template"][5])
    malformed["current_stage"]["task_id"] = "review"
    assert expert_teams.expert_team_run_view(malformed)["workflow"]["current_stage"]["task_id"] == "review"
    _memory_storage(monkeypatch, runtime, malformed)
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as invalid:
        runtime.resume_expert_team(
            workspace,
            {
                "run_id": malformed["run_id"],
                "session_id": malformed["session_id"],
                "stage_id": "review",
                "expected_version": malformed["version"],
                "idempotency_key": "resume-corrupt-delivery-index",
            },
        )
    assert invalid.value.code == "corrupt_run_state"

    for label, template, index in (
        ("missing", [], 6),
        ("short", authoritative["_tasks_template"][:-1], 5),
        (
            "long",
            authoritative["_tasks_template"] + [copy.deepcopy(authoritative["pending_system_stage"])],
            7,
        ),
    ):
        malformed = copy.deepcopy(authoritative)
        malformed["_tasks_template"] = copy.deepcopy(template)
        malformed["current_stage_index"] = index
        assert expert_teams.expert_team_run_view(malformed)["workflow"]["current_stage"]["task_id"] == "review"
        _memory_storage(monkeypatch, runtime, malformed)
        with pytest.raises(expert_teams.ExpertTeamStateConflict) as invalid_template:
            runtime.resume_expert_team(
                workspace,
                {
                    "run_id": malformed["run_id"],
                    "session_id": malformed["session_id"],
                    "stage_id": "delivery",
                    "expected_version": malformed["version"],
                    "idempotency_key": f"resume-corrupt-delivery-template-{label}",
                },
            )
        assert invalid_template.value.code == "corrupt_run_state"


def test_system_delivery_reservation_replay_does_not_render_again(tmp_path, monkeypatch):
    from api import expert_teams, routes

    run = {
        "run_id": "et-system-replay",
        "session_id": "system-replay-session",
        "workflow_state": "delivery_validation_required",
        "pending_system_stage": {
            "id": "delivery",
            "executor": "system",
            "artifact_type": "delivery_manifest",
        },
        "canonical_document_ref": {"artifact_id": "review:1", "sha256": "a" * 64},
    }
    reservation = {
        "reservation_id": "delivery-reservation-1",
        "stage_id": "delivery",
        "stage_attempt": 1,
        "status": "generated_valid",
    }
    monkeypatch.setattr(
        expert_teams,
        "reserve_system_stage_attempt",
        lambda *_a, **_k: (
            copy.deepcopy(run),
            copy.deepcopy(run["pending_system_stage"]),
            copy.deepcopy(reservation),
            False,
        ),
    )
    renders = []
    monkeypatch.setattr(
        "api.expert_teams.system_stages.dispatch_system_stage",
        lambda *_a, **_k: renders.append("render") or {},
    )

    payload, status = routes._dispatch_expert_team_system_stage(tmp_path, run)

    assert status == 200
    assert payload["run"] == run
    assert renders == []


def test_research_v2_auto_approved_direction_is_a_gateway_dependency(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import build_stage_gateway_request

    workspace = tmp_path / "direction-gateway"
    run = _research_stage_run(expert_teams, runtime, workspace, "direction")
    _memory_storage(monkeypatch, runtime, run)
    completed = _complete_with_artifact(
        monkeypatch, runtime, workspace, run, _artifact_for(run)
    )

    snapshot = expert_teams.verified_source_context_for_execution(workspace, completed)
    research_stage = next(
        stage for stage in completed["_tasks_template"] if stage["id"] == "research"
    )
    request = build_stage_gateway_request(
        completed, research_stage, source_context=snapshot
    )

    import json
    envelope = json.loads(request["messages"][1]["content"])
    assert envelope["approved_input_artifacts"][0]["artifact_id"] == "direction:1"


@pytest.mark.parametrize("tamper", ["delete", "sha"])
def test_research_v2_auto_approval_journal_must_match_approved_direction(
    tmp_path, monkeypatch, tamper
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import PromptContractError, build_stage_gateway_request

    workspace = tmp_path / f"direction-{tamper}"
    run = _research_stage_run(expert_teams, runtime, workspace, "direction")
    _memory_storage(monkeypatch, runtime, run)
    completed = _complete_with_artifact(
        monkeypatch, runtime, workspace, run, _artifact_for(run)
    )
    snapshot = expert_teams.verified_source_context_for_execution(workspace, completed)
    if tamper == "delete":
        completed["automatic_stage_approvals"] = []
    else:
        completed["automatic_stage_approvals"][0]["artifact_sha256"] = "0" * 64

    research_stage = next(
        stage for stage in completed["_tasks_template"] if stage["id"] == "research"
    )
    with pytest.raises(PromptContractError):
        build_stage_gateway_request(
            completed, research_stage, source_context=snapshot
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "profile_id",
        "contract_version",
        "output_status",
        "approval_kind",
        "approved_ref_id",
        "approved_ref_sha",
        "journal_schema",
        "journal_stage",
        "journal_artifact_id",
        "journal_attempt",
    ],
)
def test_research_v2_auto_approval_requires_every_frozen_identity_and_audit_binding(
    tmp_path, monkeypatch, tamper
):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.prompts import PromptContractError, build_stage_gateway_request

    workspace = tmp_path / f"direction-binding-{tamper}"
    run = _research_stage_run(expert_teams, runtime, workspace, "direction")
    _memory_storage(monkeypatch, runtime, run)
    completed = _complete_with_artifact(
        monkeypatch, runtime, workspace, run, _artifact_for(run)
    )
    snapshot = expert_teams.verified_source_context_for_execution(workspace, completed)
    output = completed["stage_outputs"][-1]
    approval = completed["automatic_stage_approvals"][0]
    if tamper == "profile_id":
        completed["launch_profile_snapshot"]["id"] = "other-profile"
    elif tamper == "contract_version":
        completed["launch_profile_snapshot"]["research_contract_version"] = "research-report/v1"
    elif tamper == "output_status":
        output["status"] = "confirmed"
    elif tamper == "approval_kind":
        output["approval_kind"] = "local_confirmation"
    elif tamper == "approved_ref_id":
        completed["approved_stage_artifact_refs"]["direction"]["artifact_id"] = "direction:other"
    elif tamper == "approved_ref_sha":
        completed["approved_stage_artifact_refs"]["direction"]["sha256"] = "0" * 64
    elif tamper == "journal_schema":
        approval["schema_version"] = "automatic-stage-approval/v0"
    elif tamper == "journal_stage":
        approval["stage_id"] = "research"
    elif tamper == "journal_artifact_id":
        approval["artifact_id"] = "direction:other"
    else:
        approval["stage_attempt"] = 2

    research_stage = next(
        stage for stage in completed["_tasks_template"] if stage["id"] == "research"
    )
    with pytest.raises(PromptContractError):
        build_stage_gateway_request(
            completed, research_stage, source_context=snapshot
        )


def test_content_standalone_cannot_substitute_an_automatic_approval_for_local_confirmation():
    from api.expert_teams.prompts import PromptContractError, approved_inputs_for_stage

    artifact = {"artifact_id": "plan:1", "sha256": "a" * 64, "stage_attempt": 1}
    run = {
        "team_id": "content-creator-team",
        "product_mode": "standalone",
        "review_policy": {"kind": "local_confirmation"},
        "stage_outputs": [
            {
                "task_id": "plan",
                "status": "approved",
                "approval_kind": "automatic_contract_validation",
                "artifact": artifact,
            }
        ],
        "approved_stage_artifact_refs": {
            "plan": {"artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]}
        },
        "automatic_stage_approvals": [
            {
                "schema_version": "automatic-stage-approval/v1",
                "stage_id": "plan",
                "stage_attempt": 1,
                "artifact_id": artifact["artifact_id"],
                "artifact_sha256": artifact["sha256"],
            }
        ],
    }

    with pytest.raises(PromptContractError, match="缺少已批准阶段产物：plan"):
        approved_inputs_for_stage(run, "materials")
