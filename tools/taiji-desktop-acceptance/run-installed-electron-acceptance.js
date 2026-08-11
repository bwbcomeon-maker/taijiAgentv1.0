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
const CORE_JOURNAL_PATH = "/usr/bin/journalctl";
const CORE_JOURNAL_MESSAGE_ID = "fc2e22bc6ee647b6b90729ab34a250b1";
const CORE_PATTERN_PATH = "/proc/sys/kernel/core_pattern";
const SYSTEMD_COREDUMP_PATHS = new Set([
  "/lib/systemd/systemd-coredump",
  "/usr/lib/systemd/systemd-coredump",
]);
const RESTART_ROUND_COUNT = 3;
const PROFILE_CONTINUITY_COOKIE = "taiji_acceptance_profile_continuity";
const FIXED_EXEC_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
const ELECTRON_DIST_DIR = path.dirname(ELECTRON_PATH);
const GRAPHICAL_SESSION_ENV_KEYS = new Set([
  "HOME",
  "USER",
  "LOGNAME",
  "DISPLAY",
  "WAYLAND_DISPLAY",
  "XAUTHORITY",
  "DBUS_SESSION_BUS_ADDRESS",
  "XDG_RUNTIME_DIR",
  "XDG_CURRENT_DESKTOP",
  "XDG_SESSION_TYPE",
  "XDG_SESSION_DESKTOP",
  "DESKTOP_SESSION",
  "XDG_CONFIG_HOME",
  "XDG_DATA_HOME",
  "XDG_CACHE_HOME",
  "XDG_STATE_HOME",
  "LANG",
  "LANGUAGE",
]);

function parseArgs(argv) {
  const allowed = new Set(["--electron", "--app-dir", "--output-dir", "--session-id", "--challenge", "--timeout-ms", "--matrix", "--category-id"]);
  const required = new Set(["--electron", "--app-dir", "--output-dir", "--session-id", "--challenge", "--timeout-ms"]);
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!allowed.has(key)) throw new Error(`unknown argument: ${key || "<empty>"}`);
    if (values.has(key)) throw new Error(`duplicate argument: ${key}`);
    if (typeof value !== "string" || !value) throw new Error(`missing value for ${key}`);
    values.set(key, value);
  }
  for (const key of required) {
    if (!values.has(key)) throw new Error(`missing required argument: ${key}`);
  }
  if (values.has("--matrix") !== values.has("--category-id")) {
    throw new Error("--matrix and --category-id must be supplied together");
  }

  const electron = values.get("--electron");
  const appDir = values.get("--app-dir");
  const outputDir = values.get("--output-dir");
  const sessionId = values.get("--session-id");
  const challenge = values.get("--challenge");
  const timeoutMs = Number(values.get("--timeout-ms"));
  const matrix = values.get("--matrix") || null;
  const categoryId = values.get("--category-id") || null;
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
  if (matrix !== null && (!path.isAbsolute(matrix) || path.resolve(matrix) !== matrix)) {
    throw new Error("--matrix must be a normalized absolute path");
  }
  if (categoryId !== null && !/^[a-z0-9][a-z0-9-]{2,63}$/.test(categoryId)) {
    throw new Error("--category-id has an invalid format");
  }
  return { electron, appDir, outputDir, sessionId, challenge, timeoutMs, matrix, categoryId };
}

function buildElectronArgs(port) {
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) throw new Error("invalid CDP port");
  return [
    "--remote-debugging-address=127.0.0.1",
    `--remote-debugging-port=${port}`,
    APP_DIR,
  ];
}

function buildSecondaryElectronArgs() {
  return [APP_DIR];
}

function buildInstalledAcceptanceEnv(sourceEnv = {}, identity = {}) {
  const userInfo = os.userInfo();
  const uid = identity.uid ?? userInfo.uid;
  const username = identity.username ?? userInfo.username;
  const homedir = identity.homedir ?? userInfo.homedir;
  if (!Number.isSafeInteger(uid) || uid <= 0 || !username || !path.isAbsolute(homedir) || path.resolve(homedir) !== homedir) {
    throw new Error("installed acceptance user identity is invalid");
  }
  if (sourceEnv.HOME && sourceEnv.HOME !== homedir) throw new Error("HOME does not match the current user identity");
  for (const key of ["USER", "LOGNAME"]) {
    if (sourceEnv[key] && sourceEnv[key] !== username) throw new Error(`${key} does not match the current user identity`);
  }
  const env = {};
  for (const [key, value] of Object.entries(sourceEnv)) {
    if (["HOME", "USER", "LOGNAME"].includes(key)) continue;
    if ((GRAPHICAL_SESSION_ENV_KEYS.has(key) || key.startsWith("LC_")) && typeof value === "string") {
      env[key] = value;
    }
  }
  env.HOME = homedir;
  env.USER = username;
  env.LOGNAME = username;
  for (const key of ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"]) {
    if (!env[key]) continue;
    const value = env[key];
    if (!path.isAbsolute(value) || path.resolve(value) !== value || (value !== homedir && !value.startsWith(`${homedir}${path.sep}`))) {
      throw new Error(`${key} must be a normalized absolute path inside the current user home`);
    }
  }
  if (env.XDG_RUNTIME_DIR && env.XDG_RUNTIME_DIR !== `/run/user/${uid}`) {
    throw new Error("XDG_RUNTIME_DIR does not match the current user identity");
  }
  env.PATH = FIXED_EXEC_PATH;
  env.TAIJI_AGENT_ROOT = "/opt/taiji-agent";
  env.TAIJI_AGENT_USE_USER_DIRS = "1";
  return env;
}

function assertCanonicalUserHome(identity, options = {}) {
  const uid = identity?.uid;
  const homedir = String(identity?.homedir || "");
  const lstatFn = options.lstatFn || fs.lstatSync;
  const realpathFn = options.realpathFn || fs.realpathSync;
  if (!Number.isSafeInteger(uid) || uid <= 0 || !path.isAbsolute(homedir) || path.resolve(homedir) !== homedir) {
    const error = new Error("current user home identity is invalid");
    error.code = "TAIJI-DESKTOP-E012";
    throw error;
  }
  let stat;
  let realpath;
  try {
    stat = lstatFn(homedir);
    realpath = realpathFn(homedir);
  } catch (_) {
    const error = new Error("current user home could not be verified");
    error.code = "TAIJI-DESKTOP-E012";
    throw error;
  }
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    const error = new Error("current user home must be a real directory");
    error.code = "TAIJI-DESKTOP-E012";
    throw error;
  }
  if (stat.uid !== uid) {
    const error = new Error("current user home is not owned by the current uid");
    error.code = "TAIJI-DESKTOP-E012";
    throw error;
  }
  if (realpath !== homedir) {
    const error = new Error("current user home is not its canonical path");
    error.code = "TAIJI-DESKTOP-E012";
    throw error;
  }
}

function buildProbeCode(challenge, sessionId) {
  if (!CHALLENGE_RE.test(challenge)) throw new Error("invalid challenge");
  if (!SESSION_RE.test(sessionId)) throw new Error("invalid session id");
  const digest = crypto.createHash("sha256").update(`${challenge}:${sessionId}`, "utf8").digest("hex");
  return `TAIJI-ATTACHMENT-PROBE-${digest.slice(0, 32)}`;
}

function assertVisibleFirstConfigurationStart(state) {
  if (!state || state.visible !== true || state.active !== true || state.completed !== false) {
    throw new Error("installed acceptance must start with the visible onboarding workflow");
  }
}

function firstConfigurationCompletionObserved(state) {
  return Boolean(
    state
    && state.visible === false
    && state.active === false
    && state.completed === true
    && state.preflightReady === true
  );
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

function processIdentityFromStat(pid, raw) {
  if (!Number.isSafeInteger(pid) || pid <= 1) throw new Error("process identity requires a valid pid");
  const rendered = String(raw || "");
  const prefix = `${pid} (`;
  const close = rendered.lastIndexOf(")");
  if (!rendered.startsWith(prefix) || close <= prefix.length) throw new Error("process stat has an invalid identity prefix");
  const fields = rendered.slice(close + 1).trim().split(/\s+/);
  const startTime = fields[19];
  if (!/^[0-9]+$/.test(startTime || "")) throw new Error("process stat has no start time");
  return { pid, start_time_ticks: startTime };
}

function captureProcessIdentity(pid) {
  return processIdentityFromStat(pid, fs.readFileSync(`/proc/${pid}/stat`, "utf8"));
}

function inspectProcessIdentity(identity, readStatFn = (pid) => fs.readFileSync(`/proc/${pid}/stat`, "utf8")) {
  if (!identity || !Number.isSafeInteger(identity.pid) || identity.pid <= 1 || !/^[0-9]+$/.test(identity.start_time_ticks || "")) {
    return { status: "unverified", reason: "identity_invalid" };
  }
  try {
    const current = processIdentityFromStat(identity.pid, readStatFn(identity.pid));
    return current.start_time_ticks === identity.start_time_ticks
      ? { status: "present" }
      : { status: "gone", reason: "pid_reused" };
  } catch (error) {
    if (["ENOENT", "ESRCH"].includes(String(error?.code || ""))) return { status: "gone", reason: "proc_absent" };
    if (error instanceof Error && /process stat/.test(error.message)) return { status: "unverified", reason: "proc_malformed" };
    return { status: "unverified", reason: "proc_unreadable" };
  }
}

function processIdentityStillPresent(identity, readStatFn = (pid) => fs.readFileSync(`/proc/${pid}/stat`, "utf8")) {
  const observation = inspectProcessIdentity(identity, readStatFn);
  if (observation.status === "unverified") {
    const error = new Error("process identity could not be verified");
    error.code = "TAIJI-DESKTOP-E031";
    throw error;
  }
  return observation.status === "present";
}

function captureElectronHelperIdentities(rootIdentity, options = {}) {
  if (!rootIdentity || !Number.isSafeInteger(rootIdentity.pid) || rootIdentity.pid <= 1) {
    throw new Error("Electron helper capture requires a valid root identity");
  }
  const readChildrenFn = options.readChildrenFn || ((pid) => fs.readFileSync(`/proc/${pid}/task/${pid}/children`, "utf8"));
  const captureIdentityFn = options.captureIdentityFn || captureProcessIdentity;
  const readlinkFn = options.readlinkFn || ((pid) => fs.readlinkSync(`/proc/${pid}/exe`));
  const queue = [rootIdentity];
  const visited = new Set([rootIdentity.pid]);
  const helpers = [];
  while (queue.length) {
    const parentIdentity = queue.shift();
    const parentPid = parentIdentity.pid;
    if (parentPid !== rootIdentity.pid) {
      try {
        const current = captureIdentityFn(parentPid);
        if (!current || current.start_time_ticks !== parentIdentity.start_time_ticks) continue;
      } catch (error) {
        if (["ENOENT", "ESRCH"].includes(String(error?.code || ""))) continue;
        throw error;
      }
    }
    let rendered;
    try {
      rendered = String(readChildrenFn(parentPid) || "").trim();
    } catch (error) {
      if (["ENOENT", "ESRCH"].includes(String(error?.code || "")) && parentPid !== rootIdentity.pid) continue;
      throw error;
    }
    if (rendered && !/^[0-9]+(?:\s+[0-9]+)*$/.test(rendered)) throw new Error("Electron child process list is malformed");
    for (const token of rendered ? rendered.split(/\s+/) : []) {
      const pid = Number(token);
      if (!Number.isSafeInteger(pid) || pid <= 1 || visited.has(pid)) continue;
      visited.add(pid);
      try {
        const identity = captureIdentityFn(pid);
        if (!identity || identity.pid !== pid || !/^[0-9]+$/.test(identity.start_time_ticks || "")) {
          throw new Error("Electron child process identity is invalid");
        }
        const executable = readlinkFn(pid);
        const confirmedIdentity = captureIdentityFn(pid);
        if (!confirmedIdentity || confirmedIdentity.start_time_ticks !== identity.start_time_ticks) continue;
        queue.push(confirmedIdentity);
        if (executable === ELECTRON_PATH || executable.startsWith(`${ELECTRON_DIST_DIR}${path.sep}`)) helpers.push(confirmedIdentity);
      } catch (error) {
        if (["ENOENT", "ESRCH"].includes(String(error?.code || ""))) continue;
        throw error;
      }
    }
  }
  return helpers.sort((left, right) => left.pid - right.pid);
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

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map((item) => stableJson(item)).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function requireObservationSalt(salt) {
  if (!Buffer.isBuffer(salt) || salt.length < 16) throw new Error("observation salt must contain at least 128 bits");
  return salt;
}

function saltedToken(value, salt) {
  return crypto.createHmac("sha256", requireObservationSalt(salt)).update(stableJson(value), "utf8").digest("hex");
}

function publicModelConfigProjection(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload) || payload.ok !== true) {
    throw new Error("model configuration GET did not return a public success payload");
  }
  const main = payload.main;
  const keyStatus = main && main.key_status;
  if (!main || typeof main !== "object" || Array.isArray(main) || !keyStatus || typeof keyStatus !== "object") {
    throw new Error("model configuration GET has no public main projection");
  }
  const profile = String(payload.profile || "");
  const requestId = String(payload.main_request_id || "");
  if (!/^[0-9a-f]{32}$/.test(requestId)) {
    throw new Error("model configuration GET has no valid request receipt");
  }
  const provider = String(main.provider || "");
  const model = String(main.model || "");
  const explicitKeyEnv = String(main.key_env || "");
  const statusKeyEnv = String(keyStatus.env_var || "");
  const keySource = String(keyStatus.source || "");
  const keyEnv = explicitKeyEnv || statusKeyEnv || (keySource === "oauth" ? "oauth" : "");
  if (!profile || !provider || !model || !keyEnv) {
    throw new Error("model configuration GET has an incomplete active main model");
  }
  if (keyStatus.configured !== true) {
    throw new Error("model configuration GET reports that the active main model is not configured");
  }
  if (!keySource || (explicitKeyEnv && statusKeyEnv && explicitKeyEnv !== statusKeyEnv)) {
    throw new Error("model configuration GET has an incomplete credential projection");
  }
  return {
    profile,
    main_request_id: requestId,
    main: {
      provider,
      model,
      base_url: String(main.base_url || ""),
      key_env: keyEnv,
      key_configured: true,
      key_source: keySource,
      key_env_status: statusKeyEnv,
    },
  };
}

function buildModelConfigObservation(payloads, salt) {
  if (!Array.isArray(payloads) || payloads.length !== RESTART_ROUND_COUNT) {
    throw new Error("model configuration observation requires exactly 3 GET results");
  }
  const tokens = payloads.map((payload) => saltedToken(publicModelConfigProjection(payload), salt));
  return {
    observed_rounds: RESTART_ROUND_COUNT,
    consistent: tokens.every((token) => token === tokens[0]),
    public_projection_token: tokens[0],
  };
}

function profileCookieHost(appOrigin) {
  let parsed;
  try {
    parsed = new URL(String(appOrigin || ""));
  } catch (_) {
    throw new Error("profile continuity origin is invalid");
  }
  if (parsed.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(parsed.hostname)) {
    throw new Error("profile continuity origin must be loopback HTTP");
  }
  return { origin: parsed.origin, hostname: parsed.hostname };
}

async function createProfileContinuityMarker(client, appOrigin, challenge, sessionId, salt) {
  if (!CHALLENGE_RE.test(challenge || "") || !SESSION_RE.test(sessionId || "")) {
    throw new Error("profile continuity marker requires valid acceptance identities");
  }
  const { origin } = profileCookieHost(appOrigin);
  const value = crypto.randomBytes(32).toString("hex");
  const response = await client.send("Network.setCookie", {
    name: PROFILE_CONTINUITY_COOKIE,
    value,
    url: origin,
    path: "/",
    httpOnly: true,
    sameSite: "Strict",
    secure: false,
    expires: Math.floor(Date.now() / 1000) + 1800,
  });
  if (response?.success !== true) throw new Error("persistent profile marker could not be created");
  return {
    name: PROFILE_CONTINUITY_COOKIE,
    value,
    continuity_token: saltedToken({ challenge, session_id: sessionId, name: PROFILE_CONTINUITY_COOKIE, value }, salt),
  };
}

async function verifyProfileContinuityMarker(client, appOrigin, marker) {
  const { hostname } = profileCookieHost(appOrigin);
  if (
    !marker
    || marker.name !== PROFILE_CONTINUITY_COOKIE
    || !/^[0-9a-f]{64}$/.test(marker.value || "")
    || !/^[0-9a-f]{64}$/.test(marker.continuity_token || "")
  ) {
    throw new Error("persistent profile marker identity is invalid");
  }
  const result = await client.send("Network.getAllCookies");
  const matches = (Array.isArray(result?.cookies) ? result.cookies : []).filter((cookie) => (
    cookie
    && cookie.name === marker.name
    && cookie.value === marker.value
    && String(cookie.domain || "").replace(/^\./, "") === hostname
    && cookie.path === "/"
    && cookie.httpOnly === true
  ));
  return matches.length === 1;
}

async function deleteProfileContinuityMarker(client, appOrigin, marker) {
  const { origin } = profileCookieHost(appOrigin);
  if (!marker || marker.name !== PROFILE_CONTINUITY_COOKIE) return;
  await client.send("Network.deleteCookies", { name: marker.name, url: origin });
}

function buildCoreJournalArgs(uid) {
  if (!Number.isSafeInteger(uid) || uid <= 0) throw new Error("core journal query requires a non-root uid");
  return [
    "--system",
    "--no-pager",
    "--output=json",
    `MESSAGE_ID=${CORE_JOURNAL_MESSAGE_ID}`,
    `COREDUMP_UID=${uid}`,
    `COREDUMP_EXE=${ELECTRON_PATH}`,
  ];
}

function coreHandlerIsTrusted(options = {}) {
  const readFileFn = options.readFileFn || fs.readFileSync;
  const realpathFn = options.realpathFn || fs.realpathSync;
  const lstatFn = options.lstatFn || fs.lstatSync;
  try {
    const pattern = String(readFileFn(CORE_PATTERN_PATH, "utf8") || "").trim();
    const match = /^\|(\S+)(?:\s|$)/.exec(pattern);
    if (!match || !SYSTEMD_COREDUMP_PATHS.has(match[1])) return false;
    const real = realpathFn(match[1]);
    if (!SYSTEMD_COREDUMP_PATHS.has(real)) return false;
    const stat = lstatFn(real);
    return stat.isFile()
      && !stat.isSymbolicLink()
      && stat.uid === 0
      && stat.gid === 0
      && stat.nlink === 1
      && (stat.mode & 0o111) !== 0
      && (stat.mode & 0o022) === 0;
  } catch (_) {
    return false;
  }
}

function coreJournalToolIsTrusted(options = {}) {
  const lstatFn = options.lstatFn || fs.lstatSync;
  const realpathFn = options.realpathFn || fs.realpathSync;
  const nodes = [
    { pathname: "/usr", type: "directory" },
    { pathname: "/usr/bin", type: "directory" },
    { pathname: CORE_JOURNAL_PATH, type: "file" },
  ];
  try {
    for (const node of nodes) {
      const stat = lstatFn(node.pathname);
      if (stat.isSymbolicLink() || stat.uid !== 0 || stat.gid !== 0 || (stat.mode & 0o022) !== 0) return false;
      if (node.type === "directory" && !stat.isDirectory()) return false;
      if (node.type === "file" && (!stat.isFile() || stat.nlink !== 1 || (stat.mode & 0o111) === 0)) return false;
      if (realpathFn(node.pathname) !== node.pathname) return false;
    }
    return true;
  } catch (_) {
    return false;
  }
}

function parseCoreJournalJsonCursors(raw, uid) {
  if (!Number.isSafeInteger(uid) || uid <= 0) throw new Error("core journal parser requires a non-root UID");
  const rendered = String(raw || "").trim();
  if (!rendered) return [];
  const cursors = [];
  for (const line of rendered.split(/\r?\n/)) {
    let record;
    try {
      record = JSON.parse(line);
    } catch (_) {
      throw new Error("core journal JSON could not be parsed");
    }
    if (!record || typeof record !== "object" || Array.isArray(record)) {
      throw new Error("core journal JSON row is not an object");
    }
    const required = [
      "MESSAGE_ID",
      "__CURSOR",
      "__REALTIME_TIMESTAMP",
      "COREDUMP_PID",
      "COREDUMP_UID",
      "COREDUMP_EXE",
      "COREDUMP_SIGNAL",
      "COREDUMP_TIMESTAMP",
    ];
    if (required.some((key) => typeof record[key] !== "string" || !record[key])) {
      throw new Error("core journal JSON row is missing required fields");
    }
    if (record.MESSAGE_ID !== CORE_JOURNAL_MESSAGE_ID) throw new Error("core journal message identity is not exact");
    if (record.COREDUMP_UID !== String(uid)) throw new Error("core journal UID is not exact");
    if (record.COREDUMP_EXE !== ELECTRON_PATH) throw new Error("core journal executable is not exact");
    if (
      !/^[0-9]+$/.test(record.__REALTIME_TIMESTAMP)
      || !/^[0-9]+$/.test(record.COREDUMP_PID)
      || !/^[0-9]+$/.test(record.COREDUMP_SIGNAL)
      || !/^[0-9]+$/.test(record.COREDUMP_TIMESTAMP)
      || record.__CURSOR.length > 4096
    ) {
      throw new Error("core journal JSON row has invalid required fields");
    }
    cursors.push(record.__CURSOR);
  }
  if (new Set(cursors).size !== cursors.length) throw new Error("core journal JSON contains duplicate cursors");
  return cursors;
}

function queryCoreJournalSnapshot(options = {}) {
  const uid = options.uid ?? (typeof process.getuid === "function" ? process.getuid() : null);
  if (!Number.isSafeInteger(uid) || uid <= 0) {
    return Promise.resolve({ status: "unverified", reason: "uid_unavailable" });
  }
  const trustFn = options.trustFn || coreJournalToolIsTrusted;
  if (!trustFn()) return Promise.resolve({ status: "unverified", reason: "tool_untrusted" });
  const handlerTrustFn = options.handlerTrustFn || coreHandlerIsTrusted;
  if (!handlerTrustFn()) return Promise.resolve({ status: "unverified", reason: "handler_unverified" });
  const spawnFn = options.spawnFn || spawn;
  return new Promise((resolve) => {
    let child;
    try {
      child = spawnFn(CORE_JOURNAL_PATH, buildCoreJournalArgs(uid), {
        env: { PATH: "/usr/sbin:/usr/bin:/sbin:/bin", LC_ALL: "C", LANG: "C" },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: false,
      });
    } catch (_) {
      resolve({ status: "unverified", reason: "query_failed" });
      return;
    }
    let stdout = "";
    let stderr = "";
    let oversized = false;
    let spawnFailed = false;
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch (_) {}
    }, 15000);
    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk) => {
      if (oversized) return;
      stdout += chunk;
      if (Buffer.byteLength(stdout, "utf8") > 2 * 1024 * 1024) {
        oversized = true;
        stdout = "";
        try { child.kill("SIGKILL"); } catch (_) {}
      }
    });
    child.stderr?.on("data", (chunk) => {
      if (oversized) return;
      stderr += chunk;
      if (Buffer.byteLength(stderr, "utf8") > 64 * 1024) {
        oversized = true;
        stderr = "";
        try { child.kill("SIGKILL"); } catch (_) {}
      }
    });
    child.once("error", () => { spawnFailed = true; });
    child.once("close", (code, signal) => {
      clearTimeout(timer);
      if (spawnFailed || oversized || code !== 0 || signal) {
        resolve({ status: "unverified", reason: oversized ? "output_oversized" : "query_failed" });
        return;
      }
      if (stderr.trim()) {
        resolve({ status: "unverified", reason: "journal_access_unverified" });
        return;
      }
      try {
        resolve({ status: "verified", cursors: new Set(parseCoreJournalJsonCursors(stdout, uid)) });
      } catch (_) {
        resolve({ status: "unverified", reason: "json_unavailable" });
      }
    });
  });
}

async function querySettledCoreJournalSnapshot(options = {}) {
  const sampleCount = options.sampleCount ?? 10;
  const intervalMs = options.intervalMs ?? 1000;
  if (!Number.isSafeInteger(sampleCount) || sampleCount < 3 || sampleCount > 10) {
    throw new Error("core journal settling requires 3-10 samples");
  }
  if (!Number.isSafeInteger(intervalMs) || intervalMs < 1 || intervalMs > 5000) {
    throw new Error("core journal settling interval is invalid");
  }
  const queryFn = options.queryFn || (() => queryCoreJournalSnapshot());
  const sleepFn = options.sleepFn || sleep;
  let previous = null;
  let penultimate = null;
  let latest = null;
  for (let index = 0; index < sampleCount; index += 1) {
    latest = await queryFn();
    if (latest?.status !== "verified" || !(latest.cursors instanceof Set)) return latest;
    if (previous && [...previous].some((cursor) => !latest.cursors.has(cursor))) {
      return { status: "unverified", reason: "cursor_set_regressed" };
    }
    penultimate = previous;
    if (index < sampleCount - 1) await sleepFn(intervalMs);
    previous = new Set(latest.cursors);
  }
  if (!penultimate || penultimate.size !== latest.cursors.size || [...penultimate].some((cursor) => !latest.cursors.has(cursor))) {
    return { status: "unverified", reason: "journal_not_settled" };
  }
  return latest;
}

function buildCoreObservation(snapshots, salt) {
  requireObservationSalt(salt);
  if (!Array.isArray(snapshots) || snapshots.length !== RESTART_ROUND_COUNT + 1) {
    throw new Error("core observation requires one baseline and exactly 3 round snapshots");
  }
  const baseline = snapshots[0];
  if (baseline?.status !== "verified" || !(baseline.cursors instanceof Set)) {
    const reason = String(baseline?.reason || "baseline_unavailable");
    return {
      status: "unverified",
      reason,
      mechanism: "journalctl-json-user-electron",
      baseline_entry_count: null,
      baseline_cursor_set_token: null,
      rounds: [1, 2, 3].map((round) => ({ round, status: "unverified", reason: "baseline_unavailable" })),
    };
  }

  let previous = new Set(baseline.cursors);
  let status = "verified";
  let reason = "";
  let observationGap = false;
  let observedNewCore = false;
  const rounds = [];
  for (let index = 1; index < snapshots.length; index += 1) {
    const snapshot = snapshots[index];
    if (observationGap || snapshot?.status !== "verified" || !(snapshot.cursors instanceof Set)) {
      observationGap = true;
      if (!observedNewCore) status = "unverified";
      reason ||= String(snapshot?.reason || "observation_gap");
      rounds.push({ round: index, status: "unverified", reason: "observation_gap" });
      continue;
    }
    const current = new Set(snapshot.cursors);
    const added = [...current].filter((token) => !previous.has(token)).sort();
    const regressed = [...previous].some((token) => !current.has(token));
    if (added.length) {
      observedNewCore = true;
      status = "failed";
    }
    if (regressed) {
      observationGap = true;
      if (!observedNewCore) status = "unverified";
      reason ||= "cursor_set_regressed";
      rounds.push({
        round: index,
        status: added.length ? "failed" : "unverified",
        reason: "cursor_set_regressed",
      });
      continue;
    }
    rounds.push({
      round: index,
      status: added.length ? "failed" : "verified",
      added_entry_count: added.length,
      cursor_set_token: saltedToken([...current].sort(), salt),
    });
    previous = current;
  }
  const result = {
    status,
    mechanism: "journalctl-json-user-electron",
    baseline_entry_count: baseline.cursors.size,
    baseline_cursor_set_token: saltedToken([...baseline.cursors].sort(), salt),
    rounds,
  };
  if (status === "unverified") result.reason = reason || "observation_gap";
  return result;
}

function buildDriverResult(measurements) {
  const requiredChecks = [
    "visible_first_configuration_completion",
    "desktop_launch",
    "real_model_conversation",
    "attachment_flow",
    "window_close_exit",
    "diagnostic_export",
    "three_restart_cycles",
    "second_instance_focus",
    "model_configuration_state_consistent",
    "no_new_electron_core",
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

  if (!Array.isArray(measurements.restartRounds) || measurements.restartRounds.length !== RESTART_ROUND_COUNT) {
    throw new Error("driver result requires exactly 3 restart rounds");
  }
  const restartRounds = measurements.restartRounds.map((round, index) => {
    const roundNumber = index + 1;
    const processExit = round?.process_identities_gone || {};
    const portsClosed = round?.ports_closed || {};
    const valid = round?.round === roundNumber
      && round.ready === true
      && [round.electron_pid, round.agent_pid, round.web_pid, round.secondary_pid].every((pid) => Number.isSafeInteger(pid) && pid > 1)
      && [round.cdp_port, round.webui_port].every((port) => Number.isSafeInteger(port) && port >= 1024 && port <= 65535)
      && round.second_instance_exit_code === 0
      && round.electron_exit_code === 0
      && round.restored_and_focused === true
      && round.page_close_sent === true
      && [processExit.electron, processExit.agent, processExit.webui, processExit.secondary].every((value) => value === true)
      && portsClosed.cdp === true
      && portsClosed.webui === true
      && round.pidfiles_absent === true
      && round.model_config_observed === true
      && round.profile_continuity_observed === true;
    if (!valid) throw new Error(`restart round ${roundNumber} failed its lifecycle contract`);
    return {
      round: roundNumber,
      ready: true,
      electron_pid: round.electron_pid,
      agent_pid: round.agent_pid,
      web_pid: round.web_pid,
      secondary_pid: round.secondary_pid,
      cdp_port: round.cdp_port,
      webui_port: round.webui_port,
      second_instance_exit_code: 0,
      electron_exit_code: 0,
      restored_and_focused: true,
      page_close_sent: true,
      process_identities_gone: { electron: true, agent: true, webui: true, secondary: true },
      ports_closed: { cdp: true, webui: true },
      pidfiles_absent: true,
      model_config_observed: true,
      profile_continuity_observed: true,
    };
  });
  const roundOne = restartRounds[0];
  if (
    roundOne.electron_pid !== measurements.electronPid
    || roundOne.agent_pid !== measurements.agentPid
    || roundOne.web_pid !== measurements.webPid
    || measurements.exitCode !== roundOne.electron_exit_code
    || Number(new URL(validatedApp.url).port) !== roundOne.webui_port
  ) {
    throw new Error("legacy top-level process fields are not strict round1 aliases");
  }

  const persistent = measurements.persistentUserData;
  if (
    !persistent
    || persistent.mode !== "electron-default-persistent"
    || persistent.restart_rounds !== RESTART_ROUND_COUNT
    || persistent.user_data_override !== false
    || persistent.profile_reset !== false
    || persistent.environment_reused !== true
    || persistent.continuity_observed_rounds !== RESTART_ROUND_COUNT
    || !/^[0-9a-f]{64}$/.test(persistent.continuity_token || "")
  ) {
    throw new Error("persistent Electron user-data contract was not preserved");
  }
  const persistentUserData = {
    mode: "electron-default-persistent",
    restart_rounds: RESTART_ROUND_COUNT,
    user_data_override: false,
    profile_reset: false,
    environment_reused: true,
    continuity_observed_rounds: RESTART_ROUND_COUNT,
    continuity_token: persistent.continuity_token,
  };

  const core = measurements.coreObservation;
  if (core?.status === "failed") throw new Error("new Electron core journal entries appeared during restart acceptance");
  if (core?.status === "unverified") throw new Error("core observation was not verified");
  if (!core || core.status !== "verified" || core.mechanism !== "journalctl-json-user-electron") {
    throw new Error("core observation has an invalid status");
  }
  if (!Number.isSafeInteger(core.baseline_entry_count) || core.baseline_entry_count < 0) {
    throw new Error("core observation has an invalid baseline count");
  }
  if (!/^[0-9a-f]{64}$/.test(core.baseline_cursor_set_token || "")) {
    throw new Error("core observation has an invalid baseline cursor token");
  }
  if (!Array.isArray(core.rounds) || core.rounds.length !== RESTART_ROUND_COUNT) {
    throw new Error("core observation must cover exactly 3 rounds");
  }
  const coreObservation = {
    status: core.status,
    mechanism: "journalctl-json-user-electron",
    baseline_entry_count: core.baseline_entry_count,
    baseline_cursor_set_token: core.baseline_cursor_set_token,
    rounds: core.rounds.map((round, index) => {
      if (round?.round !== index + 1) throw new Error("core observation rounds are out of order");
      if (round.status === "verified") {
        if (
          !Number.isSafeInteger(round.added_entry_count) || round.added_entry_count !== 0
          || !/^[0-9a-f]{64}$/.test(round.cursor_set_token || "")
        ) {
          throw new Error(`core observation round ${index + 1} is invalid`);
        }
        return {
          round: index + 1,
          status: "verified",
          added_entry_count: 0,
          cursor_set_token: round.cursor_set_token,
        };
      }
      throw new Error(`core observation round ${index + 1} is invalid`);
    }),
  };

  const modelConfig = measurements.modelConfigObservation;
  if (
    !modelConfig
    || modelConfig.observed_rounds !== RESTART_ROUND_COUNT
    || modelConfig.consistent !== true
    || !/^[0-9a-f]{64}$/.test(modelConfig.public_projection_token || "")
  ) {
    if (modelConfig?.consistent === false) throw new Error("model configuration projection changed across restart rounds");
    throw new Error("model configuration observation is invalid");
  }
  const modelConfigObservation = {
    observed_rounds: RESTART_ROUND_COUNT,
    consistent: true,
    public_projection_token: modelConfig.public_projection_token,
  };
  return {
    schema: "taiji.desktop.acceptance-driver.v2",
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
    restart_rounds: restartRounds,
    persistent_user_data: persistentUserData,
    core_observation: coreObservation,
    model_config_observation: modelConfigObservation,
    checks: Object.fromEntries(requiredChecks.map((key) => [key, true])),
    js_error_count: 0,
    unexpected_http_failures: 0,
    electron_exit_code: 0,
  };
}

function safeErrorText(error) {
  const code = String(error?.code || "");
  return /^TAIJI-DESKTOP-E[0-9]{3}$/.test(code) ? code : "TAIJI-DESKTOP-E999";
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
    const labelText = element.labels && element.labels.length
      ? Array.from(element.labels).map((label) => label.textContent || "").join(" ")
      : "";
    const accessibleName = String(
      element.getAttribute("aria-label")
      || element.getAttribute("title")
      || labelText
      || element.getAttribute("placeholder")
      || element.textContent
      || "",
    ).trim();
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

function readHiddenCredentialFromTty(options = {}) {
  const input = options.input || process.stdin;
  const output = options.output || process.stderr;
  const timeoutMs = options.timeoutMs ?? 300000;
  if (!input?.isTTY || typeof input.setRawMode !== "function" || typeof output?.write !== "function") {
    const error = new Error("first-run credential input requires an interactive terminal");
    error.code = "TAIJI-DESKTOP-E021";
    return Promise.reject(error);
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 900000) {
    const error = new Error("first-run credential input timeout is invalid");
    error.code = "TAIJI-DESKTOP-E021";
    return Promise.reject(error);
  }
  return new Promise((resolve, reject) => {
    let value = "";
    let settled = false;
    const wasRaw = input.isRaw === true;
    const finish = (error, credential = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      input.removeListener("data", onData);
      try { input.setRawMode(wasRaw); } catch (_) {}
      try { input.pause(); } catch (_) {}
      try { output.write("\n"); } catch (_) {}
      value = "";
      if (error) reject(error);
      else resolve(credential);
    };
    const fail = (message) => {
      const error = new Error(message);
      error.code = "TAIJI-DESKTOP-E021";
      finish(error);
    };
    const onData = (chunk) => {
      const rendered = Buffer.isBuffer(chunk) ? chunk.toString("utf8") : String(chunk || "");
      for (const character of rendered) {
        if (character === "\r" || character === "\n") {
          const credential = value.trim();
          if (!credential) {
            fail("first-run credential must not be empty");
            return;
          }
          finish(null, credential);
          return;
        }
        if (character === "\u0003" || character === "\u0004") {
          fail("first-run credential input was cancelled");
          return;
        }
        if (character === "\u007f" || character === "\b") {
          value = Array.from(value).slice(0, -1).join("");
          continue;
        }
        if (character === "\u0000" || (/^[\u0000-\u001f]$/.test(character) && character !== "\t")) {
          fail("first-run credential contains an unsupported control character");
          return;
        }
        value += character;
        if (Buffer.byteLength(value, "utf8") > 4096) {
          fail("first-run credential is too large");
          return;
        }
      }
    };
    const timer = setTimeout(() => fail("first-run credential input timed out"), timeoutMs);
    try {
      input.pause();
      input.on("data", onData);
      input.setRawMode(true);
      output.write("请输入首次配置页当前 Provider 的 API 密钥（输入不回显）: ");
      input.resume();
    } catch (_) {
      fail("first-run credential input could not be initialized");
    }
  });
}

async function insertSecretThroughVisiblePasswordInput(client, selector, secret, deadline) {
  if (typeof secret !== "string" || !secret.trim() || secret.length > 4096 || /[\r\n\0]/.test(secret)) {
    const error = new Error("first-run credential is invalid");
    error.code = "TAIJI-DESKTOP-E022";
    throw error;
  }
  await physicalClickVisibleElement(client, selector, deadline, "first-run API credential field");
  const selectorJson = JSON.stringify(selector);
  await waitFor(() => evaluate(client, `(() => {
    const element = document.querySelector(${selectorJson});
    return Boolean(element && element.type === "password" && document.activeElement === element);
  })()`, deadline), { deadline, intervalMs: 100, label: "visible first-run password field focus" });
  for (const [type, key, code, modifiers, windowsVirtualKeyCode] of [
    ["keyDown", "a", "KeyA", 2, 65],
    ["keyUp", "a", "KeyA", 2, 65],
    ["keyDown", "Backspace", "Backspace", 0, 8],
    ["keyUp", "Backspace", "Backspace", 0, 8],
  ]) {
    await client.send("Input.dispatchKeyEvent", { type, key, code, modifiers, windowsVirtualKeyCode });
  }
  await client.send("Input.insertText", { text: secret });
  await waitFor(() => evaluate(client, `(() => {
    const element = document.querySelector(${selectorJson});
    return Boolean(element && element.type === "password" && document.activeElement === element && element.value.length > 0);
  })()`, deadline), { deadline, intervalMs: 100, label: "visible first-run password field input" });
}

async function configureVisibleOnboardingCredential(client, deadline, options = {}) {
  const state = await evaluate(client, `(() => {
    const step = typeof ONBOARDING === "object" ? ONBOARDING.steps[ONBOARDING.step] : "";
    const provider = step === "setup" && typeof _getOnboardingSetupProvider === "function"
      ? _getOnboardingSetupProvider(ONBOARDING.form.provider)
      : null;
    const field = document.getElementById("onboardingApiKeyInput");
    const currentIsOauth = Boolean(ONBOARDING && ONBOARDING.status && ONBOARDING.status.setup && ONBOARDING.status.setup.current_is_oauth);
    return {
      setup: step === "setup",
      field_visible: Boolean(field && field.type === "password" && getComputedStyle(field).display !== "none"),
      has_value: Boolean(field && String(field.value || "").trim()),
      key_optional: Boolean(provider && provider.key_optional),
      current_is_oauth: currentIsOauth,
    };
  })()`, deadline);
  if (!state?.setup) {
    const error = new Error("first-run credential gate is not on the setup step");
    error.code = "TAIJI-DESKTOP-E023";
    throw error;
  }
  if (state.key_optional || state.current_is_oauth || state.has_value) return { supplied: false };
  if (!state.field_visible) {
    const error = new Error("required first-run credential field is not visibly available");
    error.code = "TAIJI-DESKTOP-E023";
    throw error;
  }
  let credential = await readHiddenCredentialFromTty(options);
  try {
    await insertSecretThroughVisiblePasswordInput(client, "#onboardingApiKeyInput", credential, deadline);
  } finally {
    credential = "";
  }
  return { supplied: true };
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

async function terminateManagedProcess(identity, label, options = {}) {
  const identityPresentFn = options.processIdentityStillPresentFn || processIdentityStillPresent;
  const installedProcessArgvFn = options.installedProcessArgvFn || installedProcessArgv;
  const killFn = options.killFn || ((targetPid, signal) => process.kill(targetPid, signal));
  const sleepFn = options.sleepFn || sleep;
  const graceMs = Number.isSafeInteger(options.graceMs) && options.graceMs > 0 ? options.graceMs : 1500;
  const pollMs = Number.isSafeInteger(options.pollMs) && options.pollMs > 0 ? Math.min(options.pollMs, graceMs) : 100;
  const pid = identity?.pid;
  if (!Number.isSafeInteger(pid) || pid <= 1) return false;
  if (!identityPresentFn(identity)) return false;
  if (!managedProcessArgvMatches(label, installedProcessArgvFn(pid))) return false;
  try {
    killFn(pid, "SIGTERM");
  } catch (_) {
    return !identityPresentFn(identity);
  }
  for (let waited = 0; waited < graceMs && identityPresentFn(identity); waited += pollMs) {
    await sleepFn(pollMs);
  }
  if (!identityPresentFn(identity)) return true;
  if (!managedProcessArgvMatches(label, installedProcessArgvFn(pid))) return false;
  try {
    killFn(pid, "SIGKILL");
  } catch (_) {
    return !identityPresentFn(identity);
  }
  for (let waited = 0; waited < graceMs && identityPresentFn(identity); waited += pollMs) {
    await sleepFn(pollMs);
  }
  return !identityPresentFn(identity);
}

async function terminateOwnedChildProcess(child, identity, options = {}) {
  const identityPresentFn = options.processIdentityStillPresentFn || processIdentityStillPresent;
  const sleepFn = options.sleepFn || sleep;
  const killFn = options.killFn || ((signal) => child.kill(signal));
  const graceMs = Number.isSafeInteger(options.graceMs) && options.graceMs > 0 ? options.graceMs : 1500;
  const pollMs = Number.isSafeInteger(options.pollMs) && options.pollMs > 0 ? Math.min(options.pollMs, graceMs) : 100;
  if (!identity || !Number.isSafeInteger(identity.pid) || identity.pid <= 1) return false;
  if (!child || child.pid !== identity.pid || typeof child.kill !== "function") return false;
  if (!identityPresentFn(identity)) return false;
  try {
    killFn("SIGTERM");
  } catch (_) {
    return !identityPresentFn(identity);
  }
  for (let waited = 0; waited < graceMs && identityPresentFn(identity); waited += pollMs) {
    await sleepFn(pollMs);
  }
  if (!identityPresentFn(identity)) return true;
  try {
    killFn("SIGKILL");
  } catch (_) {
    return !identityPresentFn(identity);
  }
  for (let waited = 0; waited < graceMs && identityPresentFn(identity); waited += pollMs) {
    await sleepFn(pollMs);
  }
  return !identityPresentFn(identity);
}

async function waitForProcessIdentityExit(identity, deadline, label) {
  await waitFor(() => !processIdentityStillPresent(identity), {
    deadline,
    intervalMs: 200,
    label: `${label} original process identity exit`,
  });
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

async function waitForInstalledAppReady(client, desktop, deadline) {
  await Promise.all([
    client.send("Runtime.enable"),
    client.send("Page.enable"),
    client.send("Network.enable"),
    client.send("Log.enable"),
    client.send("DOM.enable"),
  ]);
  const cookieResult = await client.send("Network.getAllCookies");
  const desktopAuthCookie = validateDesktopAuthCookies(cookieResult?.cookies, desktop.origin);
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
  return desktopAuthCookie;
}

async function readModelConfigPublicState(client, deadline) {
  return evaluate(client, `(async () => {
    const response = await fetch("/api/model-config", {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      headers: { "Accept": "application/json" },
    });
    if (!response.ok) throw new Error("model configuration GET failed");
    const payload = await response.json();
    const main = payload && payload.main || {};
    const keyStatus = main && main.key_status || {};
    return {
      ok: payload && payload.ok === true,
      profile: String(payload && payload.profile || ""),
      main_request_id: String(payload && payload.main_request_id || ""),
      main: {
        provider: String(main.provider || ""),
        model: String(main.model || ""),
        base_url: String(main.base_url || ""),
        key_env: String(main.key_env || ""),
        key_status: {
          configured: keyStatus.configured === true,
          source: String(keyStatus.source || ""),
          env_var: String(keyStatus.env_var || ""),
        },
      },
    };
  })()`, deadline);
}

function visibleModelConfigurationMatches(snapshot, payload) {
  let projection;
  try {
    projection = publicModelConfigProjection(payload);
  } catch (_) {
    return false;
  }
  if (
    !snapshot
    || snapshot.pane_visible !== true
    || snapshot.hero_state !== "ok"
    || snapshot.main_badge_state !== "ok"
  ) {
    return false;
  }
  const provider = projection.main.provider.toLocaleLowerCase("en-US");
  const providerTokens = String(snapshot.provider_summary || "")
    .toLocaleLowerCase("en-US")
    .split(/[\s·,/|()[\]{}:]+/)
    .filter(Boolean);
  const keySummary = String(snapshot.key_summary || "").trim();
  return providerTokens.includes(provider)
    && String(snapshot.model_summary || "").trim() === projection.main.model
    && Boolean(keySummary)
    && !/(?:未配置|待配置|需要.*认证|not\s+configured|missing|unconfigured)/i.test(keySummary);
}

async function verifyVisibleModelConfiguration(client, payload, deadline) {
  await physicalClickVisibleElement(client, '.taiji-nav-item[data-taiji-panel="settings"]', deadline, "Settings navigation entry");
  await physicalClickVisibleElement(client, '#settingsMenu [data-settings-section="models"]', deadline, "Model settings entry");
  return waitFor(() => evaluate(client, `(() => {
    const pane = document.getElementById("settingsPaneModels");
    const hero = document.getElementById("modelConfigHero");
    const badge = document.getElementById("modelConfigMainStatusBadge");
    const provider = document.getElementById("modelConfigProviderSummary");
    const model = document.getElementById("modelConfigModelSummary");
    const key = document.getElementById("modelConfigKeySummary");
    return {
      pane_visible: Boolean(pane && pane.classList.contains("active") && getComputedStyle(pane).display !== "none"),
      hero_state: String(hero && hero.dataset.state || ""),
      main_badge_state: String(badge && badge.dataset.state || ""),
      provider_summary: String(provider && provider.textContent || "").trim(),
      model_summary: String(model && model.textContent || "").trim(),
      key_summary: String(key && key.textContent || "").trim(),
    };
  })()`, deadline).then((snapshot) => visibleModelConfigurationMatches(snapshot, payload) ? snapshot : null), {
    deadline,
    intervalMs: 250,
    label: "visible model configuration matching the authoritative public state",
  });
}

function observeChildExit(child) {
  const state = { exited: false, code: null, signal: null, error: null };
  const promise = new Promise((resolve) => {
    child.once("error", (error) => {
      state.error = error;
      resolve({ code: null, signal: null, error });
    });
    child.once("exit", (code, signal) => {
      state.exited = true;
      state.code = code;
      state.signal = signal;
      resolve({ code, signal, error: state.error });
    });
  });
  return { state, promise };
}

async function captureChildIdentityOrCleanExit(child, exitPromise, deadline, options = {}) {
  if (!child || !Number.isSafeInteger(child.pid) || child.pid <= 1) {
    const error = new Error("child process has no valid pid");
    error.code = "TAIJI-DESKTOP-E032";
    throw error;
  }
  const captureIdentityFn = options.captureIdentityFn || captureProcessIdentity;
  try {
    return { identity: captureIdentityFn(child.pid), clean_exit: false };
  } catch (error) {
    if (!["ENOENT", "ESRCH"].includes(String(error?.code || ""))) {
      const wrapped = new Error("child process identity could not be established");
      wrapped.code = "TAIJI-DESKTOP-E032";
      throw wrapped;
    }
    const exit = await beforeDeadline(exitPromise, deadline, "child process fast exit");
    if (exit.error || exit.code !== 0 || exit.signal) {
      const wrapped = new Error("child process disappeared without a verified clean exit");
      wrapped.code = "TAIJI-DESKTOP-E032";
      throw wrapped;
    }
    return { identity: null, clean_exit: true };
  }
}

async function verifySecondInstanceRestoresWindow({ client, targetId, args, env, deadline }) {
  const window = await client.send("Browser.getWindowForTarget", { targetId });
  if (!Number.isSafeInteger(window?.windowId)) throw new Error("Electron target has no native window id");
  await client.send("Browser.setWindowBounds", {
    windowId: window.windowId,
    bounds: { windowState: "minimized" },
  });
  await waitFor(async () => {
    const current = await client.send("Browser.getWindowBounds", { windowId: window.windowId });
    return current?.bounds?.windowState === "minimized";
  }, { deadline, intervalMs: 150, label: "primary Electron window minimization" });

  const secondary = spawn(args.electron, buildSecondaryElectronArgs(), {
    cwd: args.appDir,
    env,
    stdio: "ignore",
    windowsHide: false,
  });
  const secondaryExit = observeChildExit(secondary);
  let identity = null;
  try {
    const captured = await captureChildIdentityOrCleanExit(secondary, secondaryExit.promise, deadline);
    identity = captured.identity;
    if (!captured.clean_exit) {
      const exit = await beforeDeadline(secondaryExit.promise, deadline, "secondary Electron single-instance exit");
      if (exit.error || exit.code !== 0 || exit.signal) {
        throw new Error(`secondary Electron did not exit normally (${exit.code ?? exit.signal})`);
      }
    }
    await waitFor(async () => {
      const current = await client.send("Browser.getWindowBounds", { windowId: window.windowId });
      if (current?.bounds?.windowState === "minimized") return false;
      return evaluate(client, `(() => document.visibilityState === "visible" && document.hasFocus())()`, deadline);
    }, { deadline, intervalMs: 200, label: "single-instance primary window restore and focus" });
    if (identity) await waitForProcessIdentityExit(identity, deadline, "secondary Electron");
    return { pid: secondary.pid, identity, exitCode: 0, restoredAndFocused: true };
  } catch (error) {
    if (identity) await terminateOwnedChildProcess(secondary, identity);
    throw error;
  }
}

async function closeRoundAndVerify({
  client,
  exitPromise,
  identities,
  cdpPort,
  webuiPort,
  logDir,
  deadline,
}) {
  const electronHelpers = captureElectronHelperIdentities(identities.electron);
  let pageCloseError = null;
  try {
    await client.send("Page.close");
  } catch (error) {
    pageCloseError = error;
  }
  const exit = await beforeDeadline(exitPromise, deadline, "Electron exit after BrowserWindow close");
  if (exit.error || exit.code !== 0 || exit.signal) {
    throw new Error(`Electron did not exit normally after Page.close (${exit.code ?? exit.signal})`);
  }
  if (pageCloseError && processIdentityStillPresent(identities.electron)) throw pageCloseError;
  client.close();
  const identityExitChecks = [
    waitForProcessIdentityExit(identities.electron, deadline, "Electron"),
    waitForProcessIdentityExit(identities.agent, deadline, "Agent"),
    waitForProcessIdentityExit(identities.webui, deadline, "WebUI"),
    ...electronHelpers.map((identity) => waitForProcessIdentityExit(identity, deadline, `Electron helper ${identity.pid}`)),
  ];
  if (identities.secondary) identityExitChecks.push(waitForProcessIdentityExit(identities.secondary, deadline, "secondary Electron"));
  await Promise.all([
    ...identityExitChecks,
    waitFor(() => portIsClosed(cdpPort), { deadline, intervalMs: 250, label: "CDP port closure" }),
    waitFor(() => portIsClosed(webuiPort), { deadline, intervalMs: 250, label: "WebUI port closure" }),
    waitFor(() => !fs.existsSync(path.join(logDir, "agent.pid")) && !fs.existsSync(path.join(logDir, "web.pid")), {
      deadline,
      intervalMs: 250,
      label: "managed runtime pid file removal",
    }),
  ]);
  return { exitCode: 0, pageCloseSent: true };
}

async function runLightweightRestartRound({ round, args, env, logDir, deadline, profileMarker }) {
  const cdpPort = await reserveLoopbackPort();
  const child = spawn(args.electron, buildElectronArgs(cdpPort), {
    cwd: args.appDir,
    env,
    stdio: "ignore",
    windowsHide: false,
  });
  const childExit = observeChildExit(child);
  let client = null;
  let agentPid = null;
  let webPid = null;
  let electronIdentity = null;
  let agentIdentity = null;
  let webIdentity = null;
  let currentOrigin = null;
  let completed = false;
  try {
    const capturedElectron = await captureChildIdentityOrCleanExit(child, childExit.promise, deadline);
    if (capturedElectron.clean_exit || !capturedElectron.identity) {
      const error = new Error(`restart round ${round} Electron exited before identity verification`);
      error.code = "TAIJI-DESKTOP-E033";
      throw error;
    }
    electronIdentity = capturedElectron.identity;
    const { target, desktop } = await findDesktopTarget(cdpPort, deadline, childExit.state);
    currentOrigin = desktop.origin;
    const electronPid = child.pid;
    if (!Number.isSafeInteger(electronPid) || electronPid <= 1) throw new Error("Electron process has no valid pid");
    if (fs.readlinkSync(`/proc/${electronPid}/exe`) !== args.electron) {
      throw new Error("Electron restart executable is not the fixed installed binary");
    }
    const electronArgv = installedProcessArgv(electronPid);
    if (electronArgv[0] !== args.electron || electronArgv.at(-1) !== args.appDir) {
      throw new Error("Electron restart argv is not anchored to the fixed installed App directory");
    }
    const confirmedElectronIdentity = captureProcessIdentity(electronPid);
    if (confirmedElectronIdentity.start_time_ticks !== electronIdentity.start_time_ticks) {
      const error = new Error("Electron restart pid identity changed during validation");
      error.code = "TAIJI-DESKTOP-E031";
      throw error;
    }
    const socket = await connectWebSocket(desktop.websocket, deadline);
    client = new CdpClient(socket, Math.min(15000, remainingTime(deadline, "CDP command")));
    await waitForInstalledAppReady(client, desktop, deadline);
    if (!await verifyProfileContinuityMarker(client, desktop.origin, profileMarker)) {
      throw new Error(`restart round ${round} did not preserve the Electron profile marker`);
    }

    agentPid = await waitFor(() => {
      try { return readManagedPid(path.join(logDir, "agent.pid"), "Agent"); } catch (_) { return null; }
    }, { deadline, intervalMs: 250, label: `restart round ${round} Agent pid` });
    webPid = await waitFor(() => {
      try { return readManagedPid(path.join(logDir, "web.pid"), "WebUI"); } catch (_) { return null; }
    }, { deadline, intervalMs: 250, label: `restart round ${round} WebUI pid` });
    agentIdentity = captureProcessIdentity(agentPid);
    webIdentity = captureProcessIdentity(webPid);
    const secondary = await verifySecondInstanceRestoresWindow({ client, targetId: target.id, args, env, deadline });
    const modelConfig = await readModelConfigPublicState(client, deadline);
    publicModelConfigProjection(modelConfig);
    await verifyVisibleModelConfiguration(client, modelConfig, deadline);
    if (round === RESTART_ROUND_COUNT) {
      await deleteProfileContinuityMarker(client, desktop.origin, profileMarker);
    }
    const webuiPort = Number(new URL(desktop.url).port);
    const close = await closeRoundAndVerify({
      client,
      exitPromise: childExit.promise,
      identities: {
        electron: electronIdentity,
        agent: agentIdentity,
        webui: webIdentity,
        secondary: secondary.identity,
      },
      cdpPort,
      webuiPort,
      logDir,
      deadline,
    });
    client = null;
    completed = true;
    return {
      modelConfig,
      evidence: {
        round,
        ready: true,
        electron_pid: electronPid,
        agent_pid: agentPid,
        web_pid: webPid,
        secondary_pid: secondary.pid,
        cdp_port: cdpPort,
        webui_port: webuiPort,
        second_instance_exit_code: secondary.exitCode,
        electron_exit_code: close.exitCode,
        restored_and_focused: secondary.restoredAndFocused,
        page_close_sent: close.pageCloseSent,
        process_identities_gone: { electron: true, agent: true, webui: true, secondary: true },
        ports_closed: { cdp: true, webui: true },
        pidfiles_absent: true,
        model_config_observed: true,
        profile_continuity_observed: true,
      },
    };
  } finally {
    if (!completed && client && profileMarker && currentOrigin) {
      try { await deleteProfileContinuityMarker(client, currentOrigin, profileMarker); } catch (_) {}
    }
    client?.close();
    if (!completed) {
      if (electronIdentity) await terminateOwnedChildProcess(child, electronIdentity);
      for (const [identity, label] of [[agentIdentity, "Agent"], [webIdentity, "WebUI"]]) {
        if (identity) await terminateManagedProcess(identity, label);
      }
    }
  }
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
  let electronIdentity = null;
  let agentIdentity = null;
  let webIdentity = null;
  let profileMarker = null;
  let currentOrigin = null;
  let completed = false;

  try {
    const userIdentity = os.userInfo();
    if (typeof process.getuid !== "function" || process.getuid() !== userIdentity.uid) {
      const error = new Error("desktop acceptance user identity is inconsistent");
      error.code = "TAIJI-DESKTOP-E011";
      throw error;
    }
    assertCanonicalUserHome(userIdentity);
    const env = buildInstalledAcceptanceEnv(process.env, userIdentity);
    const stateHome = env.XDG_STATE_HOME || path.join(env.HOME, ".local", "state");
    const logDir = path.join(stateHome, "taiji-agent", "logs");
    const coreSalt = crypto.randomBytes(32);
    const modelConfigSalt = crypto.randomBytes(32);
    const coreSnapshots = [await querySettledCoreJournalSnapshot()];
    const restartRounds = [];
    const modelConfigPayloads = [];
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
    child = spawn(args.electron, buildElectronArgs(port), {
      cwd: args.appDir,
      env,
      stdio: "ignore",
      windowsHide: false,
    });
    const childExit = observeChildExit(child);
    const capturedElectron = await captureChildIdentityOrCleanExit(child, childExit.promise, deadline);
    if (capturedElectron.clean_exit || !capturedElectron.identity) {
      const error = new Error("Electron exited before identity verification");
      error.code = "TAIJI-DESKTOP-E033";
      throw error;
    }
    electronIdentity = capturedElectron.identity;
    const childState = childExit.state;
    const exitPromise = childExit.promise;

    const { target, desktop } = await findDesktopTarget(port, deadline, childState);
    currentOrigin = desktop.origin;
    const electronPid = child.pid;
    if (!Number.isSafeInteger(electronPid) || electronPid <= 1) throw new Error("Electron process has no valid pid");
    const procExecutable = fs.readlinkSync(`/proc/${electronPid}/exe`);
    if (procExecutable !== args.electron) throw new Error(`Electron pid executable is not the fixed installed binary: ${procExecutable}`);
    const electronArgv = installedProcessArgv(electronPid);
    if (electronArgv[0] !== args.electron || electronArgv.at(-1) !== args.appDir) {
      throw new Error("Electron process argv is not anchored to the fixed installed App directory");
    }
    const confirmedElectronIdentity = captureProcessIdentity(electronPid);
    if (confirmedElectronIdentity.start_time_ticks !== electronIdentity.start_time_ticks) {
      const error = new Error("Electron pid identity changed during validation");
      error.code = "TAIJI-DESKTOP-E031";
      throw error;
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

    const desktopAuthCookie = await waitForInstalledAppReady(client, desktop, deadline);
    profileMarker = await createProfileContinuityMarker(
      client,
      desktop.origin,
      args.challenge,
      args.sessionId,
      crypto.randomBytes(32),
    );
    if (!await verifyProfileContinuityMarker(client, desktop.origin, profileMarker)) {
      throw new Error("first restart round could not observe its persistent profile marker");
    }
    const secondaryRoundOne = await verifySecondInstanceRestoresWindow({
      client,
      targetId: target.id,
      args,
      env,
      deadline,
    });

    const firstConfigurationStart = await evaluate(client, `(() => {
      const overlay = document.getElementById("onboardingOverlay");
      return {
        visible: Boolean(overlay && getComputedStyle(overlay).display !== "none"),
        active: Boolean(typeof ONBOARDING === "object" && ONBOARDING.active),
        completed: Boolean(typeof ONBOARDING === "object" && ONBOARDING.status && ONBOARDING.status.completed === true),
      };
    })()`, deadline);
    assertVisibleFirstConfigurationStart(firstConfigurationStart);

    for (const expectedStep of ["system", "setup", "workspace", "password"]) {
      await waitFor(() => evaluate(client, `(() => {
        const button = document.getElementById("onboardingNextBtn");
        const step = typeof ONBOARDING === "object" ? ONBOARDING.steps[ONBOARDING.step] : "";
        return Boolean(step === ${JSON.stringify(expectedStep)} && button && !button.disabled && getComputedStyle(button).display !== "none");
      })()`, deadline), { deadline, intervalMs: 250, label: `visible first-configuration ${expectedStep} step` });
      if (expectedStep === "setup") await configureVisibleOnboardingCredential(client, deadline);
      await physicalClickVisibleElement(client, "#onboardingNextBtn", deadline, `first-configuration ${expectedStep} Continue action`);
      await waitFor(() => evaluate(client, `(() => (
        typeof ONBOARDING === "object" && ONBOARDING.steps[ONBOARDING.step] !== ${JSON.stringify(expectedStep)}
      ))()`, deadline), { deadline, intervalMs: 250, label: `first-configuration advance from ${expectedStep}` });
    }

    await waitFor(() => evaluate(client, `(() => {
      const button = document.getElementById("onboardingNextBtn");
      const step = typeof ONBOARDING === "object" ? ONBOARDING.steps[ONBOARDING.step] : "";
      return Boolean(step === "finish" && button && !button.disabled && getComputedStyle(button).display !== "none");
    })()`, deadline), { deadline, intervalMs: 250, label: "visible first-configuration Finish action" });
    await physicalClickVisibleElement(client, "#onboardingNextBtn", deadline, "first-configuration Finish action");
    await waitFor(() => evaluate(client, `(async () => {
      const overlay = document.getElementById("onboardingOverlay");
      let status = null;
      try {
        const response = await fetch("/api/onboarding/status", { credentials: "include" });
        if (response.ok) status = await response.json();
      } catch (_) {}
      return {
        visible: Boolean(overlay && getComputedStyle(overlay).display !== "none"),
        active: Boolean(typeof ONBOARDING === "object" && ONBOARDING.active),
        completed: Boolean(status && status.completed === true),
        preflightReady: Boolean(status && status.preflight && status.preflight.overall_ready === true),
      };
    })()`, deadline).then((state) => firstConfigurationCompletionObserved(state) ? state : null), {
      deadline,
      intervalMs: 350,
      label: "server-confirmed visible first-configuration completion",
    });

    agentPid = await waitFor(() => {
      try { return readManagedPid(path.join(logDir, "agent.pid"), "Agent"); } catch (_) { return null; }
    }, { deadline, intervalMs: 250, label: "installed Agent pid" });
    webPid = await waitFor(() => {
      try { return readManagedPid(path.join(logDir, "web.pid"), "WebUI"); } catch (_) { return null; }
    }, { deadline, intervalMs: 250, label: "installed WebUI pid" });
    agentIdentity = captureProcessIdentity(agentPid);
    webIdentity = captureProcessIdentity(webPid);
    const firstModelConfig = await readModelConfigPublicState(client, deadline);
    publicModelConfigProjection(firstModelConfig);
    await verifyVisibleModelConfiguration(client, firstModelConfig, deadline);
    modelConfigPayloads.push(firstModelConfig);

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
    const firstClose = await closeRoundAndVerify({
      client,
      exitPromise,
      identities: {
        electron: electronIdentity,
        agent: agentIdentity,
        webui: webIdentity,
        secondary: secondaryRoundOne.identity,
      },
      cdpPort: port,
      webuiPort,
      logDir,
      deadline,
    });
    client = null;
    child = null;
    restartRounds.push({
      round: 1,
      ready: true,
      electron_pid: electronPid,
      agent_pid: agentPid,
      web_pid: webPid,
      secondary_pid: secondaryRoundOne.pid,
      cdp_port: port,
      webui_port: webuiPort,
      second_instance_exit_code: secondaryRoundOne.exitCode,
      electron_exit_code: firstClose.exitCode,
      restored_and_focused: secondaryRoundOne.restoredAndFocused,
      page_close_sent: firstClose.pageCloseSent,
      process_identities_gone: { electron: true, agent: true, webui: true, secondary: true },
      ports_closed: { cdp: true, webui: true },
      pidfiles_absent: true,
      model_config_observed: true,
      profile_continuity_observed: true,
    });
    coreSnapshots.push(await querySettledCoreJournalSnapshot());

    for (let round = 2; round <= RESTART_ROUND_COUNT; round += 1) {
      const restart = await runLightweightRestartRound({ round, args, env, logDir, deadline, profileMarker });
      restartRounds.push(restart.evidence);
      modelConfigPayloads.push(restart.modelConfig);
      coreSnapshots.push(await querySettledCoreJournalSnapshot());
    }

    const modelConfigObservation = buildModelConfigObservation(modelConfigPayloads, modelConfigSalt);
    const coreObservation = buildCoreObservation(coreSnapshots, coreSalt);

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
      exitCode: firstClose.exitCode,
      jsErrors: unexpectedJsErrors,
      unexpectedHttpFailures,
      restartRounds,
      persistentUserData: {
        mode: "electron-default-persistent",
        restart_rounds: RESTART_ROUND_COUNT,
        user_data_override: false,
        profile_reset: false,
        environment_reused: true,
        continuity_observed_rounds: restartRounds.filter((round) => round.profile_continuity_observed === true).length,
        continuity_token: profileMarker.continuity_token,
      },
      coreObservation,
      modelConfigObservation,
      checks: {
        visible_first_configuration_completion: true,
        desktop_launch: true,
        real_model_conversation: true,
        attachment_flow: true,
        window_close_exit: true,
        diagnostic_export: true,
        three_restart_cycles: restartRounds.length === RESTART_ROUND_COUNT,
        second_instance_focus: restartRounds.every((round) => round.restored_and_focused === true),
        model_configuration_state_consistent: modelConfigObservation.consistent,
        no_new_electron_core: coreObservation.status === "verified",
      },
    });
    fs.rmSync(fixturePath, { force: true });
    atomicWriteJson(resultPath, result);
    completed = true;
    return { resultPath, result };
  } finally {
    if (!completed && client && profileMarker && currentOrigin) {
      try { await deleteProfileContinuityMarker(client, currentOrigin, profileMarker); } catch (_) {}
    }
    client?.close();
    if (!completed) {
      fs.rmSync(resultPath, { force: true });
      fs.rmSync(screenshotPath, { force: true });
      fs.rmSync(supportBundlePath, { force: true });
      fs.rmSync(fixturePath, { force: true });
      fs.rmSync(downloadDir, { recursive: true, force: true });
      if (child && electronIdentity) await terminateOwnedChildProcess(child, electronIdentity);
      for (const [identity, label] of [[agentIdentity, "Agent"], [webIdentity, "WebUI"]]) {
        if (!identity) continue;
        const stopped = await terminateManagedProcess(identity, label);
        if (!stopped && processIdentityStillPresent(identity)) {
          process.stderr.write("taiji-desktop-acceptance-cleanup-warning\tTAIJI-DESKTOP-E034\n");
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
  assertVisibleFirstConfigurationStart,
  assertCanonicalUserHome,
  buildCoreObservation,
  buildCoreJournalArgs,
  buildDriverResult,
  buildElectronArgs,
  buildInstalledAcceptanceEnv,
  buildModelConfigObservation,
  buildProbeCode,
  buildSecondaryElectronArgs,
  captureChildIdentityOrCleanExit,
  captureElectronHelperIdentities,
  completionSnapshotPassed,
  coreHandlerIsTrusted,
  coreJournalToolIsTrusted,
  createProfileContinuityMarker,
  deleteProfileContinuityMarker,
  filterUnexpectedHttpFailures,
  filterUnexpectedJsErrors,
  firstConfigurationCompletionObserved,
  inspectProcessIdentity,
  insertSecretThroughVisiblePasswordInput,
  insertTextThroughVisibleComposer,
  isExpectedBackgroundConsoleError,
  isExpectedDesktopHttpFailure,
  managedProcessArgvMatches,
  normalizeMessageContent,
  parseArgs,
  parseCoreJournalJsonCursors,
  parsePid,
  physicalClickVisibleElement,
  processIdentityFromStat,
  processIdentityStillPresent,
  publicModelConfigProjection,
  queryCoreJournalSnapshot,
  querySettledCoreJournalSnapshot,
  readHiddenCredentialFromTty,
  redactDesktopUrl,
  safeErrorText,
  supportBundleIsSafe,
  terminateManagedProcess,
  terminateOwnedChildProcess,
  verifyVisibleModelConfiguration,
  verifyProfileContinuityMarker,
  visibleModelConfigurationMatches,
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
