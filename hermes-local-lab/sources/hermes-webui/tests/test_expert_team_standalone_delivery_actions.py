from __future__ import annotations

import io
import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import pytest


class _RouteHandler:
    def __init__(self, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
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

    def json_body(self):
        return json.loads(bytes(self.body).decode("utf-8"))


def _post(routes, path: str, payload: dict) -> _RouteHandler:
    handler = _RouteHandler(payload)
    routes.handle_post(handler, urlparse(path))
    return handler


@pytest.fixture(autouse=True)
def _fixture_has_a_committed_launch_binding(monkeypatch):
    """The launch transaction itself is covered by the Phase 1 contract suite."""

    from api.expert_teams import storage

    monkeypatch.setattr(
        storage,
        "_validate_public_standalone_run",
        lambda _workspace, run, **_kwargs: run,
    )


def _delivery_fixture(tmp_path: Path) -> tuple[dict, dict]:
    from api.expert_teams.stage_artifacts import artifact_digest
    from api.expert_teams.storage import write_run

    run_id = "et-standalone-delivery"
    session_id = "session-standalone-delivery"
    attempt_root = (
        tmp_path
        / ".taiji"
        / "expert-team-deliveries"
        / run_id
        / "delivery"
        / "attempt-1"
    )
    delivery_dir = attempt_root / "delivery"
    delivery_dir.mkdir(parents=True)
    document_path = delivery_dir / "document.docx"
    quality_path = delivery_dir / "quality-report.json"
    binding_path = attempt_root / "expert-team-delivery.json"
    document_path.write_bytes(b"standalone-docx")
    quality_path.write_text('{"schema_version":"expert-standalone-quality/v1","status":"passed","checks":[]}', encoding="utf-8")
    binding_path.write_text('{"schema_version":"expert-delivery-binding/v3"}', encoding="utf-8")

    document_sha256 = "d" * 64
    binding_sha256 = "b" * 64
    artifact = {
        "schema_version": "expert-stage-artifact/v1",
        "artifact_id": "delivery:1",
        "artifact_type": "delivery_manifest",
        "stage_id": "delivery",
        "stage_attempt": 1,
        "brief_revision": 1,
        "brief_sha256": "2" * 64,
        "input_refs": [
            {
                "ref_type": "stage_artifact",
                "artifact_id": "polish:1",
                "sha256": "a" * 64,
            }
        ],
        "summary": "独立版 DOCX 已生成并通过自动检查。",
        "payload": {
            "schema_version": "delivery-manifest/v2",
            "product_mode": "standalone",
            "render_input_fingerprint": "f" * 64,
            "delivery_attempt": 1,
            "document_revision": 1,
            "document_sha256": document_sha256,
            "delivery_binding_path": str(binding_path.relative_to(tmp_path)),
            "delivery_binding_sha256": binding_sha256,
            "standalone_quality_report_sha256": "3" * 64,
            "automatic_check_summary": {
                "status": "passed",
                "passed_count": 5,
                "failed_count": 0,
                "warning_count": 0,
                "blocking_count": 0,
            },
            "local_confirmation_required": True,
        },
        "deliverable_markdown": None,
        "blocking_issues": [],
        "validation_status": "valid",
        "created_at": "2026-07-25T10:00:00+08:00",
    }
    artifact["sha256"] = artifact_digest(artifact)
    semantic_reservation = {
        "reservation_id": "stage-polish-1",
        "stage_id": "polish",
        "stage_attempt": 1,
        "executor": "model",
        "artifact_type": "reviewed_document",
        "input_refs": [],
        "input_binding_sha256": "c" * 64,
        "idempotency_key": "semantic-polish-1",
        "status": "confirmed",
    }
    system_reservation = {
        "reservation_id": "stage-delivery-1",
        "stage_id": "delivery",
        "stage_attempt": 1,
        "executor": "system",
        "artifact_type": "delivery_manifest",
        "input_refs": deepcopy(artifact["input_refs"]),
        "input_binding_sha256": "e" * 64,
        "idempotency_key": "system-delivery-1",
        "status": "generated_valid",
    }
    delivery_reservation = {
        "reservation_id": "delivery-attempt-1",
        "document_revision": 1,
        "delivery_attempt": 1,
        "render_input_fingerprint": "f" * 64,
        "input_binding_sha256": "1" * 64,
        "idempotency_key": "render-delivery-1",
        "status": "generated_valid",
    }
    semantic_artifact = {
        "artifact_id": "polish:1",
        "sha256": "a" * 64,
        "stage_id": "polish",
        "stage_attempt": 1,
        "artifact_type": "reviewed_document",
        "input_refs": [],
        "validation_status": "valid",
        "blocking_issues": [],
    }
    tasks = [
        {
            "id": "polish",
            "title": "审稿打磨",
            "phase": "审稿打磨",
            "worker_id": "reviewer",
            "worker_name": "审稿专家",
            "executor": "model",
            "artifact_type": "reviewed_document",
            "depends_on": [],
        }
    ]
    descriptor = {
        "id": "delivery",
        "title": "生成 DOCX",
        "phase": "文档生成",
        "worker_id": "delivery",
        "worker_name": "交付复核专家",
        "executor": "system",
        "artifact_type": "delivery_manifest",
        "depends_on": ["polish"],
    }
    run = {
        "schema_version": 3,
        "contract_version": "expert-team-contract/v1",
        "product_mode": "standalone",
        "version": 8,
        "run_id": run_id,
        "session_id": session_id,
        "team_id": "content-creator-team",
        "team_title": "内容创作专家团",
        "title": "工作汇报",
        "prompt": "起草工作汇报",
        "created_at": "2026-07-25T09:00:00+08:00",
        "updated_at": "2026-07-25T10:00:00+08:00",
        "workflow_state": "awaiting_review",
        "current_stage_index": 0,
        "_tasks_template": tasks,
        "tasks": deepcopy(tasks),
        "members": [],
        "questions": [],
        "answers": [],
        "events": [],
        "timeline_events": [],
        "action_journal": [],
        "artifacts": [],
        "stage_outputs": [
            {"stage_id": "polish", "task_id": "polish", "stage_attempt": 1, "status": "confirmed"}
        ],
        "stage_results": [],
        "review_items": [],
        "document_brief": {
            "schema_version": "document-brief/v1",
            "status": "confirmed",
            "revision": 1,
            "confirmed_revision": 1,
            "confirmed_sha256": "2" * 64,
            "exact_title": "工作汇报",
            "document_type": "work_report",
            "document_control": {"render_template_id": "standalone-work-report"},
        },
        "review_policy": {"kind": "local_confirmation"},
        "launch_profile_snapshot": {
            "id": "content-work-report",
            "team_id": "content-creator-team",
            "stages": [*deepcopy(tasks), deepcopy(descriptor)],
            "review_policy": {"kind": "local_confirmation"},
        },
        "pending_system_stage": descriptor,
        "pending_system_stage_result": "generated_valid",
        "canonical_document_ref": {
            "artifact_id": "polish:1",
            "sha256": "a" * 64,
            "brief_revision": 1,
            "brief_sha256": "2" * 64,
        },
        "approved_stage_artifact_refs": {"polish": {"artifact_id": "polish:1", "sha256": "a" * 64}},
        "local_stage_confirmations": [],
        "stage_artifacts": [semantic_artifact, artifact],
        "stage_attempt_counters": {"polish": 1, "delivery": 1},
        "stage_attempt_reservations": [semantic_reservation, system_reservation],
        "current_stage_attempt_reservation": system_reservation,
        "delivery_attempt_counter": 1,
        "delivery_attempt_reservations": [delivery_reservation],
        "current_delivery_attempt_reservation": delivery_reservation,
        "current_stage_artifact_ref": {
            "artifact_id": artifact["artifact_id"],
            "sha256": artifact["sha256"],
            "stage_attempt": 1,
        },
        "current_delivery_manifest_ref": {
            "artifact_id": artifact["artifact_id"],
            "sha256": artifact["sha256"],
            "stage_attempt": 1,
            "delivery_attempt": 1,
            "delivery_binding_path": artifact["payload"]["delivery_binding_path"],
            "delivery_binding_sha256": binding_sha256,
        },
        "validation": {"status": "pass", "blocking_count": 0},
    }
    stored = write_run(tmp_path, run)
    context = {
        "session_id": session_id,
        "run_id": run_id,
        "stage_id": "delivery",
        "stage_attempt": 1,
        "artifact_id": artifact["artifact_id"],
        "artifact_sha256": artifact["sha256"],
        "delivery_attempt": 1,
        "delivery_binding_path": artifact["payload"]["delivery_binding_path"],
        "delivery_binding_sha256": binding_sha256,
        "document_path": document_path,
        "document_sha256": document_sha256,
        "delivery_dir": delivery_dir,
        "quality_path": quality_path,
        "quality_report": {"schema_version": "expert-standalone-quality/v1", "status": "passed", "checks": []},
        "binding_path": binding_path,
        "attempt_root": attempt_root,
    }
    return stored, context


def _action_body(run: dict, context: dict, *, key: str, **extra) -> dict:
    body = {
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "expected_version": run["version"],
        "stage_id": context["stage_id"],
        "stage_attempt": context["stage_attempt"],
        "artifact_id": context["artifact_id"],
        "artifact_sha256": context["artifact_sha256"],
        "delivery_attempt": context["delivery_attempt"],
        "delivery_binding_sha256": context["delivery_binding_sha256"],
        "document_sha256": context["document_sha256"],
        "idempotency_key": key,
    }
    body.update(extra)
    return body


def test_delivery_confirmation_is_hash_bound_and_only_then_completes(monkeypatch, tmp_path):
    from api.expert_teams import runtime

    run, context = _delivery_fixture(tmp_path)
    calls = []

    def validate(_workspace, _run):
        calls.append(_run["version"])
        return deepcopy(context)

    monkeypatch.setattr("api.expert_teams.standalone_delivery.validate_standalone_delivery_context", validate)
    confirmed = runtime.confirm_standalone_expert_team_delivery(
        tmp_path,
        _action_body(run, context, key="confirm-delivery-1"),
    )

    assert confirmed["workflow_state"] == "completed"
    assert len(calls) >= 2
    assert confirmed["local_delivery_confirmation"] == {
        "schema_version": "local-delivery-confirmation/v1",
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "stage_id": "delivery",
        "stage_attempt": 1,
        "artifact_id": context["artifact_id"],
        "artifact_sha256": context["artifact_sha256"],
        "delivery_attempt": 1,
        "delivery_binding_sha256": context["delivery_binding_sha256"],
        "document_sha256": context["document_sha256"],
        "confirmed_at": confirmed["local_delivery_confirmation"]["confirmed_at"],
    }
    assert confirmed["delivery_gate"]["status"] == "passed"
    assert confirmed["completion_integrity"]["status"] == "valid"


def test_delivery_confirmation_rejects_drift_between_locked_snapshots(monkeypatch, tmp_path):
    from api.expert_teams import runtime

    run, context = _delivery_fixture(tmp_path)
    changed = deepcopy(context)
    changed["document_sha256"] = "9" * 64
    snapshots = iter((deepcopy(context), changed))
    monkeypatch.setattr(
        "api.expert_teams.standalone_delivery.validate_standalone_delivery_context",
        lambda *_args: next(snapshots),
    )

    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.confirm_standalone_expert_team_delivery(
            tmp_path,
            _action_body(run, context, key="confirm-delivery-drift"),
        )

    assert error.value.code == "delivery_changed_during_confirmation"
    assert error.value.run["workflow_state"] == "awaiting_review"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("stage_attempt", 2, "stale_stage_attempt"),
        ("artifact_id", "delivery:2", "stale_artifact"),
        ("artifact_sha256", "8" * 64, "stale_artifact_hash"),
        ("delivery_attempt", 2, "stale_delivery_attempt"),
        ("delivery_binding_sha256", "7" * 64, "stale_delivery_binding"),
        ("document_sha256", "6" * 64, "stale_document_hash"),
    ],
)
def test_delivery_confirmation_rejects_every_stale_identity(monkeypatch, tmp_path, field, value, code):
    from api.expert_teams import runtime

    run, context = _delivery_fixture(tmp_path)
    monkeypatch.setattr(
        "api.expert_teams.standalone_delivery.validate_standalone_delivery_context",
        lambda *_args: deepcopy(context),
    )
    body = _action_body(run, context, key=f"confirm-stale-{field}")
    body[field] = value

    with pytest.raises(runtime.ExpertTeamStateConflict) as error:
        runtime.confirm_standalone_expert_team_delivery(tmp_path, body)

    assert error.value.code == code


def test_delivery_revision_invalidates_current_delivery_and_returns_to_semantic_review(monkeypatch, tmp_path):
    from api.expert_teams import runtime

    run, context = _delivery_fixture(tmp_path)
    monkeypatch.setattr(
        "api.expert_teams.standalone_delivery.validate_standalone_delivery_context",
        lambda *_args: deepcopy(context),
    )
    revised = runtime.request_standalone_expert_team_delivery_revision(
        tmp_path,
        _action_body(
            run,
            context,
            key="revise-delivery-1",
            feedback="请补充风险分析，并重新检查结论与来源材料是否一致。",
        ),
    )

    assert revised["workflow_state"] == "ready_to_generate"
    assert revised["current_stage"]["task_id"] == "polish"
    assert revised["current_delivery_manifest_ref"] is None
    assert revised["current_delivery_attempt_reservation"] is None
    assert revised["current_stage_artifact_ref"] is None
    assert revised["canonical_document_ref"] is None
    assert "polish" not in revised["approved_stage_artifact_refs"]
    assert revised["delivery_attempt_reservations"][-1]["status"] == "invalidated"
    assert revised["delivery_revision_feedback"][-1]["feedback"].startswith("请补充风险分析")


def test_delivery_revision_requires_nonempty_feedback(monkeypatch, tmp_path):
    from api.expert_teams import runtime

    run, context = _delivery_fixture(tmp_path)
    monkeypatch.setattr(
        "api.expert_teams.standalone_delivery.validate_standalone_delivery_context",
        lambda *_args: deepcopy(context),
    )
    with pytest.raises(ValueError, match="feedback"):
        runtime.request_standalone_expert_team_delivery_revision(
            tmp_path,
            _action_body(run, context, key="revise-delivery-empty", feedback="   "),
        )


@pytest.mark.parametrize("target", ["document", "folder"])
def test_delivery_open_resolves_only_server_owned_target(monkeypatch, tmp_path, target):
    from api.expert_teams import runtime

    run, context = _delivery_fixture(tmp_path)
    expected = context["document_path"] if target == "document" else context["delivery_dir"]
    monkeypatch.setattr(
        "api.expert_teams.standalone_delivery.validate_standalone_delivery_context",
        lambda *_args: deepcopy(context),
    )
    monkeypatch.setattr(
        "api.expert_teams.standalone_delivery.resolve_standalone_open_target",
        lambda _workspace, _run, requested: expected if requested == target else None,
    )

    resolved = runtime.resolve_standalone_expert_team_delivery_open(
        tmp_path,
        _action_body(run, context, key=f"open-{target}", target=target),
    )

    assert resolved == {"target": target, "path": expected}


def test_delivery_open_rejects_client_path_and_unknown_target(monkeypatch, tmp_path):
    from api.expert_teams import runtime

    run, context = _delivery_fixture(tmp_path)
    monkeypatch.setattr(
        "api.expert_teams.standalone_delivery.validate_standalone_delivery_context",
        lambda *_args: deepcopy(context),
    )
    body = _action_body(run, context, key="open-unsafe", target="document")
    body["path"] = "/tmp/attacker.docx"
    with pytest.raises(ValueError, match="path"):
        runtime.resolve_standalone_expert_team_delivery_open(tmp_path, body)

    body = _action_body(run, context, key="open-unknown", target="preview")
    with pytest.raises(ValueError, match="target"):
        runtime.resolve_standalone_expert_team_delivery_open(tmp_path, body)

    for field in ("document_path", "binding_path", "client_note"):
        body = _action_body(run, context, key=f"open-extra-{field}", target="document")
        body[field] = "/tmp/attacker.docx" if field.endswith("path") else "ignored"
        with pytest.raises(ValueError, match="unknown fields"):
            runtime.resolve_standalone_expert_team_delivery_open(tmp_path, body)


def test_delivery_view_exposes_one_complete_binding_and_local_actions(tmp_path):
    from api.expert_teams.view import expert_team_run_view

    run, context = _delivery_fixture(tmp_path)
    view = expert_team_run_view(run)

    assert view["public_state"] == "awaiting_delivery_confirmation"
    assert view["allowed_actions"] == [
        "delivery_open_document",
        "delivery_open_folder",
        "delivery_revise",
        "delivery_confirm",
    ]
    assert view["delivery_action_binding"] == {
        field: _action_body(run, context, key="unused")[field]
        for field in (
            "session_id",
            "run_id",
            "expected_version",
            "stage_id",
            "stage_attempt",
            "artifact_id",
            "artifact_sha256",
            "delivery_attempt",
            "delivery_binding_sha256",
            "document_sha256",
        )
    }
    assert view["standalone_delivery"]["document_name"] == "工作汇报.docx"
    assert view["standalone_delivery"]["automatic_check_summary"]["status"] == "passed"


@pytest.mark.parametrize("corruption", ["missing_stage_ledger", "duplicate_delivery_ledger", "stale_payload"])
def test_delivery_view_fails_closed_when_durable_identity_is_not_unique(tmp_path, corruption):
    from api.expert_teams.view import expert_team_run_view

    run, _context = _delivery_fixture(tmp_path)
    if corruption == "missing_stage_ledger":
        run["stage_attempt_reservations"] = run["stage_attempt_reservations"][:1]
    elif corruption == "duplicate_delivery_ledger":
        run["delivery_attempt_reservations"].append(deepcopy(run["delivery_attempt_reservations"][0]))
    else:
        run["stage_artifacts"][-1]["payload"]["document_sha256"] = "9" * 64

    view = expert_team_run_view(run)

    assert view["delivery_action_binding"] is None
    assert not any(action.startswith("delivery_") for action in view["allowed_actions"])


def test_delivery_open_route_never_accepts_or_returns_a_client_path(monkeypatch, tmp_path):
    from api import expert_teams, routes

    document = tmp_path / "document.docx"
    document.write_bytes(b"docx")
    opened = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _session_id: tmp_path)
    monkeypatch.setattr(
        expert_teams,
        "resolve_standalone_expert_team_delivery_open",
        lambda _workspace, body: {"target": body["target"], "path": document},
    )
    monkeypatch.setattr(
        routes,
        "_open_expert_team_delivery_target",
        lambda path, target: opened.append((Path(path), target)),
    )

    response = _post(
        routes,
        "/api/expert-teams/delivery/open",
        {"session_id": "sid", "run_id": "run", "target": "document"},
    )

    assert response.status == 200
    assert response.json_body() == {"ok": True, "target": "document"}
    assert opened == [(document, "document")]
    assert "path" not in response.json_body()


def test_delivery_revise_route_immediately_dispatches_the_semantic_retry(monkeypatch, tmp_path):
    from api import expert_teams, routes

    revised = {"run_id": "run-1", "session_id": "sid-1", "workflow_state": "ready_to_generate"}
    calls = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _session_id: tmp_path)
    monkeypatch.setattr(
        expert_teams,
        "request_standalone_expert_team_delivery_revision",
        lambda _workspace, body: calls.append(("revise", body["feedback"])) or revised,
    )
    monkeypatch.setattr(
        routes,
        "_start_expert_team_execution",
        lambda _workspace, run, _body: (calls.append(("start", run["run_id"])) or ({"ok": True, "run": run}, 200)),
    )
    monkeypatch.setattr(expert_teams, "expert_team_catalog", lambda: {"teams": []})

    response = _post(
        routes,
        "/api/expert-teams/delivery/revise",
        {"session_id": "sid-1", "run_id": "run-1", "feedback": "重新核对数据来源"},
    )

    assert response.status == 200
    assert response.json_body()["run"]["workflow_state"] == "ready_to_generate"
    assert calls == [("revise", "重新核对数据来源"), ("start", "run-1")]


def test_delivery_confirm_route_never_calls_enterprise_approval(monkeypatch, tmp_path):
    from api import expert_teams, routes

    completed = {"run_id": "run-1", "session_id": "sid-1", "workflow_state": "completed"}
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _session_id: tmp_path)
    monkeypatch.setattr(
        expert_teams,
        "confirm_standalone_expert_team_delivery",
        lambda _workspace, _body: completed,
    )
    monkeypatch.setattr(
        expert_teams,
        "approve_expert_team_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("enterprise approval must stay at zero")),
    )
    monkeypatch.setattr(expert_teams, "expert_team_catalog", lambda: {"teams": []})

    response = _post(
        routes,
        "/api/expert-teams/delivery/confirm",
        {"session_id": "sid-1", "run_id": "run-1"},
    )

    assert response.status == 200
    assert response.json_body()["run"]["workflow_state"] == "completed"
