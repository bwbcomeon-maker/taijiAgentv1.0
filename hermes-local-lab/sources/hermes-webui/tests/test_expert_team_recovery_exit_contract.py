"""RED contracts for bounded expert-team recovery exits.

These tests intentionally describe the release contract before production code
implements it.  Recovery may wait for remote truth, but it must never create a
second side effect or leave a run in an actionless unknown state forever.
"""

from __future__ import annotations

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


def _configure_route(monkeypatch, routes, workspace, session) -> None:
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: workspace)
    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model or "test", provider, False),
    )


def _pending_orphan_run(expert_teams, workspace, session_id: str) -> dict:
    ready = _ready_run(expert_teams, workspace, session_id)
    reserved = expert_teams.reserve_expert_team_execution_start(
        workspace,
        ready["run_id"],
        expected_version=ready["version"],
    )
    expert_teams.mark_expert_team_execution_start_adapter(
        workspace,
        ready["run_id"],
        execution_start_id=reserved["execution_start_id"],
        runtime_adapter="RunnerRuntimeAdapter",
    )
    return expert_teams.mark_expert_team_execution_start_failed(
        workspace,
        ready["run_id"],
        "启动回写失败",
        execution_start_id=reserved["execution_start_id"],
        orphan_runtime_run_id="remote-orphan",
        orphan_runtime_adapter="RunnerRuntimeAdapter",
        execution_cleanup_status="pending",
    )


def _started_runner_run(expert_teams, workspace, session_id: str) -> dict:
    ready = _ready_run(expert_teams, workspace, session_id)
    reserved = expert_teams.reserve_expert_team_execution_start(
        workspace,
        ready["run_id"],
        expected_version=ready["version"],
    )
    expert_teams.mark_expert_team_execution_start_adapter(
        workspace,
        ready["run_id"],
        execution_start_id=reserved["execution_start_id"],
        runtime_adapter="RunnerRuntimeAdapter",
    )
    return expert_teams.mark_expert_team_execution_started(
        workspace,
        ready["run_id"],
        {
            "stream_id": "remote-running",
            "runtime_run_id": "remote-running",
            "runtime_adapter": "RunnerRuntimeAdapter",
            "execution_start_id": reserved["execution_start_id"],
        },
    )


def _unknown_cancelling_run(expert_teams, runtime_adapter, workspace, session_id: str, key: str):
    generating = _started_runner_run(expert_teams, workspace, session_id)
    body = _control(generating, key)
    cancelling = expert_teams.cancel_expert_team(
        workspace,
        body,
        cancel_callback=lambda _run: runtime_adapter.ControlResult(
            False,
            status="timeout",
            safe_message="runtime timeout",
        ),
    )
    return cancelling, body


def _uncertain_start(expert_teams, workspace, session_id: str) -> dict:
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, workspace, session_id)
    reserved = expert_teams.reserve_expert_team_execution_start(
        workspace,
        ready["run_id"],
        expected_version=ready["version"],
    )
    reserved = expert_teams.mark_expert_team_execution_start_adapter(
        workspace,
        ready["run_id"],
        execution_start_id=reserved["execution_start_id"],
        runtime_adapter="RunnerRuntimeAdapter",
    )
    reserved["execution_start_deadline_at"] = time.time() - 1
    write_run(workspace, reserved)
    return reserved


class _CrashAfterReservation(BaseException):
    pass


def test_reservation_persists_planned_adapter_before_crash(monkeypatch, tmp_path):
    """A crash immediately after reserve must not leave adapter identity blank."""
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, "sid-planned-adapter")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)

    class RunnerRuntimeAdapter:
        pass

    monkeypatch.setattr(runtime_adapter, "build_runtime_adapter", lambda **_kwargs: RunnerRuntimeAdapter())
    original_reserve = expert_teams.reserve_expert_team_execution_start

    def crash_after_persist(*args, **kwargs):
        original_reserve(*args, **kwargs)
        raise _CrashAfterReservation()

    monkeypatch.setattr(expert_teams, "reserve_expert_team_execution_start", crash_after_persist)

    with pytest.raises(_CrashAfterReservation):
        routes._start_expert_team_execution(tmp_path, ready, {})

    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert stored["workflow_state"] == "starting"
    assert stored["execution_runtime_adapter"] == "RunnerRuntimeAdapter"


def test_runner_start_requires_not_found_proof_before_remote_side_effect(monkeypatch, tmp_path):
    """A new Runner start is safe only after lookup explicitly proves not_found."""
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, "sid-preflight-not-found")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    events = []

    class RunnerRuntimeAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            events.append(("lookup", key, session_id))
            return runtime_adapter.RunStatus(run_id=key, session_id=session_id, status="not_found")

        def start_run(self, request):
            events.append(("start", request.idempotency_key, request.session_id))
            return runtime_adapter.RunStartResult(
                run_id="remote-preflight",
                session_id=request.session_id,
                stream_id="remote-preflight",
            )

    monkeypatch.setattr(runtime_adapter, "build_runtime_adapter", lambda **_kwargs: RunnerRuntimeAdapter())

    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})

    assert status == 200
    assert payload["run"]["workflow_state"] == "generating"
    assert [event[0] for event in events] == ["lookup", "start"]
    assert events[0][1] == events[1][1] == payload["run"]["execution_start_id"]


@pytest.mark.parametrize("lookup_outcome", ["unsupported", "unknown"])
def test_runner_without_proven_idempotency_lookup_fails_visibly_without_start(
    monkeypatch,
    tmp_path,
    lookup_outcome,
):
    """Unsupported/unknown lookup must be incompatible, never an endless start."""
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, f"sid-preflight-{lookup_outcome}")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    events = []

    class RunnerRuntimeAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            events.append(("lookup", key, session_id))
            if lookup_outcome == "unsupported":
                raise NotImplementedError("idempotency lookup unsupported")
            return runtime_adapter.RunStatus(run_id=key, session_id=session_id, status="unknown")

        def start_run(self, request):
            events.append(("start", request.idempotency_key, request.session_id))
            return runtime_adapter.RunStartResult(
                run_id="must-not-start",
                session_id=request.session_id,
                stream_id="must-not-start",
            )

    monkeypatch.setattr(runtime_adapter, "build_runtime_adapter", lambda **_kwargs: RunnerRuntimeAdapter())

    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])

    assert status >= 400
    assert [event[0] for event in events] == ["lookup"]
    assert stored["workflow_state"] == "start_failed"
    assert payload.get("code") == "runtime_incompatible"
    assert stored.get("last_execution_error")
    assert stored["view"]["presentation"]["detail"]


def test_pending_orphan_cleanup_blocks_retry_until_terminally_confirmed(tmp_path):
    """Regenerate cannot create a second run while orphan cleanup is uncertain."""
    from api import expert_teams
    from api.expert_teams.storage import write_run

    pending = _pending_orphan_run(expert_teams, tmp_path, "sid-orphan-gate")
    retry = _control(pending, "retry-orphan")

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as blocked:
        expert_teams.resume_expert_team(tmp_path, retry)

    assert blocked.value.code == "orphan_cleanup_pending"
    still_pending = expert_teams.read_expert_team_run(tmp_path, pending["run_id"])
    assert still_pending["workflow_state"] == "start_failed"
    assert still_pending["execution_cleanup_status"] == "pending"

    confirmed = dict(still_pending)
    confirmed["orphan_runtime_run_id"] = ""
    confirmed["execution_cleanup_status"] = "confirmed"
    write_run(tmp_path, confirmed)
    resumed = expert_teams.resume_expert_team(
        tmp_path,
        _control(confirmed, "retry-after-cleanup"),
    )
    assert resumed["workflow_state"] == "ready_to_generate"


def test_pending_orphan_is_cancelled_and_closed_during_truth_reconciliation(monkeypatch, tmp_path):
    """GET/retry reconciliation must consume cleanup=pending before allowing retry."""
    from api import expert_teams, routes, runtime_adapter

    pending = _pending_orphan_run(expert_teams, tmp_path, "sid-orphan-reconcile")
    status_sequence = iter(["running", "cancelled"])
    get_calls = []
    cancel_calls = []

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            get_calls.append(run_id)
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=pending["session_id"],
                status=next(status_sequence, "cancelled"),
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    first = routes._expert_team_run_with_execution_truth(tmp_path, pending)
    routes._expert_team_run_with_execution_truth(tmp_path, first)
    closed = expert_teams.read_expert_team_run(tmp_path, pending["run_id"])

    assert get_calls
    assert cancel_calls == ["remote-orphan"]
    assert closed.get("orphan_runtime_run_id") == ""
    assert closed.get("execution_cleanup_status") in {"confirmed", "cancelled", "completed", "not_found"}
    resumed = expert_teams.resume_expert_team(
        tmp_path,
        _control(closed, "retry-after-reconcile"),
    )
    assert resumed["workflow_state"] == "ready_to_generate"


def test_cancelling_unknown_view_exposes_discoverable_recovery_action(tmp_path):
    """The workbench must never show an actionless 'stopping' state."""
    from api import expert_teams, runtime_adapter

    cancelling, _body = _unknown_cancelling_run(
        expert_teams,
        runtime_adapter,
        tmp_path,
        "sid-cancel-action",
        "cancel-visible-retry",
    )
    view = expert_teams.expert_team_run_view(cancelling)
    presentation = view["presentation"]
    actions = [presentation.get("primary_action"), *(presentation.get("secondary_actions") or [])]
    visible_recovery = [
        action
        for action in actions
        if isinstance(action, dict) and action.get("id") in {"retry_cancel", "refresh"}
    ]

    assert visible_recovery
    assert all(str(action.get("label") or "").strip() for action in visible_recovery)


def test_cancelling_unknown_retries_exact_same_idempotent_request(monkeypatch, tmp_path):
    """A transport retry uses the exact same body/key and must retry, not 409."""
    from api import expert_teams, routes, runtime_adapter

    generating = _started_runner_run(expert_teams, tmp_path, "sid-cancel-same-key")
    body = _control(generating, "cancel-same-key")
    callback_calls = []

    def cancel_callback(_run):
        callback_calls.append("cancel")
        if len(callback_calls) == 1:
            return runtime_adapter.ControlResult(False, status="timeout", safe_message="timeout")
        return runtime_adapter.ControlResult(True, status="accepted")

    first = expert_teams.cancel_expert_team(tmp_path, body, cancel_callback=cancel_callback)
    assert first["workflow_state"] == "cancelling"
    assert first["cancel_outcome"] == "unknown"
    try:
        retried = expert_teams.cancel_expert_team(tmp_path, body, cancel_callback=cancel_callback)
    except expert_teams.ExpertTeamStateConflict as exc:
        pytest.fail(f"exact idempotent cancellation retry was rejected: {exc.code}")

    assert callback_calls == ["cancel", "cancel"]
    assert retried["workflow_state"] == "cancelling"
    assert retried["cancel_outcome"] == "accepted"

    class CancelledAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=retried["session_id"],
                status="cancelled",
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=retried["session_id"],
                events=[],
                cursor=cursor,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: CancelledAdapter())
    settled = routes._expert_team_run_with_execution_truth(tmp_path, retried)
    assert settled["workflow_state"] == "cancelled"


@pytest.mark.parametrize(
    ("lookup_status", "expected_http", "expected_state", "expected_cancel_calls"),
    [
        ("unknown", 202, "cancelling", []),
        ("running", 202, "cancelling", ["remote-found"]),
        ("not_found", 200, "cancelled", []),
    ],
)
def test_uncertain_start_cancel_requires_remote_truth_before_local_terminal(
    monkeypatch,
    tmp_path,
    lookup_status,
    expected_http,
    expected_state,
    expected_cancel_calls,
):
    """Unknown may wait; found must cancel remote; only not_found is locally safe."""
    from api import expert_teams, routes, runtime_adapter

    starting = _uncertain_start(expert_teams, tmp_path, f"sid-start-cancel-{lookup_status}")
    session = SimpleNamespace(session_id=starting["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    lookup_calls = []
    cancel_calls = []

    class RunnerRuntimeAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            lookup_calls.append((key, session_id))
            return runtime_adapter.RunStatus(
                run_id="remote-found" if lookup_status == "running" else key,
                session_id=session_id,
                status=lookup_status,
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=starting["session_id"],
                status="cancelled",
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=starting["session_id"],
                events=[],
                cursor=cursor,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    handler = _post(routes, "/api/expert-teams/cancel", _control(starting, f"cancel-{lookup_status}"))
    payload = handler.json_body()

    assert lookup_calls == [(starting["execution_start_id"], starting["session_id"])]
    assert handler.status == expected_http
    assert payload["run"]["workflow_state"] == expected_state
    assert cancel_calls == expected_cancel_calls
    if lookup_status == "unknown":
        assert payload["run"]["workflow_state"] != "cancelled"
    if lookup_status == "running":
        settled = routes._expert_team_run_with_execution_truth(tmp_path, payload["run"])
        assert settled["workflow_state"] == "cancelled"


@pytest.mark.parametrize("remote_status", ["failed", "error"])
def test_cancelling_unknown_remote_failure_reaches_terminal_state(monkeypatch, tmp_path, remote_status):
    """A failed remote run is terminal truth, not an eternal cancelling state."""
    from api import expert_teams, routes, runtime_adapter

    cancelling, _body = _unknown_cancelling_run(
        expert_teams,
        runtime_adapter,
        tmp_path,
        f"sid-cancel-terminal-{remote_status}",
        f"cancel-terminal-{remote_status}",
    )

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=cancelling["session_id"],
                status=remote_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=cancelling["session_id"],
                events=[],
                cursor=cursor,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, cancelling)
    stored = expert_teams.read_expert_team_run(tmp_path, cancelling["run_id"])

    assert reconciled["workflow_state"] in {"failed", "cancelled"}
    assert stored["workflow_state"] in {"failed", "cancelled"}
    assert stored["workflow_state"] != "cancelling"
