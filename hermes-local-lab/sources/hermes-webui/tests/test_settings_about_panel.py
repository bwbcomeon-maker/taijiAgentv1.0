"""Regression coverage for the Settings > About product information panel."""

from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = ROOT.parents[1]


def read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def test_about_copy_is_developer_source_and_not_runtime_settings():
    from api.about import get_about_payload

    payload = get_about_payload()

    assert set(payload) == {"description"}
    assert "太极智能体 桌面版" in payload["description"]
    assert "乾元版 v0.1.7743" in payload["description"]
    assert "太极 Agent" not in payload["description"]
    assert "版权所有" in payload["description"]
    assert "授权范围" in payload["description"]

    about_source = read("api/about.py")
    assert "ABOUT_DESCRIPTION" in about_source
    assert "api.updates" not in about_source
    assert "version_items" not in about_source
    assert "sections" not in about_source
    assert "load_settings" not in about_source
    assert "localStorage" not in about_source
    assert "/api/settings" not in about_source
    assert "Developer-editable" in about_source


def test_about_endpoint_returns_developer_source_payload():
    import api.routes as routes

    handler = MagicMock()
    captured = {}

    def fake_json(_handler, data, status=200):
        captured["data"] = data
        captured["status"] = status
        return True

    with patch("api.routes.j", side_effect=fake_json):
        assert routes.handle_get(handler, urlparse("/api/about")) is True

    payload = captured["data"]
    assert captured["status"] == 200
    assert set(payload) == {"description"}
    assert "太极智能体 桌面版" in payload["description"]
    assert "乾元版 v0.1.7743" in payload["description"]


def test_settings_menu_and_main_area_expose_about_panel():
    html = read("static/index.html")
    panels_js = read("static/panels.js")

    assert 'data-settings-section="about"' in html
    assert "switchSettingsSection('about')" in html
    assert 'id="settingsPaneAbout"' in html
    assert 'id="settingsAboutDescription"' in html
    assert 'id="settingsAboutVersionBlock"' not in html
    assert 'id="settingsAboutSections"' not in html
    assert 'id="settingsAboutStatus"' not in html

    assert "function loadAboutPanel()" in panels_js
    assert "api('/api/about'" in panels_js
    assert "about:'About'" in panels_js
    assert "'about'" in panels_js
    assert "settingsAboutDescription" in panels_js
    assert "renderAboutSections" not in panels_js
    assert "renderAboutVersionItems" not in panels_js
    assert "display_text" not in panels_js


def test_about_panel_uses_native_product_sheet_structure():
    html = read("static/index.html")
    about_html = html[
        html.index('id="settingsPaneAbout"') : html.index(
            '<button class="workspace-panel-edge-toggle'
        )
    ]

    for expected in (
        'class="settings-about-shell"',
        'class="settings-about-card"',
        'class="settings-about-hero"',
        'class="settings-about-logo"',
        'class="settings-about-product-name"',
        'class="settings-about-subtitle"',
        'class="settings-about-body"',
        'id="settingsAboutDescription"',
        'class="settings-about-footer"',
        'class="settings-about-mark"',
        'class="settings-about-note"',
        'static/assets/taiji/logo/logo-mark.png',
        "太极智能体 桌面版",
        "乾元版 v0.1.7743",
        "此关于信息由开发人员在发行前维护，打包后随产品版本固定。",
    ):
        assert expected in about_html


def test_about_panel_visible_text_is_rendered_from_payload_contract():
    html = read("static/index.html")
    panels_js = read("static/panels.js")
    about_html = html[
        html.index('id="settingsPaneAbout"') : html.index(
            '<button class="workspace-panel-edge-toggle'
        )
    ]
    about_js = panels_js[
        panels_js.index("function loadAboutPanel()") : panels_js.index(
            "function switchSettingsSection"
        )
    ]

    for hardcoded in (
        "产品版本、版权归属和发行说明。",
        "WebUI: —",
        "Agent：未检测到",
        "产品名称",
        "产品说明",
        "主要能力",
        "版权与许可",
        "版权所有 © 2026",
        "维护方式",
        "关于页文案由开发人员",
    ):
        assert hardcoded not in about_html

    for hardcoded in (
        "`WebUI: ${",
        "`Agent: ${",
        "版权所有 © 2026",
        "关于信息已随当前版本固化。",
        "version_items",
        "sections",
    ):
        assert hardcoded not in about_js


def test_about_section_is_part_of_product_visibility_schema():
    from api.config import get_ui_visibility

    vis = get_ui_visibility(
        {"webui": {"feature_visibility": {"settings_sections": {"about": False}}}}
    )

    assert vis["settings_sections"]["about"] is False

    ui_js = read("static/ui.js")
    default_config = (LAB_ROOT / "config" / "taiji-default-config.yaml").read_text(
        encoding="utf-8"
    )

    assert "settings_sections:['conversation','appearance','preferences','models','providers','plugins','system','about']" in ui_js
    assert "about: true" in default_config


def test_about_panel_uses_single_readable_description_layout():
    css = read("static/style.css")

    for expected in (
        ".settings-about-shell",
        ".settings-about-card",
        ".settings-about-hero",
        ".settings-about-logo",
        ".settings-about-product-name",
        ".settings-about-subtitle",
        ".settings-about-body",
        ".settings-about-footer",
    ):
        assert expected in css
    assert ".settings-about-copy" in css
    assert "white-space:pre-line" in css
    assert "#settingsAboutVersionBlock" not in css
    assert ".settings-about-list" not in css


def test_packaged_about_copy_is_not_user_configured_after_install():
    build = (ROOT.parents[2] / "packaging" / "linux" / "deb" / "build-deb.sh").read_text(
        encoding="utf-8"
    )
    sync_config = (LAB_ROOT / "scripts" / "sync-packaged-config.py").read_text(
        encoding="utf-8"
    )

    assert "compile_sourceless_python" in build
    assert "find \"$target\" -type f -name '*.py' ! -path '*/venv/*' -delete" in build
    assert "about" not in sync_config.lower()
