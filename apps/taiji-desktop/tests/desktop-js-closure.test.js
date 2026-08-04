const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const stager = path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "packaging",
  "linux",
  "stage-desktop-js-closure.js",
);

test("desktop JavaScript stager copies the complete recursive relative require closure", (t) => {
  assert.equal(fs.existsSync(stager), true, "desktop closure stager must exist");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-desktop-js-closure-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const destination = path.join(root, "destination");
  fs.mkdirSync(path.join(source, "nested"), { recursive: true });
  fs.writeFileSync(path.join(source, "main.js"), 'require("./first");\n');
  fs.writeFileSync(path.join(source, "first.js"), 'require("./nested/second");\n');
  fs.writeFileSync(path.join(source, "nested", "second.js"), "module.exports = 2;\n");

  const result = spawnSync(process.execPath, [
    stager,
    "--source", source,
    "--destination", destination,
    "--entry", "main.js",
  ], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(destination, "main.js")), true);
  assert.equal(fs.existsSync(path.join(destination, "first.js")), true);
  assert.equal(fs.existsSync(path.join(destination, "nested", "second.js")), true);
});

test("desktop JavaScript stager fails closed on a missing relative dependency", (t) => {
  assert.equal(fs.existsSync(stager), true, "desktop closure stager must exist");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-desktop-js-missing-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const destination = path.join(root, "destination");
  fs.mkdirSync(source, { recursive: true });
  fs.writeFileSync(path.join(source, "main.js"), 'require("./missing");\n');

  const result = spawnSync(process.execPath, [
    stager,
    "--source", source,
    "--destination", destination,
    "--entry", "main.js",
  ], { encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /missing|resolve|dependency/i);
});

test("desktop JavaScript stager follows a direct require separated by comments", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-desktop-js-comment-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const destination = path.join(root, "destination");
  fs.mkdirSync(source, { recursive: true });
  fs.writeFileSync(path.join(source, "main.js"), 'require /* trusted separator */ ("./dep");\n');
  fs.writeFileSync(path.join(source, "dep.js"), "module.exports = 1;\n");

  const result = spawnSync(process.execPath, [
    stager,
    "--source", source,
    "--destination", destination,
    "--entry", "main.js",
  ], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(destination, "dep.js")), true);
});

test("desktop JavaScript stager fails closed when require is aliased", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-desktop-js-alias-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const destination = path.join(root, "destination");
  fs.mkdirSync(source, { recursive: true });
  fs.writeFileSync(path.join(source, "main.js"), 'const load = require; load("./dep");\n');
  fs.writeFileSync(path.join(source, "dep.js"), "module.exports = 1;\n");

  const result = spawnSync(process.execPath, [
    stager,
    "--source", source,
    "--destination", destination,
    "--entry", "main.js",
  ], { encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /alias|direct|require/i);
});

test("desktop JavaScript stager fails closed on computed module require", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-desktop-js-computed-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const destination = path.join(root, "destination");
  fs.mkdirSync(source, { recursive: true });
  fs.writeFileSync(path.join(source, "main.js"), 'module["require"]("./dep");\n');
  fs.writeFileSync(path.join(source, "dep.js"), "module.exports = 1;\n");

  const result = spawnSync(process.execPath, [
    stager,
    "--source", source,
    "--destination", destination,
    "--entry", "main.js",
  ], { encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /computed|direct|loader|require/i);
});

test("desktop JavaScript stager parses escaped require identifiers", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-desktop-js-escaped-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const destination = path.join(root, "destination");
  fs.mkdirSync(source, { recursive: true });
  fs.writeFileSync(path.join(source, "main.js"), 'requ\\u0069re("./dep");\n');
  fs.writeFileSync(path.join(source, "dep.js"), "module.exports = 1;\n");

  const result = spawnSync(process.execPath, [
    stager,
    "--source", source,
    "--destination", destination,
    "--entry", "main.js",
  ], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(destination, "dep.js")), true);
});

test("desktop JavaScript stager fails closed on dynamic import expressions", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "taiji-desktop-js-import-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  const destination = path.join(root, "destination");
  fs.mkdirSync(source, { recursive: true });
  fs.writeFileSync(path.join(source, "main.js"), 'import("./dep.js");\n');
  fs.writeFileSync(path.join(source, "dep.js"), "export default 1;\n");

  const result = spawnSync(process.execPath, [
    stager,
    "--source", source,
    "--destination", destination,
    "--entry", "main.js",
  ], { encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /dynamic import|loader|import/i);
});
