"""Tests for banner toolset name normalization and skin color usage."""

from unittest.mock import patch

from rich.console import Console

import hermes_cli.banner as banner
import model_tools
import tools.mcp_tool


def test_display_toolset_name_strips_legacy_suffix():
    assert banner._display_toolset_name("homeassistant_tools") == "homeassistant"
    assert banner._display_toolset_name("honcho_tools") == "honcho"
    assert banner._display_toolset_name("web_tools") == "web"


def test_display_toolset_name_preserves_clean_names():
    assert banner._display_toolset_name("browser") == "browser"
    assert banner._display_toolset_name("file") == "file"
    assert banner._display_toolset_name("terminal") == "terminal"


def test_display_toolset_name_handles_empty():
    assert banner._display_toolset_name("") == "unknown"
    assert banner._display_toolset_name(None) == "unknown"


def test_build_welcome_banner_uses_flat_tool_names_without_legacy_toolset_suffixes():
    """The product layout shows tool names, not legacy grouped toolset labels."""
    with (
        patch.object(
            model_tools,
            "check_tool_availability",
            return_value=(
                ["web"],
                [
                    {"name": "homeassistant", "tools": ["ha_call_service"]},
                    {"name": "honcho", "tools": ["honcho_conclude"]},
                ],
            ),
        ),
        patch.object(banner, "get_available_skills", return_value={}),
        patch.object(banner, "get_update_result", return_value=None),
        patch.object(tools.mcp_tool, "get_mcp_status", return_value=[]),
    ):
        console = Console(
            record=True, force_terminal=False, color_system=None, width=160
        )
        banner.build_welcome_banner(
            console=console,
            model="anthropic/test-model",
            cwd="/tmp/project",
            tools=[
                {"function": {"name": "web_search"}},
                {"function": {"name": "read_file"}},
            ],
            get_toolset_for_tool=lambda name: {
                "web_search": "web_tools",
                "read_file": "file",
            }.get(name),
        )

    output = console.export_text()
    assert "web_search" in output
    assert "read_file" in output
    assert "ha_call_service" in output
    assert "honcho_conclude" in output
    assert "homeassistant_tools:" not in output
    assert "honcho_tools:" not in output
    assert "web_tools:" not in output
    assert "homeassistant:" not in output
    assert "honcho:" not in output
    assert "web:" not in output


def test_build_welcome_banner_version_is_hyperlinked_to_release():
    """The left-side version label is wrapped in an OSC-8 hyperlink to the GitHub release."""
    import io
    from unittest.mock import patch as _patch
    import hermes_cli.banner as _banner
    import model_tools as _mt
    import tools.mcp_tool as _mcp

    _banner._latest_release_cache = None
    tag_url = ("v2026.4.23", "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.4.23")

    buf = io.StringIO()
    with (
        _patch.object(_mt, "check_tool_availability", return_value=(["web"], [])),
        _patch.object(_banner, "get_available_skills", return_value={}),
        _patch.object(_banner, "get_update_result", return_value=None),
        _patch.object(_mcp, "get_mcp_status", return_value=[]),
        _patch.object(_banner, "get_latest_release_tag", return_value=tag_url),
    ):
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=160)
        _banner.build_welcome_banner(
            console=console, model="x", cwd="/tmp",
            session_id="abc123",
            tools=[{"function": {"name": "read_file"}}],
            get_toolset_for_tool=lambda n: "file",
        )

    raw = buf.getvalue()
    # The product version must still be present in the startup banner.
    assert f"v{_banner.VERSION}" in raw, "Version label missing from banner"
    # OSC-8 hyperlink escape sequence present with the release URL
    assert "\x1b]8;" in raw, "OSC-8 hyperlink not emitted"
    assert "releases/tag/v2026.4.23" in raw, "Release URL missing from banner output"


def test_build_welcome_banner_version_falls_back_when_no_tag():
    """Without a resolvable tag, the version label renders as plain text (no hyperlink escape)."""
    import io
    from unittest.mock import patch as _patch
    import hermes_cli.banner as _banner
    import model_tools as _mt
    import tools.mcp_tool as _mcp

    _banner._latest_release_cache = None
    buf = io.StringIO()
    with (
        _patch.object(_mt, "check_tool_availability", return_value=(["web"], [])),
        _patch.object(_banner, "get_available_skills", return_value={}),
        _patch.object(_banner, "get_update_result", return_value=None),
        _patch.object(_mcp, "get_mcp_status", return_value=[]),
        _patch.object(_banner, "get_latest_release_tag", return_value=None),
    ):
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=160)
        _banner.build_welcome_banner(
            console=console, model="x", cwd="/tmp",
            session_id="abc123",
            tools=[{"function": {"name": "read_file"}}],
            get_toolset_for_tool=lambda n: "file",
        )

    raw = buf.getvalue()
    assert f"v{_banner.VERSION}" in raw, "Version label missing from banner"
    assert "\x1b]8;" not in raw, "OSC-8 hyperlink should not be emitted without a tag"


def test_build_welcome_banner_uses_taiji_product_branding_and_skill_alias():
    """Startup banner should expose Taiji Agent branding without legacy product marks."""
    import hermes_cli.banner as _banner
    import model_tools as _mt
    import tools.mcp_tool as _mcp

    with (
        patch.object(_mt, "check_tool_availability", return_value=(["browser"], [])),
        patch.object(_banner, "get_available_skills", return_value={"agents": ["hermes-agent"]}),
        patch.object(_banner, "get_update_result", return_value=None),
        patch.object(_mcp, "get_mcp_status", return_value=[]),
        patch.object(_banner, "get_latest_release_tag", return_value=None),
    ):
        console = Console(record=True, force_terminal=False, color_system=None, width=160)
        _banner.build_welcome_banner(
            console=console,
            model="deepseek-v4-pro",
            cwd="/tmp/taiji-agent",
            session_id="20260617_111613",
            tools=[{"function": {"name": "browser_click"}}],
            get_toolset_for_tool=lambda n: "browser",
        )

    output = console.export_text()
    assert "Taiji Agent" in output
    assert "太极智能体" in output
    assert "taiji-agent" in output
    assert "hermes-agent" not in output
    assert "Hermes Agent" not in output
    assert "Nous Research" not in output


def test_build_welcome_banner_matches_bilingual_product_layout():
    """Startup banner should use the selected wide product mockup layout."""
    import hermes_cli.banner as _banner
    import model_tools as _mt
    import tools.mcp_tool as _mcp

    tool_names = [
        "browser_back",
        "browser_click",
        "browser_cdp",
        "browser_dialog",
        "browser_wait",
        "clarify",
        "code_execution",
        "computer_use",
        "cronjob",
        "delegate_task",
        "discord",
    ]
    skills = {
        "apple": ["apple-notes", "findmy", "autonomous-ai-agents", "claude-code", "codex"],
        "agents": ["hermes-agent", "kanban-codex", "architecture-diagram", "ascii-art", "jupyter-live-kernel"],
        "devops": ["kanban-orchestrator", "minecraft-modpack-server", "gitbase-inspection", "native-mcp", "audiocraft-audio-generation"],
    }

    with (
        patch.object(_mt, "check_tool_availability", return_value=(["browser"], [])),
        patch.object(_banner, "get_available_skills", return_value=skills),
        patch.object(_banner, "get_update_result", return_value=None),
        patch.object(_mcp, "get_mcp_status", return_value=[]),
        patch.object(_banner, "get_latest_release_tag", return_value=None),
        patch("shutil.get_terminal_size", return_value=type("Size", (), {"columns": 145})()),
    ):
        console = Console(record=True, force_terminal=False, color_system=None, width=145)
        _banner.build_welcome_banner(
            console=console,
            model="deepseek-v4-pro",
            cwd="/tmp/taiji-agent",
            session_id="20260617_111613",
            tools=[{"function": {"name": name}} for name in tool_names],
            get_toolset_for_tool=lambda n: n,
        )

    output = console.export_text()
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]

    assert not any(line.startswith(("╭", "╰", "┌", "└")) for line in lines)
    assert any("Taiji Agent" in line and "AVAILABLE TOOLS" in line for line in lines)
    assert any("browser_back" in line and "clarify" in line and "discord" in line for line in lines)
    assert any("apple-notes" in line and "taiji-agent" in line and "kanban-orchestrator" in line for line in lines)
    assert "browser:" not in output
    assert "agents:" not in output
    assert "hermes-agent" not in output
    assert len(lines) <= 24
