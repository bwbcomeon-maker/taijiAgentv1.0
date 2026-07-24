from __future__ import annotations

import copy
import hashlib
import io
import json
import threading
import unicodedata
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import pytest


class _RouteHandler:
    def __init__(self, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.status = None
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = self
        self.body = bytearray()

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json_body(self) -> dict:
        return json.loads(bytes(self.body).decode("utf-8"))


def _post(routes, body: dict) -> _RouteHandler:
    handler = _RouteHandler(body)
    routes.handle_post(handler, urlparse("/api/expert-teams/start"))
    return handler


def _get(routes, path: str) -> _RouteHandler:
    handler = _RouteHandler({})
    routes.handle_get(handler, urlparse(path))
    return handler


def _post_with_handler(routes, body: dict, handler_type):
    handler = handler_type(body)
    routes.handle_post(handler, urlparse("/api/expert-teams/start"))
    return handler


class _DisconnectingHandler(_RouteHandler):
    def write(self, _data):
        raise BrokenPipeError("client disconnected before reading response")


def _start_body(session_id: str, **overrides) -> dict:
    body = {
        "launch_profile_id": "content-work-report",
        "session_id": session_id,
        "prompt": "起草迎峰度夏保供电重点工作月度汇报",
        "idempotency_key": "start-atomic-work-report",
    }
    body.update(overrides)
    return body


@pytest.fixture
def atomic_env(monkeypatch, tmp_path):
    import api.config as config
    import api.models as models
    import api.routes as routes

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sessions = OrderedDict()
    for module in (config, models, routes):
        if hasattr(module, "SESSION_DIR"):
            monkeypatch.setattr(module, "SESSION_DIR", session_dir)
        if hasattr(module, "SESSION_INDEX_FILE"):
            monkeypatch.setattr(module, "SESSION_INDEX_FILE", session_dir / "_index.json")
        if hasattr(module, "SESSIONS"):
            monkeypatch.setattr(module, "SESSIONS", sessions)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: Path(value).resolve())
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "_replace_state_db_truth", lambda *_args, **_kwargs: True)
    return routes, models, sessions, tmp_path


def _new_memory_session(models, tmp_path, *, session_id: str):
    session = models.Session(
        session_id=session_id,
        workspace=str(tmp_path),
        profile="default",
    )
    models.SESSIONS[session.session_id] = session
    return session


def _persisted_session(models, tmp_path, *, session_id: str):
    session = models.Session(
        session_id=session_id,
        title="已有会话",
        workspace=str(tmp_path),
        profile="default",
        messages=[
            {"role": "user", "content": "已有问题"},
            {"role": "assistant", "content": "已有回答"},
        ],
    )
    session.context_messages = copy.deepcopy(session.messages)
    session.save(touch_updated_at=False, skip_index=True)
    models.SESSIONS[session.session_id] = session
    return session


def _public_run_files(workspace):
    return sorted((workspace / ".taiji" / "expert-teams" / "runs").glob("*.json"))


def _transaction_id(session_id: str, idempotency_key: str) -> str:
    identity = f"expert-team-start-v1\0{session_id}\0{idempotency_key}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _receipt_path(workspace, session_id: str, idempotency_key: str) -> Path:
    return (
        workspace
        / ".taiji"
        / "expert-teams"
        / "start-transactions"
        / f"{_transaction_id(session_id, idempotency_key)}.json"
    )


def _pending_run_files(workspace):
    return sorted(
        (
            workspace
            / ".taiji"
            / "expert-teams"
            / "start-transactions"
            / "pending"
        ).glob("*.json")
    )


def _run_messages(session, run_id: str):
    return [
        message
        for message in session.messages
        if message.get("expert_team_run_id") == run_id
    ]


def test_unknown_session_is_404_and_never_falls_back_to_last_workspace(atomic_env):
    routes, _models, _sessions, workspace = atomic_env
    routes.set_last_workspace(str(workspace))

    handler = _post(routes, _start_body("missing-session"))

    assert handler.status == 404
    assert handler.json_body()["code"] == "session_not_found"
    assert _public_run_files(workspace) == []


def test_same_start_key_replays_same_run_and_canonical_messages(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-replay")
    body = _start_body(session.session_id)

    first = _post(routes, body)
    second = _post(routes, body)

    assert first.status == second.status == 200
    first_payload = first.json_body()
    second_payload = second.json_body()
    assert first_payload["replayed"] is False
    assert second_payload["replayed"] is True
    assert second_payload["run"]["run_id"] == first_payload["run"]["run_id"]
    assert second_payload["session_messages"] == first_payload["session_messages"]
    assert second_payload["session"]["session_id"] == session.session_id
    assert len(_public_run_files(workspace)) == 1
    assert len(_run_messages(session, first_payload["run"]["run_id"])) == 2
    assert all(
        "expert_team_start_transaction_id" not in message
        for message in first_payload["session_messages"]
    )
    assert {
        message["expert_team_start_transaction_id"]
        for message in _run_messages(session, first_payload["run"]["run_id"])
    } == {
        _transaction_id(session.session_id, body["idempotency_key"])
    }


def test_unicode_equivalent_prompt_replays_same_normalized_request(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-unicode")
    composed = "起草 Café 月度汇报"
    decomposed = unicodedata.normalize("NFD", composed)
    body = _start_body(session.session_id, prompt=decomposed)

    first = _post(routes, body)
    replay = _post(routes, _start_body(session.session_id, prompt=composed))

    assert first.status == replay.status == 200
    assert replay.json_body()["replayed"] is True
    assert replay.json_body()["run"]["run_id"] == first.json_body()["run"]["run_id"]
    assert len(_public_run_files(workspace)) == 1


def test_same_key_with_different_fingerprint_is_409_without_mutation(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-conflict")
    body = _start_body(session.session_id)
    first = _post(routes, body)
    before_messages = copy.deepcopy(session.messages)

    conflict = _post(routes, _start_body(session.session_id, prompt="改成另一份汇报"))

    assert first.status == 200
    assert conflict.status == 409
    assert conflict.json_body()["code"] == "start_idempotency_conflict"
    assert session.messages == before_messages
    assert len(_public_run_files(workspace)) == 1


def test_different_keys_create_distinct_runs_for_same_session(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-distinct")

    first = _post(routes, _start_body(session.session_id, idempotency_key="start-distinct-one"))
    second = _post(routes, _start_body(session.session_id, idempotency_key="start-distinct-two"))

    assert first.status == second.status == 200
    assert first.json_body()["run"]["run_id"] != second.json_body()["run"]["run_id"]
    assert len(_public_run_files(workspace)) == 2
    assert len(session.messages) == 4


def test_new_session_persistence_failure_restores_memory_and_removes_run(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-new-rollback")
    before = copy.deepcopy(session.__dict__)
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("state db locked")),
    )

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 500
    assert handler.json_body()["code"] == "start_persistence_failed"
    assert session.__dict__ == before
    assert session.path.exists() is False
    assert _public_run_files(workspace) == []


def test_existing_session_failure_preserves_exact_sidecar_and_semantics(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-existing-rollback")
    before_bytes = session.path.read_bytes()
    before_state = copy.deepcopy(session.__dict__)
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("state db locked")),
    )

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 500
    assert session.path.read_bytes() == before_bytes
    assert session.__dict__ == before_state
    assert _public_run_files(workspace) == []


def test_failed_start_keeps_newer_durable_session_loaded_while_waiting_for_lock(
    atomic_env,
    monkeypatch,
):
    """A failed start must not restore a cache snapshot captured before locking."""
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-stale-rollback")
    original_lock = storage.start_transaction_lock
    external_messages = copy.deepcopy(session.messages) + [
        {"role": "user", "content": "另一进程已持久化的问题"},
        {"role": "assistant", "content": "另一进程已持久化的回答"},
    ]

    @contextmanager
    def commit_external_update_before_lock(*args, **kwargs):
        # _coordinate_expert_team_start has already resolved the cached Session
        # before entering this context manager.  This models another process
        # committing while the start request is waiting for the writer lock.
        external = models.Session.load(session.session_id)
        external.messages = copy.deepcopy(external_messages)
        external.context_messages = copy.deepcopy(external_messages)
        external.save(touch_updated_at=False, skip_index=True)
        with original_lock(*args, **kwargs):
            yield

    monkeypatch.setattr(storage, "start_transaction_lock", commit_external_update_before_lock)
    monkeypatch.setattr(
        routes,
        "_replace_state_db_truth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("state db locked")),
    )

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 500
    assert session.messages == external_messages
    assert models.Session.load(session.session_id).messages == external_messages
    assert _public_run_files(workspace) == []


def test_active_cached_session_is_rejected_without_losing_unsaved_turn(
    atomic_env,
):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-active-session")
    disk_before = session.path.read_bytes()
    session.messages.append({"role": "assistant", "content": "尚未落盘的流式输出"})
    session.context_messages = copy.deepcopy(session.messages)
    session.active_stream_id = "stream-active-start-conflict"
    session.pending_user_message = "正在处理的新问题"
    before = copy.deepcopy(session.__dict__)

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 409
    assert handler.json_body()["code"] == "session_busy"
    assert session.__dict__ == before
    assert session.path.read_bytes() == disk_before
    assert _public_run_files(workspace) == []


def test_cached_pending_attachments_make_session_busy_without_mutation(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-pending-files")
    disk_before = session.path.read_bytes()
    session.pending_attachments = [{"name": "待发送资料.docx"}]
    before = copy.deepcopy(session.__dict__)

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 409
    assert handler.json_body()["code"] == "session_busy"
    assert session.__dict__ == before
    assert session.path.read_bytes() == disk_before
    assert _public_run_files(workspace) == []


def test_fresh_sidecar_busy_state_is_rejected_without_overwrite(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-fresh-busy")
    cached_before = copy.deepcopy(session.__dict__)
    external = models.Session.load(session.session_id)
    external.pending_user_message = "另一进程正在处理"
    external.pending_started_at = 123.0
    external.save(touch_updated_at=False, skip_index=True)
    disk_before = session.path.read_bytes()

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 409
    assert handler.json_body()["code"] == "session_busy"
    assert session.__dict__ == cached_before
    assert session.path.read_bytes() == disk_before
    assert _public_run_files(workspace) == []


def test_inactive_unsaved_cache_tail_is_rejected_without_overwrite(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-unsaved-tail")
    disk_before = session.path.read_bytes()
    session.messages.append({"role": "user", "content": "尚未落盘的新问题"})
    session.context_messages = copy.deepcopy(session.messages)
    before = copy.deepcopy(session.__dict__)

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 409
    assert handler.json_body()["code"] == "session_state_conflict"
    assert session.__dict__ == before
    assert session.path.read_bytes() == disk_before
    assert _public_run_files(workspace) == []


def test_divergent_cached_session_is_rejected_without_overwrite(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-divergent-cache")
    disk_before = session.path.read_bytes()
    session.messages[-1] = {"role": "assistant", "content": "同长度但已分叉的回答"}
    session.context_messages = copy.deepcopy(session.messages)
    before = copy.deepcopy(session.__dict__)

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 409
    assert handler.json_body()["code"] == "session_state_conflict"
    assert session.__dict__ == before
    assert session.path.read_bytes() == disk_before
    assert _public_run_files(workspace) == []


def test_concurrent_same_key_creates_exactly_one_run_and_message_pair(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-concurrent")
    body = _start_body(session.session_id)
    barrier = threading.Barrier(2)
    handlers = []
    errors = []

    def start():
        try:
            barrier.wait(timeout=2)
            handlers.append(_post(routes, body))
        except BaseException as exc:  # pragma: no cover - diagnostic branch
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(thread.is_alive() is False for thread in threads)
    assert [handler.status for handler in handlers] == [200, 200]
    payloads = [handler.json_body() for handler in handlers]
    assert len({payload["run"]["run_id"] for payload in payloads}) == 1
    assert sorted(payload["replayed"] for payload in payloads) == [False, True]
    assert len(_public_run_files(workspace)) == 1
    assert len(_run_messages(session, payloads[0]["run"]["run_id"])) == 2


def test_same_key_is_scoped_by_session_identity(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    first_session = _new_memory_session(models, workspace, session_id="atomic-scope-one")
    second_session = _new_memory_session(models, workspace, session_id="atomic-scope-two")
    shared_key = "start-shared-across-sessions"

    first = _post(
        routes,
        _start_body(first_session.session_id, idempotency_key=shared_key),
    )
    second = _post(
        routes,
        _start_body(second_session.session_id, idempotency_key=shared_key),
    )

    assert first.status == second.status == 200
    assert first.json_body()["run"]["run_id"] != second.json_body()["run"]["run_id"]
    assert len(_public_run_files(workspace)) == 2
    assert len(first_session.messages) == len(second_session.messages) == 2


def test_concurrent_different_keys_on_same_session_keep_both_message_pairs(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-two-keys")
    bodies = [
        _start_body(session.session_id, idempotency_key="start-concurrent-key-one"),
        _start_body(session.session_id, idempotency_key="start-concurrent-key-two"),
    ]
    barrier = threading.Barrier(2)
    handlers = []
    errors = []

    def start(body):
        try:
            barrier.wait(timeout=2)
            handlers.append(_post(routes, body))
        except BaseException as exc:  # pragma: no cover - diagnostic branch
            errors.append(exc)

    threads = [threading.Thread(target=start, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(thread.is_alive() is False for thread in threads)
    assert sorted(handler.status for handler in handlers) == [200, 200]
    run_ids = {handler.json_body()["run"]["run_id"] for handler in handlers}
    assert len(run_ids) == 2
    assert len(_public_run_files(workspace)) == 2
    assert len(session.messages) == 4
    assert all(len(_run_messages(session, run_id)) == 2 for run_id in run_ids)


def test_different_key_refreshes_stale_process_cache_from_session_sidecar(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-stale-cache")

    first = _post(
        routes,
        _start_body(session.session_id, idempotency_key="start-stale-cache-one"),
    )
    assert first.status == 200
    # Simulate another desktop process whose Session cache predates the first
    # process's durable sidecar commit.
    session.messages = []
    session.context_messages = []

    second = _post(
        routes,
        _start_body(session.session_id, idempotency_key="start-stale-cache-two"),
    )

    assert second.status == 200
    assert len(session.messages) == 4
    assert len(models.Session.load(session.session_id).messages) == 4
    assert len(_public_run_files(workspace)) == 2


def test_post_commit_cleanup_fault_recovers_durable_pair_instead_of_rolling_back(
    atomic_env,
    monkeypatch,
):
    import api.truth_rewrite as truth_rewrite

    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-post-commit-cleanup")
    body = _start_body(session.session_id)
    original_replace = routes._replace_state_db_truth
    original_clear = truth_rewrite.clear_truth_rewrite_intent
    original_recover = truth_rewrite.recover_truth_rewrite_intent
    state_db_committed = False
    failed_once = False

    def replace(*args, **kwargs):
        nonlocal state_db_committed
        result = original_replace(*args, **kwargs)
        state_db_committed = True
        return result

    def clear(session_arg):
        nonlocal failed_once
        if state_db_committed and not failed_once:
            failed_once = True
            raise OSError("post-commit intent cleanup failed once")
        return original_clear(session_arg)

    def recover(session_arg):
        # atomic_env replaces the real state.db bridge with an in-memory
        # success seam.  Once the injected post-commit cleanup fault fires,
        # let Session.load exercise the durable sidecar without asking that
        # test seam for a state.db hash it intentionally does not maintain.
        if failed_once:
            return {"status": "none"}
        return original_recover(session_arg)

    monkeypatch.setattr(routes, "_replace_state_db_truth", replace)
    monkeypatch.setattr(truth_rewrite, "clear_truth_rewrite_intent", clear)
    monkeypatch.setattr(truth_rewrite, "recover_truth_rewrite_intent", recover)

    handler = _post(routes, body)

    assert handler.status == 200
    payload = handler.json_body()
    assert failed_once is True
    assert payload["replayed"] is False
    assert len(_public_run_files(workspace)) == 1
    assert len(_run_messages(session, payload["run"]["run_id"])) == 2

    replay = _post(routes, body)
    assert replay.status == 200
    assert replay.json_body()["replayed"] is True
    assert len(_public_run_files(workspace)) == 1
    assert len(_run_messages(session, payload["run"]["run_id"])) == 2


def test_truth_intent_is_not_cleared_when_committed_backup_cleanup_fails(
    atomic_env,
    monkeypatch,
):
    import api.truth_rewrite as truth_rewrite

    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-truth-cleanup")
    real_clear = truth_rewrite.clear_truth_rewrite_intent
    clear_calls = 0

    def fail_discard(_session):
        raise OSError("simulated committed backup cleanup failure")

    def record_clear(session_arg):
        nonlocal clear_calls
        clear_calls += 1
        return real_clear(session_arg)

    monkeypatch.setattr(
        truth_rewrite,
        "discard_committed_shrink_backup",
        fail_discard,
    )
    monkeypatch.setattr(truth_rewrite, "clear_truth_rewrite_intent", record_clear)

    routes._rewrite_existing_session_truth(
        session,
        lambda: session.messages.append(
            {"role": "assistant", "content": "已提交但清理尚待恢复"}
        ),
        privacy_reason=None,
    )

    assert clear_calls == 0
    assert truth_rewrite.truth_rewrite_intent_path(session).exists()


def test_response_disconnect_after_commit_retries_without_duplicate(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-disconnect")
    body = _start_body(session.session_id)

    disconnected = _post_with_handler(routes, body, _DisconnectingHandler)
    assert disconnected.status == 200
    assert disconnected.body == b""

    retry = _post(routes, body)

    assert retry.status == 200
    payload = retry.json_body()
    assert payload["replayed"] is True
    assert len(_public_run_files(workspace)) == 1
    assert len(_run_messages(session, payload["run"]["run_id"])) == 2


@pytest.mark.parametrize("receipt_kind", ["corrupt", "identity_mismatch"])
def test_invalid_receipt_fails_closed_without_public_mutation(atomic_env, receipt_kind):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id=f"atomic-receipt-{receipt_kind}")
    body = _start_body(session.session_id)
    path = _receipt_path(workspace, session.session_id, body["idempotency_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_kind == "corrupt":
        path.write_text("{not-json", encoding="utf-8")
    else:
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "transaction_id": "0" * 64,
                    "session_id": "another-session",
                    "idempotency_key_hash": "0" * 64,
                    "request_fingerprint": "0" * 64,
                    "run_id": "et-0123456789abcdef",
                    "state": "prepared",
                    "created_at": "2026-07-23T00:00:00Z",
                    "updated_at": "2026-07-23T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    handler = _post(routes, body)

    assert handler.status == 503
    assert handler.json_body()["code"] == "start_receipt_invalid"
    assert _public_run_files(workspace) == []
    assert session.messages == []


def test_tampered_committed_run_fails_closed_on_replay(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-run-tamper")
    body = _start_body(session.session_id)
    first = _post(routes, body)
    run_path = _public_run_files(workspace)[0]
    tampered = json.loads(run_path.read_text(encoding="utf-8"))
    tampered["prompt"] = "被篡改的另一个任务"
    run_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    before_messages = copy.deepcopy(session.messages)

    replay = _post(routes, body)

    assert first.status == 200
    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert session.messages == before_messages


def test_legitimately_advanced_run_still_replays_same_start_transaction(atomic_env):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-advanced-replay")
    body = _start_body(session.session_id)
    first = _post(routes, body)
    run_id = first.json_body()["run"]["run_id"]
    advanced = storage.read_run(workspace, run_id)
    advanced["version"] = 2
    advanced["workflow_state"] = "ready_to_generate"
    advanced["updated_at"] = "2026-07-23T12:00:00+08:00"
    storage.write_run(workspace, advanced)

    replay = _post(routes, body)

    assert first.status == replay.status == 200
    payload = replay.json_body()
    assert payload["replayed"] is True
    assert payload["run"]["run_id"] == run_id
    assert payload["run"]["version"] == 2
    assert payload["run"]["workflow_state"] == "ready_to_generate"
    assert len(_run_messages(session, run_id)) == 2


def test_stale_initial_pending_never_overwrites_legitimately_advanced_run(atomic_env):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-advanced-pending")
    body = _start_body(session.session_id)
    first = _post(routes, body)
    run_id = first.json_body()["run"]["run_id"]
    receipt = json.loads(
        _receipt_path(workspace, session.session_id, body["idempotency_key"]).read_text(
            encoding="utf-8"
        )
    )
    advanced = storage.read_run(workspace, run_id)
    advanced["version"] = 3
    advanced["workflow_state"] = "generating"
    advanced["updated_at"] = "2026-07-23T13:00:00+08:00"
    storage.write_run(workspace, advanced)
    storage.write_pending_run(workspace, receipt["initial_run_snapshot"])

    replay = _post(routes, body)

    assert replay.status == 200
    assert replay.json_body()["replayed"] is True
    assert replay.json_body()["run"]["version"] == 3
    assert storage.read_run(workspace, run_id)["workflow_state"] == "generating"
    assert _pending_run_files(workspace) == []


def test_publish_failure_precisely_rolls_back_and_same_key_can_retry(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-publish-failure")
    body = _start_body(session.session_id)
    real_publish = routes._publish_expert_team_start_run
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated canonical Run publish failure")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(routes, "_publish_expert_team_start_run", fail_once)

    failed_start = _post(routes, body)

    assert failed_start.status == 503
    assert failed_start.json_body()["code"] == "start_finalize_failed"
    assert session.messages == []
    assert session.title == "Untitled"
    assert _public_run_files(workspace) == []
    assert _pending_run_files(workspace) == []
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["state"] == "rolled_back"

    retry = _post(routes, body)

    assert retry.status == 200
    assert retry.json_body()["replayed"] is False
    assert len(_public_run_files(workspace)) == 1
    assert len(session.messages) == 2


def test_final_receipt_failure_precisely_rolls_back_and_same_key_can_retry(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-receipt-finalize")
    updated_at_before = session.updated_at
    last_message_at_before = session.compact()["last_message_at"]
    body = _start_body(session.session_id)
    real_write = storage.write_start_transaction
    writes = 0

    def fail_committed_write_once(workspace_arg, receipt):
        nonlocal writes
        writes += 1
        if writes == 3 and receipt.get("state") == "committed":
            raise OSError("simulated committed receipt write failure")
        return real_write(workspace_arg, receipt)

    monkeypatch.setattr(storage, "write_start_transaction", fail_committed_write_once)

    failed_start = _post(routes, body)

    assert failed_start.status == 503
    assert failed_start.json_body()["code"] == "start_finalize_failed"
    assert session.messages == []
    assert session.updated_at == updated_at_before
    assert session.compact()["last_message_at"] == last_message_at_before
    assert _public_run_files(workspace) == []
    assert _pending_run_files(workspace) == []
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["state"] == "rolled_back"

    retry = _post(routes, body)

    assert retry.status == 200
    assert retry.json_body()["replayed"] is False
    assert len(_public_run_files(workspace)) == 1
    assert len(session.messages) == 2


def test_finalize_compensation_preserves_newer_durable_timestamp_loaded_before_start(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(
        models,
        workspace,
        session_id="atomic-newer-durable-timestamp",
    )
    cached_updated_at = session.updated_at
    external = models.Session.load(session.session_id)
    external.updated_at = cached_updated_at + 60
    external.save(touch_updated_at=False, skip_index=True)
    external_updated_at = external.updated_at
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("simulated publish failure after Session commit")
        ),
    )

    failed = _post(routes, _start_body(session.session_id))

    assert failed.status == 503
    assert failed.json_body()["code"] == "start_finalize_failed"
    assert session.updated_at == external_updated_at
    assert models.Session.load(session.session_id).updated_at == external_updated_at


def test_crash_before_pending_publish_never_accepts_tampered_prepared_run(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-prepared-tamper")
    body = _start_body(session.session_id)
    real_write_receipt = storage.write_start_transaction
    writes = 0

    def crash_before_digest_receipt(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise SystemExit("simulated crash before prepared Run digest persisted")
        return real_write_receipt(*args, **kwargs)

    monkeypatch.setattr(storage, "write_start_transaction", crash_before_digest_receipt)
    with pytest.raises(SystemExit, match="before prepared Run digest"):
        _post(routes, body)

    pending_path = _pending_run_files(workspace)[0]
    tampered = json.loads(pending_path.read_text(encoding="utf-8"))
    tampered["team_id"] = "deep-research"
    tampered["review_policy"] = {"kind": "enterprise_approval"}
    pending_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(storage, "write_start_transaction", real_write_receipt)

    retry = _post(routes, body)

    assert retry.status == 503
    assert retry.json_body()["code"] == "start_receipt_invalid"
    assert _public_run_files(workspace) == []
    assert session.messages == []


def test_restart_recovery_cleans_run_only_then_commits_same_reservation(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-run-only")
    body = _start_body(session.session_id)
    real_writer = getattr(routes, "_persist_expert_team_session_entry_locked", lambda *_a, **_k: None)
    attempts = 0

    def crash_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SystemExit("simulated process death before session commit")
        return real_writer(*args, **kwargs)

    monkeypatch.setattr(
        routes,
        "_persist_expert_team_session_entry_locked",
        crash_once,
        raising=False,
    )
    with pytest.raises(SystemExit, match="before session commit"):
        _post(routes, body)

    assert _public_run_files(workspace) == []
    assert len(_pending_run_files(workspace)) == 1
    assert session.messages == []

    import api.expert_teams.storage as storage

    assert storage.list_runs(workspace) == []
    monkeypatch.setattr(
        "api.expert_teams.build_standalone_expert_team_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("recovery must reuse the immutable pending Run")
        ),
    )

    retry = _post(routes, body)

    assert retry.status == 200
    payload = retry.json_body()
    assert payload["replayed"] is False
    assert len(_public_run_files(workspace)) == 1
    assert _pending_run_files(workspace) == []
    assert len(_run_messages(session, payload["run"]["run_id"])) == 2


def test_restart_recovery_commits_run_and_session_pair_without_reappend(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-run-session")
    body = _start_body(session.session_id)
    real_publish = getattr(routes, "_publish_expert_team_start_run", lambda *_a, **_k: None)
    attempts = 0

    def crash_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SystemExit("simulated process death after session commit")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        crash_once,
        raising=False,
    )
    with pytest.raises(SystemExit, match="after session commit"):
        _post(routes, body)

    assert _public_run_files(workspace) == []
    assert len(_pending_run_files(workspace)) == 1
    run_id = _pending_run_files(workspace)[0].stem
    assert len(_run_messages(session, run_id)) == 2
    assert routes._expert_team_start_public_session(session)["messages"] == []

    retry = _post(routes, body)

    assert retry.status == 200
    payload = retry.json_body()
    assert payload["replayed"] is True
    assert payload["run"]["run_id"] == run_id
    assert len(_public_run_files(workspace)) == 1
    assert _pending_run_files(workspace) == []
    assert len(_run_messages(session, run_id)) == 2


def test_public_session_read_lazily_finalizes_prepared_start_after_process_death(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-lazy-reconcile")
    body = _start_body(session.session_id)
    real_publish = routes._publish_expert_team_start_run

    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("simulated process death after Session commit")
        ),
    )
    with pytest.raises(SystemExit, match="after Session commit"):
        _post(routes, body)
    monkeypatch.setattr(routes, "_publish_expert_team_start_run", real_publish)

    assert routes._expert_team_start_public_session(session)["messages"] == []
    response = _get(
        routes,
        f"/api/session?session_id={session.session_id}&messages=1",
    )

    assert response.status == 200
    payload = response.json_body()["session"]
    assert len(payload["messages"]) == 2
    assert _pending_run_files(workspace) == []
    assert len(_public_run_files(workspace)) == 1
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["state"] == "committed"


def test_same_messages_with_changed_durable_identity_never_write_old_workspace(
    atomic_env,
    tmp_path,
):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-identity-drift")
    replacement_workspace = tmp_path / "replacement-workspace"
    replacement_workspace.mkdir()
    fresh = models.Session.load(session.session_id)
    fresh.workspace = str(replacement_workspace)
    fresh.title = "磁盘端新标题"
    fresh.model = "disk-model"
    fresh.save(touch_updated_at=False, skip_index=True)

    response = _post(routes, _start_body(session.session_id))

    assert response.status == 409
    assert response.json_body()["code"] == "session_state_conflict"
    assert session.workspace == str(replacement_workspace.resolve())
    assert session.title == "磁盘端新标题"
    assert session.model == "disk-model"
    assert _public_run_files(workspace) == []
    assert _public_run_files(replacement_workspace) == []


def test_disk_append_never_discards_unsaved_cache_tool_or_context_tail(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-unsaved-aux-tail")
    session.context_messages.append({"role": "user", "content": "未落盘 context tail"})
    session.tool_calls.append({"id": "unsaved-tool-tail", "name": "local-tool"})
    cache_before = copy.deepcopy(session.__dict__)
    fresh = models.Session.load(session.session_id)
    fresh.messages.extend(
        [
            {"role": "user", "content": "另一进程追加的问题"},
            {"role": "assistant", "content": "另一进程追加的回答"},
        ]
    )
    fresh.context_messages = copy.deepcopy(fresh.messages)
    fresh.save(touch_updated_at=False, skip_index=True)

    response = _post(routes, _start_body(session.session_id))

    assert response.status == 409
    assert response.json_body()["code"] == "session_state_conflict"
    assert session.__dict__ == cache_before
    assert models.Session.load(session.session_id).messages == fresh.messages
    assert _public_run_files(workspace) == []


def test_durable_metadata_drift_refreshes_cache_then_retry_preserves_disk_truth(
    atomic_env,
):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-metadata-drift")
    fresh = models.Session.load(session.session_id)
    fresh.title = "外部持久标题"
    fresh.model = "external-durable-model"
    fresh.save(touch_updated_at=False, skip_index=True)

    first = _post(routes, _start_body(session.session_id))

    assert first.status == 409
    assert first.json_body()["code"] == "session_state_conflict"
    assert session.title == "外部持久标题"
    assert session.model == "external-durable-model"

    second = _post(routes, _start_body(session.session_id))

    assert second.status == 200
    reloaded = models.Session.load(session.session_id)
    assert reloaded.model == "external-durable-model"
    assert reloaded.title == "外部持久标题"


def test_prepared_canonical_is_hidden_from_storage_get_list_latest_and_mutation(
    atomic_env,
    monkeypatch,
):
    from api import expert_teams
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-prepared-hidden")
    body = _start_body(session.session_id)
    real_write = storage.write_start_transaction
    writes = 0

    def crash_before_commit_receipt(workspace_arg, receipt):
        nonlocal writes
        writes += 1
        if writes == 3 and receipt.get("state") == "committed":
            raise SystemExit("crash after canonical publish")
        return real_write(workspace_arg, receipt)

    monkeypatch.setattr(storage, "write_start_transaction", crash_before_commit_receipt)
    with pytest.raises(SystemExit, match="after canonical publish"):
        _post(routes, body)
    monkeypatch.setattr(storage, "write_start_transaction", real_write)
    run_id = _public_run_files(workspace)[0].stem

    assert hasattr(storage, "read_start_transaction_for_run")
    assert storage.read_start_transaction_for_run(workspace, run_id)["state"] == "prepared"
    with pytest.raises(FileNotFoundError):
        storage.read_run(workspace, run_id)
    assert storage.list_runs(workspace) == []
    with pytest.raises(FileNotFoundError):
        storage.latest_run_for_session(workspace, session.session_id)
    raw_reader = getattr(storage, "read_run_raw", storage.read_run)
    raw = raw_reader(workspace, run_id)
    control = {
        "run_id": run_id,
        "session_id": session.session_id,
        "expected_version": raw["version"],
        "stage_id": raw["current_stage"]["task_id"],
        "idempotency_key": "prepared-mutation-must-fail",
        "answers": {},
    }
    with pytest.raises(FileNotFoundError):
        expert_teams.answer_expert_team(workspace, control)


def test_run_id_only_get_fails_closed_when_prepared_session_is_busy(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-run-id-only-busy")
    body = _start_body(session.session_id)
    real_write = storage.write_start_transaction
    writes = 0

    def crash_before_commit_receipt(workspace_arg, receipt):
        nonlocal writes
        writes += 1
        if writes == 3 and receipt.get("state") == "committed":
            raise SystemExit("crash after canonical publish")
        return real_write(workspace_arg, receipt)

    monkeypatch.setattr(storage, "write_start_transaction", crash_before_commit_receipt)
    with pytest.raises(SystemExit):
        _post(routes, body)
    monkeypatch.setattr(storage, "write_start_transaction", real_write)
    run_id = _public_run_files(workspace)[0].stem
    routes.set_last_workspace(str(workspace))
    session.active_stream_id = "busy-during-run-read"

    response = _get(routes, f"/api/expert-teams/run?run_id={run_id}")

    assert response.status == 404
    assert storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )["state"] == "prepared"


def test_advanced_canonical_is_preserved_and_receipt_marks_recovery_required(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-advanced-compensation")
    body = _start_body(session.session_id)
    real_write_receipt = storage.write_start_transaction
    writes = 0

    def advance_then_fail_commit(workspace_arg, receipt):
        nonlocal writes
        writes += 1
        if writes == 3 and receipt.get("state") == "committed":
            raw_reader = getattr(storage, "read_run_raw", storage.read_run)
            advanced = raw_reader(workspace_arg, receipt["run_id"])
            advanced["version"] = 7
            advanced["workflow_state"] = "generating"
            storage.write_run(workspace_arg, advanced)
            raise OSError("commit receipt failed after another writer advanced Run")
        return real_write_receipt(workspace_arg, receipt)

    monkeypatch.setattr(storage, "write_start_transaction", advance_then_fail_commit)

    response = _post(routes, body)

    assert response.status == 503
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["state"] == "recovery_required"
    raw_reader = getattr(storage, "read_run_raw", storage.read_run)
    assert raw_reader(workspace, receipt["run_id"])["version"] == 7
    assert len(_run_messages(session, receipt["run_id"])) == 2


def test_failed_run_cleanup_keeps_session_recovery_marker_discoverable(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-cleanup-order")
    body = _start_body(session.session_id)
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )
    monkeypatch.setattr(
        storage,
        "delete_pending_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    response = _post(routes, body)

    assert response.status == 503
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["state"] == "recovery_required"
    assert len(_run_messages(session, receipt["run_id"])) == 2
    assert len(_pending_run_files(workspace)) == 1


@pytest.mark.parametrize("cold_cache", [False, True])
def test_metadata_only_session_get_hides_prepared_title_and_message_count(
    atomic_env,
    monkeypatch,
    cold_cache,
):
    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id=f"atomic-metadata-{cold_cache}")
    body = _start_body(session.session_id)
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("crash after Session commit")
        ),
    )
    with pytest.raises(SystemExit):
        _post(routes, body)
    if cold_cache:
        sessions.clear()

    response = _get(
        routes,
        f"/api/session?session_id={session.session_id}&messages=0",
    )

    assert response.status == 200
    public = response.json_body()["session"]
    assert public["title"] == "Untitled"
    assert public["message_count"] == 0


def test_sidebar_session_row_hides_prepared_title_and_message_count(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-sidebar-prepared")
    body = _start_body(session.session_id)
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("crash after Session commit")
        ),
    )
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    with pytest.raises(SystemExit):
        _post(routes, body)

    response = _get(routes, "/api/sessions")

    assert response.status == 200
    row = next(
        item
        for item in response.json_body()["sessions"]
        if item["session_id"] == session.session_id
    )
    assert row["title"] == "Untitled"
    assert row["message_count"] == 0


def test_public_projection_reads_each_start_receipt_once_and_fails_safe(
    monkeypatch,
    tmp_path,
):
    from api import brand_privacy
    import api.expert_teams.storage as storage

    transaction_id = "a" * 64
    messages = [
        {
            "role": role,
            "content": content,
            "expert_team_run_id": "et-0123456789abcdef",
            "expert_team_start_transaction_id": transaction_id,
        }
        for role, content in (("user", "start"), ("assistant", "started"))
    ]
    calls = 0

    def committed(_workspace, requested_transaction_id):
        nonlocal calls
        calls += 1
        assert requested_transaction_id == transaction_id
        return {
            "transaction_id": transaction_id,
            "state": "committed",
            "session_id": "projection-session",
            "run_id": "et-0123456789abcdef",
        }

    monkeypatch.setattr(storage, "read_start_transaction_by_id", committed)
    payload = {
        "session_id": "projection-session",
        "workspace": str(tmp_path),
        "message_count": 2,
        "messages": messages,
    }

    projected = brand_privacy.public_session_projection(payload)

    assert len(projected["messages"]) == 2
    assert calls == 1

    monkeypatch.setattr(
        storage,
        "read_start_transaction_by_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("temporary read error")),
    )
    projected_on_error = brand_privacy.public_session_projection(payload)
    assert len(projected_on_error["messages"]) == 2
    assert projected_on_error["message_count"] == 2

    never_verified_payload = copy.deepcopy(payload)
    never_verified_payload["messages"] = [
        {
            **message,
            "expert_team_start_transaction_id": "b" * 64,
            "expert_team_run_id": "et-fedcba9876543210",
        }
        for message in messages
    ]
    never_verified = brand_privacy.public_session_projection(never_verified_payload)
    assert never_verified["messages"] == []
    assert never_verified["message_count"] == 0


def test_initial_receipt_write_crash_never_publishes_dangling_session_binding(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-receipt-write-order")
    body = _start_body(session.session_id)
    transaction_id = storage.start_transaction_id(session.session_id, body["idempotency_key"])
    real_write_json = storage._write_json_atomic
    writes = []

    def crash_on_third_write(path, payload):
        writes.append(Path(path))
        if len(writes) == 3:
            raise SystemExit("simulated crash on third transaction metadata write")
        return real_write_json(path, payload)

    monkeypatch.setattr(storage, "_write_json_atomic", crash_on_third_write)
    with pytest.raises(SystemExit, match="third transaction metadata write"):
        _post(routes, body)

    receipt_path = storage.start_transaction_path(workspace, transaction_id)
    session_binding = storage.start_session_binding_path(workspace, session.session_id)
    assert receipt_path.is_file()
    assert session_binding.exists() is False
    assert session.messages == []

    monkeypatch.setattr(storage, "_write_json_atomic", real_write_json)
    retry = _post(routes, body)

    assert retry.status == 200
    receipts = storage.list_start_transactions_for_session(workspace, session.session_id)
    assert [item["transaction_id"] for item in receipts] == [transaction_id]


@pytest.mark.parametrize("wrapped_storage_error", [False, True])
def test_transient_session_receipt_batch_failure_uses_complete_committed_cache(
    atomic_env,
    monkeypatch,
    wrapped_storage_error,
):
    from api import brand_privacy
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-batch-cache")
    response = _post(routes, _start_body(session.session_id))
    assert response.status == 200
    payload = session.compact() | {"messages": copy.deepcopy(session.messages)}

    warmed = brand_privacy.public_session_projection(payload)
    assert len(warmed["messages"]) == 2
    expected_title = warmed["title"]

    def transient_read_failure(*_args, **_kwargs):
        error = OSError("temporary receipt read failure")
        if wrapped_storage_error:
            raise storage.StartTransactionIntegrityError(
                "start transaction receipt is unreadable"
            ) from error
        raise error

    monkeypatch.setattr(
        storage,
        "list_start_transactions_for_session",
        transient_read_failure,
    )
    monkeypatch.setattr(
        storage,
        "read_start_transaction_by_id",
        transient_read_failure,
    )

    recovered = brand_privacy.public_session_projection(payload)

    assert recovered["messages"] == warmed["messages"]
    assert recovered["message_count"] == 2
    assert recovered["title"] == expected_title


@pytest.mark.parametrize("failure_kind", ["tampered", "missing"])
def test_authoritative_receipt_failure_invalidates_committed_projection_cache(
    atomic_env,
    monkeypatch,
    failure_kind,
):
    from api import brand_privacy
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-cache-integrity")
    body = _start_body(session.session_id)
    response = _post(routes, body)
    assert response.status == 200
    payload = session.compact() | {"messages": copy.deepcopy(session.messages)}

    warmed = brand_privacy.public_session_projection(payload)
    assert len(warmed["messages"]) == 2

    receipt_path = _receipt_path(
        workspace,
        session.session_id,
        body["idempotency_key"],
    )
    if failure_kind == "tampered":
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["session_metadata_before_start"]["message_count"] = "forged"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    else:
        receipt_path.unlink()

    rejected = brand_privacy.public_session_projection(payload)

    assert rejected["messages"] == []
    assert rejected["message_count"] == 0

    monkeypatch.setattr(
        storage,
        "list_start_transactions_for_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt unavailable")),
    )
    monkeypatch.setattr(
        storage,
        "read_start_transaction_by_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt unavailable")),
    )

    still_rejected = brand_privacy.public_session_projection(payload)
    assert still_rejected["messages"] == []
    assert still_rejected["message_count"] == 0


def test_prepared_projection_restores_four_message_snapshot_without_double_subtract(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _persisted_session(models, workspace, session_id="atomic-four-history")
    session.messages.extend(
        [
            {"role": "user", "content": "第二个已有问题"},
            {"role": "assistant", "content": "第二个已有回答"},
        ]
    )
    session.context_messages = copy.deepcopy(session.messages)
    session.save(touch_updated_at=False, skip_index=True)
    title_before = session.title
    body = _start_body(session.session_id)
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("crash after Session commit")),
    )

    with pytest.raises(SystemExit, match="after Session commit"):
        _post(routes, body)

    public = routes._expert_team_start_public_session(session)
    assert len(public["messages"]) == 4
    assert public["message_count"] == 4
    assert public["title"] == title_before

    metadata_response = _get(
        routes,
        f"/api/session?session_id={session.session_id}&messages=0",
    )
    assert metadata_response.status == 200
    metadata = metadata_response.json_body()["session"]
    assert metadata["message_count"] == 4
    assert metadata["title"] == title_before

    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    sidebar_response = _get(routes, "/api/sessions")
    assert sidebar_response.status == 200
    sidebar_row = next(
        item
        for item in sidebar_response.json_body()["sessions"]
        if item["session_id"] == session.session_id
    )
    assert sidebar_row["message_count"] == 4
    assert sidebar_row["title"] == title_before


def test_full_projection_preserves_later_committed_start_after_earlier_prepared(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-prepared-before-committed",
    )
    first_body = _start_body(
        session.session_id,
        idempotency_key="prepared-first-start",
        prompt="失败后待恢复的第一个专家团任务",
    )
    real_publish = routes._publish_expert_team_start_run
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("leave first start prepared")
        ),
    )
    with pytest.raises(SystemExit, match="leave first start prepared"):
        _post(routes, first_body)
    monkeypatch.setattr(routes, "_publish_expert_team_start_run", real_publish)

    second = _post(
        routes,
        _start_body(
            session.session_id,
            idempotency_key="committed-second-start",
            prompt="已提交的第二个专家团任务",
        ),
    )
    assert second.status == 200
    second_run_id = second.json_body()["run"]["run_id"]

    public = routes._expert_team_start_public_session(session)

    assert len(public["messages"]) == 2
    assert "第二个专家团任务" in public["messages"][0]["content"]
    assert public["message_count"] == 2
    assert public["user_message_count"] == 1
    assert public["title"] == routes.title_from(
        _run_messages(session, second_run_id),
        "Untitled",
    )
    assert public["updated_at"] == session.updated_at
    assert public["last_message_at"] == session.compact()["last_message_at"]


def test_receipt_metadata_snapshot_tamper_is_rejected_at_storage_boundary(
    atomic_env,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-metadata-tamper")
    body = _start_body(session.session_id)
    response = _post(routes, body)
    assert response.status == 200
    path = _receipt_path(workspace, session.session_id, body["idempotency_key"])
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["session_metadata_before_start"]["message_count"] = "forged"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.read_start_transaction_by_id(workspace, receipt["transaction_id"])


def test_receipt_owned_session_timestamp_must_be_finite(
    atomic_env,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-owned-time-tamper")
    body = _start_body(session.session_id)
    response = _post(routes, body)
    assert response.status == 200
    path = _receipt_path(workspace, session.session_id, body["idempotency_key"])
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["session_updated_at_after_start"] = float("nan")
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.read_start_transaction_by_id(workspace, receipt["transaction_id"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_at", "not-a-timestamp"),
        ("updated_at", float("nan")),
    ],
)
def test_receipt_transaction_timestamps_must_be_finite(
    atomic_env,
    field,
    value,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id=f"atomic-time-{field}")
    body = _start_body(session.session_id)
    response = _post(routes, body)
    assert response.status == 200
    path = _receipt_path(workspace, session.session_id, body["idempotency_key"])
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt[field] = value
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.read_start_transaction_by_id(workspace, receipt["transaction_id"])


def test_late_t1_compensation_recomputes_title_from_committed_t2(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-late-t1-rollback")
    first_body = _start_body(
        session.session_id,
        idempotency_key="late-t1-start",
        prompt="失败的第一个专家团任务，请起草一份完整的迎峰度夏保供电重点工作月度汇报",
    )
    real_publish = routes._publish_expert_team_start_run
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("leave T1 prepared")),
    )
    with pytest.raises(SystemExit, match="leave T1 prepared"):
        _post(routes, first_body)
    monkeypatch.setattr(routes, "_publish_expert_team_start_run", real_publish)
    first_receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=first_body["idempotency_key"],
    )

    second_body = _start_body(
        session.session_id,
        idempotency_key="late-t2-start",
        prompt="成功的第二个专家团任务，请起草一份完整的迎峰度夏保供电重点工作月度汇报",
    )
    second = _post(routes, second_body)
    assert second.status == 200
    second_run_id = second.json_body()["run"]["run_id"]
    second_updated_at = session.updated_at
    second_last_message_at = session.compact()["last_message_at"]

    routes._compensate_expert_team_start_finalize_locked(
        workspace,
        session,
        first_receipt,
    )

    remaining = _run_messages(session, second_run_id)
    assert len(remaining) == 2
    assert session.title == routes.title_from(remaining, "Untitled")
    assert session.updated_at == max(second_updated_at, second_last_message_at)
    assert session.compact()["last_message_at"] == second_last_message_at
    assert models.Session.load(session.session_id).title == session.title


def test_late_compensation_preserves_newer_user_rename_timestamp(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-late-user-rename")
    body = _start_body(
        session.session_id,
        idempotency_key="late-user-rename-start",
        prompt="请起草一份完整的迎峰度夏保供电重点工作月度汇报，用于验证失败补偿",
    )
    real_publish = routes._publish_expert_team_start_run
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("leave prepared before rename")),
    )
    with pytest.raises(SystemExit, match="leave prepared before rename"):
        _post(routes, body)
    monkeypatch.setattr(routes, "_publish_expert_team_start_run", real_publish)

    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert session.updated_at == receipt["session_updated_at_after_start"]
    session.title = "用户稍后手动重命名的会话"
    session.save(skip_index=True)
    renamed_updated_at = session.updated_at

    routes._compensate_expert_team_start_finalize_locked(
        workspace,
        session,
        receipt,
    )

    assert session.title == "用户稍后手动重命名的会话"
    assert session.updated_at == renamed_updated_at
    reloaded = models.Session.load(session.session_id)
    assert reloaded.title == session.title
    assert reloaded.updated_at == renamed_updated_at
