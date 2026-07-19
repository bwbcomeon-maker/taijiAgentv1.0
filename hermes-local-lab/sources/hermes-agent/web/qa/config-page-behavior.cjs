"use strict";

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const net = require("node:net");
const path = require("node:path");

function loadPlaywright() {
  try {
    return require("playwright");
  } catch (localError) {
    const moduleRoot = process.env.CODEX_NODE_MODULES;
    if (!moduleRoot) {
      throw new Error(
        "Playwright is not installed locally. Set CODEX_NODE_MODULES to a " +
          "node_modules directory containing playwright.",
        { cause: localError },
      );
    }
    return require(path.join(moduleRoot, "playwright"));
  }
}

const { chromium } = loadPlaywright();
const webRoot = path.resolve(__dirname, "..");

const schemaResponse = {
  fields: {
    "agent.max_turns": {
      category: "agent",
      description: "Maximum turns",
      type: "number",
    },
  },
  category_order: ["agent"],
};

function configDraft(value, token = `draft-${value}`) {
  return {
    config: {
      agent: { max_turns: value },
      model: "test/model",
      model_context_length: 0,
    },
    snapshot_token: token,
  };
}

const defaultsResponse = {
  agent: { max_turns: 20 },
  model: "test/model",
};

const statusResponse = {
  active_sessions: 0,
  auth_required: false,
  auth_providers: [],
  config_path: "/tmp/hermes-qa/config.yaml",
  config_version: 1,
  env_path: "/tmp/hermes-qa/.env",
  gateway_exit_reason: null,
  gateway_health_url: null,
  gateway_pid: null,
  gateway_platforms: {},
  gateway_running: false,
  gateway_state: "stopped",
  gateway_updated_at: null,
  hermes_home: "/tmp/hermes-qa",
  latest_config_version: 1,
  release_date: "",
  version: "qa",
};

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function waitForServer(url, processHandle) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (processHandle.exitCode !== null) {
      throw new Error(`Vite exited early with code ${processHandle.exitCode}`);
    }
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // Vite has not bound the port yet.
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function installApiMock(page, scenario) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}`;
    if (scenario[key]) {
      await scenario[key](route, request);
      return;
    }
    if (key === "GET /api/config/defaults") {
      await json(route, defaultsResponse);
      return;
    }
    if (key === "GET /api/status") {
      await json(route, statusResponse);
      return;
    }
    if (key === "GET /api/dashboard/plugins") {
      await json(route, []);
      return;
    }
    if (key === "GET /api/auth/me") {
      await json(route, { detail: "loopback" }, 401);
      return;
    }
    await json(route, { detail: `Unhandled QA route: ${key}` }, 404);
  });
}

async function openConfig(browser, baseUrl, scenario) {
  const context = await browser.newContext({ locale: "en-US" });
  const page = await context.newPage();
  await installApiMock(page, scenario);
  await page.goto(`${baseUrl}/config`, { waitUntil: "domcontentloaded" });
  return { context, page };
}

function initialSuccessScenario(value = 10) {
  return {
    "GET /api/config/draft": (route) => json(route, configDraft(value)),
    "GET /api/config/schema": (route) => json(route, schemaResponse),
  };
}

async function maxTurnsInput(page) {
  const input = page.locator('input[type="number"]').first();
  await input.waitFor({ state: "visible" });
  return input;
}

async function testInitialDependencyFailures(browser, baseUrl) {
  for (const failedEndpoint of ["draft", "schema"]) {
    let shouldFail = true;
    const scenario = {
      "GET /api/config/draft": (route) => {
        if (failedEndpoint === "draft" && shouldFail) {
          shouldFail = false;
          return json(route, { detail: "draft failed" }, 500);
        }
        return json(route, configDraft(10));
      },
      "GET /api/config/schema": (route) => {
        if (failedEndpoint === "schema" && shouldFail) {
          shouldFail = false;
          return json(route, { detail: "schema failed" }, 500);
        }
        return json(route, schemaResponse);
      },
    };
    const { context, page } = await openConfig(browser, baseUrl, scenario);
    await page.getByRole("alert").waitFor();
    await page.getByRole("button", {
      name: "Retry loading configuration",
    }).click();
    const input = await maxTurnsInput(page);
    assert.equal(await input.inputValue(), "10");
    await context.close();
  }
}

async function testConflictCancelAndConfirm(browser, baseUrl) {
  let draftReads = 0;
  const scenario = {
    ...initialSuccessScenario(),
    "GET /api/config/draft": (route) => {
      draftReads += 1;
      return json(route, configDraft(draftReads === 1 ? 10 : 99));
    },
    "PUT /api/config": (route) =>
      json(
        route,
        {
          error_code: "configuration_conflict",
          message: "Configuration changed",
          path: ["agent", "max_turns"],
        },
        409,
      ),
  };
  const { context, page } = await openConfig(browser, baseUrl, scenario);
  const input = await maxTurnsInput(page);
  await input.fill("42");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", {
    name: "Reload and discard local changes",
  }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(draftReads, 1, "cancel must not reload the draft");
  assert.equal(await input.inputValue(), "42");

  await page.getByRole("button", {
    name: "Reload and discard local changes",
  }).click();
  await page.getByRole("button", {
    name: "Reload & discard",
    exact: true,
  }).click();
  await page.waitForFunction(
    () =>
      document.querySelector('input[type="number"]')?.value === "99",
  );
  assert.equal(draftReads, 2);
  await context.close();
}

async function testRawTransportThenPolicy(browser, baseUrl) {
  let rawReads = 0;
  const scenario = {
    ...initialSuccessScenario(),
    "GET /api/config/raw": (route) => {
      rawReads += 1;
      if (rawReads === 1) {
        return json(route, { detail: "transport failed" }, 500);
      }
      return json(
        route,
        rawReads === 2
          ? {
              yaml: "",
              snapshot_token: null,
              editable: false,
              blocked_code: "literal_credentials",
              blocked_reason: "server fallback must not be the main copy",
            }
          : {
              yaml: "agent:\n  max_turns: 10\n",
              snapshot_token: "raw-rechecked",
              editable: true,
              blocked_code: null,
              blocked_reason: null,
            },
      );
    },
  };
  const { context, page } = await openConfig(browser, baseUrl, scenario);
  await maxTurnsInput(page);
  await page.getByRole("button", { name: "YAML", exact: true }).click();
  await page.getByText("Failed to load raw config", { exact: true }).waitFor();
  await page.getByRole("button", {
    name: "Retry loading raw YAML",
  }).click();
  await page
    .getByText(
      "Raw YAML editing is disabled because config.yaml contains literal credential values. Move them to environment references before using the Web editor.",
      { exact: true },
    )
    .waitFor();
  assert.equal(
    await page.locator('textarea[aria-label="Raw YAML Configuration"]').count(),
    0,
  );
  assert.equal(
    await page.getByRole("button", { name: "Save", exact: true }).count(),
    0,
  );
  await page.getByRole("button", {
    name: "Retry loading raw YAML",
  }).click();
  await page
    .locator('textarea[aria-label="Raw YAML Configuration"]')
    .waitFor({ state: "visible" });
  assert.equal(rawReads, 3, "policy block must be re-checkable in place");
  await context.close();
}

async function testSaveRefreshFailure(browser, baseUrl) {
  let draftReads = 0;
  const scenario = {
    ...initialSuccessScenario(),
    "GET /api/config/draft": (route) => {
      draftReads += 1;
      if (draftReads === 1) return json(route, configDraft(10));
      return json(route, { detail: "refresh failed" }, 500);
    },
    "PUT /api/config": (route) => json(route, { ok: true }),
  };
  const { context, page } = await openConfig(browser, baseUrl, scenario);
  const input = await maxTurnsInput(page);
  await input.fill("55");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", {
    name: "Reload and discard local changes",
  }).waitFor();
  assert.equal(await input.inputValue(), "55");
  await page.getByText(/Configuration saved/).first().waitFor();
  assert.equal(await page.getByText(/Failed to save/).count(), 0);
  await context.close();
}

async function testConcurrentEditSurvivesSave(browser, baseUrl) {
  let draftReads = 0;
  const savedPayloads = [];
  let signalPutStarted;
  const putStarted = new Promise((resolve) => {
    signalPutStarted = resolve;
  });
  const scenario = {
    ...initialSuccessScenario(),
    "GET /api/config/draft": (route) => {
      draftReads += 1;
      if (draftReads === 1) return json(route, configDraft(10, "draft-10"));
      if (draftReads === 2) return json(route, configDraft(41, "draft-41"));
      return json(route, configDraft(42, "draft-42"));
    },
    "PUT /api/config": async (route, request) => {
      savedPayloads.push(request.postDataJSON());
      if (savedPayloads.length === 1) {
        signalPutStarted();
        await sleep(350);
      }
      await json(route, { ok: true });
    },
  };
  const { context, page } = await openConfig(browser, baseUrl, scenario);
  const input = await maxTurnsInput(page);
  await input.fill("41");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await putStarted;
  await input.fill("42");
  const saveButton = page.getByRole("button", { name: "Save", exact: true });
  await saveButton.waitFor();
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll("button")].find(
      (candidate) => candidate.textContent?.trim() === "Save",
    );
    return button && !button.disabled;
  });
  assert.equal(await input.inputValue(), "42");
  assert.equal(draftReads, 2, "post-save refresh must acquire fresh authority");
  await saveButton.click();
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll("button")].find(
      (candidate) => candidate.textContent?.trim() === "Save",
    );
    return button && !button.disabled;
  });
  assert.deepEqual(savedPayloads, [
    {
      config: configDraft(41).config,
      snapshot_token: "draft-10",
    },
    {
      config: configDraft(42).config,
      snapshot_token: "draft-41",
    },
  ]);
  await context.close();
}

async function testYamlDraftSurvivesModeRoundTrip(browser, baseUrl) {
  let rawReads = 0;
  let savedPayload;
  let signalRawSave;
  const rawSaveStarted = new Promise((resolve) => {
    signalRawSave = resolve;
  });
  const scenario = {
    ...initialSuccessScenario(),
    "GET /api/config/raw": (route) => {
      rawReads += 1;
      return json(route, {
        yaml: "revision: server\n",
        snapshot_token: `raw-${rawReads}`,
        editable: true,
        blocked_code: null,
        blocked_reason: null,
      });
    },
    "PUT /api/config/raw": async (route, request) => {
      savedPayload = request.postDataJSON();
      signalRawSave();
      await json(route, { ok: true });
    },
  };
  const { context, page } = await openConfig(browser, baseUrl, scenario);
  await maxTurnsInput(page);
  await page.getByRole("button", { name: "YAML", exact: true }).click();
  const editor = page.locator(
    'textarea[aria-label="Raw YAML Configuration"]',
  );
  await editor.waitFor({ state: "visible" });
  await editor.fill("revision: local-draft\n");

  await page.getByRole("button", { name: "Form", exact: true }).click();
  await page.getByRole("button", { name: "YAML", exact: true }).click();
  await editor.waitFor({ state: "visible" });
  assert.equal(
    await editor.inputValue(),
    "revision: local-draft\n",
    "returning to YAML must preserve the unsaved editor draft",
  );
  assert.equal(rawReads, 1, "returning to YAML must not refetch the draft");

  await page.getByRole("button", { name: "Save", exact: true }).click();
  await rawSaveStarted;
  assert.deepEqual(savedPayload, {
    yaml_text: "revision: local-draft\n",
    snapshot_token: "raw-1",
  });
  await context.close();
}

async function testRapidYamlToggleIgnoresOldResponse(browser, baseUrl) {
  let rawReads = 0;
  let signalFirstRawStarted;
  const firstRawStarted = new Promise((resolve) => {
    signalFirstRawStarted = resolve;
  });
  const scenario = {
    ...initialSuccessScenario(),
    "GET /api/config/raw": async (route) => {
      rawReads += 1;
      if (rawReads === 1) {
        signalFirstRawStarted();
        await sleep(450);
        await json(route, {
          yaml: "revision: first\n",
          snapshot_token: "raw-first",
          editable: true,
          blocked_code: null,
          blocked_reason: null,
        });
        return;
      }
      await json(route, {
        yaml: "revision: second\n",
        snapshot_token: "raw-second",
        editable: true,
        blocked_code: null,
        blocked_reason: null,
      });
    },
  };
  const { context, page } = await openConfig(browser, baseUrl, scenario);
  await maxTurnsInput(page);
  await page.getByRole("button", { name: "YAML", exact: true }).click();
  await firstRawStarted;
  await page.getByRole("button", { name: "Form", exact: true }).click();
  await page.getByRole("button", { name: "YAML", exact: true }).click();
  const editor = page.locator(
    'textarea[aria-label="Raw YAML Configuration"]',
  );
  await editor.waitFor({ state: "visible" });
  assert.equal(await editor.inputValue(), "revision: second\n");
  await sleep(600);
  assert.equal(
    await editor.inputValue(),
    "revision: second\n",
    "the first delayed response must not overwrite the newer draft",
  );
  await context.close();
}

async function main() {
  const port = await freePort();
  assert.notEqual(port, 18643, "QA must never use the protected runtime port");
  const baseUrl = `http://127.0.0.1:${port}`;
  const vite = spawn(
    "npm",
    [
      "run",
      "dev",
      "--",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    {
      cwd: webRoot,
      detached: true,
      env: { ...process.env, BROWSER: "none" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let viteOutput = "";
  vite.stdout.on("data", (chunk) => {
    viteOutput += chunk.toString();
  });
  vite.stderr.on("data", (chunk) => {
    viteOutput += chunk.toString();
  });

  let browser;
  try {
    await waitForServer(baseUrl, vite);
    browser = await chromium.launch({ headless: true });
    await testInitialDependencyFailures(browser, baseUrl);
    await testConflictCancelAndConfirm(browser, baseUrl);
    await testRawTransportThenPolicy(browser, baseUrl);
    await testSaveRefreshFailure(browser, baseUrl);
    await testConcurrentEditSurvivesSave(browser, baseUrl);
    await testYamlDraftSurvivesModeRoundTrip(browser, baseUrl);
    await testRapidYamlToggleIgnoresOldResponse(browser, baseUrl);
    console.log(
      "config-page-behavior: 8 scenarios passed " +
        "(draft/schema failure, conflict, raw transport/policy, " +
        "refresh failure, concurrent edit, YAML draft round trip, " +
        "rapid YAML toggle)",
    );
  } catch (error) {
    if (viteOutput) {
      console.error(viteOutput);
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    if (vite.pid && vite.exitCode === null) {
      try {
        process.kill(-vite.pid, "SIGTERM");
      } catch {
        vite.kill("SIGTERM");
      }
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
