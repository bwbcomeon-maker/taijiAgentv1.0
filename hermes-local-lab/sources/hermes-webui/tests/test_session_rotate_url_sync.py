"""Regression tests for session id rotation URL sync."""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def test_stream_completion_syncs_rotated_session_id_to_tab_state():
    """When compact/restore returns a new session id, the tab anchor follows it."""
    # Completion and restore both sanitize the rotated session before carrying
    # ephemeral turn fields forward. URL/storage sync must follow that assignment.
    completion_marker = "S.session=(typeof sanitizeSessionRuntimeFields==='function'?sanitizeSessionRuntimeFields(d.session"
    settled_marker = "S.session=(typeof sanitizeSessionRuntimeFields==='function'?sanitizeSessionRuntimeFields(session"

    completion_pos = MESSAGES_JS.find(completion_marker)
    settled_pos = MESSAGES_JS.find(settled_marker)
    assert completion_pos != -1
    assert settled_pos != -1

    completion_block = MESSAGES_JS[completion_pos : completion_pos + 700]
    settled_block = MESSAGES_JS[settled_pos : settled_pos + 700]

    for block in (completion_block, settled_block):
        carry_idx = block.index("_carryForwardEphemeralTurnFields")
        storage_idx = block.index("localStorage.setItem('hermes-webui-session',S.session.session_id);")
        url_idx = block.index("_setActiveSessionUrl(S.session.session_id)")
        assert carry_idx < storage_idx < url_idx
        assert "localStorage.setItem('hermes-webui-session',S.session.session_id);" in block
        assert "_setActiveSessionUrl(S.session.session_id)" in block
        assert "typeof _setActiveSessionUrl==='function'" in block
