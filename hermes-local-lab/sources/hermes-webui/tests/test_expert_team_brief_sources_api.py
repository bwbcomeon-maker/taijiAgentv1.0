from pathlib import Path

import pytest


CONTRACT_VERSION = "expert-team-contract/v1"


@pytest.fixture(autouse=True)
def _enable_contract_pilot(monkeypatch):
    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")


def _start(expert_teams, workspace: Path, *, source_refs=None):
    return expert_teams.start_expert_team(
        workspace,
        {
            "session_id": "brief-sources",
            "team_id": "content-creator-team",
            "contract_version": CONTRACT_VERSION,
            "document_type": "work_report",
            "template_id": "work_report",
            "prompt": "起草迎峰度夏月度工作汇报",
            "document_brief_seed": {
                "exact_title": "迎峰度夏月度工作汇报",
                "purpose": "向分管领导汇报进展",
                "audience": "公司分管领导",
                "usage_scenario": "月度例会",
                "source_policy": {
                    "mode": "provided_only",
                    "citation_style": "source_id",
                    "unknown_fact_action": "allow_labeled_placeholder",
                    "source_refs": list(source_refs or []),
                },
                "data_handling": {
                    "model_policy_id": "enterprise-local-default",
                    "requires_zero_retention": True,
                },
                "document_control": {
                    "classification": "internal",
                    "render_template_id": "enterprise-work-report",
                },
                "content_constraints": {
                    "required_sections": ["工作开展情况", "存在问题", "下一步工作安排"],
                    "must_include": [],
                    "must_avoid": [],
                },
                "details": {"reporting_period": "2026年7月", "reporting_unit": "综合部"},
                "approval": {"human_final_review_required": True, "approver_roles": ["部门负责人"]},
            },
        },
    )


def _control(run, key):
    return {
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "expected_version": run["version"],
        "expected_brief_revision": run["document_brief"]["revision"],
        "idempotency_key": key,
    }


def test_seeded_source_context_still_normalizes_and_confirms(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    policies = {
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
    }
    monkeypatch.setattr(runtime, "load_model_policy_registry", lambda: policies)
    run = _start(
        expert_teams,
        tmp_path,
        source_refs=[{
            "source_id": "SRC-TEXT",
            "kind": "provided_text",
            "label": "系统已绑定说明",
            "text": "已完成三项重点任务，剩余两项按计划推进。",
        }],
    )

    confirmed = expert_teams.confirm_expert_team_document_brief(tmp_path, _control(run, "confirm-seeded-source"))
    ref = confirmed["document_brief"]["source_policy"]["source_refs"][0]
    assert ref["kind"] == "provided_text"
    assert "text" not in ref
    assert ref["locator"].startswith(".taiji/expert-teams/sources/")


def test_public_source_mutation_routes_runtime_functions_and_exports_are_removed():
    root = Path(__file__).resolve().parents[1]
    routes = (root / "api" / "routes.py").read_text(encoding="utf-8")
    runtime = (root / "api" / "expert_teams" / "runtime.py").read_text(encoding="utf-8")
    exports = (root / "api" / "expert_teams" / "__init__.py").read_text(encoding="utf-8")

    for marker in (
        "/api/expert-teams/brief/sources/add",
        "/api/expert-teams/brief/sources/remove",
    ):
        assert marker not in routes
    for marker in (
        "def add_expert_team_brief_source",
        "def remove_expert_team_brief_source",
    ):
        assert marker not in runtime
    for marker in (
        "add_expert_team_brief_source",
        "remove_expert_team_brief_source",
    ):
        assert marker not in exports
