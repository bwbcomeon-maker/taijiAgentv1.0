from copy import deepcopy
import json
from pathlib import Path
import re

import pytest


META_START = "<<<TAIJI_META_V1>>>"
META_END = "<<<TAIJI_META_END>>>"


def _issue(severity: str, issue_id: str = "ISS-1") -> dict:
    return {
        "issue_id": issue_id,
        "severity": severity,
        "category": "evidence",
        "field_path": None,
        "message": "存在待确认事项",
        "suggested_action": "请人工核对",
    }


def _raw(artifact_type: str, payload: dict, *, issues: list[dict] | None = None) -> str:
    return "\n".join(
        (
            META_START,
            json.dumps(
                {
                    "artifact_type": artifact_type,
                    "summary": "阶段产物摘要",
                    "payload": payload,
                    "blocking_issues": issues or [],
                },
                ensure_ascii=False,
            ),
            META_END,
        )
    )


def _confirmed_meeting_run(tmp_path, monkeypatch):
    from api import expert_teams
    from api.expert_teams import storage

    monkeypatch.setattr(
        storage,
        "_validate_public_standalone_run",
        lambda _workspace, candidate, **_kwargs: candidate,
    )
    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "meeting-warning-session",
            "launch_profile_id": "content-meeting-minutes",
            "prompt": "整理零资料会议纪要",
            "idempotency_key": "meeting-warning-launch",
        },
        run_id="meeting-warning-run",
    )
    brief = deepcopy(run["document_brief"])
    brief.update(
        {
            "exact_title": "供电服务质量提升专题会议纪要",
            "purpose": "记录会议议定事项、责任分工和后续安排",
            "audience": "参会部门负责人",
            "usage_scenario": "会后执行和跟踪",
        }
    )
    brief["details"].update(
        {
            "meeting_time": "2026年7月30日 14:00",
            "meeting_location": "第一会议室",
            "chairperson": "生产运营部负责人",
            "attendee_scope": "相关部门负责人",
        }
    )
    run["document_brief"] = brief
    stored = storage.write_run(tmp_path, run)
    return expert_teams.confirm_expert_team_document_brief(
        tmp_path,
        {
            "session_id": stored["session_id"],
            "run_id": stored["run_id"],
            "expected_version": stored["version"],
            "expected_brief_revision": stored["document_brief"]["revision"],
            "idempotency_key": "meeting-warning-confirm-brief",
        },
    )


def _complete_model_stage(tmp_path, run: dict, raw: str, *, stream_id: str) -> dict:
    from api import expert_teams

    input_refs = []
    if run["current_stage"]["task_id"] == "materials":
        plan_ref = run["approved_stage_artifact_refs"]["plan"]
        snapshot = expert_teams.verified_source_context_for_execution(tmp_path, run)
        input_refs = [
            {
                "ref_type": "stage_artifact",
                "artifact_id": plan_ref["artifact_id"],
                "sha256": plan_ref["sha256"],
            },
            {
                "ref_type": "source_context",
                "snapshot_id": snapshot["snapshot_id"],
                "sha256": snapshot["snapshot_sha256"],
            },
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
        {
            "stream_id": stream_id,
            "runtime_run_id": stream_id,
            "runtime_adapter": "RunnerRuntimeAdapter",
            "execution_start_id": reserved["execution_start_id"],
        },
    )
    return expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        run["run_id"],
        {
            "stream_id": generating["execution_stream_id"],
            "stage_id": generating["current_stage"]["task_id"],
            "attempt": generating["execution_attempt"],
            "id": f"output-{stream_id}",
            "kind": "chat",
            "content": raw,
        },
    )


def _confirm_current_stage(tmp_path, run: dict, *, key: str) -> dict:
    from api import expert_teams

    ref = run["current_stage_artifact_ref"]
    return expert_teams.confirm_standalone_expert_team_stage(
        tmp_path,
        {
            "session_id": run["session_id"],
            "run_id": run["run_id"],
            "expected_version": run["version"],
            "stage_id": run["current_stage"]["task_id"],
            "stage_attempt": ref["stage_attempt"],
            "artifact_id": ref["artifact_id"],
            "artifact_sha256": ref["sha256"],
            "idempotency_key": key,
        },
    )


def _meeting_issue_result(tmp_path, monkeypatch, *, severity: str) -> dict:
    run = _confirmed_meeting_run(tmp_path, monkeypatch)
    plan = _complete_model_stage(
        tmp_path,
        run,
        _raw(
            "writing_plan",
            {
                "objective": "形成可供会后执行和跟踪的会议纪要",
                "document_type": "meeting_minutes",
                "section_plan": [
                    {
                        "section_id": f"SEC-{index}",
                        "heading": heading,
                        "purpose": "按已确认规格组织内容",
                        "required_fact_ids": [],
                    }
                    for index, heading in enumerate(
                        ("会议基本情况", "议定事项", "责任分工", "后续跟踪"),
                        1,
                    )
                ],
                "fact_requirements": [],
                "assumptions": ["未知事实均标记为待补充"],
                "acceptance_checks": ["不得编造会议结论和责任信息"],
            },
        ),
        stream_id="meeting-plan-stream",
    )
    assert plan["workflow_state"] == "awaiting_review"
    ready_for_materials = _confirm_current_stage(
        tmp_path,
        plan,
        key="meeting-plan-confirm",
    )
    return _complete_model_stage(
        tmp_path,
        ready_for_materials,
        _raw(
            "material_ledger",
            {
                "source_assessments": [],
                "facts": [],
                "gaps": [
                    {
                        "gap_id": "GAP-1",
                        "description": "当前未提供会议原始记录",
                        "blocks_final": False,
                        "resolution": "在正文对应位置标注待补充",
                    }
                ],
            },
            issues=[_issue(severity, "missing_sources")],
        ),
        stream_id="meeting-materials-stream",
    )


def _meeting_warning_result(tmp_path, monkeypatch) -> dict:
    return _meeting_issue_result(tmp_path, monkeypatch, severity="warning")


@pytest.mark.parametrize(
    ("severity", "expected_state", "expected_blocking", "expected_warning"),
    [
        ("blocking", "blocked", 1, 0),
        ("error", "blocked", 1, 0),
        ("warning", "attention", 0, 1),
        ("info", "clear", 0, 0),
    ],
)
def test_issue_policy_has_one_severity_truth_source(
    severity,
    expected_state,
    expected_blocking,
    expected_warning,
):
    from api.expert_teams.issue_policy import classify_stage_issues

    quality = classify_stage_issues([_issue(severity)])

    assert quality["state"] == expected_state
    assert quality["blocking_count"] == expected_blocking
    assert quality["warning_count"] == expected_warning


def test_warning_only_legacy_invalid_artifact_is_effectively_reviewable():
    from api.expert_teams.issue_policy import effective_artifact_validation

    effective = effective_artifact_validation(
        {
            "validation_status": "invalid",
            "blocking_issues": [_issue("warning", "missing_sources")],
        }
    )

    assert effective == {
        "status": "valid",
        "original_status": "invalid",
        "legacy_warning_only": True,
        "state": "attention",
        "blocking_count": 0,
        "warning_count": 1,
        "info_count": 0,
    }


@pytest.mark.parametrize(
    "artifact",
    [
        {"validation_status": "invalid", "blocking_issues": []},
        {
            "validation_status": "invalid",
            "blocking_issues": [_issue("blocking")],
        },
        {
            "validation_status": "invalid",
            "blocking_issues": [_issue("error")],
        },
    ],
)
def test_invalid_without_warning_only_legacy_signature_stays_fail_closed(artifact):
    from api.expert_teams.issue_policy import effective_artifact_validation

    assert effective_artifact_validation(artifact)["status"] == "invalid"


def test_only_blocking_and_error_project_as_unresolved_upstream_issues():
    from api.expert_teams.stage_artifacts import unresolved_quality_issues

    projected = unresolved_quality_issues(
        {
            "blocking_issues": [
                _issue("blocking", "BLOCK-1"),
                _issue("error", "ERROR-1"),
                _issue("warning", "WARN-1"),
                _issue("info", "INFO-1"),
            ]
        }
    )

    assert [item["target_id"] for item in projected] == [
        "stage-issue:BLOCK-1",
        "stage-issue:ERROR-1",
    ]


def test_zero_source_meeting_minutes_warning_reaches_review_in_one_model_attempt(
    tmp_path,
    monkeypatch,
):
    result = _meeting_warning_result(tmp_path, monkeypatch)

    materials_artifacts = [
        item for item in result["stage_artifacts"] if item["stage_id"] == "materials"
    ]
    materials_outputs = [
        item for item in result["stage_outputs"] if item["task_id"] == "materials"
    ]
    assert result["workflow_state"] == "awaiting_review", json.dumps(
        result["stage_outputs"][-1], ensure_ascii=False
    )
    assert result["validation"] == {
        "status": "attention",
        "blocking_count": 0,
        "warning_count": 1,
        "message": "阶段产物可继续复核，但有待确认事项",
    }
    assert len(materials_artifacts) == 1
    assert materials_artifacts[0]["validation_status"] == "valid"
    assert len(materials_outputs) == 1
    assert result["stage_attempt_counters"]["materials"] == 1
    assert result["view"]["artifact_validation"] == {
        "status": "valid",
        "blocking_count": 0,
        "warning_count": 1,
    }
    assert result["view"]["stage_result"]["stage_quality"] == {
        "state": "attention",
        "blocking_count": 0,
        "warning_count": 1,
        "issues": [
            {
                "severity": "warning",
                "message": "存在待确认事项",
                "suggested_action": "请人工核对",
            }
        ],
    }
    assert result["view"]["allowed_actions"] == ["stage_confirm", "stage_revise"]
    assert result["view"]["stage_action_binding"]["artifact_id"] == "materials:1"


@pytest.mark.parametrize("severity", ["blocking", "error"])
def test_zero_source_meeting_minutes_blocking_issues_still_stop_progress(
    tmp_path,
    monkeypatch,
    severity,
):
    result = _meeting_issue_result(tmp_path, monkeypatch, severity=severity)
    artifact = next(
        item for item in result["stage_artifacts"] if item["stage_id"] == "materials"
    )

    assert result["workflow_state"] == "generated_invalid"
    assert result["validation"]["status"] == "rewrite_required"
    assert result["validation"]["blocking_count"] == 1
    assert artifact["validation_status"] == "valid"
    assert result["current_stage_attempt_reservation"]["status"] == "generated_invalid"


def test_resume_projects_legacy_warning_only_result_without_new_attempt(
    tmp_path,
    monkeypatch,
):
    from api import expert_teams
    from api.expert_teams.stage_artifacts import artifact_digest
    from api.expert_teams.storage import write_run

    current = _meeting_warning_result(tmp_path, monkeypatch)
    legacy = deepcopy(current)
    artifact = next(
        item for item in legacy["stage_artifacts"] if item["stage_id"] == "materials"
    )
    artifact["validation_status"] = "invalid"
    artifact["sha256"] = artifact_digest(artifact)
    legacy["current_stage_artifact_ref"] = {
        "artifact_id": artifact["artifact_id"],
        "sha256": artifact["sha256"],
        "stage_attempt": artifact["stage_attempt"],
    }
    for output in legacy["stage_outputs"]:
        if output.get("task_id") == "materials":
            output["status"] = "invalid"
            output["artifact"] = deepcopy(artifact)
    for reservation in legacy["stage_attempt_reservations"]:
        if reservation.get("stage_id") == "materials":
            reservation["status"] = "generated_invalid"
    legacy["current_stage_attempt_reservation"] = deepcopy(
        legacy["stage_attempt_reservations"][-1]
    )
    legacy["workflow_state"] = "generated_invalid"
    legacy["validation"] = {
        "status": "rewrite_required",
        "blocking_count": 1,
        "message": "阶段产物存在阻断问题",
    }
    legacy["last_validation_error"] = "阶段产物存在阻断问题"
    stored = write_run(tmp_path, legacy)
    attempts_before = deepcopy(stored["stage_attempt_counters"])
    outputs_before = len(stored["stage_outputs"])

    resumed = expert_teams.resume_expert_team(
        tmp_path,
        {
            "session_id": stored["session_id"],
            "run_id": stored["run_id"],
            "expected_version": stored["version"],
            "stage_id": "materials",
            "idempotency_key": "resume-warning-only-legacy-result",
        },
    )

    assert resumed["workflow_state"] == "awaiting_review"
    assert resumed["stage_attempt_counters"] == attempts_before
    assert len(resumed["stage_outputs"]) == outputs_before
    assert resumed["current_stage_attempt_reservation"]["status"] == "generated_valid"
    assert resumed["validation"] == {
        "status": "attention",
        "blocking_count": 0,
        "warning_count": 1,
        "message": "阶段产物可继续复核，但有待确认事项",
    }
    assert resumed["last_validation_error"] == ""


@pytest.mark.parametrize(
    ("workflow_state", "expected"),
    [
        ("ready_to_generate", True),
        ("awaiting_review", False),
        ("generated_invalid", False),
        ("completed", False),
    ],
)
def test_resume_route_dispatches_provider_only_for_ready_state(
    workflow_state,
    expected,
):
    from api.routes import _expert_team_resume_requires_execution

    assert _expert_team_resume_requires_execution(
        {"workflow_state": workflow_state}
    ) is expected


def test_runtime_modules_do_not_reintroduce_warning_as_blocking():
    api_dir = Path(__file__).resolve().parents[1] / "api" / "expert_teams"
    forbidden = re.compile(
        r"in\s*\{\s*['\"]blocking['\"]\s*,\s*['\"]error['\"]\s*,\s*['\"]warning['\"]\s*\}"
    )

    for name in ("runtime.py", "materials.py", "stage_artifacts.py", "view.py"):
        source = (api_dir / name).read_text(encoding="utf-8")
        assert forbidden.search(source) is None, name
