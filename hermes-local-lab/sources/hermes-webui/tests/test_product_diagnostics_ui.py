from pathlib import Path
import json
import re
import subprocess


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
    for web_fallback in ("chromium.launch", ".goto("):
        assert web_fallback not in smoke


def test_desktop_acceptance_smoke_allows_only_the_handled_missing_expert_run_404():
    smoke = read("tests/product_diagnostics_electron_smoke.js")

    assert "function isExpectedDesktopHttpFailure(entry, appOrigin)" in smoke
    assert 'entry.status === 404' in smoke
    assert 'url.pathname === "/api/expert-teams/run"' in smoke
    assert "unexpectedHttpFailures" in smoke
    assert "expectedBackgroundConsoleErrors" in smoke
    assert "message.location()" in smoke


def test_desktop_acceptance_404_helpers_reject_wrong_method_status_path_and_origin():
    smoke_path = ROOT / "tests" / "product_diagnostics_electron_smoke.js"
    script = r"""
const {
  isExpectedDesktopHttpFailure,
  isExpectedBackgroundConsoleError,
} = require(process.argv[1]);
const origin = "http://127.0.0.1:18787";
const http = (status, method, url) => isExpectedDesktopHttpFailure({status, method, url}, origin);
const consoleError = (text, url) => isExpectedBackgroundConsoleError({
  type: "error",
  text,
  url,
}, origin);
const expectedText = "console: Failed to load resource: the server responded with a status of 404 (Not Found)";
process.stdout.write(JSON.stringify({
  http: [
    http(404, "GET", `${origin}/api/expert-teams/run?session_id=s1`),
    http(404, "POST", `${origin}/api/expert-teams/run?session_id=s1`),
    http(500, "GET", `${origin}/api/expert-teams/run?session_id=s1`),
    http(404, "GET", `${origin}/api/other`),
    http(404, "GET", `${origin}/api/expert-teams/run`),
    http(404, "GET", `${origin}/api/expert-teams/run?session_id=%20`),
    http(404, "GET", `http://127.0.0.1:9999/api/expert-teams/run?session_id=s1`),
    http(404, "GET", "not a url"),
  ],
  console: [
    consoleError(expectedText, `${origin}/api/expert-teams/run?session_id=s1`),
    consoleError(expectedText, `${origin}/api/other`),
    consoleError(expectedText, `${origin}/api/expert-teams/run`),
    consoleError(expectedText, `${origin}/api/expert-teams/run?session_id=%20`),
    consoleError(expectedText, `http://127.0.0.1:9999/api/expert-teams/run?session_id=s1`),
    consoleError("console: unrelated 404 Not Found", `${origin}/api/expert-teams/run?session_id=s1`),
  ],
}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(smoke_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "http": [True, False, False, False, False, False, False, False],
        "console": [True, False, False, False, False, False],
    }
