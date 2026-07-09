"""Static coverage for the Settings model configuration panel."""

from pathlib import Path

ROOT = Path(__file__).parent.parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_model_config_settings_section_exists():
    assert 'data-settings-section="models"' in INDEX_HTML
    assert 'id="settingsPaneModels"' in INDEX_HTML
    assert "模型配置" in INDEX_HTML


def test_model_config_has_three_required_surfaces():
    for marker in (
        'id="modelConfigProvider"',
        'id="modelConfigAuxContainer"',
        'id="imageGenConfigProvider"',
    ):
        assert marker in INDEX_HTML


def test_model_config_js_calls_expected_endpoints():
    assert "async function loadModelConfigPanel" in PANELS_JS
    assert "/api/model-config" in PANELS_JS
    assert "/api/model-config/main" in PANELS_JS
    assert "/api/image-gen/config" in PANELS_JS
    assert "/api/model/auxiliary" in PANELS_JS
    assert "/api/model/set" in PANELS_JS


def test_model_config_secret_inputs_start_empty():
    assert 'id="modelConfigApiKey"' in INDEX_HTML
    assert 'id="imageGenConfigApiKey"' in INDEX_HTML
    assert "modelConfigApiKey')||{}).value||''" in PANELS_JS
    assert "imageGenConfigApiKey')||{}).value||''" in PANELS_JS
    assert "key_status" in PANELS_JS
    assert "payload.api_key=apiKey" in PANELS_JS


def test_secret_paste_uses_desktop_clipboard_bridge_when_available():
    assert "readSecretClipboardText" in PANELS_JS
    assert "window.taijiDesktop" in PANELS_JS
    assert "readClipboardText" in PANELS_JS
    assert "navigator.clipboard.readText" in PANELS_JS


def test_model_config_focus_layout_has_summary_cards():
    for marker in (
        'class="model-config-focus-layout"',
        "model-config-license-strip",
        'id="modelConfigMainSummaryCard"',
        'id="modelConfigImageSummaryCard"',
        'id="modelConfigAuxSummary"',
    ):
        assert marker in INDEX_HTML


def test_model_config_license_actions_remain_visible():
    for marker in (
        'id="btnImportTaijiLicense"',
        'id="btnExportTaijiMachineRequest"',
        'id="btnRefreshTaijiLicense"',
    ):
        assert marker in INDEX_HTML


def test_model_config_edit_forms_are_collapsible_in_cards():
    for marker in (
        'id="modelConfigMainEdit"',
        'id="imageGenConfigEdit"',
        'id="modelConfigAuxEdit"',
        'toggleModelConfigSection',
    ):
        assert marker in INDEX_HTML or marker in PANELS_JS


def test_model_config_js_updates_focus_summaries():
    for marker in (
        "_setModelConfigStatusBadge",
        "_renderModelConfigFocusSummary",
        "modelConfigMainEffective",
        "imageGenConfigKeyState",
        "taijiLicenseRemainingBadge",
    ):
        assert marker in PANELS_JS


def test_image_generation_summary_uses_real_availability():
    assert "imageRow.available===true" in PANELS_JS
    assert "imageRow.oauth_managed));" not in PANELS_JS


def test_image_generation_auth_hint_is_taiji_branded():
    assert "此图片生成服务由太极智能体授权管理" in PANELS_JS
    assert "Codex/ChatGPT OAuth" not in PANELS_JS


def test_image_generation_custom_provider_management_has_visible_entry():
    for marker in (
        'id="btnAddCustomImageProvider"',
        'id="btnManageCustomImageProviders"',
        'id="btnGoImageProviders"',
        'id="customImageProviderPanel"',
        'id="customImageProviderBaseUrl"',
        "添加外部图片模型",
        "管理外部模型",
        "去提供商配置",
    ):
        assert marker in INDEX_HTML
    assert "saveCustomImageProviderConfig" in PANELS_JS
    assert "deleteCustomImageProviderConfig" in PANELS_JS
    assert "/api/image-gen/custom-providers" in PANELS_JS


def test_settings_menu_does_not_add_auth_keys_section():
    assert 'data-settings-section="authorization"' not in INDEX_HTML
    assert 'data-settings-section="auth"' not in INDEX_HTML
    assert 'data-settings-section="keys"' not in INDEX_HTML
    assert "授权与密钥" not in INDEX_HTML


def test_model_config_routes_image_generation_management_to_existing_providers_tab():
    assert "switchSettingsSection('providers')" in PANELS_JS
    assert "openImageProvidersPanel" in PANELS_JS
    assert 'id="settingsPaneProviders"' in INDEX_HTML
    assert 'data-settings-section="providers"' in INDEX_HTML


def test_providers_panel_contains_image_generation_provider_management_surface():
    for marker in (
        'id="providerImageGenServices"',
        'id="providerImageGenTemplates"',
        "外部模型服务",
        "添加图片生成提供商",
        "通义万相",
        "豆包 Seedream",
        "百度千帆",
        "腾讯混元",
        "智谱 GLM-Image",
        "讯飞 HiDream",
        "自定义 HTTP",
    ):
        assert marker in INDEX_HTML
    for marker in (
        "_renderProviderImageGenSettings",
        "_DOMESTIC_IMAGE_PROVIDER_TEMPLATES",
        "providerImageGenServices",
        "providerImageGenTemplates",
    ):
        assert marker in PANELS_JS
    assert ".provider-image-services" in STYLE_CSS
    assert ".provider-template-grid" in STYLE_CSS


def test_model_config_has_clear_image_capability_cards():
    for marker in (
        'id="modelConfigImageCapabilities"',
        'id="modelConfigVisionSummaryCard"',
        'id="modelConfigImageSummaryCard"',
        'id="visionConfigProviderSummary"',
        'id="visionConfigModelSummary"',
        'id="visionConfigKeyState"',
        'id="visionConfigEdit"',
        'id="visionConfigProvider"',
        'id="visionConfigModel"',
        'id="visionConfigApiKey"',
        'id="btnSaveVisionConfig"',
        "图片能力",
        "看图识别",
        "生成图片",
    ):
        assert marker in INDEX_HTML
    assert "_renderVisionConfigSummary" in PANELS_JS
    assert "saveVisionConfig" in PANELS_JS
    assert "/api/vision/config" in PANELS_JS
    assert ".model-config-image-capability-grid" in STYLE_CSS
    assert ".model-config-capability-card" in STYLE_CSS


def test_image_generation_advanced_actions_are_outside_primary_card():
    card_start = INDEX_HTML.find('id="modelConfigImageSummaryCard"')
    card_end = INDEX_HTML.find('</section>', card_start)
    assert card_start >= 0 and card_end > card_start
    card_html = INDEX_HTML[card_start:card_end]
    for advanced_marker in (
        'id="btnAddCustomImageProvider"',
        'id="btnManageCustomImageProviders"',
        'id="btnGoImageProviders"',
        "通义万相",
        "豆包 Seedream",
        "自定义 HTTP",
    ):
        assert advanced_marker not in card_html
    assert 'id="modelConfigImageAdvanced"' in INDEX_HTML


def test_image_generation_custom_provider_new_form_generates_id():
    assert "_customImageProviderDraftId" in PANELS_JS
    assert "customImageProviderId" in PANELS_JS
    assert "||_customImageProviderDraftId(name,baseUrl)" in PANELS_JS


def test_image_generation_save_forces_full_model_config_refresh():
    assert "async function saveImageGenConfig" in PANELS_JS
    assert "await loadModelConfigPanel(true)" in PANELS_JS
    assert "image_gen:data.image_gen" not in PANELS_JS


def test_image_generation_edit_uses_selected_provider_default_model():
    assert "const selectedImage=_modelConfigImageProviderRow" in PANELS_JS
    assert (
        "imageModelInput.value=imageGen.model||String((selectedImage&&selectedImage.default_model)||'')"
        in PANELS_JS
    )


def test_image_generation_oauth_managed_provider_hides_key_paste_action():
    assert "modelConfigImagePasteAction" in INDEX_HTML
    assert "modelConfigImagePasteAction" in PANELS_JS
    assert "pasteAction.style.display=managedAuth?'none':''" in PANELS_JS


def test_image_generation_key_row_is_visible_with_managed_auth_explanation():
    assert 'id="imageGenProviderScopeHint"' in INDEX_HTML
    assert 'id="visionProviderScopeHint"' in INDEX_HTML
    assert "provider&&provider.oauth_managed" in PANELS_JS
    assert "imageGenConfigApiKey.disabled=oauth" in PANELS_JS
    assert "此服务由太极授权托管，无需填写 API 密钥。" in PANELS_JS
    assert "keyRow.style.display=envVar&&!oauth?'':'none'" not in PANELS_JS


def test_model_config_license_layout_prioritizes_customer_and_compacts_actions():
    assert 'class="model-config-license-customer"' in INDEX_HTML
    assert 'id="taijiLicenseCustomer"' in INDEX_HTML
    assert 'class="model-config-license-toolbar"' in INDEX_HTML
    assert "#settingsPaneModels .model-config-license-customer" in STYLE_CSS
    assert "#settingsPaneModels .model-config-license-toolbar" in STYLE_CSS
    assert "grid-template-columns:minmax(0,1fr) auto" in STYLE_CSS
    assert (
        "#settingsPaneModels .model-config-license-actions{display:grid;gap:6px;"
        "justify-items:end;align-self:start;min-width:0;max-width:220px;}"
    ) in STYLE_CSS


def test_model_config_styles_are_present():
    assert ".model-config-status" in STYLE_CSS
    assert ".model-config-panel" in STYLE_CSS
    assert ".model-config-aux-row" in STYLE_CSS
    assert ".model-config-focus-layout" in STYLE_CSS
    assert ".model-config-summary-card" in STYLE_CSS
    assert ".model-config-collapsible" in STYLE_CSS
