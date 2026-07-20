const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const {
  DAILY_EQUIVALENT_FEATURE_VISIBILITY,
  DAILY_EQUIVALENT_VISIBLE_NAV,
  assertNavigationParity,
  assertScreenshotSanity,
  buildAcceptanceProvenance,
  collectSourceFingerprint,
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

function cleanGitEnv() {
  const env = { ...process.env };
  for (const name of AMBIENT_GIT_ENV) delete env[name];
  return env;
}

function git(cwd, ...args) {
  const result = spawnSync("git", args, { cwd, encoding: "utf8", env: cleanGitEnv() });
  assert.equal(result.status, 0, `git ${args.join(" ")} failed: ${String(result.stderr || "").trim()}`);
  return String(result.stdout || "").trim();
}

function createFingerprintFixture() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-provenance-git-test-"));
  const primary = path.join(base, "primary");
  const linked = path.join(base, "linked");
  const webui = path.join(base, "webui");
  fs.mkdirSync(primary, { recursive: true });
  fs.mkdirSync(path.join(webui, "static"), { recursive: true });
  fs.writeFileSync(path.join(webui, "static", "index.html"), "<main>fixture</main>\n");
  fs.writeFileSync(path.join(webui, "static", "ui.js"), "window.fixture=true;\n");
  git(primary, "init", "-q", "-b", "main");
  git(primary, "config", "user.email", "provenance@example.invalid");
  git(primary, "config", "user.name", "Provenance Test");
  fs.writeFileSync(path.join(primary, "README.md"), "fixture\n");
  git(primary, "add", "README.md");
  git(primary, "commit", "-qm", "fixture");
  git(primary, "worktree", "add", "-q", "-b", "feature", linked);
  return { base, primary, linked, webui };
}

test("daily-equivalent runtime fixture contains only the approved feature visibility projection", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-parity-config-test-"));
  try {
    const projection = installDailyEquivalentRuntimeConfig(root);
    const configText = fs.readFileSync(path.join(root, "config.yaml"), "utf8");

    assert.equal(projection.source_type, "sanitized_daily_equivalent_fixture");
    assert.deepEqual(projection.feature_visibility, DAILY_EQUIVALENT_FEATURE_VISIBILITY);
    assert.deepEqual(projection.expected_visible_nav, DAILY_EQUIVALENT_VISIBLE_NAV);
    assert.match(configText, /^webui:\n  feature_visibility:\n/);
    assert.doesNotMatch(configText, /api[_-]?key|access[_-]?token|secret|password/i);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("worktree capability override remains nav-equivalent and is explicit in provenance", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-parity-worktree-config-test-"));
  try {
    const projection = installDailyEquivalentRuntimeConfig(root, {
      capability_overrides: { composer: { workspace_switcher: true } },
    });

    assert.equal(projection.source_type, "sanitized_daily_nav_equivalent_fixture");
    assert.deepEqual(projection.expected_visible_nav, DAILY_EQUIVALENT_VISIBLE_NAV);
    assert.deepEqual(projection.capability_overrides, {
      composer: { workspace_switcher: true },
    });
    assert.equal(projection.feature_visibility.composer.workspace_switcher, true);
    assert.deepEqual(projection.feature_visibility.nav, DAILY_EQUIVALENT_FEATURE_VISIBILITY.nav);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("source fingerprint classifies primary, linked, and detached checkouts", () => {
  const fixture = createFingerprintFixture();
  try {
    const options = repoRoot => ({
      repoRoot,
      webuiDir: fixture.webui,
      staticFiles: ["index.html", "ui.js"],
    });
    const primaryMain = collectSourceFingerprint(options(fixture.primary));
    assert.equal(primaryMain.branch, "main");
    assert.equal(primaryMain.checkout_type, "formal_main_primary_worktree");
    assert.match(primaryMain.commit, /^[0-9a-f]{40}$/);
    assert.equal(primaryMain.dirty, false);
    assert.deepEqual(Object.keys(primaryMain.static_files_sha256), ["index.html", "ui.js"]);
    for (const digest of Object.values(primaryMain.static_files_sha256)) {
      assert.match(digest, /^[0-9a-f]{64}$/);
    }

    const linkedBranch = collectSourceFingerprint(options(fixture.linked));
    assert.equal(linkedBranch.branch, "feature");
    assert.equal(linkedBranch.checkout_type, "linked_worktree");

    git(fixture.primary, "switch", "-q", "-c", "non-main");
    assert.equal(
      collectSourceFingerprint(options(fixture.primary)).checkout_type,
      "primary_non_main",
    );

    git(fixture.primary, "checkout", "-q", "--detach");
    const detachedPrimary = collectSourceFingerprint(options(fixture.primary));
    assert.equal(detachedPrimary.branch, "");
    assert.equal(detachedPrimary.checkout_type, "detached_primary_worktree");

    git(fixture.linked, "checkout", "-q", "--detach");
    const detachedLinked = collectSourceFingerprint(options(fixture.linked));
    assert.equal(detachedLinked.branch, "");
    assert.equal(detachedLinked.checkout_type, "linked_worktree");
  } finally {
    fs.rmSync(fixture.base, { recursive: true, force: true });
  }
});

test("explicit repoRoot wins over ambient Git locator variables", () => {
  const fixture = createFingerprintFixture();
  const original = Object.fromEntries(AMBIENT_GIT_ENV.map(name => [name, process.env[name]]));
  try {
    process.env.GIT_DIR = path.join(fixture.primary, ".git");
    process.env.GIT_WORK_TREE = fixture.primary;
    process.env.GIT_COMMON_DIR = path.join(fixture.primary, ".git");
    const fingerprint = collectSourceFingerprint({
      repoRoot: fixture.linked,
      webuiDir: fixture.webui,
      staticFiles: ["index.html", "ui.js"],
    });
    assert.equal(fingerprint.branch, "feature");
    assert.equal(fingerprint.checkout_type, "linked_worktree");
  } finally {
    for (const name of AMBIENT_GIT_ENV) {
      if (original[name] === undefined) delete process.env[name];
      else process.env[name] = original[name];
    }
    fs.rmSync(fixture.base, { recursive: true, force: true });
  }
});

test("acceptance provenance derives desktop source from the source fingerprint", () => {
  const sourceFingerprint = { checkout_type: "formal_main_primary_worktree", branch: "main" };
  const runtimeConfig = { source_type: "sanitized_daily_equivalent_fixture" };
  const navigationParity = { visible: ["chat"] };
  const provenance = buildAcceptanceProvenance({
    sourceFingerprint,
    runtimeConfig,
    navigationParity,
  });

  assert.deepEqual(provenance, {
    source: sourceFingerprint,
    desktop_app_source: "formal_main_primary_worktree",
    runtime_config: runtimeConfig,
    user_data: { type: "isolated_temporary" },
    navigation: navigationParity,
  });
});

test("navigation parity rejects any visible item that differs from the daily product profile", () => {
  assert.doesNotThrow(() => assertNavigationParity({
    all: ["chat", "tasks", "kanban", "writing", "settings"],
    visible: ["chat", "tasks", "writing", "settings"],
    hidden: ["kanban"],
    single_runtime: false,
  }));
  assert.throws(
    () => assertNavigationParity({
      all: ["chat", "tasks", "kanban", "writing", "settings"],
      visible: ["chat", "tasks", "kanban", "writing", "settings"],
      hidden: [],
      single_runtime: true,
    }),
    /navigation parity mismatch/,
  );
});

test("screenshot sanity rejects undersized or predominantly black evidence", () => {
  assert.doesNotThrow(() => assertScreenshotSanity({
    width: 1280,
    height: 900,
    byte_size: 120000,
    near_black_ratio: 0.01,
    transparent_ratio: 0,
  }, "healthy.png"));
  assert.throws(
    () => assertScreenshotSanity({
      width: 1280,
      height: 900,
      byte_size: 120000,
      near_black_ratio: 0.45,
      transparent_ratio: 0,
    }, "black.png"),
    /screenshot sanity failed/,
  );
  assert.throws(
    () => assertScreenshotSanity({
      width: 1,
      height: 1,
      byte_size: 67,
      near_black_ratio: 0,
      transparent_ratio: 0,
    }, "empty.png"),
    /screenshot sanity failed/,
  );
  assert.throws(
    () => assertScreenshotSanity({
      width: 1280,
      height: 900,
      byte_size: 120000,
      near_black_ratio: 0,
      transparent_ratio: 0.45,
    }, "transparent.png"),
    /screenshot sanity failed/,
  );
});

test("chat Electron smoke covers historical image retry as a non-destructive new turn", () => {
  const smokeSource = fs.readFileSync(
    path.join(__dirname, "chat_artifact_electron_smoke.js"),
    "utf8",
  );

  assert.match(smokeSource, /uiartifacthistoricalretry/);
  assert.match(smokeSource, /historical_retry_as_new_message/);
  assert.match(smokeSource, /historicalRetryTruncateRequests\.length === 0/);
});
