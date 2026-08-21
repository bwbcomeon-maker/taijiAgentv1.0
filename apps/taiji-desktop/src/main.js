const { app, BrowserWindow, Menu, shell, dialog, systemPreferences, ipcMain, clipboard } = require("electron");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const { createExternalWindowOpenHandler, normalizeTrustedExternalOrigins } = require("./external-link-policy");
const {
  INSTALLED_PROFILE,
  allowsDevTools,
  applyInstalledRuntimePaths,
  applySecurityProfile,
  requiresSourceGate,
  resolveLaunchProfile,
} = require("./launch-profile");
const {
  buildWindowsRuntimeEnvironment,
  requiredWindowsRuntimeFiles,
  resolveWindowsRuntimeLayout,
  windowsRuntimeCommands,
} = require("./windows-runtime");

const APP_NAME = "太极 Agent";
const DEFAULT_AGENT_PORT = 18642;
const DEFAULT_WEBUI_PORT = 18787;
const DESKTOP_CHROME_BACKGROUND = "#eaf7ff";
const SMOKE_TEST = process.env.TAIJI_DESKTOP_SMOKE_TEST === "1";

let launchProfile = null;
let launchProfileError = null;
try {
  launchProfile = resolveLaunchProfile({
    env: process.env,
    appPath: app.getAppPath(),
  });
} catch (error) {
  launchProfileError = error;
}

let mainWindow = null;
let runtimeEnv = null;
let agentProcess = null;
let webuiProcess = null;
let stopped = false;
const trustedIdentityWindows = new Set();

function configureDesktopUserDataDir() {
  const override = process.env.TAIJI_DESKTOP_USER_DATA_DIR;
  if (override) {
    app.setPath("userData", path.resolve(override));
    return;
  }
  if (process.platform === "win32") {
    const localAppData = path.resolve(String(process.env.LOCALAPPDATA || ""));
    app.setPath("userData", path.join(localAppData, "Taiji Agent", "electron"));
    return;
  }
  if ((launchProfile && launchProfile.kind === INSTALLED_PROFILE) || app.isPackaged) return;

  // Source/debug instances from different worktrees must not compete for
  // Electron's global singleton lock.  Otherwise launching worktree B can
  // silently focus stale worktree A before B validates its own source/runtime.
  const appPath = path.resolve(app.getAppPath());
  const candidateRoot = path.resolve(
    process.env.TAIJI_SOURCE_ROOT || path.join(appPath, "..", "..")
  );
  let physicalRoot = candidateRoot;
  try {
    physicalRoot = fs.realpathSync.native(candidateRoot);
  } catch (_) {
    // The source provenance gate will report an invalid root later.  Keep the
    // singleton namespace deterministic even when the path no longer exists.
  }
  const sourceInstanceId = crypto
    .createHash("sha256")
    .update(physicalRoot)
    .digest("hex")
    .slice(0, 16);
  const dataBase = process.env.XDG_DATA_HOME
    || path.join(systemAccountHome(), ".local", "share");
  const isolatedUserData = path.join(
    dataBase,
    "taiji-agent",
    "source-instances",
    sourceInstanceId,
    "electron-user-data"
  );
  process.env.TAIJI_DESKTOP_USER_DATA_DIR = isolatedUserData;
  app.setPath("userData", isolatedUserData);
}

configureDesktopUserDataDir();
const gotSingleInstanceLock = app.requestSingleInstanceLock();

function desktopBootLog(message) {
  try {
    appendDesktopLog(path.join(userStateDir(), "logs", "taiji-desktop.log"), `[desktop] ${message}`);
  } catch (_) {
    // Logging must never block app startup.
  }
}

function verifyFormalSourceBeforeWindow() {
  if (process.platform === "win32" && app.isPackaged) return;
  if (launchProfileError) throw launchProfileError;
  if (!requiresSourceGate(launchProfile)) return;

  const sourceRoot = path.resolve(process.env.TAIJI_SOURCE_ROOT || path.join(app.getAppPath(), "..", ".."));
  const gitEnv = { ...process.env };
  for (const name of ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"]) {
    delete gitEnv[name];
  }
  const sourceGate = path.join(sourceRoot, "scripts", "check-clean-worktree.sh");
  const result = spawnSync("/bin/bash", [
    sourceGate,
    "--mode", "formal",
    "--dirty-policy", "runtime",
    "--repo-root", sourceRoot,
    "--source-root", sourceRoot,
  ], { encoding: "utf8", env: gitEnv });
  const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
  if (result.status !== 0) throw new Error(output || "formal source check failed");
  if (output) desktopBootLog(`source gate output=${JSON.stringify(output)}`);
  desktopBootLog(`source gate passed root=${JSON.stringify(sourceRoot)}`);
}

if (launchProfile && launchProfile.kind === INSTALLED_PROFILE) {
  desktopBootLog(
    `boot argv=${JSON.stringify(process.argv)} defaultApp=${process.defaultApp ? "1" : "0"} ` +
    `appPath=${app.getAppPath()} lock=${gotSingleInstanceLock ? "1" : "0"} ` +
    `releaseVersion=${JSON.stringify(launchProfile.release.version)} ` +
    `releaseCommit=${JSON.stringify(launchProfile.release.commit)}`
  );
} else {
  desktopBootLog(
    `boot argv=${JSON.stringify(process.argv)} defaultApp=${process.defaultApp ? "1" : "0"} ` +
    `appPath=${app.getAppPath()} lock=${gotSingleInstanceLock ? "1" : "0"} ` +
    `sourceRoot=${JSON.stringify(process.env.TAIJI_SOURCE_ROOT || "unknown")} ` +
    `sourceCommit=${JSON.stringify(process.env.TAIJI_SOURCE_COMMIT || "unknown")} ` +
    `sourceDirty=${JSON.stringify(process.env.TAIJI_SOURCE_DIRTY || "unknown")}`
  );
}

function resolveLabDir() {
  if (process.platform === "win32") {
    return path.join(path.resolve(process.resourcesPath, ".."), "her" + "mes-local-lab");
  }
  if (launchProfile && launchProfile.kind === INSTALLED_PROFILE) {
    return launchProfile.installRoot;
  }
  if (process.env.TAIJI_AGENT_ROOT) {
    return path.resolve(process.env.TAIJI_AGENT_ROOT);
  }

  if (process.resourcesPath) {
    const bundledLab = path.resolve(process.resourcesPath, "..", "..", "..");
    if (fs.existsSync(path.join(bundledLab, "scripts", "start-agent.sh"))) {
      return bundledLab;
    }
  }

  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const sourceLab = path.join(repoRoot, "her" + "mes-local-lab");
  if (fs.existsSync(path.join(sourceLab, "scripts", "start-agent.sh"))) {
    return sourceLab;
  }
  return repoRoot;
}

function systemAccountHome() {
  let accountHome = "";
  try {
    accountHome = String(os.userInfo().homedir || "").trim();
  } catch (_) {
    accountHome = "";
  }
  if (!accountHome || !path.isAbsolute(accountHome)) {
    throw new Error(
      "Taiji Agent could not resolve the current account home from the system account database."
    );
  }
  return path.normalize(accountHome);
}

function userStateDir() {
  if (process.platform === "win32") {
    return path.join(process.env.LOCALAPPDATA, "Taiji Agent", "state");
  }
  const base = process.env.XDG_STATE_HOME || path.join(systemAccountHome(), ".local", "state");
  return path.join(base, "taiji-agent");
}

function userDataDir() {
  if (process.platform === "win32") {
    return path.join(process.env.LOCALAPPDATA, "Taiji Agent");
  }
  const base = process.env.XDG_DATA_HOME || path.join(systemAccountHome(), ".local", "share");
  return path.join(base, "taiji-agent");
}

function htmlEscape(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function statusHtml(title, lines, details = "") {
  const rendered = lines.map((line) => `<li>${htmlEscape(line)}</li>`).join("");
  const detailBlock = details ? `<pre>${htmlEscape(details)}</pre>` : "";
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${htmlEscape(APP_NAME)}</title>
  <style>
    :root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f6f7f9; color: #15171a; }
    main { width: min(680px, calc(100vw - 48px)); }
    h1 { margin: 0 0 14px; font-size: 26px; font-weight: 650; letter-spacing: 0; }
    p { margin: 0 0 18px; color: #4a515c; }
    ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
    li { padding: 12px 14px; border: 1px solid #d9dde4; background: #fff; border-radius: 8px; }
    pre { margin-top: 18px; padding: 14px; max-height: 220px; overflow: auto; border-radius: 8px; background: #171b21; color: #eef3f8; white-space: pre-wrap; }
  </style>
</head>
<body>
  <main>
    <h1>${htmlEscape(title)}</h1>
    <p>应用准备完成后会自动进入对话界面。</p>
    <ul>${rendered}</ul>
    ${detailBlock}
  </main>
</body>
</html>`;
}

function loadStatus(title, lines, details = "") {
  if (!mainWindow) return;
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(statusHtml(title, lines, details))}`);
}

function focusMainWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

function findFreePort(startPort) {
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      const server = net.createServer();
      server.unref();
      server.on("error", () => tryPort(port + 1));
      server.listen({ host: "127.0.0.1", port }, () => {
        const selected = server.address().port;
        server.close(() => resolve(selected));
      });
    };
    if (!Number.isInteger(startPort) || startPort < 1 || startPort > 65535) {
      reject(new Error(`Invalid start port: ${startPort}`));
      return;
    }
    tryPort(startPort);
  });
}

function waitForHttp(url, timeoutMs) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const request = http.get(url, (response) => {
        response.resume();
        if (response.statusCode && response.statusCode >= 200 && response.statusCode < 300) {
          resolve();
          return;
        }
        retry();
      });
      request.setTimeout(1200, () => {
        request.destroy(new Error("timeout"));
      });
      request.on("error", retry);
    };

    const retry = () => {
      if (Date.now() - startedAt > timeoutMs) {
        reject(new Error("Timed out waiting for Taiji Agent to become ready"));
        return;
      }
      setTimeout(tick, 500);
    };

    tick();
  });
}

function appendDesktopLog(logFile, message) {
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  fs.appendFileSync(logFile, `${new Date().toISOString()} ${message}\n`);
}

function resolveIconPath(labDir) {
  const candidates = [
    path.join(labDir, "resources", "icons", "taiji-agent.png"),
    path.join(labDir, "runtime", "web", "static", "favicon-512.png"),
    path.join(labDir, "sources", "her" + "mes-webui", "static", "favicon-512.png")
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

function isAllowedDesktopMediaOrigin(origin) {
  try {
    const url = new URL(String(origin || ""));
    return url.protocol === "http:" && ["127.0.0.1", "localhost", "::1", "[::1]"].includes(url.hostname);
  } catch (_) {
    return false;
  }
}

function isDesktopMicrophonePermission(permission, details = {}) {
  if (permission === "microphone") return true;
  if (permission !== "media") return false;
  const mediaTypes = Array.isArray(details.mediaTypes) ? details.mediaTypes : [];
  return mediaTypes.length === 0 || mediaTypes.includes("audio");
}

async function requestDesktopMicrophoneAccess() {
  if (process.platform !== "darwin" || !systemPreferences) return true;
  try {
    const status = systemPreferences.getMediaAccessStatus
      ? systemPreferences.getMediaAccessStatus("microphone")
      : "unknown";
    if (status === "granted") return true;
    if (status === "denied" || status === "restricted") return false;
    if (typeof systemPreferences.askForMediaAccess === "function") {
      return await systemPreferences.askForMediaAccess("microphone");
    }
  } catch (_) {
    return true;
  }
  return true;
}

function installDesktopPermissionHandlers(win) {
  if (!win || !win.webContents || !win.webContents.session) return;
  const ses = win.webContents.session;
  ses.setPermissionRequestHandler((webContents, permission, callback, details = {}) => {
    const origin = details.securityOrigin || details.requestingUrl || webContents.getURL();
    if (!isDesktopMicrophonePermission(permission, details) || !isAllowedDesktopMediaOrigin(origin)) {
      callback(false);
      return;
    }
    requestDesktopMicrophoneAccess()
      .then((granted) => callback(!!granted))
      .catch(() => callback(false));
  });
  ses.setPermissionCheckHandler((webContents, permission, requestingOrigin, details = {}) => {
    const origin = requestingOrigin || details.securityOrigin || webContents.getURL();
    return isDesktopMicrophonePermission(permission, details) && isAllowedDesktopMediaOrigin(origin);
  });
}

function installDesktopIpcHandlers() {
  ipcMain.handle("taiji:pick-directory", async (event) => {
    const senderUrl = event.senderFrame && event.senderFrame.url
      ? event.senderFrame.url
      : event.sender.getURL();
    if (!isAllowedDesktopMediaOrigin(senderUrl)) {
      return { ok: false, error: "unauthorized origin" };
    }
    const owner = BrowserWindow.fromWebContents(event.sender) || mainWindow;
    const result = await dialog.showOpenDialog(owner, {
      title: "选择授权目录",
      properties: ["openDirectory", "createDirectory"]
    });
    if (result.canceled || !result.filePaths || !result.filePaths.length) {
      return { ok: false, canceled: true };
    }
    return { ok: true, path: result.filePaths[0] };
  });

  ipcMain.handle("taiji:read-clipboard-text", async (event) => {
    const senderUrl = event.senderFrame && event.senderFrame.url
      ? event.senderFrame.url
      : event.sender.getURL();
    if (!isAllowedDesktopMediaOrigin(senderUrl)) {
      return { ok: false, error: "unauthorized origin" };
    }
    return { ok: true, text: clipboard.readText() || "" };
  });
}

async function stopExistingRuntime(labDir, logDir) {
  if (process.platform === "win32") {
    stopWindowsProcesses();
    return;
  }
  const stopScript = path.join(labDir, "scripts", "stop-all.sh");
  if (!fs.existsSync(stopScript)) return;
  const desktopLog = path.join(logDir, "taiji-desktop.log");
  const env = {
    ...process.env,
    TAIJI_AGENT_ROOT: labDir,
    TAIJI_AGENT_USE_USER_DIRS: "1",
    TAIJI_AGENT_LOG_DIR: logDir
  };
  delete env.ELECTRON_RUN_AS_NODE;
  if (launchProfile.kind === INSTALLED_PROFILE) {
    delete env.TAIJI_SOURCE_ROOT;
    delete env.TAIJI_SOURCE_COMMIT;
    delete env.TAIJI_SOURCE_DIRTY;
    delete env.TAIJI_SOURCE_MODE;
    env.TAIJI_LAUNCH_PROFILE = INSTALLED_PROFILE;
    env.TAIJI_RELEASE_VERSION = launchProfile.release.version;
    env.TAIJI_RELEASE_COMMIT = launchProfile.release.commit;
    applyInstalledRuntimePaths({ launchProfile, runtimeEnv: env });
    applySecurityProfile({
      launchProfile,
      runtimeEnv: env,
      sourceEnv: process.env,
      packaged: app.isPackaged,
    });
  }
  appendDesktopLog(desktopLog, "stopping stale desktop runtime");
  const result = spawnSync(stopScript, {
    cwd: labDir,
    env,
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 12000
  });
  if (result.stdout) appendDesktopLog(desktopLog, `[stop-all.sh] ${result.stdout.trimEnd()}`);
  if (result.stderr) appendDesktopLog(desktopLog, `[stop-all.sh error] ${result.stderr.trimEnd()}`);
  if (result.error) appendDesktopLog(desktopLog, `[stop-all.sh error] ${result.error.message}`);
}

function runScript(scriptName, env, logFile) {
  const script = path.join(env.TAIJI_AGENT_ROOT, "scripts", scriptName);
  return new Promise((resolve, reject) => {
    const outputTail = [];
    const rememberOutput = (prefix, chunk) => {
      const text = chunk.toString().trimEnd();
      if (!text) return;
      appendDesktopLog(logFile, `${prefix} ${text}`);
      for (const line of text.split(/\r?\n/)) {
        if (line.trim()) outputTail.push(`${prefix} ${line}`);
      }
      if (outputTail.length > 24) {
        outputTail.splice(0, outputTail.length - 24);
      }
    };
    appendDesktopLog(logFile, `starting ${scriptName}`);
    const child = spawn(script, {
      cwd: env.TAIJI_AGENT_ROOT,
      env,
      stdio: ["ignore", "pipe", "pipe"]
    });

    child.stdout.on("data", (chunk) => rememberOutput(`[${scriptName}]`, chunk));
    child.stderr.on("data", (chunk) => rememberOutput(`[${scriptName} error]`, chunk));
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        const detail = outputTail.length ? `\n\n最近输出：\n${outputTail.join("\n")}` : "";
        reject(new Error(`${scriptName} exited with code ${code}${detail}`));
      }
    });
  });
}

function createRuntimeEnv(labDir, agentPort, webuiPort, logDir) {
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const accountHome = systemAccountHome();
  const desktopAccessToken = crypto.randomBytes(32).toString("hex");
  env.TAIJI_AGENT_ROOT = labDir;
  if (launchProfile.kind === INSTALLED_PROFILE) {
    delete env.TAIJI_SOURCE_ROOT;
    delete env.TAIJI_SOURCE_COMMIT;
    delete env.TAIJI_SOURCE_DIRTY;
    delete env.TAIJI_SOURCE_MODE;
    env.TAIJI_LAUNCH_PROFILE = INSTALLED_PROFILE;
    env.TAIJI_RELEASE_VERSION = launchProfile.release.version;
    env.TAIJI_RELEASE_COMMIT = launchProfile.release.commit;
    applyInstalledRuntimePaths({ launchProfile, runtimeEnv: env });
  } else {
    env.TAIJI_SOURCE_ROOT = process.env.TAIJI_SOURCE_ROOT || path.resolve(labDir, "..");
    env.TAIJI_SOURCE_COMMIT = process.env.TAIJI_SOURCE_COMMIT || "unknown";
    env.TAIJI_SOURCE_DIRTY = process.env.TAIJI_SOURCE_DIRTY || "unknown";
  }
  env.TAIJI_AGENT_USE_USER_DIRS = "1";
  env.TAIJI_RUNTIME_HOME = process.env.TAIJI_RUNTIME_HOME || path.join(userDataDir(), "runtime-home");
  env.TAIJI_WORKSPACE = process.env.TAIJI_WORKSPACE || path.join(userDataDir(), "workspace");
  env.TAIJI_AGENT_LOG_DIR = logDir;
  applySecurityProfile({
    launchProfile,
    runtimeEnv: env,
    sourceEnv: process.env,
    packaged: app.isPackaged,
  });
  env.AGENT_API_HOST = "127.0.0.1";
  env.AGENT_API_PORT = String(agentPort);
  env.API_SERVER_HOST = "127.0.0.1";
  env.API_SERVER_PORT = String(agentPort);
  env.WEBUI_HOST = "127.0.0.1";
  env.WEBUI_PORT = String(webuiPort);
  env.TAIJI_WEBUI_HOST = "127.0.0.1";
  env.TAIJI_WEBUI_PORT = String(webuiPort);
  env.TAIJI_DESKTOP_ONLY = "1";
  env.TAIJI_DESKTOP_ACCESS_TOKEN = desktopAccessToken;
  env.API_SERVER_KEY = crypto.randomBytes(32).toString("hex");
  env.TAIJI_WEBUI_GATEWAY_BASE_URL = `http://127.0.0.1:${agentPort}`;
  const stateDir = process.env.TAIJI_STATE_DIR || userStateDir();
  const tmpDir = process.env.TAIJI_AGENT_TMP_DIR || path.join(stateDir, "tmp");
  env.TAIJI_ACCOUNT_HOME = accountHome;
  env.TAIJI_LICENSE_FILE = path.join(accountHome, ".config", "taiji-agent", "licenses", "active-license.jwt");
  env.TAIJI_STATE_DIR = stateDir;
  env.TAIJI_AGENT_TMP_DIR = tmpDir;
  env.TMPDIR = tmpDir;
  env.TMP = tmpDir;
  env.TEMP = tmpDir;
  env.TAIJI_LICENSE_STATE_FILE = path.join(accountHome, ".local", "state", "taiji-agent", "license-state.json");
  if (process.platform === "win32") {
    const layout = resolveWindowsRuntimeLayout({
      installRoot: path.resolve(labDir, ".."),
      localAppData: process.env.LOCALAPPDATA,
    });
    for (const directory of [
      layout.electronDir,
      layout.runtimeHome,
      layout.workspace,
      layout.stateDir,
      layout.logDir,
      layout.tmpDir,
      layout.licenseDir,
      layout.webuiStateDir,
    ]) {
      fs.mkdirSync(directory, { recursive: true });
    }
    return buildWindowsRuntimeEnvironment({
      baseEnv: env,
      layout,
      agentPort,
      webuiPort,
      desktopAccessToken,
      apiServerKey: env.API_SERVER_KEY,
    });
  }
  try {
    fs.mkdirSync(tmpDir, { recursive: true });
  } catch (error) {
    desktopBootLog(`failed to create tmp dir ${tmpDir}: ${error.message}`);
  }
  return env;
}

function stopWindowsProcesses() {
  const taskkill = path.join(process.env.SystemRoot, "System32", "taskkill.exe");
  for (const child of [webuiProcess, agentProcess]) {
    if (!child || child.exitCode !== null || !child.pid) continue;
    spawnSync(taskkill, ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      timeout: 12000,
      windowsHide: true,
      shell: false,
    });
  }
  webuiProcess = null;
  agentProcess = null;
}

function stopRuntime() {
  if (stopped || !runtimeEnv) return;
  stopped = true;
  if (process.platform === "win32") {
    stopWindowsProcesses();
    return;
  }
  const stopScript = path.join(runtimeEnv.TAIJI_AGENT_ROOT, "scripts", "stop-all.sh");
  spawnSync(stopScript, {
    cwd: runtimeEnv.TAIJI_AGENT_ROOT,
    env: runtimeEnv,
    stdio: "ignore",
    timeout: 12000
  });
}

async function startRuntime() {
  desktopBootLog("startRuntime");
  const labDir = resolveLabDir();
  const declaredSourceRoot = launchProfile.kind === "source"
    ? String(process.env.TAIJI_SOURCE_ROOT || "").trim()
    : "";
  if (declaredSourceRoot) {
    const expectedLabDir = fs.realpathSync(path.join(declaredSourceRoot, "her" + "mes-local-lab"));
    const actualLabDir = fs.realpathSync(labDir);
    if (actualLabDir !== expectedLabDir) {
      throw new Error(
        `Launcher source mismatch: declared ${expectedLabDir}, resolved ${actualLabDir}`
      );
    }
  }
  const logDir = path.join(userStateDir(), "logs");
  const desktopLog = path.join(logDir, "taiji-desktop.log");
  const iconPath = resolveIconPath(labDir);

  if (process.platform !== "win32" && !fs.existsSync(path.join(labDir, "scripts", "start-agent.sh"))) {
    throw new Error(`Runtime scripts not found under ${labDir}`);
  }
  let windowsLayout = null;
  if (process.platform === "win32") {
    windowsLayout = resolveWindowsRuntimeLayout({
      installRoot: path.resolve(labDir, ".."),
      localAppData: process.env.LOCALAPPDATA,
    });
    const missing = requiredWindowsRuntimeFiles(windowsLayout).filter((file) => !fs.existsSync(file));
    if (missing.length) {
      throw new Error(`Windows runtime files missing:\n${missing.join("\n")}`);
    }
  }
  let webuiPort;

  try {
    loadStatus("正在启动太极 Agent", [
    "正在准备本机运行环境",
    "正在检查应用状态",
    "如遇异常可运行 taiji-agent-diagnose 导出诊断"
  ]);

  await stopExistingRuntime(labDir, logDir);
  const agentPort = await findFreePort(DEFAULT_AGENT_PORT);
  webuiPort = await findFreePort(DEFAULT_WEBUI_PORT);
  runtimeEnv = createRuntimeEnv(labDir, agentPort, webuiPort, logDir);
  stopped = false;

  loadStatus("正在启动太极 Agent", [
    "正在启动对话能力",
    "正在准备工作台界面",
    "如遇异常可运行 taiji-agent-diagnose 导出诊断"
  ]);
  if (process.platform === "win32") {
    const commands = windowsRuntimeCommands(windowsLayout);
    const startWindowsProcess = (command, label) => {
      appendDesktopLog(desktopLog, `starting ${label}`);
      const child = spawn(command.file, command.args, {
        cwd: command.cwd,
        env: runtimeEnv,
        windowsHide: true,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
      });
      child.stdout.on("data", (chunk) => appendDesktopLog(desktopLog, `[${label}] ${chunk.toString().trimEnd()}`));
      child.stderr.on("data", (chunk) => appendDesktopLog(desktopLog, `[${label} error] ${chunk.toString().trimEnd()}`));
      child.on("error", (error) => appendDesktopLog(desktopLog, `[${label} spawn error] ${error.message}`));
      child.on("exit", (code, signal) => {
        if (!stopped) appendDesktopLog(desktopLog, `[${label} unexpected exit] code=${code} signal=${signal}`);
      });
      return child;
    };
    agentProcess = startWindowsProcess(commands.agent, "agent");
  } else {
    await runScript("start-agent.sh", runtimeEnv, desktopLog);
  }
  await waitForHttp(`http://127.0.0.1:${agentPort}/health`, 30000);

  loadStatus("正在启动太极 Agent", [
    "对话能力已就绪",
    "正在打开工作台界面",
    "如遇异常可运行 taiji-agent-diagnose 导出诊断"
  ]);
  if (process.platform === "win32") {
    const commands = windowsRuntimeCommands(windowsLayout);
    webuiProcess = (() => {
      appendDesktopLog(desktopLog, "starting webui");
      const child = spawn(commands.webui.file, commands.webui.args, {
        cwd: commands.webui.cwd,
        env: runtimeEnv,
        windowsHide: true,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
      });
      child.stdout.on("data", (chunk) => appendDesktopLog(desktopLog, `[webui] ${chunk.toString().trimEnd()}`));
      child.stderr.on("data", (chunk) => appendDesktopLog(desktopLog, `[webui error] ${chunk.toString().trimEnd()}`));
      child.on("error", (error) => appendDesktopLog(desktopLog, `[webui spawn error] ${error.message}`));
      child.on("exit", (code, signal) => {
        if (!stopped) appendDesktopLog(desktopLog, `[webui unexpected exit] code=${code} signal=${signal}`);
      });
      return child;
    })();
  } else {
    await runScript("start-webui.sh", runtimeEnv, desktopLog);
  }
    await waitForHttp(`http://127.0.0.1:${webuiPort}/health`, 30000);
  } catch (error) {
    if (process.platform === "win32") stopWindowsProcesses();
    throw error;
  }

  const target = new URL(`http://127.0.0.1:${webuiPort}`);
  target.searchParams.set("taiji_desktop", "1");
  await mainWindow.webContents.session.cookies.set({
    url: target.origin,
    name: "taiji_desktop_token",
    value: runtimeEnv.TAIJI_DESKTOP_ACCESS_TOKEN || "",
    path: "/",
    httpOnly: true,
    sameSite: "strict"
  });
  appendDesktopLog(desktopLog, "loading desktop workspace");
  if (iconPath) {
    mainWindow.setIcon(iconPath);
  }
  await mainWindow.loadURL(target.toString());
}

function installMenu() {
  if (process.platform === "linux" && process.env.TAIJI_DESKTOP_SHOW_MENU !== "1") {
    Menu.setApplicationMenu(null);
    return;
  }

  const viewItems = [{ role: "reload", label: "重新加载" }];
  if (allowsDevTools(launchProfile)) {
    viewItems.push({ role: "toggleDevTools", label: "开发者工具" });
  }
  const template = [
    {
      label: APP_NAME,
      submenu: [
        {
          label: "打开日志目录",
          click: () => shell.openPath(path.join(userStateDir(), "logs"))
        },
        { type: "separator" },
        {
          label: "退出",
          accelerator: "CmdOrCtrl+Q",
          click: () => app.quit()
        }
      ]
    },
    {
      label: "编辑",
      submenu: [
        { role: "undo", label: "撤销" },
        { role: "redo", label: "重做" },
        { type: "separator" },
        { role: "cut", label: "剪切" },
        { role: "copy", label: "复制" },
        { role: "paste", label: "粘贴" },
        { role: "pasteAndMatchStyle", label: "粘贴并匹配样式" },
        { type: "separator" },
        { role: "selectAll", label: "全选" }
      ]
    },
    {
      label: "视图",
      submenu: viewItems
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function openTrustedIdentityWindow(url, allowedOrigins) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return Promise.reject(new Error("main window is unavailable"));
  }
  const authIconPath = resolveIconPath(resolveLabDir());
  const authWindow = new BrowserWindow({
    parent: mainWindow,
    width: 620,
    height: 760,
    minWidth: 480,
    minHeight: 640,
    show: true,
    title: "企业身份安全登录",
    icon: authIconPath || undefined,
    autoHideMenuBar: true,
    webPreferences: {
      session: mainWindow.webContents.session,
      contextIsolation: true,
      devTools: allowsDevTools(launchProfile),
      nodeIntegration: false,
      sandbox: true
    }
  });
  trustedIdentityWindows.add(authWindow);
  authWindow.on("closed", () => trustedIdentityWindows.delete(authWindow));
  authWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  authWindow.webContents.on("will-navigate", (event, target) => {
    let callback = false;
    let trustedProvider = false;
    try {
      const parsed = new URL(String(target || ""));
      const mainOrigin = new URL(mainWindow.webContents.getURL()).origin;
      callback = parsed.origin === mainOrigin && parsed.pathname === "/api/expert-teams/identity/callback";
      trustedProvider = allowedOrigins.includes(parsed.origin);
    } catch (_) {
      callback = false;
    }
    if (!callback && !trustedProvider) event.preventDefault();
  });
  authWindow.webContents.on("did-navigate", (_event, target) => {
    try {
      const parsed = new URL(String(target || ""));
      const mainOrigin = new URL(mainWindow.webContents.getURL()).origin;
      if (parsed.origin === mainOrigin && parsed.pathname === "/api/expert-teams/identity/callback") {
        setTimeout(() => { if (!authWindow.isDestroyed()) authWindow.close(); }, 1200);
      }
    } catch (_) {
      // Keep the auth window open on an unparseable navigation so the user can close it explicitly.
    }
  });
  return authWindow.loadURL(String(url));
}

async function createWindow() {
  desktopBootLog("createWindow");
  const labDir = resolveLabDir();
  const iconPath = resolveIconPath(labDir);
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    show: !SMOKE_TEST,
    title: APP_NAME,
    icon: iconPath || undefined,
    backgroundColor: DESKTOP_CHROME_BACKGROUND,
    autoHideMenuBar: process.platform === "linux",
    ...(process.platform === "darwin" ? {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: { x: 16, y: 16 }
    } : {}),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      devTools: allowsDevTools(launchProfile),
      nodeIntegration: false,
      sandbox: true
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  const trustedOidcOrigins = normalizeTrustedExternalOrigins(process.env.TAIJI_TRUSTED_OIDC_ORIGINS || "", { allowLocalHttp: launchProfile.kind === "source" && launchProfile.mode === "development" });
  mainWindow.webContents.setWindowOpenHandler(createExternalWindowOpenHandler(
    (url) => openTrustedIdentityWindow(url, trustedOidcOrigins),
    (error) => desktopBootLog(`external URL open failed: ${error && error.message ? error.message : String(error)}`),
    trustedOidcOrigins
  ));
  installDesktopPermissionHandlers(mainWindow);

  loadStatus("正在准备太极 Agent", ["初始化桌面窗口", "准备本机运行环境"]);

  try {
    await startRuntime();
    if (SMOKE_TEST) {
      setTimeout(() => app.quit(), 800);
    }
  } catch (error) {
    const message = error && error.stack ? error.stack : String(error);
    loadStatus("启动失败", [
      "应用未能启动",
      "请运行 taiji-agent-diagnose 导出技术诊断信息"
    ], message);
    if (SMOKE_TEST) {
      console.error(message);
      app.exit(1);
      return;
    }
    dialog.showErrorBox("太极 Agent 启动失败", message);
  }
}

if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", focusMainWindow);

  app.whenReady().then(() => {
    desktopBootLog("app.whenReady");
    app.setName("taiji-agent");
    if (process.platform === "linux") {
      app.setDesktopName("taiji-agent.desktop");
    }
    try {
      verifyFormalSourceBeforeWindow();
    } catch (error) {
      const message = `源码状态校验未通过：${error.message}`;
      desktopBootLog(message);
      dialog.showErrorBox("太极 Agent 启动失败", message);
      app.quit();
      return;
    }
    installMenu();
    installDesktopIpcHandlers();
    createWindow();
  });

  app.on("before-quit", stopRuntime);

  app.on("window-all-closed", () => {
    app.quit();
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    } else {
      focusMainWindow();
    }
  });
}
