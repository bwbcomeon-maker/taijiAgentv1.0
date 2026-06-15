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

    assert payload["product_name"] == "太极 Agent"
    assert payload["copyright_owner"] == "太极 Agent 项目组"
    assert payload["webui_version"] == "v-dev"
    assert payload["agent_version"] == "agent-dev"
    assert "本地工作流" in payload["description"]
    assert "授权范围" in payload["license_notice"]
    assert len(payload["highlights"]) >= 3

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
    assert payload["product_name"] == "太极 Agent"
    assert payload["copyright_owner"] == "太极 Agent 项目组"
    assert payload["webui_version"]
    assert payload["agent_version"]


def test_settings_menu_and_main_area_expose_about_panel():
    html = read("static/index.html")
    panels_js = read("static/panels.js")

    assert 'data-settings-section="about"' in html
    assert "switchSettingsSection('about')" in html
    assert 'id="settingsPaneAbout"' in html
    assert 'id="settingsAboutProductName"' in html
    assert 'id="settings-about-webui-version-badge"' in html
    assert 'id="settings-about-agent-version-badge"' in html
    assert 'id="settingsAboutDescription"' in html
    assert 'id="settingsAboutDeveloperNote"' in html

    assert "function loadAboutPanel()" in panels_js
    assert "api('/api/about'" in panels_js
    assert "about:'About'" in panels_js
    assert "'about'" in panels_js
    assert "settingsAboutProductName" in panels_js
    assert "settings-about-webui-version-badge" in panels_js
    assert "settingsAboutDeveloperNote" in panels_js


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
