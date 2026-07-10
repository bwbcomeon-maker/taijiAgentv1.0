"""Cross-process compare-and-swap contract for expert-team run mutations."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest


_PROCESS_TIMEOUT_SECONDS = 20


def _control(run: dict, key: str, **extra) -> dict:
    current = run.get("current_stage") if isinstance(run.get("current_stage"), dict) else {}
    return {
        "run_id": run["run_id"],
        "session_id": run["session_id"],
        "expected_version": run["version"],
        "stage_id": current.get("task_id") or current.get("id"),
        "idempotency_key": key,
        **extra,
    }


def _mutation_worker(
    workspace: str,
    body: dict,
    marker: str,
    start_barrier,
    result_path: str,
) -> None:
    from api import expert_teams

    try:
        start_barrier.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
        result = expert_teams.answer_expert_team(Path(workspace), body)
        payload = {"kind": "ok", "marker": marker, "version": result["version"]}
    except expert_teams.ExpertTeamStateConflict as exc:
        payload = {"kind": "conflict", "marker": marker, "code": exc.code}
    except BaseException as exc:  # pragma: no cover - diagnostic path for child failures
        payload = {
            "kind": "error",
            "marker": marker,
            "type": type(exc).__name__,
            "message": str(exc),
        }
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


def _failing_replace_worker(workspace: str, body: dict, result_path: str) -> None:
    from api import expert_teams
    from api.expert_teams import storage

    original_replace = storage.os.replace

    def fail_replace(_source, _target):
        raise OSError("injected replace failure")

    storage.os.replace = fail_replace
    try:
        expert_teams.answer_expert_team(Path(workspace), body)
    except BaseException as exc:
        payload = {"kind": "error", "type": type(exc).__name__, "message": str(exc)}
    else:  # pragma: no cover - the injected failure must be observed
        payload = {"kind": "unexpected_success"}
    finally:
        storage.os.replace = original_replace
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


def _join_processes(processes: list, *, timeout: int = _PROCESS_TIMEOUT_SECONDS) -> None:
    for process in processes:
        process.join(timeout=timeout)
    hanging = [process for process in processes if process.is_alive()]
    for process in hanging:
        process.terminate()
        process.join(timeout=5)
    assert not hanging, "cross-process mutation did not release its lock"
    assert [process.exitcode for process in processes] == [0] * len(processes)


def _process_context():
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("cross-process CAS test requires fork-capable POSIX workers")
    return multiprocessing.get_context("fork")


def _worker_results(paths: list[Path]) -> list[dict]:
    missing = [str(path) for path in paths if not path.exists()]
    assert not missing, f"cross-process workers did not report results: {missing}"
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def _new_run(tmp_path: Path, *, session_id: str) -> dict:
    from api import expert_teams

    return expert_teams.start_expert_team(
        tmp_path,
        {
            "session_id": session_id,
            "team_id": "content-creator-team",
            "prompt": "起草一份工作汇报",
        },
    )


def test_cross_process_same_version_has_one_winner_and_one_version_conflict(tmp_path):
    from api.expert_teams.storage import run_path

    run = _new_run(tmp_path, session_id="sid-cross-process-cas")
    question_id = next(question["id"] for question in run["questions"] if question.get("required"))
    # A large payload keeps both real processes inside the read/derive/write window
    # long enough to exercise the storage boundary without mocking production reads.
    answers = {
        "A": "winner-A-" + ("A" * 2_000_000),
        "B": "winner-B-" + ("B" * 2_000_000),
    }
    context = _process_context()
    barrier = context.Barrier(2)
    processes = []
    result_paths = []
    for marker, answer in answers.items():
        body = _control(
            run,
            f"cross-process-{marker}",
            answers={question_id: answer},
        )
        result_path = tmp_path / f"worker-{marker}.json"
        result_paths.append(result_path)
        processes.append(
            context.Process(
                target=_mutation_worker,
                args=(str(tmp_path), body, marker, barrier, str(result_path)),
            )
        )

    for process in processes:
        process.start()
    _join_processes(processes)
    results = _worker_results(result_paths)

    raw_text = run_path(tmp_path, run["run_id"]).read_text(encoding="utf-8")
    raw_run = json.loads(raw_text)
    assert raw_run["run_id"] == run["run_id"]
    assert raw_run["version"] == run["version"] + 1
    stored_question = next(question for question in raw_run["questions"] if question["id"] == question_id)
    stored_markers = [marker for marker, answer in answers.items() if stored_question["answer"] == answer]
    assert len(stored_markers) == 1
    stored_winner = stored_markers[0]
    stored_loser = "B" if stored_winner == "A" else "A"
    assert answers[stored_loser] not in raw_text
    assert [entry["idempotency_key"] for entry in raw_run["action_journal"]] == [
        f"cross-process-{stored_winner}"
    ]

    winners = [result for result in results if result.get("kind") == "ok"]
    conflicts = [result for result in results if result.get("kind") == "conflict"]
    assert len(winners) == 1, results
    assert winners[0]["marker"] == stored_winner
    assert conflicts == [
        {
            "kind": "conflict",
            "marker": "B" if winners[0]["marker"] == "A" else "A",
            "code": "version_conflict",
        }
    ]


def test_failed_cross_process_writer_releases_lock_and_preserves_json(tmp_path):
    from api.expert_teams.storage import run_path

    run = _new_run(tmp_path, session_id="sid-cross-process-lock-failure")
    path = run_path(tmp_path, run["run_id"])
    original_payload = json.loads(path.read_text(encoding="utf-8"))
    question_id = next(question["id"] for question in run["questions"] if question.get("required"))
    failing_body = _control(
        run,
        "replace-failure",
        answers={question_id: "this answer must never be committed"},
    )
    context = _process_context()
    failure_result_path = tmp_path / "failing-worker.json"
    failing_process = context.Process(
        target=_failing_replace_worker,
        args=(str(tmp_path), failing_body, str(failure_result_path)),
    )

    failing_process.start()
    _join_processes([failing_process])
    assert _worker_results([failure_result_path]) == [
        {"kind": "error", "type": "OSError", "message": "injected replace failure"}
    ]
    assert json.loads(path.read_text(encoding="utf-8")) == original_payload
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))

    recovery_barrier = context.Barrier(1)
    recovery_result_path = tmp_path / "recovery-worker.json"
    recovery_body = _control(
        run,
        "after-replace-failure",
        answers={question_id: "recovered winner"},
    )
    recovery_process = context.Process(
        target=_mutation_worker,
        args=(
            str(tmp_path),
            recovery_body,
            "recovery",
            recovery_barrier,
            str(recovery_result_path),
        ),
    )
    recovery_process.start()
    _join_processes([recovery_process])
    assert _worker_results([recovery_result_path]) == [
        {"kind": "ok", "marker": "recovery", "version": run["version"] + 1}
    ]

    recovered_payload = json.loads(path.read_text(encoding="utf-8"))
    assert recovered_payload["version"] == run["version"] + 1
    recovered_question = next(
        question for question in recovered_payload["questions"] if question["id"] == question_id
    )
    assert recovered_question["answer"] == "recovered winner"
    assert "this answer must never be committed" not in path.read_text(encoding="utf-8")
