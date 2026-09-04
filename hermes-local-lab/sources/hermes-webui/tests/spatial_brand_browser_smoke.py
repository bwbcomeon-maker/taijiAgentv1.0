"""Desktop brand presentation with isolated static assets and mock APIs."""
import json
import tempfile
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from model_config_refresh_browser_smoke import ROOT, StaticHandler


def main():
    from playwright.sync_api import expect, sync_playwright

    evidence = Path(tempfile.mkdtemp(prefix="spatial-brand-browser-"))
    print(f"evidence: {evidence}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def route_request(route):
                    url = urlsplit(route.request.url)
                    if url.netloc != urlsplit(origin).netloc:
                        route.abort()
                    elif url.path == "/sw.js":
                        route.fulfill(content_type="application/javascript", body="// isolated")
                    elif url.path.startswith("/api/"):
                        payload = {}
                        if url.path == "/api/settings":
                            payload = {"bot_name": "taiji Agent"}
                        elif url.path == "/api/onboarding/status":
                            payload = {"completed": True, "settings": {}, "system": {}, "workspaces": {"items": []}, "setup": {"current": {}, "providers": []}}
                        elif url.path == "/api/about":
                            payload = {"description": "乾元版 v0.1.7743 © 太极计算机股份有限公司，版权所有。"}
                        route.fulfill(content_type="application/json", body=json.dumps(payload))
                    else:
                        route.continue_()

                page.route("**/*", route_request)
                page.route_web_socket("**/*", lambda ws: ws.close())
                page.goto(origin, wait_until="load")
                expect(page.locator(".taiji-brand-title")).to_have_text("国网空天智能体")
                expect(page.locator(".taiji-brand-subtitle")).to_have_text("空间数据运检智能体")
                for name in ("", "taiji Agent", "taijiAgent", "TAIJI AGENT", "Hermes"):
                    assert page.evaluate("name => productDisplayName(name)", name) == "Agent"
                assert page.evaluate("productDisplayName('运检专家')") == "运检专家"
                page.evaluate("""() => {
                    document.documentElement.dataset.taijiDesktop='1';
                    S.activeProfile='default';window._botName='taiji Agent';applyBotName();
                    S.session={session_id:'brand-smoke',title:'展示验证'};
                    S.messages=[{role:'user',content:'你好'}, {role:'assistant',content:'历史正文 taiji Agent 保持原样。'}];
                    renderMessages();
                }""")
                expect(page.locator("#msg")).to_have_attribute("placeholder", "输入消息给 Agent…")
                expect(page.locator(".msg-role-name").filter(has_text="Agent").first).to_have_text("Agent")
                expect(page.locator(".msg-role-name").filter(has_text="Agent").first).to_be_visible()
                assert "历史正文 taiji Agent 保持原样。" in page.locator("#messages").inner_text()
                page.screenshot(path=str(evidence / "chat-desktop.png"), full_page=True)
                page.evaluate("() => openSecuritySettings()")
                expect(page.locator("#settingsPaneSystem")).to_be_visible()
                assert page.locator("#checkUpdatesBlock, #settings-webui-version-badge, #settings-agent-version-badge").count() == 0
                assert page.locator("#settingsPaneSystem").evaluate("el => el.firstElementChild.id === 'productDiagnosticsCard'")
                expect(page.locator("#btnRefreshProductDiagnostics")).to_be_visible()
                expect(page.locator("#btnExportProductDiagnostics")).to_be_visible()
                page.screenshot(path=str(evidence / "system-desktop.png"), full_page=True)
                page.evaluate("() => switchSettingsSection('about')")
                expect(page.locator(".settings-about-product-name")).to_be_visible()
                expect(page.locator(".settings-about-product-name")).to_have_text("国网空天智能体")
                expect(page.locator(".settings-about-product-detail")).to_contain_text("空间数据运检智能体")
                expect(page.locator(".settings-about-edition")).to_have_text("桌面版")
                for selector in (".taiji-brand-title", ".taiji-brand-subtitle", ".settings-about-product-name", ".settings-about-product-detail"):
                    assert page.locator(selector).evaluate("el => el.scrollWidth <= el.clientWidth + 1"), selector
                page.screenshot(path=str(evidence / "about-desktop.png"), full_page=True)
                assert not errors, errors
                print("PASS desktop brand, legacy names, chat role, composer, preserved body, about, overflow", flush=True)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
