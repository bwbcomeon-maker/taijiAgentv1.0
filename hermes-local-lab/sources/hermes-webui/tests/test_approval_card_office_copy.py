"""Office-user approval card contract.

The primary layer explains the decision in plain language. Raw security output
is optional detail and dangerous scopes require an explicit second decision.
"""

from pathlib import Path


ROOT = Path(__file__).parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
BOOT = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
I18N = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    marker = f"function {name}"
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : pos]
    raise AssertionError(f"unclosed function: {name}")


def test_card_has_plain_language_layers_and_accessible_disclosures():
    assert 'aria-modal="true"' in HTML
    assert 'id="approvalSummary"' in HTML
    assert 'id="approvalPermission"' in HTML
    assert 'id="approvalImpact"' in HTML
    assert 'id="approvalDetailsToggle"' in HTML
    assert 'aria-controls="approvalDetails"' in HTML
    assert 'aria-expanded="false"' in HTML
    assert 'id="approvalDetails"' in HTML
    assert 'id="approvalAdvancedToggle"' in HTML
    assert 'aria-controls="approvalAdvanced"' in HTML
    assert 'id="approvalConfirmation"' in HTML
    assert 'aria-live="polite"' in HTML


def test_only_three_decisions_are_in_the_primary_action_group():
    start = HTML.index('class="approval-primary-actions"')
    end = HTML.index('</div>', start)
    primary = HTML[start:end]
    assert 'id="approvalBtnOnce"' in primary
    assert 'id="approvalBtnSession"' in primary
    assert 'id="approvalBtnDeny"' in primary
    assert 'id="approvalBtnAlways"' not in primary
    assert 'id="approvalSkipAll"' not in primary


def test_raw_backend_copy_is_never_written_to_primary_message():
    body = _function_body(MESSAGES, "showApprovalCard")
    primary_writes = [
        line for line in body.splitlines()
        if any(target in line for target in ("approvalSummary", "approvalPermission", "approvalImpact"))
    ]
    assert primary_writes
    assert all("pending.description" not in line for line in primary_writes)
    assert all("pending.summary" not in line for line in primary_writes)
    assert '$("approvalDetailsText").textContent' in body


def test_known_url_risks_have_plain_language_primary_copy():
    assert 'shortened_url: ["approval_summary_shortened_url"' in MESSAGES
    assert 'homograph_url: ["approval_summary_homograph_url"' in MESSAGES
    assert "隐藏了真实地址的短链接" in I18N
    assert "疑似仿冒正规网站的网址" in I18N


def test_advanced_scopes_require_confirmation_and_respect_availability():
    show = _function_body(MESSAGES, "showApprovalCard")
    confirm = _function_body(MESSAGES, "requestApprovalAdvancedAction")
    assert "pending.allow_permanent" in show
    assert 'choices.includes("always")' in show
    assert "approvalBtnAlways" in show
    assert "approvalSkipAll" in show
    assert "confirmApprovalAdvancedAction" in confirm
    assert "respondApproval('always')" not in HTML
    assert "toggleYoloFromApproval()" not in HTML


def test_enter_no_longer_auto_approves_and_escape_denies():
    assert "respondApproval('once')" not in BOOT
    assert "respondApproval('deny')" in BOOT
    assert "approvalCard" in BOOT


def test_card_is_responsive_and_respects_reduced_motion():
    assert "@media (max-width:600px)" in CSS
    assert ".approval-primary-actions" in CSS
    assert "@media (prefers-reduced-motion:reduce)" in CSS
    assert ".approval-card" in CSS
    assert ':root.dark[data-skin="taiji-light-glass"] .approval-inner' in CSS
    assert "color:#704100" in CSS
