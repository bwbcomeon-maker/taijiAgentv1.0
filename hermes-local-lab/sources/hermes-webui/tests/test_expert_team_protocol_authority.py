"""RED contracts for authoritative Runner pagination and event-ledger bounds."""

from types import SimpleNamespace

import pytest

from tests.test_expert_team_terminal_reconciliation import (
    _remote_generating,
    _valid_plan_content,
)


def _event(
    *,
    event_id: str = "",
    sequence=None,
    text: str,
    run_id: str,
    session_id: str,
) -> dict:
    event = {
        "type": "token.delta",
        "payload": {"text": text},
        "run_id": run_id,
        "session_id": session_id,
    }
    if event_id:
        event["event_id"] = event_id
    if sequence is not None:
        event["sequence"] = sequence
    return event


def _status(*, run_id: str, session_id: str, state: str, last_event_id=None, last_sequence=None):
    return SimpleNamespace(
        run_id=run_id,
        session_id=session_id,
        status=state,
        last_event_id=last_event_id,
        last_event_sequence=last_sequence,
        terminal_state=state if state in {"completed", "failed", "cancelled"} else None,
    )


def _stream(
    *,
    run_id: str,
    session_id: str,
    events: list[dict],
    request_cursor=None,
    next_cursor=None,
    global_last_event_id=None,
    delivered_last_event_id=None,
    delivered_through_sequence=None,
    has_more=None,
    snapshot_complete=None,
):
    """Model both global run watermarks and page-delivery authority explicitly."""
    return SimpleNamespace(
        run_id=run_id,
        session_id=session_id,
        events=events,
        request_cursor=request_cursor,
        cursor=next_cursor,
        last_event_id=global_last_event_id,
        delivered_last_event_id=delivered_last_event_id,
        delivered_through_sequence=delivered_through_sequence,
        has_more=has_more,
        snapshot_complete=snapshot_complete,
    )


def test_global_last_event_id_ahead_of_delivered_page_cannot_finalize(monkeypatch, tmp_path):
    """A run-global watermark is not proof that the page delivered through it."""
    from api import expert_teams, routes

    session_id = "sid-global-watermark-ahead"
    runtime_run_id = "remote-global-watermark-ahead"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(
                run_id=run_id,
                session_id=session_id,
                state="completed",
                last_event_id="event-2",
                last_sequence=2,
            )

        def observe_run(self, run_id, *, cursor=None):
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _event(
                        event_id="event-1",
                        sequence=1,
                        text=_valid_plan_content(),
                        run_id=run_id,
                        session_id=session_id,
                    )
                ],
                request_cursor=cursor,
                next_cursor="cursor-1",
                global_last_event_id="event-2",
                delivered_last_event_id="event-1",
                delivered_through_sequence=1,
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert observed["workflow_state"] == "generating"
    assert not observed.get("stage_outputs")
    assert stored["execution_public_output_buffer"] == _valid_plan_content()
    assert stored.get("execution_delivered_last_event_id") == "event-1"


def test_only_delivered_marker_matching_terminal_watermark_can_finalize(monkeypatch, tmp_path):
    """Global metadata may disagree; actual delivered-through identity is authoritative."""
    from api import expert_teams, routes

    session_id = "sid-delivered-marker-authority"
    runtime_run_id = "remote-delivered-marker-authority"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    content = _valid_plan_content()

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(
                run_id=run_id,
                session_id=session_id,
                state="completed",
                last_event_id="event-2",
                last_sequence=1,
            )

        def observe_run(self, run_id, *, cursor=None):
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _event(
                        event_id="event-2",
                        sequence=1,
                        text=content,
                        run_id=run_id,
                        session_id=session_id,
                    )
                ],
                request_cursor=cursor,
                next_cursor="opaque-next",
                global_last_event_id="global-event-999",
                delivered_last_event_id="event-2",
                delivered_through_sequence=1,
                has_more=False,
                snapshot_complete=True,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert observed["workflow_state"] == "awaiting_review"
    assert observed["stage_outputs"][-1]["content"] == content


def test_opaque_cursor_progress_uses_request_cursor_cas_and_rejects_stale_page(monkeypatch, tmp_path):
    """Opaque cursors have no lexical/numeric ordering; ownership comes from request CAS."""
    from api import expert_teams, routes

    session_id = "sid-opaque-cursor-cas"
    runtime_run_id = "remote-opaque-cursor-cas"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    requested_cursors = []

    pages = [
        (None, "cursor-z9", "event-z9", 1, "A"),
        ("cursor-z9", "cursor-a1", "event-a1", 2, "B"),
        # This response belongs to the old cursor-z9 request and must be ignored.
        ("cursor-z9", "cursor-z10", "stale-event", 1, "STALE"),
    ]

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="running")

        def observe_run(self, run_id, *, cursor=None):
            requested_cursors.append(cursor)
            request_cursor, next_cursor, event_id, sequence, text = pages[len(requested_cursors) - 1]
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _event(
                        event_id=event_id,
                        sequence=sequence,
                        text=text,
                        run_id=run_id,
                        session_id=session_id,
                    )
                ],
                request_cursor=request_cursor,
                next_cursor=next_cursor,
                global_last_event_id=event_id,
                delivered_last_event_id=event_id,
                delivered_through_sequence=sequence,
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    first = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    second = routes._expert_team_run_with_execution_truth(tmp_path, first)
    routes._expert_team_run_with_execution_truth(tmp_path, second)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert requested_cursors == [None, "cursor-z9", "cursor-a1"]
    assert stored["execution_cursor"] == "cursor-a1"
    assert stored["execution_public_output_buffer"] == "AB"
    assert [row["event_id"] for row in stored["execution_public_observations"]] == ["event-z9", "event-a1"]


def test_missing_watermark_requires_explicit_snapshot_completion_not_one_empty_page(monkeypatch, tmp_path):
    """One eventually-consistent empty read cannot substitute for page-complete authority."""
    from api import expert_teams, routes

    session_id = "sid-explicit-snapshot-complete"
    runtime_run_id = "remote-explicit-snapshot-complete"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    calls = 0

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="completed")

        def observe_run(self, run_id, *, cursor=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _stream(
                    run_id=run_id,
                    session_id=session_id,
                    events=[
                        _event(
                            event_id="event-1",
                            sequence=1,
                            text=_valid_plan_content(),
                            run_id=run_id,
                            session_id=session_id,
                        )
                    ],
                    request_cursor=cursor,
                    next_cursor="cursor-1",
                    delivered_last_event_id="event-1",
                    delivered_through_sequence=1,
                    has_more=None,
                    snapshot_complete=False,
                )
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[],
                request_cursor=cursor,
                next_cursor="cursor-1",
                delivered_last_event_id="event-1",
                delivered_through_sequence=1,
                has_more=False if calls >= 3 else None,
                snapshot_complete=True if calls >= 3 else None,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    first = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    second = routes._expert_team_run_with_execution_truth(tmp_path, first)

    assert second["workflow_state"] == "generating"
    assert not second.get("stage_outputs")

    third = routes._expert_team_run_with_execution_truth(tmp_path, second)
    assert third["workflow_state"] == "awaiting_review"
    assert third["stage_outputs"][-1]["content"] == _valid_plan_content()


def test_sequence_dedupes_replay_when_event_id_is_absent(tmp_path):
    from api import expert_teams

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-sequence-dedupe",
        runtime_run_id="remote-sequence-dedupe",
    )
    kwargs = {
        "runtime_run_id": generating["execution_runtime_run_id"],
        "stream_id": generating["execution_stream_id"],
        "stage_id": generating["execution_stage_id"],
        "attempt": generating["execution_attempt"],
        "cursor": "cursor-1",
        "last_event_id": None,
        "observations": [{"event_id": "", "sequence": 1, "kind": "delta", "text": "A"}],
    }

    first = expert_teams.record_expert_team_execution_observation(tmp_path, generating["run_id"], **kwargs)
    replayed = expert_teams.record_expert_team_execution_observation(tmp_path, generating["run_id"], **kwargs)

    assert first["execution_public_output_buffer"] == "A"
    assert replayed["execution_public_output_buffer"] == "A"
    assert len(replayed["execution_public_observations"]) == 1


def test_public_event_without_id_or_sequence_is_protocol_error_and_never_delivered(tmp_path):
    from api import expert_teams

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-unidentifiable-event",
        runtime_run_id="remote-unidentifiable-event",
    )

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as error:
        expert_teams.record_expert_team_execution_observation(
            tmp_path,
            generating["run_id"],
            runtime_run_id=generating["execution_runtime_run_id"],
            stream_id=generating["execution_stream_id"],
            stage_id=generating["execution_stage_id"],
            attempt=generating["execution_attempt"],
            cursor="opaque-cursor",
            last_event_id=None,
            observations=[{"event_id": "", "sequence": None, "kind": "delta", "text": "DO NOT DELIVER"}],
        )

    assert error.value.code == "runtime_protocol_error"
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert stored.get("execution_public_output_buffer") in (None, "")
    assert stored.get("execution_public_observations") in (None, [])


@pytest.mark.parametrize(
    ("event_limit", "byte_limit", "texts"),
    [
        (1, 10_000, ["A", "B"]),
        (100, 16, ["内容超过字节上限" * 8]),
    ],
    ids=["event-count", "utf8-bytes"],
)
def test_ledger_limit_becomes_explicit_retryable_failure(
    monkeypatch,
    tmp_path,
    event_limit,
    byte_limit,
    texts,
):
    from api import expert_teams, routes
    from api.expert_teams import runtime

    session_id = f"sid-ledger-limit-{event_limit}-{byte_limit}"
    runtime_run_id = f"remote-ledger-limit-{event_limit}-{byte_limit}"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    monkeypatch.setattr(runtime, "_OBSERVATION_LEDGER_MAX_EVENTS", event_limit, raising=False)
    monkeypatch.setattr(runtime, "_OBSERVATION_LEDGER_MAX_BYTES", byte_limit, raising=False)

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="running")

        def observe_run(self, run_id, *, cursor=None):
            events = [
                _event(
                    event_id=f"event-{index}",
                    sequence=index,
                    text=text,
                    run_id=run_id,
                    session_id=session_id,
                )
                for index, text in enumerate(texts, start=1)
            ]
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=events,
                request_cursor=cursor,
                next_cursor="cursor-limit",
                global_last_event_id=events[-1]["event_id"],
                delivered_last_event_id=events[-1]["event_id"],
                delivered_through_sequence=len(events),
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert observed["workflow_state"] == "generated_invalid"
    assert observed.get("last_execution_error")
    assert not observed.get("stage_outputs")
    assert observed["view"]["actions"]["can_retry"] is True


def test_authority_regression_explicit_delivered_marker_must_be_bound_to_actual_page(monkeypatch, tmp_path):
    """A page cannot claim delivery through an event that it did not actually return."""
    from api import expert_teams, routes

    session_id = "sid-unbound-delivered-marker"
    runtime_run_id = "remote-unbound-delivered-marker"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(
                run_id=run_id,
                session_id=session_id,
                state="completed",
                last_event_id="event-2",
                last_sequence=2,
            )

        def observe_run(self, run_id, *, cursor=None):
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _event(
                        event_id="event-1",
                        sequence=1,
                        text=_valid_plan_content(),
                        run_id=run_id,
                        session_id=session_id,
                    )
                ],
                request_cursor=cursor,
                next_cursor="cursor-1",
                global_last_event_id="event-2",
                delivered_last_event_id="event-2",
                delivered_through_sequence=2,
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert observed["workflow_state"] == "generated_invalid"
    assert not observed.get("stage_outputs")
    assert observed.get("last_execution_error")


def test_authority_regression_noninitial_page_requires_request_cursor(monkeypatch, tmp_path):
    """An omitted request_cursor cannot bypass ownership of an opaque-cursor response."""
    from api import expert_teams, routes

    session_id = "sid-missing-request-cursor"
    runtime_run_id = "remote-missing-request-cursor"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    seeded = expert_teams.record_expert_team_execution_observation(
        tmp_path,
        generating["run_id"],
        runtime_run_id=runtime_run_id,
        stream_id=generating["execution_stream_id"],
        stage_id=generating["execution_stage_id"],
        attempt=generating["execution_attempt"],
        cursor="cursor-current",
        last_event_id="event-2",
        delivered_last_event_id="event-2",
        delivered_through_sequence=2,
        expected_cursor=None,
        observations=[{"event_id": "event-2", "sequence": 2, "kind": "delta", "text": "B"}],
    )

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="running")

        def observe_run(self, run_id, *, cursor=None):
            assert cursor == "cursor-current"
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[],
                request_cursor=None,
                next_cursor="cursor-stale",
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    routes._expert_team_run_with_execution_truth(tmp_path, seeded)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert stored["workflow_state"] == "generating"
    assert stored["execution_cursor"] == "cursor-current"
    assert stored["execution_public_output_buffer"] == "B"


def test_authority_regression_event_id_reuse_with_different_identity_is_protocol_error(tmp_path):
    from api import expert_teams

    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id="sid-event-id-collision",
        runtime_run_id="remote-event-id-collision",
    )
    common = {
        "runtime_run_id": generating["execution_runtime_run_id"],
        "stream_id": generating["execution_stream_id"],
        "stage_id": generating["execution_stage_id"],
        "attempt": generating["execution_attempt"],
    }
    first = expert_teams.record_expert_team_execution_observation(
        tmp_path,
        generating["run_id"],
        cursor="cursor-1",
        last_event_id="event-1",
        delivered_last_event_id="event-1",
        delivered_through_sequence=1,
        expected_cursor=None,
        observations=[{"event_id": "same-id", "sequence": 1, "kind": "delta", "text": "A"}],
        **common,
    )

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as error:
        expert_teams.record_expert_team_execution_observation(
            tmp_path,
            generating["run_id"],
            cursor="cursor-2",
            last_event_id="event-2",
            delivered_last_event_id="event-2",
            delivered_through_sequence=2,
            expected_cursor="cursor-1",
            observations=[{"event_id": "same-id", "sequence": 2, "kind": "delta", "text": "B"}],
            **common,
        )

    assert error.value.code == "runtime_protocol_error"
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert stored["execution_public_output_buffer"] == first["execution_public_output_buffer"] == "A"
    assert stored["execution_delivered_through_sequence"] == 1


def test_authority_regression_ledger_byte_cap_counts_ids_and_metadata(monkeypatch, tmp_path):
    """The byte cap covers the durable ledger, not only token text."""
    from api import expert_teams, routes
    from api.expert_teams import runtime

    session_id = "sid-ledger-metadata-cap"
    runtime_run_id = "remote-ledger-metadata-cap"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    monkeypatch.setattr(runtime, "_OBSERVATION_LEDGER_MAX_BYTES", 256)

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="running")

        def observe_run(self, run_id, *, cursor=None):
            event_id = "event-" + ("x" * 5_000)
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _event(
                        event_id=event_id,
                        sequence=1,
                        text="A",
                        run_id=run_id,
                        session_id=session_id,
                    )
                ],
                request_cursor=cursor,
                next_cursor="cursor-1",
                global_last_event_id=event_id,
                delivered_last_event_id=event_id,
                delivered_through_sequence=1,
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert observed["workflow_state"] == "generated_invalid"
    assert observed.get("last_execution_error")
    assert not observed.get("stage_outputs")
    assert observed["view"]["actions"]["can_retry"] is True


def test_authority_regression_snapshot_flags_require_strict_boolean_types(monkeypatch, tmp_path):
    """The string 'false' is malformed protocol data, not completion authority."""
    from api import expert_teams, routes

    session_id = "sid-snapshot-boolean-type"
    runtime_run_id = "remote-snapshot-boolean-type"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="completed")

        def observe_run(self, run_id, *, cursor=None):
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    _event(
                        event_id="event-1",
                        sequence=1,
                        text=_valid_plan_content(),
                        run_id=run_id,
                        session_id=session_id,
                    )
                ],
                request_cursor=cursor,
                next_cursor="cursor-1",
                delivered_last_event_id="event-1",
                delivered_through_sequence=1,
                has_more=None,
                snapshot_complete="false",
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert observed["workflow_state"] == "generated_invalid"
    assert not observed.get("stage_outputs")
    assert observed.get("last_execution_error")


def test_authority_regression_private_event_marker_id_is_capped(monkeypatch, tmp_path):
    """Filtered private events cannot bypass the durable marker identity cap."""
    from api import expert_teams, routes

    session_id = "sid-private-marker-cap"
    runtime_run_id = "remote-private-marker-cap"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    oversized_id = "event-" + ("x" * 5_000)

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="running")

        def observe_run(self, run_id, *, cursor=None):
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[
                    {
                        "event_id": oversized_id,
                        "sequence": 1,
                        "type": "progress",
                        "data": {
                            "run_id": run_id,
                            "session_id": session_id,
                            "text": "private progress",
                        },
                    }
                ],
                request_cursor=cursor,
                next_cursor="cursor-private",
                delivered_last_event_id=oversized_id,
                delivered_through_sequence=1,
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert observed["workflow_state"] == "generated_invalid"
    assert len(str(stored.get("execution_delivered_last_event_id") or "").encode("utf-8")) <= 1024
    assert stored.get("execution_public_observations") in (None, [])


def test_authority_regression_oversized_opaque_cursor_is_rejected_without_persistence(
    monkeypatch, tmp_path
):
    """Runner-controlled cursors are bounded metadata and must fail closed."""
    from api import expert_teams, routes

    session_id = "sid-oversized-opaque-cursor"
    runtime_run_id = "remote-oversized-opaque-cursor"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    oversized_cursor = "cursor-" + ("x" * 5_000)

    class RunnerRuntimeAdapter:
        def get_run(self, run_id):
            return _status(run_id=run_id, session_id=session_id, state="running")

        def observe_run(self, run_id, *, cursor=None):
            return _stream(
                run_id=run_id,
                session_id=session_id,
                events=[],
                request_cursor=cursor,
                next_cursor=oversized_cursor,
                has_more=True,
                snapshot_complete=False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RunnerRuntimeAdapter())

    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert observed["workflow_state"] == "generated_invalid"
    assert stored.get("execution_cursor") in (None, "")
    assert stored.get("execution_public_observations") in (None, [])
    assert stored.get("last_execution_error")
