from __future__ import annotations

import io
import json
import multiprocessing
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import pytest


def _hold_launch_transaction_lock(
    state_dir: str,
    transaction_id: str,
    ready,
    release,
) -> None:
    """Hold one durable launch lock in an independent spawned process."""
    import api.config as config
    from api.expert_teams import launch_storage

    config.STATE_DIR = Path(state_dir)
    with launch_storage.launch_transaction_lock(transaction_id):
        ready.set()
        release.wait(timeout=15)


class _RouteHandler:
    def __init__(self, payload: dict | None = None):
        raw = json.dumps(payload or {}).encode("utf-8")
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


def _post(routes, path: str, body: dict) -> _RouteHandler:
    handler = _RouteHandler(body)
    routes.handle_post(handler, urlparse(path))
    return handler


def _get(routes, path: str) -> _RouteHandler:
    handler = _RouteHandler()
    routes.handle_get(handler, urlparse(path))
    return handler


def _launch_body(**overrides) -> dict:
    body = {
        "launch_profile_id": "content-work-report",
        "prompt": "起草迎峰度夏保供电重点工作月度汇报",
        "idempotency_key": "launch-atomic-work-report",
        "session_options": {
            "profile": "default",
            "project_id": "project-one",
            "model": "openai/gpt-5.4-mini",
            "model_provider": "openai",
        },
    }
    body.update(overrides)
    return body


def _launch_receipt(body: dict) -> dict:
    from api.expert_teams import launch_storage

    transaction_id = launch_storage.launch_transaction_id(body["idempotency_key"])
    with launch_storage.launch_transaction_lock(transaction_id):
        receipt = launch_storage.read_or_repair_launch_transaction(transaction_id)
    assert receipt is not None
    return receipt


@pytest.fixture
def launch_env(monkeypatch, tmp_path):
    import api.config as config
    import api.models as models
    import api.routes as routes
    import api.state_sync as state_sync
    from hermes_state import SessionDB

    state_dir = tmp_path / "state"
    session_dir = state_dir / "sessions"
    session_dir.mkdir(parents=True)
    sessions = OrderedDict()
    for module in (config, models, routes):
        if hasattr(module, "STATE_DIR"):
            monkeypatch.setattr(module, "STATE_DIR", state_dir)
        if hasattr(module, "SESSION_DIR"):
            monkeypatch.setattr(module, "SESSION_DIR", session_dir)
        if hasattr(module, "SESSION_INDEX_FILE"):
            monkeypatch.setattr(module, "SESSION_INDEX_FILE", session_dir / "_index.json")
        if hasattr(module, "SESSIONS"):
            monkeypatch.setattr(module, "SESSIONS", sessions)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "resolve_trusted_workspace",
        lambda value: Path(value or tmp_path).resolve(),
    )
    monkeypatch.setattr(routes, "get_last_workspace", lambda: str(tmp_path))
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [])
    state_db_path = state_dir / "state.db"
    monkeypatch.setattr(
        state_sync,
        "_get_state_db",
        lambda *_args, **_kwargs: SessionDB(state_db_path),
    )
    return routes, models, sessions, tmp_path


def test_launch_request_rejects_unknown_top_level_and_session_options():
    from api import expert_teams

    with pytest.raises(expert_teams.ContractError) as top_level:
        expert_teams.validate_standalone_launch_request(
            _launch_body(team_id="content-creator-team")
        )
    assert (top_level.value.code, top_level.value.field) == (
        "server_owned_launch_field",
        "team_id",
    )

    with pytest.raises(expert_teams.ContractError) as option:
        expert_teams.validate_standalone_launch_request(
            _launch_body(session_options={"worktree": True})
        )
    assert (option.value.code, option.value.field) == (
        "unsupported_session_option",
        "session_options.worktree",
    )


def test_launch_route_atomically_creates_one_session_and_run(launch_env):
    routes, models, sessions, workspace = launch_env
    body = _launch_body(session_options={**_launch_body()["session_options"], "workspace": str(workspace)})

    first = _post(routes, "/api/expert-teams/launch", body)
    second = _post(routes, "/api/expert-teams/launch", body)

    assert first.status == 200
    assert second.status == 200
    first_payload = first.json_body()
    second_payload = second.json_body()
    assert first_payload["ok"] is True
    assert first_payload["replayed"] is False
    assert second_payload["replayed"] is True
    assert first_payload["session"]["session_id"] == second_payload["session"]["session_id"]
    assert first_payload["run"]["run_id"] == second_payload["run"]["run_id"]
    assert len(sessions) == 1
    sid = first_payload["session"]["session_id"]
    assert models.Session.load(sid) is not None
    assert len(list((workspace / ".taiji" / "expert-teams" / "runs").glob("*.json"))) == 1


def test_launch_idempotency_conflict_never_creates_a_second_session(launch_env):
    routes, _models, sessions, workspace = launch_env
    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})
    assert _post(routes, "/api/expert-teams/launch", body).status == 200

    conflict = _post(
        routes,
        "/api/expert-teams/launch",
        {**body, "prompt": "同一个幂等键对应了另一项工作"},
    )

    assert conflict.status == 409
    assert conflict.json_body()["code"] == "launch_idempotency_conflict"
    assert len(sessions) == 1
    assert len(list((workspace / ".taiji" / "expert-teams" / "runs").glob("*.json"))) == 1


def test_outer_commit_failure_hides_session_and_run_then_same_key_recovers(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_storage

    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})
    original_write = launch_storage.write_launch_transaction
    failed = {"value": False}

    def fail_first_committed(receipt):
        if receipt.get("state") == "committed" and not failed["value"]:
            failed["value"] = True
            raise OSError("simulated outer commit failure")
        return original_write(receipt)

    monkeypatch.setattr(launch_storage, "write_launch_transaction", fail_first_committed)
    failed_launch = _post(routes, "/api/expert-teams/launch", body)

    assert failed_launch.status == 503
    failure = failed_launch.json_body()
    assert failure["code"] == "launch_recovery_required"
    assert failure["retryable"] is True
    assert "recovery" not in failure
    receipt = _launch_receipt(body)
    sid = receipt["session_id"]
    run_id = receipt["run_id"]
    assert _get(routes, f"/api/session?session_id={sid}").status == 404
    assert _get(routes, f"/api/expert-teams/run?run_id={run_id}&session_id={sid}").status == 404
    sidebar = _get(routes, "/api/sessions")
    assert sidebar.status == 200
    assert all(item["session_id"] != sid for item in sidebar.json_body()["sessions"])

    recovered = _post(routes, "/api/expert-teams/launch", body)

    assert recovered.status == 200
    assert recovered.json_body()["replayed"] is True
    assert recovered.json_body()["session"]["session_id"] == sid
    assert recovered.json_body()["run"]["run_id"] == run_id
    assert _get(routes, f"/api/session?session_id={sid}").status == 200
    assert _get(routes, f"/api/expert-teams/run?run_id={run_id}&session_id={sid}").status == 200


def test_launch_failure_before_inner_start_is_not_public(launch_env, monkeypatch):
    routes, _models, _sessions, workspace = launch_env
    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})

    def fail_start(_validated, **_kwargs):
        raise OSError("simulated run store outage")

    monkeypatch.setattr(routes, "_coordinate_expert_team_start", fail_start)
    response = _post(routes, "/api/expert-teams/launch", body)

    assert response.status == 503
    sid = _launch_receipt(body)["session_id"]
    assert _get(routes, f"/api/session?session_id={sid}").status == 404
    sidebar = _get(routes, "/api/sessions")
    assert all(item["session_id"] != sid for item in sidebar.json_body()["sessions"])


def test_run_only_mutation_is_blocked_before_inner_start_is_public(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    attempted = {}

    def probe_hidden_run(validated, **_kwargs):
        from api.expert_teams import storage

        run_id = "et-" + storage.start_transaction_id(
            validated["session_id"],
            validated["idempotency_key"],
        )
        attempted["response"] = _post(
            routes,
            "/api/expert-teams/answer",
            {"run_id": run_id, "answers": {}},
        )
        raise OSError("stop after hidden-run probe")

    monkeypatch.setattr(routes, "_coordinate_expert_team_start", probe_hidden_run)

    launch = _post(routes, "/api/expert-teams/launch", body)

    assert launch.status == 503
    assert attempted["response"].status == 404


def test_same_key_concurrency_converges_to_one_session_and_run(launch_env):
    routes, _models, sessions, workspace = launch_env
    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})
    barrier = threading.Barrier(2)

    def launch_once():
        barrier.wait(timeout=5)
        return _post(routes, "/api/expert-teams/launch", body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = [future.result(timeout=20) for future in [
            executor.submit(launch_once),
            executor.submit(launch_once),
        ]]

    assert [response.status for response in responses] == [200, 200]
    payloads = [response.json_body() for response in responses]
    assert len({payload["session"]["session_id"] for payload in payloads}) == 1
    assert len({payload["run"]["run_id"] for payload in payloads}) == 1
    assert sorted(payload["replayed"] for payload in payloads) == [False, True]
    assert len(sessions) == 1
    assert len(list((workspace / ".taiji" / "expert-teams" / "runs").glob("*.json"))) == 1


def test_sidebar_repairs_committed_session_missing_from_cross_process_index(
    launch_env,
    monkeypatch,
):
    routes, models, sessions, workspace = launch_env
    body = _launch_body(
        idempotency_key="launch-index-repair-one",
        session_options={"workspace": str(workspace), "profile": "default"},
    )
    launched = _post(routes, "/api/expert-teams/launch", body)
    assert launched.status == 200
    session_id = launched.json_body()["session"]["session_id"]

    # A sibling process can replace the rebuildable index from a stale
    # baseline after this launch commits.  Simulate the resulting projection:
    # canonical sidecar + committed launch receipt exist, but the index and
    # this process' memory cache do not contain the Session.
    sessions.clear()
    models.SESSION_INDEX_FILE.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        models,
        "_PERSISTED_SESSION_IDS_CACHE",
        (None, None, frozenset()),
    )

    sidebar = _get(routes, "/api/sessions")

    assert sidebar.status == 200
    assert session_id in {
        row["session_id"] for row in sidebar.json_body()["sessions"]
    }


def test_same_request_replay_uses_reserved_defaults_after_config_changes(
    launch_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = launch_env
    defaults = [("provider/model-a", "provider-a"), ("provider/model-b", "provider-b")]

    def shifting_defaults(_profile=None):
        return defaults.pop(0)

    monkeypatch.setattr(models, "_profile_default_model_state", shifting_defaults)
    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})
    first = _post(routes, "/api/expert-teams/launch", body)
    # The retry must not consult today's default after the receipt snapshot was
    # reserved, otherwise a harmless config change becomes a false 409.
    second = _post(routes, "/api/expert-teams/launch", body)

    assert first.status == second.status == 200
    assert first.json_body()["session"]["model"] == "provider/model-a"
    assert second.json_body()["session"]["model"] == "provider/model-a"
    assert defaults == [("provider/model-b", "provider-b")]


def test_recovery_uses_reserved_launch_profile_after_catalog_changes(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_profiles

    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    original_start = routes._coordinate_expert_team_start
    attempts = {"count": 0}

    def fail_once(validated, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("pause after launch reservation")
        return original_start(validated, **kwargs)

    monkeypatch.setattr(routes, "_coordinate_expert_team_start", fail_once)
    first = _post(routes, "/api/expert-teams/launch", body)
    reserved_profile = _launch_receipt(body)["launch_profile_snapshot"]
    changed = json.loads(json.dumps(reserved_profile, ensure_ascii=False))
    changed["stages"][0]["title"] = "目录后来被修改"
    monkeypatch.setitem(
        launch_profiles._LAUNCH_PROFILES,
        body["launch_profile_id"],
        changed,
    )

    recovered = _post(routes, "/api/expert-teams/launch", body)

    assert first.status == 503
    assert recovered.status == 200
    assert recovered.json_body()["run"]["launch_profile_snapshot"] == reserved_profile
    assert recovered.json_body()["run"]["launch_profile_snapshot"] != changed


def test_committed_launch_replays_after_catalog_profile_is_removed(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_profiles

    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    first = _post(routes, "/api/expert-teams/launch", body)
    monkeypatch.delitem(
        launch_profiles._LAUNCH_PROFILES,
        body["launch_profile_id"],
    )

    replay = _post(routes, "/api/expert-teams/launch", body)

    assert first.status == 200
    assert replay.status == 200
    assert replay.json_body()["replayed"] is True


def test_replay_after_process_cache_loss_recovers_same_objects(launch_env):
    routes, models, sessions, workspace = launch_env
    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})
    first = _post(routes, "/api/expert-teams/launch", body)
    first_payload = first.json_body()

    sessions.clear()
    second = _post(routes, "/api/expert-teams/launch", body)

    assert second.status == 200
    assert second.json_body()["replayed"] is True
    assert second.json_body()["session"]["session_id"] == first_payload["session"]["session_id"]
    assert second.json_body()["run"]["run_id"] == first_payload["run"]["run_id"]
    assert models.Session.load(first_payload["session"]["session_id"]) is not None


def test_committed_launch_replay_preserves_later_session_progress(launch_env):
    routes, models, _sessions, workspace = launch_env
    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    first = _post(routes, "/api/expert-teams/launch", body)
    sid = first.json_body()["session"]["session_id"]
    session = models.Session.load(sid)
    assert session is not None
    session.title = "用户后续修改的标题"
    session.messages.append(
        {"role": "user", "content": "公开后的正常补充", "timestamp": 2}
    )
    session.context_messages = routes._completed_semantic_messages(session.messages)
    session.save(touch_updated_at=False)

    replay = _post(routes, "/api/expert-teams/launch", body)

    assert replay.status == 200
    assert replay.json_body()["replayed"] is True
    assert replay.json_body()["session"]["title"] == "用户后续修改的标题"
    assert any(
        row.get("content") == "公开后的正常补充"
        for row in replay.json_body()["session"]["messages"]
    )


def test_committed_write_that_raises_after_rename_is_classified_as_success(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_storage

    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})
    original = launch_storage.write_launch_transaction
    raised = {"value": False}

    def write_then_raise(receipt):
        result = original(receipt)
        if receipt.get("state") == "committed" and not raised["value"]:
            raised["value"] = True
            raise OSError("simulated post-rename directory fsync error")
        return result

    monkeypatch.setattr(launch_storage, "write_launch_transaction", write_then_raise)
    response = _post(routes, "/api/expert-teams/launch", body)

    assert response.status == 200
    assert response.json_body()["ok"] is True


def test_launch_storage_symlink_is_rejected_without_writing_outside(
    launch_env,
):
    routes, _models, _sessions, workspace = launch_env
    import api.config as config

    outside = workspace / "outside-launch-store"
    outside.mkdir()
    (Path(config.STATE_DIR) / "expert-team-launches").symlink_to(
        outside,
        target_is_directory=True,
    )
    body = _launch_body(session_options={"workspace": str(workspace), "profile": "default"})

    response = _post(routes, "/api/expert-teams/launch", body)

    assert response.status == 503
    assert response.json_body()["code"] == "launch_receipt_invalid"
    assert list(outside.iterdir()) == []
    assert not list((workspace / ".taiji" / "expert-teams" / "runs").glob("*.json"))


def test_launch_transaction_lock_times_out_across_processes_with_retryable_error(
    launch_env,
):
    _routes, _models, _sessions, _workspace = launch_env
    import api.config as config
    from api.expert_teams import launch_storage

    transaction_id = launch_storage.launch_transaction_id(
        "cross-process-launch-lock-timeout"
    )
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("real cross-process flock contention requires POSIX fork")
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_launch_transaction_lock,
        args=(str(config.STATE_DIR), transaction_id, ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=10), "child process did not acquire launch lock"
        started_at = time.monotonic()
        with pytest.raises(TimeoutError) as captured:
            with launch_storage.launch_transaction_lock(
                transaction_id,
                timeout_seconds=0.2,
            ):
                raise AssertionError("contended launch lock must not be acquired")
        elapsed = time.monotonic() - started_at

        assert elapsed >= 0.15
        assert elapsed < 2.0
        assert getattr(captured.value, "retryable", False) is True
        assert "same idempotent request" in str(captured.value)
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
    assert holder.exitcode == 0


def test_launch_lock_timeout_route_is_retryable_without_exposing_object_ids(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_storage

    @contextmanager
    def fail_lock(_transaction_id, **_kwargs):
        raise launch_storage.LaunchTransactionLockTimeout("retry same request")
        yield  # pragma: no cover - contextmanager shape only

    monkeypatch.setattr(launch_storage, "launch_transaction_lock", fail_lock)

    response = _post(
        routes,
        "/api/expert-teams/launch",
        _launch_body(
            session_options={"workspace": str(workspace), "profile": "default"}
        ),
    )

    assert response.status == 503
    payload = response.json_body()
    assert payload["code"] == "launch_recovery_required"
    assert payload["retryable"] is True
    assert "session_id" not in payload
    assert "run_id" not in payload


def test_launch_atomic_write_parent_swap_never_writes_through_symlink(
    launch_env,
    monkeypatch,
):
    _routes, _models, _sessions, workspace = launch_env
    import api.config as config
    from api.expert_teams import launch_storage

    launch_root = Path(config.STATE_DIR) / "expert-team-launches" / "v1"
    receipts = launch_root / "receipts"
    receipts.mkdir(parents=True)
    displaced_receipts = launch_root / "receipts-displaced"
    outside = workspace / "outside-parent-swap"
    outside.mkdir()
    target = receipts / f"{'a' * 64}.json"
    original_open = os.open
    swapped = {"value": False}

    def swap_parent_before_temp_open(path, flags, mode=0o777, *, dir_fd=None):
        is_atomic_temp = (
            bool(flags & os.O_CREAT)
            and bool(flags & os.O_EXCL)
            and os.fspath(path).endswith(".tmp")
        )
        if is_atomic_temp and not swapped["value"]:
            receipts.rename(displaced_receipts)
            receipts.symlink_to(outside, target_is_directory=True)
            swapped["value"] = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(launch_storage.os, "open", swap_parent_before_temp_open)

    with pytest.raises(
        launch_storage.LaunchTransactionIntegrityError,
        match="parent changed|unsafe",
    ):
        launch_storage._atomic_write_json(target, {"private": "receipt"})

    assert swapped["value"] is True
    assert list(outside.iterdir()) == []


def test_launch_storage_accepts_maximum_valid_session_id(launch_env):
    _routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_storage

    transaction_id = launch_storage.launch_transaction_id("maximum-session-id")
    session_id = "s" * 240
    profile_snapshot = {"id": "content-work-report"}
    initial_session_snapshot = {
        "session_id": session_id,
        "expert_team_launch_transaction_id": transaction_id,
    }
    receipt = launch_storage.new_reserved_receipt(
        transaction_id=transaction_id,
        idempotency_key="maximum-session-id",
        request_fingerprint="f" * 64,
        launch_profile_id=profile_snapshot["id"],
        launch_profile_snapshot=profile_snapshot,
        prompt="测试最长合法会话标识",
        session_options={"workspace": str(workspace)},
        session_id=session_id,
        workspace=str(workspace),
        initial_session_snapshot=initial_session_snapshot,
    )

    with launch_storage.launch_transaction_lock(transaction_id):
        launch_storage.write_launch_transaction(receipt)

    assert launch_storage.read_launch_transaction(transaction_id) == receipt


def test_launch_visibility_enumeration_parent_swap_to_empty_fails_closed(
    launch_env,
    monkeypatch,
):
    _routes, _models, _sessions, workspace = launch_env
    import api.config as config
    from api.expert_teams import launch_storage

    registry = (
        Path(config.STATE_DIR)
        / "expert-team-launches"
        / "v1"
        / "by-session"
    )
    registry.mkdir(parents=True)
    session_id = "hidden-session-parent-swap"
    (registry / f"{session_id}.json").write_text(
        json.dumps({"session_id": session_id}),
        encoding="utf-8",
    )
    displaced_registry = registry.with_name("by-session-displaced")
    empty_outside = workspace / "empty-visibility-registry"
    empty_outside.mkdir()
    original_glob = Path.glob
    original_listdir = os.listdir
    swapped = {"value": False}

    def swap_registry_once():
        if swapped["value"]:
            return
        registry.rename(displaced_registry)
        registry.symlink_to(empty_outside, target_is_directory=True)
        swapped["value"] = True

    def swap_before_path_glob(path, pattern, *args, **kwargs):
        if Path(path) == registry:
            swap_registry_once()
        return original_glob(path, pattern, *args, **kwargs)

    def swap_before_anchored_listdir(path):
        swap_registry_once()
        return original_listdir(path)

    monkeypatch.setattr(Path, "glob", swap_before_path_glob)
    monkeypatch.setattr(launch_storage.os, "listdir", swap_before_anchored_listdir)

    with pytest.raises(
        launch_storage.LaunchTransactionIntegrityError,
        match="parent changed|unsafe",
    ):
        launch_storage.hidden_session_ids()

    assert swapped["value"] is True
    assert list(empty_outside.iterdir()) == []


def test_receipt_registry_swap_after_enumeration_still_fails_closed(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    import api.config as config
    from api.expert_teams import launch_storage

    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    monkeypatch.setattr(
        routes,
        "_coordinate_expert_team_start",
        lambda _validated, **_kwargs: (_ for _ in ()).throw(
            OSError("hold a receipt-only recovery record")
        ),
    )
    assert _post(routes, "/api/expert-teams/launch", body).status == 503
    receipt = _launch_receipt(body)
    launch_storage._session_binding_path(receipt["session_id"]).unlink()

    receipts = (
        Path(config.STATE_DIR)
        / "expert-team-launches"
        / "v1"
        / "receipts"
    )
    displaced_receipts = receipts.with_name("receipts-displaced-after-list")
    empty_outside = workspace / "empty-receipts-after-list"
    empty_outside.mkdir()
    original_list = launch_storage._list_json_paths
    swapped = {"value": False}

    def list_then_swap(directory):
        paths = original_list(directory)
        if Path(directory) == receipts and not swapped["value"]:
            receipts.rename(displaced_receipts)
            receipts.symlink_to(empty_outside, target_is_directory=True)
            swapped["value"] = True
        return paths

    monkeypatch.setattr(launch_storage, "_list_json_paths", list_then_swap)

    with pytest.raises(
        launch_storage.LaunchTransactionIntegrityError,
        match="registry changed|unsafe",
    ):
        launch_storage.hidden_session_ids()

    assert swapped["value"] is True
    assert list(empty_outside.iterdir()) == []


def test_launch_response_never_exposes_internal_transaction_markers(launch_env):
    routes, _models, _sessions, workspace = launch_env
    response = _post(
        routes,
        "/api/expert-teams/launch",
        _launch_body(session_options={"workspace": str(workspace), "profile": "default"}),
    )

    serialized = json.dumps(response.json_body(), ensure_ascii=False)
    assert response.status == 200
    assert "expert_team_launch_transaction_id" not in serialized
    assert "expert_team_start_transaction_id" not in serialized
    assert "expert_team_start_transaction_ids" not in serialized
    assert "start_transaction_id" not in serialized


def test_missing_launch_reverse_binding_fails_closed_for_every_public_read(
    launch_env,
):
    routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_storage

    launched = _post(
        routes,
        "/api/expert-teams/launch",
        _launch_body(session_options={"workspace": str(workspace), "profile": "default"}),
    ).json_body()
    sid = launched["session"]["session_id"]
    run_id = launched["run"]["run_id"]
    launch_storage._session_binding_path(sid).unlink()

    assert _get(routes, f"/api/session?session_id={sid}").status == 404
    assert _get(routes, f"/api/expert-teams/run?run_id={run_id}&session_id={sid}").status == 404
    sidebar = _get(routes, "/api/sessions")
    assert sidebar.status == 200
    assert all(row["session_id"] != sid for row in sidebar.json_body()["sessions"])


def test_sidebar_still_hides_marked_launch_when_hidden_index_is_empty(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, workspace = launch_env
    from api.expert_teams import launch_storage

    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    original_start = routes._coordinate_expert_team_start
    monkeypatch.setattr(
        routes,
        "_coordinate_expert_team_start",
        lambda _validated, **_kwargs: (_ for _ in ()).throw(
            OSError("hold before start")
        ),
    )
    _post(routes, "/api/expert-teams/launch", body)
    sid = _launch_receipt(body)["session_id"]
    monkeypatch.setattr(routes, "_coordinate_expert_team_start", original_start)

    # Simulate a lost reverse index plus a stale/empty hidden-id cache.  The
    # durable Session marker must remain sufficient to keep the row private.
    launch_storage._session_binding_path(sid).unlink()
    monkeypatch.setattr(launch_storage, "hidden_session_ids", lambda: set())

    sidebar = _get(routes, "/api/sessions")

    assert sidebar.status == 200
    assert all(row["session_id"] != sid for row in sidebar.json_body()["sessions"])


def test_sidebar_fails_closed_when_launch_visibility_registry_is_unreadable(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, _workspace = launch_env
    from api.expert_teams import launch_storage

    monkeypatch.setattr(
        launch_storage,
        "hidden_session_ids",
        lambda: (_ for _ in ()).throw(OSError("registry unavailable")),
    )

    response = _get(routes, "/api/sessions")

    assert response.status == 503
    assert response.json_body()["code"] == "launch_visibility_unavailable"


def test_sidebar_rechecks_launch_visibility_after_merging_state_db_rows(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, _workspace = launch_env
    from api.expert_teams import launch_storage

    hidden_sid = "etl-hidden-during-sidebar-merge"
    snapshots = iter((set(), {hidden_sid}))
    monkeypatch.setattr(
        launch_storage,
        "hidden_session_ids",
        lambda: next(snapshots),
    )
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: [])
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": True})
    monkeypatch.setattr(
        routes,
        "get_cli_sessions",
        lambda: [
            {
                "session_id": hidden_sid,
                "title": "尚未公开的专家团任务",
                "message_count": 2,
                "created_at": 1,
                "updated_at": 2,
                "last_message_at": 2,
                "profile": "default",
            }
        ],
    )
    monkeypatch.setattr(
        routes,
        "_dedupe_cli_sidebar_sessions_for_api",
        lambda rows, _represented: rows,
    )

    response = _get(routes, "/api/sessions")

    assert response.status == 200
    assert hidden_sid not in {
        row["session_id"] for row in response.json_body()["sessions"]
    }


def test_sidebar_fails_closed_if_visibility_breaks_after_source_merge(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, _workspace = launch_env
    from api.expert_teams import launch_storage

    calls = {"count": 0}

    def fail_second_snapshot():
        calls["count"] += 1
        if calls["count"] == 1:
            return set()
        raise OSError("visibility registry changed during merge")

    monkeypatch.setattr(
        launch_storage,
        "hidden_session_ids",
        fail_second_snapshot,
    )
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: [])
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})

    response = _get(routes, "/api/sessions")

    assert response.status == 503
    assert response.json_body()["code"] == "launch_visibility_unavailable"
    assert calls["count"] == 2


def test_real_launch_registry_enumeration_error_reaches_sidebar_fail_closed(
    launch_env,
    monkeypatch,
):
    routes, _models, _sessions, _workspace = launch_env
    from api.expert_teams import launch_storage

    original_ensure = launch_storage._ensure_directory

    def fail_by_session_directory(path):
        if Path(path).name == "by-session":
            raise OSError("simulated launch registry directory failure")
        return original_ensure(path)

    monkeypatch.setattr(
        launch_storage,
        "_ensure_directory",
        fail_by_session_directory,
    )

    response = _get(routes, "/api/sessions")

    assert response.status == 503
    assert response.json_body()["code"] == "launch_visibility_unavailable"


def test_uncommitted_launch_rejects_public_session_and_run_mutations(
    launch_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = launch_env
    from api.expert_teams import launch_storage

    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    original_write = launch_storage.write_launch_transaction

    def fail_outer_commit(receipt):
        if receipt.get("state") == "committed":
            raise OSError("hold launch behind outer receipt")
        return original_write(receipt)

    monkeypatch.setattr(
        launch_storage,
        "write_launch_transaction",
        fail_outer_commit,
    )
    _post(routes, "/api/expert-teams/launch", body)
    receipt = _launch_receipt(body)
    sid = receipt["session_id"]
    run_id = receipt["run_id"]
    before = models.Session.load(sid)
    assert before is not None

    renamed = _post(
        routes,
        "/api/session/rename",
        {"session_id": sid, "title": "不应写入"},
    )
    answered = _post(
        routes,
        "/api/expert-teams/answer",
        {"session_id": sid, "run_id": run_id, "answers": {}},
    )
    hidden_reads = [
        _get(routes, f"/api/session/export?session_id={sid}"),
        _get(routes, f"/api/session/export-bundle?session_id={sid}"),
        _get(routes, f"/api/session/status?session_id={sid}"),
        _get(routes, f"/api/expert-teams/run?run_id={run_id}"),
    ]

    after = models.Session.load(sid)
    assert renamed.status == 404
    assert answered.status == 404
    assert [response.status for response in hidden_reads] == [404, 404, 404, 404]
    assert after is not None
    assert after.title == before.title
    assert after.messages == before.messages


def test_outer_commit_refuses_session_polluted_during_hidden_window(
    launch_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = launch_env
    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )
    original_start = routes._coordinate_expert_team_start

    def pollute_then_start(validated, **kwargs):
        session = models.Session.load(validated["session_id"])
        assert session is not None
        session.messages.append(
            {"role": "user", "content": "foreign hidden write", "timestamp": 1}
        )
        session.save(touch_updated_at=False)
        return original_start(validated, **kwargs)

    monkeypatch.setattr(routes, "_coordinate_expert_team_start", pollute_then_start)

    response = _post(routes, "/api/expert-teams/launch", body)

    assert response.status == 503
    assert response.json_body()["code"] == "launch_recovery_required"
    sid = _launch_receipt(body)["session_id"]
    assert _get(routes, f"/api/session?session_id={sid}").status == 404


def test_new_session_failure_compensation_preserves_concurrent_sidecar_write(
    launch_env,
    monkeypatch,
):
    routes, models, _sessions, workspace = launch_env
    body = _launch_body(
        session_options={"workspace": str(workspace), "profile": "default"}
    )

    def mutate_sidecar_then_fail(session, _messages):
        concurrent = models.Session.load(session.session_id)
        assert concurrent is not None
        concurrent.title = "并发写入必须保留"
        concurrent.save(touch_updated_at=False, skip_index=True)
        raise OSError("simulated state.db failure after concurrent sidecar write")

    monkeypatch.setattr(routes, "_replace_state_db_truth", mutate_sidecar_then_fail)

    response = _post(routes, "/api/expert-teams/launch", body)

    assert response.status == 503
    sid = _launch_receipt(body)["session_id"]
    durable = models.Session.load(sid)
    assert durable is not None
    assert durable.title == "并发写入必须保留"
