const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const DAILY_EQUIVALENT_FEATURE_VISIBILITY = Object.freeze({
  nav: Object.freeze({
    chat: true,
    tasks: true,
    kanban: false,
    writing: true,
    skills: false,
    memory: false,
    workspaces: false,
    profiles: true,
    todos: false,
    insights: false,
    logs: false,
    settings: true,
  }),
  settings_sections: Object.freeze({
    conversation: true,
    appearance: false,
    preferences: false,
    models: true,
    providers: true,
    plugins: false,
    system: true,
    about: true,
  }),
  composer: Object.freeze({
    profile: false,
    workspace_files: false,
    workspace_switcher: false,
    model: true,
    reasoning: true,
    toolsets: false,
    quota: false,
  }),
  chat: Object.freeze({
    activity_details: false,
  }),
});

// Effective product-shell navigation. Runtime mode may independently suppress
// profiles even though the feature projection permits it; the visible contract
// is what the parity acceptance must match.
const DAILY_EQUIVALENT_VISIBLE_NAV = Object.freeze([
  "chat",
  "tasks",
  "writing",
  "settings",
]);

const DEFAULT_STATIC_FILES = Object.freeze([
  "index.html",
  "boot.js",
  "ui.js",
  "taiji-home.js",
  "messages.js",
  "style.css",
]);

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function yamlBoolean(value) {
  return value ? "true" : "false";
}

function buildDailyEquivalentConfigYaml(featureVisibility) {
  const lines = ["webui:", "  feature_visibility:"];
  for (const [group, values] of Object.entries(featureVisibility)) {
    lines.push(`    ${group}:`);
    for (const [key, value] of Object.entries(values)) {
      lines.push(`      ${key}: ${yamlBoolean(value)}`);
    }
  }
  return `${lines.join("\n")}\n`;
}

function installDailyEquivalentRuntimeConfig(runtimeHome, { capability_overrides = {} } = {}) {
  const featureVisibility = cloneJson(DAILY_EQUIVALENT_FEATURE_VISIBILITY);
  for (const [group, values] of Object.entries(capability_overrides)) {
    if (!featureVisibility[group] || !values || typeof values !== "object") {
      throw new Error(`unsupported feature visibility override group: ${group}`);
    }
    for (const [key, value] of Object.entries(values)) {
      if (!Object.prototype.hasOwnProperty.call(featureVisibility[group], key) || typeof value !== "boolean") {
        throw new Error(`unsupported feature visibility override: ${group}.${key}`);
      }
      featureVisibility[group][key] = value;
    }
  }
  const hasOverrides = Object.keys(capability_overrides).length > 0;
  fs.mkdirSync(runtimeHome, { recursive: true });
  fs.writeFileSync(
    path.join(runtimeHome, "config.yaml"),
    buildDailyEquivalentConfigYaml(featureVisibility),
    { encoding: "utf8", mode: 0o600 },
  );
  return {
    source_type: hasOverrides
      ? "sanitized_daily_nav_equivalent_fixture"
      : "sanitized_daily_equivalent_fixture",
    feature_visibility: featureVisibility,
    expected_visible_nav: [...DAILY_EQUIVALENT_VISIBLE_NAV],
    ...(hasOverrides ? { capability_overrides: cloneJson(capability_overrides) } : {}),
  };
}

function gitValue(repoRoot, args) {
  const env = { ...process.env };
  for (const name of [
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  ]) {
    delete env[name];
  }
  const result = spawnSync("git", args, {
    cwd: repoRoot,
    encoding: "utf8",
    env,
  });
  if (result.status !== 0) {
    throw new Error(`git ${args.join(" ")} failed: ${String(result.stderr || "").trim()}`);
  }
  return String(result.stdout || "").trim();
}

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function collectSourceFingerprint({ repoRoot, webuiDir, staticFiles = DEFAULT_STATIC_FILES }) {
  const hashes = {};
  for (const relativeName of staticFiles) {
    const file = path.join(webuiDir, "static", relativeName);
    if (!fs.existsSync(file)) throw new Error(`static fingerprint input missing: ${relativeName}`);
    hashes[relativeName] = sha256File(file);
  }
  const branch = gitValue(repoRoot, ["branch", "--show-current"]);
  const gitDir = path.resolve(repoRoot, gitValue(repoRoot, ["rev-parse", "--git-dir"]));
  const gitCommonDir = path.resolve(repoRoot, gitValue(repoRoot, ["rev-parse", "--git-common-dir"]));
  const primaryWorktree = gitDir === gitCommonDir;
  const checkoutType = primaryWorktree
    ? (branch === "main"
      ? "formal_main_primary_worktree"
      : (branch ? "primary_non_main" : "detached_primary_worktree"))
    : "linked_worktree";
  return {
    branch,
    commit: gitValue(repoRoot, ["rev-parse", "HEAD"]),
    dirty: Boolean(gitValue(repoRoot, ["status", "--porcelain"])),
    checkout_type: checkoutType,
    static_files_sha256: hashes,
  };
}

function buildAcceptanceProvenance({ sourceFingerprint, runtimeConfig, navigationParity }) {
  if (!sourceFingerprint || !sourceFingerprint.checkout_type) {
    throw new Error("source fingerprint checkout_type is required");
  }
  return {
    source: sourceFingerprint,
    desktop_app_source: sourceFingerprint.checkout_type,
    runtime_config: runtimeConfig,
    user_data: { type: "isolated_temporary" },
    navigation: navigationParity,
  };
}

async function inspectTaijiNavigation(page) {
  return page.evaluate(() => {
    const items = Array.from(document.querySelectorAll(".taiji-brand-nav [data-taiji-panel]"));
    const isVisible = element => {
      const style = getComputedStyle(element);
      return !element.hidden
        && element.getAttribute("aria-hidden") !== "true"
        && style.display !== "none"
        && style.visibility !== "hidden";
    };
    const all = items.map(element => element.dataset.taijiPanel);
    const visible = items.filter(isVisible).map(element => element.dataset.taijiPanel);
    return {
      all,
      visible,
      hidden: all.filter(panel => !visible.includes(panel)),
      single_runtime: Boolean(window.S && S.singleRuntime),
      ui_visibility: cloneForAcceptance(window._uiVisibility),
    };

    function cloneForAcceptance(value) {
      if (!value || typeof value !== "object") return null;
      return JSON.parse(JSON.stringify(value));
    }
  });
}

function assertNavigationParity(snapshot) {
  const actual = Array.isArray(snapshot && snapshot.visible) ? snapshot.visible : [];
  const expected = [...DAILY_EQUIVALENT_VISIBLE_NAV];
  const matches = actual.length === expected.length
    && actual.every((value, index) => value === expected[index]);
  if (!matches) {
    throw new Error(`navigation parity mismatch\n${JSON.stringify({
      expected,
      actual,
      single_runtime: snapshot && snapshot.single_runtime,
      all: snapshot && snapshot.all,
      hidden: snapshot && snapshot.hidden,
    }, null, 2)}`);
  }
}

function assertScreenshotSanity(metadata, name = "screenshot") {
  const failures = [];
  if (!Number.isFinite(metadata && metadata.width) || metadata.width < 600) failures.push("width");
  if (!Number.isFinite(metadata && metadata.height) || metadata.height < 600) failures.push("height");
  if (!Number.isFinite(metadata && metadata.byte_size) || metadata.byte_size < 10000) failures.push("byte_size");
  if (!Number.isFinite(metadata && metadata.near_black_ratio) || metadata.near_black_ratio > 0.2) {
    failures.push("near_black_ratio");
  }
  if (!Number.isFinite(metadata && metadata.transparent_ratio) || metadata.transparent_ratio > 0.01) {
    failures.push("transparent_ratio");
  }
  if (failures.length) {
    throw new Error(`screenshot sanity failed for ${name}: ${failures.join(", ")}\n${JSON.stringify(metadata, null, 2)}`);
  }
}

async function captureAuditedScreenshot(page, outDir, name) {
  const file = path.join(outDir, name);
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    if (attempt > 1) {
      await page.evaluate(() => new Promise(resolve => {
        requestAnimationFrame(() => requestAnimationFrame(resolve));
      }));
      await page.waitForTimeout(500);
    }
    const png = await page.screenshot({
      path: file,
      animations: "disabled",
      omitBackground: false,
      scale: "css",
    });
    const decoded = await page.evaluate(async base64 => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    if (typeof image.decode === "function") await image.decode();
    else await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = reject;
    });
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const step = Math.max(1, Math.floor(Math.sqrt((canvas.width * canvas.height) / 200000)));
    let sampled = 0;
    let nearBlack = 0;
    let transparent = 0;
    for (let y = 0; y < canvas.height; y += step) {
      for (let x = 0; x < canvas.width; x += step) {
        const offset = ((y * canvas.width) + x) * 4;
        sampled += 1;
        if (pixels[offset + 3] < 240) transparent += 1;
        if (
          pixels[offset + 3] >= 240
          && pixels[offset] <= 12
          && pixels[offset + 1] <= 12
          && pixels[offset + 2] <= 12
        ) {
          nearBlack += 1;
        }
      }
    }
    return {
      width: canvas.width,
      height: canvas.height,
      sampled_pixels: sampled,
      near_black_pixels: nearBlack,
      near_black_ratio: sampled ? nearBlack / sampled : 1,
      transparent_pixels: transparent,
      transparent_ratio: sampled ? transparent / sampled : 1,
    };
    }, png.toString("base64"));
    const previewName = name.replace(/\.png$/i, "") + ".preview.jpg";
    const previewFile = path.join(outDir, previewName);
    const previewResult = spawnSync("/usr/bin/sips", [
      "-s", "format", "jpeg",
      "-s", "formatOptions", "85",
      file,
      "--out", previewFile,
    ], { encoding: "utf8" });
    if (previewResult.status !== 0 || !fs.existsSync(previewFile)) {
      throw new Error(`human preview conversion failed for ${name}: ${String(previewResult.stderr || "").trim()}`);
    }
    const jpegPreview = fs.readFileSync(previewFile);
    const metadata = {
      ...decoded,
      byte_size: png.length,
      sha256: crypto.createHash("sha256").update(png).digest("hex"),
      capture_attempts: attempt,
      capture_scale: "css",
      human_preview: {
        file: previewName,
        encoder: "macos_imageio_sips",
        byte_size: jpegPreview.length,
        sha256: crypto.createHash("sha256").update(jpegPreview).digest("hex"),
      },
    };
    try {
      assertScreenshotSanity(metadata, name);
      return metadata;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

module.exports = {
  DAILY_EQUIVALENT_FEATURE_VISIBILITY,
  DAILY_EQUIVALENT_VISIBLE_NAV,
  assertNavigationParity,
  assertScreenshotSanity,
  buildAcceptanceProvenance,
  captureAuditedScreenshot,
  collectSourceFingerprint,
  inspectTaijiNavigation,
  installDailyEquivalentRuntimeConfig,
};
