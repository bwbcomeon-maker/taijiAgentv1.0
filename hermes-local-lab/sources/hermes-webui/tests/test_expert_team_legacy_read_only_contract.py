import json
import io
from urllib.parse import urlparse


def test_legacy_expert_run_get_is_read_only_and_skips_runtime_reconciliation(monkeypatch, tmp_path):
    from api import routes

    run = {
        "schema_version": 1,
        "run_id": "legacy-expert-1",
        "session_id": "sid-legacy-expert",
        "team_id": "content-creator-team",
        "workflow_state": "starting",
        "current_stage_index": 0,
        "tasks": [],
        "questions": [],
        "members": [],
    }
    for name in (
        "_reconcile_expired_expert_team_start",
        "_reconcile_expert_team_orphan_cleanup",
        "_reconcile_expert_team_cancelling_unknown_start",
    ):
        monkeypatch.setattr(
            routes,
            name,
            lambda *_args, _name=name, **_kwargs: (_ for _ in ()).throw(
                AssertionError(f"legacy GET must not call {_name}")
            ),
        )

    viewed = routes._expert_team_run_with_execution_truth(tmp_path, run)

    assert viewed["read_only"] is True
    assert viewed["workflow_state"] == "starting"
    assert viewed["view"]["actions"]["can_start_generation"] is False


def test_legacy_writeflow_read_and_list_never_rewrite_run_json(monkeypatch, tmp_path):
    from api import routes

    monkeypatch.setattr(routes, "_writeflow_image_generation_ready", lambda: False)
    path = routes._writeflow_run_path(tmp_path, "legacy-writeflow-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "run_id": "legacy-writeflow-1",
        "session_id": "sid-legacy-writeflow",
        "team_id": "content-creator-team",
        "project_slug": "legacy-project",
        "title": "历史写作任务",
        "status": "running",
        "tasks": [],
        "artifacts": [],
    }
    path.write_text(json.dumps(original, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    before = path.read_bytes()

    run, error = routes._writeflow_read_run(tmp_path, "legacy-writeflow-1")
    listed = routes._writeflow_list_runs(tmp_path)

    assert error is None and run["run_id"] == "legacy-writeflow-1"
    assert listed and listed[0]["run_id"] == "legacy-writeflow-1"
    assert path.read_bytes() == before


def test_legacy_writeflow_recover_query_never_materializes_a_new_run(monkeypatch, tmp_path):
    from api import routes

    monkeypatch.setattr(routes, "_writeflow_workspace", lambda _sid=None: tmp_path)
    monkeypatch.setattr(routes, "_writeflow_list_runs", lambda _workspace, _project=None: [])
    monkeypatch.setattr(routes, "_writeflow_find_session_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes,
        "_writeflow_ensure_session_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy recover GET must not create a run")
        ),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, extra_headers=None: {"status": status, "payload": payload},
    )

    response = routes.handle_get(
        object(),
        urlparse("/api/writeflow/runs?session_id=sid-legacy&recover=1"),
    )

    assert response["status"] == 200
    assert response["payload"]["session_run"] is None
    assert response["payload"]["recovered_session_run"] is False
    assert not routes._writeflow_runs_dir(tmp_path).exists()


def test_legacy_writeflow_compose_endpoint_is_read_only(monkeypatch):
    from api import routes

    raw = json.dumps({"session_id": "sid-legacy", "action": "start", "project": "legacy"}).encode()

    class Handler:
        headers = {"Content-Length": str(len(raw))}
        rfile = io.BytesIO(raw)

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "_writeflow_compose_message",
        lambda _body: (_ for _ in ()).throw(AssertionError("legacy compose must not mutate")),
    )
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, extra_headers=None: {"status": status, "payload": payload},
    )

    response = routes.handle_post(Handler(), urlparse("/api/writeflow/compose"))

    assert response["status"] == 409
    assert response["payload"]["code"] == "legacy_read_only"


def _persist_legacy_writeflow(routes, workspace, *, run_id="legacy-writeflow-active"):
    path = routes._writeflow_run_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "session_id": "sid-legacy-active",
        "team_id": "content-creator-team",
        "project_slug": "legacy-project",
        "artifact_root": "写作项目/legacy-project",
        "title": "历史写作任务",
        "status": "running",
        "tasks": [],
        "artifacts": [],
    }
    path.write_text(json.dumps(run, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return run, path


def test_legacy_writeflow_never_injects_execution_environment(tmp_path):
    from api import routes

    _persist_legacy_writeflow(routes, tmp_path)

    env = routes._writeflow_tool_env_for_session(tmp_path, "sid-legacy-active")

    assert env == {"HERMES_WORKSPACE": str(tmp_path.resolve())}


def test_legacy_writeflow_never_redirects_file_writes_or_changes_run(tmp_path):
    from api import routes

    _run, path = _persist_legacy_writeflow(routes, tmp_path)
    before = path.read_bytes()

    routing = routes._writeflow_artifact_target_for_file_write(
        tmp_path,
        "sid-legacy-active",
        "draft.md",
    )

    assert routing is None
    assert path.read_bytes() == before


def test_legacy_writeflow_direct_artifact_registration_is_noop(tmp_path):
    from api import routes

    run, path = _persist_legacy_writeflow(routes, tmp_path)
    artifact_path = tmp_path / "articles" / "legacy-project" / "draft.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("历史文档", encoding="utf-8")
    before = path.read_bytes()

    registered = routes._writeflow_register_artifact(
        tmp_path,
        run,
        {"id": "draft", "label": "历史初稿", "path": "articles/legacy-project/draft.md"},
    )

    assert registered is None
    assert path.read_bytes() == before
