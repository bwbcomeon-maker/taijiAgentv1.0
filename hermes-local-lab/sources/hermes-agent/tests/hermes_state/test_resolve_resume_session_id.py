"""Regression guard for #15000: --resume <id> after compression loses messages.

Context compression ends the current session and forks a new child session
(linked by ``parent_session_id``). The SQLite flush cursor is reset, so
only the latest descendant ends up with rows in the ``messages`` table —
the parent row has ``message_count = 0``. ``hermes --resume <parent_id>``
used to load zero rows and show a blank chat.

``SessionDB.resolve_resume_session_id()`` walks the parent → child chain
and redirects to the first descendant that actually has messages. These
tests pin that behaviour.
"""
import time

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _make_chain(db: SessionDB, ids_with_parent):
    """Create a deterministic legal compression chain (or fork)."""
    base = int(time.time()) - 10_000
    started_at = {}
    for i, (sid, parent) in enumerate(ids_with_parent):
        db.create_session(sid, source="cli", parent_session_id=parent)
        started_at[sid] = base + i * 100
        db._conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (started_at[sid], sid),
        )
    first_child_started_at = {}
    for sid, parent in ids_with_parent:
        if parent:
            first_child_started_at[parent] = min(
                first_child_started_at.get(parent, started_at[sid]),
                started_at[sid],
            )
    for parent, child_started_at in first_child_started_at.items():
        db._conn.execute(
            "UPDATE sessions SET ended_at = ?, end_reason = 'compression' "
            "WHERE id = ?",
            (child_started_at - 1, parent),
        )
    db._conn.commit()


def test_redirects_from_empty_head_to_descendant_with_messages(db):
    # Reproducer shape from #15000: 6 sessions, only the 5th holds messages.
    _make_chain(db, [
        ("head",   None),
        ("mid1",   "head"),
        ("mid2",   "mid1"),
        ("mid3",   "mid2"),
        ("bulk",   "mid3"),    # has messages
        ("tail",   "bulk"),    # empty tail after another compression
    ])
    for i in range(5):
        db.append_message("bulk", role="user", content=f"msg {i}")

    assert db.resolve_resume_session_id("head") == "bulk"


def test_returns_self_when_session_has_messages(db):
    _make_chain(db, [("root", None), ("child", "root")])
    db.append_message("root", role="user", content="hi")
    assert db.resolve_resume_session_id("root") == "root"


def test_returns_self_when_no_descendant_has_messages(db):
    _make_chain(db, [("root", None), ("child1", "root"), ("child2", "child1")])
    assert db.resolve_resume_session_id("root") == "root"


def test_returns_self_for_isolated_session(db):
    db.create_session("isolated", source="cli")
    assert db.resolve_resume_session_id("isolated") == "isolated"


def test_returns_self_for_nonexistent_session(db):
    assert db.resolve_resume_session_id("does_not_exist") == "does_not_exist"


def test_empty_session_id_passthrough(db):
    assert db.resolve_resume_session_id("") == ""
    assert db.resolve_resume_session_id(None) is None


def test_walks_from_middle_of_chain(db):
    # If the user happens to know an intermediate ID, we still find the msg-bearing descendant.
    _make_chain(db, [("a", None), ("b", "a"), ("c", "b"), ("d", "c")])
    db.append_message("d", role="user", content="x")
    assert db.resolve_resume_session_id("b") == "d"
    assert db.resolve_resume_session_id("c") == "d"


def test_ambiguous_compression_fork_fails_closed(db):
    _make_chain(db, [
        ("parent", None),
        ("older_fork", "parent"),
        ("newer_fork", "parent"),
    ])
    db.append_message("newer_fork", role="user", content="x")
    assert db.resolve_resume_session_id("parent") == "parent"
    assert db.resolve_resume_session_id("older_fork") == "older_fork"
    assert db.resolve_resume_session_id("newer_fork") == "newer_fork"


def test_delegate_child_is_not_a_resume_continuation(db):
    db.create_session("parent", source="cli")
    db.create_session("delegate", source="cli", parent_session_id="parent")
    db.append_message("delegate", role="user", content="delegate-only")

    assert db.resolve_resume_session_id("parent") == "parent"


def test_nested_ambiguous_compression_fork_fails_closed(db):
    _make_chain(db, [
        ("root", None),
        ("mid", "root"),
        ("fork_a", "mid"),
        ("fork_b", "mid"),
    ])
    db.append_message("fork_b", role="user", content="branch-only")

    assert db.resolve_resume_session_id("root") == "root"
    assert db.resolve_resume_session_id("mid") == "mid"
    assert db.resolve_resume_session_id("fork_a") == "fork_a"
