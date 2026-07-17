from __future__ import annotations

import json
import copy
import sqlite3
import subprocess
import sys
from collections import OrderedDict
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
    cancel_flag = threading.Event()
    monkeypatch.setattr(routes, "STREAMS", {"delete-live-stream": object()})
    monkeypatch.setattr(routes, "CANCEL_FLAGS", {"delete-live-stream": cancel_flag})
    monkeypatch.setattr(config, "AGENT_INSTANCES", {})
    deleted_state_rows = []
    monkeypatch.setattr(
        models,
        "delete_cli_session",
        lambda sid: deleted_state_rows.append(sid) or True,
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
    assert session.path.exists() is False


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
