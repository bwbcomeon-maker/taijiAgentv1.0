#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
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
  assertCanonicalUserHome,
  buildDriverResult,
  buildElectronArgs,
  buildSecondaryElectronArgs,
  buildInstalledAcceptanceEnv,
  buildCoreObservation,
  buildCoreJournalArgs,
  buildModelConfigObservation,
  buildProbeCode,
  captureChildIdentityOrCleanExit,
  captureElectronHelperIdentities,
  completionSnapshotPassed,
  coreHandlerIsTrusted,
  coreJournalToolIsTrusted,
  createProfileContinuityMarker,
  deleteProfileContinuityMarker,
  filterUnexpectedHttpFailures,
  filterUnexpectedJsErrors,
  isExpectedBackgroundConsoleError,
  isExpectedDesktopHttpFailure,
  insertTextThroughVisibleComposer,
  insertSecretThroughVisiblePasswordInput,
  inspectProcessIdentity,
  normalizeMessageContent,
  managedProcessArgvMatches,
  parseArgs,
  parseCoreJournalJsonCursors,
  parsePid,
  processIdentityFromStat,
  processIdentityStillPresent,
  publicModelConfigProjection,
  queryCoreJournalSnapshot,
  querySettledCoreJournalSnapshot,
  readHiddenCredentialFromTty,
  physicalClickVisibleElement,
  redactDesktopUrl,
  safeErrorText,
  supportBundleIsSafe,
  terminateManagedProcess,
  terminateOwnedChildProcess,
  verifyProfileContinuityMarker,
  visibleModelConfigurationMatches,
  validateDesktopAuthCookies,
  validateDesktopTarget,
  assertVisibleFirstConfigurationStart,
  firstConfigurationCompletionObserved,
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

test("parseArgs binds canonical certification category metadata when supplied", () => {
  const args = parseArgs([
    ...validArgv(),
    "--matrix", "/opt/taiji-agent/certification-matrix.json",
    "--category-id", "kylin-current-standard",
  ]);
  assert.equal(args.matrix, "/opt/taiji-agent/certification-matrix.json");
  assert.equal(args.categoryId, "kylin-current-standard");
  assert.throws(
    () => parseArgs([...validArgv(), "--category-id", "kylin-current-standard"]),
    /supplied together/,
  );
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

test("first-configuration acceptance requires a visible start and completed server state", () => {
  assert.doesNotThrow(() => assertVisibleFirstConfigurationStart({ visible: true, active: true, completed: false }));
  assert.throws(
    () => assertVisibleFirstConfigurationStart({ visible: false, active: false, completed: true }),
    /must start with the visible onboarding workflow/,
  );
  assert.equal(firstConfigurationCompletionObserved({
    visible: false,
    active: false,
    completed: true,
    preflightReady: true,
  }), true);
  assert.equal(firstConfigurationCompletionObserved({
    visible: false,
    active: false,
    completed: false,
    preflightReady: true,
  }), false);
});

test("buildElectronArgs enables loopback CDP before the fixed App directory", () => {
  assert.deepEqual(buildElectronArgs(49123), [
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=49123",
    APP_DIR,
  ]);
  assert.throws(() => buildElectronArgs(0), /CDP port/);
  assert.deepEqual(buildSecondaryElectronArgs(), [APP_DIR]);
  const rendered = [...buildElectronArgs(49123), ...buildSecondaryElectronArgs()].join(" ");
  for (const forbidden of ["--user-data-dir", "--disable-gpu", "disableHardwareAcceleration"]) {
    assert.equal(rendered.includes(forbidden), false, `${forbidden} must not weaken the installed profile`);
  }
});

test("installed acceptance environment removes development/runtime selectors", () => {
  const env = buildInstalledAcceptanceEnv({
    HOME: "/home/operator",
    USER: "operator",
    LOGNAME: "operator",
    PATH: "/untrusted/bin",
    DISPLAY: ":0",
    WAYLAND_DISPLAY: "wayland-0",
    DBUS_SESSION_BUS_ADDRESS: "unix:path=/run/user/1000/bus",
    LANG: "zh_CN.UTF-8",
    XDG_RUNTIME_DIR: "/run/user/1000",
    XDG_STATE_HOME: "/home/operator/.local/state",
    XDG_CONFIG_DIRS: "/tmp/untrusted-config-dirs",
    XDG_DATA_DIRS: "/tmp/untrusted-data-dirs",
    OPENAI_API_KEY: "provider-key",
    RANDOM_VENDOR_SECRET: "must-not-reach-electron",
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
  }, { uid: 1000, username: "operator", homedir: "/home/operator" });

  assert.equal(env.PATH, "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin");
  assert.equal(env.DISPLAY, ":0");
  assert.equal(env.WAYLAND_DISPLAY, "wayland-0");
  assert.equal(env.DBUS_SESSION_BUS_ADDRESS, "unix:path=/run/user/1000/bus");
  assert.equal(env.LANG, "zh_CN.UTF-8");
  assert.equal(env.XDG_RUNTIME_DIR, "/run/user/1000");
  assert.equal(env.XDG_STATE_HOME, "/home/operator/.local/state");
  assert.equal(Object.hasOwn(env, "XDG_CONFIG_DIRS"), false);
  assert.equal(Object.hasOwn(env, "XDG_DATA_DIRS"), false);
  assert.equal(Object.hasOwn(env, "OPENAI_API_KEY"), false);
  assert.equal(Object.hasOwn(env, "RANDOM_VENDOR_SECRET"), false);
  assert.equal(env.TAIJI_AGENT_ROOT, "/opt/taiji-agent");
  assert.equal(env.TAIJI_AGENT_USE_USER_DIRS, "1");
  assert.equal(Object.keys(env).some((key) => key.startsWith("HERMES_")), false);
  assert.equal(Object.entries(env).some(([, value]) => String(value).includes("/tmp/dev")), false);
  for (const key of ["TAIJI_AGENT_AGENT_DIR", "TAIJI_AGENT_WEBUI_DIR", "TAIJI_AGENT_PYTHON", "TAIJI_WEBUI_PYTHON", "TAIJI_AGENT_RUNTIME_ENV", "TAIJI_WEBUI_CHAT_BACKEND", "TAIJI_RUNTIME_HOME", "PYTHONPATH", "PYTHONHOME", "ELECTRON_RUN_AS_NODE", "NODE_OPTIONS"]) {
    assert.equal(Object.hasOwn(env, key), false, `${key} must not reach the installed App`);
  }

  assert.throws(() => buildInstalledAcceptanceEnv({
    HOME: "/tmp/fresh-profile",
    USER: "operator",
    LOGNAME: "operator",
  }, { uid: 1000, username: "operator", homedir: "/home/operator" }), /HOME.*identity/);
  for (const xdgStateHome of ["relative/state", "/tmp/taiji-state", "/srv/foreign-profile"]) {
    assert.throws(() => buildInstalledAcceptanceEnv({
      HOME: "/home/operator",
      USER: "operator",
      LOGNAME: "operator",
      XDG_STATE_HOME: xdgStateHome,
    }, { uid: 1000, username: "operator", homedir: "/home/operator" }), /XDG_STATE_HOME/);
  }
  assert.throws(() => buildInstalledAcceptanceEnv({
    HOME: "/home/operator",
    USER: "operator",
    LOGNAME: "operator",
    XDG_RUNTIME_DIR: "/tmp/runtime-redirect",
  }, { uid: 1000, username: "operator", homedir: "/home/operator" }), /XDG_RUNTIME_DIR/);
});

test("installed acceptance binds the canonical real home to the current uid", () => {
  const identity = { uid: 1000, username: "operator", homedir: "/home/operator" };
  const directory = {
    uid: 1000,
    isDirectory: () => true,
    isSymbolicLink: () => false,
  };
  assert.doesNotThrow(() => assertCanonicalUserHome(identity, {
    lstatFn: () => directory,
    realpathFn: (pathname) => pathname,
  }));
  assert.throws(() => assertCanonicalUserHome(identity, {
    lstatFn: () => ({ ...directory, uid: 1001 }),
    realpathFn: (pathname) => pathname,
  }), /owned by the current uid/);
  assert.throws(() => assertCanonicalUserHome(identity, {
    lstatFn: () => directory,
    realpathFn: () => "/srv/redirected-home",
  }), /canonical/);
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
  const identity = { pid: 4242, start_time_ticks: "987654" };
  const stopped = await terminateManagedProcess(identity, "Agent", {
    processIdentityStillPresentFn: () => alive,
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
  const identity = { pid: 4242, start_time_ticks: "987654" };
  const stopped = await terminateManagedProcess(identity, "Agent", {
    processIdentityStillPresentFn: () => false,
    installedProcessArgvFn: () => ["/tmp/unrelated"],
    killFn: (_pid, signal) => signals.push(signal),
    sleepFn: async () => {},
    graceMs: 1,
    pollMs: 1,
  });
  assert.equal(stopped, false);
  assert.deepEqual(signals, []);
});

test("managed cleanup stops signalling when the original pid identity is reused", async () => {
  const python = "/opt/taiji-agent/runtime/agent/venv/bin/python";
  const argv = [python, "-m", "taiji_runtime.main", "gateway", "run", "--accept-hooks"];
  const identity = { pid: 4242, start_time_ticks: "987654" };
  let checks = 0;
  const signals = [];
  const stopped = await terminateManagedProcess(identity, "Agent", {
    processIdentityStillPresentFn: () => {
      checks += 1;
      return checks === 1;
    },
    installedProcessArgvFn: () => argv,
    killFn: (_pid, signal) => signals.push(signal),
    sleepFn: async () => {},
    graceMs: 2,
    pollMs: 1,
  });
  assert.equal(stopped, true);
  assert.deepEqual(signals, ["SIGTERM"]);
});

test("owned Electron cleanup escalates only the original child identity", async () => {
  const identity = { pid: 5151, start_time_ticks: "112233" };
  let alive = true;
  const signals = [];
  const child = {
    pid: identity.pid,
    kill(signal) {
      signals.push(signal);
      if (signal === "SIGKILL") alive = false;
      return true;
    },
  };
  const stopped = await terminateOwnedChildProcess(child, identity, {
    processIdentityStillPresentFn: () => alive,
    sleepFn: async () => {},
    graceMs: 2,
    pollMs: 1,
  });
  assert.equal(stopped, true);
  assert.deepEqual(signals, ["SIGTERM", "SIGKILL"]);

  const mismatchedSignals = [];
  const mismatched = await terminateOwnedChildProcess({
    pid: 6161,
    kill(signal) { mismatchedSignals.push(signal); },
  }, identity, {
    processIdentityStillPresentFn: () => true,
    sleepFn: async () => {},
    graceMs: 1,
    pollMs: 1,
  });
  assert.equal(mismatched, false);
  assert.deepEqual(mismatchedSignals, []);
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

test("validateDesktopTarget requires a marker-only App URL and rejects query tokens", () => {
  const target = {
    type: "page",
    url: "http://127.0.0.1:18787/?taiji_desktop=1",
    webSocketDebuggerUrl: "ws://127.0.0.1:49123/devtools/page/abc",
  };
  assert.equal(validateDesktopTarget(target).origin, "http://127.0.0.1:18787");
  assert.throws(
    () => validateDesktopTarget({ ...target, url: "http://127.0.0.1:18787/" }),
    /desktop marker/,
  );
  assert.throws(
    () => validateDesktopTarget({
      ...target,
      url: "http://127.0.0.1:18787/?taiji_desktop=1&taiji_desktop_token=" + "a".repeat(64),
    }),
    /must not expose the desktop token/,
  );
  assert.throws(
    () => validateDesktopTarget({ ...target, url: `${target.url}&debug=1` }),
    /unexpected query/,
  );
  assert.throws(() => validateDesktopTarget({ ...target, type: "other" }), /page target/);
});

test("validateDesktopAuthCookies requires one strict HttpOnly host cookie without exposing its value", () => {
  const token = "a".repeat(64);
  const cookies = [
    {
      name: "taiji_desktop_token",
      value: token,
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
      sameSite: "Strict",
    },
  ];
  assert.deepEqual(
    validateDesktopAuthCookies(cookies, "http://127.0.0.1:18787"),
    {
      name: "taiji_desktop_token",
      present: true,
      http_only: true,
      same_site: "Strict",
      path: "/",
      value_format: "lowercase-hex-64",
    },
  );
  for (const invalid of [
    [{ ...cookies[0], httpOnly: false }],
    [{ ...cookies[0], sameSite: "Lax" }],
    [{ ...cookies[0], path: "/app" }],
    [{ ...cookies[0], value: "A".repeat(64) }],
    [{ ...cookies[0], value: "a".repeat(63) }],
    [{ ...cookies[0], domain: "example.com" }],
    [cookies[0], { ...cookies[0] }],
  ]) {
    let message = "";
    assert.throws(
      () => validateDesktopAuthCookies(invalid, "http://127.0.0.1:18787"),
      (error) => {
        message = String(error.message || error);
        return true;
      },
    );
    assert.equal(message.includes(token), false);
  }
});

test("redactDesktopUrl never exposes the desktop token", () => {
  assert.equal(
    redactDesktopUrl("http://127.0.0.1:18787/?taiji_desktop=1&taiji_desktop_token=secret&x=1"),
    "http://127.0.0.1:18787/?taiji_desktop=1&taiji_desktop_token=%3Credacted%3E&x=1",
  );
  const stack = "Error: failed\n    at run (/opt/taiji-agent/driver.js:10:2)";
  assert.equal(redactDesktopUrl(stack), stack);
});

test("formal failure evidence emits only a fixed classification code", () => {
  const error = new Error([
    "Authorization: Bearer sk-live-short-value",
    "sk-bare-provider-secret",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjdXN0b21lciJ9.signature",
    "api_key=another-short-secret",
    '"password": "desktop-password"',
    "at /home/operator/.config/taiji-agent/runtime.log",
    "at /data/usershare/customer/private/runtime.log",
  ].join("\n"));
  assert.equal(safeErrorText(error), "TAIJI-DESKTOP-E999");
  error.code = "TAIJI-DESKTOP-E042";
  assert.equal(safeErrorText(error), "TAIJI-DESKTOP-E042");
});

test("hidden credential input never echoes or serializes the Provider secret", async () => {
  await assert.rejects(
    () => readHiddenCredentialFromTty({ input: { isTTY: false }, output: { write() {} } }),
    (error) => error?.code === "TAIJI-DESKTOP-E021",
  );
  const input = new EventEmitter();
  input.isTTY = true;
  input.isRaw = false;
  const rawModes = [];
  input.setRawMode = (enabled) => { input.isRaw = enabled; rawModes.push(enabled); };
  input.resume = () => {};
  input.pause = () => {};
  const writes = [];
  const output = { write: (value) => { writes.push(String(value)); } };
  const pending = readHiddenCredentialFromTty({ input, output });
  queueMicrotask(() => input.emit("data", Buffer.from("sk-hidden-value\r", "utf8")));
  const secret = await pending;
  assert.equal(secret, "sk-hidden-value");
  assert.deepEqual(rawModes, [true, false]);
  assert.equal(writes.join("").includes(secret), false);

  const sent = [];
  const client = {
    async send(method, params = {}) {
      sent.push({ method, params });
      if (method === "Runtime.evaluate") {
        const value = params.expression.includes("getBoundingClientRect")
          ? { x: 10, y: 10, width: 100, height: 30, accessibleName: "API 密钥" }
          : true;
        return { result: { value } };
      }
      return {};
    },
  };
  await insertSecretThroughVisiblePasswordInput(client, "#onboardingApiKeyInput", secret, Date.now() + 2000);
  assert.equal(sent.some((call) => call.method === "Input.insertText" && call.params.text === secret), true);
  assert.equal(sent.filter((call) => call.method !== "Input.insertText").some((call) => JSON.stringify(call).includes(secret)), false);
  await assert.rejects(
    () => insertSecretThroughVisiblePasswordInput(client, "#onboardingApiKeyInput", "bad\nsecret", Date.now() + 2000),
    (error) => error?.code === "TAIJI-DESKTOP-E022",
  );
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
  const restartRounds = [1, 2, 3].map((round) => ({
    round,
    ready: true,
    electron_pid: 4241 + round,
    agent_pid: 4242 + round,
    web_pid: 4243 + round,
    secondary_pid: 5241 + round,
    cdp_port: 49122 + round,
    webui_port: 18786 + round,
    second_instance_exit_code: 0,
    electron_exit_code: 0,
    restored_and_focused: true,
    page_close_sent: true,
    process_identities_gone: {
      electron: true,
      agent: true,
      webui: true,
      secondary: true,
    },
    ports_closed: { cdp: true, webui: true },
    pidfiles_absent: true,
    model_config_observed: true,
    profile_continuity_observed: true,
  }));
  const measurements = {
    sessionId: "1".repeat(32),
    challenge: "2".repeat(64),
    electronPid: 4242,
    electronExecutableSha256: "3".repeat(64),
    desktopEntrySha256: "4".repeat(64),
    appUrl: "http://127.0.0.1:18787/?taiji_desktop=1",
    webuiOrigin: "http://127.0.0.1:18787",
    desktopAuthCookie: {
      name: "taiji_desktop_token",
      present: true,
      http_only: true,
      same_site: "Strict",
      path: "/",
      value_format: "lowercase-hex-64",
    },
    model: "openai/gpt-test",
    probeSha256: "5".repeat(64),
    agentPid: 4243,
    webPid: 4244,
    exitCode: 0,
    jsErrors: [],
    unexpectedHttpFailures: [],
    restartRounds,
    persistentUserData: {
      mode: "electron-default-persistent",
      restart_rounds: 3,
      user_data_override: false,
      profile_reset: false,
      environment_reused: true,
      continuity_observed_rounds: 3,
      continuity_token: "8".repeat(64),
    },
    coreObservation: {
      status: "verified",
      mechanism: "journalctl-json-user-electron",
      baseline_entry_count: 0,
      baseline_cursor_set_token: "7".repeat(64),
      rounds: [1, 2, 3].map((round) => ({
        round,
        status: "verified",
        added_entry_count: 0,
        cursor_set_token: String(round).repeat(64),
      })),
    },
    modelConfigObservation: {
      observed_rounds: 3,
      consistent: true,
      public_projection_token: "6".repeat(64),
    },
    checks: {
      visible_first_configuration_completion: true,
      desktop_launch: true,
      real_model_conversation: true,
      attachment_flow: true,
      window_close_exit: true,
      diagnostic_export: true,
      three_restart_cycles: true,
      second_instance_focus: true,
      model_configuration_state_consistent: true,
      no_new_electron_core: true,
    },
  };
  const result = buildDriverResult(measurements);
  assert.equal(result.schema, "taiji.desktop.acceptance-driver.v2");
  assert.equal(result.restart_rounds.length, 3);
  assert.deepEqual(result.restart_rounds.map((round) => round.round), [1, 2, 3]);
  assert.equal(result.electron_pid, result.restart_rounds[0].electron_pid);
  assert.equal(result.agent_pid, result.restart_rounds[0].agent_pid);
  assert.equal(result.web_pid, result.restart_rounds[0].web_pid);
  assert.deepEqual(result.persistent_user_data, measurements.persistentUserData);
  assert.deepEqual(result.core_observation, measurements.coreObservation);
  assert.deepEqual(result.model_config_observation, measurements.modelConfigObservation);
  assert.equal(result.app_url.includes("secret"), false);
  assert.deepEqual(result.desktop_auth_cookie, measurements.desktopAuthCookie);
  assert.equal(JSON.stringify(result).includes("a".repeat(64)), false);
  assert.deepEqual(result.checks, measurements.checks);
  assert.throws(
    () => buildDriverResult({ ...measurements, checks: { ...measurements.checks, window_close_exit: false } }),
    /driver check failed: window_close_exit/,
  );
  assert.throws(
    () => buildDriverResult({
      ...measurements,
      desktopAuthCookie: { ...measurements.desktopAuthCookie, http_only: false },
    }),
    /desktop auth cookie/,
  );
  assert.throws(
    () => buildDriverResult({
      ...measurements,
      appUrl: `${measurements.appUrl}&taiji_desktop_token=${"a".repeat(64)}`,
    }),
    /must not expose the desktop token/,
  );
  assert.throws(
    () => buildDriverResult({ ...measurements, appUrl: `${measurements.appUrl}&debug=1` }),
    /unexpected query/,
  );
  assert.throws(
    () => buildDriverResult({ ...measurements, webuiOrigin: "http://127.0.0.1:19999" }),
    /same App/,
  );
  assert.throws(() => buildDriverResult({ ...measurements, jsErrors: ["boom"] }), /JavaScript errors/);
  assert.throws(() => buildDriverResult({ ...measurements, model: "" }), /model identity/);
  assert.throws(() => buildDriverResult({ ...measurements, restartRounds: restartRounds.slice(0, 2) }), /exactly 3 restart rounds/);
  assert.throws(() => buildDriverResult({
    ...measurements,
    restartRounds: restartRounds.map((round) => round.round === 1 ? { ...round, webui_port: 19999 } : round),
  }), /strict round1 aliases/);
  assert.throws(() => buildDriverResult({
    ...measurements,
    restartRounds: restartRounds.map((round) => round.round === 2
      ? { ...round, process_identities_gone: { ...round.process_identities_gone, webui: false } }
      : round),
  }), /restart round 2 failed/);
  assert.throws(() => buildDriverResult({
    ...measurements,
    modelConfigObservation: { ...measurements.modelConfigObservation, consistent: false },
  }), /model configuration projection changed/);
  assert.throws(() => buildDriverResult({
    ...measurements,
    coreObservation: {
      status: "unverified",
      reason: "json_unavailable",
      mechanism: "journalctl-json-user-electron",
      baseline_entry_count: null,
      baseline_cursor_set_token: null,
      rounds: [1, 2, 3].map((round) => ({ round, status: "unverified", reason: "baseline_unavailable" })),
    },
  }), /core observation was not verified/);
});

test("model configuration persistence compares a public projection but emits only a salted token", () => {
  const payload = {
    ok: true,
    profile: "default",
    main_request_id: "a".repeat(32),
    config: { path: "/home/private/.config/taiji/config.yaml" },
    main: {
      provider: "custom",
      model: "private-model",
      base_url: "https://model.internal.example/v1",
      key_env: "CUSTOM_MODEL_API_KEY",
      key_status: { configured: true, source: "env_file", env_var: "CUSTOM_MODEL_API_KEY" },
      api_key: "must-never-escape",
    },
    provider_credentials: [{ api_key: "also-secret" }],
  };
  assert.deepEqual(publicModelConfigProjection(payload), {
    profile: "default",
    main_request_id: "a".repeat(32),
    main: {
      provider: "custom",
      model: "private-model",
      base_url: "https://model.internal.example/v1",
      key_env: "CUSTOM_MODEL_API_KEY",
      key_configured: true,
      key_source: "env_file",
      key_env_status: "CUSTOM_MODEL_API_KEY",
    },
  });

  const observation = buildModelConfigObservation([payload, structuredClone(payload), structuredClone(payload)], Buffer.alloc(32, 7));
  assert.equal(observation.observed_rounds, 3);
  assert.equal(observation.consistent, true);
  assert.match(observation.public_projection_token, /^[0-9a-f]{64}$/);
  const rendered = JSON.stringify(observation);
  for (const forbidden of ["must-never-escape", "also-secret", "model.internal.example", "/home/private", "private-model", "CUSTOM_MODEL_API_KEY"]) {
    assert.equal(rendered.includes(forbidden), false, `${forbidden} must not enter evidence`);
  }

  const changed = structuredClone(payload);
  changed.main.base_url = "https://replacement.internal.example/v1";
  assert.equal(buildModelConfigObservation([payload, structuredClone(payload), changed], Buffer.alloc(32, 7)).consistent, false);
  assert.throws(() => buildModelConfigObservation([payload, payload], Buffer.alloc(32, 7)), /exactly 3/);
});

test("model configuration observation rejects a consistently unconfigured or receipt-less main model", () => {
  const valid = {
    ok: true,
    profile: "default",
    main_request_id: "00112233445566778899aabbccddeeff",
    main: {
      provider: "deepseek",
      model: "deepseek-chat",
      base_url: "",
      key_env: "",
      key_status: { configured: true, source: "env_file", env_var: "DEEPSEEK_API_KEY" },
    },
  };
  assert.equal(publicModelConfigProjection(valid).main.key_env, "DEEPSEEK_API_KEY");
  assert.throws(
    () => publicModelConfigProjection({ ...valid, main_request_id: "" }),
    /request receipt/,
  );
  assert.throws(
    () => publicModelConfigProjection({
      ...valid,
      main: { ...valid.main, key_status: { ...valid.main.key_status, configured: false } },
    }),
    /not configured/,
  );
  for (const field of ["provider", "model"]) {
    assert.throws(
      () => publicModelConfigProjection({ ...valid, main: { ...valid.main, [field]: "" } }),
      /incomplete/,
    );
  }
  assert.throws(
    () => publicModelConfigProjection({
      ...valid,
      main: { ...valid.main, key_env: "", key_status: { ...valid.main.key_status, env_var: "" } },
    }),
    /incomplete/,
  );
});

test("journalctl JSON observation records exact salted cursor sets without retaining raw rows", () => {
  const uid = 1000;
  const electron = ELECTRON_PATH;
  const row = (cursor, overrides = {}) => ({
    MESSAGE_ID: "fc2e22bc6ee647b6b90729ab34a250b1",
    __CURSOR: cursor,
    __REALTIME_TIMESTAMP: "1786400000000000",
    COREDUMP_PID: "4242",
    COREDUMP_UID: String(uid),
    COREDUMP_EXE: electron,
    COREDUMP_SIGNAL: "5",
    COREDUMP_TIMESTAMP: "1786400000000000",
    ...overrides,
  });
  const cursors = parseCoreJournalJsonCursors([
    JSON.stringify(row("cursor-a")),
    JSON.stringify(row("cursor-b", { COREDUMP_PID: "4343" })),
  ].join("\n"), uid);
  assert.deepEqual(cursors, ["cursor-a", "cursor-b"]);
  assert.throws(() => parseCoreJournalJsonCursors("not-json", uid), /JSON/);
  assert.throws(() => parseCoreJournalJsonCursors(JSON.stringify(row("cursor-a", { COREDUMP_UID: "1001" })), uid), /UID/);
  assert.throws(() => parseCoreJournalJsonCursors(JSON.stringify(row("cursor-a", { COREDUMP_EXE: "/tmp/electron" })), uid), /executable/);
  const incomplete = row("cursor-a");
  delete incomplete.COREDUMP_SIGNAL;
  assert.throws(() => parseCoreJournalJsonCursors(JSON.stringify(incomplete), uid), /required fields/);

  assert.deepEqual(buildCoreJournalArgs(uid), [
    "--system",
    "--no-pager",
    "--output=json",
    "MESSAGE_ID=fc2e22bc6ee647b6b90729ab34a250b1",
    `COREDUMP_UID=${uid}`,
    `COREDUMP_EXE=${ELECTRON_PATH}`,
  ]);

  const trustedHandlerStats = {
    uid: 0,
    gid: 0,
    mode: 0o100755,
    nlink: 1,
    isSymbolicLink: () => false,
    isFile: () => true,
  };
  assert.equal(coreHandlerIsTrusted({
    readFileFn: () => "|/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c %h\n",
    realpathFn: () => "/usr/lib/systemd/systemd-coredump",
    lstatFn: () => trustedHandlerStats,
  }), true);
  assert.equal(coreHandlerIsTrusted({
    readFileFn: () => "|/usr/share/apport/apport %p %s %c\n",
    realpathFn: (pathname) => pathname,
    lstatFn: () => trustedHandlerStats,
  }), false);

  const trustedStats = (pathname) => ({
    uid: 0,
    gid: 0,
    mode: pathname === "/usr/bin/journalctl" ? 0o100755 : 0o40755,
    nlink: 1,
    isSymbolicLink: () => false,
    isDirectory: () => pathname !== "/usr/bin/journalctl",
    isFile: () => pathname === "/usr/bin/journalctl",
  });
  assert.equal(coreJournalToolIsTrusted({ lstatFn: trustedStats, realpathFn: (pathname) => pathname }), true);
  assert.equal(coreJournalToolIsTrusted({
    lstatFn: (pathname) => ({
      ...trustedStats(pathname),
      mode: pathname === "/usr/bin" ? 0o40777 : trustedStats(pathname).mode,
    }),
    realpathFn: (pathname) => pathname,
  }), false);
  assert.equal(coreJournalToolIsTrusted({
    lstatFn: (pathname) => ({
      ...trustedStats(pathname),
      nlink: pathname === "/usr/bin/journalctl" ? 2 : 1,
    }),
    realpathFn: (pathname) => pathname,
  }), false);

  const verified = (tokens) => ({ status: "verified", cursors: new Set(tokens) });
  const observation = buildCoreObservation([
    verified(["baseline-a"]),
    verified(["baseline-a"]),
    verified(["baseline-a", "round-two-core-private-row"]),
    verified(["baseline-a", "round-two-core-private-row"]),
  ], Buffer.alloc(32, 9));
  assert.equal(observation.status, "failed");
  assert.deepEqual(observation.rounds.map((round) => round.added_entry_count), [0, 1, 0]);
  assert.match(observation.baseline_cursor_set_token, /^[0-9a-f]{64}$/);
  assert.deepEqual(observation.rounds.map((round) => Object.keys(round).sort()), [
    ["added_entry_count", "cursor_set_token", "round", "status"],
    ["added_entry_count", "cursor_set_token", "round", "status"],
    ["added_entry_count", "cursor_set_token", "round", "status"],
  ]);
  assert.equal(JSON.stringify(observation).includes("round-two-core-private-row"), false);
  assert.match(observation.rounds[1].cursor_set_token, /^[0-9a-f]{64}$/);

  const failedThenUnavailable = buildCoreObservation([
    verified([]),
    verified(["known-new-core"]),
    { status: "unverified", reason: "query_failed" },
    verified(["known-new-core"]),
  ], Buffer.alloc(32, 9));
  assert.equal(failedThenUnavailable.status, "failed", "a later observation gap must not hide an already observed core");
  assert.equal(JSON.stringify(failedThenUnavailable).includes("known-new-core"), false);

  const regressed = buildCoreObservation([
    verified(["baseline-a", "baseline-b"]),
    verified(["baseline-b"]),
    verified(["baseline-b"]),
    verified(["baseline-b"]),
  ], Buffer.alloc(32, 9));
  assert.equal(regressed.status, "unverified");
  assert.equal(regressed.reason, "cursor_set_regressed");
  assert.equal(JSON.stringify(regressed).includes("baseline-a"), false);

  const addedAndRegressed = buildCoreObservation([
    verified(["baseline-a"]),
    verified(["new-core"]),
    verified(["new-core"]),
    verified(["new-core"]),
  ], Buffer.alloc(32, 9));
  assert.equal(addedAndRegressed.status, "failed");
  assert.equal(JSON.stringify(addedAndRegressed).includes("new-core"), false);

  const unavailable = buildCoreObservation([
    { status: "unverified", reason: "json_unavailable" },
    verified([]),
    verified([]),
    verified([]),
  ], Buffer.alloc(32, 9));
  assert.equal(unavailable.status, "unverified");
  assert.equal(unavailable.reason, "json_unavailable");
  assert.deepEqual(unavailable.rounds.map((round) => round.status), ["unverified", "unverified", "unverified"]);
});

test("journalctl query is fixed-path, bounded and converts tool failures into unverified evidence", async () => {
  const uid = 1000;
  const row = JSON.stringify({
    MESSAGE_ID: "fc2e22bc6ee647b6b90729ab34a250b1",
    __CURSOR: "private-cursor",
    __REALTIME_TIMESTAMP: "1786400000000000",
    COREDUMP_PID: "4242",
    COREDUMP_UID: String(uid),
    COREDUMP_EXE: ELECTRON_PATH,
    COREDUMP_SIGNAL: "5",
    COREDUMP_SIGNAL_NAME: "SIGTRAP",
    COREDUMP_TIMESTAMP: "1786400000000000",
  });
  const fakeChild = ({ stdout = "", stderr = "", code = 0, signal = null, error = null } = {}) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdout.setEncoding = () => {};
    child.stderr.setEncoding = () => {};
    child.kill = () => {};
    queueMicrotask(() => {
      if (error) child.emit("error", error);
      if (stdout) child.stdout.emit("data", stdout);
      if (stderr) child.stderr.emit("data", stderr);
      child.emit("close", code, signal);
    });
    return child;
  };

  let invocation = null;
  const verified = await queryCoreJournalSnapshot({
    uid,
    trustFn: () => true,
    handlerTrustFn: () => true,
    spawnFn: (executable, args, options) => {
      invocation = { executable, args, options };
      return fakeChild({ stdout: row });
    },
  });
  assert.equal(verified.status, "verified");
  assert.deepEqual([...verified.cursors], ["private-cursor"]);
  assert.equal(invocation.executable, "/usr/bin/journalctl");
  assert.deepEqual(invocation.args, buildCoreJournalArgs(uid));
  assert.deepEqual(invocation.options.env, {
    PATH: "/usr/sbin:/usr/bin:/sbin:/bin",
    LC_ALL: "C",
    LANG: "C",
  });

  assert.deepEqual(await queryCoreJournalSnapshot({
    uid,
    trustFn: () => false,
    handlerTrustFn: () => true,
    spawnFn: () => { throw new Error("must not spawn"); },
  }), { status: "unverified", reason: "tool_untrusted" });
  assert.deepEqual(await queryCoreJournalSnapshot({
    uid,
    trustFn: () => true,
    handlerTrustFn: () => true,
    spawnFn: () => { throw new Error("missing"); },
  }), { status: "unverified", reason: "query_failed" });
  assert.deepEqual(await queryCoreJournalSnapshot({
    uid,
    trustFn: () => true,
    handlerTrustFn: () => true,
    spawnFn: () => fakeChild({ code: 1 }),
  }), { status: "unverified", reason: "query_failed" });
  assert.deepEqual(await queryCoreJournalSnapshot({
    uid,
    trustFn: () => true,
    handlerTrustFn: () => true,
    spawnFn: () => fakeChild({ stdout: "not-json" }),
  }), { status: "unverified", reason: "json_unavailable" });

  assert.deepEqual(await queryCoreJournalSnapshot({
    uid,
    trustFn: () => true,
    handlerTrustFn: () => false,
    spawnFn: () => { throw new Error("must not spawn"); },
  }), { status: "unverified", reason: "handler_unverified" });
  assert.deepEqual(await queryCoreJournalSnapshot({
    uid,
    trustFn: () => true,
    handlerTrustFn: () => true,
    spawnFn: () => fakeChild({ stderr: "No journal files were opened due to insufficient permissions.\n" }),
  }), { status: "unverified", reason: "journal_access_unverified" });
});

test("settled core journal observation waits through delayed systemd-coredump writes", async () => {
  const snapshots = [
    { status: "verified", cursors: new Set() },
    { status: "verified", cursors: new Set() },
    { status: "verified", cursors: new Set(["delayed-core"]) },
    { status: "verified", cursors: new Set(["delayed-core"]) },
    { status: "verified", cursors: new Set(["delayed-core"]) },
  ];
  let calls = 0;
  const settled = await querySettledCoreJournalSnapshot({
    sampleCount: 5,
    intervalMs: 1,
    queryFn: async () => snapshots[calls++],
    sleepFn: async () => {},
  });
  assert.equal(calls, 5, "the observer must not accept an early empty pair as settled");
  assert.equal(settled.status, "verified");
  assert.deepEqual([...settled.cursors], ["delayed-core"]);

  const changing = [
    new Set(),
    new Set(),
    new Set(),
    new Set(["late-core"]),
    new Set(["late-core", "later-core"]),
  ];
  let changingCalls = 0;
  assert.deepEqual(await querySettledCoreJournalSnapshot({
    sampleCount: 5,
    intervalMs: 1,
    queryFn: async () => ({ status: "verified", cursors: changing[changingCalls++] }),
    sleepFn: async () => {},
  }), { status: "unverified", reason: "journal_not_settled" });
});

test("process identity uses Linux start time so PID reuse counts as original-process exit", () => {
  const fields = Array.from({ length: 40 }, (_, index) => String(index + 3));
  fields[19] = "987654";
  const raw = `4242 (electron helper) ${fields.join(" ")}`;
  const identity = processIdentityFromStat(4242, raw);
  assert.deepEqual(identity, { pid: 4242, start_time_ticks: "987654" });
  assert.deepEqual(inspectProcessIdentity(identity, () => raw), { status: "present" });
  assert.equal(processIdentityStillPresent(identity, () => raw), true);
  const reusedFields = [...fields];
  reusedFields[19] = "987655";
  assert.deepEqual(inspectProcessIdentity(identity, () => `4242 (electron helper) ${reusedFields.join(" ")}`), { status: "gone", reason: "pid_reused" });
  assert.equal(processIdentityStillPresent(identity, () => `4242 (electron helper) ${reusedFields.join(" ")}`), false);
  const gone = Object.assign(new Error("gone"), { code: "ENOENT" });
  assert.deepEqual(inspectProcessIdentity(identity, () => { throw gone; }), { status: "gone", reason: "proc_absent" });
  assert.equal(processIdentityStillPresent(identity, () => { throw gone; }), false);
  const denied = Object.assign(new Error("denied"), { code: "EACCES" });
  assert.deepEqual(inspectProcessIdentity(identity, () => { throw denied; }), { status: "unverified", reason: "proc_unreadable" });
  assert.throws(() => processIdentityStillPresent(identity, () => { throw denied; }), /could not be verified/);
  assert.deepEqual(inspectProcessIdentity(identity, () => "truncated"), { status: "unverified", reason: "proc_malformed" });
});

test("Electron helper descendants are captured by pid and start time before close", () => {
  const identities = new Map([
    [101, { pid: 101, start_time_ticks: "1010" }],
    [102, { pid: 102, start_time_ticks: "1020" }],
    [103, { pid: 103, start_time_ticks: "1030" }],
  ]);
  const children = new Map([[100, "101 102\n"], [101, "103\n"], [102, ""], [103, ""]]);
  const executables = new Map([
    [101, ELECTRON_PATH],
    [102, "/opt/taiji-agent/runtime/agent/venv/bin/python"],
    [103, "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/chrome-sandbox"],
  ]);
  assert.deepEqual(captureElectronHelperIdentities({ pid: 100, start_time_ticks: "1000" }, {
    readChildrenFn: (pid) => children.get(pid) || "",
    captureIdentityFn: (pid) => identities.get(pid),
    readlinkFn: (pid) => executables.get(pid),
  }), [identities.get(101), identities.get(103)]);
  const denied = Object.assign(new Error("denied"), { code: "EACCES" });
  assert.throws(() => captureElectronHelperIdentities({ pid: 100, start_time_ticks: "1000" }, {
    readChildrenFn: () => { throw denied; },
    captureIdentityFn: (pid) => identities.get(pid),
    readlinkFn: (pid) => executables.get(pid),
  }), /denied/);
});

test("secondary fast clean exit is accepted only when proc reports the pid absent", async () => {
  const gone = Object.assign(new Error("gone"), { code: "ENOENT" });
  assert.deepEqual(await captureChildIdentityOrCleanExit({ pid: 5252 }, Promise.resolve({ code: 0, signal: null, error: null }), Date.now() + 1000, {
    captureIdentityFn: () => { throw gone; },
  }), { identity: null, clean_exit: true });
  const denied = Object.assign(new Error("denied"), { code: "EACCES" });
  await assert.rejects(() => captureChildIdentityOrCleanExit({ pid: 5253 }, Promise.resolve({ code: 0, signal: null, error: null }), Date.now() + 1000, {
    captureIdentityFn: () => { throw denied; },
  }), /identity could not be established/);
});

test("visible model configuration must match the authoritative public projection", () => {
  const payload = {
    ok: true,
    profile: "default",
    main_request_id: "a".repeat(32),
    main: {
      provider: "deepseek",
      model: "deepseek-chat",
      key_env: "",
      key_status: { configured: true, source: "env_file", env_var: "DEEPSEEK_API_KEY" },
    },
  };
  const visible = {
    pane_visible: true,
    hero_state: "ok",
    main_badge_state: "ok",
    provider_summary: "DeepSeek · deepseek",
    model_summary: "deepseek-chat",
    key_summary: "API 密钥已配置",
  };
  assert.equal(visibleModelConfigurationMatches(visible, payload), true);
  assert.equal(visibleModelConfigurationMatches({ ...visible, key_summary: "未配置" }, payload), false);
  assert.equal(visibleModelConfigurationMatches({ ...visible, model_summary: "stale-model" }, payload), false);
});

test("persistent profile continuity uses a private cookie marker and deletes it after round three", async () => {
  const cookies = new Map();
  const calls = [];
  const client = {
    async send(method, params = {}) {
      calls.push({ method, params });
      if (method === "Network.setCookie") {
        cookies.set(params.name, { ...params, domain: "127.0.0.1" });
        return { success: true };
      }
      if (method === "Network.getAllCookies") return { cookies: [...cookies.values()] };
      if (method === "Network.deleteCookies") {
        cookies.delete(params.name);
        return {};
      }
      throw new Error(`unexpected CDP method: ${method}`);
    },
  };
  const marker = await createProfileContinuityMarker(
    client,
    "http://127.0.0.1:18789",
    "a".repeat(64),
    "b".repeat(32),
    Buffer.alloc(32, 4),
  );
  assert.equal(await verifyProfileContinuityMarker(client, "http://127.0.0.1:18790", marker), true);
  assert.match(marker.continuity_token, /^[0-9a-f]{64}$/);
  assert.equal(JSON.stringify({ continuity_token: marker.continuity_token }).includes(marker.value), false);
  await deleteProfileContinuityMarker(client, "http://127.0.0.1:18791", marker);
  assert.equal(await verifyProfileContinuityMarker(client, "http://127.0.0.1:18792", marker), false);
  assert.deepEqual(calls.map((call) => call.method), [
    "Network.setCookie",
    "Network.getAllCookies",
    "Network.deleteCookies",
    "Network.getAllCookies",
  ]);
});

test("driver source preserves the default persistent Electron profile without GPU workarounds", () => {
  const source = fs.readFileSync(DRIVER, "utf8");
  for (const forbidden of ["--user-data-dir", "disableHardwareAcceleration", "--disable-gpu"]) {
    assert.equal(source.includes(forbidden), false, `${forbidden} must not be introduced`);
  }
  assert.match(source, /\/usr\/bin\/journalctl/);
  assert.match(source, /fc2e22bc6ee647b6b90729ab34a250b1/);
  assert.equal(source.includes("coredumpctl"), false);
  assert.match(source, /Page\.close/);
  assert.equal((source.match(/verifyVisibleModelConfiguration\(/g) || []).length >= 3, true, "definition plus first/lightweight call sites are required");
  assert.equal((source.match(/querySettledCoreJournalSnapshot\(/g) || []).length >= 4, true, "definition plus baseline/round call sites are required");
  assert.equal((source.match(/configureVisibleOnboardingCredential\(/g) || []).length >= 2, true, "the visible setup step must call the secure credential gate");
  assert.equal(source.includes("outputTail"), false, "formal failures must not retain Electron output tails");
});

test("fixed desktop entry path remains under the installed product surface", () => {
  assert.equal(DESKTOP_ENTRY, "/usr/share/applications/taiji-agent.desktop");
});
