const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  isTrustedExternalUrl,
  normalizeTrustedExternalOrigins,
  createExternalWindowOpenHandler,
} = require("../src/external-link-policy");

test("trusted external URL policy is exact-origin and fail-closed", () => {
  const allowed = normalizeTrustedExternalOrigins("https://login.example.test");
  assert.deepEqual(allowed, ["https://login.example.test"]);
  assert.equal(isTrustedExternalUrl("https://login.example.test/authorize?client_id=1", allowed), true);
  assert.equal(isTrustedExternalUrl("https://arbitrary.example.test/authorize", allowed), false);
  assert.equal(isTrustedExternalUrl("https://login.example.test.evil.test/authorize", allowed), false);
  assert.equal(isTrustedExternalUrl("https://login.example.test@evil.test/authorize", allowed), false);
  assert.equal(isTrustedExternalUrl("javascript:alert(1)", allowed), false);
  assert.equal(isTrustedExternalUrl("https://login.example.test/authorize", []), false);
});

test("origin configuration rejects paths, userinfo, insecure remote origins and permits explicit local development", () => {
  assert.deepEqual(normalizeTrustedExternalOrigins("https://login.example.test/path"), []);
  assert.deepEqual(normalizeTrustedExternalOrigins("https://user@login.example.test"), []);
  assert.deepEqual(normalizeTrustedExternalOrigins("http://login.example.test"), []);
  assert.deepEqual(normalizeTrustedExternalOrigins("http://127.0.0.1:9000", { allowLocalHttp: true }), ["http://127.0.0.1:9000"]);
  assert.deepEqual(normalizeTrustedExternalOrigins("http://localhost:9000", { allowLocalHttp: true }), ["http://localhost:9000"]);
});

test("window open handler forwards trusted auth URL to the isolated auth window and denies arbitrary Electron windows", async () => {
  const opened = [];
  const pending = [];
  const handler = createExternalWindowOpenHandler((url) => {
    opened.push(url);
    const result = Promise.resolve();
    pending.push(result);
    return result;
  }, () => {}, ["https://login.example.test"]);

  assert.deepEqual(handler({ url: "https://login.example.test/authorize" }), { action: "deny" });
  assert.deepEqual(handler({ url: "https://arbitrary.example.test/authorize" }), { action: "deny" });
  assert.deepEqual(handler({ url: "file:///tmp/secret" }), { action: "deny" });
  await Promise.all(pending);
  assert.deepEqual(opened, ["https://login.example.test/authorize"]);
});

test("desktop main installs the external URL handler on the isolated BrowserWindow", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "main.js"), "utf8");
  assert.match(source, /createExternalWindowOpenHandler/);
  assert.match(source, /TAIJI_TRUSTED_OIDC_ORIGINS/);
  assert.match(source, /webContents\.setWindowOpenHandler\(/);
  assert.match(source, /openTrustedIdentityWindow/);
  assert.match(source, /session:\s*mainWindow\.webContents\.session/);
  assert.doesNotMatch(source, /createExternalWindowOpenHandler\(\s*\(url\)\s*=>\s*shell\.openExternal/);
  assert.match(source, /contextIsolation:\s*true/);
  assert.match(source, /nodeIntegration:\s*false/);
  assert.match(source, /sandbox:\s*true/);
});
