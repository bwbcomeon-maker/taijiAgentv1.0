"""Welcome banner, ASCII art, skills summary, and update check for the CLI.

Pure display functions with no HermesCLI state dependency.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, List, Optional

from rich.console import Console
from rich.cells import set_cell_size
from rich.table import Table

from prompt_toolkit import print_formatted_text as _pt_print
from prompt_toolkit.formatted_text import ANSI as _PT_ANSI

logger = logging.getLogger(__name__)


# =========================================================================
# ANSI building blocks for conversation display
# =========================================================================

_GOLD = "\033[1;38;2;255;215;0m"  # True-color #FFD700 bold
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RST = "\033[0m"


def cprint(text: str):
    """Print ANSI-colored text through prompt_toolkit's renderer."""
    _pt_print(_PT_ANSI(text))


def _display_skill_name(skill_name: str) -> str:
    """Normalize legacy/internal skill identifiers for product-facing display."""
    if not skill_name:
        return skill_name
    return PRODUCT_SKILL_ALIASES.get(skill_name, skill_name)


# =========================================================================
# Skin-aware color helpers
# =========================================================================

def _skin_color(key: str, fallback: str) -> str:
    """Get a color from the active skin, or return fallback."""
    try:
        from hermes_cli.skin_engine import get_active_skin
        return get_active_skin().get_color(key, fallback)
    except Exception:
        return fallback


def _skin_branding(key: str, fallback: str) -> str:
    """Get a branding string from the active skin, or return fallback."""
    try:
        from hermes_cli.skin_engine import get_active_skin
        return get_active_skin().get_branding(key, fallback)
    except Exception:
        return fallback


# =========================================================================
# ASCII Art & Branding
# =========================================================================

from hermes_cli import __version__ as VERSION, __release_date__ as RELEASE_DATE

PRODUCT_NAME = "Taiji Agent"
PRODUCT_SUBTITLE = "太极智能体"
PRODUCT_RUNTIME_LABEL = "Intelligent Agent Runtime"
PRODUCT_SKILL_ALIASES = {
    "hermes-agent": "taiji-agent",
}

TAIJI_AGENT_LOGO = """[bold #FFD700]Taiji Agent[/]
[#FFBF00]太极智能体[/]
[dim #B8860B]Intelligent Agent Runtime[/]"""

TAIJI_DOT_MATRIX_LINES = [
    "[#CD7F32]···············[/][dim #8B8682]···············[/]",
    "[#CD7F32]············[/][#FFF8DC]·····[/][dim #8B8682]·············[/]",
    "[#CD7F32]··········[/][#FFF8DC]········[/][dim #8B8682]···········[/]",
    "[#CD7F32]········[/][#FFF8DC]····[/]  [#FFF8DC]··[/][dim #8B8682]··········[/]",
    "[#CD7F32]······[/]      [#FFF8DC]······[/][dim #8B8682]··········[/]",
    "[#CD7F32]········[/]    [#FFF8DC]····[/][dim #8B8682]············[/]",
    "[#CD7F32]··········[/]  [#FFF8DC]··[/][dim #8B8682]··············[/]",
    "[#CD7F32]···············[/][dim #8B8682]···············[/]",
]
TAIJI_DOT_MATRIX = "\n".join(TAIJI_DOT_MATRIX_LINES)

# Backwards-compatible internal constant names used by older skins/imports.
HERMES_AGENT_LOGO = TAIJI_AGENT_LOGO
HERMES_CADUCEUS = TAIJI_DOT_MATRIX



# =========================================================================
# Skills scanning
# =========================================================================

def get_available_skills() -> Dict[str, List[str]]:
    """Return skills grouped by category, filtered by platform and disabled state.

    Delegates to ``_find_all_skills()`` from ``tools/skills_tool`` which already
    handles platform gating (``platforms:`` frontmatter) and respects the
    user's ``skills.disabled`` config list.
    """
    try:
        from tools.skills_tool import _find_all_skills
        all_skills = _find_all_skills()  # already filtered
    except Exception:
        return {}

    skills_by_category: Dict[str, List[str]] = {}
    for skill in all_skills:
        category = skill.get("category") or "general"
        skills_by_category.setdefault(category, []).append(_display_skill_name(skill["name"]))
    return skills_by_category


# =========================================================================
# Update check
# =========================================================================

# Cache update check results for 6 hours to avoid repeated git fetches
_UPDATE_CHECK_CACHE_SECONDS = 6 * 3600

# Sentinel returned when we know an update exists but can't count commits
# (e.g. nix-built hermes — no local git history to count against).
UPDATE_AVAILABLE_NO_COUNT = -1

_UPSTREAM_REPO_URL = "https://github.com/NousResearch/hermes-agent.git"


def _check_via_rev(local_rev: str) -> Optional[int]:
    """Compare an embedded git revision to upstream main via ls-remote.

    Returns 0 if up-to-date, ``UPDATE_AVAILABLE_NO_COUNT`` if behind,
    or ``None`` on failure.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", _UPSTREAM_REPO_URL, "refs/heads/main"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    upstream_rev = result.stdout.split()[0]
    if not upstream_rev:
        return None
    return 0 if upstream_rev == local_rev else UPDATE_AVAILABLE_NO_COUNT


def _check_via_local_git(repo_dir: Path) -> Optional[int]:
    """Count commits behind origin/main in a local checkout."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--quiet"],
            capture_output=True, timeout=10,
            cwd=str(repo_dir),
        )
    except Exception:
        pass  # Offline or timeout — use stale refs, that's fine

    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_dir),
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return None


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse '0.13.0' into (0, 13, 0) for comparison. Non-numeric segments become 0."""
    parts = []
    for segment in v.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _fetch_pypi_latest(package: str = "hermes-agent") -> Optional[str]:
    """Fetch the latest version of a package from PyPI. Returns None on failure."""
    try:
        import urllib.request
        url = f"https://pypi.org/pypi/{package}/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("info", {}).get("version")
    except Exception:
        return None


def check_via_pypi() -> Optional[int]:
    """Compare installed version against PyPI latest.

    Returns 0 if up-to-date, 1 if behind, None on failure.
    """
    latest = _fetch_pypi_latest()
    if latest is None:
        return None
    if latest == VERSION:
        return 0
    try:
        if _version_tuple(latest) > _version_tuple(VERSION):
            return 1
        return 0
    except Exception:
        return 1 if latest != VERSION else 0


def check_for_updates() -> Optional[int]:
    """Check whether a Hermes update is available.

    Two paths: if ``HERMES_REVISION`` is set (nix builds embed it), compare
    it to upstream main via ``git ls-remote``. Otherwise look for a local
    git checkout and count commits behind ``origin/main``.

    Returns the number of commits behind, ``UPDATE_AVAILABLE_NO_COUNT`` (-1)
    if behind but the count is unknown, ``0`` if up-to-date, or ``None`` if
    the check failed or doesn't apply. Cached for 6 hours.
    """
    hermes_home = get_hermes_home()
    cache_file = hermes_home / ".update_check"
    embedded_rev = os.environ.get("HERMES_REVISION") or None

    # Read cache — invalidate if the embedded rev has changed since last check
    now = time.time()
    try:
        if cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if (
                now - cached.get("ts", 0) < _UPDATE_CHECK_CACHE_SECONDS
                and cached.get("rev") == embedded_rev
            ):
                return cached.get("behind")
    except Exception:
        pass

    if embedded_rev:
        behind = _check_via_rev(embedded_rev)
    else:
        # Prefer the running code's location over the profile-scoped path.
        # $HERMES_HOME/hermes-agent/ may be a stale copy from --clone-all;
        # Path(__file__) always resolves to the actual installed checkout.
        repo_dir = Path(__file__).parent.parent.resolve()
        if not (repo_dir / ".git").exists():
            repo_dir = hermes_home / "hermes-agent"
        if not (repo_dir / ".git").exists():
            behind = check_via_pypi()
        else:
            behind = _check_via_local_git(repo_dir)

    try:
        cache_file.write_text(json.dumps({"ts": now, "behind": behind, "rev": embedded_rev}))
    except Exception:
        pass

    return behind


def _resolve_repo_dir() -> Optional[Path]:
    """Return the active Hermes git checkout, or None if this isn't a git install.

    Prefers the running code's location over the profile-scoped path
    because ``$HERMES_HOME/hermes-agent/`` may be a stale copy carried
    over by ``--clone-all``.
    """
    repo_dir = Path(__file__).parent.parent.resolve()
    if not (repo_dir / ".git").exists():
        hermes_home = get_hermes_home()
        repo_dir = hermes_home / "hermes-agent"
    return repo_dir if (repo_dir / ".git").exists() else None


def _git_short_hash(repo_dir: Path, rev: str) -> Optional[str]:
    """Resolve a git revision to an 8-character short hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", rev],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_dir),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def get_git_banner_state(repo_dir: Optional[Path] = None) -> Optional[dict]:
    """Return upstream/local git hashes for the startup banner.

    For source installs and dev images this runs ``git rev-parse`` against
    the active checkout.  When no checkout is available — the canonical case
    is the published Docker image, which excludes ``.git`` from the build
    context — we fall back to the baked-in build SHA (see
    ``hermes_cli/build_info.py``) and return it as a frozen
    ``upstream == local`` state with ``ahead=0``.  A built image is by
    definition pinned to one commit, so "ahead" is always zero and the
    banner correctly shows ``· upstream <sha>`` with no carried-commits
    annotation.
    """
    repo_dir = repo_dir or _resolve_repo_dir()
    if repo_dir is None:
        # No git checkout — try the baked build SHA (Docker image path).
        try:
            from hermes_cli.build_info import get_build_sha
            baked = get_build_sha(short=8)
            if baked:
                return {"upstream": baked, "local": baked, "ahead": 0}
        except Exception:
            pass
        return None

    upstream = _git_short_hash(repo_dir, "origin/main")
    local = _git_short_hash(repo_dir, "HEAD")
    if not upstream or not local:
        # Live-git lookup failed (e.g. shallow clone without origin/main).
        # Fall back to the baked build SHA if available.
        try:
            from hermes_cli.build_info import get_build_sha
            baked = get_build_sha(short=8)
            if baked:
                return {"upstream": baked, "local": baked, "ahead": 0}
        except Exception:
            pass
        return None

    ahead = 0
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_dir),
        )
        if result.returncode == 0:
            ahead = int((result.stdout or "0").strip() or "0")
    except Exception:
        ahead = 0

    return {"upstream": upstream, "local": local, "ahead": max(ahead, 0)}


_RELEASE_URL_BASE = "https://github.com/NousResearch/hermes-agent/releases/tag"
_latest_release_cache: Optional[tuple] = None  # (tag, url) once resolved


def get_latest_release_tag(repo_dir: Optional[Path] = None) -> Optional[tuple]:
    """Return ``(tag, release_url)`` for the latest git tag, or None.

    Local-only — runs ``git describe --tags --abbrev=0`` against the
    Hermes checkout. Cached per-process. Release URL always points at the
    canonical NousResearch/hermes-agent repo (forks don't get a link).
    """
    global _latest_release_cache
    if _latest_release_cache is not None:
        return _latest_release_cache or None

    repo_dir = repo_dir or _resolve_repo_dir()
    if repo_dir is None:
        _latest_release_cache = ()  # falsy sentinel — skip future lookups
        return None

    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=str(repo_dir),
        )
    except Exception:
        _latest_release_cache = ()
        return None

    if result.returncode != 0:
        _latest_release_cache = ()
        return None

    tag = (result.stdout or "").strip()
    if not tag:
        _latest_release_cache = ()
        return None

    url = f"{_RELEASE_URL_BASE}/{tag}"
    _latest_release_cache = (tag, url)
    return _latest_release_cache


def format_banner_version_label() -> str:
    """Return the version label shown in the startup banner title."""
    base = f"{PRODUCT_NAME} v{VERSION} ({RELEASE_DATE})"
    state = get_git_banner_state()
    if not state:
        return base

    upstream = state["upstream"]
    local = state["local"]
    ahead = int(state.get("ahead") or 0)

    if ahead <= 0 or upstream == local:
        return f"{base} · upstream {upstream}"

    carried_word = "commit" if ahead == 1 else "commits"
    return f"{base} · upstream {upstream} · local {local} (+{ahead} carried {carried_word})"


# =========================================================================
# Non-blocking update check
# =========================================================================

_update_result: Optional[int] = None
_update_check_done = threading.Event()


def prefetch_update_check():
    """Kick off update check in a background daemon thread."""
    def _run():
        global _update_result
        _update_result = check_for_updates()
        _update_check_done.set()
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def get_update_result(timeout: float = 0.5) -> Optional[int]:
    """Get result of prefetched check. Returns None if not ready."""
    _update_check_done.wait(timeout=timeout)
    return _update_result


# =========================================================================
# Welcome banner
# =========================================================================

def _format_context_length(tokens: int) -> str:
    """Format a token count for display (e.g. 128000 → '128K', 1048576 → '1M')."""
    if tokens >= 1_000_000:
        val = tokens / 1_000_000
        rounded = round(val)
        if abs(val - rounded) < 0.05:
            return f"{rounded}M"
        return f"{val:.1f}M"
    elif tokens >= 1_000:
        val = tokens / 1_000
        rounded = round(val)
        if abs(val - rounded) < 0.05:
            return f"{rounded}K"
        return f"{val:.1f}K"
    return str(tokens)


def _display_toolset_name(toolset_name: str) -> str:
    """Normalize internal/legacy toolset identifiers for banner display."""
    if not toolset_name:
        return "unknown"
    return (
        toolset_name[:-6]
        if toolset_name.endswith("_tools")
        else toolset_name
    )


def _cell(text: str, width: int) -> str:
    """Fit plain text to a terminal cell width without breaking CJK alignment."""
    return set_cell_size(str(text), width)


def _styled_cell(text: str, width: int, style: str, *, bold: bool = False) -> str:
    tag = f"bold {style}" if bold else style
    return f"[{tag}]{_cell(text, width)}[/]"


def _section_heading(icon: str, label: str, width: int, accent: str, dim: str) -> str:
    title = f"{icon} {label}"
    rule_width = max(0, width - len(title) - 3)
    return f"[bold {accent}]{title}[/] [dim {dim}]{'─' * rule_width}[/]"


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _column_rows(
    names: List[str],
    *,
    rows: int,
    widths: tuple[int, int, int],
    text_style: str,
    dim_style: str,
    name_style_for=None,
) -> List[str]:
    columns = [names[index * rows:(index + 1) * rows] for index in range(3)]
    rendered = []
    for row_index in range(rows):
        cells = []
        for col_index, width in enumerate(widths):
            value = columns[col_index][row_index] if row_index < len(columns[col_index]) else ""
            style = name_style_for(value) if name_style_for and value else text_style
            cells.append(_styled_cell(value, width, style))
        rendered.append(
            f"  {cells[0]} [dim {dim_style}]┊[/] {cells[1]} [dim {dim_style}]┊[/] {cells[2]}"
        )
    return rendered


def _flatten_skills_for_banner(skills_by_category: Dict[str, List[str]]) -> List[str]:
    skills: List[str] = []
    for skill_names in skills_by_category.values():
        skills.extend(_display_skill_name(name) for name in skill_names)
    return _dedupe_preserve_order(skills)


def build_welcome_banner(console: Console, model: str, cwd: str,
                         tools: List[dict] = None,
                         enabled_toolsets: List[str] = None,
                         session_id: str = None,
                         get_toolset_for_tool=None,
                         context_length: int = None):
    """Build and print a welcome banner with caduceus on left and info on right.

    Args:
        console: Rich Console instance.
        model: Current model name.
        cwd: Current working directory.
        tools: List of tool definitions.
        enabled_toolsets: List of enabled toolset names.
        session_id: Session identifier.
        get_toolset_for_tool: Callable to map tool name -> toolset name.
        context_length: Model's context window size in tokens.
    """
    from model_tools import check_tool_availability, TOOLSET_REQUIREMENTS
    if get_toolset_for_tool is None:
        from model_tools import get_toolset_for_tool

    tools = tools or []
    enabled_toolsets = enabled_toolsets or []

    _, unavailable_toolsets = check_tool_availability(quiet=True)
    disabled_tools = set()
    # Tools whose toolset has a check_fn are lazy-initialized (e.g. honcho,
    # homeassistant) — they show as unavailable at banner time because the
    # check hasn't run yet, but they aren't misconfigured.
    lazy_tools = set()
    for item in unavailable_toolsets:
        toolset_name = item.get("name", "")
        ts_req = TOOLSET_REQUIREMENTS.get(toolset_name, {})
        tools_in_ts = item.get("tools", [])
        if ts_req.get("check_fn"):
            lazy_tools.update(tools_in_ts)
        else:
            disabled_tools.update(tools_in_ts)

    # Resolve skin colors once for the entire banner
    accent = _skin_color("banner_accent", "#FFBF00")
    dim = _skin_color("banner_dim", "#B8860B")
    text = _skin_color("banner_text", "#FFF8DC")
    session_color = _skin_color("session_border", "#8B8682")

    # Use skin's custom hero art if provided
    try:
        from hermes_cli.skin_engine import get_active_skin
        _bskin = get_active_skin()
        _hero = _bskin.banner_hero if hasattr(_bskin, 'banner_hero') and _bskin.banner_hero else HERMES_CADUCEUS
    except Exception:
        _bskin = None
        _hero = HERMES_CADUCEUS
    agent_name = (
        _bskin.get_branding("agent_name", PRODUCT_NAME)
        if _bskin else PRODUCT_NAME
    )
    subtitle = (
        _bskin.get_branding("subtitle", PRODUCT_SUBTITLE)
        if _bskin else PRODUCT_SUBTITLE
    )
    runtime_label = (
        _bskin.get_branding("runtime_label", PRODUCT_RUNTIME_LABEL)
        if _bskin else PRODUCT_RUNTIME_LABEL
    )
    hero_lines = _hero.splitlines() if _hero else []

    release_info = get_latest_release_tag()
    version_plain = f"v{VERSION} {runtime_label}"
    if release_info:
        _tag, _url = release_info
        version_markup = f"[dim {text}][link={_url}]{version_plain}[/link][/]"
    else:
        version_markup = f"[dim {text}]{version_plain}[/]"

    ctx_str = f" · {_format_context_length(context_length)} context" if context_length else ""
    session_value = session_id or "new"
    platform_label = f"{platform.system().lower()} {platform.machine()}".strip()

    left_lines = [
        f"[bold {accent}]{agent_name}[/]",
        f"[{accent}]{subtitle}[/]",
        "",
        version_markup,
        *hero_lines,
        f"[dim {dim}]{'─' * 33}[/]",
    ]
    if os.getenv("HERMES_YOLO_MODE"):
        left_lines.append(f"[bold red]⚠ YOLO mode[/] [dim {dim}]— approval prompts bypassed[/]")
    left_lines.append(f"[{accent}]›[/] [{text}]Session[/] [dim {session_color}]{session_value}[/]")
    left_lines.append(f"[{accent}]›[/] [{text}]Mode[/] [dim {session_color}]interactive[/]")
    left_lines.append(
        f"[{accent}]›[/] [{text}]Runtime[/] [dim {session_color}]python "
        f"{sys.version_info.major}.{sys.version_info.minor}{ctx_str}[/]"
    )
    left_lines.append(f"[{accent}]›[/] [{text}]Platform[/] [dim {session_color}]{platform_label}[/]")

    toolsets_dict: Dict[str, list] = {}
    tool_names_for_columns: List[str] = []

    for tool in tools:
        tool_name = tool["function"]["name"]
        toolset = _display_toolset_name(get_toolset_for_tool(tool_name) or "other")
        toolsets_dict.setdefault(toolset, []).append(tool_name)
        tool_names_for_columns.append(tool_name)

    for item in unavailable_toolsets:
        toolset_id = item.get("id", item.get("name", "unknown"))
        display_name = _display_toolset_name(toolset_id)
        if display_name not in toolsets_dict:
            toolsets_dict[display_name] = []
        for tool_name in item.get("tools", []):
            if tool_name not in toolsets_dict[display_name]:
                toolsets_dict[display_name].append(tool_name)
            tool_names_for_columns.append(tool_name)

    sorted_toolsets = sorted(toolsets_dict.keys())
    remaining_toolsets = max(0, len(sorted_toolsets) - 8)

    # MCP Servers section (only if configured)
    try:
        from tools.mcp_tool import get_mcp_status
        mcp_status = get_mcp_status()
    except Exception:
        mcp_status = []

    skills_by_category = get_available_skills()
    skills_for_columns = _flatten_skills_for_banner(skills_by_category)

    def _tool_style(name: str) -> str:
        if name in disabled_tools:
            return "red"
        if name in lazy_tools:
            return "yellow"
        return text

    right_width = 86
    right_lines = [
        _section_heading("◇", "AVAILABLE TOOLS", right_width, accent, dim),
        "",
    ]
    visible_tools = _dedupe_preserve_order(tool_names_for_columns)[:11]
    right_lines.extend(
        _column_rows(
            visible_tools,
            rows=5,
            widths=(22, 22, 26),
            text_style=text,
            dim_style=dim,
            name_style_for=_tool_style,
        )
    )
    if remaining_toolsets:
        right_lines.append(
            f"  {_styled_cell('', 22, text)} [dim {dim}]┊[/] "
            f"{_styled_cell('', 22, text)} [dim {dim}]┊[/] "
            f"[{accent}]... and {remaining_toolsets} more toolsets[/]"
        )

    right_lines.extend([
        "",
        f"[dim {dim}]{'─' * right_width}[/]",
        _section_heading("▱", "AVAILABLE SKILLS", right_width, accent, dim),
        "",
    ])
    if skills_for_columns:
        right_lines.extend(
            _column_rows(
                skills_for_columns[:15],
                rows=5,
                widths=(24, 24, 30),
                text_style=text,
                dim_style=dim,
            )
        )
    else:
        right_lines.append(f"  [dim {dim}]No skills installed[/]")

    if remaining_toolsets:
        right_lines.append("")
        right_lines.append(
            f"{_styled_cell('', 38, text)}[{accent}]... and {remaining_toolsets} more toolsets[/]"
        )

    # Indicate when the codex_app_server runtime is active so users
    # understand why tool counts may not match what's actually reachable
    # (codex builds its own tool list inside the spawned subprocess).
    try:
        from hermes_cli.codex_runtime_switch import get_current_runtime
        from hermes_cli.config import load_config as _load_cfg
        if get_current_runtime(_load_cfg()) == "codex_app_server":
            right_lines.append(
                f"[bold {accent}]Runtime:[/] [{text}]codex app-server[/] "
                f"[dim {dim}](terminal/file ops/MCP run inside codex)[/]"
            )
    except Exception:
        pass
    # Show active profile name when not 'default'
    try:
        from hermes_cli.profiles import get_active_profile_name
        _profile_name = get_active_profile_name()
        if _profile_name and _profile_name != "default":
            right_lines.append(f"[bold {accent}]Profile:[/] [{text}]{_profile_name}[/]")
    except Exception:
        pass  # Never break the banner over a profiles.py bug

    max_lines = max(len(left_lines), len(right_lines))
    left_lines.extend([""] * (max_lines - len(left_lines)))
    right_lines.extend([""] * (max_lines - len(right_lines)))
    separator = "\n".join(f"[dim {dim}]│[/]" for _ in range(max_lines))

    layout_table = Table.grid(padding=(0, 2), expand=False)
    layout_table.add_column("left", width=38, no_wrap=True)
    layout_table.add_column("separator", width=1, no_wrap=True)
    layout_table.add_column("right", width=right_width, no_wrap=True)
    layout_table.add_row("\n".join(left_lines), separator, "\n".join(right_lines))

    console.print()
    console.print(layout_table)
