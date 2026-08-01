"""RED contract tests for one authoritative expert-team capability registry.

These tests intentionally describe the public API needed to remove the current
standalone/enterprise release coupling.  Production code is added only after
this file has been observed failing for the expected missing-contract reasons.
"""

from __future__ import annotations

from copy import deepcopy

import pytest


def _public_callable(module, name: str):
    value = getattr(module, name, None)
    assert callable(value), f"missing public capability-registry API: {module.__name__}.{name}"
    return value


def _synthetic_capability(
    capability_id: str,
    document_type: str,
    *,
    standalone_template: str | None,
    enterprise_template: str | None,
    task_mode: str = "create",
) -> dict:
    return {
        "capability_id": capability_id,
        "document_type": document_type,
        "task_mode": task_mode,
        "brief_schema": [
            {
                "path": "purpose",
                "label": "处理目标",
                "control": "textarea",
                "required": True,
            }
        ],
        "standalone_defaults": {
            "source_policy": {
                "mode": "provided_only",
                "as_of_date": "",
                "citation_style": "none",
                "unknown_fact_action": "allow_labeled_placeholder",
                "source_refs": [],
            },
            "data_handling": {},
            "document_control": {},
            "content_constraints": {
                "required_sections": [],
                "must_include": [],
                "must_avoid": [],
            },
            "details": {},
            "approval": {},
        },
        "source_requirement": {
            "minimum_ready": 0,
            "empty_help": "可以不添加资料。",
        },
        "releases": {
            "standalone": {
                "released": standalone_template is not None,
                "render_template_id": standalone_template,
            },
            "enterprise": {
                "released": enterprise_template is not None,
                "render_template_id": enterprise_template,
            },
        },
    }


def _available_profile_ids(catalog: dict) -> set[str]:
    return {
        str(example["launch_profile_id"])
        for team in catalog.get("teams") or []
        for example in team.get("examples") or []
        if example.get("available") is True
    }


@pytest.mark.parametrize(
    (
        "document_type",
        "task_mode",
        "capability_id",
        "template_id",
        "sections",
        "minimum_ready",
    ),
    [
        (
            "meeting_minutes",
            "create",
            "content-meeting-minutes",
            "standalone-meeting-minutes",
            ["会议基本情况", "议定事项", "责任分工", "后续跟踪"],
            0,
        ),
        (
            "notice",
            "create",
            "content-notice",
            "standalone-office-material",
            ["背景与总体要求", "通知事项", "时间安排", "责任分工", "报送要求"],
            0,
        ),
        (
            "plan",
            "create",
            "content-plan",
            "standalone-office-material",
            ["目标", "现状与问题", "主要措施", "进度安排", "保障机制"],
            0,
        ),
        (
            "summary_plan",
            "create",
            "content-summary-plan",
            "standalone-office-material",
            ["阶段性工作总结", "成效与亮点", "问题与不足", "下一步工作计划"],
            0,
        ),
        (
            "other_office_material",
            "polish",
            "content-polish",
            "standalone-office-material",
            ["润色后正文", "修改说明"],
            1,
        ),
    ],
)
def test_all_content_capabilities_are_released(
    document_type,
    task_mode,
    capability_id,
    template_id,
    sections,
    minimum_ready,
):
    from api.expert_teams.document_capabilities import resolve_document_capability

    capability = resolve_document_capability(
        document_type,
        task_mode,
        product_mode="standalone",
    )

    assert capability is not None
    assert capability["capability_id"] == capability_id
    assert capability["render_template_id"] == template_id
    assert capability["standalone_defaults"]["content_constraints"][
        "required_sections"
    ] == sections
    assert capability["source_requirement"]["minimum_ready"] == minimum_ready


def test_content_launch_profiles_cover_every_catalog_task_in_stable_order():
    from api.expert_teams.launch_profiles import CONTENT_PHASES, list_launch_profiles

    profiles = list_launch_profiles()
    content_profiles = profiles[:6]

    assert [item["id"] for item in content_profiles] == [
        "content-work-report",
        "content-meeting-minutes",
        "content-notice",
        "content-plan",
        "content-summary-plan",
        "content-polish",
    ]
    assert all(item["team_id"] == "content-creator-team" for item in content_profiles)
    assert all(item["stages"] == CONTENT_PHASES for item in content_profiles)


def test_mode_specific_capability_resolution_keeps_release_sets_independent(monkeypatch):
    from api.expert_teams import document_capabilities

    monkeypatch.setattr(
        document_capabilities,
        "_CAPABILITIES",
        {
            "standalone-only": _synthetic_capability(
                "standalone-only",
                "standalone_only",
                standalone_template="standalone-only-template",
                enterprise_template=None,
            ),
            "enterprise-only": _synthetic_capability(
                "enterprise-only",
                "enterprise_only",
                standalone_template=None,
                enterprise_template="enterprise-only-template",
            ),
        },
    )
    resolve = _public_callable(document_capabilities, "resolve_document_capability")

    standalone = resolve("standalone_only", "create", product_mode="standalone")
    enterprise = resolve("enterprise_only", "create", product_mode="enterprise")

    assert standalone["capability_id"] == "standalone-only"
    assert standalone["product_mode"] == "standalone"
    assert standalone["render_template_id"] == "standalone-only-template"
    assert enterprise["capability_id"] == "enterprise-only"
    assert enterprise["product_mode"] == "enterprise"
    assert enterprise["render_template_id"] == "enterprise-only-template"

    try:
        unavailable = resolve("enterprise_only", "create", product_mode="standalone")
    except KeyError as error:  # pragma: no cover - the assertion is the contract
        pytest.fail(f"mode-specific capability resolution must not leak a KeyError: {error}")
    assert unavailable is None
    assert resolve("standalone_only", "create", product_mode="enterprise") is None
    assert resolve("standalone_only", "create", product_mode="unknown") is None


def test_listed_launch_profiles_are_capability_derived_and_mismatches_are_rejected():
    from api.expert_teams import document_capabilities, launch_profiles
    from api.expert_teams.contracts import ContractError

    resolve = _public_callable(document_capabilities, "resolve_document_capability")
    validate = _public_callable(launch_profiles, "validate_launch_profiles")
    profiles = validate(product_mode="standalone")

    assert profiles
    for profile in profiles:
        capability = resolve(
            profile["document_type"],
            profile["task_mode"],
            product_mode="standalone",
        )
        assert capability is not None
        assert profile["capability_id"] == capability["capability_id"]
        assert profile["document_type"] == capability["document_type"]
        assert profile["task_mode"] == capability["task_mode"]
        assert profile["render_template_id"] == capability["render_template_id"]
        assert profile["brief_schema"] == capability["brief_schema"]
        assert profile["source_requirement"] == capability["source_requirement"]
        assert profile["content_constraints"] == capability["standalone_defaults"][
            "content_constraints"
        ]

    mismatched = deepcopy(profiles[0])
    mismatched["render_template_id"] = "standalone-research-report"
    with pytest.raises(ContractError) as error:
        validate([mismatched], product_mode="standalone")

    assert error.value.code == "launch_profile_capability_mismatch"


def test_catalog_available_set_equals_validated_profiles():
    from api.expert_teams import catalog as catalog_module
    from api.expert_teams import launch_profiles

    validate = _public_callable(launch_profiles, "validate_launch_profiles")
    validated = validate(product_mode="standalone")

    assert _available_profile_ids(catalog_module.expert_team_catalog()) == {
        profile["id"] for profile in validated
    }


def test_catalog_fails_closed_for_a_capability_mismatched_profile(monkeypatch):
    from api.expert_teams import catalog as catalog_module
    from api.expert_teams.launch_profiles import list_launch_profiles

    mismatched = deepcopy(list_launch_profiles()[0])
    mismatched["task_mode"] = "polish"
    monkeypatch.setattr(catalog_module, "list_launch_profiles", lambda: [mismatched])

    catalog = catalog_module.expert_team_catalog()
    assert _available_profile_ids(catalog) == set()
    examples = [example for team in catalog["teams"] for example in team["examples"]]
    assert all(example["capability"] == {"kind": "unavailable", "label": "任务配置异常"} for example in examples)
    assert all("当前任务配置异常" in example["disabled_reason"] for example in examples)


def test_catalog_does_not_silently_overwrite_duplicate_team_example_pairs(monkeypatch):
    from api.expert_teams import catalog as catalog_module
    from api.expert_teams.launch_profiles import list_launch_profiles

    first = deepcopy(list_launch_profiles()[0])
    duplicate = deepcopy(first)
    duplicate["id"] = f"{first['id']}-duplicate"
    monkeypatch.setattr(
        catalog_module,
        "list_launch_profiles",
        lambda: [first, duplicate],
    )

    assert _available_profile_ids(catalog_module.expert_team_catalog()) == set()


@pytest.mark.parametrize(
    ("product_mode", "render_template_id"),
    [
        ("standalone", "standalone-work-report"),
        (None, "enterprise-work-report"),
    ],
)
def test_work_report_polish_is_not_released_by_the_global_task_mode_enum(
    product_mode,
    render_template_id,
):
    from api.expert_teams.contracts import ContractError, build_document_brief

    payload = {
        "contract_version": "expert-team-contract/v1",
        "document_type": "work_report",
        "prompt": "在保持原意的前提下润色工作汇报",
        "document_brief_seed": {
            "task_mode": "polish",
            "document_control": {"render_template_id": render_template_id},
        },
    }
    if product_mode is not None:
        payload["product_mode"] = product_mode

    with pytest.raises(ContractError) as error:
        build_document_brief(
            "content-creator-team",
            payload,
            now="2026-07-25T12:00:00+08:00",
        )

    assert error.value.code == "capability_not_released"


def test_unknown_persisted_product_mode_is_never_reinterpreted_as_enterprise():
    from api.expert_teams.contracts import (
        build_document_brief,
        validate_document_brief,
    )

    brief = build_document_brief(
        "content-creator-team",
        {
            "contract_version": "expert-team-contract/v1",
            "product_mode": "standalone",
            "document_type": "work_report",
            "prompt": "起草月度工作汇报",
            "document_brief_seed": {
                "exact_title": "生产运营部月度工作汇报",
                "purpose": "汇报本月进展并安排下月工作",
                "audience": "部门负责人",
                "usage_scenario": "月度例会",
                "details": {
                    "reporting_period": "2026年7月",
                    "reporting_unit": "生产运营部",
                },
            },
        },
        now="2026-07-25T12:00:00+08:00",
    )
    brief["product_mode"] = "standlone"

    validation = validate_document_brief(
        brief,
        runtime_capabilities={},
        source_registry={},
        model_policy_registry={},
    )

    assert validation["valid_for_confirmation"] is False
    assert validation["release_candidate"] is False
    assert any(
        error["code"] == "product_mode_invalid"
        for error in validation["field_errors"]
    )


def test_polish_only_capability_uses_its_exact_task_mode_contract(monkeypatch):
    from api.expert_teams import document_capabilities
    from api.expert_teams.contracts import (
        build_document_brief,
        validate_document_brief,
    )

    polish_capability = _synthetic_capability(
        "material-polish",
        "other_office_material",
        task_mode="polish",
        standalone_template="standalone-material-polish",
        enterprise_template=None,
    )
    monkeypatch.setattr(
        document_capabilities,
        "_CAPABILITIES",
        {"material-polish": polish_capability},
    )

    brief = build_document_brief(
        "content-creator-team",
        {
            "contract_version": "expert-team-contract/v1",
            "product_mode": "standalone",
            "document_type": "other_office_material",
            "prompt": "在不改变事实和数据的前提下润色这份材料",
            "document_brief_seed": {
                "task_mode": "polish",
                "purpose": "提升材料的正式程度与可读性",
            },
        },
        now="2026-07-25T12:00:00+08:00",
    )
    validation = validate_document_brief(
        brief,
        runtime_capabilities={},
        source_registry={},
        model_policy_registry={},
    )

    assert brief["task_mode"] == "polish"
    assert brief["document_control"]["render_template_id"] == "standalone-material-polish"
    assert document_capabilities.brief_schema(
        "other_office_material", "polish"
    ) == polish_capability["brief_schema"]
    assert document_capabilities.source_requirement(
        "other_office_material", "polish"
    ) == polish_capability["source_requirement"]
    assert "purpose" in document_capabilities.standalone_editable_brief_paths(
        "other_office_material", "polish"
    )
    assert validation["field_errors"] == []
    assert validation["valid_for_confirmation"] is True
    assert validation["release_candidate"] is True


def test_malformed_capability_fails_closed_without_leaking_key_error(monkeypatch):
    from api.expert_teams import catalog, document_capabilities, launch_profiles
    from api.expert_teams.contracts import ContractError

    malformed = _synthetic_capability(
        "content-work-report",
        "work_report",
        standalone_template="standalone-work-report",
        enterprise_template="enterprise-work-report",
    )
    malformed.pop("capability_id")
    monkeypatch.setattr(
        document_capabilities,
        "_CAPABILITIES",
        {"content-work-report": malformed},
    )

    profile = deepcopy(launch_profiles._LAUNCH_PROFILES["content-work-report"])
    with pytest.raises(ContractError) as error:
        launch_profiles.validate_launch_profiles(
            [profile],
            product_mode="standalone",
        )

    assert error.value.code == "launch_profile_capability_mismatch"
    assert _available_profile_ids(catalog.expert_team_catalog()) == set()
