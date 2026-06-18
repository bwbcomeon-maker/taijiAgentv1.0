"""Composer context indicator layout regression tests.

The desktop context usage ring appears after an assistant response. It must not
insert a new flex slot that shifts the send button after the reply completes.
"""

import pathlib
import re

REPO = pathlib.Path(__file__).parent.parent
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")


def _strip_css_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _rule_body(css, selector):
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _strip_css_comments(css)):
        selectors = {part.strip() for part in match.group(1).split(",")}
        if selector in selectors:
            return match.group(2)
    raise AssertionError(f"Missing CSS rule for {selector}")


def _rule_bodies(css, selector):
    bodies = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _strip_css_comments(css)):
        selectors = {part.strip() for part in match.group(1).split(",")}
        if selector in selectors:
            bodies.append(match.group(2))
    return bodies


def _declarations(rule_body):
    declarations = {}
    for item in rule_body.split(";"):
        if ":" not in item:
            continue
        prop, value = item.split(":", 1)
        declarations[prop.strip()] = re.sub(r"\s+", " ", value.strip())
    return declarations


def _function_body(source, name):
    start = source.index(f"function {name}")
    open_brace = source.index("{", start)
    depth = 0
    for idx in range(open_brace, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1:idx]
    raise AssertionError(f"Could not parse function {name}")


def test_context_indicator_wrap_reserves_base_slot():
    """The indicator wrapper must have intrinsic size before usage data exists."""
    wrap = _declarations(_rule_body(CSS, ".ctx-indicator-wrap"))

    assert wrap.get("width") == "34px"
    assert wrap.get("min-width") == "34px"
    assert wrap.get("height") == "34px"
    assert wrap.get("display") == "inline-flex"


def test_taiji_context_indicator_slot_matches_send_button_width():
    """Taiji desktop skin reserves the same slot width as the send button."""
    selector = (
        ':root[data-skin="taiji-light-glass"] .taiji-home-shell '
        "#composerWrap #ctxIndicatorWrap"
    )
    wrap = _declarations(_rule_body(CSS, selector))

    assert wrap.get("display") == "inline-flex!important"
    assert wrap.get("width") == "46px!important"
    assert wrap.get("min-width") == "46px!important"
    assert wrap.get("height") == "46px!important"


def test_taiji_composer_right_uses_content_sized_control_cluster():
    """The Taiji shell right controls must fit status, badges, ring, and send."""
    generic_bodies = _rule_bodies(CSS, ".taiji-home-shell #composerWrap .composer-right")
    assert generic_bodies, "Missing Taiji composer-right override"
    for body in generic_bodies:
        assert "width:clamp(40px,2.65vw,52px)" not in body.replace(" ", "")
        assert "min-width:clamp(40px,2.65vw,52px)" not in body.replace(" ", "")
        assert "height:clamp(40px,2.65vw,52px)" not in body.replace(" ", "")

    selector = (
        ':root[data-skin="taiji-light-glass"] .taiji-home-shell '
        "#composerWrap .composer-right"
    )
    skin_bodies = _rule_bodies(CSS, selector)
    assert skin_bodies, "Missing taiji-light-glass composer-right rule"
    right = _declarations(skin_bodies[-1])

    assert right.get("flex") == "0 0 auto!important"
    assert right.get("width") == "auto!important"
    assert right.get("min-width") == "max-content!important"
    assert right.get("height") == "auto!important"
    assert right.get("justify-content") == "flex-end!important"
    assert right.get("overflow") == "visible!important"


def test_context_indicator_visibility_does_not_collapse_send_slot():
    """Usage sync should hide via visibility, not remove the slot from layout."""
    sync_body = _function_body(UI_JS, "_syncCtxIndicator")
    helper_body = _function_body(UI_JS, "_setCtxIndicatorSlotVisible")

    assert "wrap.style.display='none'" not in sync_body
    assert 'wrap.style.display="none"' not in sync_body
    assert "wrap.style.display='';" in helper_body
    assert "wrap.style.visibility=visible?'visible':'hidden';" in helper_body
    assert "wrap.style.pointerEvents=visible?'auto':'none';" in helper_body


def test_context_indicator_markup_does_not_start_display_none():
    """Initial markup must reserve the indicator slot before the first reply."""
    start = HTML.index('id="ctxIndicatorWrap"')
    tag_start = HTML.rfind("<div", 0, start)
    tag_end = HTML.index(">", start)
    tag = HTML[tag_start:tag_end]

    assert "display:none" not in tag.replace(" ", "")
