#!/usr/bin/env node
/* Real Electron acceptance for the path-free Worktree public contract. */
const fs = require("fs");
const http = require("http");
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

async function terminate(pids) {
  const owned = [...new Set(pids.filter(Boolean))];
  for (const pid of owned) if (alive(pid)) { try { process.kill(pid, "SIGTERM"); } catch (_) {} }
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline && owned.some(alive)) await new Promise(resolve => setTimeout(resolve, 150));
  for (const pid of owned) if (alive(pid)) { try { process.kill(pid, "SIGKILL"); } catch (_) {} }
}

function git(cwd, ...args) {
  const result = spawnSync("git", args, { cwd, encoding: "utf8" });
  assertState(result.status === 0, `git ${args.join(" ")} failed`, { stderr: result.stderr });
}

function prepareRepo(workspace) {
  fs.mkdirSync(workspace, { recursive: true });
  git(workspace, "init", "-q");
  git(workspace, "config", "user.email", "electron-qa@example.invalid");
  git(workspace, "config", "user.name", "Electron QA");
  fs.writeFileSync(path.join(workspace, "README.md"), "# Worktree Electron QA\n", "utf8");
  git(workspace, "add", "README.md");
  git(workspace, "commit", "-qm", "fixture");
}

async function startDeterministicChatProvider() {
  const requests = [];
  const responseChunks = [];
  const assistantText = "Worktree 会话已通过本地确定性模型完成并持久化。";
  const server = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    let payload = {};
    try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch (_) {}
    requests.push({
      method: request.method,
      url: request.url,
      model: payload.model || "",
      stream: Boolean(payload.stream),
      message_count: Array.isArray(payload.messages) ? payload.messages.length : 0,
    });
    if (request.method === "GET" && (request.url === "/v1/models" || request.url === "/models")) {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({
        object: "list",
        data: [{ id: "taiji-worktree-fixture", object: "model", owned_by: "local-fixture" }],
      }));
      return;
    }
    if (request.method !== "POST" || !request.url.endsWith("/chat/completions")) {
      response.writeHead(404, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { message: "fixture route not found" } }));
      return;
    }
    if (!payload.stream) {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({
        id: "chatcmpl-worktree-fixture",
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model: "taiji-worktree-fixture",
        choices: [{
          index: 0,
          message: { role: "assistant", content: assistantText },
          finish_reason: "stop",
        }],
      }));
      return;
    }
    response.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    });
    const event = data => {
      responseChunks.push(data);
      response.write(`data: ${JSON.stringify(data)}\n\n`);
    };
    event({
      id: "chatcmpl-worktree-fixture",
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model: "taiji-worktree-fixture",
      choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }],
    });
    event({
      id: "chatcmpl-worktree-fixture",
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model: "taiji-worktree-fixture",
      choices: [{ index: 0, delta: { content: assistantText }, finish_reason: null }],
    });
    event({
      id: "chatcmpl-worktree-fixture",
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model: "taiji-worktree-fixture",
      choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
    });
    response.end("data: [DONE]\n\n");
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  return {
    assistantText,
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    requests,
    responseChunks,
    close: () => new Promise(resolve => server.close(resolve)),
  };
}

function summarizeMessage(message) {
  if (!message || typeof message !== "object") return null;
  const summary = {
    role: String(message.role || ""),
    content: typeof message.content === "string" ? message.content : message.content,
    keys: Object.keys(message).sort(),
  };
  for (const key of ["message_id", "platform_message_id", "segments", "response_segments"]) {
    if (Object.prototype.hasOwnProperty.call(message, key)) summary[key] = message[key];
  }
  return summary;
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch (_) { return null; }
}

function collectStateDbMessages(pythonBin, dbPath, sessionId) {
  if (!fs.existsSync(dbPath)) return { exists: false, messages: [] };
  const script = [
    "import json, sqlite3, sys",
    "db, sid = sys.argv[1], sys.argv[2]",
    "conn = sqlite3.connect('file:' + db + '?mode=ro', uri=True)",
    "conn.row_factory = sqlite3.Row",
    "cols = [r['name'] for r in conn.execute('PRAGMA table_info(messages)')]",
    "wanted = [c for c in ('id','role','content','timestamp','platform_message_id','reasoning','reasoning_content','codex_message_items') if c in cols]",
    "rows = [dict(r) for r in conn.execute('SELECT ' + ','.join(wanted) + ' FROM messages WHERE session_id=? ORDER BY id', (sid,))]",
    "print(json.dumps({'columns': cols, 'messages': rows}, ensure_ascii=False))",
  ].join("; ");
  const result = spawnSync(pythonBin, ["-c", script, dbPath, sessionId], { encoding: "utf8" });
  if (result.status !== 0) {
    return { exists: true, error: String(result.stderr || result.stdout || "").trim(), messages: [] };
  }
  try { return { exists: true, ...JSON.parse(result.stdout) }; } catch (error) {
    return { exists: true, error: String(error), raw: result.stdout, messages: [] };
  }
}

async function collectDuplicateBoundaryDiagnostic({
  page,
  runtimeHome,
  pythonBin,
  sessionId,
  providerFixture,
}) {
  const sessionDir = path.join(runtimeHome, "web", "sessions");
  const sidecarPath = path.join(sessionDir, `${sessionId}.json`);
  const sidecar = readJson(sidecarPath);
  const runJournalDir = path.join(sessionDir, "_run_journal", sessionId);
  const runJournal = [];
  if (fs.existsSync(runJournalDir)) {
    for (const name of fs.readdirSync(runJournalDir).sort()) {
      const events = fs.readFileSync(path.join(runJournalDir, name), "utf8")
        .split(/\r?\n/)
        .filter(Boolean)
        .map(line => {
          try { return JSON.parse(line); } catch (_) { return { malformed: true }; }
        });
      runJournal.push({
        name,
        events: events.map(event => ({
          event: event.event || event.type || "",
          payload_keys: event.payload && typeof event.payload === "object"
            ? Object.keys(event.payload).sort()
            : [],
          text: event.payload && typeof event.payload.text === "string"
            ? event.payload.text
            : undefined,
        })),
      });
    }
  }
  const stateDb = collectStateDbMessages(
    pythonBin,
    path.join(runtimeHome, "state.db"),
    sessionId,
  );
  const domAssistantBubbles = await page.locator('.msg-row[data-role="assistant"] .msg-body').allInnerTexts();
  return {
    provider: {
      assistant_text: providerFixture.assistantText,
      requests: providerFixture.requests,
      response_chunks: providerFixture.responseChunks.map(chunk => ({
        finish_reason: chunk.choices?.[0]?.finish_reason ?? null,
        delta: chunk.choices?.[0]?.delta ?? null,
      })),
    },
    raw_sidecar: {
      exists: Boolean(sidecar),
      message_count: Array.isArray(sidecar?.messages) ? sidecar.messages.length : 0,
      messages: Array.isArray(sidecar?.messages) ? sidecar.messages.map(summarizeMessage) : [],
      context_messages: Array.isArray(sidecar?.context_messages)
        ? sidecar.context_messages.map(summarizeMessage)
        : [],
    },
    state_db: {
      ...stateDb,
      messages: (stateDb.messages || []).map(summarizeMessage),
    },
    run_journal: runJournal,
    dom: {
      assistant_bubbles: domAssistantBubbles,
    },
  };
}

function installDeterministicChatProvider(runtimeHome, provider) {
  fs.appendFileSync(
    path.join(runtimeHome, "config.yaml"),
    [
      "",
      "model:",
      "  provider: custom",
      "  default: taiji-worktree-fixture",
      `  base_url: ${provider.baseUrl}`,
      "  api_key: local-fixture-key",
      "  api_mode: chat_completions",
      "providers:",
      "  custom:",
      "    api_key: local-fixture-key",
      "custom_providers:",
      "  - name: Worktree QA Fixture",
      `    base_url: ${provider.baseUrl}`,
      "    api_key: local-fixture-key",
      "    api_mode: chat_completions",
      "    model: taiji-worktree-fixture",
      "",
    ].join("\n"),
    "utf8",
  );
}

async function waitForService(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 120000 });
  await page.waitForFunction(
    () => location.href.includes("taiji_desktop=1") && typeof newSession === "function" && S._bootReady,
    { timeout: 120000 },
  );
  await page.evaluate(async () => {
    try { localStorage.setItem("hermes-lang", "zh"); } catch (_) {}
    if (typeof setLanguage === "function") setLanguage("zh");
    const onboarding = document.getElementById("onboardingOverlay");
    if (onboarding) onboarding.remove();
    if (typeof switchPanel === "function") await switchPanel("chat");
  });
}

async function screenshot(page, outDir, name) {
  return captureAuditedScreenshot(page, outDir, name);
}

async function main() {
  const outDirArg = argument("--out-dir");
  assertState(Boolean(outDirArg), "--out-dir is required");
  const outDir = path.resolve(outDirArg);
  fs.mkdirSync(outDir, { recursive: true });
  const playwrightPath = process.env.PLAYWRIGHT_NODE_PATH || "playwright";
  const { _electron } = require(playwrightPath);
  const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
  const mainRepo = path.resolve(repoRoot, "..", "..");
  const labDir = path.join(repoRoot, "hermes-local-lab");
  const appDir = path.join(repoRoot, "apps", "taiji-desktop");
  const agentDir = path.join(repoRoot, "hermes-local-lab", "sources", "hermes-agent");
  const electronBin = process.env.TAIJI_ELECTRON_BIN || path.join(
    mainRepo, "apps", "taiji-desktop", "node_modules", "electron", "dist",
    "Electron.app", "Contents", "MacOS", "Electron",
  );
  const pythonBin = process.env.TAIJI_TEST_PYTHON || path.join(agentDir, ".venv", "bin", "python");
  assertState(fs.existsSync(electronBin), "Electron binary missing", { electronBin });
  assertState(fs.existsSync(pythonBin), "Python runtime missing", { pythonBin });

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-worktree-electron-"));
  const dirs = {
    runtimeHome: path.join(root, "runtime"),
    workspace: path.join(root, "customer-repo"),
    userData: path.join(root, "user-data"),
    config: path.join(root, "config"),
    data: path.join(root, "data"),
    state: path.join(root, "state"),
  };
  for (const directory of Object.values(dirs)) fs.mkdirSync(directory, { recursive: true });
  const runtimeConfig = installDailyEquivalentRuntimeConfig(dirs.runtimeHome, {
    capability_overrides: { composer: { workspace_switcher: true } },
  });
  const sourceFingerprint = collectSourceFingerprint({ repoRoot, webuiDir: path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui") });
  prepareRepo(dirs.workspace);

  const pidFiles = [
    path.join(dirs.state, "taiji-agent", "logs", "agent.pid"),
    path.join(dirs.state, "taiji-agent", "logs", "web.pid"),
  ];
  const observed = { requests: [], responses: [] };
  const interesting = /\/api\/(?:session(?:\/worktree\/(?:status|remove)|\/new)?|sessions|chat\/start|terminal\/start|list)(?:\?|$)/;
  let app;
  let providerFixture = null;
  let appPid = 0;
  let servicePids = [];
  let worktreePath = "";
  let resultPayload = null;
  let navigationParity = null;
  const screenshotSanity = {};
  try {
    providerFixture = await startDeterministicChatProvider();
    installDeterministicChatProvider(dirs.runtimeHome, providerFixture);
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
    appPid = app.process().pid;
    const page = await app.firstWindow({ timeout: 120000 });
    page.on("request", request => {
      if (interesting.test(new URL(request.url()).pathname)) {
        observed.requests.push({ url: request.url(), method: request.method(), body: request.postData() || "" });
      }
    });
    page.on("response", async response => {
      if (!interesting.test(new URL(response.url()).pathname)) return;
      let body = "";
      try { body = await response.text(); } catch (_) {}
      observed.responses.push({ url: response.url(), status: response.status(), body });
    });
    await waitForService(page);
    servicePids = pidFiles.map(readPid).filter(Boolean);
    navigationParity = await inspectTaijiNavigation(page);
    assertNavigationParity(navigationParity);
    assertState(
      JSON.stringify(navigationParity.ui_visibility) === JSON.stringify(runtimeConfig.feature_visibility),
      "runtime feature visibility differs from the sanitized daily-equivalent fixture",
      { expected: runtimeConfig.feature_visibility, actual: navigationParity.ui_visibility },
    );

    const chip = page.locator("#composerWorkspaceChip");
    await chip.waitFor({ state: "visible", timeout: 20000 });
    assertState(!(await chip.isDisabled()), "ordinary default workspace switcher is disabled");
    await chip.click();
    const createWorktree = page.locator("#composerWsDropdown .ws-opt-action").filter({ hasText: "在 worktree 中新建对话" });
    await createWorktree.waitFor({ state: "visible", timeout: 20000 });
    await createWorktree.click();
    await page.waitForFunction(() => S.session && S.session.is_worktree === true, { timeout: 60000 });
    const worktreeSession = await page.evaluate(() => ({ ...S.session }));
    const worktreeSid = worktreeSession.session_id;
    assertState(Boolean(worktreeSid), "worktree session id missing");
    assertState(!Object.prototype.hasOwnProperty.call(worktreeSession, "workspace"), "worktree workspace leaked into browser state", worktreeSession);
    assertState(await page.evaluate(() => chatRequestWorkspace() === undefined), "chat workspace was not delegated to server");

    const worktreeRoot = path.join(dirs.workspace, ".worktrees");
    const worktreeNames = fs.readdirSync(worktreeRoot).filter(name => fs.statSync(path.join(worktreeRoot, name)).isDirectory());
    assertState(worktreeNames.length === 1, "expected one generated worktree", { worktreeNames });
    assertState(!/hermes/i.test(worktreeNames[0]), "new Worktree basename still exposes the Hermes identity", { worktreeNames });
    assertState(/^taiji-[a-f0-9]{8}$/i.test(worktreeNames[0]), "new Worktree basename does not use the Taiji product prefix", { worktreeNames });
    worktreePath = fs.realpathSync(path.join(worktreeRoot, worktreeNames[0]));
    const canaryHash = crypto.createHash("sha256").update(worktreePath).digest("hex");

    await page.evaluate(async () => { await window.taijiHomeRefreshSessions(); });
    const row = page.locator(`.taiji-session-row[data-session-id="${worktreeSid}"]`);
    await row.waitFor({ state: "visible", timeout: 20000 });
    const visibleBadge = row.locator(".taiji-session-worktree");
    const visibleSessionSnapshot = await page.evaluate(async sid => {
      const payload = await api('/api/sessions');
      return (payload.sessions || []).find(session => session.session_id === sid) || null;
    }, worktreeSid);
    assertState(await visibleBadge.isVisible(), "visible Taiji recent-list Worktree badge missing", {
      public_session: visibleSessionSnapshot,
      row_html: await row.innerHTML(),
    });
    assertState((await visibleBadge.getAttribute("aria-label")).startsWith("Worktree："), "Worktree badge has no accessible label");
    assertState(await page.evaluate(sid => _worktreeSessionCount([sid]) === 1, worktreeSid), "worktree count did not use public identity");
    assertState(await chip.isDisabled(), "worktree workspace switcher must be display-only");
    screenshotSanity["01-created-badge.png"] = await screenshot(page, outDir, "01-created-badge.png");

    const chatResponse = page.waitForResponse(response => (
      new URL(response.url()).pathname.endsWith("/api/chat/start")
      && response.request().method() === "POST"
    ));
    await page.locator("#msg").fill("Worktree 聊天入口验收");
    await page.locator("#btnSend").click();
    await chatResponse;
    await page.waitForFunction(
      expected => S.busy === false && document.querySelector("#msgInner").innerText.includes(expected),
      providerFixture.assistantText,
      { timeout: 120000 },
    );
    const chatPayload = observed.requests
      .filter(item => new URL(item.url).pathname.endsWith("/api/chat/start"))
      .map(item => {
        try { return JSON.parse(item.body || "{}"); } catch (_) { return null; }
      })
      .find(Boolean);
    assertState(chatPayload && chatPayload.session_id === worktreeSid, "chat used wrong session", chatPayload);
    assertState(!Object.prototype.hasOwnProperty.call(chatPayload, "workspace"), "chat request exposed or rebound worktree workspace", chatPayload);
    const providerChatPosts = providerFixture.requests.filter(item => (
      item.method === "POST" && item.url.endsWith("/chat/completions")
    ));
    assertState(
      providerChatPosts.length === 1,
      "real chat chain did not call the deterministic local provider exactly once",
      providerFixture.requests,
    );
    const duplicateBoundaryDiagnostic = await collectDuplicateBoundaryDiagnostic({
      page,
      runtimeHome: dirs.runtimeHome,
      pythonBin,
      sessionId: worktreeSid,
      providerFixture,
    });
    const persistedBeforeRemoval = await page.evaluate(async sid => (
      await api(`/api/session?session_id=${encodeURIComponent(sid)}&messages=1`)
    ).session, worktreeSid);
    duplicateBoundaryDiagnostic.api_session = {
      message_count: persistedBeforeRemoval.message_count,
      messages: (persistedBeforeRemoval.messages || []).map(summarizeMessage),
    };
    fs.writeFileSync(
      path.join(outDir, "duplicate-boundary-diagnostic.json"),
      JSON.stringify(duplicateBoundaryDiagnostic, null, 2),
    );
    assertState(persistedBeforeRemoval.message_count >= 2, "real chat turn was not persisted before Worktree removal", persistedBeforeRemoval);
    const persistedAssistantsBeforeRemoval = persistedBeforeRemoval.messages.filter(
      message => message.role === "assistant",
    );
    const persistedAssistantBeforeRemoval = persistedAssistantsBeforeRemoval[0];
    const liveAssistantBubbles = (await page.locator('.msg-row[data-role="assistant"] .msg-body').allInnerTexts())
      .map(text => text.trim())
      .filter(Boolean);
    assertState(
      persistedBeforeRemoval.messages.some(message => message.role === "user" && message.content.includes("Worktree 聊天入口验收"))
      && persistedAssistantsBeforeRemoval.length === 1
      && persistedAssistantBeforeRemoval
      && persistedAssistantBeforeRemoval.content === providerFixture.assistantText,
      "persisted transcript is missing the real user/assistant turn",
      {
        expected_assistant: providerFixture.assistantText,
        actual_assistant: persistedAssistantBeforeRemoval && persistedAssistantBeforeRemoval.content,
        provider_requests: providerFixture.requests,
        persisted: persistedBeforeRemoval,
      },
    );
    assertState(
      liveAssistantBubbles.length === 1
      && liveAssistantBubbles[0] === providerFixture.assistantText,
      "live DOM assistant bubble is not exactly one copy",
      {
        expected_assistant: providerFixture.assistantText,
        assistant_bubbles: liveAssistantBubbles,
      },
    );

    await page.evaluate(async () => { await toggleComposerTerminal(true); });
    await page.waitForFunction(() => TERMINAL_UI.open === true, { timeout: 30000 });
    await page.waitForFunction(
      () => Boolean(document.querySelector("#terminalSurface .xterm-rows")?.innerText.trim()),
      { timeout: 30000 },
    );
    const terminalVisibleText = await page.locator("#composerTerminalPanel").innerText();
    assertState(!/hermes/i.test(terminalVisibleText), "terminal-visible text exposes the Hermes identity", { terminalVisibleText });
    await page.evaluate(async () => { openWorkspacePanel("browse"); await loadDir("."); });
    await page.waitForFunction(() => (S.entries || []).some(entry => entry.name === "README.md"), { timeout: 30000 });
    assertState(await page.locator("#fileTree").innerText().then(text => text.includes("README.md")), "file panel did not resolve the session workspace");
    screenshotSanity["02-chat-terminal-files.png"] = await screenshot(page, outDir, "02-chat-terminal-files.png");
    await page.evaluate(async () => { await closeComposerTerminal(); });
    await page.waitForFunction(() => TERMINAL_UI.open === false, { timeout: 30000 });

    await page.evaluate(async () => { await newSession(false); });
    await page.waitForFunction(() => S.session && S.session.is_worktree === false, { timeout: 30000 });
    const plainSession = await page.evaluate(() => ({ ...S.session }));
    assertState(
      fs.realpathSync(plainSession.workspace) === fs.realpathSync(dirs.workspace),
      "ordinary workspace contract regressed",
      plainSession,
    );
    assertState(await page.evaluate(() => chatRequestWorkspace()), "ordinary chat workspace disappeared");
    await page.evaluate(async sid => { await loadSession(sid, { force: true }); }, worktreeSid);
    await page.waitForFunction(sid => S.session && S.session.session_id === sid && S.session.is_worktree, worktreeSid);
    await page.evaluate(async () => { await window.taijiHomeRefreshSessions(); });

    await row.locator(".taiji-session-more").click();
    const removeAction = page.locator(".taiji-session-action-menu .taiji-session-action-menu-item").filter({ hasText: "移除 Worktree" });
    await removeAction.waitFor({ state: "visible", timeout: 10000 });
    assertState(!(await removeAction.innerText()).includes(worktreePath), "visible remove action leaked path");
    await removeAction.click();
    await page.locator("#appDialogOverlay").waitFor({ state: "visible", timeout: 20000 });
    await page.setViewportSize({ width: 640, height: 900 });
    const firstConfirmText = await page.locator("#appDialog").innerText();
    assertState(!firstConfirmText.includes(worktreePath), "remove confirmation leaked generated path");
    assertState(firstConfirmText.includes("Worktree 标识："), "remove confirmation still labels the public identifier as a path", { firstConfirmText });
    assertState(await page.locator("#appDialogConfirm").evaluate(node => node.classList.contains("danger")), "remove confirmation did not use the existing danger token");
    const dangerStyle = await page.locator("#appDialogConfirm").evaluate(node => {
      const style = getComputedStyle(node);
      return { color: style.color, backgroundColor: style.backgroundColor, borderColor: style.borderColor };
    });
    assertState(dangerStyle.color !== "rgb(255, 255, 255)", "Taiji skin still overrides the danger token with primary-button styling", dangerStyle);
    assertState((await page.locator("#appDialogCancel").evaluate(node => document.activeElement === node)), "cancel did not receive safe default focus");
    screenshotSanity["03-narrow-remove-confirm.png"] = await screenshot(page, outDir, "03-narrow-remove-confirm.png");
    await page.keyboard.press("Escape");
    await page.locator("#appDialogOverlay").waitFor({ state: "hidden", timeout: 10000 });
    assertState(fs.existsSync(worktreePath), "cancel removed worktree");
    assertState(await page.evaluate(() => S.session.is_worktree === true), "cancel cleared worktree state");

    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(async () => { await window.taijiHomeRefreshSessions(); });
    const moreAction = row.locator(".taiji-session-more");
    await moreAction.focus();
    await page.keyboard.press("Enter");
    const keyboardRemoveAction = page.locator(".taiji-session-action-menu .taiji-session-action-menu-item").filter({ hasText: "移除 Worktree" });
    await keyboardRemoveAction.waitFor({ state: "visible", timeout: 10000 });
    await keyboardRemoveAction.focus();
    await page.keyboard.press("Enter");
    await page.locator("#appDialogOverlay").waitFor({ state: "visible", timeout: 20000 });
    await page.keyboard.press("Tab");
    assertState(await page.locator("#appDialogConfirm").evaluate(node => document.activeElement === node), "keyboard did not reach remove confirmation");
    await page.keyboard.press("Enter");
    await page.locator("#appDialogOverlay").waitFor({ state: "hidden", timeout: 30000 });
    await page.waitForFunction(() => S.session && S.session.is_worktree === false, { timeout: 30000 });
    assertState(!fs.existsSync(worktreePath), "confirmed removal left worktree directory");
    await page.evaluate(async sid => { await loadSession(sid, { force: true }); }, worktreeSid);
    await page.waitForFunction(sid => S.session && S.session.session_id === sid, worktreeSid);
    const refreshed = await page.evaluate(() => ({ ...S.session }));
    assertState(refreshed.is_worktree === false, "refreshed session resurrected worktree state", refreshed);
    assertState(
      fs.realpathSync(refreshed.workspace) === fs.realpathSync(dirs.workspace),
      "removed worktree session did not safely rebind",
      refreshed,
    );
    await page.evaluate(async () => { await window.taijiHomeRefreshSessions(); });
    const persistedAfterRemoval = await page.evaluate(async sid => {
      const [sessionPayload, sessionsPayload] = await Promise.all([
        api(`/api/session?session_id=${encodeURIComponent(sid)}&messages=1`),
        api("/api/sessions"),
      ]);
      return {
        session: sessionPayload.session,
        listed: (sessionsPayload.sessions || []).find(item => item.session_id === sid) || null,
      };
    }, worktreeSid);
    assertState(Boolean(persistedAfterRemoval.listed), "removed Worktree conversation disappeared from the server session list", persistedAfterRemoval);
    assertState(
      persistedAfterRemoval.session.message_count === persistedBeforeRemoval.message_count,
      "Worktree removal changed the persisted transcript size",
      { before: persistedBeforeRemoval, after: persistedAfterRemoval },
    );
    const persistedAssistantsAfterRemoval = (persistedAfterRemoval.session.messages || []).filter(
      message => message.role === "assistant",
    );
    assertState(
      persistedAssistantsAfterRemoval.length === 1
      && persistedAssistantsAfterRemoval[0].content === providerFixture.assistantText,
      "Worktree removal changed or duplicated the persisted assistant reply",
      persistedAfterRemoval,
    );
    const retainedRow = page.locator(`.taiji-session-row[data-session-id="${worktreeSid}"]`);
    await retainedRow.waitFor({ state: "visible", timeout: 20000 });
    assertState(!(await retainedRow.locator(".taiji-session-worktree").count()), "visible Worktree badge resurrected after refresh");
    await page.evaluate(async () => { await newSession(false); await window.taijiHomeRefreshSessions(); });
    await retainedRow.waitFor({ state: "visible", timeout: 20000 });
    await retainedRow.click();
    await page.waitForFunction(
      ({ sid, assistantText }) => (
        S.session && S.session.session_id === sid
        && document.querySelector("#msgInner").innerText.includes(assistantText)
      ),
      { sid: worktreeSid, assistantText: providerFixture.assistantText },
      { timeout: 30000 },
    );
    const reloadedAssistantBubbles = (await page.locator('.msg-row[data-role="assistant"] .msg-body').allInnerTexts())
      .map(text => text.trim())
      .filter(Boolean);
    assertState(
      reloadedAssistantBubbles.length === 1
      && reloadedAssistantBubbles[0] === providerFixture.assistantText,
      "reloaded DOM assistant bubble is not exactly one copy",
      {
        expected_assistant: providerFixture.assistantText,
        assistant_bubbles: reloadedAssistantBubbles,
      },
    );
    screenshotSanity["04-removed-refreshed.png"] = await screenshot(page, outDir, "04-removed-refreshed.png");

    await new Promise(resolve => setTimeout(resolve, 500));
    const dom = await page.locator("html").innerText();
    const observedText = JSON.stringify(observed);
    const terminalStart = observed.responses.filter(item => new URL(item.url).pathname.endsWith("/api/terminal/start"));
    const worktreeStatus = observed.responses.filter(item => new URL(item.url).pathname.endsWith("/api/session/worktree/status"));
    const worktreeRemove = observed.responses.filter(item => new URL(item.url).pathname.endsWith("/api/session/worktree/remove"));
    assertState(terminalStart.length >= 1 && terminalStart.every(item => !item.body.includes("workspace")), "terminal response exposed workspace", terminalStart);
    assertState(worktreeStatus.length >= 2, "worktree status was not exercised", worktreeStatus);
    assertState(worktreeRemove.some(item => item.status === 200), "worktree remove success response missing", worktreeRemove);
    assertState(!dom.includes(worktreePath), "DOM contains generated worktree path");
    assertState(!observedText.includes(worktreePath), "API request/response contains generated worktree path");

    resultPayload = {
      status: "passed",
      worktree_canary_sha256: canaryHash,
      session_id: worktreeSid,
      screenshots: [
        "01-created-badge.png",
        "02-chat-terminal-files.png",
        "03-narrow-remove-confirm.png",
        "04-removed-refreshed.png",
      ],
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
        public_session_workspace_omitted: true,
        badge_and_count: true,
        chat_request_workspace_omitted: true,
        terminal_entry_and_path_free_response: true,
        terminal_visible_identity_has_no_hermes: true,
        file_panel_server_resolution: true,
        ordinary_workspace_preserved: true,
        remove_cancel_preserved_state: true,
        remove_keyboard_confirmed: true,
        remove_refresh_did_not_resurrect: true,
        removed_conversation_still_discoverable: true,
        provider_chat_completions_post_exactly_once: true,
        api_assistant_content_exactly_once: true,
        live_dom_assistant_content_exactly_once: true,
        reloaded_dom_assistant_content_exactly_once: true,
        narrow_dialog: true,
        dom_worktree_path_matches: 0,
        api_worktree_path_matches: 0,
      },
      api_observation_counts: {
        requests: observed.requests.length,
        responses: observed.responses.length,
        terminal_start: terminalStart.length,
        worktree_status: worktreeStatus.length,
        worktree_remove: worktreeRemove.length,
        local_provider_requests: providerFixture.requests.length,
      },
      transcript_counts: {
        before_worktree_remove: persistedBeforeRemoval.message_count,
        after_worktree_remove: persistedAfterRemoval.session.message_count,
      },
    };
    fs.writeFileSync(path.join(outDir, "electron-worktree-public-contract-result.json"), JSON.stringify(resultPayload, null, 2));
    process.stdout.write(`${JSON.stringify(resultPayload, null, 2)}\n`);
  } finally {
    if (app) { try { await app.close(); } catch (_) {} }
    servicePids = [...new Set([...servicePids, ...pidFiles.map(readPid).filter(Boolean)])];
    await terminate([appPid, ...servicePids]);
    if (providerFixture) await providerFixture.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
