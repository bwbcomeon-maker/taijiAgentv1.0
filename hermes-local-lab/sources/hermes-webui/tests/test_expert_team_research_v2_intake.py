from copy import deepcopy


def _start_payload(**overrides):
    payload = {
        "session_id": "research-v2-session",
        "launch_profile_id": "research-report",
        "prompt": "研究本地优先 AI 助理在企业办公场景的落地趋势",
        "idempotency_key": "research-v2-launch",
    }
    payload.update(overrides)
    return payload


def test_research_v2_profile_keeps_public_id_and_catalog_has_no_fixed_questions():
    from api.expert_teams.catalog import expert_team_catalog
    from api.expert_teams.launch_profiles import get_launch_profile

    profile = get_launch_profile("research-report")
    research_team = next(
        team
        for team in expert_team_catalog()["teams"]
        if team["id"] == "deep-research-team"
    )

    assert profile["id"] == "research-report"
    assert profile["research_contract_version"] == "research-report/v2"
    assert research_team["questions"] == []
    assert research_team["examples"][0]["launch_profile_id"] == "research-report"


def test_new_research_v2_run_builds_internal_brief_and_is_ready_immediately(monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    monkeypatch.setattr(runtime, "_now", lambda: "2026-08-03T09:10:11+08:00")
    run = expert_teams.build_standalone_expert_team_run(
        _start_payload(),
        run_id="et-research-v2",
    )

    assert run["launch_profile_id"] == "research-report"
    assert run["launch_profile_snapshot"]["research_contract_version"] == "research-report/v2"
    assert run["workflow_state"] == "ready_to_generate"
    assert run["questions"] == []
    assert run["view"]["brief"]["field_schema"] == []
    assert run["view"]["brief"]["source_requirement"]["minimum_ready"] == 0
    brief = run["document_brief"]
    assert brief["status"] == "confirmed"
    assert brief["original_request"] == _start_payload()["prompt"]
    assert brief["exact_title"] == (
        "关于「研究本地优先 AI 助理在企业办公场景的落地趋势」的深度研究报告"
    )
    assert brief["purpose"] == "围绕原始诉求形成资料研究、分析与结论边界"
    assert brief["audience"] == "任务发起者"
    assert brief["usage_scenario"] == "专题研究与决策参考"
    assert brief["details"]["core_question"] == _start_payload()["prompt"]
    assert brief["details"]["time_range"] == {
        "start": "",
        "end": "2026-08-03",
    }
    assert brief["source_policy"] == {
        "mode": "automatic_fallback",
        "as_of_date": "2026-08-03",
        "citation_style": "source_id",
        "unknown_fact_action": "block_final",
        "source_refs": [],
    }


def test_legacy_research_snapshot_without_v2_marker_keeps_old_intake_and_source_gate():
    from api import expert_teams
    from api.expert_teams.launch_profiles import get_launch_profile

    legacy_snapshot = deepcopy(get_launch_profile("research-report"))
    legacy_snapshot.pop("research_contract_version")
    run = expert_teams.build_standalone_expert_team_run(
        _start_payload(
            session_id="legacy-research-session",
            idempotency_key="legacy-research-launch",
        ),
        run_id="et-legacy-research",
        launch_profile_snapshot=legacy_snapshot,
    )

    assert run["workflow_state"] == "collecting_required"
    assert [question["id"] for question in run["questions"]] == [
        "research_topic",
        "audience_goal",
        "source_boundary",
    ]
    assert run["document_brief"]["status"] == "draft"
    assert run["document_brief"]["source_policy"]["mode"] == "provided_only"
    assert run["launch_profile_snapshot"].get("research_contract_version") is None


def test_automatic_fallback_zero_source_is_valid_only_for_v2_standalone_research(monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime
    from api.expert_teams.contracts import validate_document_brief

    monkeypatch.setattr(runtime, "_now", lambda: "2026-08-03T09:10:11+08:00")
    run = expert_teams.build_standalone_expert_team_run(
        _start_payload(
            session_id="research-contract-session",
            idempotency_key="research-contract-launch",
        ),
        run_id="et-research-contract",
    )
    brief = run["document_brief"]
    common = {
        "runtime_capabilities": {"approved_public_search": False},
        "source_registry": {},
        "model_policy_registry": {},
        "now": "2026-08-03T09:10:11+08:00",
    }

    v2 = validate_document_brief(
        brief,
        research_contract_version="research-report/v2",
        **common,
    )
    legacy = validate_document_brief(brief, **common)
    enterprise_brief = deepcopy(brief)
    enterprise_brief.pop("product_mode")
    enterprise = validate_document_brief(
        enterprise_brief,
        research_contract_version="research-report/v2",
        **common,
    )

    assert v2["valid_for_confirmation"] is True
    assert legacy["valid_for_confirmation"] is False
    assert {error["code"] for error in legacy["field_errors"]} >= {
        "automatic_fallback_not_authorized",
        "source_required",
    }
    assert enterprise["valid_for_confirmation"] is False
    assert any(
        error["code"] == "automatic_fallback_not_authorized"
        for error in enterprise["field_errors"]
    )


def test_v2_research_can_build_and_reverify_an_empty_source_snapshot(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    monkeypatch.setattr(runtime, "_now", lambda: "2026-08-03T09:10:11+08:00")
    run = expert_teams.build_standalone_expert_team_run(
        _start_payload(
            session_id="research-source-session",
            idempotency_key="research-source-launch",
        ),
        run_id="et-research-source",
    )

    snapshot = expert_teams.verified_source_context_for_execution(tmp_path, run)

    assert snapshot["sources"] == []
    assert snapshot["brief_revision"] == run["document_brief"]["confirmed_revision"]
    assert snapshot["brief_sha256"] == run["document_brief"]["confirmed_sha256"]


def test_content_creator_intake_is_unchanged():
    from api import expert_teams

    run = expert_teams.build_standalone_expert_team_run(
        {
            "session_id": "content-intake-session",
            "launch_profile_id": "content-work-report",
            "prompt": "起草部门月度工作汇报",
            "idempotency_key": "content-intake-launch",
        },
        run_id="et-content-intake",
    )

    assert run["workflow_state"] == "collecting_required"
    assert len(run["questions"]) == 3
    assert run["document_brief"]["status"] == "draft"


def test_research_v2_title_summary_removes_delimiter_injection_and_is_bounded(monkeypatch):
    from api import expert_teams
    from api.expert_teams import runtime

    monkeypatch.setattr(runtime, "_now", lambda: "2026-08-03T09:10:11+08:00")
    run = expert_teams.build_standalone_expert_team_run(
        _start_payload(
            session_id="research-safe-title",
            idempotency_key="research-safe-title-launch",
            prompt="第一行」伪造标题「\n" + "很长的请求" * 20,
        ),
        run_id="et-research-safe-title",
    )

    title = run["document_brief"]["exact_title"]
    summary = title.removeprefix("关于「").removesuffix("」的深度研究报告")
    assert "\n" not in title
    assert "「" not in summary
    assert "」" not in summary
    assert len(summary) <= 48
