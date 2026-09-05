from pathlib import Path

import json


ROOT = Path(__file__).resolve().parents[1]


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    start = src.find(f"def {name}")
    assert start != -1, f"{name} not found"
    next_def = src.find("\n            def ", start + 1)
    assert next_def != -1, f"end of {name} not found"
    return src[start:next_def]


def test_tool_start_callback_emits_existing_tool_sse_event_with_tool_id():
    src = _read("api/streaming.py")
    block = _function_block(src, "on_tool_start")

    assert "put('tool'" in block, (
        "The dedicated Hermes Agent tool_start_callback must emit the existing "
        "tool SSE event; otherwise WebUI stays visually silent while tools run."
    )
    assert "'event_type': 'tool.started'" in block
    assert "'tid': tool_call_id" in block, (
        "Live frontend cards need the tool_call_id so tool_complete can update "
        "the running card in place."
    )
    assert "_live_tool_event_start_ids" in block, (
        "Tool start SSE emission should be idempotent per callback id."
    )
    assert "STREAM_LIVE_TOOL_CALLS" in block and "'done': False" in block


def test_tool_complete_callback_emits_existing_tool_complete_sse_event_with_tool_id():
    src = _read("api/streaming.py")
    block = _function_block(src, "on_tool_complete")

    assert "put('tool_complete'" in block, (
        "The dedicated Hermes Agent tool_complete_callback must emit the existing "
        "tool_complete SSE event so the frontend can settle the running tool card."
    )
    assert "'event_type': 'tool.completed'" in block
    assert "'tid': tool_call_id" in block
    assert "_live_tool_event_complete_ids" in block, (
        "Tool completion SSE emission should be idempotent per callback id."
    )
    assert "result_snippet = public_egress_scrub(" in block
    assert "_tool_result_snippet(function_result)" in block
    assert 'surface="stream_tool_result"' in block
    assert "result_snippet = _tool_result_snippet(function_result)" not in block
    assert "_checkpoint_activity[0] += 1" in block


def test_legacy_progress_events_are_suppressed_when_structured_callbacks_are_wired():
    src = _read("api/streaming.py")
    block = _function_block(src, "on_tool")

    assert "event_type in (None, 'tool.started') and 'tool_start_callback' in _agent_params" in block
    assert "event_type == 'tool.completed' and 'tool_complete_callback' in _agent_params" in block
    assert block.index("'tool_start_callback' in _agent_params") < block.index("put('tool'")
    assert block.index("'tool_complete_callback' in _agent_params") < block.index("put('tool_complete'")


def test_tool_callback_events_keep_existing_frontend_event_contract():
    messages = _read("static/messages.js")
    ui = _read("static/ui.js")

    assert "source.addEventListener('tool',e=>{" in messages
    assert "source.addEventListener('tool_complete',e=>{" in messages
    assert "tid:d.tid" in messages
    assert "data-live-tid" in ui
    assert "existing.replaceWith(replacement)" in ui


def test_completed_file_tool_projects_existing_workspace_file_only(tmp_path):
    from api.streaming import (
        _extract_tool_calls_from_messages,
        _project_tool_artifact_paths_onto_messages,
        _sanitize_messages_for_api,
        _tool_result_is_error,
        _workspace_artifact_path_from_tool_completion,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "成果.md"
    artifact.write_text("ok", encoding="utf-8")

    assert _workspace_artifact_path_from_tool_completion(
        "write_file",
        {"path": str(artifact)},
        json.dumps({"bytes_written": 2}),
        str(workspace),
    ) == "成果.md"
    assert _workspace_artifact_path_from_tool_completion(
        "write_file",
        {"path": str(artifact)},
        json.dumps({"bytes_written": 0, "error": "denied"}),
        str(workspace),
    ) == ""

    for failed in (
        "Error executing tool 'write_file': permission denied",
        "[Tool execution cancelled — write_file was skipped due to user interrupt]",
    ):
        assert _tool_result_is_error(failed) is True
        assert _workspace_artifact_path_from_tool_completion(
            "write_file", {"path": str(artifact)}, failed, str(workspace)
        ) == ""

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "function": {"name": "write_file", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": json.dumps({"bytes_written": 2})},
    ]
    first = _extract_tool_calls_from_messages(
        messages,
        live_tool_calls=[{
            "name": "write_file",
            "tid": "call-1",
            "status": "completed",
            "done": True,
            "is_error": False,
            "artifact_path": "成果.md",
        }],
    )
    second = _extract_tool_calls_from_messages(messages, live_tool_calls=[], prior_tool_calls=first)
    assert first[0]["artifact_path"] == "成果.md"
    assert second[0]["artifact_path"] == "成果.md"

    public_history = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"event_type": "tool.started", "name": "write_file", "tid": "call-1", "artifact_path": "provider-spoof.md"}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": None},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"event_type": "tool.started", "name": "write_file", "tid": "call-2", "artifact_path": "provider-spoof-failure.md"}],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": None},
    ]
    rebuilt = _extract_tool_calls_from_messages(
        public_history,
        live_tool_calls=[{"name": "write_file", "tid": "call-2", "is_error": True}],
        prior_tool_calls=first,
    )
    by_tid = {call["tid"]: call for call in rebuilt}
    assert by_tid["call-1"]["artifact_path"] == "成果.md"
    assert "artifact_path" not in by_tid["call-2"]
    _project_tool_artifact_paths_onto_messages(public_history, rebuilt)
    assert public_history[0]["tool_calls"][0]["artifact_path"] == "成果.md"
    assert "artifact_path" not in public_history[2]["tool_calls"][0]

    failed_same_tid = _extract_tool_calls_from_messages(
        messages,
        live_tool_calls=[{"name": "write_file", "tid": "call-1", "is_error": True}],
        prior_tool_calls=first,
    )
    assert failed_same_tid[0]["is_error"] is True
    assert "artifact_path" not in failed_same_tid[0]

    running_same_tid = _extract_tool_calls_from_messages(
        messages,
        live_tool_calls=[{
            "name": "write_file",
            "tid": "call-1",
            "status": "running",
            "done": False,
        }],
        prior_tool_calls=first,
    )
    assert "artifact_path" not in running_same_tid[0]

    unmatched = [{"role": "assistant", "tool_calls": [{"tid": "unknown", "name": "write_file", "artifact_path": "provider-spoof.md"}]}]
    _project_tool_artifact_paths_onto_messages(unmatched, rebuilt)
    assert "artifact_path" not in unmatched[0]["tool_calls"][0]

    anthropic = [
        {
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "anthropic-1",
                "name": "write_file",
                "input": {"path": "成果.md"},
                "artifact_path": "provider-spoof.md",
            }],
        },
        {
            "role": "tool",
            "tool_use_id": "anthropic-1",
            "content": json.dumps({"bytes_written": 2}),
        },
    ]
    anthropic_summary = _extract_tool_calls_from_messages(
        anthropic,
        live_tool_calls=[{
            "name": "write_file",
            "tid": "anthropic-1",
            "done": True,
            "is_error": False,
            "artifact_path": "成果.md",
        }],
    )
    _project_tool_artifact_paths_onto_messages(anthropic, anthropic_summary)
    block = anthropic[0]["content"][0]
    assert block["artifact_path"] == "成果.md"
    assert block["status"] == "completed" and block["done"] is True

    from api.brand_privacy import public_session_projection

    public = public_session_projection({
        "session_id": "anthropic-session",
        "messages": anthropic,
        "tool_calls": anthropic_summary,
    })
    assert public["messages"][0]["content"][0]["artifact_path"] == "成果.md"

    provider_messages = _sanitize_messages_for_api(anthropic)
    provider_block = provider_messages[0]["content"][0]
    assert "artifact_path" not in provider_block
    assert "status" not in provider_block and "done" not in provider_block

    failed_prior = _extract_tool_calls_from_messages(
        messages,
        prior_tool_calls=[{
            "name": "write_file",
            "tid": "call-1",
            "status": "failed",
            "is_error": True,
            "artifact_path": "不应回填.md",
        }],
    )
    assert "artifact_path" not in failed_prior[0]

    tidless = _extract_tool_calls_from_messages(
        [{"role": "assistant", "content": ""}, {"role": "tool", "content": "ok"}],
        live_tool_calls=[{
            "name": "write_file",
            "tid": "",
            "done": True,
            "is_error": False,
            "artifact_path": "无标识.md",
        }],
    )
    assert "artifact_path" not in tidless[0]

    # Exercise the real public HTTP load path used by browser refresh. With a
    # message window, routes deliberately omit session-level summaries when
    # the returned messages carry tool metadata, so the Anthropic tool_use
    # block itself must retain the validated artifact projection.
    import urllib.parse
    import urllib.request
    import uuid

    from api.models import Session
    from tests._pytest_port import BASE

    sid = f"artifact-anthropic-{uuid.uuid4().hex[:10]}"
    persisted_messages = json.loads(json.dumps(anthropic, ensure_ascii=False))
    _project_tool_artifact_paths_onto_messages(persisted_messages, anthropic_summary)
    persisted = Session(
        session_id=sid,
        workspace=str(workspace),
        messages=persisted_messages,
        tool_calls=anthropic_summary,
        profile="default",
    )
    persisted.save()
    query = urllib.parse.urlencode({
        "session_id": sid,
        "messages": 1,
        "resolve_model": 0,
        "msg_limit": 30,
    })
    with urllib.request.urlopen(f"{BASE}/api/session?{query}", timeout=10) as response:
        payload = json.loads(response.read())["session"]
    assert payload["tool_calls"] == []
    public_block = payload["messages"][0]["content"][0]
    assert public_block["type"] == "tool_use"
    assert public_block["tid"] == "anthropic-1"
    assert public_block["artifact_path"] == "成果.md"

    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    assert _workspace_artifact_path_from_tool_completion(
        "write_file",
        {"path": str(outside)},
        json.dumps({"bytes_written": 7}),
        str(workspace),
    ) == ""
