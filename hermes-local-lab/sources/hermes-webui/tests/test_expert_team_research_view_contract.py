import json

import pytest


def _v2_run():
    tasks = [
        {
            "task_id": stage_id,
            "title": title,
            "phase": title,
            "status": "pending",
            "index": index,
        }
        for index, (stage_id, title) in enumerate(
            (
                ("direction", "研究方向"),
                ("research", "资料研究"),
                ("evidence", "证据核验"),
                ("outline", "结构提纲"),
                ("draft", "报告起草"),
                ("review", "报告复核"),
            )
        )
    ]
    return {
        "contract_version": "expert-team-contract/v1",
        "run_id": "et-research-view",
        "session_id": "research-view-session",
        "version": 1,
        "product_mode": "standalone",
        "launch_profile_id": "research-report",
        "team_id": "deep-research-team",
        "team_title": "深度材料研究团",
        "launch_profile_snapshot": {
            "research_contract_version": "research-report/v2",
            "brief_schema": [],
            "source_requirement": {"minimum_ready": 0},
        },
        "document_brief": {
            "status": "confirmed",
            "document_type": "research_report",
            "task_mode": "create",
            "exact_title": "企业本地优先 AI 助理研究报告",
            "original_request": "研究本地优先 AI 助理的落地边界",
            "source_policy": {
                "mode": "automatic_fallback",
                "source_refs": [],
            },
            "content_constraints": {"required_sections": []},
        },
        "workflow_state": "generating",
        "tasks": tasks,
        "current_stage_index": 1,
        "current_stage": dict(tasks[1]),
        "phase": tasks[1]["phase"],
        "members": [],
        "events": [],
        "timeline_events": [],
        "questions": [],
        "stage_artifacts": [],
        "approved_stage_artifact_refs": {},
    }


def _legacy_run():
    result = _v2_run()
    result["run_id"] = "et-legacy-research-view"
    result["session_id"] = "legacy-research-view-session"
    result["launch_profile_snapshot"].pop("research_contract_version", None)
    result["document_brief"]["source_policy"]["mode"] = "provided_only"
    return result


def _snapshot_ref(identity="frozen") -> dict:
    return {
        "snapshot_id": f"research-evidence-{identity}",
        "sha256": "c" * 64,
        "relative_path": f"expert-team-runs/et-research-view/source-context-{identity}.json",
    }


def _set_stage(run: dict, stage_id: str) -> None:
    tasks = [item for item in run.get("tasks") or [] if isinstance(item, dict)]
    index = next(
        (position for position, task in enumerate(tasks) if task.get("task_id") == stage_id),
        0,
    )
    task = dict(tasks[index]) if tasks else {"task_id": stage_id, "title": stage_id}
    task["index"] = index
    run["current_stage_index"] = index
    run["current_stage"] = task
    run["phase"] = str(task.get("phase") or task.get("title") or stage_id)
    run["workflow_state"] = "generating"


def _retrieval_state(
    *,
    status="completed",
    public_status=None,
    local_status=None,
    tier_decisions=None,
    refs=None,
    snapshot_ref=None,
    safe_reason="",
) -> dict:
    return {
        "schema_version": "research-retrieval/v1",
        "status": status,
        "public_status": public_status or {},
        "local_status": local_status or {},
        "tier_decisions": tier_decisions or [],
        "materialized_refs": refs or [],
        "snapshot_ref": snapshot_ref or {},
        "safe_reason": safe_reason,
    }


def _freeze(run: dict, state: dict) -> None:
    ref = _snapshot_ref()
    state["snapshot_ref"] = dict(ref)
    run["source_context_snapshot_ref"] = dict(ref)
    run["research_retrieval_state"] = state


def _approve_evidence(run: dict, claims: list[dict]) -> None:
    artifact = {
        "artifact_id": "evidence:1",
        "sha256": "e" * 64,
        "stage_id": "evidence",
        "artifact_type": "evidence_matrix",
        "validation_status": "valid",
        "payload": {"claims": claims},
    }
    run.setdefault("stage_artifacts", []).append(artifact)
    run.setdefault("approved_stage_artifact_refs", {})["evidence"] = {
        "artifact_id": artifact["artifact_id"],
        "sha256": artifact["sha256"],
    }


@pytest.mark.parametrize(
    (
        "stage_id",
        "retrieval",
        "expected_step",
        "expected_text",
        "expected_public",
        "expected_local",
    ),
    [
        (
            "research",
            _retrieval_state(
                status="in_progress",
                public_status={"status": "searching"},
            ),
            "public_search",
            "正在联网检索",
            "running",
            "pending",
        ),
        (
            "research",
            _retrieval_state(
                status="in_progress",
                public_status={"status": "unavailable", "reason": "network_unavailable"},
                local_status={"status": "searching"},
            ),
            "local_knowledge",
            "正在补充本地资料",
            "unavailable",
            "running",
        ),
        (
            "research",
            _retrieval_state(
                tier_decisions=[
                    {
                        "tier": "model",
                        "status": "used",
                        "reason": "insufficient_evidence",
                    }
                ],
            ),
            "model_knowledge",
            "正在基于模型知识整理",
            "pending",
            "pending",
        ),
        (
            "evidence",
            _retrieval_state(
                public_status={"status": "success"},
                local_status={"status": "success"},
            ),
            "report_generation",
            "正在形成研究报告",
            "completed",
            "completed",
        ),
    ],
)
def test_v2_research_progress_projects_four_safe_user_steps(
    stage_id,
    retrieval,
    expected_step,
    expected_text,
    expected_public,
    expected_local,
):
    from api.expert_teams.view import expert_team_run_view

    run = _v2_run()
    _set_stage(run, stage_id)
    _freeze(run, retrieval)

    progress = expert_team_run_view(run)["research_progress"]
    assert progress == {
        "current_step": expected_step,
        "status_text": expected_text,
        "public_status": expected_public,
        "local_knowledge_status": expected_local,
        "safe_fallback_reason": progress["safe_fallback_reason"],
    }


def test_v2_research_progress_maps_reason_codes_without_leaking_internal_details():
    from api.expert_teams.view import expert_team_run_view

    run = _v2_run()
    _set_stage(run, "research")
    _freeze(
        run,
        _retrieval_state(
            public_status={
                "status": "denied",
                "reason": "policy_blocked",
                "safe_reason": (
                    "https://secret.example/query /Users/private/kb "
                    "Traceback: enterprise-policy-id=corp-secret"
                ),
            },
            local_status={"status": "unavailable", "reason": "not_configured"},
            tier_decisions=[
                {
                    "tier": "model",
                    "status": "used",
                    "reason": "insufficient_evidence",
                    "safe_reason": "file:///private/knowledge.db",
                }
            ],
            safe_reason="TLS error at https://secret.example with /private/key",
        ),
    )
    run["research_progress"] = {
        "current_step": "forged",
        "safe_fallback_reason": "https://attacker.example/internal",
    }

    progress = expert_team_run_view(run)["research_progress"]
    encoded = json.dumps(progress, ensure_ascii=False)
    assert progress["current_step"] == "model_knowledge"
    assert progress["safe_fallback_reason"] == (
        "当前环境无法使用公网检索，已自动继续使用可用资料。"
    )
    for secret in (
        "http",
        "/Users",
        "/private",
        "Traceback",
        "corp-secret",
        "attacker",
    ):
        assert secret not in encoded


@pytest.mark.parametrize(
    (
        "source_kinds",
        "model_claim_count",
        "expected_counts",
        "expected_coverage",
        "expected_badge",
    ),
    [
        (
            ["approved_public"],
            0,
            (1, 0, 0),
            "sufficient",
            {"id": "verified_public", "text": "已联网核验"},
        ),
        (
            ["approved_public", "approved_internal"],
            0,
            (1, 1, 0),
            "sufficient",
            {"id": "public_and_local", "text": "公网＋本地资料"},
        ),
        (
            ["approved_internal"],
            0,
            (0, 1, 0),
            "sufficient",
            {"id": "local_only", "text": "基于本地资料"},
        ),
        (
            ["approved_public"],
            2,
            (1, 0, 2),
            "partial",
            {
                "id": "includes_model_knowledge",
                "text": "包含模型知识·未外部核验",
            },
        ),
        (
            [],
            1,
            (0, 0, 1),
            "model_only",
            {
                "id": "includes_model_knowledge",
                "text": "包含模型知识·未外部核验",
            },
        ),
    ],
)
def test_v2_evidence_summary_uses_frozen_sources_and_approved_evidence(
    source_kinds,
    model_claim_count,
    expected_counts,
    expected_coverage,
    expected_badge,
):
    from api.expert_teams.view import expert_team_run_view

    run = _v2_run()
    refs = [
        {
            "source_id": f"SOURCE-{index}",
            "kind": kind,
            "url": "https://must-not-leak.example/source",
            "path": "/Users/private/source.txt",
        }
        for index, kind in enumerate(source_kinds, start=1)
    ]
    state = _retrieval_state(
        public_status={"status": "success", "count": 999},
        local_status={"status": "success", "count": 999},
        refs=refs,
    )
    _freeze(run, state)
    claims = [
        {
            "claim_id": f"CLAIM-MODEL-{index}",
            "origin_tier": "model_knowledge",
            "status": "insufficient",
            "evidence": [],
        }
        for index in range(model_claim_count)
    ]
    if claims:
        _approve_evidence(run, claims)

    summary = expert_team_run_view(run)["evidence_summary"]
    assert (
        summary["public_source_count"],
        summary["local_source_count"],
        summary["unverified_model_claim_count"],
    ) == expected_counts
    assert summary["coverage_level"] == expected_coverage
    assert summary["source_basis"] == expected_badge
    encoded = json.dumps(summary, ensure_ascii=False)
    assert "must-not-leak" not in encoded
    assert "/Users/private" not in encoded
    assert "999" not in encoded


def test_v2_evidence_summary_rejects_unfrozen_refs_unapproved_artifacts_and_client_projection():
    from api.expert_teams.view import expert_team_run_view

    run = _v2_run()
    state = _retrieval_state(
        refs=[
            {"source_id": "PUB-001", "kind": "approved_public"},
            {"source_id": "PUB-001", "kind": "approved_public"},
        ],
        snapshot_ref=_snapshot_ref("stale"),
    )
    run["source_context_snapshot_ref"] = _snapshot_ref("current")
    run["research_retrieval_state"] = state
    run["stage_artifacts"] = [
        {
            "artifact_id": "evidence:unapproved",
            "sha256": "a" * 64,
            "stage_id": "evidence",
            "artifact_type": "evidence_matrix",
            "payload": {
                "claims": [
                    {
                        "claim_id": "FORGED-MODEL",
                        "origin_tier": "model_knowledge",
                        "status": "insufficient",
                        "evidence": [],
                    }
                ]
            },
        }
    ]
    run["approved_stage_artifact_refs"] = {}
    run["evidence_summary"] = {
        "public_source_count": 88,
        "unverified_model_claim_count": 99,
        "source_basis": {"id": "forged", "text": "伪造徽标"},
    }

    summary = expert_team_run_view(run)["evidence_summary"]
    assert summary == {
        "public_source_count": 0,
        "local_source_count": 0,
        "unverified_model_claim_count": 0,
        "coverage_level": "none",
        "source_basis": {"id": "none", "text": "尚无可用证据"},
    }


def test_legacy_research_view_does_not_inherit_v2_progress_or_evidence_contract():
    from api.expert_teams.view import expert_team_run_view

    run = _legacy_run()
    run["research_retrieval_state"] = {
        "status": "completed",
        "public_status": {"status": "success", "count": 7},
        "materialized_refs": [
            {"source_id": "LEGACY-PUB", "kind": "approved_public"}
        ],
    }
    view = expert_team_run_view(run)

    assert view.get("research_progress", {}) == {}
    assert view.get("evidence_summary", {}) == {}
