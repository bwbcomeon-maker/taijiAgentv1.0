"use strict";

const path = require("node:path");

const PRIVATE_LAB_SEGMENT = "her" + "mes-local-lab";
const PRIVATE_AGENT_SEGMENT = "her" + "mes-agent";
const PRIVATE_WEBUI_SEGMENT = "her" + "mes-webui";
const LEGACY_ENV_PREFIX = "HER" + "MES";

function requireAbsoluteWindowsPath(name, value) {
  if (typeof value !== "string" || !path.win32.isAbsolute(value)) {
    throw new TypeError(`${name} must be an absolute Windows path`);
  }
  return path.win32.normalize(value);
}

function resolveWindowsRuntimeLayout({ installRoot, localAppData }) {
  const root = requireAbsoluteWindowsPath("installRoot", installRoot);
  const appData = requireAbsoluteWindowsPath("localAppData", localAppData);
  const labRoot = path.win32.join(root, PRIVATE_LAB_SEGMENT);
  const pythonRoot = path.win32.join(labRoot, "runtime", "python");
  const userRoot = path.win32.join(appData, "Taiji Agent");

  return {
    installRoot: root,
    labRoot,
    pythonRoot,
    pythonExe: path.win32.join(pythonRoot, "python.exe"),
    sitePackages: path.win32.join(pythonRoot, "Lib", "site-packages"),
    agentRoot: path.win32.join(labRoot, "sources", PRIVATE_AGENT_SEGMENT),
    webuiRoot: path.win32.join(labRoot, "sources", PRIVATE_WEBUI_SEGMENT),
    userRoot,
    electronDir: path.win32.join(userRoot, "electron"),
    runtimeHome: path.win32.join(userRoot, "runtime-home"),
    packagedConfig: path.win32.join(labRoot, "config", "taiji-default-config.yaml"),
    workspace: path.win32.join(userRoot, "workspace"),
    stateDir: path.win32.join(userRoot, "state"),
    logDir: path.win32.join(userRoot, "logs"),
    tmpDir: path.win32.join(userRoot, "tmp"),
    licenseDir: path.win32.join(userRoot, "license"),
    webuiStateDir: path.win32.join(userRoot, "webui-state"),
  };
}

function windowsRuntimeCommands(layout) {
  return {
    agent: {
      file: layout.pythonExe,
      args: ["-m", "taiji_runtime.main", "gateway", "run", "--accept-hooks"],
      cwd: layout.agentRoot,
    },
    webui: {
      file: layout.pythonExe,
      args: [path.win32.join(layout.webuiRoot, "server.py")],
      cwd: layout.webuiRoot,
    },
  };
}

function requiredWindowsRuntimeFiles(layout) {
  return [
    layout.pythonExe,
    path.win32.join(layout.agentRoot, "taiji_runtime", "main.py"),
    path.win32.join(layout.webuiRoot, "server.py"),
    layout.packagedConfig,
  ];
}

function buildWindowsRuntimeEnvironment({
  baseEnv,
  layout,
  agentPort,
  webuiPort,
  desktopAccessToken,
  apiServerKey,
}) {
  const env = { ...baseEnv };
  const systemRoot = requireAbsoluteWindowsPath("baseEnv.SystemRoot", baseEnv.SystemRoot);

  Object.assign(env, {
    TAIJI_WINDOWS_CANDIDATE: "1",
    TAIJI_AGENT_USE_USER_DIRS: "1",
    TAIJI_RUNTIME_HOME: layout.runtimeHome,
    [`${LEGACY_ENV_PREFIX}_HOME`]: layout.runtimeHome,
    TAIJI_WORKSPACE: layout.workspace,
    TAIJI_STATE_DIR: layout.stateDir,
    TAIJI_AGENT_LOG_DIR: layout.logDir,
    TAIJI_AGENT_TMP_DIR: layout.tmpDir,
    TAIJI_AGENT_ROOT: layout.labRoot,
    TAIJI_AGENT_AGENT_DIR: layout.agentRoot,
    TAIJI_AGENT_WEBUI_DIR: layout.webuiRoot,
    TAIJI_AGENT_PYTHON: layout.pythonExe,
    TAIJI_WEBUI_PYTHON: layout.pythonExe,
    TAIJI_WEBUI_AGENT_DIR: layout.agentRoot,
    [`${LEGACY_ENV_PREFIX}_WEBUI_PYTHON`]: layout.pythonExe,
    [`${LEGACY_ENV_PREFIX}_WEBUI_AGENT_DIR`]: layout.agentRoot,
    [`${LEGACY_ENV_PREFIX}_WEBUI_AUTO_INSTALL`]: "0",
    PYTHONPATH: [layout.agentRoot, layout.webuiRoot, layout.sitePackages].join(";"),
    API_SERVER_ENABLED: "true",
    AGENT_API_HOST: "127.0.0.1",
    AGENT_API_PORT: String(agentPort),
    API_SERVER_HOST: "127.0.0.1",
    API_SERVER_PORT: String(agentPort),
    API_SERVER_KEY: apiServerKey,
    API_SERVER_CORS_ORIGINS: `http://127.0.0.1:${webuiPort},http://localhost:${webuiPort}`,
    TAIJI_ACCEPT_HOOKS: "1",
    TAIJI_WEBUI_HOST: "127.0.0.1",
    TAIJI_WEBUI_PORT: String(webuiPort),
    WEBUI_HOST: "127.0.0.1",
    WEBUI_PORT: String(webuiPort),
    [`${LEGACY_ENV_PREFIX}_WEBUI_HOST`]: "127.0.0.1",
    [`${LEGACY_ENV_PREFIX}_WEBUI_PORT`]: String(webuiPort),
    TAIJI_WEBUI_STATE_DIR: layout.webuiStateDir,
    TAIJI_WEBUI_PACKAGED_CONFIG: layout.packagedConfig,
    [`${LEGACY_ENV_PREFIX}_WEBUI_STATE_DIR`]: layout.webuiStateDir,
    TAIJI_WEBUI_DEFAULT_WORKSPACE: layout.workspace,
    TAIJI_WEBUI_CHAT_BACKEND: "gateway",
    TAIJI_WEBUI_GATEWAY_BASE_URL: `http://127.0.0.1:${agentPort}`,
    TAIJI_WEBUI_GATEWAY_API_KEY: apiServerKey,
    TAIJI_DESKTOP_ACCESS_TOKEN: desktopAccessToken,
    TAIJI_DESKTOP_ONLY: "1",
    TAIJI_ACCOUNT_HOME: layout.userRoot,
    TAIJI_LICENSE_FILE: path.win32.join(layout.licenseDir, "active-license.jwt"),
    TAIJI_LICENSE_STATE_FILE: path.win32.join(layout.stateDir, "license-state.json"),
    TERMINAL_CWD: layout.workspace,
    PATH: [path.win32.dirname(layout.pythonExe), path.win32.join(systemRoot, "System32")].join(";"),
    TMP: layout.tmpDir,
    TEMP: layout.tmpDir,
    TMPDIR: layout.tmpDir,
  });

  return env;
}

module.exports = {
  buildWindowsRuntimeEnvironment,
  requiredWindowsRuntimeFiles,
  resolveWindowsRuntimeLayout,
  windowsRuntimeCommands,
};
