from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_streaming_uses_existing_artifact_registry_and_message_artifacts_contract():
    source = _read("api/streaming.py")

    assert "image_artifact_candidate_from_tool_completion" in source
    assert "ingest_image_artifact_candidates" in source
    assert "_artifact_message['artifacts']" in source
    assert "commit_artifacts(" in source
    assert "discard_pending_artifacts(" in source


def test_only_api_media_serves_artifacts_and_legacy_image_api_is_absent():
    routes = _read("api/routes.py")
    forbidden_route = "/api/" + "image-artifacts"
    forbidden_module = ROOT / "api" / ("image_" + "artifacts.py")

    assert 'parsed.path == "/api/media"' in routes
    assert "artifact_id" in routes and "session_id" in routes
    assert forbidden_route not in routes
    assert not forbidden_module.exists()


def test_cancel_happens_before_artifact_promotion():
    source = _read("api/streaming.py")
    cancellation = source.index("if cancel_event.is_set():", source.index("_artifact_candidates = []"))
    promotion = source.index("if _artifact_candidates:", cancellation)

    assert cancellation < promotion
    cancelled_block = source[cancellation:promotion]
    assert "_finalize_cancelled_turn" in cancelled_block
    assert "return" in cancelled_block


def test_session_save_failure_keeps_compensation_and_writeback_order():
    source = _read("api/streaming.py")
    start = source.index("_artifact_writeback_snapshot")
    end = source.index("if not ephemeral:", start)
    block = source[start:end]

    assert "_new_artifact_ids" in block
    assert "commit_artifacts(" in block
    assert "discard_pending_artifacts(" in block
    assert "rollback_registered_artifacts(" in block
    assert "_artifact_writeback_snapshot" in block
    assert block.index("commit_artifacts(") < block.index("rollback_registered_artifacts(")


def test_remote_download_failure_is_generic_and_signed_url_never_leaks(tmp_path):
    from api.artifacts import (
        ArtifactRegistry,
        ingest_image_artifact_candidates,
    )

    signed_url = (
        "https://cdn.example/private.png?"
        "X-Amz-Credential=secret&X-Amz-Signature=super-secret"
    )
    candidate = {
        "tool_name": "image_generate",
        "tool_call_id": "remote-call",
        "structured_result": {
            "success": True,
            "image": signed_url,
        },
    }

    rows, errors, created_ids = ingest_image_artifact_candidates(
        ArtifactRegistry(tmp_path / "artifacts"),
        session_id="session-a",
        turn_id="turn-a",
        candidates=[candidate],
        owner_run_id="run-a",
        return_created_ids=True,
    )

    public_payload = json.dumps(
        {"artifacts": rows, "artifact_errors": errors},
        ensure_ascii=False,
    )
    assert rows == []
    assert created_ids == set()
    assert errors == ["generated image could not be persisted"]
    assert signed_url not in public_payload
    assert "super-secret" not in public_payload
    assert "X-Amz-" not in public_payload
    assert not list((tmp_path / "artifacts").glob("*/manifest.json"))


def test_duplicate_structured_and_progress_callbacks_are_idempotent():
    streaming = _read("api/streaming.py")
    artifacts = _read("api/artifacts.py")

    assert "_live_tool_event_complete_ids" in streaming
    assert "tool_call_id not in _live_tool_event_complete_ids" in streaming
    assert "seen_tool_call_ids" in artifacts
    assert "tool_call_id in seen_tool_call_ids" in artifacts
