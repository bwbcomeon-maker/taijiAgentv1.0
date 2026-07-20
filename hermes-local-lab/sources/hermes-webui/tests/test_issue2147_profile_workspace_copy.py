"""Regression tests for issue #2147 profile/workspace mental-model copy."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_profiles_panel_surfaces_profiles_vs_workspaces_help_card():
    src = read("static/panels.js")
    i18n = read("static/i18n.js")
    assert "t('profile_help_card_title')" in src
    assert "t('profile_help_card_meta')" in src
    assert "profile_help_card_title: 'Profiles and workspaces'" in i18n
    assert "profile_help_card_title: '配置与工作区'" in i18n
    assert "_renderProfileConceptHelp" in src
    assert "explainer.setAttribute('role', 'button')" in src
    assert "explainer.tabIndex = 0" in src
    assert "explainer.onclick = openProfileConceptHelp" in src
    assert "event.key === 'Enter' || event.key === ' '" in src
    assert "event.preventDefault()" in src


def test_profile_concept_help_distinguishes_how_from_where():
    src = read("static/panels.js")
    i18n = read("static/i18n.js")
    for key in (
        "profile_help_concept_title",
        "profile_help_concept_heading",
        "profile_help_profile_label",
        "profile_help_profile_detail",
        "profile_help_workspace_label",
        "profile_help_workspace_detail",
        "profile_help_combined_label",
        "profile_help_combined_detail",
    ):
        assert f"t('{key}')" in src
        assert key in i18n
    assert "Profiles answer “who is working?”; workspaces answer “where are they working?”" in i18n
    assert "配置回答“谁在工作？”，工作区回答“在哪里工作？”" in i18n


def test_empty_profiles_state_keeps_help_card_visible():
    src = read("static/panels.js")
    assert "panel.innerHTML = ''" in src
    assert "panel.appendChild(explainer)" in src
    assert "emptyMsg.textContent = t('profiles_no_profiles')" in src
    assert "panel.appendChild(emptyMsg)" in src


def test_profile_cards_expose_button_semantics_and_keyboard_activation():
    src = read("static/panels.js")
    assert "card.setAttribute('role', 'button')" in src
    assert "card.tabIndex = 0" in src
    assert "card.setAttribute('aria-label', p.name)" in src
    assert "card.setAttribute('aria-pressed', isSelected ? 'true' : 'false')" in src
    assert "const openCard = () => openProfileDetail(p.name, card)" in src
    assert "card.onclick = openCard" in src
    assert "card.onkeydown = (event) => {" in src
    assert "event.key === 'Enter' || event.key === ' '" in src
    assert "event.target !== event.currentTarget || event.repeat" in src
    assert "event.preventDefault()" in src
    assert "openCard()" in src
    assert "card.click()" not in src


def test_profile_selection_updates_accessible_pressed_state():
    src = read("static/panels.js")
    assert "e.setAttribute('aria-pressed', 'false')" in src
    assert "target.setAttribute('aria-pressed', 'true')" in src


def test_profile_cards_have_visible_keyboard_focus():
    css = read("static/index.html")
    assert ".project-chip:focus-visible,.profile-card:focus-visible{" in css
    assert "outline:2px solid var(--accent-bg-strong);" in css
    assert "outline-offset:2px;" in css
