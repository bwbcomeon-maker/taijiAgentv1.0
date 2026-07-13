"""Adversarial RED contracts for expert-team remote finality.

An acknowledgement is not terminal truth.  These tests keep ambiguous starts,
accepted cancellations, paginated output, and replayed events from being
mistaken for a completed local transition.
"""

from types import SimpleNamespace

import pytest

from tests.test_expert_team_recovery_exit_contract import _post, _uncertain_start
from tests.test_expert_team_terminal_reconciliation import (
    _configure_route,
    _control,
    _ready_run,
    _remote_generating,
    _token_event,
    _valid_plan_content,
)


def test_start_transport_timeout_preserves_reservation_for_same_key_reconciliation(monkeypatch, tmp_path):
    """A timeout may follow a remote side effect, so it is not definite failure."""
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-ambiguous-start")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    lookup_calls = []
    started_keys = []

    class RunnerRuntimeAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            lookup_calls.append(key)
            if len(lookup_calls) == 1:
                return runtime_adapter.RunStatus(run_id=key, session_id=session_id, status="not_found")
            return runtime_adapter.RunStatus(
                run_id="remote-created-before-timeout",
                session_id=session_id,
                status="running",
            )

        def start_run(self, request):
            started_keys.append(request.idempotency_key)
            raise TimeoutError("response lost after remote create")

        def cancel_run(self, _run_id):
            raise AssertionError("ambiguous start must be reconciled before cleanup")

    adapter = RunnerRuntimeAdapter()
    monkeypatch.setattr(runtime_adapter, "build_runtime_adapter", lambda **_kwargs: adapter)
    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: adapter)

    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])

    assert status == 202
    assert payload.get("code") == "start_pending"
    assert stored["workflow_state"] == "starting"
    assert stored["execution_start_id"] == started_keys[0]
    stored["execution_start_deadline_at"] = 0
    write_run(tmp_path, stored)

    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, stored)
    assert reconciled["workflow_state"] == "generating"
    assert reconciled["execution_runtime_run_id"] == "remote-created-before-timeout"
    assert started_keys == [stored["execution_start_id"]]


def test_start_cleanup_acceptance_keeps_orphan_blocked_until_terminal_truth(tmp_path):
    """cancel accepted means cleanup requested, not orphan cleanup completed."""
    from api import expert_teams, routes, runtime_adapter

    class AcceptedAdapter:
        def cancel_run(self, _run_id):
            return runtime_adapter.ControlResult(True, status="accepted")

    remote = runtime_adapter.RunStartResult(
        run_id="remote-cancel-accepted",
        session_id="sid-cleanup-accepted",
        stream_id="remote-cancel-accepted",
    )
    cleanup = routes._cancel_expert_team_runtime_start(AcceptedAdapter(), remote)

    assert cleanup["pending_run_id"] == remote.run_id

    ready = _ready_run(expert_teams, tmp_path, session_id=remote.session_id)
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=ready["version"],
        runtime_adapter="RunnerRuntimeAdapter",
    )
    failed = expert_teams.mark_expert_team_execution_start_failed(
        tmp_path,
        ready["run_id"],
        "start commit failed",
        execution_start_id=reserved["execution_start_id"],
        orphan_runtime_run_id=cleanup["pending_run_id"],
        orphan_runtime_adapter="RunnerRuntimeAdapter",
        execution_cleanup_status="pending",
    )
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as blocked:
        expert_teams.resume_expert_team(tmp_path, _control(failed, "retry-before-terminal-cleanup"))
    assert blocked.value.code == "orphan_cleanup_pending"


def test_cancel_acceptance_stays_cancelling_until_remote_terminal(tmp_path):
    """The local run may become cancelled only after authoritative remote status."""
    from api import expert_teams, runtime_adapter

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-cancel-accepted-not-terminal",
        runtime_run_id="remote-cancel-accepted-not-terminal",
    )
    accepted = expert_teams.cancel_expert_team(
        tmp_path,
        _control(generating, "cancel-accepted-not-terminal"),
        cancel_callback=lambda _run: runtime_adapter.ControlResult(True, status="accepted"),
    )

    assert accepted["workflow_state"] == "cancelling"
    assert accepted["cancel_outcome"] == "accepted"


def test_uncertain_start_found_completed_preserves_result_instead_of_fake_cancel(monkeypatch, tmp_path):
    """Only not_found permits local cancel; completed is a real run to reconcile."""
    from api import expert_teams, routes, runtime_adapter

    starting = _uncertain_start(expert_teams, tmp_path, "sid-start-already-completed")
    session = SimpleNamespace(session_id=starting["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    remote_run_id = "remote-already-completed"
    content = _valid_plan_content()
    cancel_calls = []

    class RunnerRuntimeAdapter:
        def find_run_by_idempotency_key(self, _key, *, session_id):
            return runtime_adapter.RunStatus(
                run_id=remote_run_id,
                session_id=session_id,
                status="completed",
                last_event_id="final-1",
            )

        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=starting["session_id"],
                status="completed",
                last_event_id="final-1",
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=starting["session_id"],
                events=[
                    _token_event(
                        event_id="final-1",
                        text=content,
                        run_id=run_id,
                        session_id=starting["session_id"],
                        sequence=1,
                    )
                ],
                cursor="cursor-final",
                last_event_id="final-1",
            )

        def cancel_run(self, run_id):
            cancel_calls.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    adapter = RunnerRuntimeAdapter()
    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: adapter)

    handler = _post(routes, "/api/expert-teams/cancel", _control(starting, "cancel-completed-start"))
    after_request = expert_teams.read_expert_team_run(tmp_path, starting["run_id"])
    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, after_request)

    assert handler.status in {200, 202}
    assert reconciled["workflow_state"] == "awaiting_review"
    assert reconciled["stage_outputs"][-1]["content"] == content
    assert cancel_calls == []


def test_cancel_completed_waits_for_all_terminal_event_pages(monkeypatch, tmp_path):
    """Cancel-vs-complete races use the same final-event watermark gate."""
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    session_id = "sid-cancel-completed-pages"
    runtime_run_id = "remote-cancel-completed-pages"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    cancelling = dict(generating)
    cancelling.update(
        {
            "workflow_state": "cancelling",
            "cancel_previous_state": "generating",
            "cancel_request_id": "cancel-completed-pages",
            "cancel_outcome": "unknown",
            "cancel_runtime_accepted": False,
        }
    )
    write_run(tmp_path, cancelling)
    first_page = _valid_plan_content()
    tail = "\n终态尾页不能丢失。"
    observe_calls = 0

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=session_id,
                status="completed",
                last_event_id="event-2",
            )

        def observe_run(self, run_id, *, cursor=None):
            nonlocal observe_calls
            observe_calls += 1
            event_id, text, sequence = ("event-1", first_page, 1) if observe_calls == 1 else ("event-2", tail, 2)
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _token_event(
                        event_id=event_id,
                        text=text,
                        run_id=run_id,
                        session_id=session_id,
                        sequence=sequence,
                    )
                ],
                request_cursor=cursor,
                cursor=f"cursor-{sequence}",
                last_event_id=event_id,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    first = routes._expert_team_run_with_execution_truth(tmp_path, cancelling)
    assert first["workflow_state"] == "cancelling"
    assert not first.get("stage_outputs")
    second = routes._expert_team_run_with_execution_truth(tmp_path, first)
    assert second["workflow_state"] == "awaiting_review"
    assert second["stage_outputs"][-1]["content"] == first_page + tail


def test_terminal_without_authoritative_watermark_does_not_finalize_partial_output(monkeypatch, tmp_path):
    """Optional/missing last_event_id is not proof that pagination is complete."""
    from api import expert_teams, routes, runtime_adapter

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-no-terminal-watermark",
        runtime_run_id="remote-no-terminal-watermark",
    )
    content = _valid_plan_content()

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status="completed",
                last_event_id=None,
            )

        def observe_run(self, run_id, *, cursor=None):
                return runtime_adapter.RunEventStream(
                    run_id=run_id,
                    session_id=generating["session_id"],
                events=[
                    _token_event(
                        event_id="event-1",
                        text=content,
                        run_id=run_id,
                        session_id=generating["session_id"],
                        sequence=1,
                    )
                ],
                cursor="cursor-1",
                last_event_id="event-1",
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())
    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert observed["workflow_state"] == "generating"
    assert not observed.get("stage_outputs")


def test_cross_page_sequence_and_cursor_never_regress(monkeypatch, tmp_path):
    """Ordering and watermarks are execution-log invariants, not page-local ones."""
    from api import expert_teams, routes, runtime_adapter

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-cross-page-order",
        runtime_run_id="remote-cross-page-order",
    )
    observe_calls = 0

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(run_id=run_id, session_id=generating["session_id"], status="running")

        def observe_run(self, run_id, *, cursor=None):
            nonlocal observe_calls
            observe_calls += 1
            event_id, text, sequence = ("event-2", "B", 2) if observe_calls == 1 else ("event-1", "A", 1)
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[
                    _token_event(
                        event_id=event_id,
                        text=text,
                        run_id=run_id,
                        session_id=generating["session_id"],
                        sequence=sequence,
                    )
                ],
                request_cursor=cursor,
                cursor=str(sequence),
                last_event_id=event_id,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())
    first = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    routes._expert_team_run_with_execution_truth(tmp_path, first)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert stored["execution_public_output_buffer"] == "AB"
    assert stored["execution_cursor"] == "2"
    assert stored["execution_last_event_id"] == "event-2"


def test_event_replay_beyond_recent_window_is_still_exactly_once(tmp_path):
    """Long token streams cannot forget old event identities and duplicate text."""
    from api import expert_teams

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-long-event-replay",
        runtime_run_id="remote-long-event-replay",
    )
    observations = [
        {"event_id": f"event-{index}", "sequence": index, "kind": "delta", "text": str(index % 10)}
        for index in range(257)
    ]
    first = expert_teams.record_expert_team_execution_observation(
        tmp_path,
        generating["run_id"],
        runtime_run_id=generating["execution_runtime_run_id"],
        stream_id=generating["execution_stream_id"],
        stage_id=generating["execution_stage_id"],
        attempt=generating["execution_attempt"],
        cursor="257",
        last_event_id="event-256",
        observations=observations,
    )
    replayed = expert_teams.record_expert_team_execution_observation(
        tmp_path,
        generating["run_id"],
        runtime_run_id=generating["execution_runtime_run_id"],
        stream_id=generating["execution_stream_id"],
        stage_id=generating["execution_stage_id"],
        attempt=generating["execution_attempt"],
        cursor="257",
        last_event_id="event-256",
        observations=[{"event_id": "event-0", "sequence": 0, "kind": "delta", "text": "0"}],
    )

    assert replayed["execution_public_output_buffer"] == first["execution_public_output_buffer"]


def test_caught_up_terminal_without_public_output_persists_failure(monkeypatch, tmp_path):
    """A terminal run with only private events must exit generating visibly."""
    from api import expert_teams, routes, runtime_adapter

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-terminal-private-only",
        runtime_run_id="remote-terminal-private-only",
    )

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status="completed",
                last_event_id="private-1",
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[
                    {
                        "event_id": "private-1",
                        "sequence": 1,
                        "type": "reasoning.delta",
                        "payload": {"text": "private chain of thought"},
                        "run_id": run_id,
                        "session_id": generating["session_id"],
                    }
                ],
                cursor="private-1",
                last_event_id="private-1",
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())
    routes._expert_team_run_with_execution_truth(tmp_path, generating)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert stored["workflow_state"] == "generation_failed"
    assert stored["view"]["actions"]["can_retry"] is True
    assert stored.get("last_execution_error")
