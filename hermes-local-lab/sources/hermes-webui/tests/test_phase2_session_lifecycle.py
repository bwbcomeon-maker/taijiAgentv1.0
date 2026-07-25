from __future__ import annotations

import json
import copy
import shutil
import sqlite3
import subprocess
import sys
from collections import OrderedDict
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest


def _call_post(monkeypatch, routes, path: str, body: dict):
    payload = json.dumps(body).encode()
    captured = {}

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)

    def fake_j(_handler, response, status=200, extra_headers=None):
        captured.update(payload=response, status=status)

    def fake_bad(_handler, message, status=400):
        captured.update(payload={"error": str(message)}, status=status)

    monkeypatch.setattr(routes, "j", fake_j)
    monkeypatch.setattr(routes, "bad", fake_bad)
    handler = SimpleNamespace(
        headers={"Content-Length": str(len(payload))},
        rfile=BytesIO(payload),
    )
    routes.handle_post(handler, SimpleNamespace(path=path))
    return captured


@pytest.fixture
def isolated_sessions(monkeypatch, tmp_path):
    import api.config as config
    import api.models as models
    import api.routes as routes
    import api.session_ops as session_ops

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sessions = OrderedDict()
    monkeypatch.setattr(config, "SESSION_DIR", session_dir)
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(config, "SESSIONS", sessions)
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", sessions)
    monkeypatch.setattr(routes, "SESSION_DIR", session_dir)
    monkeypatch.setattr(routes, "SESSIONS", sessions)
    monkeypatch.setattr(session_ops, "SESSIONS", sessions)
    monkeypatch.setattr(session_ops, "LOCK", config.LOCK)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_a, **_k: None)
    monkeypatch.setattr(config, "_evict_session_agent", lambda _sid: None)
    return routes, models, sessions, tmp_path


def _seed_session(models, sessions, tmp_path, *, session_id="phase2-lifecycle"):
    session = models.Session(
        session_id=session_id,
        workspace=str(tmp_path),
        profile="default",
        messages=[
            {"role": "user", "content": "first", "platform_message_id": "webui-turn:t1"},
            {"role": "assistant", "content": "reply first"},
            {"role": "user", "content": "second", "platform_message_id": "webui-turn:t2"},
            {"role": "assistant", "content": "reply second"},
        ],
        context_messages=[
            {"role": "system", "content": "stale current-session wrapper"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply first"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "reply second"},
        ],
        tool_calls=[{"name": "old-tool"}],
        active_stream_id="stream-running",
        pending_user_message="unfinished",
        pending_attachments=[{"path": "pending.txt"}],
        pending_started_at=123.0,
        compression_anchor_visible_idx=2,
        compression_anchor_message_key="anchor",
        compression_anchor_summary="summary",
        compression_anchor_engine="engine",
        compression_anchor_mode="mode",
        compression_anchor_details={"pending": True},
        context_engine_state={"in_flight": True},
        gateway_routing={"run_id": "unfinished-run"},
        gateway_routing_history=[{"run_id": "unfinished-run"}],
        privacy_context={
            "risk_type": "runtime_access",
            "source_turn_id": "t2",
            "remaining_turns": 1,
            "reset_reason": None,
        },
    )
    session.save(skip_index=True)
    sessions[session.session_id] = session
    return session


@pytest.mark.parametrize("operation", ["retry_last", "undo_last"])
def test_intentional_rewrite_is_not_resurrected_by_startup_backup_recovery(
    monkeypatch,
    isolated_sessions,
    operation,
):
    import api.session_ops as session_ops
    import api.session_recovery as recovery

    _routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"bak-{operation}",
    )
    before = copy.deepcopy(session.messages)
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: True,
    )

    getattr(session_ops, operation)(session.session_id)

    live = json.loads(session.path.read_text(encoding="utf-8"))
    assert live["messages"] == before[:2]
    assert session.path.with_suffix(".json.bak").exists() is False
    result = recovery.recover_all_sessions_on_startup(
        models.SESSION_DIR,
        rebuild_index=False,
    )
    after = json.loads(session.path.read_text(encoding="utf-8"))
    assert result["restored"] == 0
    assert after["messages"] == before[:2]


def test_truth_rewrite_and_full_index_rebuild_do_not_deadlock(tmp_path):
    script = r'''
import sys, threading
from collections import OrderedDict
from pathlib import Path
import api.config as config, api.models as models, api.routes as routes
import api.session_ops as session_ops, api.truth_rewrite as truth

d = Path(sys.argv[1]); d.mkdir(exist_ok=True)
sessions = OrderedDict()
for mod in (config, models, routes):
    if hasattr(mod, "SESSION_DIR"): mod.SESSION_DIR = d
    if hasattr(mod, "SESSION_INDEX_FILE"): mod.SESSION_INDEX_FILE = d / "_index.json"
    if hasattr(mod, "SESSIONS"): mod.SESSIONS = sessions
session_ops.SESSIONS = sessions; session_ops.LOCK = config.LOCK
s = models.Session(session_id="lock-cycle", workspace=str(d), profile="default",
    messages=[{"role":"user","content":"u1"},{"role":"assistant","content":"a1"},
              {"role":"user","content":"u2"},{"role":"assistant","content":"a2"}])
s.save(skip_index=True); sessions[s.session_id] = s
entered = threading.Event(); release = threading.Event()
def fake_replace(*_args, **_kwargs):
    entered.set(); release.wait(5); return True
routes._replace_state_db_truth = fake_replace
truth._default_read_state_messages = lambda _s: list(s.messages)
truth._default_replace_state_messages = lambda _s, _m: True

def writer():
    routes._rewrite_existing_session_truth(
        s, lambda: setattr(s, "messages", s.messages[:2]),
        privacy_reason="retry", preserve_context_messages=True)
writer_thread = threading.Thread(target=writer, daemon=True); writer_thread.start()
assert entered.wait(2)
index_thread = threading.Thread(
    target=lambda: models._write_session_index(updates=None), daemon=True)
index_thread.start()
threading.Event().wait(.2)
release.set(); writer_thread.join(2); index_thread.join(2)
if writer_thread.is_alive() or index_thread.is_alive(): raise SystemExit(77)
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "sessions")],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_expert_team_append_uses_session_writer_lock_and_preserves_concurrent_message(
    monkeypatch,
    tmp_path,
):
    import api.routes as routes

    session = SimpleNamespace(
        session_id="expert-lock-session",
        title="Existing",
        messages=[{"role": "user", "content": "initial"}],
        context_messages=[{"role": "user", "content": "initial"}],
        profile=None,
        model="test-model",
        path=tmp_path / "expert-lock-session.json",
    )
    lock_state = {"active": False, "requested": False}

    class InjectingWriterLock:
        def __enter__(self):
            lock_state["requested"] = True
            lock_state["active"] = True
            session.messages.append(
                {"role": "assistant", "content": "concurrent-stream"}
            )

        def __exit__(self, *_args):
            lock_state["active"] = False

    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(
        routes,
        "_get_session_agent_lock",
        lambda _sid: InjectingWriterLock(),
    )

    def rewrite(_session, mutate, **_kwargs):
        assert lock_state["active"] is True
        mutate()

    monkeypatch.setattr(routes, "_rewrite_existing_session_truth", rewrite)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args: None)
    run = {
        "session_id": session.session_id,
        "run_id": "expert-run-lock",
        "title": "并发安全任务",
        "team_title": "内容创作专家团",
        "view": {"business_context": {"visible_title": "并发安全任务"}},
    }

    appended = routes._append_expert_team_session_entry(run)

    assert lock_state["requested"] is True
    assert len(appended) == 2
    assert [message["content"] for message in session.messages[:2]] == [
        "initial",
        "concurrent-stream",
    ]


def test_delete_tombstones_stale_worker_and_cancels_live_stream(
    monkeypatch,
    isolated_sessions,
):
    import threading

    import api.config as config

    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-live-session",
    )
    session.active_stream_id = "delete-live-stream"
    session.pending_user_message = "running"
    session.save(skip_index=True)
    stale_worker_session = session
    external_stale_session = models.Session.load(session.session_id)
    assert external_stale_session is not None
    cancel_flag = threading.Event()
    monkeypatch.setattr(routes, "STREAMS", {"delete-live-stream": object()})
    monkeypatch.setattr(routes, "CANCEL_FLAGS", {"delete-live-stream": cancel_flag})
    monkeypatch.setattr(config, "AGENT_INSTANCES", {})
    deleted_state_rows = []
    writer_lock_depth = 0
    import api.truth_rewrite as truth_rewrite

    original_writer_lock = truth_rewrite.truth_rewrite_lock

    @contextmanager
    def observed_writer_lock(session_id, **kwargs):
        nonlocal writer_lock_depth
        with original_writer_lock(session_id, **kwargs):
            writer_lock_depth += 1
            try:
                yield
            finally:
                writer_lock_depth -= 1

    monkeypatch.setattr(truth_rewrite, "truth_rewrite_lock", observed_writer_lock)

    def delete_cli_session(sid):
        assert writer_lock_depth > 0
        deleted_state_rows.append(sid)
        return True

    monkeypatch.setattr(
        models,
        "delete_cli_session",
        delete_cli_session,
    )
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(retire_session=lambda _sid: []),
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert response["status"] == 200
    assert session.path.exists() is False
    assert getattr(stale_worker_session, "_deleted", False) is True
    assert stale_worker_session.active_stream_id is None
    assert cancel_flag.is_set()
    assert "delete-live-stream" not in routes.STREAMS
    assert deleted_state_rows == [session.session_id]

    stale_worker_session.messages.append(
        {"role": "assistant", "content": "late completion"}
    )
    with pytest.raises(RuntimeError, match="deleted session"):
        stale_worker_session.save(skip_index=True)
    external_stale_session.messages.append(
        {"role": "assistant", "content": "late cross-process completion"}
    )
    with pytest.raises(models.SessionWriteConflict, match="deleted"):
        external_stale_session.save(skip_index=True)
    assert session.path.exists() is False


@pytest.mark.parametrize("failed_authority", ["sidecar", "backup"])
def test_delete_authority_unlink_failure_is_reported_without_related_cleanup(
    failed_authority,
    monkeypatch,
    isolated_sessions,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"delete-unlink-failure-{failed_authority}",
    )
    sidecar = session.path.resolve()
    backup = sidecar.with_suffix(".json.bak")
    original_sidecar = sidecar.read_bytes()
    backup.write_bytes(original_sidecar)
    cleanup_calls = {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    monkeypatch.setattr(
        routes.shutil,
        "rmtree",
        lambda path, **_kwargs: cleanup_calls["attachments"].append(str(path)),
    )
    failed_leaf = sidecar.name if failed_authority == "sidecar" else backup.name
    real_unlink = routes.os.unlink
    failed = False

    def fail_selected_unlink(path, *args, **kwargs):
        nonlocal failed
        candidate = Path(path).name
        if (
            not failed
            and candidate.startswith(f".{failed_leaf}.")
            and candidate.endswith(".delete-quarantine")
        ):
            failed = True
            raise OSError(f"injected {failed_authority} unlink failure")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as delete_patch:
        delete_patch.setattr(routes.os, "unlink", fail_selected_unlink)
        response = _call_post(
            delete_patch,
            routes,
            "/api/session/delete",
            {"session_id": session.session_id},
        )

    assert failed is True
    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_persistence_failed"
    assert cleanup_calls == {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }
    assert sidecar.read_bytes() == original_sidecar
    assert backup.read_bytes() == original_sidecar
    assert sessions[session.session_id] is session
    assert getattr(session, "_deleted", False) is False

    # A wholly new interpreter must observe a retained session, not a falsely
    # successful deletion followed by resurrection from the surviving sidecar.
    script = """
import sys
from collections import OrderedDict
from pathlib import Path
import api.config as config
import api.models as models

session_dir = Path(sys.argv[1])
session_id = sys.argv[2]
sessions = OrderedDict()
for module in (config, models):
    module.SESSION_DIR = session_dir
    module.SESSION_INDEX_FILE = session_dir / "_index.json"
    module.SESSIONS = sessions
session = models.Session.load(session_id)
if session is None:
    raise SystemExit(71)
session.messages.append({"role": "assistant", "content": "fresh process save"})
session.context_messages = list(session.messages)
session.save(touch_updated_at=False, skip_index=True)
reloaded = models.Session.load(session_id)
if reloaded is None or reloaded.messages[-1].get("content") != "fresh process save":
    raise SystemExit(72)
"""
    fresh = subprocess.run(
        [sys.executable, "-c", script, str(sidecar.parent), session.session_id],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert fresh.returncode == 0, fresh.stderr or fresh.stdout


def test_delete_rollback_fsyncs_each_restored_authority_directory(
    monkeypatch,
    isolated_sessions,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-rollback-directory-fsync",
    )
    sidecar = session.path
    sidecar_bytes = sidecar.read_bytes()
    backup = sidecar.with_suffix(".json.bak")
    backup.write_bytes(sidecar_bytes)
    intent_dir = sidecar.parent / ".truth-rewrite-intents"
    intent_dir.mkdir()
    intent = intent_dir / sidecar.name
    intent.write_bytes(b"delete intent")
    cleanup_calls = {"artifacts": [], "index": [], "state": []}
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    root_metadata = sidecar.parent.stat()
    intent_metadata = intent_dir.stat()
    root_identity = (int(root_metadata.st_dev), int(root_metadata.st_ino))
    intent_identity = (int(intent_metadata.st_dev), int(intent_metadata.st_ino))
    fsync_counts = {root_identity: 0, intent_identity: 0}
    real_fsync = routes.os.fsync
    real_unlink = routes.os.unlink
    unlink_failed = False

    def observe_fsync(fd):
        metadata = routes.os.fstat(fd)
        identity = (int(metadata.st_dev), int(metadata.st_ino))
        if identity in fsync_counts:
            fsync_counts[identity] += 1
        return real_fsync(fd)

    def fail_backup_quarantine_unlink(path, *args, **kwargs):
        nonlocal unlink_failed
        name = Path(path).name
        if (
            not unlink_failed
            and name.startswith(f".{backup.name}.")
            and name.endswith(".delete-quarantine")
        ):
            unlink_failed = True
            raise OSError("injected backup quarantine unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(routes.os, "fsync", observe_fsync)
    monkeypatch.setattr(routes.os, "unlink", fail_backup_quarantine_unlink)

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert unlink_failed is True
    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_persistence_failed"
    assert fsync_counts[root_identity] >= 2
    assert fsync_counts[intent_identity] >= 2
    assert sidecar.read_bytes() == sidecar_bytes
    assert backup.read_bytes() == sidecar_bytes
    assert intent.read_bytes() == b"delete intent"
    assert cleanup_calls == {"artifacts": [], "index": [], "state": []}
    assert sessions[session.session_id] is session
    assert getattr(session, "_deleted", False) is False


def test_delete_rollback_fsync_failure_is_reported_as_recovery_failure(
    monkeypatch,
    isolated_sessions,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-rollback-fsync-failure",
    )
    sidecar = session.path
    sidecar_bytes = sidecar.read_bytes()
    backup = sidecar.with_suffix(".json.bak")
    backup.write_bytes(sidecar_bytes)
    intent_dir = sidecar.parent / ".truth-rewrite-intents"
    intent_dir.mkdir()
    intent = intent_dir / sidecar.name
    intent.write_bytes(b"delete intent")
    cleanup_calls = {"artifacts": [], "index": [], "state": []}
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    root_metadata = sidecar.parent.stat()
    root_identity = (int(root_metadata.st_dev), int(root_metadata.st_ino))
    root_fsync_count = 0
    real_fsync = routes.os.fsync
    real_unlink = routes.os.unlink
    unlink_failed = False
    rollback_fsync_failed = False

    def fail_rollback_root_fsync(fd):
        nonlocal root_fsync_count, rollback_fsync_failed
        metadata = routes.os.fstat(fd)
        identity = (int(metadata.st_dev), int(metadata.st_ino))
        if identity == root_identity:
            root_fsync_count += 1
            if root_fsync_count == 2:
                rollback_fsync_failed = True
                raise OSError("injected rollback root fsync failure")
        return real_fsync(fd)

    def fail_backup_quarantine_unlink(path, *args, **kwargs):
        nonlocal unlink_failed
        name = Path(path).name
        if (
            not unlink_failed
            and name.startswith(f".{backup.name}.")
            and name.endswith(".delete-quarantine")
        ):
            unlink_failed = True
            raise OSError("injected backup quarantine unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(routes.os, "fsync", fail_rollback_root_fsync)
    monkeypatch.setattr(routes.os, "unlink", fail_backup_quarantine_unlink)

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert unlink_failed is True
    assert rollback_fsync_failed is True
    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_recovery_failed"
    assert sidecar.read_bytes() == sidecar_bytes
    assert backup.read_bytes() == sidecar_bytes
    assert intent.read_bytes() == b"delete intent"
    assert cleanup_calls == {"artifacts": [], "index": [], "state": []}
    assert sessions[session.session_id] is session
    assert getattr(session, "_deleted", False) is True


@pytest.mark.parametrize(
    "authority_target",
    [
        "sidecar_same_root",
        "sidecar_outside",
        "backup_symlink",
        "intent_leaf_symlink",
    ],
)
def test_delete_rejects_session_authority_symlink_without_touching_target(
    authority_target,
    monkeypatch,
    isolated_sessions,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"delete-authority-symlink-{authority_target}",
    )
    if authority_target == "sidecar_same_root":
        victim = _seed_session(
            models,
            sessions,
            tmp_path,
            session_id="delete-authority-symlink-victim",
        )
        victim_path = victim.path
    else:
        victim_path = tmp_path / f"{authority_target}-outside-victim.json"
        victim_path.write_bytes(b"outside authority victim")
    victim_bytes = victim_path.read_bytes()
    if authority_target.startswith("sidecar"):
        authority_path = session.path
        authority_path.unlink()
    elif authority_target == "backup_symlink":
        authority_path = session.path.with_suffix(".json.bak")
    else:
        intent_dir = session.path.parent / ".truth-rewrite-intents"
        intent_dir.mkdir()
        authority_path = intent_dir / f"{session.session_id}.json"
    authority_path.symlink_to(victim_path)
    cleanup_calls = {"artifacts": [], "index": [], "state": []}
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_persistence_failed"
    assert cleanup_calls == {"artifacts": [], "index": [], "state": []}
    assert authority_path.is_symlink()
    assert victim_path.read_bytes() == victim_bytes
    assert sessions[session.session_id] is session
    assert getattr(session, "_deleted", False) is False


def test_delete_cold_cache_rejects_sidecar_symlink_without_loading_target(
    monkeypatch,
    isolated_sessions,
):
    """DELETE must anchor authority before any Session deserialization."""
    routes, models, sessions, tmp_path = isolated_sessions
    victim = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-cold-symlink-victim",
    )
    requested = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-cold-symlink-requested",
    )
    victim_bytes = victim.path.read_bytes()
    requested.path.unlink()
    requested.path.symlink_to(victim.path)
    sessions.clear()
    cleanup_calls = {"artifacts": [], "index": [], "state": []}
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": requested.session_id},
    )

    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_persistence_failed"
    assert cleanup_calls == {"artifacts": [], "index": [], "state": []}
    assert requested.path.is_symlink()
    assert victim.path.read_bytes() == victim_bytes
    assert sessions == {}


def test_delete_fails_closed_when_intent_parent_is_swapped_after_snapshot(
    monkeypatch,
    isolated_sessions,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-intent-parent-swap",
    )
    sidecar_bytes = session.path.read_bytes()
    backup = session.path.with_suffix(".json.bak")
    backup.write_bytes(sidecar_bytes)
    intent_dir = session.path.parent / ".truth-rewrite-intents"
    intent_dir.mkdir()
    intent = intent_dir / f"{session.session_id}.json"
    intent.write_bytes(b"original intent")
    held_intent_dir = session.path.parent / ".truth-rewrite-intents-held"
    outside_dir = tmp_path / "outside-intents"
    outside_dir.mkdir()
    outside_victim = outside_dir / f"{session.session_id}.json"
    outside_victim.write_bytes(b"outside intent victim")
    cleanup_calls = {"artifacts": [], "index": [], "state": []}
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    real_rename = routes.os.rename
    swapped = False

    def swap_parent_before_backup_quarantine(source, destination, *args, **kwargs):
        nonlocal swapped
        if (
            not swapped
            and Path(source).name == backup.name
            and Path(destination).name.endswith(".delete-quarantine")
        ):
            swapped = True
            intent_dir.rename(held_intent_dir)
            intent_dir.symlink_to(outside_dir, target_is_directory=True)
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(routes.os, "rename", swap_parent_before_backup_quarantine)

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert swapped is True
    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_recovery_failed"
    assert cleanup_calls == {"artifacts": [], "index": [], "state": []}
    assert session.path.read_bytes() == sidecar_bytes
    assert backup.read_bytes() == sidecar_bytes
    assert (held_intent_dir / intent.name).read_bytes() == b"original intent"
    assert outside_victim.read_bytes() == b"outside intent victim"
    assert sessions[session.session_id] is session
    assert getattr(session, "_deleted", False) is True


def test_delete_preserves_authority_leaf_replaced_after_validation(
    monkeypatch,
    isolated_sessions,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-leaf-validation-race",
    )
    sidecar = session.path
    original_bytes = sidecar.read_bytes()
    sidecar.with_suffix(".json.bak").write_bytes(original_bytes)
    replacement_payload = json.loads(original_bytes.decode("utf-8"))
    replacement_payload["title"] = "newer durable Session"
    replacement_bytes = json.dumps(
        replacement_payload,
        ensure_ascii=False,
    ).encode("utf-8")
    cleanup_calls = {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    monkeypatch.setattr(
        routes.shutil,
        "rmtree",
        lambda path, **_kwargs: cleanup_calls["attachments"].append(str(path)),
    )
    real_assert_unchanged = routes._SessionDeleteAuthority._assert_entry_unchanged
    replaced = False

    def replace_primary_after_validation(authority, label):
        nonlocal replaced
        present = real_assert_unchanged(authority, label)
        if label == "primary" and present and not replaced:
            replaced = True
            replacement = sidecar.parent / ".newer-session.json"
            replacement.write_bytes(replacement_bytes)
            replacement.replace(sidecar)
        return present

    monkeypatch.setattr(
        routes._SessionDeleteAuthority,
        "_assert_entry_unchanged",
        replace_primary_after_validation,
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert replaced is True
    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_persistence_failed"
    assert sidecar.read_bytes() == replacement_bytes
    assert cleanup_calls == {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }


def test_delete_rejects_session_root_replaced_after_snapshot(
    monkeypatch,
    isolated_sessions,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-root-binding-race",
    )
    session_root = session.path.parent
    held_root = tmp_path / "sessions-held-after-snapshot"
    sidecar_name = session.path.name
    original_bytes = session.path.read_bytes()
    session.path.with_suffix(".json.bak").write_bytes(original_bytes)
    replacement_payload = json.loads(original_bytes.decode("utf-8"))
    replacement_payload["title"] = "current configured root Session"
    replacement_bytes = json.dumps(
        replacement_payload,
        ensure_ascii=False,
    ).encode("utf-8")
    cleanup_calls = {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    monkeypatch.setattr(
        routes.shutil,
        "rmtree",
        lambda path, **_kwargs: cleanup_calls["attachments"].append(str(path)),
    )
    real_assert_unchanged = routes._SessionDeleteAuthority._assert_entry_unchanged
    replaced = False

    def replace_root_after_validation(authority, label):
        nonlocal replaced
        present = real_assert_unchanged(authority, label)
        if label == "backup" and present and not replaced:
            replaced = True
            session_root.rename(held_root)
            session_root.mkdir()
            (session_root / sidecar_name).write_bytes(replacement_bytes)
        return present

    monkeypatch.setattr(
        routes._SessionDeleteAuthority,
        "_assert_entry_unchanged",
        replace_root_after_validation,
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert replaced is True
    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_recovery_failed"
    assert (session_root / sidecar_name).read_bytes() == replacement_bytes
    assert (held_root / sidecar_name).read_bytes() == original_bytes
    assert cleanup_calls == {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }


def test_delete_rejects_root_parent_rebound_through_symlink_after_snapshot(
    monkeypatch,
    isolated_sessions,
):
    """An ancestor symlink cannot disguise a changed lexical root binding."""
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-root-parent-binding-race",
    )
    sidecar = session.path
    original_bytes = sidecar.read_bytes()
    sidecar.with_suffix(".json.bak").write_bytes(original_bytes)
    held_parent = tmp_path.with_name(f"{tmp_path.name}-held")
    cleanup_calls = {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda sid: cleanup_calls["artifacts"].append(sid)
        ),
    )
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    monkeypatch.setattr(
        routes.shutil,
        "rmtree",
        lambda path, **_kwargs: cleanup_calls["attachments"].append(str(path)),
    )
    real_assert_unchanged = routes._SessionDeleteAuthority._assert_entry_unchanged
    rebound = False

    def rebind_parent_after_validation(authority, label):
        nonlocal rebound
        present = real_assert_unchanged(authority, label)
        if label == "backup" and present and not rebound:
            rebound = True
            tmp_path.rename(held_parent)
            tmp_path.symlink_to(held_parent, target_is_directory=True)
        return present

    monkeypatch.setattr(
        routes._SessionDeleteAuthority,
        "_assert_entry_unchanged",
        rebind_parent_after_validation,
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert rebound is True
    assert response["status"] == 500
    assert response["payload"]["code"] == "session_delete_recovery_failed"
    assert (held_parent / "sessions" / sidecar.name).read_bytes() == original_bytes
    assert tmp_path.is_symlink()
    assert cleanup_calls == {
        "artifacts": [],
        "index": [],
        "state": [],
        "attachments": [],
    }


@pytest.mark.parametrize("rollback_outcome", ["retired", "both_missing"])
def test_delete_remains_durable_when_artifact_retirement_rollback_fails(
    rollback_outcome,
    monkeypatch,
    isolated_sessions,
):
    import api.artifacts as artifacts

    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"delete-artifact-rollback-failure-{rollback_outcome}",
    )
    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts")
    artifact_session = registry.root / session.session_id
    artifact_session.mkdir(parents=True)
    (artifact_session / "payload.bin").write_bytes(b"artifact")
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(routes, "_artifact_registry", lambda: registry)
    cleaned = {"index": [], "state": [], "attachments": []}
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleaned["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleaned["state"].append(sid),
    )
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(
        routes.shutil,
        "rmtree",
        lambda path, **_kwargs: cleaned["attachments"].append(str(path)),
    )
    real_atomic_write = artifacts._atomic_write
    real_replace = artifacts.os.replace

    def fail_retired_metadata(path, data):
        if Path(path).name == ".retired.json":
            raise OSError("injected retired metadata failure")
        return real_atomic_write(path, data)

    def fail_artifact_rollback(source, destination, *args, **kwargs):
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path.parent.name == ".trash"
            and destination_path == artifact_session
        ):
            if rollback_outcome == "both_missing":
                real_rmtree(source_path)
            raise OSError("injected artifact rollback failure")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(artifacts, "_atomic_write", fail_retired_metadata)
    monkeypatch.setattr(artifacts.os, "replace", fail_artifact_rollback)

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert response["status"] == 500
    assert response["payload"]["ok"] is False
    assert response["payload"]["code"] == "session_delete_artifact_recovery_failed"
    assert response["payload"]["deleted"] is True
    assert session.path.exists() is False
    assert session.path.with_suffix(".json.bak").exists() is False
    assert session.session_id not in sessions
    assert artifact_session.exists() is False
    expected_retired = 1 if rollback_outcome == "retired" else 0
    assert (
        len(list((registry.root / ".trash").glob(f"{session.session_id}--*")))
        == expected_retired
    )
    assert cleaned["index"] == [session.session_id]
    assert cleaned["state"] == [session.session_id]
    assert len(cleaned["attachments"]) == 1

    # A new process must observe the Session deletion as authoritative. The
    # retained artifact trash is diagnostic/recovery material only; it cannot
    # make the conversation loadable (and therefore cannot be saved again).
    script = """
import sys
from collections import OrderedDict
from pathlib import Path
import api.config as config
import api.models as models

session_dir = Path(sys.argv[1])
session_id = sys.argv[2]
sessions = OrderedDict()
for module in (config, models):
    module.SESSION_DIR = session_dir
    module.SESSION_INDEX_FILE = session_dir / "_index.json"
    module.SESSIONS = sessions
if models.Session.load(session_id) is not None:
    raise SystemExit(73)
if session_id in sessions:
    raise SystemExit(74)
"""
    fresh = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(session.path.parent),
            session.session_id,
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert fresh.returncode == 0, fresh.stderr or fresh.stdout


def test_delete_restores_session_when_artifact_rollback_is_live_but_not_durable(
    monkeypatch,
    isolated_sessions,
):
    import api.artifacts as artifacts

    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-artifact-rollback-durability-failure",
    )
    original_sidecar = session.path.read_bytes()
    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts")
    artifact_session = registry.root / session.session_id
    artifact_session.mkdir(parents=True)
    artifact_payload = artifact_session / "original.bin"
    artifact_payload.write_bytes(b"original artifact")
    original_artifact_identity = (
        int(artifact_session.stat().st_dev),
        int(artifact_session.stat().st_ino),
    )
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_artifact_registry", lambda: registry)
    cleanup_calls = {"index": [], "state": [], "attachments": []}
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleanup_calls["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleanup_calls["state"].append(sid),
    )
    monkeypatch.setattr(
        routes.shutil,
        "rmtree",
        lambda path, **_kwargs: cleanup_calls["attachments"].append(str(path)),
    )
    real_atomic_write = artifacts._atomic_write
    real_fsync_directory = artifacts._fsync_directory
    root_fsync_count = 0

    def fail_retired_metadata(path, data):
        if Path(path).name == ".retired.json":
            raise OSError("injected retired metadata failure")
        return real_atomic_write(path, data)

    def fail_restored_parent_fsync(path):
        nonlocal root_fsync_count
        if Path(path) == registry.root:
            root_fsync_count += 1
            if root_fsync_count >= 2:
                raise OSError("injected persistent artifact rollback fsync failure")
        return real_fsync_directory(path)

    monkeypatch.setattr(artifacts, "_atomic_write", fail_retired_metadata)
    monkeypatch.setattr(artifacts, "_fsync_directory", fail_restored_parent_fsync)

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert root_fsync_count == 2
    assert response["status"] == 500
    assert response["payload"]["ok"] is False
    assert response["payload"]["code"] == "session_delete_recovery_failed"
    assert response["payload"].get("deleted") is not True
    assert session.path.read_bytes() == original_sidecar
    restored_artifact_identity = (
        int(artifact_session.stat().st_dev),
        int(artifact_session.stat().st_ino),
    )
    assert restored_artifact_identity == original_artifact_identity
    assert artifact_payload.read_bytes() == b"original artifact"
    assert list((registry.root / ".trash").glob(f"{session.session_id}--*")) == []
    assert cleanup_calls == {"index": [], "state": [], "attachments": []}
    assert sessions[session.session_id] is session
    assert getattr(session, "_deleted", False) is False


def test_delete_does_not_restore_session_over_rebound_artifact_authority(
    monkeypatch,
    isolated_sessions,
):
    import api.artifacts as artifacts

    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id="delete-artifact-rollback-rebound",
    )
    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts")
    artifact_session = registry.root / session.session_id
    artifact_session.mkdir(parents=True)
    original_payload = artifact_session / "original.bin"
    original_payload.write_bytes(b"original artifact")
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda _sid: {})
    monkeypatch.setattr(routes, "_worktree_retained_payload_for_session_id", lambda _sid: {})
    monkeypatch.setattr(routes, "_artifact_registry", lambda: registry)
    cleaned = {"index": [], "state": [], "attachments": []}
    monkeypatch.setattr(
        routes,
        "prune_session_from_index",
        lambda sid: cleaned["index"].append(sid),
    )
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: cleaned["state"].append(sid),
    )
    monkeypatch.setattr(
        routes.shutil,
        "rmtree",
        lambda path, **_kwargs: cleaned["attachments"].append(str(path)),
    )
    real_atomic_write = artifacts._atomic_write
    real_replace = artifacts.os.replace
    retired_path = None

    def fail_retired_metadata(path, data):
        if Path(path).name == ".retired.json":
            raise OSError("injected retired metadata failure")
        return real_atomic_write(path, data)

    def fail_rollback_after_foreign_directory_appears(
        source,
        destination,
        *args,
        **kwargs,
    ):
        nonlocal retired_path
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path.parent.name == ".trash"
            and destination_path == artifact_session
        ):
            retired_path = source_path
            artifact_session.mkdir()
            (artifact_session / "foreign.bin").write_bytes(b"foreign artifact")
            raise OSError("injected artifact rollback identity conflict")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(artifacts, "_atomic_write", fail_retired_metadata)
    monkeypatch.setattr(
        artifacts.os,
        "replace",
        fail_rollback_after_foreign_directory_appears,
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/delete",
        {"session_id": session.session_id},
    )

    assert response["status"] == 500
    assert response["payload"]["ok"] is False
    assert response["payload"]["code"] == "session_delete_artifact_recovery_failed"
    assert response["payload"]["deleted"] is True
    assert session.path.exists() is False
    assert session.path.with_suffix(".json.bak").exists() is False
    assert session.session_id not in sessions
    assert (artifact_session / "foreign.bin").read_bytes() == b"foreign artifact"
    assert (artifact_session / "original.bin").exists() is False
    assert retired_path is not None
    assert retired_path.parent == registry.root / ".trash"
    assert (retired_path / "original.bin").read_bytes() == b"original artifact"
    assert (retired_path / ".retired.json").exists() is False
    assert cleaned["index"] == [session.session_id]
    assert cleaned["state"] == [session.session_id]
    assert len(cleaned["attachments"]) == 1


def test_artifact_retirement_fsyncs_source_and_trash_parents(
    monkeypatch,
    tmp_path,
):
    import api.artifacts as artifacts

    if artifacts.os.name != "posix":
        pytest.skip("directory fsync durability is POSIX-specific")
    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts")
    session_id = "artifact-retirement-parent-fsync"
    artifact_session = registry.root / session_id
    artifact_session.mkdir(parents=True)
    (artifact_session / "payload.bin").write_bytes(b"artifact")
    synced_identities = []
    real_fsync = artifacts.os.fsync

    def record_fsync(fd):
        metadata = artifacts.os.fstat(fd)
        synced_identities.append((int(metadata.st_dev), int(metadata.st_ino)))
        return real_fsync(fd)

    monkeypatch.setattr(artifacts.os, "fsync", record_fsync)

    retired = registry.retire_session(session_id, now=1234.5)

    assert retired is not None
    root_metadata = registry.root.stat()
    trash_metadata = (registry.root / ".trash").stat()
    assert (int(root_metadata.st_dev), int(root_metadata.st_ino)) in synced_identities
    assert (int(trash_metadata.st_dev), int(trash_metadata.st_ino)) in synced_identities


def test_artifact_retirement_rollback_fsyncs_both_parent_updates(
    monkeypatch,
    tmp_path,
):
    import api.artifacts as artifacts

    if artifacts.os.name != "posix":
        pytest.skip("directory fsync durability is POSIX-specific")
    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts")
    session_id = "artifact-retirement-rollback-parent-fsync"
    artifact_session = registry.root / session_id
    artifact_session.mkdir(parents=True)
    (artifact_session / "payload.bin").write_bytes(b"artifact")
    synced_identities = []
    real_fsync = artifacts.os.fsync

    def record_fsync(fd):
        metadata = artifacts.os.fstat(fd)
        synced_identities.append((int(metadata.st_dev), int(metadata.st_ino)))
        return real_fsync(fd)

    def fail_retired_metadata(path, _data):
        if Path(path).name == ".retired.json":
            raise OSError("injected retired metadata failure")
        raise AssertionError(f"unexpected artifact write: {path}")

    monkeypatch.setattr(artifacts.os, "fsync", record_fsync)
    monkeypatch.setattr(artifacts, "_atomic_write", fail_retired_metadata)

    with pytest.raises(OSError, match="injected retired metadata failure"):
        registry.retire_session(session_id, now=1234.5)

    root_metadata = registry.root.stat()
    trash_metadata = (registry.root / ".trash").stat()
    root_identity = (int(root_metadata.st_dev), int(root_metadata.st_ino))
    trash_identity = (int(trash_metadata.st_dev), int(trash_metadata.st_ino))
    assert synced_identities.count(root_identity) >= 2
    assert synced_identities.count(trash_identity) >= 2
    assert (artifact_session / "payload.bin").read_bytes() == b"artifact"


def test_artifact_restore_fsyncs_move_parents_and_restored_directory(
    monkeypatch,
    tmp_path,
):
    import api.artifacts as artifacts

    if artifacts.os.name != "posix":
        pytest.skip("directory fsync durability is POSIX-specific")
    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts")
    session_id = "artifact-restore-parent-fsync"
    artifact_session = registry.root / session_id
    artifact_session.mkdir(parents=True)
    (artifact_session / "payload.bin").write_bytes(b"artifact")
    retired = registry.retire_session(session_id, now=1234.5)
    assert retired is not None
    synced_identities = []
    real_fsync = artifacts.os.fsync

    def record_fsync(fd):
        metadata = artifacts.os.fstat(fd)
        synced_identities.append((int(metadata.st_dev), int(metadata.st_ino)))
        return real_fsync(fd)

    monkeypatch.setattr(artifacts.os, "fsync", record_fsync)

    registry.restore_session(retired)

    root_metadata = registry.root.stat()
    trash_metadata = (registry.root / ".trash").stat()
    restored_metadata = artifact_session.stat()
    assert (int(root_metadata.st_dev), int(root_metadata.st_ino)) in synced_identities
    assert (int(trash_metadata.st_dev), int(trash_metadata.st_ino)) in synced_identities
    assert (
        int(restored_metadata.st_dev),
        int(restored_metadata.st_ino),
    ) in synced_identities
    assert (artifact_session / "payload.bin").read_bytes() == b"artifact"
    assert (artifact_session / ".retired.json").exists() is False


@pytest.mark.parametrize("path", ["/api/session/clear", "/api/session/truncate"])
def test_destructive_rewrite_rebinds_canonical_session_inside_writer_lock(
    monkeypatch,
    isolated_sessions,
    path,
):
    routes, models, sessions, tmp_path = isolated_sessions
    stale = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"canonical-{path.rsplit('/', 1)[-1]}",
    )
    canonical = copy.deepcopy(stale)
    canonical.messages.append({"role": "assistant", "content": "concurrent"})
    canonical.context_messages.append(
        {"role": "assistant", "content": "concurrent"}
    )
    sessions[stale.session_id] = canonical
    lookups = iter([stale, canonical])
    monkeypatch.setattr(routes, "get_session", lambda _sid: next(lookups))
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: SimpleNamespace(
            retire_session=lambda _sid: [],
            restore_session=lambda _items: None,
        ),
    )
    rewritten = []

    def rewrite(session, mutate, **_kwargs):
        rewritten.append(session)
        mutate()

    monkeypatch.setattr(routes, "_rewrite_existing_session_truth", rewrite)
    body = {"session_id": stale.session_id}
    if path.endswith("truncate"):
        body["keep_count"] = 2

    response = _call_post(monkeypatch, routes, path, body)

    assert response["status"] == 200
    assert rewritten == [canonical]


@pytest.mark.parametrize("path", ["/api/session/clear", "/api/session/truncate"])
def test_destructive_rewrite_rejects_live_stream_without_mutation(
    monkeypatch,
    isolated_sessions,
    path,
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"live-{path.rsplit('/', 1)[-1]}",
    )
    before_sidecar = session.path.read_bytes()
    before_runtime = {
        "messages": copy.deepcopy(session.messages),
        "context_messages": copy.deepcopy(session.context_messages),
        "active_stream_id": session.active_stream_id,
        "pending_user_message": session.pending_user_message,
        "pending_attachments": copy.deepcopy(session.pending_attachments),
        "pending_started_at": session.pending_started_at,
    }
    monkeypatch.setattr(routes, "_active_stream_id_set", lambda: {"stream-running"})
    monkeypatch.setattr(
        routes,
        "_artifact_registry",
        lambda: (_ for _ in ()).throw(
            AssertionError("live stream must be rejected before artifact retirement")
        ),
    )
    monkeypatch.setattr(
        routes,
        "_rewrite_existing_session_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("live stream must not rewrite semantic truth")
        ),
    )
    body = {"session_id": session.session_id}
    if path.endswith("truncate"):
        body["keep_count"] = 2

    response = _call_post(monkeypatch, routes, path, body)

    assert response["status"] == 409
    assert response["payload"]["error"] == "session has an active stream; cancel it before rewriting"
    assert {
        "messages": session.messages,
        "context_messages": session.context_messages,
        "active_stream_id": session.active_stream_id,
        "pending_user_message": session.pending_user_message,
        "pending_attachments": session.pending_attachments,
        "pending_started_at": session.pending_started_at,
    } == before_runtime
    assert session.path.read_bytes() == before_sidecar


def test_strict_state_rewrite_surfaces_existing_database_failure(monkeypatch):
    import api.state_sync as state_sync

    monkeypatch.setattr(
        state_sync,
        "_get_state_db",
        lambda profile=None, strict=False, create_if_missing=False: (
            _ for _ in ()
        ).throw(RuntimeError("locked")),
    )

    with pytest.raises(RuntimeError, match="locked"):
        state_sync.replace_webui_session_messages(
            session_id="s1",
            messages=[],
            profile="default",
            model="test-model",
        )


def test_strict_state_db_missing_is_not_a_silent_skip(monkeypatch, tmp_path):
    import api.profiles as profiles
    import api.state_sync as state_sync

    missing_home = tmp_path / "missing-profile-home"
    monkeypatch.setattr(
        profiles,
        "_resolve_profile_home_for_name",
        lambda _profile: missing_home,
    )

    with pytest.raises(RuntimeError, match="does not exist"):
        state_sync._get_state_db(profile="maiko", strict=True)


def test_user_turn_checkpoint_failure_is_not_swallowed(monkeypatch):
    import api.state_sync as state_sync

    class FailingDB:
        def ensure_session(self, **_kwargs):
            return None

        def append_message(self, **_kwargs):
            raise RuntimeError("disk full")

        def close(self):
            return None

    monkeypatch.setattr(
        state_sync,
        "_get_state_db",
        lambda profile=None, strict=False, create_if_missing=False: FailingDB(),
    )

    with pytest.raises(RuntimeError, match="checkpoint"):
        state_sync.sync_webui_user_turn(
            session_id="strict-user-turn",
            content="must persist",
            turn_id="turn-strict",
            profile="maiko",
        )


def test_strict_rewrite_creates_first_install_profile_database(monkeypatch, tmp_path):
    import api.profiles as profiles
    import api.state_sync as state_sync
    from hermes_state import SessionDB

    profile_home = tmp_path / "new-profile-home"
    monkeypatch.setattr(
        profiles,
        "_resolve_profile_home_for_name",
        lambda _profile: profile_home,
    )

    assert state_sync.replace_webui_session_messages(
        session_id="first-install-session",
        messages=[{"role": "user", "content": "created durably"}],
        profile="maiko",
        model="test-model",
    ) is True

    assert (profile_home / "state.db").exists()
    db = SessionDB(profile_home / "state.db")
    try:
        assert [row["content"] for row in db.get_messages("first-install-session")] == [
            "created durably"
        ]
    finally:
        db.close()

    with sqlite3.connect(profile_home / "state.db") as conn:
        index_names = {
            row[1] for row in conn.execute("PRAGMA index_list('sessions')").fetchall()
        }
    assert "idx_sessions_title_unique" in index_names


def test_clear_resets_all_runtime_state_and_state_db_truth(monkeypatch, isolated_sessions):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(models, sessions, tmp_path)
    calls = []
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    response = _call_post(
        monkeypatch, routes, "/api/session/clear", {"session_id": session.session_id}
    )

    assert response["status"] == 200
    loaded = models.Session.load(session.session_id)
    assert loaded.messages == []
    assert loaded.context_messages == []
    assert loaded.tool_calls == []
    assert loaded.active_stream_id is None
    assert loaded.pending_user_message is None
    assert loaded.pending_attachments == []
    assert loaded.pending_started_at is None
    assert loaded.compression_anchor_visible_idx is None
    assert loaded.compression_anchor_message_key is None
    assert loaded.compression_anchor_summary is None
    assert loaded.compression_anchor_engine is None
    assert loaded.compression_anchor_mode is None
    assert loaded.compression_anchor_details == {}
    assert loaded.context_engine_state == {}
    assert loaded.gateway_routing is None
    assert loaded.gateway_routing_history == []
    assert loaded.privacy_context is None
    assert calls == [
        {
            "session_id": session.session_id,
            "messages": [],
            "model": session.model,
            "profile": "default",
        }
    ]


def test_clear_state_db_failure_is_visible_and_sidecar_is_rolled_back(
    monkeypatch, isolated_sessions
):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(models, sessions, tmp_path, session_id="phase2-clear-rollback")
    before = {
        "messages": copy.deepcopy(session.messages),
        "context_messages": copy.deepcopy(session.context_messages),
    }

    def fail(**_kwargs):
        raise RuntimeError("state db locked")

    monkeypatch.setattr("api.state_sync.replace_webui_session_messages", fail)
    response = _call_post(
        monkeypatch, routes, "/api/session/clear", {"session_id": session.session_id}
    )

    assert response["status"] == 500
    assert "state" in response["payload"]["error"].lower()
    loaded = models.Session.load(session.session_id)
    assert loaded.messages == before["messages"]
    assert loaded.context_messages == before["context_messages"]
    assert loaded.pending_user_message == "unfinished"


def test_truncate_rebuilds_context_and_replaces_state_db_prefix(monkeypatch, isolated_sessions):
    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(models, sessions, tmp_path, session_id="phase2-truncate")
    calls = []
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/truncate",
        {"session_id": session.session_id, "keep_count": 2},
    )

    assert response["status"] == 200
    loaded = models.Session.load(session.session_id)
    assert [m["content"] for m in loaded.messages] == ["first", "reply first"]
    assert [m["content"] for m in loaded.context_messages] == ["first", "reply first"]
    assert loaded.active_stream_id is None
    assert loaded.pending_user_message is None
    assert loaded.context_engine_state == {}
    assert calls[0]["messages"] == loaded.context_messages


@pytest.mark.parametrize(
    ("path", "body_factory"),
    [
        ("/api/session/clear", lambda sid: {"session_id": sid}),
        ("/api/session/truncate", lambda sid: {"session_id": sid, "keep_count": 2}),
    ],
)
def test_existing_transcript_rewrite_evicts_only_target_agent(
    monkeypatch, isolated_sessions, path, body_factory
):
    import api.config as config

    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"phase2-endpoint-cache-{path.rsplit('/', 1)[-1]}",
    )
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: True,
    )
    evictions = []

    def evict(session_id):
        evictions.append(session_id)
        config.SESSION_AGENT_CACHE.pop(session_id, None)

    monkeypatch.setattr(config, "_evict_session_agent", evict)
    config.SESSION_AGENT_CACHE[session.session_id] = (object(), ("target",))
    config.SESSION_AGENT_CACHE["same-name-other-profile-sentinel"] = (
        object(),
        ("other-profile",),
    )
    try:
        response = _call_post(monkeypatch, routes, path, body_factory(session.session_id))
        assert response["status"] == 200
        assert evictions == [session.session_id]
        assert session.session_id not in config.SESSION_AGENT_CACHE
        assert "same-name-other-profile-sentinel" in config.SESSION_AGENT_CACHE
    finally:
        config.SESSION_AGENT_CACHE.pop(session.session_id, None)
        config.SESSION_AGENT_CACHE.pop("same-name-other-profile-sentinel", None)


def test_branch_uses_retained_prefix_and_does_not_inherit_unfinished_run(
    monkeypatch, isolated_sessions
):
    import api.config as config

    routes, models, sessions, tmp_path = isolated_sessions
    source = _seed_session(models, sessions, tmp_path, session_id="phase2-branch-source")
    calls = []
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    evictions = []
    monkeypatch.setattr(config, "_evict_session_agent", lambda sid: evictions.append(sid))

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/branch",
        {"session_id": source.session_id, "keep_count": 2},
    )

    assert response["status"] == 200
    branch = models.Session.load(response["payload"]["session_id"])
    assert [m["content"] for m in branch.messages] == ["first", "reply first"]
    assert [m["content"] for m in branch.context_messages] == ["first", "reply first"]
    assert branch.active_stream_id is None
    assert branch.pending_user_message is None
    assert branch.pending_attachments == []
    assert branch.pending_started_at is None
    assert branch.context_engine_state == {}
    assert branch.gateway_routing is None
    assert branch.gateway_routing_history == []
    assert calls[0]["session_id"] == branch.session_id
    assert calls[0]["messages"] == branch.context_messages
    assert evictions == []


def test_duplicate_and_import_checkpoint_only_completed_messages(monkeypatch, isolated_sessions):
    import api.config as config

    routes, models, sessions, tmp_path = isolated_sessions
    source = _seed_session(models, sessions, tmp_path, session_id="phase2-duplicate-source")
    source.messages.append({"role": "assistant", "content": "", "_partial": True})
    source.context_messages = [
        {"role": "system", "content": "obsolete wrapper"},
        {"role": "user", "content": "stale context that must not be copied"},
    ]
    source.save(skip_index=True)
    calls = []
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda _path: tmp_path)
    evictions = []
    monkeypatch.setattr(config, "_evict_session_agent", lambda sid: evictions.append(sid))

    duplicated = _call_post(
        monkeypatch,
        routes,
        "/api/session/duplicate",
        {"session_id": source.session_id},
    )
    duplicate_id = duplicated["payload"]["session"]["session_id"]
    duplicate = models.Session.load(duplicate_id)
    expected_completed = [
        {"role": "user", "content": "first", "platform_message_id": "webui-turn:t1"},
        {"role": "assistant", "content": "reply first"},
        {"role": "user", "content": "second", "platform_message_id": "webui-turn:t2"},
        {"role": "assistant", "content": "reply second"},
    ]
    assert duplicate.context_messages == expected_completed
    assert duplicate.active_stream_id is None
    assert duplicate.pending_user_message is None
    assert duplicate.gateway_routing is None
    assert duplicate.gateway_routing_history == []
    assert duplicate.context_engine_state == {}

    imported = _call_post(
        monkeypatch,
        routes,
        "/api/session/import",
        {
            "messages": [{"role": "user", "content": "portable"}],
            "active_stream_id": "forged",
            "pending_user_message": "forged pending",
            "context_engine_state": {"in_flight": True},
            "gateway_routing": {"run_id": "forged"},
        },
    )
    imported_session = models.Session.load(imported["payload"]["session"]["session_id"])
    assert imported_session.active_stream_id is None
    assert imported_session.pending_user_message is None
    assert imported_session.context_engine_state == {}
    assert imported_session.gateway_routing is None

    assert [call["session_id"] for call in calls] == [duplicate_id, imported_session.session_id]
    assert calls[0]["messages"] == expected_completed
    assert calls[1]["messages"] == [{"role": "user", "content": "portable"}]
    assert evictions == []


@pytest.mark.parametrize("operation", ["retry_last", "undo_last"])
def test_retry_and_undo_replace_state_db_with_retained_prefix(
    monkeypatch, isolated_sessions, operation
):
    import api.session_ops as session_ops

    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models, sessions, tmp_path, session_id=f"phase2-{operation.replace('_', '-')}"
    )
    session.active_stream_id = None
    session.pending_user_message = None
    session.pending_attachments = []
    session.pending_started_at = None
    session.context_messages = copy.deepcopy(session.messages)
    session.save(skip_index=True)
    calls = []
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    getattr(session_ops, operation)(session.session_id)

    loaded = models.Session.load(session.session_id)
    assert [message["content"] for message in loaded.messages] == [
        "first",
        "reply first",
    ]
    assert [message["content"] for message in loaded.context_messages] == [
        "first",
        "reply first",
    ]
    assert calls[0]["messages"] == loaded.context_messages


@pytest.mark.parametrize("operation", ["retry_last", "undo_last"])
def test_retry_and_undo_process_death_leave_recoverable_truth_intent(
    monkeypatch,
    isolated_sessions,
    operation,
):
    import api.routes as routes
    import api.session_ops as session_ops
    import api.truth_rewrite as truth_rewrite

    _routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"phase2-{operation}-crash",
    )
    before = copy.deepcopy(session.messages)
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("crash between sidecar and state.db")
        ),
    )

    with pytest.raises(SystemExit, match="between sidecar and state.db"):
        getattr(session_ops, operation)(session.session_id)

    marker = truth_rewrite.truth_rewrite_intent_path(session)
    assert marker.exists()
    disk = json.loads(session.path.read_text(encoding="utf-8"))
    assert disk["messages"] == before[:2]

    replaced = []
    monkeypatch.setattr(
        truth_rewrite,
        "_default_read_state_messages",
        lambda _session: copy.deepcopy(before),
    )
    monkeypatch.setattr(
        truth_rewrite,
        "_default_replace_state_messages",
        lambda _session, messages: replaced.append(copy.deepcopy(messages)) or True,
    )
    loaded = models.Session.load(session.session_id)
    assert loaded.messages == before[:2]
    assert replaced == [before[:2]]
    assert marker.exists() is False


def test_retry_state_db_failure_restores_sidecar_and_surfaces_error(
    monkeypatch, isolated_sessions
):
    import api.session_ops as session_ops

    routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(models, sessions, tmp_path, session_id="phase2-retry-rollback")
    session.active_stream_id = None
    session.pending_user_message = None
    session.pending_attachments = []
    session.pending_started_at = None
    session.save(skip_index=True)
    before = copy.deepcopy(session.messages)

    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("state db locked")),
    )

    with pytest.raises(RuntimeError, match="state db locked"):
        session_ops.retry_last(session.session_id)

    assert models.Session.load(session.session_id).messages == before


def test_retry_unconfirmed_state_rewrite_restores_sidecar_and_surfaces_error(
    monkeypatch, isolated_sessions
):
    import api.session_ops as session_ops

    _routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(models, sessions, tmp_path, session_id="phase2-retry-false")
    before = copy.deepcopy(session.messages)
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: False,
    )

    with pytest.raises(RuntimeError, match="not confirmed"):
        session_ops.retry_last(session.session_id)

    assert models.Session.load(session.session_id).messages == before


@pytest.mark.parametrize(
    "path",
    [
        "/api/session/clear",
        "/api/session/truncate",
        "/api/session/branch",
        "/api/session/duplicate",
        "/api/session/import",
    ],
)
def test_session_mutation_does_not_publish_when_state_rewrite_is_unconfirmed(
    monkeypatch, isolated_sessions, path
):
    routes, models, sessions, tmp_path = isolated_sessions
    source = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"phase2-unconfirmed-{path.rsplit('/', 1)[-1]}",
    )
    before_ids = set(sessions)
    before_messages = copy.deepcopy(source.messages)
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda _path: tmp_path)
    if path == "/api/session/truncate":
        body = {"session_id": source.session_id, "keep_count": 2}
    elif path == "/api/session/branch":
        body = {"session_id": source.session_id, "keep_count": 2}
    elif path == "/api/session/duplicate":
        body = {"session_id": source.session_id}
    elif path == "/api/session/import":
        body = {"messages": [{"role": "user", "content": "portable"}]}
    else:
        body = {"session_id": source.session_id}

    response = _call_post(monkeypatch, routes, path, body)

    assert response["status"] == 500
    assert set(sessions) == before_ids
    assert models.Session.load(source.session_id).messages == before_messages


@pytest.mark.parametrize("operation", ["retry_last", "undo_last"])
def test_transcript_rewrite_evicts_cached_agent(
    monkeypatch, isolated_sessions, operation
):
    import api.config as config
    import api.session_ops as session_ops

    _routes, models, sessions, tmp_path = isolated_sessions
    session = _seed_session(
        models,
        sessions,
        tmp_path,
        session_id=f"phase2-cache-reset-{operation}",
    )
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: True,
    )
    evictions = []

    def evict(session_id):
        evictions.append(session_id)
        config.SESSION_AGENT_CACHE.pop(session_id, None)

    monkeypatch.setattr(config, "_evict_session_agent", evict)
    config.SESSION_AGENT_CACHE[session.session_id] = (object(), ("stale",))
    try:
        getattr(session_ops, operation)(session.session_id)
        assert session.session_id not in config.SESSION_AGENT_CACHE
        assert evictions == [session.session_id]
    finally:
        config.SESSION_AGENT_CACHE.pop(session.session_id, None)


def test_json_import_binds_current_profile_and_checkpoints_that_profile(
    monkeypatch, isolated_sessions
):
    import api.profiles as profiles

    routes, models, _sessions, tmp_path = isolated_sessions
    calls = []
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "maiko")
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda _path: tmp_path)
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/import",
        {
            "title": "Portable",
            "messages": [{"role": "user", "content": "profile-bound"}],
            "profile": "forged-other-profile",
        },
    )

    assert response["status"] == 200
    imported = models.Session.load(response["payload"]["session"]["session_id"])
    assert imported.profile == "maiko"
    assert calls == [
        {
            "session_id": imported.session_id,
            "messages": [{"role": "user", "content": "profile-bound"}],
            "model": imported.model,
            "profile": "maiko",
        }
    ]


def test_json_import_remains_bound_after_active_profile_switch(
    monkeypatch, isolated_sessions
):
    import api.models as models_api
    import api.profiles as profiles
    from hermes_state import SessionDB

    routes, models, _sessions, tmp_path = isolated_sessions
    homes = {
        "hiyuki": tmp_path / "profiles" / "hiyuki",
        "maiko": tmp_path / "profiles" / "maiko",
    }
    for home in homes.values():
        home.mkdir(parents=True)
        SessionDB(home / "state.db").close()
    active = {"name": "maiko"}
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: active["name"])
    monkeypatch.setattr(
        profiles,
        "_resolve_profile_home_for_name",
        lambda profile: homes[profile],
    )
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda profile: homes[profile],
    )
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda _path: tmp_path)

    response = _call_post(
        monkeypatch,
        routes,
        "/api/session/import",
        {"messages": [{"role": "user", "content": "maiko portable"}]},
    )
    assert response["status"] == 200
    imported = models.Session.load(response["payload"]["session"]["session_id"])
    assert imported.profile == "maiko"

    active["name"] = "hiyuki"
    recovered = models_api.reconciled_state_db_messages_for_session(imported)
    assert [message["content"] for message in recovered] == ["maiko portable"]
    maiko_db = SessionDB(homes["maiko"] / "state.db")
    hiyuki_db = SessionDB(homes["hiyuki"] / "state.db")
    try:
        assert maiko_db.get_session(imported.session_id) is not None
        assert hiyuki_db.get_session(imported.session_id) is None
    finally:
        maiko_db.close()
        hiyuki_db.close()
