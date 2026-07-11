#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const DRIVER = path.join(__dirname, "run-installed-electron-acceptance.js");

test("desktop acceptance driver exists", () => {
  assert.equal(fs.existsSync(DRIVER), true);
});

const {
  APP_DIR,
  CdpClient,
  DESKTOP_ENTRY,
  ELECTRON_PATH,
  PROBE_PROMPT,
  attachFixtureThroughVisibleChooser,
  buildDriverResult,
  buildElectronArgs,
  buildInstalledAcceptanceEnv,
  buildProbeCode,
  completionSnapshotPassed,
  filterUnexpectedHttpFailures,
  filterUnexpectedJsErrors,
  isExpectedBackgroundConsoleError,
  isExpectedDesktopHttpFailure,
  insertTextThroughVisibleComposer,
  normalizeMessageContent,
  managedProcessArgvMatches,
  parseArgs,
  parsePid,
  physicalClickVisibleElement,
  redactDesktopUrl,
  supportBundleIsSafe,
  terminateManagedProcess,
  validateDesktopTarget,
} = require("./run-installed-electron-acceptance.js");

class FakeWebSocket extends EventTarget {
  constructor(responder) {
    super();
    this.readyState = WebSocket.OPEN;
    this.responder = responder;
    this.sent = [];
  }

  send(raw) {
    const request = JSON.parse(raw);
    this.sent.push(request);
    const response = this.responder(request);
    if (response) {
      queueMicrotask(() => this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(response) })));
    }
  }

  close() {
    this.readyState = WebSocket.CLOSED;
    this.dispatchEvent(new Event("close"));
  }
}

function validArgv(overrides = {}) {
  return [
    "--electron", overrides.electron || ELECTRON_PATH,
    "--app-dir", overrides.appDir || APP_DIR,
    "--output-dir", overrides.outputDir || "/tmp/taiji-target-acceptance",
    "--session-id", overrides.sessionId || "1".repeat(32),
    "--challenge", overrides.challenge || "2".repeat(64),
    "--timeout-ms", String(overrides.timeoutMs || 600000),
  ];
}

test("parseArgs accepts only the fixed installed Electron and App paths", () => {
  const args = parseArgs(validArgv());
  assert.equal(args.electron, ELECTRON_PATH);
  assert.equal(args.appDir, APP_DIR);
  assert.equal(args.outputDir, "/tmp/taiji-target-acceptance");
  assert.equal(args.sessionId, "1".repeat(32));
  assert.equal(args.challenge, "2".repeat(64));
  assert.equal(args.timeoutMs, 600000);
});

test("parseArgs rejects alternate executables, relative output and unknown flags", () => {
  assert.throws(() => parseArgs(validArgv({ electron: "/tmp/electron" })), /fixed installed Electron path/);
  assert.throws(() => parseArgs(validArgv({ outputDir: "relative/evidence" })), /absolute path/);
  assert.throws(() => parseArgs([...validArgv(), "--headless", "1"]), /unknown argument/);
});

test("parseArgs rejects malformed identity fields and duplicate flags", () => {
  assert.throws(() => parseArgs(validArgv({ sessionId: "ABC" })), /session-id/);
  assert.throws(() => parseArgs(validArgv({ challenge: "f".repeat(63) })), /challenge/);
  assert.throws(() => parseArgs([...validArgv(), "--timeout-ms", "900000"]), /duplicate argument/);
});

test("buildElectronArgs enables loopback CDP before the fixed App directory", () => {
  assert.deepEqual(buildElectronArgs(49123), [
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=49123",
    APP_DIR,
  ]);
  assert.throws(() => buildElectronArgs(0), /CDP port/);
});

test("installed acceptance environment removes development/runtime selectors", () => {
  const env = buildInstalledAcceptanceEnv({
    PATH: "/usr/bin",
    DISPLAY: ":0",
    XDG_STATE_HOME: "/home/operator/.local/state",
    OPENAI_API_KEY: "provider-key",
    TAIJI_AGENT_ROOT: "/tmp/dev-root",
    TAIJI_AGENT_AGENT_DIR: "/tmp/dev-agent",
    TAIJI_AGENT_WEBUI_DIR: "/tmp/dev-web",
    TAIJI_AGENT_PYTHON: "/tmp/dev-python",
    TAIJI_WEBUI_PYTHON: "/tmp/dev-web-python",
    TAIJI_AGENT_RUNTIME_ENV: "/tmp/dev-runtime.env",
    TAIJI_WEBUI_CHAT_BACKEND: "direct",
    TAIJI_RUNTIME_HOME: "/tmp/dev-home",
    HERMES_HOME: "/tmp/legacy-home",
    HERMES_WEBUI_AGENT_DIR: "/tmp/legacy-agent",
    PYTHONPATH: "/tmp/dev-pythonpath",
    PYTHONHOME: "/tmp/dev-pythonhome",
    ELECTRON_RUN_AS_NODE: "1",
    NODE_OPTIONS: "--require=/tmp/dev-hook.js",
  });

  assert.equal(env.PATH, "/usr/bin");
  assert.equal(env.DISPLAY, ":0");
  assert.equal(env.XDG_STATE_HOME, "/home/operator/.local/state");
  assert.equal(env.OPENAI_API_KEY, "provider-key");
  assert.equal(env.TAIJI_AGENT_ROOT, "/opt/taiji-agent");
  assert.equal(env.TAIJI_AGENT_USE_USER_DIRS, "1");
  assert.equal(Object.keys(env).some((key) => key.startsWith("HERMES_")), false);
  assert.equal(Object.entries(env).some(([, value]) => String(value).includes("/tmp/dev")), false);
  for (const key of ["TAIJI_AGENT_AGENT_DIR", "TAIJI_AGENT_WEBUI_DIR", "TAIJI_AGENT_PYTHON", "TAIJI_WEBUI_PYTHON", "TAIJI_AGENT_RUNTIME_ENV", "TAIJI_WEBUI_CHAT_BACKEND", "TAIJI_RUNTIME_HOME", "PYTHONPATH", "PYTHONHOME", "ELECTRON_RUN_AS_NODE", "NODE_OPTIONS"]) {
    assert.equal(Object.hasOwn(env, key), false, `${key} must not reach the installed App`);
  }
});

test("managed process argv accepts only fixed installed Agent and WebUI entrypoints", () => {
  const python = "/opt/taiji-agent/runtime/agent/venv/bin/python";
  assert.equal(managedProcessArgvMatches("Agent", [python, "-m", "taiji_runtime.main", "gateway", "run", "--accept-hooks"]), true);
  assert.equal(managedProcessArgvMatches("WebUI", [python, "/opt/taiji-agent/runtime/web/server.py"]), true);
  assert.equal(managedProcessArgvMatches("WebUI", [python, "/opt/taiji-agent/runtime/web/server.pyc"]), true);
  assert.equal(managedProcessArgvMatches("Agent", ["/tmp/dev-python", "-m", "taiji_runtime.main", "gateway", "run", "--accept-hooks"]), false);
  assert.equal(managedProcessArgvMatches("Agent", [python, "/tmp/dev-agent.py"]), false);
  assert.equal(managedProcessArgvMatches("WebUI", [python, "/tmp/dev-server.py"]), false);
  assert.equal(managedProcessArgvMatches("unknown", [python]), false);
});

test("failed acceptance cleanup escalates a verified installed process from TERM to KILL", async () => {
  const python = "/opt/taiji-agent/runtime/agent/venv/bin/python";
  const argv = [python, "-m", "taiji_runtime.main", "gateway", "run", "--accept-hooks"];
  let alive = true;
  const signals = [];
  const stopped = await terminateManagedProcess(4242, "Agent", {
    processAliveFn: () => alive,
    installedProcessArgvFn: () => argv,
    killFn: (_pid, signal) => {
      signals.push(signal);
      if (signal === "SIGKILL") alive = false;
    },
    sleepFn: async () => {},
    graceMs: 4,
    pollMs: 1,
  });
  assert.equal(stopped, true);
  assert.deepEqual(signals, ["SIGTERM", "SIGKILL"]);
});

test("failed acceptance cleanup never signals an unverified or reused pid", async () => {
  const signals = [];
  const stopped = await terminateManagedProcess(4242, "Agent", {
    processAliveFn: () => true,
    installedProcessArgvFn: () => ["/tmp/unrelated"],
    killFn: (_pid, signal) => signals.push(signal),
    sleepFn: async () => {},
    graceMs: 1,
    pollMs: 1,
  });
  assert.equal(stopped, false);
  assert.deepEqual(signals, []);
});

test("attachment helper opens the chooser through the visible button before setting files", async () => {
  const calls = [];
  const listeners = new Map();
  const client = {
    on(method, listener) {
      listeners.set(method, listener);
      return () => listeners.delete(method);
    },
    async send(method, params = {}) {
      calls.push({ method, params });
      if (method === "Runtime.evaluate") {
        return { result: { value: { x: 120, y: 64, width: 40, height: 40, ariaLabel: "附加文件" } } };
      }
      if (method === "Input.dispatchMouseEvent" && params.type === "mouseReleased") {
        queueMicrotask(() => listeners.get("Page.fileChooserOpened")?.({ backendNodeId: 73, mode: "selectSingle" }));
      }
      return {};
    },
  };

  await attachFixtureThroughVisibleChooser(client, "/tmp/taiji-attachment-probe.txt", Date.now() + 1000);
  const methods = calls.map((call) => call.method);
  assert.ok(methods.indexOf("Page.setInterceptFileChooserDialog") < methods.indexOf("Input.dispatchMouseEvent"));
  assert.ok(methods.indexOf("Input.dispatchMouseEvent") < methods.indexOf("DOM.setFileInputFiles"));
  const setFiles = calls.find((call) => call.method === "DOM.setFileInputFiles");
  assert.deepEqual(setFiles.params, { files: ["/tmp/taiji-attachment-probe.txt"], backendNodeId: 73 });
  assert.equal(calls.filter((call) => call.method === "Page.setInterceptFileChooserDialog").at(-1).params.enabled, false);
});

test("desktop actions use hit-tested CDP pointer input and real text insertion", async () => {
  const calls = [];
  const client = {
    async send(method, params = {}) {
      calls.push({ method, params });
      if (method === "Runtime.evaluate") {
        return { result: { value: { x: 80, y: 90, width: 40, height: 40, active: true, valueMatches: true } } };
      }
      return {};
    },
  };
  const deadline = Date.now() + 1000;
  await physicalClickVisibleElement(client, "#btnSend", deadline, "Send");
  await insertTextThroughVisibleComposer(client, "#msg", "真实输入", deadline);
  const methods = calls.map((call) => call.method);
  assert.ok(methods.includes("Input.dispatchMouseEvent"));
  assert.ok(methods.includes("Input.insertText"));
  assert.equal(calls.find((call) => call.method === "Input.insertText").params.text, "真实输入");
  const evaluations = calls.filter((call) => call.method === "Runtime.evaluate").map((call) => call.params.expression).join("\n");
  assert.match(evaluations, /elementFromPoint/);
  assert.match(evaluations, /document\.activeElement/);
});

test("critical desktop workflow contains no renderer synthetic click or direct textarea assignment", () => {
  const source = fs.readFileSync(DRIVER, "utf8");
  assert.equal(source.includes(".click();"), false);
  assert.equal(source.includes("composer.value ="), false);
  assert.equal(source.includes('physicalClickVisibleElement(client, "#btnNewChat"'), false);
  assert.equal(source.includes('physicalClickVisibleElement(client, ".taiji-new-chat"'), true);
});

test("probe code is challenge-bound and absent from the model prompt", () => {
  const first = buildProbeCode("a".repeat(64), "b".repeat(32));
  const second = buildProbeCode("a".repeat(64), "c".repeat(32));
  assert.match(first, /^TAIJI-ATTACHMENT-PROBE-[0-9a-f]{32}$/);
  assert.notEqual(first, second);
  assert.equal(PROBE_PROMPT.includes(first), false);
  assert.equal(PROBE_PROMPT.includes("TAIJI-ATTACHMENT-PROBE-"), false);
});

test("validateDesktopTarget requires a real desktop marker, token and page websocket", () => {
  const target = {
    type: "page",
    url: "http://127.0.0.1:18787/?taiji_desktop=1&taiji_desktop_token=secret",
    webSocketDebuggerUrl: "ws://127.0.0.1:49123/devtools/page/abc",
  };
  assert.equal(validateDesktopTarget(target).origin, "http://127.0.0.1:18787");
  assert.throws(
    () => validateDesktopTarget({ ...target, url: "http://127.0.0.1:18787/" }),
    /desktop marker/,
  );
  assert.throws(
    () => validateDesktopTarget({ ...target, url: "http://127.0.0.1:18787/?taiji_desktop=1" }),
    /desktop token/,
  );
  assert.throws(() => validateDesktopTarget({ ...target, type: "other" }), /page target/);
});

test("redactDesktopUrl never exposes the desktop token", () => {
  assert.equal(
    redactDesktopUrl("http://127.0.0.1:18787/?taiji_desktop=1&taiji_desktop_token=secret&x=1"),
    "http://127.0.0.1:18787/?taiji_desktop=1&taiji_desktop_token=%3Credacted%3E&x=1",
  );
  const stack = "Error: failed\n    at run (/opt/taiji-agent/driver.js:10:2)";
  assert.equal(redactDesktopUrl(stack), stack);
});

test("HTTP failure filter allows only the exact same-origin missing expert run", () => {
  const origin = "http://127.0.0.1:18787";
  const expected = {
    status: 404,
    method: "GET",
    url: `${origin}/api/expert-teams/run?session_id=s-1`,
  };
  assert.equal(isExpectedDesktopHttpFailure(expected, origin), true);
  assert.equal(isExpectedDesktopHttpFailure({ ...expected, method: "POST" }, origin), false);
  assert.equal(isExpectedDesktopHttpFailure({ ...expected, url: "http://evil/api/expert-teams/run?session_id=s-1" }, origin), false);
  assert.deepEqual(
    filterUnexpectedHttpFailures([expected, { status: 503, method: "GET", url: `${origin}/api/product/diagnostics` }], origin),
    [{ status: 503, method: "GET", url: `${origin}/api/product/diagnostics` }],
  );
});

test("JS error filter allows only the correlated expert-run resource error", () => {
  const origin = "http://127.0.0.1:18787";
  const expected = {
    source: "log",
    text: "Failed to load resource: the server responded with a status of 404 (Not Found)",
    url: `${origin}/api/expert-teams/run?session_id=s-1`,
  };
  assert.equal(isExpectedBackgroundConsoleError(expected, origin), true);
  assert.deepEqual(
    filterUnexpectedJsErrors([expected, { source: "runtime", text: "boom", url: `${origin}/static/boot.js` }], origin),
    [{ source: "runtime", text: "boom", url: `${origin}/static/boot.js` }],
  );
});

test("normalizeMessageContent handles text parts without accepting tool payloads", () => {
  assert.equal(normalizeMessageContent("  answer  "), "answer");
  assert.equal(
    normalizeMessageContent([{ type: "text", text: "A" }, { type: "input_text", input_text: "B" }, { type: "tool_use", input: "secret" }]),
    "AB",
  );
});

test("completionSnapshotPassed requires settled UI, persisted attachment and exact answer", () => {
  const expected = {
    sessionId: "1".repeat(32),
    attachmentName: "taiji-attachment-probe.txt",
    probeCode: "TAIJI-ATTACHMENT-PROBE-" + "a".repeat(32),
  };
  const snapshot = {
    sessionId: expected.sessionId,
    busy: false,
    activeStreamId: null,
    pendingUserMessage: null,
    persistedPendingUserMessage: null,
    userAttachments: [expected.attachmentName],
    persistedUserAttachments: [expected.attachmentName],
    assistantContent: expected.probeCode,
    persistedAssistantContent: expected.probeCode,
    assistantError: false,
    assistantLicenseBlocked: false,
  };
  assert.equal(completionSnapshotPassed(snapshot, expected), true);
  assert.equal(completionSnapshotPassed({ ...snapshot, busy: true }, expected), false);
  assert.equal(completionSnapshotPassed({ ...snapshot, assistantContent: `${expected.probeCode}.` }, expected), false);
  assert.equal(completionSnapshotPassed({ ...snapshot, persistedUserAttachments: [] }, expected), false);
});

test("supportBundleIsSafe accepts only the bounded redacted product bundle", () => {
  const labels = {
    webui: "桌面界面",
    agent: "智能体服务",
    gateway: "本地任务服务",
    license: "授权状态",
    docx: "文档引擎",
    skills: "专家能力",
    node: "运行环境",
  };
  const bundle = {
    schema: "taiji.product.support-bundle.v1",
    manifest: {
      redacted: true,
      logs_included: false,
      paths_included: false,
      secrets_included: false,
    },
    diagnostics: {
      schema: "taiji.product.diagnostics.v1",
      generated_at: "2026-07-11T02:00:00Z",
      incident_id: "inc-0123456789ab",
      overall: "ready",
      components: ["webui", "agent", "gateway", "license", "docx", "skills", "node"].map((id) => ({ id, label: labels[id], status: "ready" })),
    },
  };
  assert.equal(supportBundleIsSafe(bundle), true);
  assert.equal(supportBundleIsSafe({ ...bundle, manifest: { ...bundle.manifest, logs_included: true } }), false);
  assert.equal(supportBundleIsSafe({ ...bundle, diagnostics: { ...bundle.diagnostics, components: [] } }), false);
  assert.equal(supportBundleIsSafe({
    ...bundle,
    diagnostics: {
      ...bundle.diagnostics,
      overall: "degraded",
      components: bundle.diagnostics.components.map((item, index) => index === 4 ? { ...item, status: "degraded" } : item),
    },
  }), false);
  assert.equal(supportBundleIsSafe({ ...bundle, diagnostics: { ...bundle.diagnostics, debug_path: "/opt/taiji-agent" } }), false);
  assert.equal(supportBundleIsSafe({ ...bundle, diagnostics: { ...bundle.diagnostics, incident_id: "bad" } }), false);
  assert.equal(supportBundleIsSafe({
    ...bundle,
    diagnostics: {
      ...bundle.diagnostics,
      components: bundle.diagnostics.components.map((item, index) => index === 0 ? { ...item, token: "secret" } : item),
    },
  }), false);
});

test("parsePid accepts live-process-shaped pid files only", () => {
  assert.equal(parsePid("4242\n"), 4242);
  assert.equal(parsePid("1"), null);
  assert.equal(parsePid("abc"), null);
  assert.equal(parsePid("4242 extra"), null);
});

test("CdpClient correlates responses and dispatches protocol events", async () => {
  const socket = new FakeWebSocket((request) => ({ id: request.id, result: { echoed: request.params.value } }));
  const client = new CdpClient(socket, 1000);
  const events = [];
  client.on("Runtime.consoleAPICalled", (params) => events.push(params.type));
  const result = await client.send("Runtime.evaluate", { value: "ok" });
  socket.dispatchEvent(new MessageEvent("message", {
    data: JSON.stringify({ method: "Runtime.consoleAPICalled", params: { type: "error" } }),
  }));
  assert.deepEqual(result, { echoed: "ok" });
  assert.deepEqual(events, ["error"]);
  assert.equal(socket.sent[0].method, "Runtime.evaluate");
  client.close();
});

test("CdpClient rejects protocol failures instead of returning partial data", async () => {
  const socket = new FakeWebSocket((request) => ({ id: request.id, error: { code: -32000, message: "denied" } }));
  const client = new CdpClient(socket, 1000);
  await assert.rejects(() => client.send("Browser.setDownloadBehavior", {}), /CDP Browser\.setDownloadBehavior failed.*denied/);
  client.close();
});

test("buildDriverResult is fail-closed and emits no desktop token", () => {
  const measurements = {
    sessionId: "1".repeat(32),
    challenge: "2".repeat(64),
    electronPid: 4242,
    electronExecutableSha256: "3".repeat(64),
    desktopEntrySha256: "4".repeat(64),
    appUrl: "http://127.0.0.1:18787/?taiji_desktop=1&taiji_desktop_token=secret",
    webuiOrigin: "http://127.0.0.1:18787",
    model: "openai/gpt-test",
    probeSha256: "5".repeat(64),
    agentPid: 4243,
    webPid: 4244,
    exitCode: 0,
    jsErrors: [],
    unexpectedHttpFailures: [],
    checks: {
      desktop_launch: true,
      real_model_conversation: true,
      attachment_flow: true,
      window_close_exit: true,
      diagnostic_export: true,
    },
  };
  const result = buildDriverResult(measurements);
  assert.equal(result.schema, "taiji.desktop.acceptance-driver.v1");
  assert.equal(result.app_url.includes("secret"), false);
  assert.deepEqual(result.checks, measurements.checks);
  assert.throws(
    () => buildDriverResult({ ...measurements, checks: { ...measurements.checks, window_close_exit: false } }),
    /driver check failed: window_close_exit/,
  );
  assert.throws(() => buildDriverResult({ ...measurements, jsErrors: ["boom"] }), /JavaScript errors/);
  assert.throws(() => buildDriverResult({ ...measurements, model: "" }), /model identity/);
});

test("fixed desktop entry path remains under the installed product surface", () => {
  assert.equal(DESKTOP_ENTRY, "/usr/share/applications/taiji-agent.desktop");
});
