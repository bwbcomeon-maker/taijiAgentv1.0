#!/usr/bin/env node
/*
 * Keyboard/focus smoke for a real Taiji Electron BrowserWindow.
 *
 * Prerequisites:
 *   - launch an isolated desktop QA .app with remote debugging enabled;
 *   - keep onboarding_completed=false in that isolated runtime;
 *   - point PLAYWRIGHT_NODE_PATH at a Playwright installation.
 *
 * A normal Chromium URL is deliberately rejected: this is App acceptance.
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  return require(process.env.PLAYWRIGHT_NODE_PATH || "playwright");
}

const { chromium } = loadPlaywright();
const cdpEndpoint = process.env.TAIJI_DESKTOP_CDP || "http://127.0.0.1:9233";
const evidenceDir = process.env.TAIJI_DESKTOP_EVIDENCE_DIR || "";

function assertState(condition, message, detail) {
  assert.ok(condition, `${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
}

async function onboardingStatus(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/onboarding/status", { credentials: "include" });
    const payload = await response.json();
    return { ok: response.ok, status: response.status, completed: Boolean(payload.completed) };
  });
}

async function waitForDesktopReady(page) {
  await page.waitForFunction(() => (
    document.readyState === "complete"
    && typeof ManagedDialog === "object"
    && document.getElementById("onboardingOverlay")
  ));
}

async function pressActivationKey(page) {
  await page.keyboard.press("Enter");
}

async function run() {
  const browser = await chromium.connectOverCDP(cdpEndpoint);
  const pages = browser.contexts().flatMap((context) => context.pages());
  const page = pages.find((candidate) => candidate.url().includes("taiji_desktop=1"));
  assertState(page, "No real Taiji Electron BrowserWindow was found", pages.map((item) => item.url()));
  assertState(
    page.url().includes("taiji_desktop_token="),
    "Desktop token is missing; refusing to treat a normal Web page as App acceptance",
    page.url(),
  );

  const jsErrors = [];
  page.on("pageerror", (error) => jsErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") jsErrors.push(`console: ${message.text()}`);
  });

  const originalSize = await page.evaluate(() => ({ width: outerWidth, height: outerHeight }));
  const results = { appUrl: page.url().replace(/taiji_desktop_token=[^&]+/, "taiji_desktop_token=<redacted>") };
  try {
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForDesktopReady(page);
    await page.evaluate(() => window.resizeTo(1120, 720));
    await page.waitForFunction(() => innerWidth === 1120 && innerHeight === 720);
    await page.waitForFunction(() => (
      getComputedStyle(document.getElementById("onboardingOverlay")).display === "flex"
      && document.activeElement?.id === "onboardingNextBtn"
    ));

    const statusBefore = await onboardingStatus(page);
    assertState(statusBefore.ok && !statusBefore.completed, "Onboarding fixture must be incomplete", statusBefore);
    const onboardingOpen = await page.evaluate(() => {
      const overlay = document.getElementById("onboardingOverlay");
      const card = overlay.querySelector(".onboarding-card");
      const rect = card.getBoundingClientRect();
      return {
        active: document.activeElement?.id || "",
        overlayScrollTop: overlay.scrollTop,
        cardScrollTop: card.scrollTop,
        cardOverflowY: card.scrollHeight > card.clientHeight,
        cardTop: rect.top,
        cardBottom: rect.bottom,
        viewport: [innerWidth, innerHeight],
        documentOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        documentOverflowY: document.documentElement.scrollHeight > document.documentElement.clientHeight,
      };
    });
    assertState(onboardingOpen.active === "onboardingNextBtn", "Onboarding initial focus is wrong", onboardingOpen);
    assertState(
      onboardingOpen.overlayScrollTop === 0 && onboardingOpen.cardScrollTop === 0,
      "Onboarding opened with a stale scroll offset",
      onboardingOpen,
    );
    assertState(!onboardingOpen.cardOverflowY, "Onboarding card has an unexpected internal scrollbar", onboardingOpen);
    assertState(onboardingOpen.cardTop >= 0 && onboardingOpen.cardBottom <= 720, "Onboarding card is clipped", onboardingOpen);
    assertState(!onboardingOpen.documentOverflowX && !onboardingOpen.documentOverflowY, "Onboarding overflows the App viewport", onboardingOpen);

    await page.keyboard.press("Tab");
    assert.strictEqual(await page.evaluate(() => document.activeElement?.id), "onboardingSkipBtn");
    await page.keyboard.press("Shift+Tab");
    assert.strictEqual(await page.evaluate(() => document.activeElement?.id), "onboardingNextBtn");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => (
      getComputedStyle(document.getElementById("onboardingOverlay")).display === "none"
      && document.activeElement?.id === "msg"
    ));
    assert.strictEqual(await page.evaluate(() => document.activeElement?.id), "msg");
    const statusAfterEscape = await onboardingStatus(page);
    assertState(!statusAfterEscape.completed, "Escape incorrectly persisted onboarding completion", statusAfterEscape);

    // A reload of the same real App must resume incomplete onboarding. Full App
    // relaunch is kept as a separate acceptance step in the verification ledger.
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForDesktopReady(page);
    await page.waitForFunction(() => (
      getComputedStyle(document.getElementById("onboardingOverlay")).display === "flex"
      && document.activeElement?.id === "onboardingNextBtn"
    ));
    await page.keyboard.press("Escape");

    const writingNav = page.locator('.taiji-nav-item[data-taiji-panel="writing"]');
    await writingNav.focus();
    await pressActivationKey(page);
    await page.waitForFunction(() => getComputedStyle(document.getElementById("mainWriting")).display === "flex");
    const teamCard = page.locator('#writeflowTeamGrid [data-writeflow-team="content-creator-team"]');
    await teamCard.waitFor({ state: "visible" });
    await teamCard.focus();
    await pressActivationKey(page);
    await page.waitForFunction(() => (
      !document.getElementById("writeflowTeamModal").hidden
      && document.activeElement?.id === "writeflowTeamModalTitle"
    ));

    const expertOpen = await page.evaluate(() => {
      const modal = document.getElementById("writeflowTeamModal");
      const shell = modal.querySelector(".writeflow-modal");
      const title = document.getElementById("writeflowTeamModalTitle");
      const rect = title.getBoundingClientRect();
      return {
        active: document.activeElement?.id || "",
        shellScrollTop: shell.scrollTop,
        bodyScrollTop: document.getElementById("writeflowTeamModalBody").scrollTop,
        titleTop: rect.top,
        titleBottom: rect.bottom,
        titleVisible: rect.top >= 0 && rect.bottom <= innerHeight,
      };
    });
    assertState(expertOpen.active === "writeflowTeamModalTitle", "Expert dialog initial focus is wrong", expertOpen);
    assertState(expertOpen.shellScrollTop === 0 && expertOpen.bodyScrollTop === 0, "Expert dialog opened scrolled away from its title", expertOpen);
    assertState(expertOpen.titleVisible, "Expert dialog title is outside the 1120x720 App viewport", expertOpen);

    const lastFocusable = await page.evaluate(() => {
      const modal = document.getElementById("writeflowTeamModal");
      const focusable = Array.from(modal.querySelectorAll(
        'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[contenteditable="true"],[tabindex]:not([tabindex="-1"])',
      )).filter((node) => {
        const style = getComputedStyle(node);
        return style.display !== "none" && style.visibility !== "hidden" && node.offsetParent !== null;
      });
      const last = focusable[focusable.length - 1];
      last.focus();
      return last.className;
    });
    assertState(String(lastFocusable).includes("writeflow-summon-btn"), "Unexpected last expert-dialog control", lastFocusable);
    await page.keyboard.press("Tab");
    assertState(
      String(await page.evaluate(() => document.activeElement?.className || "")).includes("writeflow-modal-close"),
      "Tab did not wrap to the first expert-dialog control",
    );
    await page.keyboard.press("Shift+Tab");
    assertState(
      String(await page.evaluate(() => document.activeElement?.className || "")).includes("writeflow-summon-btn"),
      "Shift+Tab did not wrap to the last expert-dialog control",
    );
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => (
      document.getElementById("writeflowTeamModal").hidden
      && document.activeElement?.getAttribute("data-writeflow-team") === "content-creator-team"
    ));
    assert.strictEqual(
      await page.evaluate(() => document.activeElement?.getAttribute("data-writeflow-team")),
      "content-creator-team",
    );

    if (evidenceDir) {
      fs.mkdirSync(evidenceDir, { recursive: true });
      await page.reload({ waitUntil: "domcontentloaded" });
      await waitForDesktopReady(page);
      await page.screenshot({ path: path.join(evidenceDir, "managed-dialog-electron-1120.png") });
    }
    results.onboarding = { open: onboardingOpen, statusBefore, statusAfterEscape };
    results.expert = expertOpen;
    results.jsErrorCount = jsErrors.length;
    assertState(jsErrors.length === 0, "JavaScript errors occurred during App smoke", jsErrors);
    process.stdout.write(`${JSON.stringify(results, null, 2)}\n`);
  } finally {
    await page.evaluate(({ width, height }) => window.resizeTo(width, height), originalSize).catch(() => {});
  }
}

run().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
