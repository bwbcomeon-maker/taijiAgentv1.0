const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const {
  EXPECTED_FAMILY_MISMATCH_CONSOLE,
  assertExactGuardEvents,
  assertExpectedConsoleErrors,
  assertFormalMainSource,
  assertStableSource,
  assertTcpListenerOwner,
  collectLiveGuardProcessIdentities,
  createFixtureController,
  gitSnapshot,
  initialFixtureState,
  isBaselineProcess,
  writeHarnessGuards,
} = require("./image_capability_center_electron_smoke");

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
  const result = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    env: cleanGitEnv(),
  });
  assert.equal(
    result.status,
    0,
    `git ${args.join(" ")} failed: ${String(result.stderr || "").trim()}`,
  );
  return String(result.stdout || "").trim();
}

function createGitRepo(base, name, branch) {
  const repo = path.join(base, name);
  fs.mkdirSync(repo, { recursive: true });
  git(repo, "init", "-q", "-b", branch);
  git(repo, "config", "user.email", `${name}@example.invalid`);
  git(repo, "config", "user.name", name);
  fs.writeFileSync(path.join(repo, "README.md"), `${name}\n`);
  git(repo, "add", "README.md");
  git(repo, "commit", "-qm", "fixture");
  return repo;
}

function configurePayload(state, requestId, overrides = {}) {
  return {
    expected_revision: state.revision,
    request_id: requestId,
    capabilities: {
      vision: {
        enabled: true,
        provider: "zai",
        model: "glm-5v-turbo",
        credential_ref: "zhipu-shared",
        endpoint_values: {},
      },
    },
    credential_updates: [],
    verify: ["vision"],
    ...overrides,
  };
}

test("fixture enforces revision CAS and replays only the same UUID payload", () => {
  const initial = initialFixtureState();
  const controller = createFixtureController(initial);
  const requestId = "11111111-1111-4111-8111-111111111111";
  const payload = configurePayload(initial, requestId);

  const first = controller.configure(payload);
  assert.equal(first.status, 200);
  assert.notEqual(first.body.revision, initial.revision);
  const replay = controller.configure(payload);
  assert.deepEqual(replay, first);

  const conflict = controller.configure({
    ...payload,
    capabilities: {
      ...payload.capabilities,
      vision: {
        ...payload.capabilities.vision,
        model: "glm-5v-flash",
      },
    },
  });
  assert.equal(conflict.status, 400);
  assert.equal(conflict.body.error_code, "request_id_conflict");

  const stale = controller.configure(configurePayload(
    initial,
    "22222222-2222-4222-8222-222222222222",
  ));
  assert.equal(stale.status, 409);
  assert.equal(stale.body.error_code, "configuration_conflict");

  const second = controller.configure(configurePayload(
    first.body,
    "33333333-3333-4333-8333-333333333333",
  ));
  assert.equal(second.status, 200);
  assert.notEqual(second.body.revision, first.body.revision);
  const staleSecondRevision = controller.configure(configurePayload(
    first.body,
    "44444444-4444-4444-8444-444444444444",
  ));
  assert.equal(staleSecondRevision.status, 409);
  assert.equal(
    staleSecondRevision.body.error_code,
    "configuration_conflict",
  );
  assert.equal(controller.read().revision, second.body.revision);
});

test("gitSnapshot ignores ambient Git locators and formal source gate is mandatory", () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-electron-git-gate-"));
  const original = Object.fromEntries(
    AMBIENT_GIT_ENV.map(name => [name, process.env[name]]),
  );
  try {
    const selected = createGitRepo(base, "selected", "main");
    const polluted = createGitRepo(base, "polluted", "wrong");
    process.env.GIT_DIR = path.join(polluted, ".git");
    process.env.GIT_WORK_TREE = polluted;
    process.env.GIT_COMMON_DIR = path.join(polluted, ".git");
    process.env.GIT_INDEX_FILE = path.join(polluted, ".git", "index");
    process.env.GIT_OBJECT_DIRECTORY = path.join(
      polluted,
      ".git",
      "objects",
    );
    process.env.GIT_ALTERNATE_OBJECT_DIRECTORIES = path.join(
      polluted,
      ".git",
      "objects",
    );

    const snapshot = gitSnapshot(selected);
    const fingerprint = {
      branch: "main",
      commit: snapshot.commit,
      dirty: false,
      checkout_type: "formal_main_primary_worktree",
    };
    assert.equal(snapshot.repo_root, fs.realpathSync(selected));
    assert.equal(snapshot.branch, "main");
    assert.doesNotThrow(() => assertFormalMainSource(snapshot, fingerprint));
    assert.throws(
      () => assertFormalMainSource(snapshot, {
        ...fingerprint,
        checkout_type: "linked_worktree",
      }),
      /formal main/i,
    );
    assert.throws(
      () => assertFormalMainSource(
        { ...snapshot, status_short: " M README.md" },
        fingerprint,
      ),
      /clean/i,
    );
  } finally {
    for (const name of AMBIENT_GIT_ENV) {
      if (original[name] === undefined) delete process.env[name];
      else process.env[name] = original[name];
    }
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("early startup failure removes prior canonical and temporary passed results", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-old-result-"));
  const resultFile = path.join(
    outDir,
    "electron-image-capability-center-result.json",
  );
  const staleTemporary = `${resultFile}.tmp-123`;
  try {
    fs.writeFileSync(resultFile, '{"status":"passed"}\n');
    fs.writeFileSync(staleTemporary, '{"status":"passed"}\n');
    const launched = spawnSync(
      process.execPath,
      [
        path.join(__dirname, "image_capability_center_electron_smoke.js"),
        "--out-dir",
        outDir,
      ],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          PLAYWRIGHT_NODE_PATH: path.join(outDir, "missing-playwright"),
        },
      },
    );
    assert.notEqual(launched.status, 0);
    assert.equal(fs.existsSync(resultFile), false);
    assert.equal(fs.existsSync(staleTemporary), false);
  } finally {
    fs.rmSync(outDir, { recursive: true, force: true });
  }
});

test("desktop page listener must be exclusively owned by the verified WebUI PID", () => {
  assert.deepEqual(
    assertTcpListenerOwner(18787, 42, [42]),
    { port: 18787, pid: 42, listener_pids: [42] },
  );
  assert.throws(
    () => assertTcpListenerOwner(18787, 42, [41]),
    /verified WebUI PID/,
  );
  assert.throws(
    () => assertTcpListenerOwner(18787, 42, [41, 42]),
    /verified WebUI PID/,
  );
});

test("live guard-loaded process with an untrusted identity is rejected without signalling", () => {
  const cwd = fs.realpathSync(process.cwd());
  assert.throws(
    () => collectLiveGuardProcessIdentities(
      [{
        pid: process.pid,
        role: "agent",
        type: "python_guard_loaded",
        marker_sha256: "a".repeat(64),
        cwd_sha256: crypto.createHash("sha256").update(cwd).digest("hex"),
        executable_sha256: "b".repeat(64),
      }],
      {
        baselineTable: new Map(),
        markerSha256: "a".repeat(64),
        agentDir: cwd,
        webuiDir: cwd,
        pythonEntryPath: "/definitely/not/the/current/node",
        pythonExecutableSha256: "b".repeat(64),
      },
    ),
    /not safe to own/,
  );
  assert.equal(process.kill(process.pid, 0), true);
});

test("final console gate rejects every error beyond the exact mismatch 400", () => {
  assert.doesNotThrow(() => assertExpectedConsoleErrors([
    EXPECTED_FAMILY_MISMATCH_CONSOLE,
  ]));
  assert.throws(
    () => assertExpectedConsoleErrors([
      EXPECTED_FAMILY_MISMATCH_CONSOLE,
      "late renderer failure",
    ]),
    /console/i,
  );
  assert.throws(
    () => assertExpectedConsoleErrors([]),
    /console/i,
  );
});

test("guard gate requires an exact PID, role, type, and target whitelist", () => {
  const marker = crypto.createHash("sha256").update("marker").digest("hex");
  const publicTarget = crypto.createHash("sha256")
    .update("203.0.113.1:9")
    .digest("hex");
  const canaryTarget = crypto.createHash("sha256")
    .update("https://example.invalid/oauth?state=canary")
    .digest("hex");
  const agentCwd = crypto.createHash("sha256").update("/agent").digest("hex");
  const webCwd = crypto.createHash("sha256").update("/web").digest("hex");
  const executable = crypto.createHash("sha256").update("/python").digest("hex");
  const events = [
    { pid: 10, type: "electron_guard_loaded", marker_sha256: marker },
    {
      pid: 10,
      type: "main_network_blocked",
      marker_sha256: marker,
      target_sha256: publicTarget,
    },
    {
      pid: 10,
      type: "shell_external_blocked",
      marker_sha256: marker,
      method: "openExternal",
      target_sha256: canaryTarget,
    },
    {
      pid: 10,
      type: "window_open_blocked",
      marker_sha256: marker,
      target_sha256: canaryTarget,
    },
    ...[["agent", 20, agentCwd], ["web", 30, webCwd]]
      .flatMap(([role, pid, cwdSha256]) => [
      {
        pid,
        role,
        type: "python_guard_loaded",
        marker_sha256: marker,
        cwd_sha256: cwdSha256,
        executable_sha256: executable,
      },
      {
        pid,
        role,
        type: "python_network_blocked",
        marker_sha256: marker,
        target_sha256: publicTarget,
      },
      {
        pid,
        role,
        type: "python_guard_self_test_blocked",
        marker_sha256: marker,
        target_sha256: publicTarget,
      },
    ]),
  ];
  const expected = {
    appPid: 10,
    agentPid: 20,
    webPid: 30,
    markerSha256: marker,
    publicTargetSha256: publicTarget,
    canaryTargetSha256: canaryTarget,
    agentCwdSha256: agentCwd,
    webCwdSha256: webCwd,
    pythonExecutableSha256: executable,
  };
  const laterOwnedLoad = {
    pid: 40,
    role: "agent",
    type: "python_guard_loaded",
    marker_sha256: marker,
    cwd_sha256: agentCwd,
    executable_sha256: executable,
  };

  assert.doesNotThrow(() => assertExactGuardEvents(events, expected));
  assert.doesNotThrow(
    () => assertExactGuardEvents([...events, laterOwnedLoad], expected),
  );
  for (const adversarial of [
    [...events, {
      pid: 10,
      type: "navigation_blocked",
      marker_sha256: marker,
      target_sha256: canaryTarget,
    }],
    [...events, events.find(event => event.type === "main_network_blocked")],
    events.map(event => (
      event.type === "window_open_blocked"
        ? { ...event, target_sha256: publicTarget }
        : event
    )),
    [...events, { ...laterOwnedLoad, cwd_sha256: webCwd }],
    [...events, laterOwnedLoad, laterOwnedLoad],
    [...events, {
      pid: 40,
      role: "agent",
      type: "python_network_blocked",
      marker_sha256: marker,
      target_sha256: publicTarget,
    }],
  ]) {
    assert.throws(
      () => assertExactGuardEvents(adversarial, expected),
      /guard event/i,
    );
  }
});

test("terminal source gate rejects late HEAD, dirty-state, and key hash drift", () => {
  const snapshot = {
    repo_root: "/tmp/formal-main",
    branch: "main",
    commit: "a".repeat(40),
    status_short: "",
    common_dir: "/tmp/formal-main/.git",
  };
  const fingerprint = {
    branch: "main",
    commit: snapshot.commit,
    dirty: false,
    checkout_type: "formal_main_primary_worktree",
    static_files_sha256: { "index.html": "b".repeat(64) },
    api_model_config_sha256: "c".repeat(64),
    desktop_main_sha256: "d".repeat(64),
    desktop_preload_sha256: "e".repeat(64),
  };
  assert.doesNotThrow(() => assertStableSource(
    snapshot,
    fingerprint,
    { ...snapshot },
    JSON.parse(JSON.stringify(fingerprint)),
  ));
  assert.throws(
    () => assertStableSource(
      snapshot,
      fingerprint,
      { ...snapshot, commit: "f".repeat(40) },
      JSON.parse(JSON.stringify(fingerprint)),
    ),
    /source.*changed/i,
  );
  assert.throws(
    () => assertStableSource(
      snapshot,
      fingerprint,
      { ...snapshot, status_short: " M late-change" },
      JSON.parse(JSON.stringify(fingerprint)),
    ),
    /source.*changed/i,
  );
  assert.throws(
    () => assertStableSource(
      snapshot,
      fingerprint,
      { ...snapshot },
      {
        ...JSON.parse(JSON.stringify(fingerprint)),
        desktop_main_sha256: "0".repeat(64),
      },
    ),
    /source.*changed/i,
  );
});

test("baseline comparison distinguishes PID reuse from the original identity", () => {
  const baseline = {
    pid: 42,
    started: "Mon Jul 20 10:00:00 2026",
    command_sha256: "a".repeat(64),
  };
  assert.equal(isBaselineProcess(baseline, { ...baseline }), true);
  assert.equal(isBaselineProcess(baseline, {
    ...baseline,
    started: "Mon Jul 20 10:01:00 2026",
  }), false);
});

test("generated Python guard wrapper is POSIX-valid and classifies only service entrypoints", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-python-guard-role-"));
  try {
    const fakePython = path.join(root, "fake-python");
    fs.writeFileSync(
      fakePython,
      "#!/bin/sh\nprintf '%s' \"$TAIJI_PYTHON_GUARD_ROLE\"\n",
      { mode: 0o700 },
    );
    const guard = writeHarnessGuards({
      harnessRoot: root,
      productMain: path.join(root, "product-main.js"),
      pythonBin: fakePython,
      guardMarker: "marker",
    });
    assert.equal(spawnSync("sh", ["-n", guard.pythonWrapper]).status, 0);
    const role = args => String(
      spawnSync(guard.pythonWrapper, args, { encoding: "utf8" }).stdout || "",
    );
    assert.equal(role(["-m", "taiji_runtime.main", "gateway", "run"]), "agent");
    assert.equal(role(["/tmp/server.py"]), "web");
    assert.equal(
      role(["-c", "launcher", "taiji_runtime.main", "gateway", "run"]),
      "bootstrap",
    );
    assert.equal(role(["-m", "taiji_runtime.main", "--help"]), "bootstrap");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
