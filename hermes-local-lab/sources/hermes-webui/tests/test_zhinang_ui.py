from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_zhinang_shell_assets_and_three_navigation_surfaces():
    index = _read("index.html")

    assert index.count('data-taiji-panel="zhinang"') == 1
    assert index.count('data-panel="zhinang"') == 2
    assert 'id="panelZhinang"' in index
    assert 'id="mainZhinang"' in index
    assert 'id="zhinangSessionRole"' in index
    assert 'data-zhinang-view="recent"' in index
    assert 'static/zhinang.css?v=__WEBUI_VERSION__' in index
    assert 'static/zhinang.js?v=__WEBUI_VERSION__' in index
    assert index.index("static/sessions.js") < index.index("static/zhinang.js")


def test_zhinang_runtime_owns_filters_races_favorites_and_session_entry():
    script = _read("zhinang.js")

    for marker in (
        "window.TaijiZhinang",
        "new AbortController",
        "catalogGeneration",
        "detailGeneration",
        "profileGeneration",
        "setTimeout(runSearch, 200)",
        "scope:'all'",
        "view:'featured'",
        "scope:'favorites'",
        "encodeURIComponent(roleId)",
        "method:'PUT'",
        "aria-pressed",
        "createZhinangSession",
        "awaitCurrentDraftSave",
        "requestIdForRole",
        "continue_session_id",
        "data-zhinang-starter",
        "data-zhinang-action=\"reset-filters\"",
        "data-zhinang-retry-role",
        "preserveOnError",
        "async function activate(){bind();await refreshIfProfileChanged(true)",
        "/api/zhinang/session-role",
    ):
        assert marker in script

    assert "onclick=" not in script
    assert "effective_prompt" not in script
    assert "function createRoleTask(roleId,draftText='')" in script
    assert ":{scope:'all',category:'all',view:'all',query:''});return;" in script


def test_zhinang_details_are_accessible_and_distinguish_examples_from_files():
    script = _read("zhinang.js")
    style = _read("zhinang.css")

    for marker in (
        "能力范围",
        "适用边界",
        "交付物示例",
        "示例，非已生成文件",
        "开场示例",
        "原始角色说明",
        "适配说明",
        "上游来源",
        "完整 MIT 许可证",
        "role=\"dialog\"",
        "aria-modal",
        "focusableElements",
        "Escape",
    ):
        assert marker in script

    assert "@media (max-width:700px)" in style
    assert "@media (min-width:1180px)" in style
    assert "prefers-reduced-motion" in style
    assert ":focus-visible" in style


def test_zhinang_detail_only_links_http_upstream_sources():
    script = _read("zhinang.js")

    assert "function safeHttpUrl" in script
    assert "url.protocol==='http:'||url.protocol==='https:'" in script
    assert "const sourceUrl=safeHttpUrl(role.source_url)" in script
    assert 'href="${esc(sourceUrl)}"' in script


def test_zhinang_session_role_uses_a_desktop_safe_area_outside_the_welcome_hero():
    style = _read("zhinang.css")

    assert "@media (min-width:901px)" in style
    assert ".zhinang-session-role{top:24px;left:24px;transform:none;max-width:min(360px,42%)}" in style
    assert "cursor:pointer;pointer-events:auto;backdrop-filter" in style


def test_zhinang_is_wired_into_panel_profile_and_session_lifecycle():
    panels = _read("panels.js")
    home = _read("taiji-home.js")
    sessions = _read("sessions.js")
    ui = _read("ui.js")
    style = _read("style.css")

    assert "'zhinang'" in panels
    assert "TaijiZhinang.activate" in panels
    assert "TaijiZhinang.profileChanged" in panels
    assert "nextPanel === 'zhinang'" in panels
    assert "closeMobileSidebar()" in panels
    assert "zhinang:'智囊库'" in home
    assert "panelId:'panelZhinang'" in home
    assert "TaijiZhinang.syncSessionRole" in sessions
    assert "'zhinang'" in ui
    assert "showing-zhinang" in style
    assert "#mainZhinang" in style


def test_fixed_zhinang_role_disables_personality_command_entry():
    commands = _read("commands.js")

    assert "function _isFixedZhinangRoleSession()" in commands
    assert "固定智囊角色任务不支持切换个性" in commands
    assert "aria-disabled" in commands


def test_zhinang_script_has_valid_javascript_syntax():
    completed = subprocess.run(
        ["node", "--check", str(STATIC / "zhinang.js")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
