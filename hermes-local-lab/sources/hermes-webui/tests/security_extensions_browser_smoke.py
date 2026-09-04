"""Real UI security editing; static source, mock APIs, no user configuration."""
import copy
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

    evidence = Path(tempfile.mkdtemp(prefix="taiji-security-browser-"))
    print(f"evidence: {evidence}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for width in (1280, 768, 390):
                    context = browser.new_context(viewport={"width": width, "height": 900})
                    page = context.new_page()
                    extensions = {"unapproved_skill_scripts": False, "delegate_task": False}
                    state = {"profile": "strict", "configured": {"profile": "strict", "capabilities": extensions.copy()},
                             "fail": False, "hold": False, "readonly": False}
                    writes, held, errors = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def status():
                        current = {"profile": state["profile"], "capabilities": extensions.copy()}
                        caps = {key: {"allowed": value, "enabled": value} for key, value in extensions.items()}
                        caps.update({"terminal": {"allowed": False, "approval_required": True},
                                     "execute_code": {"allowed": False, "approval_required": True},
                                     "document_read": {"allowed": True}})
                        return {"mode": "restricted", "profile": state["profile"], "configured": copy.deepcopy(state["configured"]),
                                "restart_required": current != state["configured"], "capabilities": caps,
                                "desktop_profile_write_enabled": not state["readonly"]}

                    def respond(route, data, code=200):
                        route.fulfill(status=code, content_type="application/json", body=json.dumps(data))

                    def route_request(route):
                        path = urlsplit(route.request.url).path
                        if urlsplit(route.request.url).netloc != urlsplit(origin).netloc:
                            route.abort()
                        elif path == "/sw.js":
                            route.fulfill(content_type="application/javascript", body="// isolated")
                        elif path == "/api/security/status":
                            respond(route, status())
                        elif path == "/api/security/profile":
                            body = route.request.post_data_json
                            writes.append(body)
                            if state["fail"]:
                                respond(route, {"error": "test save failure"}, 503)
                                return
                            state["configured"] = copy.deepcopy(body)
                            data = {"ok": True, "profile": body["profile"], "restart_required": status()["restart_required"], "status": status()}
                            if state["hold"]:
                                held.append((route, data))
                            else:
                                respond(route, data)
                        elif path == "/api/onboarding/status":
                            respond(route, {"completed": True, "settings": {}, "system": {}, "workspaces": {"items": []}, "setup": {"current": {}, "providers": []}})
                        elif path.startswith("/api/"):
                            respond(route, {})
                        else:
                            route.continue_()

                    page.route("**/*", route_request)
                    page.route_web_socket("**/*", lambda ws: ws.close())
                    page.goto(origin, wait_until="load")
                    page.evaluate("() => openSecuritySettings()")
                    script = page.locator("#settingsSecurityScripts")
                    delegate = page.locator("#settingsSecurityDelegate")
                    save = page.locator("#settingsSecurityProfileSave")
                    info = page.locator("#settingsSecurityStatus")
                    expect(script).to_be_visible()
                    expect(script).not_to_be_checked()
                    script.check()
                    page.evaluate("() => refreshSecurityStatus(true)")
                    expect(script).to_be_checked()
                    save.click()
                    expect(page.locator("#appDialogCancel")).to_be_focused()
                    page.keyboard.press("Escape")
                    expect(save).to_be_enabled()
                    assert writes == []
                    expect(script).to_be_checked()
                    state["fail"] = True
                    save.click()
                    page.locator("#appDialogConfirm").click()
                    expect(info).to_contain_text("失败")
                    expect(script).to_be_checked()
                    state["fail"] = False
                    state["hold"] = True
                    save.click()
                    page.locator("#appDialogConfirm").click()
                    expect(save).to_be_disabled()
                    page.evaluate("() => saveSecurityProfile()")
                    assert len(writes) == 2
                    for route, data in held:
                        respond(route, data)
                    held.clear()
                    state["hold"] = False
                    expect(info).to_contain_text("重新打开")
                    expect(page.locator('[data-security-capability="unapproved_skill_scripts"]')).to_contain_text("未开启")
                    page.evaluate("() => refreshSecurityStatus(true)")
                    expect(script).to_be_checked()
                    extensions.update(state["configured"]["capabilities"])
                    page.evaluate("() => refreshSecurityStatus(true)")
                    expect(page.locator('[data-security-capability="unapproved_skill_scripts"]')).to_contain_text("可用")
                    delegate.focus()
                    page.keyboard.press("Space")
                    expect(delegate).to_be_checked()
                    page.screenshot(path=str(evidence / f"security-{width}.png"), full_page=True)
                    state["readonly"] = True
                    page.evaluate("() => refreshSecurityStatus(true)")
                    expect(save).to_be_disabled()
                    expect(delegate).to_be_disabled()
                    assert not errors, errors
                    results.append({"width": width, "writes": len(writes), "passed": True})
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (evidence / "result.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
