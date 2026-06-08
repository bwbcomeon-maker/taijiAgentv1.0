from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
DESKTOP_MAIN_JS = (REPO_ROOT / "apps" / "taiji-desktop" / "src" / "main.js").read_text(encoding="utf-8")


def test_app_titlebar_no_longer_contains_tps_chip():
    assert 'id="tpsStat"' not in INDEX_HTML


def test_app_titlebar_returns_to_centered_desktop_layout():
    assert ".app-titlebar{display:flex;align-items:center;justify-content:center;" in STYLE_CSS
    assert ".app-titlebar-inner{display:flex;align-items:center;gap:8px;min-width:0;max-width:100%;justify-content:center;}" in STYLE_CSS


def test_app_titlebar_subtitle_shows_message_count_again():
    assert "subText = t('n_messages', vis.length);" in PANELS_JS


def test_queue_updates_do_not_hijack_app_titlebar_subtitle():
    assert "_syncQueueTitlebar" not in UI_JS


def test_taiji_shell_uses_short_native_window_title():
    assert "if(document.querySelector('.taiji-home-shell')){" in UI_JS
    assert "document.title=assistantDisplayName();" in UI_JS
    assert "document.title=sessionTitle+' \\u2014 '+assistantDisplayName();" in UI_JS


def test_taiji_desktop_polish_v2_keeps_shell_cohesive():
    assert "Taiji desktop polish v2" in STYLE_CSS
    assert "--taiji-polish-panel:rgba(255,255,255,.72);" in STYLE_CSS
    assert "--taiji-polish-shadow:0 6px 18px rgba(37,82,126,.07)" in STYLE_CSS
    assert ":root[data-skin] .taiji-home-shell .taiji-brand-sidebar," in STYLE_CSS
    assert ":root[data-skin] .taiji-home-shell .taiji-secondary-panel," in STYLE_CSS
    assert ":root[data-skin] .taiji-home-shell .taiji-main-workspace{" in STYLE_CSS
    assert "border-radius:14px!important;" in STYLE_CSS


def test_taiji_session_list_and_composer_are_lightweight():
    assert ":root[data-skin] .taiji-home-shell .taiji-session-card{" in STYLE_CSS
    assert "background:rgba(255,255,255,.28)!important;" in STYLE_CSS
    assert "font-weight:460!important;" in STYLE_CSS
    assert "background:rgba(10,174,195,.12)!important;" in STYLE_CSS
    assert ":root[data-skin] .taiji-home-shell #composerWrap{" in STYLE_CSS
    assert "left:24px!important;" in STYLE_CSS
    assert "right:24px!important;" in STYLE_CSS


def test_taiji_writeflow_dock_aligns_with_composer():
    assert ':root[data-skin] .taiji-home-shell #writeflowStatusDock{' in STYLE_CSS
    assert "width:100%!important;" in STYLE_CSS
    assert "max-width:none!important;" in STYLE_CSS
    assert "margin:0 0 8px!important;" in STYLE_CSS


def test_taiji_desktop_uses_native_integrated_titlebar():
    assert 'const DESKTOP_CHROME_BACKGROUND = "#eaf7ff";' in DESKTOP_MAIN_JS
    assert 'titleBarStyle: "hiddenInset"' in DESKTOP_MAIN_JS
    assert "trafficLightPosition: { x: 16, y: 16 }" in DESKTOP_MAIN_JS
    assert "backgroundColor: DESKTOP_CHROME_BACKGROUND" in DESKTOP_MAIN_JS


def test_taiji_desktop_loads_webui_with_desktop_marker():
    assert 'target.searchParams.set("taiji_desktop", "1");' in DESKTOP_MAIN_JS
    assert "await mainWindow.loadURL(target.toString());" in DESKTOP_MAIN_JS
    assert "dataset.taijiDesktop='1'" in INDEX_HTML


def test_taiji_desktop_shell_has_safe_drag_region_and_visible_grid_background():
    assert ':root[data-taiji-desktop="1"][data-skin] .taiji-home-shell{' in STYLE_CSS
    assert "--taiji-desktop-titlebar-h:38px;" in STYLE_CSS
    assert "-webkit-app-region:drag;" in STYLE_CSS
    assert "-webkit-app-region:no-drag;" in STYLE_CSS
    assert ':root[data-taiji-desktop="1"][data-skin] .taiji-home-shell .taiji-main-workspace{' in STYLE_CSS
    assert 'url("assets/taiji/background/background-grid.png")!important;' in STYLE_CSS
    assert "background-position:center right!important;" in STYLE_CSS


def test_taiji_desktop_bottom_operation_band_hides_background_lines():
    assert ':root[data-taiji-desktop="1"][data-skin] .taiji-home-shell #composerWrap{' in STYLE_CSS
    assert "background:rgba(251,254,255,.9)!important;" in STYLE_CSS
    assert "overflow:hidden!important;" in STYLE_CSS
    assert ':root[data-taiji-desktop="1"][data-skin] .taiji-home-shell #writeflowStatusDock{' in STYLE_CSS
    assert "max-height:56px!important;" in STYLE_CSS
    assert "max-height:min(38vh,420px)!important;" in STYLE_CSS
