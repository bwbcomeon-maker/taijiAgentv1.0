"""Projects codex app-server events into Hermes' messages list.

The translator that lets Hermes' memory/skill review keep working under the
Codex runtime: it converts Codex `item/*` notifications into the standard
OpenAI-shaped `{role, content, tool_calls, tool_call_id}` entries that
`agent/curator.py` already knows how to read.

Codex emits items with a discriminator field `type`:
  - userMessage         → {role: "user", content}
  - agentMessage        → {role: "assistant", content}
  - reasoning           → stashed in the assistant's "reasoning" field
  - commandExecution    → assistant tool_call(name="exec") + tool result
  - fileChange          → assistant tool_call(name="apply_patch") + tool result
  - mcpToolCall         → assistant tool_call(name=f"mcp.{server}.{tool}") + tool result
  - dynamicToolCall     → assistant tool_call(name=tool) + tool result
  - plan/hookPrompt/collabAgentToolCall → recorded as opaque assistant notes

Each item maps to AT MOST one assistant entry + one tool entry, preserving
Hermes' message-alternation invariants (system → user → assistant → user/tool
→ assistant → ...). Multiple Codex tool calls within one Codex turn produce
multiple consecutive (assistant, tool) pairs, which is the same shape Hermes
already produces for parallel tool calls.

Counters tracked alongside projection:
  - tool_iterations: ticks once per completed tool-shaped item. Used by
    AIAgent._iters_since_skill (skill nudge gate, default threshold 10).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional


def _deterministic_call_id(item_type: str, item_id: str) -> str:
    """Stable id for tool_call message correlation.

    Uses the codex item id directly when present (already a uuid); falls back
    to a content hash so replay produces the same id across sessions and
    prefix caches stay valid. See AGENTS.md Pitfall #16 (deterministic IDs in
    tool call history)."""
    if item_id:
        return f"codex_{item_type}_{item_id}"
    digest = hashlib.sha256(f"{item_type}".encode()).hexdigest()[:16]
    return f"codex_{item_type}_{digest}"


def _format_tool_args(d: dict) -> str:
    """Format a dict as JSON the way Hermes' existing tool_calls path does."""
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


@dataclass
class ProjectionResult:
    """Output of projecting one Codex item.

    `messages` is a list because some Codex items produce two messages
    (assistant tool_call + tool result). Empty list = item ignored (e.g. a
    streaming `outputDelta` that doesn't materialize into messages until the
    `item/completed` event)."""

    messages: list[dict] = field(default_factory=list)
    tool_completions: list["ProjectedToolCompletion"] = field(
        default_factory=list
    )
    is_tool_iteration: bool = False
    final_text: Optional[str] = None  # Set when an agentMessage completes


@dataclass(frozen=True)
class ProjectedToolCompletion:
    """Private lifecycle envelope for AIAgent's ordinary tool callbacks.

    ``function_name`` is canonical at the Hermes callback boundary.  The
    transcript message may remain namespaced (for example
    ``mcp.hermes-tools.image_generate``), but WebUI artifact ingestion must
    continue to accept only the exact, trusted ``image_generate`` name.
    """

    tool_call_id: str
    function_name: str
    function_args: dict[str, Any]
    function_result: Any
    is_error: bool = False


_FAILED_TOOL_STATUSES = frozenset({
    "cancelled",
    "canceled",
    "failed",
    "interrupted",
    "rejected",
})


def _completion_key(item: dict) -> str:
    """Return an immutable item key so replayed completion events dedupe."""
    item_type = str(item.get("type") or "")
    item_id = str(item.get("id") or "")
    if item_id:
        return f"{item_type}:{item_id}"
    try:
        encoded = json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        encoded = repr(item)
    digest = hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()
    return f"{item_type}:sha256:{digest}"


def _tool_status_is_error(item: dict) -> bool:
    return str(item.get("status") or "").strip().lower() in (
        _FAILED_TOOL_STATUSES
    )


def _failed_callback_result(item: dict) -> str:
    """Build a failure-only result that cannot smuggle a success artifact."""
    error = item.get("error")
    if isinstance(error, dict):
        detail = error.get("message") or error.get("code") or "tool failed"
    else:
        detail = error or item.get("status") or "tool failed"
    return json.dumps(
        {
            "success": False,
            "error": str(detail),
            "error_code": "codex_tool_failed",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _mcp_callback_result(result: Any) -> Any:
    """Unwrap Codex/FastMCP content into Hermes' ordinary tool result shape."""
    if not isinstance(result, dict):
        return result
    for key in ("structuredContent", "structured_content"):
        structured = result.get(key)
        if isinstance(structured, dict):
            return structured
    content = result.get("content")
    if isinstance(content, list):
        text_parts = [
            str(block.get("text"))
            for block in content
            if isinstance(block, dict) and block.get("text") is not None
        ]
        if len(text_parts) == 1:
            return text_parts[0]
        if text_parts:
            return "\n".join(text_parts)
    return result


def _mcp_callback_name(server: str, tool: str) -> str:
    """Canonicalize only the trusted Hermes MCP server's tool surface."""
    if server == "hermes-tools":
        return tool
    return f"mcp.{server}.{tool}"


class CodexEventProjector:
    """Stateful projector consuming Codex notifications in arrival order.

    Owns the in-progress reasoning content (codex emits reasoning as separate
    items but Hermes stashes it on the next assistant message)."""

    def __init__(self) -> None:
        self._pending_reasoning: list[str] = []
        self._completed_item_keys: set[str] = set()

    def project(self, notification: dict) -> ProjectionResult:
        """Project a single notification. Idempotent for non-completion events;
        only `item/completed` and `turn/completed` materialize messages."""
        method = notification.get("method", "")
        params = notification.get("params", {}) or {}

        # We only materialize messages on `item/completed`. Streaming deltas
        # (`item/<type>/outputDelta`, `item/<type>/delta`) are display-only and
        # don't enter the messages list — same way Hermes already only writes
        # the assistant message after the streaming completion event.
        if method != "item/completed":
            return ProjectionResult()

        item = params.get("item") or {}
        item_type = item.get("type") or ""
        item_id = item.get("id") or ""
        completion_key = _completion_key(item)
        if completion_key in self._completed_item_keys:
            return ProjectionResult()
        self._completed_item_keys.add(completion_key)

        if item_type == "agentMessage":
            return self._project_agent_message(item)
        if item_type == "reasoning":
            self._pending_reasoning.extend(item.get("summary") or [])
            self._pending_reasoning.extend(item.get("content") or [])
            return ProjectionResult()
        if item_type == "commandExecution":
            return self._project_command(item, item_id)
        if item_type == "fileChange":
            return self._project_file_change(item, item_id)
        if item_type == "mcpToolCall":
            return self._project_mcp_tool_call(item, item_id)
        if item_type == "dynamicToolCall":
            return self._project_dynamic_tool_call(item, item_id)
        if item_type == "userMessage":
            return self._project_user_message(item)

        # Unknown / rare items (plan, hookPrompt, collabAgentToolCall, etc.)
        # — record as opaque assistant note so memory review can still see
        # *something* happened, but don't fabricate tool_call structure.
        return self._project_opaque(item, item_type)

    # ---------- per-type projections ----------

    def _project_agent_message(self, item: dict) -> ProjectionResult:
        text = item.get("text") or ""
        msg: dict[str, Any] = {"role": "assistant", "content": text}
        if self._pending_reasoning:
            msg["reasoning"] = "\n".join(self._pending_reasoning)
            self._pending_reasoning = []
        return ProjectionResult(messages=[msg], final_text=text)

    def _project_user_message(self, item: dict) -> ProjectionResult:
        # codex's userMessage content is a list of UserInput variants. For
        # projection purposes we flatten any text fragments and ignore
        # non-text parts (images, etc.) — Hermes' messages store text only.
        text_parts: list[str] = []
        for fragment in item.get("content") or []:
            if isinstance(fragment, dict):
                if fragment.get("type") == "text":
                    text_parts.append(fragment.get("text") or "")
                elif "text" in fragment:
                    text_parts.append(str(fragment["text"]))
        return ProjectionResult(
            messages=[{"role": "user", "content": "\n".join(text_parts)}]
        )

    def _project_command(self, item: dict, item_id: str) -> ProjectionResult:
        call_id = _deterministic_call_id("exec", item_id)
        args = {
            "command": item.get("command") or "",
            "cwd": item.get("cwd") or "",
        }
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "exec_command",
                        "arguments": _format_tool_args(args),
                    },
                }
            ],
        }
        if self._pending_reasoning:
            assistant_msg["reasoning"] = "\n".join(self._pending_reasoning)
            self._pending_reasoning = []
        output = item.get("aggregatedOutput") or ""
        exit_code = item.get("exitCode")
        if exit_code is not None and exit_code != 0:
            output = f"[exit {exit_code}]\n{output}"
        tool_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        }
        is_error = bool(exit_code is not None and exit_code != 0)
        return ProjectionResult(
            messages=[assistant_msg, tool_msg],
            tool_completions=[
                ProjectedToolCompletion(
                    tool_call_id=call_id,
                    function_name="exec_command",
                    function_args=args,
                    function_result=output,
                    is_error=is_error,
                )
            ],
            is_tool_iteration=True,
        )

    def _project_file_change(self, item: dict, item_id: str) -> ProjectionResult:
        call_id = _deterministic_call_id("apply_patch", item_id)
        # Reduce the codex changes array to a digest the agent loop will
        # find readable. We record per-file change kinds (Add/Update/Delete)
        # without inlining full file contents — those can be huge.
        changes_summary = []
        for change in item.get("changes") or []:
            kind = (change.get("kind") or {}).get("type") or "update"
            path = change.get("path") or ""
            changes_summary.append({"kind": kind, "path": path})
        args = {"changes": changes_summary}
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "apply_patch",
                        "arguments": _format_tool_args(args),
                    },
                }
            ],
        }
        if self._pending_reasoning:
            assistant_msg["reasoning"] = "\n".join(self._pending_reasoning)
            self._pending_reasoning = []
        status = item.get("status") or "unknown"
        n = len(changes_summary)
        tool_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": f"apply_patch status={status}, {n} change(s)",
        }
        is_error = _tool_status_is_error(item)
        return ProjectionResult(
            messages=[assistant_msg, tool_msg],
            tool_completions=[
                ProjectedToolCompletion(
                    tool_call_id=call_id,
                    function_name="apply_patch",
                    function_args=args,
                    function_result=(
                        _failed_callback_result(item)
                        if is_error
                        else tool_msg["content"]
                    ),
                    is_error=is_error,
                )
            ],
            is_tool_iteration=True,
        )

    def _project_mcp_tool_call(self, item: dict, item_id: str) -> ProjectionResult:
        server = item.get("server") or "mcp"
        tool = item.get("tool") or "unknown"
        call_id = _deterministic_call_id(f"mcp_{server}_{tool}", item_id)
        args = item.get("arguments") or {}
        if not isinstance(args, dict):
            args = {"arguments": args}
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": f"mcp.{server}.{tool}",
                        "arguments": _format_tool_args(args),
                    },
                }
            ],
        }
        if self._pending_reasoning:
            assistant_msg["reasoning"] = "\n".join(self._pending_reasoning)
            self._pending_reasoning = []
        result = item.get("result")
        error = item.get("error")
        if error:
            content = f"[error] {json.dumps(error, ensure_ascii=False)[:1000]}"
        elif result is not None:
            content = json.dumps(result, ensure_ascii=False)[:4000]
        else:
            content = ""
        tool_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": content,
        }
        is_error = bool(error) or _tool_status_is_error(item)
        callback_result = (
            _failed_callback_result(item)
            if is_error
            else _mcp_callback_result(result)
        )
        return ProjectionResult(
            messages=[assistant_msg, tool_msg],
            tool_completions=[
                ProjectedToolCompletion(
                    tool_call_id=call_id,
                    function_name=_mcp_callback_name(server, tool),
                    function_args=args,
                    function_result=callback_result,
                    is_error=is_error,
                )
            ],
            is_tool_iteration=True,
        )

    def _project_dynamic_tool_call(
        self, item: dict, item_id: str
    ) -> ProjectionResult:
        tool = item.get("tool") or "unknown"
        call_id = _deterministic_call_id(f"dyn_{tool}", item_id)
        args = item.get("arguments") or {}
        if not isinstance(args, dict):
            args = {"arguments": args}
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool,
                        "arguments": _format_tool_args(args),
                    },
                }
            ],
        }
        if self._pending_reasoning:
            assistant_msg["reasoning"] = "\n".join(self._pending_reasoning)
            self._pending_reasoning = []
        content_items = item.get("contentItems") or []
        if isinstance(content_items, list) and content_items:
            content = json.dumps(content_items, ensure_ascii=False)[:4000]
        else:
            success = item.get("success")
            content = f"success={success}"
        tool_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": content,
        }
        is_error = item.get("success") is False or _tool_status_is_error(item)
        return ProjectionResult(
            messages=[assistant_msg, tool_msg],
            tool_completions=[
                ProjectedToolCompletion(
                    tool_call_id=call_id,
                    function_name=tool,
                    function_args=args,
                    function_result=(
                        _failed_callback_result(item)
                        if is_error
                        else content
                    ),
                    is_error=is_error,
                )
            ],
            is_tool_iteration=True,
        )

    def _project_opaque(self, item: dict, item_type: str) -> ProjectionResult:
        # Record the existence of the item without inventing tool_calls.
        # Memory review will see this and may or may not save anything.
        try:
            payload = json.dumps(item, ensure_ascii=False)[:1500]
        except (TypeError, ValueError):
            payload = repr(item)[:1500]
        return ProjectionResult(
            messages=[
                {
                    "role": "assistant",
                    "content": f"[codex {item_type}] {payload}",
                }
            ]
        )
