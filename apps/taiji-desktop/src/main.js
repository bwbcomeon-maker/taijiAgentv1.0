const { app, BrowserWindow, Menu, shell, dialog } = require("electron");
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
const gotSingleInstanceLock = app.requestSingleInstanceLock();

function resolveLabDir() {
  if (process.env.TAIJI_AGENT_ROOT) {
    return path.resolve(process.env.TAIJI_AGENT_ROOT);
  }

  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const sourceLab = path.join(repoRoot, "hermes-local-lab");
  if (fs.existsSync(path.join(sourceLab, "scripts", "start-agent.sh"))) {
    return sourceLab;
  }
  return repoRoot;
}

function userStateDir() {
  const base = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state");
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
    <p>本地服务启动完成后会自动进入对话界面。</p>
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
        reject(new Error(`Timed out waiting for ${url}`));
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

function runScript(scriptName, env, logFile) {
  const script = path.join(env.TAIJI_AGENT_ROOT, "scripts", scriptName);
  return new Promise((resolve, reject) => {
    appendDesktopLog(logFile, `starting ${scriptName}`);
    const child = spawn(script, {
      cwd: env.TAIJI_AGENT_ROOT,
      env,
      stdio: ["ignore", "pipe", "pipe"]
    });

    child.stdout.on("data", (chunk) => appendDesktopLog(logFile, `[${scriptName}] ${chunk.toString().trimEnd()}`));
    child.stderr.on("data", (chunk) => appendDesktopLog(logFile, `[${scriptName} error] ${chunk.toString().trimEnd()}`));
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${scriptName} exited with code ${code}`));
      }
    });
  });
}

function createRuntimeEnv(labDir, agentPort, webuiPort, logDir) {
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  env.TAIJI_AGENT_ROOT = labDir;
  env.TAIJI_AGENT_USE_USER_DIRS = "1";
  env.TAIJI_AGENT_LOG_DIR = logDir;
  env.AGENT_API_HOST = "127.0.0.1";
  env.AGENT_API_PORT = String(agentPort);
  env.API_SERVER_HOST = "127.0.0.1";
  env.API_SERVER_PORT = String(agentPort);
  env.WEBUI_HOST = "127.0.0.1";
  env.WEBUI_PORT = String(webuiPort);
  env.HERMES_WEBUI_HOST = "127.0.0.1";
  env.HERMES_WEBUI_PORT = String(webuiPort);
  env.API_SERVER_KEY = crypto.randomBytes(32).toString("hex");
  env.HERMES_WEBUI_GATEWAY_API_KEY = env.API_SERVER_KEY;
  env.HERMES_WEBUI_GATEWAY_BASE_URL = `http://127.0.0.1:${agentPort}`;
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
  const labDir = resolveLabDir();
  const logDir = path.join(userStateDir(), "logs");
  const desktopLog = path.join(logDir, "taiji-desktop.log");
  const iconPath = path.join(labDir, "sources", "hermes-webui", "static", "favicon-512.png");

  if (!fs.existsSync(path.join(labDir, "scripts", "start-agent.sh"))) {
    throw new Error(`Runtime scripts not found under ${labDir}`);
  }

  loadStatus("正在启动太极 Agent", [
    "正在准备本机运行环境",
    "正在检查服务状态",
    "可通过菜单打开运行日志"
  ]);

  const agentPort = await findFreePort(DEFAULT_AGENT_PORT);
  const webuiPort = await findFreePort(DEFAULT_WEBUI_PORT);
  runtimeEnv = createRuntimeEnv(labDir, agentPort, webuiPort, logDir);

  loadStatus("正在启动太极 Agent", [
    "正在启动本地对话服务",
    "正在准备工作台界面",
    "可通过菜单打开运行日志"
  ]);
  await runScript("start-agent.sh", runtimeEnv, desktopLog);
  await waitForHttp(`http://127.0.0.1:${agentPort}/health`, 30000);

  loadStatus("正在启动太极 Agent", [
    "本地对话服务已就绪",
    "正在打开工作台界面",
    "可通过菜单打开运行日志"
  ]);
  await runScript("start-webui.sh", runtimeEnv, desktopLog);
  await waitForHttp(`http://127.0.0.1:${webuiPort}/health`, 30000);

  const target = new URL(`http://127.0.0.1:${webuiPort}`);
  target.searchParams.set("taiji_desktop", "1");
  appendDesktopLog(desktopLog, `loading ${target.toString()}`);
  if (fs.existsSync(iconPath)) {
    mainWindow.setIcon(iconPath);
  }
  await mainWindow.loadURL(target.toString());
}

function installMenu() {
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
  const labDir = resolveLabDir();
  const iconPath = path.join(labDir, "sources", "hermes-webui", "static", "favicon-512.png");
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    show: !SMOKE_TEST,
    title: APP_NAME,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    backgroundColor: DESKTOP_CHROME_BACKGROUND,
    ...(process.platform === "darwin" ? {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: { x: 16, y: 16 }
    } : {}),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  loadStatus("正在准备太极 Agent", ["初始化桌面窗口", "准备本机运行环境"]);

  try {
    await startRuntime();
    if (SMOKE_TEST) {
      setTimeout(() => app.quit(), 800);
    }
  } catch (error) {
    const message = error && error.stack ? error.stack : String(error);
    loadStatus("启动失败", [
      "本地服务未能启动",
      "请通过菜单打开运行日志查看技术诊断信息"
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
    installMenu();
    createWindow();
  });

  app.on("before-quit", stopRuntime);

  app.on("window-all-closed", () => {
    stopRuntime();
    if (process.platform !== "darwin") {
      app.quit();
    }
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    } else {
      focusMainWindow();
    }
  });
}
