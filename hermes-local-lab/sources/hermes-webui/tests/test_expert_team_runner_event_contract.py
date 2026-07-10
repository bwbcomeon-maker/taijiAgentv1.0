"""RED contracts for durable, identity-safe expert-team Runner observation."""

from types import SimpleNamespace

import pytest


def _valid_plan_content() -> str:
    return (
        "阶段摘要：已形成专家团执行计划。\n"
        "正文草稿：本阶段只确认材料定位、使用对象、结构边界和后续分工，不直接起草完整正文。\n"
        "待补充事项：请补充具体数据。\n"
        "建议下一步：进入素材整理。"
    )


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
        _control(
            run,
            f"required-{run['run_id']}",
            answers=required_answers,
        ),
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


def test_payload_text_token_deltas_are_joined_in_order():
    from api import routes

    result = routes._expert_team_remote_result_content(
        [
            {"type": "token.delta", "payload": {"text": "前半段"}},
            {"type": "token.delta", "payload": {"text": "后半段"}},
        ]
    )

    assert result == "前半段后半段"


def test_payload_content_token_deltas_are_joined_in_order():
    from api import routes

    result = routes._expert_team_remote_result_content(
        [
            {"type": "token.delta", "payload": {"content": "前半段"}},
            {"type": "token.delta", "payload": {"content": "后半段"}},
        ]
    )

    assert result == "前半段后半段"


def test_reasoning_tool_and_private_events_never_override_public_tokens():
    from api import routes

    result = routes._expert_team_remote_result_content(
        [
            {"type": "reasoning", "data": {"text": "PRIVATE REASONING"}},
            {"type": "private", "payload": {"content": "PRIVATE CHANNEL"}},
            {"type": "tool.completed", "payload": {"output": "SECRET TOOL OUTPUT"}},
            {"type": "token", "data": {"text": "PUBLIC RESULT"}},
        ]
    )

    assert result == "PUBLIC RESULT"


def test_private_only_events_produce_no_public_result():
    from api import routes

    result = routes._expert_team_remote_result_content(
        [
            {"type": "reasoning", "data": {"text": "PRIVATE REASONING"}},
            {"type": "private", "payload": {"content": "PRIVATE CHANNEL"}},
            {"type": "tool.completed", "payload": {"output": "SECRET TOOL OUTPUT"}},
        ]
    )

    assert result == ""


@pytest.mark.parametrize(
    ("layer", "field"),
    [
        ("status", "run_id"),
        ("status", "session_id"),
        ("event_stream", "run_id"),
        ("event_stream", "session_id"),
        ("event", "run_id"),
        ("event", "session_id"),
    ],
    ids=[
        "status-run-id",
        "status-session-id",
        "event-stream-run-id",
        "event-stream-session-id",
        "event-run-id",
        "event-session-id",
    ],
)
def test_remote_identity_mismatch_at_every_layer_is_rejected(monkeypatch, tmp_path, layer, field):
    from api import expert_teams, routes, runtime_adapter

    session_id = f"sid-identity-{layer}-{field}"
    runtime_run_id = f"runtime-identity-{layer}-{field}"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )

    wrong_run_id = "runtime-from-another-expert-team"
    wrong_session_id = "session-from-another-conversation"
    event = {
        "type": "message",
        "payload": {"content": _valid_plan_content()},
        "run_id": runtime_run_id,
        "session_id": session_id,
    }
    if layer == "event":
        event[field] = wrong_run_id if field == "run_id" else wrong_session_id

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=wrong_run_id if layer == "status" and field == "run_id" else run_id,
                session_id=(
                    wrong_session_id
                    if layer == "status" and field == "session_id"
                    else session_id
                ),
                status="completed",
            )

        def observe_run(self, run_id, *, cursor=None):
            return SimpleNamespace(
                run_id=(
                    wrong_run_id
                    if layer == "event_stream" and field == "run_id"
                    else run_id
                ),
                session_id=(
                    wrong_session_id
                    if layer == "event_stream" and field == "session_id"
                    else session_id
                ),
                events=[event],
                cursor=cursor,
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())

    routes._expert_team_run_with_execution_truth(tmp_path, generating)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert stored["workflow_state"] == "generating"
    assert stored.get("stage_outputs") == []


def test_running_poll_persists_cursor_public_delta_and_seen_event_ids(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter

    session_id = "sid-cursor-persist"
    runtime_run_id = "runtime-cursor-persist"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    original_version = generating["version"]

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=session_id,
                status="running",
            )

        def observe_run(self, run_id, *, cursor=None):
            assert cursor is None
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                events=[
                    {
                        "event_id": "event-1",
                        "type": "token.delta",
                        "payload": {"text": "已持久化的前半段"},
                    }
                ],
                cursor="1",
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())

    routes._expert_team_run_with_execution_truth(tmp_path, generating)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert stored.get("execution_cursor") == "1"
    assert stored.get("execution_public_output_buffer") == "已持久化的前半段"
    assert stored.get("execution_seen_event_ids") == ["event-1"]
    assert stored["version"] == original_version


def test_restart_uses_cursor_and_dedupes_replayed_public_events(monkeypatch, tmp_path):
    from api import expert_teams, routes, runtime_adapter
    from api.expert_teams.storage import write_run

    session_id = "sid-cursor-restart"
    runtime_run_id = "runtime-cursor-restart"
    generating = _remote_generating(
        expert_teams,
        tmp_path,
        session_id=session_id,
        runtime_run_id=runtime_run_id,
    )
    full_content = _valid_plan_content()
    split_at = len(full_content) // 2
    first_part = full_content[:split_at]
    second_part = full_content[split_at:]
    generating["execution_cursor"] = "1"
    generating["execution_public_output_buffer"] = first_part
    generating["execution_seen_event_ids"] = ["event-1"]
    generating["execution_public_observations"] = [
        {
            "event_id": "event-1",
            "sequence": None,
            "kind": "delta",
            "text": first_part,
            "arrival": 1,
        }
    ]
    write_run(tmp_path, generating)
    cursors = []

    class FakeAdapter:
        def get_run(self, run_id):
            return runtime_adapter.RunStatus(
                run_id=run_id,
                session_id=session_id,
                status="completed",
            )

        def observe_run(self, run_id, *, cursor=None):
            cursors.append(cursor)
            if cursor == "2":
                return runtime_adapter.RunEventStream(
                    run_id=run_id,
                    events=[],
                    request_cursor=cursor,
                    cursor="2",
                    has_more=False,
                    snapshot_complete=True,
                )
            return runtime_adapter.RunEventStream(
                run_id=run_id,
                events=[
                    {
                        "event_id": "event-1",
                        "type": "token",
                        "data": {"text": first_part},
                    },
                    {
                        "event_id": "event-1",
                        "type": "token",
                        "data": {"text": first_part},
                    },
                    {
                        "event_id": "event-2",
                        "type": "token",
                        "data": {"text": second_part},
                    },
                ],
                request_cursor=cursor,
                cursor="2",
            )

    monkeypatch.setattr(routes, "_expert_team_runtime_adapter_for_run", lambda _run: FakeAdapter())

    restarted_run = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])
    first_poll = routes._expert_team_run_with_execution_truth(tmp_path, restarted_run)
    assert first_poll["workflow_state"] == "generating"
    routes._expert_team_run_with_execution_truth(tmp_path, first_poll)
    stored = expert_teams.read_expert_team_run(tmp_path, generating["run_id"])

    assert cursors == ["1", "2"]
    assert stored["workflow_state"] == "awaiting_review"
    assert stored["stage_outputs"][-1]["content"] == full_content
