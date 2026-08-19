const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");

const mainSource = fs.readFileSync(
  path.resolve(__dirname, "..", "src", "main.js"),
  "utf8",
);

test("startRuntime keeps the selected WebUI port in scope through desktop navigation", () => {
  const start = mainSource.indexOf("async function startRuntime() {");
  const end = mainSource.indexOf("\nfunction installMenu()", start);
  assert.notEqual(start, -1, "startRuntime must exist");
  assert.notEqual(end, -1, "startRuntime boundary must exist");

  const source = mainSource.slice(start, end);
  const declaration = source.indexOf("let webuiPort;");
  const guardedStartup = source.indexOf("try {");
  const selection = source.indexOf("webuiPort = await findFreePort(DEFAULT_WEBUI_PORT);");
  const navigation = source.indexOf("new URL(`http://127.0.0.1:${webuiPort}`)");

  assert.notEqual(declaration, -1, "WebUI port must be declared in startRuntime scope");
  assert.ok(declaration < guardedStartup, "WebUI port must outlive the guarded startup block");
  assert.ok(selection > guardedStartup, "WebUI port must be selected inside guarded startup");
  assert.ok(navigation > selection, "desktop navigation must reuse the selected WebUI port");
  assert.doesNotMatch(source, /const webuiPort\s*=/);
});
