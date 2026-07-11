#!/usr/bin/env node
/*
 * Product diagnostics acceptance smoke for a real Taiji Electron BrowserWindow.
 * A normal Web page is deliberately rejected and mobile viewports are out of
 * scope. The test covers the 1120x720 minimum and a 1440x900 desktop window.
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  return require(process.env.PLAYWRIGHT_NODE_PATH || "playwright");
}

const cdpEndpoint = process.env.TAIJI_DESKTOP_CDP || "http://127.0.0.1:9233";
const evidenceDir = process.env.TAIJI_DESKTOP_EVIDENCE_DIR || "";

function assertState(condition, message, detail) {
  assert.ok(condition, `${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
}

function mark(step) {
  process.stderr.write(`[product-diagnostics-electron] ${step}\n`);
}

function isExpectedDesktopHttpFailure(entry, appOrigin) {
  if (!entry || !(entry.status === 404 && entry.method === "GET")) return false;
  try {
    const url = new URL(entry.url);
    return (
      url.origin === new URL(appOrigin).origin
      && url.pathname === "/api/expert-teams/run"
      && Boolean(url.searchParams.get("session_id")?.trim())
    );
  } catch (_error) {
    return false;
  }
}

function isExpectedBackgroundConsoleError(entry, appOrigin) {
  if (!entry || entry.type !== "error") return false;
  if (entry.text !== "console: Failed to load resource: the server responded with a status of 404 (Not Found)") return false;
  try {
    const url = new URL(entry.url);
    return (
      url.origin === new URL(appOrigin).origin
      && url.pathname === "/api/expert-teams/run"
      && Boolean(url.searchParams.get("session_id")?.trim())
    );
  } catch (_error) {
    return false;
  }
}

async function waitForDesktopReady(page) {
  await page.waitForFunction(() => (
    document.readyState === "complete"
    && typeof switchPanel === "function"
    && typeof switchSettingsSection === "function"
    && typeof loadProductDiagnostics === "function"
    && document.getElementById("productDiagnosticsCard")
  ));
  const onboardingStatus = await page.evaluate(async () => {
    const response = await fetch("/api/onboarding/status", { credentials: "include" });
    return response.ok ? await response.json() : { completed: true };
  });
  const onboarding = page.locator("#onboardingOverlay");
  if (!onboardingStatus.completed) {
    await onboarding.waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await onboarding.waitFor({ state: "hidden" });
  }
}

async function waitForVisualStability(page) {
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  await page.waitForTimeout(250);
}

async function openDiagnosticsByKeyboard(page) {
  const settings = page.locator('.taiji-nav-item[data-taiji-panel="settings"]');
  await settings.waitFor({ state: "visible" });
  await settings.focus();
  await page.keyboard.press("Enter");
  const system = page.locator('#settingsMenu [data-settings-section="system"]');
  await system.waitFor({ state: "visible" });
  await system.focus();
  await page.keyboard.press("Enter");
  await page.locator("#productDiagnosticsCard").waitFor({ state: "visible" });
  await page.waitForFunction(() => (
    document.querySelectorAll("#productDiagnosticsComponents .product-diagnostics-component").length === 7
    && document.getElementById("productDiagnosticsStatus")?.dataset.status !== "loading"
  ));
}

async function snapshotLayout(page) {
  return page.evaluate(() => {
    const card = document.getElementById("productDiagnosticsCard");
    const rect = card.getBoundingClientRect();
    const systemTitle = document.querySelector("#settingsPaneSystem .settings-section-title");
    const systemTitleRect = systemTitle.getBoundingClientRect();
    const versionBadges = Array.from(document.querySelectorAll("#settingsPaneSystem #checkUpdatesBlock .settings-version-badge")).map((badge) => {
      const badgeRect = badge.getBoundingClientRect();
      return {
        text: badge.textContent.trim(),
        left: badgeRect.left,
        right: badgeRect.right,
        width: badgeRect.width,
        fullyRendered: badge.scrollWidth <= badge.clientWidth && badge.scrollHeight <= badge.clientHeight,
      };
    });
    const buttons = Array.from(card.querySelectorAll("button")).map((button) => {
      const buttonRect = button.getBoundingClientRect();
      return {
        id: button.id,
        text: button.textContent.trim(),
        width: buttonRect.width,
        height: buttonRect.height,
        visible: buttonRect.width > 0 && buttonRect.height > 0 && buttonRect.top >= 0 && buttonRect.bottom <= innerHeight,
      };
    });
    const scrollOwners = Array.from(document.querySelectorAll("#mainSettings, #mainSettings *")).filter((node) => {
      const style = getComputedStyle(node);
      return ["auto", "scroll"].includes(style.overflowY) && node.scrollHeight > node.clientHeight;
    }).map((node) => node.id || node.className || node.tagName);
    return {
      viewport: [innerWidth, innerHeight],
      systemHeader: {
        title: systemTitle.textContent.trim(),
        titleWidth: systemTitleRect.width,
        titleHeight: systemTitleRect.height,
        versionBadges,
      },
      card: { top: rect.top, left: rect.left, right: rect.right, bottom: rect.bottom, width: rect.width },
      cardVisible: rect.top >= 0 && rect.left >= 0 && rect.right <= innerWidth && rect.top < innerHeight,
      documentOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      scrollOwners,
      statusRole: document.getElementById("productDiagnosticsStatus")?.getAttribute("role"),
      statusLive: document.getElementById("productDiagnosticsStatus")?.getAttribute("aria-live"),
      componentCount: card.querySelectorAll(".product-diagnostics-component").length,
      buttons,
    };
  });
}

async function fetchDiagnostics(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/product/diagnostics", { credentials: "include" });
    return { ok: response.ok, status: response.status, payload: await response.json() };
  });
}

function assertPublicPayload(payload) {
  assert.strictEqual(payload.schema, "taiji.product.diagnostics.v1");
  assert.ok(["ready", "degraded", "blocked"].includes(payload.overall));
  assert.deepStrictEqual(payload.components.map((item) => item.id), [
    "webui", "agent", "gateway", "license", "docx", "skills", "node",
  ]);
  payload.components.forEach((item) => {
    Object.keys(item).forEach((key) => assert.ok(["id", "label", "status", "version"].includes(key)));
  });
  const rendered = JSON.stringify(payload);
  ["Hermes", "/Users/", "127.0.0.1", "TAIJI_RUNTIME_HOME", "traceback", "password", "token"].forEach((forbidden) => {
    assert.ok(!rendered.toLowerCase().includes(forbidden.toLowerCase()), `forbidden diagnostic text: ${forbidden}`);
  });
}

async function exportBundleByKeyboard(page) {
  mark("export: focus trigger");
  const exportButton = page.locator("#btnExportProductDiagnostics");
  await exportButton.focus();
  await page.keyboard.press("Enter");
  await page.locator("#appDialogOverlay").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.activeElement?.id === "appDialogCancel");
  mark("export: preview opened");
  assert.strictEqual(await page.evaluate(() => document.activeElement?.id), "appDialogCancel");
  const preview = await page.locator("#appDialogDesc").innerText();
  assert.ok(preview.includes("不包含日志、本地路径、环境变量或密钥"));

  // Cancellation is part of the user path and must restore the trigger focus.
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => (
    getComputedStyle(document.getElementById("appDialogOverlay")).display === "none"
    && document.activeElement?.id === "btnExportProductDiagnostics"
  ));
  mark("export: cancellation restored focus");

  await page.evaluate(() => {
    window.__productDiagnosticsOriginalDownload = window._downloadJsonFile;
    window.__productDiagnosticsExportCapture = null;
    window._downloadJsonFile = (filename, data) => {
      window.__productDiagnosticsExportCapture = { filename, data };
      return window.__productDiagnosticsOriginalDownload(filename, data);
    };
  });
  await page.keyboard.press("Enter");
  await page.locator("#appDialogOverlay").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.activeElement?.id === "appDialogCancel");
  mark("export: preview reopened");
  await page.keyboard.press("Tab");
  assert.strictEqual(await page.evaluate(() => document.activeElement?.id), "appDialogConfirm");
  const downloadPromise = page.waitForEvent("download", { timeout: 5000 }).catch(() => null);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.__productDiagnosticsExportCapture !== null);
  mark("export: bundle reached desktop download function");
  const download = await downloadPromise;
  const capture = await page.evaluate(() => window.__productDiagnosticsExportCapture);
  assertState(capture && capture.filename && capture.data, "Desktop App did not invoke its support-bundle download", capture);
  const raw = JSON.stringify(capture.data);
  assert.ok(Buffer.byteLength(raw, "utf8") < 64 * 1024);
  const bundle = JSON.parse(raw);
  assert.deepStrictEqual(bundle.manifest, {
    redacted: true,
    logs_included: false,
    paths_included: false,
    secrets_included: false,
  });
  assertPublicPayload(bundle.diagnostics);
  await page.evaluate(() => {
    window._downloadJsonFile = window.__productDiagnosticsOriginalDownload;
    delete window.__productDiagnosticsOriginalDownload;
    delete window.__productDiagnosticsExportCapture;
  });
  return {
    suggestedFilename: download ? download.suggestedFilename() : capture.filename,
    bytes: Buffer.byteLength(raw, "utf8"),
    downloadEventObserved: Boolean(download),
    bundle,
  };
}

async function run() {
  mark("connect real Electron");
  const { chromium } = loadPlaywright();
  const browser = await chromium.connectOverCDP(cdpEndpoint);
  const pages = browser.contexts().flatMap((context) => context.pages());
  const page = pages.find((candidate) => candidate.url().includes("taiji_desktop=1"));
  assertState(page, "No real Taiji Electron BrowserWindow was found", pages.map((item) => item.url()));
  assertState(page.url().includes("taiji_desktop_token="), "Desktop token missing; refusing Web acceptance", page.url());
  const appOrigin = new URL(page.url()).origin;

  const jsErrors = [];
  const expectedFixtureConsoleErrors = [];
  const expectedBackgroundConsoleErrors = [];
  const httpFailures = [];
  let injectingExpectedDiagnostics503 = false;
  let injectingExpectedSecurity403 = false;
  page.on("pageerror", (error) => jsErrors.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      httpFailures.push({
        status: response.status(),
        method: response.request().method(),
        url: response.url().replace(/taiji_desktop_token=[^&]+/, "taiji_desktop_token=<redacted>"),
      });
    }
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const rendered = `console: ${message.text()}`;
    const consoleEntry = {
      type: message.type(),
      text: rendered,
      url: message.location().url,
    };
    if (
      (injectingExpectedDiagnostics503 && /503|Service Unavailable/i.test(rendered))
      || (injectingExpectedSecurity403 && /403|Forbidden/i.test(rendered))
    ) expectedFixtureConsoleErrors.push(rendered);
    else if (isExpectedBackgroundConsoleError(consoleEntry, appOrigin)) expectedBackgroundConsoleErrors.push(consoleEntry);
    else jsErrors.push(rendered);
  });

  const originalSize = await page.evaluate(() => ({ width: outerWidth, height: outerHeight }));
  const results = { appUrl: page.url().replace(/taiji_desktop_token=[^&]+/, "taiji_desktop_token=<redacted>") };
  try {
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForDesktopReady(page);
    await page.evaluate(() => window.resizeTo(1120, 720));
    await page.waitForFunction(() => innerWidth === 1120 && innerHeight === 720);
    await openDiagnosticsByKeyboard(page);
    mark("opened Settings > System by keyboard");

    const diagnostics = await fetchDiagnostics(page);
    assertState(diagnostics.ok && diagnostics.status === 200, "Diagnostics endpoint failed inside App", diagnostics);
    assertPublicPayload(diagnostics.payload);
    const minimumLayout = await snapshotLayout(page);
    assertState(minimumLayout.cardVisible, "Diagnostics card is not visible at the App minimum size", minimumLayout);
    assertState(!minimumLayout.documentOverflowX, "Diagnostics page overflows horizontally at 1120x720", minimumLayout);
    assertState(minimumLayout.scrollOwners.length <= 1, "Settings has nested vertical scroll owners at 1120x720", minimumLayout);
    assertState(
      minimumLayout.systemHeader.titleWidth >= 48 && minimumLayout.systemHeader.titleHeight <= 40,
      "System title is squeezed into a vertical column at 1120x720",
      minimumLayout,
    );
    minimumLayout.systemHeader.versionBadges.forEach((badge) => {
      assertState(badge.left >= 0 && badge.right <= 1120 && badge.fullyRendered, "System version badge is clipped at 1120x720", badge);
    });
    assert.strictEqual(minimumLayout.componentCount, 7);
    assert.strictEqual(minimumLayout.statusRole, "status");
    assert.strictEqual(minimumLayout.statusLive, "polite");
    minimumLayout.buttons.forEach((button) => {
      assertState(button.visible && button.height >= 34 && button.text.length > 0, "Diagnostics action is not accessible", button);
    });
    mark("minimum desktop layout passed");

    const refresh = page.locator("#btnRefreshProductDiagnostics");
    const diagnosticsFailureRoute = async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          schema: "taiji.product.error.v1",
          code: "diagnostics_unavailable",
          title: "attacker title must not render",
          message: "/Users/alice sentinel-secret must not render",
          recovery_actions: [
            { id: "retry", label: "attacker retry label" },
            { id: "restart_app", label: "attacker restart label" },
          ],
          incident_id: "inc-0123456789ab",
          retryable: true,
        }),
      });
    };
    await page.route("**/api/product/diagnostics", diagnosticsFailureRoute);
    injectingExpectedDiagnostics503 = true;
    await refresh.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !document.getElementById("btnRefreshProductDiagnostics").disabled);
    const errorState = await page.evaluate(() => ({
      status: document.getElementById("productDiagnosticsStatus")?.textContent.trim(),
      incident: document.getElementById("productDiagnosticsIncidentId")?.textContent.trim(),
      copyDisabled: document.getElementById("btnCopyProductDiagnosticsIncident")?.disabled,
      errorText: document.getElementById("productDiagnosticsComponents")?.textContent.replace(/\s+/g, " ").trim(),
      recoveryText: document.getElementById("productDiagnosticsRecovery")?.textContent.replace(/\s+/g, " ").trim(),
    }));
    assert.strictEqual(errorState.status, "检查失败");
    assert.strictEqual(errorState.incident, "inc-0123456789ab");
    assert.strictEqual(errorState.copyDisabled, false);
    assert.ok(errorState.errorText.includes("安全诊断暂不可用"));
    assert.ok(errorState.errorText.includes("暂时无法生成安全诊断，请稍后重试。"));
    assert.ok(errorState.recoveryText.includes("重试"));
    assert.ok(errorState.recoveryText.includes("关闭并重新打开桌面 App"));
    assert.ok(!JSON.stringify(errorState).includes("attacker"));
    assert.ok(!JSON.stringify(errorState).includes("/Users/"));
    if (evidenceDir) {
      fs.mkdirSync(evidenceDir, { recursive: true });
      await waitForVisualStability(page);
      await page.screenshot({ path: path.join(evidenceDir, "product-diagnostics-error-electron-1120.png"), fullPage: false });
    }
    const originalClipboard = await page.evaluate(() => window.taijiDesktop.readClipboardText());
    const copyIncident = page.locator("#btnCopyProductDiagnosticsIncident");
    await copyIncident.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(async (expected) => (
      await window.taijiDesktop.readClipboardText()
    ) === expected, errorState.incident);
    await page.evaluate(async (original) => {
      await _copyText(original);
    }, originalClipboard);
    mark("allowlisted error state passed");
    await page.unroute("**/api/product/diagnostics", diagnosticsFailureRoute);
    await page.waitForTimeout(100);
    injectingExpectedDiagnostics503 = false;
    await refresh.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => (
      !document.getElementById("btnRefreshProductDiagnostics").disabled
      && document.querySelectorAll("#productDiagnosticsComponents .product-diagnostics-component").length === 7
    ));
    mark("live diagnostics restored after error fixture");

    const securityFailureRoute = async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          error: "/Users/alice sentinel-secret must not render",
          product_error: {
            schema: "taiji.product.error.v1",
            code: "permission_denied",
            title: "attacker title must not render",
            message: "attacker message must not render",
            recovery_actions: [
              { id: "open_security_settings", label: "attacker settings label" },
              { id: "export_diagnostics", label: "attacker export label" },
            ],
            incident_id: "inc-abcdef012345",
            retryable: false,
          },
        }),
      });
    };
    await page.route("**/api/security/profile", securityFailureRoute);
    const securitySave = page.locator("#settingsSecurityProfileSave");
    await securitySave.scrollIntoViewIfNeeded();
    await securitySave.focus();
    injectingExpectedSecurity403 = true;
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => (
      document.getElementById("settingsSecurityStatus")?.textContent.includes("当前操作未获授权")
    ));
    const securityErrorText = await page.locator("#settingsSecurityStatus").innerText();
    assert.ok(securityErrorText.includes("请检查安全模式或联系管理员确认操作权限。"));
    assert.ok(!securityErrorText.includes("attacker"));
    assert.ok(!securityErrorText.includes("/Users/"));
    assert.ok(!securityErrorText.includes("sentinel"));
    await page.waitForTimeout(100);
    injectingExpectedSecurity403 = false;
    await page.unroute("**/api/security/profile", securityFailureRoute);
    mark("nested product error stayed allowlisted in Desktop App");

    const safeDocxEvidence = await page.evaluate(() => _docxEngineFailureEvidence({
      payload: {
        failure_report_path: "/Users/alice/sentinel/failure-report.json",
        job_manifest_path: "C:\\Users\\Alice Smith\\sentinel\\job.json",
        failures: ["Traceback sentinel-secret"],
        product_error: {
          schema: "taiji.product.error.v1",
          code: "artifact_generation_failed",
          title: "attacker title must not render",
          message: "attacker message must not render",
          recovery_actions: [{ id: "retry", label: "attacker retry" }],
          incident_id: "inc-fedcba654321",
          retryable: true,
        },
      },
    }));
    assert.strictEqual(safeDocxEvidence, " 事件编号：inc-fedcba654321");
    assert.ok(!safeDocxEvidence.includes("/Users/"));
    assert.ok(!safeDocxEvidence.includes("sentinel"));
    assert.ok(!safeDocxEvidence.includes("Traceback"));
    mark("DOCX failure evidence stayed private in Desktop App");

    const exported = await exportBundleByKeyboard(page);

    await page.evaluate(() => window.resizeTo(1440, 900));
    // macOS may clamp the requested 900px content height to the visible work
    // area (876px on the QA machine). Width must remain the requested 1440px,
    // and the actual App viewport is recorded below rather than fabricated.
    await page.waitForFunction(() => innerWidth === 1440 && innerHeight >= 820);
    const standardLayout = await snapshotLayout(page);
    assertState(standardLayout.cardVisible, "Diagnostics card is not visible at 1440x900", standardLayout);
    assertState(!standardLayout.documentOverflowX, "Diagnostics page overflows horizontally at 1440x900", standardLayout);
    assertState(standardLayout.scrollOwners.length <= 1, "Settings has nested vertical scroll owners at 1440x900", standardLayout);
    standardLayout.buttons.forEach((button) => {
      assertState(button.visible && button.height >= 34 && button.text.length > 0, "Diagnostics action is clipped at the standard App size", button);
    });

    if (evidenceDir) {
      fs.mkdirSync(evidenceDir, { recursive: true });
      await waitForVisualStability(page);
      await page.screenshot({ path: path.join(evidenceDir, "product-diagnostics-electron-1440.png"), fullPage: false });
      await page.evaluate(() => window.resizeTo(1120, 720));
      await page.waitForFunction(() => innerWidth === 1120 && innerHeight === 720);
      await waitForVisualStability(page);
      await page.screenshot({ path: path.join(evidenceDir, "product-diagnostics-electron-1120.png"), fullPage: false });
    }

    results.minimumLayout = minimumLayout;
    results.standardLayout = standardLayout;
    results.errorState = errorState;
    results.expectedFixtureConsoleErrorCount = expectedFixtureConsoleErrors.length;
    results.httpFailures = httpFailures;
    const expectedBackgroundHttpFailures = httpFailures.filter((entry) => isExpectedDesktopHttpFailure(entry, appOrigin));
    const expectedDiagnosticsFixtureFailures = httpFailures.filter((entry) => {
      const url = new URL(entry.url);
      return entry.status === 503 && entry.method === "GET" && url.origin === appOrigin && url.pathname === "/api/product/diagnostics";
    });
    const expectedSecurityFixtureFailures = httpFailures.filter((entry) => {
      const url = new URL(entry.url);
      return entry.status === 403 && entry.method === "POST" && url.origin === appOrigin && url.pathname === "/api/security/profile";
    });
    const unexpectedHttpFailures = httpFailures.filter((entry) => (
      !isExpectedDesktopHttpFailure(entry, appOrigin)
      && !expectedDiagnosticsFixtureFailures.includes(entry)
      && !expectedSecurityFixtureFailures.includes(entry)
    ));
    assertState(expectedBackgroundHttpFailures.length <= 1, "Unexpected repeated missing expert-run requests", expectedBackgroundHttpFailures);
    assertState(
      expectedBackgroundConsoleErrors.length === expectedBackgroundHttpFailures.length,
      "Missing expert-run console error did not correlate with its exact same-origin request",
      { expectedBackgroundConsoleErrors, expectedBackgroundHttpFailures },
    );
    assertState(expectedDiagnosticsFixtureFailures.length === 1, "Diagnostics failure fixture did not produce exactly one 503", expectedDiagnosticsFixtureFailures);
    assertState(expectedSecurityFixtureFailures.length === 1, "Security failure fixture did not produce exactly one 403", expectedSecurityFixtureFailures);
    assertState(unexpectedHttpFailures.length === 0, "Unexpected HTTP failures occurred during App diagnostics smoke", unexpectedHttpFailures);
    results.expectedBackgroundConsoleErrorCount = expectedBackgroundConsoleErrors.length;
    results.overall = diagnostics.payload.overall;
    results.export = { suggestedFilename: exported.suggestedFilename, bytes: exported.bytes, downloadEventObserved: exported.downloadEventObserved };
    results.jsErrorCount = jsErrors.length;
    assertState(jsErrors.length === 0, "JavaScript errors occurred during App diagnostics smoke", { jsErrors, httpFailures });
    mark("desktop acceptance passed");
    process.stdout.write(`${JSON.stringify(results, null, 2)}\n`);
  } finally {
    await page.evaluate(({ width, height }) => window.resizeTo(width, height), originalSize).catch(() => {});
    await browser.close().catch(() => {});
  }
}

module.exports = {
  isExpectedDesktopHttpFailure,
  isExpectedBackgroundConsoleError,
};

if (require.main === module) {
  run().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 1;
  });
}
