from __future__ import annotations

import copy
import hashlib
import io
import json
import multiprocessing
import os
import subprocess
import sys
import textwrap
import threading
import time
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
    import api.state_sync as state_sync
    from hermes_state import SessionDB

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
    state_db_path = tmp_path / "state.db"
    monkeypatch.setattr(
        state_sync,
        "_get_state_db",
        lambda *_args, **_kwargs: SessionDB(state_db_path),
    )
    return routes, models, sessions, tmp_path


def _new_memory_session(models, tmp_path, *, session_id: str):
    session = models.Session(
        session_id=session_id,
        workspace=str(tmp_path),
        profile="default",
    )
    session.save(touch_updated_at=False, skip_index=True)
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


def _canonical_json_digest(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert_no_public_start_transaction_markers(value, path: str = "$") -> None:
    forbidden = {
        "start_transaction_id",
        "expert_team_start_transaction_id",
        "expert_team_start_transaction_ids",
    }
    if isinstance(value, dict):
        leaked = forbidden.intersection(value)
        assert not leaked, f"internal start marker leaked at {path}: {sorted(leaked)}"
        for key, item in value.items():
            _assert_no_public_start_transaction_markers(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_public_start_transaction_markers(item, f"{path}[{index}]")


def _downgrade_start_transaction_to_v1(
    storage,
    workspace: Path,
    *,
    session_id: str,
    idempotency_key: str,
) -> dict:
    transaction_id = storage.start_transaction_id(session_id, idempotency_key)
    receipt_path = storage.start_transaction_path(workspace, transaction_id)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["schema_version"] = 1
    receipt.pop("initial_session_message_pair_snapshot", None)
    receipt.pop("initial_session_message_pair_sha256", None)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    run_binding_path = storage.start_run_binding_path(
        workspace,
        str(receipt["run_id"]),
    )
    run_binding = json.loads(run_binding_path.read_text(encoding="utf-8"))
    run_binding["schema_version"] = 1
    run_binding["receipt_sha256"] = storage.start_receipt_digest(receipt)
    run_binding.pop("previous_receipt_sha256", None)
    run_binding_path.write_text(
        json.dumps(run_binding, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    session_binding_path = storage.start_session_binding_path(workspace, session_id)
    if session_binding_path.exists():
        session_binding = json.loads(
            session_binding_path.read_text(encoding="utf-8")
        )
        session_binding["schema_version"] = 1
        session_binding_path.write_text(
            json.dumps(session_binding, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return receipt


def _hold_cross_process_lock(kind, workspace, identifier, ready, release, result):
    """Fork worker used to prove the OS locks, not only thread locks."""
    try:
        if kind == "session":
            from api.truth_rewrite import truth_rewrite_lock

            manager = truth_rewrite_lock(identifier, timeout_seconds=2)
        else:
            from api.expert_teams.storage import run_file_lock

            manager = run_file_lock(Path(workspace), identifier, timeout_seconds=2)
        with manager:
            ready.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test release signal timed out")
        result.put(("ok", ""))
    except BaseException as exc:  # pragma: no cover - child diagnostic path
        result.put(("error", f"{type(exc).__name__}: {exc}"))


def _save_stale_session_after_signal(session_id, ready, proceed, result):
    try:
        from api.models import Session

        stale = Session.load(session_id)
        if stale is None:
            raise RuntimeError("session not found")
        ready.set()
        if not proceed.wait(timeout=5):
            raise TimeoutError("test proceed signal timed out")
        stale.messages.extend(
            [
                {"role": "user", "content": "过期进程的问题"},
                {"role": "assistant", "content": "过期进程的回答"},
            ]
        )
        stale.context_messages = copy.deepcopy(stale.messages)
        stale.title = "过期进程标题"
        stale.input_tokens = 999
        stale.save(touch_updated_at=False, skip_index=True)
        result.put(("saved", ""))
    except BaseException as exc:  # pragma: no cover - child diagnostic path
        result.put(("error", type(exc).__name__))


def _commit_ordinary_session_before_start(session_id, ready, result):
    try:
        import api.routes as routes
        from api.models import Session

        ordinary = Session.load(session_id)
        if ordinary is None:
            raise RuntimeError("session not found")

        def mutate():
            ready.set()
            # Keep the durable writer lock long enough for the parent request
            # to contend on the OS lock, then atomically commit ordinary truth.
            time.sleep(0.25)
            ordinary.messages.extend(
                [
                    {"role": "user", "content": "普通写入的问题"},
                    {"role": "assistant", "content": "普通写入的回答"},
                ]
            )
            ordinary.context_messages = copy.deepcopy(ordinary.messages)
            ordinary.title = "普通写入后的标题"
            ordinary.input_tokens = 123
            ordinary.output_tokens = 456

        routes._rewrite_existing_session_truth(
            ordinary,
            mutate,
            privacy_reason=None,
            touch_updated_at=False,
        )
        result.put(("ok", ""))
    except BaseException as exc:  # pragma: no cover - child diagnostic path
        result.put(("error", f"{type(exc).__name__}: {exc}"))


@pytest.mark.parametrize(
    "raw_markers",
    [
        "not-a-list",
        {"transaction_id": "not-a-list"},
        ["a" * 64, "a" * 64],
        ["a" * 64, 7],
    ],
)
def test_session_model_preserves_raw_start_transaction_marker_evidence(
    atomic_env,
    raw_markers,
):
    _routes, models, _sessions, workspace = atomic_env

    session = models.Session(
        session_id="atomic-raw-marker-shape",
        workspace=str(workspace),
        profile="default",
        expert_team_start_transaction_ids=raw_markers,
    )

    assert session.expert_team_start_transaction_ids == raw_markers
    assert type(session.expert_team_start_transaction_ids) is type(raw_markers)


@pytest.mark.parametrize(
    "raw_markers",
    [
        None,
        "a" * 64,
        {"transaction_id": "a" * 64},
        ["not-a-sha256"],
        ["a" * 64, 7],
        ["a" * 64, "a" * 64],
        ["b" * 64],
    ],
)
def test_start_transaction_marker_validator_rejects_malformed_or_wrong_evidence(
    raw_markers,
):
    from api.expert_teams import storage

    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.validate_start_session_transaction_markers(
            raw_markers,
            required_transaction_id="a" * 64,
        )


def test_start_transaction_marker_validator_preserves_valid_order_and_values():
    from api.expert_teams import storage

    raw_markers = ["b" * 64, "a" * 64]

    validated = storage.validate_start_session_transaction_markers(
        raw_markers,
        required_transaction_id="a" * 64,
    )

    assert validated == raw_markers
    assert validated is not raw_markers


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


def test_start_receipt_reserves_the_exact_session_message_pair_once(atomic_env):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-exact-message-snapshot",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="exact-message-snapshot",
    )

    started = _post(routes, body)

    assert started.status == 200
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    transaction_id = receipt["transaction_id"]
    durable_pair = [
        copy.deepcopy(message)
        for message in models.Session.load(session.session_id).messages
        if message.get("expert_team_start_transaction_id") == transaction_id
    ]
    assert receipt["schema_version"] == 2
    assert receipt["initial_session_message_pair_snapshot"] == durable_pair
    assert receipt["initial_session_message_pair_sha256"] == _canonical_json_digest(
        durable_pair
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "user_content",
        "assistant_content",
        "timestamp",
        "extra_field",
        "pair_order",
    ],
)
def test_committed_message_snapshot_tamper_is_rejected_by_replay_and_projection(
    atomic_env,
    mutation,
):
    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id=f"atomic-message-snapshot-{mutation}",
    )
    body = _start_body(
        session.session_id,
        idempotency_key=f"message-snapshot-{mutation}",
    )
    started = _post(routes, body)
    assert started.status == 200
    run_id = started.json_body()["run"]["run_id"]

    payload = json.loads(session.path.read_text(encoding="utf-8"))
    pair_indexes = [
        index
        for index, message in enumerate(payload["messages"])
        if message.get("expert_team_run_id") == run_id
    ]
    assert len(pair_indexes) == 2
    pair = [payload["messages"][index] for index in pair_indexes]
    by_type = {message["type"]: message for message in pair}
    if mutation == "user_content":
        by_type["expert_team_start"]["content"] = "FORGED_USER_CONTENT_VISIBLE"
    elif mutation == "assistant_content":
        by_type["expert_team_lifecycle"]["content"] = (
            "FORGED_ASSISTANT_CONTENT_VISIBLE"
        )
    elif mutation == "timestamp":
        by_type["expert_team_start"]["timestamp"] += 10
    elif mutation == "extra_field":
        by_type["expert_team_lifecycle"]["forged_extra"] = True
    else:
        first, second = pair_indexes
        payload["messages"][first], payload["messages"][second] = (
            payload["messages"][second],
            payload["messages"][first],
        )
    session.path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sessions.clear()

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    sessions.clear()
    public = _get(
        routes,
        f"/api/session?session_id={session.session_id}&messages=1",
    )
    assert public.status == 200
    projected = public.json_body()["session"]
    assert projected["messages"] == []
    assert projected["message_count"] == 0
    assert projected["title"] == "Untitled"


def test_expert_team_public_responses_never_expose_start_transaction_markers(
    atomic_env,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-public-start-marker",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="public-start-marker",
    )

    started = _post(routes, body)
    replayed = _post(routes, body)
    fetched = _get(
        routes,
        "/api/expert-teams/run?"
        f"session_id={session.session_id}&run_id={started.json_body()['run']['run_id']}",
    )

    assert started.status == replayed.status == fetched.status == 200
    _assert_no_public_start_transaction_markers(started.json_body())
    _assert_no_public_start_transaction_markers(replayed.json_body())
    _assert_no_public_start_transaction_markers(fetched.json_body())


def test_public_projection_validates_raw_start_pair_before_secret_redaction(
    atomic_env,
    monkeypatch,
):
    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-public-secret-redaction-order",
    )
    credential = "OPENAI_API_KEY=" + ("S" * 40)
    original_factory = routes._new_expert_team_start_session_messages

    def with_signed_secret(run, transaction_id):
        pair = original_factory(run, transaction_id)
        pair[0]["content"] = f"{pair[0]['content']} {credential}"
        return pair

    monkeypatch.setattr(
        routes,
        "_new_expert_team_start_session_messages",
        with_signed_secret,
    )
    body = _start_body(
        session.session_id,
        idempotency_key="public-secret-redaction-order",
        prompt=f"起草迎峰度夏保供电重点工作月度汇报 {credential}",
    )

    started = _post(routes, body)

    assert started.status == 200
    durable = models.Session.load(session.session_id)
    assert any(
        credential in str(message.get("content") or "")
        for message in durable.messages
    )
    started_payload = started.json_body()
    start_session = started_payload["session"]
    assert len(start_session["messages"]) == 2
    assert credential not in json.dumps(started_payload, ensure_ascii=False)

    sessions.clear()
    fetched = _get(
        routes,
        f"/api/session?session_id={session.session_id}&messages=1",
    )
    assert fetched.status == 200
    fetched_session = fetched.json_body()["session"]
    assert len(fetched_session["messages"]) == 2
    assert credential not in json.dumps(fetched_session, ensure_ascii=False)


def test_expert_team_public_json_gate_and_conflict_strip_all_marker_variants(
    atomic_env,
):
    routes, _models, _sessions, _workspace = atomic_env
    internal_run = {
        "run_id": "et-public-projection",
        "start_transaction_id": "a" * 64,
        "nested": {
            "expert_team_start_transaction_id": "a" * 64,
            "rows": [
                {"expert_team_start_transaction_ids": ["a" * 64]},
            ],
        },
    }

    serialized = _RouteHandler({})
    routes._expert_team_json_response(
        serialized,
        {"ok": True, "run": internal_run},
    )
    assert serialized.status == 200
    _assert_no_public_start_transaction_markers(serialized.json_body())

    conflict = type(
        "Conflict",
        (Exception,),
        {"code": "stale_state", "run": internal_run},
    )("stale")
    conflicted = _RouteHandler({})
    routes._expert_team_conflict_response(conflicted, conflict)
    assert conflicted.status == 409
    _assert_no_public_start_transaction_markers(conflicted.json_body())


@pytest.mark.parametrize(
    "request_target",
    [
        "/api/expert-teams/identity/callback?state=s&code=c",
        "http://127.0.0.1:8787/api/expert-teams/identity/callback?state=s&code=c",
    ],
)
def test_expert_team_error_branch_cannot_bypass_credential_redaction(
    atomic_env,
    monkeypatch,
    request_target,
):
    import api.expert_teams.trusted_identity as trusted_identity

    routes, _models, _sessions, _workspace = atomic_env
    credential = "OPENAI_API_KEY=" + ("Q" * 40)

    class FailingResolver:
        def complete_login(self, **_kwargs):
            raise ValueError(f"identity provider failed with {credential}")

    monkeypatch.setattr(
        trusted_identity,
        "get_trusted_identity_resolver",
        lambda: FailingResolver(),
    )

    response = _RouteHandler({})
    response.path = request_target
    routes.handle_get(response, urlparse(request_target))

    assert response.status == 401
    serialized = json.dumps(response.json_body(), ensure_ascii=False)
    assert credential not in serialized
    assert ("[REDACTED]" in serialized) or ("***" in serialized)


def test_json_egress_boundary_is_expert_scoped_and_future_route_safe():
    from api import helpers

    credential = "OPENAI_API_KEY=" + ("Z" * 40)
    payload = {
        "error": f"future branch failed with {credential}",
        "start_transaction_id": "a" * 64,
    }
    expert = _RouteHandler({})
    expert.path = "/api/expert-teams/future-endpoint"
    helpers.j(expert, payload, status=400)
    expert_serialized = json.dumps(expert.json_body(), ensure_ascii=False)
    assert expert.status == 400
    assert credential not in expert_serialized
    _assert_no_public_start_transaction_markers(expert.json_body())

    ordinary = _RouteHandler({})
    ordinary.path = "/api/unrelated-test-endpoint"
    helpers.j(ordinary, payload, status=400)
    assert ordinary.status == 400
    assert ordinary.json_body() == payload


def test_json_egress_scope_uses_current_keep_alive_path_over_stale_marker():
    from api import helpers

    handler = _RouteHandler({})
    handler.path = "/api/expert-teams/future-endpoint"
    handler._taiji_expert_team_json_request = True
    helpers.j(
        handler,
        {
            "start_transaction_id": "a" * 64,
            "error": "OPENAI_API_KEY=" + ("E" * 40),
        },
    )
    _assert_no_public_start_transaction_markers(handler.json_body())

    handler.path = "http://127.0.0.1:8787/api/unrelated-delete-endpoint"
    handler.status = None
    handler.body.clear()
    ordinary_payload = {
        "start_transaction_id": "ordinary-contract-value",
        "error": "OPENAI_API_KEY=" + ("O" * 40),
    }
    helpers.j(handler, ordinary_payload, status=404)

    assert handler.status == 404
    assert handler.json_body() == ordinary_payload


def test_expert_projection_failure_never_logs_or_returns_exception_secret(
    monkeypatch,
    caplog,
):
    from api import brand_privacy, helpers

    credential = "OPENAI_API_KEY=" + ("L" * 40)

    def fail_projection(_payload):
        raise RuntimeError(f"projection exploded with {credential}")

    monkeypatch.setattr(
        brand_privacy,
        "public_expert_team_response_projection",
        fail_projection,
    )
    caplog.set_level("ERROR")
    handler = _RouteHandler({})
    handler.path = "/api/expert-teams/future-endpoint"

    helpers.j(handler, {"ok": True})

    assert handler.status == 500
    assert handler.json_body()["code"] == "public_projection_failed"
    assert credential not in json.dumps(handler.json_body(), ensure_ascii=False)
    assert credential not in caplog.text


def test_v1_committed_receipt_is_not_retroactively_signed_from_current_messages(
    atomic_env,
):
    import api.expert_teams.storage as storage

    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-v1-committed-fail-closed",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="v1-committed-fail-closed",
    )
    first = _post(routes, body)
    assert first.status == 200
    _downgrade_start_transaction_to_v1(
        storage,
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    sessions.clear()

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"


def test_v1_prepared_without_session_evidence_is_rebuilt_as_v2(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-v1-prepared-rebuild",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="v1-prepared-rebuild",
    )
    real_write_pending = storage.write_pending_run
    monkeypatch.setattr(
        storage,
        "write_pending_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("crash before pending Run")
        ),
    )
    with pytest.raises(SystemExit, match="before pending Run"):
        _post(routes, body)
    monkeypatch.setattr(storage, "write_pending_run", real_write_pending)
    _downgrade_start_transaction_to_v1(
        storage,
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    sessions.clear()

    recovered = _post(routes, body)

    assert recovered.status == 200
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["schema_version"] == 2
    assert receipt["state"] == "committed"


def test_v1_prepared_with_session_evidence_remains_fail_closed(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-v1-prepared-with-evidence",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="v1-prepared-with-evidence",
    )
    real_publish = routes._publish_expert_team_start_run
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("crash after Session evidence")
        ),
    )
    with pytest.raises(SystemExit, match="after Session evidence"):
        _post(routes, body)
    monkeypatch.setattr(routes, "_publish_expert_team_start_run", real_publish)
    _downgrade_start_transaction_to_v1(
        storage,
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    sessions.clear()

    recovered = _post(routes, body)

    assert recovered.status == 503
    assert recovered.json_body()["code"] == "start_receipt_invalid"


def test_committed_replay_requires_durable_session_transaction_marker(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-replay-marker-missing")
    body = _start_body(session.session_id, idempotency_key="replay-marker-missing")
    first = _post(routes, body)
    assert first.status == 200
    before_messages = copy.deepcopy(session.messages)

    routes._rewrite_existing_session_truth(
        session,
        lambda: setattr(session, "expert_team_start_transaction_ids", []),
        privacy_reason=None,
        touch_updated_at=False,
    )
    durable = models.Session.load(session.session_id)
    assert durable.expert_team_start_transaction_ids == []
    assert durable.messages == before_messages

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert "session_messages" not in replay.json_body()
    assert len(_public_run_files(workspace)) == 1
    assert models.Session.load(session.session_id).messages == before_messages


def test_committed_replay_rejects_duplicate_durable_session_transaction_marker(
    atomic_env,
):
    routes, _models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        _models,
        workspace,
        session_id="atomic-replay-marker-duplicated",
    )
    body = _start_body(session.session_id, idempotency_key="replay-marker-duplicated")
    first = _post(routes, body)
    assert first.status == 200
    transaction_id = session.expert_team_start_transaction_ids[0]

    payload = json.loads(session.path.read_text(encoding="utf-8"))
    payload["expert_team_start_transaction_ids"] = [transaction_id, transaction_id]
    session.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert len(_public_run_files(workspace)) == 1


def test_committed_replay_fails_closed_for_null_durable_transaction_marker_store(
    atomic_env,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-replay-marker-null",
    )
    body = _start_body(session.session_id, idempotency_key="replay-marker-null")
    first = _post(routes, body)
    assert first.status == 200

    payload = json.loads(session.path.read_text(encoding="utf-8"))
    payload["expert_team_start_transaction_ids"] = None
    session.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert len(_public_run_files(workspace)) == 1


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


def test_unpersisted_session_is_rejected_before_receipt_or_run_creation(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = models.Session(
        session_id="atomic-unpersisted",
        workspace=str(workspace),
        profile="default",
    )
    models.SESSIONS[session.session_id] = session
    before = copy.deepcopy(session.__dict__)

    handler = _post(routes, _start_body(session.session_id))

    assert handler.status == 409
    assert handler.json_body()["code"] == "session_not_persisted"
    assert session.__dict__ == before
    assert session.path.exists() is False
    assert _public_run_files(workspace) == []
    assert _pending_run_files(workspace) == []
    assert not (workspace / ".taiji" / "expert-teams" / "start-transactions").exists()


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


def test_uncommitted_canonical_cannot_advance_during_commit_compensation(
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
    assert receipt["state"] == "rolled_back"
    assert storage.run_path(workspace, receipt["run_id"]).exists() is False
    assert len(_run_messages(session, receipt["run_id"])) == 0


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
    import api.state_sync as state_sync

    assert len(state_sync._get_state_db().get_messages(session.session_id)) == 2
    if cold_cache:
        from api import brand_privacy, truth_rewrite

        sessions.clear()
        with brand_privacy._COMMITTED_START_RECEIPT_CACHE_LOCK:
            brand_privacy._COMMITTED_START_RECEIPT_CACHE.clear()
        with truth_rewrite._LOCKS_GUARD:
            truth_rewrite._LOCKS.clear()

    response = _get(
        routes,
        f"/api/session?session_id={session.session_id}&messages=0",
    )

    assert response.status == 200
    public = response.json_body()["session"]
    assert public["title"] == "Untitled"
    assert public["message_count"] == 0
    assert public["messages"] == []
    assert len(state_sync._get_state_db().get_messages(session.session_id)) == 2


def test_prepared_recovery_rejects_duplicate_durable_transaction_marker(
    atomic_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-recovery-marker-duplicated",
    )
    body = _start_body(session.session_id, idempotency_key="recovery-marker-duplicated")
    real_publish = routes._publish_expert_team_start_run
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("crash after Session commit")
        ),
    )
    with pytest.raises(SystemExit, match="crash after Session commit"):
        _post(routes, body)
    monkeypatch.setattr(routes, "_publish_expert_team_start_run", real_publish)

    payload = json.loads(session.path.read_text(encoding="utf-8"))
    transaction_id = payload["expert_team_start_transaction_ids"][0]
    payload["expert_team_start_transaction_ids"] = [transaction_id, transaction_id]
    session.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    recovered = _post(routes, body)

    assert recovered.status == 503
    assert recovered.json_body()["code"] == "start_receipt_invalid"


def test_metadata_only_projection_fails_closed_when_committed_run_is_missing(
    atomic_env,
):
    from api import brand_privacy
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-metadata-committed-run-missing",
    )
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    storage.run_path(workspace, first.json_body()["run"]["run_id"]).unlink()

    metadata = models.Session.load_metadata_only(session.session_id)
    projected = brand_privacy.public_session_projection(metadata.compact())

    assert projected["title"] == "Untitled"
    assert projected["message_count"] == 0


def test_metadata_only_projection_fails_closed_when_committed_pair_is_incomplete(
    atomic_env,
):
    from api import brand_privacy

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-metadata-committed-pair-incomplete",
    )
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    run_id = first.json_body()["run"]["run_id"]
    payload = json.loads(session.path.read_text(encoding="utf-8"))
    payload["messages"] = [
        message
        for message in payload["messages"]
        if not (
            message.get("expert_team_run_id") == run_id
            and message.get("type") == "expert_team_lifecycle"
        )
    ]
    payload["message_count"] = len(payload["messages"])
    session.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    metadata = models.Session.load_metadata_only(session.session_id)
    projected = brand_privacy.public_session_projection(metadata.compact())

    assert projected["title"] == "Untitled"
    assert projected["message_count"] == 0


def test_valid_committed_metadata_and_sidebar_projection_remain_visible(
    atomic_env,
    monkeypatch,
):
    from api import brand_privacy

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-valid-committed-metadata",
    )
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    expected_title = first.json_body()["session"]["title"]
    metadata = models.Session.load_metadata_only(session.session_id)

    metadata_public = brand_privacy.public_session_projection(metadata.compact())
    assert metadata_public["title"] == expected_title
    assert metadata_public["message_count"] == 2

    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    sidebar = _get(routes, "/api/sessions")
    assert sidebar.status == 200
    row = next(
        item
        for item in sidebar.json_body()["sessions"]
        if item["session_id"] == session.session_id
    )
    assert row["title"] == expected_title
    assert row["message_count"] == 2


def test_valid_committed_metadata_get_does_not_return_loaded_messages(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-valid-committed-messages-zero",
    )
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200

    response = _get(
        routes,
        f"/api/session?session_id={session.session_id}&messages=0",
    )

    assert response.status == 200
    public = response.json_body()["session"]
    assert public["message_count"] == 2
    assert public["messages"] == []


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
            "type": (
                "expert_team_start"
                if role == "user"
                else "expert_team_lifecycle"
            ),
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
    monkeypatch.setattr(
        storage,
        "read_run_raw",
        lambda _workspace, run_id: {"run_id": run_id},
    )
    monkeypatch.setattr(
        storage,
        "validate_start_transaction_bundle",
        lambda *_args, **_kwargs: copy.deepcopy(messages),
    )
    payload = {
        "session_id": "projection-session",
        "workspace": str(tmp_path),
        "message_count": 2,
        "messages": messages,
        "expert_team_start_transaction_ids": [transaction_id],
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
    assert projected_on_error["messages"] == []
    assert projected_on_error["message_count"] == 0

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


@pytest.mark.parametrize("crash_boundary", ["by_run", "receipt", "by_session"])
def test_initial_transaction_metadata_crash_recovers_same_key_from_cold_cache(
    atomic_env,
    monkeypatch,
    crash_boundary,
):
    import api.expert_teams.storage as storage

    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id=f"atomic-metadata-crash-{crash_boundary}",
    )
    body = _start_body(
        session.session_id,
        idempotency_key=f"metadata-crash-{crash_boundary}",
    )
    transaction_id = storage.start_transaction_id(session.session_id, body["idempotency_key"])
    run_id = "et-" + transaction_id
    targets = {
        "by_run": storage.start_run_binding_path(workspace, run_id),
        "receipt": storage.start_transaction_path(workspace, transaction_id),
        "by_session": storage.start_session_binding_path(
            workspace,
            session.session_id,
        ),
    }
    real_write_json = storage._write_json_atomic

    def crash_after_target_write(path, payload):
        result = real_write_json(path, payload)
        if Path(path) == targets[crash_boundary]:
            raise SystemExit(f"simulated crash after {crash_boundary}")
        return result

    monkeypatch.setattr(storage, "_write_json_atomic", crash_after_target_write)
    with pytest.raises(SystemExit, match=f"after {crash_boundary}"):
        _post(routes, body)

    assert session.messages == []
    monkeypatch.setattr(storage, "_write_json_atomic", real_write_json)
    sessions.clear()
    retry = _post(routes, body)

    assert retry.status == 200
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["state"] == "committed"
    assert receipt["schema_version"] == 2
    durable = models.Session.load(session.session_id)
    assert durable.expert_team_start_transaction_ids == [transaction_id]
    assert len(_run_messages(durable, run_id)) == 2
    assert len(_public_run_files(workspace)) == 1
    assert _pending_run_files(workspace) == []


def test_committed_receipt_post_write_exception_is_reconciled_without_compensation(
    atomic_env,
    monkeypatch,
):
    import api.expert_teams.storage as storage

    routes, models, sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-committed-receipt-post-write-error",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="committed-receipt-post-write-error",
    )
    transaction_id = storage.start_transaction_id(
        session.session_id,
        body["idempotency_key"],
    )
    receipt_path = storage.start_transaction_path(workspace, transaction_id)
    real_write_json = storage._write_json_atomic
    injected = False

    def fail_once_after_committed_receipt_write(path, payload):
        nonlocal injected
        result = real_write_json(path, payload)
        if (
            not injected
            and Path(path) == receipt_path
            and payload.get("state") == "committed"
        ):
            injected = True
            raise OSError("injected exception after committed receipt replace")
        return result

    monkeypatch.setattr(
        storage,
        "_write_json_atomic",
        fail_once_after_committed_receipt_write,
    )

    started = _post(routes, body)

    assert injected is True
    assert started.status == 200
    assert started.json_body()["replayed"] is False
    receipt = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    assert receipt["state"] == "committed"
    run_id = receipt["run_id"]
    durable = models.Session.load(session.session_id)
    assert len(_run_messages(durable, run_id)) == 2
    assert len(_public_run_files(workspace)) == 1
    assert _pending_run_files(workspace) == []

    sessions.clear()
    replayed = _post(routes, body)
    assert replayed.status == 200
    assert replayed.json_body()["replayed"] is True
    durable = models.Session.load(session.session_id)
    assert len(_run_messages(durable, run_id)) == 2
    assert len(_public_run_files(workspace)) == 1


def test_compensation_refuses_stale_prepared_view_of_committed_receipt(
    atomic_env,
):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-stale-prepared-compensation",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="stale-prepared-compensation",
    )
    started = _post(routes, body)
    assert started.status == 200
    committed = storage.read_start_transaction(
        workspace,
        session_id=session.session_id,
        idempotency_key=body["idempotency_key"],
    )
    run_id = committed["run_id"]
    stale_prepared = dict(committed)
    stale_prepared["state"] = "prepared"
    stale_prepared["updated_at"] = committed["created_at"]

    with pytest.raises(routes._ExpertTeamStartIntegrityError):
        routes._compensate_expert_team_start_finalize_locked(
            workspace,
            session,
            stale_prepared,
        )

    durable = models.Session.load(session.session_id)
    assert storage.read_start_transaction_by_id(
        workspace,
        committed["transaction_id"],
    )["state"] == "committed"
    assert storage.read_run(workspace, run_id)["run_id"] == run_id
    assert len(_run_messages(durable, run_id)) == 2


@pytest.mark.parametrize("wrapped_storage_error", [False, True])
def test_transient_session_receipt_failure_never_uses_cache_as_authority(
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

    rejected = brand_privacy.public_session_projection(payload)

    assert rejected["messages"] == []
    assert rejected["message_count"] == 0
    assert rejected["title"] == "Untitled"


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


def test_committed_same_key_replay_precedes_busy_session_rejection(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-replay-busy")
    body = _start_body(session.session_id, idempotency_key="replay-before-busy")
    first = _post(routes, body)
    assert first.status == 200
    first_run_id = first.json_body()["run"]["run_id"]
    before_messages = copy.deepcopy(session.messages)

    session.active_stream_id = "ordinary-chat-is-running"
    session.pending_user_message = "另一条普通消息正在处理"
    replay = _post(routes, body)

    assert replay.status == 200
    assert replay.json_body()["replayed"] is True
    assert replay.json_body()["run"]["run_id"] == first_run_id
    assert session.messages == before_messages
    assert len(_public_run_files(workspace)) == 1


def test_missing_committed_receipt_cannot_create_a_second_run_for_same_key(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-receipt-missing-replay")
    body = _start_body(session.session_id, idempotency_key="missing-receipt-same-key")
    first = _post(routes, body)
    assert first.status == 200
    first_run_id = first.json_body()["run"]["run_id"]
    receipt_path = _receipt_path(workspace, session.session_id, body["idempotency_key"])
    receipt_path.unlink()

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert [path.stem for path in _public_run_files(workspace)] == [first_run_id]
    assert len(_run_messages(session, first_run_id)) == 2


@pytest.mark.parametrize("binding_kind", ["by_run", "by_session"])
def test_committed_replay_requires_both_reverse_bindings(atomic_env, binding_kind):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id=f"atomic-missing-{binding_kind}",
    )
    body = _start_body(
        session.session_id,
        idempotency_key=f"missing-{binding_kind}-binding",
    )
    first = _post(routes, body)
    assert first.status == 200
    run_id = first.json_body()["run"]["run_id"]
    target = (
        storage.start_run_binding_path(workspace, run_id)
        if binding_kind == "by_run"
        else storage.start_session_binding_path(workspace, session.session_id)
    )
    target.unlink()

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert len(_public_run_files(workspace)) == 1


def test_legal_receipt_state_tamper_is_rejected_instead_of_self_healed(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-state-tamper")
    body = _start_body(session.session_id, idempotency_key="state-tamper")
    first = _post(routes, body)
    assert first.status == 200
    path = _receipt_path(workspace, session.session_id, body["idempotency_key"])
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["state"] = "prepared"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert len(_public_run_files(workspace)) == 1


def test_bound_standalone_v3_run_without_binding_is_not_treated_as_legacy(atomic_env):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-v3-binding-required")
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    run_id = first.json_body()["run"]["run_id"]
    storage.start_run_binding_path(workspace, run_id).unlink()

    with pytest.raises(FileNotFoundError):
        storage.read_run(workspace, run_id)
    assert storage.list_runs(workspace) == []
    with pytest.raises(FileNotFoundError):
        storage.latest_run_for_session(workspace, session.session_id)


@pytest.mark.parametrize("mutation", ["prompt", "session_id", "launch_profile_snapshot"])
def test_committed_run_read_rejects_immutable_projection_tamper(atomic_env, mutation):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id=f"atomic-run-projection-{mutation}",
    )
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    run_id = first.json_body()["run"]["run_id"]
    path = storage.run_path(workspace, run_id)
    run = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "prompt":
        run["prompt"] = "被篡改的任务"
    elif mutation == "session_id":
        run["session_id"] = "another-session"
    else:
        run["launch_profile_snapshot"]["title"] = "被篡改的配置快照"
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        storage.read_run(workspace, run_id)
    assert storage.list_runs(workspace) == []


def test_write_run_rejects_immutable_projection_change_for_committed_run(atomic_env):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-run-write-guard")
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    run = copy.deepcopy(first.json_body()["run"])
    run["prompt"] = "mutation API 不得改写启动投影"

    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.write_run(workspace, run)

    assert storage.read_run(workspace, run["run_id"])["prompt"] != run["prompt"]


def test_committed_message_pair_is_hidden_as_a_group_when_one_row_is_missing(atomic_env):
    from api import brand_privacy

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-incomplete-pair")
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    run_id = first.json_body()["run"]["run_id"]
    session.messages = [
        message
        for message in session.messages
        if not (
            message.get("expert_team_run_id") == run_id
            and message.get("type") == "expert_team_lifecycle"
        )
    ]
    payload = session.compact() | {"messages": copy.deepcopy(session.messages)}

    projected = brand_privacy.public_session_projection(payload)

    assert projected["messages"] == []
    assert projected["message_count"] == 0


def test_metadata_projection_does_not_rollback_activity_after_prepared_start(
    atomic_env,
    monkeypatch,
):
    from api import brand_privacy

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-prepared-later-activity")
    body = _start_body(session.session_id, idempotency_key="prepared-before-chat")
    monkeypatch.setattr(
        routes,
        "_publish_expert_team_start_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("leave prepared")),
    )
    with pytest.raises(SystemExit, match="leave prepared"):
        _post(routes, body)

    later_timestamp = time.time() + 100

    def append_later_activity():
        session.messages.extend(
            [
                {
                    "role": "user",
                    "content": "专家团启动后新增的普通问题",
                    "timestamp": later_timestamp,
                },
                {
                    "role": "assistant",
                    "content": "专家团启动后新增的普通回答",
                    "timestamp": later_timestamp + 1,
                },
            ]
        )
        session.context_messages = copy.deepcopy(session.messages)
        session.title = "用户后续重命名"
        session.input_tokens = 321
        session.output_tokens = 654
        session.updated_at = later_timestamp + 1

    routes._rewrite_existing_session_truth(
        session,
        append_later_activity,
        privacy_reason=None,
        touch_updated_at=False,
    )
    metadata = models.Session.load_metadata_only(session.session_id)
    projected = brand_privacy.public_session_projection(metadata.compact())

    assert projected["title"] == "用户后续重命名"
    assert projected["message_count"] == 2
    assert projected["updated_at"] == later_timestamp + 1
    assert projected["last_message_at"] == later_timestamp + 1
    assert projected["input_tokens"] == 321
    assert projected["output_tokens"] == 654


def test_missing_by_session_binding_fails_closed_for_metadata_projection(atomic_env):
    from api import brand_privacy
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-sidebar-binding-missing")
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200
    persisted = models.Session.load_metadata_only(session.session_id)
    assert persisted.expert_team_start_transaction_ids
    storage.start_session_binding_path(workspace, session.session_id).unlink()

    projected = brand_privacy.public_session_projection(persisted.compact())

    assert projected["title"] == "Untitled"
    assert projected["message_count"] == 0


def test_discovered_committed_receipt_requires_durable_marker_and_pair(atomic_env):
    from api import brand_privacy

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-committed-marker-and-pair-missing",
    )
    first = _post(routes, _start_body(session.session_id))
    assert first.status == 200

    # Preserve the by-session receipt discovery index while simulating a
    # corrupted Session that lost both its owned message pair and durable marker.
    session.messages = []
    session.context_messages = []
    session.expert_team_start_transaction_ids = []
    payload = session.compact() | {"messages": []}

    projected = brand_privacy.public_session_projection(payload)

    assert projected["messages"] == []
    assert projected["title"] == "Untitled"
    assert projected["message_count"] == 0


def test_same_transaction_row_for_another_run_invalidates_replay(atomic_env):
    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-cross-run-transaction-row",
    )
    body = _start_body(session.session_id)
    first = _post(routes, body)
    assert first.status == 200
    transaction_id = session.expert_team_start_transaction_ids[0]

    def append_rogue_row():
        session.messages.append(
            {
                "role": "assistant",
                "type": "expert_team_lifecycle",
                "content": "rogue",
                "expert_team_run_id": "et-another-run",
                "expert_team_start_transaction_id": transaction_id,
            }
        )
        session.context_messages = copy.deepcopy(session.messages)

    routes._rewrite_existing_session_truth(
        session,
        append_rogue_row,
        privacy_reason=None,
        touch_updated_at=False,
    )

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"


def test_missing_all_receipt_bindings_cannot_create_second_run(atomic_env):
    import api.expert_teams.storage as storage

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(
        models,
        workspace,
        session_id="atomic-all-start-bindings-missing",
    )
    body = _start_body(
        session.session_id,
        idempotency_key="all-bindings-missing-same-key",
    )
    first = _post(routes, body)
    assert first.status == 200
    run_id = first.json_body()["run"]["run_id"]
    transaction_id = session.expert_team_start_transaction_ids[0]

    storage.start_transaction_path(workspace, transaction_id).unlink()
    storage.start_run_binding_path(workspace, run_id).unlink()
    storage.start_session_binding_path(workspace, session.session_id).unlink()

    def remove_session_evidence():
        session.messages = [
            message
            for message in session.messages
            if message.get("expert_team_start_transaction_id") != transaction_id
        ]
        session.context_messages = copy.deepcopy(session.messages)
        session.expert_team_start_transaction_ids = []

    routes._rewrite_existing_session_truth(
        session,
        remove_session_evidence,
        privacy_reason=None,
        touch_updated_at=False,
    )

    replay = _post(routes, body)

    assert replay.status == 503
    assert replay.json_body()["code"] == "start_receipt_invalid"
    assert [path.stem for path in storage.runs_dir(workspace).glob("*.json")] == [run_id]


def test_storage_rejects_symlinked_parent_directory(tmp_path):
    import api.expert_teams.storage as storage

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".taiji").symlink_to(outside, target_is_directory=True)
    run = {"run_id": "et-symlink-parent"}

    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.write_pending_run(workspace, run)

    assert not (outside / "expert-teams" / "start-transactions" / "pending").exists()


def test_storage_atomic_write_stays_anchored_during_parent_symlink_swap(
    tmp_path,
    monkeypatch,
):
    """A parent exchange after validation must never redirect a Run write."""
    import api.expert_teams.storage as storage

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    pending = storage.pending_run_path(workspace, "et-parent-swap").parent
    pending.mkdir(parents=True)
    detached = pending.with_name("pending-detached")
    real_open = storage.os.open
    swapped = False

    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        is_temp_create = bool(flags & os.O_CREAT and flags & os.O_EXCL)
        if not swapped and is_temp_create and str(path).endswith(".tmp"):
            pending.rename(detached)
            pending.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(storage.os, "open", racing_open)
    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.write_pending_run(workspace, {"run_id": "et-parent-swap"})

    assert swapped is True
    assert not (outside / "et-parent-swap.json").exists()
    assert (detached / "et-parent-swap.json").is_file()


def test_storage_read_fails_when_parent_is_exchanged_after_open(
    tmp_path,
    monkeypatch,
):
    """A read from a detached inode must not be reported as canonical truth."""
    import api.expert_teams.storage as storage

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    run_id = "et-read-parent-swap"
    storage.write_pending_run(workspace, {"run_id": run_id, "source": "trusted"})
    pending = storage.pending_run_path(workspace, run_id).parent
    detached = pending.with_name("pending-read-detached")
    (outside / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "source": "outside"}),
        encoding="utf-8",
    )
    real_open = storage.os.open
    swapped = False

    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if (
            not swapped
            and kwargs.get("dir_fd") is not None
            and str(path) == f"{run_id}.json"
        ):
            pending.rename(detached)
            pending.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(storage.os, "open", racing_open)
    with pytest.raises(storage.StartTransactionIntegrityError):
        storage.read_pending_run(workspace, run_id)

    assert swapped is True
    assert json.loads((outside / f"{run_id}.json").read_text(encoding="utf-8"))[
        "source"
    ] == "outside"


def test_publish_pending_run_never_promotes_a_replaced_source_leaf(
    tmp_path,
    monkeypatch,
):
    """Publishing must use the payload that was validated, not reopen its name."""
    import api.expert_teams.storage as storage

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.json"
    workspace.mkdir()
    run_id = "et-leaf-publish-race"
    trusted = {"run_id": run_id, "source": "trusted"}
    malicious = {"run_id": run_id, "source": "outside"}
    storage.write_pending_run(workspace, trusted)
    outside.write_text(json.dumps(malicious), encoding="utf-8")
    pending = storage.pending_run_path(workspace, run_id)
    detached = pending.with_name(f"{pending.stem}-trusted-backup.json")
    real_read_pending = storage.read_pending_run
    swapped = False

    def read_then_swap(workspace_arg, run_id_arg):
        nonlocal swapped
        result = real_read_pending(workspace_arg, run_id_arg)
        pending.rename(detached)
        pending.symlink_to(outside)
        swapped = True
        return result

    monkeypatch.setattr(storage, "read_pending_run", read_then_swap)

    published = storage.publish_pending_run(workspace, run_id)
    canonical = storage.run_path(workspace, run_id)

    assert swapped is True
    assert published == trusted
    assert canonical.is_file()
    assert not canonical.is_symlink()
    assert storage.read_run_raw(workspace, run_id) == trusted
    assert json.loads(outside.read_text(encoding="utf-8")) == malicious
    assert not pending.exists()


def test_publish_pending_run_reuses_identical_canonical_and_removes_pending(tmp_path):
    import api.expert_teams.storage as storage

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = {"run_id": "et-identical-canonical", "source": "trusted"}
    storage.write_pending_run(workspace, run)
    storage.write_run(workspace, run)

    assert storage.publish_pending_run(workspace, run["run_id"]) == run
    assert storage.read_run_raw(workspace, run["run_id"]) == run
    assert not storage.pending_run_path(workspace, run["run_id"]).exists()


def test_publish_pending_run_preserves_conflicting_canonical_and_pending(tmp_path):
    import api.expert_teams.storage as storage

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_id = "et-conflicting-canonical"
    pending = {"run_id": run_id, "source": "pending"}
    canonical = {"run_id": run_id, "source": "canonical"}
    storage.write_pending_run(workspace, pending)
    storage.write_run(workspace, canonical)

    with pytest.raises(
        storage.StartTransactionIntegrityError,
        match="canonical run conflicts with pending run",
    ):
        storage.publish_pending_run(workspace, run_id)

    assert storage.read_run_raw(workspace, run_id) == canonical
    assert storage.read_pending_run(workspace, run_id) == pending


def test_storage_lock_stays_anchored_during_parent_symlink_swap(
    tmp_path,
    monkeypatch,
):
    """A parent exchange must not redirect the inter-process lock file."""
    import api.expert_teams.storage as storage

    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    transaction_id = "d" * 64
    locks = storage.start_transaction_lock_path(workspace, transaction_id).parent
    locks.mkdir(parents=True)
    detached = locks.with_name("locks-detached")
    real_open = storage.os.open
    swapped = False

    def racing_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if (
            not swapped
            and flags & os.O_CREAT
            and str(path).endswith(f"{transaction_id}.lock")
        ):
            locks.rename(detached)
            locks.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(storage.os, "open", racing_open)
    with pytest.raises(storage.StartTransactionIntegrityError):
        with storage.start_transaction_lock(workspace, transaction_id):
            pass

    assert swapped is True
    assert not (outside / f"{transaction_id}.lock").exists()
    assert (detached / f"{transaction_id}.lock").is_file()


@pytest.mark.parametrize(
    ("crash_point", "expected_exit"),
    [
        ("after_by_run_metadata", 68),
        ("after_receipt_metadata", 69),
        ("after_by_session_metadata", 70),
        ("after_receipt", 71),
        ("after_session", 72),
        ("after_canonical", 73),
    ],
)
def test_fresh_interpreter_recovers_three_durable_start_crash_points(
    tmp_path,
    crash_point,
    expected_exit,
):
    """Prove cold recovery against real sidecar, state.db, index, Run and receipt.

    Each first process exits abruptly at a different durable boundary. A wholly
    new interpreter then retries the same idempotency key and must converge all
    stores to one committed Run and one exact message pair.
    """
    webui_root = Path(__file__).resolve().parents[1]
    case_root = tmp_path / crash_point
    state_root = case_root / "state"
    workspace = case_root / "workspace"
    state_root.mkdir(parents=True)
    workspace.mkdir(parents=True)
    session_id = f"spawn-{crash_point}"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(webui_root),
            "PYTHONUNBUFFERED": "1",
            "HERMES_WEBUI_STATE_DIR": str(state_root),
            "HERMES_WEBUI_TEST_STATE_DIR": str(state_root),
            "HERMES_WEBUI_DEFAULT_WORKSPACE": str(workspace),
            "HERMES_HOME": str(state_root),
            "HERMES_BASE_HOME": str(state_root),
            "HERMES_CONFIG_PATH": str(state_root / "config.yaml"),
            "AWS_EC2_METADATA_DISABLED": "true",
            "EXPERT_TEAM_CRASH_POINT": crash_point,
            "EXPERT_TEAM_WORKSPACE": str(workspace),
            "EXPERT_TEAM_SESSION_ID": session_id,
        }
    )
    crashing_script = textwrap.dedent(
        """
        import os
        from pathlib import Path

        from api import config
        from api.models import Session
        import api.routes as routes
        import api.expert_teams.storage as storage

        workspace = Path(os.environ["EXPERT_TEAM_WORKSPACE"])
        session_id = os.environ["EXPERT_TEAM_SESSION_ID"]
        Path(config.SESSION_DIR).mkdir(parents=True, exist_ok=True)
        session = Session(
            session_id=session_id,
            workspace=str(workspace),
            profile="default",
        )
        session.save(touch_updated_at=False)
        body = {
            "launch_profile_id": "content-work-report",
            "session_id": session_id,
            "prompt": "fresh process atomic recovery",
            "idempotency_key": "fresh-process-idempotency",
        }
        point = os.environ["EXPERT_TEAM_CRASH_POINT"]
        metadata_exits = {
            "after_by_run_metadata": 68,
            "after_receipt_metadata": 69,
            "after_by_session_metadata": 70,
        }
        if point in metadata_exits:
            transaction_id = storage.start_transaction_id(
                session_id,
                body["idempotency_key"],
            )
            run_id = "et-" + transaction_id
            metadata_targets = {
                "after_by_run_metadata": storage.start_run_binding_path(
                    workspace,
                    run_id,
                ),
                "after_receipt_metadata": storage.start_transaction_path(
                    workspace,
                    transaction_id,
                ),
                "after_by_session_metadata": storage.start_session_binding_path(
                    workspace,
                    session_id,
                ),
            }
            original_write_json = storage._write_json_atomic
            def crash_after_metadata_write(path, payload):
                result = original_write_json(path, payload)
                if Path(path) == metadata_targets[point]:
                    os._exit(metadata_exits[point])
                return result
            storage._write_json_atomic = crash_after_metadata_write
        elif point == "after_receipt":
            def crash_pending(*_args, **_kwargs):
                os._exit(71)
            storage.write_pending_run = crash_pending
        elif point == "after_session":
            def crash_publish(*_args, **_kwargs):
                os._exit(72)
            routes._publish_expert_team_start_run = crash_publish
        elif point == "after_canonical":
            original_write_receipt = storage.write_start_transaction
            def crash_committed_receipt(target_workspace, receipt):
                if receipt.get("state") == "committed":
                    os._exit(73)
                return original_write_receipt(target_workspace, receipt)
            storage.write_start_transaction = crash_committed_receipt
        routes._coordinate_expert_team_start(body)
        raise SystemExit(90)
        """
    )
    crashed = subprocess.run(
        [sys.executable, "-c", crashing_script],
        cwd=webui_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert crashed.returncode == expected_exit, crashed.stderr

    recovery_script = textwrap.dedent(
        """
        import json
        import os
        from pathlib import Path

        from api import config
        from api.models import Session
        import api.routes as routes
        import api.state_sync as state_sync
        import api.expert_teams.storage as storage

        workspace = Path(os.environ["EXPERT_TEAM_WORKSPACE"])
        session_id = os.environ["EXPERT_TEAM_SESSION_ID"]
        body = {
            "launch_profile_id": "content-work-report",
            "session_id": session_id,
            "prompt": "fresh process atomic recovery",
            "idempotency_key": "fresh-process-idempotency",
        }
        result = routes._coordinate_expert_team_start(body)
        session = Session.load(session_id)
        receipt = storage.read_start_transaction(
            workspace,
            session_id=session_id,
            idempotency_key=body["idempotency_key"],
        )
        run = storage.read_run(workspace, receipt["run_id"])
        db = state_sync._get_state_db(
            profile="default",
            strict=True,
            create_if_missing=True,
        )
        try:
            state_messages = list(db.get_messages(session_id) or [])
        finally:
            db.close()
        index = json.loads(Path(config.SESSION_INDEX_FILE).read_text(encoding="utf-8"))
        index_row = next(row for row in index if row.get("session_id") == session_id)
        transaction_id = receipt["transaction_id"]
        owned_messages = [
            row for row in session.messages
            if row.get("expert_team_start_transaction_id") == transaction_id
        ]
        owned_semantic_messages = [
            {"role": row.get("role"), "content": row.get("content")}
            for row in owned_messages
        ]
        state_semantic_messages = [
            {"role": row.get("role"), "content": row.get("content")}
            for row in state_messages
        ]
        print(json.dumps({
            "receipt_state": receipt["state"],
            "run_id": run["run_id"],
            "run_transaction_id": run.get("start_transaction_id"),
            "canonical_run_ids": sorted(
                path.stem for path in storage.runs_dir(workspace).glob("*.json")
            ),
            "pending_exists": storage.pending_run_path(workspace, run["run_id"]).exists(),
            "marker_ids": session.expert_team_start_transaction_ids,
            "owned_message_types": sorted(row.get("type") for row in owned_messages),
            "owned_semantic_messages": owned_semantic_messages,
            "state_semantic_messages": state_semantic_messages,
            "index_message_count": index_row.get("message_count"),
            "result_run_id": result["run"]["run_id"],
        }, ensure_ascii=False))
        """
    )
    recovered = subprocess.run(
        [sys.executable, "-c", recovery_script],
        cwd=webui_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert recovered.returncode == 0, recovered.stderr
    evidence = json.loads(recovered.stdout.strip().splitlines()[-1])
    expected_types = ["expert_team_lifecycle", "expert_team_start"]
    assert evidence["receipt_state"] == "committed"
    assert evidence["run_id"] == evidence["result_run_id"]
    assert evidence["run_transaction_id"] in evidence["marker_ids"]
    assert evidence["canonical_run_ids"] == [evidence["run_id"]]
    assert evidence["pending_exists"] is False
    assert evidence["owned_message_types"] == expected_types
    assert evidence["state_semantic_messages"] == evidence["owned_semantic_messages"]
    assert evidence["index_message_count"] == 2


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires POSIX fork to inherit the isolated test stores",
)
def test_start_session_and_run_file_locks_have_bounded_cross_process_waits(atomic_env):
    from api import truth_rewrite
    import api.expert_teams.storage as storage

    _routes, _models, _sessions, workspace = atomic_env
    context = multiprocessing.get_context("fork")
    cases = (
        (
            "session",
            "bounded-session-writer",
            lambda: truth_rewrite.truth_rewrite_lock(
                "bounded-session-writer",
                timeout_seconds=0.1,
            ),
        ),
        (
            "run",
            "bounded-run-writer",
            lambda: storage.run_file_lock(
                workspace,
                "bounded-run-writer",
                timeout_seconds=0.1,
            ),
        ),
    )
    for kind, identifier, contender in cases:
        ready = context.Event()
        release = context.Event()
        result = context.Queue()
        process = context.Process(
            target=_hold_cross_process_lock,
            args=(kind, str(workspace), identifier, ready, release, result),
        )
        process.start()
        try:
            assert ready.wait(timeout=2)
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                with contender():
                    pass
            assert time.monotonic() - started < 1
        finally:
            release.set()
            process.join(timeout=3)
            if process.is_alive():  # pragma: no cover - diagnostic cleanup
                process.terminate()
                process.join(timeout=1)
        assert process.exitcode == 0
        assert result.get(timeout=1) == ("ok", "")


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires POSIX fork to inherit the isolated test stores",
)
def test_stale_process_loaded_before_start_cannot_overwrite_committed_start(atomic_env):
    import api.state_sync as state_sync

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-stale-process")
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    proceed = context.Event()
    result = context.Queue()
    process = context.Process(
        target=_save_stale_session_after_signal,
        args=(session.session_id, ready, proceed, result),
    )
    process.start()
    try:
        assert ready.wait(timeout=2)
        started = _post(routes, _start_body(session.session_id))
        assert started.status == 200
        proceed.set()
        process.join(timeout=3)
    finally:
        proceed.set()
        if process.is_alive():  # pragma: no cover - diagnostic cleanup
            process.terminate()
            process.join(timeout=1)
    assert process.exitcode == 0
    assert result.get(timeout=1) == ("error", "SessionWriteConflict")
    durable = models.Session.load(session.session_id)
    assert durable.title != "过期进程标题"
    assert all("过期进程" not in str(message.get("content") or "") for message in durable.messages)
    assert len(durable.messages) == 2
    assert len(state_sync._get_state_db().get_messages(session.session_id)) == 2


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires POSIX fork to inherit the isolated test stores",
)
def test_cross_process_ordinary_commit_wins_then_start_reloads_and_retries(atomic_env):
    import api.state_sync as state_sync

    routes, models, _sessions, workspace = atomic_env
    session = _new_memory_session(models, workspace, session_id="atomic-ordinary-first")
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    result = context.Queue()
    process = context.Process(
        target=_commit_ordinary_session_before_start,
        args=(session.session_id, ready, result),
    )
    process.start()
    try:
        assert ready.wait(timeout=2)
        conflicted = _post(routes, _start_body(session.session_id))
        process.join(timeout=3)
    finally:
        if process.is_alive():  # pragma: no cover - diagnostic cleanup
            process.terminate()
            process.join(timeout=1)
    assert process.exitcode == 0
    assert result.get(timeout=1) == ("ok", "")
    assert conflicted.status == 409
    assert conflicted.json_body()["code"] == "session_state_conflict"
    assert session.title == "普通写入后的标题"
    assert session.input_tokens == 123
    assert session.output_tokens == 456
    assert len(session.messages) == 2

    retry = _post(routes, _start_body(session.session_id))
    assert retry.status == 200
    durable = models.Session.load(session.session_id)
    assert durable.title == "普通写入后的标题"
    assert durable.input_tokens == 123
    assert durable.output_tokens == 456
    assert len(durable.messages) == 4
    assert len(state_sync._get_state_db().get_messages(session.session_id)) == 4
