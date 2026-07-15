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
    payload = json.loads((tmp_path / ref["path"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "source-context-snapshot/v1"
    assert payload["brief_sha256"] == "b" * 64
    assert len(payload["segments"]) >= 2
    for segment in payload["segments"]:
        assert segment["sha256"] == hashlib.sha256(segment["text"].encode("utf-8")).hexdigest()

    same = build_source_context_snapshot(
        tmp_path,
        "et-snapshot",
        brief,
        registry,
        brief_sha256="b" * 64,
        brief_revision=2,
    )
    assert same == ref
