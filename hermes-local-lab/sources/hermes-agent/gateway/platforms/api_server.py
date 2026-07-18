"""
OpenAI-compatible API server platform adapter.

Exposes an HTTP server with endpoints:
- POST /v1/chat/completions        — OpenAI Chat Completions format (stateless; opt-in session continuity via X-Hermes-Session-Id header; opt-in long-term memory scoping via X-Hermes-Session-Key header)
- POST /v1/responses               — OpenAI Responses API format (stateful via previous_response_id; X-Hermes-Session-Key supported)
- GET  /v1/responses/{response_id} — Retrieve a stored response
- DELETE /v1/responses/{response_id} — Delete a stored response
- GET  /v1/models                  — lists hermes-agent as an available model
- GET  /v1/capabilities            — machine-readable API capabilities for external UIs
- GET  /api/sessions               — list client-visible Hermes sessions
- POST /api/sessions               — create an empty Hermes session
- GET/PATCH/DELETE /api/sessions/{session_id} — read/update/delete a session
- GET  /api/sessions/{session_id}/messages — read session message history
- POST /api/sessions/{session_id}/fork — branch a session using SessionDB lineage
- POST /api/sessions/{session_id}/chat[/stream] — chat with a persisted session
- POST /v1/runs                    — start a run, returns run_id immediately (202)
- GET  /v1/runs/{run_id}           — retrieve current run status
- GET  /v1/runs/{run_id}/events    — SSE stream of structured lifecycle events
- POST /v1/runs/{run_id}/approval — resolve a pending run approval
- POST /v1/runs/{run_id}/stop       — interrupt a running agent
- GET  /health                     — health check
- GET  /health/detailed            — rich status for cross-container dashboard probing

Any OpenAI-compatible frontend (Open WebUI, LobeChat, LibreChat,
AnythingLLM, NextChat, ChatBox, etc.) can connect to hermes-agent
through this adapter by pointing at http://localhost:8642/v1 and
authenticating with API_SERVER_KEY.

Requires:
- aiohttp (already available in the gateway)

Managed ``/v1/runs`` requests (those carrying a session ID) are serialized
through a renewable SQLite lease in ``state.db``.  The lease is shared across
adapter instances and processes, and message inserts are transactionally
fenced by the exact owner/run pair.  Lease database I/O is kept off the aiohttp
event loop; errors fail closed instead of falling back to process-local admission.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import socket as _socket
import re
import sqlite3
import threading
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    is_network_accessible,
)
import taiji_license

logger = logging.getLogger(__name__)

# Default settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642
MAX_STORED_RESPONSES = 100
MAX_REQUEST_BYTES = 10_000_000  # 10 MB — accommodates long agent conversations with tool calls
CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS = 30.0
MAX_NORMALIZED_TEXT_LENGTH = 65_536  # 64 KB cap for normalized content parts
MAX_CONTENT_LIST_SIZE = 1_000  # Max items when content is an array
PUBLIC_SESSION_STREAM_ERROR_MESSAGE = "太极 Agent 处理本次会话时出现错误，请稍后重试或导出诊断报告。"


def _structured_tool_result_for_gateway(tool_name: str, result: Any) -> Dict[str, Any] | None:
    """Allowlist the one tool result the WebUI artifact bridge consumes.

    Tool arguments, prompts, provider diagnostics and arbitrary results remain
    private.  The image location is an internal candidate consumed by the
    WebUI before its public SSE/journal projection.
    """
    if str(tool_name or "") != "image_generate":
        return None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return None
    elif isinstance(result, dict):
        parsed = result
    else:
        return None
    if parsed.get("success") is not True:
        return None
    from agent.image_gen_provider import validated_cache_image_ref

    verified_ref = validated_cache_image_ref(parsed.get("image"))
    if verified_ref is None:
        return None
    image_ref, digest = verified_ref
    return {"success": True, "image_ref": image_ref, "sha256": digest}


def _coerce_port(value: Any, default: int = DEFAULT_PORT) -> int:
    """Parse a listen port without letting malformed env/config values crash startup."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_TRUE_REQUEST_BOOL_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_REQUEST_BOOL_STRINGS = frozenset({"0", "false", "no", "off"})


def _coerce_request_bool(value: Any, default: bool = False) -> bool:
    """Normalize boolean-like API payload values.

    External clients should send real JSON booleans, but some OpenAI-compatible
    frontends and middleware serialize flags like ``stream`` as strings.  Using
    Python truthiness on those values misroutes requests because ``"false"`` is
    still truthy.  Treat only explicit bool-ish scalars as booleans; everything
    else falls back to the caller's default.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_REQUEST_BOOL_STRINGS:
            return True
        if normalized in _FALSE_REQUEST_BOOL_STRINGS:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _session_stream_error_payload() -> Dict[str, str]:
    return {
        "message": PUBLIC_SESSION_STREAM_ERROR_MESSAGE,
        "code": "session_chat_stream_failed",
        "finish_reason": "error",
    }


def _normalize_chat_content(
    content: Any, *, _max_depth: int = 10, _depth: int = 0,
) -> str:
    """Normalize OpenAI chat message content into a plain text string.

    Some clients (Open WebUI, LobeChat, etc.) send content as an array of
    typed parts instead of a plain string::

        [{"type": "text", "text": "hello"}, {"type": "input_text", "text": "..."}]

    This function flattens those into a single string so the agent pipeline
    (which expects strings) doesn't choke.

    Defensive limits prevent abuse: recursion depth, list size, and output
    length are all bounded.
    """
    if _depth > _max_depth:
        return ""
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:MAX_NORMALIZED_TEXT_LENGTH] if len(content) > MAX_NORMALIZED_TEXT_LENGTH else content

    if isinstance(content, list):
        parts: List[str] = []
        items = content[:MAX_CONTENT_LIST_SIZE] if len(content) > MAX_CONTENT_LIST_SIZE else content
        for item in items:
            if isinstance(item, str):
                if item:
                    parts.append(item[:MAX_NORMALIZED_TEXT_LENGTH])
            elif isinstance(item, dict):
                item_type = str(item.get("type") or "").strip().lower()
                if item_type in {"text", "input_text", "output_text"}:
                    text = item.get("text", "")
                    if text:
                        try:
                            parts.append(str(text)[:MAX_NORMALIZED_TEXT_LENGTH])
                        except Exception:
                            pass
                # Silently skip image_url / other non-text parts
            elif isinstance(item, list):
                nested = _normalize_chat_content(item, _max_depth=_max_depth, _depth=_depth + 1)
                if nested:
                    parts.append(nested)
            # Check accumulated size
            if sum(len(p) for p in parts) >= MAX_NORMALIZED_TEXT_LENGTH:
                break
        result = "\n".join(parts)
        return result[:MAX_NORMALIZED_TEXT_LENGTH] if len(result) > MAX_NORMALIZED_TEXT_LENGTH else result

    # Fallback for unexpected types (int, float, bool, etc.)
    try:
        result = str(content)
        return result[:MAX_NORMALIZED_TEXT_LENGTH] if len(result) > MAX_NORMALIZED_TEXT_LENGTH else result
    except Exception:
        return ""


# Content part type aliases used by the OpenAI Chat Completions and Responses
# APIs.  We accept both spellings on input and emit a single canonical internal
# shape (``{"type": "text", ...}`` / ``{"type": "image_url", ...}``) that the
# rest of the agent pipeline already understands.
_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_IMAGE_PART_TYPES = frozenset({"image_url", "input_image"})
_FILE_PART_TYPES = frozenset({"file", "input_file"})


def _normalize_multimodal_content(content: Any) -> Any:
    """Validate and normalize multimodal content for the API server.

    Returns a plain string when the content is text-only, or a list of
    ``{"type": "text"|"image_url", ...}`` parts when images are present.
    The output shape is the native OpenAI Chat Completions vision format,
    which the agent pipeline accepts verbatim (OpenAI-wire providers) or
    converts (``_preprocess_anthropic_content`` for Anthropic).

    Raises ``ValueError`` with an OpenAI-style code on invalid input:
      * ``unsupported_content_type`` — file/input_file/file_id parts, or
        non-image ``data:`` URLs.
      * ``invalid_image_url`` — missing URL or unsupported scheme.
      * ``invalid_content_part`` — malformed text/image objects.

    Callers translate the ValueError into a 400 response.
    """
    # Scalar passthrough mirrors ``_normalize_chat_content``.
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:MAX_NORMALIZED_TEXT_LENGTH] if len(content) > MAX_NORMALIZED_TEXT_LENGTH else content
    if not isinstance(content, list):
        # Mirror the legacy text-normalizer's fallback so callers that
        # pre-existed image support still get a string back.
        return _normalize_chat_content(content)

    items = content[:MAX_CONTENT_LIST_SIZE] if len(content) > MAX_CONTENT_LIST_SIZE else content
    normalized_parts: List[Dict[str, Any]] = []
    text_accum_len = 0

    for part in items:
        if isinstance(part, str):
            if part:
                trimmed = part[:MAX_NORMALIZED_TEXT_LENGTH]
                normalized_parts.append({"type": "text", "text": trimmed})
                text_accum_len += len(trimmed)
            continue

        if not isinstance(part, dict):
            # Ignore unknown scalars for forward compatibility with future
            # Responses API additions (e.g. ``refusal``).  The same policy
            # the text normalizer applies.
            continue

        raw_type = part.get("type")
        part_type = str(raw_type or "").strip().lower()

        if part_type in _TEXT_PART_TYPES:
            text = part.get("text")
            if text is None:
                continue
            if not isinstance(text, str):
                text = str(text)
            if text:
                trimmed = text[:MAX_NORMALIZED_TEXT_LENGTH]
                normalized_parts.append({"type": "text", "text": trimmed})
                text_accum_len += len(trimmed)
            continue

        if part_type in _IMAGE_PART_TYPES:
            detail = part.get("detail")
            image_ref = part.get("image_url")
            # OpenAI Responses sends ``input_image`` with a top-level
            # ``image_url`` string; Chat Completions sends ``image_url`` as
            # ``{"url": "...", "detail": "..."}``.  Support both.
            if isinstance(image_ref, dict):
                url_value = image_ref.get("url")
                detail = image_ref.get("detail", detail)
            else:
                url_value = image_ref
            if not isinstance(url_value, str) or not url_value.strip():
                raise ValueError("invalid_image_url:Image parts must include a non-empty image URL.")
            url_value = url_value.strip()
            lowered = url_value.lower()
            if lowered.startswith("data:"):
                if not lowered.startswith("data:image/") or "," not in url_value:
                    raise ValueError(
                        "unsupported_content_type:Only image data URLs are supported. "
                        "Non-image data payloads are not supported."
                    )
            elif not (lowered.startswith("http://") or lowered.startswith("https://")):
                raise ValueError(
                    "invalid_image_url:Image inputs must use http(s) URLs or data:image/... URLs."
                )
            image_part: Dict[str, Any] = {"type": "image_url", "image_url": {"url": url_value}}
            if detail is not None:
                if not isinstance(detail, str) or not detail.strip():
                    raise ValueError("invalid_content_part:Image detail must be a non-empty string when provided.")
                image_part["image_url"]["detail"] = detail.strip()
            normalized_parts.append(image_part)
            continue

        if part_type in _FILE_PART_TYPES:
            raise ValueError(
                "unsupported_content_type:Inline image inputs are supported, "
                "but uploaded files and document inputs are not supported on this endpoint."
            )

        # Unknown part type — reject explicitly so clients get a clear error
        # instead of a silently dropped turn.
        raise ValueError(
            f"unsupported_content_type:Unsupported content part type {raw_type!r}. "
            "Only text and image_url/input_image parts are supported."
        )

    if not normalized_parts:
        return ""

    # Text-only: collapse to a plain string so downstream logging/trajectory
    # code sees the native shape and prompt caching on text-only turns is
    # unaffected.
    if all(p.get("type") == "text" for p in normalized_parts):
        return "\n".join(p["text"] for p in normalized_parts if p.get("text"))

    return normalized_parts


def _normalized_conversation_message(entry: Dict[str, Any], content: Any) -> Dict[str, Any]:
    """Keep provider-relevant tool correlation fields on normalized history."""
    message = {"role": str(entry.get("role") or ""), "content": content}
    for key in ("tool_calls", "tool_call_id", "name", "tool_name"):
        if key in entry:
            message[key] = entry[key]
    return message


def _normalized_platform_message_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 256 or re.search(r"[\r\n\x00]", value):
        return None
    return value


def _parse_request_platform_message_id(
    body: Dict[str, Any],
) -> tuple[Optional[str], Optional["web.Response"]]:
    """Parse an optional request ID without silently disabling turn identity."""
    if "platform_message_id" not in body:
        return None, None

    platform_message_id = _normalized_platform_message_id(
        body.get("platform_message_id")
    )
    if platform_message_id is None:
        return None, web.json_response(
            _openai_error(
                "platform_message_id must be a non-empty string of at most "
                "256 characters without CR, LF, or NUL",
                code="invalid_platform_message_id",
                param="platform_message_id",
            ),
            status=400,
        )
    return platform_message_id, None


def _validated_run_route_value(body: Dict[str, Any], field: str) -> Optional[str]:
    """Return one explicit run route selector, or ``None`` for the default."""
    if field not in body:
        return None
    raw = body.get(field)
    if not isinstance(raw, str):
        raise ValueError(field)
    value = raw.strip()
    if (
        not value
        or len(value) > 512
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        raise ValueError(field)
    if value.lower() == "default":
        return None
    return value.lower() if field == "provider" else value


def _normalized_explicit_run_route(
    body: Dict[str, Any],
    requested_model: Optional[str],
    requested_provider: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Validate and normalize an optional explicit managed-run route.

    A route is explicit as soon as either selector field is present.  Such a
    route must name both a real model and provider; the ``default`` sentinel
    is only meaningful when both fields are omitted.  Provider-qualified
    model selectors are unwrapped only when their qualifier matches the
    selected provider, including named custom providers whose ids contain
    colons.
    """
    has_model = "model" in body
    has_provider = "provider" in body
    if not has_model and not has_provider:
        return None, None
    if (
        not has_model
        or not has_provider
        or requested_model is None
        or requested_provider is None
    ):
        raise ValueError("incomplete_model_route")

    from hermes_cli.model_normalize import normalize_model_for_provider
    from hermes_cli.models import normalize_provider

    raw_provider = requested_provider
    normalized_provider = normalize_provider(raw_provider)
    model = requested_model
    if model.startswith("@"):
        matching_prefix = None
        prefixes = {
            f"@{raw_provider}:",
            f"@{normalized_provider}:",
        }
        for prefix in sorted(prefixes, key=len, reverse=True):
            if model.lower().startswith(prefix.lower()):
                matching_prefix = prefix
                break
        if matching_prefix is None:
            raise ValueError("invalid_model_selector")
        model = model[len(matching_prefix) :].strip()
        if not model:
            raise ValueError("invalid_model_selector")

    normalized_model = normalize_model_for_provider(model, normalized_provider)
    if not normalized_model:
        raise ValueError("invalid_model_selector")
    return normalized_model, normalized_provider


def _content_has_visible_payload(content: Any) -> bool:
    """True when content has any text or image attachment.  Used to reject empty turns."""
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                ptype = str(part.get("type") or "").strip().lower()
                if ptype in _TEXT_PART_TYPES and str(part.get("text") or "").strip():
                    return True
                if ptype in _IMAGE_PART_TYPES:
                    return True
    return False


def _multimodal_validation_error(exc: ValueError, *, param: str) -> "web.Response":
    """Translate a ``_normalize_multimodal_content`` ValueError into a 400 response."""
    raw = str(exc)
    code, _, message = raw.partition(":")
    if not message:
        code, message = "invalid_content_part", raw
    return web.json_response(
        _openai_error(message, code=code, param=param),
        status=400,
    )


def _session_chat_user_message(body: Dict[str, Any], *, param: str = "message") -> tuple[Any, Optional["web.Response"]]:
    """Parse and normalize session chat ``message`` / ``input`` like chat completions."""
    user_message = body.get("message") or body.get("input")
    if not _content_has_visible_payload(user_message):
        return None, web.json_response(
            _openai_error("Missing 'message' field", code="missing_message"),
            status=400,
        )
    try:
        return _normalize_multimodal_content(user_message), None
    except ValueError as exc:
        return None, _multimodal_validation_error(exc, param=param)


def check_api_server_requirements() -> bool:
    """Check if API server dependencies are available."""
    return AIOHTTP_AVAILABLE


class ResponseStore:
    """
    SQLite-backed LRU store for Responses API state.

    Each stored response includes the full internal conversation history
    (with tool calls and results) so it can be reconstructed on subsequent
    requests via previous_response_id.

    Persists across gateway restarts.  Falls back to in-memory SQLite
    if the on-disk path is unavailable.
    """

    def __init__(self, max_size: int = MAX_STORED_RESPONSES, db_path: str = None):
        self._max_size = max_size
        if db_path is None:
            try:
                from hermes_cli.config import get_hermes_home
                db_path = str(get_hermes_home() / "response_store.db")
            except Exception:
                db_path = ":memory:"
        self._db_path: Optional[str] = db_path if db_path != ":memory:" else None
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        except Exception:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._db_path = None
        # Use shared WAL-fallback helper so response_store.db degrades
        # gracefully on NFS/SMB/FUSE-mounted HERMES_HOME (same filesystem
        # issue addressed for state.db/kanban.db — see
        # hermes_state._WAL_INCOMPAT_MARKERS).
        from hermes_state import apply_wal_with_fallback
        apply_wal_with_fallback(self._conn, db_label="response_store.db")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS responses (
                response_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                accessed_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS conversations (
                name TEXT PRIMARY KEY,
                response_id TEXT NOT NULL
            )"""
        )
        self._conn.commit()
        # response_store.db contains conversation history (tool payloads,
        # prompts, results). Tighten to owner-only after creation so other
        # local users on a shared box can't read it. Run once at __init__
        # rather than after every commit — chmod-on-every-write is wasted
        # syscalls on a hot path.
        self._tighten_file_permissions()

    def _tighten_file_permissions(self) -> None:
        """Force owner-only permissions on the DB and SQLite sidecars."""
        if not self._db_path:
            return
        for candidate in (
            Path(self._db_path),
            Path(f"{self._db_path}-wal"),
            Path(f"{self._db_path}-shm"),
        ):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                logger.debug(
                    "Failed to restrict response store permissions for %s",
                    candidate,
                    exc_info=True,
                )

    def get(self, response_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a stored response by ID (updates access time for LRU)."""
        row = self._conn.execute(
            "SELECT data FROM responses WHERE response_id = ?", (response_id,)
        ).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE responses SET accessed_at = ? WHERE response_id = ?",
            (time.time(), response_id),
        )
        self._conn.commit()
        return json.loads(row[0])

    def put(self, response_id: str, data: Dict[str, Any]) -> None:
        """Store a response, evicting the oldest if at capacity."""
        self._conn.execute(
            "INSERT OR REPLACE INTO responses (response_id, data, accessed_at) VALUES (?, ?, ?)",
            (response_id, json.dumps(data, default=str), time.time()),
        )
        # Evict oldest entries beyond max_size
        count = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        if count > self._max_size:
            # Collect IDs that will be evicted
            evict_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT response_id FROM responses ORDER BY accessed_at ASC LIMIT ?",
                    (count - self._max_size,),
                ).fetchall()
            ]
            if evict_ids:
                placeholders = ",".join("?" for _ in evict_ids)
                # Clear conversation mappings pointing to evicted responses
                self._conn.execute(
                    f"DELETE FROM conversations WHERE response_id IN ({placeholders})",
                    evict_ids,
                )
                # Delete evicted responses
                self._conn.execute(
                    f"DELETE FROM responses WHERE response_id IN ({placeholders})",
                    evict_ids,
                )
        self._conn.commit()

    def delete(self, response_id: str) -> bool:
        """Remove a response from the store. Returns True if found and deleted."""
        # Clear conversation mappings pointing to this response
        self._conn.execute(
            "DELETE FROM conversations WHERE response_id = ?", (response_id,)
        )
        cursor = self._conn.execute(
            "DELETE FROM responses WHERE response_id = ?", (response_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_conversation(self, name: str) -> Optional[str]:
        """Get the latest response_id for a conversation name."""
        row = self._conn.execute(
            "SELECT response_id FROM conversations WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row else None

    def set_conversation(self, name: str, response_id: str) -> None:
        """Map a conversation name to its latest response_id."""
        self._conn.execute(
            "INSERT OR REPLACE INTO conversations (name, response_id) VALUES (?, ?)",
            (name, response_id),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key",
}


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def cors_middleware(request, handler):
        """Add CORS headers for explicitly allowed origins; handle OPTIONS preflight."""
        adapter = request.app.get("api_server_adapter")
        origin = request.headers.get("Origin", "")
        cors_headers = None
        if adapter is not None:
            if not adapter._origin_allowed(origin):
                return web.Response(status=403)
            cors_headers = adapter._cors_headers_for_origin(origin)

        if request.method == "OPTIONS":
            if cors_headers is None:
                return web.Response(status=403)
            return web.Response(status=200, headers=cors_headers)

        response = await handler(request)
        if cors_headers is not None:
            response.headers.update(cors_headers)
        return response
else:
    cors_middleware = None  # type: ignore[assignment]


def _openai_error(message: str, err_type: str = "invalid_request_error", param: str = None, code: str = None) -> Dict[str, Any]:
    """OpenAI-style error envelope."""
    return {
        "error": {
            "message": message,
            "type": err_type,
            "param": param,
            "code": code,
        }
    }


def _classify_agent_stream_error(raw_message: str) -> str:
    lowered = str(raw_message or "").lower()
    if "api key" in lowered and (
        "no api key" in lowered
        or "not found" in lowered
        or "missing" in lowered
        or "was found" in lowered
    ):
        return "model_configuration_error"
    if "provider" in lowered and "config" in lowered:
        return "model_configuration_error"
    return "agent_error"


def _public_agent_stream_error(raw_message: str) -> Dict[str, str]:
    code = _classify_agent_stream_error(raw_message)
    if code == "model_configuration_error":
        message = "模型服务未配置或不可用。请在配置页补充模型 API Key，或切换到可用模型。"
    else:
        message = "本地对话服务暂时不可用。请稍后重试，或导出诊断报告。"
    return {
        "message": message,
        "type": "server_error",
        "code": code,
    }


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def body_limit_middleware(request, handler):
        """Reject overly large request bodies early based on Content-Length."""
        if request.method in {"POST", "PUT", "PATCH"}:
            cl = request.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) > MAX_REQUEST_BYTES:
                        return web.json_response(_openai_error("Request body too large.", code="body_too_large"), status=413)
                except ValueError:
                    return web.json_response(_openai_error("Invalid Content-Length header.", code="invalid_content_length"), status=400)
        return await handler(request)
else:
    body_limit_middleware = None  # type: ignore[assignment]

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    "Referrer-Policy": "no-referrer",
}


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def security_headers_middleware(request, handler):
        """Add security headers to all responses (including errors)."""
        response = await handler(request)
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response
else:
    security_headers_middleware = None  # type: ignore[assignment]


class _IdempotencyCache:
    """In-memory idempotency cache with TTL and basic LRU semantics."""
    def __init__(self, max_items: int = 1000, ttl_seconds: int = 300):
        from collections import OrderedDict
        self._store = OrderedDict()
        self._inflight: Dict[tuple[str, str], "asyncio.Task[Any]"] = {}
        self._ttl = ttl_seconds
        self._max = max_items

    def _purge(self):
        now = time.time()
        expired = [k for k, v in self._store.items() if now - v["ts"] > self._ttl]
        for k in expired:
            self._store.pop(k, None)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    async def get_or_set(self, key: str, fingerprint: str, compute_coro):
        self._purge()
        item = self._store.get(key)
        if item and item["fp"] == fingerprint:
            return item["resp"]

        inflight_key = (key, fingerprint)
        task = self._inflight.get(inflight_key)
        if task is None:
            async def _compute_and_store():
                resp = await compute_coro()
                import time as _t
                self._store[key] = {"resp": resp, "fp": fingerprint, "ts": _t.time()}
                self._purge()
                return resp

            task = asyncio.create_task(_compute_and_store())
            self._inflight[inflight_key] = task

            def _clear_inflight(done_task: "asyncio.Task[Any]") -> None:
                if self._inflight.get(inflight_key) is done_task:
                    self._inflight.pop(inflight_key, None)

            task.add_done_callback(_clear_inflight)

        return await asyncio.shield(task)


_idem_cache = _IdempotencyCache()


def _make_request_fingerprint(body: Dict[str, Any], keys: List[str]) -> str:
    from hashlib import sha256
    subset = {k: body.get(k) for k in keys}
    return sha256(repr(subset).encode("utf-8")).hexdigest()


def _derive_chat_session_id(
    system_prompt: Optional[str],
    first_user_message: str,
) -> str:
    """Derive a stable session ID from the conversation's first user message.

    OpenAI-compatible frontends (Open WebUI, LibreChat, etc.) send the full
    conversation history with every request.  The system prompt and first user
    message are constant across all turns of the same conversation, so hashing
    them produces a deterministic session ID that lets the API server reuse
    the same Hermes session (and therefore the same Docker container sandbox
    directory) across turns.
    """
    seed = f"{system_prompt or ''}\n{first_user_message}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"api-{digest}"


_CRON_AVAILABLE = False
try:
    from cron.jobs import (
        list_jobs as _cron_list,
        get_job as _cron_get,
        create_job as _cron_create,
        update_job as _cron_update,
        remove_job as _cron_remove,
        pause_job as _cron_pause,
        resume_job as _cron_resume,
        trigger_job as _cron_trigger,
    )
    _CRON_AVAILABLE = True
except ImportError:
    _cron_list = None
    _cron_get = None
    _cron_create = None
    _cron_update = None
    _cron_remove = None
    _cron_pause = None
    _cron_resume = None
    _cron_trigger = None


class _ManagedLeaseLifecycle:
    """Event-loop-owned lease placeholder spanning acquire through release."""

    __slots__ = (
        "acquire_task",
        "admission_token",
        "cleanup_started",
        "completed",
        "done",
        "epoch",
        "shutdown_requested",
        "supervisor_task",
    )

    def __init__(
        self,
        loop: "asyncio.AbstractEventLoop",
        *,
        admission_token: str,
        epoch: int,
    ) -> None:
        self.acquire_task: Optional["asyncio.Task"] = None
        self.admission_token = admission_token
        self.cleanup_started = False
        self.completed = False
        self.done: "asyncio.Future" = loop.create_future()
        self.epoch = epoch
        self.shutdown_requested = False
        self.supervisor_task: Optional["asyncio.Future"] = None


class APIServerAdapter(BasePlatformAdapter):
    """
    OpenAI-compatible HTTP API server adapter.

    Runs an aiohttp web server that accepts OpenAI-format requests
    and routes them through hermes-agent's AIAgent.
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.API_SERVER)
        extra = config.extra or {}
        self._host: str = extra.get("host", os.getenv("API_SERVER_HOST", DEFAULT_HOST))
        raw_port = extra.get("port")
        if raw_port is None:
            raw_port = os.getenv("API_SERVER_PORT", str(DEFAULT_PORT))
        self._port: int = _coerce_port(raw_port, DEFAULT_PORT)
        self._api_key: str = extra.get("key", os.getenv("API_SERVER_KEY", ""))
        self._cors_origins: tuple[str, ...] = self._parse_cors_origins(
            extra.get("cors_origins", os.getenv("API_SERVER_CORS_ORIGINS", "")),
        )
        self._model_name: str = self._resolve_model_name(
            extra.get("model_name", os.getenv("API_SERVER_MODEL_NAME", "")),
        )
        self._app: Optional["web.Application"] = None
        self._runner: Optional["web.AppRunner"] = None
        self._site: Optional["web.TCPSite"] = None
        self._response_store = ResponseStore()
        # Active run streams: run_id -> asyncio.Queue of SSE event dicts
        self._run_streams: Dict[str, "asyncio.Queue[Optional[Dict]]"] = {}
        # Admission reservations cover route resolution through worker
        # completion.  They are separate from SSE stream retention so a
        # completed-but-unconsumed event stream does not consume capacity.
        self._run_admission_reservations: set[str] = set()
        # Creation timestamps for orphaned-run TTL sweep
        self._run_streams_created: Dict[str, float] = {}
        # Active run agent/task references for stop support
        self._active_run_agents: Dict[str, Any] = {}
        self._active_run_tasks: Dict[str, "asyncio.Task"] = {}
        # Shared idempotence guard for stop, direct cancellation, and
        # shutdown paths that can race while one run is unwinding.
        self._interrupted_run_ids: set[str] = set()
        # A stop that first denies a live approval can let the executor return
        # before Task.cancel() is observed.  Preserve the user's cancellation
        # intent across that narrow wake-to-cancel race.
        self._approval_cancelled_run_ids: set[str] = set()
        # Pollable run status for dashboards and external control-plane UIs.
        self._run_statuses: Dict[str, Dict[str, Any]] = {}
        # Active approval session key for each run_id.  The approval core
        # resolves requests by session key, while API clients address the
        # in-flight run by run_id.
        self._run_approval_sessions: Dict[str, str] = {}
        self._session_db: Optional[Any] = None  # Lazy-init SessionDB for session continuity
        # Process-local mirror for managed-session run lifecycle and tests.
        # Cross-adapter/process exclusion is enforced by the state.db lease;
        # this map must never be used as the authoritative admission gate.
        self._managed_session_runs: Dict[str, str] = {}
        self._managed_session_runs_lock = threading.Lock()
        self._managed_run_lease_owner_id = f"{os.getpid()}:{uuid.uuid4().hex}"
        # A managed lease can outlive its request while acquire, worker, or
        # exact release work is still running.  These lifecycle owners are
        # intentionally separate from ``_background_tasks``: generic gateway
        # shutdown cancels and eventually clears that set, whereas durable
        # ownership must drain through exact release before shutdown finishes.
        self._managed_lease_lifecycle_tasks: set[
            _ManagedLeaseLifecycle
        ] = set()
        self._managed_lease_shutdown_epoch = 0
        self._managed_lease_shutdown_depth = 0

    @staticmethod
    def _parse_cors_origins(value: Any) -> tuple[str, ...]:
        """Normalize configured CORS origins into a stable tuple."""
        if not value:
            return ()

        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = [str(value)]

        return tuple(str(item).strip() for item in items if str(item).strip())

    @staticmethod
    def _resolve_model_name(explicit: str) -> str:
        """Derive the advertised model name for /v1/models.

        Priority:
        1. Explicit override (config extra or API_SERVER_MODEL_NAME env var)
        2. Active profile name (so each profile advertises a distinct model)
        3. Fallback: "hermes-agent"
        """
        if explicit and explicit.strip():
            return explicit.strip()
        try:
            from hermes_cli.profiles import get_active_profile_name
            profile = get_active_profile_name()
            if profile and profile not in {"default", "custom"}:
                return profile
        except Exception:
            pass
        return "hermes-agent"

    def _cors_headers_for_origin(self, origin: str) -> Optional[Dict[str, str]]:
        """Return CORS headers for an allowed browser origin."""
        if not origin or not self._cors_origins:
            return None

        if "*" in self._cors_origins:
            headers = dict(_CORS_HEADERS)
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Access-Control-Max-Age"] = "600"
            return headers

        if origin not in self._cors_origins:
            return None

        headers = dict(_CORS_HEADERS)
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
        headers["Access-Control-Max-Age"] = "600"
        return headers

    def _origin_allowed(self, origin: str) -> bool:
        """Allow non-browser clients and explicitly configured browser origins."""
        if not origin:
            return True

        if not self._cors_origins:
            return False

        return "*" in self._cors_origins or origin in self._cors_origins

    @staticmethod
    def _clean_log_value(value: Any, *, max_len: int = 200) -> str:
        """Sanitize request metadata before it reaches security logs."""
        if value is None:
            return ""
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        return text[:max_len]

    def _request_audit_context(self, request: "web.Request") -> Dict[str, str]:
        """Return non-secret source metadata for security/audit warnings."""
        peer_ip = ""
        try:
            peer = request.transport.get_extra_info("peername") if request.transport else None
            if isinstance(peer, (tuple, list)) and peer:
                peer_ip = str(peer[0])
        except Exception:
            peer_ip = ""

        return {
            "remote": self._clean_log_value(getattr(request, "remote", "") or peer_ip),
            "peer_ip": self._clean_log_value(peer_ip),
            "forwarded_for": self._clean_log_value(request.headers.get("X-Forwarded-For", "")),
            "real_ip": self._clean_log_value(request.headers.get("X-Real-IP", "")),
            "method": self._clean_log_value(request.method, max_len=16),
            "path": self._clean_log_value(request.path_qs, max_len=500),
            "user_agent": self._clean_log_value(request.headers.get("User-Agent", ""), max_len=300),
        }

    def _request_audit_log_suffix(self, request: "web.Request") -> str:
        ctx = self._request_audit_context(request)
        fields = [f"{key}={value!r}" for key, value in ctx.items() if value]
        return " ".join(fields) if fields else "source='unknown'"

    def _cron_origin_from_request(self, request: "web.Request") -> Dict[str, str]:
        """Persist safe API source metadata on cron jobs created over HTTP."""
        ctx = self._request_audit_context(request)
        origin = {
            "platform": "api_server",
            "chat_id": "api",
        }
        if ctx.get("remote"):
            origin["source_ip"] = ctx["remote"]
        if ctx.get("peer_ip"):
            origin["peer_ip"] = ctx["peer_ip"]
        if ctx.get("forwarded_for"):
            origin["forwarded_for"] = ctx["forwarded_for"]
        if ctx.get("real_ip"):
            origin["real_ip"] = ctx["real_ip"]
        if ctx.get("user_agent"):
            origin["user_agent"] = ctx["user_agent"]
        return origin

    # ------------------------------------------------------------------
    # Auth helper
    # ------------------------------------------------------------------

    def _check_auth(self, request: "web.Request") -> Optional["web.Response"]:
        """
        Validate Bearer token from Authorization header.

        Returns None if auth is OK, or a 401 web.Response on failure.
        connect() refuses to start the API server without API_SERVER_KEY, so
        the no-key branch only exists for tests or unsupported manual wiring.
        """
        if not self._api_key:
            return None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if hmac.compare_digest(token, self._api_key):
                return None  # Auth OK

        logger.warning(
            "API server rejected invalid API key: %s",
            self._request_audit_log_suffix(request),
        )
        return web.json_response(
            {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}},
            status=401,
        )

    # ------------------------------------------------------------------
    # Session header helpers
    # ------------------------------------------------------------------

    # Soft length cap for session identifiers.  Headers are bounded in
    # aggregate by aiohttp (``client_max_size`` / default 8 KiB per
    # header), but we impose a tighter limit on the session headers so a
    # caller can't burn memory by passing a multi-kilobyte "session key".
    # 256 chars is well above any realistic stable channel identifier
    # (e.g. ``agent:main:webui:dm:user-42``) while staying small enough
    # that the sanitized form is safe to pass into Honcho / state.db.
    _MAX_SESSION_HEADER_LEN = 256

    def _parse_session_key_header(
        self, request: "web.Request"
    ) -> tuple[Optional[str], Optional["web.Response"]]:
        """Extract and validate the ``X-Hermes-Session-Key`` header.

        The session key is a stable per-channel identifier that scopes
        long-term memory (e.g. Honcho sessions) across transcripts.  It
        is independent of ``X-Hermes-Session-Id``: callers may send
        either, both, or neither.

        Returns ``(session_key, None)`` on success (with an empty/absent
        header yielding ``None`` for the key), or ``(None, error_response)``
        on validation failure.

        Security: like session continuation, accepting a caller-supplied
        memory scope requires API-key authentication so that an
        unauthenticated client on a local-only server can't inject itself
        into another user's long-term memory scope by guessing a key.
        """
        raw = request.headers.get("X-Hermes-Session-Key", "").strip()
        if not raw:
            return None, None

        if not self._api_key:
            logger.warning(
                "X-Hermes-Session-Key rejected: no API key configured. "
                "Set API_SERVER_KEY to enable long-term memory scoping."
            )
            return None, web.json_response(
                _openai_error(
                    "X-Hermes-Session-Key requires API key authentication. "
                    "Configure API_SERVER_KEY to enable this feature."
                ),
                status=403,
            )

        # Reject control characters that could enable header injection on
        # the echo path.
        if re.search(r'[\r\n\x00]', raw):
            return None, web.json_response(
                {"error": {"message": "Invalid session key", "type": "invalid_request_error"}},
                status=400,
            )

        if len(raw) > self._MAX_SESSION_HEADER_LEN:
            return None, web.json_response(
                {"error": {"message": "Session key too long", "type": "invalid_request_error"}},
                status=400,
            )

        return raw, None

    # ------------------------------------------------------------------
    # Session DB helper
    # ------------------------------------------------------------------

    def _ensure_session_db(self):
        """Lazily initialise and return the shared SessionDB instance.

        Sessions are persisted to ``state.db`` so that ``hermes sessions list``
        shows API-server conversations alongside CLI and gateway ones.
        """
        if self._session_db is None:
            try:
                from hermes_state import SessionDB
                self._session_db = SessionDB()
            except Exception as e:
                logger.debug("SessionDB unavailable for API server: %s", e)
        return self._session_db

    # ------------------------------------------------------------------
    # Agent creation helper
    # ------------------------------------------------------------------

    def _resolve_agent_route(
        self,
        *,
        requested_model: Optional[str] = None,
        requested_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve one internally consistent model/provider route for a run."""
        from gateway.run import (
            GatewayRunner,
            _resolve_gateway_model,
            _resolve_runtime_agent_kwargs,
        )

        explicit_route = bool(requested_model or requested_provider)
        model = requested_model or _resolve_gateway_model()
        if explicit_route:
            if not model:
                raise RuntimeError("No model configured for the requested provider")
            runtime_kwargs = _resolve_runtime_agent_kwargs(
                requested=requested_provider,
                target_model=model,
                allow_configured_fallback=False,
            )
            fallback_model = None
        else:
            runtime_kwargs = _resolve_runtime_agent_kwargs()
            fallback_model = GatewayRunner._load_fallback_model()

        runtime_kwargs = dict(runtime_kwargs)
        runtime_model = runtime_kwargs.pop("model", None)
        if not explicit_route and runtime_model:
            model = runtime_model
        if not model and runtime_kwargs.get("provider"):
            from hermes_cli.models import get_default_model_for_provider

            model = get_default_model_for_provider(runtime_kwargs["provider"])
        if not model:
            raise RuntimeError("No model configured for the resolved provider")

        return {
            "model": model,
            "runtime_kwargs": runtime_kwargs,
            "fallback_model": fallback_model,
            "provider": runtime_kwargs.get("provider"),
        }

    def _create_agent(
        self,
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        gateway_session_key: Optional[str] = None,
        requested_model: Optional[str] = None,
        requested_provider: Optional[str] = None,
        resolved_route: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Create an AIAgent instance using the gateway's runtime config.

        Uses _resolve_runtime_agent_kwargs() to pick up model, api_key,
        base_url, etc. from config.yaml / env vars.  Toolsets are resolved
        from config.yaml platform_toolsets.api_server (same as all other
        gateway platforms), falling back to the hermes-api-server default.

        ``gateway_session_key`` is a stable per-channel identifier supplied
        by the client (via ``X-Hermes-Session-Key``).  Unlike ``session_id``
        which scopes the short-term transcript and rotates on /new, this
        key is meant to persist across transcripts so long-term memory
        providers (e.g. Honcho) can scope their per-chat state correctly
        — matching the semantics of the native gateway's ``session_key``.
        """
        from run_agent import AIAgent
        from gateway.run import GatewayRunner, _load_gateway_config
        from hermes_cli.tools_config import _get_platform_tools

        route = resolved_route or self._resolve_agent_route(
            requested_model=requested_model,
            requested_provider=requested_provider,
        )
        runtime_kwargs = route["runtime_kwargs"]
        reasoning_config = GatewayRunner._load_reasoning_config()
        model = route["model"]

        user_config = _load_gateway_config()
        enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))

        max_iterations = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))

        agent = AIAgent(
            model=model,
            **runtime_kwargs,
            max_iterations=max_iterations,
            quiet_mode=True,
            verbose_logging=False,
            ephemeral_system_prompt=ephemeral_system_prompt or None,
            enabled_toolsets=enabled_toolsets,
            session_id=session_id,
            platform="api_server",
            stream_delta_callback=stream_delta_callback,
            tool_progress_callback=tool_progress_callback,
            tool_start_callback=tool_start_callback,
            tool_complete_callback=tool_complete_callback,
            session_db=self._ensure_session_db(),
            fallback_model=route["fallback_model"],
            reasoning_config=reasoning_config,
            gateway_session_key=gateway_session_key,
        )
        return agent

    # ------------------------------------------------------------------
    # HTTP Handlers
    # ------------------------------------------------------------------

    def _license_guard_response(self) -> Optional["web.Response"]:
        blocked = taiji_license.require_valid_license()
        if blocked is None:
            return None
        return web.json_response(
            _openai_error(
                blocked.message or taiji_license.MESSAGE_INVALID,
                code=blocked.code or "license_invalid",
            ),
            status=403,
        )

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        """GET /health — simple health check."""
        return web.json_response({"status": "ok", "platform": "taiji-agent"})

    async def _handle_license_status(self, request: "web.Request") -> "web.Response":
        """GET /v1/license/status — return redacted license state."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        return web.json_response(taiji_license.load_license_status().to_public_dict())

    async def _handle_license_activate(self, request: "web.Request") -> "web.Response":
        """POST /v1/license/activate — reserved for a later online activation service."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        return web.json_response(
            _openai_error(
                taiji_license.MESSAGE_ONLINE_ACTIVATION_UNAVAILABLE,
                code="license_online_activation_unavailable",
            ),
            status=501,
        )

    async def _handle_health_detailed(self, request: "web.Request") -> "web.Response":
        """GET /health/detailed — rich status for cross-container dashboard probing.

        Returns gateway state, connected platforms, PID, and uptime so the
        dashboard can display full status without needing a shared PID file or
        /proc access.  No authentication required.
        """
        from gateway.status import read_runtime_status

        runtime = read_runtime_status() or {}
        return web.json_response({
            "status": "ok",
            "platform": "taiji-agent",
            "gateway_state": runtime.get("gateway_state"),
            "platforms": runtime.get("platforms", {}),
            "active_agents": runtime.get("active_agents", 0),
            "exit_reason": runtime.get("exit_reason"),
            "updated_at": runtime.get("updated_at"),
            "pid": os.getpid(),
            "license": taiji_license.load_license_status().to_public_dict(),
        })

    async def _handle_models(self, request: "web.Request") -> "web.Response":
        """GET /v1/models — return hermes-agent as an available model."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        return web.json_response({
            "object": "list",
            "data": [
                {
                    "id": self._model_name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "taiji",
                    "permission": [],
                    "root": self._model_name,
                    "parent": None,
                }
            ],
        })

    async def _handle_capabilities(self, request: "web.Request") -> "web.Response":
        """GET /v1/capabilities — advertise the stable API surface.

        External UIs and orchestrators use this endpoint to discover the API
        server's plugin-safe contract without scraping docs or assuming that
        every Hermes version exposes the same endpoints.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        return web.json_response({
            "object": "taiji.api_server.capabilities",
            "platform": "taiji-agent",
            "model": self._model_name,
            "auth": {
                "type": "bearer",
                "required": bool(self._api_key),
            },
            "runtime": {
                "mode": "server_agent",
                "tool_execution": "server",
                "split_runtime": False,
                "description": (
                    "The API server creates a server-side Taiji agent runtime; "
                    "tools execute on the API-server host unless a future "
                    "explicit split-runtime mode is enabled."
                ),
            },
            "features": {
                "chat_completions": True,
                "chat_completions_streaming": True,
                "responses_api": True,
                "responses_streaming": True,
                "run_submission": True,
                "run_status": True,
                "run_events_sse": True,
                "run_stop": True,
                "run_approval_response": True,
                "tool_progress_events": True,
                "approval_events": True,
                "session_resources": True,
                "session_chat": True,
                "session_chat_streaming": True,
                "session_fork": True,
                "admin_config_rw": False,
                "jobs_admin": False,
                "memory_write_api": False,
                "skills_api": True,
                "audio_api": False,
                "realtime_voice": False,
                "session_continuity_header": "X-Hermes-Session-Id",
                "session_key_header": "X-Hermes-Session-Key",
                "cors": bool(self._cors_origins),
            },
            "endpoints": {
                "health": {"method": "GET", "path": "/health"},
                "health_detailed": {"method": "GET", "path": "/health/detailed"},
                "models": {"method": "GET", "path": "/v1/models"},
                "chat_completions": {"method": "POST", "path": "/v1/chat/completions"},
                "responses": {"method": "POST", "path": "/v1/responses"},
                "runs": {"method": "POST", "path": "/v1/runs"},
                "run_status": {"method": "GET", "path": "/v1/runs/{run_id}"},
                "run_events": {"method": "GET", "path": "/v1/runs/{run_id}/events"},
                "run_approval": {"method": "POST", "path": "/v1/runs/{run_id}/approval"},
                "run_stop": {"method": "POST", "path": "/v1/runs/{run_id}/stop"},
                "skills": {"method": "GET", "path": "/v1/skills"},
                "toolsets": {"method": "GET", "path": "/v1/toolsets"},
                "sessions": {"method": "GET", "path": "/api/sessions"},
                "session_create": {"method": "POST", "path": "/api/sessions"},
                "session": {"method": "GET", "path": "/api/sessions/{session_id}"},
                "session_update": {"method": "PATCH", "path": "/api/sessions/{session_id}"},
                "session_delete": {"method": "DELETE", "path": "/api/sessions/{session_id}"},
                "session_messages": {"method": "GET", "path": "/api/sessions/{session_id}/messages"},
                "session_fork": {"method": "POST", "path": "/api/sessions/{session_id}/fork"},
                "session_chat": {"method": "POST", "path": "/api/sessions/{session_id}/chat"},
                "session_chat_stream": {"method": "POST", "path": "/api/sessions/{session_id}/chat/stream"},
            },
        })

    async def _handle_skills(self, request: "web.Request") -> "web.Response":
        """GET /v1/skills — list installed skills visible to the API-server agent.

        Read-only listing intended for external clients that need to know
        which skills are available without sending a chat message and asking
        the model. Mirrors what the gateway/CLI surfaces through
        ``/skills list``, but as a deterministic JSON payload.

        Returns the same skill metadata (name, description, category) the
        skills hub uses internally. Disabled skills are excluded so the
        listing matches what the agent actually loads.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        try:
            from tools.skills_tool import _find_all_skills, _sort_skills
            skills = _sort_skills(_find_all_skills(skip_disabled=False))
        except Exception:
            logger.exception("GET /v1/skills failed")
            return web.json_response(
                _openai_error("Failed to enumerate skills", err_type="server_error"),
                status=500,
            )

        return web.json_response({
            "object": "list",
            "data": skills,
        })

    async def _handle_toolsets(self, request: "web.Request") -> "web.Response":
        """GET /v1/toolsets — list toolsets and their resolved tools.

        Returns the toolset surface the api_server platform actually exposes
        to its agent: each toolset's enabled/configured state plus the
        concrete tool names it expands to. This is the deterministic
        equivalent of what a client would otherwise have to recover by
        asking the model what tools it can call.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        try:
            from hermes_cli.config import load_config
            from hermes_cli.tools_config import (
                _get_effective_configurable_toolsets,
                _get_platform_tools,
                _toolset_has_keys,
            )
            from toolsets import resolve_toolset

            config = load_config()
            enabled_toolsets = _get_platform_tools(
                config,
                "api_server",
                include_default_mcp_servers=False,
            )
            data: List[Dict[str, Any]] = []
            for name, label, desc in _get_effective_configurable_toolsets():
                try:
                    tools = sorted(set(resolve_toolset(name)))
                except Exception:
                    tools = []
                is_enabled = name in enabled_toolsets
                configured = _toolset_has_keys(name, config)
                available = bool(is_enabled and configured)
                reason_code = "ready" if available else (
                    "disabled" if not is_enabled else "not_configured"
                )
                public_message = ""
                if name == "image_gen":
                    try:
                        from tools.image_generation_tool import get_image_generation_readiness
                        import model_tools

                        readiness = get_image_generation_readiness()
                        configured = bool(readiness.get("configured"))
                        tool_defs = model_tools.get_tool_definitions(
                            enabled_toolsets=[name],
                            quiet_mode=True,
                        )
                        schema_names = {
                            item.get("function", {}).get("name")
                            for item in tool_defs
                            if isinstance(item, dict)
                        }
                        if not is_enabled:
                            available = False
                            reason_code = "disabled"
                            public_message = "图像生成未启用。"
                            tools = []
                        else:
                            available = bool(
                                readiness.get("available")
                                and "image_generate" in schema_names
                            )
                            reason_code = str(
                                readiness.get("reason_code")
                                or ("ready" if available else "unavailable")
                            )
                            public_message = str(readiness.get("public_message") or "")
                            tools = sorted(schema_names) if available else []
                    except Exception:
                        logger.debug("Failed to resolve image generation readiness", exc_info=True)
                        configured = False
                        available = False
                        reason_code = "unavailable"
                        public_message = "图像生成服务暂不可用，请检查太极智能体图像生成配置。"
                        tools = []
                data.append({
                    "name": name,
                    "label": label,
                    "description": desc,
                    "enabled": is_enabled,
                    "configured": configured,
                    "available": available,
                    "reason_code": reason_code,
                    "public_message": public_message,
                    "tools": tools,
                })
        except Exception:
            logger.exception("GET /v1/toolsets failed")
            return web.json_response(
                _openai_error("Failed to enumerate toolsets", err_type="server_error"),
                status=500,
            )

        return web.json_response({
            "object": "list",
            "platform": "api_server",
            "data": data,
        })

    # ------------------------------------------------------------------
    # /api/sessions — thin client/session resource API
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_nonnegative_int(value: Any, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        if parsed < 0:
            return default
        return min(parsed, maximum)

    @staticmethod
    def _session_response(session: Dict[str, Any]) -> Dict[str, Any]:
        """Return a stable, client-safe session representation."""
        safe_keys = (
            "id", "source", "user_id", "model", "title", "started_at", "ended_at",
            "end_reason", "message_count", "tool_call_count", "input_tokens",
            "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd",
            "api_call_count", "parent_session_id", "last_active", "preview",
            "_lineage_root_id",
        )
        payload = {key: session.get(key) for key in safe_keys if key in session}
        # Avoid exposing full system prompts/model_config through the client API;
        # callers only need to know whether those snapshots exist.
        payload["has_system_prompt"] = bool(session.get("system_prompt"))
        payload["has_model_config"] = bool(session.get("model_config"))
        return payload

    @staticmethod
    def _message_response(message: Dict[str, Any]) -> Dict[str, Any]:
        safe_keys = (
            "id", "session_id", "role", "content", "tool_call_id", "tool_calls",
            "tool_name", "timestamp", "token_count", "finish_reason", "reasoning",
            "reasoning_content",
        )
        return {key: message.get(key) for key in safe_keys if key in message}

    async def _read_json_body(self, request: "web.Request") -> tuple[Dict[str, Any], Optional["web.Response"]]:
        try:
            body = await request.json()
        except Exception:
            return {}, web.json_response(_openai_error("Invalid JSON in request body"), status=400)
        if not isinstance(body, dict):
            return {}, web.json_response(_openai_error("Request body must be a JSON object"), status=400)
        return body, None

    def _get_existing_session_or_404(self, session_id: str) -> tuple[Optional[Dict[str, Any]], Optional["web.Response"]]:
        db = self._ensure_session_db()
        if db is None:
            return None, web.json_response(_openai_error("Session database unavailable", code="session_db_unavailable"), status=503)
        session = db.get_session(session_id)
        if not session:
            return None, web.json_response(_openai_error(f"Session not found: {session_id}", code="session_not_found"), status=404)
        return session, None

    def _conversation_history_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        db = self._ensure_session_db()
        if db is None:
            return []
        try:
            return db.get_messages_as_conversation(session_id)
        except Exception as exc:
            logger.warning("Failed to load session history for %s: %s", session_id, exc)
            return []

    async def _handle_list_sessions(self, request: "web.Request") -> "web.Response":
        """GET /api/sessions — list persisted Hermes sessions."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        db = self._ensure_session_db()
        if db is None:
            return web.json_response(_openai_error("Session database unavailable", code="session_db_unavailable"), status=503)

        limit = self._parse_nonnegative_int(request.query.get("limit"), default=50, maximum=200)
        offset = self._parse_nonnegative_int(request.query.get("offset"), default=0, maximum=1_000_000)
        source = request.query.get("source") or None
        include_children = _coerce_request_bool(request.query.get("include_children"), default=False)
        sessions = db.list_sessions_rich(
            source=source,
            limit=limit,
            offset=offset,
            include_children=include_children,
            order_by_last_active=True,
        )
        return web.json_response({
            "object": "list",
            "data": [self._session_response(s) for s in sessions],
            "limit": limit,
            "offset": offset,
            "has_more": len(sessions) == limit,
        })

    async def _handle_create_session(self, request: "web.Request") -> "web.Response":
        """POST /api/sessions — create an empty Hermes session row."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err

        db = self._ensure_session_db()
        if db is None:
            return web.json_response(_openai_error("Session database unavailable", code="session_db_unavailable"), status=503)

        raw_id = body.get("id") or body.get("session_id")
        session_id = str(raw_id).strip() if raw_id else f"api_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        if not session_id or re.search(r'[\r\n\x00]', session_id):
            return web.json_response(_openai_error("Invalid session ID", code="invalid_session_id"), status=400)
        if len(session_id) > self._MAX_SESSION_HEADER_LEN:
            return web.json_response(_openai_error("Session ID too long", code="invalid_session_id"), status=400)
        if db.get_session(session_id):
            return web.json_response(_openai_error(f"Session already exists: {session_id}", code="session_exists"), status=409)

        model = body.get("model") or self._model_name
        system_prompt = body.get("system_prompt")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return web.json_response(_openai_error("system_prompt must be a string", code="invalid_system_prompt"), status=400)
        db.create_session(session_id, "api_server", model=str(model) if model else None, system_prompt=system_prompt)
        title = body.get("title")
        if title is not None:
            try:
                db.set_session_title(session_id, str(title))
            except ValueError as exc:
                db.delete_session(session_id)
                return web.json_response(_openai_error(str(exc), code="invalid_title"), status=400)
        session = db.get_session(session_id) or {"id": session_id, "source": "api_server", "model": model, "title": title}
        return web.json_response({"object": "hermes.session", "session": self._session_response(session)}, status=201)

    async def _handle_get_session(self, request: "web.Request") -> "web.Response":
        """GET /api/sessions/{session_id}."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session, err = self._get_existing_session_or_404(request.match_info["session_id"])
        if err:
            return err
        return web.json_response({"object": "hermes.session", "session": self._session_response(session)})

    async def _handle_patch_session(self, request: "web.Request") -> "web.Response":
        """PATCH /api/sessions/{session_id} — update client-safe session metadata."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        session, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        allowed = {"title", "end_reason"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            return web.json_response(_openai_error(f"Unsupported session fields: {', '.join(unknown)}", code="unsupported_session_field"), status=400)

        db = self._ensure_session_db()
        if "title" in body:
            try:
                db.set_session_title(session_id, "" if body["title"] is None else str(body["title"]))
            except ValueError as exc:
                return web.json_response(_openai_error(str(exc), code="invalid_title"), status=400)
        if body.get("end_reason"):
            db.end_session(session_id, str(body["end_reason"]))
        session = db.get_session(session_id) or session
        return web.json_response({"object": "hermes.session", "session": self._session_response(session)})

    async def _handle_delete_session(self, request: "web.Request") -> "web.Response":
        """DELETE /api/sessions/{session_id}."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        session, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        db = self._ensure_session_db()
        deleted = db.delete_session(session_id)
        return web.json_response({"object": "hermes.session.deleted", "id": session_id, "deleted": bool(deleted)})

    async def _handle_session_messages(self, request: "web.Request") -> "web.Response":
        """GET /api/sessions/{session_id}/messages."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        _, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        db = self._ensure_session_db()
        messages = db.get_messages(session_id)
        return web.json_response({
            "object": "list",
            "session_id": session_id,
            "data": [self._message_response(m) for m in messages],
        })

    async def _handle_fork_session(self, request: "web.Request") -> "web.Response":
        """POST /api/sessions/{session_id}/fork — branch via current SessionDB primitives."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        source_id = request.match_info["session_id"]
        source, err = self._get_existing_session_or_404(source_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        db = self._ensure_session_db()
        fork_id = str(body.get("id") or body.get("session_id") or f"api_{int(time.time())}_{uuid.uuid4().hex[:8]}").strip()
        if not fork_id or re.search(r'[\r\n\x00]', fork_id):
            return web.json_response(_openai_error("Invalid session ID", code="invalid_session_id"), status=400)
        if db.get_session(fork_id):
            return web.json_response(_openai_error(f"Session already exists: {fork_id}", code="session_exists"), status=409)

        # Match the CLI /branch semantics: mark the original as branched, then
        # create a child session that carries the transcript forward. This uses
        # SessionDB's native parent_session_id/end_reason visibility model rather
        # than inventing a parallel fork store.
        db.end_session(source_id, "branched")
        db.create_session(
            fork_id,
            "api_server",
            model=source.get("model"),
            system_prompt=source.get("system_prompt"),
            parent_session_id=source_id,
        )
        messages = db.get_messages(source_id)
        db.replace_messages(fork_id, messages)
        title = body.get("title")
        if title is None:
            base = source.get("title") or "fork"
            try:
                title = db.get_next_title_in_lineage(base)
            except Exception:
                title = f"{base} fork"
        try:
            db.set_session_title(fork_id, str(title))
        except ValueError as exc:
            return web.json_response(_openai_error(str(exc), code="invalid_title"), status=400)
        fork = db.get_session(fork_id) or {"id": fork_id, "parent_session_id": source_id}
        return web.json_response({"object": "hermes.session", "session": self._session_response(fork)}, status=201)

    async def _handle_session_chat(self, request: "web.Request") -> "web.Response":
        """POST /api/sessions/{session_id}/chat — one synchronous agent turn."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        license_err = self._license_guard_response()
        if license_err:
            return license_err
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err
        session_id = request.match_info["session_id"]
        _, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        user_message, err = _session_chat_user_message(body)
        if err is not None:
            return err
        system_prompt = body.get("system_message") or body.get("instructions")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return web.json_response(_openai_error("system_message must be a string", code="invalid_system_message"), status=400)
        history = self._conversation_history_for_session(session_id)
        result, usage = await self._run_agent(
            user_message=user_message,
            conversation_history=history,
            ephemeral_system_prompt=system_prompt,
            session_id=session_id,
            gateway_session_key=gateway_session_key,
        )
        effective_session_id = result.get("session_id") if isinstance(result, dict) else session_id
        final_response = result.get("final_response", "") if isinstance(result, dict) else ""
        headers = {"X-Hermes-Session-Id": effective_session_id or session_id}
        if gateway_session_key:
            headers["X-Hermes-Session-Key"] = gateway_session_key
        return web.json_response(
            {
                "object": "hermes.session.chat.completion",
                "session_id": effective_session_id or session_id,
                "message": {"role": "assistant", "content": final_response},
                "usage": usage,
            },
            headers=headers,
        )

    async def _handle_session_chat_stream(self, request: "web.Request") -> "web.StreamResponse":
        """POST /api/sessions/{session_id}/chat/stream — SSE wrapper over _run_agent."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        license_err = self._license_guard_response()
        if license_err:
            return license_err
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err
        session_id = request.match_info["session_id"]
        _, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        user_message, err = _session_chat_user_message(body)
        if err is not None:
            return err
        system_prompt = body.get("system_message") or body.get("instructions")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return web.json_response(_openai_error("system_message must be a string", code="invalid_system_message"), status=400)

        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[Optional[tuple[str, Dict[str, Any]]]]" = asyncio.Queue()
        message_id = f"msg_{uuid.uuid4().hex}"
        run_id = f"run_{uuid.uuid4().hex}"
        seq = 0

        def _event_payload(name: str, payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
            nonlocal seq
            seq += 1
            payload.setdefault("session_id", session_id)
            payload.setdefault("run_id", run_id)
            payload.setdefault("seq", seq)
            payload.setdefault("ts", time.time())
            return name, payload

        def _enqueue(name: str, payload: Dict[str, Any]) -> None:
            event = _event_payload(name, payload)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            try:
                if running_loop is loop:
                    queue.put_nowait(event)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass

        def _delta(delta: str) -> None:
            if delta:
                _enqueue("assistant.delta", {"message_id": message_id, "delta": delta})

        def _tool_progress(event_type: str, tool_name: str = None, preview: str = None, args=None, **kwargs) -> None:
            if event_type == "reasoning.available":
                _enqueue("tool.progress", {"message_id": message_id, "tool_name": tool_name or "_thinking", "delta": preview or ""})
            elif event_type in {"tool.started", "tool.completed", "tool.failed"}:
                event_name = event_type.replace("tool.", "tool.")
                _enqueue(event_name, {"message_id": message_id, "tool_name": tool_name, "preview": preview, "args": args})

        async def _run_and_signal() -> None:
            try:
                await queue.put(_event_payload("run.started", {"user_message": {"role": "user", "content": user_message}}))
                await queue.put(_event_payload("message.started", {"message": {"id": message_id, "role": "assistant"}}))
                history = self._conversation_history_for_session(session_id)
                result, usage = await self._run_agent(
                    user_message=user_message,
                    conversation_history=history,
                    ephemeral_system_prompt=system_prompt,
                    session_id=session_id,
                    stream_delta_callback=_delta,
                    tool_progress_callback=_tool_progress,
                    gateway_session_key=gateway_session_key,
                )
                final_response = result.get("final_response", "") if isinstance(result, dict) else ""
                effective_session_id = result.get("session_id", session_id) if isinstance(result, dict) else session_id
                await queue.put(_event_payload("assistant.completed", {
                    "session_id": effective_session_id,
                    "message_id": message_id,
                    "content": final_response,
                    "completed": True,
                    "partial": False,
                    "interrupted": False,
                }))
                await queue.put(_event_payload("run.completed", {
                    "session_id": effective_session_id,
                    "message_id": message_id,
                    "completed": True,
                    "usage": usage,
                }))
            except Exception as exc:
                logger.exception("[api_server] session chat stream failed")
                await queue.put(_event_payload("error", _session_stream_error_payload()))
            finally:
                await queue.put(_event_payload("done", {}))
                await queue.put(None)

        task = asyncio.create_task(_run_and_signal())
        try:
            self._background_tasks.add(task)
        except TypeError:
            pass
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(self._background_tasks.discard)

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Hermes-Session-Id": session_id,
        }
        if gateway_session_key:
            headers["X-Hermes-Session-Key"] = gateway_session_key
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        last_write = time.monotonic()
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
                    last_write = time.monotonic()
                    continue
                if item is None:
                    break
                name, payload = item
                data = json.dumps(payload, ensure_ascii=False)
                await response.write(f"event: {name}\ndata: {data}\n\n".encode("utf-8"))
                last_write = time.monotonic()
        except (asyncio.CancelledError, ConnectionResetError):
            task.cancel()
            raise
        except Exception as exc:
            logger.debug("[api_server] session SSE stream error: %s", exc)
        return response

    async def _handle_chat_completions(self, request: "web.Request") -> "web.Response":
        """POST /v1/chat/completions — OpenAI Chat Completions format."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        license_err = self._license_guard_response()
        if license_err:
            return license_err

        # Parse request body
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(_openai_error("Invalid JSON in request body"), status=400)

        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return web.json_response(
                {"error": {"message": "Missing or invalid 'messages' field", "type": "invalid_request_error"}},
                status=400,
            )

        stream = _coerce_request_bool(body.get("stream"), default=False)

        # Extract system message (becomes ephemeral system prompt layered ON TOP of core)
        system_prompt = None
        conversation_messages: List[Dict[str, str]] = []

        for idx, msg in enumerate(messages):
            role = msg.get("role", "")
            raw_content = msg.get("content", "")
            if role == "system":
                # System messages don't support images (Anthropic rejects, OpenAI
                # text-model systems don't render them).  Flatten to text.
                content = _normalize_chat_content(raw_content)
                if system_prompt is None:
                    system_prompt = content
                else:
                    system_prompt = system_prompt + "\n" + content
            elif role in {"user", "assistant", "tool"}:
                try:
                    content = _normalize_multimodal_content(raw_content)
                except ValueError as exc:
                    return _multimodal_validation_error(exc, param=f"messages[{idx}].content")
                conversation_messages.append(_normalized_conversation_message(msg, content))

        # Extract the last user message as the primary input
        user_message: Any = ""
        history = []
        if conversation_messages:
            user_message = conversation_messages[-1].get("content", "")
            history = conversation_messages[:-1]

        if not _content_has_visible_payload(user_message):
            return web.json_response(
                {"error": {"message": "No user message found in messages", "type": "invalid_request_error"}},
                status=400,
            )

        platform_message_id, platform_message_id_err = (
            _parse_request_platform_message_id(body)
        )
        if platform_message_id_err is not None:
            return platform_message_id_err

        # Allow caller to scope long-term memory (e.g. Honcho) with a
        # stable per-channel identifier via X-Hermes-Session-Key.  This
        # is independent of X-Hermes-Session-Id: the key persists across
        # transcripts while the id rotates when the caller starts a new
        # transcript (i.e. /new semantics).  See _parse_session_key_header.
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        # Allow caller to continue an existing session by passing X-Hermes-Session-Id.
        # When provided, history is loaded from state.db instead of from the request body.
        #
        # Security: session continuation exposes conversation history, so it is
        # only allowed when the API key is configured and the request is
        # authenticated.  Without this gate, any unauthenticated client could
        # read arbitrary session history by guessing/enumerating session IDs.
        provided_session_id = request.headers.get("X-Hermes-Session-Id", "").strip()
        if provided_session_id:
            if not self._api_key:
                logger.warning(
                    "Session continuation via X-Hermes-Session-Id rejected: "
                    "no API key configured.  Set API_SERVER_KEY to enable "
                    "session continuity."
                )
                return web.json_response(
                    _openai_error(
                        "Session continuation requires API key authentication. "
                        "Configure API_SERVER_KEY to enable this feature."
                    ),
                    status=403,
                )
            # Sanitize: reject control characters that could enable header injection.
            if re.search(r'[\r\n\x00]', provided_session_id):
                return web.json_response(
                    {"error": {"message": "Invalid session ID", "type": "invalid_request_error"}},
                    status=400,
                )
            session_id = provided_session_id
            try:
                db = self._ensure_session_db()
                if db is not None:
                    history = db.get_messages_as_conversation(session_id)
            except Exception as e:
                logger.warning("Failed to load session history for %s: %s", session_id, e)
                history = []
        else:
            # Derive a stable session ID from the conversation fingerprint so
            # that consecutive messages from the same Open WebUI (or similar)
            # conversation map to the same Hermes session.  The first user
            # message + system prompt are constant across all turns.
            first_user = ""
            for cm in conversation_messages:
                if cm.get("role") == "user":
                    first_user = cm.get("content", "")
                    break
            session_id = _derive_chat_session_id(system_prompt, first_user)
            # history already set from request body above

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        model_name = body.get("model", self._model_name)
        created = int(time.time())

        if stream:
            import queue as _q
            _stream_q: _q.Queue = _q.Queue()

            def _on_delta(delta):
                # Filter out None — the agent fires stream_delta_callback(None)
                # to signal the CLI display to close its response box before
                # tool execution, but the SSE writer uses None as end-of-stream
                # sentinel.  Forwarding it would prematurely close the HTTP
                # response, causing Open WebUI (and similar frontends) to miss
                # the final answer after tool calls.  The SSE loop detects
                # completion via agent_task.done() instead.
                if delta is not None:
                    _stream_q.put(delta)

            # Track which tool_call_ids we've emitted a "running" lifecycle
            # event for, so a "completed" event without a matching "running"
            # (e.g. internal/filtered tools) is silently dropped instead of
            # producing an orphaned event clients can't correlate.
            _started_tool_call_ids: set[str] = set()

            def _on_tool_start(tool_call_id, function_name, function_args):
                """Emit ``hermes.tool.progress`` with ``status: running``.

                Replaces the old ``tool_progress_callback("tool.started",
                ...)`` emit so SSE consumers receive a single event per
                tool start, carrying both the legacy ``tool``/``emoji``/
                ``label`` payload (for #6972 frontends) and the new
                ``toolCallId``/``status`` correlation fields (#16588).

                Skips tools whose names start with ``_`` so internal
                events (``_thinking``, …) stay off the wire — matching
                the prior ``_on_tool_progress`` filter exactly.
                """
                if not tool_call_id or function_name.startswith("_"):
                    return
                _started_tool_call_ids.add(tool_call_id)
                from agent.display import build_tool_preview, get_tool_emoji
                label = build_tool_preview(function_name, function_args) or function_name
                _stream_q.put(("__tool_progress__", {
                    "tool": function_name,
                    "emoji": get_tool_emoji(function_name),
                    "label": label,
                    "toolCallId": tool_call_id,
                    "status": "running",
                }))

            def _on_tool_complete(tool_call_id, function_name, function_args, function_result):
                """Emit the matching ``status: completed`` event.

                Dropped if the start was filtered (internal tool, missing
                id, or never seen) so clients never get an orphaned
                ``completed`` they can't correlate to a prior ``running``.
                """
                if not tool_call_id or tool_call_id not in _started_tool_call_ids:
                    return
                _started_tool_call_ids.discard(tool_call_id)
                event = {
                    "tool": function_name,
                    "toolCallId": tool_call_id,
                    "status": "completed",
                }
                structured_result = _structured_tool_result_for_gateway(
                    function_name, function_result
                )
                if structured_result is not None:
                    event["structured_result"] = structured_result
                _stream_q.put(("__tool_progress__", event))

            # Start agent in background.  agent_ref is a mutable container
            # so the SSE writer can interrupt the agent on client disconnect.
            #
            # ``tool_progress_callback`` is intentionally not wired here:
            # it would duplicate every emit because ``run_agent`` fires it
            # side-by-side with ``tool_start_callback``/``tool_complete_callback``.
            # The structured callbacks are strictly richer (they carry the
            # tool_call id), so they own the chat-completions SSE channel.
            agent_ref = [None]
            persist_kwargs = (
                {"persist_user_platform_message_id": platform_message_id}
                if platform_message_id
                else {}
            )
            agent_task = asyncio.ensure_future(self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                agent_ref=agent_ref,
                gateway_session_key=gateway_session_key,
                **persist_kwargs,
            ))
            # Ensure SSE drain loops can terminate without relying on polling
            # agent_task.done(), which can race with queue timeout checks.
            agent_task.add_done_callback(lambda _fut: _stream_q.put(None))

            return await self._write_sse_chat_completion(
                request, completion_id, model_name, created, _stream_q,
                agent_task, agent_ref, session_id=session_id,
                gateway_session_key=gateway_session_key,
            )

        # Non-streaming: run the agent (with optional Idempotency-Key)
        async def _compute_completion():
            persist_kwargs = (
                {"persist_user_platform_message_id": platform_message_id}
                if platform_message_id
                else {}
            )
            return await self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
                **persist_kwargs,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            fp = _make_request_fingerprint(body, keys=["model", "messages", "tools", "tool_choice", "stream"])
            try:
                result, usage = await _idem_cache.get_or_set(idempotency_key, fp, _compute_completion)
            except Exception as e:
                logger.error("Error running agent for chat completions: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )
        else:
            try:
                result, usage = await _compute_completion()
            except Exception as e:
                logger.error("Error running agent for chat completions: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )

        final_response = result.get("final_response") or ""
        is_partial = bool(result.get("partial"))
        is_failed = bool(result.get("failed"))
        completed = bool(result.get("completed", True))
        err_msg = result.get("error")

        # Decide finish_reason. OpenAI uses "length" for truncation, "stop"
        # for normal completion, and downstream SDKs accept "error" / custom
        # codes. See issue #22496.
        if is_partial and err_msg and "truncat" in err_msg.lower():
            finish_reason = "length"
        elif is_failed or (not completed and err_msg):
            finish_reason = "error"
        else:
            finish_reason = "stop"

        response_headers = {
            "X-Hermes-Session-Id": result.get("session_id", session_id),
        }
        if gateway_session_key:
            response_headers["X-Hermes-Session-Key"] = gateway_session_key

        # Hard-fail path: no usable assistant text AND a real failure → 5xx
        # with OpenAI-style error envelope so SDK clients raise instead of
        # silently rendering the internal failure string as message.content.
        if not final_response and (is_failed or is_partial):
            err_body = _openai_error(
                err_msg or "Agent run did not produce a response.",
                err_type="server_error",
                code="agent_incomplete",
            )
            err_body["error"]["hermes"] = {
                "completed": completed,
                "partial": is_partial,
                "failed": is_failed,
            }
            response_headers["X-Hermes-Completed"] = "false"
            response_headers["X-Hermes-Partial"] = "true" if is_partial else "false"
            return web.json_response(err_body, status=502, headers=response_headers)

        # Soft-partial path: we have *some* text but the run did not complete
        # (e.g. truncation with partial buffered output). Still 200 but signal
        # truncation via finish_reason="length" + Hermes-specific extras.
        response_data = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": final_response,
                    },
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        if is_partial or is_failed or not completed:
            response_data["hermes"] = {
                "completed": completed,
                "partial": is_partial,
                "failed": is_failed,
                "error": err_msg,
                "error_code": "output_truncated" if finish_reason == "length" else "agent_error",
            }
            response_headers["X-Hermes-Completed"] = "false"
            response_headers["X-Hermes-Partial"] = "true" if is_partial else "false"
            if err_msg:
                response_headers["X-Hermes-Error"] = err_msg[:200]

        return web.json_response(response_data, headers=response_headers)

    async def _write_sse_chat_completion(
        self, request: "web.Request", completion_id: str, model: str,
        created: int, stream_q, agent_task, agent_ref=None, session_id: str = None,
        gateway_session_key: str = None,
    ) -> "web.StreamResponse":
        """Write real streaming SSE from agent's stream_delta_callback queue.

        If the client disconnects mid-stream (network drop, browser tab close),
        the agent is interrupted via ``agent.interrupt()`` so it stops making
        LLM API calls, and the asyncio task wrapper is cancelled.
        """
        import queue as _q

        sse_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        # CORS middleware can't inject headers into StreamResponse after
        # prepare() flushes them, so resolve CORS headers up front.
        origin = request.headers.get("Origin", "")
        cors = self._cors_headers_for_origin(origin) if origin else None
        if cors:
            sse_headers.update(cors)
        if session_id:
            sse_headers["X-Hermes-Session-Id"] = session_id
        if gateway_session_key:
            sse_headers["X-Hermes-Session-Key"] = gateway_session_key
        response = web.StreamResponse(status=200, headers=sse_headers)
        await response.prepare(request)

        try:
            last_activity = time.monotonic()

            # Role chunk
            role_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            await response.write(f"data: {json.dumps(role_chunk)}\n\n".encode())
            last_activity = time.monotonic()

            # Helper — route a queue item to the correct SSE event.
            async def _emit(item):
                """Write a single queue item to the SSE stream.

                Plain strings are sent as normal ``delta.content`` chunks.
                Tagged tuples ``("__tool_progress__", payload)`` are sent
                as a custom ``event: hermes.tool.progress`` SSE event so
                frontends can display them without storing the markers in
                conversation history.  See #6972 for the original event,
                #16588 for the ``toolCallId``/``status`` lifecycle fields.
                """
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "__tool_progress__":
                    event_data = json.dumps(item[1])
                    await response.write(
                        f"event: hermes.tool.progress\ndata: {event_data}\n\n".encode()
                    )
                else:
                    content_chunk = {
                        "id": completion_id, "object": "chat.completion.chunk",
                        "created": created, "model": model,
                        "choices": [{"index": 0, "delta": {"content": item}, "finish_reason": None}],
                    }
                    await response.write(f"data: {json.dumps(content_chunk)}\n\n".encode())
                return time.monotonic()

            # Stream content chunks as they arrive from the agent
            loop = asyncio.get_running_loop()
            while True:
                try:
                    delta = await loop.run_in_executor(None, lambda: stream_q.get(timeout=0.5))
                except _q.Empty:
                    if agent_task.done():
                        # Drain any remaining items
                        while True:
                            try:
                                delta = stream_q.get_nowait()
                                if delta is None:
                                    break
                                last_activity = await _emit(delta)
                            except _q.Empty:
                                break
                        break
                    if time.monotonic() - last_activity >= CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS:
                        await response.write(b": keepalive\n\n")
                        last_activity = time.monotonic()
                    continue

                if delta is None:  # End of stream sentinel
                    break

                last_activity = await _emit(delta)

            # Get usage from completed agent
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            error_payload = None
            try:
                result, agent_usage = await agent_task
                usage = agent_usage or usage
            except Exception as exc:
                error_payload = _public_agent_stream_error(str(exc))
                logger.warning(
                    "Agent task %s failed, usage data lost: %s",
                    completion_id,
                    error_payload["message"],
                )

            # Finish chunk
            finish_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "error" if error_payload else "stop"}],
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }
            if error_payload:
                finish_chunk["error"] = error_payload
            await response.write(f"data: {json.dumps(finish_chunk)}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            # Client disconnected mid-stream.  Interrupt the agent so it
            # stops making LLM API calls at the next loop iteration, then
            # cancel the asyncio task wrapper.
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                try:
                    agent.interrupt("SSE client disconnected")
                except Exception:
                    pass
            if not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.info("SSE client disconnected; interrupted agent task %s", completion_id)
        except Exception as _exc:
            # Agent crashed mid-stream.  Try to emit an error chunk
            # so the client gets a proper response instead of a
            # TransferEncodingError from incomplete chunked encoding.
            import traceback as _tb
            logger.error("Agent crashed mid-stream for %s: %s", completion_id, _tb.format_exc()[:300])
            try:
                error_chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
                }
                await response.write(f"data: {json.dumps(error_chunk)}\n\n".encode())
                await response.write(b"data: [DONE]\n\n")
            except Exception:
                pass

        return response

    async def _write_sse_responses(
        self,
        request: "web.Request",
        response_id: str,
        model: str,
        created_at: int,
        stream_q,
        agent_task,
        agent_ref,
        conversation_history: List[Dict[str, str]],
        user_message: str,
        instructions: Optional[str],
        conversation: Optional[str],
        store: bool,
        session_id: str,
        gateway_session_key: Optional[str] = None,
    ) -> "web.StreamResponse":
        """Write an SSE stream for POST /v1/responses (OpenAI Responses API).

        Emits spec-compliant event types as the agent runs:

        - ``response.created`` — initial envelope (status=in_progress)
        - ``response.output_text.delta`` / ``response.output_text.done`` —
          streamed assistant text
        - ``response.output_item.added`` / ``response.output_item.done``
          with ``item.type == "function_call"`` — when the agent invokes a
          tool (both events fire; the ``done`` event carries the finalized
          ``arguments`` string)
        - ``response.output_item.added`` with
          ``item.type == "function_call_output"`` — tool result with
          ``{call_id, output, status}``
        - ``response.completed`` — terminal event carrying the full
          response object with all output items + usage (same payload
          shape as the non-streaming path for parity)
        - ``response.failed`` — terminal event on agent error

        If the client disconnects mid-stream, ``agent.interrupt()`` is
        called so the agent stops issuing upstream LLM calls, then the
        asyncio task is cancelled.  When ``store=True`` an initial
        ``in_progress`` snapshot is persisted immediately after
        ``response.created`` and disconnects update it to an
        ``incomplete`` snapshot so GET /v1/responses/{id} and
        ``previous_response_id`` chaining still have something to
        recover from.
        """
        import queue as _q

        sse_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        origin = request.headers.get("Origin", "")
        cors = self._cors_headers_for_origin(origin) if origin else None
        if cors:
            sse_headers.update(cors)
        if session_id:
            sse_headers["X-Hermes-Session-Id"] = session_id
        if gateway_session_key:
            sse_headers["X-Hermes-Session-Key"] = gateway_session_key
        response = web.StreamResponse(status=200, headers=sse_headers)
        await response.prepare(request)

        # State accumulated during the stream
        final_text_parts: List[str] = []
        # Track open function_call items by name so we can emit a matching
        # ``done`` event when the tool completes.  Order preserved.
        pending_tool_calls: List[Dict[str, Any]] = []
        # Output items we've emitted so far (used to build the terminal
        # response.completed payload).  Kept in the order they appeared.
        emitted_items: List[Dict[str, Any]] = []
        # Monotonic counter for output_index (spec requires it).
        output_index = 0
        # Monotonic counter for call_id generation if the agent doesn't
        # provide one (it doesn't, from tool_progress_callback).
        call_counter = 0
        # Canonical Responses SSE events include a monotonically increasing
        # sequence_number. Add it server-side for every emitted event so
        # clients that validate the OpenAI event schema can parse our stream.
        sequence_number = 0
        # Track the assistant message item id + content index for text
        # delta events — the spec ties deltas to a specific item.
        message_item_id = f"msg_{uuid.uuid4().hex[:24]}"
        message_output_index: Optional[int] = None
        message_opened = False

        async def _write_event(event_type: str, data: Dict[str, Any]) -> None:
            nonlocal sequence_number
            if "sequence_number" not in data:
                data["sequence_number"] = sequence_number
            sequence_number += 1
            payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            await response.write(payload.encode())

        def _envelope(status: str) -> Dict[str, Any]:
            env: Dict[str, Any] = {
                "id": response_id,
                "object": "response",
                "status": status,
                "created_at": created_at,
                "model": model,
            }
            return env

        final_response_text = ""
        agent_error: Optional[str] = None
        usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        terminal_snapshot_persisted = False

        def _persist_response_snapshot(
            response_env: Dict[str, Any],
            *,
            conversation_history_snapshot: Optional[List[Dict[str, Any]]] = None,
        ) -> None:
            if not store:
                return
            if conversation_history_snapshot is None:
                conversation_history_snapshot = list(conversation_history)
                conversation_history_snapshot.append({"role": "user", "content": user_message})
            self._response_store.put(response_id, {
                "response": response_env,
                "conversation_history": conversation_history_snapshot,
                "instructions": instructions,
                "session_id": session_id,
            })
            if conversation:
                self._response_store.set_conversation(conversation, response_id)

        def _persist_incomplete_if_needed() -> None:
            """Persist an ``incomplete`` snapshot if no terminal one was written.

            Called from both the client-disconnect (``ConnectionResetError``)
            and server-cancellation (``asyncio.CancelledError``) paths so
            GET /v1/responses/{id} and ``previous_response_id`` chaining keep
            working after abrupt stream termination.
            """
            if not store or terminal_snapshot_persisted:
                return
            incomplete_text = "".join(final_text_parts) or final_response_text
            incomplete_items: List[Dict[str, Any]] = list(emitted_items)
            if incomplete_text:
                incomplete_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": incomplete_text}],
                })
            incomplete_env = _envelope("incomplete")
            incomplete_env["output"] = incomplete_items
            incomplete_env["usage"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
            incomplete_history = list(conversation_history)
            incomplete_history.append({"role": "user", "content": user_message})
            if incomplete_text:
                incomplete_history.append({"role": "assistant", "content": incomplete_text})
            _persist_response_snapshot(
                incomplete_env,
                conversation_history_snapshot=incomplete_history,
            )

        try:
            # response.created — initial envelope, status=in_progress
            created_env = _envelope("in_progress")
            created_env["output"] = []
            await _write_event("response.created", {
                "type": "response.created",
                "response": created_env,
            })
            _persist_response_snapshot(created_env)
            last_activity = time.monotonic()

            async def _open_message_item() -> None:
                """Emit response.output_item.added for the assistant message
                the first time any text delta arrives."""
                nonlocal message_opened, message_output_index, output_index
                if message_opened:
                    return
                message_opened = True
                message_output_index = output_index
                output_index += 1
                item = {
                    "id": message_item_id,
                    "type": "message",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                }
                await _write_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": message_output_index,
                    "item": item,
                })

            async def _emit_text_delta(delta_text: str) -> None:
                await _open_message_item()
                final_text_parts.append(delta_text)
                await _write_event("response.output_text.delta", {
                    "type": "response.output_text.delta",
                    "item_id": message_item_id,
                    "output_index": message_output_index,
                    "content_index": 0,
                    "delta": delta_text,
                    "logprobs": [],
                })

            async def _emit_tool_started(payload: Dict[str, Any]) -> str:
                """Emit response.output_item.added for a function_call.

                Returns the call_id so the matching completion event can
                reference it.  Prefer the real ``tool_call_id`` from the
                agent when available; fall back to a generated call id for
                safety in tests or older code paths.
                """
                nonlocal output_index, call_counter
                call_counter += 1
                call_id = payload.get("tool_call_id") or f"call_{response_id[5:]}_{call_counter}"
                args = payload.get("arguments", {})
                if isinstance(args, dict):
                    arguments_str = json.dumps(args)
                else:
                    arguments_str = str(args)
                item = {
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "type": "function_call",
                    "status": "in_progress",
                    "name": payload.get("name", ""),
                    "call_id": call_id,
                    "arguments": arguments_str,
                }
                idx = output_index
                output_index += 1
                pending_tool_calls.append({
                    "call_id": call_id,
                    "name": payload.get("name", ""),
                    "arguments": arguments_str,
                    "item_id": item["id"],
                    "output_index": idx,
                })
                emitted_items.append({
                    "type": "function_call",
                    "name": payload.get("name", ""),
                    "arguments": arguments_str,
                    "call_id": call_id,
                })
                await _write_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": idx,
                    "item": item,
                })
                return call_id

            async def _emit_tool_completed(payload: Dict[str, Any]) -> None:
                """Emit response.output_item.done (function_call) followed
                by response.output_item.added (function_call_output)."""
                nonlocal output_index
                call_id = payload.get("tool_call_id")
                result = payload.get("result", "")
                pending = None
                if call_id:
                    for i, p in enumerate(pending_tool_calls):
                        if p["call_id"] == call_id:
                            pending = pending_tool_calls.pop(i)
                            break
                if pending is None:
                    # Completion without a matching start — skip to avoid
                    # emitting orphaned done events.
                    return

                # function_call done
                done_item = {
                    "id": pending["item_id"],
                    "type": "function_call",
                    "status": "completed",
                    "name": pending["name"],
                    "call_id": pending["call_id"],
                    "arguments": pending["arguments"],
                }
                await _write_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": pending["output_index"],
                    "item": done_item,
                })

                # function_call_output added (result)
                result_str = result if isinstance(result, str) else json.dumps(result)
                output_parts = [{"type": "input_text", "text": result_str}]
                output_item = {
                    "id": f"fco_{uuid.uuid4().hex[:24]}",
                    "type": "function_call_output",
                    "call_id": pending["call_id"],
                    "output": output_parts,
                    "status": "completed",
                }
                idx = output_index
                output_index += 1
                emitted_items.append({
                    "type": "function_call_output",
                    "call_id": pending["call_id"],
                    "output": output_parts,
                })
                await _write_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": idx,
                    "item": output_item,
                })
                await _write_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": idx,
                    "item": output_item,
                })

            # Main drain loop — thread-safe queue fed by agent callbacks.
            async def _dispatch(it) -> None:
                """Route a queue item to the correct SSE emitter.

                Plain strings are text deltas — they are batched (50ms)
                to reduce Open WebUI re-render storms.  Tagged tuples
                with ``__tool_started__`` / ``__tool_completed__``
                prefixes are tool lifecycle events and flush the buffer
                before emitting.
                """
                nonlocal _batch_timer
                if isinstance(it, tuple) and len(it) == 2 and isinstance(it[0], str):
                    tag, payload = it
                    # Flush batched text before tool events
                    if _batch_buf:
                        await _flush_batch()
                    if tag == "__tool_started__":
                        await _emit_tool_started(payload)
                    elif tag == "__tool_completed__":
                        await _emit_tool_completed(payload)
                elif isinstance(it, str):
                    # Batch text deltas — append to buffer, flush on timer
                    _batch_buf.append(it)
                    if _batch_timer is None:
                        _batch_timer = asyncio.create_task(_batch_flush_after(0.05))
                # Other types are silently dropped.

            # ── Batching state ──
            _batch_buf: List[str] = []
            _batch_timer: Optional[asyncio.Task] = None
            _batch_lock = asyncio.Lock()

            async def _batch_flush_after(delay: float) -> None:
                """Wait delay seconds, then flush accumulated text deltas."""
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                # Clear timer reference BEFORE flush so new deltas
                # can start a fresh timer while we emit
                nonlocal _batch_buf, _batch_timer
                _batch_timer = None
                await _flush_batch()

            async def _flush_batch() -> None:
                """Emit a single SSE delta for all accumulated text."""
                nonlocal _batch_buf
                async with _batch_lock:
                    if _batch_buf:
                        combined = "".join(_batch_buf)
                        _batch_buf = []
                        await _emit_text_delta(combined)

            loop = asyncio.get_running_loop()
            while True:
                try:
                    item = await loop.run_in_executor(None, lambda: stream_q.get(timeout=0.5))
                except _q.Empty:
                    if agent_task.done():
                        # Drain remaining
                        while True:
                            try:
                                item = stream_q.get_nowait()
                                if item is None:
                                    break
                                await _dispatch(item)
                                last_activity = time.monotonic()
                            except _q.Empty:
                                break
                        break
                    if time.monotonic() - last_activity >= CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS:
                        await response.write(b": keepalive\n\n")
                        last_activity = time.monotonic()
                    continue

                if item is None:  # EOS sentinel
                    # Cancel pending timer and flush remaining batched text
                    if _batch_timer and not _batch_timer.done():
                        _batch_timer.cancel()
                        _batch_timer = None
                    if _batch_buf:
                        await _flush_batch()
                    break

                await _dispatch(item)
                last_activity = time.monotonic()

            # Flush any final batched text before processing result
            if _batch_buf:
                await _flush_batch()

            # Pick up agent result + usage from the completed task
            try:
                result, agent_usage = await agent_task
                usage = agent_usage or usage
                # If the agent produced a final_response but no text
                # deltas were streamed (e.g. some providers only emit
                # the full response at the end), emit a single fallback
                # delta so Responses clients still receive a live text part.
                agent_final = result.get("final_response", "") if isinstance(result, dict) else ""
                if agent_final and not final_text_parts:
                    await _emit_text_delta(agent_final)
                if agent_final and not final_response_text:
                    final_response_text = agent_final
                if isinstance(result, dict) and result.get("error") and not final_response_text:
                    agent_error = result["error"]
            except Exception as e:  # noqa: BLE001
                logger.error("Error running agent for streaming responses: %s", e, exc_info=True)
                agent_error = str(e)

            # Close the message item if it was opened
            final_response_text = "".join(final_text_parts) or final_response_text
            if message_opened:
                await _write_event("response.output_text.done", {
                    "type": "response.output_text.done",
                    "item_id": message_item_id,
                    "output_index": message_output_index,
                    "content_index": 0,
                    "text": final_response_text,
                    "logprobs": [],
                })
                msg_done_item = {
                    "id": message_item_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": final_response_text}
                    ],
                }
                await _write_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": message_output_index,
                    "item": msg_done_item,
                })

            # Always append a final message item in the completed
            # response envelope so clients that only parse the terminal
            # payload still see the assistant text.  This mirrors the
            # shape produced by _extract_output_items in the batch path.
            final_items: List[Dict[str, Any]] = list(emitted_items)

            # Trim large content from tool call arguments to keep the
            # response.completed event under ~100KB.  Clients already
            # received full details via incremental events.
            for _item in final_items:
                if _item.get("type") == "function_call":
                    try:
                        _args = json.loads(_item.get("arguments", "{}")) if isinstance(_item.get("arguments"), str) else _item.get("arguments", {})
                        if isinstance(_args, dict):
                            for _k in ("content", "query", "pattern", "old_string", "new_string"):
                                if isinstance(_args.get(_k), str) and len(_args[_k]) > 500:
                                    _args[_k] = "[" + str(len(_args[_k])) + " chars — truncated for response.completed]"
                            _item["arguments"] = json.dumps(_args)
                    except Exception:
                        pass
                elif _item.get("type") == "function_call_output":
                    _output = _item.get("output", [])
                    if isinstance(_output, list) and _output:
                        _first = _output[0]
                        if isinstance(_first, dict) and _first.get("type") == "input_text":
                            _text = _first.get("text", "")
                            if len(_text) > 1000:
                                _first["text"] = _text[:500] + "...[" + str(len(_text) - 500) + " more chars]"
                                _item["output"] = [_first]

            final_items.append({
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": final_response_text or (agent_error or "")}
                ],
            })

            if agent_error:
                failed_env = _envelope("failed")
                failed_env["output"] = final_items
                failed_env["error"] = {"message": agent_error, "type": "server_error"}
                failed_env["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                _failed_history = list(conversation_history)
                _failed_history.append({"role": "user", "content": user_message})
                if final_response_text or agent_error:
                    _failed_history.append({
                        "role": "assistant",
                        "content": final_response_text or agent_error,
                    })
                _persist_response_snapshot(
                    failed_env,
                    conversation_history_snapshot=_failed_history,
                )
                terminal_snapshot_persisted = True
                await _write_event("response.failed", {
                    "type": "response.failed",
                    "response": failed_env,
                })
            else:
                completed_env = _envelope("completed")
                completed_env["output"] = final_items
                completed_env["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                full_history = self._build_response_conversation_history(
                    conversation_history,
                    user_message,
                    result,
                    final_response_text,
                )
                _persist_response_snapshot(
                    completed_env,
                    conversation_history_snapshot=full_history,
                )
                terminal_snapshot_persisted = True
                await _write_event("response.completed", {
                    "type": "response.completed",
                    "response": completed_env,
                })

        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            _persist_incomplete_if_needed()
            # Client disconnected — interrupt the agent so it stops
            # making upstream LLM calls, then cancel the task.
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                try:
                    agent.interrupt("SSE client disconnected")
                except Exception:
                    pass
            if not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.info("SSE client disconnected; interrupted agent task %s", response_id)
        except asyncio.CancelledError:
            # Server-side cancellation (e.g. shutdown, request timeout) —
            # persist an incomplete snapshot so GET /v1/responses/{id} and
            # previous_response_id chaining still work, then re-raise so the
            # runtime's cancellation semantics are respected.
            _persist_incomplete_if_needed()
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                try:
                    agent.interrupt("SSE task cancelled")
                except Exception:
                    pass
            if not agent_task.done():
                agent_task.cancel()
            logger.info("SSE task cancelled; persisted incomplete snapshot for %s", response_id)
            raise
        except Exception as _exc:
            # Agent crashed with an unhandled error (e.g. model API error like
            # BadRequestError, AuthenticationError).  Emit a response.failed
            # event and properly terminate the SSE stream so the client doesn't
            # get a TransferEncodingError from incomplete chunked encoding.
            import traceback as _tb
            _persist_incomplete_if_needed()
            agent_error = _tb.format_exc()
            try:
                failed_env = _envelope("failed")
                failed_env["output"] = list(emitted_items)
                failed_env["error"] = {"message": str(_exc)[:500], "type": "server_error"}
                failed_env["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                await _write_event("response.failed", {
                    "type": "response.failed",
                    "response": failed_env,
                })
            except Exception:
                pass
            logger.error("Agent crashed mid-stream for %s: %s", response_id, str(agent_error)[:300])

        return response

    async def _handle_responses(self, request: "web.Request") -> "web.Response":
        """POST /v1/responses — OpenAI Responses API format."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        license_err = self._license_guard_response()
        if license_err:
            return license_err

        # Long-term memory scope header (see chat_completions for details).
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        # Parse request body
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": {"message": "Invalid JSON in request body", "type": "invalid_request_error"}},
                status=400,
            )

        raw_input = body.get("input")
        if raw_input is None:
            return web.json_response(_openai_error("Missing 'input' field"), status=400)

        instructions = body.get("instructions")
        previous_response_id = body.get("previous_response_id")
        conversation = body.get("conversation")
        store = _coerce_request_bool(body.get("store"), default=True)

        # conversation and previous_response_id are mutually exclusive
        if conversation and previous_response_id:
            return web.json_response(_openai_error("Cannot use both 'conversation' and 'previous_response_id'"), status=400)

        # Resolve conversation name to latest response_id
        if conversation:
            previous_response_id = self._response_store.get_conversation(conversation)
            # No error if conversation doesn't exist yet — it's a new conversation

        # Normalize input to message list
        input_messages: List[Dict[str, Any]] = []
        if isinstance(raw_input, str):
            input_messages = [{"role": "user", "content": raw_input}]
        elif isinstance(raw_input, list):
            for idx, item in enumerate(raw_input):
                if isinstance(item, str):
                    input_messages.append({"role": "user", "content": item})
                elif isinstance(item, dict):
                    role = item.get("role", "user")
                    try:
                        content = _normalize_multimodal_content(item.get("content", ""))
                    except ValueError as exc:
                        return _multimodal_validation_error(exc, param=f"input[{idx}].content")
                    input_messages.append({"role": role, "content": content})
        else:
            return web.json_response(_openai_error("'input' must be a string or array"), status=400)

        # Accept explicit conversation_history from the request body.
        # This lets stateless clients supply their own history instead of
        # relying on server-side response chaining via previous_response_id.
        # Precedence: explicit conversation_history > previous_response_id.
        conversation_history: List[Dict[str, Any]] = []
        raw_history = body.get("conversation_history")
        if raw_history:
            if not isinstance(raw_history, list):
                return web.json_response(
                    _openai_error("'conversation_history' must be an array of message objects"),
                    status=400,
                )
            for i, entry in enumerate(raw_history):
                if not isinstance(entry, dict) or "role" not in entry or "content" not in entry:
                    return web.json_response(
                        _openai_error(f"conversation_history[{i}] must have 'role' and 'content' fields"),
                        status=400,
                    )
                try:
                    entry_content = _normalize_multimodal_content(entry["content"])
                except ValueError as exc:
                    return _multimodal_validation_error(exc, param=f"conversation_history[{i}].content")
                conversation_history.append({"role": str(entry["role"]), "content": entry_content})
            if previous_response_id:
                logger.debug("Both conversation_history and previous_response_id provided; using conversation_history")

        stored_session_id = None
        if not conversation_history and previous_response_id:
            stored = self._response_store.get(previous_response_id)
            if stored is None:
                return web.json_response(_openai_error(f"Previous response not found: {previous_response_id}"), status=404)
            conversation_history = list(stored.get("conversation_history", []))
            stored_session_id = stored.get("session_id")
            # If no instructions provided, carry forward from previous
            if instructions is None:
                instructions = stored.get("instructions")

        # Append new input messages to history (all but the last become history)
        for msg in input_messages[:-1]:
            conversation_history.append(msg)

        # Last input message is the user_message
        user_message: Any = input_messages[-1].get("content", "") if input_messages else ""
        if not _content_has_visible_payload(user_message):
            return web.json_response(_openai_error("No user message found in input"), status=400)

        # Truncation support
        if body.get("truncation") == "auto" and len(conversation_history) > 100:
            conversation_history = conversation_history[-100:]

        # Reuse session from previous_response_id chain so the dashboard
        # groups the entire conversation under one session entry.
        session_id = stored_session_id or str(uuid.uuid4())

        stream = _coerce_request_bool(body.get("stream"), default=False)
        if stream:
            # Streaming branch — emit OpenAI Responses SSE events as the
            # agent runs so frontends can render text deltas and tool
            # calls in real time.  See _write_sse_responses for details.
            import queue as _q
            _stream_q: _q.Queue = _q.Queue()

            def _on_delta(delta):
                # None from the agent is a CLI box-close signal, not EOS.
                # Forwarding would kill the SSE stream prematurely; the
                # SSE writer detects completion via agent_task.done().
                if delta is not None:
                    _stream_q.put(delta)

            def _on_tool_progress(event_type, name, preview, args, **kwargs):
                """Queue non-start tool progress events if needed in future.

                The structured Responses stream uses ``tool_start_callback``
                and ``tool_complete_callback`` for exact call-id correlation,
                so progress events are currently ignored here.
                """
                return

            def _on_tool_start(tool_call_id, function_name, function_args):
                """Queue a started tool for live function_call streaming."""
                _stream_q.put(("__tool_started__", {
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "arguments": function_args or {},
                }))

            def _on_tool_complete(tool_call_id, function_name, function_args, function_result):
                """Queue a completed tool result for live function_call_output streaming."""
                _stream_q.put(("__tool_completed__", {
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "arguments": function_args or {},
                    "result": function_result,
                }))

            agent_ref = [None]
            agent_task = asyncio.ensure_future(self._run_agent(
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=instructions,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                tool_progress_callback=_on_tool_progress,
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                agent_ref=agent_ref,
                gateway_session_key=gateway_session_key,
            ))
            # Ensure SSE drain loops can terminate without relying on polling
            # agent_task.done(), which can race with queue timeout checks.
            agent_task.add_done_callback(lambda _fut: _stream_q.put(None))

            response_id = f"resp_{uuid.uuid4().hex[:28]}"
            model_name = body.get("model", self._model_name)
            created_at = int(time.time())

            return await self._write_sse_responses(
                request=request,
                response_id=response_id,
                model=model_name,
                created_at=created_at,
                stream_q=_stream_q,
                agent_task=agent_task,
                agent_ref=agent_ref,
                conversation_history=conversation_history,
                user_message=user_message,
                instructions=instructions,
                conversation=conversation,
                store=store,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
            )

        async def _compute_response():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=instructions,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            fp = _make_request_fingerprint(
                body,
                keys=["input", "instructions", "previous_response_id", "conversation", "model", "tools"],
            )
            try:
                result, usage = await _idem_cache.get_or_set(idempotency_key, fp, _compute_response)
            except Exception as e:
                logger.error("Error running agent for responses: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )
        else:
            try:
                result, usage = await _compute_response()
            except Exception as e:
                logger.error("Error running agent for responses: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )

        final_response = result.get("final_response", "")
        if not final_response:
            final_response = result.get("error", "(No response generated)")

        response_id = f"resp_{uuid.uuid4().hex[:28]}"
        created_at = int(time.time())

        # Build the full conversation history for storage
        # (includes tool calls from the agent run)
        full_history = self._build_response_conversation_history(
            conversation_history,
            user_message,
            result,
            final_response,
        )

        # Build output items from the current turn only.  AIAgent returns a
        # full transcript in result["messages"], while older/mocked paths may
        # return only the current turn suffix.
        output_start_index = self._response_messages_turn_start_index(
            conversation_history,
            user_message,
            result,
        )
        output_items = self._extract_output_items(result, start_index=output_start_index)

        response_data = {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "created_at": created_at,
            "model": body.get("model", self._model_name),
            "output": output_items,
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

        # Store the complete response object for future chaining / GET retrieval
        if store:
            self._response_store.put(response_id, {
                "response": response_data,
                "conversation_history": full_history,
                "instructions": instructions,
                "session_id": session_id,
            })
            # Update conversation mapping so the next request with the same
            # conversation name automatically chains to this response
            if conversation:
                self._response_store.set_conversation(conversation, response_id)

        response_headers = {"X-Hermes-Session-Id": session_id}
        if gateway_session_key:
            response_headers["X-Hermes-Session-Key"] = gateway_session_key
        return web.json_response(response_data, headers=response_headers)

    # ------------------------------------------------------------------
    # GET / DELETE response endpoints
    # ------------------------------------------------------------------

    async def _handle_get_response(self, request: "web.Request") -> "web.Response":
        """GET /v1/responses/{response_id} — retrieve a stored response."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        response_id = request.match_info["response_id"]
        stored = self._response_store.get(response_id)
        if stored is None:
            return web.json_response(_openai_error(f"Response not found: {response_id}"), status=404)

        return web.json_response(stored["response"])

    async def _handle_delete_response(self, request: "web.Request") -> "web.Response":
        """DELETE /v1/responses/{response_id} — delete a stored response."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        response_id = request.match_info["response_id"]
        deleted = self._response_store.delete(response_id)
        if not deleted:
            return web.json_response(_openai_error(f"Response not found: {response_id}"), status=404)

        return web.json_response({
            "id": response_id,
            "object": "response",
            "deleted": True,
        })

    # ------------------------------------------------------------------
    # Cron jobs API
    # ------------------------------------------------------------------

    _JOB_ID_RE = __import__("re").compile(r"[a-f0-9]{12}")
    # Allowed fields for update — prevents clients injecting arbitrary keys
    _UPDATE_ALLOWED_FIELDS = {"name", "schedule", "prompt", "deliver", "skills", "skill", "repeat", "enabled"}
    _MAX_NAME_LENGTH = 200
    _MAX_PROMPT_LENGTH = 5000

    @staticmethod
    def _check_jobs_available() -> Optional["web.Response"]:
        """Return error response if cron module isn't available."""
        if not _CRON_AVAILABLE:
            return web.json_response(
                {"error": "Cron module not available"}, status=501,
            )
        return None

    def _check_job_id(self, request: "web.Request") -> tuple:
        """Validate and extract job_id. Returns (job_id, error_response)."""
        job_id = request.match_info["job_id"]
        if not self._JOB_ID_RE.fullmatch(job_id):
            logger.warning(
                "Cron jobs API rejected invalid job_id %r: %s",
                job_id,
                self._request_audit_log_suffix(request),
            )
            return job_id, web.json_response(
                {"error": "Invalid job ID format"}, status=400,
            )
        return job_id, None

    async def _handle_list_jobs(self, request: "web.Request") -> "web.Response":
        """GET /api/jobs — list all cron jobs."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        license_err = self._license_guard_response()
        if license_err:
            return license_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        try:
            include_disabled = request.query.get("include_disabled", "").lower() in {"true", "1"}
            jobs = _cron_list(include_disabled=include_disabled)
            return web.json_response({"jobs": jobs})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_create_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs — create a new cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        try:
            body = await request.json()
            name = (body.get("name") or "").strip()
            schedule = (body.get("schedule") or "").strip()
            prompt = body.get("prompt", "")
            deliver = body.get("deliver", "local")
            skills = body.get("skills")
            repeat = body.get("repeat")

            if not name:
                return web.json_response({"error": "Name is required"}, status=400)
            if len(name) > self._MAX_NAME_LENGTH:
                return web.json_response(
                    {"error": f"Name must be ≤ {self._MAX_NAME_LENGTH} characters"}, status=400,
                )
            if not schedule:
                return web.json_response({"error": "Schedule is required"}, status=400)
            if len(prompt) > self._MAX_PROMPT_LENGTH:
                return web.json_response(
                    {"error": f"Prompt must be ≤ {self._MAX_PROMPT_LENGTH} characters"}, status=400,
                )
            if repeat is not None and (not isinstance(repeat, int) or repeat < 1):
                return web.json_response({"error": "Repeat must be a positive integer"}, status=400)

            kwargs = {
                "prompt": prompt,
                "schedule": schedule,
                "name": name,
                "deliver": deliver,
                "origin": self._cron_origin_from_request(request),
            }
            if skills:
                kwargs["skills"] = skills
            if repeat is not None:
                kwargs["repeat"] = repeat

            job = _cron_create(**kwargs)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_get_job(self, request: "web.Request") -> "web.Response":
        """GET /api/jobs/{job_id} — get a single cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_get(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_update_job(self, request: "web.Request") -> "web.Response":
        """PATCH /api/jobs/{job_id} — update a cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            body = await request.json()
            # Whitelist allowed fields to prevent arbitrary key injection
            sanitized = {k: v for k, v in body.items() if k in self._UPDATE_ALLOWED_FIELDS}
            if not sanitized:
                return web.json_response({"error": "No valid fields to update"}, status=400)
            # Validate lengths if present
            if "name" in sanitized and len(sanitized["name"]) > self._MAX_NAME_LENGTH:
                return web.json_response(
                    {"error": f"Name must be ≤ {self._MAX_NAME_LENGTH} characters"}, status=400,
                )
            if "prompt" in sanitized and len(sanitized["prompt"]) > self._MAX_PROMPT_LENGTH:
                return web.json_response(
                    {"error": f"Prompt must be ≤ {self._MAX_PROMPT_LENGTH} characters"}, status=400,
                )
            job = _cron_update(job_id, sanitized)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_delete_job(self, request: "web.Request") -> "web.Response":
        """DELETE /api/jobs/{job_id} — delete a cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            success = _cron_remove(job_id)
            if not success:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_pause_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/pause — pause a cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_pause(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_resume_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/resume — resume a paused cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_resume(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_run_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/run — trigger immediate execution."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_trigger(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ------------------------------------------------------------------
    # Output extraction helper
    # ------------------------------------------------------------------

    @staticmethod
    def _build_response_conversation_history(
        conversation_history: List[Dict[str, Any]],
        user_message: Any,
        result: Dict[str, Any],
        final_response: Any,
    ) -> List[Dict[str, Any]]:
        """Build the stored Responses transcript without duplicating history."""
        prior = list(conversation_history)
        current_user = {"role": "user", "content": user_message}
        agent_messages = result.get("messages") if isinstance(result, dict) else None

        if isinstance(agent_messages, list) and agent_messages:
            turn_start = APIServerAdapter._response_messages_turn_start_index(
                conversation_history,
                user_message,
                result,
            )
            if turn_start:
                return list(agent_messages)

            full_history = prior
            full_history.append(current_user)
            full_history.extend(agent_messages)
            return full_history

        full_history = prior
        full_history.append(current_user)
        full_history.append({"role": "assistant", "content": final_response})
        return full_history

    @staticmethod
    def _response_messages_turn_start_index(
        conversation_history: List[Dict[str, Any]],
        user_message: Any,
        result: Dict[str, Any],
    ) -> int:
        """Detect transcript-shaped result["messages"] and return turn start."""
        agent_messages = result.get("messages") if isinstance(result, dict) else None
        if not isinstance(agent_messages, list) or not agent_messages:
            return 0

        prior = list(conversation_history)
        current_user = {"role": "user", "content": user_message}
        expected_prefix = prior + [current_user]
        if agent_messages[:len(expected_prefix)] == expected_prefix:
            return len(expected_prefix)
        if prior and agent_messages[:len(prior)] == prior:
            return len(prior)
        return 0

    @staticmethod
    def _extract_output_items(result: Dict[str, Any], start_index: int = 0) -> List[Dict[str, Any]]:
        """
        Build the output item array from the agent's messages.

        Walks *result["messages"]* starting at *start_index* and emits:
        - ``function_call`` items for each tool_call on assistant messages
        - ``function_call_output`` items for each tool-role message
        - a final ``message`` item with the assistant's text reply
        """
        items: List[Dict[str, Any]] = []
        messages = result.get("messages", [])
        if start_index > 0:
            messages = messages[start_index:]

        for msg in messages:
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    items.append({
                        "type": "function_call",
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", ""),
                        "call_id": tc.get("id", ""),
                    })
            elif role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": msg.get("content", ""),
                })

        # Final assistant message
        final = result.get("final_response", "")
        if not final:
            final = result.get("error", "(No response generated)")

        items.append({
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": final,
                }
            ],
        })
        return items

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        agent_ref: Optional[list] = None,
        gateway_session_key: Optional[str] = None,
        persist_user_platform_message_id: Optional[str] = None,
    ) -> tuple:
        """
        Create an agent and run a conversation in a thread executor.

        Returns ``(result_dict, usage_dict)`` where *usage_dict* contains
        ``input_tokens``, ``output_tokens`` and ``total_tokens``.

        If *agent_ref* is a one-element list, the AIAgent instance is stored
        at ``agent_ref[0]`` before ``run_conversation`` begins.  This allows
        callers (e.g. the SSE writer) to call ``agent.interrupt()`` from
        another thread to stop in-progress LLM calls.
        """
        loop = asyncio.get_running_loop()

        def _run():
            agent = self._create_agent(
                ephemeral_system_prompt=ephemeral_system_prompt,
                session_id=session_id,
                stream_delta_callback=stream_delta_callback,
                tool_progress_callback=tool_progress_callback,
                tool_start_callback=tool_start_callback,
                tool_complete_callback=tool_complete_callback,
                gateway_session_key=gateway_session_key,
            )
            if agent_ref is not None:
                agent_ref[0] = agent
            effective_task_id = session_id or str(uuid.uuid4())
            conversation_kwargs = {
                "user_message": user_message,
                "conversation_history": conversation_history,
                "task_id": effective_task_id,
            }
            if persist_user_platform_message_id:
                conversation_kwargs["persist_user_platform_message_id"] = (
                    persist_user_platform_message_id
                )
            result = agent.run_conversation(
                **conversation_kwargs,
            )
            usage = {
                "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
            }
            # Include the effective session ID in the result so callers
            # (e.g. X-Hermes-Session-Id header) can track compression-
            # triggered session rotations. (#16938)
            _eff_sid = getattr(agent, "session_id", session_id)
            if isinstance(_eff_sid, str) and _eff_sid:
                result["session_id"] = _eff_sid
            return result, usage

        return await loop.run_in_executor(None, _run)

    # ------------------------------------------------------------------
    # /v1/runs — structured event streaming
    # ------------------------------------------------------------------

    _MAX_CONCURRENT_RUNS = 10  # Prevent unbounded resource allocation
    _MAX_RETAINED_RUN_STREAMS = 100  # Bound unconsumed SSE result memory
    _RUN_STREAM_TTL = 300  # seconds before orphaned runs are swept
    _RUN_STATUS_TTL = 3600  # seconds to retain terminal run status for polling
    _MANAGED_RUN_LEASE_SECONDS = 30.0
    _MANAGED_RUN_LEASE_HEARTBEAT_SECONDS = 5.0

    def _try_reserve_run_admission(self) -> Optional[str]:
        """Atomically reserve one run slot on the adapter event loop."""
        if len(self._run_admission_reservations) >= self._MAX_CONCURRENT_RUNS:
            return None
        token = uuid.uuid4().hex
        # There is deliberately no await between the capacity check and add.
        self._run_admission_reservations.add(token)
        return token

    def _release_run_admission(self, token: str) -> None:
        self._run_admission_reservations.discard(token)

    def _begin_managed_lease_lifecycle(
        self,
        admission_token: str,
    ) -> Optional[_ManagedLeaseLifecycle]:
        """Register acquire ownership atomically unless shutdown is active."""
        if self._managed_lease_shutdown_depth:
            return None
        lifecycle = _ManagedLeaseLifecycle(
            asyncio.get_running_loop(),
            admission_token=admission_token,
            epoch=self._managed_lease_shutdown_epoch,
        )
        self._managed_lease_lifecycle_tasks.add(lifecycle)
        return lifecycle

    def _finish_managed_lease_lifecycle(
        self,
        lifecycle: _ManagedLeaseLifecycle,
        *,
        release_admission: bool,
    ) -> None:
        if lifecycle.completed:
            return
        lifecycle.completed = True
        self._managed_lease_lifecycle_tasks.discard(lifecycle)
        if release_admission:
            self._release_run_admission(lifecycle.admission_token)
        if not lifecycle.done.done():
            lifecycle.done.set_result(None)

    def _enter_managed_lease_shutdown(self) -> None:
        """Close the acquire gate and mark every pre-worker owner for cleanup."""
        self._managed_lease_shutdown_depth += 1
        self._managed_lease_shutdown_epoch += 1
        for lifecycle in tuple(self._managed_lease_lifecycle_tasks):
            lifecycle.shutdown_requested = True

    def _exit_managed_lease_shutdown(self) -> None:
        self._managed_lease_shutdown_depth -= 1

    @staticmethod
    async def _await_task_without_forwarding_cancel(
        task: "asyncio.Future",
    ) -> Any:
        """Await one owned child without forwarding caller cancellation."""
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A shutdown caller may itself be cancelled repeatedly.  The
                # durable lease child still owns admission and must finish.
                continue
        return task.result()

    def _create_owned_task(
        self,
        coroutine: Any,
        *,
        name: str,
    ) -> "asyncio.Task":
        """Create a real Task or leave the coroutine safely unowned."""
        try:
            candidate = asyncio.get_running_loop().create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise
        if not isinstance(candidate, asyncio.Task):
            try:
                candidate.cancel()
            except BaseException as exc:
                logger.error(
                    "[api_server] could not cancel non-Task factory result "
                    "for %s: %s",
                    name,
                    exc,
                )
            finally:
                coroutine.close()
            raise TypeError(
                "task factory returned a non-Task result"
            )
        try:
            candidate.set_name(name)
        except BaseException as exc:
            # A real Task already owns the coroutine.  Naming failure must not
            # start a second cleanup owner or close the live coroutine.
            logger.error(
                "[api_server] could not name owned task %s: %s",
                name,
                exc,
            )
        return candidate

    async def _await_preworker_owned_task(
        self,
        task: "asyncio.Task",
        *,
        admission_token: str,
        admission_state: Dict[str, Any],
        operation: str,
    ) -> Any:
        """Shield pre-worker work and transfer admission after request cancel."""
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as request_cancel:
            # ``to_thread`` keeps running after its request is cancelled.
            # Transfer admission to its real Task so cancellation storms
            # cannot exceed the global pre-worker capacity.
            def _release_after_preworker(
                finished_task: "asyncio.Task",
            ) -> None:
                try:
                    finished_task.result()
                except BaseException:
                    # The request that could report this failure no longer
                    # exists.  Consume it before releasing the exact slot.
                    pass
                finally:
                    self._release_run_admission(admission_token)

            try:
                task.add_done_callback(_release_after_preworker)
            except BaseException as exc:
                # A real Task already owns the thread-backed operation, but
                # callback registration did not transfer admission.  Retain
                # the current request as owner through repeated cancellation.
                logger.error(
                    "[api_server] %s admission handoff failed: %s",
                    operation,
                    exc,
                )
                try:
                    await self._await_task_without_forwarding_cancel(task)
                except BaseException:
                    pass
                raise request_cancel
            admission_state["preworker_owned"] = True
            raise

    def _register_managed_lease_lifecycle(
        self,
        coroutine: Any,
        *,
        lifecycle: _ManagedLeaseLifecycle,
        name: str,
    ) -> "asyncio.Task":
        """Atomically hand a pre-registered owner to its supervisor Task."""
        task = self._create_owned_task(coroutine, name=name)
        lifecycle.supervisor_task = task

        def _consume(finished_task: "asyncio.Task") -> None:
            try:
                finished_task.result()
            except BaseException as exc:
                # Supervisors normally consume their own failures.  Keep this
                # final guard so no lifecycle exception becomes unhandled.
                logger.error(
                    "[api_server] managed lease lifecycle task failed: %s",
                    exc,
                )

        try:
            task.add_done_callback(_consume)
        except BaseException as exc:
            # The Task is already live and the supervisor consumes all child
            # failures itself.  Keep that single owner instead of installing a
            # duplicate exact-release fallback.
            logger.error(
                "[api_server] could not register managed lease task callback: %s",
                exc,
            )
        return task

    async def _start_managed_lease_cleanup(
        self,
        *,
        lifecycle: _ManagedLeaseLifecycle,
        acquire_task: "asyncio.Task",
        db: Any,
        lease_session_id: str,
        owner_id: str,
        run_id: str,
    ) -> None:
        """Transfer cleanup to a supervisor or retain it in this request."""
        if lifecycle.cleanup_started:
            return
        lifecycle.acquire_task = acquire_task
        supervisor_coroutine = self._supervise_cancelled_managed_lease_acquire(
            lifecycle=lifecycle,
            acquire_task=acquire_task,
            db=db,
            lease_session_id=lease_session_id,
            owner_id=owner_id,
            run_id=run_id,
        )
        try:
            self._register_managed_lease_lifecycle(
                supervisor_coroutine,
                lifecycle=lifecycle,
                name=f"managed-run-lease-supervisor-{run_id[-8:]}",
            )
        except BaseException as exc:
            # _register only raises before a real Task takes ownership.  Keep
            # the current request Task as the sole owner and drain the exact
            # acquire/release pair even if that request is cancelled again.
            supervisor_coroutine.close()
            lifecycle.cleanup_started = True
            current_task = asyncio.current_task()
            if isinstance(current_task, asyncio.Task):
                lifecycle.supervisor_task = current_task
            logger.error(
                "[api_server] managed lease supervisor unavailable for "
                "%s/%s; request retains cleanup ownership: %s",
                lease_session_id,
                run_id,
                exc,
            )
            await self._supervise_cancelled_managed_lease_acquire(
                lifecycle=lifecycle,
                acquire_task=acquire_task,
                db=db,
                lease_session_id=lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )
            return
        lifecycle.cleanup_started = True

    async def _supervise_cancelled_managed_lease_acquire(
        self,
        *,
        lifecycle: _ManagedLeaseLifecycle,
        acquire_task: "asyncio.Task",
        db: Any,
        lease_session_id: str,
        owner_id: str,
        run_id: str,
    ) -> None:
        """Drain a cancelled request's acquire and any exact lease release."""
        try:
            acquired = await self._await_task_without_forwarding_cancel(
                acquire_task
            )
            if not acquired:
                return

            release_coroutine = asyncio.to_thread(
                db.release_managed_run_lease,
                lease_session_id,
                owner_id=owner_id,
                run_id=run_id,
            )
            try:
                release_task = self._create_owned_task(
                    release_coroutine,
                    name=f"managed-run-lease-release-{run_id[-8:]}",
                )
            except BaseException as exc:
                logger.error(
                    "[api_server] cancelled managed lease release task "
                    "unavailable for %s/%s; retaining ownership through "
                    "executor Future: %s",
                    lease_session_id,
                    run_id,
                    exc,
                )
                try:
                    release_future = asyncio.get_running_loop().run_in_executor(
                        None,
                        partial(
                            db.release_managed_run_lease,
                            lease_session_id,
                            owner_id=owner_id,
                            run_id=run_id,
                        ),
                    )
                except BaseException as fallback_exc:
                    # A hard event-loop/executor failure leaves the durable row
                    # fail-closed until TTL.
                    logger.error(
                        "[api_server] cancelled managed lease exact release "
                        "executor unavailable for %s/%s: %s",
                        lease_session_id,
                        run_id,
                        fallback_exc,
                    )
                    return
                released = await self._await_task_without_forwarding_cancel(
                    release_future
                )
            else:
                released = await self._await_task_without_forwarding_cancel(
                    release_task
                )
            if not released:
                # Fail closed: the durable row remains until TTL.
                logger.error(
                    "[api_server] cancelled managed lease acquire cleanup "
                    "lost ownership for %s/%s",
                    lease_session_id,
                    run_id,
                )
        except BaseException as exc:
            # Acquire/release failures have no HTTP consumer after request
            # cancellation.  The durable database remains authoritative.
            logger.error(
                "[api_server] cancelled managed lease lifecycle failed "
                "for %s/%s: %s",
                lease_session_id,
                run_id,
                exc,
            )
        finally:
            self._finish_managed_lease_lifecycle(
                lifecycle,
                release_admission=True,
            )

    async def _drain_managed_lease_lifecycles(self) -> None:
        """Wait for all owners registered before the shutdown barrier."""
        while self._managed_lease_lifecycle_tasks:
            lifecycles = tuple(self._managed_lease_lifecycle_tasks)
            waiter = asyncio.gather(
                *(lifecycle.done for lifecycle in lifecycles),
                return_exceptions=True,
            )
            await self._await_task_without_forwarding_cancel(waiter)

    async def cancel_background_tasks(self) -> None:
        """Cancel ordinary work, then drain durable lease lifecycles."""
        self._enter_managed_lease_shutdown()
        try:
            try:
                try:
                    from tools.approval import unregister_gateway_notify

                    for run_id, approval_session_key in tuple(
                        self._run_approval_sessions.items()
                    ):
                        if unregister_gateway_notify(approval_session_key):
                            self._approval_cancelled_run_ids.add(run_id)
                except Exception:
                    pass
                for run_id in tuple(self._active_run_tasks):
                    self._interrupt_run_agent_once(
                        run_id,
                        "API server shutting down",
                    )
                await super().cancel_background_tasks()
            finally:
                await self._drain_managed_lease_lifecycles()
        finally:
            self._exit_managed_lease_shutdown()

    @staticmethod
    def _managed_history_projection(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Project history onto fields that participate in managed continuity.

        state.db is authoritative.  Request-only metadata such as timestamps,
        model names, reasoning, and UI fields must not manufacture a conflict.
        Tool linkage remains part of the comparison because dropping it changes
        the provider-visible conversation.
        """
        projected: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            item: Dict[str, Any] = {
                "role": str(message.get("role") or ""),
                "content": message.get("content"),
            }
            for key in ("tool_calls", "tool_call_id", "tool_name"):
                if message.get(key) is not None:
                    item[key] = message.get(key)
            projected.append(item)
        return projected

    @classmethod
    def _managed_history_matches(
        cls,
        submitted: List[Dict[str, Any]],
        stored: List[Dict[str, Any]],
    ) -> bool:
        submitted_bytes = json.dumps(
            cls._managed_history_projection(submitted),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        stored_bytes = json.dumps(
            cls._managed_history_projection(stored),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.compare_digest(
            hashlib.sha256(submitted_bytes).digest(),
            hashlib.sha256(stored_bytes).digest(),
        )

    @staticmethod
    def _managed_content_digest(content: Any) -> Optional[bytes]:
        """Return a provider-shape digest for one managed user message."""
        try:
            normalized = _normalize_multimodal_content(content)
            encoded = json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        return hashlib.sha256(encoded).digest()

    @classmethod
    def _managed_provider_history(
        cls,
        stored_history: List[Dict[str, Any]],
        *,
        platform_message_id: Optional[str],
        checkpoint_content: Any,
    ) -> tuple[List[Dict[str, Any]], bool]:
        """Remove only the exact current checkpoint from provider history.

        WebUI durably writes the accepted user turn before starting a managed
        run.  state.db remains authoritative and unchanged; this boundary only
        prevents that checkpoint from being sent once as restored history and
        again as the run's current input.  ``checkpoint_content`` is the
        display-layer content used only for this comparison; prepared provider
        input remains separate.
        """
        history = list(stored_history)
        if not platform_message_id:
            return history, False

        matches = [
            (index, message)
            for index, message in enumerate(history)
            if isinstance(message, dict)
            and _normalized_platform_message_id(
                message.get("message_id") or message.get("platform_message_id")
            )
            == platform_message_id
        ]
        if not matches:
            return history, False
        if len(matches) != 1:
            return history, True

        checkpoint_index, checkpoint = matches[0]
        if (
            str(checkpoint.get("role") or "") != "user"
            or checkpoint_index != len(history) - 1
        ):
            return history, True

        checkpoint_digest = cls._managed_content_digest(checkpoint.get("content"))
        input_digest = cls._managed_content_digest(checkpoint_content)
        if (
            checkpoint_digest is None
            or input_digest is None
            or not hmac.compare_digest(checkpoint_digest, input_digest)
        ):
            return history, True
        return history[:checkpoint_index], False

    def _set_run_status(self, run_id: str, status: str, **fields: Any) -> Dict[str, Any]:
        """Update pollable run status without exposing private agent objects."""
        now = time.time()
        current = self._run_statuses.get(run_id, {})
        current.update({
            "object": "hermes.run",
            "run_id": run_id,
            "status": status,
            "updated_at": now,
        })
        current.setdefault("created_at", fields.pop("created_at", now))
        current.update(fields)
        self._run_statuses[run_id] = current
        return current

    def _interrupt_run_agent_once(self, run_id: str, reason: str) -> None:
        """Deliver one best-effort interrupt across racing cancel paths."""
        agent = self._active_run_agents.get(run_id)
        if agent is None or run_id in self._interrupted_run_ids:
            return
        self._interrupted_run_ids.add(run_id)
        try:
            agent.interrupt(reason)
        except Exception:
            pass

    def _queue_run_event(
        self,
        run_id: str,
        loop: "asyncio.AbstractEventLoop",
        event: Dict[str, Any],
    ) -> None:
        self._set_run_status(
            run_id,
            self._run_statuses.get(run_id, {}).get("status", "running"),
            last_event=event.get("event"),
        )
        q = self._run_streams.get(run_id)
        if q is None:
            return
        try:
            loop.call_soon_threadsafe(q.put_nowait, event)
        except Exception:
            pass

    def _make_run_event_callback(self, run_id: str, loop: "asyncio.AbstractEventLoop"):
        """Return the progress callback for non-tool run events.

        Tool lifecycle uses AIAgent's ID-bearing start/complete callbacks below;
        forwarding progress tool events too would duplicate every tool card.
        """

        def _callback(event_type: str, tool_name: str = None, preview: str = None, args=None, **kwargs):
            ts = time.time()
            if event_type == "reasoning.available":
                self._queue_run_event(run_id, loop, {
                    "event": "reasoning.available",
                    "run_id": run_id,
                    "timestamp": ts,
                    "text": preview or "",
                })
            # _thinking and subagent_progress are intentionally not forwarded

        return _callback

    def _make_run_tool_callbacks(
        self,
        run_id: str,
        loop: "asyncio.AbstractEventLoop",
    ):
        """Return ID-stable callbacks for concurrent tool lifecycle events."""
        started_at: Dict[str, float] = {}
        started_lock = threading.Lock()

        def _start(tool_call_id: str, tool_name: str, args: Any) -> None:
            call_id = str(tool_call_id or "")
            started = time.time()
            with started_lock:
                started_at[call_id] = started
            normalized_args = args if isinstance(args, dict) else {}
            try:
                preview = f"{tool_name}: {json.dumps(normalized_args, ensure_ascii=False)[:240]}"
            except Exception:
                preview = str(tool_name or "")
            self._queue_run_event(run_id, loop, {
                "event": "tool.started",
                "run_id": run_id,
                "timestamp": started,
                "tool_call_id": call_id,
                "tool": tool_name,
                "preview": preview,
                "args": normalized_args,
            })

        def _complete(tool_call_id: str, tool_name: str, args: Any, result: Any) -> None:
            call_id = str(tool_call_id or "")
            completed = time.time()
            with started_lock:
                started = started_at.pop(call_id, completed)
            try:
                from agent.display import _detect_tool_failure

                is_error = bool(_detect_tool_failure(tool_name, result)[0])
            except Exception:
                is_error = False
            event = {
                "event": "tool.completed",
                "run_id": run_id,
                "timestamp": completed,
                "tool_call_id": call_id,
                "toolCallId": call_id,
                "tool": tool_name,
                "duration": round(max(0.0, completed - started), 3),
                "error": is_error,
            }
            structured_result = _structured_tool_result_for_gateway(tool_name, result)
            if structured_result is not None:
                event["structured_result"] = structured_result
            self._queue_run_event(run_id, loop, event)

        return _start, _complete

    async def _handle_runs(self, request: "web.Request") -> "web.Response":
        """POST /v1/runs with request-scoped admission cleanup."""
        admission_state: Dict[str, Any] = {
            "token": None,
            "worker_owned": False,
            "preworker_owned": False,
            "lease_acquire_owned": False,
            "lease_lifecycle": None,
        }
        try:
            return await self._handle_runs_impl(request, admission_state)
        finally:
            lease_lifecycle = admission_state["lease_lifecycle"]
            if (
                lease_lifecycle is not None
                and not lease_lifecycle.completed
                and not lease_lifecycle.cleanup_started
            ):
                self._finish_managed_lease_lifecycle(
                    lease_lifecycle,
                    release_admission=True,
                )
            admission_token = admission_state["token"]
            if (
                admission_token
                and not admission_state["worker_owned"]
                and not admission_state["preworker_owned"]
                and not admission_state["lease_acquire_owned"]
            ):
                self._release_run_admission(admission_token)

    async def _handle_runs_impl(
        self,
        request: "web.Request",
        admission_state: Dict[str, Any],
    ) -> "web.Response":
        """POST /v1/runs — start an agent run, return run_id immediately."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        license_err = self._license_guard_response()
        if license_err:
            return license_err

        # Long-term memory scope header (see chat_completions for details).
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        try:
            body = await request.json()
        except Exception:
            return web.json_response(_openai_error("Invalid JSON"), status=400)
        if not isinstance(body, dict):
            return web.json_response(_openai_error("Request body must be a JSON object"), status=400)

        try:
            requested_model = _validated_run_route_value(body, "model")
            requested_provider = _validated_run_route_value(body, "provider")
        except ValueError as exc:
            field = str(exc)
            return web.json_response(
                _openai_error(
                    f"{field} must be a non-empty string of at most 512 "
                    "characters without control characters",
                    param=field,
                    code=f"invalid_{field}",
                ),
                status=400,
            )
        header_session_id = request.headers.get("X-Hermes-Session-Id", "").strip()
        raw_body_session_id = body.get("session_id")
        body_session_id = str(raw_body_session_id).strip() if raw_body_session_id else ""
        if header_session_id and body_session_id and header_session_id != body_session_id:
            return web.json_response(
                _openai_error(
                    "Header and body session IDs do not match",
                    code="session_id_conflict",
                ),
                status=400,
            )

        raw_input = body.get("input")
        if raw_input is None:
            return web.json_response(_openai_error("Missing 'input' field"), status=400)

        input_history: List[Dict[str, Any]] = []
        try:
            if isinstance(raw_input, str):
                user_message = _normalize_multimodal_content(raw_input)
            elif isinstance(raw_input, list):
                # WebUI sends the current multimodal content array directly;
                # OpenAI-style clients may instead send an array of message
                # objects.  Distinguish the two shapes before normalization.
                content_parts = bool(raw_input) and all(
                    isinstance(item, dict)
                    and "type" in item
                    and "role" not in item
                    and "content" not in item
                    for item in raw_input
                )
                if content_parts:
                    user_message = _normalize_multimodal_content(raw_input)
                else:
                    input_messages: List[Dict[str, Any]] = []
                    for idx, item in enumerate(raw_input):
                        if isinstance(item, str):
                            input_messages.append({
                                "role": "user",
                                "content": _normalize_multimodal_content(item),
                            })
                            continue
                        if not isinstance(item, dict):
                            return web.json_response(
                                _openai_error(f"input[{idx}] must be a string or message object"),
                                status=400,
                            )
                        if "role" not in item or "content" not in item:
                            return web.json_response(
                                _openai_error(
                                    f"input[{idx}] must have 'role' and 'content' fields"
                                ),
                                status=400,
                            )
                        normalized_message = _normalized_conversation_message(
                            item,
                            _normalize_multimodal_content(item["content"]),
                        )
                        input_messages.append(normalized_message)
                    input_history = input_messages[:-1]
                    user_message = input_messages[-1].get("content", "") if input_messages else ""
            else:
                return web.json_response(
                    _openai_error("'input' must be a string or array"),
                    status=400,
                )
        except ValueError as exc:
            return _multimodal_validation_error(exc, param="input")
        if not _content_has_visible_payload(user_message):
            return web.json_response(_openai_error("No user message found in input"), status=400)

        try:
            requested_model, requested_provider = _normalized_explicit_run_route(
                body,
                requested_model,
                requested_provider,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "invalid_model_selector":
                message = (
                    "Qualified model selector must match the selected provider "
                    "and include a model name"
                )
                param = "model"
            else:
                code = "incomplete_model_route"
                message = (
                    "Explicit run routing requires both a concrete model and provider"
                )
                param = None
            return web.json_response(
                _openai_error(message, code=code, param=param),
                status=400,
            )

        instructions = body.get("instructions")
        platform_message_id, platform_message_id_err = (
            _parse_request_platform_message_id(body)
        )
        if platform_message_id_err is not None:
            return platform_message_id_err
        checkpoint_content = (
            body.get("checkpoint_content")
            if "checkpoint_content" in body
            else user_message
        )
        previous_response_id = body.get("previous_response_id")

        # Accept explicit conversation_history from the request body.
        # Precedence: explicit conversation_history > previous_response_id.
        conversation_history: List[Dict[str, Any]] = []
        raw_history = body.get("conversation_history")
        raw_history_provided = "conversation_history" in body
        if raw_history_provided:
            if not isinstance(raw_history, list):
                return web.json_response(
                    _openai_error("'conversation_history' must be an array of message objects"),
                    status=400,
                )
            for i, entry in enumerate(raw_history):
                if not isinstance(entry, dict) or "role" not in entry or "content" not in entry:
                    return web.json_response(
                        _openai_error(f"conversation_history[{i}] must have 'role' and 'content' fields"),
                        status=400,
                    )
                try:
                    content = _normalize_multimodal_content(entry["content"])
                except ValueError as exc:
                    return _multimodal_validation_error(exc, param=f"conversation_history[{i}].content")
                conversation_history.append(_normalized_conversation_message(entry, content))
            if previous_response_id:
                logger.debug("Both conversation_history and previous_response_id provided; using conversation_history")

        stored_session_id = None
        if not raw_history_provided and previous_response_id:
            stored = self._response_store.get(previous_response_id)
            if stored:
                conversation_history = list(stored.get("conversation_history", []))
                stored_session_id = stored.get("session_id")
                if instructions is None:
                    instructions = stored.get("instructions")

        # When input is a multi-message array, extract all but the last
        # message as conversation history (the last becomes user_message).
        if input_history:
            if raw_history_provided:
                conversation_history.extend(input_history)
            else:
                conversation_history = input_history

        admission_token = self._try_reserve_run_admission()
        if admission_token is None:
            return web.json_response(
                _openai_error(
                    f"Too many concurrent runs (max {self._MAX_CONCURRENT_RUNS})",
                    code="rate_limit_exceeded",
                ),
                status=429,
            )
        admission_state["token"] = admission_token

        try:
            resolver_coroutine = asyncio.to_thread(
                self._resolve_agent_route,
                requested_model=requested_model,
                requested_provider=requested_provider,
            )
            try:
                resolver_task = self._create_owned_task(
                    resolver_coroutine,
                    name=f"managed-run-resolver-{admission_token[-8:]}",
                )
            except Exception as exc:
                logger.error(
                    "[api_server] failed to create run resolver task: %s",
                    exc,
                )
                return web.json_response(
                    _openai_error(
                        "Run executor unavailable",
                        code="run_executor_unavailable",
                    ),
                    status=503,
                )
            resolved_route = await self._await_preworker_owned_task(
                resolver_task,
                admission_token=admission_token,
                admission_state=admission_state,
                operation="run resolver",
            )
            if not isinstance(resolved_route, dict):
                raise RuntimeError("resolved route is not a mapping")
            runtime_kwargs = resolved_route.get("runtime_kwargs")
            if not isinstance(runtime_kwargs, dict):
                raise RuntimeError("resolved runtime is not a mapping")

            from agent.agent_init import resolved_runtime_is_constructible

            route_model = str(resolved_route.get("model") or "").strip()
            route_provider = str(resolved_route.get("provider") or "").strip()
            if (
                not route_model
                or not route_provider
                or not resolved_runtime_is_constructible(
                    provider=runtime_kwargs.get("provider"),
                    api_mode=runtime_kwargs.get("api_mode"),
                    base_url=runtime_kwargs.get("base_url"),
                    api_key=runtime_kwargs.get("api_key"),
                )
            ):
                raise RuntimeError("resolved route is not constructible")
        except Exception as exc:
            logger.warning(
                "[api_server] run route could not be resolved: %s",
                type(exc).__name__,
            )
            return web.json_response(
                _openai_error(
                    "Requested model/provider could not be resolved from configured credentials.",
                    code="model_configuration_error",
                ),
                status=400,
            )

        requested_session_id = header_session_id or body_session_id or stored_session_id or ""
        managed_session = bool(requested_session_id)
        run_id = f"run_{uuid.uuid4().hex}"
        session_id = requested_session_id or run_id
        managed_lease_session_id = session_id
        managed_session_db = None
        managed_lease_lifecycle: Optional[_ManagedLeaseLifecycle] = None

        worker_started = threading.Event()
        worker_exited = threading.Event()
        worker_abandoned = threading.Event()
        lease_heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        lease_release_started = threading.Event()
        lease_release_lock = threading.Lock()
        run_agent_ref: List[Optional[Any]] = [None]
        worker_outcome: List[tuple] = []
        worker_exception: List[BaseException] = []

        def _clear_managed_lease_tracking() -> None:
            lease_heartbeat_stop.set()
            with self._managed_session_runs_lock:
                if (
                    self._managed_session_runs.get(
                        managed_lease_session_id
                    )
                    == run_id
                ):
                    self._managed_session_runs.pop(
                        managed_lease_session_id,
                        None,
                    )

        def _release_managed_lease() -> None:
            if not managed_session or managed_session_db is None:
                return
            with lease_release_lock:
                if lease_release_started.is_set():
                    return
                lease_release_started.set()
            lease_heartbeat_stop.set()
            try:
                managed_session_db.release_managed_run_lease(
                    managed_lease_session_id,
                    owner_id=self._managed_run_lease_owner_id,
                    run_id=run_id,
                )
            except Exception as exc:
                # Fail closed: a release failure leaves the durable lease in
                # place until expiry instead of admitting a duplicate turn.
                logger.error(
                    "[api_server] failed to release managed run lease %s/%s: %s",
                    session_id,
                    run_id,
                    exc,
                )
            finally:
                _clear_managed_lease_tracking()

        async def _release_managed_lease_async() -> None:
            if not managed_session or managed_session_db is None:
                return
            release_coroutine = asyncio.to_thread(_release_managed_lease)
            try:
                release_task = self._create_owned_task(
                    release_coroutine,
                    name=f"managed-run-worker-release-{run_id[-8:]}",
                )
            except BaseException as exc:
                logger.error(
                    "[api_server] managed run release task unavailable for "
                    "%s/%s; retaining ownership through executor Future: %s",
                    session_id,
                    run_id,
                    exc,
                )
                try:
                    release_future = (
                        asyncio.get_running_loop().run_in_executor(
                            None,
                            _release_managed_lease,
                        )
                    )
                except BaseException as fallback_exc:
                    # A hard event-loop/executor failure is the only path that
                    # falls back to the durable lease TTL.
                    _clear_managed_lease_tracking()
                    logger.error(
                        "[api_server] managed run exact release executor "
                        "unavailable for %s/%s: %s",
                        session_id,
                        run_id,
                        fallback_exc,
                    )
                    return
                try:
                    await self._await_task_without_forwarding_cancel(
                        release_future
                    )
                except BaseException as fallback_exc:
                    logger.error(
                        "[api_server] managed run exact release Future failed "
                        "for %s/%s: %s",
                        session_id,
                        run_id,
                        fallback_exc,
                    )
                return
            try:
                await self._await_task_without_forwarding_cancel(
                    release_task
                )
            except BaseException as exc:
                logger.error(
                    "[api_server] managed run release task failed for "
                    "%s/%s: %s",
                    session_id,
                    run_id,
                    exc,
                )

        def _mark_managed_lease_lost(reason: str) -> None:
            if lease_lost.is_set():
                return
            lease_lost.set()
            logger.error(
                "[api_server] managed run lease lost for %s/%s: %s",
                session_id,
                run_id,
                reason,
            )
            agent = run_agent_ref[0]
            if agent is not None:
                try:
                    agent.interrupt("Managed session lease lost; stopping run")
                except Exception:
                    pass

        def _managed_lease_heartbeat_loop() -> None:
            if not managed_session or managed_session_db is None:
                return
            interval = float(self._MANAGED_RUN_LEASE_HEARTBEAT_SECONDS)
            while not lease_heartbeat_stop.wait(interval):
                if lease_heartbeat_stop.is_set():
                    return
                try:
                    renewed = managed_session_db.heartbeat_managed_run_lease(
                        managed_lease_session_id,
                        owner_id=self._managed_run_lease_owner_id,
                        run_id=run_id,
                        lease_seconds=self._MANAGED_RUN_LEASE_SECONDS,
                    )
                except Exception as exc:
                    _mark_managed_lease_lost(
                        f"heartbeat unavailable: {type(exc).__name__}: {exc}"
                    )
                    return
                # Release sets the stop flag before deleting the row.  An
                # already in-flight heartbeat may therefore observe rowcount
                # zero after a legitimate terminal release; that is shutdown,
                # not ownership loss, and must not interrupt a completed run.
                if lease_heartbeat_stop.is_set():
                    return
                if not renewed:
                    _mark_managed_lease_lost("lease no longer owned by this run")
                    return

        if managed_session:
            db = self._ensure_session_db()
            if db is None:
                return web.json_response(
                    _openai_error(
                        "Session database unavailable",
                        code="session_db_unavailable",
                    ),
                    status=503,
                )
            try:
                session_query_coroutine = asyncio.to_thread(
                    db.get_session,
                    requested_session_id,
                )
                session_query_task = self._create_owned_task(
                    session_query_coroutine,
                    name=f"managed-run-session-query-{run_id[-8:]}",
                )
                managed_session_record = (
                    await self._await_preworker_owned_task(
                        session_query_task,
                        admission_token=admission_token,
                        admission_state=admission_state,
                        operation="managed session query",
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "[api_server] managed session query unavailable for "
                    "%s: %s",
                    requested_session_id,
                    exc,
                )
                return web.json_response(
                    _openai_error(
                        "Session database unavailable",
                        code="session_db_unavailable",
                    ),
                    status=503,
                )
            if managed_session_record is None:
                return web.json_response(
                    _openai_error("Session not found", code="session_not_found"),
                    status=404,
                )
            try:
                lease_key_query_coroutine = asyncio.to_thread(
                    db.get_managed_run_lease_key,
                    requested_session_id,
                )
                lease_key_query_task = self._create_owned_task(
                    lease_key_query_coroutine,
                    name=f"managed-run-lease-key-query-{run_id[-8:]}",
                )
                managed_lease_session_id = (
                    await self._await_preworker_owned_task(
                        lease_key_query_task,
                        admission_token=admission_token,
                        admission_state=admission_state,
                        operation="managed lease key query",
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "[api_server] managed lease key query unavailable for "
                    "%s: %s",
                    requested_session_id,
                    exc,
                )
                return web.json_response(
                    _openai_error(
                        "Session database unavailable",
                        code="session_db_unavailable",
                    ),
                    status=503,
                )
            managed_lease_lifecycle = self._begin_managed_lease_lifecycle(
                admission_token
            )
            if managed_lease_lifecycle is None:
                return web.json_response(
                    _openai_error(
                        "API server is draining managed session work",
                        code="server_shutting_down",
                    ),
                    status=503,
                )
            admission_state["lease_lifecycle"] = managed_lease_lifecycle
            admission_state["lease_acquire_owned"] = True
            try:
                lease_acquire_coroutine = asyncio.to_thread(
                    db.acquire_managed_run_lease,
                    managed_lease_session_id,
                    owner_id=self._managed_run_lease_owner_id,
                    run_id=run_id,
                    lease_seconds=self._MANAGED_RUN_LEASE_SECONDS,
                )
                lease_acquire_task = self._create_owned_task(
                    lease_acquire_coroutine,
                    name=f"managed-run-lease-acquire-{run_id[-8:]}",
                )
                managed_lease_lifecycle.acquire_task = lease_acquire_task
                try:
                    managed_lease_acquired = await asyncio.shield(
                        lease_acquire_task
                    )
                except asyncio.CancelledError:
                    await self._start_managed_lease_cleanup(
                        lifecycle=managed_lease_lifecycle,
                        acquire_task=lease_acquire_task,
                        db=db,
                        lease_session_id=managed_lease_session_id,
                        owner_id=self._managed_run_lease_owner_id,
                        run_id=run_id,
                    )
                    raise
                if (
                    managed_lease_lifecycle.shutdown_requested
                    or managed_lease_lifecycle.epoch
                    != self._managed_lease_shutdown_epoch
                ):
                    await self._start_managed_lease_cleanup(
                        lifecycle=managed_lease_lifecycle,
                        acquire_task=lease_acquire_task,
                        db=db,
                        lease_session_id=managed_lease_session_id,
                        owner_id=self._managed_run_lease_owner_id,
                        run_id=run_id,
                    )
                    return web.json_response(
                        _openai_error(
                            "API server is draining managed session work",
                            code="server_shutting_down",
                        ),
                        status=503,
                    )
            except Exception as exc:
                logger.error(
                    "[api_server] managed run lease unavailable for session %s: %s",
                    requested_session_id,
                    exc,
                )
                return web.json_response(
                    _openai_error(
                        "Session lease unavailable",
                        code="session_lease_unavailable",
                    ),
                    status=503,
                )
            if not managed_lease_acquired:
                return web.json_response(
                    _openai_error(
                        "Session already has an active run",
                        code="session_busy",
                    ),
                    status=409,
                )

            managed_session_db = db
            try:
                threading.Thread(
                    target=_managed_lease_heartbeat_loop,
                    name=f"managed-run-lease-{run_id[-8:]}",
                    daemon=True,
                ).start()
            except Exception as exc:
                logger.error(
                    "[api_server] failed to start managed lease heartbeat: %s",
                    exc,
                )
                worker_abandoned.set()
                await _release_managed_lease_async()
                return web.json_response(
                    _openai_error(
                        "Session lease unavailable",
                        code="session_lease_unavailable",
                    ),
                    status=503,
                )

            # Read the authoritative transcript only after acquiring the
            # session.  Reading first lets a previous owner commit and release
            # in the gap, causing this run to execute with stale history.
            try:
                stored_history = await asyncio.to_thread(
                    db.get_messages_as_conversation,
                    requested_session_id,
                )
            except asyncio.CancelledError:
                await _release_managed_lease_async()
                raise
            except Exception as exc:
                await _release_managed_lease_async()
                logger.error(
                    "[api_server] managed history unavailable for session %s: %s",
                    requested_session_id,
                    exc,
                )
                return web.json_response(
                    _openai_error(
                        "Session database unavailable",
                        code="session_db_unavailable",
                    ),
                    status=503,
                )

            explicit_history_provided = raw_history_provided or bool(input_history)
            if explicit_history_provided and not self._managed_history_matches(
                conversation_history,
                stored_history,
            ):
                await _release_managed_lease_async()
                return web.json_response(
                    _openai_error(
                        "Submitted history conflicts with managed session history",
                        code="session_history_conflict",
                    ),
                    status=409,
                )

            provider_history, checkpoint_conflict = self._managed_provider_history(
                stored_history,
                platform_message_id=platform_message_id,
                checkpoint_content=checkpoint_content,
            )
            if checkpoint_conflict:
                await _release_managed_lease_async()
                return web.json_response(
                    _openai_error(
                        "Submitted platform message conflicts with managed session checkpoint",
                        code="platform_message_conflict",
                    ),
                    status=409,
                )

            try:
                admission_lease_renewed = await asyncio.to_thread(
                    db.heartbeat_managed_run_lease,
                    managed_lease_session_id,
                    owner_id=self._managed_run_lease_owner_id,
                    run_id=run_id,
                    lease_seconds=self._MANAGED_RUN_LEASE_SECONDS,
                )
            except asyncio.CancelledError:
                await _release_managed_lease_async()
                raise
            except Exception as exc:
                logger.error(
                    "[api_server] managed lease admission check unavailable for %s/%s: %s",
                    requested_session_id,
                    run_id,
                    exc,
                )
                await _release_managed_lease_async()
                return web.json_response(
                    _openai_error(
                        "Session lease unavailable",
                        code="session_lease_unavailable",
                    ),
                    status=503,
                )
            if lease_lost.is_set() or not admission_lease_renewed:
                _mark_managed_lease_lost("lease lost before worker admission")
                await _release_managed_lease_async()
                return web.json_response(
                    _openai_error(
                        "Session lease lost before run start",
                        code="session_lease_lost",
                    ),
                    status=409,
                )
            conversation_history = provider_history

        if (
            managed_lease_lifecycle is not None
            and (
                managed_lease_lifecycle.shutdown_requested
                or managed_lease_lifecycle.epoch
                != self._managed_lease_shutdown_epoch
            )
        ):
            lease_heartbeat_stop.set()
            await self._start_managed_lease_cleanup(
                lifecycle=managed_lease_lifecycle,
                acquire_task=managed_lease_lifecycle.acquire_task,
                db=managed_session_db,
                lease_session_id=managed_lease_session_id,
                owner_id=self._managed_run_lease_owner_id,
                run_id=run_id,
            )
            return web.json_response(
                _openai_error(
                    "API server is draining managed session work",
                    code="server_shutting_down",
                ),
                status=503,
            )

        # Keep event-stream retention bounded independently from active-run
        # admission.  This synchronous check and allocation below have no
        # intervening await, so concurrent requests cannot overshoot the cap.
        if len(self._run_streams) >= self._MAX_RETAINED_RUN_STREAMS:
            if managed_session:
                worker_abandoned.set()
                await _release_managed_lease_async()
            return web.json_response(
                _openai_error(
                    "Too many unconsumed run event streams",
                    code="run_stream_capacity_exceeded",
                ),
                status=429,
            )

        if managed_session:
            with self._managed_session_runs_lock:
                self._managed_session_runs[managed_lease_session_id] = run_id
        approval_session_key = gateway_session_key or session_id or run_id
        ephemeral_system_prompt = instructions
        loop = asyncio.get_running_loop()
        q: "asyncio.Queue[Optional[Dict]]" = asyncio.Queue()
        created_at = time.time()
        self._run_streams[run_id] = q
        self._run_streams_created[run_id] = created_at
        self._run_approval_sessions[run_id] = approval_session_key

        event_cb = self._make_run_event_callback(run_id, loop)
        tool_start_cb, tool_complete_cb = self._make_run_tool_callbacks(run_id, loop)

        # Also wire stream_delta_callback so message.delta events flow through.
        def _text_cb(delta: Optional[str]) -> None:
            if delta is None:
                return
            try:
                loop.call_soon_threadsafe(q.put_nowait, {
                    "event": "message.delta",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "delta": delta,
                })
            except Exception:
                pass

        status_fields = {
            "created_at": created_at,
            "session_id": session_id,
            "model": (
                resolved_route["model"]
                if resolved_route is not None
                else body.get("model", self._model_name)
            ),
        }
        if resolved_route is not None and resolved_route.get("provider"):
            status_fields["provider"] = resolved_route["provider"]
        self._set_run_status(run_id, "queued", **status_fields)

        def _publish_worker_outcome(result: Any, usage: Dict[str, Any]) -> None:
            if isinstance(result, dict) and result.get("failed"):
                error_msg = result.get("error") or "agent run failed"
                error_payload = {
                    "message": str(error_msg),
                    "code": str(
                        result.get("error_code")
                        or result.get("code")
                        or "run_failed"
                    ),
                }
                q.put_nowait({
                    "event": "run.failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "error": error_payload,
                })
                self._set_run_status(
                    run_id,
                    "failed",
                    error=error_msg,
                    last_event="run.failed",
                )
                return

            final_response = (
                result.get("final_response", "") if isinstance(result, dict) else ""
            )
            q.put_nowait({
                "event": "run.completed",
                "run_id": run_id,
                "timestamp": time.time(),
                "output": final_response,
                "usage": usage,
            })
            self._set_run_status(
                run_id,
                "completed",
                output=final_response,
                usage=usage,
                last_event="run.completed",
            )

        def _publish_worker_exception(exc: BaseException) -> None:
            self._set_run_status(
                run_id,
                "failed",
                error=str(exc),
                last_event="run.failed",
            )
            try:
                q.put_nowait({
                    "event": "run.failed",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "error": str(exc),
                })
            except Exception:
                pass

        def _publish_cancelled_after_worker(
            result: Any = None,
            usage: Optional[Dict[str, Any]] = None,
            exc: Optional[BaseException] = None,
            *,
            worker_completed: bool = False,
        ) -> None:
            event: Dict[str, Any] = {
                "event": "run.cancelled",
                "run_id": run_id,
                "timestamp": time.time(),
            }
            status_fields: Dict[str, Any] = {
                "last_event": "run.cancelled",
            }
            if worker_completed:
                event["worker_completed_after_cancel"] = True
                status_fields["worker_completed_after_cancel"] = True
            if isinstance(result, dict):
                if result.get("failed"):
                    error_msg = result.get("error") or "agent run failed"
                    event["error"] = str(error_msg)
                    status_fields["error"] = str(error_msg)
                else:
                    output = result.get("final_response", "")
                    event["output"] = output
                    status_fields["output"] = output
            if usage is not None:
                event["usage"] = usage
                status_fields["usage"] = usage
            if exc is not None:
                event["error"] = str(exc)
                status_fields["error"] = str(exc)
            try:
                q.put_nowait(event)
            except Exception:
                pass
            self._set_run_status(run_id, "cancelled", **status_fields)

        worker_wrapper_started = False
        worker_finalized = False
        worker_prestart_release_complete = False
        worker_reconcile_task: Optional["asyncio.Future"] = None
        worker_finalized_waiter = loop.create_future()

        def _unregister_run_approval_notify() -> None:
            try:
                from tools.approval import unregister_gateway_notify

                unregister_gateway_notify(approval_session_key)
            except Exception:
                pass

        def _complete_worker_reconciliation(
            finished_task: "asyncio.Task",
        ) -> None:
            nonlocal worker_finalized
            if not worker_finalized:
                if not worker_wrapper_started:
                    _publish_cancelled_after_worker()
                _unregister_run_approval_notify()
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
                self._release_run_admission(admission_token)
                if (
                    managed_lease_lifecycle is not None
                    and not managed_lease_lifecycle.completed
                ):
                    self._finish_managed_lease_lifecycle(
                        managed_lease_lifecycle,
                        release_admission=False,
                    )
                worker_finalized = True
                if not worker_finalized_waiter.done():
                    worker_finalized_waiter.set_result(None)

            # Repeat the idempotent map removals even after finalization.  An
            # eager/custom Task may reach terminal state before the outer
            # startup path inserts these references.
            self._background_tasks.discard(finished_task)
            if self._active_run_tasks.get(run_id) is finished_task:
                self._active_run_tasks.pop(run_id, None)
            self._active_run_agents.pop(run_id, None)
            self._interrupted_run_ids.discard(run_id)
            self._approval_cancelled_run_ids.discard(run_id)
            self._run_approval_sessions.pop(run_id, None)

        async def _release_prestart_and_reconcile(
            finished_task: "asyncio.Task",
        ) -> None:
            nonlocal worker_prestart_release_complete
            try:
                await _release_managed_lease_async()
            finally:
                worker_prestart_release_complete = True
                _complete_worker_reconciliation(finished_task)

        def _finish_prestart_release_future(
            release_future: "asyncio.Future",
            finished_task: "asyncio.Task",
        ) -> None:
            nonlocal worker_prestart_release_complete
            if worker_prestart_release_complete:
                return
            try:
                release_future.result()
            except BaseException as exc:
                logger.error(
                    "[api_server] prestart exact release Future failed for "
                    "%s/%s: %s",
                    session_id,
                    run_id,
                    exc,
                )
            finally:
                worker_prestart_release_complete = True
                _complete_worker_reconciliation(finished_task)

        def _poll_prestart_release_future(
            release_future: "asyncio.Future",
            finished_task: "asyncio.Task",
        ) -> None:
            if release_future.done():
                _finish_prestart_release_future(
                    release_future,
                    finished_task,
                )
                return
            try:
                loop.call_later(
                    0.01,
                    _poll_prestart_release_future,
                    release_future,
                    finished_task,
                )
            except BaseException as exc:
                # Retain the lifecycle and admission slot fail-closed if the
                # event loop can no longer observe the exact release Future.
                logger.error(
                    "[api_server] could not poll prestart exact release for "
                    "%s/%s: %s",
                    session_id,
                    run_id,
                    exc,
                )

        def _reconcile_worker_terminal(
            finished_task: "asyncio.Task",
        ) -> None:
            nonlocal worker_prestart_release_complete
            nonlocal worker_reconcile_task
            try:
                finished_task.exception()
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                logger.error(
                    "[api_server] run task %s ended unexpectedly: %s",
                    run_id,
                    exc,
                )

            if (
                managed_session
                and not worker_wrapper_started
                and not worker_prestart_release_complete
            ):
                with self._managed_session_runs_lock:
                    if not worker_started.is_set():
                        worker_abandoned.set()
                if worker_reconcile_task is None:
                    reconcile_coroutine = _release_prestart_and_reconcile(
                        finished_task
                    )
                    try:
                        worker_reconcile_task = self._create_owned_task(
                            reconcile_coroutine,
                            name=(
                                "managed-run-prestart-release-"
                                f"{run_id[-8:]}"
                            ),
                        )
                    except BaseException as exc:
                        logger.error(
                            "[api_server] prestart release task unavailable "
                            "for %s/%s; retaining ownership through executor "
                            "Future: %s",
                            session_id,
                            run_id,
                            exc,
                        )
                        try:
                            release_future = loop.run_in_executor(
                                None,
                                _release_managed_lease,
                            )
                        except BaseException as fallback_exc:
                            # Only a hard loop/executor failure falls back to
                            # the durable lease TTL.
                            worker_prestart_release_complete = True
                            _clear_managed_lease_tracking()
                            logger.error(
                                "[api_server] prestart exact release executor "
                                "unavailable for %s/%s: %s",
                                session_id,
                                run_id,
                                fallback_exc,
                            )
                            _complete_worker_reconciliation(finished_task)
                        else:
                            worker_reconcile_task = release_future
                            if managed_lease_lifecycle is not None:
                                managed_lease_lifecycle.supervisor_task = (
                                    release_future
                                )
                            try:
                                release_future.add_done_callback(
                                    lambda future: (
                                        _finish_prestart_release_future(
                                            future,
                                            finished_task,
                                        )
                                    )
                                )
                            except BaseException as callback_exc:
                                logger.error(
                                    "[api_server] prestart exact release "
                                    "callback unavailable for %s/%s: %s",
                                    session_id,
                                    run_id,
                                    callback_exc,
                                )
                                _poll_prestart_release_future(
                                    release_future,
                                    finished_task,
                                )
                    else:
                        if managed_lease_lifecycle is not None:
                            managed_lease_lifecycle.supervisor_task = (
                                worker_reconcile_task
                            )
                return

            _complete_worker_reconciliation(finished_task)

        async def _run_and_close():
            nonlocal worker_wrapper_started
            worker_wrapper_started = True
            try:
                self._set_run_status(run_id, "running")
                agent = self._create_agent(
                    ephemeral_system_prompt=ephemeral_system_prompt,
                    session_id=session_id,
                    stream_delta_callback=_text_cb,
                    tool_progress_callback=event_cb,
                    tool_start_callback=tool_start_cb,
                    tool_complete_callback=tool_complete_cb,
                    gateway_session_key=gateway_session_key,
                    requested_model=requested_model,
                    requested_provider=requested_provider,
                    resolved_route=resolved_route,
                )
                self._active_run_agents[run_id] = agent
                run_agent_ref[0] = agent

                def _approval_notify(approval_data: Dict[str, Any]) -> None:
                    event = dict(approval_data or {})
                    event.update({
                        "event": "approval.request",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "choices": ["once", "session", "always", "deny"],
                    })
                    self._set_run_status(
                        run_id,
                        "waiting_for_approval",
                        last_event="approval.request",
                    )
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, event)
                    except Exception:
                        pass

                def _run_sync_inner():
                    from gateway.session_context import clear_session_vars, set_session_vars
                    from hermes_state import (
                        bind_managed_run_write_lease,
                        reset_managed_run_write_lease,
                    )
                    from tools.approval import (
                        register_gateway_notify,
                        reset_current_session_key,
                        set_current_session_key,
                        unregister_gateway_notify,
                    )

                    effective_task_id = session_id or run_id
                    approval_token = None
                    write_lease_token = None
                    session_tokens = []
                    try:
                        # Bind approval/session identity for this API run via
                        # contextvars so concurrent runs do not share process
                        # environment state.
                        approval_token = set_current_session_key(approval_session_key)
                        session_tokens = set_session_vars(
                            platform="api_server",
                            session_key=approval_session_key,
                        )
                        if managed_session:
                            write_lease_token = bind_managed_run_write_lease(
                                managed_lease_session_id,
                                owner_id=self._managed_run_lease_owner_id,
                                run_id=run_id,
                                lost_event=lease_lost,
                            )
                        register_gateway_notify(approval_session_key, _approval_notify)
                        conversation_kwargs = {
                            "user_message": user_message,
                            "conversation_history": conversation_history,
                            "task_id": effective_task_id,
                        }
                        if platform_message_id:
                            conversation_kwargs["persist_user_platform_message_id"] = (
                                platform_message_id
                            )
                        r = agent.run_conversation(**conversation_kwargs)
                    finally:
                        try:
                            unregister_gateway_notify(approval_session_key)
                        finally:
                            if approval_token is not None:
                                try:
                                    reset_current_session_key(approval_token)
                                except Exception:
                                    pass
                            if write_lease_token is not None:
                                try:
                                    reset_managed_run_write_lease(write_lease_token)
                                except Exception:
                                    pass
                            if session_tokens:
                                try:
                                    clear_session_vars(session_tokens)
                                except Exception:
                                    pass
                    u = {
                        "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                        "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                        "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
                    }
                    return r, u

                def _run_sync():
                    cancelled_before_start = (
                        {
                            "failed": True,
                            "error": "run cancelled before worker start",
                            "error_code": "run_cancelled",
                        },
                        {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        },
                    )

                    def _record_outcome(outcome: tuple) -> tuple:
                        worker_outcome[:] = [outcome]
                        return outcome

                    try:
                        if managed_session:
                            # Check the process-local cancellation marker before
                            # touching SQLite, then renew the exact durable
                            # owner/run pair at the final executor boundary.
                            # The HTTP admission check can be arbitrarily far
                            # behind a queued worker and is not sufficient here.
                            with self._managed_session_runs_lock:
                                if (
                                    worker_abandoned.is_set()
                                    or self._managed_session_runs.get(managed_lease_session_id) != run_id
                                ):
                                    return _record_outcome(cancelled_before_start)

                            try:
                                worker_lease_renewed = (
                                    managed_session_db is not None
                                    and managed_session_db.heartbeat_managed_run_lease(
                                        managed_lease_session_id,
                                        owner_id=self._managed_run_lease_owner_id,
                                        run_id=run_id,
                                        lease_seconds=self._MANAGED_RUN_LEASE_SECONDS,
                                    )
                                )
                            except Exception as exc:
                                _mark_managed_lease_lost(
                                    "worker admission heartbeat unavailable: "
                                    f"{type(exc).__name__}: {exc}"
                                )
                                worker_lease_renewed = False

                            # Cancellation can win while the durable renewal is
                            # blocked.  Re-check under the process-local lock
                            # before claiming worker execution.
                            with self._managed_session_runs_lock:
                                if (
                                    worker_abandoned.is_set()
                                    or self._managed_session_runs.get(managed_lease_session_id) != run_id
                                ):
                                    return _record_outcome(cancelled_before_start)
                                if worker_lease_renewed and not lease_lost.is_set():
                                    worker_started.set()

                            if not worker_lease_renewed or lease_lost.is_set():
                                _mark_managed_lease_lost(
                                    "lease lost before executor worker start"
                                )
                                return _record_outcome(
                                    (
                                        {
                                            "failed": True,
                                            "error": "managed session lease lost before worker start",
                                            "error_code": "session_lease_lost",
                                        },
                                        cancelled_before_start[1],
                                    )
                                )

                        result_and_usage = _run_sync_inner()
                        if managed_session and lease_lost.is_set():
                            return _record_outcome(
                                (
                                    {
                                        "failed": True,
                                        "error": "managed session lease lost during run",
                                        "error_code": "session_lease_lost",
                                    },
                                    result_and_usage[1],
                                )
                            )
                        return _record_outcome(result_and_usage)
                    except BaseException as exc:
                        worker_exception[:] = [exc]
                        raise
                    finally:
                        if managed_session:
                            # Mark the executor boundary.  The async wrapper
                            # publishes the terminal event/status before it
                            # releases the durable lease in its ``finally``.
                            worker_exited.set()

                result, usage = await asyncio.get_running_loop().run_in_executor(None, _run_sync)
                # Structured client failures (401/400) return ``failed=True``
                # instead of raising; the shared publisher handles both those
                # and successful results (issue #15561).
                if run_id in self._approval_cancelled_run_ids:
                    _publish_cancelled_after_worker(
                        result,
                        usage,
                        worker_completed=True,
                    )
                else:
                    _publish_worker_outcome(result, usage)
            except asyncio.CancelledError:
                # Cancellation can race with an executor turn that has already
                # returned and persisted its messages.  In that case the
                # durable turn wins: publish its terminal outcome instead of a
                # contradictory cancelled event.
                if worker_exited.is_set() and worker_outcome:
                    if run_id in self._approval_cancelled_run_ids:
                        _publish_cancelled_after_worker(
                            *worker_outcome[0],
                            worker_completed=True,
                        )
                    else:
                        _publish_worker_outcome(*worker_outcome[0])
                    return
                if worker_exited.is_set() and worker_exception:
                    _publish_worker_exception(worker_exception[0])
                    return

                self._set_run_status(
                    run_id,
                    "stopping",
                    last_event="run.stopping",
                )
                self._interrupt_run_agent_once(
                    run_id,
                    "Run task cancelled",
                )
                # Deny and wake approval waits before any drain of the
                # non-preemptible executor worker.
                _unregister_run_approval_notify()

                with self._managed_session_runs_lock:
                    if managed_session and not worker_started.is_set():
                        worker_abandoned.set()
                if not managed_session or worker_abandoned.is_set():
                    _publish_cancelled_after_worker()
                    return

                # The executor cannot be preempted by Task.cancel().  Keep the
                # wrapper, heartbeat, and durable lease alive until the worker
                # actually exits; otherwise a terminal cancelled event can be
                # followed by a late history write from the same run.
                while not worker_exited.is_set():
                    try:
                        await asyncio.sleep(0.025)
                    except asyncio.CancelledError:
                        # Repeated stop/shutdown cancellation does not widen
                        # the admission window while the worker still writes.
                        continue

                if worker_outcome:
                    result, usage = worker_outcome[0]
                    _publish_cancelled_after_worker(
                        result,
                        usage,
                        worker_completed=True,
                    )
                elif worker_exception:
                    _publish_cancelled_after_worker(
                        exc=worker_exception[0],
                        worker_completed=True,
                    )
                else:
                    _publish_cancelled_after_worker(worker_completed=True)
                return
            except Exception as exc:
                logger.exception("[api_server] run %s failed", run_id)
                _publish_worker_exception(exc)
            finally:
                if managed_session:
                    with self._managed_session_runs_lock:
                        if not worker_started.is_set():
                            worker_abandoned.set()
                    # The Task cannot become terminal, and therefore cannot
                    # release admission, until the exact durable release has
                    # completed (or failed closed).
                    await _release_managed_lease_async()

        run_coroutine = _run_and_close()
        try:
            task = self._create_owned_task(
                run_coroutine,
                name=f"api-run-worker-{run_id[-8:]}",
            )
        except Exception as exc:
            logger.error(
                "[api_server] failed to create run task %s: %s",
                run_id,
                exc,
            )
            worker_abandoned.set()
            await _release_managed_lease_async()
            self._run_streams.pop(run_id, None)
            self._run_streams_created.pop(run_id, None)
            self._run_approval_sessions.pop(run_id, None)
            self._run_statuses.pop(run_id, None)
            return web.json_response(
                _openai_error(
                    "Run executor unavailable",
                    code="run_executor_unavailable",
                ),
                status=503,
            )
        self._active_run_tasks[run_id] = task
        self._background_tasks.add(task)
        if managed_lease_lifecycle is not None:
            # The durable lifecycle now spans the real worker Task through its
            # exact DB release.  Shutdown drains this lifecycle even when the
            # worker is cancelled before its coroutine executes one step.
            managed_lease_lifecycle.cleanup_started = True
            managed_lease_lifecycle.supervisor_task = task
            admission_state["lease_acquire_owned"] = False
        # From this point either the registered callback or the request's
        # callback-failure path runs the same reconciler.  The outer request
        # wrapper must not become a second admission owner.
        admission_state["worker_owned"] = True
        try:
            task.add_done_callback(_reconcile_worker_terminal)
        except BaseException as exc:
            # A real Task owns the coroutine, but startup is not externally
            # committed without its one terminal reconciler.  Cancel, drain,
            # and run that same idempotent reconciler inline before returning.
            logger.error(
                "[api_server] failed to supervise run task %s: %s",
                run_id,
                exc,
            )
            if managed_session:
                with self._managed_session_runs_lock:
                    if not worker_started.is_set():
                        worker_abandoned.set()
            task.cancel()
            try:
                await self._await_task_without_forwarding_cancel(task)
            except BaseException:
                pass
            _reconcile_worker_terminal(task)
            try:
                await self._await_task_without_forwarding_cancel(
                    worker_finalized_waiter
                )
            except BaseException:
                pass

            self._run_streams.pop(run_id, None)
            self._run_streams_created.pop(run_id, None)
            self._run_statuses.pop(run_id, None)
            return web.json_response(
                _openai_error(
                    "Run executor unavailable",
                    code="run_executor_unavailable",
                ),
                status=503,
            )
        if task.done():
            # Custom/eager Task factories may return an already-terminal real
            # Task.  Reconcile again after outer map insertion so no reference
            # can be reintroduced after terminal cleanup.
            _reconcile_worker_terminal(task)

        response_headers = {"X-Hermes-Session-Id": session_id}
        if gateway_session_key:
            response_headers["X-Hermes-Session-Key"] = gateway_session_key
        return web.json_response(
            {"run_id": run_id, "session_id": session_id, "status": "started"},
            status=202,
            headers=response_headers,
        )

    async def _handle_get_run(self, request: "web.Request") -> "web.Response":
        """GET /v1/runs/{run_id} — return pollable run status for external UIs."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]
        status = self._run_statuses.get(run_id)
        if status is None:
            return web.json_response(
                _openai_error(f"Run not found: {run_id}", code="run_not_found"),
                status=404,
            )
        return web.json_response(status)

    async def _handle_run_events(self, request: "web.Request") -> "web.StreamResponse":
        """GET /v1/runs/{run_id}/events — SSE stream of structured agent lifecycle events."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]

        # Allow subscribing slightly before the run is registered (race condition window)
        for _ in range(20):
            if run_id in self._run_streams:
                break
            await asyncio.sleep(0.05)
        else:
            return web.json_response(_openai_error(f"Run not found: {run_id}", code="run_not_found"), status=404)

        q = self._run_streams[run_id]

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                if event is None:
                    # Run finished — send final SSE comment and close
                    await response.write(b": stream closed\n\n")
                    break
                payload = f"data: {json.dumps(event)}\n\n"
                await response.write(payload.encode())
        except Exception as exc:
            logger.debug("[api_server] SSE stream error for run %s: %s", run_id, exc)
        finally:
            self._run_streams.pop(run_id, None)
            self._run_streams_created.pop(run_id, None)

        return response


    async def _handle_run_approval(self, request: "web.Request") -> "web.Response":
        """POST /v1/runs/{run_id}/approval — resolve a pending run approval."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]
        status = self._run_statuses.get(run_id)
        if status is None:
            return web.json_response(
                _openai_error(f"Run not found: {run_id}", code="run_not_found"),
                status=404,
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response(_openai_error("Invalid JSON"), status=400)

        raw_choice = str(body.get("choice", "")).strip().lower()
        aliases = {"approve": "once", "approved": "once", "allow": "once"}
        choice = aliases.get(raw_choice, raw_choice)
        allowed = {"once", "session", "always", "deny"}
        if choice not in allowed:
            return web.json_response(
                _openai_error(
                    "Invalid approval choice; expected one of: once, session, always, deny",
                    code="invalid_approval_choice",
                ),
                status=400,
            )

        approval_session_key = self._run_approval_sessions.get(run_id)
        if not approval_session_key:
            return web.json_response(
                _openai_error(
                    f"Run has no active approval session: {run_id}",
                    code="approval_not_active",
                ),
                status=409,
            )

        resolve_all = (
            _coerce_request_bool(body.get("all"), default=False)
            or _coerce_request_bool(body.get("resolve_all"), default=False)
        )
        try:
            from tools.approval import resolve_gateway_approval

            resolved = resolve_gateway_approval(
                approval_session_key,
                choice,
                resolve_all=resolve_all,
            )
        except Exception as exc:
            logger.exception("[api_server] approval resolution failed for run %s", run_id)
            return web.json_response(_openai_error(str(exc)), status=500)

        if resolved <= 0:
            return web.json_response(
                _openai_error(
                    f"Run has no pending approval: {run_id}",
                    code="approval_not_pending",
                ),
                status=409,
            )

        self._set_run_status(run_id, "running", last_event="approval.responded")
        q = self._run_streams.get(run_id)
        if q is not None:
            try:
                q.put_nowait({
                    "event": "approval.responded",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "choice": choice,
                    "resolved": resolved,
                })
            except Exception:
                pass

        return web.json_response({
            "object": "hermes.run.approval_response",
            "run_id": run_id,
            "choice": choice,
            "resolved": resolved,
        })

    async def _handle_stop_run(self, request: "web.Request") -> "web.Response":
        """POST /v1/runs/{run_id}/stop — interrupt a running agent."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]
        agent = self._active_run_agents.get(run_id)
        task = self._active_run_tasks.get(run_id)

        if agent is None and task is None:
            return web.json_response(_openai_error(f"Run not found: {run_id}", code="run_not_found"), status=404)

        current_status = self._run_statuses.get(run_id, {}).get("status")
        if current_status in {"completed", "failed", "cancelled"}:
            return web.json_response({
                "run_id": run_id,
                "status": current_status,
            })

        self._set_run_status(run_id, "stopping", last_event="run.stopping")

        self._interrupt_run_agent_once(
            run_id,
            "Stop requested via API",
        )

        def _deny_pending_approvals() -> None:
            approval_session_key = self._run_approval_sessions.get(run_id)
            if not approval_session_key:
                return
            try:
                from tools.approval import unregister_gateway_notify

                if unregister_gateway_notify(approval_session_key):
                    self._approval_cancelled_run_ids.add(run_id)
            except Exception:
                pass

        # The executor worker cannot enter its own finally while blocked on
        # an approval Event.  Deny and wake it before cancelling the wrapper.
        _deny_pending_approvals()

        if task is not None and not task.done():
            task.cancel()
            # Bounded wait: run_conversation() executes in the default
            # executor thread which task.cancel() cannot preempt — we rely on
            # agent.interrupt() above to break the loop. Cap the wait so a
            # slow/unresponsive interrupt can't hang this handler.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[api_server] stop for run %s timed out after 5s; "
                    "agent may still be finishing the current step",
                    run_id,
                )
            except asyncio.CancelledError:
                self._interrupt_run_agent_once(
                    run_id,
                    "Stop request cancelled",
                )
                _deny_pending_approvals()
            except Exception:
                pass

        return web.json_response({"run_id": run_id, "status": "stopping"})

    async def _sweep_orphaned_runs(self) -> None:
        """Periodically clean up run streams that were never consumed."""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            stale = [
                run_id
                for run_id, created_at in list(self._run_streams_created.items())
                if now - created_at > self._RUN_STREAM_TTL
            ]
            for run_id in stale:
                logger.debug("[api_server] sweeping orphaned run %s", run_id)
                try:
                    from tools.approval import unregister_gateway_notify

                    approval_session_key = self._run_approval_sessions.get(run_id)
                    if approval_session_key:
                        unregister_gateway_notify(approval_session_key)
                except Exception:
                    pass
                self._run_streams.pop(run_id, None)
                self._run_streams_created.pop(run_id, None)
                self._active_run_agents.pop(run_id, None)
                self._active_run_tasks.pop(run_id, None)
                self._interrupted_run_ids.discard(run_id)
                self._approval_cancelled_run_ids.discard(run_id)
                self._run_approval_sessions.pop(run_id, None)

            stale_statuses = [
                run_id
                for run_id, status in list(self._run_statuses.items())
                if status.get("status") in {"completed", "failed", "cancelled"}
                and now - float(status.get("updated_at", 0) or 0) > self._RUN_STATUS_TTL
            ]
            for run_id in stale_statuses:
                self._run_statuses.pop(run_id, None)

    # ------------------------------------------------------------------
    # BasePlatformAdapter interface
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Start the aiohttp web server."""
        if not AIOHTTP_AVAILABLE:
            logger.warning("[%s] aiohttp not installed", self.name)
            return False

        try:
            mws = [mw for mw in (cors_middleware, body_limit_middleware, security_headers_middleware) if mw is not None]
            self._app = web.Application(middlewares=mws, client_max_size=MAX_REQUEST_BYTES)
            assert self._app is not None
            self._app.router.add_get("/health", self._handle_health)
            self._app.router.add_get("/health/detailed", self._handle_health_detailed)
            self._app.router.add_get("/v1/health", self._handle_health)
            self._app.router.add_get("/v1/license/status", self._handle_license_status)
            self._app.router.add_post("/v1/license/activate", self._handle_license_activate)
            self._app.router.add_get("/v1/models", self._handle_models)
            self._app.router.add_get("/v1/capabilities", self._handle_capabilities)
            self._app.router.add_get("/v1/skills", self._handle_skills)
            self._app.router.add_get("/v1/toolsets", self._handle_toolsets)
            # Session/client control surface (thin wrappers over SessionDB + _run_agent)
            self._app.router.add_get("/api/sessions", self._handle_list_sessions)
            self._app.router.add_post("/api/sessions", self._handle_create_session)
            self._app.router.add_get("/api/sessions/{session_id}", self._handle_get_session)
            self._app.router.add_patch("/api/sessions/{session_id}", self._handle_patch_session)
            self._app.router.add_delete("/api/sessions/{session_id}", self._handle_delete_session)
            self._app.router.add_get("/api/sessions/{session_id}/messages", self._handle_session_messages)
            self._app.router.add_post("/api/sessions/{session_id}/fork", self._handle_fork_session)
            self._app.router.add_post("/api/sessions/{session_id}/chat", self._handle_session_chat)
            self._app.router.add_post("/api/sessions/{session_id}/chat/stream", self._handle_session_chat_stream)
            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
            self._app.router.add_post("/v1/responses", self._handle_responses)
            self._app.router.add_get("/v1/responses/{response_id}", self._handle_get_response)
            self._app.router.add_delete("/v1/responses/{response_id}", self._handle_delete_response)
            # Cron jobs management API
            self._app.router.add_get("/api/jobs", self._handle_list_jobs)
            self._app.router.add_post("/api/jobs", self._handle_create_job)
            self._app.router.add_get("/api/jobs/{job_id}", self._handle_get_job)
            self._app.router.add_patch("/api/jobs/{job_id}", self._handle_update_job)
            self._app.router.add_delete("/api/jobs/{job_id}", self._handle_delete_job)
            self._app.router.add_post("/api/jobs/{job_id}/pause", self._handle_pause_job)
            self._app.router.add_post("/api/jobs/{job_id}/resume", self._handle_resume_job)
            self._app.router.add_post("/api/jobs/{job_id}/run", self._handle_run_job)
            # Structured event streaming
            self._app.router.add_post("/v1/runs", self._handle_runs)
            self._app.router.add_get("/v1/runs/{run_id}", self._handle_get_run)
            self._app.router.add_get("/v1/runs/{run_id}/events", self._handle_run_events)
            self._app.router.add_post("/v1/runs/{run_id}/approval", self._handle_run_approval)
            self._app.router.add_post("/v1/runs/{run_id}/stop", self._handle_stop_run)
            # Store the adapter after native routes are registered. Local Hermes-Relay
            # bootstrap shims use this key as a feature-detection hook; registering
            # native routes first lets those shims no-op instead of shadowing the
            # upstream session-control handlers.
            self._app["api_server_adapter"] = self

            # Start background sweep to clean up orphaned (unconsumed) run streams
            sweep_task = asyncio.create_task(self._sweep_orphaned_runs())
            try:
                self._background_tasks.add(sweep_task)
            except TypeError:
                pass
            if hasattr(sweep_task, "add_done_callback"):
                sweep_task.add_done_callback(self._background_tasks.discard)

            # Refuse to start without authentication. The API server can
            # dispatch terminal-capable agent work, so every deployment needs
            # an explicit API_SERVER_KEY regardless of bind address.
            if not self._api_key:
                logger.error(
                    "[%s] Refusing to start: API_SERVER_KEY is required for the API server, "
                    "including loopback-only binds on %s.",
                    self.name, self._host,
                )
                return False

            # Refuse to start network-accessible with a placeholder key.
            # Ported from openclaw/openclaw#64586.
            if is_network_accessible(self._host) and self._api_key:
                try:
                    from hermes_cli.auth import has_usable_secret
                    if not has_usable_secret(self._api_key, min_length=8):
                        logger.error(
                            "[%s] Refusing to start: API_SERVER_KEY is set to a "
                            "placeholder value. Generate a real secret "
                            "(e.g. `openssl rand -hex 32`) and set API_SERVER_KEY "
                            "before exposing the API server on %s.",
                            self.name, self._host,
                        )
                        return False
                except ImportError:
                    pass

            # Port conflict detection — fail fast if port is already in use
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
                    _s.settimeout(1)
                    _s.connect(('127.0.0.1', self._port))
                logger.error('[%s] Port %d already in use. Set a different port in config.yaml: platforms.api_server.port', self.name, self._port)
                return False
            except (ConnectionRefusedError, OSError):
                pass  # port is free

            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self._host, self._port)
            await self._site.start()

            self._mark_connected()
            logger.info(
                "[%s] API server listening on http://%s:%d (model: %s)",
                self.name, self._host, self._port, self._model_name,
            )
            return True

        except Exception as e:
            logger.error("[%s] Failed to start API server: %s", self.name, e)
            return False

    async def disconnect(self) -> None:
        """Stop the aiohttp web server."""
        self._mark_disconnected()
        self._enter_managed_lease_shutdown()
        try:
            try:
                await self.cancel_background_tasks()
                if self._site:
                    await self._site.stop()
                    self._site = None
                if self._runner:
                    await self._runner.cleanup()
                    self._runner = None
            finally:
                await self._drain_managed_lease_lifecycles()
        finally:
            self._exit_managed_lease_shutdown()
            self._app = None
        logger.info("[%s] API server stopped", self.name)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        Not used — HTTP request/response cycle handles delivery directly.
        """
        return SendResult(success=False, error="API server uses HTTP request/response, not send()")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic info about the API server."""
        return {
            "name": "API Server",
            "type": "api",
            "host": self._host,
            "port": self._port,
        }
