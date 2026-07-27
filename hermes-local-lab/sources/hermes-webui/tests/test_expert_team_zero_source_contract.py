from copy import deepcopy
import json

import pytest


def _persist_complete_standalone_work_report(workspace):
    from api import expert_teams
    from api.expert_teams.storage import write_run

    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "zero-source-work-report",
            "launch_profile_id": "content-work-report",
            "prompt": "起草部门月度工作汇报",
            "idempotency_key": "zero-source-launch",
        },
        run_id="et-zero-source-work-report",
    )
    brief = deepcopy(run["document_brief"])
    brief.update(
        {
            "exact_title": "迎峰度夏保供电重点工作月度汇报",
            "purpose": "向分管领导汇报工作进展并明确下一步安排",
            "audience": "公司分管领导",
            "usage_scenario": "月度工作例会",
        }
    )
    brief["details"].update(
        {"reporting_period": "2026年7月", "reporting_unit": "生产运营部"}
    )
    run["document_brief"] = brief
    return write_run(workspace, run)


def test_zero_source_work_report_confirms_and_materials_dispatch_binds_empty_snapshot(
    tmp_path, monkeypatch,
):
    from api import expert_teams, routes
    from api.expert_teams import storage

    # Atomic launch binding is exhaustively covered in its own suite.  This
    # test isolates the confirmation-to-dispatch source contract while still
    # using a schema-v3 standalone Run.
    monkeypatch.setattr(
        storage,
        "_validate_public_standalone_run",
        lambda _workspace, candidate, **_kwargs: candidate,
    )

    run = _persist_complete_standalone_work_report(tmp_path)
    confirmed = expert_teams.confirm_expert_team_document_brief(
        tmp_path,
        {
            "session_id": run["session_id"],
            "run_id": run["run_id"],
            "expected_version": run["version"],
            "expected_brief_revision": run["document_brief"]["revision"],
            "idempotency_key": "zero-source-confirm",
        },
    )

    assert confirmed["workflow_state"] == "ready_to_generate"
    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, confirmed)
    assert snapshot["sources"] == []
    assert snapshot["brief_sha256"] == confirmed["document_brief"]["confirmed_sha256"]

    dispatch_run = deepcopy(confirmed)
    dispatch_run["current_stage_index"] = 1
    dispatch_run["current_stage"] = {"task_id": "materials"}
    dispatch_run["stage_outputs"] = [
        {
            "task_id": "plan",
            "status": "confirmed",
            "artifact": {"artifact_id": "plan:1", "sha256": "b" * 64},
        }
    ]
    dispatch_run["approved_stage_artifact_refs"] = {
        "plan": {"artifact_id": "plan:1", "sha256": "b" * 64}
    }
    dispatch_run["local_stage_confirmations"] = [
        {
            "stage_id": "plan",
            "artifact_id": "plan:1",
            "artifact_sha256": "b" * 64,
        }
    ]
    request = routes._expert_team_enterprise_gateway_request(tmp_path, dispatch_run)
    envelope = json.loads(request["messages"][1]["content"])
    materials_output_contract = json.loads(
        request["messages"][0]["content"]
        .split("[OUTPUT CONTRACT]\n", 1)[1]
        .splitlines()[0]
    )

    assert envelope["source_context"] == snapshot
    assert request["input_refs"][-1] == {
        "ref_type": "source_context",
        "snapshot_id": snapshot["snapshot_id"],
        "sha256": snapshot["snapshot_sha256"],
    }
    assert "待补充" in request["messages"][0]["content"]
    assert "资料为空" not in request["messages"][0]["content"]
    assert materials_output_contract["allowed_payload_fields"] == [
        "source_assessments",
        "facts",
        "gaps",
    ]

    from api.expert_teams.prompts import build_stage_gateway_request
    from api.expert_teams.stage_artifacts import build_stage_artifact

    plan_request = build_stage_gateway_request(
        confirmed,
        confirmed["launch_profile_snapshot"]["stages"][0],
    )
    plan_output_contract = json.loads(
        plan_request["messages"][0]["content"]
        .split("[OUTPUT CONTRACT]\n", 1)[1]
        .splitlines()[0]
    )
    assert plan_output_contract["allowed_payload_fields"] == [
        "objective",
        "document_type",
        "section_plan",
        "fact_requirements",
        "assumptions",
        "acceptance_checks",
    ]

    plan = build_stage_artifact(
        {
            "artifact_type": "writing_plan",
            "summary": "先形成结构，再逐项标记待补充事实",
            "payload": {
                "objective": "形成可供用户补充事实的月度汇报",
                "document_type": "work_report",
                "section_plan": [
                    {
                        "section_id": "SEC-1",
                        "heading": "工作开展情况",
                        "purpose": "按结构标记待补充的工作事实",
                        "required_fact_ids": [],
                    },
                    {
                        "section_id": "SEC-2",
                        "heading": "存在问题",
                        "purpose": "按结构标记待补充的问题事实",
                        "required_fact_ids": [],
                    },
                    {
                        "section_id": "SEC-3",
                        "heading": "下一步工作安排",
                        "purpose": "按结构标记待补充的后续安排",
                        "required_fact_ids": [],
                    },
                ],
                "fact_requirements": [],
                "assumptions": ["当前没有来源资料，事实和数据均标记为待补充"],
                "acceptance_checks": ["不得编造事实或数据"],
            },
            "blocking_issues": [],
            "deliverable_markdown": None,
        },
        stage_id="plan",
        stage_attempt=1,
        brief=confirmed["document_brief"],
        input_refs=[],
        now="2026-07-25T12:00:00+08:00",
    )
    ledger = build_stage_artifact(
        {
            "artifact_type": "material_ledger",
            "summary": "记录待补充事实，不将没有资料本身作为阻断",
            "payload": {
                "source_assessments": [],
                "facts": [
                    {
                        "fact_id": "FACT-TBD-1",
                        "statement": "本月重点工作完成情况待补充",
                        "evidence_refs": [],
                        "status": "missing",
                        "usable": False,
                    }
                ],
                "gaps": [
                    {
                        "gap_id": "GAP-1",
                        "description": "缺少本月重点工作事实和数据",
                        "blocks_final": False,
                        "resolution": "在正文对应位置标注待补充",
                    }
                ],
            },
            "blocking_issues": [],
            "deliverable_markdown": None,
        },
        stage_id="materials",
        stage_attempt=1,
        brief=confirmed["document_brief"],
        input_refs=[
            {
                "ref_type": "stage_artifact",
                "artifact_id": plan["artifact_id"],
                "sha256": plan["sha256"],
            },
            {
                "ref_type": "source_context",
                "snapshot_id": snapshot["snapshot_id"],
                "sha256": snapshot["snapshot_sha256"],
            },
        ],
        source_snapshot=snapshot,
        now="2026-07-25T12:01:00+08:00",
    )

    assert plan["validation_status"] == "valid"
    assert ledger["validation_status"] == "valid"
    assert ledger["payload"]["sources"] == []


def test_empty_source_snapshot_remains_fail_closed_without_server_authorization(tmp_path):
    from api.expert_teams.source_context import (
        SourceContextError,
        build_source_context_snapshot,
    )

    with pytest.raises(SourceContextError, match="no sources"):
        build_source_context_snapshot(
            tmp_path,
            "et-enterprise-empty",
            {"source_policy": {"source_refs": []}},
            {},
            brief_sha256="a" * 64,
            brief_revision=1,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run: run.update(schema_version=2),
        lambda run: run.update(product_mode="enterprise"),
        lambda run: run["launch_profile_snapshot"].update(id="other-profile"),
        lambda run: run["launch_profile_snapshot"].update(team_id="deep-research-team"),
        lambda run: run["launch_profile_snapshot"]["source_requirement"].update(
            minimum_ready=1
        ),
        lambda run: run["document_brief"]["source_policy"].update(
            unknown_fact_action="block_final"
        ),
    ],
    ids=(
        "legacy-schema",
        "enterprise-mode",
        "profile-id-drift",
        "team-drift",
        "sources-required",
        "placeholder-policy-removed",
    ),
)
def test_empty_source_snapshot_reverification_fails_closed_on_authority_drift(
    tmp_path, mutate
):
    from api import expert_teams
    from api.expert_teams.contracts import brief_digest, confirm_document_brief
    from api.expert_teams.source_context import (
        SourceContextError,
        build_source_context_snapshot,
        verify_source_context_snapshot,
    )

    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "zero-source-authority",
            "launch_profile_id": "content-work-report",
            "prompt": "起草部门月度工作汇报",
            "idempotency_key": "zero-source-authority-launch",
        },
        run_id="et-zero-source-authority",
    )
    brief = deepcopy(run["document_brief"])
    brief.update(
        {
            "exact_title": "迎峰度夏保供电重点工作月度汇报",
            "purpose": "向分管领导汇报工作进展并明确下一步安排",
            "audience": "公司分管领导",
            "usage_scenario": "月度工作例会",
        }
    )
    brief["details"].update(
        {"reporting_period": "2026年7月", "reporting_unit": "生产运营部"}
    )
    confirmed = confirm_document_brief(brief, now="2026-07-25T12:00:00+08:00")
    run["document_brief"] = confirmed
    run["source_context_snapshot_ref"] = build_source_context_snapshot(
        tmp_path,
        run["run_id"],
        confirmed,
        {},
        brief_sha256=brief_digest(confirmed),
        brief_revision=confirmed["confirmed_revision"],
        allow_empty=True,
    )

    assert verify_source_context_snapshot(tmp_path, run)["sources"] == []
    mutate(run)
    with pytest.raises(SourceContextError):
        verify_source_context_snapshot(tmp_path, run)


def test_snapshot_io_failure_is_projected_as_stable_brief_contract_error(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams import source_context, storage

    monkeypatch.setattr(
        storage,
        "_validate_public_standalone_run",
        lambda _workspace, candidate, **_kwargs: candidate,
    )
    run = _persist_complete_standalone_work_report(tmp_path)
    monkeypatch.setattr(
        source_context.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(expert_teams.ContractError) as error:
        expert_teams.confirm_expert_team_document_brief(
            tmp_path,
            {
                "session_id": run["session_id"],
                "run_id": run["run_id"],
                "expected_version": run["version"],
                "expected_brief_revision": run["document_brief"]["revision"],
                "idempotency_key": "zero-source-confirm-io-failure",
            },
        )

    assert error.value.code == "source_context_invalid"
    assert error.value.field == "source_policy.source_refs"
