"""Tests for #1100 — Prism.js SRI integrity check no longer blocks theme CSS."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_prism_theme_link_has_no_integrity():
    """The prism-tomorrow.min.css link must not have an integrity attribute."""
    with open("static/index.html") as f:
        src = f.read()
    # Find the prism-theme link tag
    m = re.search(
        r'<link[^>]*id="prism-theme"[^>]*>',
        src
    )
    assert m, "prism-theme link must exist"
    link_tag = m.group(0)
    assert "integrity=" not in link_tag, \
        "prism-theme link must not have integrity attribute (causes intermittent failures)"


def test_prism_theme_link_is_vendored_without_crossorigin():
    """The offline vendored theme must not depend on CDN/CORS behavior."""
    with open("static/index.html") as f:
        src = f.read()
    m = re.search(
        r'<link[^>]*id="prism-theme"[^>]*>',
        src
    )
    assert m, "prism-theme link must exist"
    link_tag = m.group(0)
    assert "crossorigin" not in link_tag, \
        "a same-origin vendored stylesheet does not need crossorigin"


def test_prism_theme_version_pinned():
    """The prism CSS URL must pin the version to prevent breaking changes."""
    with open("static/index.html") as f:
        src = f.read()
    m = re.search(
        r'<link[^>]*id="prism-theme"[^>]*href="([^"]*)"[^>]*>',
        src
    )
    assert m, "prism-theme link must have href"
    href = m.group(1)
    assert href == "static/vendor/prismjs/1.29.0/themes/prism-tomorrow.min.css", \
        f"Prism CSS version must be pinned, found href: {href}"
    assert (ROOT / href).is_file()


def test_prism_js_is_vendored_and_version_pinned():
    """Offline Prism must load from the same-origin, versioned vendor tree."""
    with open("static/index.html") as f:
        src = f.read()
    assert (
        'src="static/vendor/prismjs/1.29.0/prism.min.js"' in src
    ), "Prism JS must use the pinned vendored copy"
    assert (
        ROOT / "static/vendor/prismjs/1.29.0/prism.min.js"
    ).is_file()
    assert "cdn.jsdelivr.net/npm/prismjs" not in src
    assert "unpkg.com/prismjs" not in src


def test_boot_js_set_resolved_theme_no_integrity():
    """_setResolvedTheme in boot.js must not re-apply integrity on theme switch."""
    with open("static/boot.js") as f:
        src = f.read()
    # _setResolvedTheme function must exist
    assert "_setResolvedTheme" in src, "_setResolvedTheme function must exist"
    # Must NOT assign link.integrity with a hash value
    assert not re.search(r'link\.integrity\s*=\s*["\']sha', src), \
        "_setResolvedTheme must not set link.integrity to an SRI hash"
    # Must NOT have a wantIntegrity variable
    assert "wantIntegrity" not in src, \
        "wantIntegrity variable should be removed from _setResolvedTheme"
    # Should clear integrity (set to empty) when switching theme
    assert re.search(r"link\.integrity\s*=\s*['\"]", src), \
        "_setResolvedTheme should clear link.integrity on theme switch"
