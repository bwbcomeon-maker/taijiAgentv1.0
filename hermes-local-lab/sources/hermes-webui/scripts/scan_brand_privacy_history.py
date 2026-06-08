#!/usr/bin/env python3
"""Report possible historical brand-privacy scrub pollution in WebUI sessions.

The previous scrubber could rewrite user-authored Hermes text to "taiji Agent".
This script is intentionally read-only because old user text cannot always be
reconstructed safely from the persisted transcript.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


_SAFE_REPLY_MARKER = "内部实现与部署细节不在普通对话中公开"
_TAIJI_TEXT_RE = re.compile(r"taiji\s*Agent|太极智能体", re.I)
_INTERNAL_LEAK_RE = re.compile(
    r"(?i)(Hermes\s+(?:Agent|WebUI)|hermes[-_ ](?:agent|webui|local-lab|state)|"
    r"run_agent\.py|hermes_state\.py|model_tools\.py|HERMES_HOME|HERMES_WEBUI_|"
    r"X-Hermes-CSRF-Token|~/.hermes|/[^ \n\r\t`'\"<>]*hermes[^ \n\r\t`'\"<>]*)"
)


def default_sessions_dir() -> Path:
    state_dir = os.getenv("HERMES_WEBUI_STATE_DIR")
    if state_dir:
        return Path(state_dir).expanduser().resolve() / "sessions"
    return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve() / "webui" / "sessions"


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or message.get("text") or "")


def scan_session(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"file": path.name, "error": "unreadable_json"}

    messages = data.get("messages")
    if not isinstance(messages, list):
        return None

    suspicious_user_turns: list[dict[str, Any]] = []
    internal_leak_turns: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        text = _message_text(message)
        if role == "user" and _TAIJI_TEXT_RE.search(text):
            next_text = _message_text(messages[index + 1]) if index + 1 < len(messages) else ""
            suspicious_user_turns.append(
                {
                    "index": index,
                    "preview": text[:120],
                    "followed_by_safe_reply": _SAFE_REPLY_MARKER in next_text,
                }
            )
        if role in {"assistant", "tool"} and _INTERNAL_LEAK_RE.search(text):
            internal_leak_turns.append({"index": index, "role": role, "preview": text[:160]})

    title = str(data.get("title") or "")
    suspicious_title = bool(_TAIJI_TEXT_RE.search(title))
    if not suspicious_user_turns and not internal_leak_turns and not suspicious_title:
        return None
    return {
        "file": path.name,
        "session_id": data.get("session_id") or path.stem,
        "title": title,
        "suspicious_title": suspicious_title,
        "suspicious_user_turns": suspicious_user_turns,
        "internal_leak_turns": internal_leak_turns,
    }


def scan_sessions(sessions_dir: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.glob("*.json")):
        if path.name == "_index.json":
            continue
        report = scan_session(path)
        if report:
            reports.append(report)
    return {
        "sessions_dir": str(sessions_dir),
        "scanned_files": len(list(sessions_dir.glob("*.json"))) if sessions_dir.exists() else 0,
        "affected_files": len(reports),
        "reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-dir", type=Path, default=default_sessions_dir(), help="WebUI sidecar session directory")
    args = parser.parse_args()

    sessions_dir = args.sessions_dir.expanduser().resolve()
    if not sessions_dir.exists():
        print(json.dumps({"sessions_dir": str(sessions_dir), "error": "sessions_dir_not_found"}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(scan_sessions(sessions_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
