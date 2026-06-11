from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_license_settings_panel_and_import_controls_are_present():
    html = _read("static/index.html")
    panels_js = _read("static/panels.js")

    assert "taijiLicensePanel" in html
    assert "taijiLicenseFile" in html
    assert "/api/license/status" in panels_js
    assert "/api/license/import" in panels_js


def test_chat_start_handles_license_blocked_without_stream():
    messages_js = _read("static/messages.js")

    assert "license_blocked" in messages_js
    assert "startData.license_blocked" in messages_js


def test_backend_exposes_license_status_and_import_routes():
    routes_py = _read("api/routes.py")

    assert 'path == "/api/license/status"' in routes_py
    assert 'path == "/api/license/import"' in routes_py
