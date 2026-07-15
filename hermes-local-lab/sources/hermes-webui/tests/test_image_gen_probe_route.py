from __future__ import annotations

import io
import json
from types import SimpleNamespace


class _Handler:
    def __init__(self):
        self.rfile = io.BytesIO(b"{}")
        self.wfile = self
        self.headers = {"Content-Length": "2"}
        self.status = None
        self.body = bytearray()

    def send_response(self, status):
        self.status = status

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)


def test_image_gen_probe_post_route_returns_probe_payload(monkeypatch):
    from api import model_config, routes

    monkeypatch.setattr(routes, "_check_csrf", lambda *_: True)
    monkeypatch.setattr(
        model_config,
        "test_image_gen_config",
        lambda: {"ok": True, "status": "verified"},
    )
    handler = _Handler()

    handled = routes.handle_post(
        handler, SimpleNamespace(path="/api/image-gen/test")
    )

    assert handled is None
    assert handler.status == 200
    assert json.loads(handler.body) == {"ok": True, "status": "verified"}


def test_image_gen_probe_post_route_maps_unexpected_error_without_leak(monkeypatch):
    from api import model_config, routes

    monkeypatch.setattr(routes, "_check_csrf", lambda *_: True)

    def fail():
        raise RuntimeError("secret-key /private/provider/path")

    monkeypatch.setattr(model_config, "test_image_gen_config", fail)
    handler = _Handler()

    handled = routes.handle_post(
        handler, SimpleNamespace(path="/api/image-gen/test")
    )

    assert handled is None
    assert handler.status == 500
    payload = json.loads(handler.body)
    assert payload["error"] == "生图验证暂时无法执行，请稍后重试。"
    assert "secret-key" not in json.dumps(payload)
    assert "/private/provider/path" not in json.dumps(payload)
