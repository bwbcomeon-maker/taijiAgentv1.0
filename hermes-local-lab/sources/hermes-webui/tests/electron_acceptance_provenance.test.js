const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  DAILY_EQUIVALENT_FEATURE_VISIBILITY,
  DAILY_EQUIVALENT_VISIBLE_NAV,
  assertNavigationParity,
  assertScreenshotSanity,
  collectSourceFingerprint,
  installDailyEquivalentRuntimeConfig,
} = require("./electron_acceptance_provenance");

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

test("source fingerprint records branch, commit, dirty state, and selected static sha256 values", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-parity-source-test-"));
  const staticDir = path.join(root, "static");
  fs.mkdirSync(staticDir, { recursive: true });
  fs.writeFileSync(path.join(staticDir, "index.html"), "<main>fixture</main>\n");
  fs.writeFileSync(path.join(staticDir, "ui.js"), "window.fixture=true;\n");

  try {
    const fingerprint = collectSourceFingerprint({
      repoRoot: path.resolve(__dirname, "..", "..", "..", ".."),
      webuiDir: root,
      staticFiles: ["index.html", "ui.js"],
    });
    assert.match(fingerprint.branch, /\S+/);
    assert.match(fingerprint.commit, /^[0-9a-f]{40}$/);
    assert.equal(typeof fingerprint.dirty, "boolean");
    assert.deepEqual(Object.keys(fingerprint.static_files_sha256), ["index.html", "ui.js"]);
    for (const digest of Object.values(fingerprint.static_files_sha256)) {
      assert.match(digest, /^[0-9a-f]{64}$/);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
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
