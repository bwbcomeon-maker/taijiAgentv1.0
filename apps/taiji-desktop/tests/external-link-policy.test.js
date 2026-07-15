const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  isTrustedExternalUrl,
  createExternalWindowOpenHandler,
} = require("../src/external-link-policy");

test("trusted external URL policy accepts only http and https", () => {
  assert.equal(isTrustedExternalUrl("https://login.example.test/authorize"), true);
  assert.equal(isTrustedExternalUrl("http://127.0.0.1/callback"), true);
  assert.equal(isTrustedExternalUrl("javascript:alert(1)"), false);
  assert.equal(isTrustedExternalUrl("file:///tmp/secret"), false);
  assert.equal(isTrustedExternalUrl("data:text/html,unsafe"), false);
  assert.equal(isTrustedExternalUrl("not a URL"), false);
});

test("window open handler forwards trusted auth URL to the system browser and denies Electron windows", async () => {
  const opened = [];
  const pending = [];
  const handler = createExternalWindowOpenHandler((url) => {
    opened.push(url);
    const result = Promise.resolve();
    pending.push(result);
    return result;
  });

  assert.deepEqual(handler({ url: "https://login.example.test/authorize" }), { action: "deny" });
  assert.deepEqual(handler({ url: "file:///tmp/secret" }), { action: "deny" });
  await Promise.all(pending);
  assert.deepEqual(opened, ["https://login.example.test/authorize"]);
});

test("desktop main installs the external URL handler on the isolated BrowserWindow", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "main.js"), "utf8");
  assert.match(source, /createExternalWindowOpenHandler/);
  assert.match(source, /webContents\.setWindowOpenHandler\(/);
  assert.match(source, /shell\.openExternal/);
  assert.match(source, /contextIsolation:\s*true/);
  assert.match(source, /nodeIntegration:\s*false/);
  assert.match(source, /sandbox:\s*true/);
});
