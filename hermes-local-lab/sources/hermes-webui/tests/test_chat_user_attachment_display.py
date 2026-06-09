"""Regression coverage for user-message attachment presentation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_user_attachments_render_inside_user_message_bubble():
    """Uploaded-file chips should travel with the user prompt, not as a side rail."""
    assert "const userBodyHtml=filesHtml?`${filesHtml}${bodyHtml}`:bodyHtml;" in UI_JS
    assert 'row.innerHTML=`<div class="msg-body">${userBodyHtml}</div>${footHtml}`;' in UI_JS
    assert 'row.innerHTML=`${filesHtml}<div class="msg-body">${bodyHtml}</div>${footHtml}`;' not in UI_JS


def test_user_attachment_badges_have_single_line_ellipsis_constraints():
    assert '<span class="msg-file-name">${esc(fname)}</span>' in UI_JS
    assert ".msg-row[data-role=\"user\"] .msg-body .msg-files" in STYLE_CSS
    assert ".msg-row[data-role=\"user\"] .msg-file-badge" in STYLE_CSS
    assert ".msg-file-name" in STYLE_CSS
    name_rule = STYLE_CSS[STYLE_CSS.index(".msg-file-name") : STYLE_CSS.index(".msg-file-name") + 240]
    assert "white-space: nowrap" in name_rule
    assert "overflow: hidden" in name_rule
    assert "text-overflow: ellipsis" in name_rule
    assert "min-width: 0" in name_rule
