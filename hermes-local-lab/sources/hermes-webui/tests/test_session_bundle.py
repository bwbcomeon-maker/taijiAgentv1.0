import base64
import hashlib
import io
import json
import stat
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _zip(entries: list[tuple[str, bytes]], *, infos=None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (name, payload) in enumerate(entries):
            info = infos[index] if infos else name
            archive.writestr(info, payload)
    return out.getvalue()


def _minimal_bundle_entries(*, artifact_bytes=PNG_1X1, artifact_path="artifacts/old.png"):
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
    messages = [{
        "role": "assistant",
        "content": "generated",
        "artifacts": [{
            "artifact_id": "old-artifact",
            "kind": "image",
            "mime": "image/png",
            "name": "old.png",
            "size": len(artifact_bytes),
            "sha256": artifact_sha,
            "status": "ready",
        }],
    }]
    session = {
        "export_schema_version": 2,
        "session_id": "source-session",
        "title": "Portable",
        "model": "test-model",
        "messages": messages,
    }
    session_bytes = json.dumps(
        session, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    messages_bytes = json.dumps(
        messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest = {
        "bundle_schema_version": 1,
        "original_session_id": "source-session",
        "session_sha256": hashlib.sha256(session_bytes).hexdigest(),
        "messages_sha256": hashlib.sha256(messages_bytes).hexdigest(),
        "artifacts": [{
            "artifact_id": "old-artifact",
            "path": artifact_path,
            "kind": "image",
            "mime": "image/png",
            "name": "old.png",
            "size": len(artifact_bytes),
            "sha256": artifact_sha,
            "status": "ready",
            "source_turn_id": "turn-old",
            "source_tool_call_id": "tool-old",
        }],
    }
    return [
        ("session.json", session_bytes),
        ("manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8")),
        (artifact_path, artifact_bytes),
    ]


def _entries_with_session(entries, session):
    session_bytes = json.dumps(
        session, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest = json.loads(entries[1][1])
    manifest["session_sha256"] = hashlib.sha256(session_bytes).hexdigest()
    manifest["messages_sha256"] = hashlib.sha256(
        json.dumps(
            session["messages"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return [
        ("session.json", session_bytes),
        ("manifest.json", json.dumps(manifest).encode()),
        entries[2],
    ]


def test_bundle_export_contains_only_public_session_and_verified_artifacts(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.session_bundle import build_session_bundle

    registry = ArtifactRegistry(tmp_path / "artifacts")
    artifact = registry.register_image_bytes(
        "session-a", "turn-a", "tool-a", PNG_1X1, mime="image/png", name="生成 图片.png"
    )
    session = SimpleNamespace(
        session_id="session-a",
        title="Portable",
        model="test-model",
        messages=[
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "generated",
                "artifacts": [{
                    **artifact,
                    "storage_path": "/Users/private/runtime/secret.png",
                    "owner_session_id": "session-a",
                }],
            },
        ],
        context_messages=[{"role": "user", "content": "private context"}],
        pending_user_message="private pending",
        privacy_context={"risk_type": "runtime_access", "remaining_turns": 1},
        tool_calls=[{"name": "image_generate", "args": {"token": "secret-canary"}}],
    )

    raw = build_session_bundle(session, registry)
    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        names = sorted(archive.namelist())
        assert names == [
            f"artifacts/{artifact['artifact_id']}.png",
            "manifest.json",
            "session.json",
        ]
        session_payload = json.loads(archive.read("session.json"))
        manifest = json.loads(archive.read("manifest.json"))
        artifact_bytes = archive.read(names[0])

    serialized = json.dumps(session_payload, ensure_ascii=False)
    assert session_payload["export_schema_version"] == 2
    assert session_payload["messages"][1]["artifacts"] == [artifact]
    assert "context_messages" not in serialized
    assert "pending_user_message" not in serialized
    assert "privacy_context" not in serialized
    assert "storage_path" not in serialized
    assert "owner_session_id" not in serialized
    assert "args" not in serialized and "secret-canary" not in serialized
    assert manifest["bundle_schema_version"] == 1
    assert manifest["original_session_id"] == "session-a"
    assert len(manifest["messages_sha256"]) == 64
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256(PNG_1X1).hexdigest()
    assert artifact_bytes == PNG_1X1


@pytest.mark.parametrize(
    "bad_name",
    ["../escape.png", "/absolute.png", "artifacts/../escape.png", "artifacts\\evil.png"],
)
def test_bundle_import_rejects_path_traversal_and_non_portable_names(bad_name):
    from api.session_bundle import BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries(artifact_path=bad_name)
    with pytest.raises(BundleValidationError):
        inspect_session_bundle(_zip(entries))


def test_bundle_import_rejects_duplicate_names_and_symlinks():
    from api.session_bundle import BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries()
    # zipfile warns while constructing this deliberately malformed fixture;
    # the product behavior under test is still the importer's rejection.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        duplicate = _zip([*entries, ("session.json", entries[0][1])])
    with pytest.raises(BundleValidationError, match="duplicate"):
        inspect_session_bundle(duplicate)

    link_info = zipfile.ZipInfo("artifacts/old.png")
    link_info.create_system = 3
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink = _zip(entries, infos=["session.json", "manifest.json", link_info])
    with pytest.raises(BundleValidationError, match="symbolic link"):
        inspect_session_bundle(symlink)


def test_bundle_import_enforces_count_size_ratio_hash_and_image_magic():
    from api.session_bundle import BundleLimits, BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries()
    with pytest.raises(BundleValidationError, match="file count"):
        inspect_session_bundle(_zip(entries), limits=BundleLimits(max_files=2))
    with pytest.raises(BundleValidationError, match="single file"):
        inspect_session_bundle(
            _zip(entries), limits=BundleLimits(max_single_file=32)
        )
    with pytest.raises(BundleValidationError, match="compression ratio"):
        inspect_session_bundle(
            _zip([*entries[:2], (entries[2][0], b"A" * 20_000)]),
            limits=BundleLimits(max_compression_ratio=2),
        )

    tampered = list(entries)
    tampered_bytes = bytes([PNG_1X1[0] ^ 0x01]) + PNG_1X1[1:]
    tampered[2] = (tampered[2][0], tampered_bytes)
    with pytest.raises(BundleValidationError, match="hash"):
        inspect_session_bundle(_zip(tampered))

    wrong_magic = _minimal_bundle_entries(artifact_bytes=b"not-a-png")
    with pytest.raises(BundleValidationError, match="image"):
        inspect_session_bundle(_zip(wrong_magic))


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda session, manifest: manifest.__setitem__("bundle_schema_version", 99), "schema"),
        (lambda session, manifest: manifest.__setitem__("original_session_id", "other"), "identity"),
        (lambda session, manifest: manifest.__setitem__("session_sha256", "0" * 64), "session hash"),
        (lambda session, manifest: manifest.__setitem__("messages_sha256", "0" * 64), "messages hash"),
    ],
)
def test_bundle_import_rejects_schema_identity_and_metadata_checksum_tampering(
    mutation, error
):
    from api.session_bundle import BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries()
    session = json.loads(entries[0][1])
    manifest = json.loads(entries[1][1])
    mutation(session, manifest)
    with pytest.raises(BundleValidationError, match=error):
        inspect_session_bundle(_zip([
            entries[0],
            ("manifest.json", json.dumps(manifest).encode()),
            entries[2],
        ]))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.pop("kind"),
        lambda row: row.__setitem__("kind", "file"),
        lambda row: row.__setitem__("status", "pending"),
        lambda row: row.__setitem__("mime", "image/jpeg"),
        lambda row: row.__setitem__("name", "/Users/private/image.png"),
        lambda row: row.__setitem__("path", "artifacts/old.jpg"),
    ],
)
def test_bundle_import_requires_complete_consistent_public_artifact_descriptor(mutation):
    from api.session_bundle import BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries()
    session = json.loads(entries[0][1])
    manifest = json.loads(entries[1][1])
    mutation(manifest["artifacts"][0])
    with pytest.raises(BundleValidationError, match="artifact"):
        inspect_session_bundle(_zip([
            entries[0],
            ("manifest.json", json.dumps(manifest).encode()),
            (manifest["artifacts"][0].get("path", entries[2][0]), entries[2][1]),
        ]))


def test_bundle_import_rejects_unknown_internal_message_and_manifest_fields():
    from api.session_bundle import BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries()
    session = json.loads(entries[0][1])
    session["messages"][0]["context_messages"] = [{"role": "user", "content": "internal"}]
    session_bytes = json.dumps(
        session, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest = json.loads(entries[1][1])
    manifest["session_sha256"] = hashlib.sha256(session_bytes).hexdigest()
    manifest["messages_sha256"] = hashlib.sha256(
        json.dumps(
            session["messages"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(BundleValidationError, match="canonical public"):
        inspect_session_bundle(_zip([
            ("session.json", session_bytes),
            ("manifest.json", json.dumps(manifest).encode()),
            entries[2],
        ]))

    entries = _minimal_bundle_entries()
    manifest = json.loads(entries[1][1])
    manifest["artifacts"][0]["storage_path"] = "/Users/private/secret.png"
    with pytest.raises(BundleValidationError, match="non-public"):
        inspect_session_bundle(_zip([
            entries[0],
            ("manifest.json", json.dumps(manifest).encode()),
            entries[2],
        ]))


def test_bundle_import_rebinds_session_and_artifact_ids_and_rolls_back_atomically(
    tmp_path, monkeypatch
):
    import api.models as models
    from api.artifacts import ArtifactRegistry
    from api.session_bundle import import_session_bundle

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    registry = ArtifactRegistry(tmp_path / "artifacts")
    raw = _zip(_minimal_bundle_entries())

    persisted = []
    imported = import_session_bundle(
        raw,
        registry,
        workspace=tmp_path,
        profile="default",
        persist_session=lambda session: persisted.append(session.session_id),
    )
    assert imported.session_id != "source-session"
    new_artifact = imported.messages[0]["artifacts"][0]
    assert new_artifact["artifact_id"] != "old-artifact"
    assert registry.authorize(imported.session_id, new_artifact["artifact_id"]).read_bytes() == PNG_1X1
    assert persisted == [imported.session_id]

    def _write_then_fail(session):
        session.save()
        raise RuntimeError("state db failed")

    before = {path.name for path in (tmp_path / "artifacts").iterdir() if path.is_dir()}
    with pytest.raises(RuntimeError, match="state db failed"):
        import_session_bundle(
            raw,
            registry,
            workspace=tmp_path,
            profile="default",
            persist_session=_write_then_fail,
        )
    after = {path.name for path in (tmp_path / "artifacts").iterdir() if path.is_dir()}
    assert after == before
    assert not [path for path in session_dir.glob("*.json") if path.name != "_index.json"]


@pytest.mark.parametrize(
    "crafted_message",
    [
        {
            "role": "assistant",
            "content": [{"type": "input_text", "text": "ok", "token": "secret"}],
        },
        {
            "role": "assistant",
            "content": [{
                "type": "tool_use", "name": "image_generate",
                "input": {"path": "/Users/private/image.png", "token": "secret"},
            }],
        },
        {
            "role": "assistant", "content": "ok",
            "reasoning": {"text": "hidden", "token": "secret"},
        },
        {
            "role": "assistant", "content": "ok",
            "artifact_errors": [{"path": "/Users/private/image.png"}],
        },
        {"role": "assistant", "content": {"text": "not-a-public-content-shape"}},
    ],
)
def test_bundle_import_requires_each_message_to_equal_canonical_public_projection(
    crafted_message,
):
    from api.session_bundle import BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries()
    session = json.loads(entries[0][1])
    crafted_message["artifacts"] = session["messages"][0]["artifacts"]
    session["messages"] = [crafted_message]
    with pytest.raises(BundleValidationError, match="canonical public"):
        inspect_session_bundle(_zip(_entries_with_session(entries, session)))


@pytest.mark.parametrize(
    ("container", "payload"),
    [
        ("content", {"type": "text", "text": "ok", "unknown": {"token": "x"}}),
        ("content", {"type": "tool_result", "summary": "ok", "input": {"path": "/tmp/x"}}),
        ("tool_calls", {"name": "tool", "summary": "ok", "arguments": {"token": "x"}}),
        ("artifact_errors", {"message": "failed", "details": {"path": "/tmp/x"}}),
    ],
)
def test_bundle_import_fuzzed_nested_unknown_fields_fail_closed(container, payload):
    from api.session_bundle import BundleValidationError, inspect_session_bundle

    entries = _minimal_bundle_entries()
    session = json.loads(entries[0][1])
    message = session["messages"][0]
    if container == "content":
        message[container] = [payload]
    else:
        message[container] = [payload]
    with pytest.raises(BundleValidationError, match="canonical public"):
        inspect_session_bundle(_zip(_entries_with_session(entries, session)))


def test_bundle_export_canonicalizes_messages_and_enforces_preflight_limits(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.brand_privacy import public_message_projection
    from api.session_bundle import (
        BundleLimits,
        BundleValidationError,
        build_session_bundle,
        inspect_session_bundle,
    )

    registry = ArtifactRegistry(tmp_path / "artifacts")
    artifact = registry.register_image_bytes(
        "session-a", "turn-a", "tool-a", PNG_1X1, mime="image/png", name="image.png"
    )
    raw_message = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "safe"},
            {"type": "tool_use", "name": "image_generate", "input": {"token": "secret"}},
        ],
        "artifacts": [artifact],
        "artifact_errors": [{"path": "/Users/private/failure"}],
    }
    session = SimpleNamespace(
        session_id="session-a", title="Portable", model="test-model",
        messages=[raw_message], tool_calls=[],
    )

    inspected = inspect_session_bundle(build_session_bundle(session, registry))
    expected = public_message_projection(raw_message, session_id="session-a")
    assert inspected.session["messages"][0] == expected
    serialized = json.dumps(inspected.session, ensure_ascii=False)
    assert "secret" not in serialized and "/Users/private" not in serialized

    with pytest.raises(BundleValidationError, match="file count"):
        build_session_bundle(
            session, registry, limits=BundleLimits(max_files=2)
        )
    with pytest.raises(BundleValidationError, match="total size"):
        build_session_bundle(
            session, registry,
            limits=BundleLimits(max_total_uncompressed=len(PNG_1X1) - 1),
        )


def test_bundle_export_rejects_metadata_limit_before_artifact_io(tmp_path, monkeypatch):
    from api.artifacts import ArtifactRegistry
    from api.session_bundle import BundleLimits, BundleValidationError, build_session_bundle

    registry = ArtifactRegistry(tmp_path / "artifacts")
    artifact = registry.register_image_bytes(
        "session-a", "turn-a", "tool-a", PNG_1X1,
        mime="image/png", name="image.png",
    )
    session = SimpleNamespace(
        session_id="session-a", title="Portable", model="test-model",
        messages=[{
            "role": "assistant", "content": "x" * 2_000,
            "artifacts": [artifact],
        }],
        tool_calls=[],
    )
    artifact_io = []
    real_authorize = registry.authorize

    def _record_authorize(*args, **kwargs):
        artifact_io.append(args)
        return real_authorize(*args, **kwargs)

    monkeypatch.setattr(registry, "authorize", _record_authorize)
    with pytest.raises(BundleValidationError, match="single file size"):
        build_session_bundle(
            session, registry,
            limits=BundleLimits(max_single_file=512),
        )
    assert artifact_io == []


@pytest.mark.parametrize("cleanup_stage", ["sidecar", "index", "manifest", "artifact"])
def test_bundle_failed_import_cleanup_is_verified_and_quarantined(
    tmp_path, monkeypatch, cleanup_stage
):
    import api.models as models
    import api.session_bundle as bundle
    from api.artifacts import ArtifactRegistry

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    registry = ArtifactRegistry(tmp_path / "artifacts")
    raw = _zip(_minimal_bundle_entries())
    real_unlink = Path.unlink
    failed_once = False

    def _fail_selected_unlink(path, *args, **kwargs):
        nonlocal failed_once
        selected = (
            cleanup_stage == "sidecar" and path.parent == session_dir
            and path.name.endswith(".json") and path.name != "_index.json"
        ) or (
            cleanup_stage == "manifest" and path.name == "manifest.json"
        ) or (
            cleanup_stage == "artifact" and path.suffix == ".png"
            and registry.root in path.parents
        )
        if selected and not failed_once:
            failed_once = True
            raise OSError(f"injected {cleanup_stage} cleanup failure")
        return real_unlink(path, *args, **kwargs)

    if cleanup_stage in {"sidecar", "manifest", "artifact"}:
        monkeypatch.setattr(Path, "unlink", _fail_selected_unlink)
    if cleanup_stage == "index":
        monkeypatch.setattr(
            models,
            "prune_session_from_index",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("injected index cleanup failure")
            ),
        )

    def _persist_then_fail(session):
        session.save()
        raise RuntimeError("injected state.db failure")

    error_type = getattr(bundle, "BundleImportRollbackError", RuntimeError)
    with pytest.raises(error_type) as raised:
        bundle.import_session_bundle(
            raw,
            registry,
            workspace=tmp_path,
            profile="default",
            persist_session=_persist_then_fail,
        )
    assert getattr(raised.value, "rollback_incomplete", False) is True
    assert getattr(raised.value, "code", "") == "rollback_incomplete"
    assert not [
        path for path in session_dir.glob("*.json")
        if path.name != "_index.json"
    ]
    index_payload = json.loads((session_dir / "_index.json").read_text("utf-8")) \
        if (session_dir / "_index.json").is_file() else []
    assert not any(row.get("session_id") == getattr(raised.value, "session_id", "") for row in index_payload)
    formal_artifact_dirs = [
        path for path in registry.root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert formal_artifact_dirs == []
    assert (registry.root / ".quarantine").exists() or (session_dir / "_quarantine").exists()
    assert models.Session.load(raised.value.session_id) is None
