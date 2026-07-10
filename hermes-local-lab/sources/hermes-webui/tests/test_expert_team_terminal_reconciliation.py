"""RED contracts for terminal Runner reconciliation and cancellation safety."""

import io
import json
import subprocess
import sys
import time
from pathlib import Path
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


def _ready_run(expert_teams, workspace, *, session_id: str) -> dict:
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
    run = expert_teams.answer_expert_team(
        workspace,
        _control(run, f"required-{run['run_id']}", answers=required_answers),
    )
    return expert_teams.answer_expert_team(
        workspace,
        _control(
            run,
            f"optional-{run['run_id']}",
            answers={"optional_context": ""},
            skip_optional=True,
        ),
    )


def _remote_generating(expert_teams, workspace, *, session_id: str, runtime_run_id: str) -> dict:
    ready = _ready_run(expert_teams, workspace, session_id=session_id)
    reserved = expert_teams.reserve_expert_team_execution_start(
        workspace,
        ready["run_id"],
        expected_version=ready["version"],
    )
    return expert_teams.mark_expert_team_execution_started(
        workspace,
        ready["run_id"],
        {
            "stream_id": f"stream-{runtime_run_id}",
            "runtime_run_id": runtime_run_id,
            "runtime_adapter": "RunnerRuntimeAdapter",
            "execution_start_id": reserved["execution_start_id"],
        },
    )


def _expired_start(expert_teams, workspace, run: dict) -> dict:
    from api.expert_teams.storage import write_run

    reserved = expert_teams.reserve_expert_team_execution_start(
        workspace,
        run["run_id"],
        expected_version=run["version"],
    )
    reserved["execution_start_deadline_at"] = time.time() - 1
    reserved["execution_runtime_adapter"] = "RunnerRuntimeAdapter"
    reserved["execution_start_dispatch_state"] = "dispatching"
    return write_run(workspace, reserved)


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


def _token_event(*, event_id: str, text: str, run_id: str, session_id: str, sequence: int) -> dict:
    return {
        "event_id": event_id,
        "sequence": sequence,
        "type": "token.delta",
        "payload": {"text": text},
        "run_id": run_id,
        "session_id": session_id,
    }


def test_terminal_completion_waits_until_observation_reaches_status_last_event(monkeypatch, tmp_path):
    """A terminal status is not proof that the paginated result body is complete."""
    from api import expert_teams, routes, runtime_adapter

    session_id = "sid-terminal-page-catch-up"
    runtime_run_id = "remote-terminal-page-catch-up"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    first_page = _valid_plan_content()
    second_page = "\n补充说明：第二页是终态结果不可丢失的尾部。"
    requested_cursors = []

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=session_id,
                status="completed",
                last_event_id="event-2",
            )

        def observe_run(self, run_id, *, cursor=None):
            requested_cursors.append(cursor)
            if cursor is None:
                return runtime_adapter.RunEventStream(
                    run_id=run_id,
                    session_id=session_id,
                    events=[
                        _token_event(
                            event_id="event-1",
                            text=first_page,
                            run_id=run_id,
                            session_id=session_id,
                            sequence=1,
                        )
                    ],
                    request_cursor=cursor,
                    cursor="cursor-1",
                    last_event_id="event-1",
                )
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _token_event(
                        event_id="event-2",
                        text=second_page,
                        run_id=run_id,
                        session_id=session_id,
                        sequence=2,
                    )
                ],
                request_cursor=cursor,
                cursor="cursor-2",
                last_event_id="event-2",
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())

    first_poll = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    first_stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert first_poll["workflow_state"] == "generating"
    assert first_stored["execution_cursor"] == "cursor-1"
    assert first_stored["execution_public_output_buffer"] == first_page
    assert first_stored.get("stage_outputs") in (None, [])

    second_poll = routes._expert_team_run_with_execution_truth(tmp_path, first_stored)
    assert requested_cursors == [None, "cursor-1"]
    assert second_poll["workflow_state"] == "awaiting_review"
    assert second_poll["stage_outputs"][-1]["content"] == first_page + second_page


def test_terminal_reordered_duplicate_pages_are_ordered_once_without_truncation(monkeypatch, tmp_path):
    """Replay and transport reordering must not change the logical public result."""
    from api import expert_teams, routes, runtime_adapter

    session_id = "sid-terminal-reordered-pages"
    runtime_run_id = "remote-terminal-reordered-pages"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    full_content = _valid_plan_content()
    cut_1 = len(full_content) // 3
    cut_2 = (len(full_content) * 2) // 3
    parts = (full_content[:cut_1], full_content[cut_1:cut_2], full_content[cut_2:])
    status_calls = 0

    class FakeAdapter:
        def get_run(self, run_id):
            nonlocal status_calls
            status_calls += 1
            if status_calls == 1:
                return runtime_adapter.RunStatus(
                    run_id=run_id,
                    session_id=session_id,
                    status="running",
                    last_event_id="event-1",
                )
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=session_id,
                status="completed",
                last_event_id="event-3",
            )

        def observe_run(self, run_id, *, cursor=None):
            if cursor is None:
                events = [
                    _token_event(
                        event_id="event-1",
                        text=parts[0],
                        run_id=run_id,
                        session_id=session_id,
                        sequence=1,
                    )
                ]
                return runtime_adapter.RunEventStream(
                    run_id=run_id,
                    session_id=session_id,
                    events=events,
                    request_cursor=cursor,
                    cursor="cursor-1",
                    last_event_id="event-1",
                )
            events = [
                _token_event(
                    event_id="event-3",
                    text=parts[2],
                    run_id=run_id,
                    session_id=session_id,
                    sequence=3,
                ),
                _token_event(
                    event_id="event-2",
                    text=parts[1],
                    run_id=run_id,
                    session_id=session_id,
                    sequence=2,
                ),
                _token_event(
                    event_id="event-2",
                    text=parts[1],
                    run_id=run_id,
                    session_id=session_id,
                    sequence=2,
                ),
            ]
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=session_id,
                events=events,
                request_cursor=cursor,
                cursor="cursor-3",
                last_event_id="event-3",
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())

    first_poll = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    second_poll = routes._expert_team_run_with_execution_truth(tmp_path, first_poll)

    assert second_poll["stage_outputs"][-1]["content"] == full_content
    assert second_poll["workflow_state"] == "awaiting_review"
    assert second_poll["execution_seen_event_ids"] == ["event-1", "event-2", "event-3"]


@pytest.mark.parametrize("runtime_status", ["failed", "error", "errored"])
def test_cancelling_reconciles_remote_failure_instead_of_staying_forever(
    monkeypatch,
    tmp_path,
    runtime_status,
):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    session_id = f"sid-cancelling-{runtime_status}"
    runtime_run_id = f"remote-cancelling-{runtime_status}"
    cancelling = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    cancelling.update(
        {
            "workflow_state": "cancelling",
            "cancel_previous_state": "generating",
            "cancel_request_id": f"cancel-{runtime_status}",
            "cancel_outcome": "unknown",
            "cancel_runtime_accepted": False,
        }
    )
    cancelling = write_run(tmp_path, cancelling)

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=session_id,
                status=runtime_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=session_id,
                events=[],
                cursor=cursor,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())

    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, cancelling)
    assert reconciled["workflow_state"] == "failed"
    assert reconciled["workflow_state"] != "cancelling"


def test_cancel_expired_unknown_start_waits_for_lookup_then_cancels_real_remote_run(monkeypatch, tmp_path):
    """No remote id means cancel intent, not evidence that cancellation succeeded."""
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-cancel-unknown-start")
    expired = _expired_start(expert_teams, tmp_path, ready)
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    lookup_found = False
    remote_status = "running"
    cancel_calls = []

    class FakeAdapter:
        def find_run_by_idempotency_key(self, _key, *, session_id):
            if not lookup_found:
                return runtime_adapter.RunStatus(
                    run_id="",
                    session_id=session_id,
                    status="unknown",
                )
            return runtime_adapter.RunStatus(
                run_id="remote-found-after-cancel",
                session_id=session_id,
                status="running",
            )

        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=ready["session_id"],
                status=remote_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=ready["session_id"],
                events=[],
                cursor=cursor,
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(accepted=True, status="accepted")

    adapter = FakeAdapter()
    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: adapter)

    still_unknown = routes._expert_team_run_with_execution_truth(tmp_path, expired)
    assert still_unknown["workflow_state"] == "starting"

    handler = _post(
        routes,
        "/api/expert-teams/cancel",
        _control(still_unknown, "cancel-expired-unknown-start"),
    )
    after_cancel_request = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])

    assert handler.status == 202
    assert handler.json_body()["code"] == "cancel_pending"
    assert after_cancel_request["workflow_state"] != "cancelled"
    assert cancel_calls == []

    lookup_found = True
    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, after_cancel_request)
    assert cancel_calls == ["remote-found-after-cancel"]
    assert reconciled["workflow_state"] == "cancelling"
    remote_status = "cancelled"
    settled = routes._expert_team_run_with_execution_truth(tmp_path, reconciled)
    assert settled["workflow_state"] == "cancelled"


def test_remote_cancel_callback_runs_without_holding_run_os_file_lock(tmp_path):
    """External I/O must not extend the cross-process run-lock critical section."""
    from api import expert_teams, runtime_adapter

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-cancel-lock-scope",
        runtime_run_id="remote-cancel-lock-scope",
    )
    result_path = tmp_path / "independent-lock-acquired.txt"
    callback_observations = []
    probe = (
        "from pathlib import Path\n"
        "from api.expert_teams.storage import run_file_lock\n"
        "workspace = Path(__import__('sys').argv[1])\n"
        "run_id = __import__('sys').argv[2]\n"
        "result = Path(__import__('sys').argv[3])\n"
        "with run_file_lock(workspace, run_id):\n"
        "    result.write_text('acquired', encoding='utf-8')\n"
    )

    def cancel_callback(_run):
        process = subprocess.Popen(
            [sys.executable, "-c", probe, str(tmp_path), generating["run_id"], str(result_path)],
            cwd=Path(__file__).resolve().parents[1],
        )
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=2)
        callback_observations.append(process.returncode == 0 and result_path.exists())
        return runtime_adapter.ControlResult(accepted=True, status="accepted")

    expert_teams.cancel_expert_team(
        tmp_path,
        _control(generating, "cancel-lock-scope"),
        cancel_callback=cancel_callback,
    )

    assert callback_observations == [True]
    assert result_path.read_text(encoding="utf-8") == "acquired"
