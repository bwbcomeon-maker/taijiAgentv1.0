from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_managed_dialog_owns_keyboard_and_focus_lifecycle():
    js = read("static/managed-dialog.js")

    for contract in (
        "const ManagedDialog",
        "document.addEventListener('keydown'",
        "document.removeEventListener('keydown'",
        "event.key==='Tab'",
        "event.key==='Escape'",
        "event.stopImmediatePropagation()",
        "event.shiftKey",
        "document.activeElement",
        "root.contains(activeElement)",
        "previousFocus===document.body",
        "returnFocus",
        "initialFocus",
        "closeOnBackdrop",
    ):
        assert contract in js


def test_managed_dialog_loads_before_its_consumers():
    html = read("static/index.html")

    managed = html.index('src="static/managed-dialog.js?v=__WEBUI_VERSION__"')
    expert = html.index('src="static/expert-team-ui.js?v=__WEBUI_VERSION__"')
    panels = html.index('src="static/panels.js?v=__WEBUI_VERSION__"')
    onboarding = html.index('src="static/onboarding.js?v=__WEBUI_VERSION__"')
    assert managed < expert < panels < onboarding


def test_high_risk_dialogs_expose_name_and_description():
    html = read("static/index.html")

    onboarding = html[html.index('id="onboardingOverlay"') : html.index('id="writeflowTeamModal"')]
    assert 'role="dialog"' in onboarding
    assert 'aria-modal="true"' in onboarding
    assert 'aria-labelledby="onboardingTitle"' in onboarding
    assert 'aria-describedby="onboardingLead"' in onboarding

    expert = html[html.index('id="writeflowTeamModal"') : html.index('id="taijiFloatingLayer"')]
    assert 'role="dialog"' in expert
    assert 'aria-modal="true"' in expert
    assert 'aria-labelledby="writeflowTeamModalTitle"' in expert
    assert 'aria-describedby="writeflowTeamModalDescription"' in expert


def test_high_risk_dialogs_use_managed_dialog_controllers():
    onboarding = read("static/onboarding.js")
    panels = read("static/panels.js")
    boot = read("static/boot.js")

    assert "ManagedDialog.create" in onboarding
    assert "initialFocus:'#onboardingNextBtn'" in onboarding
    assert "closeOnBackdrop:false" in onboarding
    assert "function dismissOnboardingWizard()" in onboarding
    assert "_getOnboardingDialog().close()" in onboarding

    assert "ManagedDialog.create" in panels
    assert "initialFocus:'#writeflowTeamModalTitle'" in panels
    assert '<h3 id="writeflowTeamModalTitle" tabindex="-1">' in panels
    assert "returnFocus:()=>_findWriteflowTeamTrigger(_writeflowModalTeamId)" in panels
    assert "closeOnBackdrop:true" in panels
    assert "_getWriteflowTeamDialog().open()" in panels
    assert "_getWriteflowTeamDialog().close()" in panels

    escape_block = boot[boot.index("if(e.key==='Escape')") :]
    assert "dismissOnboardingWizard" in escape_block
    assert "skipOnboarding" not in escape_block[:700]
