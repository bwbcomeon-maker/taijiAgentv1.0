"""Regression tests for issue #696 — MCP server visibility panel MVP."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def test_settings_system_panel_contains_readonly_mcp_visibility_section():
    html = read("static/index.html")
    assert 'data-i18n="mcp_servers_title"' in html
    assert 'id="mcpServerList"' in html
    assert 'class="mcp-restart-hint"' in html
    assert 'id="mcpConfigForm"' in html
    assert 'id="mcpPresetSelect"' in html
    assert 'id="mcpAllowedRoots"' in html
    assert 'onclick="pasteMcpAllowedRoots()"' in html
    assert 'onclick="chooseMcpAllowedDirectory()"' in html
    assert 'id="mcpSecurityNotice"' in html


def test_mcp_panel_renders_status_badges_tool_counts_and_empty_error_states():
    js = read("static/panels.js")
    assert "function _mcpStatusLabel" in js
    assert "mcp-status-badge" in js
    assert "mcp-tool-count" in js
    assert "mcp-empty-state" in js
    assert "mcp-error-state" in js
    assert "toggleMcpServer" in js
    assert "function deleteMcpServer" in js
    assert "mcp-toggle-btn" in js
    assert "mcp-delete-btn" in js
    assert "api('/api/mcp/servers')" in js
    assert "method:'DELETE'" in js
    assert "showConfirmDialog" in js
    assert "function loadMcpPresets" in js
    assert "function saveMcpServerConfig" in js
    assert "function testMcpServer" in js
    assert "function pasteMcpAllowedRoots" in js
    assert "function chooseMcpAllowedDirectory" in js
    assert "const testBody=isSavedRow?{}:_mcpConfigPayloadFromUi();" in js
    assert "window.taijiDesktop&&window.taijiDesktop.pickDirectory" in js
    assert "function loadMcpLogs" in js
    assert "api('/api/mcp/presets')" in js
    assert "api('/api/mcp/logs')" in js


def test_mcp_i18n_includes_visibility_status_labels():
    i18n = read("static/i18n.js")
    for key in [
        "mcp_status_active",
        "mcp_status_configured",
        "mcp_status_disabled",
        "mcp_status_invalid_config",
        "mcp_tool_count",
        "mcp_enabled_yes",
        "mcp_enabled_no",
        "mcp_toggle_followup",
        "mcp_delete_confirm_title",
        "mcp_delete_confirm_message",
        "mcp_deleted",
        "mcp_delete_failed",
        "mcp_config_title",
        "mcp_test_connection",
        "mcp_paste_path",
        "mcp_choose_directory",
        "mcp_clipboard_unavailable",
        "mcp_directory_picker_unavailable",
        "mcp_call_logs_title",
        "mcp_security_notice",
    ]:
        assert key in i18n
