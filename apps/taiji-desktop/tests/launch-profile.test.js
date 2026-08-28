const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const modulePath = path.join(__dirname, "..", "src", "launch-profile.js");
const FULL_COMMIT = "0123456789abcdef0123456789abcdef01234567";

function loadLaunchProfile() {
  assert.equal(
    fs.existsSync(modulePath),
    true,
    "launch-profile.js must provide the testable launch identity contract",
  );
  return require(modulePath);
}

function createInstalledTree(t, manifestOverrides = {}, { writeManifest = true } = {}) {
  const installRoot = fs.realpathSync(
    fs.mkdtempSync(path.join(os.tmpdir(), "taiji-installed-profile-")),
  );
  const appPath = path.join(installRoot, "apps", "taiji-desktop");
  const resources = path.join(installRoot, "resources");
  const manifestPath = path.join(resources, "taiji-release-manifest.json");
  const agentDir = path.join(installRoot, "runtime", "agent");
  const webuiDir = path.join(installRoot, "runtime", "web");
  const pythonPath = path.join(agentDir, "venv", "bin", "python");
  fs.mkdirSync(appPath, { recursive: true });
  fs.mkdirSync(resources, { recursive: true });
  fs.mkdirSync(path.dirname(pythonPath), { recursive: true });
  fs.mkdirSync(webuiDir, { recursive: true });
  fs.writeFileSync(pythonPath, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  const manifest = {
    schema: "taiji-release-manifest/v1",
    platform: "linux",
    arch: "amd64",
    version: "1.2.3",
    commit: FULL_COMMIT,
    installRoot,
    ...manifestOverrides,
  };
  if (writeManifest) {
    fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`, { mode: 0o644 });
  }
  t.after(() => fs.rmSync(installRoot, { recursive: true, force: true }));
  return { installRoot, appPath, manifestPath, agentDir, webuiDir, pythonPath, manifest };
}

function resolveInstalled(t, manifestOverrides = {}, fixtureOptions = {}) {
  const { resolveLaunchProfile } = loadLaunchProfile();
  const fixture = createInstalledTree(t, manifestOverrides, fixtureOptions);
  return {
    ...fixture,
    resolve: (overrides = {}) => resolveLaunchProfile({
      env: { TAIJI_LAUNCH_PROFILE: "installed-production" },
      appPath: fixture.appPath,
      platform: "linux",
      arch: "x64",
      installRoot: fixture.installRoot,
      expectedManifestUid: typeof process.getuid === "function" ? process.getuid() : 0,
      ...overrides,
    }),
  };
}

test("trusted installed tree without Git resolves installed-production", (t) => {
  const fixture = resolveInstalled(t);
  assert.equal(fs.existsSync(path.join(fixture.installRoot, ".git")), false);
  const profile = fixture.resolve({ env: {} });
  assert.equal(profile.kind, "installed-production");
  assert.equal(profile.installRoot, fixture.installRoot);
  assert.deepEqual(profile.release, {
    version: "1.2.3",
    commit: FULL_COMMIT,
  });
});

test("a physical installed app reached through an external symlink remains installed-production", (t) => {
  const fixture = resolveInstalled(t);
  const aliasParent = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-installed-alias-"));
  const alias = path.join(aliasParent, "desktop-alias");
  fs.symlinkSync(fixture.appPath, alias, "dir");
  t.after(() => fs.rmSync(aliasParent, { recursive: true, force: true }));

  const profile = fixture.resolve({
    appPath: alias,
    env: { TAIJI_LAUNCH_PROFILE: "source", TAIJI_SOURCE_MODE: "development" },
  });
  assert.equal(profile.kind, "installed-production");
});

test("installed-production environment request without a manifest fails closed", (t) => {
  const fixture = resolveInstalled(t, {}, { writeManifest: false });
  assert.throws(() => fixture.resolve(), /manifest/i);
});

test("installed-production rejects an app path outside the fixed install root", (t) => {
  const fixture = resolveInstalled(t);
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-outside-app-"));
  t.after(() => fs.rmSync(outside, { recursive: true, force: true }));
  assert.throws(() => fixture.resolve({ appPath: outside }), /app path|install root/i);
});

for (const [label, overrides, expected] of [
  ["schema", { schema: "taiji-release-manifest/v2" }, /schema/i],
  ["platform", { platform: "darwin" }, /platform/i],
  ["arch", { arch: "arm64" }, /arch/i],
  ["version", { version: "" }, /version/i],
  ["commit", { commit: "" }, /commit/i],
  ["installRoot", { installRoot: "/opt/not-taiji" }, /install root/i],
]) {
  test(`installed-production rejects mismatched ${label}`, (t) => {
    const fixture = resolveInstalled(t, overrides);
    assert.throws(() => fixture.resolve(), expected);
  });
}

for (const [label, overrides, expected] of [
  ["object version", { version: { major: 1 } }, /version/i],
  ["array version", { version: [1, 2, 3] }, /version/i],
  ["non-SemVer version", { version: "01.2.3" }, /version/i],
  ["space-padded version", { version: "1.2.3 " }, /version/i],
  ["object commit", { commit: { sha: FULL_COMMIT } }, /commit/i],
  ["array commit", { commit: [FULL_COMMIT] }, /commit/i],
  ["short commit", { commit: "01234567" }, /commit/i],
  ["space-padded commit", { commit: ` ${FULL_COMMIT}` }, /commit/i],
  ["uppercase commit", { commit: FULL_COMMIT.toUpperCase() }, /commit/i],
  ["non-hex commit", { commit: `${FULL_COMMIT.slice(0, -1)}z` }, /commit/i],
]) {
  test(`installed-production rejects ${label}`, (t) => {
    const fixture = resolveInstalled(t, overrides);
    assert.throws(() => fixture.resolve(), expected);
  });
}

test("installed-production rejects a symlinked release manifest", (t) => {
  const fixture = resolveInstalled(t);
  const target = path.join(fixture.installRoot, "resources", "manifest-target.json");
  fs.renameSync(fixture.manifestPath, target);
  fs.symlinkSync(target, fixture.manifestPath);
  assert.throws(() => fixture.resolve(), /symlink|regular file/i);
});

test("installed-production rejects a hardlinked release manifest", (t) => {
  const fixture = resolveInstalled(t);
  const secondLink = path.join(fixture.installRoot, "resources", "manifest-hardlink.json");
  fs.linkSync(fixture.manifestPath, secondLink);
  assert.throws(() => fixture.resolve(), /hardlink|link count/i);
});

test("installed-production requires a root-owned release manifest", (t) => {
  const fixture = resolveInstalled(t);
  const currentUid = typeof process.getuid === "function" ? process.getuid() : 0;
  assert.throws(
    () => fixture.resolve({ expectedManifestUid: currentUid + 1 }),
    /owner|uid/i,
  );
});

test("installed-production rejects a group-writable release manifest", (t) => {
  const fixture = resolveInstalled(t);
  fs.chmodSync(fixture.manifestPath, 0o664);
  assert.throws(() => fixture.resolve(), /writable|permission|mode/i);
});

test("installed-production rejects an oversized release manifest", (t) => {
  const fixture = resolveInstalled(t, { padding: "x".repeat(20 * 1024) });
  assert.throws(() => fixture.resolve(), /size|large/i);
});

for (const field of ["size", "mtimeMs", "ctimeMs"]) {
  test(`installed-production rejects in-place manifest ${field} drift while being read`, (t) => {
    const fixture = resolveInstalled(t);
    let fstatCalls = 0;
    const mutatingFs = Object.create(fs);
    mutatingFs.fstatSync = (descriptor) => {
      const metadata = fs.fstatSync(descriptor);
      fstatCalls += 1;
      if (fstatCalls < 2) return metadata;
      return {
        ...metadata,
        [field]: metadata[field] + 1,
        isFile: () => metadata.isFile(),
      };
    };
    assert.throws(() => fixture.resolve({ fsModule: mutatingFs }), /changed|size/i);
  });
}

test("installed-production fails closed when O_NOFOLLOW is unavailable", (t) => {
  const fixture = resolveInstalled(t);
  const unsafeFs = Object.create(fs);
  const unsafeConstants = { ...fs.constants };
  delete unsafeConstants.O_NOFOLLOW;
  Object.defineProperty(unsafeFs, "constants", { value: unsafeConstants });
  assert.throws(() => fixture.resolve({ fsModule: unsafeFs }), /O_NOFOLLOW|nofollow|safe open/i);
});

test("installed-production clears inherited code selectors and pins physical runtime paths", (t) => {
  const { applyInstalledRuntimePaths } = loadLaunchProfile();
  assert.equal(typeof applyInstalledRuntimePaths, "function");
  const fixture = resolveInstalled(t);
  const profile = fixture.resolve();
  const env = {
    TAIJI_AGENT_ROOT: "/tmp/evil-root",
    TAIJI_AGENT_AGENT_DIR: "/tmp/evil-agent",
    TAIJI_AGENT_WEBUI_DIR: "/tmp/evil-web",
    TAIJI_AGENT_PYTHON: "/tmp/evil-python",
    TAIJI_WEBUI_PYTHON: "/tmp/evil-web-python",
    TAIJI_WEBUI_AGENT_DIR: "/tmp/evil-web-agent",
    PYTHONPATH: "/tmp/evil-pythonpath",
    PYTHONHOME: "/tmp/evil-pythonhome",
    NODE_OPTIONS: "--require=/tmp/evil-node.js",
    ELECTRON_RUN_AS_NODE: "1",
    BASH_ENV: "/tmp/evil-bash-env",
    PATH: "/tmp/evil-bin",
  };

  applyInstalledRuntimePaths({ launchProfile: profile, runtimeEnv: env });
  assert.equal(env.TAIJI_AGENT_ROOT, fixture.installRoot);
  assert.equal(env.TAIJI_AGENT_AGENT_DIR, fixture.agentDir);
  assert.equal(env.TAIJI_AGENT_WEBUI_DIR, fixture.webuiDir);
  assert.equal(env.TAIJI_AGENT_PYTHON, fixture.pythonPath);
  assert.equal(env.TAIJI_WEBUI_PYTHON, fixture.pythonPath);
  assert.equal(env.TAIJI_WEBUI_AGENT_DIR, fixture.agentDir);
  assert.equal(env.PATH, "/usr/bin:/bin:/usr/sbin:/sbin");
  for (const name of ["PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "ELECTRON_RUN_AS_NODE", "BASH_ENV"]) {
    assert.equal(Object.hasOwn(env, name), false, `${name} must not cross the installed child boundary`);
  }
});

test("installed-production rejects a symlinked runtime code directory", (t) => {
  const { applyInstalledRuntimePaths } = loadLaunchProfile();
  assert.equal(typeof applyInstalledRuntimePaths, "function");
  const fixture = resolveInstalled(t);
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-runtime-outside-"));
  t.after(() => fs.rmSync(outside, { recursive: true, force: true }));
  fs.rmSync(fixture.webuiDir, { recursive: true, force: true });
  fs.symlinkSync(outside, fixture.webuiDir, "dir");
  assert.throws(
    () => applyInstalledRuntimePaths({ launchProfile: fixture.resolve(), runtimeEnv: {} }),
    /runtime|physical|symlink|install root/i,
  );
});

test("installed-production defaults to local-controlled terminal and code execution", (t) => {
  const { applySecurityProfile } = loadLaunchProfile();
  const fixture = resolveInstalled(t);
  const profile = fixture.resolve();
  const env = {
    TAIJI_ALLOW_FUTURE_RELAXATION: "true",
  };
  const security = applySecurityProfile({
    launchProfile: profile,
    runtimeEnv: env,
    sourceEnv: env,
    packaged: false,
  });
  assert.deepEqual(security, { name: "local_controlled", mode: "restricted", allow: true });
  assert.equal(env.TAIJI_SECURITY_PROFILE, "local_controlled");
  assert.equal(env.TAIJI_SECURITY_MODE, "restricted");
  assert.equal(env.TAIJI_ALLOW_TERMINAL, "1");
  assert.equal(env.TAIJI_ALLOW_EXECUTE_CODE, "1");
  assert.equal(env.TAIJI_ALLOW_DELEGATE_TASK, "0");
  assert.equal(env.TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS, "0");
  assert.equal(env.TAIJI_ALLOW_FUTURE_RELAXATION, "true");
});

test("installed-production preserves an explicit strict profile across restart", (t) => {
  const { applySecurityProfile } = loadLaunchProfile();
  const profile = resolveInstalled(t).resolve();
  const env = {
    TAIJI_SECURITY_PROFILE: "strict",
    TAIJI_ALLOW_TERMINAL: "1",
    TAIJI_ALLOW_EXECUTE_CODE: "1",
    TAIJI_ALLOW_DELEGATE_TASK: "1",
    TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS: "1",
    TAIJI_ALLOW_FUTURE_RELAXATION: "true",
  };

  const security = applySecurityProfile({
    launchProfile: profile,
    runtimeEnv: env,
    sourceEnv: env,
    packaged: false,
  });

  assert.deepEqual(security, { name: "strict", mode: "restricted", allow: false });
  assert.equal(env.TAIJI_SECURITY_PROFILE, "strict");
  assert.equal(env.TAIJI_SECURITY_MODE, "restricted");
  assert.equal(env.TAIJI_ALLOW_TERMINAL, "0");
  assert.equal(env.TAIJI_ALLOW_EXECUTE_CODE, "0");
  assert.equal(env.TAIJI_ALLOW_DELEGATE_TASK, "0");
  assert.equal(env.TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS, "0");
  assert.equal(env.TAIJI_ALLOW_FUTURE_RELAXATION, "true");
});

test("installed-production fails closed for an invalid persisted security profile", (t) => {
  const { applySecurityProfile } = loadLaunchProfile();
  const fixture = resolveInstalled(t);
  const env = { TAIJI_SECURITY_PROFILE: "tampered-profile" };

  const profile = fixture.resolve({ env });
  const security = applySecurityProfile({
    launchProfile: profile,
    runtimeEnv: env,
    sourceEnv: env,
    packaged: false,
  });

  assert.deepEqual(security, { name: "strict", mode: "restricted", allow: false });
  assert.equal(env.TAIJI_SECURITY_PROFILE, "strict");
  assert.equal(env.TAIJI_ALLOW_TERMINAL, "0");
  assert.equal(env.TAIJI_ALLOW_EXECUTE_CODE, "0");
});

test("installed local-controlled keeps delegate and skill-script choices independent", (t) => {
  const { applySecurityProfile } = loadLaunchProfile();
  const profile = resolveInstalled(t).resolve();
  const env = {
    TAIJI_SECURITY_PROFILE: "local_controlled",
    TAIJI_ALLOW_DELEGATE_TASK: "1",
  };

  const security = applySecurityProfile({
    launchProfile: profile,
    runtimeEnv: env,
    sourceEnv: env,
    packaged: false,
  });

  assert.equal(security.name, "local_controlled");
  assert.equal(env.TAIJI_ALLOW_TERMINAL, "1");
  assert.equal(env.TAIJI_ALLOW_EXECUTE_CODE, "1");
  assert.equal(env.TAIJI_ALLOW_DELEGATE_TASK, "1");
  assert.equal(env.TAIJI_ALLOW_UNAPPROVED_SKILL_SCRIPTS, "0");
});

test("source formal runs the source gate while development and installed-production skip it", (t) => {
  const { resolveLaunchProfile, requiresSourceGate } = loadLaunchProfile();
  const formal = resolveLaunchProfile({ env: {}, appPath: __dirname });
  const development = resolveLaunchProfile({
    env: { TAIJI_SOURCE_MODE: "development" },
    appPath: __dirname,
  });
  const installed = resolveInstalled(t).resolve();
  assert.equal(requiresSourceGate(formal), true);
  assert.equal(requiresSourceGate(development), false);
  assert.equal(requiresSourceGate(installed), false);
});

test("source security profile preserves existing explicit behavior", () => {
  const { applySecurityProfile, resolveLaunchProfile } = loadLaunchProfile();
  const launchProfile = resolveLaunchProfile({
    env: { TAIJI_SOURCE_MODE: "formal" },
    appPath: __dirname,
  });
  const env = { TAIJI_SECURITY_PROFILE: "full", TAIJI_SECURITY_MODE: "full" };
  const security = applySecurityProfile({
    launchProfile,
    runtimeEnv: env,
    sourceEnv: env,
    packaged: false,
  });
  assert.equal(security.name, "full");
  assert.equal(env.TAIJI_SECURITY_PROFILE, "full");
  assert.equal(env.TAIJI_SECURITY_MODE, "full");
  assert.equal(env.TAIJI_ALLOW_TERMINAL, "1");
});

test("installed windows disable DevTools while source development keeps them available", (t) => {
  const { allowsDevTools, resolveLaunchProfile } = loadLaunchProfile();
  assert.equal(typeof allowsDevTools, "function", "launch profile must expose its DevTools policy");
  const development = resolveLaunchProfile({
    env: { TAIJI_SOURCE_MODE: "development" },
    appPath: __dirname,
  });
  const installed = resolveInstalled(t).resolve();
  assert.equal(allowsDevTools(development), true);
  assert.equal(allowsDevTools(installed), false);
});
