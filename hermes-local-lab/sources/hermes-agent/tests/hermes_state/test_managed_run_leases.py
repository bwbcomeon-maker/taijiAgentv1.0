"""Cross-process managed-run lease tests for state.db."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import sqlite3
import threading
import time

import pytest

import hermes_state
from hermes_state import (
    ManagedRunLeaseLostError,
    SessionDB,
    bind_managed_run_write_lease,
    reset_managed_run_write_lease,
)


class _AdvanceClockAfterLock:
    """Advance a fake clock once the SessionDB process lock is acquired."""

    def __init__(self, lock, advance_clock):
        self._lock = lock
        self._advance_clock = advance_clock

    def __enter__(self):
        entered = self._lock.__enter__()
        self._advance_clock()
        return entered

    def __exit__(self, exc_type, exc_value, traceback):
        return self._lock.__exit__(exc_type, exc_value, traceback)


def _process_acquire(db_path, barrier, result_queue, owner_id, run_id):
    db = SessionDB(db_path=Path(db_path))
    try:
        barrier.wait(timeout=5.0)
        acquired = db.acquire_managed_run_lease(
            "session-process-shared",
            owner_id=owner_id,
            run_id=run_id,
            lease_seconds=30.0,
            now=100.0,
        )
        result_queue.put((owner_id, acquired, None))
    except Exception as exc:  # pragma: no cover - returned to parent assertion
        result_queue.put((owner_id, None, repr(exc)))
    finally:
        db.close()


def _process_first_init(db_path, barrier, result_queue, session_id):
    db = None
    try:
        barrier.wait(timeout=5.0)
        db = SessionDB(db_path=Path(db_path))
        db.create_session(session_id, "api_server")
        result_queue.put((session_id, True, None))
    except Exception as exc:  # pragma: no cover - returned to parent assertion
        result_queue.put((session_id, False, repr(exc)))
    finally:
        if db is not None:
            db.close()


def _shared_dbs(tmp_path):
    db_path = tmp_path / "state.db"
    return SessionDB(db_path=db_path), SessionDB(db_path=db_path)


def test_same_session_acquire_is_atomic_across_connections(tmp_path):
    db_a, db_b = _shared_dbs(tmp_path)
    db_a.create_session("session-shared", "api_server")
    barrier = threading.Barrier(2)

    def _acquire(db, owner, run_id):
        barrier.wait(timeout=3.0)
        return db.acquire_managed_run_lease(
            "session-shared",
            owner_id=owner,
            run_id=run_id,
            lease_seconds=30.0,
            now=100.0,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            result_a = pool.submit(_acquire, db_a, "owner-a", "run-a")
            result_b = pool.submit(_acquire, db_b, "owner-b", "run-b")
            results = [result_a.result(timeout=5.0), result_b.result(timeout=5.0)]

        assert sorted(results) == [False, True]
        lease = db_a.get_managed_run_lease("session-shared")
        assert lease is not None
        assert lease["owner_id"] in {"owner-a", "owner-b"}
        assert lease["run_id"] in {"run-a", "run-b"}
        assert lease["acquired_at"] == 100.0
        assert lease["heartbeat_at"] == 100.0
        assert lease["expires_at"] == 130.0
    finally:
        db_a.close()
        db_b.close()


def test_acquire_lease_starts_duration_after_write_lock_wait(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("session-delayed-acquire", "api_server")
    clock = {"now": 100.0}
    original_lock = db._lock
    db._lock = _AdvanceClockAfterLock(
        original_lock,
        lambda: clock.update(now=120.0),
    )
    monkeypatch.setattr(hermes_state.time, "time", lambda: clock["now"])

    try:
        assert db.acquire_managed_run_lease(
            "session-delayed-acquire",
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=10.0,
        ) is True
    finally:
        db._lock = original_lock

    try:
        lease = db.get_managed_run_lease("session-delayed-acquire")
        assert lease is not None
        assert lease["acquired_at"] == 120.0
        assert lease["heartbeat_at"] == 120.0
        assert lease["expires_at"] == 130.0
    finally:
        db.close()


def test_heartbeat_lease_starts_duration_after_write_lock_wait(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("session-delayed-heartbeat", "api_server")
    assert db.acquire_managed_run_lease(
        "session-delayed-heartbeat",
        owner_id="owner-a",
        run_id="run-a",
        lease_seconds=100.0,
        now=100.0,
    ) is True
    clock = {"now": 105.0}
    original_lock = db._lock
    db._lock = _AdvanceClockAfterLock(
        original_lock,
        lambda: clock.update(now=120.0),
    )
    monkeypatch.setattr(hermes_state.time, "time", lambda: clock["now"])

    try:
        assert db.heartbeat_managed_run_lease(
            "session-delayed-heartbeat",
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=10.0,
        ) is True
    finally:
        db._lock = original_lock

    try:
        lease = db.get_managed_run_lease("session-delayed-heartbeat")
        assert lease is not None
        assert lease["acquired_at"] == 100.0
        assert lease["heartbeat_at"] == 120.0
        assert lease["expires_at"] == 130.0
    finally:
        db.close()


def test_v13_database_migrates_managed_run_lease_schema(tmp_path):
    db_path = tmp_path / "state.db"
    current = SessionDB(db_path=db_path)
    current.close()
    raw = sqlite3.connect(str(db_path))
    try:
        raw.execute("DROP INDEX IF EXISTS idx_managed_run_leases_expiry")
        raw.execute("DROP TABLE IF EXISTS managed_run_leases")
        raw.execute("UPDATE schema_version SET version = 13")
        raw.commit()
    finally:
        raw.close()

    migrated = SessionDB(db_path=db_path)
    try:
        version = migrated._conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()[0]
        columns = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(managed_run_leases)"
            ).fetchall()
        }
        assert version == 14
        assert columns == {
            "session_id",
            "owner_id",
            "run_id",
            "acquired_at",
            "heartbeat_at",
            "expires_at",
        }
    finally:
        migrated.close()


def test_same_session_acquire_is_atomic_across_processes(tmp_path):
    db_path = tmp_path / "state.db"
    setup_db = SessionDB(db_path=db_path)
    setup_db.create_session("session-process-shared", "api_server")
    setup_db.close()
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_process_acquire,
            args=(str(db_path), barrier, result_queue, "owner-a", "run-a"),
        ),
        ctx.Process(
            target=_process_acquire,
            args=(str(db_path), barrier, result_queue, "owner-b", "run-b"),
        ),
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10.0)

    try:
        assert [process.exitcode for process in processes] == [0, 0]
        results = [result_queue.get(timeout=2.0), result_queue.get(timeout=2.0)]
        assert [error for _, _, error in results] == [None, None]
        assert sorted(acquired for _, acquired, _ in results) == [False, True]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
        result_queue.close()


def test_concurrent_processes_can_initialize_lease_schema(tmp_path):
    db_path = tmp_path / "first-init.db"
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_process_first_init,
            args=(str(db_path), barrier, result_queue, "session-a"),
        ),
        ctx.Process(
            target=_process_first_init,
            args=(str(db_path), barrier, result_queue, "session-b"),
        ),
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10.0)

    try:
        assert [process.exitcode for process in processes] == [0, 0]
        results = [result_queue.get(timeout=2.0), result_queue.get(timeout=2.0)]
        assert [error for _, _, error in results] == [None, None]
        assert all(ok for _, ok, _ in results)
        db = SessionDB(db_path=db_path)
        try:
            assert db.get_session("session-a") is not None
            assert db.get_session("session-b") is not None
            columns = {
                row[1]
                for row in db._conn.execute(
                    "PRAGMA table_info(managed_run_leases)"
                ).fetchall()
            }
            assert "expires_at" in columns
        finally:
            db.close()
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
        result_queue.close()


def test_different_sessions_can_both_acquire_across_connections(tmp_path):
    db_a, db_b = _shared_dbs(tmp_path)
    db_a.create_session("session-a", "api_server")
    db_a.create_session("session-b", "api_server")
    barrier = threading.Barrier(2)

    def _acquire(db, session_id, owner, run_id):
        barrier.wait(timeout=3.0)
        return db.acquire_managed_run_lease(
            session_id,
            owner_id=owner,
            run_id=run_id,
            lease_seconds=30.0,
            now=100.0,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            result_a = pool.submit(
                _acquire, db_a, "session-a", "owner-a", "run-a"
            )
            result_b = pool.submit(
                _acquire, db_b, "session-b", "owner-b", "run-b"
            )
            assert result_a.result(timeout=5.0) is True
            assert result_b.result(timeout=5.0) is True
    finally:
        db_a.close()
        db_b.close()


def test_expired_lease_can_be_taken_over_safely(tmp_path):
    db_a, db_b = _shared_dbs(tmp_path)
    db_a.create_session("session-expired", "api_server")
    try:
        assert db_a.acquire_managed_run_lease(
            "session-expired",
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=10.0,
            now=100.0,
        ) is True
        # Simulate the owning service disappearing without a release.  The
        # durable row must block until expiry, then permit a new owner.
        db_a.close()
        assert db_b.acquire_managed_run_lease(
            "session-expired",
            owner_id="owner-b",
            run_id="run-b",
            lease_seconds=10.0,
            now=109.999,
        ) is False
        assert db_b.acquire_managed_run_lease(
            "session-expired",
            owner_id="owner-b",
            run_id="run-b",
            lease_seconds=10.0,
            now=110.0,
        ) is True
        assert db_b.release_managed_run_lease(
            "session-expired", owner_id="owner-a", run_id="run-a"
        ) is False
        assert db_b.heartbeat_managed_run_lease(
            "session-expired",
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=10.0,
            now=111.0,
        ) is False

        lease = db_b.get_managed_run_lease("session-expired")
        assert lease is not None
        assert lease["owner_id"] == "owner-b"
        assert lease["run_id"] == "run-b"
        assert lease["acquired_at"] == 110.0
        assert lease["heartbeat_at"] == 110.0
        assert lease["expires_at"] == 120.0
    finally:
        db_a.close()
        db_b.close()


def test_only_exact_owner_and_run_can_heartbeat_or_release(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("session-owned", "api_server")
    try:
        assert db.acquire_managed_run_lease(
            "session-owned",
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=20.0,
            now=100.0,
        ) is True

        assert db.heartbeat_managed_run_lease(
            "session-owned",
            owner_id="owner-b",
            run_id="run-a",
            lease_seconds=20.0,
            now=105.0,
        ) is False
        assert db.heartbeat_managed_run_lease(
            "session-owned",
            owner_id="owner-a",
            run_id="run-b",
            lease_seconds=20.0,
            now=105.0,
        ) is False
        assert db.release_managed_run_lease(
            "session-owned", owner_id="owner-b", run_id="run-a"
        ) is False
        assert db.release_managed_run_lease(
            "session-owned", owner_id="owner-a", run_id="run-b"
        ) is False

        assert db.heartbeat_managed_run_lease(
            "session-owned",
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=20.0,
            now=105.0,
        ) is True
        lease = db.get_managed_run_lease("session-owned")
        assert lease is not None
        assert lease["acquired_at"] == 100.0
        assert lease["heartbeat_at"] == 105.0
        assert lease["expires_at"] == 125.0

        assert db.release_managed_run_lease(
            "session-owned", owner_id="owner-a", run_id="run-a"
        ) is True
        assert db.get_managed_run_lease("session-owned") is None
    finally:
        db.close()


def test_bound_write_lease_fences_stale_owner_after_takeover(tmp_path):
    db_a, db_b = _shared_dbs(tmp_path)
    db_a.create_session("session-fenced", "api_server")
    stale_at = time.time() - 10.0
    lost_event = threading.Event()
    try:
        assert db_a.acquire_managed_run_lease(
            "session-fenced",
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=1.0,
            now=stale_at,
        ) is True
        assert db_b.acquire_managed_run_lease(
            "session-fenced",
            owner_id="owner-b",
            run_id="run-b",
            lease_seconds=30.0,
        ) is True

        token = bind_managed_run_write_lease(
            "session-fenced",
            owner_id="owner-a",
            run_id="run-a",
            lost_event=lost_event,
        )
        try:
            with pytest.raises(ManagedRunLeaseLostError):
                db_a.append_message("session-fenced", "assistant", "late write")
        finally:
            reset_managed_run_write_lease(token)

        assert lost_event.is_set()
        assert db_a.get_messages_as_conversation("session-fenced") == []

        token = bind_managed_run_write_lease(
            "session-fenced",
            owner_id="owner-b",
            run_id="run-b",
        )
        try:
            db_b.append_message("session-fenced", "assistant", "current write")
        finally:
            reset_managed_run_write_lease(token)
        assert db_a.get_messages_as_conversation("session-fenced") == [
            {"role": "assistant", "content": "current write"}
        ]
    finally:
        db_a.close()
        db_b.close()
def test_compression_child_uses_root_lease_and_can_append(tmp_path):
    db = SessionDB(db_path=tmp_path / "compression.db")
    db.create_session("compression-parent", "api_server")
    db.end_session("compression-parent", "compression")
    db.create_session(
        "compression-child",
        "api_server",
        parent_session_id="compression-parent",
    )
    db.end_session("compression-child", "compression")
    db.create_session(
        "compression-grandchild",
        "api_server",
        parent_session_id="compression-child",
    )
    assert db.get_managed_run_lease_key("compression-child") == "compression-parent"
    assert db.get_managed_run_lease_key("compression-grandchild") == "compression-parent"
    assert db.acquire_managed_run_lease(
        "compression-child", owner_id="owner", run_id="run", lease_seconds=30
    )
    token = bind_managed_run_write_lease(
        "compression-parent", owner_id="owner", run_id="run"
    )
    try:
        db.append_message("compression-grandchild", "assistant", "continued")
    finally:
        reset_managed_run_write_lease(token)
        db.close()


def test_unique_compression_lineage_has_one_root_lease_across_connections(
    tmp_path,
):
    db_a, db_b = _shared_dbs(tmp_path)
    db_a.create_session("lineage-root", "api_server")
    db_a.end_session("lineage-root", "compression")
    db_a.create_session(
        "lineage-mid",
        "api_server",
        parent_session_id="lineage-root",
    )
    db_a.end_session("lineage-mid", "compression")
    db_a.create_session(
        "lineage-tip",
        "api_server",
        parent_session_id="lineage-mid",
    )

    try:
        resolutions = [
            db_a.resolve_compression_lineage(session_id)
            for session_id in ("lineage-root", "lineage-mid", "lineage-tip")
        ]
        assert {
            (result.root_id, result.tip_id, result.status)
            for result in resolutions
        } == {("lineage-root", "lineage-tip", "ok")}

        assert db_a.acquire_managed_run_lease(
            "lineage-root",
            owner_id="owner-root",
            run_id="run-root",
            lease_seconds=30.0,
        )
        assert not db_b.acquire_managed_run_lease(
            "lineage-tip",
            owner_id="owner-tip",
            run_id="run-tip",
            lease_seconds=30.0,
        )
        rows = db_a._conn.execute(
            "SELECT session_id FROM managed_run_leases"
        ).fetchall()
        assert [row["session_id"] for row in rows] == ["lineage-root"]
    finally:
        db_a.close()
        db_b.close()


def test_ambiguous_compression_lineage_rejects_all_lease_entrypoints(tmp_path):
    db = SessionDB(db_path=tmp_path / "ambiguous.db")
    db.create_session("ambiguous-root", "api_server")
    db.end_session("ambiguous-root", "compression")
    for child_id in ("ambiguous-a", "ambiguous-b"):
        db.create_session(
            child_id,
            "api_server",
            parent_session_id="ambiguous-root",
        )

    try:
        resolutions = [
            db.resolve_compression_lineage(session_id)
            for session_id in ("ambiguous-root", "ambiguous-a", "ambiguous-b")
        ]
        assert {
            (result.root_id, result.tip_id, result.status, result.conflict_count)
            for result in resolutions
        } == {("ambiguous-root", None, "ambiguous", 2)}

        for session_id in ("ambiguous-root", "ambiguous-a", "ambiguous-b"):
            with pytest.raises(hermes_state.CompressionLineageAmbiguousError):
                db.acquire_managed_run_lease(
                    session_id,
                    owner_id=f"owner-{session_id}",
                    run_id=f"run-{session_id}",
                    lease_seconds=30.0,
                )
        assert db._conn.execute(
            "SELECT COUNT(*) FROM managed_run_leases"
        ).fetchone()[0] == 0
    finally:
        db.close()


def test_lease_acquire_rechecks_lineage_after_preflight(tmp_path):
    db_a, db_b = _shared_dbs(tmp_path)
    db_a.create_session("race-root", "api_server")
    db_a.end_session("race-root", "compression")
    db_a.create_session(
        "race-a",
        "api_server",
        parent_session_id="race-root",
    )

    try:
        preflight = db_a.resolve_compression_lineage("race-a")
        assert (preflight.root_id, preflight.tip_id, preflight.status) == (
            "race-root",
            "race-a",
            "ok",
        )
        db_b.create_session(
            "race-b",
            "api_server",
            parent_session_id="race-root",
        )

        with pytest.raises(hermes_state.CompressionLineageAmbiguousError):
            db_a.acquire_managed_run_lease(
                "race-a",
                owner_id="owner-a",
                run_id="run-a",
                lease_seconds=30.0,
            )
        assert db_a._conn.execute(
            "SELECT COUNT(*) FROM managed_run_leases"
        ).fetchone()[0] == 0
    finally:
        db_a.close()
        db_b.close()


def test_lineage_ambiguity_fences_live_lease_but_exact_release_still_works(
    tmp_path,
):
    db_a, db_b = _shared_dbs(tmp_path)
    db_a.create_session("live-root", "api_server")
    assert db_a.acquire_managed_run_lease(
        "live-root",
        owner_id="owner-live",
        run_id="run-live",
        lease_seconds=30.0,
    )
    db_a.end_session("live-root", "compression")
    db_a.create_session(
        "live-a",
        "api_server",
        parent_session_id="live-root",
    )
    db_b.create_session(
        "live-b",
        "api_server",
        parent_session_id="live-root",
    )
    lost_event = threading.Event()

    try:
        assert not db_a.heartbeat_managed_run_lease(
            "live-a",
            owner_id="owner-live",
            run_id="run-live",
            lease_seconds=30.0,
        )
        token = bind_managed_run_write_lease(
            "live-root",
            owner_id="owner-live",
            run_id="run-live",
            lost_event=lost_event,
        )
        try:
            with pytest.raises(ManagedRunLeaseLostError):
                db_a.append_message("live-a", "assistant", "must not persist")
        finally:
            reset_managed_run_write_lease(token)
        assert lost_event.is_set()
        assert db_a.get_messages_as_conversation("live-a") == []

        assert db_a.release_managed_run_lease(
            "live-a",
            owner_id="owner-live",
            run_id="run-live",
        )
        assert db_a._conn.execute(
            "SELECT COUNT(*) FROM managed_run_leases"
        ).fetchone()[0] == 0
    finally:
        db_a.close()
        db_b.close()


def test_preexisting_branch_child_does_not_inherit_compression_lease(tmp_path):
    db = SessionDB(db_path=tmp_path / "branch.db")
    db.create_session("branch-parent", "api_server")
    db.create_session("branch-child", "api_server", parent_session_id="branch-parent")
    db.end_session("branch-parent", "compression")
    assert db.get_managed_run_lease_key("branch-child") == "branch-child"
    assert db.acquire_managed_run_lease(
        "branch-parent", owner_id="owner", run_id="run", lease_seconds=30
    )
    token = bind_managed_run_write_lease(
        "branch-parent", owner_id="owner", run_id="run"
    )
    try:
        with pytest.raises(ManagedRunLeaseLostError):
            db.append_message("branch-child", "assistant", "forbidden")
    finally:
        reset_managed_run_write_lease(token)
        db.close()


def _durable_session_snapshot(db):
    """Capture the state rows a managed turn is allowed to mutate."""
    with db._lock:
        return {
            "sessions": [
                tuple(row)
                for row in db._conn.execute(
                    "SELECT * FROM sessions ORDER BY id"
                ).fetchall()
            ],
            "messages": [
                tuple(row)
                for row in db._conn.execute(
                    "SELECT * FROM messages ORDER BY id"
                ).fetchall()
            ],
            "leases": [
                tuple(row)
                for row in db._conn.execute(
                    "SELECT * FROM managed_run_leases ORDER BY session_id"
                ).fetchall()
            ],
        }


def _invoke_session_write(db, operation, *, root_id, child_id):
    if operation == "_insert_session_row":
        return db._insert_session_row(
            child_id,
            "api_server",
            parent_session_id=root_id,
        )
    if operation == "create_session":
        return db.create_session(
            child_id,
            "api_server",
            parent_session_id=root_id,
        )
    if operation == "ensure_session":
        return db.ensure_session(
            child_id,
            "api_server",
            parent_session_id=root_id,
        )
    if operation == "end_session":
        return db.end_session(root_id, "manual")
    if operation == "reopen_session":
        return db.reopen_session(root_id)
    if operation == "update_system_prompt":
        return db.update_system_prompt(root_id, "stale prompt")
    if operation == "update_token_counts":
        return db.update_token_counts(
            root_id,
            input_tokens=7,
            output_tokens=3,
            api_call_count=1,
        )
    if operation == "set_session_title":
        return db.set_session_title(root_id, "stale title")
    if operation == "replace_messages":
        return db.replace_messages(
            root_id,
            [{"role": "assistant", "content": "stale replacement"}],
        )
    if operation == "clear_messages":
        return db.clear_messages(root_id)
    if operation == "delete_session":
        return db.delete_session(root_id)
    if operation == "append_message":
        return db.append_message(root_id, "assistant", "stale append")
    raise AssertionError(f"unknown operation: {operation}")


@pytest.mark.parametrize(
    "operation",
    [
        "_insert_session_row",
        "create_session",
        "ensure_session",
        "end_session",
        "reopen_session",
        "update_system_prompt",
        "update_token_counts",
        "set_session_title",
        "replace_messages",
        "clear_messages",
        "delete_session",
        "append_message",
    ],
)
def test_stale_bound_owner_cannot_mutate_any_session_state_after_takeover(
    tmp_path,
    operation,
):
    db_a, db_b = _shared_dbs(tmp_path)
    root_id = f"stale-root-{operation}"
    child_id = f"stale-child-{operation}"
    db_a.create_session(
        root_id,
        "api_server",
        system_prompt="original prompt",
    )
    db_a.append_message(root_id, "user", "original message")
    db_a.set_session_title(root_id, f"original title {operation}")
    db_a.end_session(root_id, "compression")
    stale_at = time.time() - 10.0
    lost_event = threading.Event()

    try:
        assert db_a.acquire_managed_run_lease(
            root_id,
            owner_id="owner-a",
            run_id="run-a",
            lease_seconds=1.0,
            now=stale_at,
        )
        assert db_b.acquire_managed_run_lease(
            root_id,
            owner_id="owner-b",
            run_id="run-b",
            lease_seconds=30.0,
        )
        before = _durable_session_snapshot(db_a)

        token = bind_managed_run_write_lease(
            root_id,
            owner_id="owner-a",
            run_id="run-a",
            lost_event=lost_event,
        )
        try:
            with pytest.raises(ManagedRunLeaseLostError):
                _invoke_session_write(
                    db_a,
                    operation,
                    root_id=root_id,
                    child_id=child_id,
                )
        finally:
            reset_managed_run_write_lease(token)

        assert lost_event.is_set()
        assert _durable_session_snapshot(db_a) == before
    finally:
        db_a.close()
        db_b.close()


@pytest.mark.parametrize(
    "operation",
    [
        "_insert_session_row",
        "create_session",
        "ensure_session",
        "end_session",
        "reopen_session",
        "update_system_prompt",
        "update_token_counts",
        "set_session_title",
        "replace_messages",
        "clear_messages",
        "delete_session",
        "append_message",
    ],
)
def test_live_bound_owner_can_mutate_session_state(
    tmp_path,
    operation,
):
    db = SessionDB(db_path=tmp_path / f"live-{operation}.db")
    root_id = f"live-root-{operation}"
    child_id = f"live-child-{operation}"
    db.create_session(root_id, "api_server", system_prompt="original prompt")
    db.append_message(root_id, "user", "original message")
    if operation in {"_insert_session_row", "create_session", "ensure_session"}:
        db.end_session(root_id, "compression")
    elif operation == "reopen_session":
        db.end_session(root_id, "manual")
    lost_event = threading.Event()

    try:
        assert db.acquire_managed_run_lease(
            root_id,
            owner_id="owner-live",
            run_id="run-live",
            lease_seconds=30.0,
        )
        before = _durable_session_snapshot(db)
        token = bind_managed_run_write_lease(
            root_id,
            owner_id="owner-live",
            run_id="run-live",
            lost_event=lost_event,
        )
        try:
            _invoke_session_write(
                db,
                operation,
                root_id=root_id,
                child_id=child_id,
            )
        finally:
            reset_managed_run_write_lease(token)

        assert not lost_event.is_set()
        after = _durable_session_snapshot(db)
        assert after != before
        if operation in {"_insert_session_row", "create_session", "ensure_session"}:
            child = db.get_session(child_id)
            assert child is not None
            assert child["parent_session_id"] == root_id
            assert db.get_managed_run_lease_key(child_id) == root_id
    finally:
        db.close()


def test_live_compression_continuation_keeps_root_lease_for_all_writes(tmp_path):
    db = SessionDB(db_path=tmp_path / "live-compression.db")
    root_id = "live-compression-root"
    child_id = "live-compression-child"
    db.create_session(root_id, "api_server")
    db.set_session_title(root_id, "compression title")
    assert db.acquire_managed_run_lease(
        root_id,
        owner_id="owner-live",
        run_id="run-live",
        lease_seconds=30.0,
    )

    token = bind_managed_run_write_lease(
        root_id,
        owner_id="owner-live",
        run_id="run-live",
    )
    try:
        db.end_session(root_id, "compression")
        db.create_session(
            child_id,
            "api_server",
            parent_session_id=root_id,
        )
        db.set_session_title(child_id, "compression title #2")
        db.update_system_prompt(child_id, "continued prompt")
        db.update_token_counts(
            child_id,
            input_tokens=11,
            output_tokens=5,
            api_call_count=1,
        )
        db.append_message(child_id, "assistant", "first continuation")
        db.replace_messages(
            child_id,
            [{"role": "assistant", "content": "final continuation"}],
        )
    finally:
        reset_managed_run_write_lease(token)

    try:
        child = db.get_session(child_id)
        assert child is not None
        assert child["parent_session_id"] == root_id
        assert child["title"] == "compression title #2"
        assert child["system_prompt"] == "continued prompt"
        assert child["input_tokens"] == 11
        assert child["output_tokens"] == 5
        assert child["api_call_count"] == 1
        assert child["message_count"] == 1
        assert db.get_messages_as_conversation(child_id) == [
            {"role": "assistant", "content": "final continuation"}
        ]
        assert db.get_managed_run_lease_key(child_id) == root_id
    finally:
        db.close()


@pytest.mark.parametrize(
    "target_kind",
    [
        "missing-parent",
        "unrelated-parent",
        "existing-branch-with-forged-parent",
        "existing-ambiguous-compression-branch",
        "future-ended-compression-parent",
        "second-compression-child",
    ],
)
def test_bound_lease_rejects_forged_or_ambiguous_compression_children(
    tmp_path,
    target_kind,
):
    db = SessionDB(db_path=tmp_path / f"forged-{target_kind}.db")
    root_id = "leased-root"
    db.create_session(root_id, "api_server")
    if target_kind == "existing-branch-with-forged-parent":
        db.create_session(
            "existing-branch",
            "api_server",
            parent_session_id=root_id,
        )
    db.end_session(root_id, "compression")
    if target_kind == "unrelated-parent":
        db.create_session("unrelated-parent", "api_server")
        db.end_session("unrelated-parent", "compression")
    if target_kind == "future-ended-compression-parent":
        db._execute_write(
            lambda conn: conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (time.time() + 60.0, root_id),
            )
        )
    if target_kind == "second-compression-child":
        db.create_session(
            "first-compression-child",
            "api_server",
            parent_session_id=root_id,
        )

    assert db.acquire_managed_run_lease(
        root_id,
        owner_id="owner-live",
        run_id="run-live",
        lease_seconds=30.0,
    )
    if target_kind == "existing-ambiguous-compression-branch":
        db.create_session(
            "ambiguous-child-a",
            "api_server",
            parent_session_id=root_id,
        )
        db.create_session(
            "ambiguous-child-b",
            "api_server",
            parent_session_id=root_id,
        )
    lost_event = threading.Event()
    before = _durable_session_snapshot(db)
    token = bind_managed_run_write_lease(
        root_id,
        owner_id="owner-live",
        run_id="run-live",
        lost_event=lost_event,
    )
    try:
        with pytest.raises(ManagedRunLeaseLostError):
            if target_kind == "missing-parent":
                db.create_session(
                    "forged-child",
                    "api_server",
                    parent_session_id="missing-parent",
                )
            elif target_kind == "unrelated-parent":
                db.create_session(
                    "forged-child",
                    "api_server",
                    parent_session_id="unrelated-parent",
                )
            elif target_kind == "existing-branch-with-forged-parent":
                db.create_session(
                    "existing-branch",
                    "api_server",
                    parent_session_id=root_id,
                )
            elif target_kind == "existing-ambiguous-compression-branch":
                db.create_session(
                    "ambiguous-child-a",
                    "api_server",
                    parent_session_id=root_id,
                )
            elif target_kind == "future-ended-compression-parent":
                db.create_session(
                    "future-child",
                    "api_server",
                    parent_session_id=root_id,
                )
            else:
                db.create_session(
                    "second-compression-child",
                    "api_server",
                    parent_session_id=root_id,
                )
    finally:
        reset_managed_run_write_lease(token)

    try:
        assert lost_event.is_set()
        assert _durable_session_snapshot(db) == before
    finally:
        db.close()


def test_token_ensure_and_counter_update_share_one_fenced_transaction(
    tmp_path,
    monkeypatch,
):
    db_a, db_b = _shared_dbs(tmp_path)
    root_id = "token-atomic-root"
    db_a.create_session(root_id, "api_server")
    assert db_a.acquire_managed_run_lease(
        root_id,
        owner_id="owner-a",
        run_id="run-a",
        lease_seconds=10.0,
        now=100.0,
    )
    monkeypatch.setattr(hermes_state.time, "time", lambda: 105.0)
    original_execute_write = db_a._execute_write
    calls = {"count": 0}

    def _count_and_take_over_after_transaction(fn):
        result = original_execute_write(fn)
        calls["count"] += 1
        if calls["count"] == 1:
            assert db_b.acquire_managed_run_lease(
                root_id,
                owner_id="owner-b",
                run_id="run-b",
                lease_seconds=30.0,
                now=110.0,
            )
        return result

    monkeypatch.setattr(db_a, "_execute_write", _count_and_take_over_after_transaction)
    token = bind_managed_run_write_lease(
        root_id,
        owner_id="owner-a",
        run_id="run-a",
    )
    try:
        db_a.update_token_counts(
            root_id,
            input_tokens=5,
            output_tokens=2,
            api_call_count=1,
        )
    finally:
        reset_managed_run_write_lease(token)

    try:
        assert calls["count"] == 1
        session = db_a.get_session(root_id)
        assert session["input_tokens"] == 5
        assert session["output_tokens"] == 2
        assert session["api_call_count"] == 1
        assert db_a.get_managed_run_lease(root_id)["owner_id"] == "owner-b"
    finally:
        db_a.close()
        db_b.close()


def test_bound_write_fails_closed_at_exact_lease_expiry(
    tmp_path,
    monkeypatch,
):
    db = SessionDB(db_path=tmp_path / "exact-expiry.db")
    root_id = "exact-expiry-root"
    db.create_session(root_id, "api_server", system_prompt="original")
    assert db.acquire_managed_run_lease(
        root_id,
        owner_id="owner-a",
        run_id="run-a",
        lease_seconds=10.0,
        now=100.0,
    )
    monkeypatch.setattr(hermes_state.time, "time", lambda: 110.0)
    lost_event = threading.Event()
    before = _durable_session_snapshot(db)
    token = bind_managed_run_write_lease(
        root_id,
        owner_id="owner-a",
        run_id="run-a",
        lost_event=lost_event,
    )
    try:
        with pytest.raises(ManagedRunLeaseLostError):
            db.update_system_prompt(root_id, "expired")
    finally:
        reset_managed_run_write_lease(token)

    try:
        assert lost_event.is_set()
        assert _durable_session_snapshot(db) == before
    finally:
        db.close()


def test_unbound_session_db_writes_remain_compatible(tmp_path):
    db = SessionDB(db_path=tmp_path / "unbound.db")
    root_id = "unbound-root"
    try:
        db.create_session(root_id, "cli")
        db.ensure_session(root_id, "cli")
        db.update_system_prompt(root_id, "plain prompt")
        db.update_token_counts(
            root_id,
            input_tokens=3,
            output_tokens=2,
            api_call_count=1,
        )
        assert db.set_session_title(root_id, "plain title")
        db.append_message(root_id, "user", "plain message")
        db.replace_messages(
            root_id,
            [{"role": "assistant", "content": "plain replacement"}],
        )
        db.clear_messages(root_id)
        db.end_session(root_id, "completed")
        db.reopen_session(root_id)

        session = db.get_session(root_id)
        assert session["system_prompt"] == "plain prompt"
        assert session["input_tokens"] == 3
        assert session["output_tokens"] == 2
        assert session["api_call_count"] == 1
        assert session["title"] == "plain title"
        assert session["ended_at"] is None
        assert session["message_count"] == 0
        assert db.delete_session(root_id)
        assert db.get_session(root_id) is None
    finally:
        db.close()
