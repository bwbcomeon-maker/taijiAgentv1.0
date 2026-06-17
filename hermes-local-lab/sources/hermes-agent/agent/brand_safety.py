"""Public-chat brand safety guard for tool access.

This guard is intentionally narrow: it only applies to ordinary public chat
contexts and blocks reads/searches/commands that inspect product internals,
runtime files, license material, ports, or process environment details.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator


_PUBLIC_CHAT_GUARD: ContextVar[bool] = ContextVar("taiji_public_chat_guard", default=False)

_SENSITIVE_PATH_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"/opt/taiji-agent(?:/|$)|"
    r"(?:^|/)\.local/state/taiji-agent/logs(?:/|$)|"
    r"(?:^|/)taiji-agent/logs(?:/|$)|"
    r"(?:taiji-desktop|agent|web)\.log|"
    r"(?:runtime-env|start-agent|start-webui|health-check|taiji-native-verify|taiji-agent-diagnose)\.sh|"
    r"(?:^|/)(?:config\.yaml|\.env|license\.jwt)(?:$|[ \n\r\t`'\"<>])|"
    r"hermes-local-lab|hermes-agent|hermes-webui|hermes-home|\.hermes(?:/|$)|"
    r"agent-runtime\.license|web-runtime\.license|claw\.pyc|"
    r"(?:runtime|site-packages|dist-info|licenses?)[^ \n\r\t`'\"<>]{0,80}"
    r"(?:agent-runtime|web-runtime|hermes|claw)"
    r")"
)

_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"Nous\s+Research|Hermes\s+Web\s+UI\s+Contributors|agent-runtime|web-runtime|"
    r"claw\.pyc|HERMES_HOME|HERMES_WEBUI_|X-Hermes-CSRF-Token|"
    r"TAIJI_WEBUI_PORT|WEBUI_PORT|AGENT_API_PORT|API_SERVER_PORT|"
    r"127\.0\.0\.1|localhost|listen|listening|端口|访问地址|服务地址|浏览器"
    r")"
)

_SENSITIVE_COMMAND_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:find|rg|grep|cat|head|tail|ls|tree|python|python3|sed|awk).{0,120}"
    r"(?:/opt/taiji-agent|taiji-agent/logs|hermes-local-lab|hermes-agent|hermes-webui|\.hermes|"
    r"license|dist-info|site-packages|runtime|config\.yaml|\.env|web\.log|agent\.log|taiji-desktop\.log)|"
    r"(?:lsof|netstat|ss\s+-|ps\s+(?:aux|ef|-ef)|printenv|env\s*$|/proc/|cmdline|environ)|"
    r"(?:curl|wget).{0,40}(?:localhost|127\.0\.0\.1|/health)|"
    r"(?:localhost|127\.0\.0\.1).{0,80}(?:port|端口|url|访问地址|health)|"
    r"(?:端口|监听|服务地址|访问地址|浏览器|地址栏).{0,80}(?:taiji|agent|web|服务|server|port|listen|health)"
    r")"
)

_PUBLIC_BLOCK_MESSAGE = (
    "该信息属于产品内部实现、部署或合规材料，不在普通对话中公开。"
)


@contextlib.contextmanager
def public_chat_guard(enabled: bool) -> Iterator[None]:
    token = _PUBLIC_CHAT_GUARD.set(bool(enabled))
    try:
        yield
    finally:
        _PUBLIC_CHAT_GUARD.reset(token)


def public_chat_guard_enabled() -> bool:
    env_value = os.getenv("TAIJI_PUBLIC_CHAT_GUARD") or os.getenv("HERMES_PUBLIC_CHAT_GUARD")
    return bool(_PUBLIC_CHAT_GUARD.get() or str(env_value or "").strip().lower() in {"1", "true", "yes", "on"})


def public_chat_block_json() -> str:
    return json.dumps(
        {
            "output": "",
            "exit_code": -1,
            "error": _PUBLIC_BLOCK_MESSAGE,
            "status": "blocked",
        },
        ensure_ascii=False,
    )


def block_reason_for_file_path(path: Any) -> str | None:
    if not public_chat_guard_enabled():
        return None
    value = _normalize_path_text(path)
    if not value:
        return None
    if _SENSITIVE_PATH_RE.search(value) or _SENSITIVE_TEXT_RE.search(value):
        return _PUBLIC_BLOCK_MESSAGE
    return None


def block_reason_for_search(pattern: Any, path: Any) -> str | None:
    if not public_chat_guard_enabled():
        return None
    combined = f"{pattern or ''}\n{_normalize_path_text(path)}"
    if _SENSITIVE_PATH_RE.search(combined) or _SENSITIVE_TEXT_RE.search(combined):
        return _PUBLIC_BLOCK_MESSAGE
    return None


def block_reason_for_terminal(command: Any, *, workdir: Any = None) -> str | None:
    if not public_chat_guard_enabled():
        return None
    command_text = str(command or "")
    combined = f"{command_text}\n{_normalize_path_text(workdir)}"
    if (
        _SENSITIVE_PATH_RE.search(combined)
        or _SENSITIVE_TEXT_RE.search(combined)
        or _SENSITIVE_COMMAND_RE.search(combined)
    ):
        return _PUBLIC_BLOCK_MESSAGE
    return None


def block_reason_for_tool(tool_name: str, args: dict[str, Any] | None) -> str | None:
    if not public_chat_guard_enabled():
        return None
    args = args or {}
    name = str(tool_name or "")
    if name == "read_file":
        return block_reason_for_file_path(args.get("path"))
    if name == "search_files":
        return block_reason_for_search(args.get("pattern"), args.get("path", "."))
    if name == "terminal":
        return block_reason_for_terminal(args.get("command"), workdir=args.get("workdir"))
    return None


def _normalize_path_text(path: Any) -> str:
    if path is None:
        return ""
    value = str(path)
    try:
        expanded = Path(value).expanduser()
        return f"{value}\n{expanded}"
    except Exception:
        return value
