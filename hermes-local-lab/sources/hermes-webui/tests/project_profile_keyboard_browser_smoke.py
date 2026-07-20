#!/usr/bin/env python3
"""Real-browser keyboard gate for project filters and profile cards.

The smoke boots this checkout's WebUI with isolated state, supplies stable API
fixtures at the browser boundary, and activates the visible controls using only
Enter/Space.  It catches focus-loss and keyboard regressions that source-text
assertions cannot observe after the project-filter DOM is rebuilt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent.parent


def _select_port() -> int:
    configured = os.getenv("PROJECT_PROFILE_KEYBOARD_SMOKE_PORT", "").strip()
    requested = int(configured) if configured else 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", requested))
        except OSError as error:
            raise RuntimeError(
                f"smoke port {requested} is already occupied; refusing to disturb its owner"
            ) from error
        return int(probe.getsockname()[1])


PORT = _select_port()
BASE = f"http://127.0.0.1:{PORT}"

SESSIONS_RESPONSE = {
    "sessions": [
        {
            "session_id": "session-project-alpha",
            "title": "Alpha project conversation",
            "message_count": 2,
            "project_id": "project-alpha",
            "profile": "default",
            "updated_at": "2026-07-20T08:00:00Z",
        },
        {
            "session_id": "session-unassigned",
            "title": "Unassigned conversation",
            "message_count": 3,
            "project_id": None,
            "profile": "default",
            "updated_at": "2026-07-20T07:00:00Z",
        },
        {
            "session_id": "session-project-beta",
            "title": "Beta project conversation",
            "message_count": 4,
            "project_id": "project-beta",
            "profile": "default",
            "updated_at": "2026-07-20T06:00:00Z",
        },
    ],
    "other_profile_count": 0,
    "server_time": 1784534400,
    "server_tz": "UTC",
}

PROJECTS_RESPONSE = {
    "projects": [
        {"project_id": "project-alpha", "name": "Alpha", "color": "#2563eb"},
        {"project_id": "project-beta", "name": "Beta", "color": "#16a34a"},
    ]
}

PROFILES_RESPONSE = {
    "active": "default",
    "single_runtime": False,
    "profiles": [
        {
            "name": "default",
            "is_default": True,
            "gateway_running": True,
            "model": "openai/gpt-5.6",
            "provider": "openai",
            "enabled_skills": 2,
            "total_skills": 3,
            "has_env": True,
            "default_workspace": "/tmp/default-workspace",
        },
        {
            "name": "research",
            "is_default": False,
            "gateway_running": False,
            "model": "anthropic/claude-sonnet",
            "provider": "anthropic",
            "enabled_skills": 1,
            "total_skills": 2,
            "has_env": False,
            "default_workspace": "/tmp/research-workspace",
        },
    ],
}


def _wait_for_health(timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.25)
    return False


def _wait_until(predicate, description: str, timeout: float = 10) -> None:
    """Poll from Python so the page's strict CSP never needs unsafe-eval."""
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as error:
            last_error = error
        time.sleep(0.05)
    detail = f"; last error: {last_error}" if last_error else ""
    raise AssertionError(f"timed out waiting for {description}{detail}")


def _fulfill_json(route, payload: dict) -> None:
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    )


def _visible_session_ids(page) -> list[str]:
    return page.locator(".taiji-session-row[data-session-id]:visible").evaluate_all(
        "rows => rows.map(row => row.dataset.sessionId)"
    )


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SETUP FAIL: playwright is not installed", file=sys.stderr)
        return 2

    state_dir = tempfile.mkdtemp(prefix="taiji-project-profile-keyboard-smoke-")
    evidence_value = os.getenv("PROJECT_PROFILE_KEYBOARD_SMOKE_EVIDENCE_DIR", "")
    evidence_dir = Path(evidence_value).resolve() if evidence_value else None
    if evidence_dir:
        evidence_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key in list(env):
        if key.endswith("_API_KEY"):
            env.pop(key, None)
    env.update(
        {
            "HERMES_WEBUI_PORT": str(PORT),
            "HERMES_WEBUI_HOST": "127.0.0.1",
            "HERMES_WEBUI_STATE_DIR": state_dir,
            "HERMES_HOME": state_dir,
            "HERMES_BASE_HOME": state_dir,
            "HERMES_WEBUI_SKIP_ONBOARDING": "1",
            "HERMES_WEBUI_AGENT_DIR": str(Path(state_dir) / "no-agent"),
            "TAIJI_WEBUI_TEST_NETWORK_BLOCK": "1",
        }
    )

    log_path = Path(state_dir) / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py")],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            if not _wait_for_health():
                print("SETUP FAIL: candidate server did not become healthy", file=sys.stderr)
                print(log_path.read_text(encoding="utf-8")[-3000:], file=sys.stderr)
                return 2

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                )
                page = context.new_page()
                errors: list[tuple[str, str]] = []
                page.on(
                    "console",
                    lambda message: errors.append(("console", message.text))
                    if message.type == "error"
                    else None,
                )
                page.on(
                    "pageerror",
                    lambda error: errors.append(
                        ("pageerror", getattr(error, "stack", "") or str(error))
                    ),
                )

                def route_request(route):
                    request_url = route.request.url
                    parsed = urllib.parse.urlsplit(request_url)
                    if request_url.startswith(BASE) and parsed.path == "/api/sessions":
                        _fulfill_json(route, SESSIONS_RESPONSE)
                    elif request_url.startswith(BASE) and parsed.path == "/api/projects":
                        _fulfill_json(route, PROJECTS_RESPONSE)
                    elif request_url.startswith(BASE) and parsed.path == "/api/profiles":
                        _fulfill_json(route, PROFILES_RESPONSE)
                    elif request_url.startswith(BASE + "/"):
                        route.continue_()
                    else:
                        route.abort("blockedbyclient")

                page.route("**/*", route_request)

                page.goto(BASE + "/", wait_until="domcontentloaded")
                _wait_until(
                    lambda: page.evaluate(
                        "typeof renderSessionList === 'function'"
                        " && typeof switchPanel === 'function'"
                    ),
                    "session and panel functions",
                )
                page.evaluate("TaijiHomeController.refreshSessions()")
                page.wait_for_selector(
                    "#taijiProjectFilterTrigger",
                    state="visible",
                )
                _wait_until(
                    lambda: set(_visible_session_ids(page))
                    == {
                        "session-project-alpha",
                        "session-unassigned",
                        "session-project-beta",
                    },
                    "the initial unfiltered session list",
                )

                all_filter = page.locator(
                    ".taiji-filter[data-taiji-session-filter='all']"
                )
                ungrouped_filter = page.locator(
                    ".taiji-filter[data-taiji-session-filter='ungrouped']"
                )
                all_filter.focus()
                page.keyboard.press("Tab")
                assert ungrouped_filter.evaluate("el => document.activeElement === el")
                ungrouped_filter.press("Space")
                _wait_until(
                    lambda: _visible_session_ids(page) == ["session-unassigned"],
                    "the ungrouped keyboard filter",
                )
                assert "is-active" in (ungrouped_filter.get_attribute("class") or "")
                assert ungrouped_filter.evaluate("el => document.activeElement === el")

                all_filter.focus()
                all_filter.press("Enter")
                _wait_until(
                    lambda: set(_visible_session_ids(page))
                    == {
                        "session-project-alpha",
                        "session-unassigned",
                        "session-project-beta",
                    },
                    "the All keyboard filter",
                )
                assert "is-active" in (all_filter.get_attribute("class") or "")
                assert all_filter.evaluate("el => document.activeElement === el")

                project_trigger = page.locator("#taijiProjectFilterTrigger")
                project_trigger.focus()
                project_trigger.press("Enter")
                _wait_until(
                    lambda: project_trigger.get_attribute("aria-expanded") == "true",
                    "the project panel opened by Enter",
                )
                _wait_until(
                    lambda: page.locator("#taijiProjectSearch").evaluate(
                        "el => document.activeElement === el"
                    ),
                    "project search focus",
                )
                page.keyboard.press("Escape")
                project_trigger = page.locator("#taijiProjectFilterTrigger")
                _wait_until(
                    lambda: project_trigger.evaluate(
                        "el => document.activeElement === el"
                    ),
                    "project trigger focus after Escape",
                )
                # Re-open from the restored native-button focus. Space is
                # exercised below on the ungrouped filter and custom options;
                # Enter keeps this assertion about focus restoration isolated
                # from browser-specific synthetic-click timing.
                page.keyboard.press("Enter")
                _wait_until(
                    lambda: page.locator(
                        "#taijiProjectFilterTrigger"
                    ).get_attribute("aria-expanded")
                    == "true",
                    "the project panel opened from restored focus",
                )
                _wait_until(
                    lambda: page.locator("#taijiProjectSearch").evaluate(
                        "el => document.activeElement === el"
                    ),
                    "the project panel reopened from restored focus",
                )
                page.keyboard.press("Tab")
                alpha_option = page.locator(
                    ".taiji-project-panel-row[data-project-id='project-alpha']"
                )
                assert alpha_option.evaluate("el => document.activeElement === el")
                assert alpha_option.get_attribute("role") == "option"
                alpha_focus_style = alpha_option.evaluate(
                    "el => ({"
                    "focused: el.matches(':focus'),"
                    "focusVisible: el.matches(':focus-visible'),"
                    "borderColor: getComputedStyle(el).borderColor,"
                    "backgroundColor: getComputedStyle(el).backgroundColor,"
                    "outlineWidth: getComputedStyle(el).outlineWidth"
                    "})"
                )
                # A scripted panel rebuild can make Chromium's heuristic
                # :focus-visible state transient even though Tab moved focus.
                # The product contract is that the focused keyboard option
                # retains an explicit visible ring.
                assert alpha_focus_style["focused"] is True, alpha_focus_style
                assert alpha_option.evaluate(
                    "el => getComputedStyle(el).outlineWidth"
                ) == "2px"
                alpha_option.press("Enter")
                project_trigger = page.locator("#taijiProjectFilterTrigger")
                _wait_until(
                    lambda: project_trigger.evaluate(
                        "el => document.activeElement === el"
                    ),
                    "project trigger focus after selecting Alpha",
                )
                assert project_trigger.get_attribute("aria-expanded") == "false"
                assert _visible_session_ids(page) == ["session-project-alpha"]
                assert "Alpha" in page.locator("#taijiFilterStatus").text_content()

                project_trigger.press("Enter")
                _wait_until(
                    lambda: page.locator("#taijiProjectSearch").evaluate(
                        "el => document.activeElement === el"
                    ),
                    "the project panel reopened by Enter",
                )
                beta_option = page.locator(
                    ".taiji-project-panel-row[data-project-id='project-beta']"
                )
                beta_option.focus()
                beta_option.press("Space")
                project_trigger = page.locator("#taijiProjectFilterTrigger")
                _wait_until(
                    lambda: project_trigger.evaluate(
                        "el => document.activeElement === el"
                    ),
                    "project trigger focus after selecting Beta",
                )
                assert _visible_session_ids(page) == ["session-project-beta"]
                assert "Beta" in page.locator("#taijiFilterStatus").text_content()
                if evidence_dir:
                    page.screenshot(
                        path=str(evidence_dir / "project-filter-keyboard.png"),
                        full_page=True,
                    )

                page.evaluate("switchPanel('profiles',{fromRailClick:false})")
                page.wait_for_selector(
                    ".profile-card[data-name='research']", state="visible"
                )
                help_card = page.locator(".profile-help-card")
                help_card.focus()
                page.keyboard.press("Tab")
                default_card = page.locator(".profile-card[data-name='default']")
                assert default_card.evaluate("el => document.activeElement === el")
                assert default_card.get_attribute("role") == "button"
                assert default_card.get_attribute("tabindex") == "0"
                assert default_card.evaluate(
                    "el => getComputedStyle(el).outlineWidth"
                ) == "2px"
                default_card.press("Enter")
                assert default_card.get_attribute("aria-pressed") == "true"
                assert default_card.evaluate("el => document.activeElement === el")
                assert page.locator("#profileDetailTitle").text_content() == "default"

                page.keyboard.press("Tab")
                research_card = page.locator(".profile-card[data-name='research']")
                assert research_card.evaluate("el => document.activeElement === el")
                research_card.press("Space")
                assert research_card.get_attribute("aria-pressed") == "true"
                assert default_card.get_attribute("aria-pressed") == "false"
                assert research_card.evaluate("el => document.activeElement === el")
                assert page.locator("#profileDetailTitle").text_content() == "research"
                assert "research-workspace" in page.locator(
                    "#profileDetailBody"
                ).text_content()
                if evidence_dir:
                    page.screenshot(
                        path=str(evidence_dir / "profile-card-keyboard.png"),
                        full_page=True,
                    )

                assert not errors, errors
                context.close()
                browser.close()

            print(f"STATIC_ROOT={ROOT / 'static'}")
            if evidence_dir:
                print(f"EVIDENCE_DIR={evidence_dir}")
            print("PROJECT/PROFILE KEYBOARD BROWSER SMOKE PASSED")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
