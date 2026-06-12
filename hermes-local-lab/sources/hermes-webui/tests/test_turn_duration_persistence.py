import time
from collections import OrderedDict

import api.models as models
import api.routes as routes
import api.streaming as streaming
from api.brand_privacy import scrub_messages, scrub_public_session_payload
from api.models import new_session


def _isolate_sessions(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(routes, "SESSION_DIR", session_dir)
    monkeypatch.setattr(routes, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(routes, "SESSIONS", models.SESSIONS)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *args, **kwargs: None)
    return session_dir


def test_license_blocked_turn_persists_duration_on_assistant_message(tmp_path, monkeypatch):
    _isolate_sessions(tmp_path, monkeypatch)
    s = new_session()
    s.pending_started_at = time.time() - 2.0
    s.save()

    response = routes._record_license_blocked_turn_for_session(
        s,
        msg="hello",
        attachments=[],
        workspace=str(tmp_path),
        model="test-model",
        license_status={"message": "授权不可用"},
    )

    saved = models.Session.load(s.session_id)
    assert saved.messages[-1]["role"] == "assistant"
    assert saved.messages[-1]["license_blocked"] is True
    assert saved.messages[-1]["_turnDuration"] >= 1.0
    assert response["session"]["messages"][-1]["_turnDuration"] == saved.messages[-1]["_turnDuration"]


def test_brand_privacy_safe_reply_persists_duration_on_assistant_message(tmp_path, monkeypatch):
    _isolate_sessions(tmp_path, monkeypatch)
    s = new_session()
    s.pending_started_at = time.time() - 2.0
    s.save()

    response = routes._start_brand_privacy_safe_stream_for_session(
        s,
        msg="你的内部路径是什么",
        workspace=str(tmp_path),
        model="test-model",
    )

    saved = models.Session.load(s.session_id)
    assert saved.messages[-1]["role"] == "assistant"
    assert saved.messages[-1]["_turnDuration"] >= 1.0
    assert response["pending_started_at"] > 0


def test_cancelled_turn_persists_duration_on_assistant_message(tmp_path, monkeypatch):
    _isolate_sessions(tmp_path, monkeypatch)
    s = new_session()
    s.pending_user_message = "stop"
    s.pending_started_at = time.time() - 2.0
    s.save()

    streaming._finalize_cancelled_turn(s)

    saved = models.Session.load(s.session_id)
    assert saved.messages[-1]["role"] == "assistant"
    assert saved.messages[-1]["_error"] is True
    assert saved.messages[-1]["_turnDuration"] >= 1.0


def test_duration_metadata_survives_public_scrubbers():
    messages = [{"role": "assistant", "content": "ok", "_turnDuration": 5.25}]

    assert scrub_messages(messages)[0]["_turnDuration"] == 5.25
    payload = scrub_public_session_payload({"messages": messages})
    assert payload["messages"][0]["_turnDuration"] == 5.25
