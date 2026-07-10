import io
import json
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest


class _Handler:
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

    def json_body(self) -> dict:
        return json.loads(bytes(self.body).decode("utf-8"))


def _post(routes, path: str, body: dict) -> _Handler:
    handler = _Handler(body)
    routes.handle_post(handler, urlparse(path))
    return handler


def _control(run: dict, key: str, **extra) -> dict:
    return {
        "run_id": run["run_id"],
        "session_id": run["session_id"],
        "expected_version": run["version"],
        "stage_id": run["current_stage"]["task_id"],
        "idempotency_key": key,
        **extra,
    }


def _ready_run(expert_teams, workspace, session_id: str) -> dict:
    run = expert_teams.start_expert_team(
        workspace,
        {
            "session_id": session_id,
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    required_answers = {
        str(question.get("id")): "已确认"
        for question in run.get("questions") or []
        if question.get("required")
    }
    required = expert_teams.answer_expert_team(
        workspace,
        _control(run, "answer-required", answers=required_answers),
    )
    return expert_teams.answer_expert_team(
        workspace,
        _control(
            required,
            "answer-optional",
            answers={"optional_context": ""},
            skip_optional=True,
        ),
    )


def _started(expert_teams, workspace, run: dict, stream_id: str) -> dict:
    reserved = expert_teams.reserve_expert_team_execution_start(
        workspace,
        run["run_id"],
        expected_version=run["version"],
    )
    return expert_teams.mark_expert_team_execution_started(
        workspace,
        run["run_id"],
        {
            "stream_id": stream_id,
            "turn_id": f"turn-{stream_id}",
            "execution_start_id": reserved["execution_start_id"],
        },
    )


def _expired_start(
    expert_teams,
    workspace,
    run: dict,
    *,
    adapter_name: str,
    dispatch_state: str = "dispatching",
) -> dict:
    from api.expert_teams.storage import write_run

    reserved = expert_teams.reserve_expert_team_execution_start(
        workspace,
        run["run_id"],
        expected_version=run["version"],
    )
    reserved["execution_start_deadline_at"] = time.time() - 1
    reserved["execution_runtime_adapter"] = adapter_name
    reserved["execution_start_dispatch_state"] = dispatch_state
    write_run(workspace, reserved)
    return reserved


def _configure_route(monkeypatch, routes, workspace, session):
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: workspace)
    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model or "test", provider, False),
    )


def _valid_plan_content() -> str:
    return (
        "阶段摘要：已形成专家团执行计划。\n"
        "正文草稿：本阶段只确认材料定位、使用对象、结构边界和后续分工，不直接起草完整正文。\n"
        "待补充事项：请补充具体数据。\n"
        "建议下一步：进入素材整理。"
    )


def test_start_request_uses_persisted_start_id_as_idempotency_key(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, "sid-start-key")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    captured = []

    def capture_start(self, request):
        captured.append(request)
        return runtime_adapter.RunStartResult(
            run_id="remote-start-key",
            session_id=request.session_id,
            stream_id="remote-start-stream",
        )

    monkeypatch.setattr(runtime_adapter.LegacyJournalRuntimeAdapter, "start_run", capture_start)
    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert status == 200
    assert captured[0].idempotency_key == payload["run"]["execution_start_id"]
    assert captured[0].metadata["execution_start_id"] == captured[0].idempotency_key


def test_http_runner_start_sends_idempotency_key_header(monkeypatch):
    from api.runner_client import HttpRunnerClient
    from api.runtime_adapter import StartRunRequest

    client = HttpRunnerClient(base_url="http://runner.local")
    requests = []
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda request: requests.append(request)
        or {"run_id": "remote", "session_id": "sid", "stream_id": "remote"},
    )
    client.start_run(StartRunRequest(session_id="sid", message="hello", idempotency_key="start-key"))
    assert requests[0].get_header("Idempotency-key") == "start-key"


def test_expired_start_binds_remote_run_found_by_start_key(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, "sid-start-bind")
    expired = _expired_start(
        expert_teams,
        tmp_path,
        ready,
        adapter_name="RunnerRuntimeAdapter",
    )
    lookups = []

    class FakeAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            lookups.append((key, session_id))
            return runtime_adapter.RunStatus(
                run_id="remote-already-created",
                session_id=session_id,
                status="running",
            )

        def start_run(self, _request):
            raise AssertionError("reconcile must not create a second remote run")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())
    observed = routes._expert_team_run_with_execution_truth(tmp_path, expired)
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert lookups == [(expired["execution_start_id"], expired["session_id"])]
    assert observed["workflow_state"] == "generating"
    assert stored["execution_runtime_run_id"] == "remote-already-created"
    assert stored["execution_start_id"] == expired["execution_start_id"]


def test_expired_start_on_old_runner_stays_unknown_without_second_start(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, "sid-old-runner")
    expired = _expired_start(
        expert_teams,
        tmp_path,
        ready,
        adapter_name="LegacyJournalRuntimeAdapter",
        dispatch_state="",
    )
    starts = []

    class OldAdapter:
        def start_run(self, request):
            starts.append(request)
            raise AssertionError("old runner must not be retried without a proven idempotency contract")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: OldAdapter())
    observed = routes._expert_team_run_with_execution_truth(tmp_path, expired)
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert starts == []
    assert observed["workflow_state"] == "starting"
    assert stored["workflow_state"] == "starting"
    assert stored["execution_start_id"] == expired["execution_start_id"]


def test_explicit_remote_not_found_is_the_only_expired_start_failure(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, "sid-start-not-found")
    expired = _expired_start(
        expert_teams,
        tmp_path,
        ready,
        adapter_name="RunnerRuntimeAdapter",
    )
    lookups = []

    class MissingAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            lookups.append((key, session_id))
            return runtime_adapter.RunStatus(run_id=key, session_id=session_id, status="not_found")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: MissingAdapter())
    observed = routes._expert_team_run_with_execution_truth(tmp_path, expired)
    assert lookups == [(expired["execution_start_id"], expired["session_id"])]
    assert observed["workflow_state"] == "start_failed"


def test_rejected_start_cleanup_keeps_remote_identity_pending(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, "sid-start-cleanup")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)

    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "start_run",
        lambda self, request: runtime_adapter.RunStartResult(
            run_id="remote-cleanup-pending",
            session_id=request.session_id,
            stream_id="",
        ),
    )
    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "cancel_run",
        lambda self, _run_id: runtime_adapter.ControlResult(False, status="timeout", safe_message="timeout"),
    )
    _payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert status == 502
    assert stored["orphan_runtime_run_id"] == "remote-cleanup-pending"
    assert stored["execution_cleanup_status"] == "pending"


def test_cancel_timeout_is_unknown_not_rejected(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, "sid-cancel-timeout")
    generating = _started(expert_teams, tmp_path, ready, "stream-cancel-timeout")
    session = SimpleNamespace(session_id=generating["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)

    class TimeoutAdapter:
        def cancel_run(self, _run_id):
            raise TimeoutError("runtime timeout")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: TimeoutAdapter())
    handler = _post(routes, "/api/expert-teams/cancel", _control(generating, "cancel-timeout"))
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert handler.status == 202
    assert stored["workflow_state"] == "cancelling"
    assert stored["cancel_outcome"] == "unknown"
    assert stored["execution_stream_id"] == generating["execution_stream_id"]


def test_cancel_intent_crash_is_retried_from_remote_running(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, "sid-cancel-crash")
    generating = _started(expert_teams, tmp_path, ready, "stream-cancel-crash")
    with pytest.raises(SystemExit):
        expert_teams.cancel_expert_team(
            tmp_path,
            _control(generating, "cancel-crash"),
            cancel_callback=lambda _run: (_ for _ in ()).throw(SystemExit("crash")),
        )
    persisted = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert persisted["workflow_state"] == "cancelling"
    calls = []
    remote_status = "running"

    class RunningAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=persisted["session_id"],
                status=remote_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(run_id=run_id, events=[], cursor=cursor)

        def cancel_run(self, run_id):
            calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunningAdapter())
    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, persisted)
    assert calls == [persisted["execution_runtime_run_id"]]
    assert reconciled["workflow_state"] == "cancelling"
    assert reconciled["cancel_outcome"] == "accepted"
    remote_status = "cancelled"
    settled = routes._expert_team_run_with_execution_truth(tmp_path, reconciled)
    assert settled["workflow_state"] == "cancelled"


@pytest.mark.parametrize(
    ("remote_status", "expected_state"),
    [
        ("running", "cancelling"),
        ("completed", "cancelling"),
        ("cancelled", "cancelled"),
        ("unknown", "cancelling"),
    ],
)
def test_unknown_cancel_reconciles_running_completed_cancelled(
    monkeypatch,
    tmp_path,
    remote_status,
    expected_state,
):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, f"sid-cancel-{remote_status}")
    generating = _started(expert_teams, tmp_path, ready, f"stream-cancel-{remote_status}")
    cancelling = dict(generating)
    cancelling.update(
        {
            "workflow_state": "cancelling",
            "execution_runtime_adapter": "RunnerRuntimeAdapter",
            "execution_runtime_run_id": f"remote-cancel-{remote_status}",
            "cancel_request_id": f"cancel-{remote_status}",
            "cancel_previous_state": "generating",
            "cancel_runtime_accepted": False,
            "cancel_outcome": "unknown",
        }
    )
    write_run(tmp_path, cancelling)
    cancel_calls = []
    current_status = remote_status
    observe_calls = 0

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=cancelling["session_id"],
                status=current_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            nonlocal observe_calls
            observe_calls += 1
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                events=(
                    [
                        {
                            "event_id": "event-1",
                            "sequence": 1,
                            "content": _valid_plan_content(),
                        }
                    ]
                    if current_status == "completed" and observe_calls == 1
                    else []
                ),
                cursor=cursor,
                has_more=False if current_status == "completed" and observe_calls > 1 else None,
                snapshot_complete=(
                    True if current_status == "completed" and observe_calls > 1 else False
                ),
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())
    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, cancelling)
    stored = expert_teams.read_expert_team_run(tmp_path, cancelling["run_id"])
    assert reconciled["workflow_state"] == expected_state
    assert stored["workflow_state"] == expected_state
    assert stored["workflow_state"] != "failed"
    if remote_status == "running":
        assert cancel_calls == [cancelling["execution_runtime_run_id"]]
        current_status = "cancelled"
        settled = routes._expert_team_run_with_execution_truth(tmp_path, reconciled)
        assert settled["workflow_state"] == "cancelled"
    if remote_status == "completed":
        settled = routes._expert_team_run_with_execution_truth(tmp_path, reconciled)
        assert settled["workflow_state"] == "awaiting_review"
        assert settled["stage_outputs"][-1]["content"] == _valid_plan_content()
