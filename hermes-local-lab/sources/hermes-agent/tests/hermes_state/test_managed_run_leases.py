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
