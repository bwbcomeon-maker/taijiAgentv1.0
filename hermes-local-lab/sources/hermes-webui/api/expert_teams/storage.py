"""JSON storage for expert team runs."""

from __future__ import annotations

import json
import re
from pathlib import Path


def runs_dir(workspace: Path) -> Path:
    return Path(workspace) / ".taiji" / "expert-teams" / "runs"


def safe_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if not run_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", run_id):
        raise ValueError("Invalid expert team run_id")
    return run_id


def run_path(workspace: Path, run_id: str) -> Path:
    return runs_dir(workspace) / f"{safe_run_id(run_id)}.json"


def write_run(workspace: Path, run: dict) -> dict:
    path = run_path(workspace, str(run.get("run_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return run


def read_run(workspace: Path, run_id: str) -> dict:
    path = run_path(workspace, run_id)
    if not path.exists():
        raise FileNotFoundError(run_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("run_id", safe_run_id(run_id))
    return data


def list_runs(workspace: Path) -> list[dict]:
    root = runs_dir(workspace)
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


def latest_run_for_session(workspace: Path, session_id: str) -> dict:
    sid = str(session_id or "").strip()
    for run in list_runs(workspace):
        if str(run.get("session_id") or "").strip() == sid:
            return run
    raise FileNotFoundError(session_id)
