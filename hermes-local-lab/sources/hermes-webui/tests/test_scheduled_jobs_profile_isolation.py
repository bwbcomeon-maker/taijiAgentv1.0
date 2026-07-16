"""Regression test: /api/crons must read jobs.json from the *active profile*.

Before the fix, `cron.jobs.list_jobs()` resolved HERMES_HOME from os.environ
at call time, ignoring the WebUI's per-request thread-local profile. So the
Scheduled Jobs panel showed the process-default profile's jobs regardless of
which profile the user had selected in the cookie.

This test writes two distinct jobs.json files (default + a named profile),
then verifies `cron_profile_context` pins the cron.jobs call to the named
profile's file.
"""
import json
import os
import pathlib
import sys
import threading
from unittest import mock

import pytest

# Ensure both repos are importable.
WEBUI_ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENT_ROOT = pathlib.Path(os.environ.get("HERMES_AGENT_ROOT", pathlib.Path.home() / "hermes-agent"))
for p in (str(WEBUI_ROOT), str(AGENT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _write_jobs(home: pathlib.Path, jobs: list):
    cron_dir = home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "jobs.json").write_text(
        json.dumps({"jobs": jobs}), encoding="utf-8"
    )


def test_cron_profile_context_pins_profile_home(tmp_path, monkeypatch):
    """The context manager should swap cron.jobs to read from the named profile."""
    pytest.importorskip("cron.jobs")  # auto-skip when hermes-agent is unavailable

    default_home = tmp_path / "default_home"
    meow_home = tmp_path / "default_home" / "profiles" / "meow"

    _write_jobs(default_home, [{"id": "d1", "name": "default-job"}])
    _write_jobs(meow_home, [{"id": "m1", "name": "meow-job"}])

    # Point base at default_home; HERMES_HOME env starts at default.
    monkeypatch.setenv("HERMES_HOME", str(default_home))

    from api import profiles as p

    monkeypatch.setattr(p, "_DEFAULT_HERMES_HOME", default_home)

    # Baseline: no context → default profile.
    from cron.jobs import list_jobs
    # Force cron.jobs to re-evaluate its cached constants for this test run.
    import cron.jobs as _cj
    _cj.HERMES_DIR = default_home
    _cj.CRON_DIR = default_home / "cron"
    _cj.JOBS_FILE = _cj.CRON_DIR / "jobs.json"
    _cj.OUTPUT_DIR = _cj.CRON_DIR / "output"

    jobs_before = list_jobs(include_disabled=True)
    assert any(j["id"] == "d1" for j in jobs_before), \
        f"Expected default-profile job before entering context, got {jobs_before}"

    # Simulate a request with TLS profile = 'meow'.
    p.set_request_profile("meow")
    try:
        with p.cron_profile_context():
            jobs_inside = list_jobs(include_disabled=True)
            assert any(j["id"] == "m1" for j in jobs_inside), \
                f"Expected meow-profile job inside context, got {jobs_inside}"
            assert not any(j["id"] == "d1" for j in jobs_inside), \
                "Default-profile job leaked into meow context"
    finally:
        p.clear_request_profile()

    # After the context exits, we should be back to default.
    jobs_after = list_jobs(include_disabled=True)
    assert any(j["id"] == "d1" for j in jobs_after), \
        f"Expected default-profile job after exiting context, got {jobs_after}"


def test_cron_profile_context_for_home_pins_explicit_home(tmp_path):
    """Thread variant: pin by explicit path (no TLS)."""
    pytest.importorskip("cron.jobs")  # auto-skip when hermes-agent is unavailable

    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    _write_jobs(home_a, [{"id": "a1", "name": "A"}])
    _write_jobs(home_b, [{"id": "b1", "name": "B"}])

    # Start with env pointing at A.
    prev = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(home_a)
    try:
        import cron.jobs as _cj
        _cj.HERMES_DIR = home_a
        _cj.CRON_DIR = home_a / "cron"
        _cj.JOBS_FILE = _cj.CRON_DIR / "jobs.json"
        _cj.OUTPUT_DIR = _cj.CRON_DIR / "output"

        from cron.jobs import list_jobs
        from api.profiles import cron_profile_context_for_home

        assert any(j["id"] == "a1" for j in list_jobs(include_disabled=True))

        with cron_profile_context_for_home(home_b):
            jobs_inside = list_jobs(include_disabled=True)
            assert any(j["id"] == "b1" for j in jobs_inside), jobs_inside
            assert not any(j["id"] == "a1" for j in jobs_inside), jobs_inside

        # Restored to A.
        assert any(j["id"] == "a1" for j in list_jobs(include_disabled=True))
    finally:
        if prev is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = prev


def test_cron_profile_context_serializes_concurrent_access(tmp_path):
    """The lock must prevent concurrent contexts from interleaving."""
    from api.profiles import cron_profile_context_for_home

    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()

    # Ensure the context lock is released between tests.
    from api import profiles as p
    assert not p._cron_env_lock.locked(), \
        "Lock leaked from a previous test"

    observed = []
    barrier = threading.Barrier(2)

    def worker(home, tag):
        barrier.wait()
        with cron_profile_context_for_home(home):
            observed.append(("enter", tag, os.environ["HERMES_HOME"]))
            # If serialization works, the partner thread cannot be inside
            # its own context at this moment.
            observed.append(("exit", tag))

    t1 = threading.Thread(target=worker, args=(home_a, "A"))
    t2 = threading.Thread(target=worker, args=(home_b, "B"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Every enter must be immediately followed by its matching exit (no
    # interleaving), because the lock serializes the two contexts.
    assert len(observed) == 4
    first, second, third, fourth = observed
    assert first[0] == "enter" and second[0] == "exit" and first[1] == second[1]
    assert third[0] == "enter" and fourth[0] == "exit" and third[1] == fourth[1]


def test_cron_run_does_not_silently_swallow_profile_resolution_errors():
    """_handle_cron_run must NOT silently fall through to profile_home=None
    when get_active_hermes_home() raises.

    A silent fallback would re-introduce the exact bug #1573 fixes — the
    worker thread would run unpinned against the process-global HERMES_HOME,
    silently corrupting cross-profile state. We'd rather 500 the request
    than risk that, since get_active_hermes_home() raising at all from
    inside a request handler means api.profiles is in a state we shouldn't
    be making cron decisions in.

    Source-level assertion to catch any future re-introduction of the
    over-broad except clause.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "api" / "routes.py").read_text(encoding="utf-8")

    # Locate _handle_cron_run definition; assert the spawn block does NOT
    # wrap get_active_hermes_home() in a bare except that falls back to None.
    idx = src.find("def _handle_cron_run(handler, body):")
    assert idx != -1, "_handle_cron_run not found"
    body = src[idx : idx + 4000]

    # The spawn site must call get_active_hermes_home() unguarded (no
    # try/except around it specifically), because a silent fallback to None
    # is exactly what would re-introduce #1573.
    spawn_idx = body.find("start_legacy_migration_guarded_worker(")
    assert spawn_idx != -1, "migration-guarded worker spawn not found in _handle_cron_run"
    assert "threading.Thread(target=_run_cron_tracked" not in body

    # Look at the 1500 chars before the spawn — should NOT contain the
    # `_profile_home = None` fallback pattern.
    pre_spawn = body[max(0, spawn_idx - 1500) : spawn_idx]
    assert "_profile_home = None" not in pre_spawn, (
        "_handle_cron_run silently falls back to _profile_home=None when "
        "get_active_hermes_home() raises. That re-introduces bug #1573 — "
        "the worker thread would run unpinned against the process-global "
        "HERMES_HOME. Let the exception propagate (500 the request) rather "
        "than corrupt cross-profile state silently."
    )


def test_webui_installs_profile_context_on_in_process_scheduler_run_job(tmp_path, monkeypatch):
    """If WebUI ever runs cron.scheduler.tick in-process, scheduled run_job calls
    must execute under the job's selected profile home, not the process-global
    HERMES_HOME that happened to be active when the scheduler thread fired.
    """
    import types

    from api import profiles as p

    default_home = tmp_path / "home"
    research_home = default_home / "profiles" / "research"
    research_home.mkdir(parents=True)
    events = []

    class Ctx:
        def __init__(self, home):
            self.home = str(home)

        def __enter__(self):
            events.append(("enter", self.home))
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", self.home))
            return False

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_scheduler = types.ModuleType("cron.scheduler")
    cron_scheduler.run_job = lambda job: events.append(("run", job["id"])) or "ok"

    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.scheduler", cron_scheduler)
    monkeypatch.setattr(p, "_DEFAULT_HERMES_HOME", default_home)
    monkeypatch.setattr(p, "cron_profile_context_for_home", Ctx)
    monkeypatch.setattr(p, "publish_session_list_changed", lambda reason: events.append(("publish", reason)))

    p.install_cron_scheduler_profile_isolation()

    assert cron_scheduler.run_job({"id": "job1575", "profile": "research"}) == "ok"
    assert events == [
        ("enter", str(research_home)),
        ("run", "job1575"),
        ("exit", str(research_home)),
        ("publish", "cron_complete"),
    ]


def test_scheduler_run_job_wrapper_does_not_reenter_manual_cron_context(tmp_path, monkeypatch):
    """Manual /api/crons/run already pins run_job before calling it.

    The scheduler safety wrapper must detect that existing context and delegate
    directly, otherwise the non-reentrant env lock would deadlock or override the
    manual execution profile.
    """
    import types

    from api import profiles as p

    events = []

    class Ctx:
        def __init__(self, home):
            self.home = str(home)

        def __enter__(self):
            events.append(("unexpected-enter", self.home))
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append(("unexpected-exit", self.home))
            return False

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_scheduler = types.ModuleType("cron.scheduler")
    cron_scheduler.run_job = lambda job: events.append(("run", job["id"])) or "ok"

    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.scheduler", cron_scheduler)
    monkeypatch.setattr(p, "_DEFAULT_HERMES_HOME", tmp_path / "home")
    monkeypatch.setattr(p, "cron_profile_context_for_home", Ctx)
    monkeypatch.setattr(p, "publish_session_list_changed", lambda reason: events.append(("unexpected-publish", reason)))
    monkeypatch.setattr(p._tls, "cron_profile_depth", 1, raising=False)

    p.install_cron_scheduler_profile_isolation()

    assert cron_scheduler.run_job({"id": "manual1575", "profile": "research"}) == "ok"
    assert events == [("run", "manual1575")]


def test_cron_worker_does_not_silently_fall_back_on_profile_context_failure():
    """The subprocess target must not fall back to an unpinned cron run.

    A silent fallback would leave the job running against process-global
    HERMES_HOME, silently corrupting cross-profile state — the same class of bug
    as #1573. The child process may report the exception to the parent, but it
    must not continue into run_job outside the requested profile context.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "api" / "routes.py").read_text(encoding="utf-8")

    idx = src.find("def _cron_job_subprocess_main(job")
    assert idx != -1, "_cron_job_subprocess_main not found"
    body = src[idx : idx + 2000]

    assert "with cron_profile_context_for_home(execution_profile_home):" in body
    assert "result = _run()" in body
    assert "ctx = None" not in body
    assert "except Exception" not in body[:body.find("with cron_profile_context_for_home")], (
        "cron subprocess target appears to catch profile-context setup before "
        "entering the context; do not fall back to an unpinned run_job call."
    )


def _prepare_manual_cron_route(monkeypatch, tmp_path, jobs, worker):
    import cron.jobs as cron_jobs
    from api import profiles, routes

    monkeypatch.setattr(cron_jobs, "get_job", lambda job_id: jobs.get(job_id))
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path / "owner")
    monkeypatch.setattr(routes, "_profile_home_for_cron_job", lambda _job: tmp_path / "execution")
    monkeypatch.setattr(routes, "_run_cron_tracked", worker)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: payload)
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: {"error": message, "status": status},
    )
    routes._RUNNING_CRON_JOBS.clear()
    return routes


def test_manual_cron_worker_blocks_apply_and_queued_writer_blocks_new_cron(
    tmp_path, monkeypatch
):
    """The cron lease spans the complete worker/subprocess lifecycle."""
    import time

    from api import legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    monkeypatch.setattr(migration, "_MIGRATION_STATE_BARRIER", barrier)
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    release_second = threading.Event()
    writer_entered = threading.Event()
    release_writer = threading.Event()

    def worker(job, _profile_home, _execution_profile_home):
        try:
            if job["id"] == "cron-one":
                first_started.set()
                assert release_first.wait(timeout=5)
            else:
                second_started.set()
                assert release_second.wait(timeout=5)
        finally:
            routes._mark_cron_done(job["id"])

    jobs = {
        "cron-one": {"id": "cron-one"},
        "cron-two": {"id": "cron-two"},
    }
    routes = _prepare_manual_cron_route(monkeypatch, tmp_path, jobs, worker)
    assert migration._route_touches_migration_state("POST", "/api/crons/run") is True
    assert migration._route_touches_migration_state("POST", "/api/cron/run") is True
    assert routes._handle_cron_run(None, {"job_id": "cron-one"})["status"] == "running"
    assert first_started.wait(timeout=2)

    def apply():
        with barrier.write():
            writer_entered.set()
            assert release_writer.wait(timeout=5)

    writer = threading.Thread(target=apply, daemon=True)
    writer.start()
    deadline = time.monotonic() + 2
    while barrier._waiting_writers == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert barrier._waiting_writers == 1
    assert not writer_entered.is_set(), "Apply entered before the active cron finished"

    second_response = {}

    def start_second():
        second_response.update(routes._handle_cron_run(None, {"job_id": "cron-two"}))

    second_request = threading.Thread(target=start_second, daemon=True)
    second_request.start()
    time.sleep(0.05)
    assert not second_started.is_set(), "new cron bypassed a queued migration writer"

    release_first.set()
    assert writer_entered.wait(timeout=2), "Apply did not acquire after the active cron ended"
    assert not second_started.is_set(), "new cron started inside the exclusive Apply window"
    release_writer.set()
    writer.join(timeout=2)
    assert second_started.wait(timeout=2), "new cron did not resume after Apply"
    release_second.set()
    second_request.join(timeout=2)
    assert second_response["status"] == "running"
    deadline = time.monotonic() + 2
    while barrier._readers and time.monotonic() < deadline:
        time.sleep(0.005)
    assert barrier._readers == 0
    assert routes._is_cron_running("cron-one")[0] is False
    assert routes._is_cron_running("cron-two")[0] is False


def test_manual_cron_start_failure_and_cancel_release_lease_and_running_marker(
    tmp_path, monkeypatch
):
    from api import legacy_session_migration as migration

    barrier = migration._MigrationStateBarrier()
    monkeypatch.setattr(migration, "_MIGRATION_STATE_BARRIER", barrier)
    cancelled = threading.Event()
    worker_started = threading.Event()

    def cancellable(job, _profile_home, _execution_profile_home):
        try:
            worker_started.set()
            assert cancelled.wait(timeout=5)
        finally:
            routes._mark_cron_done(job["id"])

    jobs = {
        "cron-cancel": {"id": "cron-cancel"},
        "cron-start-failure": {"id": "cron-start-failure"},
    }
    routes = _prepare_manual_cron_route(monkeypatch, tmp_path, jobs, cancellable)
    routes._handle_cron_run(None, {"job_id": "cron-cancel"})
    assert worker_started.wait(timeout=2)
    assert barrier._readers == 1
    cancelled.set()
    deadline = __import__("time").monotonic() + 2
    while barrier._readers and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.005)
    assert barrier._readers == 0
    assert routes._is_cron_running("cron-cancel")[0] is False

    class FailingThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("injected cron thread start failure")

    monkeypatch.setattr(migration.threading, "Thread", FailingThread)
    with pytest.raises(RuntimeError, match="injected cron thread start failure"):
        routes._handle_cron_run(None, {"job_id": "cron-start-failure"})
    assert barrier._readers == 0
    assert routes._is_cron_running("cron-start-failure")[0] is False
