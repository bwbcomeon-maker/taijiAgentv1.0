from pathlib import Path

import pytest


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


def test_only_valid_license_is_presented_as_success_state():
    panels_js = _read("static/panels.js")
    styles = _read("static/style.css")

    assert "not_required" not in panels_js
    assert "开发环境无需授权" not in panels_js
    assert "开发源码模式无需授权" not in panels_js
    assert "const state=status==='valid'?'ok'" in panels_js
    assert "if(icon) icon.textContent=status==='valid'?'✓':'!';" in panels_js
    assert "return '授权状态不可用';" in panels_js
    assert 'data-license-status="not_required"' not in styles


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


def test_webui_license_fallback_is_fail_closed_without_environment_policy_switch():
    routes_py = _read("api/routes.py")
    start = routes_py.index("def _taiji_license_error_status")
    end = routes_py.index("def _handle_license_status", start)
    license_guard = routes_py[start:end]

    assert '"required": True' in license_guard
    assert 'os.environ.get("TAIJI_LICENSE_REQUIRED"' not in license_guard
    assert "return None" not in license_guard


def test_license_import_delegates_canonical_install_to_agent_core():
    routes_py = _read("api/routes.py")
    start = routes_py.index("def _handle_license_import")
    end = routes_py.index("def _clear_stale_stream_state", start)
    import_handler = routes_py[start:end]

    assert "runtime_license_path()" not in import_handler
    assert "default_license_path()" not in import_handler
    assert "install_license_token(token)" in import_handler
    assert "validate_license_candidate" not in import_handler
    assert "write_text" not in import_handler
    assert "os.replace" not in import_handler
    assert ".tmp" not in import_handler


class _ImportStatus:
    def __init__(self, status, code=None, message=""):
        self.status = status
        self.code = code
        self.message = message

    def to_public_dict(self):
        return {
            "status": self.status,
            "required": True,
            "code": self.code,
            "message": self.message,
        }


class _ImportLicenseModule:
    def __init__(self, result):
        self.result = result
        self.tokens = []

    def install_license_token(self, token):
        self.tokens.append(token)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _call_import(monkeypatch, result, body):
    from api import routes

    module = _ImportLicenseModule(result)
    monkeypatch.setattr(routes, "_taiji_license_module", lambda: module)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: {"kind": "json", "status": status, "payload": payload},
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: {"kind": "bad", "status": status, "message": message},
    )
    return routes._handle_license_import(object(), body), module


def test_license_import_installs_the_exact_normalized_token(monkeypatch):
    response, module = _call_import(
        monkeypatch,
        _ImportStatus("valid", message="授权有效"),
        {"license": "  header.payload.signature\n"},
    )

    assert module.tokens == ["header.payload.signature"]
    assert response == {
        "kind": "json",
        "status": 200,
        "payload": {
            "status": "valid",
            "required": True,
            "code": None,
            "message": "授权有效",
        },
    }


@pytest.mark.parametrize("code", ["license_invalid_signature", "license_file_untrusted"])
def test_license_import_rejects_invalid_or_unsafe_candidate(monkeypatch, code):
    response, module = _call_import(
        monkeypatch,
        _ImportStatus("invalid", code=code, message="请重新导入授权。"),
        {"license": "candidate-token"},
    )

    assert module.tokens == ["candidate-token"]
    assert response["status"] == 400
    assert response["payload"]["code"] == code


def test_license_import_exception_response_and_log_do_not_disclose_token_or_path(
    monkeypatch, caplog
):
    secret = "secret-license-token"
    response, _module = _call_import(
        monkeypatch,
        RuntimeError(f"failed for {secret} at /Users/private/license.jwt"),
        {"license": secret},
    )

    rendered = str(response)
    assert response["status"] == 500
    assert secret not in rendered
    assert "/Users/private" not in rendered
    assert secret not in caplog.text
    assert "/Users/private" not in caplog.text
