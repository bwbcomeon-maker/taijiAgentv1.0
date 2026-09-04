const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const path = require("node:path");

const {
  buildWindowsRuntimeEnvironment,
  requiredWindowsRuntimeFiles,
  resolveWindowsRuntimeLayout,
  windowsRuntimeCommands,
} = require("../src/windows-runtime");

test("Windows layout stays under install root and LOCALAPPDATA", () => {
  const layout = resolveWindowsRuntimeLayout({
    installRoot: "C:\\Program Files\\Taiji Agent",
    localAppData: "C:\\Users\\Customer\\AppData\\Local",
  });
  assert.equal(layout.pythonExe, "C:\\Program Files\\Taiji Agent\\hermes-local-lab\\runtime\\python\\python.exe");
  assert.equal(layout.agentRoot, "C:\\Program Files\\Taiji Agent\\hermes-local-lab\\sources\\hermes-agent");
  assert.equal(layout.webuiRoot, "C:\\Program Files\\Taiji Agent\\hermes-local-lab\\sources\\hermes-webui");
  assert.equal(layout.packagedConfig, "C:\\Program Files\\Taiji Agent\\hermes-local-lab\\config\\taiji-default-config.yaml");
  assert.equal(layout.userRoot, "C:\\Users\\Customer\\AppData\\Local\\Taiji Agent");
  assert.equal(layout.stateDir, "C:\\Users\\Customer\\AppData\\Local\\Taiji Agent\\state");
});

test("Windows commands use private Python and no shell script", () => {
  const layout = resolveWindowsRuntimeLayout({
    installRoot: "C:\\Program Files\\Taiji Agent",
    localAppData: "C:\\Users\\Customer\\AppData\\Local",
  });
  const commands = windowsRuntimeCommands(layout);
  assert.equal(commands.agent.file, layout.pythonExe);
  assert.deepEqual(commands.agent.args, ["-m", "taiji_runtime.main", "gateway", "run", "--accept-hooks"]);
  assert.equal(commands.agent.cwd, layout.agentRoot);
  assert.equal(commands.webui.file, layout.pythonExe);
  assert.deepEqual(commands.webui.args, [path.win32.join(layout.webuiRoot, "server.py")]);
  assert.equal(commands.webui.cwd, layout.webuiRoot);
  assert.doesNotMatch(JSON.stringify(commands), /bash|\.sh/);
});

test("Windows runtime checks private files instead of shell scripts", () => {
  const layout = resolveWindowsRuntimeLayout({
    installRoot: "C:\\Program Files\\Taiji Agent",
    localAppData: "C:\\Users\\Customer\\AppData\\Local",
  });
  const required = requiredWindowsRuntimeFiles(layout);
  assert.deepEqual(required, [
    layout.pythonExe,
    layout.nodeExe,
    path.win32.join(layout.docxRoot, "src", "cli", "list-templates.js"),
    path.win32.join(layout.docxRoot, "template-registry.json"),
    path.win32.join(layout.docxRoot, "node_modules", "@resvg", "resvg-js-win32-x64-msvc", "resvgjs.win32-x64-msvc.node"),
    path.win32.join(layout.agentRoot, "taiji_runtime", "main.py"),
    path.win32.join(layout.webuiRoot, "server.py"),
    layout.packagedConfig,
  ]);
  assert.doesNotMatch(JSON.stringify(required), /start-agent\.sh|start-webui\.sh|stop-all\.sh/);
});

test("Packaged nav policy exposes exactly the four approved entries", () => {
  const templatePath = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "packaging",
    "windows",
    "taiji-default-config.yaml",
  );
  assert.equal(fs.existsSync(templatePath), true, "Windows menu policy must use a dedicated packaging config");
  const template = fs.readFileSync(templatePath, "utf8");
  const navBlock = template.split(/\n\s*nav:\s*\n/, 2)[1].split(/\n\s*settings_sections:\s*\n/, 1)[0];
  const visible = [...navBlock.matchAll(/^\s{6}([a-z_]+):\s*true\s*$/gm)].map((match) => match[1]);
  assert.deepEqual(visible, ["chat", "tasks", "writing", "settings"]);
});

test("Windows environment is private and omits license path overrides", () => {
  const layout = resolveWindowsRuntimeLayout({
    installRoot: "C:\\Program Files\\Taiji Agent",
    localAppData: "D:\\Poisoned\\AppData\\Local",
  });
  const env = buildWindowsRuntimeEnvironment({
    baseEnv: {
      SystemRoot: "C:\\Windows",
      PRESERVE_ME: "yes",
      TAIJI_ACCOUNT_HOME: "C:\\Users\\Customer",
      TAIJI_DOCX_RUNTIME_HOME: "D:\\old-docx",
      taiji_docx_runtime_home: "D:\\other-docx",
      taiji_license_file: "C:\\poisoned\\license.jwt",
      TaIjI_LiCeNsE_sTaTe_FiLe: "C:\\poisoned\\state.json",
    },
    layout,
    agentPort: 18642,
    webuiPort: 18787,
    desktopAccessToken: "desktop-token",
    apiServerKey: "api-key",
  });
  assert.equal(env.TAIJI_WINDOWS_CANDIDATE, "1");
  assert.equal(env.TAIJI_RUNTIME_HOME, "D:\\Poisoned\\AppData\\Local\\Taiji Agent\\runtime-home");
  assert.equal(env.TAIJI_STATE_DIR, layout.stateDir);
  assert.equal(env.TAIJI_AGENT_USE_USER_DIRS, "1");
  assert.equal(env.TAIJI_AGENT_LOG_DIR, layout.logDir);
  assert.equal(env.TAIJI_WEBUI_PACKAGED_CONFIG, layout.packagedConfig);
  assert.equal(env.TAIJI_AGENT_PYTHON, layout.pythonExe);
  assert.equal(env.TAIJI_WEBUI_PYTHON, layout.pythonExe);
  assert.equal(env.AGENT_API_HOST, "127.0.0.1");
  assert.equal(env.AGENT_API_PORT, "18642");
  assert.equal(env.WEBUI_HOST, "127.0.0.1");
  assert.equal(env.WEBUI_PORT, "18787");
  assert.equal(env.TAIJI_DESKTOP_ONLY, "1");
  assert.equal(env.TAIJI_ACCOUNT_HOME, "C:\\Users\\Customer");
  assert.equal(env.HERMES_WEBUI_AUTO_INSTALL, "0");
  assert.equal(env.TAIJI_WEBUI_GATEWAY_BASE_URL, "http://127.0.0.1:18642");
  assert.equal(env.TERMINAL_CWD, layout.workspace);
  assert.deepEqual(
    Object.keys(env).filter((key) => ["TAIJI_LICENSE_FILE", "TAIJI_LICENSE_STATE_FILE"].includes(key.toUpperCase())),
    [],
  );
  assert.equal(env.TMPDIR, layout.tmpDir);
  assert.equal(env.PRESERVE_ME, "yes");
  assert.equal(layout.nodeExe, "C:\\Program Files\\Taiji Agent\\hermes-local-lab\\runtime\\node\\node.exe");
  assert.equal(env.TAIJI_DOCX_ENGINE_V2_ROOT, layout.docxRoot);
  assert.equal(env.TAIJI_DOCX_BUILTIN_ROOT, layout.docxRoot);
  assert.equal(env.TAIJI_DOCX_RUNTIME_HOME, path.win32.join(layout.runtimeHome, "docx-engine-v2"));
  assert.equal(env.taiji_docx_runtime_home, undefined);
  assert.equal(env.PATH, "C:\\Program Files\\Taiji Agent\\hermes-local-lab\\runtime\\node;C:\\Program Files\\Taiji Agent\\hermes-local-lab\\runtime\\python;C:\\Windows\\System32");
});

test("Windows environment requires an absolute system account home", () => {
  const layout = resolveWindowsRuntimeLayout({
    installRoot: "C:\\Program Files\\Taiji Agent",
    localAppData: "D:\\Poisoned\\AppData\\Local",
  });
  const build = (baseEnv) => buildWindowsRuntimeEnvironment({
    baseEnv,
    layout,
    agentPort: 18642,
    webuiPort: 18787,
    desktopAccessToken: "desktop-token",
    apiServerKey: "api-key",
  });

  assert.throws(
    () => build({ SystemRoot: "C:\\Windows" }),
    /baseEnv\.TAIJI_ACCOUNT_HOME must be an absolute Windows path/,
  );
  assert.throws(
    () => build({ SystemRoot: "C:\\Windows", TAIJI_ACCOUNT_HOME: "relative\\home" }),
    /baseEnv\.TAIJI_ACCOUNT_HOME must be an absolute Windows path/,
  );
});
