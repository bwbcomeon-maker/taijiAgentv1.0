"""Regression guard: tests must never open the user's real browser."""

import importlib.util
from pathlib import Path
import socket
import webbrowser

import pytest


def _load_browser_smoke_module():
    smoke_path = Path(__file__).with_name("browser_smoke.py")
    spec = importlib.util.spec_from_file_location("taiji_browser_smoke", smoke_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("method_name", ["open", "open_new", "open_new_tab"])
def test_external_browser_calls_fail_closed(method_name):
    with pytest.raises(AssertionError, match="must not open"):
        getattr(webbrowser, method_name)("https://accounts.example.invalid/oauth")


def test_browser_smoke_aborts_every_non_local_request():
    smoke = _load_browser_smoke_module()

    class Request:
        def __init__(self, url):
            self.url = url

    class Route:
        def __init__(self, url):
            self.request = Request(url)
            self.action = None

        def abort(self, reason):
            self.action = ("abort", reason)

        def continue_(self):
            self.action = ("continue", None)

    local = Route(f"http://127.0.0.1:{smoke.PORT}/static/index.html")
    external = Route("https://accounts.x.ai/sign-in")

    smoke._route_browser_request(local)
    smoke._route_browser_request(external)

    assert local.action == ("continue", None)
    assert external.action == ("abort", "blockedbyclient")


def test_browser_smoke_refuses_to_reuse_an_occupied_port(monkeypatch):
    smoke = _load_browser_smoke_module()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
        owner.bind(("127.0.0.1", 0))
        occupied = owner.getsockname()[1]
        monkeypatch.setenv("SMOKE_PORT", str(occupied))
        with pytest.raises(RuntimeError, match="already occupied"):
            smoke._select_port()
