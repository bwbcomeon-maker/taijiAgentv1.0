const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const labRoot = path.join(repoRoot, "hermes-local-lab");
const commandLauncher = fs.readFileSync(
  path.join(labRoot, "启动太极Agent桌面端.command"),
  "utf8",
);
const browserLauncher = fs.readFileSync(
  path.join(labRoot, "启动太极Agent.command"),
  "utf8",
);
const appLauncher = fs.readFileSync(
  path.join(
    labRoot,
    "启动太极Agent桌面端.app",
    "Contents",
    "MacOS",
    "taiji-agent-desktop-launcher",
  ),
  "utf8",
);
const mainSource = fs.readFileSync(
  path.join(repoRoot, "apps", "taiji-desktop", "src", "main.js"),
  "utf8",
);

for (const [label, source] of [
  ["command", commandLauncher],
  ["browser command", browserLauncher],
]) {
  test(`${label} launcher resolves the repository from its own physical location`, () => {
    assert.doesNotMatch(source, /\/Users\/bwb\/Documents\/工作\/taiji-agentv1\.0/);
    assert.match(source, /BASH_SOURCE\[0\]/);
    assert.match(source, /pwd -P/);
    assert.match(source, /TAIJI_SOURCE_ROOT/);
    assert.match(source, /TAIJI_SOURCE_COMMIT/);
    assert.match(source, /TAIJI_SOURCE_DIRTY/);
  });
}

test("Finder app resolves its adjacent command launcher from its physical location", () => {
  assert.doesNotMatch(appLauncher, /\/Users\/bwb\/Documents\/工作\/taiji-agentv1\.0/);
  assert.match(appLauncher, /BASH_SOURCE\[0\]/);
  assert.match(appLauncher, /pwd -P/);
  assert.match(appLauncher, /COMMAND_LAUNCHER=.*启动太极Agent桌面端\.command/);
  assert.match(appLauncher, /\/usr\/bin\/open -a Terminal "\$COMMAND_LAUNCHER"/);
});

test("the source command launcher never silently redirects to a stale app bundle", () => {
  assert.doesNotMatch(commandLauncher, /open "\$APP_BUNDLE"/);
  assert.doesNotMatch(commandLauncher, /Opening app bundle/);
  assert.match(commandLauncher, /Electron\.app\/Contents\/MacOS\/Electron/);
});

test("source command launcher defaults linked worktrees to development and runs the source gate", () => {
  assert.match(commandLauncher, /\[ -d "\$REPO_DIR\/\.git" \]/);
  assert.match(commandLauncher, /\[ -f "\$REPO_DIR\/\.git" \]/);
  assert.match(commandLauncher, /TAIJI_SOURCE_MODE="development"/);
  assert.match(commandLauncher, /TAIJI_SOURCE_MODE="formal"/);
  assert.match(commandLauncher, /\[ -z "\$\{TAIJI_SOURCE_MODE:-\}" \]/);
  assert.match(commandLauncher, /check-clean-worktree\.sh/);
  assert.match(commandLauncher, /\/bin\/bash "\$SOURCE_GATE"/);
  assert.match(commandLauncher, /--mode "\$TAIJI_SOURCE_MODE"/);
  assert.match(commandLauncher, /--repo-root "\$REPO_DIR"/);
  assert.match(commandLauncher, /--source-root "\$REPO_DIR"/);
  assert.match(commandLauncher, /--dirty-policy runtime/);
  assert.match(commandLauncher, /export TAIJI_SOURCE_MODE/);
});

test("source command launcher passes the available Python runtime to both services", () => {
  assert.match(commandLauncher, /sources\/hermes-agent\/venv\/bin\/python/);
  assert.match(commandLauncher, /sources\/hermes-agent\/\.venv\/bin\/python/);
  assert.match(commandLauncher, /export TAIJI_AGENT_PYTHON/);
  assert.match(commandLauncher, /export TAIJI_WEBUI_PYTHON/);
});

test("source command launcher isolates all mutable runtime state by physical source root", () => {
  assert.match(commandLauncher, /XDG_STATE_HOME=.*source-instances.*SOURCE_INSTANCE_ID/);
  assert.match(commandLauncher, /TAIJI_RUNTIME_HOME=.*source-instances.*SOURCE_INSTANCE_ID/);
  assert.match(commandLauncher, /TAIJI_WORKSPACE=.*source-instances.*SOURCE_INSTANCE_ID/);
  assert.match(commandLauncher, /TAIJI_AGENT_TMP_DIR=.*source-instances.*SOURCE_INSTANCE_ID/);
  assert.match(commandLauncher, /export XDG_STATE_HOME/);
  assert.match(commandLauncher, /export TAIJI_RUNTIME_HOME/);
  assert.match(commandLauncher, /export TAIJI_WORKSPACE/);
  assert.match(commandLauncher, /export TAIJI_AGENT_TMP_DIR/);
});

for (const [label, source] of [
  ["command", commandLauncher],
]) {
  test(`${label} desktop launcher isolates Electron single-instance state by physical source root`, () => {
    assert.match(source, /SOURCE_INSTANCE_ID/);
    assert.match(source, /shasum -a 256/);
    assert.match(source, /TAIJI_DESKTOP_USER_DATA_DIR/);
    assert.match(source, /source-instances/);
  });
}

test("Electron applies a source launcher user-data override before acquiring its singleton lock", () => {
  const configureIndex = mainSource.indexOf("configureDesktopUserDataDir();");
  const lockIndex = mainSource.indexOf("app.requestSingleInstanceLock()");
  assert.notEqual(configureIndex, -1);
  assert.notEqual(lockIndex, -1);
  assert.ok(configureIndex < lockIndex);
});

test("Electron boot and runtime logs preserve the exact source provenance", () => {
  assert.match(mainSource, /TAIJI_SOURCE_ROOT/);
  assert.match(mainSource, /TAIJI_SOURCE_COMMIT/);
  assert.match(mainSource, /TAIJI_SOURCE_DIRTY/);
  assert.match(mainSource, /sourceRoot=/);
  assert.match(mainSource, /sourceCommit=/);
  assert.match(mainSource, /sourceDirty=/);
});

test("Electron verifies formal source provenance before creating a window", () => {
  const gateIndex = mainSource.indexOf("verifyFormalSourceBeforeWindow();");
  const menuIndex = mainSource.indexOf("installMenu();", gateIndex);
  const windowIndex = mainSource.indexOf("createWindow();", gateIndex);
  assert.notEqual(gateIndex, -1);
  assert.notEqual(menuIndex, -1);
  assert.notEqual(windowIndex, -1);
  assert.ok(gateIndex < menuIndex);
  assert.ok(gateIndex < windowIndex);
  assert.match(mainSource, /check-clean-worktree\.sh/);
  assert.match(mainSource, /"--dirty-policy", "runtime"/);
  assert.doesNotMatch(mainSource, /Formal source worktree is dirty/);
});

test("installed Linux launcher fixes the install root and explicitly requests installed-production", () => {
  const installedLauncher = fs.readFileSync(
    path.join(repoRoot, "packaging", "linux", "bin", "taiji-agent"),
    "utf8",
  );
  assert.match(installedLauncher, /APP_ROOT="\/opt\/taiji-agent"/);
  assert.doesNotMatch(installedLauncher, /TAIJI_AGENT_ROOT:-\/opt\/taiji-agent/);
  assert.match(installedLauncher, /TAIJI_LAUNCH_PROFILE="installed-production"/);
  assert.match(installedLauncher, /export TAIJI_LAUNCH_PROFILE/);
  assert.match(installedLauncher, /^#!\/bin\/bash -p$/m);
  assert.match(installedLauncher, /PATH="\/usr\/bin:\/bin:\/usr\/sbin:\/sbin"/);
  assert.match(installedLauncher, /export PATH/);
  assert.match(installedLauncher, /\/usr\/bin\/env -0/);
  assert.match(installedLauncher, /\/usr\/bin\/env "\$\{_taiji_unset_args\[@\]\}"/);
  assert.match(installedLauncher, /\/bin\/bash --noprofile --norc -p "\$0" "\$@"/);
  for (const selector of [
    "ELECTRON_RUN_AS_NODE",
    "NODE_*",
    "PYTHON*",
    "LD_*",
    "BASH_ENV",
    "ENV",
  ]) {
    assert.equal(installedLauncher.includes(selector), true, `${selector} must be sanitized`);
  }
});

test("installed Electron integration uses release provenance and keeps local HTTP OIDC development-only", () => {
  assert.match(mainSource, /TAIJI_RELEASE_VERSION/);
  assert.match(mainSource, /TAIJI_RELEASE_COMMIT/);
  assert.match(mainSource, /delete env\.TAIJI_SOURCE_ROOT/);
  assert.match(mainSource, /delete env\.TAIJI_SOURCE_COMMIT/);
  assert.match(mainSource, /delete env\.TAIJI_SOURCE_DIRTY/);
  const installedRuntimeBoundaries = mainSource.match(
    /applyInstalledRuntimePaths\(\{ launchProfile, runtimeEnv: env \}\)/g,
  ) || [];
  assert.equal(installedRuntimeBoundaries.length, 2, "both stop and start child environments must be pinned");
  assert.match(mainSource, /allowLocalHttp:\s*launchProfile\.kind === "source"\s*&&\s*launchProfile\.mode === "development"/);
});

test("both desktop and identity windows apply the launch-profile DevTools policy", () => {
  const matches = mainSource.match(/devTools:\s*allowsDevTools\(launchProfile\)/g) || [];
  assert.equal(matches.length, 2);
});

test("Electron authenticates the desktop session without putting the bearer token in the URL", () => {
  const cookieIndex = mainSource.indexOf("webContents.session.cookies.set");
  const loadIndex = mainSource.indexOf(
    "await mainWindow.loadURL(target.toString())",
    cookieIndex,
  );
  assert.notEqual(cookieIndex, -1);
  assert.notEqual(loadIndex, -1);
  assert.ok(cookieIndex < loadIndex);
  assert.match(mainSource, /name:\s*"taiji_desktop_token"/);
  assert.match(mainSource, /httpOnly:\s*true/);
  assert.match(mainSource, /sameSite:\s*"strict"/);
  assert.doesNotMatch(
    mainSource,
    /searchParams\.set\("taiji_desktop_token"/,
  );
});
