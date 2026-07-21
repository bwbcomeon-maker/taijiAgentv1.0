"""Regression coverage for Taiji desktop approval prompt visibility.

The approval/clarify cards live in the composer flyout. In the Taiji desktop
shell the composer is an absolute glass dock, so clipping the composer hides the
prompt above the input and leaves users with no visible approval entry.
"""

from pathlib import Path


ROOT = Path(__file__).parent.parent
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")


def _css_block(selector: str, *, last: bool = False) -> str:
    marker = f"{selector}{{"
    idx = STYLE_CSS.rfind(marker) if last else STYLE_CSS.find(marker)
    assert idx >= 0, f"CSS selector not found: {selector}"
    brace = idx + len(marker) - 1
    depth = 0
    for pos in range(brace, len(STYLE_CSS)):
        char = STYLE_CSS[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return STYLE_CSS[brace + 1 : pos]
    raise AssertionError(f"CSS block did not close: {selector}")


def test_taiji_desktop_composer_does_not_clip_approval_flyouts():
    """Pending approval UI must escape above the composer dock."""
    block = _css_block(
        ":root[data-taiji-desktop=\"1\"][data-skin] .taiji-home-shell #composerWrap",
        last=True,
    )

    assert "overflow:visible!important" in block
    assert "overflow:hidden!important" not in block


def test_taiji_desktop_approval_flyout_is_readable_above_composer():
    """Desktop approval prompts must be visible, layered, and readable."""
    marker = (
        ':root[data-taiji-desktop="1"][data-skin="taiji-light-glass"] '
        ".taiji-home-shell #composerWrap .approval-card"
    )
    idx = STYLE_CSS.find(marker)
    assert idx >= 0
    desktop_flyout = STYLE_CSS[idx : idx + 900]

    assert "bottom:48px!important" in desktop_flyout
    assert "z-index:60!important" in desktop_flyout
    assert "overflow:visible!important" in desktop_flyout
    assert "background:rgba(255,255,255,.98)!important" in desktop_flyout


def _js_function_body(name: str) -> str:
    marker = f"function {name}"
    idx = MESSAGES_JS.find(marker)
    assert idx >= 0, f"JS function not found: {name}"
    brace = MESSAGES_JS.find("{", idx)
    assert brace >= 0, f"JS function body not found: {name}"
    depth = 0
    for pos in range(brace, len(MESSAGES_JS)):
        char = MESSAGES_JS[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return MESSAGES_JS[brace + 1 : pos]
    raise AssertionError(f"JS function did not close: {name}")


def test_approval_polling_survives_idle_while_prompt_is_pending():
    """A pending approval is a blocking UI state, even after streaming goes idle."""
    polling = _js_function_body("startApprovalPolling")
    fallback = _js_function_body("_startApprovalFallbackPoll")

    assert "if (!S.busy || !S.session || S.session.session_id !== sid)" not in polling
    assert "if (!S.busy || !S.session || S.session.session_id !== sid)" not in fallback
    assert "if (!S.session || S.session.session_id !== sid)" in polling
    assert "if (!S.session || S.session.session_id !== sid)" in fallback
    assert "if (!S.busy) stopApprovalPollingForSession(sid)" in polling
    assert "if (!S.busy) stopApprovalPollingForSession(sid)" in fallback


def test_done_cleanup_keeps_remembered_pending_approval():
    """Stream completion must not erase an approval prompt that still needs action."""
    clear_owner = _js_function_body("_clearApprovalForOwner")

    assert "if(_hasApprovalPendingForSession(activeSid)) return;" in clear_owner
    assert clear_owner.index("_hasApprovalPendingForSession(activeSid)") < clear_owner.index(
        "_clearApprovalPendingForSession(activeSid)"
    )


def test_idle_session_load_refreshes_pending_approvals():
    """Reopening a desktop session with server-side pending approval should restore UI."""
    refresh = _js_function_body("refreshApprovalPendingForSession")

    assert "/api/approval/pending?session_id=" in refresh
    assert "showApprovalForSession(sid, data.pending" in refresh
    assert "startApprovalPolling(sid)" in refresh
    assert "refreshApprovalPendingForSession(sid)" in SESSIONS_JS


def test_failed_approval_response_reconciles_server_pending_state():
    """A failed approval response must not leave a stale local card/busy state."""
    respond = _js_function_body("respondApproval")

    assert "const result = await api(\"/api/approval/respond\"" in respond
    assert "if(!result.ok)" in respond
    assert "await refreshApprovalPendingForSession(sid)" in respond
    assert "_settleStaleApprovalUiForSession(sid)" in respond


def test_stale_approval_settlement_restores_idle_composer():
    """When the server has no pending approval, the desktop composer must become usable."""
    settle = _js_function_body("_settleStaleApprovalUiForSession")

    assert "S.busy=false" in settle
    assert "S.activeStreamId=null" in settle
    assert "session.active_stream_id=null" in settle
    assert "updateSendBtn()" in settle
