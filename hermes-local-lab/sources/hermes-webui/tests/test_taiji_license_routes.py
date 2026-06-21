from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_license_settings_panel_and_import_controls_are_present():
    html = _read("static/index.html")
    panels_js = _read("static/panels.js")
    styles = _read("static/style.css")

    assert "taijiLicensePanel" in html
    assert "taijiLicenseFile" in html
    assert "model-config-license-status" in html
    assert "model-config-license-facts" in html
    assert html.count("model-config-license-fact") >= 4
    assert "model-config-license-primary-action" in html
    assert "model-config-license-secondary-actions" in html
    assert "taijiLicenseMachine" in html
    assert "taijiLicenseSource" in html
    assert "btnExportTaijiMachineRequest" in html
    assert "btnTaijiOnlineActivate" not in html
    assert "btnTaijiQrActivate" not in html
    assert "btnRefreshTaijiActivation" not in html
    assert "model-config-license-online-note" in html
    assert "/api/license/status" in panels_js
    assert "/api/license/import" in panels_js
    assert "/api/license/machine-request" in panels_js
    assert "suggested_filename" in panels_js
    assert "taiji-machine-request.json" in panels_js
    assert "/api/license/activate" not in panels_js
    assert "/api/license/qr-request" not in panels_js
    assert "/api/license/qr-complete" not in panels_js
    assert "#settingsPaneModels .model-config-license-status" in styles
    assert "#settingsPaneModels .model-config-license-facts" in styles
    assert "#settingsPaneModels .model-config-license-fact" in styles
    assert "#settingsPaneModels .model-config-license-actions" in styles
    assert "后续版本支持" in html


def test_chat_start_handles_license_blocked_without_stream():
    messages_js = _read("static/messages.js")

    assert "license_blocked" in messages_js
    assert "startData.license_blocked" in messages_js


def test_backend_exposes_license_status_and_import_routes():
    routes_py = _read("api/routes.py")

    assert 'path == "/api/license/status"' in routes_py
    assert 'path == "/api/license/import"' in routes_py
    assert 'path == "/api/license/machine-request"' in routes_py
    assert "suggested_filename" in routes_py
    assert 'path == "/api/license/activate"' in routes_py
    assert 'path == "/api/license/qr-request"' in routes_py
    assert 'path == "/api/license/qr-complete"' in routes_py
    assert "license_online_activation_unavailable" in routes_py
    assert "build_machine_request" in routes_py
