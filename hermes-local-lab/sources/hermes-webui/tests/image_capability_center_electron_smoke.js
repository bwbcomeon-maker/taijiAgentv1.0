#!/usr/bin/env node
/*
 * Real Electron acceptance for the visible Image Capability Center.
 *
 * The desktop shell, WebUI, settings navigation, DOM, keyboard focus, and
 * reload path are production code. The two image-capability HTTP endpoints
 * are intercepted inside Electron's BrowserContext so no Provider, OAuth
 * flow, system browser, or public network is contacted.
 */
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const {
  assertNavigationParity,
  buildAcceptanceProvenance,
  captureAuditedScreenshot,
  collectSourceFingerprint,
  inspectTaijiNavigation,
  installDailyEquivalentRuntimeConfig,
} = require("./electron_acceptance_provenance");

function parseArgs(argv) {
  const result = { outDir: "" };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--out-dir") result.outDir = argv[index + 1] || "";
  }
  if (!result.outDir) throw new Error("--out-dir is required");
  return result;
}

function loadPlaywright() {
  const moduleId = process.env.PLAYWRIGHT_NODE_PATH || "playwright";
  try {
    return require(moduleId);
  } catch (error) {
    throw new Error(`Cannot resolve Playwright from ${moduleId}`, { cause: error });
  }
}

function assertState(condition, message, detail) {
  if (!condition) {
    throw new Error(`${message}${detail ? `\n${JSON.stringify(detail, null, 2)}` : ""}`);
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function pgrepElectron() {
  const result = spawnSync("pgrep", ["-x", "Electron"], { encoding: "utf8" });
  if (result.status !== 0 && result.status !== 1) return [];
  return String(result.stdout || "")
    .split(/\s+/)
    .filter(Boolean)
    .map(Number)
    .filter(Number.isFinite);
}

function pidAlive(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (_) {
    return false;
  }
}

async function waitForPidsToExit(pids, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (pids.every(pid => !pidAlive(pid))) return true;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  return pids.every(pid => !pidAlive(pid));
}

async function terminateOwnedPids(pids) {
  const owned = [...new Set(pids.filter(pid => Number.isFinite(pid) && pid > 0))];
  const term = [];
  for (const pid of owned) {
    if (!pidAlive(pid)) continue;
    try {
      process.kill(pid, "SIGTERM");
      term.push(pid);
    } catch (_) {}
  }
  if (await waitForPidsToExit(owned, 5000)) return { term, kill: [] };
  const kill = [];
  for (const pid of owned) {
    if (!pidAlive(pid)) continue;
    try {
      process.kill(pid, "SIGKILL");
      kill.push(pid);
    } catch (_) {}
  }
  assertState(
    await waitForPidsToExit(owned, 5000),
    "owned fixture processes survived cleanup",
    { owned },
  );
  return { term, kill };
}

function readPid(file) {
  try {
    const value = Number(fs.readFileSync(file, "utf8").trim());
    return Number.isFinite(value) ? value : 0;
  } catch (_) {
    return 0;
  }
}

function providerFamilyFor(state, capability, providerId) {
  for (const provider of state.providers || []) {
    const capabilities = Array.isArray(provider.capabilities) ? provider.capabilities : [];
    if (!capabilities.includes(capability)) continue;
    const providerIds = provider.provider_ids || {};
    if (String(providerIds[capability] || "") === String(providerId || "")) {
      return String(provider.provider_family || "");
    }
  }
  return "";
}

function configuredResponse(state, payload, requestLog) {
  const next = clone(state);
  const updates = Array.isArray(payload.credential_updates)
    ? payload.credential_updates
    : [];
  const sanitizedUpdates = updates.map(update => ({
    id: String(update.id || ""),
    provider_family: String(update.provider_family || ""),
    label: String(update.label || ""),
    operation: String(update.operation || ""),
    source_capability: String(update.source_capability || ""),
    source_provider_id: String(update.source_provider_id || ""),
    api_key_present: Boolean(String(update.api_key || "")),
    api_key_length: String(update.api_key || "").length,
  }));
  requestLog.push({
    expected_revision: String(payload.expected_revision || ""),
    request_id: String(payload.request_id || ""),
    capabilities: clone(payload.capabilities || {}),
    credential_updates: sanitizedUpdates,
    verify: clone(payload.verify || []),
  });

  for (const update of updates) {
    const id = String(update.id || "");
    const family = String(update.provider_family || "");
    if (!id || !family || !String(update.api_key || "")) {
      return {
        status: 400,
        body: {
          error: "新增命名凭据需要名称、Provider family 和 API Key。",
          error_code: "invalid_credential_update",
        },
      };
    }
    const existing = next.provider_credentials.find(row => String(row.id) === id);
    const safe = {
      id,
      provider_family: family,
      label: String(update.label || id),
      default: Boolean(update.default),
      configured: true,
      managed_by: "image-capability-center",
    };
    if (existing) Object.assign(existing, safe);
    else next.provider_credentials.push(safe);
  }

  for (const capability of ["vision", "image_generation"]) {
    const draft = payload.capabilities && payload.capabilities[capability];
    if (!draft) continue;
    const expectedFamily = providerFamilyFor(
      next,
      capability,
      String(draft.provider || ""),
    );
    const credentialRef = String(draft.credential_ref || "");
    const credential = credentialRef
      ? next.provider_credentials.find(row => String(row.id) === credentialRef)
      : null;
    if (
      credential
      && expectedFamily
      && String(credential.provider_family || "") !== expectedFamily
    ) {
      return {
        status: 400,
        body: {
          error: "所选凭据不属于当前 Provider，请选择同一平台的凭据或新建凭据。",
          error_code: "credential_family_mismatch",
        },
      };
    }
    next.capabilities[capability] = {
      enabled: Boolean(draft.enabled),
      provider: String(draft.provider || ""),
      model: String(draft.model || ""),
      credential_ref: credentialRef,
      endpoint_values: clone(draft.endpoint_values || {}),
      verification: {
        status: draft.enabled ? "verified" : "disabled",
      },
    };
  }

  next.revision = "b".repeat(64);
  next.effective_route = {
    vision: {
      route: next.capabilities.vision.enabled ? "auxiliary_vision" : "disabled",
      provider: next.capabilities.vision.provider,
      model: next.capabilities.vision.model,
    },
    image_generation: {
      route: next.capabilities.image_generation.enabled
        ? "image_generation_provider"
        : "disabled",
      provider: next.capabilities.image_generation.provider,
      model: next.capabilities.image_generation.model,
    },
  };
  next.verification_results = {
    vision: { status: next.capabilities.vision.verification.status },
    image_generation: {
      status: next.capabilities.image_generation.verification.status,
    },
  };
  return { status: 200, body: next };
}

function initialFixtureState() {
  return {
    ok: true,
    profile: "electron-fixture",
    revision: "a".repeat(64),
    capabilities: {
      vision: {
        enabled: true,
        provider: "alibaba",
        model: "qwen3-vl-plus",
        credential_ref: "alibaba-default",
        endpoint_values: {},
        verification: { status: "configured_unverified" },
      },
      image_generation: {
        enabled: false,
        provider: "dashscope",
        model: "qwen-image-2.0-pro",
        credential_ref: "alibaba-default",
        endpoint_values: {},
        verification: { status: "disabled" },
      },
    },
    providers: [
      {
        provider_family: "alibaba_dashscope",
        label: "阿里云百炼",
        capabilities: ["vision", "image_generation"],
        provider_ids: {
          vision: "alibaba",
          image_generation: "dashscope",
        },
        auth_type: "api_key",
        support_level: "native",
        supports_named_credentials: true,
        selectable: true,
        models: {
          vision: [{ id: "qwen3-vl-plus", label: "Qwen3 VL Plus" }],
          image_generation: [
            { id: "qwen-image-2.0-pro", label: "Qwen Image 2.0 Pro" },
          ],
        },
        default_models: {
          vision: "qwen3-vl-plus",
          image_generation: "qwen-image-2.0-pro",
        },
        credential_fields: {
          vision: [{ name: "api_key", label: "API Key", secret: true }],
          image_generation: [
            { name: "api_key", label: "API Key", secret: true },
          ],
        },
        endpoint_fields: { vision: [], image_generation: [] },
      },
      {
        provider_family: "zhipu",
        label: "智谱 AI",
        capabilities: ["vision", "image_generation"],
        provider_ids: {
          vision: "zai",
          image_generation: "zhipu-image",
        },
        auth_type: "api_key",
        support_level: "native",
        supports_named_credentials: true,
        selectable: true,
        models: {
          vision: [{ id: "glm-5v-turbo", label: "GLM-5V Turbo" }],
          image_generation: [{ id: "glm-image", label: "GLM-Image" }],
        },
        default_models: {
          vision: "glm-5v-turbo",
          image_generation: "glm-image",
        },
        credential_fields: {
          vision: [{ name: "api_key", label: "API Key", secret: true }],
          image_generation: [
            { name: "api_key", label: "API Key", secret: true },
          ],
        },
        endpoint_fields: { vision: [], image_generation: [] },
      },
      {
        provider_family: "doubao",
        label: "火山方舟",
        capabilities: ["image_generation"],
        provider_ids: { image_generation: "doubao" },
        auth_type: "api_key",
        support_level: "native",
        supports_named_credentials: true,
        selectable: true,
        models: {
          image_generation: [
            {
              id: "doubao-seedream-5-0-260128",
              label: "Doubao Seedream",
            },
          ],
        },
        default_models: {
          image_generation: "doubao-seedream-5-0-260128",
        },
        credential_fields: {
          image_generation: [
            { name: "api_key", label: "API Key", secret: true },
          ],
        },
        endpoint_fields: { image_generation: [] },
      },
    ],
    provider_credentials: [
      {
        id: "alibaba-default",
        provider_family: "alibaba_dashscope",
        label: "阿里默认凭据",
        default: true,
        configured: true,
      },
      {
        id: "zhipu-shared",
        provider_family: "zhipu",
        label: "智谱共享凭据",
        default: false,
        configured: true,
      },
      {
        id: "doubao-shared",
        provider_family: "doubao",
        label: "豆包共享凭据",
        default: false,
        configured: true,
      },
    ],
    effective_route: {
      vision: {
        route: "auxiliary_vision",
        provider: "alibaba",
        model: "qwen3-vl-plus",
      },
      image_generation: {
        route: "disabled",
        provider: "dashscope",
        model: "qwen-image-2.0-pro",
      },
    },
  };
}

function sanitizedLaunchEnv(base, dirs, {
  agentDir,
  labDir,
  pythonBin,
  workspace,
}) {
  const env = { ...base };
  for (const key of Object.keys(env)) {
    if (
      key.endsWith("_API_KEY")
      || /(?:^|_)(?:OAUTH|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN|CLIENT_SECRET)$/.test(key)
    ) {
      delete env[key];
    }
  }
  return {
    ...env,
    HOME: dirs.home,
    XDG_CONFIG_HOME: dirs.config,
    XDG_DATA_HOME: dirs.data,
    XDG_STATE_HOME: dirs.state,
    HERMES_HOME: dirs.runtimeHome,
    HERMES_BASE_HOME: dirs.runtimeHome,
    HERMES_WEBUI_STATE_DIR: path.join(dirs.runtimeHome, "web"),
    TAIJI_AGENT_ROOT: labDir,
    TAIJI_AGENT_USE_USER_DIRS: "1",
    TAIJI_DESKTOP_USER_DATA_DIR: dirs.userData,
    TAIJI_RUNTIME_HOME: dirs.runtimeHome,
    TAIJI_WORKSPACE: workspace,
    TAIJI_AGENT_PYTHON: pythonBin,
    TAIJI_WEBUI_PYTHON: pythonBin,
    HERMES_WEBUI_AGENT_DIR: agentDir,
    TAIJI_LICENSE_REQUIRED: "0",
    TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: "0",
    TAIJI_AGENT_SYNC_PACKAGED_CONFIG: "0",
    HTTP_PROXY: "http://127.0.0.1:9",
    HTTPS_PROXY: "http://127.0.0.1:9",
    ALL_PROXY: "http://127.0.0.1:9",
    NO_PROXY: "127.0.0.1,localhost",
  };
}

async function waitForDesktopReady(page) {
  await page.waitForLoadState("domcontentloaded", { timeout: 120000 });
  try {
    await page.waitForFunction(
      () => (
        document.readyState === "complete"
        && location.href.includes("taiji_desktop=1")
        && typeof switchPanel === "function"
        && typeof switchSettingsSection === "function"
        && typeof window.loadImageCapabilityCenter === "function"
        && Boolean(document.getElementById("imageCapabilityCenter"))
      ),
      null,
      { timeout: 120000 },
    );
  } catch (error) {
    const snapshot = await page.evaluate(() => ({
      url: location.href,
      ready_state: document.readyState,
      switch_panel: typeof switchPanel,
      switch_settings_section: typeof switchSettingsSection,
      image_loader: typeof window.loadImageCapabilityCenter,
      image_center: Boolean(document.getElementById("imageCapabilityCenter")),
      title: document.title,
      body_text: String(document.body?.innerText || "").slice(0, 500),
    })).catch(snapshotError => ({
      snapshot_error: String(snapshotError && snapshotError.message
        ? snapshotError.message
        : snapshotError),
    }));
    throw new Error(
      `desktop settings UI did not become ready\n${JSON.stringify(snapshot, null, 2)}`,
      { cause: error },
    );
  }
  await page.evaluate(() => {
    try {
      localStorage.setItem("hermes-lang", "zh");
    } catch (_) {}
    if (typeof setLanguage === "function") setLanguage("zh");
    const onboarding = document.getElementById("onboardingOverlay");
    if (onboarding) onboarding.remove();
  });
}

async function openModelsByKeyboard(page) {
  const settings = page.locator('.taiji-nav-item[data-taiji-panel="settings"]');
  await settings.waitFor({ state: "visible", timeout: 20000 });
  await settings.focus();
  await page.keyboard.press("Enter");
  const models = page.locator('#settingsMenu [data-settings-section="models"]');
  await models.waitFor({ state: "visible", timeout: 20000 });
  await models.focus();
  await page.keyboard.press("Enter");
  const center = page.locator("#imageCapabilityCenter");
  await center.waitFor({ state: "visible", timeout: 30000 });
  await page.waitForFunction(
    () => document.getElementById("imageCapabilityCenter")?.dataset.state === "ready",
    { timeout: 30000 },
  );
  await center.scrollIntoViewIfNeeded();
}

async function main() {
  const cli = parseArgs(process.argv.slice(2));
  const { _electron } = loadPlaywright();
  const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
  const mainRepo = path.resolve(repoRoot, "..", "..");
  const webuiDir = path.join(repoRoot, "hermes-local-lab", "sources", "hermes-webui");
  const agentDir = path.join(repoRoot, "hermes-local-lab", "sources", "hermes-agent");
  const labDir = path.join(repoRoot, "hermes-local-lab");
  const appDir = path.join(repoRoot, "apps", "taiji-desktop");
  const electronBin = process.env.TAIJI_ELECTRON_BIN || path.join(
    mainRepo,
    "apps",
    "taiji-desktop",
    "node_modules",
    "electron",
    "dist",
    "Electron.app",
    "Contents",
    "MacOS",
    "Electron",
  );
  const pythonBin = process.env.TAIJI_TEST_PYTHON
    || path.join(agentDir, "venv", "bin", "python");
  assertState(fs.existsSync(electronBin), "Electron binary missing", { electronBin });
  assertState(fs.existsSync(pythonBin), "Python runtime missing", { pythonBin });

  const outDir = path.resolve(cli.outDir);
  fs.mkdirSync(outDir, { recursive: true });
  const harnessRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), "taiji-image-capability-electron-"),
  );
  const dirs = {
    runtimeHome: path.join(harnessRoot, "runtime"),
    workspace: path.join(harnessRoot, "workspace"),
    userData: path.join(harnessRoot, "user-data"),
    home: path.join(harnessRoot, "home"),
    config: path.join(harnessRoot, "config"),
    data: path.join(harnessRoot, "data"),
    state: path.join(harnessRoot, "state"),
  };
  for (const directory of Object.values(dirs)) {
    fs.mkdirSync(directory, { recursive: true });
  }
  fs.writeFileSync(
    path.join(dirs.workspace, "README.md"),
    "# Isolated Image Capability Electron Fixture\n",
    "utf8",
  );

  const runtimeConfig = installDailyEquivalentRuntimeConfig(dirs.runtimeHome);
  const sourceFingerprint = collectSourceFingerprint({ repoRoot, webuiDir });
  Object.assign(sourceFingerprint, {
    acceptance_script_sha256: sha256File(__filename),
    api_model_config_sha256: sha256File(path.join(webuiDir, "api", "model_config.py")),
    desktop_main_sha256: sha256File(path.join(appDir, "src", "main.js")),
  });

  let fixtureState = initialFixtureState();
  const configureRequests = [];
  const apiRequests = [];
  const externalRequests = [];
  const popupUrls = [];
  const pageErrors = [];
  const consoleErrors = [];
  const baselineElectronPids = pgrepElectron();
  const pidFiles = {
    agent: path.join(dirs.state, "taiji-agent", "logs", "agent.pid"),
    web: path.join(dirs.state, "taiji-agent", "logs", "web.pid"),
  };
  const launches = [];
  const screenshotSanity = {};
  let app = null;
  let navigationParity = null;
  let result = null;
  let runError = null;
  let cleanup = null;

  try {
    app = await _electron.launch({
      executablePath: electronBin,
      args: [appDir],
      env: sanitizedLaunchEnv(process.env, dirs, {
        agentDir,
        labDir,
        pythonBin,
        workspace: dirs.workspace,
      }),
      timeout: 120000,
    });
    const appPid = app.process().pid;
    const context = app.context();
    await context.route("**/*", async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === "/api/image-capabilities") {
        apiRequests.push({ method: request.method(), path: url.pathname });
        if (request.method() !== "GET") {
          await route.fulfill({
            status: 405,
            contentType: "application/json",
            body: JSON.stringify({ error: "method not allowed" }),
          });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(fixtureState),
        });
        return;
      }
      if (url.pathname === "/api/image-capabilities/configure") {
        apiRequests.push({ method: request.method(), path: url.pathname });
        let payload = {};
        try {
          payload = request.postDataJSON();
        } catch (_) {}
        const response = configuredResponse(
          fixtureState,
          payload,
          configureRequests,
        );
        if (response.status === 200) fixtureState = clone(response.body);
        await route.fulfill({
          status: response.status,
          contentType: "application/json",
          body: JSON.stringify(response.body),
        });
        return;
      }
      if (
        ["http:", "https:"].includes(url.protocol)
        && !["127.0.0.1", "localhost"].includes(url.hostname)
      ) {
        externalRequests.push({
          method: request.method(),
          url: request.url(),
          resource_type: request.resourceType(),
        });
        await route.fulfill({
          status: 599,
          contentType: "text/plain",
          body: "External network is disabled in Electron acceptance.",
        });
        return;
      }
      await route.continue();
    });

    const page = await app.firstWindow({ timeout: 120000 });
    page.on("pageerror", error => {
      pageErrors.push(String(error && error.message ? error.message : error));
    });
    page.on("console", message => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("popup", popup => {
      popupUrls.push(popup.url());
      void popup.close().catch(() => {});
    });
    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
    await waitForDesktopReady(page);
    launches.push({
      electron: appPid,
      agent: readPid(pidFiles.agent),
      web: readPid(pidFiles.web),
    });

    navigationParity = await inspectTaijiNavigation(page);
    assertNavigationParity(navigationParity);
    assertState(
      JSON.stringify(navigationParity.ui_visibility)
        === JSON.stringify(runtimeConfig.feature_visibility),
      "runtime feature visibility differs from isolated fixture",
      {
        expected: runtimeConfig.feature_visibility,
        actual: navigationParity.ui_visibility,
      },
    );

    await openModelsByKeyboard(page);
    const visionProvider = page.locator("#imageCapabilityVisionProvider");
    const generationProvider = page.locator("#imageCapabilityGenerationProvider");
    await visionProvider.selectOption("zai");
    await generationProvider.selectOption("zhipu-image");

    const visionSecret = page.locator("#imageCapabilityVisionCredentialField0");
    const generationSecret = page.locator(
      "#imageCapabilityGenerationCredentialField0",
    );
    for (const [name, input] of [
      ["vision", visionSecret],
      ["image_generation", generationSecret],
    ]) {
      await input.waitFor({ state: "visible", timeout: 10000 });
      assertState((await input.getAttribute("type")) === "password", `${name} secret is not a password input`);
      assertState((await input.getAttribute("data-secret-field")) === "true", `${name} secret is not marked as secret`);
      assertState((await input.getAttribute("autocomplete")) === "off", `${name} secret autocomplete is not disabled`);
      const labelled = await input.evaluate(node => {
        const label = document.querySelector(`label[for="${node.id}"]`);
        return Boolean(label && label.textContent.trim());
      });
      assertState(labelled, `${name} secret is not discoverable through a visible label`);
      await input.focus();
      assertState(await input.evaluate(node => document.activeElement === node), `${name} secret is not focusable`);
    }

    const visionCredential = page.locator("#imageCapabilityVisionCredential");
    const visionOptions = await visionCredential.locator("option").evaluateAll(
      options => options.map(option => ({
        value: option.value,
        text: option.textContent.trim(),
        disabled: option.disabled,
      })),
    );
    assertState(
      visionOptions.some(option => (
        option.value === "zhipu-shared"
        && option.text.includes("智谱共享凭据")
        && !option.disabled
      )),
      "same-family shared credential is not visible/selectable",
      visionOptions,
    );
    assertState(
      !visionOptions.some(option => option.value === "doubao-shared"),
      "mismatched-family shared credential leaked into the visible selector",
      visionOptions,
    );
    await visionCredential.selectOption("zhipu-shared");
    assertState(
      await visionSecret.count() === 0,
      "selecting a shared credential did not remove the new-secret field",
    );
    assertState(
      (await page.locator("#imageCapabilityVisionCredentialFields").innerText())
        .includes("已选择命名凭据"),
      "shared credential selection did not expose an understandable status",
    );

    await generationSecret.focus();
    await page.keyboard.type("fixture-zhipu-image-secret");
    assertState(
      (await generationSecret.inputValue()) === "fixture-zhipu-image-secret",
      "keyboard entry did not reach the image-generation password input",
    );
    const generationSwitch = page.locator("#imageCapabilityGenerationEnabled");
    await generationSwitch.focus();
    await page.keyboard.press("Space");
    assertState(await generationSwitch.isChecked(), "keyboard did not enable image generation");

    screenshotSanity["01-zhipu-named-credential.png"] =
      await captureAuditedScreenshot(
        page,
        outDir,
        "01-zhipu-named-credential.png",
      );

    const save = page.locator("#btnSaveVerifyImageCapabilityCenter");
    await save.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(
      () => (
        document.getElementById("imageCapabilityCenterStatusTitle")
          ?.textContent.includes("图片能力已保存并完成验证")
      ),
      { timeout: 30000 },
    );
    assertState(configureRequests.length === 1, "happy-path save request count is not one", configureRequests);
    const happyRequest = configureRequests[0];
    assertState(
      happyRequest.capabilities.vision.provider === "zai"
        && happyRequest.capabilities.vision.credential_ref === "zhipu-shared",
      "vision did not bind the selected shared credential",
      happyRequest,
    );
    assertState(
      happyRequest.capabilities.image_generation.provider === "zhipu-image",
      "image generation did not bind zhipu-image",
      happyRequest,
    );
    assertState(
      happyRequest.credential_updates.length === 1
        && happyRequest.credential_updates[0].provider_family === "zhipu"
        && happyRequest.credential_updates[0].api_key_present
        && happyRequest.credential_updates[0].api_key_length > 0,
      "image-generation named credential was not created",
      happyRequest,
    );
    const createdCredentialId = happyRequest.credential_updates[0].id;
    assertState(
      Boolean(createdCredentialId)
        && fixtureState.capabilities.image_generation.credential_ref
          === createdCredentialId,
      "created credential was not persisted in the fixture response",
      { createdCredentialId, fixtureState },
    );

    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
    await waitForDesktopReady(page);
    await openModelsByKeyboard(page);
    assertState(
      (await visionProvider.inputValue()) === "zai"
        && (await visionCredential.inputValue()) === "zhipu-shared",
      "shared vision credential binding was not restored after reload",
    );
    assertState(
      (await generationProvider.inputValue()) === "zhipu-image"
        && (await page.locator("#imageCapabilityGenerationCredential").inputValue())
          === createdCredentialId,
      "created image-generation credential binding was not restored after reload",
    );
    assertState(
      await page.locator("#imageCapabilityGenerationCredentialField0").count() === 0,
      "saved secret field was unexpectedly redisplayed after reload",
    );

    fixtureState.revision = "c".repeat(64);
    fixtureState.capabilities.image_generation.credential_ref = "doubao-shared";
    await page.locator("#btnReloadImageCapabilityCenter").click();
    await page.waitForFunction(
      () => (
        document.getElementById("imageCapabilityGenerationCredential")?.value
          === "doubao-shared"
      ),
      { timeout: 10000 },
    );
    const mismatchOption = page.locator(
      '#imageCapabilityGenerationCredential option[value="doubao-shared"]',
    );
    assertState(
      await mismatchOption.isDisabled()
        && (await mismatchOption.innerText()).includes("当前配置"),
      "legacy mismatched binding was not rendered as a disabled current value",
    );
    await page.locator("#btnSaveVerifyImageCapabilityCenter").click();
    await page.waitForFunction(
      () => (
        document.getElementById("imageCapabilityCenterError")
          ?.textContent.includes("所选凭据不属于当前 Provider")
      ),
      { timeout: 10000 },
    );
    assertState(
      (await page.locator("#imageCapabilityCenterStatusTitle").innerText())
        .includes("图片能力保存失败"),
      "family mismatch did not expose an understandable failure title",
    );
    await page.locator("#imageCapabilityCenterError").scrollIntoViewIfNeeded();
    screenshotSanity["02-family-mismatch.png"] =
      await captureAuditedScreenshot(
        page,
        outDir,
        "02-family-mismatch.png",
      );

    await page.setViewportSize({ width: 640, height: 900 });
    await page.locator("#imageCapabilityCenter").scrollIntoViewIfNeeded();
    const narrow = await page.evaluate(() => {
      const center = document.getElementById("imageCapabilityCenter");
      const vision = document.querySelector('[data-image-capability="vision"]');
      const generation = document.querySelector(
        '[data-image-capability="image_generation"]',
      );
      const centerRect = center.getBoundingClientRect();
      const visionRect = vision.getBoundingClientRect();
      const generationRect = generation.getBoundingClientRect();
      return {
        viewport: document.documentElement.clientWidth,
        document_scroll_width: document.documentElement.scrollWidth,
        center_left: centerRect.left,
        center_right: centerRect.right,
        vision_bottom: visionRect.bottom,
        generation_top: generationRect.top,
        save_visible: Boolean(
          document.getElementById("btnSaveVerifyImageCapabilityCenter")
            ?.getClientRects().length,
        ),
      };
    });
    assertState(
      narrow.center_left >= -1
        && narrow.center_right <= narrow.viewport + 1
        && narrow.generation_top >= narrow.vision_bottom - 4
        && narrow.save_visible,
      "image capability center is not usable in a 640px Electron viewport",
      narrow,
    );
    screenshotSanity["03-narrow.png"] =
      await captureAuditedScreenshot(
        page,
        outDir,
        "03-narrow.png",
      );

    assertState(externalRequests.length === 0, "public network request escaped the fixture", externalRequests);
    assertState(popupUrls.length === 0, "Electron opened an unexpected popup or OAuth window", popupUrls);
    assertState(pageErrors.length === 0, "page JavaScript error occurred", pageErrors);
    result = {
      status: "passed",
      scope: "real Electron + production visible settings UI + in-memory image capability API fixture",
      provider_network: "blocked; no OAuth, public network, or real Provider verification",
      acceptance_provenance: buildAcceptanceProvenance({
        sourceFingerprint,
        runtimeConfig,
        navigationParity,
      }),
      source_fingerprint: sourceFingerprint,
      screenshots: screenshotSanity,
      checks: {
        visible_settings_entry_opened_by_keyboard: true,
        zai_password_visible_labelled_focusable: true,
        zhipu_image_password_visible_labelled_focusable: true,
        shared_credential_selected: "zhipu-shared",
        named_credential_created: createdCredentialId,
        reload_binding_restored: true,
        saved_secret_not_redisplayed: true,
        credential_family_mismatch_rejected: true,
        keyboard_secret_entry_and_save: true,
        narrow_layout: narrow,
        api_request_count: apiRequests.length,
        configure_requests: configureRequests,
        external_requests: externalRequests,
        popup_urls: popupUrls,
        page_errors: pageErrors,
        console_errors: consoleErrors,
      },
      pid_ownership: {
        baseline_electron_pids: baselineElectronPids,
        launches,
      },
    };
  } catch (error) {
    runError = error;
  } finally {
    if (app) await app.close().catch(() => {});
    launches.push({
      electron: 0,
      agent: readPid(pidFiles.agent),
      web: readPid(pidFiles.web),
    });
    const ownedPids = [
      ...new Set(
        launches
          .flatMap(item => Object.values(item))
          .filter(pid => Number.isFinite(pid) && pid > 0),
      ),
    ];
    cleanup = await terminateOwnedPids(ownedPids).catch(error => ({
      error: String(error && error.message ? error.message : error),
    }));
    const survivors = ownedPids.filter(pidAlive);
    if (!runError && survivors.length) {
      runError = new Error(`owned fixture processes survived cleanup: ${survivors.join(",")}`);
    }
    if (result) {
      result.pid_ownership.owned_pids = ownedPids;
      result.pid_ownership.owned_pids_alive_after_cleanup = survivors;
      result.cleanup = {
        ...cleanup,
        strategy: "Electron close, then TERM/KILL only PIDs recorded from this isolated launch",
      };
    }
    fs.rmSync(harnessRoot, { recursive: true, force: true });
  }

  if (runError) throw runError;
  assertState(result, "Electron acceptance did not produce a result");
  fs.writeFileSync(
    path.join(outDir, "electron-image-capability-center-result.json"),
    JSON.stringify(result, null, 2),
  );
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

main().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
