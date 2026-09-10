"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PRIVATE_LAB_SEGMENT = "her" + "mes-local-lab";
const PRIVATE_AGENT_SEGMENT = "her" + "mes-agent";
const PRIVATE_WEBUI_SEGMENT = "her" + "mes-webui";
const LEGACY_ENV_PREFIX = "HER" + "MES";
const LICENSE_PATH_OVERRIDE_NAMES = new Set([
  "TAIJI_LICENSE_FILE",
  "TAIJI_LICENSE_STATE_FILE",
]);
const SECURITY_SETTINGS_NAME = "security-settings.json";
const SECURITY_SETTINGS_SCHEMA = "taiji-security-settings/v1";
const SECURITY_SETTINGS_MAX_BYTES = 4096;

function strictSecuritySettings() {
  return {
    profile: "strict",
    capabilities: {
      unapproved_skill_scripts: false,
      delegate_task: false,
    },
  };
}

function currentWindowsSecuritySettings(env) {
  const requested = String(env.TAIJI_SECURITY_PROFILE || "").trim();
  const profile = ["strict", "local_controlled", "full"].includes(requested) ? requested : "strict";
  const enabled = (name) => /^(1|true|yes|on|y)$/i.test(String(env[name] || "").trim());
  return {
    profile,
    capabilities: {
      unapproved_skill_scripts: enabled("TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS"),
      delegate_task: enabled("TAIJI_ALLOW_DELEGATE_TASK"),
    },
  };
}

function readPersistedWindowsSecuritySettings({ runtimeHome, fsModule = fs }) {
  const settingsPath = path.join(runtimeHome, SECURITY_SETTINGS_NAME);
  let metadata;
  try {
    metadata = fsModule.lstatSync(settingsPath);
  } catch (error) {
    if (error && error.code === "ENOENT") return undefined;
    return null;
  }
  try {
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.nlink !== 1) return null;
    if (metadata.size < 1 || metadata.size > SECURITY_SETTINGS_MAX_BYTES) return null;
    const parsed = JSON.parse(fsModule.readFileSync(settingsPath, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    if (Object.keys(parsed).sort().join(",") !== "capabilities,profile,schema") return null;
    if (parsed.schema !== SECURITY_SETTINGS_SCHEMA) return null;
    if (parsed.profile !== "strict" && parsed.profile !== "local_controlled") return null;
    const capabilities = parsed.capabilities;
    if (!capabilities || typeof capabilities !== "object" || Array.isArray(capabilities)) return null;
    if (Object.keys(capabilities).sort().join(",") !== "delegate_task,unapproved_skill_scripts") return null;
    if (typeof capabilities.delegate_task !== "boolean" || typeof capabilities.unapproved_skill_scripts !== "boolean") return null;
    return {
      profile: parsed.profile,
      capabilities: {
        unapproved_skill_scripts: capabilities.unapproved_skill_scripts,
        delegate_task: capabilities.delegate_task,
      },
    };
  } catch (_error) {
    return null;
  }
}

function applyPersistedWindowsSecuritySettings(env, options) {
  const persisted = readPersistedWindowsSecuritySettings(options);
  const settings = persisted === undefined
    ? currentWindowsSecuritySettings(env)
    : (persisted || strictSecuritySettings());
  const localControlled = settings.profile === "local_controlled";
  env.TAIJI_SECURITY_PROFILE = settings.profile;
  env.TAIJI_SECURITY_MODE = settings.profile === "full" ? "full" : "restricted";
  env.TAIJI_ALLOW_TERMINAL = (localControlled || settings.profile === "full") ? "1" : "0";
  env.TAIJI_ALLOW_EXECUTE_CODE = (localControlled || settings.profile === "full") ? "1" : "0";
  env.TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS = settings.capabilities.unapproved_skill_scripts ? "1" : "0";
  env.TAIJI_ALLOW_DELEGATE_TASK = settings.capabilities.delegate_task ? "1" : "0";
  return settings;
}

function deleteLicensePathOverrides(env) {
  for (const key of Object.keys(env)) {
    if (LICENSE_PATH_OVERRIDE_NAMES.has(key.toUpperCase())) delete env[key];
  }
}

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
    nodeExe: path.win32.join(labRoot, "runtime", "node", "node.exe"),
    docxRoot: path.win32.join(labRoot, "sources", "docx-engine-v2"),
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
    layout.nodeExe,
    path.win32.join(layout.docxRoot, "src", "cli", "list-templates.js"),
    path.win32.join(layout.docxRoot, "template-registry.json"),
    path.win32.join(layout.docxRoot, "node_modules", "@resvg", "resvg-js-win32-x64-msvc", "resvgjs.win32-x64-msvc.node"),
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
  fsModule = fs,
}) {
  const env = { ...baseEnv };
  deleteLicensePathOverrides(env);
  for (const key of Object.keys(env)) {
    if (["TAIJI_DOCX_ENGINE_V2_ROOT", "TAIJI_DOCX_BUILTIN_ROOT", "TAIJI_DOCX_RUNTIME_HOME"].includes(key.toUpperCase())) {
      delete env[key];
    }
  }
  const systemRoot = requireAbsoluteWindowsPath("baseEnv.SystemRoot", baseEnv.SystemRoot);
  const accountHome = requireAbsoluteWindowsPath(
    "baseEnv.TAIJI_ACCOUNT_HOME",
    baseEnv.TAIJI_ACCOUNT_HOME,
  );

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
    TAIJI_DOCX_ENGINE_V2_ROOT: layout.docxRoot,
    TAIJI_DOCX_BUILTIN_ROOT: layout.docxRoot,
    TAIJI_DOCX_RUNTIME_HOME: path.win32.join(layout.runtimeHome, "docx-engine-v2"),
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
    TAIJI_ACCOUNT_HOME: accountHome,
    TERMINAL_CWD: layout.workspace,
    PATH: [path.win32.dirname(layout.nodeExe), path.win32.dirname(layout.pythonExe), path.win32.join(systemRoot, "System32")].join(";"),
    TMP: layout.tmpDir,
    TEMP: layout.tmpDir,
    TMPDIR: layout.tmpDir,
  });

  applyPersistedWindowsSecuritySettings(env, {
    runtimeHome: layout.runtimeHome,
    fsModule,
  });

  return env;
}

module.exports = {
  applyPersistedWindowsSecuritySettings,
  buildWindowsRuntimeEnvironment,
  requiredWindowsRuntimeFiles,
  resolveWindowsRuntimeLayout,
  windowsRuntimeCommands,
};
