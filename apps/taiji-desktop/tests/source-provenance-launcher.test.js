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
  ["app", appLauncher],
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

test("the source command launcher never silently redirects to a stale app bundle", () => {
  assert.doesNotMatch(commandLauncher, /open "\$APP_BUNDLE"/);
  assert.doesNotMatch(commandLauncher, /Opening app bundle/);
  assert.match(commandLauncher, /Electron\.app\/Contents\/MacOS\/Electron/);
});

test("Electron boot and runtime logs preserve the exact source provenance", () => {
  assert.match(mainSource, /TAIJI_SOURCE_ROOT/);
  assert.match(mainSource, /TAIJI_SOURCE_COMMIT/);
  assert.match(mainSource, /TAIJI_SOURCE_DIRTY/);
  assert.match(mainSource, /sourceRoot=/);
  assert.match(mainSource, /sourceCommit=/);
  assert.match(mainSource, /sourceDirty=/);
});
