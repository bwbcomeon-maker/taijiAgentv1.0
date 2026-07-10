"""Crash-safe contracts for the final intake answer and start reservation."""

import threading
import time
from types import SimpleNamespace

import pytest

from tests.test_expert_team_v2_runtime import (
    _answer_required,
    _bound_delivery,
    _configure_route,
    _control,
    _post,
    _ready_run,
    _started,
)


def _collecting_optional(expert_teams, workspace, *, session_id: str) -> dict:
    run = expert_teams.start_expert_team(
        workspace,
        {
            "session_id": session_id,
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    required = _answer_required(expert_teams, workspace, run)
    assert required["workflow_state"] == "collecting_optional"
    return required


def _final_answer_body(run: dict, key: str) -> dict:
    return _control(
        run,
        key,
        answers={"optional_context": ""},
        skip_optional=True,
    )


def _run_for_generic_start_entry(expert_teams, workspace, entry: str) -> dict:
    ready = _ready_run(expert_teams, workspace, session_id=f"sid-generic-{entry}")
    generating = _started(
        expert_teams,
        workspace,
        ready,
        f"stream-generic-{entry}",
        f"turn-generic-{entry}",
    )
    if entry in {"approve", "revise"}:
        return expert_teams.mark_expert_team_execution_complete(
            workspace,
            generating["run_id"],
            _bound_delivery(generating, f"output-generic-{entry}"),
        )
    if entry == "input":
        return expert_teams.request_expert_team_stage_input(
            workspace,
            _control(
                generating,
                "request-generic-input",
                input_id="input-generic",
                question="请确认执行口径？",
            ),
        )
    if entry == "resume":
        return expert_teams.fail_expert_team_execution(
            workspace,
            generating["run_id"],
            "模拟可恢复失败",
            stream_id=generating["execution_stream_id"],
        )
    raise AssertionError(f"unsupported entry: {entry}")


def _generic_entry_request(run: dict, entry: str) -> tuple[str, dict]:
    if entry == "approve":
        return "/api/expert-teams/stage/approve", _control(run, "generic-approve-exit")
    if entry == "revise":
        return "/api/expert-teams/stage/revise", _control(
            run,
            "generic-revise-exit",
            feedback="请调整本阶段内容",
        )
    if entry == "input":
        return "/api/expert-teams/stage/input", _control(
            run,
            "generic-input-exit",
            input_id="input-generic",
            answer="确认",
        )
    if entry == "resume":
        return "/api/expert-teams/resume", _control(run, "generic-resume-exit")
    raise AssertionError(f"unsupported entry: {entry}")


def test_new_generic_start_reservation_has_pre_dispatch_evidence(tmp_path):
    from api import expert_teams, routes
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-generic-reservation")
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=ready["version"],
        runtime_adapter="LegacyJournalRuntimeAdapter",
    )

    assert reserved["execution_start_dispatch_state"] == "reserved"
    reserved["execution_start_deadline_at"] = 0
    write_run(tmp_path, reserved)
    recovered = routes._reconcile_expired_expert_team_start(tmp_path, reserved)
    assert recovered["workflow_state"] == "start_failed"
    assert recovered["view"]["actions"]["can_retry"] is True


@pytest.mark.parametrize("entry", ["approve", "revise", "input", "resume"])
def test_generic_entry_exit_after_reserve_before_dispatch_is_recoverable(
    monkeypatch,
    tmp_path,
    entry,
):
    from api import expert_teams, routes
    from api.expert_teams.storage import write_run

    current = _run_for_generic_start_entry(expert_teams, tmp_path, entry)
    session = SimpleNamespace(
        session_id=current["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(
        expert_teams,
        "mark_expert_team_execution_start_dispatching",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("exit before dispatch")),
    )
    path, body = _generic_entry_request(current, entry)

    with pytest.raises(SystemExit):
        _post(routes, path, body)

    stored = expert_teams.read_expert_team_run(tmp_path, current["run_id"])
    assert stored["workflow_state"] == "starting"
    assert stored["execution_start_dispatch_state"] == "reserved"
    stored["execution_start_deadline_at"] = 0
    write_run(tmp_path, stored)
    recovered = routes._reconcile_expired_expert_team_start(tmp_path, stored)
    assert recovered["workflow_state"] == "start_failed"
    assert recovered["view"]["actions"]["can_retry"] is True


def test_system_exit_after_stream_registration_rolls_back_session_claim(monkeypatch):
    from api import routes, run_journal, turn_journal

    class Session:
        session_id = "sid-system-exit-rollback"
        title = "Existing session"
        messages = [{"role": "user", "content": "existing"}]
        active_stream_id = None
        pending_user_message = None
        pending_attachments = []
        pending_started_at = None

        def __init__(self):
            self.save_count = 0

        def save(self, **_kwargs):
            self.save_count += 1

    session = Session()
    session_lock = threading.Lock()
    stream_id = "start-system-exit-rollback"

    def prepare(_session, *, stream_id, **_kwargs):
        _session.active_stream_id = stream_id
        _session.pending_user_message = "expert prompt"
        _session.pending_attachments = []
        _session.pending_started_at = time.time()

    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda _session_id: session_lock)
    monkeypatch.setattr(routes, "_prepare_chat_start_session_for_stream", prepare)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "set_last_workspace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        turn_journal,
        "append_turn_journal_event",
        lambda *_args, **_kwargs: {"turn_id": "turn-system-exit"},
    )
    monkeypatch.setattr(
        run_journal,
        "append_run_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("process exiting")),
    )

    try:
        with pytest.raises(SystemExit):
            routes._start_chat_stream_for_session(
                session,
                msg="expert prompt",
                workspace="/tmp",
                model="test-model",
                stream_id=stream_id,
            )

        assert stream_id not in routes.STREAMS
        assert session.active_stream_id is None
        assert session.pending_user_message is None
        assert session.pending_started_at is None
        assert session.save_count == 1
    finally:
        with routes.STREAMS_LOCK:
            routes.STREAMS.pop(stream_id, None)
        session.active_stream_id = None


def test_last_answer_and_start_reservation_are_one_durable_transition(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-atomic-single-write")
    writes = []
    original_write = runtime.write_run

    def capture_write(workspace, run):
        writes.append((run["workflow_state"], run.get("execution_start_id")))
        return original_write(workspace, run)

    monkeypatch.setattr(runtime, "write_run", capture_write)

    reserved, reservation_created = expert_teams.answer_and_reserve_expert_team_execution_start(
        tmp_path,
        _final_answer_body(collecting, "answer-and-reserve-single-write"),
        runtime_adapter="LegacyJournalRuntimeAdapter",
    )

    assert reservation_created is True
    assert writes == [("starting", reserved["execution_start_id"])]
    assert reserved["workflow_state"] == "starting"
    assert reserved["execution_start_id"].startswith("start-")
    assert reserved["execution_start_deadline_at"] > 0
    assert reserved["execution_runtime_adapter"] == "LegacyJournalRuntimeAdapter"
    assert reserved["version"] == collecting["version"] + 1
    assert any(
        row.get("idempotency_key") == "answer-and-reserve-single-write"
        for row in reserved.get("action_journal") or []
    )


def test_process_exit_at_start_helper_boundary_never_leaves_ready_to_generate(monkeypatch, tmp_path):
    from api import expert_teams, routes
    from api.expert_teams.storage import write_run

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-answer-helper-crash")
    session = SimpleNamespace(
        session_id=collecting["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)

    def crash_before_remote_start(*_args, **_kwargs):
        raise SystemExit("process exited before remote start")

    monkeypatch.setattr(routes, "_start_expert_team_execution", crash_before_remote_start)

    with pytest.raises(SystemExit):
        _post(
            routes,
            "/api/expert-teams/answer",
            _final_answer_body(collecting, "answer-helper-crash"),
        )

    stored = expert_teams.read_expert_team_run(tmp_path, collecting["run_id"])
    assert stored["workflow_state"] == "starting"
    assert stored["execution_start_id"].startswith("start-")
    assert stored["execution_runtime_adapter"] == "LegacyJournalRuntimeAdapter"
    assert stored["execution_start_dispatch_state"] == "reserved"

    stored["execution_start_deadline_at"] = 0
    write_run(tmp_path, stored)
    recovered = routes._expert_team_run_with_execution_truth(tmp_path, stored)
    assert recovered["workflow_state"] == "start_failed"
    assert recovered["view"]["actions"]["can_retry"] is True


def test_known_pre_dispatch_failure_leaves_recoverable_start_failed(monkeypatch, tmp_path):
    from api import expert_teams, routes

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-start-preflight-failure")
    session = SimpleNamespace(
        session_id=collecting["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(
        routes,
        "get_session",
        lambda _sid, **_kwargs: (_ for _ in ()).throw(KeyError("session disappeared")),
    )

    handler = _post(
        routes,
        "/api/expert-teams/answer",
        _final_answer_body(collecting, "answer-start-preflight-failure"),
    )
    stored = expert_teams.read_expert_team_run(tmp_path, collecting["run_id"])

    assert handler.status == 404
    assert stored["workflow_state"] == "start_failed"
    assert stored["view"]["actions"]["can_retry"] is True
    assert stored.get("last_execution_error")


def test_legacy_dispatch_intent_is_durable_before_external_start(monkeypatch, tmp_path):
    from api import expert_teams, routes

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-legacy-dispatch-marker")
    session = SimpleNamespace(
        session_id=collecting["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    observed_dispatch_states = []

    def start_after_marker(_session, **_kwargs):
        persisted = expert_teams.read_expert_team_run(tmp_path, collecting["run_id"])
        observed_dispatch_states.append(persisted.get("execution_start_dispatch_state"))
        return {"stream_id": "stream-dispatch-marker", "turn_id": "turn-dispatch-marker"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", start_after_marker)

    handler = _post(
        routes,
        "/api/expert-teams/answer",
        _final_answer_body(collecting, "answer-legacy-dispatch-marker"),
    )

    assert handler.status == 200
    assert observed_dispatch_states == ["dispatching"]
    assert handler.json_body()["run"]["workflow_state"] == "generating"


def test_exit_after_dispatch_marker_before_legacy_start_recovers_not_found(monkeypatch, tmp_path):
    from api import expert_teams, routes
    from api.expert_teams.storage import write_run

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-dispatch-exit")
    reserved, created = expert_teams.answer_and_reserve_expert_team_execution_start(
        tmp_path,
        _final_answer_body(collecting, "answer-dispatch-exit"),
        runtime_adapter="LegacyJournalRuntimeAdapter",
    )
    assert created is True
    session = SimpleNamespace(
        session_id=reserved["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(
        routes,
        "_start_chat_stream_for_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("exit before legacy start")),
    )

    with pytest.raises(SystemExit):
        routes._start_expert_team_execution(tmp_path, reserved, {}, already_reserved=True)

    persisted = expert_teams.read_expert_team_run(tmp_path, reserved["run_id"])
    assert persisted["workflow_state"] == "starting"
    assert persisted["execution_start_dispatch_state"] == "dispatching"
    persisted["execution_start_deadline_at"] = 0
    write_run(tmp_path, persisted)

    recovered = routes._expert_team_run_with_execution_truth(tmp_path, persisted)
    assert recovered["workflow_state"] == "start_failed"
    assert recovered["view"]["actions"]["can_retry"] is True


def test_registered_legacy_stream_is_bound_after_exit_before_start_commit(monkeypatch, tmp_path):
    from api import expert_teams, models, routes
    from api.expert_teams.storage import write_run
    from api.run_journal import append_run_event

    session_dir = tmp_path / "sessions"
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-bind-after-exit")
    reserved, created = expert_teams.answer_and_reserve_expert_team_execution_start(
        tmp_path,
        _final_answer_body(collecting, "answer-bind-after-exit"),
        runtime_adapter="LegacyJournalRuntimeAdapter",
    )
    assert created is True
    session = SimpleNamespace(
        session_id=reserved["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    observed_stream_ids = []

    def register_stream(_session, *, stream_id, **_kwargs):
        observed_stream_ids.append(stream_id)
        append_run_event(_session.session_id, stream_id, "submitted", session_dir=session_dir)
        return {
            "stream_id": stream_id,
            "session_id": _session.session_id,
            "turn_id": f"turn-{stream_id}",
        }

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", register_stream)
    original_mark_started = expert_teams.mark_expert_team_execution_started
    monkeypatch.setattr(
        expert_teams,
        "mark_expert_team_execution_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("exit before bind")),
    )

    with pytest.raises(SystemExit):
        routes._start_expert_team_execution(tmp_path, reserved, {}, already_reserved=True)

    persisted = expert_teams.read_expert_team_run(tmp_path, reserved["run_id"])
    assert observed_stream_ids == [persisted["execution_start_id"]]
    assert persisted["execution_start_dispatch_state"] == "dispatching"
    persisted["execution_start_deadline_at"] = 0
    write_run(tmp_path, persisted)
    monkeypatch.setattr(expert_teams, "mark_expert_team_execution_started", original_mark_started)
    monkeypatch.setattr(routes, "_active_stream_id_set", lambda: set())

    recovered = routes._reconcile_expired_expert_team_start(tmp_path, persisted)
    assert recovered["workflow_state"] == "generating"
    assert recovered["execution_stream_id"] == persisted["execution_start_id"]
    assert recovered["execution_runtime_run_id"] == persisted["execution_start_id"]


def test_late_legacy_start_is_cancelled_after_manual_retry_wins(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-late-start-retry")
    first_reserved, created = expert_teams.answer_and_reserve_expert_team_execution_start(
        tmp_path,
        _final_answer_body(collecting, "answer-late-start-retry"),
        runtime_adapter="LegacyJournalRuntimeAdapter",
    )
    assert created is True
    session = SimpleNamespace(
        session_id=first_reserved["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    first_call_entered = threading.Event()
    release_first_call = threading.Event()
    started_ids = []
    cancelled_ids = []

    class LegacyJournalRuntimeAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            return runtime_adapter.RunStatus(run_id=key, session_id=session_id, status="not_found")

        def start_run(self, request):
            start_id = str(request.idempotency_key or "")
            started_ids.append(start_id)
            if start_id == first_reserved["execution_start_id"]:
                first_call_entered.set()
                assert release_first_call.wait(timeout=3)
            return runtime_adapter.RunStartResult(
                run_id=start_id,
                session_id=request.session_id,
                stream_id=start_id,
            )

        def cancel_run(self, run_id):
            cancelled_ids.append(run_id)
            return runtime_adapter.ControlResult(True, status="accepted")

    adapter = LegacyJournalRuntimeAdapter()
    monkeypatch.setattr(runtime_adapter, "build_runtime_adapter", lambda **_kwargs: adapter)
    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: adapter)
    first_result = []

    def start_first():
        first_result.append(
            routes._start_expert_team_execution(
                tmp_path,
                first_reserved,
                {},
                already_reserved=True,
            )
        )

    first_thread = threading.Thread(target=start_first)
    first_thread.start()
    assert first_call_entered.wait(timeout=3)

    expired = expert_teams.read_expert_team_run(tmp_path, first_reserved["run_id"])
    assert expired["execution_start_dispatch_state"] == "dispatching"
    expired["execution_start_deadline_at"] = time.time() - 1
    write_run(tmp_path, expired)
    failed = routes._expert_team_run_with_execution_truth(tmp_path, expired)
    assert failed["workflow_state"] == "start_failed"

    ready = expert_teams.resume_expert_team(
        tmp_path,
        _control(failed, "resume-after-late-start"),
    )
    retry_payload, retry_status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert retry_status == 200
    retry_run = retry_payload["run"]
    assert retry_run["workflow_state"] == "generating"
    assert retry_run["execution_stream_id"] != first_reserved["execution_start_id"]

    release_first_call.set()
    first_thread.join(timeout=3)
    assert not first_thread.is_alive()
    assert len(first_result) == 1
    assert first_result[0][1] == 409
    assert started_ids == [first_reserved["execution_start_id"], retry_run["execution_start_id"]]
    assert cancelled_ids == [first_reserved["execution_start_id"]]
    stored = expert_teams.read_expert_team_run(tmp_path, first_reserved["run_id"])
    assert stored["workflow_state"] == "generating"
    assert stored["execution_start_id"] == retry_run["execution_start_id"]
    assert stored["execution_stream_id"] == retry_run["execution_stream_id"]


def test_concurrent_deterministic_legacy_starts_have_one_stream_owner(monkeypatch):
    from api import routes, run_journal, turn_journal

    session = SimpleNamespace(
        session_id="sid-deterministic-owner",
        title="Existing session",
        messages=[{"role": "user", "content": "existing"}],
        active_stream_id=None,
        pending_user_message=None,
        pending_started_at=None,
    )
    session_lock = threading.Lock()
    callers_ready = threading.Barrier(2)

    def synchronized_lock_lookup(_session_id):
        callers_ready.wait(timeout=3)
        return session_lock

    def prepare(_session, *, stream_id, **_kwargs):
        _session.active_stream_id = stream_id
        _session.pending_user_message = "expert prompt"
        _session.pending_started_at = time.time()

    monkeypatch.setattr(routes, "_get_session_agent_lock", synchronized_lock_lookup)
    monkeypatch.setattr(routes, "_prepare_chat_start_session_for_stream", prepare)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "set_last_workspace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "webui_gateway_chat_enabled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(routes, "_run_agent_streaming", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        turn_journal,
        "append_turn_journal_event",
        lambda *_args, **_kwargs: {"turn_id": "turn-owner"},
    )
    monkeypatch.setattr(run_journal, "append_run_event", lambda *_args, **_kwargs: {})
    stream_ids = ["start-owner-a", "start-owner-b"]
    results = []
    errors = []

    def start(stream_id):
        try:
            results.append(
                routes._start_chat_stream_for_session(
                    session,
                    msg="expert prompt",
                    workspace="/tmp",
                    model="test-model",
                    stream_id=stream_id,
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=start, args=(stream_id,)) for stream_id in stream_ids]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert sum(int(result.get("_status") or 200) == 200 for result in results) == 1
        assert sum(int(result.get("_status") or 200) == 409 for result in results) == 1
        owned_ids = [stream_id for stream_id in stream_ids if stream_id in routes.STREAMS]
        assert len(owned_ids) == 1
        assert session.active_stream_id == owned_ids[0]
    finally:
        with routes.STREAMS_LOCK:
            for stream_id in stream_ids:
                routes.STREAMS.pop(stream_id, None)


def test_duplicate_final_answer_reuses_same_reservation(tmp_path):
    from api import expert_teams

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-answer-reserve-duplicate")
    body = _final_answer_body(collecting, "answer-reserve-duplicate")

    first, first_created = expert_teams.answer_and_reserve_expert_team_execution_start(
        tmp_path,
        body,
        runtime_adapter="RunnerRuntimeAdapter",
    )
    duplicate, duplicate_created = expert_teams.answer_and_reserve_expert_team_execution_start(
        tmp_path,
        body,
        runtime_adapter="RunnerRuntimeAdapter",
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate["workflow_state"] == "starting"
    assert duplicate["execution_start_id"] == first["execution_start_id"]
    assert sum(
        row.get("idempotency_key") == "answer-reserve-duplicate"
        for row in duplicate.get("action_journal") or []
    ) == 1
    assert sum(
        row.get("type") == "generation_start_reserved"
        for row in duplicate.get("events") or []
    ) == 1


def test_partial_answer_persists_without_starting_runtime(monkeypatch, tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-partial-no-runtime",
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    required_questions = [question for question in run.get("questions") or [] if question.get("required")]
    assert required_questions
    session = SimpleNamespace(
        session_id=run["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(
        routes,
        "_start_expert_team_execution",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("partial answer initialized runtime")),
    )

    handler = _post(
        routes,
        "/api/expert-teams/answer",
        _control(
            run,
            "partial-answer-no-runtime",
            answers={str(required_questions[0]["id"]): "已确认"},
        ),
    )
    stored = expert_teams.read_expert_team_run(tmp_path, run["run_id"])

    assert handler.status == 200
    assert stored["workflow_state"] in {"collecting_required", "collecting_optional"}
    assert not stored.get("execution_start_id")
    assert any(
        row.get("question_id") == str(required_questions[0]["id"])
        for row in stored.get("answers") or []
    )


def test_concurrent_identical_final_answers_create_one_reservation(tmp_path):
    from api import expert_teams

    collecting = _collecting_optional(expert_teams, tmp_path, session_id="sid-concurrent-answer-reserve")
    body = _final_answer_body(collecting, "concurrent-answer-reserve")
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def answer_and_reserve():
        try:
            barrier.wait(timeout=2)
            results.append(
                expert_teams.answer_and_reserve_expert_team_execution_start(
                    tmp_path,
                    body,
                    runtime_adapter="RunnerRuntimeAdapter",
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=answer_and_reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert sum(created for _run, created in results) == 1
    assert len({run["execution_start_id"] for run, _created in results}) == 1
    stored = expert_teams.read_expert_team_run(tmp_path, collecting["run_id"])
    assert stored["workflow_state"] == "starting"
    assert sum(
        row.get("type") == "generation_start_reserved"
        for row in stored.get("events") or []
    ) == 1
