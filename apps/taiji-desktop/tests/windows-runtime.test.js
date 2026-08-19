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
    path.win32.join(layout.agentRoot, "taiji_runtime", "main.py"),
    path.win32.join(layout.webuiRoot, "server.py"),
    layout.packagedConfig,
  ]);
  assert.doesNotMatch(JSON.stringify(required), /start-agent\.sh|start-webui\.sh|stop-all\.sh/);
});

test("Packaged nav policy exposes exactly the four approved entries", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "..", "..", "..", "hermes-local-lab", "config", "taiji-default-config.yaml"),
    "utf8",
  );
  const navBlock = template.split(/\n\s*nav:\s*\n/, 2)[1].split(/\n\s*settings_sections:\s*\n/, 1)[0];
  const visible = [...navBlock.matchAll(/^\s{6}([a-z_]+):\s*true\s*$/gm)].map((match) => match[1]);
  assert.deepEqual(visible, ["chat", "tasks", "writing", "settings"]);
});

test("Windows environment is private and uses per-user state", () => {
  const layout = resolveWindowsRuntimeLayout({
    installRoot: "C:\\Program Files\\Taiji Agent",
    localAppData: "C:\\Users\\Customer\\AppData\\Local",
  });
  const env = buildWindowsRuntimeEnvironment({
    baseEnv: { SystemRoot: "C:\\Windows", PRESERVE_ME: "yes" },
    layout,
    agentPort: 18642,
    webuiPort: 18787,
    desktopAccessToken: "desktop-token",
    apiServerKey: "api-key",
  });
  assert.equal(env.TAIJI_WINDOWS_CANDIDATE, "1");
  assert.equal(env.TAIJI_RUNTIME_HOME, "C:\\Users\\Customer\\AppData\\Local\\Taiji Agent\\runtime-home");
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
  assert.equal(env.TAIJI_ACCOUNT_HOME, layout.userRoot);
  assert.equal(env.HERMES_WEBUI_AUTO_INSTALL, "0");
  assert.equal(env.TAIJI_WEBUI_GATEWAY_BASE_URL, "http://127.0.0.1:18642");
  assert.equal(env.TERMINAL_CWD, layout.workspace);
  assert.equal(env.TAIJI_LICENSE_FILE, path.win32.join(layout.licenseDir, "active-license.jwt"));
  assert.equal(env.TMPDIR, layout.tmpDir);
  assert.equal(env.PRESERVE_ME, "yes");
  assert.equal(env.PATH, "C:\\Program Files\\Taiji Agent\\hermes-local-lab\\runtime\\python;C:\\Windows\\System32");
});
