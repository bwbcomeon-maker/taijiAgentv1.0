import io
import hashlib
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest


@pytest.fixture(autouse=True)
def _enable_contract_pilot_for_contract_tests(monkeypatch):
    monkeypatch.setenv("TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT", "pilot")


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


def _answer_required(expert_teams, workspace, run, **extra):
    answers = {
        str(question.get("id")): "已确认"
        for question in run.get("questions") or []
        if question.get("required")
    }
    payload = {
        "run_id": run["run_id"],
        "answers": answers,
        "session_id": run.get("session_id"),
        "expected_version": run.get("version"),
        "stage_id": (run.get("current_stage") or {}).get("task_id"),
        "idempotency_key": f"answer-required-{run['run_id']}",
    }
    payload.update(extra)
    return expert_teams.answer_expert_team(
        workspace,
        payload,
    )


def _control(run, key: str, **extra) -> dict:
    return {
        "run_id": run["run_id"],
        "session_id": run.get("session_id"),
        "expected_version": run.get("version"),
        "stage_id": (run.get("current_stage") or {}).get("task_id"),
        "idempotency_key": key,
        **extra,
    }


def _ready_run(expert_teams, workspace, session_id="sid-v2"):
    run = expert_teams.start_expert_team(
        workspace,
        {
            "session_id": session_id,
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    required = _answer_required(expert_teams, workspace, run)
    return expert_teams.answer_expert_team(
        workspace,
        _control(
            required,
            f"answer-optional-{run['run_id']}",
            answers={"optional_context": ""},
            skip_optional=True,
        ),
    )


def _valid_plan_content() -> str:
    return (
        "阶段摘要：已形成专家团执行计划。\n"
        "正文草稿：本阶段只确认材料定位、使用对象、结构边界和后续分工，不直接起草完整正文。\n"
        "待补充事项：请补充具体数据。\n"
        "建议下一步：进入素材整理。"
    )


def _started(expert_teams, workspace, run, stream_id: str, turn_id: str | None = None):
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
            "turn_id": turn_id or f"turn-{stream_id}",
            "execution_start_id": reserved["execution_start_id"],
        },
    )


def _bound_delivery(run, output_id: str, content: str | None = None) -> dict:
    return {
        "stream_id": run["execution_stream_id"],
        "stage_id": run["execution_stage_id"],
        "attempt": run["execution_attempt"],
        "id": output_id,
        "kind": "chat",
        "content": content or _valid_plan_content(),
    }


def _install_write_barrier(monkeypatch, runtime):
    original = runtime.write_run
    barrier = threading.Barrier(2)

    def delayed_write(workspace, run):
        try:
            barrier.wait(timeout=0.3)
        except threading.BrokenBarrierError:
            pass
        return original(workspace, run)

    monkeypatch.setattr(runtime, "write_run", delayed_write)


def _configure_route(monkeypatch, routes, workspace, session):
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: workspace)
    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: session)
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider: (model or "test-model", provider, False),
    )


def test_answer_route_starts_first_stage_in_the_same_request(monkeypatch, tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-answer-start",
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    required = _answer_required(expert_teams, tmp_path, run)
    session = SimpleNamespace(
        session_id="sid-answer-start",
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    calls = []

    def fake_start(_session, **kwargs):
        calls.append(kwargs)
        return {"stream_id": "stream-first", "turn_id": "turn-first"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", fake_start)
    handler = _post(
        routes,
        "/api/expert-teams/answer",
        _control(
            required,
            "route-answer-start",
            answers={"optional_context": ""},
            skip_optional=True,
        ),
    )

    payload = handler.json_body()
    assert handler.status == 200
    assert len(calls) == 1
    assert calls[0]["turn_metadata"]["expert_team_run_id"] == run["run_id"]
    assert calls[0]["turn_metadata"]["stage_id"] == "plan"
    assert calls[0]["turn_metadata"]["attempt"] == 1
    assert calls[0]["turn_metadata"]["execution_start_id"]
    assert payload["run"]["workflow_state"] == "generating"
    assert payload["run"]["execution_stream_id"] == "stream-first"


def test_contract_answer_route_never_auto_starts_before_brief_confirmation(monkeypatch, tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-contract-answer",
            "team_id": "content-creator-team",
                "contract_version": "expert-team-contract/v1",
                "intake_example_id": "work_report",
                "document_type": "work_report",
            "prompt": "起草工作汇报，不要写成公众号文章",
            "document_brief_seed": {"document_control": {"render_template_id": "enterprise-work-report"}},
        },
    )
    session = SimpleNamespace(session_id=run["session_id"], model="test-model", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    calls = []
    monkeypatch.setattr(routes, "_start_chat_stream_for_session", lambda *args, **kwargs: calls.append(kwargs))
    answers = {str(item["id"]): "已确认" for item in run["questions"]}

    handler = _post(
        routes,
        "/api/expert-teams/answer",
        _control(run, "contract-answer", answers=answers, skip_optional=True),
    )

    assert handler.status == 200
    assert calls == []
    assert handler.json_body()["run"]["workflow_state"] == "collecting_required"
    assert handler.json_body()["run"]["view"]["brief"]["gate"] == "needs_confirmation"


def test_start_route_returns_stable_contract_error_code(monkeypatch, tmp_path):
    from api import routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    handler = _post(
        routes,
        "/api/expert-teams/start",
        {
            "session_id": "sid-invalid-contract",
            "team_id": "content-creator-team",
            "contract_version": "expert-team-contract/v9",
            "document_type": "work_report",
            "prompt": "起草工作汇报",
        },
    )
    assert handler.status == 400
    assert handler.json_body()["code"] == "unsupported_contract_version"


def test_approve_route_starts_next_stage_in_the_same_request(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-approve-start")
    generating = _started(expert_teams, tmp_path, ready, "stream-plan", "turn-plan")
    review = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        generating["run_id"],
        _bound_delivery(generating, "plan-output"),
    )
    session = SimpleNamespace(
        session_id="sid-approve-start",
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    calls = []

    def fake_start(_session, **kwargs):
        calls.append(kwargs)
        return {"stream_id": "stream-materials", "turn_id": "turn-materials"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", fake_start)
    handler = _post(
        routes,
        "/api/expert-teams/stage/approve",
        _control(review, "route-approve-start"),
    )

    payload = handler.json_body()
    assert handler.status == 200
    assert len(calls) == 1
    assert payload["run"]["workflow_state"] == "generating"
    assert payload["run"]["current_stage"]["task_id"] == "materials"
    assert payload["run"]["execution_stream_id"] == "stream-materials"


def test_failed_start_stays_recoverable_and_never_reports_generating(monkeypatch, tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-start-failed",
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    required = _answer_required(expert_teams, tmp_path, run)
    session = SimpleNamespace(
        session_id="sid-start-failed",
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    monkeypatch.setattr(
        routes,
        "_start_chat_stream_for_session",
        lambda *_args, **_kwargs: {"_status": 503, "error": "provider unavailable"},
    )

    handler = _post(
        routes,
        "/api/expert-teams/answer",
        _control(
            required,
            "route-answer-failed",
            answers={"optional_context": ""},
            skip_optional=True,
        ),
    )

    payload = handler.json_body()
    stored = expert_teams.read_expert_team_run(tmp_path, run["run_id"])
    assert handler.status == 503
    assert payload["run"]["workflow_state"] == "start_failed"
    assert stored["workflow_state"] == "start_failed"
    assert stored["execution_status"] != "running"
    assert stored["last_execution_error"] == "provider unavailable"


def test_answer_rejects_a_different_session_owner(monkeypatch, tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-owner",
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    session = SimpleNamespace(
        session_id="sid-other",
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    handler = _post(
        routes,
        "/api/expert-teams/answer",
        {
            "run_id": run["run_id"],
            "session_id": "sid-other",
            "answers": {"topic": "不应写入"},
        },
    )

    stored = expert_teams.read_expert_team_run(tmp_path, run["run_id"])
    assert handler.status == 404
    assert handler.json_body()["error"] == "expert team run does not belong to this session"
    assert all(question.get("status") == "pending" for question in stored["questions"])


def test_v2_version_stage_and_idempotency_guards(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": "sid-guards",
            "team_id": "content-creator-team",
            "prompt": "帮我起草工作汇报",
        },
    )
    assert run["schema_version"] == 2
    assert run["version"] == 1

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale:
        _answer_required(
            expert_teams,
            tmp_path,
            run,
            expected_version=0,
            stage_id="plan",
            idempotency_key="answer-required",
        )
    assert stale.value.code == "version_conflict"

    updated = _answer_required(
        expert_teams,
        tmp_path,
        run,
        expected_version=1,
        stage_id="plan",
        idempotency_key="answer-required",
    )
    assert updated["version"] == 2

    duplicate = _answer_required(
        expert_teams,
        tmp_path,
        updated,
        expected_version=1,
        stage_id="plan",
        idempotency_key="answer-required",
    )
    assert duplicate["version"] == updated["version"]

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale_stage:
        expert_teams.answer_expert_team(
            tmp_path,
            _control(
                updated,
                "answer-optional",
                stage_id="materials",
                answers={"optional_context": ""},
                skip_optional=True,
            ),
        )
    assert stale_stage.value.code == "stale_stage"


def test_revision_feedback_is_injected_into_the_next_prompt(tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-feedback")
    generating = _started(expert_teams, tmp_path, ready, "stream-feedback", "turn-feedback")
    review = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        generating["run_id"],
        _bound_delivery(generating, "plan-output"),
    )
    revised = expert_teams.request_expert_team_stage_revision(
        tmp_path,
        _control(review, "revision-feedback", feedback="请增加责任部门和时间节点。"),
    )

    prompt = routes._expert_team_execution_prompt(revised)
    assert "请增加责任部门和时间节点。" in prompt


def test_completion_rejects_a_different_execution_stream(tmp_path):
    from api import expert_teams

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-stream-guard")
    generating = _started(expert_teams, tmp_path, ready, "stream-current", "turn-current")

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as mismatch:
        expert_teams.mark_expert_team_execution_complete(
            tmp_path,
            generating["run_id"],
            {
                "stream_id": "stream-unrelated",
                "id": "unrelated-output",
                "kind": "chat",
                "content": _valid_plan_content(),
            },
        )
    assert mismatch.value.code == "stale_stream"
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert stored["workflow_state"] == "generating"
    assert stored.get("stage_outputs") == []


def test_assistant_result_requires_a_stream_bound_completion_event(monkeypatch):
    from api import routes
    from api import turn_journal

    session = SimpleNamespace(
        messages=[
            {"role": "user", "content": "普通历史消息", "timestamp": 1},
            {"role": "assistant", "content": "普通历史回复", "timestamp": 2},
            {"role": "user", "content": "专家团开始生成：计划", "timestamp": 3},
            {"role": "assistant", "content": "专家团阶段成果", "timestamp": 4},
            {"role": "user", "content": "另一个无关问题", "timestamp": 5},
            {"role": "assistant", "content": "无关问题回复", "timestamp": 6},
        ]
    )
    run = {
        "session_id": "sid-result-binding",
        "execution_stream_id": "stream-result-binding",
        "execution_message_start_index": 2,
        "pending_user_message": "专家团开始生成：计划",
    }

    monkeypatch.setattr(
        turn_journal,
        "read_turn_journal",
        lambda _sid: {"events": [], "malformed": []},
    )
    assert routes._latest_expert_team_assistant_content_after_execution(session, run) == ""


def test_assistant_result_uses_the_stream_bound_journal_message_index(monkeypatch):
    from api import routes, turn_journal

    session = SimpleNamespace(
        messages=[
            {"role": "user", "content": "专家团开始生成：计划", "timestamp": 1},
            {"role": "assistant", "content": "专家团阶段成果", "timestamp": 2},
            {"role": "assistant", "content": "无关回复", "timestamp": 3},
        ]
    )
    run = {
        "session_id": "sid-journal-binding",
        "execution_stream_id": "stream-journal",
        "execution_turn_id": "turn-journal",
    }
    monkeypatch.setattr(
        turn_journal,
        "read_turn_journal",
        lambda _sid: {
            "events": [
                {
                    "event": "completed",
                    "stream_id": "stream-journal",
                    "turn_id": "turn-journal",
                    "assistant_message_index": 1,
                }
            ],
            "malformed": [],
        },
    )

    assert routes._latest_expert_team_assistant_content_after_execution(session, run) == "专家团阶段成果"


def test_assistant_result_rejects_conflicting_completed_candidates(monkeypatch):
    from api import routes, turn_journal

    session = SimpleNamespace(messages=[
        {"role": "user", "content": "请求"},
        {"role": "assistant", "content": "结果一"},
        {"role": "assistant", "content": "结果二"},
    ])
    run = {"session_id": "sid-conflict", "run_id": "et-conflict", "execution_stream_id": "stream-conflict", "execution_turn_id": "turn-conflict"}
    monkeypatch.setattr(turn_journal, "read_turn_journal", lambda _sid: {"events": [
        {"event": "completed", "stream_id": "stream-conflict", "turn_id": "turn-conflict", "assistant_message_index": 1},
        {"event": "completed", "stream_id": "stream-conflict", "turn_id": "turn-conflict", "assistant_message_index": 2},
    ], "malformed": []})

    assert routes._latest_expert_team_assistant_content_after_execution(session, run) == ""


def test_assistant_result_rejects_completed_interrupted_terminal_collision(monkeypatch):
    from api import routes, turn_journal

    session = SimpleNamespace(messages=[{"role": "assistant", "content": "结果"}])
    run = {"session_id": "sid-terminal-conflict", "run_id": "et-terminal-conflict", "execution_stream_id": "stream-terminal-conflict", "execution_turn_id": "turn-terminal-conflict"}
    monkeypatch.setattr(turn_journal, "read_turn_journal", lambda _sid: {"events": [
        {"event": "completed", "stream_id": "stream-terminal-conflict", "turn_id": "turn-terminal-conflict", "assistant_message_index": 0},
        {"event": "interrupted", "stream_id": "stream-terminal-conflict", "turn_id": "turn-terminal-conflict", "reason": "gateway_error"},
    ], "malformed": []})

    assert routes._latest_expert_team_assistant_content_after_execution(session, run) == ""


def test_storage_rejects_payload_with_a_different_filename_run_id(tmp_path):
    from api.expert_teams.storage import read_run, run_path

    path = run_path(tmp_path, "et-canonical")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "et-other"}), encoding="utf-8")

    with pytest.raises(ValueError, match="run_id does not match"):
        read_run(tmp_path, "et-canonical")


def test_ready_run_cannot_complete_without_an_active_stream(tmp_path):
    from api import expert_teams

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-ready-no-stream")
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as conflict:
        expert_teams.mark_expert_team_execution_complete(
            tmp_path,
            ready["run_id"],
            {"id": "premature", "kind": "chat", "content": _valid_plan_content()},
        )
    assert conflict.value.code == "missing_stream"
    assert expert_teams.read_expert_team_run(tmp_path, ready["run_id"])["stage_outputs"] == []


def test_same_stream_can_complete_only_once(tmp_path):
    from api import expert_teams

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-once")
    generating = _started(expert_teams, tmp_path, ready, "stream-once", "turn-once")
    first = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        generating["run_id"],
        _bound_delivery(generating, "first"),
    )

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as duplicate:
        expert_teams.mark_expert_team_execution_complete(
            tmp_path,
            first["run_id"],
            _bound_delivery(generating, "duplicate"),
        )
    assert duplicate.value.code == "stale_state"
    stored = expert_teams.read_expert_team_run(tmp_path, first["run_id"])
    assert len(stored["stage_outputs"]) == 1


@pytest.mark.parametrize("missing", ["session_id", "expected_version", "stage_id", "idempotency_key"])
def test_v2_answer_requires_the_full_control_envelope(tmp_path, missing):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": f"sid-required-{missing}", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    body = _control(
        run,
        f"required-{missing}",
        answers={question["id"]: "已确认" for question in run["questions"] if question.get("required")},
    )
    body.pop(missing)

    with pytest.raises(ValueError, match=missing):
        expert_teams.answer_expert_team(tmp_path, body)


def test_ownership_is_checked_before_duplicate_idempotency(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-owner-first", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    updated = _answer_required(
        expert_teams,
        tmp_path,
        run,
        idempotency_key="same-key-owner-check",
    )
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as conflict:
        _answer_required(
            expert_teams,
            tmp_path,
            updated,
            session_id="sid-attacker",
            expected_version=run["version"],
            idempotency_key="same-key-owner-check",
        )
    assert conflict.value.code == "wrong_session"


def test_wrong_session_response_does_not_disclose_run(monkeypatch, tmp_path):
    from api import expert_teams, routes

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-private", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    session = SimpleNamespace(session_id="sid-attacker", model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    handler = _post(
        routes,
        "/api/expert-teams/answer",
        _control(
            run,
            "attacker-key",
            session_id="sid-attacker",
            answers={"topic": "不应写入"},
        ),
    )
    payload = handler.json_body()
    assert handler.status == 404
    assert "run" not in payload


def test_idempotency_key_is_bound_to_request_fingerprint_and_stage(tmp_path):
    from api import expert_teams

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-fingerprint", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    updated = _answer_required(
        expert_teams,
        tmp_path,
        run,
        idempotency_key="fingerprint-key",
    )
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as conflict:
        expert_teams.answer_expert_team(
            tmp_path,
            _control(
                updated,
                "fingerprint-key",
                expected_version=run["version"],
                answers={"topic": "篡改后的不同答案"},
            ),
        )
    assert conflict.value.code == "idempotency_key_reused"
    entry = expert_teams.read_expert_team_run(tmp_path, run["run_id"])["action_journal"][-1]
    assert entry["stage_id"] == "plan"
    assert entry["request_fingerprint"]


def test_action_journal_is_bounded(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import write_run

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-journal-bound", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    run["action_journal"] = [
        {
            "idempotency_key": f"old-{index}",
            "action": "answer",
            "stage_id": "plan",
            "request_fingerprint": f"fingerprint-{index}",
        }
        for index in range(128)
    ]
    write_run(tmp_path, run)
    updated = _answer_required(expert_teams, tmp_path, run, idempotency_key="new-entry")
    assert len(updated["action_journal"]) == 128
    assert updated["action_journal"][-1]["idempotency_key"] == "new-entry"


def test_revision_requires_review_state_and_latest_attempt_feedback_wins(tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-revision-attempt")
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as invalid_state:
        expert_teams.request_expert_team_stage_revision(
            tmp_path,
            _control(ready, "revise-too-early", feedback="过早修改"),
        )
    assert invalid_state.value.code == "stale_state"

    first_generating = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=ready["version"],
    )
    first_generating = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        ready["run_id"],
        {
            "stream_id": "stream-first-review",
            "turn_id": "turn-first-review",
            "execution_start_id": first_generating["execution_start_id"],
        },
    )
    first_review = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        first_generating["run_id"],
        _bound_delivery(first_generating, "first-plan"),
    )
    first_revision = expert_teams.request_expert_team_stage_revision(
        tmp_path,
        _control(first_review, "revision-one", feedback="第一轮意见"),
    )
    reserved_revision = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        first_revision["run_id"],
        expected_version=first_revision["version"],
    )
    first_generating = expert_teams.mark_expert_team_execution_started(
        tmp_path,
        first_revision["run_id"],
        {
            "stream_id": "stream-revision",
            "turn_id": "turn-revision",
            "execution_start_id": reserved_revision["execution_start_id"],
        },
    )
    second_review = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        first_generating["run_id"],
        _bound_delivery(first_generating, "second-plan"),
    )
    second_revision = expert_teams.request_expert_team_stage_revision(
        tmp_path,
        _control(second_review, "revision-two", feedback="第二轮最新意见"),
    )
    prompt = routes._expert_team_execution_prompt(second_revision)
    assert "第二轮最新意见" in prompt
    assert "第一轮意见" not in prompt.split("本轮用户修订意见：", 1)[1].split("重要规则：", 1)[0]


def test_stage_input_requires_matching_input_id(tmp_path):
    from api import expert_teams

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-input-id")
    generating = _started(expert_teams, tmp_path, ready, "stream-input-id", "turn-input-id")
    paused = expert_teams.request_expert_team_stage_input(
        tmp_path,
        _control(
            generating,
            "request-input",
            input_id="input-current",
            question="确认口径？",
        ),
    )
    missing = _control(paused, "submit-input-missing", answer="确认")
    with pytest.raises(ValueError, match="input_id"):
        expert_teams.submit_expert_team_stage_input(tmp_path, missing)
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale:
        expert_teams.submit_expert_team_stage_input(
            tmp_path,
            _control(paused, "submit-input-stale", input_id="input-old", answer="确认"),
        )
    assert stale.value.code == "stale_input"


def test_resume_rejects_terminal_run_and_cancel_uses_same_version_guard(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import write_run

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-terminal", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    terminal = dict(run)
    terminal["workflow_state"] = "completed"
    write_run(tmp_path, terminal)
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as terminal_conflict:
        expert_teams.resume_expert_team(tmp_path, _control(terminal, "resume-terminal"))
    assert terminal_conflict.value.code == "terminal_state"

    active = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-cancel-guard", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale:
        expert_teams.cancel_expert_team(
            tmp_path,
            _control(active, "cancel-stale", expected_version=0),
        )
    assert stale.value.code == "version_conflict"


def test_legacy_run_without_payload_run_id_remains_readable_but_v2_is_strict(tmp_path):
    from api.expert_teams.storage import read_run, run_path

    legacy_path = run_path(tmp_path, "legacy-run")
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps({"workflow_state": "completed"}), encoding="utf-8")
    assert read_run(tmp_path, "legacy-run")["run_id"] == "legacy-run"

    v2_path = run_path(tmp_path, "v2-run")
    v2_path.write_text(json.dumps({"schema_version": 2, "workflow_state": "completed"}), encoding="utf-8")
    with pytest.raises(ValueError, match="run_id does not match"):
        read_run(tmp_path, "v2-run")


def test_concurrent_start_reserves_once_before_external_runtime(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-concurrent-start")
    session = SimpleNamespace(
        session_id="sid-concurrent-start",
        model="test-model",
        model_provider=None,
        messages=[],
    )
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    calls = []
    call_lock = threading.Lock()

    def fake_start(_session, **_kwargs):
        with call_lock:
            calls.append(len(calls) + 1)
            number = calls[-1]
        time.sleep(0.15)
        return {"stream_id": f"stream-{number}", "turn_id": f"turn-{number}"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", fake_start)
    results = []

    def invoke():
        try:
            results.append(routes._start_expert_team_execution(tmp_path, ready, {}))
        except Exception as exc:  # pre-fix path is part of the RED evidence
            results.append(exc)

    threads = [threading.Thread(target=invoke), threading.Thread(target=invoke)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert len(calls) == 1
    statuses = sorted(result[1] for result in results if isinstance(result, tuple))
    assert statuses == [200, 409]


def test_orphan_stream_is_cancelled_when_start_commit_fails(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-orphan")
    session = SimpleNamespace(session_id="sid-orphan", model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    monkeypatch.setattr(
        routes,
        "_start_chat_stream_for_session",
        lambda *_args, **_kwargs: {"stream_id": "stream-orphan", "turn_id": "turn-orphan"},
    )
    monkeypatch.setattr(
        expert_teams,
        "mark_expert_team_execution_started",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            expert_teams.ExpertTeamStateConflict("stale_state", "commit failed")
        ),
    )
    cancelled = []
    monkeypatch.setattr(routes, "cancel_stream", lambda stream_id: cancelled.append(stream_id) or True)

    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert status == 409
    assert payload["ok"] is False
    assert cancelled == ["stream-orphan"]


def test_expert_start_obeys_license_gate_before_runtime(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-license")
    session = SimpleNamespace(session_id="sid-license", model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    monkeypatch.setattr(
        routes,
        "_taiji_license_blocked_status",
        lambda: {"code": "license_expired", "message": "授权已过期"},
    )
    monkeypatch.setattr(
        routes,
        "_start_chat_stream_for_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime must not start")),
    )

    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert status == 403
    assert payload["license_blocked"] is True
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert stored["workflow_state"] == "start_failed"
    assert stored["last_execution_error"] == "授权已过期"


def test_expert_start_enters_runtime_through_adapter(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-adapter")
    session = SimpleNamespace(session_id="sid-adapter", model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    monkeypatch.setattr(
        routes,
        "_start_chat_stream_for_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must enter adapter first")),
    )
    adapter_calls = []

    def fake_adapter_start(self, request):
        adapter_calls.append(request)
        return runtime_adapter.RunStartResult(
            run_id="runtime-run",
            session_id=request.session_id,
            stream_id="adapter-stream",
            payload={"stream_id": "adapter-stream", "turn_id": "adapter-turn"},
        )

    monkeypatch.setattr(runtime_adapter.LegacyJournalRuntimeAdapter, "start_run", fake_adapter_start)
    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert status == 200
    assert len(adapter_calls) == 1
    assert adapter_calls[0].metadata["expert_team_run_id"] == ready["run_id"]
    assert payload["run"]["execution_stream_id"] == "adapter-stream"


def test_concurrent_v2_answers_are_serialized_by_version(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-concurrent-answer", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    answers = {question["id"]: "已确认" for question in run["questions"] if question.get("required")}
    bodies = [
        _control(run, f"answer-race-{index}", answers=answers)
        for index in range(2)
    ]
    _install_write_barrier(monkeypatch, runtime)
    results = []

    def invoke(body):
        try:
            results.append(("ok", expert_teams.answer_expert_team(tmp_path, body)))
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append((exc.code, None))

    threads = [threading.Thread(target=invoke, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(kind for kind, _ in results) == ["ok", "version_conflict"]
    assert expert_teams.read_expert_team_run(tmp_path, run["run_id"])["version"] == run["version"] + 1


def test_concurrent_approve_and_revise_are_serialized(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-approve-revise-race")
    generating = _started(expert_teams, tmp_path, ready, "stream-approve-revise")
    review = expert_teams.mark_expert_team_execution_complete(
        tmp_path,
        generating["run_id"],
        _bound_delivery(generating, "review-race"),
    )
    _install_write_barrier(monkeypatch, runtime)
    results = []

    def approve():
        try:
            expert_teams.approve_expert_team_stage(tmp_path, _control(review, "approve-race"))
            results.append("ok")
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append(exc.code)

    def revise():
        try:
            expert_teams.request_expert_team_stage_revision(
                tmp_path,
                _control(review, "revise-race", feedback="并发修改"),
            )
            results.append("ok")
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=approve), threading.Thread(target=revise)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert sorted(results) == ["ok", "version_conflict"]


def test_concurrent_stage_input_and_cancel_are_serialized(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-input-cancel-race")
    generating = _started(expert_teams, tmp_path, ready, "stream-input-cancel")
    paused = expert_teams.request_expert_team_stage_input(
        tmp_path,
        _control(generating, "request-race-input", input_id="race-input", question="确认？"),
    )
    _install_write_barrier(monkeypatch, runtime)
    results = []

    def submit():
        try:
            expert_teams.submit_expert_team_stage_input(
                tmp_path,
                _control(paused, "submit-race-input", input_id="race-input", answer="确认"),
            )
            results.append("ok")
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append(exc.code)

    def cancel():
        try:
            expert_teams.cancel_expert_team(tmp_path, _control(paused, "cancel-race-input"))
            results.append("ok")
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=submit), threading.Thread(target=cancel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert sorted(results) == ["ok", "version_conflict"]


def test_concurrent_completion_consumes_execution_once(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-completion-race")
    generating = _started(expert_teams, tmp_path, ready, "stream-completion-race")
    _install_write_barrier(monkeypatch, runtime)
    results = []

    def complete(index):
        try:
            expert_teams.mark_expert_team_execution_complete(
                tmp_path,
                generating["run_id"],
                _bound_delivery(generating, f"completion-{index}"),
            )
            results.append("ok")
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=complete, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(results) == ["ok", "stale_state"]
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert len(stored["stage_outputs"]) == 1


def test_completion_requires_exact_stream_stage_and_attempt(tmp_path):
    from api import expert_teams

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-completion-identity")
    generating = _started(expert_teams, tmp_path, ready, "stream-identity")
    for field, bad_value, expected_code in (
        ("stream_id", "old-stream", "stale_stream"),
        ("stage_id", "old-stage", "stale_stage"),
        ("attempt", generating["execution_attempt"] + 1, "stale_attempt"),
    ):
        delivery = _bound_delivery(generating, f"bad-{field}")
        delivery[field] = bad_value
        with pytest.raises(expert_teams.ExpertTeamStateConflict) as conflict:
            expert_teams.mark_expert_team_execution_complete(tmp_path, generating["run_id"], delivery)
        assert conflict.value.code == expected_code


def test_legacy_runs_are_read_only_for_every_mutation(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import read_run, run_path

    path = run_path(tmp_path, "legacy-read-only")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = {"run_id": "legacy-read-only", "workflow_state": "ready_to_generate", "version": 7}
    path.write_text(json.dumps(original), encoding="utf-8")
    for mutate in (
        lambda: expert_teams.answer_expert_team(tmp_path, {"run_id": "legacy-read-only", "answers": {}}),
        lambda: expert_teams.cancel_expert_team(tmp_path, {"run_id": "legacy-read-only"}),
        lambda: expert_teams.mark_expert_team_execution_complete(
            tmp_path,
            "legacy-read-only",
            {"stream_id": "legacy", "stage_id": "plan", "attempt": 1, "content": "legacy"},
        ),
    ):
        with pytest.raises(expert_teams.ExpertTeamStateConflict) as conflict:
            mutate()
        assert conflict.value.code == "legacy_read_only"
    assert read_run(tmp_path, "legacy-read-only") == original


def test_generating_without_stream_derives_start_failed(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import write_run

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-empty-generating", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    run["workflow_state"] = "generating"
    run["execution_stream_id"] = ""
    write_run(tmp_path, run)
    derived = expert_teams.read_expert_team_run(tmp_path, run["run_id"])
    assert derived["workflow_state"] == "start_failed"
    assert derived["status"] != "running"
    assert derived["execution_status"] == "error"


def test_runtime_result_without_stream_is_cancelled(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-runtime-empty-stream")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    cancelled = []

    def fake_start(self, request):
        return runtime_adapter.RunStartResult(
            run_id="runtime-created-no-stream",
            session_id=request.session_id,
            stream_id="",
            payload={"run_id": "runtime-created-no-stream"},
        )

    monkeypatch.setattr(runtime_adapter.LegacyJournalRuntimeAdapter, "start_run", fake_start)
    monkeypatch.setattr(runtime_adapter.LegacyJournalRuntimeAdapter, "cancel_run", lambda self, run_id: cancelled.append(run_id))
    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert status == 502
    assert payload["run"]["workflow_state"] == "start_failed"
    assert cancelled == ["runtime-created-no-stream"]


def test_runtime_exception_with_run_identity_is_cancelled(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-runtime-exception")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    cancelled = []

    class CreatedThenFailed(RuntimeError):
        run_id = "runtime-created-before-error"
        stream_id = "stream-created-before-error"

    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "start_run",
        lambda self, request: (_ for _ in ()).throw(CreatedThenFailed("runtime failed")),
    )
    monkeypatch.setattr(runtime_adapter.LegacyJournalRuntimeAdapter, "cancel_run", lambda self, run_id: cancelled.append(run_id))
    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert status == 500
    assert payload["run"]["workflow_state"] == "start_failed"
    assert cancelled == ["runtime-created-before-error"]


def test_start_requires_session_id(tmp_path):
    from api import expert_teams

    with pytest.raises(ValueError, match="session_id"):
        expert_teams.start_expert_team(
            tmp_path,
            {"session_id": "", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
        )


def test_expired_start_reservation_cannot_be_replaced_without_runtime_reconcile(tmp_path):
    from api import expert_teams
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-start-lease")
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=ready["version"],
    )
    expired = dict(reserved)
    expired["execution_start_deadline_at"] = 0
    write_run(tmp_path, expired)
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as in_progress:
        expert_teams.reserve_expert_team_execution_start(
            tmp_path,
            ready["run_id"],
            expected_version=reserved["version"],
        )
    assert in_progress.value.code == "start_in_progress"
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert stored["workflow_state"] == "starting"
    assert stored["execution_start_id"] == reserved["execution_start_id"]


def test_fail_execution_requires_matching_active_stream(tmp_path):
    from api import expert_teams

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-fail-stream")
    generating = _started(expert_teams, tmp_path, ready, "stream-fail-current")
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale:
        expert_teams.fail_expert_team_execution(
            tmp_path,
            generating["run_id"],
            "失败",
            stream_id="stream-fail-old",
        )
    assert stale.value.code == "stale_stream"
    failed = expert_teams.fail_expert_team_execution(
        tmp_path,
        generating["run_id"],
        "真实失败",
        stream_id="stream-fail-current",
    )
    assert failed["workflow_state"] == "generation_failed"
    assert failed["view"]["actions"]["can_retry"] is True


def test_remote_runtime_poll_uses_adapter_not_local_stream_registry(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-remote-poll")
    generating = _started(expert_teams, tmp_path, ready, "remote-stream")
    generating["execution_runtime_run_id"] = "remote-run"
    generating["execution_runtime_adapter"] = "RunnerRuntimeAdapter"
    write_run(tmp_path, generating)
    calls = []

    class FakeAdapter:
        def get_run(self, run_id):
            calls.append(("get", run_id))
            return runtime_adapter.RunStatus(run_id=run_id, status="running")

        def observe_run(self, run_id, *, cursor=None):
            calls.append(("observe", run_id, cursor))
            return runtime_adapter.RunEventStream(run_id=run_id, events=[], cursor=cursor)

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())
    monkeypatch.setattr(
        routes,
        "_active_stream_id_set",
        lambda: (_ for _ in ()).throw(AssertionError("remote runs must not use local STREAMS")),
    )
    observed = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    assert observed["workflow_state"] == "generating"
    assert calls == [("get", "remote-run"), ("observe", "remote-run", None)]


def test_cancel_rejected_does_not_persist_cancelled(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-cancel-rejected")
    generating = _started(expert_teams, tmp_path, ready, "stream-cancel-rejected")
    session = SimpleNamespace(session_id=generating["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)

    class RejectingAdapter:
        def cancel_run(self, run_id):
            return runtime_adapter.ControlResult(False, status="rejected", safe_message="runtime rejected")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RejectingAdapter())
    handler = _post(routes, "/api/expert-teams/cancel", _control(generating, "cancel-rejected"))
    assert handler.status == 409
    assert expert_teams.read_expert_team_run(tmp_path, generating["run_id"])["workflow_state"] == "generating"


def test_cancel_is_accepted_before_cancelled_state_is_written(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-cancel-order")
    generating = _started(expert_teams, tmp_path, ready, "stream-cancel-order")
    session = SimpleNamespace(session_id=generating["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    events = []
    remote_status = "running"

    class AcceptingAdapter:
        def cancel_run(self, run_id):
            events.append(("adapter", run_id))
            return runtime_adapter.ControlResult(True, status="accepted")

        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status=remote_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[],
                cursor=cursor,
            )

    original_cancel = expert_teams.cancel_expert_team

    def checked_cancel(workspace, body, **kwargs):
        callback = kwargs.get("cancel_callback")

        def checked_callback(run):
            result = callback(run)
            events.append(("persist", run["run_id"]))
            return result

        return original_cancel(workspace, body, cancel_callback=checked_callback)

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: AcceptingAdapter())
    monkeypatch.setattr(expert_teams, "cancel_expert_team", checked_cancel)
    handler = _post(routes, "/api/expert-teams/cancel", _control(generating, "cancel-order"))
    assert handler.status == 202
    assert [event[0] for event in events] == ["adapter", "persist"]
    pending = handler.json_body()["run"]
    assert pending["workflow_state"] == "cancelling"
    assert pending["cancel_outcome"] == "accepted"
    remote_status = "cancelled"
    settled = routes._expert_team_run_with_execution_truth(tmp_path, pending)
    assert settled["workflow_state"] == "cancelled"


def test_cancel_and_start_commit_are_serialized(monkeypatch, tmp_path):
    from api import expert_teams
    from api.expert_teams import runtime

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-cancel-start-race")
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=ready["version"],
    )
    _install_write_barrier(monkeypatch, runtime)
    results = []

    def commit_start():
        try:
            expert_teams.mark_expert_team_execution_started(
                tmp_path,
                ready["run_id"],
                {
                    "stream_id": "stream-cancel-start",
                    "turn_id": "turn-cancel-start",
                    "execution_start_id": reserved["execution_start_id"],
                },
            )
            results.append(("start", "ok"))
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append(("start", exc.code))

    def cancel():
        try:
            expert_teams.cancel_expert_team(tmp_path, _control(reserved, "cancel-start-race"))
            results.append(("cancel", "ok"))
        except expert_teams.ExpertTeamStateConflict as exc:
            results.append(("cancel", exc.code))

    threads = [threading.Thread(target=commit_start), threading.Thread(target=cancel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    outcomes = dict(results)
    assert set(outcomes) == {"start", "cancel"}
    assert list(outcomes.values()).count("ok") == 1
    stored = expert_teams.read_expert_team_run(tmp_path, reserved["run_id"])
    if outcomes["start"] == "ok":
        assert outcomes["cancel"] == "version_conflict"
        assert stored["workflow_state"] == "generating"
        assert stored["version"] == reserved["version"] + 1
        assert stored["execution_start_id"] == reserved["execution_start_id"]
        assert stored["execution_stream_id"] == "stream-cancel-start"
        assert not any(
            entry.get("idempotency_key") == "cancel-start-race"
            for entry in stored.get("action_journal") or []
        )
    else:
        assert outcomes["cancel"] == "ok"
        assert outcomes["start"] == "stale_state"
        assert stored["workflow_state"] == "cancelling"
        assert stored["cancel_outcome"] == "accepted"
        assert stored["version"] == reserved["version"] + 2
        assert stored["execution_stream_id"] == ""
        assert any(
            entry.get("idempotency_key") == "cancel-start-race"
            for entry in stored.get("action_journal") or []
        )


def test_expired_starting_poll_becomes_recoverable(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-expired-poll")
    reserved = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=ready["version"],
    )
    reserved["execution_start_deadline_at"] = 0
    reserved["execution_runtime_adapter"] = "RunnerRuntimeAdapter"
    write_run(tmp_path, reserved)

    class MissingAdapter:
        def find_run_by_idempotency_key(self, key, *, session_id):
            return runtime_adapter.RunStatus(run_id=key, session_id=session_id, status="not_found")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: MissingAdapter())

    reconciled = routes._expert_team_run_with_execution_truth(
        tmp_path,
        expert_teams.read_expert_team_run(tmp_path, ready["run_id"]),
    )
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert reconciled["workflow_state"] == "start_failed"
    assert stored["workflow_state"] == "start_failed"
    assert stored["execution_start_id"] == ""
    resumed = expert_teams.resume_expert_team(
        tmp_path,
        _control(stored, "resume-expired-start"),
    )
    assert resumed["workflow_state"] == "ready_to_generate"


def test_old_start_failure_cannot_clear_replacement_lease(tmp_path):
    from api import expert_teams

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-old-start-failure")
    first = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=ready["version"],
    )
    failed = expert_teams.mark_expert_team_execution_start_failed(
        tmp_path,
        ready["run_id"],
        "remote start was explicitly not found",
        execution_start_id=first["execution_start_id"],
    )
    resumed = expert_teams.resume_expert_team(
        tmp_path,
        _control(failed, "resume-after-not-found"),
    )
    replacement = expert_teams.reserve_expert_team_execution_start(
        tmp_path,
        ready["run_id"],
        expected_version=resumed["version"],
    )

    with pytest.raises(expert_teams.ExpertTeamStateConflict) as stale:
        expert_teams.mark_expert_team_execution_start_failed(
            tmp_path,
            ready["run_id"],
            "old start failed",
            execution_start_id=first["execution_start_id"],
        )
    assert stale.value.code == "stale_start"
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert stored["workflow_state"] == "starting"
    assert stored["execution_start_id"] == replacement["execution_start_id"]
    assert stored["version"] == replacement["version"]


def test_route_old_start_commit_cannot_overwrite_replacement_lease(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-route-old-start")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    replacement_ids = []

    def rotate_lease(*_args, **_kwargs):
        current = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
        failed = expert_teams.mark_expert_team_execution_start_failed(
            tmp_path,
            ready["run_id"],
            "remote start was explicitly not found",
            execution_start_id=current["execution_start_id"],
        )
        resumed = expert_teams.resume_expert_team(
            tmp_path,
            _control(failed, "route-resume-after-not-found"),
        )
        replacement = expert_teams.reserve_expert_team_execution_start(
            tmp_path,
            ready["run_id"],
            expected_version=resumed["version"],
        )
        replacement_ids.append(replacement["execution_start_id"])
        return {"stream_id": "old-start-stream", "turn_id": "old-start-turn"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", rotate_lease)
    monkeypatch.setattr(routes, "cancel_stream", lambda _stream_id: True)
    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert status == 409
    assert payload["code"] == "stale_start"
    assert stored["workflow_state"] == "starting"
    assert stored["execution_start_id"] == replacement_ids[-1]


def test_route_late_start_commit_is_idempotent_after_same_reservation_was_reconciled(
    monkeypatch,
    tmp_path,
):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-route-reconciled-start")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    cancelled = []

    def reconcile_same_start_before_return(*_args, **_kwargs):
        current = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
        expert_teams.mark_expert_team_execution_started(
            tmp_path,
            ready["run_id"],
            {
                "stream_id": "same-runtime-run",
                "runtime_run_id": "same-runtime-run",
                "runtime_adapter": "LegacyJournalRuntimeAdapter",
                "execution_start_id": current["execution_start_id"],
            },
        )
        return {"stream_id": "same-runtime-run", "turn_id": "late-original-turn"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", reconcile_same_start_before_return)
    monkeypatch.setattr(routes, "cancel_stream", lambda stream_id: cancelled.append(stream_id) or True)

    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})

    assert status == 200
    assert payload["ok"] is True
    assert cancelled == []
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert stored["workflow_state"] == "generating"
    assert stored["execution_start_id"] == payload["run"]["execution_start_id"]
    assert stored["execution_runtime_run_id"] == "same-runtime-run"
    assert stored["execution_stream_id"] == "same-runtime-run"
    assert stored["execution_turn_id"] == "late-original-turn"
    assert stored["execution_attempt"] == 1


def test_route_late_start_cancels_different_runtime_after_reservation_was_reconciled(
    monkeypatch,
    tmp_path,
):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-route-reconciled-winner")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    cancelled = []

    def reconcile_winner_before_loser_returns(*_args, **_kwargs):
        current = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
        expert_teams.mark_expert_team_execution_started(
            tmp_path,
            ready["run_id"],
            {
                "stream_id": "winning-runtime-run",
                "runtime_run_id": "winning-runtime-run",
                "runtime_adapter": "LegacyJournalRuntimeAdapter",
                "execution_start_id": current["execution_start_id"],
            },
        )
        return {"stream_id": "late-losing-runtime", "turn_id": "late-losing-turn"}

    monkeypatch.setattr(routes, "_start_chat_stream_for_session", reconcile_winner_before_loser_returns)
    monkeypatch.setattr(routes, "cancel_stream", lambda stream_id: cancelled.append(stream_id) or True)

    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})

    assert status == 409
    assert payload["ok"] is False
    assert cancelled == ["late-losing-runtime"]
    stored = expert_teams.read_expert_team_run(tmp_path, ready["run_id"])
    assert stored["workflow_state"] == "generating"
    assert stored["execution_runtime_run_id"] == "winning-runtime-run"
    assert stored["execution_attempt"] == 1


@pytest.mark.parametrize(
    "events",
    [
        lambda content: [{"event": "message", "payload": {"content": content}}],
        lambda content: [
            {"type": "token", "data": {"text": content[: len(content) // 2]}},
            {"type": "token", "data": {"text": content[len(content) // 2 :]}},
            {"type": "done", "data": {"ok": True}},
        ],
    ],
    ids=["payload-content", "data-text-tokens"],
)
def test_remote_completed_parses_runner_event_contract(monkeypatch, tmp_path, events):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-remote-contract")
    generating = _started(expert_teams, tmp_path, ready, "remote-contract-stream")
    generating["execution_runtime_run_id"] = "remote-contract-run"
    generating["execution_runtime_adapter"] = "RunnerRuntimeAdapter"
    write_run(tmp_path, generating)
    content = _valid_plan_content()
    observe_calls = 0

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status="completed",
            )

        def observe_run(self, run_id, *, cursor=None):
            nonlocal observe_calls
            observe_calls += 1
            page_events = []
            if observe_calls == 1:
                for index, event in enumerate(events(content), start=1):
                    event = dict(event)
                    event.setdefault("event_id", f"event-{index}")
                    event.setdefault("sequence", index)
                    page_events.append(event)
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                events=page_events,
                cursor=cursor,
                has_more=False if observe_calls > 1 else None,
                snapshot_complete=True if observe_calls > 1 else False,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())
    first_poll = routes._expert_team_run_with_execution_truth(tmp_path, generating)
    assert first_poll["workflow_state"] == "generating"
    observed = routes._expert_team_run_with_execution_truth(tmp_path, first_poll)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert observed["workflow_state"] == "awaiting_review"
    assert stored["workflow_state"] == "awaiting_review"
    assert stored["stage_outputs"][-1]["content"] == content


def test_start_409_never_cancels_preexisting_active_stream(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-existing-stream")
    session = SimpleNamespace(session_id=ready["session_id"], model="test", model_provider=None, messages=[])
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_taiji_license_blocked_status", lambda: None)
    monkeypatch.setattr(routes, "_resolve_compatible_session_model_state", lambda m, p: (m, p, False))
    cancelled = []

    def rejected_start(self, request):
        return runtime_adapter.RunStartResult(
            run_id="",
            session_id=request.session_id,
            stream_id="",
            payload={
                "_status": 409,
                "error": "session already has an active stream",
                "active_stream_id": "preexisting-stream",
            },
        )

    monkeypatch.setattr(runtime_adapter.LegacyJournalRuntimeAdapter, "start_run", rejected_start)
    monkeypatch.setattr(
        runtime_adapter.LegacyJournalRuntimeAdapter,
        "cancel_run",
        lambda self, run_id: cancelled.append(run_id) or runtime_adapter.ControlResult(True),
    )
    payload, status = routes._start_expert_team_execution(tmp_path, ready, {})
    assert status == 409
    assert payload["run"]["workflow_state"] == "start_failed"
    assert cancelled == []


@pytest.mark.parametrize("mismatch", ["status", "events"])
def test_remote_poll_rejects_mismatched_runtime_run_id(monkeypatch, tmp_path, mismatch):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(expert_teams, tmp_path, session_id=f"sid-remote-mismatch-{mismatch}")
    generating = _started(expert_teams, tmp_path, ready, f"remote-mismatch-{mismatch}")
    generating["execution_runtime_run_id"] = "remote-A"
    generating["execution_runtime_adapter"] = "RunnerRuntimeAdapter"
    write_run(tmp_path, generating)

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id="remote-B" if mismatch == "status" else run_id,
                session_id=generating["session_id"],
                status="completed",
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id="remote-B" if mismatch == "events" else run_id,
                events=[{"content": _valid_plan_content()}],
                cursor=cursor,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())
    routes._expert_team_run_with_execution_truth(tmp_path, generating)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert stored["workflow_state"] == "generating"
    assert stored.get("stage_outputs") == []


def test_only_recoverable_failures_expose_retry_action(tmp_path):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    run = expert_teams.start_expert_team(
        tmp_path,
        {"session_id": "sid-view-retry", "team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    for terminal_state in ("failed", "cancelled"):
        terminal = dict(run)
        terminal["workflow_state"] = terminal_state
        view = expert_team_run_view(terminal)
        assert (view["presentation"].get("primary_action") or {}).get("id") != "regenerate"
        assert view["actions"]["can_retry"] is False
    recoverable = dict(run)
    recoverable["workflow_state"] = "start_failed"
    recoverable_view = expert_team_run_view(recoverable)
    assert recoverable_view["presentation"]["primary_action"]["id"] == "regenerate"
    assert recoverable_view["actions"]["can_retry"] is True


def test_legacy_post_generation_binding_failure_never_presents_as_start_failure(tmp_path):
    from api import expert_teams
    from api.expert_teams.view import expert_team_run_view

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-legacy-unbound")
    legacy = dict(ready)
    legacy["workflow_state"] = "start_failed"
    legacy["last_execution_error"] = "本轮生成已结束，但没有检测到有效结果，请重新尝试。"

    view = expert_team_run_view(legacy)

    assert view["presentation"]["state"] == "legacy_result_unverified"
    assert view["presentation"]["title"] == "历史结果未绑定"
    assert view["presentation"]["primary_action"]["id"] == "regenerate_unverified"
    assert "已有内容会保留" in view["presentation"]["detail"]
    assert view["actions"]["can_retry"] is False


def test_cancel_persists_cancelling_before_runtime_side_effect(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-cancel-intent")
    generating = _started(expert_teams, tmp_path, ready, "stream-cancel-intent")
    session = SimpleNamespace(session_id=generating["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    observed = []
    remote_status = "running"

    class AcceptingAdapter:
        def cancel_run(self, run_id):
            persisted = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
            observed.append((persisted["workflow_state"], persisted.get("cancel_request_id"), run_id))
            return runtime_adapter.ControlResult(True, status="accepted")

        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status=remote_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[],
                cursor=cursor,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: AcceptingAdapter())
    handler = _post(routes, "/api/expert-teams/cancel", _control(generating, "cancel-intent"))
    assert handler.status == 202
    assert observed == [("cancelling", "cancel-intent", "stream-cancel-intent")]
    pending = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert pending["workflow_state"] == "cancelling"
    assert pending["cancel_outcome"] == "accepted"
    remote_status = "cancelled"
    settled = routes._expert_team_run_with_execution_truth(tmp_path, pending)
    assert settled["workflow_state"] == "cancelled"


def test_cancel_rejection_rolls_back_without_losing_execution(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-cancel-rollback")
    generating = _started(expert_teams, tmp_path, ready, "stream-cancel-rollback")
    session = SimpleNamespace(session_id=generating["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)
    observed_states = []

    class RejectingAdapter:
        def cancel_run(self, _run_id):
            observed_states.append(
                expert_teams.read_expert_team_run(tmp_path, generating["run_id"])["workflow_state"]
            )
            return runtime_adapter.ControlResult(False, status="rejected", safe_message="runtime rejected")

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: RejectingAdapter())
    handler = _post(routes, "/api/expert-teams/cancel", _control(generating, "cancel-rollback"))
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert handler.status == 409
    assert observed_states == ["cancelling"]
    assert stored["workflow_state"] == "generating"
    assert stored["execution_stream_id"] == generating["execution_stream_id"]
    assert stored["execution_runtime_run_id"] == generating["execution_runtime_run_id"]
    assert stored["execution_attempt"] == generating["execution_attempt"]
    assert stored["last_execution_error"] == "runtime rejected"


def test_cancel_final_write_failure_reconciles_from_cancelling(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams import runtime

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-cancel-reconcile")
    generating = _started(expert_teams, tmp_path, ready, "stream-cancel-reconcile")
    session = SimpleNamespace(session_id=generating["session_id"], model="test", model_provider=None, messages=[])
    _configure_route(monkeypatch, routes, tmp_path, session)

    class AcceptingAdapter:
        def cancel_run(self, _run_id):
            return runtime_adapter.ControlResult(True, status="accepted")

        def get_run(self, run_id):
            return runtime_adapter.RunStatus(run_id=run_id, status="cancelled")

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(run_id=run_id, events=[], cursor=cursor)

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: AcceptingAdapter())
    original_write = runtime.write_run
    fail_final_write = {"enabled": True}

    def flaky_write(workspace, run):
        if fail_final_write["enabled"] and run.get("workflow_state") == "cancelled":
            raise OSError("disk full")
        return original_write(workspace, run)

    monkeypatch.setattr(runtime, "write_run", flaky_write)
    handler = _post(routes, "/api/expert-teams/cancel", _control(generating, "cancel-reconcile"))
    persisted_intent = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert handler.status == 202
    assert persisted_intent["workflow_state"] == "cancelling"
    assert persisted_intent["cancel_outcome"] == "accepted"
    assert persisted_intent["cancel_request_id"] == "cancel-reconcile"
    assert persisted_intent["execution_runtime_run_id"] == generating["execution_runtime_run_id"]

    failed_commit = routes._expert_team_run_with_execution_truth(tmp_path, persisted_intent)
    assert failed_commit["workflow_state"] == "cancelling"
    still_pending = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    assert still_pending["workflow_state"] == "cancelling"
    assert still_pending["execution_runtime_run_id"] == generating["execution_runtime_run_id"]

    fail_final_write["enabled"] = False
    reconciled = routes._expert_team_run_with_execution_truth(tmp_path, still_pending)
    assert reconciled["workflow_state"] == "cancelled"
    assert expert_teams.read_expert_team_run(tmp_path, generating["run_id"])["workflow_state"] == "cancelled"


def test_start_route_rejects_missing_session_id(monkeypatch, tmp_path):
    from api import routes

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_expert_team_workspace", lambda _sid=None: tmp_path)
    handler = _post(
        routes,
        "/api/expert-teams/start",
        {"team_id": "content-creator-team", "prompt": "起草工作汇报"},
    )
    assert handler.status == 400
    assert "session_id" in handler.json_body()["error"]


def test_local_stream_ended_without_bound_result_enters_result_unverified(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-local-empty-recoverable")
    generating = _started(expert_teams, tmp_path, ready, "stream-local-empty")
    attempt = generating["execution_attempt"]
    stage_id = (generating.get("current_stage") or {}).get("task_id")
    confirmed = [
        (item.get("id"), item.get("answer"), item.get("status"))
        for item in generating.get("questions") or []
    ]
    session = SimpleNamespace(
        session_id=generating["session_id"],
        active_stream_id=None,
        pending_user_message=None,
        messages=[],
    )
    monkeypatch.setattr(routes, "_active_stream_id_set", lambda: set())
    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: session)

    recovered = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert recovered["workflow_state"] == "result_unverified"
    assert recovered["execution_attempt"] == attempt
    assert recovered["execution_stream_id"] == "stream-local-empty"
    assert recovered["execution_stage_id"] == stage_id
    assert (recovered.get("current_stage") or {}).get("task_id") == stage_id
    assert [
        (item.get("id"), item.get("answer"), item.get("status"))
        for item in recovered.get("questions") or []
    ] == confirmed
    assert recovered["view"]["presentation"]["primary_action"]["id"] == "refresh"
    assert recovered["view"]["presentation"]["secondary_actions"][0]["id"] == "regenerate_unverified"
    assert recovered["view"]["actions"]["can_retry"] is False


def test_local_stream_terminal_gateway_error_enters_generation_failed(monkeypatch, tmp_path):
    from api import expert_teams, routes, turn_journal

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-gateway-failed")
    generating = _started(expert_teams, tmp_path, ready, "stream-gateway-failed", "turn-gateway-failed")
    session = SimpleNamespace(
        session_id=generating["session_id"], active_stream_id=None,
        pending_user_message=None, messages=[],
    )
    monkeypatch.setattr(routes, "_active_stream_id_set", lambda: set())
    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: session)
    monkeypatch.setattr(turn_journal, "read_turn_journal", lambda _sid: {"events": [{
        "event": "interrupted", "stream_id": "stream-gateway-failed",
        "turn_id": "turn-gateway-failed", "reason": "model_configuration_error",
    }], "malformed": []})

    failed = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert failed["workflow_state"] == "generation_failed"
    assert failed["view"]["presentation"]["title"] == "生成失败"
    assert failed["view"]["presentation"]["primary_action"]["id"] == "regenerate"


def test_result_unverified_reconciles_started_intent_without_rerun(monkeypatch, tmp_path):
    from api import expert_teams, models, routes, turn_journal

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-late-result")
    generating = _started(expert_teams, tmp_path, ready, "stream-late-result", "turn-late-result")
    pending = expert_teams.mark_expert_team_result_unverified(
        tmp_path,
        generating["run_id"],
        "等待核验",
        stream_id=generating["execution_stream_id"],
    )
    content = _valid_plan_content()
    user_content = "专家团开始生成：计划"
    session = SimpleNamespace(
        session_id=pending["session_id"],
        active_stream_id=None,
        pending_user_message=None,
        messages=[
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": content},
        ],
    )
    monkeypatch.setattr(routes, "_active_stream_id_set", lambda: set())
    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: session)
    monkeypatch.setattr(models.Session, "load", staticmethod(lambda _sid: session))
    monkeypatch.setattr(
        turn_journal,
        "read_turn_journal",
        lambda _sid: {
            "events": [{
                "event": "assistant_started",
                "stream_id": "stream-late-result",
                "turn_id": "turn-late-result",
                "assistant_message_index": 1,
                "assistant_content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "user_message_index": 0,
                "user_content_sha256": hashlib.sha256(user_content.encode()).hexdigest(),
            }],
            "malformed": [],
        },
    )

    recovered = routes._expert_team_run_with_execution_truth(tmp_path, pending)

    assert recovered["workflow_state"] == "awaiting_review"
    assert recovered["execution_attempt"] == generating["execution_attempt"]
    assert len(recovered["stage_outputs"]) == 1


def test_result_unverified_rejects_started_intent_when_content_digest_drifts(monkeypatch, tmp_path):
    from api import expert_teams, models, routes, turn_journal

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-drifted-result")
    generating = _started(expert_teams, tmp_path, ready, "stream-drifted", "turn-drifted")
    pending = expert_teams.mark_expert_team_result_unverified(
        tmp_path, generating["run_id"], "等待核验", stream_id="stream-drifted"
    )
    session = SimpleNamespace(
        session_id=pending["session_id"], active_stream_id=None, pending_user_message=None,
        messages=[{"role": "user", "content": "请求"}, {"role": "assistant", "content": _valid_plan_content()}],
    )
    monkeypatch.setattr(routes, "_active_stream_id_set", lambda: set())
    monkeypatch.setattr(routes, "get_session", lambda _sid, **_kwargs: session)
    monkeypatch.setattr(models.Session, "load", staticmethod(lambda _sid: session))
    monkeypatch.setattr(turn_journal, "read_turn_journal", lambda _sid: {"events": [{
        "event": "assistant_started", "stream_id": "stream-drifted", "turn_id": "turn-drifted",
        "assistant_message_index": 1, "assistant_content_sha256": hashlib.sha256(b"other").hexdigest(),
        "user_message_index": 0, "user_content_sha256": hashlib.sha256("请求".encode()).hexdigest(),
    }], "malformed": []})

    observed = routes._expert_team_run_with_execution_truth(tmp_path, pending)

    assert observed["workflow_state"] == "result_unverified"
    assert observed["stage_outputs"] == []


@pytest.mark.parametrize("runtime_status", ["failed", "error", "errored"])
def test_remote_runtime_failure_is_recoverable(monkeypatch, tmp_path, runtime_status):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(
        expert_teams,
        tmp_path,
        session_id=f"sid-remote-{runtime_status}-recoverable",
    )
    generating = _started(expert_teams, tmp_path, ready, f"stream-{runtime_status}")
    generating["execution_runtime_run_id"] = f"remote-{runtime_status}"
    generating["execution_runtime_adapter"] = "RunnerRuntimeAdapter"
    generating = write_run(tmp_path, generating)
    attempt = generating["execution_attempt"]
    stage_id = (generating.get("current_stage") or {}).get("task_id")

    class FailedAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status=runtime_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[],
                request_cursor=cursor,
                cursor=cursor,
                has_more=False,
                snapshot_complete=True,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FailedAdapter())

    recovered = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert recovered["workflow_state"] == "generation_failed"
    assert recovered["execution_attempt"] == attempt
    assert recovered["execution_stream_id"] == ""
    assert (recovered.get("current_stage") or {}).get("task_id") == stage_id
    assert recovered["view"]["presentation"]["primary_action"]["id"] == "regenerate"
    assert recovered["view"]["actions"]["can_retry"] is True


@pytest.mark.parametrize(
    "runtime_status",
    ["cancelled", "canceled", "interrupted", "interrupted-by-user"],
)
def test_remote_runtime_cancelled_stays_terminal_without_retry(monkeypatch, tmp_path, runtime_status):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    ready = _ready_run(
        expert_teams,
        tmp_path,
        session_id=f"sid-remote-{runtime_status}-terminal",
    )
    generating = _started(expert_teams, tmp_path, ready, f"stream-{runtime_status}")
    generating["execution_runtime_run_id"] = f"remote-{runtime_status}"
    generating["execution_runtime_adapter"] = "RunnerRuntimeAdapter"
    generating = write_run(tmp_path, generating)

    class CancelledAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=generating["session_id"],
                status=runtime_status,
            )

        def observe_run(self, run_id, *, cursor=None):
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                session_id=generating["session_id"],
                events=[],
                request_cursor=cursor,
                cursor=cursor,
                has_more=False,
                snapshot_complete=True,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: CancelledAdapter())

    terminal = routes._expert_team_run_with_execution_truth(tmp_path, generating)

    assert terminal["workflow_state"] == "cancelled"
    assert terminal["view"]["presentation"].get("primary_action") is None
    assert terminal["view"]["actions"]["can_retry"] is False
    with pytest.raises(expert_teams.ExpertTeamStateConflict) as error:
        expert_teams.resume_expert_team(
            tmp_path,
            _control(terminal, f"resume-terminal-{runtime_status}"),
        )
    assert error.value.code == "terminal_state"


def test_http_resume_restarts_recoverable_execution_failure(monkeypatch, tmp_path):
    from api import expert_teams, routes

    ready = _ready_run(expert_teams, tmp_path, session_id="sid-http-recoverable-resume")
    generating = _started(expert_teams, tmp_path, ready, "stream-http-failed")
    failed = expert_teams.fail_expert_team_execution(
        tmp_path,
        generating["run_id"],
        "本轮执行未返回有效结果，请重新尝试。",
        stream_id=generating["execution_stream_id"],
    )
    attempt = generating["execution_attempt"]
    stage_id = (generating.get("current_stage") or {}).get("task_id")
    session = SimpleNamespace(
        session_id=generating["session_id"],
        model="test-model",
        model_provider=None,
        messages=[],
    )
    _configure_route(monkeypatch, routes, tmp_path, session)
    starts = []

    def restart_execution(workspace, run, _body):
        starts.append(run["run_id"])
        reserved = expert_teams.reserve_expert_team_execution_start(
            workspace,
            run["run_id"],
            expected_version=run["version"],
            runtime_adapter="LegacyJournalRuntimeAdapter",
        )
        started = expert_teams.mark_expert_team_execution_started(
            workspace,
            run["run_id"],
            {
                "stream_id": "stream-http-retry",
                "runtime_run_id": "stream-http-retry",
                "runtime_adapter": "LegacyJournalRuntimeAdapter",
                "execution_start_id": reserved["execution_start_id"],
            },
        )
        return {"ok": True, "run": started, "stream_id": "stream-http-retry"}, 202

    monkeypatch.setattr(routes, "_start_expert_team_execution", restart_execution)
    handler = _post(
        routes,
        "/api/expert-teams/resume",
        _control(failed, "resume-recoverable-execution"),
    )
    payload = handler.json_body()

    assert handler.status == 202
    assert starts == [generating["run_id"]]
    assert payload["run"]["workflow_state"] == "generating"
    assert payload["run"]["execution_attempt"] == attempt + 1
    assert (payload["run"].get("current_stage") or {}).get("task_id") == stage_id
