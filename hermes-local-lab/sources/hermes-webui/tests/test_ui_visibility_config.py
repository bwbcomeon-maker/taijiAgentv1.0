from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = ROOT.parents[1]
BOOT_JS = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
TAIJI_HOME_JS = (ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
ROUTES_PY = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
RUNTIME_ENV_SH = (LAB_ROOT / "scripts" / "runtime-env.sh").read_text(encoding="utf-8")
PACKAGED_CONFIG = (LAB_ROOT / "hermes-home" / "config.yaml").read_text(encoding="utf-8")


def test_backend_ui_visibility_defaults_fail_open():
    from api.config import get_ui_visibility

    vis = get_ui_visibility({})

    assert vis["nav"]["chat"] is True
    assert all(vis["nav"].values())
    assert all(vis["settings_sections"].values())
    assert all(vis["composer"].values())


def test_backend_ui_visibility_hides_known_features_and_ignores_unknown():
    from api.config import get_ui_visibility

    vis = get_ui_visibility(
        {
            "webui": {
                "hidden_features": {
                    "nav": ["tasks", "kanban", "chat", "unknown"],
                    "settings_sections": ["models", "providers", "unknown"],
                    "composer": ["profile", "workspace-files", "model", "unknown"],
                }
            }
        }
    )

    assert vis["nav"]["chat"] is True
    assert vis["nav"]["tasks"] is False
    assert vis["nav"]["kanban"] is False
    assert "unknown" not in vis["nav"]
    assert vis["settings_sections"]["models"] is False
    assert vis["settings_sections"]["providers"] is False
    assert "unknown" not in vis["settings_sections"]
    assert vis["composer"]["profile"] is False
    assert vis["composer"]["workspace_files"] is False
    assert vis["composer"]["model"] is False
    assert "unknown" not in vis["composer"]


def test_backend_feature_visibility_boolean_schema_controls_visibility():
    from api.config import get_ui_visibility

    vis = get_ui_visibility(
        {
            "webui": {
                "feature_visibility": {
                    "nav": {"tasks": False, "chat": False, "skills": True},
                    "settings_sections": {"models": False},
                    "composer": {
                        "profile": False,
                        "workspace_files": True,
                        "model": "false",
                    },
                }
            }
        }
    )

    assert vis["nav"]["chat"] is True
    assert vis["nav"]["tasks"] is False
    assert vis["nav"]["skills"] is True
    assert vis["nav"]["kanban"] is True
    assert vis["settings_sections"]["models"] is False
    assert vis["settings_sections"]["providers"] is True
    assert vis["composer"]["profile"] is False
    assert vis["composer"]["workspace_files"] is True
    assert vis["composer"]["model"] is True


def test_feature_visibility_schema_overrides_legacy_hidden_features():
    from api.config import get_ui_visibility

    vis = get_ui_visibility(
        {
            "webui": {
                "hidden_features": {
                    "nav": ["tasks", "kanban"],
                    "settings_sections": ["models"],
                    "composer": ["model"],
                },
                "feature_visibility": {
                    "nav": {"tasks": True, "skills": False},
                    "settings_sections": {"models": True},
                    "composer": {"model": True},
                },
            }
        }
    )

    assert vis["nav"]["tasks"] is True
    assert vis["nav"]["kanban"] is False
    assert vis["nav"]["skills"] is False
    assert vis["settings_sections"]["models"] is True
    assert vis["composer"]["model"] is True


def test_settings_api_returns_computed_visibility_not_raw_config():
    assert "get_ui_visibility" in ROUTES_PY
    get_block = ROUTES_PY[
        ROUTES_PY.index('if parsed.path == "/api/settings":') : ROUTES_PY.index(
            'if parsed.path == "/api/reasoning":'
        )
    ]
    post_block = ROUTES_PY[
        ROUTES_PY.rindex('if parsed.path == "/api/settings":') : ROUTES_PY.index(
            "auth_enabled_after = is_auth_enabled()"
        )
    ]

    assert 'settings["ui_visibility"] = get_ui_visibility()' in get_block
    assert 'saved["ui_visibility"] = get_ui_visibility()' in post_block
    assert "hidden_features" not in get_block
    assert "hidden_features" not in post_block
    assert "feature_visibility" not in get_block
    assert "feature_visibility" not in post_block


def test_packaged_config_lists_all_features_with_chinese_explanations():
    config = yaml.safe_load(PACKAGED_CONFIG)
    feature_visibility = config["webui"]["feature_visibility"]

    assert "左侧主导航" in PACKAGED_CONFIG
    assert "底部输入区控制条" in PACKAGED_CONFIG
    assert "true=显示，false=隐藏" in PACKAGED_CONFIG
    assert set(feature_visibility["nav"]) == {
        "chat",
        "tasks",
        "kanban",
        "writing",
        "skills",
        "memory",
        "workspaces",
        "profiles",
        "todos",
        "insights",
        "logs",
        "settings",
    }
    assert set(feature_visibility["settings_sections"]) == {
        "conversation",
        "appearance",
        "preferences",
        "models",
        "providers",
        "plugins",
        "system",
    }
    assert set(feature_visibility["composer"]) == {
        "profile",
        "workspace_files",
        "workspace_switcher",
        "model",
        "reasoning",
        "toolsets",
        "quota",
    }
    assert all(isinstance(value, bool) for value in feature_visibility["nav"].values())
    assert all(isinstance(value, bool) for value in feature_visibility["settings_sections"].values())
    assert all(isinstance(value, bool) for value in feature_visibility["composer"].values())


def test_runtime_env_syncs_packaged_feature_visibility_on_startup():
    assert "sync-feature-visibility.py" in RUNTIME_ENV_SH
    assert 'TAIJI_AGENT_SYNC_FEATURE_VISIBILITY:-1' in RUNTIME_ENV_SH
    assert "$LAB_DIR/hermes-home/config.yaml" in RUNTIME_ENV_SH
    assert "$HERMES_HOME/config.yaml" in RUNTIME_ENV_SH


def test_sync_feature_visibility_script_preserves_user_model_config(tmp_path):
    template = tmp_path / "template.yaml"
    target = tmp_path / "user" / "config.yaml"
    template.write_text(
        yaml.safe_dump(
            {
                "model": {"default": "packaged-model"},
                "webui": {
                    "feature_visibility": {
                        "nav": {"tasks": False},
                        "settings_sections": {"models": False},
                        "composer": {"model": False},
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    target.parent.mkdir(parents=True)
    target.write_text(
        yaml.safe_dump(
            {
                "model": {"default": "user-model", "api_key": "keep-me"},
                "providers": {"deepseek": {"api_key": "also-keep"}},
                "webui": {"feature_visibility": {"nav": {"tasks": True}}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    subprocess.run(
        ["python3", str(LAB_ROOT / "scripts" / "sync-feature-visibility.py"), str(template), str(target)],
        check=True,
    )
    merged = yaml.safe_load(target.read_text(encoding="utf-8"))

    assert merged["model"]["default"] == "user-model"
    assert merged["model"]["api_key"] == "keep-me"
    assert merged["providers"]["deepseek"]["api_key"] == "also-keep"
    assert merged["webui"]["feature_visibility"]["nav"]["tasks"] is False
    assert merged["webui"]["feature_visibility"]["settings_sections"]["models"] is False
    assert merged["webui"]["feature_visibility"]["composer"]["model"] is False


def test_sync_feature_visibility_script_copies_template_for_missing_target(tmp_path):
    template = LAB_ROOT / "hermes-home" / "config.yaml"
    target = tmp_path / "fresh" / "config.yaml"

    subprocess.run(
        ["python3", str(LAB_ROOT / "scripts" / "sync-feature-visibility.py"), str(template), str(target)],
        check=True,
    )

    copied = target.read_text(encoding="utf-8")
    assert "左侧主导航" in copied
    assert "feature_visibility" in copied


def test_boot_reads_ui_visibility_and_applies_it():
    assert "window._uiVisibility=s.ui_visibility||null;" in BOOT_JS
    assert "if(typeof applyUiVisibility==='function') applyUiVisibility();" in BOOT_JS
    assert "window._uiVisibility=s&&s.ui_visibility?s.ui_visibility:null;" in PANELS_JS
    assert "window._uiVisibility=settings&&settings.ui_visibility?settings.ui_visibility:null;" in PANELS_JS


def test_frontend_has_unified_visibility_helper_and_selectors():
    assert "function isUiFeatureVisible(group,key)" in UI_JS
    assert "function applyUiVisibility()" in UI_JS
    for selector in (
        "[data-panel]",
        "[data-taiji-panel]",
        "#settingsMenu [data-settings-section]",
        "profileChipWrap",
        "btnWorkspacePanelToggle",
        "btnWorkspacePanelEdgeToggle",
        "composerWorkspaceGroup",
        "composerWorkspaceChip",
        "composerModelChip",
        "composerReasoningWrap",
        "composerToolsetsWrap",
        "providerQuotaChip",
        "composerMobileConfigBtn",
        "composerMobileWorkspaceAction",
        "composerMobileModelAction",
        "composerMobileReasoningAction",
    ):
        assert selector in UI_JS


def test_visibility_hidden_elements_are_forced_out_of_layout():
    helper_start = UI_JS.index("function _setUiVisibilityHidden")
    helper_body = UI_JS[helper_start : UI_JS.index("function resolveUiSettingsSection", helper_start)]

    assert "el.dataset.uiVisibilityHidden='1';" in helper_body
    assert "el.classList.add('ui-visibility-hidden');" in helper_body
    assert "el.setAttribute('tabindex','-1');" in helper_body
    assert "el.classList.remove('active','is-active');" in helper_body
    assert "[data-ui-visibility-hidden=\"1\"]" in STYLE_CSS
    assert ".taiji-home-shell .taiji-nav-item[data-ui-visibility-hidden=\"1\"]" in STYLE_CSS
    assert ".nav-tab[data-ui-visibility-hidden=\"1\"]" in STYLE_CSS
    assert ".side-menu-item[data-ui-visibility-hidden=\"1\"]" in STYLE_CSS
    assert "display:none!important;" in STYLE_CSS


def test_hidden_panels_and_settings_sections_are_guarded():
    assert "!isUiFeatureVisible('nav',nextPanel)" in PANELS_JS
    assert "!isUiFeatureVisible('nav','settings')" in PANELS_JS
    assert "resolveUiSettingsSection(requested)" in PANELS_JS
    assert "!isUiFeatureVisible('nav',panel)" in TAIJI_HOME_JS
    assert "if(typeof applyUiVisibility==='function') applyUiVisibility();" in TAIJI_HOME_JS
    assert "function visiblePanel(panel)" in TAIJI_HOME_JS
    assert "const panel=visiblePanel(rawPanel);" in TAIJI_HOME_JS
    assert "const key=visiblePanel(panel);" in TAIJI_HOME_JS
    assert "setTimeout(()=>switchPanel('chat',{bypassSettingsGuard:true}),0);" in TAIJI_HOME_JS


def test_hidden_composer_controls_noop_their_dropdowns():
    for guard in (
        "!isUiFeatureVisible('composer','model')",
        "!isUiFeatureVisible('composer','reasoning')",
        "!isUiFeatureVisible('composer', 'toolsets')",
        "!isUiFeatureVisible('composer','workspace_switcher')",
        "!isUiFeatureVisible('composer','profile')",
        "!isUiFeatureVisible('composer','workspace_files')",
        "!isUiFeatureVisible('composer','quota')",
    ):
        assert guard in (UI_JS + PANELS_JS + BOOT_JS)

    assert "closeModelDropdown();" in UI_JS
    assert "closeReasoningDropdown();" in UI_JS
    assert "closeToolsetsDropdown();" in UI_JS
    assert "closeWsDropdown();" in PANELS_JS
    assert "closeProfileDropdown();" in PANELS_JS
