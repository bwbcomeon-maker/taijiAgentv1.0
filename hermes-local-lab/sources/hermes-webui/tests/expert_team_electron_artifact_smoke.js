#!/usr/bin/env node
/*
 * Electron smoke for expert-team artifact actions.
 *
 * This launches the desktop shell, not a standalone browser tab. Set
 * PLAYWRIGHT_NODE_PATH to a local playwright package path when the repo does
 * not install Playwright as a Node dependency.
 */
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  const candidate = process.env.PLAYWRIGHT_NODE_PATH || "playwright";
  return require(candidate);
}

const { _electron } = loadPlaywright();

const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
const appDir = path.join(repoRoot, "apps", "taiji-desktop");
const labDir = path.join(repoRoot, "hermes-local-lab");
const electronBin = path.join(
  appDir,
  "node_modules",
  "electron",
  "dist",
  "Electron.app",
  "Contents",
  "MacOS",
  "Electron"
);
const outDir = path.join(repoRoot, "output", "playwright");

function assertState(condition, message, detail) {
  if (!condition) {
    const suffix = detail ? `\n${JSON.stringify(detail, null, 2)}` : "";
    throw new Error(`${message}${suffix}`);
  }
}

async function prepareSession(page, workspace) {
  await page.evaluate(async ({ workspace }) => {
    document.documentElement.dataset.taijiDesktop = "1";
    document.documentElement.dataset.skin = "taiji-light-glass";
    window._uiVisibility = Object.assign({}, window._uiVisibility || {}, {
      composer: {
        profile: true,
        workspace_files: true,
        workspace_switcher: true,
        model: true,
        reasoning: true,
        toolsets: true,
        quota: true,
      },
    });
    if (typeof applyUiVisibility === "function") applyUiVisibility();

    const response = await fetch("/api/session/new", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace }),
    });
    if (!response.ok) {
      throw new Error(`session/new failed ${response.status}: ${await response.text()}`);
    }
    const payload = await response.json();
    if (!payload.session || !payload.session.session_id) {
      throw new Error(`session/new returned no session: ${JSON.stringify(payload)}`);
    }
    S.session = payload.session;
    S.messages = [{ role: "assistant", content: "专家团生成结果已写入当前对话。" }];
    if (typeof syncTopbar === "function") syncTopbar();
    if (typeof renderMessages === "function") renderMessages();
    if (typeof switchPanel === "function") await switchPanel("chat");
    if (typeof loadDir === "function") {
      try { await loadDir("."); } catch (_) {}
    }
  }, { workspace });
}

async function renderExpertCard(page, artifact) {
  await page.evaluate(({ artifact }) => {
    const base = {
      type: "expert-team",
      kind: "expert_team",
      runId: `expert-team-electron-${artifact.kind || "file"}`,
      sessionId: S.session.session_id,
      sourceSessionId: S.session.session_id,
      team: { id: "content-creator-team", title: "内容创作专家团" },
      subtitle: "Electron app 端产物入口验收",
      status: "done",
      statusLabel: "已完成",
      phase: "交付",
      phases: ["需求确认", "生成初稿", "打磨发布", "交付"],
      progress: { done: 4, total: 4 },
      members: [
        { id: "flow", name: "流程编排", status: "done" },
        { id: "writer", name: "文案创作专家", status: "done" },
        { id: "image", name: "配图专家", status: "done" },
        { id: "review", name: "审稿润色", status: "done" },
      ],
      tasks: [
        { id: "direction", title: "需求确认", worker_name: "流程编排", status: "done", status_label: "完成" },
        { id: "draft", title: "撰写公众号长文", worker_name: "文案创作专家", status: "done", status_label: "完成" },
        { id: "image", title: "生成封面和文中配图", worker_name: "配图专家", status: "done", status_label: "完成" },
        { id: "delivery", title: "交付整理", worker_name: "审稿润色", status: "done", status_label: "完成" },
      ],
      artifacts: [artifact],
      questions: [],
    };
    renderWriteflowStatusDock(base);
    if (typeof focusExpertTeamBottomDock === "function") focusExpertTeamBottomDock(null);
  }, { artifact });
}

async function main() {
  assertState(fs.existsSync(electronBin), `Electron binary not found: ${electronBin}`);
  fs.mkdirSync(outDir, { recursive: true });
  const tmpRoot = fs.mkdtempSync(path.join(outDir, "electron-artifact-runtime-"));
  const workspace = path.join(tmpRoot, "workspace");
  fs.mkdirSync(path.join(workspace, "articles"), { recursive: true });
  fs.writeFileSync(
    path.join(workspace, "articles", "expert-team-electron.md"),
    "# 专家团生成结果\n\n这是 Electron app 端验收文件。\n",
    "utf-8"
  );

  const env = {
    ...process.env,
    TAIJI_AGENT_ROOT: labDir,
    TAIJI_AGENT_USE_USER_DIRS: "1",
    TAIJI_DESKTOP_USER_DATA_DIR: path.join(tmpRoot, "electron-user-data"),
    XDG_CONFIG_HOME: path.join(tmpRoot, "config"),
    XDG_DATA_HOME: path.join(tmpRoot, "data"),
    XDG_STATE_HOME: path.join(tmpRoot, "state"),
    AGENT_API_PORT: "19942",
    API_SERVER_PORT: "19942",
    WEBUI_PORT: "19987",
    TAIJI_WEBUI_PORT: "19987",
    TAIJI_LICENSE_REQUIRED: "0",
    TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: "0",
  };

  const app = await _electron.launch({
    executablePath: electronBin,
    args: [appDir],
    env,
    timeout: 90000,
  });
  let page;
  try {
    page = await app.firstWindow({ timeout: 90000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 90000 });
    await page.waitForFunction(
      () => (
        location.href.includes("taiji_desktop=1") &&
        typeof renderWriteflowStatusDock === "function" &&
        typeof openExpertTeamChatDelivery === "function"
      ),
      { timeout: 90000 }
    );

    const shellState = await page.evaluate(() => ({
      href: location.href,
      desktop: document.documentElement.dataset.taijiDesktop,
      uiScript: Array.from(document.scripts).map((script) => script.src).find((src) => src.includes("/static/ui.js")) || "",
      hasChatDeliveryHandler: typeof openExpertTeamChatDelivery === "function",
    }));
    assertState(shellState.desktop === "1", "Electron shell did not load desktop mode", shellState);
    assertState(shellState.uiScript.includes("taiji-shell-31"), "Electron shell loaded stale ui.js cache token", shellState);
    assertState(shellState.hasChatDeliveryHandler, "Electron shell did not load chat delivery handler", shellState);

    await prepareSession(page, workspace);
    await renderExpertCard(page, {
      id: "draft",
      label: "专家团生成结果",
      path: "articles/expert-team-electron.md",
      kind: "md",
      exists: true,
      openable: true,
      download_name: "专家团生成结果.md",
    });
    await page.click("#writeflowStatusDock .expert-team-panel-artifact-open");
    await page.waitForFunction(
      () => {
        const preview = document.getElementById("previewArea");
        const pathText = document.getElementById("previewPathText");
        return preview && preview.classList.contains("visible") &&
          pathText && pathText.textContent.includes("expert-team-electron.md");
      },
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(outDir, "expert-team-electron-file-artifact.png"), fullPage: true });

    await renderExpertCard(page, {
      id: "expert-team-chat-delivery",
      label: "专家团生成结果",
      path: "",
      kind: "chat",
      exists: true,
      openable: false,
      note: "已写入当前对话",
    });
    const chatState = await page.evaluate(() => {
      const card = document.querySelector("#writeflowStatusDock .status-card-writeflow");
      const priority = document.querySelector("#writeflowStatusDock .expert-team-panel-priority-card.artifact");
      const button = priority && priority.querySelector(".expert-team-panel-artifact-open");
      return {
        expanded: Boolean(card && card.classList.contains("is-expanded")),
        text: priority ? priority.textContent.replace(/\s+/g, " ").trim() : "",
        buttonText: button ? button.textContent.replace(/\s+/g, " ").trim() : "",
        buttonDisabled: Boolean(button && button.disabled),
        buttonChatDelivery: Boolean(button && button.dataset.expertTeamChatDelivery === "1"),
      };
    });
    assertState(chatState.expanded, "Chat delivery card is not expanded", chatState);
    assertState(chatState.text.includes("已写入当前对话"), "Chat delivery is not labeled as conversation result", chatState);
    assertState(!chatState.text.includes("1 个可打开"), "Chat delivery is still presented as openable", chatState);
    assertState(chatState.buttonText.includes("查看对话结果"), "Chat delivery button has the wrong label", chatState);
    assertState(!chatState.buttonDisabled && chatState.buttonChatDelivery, "Chat delivery button is not actionable", chatState);
    await page.click("#writeflowStatusDock .expert-team-panel-artifact-open");
    await page.waitForFunction(
      () => {
        const card = document.querySelector("#writeflowStatusDock .status-card-writeflow");
        const toast = document.getElementById("toast");
        return card && card.classList.contains("is-collapsed") &&
          toast && (toast.dataset.toastMessage || "").includes("专家团结果已写入当前对话");
      },
      { timeout: 5000 }
    );
    const compactChatState = await page.evaluate(() => {
      const summary = document.querySelector("#writeflowStatusDock .status-card-expert-dock-summary");
      return {
        text: summary ? summary.textContent.replace(/\s+/g, " ").trim() : "",
      };
    });
    assertState(compactChatState.text.includes("查看结果"), "Compact chat delivery summary should say 查看结果", compactChatState);
    assertState(!compactChatState.text.includes("查看产物"), "Compact chat delivery summary still says 查看产物", compactChatState);
    await page.screenshot({ path: path.join(outDir, "expert-team-electron-chat-delivery.png"), fullPage: true });
  } finally {
    await app.close().catch(() => {});
  }
  console.log("EXPERT TEAM ELECTRON ARTIFACT SMOKE PASSED");
  console.log(`workspace=${workspace}`);
}

main().catch((error) => {
  console.error("EXPERT TEAM ELECTRON ARTIFACT SMOKE FAILED");
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
