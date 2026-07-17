import json

import pytest

from api.run_journal import (
    RunJournalWriter,
    append_run_event,
    find_run_summary,
    latest_run_summary,
    read_run_events,
    stale_interrupted_event,
)


@pytest.mark.parametrize(
    ("event_name", "use_writer"),
    [
        ("tool_complete", False),
        ("tool.started", True),
    ],
)
def test_run_journal_write_boundary_projects_tool_payloads(
    tmp_path,
    event_name,
    use_writer,
):
    canary = "sk-run-journal-canary-1234567890"
    absolute_path = "/Users/probe/private/input.txt"
    payload = {
        "name": "image_generate",
        "status": "completed",
        "summary": "图片生成完成",
        "args": {"path": absolute_path, "token": canary},
        "result": {"path": absolute_path, "token": canary},
        "token": canary,
        "path": absolute_path,
    }

    if use_writer:
        event = RunJournalWriter(
            "session_projection",
            "run_projection",
            session_dir=tmp_path,
        ).append_sse_event(event_name, payload)
    else:
        event = append_run_event(
            "session_projection",
            "run_projection",
            event_name,
            payload,
            session_dir=tmp_path,
        )

    raw = (
        tmp_path
        / "_run_journal"
        / "session_projection"
        / "run_projection.jsonl"
    ).read_text("utf-8")
    for forbidden in ("args", "result", "token", canary, absolute_path):
        assert forbidden not in raw
    assert event["payload"] == {
        "event_type": (
            "tool.completed" if event_name == "tool_complete" else "tool.started"
        ),
        "name": "image_generate",
        "status": "completed",
        "summary": "图片生成完成",
    }


def test_run_journal_submitted_projection_preserves_recovery_identity_only(tmp_path):
    canary = "sk-run-journal-canary-1234567890"
    absolute_path = "/Users/probe/private/input.txt"

    event = append_run_event(
        "session_submitted",
        "run_submitted",
        "submitted",
        {
            "source": "expert-team",
            "turn_id": "turn-submitted",
            "idempotency_key": "expert-team:start:submitted",
            "expert_team_run_id": "expert-run-1",
            "stage_id": "stage-1",
            "attempt": 2,
            "execution_start_id": "execution-start-1",
            "message": f"恢复 {absolute_path}",
            "args": {"path": absolute_path, "token": canary},
            "result": canary,
            "token": canary,
            "path": absolute_path,
        },
        session_dir=tmp_path,
    )

    expected = {
        "source": "expert-team",
        "turn_id": "turn-submitted",
        "idempotency_key": "expert-team:start:submitted",
        "expert_team_run_id": "expert-run-1",
        "stage_id": "stage-1",
        "attempt": 2,
        "execution_start_id": "execution-start-1",
    }
    assert event["payload"] == expected
    stored = read_run_events(
        "session_submitted",
        "run_submitted",
        session_dir=tmp_path,
    )["events"][0]
    assert stored["payload"] == expected
    raw = json.dumps(stored, ensure_ascii=False)
    for forbidden in (
        "args",
        "result",
        "token",
        "message",
        canary,
        absolute_path,
    ):
        assert forbidden not in raw


def test_run_journal_appends_monotonic_seq_and_reads_after_cursor(tmp_path):
    writer = RunJournalWriter("session_1", "run_1", session_dir=tmp_path)

    first = writer.append_sse_event("token", {"text": "hello"})
    second = writer.append_sse_event("done", {"session": {"session_id": "session_1"}})

    assert first["seq"] == 1
    assert first["event_id"] == "run_1:1"
    assert first["terminal"] is False
    assert second["seq"] == 2
    assert second["terminal"] is True
    assert second["terminal_state"] == "completed"

    journal = read_run_events("session_1", "run_1", after_seq=1, session_dir=tmp_path)
    assert [event["event"] for event in journal["events"]] == ["done"]


def test_run_journal_default_fsyncs_terminal_events_only(tmp_path, monkeypatch):
    path = tmp_path / "_run_journal" / "session_1" / "run_1.jsonl"
    path.parent.mkdir(parents=True)
    path.touch()
    fsync_calls = []
    monkeypatch.delenv("HERMES_WEBUI_RUN_JOURNAL_FSYNC", raising=False)
    monkeypatch.setattr("api.run_journal.os.fsync", lambda fd: fsync_calls.append(fd))

    append_run_event("session_1", "run_1", "token", {"text": "ok"}, session_dir=tmp_path)

    assert fsync_calls == []

    append_run_event("session_1", "run_1", "done", {"session": {}}, session_dir=tmp_path)

    assert len(fsync_calls) == 1


def test_run_journal_eager_fsync_mode_fsyncs_non_terminal_events(tmp_path, monkeypatch):
    path = tmp_path / "_run_journal" / "session_1" / "run_1.jsonl"
    path.parent.mkdir(parents=True)
    path.touch()
    fsync_calls = []
    monkeypatch.setenv("HERMES_WEBUI_RUN_JOURNAL_FSYNC", "eager")
    monkeypatch.setattr("api.run_journal.os.fsync", lambda fd: fsync_calls.append(fd))

    append_run_event("session_1", "run_1", "token", {"text": "ok"}, session_dir=tmp_path)

    assert len(fsync_calls) == 1


def test_run_journal_tolerates_malformed_lines(tmp_path):
    append_run_event("session_1", "run_1", "token", {"text": "ok"}, session_dir=tmp_path)
    path = tmp_path / "_run_journal" / "session_1" / "run_1.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n")
        fh.write(json.dumps(["wrong-shape"]) + "\n")

    journal = read_run_events("session_1", "run_1", session_dir=tmp_path)

    assert len(journal["events"]) == 1
    assert len(journal["malformed"]) == 2


def test_latest_summary_and_find_run_summary_classify_terminal_state(tmp_path):
    append_run_event("session_1", "run_1", "token", {"text": "ok"}, session_dir=tmp_path)
    append_run_event("session_1", "run_1", "cancel", {"message": "Cancelled by user"}, session_dir=tmp_path)

    summary = latest_run_summary("session_1", "run_1", session_dir=tmp_path)
    found = find_run_summary("run_1", session_dir=tmp_path)

    assert summary["terminal"] is True
    assert summary["terminal_state"] == "interrupted-by-user"
    assert summary["last_seq"] == 2
    assert found["session_id"] == "session_1"
    assert found["terminal_state"] == "interrupted-by-user"


def test_terminal_state_classification_distinguishes_crash_from_user_cancel(tmp_path):
    append_run_event("session_1", "run_cancelled", "cancel", {"message": "Cancelled by user"}, session_dir=tmp_path)
    append_run_event("session_1", "run_crashed", "apperror", {"type": "interrupted"}, session_dir=tmp_path)
    append_run_event("session_1", "run_failed", "apperror", {"type": "auth_mismatch"}, session_dir=tmp_path)
    append_run_event("session_1", "run_done", "done", {"session": {}}, session_dir=tmp_path)

    assert latest_run_summary("session_1", "run_cancelled", session_dir=tmp_path)["terminal_state"] == "interrupted-by-user"
    assert latest_run_summary("session_1", "run_crashed", session_dir=tmp_path)["terminal_state"] == "interrupted-by-crash"
    assert latest_run_summary("session_1", "run_failed", session_dir=tmp_path)["terminal_state"] == "errored"
    assert latest_run_summary("session_1", "run_done", session_dir=tmp_path)["terminal_state"] == "completed"


def test_summary_keeps_logical_terminal_state_when_stream_end_follows(tmp_path):
    append_run_event("session_1", "run_1", "apperror", {"type": "auth_mismatch"}, session_dir=tmp_path)
    append_run_event("session_1", "run_1", "stream_end", {"session_id": "session_1"}, session_dir=tmp_path)

    summary = latest_run_summary("session_1", "run_1", session_dir=tmp_path)

    assert summary["terminal"] is True
    assert summary["last_event"] == "stream_end"
    assert summary["terminal_state"] == "errored"


def test_stale_interrupted_event_reports_non_terminal_journal(tmp_path, monkeypatch):
    append_run_event("session_1", "run_1", "token", {"text": "partial"}, session_dir=tmp_path)

    monkeypatch.setattr("api.run_journal._default_session_dir", lambda: tmp_path)
    event = stale_interrupted_event("session_1", "run_1")
    assert event is not None

    assert event["event"] == "apperror"
    assert event["seq"] == 2
    assert event["terminal_state"] == "lost-worker-bookkeeping"
    assert event["payload"]["type"] == "interrupted"
    assert "last journaled event" in event["payload"]["hint"]
    assert "process restarted" not in event["payload"]["message"]
    assert "lost the live worker" not in event["payload"]["message"]
    assert "live worker stopped" in event["payload"]["message"]


def test_stale_interrupted_event_skips_terminal_journal(tmp_path, monkeypatch):
    append_run_event("session_1", "run_1", "done", {"session": {}}, session_dir=tmp_path)

    monkeypatch.setattr("api.run_journal._default_session_dir", lambda: tmp_path)

    assert stale_interrupted_event("session_1", "run_1") is None
