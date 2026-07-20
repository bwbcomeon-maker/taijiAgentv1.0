#!/usr/bin/env node
/*
 * Real Electron acceptance for the visible Image Capability Center.
 *
 * The desktop shell, WebUI, settings navigation, DOM, keyboard focus, and
 * reload path are production code. The two image-capability HTTP endpoints
 * are intercepted inside Electron's BrowserContext so no Provider, OAuth
 * flow, system browser, or public network is contacted.
 */
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const {
  assertNavigationParity,
  buildAcceptanceProvenance,
  captureAuditedScreenshot,
  collectSourceFingerprint,
  inspectTaijiNavigation,
  installDailyEquivalentRuntimeConfig,
} = require("./electron_acceptance_provenance");

const AMBIENT_GIT_ENV = [
  "GIT_DIR",
  "GIT_WORK_TREE",
  "GIT_COMMON_DIR",
  "GIT_INDEX_FILE",
  "GIT_OBJECT_DIRECTORY",
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
];

function parseArgs(argv) {
  const result = { outDir: "", repoRoot: "", requireCleanSource: false };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--out-dir") result.outDir = argv[index + 1] || "";
    if (argv[index] === "--repo-root") result.repoRoot = argv[index + 1] || "";
    if (argv[index] === "--require-clean-source") result.requireCleanSource = true;
  }
  if (!result.outDir) throw new Error("--out-dir is required");
  return result;
}

function prepareResultDestination(outDirValue) {
  const outDir = path.resolve(outDirValue);
  fs.mkdirSync(outDir, { recursive: true });
  const resultFile = path.join(
    outDir,
    "electron-image-capability-center-result.json",
  );
  fs.rmSync(resultFile, { force: true });
  for (const entry of fs.readdirSync(outDir, { withFileTypes: true })) {
    if (
      entry.isFile()
      && (
        entry.name.startsWith(
          "electron-image-capability-center-result.json.tmp-",
        )
        || entry.name.startsWith(
          ".electron-image-capability-center-result.candidate-",
        )
      )
    ) {
      fs.rmSync(path.join(outDir, entry.name), { force: true });
    }
  }
  return { outDir, resultFile };
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
  if (!condition) {
    throw new Error(`${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function sha256Text(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex");
}

function pidAlive(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (_) {
    return false;
  }
}

function runText(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed`, {
      cause: result.error || new Error(String(result.stderr || "").trim()),
    });
  }
  return String(result.stdout || "").trim();
}

function cleanGitEnv() {
  const env = { ...process.env };
  for (const name of AMBIENT_GIT_ENV) delete env[name];
  return env;
}

function gitText(repoRoot, args) {
  return runText("git", ["-C", repoRoot, ...args], {
    env: cleanGitEnv(),
  });
}

function realpathExisting(candidate, label) {
  assertState(fs.existsSync(candidate), `${label} missing`, { path: candidate });
  return fs.realpathSync(candidate);
}

function gitSnapshot(repoRoot) {
  return {
    repo_root: fs.realpathSync(repoRoot),
    branch: gitText(repoRoot, ["rev-parse", "--abbrev-ref", "HEAD"]),
    commit: gitText(repoRoot, ["rev-parse", "HEAD"]),
    status_short: gitText(repoRoot, ["status", "--short"]),
    common_dir: fs.realpathSync(
      path.resolve(
        repoRoot,
        gitText(repoRoot, ["rev-parse", "--git-common-dir"]),
      ),
    ),
  };
}

function assertFormalMainSource(sourceSnapshot, sourceFingerprint) {
  assertState(
    sourceSnapshot.branch === "main"
      && !sourceSnapshot.status_short
      && sourceFingerprint.branch === "main"
      && sourceFingerprint.checkout_type === "formal_main_primary_worktree"
      && sourceFingerprint.dirty === false
      && sourceFingerprint.commit === sourceSnapshot.commit,
    "Electron acceptance requires a clean formal main source and matching fingerprint",
    {
      source_snapshot: sourceSnapshot,
      source_fingerprint: sourceFingerprint,
    },
  );
}

function collectExecutionSourceFingerprint({
  repoRoot,
  webuiDir,
  productMain,
  productPreload,
}) {
  return {
    ...collectSourceFingerprint({ repoRoot, webuiDir }),
    acceptance_script_realpath: fs.realpathSync(__filename),
    acceptance_script_sha256: sha256File(__filename),
    api_model_config_sha256: sha256File(
      path.join(webuiDir, "api", "model_config.py"),
    ),
    desktop_main_realpath: productMain,
    desktop_main_sha256: sha256File(productMain),
    desktop_preload_realpath: productPreload,
    desktop_preload_sha256: sha256File(productPreload),
  };
}

function sourceStabilityProjection(snapshot, fingerprint) {
  return {
    snapshot: {
      repo_root: snapshot.repo_root,
      branch: snapshot.branch,
      commit: snapshot.commit,
      status_short: snapshot.status_short,
      common_dir: snapshot.common_dir,
    },
    fingerprint: {
      branch: fingerprint.branch,
      commit: fingerprint.commit,
      dirty: fingerprint.dirty,
      checkout_type: fingerprint.checkout_type,
      static_files_sha256: fingerprint.static_files_sha256,
      acceptance_script_realpath: fingerprint.acceptance_script_realpath,
      acceptance_script_sha256: fingerprint.acceptance_script_sha256,
      api_model_config_sha256: fingerprint.api_model_config_sha256,
      desktop_main_realpath: fingerprint.desktop_main_realpath,
      desktop_main_sha256: fingerprint.desktop_main_sha256,
      desktop_preload_realpath: fingerprint.desktop_preload_realpath,
      desktop_preload_sha256: fingerprint.desktop_preload_sha256,
    },
  };
}

function assertStableSource(
  initialSnapshot,
  initialFingerprint,
  finalSnapshot,
  finalFingerprint,
) {
  const initial = sourceStabilityProjection(initialSnapshot, initialFingerprint);
  const final = sourceStabilityProjection(finalSnapshot, finalFingerprint);
  assertState(
    JSON.stringify(final) === JSON.stringify(initial),
    "source changed during Electron acceptance",
    { initial, final },
  );
  assertFormalMainSource(finalSnapshot, finalFingerprint);
}

function resolveDefaultElectron(repoRoot) {
  const suffix = path.join(
    "apps",
    "taiji-desktop",
    "node_modules",
    "electron",
    "dist",
    "Electron.app",
    "Contents",
    "MacOS",
    "Electron",
  );
  const candidates = [path.join(repoRoot, suffix)];
  try {
    const commonDir = path.resolve(
      repoRoot,
      gitText(repoRoot, ["rev-parse", "--git-common-dir"]),
    );
    candidates.push(path.join(path.dirname(commonDir), suffix));
  } catch (_) {}
  candidates.push(path.join(path.resolve(repoRoot, "..", ".."), suffix));
  const chosen = candidates.find(candidate => fs.existsSync(candidate));
  assertState(Boolean(chosen), "Electron binary missing", { candidates });
  return {
    candidates,
    chosen: fs.realpathSync(chosen),
  };
}

function processTable() {
  const result = spawnSync("ps", ["-axo", "pid=,ppid=,lstart=,command="], {
    encoding: "utf8",
  });
  if (result.status !== 0) return new Map();
  const table = new Map();
  for (const line of String(result.stdout || "").split(/\r?\n/)) {
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+(.{24})\s+(.*)$/);
    if (!match) continue;
    table.set(Number(match[1]), {
      pid: Number(match[1]),
      ppid: Number(match[2]),
      started: match[3],
      command: match[4],
      command_sha256: sha256Text(match[4]),
    });
  }
  return table;
}

function isBaselineProcess(baseline, current) {
  return Boolean(
    baseline
      && current
      && baseline.pid === current.pid
      && baseline.started === current.started
      && baseline.command_sha256 === current.command_sha256,
  );
}

function psField(pid, field) {
  const result = spawnSync("ps", ["-p", String(pid), "-o", `${field}=`], {
    encoding: "utf8",
  });
  if (result.status !== 0) return "";
  return String(result.stdout || "").trim();
}

function processCwd(pid) {
  const result = spawnSync("lsof", ["-a", "-p", String(pid), "-d", "cwd", "-Fn"], {
    encoding: "utf8",
  });
  if (result.status !== 0) return "";
  const line = String(result.stdout || "")
    .split(/\r?\n/)
    .find(value => value.startsWith("n"));
  if (!line) return "";
  const candidate = line.slice(1);
  try {
    return fs.realpathSync(candidate);
  } catch (_) {
    return path.resolve(candidate);
  }
}

function processIdentity(pid) {
  if (!pidAlive(pid)) return null;
  const command = psField(pid, "command");
  const started = psField(pid, "lstart");
  const ppid = Number(psField(pid, "ppid")) || 0;
  const pgid = Number(psField(pid, "pgid")) || 0;
  const cwd = processCwd(pid);
  if (!command || !started) return null;
  return {
    pid,
    ppid,
    pgid,
    started,
    command,
    command_sha256: sha256Text(command),
    cwd,
    identity_sha256: sha256Text([pid, started, command, cwd].join("\0")),
  };
}

function tcpListenerPids(port) {
  assertState(
    Number.isInteger(Number(port)) && Number(port) > 0 && Number(port) <= 65535,
    "invalid TCP listener port",
    { port },
  );
  const result = spawnSync(
    "lsof",
    [
      "-nP",
      "-a",
      `-iTCP:${Number(port)}`,
      "-sTCP:LISTEN",
      "-Fp",
    ],
    { encoding: "utf8" },
  );
  if (result.status !== 0) return [];
  return [
    ...new Set(
      String(result.stdout || "")
        .split(/\r?\n/)
        .filter(line => /^p\d+$/.test(line))
        .map(line => Number(line.slice(1)))
        .filter(pid => Number.isInteger(pid) && pid > 0),
    ),
  ].sort((left, right) => left - right);
}

function assertTcpListenerOwner(port, expectedPid, listenerPids = tcpListenerPids(port)) {
  const expected = Number(expectedPid);
  const actual = [...new Set(listenerPids.map(Number))]
    .filter(pid => Number.isInteger(pid) && pid > 0)
    .sort((left, right) => left - right);
  assertState(
    actual.length === 1 && actual[0] === expected,
    "desktop page TCP listener is not owned by the verified WebUI PID",
    { port: Number(port), expected_pid: expected, listener_pids: actual },
  );
  return {
    port: Number(port),
    pid: expected,
    listener_pids: actual,
  };
}

function descendantsOf(rootPids, table = processTable()) {
  const descendants = new Set();
  const queue = [...rootPids];
  while (queue.length) {
    const parent = queue.shift();
    for (const row of table.values()) {
      if (row.ppid !== parent || descendants.has(row.pid) || rootPids.includes(row.pid)) {
        continue;
      }
      descendants.add(row.pid);
      queue.push(row.pid);
    }
  }
  return [...descendants];
}

function assertSameProcess(expected, current, phase) {
  assertState(Boolean(current), `owned ${phase} process disappeared before identity check`, {
    expected,
  });
  assertState(
    current.identity_sha256 === expected.identity_sha256,
    `refusing to signal reused or replaced PID during ${phase}`,
    {
      expected,
      current,
    },
  );
}

async function waitForPidsToExit(pids, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (pids.every(pid => !pidAlive(pid))) return true;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  return pids.every(pid => !pidAlive(pid));
}

async function terminateOwnedProcesses(identities) {
  const owned = [...new Map(
    identities
      .filter(Boolean)
      .map(identity => [identity.pid, identity]),
  ).values()];
  const term = [];
  const skipped = [];
  for (const identity of owned) {
    const { pid } = identity;
    if (!pidAlive(pid)) continue;
    const current = processIdentity(pid);
    try {
      assertSameProcess(identity, current, "SIGTERM");
    } catch (error) {
      skipped.push({
        pid,
        reason: String(error && error.message ? error.message : error),
      });
      continue;
    }
    try {
      process.kill(pid, "SIGTERM");
      term.push(pid);
    } catch (_) {}
  }
  const ownedPids = owned.map(identity => identity.pid);
  if (await waitForPidsToExit(ownedPids, 5000)) {
    return { term, kill: [], skipped };
  }
  const kill = [];
  for (const identity of owned) {
    const { pid } = identity;
    if (!pidAlive(pid)) continue;
    const current = processIdentity(pid);
    try {
      assertSameProcess(identity, current, "SIGKILL");
    } catch (error) {
      skipped.push({
        pid,
        reason: String(error && error.message ? error.message : error),
      });
      continue;
    }
    try {
      process.kill(pid, "SIGKILL");
      kill.push(pid);
    } catch (_) {}
  }
  assertState(
    await waitForPidsToExit(ownedPids, 5000),
    "owned fixture processes survived cleanup",
    { owned: ownedPids, skipped },
  );
  return { term, kill, skipped };
}

function readPid(file) {
  try {
    const value = Number(fs.readFileSync(file, "utf8").trim());
    return Number.isFinite(value) ? value : 0;
  } catch (_) {
    return 0;
  }
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\"'\"'`)}'`;
}

function writeHarnessGuards({
  harnessRoot,
  productMain,
  pythonBin,
  guardMarker,
}) {
  const electronWrapper = path.join(harnessRoot, "electron-wrapper");
  const pythonGuard = path.join(harnessRoot, "python-guard");
  const guardLog = path.join(harnessRoot, "network-guard.jsonl");
  fs.mkdirSync(electronWrapper, { recursive: true });
  fs.mkdirSync(pythonGuard, { recursive: true });
  fs.writeFileSync(
    path.join(electronWrapper, "package.json"),
    `${JSON.stringify({
      name: "taiji-image-capability-electron-guard",
      private: true,
      main: "bootstrap.js",
    }, null, 2)}\n`,
    "utf8",
  );
  const electronBootstrap = `
"use strict";
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const electron = require("electron");
const guardLog = process.env.TAIJI_ELECTRON_GUARD_LOG;
function record(type, detail = {}) {
  const row = {
    pid: process.pid,
    type,
    marker_sha256: crypto.createHash("sha256")
      .update(String(process.env.TAIJI_NETWORK_GUARD_MARKER || ""))
      .digest("hex"),
    ...detail,
  };
  fs.appendFileSync(guardLog, JSON.stringify(row) + "\\n", { mode: 0o600 });
}
function urlDigest(value) {
  return crypto.createHash("sha256").update(String(value || "")).digest("hex");
}
function isLoopbackUrl(value) {
  try {
    const parsed = new URL(String(value));
    return parsed.protocol === "http:"
      && (parsed.hostname === "127.0.0.1" || parsed.hostname === "::1");
  } catch (_) {
    return false;
  }
}
function isLoopbackHost(value) {
  const host = String(value || "").replace(/^\\[|\\]$/g, "").toLowerCase();
  return host === "127.0.0.1" || host === "::1" || host === "localhost";
}
electron.app.commandLine.appendSwitch("disable-background-networking");
electron.app.commandLine.appendSwitch("disable-component-update");
electron.app.commandLine.appendSwitch("disable-domain-reliability");
electron.app.commandLine.appendSwitch("proxy-server", "http://127.0.0.1:9");
electron.app.commandLine.appendSwitch("proxy-bypass-list", "127.0.0.1;[::1];localhost");
const originalConnect = net.Socket.prototype.connect;
const originalCreateConnection = net.createConnection;
function connectionTarget(args) {
  const first = args[0];
  let host = "";
  let port = "";
  let isUnix = false;
  if (typeof first === "string") {
    isUnix = true;
  } else if (first && typeof first === "object") {
    if (first.path && !first.host && !first.hostname) isUnix = true;
    host = first.host || first.hostname || "";
    port = first.port || "";
  } else if (typeof first === "number") {
    port = first;
    host = typeof args[1] === "string" ? args[1] : "localhost";
  }
  return { host, port, isUnix };
}
function blockedError() {
  return Object.assign(
    new Error("TAIJI_ELECTRON_TEST_NETWORK_BLOCKED"),
    { code: "TAIJI_ELECTRON_TEST_NETWORK_BLOCKED" },
  );
}
function blockedSocket() {
  const socket = new net.Socket();
  process.nextTick(() => socket.destroy(blockedError()));
  return socket;
}
function mustBlock(args) {
  const target = connectionTarget(args);
  return !target.isUnix && Boolean(target.host) && !isLoopbackHost(target.host);
}
net.Socket.prototype.connect = function guardedConnect(...args) {
  if (mustBlock(args)) {
    const target = connectionTarget(args);
    record("main_network_blocked", {
      destination_class: "public",
      target_sha256: urlDigest(\`\${target.host}:\${target.port}\`),
    });
    process.nextTick(() => this.destroy(blockedError()));
    return this;
  }
  return originalConnect.apply(this, args);
};
function guardedCreateConnection(...args) {
  if (mustBlock(args)) {
    const target = connectionTarget(args);
    record("main_network_blocked", {
      destination_class: "public",
      target_sha256: urlDigest(\`\${target.host}:\${target.port}\`),
    });
    return blockedSocket();
  }
  return originalCreateConnection.apply(net, args);
}
net.connect = guardedCreateConnection;
net.createConnection = guardedCreateConnection;
for (const method of ["openExternal", "openPath"]) {
  if (typeof electron.shell[method] !== "function") continue;
  electron.shell[method] = async value => {
    record("shell_external_blocked", {
      method,
      target_sha256: urlDigest(value),
    });
    throw Object.assign(
      new Error("TAIJI_ELECTRON_TEST_EXTERNAL_OPEN_BLOCKED"),
      { code: "TAIJI_ELECTRON_TEST_EXTERNAL_OPEN_BLOCKED" },
    );
  };
}
electron.app.on("web-contents-created", (_event, contents) => {
  contents.on("will-navigate", (event, target) => {
    if (isLoopbackUrl(target)) return;
    event.preventDefault();
    record("navigation_blocked", { target_sha256: urlDigest(target) });
  });
  contents.on("will-redirect", (event, target) => {
    if (isLoopbackUrl(target)) return;
    event.preventDefault();
    record("redirect_blocked", { target_sha256: urlDigest(target) });
  });
  const install = contents.setWindowOpenHandler.bind(contents);
  contents.setWindowOpenHandler = handler => install(details => {
    if (!isLoopbackUrl(details.url)) {
      record("window_open_blocked", {
        target_sha256: urlDigest(details.url),
        disposition: String(details.disposition || ""),
      });
      return { action: "deny" };
    }
    return typeof handler === "function" ? handler(details) : { action: "deny" };
  });
});
electron.app.__taijiGuardProbeMainNetwork = () => new Promise(resolve => {
  const socket = net.createConnection({ host: "203.0.113.1", port: 9 });
  socket.once("error", error => resolve({
    blocked: error && error.code === "TAIJI_ELECTRON_TEST_NETWORK_BLOCKED",
    code: String(error && error.code || ""),
  }));
  socket.once("connect", () => {
    socket.destroy();
    resolve({ blocked: false, code: "unexpected_connect" });
  });
});
electron.app.__taijiGuardProbeShell = async value => {
  try {
    await electron.shell.openExternal(value);
    return { blocked: false, code: "unexpected_open" };
  } catch (error) {
    return {
      blocked: error && error.code === "TAIJI_ELECTRON_TEST_EXTERNAL_OPEN_BLOCKED",
      code: String(error && error.code || ""),
    };
  }
};
record("electron_guard_loaded");
require(${JSON.stringify(productMain)});
`;
  fs.writeFileSync(
    path.join(electronWrapper, "bootstrap.js"),
    electronBootstrap.trimStart(),
    { encoding: "utf8", mode: 0o600 },
  );

  const sitecustomize = `
import errno
import hashlib
import json
import os
import socket
import sys

_GUARD_LOG = os.environ["TAIJI_PYTHON_GUARD_LOG"]
_ROLE = os.environ.get("TAIJI_PYTHON_GUARD_ROLE", "unknown")
_MARKER_SHA256 = hashlib.sha256(
    os.environ.get("TAIJI_NETWORK_GUARD_MARKER", "").encode("utf-8")
).hexdigest()
_CWD_SHA256 = hashlib.sha256(
    os.path.realpath(os.getcwd()).encode("utf-8")
).hexdigest()
_EXECUTABLE_SHA256 = hashlib.sha256(
    os.path.realpath(sys.executable).encode("utf-8")
).hexdigest()

def _target_digest(target):
    return hashlib.sha256(str(target or "").encode("utf-8")).hexdigest()

def _address_target(address):
    if isinstance(address, tuple) and address:
        host = address[0]
        port = address[1] if len(address) > 1 else ""
        return f"{host}:{port}"
    return str(address or "")

def _record(kind, target=""):
    row = {
        "pid": os.getpid(),
        "type": kind,
        "role": _ROLE,
        "marker_sha256": _MARKER_SHA256,
        "cwd_sha256": _CWD_SHA256,
        "executable_sha256": _EXECUTABLE_SHA256,
    }
    if target:
        row["target_sha256"] = _target_digest(target)
    with open(_GUARD_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\\n")

def _claim_role_probe():
    claim = f"{_GUARD_LOG}.{_ROLE}.probe"
    try:
        descriptor = os.open(
            claim,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    return True

def _loopback(host):
    value = str(host or "").strip("[]").lower()
    return value in {"127.0.0.1", "::1", "localhost"}

def _blocked(target):
    _record("python_network_blocked", target)
    raise OSError(errno.ENETUNREACH, "TAIJI_PYTHON_TEST_NETWORK_BLOCKED")

_original_socket_connect = socket.socket.connect
_original_socket_connect_ex = socket.socket.connect_ex
_original_create_connection = socket.create_connection

def _guarded_socket_connect(instance, address):
    if instance.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0] if isinstance(address, tuple) and address else ""
        if not _loopback(host):
            return _blocked(_address_target(address))
    return _original_socket_connect(instance, address)

def _guarded_socket_connect_ex(instance, address):
    if instance.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0] if isinstance(address, tuple) and address else ""
        if not _loopback(host):
            _record("python_network_blocked", _address_target(address))
            return errno.ENETUNREACH
    return _original_socket_connect_ex(instance, address)

def _guarded_create_connection(address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) and address else ""
    if not _loopback(host):
        return _blocked(_address_target(address))
    return _original_create_connection(address, *args, **kwargs)

socket.socket.connect = _guarded_socket_connect
socket.socket.connect_ex = _guarded_socket_connect_ex
socket.create_connection = _guarded_create_connection
if _ROLE in {"agent", "web"}:
    _record("python_guard_loaded")
if _ROLE in {"agent", "web"} and _claim_role_probe():
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.01)
        probe.connect(("203.0.113.1", 9))
    except OSError as error:
        if getattr(error, "errno", None) == errno.ENETUNREACH:
            _record("python_guard_self_test_blocked", "203.0.113.1:9")
        else:
            _record("python_guard_self_test_failed")
    finally:
        try:
            probe.close()
        except Exception:
            pass
`;
  fs.writeFileSync(
    path.join(pythonGuard, "sitecustomize.py"),
    sitecustomize.trimStart(),
    { encoding: "utf8", mode: 0o600 },
  );
  const pythonWrapper = path.join(pythonGuard, "python-with-network-guard");
  fs.writeFileSync(
    pythonWrapper,
    [
      "#!/bin/sh",
      "set -eu",
      `export PYTHONPATH=${shellQuote(pythonGuard)}`,
      'role="bootstrap"',
      'if [ "${1:-}" = "-m" ] && [ "${2:-}" = "taiji_runtime.main" ] && [ "${3:-}" = "gateway" ] && [ "${4:-}" = "run" ]; then',
      '  role="agent"',
      "else",
      '  case "${1:-}" in',
      '    */server.py|*/server.pyc) role="web" ;;',
      "  esac",
      "fi",
      'export TAIJI_PYTHON_GUARD_ROLE="$role"',
      `exec ${shellQuote(pythonBin)} "$@"`,
      "",
    ].join("\n"),
    { encoding: "utf8", mode: 0o700 },
  );
  return {
    electronWrapper: fs.realpathSync(electronWrapper),
    pythonWrapper: fs.realpathSync(pythonWrapper),
    pythonGuard: fs.realpathSync(pythonGuard),
    guardLog,
    markerSha256: sha256Text(guardMarker),
  };
}

function readGuardEvents(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(`invalid network guard event at line ${index + 1}`, {
          cause: error,
        });
      }
    });
}

function normalizedGuardEvent(event) {
  return {
    pid: Number(event.pid),
    role: String(event.role || "electron"),
    type: String(event.type || ""),
    marker_sha256: String(event.marker_sha256 || ""),
    target_sha256: String(event.target_sha256 || ""),
    method: String(event.method || ""),
  };
}

function sortGuardEvents(events) {
  return events
    .map(normalizedGuardEvent)
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

function assertExactGuardEvents(events, {
  appPid,
  agentPid,
  webPid,
  markerSha256,
  publicTargetSha256,
  canaryTargetSha256,
  agentCwdSha256,
  webCwdSha256,
  pythonExecutableSha256,
}) {
  const electron = { pid: appPid, marker_sha256: markerSha256 };
  const pythonProbeEvents = (role, pid) => [
    {
      pid,
      role,
      type: "python_network_blocked",
      marker_sha256: markerSha256,
      target_sha256: publicTargetSha256,
    },
    {
      pid,
      role,
      type: "python_guard_self_test_blocked",
      marker_sha256: markerSha256,
      target_sha256: publicTargetSha256,
    },
  ];
  const expectedCore = [
    { ...electron, type: "electron_guard_loaded" },
    {
      ...electron,
      type: "main_network_blocked",
      target_sha256: publicTargetSha256,
    },
    {
      ...electron,
      type: "shell_external_blocked",
      target_sha256: canaryTargetSha256,
      method: "openExternal",
    },
    {
      ...electron,
      type: "window_open_blocked",
      target_sha256: canaryTargetSha256,
    },
    ...pythonProbeEvents("agent", agentPid),
    ...pythonProbeEvents("web", webPid),
  ];
  const loaded = events.filter(event => event.type === "python_guard_loaded");
  const expectedIdentity = {
    agent: agentCwdSha256,
    web: webCwdSha256,
  };
  const loadedKeys = new Set();
  for (const event of loaded) {
    const role = String(event.role || "");
    const key = `${role}:${Number(event.pid)}`;
    assertState(
      ["agent", "web"].includes(role)
        && Number.isInteger(Number(event.pid))
        && Number(event.pid) > 0
        && Number(event.pid) !== appPid
        && event.marker_sha256 === markerSha256
        && event.cwd_sha256 === expectedIdentity[role]
        && event.executable_sha256 === pythonExecutableSha256
        && !event.target_sha256
        && !loadedKeys.has(key),
      "guard event loaded identity mismatch",
      { event, expectedIdentity, pythonExecutableSha256 },
    );
    loadedKeys.add(key);
  }
  assertState(
    loaded.filter(event => (
      event.role === "agent" && Number(event.pid) === agentPid
    )).length === 1
      && loaded.filter(event => (
        event.role === "web" && Number(event.pid) === webPid
      )).length === 1,
    "guard event loaded evidence is missing for a service PID",
    { loaded, agentPid, webPid },
  );
  const actualSorted = sortGuardEvents(
    events.filter(event => event.type !== "python_guard_loaded"),
  );
  const expectedSorted = sortGuardEvents(expectedCore);
  assertState(
    JSON.stringify(actualSorted) === JSON.stringify(expectedSorted),
    "guard event whitelist mismatch",
    { expected: expectedSorted, actual: actualSorted, loaded },
  );
}

function collectLiveGuardProcessIdentities(events, {
  baselineTable,
  markerSha256,
  agentDir,
  webuiDir,
  pythonEntryPath,
  pythonExecutableSha256,
}) {
  const result = [];
  const seen = new Set();
  for (const event of events) {
    if (event.type !== "python_guard_loaded") continue;
    const pid = Number(event.pid);
    if (!Number.isInteger(pid) || pid <= 0 || seen.has(pid) || !pidAlive(pid)) {
      continue;
    }
    seen.add(pid);
    const role = String(event.role || "");
    const expectedCwd = role === "agent"
      ? agentDir
      : role === "web"
        ? webuiDir
        : "";
    const identity = processIdentity(pid);
    const commandMatchesRole = role === "agent"
      ? identity
        && identity.command.includes("taiji_runtime.main")
        && identity.command.includes("gateway run")
      : role === "web"
        ? identity && identity.command.includes(path.join(webuiDir, "server.py"))
        : false;
    assertState(
      Boolean(identity)
        && Boolean(expectedCwd)
        && event.marker_sha256 === markerSha256
        && event.cwd_sha256 === sha256Text(expectedCwd)
        && event.executable_sha256 === pythonExecutableSha256
        && identity.cwd === expectedCwd
        && identity.command.includes(pythonEntryPath)
        && commandMatchesRole
        && !isBaselineProcess(baselineTable.get(pid), identity),
      "live guard-loaded process identity is not safe to own",
      {
        event,
        identity,
        expected_cwd: expectedCwd,
        python_entry_path: pythonEntryPath,
      },
    );
    result.push(identity);
  }
  return result;
}

function mergeCleanupEvidence(target, update) {
  for (const key of ["term", "kill", "skipped"]) {
    target[key].push(...(update[key] || []));
  }
}

async function stabilizeProcessCleanup({
  baselineTable,
  harnessRoot,
  initialIdentities,
  rootPids,
  guardLog,
  guardProcessExpected,
  timeoutMs = 8000,
}) {
  const identities = new Map(
    initialIdentities.filter(Boolean).map(identity => [identity.pid, identity]),
  );
  const cleanup = { term: [], kill: [], skipped: [] };
  const snapshots = [];
  const deadline = Date.now() + timeoutMs;
  let consecutiveClean = 0;

  while (Date.now() < deadline) {
    const events = readGuardEvents(guardLog);
    const guarded = collectLiveGuardProcessIdentities(
      events,
      guardProcessExpected,
    );
    for (const identity of guarded) identities.set(identity.pid, identity);

    const table = processTable();
    const descendantPids = descendantsOf(
      [...new Set([...rootPids, ...identities.keys()])],
      table,
    );
    for (const pid of descendantPids) {
      const row = table.get(pid);
      if (isBaselineProcess(baselineTable.get(pid), row)) continue;
      const identity = processIdentity(pid);
      if (identity) identities.set(pid, identity);
    }

    const liveIdentities = [...identities.values()].filter(identity => (
      pidAlive(identity.pid)
    ));
    if (liveIdentities.length > 0) {
      mergeCleanupEvidence(
        cleanup,
        await terminateOwnedProcesses(liveIdentities),
      );
    }

    const afterTable = processTable();
    const liveOwned = [...identities.keys()].filter(pidAlive);
    const liveGuardLoaded = readGuardEvents(guardLog)
      .filter(event => event.type === "python_guard_loaded")
      .map(event => Number(event.pid))
      .filter(pid => Number.isInteger(pid) && pid > 0 && pidAlive(pid));
    const markedDelta = [...afterTable.values()]
      .filter(row => !isBaselineProcess(baselineTable.get(row.pid), row))
      .filter(row => (
        row.command.includes(harnessRoot)
        || identities.has(row.pid)
        || liveGuardLoaded.includes(row.pid)
      ))
      .map(row => row.pid);
    const clean = (
      liveOwned.length === 0
      && liveGuardLoaded.length === 0
      && markedDelta.length === 0
    );
    snapshots.push({
      clean,
      live_owned: liveOwned,
      live_guard_loaded: [...new Set(liveGuardLoaded)],
      marked_delta: markedDelta,
    });
    consecutiveClean = clean ? consecutiveClean + 1 : 0;
    if (consecutiveClean >= 2) {
      return {
        cleanup,
        identities: [...identities.values()],
        final_snapshot: snapshots.at(-1),
        stable_clean_snapshots: 2,
        attempts: snapshots.length,
      };
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`process cleanup did not stabilize\n${JSON.stringify(
    snapshots.slice(-4),
    null,
    2,
  )}`);
}

function waitForCondition(check, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      try {
        const value = check();
        if (value) {
          resolve(value);
          return;
        }
      } catch (error) {
        reject(error);
        return;
      }
      if (Date.now() >= deadline) {
        reject(new Error("timed out waiting for condition"));
        return;
      }
      setTimeout(poll, 100);
    };
    poll();
  });
}

function redactUrl(value) {
  try {
    const parsed = new URL(String(value));
    parsed.username = "";
    parsed.password = "";
    if (parsed.search) parsed.search = "?[REDACTED]";
    if (parsed.hash) parsed.hash = "#[REDACTED]";
    return parsed.toString();
  } catch (_) {
    return "[INVALID_URL]";
  }
}

function redactText(value, sensitiveValues = []) {
  let result = String(value || "");
  for (const sensitive of sensitiveValues.filter(Boolean)) {
    result = result.split(String(sensitive)).join("[REDACTED]");
  }
  result = result.replace(
    /\b(?:sk|key|token|secret)[-_][A-Za-z0-9._:-]{8,}\b/gi,
    "[REDACTED_TOKEN]",
  );
  result = result.replace(
    /(https?:\/\/[^\s?#]+)\?[^\s#]*/gi,
    "$1?[REDACTED]",
  );
  return result;
}

function assertNoSensitiveValues(value, sensitiveValues, label) {
  const text = String(value);
  const leaked = sensitiveValues
    .filter(Boolean)
    .filter(sensitive => text.includes(String(sensitive)));
  assertState(leaked.length === 0, `${label} contains a sensitive canary`, {
    leaked_count: leaked.length,
  });
}

function scanTextArtifacts(root, sensitiveValues) {
  const scanned = [];
  const leaked = [];
  const visit = current => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) {
        visit(absolute);
        continue;
      }
      if (!/\.(?:json|jsonl|log|txt|html|md)$/i.test(entry.name)) continue;
      scanned.push(absolute);
      const text = fs.readFileSync(absolute, "utf8");
      for (const sensitive of sensitiveValues.filter(Boolean)) {
        if (text.includes(String(sensitive))) {
          leaked.push({ file: absolute, value_sha256: sha256Text(sensitive) });
        }
      }
    }
  };
  visit(root);
  assertState(leaked.length === 0, "text evidence contains a sensitive canary", {
    leaked,
  });
  return scanned;
}

function providerFamilyFor(state, capability, providerId) {
  for (const provider of state.providers || []) {
    const capabilities = Array.isArray(provider.capabilities) ? provider.capabilities : [];
    if (!capabilities.includes(capability)) continue;
    const providerIds = provider.provider_ids || {};
    if (String(providerIds[capability] || "") === String(providerId || "")) {
      return String(provider.provider_family || "");
    }
  }
  return "";
}

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(item => canonicalJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function imageCapabilityRequestDigest(payload) {
  const canonicalPayload = {};
  for (const [key, value] of Object.entries(payload || {})) {
    if (key !== "request_id") canonicalPayload[key] = value;
  }
  return sha256Text(canonicalJson(canonicalPayload));
}

function configuredResponse(state, payload, requestLog) {
  const next = clone(state);
  const updates = Array.isArray(payload.credential_updates)
    ? payload.credential_updates
    : [];
  const sanitizedUpdates = updates.map(update => ({
    id: String(update.id || ""),
    provider_family: String(update.provider_family || ""),
    label: String(update.label || ""),
    operation: String(update.operation || ""),
    source_capability: String(update.source_capability || ""),
    source_provider_id: String(update.source_provider_id || ""),
    api_key_present: Boolean(String(update.api_key || "")),
    api_key_length: String(update.api_key || "").length,
  }));
  requestLog.push({
    expected_revision: {
      length: String(payload.expected_revision || "").length,
      sha256: sha256Text(payload.expected_revision || ""),
      valid_64_hex: /^[0-9a-f]{64}$/i.test(
        String(payload.expected_revision || ""),
      ),
    },
    request_id: {
      length: String(payload.request_id || "").length,
      sha256: sha256Text(payload.request_id || ""),
      valid_product_identifier: /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(
        String(payload.request_id || ""),
      ),
      canonical_uuid: /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        String(payload.request_id || ""),
      ),
    },
    capabilities: clone(payload.capabilities || {}),
    credential_updates: sanitizedUpdates,
    verify: clone(payload.verify || []),
  });

  for (const update of updates) {
    const id = String(update.id || "");
    const family = String(update.provider_family || "");
    if (!id || !family || !String(update.api_key || "")) {
      return {
        status: 400,
        body: {
          error: "新增命名凭据需要名称、Provider family 和 API Key。",
          error_code: "invalid_credential_update",
        },
      };
    }
    const existing = next.provider_credentials.find(row => String(row.id) === id);
    const safe = {
      id,
      provider_family: family,
      label: String(update.label || id),
      default: Boolean(update.default),
      configured: true,
      managed_by: "image-capability-center",
    };
    if (existing) Object.assign(existing, safe);
    else next.provider_credentials.push(safe);
  }

  for (const capability of ["vision", "image_generation"]) {
    const draft = payload.capabilities && payload.capabilities[capability];
    if (!draft) continue;
    const expectedFamily = providerFamilyFor(
      next,
      capability,
      String(draft.provider || ""),
    );
    const credentialRef = String(draft.credential_ref || "");
    const credential = credentialRef
      ? next.provider_credentials.find(row => String(row.id) === credentialRef)
      : null;
    if (
      credential
      && expectedFamily
      && String(credential.provider_family || "") !== expectedFamily
    ) {
      return {
        status: 400,
        body: {
          error: "所选凭据不属于当前 Provider，请选择同一平台的凭据或新建凭据。",
          error_code: "credential_family_mismatch",
        },
      };
    }
    next.capabilities[capability] = {
      enabled: Boolean(draft.enabled),
      provider: String(draft.provider || ""),
      model: String(draft.model || ""),
      credential_ref: credentialRef,
      endpoint_values: clone(draft.endpoint_values || {}),
      verification: {
        status: draft.enabled ? "verified" : "disabled",
      },
    };
  }

  next.effective_route = {
    vision: {
      route: next.capabilities.vision.enabled ? "auxiliary_vision" : "disabled",
      provider: next.capabilities.vision.provider,
      model: next.capabilities.vision.model,
    },
    image_generation: {
      route: next.capabilities.image_generation.enabled
        ? "image_generation_provider"
        : "disabled",
      provider: next.capabilities.image_generation.provider,
      model: next.capabilities.image_generation.model,
    },
  };
  next.verification_results = {
    vision: { status: next.capabilities.vision.verification.status },
    image_generation: {
      status: next.capabilities.image_generation.verification.status,
    },
  };
  next.revision = sha256Text(canonicalJson({
    previous_revision: state.revision,
    request_id: String(payload.request_id || ""),
    capabilities: next.capabilities,
    provider_credentials: next.provider_credentials,
    effective_route: next.effective_route,
    verification_results: next.verification_results,
  }));
  return { status: 200, body: next };
}

function initialFixtureState() {
  return {
    ok: true,
    profile: "electron-fixture",
    revision: "a".repeat(64),
    capabilities: {
      vision: {
        enabled: true,
        provider: "alibaba",
        model: "qwen3-vl-plus",
        credential_ref: "alibaba-default",
        endpoint_values: {},
        verification: { status: "configured_unverified" },
      },
      image_generation: {
        enabled: false,
        provider: "dashscope",
        model: "qwen-image-2.0-pro",
        credential_ref: "alibaba-default",
        endpoint_values: {},
        verification: { status: "disabled" },
      },
    },
    providers: [
      {
        provider_family: "alibaba_dashscope",
        label: "阿里云百炼",
        capabilities: ["vision", "image_generation"],
        provider_ids: {
          vision: "alibaba",
          image_generation: "dashscope",
        },
        auth_type: "api_key",
        support_level: "native",
        supports_named_credentials: true,
        selectable: true,
        models: {
          vision: [{ id: "qwen3-vl-plus", label: "Qwen3 VL Plus" }],
          image_generation: [
            { id: "qwen-image-2.0-pro", label: "Qwen Image 2.0 Pro" },
          ],
        },
        default_models: {
          vision: "qwen3-vl-plus",
          image_generation: "qwen-image-2.0-pro",
        },
        credential_fields: {
          vision: [{ name: "api_key", label: "API Key", secret: true }],
          image_generation: [
            { name: "api_key", label: "API Key", secret: true },
          ],
        },
        endpoint_fields: { vision: [], image_generation: [] },
      },
      {
        provider_family: "zhipu",
        label: "智谱 AI",
        capabilities: ["vision", "image_generation"],
        provider_ids: {
          vision: "zai",
          image_generation: "zhipu-image",
        },
        auth_type: "api_key",
        support_level: "native",
        supports_named_credentials: true,
        selectable: true,
        models: {
          vision: [{ id: "glm-5v-turbo", label: "GLM-5V Turbo" }],
          image_generation: [{ id: "glm-image", label: "GLM-Image" }],
        },
        default_models: {
          vision: "glm-5v-turbo",
          image_generation: "glm-image",
        },
        credential_fields: {
          vision: [{ name: "api_key", label: "API Key", secret: true }],
          image_generation: [
            { name: "api_key", label: "API Key", secret: true },
          ],
        },
        endpoint_fields: { vision: [], image_generation: [] },
      },
      {
        provider_family: "doubao",
        label: "火山方舟",
        capabilities: ["image_generation"],
        provider_ids: { image_generation: "doubao" },
        auth_type: "api_key",
        support_level: "native",
        supports_named_credentials: true,
        selectable: true,
        models: {
          image_generation: [
            {
              id: "doubao-seedream-5-0-260128",
              label: "Doubao Seedream",
            },
          ],
        },
        default_models: {
          image_generation: "doubao-seedream-5-0-260128",
        },
        credential_fields: {
          image_generation: [
            { name: "api_key", label: "API Key", secret: true },
          ],
        },
        endpoint_fields: { image_generation: [] },
      },
    ],
    provider_credentials: [
      {
        id: "alibaba-default",
        provider_family: "alibaba_dashscope",
        label: "阿里默认凭据",
        default: true,
        configured: true,
      },
      {
        id: "zhipu-shared",
        provider_family: "zhipu",
        label: "智谱共享凭据",
        default: false,
        configured: true,
      },
      {
        id: "doubao-shared",
        provider_family: "doubao",
        label: "豆包共享凭据",
        default: false,
        configured: true,
      },
    ],
    effective_route: {
      vision: {
        route: "auxiliary_vision",
        provider: "alibaba",
        model: "qwen3-vl-plus",
      },
      image_generation: {
        route: "disabled",
        provider: "dashscope",
        model: "qwen-image-2.0-pro",
      },
    },
  };
}

function createFixtureController(initialState, { requestLog = [] } = {}) {
  let state = clone(initialState);
  const requests = new Map();
  return {
    read() {
      return clone(state);
    },
    replaceState(nextState) {
      state = clone(nextState);
    },
    configure(payload) {
      const requestId = String(payload && payload.request_id || "");
      const digest = imageCapabilityRequestDigest(payload);
      const cached = requests.get(requestId);
      if (cached) {
        if (cached.digest !== digest) {
          return {
            status: 400,
            body: {
              error: "request_id was already used for a different payload",
              error_code: "request_id_conflict",
            },
          };
        }
        return clone(cached.response);
      }

      let response;
      if (String(payload && payload.expected_revision || "").toLowerCase()
          !== String(state.revision || "").toLowerCase()) {
        response = {
          status: 409,
          body: {
            error: "配置已被其他请求更新，请刷新后重试。",
            error_code: "configuration_conflict",
          },
        };
      } else {
        response = configuredResponse(state, payload, requestLog);
        if (response.status === 200) state = clone(response.body);
      }
      requests.set(requestId, { digest, response: clone(response) });
      return clone(response);
    },
  };
}

function sanitizedLaunchEnv(base, dirs, {
  agentDir,
  guard,
  guardMarker,
  labDir,
  repoRoot,
  sourceSnapshot,
  workspace,
}) {
  const env = { ...base };
  for (const key of Object.keys(env)) {
    if (
      key.endsWith("_API_KEY")
      || /(?:^|_)(?:OAUTH|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN|CLIENT_SECRET)$/.test(key)
    ) {
      delete env[key];
    }
  }
  return {
    ...env,
    HOME: dirs.home,
    XDG_CONFIG_HOME: dirs.config,
    XDG_DATA_HOME: dirs.data,
    XDG_STATE_HOME: dirs.state,
    HERMES_HOME: dirs.runtimeHome,
    HERMES_BASE_HOME: dirs.runtimeHome,
    HERMES_WEBUI_STATE_DIR: path.join(dirs.runtimeHome, "web"),
    TAIJI_AGENT_ROOT: labDir,
    TAIJI_AGENT_USE_USER_DIRS: "1",
    TAIJI_DESKTOP_USER_DATA_DIR: dirs.userData,
    TAIJI_RUNTIME_HOME: dirs.runtimeHome,
    TAIJI_WORKSPACE: workspace,
    TAIJI_AGENT_PYTHON: guard.pythonWrapper,
    TAIJI_WEBUI_PYTHON: guard.pythonWrapper,
    HERMES_WEBUI_AGENT_DIR: agentDir,
    TAIJI_SOURCE_ROOT: repoRoot,
    TAIJI_SOURCE_COMMIT: sourceSnapshot.commit,
    TAIJI_SOURCE_DIRTY: sourceSnapshot.status_short ? "1" : "0",
    TAIJI_LICENSE_REQUIRED: "0",
    TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: "0",
    TAIJI_AGENT_SYNC_PACKAGED_CONFIG: "0",
    TAIJI_WEBUI_TEST_NETWORK_BLOCK: "1",
    TAIJI_ELECTRON_GUARD_LOG: guard.guardLog,
    TAIJI_PYTHON_GUARD_LOG: guard.guardLog,
    TAIJI_NETWORK_GUARD_MARKER: guardMarker,
    TAIJI_TRUSTED_OIDC_ORIGINS: "",
    HTTP_PROXY: "http://127.0.0.1:9",
    HTTPS_PROXY: "http://127.0.0.1:9",
    ALL_PROXY: "http://127.0.0.1:9",
    NO_PROXY: "127.0.0.1,localhost",
  };
}

async function waitForDesktopReady(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 120000 });
  try {
    await page.waitForFunction(
      () => (
        document.readyState === "complete"
        && location.href.includes("taiji_desktop=1")
        && typeof switchPanel === "function"
        && typeof switchSettingsSection === "function"
        && typeof window.loadImageCapabilityCenter === "function"
        && Boolean(document.getElementById("imageCapabilityCenter"))
      ),
      null,
      { timeout: 120000 },
    );
  } catch (error) {
    const snapshot = await page.evaluate(() => ({
      url: location.href,
      ready_state: document.readyState,
      switch_panel: typeof switchPanel,
      switch_settings_section: typeof switchSettingsSection,
      image_loader: typeof window.loadImageCapabilityCenter,
      image_center: Boolean(document.getElementById("imageCapabilityCenter")),
      title: document.title,
      body_text: String(document.body?.innerText || "").slice(0, 500),
    })).catch(snapshotError => ({
      snapshot_error: String(snapshotError && snapshotError.message
        ? snapshotError.message
        : snapshotError),
    }));
    throw new Error(
      `desktop settings UI did not become ready\n${JSON.stringify(snapshot, null, 2)}`,
      { cause: error },
    );
  }
  await page.evaluate(() => {
    try {
      localStorage.setItem("hermes-lang", "zh");
    } catch (_) {}
    if (typeof setLanguage === "function") setLanguage("zh");
    const onboarding = document.getElementById("onboardingOverlay");
    if (onboarding) onboarding.remove();
  });
}

async function openModelsByKeyboard(page) {
  const settings = page.locator('.taiji-nav-item[data-taiji-panel="settings"]');
  await settings.waitFor({ state: "visible", timeout: 20000 });
  await settings.focus();
  await page.keyboard.press("Enter");
  const models = page.locator('#settingsMenu [data-settings-section="models"]');
  await models.waitFor({ state: "visible", timeout: 20000 });
  await models.focus();
  await page.keyboard.press("Enter");
  const center = page.locator("#imageCapabilityCenter");
  await center.waitFor({ state: "visible", timeout: 30000 });
  await page.waitForFunction(
    () => document.getElementById("imageCapabilityCenter")?.dataset.state === "ready",
    { timeout: 30000 },
  );
  await center.scrollIntoViewIfNeeded();
}

const EXPECTED_FAMILY_MISMATCH_CONSOLE =
  "Failed to load resource: the server responded with a status of 400 (Bad Request)";
const FAILURE_REDACTION_VALUES = [];

function assertExpectedConsoleErrors(consoleErrors) {
  assertState(
    Array.isArray(consoleErrors)
      && consoleErrors.length === 1
      && consoleErrors[0] === EXPECTED_FAMILY_MISMATCH_CONSOLE,
    "console error whitelist mismatch",
    {
      expected: [EXPECTED_FAMILY_MISMATCH_CONSOLE],
      actual: consoleErrors,
    },
  );
}

function assertTerminalGates({
  consoleErrors,
  guardEvents,
  guardExpected,
  initialSourceSnapshot,
  initialSourceFingerprint,
  finalSourceSnapshot,
  finalSourceFingerprint,
  lateSignals = {},
}) {
  assertExpectedConsoleErrors(consoleErrors);
  assertExactGuardEvents(guardEvents, guardExpected);
  for (const [name, values] of Object.entries(lateSignals)) {
    assertState(
      Array.isArray(values) && values.length === 0,
      `late ${name} evidence appeared before final pass`,
      values,
    );
  }
  assertStableSource(
    initialSourceSnapshot,
    initialSourceFingerprint,
    finalSourceSnapshot,
    finalSourceFingerprint,
  );
}

async function main() {
  let harnessRoot = "";
  let outDir = "";
  let resultFile = "";
  let app = null;
  let result = null;
  let runError = null;
  let cleanupError = null;
  let guard = null;
  let guardProcessExpected = null;
  let pidFiles = null;
  let baselineTable = new Map();
  let sourceSnapshot = null;
  let sourceFingerprint = null;
  let sourceInputs = null;
  let terminalGate = null;
  let finalGuardEvents = [];
  let expectedAgentDir = "";
  let expectedWebuiDir = "";
  let appIdentity = null;
  let agentIdentity = null;
  let webIdentity = null;
  let pageListenerOwnership = null;
  let ownedDescendantIdentities = [];
  const sensitiveValues = [];
  const screenshotSanity = {};

  try {
    const cli = parseArgs(process.argv.slice(2));
    ({ outDir, resultFile } = prepareResultDestination(cli.outDir));
    const { _electron } = loadPlaywright();
    const defaultRepoRoot = path.resolve(__dirname, "..", "..", "..", "..");
    const repoRoot = realpathExisting(
      path.resolve(
        cli.repoRoot
          || process.env.TAIJI_SMOKE_REPO_ROOT
          || defaultRepoRoot,
      ),
      "source repository",
    );
    sourceSnapshot = gitSnapshot(repoRoot);
    const webuiDir = realpathExisting(
      path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui"),
      "WebUI source",
    );
    const agentDir = realpathExisting(
      path.join(repoRoot, "hermes-local-lab", "sources", "hermes-agent"),
      "Agent source",
    );
    expectedAgentDir = agentDir;
    expectedWebuiDir = webuiDir;
    const labDir = realpathExisting(
      path.join(repoRoot, "hermes-local-lab"),
      "runtime source",
    );
    const appDir = realpathExisting(
      path.join(repoRoot, "apps", "taiji-desktop"),
      "desktop source",
    );
    const productMain = realpathExisting(
      path.join(appDir, "src", "main.js"),
      "desktop main entry",
    );
    const productPreload = realpathExisting(
      path.join(appDir, "src", "preload.js"),
      "desktop preload entry",
    );
    sourceInputs = {
      repoRoot,
      webuiDir,
      productMain,
      productPreload,
    };
    sourceFingerprint = collectExecutionSourceFingerprint(sourceInputs);
    assertFormalMainSource(sourceSnapshot, sourceFingerprint);
    const electronResolution = process.env.TAIJI_ELECTRON_BIN
      ? {
        candidates: [process.env.TAIJI_ELECTRON_BIN],
        chosen: realpathExisting(
          process.env.TAIJI_ELECTRON_BIN,
          "Electron binary",
        ),
      }
      : resolveDefaultElectron(repoRoot);
    const electronBin = electronResolution.chosen;
    const pythonCandidate = path.resolve(
      process.env.TAIJI_TEST_PYTHON
        || path.join(agentDir, "venv", "bin", "python"),
    );
    assertState(
      fs.existsSync(pythonCandidate),
      "Python runtime missing",
      { path: pythonCandidate },
    );
    // Preserve the venv entry path when executing. Resolving the symlink here
    // would discard pyvenv.cfg discovery and silently lose project packages.
    const pythonBin = pythonCandidate;
    const pythonBinaryRealpath = fs.realpathSync(pythonCandidate);

    harnessRoot = fs.mkdtempSync(
      path.join(os.tmpdir(), "taiji-image-capability-electron-"),
    );
    const dirs = {
      runtimeHome: path.join(harnessRoot, "runtime"),
      workspace: path.join(harnessRoot, "workspace"),
      userData: path.join(harnessRoot, "user-data"),
      home: path.join(harnessRoot, "home"),
      config: path.join(harnessRoot, "config"),
      data: path.join(harnessRoot, "data"),
      state: path.join(harnessRoot, "state"),
    };
    for (const directory of Object.values(dirs)) {
      fs.mkdirSync(directory, { recursive: true });
    }
    fs.writeFileSync(
      path.join(dirs.workspace, "README.md"),
      "# Isolated Image Capability Electron Fixture\n",
      "utf8",
    );

    const fixtureSecret = `fixture-secret-${crypto.randomBytes(24).toString("hex")}`;
    const guardMarker = `guard-marker-${crypto.randomBytes(24).toString("hex")}`;
    const canary = `external-canary-${crypto.randomBytes(24).toString("hex")}`;
    const canaryUrl = `https://example.invalid/oauth?state=${encodeURIComponent(canary)}`;
    sensitiveValues.push(fixtureSecret, guardMarker, canary, canaryUrl);
    FAILURE_REDACTION_VALUES.push(...sensitiveValues);

    const runtimeConfig = installDailyEquivalentRuntimeConfig(dirs.runtimeHome);
    guard = writeHarnessGuards({
      harnessRoot,
      productMain,
      pythonBin,
      guardMarker,
    });
    baselineTable = processTable();
    guardProcessExpected = {
      baselineTable,
      markerSha256: guard.markerSha256,
      agentDir,
      webuiDir,
      pythonEntryPath: pythonBin,
      pythonExecutableSha256: sha256Text(pythonBinaryRealpath),
    };
    pidFiles = {
      agent: path.join(dirs.state, "taiji-agent", "logs", "agent.pid"),
      web: path.join(dirs.state, "taiji-agent", "logs", "web.pid"),
    };

    const configureRequests = [];
    const fixtureController = createFixtureController(initialFixtureState(), {
      requestLog: configureRequests,
    });
    const apiRequests = [];
    const fixtureResponses = [];
    const routeViolations = [];
    const externalRequests = [];
    const popupUrls = [];
    const pageErrors = [];
    const consoleErrors = [];
    let navigationParity = null;

    app = await _electron.launch({
      executablePath: electronBin,
      args: [guard.electronWrapper],
      env: sanitizedLaunchEnv(process.env, dirs, {
        agentDir,
        guard,
        guardMarker,
        labDir,
        repoRoot,
        sourceSnapshot,
        workspace: dirs.workspace,
      }),
      timeout: 120000,
    });
    const appPid = app.process().pid;
    appIdentity = processIdentity(appPid);
    assertState(Boolean(appIdentity), "could not capture Electron process identity");
    assertState(
      !isBaselineProcess(baselineTable.get(appPid), appIdentity),
      "Electron process identity existed before launch",
      { appIdentity, baseline: baselineTable.get(appPid) || null },
    );
    assertState(
      appIdentity.command.includes(electronBin)
        && appIdentity.command.includes(guard.electronWrapper),
      "Electron command does not identify the guarded product launch",
      appIdentity,
    );

    const context = app.context();
    await context.addInitScript(() => {
      window.__taijiBlockedWindowOpen = [];
      Object.defineProperty(window, "open", {
        configurable: false,
        enumerable: true,
        writable: false,
        value: () => {
          window.__taijiBlockedWindowOpen.push({ blocked: true });
          return null;
        },
      });
    });
    const page = await app.firstWindow({ timeout: 120000 });
    await page.waitForURL(
      value => (
        value.protocol === "http:"
        && value.hostname === "127.0.0.1"
        && /^\d+$/.test(value.port)
        && value.searchParams.get("taiji_desktop") === "1"
      ),
      { timeout: 120000 },
    );
    const pageUrl = new URL(page.url());
    assertState(
      pageUrl.protocol === "http:"
        && pageUrl.hostname === "127.0.0.1"
        && /^\d+$/.test(pageUrl.port)
        && pageUrl.searchParams.get("taiji_desktop") === "1",
      "desktop page URL is not the expected loopback Electron entry",
      { url: redactUrl(page.url()) },
    );
    const pageOrigin = pageUrl.origin;
    const actualPageUrl = redactUrl(page.url());

    page.on("pageerror", error => {
      pageErrors.push(
        redactText(
          String(error && error.message ? error.message : error),
          sensitiveValues,
        ),
      );
    });
    page.on("console", message => {
      if (message.type() !== "error") return;
      consoleErrors.push(
        redactText(message.text(), sensitiveValues),
      );
    });
    page.on("popup", popup => {
      popupUrls.push(redactUrl(popup.url()));
      void popup.close().catch(() => {});
    });
    await context.route("**/*", async route => {
      const request = route.request();
      const url = new URL(request.url());
      const isFixturePath = [
        "/api/image-capabilities",
        "/api/image-capabilities/configure",
      ].includes(url.pathname);
      if (isFixturePath) {
        const requestEvidence = {
          method: request.method(),
          origin: url.origin,
          path: url.pathname,
          query_present: Boolean(url.search),
          content_type: String(request.headers()["content-type"] || ""),
        };
        apiRequests.push(requestEvidence);
        const failRoute = async (message, detail = {}) => {
          routeViolations.push({ message, ...detail, request: requestEvidence });
          await route.fulfill({
            status: 400,
            contentType: "application/json",
            body: JSON.stringify({ error: "invalid acceptance fixture request" }),
          });
        };
        if (
          url.protocol !== "http:"
          || url.hostname !== "127.0.0.1"
          || url.origin !== pageOrigin
          || url.search
          || url.username
          || url.password
        ) {
          await failRoute("fixture endpoint origin or URL mismatch");
          return;
        }
        if (url.pathname === "/api/image-capabilities") {
          if (request.method() !== "GET" || request.postData() !== null) {
            await failRoute("image capability read must be a bodyless GET");
            return;
          }
          fixtureResponses.push({
            operation: "read",
            status: 200,
          });
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(fixtureController.read()),
          });
          return;
        }
        const contentType = String(
          request.headers()["content-type"] || "",
        ).split(";", 1)[0].trim().toLowerCase();
        if (request.method() !== "POST" || contentType !== "application/json") {
          await failRoute("image capability configure must be JSON POST");
          return;
        }
        let payload = null;
        try {
          payload = request.postDataJSON();
        } catch (error) {
          await failRoute("image capability configure body is not valid JSON");
          return;
        }
        const revision = String(payload.expected_revision || "");
        const requestId = String(payload.request_id || "");
        if (!/^[0-9a-f]{64}$/i.test(revision)) {
          await failRoute("expected_revision is not 64 hexadecimal characters");
          return;
        }
        if (
          !/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(requestId)
          || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
            requestId,
          )
        ) {
          await failRoute("request_id is not the canonical UUID emitted by the UI");
          return;
        }
        const response = fixtureController.configure(payload);
        fixtureResponses.push({
          operation: "configure",
          status: response.status,
          error_code: String(response.body.error_code || ""),
        });
        await route.fulfill({
          status: response.status,
          contentType: "application/json",
          body: JSON.stringify(response.body),
        });
        return;
      }
      if (
        ["http:", "https:"].includes(url.protocol)
        && url.hostname !== "127.0.0.1"
        && url.hostname !== "::1"
      ) {
        externalRequests.push({
          method: request.method(),
          url: redactUrl(request.url()),
          url_sha256: sha256Text(request.url()),
          resource_type: request.resourceType(),
        });
        await route.fulfill({
          status: 599,
          contentType: "text/plain",
          body: "External network is disabled in Electron acceptance.",
        });
        return;
      }
      await route.continue();
    });

    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
    await waitForDesktopReady(page);
    const agentPid = await waitForCondition(() => readPid(pidFiles.agent), 30000);
    const webPid = await waitForCondition(() => readPid(pidFiles.web), 30000);
    agentIdentity = processIdentity(agentPid);
    webIdentity = processIdentity(webPid);
    for (const [kind, identity] of [
      ["agent", agentIdentity],
      ["web", webIdentity],
    ]) {
      assertState(
        identity
          && !isBaselineProcess(baselineTable.get(identity.pid), identity),
        `${kind} process identity existed before launch`,
        {
          identity,
          baseline: identity ? baselineTable.get(identity.pid) || null : null,
        },
      );
    }
    assertState(
      Boolean(agentIdentity)
        && agentIdentity.cwd === agentDir
        && agentIdentity.command.includes("taiji_runtime.main")
        && agentIdentity.command.includes("gateway run"),
      "Agent process provenance does not match the selected source",
      agentIdentity,
    );
    assertState(
      Boolean(webIdentity)
        && webIdentity.cwd === webuiDir
        && webIdentity.command.includes(path.join(webuiDir, "server.py")),
      "WebUI process provenance does not match the selected source",
      webIdentity,
    );
    pageListenerOwnership = {
      ...assertTcpListenerOwner(Number(pageUrl.port), webPid),
      cwd: webIdentity.cwd,
      command_sha256: webIdentity.command_sha256,
      identity_sha256: webIdentity.identity_sha256,
    };
    terminalGate = {
      consoleErrors,
      lateSignals: {
        external_requests: externalRequests,
        page_errors: pageErrors,
        popup_urls: popupUrls,
        route_violations: routeViolations,
      },
      guardExpected: {
        appPid,
        agentPid,
        webPid,
        markerSha256: guard.markerSha256,
        publicTargetSha256: sha256Text("203.0.113.1:9"),
        canaryTargetSha256: sha256Text(canaryUrl),
        agentCwdSha256: sha256Text(agentDir),
        webCwdSha256: sha256Text(webuiDir),
        pythonExecutableSha256: sha256Text(pythonBinaryRealpath),
      },
    };

    const guardEventsReady = await waitForCondition(() => {
      const events = readGuardEvents(guard.guardLog);
      const nodeReady = events.some(event => (
        event.pid === appPid
        && event.type === "electron_guard_loaded"
        && event.marker_sha256 === guard.markerSha256
      ));
      const pythonReady = [agentPid, webPid].every(pid => (
        events.some(event => (
          event.pid === pid
          && event.type === "python_guard_loaded"
          && event.marker_sha256 === guard.markerSha256
        ))
        && events.some(event => (
          event.pid === pid
          && event.type === "python_guard_self_test_blocked"
          && event.marker_sha256 === guard.markerSha256
        ))
      ));
      return nodeReady && pythonReady ? events : null;
    }, 30000);
    const mainNetworkProbe = await app.evaluate(
      async ({ app: electronApp }) => electronApp.__taijiGuardProbeMainNetwork(),
    );
    const shellProbe = await app.evaluate(
      async ({ app: electronApp }, target) => (
        electronApp.__taijiGuardProbeShell(target)
      ),
      canaryUrl,
    );
    assertState(
      mainNetworkProbe.blocked
        && mainNetworkProbe.code === "TAIJI_ELECTRON_TEST_NETWORK_BLOCKED",
      "main-process public network guard self-test did not block",
      mainNetworkProbe,
    );
    assertState(
      shellProbe.blocked
        && shellProbe.code === "TAIJI_ELECTRON_TEST_EXTERNAL_OPEN_BLOCKED",
      "Electron shell.openExternal guard self-test did not block",
      shellProbe,
    );
    const rendererOpenProbe = await page.evaluate(target => {
      const result = window.open(target, "_blank", "noopener");
      const anchor = document.createElement("a");
      anchor.href = target;
      anchor.target = "_blank";
      anchor.rel = "noopener";
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      return {
        window_open_returned_null: result === null,
        blocked_call_count: window.__taijiBlockedWindowOpen.length,
      };
    }, canaryUrl);
    assertState(
      rendererOpenProbe.window_open_returned_null
        && rendererOpenProbe.blocked_call_count === 1,
      "renderer window.open was not blocked before the probe",
      rendererOpenProbe,
    );
    const guardEventsWithProbes = await waitForCondition(() => {
      const events = readGuardEvents(guard.guardLog);
      const required = [
        "main_network_blocked",
        "shell_external_blocked",
        "window_open_blocked",
      ];
      return required.every(type => events.some(event => (
        event.pid === appPid
        && event.type === type
        && event.marker_sha256 === guard.markerSha256
      ))) ? events : null;
    }, 10000);

    navigationParity = await inspectTaijiNavigation(page);
    assertNavigationParity(navigationParity);
    assertState(
      JSON.stringify(navigationParity.ui_visibility)
        === JSON.stringify(runtimeConfig.feature_visibility),
      "runtime feature visibility differs from isolated fixture",
      {
        expected: runtimeConfig.feature_visibility,
        actual: navigationParity.ui_visibility,
      },
    );

    await openModelsByKeyboard(page);
    const visionProvider = page.locator("#imageCapabilityVisionProvider");
    const generationProvider = page.locator("#imageCapabilityGenerationProvider");
    await visionProvider.selectOption("zai");
    await generationProvider.selectOption("zhipu-image");

    const visionSecret = page.locator("#imageCapabilityVisionCredentialField0");
    const generationSecret = page.locator(
      "#imageCapabilityGenerationCredentialField0",
    );
    for (const [name, input] of [
      ["vision", visionSecret],
      ["image_generation", generationSecret],
    ]) {
      await input.waitFor({ state: "visible", timeout: 10000 });
      assertState(
        (await input.getAttribute("type")) === "password",
        `${name} secret is not a password input`,
      );
      assertState(
        (await input.getAttribute("data-secret-field")) === "true",
        `${name} secret is not marked as secret`,
      );
      assertState(
        (await input.getAttribute("autocomplete")) === "off",
        `${name} secret autocomplete is not disabled`,
      );
      const labelled = await input.evaluate(node => {
        const label = document.querySelector(`label[for="${node.id}"]`);
        return Boolean(label && label.textContent.trim());
      });
      assertState(
        labelled,
        `${name} secret is not discoverable through a visible label`,
      );
      await input.focus();
      assertState(
        await input.evaluate(node => document.activeElement === node),
        `${name} secret is not focusable`,
      );
    }

    const visionCredential = page.locator("#imageCapabilityVisionCredential");
    const visionOptions = await visionCredential.locator("option").evaluateAll(
      options => options.map(option => ({
        value: option.value,
        text: option.textContent.trim(),
        disabled: option.disabled,
      })),
    );
    assertState(
      visionOptions.some(option => (
        option.value === "zhipu-shared"
        && option.text.includes("智谱共享凭据")
        && !option.disabled
      )),
      "same-family shared credential is not visible/selectable",
      visionOptions,
    );
    assertState(
      !visionOptions.some(option => option.value === "doubao-shared"),
      "mismatched-family shared credential leaked into the visible selector",
      visionOptions,
    );
    await visionCredential.selectOption("zhipu-shared");
    assertState(
      await visionSecret.count() === 0,
      "selecting a shared credential did not remove the new-secret field",
    );
    assertState(
      (await page.locator("#imageCapabilityVisionCredentialFields").innerText())
        .includes("已选择命名凭据"),
      "shared credential selection did not expose an understandable status",
    );

    await generationSecret.focus();
    await page.keyboard.type(fixtureSecret);
    assertState(
      (await generationSecret.inputValue()) === fixtureSecret,
      "keyboard entry did not reach the image-generation password input",
    );
    const generationSwitch = page.locator("#imageCapabilityGenerationEnabled");
    await generationSwitch.focus();
    await page.keyboard.press("Space");
    assertState(
      await generationSwitch.isChecked(),
      "keyboard did not enable image generation",
    );

    screenshotSanity["01-zhipu-named-credential.png"] =
      await captureAuditedScreenshot(
        page,
        outDir,
        "01-zhipu-named-credential.png",
      );

    const save = page.locator("#btnSaveVerifyImageCapabilityCenter");
    await save.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(
      () => (
        document.getElementById("imageCapabilityCenterStatusTitle")
          ?.textContent.includes("图片能力已保存并完成验证")
      ),
      { timeout: 30000 },
    );
    assertState(
      configureRequests.length === 1,
      "happy-path save request count is not one",
      configureRequests,
    );
    const happyRequest = configureRequests[0];
    assertState(
      happyRequest.expected_revision.valid_64_hex
        && happyRequest.expected_revision.length === 64
        && happyRequest.request_id.valid_product_identifier
        && happyRequest.request_id.canonical_uuid,
      "revision/request_id contracts were not preserved by the real UI",
      {
        expected_revision: happyRequest.expected_revision,
        request_id: happyRequest.request_id,
      },
    );
    assertState(
      happyRequest.capabilities.vision.provider === "zai"
        && happyRequest.capabilities.vision.credential_ref === "zhipu-shared",
      "vision did not bind the selected shared credential",
      happyRequest,
    );
    assertState(
      happyRequest.capabilities.image_generation.provider === "zhipu-image",
      "image generation did not bind zhipu-image",
      happyRequest,
    );
    assertState(
      happyRequest.credential_updates.length === 1
        && happyRequest.credential_updates[0].provider_family === "zhipu"
        && happyRequest.credential_updates[0].api_key_present
        && happyRequest.credential_updates[0].api_key_length === fixtureSecret.length,
      "image-generation named credential was not created",
      happyRequest,
    );
    const createdCredentialId = happyRequest.credential_updates[0].id;
    const happyFixtureState = fixtureController.read();
    assertState(
      Boolean(createdCredentialId)
        && happyFixtureState.capabilities.image_generation.credential_ref
          === createdCredentialId,
      "created credential was not persisted in the fixture response",
      { createdCredentialId, fixtureState: happyFixtureState },
    );

    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
    await waitForDesktopReady(page);
    await openModelsByKeyboard(page);
    assertState(
      (await visionProvider.inputValue()) === "zai"
        && (await visionCredential.inputValue()) === "zhipu-shared",
      "shared vision credential binding was not restored after reload",
    );
    assertState(
      (await generationProvider.inputValue()) === "zhipu-image"
        && (await page.locator("#imageCapabilityGenerationCredential").inputValue())
          === createdCredentialId,
      "created image-generation credential binding was not restored after reload",
    );
    assertState(
      await page.locator("#imageCapabilityGenerationCredentialField0").count() === 0,
      "saved secret field was unexpectedly redisplayed after reload",
    );

    const mismatchFixtureState = fixtureController.read();
    mismatchFixtureState.revision = "c".repeat(64);
    mismatchFixtureState.capabilities.image_generation.credential_ref =
      "doubao-shared";
    fixtureController.replaceState(mismatchFixtureState);
    await page.locator("#btnReloadImageCapabilityCenter").click();
    await page.waitForFunction(
      () => (
        document.getElementById("imageCapabilityGenerationCredential")?.value
          === "doubao-shared"
      ),
      { timeout: 10000 },
    );
    const mismatchOption = page.locator(
      '#imageCapabilityGenerationCredential option[value="doubao-shared"]',
    );
    assertState(
      await mismatchOption.isDisabled()
        && (await mismatchOption.innerText()).includes("当前配置"),
      "legacy mismatched binding was not rendered as a disabled current value",
    );
    await save.click();
    await page.waitForFunction(
      () => (
        document.getElementById("imageCapabilityCenterError")
          ?.textContent.includes("所选凭据不属于当前 Provider")
      ),
      { timeout: 10000 },
    );
    assertState(
      (await page.locator("#imageCapabilityCenterStatusTitle").innerText())
        .includes("图片能力保存失败"),
      "family mismatch did not expose an understandable failure title",
    );
    await waitForCondition(() => consoleErrors.length >= 1, 5000);
    const mismatchResponses = fixtureResponses.filter(response => (
      response.operation === "configure"
      && response.status === 400
      && response.error_code === "credential_family_mismatch"
    ));
    assertState(
      mismatchResponses.length === 1,
      "family mismatch did not produce exactly one 400 response",
      { mismatchResponses },
    );
    assertExpectedConsoleErrors(consoleErrors);
    await page.locator("#imageCapabilityCenterError").scrollIntoViewIfNeeded();
    screenshotSanity["02-family-mismatch.png"] =
      await captureAuditedScreenshot(
        page,
        outDir,
        "02-family-mismatch.png",
      );

    await page.setViewportSize({ width: 640, height: 900 });
    await save.scrollIntoViewIfNeeded();
    await save.waitFor({ state: "visible", timeout: 10000 });
    assertState(await save.isEnabled(), "save button is disabled at 640px");
    await save.focus();
    const narrow = await page.evaluate(() => {
      const center = document.getElementById("imageCapabilityCenter");
      const vision = document.querySelector('[data-image-capability="vision"]');
      const generation = document.querySelector(
        '[data-image-capability="image_generation"]',
      );
      const saveButton = document.getElementById(
        "btnSaveVerifyImageCapabilityCenter",
      );
      const centerRect = center.getBoundingClientRect();
      const visionRect = vision.getBoundingClientRect();
      const generationRect = generation.getBoundingClientRect();
      const saveRect = saveButton.getBoundingClientRect();
      return {
        viewport_width: document.documentElement.clientWidth,
        viewport_height: window.innerHeight,
        document_scroll_width: document.documentElement.scrollWidth,
        center_left: centerRect.left,
        center_right: centerRect.right,
        vision_bottom: visionRect.bottom,
        generation_top: generationRect.top,
        save_bounds: {
          left: saveRect.left,
          top: saveRect.top,
          right: saveRect.right,
          bottom: saveRect.bottom,
          width: saveRect.width,
          height: saveRect.height,
        },
        save_visible: Boolean(saveButton.getClientRects().length),
        save_enabled: !saveButton.disabled,
        save_focused: document.activeElement === saveButton,
      };
    });
    assertState(
      narrow.center_left >= -1
        && narrow.center_right <= narrow.viewport_width + 1
        && narrow.generation_top >= narrow.vision_bottom - 4
        && narrow.save_visible
        && narrow.save_enabled
        && narrow.save_focused
        && narrow.save_bounds.left >= 0
        && narrow.save_bounds.top >= 0
        && narrow.save_bounds.right <= narrow.viewport_width
        && narrow.save_bounds.bottom <= narrow.viewport_height
        && narrow.save_bounds.width > 0
        && narrow.save_bounds.height > 0,
      "save button is not visible and operable in the 640px viewport",
      narrow,
    );
    screenshotSanity["03-narrow.png"] =
      await captureAuditedScreenshot(
        page,
        outDir,
        "03-narrow.png",
      );

    assertState(
      routeViolations.length === 0,
      "renderer API fixture received an invalid request",
      routeViolations,
    );
    assertState(
      externalRequests.length === 0,
      "public renderer request reached the BrowserContext guard",
      externalRequests,
    );
    assertState(
      popupUrls.length === 0,
      "Electron opened an unexpected popup or OAuth window",
      popupUrls,
    );
    assertState(
      pageErrors.length === 0,
      "page JavaScript error occurred",
      pageErrors,
    );
    const guardEvents = readGuardEvents(guard.guardLog);
    assertState(
      guardEvents.length >= guardEventsReady.length
        && guardEvents.length >= guardEventsWithProbes.length,
      "network guard evidence regressed during the run",
    );
    const guardEventCounts = {};
    for (const event of guardEvents) {
      const key = `${event.role || "electron"}:${event.type}`;
      guardEventCounts[key] = (guardEventCounts[key] || 0) + 1;
    }

    result = {
      status: "pending_terminal_gates",
      scope: "real Electron desktop/WebUI/DOM with renderer-only image capability API fixture",
      release_gate_boundary: {
        this_script_proves: [
          "real Electron shell and production Settings > Models UI",
          "keyboard-visible named credential creation and reload binding",
          "family mismatch error presentation and narrow viewport operability",
          "pre-product Electron shell/window/main network guards",
          "active Agent and WebUI Python network guards",
        ],
        this_script_does_not_prove: [
          "real image capability backend persistence or encryption",
          "real Provider authentication or generation request",
          "production OAuth completion",
        ],
        backend_companion_tests: [
          "tests/test_model_config_api.py targeted image capability cases",
          "hermes-agent/tests/agent/test_vision_runtime_binding.py",
          "hermes-agent/tests/tools/test_image_generation_plugin_dispatch.py",
        ],
      },
      request_contract: {
        expected_revision: "64 hexadecimal characters",
        request_id: "8-128 product identifier; current renderer emits canonical UUID v4",
        clarification: "request_id is intentionally not forced to 64 characters because the backend and renderer contract is UUID-compatible 8-128 characters",
      },
      provider_network: "blocked; no OAuth, public network, or real Provider verification",
      source_execution: {
        git: sourceSnapshot,
        actual_page_url: actualPageUrl,
        actual_page_origin: pageOrigin,
        source_root_realpath: repoRoot,
        desktop_app_realpath: appDir,
        desktop_main_realpath: productMain,
        desktop_preload_realpath: productPreload,
        electron_binary_realpath: electronBin,
        electron_resolution_candidates: electronResolution.candidates,
        guarded_wrapper_realpath: guard.electronWrapper,
        python_entry_path: pythonBin,
        python_binary_realpath: pythonBinaryRealpath,
        page_listener: pageListenerOwnership,
      },
      acceptance_provenance: buildAcceptanceProvenance({
        sourceFingerprint,
        runtimeConfig,
        navigationParity,
      }),
      source_fingerprint: sourceFingerprint,
      screenshots: screenshotSanity,
      network_isolation: {
        installed_before_product_main: true,
        chromium_proxy: "loopback refusal proxy with loopback bypass",
        main_process_probe: mainNetworkProbe,
        shell_open_external_probe: shellProbe,
        renderer_window_open_probe: rendererOpenProbe,
        python_agent_guard_pid: agentPid,
        python_web_guard_pid: webPid,
        guard_event_counts: guardEventCounts,
        renderer_external_requests: externalRequests,
        popup_urls: popupUrls,
      },
      checks: {
        visible_settings_entry_opened_by_keyboard: true,
        zai_password_visible_labelled_focusable: true,
        zhipu_image_password_visible_labelled_focusable: true,
        shared_credential_selected: "zhipu-shared",
        named_credential_created: createdCredentialId,
        reload_binding_restored: true,
        saved_secret_not_redisplayed: true,
        credential_family_mismatch_rejected: true,
        credential_family_mismatch_status: 400,
        keyboard_secret_entry_and_save: true,
        narrow_layout: narrow,
        api_requests: apiRequests,
        fixture_responses: fixtureResponses,
        configure_requests: configureRequests,
        route_violations: routeViolations,
        page_errors: pageErrors,
        console_errors: consoleErrors,
      },
      process_ownership: {
        baseline_process_count: baselineTable.size,
        electron: appIdentity,
        agent: agentIdentity,
        web: webIdentity,
      },
      canary_scan: {
        result_and_stdout_checked: false,
        evidence_text_files_checked: [],
        sensitive_value_count: sensitiveValues.length,
      },
    };
  } catch (error) {
    runError = error;
  } finally {
    try {
      if (!agentIdentity && pidFiles) {
        const candidate = processIdentity(readPid(pidFiles.agent));
        if (
          candidate
          && !isBaselineProcess(baselineTable.get(candidate.pid), candidate)
          && candidate.cwd === expectedAgentDir
          && candidate.command.includes("taiji_runtime.main")
          && candidate.command.includes("gateway run")
        ) {
          agentIdentity = candidate;
        }
      }
      if (!webIdentity && pidFiles) {
        const candidate = processIdentity(readPid(pidFiles.web));
        if (
          candidate
          && !isBaselineProcess(baselineTable.get(candidate.pid), candidate)
          && candidate.cwd === expectedWebuiDir
          && candidate.command.includes(path.join(expectedWebuiDir, "server.py"))
        ) {
          webIdentity = candidate;
        }
      }
      const rootIdentities = [
        appIdentity,
        agentIdentity,
        webIdentity,
      ].filter(Boolean);
      const rootPids = rootIdentities.map(identity => identity.pid);
      const currentTable = processTable();
      ownedDescendantIdentities = descendantsOf(rootPids, currentTable)
        .filter(pid => (
          !isBaselineProcess(
            baselineTable.get(pid),
            currentTable.get(pid),
          )
        ))
        .map(processIdentity)
        .filter(Boolean);
      let electronCloseTimedOut = false;
      if (app) {
        await Promise.race([
          app.close().catch(() => {}),
          new Promise(resolve => setTimeout(() => {
            electronCloseTimedOut = true;
            resolve();
          }, 5000)),
        ]);
      }
      const afterCloseTable = processTable();
      const afterCloseDescendants = descendantsOf(rootPids, afterCloseTable)
        .filter(pid => (
          !isBaselineProcess(
            baselineTable.get(pid),
            afterCloseTable.get(pid),
          )
        ))
        .map(processIdentity)
        .filter(Boolean);
      const initialCleanupIdentities = [
        ...ownedDescendantIdentities,
        ...afterCloseDescendants,
        ...rootIdentities,
      ];
      const stabilized = guard && guardProcessExpected
        ? await stabilizeProcessCleanup({
          baselineTable,
          harnessRoot,
          initialIdentities: initialCleanupIdentities,
          rootPids,
          guardLog: guard.guardLog,
          guardProcessExpected,
        })
        : {
          cleanup: await terminateOwnedProcesses(initialCleanupIdentities),
          identities: initialCleanupIdentities,
          final_snapshot: {
            live_owned: [],
            live_guard_loaded: [],
            marked_delta: [],
          },
          stable_clean_snapshots: 0,
          attempts: 1,
        };
      const cleanup = stabilized.cleanup;
      const allOwnedIdentities = stabilized.identities;
      ownedDescendantIdentities = allOwnedIdentities.filter(identity => (
        !rootPids.includes(identity.pid)
      ));
      const ownedPids = [...new Set(
        allOwnedIdentities.map(identity => identity.pid),
      )];
      const survivors = stabilized.final_snapshot.live_owned;
      const markedDelta = stabilized.final_snapshot.marked_delta;
      if (guard) {
        finalGuardEvents = readGuardEvents(guard.guardLog);
      }
      if (result) {
        result.process_ownership.descendants_captured_before_close =
          ownedDescendantIdentities;
        result.process_ownership.owned_pids = ownedPids;
        result.process_ownership.owned_pids_alive_after_cleanup = survivors;
        result.process_ownership.baseline_delta_after_cleanup = markedDelta;
        result.cleanup = {
          ...cleanup,
          electron_close_timed_out: electronCloseTimedOut,
          stable_clean_snapshots: stabilized.stable_clean_snapshots,
          stabilization_attempts: stabilized.attempts,
          final_stability_snapshot: stabilized.final_snapshot,
          strategy: "capture before and after close, own strict guard-loaded PIDs, then require two clean bounded snapshots",
          pid_reuse_guard: "pid + start time + command + cwd SHA-256 must match before every signal",
        };
      }
    } catch (error) {
      cleanupError = error;
    }
  }

  if (runError) {
    if (cleanupError) {
      runError.message += `\nCleanup also failed: ${cleanupError.message}`;
    }
    if (harnessRoot && fs.existsSync(harnessRoot)) {
      runError.message += `\nHarness preserved at: ${harnessRoot}`;
    }
    throw runError;
  }
  if (cleanupError) {
    if (harnessRoot && fs.existsSync(harnessRoot)) {
      cleanupError.message += `\nHarness preserved at: ${harnessRoot}`;
    }
    throw cleanupError;
  }
  assertState(result, "Electron acceptance did not produce a result");
  assertState(
    terminalGate && sourceInputs && sourceSnapshot && sourceFingerprint,
    "terminal acceptance gate inputs are incomplete",
  );
  let terminalSourceSnapshot = null;
  let terminalSourceFingerprint = null;
  try {
    terminalSourceSnapshot = gitSnapshot(sourceInputs.repoRoot);
    terminalSourceFingerprint =
      collectExecutionSourceFingerprint(sourceInputs);
    assertTerminalGates({
      consoleErrors: terminalGate.consoleErrors,
      guardEvents: finalGuardEvents,
      guardExpected: terminalGate.guardExpected,
      initialSourceSnapshot: sourceSnapshot,
      initialSourceFingerprint: sourceFingerprint,
      finalSourceSnapshot: terminalSourceSnapshot,
      finalSourceFingerprint: terminalSourceFingerprint,
      lateSignals: terminalGate.lateSignals,
    });
  } catch (error) {
    if (harnessRoot && fs.existsSync(harnessRoot)) {
      error.message += `\nHarness preserved at: ${harnessRoot}`;
    }
    throw error;
  }
  const terminalGuardEventCounts = {};
  for (const event of finalGuardEvents) {
    const key = `${event.role || "electron"}:${event.type}`;
    terminalGuardEventCounts[key] = (terminalGuardEventCounts[key] || 0) + 1;
  }
  result.source_execution.terminal_git = terminalSourceSnapshot;
  result.source_stability = {
    checked_after_process_cleanup: true,
    unchanged: true,
    terminal_fingerprint: terminalSourceFingerprint,
  };
  result.network_isolation.guard_event_counts = terminalGuardEventCounts;
  result.network_isolation.exact_guard_event_whitelist = true;
  result.network_isolation.final_guard_events = finalGuardEvents;
  if (harnessRoot) {
    try {
      fs.rmSync(harnessRoot, { recursive: true, force: true });
    } catch (error) {
      error.message += `\nHarness cleanup failed; preserved at: ${harnessRoot}`;
      throw error;
    }
  }
  assertTerminalGates({
    consoleErrors: terminalGate.consoleErrors,
    guardEvents: finalGuardEvents,
    guardExpected: terminalGate.guardExpected,
    initialSourceSnapshot: sourceSnapshot,
    initialSourceFingerprint: sourceFingerprint,
    finalSourceSnapshot: gitSnapshot(sourceInputs.repoRoot),
    finalSourceFingerprint: collectExecutionSourceFingerprint(sourceInputs),
    lateSignals: terminalGate.lateSignals,
  });
  result.status = "passed";
  result.canary_scan.result_and_stdout_checked = true;
  let serialized = JSON.stringify(result, null, 2);
  assertNoSensitiveValues(serialized, sensitiveValues, "result/stdout");
  result.canary_scan.evidence_text_files_checked = scanTextArtifacts(
    outDir,
    sensitiveValues,
  );
  serialized = JSON.stringify(result, null, 2);
  assertNoSensitiveValues(serialized, sensitiveValues, "final result/stdout");
  const temporaryResultFile = `${resultFile}.tmp-${process.pid}`;
  try {
    fs.writeFileSync(temporaryResultFile, `${serialized}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    fs.renameSync(temporaryResultFile, resultFile);
  } catch (error) {
    fs.rmSync(temporaryResultFile, { force: true });
    throw error;
  }
  process.stdout.write(`${serialized}\n`);
}

module.exports = {
  EXPECTED_FAMILY_MISMATCH_CONSOLE,
  assertExactGuardEvents,
  assertExpectedConsoleErrors,
  assertFormalMainSource,
  assertStableSource,
  assertTcpListenerOwner,
  collectLiveGuardProcessIdentities,
  createFixtureController,
  gitSnapshot,
  initialFixtureState,
  isBaselineProcess,
  prepareResultDestination,
  writeHarnessGuards,
};

if (require.main === module) {
  main().catch(error => {
    const detail = redactText(
      error && error.stack ? error.stack : error,
      FAILURE_REDACTION_VALUES,
    );
    console.error(detail);
    process.exitCode = 1;
  });
}
