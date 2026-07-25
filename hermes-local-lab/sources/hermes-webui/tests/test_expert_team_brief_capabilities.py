from copy import deepcopy

import pytest


def _standalone_payload(document_type: str, *, prompt: str = "请生成文档") -> dict:
    template = {
        "work_report": "standalone-work-report",
        "research_report": "standalone-research-report",
    }[document_type]
    return {
        "contract_version": "expert-team-contract/v1",
        "product_mode": "standalone",
        "document_type": document_type,
        "intake_example_id": document_type,
        "prompt": prompt,
        "document_brief_seed": {
            "task_mode": "create",
            "document_control": {"render_template_id": template},
        },
    }


def _complete_work_report(brief: dict) -> dict:
    completed = deepcopy(brief)
    completed.update(
        {
            "exact_title": "迎峰度夏保供电重点工作月度汇报",
            "purpose": "向分管领导汇报工作进展并明确下一步安排",
            "audience": "公司分管领导",
            "usage_scenario": "月度工作例会",
        }
    )
    completed["details"].update(
        {"reporting_period": "2026年7月", "reporting_unit": "生产运营部"}
    )
    return completed


def _complete_research_report(brief: dict) -> dict:
    completed = deepcopy(brief)
    completed.update(
        {
            "exact_title": "人工智能辅助办公应用研究报告",
            "purpose": "为下一阶段产品决策提供依据",
            "audience": "项目决策小组",
            "usage_scenario": "专题研究评审会",
        }
    )
    completed["details"].update(
        {
            "core_question": "人工智能辅助办公在现有业务中应如何落地",
            "time_range": {"start": "2025-01-01", "end": "2026-07-25"},
        }
    )
    completed["source_policy"]["as_of_date"] = "2026-07-25"
    return completed


def test_launch_profiles_publish_chinese_profile_driven_brief_schemas():
    from api.expert_teams.launch_profiles import get_launch_profile

    work = get_launch_profile("content-work-report")
    research = get_launch_profile("research-report")

    assert [field["path"] for field in work["brief_schema"]] == [
        "exact_title",
        "purpose",
        "audience",
        "usage_scenario",
        "details.reporting_period",
        "details.reporting_unit",
    ]
    assert [field["path"] for field in research["brief_schema"]] == [
        "exact_title",
        "purpose",
        "audience",
        "usage_scenario",
        "details.core_question",
        "details.time_range.start",
        "details.time_range.end",
        "source_policy.as_of_date",
    ]
    for profile in (work, research):
        for field in profile["brief_schema"]:
            assert field["label"]
            assert field["placeholder"]
            assert field["help"]
            assert field["required"] is True
            assert "model_policy_id" not in field["path"]
            assert "classification" not in field["path"]
            assert "approval" not in field["path"]


def test_standalone_work_report_defaults_allow_labeled_placeholders_without_enterprise_fields():
    from api.expert_teams.contracts import build_document_brief, validate_document_brief

    brief = build_document_brief(
        "content-creator-team",
        _standalone_payload("work_report", prompt="起草部门月度工作汇报"),
        now="2026-07-25T10:00:00+08:00",
    )

    assert brief["source_policy"] == {
        "mode": "provided_only",
        "as_of_date": "",
        "citation_style": "none",
        "unknown_fact_action": "allow_labeled_placeholder",
        "source_refs": [],
    }
    assert brief["data_handling"] == {}
    assert brief["document_control"] == {
        "render_template_id": "standalone-work-report"
    }
    assert brief["approval"] == {}

    validation = validate_document_brief(
        _complete_work_report(brief),
        runtime_capabilities={},
        source_registry={},
        model_policy_registry={},
        now="2026-07-25T10:00:00+08:00",
    )

    assert validation["valid_for_confirmation"] is True
    assert validation["field_errors"] == []
    assert validation["model_policy"] == {
        "policy_id": "",
        "label": "单机版由服务端模型配置决定",
        "authorized": True,
        "authorization_basis": "server_runtime",
    }


def test_standalone_ignores_client_enterprise_policy_and_classification_fields():
    from api.expert_teams.contracts import build_document_brief, patch_document_brief

    payload = _standalone_payload("work_report")
    payload["document_brief_seed"].update(
        {
            "data_handling": {"model_policy_id": "client-forged-policy"},
            "approval": {"approver_roles": ["企业审批人"]},
            "document_control": {
                "render_template_id": "standalone-work-report",
                "classification": "restricted",
                "classification_label": "机密",
            },
        }
    )
    brief = build_document_brief(
        "content-creator-team", payload, now="2026-07-25T10:00:00+08:00"
    )

    assert brief["data_handling"] == {}
    assert brief["approval"] == {}
    assert brief["document_control"] == {
        "render_template_id": "standalone-work-report"
    }

    brief["source_policy"]["source_refs"] = [
        {"source_id": "SRC-KEEP", "kind": "provided_text", "label": "已绑定资料"}
    ]
    patched = patch_document_brief(
        brief,
        {
            "details": {"reporting_period": "2026年7月"},
            "source_policy": {"as_of_date": "2026-07-25"},
        },
        expected_revision=1,
        stage_started=False,
    )
    assert patched["data_handling"] == {}
    assert patched["source_policy"]["unknown_fact_action"] == "allow_labeled_placeholder"
    assert patched["source_policy"]["source_refs"][0]["source_id"] == "SRC-KEEP"
    assert patched["source_policy"]["as_of_date"] == "2026-07-25"
    assert patched["details"]["reporting_period"] == "2026年7月"


def test_enterprise_mapping_patch_keeps_original_replace_semantics():
    from api.expert_teams.contracts import build_document_brief, patch_document_brief

    payload = {
        "document_type": "work_report",
        "prompt": "起草企业工作汇报",
        "document_brief_seed": {
            "source_policy": {
                "mode": "provided_only",
                "citation_style": "source_id",
                "unknown_fact_action": "block_final",
                "source_refs": [
                    {"source_id": "SRC-OLD", "kind": "attachment", "label": "旧资料"}
                ],
            }
        },
    }
    brief = build_document_brief(
        "content-creator-team", payload, now="2026-07-25T10:00:00+08:00"
    )
    patched = patch_document_brief(
        brief,
        {"source_policy": {"as_of_date": "2026-07-25"}},
        expected_revision=1,
        stage_started=False,
    )

    assert patched["source_policy"] == {
        "as_of_date": "2026-07-25",
        "source_refs": [],
    }


def test_standalone_patch_rejects_fields_outside_profile_schema():
    from api.expert_teams.contracts import (
        ContractError,
        build_document_brief,
        patch_document_brief,
    )

    brief = build_document_brief(
        "content-creator-team",
        _standalone_payload("work_report"),
        now="2026-07-25T10:00:00+08:00",
    )
    rejected = (
        {"data_handling": {"model_policy_id": "client-forged-policy"}},
        {"document_control": {"classification": "restricted"}},
        {"approval": {"approver_roles": ["企业审批人"]}},
        {"details": {"reporting_period": "2026年7月", "admin_override": True}},
        {"content_constraints": {"must_include": ["未经确认的数据"]}},
    )

    for patch in rejected:
        with pytest.raises(ContractError) as error:
            patch_document_brief(
                brief,
                patch,
                expected_revision=1,
                stage_started=False,
            )
        assert error.value.code == "unknown_brief_field"


def test_standalone_runtime_brief_update_rejects_unknown_nested_client_field(
    tmp_path, monkeypatch
):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")
    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "standalone-brief-whitelist",
            **_standalone_payload("work_report", prompt="形成月度工作汇报"),
        },
    )

    with pytest.raises(ContractError) as error:
        expert_teams.update_expert_team_document_brief(
            tmp_path,
            {
                "session_id": run["session_id"],
                "run_id": run["run_id"],
                "expected_version": run["version"],
                "expected_brief_revision": 1,
                "idempotency_key": "standalone-whitelist-1",
                "patch": {
                    "details": {
                        "reporting_period": "2026年7月",
                        "private_override": "bypass",
                    }
                },
            },
        )

    assert error.value.code == "unknown_brief_field"
    assert error.value.field == "details.private_override"


def test_standalone_research_report_requires_ready_source_and_traceable_citation():
    from api.expert_teams.contracts import build_document_brief, validate_document_brief

    brief = build_document_brief(
        "deep-research-team",
        _standalone_payload("research_report", prompt="形成专题研究报告"),
        now="2026-07-25T10:00:00+08:00",
    )
    completed = _complete_research_report(brief)

    missing_source = validate_document_brief(
        completed,
        runtime_capabilities={},
        source_registry={},
        model_policy_registry={},
        now="2026-07-25T10:00:00+08:00",
    )
    assert missing_source["valid_for_confirmation"] is False
    assert any(
        item["field"] == "source_policy.source_refs"
        and item["code"] == "source_required"
        for item in missing_source["field_errors"]
    )

    completed["source_policy"]["source_refs"] = [
        {"source_id": "SRC-RESEARCH", "kind": "attachment", "label": "研究资料"}
    ]
    ready_source = validate_document_brief(
        completed,
        runtime_capabilities={},
        source_registry={
            "SRC-RESEARCH": {
                "status": "ready",
                "sha256": "a" * 64,
                "kind": "attachment",
            }
        },
        model_policy_registry={},
        now="2026-07-25T10:00:00+08:00",
    )
    assert ready_source["valid_for_confirmation"] is True
    assert ready_source["field_errors"] == []

    completed["source_policy"]["citation_style"] = "none"
    no_citation = validate_document_brief(
        completed,
        runtime_capabilities={},
        source_registry={
            "SRC-RESEARCH": {
                "status": "ready",
                "sha256": "a" * 64,
                "kind": "attachment",
            }
        },
        model_policy_registry={},
        now="2026-07-25T10:00:00+08:00",
    )
    assert any(
        item["code"] == "citation_style_required"
        for item in no_citation["field_errors"]
    )


def test_enterprise_contract_still_requires_classification_source_and_model_policy():
    from api.expert_teams.contracts import build_document_brief, validate_document_brief

    payload = {
        "contract_version": "expert-team-contract/v1",
        "document_type": "work_report",
        "prompt": "起草企业工作汇报",
        "document_brief_seed": {
            "task_mode": "create",
            "exact_title": "企业工作汇报",
            "purpose": "经营汇报",
            "audience": "管理层",
            "usage_scenario": "经营分析会",
            "source_policy": {
                "mode": "provided_only",
                "citation_style": "source_id",
                "unknown_fact_action": "block_final",
                "source_refs": [],
            },
            "details": {
                "reporting_period": "2026年7月",
                "reporting_unit": "经营管理部",
            },
        },
    }
    brief = build_document_brief(
        "content-creator-team", payload, now="2026-07-25T10:00:00+08:00"
    )
    validation = validate_document_brief(
        brief,
        runtime_capabilities={},
        source_registry={},
        model_policy_registry={},
        now="2026-07-25T10:00:00+08:00",
    )
    codes = {item["code"] for item in validation["field_errors"]}
    assert {"invalid_enum", "source_unresolved", "data_egress_not_authorized"} <= codes


def test_standalone_view_projects_field_values_errors_and_source_requirement():
    from api import expert_teams

    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "brief-profile-view",
            "launch_profile_id": "research-report",
            "prompt": "形成专题研究报告",
            "idempotency_key": "brief-profile-view-1",
        },
        run_id="et-brief-profile-view",
    )
    view_brief = run["view"]["brief"]

    assert view_brief["field_schema"][0]["path"] == "exact_title"
    assert view_brief["field_schema"][0]["value"] == ""
    assert any(
        field["path"] == "details.core_question" and field["label"] == "核心研究问题"
        for field in view_brief["field_schema"]
    )
    assert view_brief["source_requirement"] == {
        "minimum_ready": 1,
        "empty_help": "研究报告必须至少添加一份可核对资料，并在正文中保留引用。",
    }
    assert view_brief["field_errors"] == []
