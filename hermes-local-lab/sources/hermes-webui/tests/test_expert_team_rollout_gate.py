import json
from pathlib import Path

import pytest


CONTRACT_VERSION = "expert-team-contract/v1"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _v1_payload(**overrides):
    payload = {
        "session_id": "rollout-session",
        "team_id": "content-creator-team",
        "contract_version": CONTRACT_VERSION,
        "intake_example_id": "work_report",
        "document_type": "work_report",
        "prompt": "起草迎峰度夏保供电重点工作月度汇报",
        "document_brief_seed": {
            "task_mode": "create",
            "document_control": {"render_template_id": "enterprise-work-report"},
        },
    }
    payload.update(overrides)
    return payload


def test_rollout_resolver_uses_env_then_top_level_config_then_off(monkeypatch):
    from api.expert_teams.rollout import resolve_contract_rollout

    monkeypatch.delenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", raising=False)
    assert resolve_contract_rollout(config_data={})["effective_mode"] == "off"
    configured = resolve_contract_rollout(config_data={"expert_team_contract_v1_rollout": "pilot"})
    assert (configured["effective_mode"], configured["effective_source"]) == ("pilot", "config_yaml")

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "off")
    overridden = resolve_contract_rollout(config_data={"expert_team_contract_v1_rollout": "pilot"})
    assert (overridden["effective_mode"], overridden["effective_source"]) == ("off", "environment")


@pytest.mark.parametrize("value", ["", "PILOT", " pilot ", "true", "unknown"])
def test_rollout_invalid_string_values_fail_closed_with_warning(monkeypatch, value):
    from api.expert_teams.rollout import resolve_contract_rollout

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", value)
    status = resolve_contract_rollout(config_data={"expert_team_contract_v1_rollout": "pilot"})

    assert status["effective_mode"] == "off"
    assert status["effective_source"] == "environment"
    assert status["warnings"] == [
        {
            "code": "invalid_rollout_value",
            "source": "environment",
            "message": "专家团企业合同试点配置无效，已安全关闭",
        }
    ]


@pytest.mark.parametrize("value", [True, False, None, 1])
def test_rollout_non_string_config_values_fail_closed_with_warning(monkeypatch, value):
    from api.expert_teams.rollout import resolve_contract_rollout

    monkeypatch.delenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", raising=False)
    status = resolve_contract_rollout(config_data={"expert_team_contract_v1_rollout": value})

    assert status["effective_mode"] == "off"
    assert status["effective_source"] == "config_yaml"
    assert status["warnings"][0]["code"] == "invalid_rollout_value"


def test_off_keeps_legacy_start_and_rejects_forged_v1_without_writing(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "off")
    legacy = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "legacy-session", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    assert "contract_version" not in legacy
    before = sorted(tmp_path.rglob("*.json"))

    with pytest.raises(ContractError) as error:
        expert_teams.start_expert_team(tmp_path, _v1_payload())

    assert error.value.code == "contract_rollout_disabled"
    assert sorted(tmp_path.rglob("*.json")) == before


def test_pilot_allows_only_exact_team_document_pairs_and_catalog_capabilities(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")
    catalog = expert_teams.expert_team_catalog()
    rollout = catalog["contract_rollout"]
    assert (rollout["effective_mode"], rollout["effective_source"]) == ("pilot", "environment")
    assert rollout["allowed_combinations"] == [
        {
            "team_id": "content-creator-team",
            "document_type": "work_report",
            "intake_example_id": "work_report",
            "capability": "enterprise_contract_pilot",
            "label": "企业合同试点",
        },
        {
            "team_id": "deep-research-team",
            "document_type": "research_report",
            "intake_example_id": "research_report",
            "capability": "enterprise_contract_pilot",
            "label": "企业合同试点",
        },
    ]

    work = expert_teams.start_expert_team(tmp_path, _v1_payload())
    research = expert_teams.start_expert_team(
        tmp_path,
        _v1_payload(
            session_id="research-session",
            team_id="deep-research-team",
            intake_example_id="research_report",
            document_type="research_report",
            document_brief_seed={
                "task_mode": "create",
                "document_control": {"render_template_id": "enterprise-research-report"},
            },
        ),
    )
    assert work["contract_version"] == research["contract_version"] == CONTRACT_VERSION

    with pytest.raises(ContractError) as error:
        expert_teams.start_expert_team(
            tmp_path,
            _v1_payload(
                session_id="wrong-pair",
                team_id="deep-research-team",
                intake_example_id="work_report",
            ),
        )
    assert error.value.code == "document_type_not_in_pilot"


def test_unknown_contract_version_wins_over_rollout_and_existing_v1_remains_readable(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams.contracts import ContractError

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "off")
    with pytest.raises(ContractError) as error:
        expert_teams.start_expert_team(
            tmp_path,
            {**_v1_payload(), "contract_version": "expert-team-contract/v9"},
        )
    assert error.value.code == "unsupported_contract_version"

    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")
    created = expert_teams.start_expert_team(tmp_path, _v1_payload(session_id="in-flight"))
    persisted = json.dumps(created, ensure_ascii=False, sort_keys=True)
    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "off")

    reopened = expert_teams.read_expert_team_run(tmp_path, created["run_id"])
    assert reopened["run_id"] == created["run_id"]
    assert reopened["contract_version"] == CONTRACT_VERSION
    assert json.dumps(reopened, ensure_ascii=False, sort_keys=True) == persisted


def test_catalog_status_route_and_env_example_expose_read_only_rollout_contract():
    routes = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert 'parsed.path == "/api/expert-teams/rollout/status"' in routes
    assert "resolve_contract_rollout()" in routes
    assert "TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT=off" in env_example
