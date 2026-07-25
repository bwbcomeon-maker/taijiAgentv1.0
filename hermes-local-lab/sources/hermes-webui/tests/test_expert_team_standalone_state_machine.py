from __future__ import annotations

import io
import json
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import threading
from urllib.parse import urlparse

import pytest


class _RouteHandler:
    def __init__(self, payload: dict | None = None):
        raw = json.dumps(payload or {}).encode("utf-8")
        self.status = None
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = self
        self.body = bytearray()

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json_body(self) -> dict:
        return json.loads(bytes(self.body).decode("utf-8"))


def _post(routes, path: str, body: dict) -> _RouteHandler:
    handler = _RouteHandler(body)
    routes.handle_post(handler, urlparse(path))
    return handler


@pytest.fixture
def standalone_env(monkeypatch, tmp_path):
    import api.config as config
    import api.models as models
    import api.routes as routes
    import api.state_sync as state_sync
    from hermes_state import SessionDB

    state_dir = tmp_path / "state"
    session_dir = state_dir / "sessions"
    session_dir.mkdir(parents=True)
    sessions = OrderedDict()
    for module in (config, models, routes):
        if hasattr(module, "STATE_DIR"):
            monkeypatch.setattr(module, "STATE_DIR", state_dir)
        if hasattr(module, "SESSION_DIR"):
            monkeypatch.setattr(module, "SESSION_DIR", session_dir)
        if hasattr(module, "SESSION_INDEX_FILE"):
            monkeypatch.setattr(module, "SESSION_INDEX_FILE", session_dir / "_index.json")
        if hasattr(module, "SESSIONS"):
            monkeypatch.setattr(module, "SESSIONS", sessions)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "resolve_trusted_workspace",
        lambda value: Path(value or tmp_path).resolve(),
    )
    monkeypatch.setattr(routes, "get_last_workspace", lambda: str(tmp_path))
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [])
    state_db_path = state_dir / "state.db"
    monkeypatch.setattr(
        state_sync,
        "_get_state_db",
        lambda *_args, **_kwargs: SessionDB(state_db_path),
    )
    return routes, tmp_path


def _launch(standalone_env, *, profile_id="content-work-report", key="standalone-state-launch"):
    routes, workspace = standalone_env
    response = _post(
        routes,
        "/api/expert-teams/launch",
        {
            "launch_profile_id": profile_id,
            "prompt": "起草迎峰度夏保供电重点工作月度汇报",
            "idempotency_key": key,
            "session_options": {"workspace": str(workspace), "profile": "default"},
        },
    )
    assert response.status == 200
    payload = response.json_body()
    return routes, workspace, payload["session"]["session_id"], payload["run"]["run_id"]


def _confirmed_brief(*, document_type="work_report", exact_title="迎峰度夏保供电重点工作月度汇报"):
    return {
        "schema_version": "document-brief/v1",
        "revision": 2,
        "status": "confirmed",
        "confirmed_revision": 2,
        "confirmed_sha256": "b" * 64,
        "exact_title": exact_title,
        "document_type": document_type,
        "content_constraints": {"required_sections": ["工作开展情况"]},
        "document_control": {"render_template_id": "standalone-test"},
    }


def _writing_plan_artifact(*, attempt=1, brief=None):
    from api.expert_teams.stage_artifacts import build_stage_artifact

    brief = brief or _confirmed_brief()
    parsed = {
        "artifact_type": "writing_plan",
        "summary": "已形成写作计划。",
        "payload": {
            "objective": "形成月度工作汇报",
            "document_type": brief["document_type"],
            "section_plan": [
                {
                    "section_id": "SEC-1",
                    "heading": "工作开展情况",
                    "purpose": "汇报重点任务进展",
                    "required_fact_ids": ["FACT-1"],
                }
            ],
            "fact_requirements": [
                {
                    "fact_id": "FACT-1",
                    "description": "重点任务完成情况",
                    "required": True,
                    "source_requirement": "provided_source",
                }
            ],
            "assumptions": [],
            "acceptance_checks": ["标题与文档规格一致"],
        },
        "blocking_issues": [],
        "deliverable_markdown": None,
    }
    return build_stage_artifact(
        parsed,
        stage_id="plan",
        stage_attempt=attempt,
        brief=brief,
        input_refs=[],
        now="2026-07-25T10:00:00+08:00",
    )


def _reviewed_document_artifact(*, attempt=1, brief=None):
    from api.expert_teams.stage_artifacts import build_stage_artifact

    brief = brief or _confirmed_brief()
    parsed = {
        "artifact_type": "reviewed_document",
        "summary": "正文已完成语义复核。",
        "payload": {
            "title": brief["exact_title"],
            "document_type": brief["document_type"],
            "section_map": [{"section_id": "SEC-1", "heading": "工作开展情况"}],
            "fact_usage": [],
            "asset_requests": [],
            "review_report": {
                "schema_version": "content-review-report/v1",
                "checks": {
                    "brief_alignment": "passed",
                    "fact_traceability": "passed",
                    "document_purity": "passed",
                    "confidentiality": "passed",
                    "document_structure": "passed",
                },
                "issues": [],
                "change_summary": ["已完成语义复核"],
                "unresolved_issue_ids": [],
            },
            "open_issues": [],
        },
        "blocking_issues": [],
        "deliverable_markdown": f"# {brief['exact_title']}\n\n## 工作开展情况\n\n重点任务按计划推进。",
    }
    return build_stage_artifact(
        parsed,
        stage_id="polish",
        stage_attempt=attempt,
        brief=brief,
        input_refs=[
            {"ref_type": "stage_artifact", "artifact_id": "materials:1", "sha256": "1" * 64},
            {"ref_type": "stage_artifact", "artifact_id": "draft:1", "sha256": "2" * 64},
        ],
        now="2026-07-25T10:00:00+08:00",
    )


def _install_review_artifact(workspace, run_id, *, stage_index=0, attempt=1):
    from api import expert_teams
    from api.expert_teams.storage import read_run, write_run

    run = read_run(workspace, run_id)
    brief = _confirmed_brief()
    artifact = (
        _reviewed_document_artifact(attempt=attempt, brief=brief)
        if stage_index == 3
        else _writing_plan_artifact(attempt=attempt, brief=brief)
    )
    stage = run["_tasks_template"][stage_index]
    run["document_brief"] = brief
    run["workflow_state"] = "awaiting_review"
    run["current_stage_index"] = stage_index
    run["current_stage"] = {
        "index": stage_index,
        "id": stage["id"],
        "task_id": stage["id"],
        "status": "awaiting_review",
    }
    run["stage_artifacts"] = [artifact]
    run["current_stage_artifact_ref"] = {
        "artifact_id": artifact["artifact_id"],
        "sha256": artifact["sha256"],
        "stage_attempt": attempt,
    }
    reservation = {
        "reservation_id": f"stage-attempt-{stage['id']}-{attempt}",
        "stage_id": stage["id"],
        "stage_attempt": attempt,
        "executor": stage["executor"],
        "artifact_type": stage["artifact_type"],
        "input_refs": artifact["input_refs"],
        "input_binding_sha256": "c" * 64,
        "idempotency_key": f"generate-{stage['id']}-{attempt}",
        "status": "generated_valid",
    }
    run["stage_attempt_counters"] = {stage["id"]: attempt}
    run["stage_attempt_reservations"] = [reservation]
    run["current_stage_attempt_reservation"] = reservation
    run["stage_outputs"] = [
        {
            "task_id": stage["id"],
            "stage_id": stage["id"],
            "stage_attempt": attempt,
            "status": "generated",
            "artifact": artifact,
        }
    ]
    run["validation"] = {"status": "pass", "blocking_count": 0}
    return expert_teams.read_expert_team_run(workspace, write_run(workspace, run)["run_id"])


def _binding(run, *, key="confirm-stage-1", **overrides):
    artifact_ref = run["current_stage_artifact_ref"]
    body = {
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "expected_version": run["version"],
        "stage_id": run["current_stage"]["task_id"],
        "stage_attempt": artifact_ref["stage_attempt"],
        "artifact_id": artifact_ref["artifact_id"],
        "artifact_sha256": artifact_ref["sha256"],
        "idempotency_key": key,
    }
    body.update(overrides)
    return body


def test_local_stage_confirmation_is_bound_idempotent_and_has_no_enterprise_principal(standalone_env):
    from api import expert_teams

    _routes, workspace, _session_id, run_id = _launch(standalone_env)
    reviewed = _install_review_artifact(workspace, run_id)
    body = _binding(reviewed)

    confirmed = expert_teams.confirm_standalone_expert_team_stage(workspace, body)
    replay = expert_teams.confirm_standalone_expert_team_stage(workspace, body)

    assert replay == confirmed
    assert confirmed["workflow_state"] == "ready_to_generate"
    assert confirmed["current_stage_index"] == 1
    assert confirmed["approved_stage_artifact_refs"]["plan"] == {
        "artifact_id": reviewed["current_stage_artifact_ref"]["artifact_id"],
        "sha256": reviewed["current_stage_artifact_ref"]["sha256"],
    }
    assert len(confirmed["local_stage_confirmations"]) == 1
    local_confirmation = confirmed["local_stage_confirmations"][0]
    assert local_confirmation["stage_attempt"] == 1
    assert "principal" not in json.dumps(local_confirmation, ensure_ascii=False).lower()
    assert "identity" not in json.dumps(local_confirmation, ensure_ascii=False).lower()


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"expected_version": -1}, "version_conflict"),
        ({"stage_id": "materials"}, "stale_stage"),
        ({"stage_attempt": 99}, "stale_stage_attempt"),
        ({"artifact_id": "plan:99"}, "stale_artifact"),
        ({"artifact_sha256": "f" * 64}, "stale_artifact_hash"),
    ],
)
def test_local_stage_confirmation_rejects_every_stale_binding_with_authoritative_run(
    standalone_env,
    override,
    code,
):
    from api import expert_teams

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key=f"standalone-stale-{code}",
    )
    reviewed = _install_review_artifact(workspace, run_id)

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as error:
        expert_teams.confirm_standalone_expert_team_stage(
            workspace,
            _binding(reviewed, key=f"confirm-{code}", **override),
        )

    assert error.value.code == code
    assert error.value.run["run_id"] == run_id
    assert error.value.run["version"] == reviewed["version"]


def test_revision_requires_feedback_invalidates_old_artifact_and_allocates_a_new_attempt(standalone_env):
    from api import expert_teams

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-revision-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id)
    with pytest.raises(ValueError, match="feedback"):
        expert_teams.request_expert_team_stage_revision(
            workspace,
            {**_binding(reviewed, key="revise-empty"), "feedback": "   "},
        )

    revised = expert_teams.request_expert_team_stage_revision(
        workspace,
        {**_binding(reviewed, key="revise-plan-1"), "feedback": "请补充风险与下一步安排。"},
    )

    assert revised["workflow_state"] == "ready_to_generate"
    assert revised["current_stage_artifact_ref"] is None
    assert revised["stage_outputs"][-1]["status"] == "revision_requested"
    assert revised["stage_attempt_reservations"][-1]["status"] == "revision_requested"
    next_run, reservation, created = expert_teams.reserve_stage_attempt(
        workspace,
        run_id,
        stage_id="plan",
        executor="model",
        input_refs=[],
        idempotency_key="regenerate-after-revision",
    )
    assert created is True
    assert reservation["stage_attempt"] == 2
    assert next_run["version"] > revised["version"]

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as error:
        expert_teams.confirm_standalone_expert_team_stage(
            workspace,
            {
                **_binding(reviewed, key="confirm-invalidated-old-artifact"),
                "expected_version": next_run["version"],
            },
        )
    assert error.value.code in {"stale_state", "stage_artifact_missing", "stale_stage_attempt"}


def test_last_semantic_confirmation_waits_for_delivery_and_never_completes(standalone_env):
    from api import expert_teams

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-final-semantic-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)

    confirmed = expert_teams.confirm_standalone_expert_team_stage(
        workspace,
        _binding(reviewed, key="confirm-polish-1"),
    )

    assert confirmed["workflow_state"] == "delivery_validation_required"
    assert confirmed["workflow_state"] != "completed"
    assert confirmed["canonical_document_ref"]["artifact_id"] == "polish:1"
    assert confirmed["pending_system_stage"]["id"] == "delivery"
    assert confirmed["view"]["public_state"] == "generating_document"
    assert confirmed["view"]["allowed_actions"] == []
    assert confirmed["view"]["workflow"]["total"] == 5


def test_view_exposes_exact_action_binding_and_restart_reads_same_authoritative_state(standalone_env):
    from api import expert_teams

    _routes, workspace, session_id, run_id = _launch(
        standalone_env,
        key="standalone-restart-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id)
    expected = _binding(reviewed)

    assert reviewed["view"]["public_state"] == "awaiting_stage_confirmation"
    assert reviewed["view"]["allowed_actions"] == ["stage_confirm", "stage_revise"]
    assert reviewed["view"]["stage_action_binding"] == {
        key: expected[key]
        for key in (
            "session_id",
            "run_id",
            "expected_version",
            "stage_id",
            "stage_attempt",
            "artifact_id",
            "artifact_sha256",
        )
    }
    assert reviewed["view"]["workflow"]["total"] == 5

    reopened = expert_teams.read_expert_team_run(workspace, run_id)
    assert reopened["session_id"] == session_id
    assert reopened["version"] == reviewed["version"]
    assert reopened["view"]["public_state"] == "awaiting_stage_confirmation"
    assert reopened["view"]["stage_action_binding"] == reviewed["view"]["stage_action_binding"]


def test_public_stage_count_comes_from_each_server_owned_launch_profile(standalone_env):
    _routes, _workspace, _session_id, content_run_id = _launch(
        standalone_env,
        key="standalone-content-stage-count",
    )
    from api import expert_teams

    # The fixture keeps one workspace but each launch owns a distinct Session
    # and Run, matching the portal contract.
    _routes, workspace, _research_session_id, research_run_id = _launch(
        standalone_env,
        profile_id="research-report",
        key="standalone-research-stage-count",
    )

    content = expert_teams.read_expert_team_run(workspace, content_run_id)
    research = expert_teams.read_expert_team_run(workspace, research_run_id)
    assert content["view"]["workflow"]["total"] == 5
    assert research["view"]["workflow"]["total"] == 6


@pytest.mark.parametrize(
    ("workflow_state", "expected_actions"),
    [
        ("ready_to_generate", ["start_generation"]),
        ("awaiting_stage_input", ["submit_stage_input"]),
        ("start_failed", ["resume"]),
        ("generation_failed", ["resume"]),
        ("generated_invalid", ["resume"]),
        ("generating", ["cancel"]),
    ],
)
def test_allowed_actions_preserve_internal_ready_state_requirements(
    standalone_env,
    workflow_state,
    expected_actions,
):
    from api import expert_teams
    from api.expert_teams.storage import read_run, write_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key=f"standalone-actions-{workflow_state}",
    )
    run = read_run(workspace, run_id)
    run["workflow_state"] = workflow_state
    if workflow_state == "generating":
        run["execution_stream_id"] = "stream-action-matrix"
    if workflow_state == "awaiting_stage_input":
        run["pending_input"] = {
            "id": "stage-input-1",
            "stage_id": "plan",
            "question": "请确认写作口径",
            "required": True,
        }
    stored = write_run(workspace, run)
    view = expert_teams.read_expert_team_run(workspace, stored["run_id"])["view"]

    assert view["allowed_actions"] == expected_actions


def test_unbound_standalone_completed_state_never_claims_delivery_completed(standalone_env):
    from api import expert_teams
    from api.expert_teams.storage import read_run, write_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-completed-truth-launch",
    )
    run = read_run(workspace, run_id)
    run["workflow_state"] = "completed"
    run["current_stage_index"] = len(run["_tasks_template"])
    write_run(workspace, run)

    reopened = expert_teams.read_expert_team_run(workspace, run_id)

    assert reopened["workflow_state"] == "completed"
    assert reopened["view"]["public_state"] == "failed"
    assert reopened["view"]["presentation"]["state"] != "completed"
    assert reopened["view"]["allowed_actions"] == []
    assert "delivery_confirm" not in reopened["view"]["allowed_actions"]
    assert reopened["view"]["delivery_action_binding"] is None
    assert reopened["view"]["delivery_recovery_binding"] is None
    assert reopened["view"]["delivery_status"] == "delivery_unverified"
    assert reopened["view"]["next_action"] == {
        "type": "none",
        "label": "当前交付无法自动恢复，请新建专家团任务",
    }


def test_routes_keep_standalone_out_of_enterprise_approval_and_return_authoritative_409(
    standalone_env,
    monkeypatch,
):
    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-route-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id)
    monkeypatch.setattr(
        routes,
        "_expert_identity_session",
        lambda _handler: (_ for _ in ()).throw(AssertionError("identity path must not run")),
    )

    response = _post(routes, "/api/expert-teams/stage/approve", _binding(reviewed))

    assert response.status == 409
    payload = response.json_body()
    assert payload["code"] == "standalone_confirmation_required"
    assert payload["run"]["run_id"] == run_id
    assert payload["run"]["view"]["stage_action_binding"]["artifact_id"] == "plan:1"


def test_confirm_route_succeeds_without_touching_enterprise_identity(
    standalone_env,
    monkeypatch,
):
    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-confirm-route-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id)
    monkeypatch.setattr(
        routes,
        "_expert_identity_session",
        lambda _handler: (_ for _ in ()).throw(AssertionError("identity path must not run")),
    )
    monkeypatch.setattr(
        routes,
        "_start_expert_team_execution",
        lambda _workspace, run, _body: ({"ok": True, "run": run}, 200),
    )

    response = _post(
        routes,
        "/api/expert-teams/stage/confirm",
        _binding(reviewed, key="confirm-route-plan-1"),
    )

    assert response.status == 200
    payload = response.json_body()
    assert payload["ok"] is True
    assert payload["run"]["workflow_state"] == "ready_to_generate"
    assert payload["run"]["view"]["public_state"] == "ready"


def test_confirm_route_dispatches_document_generation_after_last_semantic_stage(
    standalone_env,
    monkeypatch,
):
    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-final-confirm-route-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)
    dispatched = []

    def record_dispatch(_workspace, run, _body):
        dispatched.append((run["run_id"], run["workflow_state"]))
        return {"ok": True, "run": run}, 200

    monkeypatch.setattr(routes, "_start_expert_team_execution", record_dispatch)
    response = _post(
        routes,
        "/api/expert-teams/stage/confirm",
        _binding(reviewed, key="confirm-route-polish-1"),
    )

    assert response.status == 200
    assert dispatched == [(run_id, "delivery_validation_required")]
    assert response.json_body()["run"]["view"]["public_state"] == "generating_document"


def test_delivery_render_holds_attempt_lock_and_releases_before_run_completion(
    standalone_env,
    monkeypatch,
):
    from api import docx_engine_v2, expert_teams
    from api.expert_teams import delivery_integrity

    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-delivery-lock-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)
    confirmed_stage = expert_teams.confirm_standalone_expert_team_stage(
        workspace,
        _binding(reviewed, key="standalone-delivery-lock-stage-confirm"),
    )
    lock_depth = 0
    events = []
    original_lock = delivery_integrity.delivery_attempt_lock
    original_engine = docx_engine_v2._create_expert_delivery_job
    original_complete = expert_teams.complete_system_stage_attempt

    @contextmanager
    def observed_lock(*args, **kwargs):
        nonlocal lock_depth
        with original_lock(*args, **kwargs):
            lock_depth += 1
            events.append("delivery-lock-enter")
            try:
                yield
            finally:
                events.append("delivery-lock-exit")
                lock_depth -= 1

    def observed_engine(*args, **kwargs):
        assert lock_depth == 1
        events.append("engine")
        return original_engine(*args, **kwargs)

    def observed_complete(*args, **kwargs):
        assert lock_depth == 0
        events.append("complete")
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(delivery_integrity, "delivery_attempt_lock", observed_lock)
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", observed_engine)
    monkeypatch.setattr(expert_teams, "complete_system_stage_attempt", observed_complete)

    payload, status = routes._start_expert_team_execution(workspace, confirmed_stage, {})

    assert status == 200, payload
    assert events == ["delivery-lock-enter", "engine", "delivery-lock-exit", "complete"]


def test_delivery_generation_rejects_symlinked_attempt_root_before_any_write(
    standalone_env,
):
    from api import expert_teams
    from api.expert_teams.delivery_integrity import canonical_attempt_root

    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-delivery-symlink-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)
    confirmed_stage = expert_teams.confirm_standalone_expert_team_stage(
        workspace,
        _binding(reviewed, key="standalone-delivery-symlink-stage-confirm"),
    )
    attempt_root = canonical_attempt_root(workspace, run_id, "delivery", 1)
    external = workspace.parent / f"{workspace.name}-external-delivery-attempt"
    external.mkdir(parents=True)
    attempt_root.parent.mkdir(parents=True, exist_ok=True)
    attempt_root.symlink_to(external, target_is_directory=True)

    payload, status = routes._start_expert_team_execution(workspace, confirmed_stage, {})

    assert status == 503, payload
    assert payload["code"] == "delivery_path_unsafe"
    assert list(external.iterdir()) == []


def test_retry_reuses_complete_orphan_delivery_output_after_binding_crash(
    standalone_env,
    monkeypatch,
):
    from api import expert_teams
    from api.expert_teams import documents
    from api.expert_teams.delivery_integrity import canonical_attempt_root

    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-complete-orphan-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)
    confirmed_stage = expert_teams.confirm_standalone_expert_team_stage(
        workspace,
        _binding(reviewed, key="standalone-complete-orphan-stage-confirm"),
    )
    original_builder = documents.build_delivery_binding_v3

    def crash_before_binding(*_args, **_kwargs):
        raise RuntimeError("simulated crash after renderer output")

    monkeypatch.setattr(documents, "build_delivery_binding_v3", crash_before_binding)
    first_payload, first_status = routes._start_expert_team_execution(
        workspace,
        confirmed_stage,
        {},
    )
    assert first_status == 503, first_payload
    attempt_root = canonical_attempt_root(workspace, run_id, "delivery", 1)
    assert (attempt_root / "delivery" / "document.docx").is_file()
    assert not (attempt_root / "expert-team-delivery.json").exists()

    engine_calls = []

    def renderer_must_not_run(*_args, **_kwargs):
        engine_calls.append("called")
        return {"ok": False, "code": "renderer_replayed", "message": "renderer must not replay"}, 409

    monkeypatch.setattr(documents, "build_delivery_binding_v3", original_builder)
    monkeypatch.setattr(routes.docx_engine_v2, "_create_expert_delivery_job", renderer_must_not_run)
    current = expert_teams.read_expert_team_run(workspace, run_id)
    retry_payload, retry_status = routes._start_expert_team_execution(workspace, current, {})

    assert retry_status == 200, retry_payload
    assert engine_calls == []
    assert retry_payload["run"]["current_delivery_attempt_reservation"]["delivery_attempt"] == 1
    assert retry_payload["run"]["delivery_attempt_counter"] == 1


def test_retry_quarantines_incomplete_orphan_output_then_renders_safely(
    standalone_env,
    monkeypatch,
):
    from api import docx_engine_v2, expert_teams
    from api.expert_teams import documents
    from api.expert_teams.delivery_integrity import canonical_attempt_root

    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-incomplete-orphan-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)
    confirmed_stage = expert_teams.confirm_standalone_expert_team_stage(
        workspace,
        _binding(reviewed, key="standalone-incomplete-orphan-stage-confirm"),
    )
    original_builder = documents.build_delivery_binding_v3
    original_engine = docx_engine_v2._create_expert_delivery_job
    monkeypatch.setattr(
        documents,
        "build_delivery_binding_v3",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated crash after renderer output")
        ),
    )
    first_payload, first_status = routes._start_expert_team_execution(
        workspace,
        confirmed_stage,
        {},
    )
    assert first_status == 503, first_payload

    attempt_root = canonical_attempt_root(workspace, run_id, "delivery", 1)
    delivery_dir = attempt_root / "delivery"
    (delivery_dir / "document.docx").unlink()
    (delivery_dir / "orphan-marker.txt").write_text("partial", encoding="utf-8")
    engine_calls = []

    def observed_engine(*args, **kwargs):
        engine_calls.append("called")
        return original_engine(*args, **kwargs)

    monkeypatch.setattr(documents, "build_delivery_binding_v3", original_builder)
    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", observed_engine)
    current = expert_teams.read_expert_team_run(workspace, run_id)
    retry_payload, retry_status = routes._start_expert_team_execution(workspace, current, {})

    assert retry_status == 200, retry_payload
    assert engine_calls == ["called"]
    assert (delivery_dir / "document.docx").is_file()
    quarantined_markers = list((attempt_root / "recovery").glob("*/delivery/orphan-marker.txt"))
    assert len(quarantined_markers) == 1
    assert quarantined_markers[0].read_text(encoding="utf-8") == "partial"


def test_real_standalone_delivery_generation_confirmation_and_drift_detection(
    standalone_env,
    monkeypatch,
):
    from api import expert_teams
    from api.expert_teams.standalone_delivery import validate_standalone_delivery_context

    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-real-delivery-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)
    confirmed_stage = expert_teams.confirm_standalone_expert_team_stage(
        workspace,
        _binding(reviewed, key="standalone-real-delivery-stage-confirm"),
    )

    generated_payload, generated_status = routes._start_expert_team_execution(
        workspace,
        confirmed_stage,
        {},
    )

    assert generated_status == 200, generated_payload
    generated = generated_payload["run"]
    assert generated["workflow_state"] == "awaiting_review"
    assert generated["view"]["public_state"] == "awaiting_delivery_confirmation"
    assert generated["view"]["allowed_actions"] == [
        "delivery_open_document",
        "delivery_open_folder",
        "delivery_revise",
        "delivery_confirm",
    ]
    action_binding = generated["view"]["delivery_action_binding"]
    assert action_binding["document_sha256"]
    delivery = validate_standalone_delivery_context(workspace, generated)
    assert delivery["document_path"].is_file()
    assert delivery["document_path"].suffix == ".docx"

    response = _post(
        routes,
        "/api/expert-teams/delivery/confirm",
        {**action_binding, "idempotency_key": "standalone-real-delivery-confirm"},
    )

    assert response.status == 200, response.json_body()
    completed = response.json_body()["run"]
    assert completed["workflow_state"] == "completed"
    assert completed["completion_integrity"]["status"] == "valid"
    assert completed["view"]["public_state"] == "completed"
    assert completed["view"]["allowed_actions"] == [
        "delivery_open_document",
        "delivery_open_folder",
    ]

    opened = []
    monkeypatch.setattr(
        routes,
        "_open_expert_team_delivery_target",
        lambda path, target: opened.append((Path(path), target)),
    )
    open_response = _post(
        routes,
        "/api/expert-teams/delivery/open",
        {
            **action_binding,
            "expected_version": completed["version"],
            "idempotency_key": "standalone-real-delivery-open-after-confirm",
            "target": "document",
        },
    )
    assert open_response.status == 200, open_response.json_body()
    assert opened == [(delivery["document_path"], "document")]

    replay = _post(
        routes,
        "/api/expert-teams/delivery/confirm",
        {**action_binding, "idempotency_key": "standalone-real-delivery-confirm"},
    )
    assert replay.status == 200, replay.json_body()
    replayed = replay.json_body()["run"]
    assert replayed["version"] == completed["version"]
    assert replayed["local_delivery_confirmation"] == completed["local_delivery_confirmation"]
    assert sum(
        item.get("action") == "confirm_delivery"
        for item in replayed.get("action_journal") or []
        if isinstance(item, dict)
    ) == 1

    original_document = delivery["document_path"].read_bytes()
    delivery["document_path"].write_bytes(original_document + b"tampered")
    drifted = expert_teams.read_expert_team_run(workspace, run_id)
    assert drifted["completion_integrity"]["status"] == "drifted"
    assert drifted["view"]["public_state"] == "awaiting_delivery_confirmation"
    assert "delivery_confirm" not in drifted["view"]["allowed_actions"]
    assert drifted["view"]["allowed_actions"] == ["delivery_recover"]
    assert drifted["view"]["presentation"]["primary_action"] == {
        "id": "delivery_recover",
        "label": "重新生成 DOCX",
        "kind": "primary",
    }
    assert drifted["view"]["delivery_status"] == "delivery_drifted"
    assert drifted["view"]["next_action"] == {
        "type": "recover_delivery",
        "label": "重新生成 DOCX",
    }
    recovery_binding = drifted["view"]["delivery_recovery_binding"]
    assert recovery_binding == {
        **{
            field: completed["local_delivery_confirmation"][field]
            for field in (
                "session_id",
                "run_id",
                "stage_id",
                "stage_attempt",
                "artifact_id",
                "artifact_sha256",
                "delivery_attempt",
                "delivery_binding_sha256",
                "document_sha256",
            )
        },
        "expected_version": completed["version"],
    }

    rejected_path = _post(
        routes,
        "/api/expert-teams/delivery/recover",
        {
            **recovery_binding,
            "idempotency_key": "standalone-real-delivery-recover-path",
            "path": str(delivery["document_path"]),
        },
    )
    assert rejected_path.status == 400, rejected_path.json_body()
    unchanged = expert_teams.read_expert_team_run(workspace, run_id)
    assert unchanged["version"] == completed["version"]
    assert unchanged["delivery_attempt_counter"] == 1

    for label, mutation, expected_status, expected_code in (
        (
            "unknown-field",
            {"client_note": "must be rejected"},
            400,
            None,
        ),
        (
            "boolean-version",
            {"expected_version": True},
            400,
            None,
        ),
        (
            "stale-version",
            {"expected_version": completed["version"] - 1},
            409,
            "version_conflict",
        ),
        (
            "stale-stage",
            {"stage_id": "other-stage"},
            409,
            "stale_stage",
        ),
        (
            "stale-stage-attempt",
            {"stage_attempt": recovery_binding["stage_attempt"] + 1},
            409,
            "stale_stage_attempt",
        ),
        (
            "stale-artifact",
            {"artifact_id": "delivery:999"},
            409,
            "stale_artifact",
        ),
        (
            "stale-artifact-hash",
            {"artifact_sha256": "e" * 64},
            409,
            "stale_artifact_hash",
        ),
        (
            "stale-delivery-attempt",
            {"delivery_attempt": recovery_binding["delivery_attempt"] + 1},
            409,
            "stale_delivery_attempt",
        ),
        (
            "stale-binding-hash",
            {"delivery_binding_sha256": "c" * 64},
            409,
            "stale_delivery_binding",
        ),
        (
            "stale-document-hash",
            {"document_sha256": "f" * 64},
            409,
            "stale_document_hash",
        ),
    ):
        rejected = _post(
            routes,
            "/api/expert-teams/delivery/recover",
            {
                **recovery_binding,
                "idempotency_key": f"standalone-real-delivery-recover-{label}",
                **mutation,
            },
        )
        assert rejected.status == expected_status, rejected.json_body()
        if expected_code is not None:
            assert rejected.json_body()["code"] == expected_code
        unchanged = expert_teams.read_expert_team_run(workspace, run_id)
        assert unchanged["version"] == completed["version"]
        assert unchanged["delivery_attempt_counter"] == 1

    delivery["document_path"].write_bytes(original_document)
    no_longer_drifted = _post(
        routes,
        "/api/expert-teams/delivery/recover",
        {
            **recovery_binding,
            "idempotency_key": "standalone-real-delivery-recover-no-drift",
        },
    )
    assert no_longer_drifted.status == 409, no_longer_drifted.json_body()
    assert no_longer_drifted.json_body()["code"] == "delivery_recovery_not_required"
    delivery["document_path"].write_bytes(original_document + b"tampered-again")

    recovered_response = _post(
        routes,
        "/api/expert-teams/delivery/recover",
        {
            **recovery_binding,
            "idempotency_key": "standalone-real-delivery-recover",
        },
    )
    assert recovered_response.status == 200, recovered_response.json_body()
    regenerated = recovered_response.json_body()["run"]
    assert regenerated["workflow_state"] == "awaiting_review"
    assert regenerated["view"]["public_state"] == "awaiting_delivery_confirmation"
    assert regenerated["current_delivery_attempt_reservation"]["delivery_attempt"] == 2
    assert regenerated["delivery_attempt_counter"] == 2
    assert regenerated["current_stage_attempt_reservation"]["stage_attempt"] == 2
    assert regenerated["local_delivery_confirmation"] is None
    assert regenerated["delivery_gate"]["status"] == "pending_confirmation"
    assert [
        item["status"]
        for item in regenerated["delivery_attempt_reservations"]
        if item["delivery_attempt"] == 1
    ] == ["invalidated"]
    assert [
        item["status"]
        for item in regenerated["stage_attempt_reservations"]
        if item["stage_id"] == "delivery" and item["stage_attempt"] == 1
    ] == ["invalidated"]

    recovery_replay = _post(
        routes,
        "/api/expert-teams/delivery/recover",
        {
            **recovery_binding,
            "idempotency_key": "standalone-real-delivery-recover",
        },
    )
    assert recovery_replay.status == 200, recovery_replay.json_body()
    replayed_recovery = recovery_replay.json_body()["run"]
    assert replayed_recovery["version"] == regenerated["version"]
    assert replayed_recovery["delivery_attempt_counter"] == 2
    assert replayed_recovery["stage_attempt_counters"]["delivery"] == 2

    reconfirmed_response = _post(
        routes,
        "/api/expert-teams/delivery/confirm",
        {
            **regenerated["view"]["delivery_action_binding"],
            "idempotency_key": "standalone-real-delivery-reconfirm",
        },
    )
    assert reconfirmed_response.status == 200, reconfirmed_response.json_body()
    reconfirmed = reconfirmed_response.json_body()["run"]
    assert reconfirmed["workflow_state"] == "completed"
    assert reconfirmed["completion_integrity"]["status"] == "valid"
    assert reconfirmed["local_delivery_confirmation"]["delivery_attempt"] == 2
    assert reconfirmed["view"]["public_state"] == "completed"


def test_concurrent_same_key_delivery_recovery_generates_and_completes_once(
    standalone_env,
    monkeypatch,
):
    from api import docx_engine_v2, expert_teams
    from api.expert_teams.standalone_delivery import validate_standalone_delivery_context

    routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-concurrent-recovery-launch",
    )
    reviewed = _install_review_artifact(workspace, run_id, stage_index=3)
    confirmed_stage = expert_teams.confirm_standalone_expert_team_stage(
        workspace,
        _binding(reviewed, key="standalone-concurrent-recovery-stage-confirm"),
    )
    generated_payload, generated_status = routes._start_expert_team_execution(
        workspace,
        confirmed_stage,
        {},
    )
    assert generated_status == 200, generated_payload
    generated = generated_payload["run"]
    confirmed_response = _post(
        routes,
        "/api/expert-teams/delivery/confirm",
        {
            **generated["view"]["delivery_action_binding"],
            "idempotency_key": "standalone-concurrent-recovery-confirm",
        },
    )
    assert confirmed_response.status == 200, confirmed_response.json_body()
    completed = confirmed_response.json_body()["run"]
    delivery = validate_standalone_delivery_context(workspace, completed)
    delivery["document_path"].write_bytes(delivery["document_path"].read_bytes() + b"drift")
    drifted = expert_teams.read_expert_team_run(workspace, run_id)
    action = {
        **drifted["view"]["delivery_recovery_binding"],
        "idempotency_key": "standalone-concurrent-recovery",
    }

    original_engine = docx_engine_v2._create_expert_delivery_job
    original_reserve = expert_teams.reserve_system_stage_attempt
    engine_entered = threading.Event()
    release_engine = threading.Event()
    second_reserved = threading.Event()
    counter_lock = threading.Lock()
    engine_calls = 0
    reserve_calls = 0

    def slow_engine(*args, **kwargs):
        nonlocal engine_calls
        with counter_lock:
            engine_calls += 1
        engine_entered.set()
        assert release_engine.wait(timeout=10)
        return original_engine(*args, **kwargs)

    def observed_reserve(*args, **kwargs):
        nonlocal reserve_calls
        result = original_reserve(*args, **kwargs)
        with counter_lock:
            reserve_calls += 1
            if reserve_calls >= 2:
                second_reserved.set()
        return result

    monkeypatch.setattr(docx_engine_v2, "_create_expert_delivery_job", slow_engine)
    monkeypatch.setattr(expert_teams, "reserve_system_stage_attempt", observed_reserve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_post, routes, "/api/expert-teams/delivery/recover", action)
        assert engine_entered.wait(timeout=10)
        second = pool.submit(_post, routes, "/api/expert-teams/delivery/recover", action)
        assert second_reserved.wait(timeout=10)
        release_engine.set()
        responses = [first.result(timeout=20), second.result(timeout=20)]

    assert [response.status for response in responses] == [200, 200]
    assert engine_calls == 1
    observed = expert_teams.read_expert_team_run(workspace, run_id)
    assert observed["delivery_attempt_counter"] == 2
    assert observed["stage_attempt_counters"]["delivery"] == 2
    assert sum(
        item.get("action") == "recover_delivery"
        for item in observed.get("action_journal") or []
        if isinstance(item, dict)
    ) == 1


def test_standalone_read_ignores_residual_enterprise_office_evidence(
    standalone_env,
    monkeypatch,
):
    from api.expert_teams import office_review, runtime
    from api.expert_teams.delivery_integrity import canonical_attempt_root
    from api.expert_teams.storage import read_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-office-residual-launch",
    )
    run = read_run(workspace, run_id)
    run["workflow_state"] = "awaiting_review"
    attempt_root = canonical_attempt_root(workspace, run_id, "delivery", 1)
    attempt_root.mkdir(parents=True, exist_ok=True)
    (attempt_root / "expert-team-wps-acceptance.json").write_text("{}", encoding="utf-8")
    (attempt_root / "enterprise-completion-transaction.json").write_text("{}", encoding="utf-8")
    binding_path = attempt_root / "delivery-binding.json"
    binding_path.write_text("{}", encoding="utf-8")
    run["current_delivery_manifest_ref"] = {
        "delivery_attempt": 1,
        "delivery_binding_path": str(binding_path.relative_to(workspace)),
        "delivery_binding_sha256": "a" * 64,
    }
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("enterprise")
        raise AssertionError("standalone read entered enterprise Office reconciliation")

    monkeypatch.setattr(runtime, "_attach_office_review_view", forbidden)
    monkeypatch.setattr(office_review, "reconcile_enterprise_completion", forbidden)

    observed = runtime._completion_integrity_for_read(workspace, run)

    assert calls == []
    assert observed["workflow_state"] == "awaiting_review"
    assert "office_review_view" not in observed
    assert "completion_transaction_ref" not in observed


def test_standalone_completed_read_still_runs_local_digest_integrity_after_office_bypass(
    standalone_env,
    monkeypatch,
):
    from api.expert_teams import runtime
    from api.expert_teams.storage import read_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-local-integrity-launch",
    )
    run = read_run(workspace, run_id)
    run["workflow_state"] = "completed"
    run["current_delivery_manifest_ref"] = {
        "delivery_attempt": 1,
        "delivery_binding_path": "residual-enterprise-binding.json",
        "delivery_binding_sha256": "a" * 64,
    }
    monkeypatch.setattr(
        runtime,
        "_attach_office_review_view",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("standalone completed read entered enterprise Office view")
        ),
    )

    observed = runtime._completion_integrity_for_read(workspace, run)

    assert observed["workflow_state"] == "completed"
    assert observed["completion_integrity"]["status"] == "unverified"
    assert "office_review_view" not in observed

    view = runtime.expert_team_run_view(observed)
    assert view["public_state"] == "failed"
    assert view["allowed_actions"] == []
    assert view["delivery_action_binding"] is None
    assert view["delivery_recovery_binding"] is None
    assert view["delivery_status"] == "delivery_unverified"
    assert view["next_action"] == {
        "type": "none",
        "label": "当前交付无法自动恢复，请新建专家团任务",
    }
    assert view["presentation"]["title"] == "交付确认缺失"
    assert view["presentation"]["primary_action"] is None


@pytest.mark.parametrize("ledger_case", ["missing", "duplicate", "mismatched", "corrupt"])
def test_stage_action_binding_requires_one_exact_durable_reservation(
    standalone_env,
    ledger_case,
):
    from api import expert_teams

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key=f"standalone-ledger-{ledger_case}",
    )
    reviewed = _install_review_artifact(workspace, run_id)
    run = deepcopy(reviewed)
    authoritative = deepcopy(run["current_stage_attempt_reservation"])
    if ledger_case == "missing":
        run["stage_attempt_reservations"] = []
    elif ledger_case == "duplicate":
        run["stage_attempt_reservations"] = [authoritative, deepcopy(authoritative)]
    elif ledger_case == "mismatched":
        drifted = deepcopy(authoritative)
        drifted["input_refs"] = [{"ref_type": "stage_artifact", "artifact_id": "other:1", "sha256": "f" * 64}]
        run["stage_attempt_reservations"] = [drifted]
    else:
        corrupt = deepcopy(authoritative)
        corrupt["stage_attempt"] = "not-an-integer"
        run["stage_attempt_reservations"] = [corrupt]

    view = expert_teams.expert_team_run_view(run)

    assert view["stage_action_binding"] is None
    assert view["allowed_actions"] == []
    assert [item["id"] for item in view["presentation"]["secondary_actions"]] == ["view_result"]


@pytest.mark.parametrize(
    ("workflow_state", "expected_public_state", "expected_actions"),
    [
        ("failed", "failed", []),
        ("cancelled", "cancelled", []),
        ("cancelling", "cancelling", ["refresh"]),
    ],
)
def test_terminal_and_cancelling_states_are_explicit_and_never_look_ready(
    standalone_env,
    workflow_state,
    expected_public_state,
    expected_actions,
):
    from api import expert_teams
    from api.expert_teams.storage import read_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key=f"standalone-terminal-{workflow_state}",
    )
    run = read_run(workspace, run_id)
    run["workflow_state"] = workflow_state
    if workflow_state == "cancelling":
        run["cancel_outcome"] = "accepted"

    view = expert_teams.expert_team_run_view(run)

    assert view["public_state"] == expected_public_state
    assert view["allowed_actions"] == expected_actions
    assert view["presentation"]["state"] == workflow_state
    assert view["public_state"] != "ready"
    if workflow_state == "cancelling":
        assert view["cancel_action_binding"] is None


def test_unknown_cancellation_binding_retries_the_exact_original_request(standalone_env):
    from api import expert_teams, runtime_adapter
    from api.expert_teams.storage import read_run, write_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-cancel-binding-launch",
    )
    generating = read_run(workspace, run_id)
    generating.update(
        {
            "workflow_state": "generating",
            "execution_stream_id": "standalone-cancel-stream",
            "execution_runtime_run_id": "standalone-cancel-runtime",
            "execution_runtime_adapter": "RunnerRuntimeAdapter",
        }
    )
    generating = read_run(workspace, write_run(workspace, generating)["run_id"])
    original_body = {
        "session_id": generating["session_id"],
        "run_id": generating["run_id"],
        "expected_version": generating["version"],
        "stage_id": generating["current_stage"]["task_id"],
        "idempotency_key": "standalone-cancel-original-request",
    }
    callback_calls = []

    def cancel_callback(_run):
        callback_calls.append("cancel")
        if len(callback_calls) == 1:
            return runtime_adapter.ControlResult(
                False,
                status="timeout",
                safe_message="runtime timeout",
            )
        return runtime_adapter.ControlResult(True, status="accepted")

    unknown = expert_teams.cancel_expert_team(
        workspace,
        original_body,
        cancel_callback=cancel_callback,
    )
    view = expert_teams.expert_team_run_view(unknown)

    assert view["allowed_actions"] == ["refresh", "retry_cancel"]
    assert view["cancel_action_binding"] == {
        "session_id": unknown["session_id"],
        "run_id": unknown["run_id"],
        "expected_version": unknown["version"],
        "stage_id": unknown["current_stage"]["task_id"],
        "idempotency_key": original_body["idempotency_key"],
    }

    retried = expert_teams.cancel_expert_team(
        workspace,
        view["cancel_action_binding"],
        cancel_callback=cancel_callback,
    )

    assert callback_calls == ["cancel", "cancel"]
    assert retried["workflow_state"] == "cancelling"
    assert retried["cancel_outcome"] == "accepted"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cancel_request_id", ""),
        ("cancel_request_fingerprint", ""),
        ("cancel_request_fingerprint", "f" * 64),
        ("session_id", ""),
        ("run_id", ""),
        ("version", "not-an-integer"),
    ],
)
def test_unknown_cancellation_binding_fails_closed_when_request_identity_is_incomplete(
    standalone_env,
    field,
    value,
):
    from api import expert_teams, runtime_adapter
    from api.expert_teams.storage import read_run, write_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key=f"standalone-cancel-binding-invalid-{field}-{value}",
    )
    generating = read_run(workspace, run_id)
    generating.update(
        {
            "workflow_state": "generating",
            "execution_stream_id": "standalone-invalid-cancel-stream",
            "execution_runtime_run_id": "standalone-invalid-cancel-runtime",
            "execution_runtime_adapter": "RunnerRuntimeAdapter",
        }
    )
    generating = read_run(workspace, write_run(workspace, generating)["run_id"])
    body = {
        "session_id": generating["session_id"],
        "run_id": generating["run_id"],
        "expected_version": generating["version"],
        "stage_id": generating["current_stage"]["task_id"],
        "idempotency_key": f"standalone-cancel-invalid-{field}",
    }
    unknown = expert_teams.cancel_expert_team(
        workspace,
        body,
        cancel_callback=lambda _run: runtime_adapter.ControlResult(
            False,
            status="timeout",
            safe_message="runtime timeout",
        ),
    )
    corrupt = deepcopy(unknown)
    corrupt[field] = value

    view = expert_teams.expert_team_run_view(corrupt)

    assert view["cancel_action_binding"] is None
    assert view["allowed_actions"] == ["refresh"]


def test_awaiting_stage_input_uses_supplement_copy_and_one_matching_action(standalone_env):
    from api import expert_teams
    from api.expert_teams.storage import read_run

    _routes, workspace, _session_id, run_id = _launch(
        standalone_env,
        key="standalone-stage-input-copy",
    )
    run = read_run(workspace, run_id)
    run["workflow_state"] = "awaiting_stage_input"
    run["pending_input"] = {
        "id": "stage-input-copy",
        "stage_id": "plan",
        "question": "请补充汇报时间范围",
        "required": True,
    }

    view = expert_teams.expert_team_run_view(run)

    assert "补充" in view["presentation"]["title"]
    assert view["presentation"]["primary_action"]["id"] == "submit_stage_input"
    assert view["allowed_actions"] == ["submit_stage_input"]
