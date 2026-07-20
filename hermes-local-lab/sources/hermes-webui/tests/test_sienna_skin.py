"""Sienna skin: warm clay/sand earth palette, opt-in via Settings → Skin."""

from pathlib import Path

REPO = Path(__file__).parent.parent
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")
BOOT_JS = (REPO / "static" / "boot.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")


def _early_appearance_init_block():
    anchor = "themes={light:1,dark:1,system:1}"
    anchor_idx = INDEX_HTML.find(anchor)
    assert anchor_idx != -1, "Early appearance bootstrap is missing"
    start_idx = INDEX_HTML.rfind("<script", 0, anchor_idx)
    end_idx = INDEX_HTML.find("</script>", anchor_idx)
    assert start_idx != -1 and end_idx != -1
    return INDEX_HTML[start_idx:end_idx]


def test_sienna_skin_present_in_skins_list():
    """The Sienna skin must be exposed in the picker grid via _SKINS."""
    assert "{name:'Sienna'" in BOOT_JS, "Sienna skin missing from _SKINS list"
    assert "'#D97757','#C06A49','#9A523A'" in BOOT_JS, (
        "Sienna preview swatches missing"
    )


def test_sienna_skin_in_early_init_allowlist():
    """The early-init skin allowlist must accept 'sienna'."""
    assert "sienna:1" in INDEX_HTML, (
        "Sienna missing from early-init skin allowlist; saved skin would be "
        "rejected and reset to default on boot"
    )


def test_sienna_skin_palette_has_full_light_and_dark():
    """Sienna defines both light and dark scoped palettes."""
    assert ':root[data-skin="sienna"]{' in CSS, (
        "Sienna light-mode palette block missing"
    )
    assert ':root.dark[data-skin="sienna"]{' in CSS, (
        "Sienna dark-mode palette block missing"
    )
    # Spot-check that the palette is a full rewrite (not just --accent)
    for token in ("--bg:#FAF9F5", "--sidebar:#F0EEE6", "--accent:#D97757"):
        assert token in CSS, f"Sienna light palette token missing: {token}"
    for token in ("--bg:#1F1E1C", "--sidebar:#262522", "--accent:#E0896D"):
        assert token in CSS, f"Sienna dark palette token missing: {token}"


def test_sienna_skin_does_not_force_migration():
    """Sienna must not be silently migrated onto existing users.

    The early-init script in index.html must NOT contain logic that flips an
    existing 'default' skin to 'sienna' on first load. New users keep the Taiji
    glass default; users opt in via Settings → Skin.
    """
    # The skin allowlist line should NOT contain a sienna-migration flag.
    init_block = _early_appearance_init_block()
    forbidden = ["sienna-migrated", "skin-sienna-migrated", "skin='sienna'", 'skin="sienna"']
    for marker in forbidden:
        assert marker not in init_block, (
            f"Sienna skin must be opt-in, not force-migrated. Found '{marker}' "
            f"in early-init script."
        )


def test_default_appearance_remains_taiji_light_glass():
    """Adding a new skin must not change the product's default appearance."""
    init_block = _early_appearance_init_block()
    assert "sg('theme','light')" in init_block
    assert "?'taiji-light-glass':rawSkin" in init_block


def test_sienna_new_chat_button_specificity_guards_against_clay_on_clay():
    """The new-chat button needs higher specificity than the base
    :root:not(.dark) .new-chat-btn rule, otherwise the inherited
    color:var(--accent-text) collides with the solid-accent background and
    produces invisible clay-on-clay text in light mode."""
    assert ':root[data-skin="sienna"]:not(.dark) .new-chat-btn' in CSS, (
        "Sienna light-mode .new-chat-btn override missing — clay-on-clay risk"
    )
