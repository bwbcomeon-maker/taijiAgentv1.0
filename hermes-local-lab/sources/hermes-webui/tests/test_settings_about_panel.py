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

    payload = get_about_payload(webui_version="v-dev", agent_version="agent-dev")

    assert payload["title"] == "关于"
    assert payload["subtitle"] == "产品版本、版权归属和发行说明。"
    assert payload["success_status"] == "关于信息已随当前版本固化。"

    version_items = {item["id"]: item for item in payload["version_items"]}
    assert version_items["webui"]["label"] == "WebUI"
    assert version_items["webui"]["value"] == "v-dev"
    assert version_items["webui"]["display_text"] == "WebUI: v-dev"
    assert version_items["agent"]["label"] == "Agent"
    assert version_items["agent"]["value"] == "agent-dev"
    assert version_items["agent"]["display_text"] == "Agent: agent-dev"

    sections = {section["id"]: section for section in payload["sections"]}
    assert sections["product_name"] == {
        "id": "product_name",
        "label": "产品名称",
        "kind": "heading",
        "body": "太极 Agent",
    }
    assert sections["description"]["label"] == "产品说明"
    assert "本地工作流" in sections["description"]["body"]
    assert sections["highlights"]["label"] == "主要能力"
    assert len(sections["highlights"]["items"]) >= 3
    assert sections["copyright_license"]["label"] == "版权与许可"
    assert "版权所有 © 2026 太极 Agent 项目组。保留所有权利。" in sections["copyright_license"]["paragraphs"]
    assert any("授权范围" in text for text in sections["copyright_license"]["paragraphs"])
    assert sections["maintenance"]["label"] == "维护方式"
    assert any("api/about.py" in text for text in sections["maintenance"]["paragraphs"])

    about_source = read("api/about.py")
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
    assert payload["title"] == "关于"
    assert len(payload["version_items"]) == 2
    assert all(item["display_text"] for item in payload["version_items"])
    assert any(section["id"] == "copyright_license" for section in payload["sections"])


def test_settings_menu_and_main_area_expose_about_panel():
    html = read("static/index.html")
    panels_js = read("static/panels.js")

    assert 'data-settings-section="about"' in html
    assert "switchSettingsSection('about')" in html
    assert 'id="settingsPaneAbout"' in html
    assert 'id="settingsAboutTitle"' in html
    assert 'id="settingsAboutSubtitle"' in html
    assert 'id="settingsAboutVersionBlock"' in html
    assert 'id="settingsAboutSections"' in html
    assert 'id="settingsAboutStatus"' in html

    assert "function loadAboutPanel()" in panels_js
    assert "api('/api/about'" in panels_js
    assert "about:'About'" in panels_js
    assert "'about'" in panels_js
    assert "settingsAboutSections" in panels_js
    assert "display_text" in panels_js
    assert "renderAboutSections" in panels_js


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


def test_about_panel_header_layout_does_not_squeeze_title():
    css = read("static/style.css")

    assert "#settingsPaneAbout .settings-section-head{display:grid;" in css
    assert "#settingsPaneAbout .settings-version-badge{max-width:100%;" in css
    assert "text-overflow:ellipsis" in css
    assert "#settingsAboutVersionBlock{display:inline-flex;" in css
    assert "justify-content:flex-start" in css


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
