from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("internal_code", "http_status", "product_code"),
    [
        ("stage_attempt_in_progress", 409, "expert_team_in_progress"),
        ("stale_state", 409, "expert_team_state_conflict"),
        ("stage_artifact_blocked", 409, "expert_team_content_blocked"),
        ("runtime_protocol_error", 400, "model_output_invalid"),
        ("delivery_semantic_blocked", 503, "expert_team_content_blocked"),
        ("validation_failed", 503, "document_render_failed"),
        ("delivery_generation_failed", 500, "document_render_failed"),
        ("delivery_open_target_changed", 409, "document_open_failed"),
        ("delivery_copy_conflict", 400, "delivery_copy_failed"),
        ("source_context_invalid", 409, "expert_team_source_invalid"),
        ("provider_request_failed", 401, "provider_authorization_failed"),
        ("provider_request_failed", 429, "provider_rate_limited"),
        ("provider_request_failed", 504, "provider_timeout"),
    ],
)
def test_expert_team_internal_errors_map_to_one_public_catalog(
    internal_code,
    http_status,
    product_code,
):
    from api.expert_teams.error_projection import expert_team_product_error_code

    assert expert_team_product_error_code(internal_code, http_status=http_status) == product_code


def test_expert_team_conflict_response_never_exposes_internal_english(monkeypatch):
    from api import routes

    class Handler:
        def __init__(self):
            self.status = None
            self.body = bytearray()
            self.wfile = self

        def send_response(self, status):
            self.status = status

        def send_header(self, _name, _value):
            pass

        def end_headers(self):
            pass

        def write(self, data):
            self.body.extend(data)

    conflict = type(
        "Conflict",
        (Exception,),
        {"code": "stage_attempt_in_progress", "run": {"run_id": "run-1"}},
    )("another stage attempt is already authoritative for this stage")
    handler = Handler()

    routes._expert_team_conflict_response(handler, conflict)
    payload = json.loads(bytes(handler.body).decode("utf-8"))

    assert handler.status == 409
    assert payload["code"] == "stage_attempt_in_progress"
    assert payload["product_error"]["code"] == "expert_team_in_progress"
    assert payload["product_error"]["incident_id"].startswith("inc-")
    assert payload["error"] == payload["product_error"]["message"]
    assert "another stage" not in json.dumps(payload, ensure_ascii=False)


def test_public_run_projection_replaces_internal_runtime_error_text():
    from api.brand_privacy import public_expert_team_run_projection

    raw_error = "runtime observation cursor exceeded the durable metadata limit"
    projected = public_expert_team_run_projection(
        {
            "run_id": "run-1",
            "workflow_state": "generated_invalid",
            "last_execution_error": raw_error,
            "last_validation_error": raw_error,
            "execution_cleanup_error": raw_error,
            "last_execution_error_code": "runtime_observation_limit",
            "view": {
                "product_error": {
                    "schema": "taiji.product.error.v1",
                    "code": "model_output_invalid",
                    "message": "模型返回内容格式不完整；已有输入和阶段记录已保留，请明确重试当前阶段。",
                }
            },
        }
    )

    serialized = json.dumps(projected, ensure_ascii=False)
    assert raw_error not in serialized
    assert projected["last_execution_error"] == projected["view"]["product_error"]["message"]
    assert projected["last_validation_error"] == projected["view"]["product_error"]["message"]
    assert "execution_cleanup_error" not in projected


def test_system_delivery_route_preserves_semantic_blocking_category(monkeypatch, tmp_path):
    from api import expert_teams, routes
    from api.expert_teams import system_stages

    run = {
        "run_id": "run-1",
        "pending_system_stage": {"id": "delivery", "executor": "system"},
        "canonical_document_ref": {"sha256": "a" * 64},
    }
    reservation = {"reservation_id": "reservation-1"}
    current = {
        **run,
        "workflow_state": "generated_invalid",
        "last_execution_error_code": "delivery_semantic_blocked",
    }

    monkeypatch.setattr(
        expert_teams,
        "reserve_system_stage_attempt",
        lambda *_args, **_kwargs: (run, run["pending_system_stage"], reservation, True),
    )
    monkeypatch.setattr(
        system_stages,
        "dispatch_system_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            system_stages.SystemStageError(
                "delivery_semantic_blocked",
                "raw semantic gate detail must remain private",
            )
        ),
    )
    monkeypatch.setattr(
        expert_teams,
        "fail_system_stage_attempt",
        lambda *_args, **_kwargs: current,
    )

    payload, status = routes._dispatch_expert_team_system_stage(tmp_path, run)

    assert status == 503
    assert payload["code"] == "delivery_semantic_blocked"
    assert payload["product_error"]["code"] == "expert_team_content_blocked"
    assert payload["error"] == payload["product_error"]["message"]
    assert "raw semantic" not in json.dumps(payload, ensure_ascii=False)


def test_expert_team_view_projects_safe_error_and_preserves_retry_state(monkeypatch):
    from api.expert_teams.view import expert_team_run_view

    monkeypatch.setenv("TAIJI_SOURCE_COMMIT", "7a6746baf5da")
    monkeypatch.setenv("TAIJI_SOURCE_MODE", "development-linked-worktree")

    run = {
        "schema_version": 3,
        "contract_version": "expert-team-contract/v1",
        "product_mode": "standalone",
        "run_id": "run-1",
        "session_id": "session-1",
        "team_id": "content-creator-team",
        "workflow_state": "generation_failed",
        "version": 3,
        "current_stage_index": 0,
        "current_stage": {"task_id": "flow-design"},
        "current_stage_attempt_reservation": {"stage_attempt": 2},
        "tasks": [],
        "questions": [],
        "answers": [],
        "events": [],
        "stage_outputs": [],
        "stage_results": [],
        "review_items": [],
        "last_execution_error": "raw internal provider stack must not render",
        "last_execution_error_code": "runtime_protocol_error",
        "last_execution_incident_id": "inc-0123456789ab",
        "document_brief": {},
    }

    view = expert_team_run_view(run)

    assert view["product_error"]["code"] == "model_output_invalid"
    assert view["product_error"]["incident_id"] == "inc-0123456789ab"
    assert view["presentation"]["detail"] == view["product_error"]["message"]
    assert "raw internal" not in json.dumps(view, ensure_ascii=False)
    assert "resume" in view["allowed_actions"]
    assert view["diagnostics"] == {
        "schema": "expert-team-diagnostics/v1",
        "commit": "7a6746baf5da",
        "source_mode": "development-linked-worktree",
        "run_id": "run-1",
        "stage_id": "flow-design",
        "stage_attempt": 2,
        "error_code": "model_output_invalid",
        "incident_id": "inc-0123456789ab",
        "blocking_count": 0,
        "warning_count": 0,
        "provider_error_category": "",
        "delivery_state": "content_required",
    }


def test_expert_team_product_catalog_explains_preservation_and_next_action():
    from api.product_contract import build_product_error

    for code in (
        "provider_authorization_failed",
        "provider_rate_limited",
        "provider_timeout",
        "model_output_invalid",
        "expert_team_content_blocked",
        "expert_team_evidence_required",
        "expert_team_state_conflict",
        "expert_team_in_progress",
        "document_render_failed",
        "document_open_failed",
        "delivery_copy_failed",
        "expert_team_not_found",
        "expert_team_source_invalid",
    ):
        envelope = build_product_error(code, incident_id="inc-0123456789ab")
        assert envelope["code"] == code
        assert "已保留" in envelope["message"] or "不会丢失" in envelope["message"]
        assert envelope["recovery_actions"]
        assert envelope["incident_id"] == "inc-0123456789ab"


def test_generated_invalid_evidence_block_requires_new_sources_not_blind_retry():
    from api.expert_teams.view import expert_team_run_view

    artifact = {
        "artifact_id": "research:1",
        "sha256": "a" * 64,
        "stage_id": "research",
        "stage_attempt": 1,
        "artifact_type": "source_register",
        "validation_status": "valid",
        "summary": "现有资料不足以支撑研究结论。",
        "blocking_issues": [
            {
                "severity": "blocking",
                "category": "evidence",
                "message": "缺少复核流程依据。",
                "suggested_action": "补充复核流程资料后重新发起。",
            }
        ],
    }
    run = {
        "schema_version": 3,
        "contract_version": "expert-team-contract/v1",
        "product_mode": "standalone",
        "run_id": "run-evidence",
        "session_id": "session-evidence",
        "team_id": "deep-research-team",
        "workflow_state": "generated_invalid",
        "version": 5,
        "current_stage_index": 1,
        "current_stage": {"task_id": "research"},
        "current_stage_attempt_reservation": {
            "stage_id": "research",
            "stage_attempt": 1,
            "status": "generated_invalid",
        },
        "current_stage_artifact_ref": {
            "artifact_id": artifact["artifact_id"],
            "sha256": artifact["sha256"],
            "stage_attempt": 1,
        },
        "stage_artifacts": [artifact],
        "stage_outputs": [],
        "stage_results": [],
        "tasks": [{"task_id": "direction"}, {"task_id": "research"}],
        "questions": [],
        "answers": [],
        "events": [],
        "review_items": [],
        "validation": {"status": "rewrite_required", "blocking_count": 1},
        "document_brief": {
            "status": "confirmed",
            "revision": 1,
            "document_type": "research_report",
            "task_mode": "research",
            "original_request": "请基于所附资料形成研究报告。",
            "source_policy": {"source_refs": [{"source_id": "SRC-1"}]},
        },
    }

    view = expert_team_run_view(run)

    assert view["product_error"]["code"] == "expert_team_evidence_required"
    assert [action["id"] for action in view["product_error"]["recovery_actions"]] == [
        "open_result",
        "start_new",
        "export_diagnostics",
    ]
    assert "直接重试不会增加资料" in view["product_error"]["message"]
    assert "resume" in view["allowed_actions"]


def test_expert_team_post_routes_have_no_legacy_raw_exception_responses():
    from api import routes

    source = Path(routes.__file__).read_text(encoding="utf-8")
    start = source.index('if parsed.path == "/api/expert-teams/start":')
    end = source.index('if parsed.path == "/api/writeflow/compose":', start)
    expert_team_routes = source[start:end]

    assert "return bad(handler" not in expert_team_routes
    assert "_sanitize_error(exc)" not in expert_team_routes
    for legacy_message in (
        "expert team run not found",
        "expert team run does not belong to this session",
        "Failed to update expert team",
        "Failed to resume expert team",
    ):
        assert legacy_message not in expert_team_routes
