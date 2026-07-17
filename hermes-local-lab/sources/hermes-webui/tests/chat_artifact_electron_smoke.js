#!/usr/bin/env node
/*
 * Real Electron smoke for durable chat image artifacts.
 *
 * The external image provider is replaced by deterministic local fixture
 * input, but Session and Artifact Registry persistence, HTTP media
 * authorization, rendering, restart recovery, and keyboard interaction all
 * use the production application chain.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const {
  assertNavigationParity,
  captureAuditedScreenshot,
  collectSourceFingerprint,
  inspectTaijiNavigation,
  installDailyEquivalentRuntimeConfig,
} = require("./electron_acceptance_provenance");

function parseArgs(argv) {
  const result = { outDir: "" };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--out-dir") result.outDir = argv[index + 1] || "";
  }
  if (!result.outDir) throw new Error("--out-dir is required");
  return result;
}

function loadPlaywright() {
  const moduleId = process.env.PLAYWRIGHT_NODE_PATH || "playwright";
  try {
    return require(moduleId);
  } catch (error) {
    throw new Error(`Cannot resolve Playwright from ${moduleId}`, { cause: error });
  }
}

function assertState(condition, message, detail) {
  if (!condition) throw new Error(`${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
}

function pgrepElectron() {
  const result = spawnSync("pgrep", ["-x", "Electron"], { encoding: "utf8" });
  if (result.status !== 0 && result.status !== 1) return [];
  return String(result.stdout || "").split(/\s+/).filter(Boolean).map(Number).filter(Number.isFinite);
}

function pidAlive(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (_) { return false; }
}

async function waitForPidsToExit(pids, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (pids.every(pid => !pidAlive(pid))) return true;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  return pids.every(pid => !pidAlive(pid));
}

async function terminateOwnedPids(pids) {
  const owned = [...new Set(pids.filter(pid => Number.isFinite(pid) && pid > 0))];
  const terminated = [];
  for (const pid of owned) {
    if (pidAlive(pid)) {
      try { process.kill(pid, "SIGTERM"); terminated.push(pid); } catch (_) {}
    }
  }
  if (await waitForPidsToExit(owned, 5000)) return { term: terminated, kill: [] };
  const forced = [];
  for (const pid of owned) {
    if (pidAlive(pid)) {
      try { process.kill(pid, "SIGKILL"); forced.push(pid); } catch (_) {}
    }
  }
  assertState(await waitForPidsToExit(owned, 5000), "owned fixture processes survived cleanup", { owned });
  return { term: owned, kill: forced };
}

function readPid(file) {
  try {
    const value = Number(fs.readFileSync(file, "utf8").trim());
    return Number.isFinite(value) ? value : 0;
  } catch (_) {
    return 0;
  }
}

function prepareFixture({ pythonBin, webuiDir, agentDir, runtimeHome, workspace, imageSource }) {
  const python = String.raw`
import json
import sys
import time
from pathlib import Path

from api.artifacts import ArtifactRegistry
from api.models import Session

runtime_home = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
image_source = Path(sys.argv[3]).resolve()
now = time.time()
(runtime_home / "web" / "sessions").mkdir(parents=True, exist_ok=True)

registry = ArtifactRegistry(
    runtime_home / "web" / "artifacts",
    allowed_source_roots=[image_source.parent],
)
success_id = "uiartifactsuccess"
artifact = registry.register_image_file(
    session_id=success_id,
    source_turn_id="turn-image-success",
    source_tool_call_id="tool-image-success",
    source_path=image_source,
    name="本地验收图片.png",
)
missing_id = "uiartifactmissing"
missing_artifact = registry.register_image_file(
    session_id=missing_id,
    source_turn_id="turn-image-missing",
    source_tool_call_id="tool-image-missing",
    source_path=image_source,
    name="已损坏的验收图片.png",
)
historical_id = "uiartifacthistoricalretry"
historical_artifact = registry.register_image_file(
    session_id=historical_id,
    source_turn_id="turn-image-historical",
    source_tool_call_id="tool-image-historical",
    source_path=image_source,
    name="历史缺失图片.png",
)
scroll_id = "uiartifactscrollanchor"
scroll_artifact = registry.register_image_file(
    session_id=scroll_id,
    source_turn_id="turn-image-scroll",
    source_tool_call_id="tool-image-scroll",
    source_path=image_source,
    name="延迟加载验收图片.png",
)

fixtures = {
    "uiartifactdefault": {
        "title": "默认会话验收",
        "messages": [
            {"role": "user", "content": "请生成一张用于验收的图片。", "timestamp": now - 80},
            {"role": "assistant", "content": "好的，我会在当前回复中处理图片产物。", "timestamp": now - 70},
        ],
    },
    "uiartifactgenerating": {
        "title": "图片生成中验收",
        "messages": [
            {"role": "user", "content": "请生成一张蓝色科技风图片。", "timestamp": now - 60},
            {
                "role": "assistant", "content": "", "timestamp": now - 55,
                "tool_calls": [{
                    "event_type": "tool.started", "name": "image_generate",
                    "status": "running", "summary": "正在生成图片",
                    "tid": "fixture-image-running", "done": False,
                    "is_error": False, "assistant_msg_idx": 1,
                }],
            },
        ],
    },
    success_id: {
        "title": "图片成功与恢复验收",
        "messages": [
            {"role": "user", "content": "请生成一张蓝色科技风图片。", "timestamp": now - 50},
            {
                "role": "assistant", "content": "图片已生成，并已安全保存到当前会话。",
                "timestamp": now - 45, "artifacts": [artifact],
                "tool_calls": [{
                    "event_type": "tool.completed", "name": "image_generate",
                    "status": "completed", "summary": "图片已生成",
                    "tid": "fixture-image-success", "done": True,
                    "is_error": False, "assistant_msg_idx": 1,
                }],
            },
            {"role": "user", "content": "请基于上一轮图片，把构图改得更紧凑。", "timestamp": now - 40},
            {"role": "assistant", "content": "已识别上一轮的图片要求，细化时会沿用蓝色科技风并收紧构图。", "timestamp": now - 35},
        ],
    },
    "uiartifactfailure": {
        "title": "图片失败验收",
        "messages": [
            {"role": "user", "content": "请生成一张测试图片。", "timestamp": now - 30},
            {
                "role": "assistant", "content": "", "timestamp": now - 25,
                "tool_calls": [{
                    "event_type": "tool.completed", "name": "image_generate",
                    "status": "failed", "summary": "图片未能安全保存",
                    "tid": "fixture-image-failed", "done": True,
                    "is_error": True, "assistant_msg_idx": 1,
                }],
            },
        ],
    },
    "uiartifactcancelboundary": {
        "title": "图片取消轮次边界验收",
        "messages": [
            {"role": "user", "content": "上一轮普通问题。", "timestamp": now - 29},
            {"role": "assistant", "content": "上一轮普通回答，不应被当前取消态污染。", "timestamp": now - 28},
            {"role": "user", "content": "当前轮请生成一张图片，随后取消。", "timestamp": now - 27},
        ],
    },
    missing_id: {
        "title": "图片缺失与重试验收",
        "messages": [
            {"role": "user", "content": "请重新生成这张缺失的测试图片。", "timestamp": now - 24},
            {
                "role": "assistant", "content": "图片记录存在，但文件已不可用。",
                "timestamp": now - 23, "artifacts": [missing_artifact],
            },
        ],
    },
    historical_id: {
        "title": "历史图片非破坏重试验收",
        "messages": [
            {"role": "user", "content": "请生成一张历史蓝色科技图。", "timestamp": now - 20},
            {
                "role": "assistant", "content": "历史图片曾成功生成，但文件随后丢失。",
                "timestamp": now - 19, "artifacts": [historical_artifact],
            },
            {"role": "user", "content": "后续第一轮：请记录项目代号为甲。", "timestamp": now - 18},
            {"role": "assistant", "content": "已记录项目代号为甲。", "timestamp": now - 17},
            {"role": "user", "content": "后续第二轮：请确认项目代号。", "timestamp": now - 16},
            {"role": "assistant", "content": "项目代号仍然是甲。", "timestamp": now - 15},
        ],
    },
    scroll_id: {
        "title": "图片延迟加载滚动锚点验收",
        "messages": [
            {"role": "user", "content": "请生成一张延迟加载测试图片。", "timestamp": now - 22},
            {
                "role": "assistant", "content": "图片位于较早消息中。",
                "timestamp": now - 21, "artifacts": [scroll_artifact],
            },
        ] + [
            message
            for index in range(1, 11)
            for message in (
                {"role": "user", "content": f"滚动锚点测试消息 {index}：" + "保持当前阅读位置。" * 8, "timestamp": now - 21 + index * 2},
                {"role": "assistant", "content": f"滚动锚点测试回复 {index}：" + "此内容用于形成足够长的会话记录。" * 8, "timestamp": now - 20 + index * 2},
            )
        ],
    },
}

for session_id, fixture in fixtures.items():
    messages = fixture["messages"]
    session = Session(
        session_id=session_id,
        title=fixture["title"],
        workspace=str(workspace),
        model="fixture-model",
        model_provider="fixture",
        messages=messages,
        context_messages=[dict(message) for message in messages],
    )
    session.save()

manifest = json.loads((runtime_home / "web" / "artifacts" / missing_id / "manifest.json").read_text("utf-8"))
missing_record = next(item for item in manifest["artifacts"] if item["artifact_id"] == missing_artifact["artifact_id"])
Path(missing_record["storage_path"]).unlink()
historical_manifest = json.loads((runtime_home / "web" / "artifacts" / historical_id / "manifest.json").read_text("utf-8"))
historical_record = next(item for item in historical_manifest["artifacts"] if item["artifact_id"] == historical_artifact["artifact_id"])
Path(historical_record["storage_path"]).unlink()

print(json.dumps({
    "artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"],
    "missing_artifact_id": missing_artifact["artifact_id"],
    "historical_artifact_id": historical_artifact["artifact_id"],
    "scroll_artifact_id": scroll_artifact["artifact_id"],
    "sessions": list(fixtures),
}))
`;
  const env = {
    ...process.env,
    TAIJI_RUNTIME_HOME: runtimeHome,
    TAIJI_WEBUI_STATE_DIR: path.join(runtimeHome, "web"),
    TAIJI_WEBUI_DEFAULT_WORKSPACE: workspace,
    TAIJI_LICENSE_REQUIRED: "0",
    PYTHONPATH: [webuiDir, agentDir].join(path.delimiter),
  };
  const result = spawnSync(pythonBin, ["-c", python, runtimeHome, workspace, imageSource], {
    env,
    encoding: "utf8",
  });
  assertState(result.status === 0, "fixture persistence failed", { stderr: String(result.stderr || "").slice(-1200) });
  return JSON.parse(result.stdout);
}

async function launchDesktop({ _electron, electronBin, appDir, labDir, pythonBin, dirs }) {
  return _electron.launch({
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
}

async function readyPage(app, sessionId) {
  const page = await app.firstWindow({ timeout: 120000 });
  await page.waitForLoadState("domcontentloaded", { timeout: 120000 });
  await page.waitForFunction(
    () => location.href.includes("taiji_desktop=1") && typeof loadSession === "function" && typeof renderMessages === "function",
    { timeout: 120000 },
  );
  await page.evaluate(async sid => {
    try { localStorage.setItem("hermes-lang", "zh"); } catch (_) {}
    if (typeof setLanguage === "function") setLanguage("zh");
    const onboarding = document.getElementById("onboardingOverlay");
    if (onboarding) onboarding.remove();
    if (typeof switchPanel === "function") await switchPanel("chat");
    await loadSession(sid, { force: true });
  }, sessionId);
  await page.waitForFunction(sid => S.session && S.session.session_id === sid, sessionId);
  return page;
}

async function loadFixtureSession(page, sessionId) {
  await page.evaluate(async sid => { await loadSession(sid, { force: true }); }, sessionId);
  await page.waitForFunction(sid => S.session && S.session.session_id === sid, sessionId);
  await page.locator("#messages").evaluate(node => { node.scrollTop = node.scrollHeight; });
}

async function tabToSelector(page, selector, { reverse = false, maxSteps = 160 } = {}) {
  const key = reverse ? "Shift+Tab" : "Tab";
  for (let step = 1; step <= maxSteps; step += 1) {
    await page.keyboard.press(key);
    const matched = await page.evaluate(sel => {
      const active = document.activeElement;
      return Boolean(active && active.matches && active.matches(sel));
    }, selector);
    if (matched) return step;
  }
  const active = await page.evaluate(() => ({
    tag: document.activeElement && document.activeElement.tagName,
    id: document.activeElement && document.activeElement.id,
    className: document.activeElement && document.activeElement.className,
  }));
  throw new Error(`Natural focus order did not reach ${selector}: ${JSON.stringify(active)}`);
}

async function screenshot(page, outDir, name) {
  return captureAuditedScreenshot(page, outDir, name);
}

async function main() {
  const cli = parseArgs(process.argv.slice(2));
  const { _electron } = loadPlaywright();
  const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
  const webuiDir = path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui");
  const agentDir = path.join(repoRoot, "hermes-local-lab", "sources", "hermes-agent");
  const labDir = path.join(repoRoot, "hermes-local-lab");
  const appDir = path.join(repoRoot, "apps", "taiji-desktop");
  const electronBin = process.env.TAIJI_ELECTRON_BIN || path.join(appDir, "node_modules", "electron", "dist", "Electron.app", "Contents", "MacOS", "Electron");
  const pythonBin = process.env.TAIJI_TEST_PYTHON || path.join(agentDir, "venv", "bin", "python");
  const outDir = path.resolve(cli.outDir);
  fs.mkdirSync(outDir, { recursive: true });
  const harnessRoot = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-chat-artifact-electron-"));
  const dirs = {
    runtimeHome: path.join(harnessRoot, "runtime-fixture"),
    workspace: path.join(harnessRoot, "workspace-fixture"),
    userData: path.join(harnessRoot, "electron-user-data"),
    config: path.join(harnessRoot, "xdg-config"),
    data: path.join(harnessRoot, "xdg-data"),
    state: path.join(harnessRoot, "xdg-state"),
  };
  for (const dir of Object.values(dirs)) fs.mkdirSync(dir, { recursive: true });
  assertState(fs.existsSync(electronBin), `Electron binary not found`);
  assertState(fs.existsSync(pythonBin), `Python runtime not found`);

  const runtimeConfig = installDailyEquivalentRuntimeConfig(dirs.runtimeHome);
  const sourceFingerprint = collectSourceFingerprint({ repoRoot, webuiDir });
  const imageSource = path.join(webuiDir, "static", "assets", "taiji", "background", "background-grid.png");
  const fixture = prepareFixture({ pythonBin, webuiDir, agentDir, runtimeHome: dirs.runtimeHome, workspace: dirs.workspace, imageSource });
  const baselineElectronPids = pgrepElectron();
  const pidFiles = {
    agent: path.join(dirs.state, "taiji-agent", "logs", "agent.pid"),
    web: path.join(dirs.state, "taiji-agent", "logs", "web.pid"),
  };
  const launches = [];
  const screenshotSanity = {};
  let navigationParity = null;
  let app = null;
  try {
    app = await launchDesktop({ _electron, electronBin, appDir, labDir, pythonBin, dirs });
    let page = await readyPage(app, "uiartifactdefault");
    launches.push({ electron: app.process().pid, agent: readPid(pidFiles.agent), web: readPid(pidFiles.web) });

    navigationParity = await inspectTaijiNavigation(page);
    assertNavigationParity(navigationParity);
    assertState(
      JSON.stringify(navigationParity.ui_visibility) === JSON.stringify(runtimeConfig.feature_visibility),
      "runtime feature visibility differs from the sanitized daily-equivalent fixture",
      { expected: runtimeConfig.feature_visibility, actual: navigationParity.ui_visibility },
    );
    assertState(await page.locator("#msgInner").innerText().then(text => text.includes("当前回复中处理图片产物")), "default fixture did not render");
    screenshotSanity["01-default.png"] = await screenshot(page, outDir, "01-default.png");

    await loadFixtureSession(page, "uiartifactgenerating");
    await page.waitForSelector('.image-generation-state[data-state="loading"]', { timeout: 10000 });
    assertState((await page.locator('.image-generation-state[data-state="loading"]').getAttribute("role")) === "status", "generation state lacks status semantics");
    screenshotSanity["02-generating.png"] = await screenshot(page, outDir, "02-generating.png");

    await loadFixtureSession(page, "uiartifactsuccess");
    const image = page.locator(".chat-artifact-image").first();
    await image.waitFor({ state: "visible", timeout: 15000 });
    await page.waitForFunction(() => {
      const img = document.querySelector(".chat-artifact-image");
      return img && img.complete && img.naturalWidth > 0;
    });
    const transcript = await page.locator("#msgInner").innerText();
    assertState(transcript.includes("已识别上一轮的图片要求"), "refinement fixture did not preserve prior-turn reference");
    assertState(!(await page.locator("#msgInner").innerText()).includes("runtime-fixture"), "internal path leaked to transcript");
    await page.locator("#msg").click();
    const imageTabSteps = await tabToSelector(page, ".chat-artifact-image", { reverse: true });
    await page.keyboard.press("Space");
    await page.waitForSelector('.img-lightbox[aria-modal="true"]');
    assertState((await page.locator(":focus").getAttribute("aria-label")) === "关闭图片查看器", "lightbox did not focus close button");
    await page.keyboard.press("Escape");
    await page.waitForSelector(".img-lightbox", { state: "detached" });
    assertState(await image.evaluate(node => document.activeElement === node), "lightbox did not restore image focus");
    const download = page.locator(".msg-artifact-download").first();
    assertState((await download.getAttribute("aria-label") || "").includes("下载"), "download action lacks accessible name");
    const href = await download.getAttribute("href");
    assertState(Boolean(href && href.includes("session_id=uiartifactsuccess") && href.includes(`artifact_id=${fixture.artifact_id}`) && href.includes(`v=${fixture.sha256}`) && href.includes("download=1")), "download href is not versioned session-authorized artifact URL", { href });
    await page.evaluate(() => {
      window.__artifactDownloadKeyboardActivated = false;
      const link = document.querySelector(".msg-artifact-download");
      link.addEventListener("click", event => {
        window.__artifactDownloadKeyboardActivated = true;
        event.preventDefault();
      }, { capture: true, once: true });
    });
    const downloadTabSteps = await tabToSelector(page, ".msg-artifact-download");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => window.__artifactDownloadKeyboardActivated === true);
    const downloadResponse = await page.evaluate(async downloadHref => {
      const response = await fetch(downloadHref, { credentials: "include", cache: "no-store" });
      const bytes = (await response.arrayBuffer()).byteLength;
      return {
        status: response.status,
        disposition: response.headers.get("content-disposition") || "",
        bytes,
      };
    }, href);
    assertState(downloadResponse.status === 200 && downloadResponse.bytes > 0, "authorized artifact download request failed", downloadResponse);
    assertState(/attachment/i.test(downloadResponse.disposition), "artifact download lacks Content-Disposition attachment", { href, ...downloadResponse });
    const beforeRestartArtifact = await image.evaluate(node => ({
      src: node.getAttribute("src"),
      visible: node.offsetWidth > 0 && node.offsetHeight > 0,
      naturalWidth: node.naturalWidth,
    }));
    screenshotSanity["03-success.png"] = await screenshot(page, outDir, "03-success.png");

    await loadFixtureSession(page, "uiartifactmissing");
    await page.waitForSelector(".chat-artifact-unavailable", { timeout: 15000 });
    assertState((await page.locator(".chat-artifact-unavailable img").count()) === 0, "missing artifact left a broken image element");
    screenshotSanity["04-missing.png"] = await screenshot(page, outDir, "04-missing.png");
    await page.route("**/api/chat/start", async route => {
      const payload = JSON.parse(route.request().postData() || "{}");
      if (payload.session_id === "uiartifactmissing") {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "controlled retry smoke stop" }),
        });
        return;
      }
      await route.continue();
    });
    const retryTruncatePromise = page.waitForRequest(request => {
      if (!request.url().includes("/api/session/truncate") || request.method() !== "POST") return false;
      try { return JSON.parse(request.postData() || "{}").session_id === "uiartifactmissing"; } catch (_) { return false; }
    });
    const retryRequestPromise = page.waitForRequest(request => {
      if (!request.url().includes("/api/chat/start") || request.method() !== "POST") return false;
      try { return JSON.parse(request.postData() || "{}").session_id === "uiartifactmissing"; } catch (_) { return false; }
    });
    await page.locator("#msg").click();
    const retryTabSteps = await tabToSelector(page, ".chat-artifact-unavailable .chat-artifact-retry", { reverse: true });
    await page.keyboard.press("Enter");
    const [retryTruncateRequest, retryRequest] = await Promise.all([retryTruncatePromise, retryRequestPromise]);
    const retryTruncatePayload = JSON.parse(retryTruncateRequest.postData() || "{}");
    const retryPayload = JSON.parse(retryRequest.postData() || "{}");
    assertState(retryTruncatePayload.keep_count === 1, "retry did not truncate at the failed assistant turn", retryTruncatePayload);
    assertState(retryPayload.message.includes("缺失的测试图片"), "retry did not submit the original image request", retryPayload);
    await page.waitForFunction(() => {
      const text = document.getElementById("msgInner")?.innerText || "";
      return S.busy === false && text.includes("controlled retry smoke stop");
    });
    const retryFailureState = await page.evaluate(() => ({
      busy: S.busy,
      errorVisible: (document.getElementById("msgInner")?.innerText || "").includes("controlled retry smoke stop"),
      loadingStates: document.querySelectorAll('.image-generation-state[data-state="loading"],.thinking').length,
    }));
    assertState(!retryFailureState.busy && retryFailureState.errorVisible && retryFailureState.loadingStates === 0, "controlled retry failure left a busy/loading UI", retryFailureState);
    await page.unroute("**/api/chat/start");

    await loadFixtureSession(page, "uiartifacthistoricalretry");
    const historicalRetryButton = page.locator(".chat-artifact-unavailable .chat-artifact-retry").first();
    await historicalRetryButton.waitFor({ state: "visible", timeout: 15000 });
    const historicalRetryButtonState = {
      text: (await historicalRetryButton.innerText()).trim(),
      aria_label: await historicalRetryButton.getAttribute("aria-label"),
    };
    assertState(
      historicalRetryButtonState.text === "作为新消息重新生成"
        && historicalRetryButtonState.aria_label === "作为新消息重新生成图片",
      "historical artifact retry is not presented as a new-message action",
      historicalRetryButtonState,
    );
    const historicalSnapshot = () => page.evaluate(() => S.messages.map(message => ({
      role: String(message && message.role || ""),
      content: typeof msgContent === "function" ? msgContent(message) : String(message && message.content || ""),
      artifact_ids: Array.isArray(message && message.artifacts)
        ? message.artifacts.map(artifact => String(artifact && artifact.artifact_id || ""))
        : [],
    })));
    const historicalRetryBefore = await historicalSnapshot();
    assertState(
      historicalRetryBefore.length === 6
        && historicalRetryBefore[4].content.includes("后续第二轮")
        && historicalRetryBefore[5].content.includes("项目代号仍然是甲"),
      "historical retry fixture does not contain two complete later turns",
      historicalRetryBefore,
    );
    const historicalRetryTruncateRequests = [];
    let historicalRetryStartPayload = null;
    await page.route("**/api/session/truncate", async route => {
      let payload = {};
      try { payload = JSON.parse(route.request().postData() || "{}"); } catch (_) {}
      if (payload.session_id === "uiartifacthistoricalretry") {
        historicalRetryTruncateRequests.push(payload);
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "historical retry must not truncate" }),
        });
        return;
      }
      await route.continue();
    });
    await page.route("**/api/chat/start", async route => {
      const payload = JSON.parse(route.request().postData() || "{}");
      if (payload.session_id === "uiartifacthistoricalretry") {
        historicalRetryStartPayload = payload;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "controlled historical retry smoke stop" }),
        });
        return;
      }
      await route.continue();
    });
    const historicalRetryStartPromise = page.waitForRequest(request => {
      if (!request.url().includes("/api/chat/start") || request.method() !== "POST") return false;
      try { return JSON.parse(request.postData() || "{}").session_id === "uiartifacthistoricalretry"; } catch (_) { return false; }
    });
    await historicalRetryButton.scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    screenshotSanity["06-historical-retry-as-new-message.png"] = await screenshot(
      page,
      outDir,
      "06-historical-retry-as-new-message.png",
    );
    await historicalRetryButton.click();
    await historicalRetryStartPromise;
    await page.waitForFunction(() => {
      const text = document.getElementById("msgInner")?.innerText || "";
      return S.busy === false && text.includes("controlled historical retry smoke stop");
    });
    await page.waitForTimeout(250);
    const historicalRetryAfter = await historicalSnapshot();
    const historicalPrefixPreserved = JSON.stringify(
      historicalRetryAfter.slice(0, historicalRetryBefore.length),
    ) === JSON.stringify(historicalRetryBefore);
    assertState(
      historicalRetryTruncateRequests.length === 0,
      "historical image retry called the destructive truncate endpoint",
      historicalRetryTruncateRequests,
    );
    assertState(
      historicalRetryStartPayload
        && historicalRetryStartPayload.message === "请生成一张历史蓝色科技图。",
      "historical image retry did not submit the original prompt as a new turn",
      historicalRetryStartPayload,
    );
    assertState(
      historicalPrefixPreserved
        && historicalRetryAfter.length >= historicalRetryBefore.length,
      "historical image retry changed existing messages",
      { before: historicalRetryBefore, after: historicalRetryAfter },
    );
    await page.unroute("**/api/session/truncate");
    await page.unroute("**/api/chat/start");

    await loadFixtureSession(page, "uiartifactfailure");
    await page.waitForSelector('.image-generation-state[data-state="failed"]', { timeout: 10000 });
    assertState((await page.locator('.image-generation-state[data-state="failed"] button').getAttribute("aria-label")) === "重新生成图片", "failure retry lacks accessible name");
    screenshotSanity["05-failure.png"] = await screenshot(page, outDir, "05-failure.png");

    await loadFixtureSession(page, "uiartifactcancelboundary");
    const cancelBoundary = await page.evaluate(() => {
      S.activeStreamId = "fixture-live-image-cancel";
      appendLiveToolCard({
        event_type: "tool.started", name: "image_generate", status: "running",
        summary: "正在生成图片", tid: "fixture-live-image-cancel", done: false, is_error: false,
      });
      const events = _finalizeLiveImageGenerationStates("cancelled", "用户已取消本次生成。");
      clearLiveToolCards();
      const target = _attachImageGenerationTerminalEventsToCurrentTurn(S.messages, events);
      S.activeStreamId = null;
      renderMessages({ preserveScroll: true });
      const previous = S.messages[1];
      const keys = target && target.image_generation_events && target.image_generation_events[0]
        ? Object.keys(target.image_generation_events[0]).sort() : [];
      return {
        previousPolluted: Boolean(previous && previous.image_generation_events),
        targetIndex: target ? S.messages.indexOf(target) : -1,
        transient: Boolean(target && target._transient),
        eventKeys: keys,
        cancelledCards: document.querySelectorAll('.image-generation-state[data-state="cancelled"]').length,
        rawToolCards: document.querySelectorAll('.tool-card-row').length,
      };
    });
    assertState(!cancelBoundary.previousPolluted && cancelBoundary.targetIndex === 3 && cancelBoundary.transient, "live cancel terminal state crossed the current user boundary", cancelBoundary);
    assertState(cancelBoundary.cancelledCards === 1 && cancelBoundary.rawToolCards === 0, "live cancel did not settle as one safe image status card", cancelBoundary);
    assertState(!cancelBoundary.eventKeys.some(key => ["args", "result", "path", "token"].includes(key)), "live cancel retained a raw tool payload", cancelBoundary);
    screenshotSanity["06-cancel-boundary.png"] = await screenshot(page, outDir, "06-cancel-boundary.png");
    const cancelTransientAfterDiscard = await page.evaluate(() => {
      S.messages = _discardTransientImageTerminalMessages(S.messages);
      renderMessages({ preserveScroll: true });
      return {
        messageCount: S.messages.length,
        transientCount: S.messages.filter(message => message && message._transient).length,
        previousPolluted: Boolean(S.messages[1] && S.messages[1].image_generation_events),
      };
    });
    assertState(cancelTransientAfterDiscard.messageCount === 3 && cancelTransientAfterDiscard.transientCount === 0 && !cancelTransientAfterDiscard.previousPolluted, "transient cancel state survived the next-turn discard boundary", cancelTransientAfterDiscard);

    let delayedMediaRequests = 0;
    let delayedMediaRequestStartedAt = 0;
    let delayedMediaReleased = false;
    let resolveDelayedMediaStart;
    const delayedMediaStarted = new Promise(resolve => { resolveDelayedMediaStart = resolve; });
    await page.route("**/api/media?**", async route => {
      const url = new URL(route.request().url());
      if (url.searchParams.get("session_id") === "uiartifactscrollanchor") {
        delayedMediaRequests += 1;
        delayedMediaRequestStartedAt = Date.now();
        resolveDelayedMediaStart();
        await new Promise(resolve => setTimeout(resolve, 2500));
        delayedMediaReleased = true;
      }
      await route.continue();
    });
    await loadFixtureSession(page, "uiartifactscrollanchor");
    // Do not rely on Chromium's native `loading=lazy` distance heuristic to
    // decide whether the delayed request starts.  Explicitly expose the target
    // image once, then move it above the viewport for the scroll-anchor check.
    // This still exercises the real network/image/render path while removing a
    // platform-timing race that intermittently skipped the request entirely.
    await page.locator(".msg-artifact-image").first().scrollIntoViewIfNeeded();
    await Promise.race([
      delayedMediaStarted,
      new Promise((_, reject) => setTimeout(() => reject(new Error("lazy artifact request did not start")), 10000)),
    ]);
    await page.locator("#messages").evaluate(node => { node.scrollTop = node.scrollHeight; });
    await page.locator("#messages").hover();
    const scrollTopBeforeWheel = await page.locator("#messages").evaluate(node => node.scrollTop);
    const scrollWheelAt = Date.now();
    await page.mouse.wheel(0, -1050);
    await page.waitForFunction(start => {
      const pane = document.getElementById("messages");
      return typeof _messageUserUnpinned !== "undefined" && _messageUserUnpinned === true && pane.scrollTop < start - 500;
    }, scrollTopBeforeWheel);
    await page.waitForTimeout(100);
    const scrollAnchorBefore = await page.evaluate(() => {
      const pane = document.getElementById("messages");
      const paneRect = pane.getBoundingClientRect();
      const artifact = document.querySelector(".msg-artifact-image[data-state='loading']");
      const candidates = Array.from(document.querySelectorAll("#msgInner [data-msg-idx]"));
      const anchor = candidates.find(node => {
        const rect = node.getBoundingClientRect();
        return rect.top >= paneRect.top + 12 && rect.bottom <= paneRect.bottom - 12;
      });
      return {
        artifactPresent: Boolean(artifact),
        anchorIndex: anchor ? anchor.getAttribute("data-msg-idx") : "",
        anchorSelector: anchor ? `[data-msg-idx="${anchor.getAttribute("data-msg-idx")}"]` : "",
        anchorTop: anchor ? anchor.getBoundingClientRect().top : 0,
        artifactBottom: artifact ? artifact.getBoundingClientRect().bottom : 0,
        paneTop: paneRect.top,
        scrollTop: pane.scrollTop,
        unpinned: typeof _messageUserUnpinned !== "undefined" && _messageUserUnpinned === true,
      };
    });
    const mediaRequestPendingAtAnchor = !delayedMediaReleased;
    assertState(scrollAnchorBefore.artifactPresent, "delayed artifact is outside the rendered message window", scrollAnchorBefore);
    assertState(Boolean(scrollAnchorBefore.anchorIndex), "could not select a visible scroll anchor", scrollAnchorBefore);
    assertState(scrollAnchorBefore.artifactBottom <= scrollAnchorBefore.paneTop + 1, "delayed artifact was not above the viewport", scrollAnchorBefore);
    await page.waitForSelector(".msg-artifact-image[data-state='ready']", { timeout: 15000 });
    const scrollAnchorAfter = await page.evaluate(index => {
      const pane = document.getElementById("messages");
      const anchor = document.querySelector(`#msgInner [data-msg-idx="${CSS.escape(index)}"]`);
      return {
        anchorTop: anchor ? anchor.getBoundingClientRect().top : 0,
        scrollTop: pane.scrollTop,
      };
    }, scrollAnchorBefore.anchorIndex);
    const scrollAnchorShift = Math.abs(scrollAnchorAfter.anchorTop - scrollAnchorBefore.anchorTop);
    const scrollAnchorTolerance = 2;
    assertState(delayedMediaRequests === 1, "delayed artifact request did not use the real media route", { delayedMediaRequests });
    assertState(delayedMediaRequestStartedAt > 0 && delayedMediaRequestStartedAt <= scrollWheelAt, "wheel happened before the delayed media request started", { delayedMediaRequestStartedAt, scrollWheelAt });
    assertState(scrollAnchorBefore.unpinned && mediaRequestPendingAtAnchor, "anchor was not captured after unpinning while media was pending", { scrollAnchorBefore, mediaRequestPendingAtAnchor });
    assertState(scrollAnchorShift <= scrollAnchorTolerance, "late image load moved the user's reading anchor", { scrollAnchorBefore, scrollAnchorAfter, scrollAnchorShift, scrollAnchorTolerance });
    await page.unroute("**/api/media?**");
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.waitForTimeout(500);
    screenshotSanity["07-scroll-anchor.png"] = await screenshot(page, outDir, "07-scroll-anchor.png");

    await loadFixtureSession(page, "uiartifactsuccess");
    await page.evaluate(() => { void clearConversation(); });
    await page.waitForSelector("#appDialogOverlay[aria-hidden='false']");
    const clearText = await page.locator("#appDialogDesc").innerText();
    assertState(clearText.includes("7 天") && clearText.includes("图片"), "clear confirmation lacks 7-day artifact recovery warning");
    await page.locator("#appDialogCancel").click();

    const firstOwnedPids = launches[0];
    await app.close();
    app = null;
    const firstCleanup = await terminateOwnedPids(Object.values(firstOwnedPids));
    const baselineSignaledByCleanup = [...firstCleanup.term, ...firstCleanup.kill]
      .filter(pid => baselineElectronPids.includes(pid));
    assertState(baselineSignaledByCleanup.length === 0, "cleanup targeted a pre-existing Electron process", { baselineSignaledByCleanup });

    app = await launchDesktop({ _electron, electronBin, appDir, labDir, pythonBin, dirs });
    page = await readyPage(app, "uiartifactsuccess");
    launches.push({ electron: app.process().pid, agent: readPid(pidFiles.agent), web: readPid(pidFiles.web) });
    await page.waitForFunction(() => {
      const img = document.querySelector(".chat-artifact-image");
      return img && img.complete && img.naturalWidth > 0;
    });
    assertState((await page.locator(".chat-artifact-image").count()) === 1, "artifact did not recover after Electron restart");
    const afterRestartArtifact = await page.locator(".chat-artifact-image").evaluate(node => ({
      src: node.getAttribute("src"),
      visible: node.offsetWidth > 0 && node.offsetHeight > 0,
      naturalWidth: node.naturalWidth,
    }));
    assertState(beforeRestartArtifact.src === afterRestartArtifact.src && beforeRestartArtifact.visible && afterRestartArtifact.visible, "artifact URL or visibility changed across restart", { beforeRestartArtifact, afterRestartArtifact });
    screenshotSanity["08-restart-recovery.png"] = await screenshot(page, outDir, "08-restart-recovery.png");

    await app.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0];
      win.setMinimumSize(600, 600);
      win.setSize(640, 900, true);
    });
    await page.waitForFunction(() => window.innerWidth <= 660);
    await page.locator(".chat-artifact-image").scrollIntoViewIfNeeded();
    const narrow = await page.evaluate(() => ({
      width: window.innerWidth,
      imageRight: document.querySelector(".chat-artifact-card")?.getBoundingClientRect().right || 0,
      viewport: document.documentElement.clientWidth,
      composerTop: document.getElementById("composerWrap")?.getBoundingClientRect().top || 0,
      imageBottom: document.querySelector(".chat-artifact-card")?.getBoundingClientRect().bottom || 0,
    }));
    assertState(narrow.imageRight <= narrow.viewport + 1, "artifact overflows narrow viewport", narrow);
    screenshotSanity["09-narrow.png"] = await screenshot(page, outDir, "09-narrow.png");

    const result = {
      status: "passed_with_provider_fixture",
      screenshots: [
        "01-default.png", "02-generating.png", "03-success.png",
        "04-missing.png", "05-failure.png", "06-historical-retry-as-new-message.png",
        "06-cancel-boundary.png",
        "07-scroll-anchor.png", "08-restart-recovery.png", "09-narrow.png",
      ],
      screenshot_states: {
        "01-default.png": "default persisted conversation",
        "02-generating.png": "replayed image generation in progress",
        "03-success.png": "authorized inline artifact success",
        "04-missing.png": "missing artifact replaced by an actionable error card",
        "05-failure.png": "persisted image generation failure with retry",
        "06-historical-retry-as-new-message.png": "historical missing artifact offers a non-destructive new-turn retry",
        "06-cancel-boundary.png": "live image cancel stays on the current user turn",
        "07-scroll-anchor.png": "late image load preserves an unpinned reading anchor",
        "08-restart-recovery.png": "same artifact recovered after Electron restart",
        "09-narrow.png": "640px narrow Electron window",
      },
      provider: "safe local fixture; external image provider not invoked",
      provider_realtime_verified: false,
      persistence: "production Session + Artifact Registry + HTTP media authorization",
      artifact_id: fixture.artifact_id,
      acceptance_provenance: {
        source: sourceFingerprint,
        desktop_app_source: "isolated_worktree",
        runtime_config: runtimeConfig,
        user_data: { type: "isolated_temporary" },
        navigation: navigationParity,
      },
      screenshot_sanity: screenshotSanity,
      checks: {
        daily_configuration_navigation_parity: true,
        auditable_source_fingerprint: true,
        screenshot_sanity: true,
        lightbox_keyboard_and_focus: true,
        download_keyboard_and_accessible_name: true,
        natural_focus_order: { imageTabSteps, downloadTabSteps, retryTabSteps },
        missing_artifact_error_card: true,
        live_cancel_turn_boundary: { beforeDiscard: cancelBoundary, afterDiscard: cancelTransientAfterDiscard },
        retry_keyboard_request: {
          truncate: retryTruncatePayload,
          start: { session_id: retryPayload.session_id, message: retryPayload.message },
          settled_failure: retryFailureState,
        },
        historical_retry_as_new_message: {
          button: historicalRetryButtonState,
          truncate_requests: historicalRetryTruncateRequests.length,
          start: {
            session_id: historicalRetryStartPayload && historicalRetryStartPayload.session_id,
            message: historicalRetryStartPayload && historicalRetryStartPayload.message,
          },
          history_before_count: historicalRetryBefore.length,
          history_after_count: historicalRetryAfter.length,
          history_prefix_preserved: historicalPrefixPreserved,
        },
        late_load_scroll_anchor: {
          requestStartedBeforeWheel: delayedMediaRequestStartedAt <= scrollWheelAt,
          wheelBeforeScrollTop: scrollTopBeforeWheel,
          wheelSettledScrollTop: scrollAnchorBefore.scrollTop,
          unpinned: scrollAnchorBefore.unpinned,
          mediaRequestPendingAtAnchor,
          anchorSelector: scrollAnchorBefore.anchorSelector,
          beforeRectTop: scrollAnchorBefore.anchorTop,
          afterRectTop: scrollAnchorAfter.anchorTop,
          shift: scrollAnchorShift,
          tolerance: scrollAnchorTolerance,
          before: scrollAnchorBefore,
          after: scrollAnchorAfter,
        },
        seven_day_clear_warning: true,
        prior_turn_refinement_fixture_visible: true,
        restart_recovery: true,
        restart_artifact: { before: beforeRestartArtifact, after: afterRestartArtifact },
        narrow_layout: narrow,
      },
      pid_ownership: {
        baseline_electron_pids: baselineElectronPids,
        baseline_alive: Object.fromEntries(baselineElectronPids.map(pid => [pid, pidAlive(pid)])),
        baseline_signaled_by_cleanup: baselineSignaledByCleanup,
        launches,
      },
      cleanup: {
        first_launch: firstCleanup,
        strategy: "Electron close, then TERM and timeout KILL only for PIDs recorded from this isolated launch; isolated fixture files are removed after verification.",
      },
    };
    fs.writeFileSync(path.join(outDir, "electron-chat-artifact-result.json"), JSON.stringify(result, null, 2));
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } finally {
    if (app) await app.close().catch(() => {});
    const ownedPids = launches.flatMap(item => Object.values(item)).filter(Boolean);
    await terminateOwnedPids(ownedPids).catch(() => {});
    fs.rmSync(harnessRoot, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
