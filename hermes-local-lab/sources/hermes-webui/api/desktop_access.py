from __future__ import annotations

import hmac
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from api.helpers import j


_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_LAUNCH_ONLY_PAGE = (
    Path(__file__).resolve().parent.parent
    / "static"
    / "desktop-launch-only.html"
)
_LAUNCH_ONLY_MESSAGE = "请从桌面应用启动太极 Agent"
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE_ENV_VALUES


def desktop_access_required() -> bool:
    return _truthy_env("TAIJI_DESKTOP_ONLY")


def desktop_access_token() -> str:
    token = os.getenv("TAIJI_DESKTOP_ACCESS_TOKEN", "").strip()
    return token if _TOKEN_RE.fullmatch(token) else ""


def _cookie_value(cookie_header, name: str) -> str:
    if not cookie_header:
        return ""
    for part in str(cookie_header).split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value.strip()
    return ""


def request_has_desktop_access(handler, parsed) -> bool:
    expected = desktop_access_token()
    if not expected:
        return False

    candidates = []
    try:
        header_token = handler.headers.get("X-Taiji-Desktop-Token")
        if header_token:
            candidates.append(header_token)
    except Exception:
        pass
    try:
        cookie_token = _cookie_value(
            handler.headers.get("Cookie"),
            "taiji_desktop_token",
        )
        if cookie_token:
            candidates.append(cookie_token)
    except Exception:
        pass

    for candidate in candidates:
        rendered = str(candidate)
        if (
            len(rendered) == 64
            and _TOKEN_RE.fullmatch(rendered)
            and hmac.compare_digest(rendered, expected)
        ):
            handler._taiji_desktop_access_granted = True
            return True
    return False


def _desktop_access_exempt_path(path: str) -> bool:
    return path == "/health"


def _send_desktop_launch_only(handler, parsed) -> None:
    if parsed.path.startswith("/api/"):
        j(handler, {"error": _LAUNCH_ONLY_MESSAGE}, status=403)
        return

    body = _LAUNCH_ONLY_PAGE.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _query_has_desktop_token(parsed) -> bool:
    return any(
        key == "taiji_desktop_token"
        for key, _value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )


def _clean_request_location(parsed) -> str:
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(
                parsed.query,
                keep_blank_values=True,
            )
            if key != "taiji_desktop_token"
        ],
        doseq=True,
    )
    path = parsed.path or "/"
    if parsed.params:
        path = f"{path};{parsed.params}"
    return f"{path}?{query}" if query else path


def _redirect_without_query_token(handler, parsed) -> None:
    handler.send_response(303)
    handler.send_header("Location", _clean_request_location(parsed))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def enforce_desktop_access(handler, parsed) -> bool:
    if not desktop_access_required():
        return True
    if _desktop_access_exempt_path(parsed.path):
        return True
    # Query strings are not an authentication channel.  Sanitize a legacy or
    # hostile URL before any credential check, but never grant access or mint
    # a cookie from the value it carried.
    if _query_has_desktop_token(parsed):
        _redirect_without_query_token(handler, parsed)
        return False
    if request_has_desktop_access(handler, parsed):
        return True
    _send_desktop_launch_only(handler, parsed)
    return False
