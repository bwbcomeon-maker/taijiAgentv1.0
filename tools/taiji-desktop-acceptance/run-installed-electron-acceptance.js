#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const ELECTRON_PATH = "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron";
const APP_DIR = "/opt/taiji-agent/apps/taiji-desktop";
const DESKTOP_ENTRY = "/usr/share/applications/taiji-agent.desktop";
const INSTALLED_PYTHON = "/opt/taiji-agent/runtime/agent/venv/bin/python";
const INSTALLED_WEBUI_ENTRIES = new Set([
  "/opt/taiji-agent/runtime/web/server.py",
  "/opt/taiji-agent/runtime/web/server.pyc",
]);
const SESSION_RE = /^[0-9a-f]{32}$/;
const CHALLENGE_RE = /^[0-9a-f]{64,128}$/;
const DESKTOP_TOKEN_RE = /^[0-9a-f]{64}$/;
const INCIDENT_RE = /^inc-[0-9a-f]{12,32}$/;
const TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const PUBLIC_VERSION_RE = /^(?:v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?|[0-9a-f]{7,40}(?:-dirty(?:\.[0-9a-f]{7,40})?)?)$/;
const UNSAFE_VERSION_RE = /(?:hermes|password|passwd|passphrase|secret|token|bearer|(?:^|[-_.])sk-|(?:^|[-_.])key(?:[-_.]|$))/i;
const EXPECTED_COMPONENT_LABELS = {
  webui: "桌面界面",
  agent: "智能体服务",
  gateway: "本地任务服务",
  license: "授权状态",
  docx: "文档引擎",
  skills: "专家能力",
  node: "运行环境",
};
const EXPECTED_COMPONENTS = Object.keys(EXPECTED_COMPONENT_LABELS);
const PROBE_PROMPT = "请读取本次附加的文本文件，并且只回复文件中唯一的验收代码，不要添加引号、标点、解释或其他文字。";
const RESULT_BASENAME = "driver-result.json";
const SCREENSHOT_BASENAME = "desktop-app.png";
const SUPPORT_BUNDLE_BASENAME = "taiji-support-bundle.json";
const FIXTURE_BASENAME = "taiji-attachment-probe.txt";

function parseArgs(argv) {
  const allowed = new Set(["--electron", "--app-dir", "--output-dir", "--session-id", "--challenge", "--timeout-ms"]);
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!allowed.has(key)) throw new Error(`unknown argument: ${key || "<empty>"}`);
    if (values.has(key)) throw new Error(`duplicate argument: ${key}`);
    if (typeof value !== "string" || !value) throw new Error(`missing value for ${key}`);
    values.set(key, value);
  }
  for (const key of allowed) {
    if (!values.has(key)) throw new Error(`missing required argument: ${key}`);
  }

  const electron = values.get("--electron");
  const appDir = values.get("--app-dir");
  const outputDir = values.get("--output-dir");
  const sessionId = values.get("--session-id");
  const challenge = values.get("--challenge");
  const timeoutMs = Number(values.get("--timeout-ms"));
  if (electron !== ELECTRON_PATH) throw new Error(`--electron must use the fixed installed Electron path: ${ELECTRON_PATH}`);
  if (appDir !== APP_DIR) throw new Error(`--app-dir must use the fixed installed App path: ${APP_DIR}`);
  if (!path.isAbsolute(outputDir) || path.resolve(outputDir) !== outputDir) {
    throw new Error("--output-dir must be a normalized absolute path");
  }
  if (!SESSION_RE.test(sessionId)) throw new Error("--session-id must be 32 lowercase hexadecimal characters");
  if (!CHALLENGE_RE.test(challenge)) throw new Error("--challenge must be 64-128 lowercase hexadecimal characters");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 30000 || timeoutMs > 1800000) {
    throw new Error("--timeout-ms must be an integer between 30000 and 1800000");
  }
  return { electron, appDir, outputDir, sessionId, challenge, timeoutMs };
}

function buildElectronArgs(port) {
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) throw new Error("invalid CDP port");
  return [
    "--remote-debugging-address=127.0.0.1",
    `--remote-debugging-port=${port}`,
    APP_DIR,
  ];
}

function buildInstalledAcceptanceEnv(sourceEnv = {}) {
  const env = { ...sourceEnv };
  for (const key of Object.keys(env)) {
    if (
      key.startsWith("TAIJI_")
      || key.startsWith("HERMES_")
      || ["PYTHONPATH", "PYTHONHOME", "ELECTRON_RUN_AS_NODE", "NODE_OPTIONS"].includes(key)
    ) {
      delete env[key];
    }
  }
  env.TAIJI_AGENT_ROOT = "/opt/taiji-agent";
  env.TAIJI_AGENT_USE_USER_DIRS = "1";
  return env;
}

function buildProbeCode(challenge, sessionId) {
  if (!CHALLENGE_RE.test(challenge)) throw new Error("invalid challenge");
  if (!SESSION_RE.test(sessionId)) throw new Error("invalid session id");
  const digest = crypto.createHash("sha256").update(`${challenge}:${sessionId}`, "utf8").digest("hex");
  return `TAIJI-ATTACHMENT-PROBE-${digest.slice(0, 32)}`;
}

function validateDesktopAppUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(String(rawUrl || ""));
  } catch (_) {
    throw new Error("Electron page target URL is invalid");
  }
  if (parsed.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(parsed.hostname)) {
    throw new Error("Electron page target is not a loopback App URL");
  }
  if (parsed.searchParams.has("taiji_desktop_token")) {
    throw new Error("Electron page target must not expose the desktop token");
  }
  const markerValues = parsed.searchParams.getAll("taiji_desktop");
  if (markerValues.length !== 1 || markerValues[0] !== "1") {
    throw new Error("Electron page target is missing the desktop marker");
  }
  if ([...parsed.searchParams.keys()].some((name) => name !== "taiji_desktop")) {
    throw new Error("Electron page target has an unexpected query parameter");
  }
  return { origin: parsed.origin, url: parsed.toString() };
}

function validateDesktopTarget(target) {
  if (!target || target.type !== "page") throw new Error("CDP target is not an Electron page target");
  if (typeof target.webSocketDebuggerUrl !== "string" || !target.webSocketDebuggerUrl.startsWith("ws://127.0.0.1:")) {
    throw new Error("Electron page target has no loopback CDP websocket");
  }
  return {
    ...validateDesktopAppUrl(target.url),
    websocket: target.webSocketDebuggerUrl,
  };
}

function validateDesktopAuthCookies(cookies, appOrigin) {
  let parsedOrigin;
  try {
    parsedOrigin = new URL(String(appOrigin || ""));
  } catch (_) {
    throw new Error("desktop auth cookie origin is invalid");
  }
  if (parsedOrigin.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(parsedOrigin.hostname)) {
    throw new Error("desktop auth cookie origin is not loopback HTTP");
  }
  const matches = (Array.isArray(cookies) ? cookies : []).filter(
    (cookie) => cookie && cookie.name === "taiji_desktop_token",
  );
  if (matches.length !== 1) throw new Error("expected exactly one desktop auth cookie");
  const cookie = matches[0];
  const domain = String(cookie.domain || "").replace(/^\./, "");
  if (domain !== parsedOrigin.hostname) throw new Error("desktop auth cookie has the wrong host");
  if (cookie.path !== "/") throw new Error("desktop auth cookie has the wrong path");
  if (cookie.httpOnly !== true) throw new Error("desktop auth cookie is not HttpOnly");
  if (cookie.sameSite !== "Strict") throw new Error("desktop auth cookie is not SameSite Strict");
  if (!DESKTOP_TOKEN_RE.test(String(cookie.value || ""))) {
    throw new Error("desktop auth cookie has an invalid value format");
  }
  return {
    name: "taiji_desktop_token",
    present: true,
    http_only: true,
    same_site: "Strict",
    path: "/",
    value_format: "lowercase-hex-64",
  };
}

function redactDesktopUrl(raw) {
  const rendered = String(raw || "");
  if (!/^(?:https?|wss?):\/\//i.test(rendered)) {
    return rendered.replace(/taiji_desktop_token=[^&\s]+/g, "taiji_desktop_token=<redacted>");
  }
  try {
    const parsed = new URL(rendered);
    if (parsed.searchParams.has("taiji_desktop_token")) parsed.searchParams.set("taiji_desktop_token", "<redacted>");
    return parsed.toString();
  } catch (_) {
    return rendered.replace(/taiji_desktop_token=[^&\s]+/g, "taiji_desktop_token=<redacted>");
  }
}

function isExpectedDesktopHttpFailure(entry, appOrigin) {
  if (!entry || entry.status !== 404 || entry.method !== "GET") return false;
  try {
    const url = new URL(entry.url);
    return url.origin === new URL(appOrigin).origin
      && url.pathname === "/api/expert-teams/run"
      && Boolean(url.searchParams.get("session_id")?.trim());
  } catch (_) {
    return false;
  }
}

function filterUnexpectedHttpFailures(entries, appOrigin) {
  return (Array.isArray(entries) ? entries : []).filter((entry) => !isExpectedDesktopHttpFailure(entry, appOrigin));
}

function isExpectedBackgroundConsoleError(entry, appOrigin) {
  if (!entry || entry.source !== "log") return false;
  const text = String(entry.text || "").replace(/^console:\s*/, "");
  if (text !== "Failed to load resource: the server responded with a status of 404 (Not Found)") return false;
  try {
    const url = new URL(entry.url);
    return url.origin === new URL(appOrigin).origin
      && url.pathname === "/api/expert-teams/run"
      && Boolean(url.searchParams.get("session_id")?.trim());
  } catch (_) {
    return false;
  }
}

function filterUnexpectedJsErrors(entries, appOrigin) {
  return (Array.isArray(entries) ? entries : []).filter((entry) => !isExpectedBackgroundConsoleError(entry, appOrigin));
}

function normalizeMessageContent(content) {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content.map((part) => {
    if (!part || typeof part !== "object") return "";
    if (part.type === "text") return String(part.text || "");
    if (part.type === "input_text") return String(part.input_text || "");
    return "";
  }).join("").trim();
}

function hasAttachment(items, name) {
  return Array.isArray(items) && items.some((item) => {
    if (typeof item === "string") return item === name;
    return item && typeof item === "object" && [item.name, item.filename].includes(name);
  });
}

function completionSnapshotPassed(snapshot, expected) {
  if (!snapshot || !expected) return false;
  return snapshot.sessionId === expected.sessionId
    && snapshot.busy === false
    && !snapshot.activeStreamId
    && !snapshot.pendingUserMessage
    && !snapshot.persistedPendingUserMessage
    && hasAttachment(snapshot.userAttachments, expected.attachmentName)
    && hasAttachment(snapshot.persistedUserAttachments, expected.attachmentName)
    && normalizeMessageContent(snapshot.assistantContent) === expected.probeCode
    && normalizeMessageContent(snapshot.persistedAssistantContent) === expected.probeCode
    && snapshot.assistantError === false
    && snapshot.assistantLicenseBlocked === false;
}

function calculatedOverall(components) {
  const byId = Object.fromEntries(components.map((item) => [item.id, item.status]));
  if (["webui", "agent", "gateway", "license"].some((id) => byId[id] === "blocked")) return "blocked";
  const material = components.map((item) => item.status).filter((status) => status !== "not_applicable");
  return material.some((status) => ["blocked", "degraded", "unknown"].includes(status)) ? "degraded" : "ready";
}

function supportBundleIsSafe(bundle) {
  if (!bundle || typeof bundle !== "object" || Array.isArray(bundle)) return false;
  let rendered;
  try {
    rendered = JSON.stringify(bundle);
  } catch (_) {
    return false;
  }
  if (Buffer.byteLength(rendered, "utf8") >= 64 * 1024) return false;
  const topKeys = Object.keys(bundle).sort();
  if (topKeys.join(",") !== "diagnostics,manifest,schema") return false;
  if (bundle.schema !== "taiji.product.support-bundle.v1") return false;
  const manifest = bundle.manifest;
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) return false;
  if (Object.keys(manifest).sort().join(",") !== "logs_included,paths_included,redacted,secrets_included") return false;
  if (manifest.redacted !== true || manifest.logs_included !== false || manifest.paths_included !== false || manifest.secrets_included !== false) return false;
  const diagnostics = bundle.diagnostics;
  if (!diagnostics || typeof diagnostics !== "object" || Array.isArray(diagnostics)) return false;
  if (Object.keys(diagnostics).sort().join(",") !== "components,generated_at,incident_id,overall,schema") return false;
  if (diagnostics.schema !== "taiji.product.diagnostics.v1") return false;
  if (!TIMESTAMP_RE.test(diagnostics.generated_at || "")) return false;
  if (!INCIDENT_RE.test(diagnostics.incident_id || "")) return false;
  if (diagnostics.overall !== "ready") return false;
  if (!Array.isArray(diagnostics.components)) return false;
  if (diagnostics.components.map((item) => item && item.id).join(",") !== EXPECTED_COMPONENTS.join(",")) return false;
  const allowedStatuses = new Set(["ready", "degraded", "blocked", "not_applicable", "unknown"]);
  if (diagnostics.components.some((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return true;
    const keys = Object.keys(item);
    if (keys.some((key) => !["id", "label", "status", "version"].includes(key))) return true;
    if (!["id", "label", "status"].every((key) => keys.includes(key))) return true;
    if (item.label !== EXPECTED_COMPONENT_LABELS[item.id] || !allowedStatuses.has(item.status)) return true;
    return Object.hasOwn(item, "version") && (
      typeof item.version !== "string"
      || !PUBLIC_VERSION_RE.test(item.version)
      || UNSAFE_VERSION_RE.test(item.version)
    );
  })) return false;
  return diagnostics.overall === calculatedOverall(diagnostics.components);
}

function parsePid(raw) {
  const value = String(raw || "");
  if (!/^[0-9]+\n?$/.test(value)) return null;
  const pid = Number(value.trim());
  return Number.isSafeInteger(pid) && pid > 1 ? pid : null;
}

class CdpClient {
  constructor(socket, timeoutMs) {
    if (!socket || typeof socket.send !== "function") throw new Error("CDP websocket is required");
    this.socket = socket;
    this.timeoutMs = timeoutMs;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.closed = false;
    this.handleMessage = (event) => this._handleMessage(event);
    this.handleClose = () => this._handleClose(new Error("CDP websocket closed"));
    this.handleError = () => this._handleClose(new Error("CDP websocket failed"));
    socket.addEventListener("message", this.handleMessage);
    socket.addEventListener("close", this.handleClose);
    socket.addEventListener("error", this.handleError);
  }

  on(method, listener) {
    if (!this.listeners.has(method)) this.listeners.set(method, new Set());
    this.listeners.get(method).add(listener);
    return () => this.listeners.get(method)?.delete(listener);
  }

  send(method, params = {}) {
    if (this.closed || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error(`CDP ${method} failed: websocket is not open`));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP ${method} timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      this.pending.set(id, { method, resolve, reject, timer });
      try {
        this.socket.send(JSON.stringify({ id, method, params }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new Error(`CDP ${method} failed: ${error.message}`));
      }
    });
  }

  _handleMessage(event) {
    let message;
    try {
      message = JSON.parse(typeof event.data === "string" ? event.data : Buffer.from(event.data).toString("utf8"));
    } catch (_) {
      this._handleClose(new Error("CDP websocket returned invalid JSON"));
      return;
    }
    if (Number.isSafeInteger(message.id)) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) {
        pending.reject(new Error(`CDP ${pending.method} failed (${message.error.code}): ${message.error.message}`));
      } else {
        pending.resolve(message.result || {});
      }
      return;
    }
    if (typeof message.method !== "string") return;
    for (const listener of this.listeners.get(message.method) || []) {
      try {
        listener(message.params || {});
      } catch (_) {
        // Listener failures are isolated; the acceptance workflow owns its state.
      }
    }
  }

  _handleClose(error) {
    if (this.closed) return;
    this.closed = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  close() {
    if (!this.closed && this.socket.readyState < WebSocket.CLOSING) this.socket.close();
    this._handleClose(new Error("CDP client closed"));
  }
}

function buildDriverResult(measurements) {
  const requiredChecks = [
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "window_close_exit",
    "diagnostic_export",
  ];
  for (const check of requiredChecks) {
    if (measurements?.checks?.[check] !== true) throw new Error(`driver check failed: ${check}`);
  }
  if ((measurements.jsErrors || []).length) throw new Error("driver observed JavaScript errors");
  if ((measurements.unexpectedHttpFailures || []).length) throw new Error("driver observed unexpected HTTP failures");
  if (!SESSION_RE.test(measurements.sessionId || "")) throw new Error("driver result has invalid session id");
  if (!CHALLENGE_RE.test(measurements.challenge || "")) throw new Error("driver result has invalid challenge");
  if (!Number.isSafeInteger(measurements.electronPid) || measurements.electronPid <= 1) throw new Error("driver result has invalid Electron pid");
  if (!Number.isSafeInteger(measurements.agentPid) || measurements.agentPid <= 1) throw new Error("driver result has invalid Agent pid");
  if (!Number.isSafeInteger(measurements.webPid) || measurements.webPid <= 1) throw new Error("driver result has invalid WebUI pid");
  if (measurements.exitCode !== 0) throw new Error("Electron did not exit successfully after closing its window");
  if (typeof measurements.model !== "string" || !measurements.model.trim()) throw new Error("driver result has no model identity");
  const validatedApp = validateDesktopAppUrl(measurements.appUrl);
  if (String(measurements.webuiOrigin || "") !== validatedApp.origin) {
    throw new Error("driver app URL and WebUI origin do not identify the same App");
  }
  const desktopAuthCookie = measurements.desktopAuthCookie;
  if (
    !desktopAuthCookie
    || desktopAuthCookie.name !== "taiji_desktop_token"
    || desktopAuthCookie.present !== true
    || desktopAuthCookie.http_only !== true
    || desktopAuthCookie.same_site !== "Strict"
    || desktopAuthCookie.path !== "/"
    || desktopAuthCookie.value_format !== "lowercase-hex-64"
  ) {
    throw new Error("driver result has no verified desktop auth cookie");
  }
  for (const [key, value] of [
    ["electron executable", measurements.electronExecutableSha256],
    ["desktop entry", measurements.desktopEntrySha256],
    ["probe", measurements.probeSha256],
  ]) {
    if (!/^[0-9a-f]{64}$/.test(value || "")) throw new Error(`driver result has invalid ${key} SHA256`);
  }
  return {
    schema: "taiji.desktop.acceptance-driver.v1",
    acceptance_session_id: measurements.sessionId,
    challenge_nonce: measurements.challenge,
    electron_pid: measurements.electronPid,
    electron_executable: ELECTRON_PATH,
    electron_executable_sha256: measurements.electronExecutableSha256,
    desktop_entry_sha256: measurements.desktopEntrySha256,
    app_url: validatedApp.url,
    webui_origin: validatedApp.origin,
    desktop_auth_cookie: { ...desktopAuthCookie },
    model: String(measurements.model || ""),
    attachment_probe_sha256: measurements.probeSha256,
    agent_pid: measurements.agentPid,
    web_pid: measurements.webPid,
    screenshot_basename: "desktop-app.png",
    diagnostic_basename: "taiji-support-bundle.json",
    checks: Object.fromEntries(requiredChecks.map((key) => [key, true])),
    js_error_count: 0,
    unexpected_http_failures: 0,
    electron_exit_code: 0,
  };
}

function safeErrorText(error) {
  const rendered = error && error.stack ? error.stack : String(error || "unknown error");
  return redactDesktopUrl(rendered)
    .replace(/taiji_desktop_token=[^&\s)]+/g, "taiji_desktop_token=<redacted>")
    .replace(/\b[0-9a-f]{64,128}\b/g, "<redacted-hex>");
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function remainingTime(deadline, label) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw new Error(`${label} timed out`);
  return remaining;
}

async function waitFor(predicate, { deadline, intervalMs = 250, label }) {
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await predicate();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await sleep(intervalMs);
  }
  const detail = lastError ? `: ${safeErrorText(lastError).split("\n")[0]}` : "";
  throw new Error(`${label} timed out${detail}`);
}

async function reserveLoopbackPort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, resolve);
  });
  const address = server.address();
  const port = address && typeof address === "object" ? address.port : 0;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  if (!Number.isSafeInteger(port) || port < 1024) throw new Error("could not reserve a loopback CDP port");
  return port;
}

async function connectWebSocket(url, deadline) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      try { socket.close(); } catch (_) {}
      reject(new Error("CDP websocket open timed out"));
    }, remainingTime(deadline, "CDP websocket"));
    const cleanup = () => {
      clearTimeout(timer);
      socket.removeEventListener("open", onOpen);
      socket.removeEventListener("error", onError);
    };
    const onOpen = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error("CDP websocket open failed"));
    };
    socket.addEventListener("open", onOpen);
    socket.addEventListener("error", onError);
  });
  return socket;
}

async function findDesktopTarget(port, deadline, childState) {
  return waitFor(async () => {
    if (childState.error) throw childState.error;
    if (childState.exited) throw new Error(`Electron exited before the desktop page was ready (${childState.code ?? childState.signal})`);
    const response = await fetch(`http://127.0.0.1:${port}/json/list`, {
      signal: AbortSignal.timeout(Math.min(1500, remainingTime(deadline, "desktop CDP target"))),
    });
    if (!response.ok) return null;
    const targets = await response.json();
    if (!Array.isArray(targets)) return null;
    for (const target of targets) {
      try {
        return { target, desktop: validateDesktopTarget(target) };
      } catch (_) {
        // Startup data: pages and DevTools targets are expected before App load.
      }
    }
    return null;
  }, { deadline, intervalMs: 300, label: "real Electron desktop page" });
}

async function evaluate(client, expression, deadline) {
  const response = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true,
  }, remainingTime(deadline, "Runtime.evaluate"));
  if (response.exceptionDetails) {
    const detail = response.exceptionDetails.exception?.description || response.exceptionDetails.text || "unknown renderer exception";
    throw new Error(`renderer evaluation failed: ${detail}`);
  }
  return response.result ? response.result.value : undefined;
}

async function visibleElementCenter(client, selector, deadline, label) {
  const selectorJson = JSON.stringify(selector);
  return waitFor(() => evaluate(client, `(() => {
    const element = document.querySelector(${selectorJson});
    if (!element || element.disabled || element.getAttribute("aria-disabled") === "true") return null;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    if (rect.width < 1 || rect.height < 1 || style.display === "none" || style.visibility === "hidden" || Number(style.opacity) <= 0 || style.pointerEvents === "none") return null;
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return null;
    const hit = document.elementFromPoint(x, y);
    if (!hit || (hit !== element && !element.contains(hit))) return null;
    const accessibleName = String(element.getAttribute("aria-label") || element.getAttribute("title") || element.textContent || "").trim();
    if (!accessibleName) return null;
    return { x, y, width: rect.width, height: rect.height, accessibleName };
  })()`, deadline), {
    deadline,
    intervalMs: 150,
    label: `hit-tested visible ${label}`,
  });
}

async function dispatchPointerClick(client, point) {
  await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: point.x, y: point.y });
  await client.send("Input.dispatchMouseEvent", {
    type: "mousePressed",
    x: point.x,
    y: point.y,
    button: "left",
    buttons: 1,
    clickCount: 1,
  });
  await client.send("Input.dispatchMouseEvent", {
    type: "mouseReleased",
    x: point.x,
    y: point.y,
    button: "left",
    buttons: 0,
    clickCount: 1,
  });
}

async function physicalClickVisibleElement(client, selector, deadline, label) {
  const point = await visibleElementCenter(client, selector, deadline, label);
  await dispatchPointerClick(client, point);
  return point;
}

async function insertTextThroughVisibleComposer(client, selector, value, deadline) {
  await physicalClickVisibleElement(client, selector, deadline, "chat composer");
  const selectorJson = JSON.stringify(selector);
  await waitFor(() => evaluate(client, `(() => {
    const element = document.querySelector(${selectorJson});
    return Boolean(element && document.activeElement === element);
  })()`, deadline), { deadline, intervalMs: 100, label: "real chat composer focus" });
  await client.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "a",
    code: "KeyA",
    modifiers: 2,
    windowsVirtualKeyCode: 65,
  });
  await client.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "a",
    code: "KeyA",
    modifiers: 2,
    windowsVirtualKeyCode: 65,
  });
  await client.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Backspace",
    code: "Backspace",
    windowsVirtualKeyCode: 8,
  });
  await client.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Backspace",
    code: "Backspace",
    windowsVirtualKeyCode: 8,
  });
  await client.send("Input.insertText", { text: value });
  await waitFor(() => evaluate(client, `(() => {
    const element = document.querySelector(${selectorJson});
    return Boolean(element && document.activeElement === element && element.value === ${JSON.stringify(value)});
  })()`, deadline), { deadline, intervalMs: 100, label: "real chat composer text input" });
}

async function attachFixtureThroughVisibleChooser(client, fixturePath, deadline) {
  const chooser = deferred();
  const unsubscribe = client.on("Page.fileChooserOpened", (event) => chooser.resolve(event));
  await client.send("Page.setInterceptFileChooserDialog", { enabled: true });
  try {
    const target = await visibleElementCenter(client, "#btnAttach", deadline, "attachment action");
    if (target.width < 34 || target.height < 34) throw new Error("visible attachment action is too small");
    await dispatchPointerClick(client, target);
    const opened = await beforeDeadline(chooser.promise, deadline, "visible attachment file chooser");
    if (!Number.isSafeInteger(opened?.backendNodeId) || opened.backendNodeId <= 0) {
      throw new Error("visible attachment action did not open the real file chooser");
    }
    await client.send("DOM.setFileInputFiles", { files: [fixturePath], backendNodeId: opened.backendNodeId });
  } finally {
    unsubscribe();
    await client.send("Page.setInterceptFileChooserDialog", { enabled: false }).catch(() => {});
  }
}

async function sha256File(filePath) {
  const digest = crypto.createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(filePath);
    stream.on("data", (chunk) => digest.update(chunk));
    stream.once("error", reject);
    stream.once("end", resolve);
  });
  return digest.digest("hex");
}

function assertRegular(pathname, label, executable = false) {
  const stat = fs.lstatSync(pathname);
  if (stat.isSymbolicLink() || !stat.isFile() || stat.nlink !== 1) throw new Error(`${label} must be a single-link regular file`);
  if (fs.realpathSync(pathname) !== pathname) throw new Error(`${label} must resolve to its fixed installed path`);
  if (executable && (stat.mode & 0o111) === 0) throw new Error(`${label} is not executable`);
}

function prepareOutputDirectory(outputDir) {
  fs.mkdirSync(outputDir, { recursive: true, mode: 0o700 });
  const stat = fs.lstatSync(outputDir);
  if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error("output directory must be a real directory");
  if (fs.readdirSync(outputDir).length !== 0) throw new Error("output directory must be empty");
  fs.chmodSync(outputDir, 0o700);
}

function assertTargetRuntime(args) {
  if (process.platform !== "linux" || process.arch !== "x64") throw new Error("target acceptance requires Linux x86_64");
  if (typeof process.getuid === "function" && process.getuid() === 0) throw new Error("target acceptance must run as a normal desktop user, not root");
  if (!process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) throw new Error("target acceptance requires a graphical desktop session");
  if (typeof fetch !== "function" || typeof WebSocket !== "function") throw new Error("target acceptance requires the bundled Node 22 fetch/WebSocket runtime");
  assertRegular(args.electron, "installed Electron", true);
  const appStat = fs.lstatSync(args.appDir);
  if (appStat.isSymbolicLink() || !appStat.isDirectory() || fs.realpathSync(args.appDir) !== args.appDir) {
    throw new Error("installed App directory must be the real fixed /opt directory");
  }
  assertRegular(path.join(args.appDir, "src", "main.js"), "installed Electron main.js");
  assertRegular(DESKTOP_ENTRY, "installed desktop entry");
  prepareOutputDirectory(args.outputDir);
}

function processAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (_) {
    return false;
  }
}

function installedProcessArgv(pid) {
  try {
    const raw = fs.readFileSync(`/proc/${pid}/cmdline`, "utf8");
    const argv = raw.split("\0");
    if (argv.at(-1) === "") argv.pop();
    return argv;
  } catch (_) {
    return [];
  }
}

function managedProcessArgvMatches(label, argv) {
  if (!Array.isArray(argv)) return false;
  if (label === "Agent") {
    return argv.length === 6
      && argv[0] === INSTALLED_PYTHON
      && argv[1] === "-m"
      && argv[2] === "taiji_runtime.main"
      && argv[3] === "gateway"
      && argv[4] === "run"
      && argv[5] === "--accept-hooks";
  }
  if (label === "WebUI") {
    return argv.length === 2
      && argv[0] === INSTALLED_PYTHON
      && INSTALLED_WEBUI_ENTRIES.has(argv[1]);
  }
  return false;
}

function readManagedPid(pidFile, label) {
  const pid = parsePid(fs.readFileSync(pidFile, "utf8"));
  if (!pid || !processAlive(pid)) throw new Error(`${label} pid file does not identify a live process`);
  if (!managedProcessArgvMatches(label, installedProcessArgv(pid))) {
    throw new Error(`${label} pid is not running the fixed installed product entrypoint`);
  }
  return pid;
}

async function terminateManagedProcess(pid, label, options = {}) {
  const processAliveFn = options.processAliveFn || processAlive;
  const installedProcessArgvFn = options.installedProcessArgvFn || installedProcessArgv;
  const killFn = options.killFn || ((targetPid, signal) => process.kill(targetPid, signal));
  const sleepFn = options.sleepFn || sleep;
  const graceMs = Number.isSafeInteger(options.graceMs) && options.graceMs > 0 ? options.graceMs : 1500;
  const pollMs = Number.isSafeInteger(options.pollMs) && options.pollMs > 0 ? Math.min(options.pollMs, graceMs) : 100;
  if (!Number.isSafeInteger(pid) || pid <= 1) return false;
  if (!processAliveFn(pid)) return true;
  if (!managedProcessArgvMatches(label, installedProcessArgvFn(pid))) return false;
  try {
    killFn(pid, "SIGTERM");
  } catch (_) {
    return !processAliveFn(pid);
  }
  for (let waited = 0; waited < graceMs && processAliveFn(pid); waited += pollMs) {
    await sleepFn(pollMs);
  }
  if (!processAliveFn(pid)) return true;
  if (!managedProcessArgvMatches(label, installedProcessArgvFn(pid))) return false;
  try {
    killFn(pid, "SIGKILL");
  } catch (_) {
    return !processAliveFn(pid);
  }
  for (let waited = 0; waited < graceMs && processAliveFn(pid); waited += pollMs) {
    await sleepFn(pollMs);
  }
  return !processAliveFn(pid);
}

async function waitForProcessExit(pid, deadline, label) {
  await waitFor(() => !processAlive(pid), { deadline, intervalMs: 200, label: `${label} process exit` });
}

async function portIsClosed(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    const done = (closed) => {
      socket.destroy();
      resolve(closed);
    };
    socket.setTimeout(500, () => done(true));
    socket.once("connect", () => done(false));
    socket.once("error", () => done(true));
  });
}

function atomicWriteJson(filePath, payload) {
  const temporary = `${filePath}.tmp.${process.pid}.${crypto.randomBytes(6).toString("hex")}`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
    fs.renameSync(temporary, filePath);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function beforeDeadline(promise, deadline, label) {
  const timeout = remainingTime(deadline, label);
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out`)), timeout);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function responseBodyToJson(response) {
  const body = response.base64Encoded
    ? Buffer.from(response.body || "", "base64").toString("utf8")
    : String(response.body || "");
  return JSON.parse(body);
}

async function runAcceptance(args) {
  assertTargetRuntime(args);
  const deadline = Date.now() + args.timeoutMs;
  const resultPath = path.join(args.outputDir, RESULT_BASENAME);
  const screenshotPath = path.join(args.outputDir, SCREENSHOT_BASENAME);
  const supportBundlePath = path.join(args.outputDir, SUPPORT_BUNDLE_BASENAME);
  const fixturePath = path.join(args.outputDir, FIXTURE_BASENAME);
  const downloadDir = path.join(args.outputDir, ".downloads");
  const ownedPaths = [resultPath, screenshotPath, supportBundlePath, fixturePath];
  let child = null;
  let client = null;
  let agentPid = null;
  let webPid = null;
  let completed = false;

  try {
    const electronExecutableSha256 = await sha256File(args.electron);
    const desktopEntrySha256 = await sha256File(DESKTOP_ENTRY);
    const probeCode = buildProbeCode(args.challenge, args.sessionId);
    const fixture = [
      "太极 Agent 安装态桌面验收附件。",
      "请只返回下面一行中的唯一验收代码：",
      probeCode,
      "",
    ].join("\n");
    fs.writeFileSync(fixturePath, fixture, { encoding: "utf8", mode: 0o600, flag: "wx" });
    const probeSha256 = crypto.createHash("sha256").update(fixture, "utf8").digest("hex");

    const port = await reserveLoopbackPort();
    const childState = { exited: false, code: null, signal: null, error: null };
    const outputTail = [];
    const rememberOutput = (prefix, chunk) => {
      for (const line of safeErrorText(String(chunk || "")).split(/\r?\n/)) {
        if (line.trim()) outputTail.push(`${prefix} ${line.trim()}`);
      }
      if (outputTail.length > 24) outputTail.splice(0, outputTail.length - 24);
    };
    const env = buildInstalledAcceptanceEnv(process.env);
    child = spawn(args.electron, buildElectronArgs(port), {
      cwd: args.appDir,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: false,
    });
    child.stdout.on("data", (chunk) => rememberOutput("[electron]", chunk));
    child.stderr.on("data", (chunk) => rememberOutput("[electron-error]", chunk));
    const exitPromise = new Promise((resolve) => {
      child.once("error", (error) => {
        childState.error = error;
        resolve({ code: null, signal: null, error });
      });
      child.once("exit", (code, signal) => {
        childState.exited = true;
        childState.code = code;
        childState.signal = signal;
        resolve({ code, signal, error: childState.error });
      });
    });

    const { desktop } = await findDesktopTarget(port, deadline, childState);
    const electronPid = child.pid;
    if (!Number.isSafeInteger(electronPid) || electronPid <= 1) throw new Error("Electron process has no valid pid");
    const procExecutable = fs.readlinkSync(`/proc/${electronPid}/exe`);
    if (procExecutable !== args.electron) throw new Error(`Electron pid executable is not the fixed installed binary: ${procExecutable}`);
    const electronArgv = installedProcessArgv(electronPid);
    if (electronArgv[0] !== args.electron || electronArgv.at(-1) !== args.appDir) {
      throw new Error("Electron process argv is not anchored to the fixed installed App directory");
    }

    const socket = await connectWebSocket(desktop.websocket, deadline);
    client = new CdpClient(socket, Math.min(15000, remainingTime(deadline, "CDP command")));
    const httpFailures = [];
    const jsErrors = [];
    const requests = new Map();
    const responseMetadata = new Map();
    const uploadDeferred = deferred();
    const chatStartDeferred = deferred();
    let turnStarted = false;

    client.on("Network.requestWillBeSent", ({ requestId, request }) => {
      requests.set(requestId, { method: String(request?.method || ""), url: String(request?.url || "") });
    });
    client.on("Network.responseReceived", ({ requestId, response }) => {
      const request = requests.get(requestId) || { method: "", url: String(response?.url || "") };
      const status = Number(response?.status || 0);
      responseMetadata.set(requestId, { ...request, status });
      if (status >= 400) {
        httpFailures.push({ status, method: request.method, url: redactDesktopUrl(request.url) });
      }
    });
    client.on("Network.loadingFailed", ({ requestId, errorText }) => {
      if (!turnStarted) return;
      const request = requests.get(requestId);
      if (!request) return;
      let pathname = "";
      try { pathname = new URL(request.url).pathname; } catch (_) { return; }
      const failed = { ok: false, status: 0, error: String(errorText || "network loading failed") };
      if (request.method === "POST" && pathname === "/api/upload") uploadDeferred.resolve(failed);
      if (request.method === "POST" && pathname === "/api/chat/start") chatStartDeferred.resolve(failed);
    });
    client.on("Network.loadingFinished", ({ requestId }) => {
      if (!turnStarted) return;
      const metadata = responseMetadata.get(requestId);
      if (!metadata || metadata.method !== "POST") return;
      let pathname = "";
      try { pathname = new URL(metadata.url).pathname; } catch (_) { return; }
      let targetDeferred = null;
      if (pathname === "/api/upload") targetDeferred = uploadDeferred;
      if (pathname === "/api/chat/start") targetDeferred = chatStartDeferred;
      if (!targetDeferred) return;
      void client.send("Network.getResponseBody", { requestId })
        .then((body) => targetDeferred.resolve({
          ok: metadata.status >= 200 && metadata.status < 300,
          status: metadata.status,
          payload: responseBodyToJson(body),
        }))
        .catch((error) => targetDeferred.resolve({ ok: false, status: metadata.status, error: safeErrorText(error) }));
    });
    client.on("Runtime.exceptionThrown", ({ exceptionDetails }) => {
      const frame = exceptionDetails?.stackTrace?.callFrames?.[0];
      jsErrors.push({
        source: "runtime",
        text: String(exceptionDetails?.exception?.description || exceptionDetails?.text || "renderer exception"),
        url: redactDesktopUrl(frame?.url || ""),
      });
    });
    client.on("Runtime.consoleAPICalled", ({ type, args: consoleArgs, stackTrace }) => {
      if (type !== "error") return;
      const text = (consoleArgs || []).map((item) => item.value ?? item.description ?? "").join(" ");
      jsErrors.push({
        source: "runtime",
        text: String(text || "console error"),
        url: redactDesktopUrl(stackTrace?.callFrames?.[0]?.url || ""),
      });
    });
    client.on("Log.entryAdded", ({ entry }) => {
      if (entry?.level !== "error") return;
      jsErrors.push({
        source: "log",
        text: String(entry.text || "log error"),
        url: redactDesktopUrl(entry.url || ""),
      });
    });

    await Promise.all([
      client.send("Runtime.enable"),
      client.send("Page.enable"),
      client.send("Network.enable"),
      client.send("Log.enable"),
      client.send("DOM.enable"),
    ]);
    const cookieResult = await client.send("Network.getAllCookies");
    const desktopAuthCookie = validateDesktopAuthCookies(
      cookieResult?.cookies,
      desktop.origin,
    );
    await client.send("Page.reload", { ignoreCache: true });
    await waitFor(async () => evaluate(client, `(() => ({
      ready: document.readyState === "complete" && typeof send === "function" && typeof switchPanel === "function",
      bridge: Boolean(window.taijiDesktop && typeof window.taijiDesktop.pickDirectory === "function" && typeof window.taijiDesktop.readClipboardText === "function"),
      desktop: document.documentElement.dataset.taijiDesktop === "1",
      viewport: [innerWidth, innerHeight]
    }))()`, deadline).then((state) => state?.ready && state?.bridge && state?.desktop && state?.viewport?.[0] >= 800 && state?.viewport?.[1] >= 600 ? state : null), {
      deadline,
      intervalMs: 300,
      label: "installed Electron App readiness with preload bridge",
    });

    const onboardingVisible = await evaluate(client, `(() => {
      const overlay = document.getElementById("onboardingOverlay");
      return Boolean(overlay && getComputedStyle(overlay).display !== "none");
    })()`, deadline);
    if (onboardingVisible) {
      await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await waitFor(() => evaluate(client, `(() => {
        const overlay = document.getElementById("onboardingOverlay");
        return !overlay || getComputedStyle(overlay).display === "none";
      })()`, deadline), { deadline, label: "onboarding dismissal" });
    }

    const stateHome = process.env.XDG_STATE_HOME
      ? path.resolve(process.env.XDG_STATE_HOME)
      : path.join(os.homedir(), ".local", "state");
    const logDir = path.join(stateHome, "taiji-agent", "logs");
    agentPid = await waitFor(() => {
      try { return readManagedPid(path.join(logDir, "agent.pid"), "Agent"); } catch (_) { return null; }
    }, { deadline, intervalMs: 250, label: "installed Agent pid" });
    webPid = await waitFor(() => {
      try { return readManagedPid(path.join(logDir, "web.pid"), "WebUI"); } catch (_) { return null; }
    }, { deadline, intervalMs: 250, label: "installed WebUI pid" });

    const chatOpened = await evaluate(client, `(() => {
      const oldSessionId = String(S.session && S.session.session_id || "");
      const hadMessages = Boolean(Array.isArray(S.messages) && S.messages.some((message) => message && message.role));
      return { ok: true, oldSessionId, hadMessages };
    })()`, deadline);
    if (!chatOpened?.ok) throw new Error("Chat state is unavailable");
    await physicalClickVisibleElement(client, '.taiji-nav-item[data-taiji-panel="chat"]', deadline, "Chat navigation entry");
    await physicalClickVisibleElement(client, ".taiji-new-chat", deadline, "New Chat action");
    await waitFor(() => evaluate(client, `(() => {
      const panel = document.getElementById("mainChat");
      const composer = document.getElementById("msg");
      const sessionId = String(S.session && S.session.session_id || "");
      const sessionReady = Boolean(sessionId) && (!${chatOpened.hadMessages ? "true" : "false"} || sessionId !== ${JSON.stringify(chatOpened.oldSessionId)});
      return Boolean(panel && composer && getComputedStyle(panel).display !== "none" && !S.busy && sessionReady);
    })()`, deadline), { deadline, label: "visible Chat workspace" });

    await attachFixtureThroughVisibleChooser(client, fixturePath, deadline);
    await waitFor(() => evaluate(client, `(() => ({
      pending: Array.isArray(S.pendingFiles) && S.pendingFiles.some((file) => file && file.name === ${JSON.stringify(FIXTURE_BASENAME)}),
      tray: Boolean(Array.from(document.querySelectorAll("#attachTray .attach-chip")).find((node) => node.textContent.includes(${JSON.stringify(FIXTURE_BASENAME)})))
    }))()`, deadline).then((state) => state?.pending && state?.tray), { deadline, label: "attachment selection in the visible composer" });

    const promptJson = JSON.stringify(PROBE_PROMPT);
    await insertTextThroughVisibleComposer(client, "#msg", PROBE_PROMPT, deadline);
    await waitFor(() => evaluate(client, `(() => {
      const button = document.getElementById("btnSend");
      return Boolean(button && !button.disabled && button.dataset.action === "send" && button.getAttribute("aria-label"));
    })()`, deadline), { deadline, label: "visible Send action" });

    turnStarted = true;
    await physicalClickVisibleElement(client, "#btnSend", deadline, "Send action");

    const uploadResult = await beforeDeadline(uploadDeferred.promise, deadline, "attachment upload response");
    if (!uploadResult.ok || uploadResult.status !== 200 || uploadResult.payload?.filename !== FIXTURE_BASENAME || !uploadResult.payload?.path) {
      throw new Error(`attachment upload did not return the expected persisted file: ${JSON.stringify({ status: uploadResult.status, filename: uploadResult.payload?.filename })}`);
    }
    const chatStart = await beforeDeadline(chatStartDeferred.promise, deadline, "real model chat start response");
    if (!chatStart.ok || chatStart.status !== 200 || !String(chatStart.payload?.stream_id || "").trim() || chatStart.payload?.license_blocked === true) {
      throw new Error(`real model chat did not start successfully: ${JSON.stringify({ status: chatStart.status, code: chatStart.payload?.code || "" })}`);
    }

    const appSessionId = await waitFor(() => evaluate(client, `(() => {
      if (!S.session || !S.session.session_id || !Array.isArray(S.messages)) return "";
      const user = [...S.messages].reverse().find((message) => message && message.role === "user" && String(message.content || "").trim() === ${promptJson});
      return user ? String(S.session.session_id) : "";
    })()`, deadline), { deadline, intervalMs: 300, label: "chat session created by the visible send action" });

    const expectedCompletion = { sessionId: appSessionId, attachmentName: FIXTURE_BASENAME, probeCode };
    const completion = await waitFor(() => evaluate(client, `(async () => {
      const sid = ${JSON.stringify(appSessionId)};
      const prompt = ${promptJson};
      const localMessages = Array.isArray(S.messages) ? S.messages : [];
      const localUser = [...localMessages].reverse().find((message) => message && message.role === "user" && String(message.content || "").trim() === prompt) || {};
      const localAssistant = [...localMessages].reverse().find((message) => message && message.role === "assistant") || {};
      let persisted = null;
      try {
        const response = await fetch("/api/session?session_id=" + encodeURIComponent(sid), { credentials: "include" });
        if (response.ok) persisted = (await response.json()).session || null;
      } catch (_) {}
      const persistedMessages = Array.isArray(persisted && persisted.messages) ? persisted.messages : [];
      const persistedUser = [...persistedMessages].reverse().find((message) => message && message.role === "user" && String(message.content || "").trim() === prompt) || {};
      const persistedAssistant = [...persistedMessages].reverse().find((message) => message && message.role === "assistant") || {};
      return {
        sessionId: String(S.session && S.session.session_id || ""),
        busy: Boolean(S.busy),
        activeStreamId: S.activeStreamId || (S.session && S.session.active_stream_id) || null,
        pendingUserMessage: S.session && S.session.pending_user_message || null,
        persistedPendingUserMessage: persisted && persisted.pending_user_message || null,
        userAttachments: localUser.attachments || [],
        persistedUserAttachments: persistedUser.attachments || [],
        assistantContent: localAssistant.content || "",
        persistedAssistantContent: persistedAssistant.content || "",
        assistantError: Boolean(localAssistant._error || localAssistant.error),
        assistantLicenseBlocked: Boolean(localAssistant.license_blocked),
        model: String(persisted && persisted.model || S.session && S.session.model || "")
      };
    })()`, deadline).then((snapshot) => completionSnapshotPassed(snapshot, expectedCompletion) ? snapshot : null), {
      deadline,
      intervalMs: 500,
      label: "settled persisted exact model response from the attachment",
    });
    const model = String(chatStart.payload?.effective_model || completion.model || "").trim();
    if (!model) throw new Error("real model response has no model identity");

    await evaluate(client, `(async () => {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (typeof scrollToBottom === "function") scrollToBottom();
      await new Promise((resolve) => setTimeout(resolve, 250));
      return { width: innerWidth, height: innerHeight };
    })()`, deadline);
    const screenshot = await client.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    const screenshotBytes = Buffer.from(String(screenshot.data || ""), "base64");
    if (screenshotBytes.length < 1024 || !screenshotBytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))) {
      throw new Error("Electron chat screenshot is not a complete PNG");
    }
    fs.writeFileSync(screenshotPath, screenshotBytes, { mode: 0o600, flag: "wx" });

    fs.mkdirSync(downloadDir, { mode: 0o700 });
    await client.send("Browser.setDownloadBehavior", {
      behavior: "allowAndName",
      downloadPath: downloadDir,
      eventsEnabled: true,
    });
    const downloadBegan = deferred();
    const downloadCompleted = deferred();
    client.on("Browser.downloadWillBegin", (event) => downloadBegan.resolve(event));
    client.on("Browser.downloadProgress", (event) => {
      if (["completed", "canceled"].includes(event.state)) downloadCompleted.resolve(event);
    });

    await physicalClickVisibleElement(client, '.taiji-nav-item[data-taiji-panel="settings"]', deadline, "Settings navigation entry");
    await physicalClickVisibleElement(client, '#settingsMenu [data-settings-section="system"]', deadline, "System settings entry");
    await waitFor(() => evaluate(client, `(() => {
      const card = document.getElementById("productDiagnosticsCard");
      const status = document.getElementById("productDiagnosticsStatus");
      return Boolean(card && getComputedStyle(card).display !== "none" && status && status.dataset.status !== "loading" && document.querySelectorAll("#productDiagnosticsComponents .product-diagnostics-component").length === 7);
    })()`, deadline), { deadline, intervalMs: 350, label: "live product diagnostics in the visible App" });
    await physicalClickVisibleElement(client, "#btnExportProductDiagnostics", deadline, "support-bundle export action");
    await waitFor(() => evaluate(client, `(() => {
      const overlay = document.getElementById("appDialogOverlay");
      const confirm = document.getElementById("appDialogConfirm");
      return Boolean(overlay && confirm && getComputedStyle(overlay).display !== "none" && !confirm.disabled);
    })()`, deadline), { deadline, label: "support-bundle export confirmation" });
    await physicalClickVisibleElement(client, "#appDialogConfirm", deadline, "support-bundle export confirmation");

    const download = await beforeDeadline(downloadBegan.promise, deadline, "support-bundle download start");
    if (!/^taiji-support-bundle-\d{4}-\d{2}-\d{2}\.json$/.test(String(download.suggestedFilename || ""))) {
      throw new Error(`unexpected support-bundle filename: ${download.suggestedFilename || ""}`);
    }
    const progress = await beforeDeadline(downloadCompleted.promise, deadline, "support-bundle download completion");
    if (progress.guid !== download.guid || progress.state !== "completed") throw new Error("support-bundle download did not complete");
    const downloadedPath = path.join(downloadDir, download.guid);
    await waitFor(() => fs.existsSync(downloadedPath) && fs.statSync(downloadedPath).size > 0, { deadline, label: "downloaded support-bundle file" });
    fs.renameSync(downloadedPath, supportBundlePath);
    fs.rmSync(downloadDir, { recursive: true, force: true });
    if (fs.statSync(supportBundlePath).size >= 64 * 1024) throw new Error("App exported an oversized support bundle");
    const bundle = JSON.parse(fs.readFileSync(supportBundlePath, "utf8"));
    if (!supportBundleIsSafe(bundle)) throw new Error("App exported an unsafe or inconsistent support bundle");

    const expectedHttpCount = httpFailures.filter((entry) => isExpectedDesktopHttpFailure(entry, desktop.origin)).length;
    if (expectedHttpCount > 1) throw new Error("App repeatedly requested a missing expert-team run");
    const unexpectedHttpFailures = filterUnexpectedHttpFailures(httpFailures, desktop.origin);
    const unexpectedJsErrors = filterUnexpectedJsErrors(jsErrors, desktop.origin);
    if (unexpectedHttpFailures.length) throw new Error(`unexpected App HTTP failures: ${JSON.stringify(unexpectedHttpFailures)}`);
    if (unexpectedJsErrors.length) throw new Error(`unexpected App JavaScript errors: ${JSON.stringify(unexpectedJsErrors)}`);

    const webuiPort = Number(new URL(desktop.url).port);
    let pageCloseError = null;
    try {
      await client.send("Page.close");
    } catch (error) {
      pageCloseError = error;
    }
    const exit = await beforeDeadline(exitPromise, deadline, "Electron exit after BrowserWindow close");
    if (exit.error || exit.code !== 0 || exit.signal) {
      const tail = outputTail.length ? `\n${outputTail.join("\n")}` : "";
      throw new Error(`Electron did not exit normally after Page.close (${exit.code ?? exit.signal})${tail}`);
    }
    if (pageCloseError && processAlive(electronPid)) throw pageCloseError;
    await Promise.all([
      waitForProcessExit(agentPid, deadline, "Agent"),
      waitForProcessExit(webPid, deadline, "WebUI"),
      waitFor(() => portIsClosed(webuiPort), { deadline, intervalMs: 250, label: "WebUI port closure" }),
    ]);
    if (fs.existsSync(path.join(logDir, "agent.pid")) || fs.existsSync(path.join(logDir, "web.pid"))) {
      throw new Error("managed runtime pid files remained after closing the Electron window");
    }

    const result = buildDriverResult({
      sessionId: args.sessionId,
      challenge: args.challenge,
      electronPid,
      electronExecutableSha256,
      desktopEntrySha256,
      appUrl: desktop.url,
      webuiOrigin: desktop.origin,
      desktopAuthCookie,
      model,
      probeSha256,
      agentPid,
      webPid,
      exitCode: exit.code,
      jsErrors: unexpectedJsErrors,
      unexpectedHttpFailures,
      checks: {
        desktop_launch: true,
        real_model_conversation: true,
        attachment_flow: true,
        window_close_exit: true,
        diagnostic_export: true,
      },
    });
    fs.rmSync(fixturePath, { force: true });
    atomicWriteJson(resultPath, result);
    completed = true;
    return { resultPath, result };
  } finally {
    client?.close();
    if (!completed) {
      fs.rmSync(resultPath, { force: true });
      fs.rmSync(screenshotPath, { force: true });
      fs.rmSync(supportBundlePath, { force: true });
      fs.rmSync(fixturePath, { force: true });
      fs.rmSync(downloadDir, { recursive: true, force: true });
      if (child && processAlive(child.pid)) {
        child.kill("SIGTERM");
        await sleep(1000);
        if (processAlive(child.pid)) child.kill("SIGKILL");
      }
      for (const [pid, label] of [[agentPid, "Agent"], [webPid, "WebUI"]]) {
        if (!pid) continue;
        const stopped = await terminateManagedProcess(pid, label);
        if (!stopped && processAlive(pid)) {
          process.stderr.write(`taiji-desktop-acceptance-cleanup-warning\t${label} process ${pid} could not be safely stopped\n`);
        }
      }
    }
  }
}

module.exports = {
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
  insertTextThroughVisibleComposer,
  isExpectedBackgroundConsoleError,
  isExpectedDesktopHttpFailure,
  managedProcessArgvMatches,
  normalizeMessageContent,
  parseArgs,
  parsePid,
  physicalClickVisibleElement,
  redactDesktopUrl,
  supportBundleIsSafe,
  terminateManagedProcess,
  validateDesktopAuthCookies,
  validateDesktopTarget,
};

if (require.main === module) {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`taiji-desktop-acceptance-failed\t${safeErrorText(error).split("\n")[0]}\n`);
    process.exitCode = 1;
  }
  if (args) {
    runAcceptance(args).then(({ resultPath }) => {
      process.stdout.write(`${JSON.stringify({
        status: "taiji-desktop-acceptance-valid",
        driver_result: resultPath,
      })}\n`);
    }).catch((error) => {
      process.stderr.write(`taiji-desktop-acceptance-failed\t${safeErrorText(error)}\n`);
      process.exitCode = 1;
    });
  }
}
