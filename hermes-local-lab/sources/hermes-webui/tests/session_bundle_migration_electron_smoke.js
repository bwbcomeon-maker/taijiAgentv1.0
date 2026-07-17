#!/usr/bin/env node
/* Real Electron acceptance for portable bundles and opt-in legacy repair. */
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");
const {
  assertNavigationParity,
  captureAuditedScreenshot,
  collectSourceFingerprint,
  inspectTaijiNavigation,
  installDailyEquivalentRuntimeConfig,
} = require("./electron_acceptance_provenance");

function assertState(condition, message, detail) {
  if (!condition) throw new Error(`${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
}

function argument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : "";
}

function readPid(file) {
  try { return Number(fs.readFileSync(file, "utf8").trim()) || 0; } catch (_) { return 0; }
}

function alive(pid) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; } catch (_) { return false; }
}

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function findNamedFiles(root, name, found = []) {
  if (!fs.existsSync(root)) return found;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) findNamedFiles(target, name, found);
    else if (entry.isFile() && entry.name === name) found.push(target);
  }
  return found;
}

async function terminate(pids) {
  for (const pid of [...new Set(pids.filter(Boolean))]) {
    if (alive(pid)) { try { process.kill(pid, "SIGTERM"); } catch (_) {} }
  }
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline && pids.some(alive)) await new Promise(resolve => setTimeout(resolve, 150));
  for (const pid of pids) if (alive(pid)) { try { process.kill(pid, "SIGKILL"); } catch (_) {} }
}

function prepareFixture({ pythonBin, webuiDir, agentDir, runtimeHome, workspace }) {
  const script = String.raw`
import json
import shutil
import sys
from pathlib import Path
from api.artifacts import ArtifactRegistry
from api.models import Session

runtime_home = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
image_source = Path(sys.argv[3]).resolve()
(runtime_home / "web" / "sessions").mkdir(parents=True, exist_ok=True)
registry = ArtifactRegistry(runtime_home / "web" / "artifacts", allowed_source_roots=[image_source.parent])
artifact = registry.register_image_file(
    "bundle-source", "bundle-turn", "bundle-tool", image_source, name="资源包验收图片.png"
)
source = Session(
    session_id="bundle-source", title="资源包导入导出验收", workspace=str(workspace),
    model="fixture-model", model_provider="fixture",
    messages=[{"role": "assistant", "content": "这是需要跨会话保留的文本和图片。", "artifacts": [artifact]}],
)
source.save()
legacy_cache = runtime_home / "cache" / "images" / "legacy migration image.png"
legacy_cache.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(image_source, legacy_cache)
legacy = Session(
    session_id="legacy-repair", title="旧会话修复验收", workspace=str(workspace),
    model="fixture-model", messages=[{
        "role": "assistant",
        "content": f"需要清理旧隐私标记。\nMEDIA:{legacy_cache}",
    }],
)
legacy.save()
legacy_path = runtime_home / "web" / "sessions" / "legacy-repair.json"
payload = json.loads(legacy_path.read_text("utf-8"))
payload["brand_privacy_tainted"] = True
legacy_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps({"artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]}))
`;
  const image = path.join(webuiDir, "static", "assets", "taiji", "background", "background-grid.png");
  const env = {
    ...process.env,
    TAIJI_RUNTIME_HOME: runtimeHome,
    TAIJI_WEBUI_STATE_DIR: path.join(runtimeHome, "web"),
    TAIJI_WEBUI_DEFAULT_WORKSPACE: workspace,
    TAIJI_LICENSE_REQUIRED: "0",
    PYTHONPATH: [webuiDir, agentDir].join(path.delimiter),
  };
  const result = spawnSync(pythonBin, ["-c", script, runtimeHome, workspace, image], { env, encoding: "utf8" });
  assertState(result.status === 0, "fixture creation failed", { stderr: String(result.stderr || "").slice(-1500) });
  return JSON.parse(result.stdout);
}

async function main() {
  const startedAt = new Date().toISOString();
  const outDir = path.resolve(argument("--out-dir"));
  assertState(Boolean(argument("--out-dir")), "--out-dir is required");
  fs.mkdirSync(outDir, { recursive: true });
  const playwrightPath = process.env.PLAYWRIGHT_NODE_PATH || "playwright";
  const { _electron } = require(playwrightPath);
  const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
  const mainRepo = path.resolve(repoRoot, "..", "..");
  const webuiDir = path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui");
  const agentDir = path.join(repoRoot, "hermes-local-lab", "sources", "hermes-agent");
  const labDir = path.join(repoRoot, "hermes-local-lab");
  const appDir = path.join(repoRoot, "apps", "taiji-desktop");
  const electronBin = process.env.TAIJI_ELECTRON_BIN || path.join(
    mainRepo, "apps", "taiji-desktop", "node_modules", "electron", "dist",
    "Electron.app", "Contents", "MacOS", "Electron",
  );
  const pythonBin = process.env.TAIJI_TEST_PYTHON || path.join(agentDir, ".venv", "bin", "python");
  assertState(fs.existsSync(electronBin), "Electron binary missing", { electronBin });
  assertState(fs.existsSync(pythonBin), "Python runtime missing", { pythonBin });

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-bundle-migration-electron-"));
  const dirs = {
    runtimeHome: path.join(root, "runtime"), workspace: path.join(root, "workspace"),
    userData: path.join(root, "user-data"), config: path.join(root, "config"),
    data: path.join(root, "data"), state: path.join(root, "state"),
  };
  Object.values(dirs).forEach(directory => fs.mkdirSync(directory, { recursive: true }));
  const runtimeConfig = installDailyEquivalentRuntimeConfig(dirs.runtimeHome);
  const sourceFingerprint = collectSourceFingerprint({ repoRoot, webuiDir });
  const fixture = prepareFixture({ pythonBin, webuiDir, agentDir, runtimeHome: dirs.runtimeHome, workspace: dirs.workspace });
  const pidFiles = [
    path.join(dirs.state, "taiji-agent", "logs", "agent.pid"),
    path.join(dirs.state, "taiji-agent", "logs", "web.pid"),
  ];
  let app;
  let electronPid = 0;
  let servicePids = [];
  let resultPayload = null;
  const screenshotSanity = {};
  try {
    app = await _electron.launch({
      executablePath: electronBin,
      args: [appDir],
      env: {
        ...process.env,
        TAIJI_AGENT_ROOT: labDir,
        TAIJI_AGENT_USE_USER_DIRS: "1",
        TAIJI_DESKTOP_USER_DATA_DIR: dirs.userData,
        XDG_CONFIG_HOME: dirs.config,
        XDG_DATA_HOME: dirs.data,
        XDG_STATE_HOME: dirs.state,
        TAIJI_RUNTIME_HOME: dirs.runtimeHome,
        TAIJI_WORKSPACE: dirs.workspace,
        TAIJI_AGENT_PYTHON: pythonBin,
        TAIJI_WEBUI_PYTHON: pythonBin,
        TAIJI_LICENSE_REQUIRED: "0",
        TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: "0",
        TAIJI_AGENT_SYNC_PACKAGED_CONFIG: "0",
      },
      timeout: 120000,
    });
    electronPid = app.process()?.pid || 0;
    const page = await app.firstWindow({ timeout: 120000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 120000 });
    await page.waitForFunction(
      () => location.href.includes("taiji_desktop=1") && typeof loadSession === "function",
      { timeout: 120000 },
    );
    await page.evaluate(async () => {
      try { localStorage.setItem("hermes-lang", "zh"); } catch (_) {}
      if (typeof setLanguage === "function") setLanguage("zh");
      document.getElementById("onboardingOverlay")?.remove();
      await loadSession("bundle-source", { force: true });
      await switchPanel("settings");
      switchSettingsSection("conversation");
    });
    await page.waitForSelector("#btnExportBundle", { state: "visible" });
    await page.waitForFunction(() => !document.getElementById("legacyMigrationCard")?.hidden);
    const navigation = await inspectTaijiNavigation(page);
    assertNavigationParity(navigation);
    const preloadedLegacy = await page.evaluate(() => api(
      "/api/session?session_id=legacy-repair&messages=1&resolve_model=0"
    ));
    assertState(preloadedLegacy.session?.session_id === "legacy-repair", "legacy session was not preloaded through the production GET API", preloadedLegacy);
    const actionText = await page.locator("#settingsPaneConversation").innerText();
    assertState(actionText.includes("资源包 ZIP（含图片）") && actionText.includes("兼容 JSON（仅文本）"), "bundle/JSON actions are not distinct", { actionText });
    screenshotSanity["01-settings-audit.png"] = await captureAuditedScreenshot(
      page, outDir, "01-settings-audit.png",
    );

    const roundtrip = await page.evaluate(async sourceId => {
      const exported = await fetch(`api/session/export-bundle?session_id=${encodeURIComponent(sourceId)}`, { credentials: "include" });
      if (!exported.ok) throw new Error(`bundle export ${exported.status}`);
      const bytes = await exported.arrayBuffer();
      const imported = await fetch("api/session/import-bundle", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/zip" }, body: bytes,
      });
      if (!imported.ok) throw new Error(`bundle import ${imported.status}: ${await imported.text()}`);
      return imported.json();
    }, "bundle-source");
    assertState(roundtrip.ok && roundtrip.session.session_id !== "bundle-source", "bundle did not create a fresh session", roundtrip);
    await page.evaluate(async sid => { await switchPanel("chat"); await loadSession(sid, { force: true }); }, roundtrip.session.session_id);
    await page.waitForSelector(".chat-artifact-image", { state: "visible", timeout: 30000 });
    await page.waitForFunction(() => {
      const image = document.querySelector(".chat-artifact-image");
      return image?.complete && image.naturalWidth > 0 && image.closest(".msg-artifact-image")?.dataset.state === "ready";
    }, { timeout: 30000 });
    const importedView = await page.evaluate(() => ({
      text: document.getElementById("messages")?.innerText || "",
      artifactId: S.messages.flatMap(message => message.artifacts || [])[0]?.artifact_id || "",
      imageCount: document.querySelectorAll(".chat-artifact-image").length,
      naturalWidth: document.querySelector(".chat-artifact-image")?.naturalWidth || 0,
    }));
    assertState(importedView.text.includes("这是需要跨会话保留的文本和图片。"), "bundle text did not recover", importedView);
    assertState(importedView.imageCount === 1 && importedView.naturalWidth > 0 && importedView.artifactId && importedView.artifactId !== fixture.artifact_id, "bundle artifact was not rebound/rendered", importedView);
    const mediaResponse = await page.evaluate(async () => {
      const image = document.querySelector(".chat-artifact-image");
      const response = await fetch(image.src, { credentials: "include" });
      return { status: response.status, bytes: (await response.arrayBuffer()).byteLength };
    });
    assertState(mediaResponse.status === 200 && mediaResponse.bytes > 0, "imported artifact backend route failed", mediaResponse);
    screenshotSanity["02-bundle-roundtrip.png"] = await captureAuditedScreenshot(
      page, outDir, "02-bundle-roundtrip.png",
    );

    const legacyJson = await page.evaluate(async () => {
      const exported = await fetch("api/session/export?session_id=bundle-source", { credentials: "include" });
      const payload = await exported.json();
      const imported = await fetch("api/session/import", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      return imported.json();
    });
    await page.evaluate(async sid => { await loadSession(sid, { force: true }); }, legacyJson.session.session_id);
    const legacyView = await page.evaluate(() => ({
      artifacts: S.messages.reduce((total, message) => total + (message.artifacts || []).length, 0),
      images: document.querySelectorAll(".chat-artifact-image").length,
      text: document.getElementById("messages")?.innerText || "",
    }));
    assertState(legacyView.artifacts === 0 && legacyView.images === 0 && legacyView.text.includes("这是需要跨会话保留的文本和图片。"), "legacy JSON was not text-only", legacyView);
    screenshotSanity["03-legacy-json-text-only.png"] = await captureAuditedScreenshot(
      page, outDir, "03-legacy-json-text-only.png",
    );

    await page.evaluate(async () => { await switchPanel("settings"); switchSettingsSection("conversation"); await loadLegacyMigrationAudit(); });
    await page.locator("#btnApplyLegacyMigration").click();
    await page.waitForSelector("#appDialogOverlay", { state: "visible" });
    const confirmation = await page.locator("#appDialogDesc").innerText();
    assertState(confirmation.includes("先创建完整本地备份") && confirmation.includes("不可追回"), "migration confirmation omits backup/risk", { confirmation });
    await page.keyboard.press("Escape");
    await page.waitForSelector("#appDialogOverlay", { state: "hidden" });
    const afterCancel = await page.evaluate(() => api("/api/session/migration/audit"));
    assertState(afterCancel.items.some(item => item.code === "legacy_privacy_taint"), "cancel unexpectedly applied migration", afterCancel);

    const workerHealthBeforeApply = await page.evaluate(async () => {
      const response = await fetch("/health?deep=1", { credentials: "include" });
      return { http_status: response.status, ...(await response.json()) };
    });
    const cronStatusBeforeApply = await page.evaluate(() => api("/api/crons/status"));
    assertState(
      workerHealthBeforeApply.status === "ok" && workerHealthBeforeApply.active_runs === 0,
      "migration fixture was not idle before Apply",
      workerHealthBeforeApply,
    );
    assertState(
      Object.keys(cronStatusBeforeApply.running || {}).length === 0,
      "manual cron route was not idle before Apply",
      cronStatusBeforeApply,
    );

    await page.locator("#btnApplyLegacyMigration").click();
    await page.waitForSelector("#appDialogOverlay", { state: "visible" });
    const successApplyResponsePromise = page.waitForResponse(response => (
      response.request().method() === "POST" && response.url().includes("/api/session/migration/apply")
    ), { timeout: 120000 });
    const successFreshAuditResponsePromise = page.waitForResponse(response => (
      response.request().method() === "GET" && response.url().includes("/api/session/migration/audit")
    ), { timeout: 120000 });
    const successApplyStartedMs = Date.now();
    await page.locator("#appDialogConfirm").click();
    const successApplyResponse = await successApplyResponsePromise;
    const successApplyPayload = await successApplyResponse.json();
    const successFreshAuditResponse = await successFreshAuditResponsePromise;
    const successFreshAuditPayload = await successFreshAuditResponse.json();
    const successApplyDurationMs = Date.now() - successApplyStartedMs;
    assertState(successApplyResponse.ok(), "successful migration apply request failed", successApplyPayload);
    assertState(successFreshAuditResponse.ok(), "post-apply authoritative audit request failed", successFreshAuditPayload);
    await page.waitForFunction(() => {
      const status = document.getElementById("legacyMigrationStatus")?.innerText || "";
      const result = document.getElementById("legacyMigrationResult")?.innerText || "";
      return status !== "正在备份并修复…"
        && status !== "正在只读检测旧会话…"
        && result.includes("已创建本地备份");
    }, { timeout: 30000 });
    const successReceipt = await page.locator("#legacyMigrationCard").evaluate(card => ({
      hidden: card.hidden,
      text: card.innerText,
      status: document.getElementById("legacyMigrationStatus")?.innerText || "",
      result: document.getElementById("legacyMigrationResult")?.innerText || "",
      badge: document.getElementById("legacyMigrationBadge")?.innerText || "",
      toast: document.getElementById("toast")?.dataset.toastMessage || "",
      toastClass: document.getElementById("toast")?.className || "",
      toastRole: document.getElementById("toast")?.getAttribute("role") || "",
      toastLive: document.getElementById("toast")?.getAttribute("aria-live") || "",
    }));
    assertState(successReceipt.result.includes("已创建本地备份"), "successful migration receipt omitted backup confirmation", {
      successApplyPayload, successFreshAuditPayload, successReceipt,
    });
    assertState(await page.locator("#btnApplyLegacyMigration").isDisabled(), "completed migration still offers a misleading apply action");
    assertState((await page.locator("#legacyMigrationBadge").innerText()) === "已修复", "completed migration badge is stale");
    assertState(
      successReceipt.toast.includes("旧会话修复完成")
      && successReceipt.toastClass.split(/\s+/).includes("success")
      && successReceipt.toastRole === "status"
      && successReceipt.toastLive === "polite",
      "successful migration did not expose a consistent success toast/live status",
      successReceipt,
    );
    const afterApply = await page.evaluate(() => api("/api/session/migration/audit"));
    assertState(!afterApply.items.some(item => ["legacy_privacy_taint", "state_db_user_backfill_exact", "legacy_cached_image"].includes(item.code)), "migration left an auto-repairable item", afterApply);
    assertState(await page.locator("#btnApplyLegacyMigration").isDisabled(), "fresh audit re-enabled migration");
    const workerHealthAfterApply = await page.evaluate(async () => {
      const response = await fetch("/health?deep=1", { credentials: "include" });
      return { http_status: response.status, ...(await response.json()) };
    });
    const cronStatusAfterApply = await page.evaluate(() => api("/api/crons/status"));
    assertState(
      workerHealthAfterApply.status === "ok" && workerHealthAfterApply.active_runs === 0,
      "worker lifecycle did not return to idle after Apply",
      workerHealthAfterApply,
    );
    assertState(
      Object.keys(cronStatusAfterApply.running || {}).length === 0,
      "manual cron route did not remain idle after Apply",
      cronStatusAfterApply,
    );
    const cacheConsistency = await page.evaluate(async () => {
      const renamed = await api("/api/session/rename", {
        method: "POST",
        body: JSON.stringify({ session_id: "legacy-repair", title: "迁移后正常保存验收" }),
      });
      const fresh = await api(
        "/api/session?session_id=legacy-repair&messages=1&resolve_model=0"
      );
      return {
        renamedTitle: renamed?.session?.title || "",
        title: fresh?.session?.title || "",
        messages: fresh?.session?.messages || [],
      };
    });
    const cacheConsistencyText = JSON.stringify(cacheConsistency);
    assertState(
      cacheConsistency.renamedTitle === "迁移后正常保存验收"
      && cacheConsistency.title === "迁移后正常保存验收"
      && cacheConsistency.messages.some(message => Array.isArray(message.artifacts) && message.artifacts.length === 1)
      && !cacheConsistencyText.includes("MEDIA:")
      && !cacheConsistencyText.includes("privacy_context")
      && !cacheConsistencyText.includes("brand_privacy_tainted"),
      "preloaded cache revived legacy fields after a normal production rename/save/GET",
      cacheConsistency,
    );
    const secondApplyStartedMs = Date.now();
    const secondApply = await page.evaluate(() => api("/api/session/migration/apply", {
      method: "POST", body: JSON.stringify({ confirm: true }),
    }));
    const secondApplyDurationMs = Date.now() - secondApplyStartedMs;
    assertState(secondApply.modified === 0 && secondApply.backup_created === false && secondApply.failed === 0, "second migration apply is not an idempotent no-op", secondApply);
    screenshotSanity["04-migration-applied.png"] = await captureAuditedScreenshot(
      page, outDir, "04-migration-applied.png",
    );
    await page.locator("#btnAuditLegacySessions").click();
    await page.waitForFunction(() => document.getElementById("legacyMigrationCard")?.hidden === true);
    assertState(await page.locator("#legacyMigrationCard").isHidden(), "manual fresh audit did not converge the success receipt to current truth");

    const failureImage = path.join(dirs.runtimeHome, "cache", "images", "migration-failure.png");
    const failureSession = path.join(dirs.runtimeHome, "web", "sessions", "migration-failure.json");
    const artifactRoot = path.join(dirs.runtimeHome, "web", "artifacts");
    fs.mkdirSync(path.dirname(failureImage), { recursive: true });
    fs.copyFileSync(
      path.join(webuiDir, "static", "assets", "taiji", "background", "background-grid.png"),
      failureImage,
    );
    fs.writeFileSync(failureSession, JSON.stringify({
      session_id: "migration-failure", title: "迁移回滚验收", workspace: dirs.workspace,
      model: "fixture-model", messages: [{
        role: "assistant", content: `待提升图片\nMEDIA:${failureImage}`,
      }], tool_calls: [],
    }, null, 2));
    const failureSessionBefore = sha256File(failureSession);
    const failureCacheBefore = sha256File(failureImage);
    const stateDbFiles = [...new Set(
      Object.values(dirs).flatMap(directory => findNamedFiles(directory, "state.db")),
    )].sort();
    assertState(stateDbFiles.length > 0, "state.db was not found inside the isolated Electron fixture", { dirs });
    const stateDbBefore = Object.fromEntries(stateDbFiles.map(file => [file, sha256File(file)]));
    const failureArtifactDir = path.join(artifactRoot, "migration-failure");
    assertState(!fs.existsSync(failureArtifactDir), "failure artifact fixture was not isolated");
    let appliedFailure;
    let failureApplyDurationMs = 0;
    try {
      fs.chmodSync(artifactRoot, 0o555);
      await page.locator('.taiji-nav-item[data-taiji-panel="chat"]').click();
      await page.locator('.taiji-nav-item[data-taiji-panel="settings"]').click();
      await page.waitForFunction(() => !document.getElementById("legacyMigrationCard")?.hidden);
      await page.waitForFunction(() => !document.getElementById("btnApplyLegacyMigration")?.disabled);
      await page.locator("#btnApplyLegacyMigration").click();
      await page.waitForSelector("#appDialogOverlay", { state: "visible" });
      const applyResponsePromise = page.waitForResponse(response => (
        response.request().method() === "POST" && response.url().includes("/api/session/migration/apply")
      ), { timeout: 120000 });
      const failureApplyStartedMs = Date.now();
      await page.locator("#appDialogConfirm").click();
      const applyResponse = await applyResponsePromise;
      failureApplyDurationMs = Date.now() - failureApplyStartedMs;
      appliedFailure = await applyResponse.json();
      await page.waitForFunction(
        () => document.getElementById("legacyMigrationResult")?.innerText.includes("失败 1 批"),
        { timeout: 30000 },
      );
    } finally {
      fs.chmodSync(artifactRoot, 0o755);
    }
    const rollbackItem = appliedFailure?.items?.find(item => item.code === "migration_failed");
    assertState(
      appliedFailure?.failed === 1 && rollbackItem?.rollback_complete === true && rollbackItem?.reason === "batch_rolled_back",
      "real failure did not report a complete batch rollback", appliedFailure,
    );
    const freshFailureAudit = await page.evaluate(() => api("/api/session/migration/audit"));
    assertState(
      freshFailureAudit.needs_repair === true && freshFailureAudit.items.some(item => item.code === "legacy_cached_image"),
      "fresh audit did not retain the repairable failure finding", freshFailureAudit,
    );
    assertState(await page.locator("#btnApplyLegacyMigration").isEnabled(), "fresh failure audit did not leave a retry path");
    assertState(sha256File(failureSession) === failureSessionBefore, "rollback changed the failure sidecar");
    assertState(sha256File(failureImage) === failureCacheBefore, "rollback changed the referenced cache image");
    assertState(!fs.existsSync(failureArtifactDir), "rollback left a partial artifact session");
    const stateDbAfter = Object.fromEntries(stateDbFiles.map(file => [file, sha256File(file)]));
    assertState(JSON.stringify(stateDbAfter) === JSON.stringify(stateDbBefore), "rollback changed state.db bytes", { stateDbBefore, stateDbAfter });
    const failureText = await page.locator("#legacyMigrationCard").innerText();
    const failureFeedback = await page.evaluate(() => ({
      status: document.getElementById("legacyMigrationStatus")?.innerText || "",
      badge: document.getElementById("legacyMigrationBadge")?.innerText || "",
      toast: document.getElementById("toast")?.dataset.toastMessage || "",
      toastClass: document.getElementById("toast")?.className || "",
      toastRole: document.getElementById("toast")?.getAttribute("role") || "",
      toastLive: document.getElementById("toast")?.getAttribute("aria-live") || "",
    }));
    const bodyText = await page.locator("body").innerText();
    assertState(failureText.includes("失败 1 批") && failureText.includes("失败批次已回滚"), "failure report is not actionable", { failureText });
    assertState(
      failureFeedback.status.includes("失败批次已回滚")
      && failureFeedback.badge === "需处理"
      && failureFeedback.toast.includes("修复失败")
      && !failureFeedback.toast.includes("修复完成")
      && failureFeedback.toastClass.split(/\s+/).includes("error")
      && failureFeedback.toastRole === "status"
      && failureFeedback.toastLive === "polite",
      "failed migration exposed contradictory success feedback",
      failureFeedback,
    );
    assertState(
      !bodyText.includes("修复完成") && !bodyText.includes("无需修复"),
      "failed migration page still exposed success copy",
      { failureText, failureFeedback },
    );
    assertState(!bodyText.includes("/Users/") && !bodyText.includes("runtime-home") && !bodyText.includes("storage_path") && !bodyText.includes("backup_path"), "internal path leaked into DOM");
    screenshotSanity["05-migration-failure-report.png"] = await captureAuditedScreenshot(
      page, outDir, "05-migration-failure-report.png",
    );

    await page.setViewportSize({ width: 760, height: 900 });
    const narrow = await page.locator("#settingsPaneConversation").evaluate(node => ({
      right: node.getBoundingClientRect().right, viewport: innerWidth,
      exportName: document.getElementById("btnExportBundle")?.getAttribute("aria-label"),
      importName: document.getElementById("btnImportBundle")?.getAttribute("aria-label"),
    }));
    assertState(narrow.right <= narrow.viewport + 1 && narrow.exportName && narrow.importName, "narrow/a11y settings state failed", narrow);
    screenshotSanity["06-narrow.png"] = await captureAuditedScreenshot(
      page, outDir, "06-narrow.png",
    );

    servicePids = pidFiles.map(readPid).filter(Boolean);
    resultPayload = {
      status: "passed",
      run_id: path.basename(root),
      started_at: startedAt,
      temp_root: root,
      source_fingerprint: sourceFingerprint,
      runtime_config: runtimeConfig,
      navigation,
      launch_pids: { electron: electronPid, services: servicePids },
      source_artifact_id: fixture.artifact_id,
      imported_artifact_id: importedView.artifactId,
      imported_session_id: roundtrip.session.session_id,
      legacy_json_text_only: legacyView.artifacts === 0 && legacyView.images === 0,
      cancel_preserved_audit: true,
      apply_backup_created: true,
      success_toast_consistent: true,
      apply_duration_ms: successApplyDurationMs,
      second_apply_modified: 0,
      second_apply_duration_ms: secondApplyDurationMs,
      failed_apply_duration_ms: failureApplyDurationMs,
      cache_consistency_after_mutation: true,
      worker_barrier_api_contract: true,
      worker_idle_before_after_apply: true,
      cron_route_idle_before_after_apply: true,
      cron_running_before_apply: Object.keys(cronStatusBeforeApply.running || {}).length,
      cron_running_after_apply: Object.keys(cronStatusAfterApply.running || {}).length,
      worker_health_before_apply: {
        http_status: workerHealthBeforeApply.http_status,
        status: workerHealthBeforeApply.status,
        active_runs: workerHealthBeforeApply.active_runs,
      },
      worker_health_after_apply: {
        http_status: workerHealthAfterApply.http_status,
        status: workerHealthAfterApply.status,
        active_runs: workerHealthAfterApply.active_runs,
      },
      rollback_complete: rollbackItem?.rollback_complete === true,
      failed_apply_has_no_success_toast: true,
      rollback_checksums_restored: true,
      fresh_failure_audit_repairable: freshFailureAudit.needs_repair === true,
      artifact_rollback_quarantine: {
        count: Number(freshFailureAudit.quarantine_count || 0),
        status: String(freshFailureAudit.quarantine_status || "clean"),
      },
      media_response_status: mediaResponse.status,
      dom_internal_path_matches: 0,
      screenshots: [
        "01-settings-audit.png", "02-bundle-roundtrip.png", "03-legacy-json-text-only.png",
        "04-migration-applied.png", "05-migration-failure-report.png", "06-narrow.png",
      ],
      screenshot_sanity: screenshotSanity,
      acceptance_provenance: {
        source_fingerprint: true,
        runtime_config: true,
        navigation: true,
        screenshot_sanity: true,
      },
    };
  } finally {
    servicePids = [...new Set([...servicePids, ...pidFiles.map(readPid)].filter(Boolean))];
    if (app) { try { await app.close(); } catch (_) {} }
    await terminate([electronPid, ...servicePids]);
    const cleanup = {
      electron_stopped: electronPid > 0 && !alive(electronPid),
      service_pids_stopped: servicePids.every(pid => !alive(pid)),
      stopped_pids: [electronPid, ...servicePids].filter(Boolean),
      temp_root_removed: false,
    };
    try {
      fs.rmSync(root, { recursive: true, force: true });
      cleanup.temp_root_removed = !fs.existsSync(root);
    } catch (_) {}
    if (resultPayload) {
      resultPayload.finished_at = new Date().toISOString();
      resultPayload.cleanup = cleanup;
      fs.writeFileSync(
        path.join(outDir, "electron-session-bundle-migration-result.json"),
        JSON.stringify(resultPayload, null, 2),
      );
    }
  }
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
