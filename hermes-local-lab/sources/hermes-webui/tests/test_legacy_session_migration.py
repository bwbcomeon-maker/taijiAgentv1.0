import base64
import hashlib
import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _session_db(path: Path):
    from hermes_state import SessionDB

    db = SessionDB(path)
    db.ensure_session("legacy-session", "webui", "test-model")
    return db


def _write_legacy_fixture(tmp_path: Path, *, journal_content="hello", user_turn_id=True):
    from api.turn_journal import append_turn_journal_event

    session_dir = tmp_path / "sessions"
    cache_dir = tmp_path / "cache" / "images"
    session_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    image_path = cache_dir / "legacy image.png"
    image_path.write_bytes(PNG_1X1)
    user = {
        "role": "user",
        "content": "hello",
        "platform_message_id": "webui-turn:turn-1" if user_turn_id else None,
    }
    if user_turn_id:
        user["turn_id"] = "turn-1"
    assistant = {
        "role": "assistant",
        "content": f"generated\nMEDIA:{image_path}",
    }
    payload = {
        "session_id": "legacy-session",
        "title": "Legacy",
        "workspace": str(tmp_path),
        "model": "test-model",
        "messages": [user, assistant],
        "tool_calls": [],
        "brand_privacy_tainted": True,
    }
    session_path = session_dir / "legacy-session.json"
    session_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    append_turn_journal_event(
        "legacy-session",
        {
            "event": "submitted",
            "turn_id": "turn-1",
            "role": "user",
            "content": journal_content,
        },
        session_dir=session_dir,
    )
    return session_dir, cache_dir, session_path, image_path


def _logical_messages(db):
    return [
        {
            "role": row["role"],
            "content": row["content"],
            "platform_message_id": row.get("platform_message_id"),
        }
        for row in db.get_messages("legacy-session")
    ]


def test_audit_is_read_only_and_reports_explicit_proof_reasons(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import audit_legacy_sessions

    session_dir, cache_dir, session_path, _image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    before_session = session_path.read_bytes()
    before_db = state_db_path.read_bytes()
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])

    report = audit_legacy_sessions(session_dir, state_db_path, registry)

    assert report["needs_repair"] is True
    assert report["scanned"] == 1
    assert {item["code"] for item in report["items"]} >= {
        "legacy_privacy_taint",
        "state_db_user_backfill_exact",
        "legacy_cached_image",
    }
    reasons = {item["code"]: item["reason"] for item in report["items"]}
    assert reasons["legacy_privacy_taint"] == "legacy_unbounded_taint"
    assert reasons["state_db_user_backfill_exact"] == "turn_id_order_content_exact"
    assert reasons["legacy_cached_image"] == "existing_cache_image_exact"
    assert session_path.read_bytes() == before_session
    assert state_db_path.read_bytes() == before_db
    assert not (tmp_path / "artifacts" / "legacy-session").exists()
    db.close()


def test_apply_backs_up_migrates_exact_data_and_is_idempotent(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import migrate_legacy_sessions

    session_dir, cache_dir, session_path, _image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])

    report = migrate_legacy_sessions(
        session_dir,
        state_db_path,
        registry,
        dry_run=False,
        backup_root=tmp_path / "backups",
    )

    assert report["failed"] == 0
    assert report["modified"] >= 3
    backup_path = Path(report["backup_path"])
    assert backup_path.is_dir()
    assert (backup_path / "sessions" / session_path.name).is_file()
    assert (backup_path / "state.db").is_file()
    migrated = json.loads(session_path.read_text("utf-8"))
    assert "brand_privacy_tainted" not in migrated
    assert migrated["messages"][0]["content"] == "hello"
    assert migrated["messages"][0]["turn_id"] == "turn-1"
    assert "MEDIA:" not in migrated["messages"][1]["content"]
    descriptor = migrated["messages"][1]["artifacts"][0]
    assert set(descriptor) == {
        "artifact_id", "kind", "mime", "name", "size", "sha256", "status"
    }
    assert registry.authorize("legacy-session", descriptor["artifact_id"]).read_bytes() == PNG_1X1
    assert [row["role"] for row in db.get_messages("legacy-session")] == ["user", "assistant"]

    second = migrate_legacy_sessions(
        session_dir,
        state_db_path,
        registry,
        dry_run=False,
        backup_root=tmp_path / "backups",
    )
    assert second["modified"] == 0
    assert second["backup_path"] is None
    db.close()


def test_ambiguous_user_backfill_is_skipped_with_reason_and_never_guessed(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import migrate_legacy_sessions

    session_dir, cache_dir, _session_path, image_path = _write_legacy_fixture(
        tmp_path, journal_content="different"
    )
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message(
        "legacy-session", "assistant", f"generated\nMEDIA:{image_path}"
    )
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])

    report = migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )

    skipped = [item for item in report["items"] if item["code"] == "state_db_user_backfill_skipped"]
    assert skipped and skipped[0]["reason"] == "content_mismatch"
    assert _logical_messages(db) == [{
        "role": "assistant",
        "content": "generated",
        "platform_message_id": None,
    }]
    db.close()


def test_rollback_state_is_not_observable_through_migration_read_guard(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry

    session_dir, cache_dir, session_path, _image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    before_session = session_path.read_bytes()
    before_db = _logical_messages(db)
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    half_state_reached = threading.Event()
    allow_rollback = threading.Event()
    reader_finished = threading.Event()
    observed = {}

    def _pause_then_fail(*_args, **_kwargs):
        half_state_reached.set()
        assert allow_rollback.wait(timeout=5)
        raise RuntimeError("injected failure before state.db replace")

    monkeypatch.setattr(migration, "_replace_state_db_messages", _pause_then_fail)

    migration_thread = threading.Thread(
        target=migration.migrate_legacy_sessions,
        args=(session_dir, state_db_path, registry),
        kwargs={"dry_run": False, "backup_root": tmp_path / "backups"},
        daemon=True,
    )

    @migration.migration_consistent_http_routes("GET")
    def _read_public_state(_handler, _parsed):
        observed["session"] = session_path.read_bytes()
        observed["db"] = _logical_messages(db)
        reader_finished.set()

    migration_thread.start()
    assert half_state_reached.wait(timeout=5)
    reader_thread = threading.Thread(
        target=_read_public_state,
        args=(None, SimpleNamespace(path="/api/session/export")),
        daemon=True,
    )
    reader_thread.start()
    time.sleep(0.05)
    assert reader_finished.is_set() is False
    allow_rollback.set()
    migration_thread.join(timeout=5)
    reader_thread.join(timeout=5)

    assert migration_thread.is_alive() is False
    assert reader_thread.is_alive() is False
    assert observed == {"session": before_session, "db": before_db}
    assert not (tmp_path / "artifacts" / "legacy-session").exists()
    db.close()


def test_migration_route_barrier_covers_state_readers_writers_and_apply_upgrade():
    import api.legacy_session_migration as migration

    for path in (
        "/api/session", "/api/sessions", "/api/sessions/search",
        "/api/session/export", "/api/session/export-bundle",
        "/api/session/migration/audit", "/api/media",
    ):
        assert migration._route_touches_migration_state("GET", path), path
    for path in (
        "/api/chat/stream", "/api/sessions/gateway/stream", "/api/sessions/events",
    ):
        assert migration._route_touches_migration_state("GET", path) is False, path
    for path in (
        "/api/session/clear", "/api/session/import-bundle", "/api/sessions/cleanup",
        "/api/chat", "/api/background", "/api/goal", "/api/btw",
        "/api/expert-teams/start", "/api/writeflow/compose",
    ):
        assert migration._route_touches_migration_state("POST", path), path
    assert migration._route_touches_migration_state(
        "POST", "/api/session/migration/apply"
    ) is False
    assert migration._route_touches_migration_state("GET", "/api/health/agent") is False
    assert migration._route_touches_migration_state("POST", "/api/settings") is False


def test_long_lived_sse_does_not_starve_migration_exclusive_window():
    import api.legacy_session_migration as migration

    sse_started = threading.Event()
    release_sse = threading.Event()
    writer_acquired = threading.Event()

    @migration.migration_consistent_http_routes("GET")
    def _fake_sse(_handler, _parsed):
        sse_started.set()
        assert release_sse.wait(timeout=2)

    sse = threading.Thread(
        target=_fake_sse,
        args=(None, SimpleNamespace(path="/api/sessions/events")),
        daemon=True,
    )
    sse.start()
    assert sse_started.wait(timeout=2)

    def _writer():
        with migration._legacy_migration_exclusive_guard():
            writer_acquired.set()

    writer = threading.Thread(target=_writer, daemon=True)
    writer.start()
    assert writer_acquired.wait(timeout=2)
    release_sse.set()
    sse.join(timeout=2)
    writer.join(timeout=2)
    assert sse.is_alive() is False and writer.is_alive() is False


def test_gateway_watcher_snapshot_waits_for_migration_and_is_not_half_state(
    tmp_path, monkeypatch
):
    import api.gateway_watcher as watcher
    import api.legacy_session_migration as migration

    state_db = tmp_path / "state.db"
    state_db.write_bytes(b"fixture")
    observed = []
    snapshot_finished = threading.Event()
    release_writer = threading.Event()
    writer_started = threading.Event()

    monkeypatch.setattr(watcher, "_get_state_db_path", lambda: state_db)

    def _rows(*_args, **_kwargs):
        observed.append("post")
        return []

    monkeypatch.setattr(watcher, "read_importable_agent_session_rows", _rows)

    def _migration_window():
        with migration._legacy_migration_exclusive_guard():
            writer_started.set()
            assert release_writer.wait(timeout=2)

    writer = threading.Thread(target=_migration_window, daemon=True)
    writer.start()
    assert writer_started.wait(timeout=2)

    def _snapshot():
        watcher._get_agent_sessions_from_db()
        snapshot_finished.set()

    reader = threading.Thread(target=_snapshot, daemon=True)
    reader.start()
    time.sleep(0.05)
    assert snapshot_finished.is_set() is False and observed == []
    release_writer.set()
    writer.join(timeout=2)
    reader.join(timeout=2)
    assert snapshot_finished.is_set() is True and observed == ["post"]


def test_migration_barrier_nested_reader_with_queued_writer_does_not_deadlock():
    import api.legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    writer_finished = threading.Event()

    def _writer():
        with barrier.write():
            pass
        writer_finished.set()

    with barrier.read():
        writer = threading.Thread(target=_writer, daemon=True)
        writer.start()
        deadline = time.monotonic() + 2
        while barrier._waiting_writers != 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert barrier._waiting_writers == 1
        with barrier.read():
            assert writer_finished.is_set() is False
    writer.join(timeout=2)
    assert writer_finished.is_set() is True


def test_migration_barrier_supports_concurrent_readers_and_writer_waits_for_them():
    import api.legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    second_reader_acquired = threading.Event()
    release_second_reader = threading.Event()
    writer_acquired = threading.Event()

    def _second_reader():
        with barrier.read():
            second_reader_acquired.set()
            assert release_second_reader.wait(timeout=2)

    def _writer():
        with barrier.write():
            writer_acquired.set()

    with barrier.read():
        reader = threading.Thread(target=_second_reader, daemon=True)
        reader.start()
        assert second_reader_acquired.wait(timeout=2)
        writer = threading.Thread(target=_writer, daemon=True)
        writer.start()
        time.sleep(0.05)
        assert writer_acquired.is_set() is False
        release_second_reader.set()
        reader.join(timeout=2)
        assert reader.is_alive() is False
        assert writer_acquired.is_set() is False
    writer.join(timeout=2)
    assert writer_acquired.is_set() is True


def test_migration_barrier_writer_reentrancy_and_exception_release():
    import api.legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    with pytest.raises(RuntimeError, match="injected writer failure"):
        with barrier.write():
            with barrier.write():
                with barrier.read():
                    raise RuntimeError("injected writer failure")

    reader_finished = threading.Event()

    def _reader():
        with barrier.read():
            reader_finished.set()

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert reader_finished.is_set() is True


def test_leak_scan_reports_locations_not_secret_values_and_ignores_user_text(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import audit_legacy_sessions

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    canary = "OPENAI_API_KEY=sk-test-canary-123456789012345678901234"
    payload = {
        "session_id": "legacy-session",
        "messages": [
            {"role": "user", "content": canary},
            {"role": "assistant", "content": f"accident {canary}", "reasoning": canary},
            {"role": "tool", "content": canary},
        ],
        "tool_calls": [],
    }
    (session_dir / "legacy-session.json").write_text(json.dumps(payload), "utf-8")
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.close()
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[])

    report = audit_legacy_sessions(session_dir, state_db_path, registry)
    serialized = json.dumps(report, ensure_ascii=False)

    leaks = [item for item in report["items"] if item["code"] == "credential_leak_detected"]
    assert {item["location"] for item in leaks} == {
        "messages[1].content", "messages[1].reasoning", "messages[2].content"
    }
    assert all(item["reason"] == "manual_review_required" for item in leaks)
    assert canary not in serialized and "sk-test-canary" not in serialized


def test_apply_rolls_back_sidecar_db_and_new_artifact_on_batch_failure(tmp_path, monkeypatch):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry

    session_dir, cache_dir, session_path, _image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    before_session = session_path.read_bytes()
    before_db_messages = _logical_messages(db)
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])

    real_replace = migration._replace_state_db_messages

    def _fail_after_replace(*args, **kwargs):
        real_replace(*args, **kwargs)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(migration, "_replace_state_db_messages", _fail_after_replace)
    report = migration.migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )

    assert report["failed"] == 1
    assert session_path.read_bytes() == before_session
    assert _logical_messages(db) == before_db_messages
    assert not (tmp_path / "artifacts" / "legacy-session").exists()
    db.close()


def test_any_applied_sidecar_change_replaces_state_db_and_keeps_messages_equal(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry

    session_dir, cache_dir, session_path, image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message(
        "legacy-session", "user", "hello",
        platform_message_id="webui-turn:turn-1",
    )
    db.append_message(
        "legacy-session", "assistant", f"generated\nMEDIA:{image_path}"
    )
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    real_replace = migration._replace_state_db_messages
    replacements = []

    def _record_replace(path, session_id, messages):
        replacements.append(session_id)
        return real_replace(path, session_id, messages)

    monkeypatch.setattr(migration, "_replace_state_db_messages", _record_replace)
    report = migration.migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )

    assert report["failed"] == 0
    assert replacements == ["legacy-session"]
    sidecar = json.loads(session_path.read_text("utf-8"))["messages"]
    assert _logical_messages(db) == [
        {
            "role": item["role"],
            "content": item["content"],
            "platform_message_id": item.get("platform_message_id"),
        }
        for item in sidecar
    ]
    db.close()


@pytest.mark.parametrize("fail_on", [1, 2, 3])
def test_artifact_promotion_tracks_each_created_id_and_rolls_back_every_failure_index(
    tmp_path, monkeypatch, fail_on
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry

    session_dir, cache_dir, session_path, image_path = _write_legacy_fixture(tmp_path)
    second = cache_dir / "second.png"
    third = cache_dir / "third.png"
    second.write_bytes(PNG_1X1)
    third.write_bytes(PNG_1X1)
    payload = json.loads(session_path.read_text("utf-8"))
    payload["messages"][1]["content"] = (
        f"generated\nMEDIA:{image_path}\nMEDIA:{second}\nMEDIA:{third}"
    )
    session_path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    real_register = registry.register_image_file
    calls = 0

    def _fail_at_index(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise RuntimeError(f"injected artifact failure {fail_on}")
        return real_register(*args, **kwargs)

    monkeypatch.setattr(registry, "register_image_file", _fail_at_index)
    report = migration.migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )

    assert report["failed"] == 1
    failure = next(item for item in report["items"] if item["code"] == "migration_failed")
    assert failure["stage"] == "artifact_promotion"
    assert failure["rollback_complete"] is True
    artifact_session = tmp_path / "artifacts" / "legacy-session"
    assert not artifact_session.exists()
    assert json.loads(session_path.read_text("utf-8")) == payload
    db.close()


def test_media_promotion_skips_fences_and_removes_only_exact_successful_lines(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import migrate_legacy_sessions

    session_dir, cache_dir, session_path, image_path = _write_legacy_fixture(tmp_path)
    missing = cache_dir / "missing.png"
    payload = json.loads(session_path.read_text("utf-8"))
    payload["messages"][1]["content"] = (
        "before\n```text\n"
        f"MEDIA:{image_path}\n"
        "```\n"
        f"MEDIA:{image_path}\n"
        f"MEDIA:{missing}\n"
        "after"
    )
    session_path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", payload["messages"][1]["content"])
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])

    report = migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )

    assert report["failed"] == 0
    migrated = json.loads(session_path.read_text("utf-8"))["messages"][1]
    assert migrated["content"].count(f"MEDIA:{image_path}") == 1
    assert f"MEDIA:{missing}" in migrated["content"]
    assert len(migrated["artifacts"]) == 1
    db.close()


def test_backup_covers_journals_artifacts_referenced_cache_and_supports_verified_restore(
    tmp_path,
):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import (
        migrate_legacy_sessions,
        restore_legacy_migration_backup,
    )
    from api.run_journal import append_run_event

    session_dir, cache_dir, session_path, image_path = _write_legacy_fixture(tmp_path)
    append_run_event(
        "legacy-session", "run-1", "done", {"content": "safe"},
        session_dir=session_dir,
    )
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    existing = registry.register_image_bytes(
        "legacy-session", "existing-turn", "existing-tool", PNG_1X1,
        mime="image/png", name="existing.png",
    )
    before_session = session_path.read_bytes()
    before_cache = image_path.read_bytes()
    before_artifact = registry.authorize(
        "legacy-session", existing["artifact_id"]
    ).read_bytes()

    report = migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )
    backup = Path(report["backup_path"])
    manifest = json.loads((backup / "backup-manifest.json").read_text("utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["sqlite_backup"]["captures_committed_wal_state"] is True
    relative_paths = {item["path"] for item in manifest["files"]}
    assert any(path.startswith("sessions/_turn_journal/") for path in relative_paths)
    assert any(path.startswith("sessions/_run_journal/") for path in relative_paths)
    assert any(path.startswith("artifacts/legacy-session/") for path in relative_paths)
    assert any(path.startswith("referenced-cache/") for path in relative_paths)
    for item in manifest["files"]:
        data = (backup / item["path"]).read_bytes()
        assert item["size"] == len(data)
        assert item["sha256"] == hashlib.sha256(data).hexdigest()

    session_path.write_text("{}", "utf-8")
    image_path.write_bytes(b"damaged cache")
    shutil.rmtree(tmp_path / "artifacts" / "legacy-session")
    db.replace_messages("legacy-session", [{"role": "assistant", "content": "damaged"}])
    restored = restore_legacy_migration_backup(
        backup, session_dir, state_db_path, registry
    )
    assert restored["verified"] is True
    assert session_path.read_bytes() == before_session
    assert image_path.read_bytes() == before_cache
    assert registry.authorize(
        "legacy-session", existing["artifact_id"]
    ).read_bytes() == before_artifact
    assert _logical_messages(db)[0]["content"] == "generated"
    db.close()


def test_recursive_leak_scan_covers_messages_tools_and_both_journals_but_not_user(
    tmp_path,
):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import audit_legacy_sessions
    from api.run_journal import append_run_event
    from api.turn_journal import append_turn_journal_event

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    canary = "OPENAI_API_KEY=sk-test-recursive-canary-123456789012345678901234"
    payload = {
        "session_id": "legacy-session",
        "messages": [
            {"role": "user", "content": canary, "reasoning_details": {"secret": canary}},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": canary}],
                "reasoning_details": [{"nested": {"value": canary}}],
                "tool_calls": [{"name": "tool", "arguments": {"token": canary}}],
            },
            {"role": "tool", "content": {"nested": [canary]}},
        ],
        "tool_calls": [{"name": "top", "result": {"nested": canary}}],
    }
    (session_dir / "legacy-session.json").write_text(json.dumps(payload), "utf-8")
    append_turn_journal_event(
        "legacy-session",
        {"event": "submitted", "role": "user", "content": canary},
        session_dir=session_dir,
    )
    append_turn_journal_event(
        "legacy-session",
        {"event": "completed", "role": "assistant", "payload": {"nested": canary}},
        session_dir=session_dir,
    )
    append_run_event(
        "legacy-session", "run-1", "token", {"nested": {"text": canary}},
        session_dir=session_dir,
    )
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.close()
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[])

    report = audit_legacy_sessions(session_dir, state_db_path, registry)
    locations = {
        item["location"] for item in report["items"]
        if item["code"] == "credential_leak_detected"
    }
    assert locations >= {
        "messages[1].content[0].text",
        "messages[1].reasoning_details[0].nested.value",
        "messages[1].tool_calls[0].arguments.token",
        "messages[2].content.nested[0]",
        "tool_calls[0].result.nested",
        "turn_journal[1].payload.nested",
        "run_journal[run-1][0].payload.nested.text",
    }
    assert not any(location.startswith("messages[0]") for location in locations)
    serialized = json.dumps(report, ensure_ascii=False)
    assert canary not in serialized and "recursive-canary" not in serialized


def test_audit_tolerates_malformed_privacy_context_and_flags_stale_adjacency(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import audit_legacy_sessions

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    malformed = {
        "session_id": "malformed",
        "messages": [],
        "privacy_context": {
            "risk_type": "runtime_access", "source_turn_id": "t1",
            "remaining_turns": {"not": "numeric"},
        },
    }
    stale = {
        "session_id": "stale",
        "messages": [
            {"role": "user", "content": "sensitive", "turn_id": "t1"},
            {"role": "assistant", "content": "blocked"},
            {"role": "user", "content": "normal business", "turn_id": "t2"},
        ],
        "privacy_context": {
            "risk_type": "runtime_access", "source_turn_id": "t1",
            "remaining_turns": 1,
        },
    }
    for payload in (malformed, stale):
        (session_dir / f"{payload['session_id']}.json").write_text(
            json.dumps(payload), "utf-8"
        )
    state_db_path = tmp_path / "state.db"
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[])

    report = audit_legacy_sessions(session_dir, state_db_path, registry)

    assert report["scanned"] == 2
    reasons = {
        (item["session_id"], item["reason"]) for item in report["items"]
        if item["code"] == "legacy_privacy_taint"
    }
    assert ("malformed", "invalid_privacy_context") in reasons
    assert ("stale", "stale_privacy_context") in reasons


def test_privacy_context_without_any_user_turn_is_repairable_orphan(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import audit_legacy_sessions

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    payload = {
        "session_id": "orphan",
        "messages": [{"role": "assistant", "content": "hello"}],
        "privacy_context": {
            "risk_type": "runtime_access",
            "source_turn_id": "missing-user-turn",
            "remaining_turns": 1,
        },
    }
    (session_dir / "orphan.json").write_text(json.dumps(payload), "utf-8")
    report = audit_legacy_sessions(
        session_dir,
        tmp_path / "state.db",
        ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[]),
    )
    assert any(
        item["session_id"] == "orphan"
        and item["code"] == "legacy_privacy_taint"
        and item["reason"] == "orphan_privacy_context"
        for item in report["items"]
    )


def test_tilde_fenced_media_is_never_promoted_or_stripped(tmp_path):
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import migrate_legacy_sessions

    session_dir, cache_dir, session_path, image_path = _write_legacy_fixture(tmp_path)
    payload = json.loads(session_path.read_text("utf-8"))
    payload["messages"][1]["content"] = (
        "~~~text\n"
        f"MEDIA:{image_path}\n"
        "~~~\n"
        f"MEDIA:{image_path}"
    )
    session_path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", payload["messages"][1]["content"])
    report = migrate_legacy_sessions(
        session_dir,
        state_db_path,
        ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir]),
        dry_run=False,
        backup_root=tmp_path / "backups",
    )
    assert report["failed"] == 0
    migrated = json.loads(session_path.read_text("utf-8"))["messages"][1]
    assert migrated["content"].count(f"MEDIA:{image_path}") == 1
    assert f"~~~text\nMEDIA:{image_path}\n~~~" in migrated["content"]
    assert len(migrated["artifacts"]) == 1
    db.close()


def test_migration_invalidates_preloaded_session_and_stale_object_cannot_resave(
    tmp_path, monkeypatch
):
    import api.models as models
    from api.artifacts import ArtifactRegistry
    from api.legacy_session_migration import migrate_legacy_sessions

    session_dir, cache_dir, session_path, _image_path = _write_legacy_fixture(tmp_path)
    payload = json.loads(session_path.read_text("utf-8"))
    payload["privacy_context"] = {
        "risk_type": "runtime_access",
        "source_turn_id": "turn-1",
        "remaining_turns": 1,
    }
    session_path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    with models.LOCK:
        models.SESSIONS.clear()
    cached = models.get_session("legacy-session")
    assert "MEDIA:" in cached.messages[1]["content"]

    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    report = migrate_legacy_sessions(
        session_dir,
        state_db_path,
        ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir]),
        dry_run=False,
        backup_root=tmp_path / "backups",
    )
    assert report["failed"] == 0
    fresh = models.get_session("legacy-session")
    assert fresh is not cached
    assert fresh.privacy_context is None
    assert "MEDIA:" not in fresh.messages[1]["content"]
    with pytest.raises(RuntimeError, match="invalidated by legacy migration"):
        cached.save()
    assert "MEDIA:" not in json.loads(session_path.read_text("utf-8"))["messages"][1]["content"]
    db.close()


@pytest.mark.parametrize("rollback_incomplete", [False, True])
def test_first_migration_failure_stops_before_later_session_mutation(
    tmp_path, monkeypatch, rollback_incomplete
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry
    from hermes_state import SessionDB

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    state_db_path = tmp_path / "state.db"
    db = SessionDB(state_db_path)
    before = {}
    for sid in ("a-session", "b-session"):
        payload = {
            "session_id": sid,
            "title": sid,
            "messages": [{"role": "assistant", "content": sid}],
            "brand_privacy_tainted": True,
        }
        path = session_dir / f"{sid}.json"
        path.write_text(json.dumps(payload, sort_keys=True), "utf-8")
        db.ensure_session(sid, "webui", "test-model")
        db.append_message(sid, "assistant", sid)
        before[sid] = {
            "sidecar": path.read_bytes(),
            "db": [dict(row) for row in db.get_messages(sid)],
        }
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[])
    real_replace = migration._replace_state_db_messages

    def _fail_first(path, session_id, messages):
        if session_id == "a-session":
            raise RuntimeError("injected first batch failure")
        return real_replace(path, session_id, messages)

    monkeypatch.setattr(migration, "_replace_state_db_messages", _fail_first)
    if rollback_incomplete:
        monkeypatch.setattr(
            registry,
            "rollback_registered_artifacts",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rollback failed")),
        )
    report = migration.migrate_legacy_sessions(
        session_dir,
        state_db_path,
        registry,
        dry_run=False,
        backup_root=tmp_path / "backups",
    )
    assert report["failed"] == 1
    failure = next(item for item in report["items"] if item["code"] == "migration_failed")
    assert failure["rollback_complete"] is (not rollback_incomplete)
    assert (session_dir / "b-session.json").read_bytes() == before["b-session"]["sidecar"]
    assert [dict(row) for row in db.get_messages("b-session")] == before["b-session"]["db"]
    assert not (tmp_path / "artifacts" / "b-session").exists()
    db.close()


def test_migration_marks_incomplete_when_created_artifact_unlink_needs_quarantine(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry

    session_dir, cache_dir, _session_path, _image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    existing = registry.register_image_bytes(
        "legacy-session", "existing-turn", "existing-tool", PNG_1X1,
        mime="image/png", name="existing.png",
    )
    session_artifact_dir = tmp_path / "artifacts" / "legacy-session"
    manifest_path = session_artifact_dir / "manifest.json"
    existing_record = json.loads(manifest_path.read_text("utf-8"))["artifacts"][0]
    existing_path = Path(existing_record["storage_path"])
    existing_sha = hashlib.sha256(existing_path.read_bytes()).hexdigest()
    rollback_started = False
    real_unlink = Path.unlink

    def _fail_after_promotion(*_args, **_kwargs):
        nonlocal rollback_started
        rollback_started = True
        raise RuntimeError("injected state db failure")

    def _fail_created_unlink(path, *args, **kwargs):
        if (
            rollback_started
            and path.parent == session_artifact_dir
            and path.name not in {"manifest.json", existing_path.name}
        ):
            raise OSError("injected persistent unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(migration, "_replace_state_db_messages", _fail_after_promotion)
    monkeypatch.setattr(Path, "unlink", _fail_created_unlink)
    report = migration.migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )

    assert report["failed"] == 1
    failure = next(item for item in report["items"] if item["code"] == "migration_failed")
    assert failure["reason"] == "rollback_incomplete"
    assert failure["rollback_complete"] is False
    after = json.loads(manifest_path.read_text("utf-8"))
    assert [row["artifact_id"] for row in after["artifacts"]] == [
        existing["artifact_id"]
    ]
    assert hashlib.sha256(existing_path.read_bytes()).hexdigest() == existing_sha
    assert registry.authorize("legacy-session", existing["artifact_id"]).data == PNG_1X1
    expected_names = {"manifest.json", existing_path.name}
    assert {path.name for path in session_artifact_dir.iterdir()} == expected_names
    quarantined = list((tmp_path / "artifacts" / ".quarantine").glob(
        "migration-rollback-legacy-session-*"
    ))
    assert len(quarantined) == 1
    quarantined_payloads = [
        path for path in quarantined[0].iterdir() if path.name != "quarantine.json"
    ]
    assert len(quarantined_payloads) == 1
    assert hashlib.sha256(quarantined_payloads[0].read_bytes()).hexdigest() == hashlib.sha256(PNG_1X1).hexdigest()
    db.close()


def test_explicit_other_profile_session_fails_closed_before_wrong_database_write(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    import api.profiles as profiles
    from api.artifacts import ArtifactRegistry
    from hermes_state import SessionDB

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sidecar = session_dir / "other-profile.json"
    sidecar.write_text(json.dumps({
        "session_id": "other-profile",
        "profile": "finance",
        "messages": [{"role": "assistant", "content": "keep"}],
        "brand_privacy_tainted": True,
    }), "utf-8")
    active_db = tmp_path / "active" / "state.db"
    active_db.parent.mkdir()
    db = SessionDB(active_db)
    db.ensure_session("other-profile", "webui", "test-model")
    db.append_message("other-profile", "assistant", "keep")
    before_db = [dict(row) for row in db.get_messages("other-profile")]
    before_sidecar = sidecar.read_bytes()
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda profile: tmp_path / profile,
    )
    replaced = []
    monkeypatch.setattr(
        migration,
        "_replace_state_db_messages",
        lambda *args, **kwargs: replaced.append(args),
    )
    report = migration.migrate_legacy_sessions(
        session_dir,
        active_db,
        ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[]),
        dry_run=False,
        backup_root=tmp_path / "backups",
    )
    assert any(item["code"] == "profile_state_db_mismatch" for item in report["items"])
    assert replaced == []
    assert sidecar.read_bytes() == before_sidecar
    assert [dict(row) for row in db.get_messages("other-profile")] == before_db
    db.close()


def test_transferable_worker_lease_blocks_writer_and_releases_on_worker_exception():
    import api.legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    lease = barrier.reserve_worker_read()
    worker_started = threading.Event()
    writer_acquired = threading.Event()

    def _worker():
        try:
            worker_started.set()
            raise RuntimeError("worker failed")
        except RuntimeError:
            pass
        finally:
            lease.release()

    def _writer():
        with barrier.write():
            writer_acquired.set()

    worker = threading.Thread(target=_worker, daemon=True)
    writer = threading.Thread(target=_writer, daemon=True)
    worker.start()
    assert worker_started.wait(timeout=2)
    writer.start()
    worker.join(timeout=2)
    writer.join(timeout=2)
    assert writer_acquired.is_set() is True
    lease.release()  # exactly-once/no-op after worker finalizer


def test_writer_preference_prevents_new_worker_reservation_until_apply_finishes():
    import api.legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    existing = barrier.reserve_worker_read()
    writer_acquired = threading.Event()
    release_writer = threading.Event()
    new_worker_acquired = threading.Event()

    def _writer():
        with barrier.write():
            writer_acquired.set()
            assert release_writer.wait(timeout=2)

    def _new_worker():
        lease = barrier.reserve_worker_read()
        new_worker_acquired.set()
        lease.release()

    writer = threading.Thread(target=_writer, daemon=True)
    writer.start()
    deadline = time.monotonic() + 2
    while barrier._waiting_writers != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert barrier._waiting_writers == 1
    newcomer = threading.Thread(target=_new_worker, daemon=True)
    newcomer.start()
    time.sleep(0.05)
    assert new_worker_acquired.is_set() is False
    existing.release()
    assert writer_acquired.wait(timeout=2)
    assert new_worker_acquired.is_set() is False
    release_writer.set()
    writer.join(timeout=2)
    newcomer.join(timeout=2)
    assert new_worker_acquired.is_set() is True


def test_real_guarded_worker_holds_apply_through_artifact_commit_and_session_save(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    import api.models as models
    from api.artifacts import ArtifactRegistry

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    session = models.Session(
        session_id="worker-session", workspace=tmp_path,
        messages=[{"role": "assistant", "content": "generated"}],
    )
    registry = ArtifactRegistry(tmp_path / "artifacts")
    before_save = threading.Event()
    allow_save = threading.Event()
    worker_done = threading.Event()
    writer_acquired = threading.Event()

    def _worker_commit_then_save():
        descriptor = registry.register_image_bytes(
            session.session_id, "turn", "tool", PNG_1X1,
            mime="image/png", name="generated.png",
        )
        session.messages[0]["artifacts"] = [descriptor]
        before_save.set()
        assert allow_save.wait(timeout=2)
        session.save()
        worker_done.set()

    worker = migration.start_legacy_migration_guarded_worker(
        _worker_commit_then_save, name="migration-worker-contract"
    )
    assert before_save.wait(timeout=2)

    def _apply():
        with migration._legacy_migration_exclusive_guard():
            writer_acquired.set()

    writer = threading.Thread(target=_apply, daemon=True)
    writer.start()
    time.sleep(0.05)
    assert writer_acquired.is_set() is False
    allow_save.set()
    worker.join(timeout=2)
    writer.join(timeout=2)
    assert worker_done.is_set() is True
    assert writer_acquired.is_set() is True
    assert json.loads(session.path.read_text("utf-8"))["messages"][0]["artifacts"]


def test_guarded_worker_releases_lease_on_target_exception():
    import api.legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    lease = barrier.reserve_worker_read()

    def _fail():
        raise RuntimeError("injected target exception")

    with pytest.raises(RuntimeError, match="target exception"):
        migration.run_legacy_migration_guarded_worker(lease, _fail)
    with barrier.write():
        pass
    lease.release()


def test_guarded_worker_releases_lease_when_thread_start_fails(monkeypatch):
    import api.legacy_session_migration as migration

    class _StartFailureThread:
        def __init__(self, *args, **kwargs):
            self.target = kwargs.get("target")

        def start(self):
            raise RuntimeError("injected Thread.start failure")

    monkeypatch.setattr(migration.threading, "Thread", _StartFailureThread)
    with pytest.raises(RuntimeError, match="Thread.start failure"):
        migration.start_legacy_migration_guarded_worker(lambda: None)
    assert migration._MIGRATION_STATE_BARRIER._readers == 0


def test_guarded_worker_cancel_return_releases_apply_waiter():
    import api.legacy_session_migration as migration

    cancel = threading.Event()
    entered = threading.Event()
    writer_acquired = threading.Event()

    def _cancelable_worker():
        entered.set()
        assert cancel.wait(timeout=2)
        return "cancelled"

    worker = migration.start_legacy_migration_guarded_worker(_cancelable_worker)
    assert entered.wait(timeout=2)

    def _apply():
        with migration._legacy_migration_exclusive_guard():
            writer_acquired.set()

    writer = threading.Thread(target=_apply, daemon=True)
    writer.start()
    time.sleep(0.05)
    assert writer_acquired.is_set() is False
    cancel.set()
    worker.join(timeout=2)
    writer.join(timeout=2)
    assert writer_acquired.is_set() is True


def test_chat_worker_start_path_uses_transferable_migration_lease_for_both_backends():
    import inspect
    import api.routes as routes

    source = inspect.getsource(routes._start_chat_stream_for_session)
    assert "worker_target = _run_gateway_chat_streaming" in source
    assert "start_legacy_migration_guarded_worker" in source
    assert "threading.Thread(" not in source


def test_transferred_worker_lease_is_reentrant_for_sink_guard_with_waiting_writer(
    monkeypatch,
):
    """A queued Apply must not deadlock the already-reserved worker at a sink."""
    import api.legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    monkeypatch.setattr(migration, "_MIGRATION_STATE_BARRIER", barrier)
    lease = migration.reserve_legacy_migration_worker_lease()
    worker_started = threading.Event()
    enter_sink = threading.Event()
    sink_finished = threading.Event()
    writer_finished = threading.Event()

    def _worker_target():
        worker_started.set()
        assert enter_sink.wait(timeout=2)
        with migration.legacy_migration_state_guard():
            sink_finished.set()

    worker = threading.Thread(
        target=migration.run_legacy_migration_guarded_worker,
        args=(lease, _worker_target),
        daemon=True,
    )
    worker.start()
    assert worker_started.wait(timeout=2)

    def _writer():
        with barrier.write():
            writer_finished.set()

    writer = threading.Thread(target=_writer, daemon=True)
    writer.start()
    deadline = time.monotonic() + 2
    while barrier._waiting_writers != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert barrier._waiting_writers == 1
    enter_sink.set()
    worker.join(timeout=1)

    assert sink_finished.is_set() is True
    writer.join(timeout=1)
    assert writer_finished.is_set() is True


def test_manual_compression_worker_holds_apply_until_real_worker_returns(monkeypatch):
    import api.legacy_session_migration as migration
    import api.routes as routes

    worker_started = threading.Event()
    release_worker = threading.Event()
    writer_acquired = threading.Event()
    routes._MANUAL_COMPRESSION_JOBS.clear()
    monkeypatch.setattr(
        routes,
        "get_session",
        lambda _sid: SimpleNamespace(active_stream_id=None),
    )
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: payload)

    def _real_worker(_sid, _body):
        worker_started.set()
        assert release_worker.wait(timeout=2)

    monkeypatch.setattr(routes, "_run_manual_compression_job", _real_worker)
    routes._handle_session_compress_start(None, {"session_id": "manual-session"})
    assert worker_started.wait(timeout=2)

    def _apply():
        with migration._legacy_migration_exclusive_guard():
            writer_acquired.set()

    writer = threading.Thread(target=_apply, daemon=True)
    writer.start()
    time.sleep(0.05)
    assert writer_acquired.is_set() is False
    release_worker.set()
    writer.join(timeout=2)
    assert writer_acquired.is_set() is True


def test_adaptive_title_worker_holds_apply_until_real_worker_returns(monkeypatch):
    import api.legacy_session_migration as migration
    import api.streaming as streaming

    worker_started = threading.Event()
    release_worker = threading.Event()
    writer_acquired = threading.Event()
    monkeypatch.setattr(streaming, "_get_title_refresh_interval", lambda: 1)
    monkeypatch.setattr(streaming, "_count_exchanges", lambda _messages: 1)
    monkeypatch.setattr(streaming, "_latest_exchange_snippets", lambda _messages: ("u", "a"))

    def _real_worker(*_args, **_kwargs):
        worker_started.set()
        assert release_worker.wait(timeout=2)

    monkeypatch.setattr(streaming, "_run_background_title_refresh", _real_worker)
    session = SimpleNamespace(
        session_id="title-session",
        title="Existing title",
        llm_title_generated=True,
        messages=[{"role": "user", "content": "u"}],
    )
    streaming._maybe_schedule_title_refresh(session, lambda *_args: None, None)
    assert worker_started.wait(timeout=2)

    def _apply():
        with migration._legacy_migration_exclusive_guard():
            writer_acquired.set()

    writer = threading.Thread(target=_apply, daemon=True)
    writer.start()
    time.sleep(0.05)
    assert writer_acquired.is_set() is False
    release_worker.set()
    writer.join(timeout=2)
    assert writer_acquired.is_set() is True


def test_session_save_sink_waits_for_migration_exclusive_window(tmp_path, monkeypatch):
    import api.legacy_session_migration as migration
    import api.models as models

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    session = models.Session(session_id="sink-session", workspace=tmp_path)
    saved = threading.Event()

    def _save():
        session.save(skip_index=True)
        saved.set()

    with migration._legacy_migration_exclusive_guard():
        worker = threading.Thread(target=_save, daemon=True)
        worker.start()
        time.sleep(0.05)
        assert saved.is_set() is False
        assert session.path.exists() is False
    worker.join(timeout=2)
    assert saved.is_set() is True


def test_state_db_and_artifact_write_sinks_wait_for_migration_exclusive_window(
    tmp_path,
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    db.ensure_session("sink-session", "webui")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[])
    db_written = threading.Event()
    artifact_written = threading.Event()

    def _write_db():
        db.append_message("sink-session", "user", "blocked")
        db_written.set()

    def _write_artifact():
        registry.register_image_bytes(
            "sink-session", "turn-1", "tool-1", PNG_1X1,
            mime="image/png", name="blocked.png",
        )
        artifact_written.set()

    with migration._legacy_migration_exclusive_guard():
        db_worker = threading.Thread(target=_write_db, daemon=True)
        artifact_worker = threading.Thread(target=_write_artifact, daemon=True)
        db_worker.start()
        artifact_worker.start()
        time.sleep(0.05)
        assert db_written.is_set() is False
        assert artifact_written.is_set() is False
    db_worker.join(timeout=2)
    artifact_worker.join(timeout=2)
    assert db_written.is_set() is True
    assert artifact_written.is_set() is True
    db.close()


def test_session_state_db_and_artifact_sinks_are_writer_reentrant(tmp_path, monkeypatch):
    import api.legacy_session_migration as migration
    import api.models as models
    from api.artifacts import ArtifactRegistry
    from hermes_state import SessionDB

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    session = models.Session(session_id="reentrant-session", workspace=tmp_path)
    db = SessionDB(tmp_path / "state.db")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[])

    with migration._legacy_migration_exclusive_guard():
        session.save(skip_index=True)
        db.ensure_session("reentrant-session", "webui")
        db.replace_messages(
            "reentrant-session", [{"role": "user", "content": "inside writer"}],
        )
        artifact = registry.register_image_bytes(
            "reentrant-session", "turn-1", "tool-1", PNG_1X1,
            mime="image/png", name="inside-writer.png",
        )

    assert session.path.exists()
    assert db.get_messages("reentrant-session")[0]["content"] == "inside writer"
    assert registry.authorize(
        "reentrant-session", artifact["artifact_id"]
    ).read_bytes() == PNG_1X1
    db.close()


def test_no_bare_async_session_state_writer_targets_remain():
    """Static gate: known state-writing targets must use the transferable wrapper."""
    import re

    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        (root / relative).read_text("utf-8")
        for relative in ("api/routes.py", "api/streaming.py", "api/models.py")
    )
    bare_targets = (
        "_run_manual_compression_job",
        "_periodic_checkpoint",
        "_run_background_title_update",
        "_run_background_title_refresh",
        "_rebuild_session_index_background",
        "_run_cron_tracked",
    )
    for target in bare_targets:
        assert re.search(
            rf"threading\.Thread\s*\([^)]*target\s*=\s*{target}\b",
            sources,
            flags=re.DOTALL,
        ) is None, target


def test_session_db_write_guard_hook_registration_is_owner_safe(tmp_path):
    from contextlib import contextmanager
    from hermes_state import (
        SessionDB,
        install_state_write_guard,
        restore_state_write_guard,
    )

    first_entries = []
    second_entries = []

    @contextmanager
    def first_guard():
        first_entries.append("enter")
        yield

    @contextmanager
    def second_guard():
        second_entries.append("enter")
        yield

    original = install_state_write_guard(first_guard)
    try:
        replaced = install_state_write_guard(second_guard)
        assert replaced is first_guard
        assert restore_state_write_guard(first_guard, original) is False
        db = SessionDB(tmp_path / "hook.db")
        db.ensure_session("hook-session", "webui")
        # Schema initialization and the first semantic write are both guarded.
        assert second_entries == ["enter", "enter"]
        assert first_entries == []
        assert restore_state_write_guard(second_guard, first_guard) is True
        db.append_message("hook-session", "user", "guarded")
        assert first_entries == ["enter"]
        db.close()
    finally:
        restore_state_write_guard(first_guard, original)


def test_session_db_schema_init_waits_for_migration_exclusive_window(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    from hermes_state import SessionDB

    barrier = migration._MigrationStateBarrier()
    monkeypatch.setattr(migration, "_MIGRATION_STATE_BARRIER", barrier)
    writer_entered = threading.Event()
    release_writer = threading.Event()
    init_finished = threading.Event()
    holder = {}

    def writer():
        with barrier.write():
            writer_entered.set()
            assert release_writer.wait(timeout=5)

    def initialize():
        holder["db"] = SessionDB(tmp_path / "schema-blocked.db")
        init_finished.set()

    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()
    assert writer_entered.wait(timeout=2)
    init_thread = threading.Thread(target=initialize, daemon=True)
    init_thread.start()
    time.sleep(0.05)
    assert not init_finished.is_set(), "schema commit bypassed migration exclusive guard"
    release_writer.set()
    writer_thread.join(timeout=2)
    init_thread.join(timeout=2)
    assert init_finished.is_set()
    assert barrier._readers == 0
    holder["db"].close()


def test_session_db_schema_failure_releases_process_local_write_guard(
    tmp_path, monkeypatch
):
    from contextlib import contextmanager
    from hermes_state import (
        SessionDB,
        install_state_write_guard,
        restore_state_write_guard,
    )

    entries = []

    @contextmanager
    def tracking_guard():
        entries.append("enter")
        try:
            yield
        finally:
            entries.append("exit")

    def fail_schema(_self, _cursor):
        raise RuntimeError("injected schema failure")

    previous = install_state_write_guard(tracking_guard)
    monkeypatch.setattr(SessionDB, "_reconcile_columns", fail_schema)
    try:
        with pytest.raises(RuntimeError, match="injected schema failure"):
            SessionDB(tmp_path / "schema-failure.db")
        assert entries == ["enter", "exit"]
    finally:
        restore_state_write_guard(tracking_guard, previous)


def test_agent_state_write_hook_has_no_reverse_webui_import():
    import hermes_state

    source = Path(hermes_state.__file__).read_text("utf-8")
    assert "api.legacy_session_migration" not in source


def test_core_sink_exceptions_release_shared_barrier(tmp_path, monkeypatch):
    import api.legacy_session_migration as migration
    import api.models as models
    from api.artifacts import ArtifactRegistry, ArtifactValidationError
    from hermes_state import SessionDB

    barrier = migration._MigrationStateBarrier()
    monkeypatch.setattr(migration, "_MIGRATION_STATE_BARRIER", barrier)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")

    stale = models.Session(session_id="stale-session", workspace=tmp_path)
    stale._invalidated_by_legacy_migration = True
    with pytest.raises(RuntimeError, match="invalidated by legacy migration"):
        stale.save(skip_index=True)

    db = SessionDB(tmp_path / "state.db")
    with pytest.raises(RuntimeError, match="sink failure"):
        db._execute_write(
            lambda _conn: (_ for _ in ()).throw(RuntimeError("sink failure"))
        )

    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[])
    with pytest.raises(ArtifactValidationError):
        registry.register_image_bytes(
            "stale-session", "turn-1", "tool-1", b"not-an-image",
            mime="image/png", name="bad.png",
        )

    assert barrier._readers == 0
    with barrier.write():
        pass
    db.close()


def test_restore_failure_rolls_back_current_snapshot_or_reports_quarantine(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry

    session_dir, cache_dir, session_path, image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    registry.register_image_bytes(
        "legacy-session", "turn", "tool", PNG_1X1,
        mime="image/png", name="existing.png",
    )
    applied = migration.migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )
    backup = Path(applied["backup_path"])
    session_path.write_text('{"damaged": true}', "utf-8")
    image_path.write_bytes(b"damaged-cache")
    db.replace_messages("legacy-session", [{"role": "assistant", "content": "damaged-db"}])
    damaged_session = session_path.read_bytes()
    damaged_cache = image_path.read_bytes()
    damaged_db = _logical_messages(db)
    real_replace_tree = migration._replace_tree_from_backup
    calls = 0

    def _fail_artifact_publish(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected restore artifact failure")
        return real_replace_tree(source, destination)

    monkeypatch.setattr(migration, "_replace_tree_from_backup", _fail_artifact_publish)
    error_type = getattr(migration, "MigrationRestoreError", RuntimeError)
    with pytest.raises(error_type) as raised:
        migration.restore_legacy_migration_backup(
            backup, session_dir, state_db_path, registry
        )
    assert getattr(raised.value, "code", "") in {
        "restore_rolled_back", "rollback_incomplete"
    }
    if getattr(raised.value, "code", "") == "restore_rolled_back":
        assert session_path.read_bytes() == damaged_session
        assert image_path.read_bytes() == damaged_cache
        assert _logical_messages(db) == damaged_db
    else:
        assert getattr(raised.value, "quarantined", False) is True
    db.close()


def test_restore_rollback_failure_is_explicit_and_keeps_private_quarantine(
    tmp_path, monkeypatch
):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry

    session_dir, cache_dir, session_path, _image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    registry.register_image_bytes(
        "legacy-session", "existing-turn", "existing-tool", PNG_1X1,
        mime="image/png", name="existing.png",
    )
    applied = migration.migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )
    backup = Path(applied["backup_path"])
    session_path.write_text('{"damaged": true}', "utf-8")
    real_replace_tree = migration._replace_tree_from_backup
    real_restore_sessions = migration._restore_session_tree_preserving_journals
    artifact_calls = 0
    session_calls = 0

    def _fail_forward_artifact(source, destination):
        nonlocal artifact_calls
        artifact_calls += 1
        if artifact_calls == 1:
            raise OSError("injected restore artifact failure")
        return real_replace_tree(source, destination)

    def _fail_session_rollback(source, destination):
        nonlocal session_calls
        session_calls += 1
        if session_calls == 2:
            raise OSError("injected session rollback failure")
        return real_restore_sessions(source, destination)

    monkeypatch.setattr(migration, "_replace_tree_from_backup", _fail_forward_artifact)
    monkeypatch.setattr(
        migration,
        "_restore_session_tree_preserving_journals",
        _fail_session_rollback,
    )
    with pytest.raises(migration.MigrationRestoreError) as raised:
        migration.restore_legacy_migration_backup(
            backup, session_dir, state_db_path, registry
        )
    assert raised.value.code == "rollback_incomplete"
    assert raised.value.quarantined is True
    receipts = list((backup.parent / ".restore-quarantine").glob(
        "*/restore-failure.json"
    ))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text("utf-8"))
    assert receipt == {
        "schema_version": 1,
        "code": "rollback_incomplete",
        "quarantined": True,
        "created_at": receipt["created_at"],
    }
    assert "/Users/" not in receipts[0].read_text("utf-8")
    db.close()


def test_metering_journal_remains_append_only_during_migration_and_restore(tmp_path):
    import api.legacy_session_migration as migration
    from api.artifacts import ArtifactRegistry
    from api.run_journal import RunJournalWriter, read_run_events

    session_dir, cache_dir, _session_path, _image_path = _write_legacy_fixture(tmp_path)
    state_db_path = tmp_path / "state.db"
    db = _session_db(state_db_path)
    db.append_message("legacy-session", "assistant", "generated")
    registry = ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[cache_dir])
    writer = RunJournalWriter("legacy-session", "metering-run", session_dir=session_dir)
    writer.append_sse_event("metering", {"tick": 0})
    started = threading.Event()
    stop = threading.Event()
    appended = []

    def _metering_ticker():
        tick = 1
        started.set()
        while not stop.is_set() and tick <= 200:
            writer.append_sse_event("metering", {"tick": tick})
            appended.append(tick)
            tick += 1
            time.sleep(0.001)

    ticker = threading.Thread(target=_metering_ticker, daemon=True)
    ticker.start()
    assert started.wait(timeout=2)
    began = time.monotonic()
    applied = migration.migrate_legacy_sessions(
        session_dir, state_db_path, registry, dry_run=False,
        backup_root=tmp_path / "backups",
    )
    assert applied["failed"] == 0
    migration.restore_legacy_migration_backup(
        Path(applied["backup_path"]), session_dir, state_db_path, registry
    )
    stop.set()
    ticker.join(timeout=2)
    assert ticker.is_alive() is False
    assert time.monotonic() - began < 5

    events = read_run_events(
        "legacy-session", "metering-run", session_dir=session_dir
    )["events"]
    assert len(events) == 1 + len(appended)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert [event["payload"]["tick"] for event in events] == [0, *appended]
    db.close()
