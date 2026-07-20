from __future__ import annotations

import io
import json
from urllib.parse import urlparse


TOKEN = "a" * 64


class _Handler:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = []
        self.ended = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        self.ended = True


def test_desktop_access_accepts_header_and_cookie_but_never_query_token(monkeypatch):
    from api.desktop_access import request_has_desktop_access

    monkeypatch.setenv("TAIJI_DESKTOP_ACCESS_TOKEN", TOKEN)
    denied = _Handler({"X-Taiji-Desktop-Token": "wrong"})
    assert not request_has_desktop_access(denied, urlparse("/"))

    query = _Handler()
    assert not request_has_desktop_access(
        query, urlparse(f"/?taiji_desktop_token={TOKEN}")
    )
    assert not hasattr(query, "_taiji_desktop_access_granted")

    header = _Handler({"X-Taiji-Desktop-Token": TOKEN})
    assert request_has_desktop_access(header, urlparse("/"))
    assert header._taiji_desktop_access_granted is True

    cookie = _Handler({"Cookie": f"theme=dark; taiji_desktop_token={TOKEN}"})
    assert request_has_desktop_access(cookie, urlparse("/"))
    assert cookie._taiji_desktop_access_granted is True


def test_desktop_access_rejects_non_hex_and_unicode_tokens(monkeypatch):
    from api.desktop_access import request_has_desktop_access

    monkeypatch.setenv("TAIJI_DESKTOP_ACCESS_TOKEN", TOKEN)
    for candidate in ("中", "g" * 64, "a" * 63, "a" * 65):
        handler = _Handler({"X-Taiji-Desktop-Token": candidate})
        assert not request_has_desktop_access(handler, urlparse("/"))


def test_query_token_never_authenticates_and_only_redirects_to_a_clean_url(monkeypatch):
    from api.desktop_access import enforce_desktop_access

    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_DESKTOP_ACCESS_TOKEN", TOKEN)
    handler = _Handler()

    assert not enforce_desktop_access(
        handler,
        urlparse(
            f"/?taiji_desktop=1&taiji_desktop_token={TOKEN}&keep=yes"
        ),
    )
    assert handler.status == 303
    headers = dict(handler.response_headers)
    assert headers["Location"] == "/?taiji_desktop=1&keep=yes"
    assert headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in headers
    assert not hasattr(handler, "_taiji_desktop_access_granted")


def test_desktop_access_health_is_exempt_but_other_routes_are_not(monkeypatch):
    from api.desktop_access import enforce_desktop_access

    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_DESKTOP_ACCESS_TOKEN", TOKEN)

    health = _Handler()
    assert enforce_desktop_access(health, urlparse("/health"))
    assert health.status is None

    api = _Handler()
    assert not enforce_desktop_access(api, urlparse("/api/settings"))
    assert api.status == 403
    payload = json.loads(api.wfile.getvalue())
    assert payload == {"error": "请从桌面应用启动太极 Agent"}


def test_desktop_launch_only_page_is_static_and_contains_no_token(monkeypatch):
    from api.desktop_access import enforce_desktop_access

    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "true")
    monkeypatch.setenv("TAIJI_DESKTOP_ACCESS_TOKEN", TOKEN)
    handler = _Handler()

    assert not enforce_desktop_access(handler, urlparse("/"))
    body = handler.wfile.getvalue().decode("utf-8")
    assert handler.status == 200
    assert "<!doctype html>" in body.lower()
    assert "请从桌面应用启动太极 Agent" in body
    assert TOKEN not in body
    assert handler.ended


def test_authorized_handler_sets_a_strict_http_only_cookie(monkeypatch):
    from server import Handler

    monkeypatch.setenv("TAIJI_DESKTOP_ONLY", "1")
    monkeypatch.setenv("TAIJI_DESKTOP_ACCESS_TOKEN", TOKEN)
    handler = object.__new__(Handler)
    handler._headers_buffer = []
    handler.wfile = io.BytesIO()
    handler.request_version = "HTTP/1.1"
    handler._taiji_desktop_access_granted = True

    handler.end_headers()

    response = handler.wfile.getvalue().decode("latin-1")
    assert (
        f"Set-Cookie: taiji_desktop_token={TOKEN}; "
        "Path=/; SameSite=Strict; HttpOnly"
    ) in response
