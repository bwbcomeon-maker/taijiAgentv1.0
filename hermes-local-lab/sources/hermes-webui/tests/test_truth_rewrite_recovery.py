import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _session(tmp_path: Path, messages):
    path = tmp_path / "sessions" / "truth-rewrite-session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    session = SimpleNamespace(
        session_id="truth-rewrite-session",
        path=path,
        messages=list(messages),
        context_messages=list(messages),
        title="Truth rewrite",
        model="test-model",
        profile=None,
    )

    def save(**_kwargs):
        path.write_text(
            json.dumps(
                {
                    "session_id": session.session_id,
                    "messages": session.messages,
                    "context_messages": session.context_messages,
                    "title": session.title,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    session.save = save
    session.save()
    return session


def _disk_messages(session):
    return json.loads(session.path.read_text(encoding="utf-8"))["messages"]


def test_system_exit_after_sidecar_leaves_intent_and_recovery_rolls_forward(
    tmp_path,
    monkeypatch,
):
    import api.routes as routes
    import api.truth_rewrite as truth_rewrite

    before = [{"role": "user", "content": "before"}]
    target = before + [{"role": "assistant", "content": "target"}]
    session = _session(tmp_path, before)
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("crash between stores")
        ),
    )

    with pytest.raises(SystemExit, match="between stores"):
        routes._rewrite_existing_session_truth(
            session,
            lambda: setattr(session, "messages", list(target)),
            privacy_reason=None,
        )

    marker = truth_rewrite.truth_rewrite_intent_path(session)
    assert marker.exists()
    assert _disk_messages(session) == target
    replaced = []
    import api.models as models

    monkeypatch.setattr(models, "SESSION_DIR", session.path.parent)
    monkeypatch.setattr(
        models,
        "SESSION_INDEX_FILE",
        session.path.parent / "_index.json",
    )
    monkeypatch.setattr(
        truth_rewrite,
        "_default_read_state_messages",
        lambda _session: list(before),
    )
    monkeypatch.setattr(
        truth_rewrite,
        "_default_replace_state_messages",
        lambda _session, messages: replaced.append(list(messages)) or True,
    )
    outcomes = truth_rewrite.recover_orphan_truth_rewrite_intents(session.path.parent)
    assert outcomes == [
        {
            "kind": "truth_rewrite_crash_recovery",
            "status": "existing_recovered",
            "session_id": session.session_id,
        }
    ]
    loaded = models.Session.load(session.session_id)
    assert loaded is not None
    assert loaded.messages == target
    assert replaced == [target]
    assert marker.exists() is False


def test_crash_after_db_commit_before_marker_clear_recovers_idempotently(
    tmp_path,
    monkeypatch,
):
    import api.routes as routes
    import api.truth_rewrite as truth_rewrite

    before = [{"role": "user", "content": "before"}]
    target = before + [{"role": "assistant", "content": "target"}]
    session = _session(tmp_path, before)
    state_messages = list(before)

    def replace(_session, messages):
        nonlocal state_messages
        state_messages = list(messages)
        return True

    monkeypatch.setattr(routes, "_replace_state_db_truth", replace)
    monkeypatch.setattr(
        truth_rewrite,
        "clear_truth_rewrite_intent",
        lambda _session: (_ for _ in ()).throw(SystemExit("crash before clear")),
    )
    with pytest.raises(SystemExit, match="before clear"):
        routes._rewrite_existing_session_truth(
            session,
            lambda: setattr(session, "messages", list(target)),
            privacy_reason=None,
        )

    marker = truth_rewrite.truth_rewrite_intent_path(session)
    assert marker.exists()
    monkeypatch.undo()
    replaced = []
    outcome = truth_rewrite.recover_truth_rewrite_intent(
        session,
        read_state_messages=lambda _session: list(state_messages),
        replace_state_messages=lambda _session, messages: replaced.append(list(messages)) or True,
    )
    assert outcome["status"] == "completed"
    assert replaced == []
    assert marker.exists() is False


@pytest.mark.parametrize(
    ("commit_before_crash", "expected_status", "expected_replacements"),
    [
        (False, "rolled_forward", 2),
        (True, "completed", 1),
    ],
)
def test_recovery_can_crash_again_and_second_recovery_is_idempotent(
    commit_before_crash,
    expected_status,
    expected_replacements,
    tmp_path,
):
    import api.truth_rewrite as truth_rewrite

    before = [{"role": "user", "content": "before"}]
    target = before + [{"role": "assistant", "content": "target"}]
    session = _session(tmp_path, before)
    truth_rewrite.write_truth_rewrite_intent(session, before, target)
    session.messages = list(target)
    session.context_messages = list(target)
    session.save()
    state_messages = list(before)
    replacements = []

    def crashing_replace(_session, messages):
        nonlocal state_messages
        replacements.append(list(messages))
        if commit_before_crash:
            state_messages = list(messages)
        raise SystemExit("recovery process died")

    with pytest.raises(SystemExit, match="recovery process died"):
        truth_rewrite.recover_truth_rewrite_intent(
            session,
            read_state_messages=lambda _session: list(state_messages),
            replace_state_messages=crashing_replace,
        )

    marker = truth_rewrite.truth_rewrite_intent_path(session)
    assert marker.exists()

    def successful_replace(_session, messages):
        nonlocal state_messages
        replacements.append(list(messages))
        state_messages = list(messages)
        return True

    outcome = truth_rewrite.recover_truth_rewrite_intent(
        session,
        read_state_messages=lambda _session: list(state_messages),
        replace_state_messages=successful_replace,
    )
    assert outcome["status"] == expected_status
    assert len(replacements) == expected_replacements
    assert state_messages == target
    assert marker.exists() is False


def test_startup_scan_clears_new_session_orphan_intent_before_sidecar(
    tmp_path,
):
    import api.truth_rewrite as truth_rewrite

    target = [{"role": "user", "content": "not stored in marker"}]
    session = _session(tmp_path, target)
    session.profile = "research"
    session.path.unlink()
    truth_rewrite.write_truth_rewrite_intent(session, [], target)
    marker = truth_rewrite.truth_rewrite_intent_path(session)
    seen_profiles = []

    outcomes = truth_rewrite.recover_orphan_truth_rewrite_intents(
        session.path.parent,
        read_state_messages=lambda candidate: seen_profiles.append(candidate.profile) or [],
    )

    assert outcomes == [
        {
            "kind": "truth_rewrite_crash_recovery",
            "status": "orphan_aborted",
            "session_id": session.session_id,
        }
    ]
    assert seen_profiles == ["research"]
    assert marker.exists() is False


def test_startup_scan_keeps_orphan_intent_when_state_is_not_before_hash(
    tmp_path,
):
    import api.truth_rewrite as truth_rewrite

    target = [{"role": "user", "content": "target"}]
    session = _session(tmp_path, target)
    session.path.unlink()
    truth_rewrite.write_truth_rewrite_intent(session, [], target)
    marker = truth_rewrite.truth_rewrite_intent_path(session)

    outcomes = truth_rewrite.recover_orphan_truth_rewrite_intents(
        session.path.parent,
        read_state_messages=lambda _session: list(target),
    )

    assert outcomes == [
        {
            "kind": "truth_rewrite_crash_recovery",
            "status": "diverged",
            "reason": "sidecar_missing_state_not_before",
            "session_id": session.session_id,
        }
    ]
    assert marker.exists()


def test_startup_scan_fails_closed_when_intent_directory_is_unreadable(
    tmp_path,
    monkeypatch,
):
    import api.truth_rewrite as truth_rewrite

    intent_dir = tmp_path / "sessions" / ".truth-rewrite-intents"
    original_glob = Path.glob

    def denied_glob(path, pattern):
        if path == intent_dir:
            raise OSError("permission denied secret-path-canary")
        return original_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", denied_glob)
    with pytest.raises(
        truth_rewrite.TruthRewriteRecoveryError,
        match="intent directory is unreadable",
    ) as caught:
        truth_rewrite.recover_orphan_truth_rewrite_intents(intent_dir.parent)
    assert "secret-path-canary" not in str(caught.value)


def test_server_startup_invokes_orphan_intent_scan_before_serving(
    tmp_path,
    monkeypatch,
    capsys,
):
    import api.truth_rewrite as truth_rewrite
    import server

    seen = []
    monkeypatch.setattr(server, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(
        truth_rewrite,
        "recover_orphan_truth_rewrite_intents",
        lambda session_dir: seen.append(session_dir)
        or [
            {
                "status": "orphan_aborted",
                "session_id": "new-session",
            }
        ],
    )

    server._recover_orphan_truth_rewrites_on_startup()

    assert seen == [server.SESSION_DIR]
    assert "Resolved 1 session rewrite intents" in capsys.readouterr().out


def _patch_server_main_until_http_constructor(monkeypatch, tmp_path, server):
    """Keep ``server.main`` real through recovery, then stop at HTTP creation."""
    import api.artifacts as artifacts
    import api.auth as auth
    import api.config as config
    import api.gateway_watcher as gateway_watcher
    import api.legacy_session_migration as legacy_migration
    import api.models as models
    import api.plugins as plugins
    import api.session_recovery as session_recovery

    monkeypatch.setattr(server, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(server, "SESSION_DIR", tmp_path / "state" / "sessions")
    monkeypatch.setattr(server, "DEFAULT_WORKSPACE", tmp_path / "workspace")
    monkeypatch.setattr(server, "_truthy_env", lambda _name: False)
    monkeypatch.setattr(server, "_raise_fd_soft_limit", lambda: {"status": "unchanged"})
    monkeypatch.setattr(server, "fix_credential_permissions", lambda: None)
    monkeypatch.setattr(config, "verify_hermes_imports", lambda: (True, [], {}))
    monkeypatch.setattr(config, "_HERMES_FOUND", False)
    monkeypatch.setattr(models, "_active_state_db_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(
        session_recovery,
        "recover_all_sessions_on_startup",
        lambda *_args, **_kwargs: {"restored": 0, "scanned": 0},
    )
    monkeypatch.setattr(artifacts, "ArtifactRegistry", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        legacy_migration,
        "audit_legacy_sessions",
        lambda *_args, **_kwargs: {"needs_repair": False, "scanned": 0},
    )
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(gateway_watcher, "start_watcher", lambda: None)
    monkeypatch.setattr(plugins, "load_plugins", lambda: None)


@pytest.mark.parametrize(
    "blocked_status",
    ["diverged", "invalid", "error"],
)
def test_server_startup_blocks_http_for_unresolved_truth_rewrite_outcome(
    blocked_status,
    tmp_path,
    monkeypatch,
    capsys,
):
    import api.truth_rewrite as truth_rewrite
    import server

    _patch_server_main_until_http_constructor(monkeypatch, tmp_path, server)
    monkeypatch.setattr(
        truth_rewrite,
        "recover_orphan_truth_rewrite_intents",
        lambda _session_dir: [
            {
                "status": blocked_status,
                "reason": "sensitive-reason-must-not-be-printed",
                "session_id": "sensitive-session-must-not-be-printed",
            }
        ],
    )
    http_started = []
    monkeypatch.setattr(
        server,
        "QuietHTTPServer",
        lambda *_args, **_kwargs: http_started.append(True),
    )

    with pytest.raises(RuntimeError, match="truth_rewrite_recovery_blocked"):
        server.main()

    assert http_started == []
    output = capsys.readouterr().out
    assert "sensitive-reason-must-not-be-printed" not in output
    assert "sensitive-session-must-not-be-printed" not in output


def test_server_startup_blocks_http_when_truth_rewrite_scan_raises(
    tmp_path,
    monkeypatch,
    capsys,
):
    import api.truth_rewrite as truth_rewrite
    import server

    _patch_server_main_until_http_constructor(monkeypatch, tmp_path, server)
    monkeypatch.setattr(
        truth_rewrite,
        "recover_orphan_truth_rewrite_intents",
        lambda _session_dir: (_ for _ in ()).throw(
            RuntimeError("/private/sensitive-path raw-message-canary")
        ),
    )
    http_started = []
    monkeypatch.setattr(
        server,
        "QuietHTTPServer",
        lambda *_args, **_kwargs: http_started.append(True),
    )

    with pytest.raises(RuntimeError, match="truth_rewrite_recovery_failed"):
        server.main()

    assert http_started == []
    output = capsys.readouterr().out
    assert "/private/sensitive-path" not in output
    assert "raw-message-canary" not in output


@pytest.mark.parametrize(
    "outcomes",
    [
        [],
        [{"status": "orphan_aborted", "session_id": "safe-session"}],
    ],
)
def test_server_startup_clean_or_recovered_truth_state_reaches_http_constructor(
    outcomes,
    tmp_path,
    monkeypatch,
):
    import api.truth_rewrite as truth_rewrite
    import server

    _patch_server_main_until_http_constructor(monkeypatch, tmp_path, server)
    monkeypatch.setattr(
        truth_rewrite,
        "recover_orphan_truth_rewrite_intents",
        lambda _session_dir: list(outcomes),
    )
    http_started = []

    class _HTTPConstructorReached(RuntimeError):
        pass

    def stop_at_http_constructor(*args, **kwargs):
        http_started.append((args, kwargs))
        raise _HTTPConstructorReached

    monkeypatch.setattr(server, "QuietHTTPServer", stop_at_http_constructor)

    with pytest.raises(_HTTPConstructorReached):
        server.main()

    assert len(http_started) == 1


def test_new_session_crash_after_sidecar_recovers_by_same_intent_protocol(
    tmp_path,
    monkeypatch,
):
    import api.routes as routes
    import api.truth_rewrite as truth_rewrite

    target = [
        {"role": "user", "content": "new session question"},
        {"role": "assistant", "content": "new session answer"},
    ]
    session = _session(tmp_path, target)
    session.path.unlink()
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("new session crash between stores")
        ),
    )

    with pytest.raises(SystemExit, match="new session crash"):
        routes._persist_new_session_truth(session)

    marker = truth_rewrite.truth_rewrite_intent_path(session)
    assert session.path.exists()
    assert marker.exists()
    replaced = []
    outcome = truth_rewrite.recover_truth_rewrite_intent(
        session,
        read_state_messages=lambda _session: [],
        replace_state_messages=lambda _session, messages: replaced.append(list(messages)) or True,
    )
    assert outcome["status"] == "rolled_forward"
    assert replaced == [target]
    assert marker.exists() is False


def test_new_session_recovery_repairs_existing_sidebar_index(tmp_path, monkeypatch):
    import api.models as models
    import api.routes as routes
    import api.truth_rewrite as truth_rewrite

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_path = session_dir / "_index.json"
    index_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_path)
    monkeypatch.setattr(models, "SESSIONS", {})

    target = [
        {"role": "user", "content": "new session question"},
        {"role": "assistant", "content": "new session answer"},
    ]
    session = models.Session(
        session_id="new-session-index-recovery",
        title="Recovered conversation",
        workspace=str(tmp_path),
        messages=target,
        context_messages=target,
    )
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("new session crash before index")
        ),
    )

    with pytest.raises(SystemExit, match="before index"):
        routes._persist_new_session_truth(session)

    assert json.loads(index_path.read_text(encoding="utf-8")) == []
    replaced = []
    monkeypatch.setattr(
        truth_rewrite,
        "_default_read_state_messages",
        lambda _session: [],
    )
    monkeypatch.setattr(
        truth_rewrite,
        "_default_replace_state_messages",
        lambda _session, messages: replaced.append(list(messages)) or True,
    )

    outcomes = truth_rewrite.recover_orphan_truth_rewrite_intents(session_dir)
    assert outcomes == [
        {
            "kind": "truth_rewrite_crash_recovery",
            "status": "existing_recovered",
            "session_id": session.session_id,
        }
    ]
    loaded = models.Session.load(session.session_id)
    assert loaded is not None
    assert loaded.messages == target
    assert replaced == [target]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert [row["session_id"] for row in index] == [session.session_id]
    assert truth_rewrite.truth_rewrite_intent_path(session).exists() is False


def test_startup_rebuild_repairs_index_if_recovery_crashes_after_marker_clear(
    tmp_path,
    monkeypatch,
):
    """A second crash during index publication must remain recoverable.

    The transcript stores may already agree and the rewrite marker may already
    be gone when the process dies while publishing the rebuildable sidebar
    index.  The next startup must reconcile the index from durable sidecars
    even when an existing (but stale) index file is present.
    """
    import api.models as models
    import api.routes as routes
    import api.session_recovery as session_recovery
    import api.truth_rewrite as truth_rewrite

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_path = session_dir / "_index.json"
    index_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_path)
    monkeypatch.setattr(models, "SESSIONS", {})

    target = [
        {"role": "user", "content": "new session question"},
        {"role": "assistant", "content": "new session answer"},
    ]
    session = models.Session(
        session_id="new-session-second-crash",
        title="Recovered after second crash",
        workspace=str(tmp_path),
        messages=target,
        context_messages=target,
    )
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("first crash before state commit")
        ),
    )
    with pytest.raises(SystemExit, match="first crash"):
        routes._persist_new_session_truth(session)

    state_messages = []

    def replace_state(_session, messages):
        nonlocal state_messages
        state_messages = list(messages)
        return True

    monkeypatch.setattr(
        truth_rewrite,
        "_default_read_state_messages",
        lambda _session: list(state_messages),
    )
    monkeypatch.setattr(
        truth_rewrite,
        "_default_replace_state_messages",
        replace_state,
    )
    original_write_index = models._write_session_index
    monkeypatch.setattr(
        models,
        "_write_session_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("second crash during index publication")
        ),
    )
    with pytest.raises(SystemExit, match="second crash"):
        truth_rewrite.recover_orphan_truth_rewrite_intents(session_dir)

    assert state_messages == target
    assert truth_rewrite.truth_rewrite_intent_path(session).exists() is False
    assert json.loads(index_path.read_text(encoding="utf-8")) == []

    monkeypatch.setattr(models, "_write_session_index", original_write_index)
    result = session_recovery.recover_all_sessions_on_startup(
        session_dir,
        rebuild_index=True,
    )

    assert result["restored"] == 0
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert [row["session_id"] for row in index] == [session.session_id]


def test_committed_truth_truncation_cannot_be_restored_from_precommit_backup(
    tmp_path,
    monkeypatch,
):
    import api.models as models
    import api.routes as routes
    import api.session_recovery as session_recovery

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", {})
    before = [
        {"role": "user", "content": "question one"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "question two"},
        {"role": "assistant", "content": "answer two"},
    ]
    target = before[:2]
    session = models.Session(
        session_id="authorized-truncation",
        title="Authorized truncation",
        workspace=str(tmp_path),
        messages=before,
        context_messages=before,
    )
    session.save(skip_index=True)
    state_messages = list(before)

    def replace_state(_session, messages):
        nonlocal state_messages
        state_messages = list(messages)
        return True

    monkeypatch.setattr(routes, "_replace_state_db_truth", replace_state)
    routes._rewrite_existing_session_truth(
        session,
        lambda: setattr(session, "messages", list(target)),
        privacy_reason="retry",
    )

    assert state_messages == target
    assert _disk_messages(session) == target
    assert session.path.with_suffix(".json.bak").exists() is False

    outcome = session_recovery.recover_all_sessions_on_startup(
        session_dir,
        rebuild_index=False,
    )
    assert outcome["restored"] == 0
    assert _disk_messages(session) == target


def test_completed_recovery_discards_precommit_backup_before_marker_clear(
    tmp_path,
    monkeypatch,
):
    import api.models as models
    import api.truth_rewrite as truth_rewrite

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    before = [
        {"role": "user", "content": "question one"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "question two"},
        {"role": "assistant", "content": "answer two"},
    ]
    target = before[:2]
    session = models.Session(
        session_id="completed-shrink-recovery",
        title="Completed shrink recovery",
        workspace=str(tmp_path),
        messages=before,
        context_messages=before,
    )
    session.save(skip_index=True)
    truth_rewrite.write_truth_rewrite_intent(session, before, target)
    session.messages = list(target)
    session.context_messages = list(target)
    session.save(skip_index=True)
    marker = truth_rewrite.truth_rewrite_intent_path(session)
    backup = session.path.with_suffix(".json.bak")
    assert marker.exists()
    assert backup.exists()

    outcome = truth_rewrite.recover_truth_rewrite_intent(
        session,
        read_state_messages=lambda _session: list(target),
        replace_state_messages=lambda *_args, **_kwargs: pytest.fail(
            "completed recovery must not rewrite state.db"
        ),
    )

    assert outcome["status"] == "completed"
    assert backup.exists() is False
    assert marker.exists() is False


def test_backup_recovery_defers_to_live_truth_intent_after_db_commit_crash(
    tmp_path,
    monkeypatch,
):
    import api.models as models
    import api.routes as routes
    import api.session_recovery as session_recovery
    import api.truth_rewrite as truth_rewrite

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    before = [
        {"role": "user", "content": "question one"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "question two"},
        {"role": "assistant", "content": "answer two"},
    ]
    target = before[:2]
    session = models.Session(
        session_id="db-committed-before-backup-cleanup",
        title="DB committed before backup cleanup",
        workspace=str(tmp_path),
        messages=before,
        context_messages=before,
    )
    session.save(skip_index=True)
    state_messages = list(before)

    def commit_then_crash(_session, messages):
        nonlocal state_messages
        state_messages = list(messages)
        raise SystemExit("crash after DB commit")

    monkeypatch.setattr(routes, "_replace_state_db_truth", commit_then_crash)
    with pytest.raises(SystemExit, match="after DB commit"):
        routes._rewrite_existing_session_truth(
            session,
            lambda: setattr(session, "messages", list(target)),
            privacy_reason="retry",
        )

    marker = truth_rewrite.truth_rewrite_intent_path(session)
    backup = session.path.with_suffix(".json.bak")
    assert marker.exists()
    assert backup.exists()
    assert _disk_messages(session) == target
    assert state_messages == target

    backup_outcome = session_recovery.recover_all_sessions_on_startup(
        session_dir,
        rebuild_index=False,
    )
    assert backup_outcome["restored"] == 0
    assert backup_outcome["details"] == []
    assert _disk_messages(session) == target

    recovery = truth_rewrite.recover_truth_rewrite_intent(
        session,
        read_state_messages=lambda _session: list(state_messages),
        replace_state_messages=lambda *_args, **_kwargs: pytest.fail(
            "completed recovery must not rewrite state.db"
        ),
    )
    assert recovery["status"] == "completed"
    assert marker.exists() is False
    assert backup.exists() is False
    assert _disk_messages(session) == target


def test_full_index_rebuild_does_not_enter_truth_recovering_session_load(
    tmp_path,
    monkeypatch,
):
    """Index lock holders must never acquire a per-session truth lock."""
    import api.models as models

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_path = session_dir / "_index.json"
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_path)
    monkeypatch.setattr(models, "SESSIONS", {})
    session = models.Session(
        session_id="index-lock-order",
        title="Index lock order",
        workspace=str(tmp_path),
        messages=[{"role": "user", "content": "hello"}],
        context_messages=[{"role": "user", "content": "hello"}],
    )
    session.save(skip_index=True)

    def forbidden_load(cls, _session_id):
        raise AssertionError("full index rebuild entered truth-recovering Session.load")

    monkeypatch.setattr(models.Session, "load", classmethod(forbidden_load))
    models._write_session_index(updates=None)

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert [row["session_id"] for row in index] == [session.session_id]


def test_server_runs_truth_intent_recovery_before_backup_heuristics():
    import inspect
    import server

    source = inspect.getsource(server.main)
    assert source.index("_recover_orphan_truth_rewrites_on_startup()") < source.index(
        "recover_all_sessions_on_startup("
    )


@pytest.mark.parametrize("mode", ["success", "ordinary_exception"])
def test_non_crash_completion_never_leaves_intent(mode, tmp_path, monkeypatch):
    import api.routes as routes
    import api.truth_rewrite as truth_rewrite

    before = [{"role": "user", "content": "before"}]
    target = before + [{"role": "assistant", "content": "target"}]
    session = _session(tmp_path, before)
    if mode == "ordinary_exception":
        monkeypatch.setattr(
            routes,
            "_replace_state_db_truth",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("ordinary DB failure")
            ),
        )
        with pytest.raises(RuntimeError, match="ordinary DB failure"):
            routes._rewrite_existing_session_truth(
                session,
                lambda: setattr(session, "messages", list(target)),
                privacy_reason=None,
            )
        assert session.messages == before
        assert _disk_messages(session) == before
    else:
        monkeypatch.setattr(routes, "_replace_state_db_truth", lambda *_args: True)
        routes._rewrite_existing_session_truth(
            session,
            lambda: setattr(session, "messages", list(target)),
            privacy_reason=None,
        )
        assert _disk_messages(session) == target
    assert truth_rewrite.truth_rewrite_intent_path(session).exists() is False


def test_diverged_recovery_reports_without_guessing_or_disclosing_payload(
    tmp_path,
):
    import api.truth_rewrite as truth_rewrite

    before = [{"role": "user", "content": "before-secret-canary"}]
    target = before + [
        {
            "role": "assistant",
            "content": "target-secret-canary",
            "tool_calls": [{"name": "terminal", "args": {"token": "raw-tool-canary"}}],
        }
    ]
    session = _session(tmp_path, before)
    truth_rewrite.write_truth_rewrite_intent(session, before, target)
    session.messages = [{"role": "assistant", "content": "diverged"}]
    session.save()
    marker = truth_rewrite.truth_rewrite_intent_path(session)
    serialized = marker.read_text(encoding="utf-8")
    assert "before-secret-canary" not in serialized
    assert "target-secret-canary" not in serialized
    assert "raw-tool-canary" not in serialized
    assert str(tmp_path) not in serialized
    assert set(json.loads(serialized)) == {
        "schema_version",
        "session_id",
        "profile",
        "before_semantic_sha256",
        "target_semantic_sha256",
        "created_at",
    }

    replaced = []
    outcome = truth_rewrite.recover_truth_rewrite_intent(
        session,
        read_state_messages=lambda _session: list(before),
        replace_state_messages=lambda _session, messages: replaced.append(list(messages)) or True,
    )
    assert outcome == {
        "kind": "truth_rewrite_crash_recovery",
        "status": "diverged",
        "reason": "sidecar_or_state_hash_unknown",
        "session_id": session.session_id,
    }
    assert replaced == []
    assert marker.exists()


def test_default_recovery_hash_matches_real_sessiondb_roundtrip(tmp_path, monkeypatch):
    import api.profiles as profiles
    import api.state_sync as state_sync
    import api.truth_rewrite as truth_rewrite
    from hermes_state import SessionDB

    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: hermes_home)
    before = [
        {
            "role": "user",
            "content": "before",
            "platform_message_id": "webui-turn:before",
        }
    ]
    target = before + [
        {
            "role": "assistant",
            "content": "target",
            "reasoning": "safe reasoning",
        }
    ]
    session = _session(tmp_path, before)
    state_sync.replace_webui_session_messages(
        session_id=session.session_id,
        messages=before,
        model=session.model,
    )
    truth_rewrite.write_truth_rewrite_intent(session, before, target)
    session.messages = list(target)
    session.context_messages = list(target)
    session.save()

    outcome = truth_rewrite.recover_truth_rewrite_intent(session)
    assert outcome["status"] == "rolled_forward"
    db = SessionDB(hermes_home / "state.db")
    try:
        assert truth_rewrite.semantic_truth_hash(db.get_messages(session.session_id)) == (
            truth_rewrite.semantic_truth_hash(target)
        )
    finally:
        db.close()
    assert truth_rewrite.truth_rewrite_intent_path(session).exists() is False
