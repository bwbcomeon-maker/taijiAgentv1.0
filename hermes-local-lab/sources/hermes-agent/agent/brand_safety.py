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

_EXPLICIT_INTERNAL_TARGET_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"/opt/taiji-agent(?:/|$|[ \n\r\t`'\"<>])|"
    r"(?<![A-Za-z0-9])taiji[-_ ]agent(?:\.service)?(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])com\.taiji\.(?:agent|desktop|webui)(?![A-Za-z0-9])|"
    r"太极(?:智能体|\s*Agent)(?:的)?(?:内部|运行时|本机|服务|进程|配置|日志|许可证|版权)|"
    r"(?:^|/)\.local/state/taiji-agent/logs(?:/|$)|"
    r"(?:^|/)taiji-agent/logs(?:/|$)|"
    r"taiji-desktop\.log|"
    r"(?:runtime-env|start-agent|start-webui|health-check|taiji-native-verify|taiji-agent-diagnose)\.sh|"
    r"hermes-local-lab|hermes-agent|hermes-webui|hermes-home|\.hermes(?:/|$)|"
    r"agent-runtime\.license|web-runtime\.license|claw\.pyc|"
    r"HERMES_HOME|HERMES_WEBUI_|X-Hermes-CSRF-Token|"
    r"TAIJI_WEBUI_PORT|WEBUI_PORT|AGENT_API_PORT|API_SERVER_PORT|"
    r"(?:localhost|127\.0\.0\.1).{0,80}/taiji(?:/|$)|"
    r"(?:runtime|site-packages|dist-info|licenses?)[^ \n\r\t`'\"<>]{0,80}"
    r"(?:agent-runtime|web-runtime|hermes|claw)"
    r")"
)

_SYSTEM_PROBE_RE = re.compile(
    r"(?is)(?:\b(?:lsof|netstat|ss|ps|printenv|env|curl|wget)\b|/proc/|cmdline|environ)"
)

_TAIJI_REFERENCE_RE = re.compile(r"(?i)(?:\btaiji(?:-agent|\s+agent)?\b|太极智能体)")

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
    if _EXPLICIT_INTERNAL_TARGET_RE.search(value):
        return _PUBLIC_BLOCK_MESSAGE
    return None


def block_reason_for_search(pattern: Any, path: Any) -> str | None:
    if not public_chat_guard_enabled():
        return None
    combined = f"{pattern or ''}\n{_normalize_path_text(path)}"
    if _EXPLICIT_INTERNAL_TARGET_RE.search(combined):
        return _PUBLIC_BLOCK_MESSAGE
    return None


def block_reason_for_terminal(command: Any, *, workdir: Any = None) -> str | None:
    if not public_chat_guard_enabled():
        return None
    command_text = str(command or "")
    combined = f"{command_text}\n{_normalize_path_text(workdir)}"
    # A terminal invocation already establishes operational intent. Once the
    # target is an explicit taiji/Hermes runtime identifier, fail closed without
    # maintaining a brittle allowlist of process/service commands. Keep the
    # legacy narrow process-probe rule only for ambiguous bare "taiji" wording.
    explicit_target = bool(_EXPLICIT_INTERNAL_TARGET_RE.search(combined))
    ambiguous_bare_taiji_probe = bool(
        _TAIJI_REFERENCE_RE.search(combined)
        and _SYSTEM_PROBE_RE.search(combined)
    )
    if explicit_target or ambiguous_bare_taiji_probe:
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
    has_access_shape = any(
        value not in (None, "")
        for value in (path_value, workdir_value, command_value, query_value)
    ) or bool(re.search(r"(?:read|list|search|find|open|navigate|browse|call)", name))
    if has_access_shape and _EXPLICIT_INTERNAL_TARGET_RE.search(combined):
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
