#!/usr/bin/env python3
"""Real-browser smoke for the first-run setup workbench.

This script boots ``server.py`` from the checkout that contains this file and
binds both agent-directory environment variables to the sibling
``hermes-agent`` checkout.  Browser-boundary fixtures make the interaction
deterministic; backend readiness and persistence are covered separately by the
pytest API integration suite.

Usage:
  <agent-python> tests/onboarding_workbench_browser_smoke.py

Optional evidence directory:
  TAIJI_ONBOARDING_SMOKE_EVIDENCE_DIR=/absolute/path

Exit codes:
  0 - browser workflow passed
  1 - product assertion or browser runtime error
  2 - test environment could not start (for example Playwright is absent)
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


WEBUI_ROOT = Path(__file__).resolve().parent.parent
AGENT_ROOT = (WEBUI_ROOT.parent / "hermes-agent").resolve()
WORKTREE_ROOT = WEBUI_ROOT.parents[2].resolve()
SETUP_ITEM_IDS = ("license", "model", "workspace", "security")


def _select_port() -> int:
    configured = os.getenv("TAIJI_ONBOARDING_SMOKE_PORT", "").strip()
    requested = int(configured) if configured else 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", requested))
        except OSError as error:
            raise RuntimeError(
                f"smoke port {requested} is occupied; refusing to disturb its owner"
            ) from error
        return int(probe.getsockname()[1])


def _wait_for_health(base_url: str, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.25)
    return False


def _wait_until(predicate, description: str, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as error:  # browser may be between DOM renders
            last_error = error
        time.sleep(0.05)
    detail = f"; last error: {last_error}" if last_error else ""
    raise AssertionError(f"timed out waiting for {description}{detail}")


def _fulfill_json(route, payload: dict, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
        body=json.dumps(payload, ensure_ascii=False),
    )


def _setup_status(*, ready: bool) -> dict:
    states = {
        "license": (True, "源码烟测模式不要求产品授权。", None),
        "model": (
            ready,
            "模型已就绪。" if ready else "尚未保存模型凭据。",
            {"id": "configure_model", "label": "配置模型", "target_step": "setup"},
        ),
        "workspace": (
            ready,
            "工作区可访问。" if ready else "尚未选择可写工作区。",
            {"id": "choose_workspace", "label": "选择工作区", "target_step": "workspace"},
        ),
        "security": (True, "已启用本机调试模式。允许终端和代码执行，请仅在可信环境中使用。", None),
    }
    items = []
    for item_id in SETUP_ITEM_IDS:
        item_ready, reason, recovery = states[item_id]
        items.append(
            {
                "id": item_id,
                "label": {
                    "license": "授权",
                    "model": "模型",
                    "workspace": "工作区",
                    "security": "安全策略",
                }[item_id],
                "status": "ready" if item_ready else "action_required",
                "ready": item_ready,
                "reason": reason,
                "recovery": recovery or {"id": "retry", "label": "重新检查"},
            }
        )
    return {
        "schema_version": "taiji-setup-status/v1",
        "installed_production": True,
        "overall_ready": all(item["ready"] for item in items),
        "items": items,
    }


def _onboarding_status(workspace: Path, *, completed: bool, ready: bool) -> dict:
    return {
        "completed": completed,
        "settings": {
            "default_model": "anthropic/claude-sonnet-4.6",
            "default_workspace": str(workspace),
            "password_enabled": False,
            "bot_name": "taiji Agent",
        },
        "system": {
            "hermes_found": True,
            "imports_ok": True,
            "config_exists": True,
            "chat_ready": ready,
            "provider_configured": ready,
            "provider_ready": ready,
            "setup_state": "ready" if ready else "needs_provider",
            "provider_note": "模型已就绪。" if ready else "需要配置模型。",
            "current_provider": "openrouter",
            "current_model": "anthropic/claude-sonnet-4.6",
        },
        "setup": {
            "providers": [
                {
                    "id": "openrouter",
                    "label": "OpenRouter",
                    "env_var": "OPENROUTER_API_KEY",
                    "default_model": "anthropic/claude-sonnet-4.6",
                    "default_base_url": "",
                    "requires_base_url": False,
                    "key_optional": False,
                    "models": [
                        {
                            "id": "anthropic/claude-sonnet-4.6",
                            "label": "Claude Sonnet 4.6",
                        }
                    ],
                    "category": "easy_start",
                    "quick": True,
                    "oauth_provider": "",
                    "oauth_label": "",
                },
                {
                    "id": "custom",
                    "label": "Custom OpenAI API",
                    "env_var": "",
                    "default_model": "",
                    "default_base_url": "",
                    "requires_base_url": True,
                    "key_optional": True,
                    "models": [],
                    "category": "advanced",
                    "quick": False,
                    "oauth_provider": "",
                    "oauth_label": "",
                }
            ],
            "categories": [
                {"id": "easy_start", "label": "快速开始", "providers": ["openrouter"]}
            ],
            "unsupported_note": "",
            "current_is_oauth": False,
            "current": {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4.6",
                "base_url": "",
            },
        },
        "workspaces": {"items": [], "last": None},
        "models": ["anthropic/claude-sonnet-4.6"],
        "preflight": _setup_status(ready=ready),
    }


def _assert_source_binding() -> str:
    server_path = (WEBUI_ROOT / "server.py").resolve()
    if not server_path.is_file():
        raise RuntimeError(f"candidate server is missing: {server_path}")
    if not AGENT_ROOT.is_dir():
        raise RuntimeError(f"candidate agent checkout is missing: {AGENT_ROOT}")
    git_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=WEBUI_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if Path(git_root).resolve() != WORKTREE_ROOT:
        raise RuntimeError(
            f"source binding mismatch: expected {WORKTREE_ROOT}, git reported {git_root}"
        )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=WEBUI_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "SETUP FAIL: playwright is not installed; browser smoke was not executed",
            file=sys.stderr,
        )
        return 2

    try:
        source_head = _assert_source_binding()
        port = _select_port()
        # Preserve a venv's ``bin/python`` path instead of resolving its
        # interpreter symlink; executing the resolved base binary would lose
        # the venv site-packages that the candidate server depends on.
        server_python = Path(
            os.path.abspath(
                os.path.expanduser(
                    os.getenv("TAIJI_ONBOARDING_SMOKE_SERVER_PYTHON", sys.executable)
                )
            )
        )
        if not server_python.is_file():
            raise RuntimeError(
                f"smoke server Python is missing: {server_python}"
            )
    except Exception as error:
        print(f"SETUP FAIL: {error}", file=sys.stderr)
        return 2

    base_url = f"http://127.0.0.1:{port}"
    evidence_value = os.getenv("TAIJI_ONBOARDING_SMOKE_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_value).resolve() if evidence_value else None
    if evidence_dir:
        evidence_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="taiji-onboarding-workbench-smoke-") as temp:
        temp_root = Path(temp)
        state_dir = temp_root / "state"
        workspace = temp_root / "workspace"
        state_dir.mkdir()
        workspace.mkdir()

        env = os.environ.copy()
        for key in tuple(env):
            if key.endswith("_API_KEY") or key in {
                "HERMES_WEBUI_SKIP_ONBOARDING",
                "TAIJI_WEBUI_SKIP_ONBOARDING",
            }:
                env.pop(key, None)
        env.update(
            {
                "HERMES_WEBUI_PORT": str(port),
                "HERMES_WEBUI_HOST": "127.0.0.1",
                "HERMES_WEBUI_STATE_DIR": str(state_dir),
                "HERMES_HOME": str(state_dir),
                "HERMES_BASE_HOME": str(state_dir),
                "HERMES_WEBUI_DEFAULT_WORKSPACE": str(workspace),
                "HERMES_WEBUI_AGENT_DIR": str(AGENT_ROOT),
                "TAIJI_WEBUI_AGENT_DIR": str(AGENT_ROOT),
                "HERMES_WEBUI_PYTHON": str(server_python),
                "TAIJI_WEBUI_PYTHON": str(server_python),
                "TAIJI_WEBUI_TEST_NETWORK_BLOCK": "1",
            }
        )

        log_path = state_dir / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(server_python), str(WEBUI_ROOT / "server.py")],
                cwd=WEBUI_ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                if not _wait_for_health(base_url):
                    print("SETUP FAIL: candidate server did not become healthy", file=sys.stderr)
                    print(log_path.read_text(encoding="utf-8")[-3000:], file=sys.stderr)
                    return 2

                runtime = {
                    "ready": False,
                    "completed": False,
                    "setup_posts": [],
                    "complete_posts": 0,
                    "setup_status_gets": 0,
                    "onboarding_status_failures": 0,
                }
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                    )

                    def install_route(page):
                        def route_request(route):
                            request = route.request
                            parsed = urllib.parse.urlsplit(request.url)
                            path = parsed.path
                            method = request.method.upper()
                            if not request.url.startswith(base_url + "/"):
                                route.abort("blockedbyclient")
                                return
                            if path == "/api/onboarding/status" and method == "GET":
                                if runtime["onboarding_status_failures"]:
                                    runtime["onboarding_status_failures"] -= 1
                                    _fulfill_json(
                                        route,
                                        {"error": "status_unavailable"},
                                        status=503,
                                    )
                                else:
                                    _fulfill_json(
                                        route,
                                        _onboarding_status(
                                            workspace,
                                            completed=runtime["completed"],
                                            ready=runtime["ready"],
                                        ),
                                    )
                            elif path == "/api/setup/status" and method == "GET":
                                runtime["setup_status_gets"] += 1
                                _fulfill_json(route, _setup_status(ready=runtime["ready"]))
                            elif path == "/api/onboarding/setup" and method == "POST":
                                payload = json.loads(request.post_data or "{}")
                                runtime["setup_posts"].append(payload)
                                if not payload.get("confirm_overwrite"):
                                    _fulfill_json(
                                        route,
                                        {
                                            "error": "config_exists",
                                            "error_code": "config_exists",
                                            "message": "当前终端已有模型配置，需要明确确认覆盖。",
                                            "requires_confirm": True,
                                            "recovery": {
                                                "id": "confirm_overwrite",
                                                "label": "确认覆盖并重试",
                                            },
                                        },
                                        status=409,
                                    )
                                else:
                                    _fulfill_json(
                                        route,
                                        _onboarding_status(
                                            workspace, completed=False, ready=False
                                        ),
                                    )
                            elif path == "/api/onboarding/probe" and method == "POST":
                                payload = json.loads(request.post_data or "{}")
                                if str(payload.get("base_url", "")).endswith("/empty"):
                                    _fulfill_json(
                                        route,
                                        {"ok": False, "error": "parse", "detail": "no usable models"},
                                    )
                                    return
                                _fulfill_json(
                                    route,
                                    {"ok": True, "models": [{"id": "local-model", "label": "Local model"}]},
                                )
                            elif path == "/api/workspaces/add" and method == "POST":
                                _fulfill_json(route, {"ok": True, "path": str(workspace)})
                            elif path == "/api/settings" and method == "POST":
                                runtime["ready"] = True
                                _fulfill_json(route, {"ok": True, "auth_enabled": False})
                            elif path == "/api/onboarding/complete" and method == "POST":
                                runtime["complete_posts"] += 1
                                if not runtime["ready"]:
                                    _fulfill_json(
                                        route,
                                        {
                                            "error": "setup_not_ready",
                                            "preflight": _setup_status(ready=False),
                                        },
                                        status=409,
                                    )
                                else:
                                    runtime["completed"] = True
                                    _fulfill_json(
                                        route,
                                        _onboarding_status(
                                            workspace, completed=True, ready=True
                                        ),
                                    )
                            else:
                                route.continue_()

                        page.route("**/*", route_request)

                    # Responsive gate: the workbench must remain inside a 390 px viewport.
                    mobile_context = browser.new_context(
                        locale="zh-CN", viewport={"width": 390, "height": 844}
                    )
                    mobile_page = mobile_context.new_page()
                    install_route(mobile_page)
                    mobile_page.goto(base_url + "/", wait_until="domcontentloaded")
                    mobile_page.locator("#onboardingOverlay").wait_for(state="visible")
                    _wait_until(
                        lambda: mobile_page.locator(".onboarding-check-row").count() == 4,
                        "four mobile setup rows",
                    )
                    overflow = mobile_page.evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                    )
                    if overflow:
                        raise AssertionError("onboarding workbench overflows the mobile viewport")
                    first_row_box = mobile_page.locator(".onboarding-check-row").first.bounding_box()
                    if not first_row_box or first_row_box["y"] >= 844:
                        raise AssertionError("mobile first viewport hides the setup workbench")
                    mobile_page.keyboard.press("Escape")
                    mobile_page.locator("#onboardingOverlay").wait_for(state="hidden")
                    mobile_resume = mobile_page.locator("#onboardingResumeBtn")
                    mobile_resume.wait_for(state="visible")
                    _wait_until(
                        lambda: mobile_page.evaluate(
                            "document.activeElement && document.activeElement.id === 'onboardingResumeBtn'"
                        ),
                        "mobile resume entry focus after keyboard close",
                    )
                    mobile_box = mobile_resume.bounding_box()
                    if not mobile_box or mobile_box["x"] < 0 or mobile_box["x"] + mobile_box["width"] > 390:
                        raise AssertionError("mobile resume entry is outside the narrow viewport")
                    if evidence_dir:
                        mobile_page.screenshot(
                            path=str(evidence_dir / "onboarding-resume-mobile.png"),
                            full_page=True,
                        )
                    mobile_page.keyboard.press("Enter")
                    mobile_page.locator("#onboardingOverlay").wait_for(state="visible")
                    if evidence_dir:
                        mobile_page.screenshot(
                            path=str(evidence_dir / "onboarding-workbench-mobile.png"),
                            full_page=True,
                        )
                    mobile_context.close()

                    # Initial status failure must open a visible, blocking retry
                    # state instead of treating "unknown" as "already complete".
                    runtime["onboarding_status_failures"] = 1
                    error_context = browser.new_context(
                        locale="zh-CN", viewport={"width": 1024, "height": 768}
                    )
                    error_page = error_context.new_page()
                    install_route(error_page)
                    error_page.goto(base_url + "/", wait_until="domcontentloaded")
                    error_page.locator("#onboardingOverlay").wait_for(state="visible")
                    error_page.get_by_text("检查失败", exact=True).wait_for()
                    if not error_page.locator("#onboardingNextBtn").is_disabled():
                        raise AssertionError("initial status failure did not block continue")
                    _wait_until(
                        lambda: error_page.evaluate("document.activeElement && document.activeElement.id")
                        == "onboardingStatusRetryBtn",
                        "status retry button initial focus",
                    )
                    error_page.get_by_role(
                        "button", name="全部重新检查", exact=True
                    ).click()
                    _wait_until(
                        lambda: error_page.locator(".onboarding-check-row").count() == 4,
                        "four setup rows after initial status retry",
                    )
                    if error_page.locator("#onboardingNextBtn").is_disabled():
                        raise AssertionError(
                            "top-level status retry left continue permanently blocked"
                        )
                    if evidence_dir:
                        error_page.screenshot(
                            path=str(evidence_dir / "onboarding-workbench-status-error-recovered.png"),
                            full_page=True,
                        )
                    error_context.close()

                    # A visible onboarding dialog owns Escape before the V3
                    # workbench.  Once it closes, the resume entry must either
                    # stay clear of the wide workbench or yield its focus to the
                    # workbench's close control on narrow layouts.
                    joint_context = browser.new_context(
                        locale="zh-CN", viewport={"width": 1281, "height": 800}
                    )
                    joint_page = joint_context.new_page()
                    install_route(joint_page)
                    joint_errors: list[str] = []
                    joint_page.on(
                        "console",
                        lambda message: joint_errors.append(f"console: {message.text}")
                        if message.type == "error"
                        else None,
                    )
                    joint_page.on(
                        "pageerror",
                        lambda error: joint_errors.append(
                            f"pageerror: {getattr(error, 'stack', '') or str(error)}"
                        ),
                    )
                    joint_page.goto(base_url + "/", wait_until="domcontentloaded")
                    joint_overlay = joint_page.locator("#onboardingOverlay")
                    joint_overlay.wait_for(state="visible")
                    _wait_until(
                        lambda: joint_page.locator(".onboarding-check-row").count() == 4,
                        "four joint-layout setup rows",
                    )
                    joint_page.evaluate(
                        """() => window.ExpertTeamV3.renderStatusSurface({
                          kind: 'expert_team',
                          productMode: 'standalone',
                          readOnly: false,
                          runId: 'onboarding-v3-smoke',
                          sourceSessionId: '',
                          version: 1,
                          currentStageId: 'draft',
                          publicState: 'awaiting_stage_confirmation',
                          allowedActions: ['stage_revise', 'stage_confirm'],
                          phase: '任务规格',
                          team: { id: 'content-creator-team', title: '联合烟测专家团' },
                          presentation: { visibleTitle: '联合布局验收' },
                          brief: { exactTitle: '联合布局验收', audience: '验收' },
                          progress: { done: 1, total: 5, current: '任务规格' },
                          workflow: { currentStage: { title: '任务规格' } },
                          stageActionBinding: {
                            session_id: 'onboarding-v3-smoke', run_id: 'onboarding-v3-smoke',
                            expected_version: 1, stage_id: 'draft', stage_attempt: 1,
                            artifact_id: 'draft:1', artifact_sha256: 'a'.repeat(64)
                          },
                        })"""
                    )
                    workbench = joint_page.locator("#expertTeamV3Workbench")
                    workbench.wait_for(state="attached")
                    joint_page.keyboard.press("Escape")
                    joint_overlay.wait_for(state="hidden")
                    if workbench.evaluate("root => root.classList.contains('is-collapsed')"):
                        raise AssertionError("onboarding Escape also collapsed the V3 workbench")
                    joint_resume = joint_page.locator("#onboardingResumeBtn")
                    joint_resume.wait_for(state="visible")
                    _wait_until(
                        lambda: joint_page.evaluate(
                            "document.activeElement && document.activeElement.id === 'onboardingResumeBtn'"
                        ),
                        "wide-layout resume focus after onboarding Escape",
                    )
                    wide_layout = joint_page.evaluate(
                        """() => {
                          const resume=document.querySelector('#onboardingResumeBtn');
                          const workbench=document.querySelector('#expertTeamV3Workbench');
                          const resumeRect=resume.getBoundingClientRect();
                          const workbenchRect=workbench.getBoundingClientRect();
                          const centerX=Math.round(resumeRect.left+resumeRect.width/2);
                          const centerY=Math.round(resumeRect.top+resumeRect.height/2);
                          const hit=document.elementFromPoint(centerX,centerY);
                          return {
                            resumeRight:resumeRect.right,
                            workbenchLeft:workbenchRect.left,
                            inViewport:resumeRect.left >= 0 && resumeRect.right <= innerWidth,
                            hitResume:Boolean(hit && (hit===resume || resume.contains(hit))),
                          };
                        }"""
                    )
                    if (
                        not wide_layout["inViewport"]
                        or not wide_layout["hitResume"]
                        or wide_layout["resumeRight"] > wide_layout["workbenchLeft"] - 8
                    ):
                        raise AssertionError(
                            f"wide onboarding resume overlaps or is covered by V3 workbench: {wide_layout}"
                        )
                    if evidence_dir:
                        joint_page.screenshot(
                            path=str(evidence_dir / "onboarding-v3-wide-resume.png"),
                            full_page=True,
                        )
                    joint_page.set_viewport_size({"width": 1280, "height": 800})
                    _wait_until(
                        lambda: joint_page.evaluate(
                            """() => {
                              const resume=document.querySelector('#onboardingResumeBtn');
                              return resume.getClientRects().length === 0
                                && getComputedStyle(resume).display === 'none'
                                && document.activeElement
                                && document.activeElement.matches('#expertTeamV3Workbench [data-et3-action="close-workbench"]');
                            }"""
                        ),
                        "resize from wide onboarding resume to narrow V3 close focus",
                    )
                    joint_page.evaluate("loadOnboardingWizard()")
                    joint_overlay.wait_for(state="visible")
                    joint_page.keyboard.press("Escape")
                    joint_overlay.wait_for(state="hidden")
                    if workbench.evaluate("root => root.classList.contains('is-collapsed')"):
                        raise AssertionError("reopened onboarding Escape also collapsed the V3 workbench")

                    for width, height in ((1280, 800), (1024, 768), (760, 800)):
                        joint_page.set_viewport_size({"width": width, "height": height})
                        _wait_until(
                            lambda: joint_page.evaluate(
                                """() => {
                                  const resume=document.querySelector('#onboardingResumeBtn');
                                  return resume.getClientRects().length === 0
                                    && getComputedStyle(resume).display === 'none';
                                }"""
                            ),
                            f"hidden narrow-layout resume entry at {width}px",
                        )
                        narrow_layout = joint_page.evaluate(
                            """() => {
                              const workbench=document.querySelector('#expertTeamV3Workbench');
                              const parent=workbench.parentElement;
                              return {
                                width:workbench.getBoundingClientRect().width,
                                parentWidth:parent.getBoundingClientRect().width,
                                viewportWidth:innerWidth,
                                overflow:document.documentElement.scrollWidth > document.documentElement.clientWidth,
                              };
                            }"""
                        )
                        if (
                            (
                                abs(narrow_layout["width"] - narrow_layout["parentWidth"]) > 1
                                and abs(narrow_layout["width"] - narrow_layout["viewportWidth"]) > 1
                            )
                            or narrow_layout["overflow"]
                        ):
                            raise AssertionError(
                                f"narrow V3 workbench does not fit at {width}px: {narrow_layout}"
                            )
                        joint_page.evaluate("loadOnboardingWizard()")
                        joint_overlay.wait_for(state="visible")
                        joint_page.keyboard.press("Escape")
                        joint_overlay.wait_for(state="hidden")
                        if workbench.evaluate("root => root.classList.contains('is-collapsed')"):
                            raise AssertionError(
                                f"narrow onboarding Escape collapsed the V3 workbench at {width}px"
                            )
                        try:
                            _wait_until(
                                lambda: joint_page.evaluate(
                                    "document.activeElement && document.activeElement.matches('#expertTeamV3Workbench [data-et3-action=\"close-workbench\"]')"
                                ),
                                f"narrow workbench close focus after onboarding Escape at {width}px",
                            )
                        except AssertionError:
                            print("focus diagnostic=" + json.dumps(joint_page.evaluate("""() => {
                              const describe=el=>el?{tag:el.tagName,id:el.id,action:el.dataset.et3Action,
                                display:getComputedStyle(el).display,visibility:getComputedStyle(el).visibility,
                                rects:el.getClientRects().length,disabled:el.disabled}:null;
                              return {width:innerWidth,active:describe(document.activeElement),
                                close:describe(document.querySelector('#expertTeamV3Workbench [data-et3-action="close-workbench"]')),
                                resume:describe(document.querySelector('#onboardingResumeBtn'))};
                            }""")), flush=True)
                            raise
                    if joint_errors:
                        raise AssertionError("joint onboarding/V3 browser errors: " + " | ".join(joint_errors))
                    if evidence_dir:
                        joint_page.screenshot(
                            path=str(evidence_dir / "onboarding-v3-narrow-resume-hidden.png"),
                            full_page=True,
                        )
                    joint_context.close()

                    context = browser.new_context(
                        locale="zh-CN", viewport={"width": 1440, "height": 960}
                    )
                    page = context.new_page()
                    install_route(page)
                    browser_errors: list[str] = []
                    page.on(
                        "console",
                        lambda message: browser_errors.append(
                            f"console: {message.text}"
                        )
                        if message.type == "error"
                        else None,
                    )
                    page.on(
                        "pageerror",
                        lambda error: browser_errors.append(
                            f"pageerror: {getattr(error, 'stack', '') or str(error)}"
                        ),
                    )
                    page.goto(base_url + "/", wait_until="domcontentloaded")
                    overlay = page.locator("#onboardingOverlay")
                    overlay.wait_for(state="visible")
                    assert page.locator("#onboardingSteps, .onboarding-step").count() == 0
                    _wait_until(
                        lambda: page.locator(".onboarding-check-row").count() == 4,
                        "four desktop setup rows",
                    )
                    actual_ids = page.locator(".onboarding-check-row").evaluate_all(
                        "rows => rows.map(row => row.dataset.setupCheck)"
                    )
                    if actual_ids != list(SETUP_ITEM_IDS):
                        raise AssertionError(f"unstable setup item IDs: {actual_ids}")
                    security_row = page.locator('[data-setup-check="security"]')
                    assert "本机调试" in security_row.inner_text()
                    assert "可信环境" in security_row.inner_text()
                    assert "需要处理" not in security_row.inner_text()
                    page.locator("#onboardingWorkbenchTitle").wait_for()
                    # ManagedDialog applies its initial focus on the next animation
                    # frame.  Do not start the probe interaction until that open
                    # transition has finished, or the delayed focus can race the
                    # URL field without representing a probe regression.
                    _wait_until(
                        lambda: page.evaluate(
                            "document.activeElement && document.activeElement.id === 'onboardingTitle'"
                        ),
                        "onboarding dialog initial focus",
                    )
                    if evidence_dir:
                        page.screenshot(
                            path=str(evidence_dir / "onboarding-workbench-default.png"),
                            full_page=True,
                        )

                    # A self-hosted endpoint probe must update status in place;
                    # a debounce or response must not destroy the focused URL input.
                    page.evaluate("ONBOARDING.step=1;_renderOnboardingSteps();syncOnboardingProvider('custom')")
                    base_url_input = page.locator("#onboardingBaseUrlInput")
                    base_url_input.fill("http://127.0.0.1:9999/v1")
                    _wait_until(
                        lambda: page.evaluate(
                            "document.activeElement && document.activeElement.id === 'onboardingBaseUrlInput'"
                        ),
                        "self-hosted probe preserves URL input focus",
                    )
                    _wait_until(
                        lambda: "onboarding-probe-ok" in (page.locator("#onboardingProbeStatus").get_attribute("class") or ""),
                        "self-hosted probe live status",
                    )
                    if page.evaluate("document.activeElement && document.activeElement.id") != "onboardingBaseUrlInput":
                        raise AssertionError("self-hosted probe response replaced the focused URL input")
                    base_url_input.fill("http://127.0.0.1:9999/empty")
                    _wait_until(
                        lambda: "onboarding-probe-error" in (page.locator("#onboardingProbeStatus").get_attribute("class") or ""),
                        "empty self-hosted catalog blocks onboarding",
                    )
                    if not page.locator("#onboardingNextBtn").is_disabled():
                        raise AssertionError("empty self-hosted model catalog left Continue enabled")
                    if page.evaluate("document.activeElement && document.activeElement.id") != "onboardingBaseUrlInput":
                        raise AssertionError("empty-catalog probe response replaced the focused URL input")
                    page.evaluate("loadOnboardingWizard()")
                    page.locator("#onboardingWorkbenchTitle").wait_for()

                    # External recovery must close the dialog without pulling
                    # keyboard focus back to the floating resume entry.
                    page.evaluate("""(() => {
                      const security=ONBOARDING.preflight.items.find(item=>item.id==='security');
                      security.ready=false;
                      security.status='action_required';
                      security.recovery={id:'open_security',label:'打开安全设置',target_section:'system'};
                      _renderOnboardingBody();
                      openOnboardingRecovery('security');
                    })()""")
                    overlay.wait_for(state="hidden")
                    _wait_until(
                        lambda: page.evaluate(
                            "Boolean(document.activeElement && document.activeElement.closest('[id^=settingsPane].active'))"
                        ),
                        "external recovery focus enters selected settings section",
                    )
                    page.evaluate("loadOnboardingWizard()")
                    overlay.wait_for(state="visible")
                    page.locator("#onboardingWorkbenchTitle").wait_for()

                    # Temporary close must expose an immediate, keyboard-usable
                    # re-entry without marking completion or refreshing the page.
                    page.locator("#onboardingSkipBtn").focus()
                    page.keyboard.press("Enter")
                    overlay.wait_for(state="hidden")
                    if runtime["complete_posts"] != 0:
                        raise AssertionError("temporary close called the completion endpoint")
                    resume = page.locator("#onboardingResumeBtn")
                    resume.wait_for(state="visible")
                    _wait_until(
                        lambda: page.evaluate(
                            "document.activeElement && document.activeElement.id === 'onboardingResumeBtn'"
                        ),
                        "desktop resume entry focus after close",
                    )
                    toast = page.locator("#toast")
                    toast.wait_for(state="visible")
                    resume_box = resume.bounding_box()
                    toast_box = toast.bounding_box()
                    if not resume_box or not toast_box:
                        raise AssertionError("resume entry or close toast has no layout box")
                    boxes_overlap = not (
                        resume_box["x"] + resume_box["width"] <= toast_box["x"]
                        or toast_box["x"] + toast_box["width"] <= resume_box["x"]
                        or resume_box["y"] + resume_box["height"] <= toast_box["y"]
                        or toast_box["y"] + toast_box["height"] <= resume_box["y"]
                    )
                    if boxes_overlap:
                        raise AssertionError("resume entry overlaps the temporary-close toast")
                    if evidence_dir:
                        page.screenshot(
                            path=str(evidence_dir / "onboarding-resume-desktop.png"),
                            full_page=True,
                        )
                    page.keyboard.press("Enter")
                    overlay.wait_for(state="visible")
                    _wait_until(
                        lambda: page.locator(".onboarding-check-row").count() == 4,
                        "setup rows after refresh-free resume",
                    )

                    # Per-item retry must call the status API and restore row focus.
                    calls_before_retry = runtime["setup_status_gets"]
                    model_row = page.locator('[data-setup-check="model"]')
                    model_row.locator(".onboarding-check-retry").click()
                    _wait_until(
                        lambda: runtime["setup_status_gets"] > calls_before_retry,
                        "model retry API call",
                    )
                    _wait_until(
                        lambda: page.evaluate(
                            "document.activeElement && document.activeElement.id === 'onboardingCheck-model'"
                        ),
                        "focus restoration after model retry",
                    )

                    # Keyboard advance, then fill the recoverable configuration path.
                    page.locator("#onboardingNextBtn").focus()
                    page.keyboard.press("Enter")
                    page.locator("#onboardingProviderSelect").wait_for()
                    page.locator("#onboardingApiKeyInput").fill("smoke-secret-not-sent")
                    page.locator("#onboardingNextBtn").click()
                    page.locator("#onboardingWorkspaceInput").wait_for()
                    page.locator("#onboardingWorkspaceInput").fill(str(workspace))
                    page.locator("#onboardingModelSelect").select_option(
                        "anthropic/claude-sonnet-4.6"
                    )
                    page.locator("#onboardingNextBtn").click()
                    page.locator("#onboardingPasswordInput").wait_for()
                    page.locator("#onboardingNextBtn").click()
                    page.get_by_role("heading", name="完成前复检").wait_for()

                    # First save conflicts; only explicit confirmation may retry overwrite.
                    page.locator("#onboardingNextBtn").click()
                    confirm = page.locator("#onboardingConfirmOverwriteBtn")
                    confirm.wait_for()
                    _wait_until(
                        lambda: page.evaluate(
                            "document.activeElement && document.activeElement.id === 'onboardingConfirmOverwriteBtn'"
                        ),
                        "overwrite confirmation focus",
                    )
                    if runtime["complete_posts"] != 0:
                        raise AssertionError("completion ran before overwrite confirmation")
                    if evidence_dir:
                        page.screenshot(
                            path=str(evidence_dir / "onboarding-workbench-conflict.png"),
                            full_page=True,
                        )
                    confirm.click()
                    overlay.wait_for(state="hidden")
                    if not page.locator("#onboardingResumeBtn").is_hidden():
                        raise AssertionError("completed onboarding left the resume entry visible")

                    if len(runtime["setup_posts"]) != 2:
                        raise AssertionError(
                            f"expected conflict plus confirmed retry, got {runtime['setup_posts']}"
                        )
                    if runtime["setup_posts"][0].get("confirm_overwrite"):
                        raise AssertionError("initial setup request pre-confirmed overwrite")
                    if runtime["setup_posts"][1].get("confirm_overwrite") is not True:
                        raise AssertionError("confirmed retry omitted confirm_overwrite=true")
                    if runtime["complete_posts"] != 1 or not runtime["completed"]:
                        raise AssertionError("ready setup was not completed exactly once")
                    expected_conflict_console = (
                        "console: Failed to load resource: the server responded with a status of 409 (Conflict)"
                    )
                    unexpected_browser_errors = [
                        error for error in browser_errors if error != expected_conflict_console
                    ]
                    if unexpected_browser_errors:
                        raise AssertionError(
                            "browser runtime errors: " + " | ".join(unexpected_browser_errors)
                        )
                    if evidence_dir:
                        page.screenshot(
                            path=str(evidence_dir / "onboarding-workbench-completed.png"),
                            full_page=True,
                        )
                    context.close()
                    browser.close()

                print(f"PASS source_worktree={WORKTREE_ROOT}")
                print(f"PASS source_head={source_head}")
                print(f"PASS agent_dir={AGENT_ROOT}")
                print("PASS onboarding_workbench=mobile+desktop+keyboard+retry+conflict+completion")
                return 0
            except Exception as error:
                print(f"BROWSER SMOKE FAILED: {error}", file=sys.stderr)
                print(f"source_worktree={WORKTREE_ROOT}", file=sys.stderr)
                print(f"source_head={source_head}", file=sys.stderr)
                print(log_path.read_text(encoding="utf-8")[-3000:], file=sys.stderr)
                return 1
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
