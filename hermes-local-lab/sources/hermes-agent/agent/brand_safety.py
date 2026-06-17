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
    raw_name = str(tool_name or "")
    name = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")

    path_value = _first_arg(args, "path", "file", "file_path", "filepath", "directory", "dir", "root")
    workdir_value = _first_arg(args, "workdir", "cwd", "working_directory")
    command_value = _first_arg(args, "command", "cmd", "shell", "script", "code")
    query_value = _first_arg(args, "pattern", "query", "search", "text", "needle")

    if name in {"read_file", "read_file_tool"} or ("read" in name and ("file" in name or "filesystem" in name)):
        return block_reason_for_file_path(path_value)
    if (
        name in {"list_directory", "list_files", "directory_list", "tree"}
        or ("list" in name and ("dir" in name or "file" in name or "workspace" in name))
    ):
        return block_reason_for_file_path(path_value)
    if (
        name in {"search_files", "search", "grep", "find"}
        or "search" in name
        or "grep" in name
        or name.startswith("find_")
    ):
        return block_reason_for_search(query_value, path_value or workdir_value or ".")
    if (
        name in {"terminal", "execute_command", "run_command", "shell", "bash", "execute_code"}
        or "command" in name
        or "terminal" in name
        or "shell" in name
        or "execute" in name
    ):
        return block_reason_for_terminal(command_value, workdir=workdir_value)

    combined = _args_to_text(args)
    if _SENSITIVE_PATH_RE.search(combined) or _SENSITIVE_TEXT_RE.search(combined):
        return _PUBLIC_BLOCK_MESSAGE
    return None


def _first_arg(args: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in args and args.get(key) not in (None, ""):
            return args.get(key)
    return None


def _args_to_text(args: dict[str, Any]) -> str:
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(args)


def _normalize_path_text(path: Any) -> str:
    if path is None:
        return ""
    value = str(path)
    try:
        expanded = Path(value).expanduser()
        return f"{value}\n{expanded}"
    except Exception:
        return value
