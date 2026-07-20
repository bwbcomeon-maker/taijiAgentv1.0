#!/usr/bin/env python3
"""Real-browser smoke for the unified image capability settings path.

The test boots this checkout's WebUI with isolated state, stubs only the new
capability API in the browser, and drives the visible Settings controls.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT.parent / "hermes-agent"
PORT = int(os.getenv("IMAGE_CAPABILITY_SMOKE_PORT", "8798"))
BASE = f"http://127.0.0.1:{PORT}"

CAPABILITY_RESPONSE = {
    "ok": True,
    "profile": "default",
    "revision": "a" * 64,
    "capabilities": {
        "vision": {
            "enabled": True,
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
            "credential_ref": "alibaba-default",
            "endpoint_values": {},
            "key_status": {"configured": True},
            "verification": {"status": "configured_unverified"},
        },
        "image_generation": {
            "enabled": False,
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
            "credential_ref": "alibaba-default",
            "endpoint_values": {},
            "verification": {"status": "unconfigured"},
        },
    },
    "providers": [
        {
            "provider_family": "alibaba_dashscope",
            "label": "阿里云百炼",
            "capabilities": ["vision", "image_generation"],
            "provider_ids": {
                "vision": "alibaba",
                "image_generation": "dashscope",
            },
            "auth_type": "api_key",
            "support_level": "native",
            "supports_named_credentials": True,
            "selectable": True,
            "models": {
                "vision": [{"id": "qwen3-vl-plus", "label": "Qwen3 VL Plus"}],
                "image_generation": [
                    {"id": "qwen-image-2.0-pro", "label": "Qwen Image 2.0 Pro"}
                ],
            },
            "default_models": {
                "vision": "qwen3-vl-plus",
                "image_generation": "qwen-image-2.0-pro",
            },
            "credential_fields": [
                {"name": "api_key", "label": "API Key", "secret": True}
            ],
            "endpoint_fields": [],
        }
    ],
    "provider_credentials": [
        {
            "id": "alibaba-default",
            "provider_family": "alibaba_dashscope",
            "label": "阿里默认凭据",
            "default": True,
            "configured": True,
        }
    ],
    "effective_route": {
        "vision": {
            "route": "auxiliary_vision",
            "provider": "alibaba",
            "model": "qwen3-vl-plus",
        },
        "image_generation": {
            "route": "unavailable",
            "provider": "dashscope",
            "model": "qwen-image-2.0-pro",
        },
    },
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
    """Poll from Python without requiring unsafe-eval in the page CSP."""
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as error:  # The page may still be navigating/rendering.
            last_error = error
        time.sleep(0.05)
    detail = f"; last error: {last_error}" if last_error else ""
    raise AssertionError(f"timed out waiting for {description}{detail}")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SETUP FAIL: playwright is not installed", file=sys.stderr)
        return 2

    state_dir = tempfile.mkdtemp(prefix="taiji-image-capability-smoke-")
    evidence_dir_value = os.getenv("IMAGE_CAPABILITY_SMOKE_EVIDENCE_DIR", "")
    evidence_dir = Path(evidence_dir_value).resolve() if evidence_dir_value else None
    if evidence_dir:
        evidence_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for key in list(env):
        if key.endswith("_API_KEY"):
            env.pop(key, None)
    python_path = os.pathsep.join(
        path
        for path in (str(AGENT_DIR), str(ROOT), env.get("PYTHONPATH", ""))
        if path
    )
    env.update(
        {
            "HERMES_WEBUI_PORT": str(PORT),
            "HERMES_WEBUI_HOST": "127.0.0.1",
            "HERMES_WEBUI_STATE_DIR": state_dir,
            "HERMES_HOME": state_dir,
            "HERMES_BASE_HOME": state_dir,
            "HERMES_WEBUI_SKIP_ONBOARDING": "1",
            "HERMES_WEBUI_AGENT_DIR": str(AGENT_DIR),
            "PYTHONPATH": python_path,
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
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                # imageCapabilityBind() starts a GET during DOMContentLoaded.
                # Stub that first request at the browser boundary so it cannot
                # race with the controllable window.api double installed below.
                page.route(
                    "**/api/image-capabilities",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(CAPABILITY_RESPONSE, ensure_ascii=False),
                    ),
                )
                page.goto(BASE + "/#settings", wait_until="domcontentloaded")
                _wait_until(
                    lambda: page.evaluate(
                        "typeof window.loadImageCapabilityCenter === 'function'"
                    ),
                    "image capability loader",
                )
                page.wait_for_selector(
                    "#imageCapabilityCenter[data-state='ready']",
                    state="attached",
                )

                page.evaluate(
                    """response => {
                      window.__imageCapabilityGets = 0;
                      window.__imageCapabilityPosts = [];
                      window.__imageCapabilityMode = 'normal';
                      window.__resolveImageCapability = null;
                      window.api = async (url, options) => {
                        if (url === '/api/image-capabilities') {
                          window.__imageCapabilityGets += 1;
                          return structuredClone(response);
                        }
                        if (url === '/api/image-capabilities/configure') {
                          window.__imageCapabilityPosts.push(JSON.parse(options.body));
                          if (window.__imageCapabilityMode === 'conflict') {
                            const error = new Error('configuration changed');
                            error.status = 409;
                            error.payload = {error_code:'configuration_conflict'};
                            throw error;
                          }
                          if (window.__imageCapabilityMode === 'timeout') {
                            const error = new Error('request timeout');
                            error.name = 'TimeoutError';
                            error.timeout = true;
                            throw error;
                          }
                          const saved = structuredClone(response);
                          saved.revision = 'b'.repeat(64);
                          saved.capabilities.image_generation.enabled = true;
                          saved.capabilities.image_generation.verification = {status:'verified'};
                          saved.capabilities.vision.verification = {status:'verified'};
                          saved.effective_route.image_generation = {
                            route:'image_generation_provider',
                            provider:'dashscope',
                            model:'qwen-image-2.0-pro'
                          };
                          saved.verification_results = {
                            vision:{status:'verified'},
                            image_generation:{status:'verified'}
                          };
                          if (window.__imageCapabilityMode === 'superseded') {
                            saved.request_status = 'superseded';
                            saved.verification_results = {
                              vision:{status:'superseded'},
                              image_generation:{status:'superseded'}
                            };
                          }
                          if (window.__imageCapabilityMode === 'pending') {
                            return new Promise(resolve => {
                              window.__resolveImageCapability = () => resolve(saved);
                            });
                          }
                          return saved;
                        }
                        return {};
                      };
                    }""",
                    CAPABILITY_RESPONSE,
                )
                page.evaluate("window.loadImageCapabilityCenter(true)")
                # The card lives in the hidden Settings/Models pane until the
                # user navigates there. First wait for data readiness without
                # incorrectly requiring pre-navigation visibility.
                page.wait_for_selector(
                    "#imageCapabilityCenter[data-state='ready']",
                    state="attached",
                )
                _wait_until(
                    lambda: page.evaluate("window.__imageCapabilityGets === 1"),
                    "one controlled capability reload",
                )

                page.evaluate(
                    """() => {
                      if (typeof switchPanel === 'function') switchPanel('settings');
                      if (typeof switchSettingsSection === 'function') switchSettingsSection('models');
                    }"""
                )
                page.locator("#imageCapabilityCenter").scroll_into_view_if_needed()
                assert page.locator("[data-image-capability='vision']").is_visible()
                assert page.locator(
                    "[data-image-capability='image_generation']"
                ).is_visible()
                vision_provider_options = page.locator(
                    "#imageCapabilityVisionProvider option"
                ).evaluate_all(
                    "options => options.map(option => ({value: option.value, text: option.textContent}))"
                )
                assert len(vision_provider_options) == 1, vision_provider_options
                assert "阿里云百炼" in page.locator(
                    "#imageCapabilityVisionProvider"
                ).text_content()
                assert "辅助识图模型" in page.locator(
                    "#imageCapabilityVisionRoute"
                ).text_content()
                assert "当前无可用生图路由" in page.locator(
                    "#imageCapabilityGenerationRoute"
                ).text_content()
                if evidence_dir:
                    page.screenshot(
                        path=str(evidence_dir / "image-capability-center-desktop.png"),
                        full_page=True,
                    )

                generation_switch = page.locator(
                    "#imageCapabilityGenerationEnabled"
                )
                generation_switch.focus()
                page.keyboard.press("Space")
                assert generation_switch.is_checked()
                # Hold the first request open so the second click exercises
                # the real busy-state de-duplication window.
                page.evaluate("window.__imageCapabilityMode = 'pending'")
                save_button = page.locator("#btnSaveVerifyImageCapabilityCenter")
                assert not save_button.is_disabled()
                save_button.click()
                _wait_until(
                    lambda: page.evaluate(
                        "window.__resolveImageCapability !== null"
                    ),
                    "the first pending capability save",
                )
                assert save_button.is_disabled()
                page.evaluate("window.saveAndVerifyImageCapabilityCenter()")
                assert page.evaluate("window.__imageCapabilityPosts.length") == 1
                payload = page.evaluate("window.__imageCapabilityPosts[0]")
                assert payload["expected_revision"] == "a" * 64
                assert isinstance(payload["request_id"], str)
                assert len(payload["request_id"]) >= 16
                assert payload["capabilities"]["vision"]["enabled"] is True
                assert (
                    payload["capabilities"]["image_generation"]["enabled"] is True
                )
                assert payload["verify"] == ["vision", "image_generation"]
                page.evaluate("window.__resolveImageCapability()")
                _wait_until(
                    lambda: page.locator(
                        "#btnSaveVerifyImageCapabilityCenter"
                    ).get_attribute("aria-busy")
                    == "false",
                    "the first capability verification",
                )
                page.evaluate("window.__imageCapabilityMode = 'normal'")
                assert "已验证" in page.locator(
                    "#imageCapabilityVisionVerification"
                ).text_content()
                assert "已验证" in page.locator(
                    "#imageCapabilityGenerationVerification"
                ).text_content()
                assert "生图 Provider" in page.locator(
                    "#imageCapabilityGenerationRoute"
                ).text_content()
                assert "qwen-image-2.0-pro" in page.locator(
                    "#imageCapabilityGenerationRoute"
                ).text_content()

                # A real workspace.api 409 exposes error_code under error.payload.
                page.evaluate("window.__imageCapabilityMode = 'conflict'")
                generation_switch.focus()
                page.keyboard.press("Space")
                page.locator("#btnSaveVerifyImageCapabilityCenter").click()
                _wait_until(
                    lambda: "其他窗口"
                    in page.locator(
                        "#imageCapabilityCenterStatusTitle"
                    ).text_content(),
                    "the configuration conflict state",
                )
                first_conflict_id = page.evaluate(
                    "window.__imageCapabilityPosts.at(-1).request_id"
                )
                page.locator("#btnSaveVerifyImageCapabilityCenter").click()
                _wait_until(
                    lambda: page.evaluate(
                        "window.__imageCapabilityPosts.length === 3"
                    ),
                    "the conflict retry",
                )
                second_conflict_id = page.evaluate(
                    "window.__imageCapabilityPosts.at(-1).request_id"
                )
                assert first_conflict_id != second_conflict_id

                # A timeout keeps the idempotency key, so retrying cannot double-charge.
                page.evaluate("window.__imageCapabilityMode = 'timeout'")
                page.locator("#btnSaveVerifyImageCapabilityCenter").click()
                _wait_until(
                    lambda: "超时"
                    in page.locator(
                        "#imageCapabilityCenterStatusTitle"
                    ).text_content(),
                    "the timeout state",
                )
                timeout_id = page.evaluate(
                    "window.__imageCapabilityPosts.at(-1).request_id"
                )
                page.locator("#btnSaveVerifyImageCapabilityCenter").click()
                _wait_until(
                    lambda: page.evaluate(
                        "window.__imageCapabilityPosts.length === 5"
                    ),
                    "the timeout retry",
                )
                retry_id = page.evaluate(
                    "window.__imageCapabilityPosts.at(-1).request_id"
                )
                assert timeout_id == retry_id
                assert "请勿刷新或修改配置" in page.locator(
                    "#imageCapabilityCenterStatusDetail"
                ).text_content()

                # A superseded request is a warning, never a green completion.
                page.evaluate("window.__imageCapabilityMode = 'superseded'")
                page.locator("#btnSaveVerifyImageCapabilityCenter").click()
                _wait_until(
                    lambda: "较新配置取代"
                    in page.locator(
                        "#imageCapabilityCenterStatusTitle"
                    ).text_content(),
                    "the superseded state",
                )
                assert "未执行旧配置验证" in page.locator(
                    "#imageCapabilityCenterStatusTitle"
                ).text_content()
                assert "当前显示为最新服务器状态" in page.locator(
                    "#imageCapabilityCenterStatusDetail"
                ).text_content()

                # Reload never overwrites a dirty draft without explicit confirmation.
                page.evaluate("window.__imageCapabilityMode = 'normal'")
                generation_switch.focus()
                page.keyboard.press("Space")
                get_count = page.evaluate("window.__imageCapabilityGets")
                page.evaluate("window.showConfirmDialog = async () => false")
                page.evaluate("window.loadImageCapabilityCenter(true)")
                assert page.evaluate("window.__imageCapabilityGets") == get_count
                page.evaluate("window.showConfirmDialog = async () => true")
                page.evaluate("window.loadImageCapabilityCenter(true)")
                _wait_until(
                    lambda: page.evaluate("window.__imageCapabilityGets")
                    == get_count + 1,
                    "the confirmed capability reload",
                )

                # A refresh attempt during a pending save is a no-op.
                generation_switch = page.locator(
                    "#imageCapabilityGenerationEnabled"
                )
                generation_switch.focus()
                page.keyboard.press("Space")
                page.evaluate("window.__imageCapabilityMode = 'pending'")
                page.locator("#btnSaveVerifyImageCapabilityCenter").click()
                _wait_until(
                    lambda: page.evaluate(
                        "window.__resolveImageCapability !== null"
                    ),
                    "the pending capability save",
                )
                get_count = page.evaluate("window.__imageCapabilityGets")
                page.evaluate("window.loadImageCapabilityCenter(true)")
                assert page.evaluate("window.__imageCapabilityGets") == get_count
                page.evaluate("window.__resolveImageCapability()")
                _wait_until(
                    lambda: page.locator(
                        "#btnSaveVerifyImageCapabilityCenter"
                    ).get_attribute("aria-busy")
                    == "false",
                    "the pending capability save completion",
                )

                page.set_viewport_size({"width": 360, "height": 800})
                page.locator("#imageCapabilityCenter").scroll_into_view_if_needed()
                vision_box = page.locator(
                    "[data-image-capability='vision']"
                ).bounding_box()
                image_box = page.locator(
                    "[data-image-capability='image_generation']"
                ).bounding_box()
                assert vision_box and image_box
                assert image_box["y"] > vision_box["y"] + vision_box["height"] - 4
                assert page.locator(
                    "#btnSaveVerifyImageCapabilityCenter"
                ).is_visible()
                if evidence_dir:
                    page.screenshot(
                        path=str(evidence_dir / "image-capability-center-mobile.png"),
                        full_page=True,
                    )
                browser.close()

            print(f"STATIC_ROOT={ROOT / 'static'}")
            print(f"AGENT_DIR={AGENT_DIR}")
            if evidence_dir:
                print(f"EVIDENCE_DIR={evidence_dir}")
            print("IMAGE CAPABILITY CENTER BROWSER SMOKE PASSED")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
