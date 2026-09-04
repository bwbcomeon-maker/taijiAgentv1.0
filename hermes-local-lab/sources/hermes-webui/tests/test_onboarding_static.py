import pathlib


REPO = pathlib.Path(__file__).parent.parent


def read(path):
    return (REPO / path).read_text(encoding="utf-8")


def test_index_contains_onboarding_overlay_markup():
    html = read("static/index.html")
    assert 'id="onboardingOverlay"' in html
    assert 'id="onboardingBody"' in html
    assert 'id="onboardingNextBtn"' in html
    assert 'id="onboardingResumeBtn"' in html
    assert 'id="onboardingResumeBtn" type="button"' in html
    assert 'data-i18n="onboarding_resume"' in html
    assert 'hidden' in html[html.index('id="onboardingResumeBtn"') : html.index('id="onboardingResumeBtn"') + 240]
    assert 'src="static/onboarding.js?v=__WEBUI_VERSION__"' in html


def test_escape_dismisses_without_marking_onboarding_complete():
    js = read("static/onboarding.js")
    dismiss_start = js.index("function dismissOnboardingWizard(")
    dismiss_body = js[dismiss_start : js.index("async function skipOnboarding()", dismiss_start)]

    assert "_getOnboardingDialog().close({restoreFocus:false})" in dismiss_body
    assert "_syncOnboardingResumeEntry()" in dismiss_body
    assert "/api/onboarding/complete" not in dismiss_body
    assert "ONBOARDING.active=false" not in dismiss_body


def test_incomplete_onboarding_has_refresh_free_keyboard_reentry():
    js = read("static/onboarding.js")

    assert "function _syncOnboardingResumeEntry()" in js
    assert "async function resumeOnboardingWizard()" in js
    resume_start = js.index("async function resumeOnboardingWizard()")
    resume_body = js[resume_start : js.index("function prevOnboardingStep()", resume_start)]
    assert "loadOnboardingWizard()" in resume_body
    assert "window.location.reload" not in resume_body
    assert "resume.focus()" in js
    completion_start = js.index("async function _finishOnboarding()")
    completion_body = js[completion_start : js.index("async function confirmOnboardingOverwrite", completion_start)]
    assert "_syncOnboardingResumeEntry()" in completion_body


def test_onboarding_css_rules_exist():
    css = read("static/style.css")
    for selector in (
        ".onboarding-overlay",
        ".onboarding-card",
        ".onboarding-step",
        ".onboarding-status.warn",
        ".onboarding-resume-entry",
    ):
        assert selector in css


def test_onboarding_resume_entry_yields_to_the_expanded_v3_workbench():
    v3_css = read("static/expert-team-v3.css")
    onboarding = read("static/onboarding.js")
    dismiss_start = onboarding.index("function dismissOnboardingWizard(")
    dismiss_body = onboarding[dismiss_start : onboarding.index("async function skipOnboarding()", dismiss_start)]

    assert "body.expert-team-v3-active:not(.expert-team-v3-collapsed)" in v3_css
    assert ".onboarding-resume-entry" in v3_css
    assert "@media (min-width: 1281px)" in v3_css
    assert "--et3-onboarding-resume-right" in v3_css
    assert "@media (max-width: 1280px)" in v3_css
    assert "display: none !important" in v3_css
    assert "getClientRects().length" in dismiss_body
    assert "[data-et3-action=\"close-workbench\"]" in dismiss_body


def test_probe_updates_in_place_without_replacing_the_focused_form():
    html = read("static/index.html")
    js = read("static/onboarding.js")
    setter_start = js.index("function _setOnboardingProbeState")
    setter_body = js[setter_start : js.index("async function _runOnboardingProbe", setter_start)]

    assert "_syncOnboardingProbeUi()" in setter_body
    assert "_renderOnboardingBody()" not in setter_body
    assert 'id="onboardingProbeStatus"' in js
    assert 'role="status"' in js
    assert 'aria-live="polite"' in js
    assert 'id="onboardingProbeBtn"' in js


def test_external_recovery_keeps_focus_in_the_selected_settings_section():
    html = read("static/index.html")
    js = read("static/onboarding.js")
    recovery_start = js.index("function openOnboardingRecovery")
    recovery_body = js[recovery_start : js.index("function _renderOnboardingOverwriteConflict", recovery_start)]

    assert "dismissOnboardingWizard({focusResume:false})" in recovery_body
    assert "focusSettingsRecoveryTarget(recovery.target_section,recovery.target_element)" in recovery_body
    assert "allowedTargets.has(String(targetElement||''))" in recovery_body
    assert "productDiagnosticsCard" in recovery_body
    assert "settingsSecurityProfileSelect" in recovery_body
    assert 'id="productDiagnosticsCard" tabindex="-1"' in html
    assert "prefers-reduced-motion: reduce" in recovery_body


def test_first_check_action_names_the_next_step():
    js = read("static/onboarding.js")
    sync_start = js.index("function _syncOnboardingActionState()")
    sync_body = js[sync_start : js.index("function _markOnboardingDirty()", sync_start)]

    assert "key==='system'?'进入配置'" in sync_body


def test_onboarding_sidebar_omits_step_list_and_preserves_mobile_action_order():
    html = read("static/index.html")
    js = read("static/onboarding.js")
    css = read("static/style.css")

    assert 'id="onboardingTitle" tabindex="-1"' in html
    assert 'id="onboardingSteps"' not in html
    assert 'id="onboardingLead"' in html
    assert "steps:['system','setup','workspace','password','finish']" in js
    assert ".onboarding-actions{flex-direction:column;}" in css
    assert ".onboarding-actions{flex-direction:column-reverse;}" not in css


def test_onboarding_js_exposes_bootstrap_hooks():
    js = read("static/onboarding.js")
    assert "async function loadOnboardingWizard()" in js
    assert "async function nextOnboardingStep()" in js
    assert "api('/api/onboarding/status')" in js
    assert "api('/api/onboarding/setup'" in js
    assert "api('/api/onboarding/complete'" in js


def test_boot_always_rechecks_server_side_onboarding_readiness():
    boot = read("static/boot.js")

    assert "const _onboardingReady=loadOnboardingWizard();" in boot
    assert (
        "_bootSettings.onboarding_completed?Promise.resolve(false):loadOnboardingWizard()"
        not in boot
    )


def test_initial_onboarding_status_failure_opens_retryable_fail_closed_state():
    js = read("static/onboarding.js")

    assert "statusLoadFailed:false" in js
    assert "async function retryOnboardingStatus()" in js
    assert 'id="onboardingStatusRetryBtn"' in js
    assert "initialStatusBlocked" in js
    assert "ONBOARDING.statusLoadFailed=true" in js
    assert "ONBOARDING.active=true" in js
    assert "onboarding status failed" in js
    assert "状态恢复前不能继续" in js
    assert "ONBOARDING.statusLoadFailed?'retryOnboardingStatus()':\"retryOnboardingCheck('all')\"" in js
    assert 'onclick="${retryAllAction}"' in js
    assert "requestAnimationFrame(()=>{const retry=$('onboardingStatusRetryBtn');if(retry)retry.focus();});" in js


def test_mobile_onboarding_prioritizes_the_workbench_over_step_descriptions():
    css = read("static/style.css")

    assert "grid-template-columns:repeat(5,minmax(0,1fr))" in css
    assert ".onboarding-step-desc{display:none;}" in css
    assert ".onboarding-sidebar>p{display:none;}" in css


def test_frontend_only_closes_after_consistent_completed_ready_response():
    js = read("static/onboarding.js")

    assert "done.completed!==true" in js
    assert "done.preflight.overall_ready!==true" in js
    completion_guard = js.index("done.completed!==true")
    close_dialog = js.index("_getOnboardingDialog().close()", completion_guard)
    assert completion_guard < close_dialog


def test_onboarding_exposes_recoverable_preflight_workbench_contract():
    html = read("static/index.html")
    js = read("static/onboarding.js")

    assert "开始使用前检查" in html
    assert "配置工作台" in html
    assert "api('/api/setup/status')" in js
    for stable_id in ("license", "model", "workspace", "security"):
        assert stable_id in js
    assert "retryOnboardingCheck" in js
    assert "confirmOnboardingOverwrite" in js
    assert "config_exists" in js
    assert "aria-live=\"polite\"" in js
    assert "aria-busy" in js
    assert ".disabled=" in js


def test_skip_only_dismisses_and_cannot_bypass_readiness_gate():
    js = read("static/onboarding.js")
    start = js.index("async function skipOnboarding()")
    body = js[start : js.index("async function nextOnboardingStep()", start)]

    assert "/api/onboarding/complete" not in body
    assert "dismissOnboardingWizard()" in body


def test_recovery_edits_reenable_save_and_recheck_action():
    js = read("static/onboarding.js")
    dirty_start = js.index("function _markOnboardingDirty()")
    dirty_body = js[dirty_start : js.index("function _setupStatusItem", dirty_start)]

    assert "ONBOARDING.savedOnce=false" in dirty_body
    assert "ONBOARDING.confirmOverwrite=false" in dirty_body
    assert "_markOnboardingDirty()" in js[js.index("function openOnboardingRecovery") :]
    for change_contract in (
        "ONBOARDING.form.apiKey=this.value;_markOnboardingDirty()",
        "ONBOARDING.form.baseUrl=this.value;_markOnboardingDirty()",
        "ONBOARDING.form.workspace=this.value;_markOnboardingDirty()",
        "ONBOARDING.form.model=this.value;_markOnboardingDirty()",
    ):
        assert change_contract in js


def test_onboarding_browser_smoke_is_checkout_bound_and_network_isolated():
    smoke = read("tests/onboarding_workbench_browser_smoke.py")

    assert "Path(__file__).resolve().parent.parent" in smoke
    assert 'git", "rev-parse", "--show-toplevel"' in smoke
    assert '"HERMES_WEBUI_AGENT_DIR": str(AGENT_ROOT)' in smoke
    assert '"TAIJI_WEBUI_AGENT_DIR": str(AGENT_ROOT)' in smoke
    assert 'route.abort("blockedbyclient")' in smoke
    assert "SETUP_ITEM_IDS = (\"license\", \"model\", \"workspace\", \"security\")" in smoke
    assert "confirm_overwrite" in smoke


def test_onboarding_uses_i18n_helpers():
    html = read("static/index.html")
    js = read("static/onboarding.js")
    i18n = read("static/i18n.js")
    assert 'data-i18n="onboarding_title"' in html
    assert 'data-i18n="onboarding_continue"' in html
    assert 'data-i18n="onboarding_resume"' in html
    assert "t('onboarding_step_system_title')" in js
    assert "t('onboarding_step_setup_title')" in js
    assert "t('onboarding_complete')" in js
    assert "onboarding_title: 'Welcome to taiji Agent'" in i18n
    assert "onboarding_title: 'Bienvenido a taiji Agent'" in i18n
    assert "onboarding_resume: 'Continue setup checks'" in i18n
    assert "onboarding_resume: '继续配置·开始使用检查'" in i18n
    assert i18n.count("onboarding_resume:") == 11
    assert "Hermes Web UI" not in i18n


def test_bootstrap_script_contains_official_installer_and_windows_guard():
    src = read("bootstrap.py")
    assert (
        "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
        in src
    )
    assert "Native Windows is not supported" in src
