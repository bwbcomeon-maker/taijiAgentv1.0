from __future__ import annotations

import base64
import hashlib
import json


def test_successful_image_tool_completion_becomes_one_private_opaque_candidate():
    from api.artifacts import image_artifact_candidate_from_tool_completion

    candidate = image_artifact_candidate_from_tool_completion(
        tool_name="image_generate",
        tool_call_id="image-call",
        structured_result={
            "success": True,
            "image_ref": "generated.png",
            "sha256": "a" * 64,
            "provider": "private-provider",
            "prompt": "private prompt",
            "signed_url": "https://private.example/image?signature=secret",
        },
    )

    assert candidate == {
        "tool_name": "image_generate",
        "tool_call_id": "image-call",
        "structured_result": {
            "success": True,
            "image_ref": "generated.png",
            "sha256": "a" * 64,
        },
    }


def test_failed_non_image_or_remote_tool_results_never_become_public_candidates():
    from api.artifacts import image_artifact_candidate_from_tool_completion

    assert image_artifact_candidate_from_tool_completion(
        tool_name="image_generate",
        tool_call_id="failed",
        structured_result={"success": False, "image_ref": "failed.png"},
    ) is None
    assert image_artifact_candidate_from_tool_completion(
        tool_name="vision_analyze",
        tool_call_id="vision",
        structured_result={
            "success": True,
            "image_ref": "vision.png",
            "sha256": "a" * 64,
        },
    ) is None
    assert image_artifact_candidate_from_tool_completion(
        tool_name="image_generate",
        tool_call_id="remote",
        structured_result={
            "success": True,
            "image": "https://cdn.example/image.png?signature=secret",
        },
    ) is None


def test_duplicate_tool_callbacks_promote_one_artifact(tmp_path):
    from api.artifacts import (
        ArtifactRegistry,
        image_artifact_candidate_from_tool_completion,
        ingest_image_artifact_candidates,
    )

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQ"
        "VR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    source = cache / "generated.png"
    source.write_bytes(png)
    digest = hashlib.sha256(png).hexdigest()
    candidate = image_artifact_candidate_from_tool_completion(
        tool_name="image_generate",
        tool_call_id="same-call",
        structured_result={
            "success": True,
            "image_ref": source.name,
            "sha256": digest,
        },
    )
    assert candidate is not None

    rows, errors = ingest_image_artifact_candidates(
        ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache]),
        session_id="session-a",
        turn_id="turn-a",
        candidates=[candidate, dict(candidate)],
        owner_run_id="run-a",
    )

    assert errors == []
    assert len(rows) == 1
    assert set(rows[0]) == {
        "artifact_id",
        "kind",
        "mime",
        "name",
        "size",
        "sha256",
        "status",
    }
    assert "MEDIA:" not in json.dumps(rows, ensure_ascii=False)
