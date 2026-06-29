#!/usr/bin/env python3
"""Remove local expert-team runs and expert-team-marked chat sessions.

The script is intentionally conservative:
- dry-run by default;
- run files are removed only from explicitly supplied workspaces;
- chat sessions are removed only when they carry expert-team markers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


EXPERT_TEAM_MESSAGE_TYPES = {
    "expert_team_start",
    "expert_team_lifecycle",
    "expert_team_delivery",
}


def default_sessions_dir() -> Path:
    state_dir = os.getenv("TAIJI_WEBUI_STATE_DIR") or os.getenv("HERMES_WEBUI_STATE_DIR")
    if state_dir:
        return Path(state_dir).expanduser().resolve() / "sessions"
    runtime_home = os.getenv("TAIJI_RUNTIME_HOME")
    if runtime_home:
        return Path(runtime_home).expanduser().resolve() / "web" / "sessions"
    hermes_home = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
    return Path(hermes_home).expanduser().resolve() / "webui" / "sessions"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_expert_team_message(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get("expert_team_run_id"):
        return True
    if str(message.get("type") or "") in EXPERT_TEAM_MESSAGE_TYPES:
        return True
    metadata = message.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("expert_team_run_id"))


def _is_expert_team_session(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("expert_team_run_id"):
        return True
    title = str(data.get("title") or "")
    if title.startswith("召唤内容创作专家团") or title.startswith("召唤深度材料研究团"):
        return True
    messages = data.get("messages")
    return isinstance(messages, list) and any(_is_expert_team_message(message) for message in messages)


def _session_id_from_payload(path: Path, data: Any) -> str:
    if isinstance(data, dict):
        sid = str(data.get("session_id") or "").strip()
        if sid:
            return sid
    return path.stem


def cleanup_runs(workspaces: list[Path], apply: bool) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    total = 0
    for workspace in workspaces:
        root = workspace.expanduser().resolve() / ".taiji" / "expert-teams" / "runs"
        files = sorted(root.glob("*.json")) if root.exists() else []
        deleted: list[str] = []
        for path in files:
            deleted.append(str(path))
            if apply:
                path.unlink(missing_ok=True)
        total += len(deleted)
        reports.append({"workspace": str(workspace.expanduser().resolve()), "runs_dir": str(root), "matched": len(deleted), "files": deleted})
    return {"matched": total, "workspaces": reports}


def _rewrite_index(index_path: Path, removed_ids: set[str]) -> bool:
    data = _read_json(index_path)
    if not isinstance(data, list):
        return False
    filtered = []
    for item in data:
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        sid = str(item.get("session_id") or item.get("id") or "").strip()
        if sid not in removed_ids:
            filtered.append(item)
    if len(filtered) == len(data):
        return False
    index_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def cleanup_sessions(sessions_dir: Path, apply: bool) -> dict[str, Any]:
    sessions_dir = sessions_dir.expanduser().resolve()
    files = sorted(path for path in sessions_dir.glob("*.json") if path.name != "_index.json") if sessions_dir.exists() else []
    matched: list[dict[str, Any]] = []
    removed_ids: set[str] = set()
    for path in files:
        data = _read_json(path)
        if not _is_expert_team_session(data):
            continue
        sid = _session_id_from_payload(path, data)
        removed_ids.add(sid)
        matched.append({"session_id": sid, "file": str(path), "title": str(data.get("title") or "") if isinstance(data, dict) else ""})
        if apply:
            path.unlink(missing_ok=True)
    index_rewritten = False
    if apply and removed_ids:
        index_rewritten = _rewrite_index(sessions_dir / "_index.json", removed_ids)
    return {
        "sessions_dir": str(sessions_dir),
        "matched": len(matched),
        "index_rewritten": index_rewritten,
        "sessions": matched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", action="append", type=Path, default=[], help="Workspace whose .taiji expert-team runs should be removed. Repeatable.")
    parser.add_argument("--sessions-dir", type=Path, default=default_sessions_dir(), help="WebUI sidecar session directory.")
    parser.add_argument("--apply", action="store_true", help="Actually delete matched files. Omit for dry-run report.")
    args = parser.parse_args()

    report = {
        "mode": "apply" if args.apply else "dry-run",
        "runs": cleanup_runs(args.workspace, args.apply),
        "sessions": cleanup_sessions(args.sessions_dir, args.apply),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
