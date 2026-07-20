from __future__ import annotations

import logging
import os
import re


logger = logging.getLogger(__name__)

_CONNECT_BASE = (
    "'self' http://127.0.0.1:* http://localhost:* "
    "ws://127.0.0.1:* ws://localhost:*"
)
_EXTRA_CONNECT_RE = re.compile(
    r"^(?:https?|wss?)://(?:\*\.)?[A-Za-z0-9._~-]+"
    r"(?::(?P<port>\d{1,5}|\*))?$"
)
_LEGACY_EXTRA_CONNECT_ENV = "HER" + "MES_WEBUI_CSP_CONNECT_EXTRA"


def _valid_extra_connect_source(source: str) -> bool:
    match = _EXTRA_CONNECT_RE.fullmatch(source)
    if not match:
        return False
    port = match.group("port")
    if not port or port == "*":
        return True
    try:
        return 1 <= int(port) <= 65535
    except ValueError:
        return False


def _extra_connect_src() -> str:
    raw = (
        os.getenv("TAIJI_WEBUI_CSP_CONNECT_EXTRA")
        or os.getenv(_LEGACY_EXTRA_CONNECT_ENV, "")
    ).strip()
    if not raw:
        return ""
    sources = raw.split()
    if not sources or any(
        not _valid_extra_connect_source(source) for source in sources
    ):
        logger.warning(
            "Ignoring invalid TAIJI_WEBUI_CSP_CONNECT_EXTRA value"
        )
        return ""
    return " " + " ".join(sources)


def build_csp_report_only_policy() -> str:
    connect_src = _CONNECT_BASE + _extra_connect_src()
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "media-src 'self' data: blob:; "
        f"connect-src {connect_src}; "
        "report-uri /api/csp-report; report-to csp-endpoint"
    )
