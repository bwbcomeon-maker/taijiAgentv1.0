from __future__ import annotations

from collections import OrderedDict

import pytest


@pytest.fixture
def isolated_title_session(monkeypatch, tmp_path):
    import api.models as models
    import api.streaming as streaming

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sessions = OrderedDict()

    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", sessions)
    monkeypatch.setattr(streaming, "SESSIONS", sessions)

    session = models.Session(
        session_id="background-title-cas",
        title="请总结这次对话",
        workspace=str(tmp_path),
        profile="default",
        messages=[
            {"role": "user", "content": "请总结这次对话"},
            {"role": "assistant", "content": "这是回复。"},
        ],
        llm_title_generated=False,
    )
    session.save(skip_index=True)
    sessions[session.session_id] = session
    return models, streaming, sessions, session


@pytest.mark.parametrize("concurrent_change", ["deleted", "tombstoned", "updated"])
def test_background_title_update_skips_stale_write_conflict(
    monkeypatch,
    isolated_title_session,
    concurrent_change,
):
    """A detached title worker must not resurrect or overwrite newer truth."""
    models, streaming, sessions, stale_session = isolated_title_session
    original_title = stale_session.title
    original_generated = stale_session.llm_title_generated
    durable_external_title = "用户在另一个进程中修改的标题"

    def generate_title_after_concurrent_change(*_args, **_kwargs):
        if concurrent_change in {"deleted", "tombstoned"}:
            if concurrent_change == "tombstoned":
                # Real WebUI DELETE marks the cached object before evicting it.
                # Session.save() rejects this before reaching its disk CAS.
                stale_session._deleted = True
            stale_session.path.unlink()
            sessions.pop(stale_session.session_id, None)
        else:
            external_session = models.Session.load(stale_session.session_id)
            assert external_session is not None
            external_session.title = durable_external_title
            external_session.llm_title_generated = True
            external_session.save(touch_updated_at=False, skip_index=True)
        return "后台生成的过期标题", "llm_aux", ""

    monkeypatch.setattr(streaming, "_aux_title_configured", lambda: True)
    monkeypatch.setattr(
        streaming,
        "_generate_llm_session_title_via_aux",
        generate_title_after_concurrent_change,
    )

    events = []
    streaming._run_background_title_update(
        session_id=stale_session.session_id,
        user_text="请总结这次对话",
        assistant_text="这是回复。",
        placeholder_title=original_title,
        put_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert stale_session.title == original_title
    assert stale_session.llm_title_generated is original_generated
    assert [payload for event, payload in events if event == "title"] == []
    assert [payload for event, payload in events if event == "title_status"] == [
        {
            "session_id": stale_session.session_id,
            "status": "skipped",
            "reason": "session_changed",
        }
    ]
    assert [payload for event, payload in events if event == "stream_end"] == [
        {"session_id": stale_session.session_id}
    ]

    if concurrent_change in {"deleted", "tombstoned"}:
        assert stale_session.path.exists() is False
    else:
        durable_session = models.Session.load(stale_session.session_id)
        assert durable_session is not None
        assert durable_session.title == durable_external_title
        assert durable_session.llm_title_generated is True


def test_background_title_update_does_not_swallow_unexpected_save_error(
    monkeypatch,
    isolated_title_session,
):
    """Only the expected CAS race is a valid background-worker skip."""
    _models, streaming, _sessions, session = isolated_title_session
    monkeypatch.setattr(streaming, "_aux_title_configured", lambda: True)
    monkeypatch.setattr(
        streaming,
        "_generate_llm_session_title_via_aux",
        lambda *_args, **_kwargs: ("新标题", "llm_aux", ""),
    )
    monkeypatch.setattr(
        session,
        "save",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected write failure")),
    )

    events = []
    with pytest.raises(RuntimeError, match="unexpected write failure"):
        streaming._run_background_title_update(
            session_id=session.session_id,
            user_text="请总结这次对话",
            assistant_text="这是回复。",
            placeholder_title=session.title,
            put_event=lambda event_type, payload: events.append((event_type, payload)),
        )

    assert [payload for event, payload in events if event == "stream_end"] == [
        {"session_id": session.session_id}
    ]
