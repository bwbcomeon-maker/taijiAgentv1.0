"""Responsive contracts for Settings -> Model configuration."""

import re
from pathlib import Path


ROOT = Path(__file__).parent.parent
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _balanced_block(css: str, opening_pattern: str) -> str:
    match = re.search(opening_pattern, css)
    assert match, f"Missing CSS block matching {opening_pattern!r}"
    open_brace = css.find("{", match.start())
    depth = 0
    for index in range(open_brace, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace + 1 : index]
    raise AssertionError("Unbalanced CSS block")


def test_todos_is_hidden_by_default_when_settings_is_the_active_view():
    """Every main view, including todos, must start from the same hidden base state."""
    hidden_rule = re.search(
        r"(?P<selectors>main\.main\s*>\s*#mainChat,.*?main\.main\s*>\s*#mainLogs)"
        r"\s*\{\s*display\s*:\s*none\s*;\s*\}",
        STYLE_CSS,
        re.DOTALL,
    )
    assert hidden_rule, "Missing the generalized hidden-by-default main-view rule"
    assert "main.main > #mainTodos" in hidden_rule.group("selectors"), (
        "#mainTodos must be hidden by default so showing-settings cannot expose two main views"
    )
    assert re.search(
        r"main\.main\.showing-todos\s*>\s*#mainTodos\s*\{\s*display\s*:\s*flex\s*;\s*\}",
        STYLE_CSS,
    ), "The generalized view switch must still reveal todos when showing-todos is active"


def test_model_config_uses_its_real_content_width_for_compact_layout():
    """Sidebars can narrow the model pane even while the viewport remains desktop-sized."""
    pane_rule = re.search(r"#settingsPaneModels\s*\{(?P<body>[^}]*)\}", STYLE_CSS)
    assert pane_rule, "#settingsPaneModels needs a responsive container contract"
    declarations = pane_rule.group("body").replace(" ", "")
    assert "container-type:inline-size" in declarations
    assert "container-name:model-config" in declarations

    compact = _balanced_block(
        STYLE_CSS,
        r"@container\s+model-config\s*\(\s*max-width\s*:\s*880px\s*\)",
    )
    compact_no_space = compact.replace(" ", "")
    assert "#settingsPaneModels .model-config-license-strip" in compact
    assert "#settingsPaneModels .model-config-image-capability-grid" in compact
    assert "#settingsPaneModels .model-config-license-facts" in compact
    assert "grid-template-columns:1fr" in compact_no_space
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in compact_no_space
