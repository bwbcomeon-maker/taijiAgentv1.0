import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "assets" / "taiji"
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
ICONS_JS = (ROOT / "static" / "icons.js").read_text(encoding="utf-8")

NAV_ICONS = [
    "nav-chat",
    "nav-dashboard",
    "nav-insights",
    "nav-kanban",
    "nav-logs",
    "nav-memory",
    "nav-profiles",
    "nav-settings",
    "nav-skills",
    "nav-tasks",
    "nav-todos",
    "nav-workspaces",
    "nav-writing",
]

ACTION_ICONS = [
    "action-attach",
    "action-collapse",
    "action-expand",
    "action-folder",
    "action-mode",
    "action-model",
    "action-new",
    "action-next",
    "action-scope",
    "action-search",
    "action-send",
    "action-user",
    "action-voice",
]


def _png_meta(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"{path.name} is not a PNG"
    width, height, _bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, color_type


def _css_rule(selector: str) -> str:
    rule_start = STYLE_CSS.rindex(selector)
    return STYLE_CSS[rule_start : STYLE_CSS.index("}", rule_start)]


def test_taiji_nav_and_action_icons_are_complete_256_alpha_pngs():
    for name in NAV_ICONS:
        width, height, color_type = _png_meta(ASSETS / "nav" / f"{name}.png")
        assert (width, height) == (256, 256), name
        assert color_type in (4, 6), f"{name} must keep alpha transparency"

    for name in ACTION_ICONS:
        width, height, color_type = _png_meta(ASSETS / "action" / f"{name}.png")
        assert (width, height) == (256, 256), name
        assert color_type in (4, 6), f"{name} must keep alpha transparency"


def test_taiji_icon_source_sheets_are_preserved_for_design_review():
    for name in ("nav-icon-sheet-v2", "action-icon-sheet-v2"):
        width, height, color_type = _png_meta(ASSETS / "source" / f"{name}.png")
        assert (width, height) == (1024, 1024), name
        assert color_type in (4, 6), f"{name} must keep alpha transparency"


def test_taiji_home_uses_lucide_svg_icon_slots_for_nav_and_quick_cards():
    for icon in (
        "messages-square",
        "clipboard-check",
        "kanban",
        "users-round",
        "blocks",
        "brain-circuit",
        "folder-open",
        "user-round-cog",
        "list-checks",
        "chart-column",
        "scroll-text",
        "settings",
        "calendar-check-2",
        "workflow",
        "terminal",
    ):
        assert f'data-icon="{icon}"' in INDEX_HTML

    assert 'class="taiji-icon taiji-nav-icon"' in INDEX_HTML
    assert 'class="taiji-icon taiji-quick-icon"' in INDEX_HTML
    assert ".taiji-icon svg{" in STYLE_CSS
    assert ".taiji-nav-icon{" in STYLE_CSS
    assert "width:30px;" in STYLE_CSS
    assert "height:30px;" in STYLE_CSS
    assert ".taiji-quick-icon{" in STYLE_CSS
    assert "width:38px;" in STYLE_CSS
    assert "height:38px;" in STYLE_CSS


def test_taiji_active_nav_icons_are_not_force_inverted():
    active_start = STYLE_CSS.index(":root[data-skin] .taiji-home-shell .taiji-nav-item.is-active .taiji-nav-icon{")
    active_rule = STYLE_CSS[active_start : STYLE_CSS.index("}", active_start)]
    assert "invert(1)" not in active_rule
    assert "brightness(0)" not in active_rule


def test_taiji_composer_uses_svg_controls_instead_of_png_pseudo_icons():
    for control_class in (
        "composer-control",
        "composer-control-icon",
        "composer-control-label",
    ):
        assert control_class in INDEX_HTML
        assert f".{control_class}" in STYLE_CSS

    assert "#composerWrap #btnAttach::after" not in STYLE_CSS
    assert "#composerWrap #btnMic::after" not in STYLE_CSS
    assert "background-image:var(--taiji-action-attach)" not in STYLE_CSS
    assert "background-image:var(--taiji-action-voice)" not in STYLE_CSS
    assert "static/icons.js?v=__WEBUI_VERSION__-taiji-shell-34" in INDEX_HTML
    assert "static/style.css?v=__WEBUI_VERSION__-taiji-shell-34" in INDEX_HTML


def test_taiji_composer_layout_does_not_clip_or_force_optional_toolsets():
    assert ".taiji-home-shell #composerWrap .composer-left{\n    flex:1 1 auto;\n    overflow:hidden;" not in STYLE_CSS
    assert ".taiji-home-shell #composerWrap .composer-toolsets-wrap{\n    display:block!important;" not in STYLE_CSS
    assert ":root[data-skin] .taiji-home-shell #composerWrap .composer-left{" in STYLE_CSS
    assert "overflow-x:auto!important;" in STYLE_CSS
    assert "overflow-y:visible!important;" in STYLE_CSS
    assert ".taiji-home-shell #composerWrap .composer-toolsets-wrap{display:none!important;}" in STYLE_CSS


def test_taiji_redesign_tokens_and_reading_surface_are_present():
    """The Taiji desktop shell should be driven by a calm workbench token set."""
    for token in (
        "--taiji-ui-bg",
        "--taiji-ui-panel",
        "--taiji-ui-reading",
        "--taiji-ui-line",
        "--taiji-ui-text",
        "--taiji-ui-muted",
        "--taiji-ui-accent",
        "--taiji-ui-shadow",
    ):
        assert token in STYLE_CSS

    assert ".taiji-home-shell .taiji-main-workspace .taiji-real-main{" in STYLE_CSS
    assert ".taiji-home-shell.taiji-chat-has-messages main.main.taiji-real-main #mainChat .messages-shell{" in STYLE_CSS
    assert "max-width:min(760px,calc(100% - 56px))" in STYLE_CSS
    assert "background:var(--taiji-ui-reading)" in STYLE_CSS
    assert "background-grid.png" in STYLE_CSS


def test_taiji_redesign_nav_and_composer_visual_weight_are_unified():
    nav_rule_start = STYLE_CSS.rindex(".taiji-home-shell .taiji-nav-item{")
    nav_rule = STYLE_CSS[nav_rule_start : STYLE_CSS.index("}", nav_rule_start)]
    assert "min-height:44px" in nav_rule
    assert "font-size:15px" in nav_rule

    icon_rule_start = STYLE_CSS.rindex(".taiji-home-shell .taiji-nav-icon{")
    icon_rule = STYLE_CSS[icon_rule_start : STYLE_CSS.index("}", icon_rule_start)]
    assert "width:32px" in icon_rule
    assert "height:32px" in icon_rule

    composer_rule_start = STYLE_CSS.rindex(".taiji-home-shell #composerWrap .composer-control{")
    composer_rule = STYLE_CSS[composer_rule_start : STYLE_CSS.index("}", composer_rule_start)]
    assert "height:44px" in composer_rule
    assert "border-radius:12px" in composer_rule

    assert ".taiji-home-shell #composerWrap .composer-workspace-group:has(.composer-workspace-chip[data-ui-visibility-hidden=\"1\"])" in STYLE_CSS
    assert ".taiji-home-shell #composerWrap .composer-control:empty" in STYLE_CSS


def test_taiji_redesign_writeflow_dock_matches_composer_system():
    assert ".taiji-home-shell #writeflowStatusDock .status-card-writeflow-mini{" in STYLE_CSS
    assert "grid-template-columns:auto minmax(0,1fr) auto auto auto auto" in STYLE_CSS
    assert ".taiji-home-shell #writeflowStatusDock .status-card-writeflow.is-expanded{" in STYLE_CSS
    assert "background:var(--taiji-ui-panel-solid)" in STYLE_CSS


def test_taiji_welcome_keeps_composer_visible_for_quick_prompts():
    assert ':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell.taiji-welcome .taiji-main-workspace > main.main.taiji-real-main{' in STYLE_CSS
    assert ':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell.taiji-welcome #composerWrap{' in STYLE_CSS
    assert '.taiji-home-shell.taiji-welcome main.main.taiji-real-main #mainChat .messages-shell{' in STYLE_CSS
    assert "display:none!important;" in STYLE_CSS[
        STYLE_CSS.rindex(".taiji-home-shell.taiji-welcome main.main.taiji-real-main #mainChat .messages-shell{") :
        STYLE_CSS.index("}", STYLE_CSS.rindex(".taiji-home-shell.taiji-welcome main.main.taiji-real-main #mainChat .messages-shell{"))
    ]


def test_taiji_chat_restores_avatars_and_uses_single_message_surface():
    assistant_role_start = STYLE_CSS.rindex('.taiji-home-shell main.main.taiji-real-main .msg-role.assistant{')
    assistant_role = STYLE_CSS[assistant_role_start : STYLE_CSS.index("}", assistant_role_start)]
    assert "display:grid!important" in assistant_role
    assert "display:none" not in assistant_role

    user_avatar_start = STYLE_CSS.rindex('.taiji-home-shell main.main.taiji-real-main .msg-row[data-role="user"]::after{')
    user_avatar = STYLE_CSS[user_avatar_start : STYLE_CSS.index("}", user_avatar_start)]
    assert "width:40px" in user_avatar
    assert "height:40px" in user_avatar
    assert "align-self:start" in user_avatar
    assert "var(--taiji-user-avatar)" in user_avatar
    assert "var(--taiji-action-user)" not in user_avatar

    messages_shell_start = STYLE_CSS.rindex(':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell.taiji-chat-has-messages main.main.taiji-real-main #mainChat .messages-shell{')
    messages_shell = STYLE_CSS[messages_shell_start : STYLE_CSS.index("}", messages_shell_start)]
    assert "background:transparent!important" in messages_shell
    assert "border:0!important" in messages_shell
    assert "box-shadow:none!important" in messages_shell


def test_taiji_secondary_collapse_expands_workspace_in_final_override():
    collapsed_rule = _css_rule(':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell[data-secondary-collapsed="1"]{')
    assert "grid-template-columns:var(--taiji-brand-w) minmax(0,1fr)!important" in collapsed_rule

    toggle_rule = _css_rule(':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell[data-secondary-collapsed="1"] .taiji-secondary-toggle{')
    assert "left:calc(var(--taiji-shell-pad) + var(--taiji-brand-w) + (var(--taiji-gap) / 2))!important" in toggle_rule

    expanded_messages_rule = _css_rule(':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell.taiji-chat-has-messages main.main.taiji-real-main #mainChat .messages-shell{')
    assert "width:min(860px,calc(100% - 72px))!important" in expanded_messages_rule
    assert "max-width:min(860px,calc(100% - 72px))!important" in expanded_messages_rule

    collapsed_messages_rule = _css_rule(':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell[data-secondary-collapsed="1"].taiji-chat-has-messages main.main.taiji-real-main #mainChat .messages-shell{')
    assert "width:min(1020px,calc(100% - 96px))!important" in collapsed_messages_rule
    assert "max-width:min(1020px,calc(100% - 96px))!important" in collapsed_messages_rule

    collapsed_composer_rule = _css_rule(':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell[data-secondary-collapsed="1"] #composerWrap{')
    assert "width:min(1020px,calc(100% - 96px))!important" in collapsed_composer_rule
    assert "max-width:min(1020px,calc(100% - 96px))!important" in collapsed_composer_rule

    collapsed_body_rule = _css_rule(':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] .taiji-home-shell[data-secondary-collapsed="1"] main.main.taiji-real-main .assistant-segment .msg-body,')
    assert "font-size:14.5px!important" in collapsed_body_rule
    assert "font-size:16px!important" not in collapsed_body_rule


def test_lucide_registry_contains_all_taiji_shell_icons():
    for icon in (
        "messages-square",
        "clipboard-check",
        "kanban",
        "users-round",
        "blocks",
        "brain-circuit",
        "folder-open",
        "user-round-cog",
        "list-checks",
        "chart-column",
        "scroll-text",
        "calendar-check-2",
        "workflow",
        "terminal",
        "settings",
        "paperclip",
        "mic",
        "audio-lines",
        "bot",
        "gauge",
        "wrench",
        "sliders-horizontal",
        "arrow-up",
        "globe",
    ):
        assert f"'{icon}':" in ICONS_JS


def test_taiji_user_avatar_cache_bust_and_asset_source_are_locked():
    assert "taiji-shell-21" not in INDEX_HTML
    assert "taiji-shell-22" not in INDEX_HTML
    assert "taiji-shell-23" not in INDEX_HTML
    assert "taiji-shell-32" not in INDEX_HTML
    assert "taiji-shell-33" not in INDEX_HTML
    assert "taiji-shell-34" in INDEX_HTML

    user_avatar_start = STYLE_CSS.rindex('.taiji-home-shell main.main.taiji-real-main .msg-row[data-role="user"]::after{')
    user_avatar = STYLE_CSS[user_avatar_start : STYLE_CSS.index("}", user_avatar_start)]
    assert "var(--taiji-user-avatar)" in user_avatar
    assert "var(--taiji-action-user)" not in user_avatar


def test_taiji_mic_and_voice_mode_icons_are_distinct_controls():
    assert "'mic': LI_PATHS['audio-lines']" not in ICONS_JS

    mic_path_start = ICONS_JS.index("'mic':")
    mic_path = ICONS_JS[mic_path_start : ICONS_JS.index(",", mic_path_start)]
    audio_path_start = ICONS_JS.index("'audio-lines':")
    audio_path = ICONS_JS[audio_path_start : ICONS_JS.index(",", audio_path_start)]
    assert mic_path != audio_path
    assert "M12 2a3" in mic_path
    assert "M2 10v4" not in mic_path
    assert "M2 10v4" in audio_path

    mic_button_start = INDEX_HTML.index('id="btnMic"')
    mic_button = INDEX_HTML[INDEX_HTML.rfind("<button", 0, mic_button_start) : INDEX_HTML.index(">", mic_button_start)]
    voice_button_start = INDEX_HTML.index('id="btnVoiceMode"')
    voice_button = INDEX_HTML[INDEX_HTML.rfind("<button", 0, voice_button_start) : INDEX_HTML.index(">", voice_button_start)]

    assert 'aria-label="语音输入"' in mic_button
    assert 'title="Dictate"' in mic_button
    assert 'data-tooltip="Dictate"' in mic_button
    assert 'aria-label="语音模式"' in voice_button
    assert 'title="Voice mode"' in voice_button
    assert 'data-tooltip="Voice mode"' in voice_button


def test_taiji_recent_session_running_state_is_separated_from_time_column():
    home_js = (ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")
    assert "function taijiSessionKind(session)" in home_js
    assert 'class="taiji-session-meta"' in home_js
    assert 'class="taiji-session-kind"' in home_js
    assert '<span class="taiji-session-live">运行</span>' in home_js

    row_start = home_js.index('class="taiji-session-row')
    row_template = home_js[row_start : home_js.index("</div>`", row_start)]
    meta_start = row_template.index('taiji-session-meta')
    meta_end = row_template.index("</span>", meta_start)
    meta_template = row_template[meta_start:meta_end]
    assert 'taiji-session-kind' in row_template
    assert '${badge}' in row_template
    assert 'taiji-session-title' in row_template
    assert 'taiji-session-meta' in row_template
    assert row_template.index('taiji-session-kind') < row_template.index('taiji-session-title')
    assert row_template.index('${badge}') < row_template.index('taiji-session-title')
    assert row_template.index('taiji-session-title') < row_template.index('taiji-session-meta')
    assert 'taiji-session-more' in row_template
    assert row_template.index('taiji-session-meta') < row_template.index('taiji-session-more')
    assert 'taiji-session-live' not in meta_template
    assert '<time>' not in row_template
    assert '<time class="taiji-session-time">' in row_template

    row_rule_start = STYLE_CSS.rindex(':root[data-skin="taiji-light-glass"] .taiji-home-shell .taiji-session-row{')
    row_rule = STYLE_CSS[row_rule_start : STYLE_CSS.index("}", row_rule_start)]
    assert "display:grid!important" in row_rule
    assert "grid-template-columns:minmax(0,1fr) 30px!important" in row_rule

    base_row_rule_start = STYLE_CSS.index('  .taiji-session-card .taiji-session-row{')
    base_row_rule = STYLE_CSS[base_row_rule_start : STYLE_CSS.index("}", base_row_rule_start)]
    assert "display:grid" in base_row_rule
    assert "grid-template-columns:minmax(0,1fr) 30px" in base_row_rule

    meta_rule_start = STYLE_CSS.rindex(':root[data-skin="taiji-light-glass"] .taiji-home-shell .taiji-session-meta{')
    meta_rule = STYLE_CSS[meta_rule_start : STYLE_CSS.index("}", meta_rule_start)]
    assert "display:flex!important" in meta_rule
    assert "align-items:flex-end!important" in meta_rule
    assert "min-width:" in meta_rule

    kind_rule_start = STYLE_CSS.rindex(':root[data-skin="taiji-light-glass"] .taiji-home-shell .taiji-session-kind{')
    kind_rule = STYLE_CSS[kind_rule_start : STYLE_CSS.index("}", kind_rule_start)]
    assert "white-space:nowrap!important" in kind_rule
    assert "font-weight:600!important" in kind_rule

    action_separator_start = STYLE_CSS.rindex(':root[data-skin="taiji-light-glass"] .taiji-home-shell .taiji-session-action-separator{')
    action_separator_rule = STYLE_CSS[action_separator_start : STYLE_CSS.index("}", action_separator_start)]
    assert "width:1px!important" in action_separator_rule
    assert "background:" in action_separator_rule

    base_action_separator_start = STYLE_CSS.index('  .taiji-session-action-separator{')
    base_action_separator_rule = STYLE_CSS[base_action_separator_start : STYLE_CSS.index("}", base_action_separator_start)]
    assert "width:1px" in base_action_separator_rule
    assert "background:" in base_action_separator_rule

    live_rule_start = STYLE_CSS.rindex(':root[data-skin="taiji-light-glass"] .taiji-home-shell .taiji-session-live{')
    live_rule = STYLE_CSS[live_rule_start : STYLE_CSS.index("}", live_rule_start)]
    assert "position:static!important" in live_rule
    assert "transform:none!important" in live_rule


def test_taiji_voice_mode_is_not_a_default_primary_composer_button():
    boot_js = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
    assert "_taijiVoiceModePrimarySurfaceEnabled" in boot_js
    assert "modeBtn.style.display = enabled ? '' : 'none';" not in boot_js
    assert "const showPrimary=enabled && _taijiVoiceModePrimarySurfaceEnabled();" in boot_js
    assert "modeBtn.style.setProperty('display','none','important')" in boot_js
    assert "modeBtn.hidden=!showPrimary" in boot_js


def test_taiji_mic_unsupported_path_surfaces_feedback():
    boot_js = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
    assert "function _showMicUnavailable" in boot_js
    assert "mic_unavailable" in boot_js
    assert "btn.disabled=true" in boot_js
    assert "btn.onclick=()=>_showMicUnavailable();" in boot_js


def test_taiji_workspace_composer_controls_have_distinct_semantics():
    assert 'id="btnWorkspacePanelToggle"' in INDEX_HTML
    assert 'aria-label="打开工作区文件面板"' in INDEX_HTML
    assert 'data-tooltip="工作区文件"' in INDEX_HTML
    assert 'id="composerWorkspaceChip"' in INDEX_HTML
    assert 'aria-label="切换工作区"' in INDEX_HTML
    assert 'aria-haspopup="listbox"' in INDEX_HTML
    assert 'aria-expanded="false"' in INDEX_HTML

    group_rule_start = STYLE_CSS.rindex(':root[data-skin="taiji-light-glass"] .taiji-home-shell #composerWrap .composer-workspace-group{')
    group_rule = STYLE_CSS[group_rule_start : STYLE_CSS.index("}", group_rule_start)]
    assert "gap:8px!important" in group_rule
    assert "border:0!important" in group_rule
    assert "background:transparent!important" in group_rule


def test_taiji_inactive_composer_flyouts_do_not_keep_hit_test_geometry():
    for selector in (
        ".approval-card:not(.visible)",
        ".clarify-card:not(.visible)",
        ".queue-card:not(.visible)",
    ):
        assert selector in STYLE_CSS
        rule_start = STYLE_CSS.rindex(selector)
        rule = STYLE_CSS[rule_start : STYLE_CSS.index("}", rule_start)]
        assert "display:none!important" in rule
        assert "visibility:hidden!important" in rule
        assert "pointer-events:none!important" in rule

    for selector in (
        ".approval-card.visible",
        ".clarify-card.visible",
        ".queue-card.visible",
    ):
        selector_start = f"{selector},"
        if selector_start not in STYLE_CSS:
            selector_start = f"{selector}{{"
        assert selector_start in STYLE_CSS
        rule_start = STYLE_CSS.index(selector_start)
        rule = STYLE_CSS[rule_start : STYLE_CSS.index("}", rule_start)]
        assert "display:block!important" in rule
