"""Regression tests for the sidebar "Unassigned" project-filter chip.

Spliced from contributor PRs #1497 (Thanatos-Z) and #1513 (AlexeyDsov), which
both added the ability to filter the sidebar to sessions with no project_id
assigned. Lands here as a focused PR with the best of both:

- #1497's `NO_PROJECT_FILTER` sentinel (single state variable, no parallel
  boolean to keep in sync) and conditional rendering (only show the chip
  when there ARE unassigned sessions).
- #1497's dashed-border visual treatment to distinguish from real project
  chips.
- AlexeyDsov #1513's user need framing — "easy way to view sessions
  not yet organized into projects."

UI choice: label is "Unassigned" rather than #1497's "No project" or
#1513's "None" — clearer than both ("None" is ambiguous, "No project"
sounds like a status). Matches the conventional file-manager / task-tracker
mental model: "things not yet assigned to a category."

These tests pin the feature contract so a future refactor can't silently
break the chip.
"""

from __future__ import annotations

import pathlib

JS = pathlib.Path(__file__).parent.parent / "static" / "sessions.js"
CSS = pathlib.Path(__file__).parent.parent / "static" / "style.css"
INDEX = pathlib.Path(__file__).parent.parent / "static" / "index.html"


def _js() -> str:
    return JS.read_text(encoding="utf-8")


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _index() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_no_project_filter_sentinel_declared():
    """A stable sentinel constant identifies the "no project" filter state.

    Using a sentinel on the existing `_activeProject` variable (rather than
    a parallel `_showNoneProject` boolean) keeps the filter state to one
    place — no two-state-machine ambiguity, no risk of "All" + "Unassigned"
    both appearing active.
    """
    js = _js()
    assert "const NO_PROJECT_FILTER = '__none__';" in js, (
        "static/sessions.js must declare a NO_PROJECT_FILTER sentinel for "
        "the unassigned-sessions filter state"
    )


def test_unassigned_chip_filter_logic():
    """The render function must filter to !s.project_id when the sentinel is active."""
    js = _js()
    assert "_activeProject===NO_PROJECT_FILTER" in js, (
        "renderSessionListFromCache must branch on the NO_PROJECT_FILTER sentinel"
    )
    assert "profileFiltered.filter(s=>!s.project_id)" in js, (
        "The Unassigned filter must select sessions without a project_id"
    )


def test_unassigned_chip_only_shown_when_relevant():
    """The Unassigned chip should only render when there are unassigned sessions.

    In the common case where every session is already organized, hiding the
    chip keeps the project-bar uncluttered. The conditional also keeps the
    project-bar from rendering at all when there are NO projects AND NO
    unassigned sessions (e.g. brand-new install with one organized session
    — though that's vanishingly rare).
    """
    js = _js()
    assert "const hasUnprojected=profileFiltered.some(s=>!s.project_id);" in js, (
        "The render function must compute whether unassigned sessions exist"
    )
    assert "if(_allProjects.length>0||hasUnprojected){" in js, (
        "The project-bar must render when EITHER there are real projects OR "
        "there are unassigned sessions to filter to"
    )
    assert "if(hasUnprojected){" in js, (
        "The Unassigned chip must be conditionally rendered on hasUnprojected"
    )


def test_unassigned_chip_label_handler_and_keyboard_access():
    """The localized chip must support click, Enter, and Space activation."""
    js = _js()
    assert "noneChip.textContent=t('session_project_filter_unassigned');" in js
    assert "noneChip.title=t('session_project_filter_unassigned_title');" in js
    assert "noneChip.setAttribute('role','button');" in js
    assert "noneChip.tabIndex=0;" in js
    assert "noneChip.setAttribute('aria-pressed'" in js
    assert "event.key==='Enter'||event.key===' '" in js
    assert "event.preventDefault();" in js
    assert "const activateUnassignedProjects=()=>_activateProjectFilter(NO_PROJECT_FILTER,'unassigned');" in js, (
        "Clicking the Unassigned chip must set _activeProject to the sentinel"
    )
    # Active-state contract — the chip must reflect when it's the active filter.
    assert "_activeProject===NO_PROJECT_FILTER?' active':''" in js, (
        "The Unassigned chip must apply the .active class when the filter is the "
        "current state"
    )


def test_unassigned_chip_visual_treatment():
    """A dashed border distinguishes the Unassigned chip from real project chips."""
    css = _css()
    assert ".project-chip.no-project{border-style:dashed;}" in css, (
        "The Unassigned chip must have a dashed border to read as a meta-filter "
        "rather than a real project"
    )
    js = _js()
    assert "noneChip.className='project-chip no-project" in js, (
        "The Unassigned chip must have the .no-project class for the dashed-border styling"
    )


def test_empty_state_message_for_unassigned_filter():
    """When the Unassigned filter is active and no sessions match, the empty-state
    message should be specific to that filter rather than generic project text."""
    js = _js()
    assert "_activeProject===NO_PROJECT_FILTER" in js
    assert "t('session_project_filter_empty_unassigned')" in js
    assert "t('session_project_filter_empty_project')" in js


def test_all_chip_clear_clears_unassigned_filter_too():
    """Clicking 'All' must reset the filter unconditionally — including when
    the Unassigned filter is currently active.

    Using a sentinel value on `_activeProject` (rather than a parallel
    `_showNoneProject` boolean) makes this automatic: there's only one
    variable to clear, and 'All' already sets `_activeProject = null`.
    A regression where 'All' didn't reset the unassigned state would
    only happen if someone migrated to a parallel boolean.
    """
    js = _js()
    # Find the "All" chip handler. It must clear _activeProject to null and
    # NOT preserve any unassigned-flag state.
    assert "const activateAllProjects=()=>_activateProjectFilter(null,'all');" in js, (
        "The All chip handler must reset _activeProject to null. If a parallel "
        "_showNoneProject boolean is reintroduced, this test will catch it because "
        "the handler will need additional state to reset."
    )


def test_every_project_filter_chip_supports_keyboard_and_exposes_selection():
    """Real project chips must be as accessible as All and Unassigned."""
    js = _js()
    assert "chip.setAttribute('role','button');" in js
    assert "chip.tabIndex=0;" in js
    assert "chip.setAttribute('aria-pressed',p.project_id===_activeProject?'true':'false');" in js
    assert "chip.dataset.projectFilterKey=p.project_id;" in js
    assert "chip.onkeydown=(event)=>{" in js
    assert "event.key==='Enter'||event.key===' '" in js
    assert "event.target!==event.currentTarget||event.repeat" in js
    assert "activateProject();" in js
    assert "chip.click()" not in js


def test_project_filter_rerender_restores_trigger_focus():
    """Filtering rebuilds the sidebar, so focus must move to the replacement chip."""
    js = _js()
    assert "let _pendingProjectFilterFocus=null;" in js
    assert "function _activateProjectFilter(projectId,focusKey){" in js
    assert "_pendingProjectFilterFocus=focusKey;" in js
    assert "function _restoreProjectFilterFocus(bar){" in js
    assert "candidate.dataset.projectFilterKey===focusKey" in js
    assert "target.focus({preventScroll:true});" in js
    assert "_restoreProjectFilterFocus(bar);" in js


def test_project_and_profile_cards_have_visible_keyboard_focus():
    css = _index()
    assert ".project-chip:focus-visible,.profile-card:focus-visible{" in css
    assert "outline:2px solid var(--accent-bg-strong);" in css
    assert "outline-offset:2px;" in css


def test_project_filter_copy_is_localized_in_english_and_chinese():
    i18n = (JS.parent / "i18n.js").read_text(encoding="utf-8")
    assert "session_project_filter_unassigned: 'Unassigned'" in i18n
    assert "session_project_filter_unassigned: '未分配'" in i18n
    assert "session_project_filter_unassigned_title: '显示尚未分配到项目的会话'" in i18n
