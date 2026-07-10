"""RED contracts for bounded, throttled expert-team recovery.

Safety may require waiting for remote truth.  Waiting must still be durable,
rate-limited, user-recoverable, and must never silently release an orphan gate.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tests.test_expert_team_recovery_exit_contract import (
    _configure_route,
    _control,
    _pending_orphan_run,
    _post,
    _uncertain_start,
)
from tests.test_expert_team_terminal_reconciliation import (
    _remote_generating,
    _token_event,
    _valid_plan_content,
)


def _recovery_action_ids(run: dict) -> set[str]:
    presentation = (run.get("view") or {}).get("presentation") or {}
    actions = [presentation.get("primary_action"), *(presentation.get("secondary_actions") or [])]
    return {
        str(action.get("id"))
        for action in actions
        if isinstance(action, dict) and str(action.get("id") or "")
    }


def test_accepted_cancel_persists_retry_schedule_and_throttles_get_retries(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-bounded-cancel",
        runtime_run_id="remote-bounded-cancel",
    )
    cancel_calls = []

    class RunningAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status="running",
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[],
                cursor=cursor,
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    adapter = RunningAdapter()
    body = _control(generating, "cancel-bounded")
    cancelling = expert_teams.cancel_expert_team(
        tmp_path,
        body,
        cancel_callback=lambda _run: adapter.cancel_run(generating["execution_runtime_run_id"]),
    )

    assert cancelling["workflow_state"] == "cancelling"
    assert int(cancelling.get("cancel_retry_count") or 0) == 1
    assert float(cancelling.get("cancel_next_retry_at") or 0) > time.time()
    assert float(cancelling.get("cancel_deadline_at") or 0) > float(cancelling["cancel_next_retry_at"])

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: adapter)
    first = routes._expert_team_run_with_execution_truth(tmp_path, cancelling)
    routes._expert_team_run_with_execution_truth(tmp_path, first)
    assert cancel_calls == [generating["execution_runtime_run_id"]]


def test_expired_accepted_cancel_exposes_actions_and_same_key_can_retry(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-expired-cancel",
        runtime_run_id="remote-expired-cancel",
    )
    body = _control(generating, "cancel-expired-same-key")
    cancelling = expert_teams.cancel_expert_team(
        tmp_path,
        body,
        cancel_callback=lambda _run: runtime_adapter.ControlResult(True, status="accepted"),
    )
    expiring = dict(cancelling)
    expiring["cancel_next_retry_at"] = 0
    expiring["cancel_deadline_at"] = time.time() - 1
    write_run(tmp_path, expiring)

    class RunningAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status="running",
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[],
                cursor=cursor,
            )

        def cancel_run(self, _run_id):
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunningAdapter())
    expired = routes._expert_team_run_with_execution_truth(tmp_path, expiring)

    assert expired["workflow_state"] == "cancelling"
    assert expired["cancel_outcome"] == "unknown"
    assert {"retry_cancel", "refresh"}.issubset(_recovery_action_ids(expired))

    retry_calls = []
    retried = expert_teams.cancel_expert_team(
        tmp_path,
        body,
        cancel_callback=lambda _run: retry_calls.append("retry")
        or runtime_adapter.ControlResult(True, status="accepted"),
    )
    assert retry_calls == ["retry"]
    assert retried["workflow_state"] == "cancelling"
    assert int(retried.get("cancel_retry_count") or 0) >= 1


def test_orphan_cancel_requested_is_persisted_and_throttled(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    pending = _pending_orphan_run(expert_teams, tmp_path, "sid-bounded-orphan")
    cancel_calls = []

    class RunningAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=pending["session_id"],
                status="running",
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunningAdapter())
    routes._expert_team_run_with_execution_truth(tmp_path, pending)
    stored = expert_teams.read_expert_team_run(tmp_path, pending["run_id"])

    assert stored["execution_cleanup_status"] == "cancel_requested"
    assert int(stored.get("execution_cleanup_retry_count") or 0) == 1
    assert float(stored.get("execution_cleanup_next_retry_at") or 0) > time.time()
    assert float(stored.get("execution_cleanup_deadline_at") or 0) > float(
        stored["execution_cleanup_next_retry_at"]
    )

    routes._expert_team_run_with_execution_truth(tmp_path, stored)
    assert cancel_calls == ["remote-orphan"]


def test_expired_orphan_cleanup_remains_gated_with_visible_recovery(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    pending = _pending_orphan_run(expert_teams, tmp_path, "sid-expired-orphan")
    expired = dict(pending)
    expired.update(
        {
            "execution_cleanup_status": "cancel_requested",
            "execution_cleanup_retry_count": 3,
            "execution_cleanup_next_retry_at": 0,
            "execution_cleanup_deadline_at": time.time() - 1,
        }
    )
    write_run(tmp_path, expired)

    class RunningAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=pending["session_id"],
                status="running",
            )

        def cancel_run(self, _run_id):
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunningAdapter())
    observed = routes._expert_team_run_with_execution_truth(tmp_path, expired)
    stored = expert_teams.read_expert_team_run(tmp_path, pending["run_id"])

    assert stored["orphan_runtime_run_id"] == "remote-orphan"
    assert stored["execution_cleanup_status"] in {"unknown", "retry_required"}
    assert {"refresh", "retry_cleanup"}.issubset(_recovery_action_ids(observed))
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as blocked:
        expert_teams.resume_expert_team(tmp_path, _control(stored, "resume-still-gated"))
    assert blocked.value.code == "orphan_cleanup_pending"


def test_ambiguous_start_cancel_later_completed_binds_and_catches_up(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    starting = _uncertain_start(expert_teams, tmp_path, "sid-late-completed-bounded")
    session = SimpleNamespace(session_id=starting["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    lookup_outcome = {"status": "unknown"}
    observe_calls = 0
    cancel_calls = []
    remote_run_id = "remote-late-completed-bounded"
    first_page = _valid_plan_content()
    tail = "\n最后一页也必须追平。"

    class RunnerRuntimeAdapter:
        def find_run_by_idempotency_key(self, _key, *, session_id):
            if lookup_outcome["status"] == "unknown":
                return runtime_adapter.RunStatus(run_id="", session_id=session_id, status="unknown")
            return runtime_adapter.RunStatus(
                run_id=remote_run_id,
                session_id=session_id,
                status="completed",
                last_event_id="event-2",
            )

        def get_run(self, run_id):
            assert run_id == remote_run_id
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=starting["session_id"],
                status="completed",
                last_event_id="event-2",
            )

        def observe_run(self, run_id, *, cursor=None):
            nonlocal observe_calls
            assert run_id == remote_run_id
            observe_calls += 1
            event_id, text, sequence = ("event-1", first_page, 1) if observe_calls == 1 else ("event-2", tail, 2)
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=starting["session_id"],
                events=[
                    _token_event(
                        event_id=event_id,
                        text=text,
                        run_id=run_id,
                        session_id=starting["session_id"],
                        sequence=sequence,
                    )
                ],
                request_cursor=cursor,
                cursor=f"cursor-{sequence}",
                last_event_id=event_id,
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    adapter = RunnerRuntimeAdapter()
    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: adapter)
    handler = _post(routes, "/api/expert-teams/cancel", _control(starting, "cancel-late-completed"))
    assert handler.status == 202
    cancelling = expert_teams.read_expert_team_run(tmp_path, starting["run_id"])

    lookup_outcome["status"] = "completed"
    first = routes._expert_team_run_with_execution_truth(tmp_path, cancelling)
    assert first["execution_runtime_run_id"] == remote_run_id
    assert first["workflow_state"] == "cancelling"
    assert not first.get("stage_outputs")

    second = routes._expert_team_run_with_execution_truth(tmp_path, first)
    assert second["workflow_state"] == "awaiting_review"
    assert second["stage_outputs"][-1]["content"] == first_page + tail
    assert cancel_calls == []


def test_cleanup_accepted_without_terminal_truth_never_releases_resume_gate(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    pending = _pending_orphan_run(expert_teams, tmp_path, "sid-cleanup-never-terminal")

    class ForeverRunningAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=pending["session_id"],
                status="running",
            )

        def cancel_run(self, _run_id):
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: ForeverRunningAdapter())
    current = pending
    for _ in range(3):
        current = routes._expert_team_run_with_execution_truth(tmp_path, current)
    stored = expert_teams.read_expert_team_run(tmp_path, pending["run_id"])

    assert stored["orphan_runtime_run_id"] == "remote-orphan"
    assert stored["execution_cleanup_status"] != "confirmed"
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as blocked:
        expert_teams.resume_expert_team(tmp_path, _control(stored, "resume-before-orphan-terminal"))
    assert blocked.value.code == "orphan_cleanup_pending"


def test_retry_required_orphan_manual_refresh_reenters_reconciliation_and_clears_gate(
    monkeypatch, tmp_path
):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    pending = _pending_orphan_run(expert_teams, tmp_path, "sid-retry-required-orphan")
    retry_required = dict(pending)
    retry_required.update(
        {
            "execution_cleanup_status": "retry_required",
            "execution_cleanup_error": "自动清理未确认，请手动刷新或重试。",
            "execution_cleanup_next_retry_at": 0,
        }
    )
    write_run(tmp_path, retry_required)
    get_calls = []

    class AlreadyCancelledAdapter:
        def get_run(self, run_id):
            get_calls.append(run_id)
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=pending["session_id"],
                status="cancelled",
            )

        def cancel_run(self, _run_id):
            raise AssertionError("已取消运行只需对账，不应重复取消")

    monkeypatch.setattr(
        routes,
        "_expert_team_runtime_adapter_for_run",
        lambda _run: AlreadyCancelledAdapter(),
    )

    # 用户点击“刷新清理状态”或“重试清理”后，同一运行应重新进入真值对账。
    refreshed = routes._expert_team_run_with_execution_truth(tmp_path, retry_required)
    stored = expert_teams.read_expert_team_run(tmp_path, pending["run_id"])

    assert get_calls == ["remote-orphan"]
    assert refreshed["orphan_runtime_run_id"] == ""
    assert stored["orphan_runtime_run_id"] == ""
    assert stored["execution_cleanup_status"] == "cancelled"

    resumed = expert_teams.resume_expert_team(
        tmp_path,
        _control(stored, "resume-after-manual-orphan-reconciliation"),
    )
    assert resumed["run_id"] == stored["run_id"]


def test_retry_required_running_orphan_manual_retry_reissues_cancel_and_renews_window(
    monkeypatch, tmp_path
):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    pending = _pending_orphan_run(expert_teams, tmp_path, "sid-retry-running-orphan")
    expired_deadline = time.time() - 1
    retry_required = dict(pending)
    retry_required.update(
        {
            "execution_cleanup_status": "retry_required",
            "execution_cleanup_error": "自动清理未确认，请手动重试。",
            "execution_cleanup_retry_count": 3,
            "execution_cleanup_next_retry_at": 0,
            "execution_cleanup_deadline_at": expired_deadline,
        }
    )
    write_run(tmp_path, retry_required)
    cancel_calls = []

    class StillRunningAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=pending["session_id"],
                status="running",
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    monkeypatch.setattr(
        routes,
        "_expert_team_runtime_adapter_for_run",
        lambda _run: StillRunningAdapter(),
    )

    retried = routes._expert_team_run_with_execution_truth(tmp_path, retry_required)
    stored = expert_teams.read_expert_team_run(tmp_path, pending["run_id"])

    assert cancel_calls == ["remote-orphan"]
    assert retried["orphan_runtime_run_id"] == "remote-orphan"
    assert stored["execution_cleanup_status"] == "cancel_requested"
    assert int(stored.get("execution_cleanup_retry_count") or 0) > 3
    assert float(stored.get("execution_cleanup_next_retry_at") or 0) > time.time()
    assert float(stored.get("execution_cleanup_deadline_at") or 0) > float(
        stored["execution_cleanup_next_retry_at"]
    )

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as blocked:
        expert_teams.resume_expert_team(
            tmp_path,
            _control(stored, "resume-while-retried-orphan-still-running"),
        )
    assert blocked.value.code == "orphan_cleanup_pending"
