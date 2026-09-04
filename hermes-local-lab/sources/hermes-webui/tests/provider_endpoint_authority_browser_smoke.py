"""Headless, isolated browser smoke for the Task 9 endpoint contracts.

This script uses the repository's existing server and static assets, while
intercepting the small API boundary needed for deterministic UI states.  It
never opens the default browser or contacts a provider.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT.parent / "hermes-agent"
AGENT_PYTHON = AGENT_ROOT / "venv" / "bin" / "python"
BIGMODEL_URL = "https://open.bigmodel.cn/api/paas/v4"
DEEPSEEK_URL = "https://api.deepseek.com/v1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _model_config_fixture() -> dict:
    endpoint = {
        "display_url": BIGMODEL_URL,
        "policy": "fixed",
        "source": "system",
        "editable": False,
        "status": "resolved",
        "override_ignored": True,
    }
    rows = [
        {
            "id": "zai-cn",
            "display_name": "智谱 GLM（国内）",
            "configurable": True,
            "has_key": True,
            "key_source": "env_file",
            "models": [{"id": "glm-5"}],
            "endpoint": endpoint,
        },
        {
            "id": "deepseek",
            "display_name": "DeepSeek",
            "configurable": True,
            "has_key": True,
            "key_source": "env_file",
            "models": [{"id": "deepseek-chat"}],
            "endpoint": {
                "display_url": DEEPSEEK_URL,
                "policy": "configurable",
                "source": "managed",
                "editable": False,
                "status": "resolved",
                "override_ignored": False,
            },
        },
        {
            "id": "qwen-oauth",
            "display_name": "Qwen OAuth",
            "configurable": False,
            "is_oauth": True,
            "has_key": False,
            "key_source": "oauth",
            "models": [{"id": "qwen-plus"}],
            "endpoint": {
                "display_url": "",
                "policy": "runtime_managed",
                "source": "runtime",
                "editable": False,
                "status": "runtime_managed",
                "override_ignored": False,
            },
        },
        {
            "id": "runtime-managed",
            "display_name": "Runtime Managed",
            "configurable": True,
            "has_key": False,
            "key_source": "none",
            "models": [{"id": "runtime-model"}],
            "endpoint": {
                "display_url": "",
                "policy": "configurable",
                "source": "runtime",
                "editable": False,
                "status": "runtime_unresolved",
                "override_ignored": False,
            },
        },
        {
            "id": "missing-provider",
            "display_name": "Missing Endpoint",
            "configurable": True,
            "has_key": False,
            "key_source": "none",
            "models": [{"id": "missing-model"}],
            "endpoint": {
                "display_url": "",
                "policy": "configurable",
                "source": "managed",
                "editable": False,
                "status": "missing",
                "override_ignored": False,
            },
        },
        {
            "id": "custom:acme",
            "display_name": "Acme Custom",
            "configurable": False,
            "is_custom": True,
            "has_key": False,
            "key_source": "none",
            "models": [{"id": "acme-model"}],
            "endpoint": {
                "display_url": "https://custom.example/v1",
                "policy": "configurable",
                "source": "managed",
                "editable": False,
                "status": "resolved",
                "override_ignored": False,
            },
        },
        {
            "id": "custom",
            "display_name": "Custom",
            "configurable": True,
            "has_key": False,
            "key_source": "none",
            "models": [{"id": "custom-model"}],
            "endpoint": {
                "display_url": "",
                "policy": "configurable",
                "source": "custom",
                "editable": False,
                "status": "missing",
                "override_ignored": False,
            },
        },
    ]
    return {
        "ok": True,
        "profile": "default",
        "config": {"label": "本机配置", "exists": True, "source": "active_profile"},
        "main_request_id": "",
        "main": {
            "provider": "zai-cn",
            "model": "glm-5",
            "base_url": "",
            "endpoint": endpoint,
            "key_status": {"configured": True, "source": "env_file", "env_var": "GLM_CN_API_KEY"},
            "verification": {"state": "configured_unverified"},
        },
        "providers": rows,
        "auxiliary": {},
        "vision": {},
        "vision_providers": [],
        "image_gen": {},
        "image_gen_providers": [],
        "provider_credentials": [],
        "custom": {"supported": True, "key_env": "HERMES_CUSTOM_MODEL_API_KEY"},
    }


def _providers_fixture() -> dict:
    return {
        "providers": [row for row in _model_config_fixture()["providers"] if row["id"] != "custom"],
    }


def _write_json(route, payload: dict, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    )


def _copy_json(payload: dict) -> dict:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _saved_model_config(payload: dict, pending: bool, fixed_cleanup: bool = False) -> dict:
    data = _model_config_fixture()
    provider_id = str(payload["provider"])
    provider = next(row for row in data["providers"] if row["id"] == provider_id)
    endpoint = _copy_json(provider.get("endpoint") or {})
    if provider_id == "custom":
        endpoint.update(
            {
                "display_url": str(payload["base_url"]).strip(),
                "source": "custom",
                "status": "resolved",
                "editable": True,
            }
        )
    if fixed_cleanup:
        endpoint["override_ignored"] = False
    data["main_request_id"] = str(payload["request_id"])
    data["main"] = {
        "provider": provider_id,
        "model": str(payload["model"]),
        "base_url": str(payload.get("base_url") or ""),
        "endpoint": endpoint,
        "key_status": {"configured": True, "source": "fixture", "env_var": ""},
        "verification": {"state": "configured_unverified"},
    }
    data["runtime_state"] = "refresh_pending" if pending else "applied"
    data["refresh_pending"] = pending
    return data


def _run_smoke() -> None:
    from playwright.sync_api import expect, sync_playwright

    evidence_dir = Path(tempfile.mkdtemp(prefix="task9-browser-evidence-", dir="/private/tmp"))
    state_dir = Path(tempfile.mkdtemp(prefix="task9-browser-state-", dir="/private/tmp"))
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server_env = os.environ.copy()
    for key in list(server_env):
        if key.endswith("_API_KEY") or key in {"OPENAI_BASE_URL", "AZURE_OPENAI_ENDPOINT"}:
            server_env.pop(key, None)
    server_env.update(
        {
            "HOME": str(state_dir / "home"),
            "HERMES_HOME": str(state_dir),
            "HERMES_BASE_HOME": str(state_dir),
            "HERMES_CONFIG_PATH": str(state_dir / "config.yaml"),
            "HERMES_WEBUI_STATE_DIR": str(state_dir / "webui-state"),
            "HERMES_WEBUI_AGENT_DIR": str(AGENT_ROOT),
            "HERMES_WEBUI_HOST": "127.0.0.1",
            "HERMES_WEBUI_PORT": str(port),
            "HERMES_WEBUI_SKIP_ONBOARDING": "1",
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "TAIJI_WEBUI_TEST_NETWORK_BLOCK": "1",
        }
    )
    (state_dir / "home").mkdir(parents=True)
    (state_dir / "webui-state").mkdir(parents=True)
    config_path = state_dir / "config.yaml"
    config_path.write_text(
        "model:\n  provider: zai-cn\n  default: glm-5\n  base_url: "
        + DEEPSEEK_URL
        + "\n",
        encoding="utf-8",
    )
    before_disk = config_path.read_bytes()
    server_log = evidence_dir / "server.log"
    server_log_handle = server_log.open("w", encoding="utf-8")
    server = subprocess.Popen(
        [str(AGENT_PYTHON), str(ROOT / "server.py")],
        cwd=str(AGENT_ROOT),
        env=server_env,
        stdout=server_log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if server.poll() is not None:
                raise RuntimeError(f"isolated WebUI server exited: {server.returncode}")
            try:
                import urllib.request

                with urllib.request.urlopen(base_url + "/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("isolated WebUI server did not become healthy")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=playwright.chromium.executable_path,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            active_page = None
            active_viewport = None
            try:
                for viewport in ((1280, 900), (768, 900), (390, 844)):
                    active_viewport = viewport
                    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
                    page = context.new_page()
                    active_page = page
                    route_state = {
                        "post_count": 0,
                        "post_bodies": [],
                        "next_behavior": "fixed_cleanup",
                        "authoritative": _model_config_fixture(),
                        "blocked_urls": [],
                        "expected_console_counts": {"400": 0, "failed": 0},
                    }
                    page_errors: list[str] = []
                    console_events: list[dict[str, str]] = []
                    console_errors: list[dict[str, str]] = []
                    response_errors: list[dict[str, object]] = []
                    failed_requests: list[dict[str, str]] = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.on(
                        "console",
                        lambda message: (
                            console_events.append({
                                "type": message.type,
                                "text": message.text,
                                "url": str((message.location or {}).get("url") or ""),
                            }),
                            console_errors.append({
                                "text": message.text,
                                "url": str((message.location or {}).get("url") or ""),
                            }) if message.type == "error" else None,
                        ),
                    )
                    page.on(
                        "response",
                        lambda response: response_errors.append({"url": response.url, "status": response.status})
                        if response.status >= 400 else None,
                    )
                    page.on(
                        "requestfailed",
                        lambda request: failed_requests.append({"url": request.url, "failure": request.failure or ""}),
                    )

                    def handle_route(route):
                        request = route.request
                        url = request.url
                        if not url.startswith(base_url):
                            route_state["blocked_urls"].append(url)
                            route.abort()
                            return
                        path = url.split("/api/", 1)[-1] if "/api/" in url else ""
                        if request.method == "GET" and path.startswith("model-config"):
                            _write_json(route, _copy_json(route_state["authoritative"]))
                        elif request.method == "GET" and path.startswith("providers"):
                            _write_json(route, _providers_fixture())
                        elif request.method == "GET" and path.startswith("provider/quota"):
                            _write_json(route, {"ok": False, "provider": "zai-cn", "status": "unsupported", "quota": None})
                        elif request.method == "POST" and path.startswith("model-config/main"):
                            payload = json.loads(request.post_data or "{}")
                            route_state["post_count"] += 1
                            route_state["post_bodies"].append(payload)
                            base_url_value = str(payload.get("base_url") or "").strip()
                            if payload.get("provider") == "custom" and not base_url_value.startswith(("http://", "https://")):
                                route_state["expected_console_counts"]["400"] += 1
                                _write_json(route, {"error": "base_url must be a safe HTTP(S) URL", "error_code": "invalid_base_url", "field": "base_url"}, status=400)
                            elif route_state["next_behavior"] == "uncertain":
                                route_state["next_behavior"] = "invalid"
                                route_state["expected_console_counts"]["failed"] += 1
                                route.abort("failed")
                            else:
                                behavior = route_state["next_behavior"]
                                pending = behavior == "pending"
                                route_state["next_behavior"] = (
                                    "applied" if behavior == "fixed_cleanup" else
                                    "pending" if behavior == "applied" else
                                    "uncertain"
                                )
                                response = _saved_model_config(payload, pending, fixed_cleanup=behavior == "fixed_cleanup")
                                authoritative = _copy_json(response)
                                if behavior == "fixed_cleanup":
                                    response["endpoint_mutation"] = {"code": "fixed_override_cleaned"}
                                route_state["authoritative"] = authoritative
                                _write_json(route, response)
                        else:
                            route.continue_()

                    page.route("**/*", handle_route)
                    page.route_web_socket("**/*", lambda websocket: websocket.close(reason="blocked by isolated smoke"))
                    page.goto(base_url + "/", wait_until="domcontentloaded")
                    if viewport[0] <= 640:
                        page.locator("#btnHamburger").click()
                        sidebar = page.locator(".sidebar.mobile-open")
                        expect(sidebar).to_be_visible(timeout=5000)
                        settings_entry = sidebar.locator('.sidebar-nav [data-panel="settings"]:visible').first
                    else:
                        home_settings = page.locator('[data-taiji-panel="settings"]:visible')
                        settings_entry = home_settings.first if home_settings.count() else page.locator('[data-panel="settings"]:visible').first
                    settings_entry.click()

                    def select_settings_section(section: str) -> None:
                        sidebar = page.locator(".sidebar")
                        if viewport[0] <= 640:
                            if "mobile-open" not in (sidebar.get_attribute("class") or ""):
                                page.locator("#btnHamburger").click()
                            expect(sidebar).to_have_class(re.compile(r".*\bmobile-open\b.*"), timeout=5000)
                            sidebar.locator(f'[data-settings-section="{section}"]').click()
                            page.locator("#mobileOverlay").click(
                                position={"x": viewport[0] - 20, "y": viewport[1] // 2}
                            )
                            expect(sidebar).not_to_have_class(
                                re.compile(r"(?:^|\s)mobile-open(?:\s|$)"), timeout=5000
                            )
                        else:
                            page.locator("#settingsMenu").wait_for(state="visible", timeout=10000)
                            page.locator(f'#settingsMenu [data-settings-section="{section}"]').click()

                    select_settings_section("models")
                    page.locator("#modelConfigMainModelName").wait_for(state="visible", timeout=10000)
                    expect(page.locator("#modelConfigMainModelName")).to_have_text("glm-5", timeout=10000)
                    expect(page.locator("#modelConfigDraftStatus")).not_to_contain_text("未保存草稿")
                    page.locator("#btnReloadAllModelConfig").click()
                    expect(page.locator("#modelConfigDraftStatus")).to_contain_text("已刷新", timeout=10000)
                    expect(page.locator("#modelConfigMainEndpointValue")).to_have_text(BIGMODEL_URL, timeout=10000)
                    expect(page.locator("#modelConfigMainEndpointNotice")).to_contain_text("检测到旧版地址覆盖", timeout=5000)
                    assert DEEPSEEK_URL not in page.locator("#settingsPaneModels").inner_text()
                    page.locator("#modelConfigMainEndpointValue").scroll_into_view_if_needed()
                    page.screenshot(path=str(evidence_dir / f"main-zai-{viewport[0]}x{viewport[1]}.png"), full_page=True)

                    page.locator("#modelConfigMainEdit").wait_for(state="attached")
                    page.locator('[data-model-config-toggle="modelConfigMainEdit"]').click()
                    page.locator("#modelConfigProvider").select_option("zai-cn")
                    page.locator("#btnSaveMainModel").click()
                    page.wait_for_function(
                        "() => document.getElementById('modelConfigMainEffective').textContent === '已配置，尚未验证'",
                        timeout=10000,
                    )
                    assert "旧版地址覆盖已清理" in page.locator("#modelConfigMainEndpointNotice").inner_text()
                    assert route_state["post_count"] == 1
                    cleanup_payload = route_state["post_bodies"][0]
                    assert cleanup_payload["provider"] == "zai-cn"
                    assert re.fullmatch(r"[0-9a-f]{32}", cleanup_payload["request_id"])
                    assert "base_url" not in cleanup_payload
                    assert "api_key" not in cleanup_payload
                    page.locator("#btnReloadAllModelConfig").click()
                    expect(page.locator("#modelConfigMainEndpointNotice")).not_to_contain_text("旧版地址覆盖已清理", timeout=10000)
                    page.locator('[data-model-config-toggle="modelConfigMainEdit"]').click()
                    page.locator("#modelConfigProvider").select_option("custom")
                    preview = page.locator("#modelConfigEditEndpointPreview")
                    assert preview.get_attribute("hidden") is not None
                    assert page.locator("#modelConfigBaseUrl").is_visible()
                    page.locator("#modelConfigBaseUrl").fill("https://draft.example/v1")
                    page.locator("#modelConfigProvider").select_option("zai-cn")
                    assert page.locator("#modelConfigBaseUrl").input_value() == ""

                    page.locator("#modelConfigProvider").select_option("custom")
                    assert page.locator("#modelConfigBaseUrl").input_value() == ""
                    page.locator("#modelConfigBaseUrl").fill("https://saved.example/v1")
                    page.locator("#btnSaveMainModel").click()
                    page.wait_for_function(
                        "() => document.getElementById('modelConfigMainEffective').textContent === '已配置，尚未验证'",
                        timeout=10000,
                    )
                    saved_payload = route_state["post_bodies"][1]
                    assert saved_payload["provider"] == "custom"
                    assert saved_payload["base_url"] == "https://saved.example/v1"
                    assert re.fullmatch(r"[0-9a-f]{32}", saved_payload["request_id"])
                    assert "api_key" not in saved_payload
                    assert route_state["post_count"] == 2
                    assert page.locator("#modelConfigMainEdit").is_hidden()

                    page.locator('[data-model-config-toggle="modelConfigMainEdit"]').click()
                    page.locator("#modelConfigBaseUrl").fill("https://pending.example/v1")
                    page.locator("#btnSaveMainModel").click()
                    page.wait_for_function(
                        "() => document.getElementById('modelConfigMainEffective').textContent === '刷新中'",
                        timeout=10000,
                    )
                    assert route_state["post_count"] == 3
                    assert route_state["post_bodies"][2]["base_url"] == "https://pending.example/v1"
                    assert page.locator("#modelConfigMainEdit").is_visible()

                    page.locator("#modelConfigBaseUrl").fill("https://uncertain.example/v1")
                    page.locator("#btnSaveMainModel").click()
                    page.wait_for_function(
                        "() => document.getElementById('modelConfigMainEffective').textContent === '保存失败'",
                        timeout=10000,
                    )
                    assert route_state["post_count"] == 4
                    assert page.locator("#modelConfigMainEffective").inner_text() == "保存失败"

                    page.locator("#modelConfigBaseUrl").fill("not-a-url")
                    page.locator("#btnSaveMainModel").click()
                    expect(page.locator("#modelConfigBaseUrlError")).to_contain_text("safe HTTP(S)", timeout=5000)
                    assert page.locator("#modelConfigBaseUrl").input_value() == "not-a-url"
                    assert page.locator("#modelConfigBaseUrl").evaluate("el => el === document.activeElement")
                    assert route_state["post_count"] == 5
                    assert config_path.read_bytes() == before_disk
                    assert page.locator("#modelConfigMainEffective").inner_text() == "保存失败"
                    page.screenshot(path=str(evidence_dir / f"invalid-custom-{viewport[0]}x{viewport[1]}.png"), full_page=True)

                    select_settings_section("providers")
                    assert page.locator('.provider-card[data-provider="custom"]').count() == 0
                    provider_console_start = len(console_errors)
                    for provider_id in ("zai-cn", "deepseek", "runtime-managed", "missing-provider", "custom:acme", "qwen-oauth"):
                        header = page.locator(f'.provider-card[data-provider="{provider_id}"] .provider-card-header')
                        header.wait_for(state="visible", timeout=10000)
                        assert header.get_attribute("aria-controls")
                        header.focus()
                        header.press("Enter")
                        assert header.get_attribute("aria-expanded") == "true"
                        header.press("Space")
                        assert header.get_attribute("aria-expanded") == "false"
                        header.click()
                        assert header.get_attribute("aria-expanded") == "true"

                    deepseek_card = page.locator('.provider-card[data-provider="deepseek"]')
                    assert DEEPSEEK_URL in deepseek_card.inner_text()
                    zai_card = page.locator('.provider-card[data-provider="zai-cn"]')
                    assert BIGMODEL_URL in zai_card.inner_text()
                    assert zai_card.locator(".provider-card-endpoint-value").count() == 1
                    assert "管理员配置" in deepseek_card.inner_text()
                    qwen_card = page.locator('.provider-card[data-provider="qwen-oauth"]')
                    assert "运行时分配" in qwen_card.inner_text()
                    runtime_card = page.locator('.provider-card[data-provider="runtime-managed"]')
                    assert "请求时确定" in runtime_card.inner_text()
                    assert "尚未配置" in page.locator('.provider-card[data-provider="missing-provider"] .provider-card-endpoint').inner_text()
                    zai_card.locator(".provider-card-endpoint").scroll_into_view_if_needed()
                    page.screenshot(path=str(evidence_dir / f"providers-endpoints-{viewport[0]}x{viewport[1]}.png"), full_page=True)

                    assert not [event for event in console_errors[provider_console_start:] if event["text"]], console_errors[provider_console_start:]

                    select_settings_section("models")
                    assert not page.locator("body").inner_text().count(FAKE_SECRET_MARKER)
                    assert not page.locator("body").inner_text().count("Authorization")
                    page.screenshot(path=str(evidence_dir / f"endpoint-authority-{viewport[0]}x{viewport[1]}.png"), full_page=True)
                    assert not page.locator("body").evaluate("el => el.scrollWidth > window.innerWidth")
                    assert not page_errors, page_errors
                    main_post_url = base_url + "/api/model-config/main"
                    expected_400 = sum(
                        1
                        for event in response_errors
                        if event["status"] == 400 and event["url"] == main_post_url
                    )
                    expected_failed = sum(
                        1
                        for event in failed_requests
                        if event["url"] == main_post_url and event["failure"] == "net::ERR_FAILED"
                    )
                    assert expected_400 == route_state["expected_console_counts"]["400"]
                    assert expected_failed == route_state["expected_console_counts"]["failed"]
                    unexpected_console = []
                    consumed_400 = 0
                    consumed_failed = 0
                    for event in console_errors:
                        event_text = event["text"]
                        if (
                            event["url"] == main_post_url
                            and "400 (Bad Request)" in event_text
                            and consumed_400 < expected_400
                        ):
                            consumed_400 += 1
                            continue
                        if (
                            event["url"] == main_post_url
                            and "ERR_FAILED" in event_text
                            and consumed_failed < expected_failed
                        ):
                            consumed_failed += 1
                            continue
                        unexpected_console.append(event)
                    assert consumed_400 == expected_400
                    assert consumed_failed == expected_failed
                    assert not unexpected_console, unexpected_console
                    (evidence_dir / f"network-events-{viewport[0]}x{viewport[1]}.json").write_text(
                        json.dumps(
                            {
                                "console": console_events,
                                "page_errors": page_errors,
                                "responses_ge_400": response_errors,
                                "request_failures": failed_requests,
                                "blocked_external_urls": route_state["blocked_urls"],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    context.close()
            except Exception:
                if active_page is not None:
                    try:
                        active_page.screenshot(path=str(evidence_dir / f"failure-{active_viewport[0]}x{active_viewport[1]}.png"), full_page=True)
                        dom_summary = active_page.evaluate(
                            "() => ({url: location.href, body: (document.body && document.body.innerText || '').slice(0, 1200), model: document.getElementById('modelConfigMainModelName')?.textContent || '', endpoint: document.getElementById('modelConfigMainEndpointValue')?.textContent || '', effective: document.getElementById('modelConfigMainEffective')?.textContent || ''})"
                        )
                        dom_summary["page_errors"] = page_errors
                        dom_summary["console_events"] = console_events
                        dom_summary["console_errors"] = console_errors
                        dom_summary["responses_ge_400"] = response_errors
                        dom_summary["request_failures"] = failed_requests
                        dom_summary["blocked_external_urls"] = route_state["blocked_urls"]
                    except Exception as capture_error:
                        dom_summary = {"capture_error": str(capture_error)}
                else:
                    dom_summary = {"capture_error": "no active page"}
                (evidence_dir / "failure-traceback.log").write_text(
                    traceback.format_exc() + "\nDOM_SUMMARY=" + json.dumps(dom_summary, ensure_ascii=False),
                    encoding="utf-8",
                )
                raise
            finally:
                browser.close()
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        server_log_handle.close()
        shutil.rmtree(state_dir, ignore_errors=True)
        print(f"browser_evidence_dir={evidence_dir}")


FAKE_SECRET_MARKER = "task9-fake-"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        print(f"C-class prerequisite missing: Playwright is unavailable in {sys.executable}: {exc}")
        return 2
    try:
        _run_smoke()
    except Exception as exc:
        print(f"browser smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
