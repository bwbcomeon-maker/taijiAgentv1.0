import hashlib
import json

import pytest


CONTRACT_VERSION = "expert-team-contract/v1"
CANARY = "CANARY-NEGATION-7f9c 不要写成公众号文章"


def _payload(**overrides):
    payload = {
        "contract_version": CONTRACT_VERSION,
        "team_id": "content-creator-team",
        "document_type": "work_report",
        "template_id": "work_report",
        "prompt": f"起草工作汇报，{CANARY}",
        "document_brief_seed": {
            "exact_title": "迎峰度夏保供电重点工作月度汇报",
            "purpose": "向分管领导汇报进展",
            "audience": "公司分管领导",
            "usage_scenario": "月度例会",
            "source_policy": {
                "mode": "provided_only",
                "as_of_date": "2026-07-15",
                "citation_style": "source_id",
                "unknown_fact_action": "block_final",
                "source_refs": [{"source_id": "SRC-001", "kind": "attachment", "label": "月度数据"}],
            },
            "data_handling": {
                "model_policy_id": "enterprise-local-default",
                "requires_zero_retention": True,
            },
            "document_control": {
                "issuer": "某某部门",
                "compiler": "某某部门",
                "version_label": "V1.0",
                "classification": "internal",
                "classification_label": "内部资料",
                "document_date": "2026-07-15",
                "render_template_id": "enterprise-work-report",
            },
            "content_constraints": {
                "required_sections": ["工作开展情况", "存在问题", "下一步工作安排"],
                "must_include": [],
                "must_avoid": [],
                "target_length_chars": {"min": 1500, "max": 3000},
                "tone": "正式、克制",
            },
            "details": {"reporting_period": "2026年7月", "reporting_unit": "某某部门"},
            "approval": {"human_final_review_required": True, "approver_roles": ["部门负责人"]},
        },
    }
    payload.update(overrides)
    return payload


def _registries():
    return (
        {"approved_public_search": False},
        {"SRC-001": {"status": "ready", "sha256": "a" * 64, "kind": "attachment"}},
        {
            "enterprise-local-default": {
                "label": "企业本地模型",
                "allowed_classifications": ["public", "internal", "restricted"],
                "provider_ids": ["local-enterprise-model"],
                "deployment_ids": ["taiji-onprem-01"],
                "trust_zones": ["local"],
                "retention_modes": ["zero_retention"],
                "training_opt_out_required": True,
                "allowed_source_kinds": ["attachment", "local_file", "provided_text"],
                "expires_at": "2027-07-15T00:00:00+08:00",
                "approval_ref": "security-policy-2026-01",
            }
        },
    )


def test_contract_version_distinguishes_missing_from_invalid_values():
    from api.expert_teams.contracts import ContractError, classify_contract_version

    assert classify_contract_version({}) == "legacy"
    assert classify_contract_version({"contract_version": CONTRACT_VERSION}) == CONTRACT_VERSION
    for invalid in (None, "", "expert-team-contract/v2", "expert-team-contract/V1"):
        with pytest.raises(ContractError) as error:
            classify_contract_version({"contract_version": invalid})
        assert error.value.code == "unsupported_contract_version"
        assert error.value.field == "contract_version"


def test_build_brief_separates_intake_document_and_render_template_ids():
    from api.expert_teams.contracts import build_document_brief

    brief = build_document_brief("content-creator-team", _payload(), now="2026-07-15T10:00:00+08:00")

    assert brief["document_type"] == "work_report"
    assert brief["intake_example_id"] == "work_report"
    assert brief["document_control"]["render_template_id"] == "enterprise-work-report"
    assert brief["original_request"] == f"起草工作汇报，{CANARY}"
    assert brief["document_type"] != "public_account"


def test_build_brief_rejects_missing_intent_empty_prompt_and_template_mismatch():
    from api.expert_teams.contracts import ContractError, build_document_brief

    cases = [
        (_payload(document_type=None), "document_type_required"),
        (_payload(prompt="  \n"), "original_request_required"),
        (
            _payload(
                document_brief_seed={
                    **_payload()["document_brief_seed"],
                    "document_control": {
                        **_payload()["document_brief_seed"]["document_control"],
                        "render_template_id": "enterprise-research-report",
                    },
                }
            ),
            "render_template_mismatch",
        ),
    ]
    for payload, code in cases:
        with pytest.raises(ContractError) as error:
            build_document_brief("content-creator-team", payload, now="2026-07-15T10:00:00+08:00")
        assert error.value.code == code


def test_digest_is_stable_and_excludes_lifecycle_fields_but_includes_canary():
    from api.expert_teams.contracts import brief_digest, build_document_brief, confirm_document_brief

    brief = build_document_brief("content-creator-team", _payload(), now="2026-07-15T10:00:00+08:00")
    before = brief_digest(brief)
    confirmed = confirm_document_brief(brief, now="2026-07-15T10:30:00+08:00")

    assert confirmed["confirmed_sha256"] == before
    assert brief_digest(confirmed) == before
    canonical = json.dumps(
        {k: v for k, v in brief.items() if k not in {"revision", "status", "confirmed_revision", "confirmed_at", "confirmed_sha256"}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert before == hashlib.sha256(canonical).hexdigest()
    assert CANARY in confirmed["original_request"]


def test_validate_work_report_uses_server_source_and_model_policy_registries():
    from api.expert_teams.contracts import build_document_brief, validate_document_brief

    runtime, sources, policies = _registries()
    brief = build_document_brief("content-creator-team", _payload(), now="2026-07-15T10:00:00+08:00")
    validation = validate_document_brief(
        brief,
        runtime_capabilities=runtime,
        source_registry=sources,
        model_policy_registry=policies,
        now="2026-07-15T10:00:00+08:00",
    )

    assert validation["valid_for_confirmation"] is True
    assert validation["field_errors"] == []
    assert validation["release_candidate"] is True
    assert validation["enterprise_released"] is False


def test_validate_rejects_forged_or_missing_sources_and_unauthorized_egress():
    from api.expert_teams.contracts import build_document_brief, validate_document_brief

    runtime, _, policies = _registries()
    brief = build_document_brief("content-creator-team", _payload(), now="2026-07-15T10:00:00+08:00")
    brief["source_policy"]["source_refs"][0]["sha256"] = "forged"
    validation = validate_document_brief(
        brief,
        runtime_capabilities=runtime,
        source_registry={},
        model_policy_registry=policies,
        now="2026-07-15T10:00:00+08:00",
    )
    assert {item["code"] for item in validation["field_errors"]} >= {"source_unresolved"}

    validation = validate_document_brief(
        brief,
        runtime_capabilities=runtime,
        source_registry={"SRC-001": {"status": "ready", "sha256": "a" * 64, "kind": "attachment"}},
        model_policy_registry={},
        now="2026-07-15T10:00:00+08:00",
    )
    assert {item["code"] for item in validation["field_errors"]} >= {"data_egress_not_authorized"}


def test_research_report_requires_citations():
    from api.expert_teams.contracts import build_document_brief, validate_document_brief

    runtime, sources, policies = _registries()
    seed = _payload()["document_brief_seed"]
    seed = {
        **seed,
        "source_policy": {**seed["source_policy"], "citation_style": "none"},
        "document_control": {**seed["document_control"], "render_template_id": "enterprise-research-report"},
        "details": {"core_question": "AI 办公如何落地", "time_range": {"start": "2025-01-01", "end": "2026-07-15"}},
    }
    brief = build_document_brief(
        "deep-research-team",
        _payload(team_id="deep-research-team", document_type="research_report", document_brief_seed=seed),
        now="2026-07-15T10:00:00+08:00",
    )
    validation = validate_document_brief(
        brief,
        runtime_capabilities=runtime,
        source_registry=sources,
        model_policy_registry=policies,
        now="2026-07-15T10:00:00+08:00",
    )
    assert "citation_style_required" in {item["code"] for item in validation["field_errors"]}


def test_patch_checks_revision_and_freezes_after_stage_start():
    from api.expert_teams.contracts import ContractError, build_document_brief, patch_document_brief

    brief = build_document_brief("content-creator-team", _payload(), now="2026-07-15T10:00:00+08:00")
    updated = patch_document_brief(brief, {"exact_title": "更新后的精确标题"}, expected_revision=1, stage_started=False)
    assert updated["revision"] == 2
    assert updated["exact_title"] == "更新后的精确标题"
    with pytest.raises(ContractError) as conflict:
        patch_document_brief(updated, {"purpose": "新目的"}, expected_revision=1, stage_started=False)
    assert conflict.value.code == "brief_revision_conflict"
    with pytest.raises(ContractError) as frozen:
        patch_document_brief(updated, {"purpose": "新目的"}, expected_revision=2, stage_started=True)
    assert frozen.value.code == "brief_frozen_new_run_required"


def test_legacy_run_read_does_not_add_contract_fields_or_write_disk(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import run_path

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "legacy-session", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    path = run_path(tmp_path, run["run_id"])
    before = path.read_bytes()
    opened = expert_teams.read_expert_team_run(tmp_path, run["run_id"])

    assert "contract_version" not in opened
    assert "document_brief" not in opened
    assert path.read_bytes() == before


def test_contract_run_persists_draft_brief_without_starting_generation(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(tmp_path, {"session_id": "contract-session", **_payload()})

    assert run["schema_version"] == 2
    assert run["contract_version"] == CONTRACT_VERSION
    assert run["document_brief"]["status"] == "draft"
    assert run["document_brief"]["original_request"].endswith(CANARY)
    assert run["stage_artifacts"] == []
    assert run["canonical_document_ref"] is None
    assert run["workflow_state"] == "collecting_required"
    assert run["view"]["brief"]["document_type"] == "work_report"
    assert run["view"]["phase_progress"]["done"] == 0
    assert run["view"]["phase_progress"]["total"] == 5
    assert run["view"]["phase_progress"]["is_intake"] is True


def test_catalog_examples_expose_explicit_intake_and_document_semantics():
    from api import expert_teams

    catalog = expert_teams.expert_team_catalog()
    examples = {
        example["intake_example_id"]: example
        for team in catalog["teams"]
        for example in team["examples"]
    }
    assert examples["work_report"]["document_type"] == "work_report"
    assert examples["work_report"]["task_mode"] == "create"
    assert examples["work_report"]["document_brief_seed"]["document_control"]["render_template_id"] == "enterprise-work-report"
    assert examples["research_report"]["document_type"] == "research_report"
    assert examples["polish"]["task_mode"] == "polish"


def test_unknown_persisted_contract_version_fails_closed_without_rewrite(tmp_path):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError
    from api.expert_teams.storage import run_path, write_run

    persisted = {
        "schema_version": 2,
        "version": 1,
        "run_id": "et-unknown-contract",
        "session_id": "unknown-contract-session",
        "contract_version": "expert-team-contract/v9",
        "workflow_state": "collecting_required",
    }
    write_run(tmp_path, persisted)
    path = run_path(tmp_path, persisted["run_id"])
    before = path.read_bytes()

    with pytest.raises(ContractError) as error:
        expert_teams.read_expert_team_run(tmp_path, persisted["run_id"])

    assert error.value.code == "unsupported_contract_version"
    assert path.read_bytes() == before


@pytest.mark.parametrize("contract_version", [None, "", "expert-team-contract/v2", "expert-team-contract/V1"])
def test_explicit_invalid_contract_version_fails_closed_without_creating_run(tmp_path, contract_version):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    with pytest.raises(ContractError) as error:
        expert_teams.start_expert_team(
            tmp_path,
            {"session_id": "invalid-contract", **_payload(), "contract_version": contract_version},
        )

    assert error.value.code == "unsupported_contract_version"
    assert list(tmp_path.rglob("et-*.json")) == []
