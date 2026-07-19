"""Default-off Hermes Gateway bridge for browser-originated chat turns."""
from __future__ import annotations

import copy
import json
import hashlib
import logging
import os
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from api.config import (
    AGENT_INSTANCES,
    CANCEL_FLAGS,
    STREAMS,
    STREAMS_LOCK,
    STREAM_LAST_EVENT_ID,
    STREAM_LIVE_TOOL_CALLS,
    STREAM_PARTIAL_TEXT,
    STREAM_REASONING_TEXT,
    _get_session_agent_lock,
    register_active_run,
    unregister_active_run,
    update_active_run,
)
from api.helpers import _redact_text, redact_session_data
from api.brand_privacy import (
    BRAND_PRIVACY_SYSTEM_PROMPT,
    public_egress_scrub,
    sanitize_persisted_assistant_message,
    scrub_brand_leaks,
    scrub_public_session_payload,
    scrub_streaming_token_delta,
)
from api.models import (
    get_session,
    get_state_db_session_messages,
    reconciled_state_db_messages_for_session,
)
from api.run_journal import RunJournalWriter
from api.turn_envelope import TurnEnvelope
from api.turn_journal import append_turn_journal_event_for_stream
from api.turn_duration import stamp_turn_duration_on_latest_assistant
from api.streaming import (
    WebUIChatInputCancelled,
    WebUIChatInputError,
    _deduplicate_context_messages,
    _finalize_public_reasoning,
    _new_turn_context_from_messages,
    _persist_webui_chat_input_error,
    _sanitize_messages_for_api,
    prepare_webui_chat_input,
)

logger = logging.getLogger(__name__)


def _turn_message_sha256(content: Any) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()

_WEBUI_CHAT_BACKEND_ENV = "HERMES_WEBUI_CHAT_BACKEND"
_WEBUI_GATEWAY_BASE_URL_ENV = "HERMES_WEBUI_GATEWAY_BASE_URL"
_WEBUI_GATEWAY_API_KEY_ENV = "HERMES_WEBUI_GATEWAY_API_KEY"
_WEBUI_GATEWAY_CHAT_TRANSPORT_ENV = "HERMES_WEBUI_GATEWAY_CHAT_TRANSPORT"
_GATEWAY_CHAT_BACKENDS = {"gateway", "api_server", "api-server"}
_GATEWAY_RUN_FALLBACK_STATUSES = {404, 405, 501}


class _GatewayRunHandle:
    """Minimal cancel_stream-compatible handle for a remote managed run."""

    def __init__(self, session_id: str, cancel_event: threading.Event):
        self.session_id = session_id
        self._cancel_event = cancel_event
        self._lock = threading.Lock()
        self._base_url = ""
        self._headers: dict[str, str] = {}
        self._run_id = ""
        self._stop_requested = False

    def bind_transport(self, base_url: str, headers: dict[str, str]) -> None:
        with self._lock:
            self._base_url = base_url
            self._headers = dict(headers)
        self._dispatch_stop_if_ready()

    def bind_run(self, run_id: str) -> None:
        with self._lock:
            self._run_id = str(run_id or "")
        self._dispatch_stop_if_ready()

    def interrupt(self, message: str = "Cancelled by user") -> None:
        self._cancel_event.set()
        self._dispatch_stop_if_ready()

    def _dispatch_stop_if_ready(self) -> None:
        with self._lock:
            if (
                self._stop_requested
                or not self._cancel_event.is_set()
                or not self._base_url
                or not self._run_id
            ):
                return
            self._stop_requested = True
            base_url = self._base_url
            headers = dict(self._headers)
            run_id = self._run_id
        threading.Thread(
            target=_stop_gateway_run,
            args=(base_url, headers, run_id),
            name=f"gateway-stop-{run_id[:12]}",
            daemon=True,
        ).start()


def webui_chat_backend_mode(config_data=None, environ: dict[str, str] | None = None) -> str:
    """Return the explicitly selected browser chat backend.

    The default remains the in-process WebUI runtime. Only explicit gateway
    values opt browser chat into the Hermes API server bridge; generic truthy
    strings are deliberately ignored so deployments do not change execution
    ownership by accident.
    """
    source = os.environ if environ is None else environ
    cfg = config_data if isinstance(config_data, dict) else {}
    raw = str(
        source.get(_WEBUI_CHAT_BACKEND_ENV)
        or cfg.get("webui_chat_backend")
        or ""
    ).strip().lower()
    if raw in _GATEWAY_CHAT_BACKENDS:
        return "gateway"
    return "legacy"


def webui_gateway_chat_enabled(config_data=None, environ: dict[str, str] | None = None) -> bool:
    return webui_chat_backend_mode(config_data, environ) == "gateway"


def _gateway_base_url(config_data=None, environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    cfg = config_data if isinstance(config_data, dict) else {}
    raw = str(
        source.get(_WEBUI_GATEWAY_BASE_URL_ENV)
        or cfg.get("webui_gateway_base_url")
        or "http://127.0.0.1:8642"
    ).strip()
    return raw.rstrip("/") or "http://127.0.0.1:8642"


def gateway_chat_probe_base_url(config_data=None, environ: dict[str, str] | None = None) -> str | None:
    """Return the explicitly configured gateway chat base URL for health probes."""
    if not webui_gateway_chat_enabled(config_data, environ):
        return None
    source = os.environ if environ is None else environ
    cfg = config_data if isinstance(config_data, dict) else {}
    raw = str(
        source.get(_WEBUI_GATEWAY_BASE_URL_ENV)
        or cfg.get("webui_gateway_base_url")
        or ""
    ).strip()
    return raw.rstrip("/") if raw else None


def _gateway_api_key(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    return str(
        source.get(_WEBUI_GATEWAY_API_KEY_ENV)
        or source.get("API_SERVER_KEY")
        or ""
    ).strip()


def _gateway_chat_transport(config_data=None, environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    cfg = config_data if isinstance(config_data, dict) else {}
    raw = str(
        source.get(_WEBUI_GATEWAY_CHAT_TRANSPORT_ENV)
        or cfg.get("webui_gateway_chat_transport")
        or "runs"
    ).strip().lower()
    if raw in {"chat_completions", "chat-completions", "openai"}:
        return "chat_completions"
    return "runs"


def _gateway_request_headers(
    session_id: str,
    api_key: str,
    *,
    event_stream: bool = False,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Hermes-Session-Id": session_id,
    }
    headers["Accept"] = "text/event-stream" if event_stream else "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        # Scope Gateway long-term continuity to this WebUI conversation
        # without exposing the browser's auth cookie or CSRF material.
        headers["X-Hermes-Session-Key"] = f"webui:{session_id}"
    return headers


def gateway_chat_config_status(config_data=None, environ: dict[str, str] | None = None) -> dict:
    """Return redacted Gateway-backed chat configuration status."""
    mode = webui_chat_backend_mode(config_data, environ)
    base_url = _gateway_base_url(config_data, environ)
    return {
        "enabled": mode == "gateway",
        "backend": mode,
        "base_url_configured": bool(base_url),
        "api_key_configured": bool(_gateway_api_key(environ)),
    }


def _gateway_http_error_event(exc: urllib.error.HTTPError, err_body: str, *, api_key_configured: bool) -> dict:
    if exc.code == 401:
        return {
            "label": "本地对话服务认证失败",
            "type": "gateway_auth_error",
            "message": "本地对话服务认证失败（HTTP 401）。",
            "hint": "请重启太极智能体，或导出诊断报告后交给管理员排查。",
        }
    return {
        "label": "太极本地对话服务请求失败",
        "type": "gateway_http_error",
        "message": f"本地对话服务返回 HTTP {exc.code}。",
        "hint": "请检查太极智能体是否已启动，或导出诊断报告后交给管理员排查。",
    }


def _gateway_http_error_body(exc: urllib.error.HTTPError) -> str:
    """Read an HTTPError body once while keeping it available to outer handlers."""
    cached = getattr(exc, "_taiji_cached_body", None)
    if isinstance(cached, str):
        return cached
    try:
        body = exc.read(2048).decode("utf-8", errors="replace")
    except Exception:
        body = ""
    setattr(exc, "_taiji_cached_body", body)
    return body


def _gateway_run_fallback_allowed(exc: urllib.error.HTTPError) -> bool:
    """Fallback only when managed-run bootstrap endpoints are unavailable.

    A 404 from a managed-session lookup or from an accepted run's event stream
    is authoritative.  Replaying that turn through chat completions would
    silently fork history or execute the same user input twice.
    """
    error_url = str(getattr(exc, "url", "") or getattr(exc, "filename", "") or "")
    if urllib.parse.urlsplit(error_url).path.rstrip("/") not in {
        "/api/sessions",
        "/v1/runs",
    }:
        return False
    if exc.code not in _GATEWAY_RUN_FALLBACK_STATUSES:
        return False
    if exc.code != 404:
        return True
    body = _gateway_http_error_body(exc)
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        payload = {}
    raw_error = payload.get("error") if isinstance(payload, dict) else None
    code = str(raw_error.get("code") or "").strip() if isinstance(raw_error, dict) else ""
    return code != "session_not_found"


def _gateway_sse_finish_reason(payload: dict) -> str:
    try:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] or {}
        return str(choice.get("finish_reason") or "").strip().lower()
    except Exception:
        return ""


def _gateway_sse_error_event(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    raw_error = payload.get("error")
    finish_reason = _gateway_sse_finish_reason(payload)
    if not isinstance(raw_error, dict) and finish_reason != "error":
        return None
    code = ""
    message = ""
    if isinstance(raw_error, dict):
        code = str(raw_error.get("code") or raw_error.get("type") or "").strip().lower()
        message = str(raw_error.get("message") or "").strip()
    lowered = message.lower()
    if (
        code == "model_configuration_error"
        or ("api key" in lowered and ("no api key" in lowered or "not found" in lowered or "missing" in lowered))
        or ("provider" in lowered and "config" in lowered)
    ):
        return {
            "label": "模型服务配置不可用",
            "type": "model_configuration_error",
            "message": "模型服务未配置或不可用。请在配置页补充模型 API Key，或切换到可用模型。",
            "hint": "请检查太极智能体的模型配置、网络或账号余额状态。",
        }
    return {
        "label": "太极本地对话服务不可用",
        "type": "gateway_error",
        "message": "本地对话服务暂时不可用。",
        "hint": "请稍后重试，或导出诊断报告后交给管理员排查。",
    }


def _gateway_sse_delta(payload: dict) -> str:
    """Extract assistant text from an OpenAI-compatible streaming chunk."""
    try:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] or {}
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        message = choice.get("message") or {}
        content = message.get("content")
        return content if isinstance(content, str) else ""
    except Exception:
        return ""


def _gateway_sse_reasoning_delta(payload: dict) -> str:
    """Extract hidden reasoning text without mixing it into visible content."""
    try:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] or {}
        for container_name in ("delta", "message"):
            container = choice.get(container_name) or {}
            for key in ("reasoning_content", "reasoning"):
                value = container.get(key)
                if isinstance(value, str):
                    return value
        return ""
    except Exception:
        return ""


def _gateway_stream_usage(payload: dict) -> dict:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        "estimated_cost": usage.get("estimated_cost") or usage.get("estimated_cost_usd") or 0,
    }


def _gateway_tool_progress_event(payload: dict) -> tuple[str, dict] | None:
    """Translate Hermes Gateway tool-progress SSE payloads to WebUI events."""
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("tool") or payload.get("name") or payload.get("function_name") or "").strip()
    if not name or name.startswith("_"):
        return None
    status = str(payload.get("status") or "running").strip().lower()
    tid = payload.get("toolCallId") or payload.get("tool_call_id") or payload.get("id")
    is_complete = status in {"completed", "complete", "success", "error", "failed"}
    event_payload = {
        "event_type": "tool.completed" if is_complete else "tool.started",
        "name": name,
        "status": "failed" if status in {"error", "failed"} else ("completed" if is_complete else "running"),
        "is_error": status in {"error", "failed"},
    }
    if tid:
        event_payload["tid"] = str(tid)
    return ("tool_complete" if is_complete else "tool"), event_payload


def _gateway_image_artifact_candidate(payload: dict) -> dict | None:
    """Extract the private image candidate before public tool projection."""
    if not isinstance(payload, dict):
        return None
    from api.artifacts import image_artifact_candidate_from_tool_completion

    status = str(payload.get("status") or "").strip().lower()
    if status not in {"completed", "complete", "success"}:
        return None
    return image_artifact_candidate_from_tool_completion(
        tool_name=str(payload.get("tool") or payload.get("name") or ""),
        tool_call_id=str(
            payload.get("toolCallId")
            or payload.get("tool_call_id")
            or payload.get("id")
            or ""
        ),
        structured_result=payload.get("structured_result"),
        is_error=False,
    )


def _gateway_run_approval_payload(session_id: str, event: dict, *, run_id: str = "") -> dict:
    """Convert a Gateway run approval event into the WebUI pending shape."""
    payload = dict(event or {})
    gateway_run_id = str(payload.get("run_id") or run_id or "").strip()
    approval_id = str(payload.get("approval_id") or "").strip() or uuid.uuid4().hex
    pattern_keys = payload.get("pattern_keys")
    if not isinstance(pattern_keys, list):
        pattern_key = payload.get("pattern_key")
        pattern_keys = [pattern_key] if pattern_key else []
    pending = {
        "approval_id": approval_id,
        "_session_id": session_id,
        "_gateway_run_id": gateway_run_id,
        "command": str(payload.get("command") or ""),
        "description": str(payload.get("description") or ""),
        "pattern_key": str(payload.get("pattern_key") or (pattern_keys[0] if pattern_keys else "")),
        "pattern_keys": [str(key) for key in pattern_keys if key],
    }
    for key in (
        "approval_type",
        "kind",
        "capability",
        "allow_var",
        "title",
        "choices",
    ):
        if key in payload:
            pending[key] = payload[key]
    return pending


def _submit_gateway_run_approval_to_webui(session_id: str, event: dict, *, run_id: str = "") -> dict:
    """Store a Gateway approval event in the WebUI approval queue."""
    pending = _gateway_run_approval_payload(session_id, event, run_id=run_id)
    try:
        from api import routes as _routes

        _routes.submit_pending(session_id, pending)
    except Exception:
        logger.warning("Failed to submit gateway approval to WebUI queue", exc_info=True)
    return pending


def _clear_gateway_run_approvals_from_webui(session_id: str, run_id: str) -> None:
    if not session_id or not run_id:
        return
    try:
        from api import routes as _routes

        clear_fn = getattr(_routes, "clear_gateway_run_pending_approvals", None)
        if clear_fn is not None:
            clear_fn(session_id, run_id)
    except Exception:
        logger.debug("Failed to clear gateway approval from WebUI queue", exc_info=True)


def _stop_gateway_run(base_url: str, headers: dict[str, str], run_id: str) -> None:
    if not run_id:
        return
    url = f"{base_url}/v1/runs/{urllib.parse.quote(run_id, safe='')}/stop"
    req = urllib.request.Request(url, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        logger.debug("Failed to stop gateway run %s", run_id, exc_info=True)


def _mark_gateway_live_tool_complete(
    stream_id: str,
    *,
    tool_name: str,
    tool_call_id: str = "",
    is_error: bool = False,
) -> bool:
    """Close exactly one live tool call, preferring the stable call ID."""
    for shared_tc in reversed(STREAM_LIVE_TOOL_CALLS.get(stream_id, [])):
        if shared_tc.get("done"):
            continue
        if tool_call_id:
            if str(shared_tc.get("tid") or "") != tool_call_id:
                continue
        elif shared_tc.get("name") != tool_name:
            continue
        shared_tc["done"] = True
        shared_tc["is_error"] = bool(is_error)
        return True
    return False


def _gateway_run_approval_error_code(raw_body: str) -> str:
    try:
        body = json.loads(raw_body or "{}")
    except Exception:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("code") or err.get("type") or "").strip()
    return ""


def resolve_gateway_run_approval_result(approval: dict, choice: str) -> dict:
    """Resolve a WebUI approval card and keep stale-vs-retryable failure detail."""
    run_id = str((approval or {}).get("_gateway_run_id") or "").strip()
    session_id = str((approval or {}).get("_session_id") or "").strip()
    if not run_id:
        return {"resolved": False, "inactive": True, "code": "missing_run_id"}
    from api.config import get_config

    cfg = get_config()
    base_url = _gateway_base_url(cfg)
    api_key = _gateway_api_key()
    url = f"{base_url}/v1/runs/{urllib.parse.quote(run_id, safe='')}/approval"
    headers = _gateway_request_headers(session_id, api_key)
    req = urllib.request.Request(
        url,
        data=json.dumps({"choice": choice}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            try:
                body = json.loads(resp.read().decode("utf-8") or "{}")
            except Exception:
                body = {}
            resolved = body.get("resolved")
            return {
                "resolved": bool(resolved) if resolved is not None else 200 <= resp.status < 300,
                "inactive": False,
                "status": resp.status,
            }
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        code = _gateway_run_approval_error_code(err_body)
        inactive = exc.code in {404, 409} and code in {
            "run_not_found",
            "approval_not_active",
            "approval_not_pending",
        }
        logger.warning("Gateway run approval resolve failed: HTTP %s code=%s", exc.code, code or "unknown")
        return {
            "resolved": False,
            "inactive": inactive,
            "status": exc.code,
            "code": code,
        }
    except Exception:
        logger.warning("Gateway run approval resolve failed", exc_info=True)
        return {"resolved": False, "inactive": False, "code": "request_failed"}


def resolve_gateway_run_approval(approval: dict, choice: str) -> bool:
    """Backward-compatible boolean wrapper for Gateway run approval resolution."""
    return bool(resolve_gateway_run_approval_result(approval, choice).get("resolved"))


def _gateway_run_request_body(
    body: dict,
    *,
    session_id: str,
    ephemeral_messages: list[dict] | None = None,
) -> dict:
    """Build a managed /v1/runs request without duplicating WebUI history."""
    messages = list(body.get("messages") or [])
    system_parts: list[str] = []
    user_message: Any = ""
    non_system = [msg for msg in messages if isinstance(msg, dict) and msg.get("role") != "system"]
    if non_system:
        last = non_system[-1]
        user_message = last.get("content", "")
    instruction_source = (
        list(ephemeral_messages)
        if ephemeral_messages is not None
        else [
            msg for msg in messages
            if isinstance(msg, dict) and msg.get("role") == "system"
        ]
    )
    for msg in instruction_source:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = msg.get("content")
        if not content:
            continue
        if role == "system":
            system_parts.append(str(content))
            continue
        if role not in {"user", "assistant", "tool"}:
            continue
        if isinstance(content, str):
            rendered = content
        else:
            rendered = json.dumps(content, ensure_ascii=False, sort_keys=True)
        system_parts.append(
            "Ephemeral WebUI prefill context "
            f"(role={role}; treat as context, not conversation history):\n{rendered}"
        )
    model = str(body.get("model") or "").strip()
    provider = str(body.get("provider") or "").strip()
    run_body = {
        "input": user_message,
        "session_id": session_id,
    }
    if (
        model
        and provider
        and model.casefold() != "default"
        and provider.casefold() != "default"
    ):
        run_body["model"] = model
        run_body["provider"] = provider
    if body.get("platform_message_id"):
        run_body["platform_message_id"] = body.get("platform_message_id")
    if "checkpoint_content" in body:
        run_body["checkpoint_content"] = body.get("checkpoint_content")
    if system_parts:
        run_body["instructions"] = "\n\n".join(system_parts)
    return run_body


def _gateway_messages_for_new_turn(
    session,
    display_user_message: str,
    ephemeral_messages: list[dict],
    prepared_user_content: Any,
    *,
    cfg: dict | None = None,
    capability_generation=None,
    state_messages: list[dict] | None = None,
) -> list[dict]:
    """Build Gateway input through the standard WebUI context pipeline."""
    if state_messages is None:
        state_messages = get_state_db_session_messages(
            getattr(session, "session_id", None),
            profile=getattr(session, "profile", None) or None,
        )
    history = reconciled_state_db_messages_for_session(
        session,
        prefer_context=True,
        state_messages=state_messages,
    )
    history = _new_turn_context_from_messages(history, display_user_message)
    history = _deduplicate_context_messages(history)
    history = _sanitize_messages_for_api(
        history,
        cfg=cfg,
        capability_generation=capability_generation,
    )
    return [
        *[dict(message) for message in (ephemeral_messages or [])],
        *history,
        {"role": "user", "content": prepared_user_content},
    ]


def _gateway_run_error_event(payload: dict, default_message: str = "") -> dict:
    raw_error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(raw_error, dict):
        classified = _gateway_sse_error_event({"error": raw_error})
        if classified is not None:
            return classified
    return {
        "label": "太极本地对话服务不可用",
        "type": "gateway_error",
        "message": "本地对话服务暂时不可用。",
        "hint": "请稍后重试，或导出诊断报告后交给管理员排查。",
    }


def _gateway_history_messages(session: Any, current_user_text: str) -> list[dict]:
    """Return provider-visible WebUI history, excluding an eager current turn."""
    source = list(
        getattr(session, "context_messages", None)
        or getattr(session, "messages", None)
        or []
    )
    current_norm = " ".join(str(current_user_text or "").split())
    if source and isinstance(source[-1], dict) and source[-1].get("role") == "user":
        latest_norm = " ".join(str(source[-1].get("content") or "").split())
        if current_norm and latest_norm == current_norm:
            source = source[:-1]

    history: list[dict] = []
    for message in source:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant", "tool"}:
            continue
        item = {"role": role, "content": message.get("content", "")}
        for key in ("tool_calls", "tool_call_id", "name"):
            if message.get(key) is not None:
                item[key] = message.get(key)
        history.append(item)
    return history


def _ensure_gateway_managed_session(
    *,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    model: str,
) -> str:
    """Create the state.db session before /v1/runs; 409 means it exists."""
    request = urllib.request.Request(
        f"{base_url}/api/sessions",
        data=json.dumps({"id": session_id, "model": model or "default"}).encode("utf-8"),
        headers={**headers, "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return session_id
        raise

    actual = str(((payload.get("session") or {}).get("id") or "")).strip()
    if actual != session_id:
        raise RuntimeError("Managed session creation returned a different session ID")
    return actual


def _stream_gateway_run_events(
    *,
    base_url: str,
    headers: dict[str, str],
    body: dict,
    session_id: str,
    stream_id: str,
    cancel_event: threading.Event,
    brand_token_tail: list[str],
    put_gateway_event,
    run_handle: _GatewayRunHandle | None = None,
    ephemeral_messages: list[dict] | None = None,
) -> dict:
    """Run a Gateway /v1/runs turn and translate structured events to WebUI SSE."""
    if cancel_event.is_set():
        return {"final_text": "", "usage": {}, "error_event": None, "cancelled": True}
    run_req = urllib.request.Request(
        f"{base_url}/v1/runs",
        data=json.dumps(_gateway_run_request_body(
            body,
            session_id=session_id,
            ephemeral_messages=ephemeral_messages,
        )).encode("utf-8"),
        headers={**headers, "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(run_req, timeout=60) as resp:
        run_start = json.loads(resp.read().decode("utf-8") or "{}")
    run_id = str(run_start.get("run_id") or "").strip()
    actual_session_id = str(run_start.get("session_id") or "").strip()
    if run_handle is not None and run_id:
        run_handle.bind_run(run_id)
    if not run_id:
        return {
            "final_text": "",
            "public_final_text": "",
            "raw_final_text": "",
            "usage": {},
            "error_event": _gateway_run_error_event(run_start, "Gateway run did not return a run_id."),
        }
    if actual_session_id != session_id:
        _stop_gateway_run(base_url, headers, run_id)
        _clear_gateway_run_approvals_from_webui(session_id, run_id)
        return {
            "final_text": "",
            "usage": {},
            "error_event": _gateway_run_error_event(
                {"error": {"code": "session_id_conflict"}},
                "Gateway run returned an unexpected session ID.",
            ),
        }

    if cancel_event.is_set():
        _stop_gateway_run(base_url, headers, run_id)
        _clear_gateway_run_approvals_from_webui(session_id, run_id)
        return {"final_text": "", "usage": {}, "error_event": None, "cancelled": True}

    update_active_run(stream_id, phase="gateway-run", gateway_run_id=run_id)
    event_url = f"{base_url}/v1/runs/{urllib.parse.quote(run_id, safe='')}/events"
    event_req = urllib.request.Request(
        event_url,
        headers={**headers, "Accept": "text/event-stream"},
        method="GET",
    )
    raw_final_text = ""
    public_final_text = ""
    usage: dict[str, Any] = {}
    error_event = None
    saw_run_completed = False
    reasoning_buffer: list[str] = []
    artifact_candidates: list[dict] = []

    def emit_reasoning() -> None:
        safe_text = _finalize_public_reasoning("".join(reasoning_buffer))
        if safe_text:
            if stream_id in STREAM_REASONING_TEXT:
                STREAM_REASONING_TEXT[stream_id] += safe_text
            put_gateway_event("reasoning", {"text": safe_text})

    with urllib.request.urlopen(event_req, timeout=600) as resp:
        for raw_line in resp:
            if cancel_event.is_set():
                _stop_gateway_run(base_url, headers, run_id)
                _clear_gateway_run_approvals_from_webui(session_id, run_id)
                return {
                    "final_text": public_final_text,
                    "public_final_text": public_final_text,
                    "raw_final_text": raw_final_text,
                    "usage": usage,
                    "error_event": None,
                    "terminal_outcome": "cancelled",
                    "cancelled": True,
                    "artifact_candidates": artifact_candidates,
                }
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            event_name = str(payload.get("event") or "").strip()
            if event_name == "message.delta":
                raw_delta = str(payload.get("delta") or "")
                raw_final_text += raw_delta
                public_delta = scrub_streaming_token_delta(raw_delta, brand_token_tail)
                if public_delta:
                    public_final_text += public_delta
                    if stream_id in STREAM_PARTIAL_TEXT:
                        STREAM_PARTIAL_TEXT[stream_id] += public_delta
                    put_gateway_event("token", {"text": public_delta})
                continue
            if event_name == "reasoning.available":
                reasoning_buffer.append(str(payload.get("text") or ""))
                continue
            if event_name == "tool.started":
                tool_name = str(payload.get("tool") or "").strip()
                event_payload = {
                    "event_type": "tool.started",
                    "name": tool_name,
                    "status": "running",
                    "preview": payload.get("preview"),
                    "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
                }
                tool_call_id = str(payload.get("tool_call_id") or "").strip()
                if tool_call_id:
                    event_payload["tid"] = tool_call_id
                if stream_id in STREAM_LIVE_TOOL_CALLS:
                    STREAM_LIVE_TOOL_CALLS[stream_id].append({
                        "name": tool_name,
                        "args": event_payload["args"],
                        "done": False,
                        **({"tid": tool_call_id} if tool_call_id else {}),
                    })
                put_gateway_event("tool", event_payload)
                update_active_run(stream_id, phase="gateway-tool", latest_tool=tool_name)
                continue
            if event_name == "tool.completed":
                tool_name = str(payload.get("tool") or "").strip()
                artifact_candidate = _gateway_image_artifact_candidate({
                    **payload,
                    "status": "failed" if payload.get("error") else "completed",
                })
                if artifact_candidate is not None:
                    artifact_candidates.append(artifact_candidate)
                tool_call_id = str(payload.get("tool_call_id") or "").strip()
                _mark_gateway_live_tool_complete(
                    stream_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    is_error=bool(payload.get("error")),
                )
                put_gateway_event("tool_complete", {
                    "event_type": "tool.completed",
                    "name": tool_name,
                    "status": "failed" if payload.get("error") else "completed",
                    "duration": payload.get("duration"),
                    "is_error": bool(payload.get("error")),
                    **(
                        {"tid": tool_call_id}
                        if tool_call_id
                        else {}
                    ),
                })
                continue
            if event_name == "approval.request":
                pending = _submit_gateway_run_approval_to_webui(session_id, payload, run_id=run_id)
                put_gateway_event("approval", pending)
                update_active_run(stream_id, phase="gateway-approval", gateway_run_id=run_id)
                continue
            if event_name == "approval.responded":
                _clear_gateway_run_approvals_from_webui(session_id, run_id)
                continue
            if event_name == "run.completed":
                saw_run_completed = True
                raw_output = str(payload.get("output") or "").strip()
                if raw_output and not raw_final_text:
                    raw_final_text = raw_output
                    public_final_text = str(
                        public_egress_scrub(raw_output, surface="gateway_done_output")
                    ).strip()
                if isinstance(payload.get("usage"), dict):
                    usage.update(payload.get("usage") or {})
                _clear_gateway_run_approvals_from_webui(session_id, run_id)
                break
            if event_name == "run.cancelled":
                _clear_gateway_run_approvals_from_webui(session_id, run_id)
                return {
                    "final_text": public_final_text,
                    "public_final_text": public_final_text,
                    "raw_final_text": raw_final_text,
                    "usage": usage,
                    "error_event": None,
                    "terminal_outcome": "cancelled",
                    "cancelled": True,
                    "artifact_candidates": artifact_candidates,
                }
            if event_name == "run.failed":
                error_event = _gateway_run_error_event(payload, str(payload.get("error") or event_name))
                break
    if error_event is None and saw_run_completed:
        emit_reasoning()
    public_tail = scrub_streaming_token_delta("", brand_token_tail, final=True)
    if public_tail:
        public_final_text += public_tail
        if stream_id in STREAM_PARTIAL_TEXT:
            STREAM_PARTIAL_TEXT[stream_id] += public_tail
        put_gateway_event("token", {"text": public_tail})
    if error_event is None and not saw_run_completed:
        _stop_gateway_run(base_url, headers, run_id)
        _clear_gateway_run_approvals_from_webui(session_id, run_id)
        error_event = _gateway_run_error_event(
            {"error": {"code": "run_event_stream_incomplete"}},
            "Gateway run event stream ended before a terminal event.",
        )
    if error_event is not None:
        _clear_gateway_run_approvals_from_webui(session_id, run_id)
    return {
        "final_text": public_final_text,
        "public_final_text": public_final_text,
        "raw_final_text": raw_final_text,
        "usage": usage,
        "error_event": error_event,
        "terminal_outcome": "failed" if error_event is not None else "completed",
        "artifact_candidates": artifact_candidates,
    }


def _stream_writeback_is_current(session: Any, stream_id: str) -> bool:
    return bool(stream_id and getattr(session, "active_stream_id", None) == stream_id)


def _clear_gateway_pending_state(session: Any, stream_id: str) -> None:
    if not _stream_writeback_is_current(session, stream_id):
        return
    session.active_stream_id = None
    session.pending_user_message = None
    session.pending_attachments = None
    session.pending_started_at = None
    session.save()


def _ingest_gateway_artifact_candidates(
    session_id: str,
    turn_id: str,
    candidates: list[dict],
    owner_run_id: str,
) -> tuple[list[dict], list[str], set[str]]:
    """Promote validated image candidates without exposing their source path."""
    if not candidates:
        return [], [], set()
    from api.artifacts import (
        ArtifactRegistry,
        ingest_image_artifact_candidates,
    )
    from api.config import STATE_DIR

    registry = ArtifactRegistry(STATE_DIR / "artifacts")
    try:
        registry.cleanup_retired()
    except Exception:
        logger.debug("Failed to clean retired artifacts", exc_info=True)
    return ingest_image_artifact_candidates(
        registry,
        session_id=session_id,
        turn_id=turn_id,
        candidates=candidates,
        owner_run_id=owner_run_id,
        return_created_ids=True,
    )


def _remove_uncommitted_gateway_artifacts(
    session_id: str, artifact_ids: set[str], owner_run_id: str
) -> None:
    if not artifact_ids:
        return
    from api.artifacts import ArtifactRegistry
    from api.config import STATE_DIR

    ArtifactRegistry(STATE_DIR / "artifacts").discard_pending_artifacts(
        session_id, artifact_ids, owner_run_id=owner_run_id
    )


def _commit_gateway_artifacts(
    session_id: str, artifact_ids: set[str], owner_run_id: str
) -> None:
    if not artifact_ids:
        return
    from api.artifacts import ArtifactRegistry
    from api.config import STATE_DIR

    ArtifactRegistry(STATE_DIR / "artifacts").commit_artifacts(
        session_id, artifact_ids, owner_run_id=owner_run_id
    )


def _run_gateway_chat_streaming(
    session_id,
    msg_text,
    model,
    workspace,
    stream_id,
    attachments=None,
    *,
    model_provider=None,
    display_msg=None,
    turn_id=None,
    turn_envelope=None,
):
    """Bridge a WebUI chat turn through Hermes Gateway's API server.

    This default-off path keeps the browser contract unchanged: /api/chat/start
    still returns a local stream_id and /api/chat/stream still receives WebUI SSE
    event names. The worker translates OpenAI-compatible streaming chunks from
    the configured Gateway API server into those local events and persists the
    final user/assistant turn back into the WebUI session.
    """
    q = STREAMS.get(stream_id)
    if q is None:
        # cancel_stream() can win the race immediately after the route starts
        # this worker: it removes STREAMS before we have registered a remote
        # handle, so the normal cancel path has no session_id to finalize.
        # Preserve the pending user turn and cancellation marker here, but only
        # while this worker still owns the session stream.
        try:
            with _get_session_agent_lock(session_id):
                cancelled_session = get_session(session_id)
                if _stream_writeback_is_current(cancelled_session, stream_id):
                    from api.streaming import _finalize_cancelled_turn

                    _finalize_cancelled_turn(
                        cancelled_session,
                        message="Task cancelled before gateway worker start.",
                    )
        except Exception:
            logger.debug(
                "Failed to finalize gateway cancellation before worker registration",
                exc_info=True,
            )
        return
    persist_msg_text = display_msg if display_msg is not None else msg_text
    register_active_run(
        stream_id,
        session_id=session_id,
        started_at=time.time(),
        phase="gateway-starting",
        workspace=str(workspace),
        model=model,
        provider=model_provider,
        backend="gateway",
    )
    try:
        run_journal = RunJournalWriter(session_id, stream_id)
    except Exception:
        run_journal = None
        logger.debug("Failed to initialize gateway run journal for stream %s", stream_id, exc_info=True)
    cancel_event = threading.Event()
    turn_terminal_recorded = False
    run_handle = _GatewayRunHandle(session_id, cancel_event)
    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event
        AGENT_INSTANCES[stream_id] = run_handle
        STREAM_PARTIAL_TEXT[stream_id] = ""
        STREAM_REASONING_TEXT[stream_id] = ""
        STREAM_LIVE_TOOL_CALLS[stream_id] = []

    def put_gateway_event(event, data):
        if cancel_event.is_set() and event not in ("cancel", "error", "apperror"):
            return
        if event == "done" and isinstance(data, dict) and isinstance(data.get("session"), dict):
            data = {
                **data,
                "session": scrub_public_session_payload(data.get("session")),
            }
        data = public_egress_scrub(data, surface="gateway_stream", event_name=event)
        if run_journal is not None:
            try:
                journaled = run_journal.append_sse_event(event, data)
                event_id = (journaled or {}).get("event_id") if isinstance(journaled, dict) else None
                if event_id:
                    STREAM_LAST_EVENT_ID[stream_id] = event_id
            except Exception:
                logger.debug("Failed to append gateway event %s for stream %s", event, stream_id, exc_info=True)
        try:
            q.put_nowait((event, data))
        except Exception:
            logger.debug("Failed to put gateway event to queue")

    def record_turn_interrupted(reason: str) -> None:
        nonlocal turn_terminal_recorded
        if turn_terminal_recorded:
            return
        try:
            append_turn_journal_event_for_stream(
                session_id,
                stream_id,
                {
                    "event": "interrupted",
                    "turn_id": str(turn_id or ""),
                    "created_at": time.time(),
                    "reason": str(reason or "failed"),
                },
            )
            turn_terminal_recorded = True
        except Exception:
            logger.warning("Failed to append Gateway interrupted turn journal event", exc_info=True)

    s = None
    raw_final_text = ""
    public_final_text = ""
    public_reasoning_text = ""
    raw_reasoning_buffer: list[str] = []
    artifact_candidates: list[dict] = []
    uncommitted_artifact_ids: set[str] = set()
    artifacts_committed = False
    chat_stream_completed = False
    brand_token_tail = [""]
    usage = {"input_tokens": 0, "output_tokens": 0, "estimated_cost": 0}
    try:
        s = get_session(session_id)
        from api.config import get_config  # imported lazily to avoid config-cycle churn
        from agent.image_runtime import (
            capture_capability_runtime_generation,
        )

        cfg = get_config()
        capability_generation = (
            capture_capability_runtime_generation()
        )
        try:
            from api.streaming import (
                _WEBUI_PROGRESS_PROMPT,
                _load_webui_prefill_context,
                _prefill_messages_with_webui_context,
                _public_prefill_context_status,
            )

            prefill_context = _load_webui_prefill_context(cfg)
            prefill_messages = [
                {"role": "system", "content": f"{BRAND_PRIVACY_SYSTEM_PROMPT}\n\n{_WEBUI_PROGRESS_PROMPT}"},
                *_prefill_messages_with_webui_context(prefill_context, cfg),
            ]
            put_gateway_event("context_status", {
                "session_id": session_id,
                "prefill": _public_prefill_context_status(prefill_context),
            })
        except Exception:
            logger.debug("Failed to load WebUI gateway prefill context", exc_info=True)
            prefill_messages = []
        base_url = _gateway_base_url(cfg)
        api_key = _gateway_api_key()
        url = f"{base_url}/v1/chat/completions"
        headers = _gateway_request_headers(session_id, api_key, event_stream=True)
        run_handle.bind_transport(base_url, headers)
        if cancel_event.is_set():
            from api.streaming import cancel_stream

            cancel_stream(stream_id)
            return
        message_text = str(msg_text or "")
        try:
            message_content: Any = prepare_webui_chat_input(
                message_text,
                attachments,
                workspace=str(workspace),
                session_id=session_id,
                cfg=cfg,
                provider=model_provider,
                model=model,
                cancel_check=cancel_event.is_set,
                capability_generation=capability_generation,
            )
        except WebUIChatInputCancelled:
            record_turn_interrupted("cancelled")
            put_gateway_event("cancel", {"message": "Cancelled by user"})
            return
        except WebUIChatInputError as exc:
            with _get_session_agent_lock(session_id):
                _persist_webui_chat_input_error(s, stream_id, exc.payload)
            record_turn_interrupted(str(exc.payload.get("type") or "chat_input_error"))
            put_gateway_event("apperror", exc.payload)
            return
        if cancel_event.is_set():
            record_turn_interrupted("cancelled")
            put_gateway_event("cancel", {"message": "Cancelled by user"})
            return
        model_messages = _gateway_messages_for_new_turn(
            s,
            str(persist_msg_text or ""),
            prefill_messages,
            message_content,
            cfg=cfg,
            capability_generation=capability_generation,
        )
        if turn_envelope is None:
            turn_envelope = TurnEnvelope.create(
                turn_id=str(turn_id or uuid.uuid4().hex),
                session_id=session_id,
                submitted_at=getattr(s, "pending_started_at", None) or time.time(),
                display_user_message=str(persist_msg_text or ""),
                model_messages=model_messages,
                attachments=attachments,
            )
        else:
            turn_envelope = turn_envelope.with_model_messages(model_messages)
        model_messages = [copy.deepcopy(message) for message in turn_envelope.model_messages]
        body = {
            "model": model or "default",
            "stream": True,
            "messages": model_messages,
            "platform_message_id": turn_envelope.platform_message_id,
        }
        if model_provider:
            body["provider"] = model_provider
        update_active_run(stream_id, phase="gateway-request")
        last_payload = {}
        gateway_error_event = None
        sse_event = "message"
        run_result = None
        if _gateway_chat_transport(cfg) == "runs":
            try:
                _ensure_gateway_managed_session(
                    base_url=base_url,
                    headers=headers,
                    session_id=session_id,
                    model=model or "default",
                )
                run_result = _stream_gateway_run_events(
                    base_url=base_url,
                    headers=headers,
                    body={
                        **body,
                        "checkpoint_content": turn_envelope.display_user_message,
                    },
                    session_id=session_id,
                    stream_id=stream_id,
                    cancel_event=cancel_event,
                    brand_token_tail=brand_token_tail,
                    put_gateway_event=put_gateway_event,
                    run_handle=run_handle,
                    ephemeral_messages=prefill_messages,
                )
            except urllib.error.HTTPError as exc:
                if not _gateway_run_fallback_allowed(exc):
                    raise
                logger.info(
                    "Gateway managed-run transport unavailable (HTTP %s), "
                    "falling back to chat completions",
                    exc.code,
                )
                run_result = None
        if run_result is not None:
            if run_result.get("cancelled"):
                record_turn_interrupted("cancelled")
                from api.streaming import cancel_stream

                cancel_stream(stream_id)
                return
            raw_final_text = str(run_result.get("raw_final_text") or "")
            public_final_text = str(
                run_result.get("public_final_text")
                or run_result.get("final_text")
                or ""
            )
            usage.update({k: v for k, v in (run_result.get("usage") or {}).items() if v})
            gateway_error_event = run_result.get("error_event")
            artifact_candidates.extend(run_result.get("artifact_candidates") or [])
            if str(run_result.get("terminal_outcome") or "") == "cancelled":
                record_turn_interrupted("cancelled")
                put_gateway_event("cancel", {"message": "Cancelled by user"})
                return
        else:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                for raw_line in resp:
                    if cancel_event.is_set():
                        record_turn_interrupted("cancelled")
                        put_gateway_event("cancel", {"message": "Cancelled by user"})
                        return
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        sse_event = "message"
                        continue
                    if line.startswith("event:"):
                        sse_event = line[6:].strip() or "message"
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        chat_stream_completed = True
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if sse_event == "hermes.tool.progress":
                        artifact_candidate = _gateway_image_artifact_candidate(payload)
                        if artifact_candidate is not None:
                            artifact_candidates.append(artifact_candidate)
                        translated = _gateway_tool_progress_event(payload)
                        if translated:
                            event_name, event_payload = translated
                            if stream_id in STREAM_LIVE_TOOL_CALLS:
                                if event_name == "tool":
                                    STREAM_LIVE_TOOL_CALLS[stream_id].append({
                                        "name": event_payload.get("name"),
                                        "args": event_payload.get("args") or {},
                                        "done": False,
                                        **({"tid": event_payload.get("tid")} if event_payload.get("tid") else {}),
                                    })
                                else:
                                    _mark_gateway_live_tool_complete(
                                        stream_id,
                                        tool_name=str(event_payload.get("name") or ""),
                                        tool_call_id=str(event_payload.get("tid") or ""),
                                        is_error=bool(event_payload.get("is_error")),
                                    )
                            put_gateway_event(event_name, event_payload)
                            update_active_run(stream_id, phase="gateway-tool", latest_tool=event_payload.get("name"))
                        sse_event = "message"
                        continue
                    last_payload = payload
                    error_event = _gateway_sse_error_event(payload)
                    if error_event:
                        if gateway_error_event is None or error_event.get("type") == "model_configuration_error":
                            gateway_error_event = error_event
                        update_active_run(stream_id, phase="gateway-error")
                        usage.update({k: v for k, v in _gateway_stream_usage(payload).items() if v})
                        continue
                    reasoning_delta = _gateway_sse_reasoning_delta(payload)
                    if reasoning_delta:
                        raw_reasoning_buffer.append(reasoning_delta)
                    delta = _gateway_sse_delta(payload)
                    if delta:
                        raw_final_text += delta
                        public_delta = scrub_streaming_token_delta(delta, brand_token_tail)
                        if not public_delta:
                            usage.update({k: v for k, v in _gateway_stream_usage(payload).items() if v})
                            continue
                        public_final_text += public_delta
                        if stream_id in STREAM_PARTIAL_TEXT:
                            STREAM_PARTIAL_TEXT[stream_id] += public_delta
                        put_gateway_event("token", {"text": public_delta})
                    usage.update({k: v for k, v in _gateway_stream_usage(payload).items() if v})
            if chat_stream_completed and gateway_error_event is None:
                public_reasoning_text = _finalize_public_reasoning(
                    "".join(raw_reasoning_buffer)
                )
                if public_reasoning_text:
                    if stream_id in STREAM_REASONING_TEXT:
                        STREAM_REASONING_TEXT[stream_id] += public_reasoning_text
                    put_gateway_event("reasoning", {"text": public_reasoning_text})
        tail_delta = scrub_streaming_token_delta("", brand_token_tail, final=True)
        if tail_delta:
            public_final_text += tail_delta
            if stream_id in STREAM_PARTIAL_TEXT:
                STREAM_PARTIAL_TEXT[stream_id] += tail_delta
            put_gateway_event("token", {"text": tail_delta})
        usage.update({k: v for k, v in _gateway_stream_usage(last_payload).items() if v})
        if gateway_error_event:
            record_turn_interrupted(str(gateway_error_event.get("type") or "gateway_error"))
            put_gateway_event("apperror", gateway_error_event)
            return
        internal_assistant_text = str(raw_final_text or "").strip()
        public_assistant_text = str(public_final_text or "").strip()
        if not internal_assistant_text:
            record_turn_interrupted("gateway_empty_response")
            put_gateway_event("apperror", {
                "label": "太极本地对话服务未返回内容",
                "type": "gateway_empty_response",
                "message": "本地对话服务没有返回有效回复。",
                "hint": "请检查模型配置、网络或账号余额状态，必要时导出诊断报告。",
            })
            return
        artifacts, artifact_errors, uncommitted_artifact_ids = _ingest_gateway_artifact_candidates(
            session_id,
            str(turn_id or turn_envelope.turn_id),
            artifact_candidates,
            stream_id,
        )
        with _get_session_agent_lock(session_id):
            s = get_session(session_id)
            if not _stream_writeback_is_current(s, stream_id):
                return
            writeback_snapshot = {
                field: copy.deepcopy(getattr(s, field, None))
                for field in (
                    "messages", "context_messages", "active_stream_id",
                    "pending_user_message", "pending_attachments",
                    "pending_started_at", "workspace", "model",
                    "model_provider", "updated_at",
                )
            }
            turn_started_at = getattr(s, "pending_started_at", None)
            now = time.time()
            # Preserve subsecond ordering for gateway-backed turns. Using an
            # integer seconds timestamp gives the user and assistant rows the
            # same sort key; later transcript merges can then fall back to
            # role/content ordering instead of turn order.
            assistant_ts = now + 0.000001
            user_msg = {
                "role": "user",
                "content": str(persist_msg_text or ""),
                "timestamp": now,
                "platform_message_id": turn_envelope.platform_message_id,
            }
            if attachments:
                user_msg["attachments"] = list(attachments)
            internal_assistant_msg = {
                "role": "assistant",
                "content": internal_assistant_text,
                "timestamp": assistant_ts,
            }
            if artifacts:
                internal_assistant_msg["artifacts"] = copy.deepcopy(artifacts)
            assistant_msg = sanitize_persisted_assistant_message({
                **internal_assistant_msg,
                "content": public_assistant_text,
            })
            if artifacts:
                assistant_msg["artifacts"] = copy.deepcopy(artifacts)
            if artifact_errors:
                assistant_msg["artifact_errors"] = list(artifact_errors)
            assistant_text = str(assistant_msg.get("content") or "")
            previous_context = list(getattr(s, "context_messages", None) or getattr(s, "messages", None) or [])
            s.context_messages = list(previous_context + [user_msg, internal_assistant_msg])
            display = list(getattr(s, "messages", None) or [])
            # Avoid duplicating the eager-save checkpointed user message.
            if display:
                latest = display[-1]
                if isinstance(latest, dict) and latest.get("role") == "user":
                    latest_text = " ".join(str(latest.get("content") or "").split())
                    msg_norm = " ".join(str(persist_msg_text or "").split())
                    if latest_text == msg_norm:
                        display = display[:-1]
            s.messages = display + [user_msg, assistant_msg]
            assistant_message_index = next(
                (idx for idx in range(len(s.messages) - 1, -1, -1)
                 if isinstance(s.messages[idx], dict) and s.messages[idx].get("role") == "assistant"),
                None,
            )
            user_message_index = (
                assistant_message_index - 1
                if isinstance(assistant_message_index, int)
                and assistant_message_index > 0
                and isinstance(s.messages[assistant_message_index - 1], dict)
                and s.messages[assistant_message_index - 1].get("role") == "user"
                else None
            )
            lifecycle_identity = {
                "assistant_message_index": assistant_message_index,
                "assistant_content_sha256": _turn_message_sha256(assistant_text),
                "user_message_index": user_message_index,
                "user_content_sha256": _turn_message_sha256(
                    s.messages[user_message_index].get("content")
                    if isinstance(user_message_index, int) else ""
                ),
            }
            duration_seconds = stamp_turn_duration_on_latest_assistant(s, turn_started_at, time.time())
            if duration_seconds is not None:
                usage["duration_seconds"] = duration_seconds
            s.active_stream_id = None
            s.pending_user_message = None
            s.pending_attachments = None
            s.pending_started_at = None
            s.workspace = str(workspace)
            s.model = model
            s.model_provider = model_provider
            try:
                append_turn_journal_event_for_stream(
                    s.session_id,
                    stream_id,
                    {
                        "event": "assistant_started",
                        "turn_id": str(turn_id or ""),
                        "created_at": assistant_ts,
                        **lifecycle_identity,
                    },
                )
            except Exception:
                logger.warning("Failed to append Gateway assistant_started turn journal event", exc_info=True)
            try:
                _commit_gateway_artifacts(
                    session_id, uncommitted_artifact_ids, stream_id
                )
                artifacts_committed = True
                s.save()
            except BaseException:
                # A failed commit must never persist a dangling reference.  A
                # failed save after commit leaves a durable orphan for later
                # reconciliation, but the live Session projection is restored.
                for field, value in writeback_snapshot.items():
                    setattr(s, field, value)
                raise
            try:
                append_turn_journal_event_for_stream(
                    s.session_id,
                    stream_id,
                    {
                        "event": "completed",
                        "turn_id": str(turn_id or ""),
                        "created_at": time.time(),
                        **lifecycle_identity,
                    },
                )
                turn_terminal_recorded = True
            except Exception:
                logger.warning("Failed to append Gateway completed turn journal event", exc_info=True)
        gateway_session_payload = scrub_public_session_payload(s.compact() | {"messages": s.messages, "tool_calls": []})
        put_gateway_event("done", {"session": redact_session_data(gateway_session_payload), "usage": usage})
        put_gateway_event("stream_end", {"session_id": session_id})
    except urllib.error.HTTPError as exc:
        if cancel_event.is_set():
            record_turn_interrupted("cancelled")
            return
        record_turn_interrupted("gateway_http_error")
        err_body = _gateway_http_error_body(exc)
        put_gateway_event(
            "apperror",
            scrub_brand_leaks(_gateway_http_error_event(exc, err_body, api_key_configured=bool(_gateway_api_key()))),
        )
    except Exception as exc:
        if cancel_event.is_set():
            record_turn_interrupted("cancelled")
            return
        record_turn_interrupted("gateway_error")
        safe = scrub_brand_leaks(_redact_text(str(exc))[:500])
        put_gateway_event("apperror", {
            "label": "太极本地对话服务请求失败",
            "type": "gateway_error",
            "message": safe or "本地对话服务请求失败。",
            "hint": "请检查太极智能体是否已启动，或导出诊断报告。",
        })
    finally:
        if uncommitted_artifact_ids and not artifacts_committed:
            try:
                _remove_uncommitted_gateway_artifacts(
                    session_id, uncommitted_artifact_ids, stream_id
                )
            except Exception:
                logger.critical(
                    "Failed to remove uncommitted Gateway artifacts for %s",
                    session_id,
                    exc_info=True,
                )
        if s is not None:
            try:
                with _get_session_agent_lock(session_id):
                    _clear_gateway_pending_state(get_session(session_id), stream_id)
            except Exception:
                logger.debug("Failed to clear gateway stream state", exc_info=True)
        with STREAMS_LOCK:
            CANCEL_FLAGS.pop(stream_id, None)
            if AGENT_INSTANCES.get(stream_id) is run_handle:
                AGENT_INSTANCES.pop(stream_id, None)
            STREAM_PARTIAL_TEXT.pop(stream_id, None)
            STREAM_REASONING_TEXT.pop(stream_id, None)
            STREAM_LIVE_TOOL_CALLS.pop(stream_id, None)
            STREAM_LAST_EVENT_ID.pop(stream_id, None)
            STREAMS.pop(stream_id, None)
        unregister_active_run(stream_id)
