"""Real DOM refresh regression; static assets only, all APIs mocked, no credentials."""

import json
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from provider_endpoint_authority_browser_smoke import _model_config_fixture

ROOT = Path(__file__).resolve().parents[1]


class StaticHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            html = (ROOT / "static/index.html").read_text()
            html = html.replace("__MAX_UPLOAD_BYTES__", str(20 * 1024 * 1024)).replace("__CSRF_TOKEN_JSON__", '""').replace("__WEBUI_VERSION__", "refresh-smoke")
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *_args):
        pass


def main():
    from playwright.sync_api import expect, sync_playwright

    evidence = Path(tempfile.mkdtemp(prefix="taiji-model-refresh-browser-"))
    print(f"evidence: {evidence}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticHandler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for width, height in ((1280, 900), (768, 900), (390, 844)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors, writes, blocked, pending = [], [], [], []
                    state = {"config": _model_config_fixture(), "fail": False, "hold_image": False, "reads": 0}
                    # An unconfigured capability can render a default choice without
                    # the user ever editing it. It must not become a main-page draft.
                    state["config"]["vision_providers"] = [{"id": "alibaba", "name": "阿里云", "default_model": "qwen-vl-plus"}]
                    state["config"]["image_gen_providers"] = [{"id": "dashscope", "name": "阿里云", "default_model": "wanx-v1"}]
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def respond(route, data, status=200):
                        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

                    def route_request(route):
                        req = route.request
                        if urlsplit(req.url).netloc != urlsplit(origin).netloc:
                            blocked.append(req.url)
                            route.abort()
                            return
                        path = urlsplit(req.url).path
                        if path == "/sw.js":
                            route.fulfill(content_type="application/javascript", body="// No service worker in this isolated smoke.")
                            return
                        if not path.startswith("/api/"):
                            route.continue_()
                            return
                        if req.method != "GET":
                            writes.append(path)
                        if path == "/api/model-config":
                            state["reads"] += 1
                            respond(route, {"error": "fixture unavailable"} if state["fail"] else state["config"], 503 if state["fail"] else 200)
                        elif path == "/api/onboarding/status":
                            respond(route, {"completed": True, "settings": {}, "system": {}, "workspaces": {"items": []}, "setup": {"current": {}, "providers": []}})
                        elif path == "/api/providers":
                            respond(route, {"providers": state["config"]["providers"]})
                        elif path == "/api/image-capabilities" and state["hold_image"]:
                            pending.append(route)
                        elif path == "/api/provider/quota":
                            respond(route, {"status": "unsupported", "quota": None})
                        else:
                            respond(route, {})

                    page.route("**/*", route_request)
                    page.route_web_socket("**/*", lambda ws: ws.close())
                    page.goto(origin, wait_until="load")
                    # Set up the same providers-before-models order as Settings.
                    page.evaluate("async () => { await loadProvidersPanel(); switchSettingsSection('models'); }")
                    if width <= 640:
                        page.locator("#btnHamburger").click()
                    page.locator('[data-taiji-panel="settings"]:visible, [data-panel="settings"]:visible').first.click()
                    if width <= 640:
                        page.locator("#mobileOverlay").click(position={"x": width - 20, "y": height // 2})
                    page.evaluate("() => switchSettingsSection('models')")
                    expect(page.locator("#modelConfigMainModelName")).to_have_text("glm-5")
                    page.screenshot(path=str(evidence / f"initial-{width}.png"), full_page=True)
                    expect(page.locator("#modelConfigDraftStatus")).not_to_contain_text("未保存草稿")
                    assert page.evaluate("() => _modelConfigHasUnsavedChanges()") is False, page.evaluate("() => ({main:_modelConfigMainHasUnsavedChanges(),vision:_visionConfigHasUnsavedChanges(),image:_imageGenConfigHasUnsavedChanges()})")
                    button = page.get_by_role("button", name="刷新本机模型配置状态", exact=True)
                    button.scroll_into_view_if_needed()
                    expect(button).to_be_visible()
                    button.click()
                    expect(page.locator("#modelConfigDraftStatus")).to_contain_text("已刷新")
                    expect(page.locator("#appDialogConfirm")).not_to_be_visible()

                    # Slow image state cannot disable or intercept main refresh.
                    state["hold_image"] = True
                    page.evaluate("() => { void loadImageCapabilityCenter(true); }")
                    expect(page.locator("#imageCapabilityCenter")).to_have_attribute("aria-busy", "true")
                    before = state["reads"]
                    button.click()
                    expect(page.locator("#modelConfigDraftStatus")).to_contain_text("图片能力操作尚未结束")
                    assert state["reads"] == before + 1
                    expect(button).to_be_enabled()
                    page.screenshot(path=str(evidence / f"image-busy-{width}.png"), full_page=True)
                    state["hold_image"] = False
                    for route in pending:
                        respond(route, {})
                    pending.clear()
                    expect(page.locator("#imageCapabilityCenter")).to_have_attribute("aria-busy", "false")

                    # HTTP failure is visible; keyboard retry works without POST.
                    state["fail"] = True
                    button.click()
                    expect(page.locator("#modelConfigDraftStatus")).to_contain_text("失败")
                    expect(button).to_be_enabled()
                    page.screenshot(path=str(evidence / f"failure-{width}.png"), full_page=True)
                    state["fail"] = False
                    button.focus()
                    button.press("Enter")
                    expect(page.locator("#modelConfigDraftStatus")).to_contain_text("已刷新")

                    # Real edits survive navigation and cancel; discard is explicit.
                    page.locator('[data-model-config-toggle="modelConfigMainEdit"]').click()
                    page.locator("#modelConfigModel").fill("draft-model")
                    page.locator("#modelConfigApiKey").fill("fixture-unsaved-value")
                    page.evaluate("async () => { await loadProvidersPanel(); await loadModelConfigPanel(); }")
                    expect(page.locator("#modelConfigModel")).to_have_value("draft-model")
                    button.click()
                    expect(page.locator("#appDialogCancel")).to_be_focused()
                    page.locator("#appDialogCancel").click()
                    expect(page.locator("#modelConfigDraftStatus")).to_contain_text("已取消")
                    expect(page.locator("#modelConfigApiKey")).to_have_value("fixture-unsaved-value")
                    button.click()
                    page.locator("#appDialogConfirm").click()
                    expect(page.locator("#modelConfigDraftStatus")).to_contain_text("已刷新")
                    expect(page.locator("#modelConfigModel")).to_have_value("glm-5")
                    expect(page.locator("#modelConfigApiKey")).to_have_value("")
                    button.scroll_into_view_if_needed()
                    page.screenshot(path=str(evidence / f"success-{width}.png"), full_page=True)
                    assert not errors, errors
                    assert not [path for path in writes if path != "/api/auth/passkeys"], writes
                    assert not blocked, blocked
                    results.append({"viewport": [width, height], "main_reads": state["reads"], "page_errors": errors, "writes": writes})
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(json.dumps({"status": "PASS", "evidence": str(evidence), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
