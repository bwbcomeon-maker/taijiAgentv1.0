const { app, BrowserWindow, Menu, shell, dialog, systemPreferences, ipcMain, clipboard } = require("electron");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const net = require("net");
const os = require("os");
const path = require("path");
const { spawn, spawnSync } = require("child_process");

const APP_NAME = "太极 Agent";
const DEFAULT_AGENT_PORT = 18642;
const DEFAULT_WEBUI_PORT = 18787;
const DESKTOP_CHROME_BACKGROUND = "#eaf7ff";
const SMOKE_TEST = process.env.TAIJI_DESKTOP_SMOKE_TEST === "1";

let mainWindow = null;
let runtimeEnv = null;
let stopped = false;

const SECURITY_ALLOW_FLAGS = [
  "TAIJI_ALLOW_TERMINAL",
  "TAIJI_ALLOW_EXECUTE_CODE",
  "TAIJI_ALLOW_DELEGATE_TASK",
  "TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS"
];

function hasExplicitEnv(name) {
  return Object.prototype.hasOwnProperty.call(process.env, name);
}

function securityProfileDefaults(profileName) {
  if (profileName === "full") {
    return { name: "full", mode: "full", allow: true };
  }
  if (profileName === "local_controlled") {
    return { name: "local_controlled", mode: "restricted", allow: true };
  }
  return { name: "strict", mode: "restricted", allow: false };
}

function resolveSecurityProfile() {
  const explicit = String(process.env.TAIJI_SECURITY_PROFILE || "").trim();
  if (["strict", "local_controlled", "full"].includes(explicit)) {
    return securityProfileDefaults(explicit);
  }
  return securityProfileDefaults(app.isPackaged ? "strict" : "local_controlled");
}

function applySecurityProfile(env) {
  const profile = resolveSecurityProfile();
  env.TAIJI_SECURITY_PROFILE = process.env.TAIJI_SECURITY_PROFILE || profile.name;
  env.TAIJI_SECURITY_MODE = process.env.TAIJI_SECURITY_MODE || profile.mode;
  if (profile.name === "local_controlled" || profile.name === "full") {
    for (const flag of SECURITY_ALLOW_FLAGS) {
      if (!hasExplicitEnv(flag)) {
        env[flag] = "1";
      }
    }
  }
  return profile;
}

function configureDesktopUserDataDir() {
  const override = process.env.TAIJI_DESKTOP_USER_DATA_DIR;
  if (!override) return;
  app.setPath("userData", path.resolve(override));
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

desktopBootLog(`boot argv=${JSON.stringify(process.argv)} defaultApp=${process.defaultApp ? "1" : "0"} appPath=${app.getAppPath()} lock=${gotSingleInstanceLock ? "1" : "0"}`);

function resolveLabDir() {
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

function userStateDir() {
  const base = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state");
  return path.join(base, "taiji-agent");
}

function userDataDir() {
  const base = process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
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
  const desktopAccessToken = crypto.randomBytes(32).toString("hex");
  env.TAIJI_AGENT_ROOT = labDir;
  env.TAIJI_AGENT_USE_USER_DIRS = "1";
  env.TAIJI_RUNTIME_HOME = process.env.TAIJI_RUNTIME_HOME || path.join(userDataDir(), "runtime-home");
  env.TAIJI_WORKSPACE = process.env.TAIJI_WORKSPACE || path.join(userDataDir(), "workspace");
  env.TAIJI_AGENT_LOG_DIR = logDir;
  applySecurityProfile(env);
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
  const configHome = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  const stateDir = process.env.TAIJI_STATE_DIR || userStateDir();
  const tmpDir = process.env.TAIJI_AGENT_TMP_DIR || path.join(stateDir, "tmp");
  env.TAIJI_LICENSE_FILE = process.env.TAIJI_LICENSE_FILE || path.join(configHome, "taiji-agent", "license.jwt");
  env.TAIJI_STATE_DIR = stateDir;
  env.TAIJI_AGENT_TMP_DIR = tmpDir;
  env.TMPDIR = tmpDir;
  env.TMP = tmpDir;
  env.TEMP = tmpDir;
  env.TAIJI_LICENSE_STATE_FILE = process.env.TAIJI_LICENSE_STATE_FILE || path.join(stateDir, "license-state.json");
  env.TAIJI_LICENSE_REQUIRED = process.env.TAIJI_LICENSE_REQUIRED || "1";
  env.TAIJI_LICENSE_MACHINE_BINDING_REQUIRED = process.env.TAIJI_LICENSE_MACHINE_BINDING_REQUIRED || "1";
  try {
    fs.mkdirSync(tmpDir, { recursive: true });
  } catch (error) {
    desktopBootLog(`failed to create tmp dir ${tmpDir}: ${error.message}`);
  }
  return env;
}

function stopRuntime() {
  if (stopped || !runtimeEnv) return;
  stopped = true;
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
  const logDir = path.join(userStateDir(), "logs");
  const desktopLog = path.join(logDir, "taiji-desktop.log");
  const iconPath = resolveIconPath(labDir);

  if (!fs.existsSync(path.join(labDir, "scripts", "start-agent.sh"))) {
    throw new Error(`Runtime scripts not found under ${labDir}`);
  }

  loadStatus("正在启动太极 Agent", [
    "正在准备本机运行环境",
    "正在检查应用状态",
    "如遇异常可运行 taiji-agent-diagnose 导出诊断"
  ]);

  await stopExistingRuntime(labDir, logDir);
  const agentPort = await findFreePort(DEFAULT_AGENT_PORT);
  const webuiPort = await findFreePort(DEFAULT_WEBUI_PORT);
  runtimeEnv = createRuntimeEnv(labDir, agentPort, webuiPort, logDir);

  loadStatus("正在启动太极 Agent", [
    "正在启动对话能力",
    "正在准备工作台界面",
    "如遇异常可运行 taiji-agent-diagnose 导出诊断"
  ]);
  await runScript("start-agent.sh", runtimeEnv, desktopLog);
  await waitForHttp(`http://127.0.0.1:${agentPort}/health`, 30000);

  loadStatus("正在启动太极 Agent", [
    "对话能力已就绪",
    "正在打开工作台界面",
    "如遇异常可运行 taiji-agent-diagnose 导出诊断"
  ]);
  await runScript("start-webui.sh", runtimeEnv, desktopLog);
  await waitForHttp(`http://127.0.0.1:${webuiPort}/health`, 30000);

  const target = new URL(`http://127.0.0.1:${webuiPort}`);
  target.searchParams.set("taiji_desktop", "1");
  target.searchParams.set("taiji_desktop_token", runtimeEnv.TAIJI_DESKTOP_ACCESS_TOKEN || "");
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
      submenu: [
        { role: "reload", label: "重新加载" },
        { role: "toggleDevTools", label: "开发者工具" }
      ]
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
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
      nodeIntegration: false,
      sandbox: true
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
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
