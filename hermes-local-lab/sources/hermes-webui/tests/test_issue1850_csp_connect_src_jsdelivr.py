"""Regression test for #1850 — CSP connect-src stays local for vendored xterm.

xterm.js, xterm-addon-fit, and xterm-addon-web-links are loaded from vendored
static assets. Their sourcemaps should not require a runtime CDN exception.
"""
import re
from pathlib import Path

_HELPERS_PY = Path(__file__).resolve().parents[1] / "api/helpers.py"


def _helpers_src() -> str:
    return _HELPERS_PY.read_text()


class TestCSPConnectSrcVendoredAssets:
    """connect-src should stay local after xterm source maps are vendored."""

    def test_connect_src_excludes_jsdelivr(self):
        """connect-src must not include https://cdn.jsdelivr.net."""
        src = _helpers_src()
        connect_match = re.search(r"connect-src\s+([^;]+);", src)
        assert connect_match, "connect-src directive must exist in CSP"
        assert "https://cdn.jsdelivr.net" not in connect_match.group(1), (
            "connect-src must not allow cdn.jsdelivr.net after xterm source maps "
            "are vendored for offline delivery"
        )

    def test_connect_src_still_includes_self(self):
        """connect-src must still include 'self'."""
        src = _helpers_src()
        connect_match = re.search(r"connect-src\s+([^;]+);", src)
        assert connect_match, "connect-src directive must exist in CSP"
        assert "'self'" in connect_match.group(1), (
            "connect-src must retain 'self' for local WebUI requests"
        )
