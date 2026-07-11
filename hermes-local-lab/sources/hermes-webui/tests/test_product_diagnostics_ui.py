from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_system_settings_has_visible_accessible_diagnostics_entry():
    html = read("static/index.html")
    system = html[html.index('id="settingsPaneSystem"') : html.index('id="settingsPaneAbout"')]

    for expected in (
        'id="productDiagnosticsCard"',
        'id="productDiagnosticsStatus"',
        'id="productDiagnosticsComponents"',
        'id="productDiagnosticsCheckedAt"',
        'id="productDiagnosticsIncidentId"',
        'id="btnCopyProductDiagnosticsIncident"',
        'id="productDiagnosticsRecovery"',
        'id="btnRefreshProductDiagnostics"',
        'id="btnExportProductDiagnostics"',
        'aria-labelledby="productDiagnosticsTitle"',
        'role="status"',
        'aria-live="polite"',
        "安全诊断",
        "预览并导出脱敏支持包",
    ):
        assert expected in system


def test_diagnostics_ui_loads_renders_confirms_and_exports():
    panels = read("static/panels.js")

    for expected in (
        "async function loadProductDiagnostics(force)",
        "function _renderProductDiagnostics(data)",
        "async function exportProductDiagnostics()",
        "async function copyProductDiagnosticsIncidentId()",
        "function _renderProductDiagnosticsRecovery(data)",
        "function _safeProductErrorEnvelope(error)",
        "function _renderProductDiagnosticsError(error)",
        "error&&error.payload",
        "payload.product_error",
        "_productDiagnosticsPending",
        "api('/api/product/diagnostics')",
        "api('/api/product/diagnostics/export'",
        "showConfirmDialog({",
        "_downloadJsonFile(",
        "loadProductDiagnostics(true)",
    ):
        assert expected in panels
    assert "if(name==='system')" in panels
    assert "loadProductDiagnostics();" in panels


def test_security_profile_failure_uses_allowlisted_product_copy():
    ui = read("static/ui.js")
    function = ui[ui.index("async function saveSecurityProfile()") : ui.index("function startSecurityStatusMonitor()")]

    assert "_safeProductErrorEnvelope(e)" in function
    assert "productError.title" in function
    assert "productError.message" in function
    assert "e&&e.message" not in function
    assert "安全模式保存失败，请重试。" in function


def test_diagnostics_styles_keep_status_and_actions_discoverable():
    styles = read("static/style.css")

    for selector in (
        ".product-diagnostics-card",
        ".product-diagnostics-grid",
        ".product-diagnostics-component",
        ".product-diagnostics-actions",
        ".product-diagnostics-status",
        ".product-diagnostics-meta",
        ".product-diagnostics-recovery",
        "@media (max-width:1200px)",
    ):
        assert selector in styles


def test_desktop_settings_have_one_vertical_scroll_owner():
    styles = read("static/style.css")

    assert re.search(
        r"\.taiji-home-shell main\.main\.taiji-real-main > #mainSettings\s*\{\s*overflow:hidden;",
        styles,
    )
    assert re.search(
        r"\.taiji-home-shell main\.main\.taiji-real-main > #mainSettings \.settings-main\s*\{[^}]*overflow:auto;",
        styles,
        re.DOTALL,
    )


def test_shared_app_dialog_escape_is_exclusive_before_focus_restore():
    ui = read("static/ui.js")
    bindings = ui[ui.index("function _ensureAppDialogBindings()") : ui.index("function showConfirmDialog(opts={})")]
    escape = bindings[bindings.index("if(e.key==='Escape')") : bindings.index("if(e.key==='Enter')")]

    assert "e.preventDefault()" in escape
    assert "e.stopImmediatePropagation()" in escape
    assert "_finishAppDialog" in escape


def test_desktop_acceptance_smoke_rejects_web_and_covers_only_app_sizes():
    smoke = read("tests/product_diagnostics_electron_smoke.js")

    for expected in (
        "chromium.connectOverCDP",
        'taiji_desktop=1',
        'taiji_desktop_token=',
        "window.resizeTo(1120, 720)",
        "window.resizeTo(1440, 900)",
        'page.waitForEvent("download",',
        'page.route("**/api/security/profile"',
        "nested product error stayed allowlisted in Desktop App",
        "Desktop App",
    ):
        assert expected in smoke
    for excluded in ("resizeTo(390", "resizeTo(375", "isMobile"):
        assert excluded not in smoke
