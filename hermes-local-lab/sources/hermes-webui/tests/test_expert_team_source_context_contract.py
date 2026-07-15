import hashlib
import json

import pytest


def test_local_source_is_hashed_from_original_bytes_and_locator_is_sanitized(tmp_path):
    from api.expert_teams.source_registry import resolve_source_registry

    source = tmp_path / "materials" / "monthly.txt"
    source.parent.mkdir()
    raw = "指标完成率：98.7%\r\n待协调事项：2项".encode("utf-8")
    source.write_bytes(raw)

    refs, registry = resolve_source_registry(
        tmp_path,
        "et-source",
        [{"source_id": "SRC-001", "kind": "local_file", "label": "月度数据", "locator": "materials/monthly.txt"}],
    )

    assert registry["SRC-001"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert refs[0]["sha256"] == registry["SRC-001"]["sha256"]
    assert refs[0]["locator"] == "materials/monthly.txt"
    assert not refs[0]["locator"].startswith("/")


def test_source_resolution_rejects_workspace_escape_and_symlink(tmp_path):
    from api.expert_teams.source_registry import SourceRegistryError, resolve_source_registry

    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "escape.txt"
    link.symlink_to(outside)

    for locator in ("../outside-secret.txt", "escape.txt", str(outside)):
        with pytest.raises(SourceRegistryError) as error:
            resolve_source_registry(
                tmp_path,
                "et-source",
                [{"source_id": "SRC-001", "kind": "local_file", "label": "x", "locator": locator}],
            )
        assert error.value.code == "source_unresolved"


def test_provided_text_is_materialized_before_becoming_a_ready_source(tmp_path):
    from api.expert_teams.source_registry import resolve_source_registry

    refs, registry = resolve_source_registry(
        tmp_path,
        "et-provided",
        [{"source_id": "SRC-TEXT", "kind": "provided_text", "label": "用户提供口径", "text": "已完成 3 项重点任务。"}],
    )

    target = tmp_path / refs[0]["locator"]
    assert target.read_text(encoding="utf-8") == "已完成 3 项重点任务。"
    assert registry["SRC-TEXT"]["status"] == "ready"
    assert "text" not in refs[0]


def test_source_context_snapshot_is_immutable_and_segment_hashes_recompute(tmp_path):
    from api.expert_teams.source_context import build_source_context_snapshot
    from api.expert_teams.source_registry import resolve_source_registry

    source = tmp_path / "material.txt"
    source.write_text("第一部分\n" + ("可核对数据。" * 900), encoding="utf-8")
    refs, registry = resolve_source_registry(
        tmp_path,
        "et-snapshot",
        [{"source_id": "SRC-001", "kind": "local_file", "label": "材料", "locator": "material.txt"}],
    )
    brief = {"source_policy": {"source_refs": refs}}

    ref = build_source_context_snapshot(
        tmp_path,
        "et-snapshot",
        brief,
        registry,
        brief_sha256="b" * 64,
        brief_revision=2,
    )
    payload = json.loads((tmp_path / ref["relative_path"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "expert-source-context/v1"
    assert payload["brief_sha256"] == "b" * 64
    assert ref["snapshot_id"] == "source-context-0002"
    assert len(payload["sources"][0]["segments"]) >= 2
    for segment in payload["sources"][0]["segments"]:
        assert segment["text_sha256"] == hashlib.sha256(segment["text"].encode("utf-8")).hexdigest()

    same = build_source_context_snapshot(
        tmp_path,
        "et-snapshot",
        brief,
        registry,
        brief_sha256="b" * 64,
        brief_revision=2,
    )
    assert same == ref


def _confirmed_run(tmp_path):
    from api.expert_teams.contracts import brief_digest
    from api.expert_teams.source_context import build_source_context_snapshot
    from api.expert_teams.source_registry import resolve_source_registry

    source = tmp_path / "material.txt"
    source.write_text("已完成重点任务 3 项。\n待协调事项 2 项。", encoding="utf-8")
    refs, registry = resolve_source_registry(
        tmp_path,
        "et-verify",
        [{"source_id": "SRC-001", "kind": "local_file", "label": "月度材料", "locator": "material.txt"}],
    )
    brief = {
        "schema_version": "document-brief/v1",
        "status": "confirmed",
        "revision": 1,
        "confirmed_revision": 1,
        "confirmed_at": "2026-07-15T10:00:00+08:00",
        "confirmed_sha256": "",
        "source_policy": {"source_refs": refs},
    }
    brief["confirmed_sha256"] = brief_digest(brief)
    ref = build_source_context_snapshot(
        tmp_path,
        "et-verify",
        brief,
        registry,
        brief_sha256=brief["confirmed_sha256"],
        brief_revision=1,
    )
    return {"run_id": "et-verify", "document_brief": brief, "source_context_snapshot_ref": ref}


def test_execution_reverifies_snapshot_brief_and_extractor_identity(tmp_path):
    from api.expert_teams.source_context import DEFAULT_EXTRACTOR_IDENTITY, verify_source_context_snapshot

    run = _confirmed_run(tmp_path)
    payload = verify_source_context_snapshot(tmp_path, run, extractor_identity=DEFAULT_EXTRACTOR_IDENTITY)
    assert payload["sources"][0]["content_text"].startswith("已完成重点任务")

    drifted = {**DEFAULT_EXTRACTOR_IDENTITY, "extractor_version": "2"}
    with pytest.raises(ValueError, match="new run"):
        verify_source_context_snapshot(tmp_path, run, extractor_identity=drifted)

    run["document_brief"]["source_policy"]["source_refs"][0]["label"] = "确认后被修改"
    with pytest.raises(ValueError, match="new run"):
        verify_source_context_snapshot(tmp_path, run, extractor_identity=DEFAULT_EXTRACTOR_IDENTITY)


def test_execution_rejects_missing_modified_or_symlinked_snapshot(tmp_path):
    from api.expert_teams.source_context import verify_source_context_snapshot

    run = _confirmed_run(tmp_path)
    target = tmp_path / run["source_context_snapshot_ref"]["relative_path"]
    target.chmod(0o600)
    original = target.read_text(encoding="utf-8")
    target.write_text(original.replace("重点任务", "篡改任务", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="hash changed"):
        verify_source_context_snapshot(tmp_path, run)

    target.unlink()
    with pytest.raises(ValueError, match="missing or unsafe"):
        verify_source_context_snapshot(tmp_path, run)

    outside = tmp_path / "outside.json"
    outside.write_text(original, encoding="utf-8")
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="missing or unsafe"):
        verify_source_context_snapshot(tmp_path, run)


def test_materials_prompt_consumes_verified_real_segments_and_binds_snapshot(tmp_path):
    from api.expert_teams.prompts import build_stage_gateway_request
    from api.expert_teams.source_context import verify_source_context_snapshot

    run = _confirmed_run(tmp_path)
    run.update(
        {
            "team_id": "content-creator-team",
            "stage_outputs": [
                {
                    "task_id": "plan",
                    "status": "approved",
                    "artifact": {
                        "artifact_id": "art-plan",
                        "sha256": "1" * 64,
                        "artifact_type": "writing_plan",
                        "payload": {"goal": "整理材料"},
                    },
                }
            ],
        }
    )
    snapshot = verify_source_context_snapshot(tmp_path, run)
    request = build_stage_gateway_request(
        run,
        {"id": "materials", "executor": "model", "artifact_type": "material_ledger", "depends_on": ["plan"]},
        source_context=snapshot,
    )
    envelope = json.loads(request["messages"][1]["content"])
    assert envelope["source_context"]["snapshot_id"] == run["source_context_snapshot_ref"]["snapshot_id"]
    assert envelope["source_context"]["snapshot_sha256"] == run["source_context_snapshot_ref"]["sha256"]
    assert envelope["source_context"]["sources"][0]["segments"][0]["text"].startswith("已完成重点任务")
    assert "已完成重点任务" not in request["messages"][0]["content"]
    assert request["input_refs"][-1] == {
        "ref_type": "source_context",
        "snapshot_id": run["source_context_snapshot_ref"]["snapshot_id"],
        "sha256": run["source_context_snapshot_ref"]["sha256"],
    }

    snapshot["snapshot_sha256"] = "0" * 64
    with pytest.raises(ValueError) as error:
        build_stage_gateway_request(
            run,
            {"id": "materials", "executor": "model", "artifact_type": "material_ledger", "depends_on": ["plan"]},
            source_context=snapshot,
        )
    assert error.value.code == "source_context_binding_mismatch"
