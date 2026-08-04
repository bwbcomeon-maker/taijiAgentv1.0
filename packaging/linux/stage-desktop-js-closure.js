#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const acorn = require(path.resolve(
  __dirname,
  "../../apps/taiji-desktop/node_modules/acorn",
));

function fail(message) {
  process.stderr.write(`Desktop JavaScript closure error: ${message}\n`);
  process.exit(1);
}

function parseArguments(argv) {
  const options = { entries: [] };
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!value) fail(`missing value for ${name || "argument"}`);
    if (name === "--source") options.source = value;
    else if (name === "--destination") options.destination = value;
    else if (name === "--entry") options.entries.push(value);
    else fail(`unsupported argument: ${name}`);
  }
  if (!options.source || !options.destination || options.entries.length === 0) {
    fail("--source, --destination, and at least one --entry are required");
  }
  return options;
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function assertRegularPhysicalFile(sourceRoot, candidate, label) {
  let metadata;
  try {
    metadata = fs.lstatSync(candidate);
  } catch (error) {
    fail(`${label} is missing: ${candidate}`);
  }
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    fail(`${label} must be a regular file, not a symlink: ${candidate}`);
  }
  const physical = fs.realpathSync.native(candidate);
  if (physical !== path.resolve(candidate) || !isWithin(sourceRoot, physical)) {
    fail(`${label} escapes the physical desktop source root: ${candidate}`);
  }
  return physical;
}

function resolveRelativeDependency(sourceRoot, parentFile, specifier) {
  const base = path.resolve(path.dirname(parentFile), specifier);
  const candidates = [base, `${base}.js`, `${base}.json`, path.join(base, "index.js"), path.join(base, "index.json")];
  for (const candidate of candidates) {
    try {
      const metadata = fs.lstatSync(candidate);
      if (!metadata.isFile() && !metadata.isSymbolicLink()) continue;
      return assertRegularPhysicalFile(sourceRoot, candidate, `relative dependency ${specifier}`);
    } catch (error) {
      if (error && error.code === "ENOENT") continue;
      throw error;
    }
  }
  fail(`missing relative dependency ${specifier} required by ${parentFile}`);
}

function memberPropertyName(node) {
  if (!node || node.type !== "MemberExpression") return null;
  if (!node.computed && node.property.type === "Identifier") return node.property.name;
  if (node.computed && node.property.type === "Literal" && typeof node.property.value === "string") {
    return node.property.value;
  }
  return null;
}

function walkJavaScript(node, parent, filename, dependencies) {
  if (!node || typeof node !== "object" || typeof node.type !== "string") return;

  if (node.type === "ImportExpression") {
    fail(`dynamic import is not allowed in packaged desktop code: ${filename}`);
  }

  if (node.type === "CallExpression" && node.callee.type === "Identifier" && node.callee.name === "require") {
    if (
      node.arguments.length !== 1
      || node.arguments[0].type !== "Literal"
      || typeof node.arguments[0].value !== "string"
    ) {
      fail(`dynamic require is not allowed in packaged desktop code: ${filename}`);
    }
    const specifier = node.arguments[0].value;
    if (specifier === "module" || specifier === "node:module") {
      fail(`runtime module-loader construction is not allowed in packaged desktop code: ${filename}`);
    }
    if (specifier.startsWith(".")) dependencies.push(specifier);
  }

  if (node.type === "Identifier" && node.name === "require") {
    const isDirectCallee = parent && parent.type === "CallExpression" && parent.callee === node;
    if (!isDirectCallee) {
      fail(`require must be called directly and may not be aliased in packaged desktop code: ${filename}`);
    }
  }

  if (
    node.type === "Identifier"
    && ["eval", "Function", "createRequire", "getBuiltinModule", "_load"].includes(node.name)
  ) {
    fail(`dynamic module-loader construct ${node.name} is not allowed in packaged desktop code: ${filename}`);
  }

  if (node.type === "MemberExpression") {
    const property = memberPropertyName(node);
    if (["require", "createRequire", "getBuiltinModule", "mainModule", "_load"].includes(property)) {
      fail(`computed or indirect module loader ${property} is not allowed in packaged desktop code: ${filename}`);
    }
    if (
      node.object.type === "Identifier"
      && node.object.name === "module"
      && property !== "exports"
    ) {
      fail(`only module.exports is allowed in packaged desktop code: ${filename}`);
    }
  }

  if (
    node.type === "Literal"
    && node.value === "require"
    && !(parent && parent.type === "CallExpression" && parent.arguments.includes(node))
  ) {
    fail(`indirect require lookup is not allowed in packaged desktop code: ${filename}`);
  }

  for (const [key, value] of Object.entries(node)) {
    if (["start", "end", "loc", "range"].includes(key)) continue;
    if (Array.isArray(value)) {
      for (const child of value) walkJavaScript(child, node, filename, dependencies);
    } else {
      walkJavaScript(value, node, filename, dependencies);
    }
  }
}

function relativeRequires(source, filename) {
  if (path.extname(filename) === ".json") {
    try {
      JSON.parse(source);
    } catch (error) {
      fail(`invalid JSON dependency ${filename}: ${error.message}`);
    }
    return [];
  }
  let tree;
  try {
    tree = acorn.parse(source, {
      allowHashBang: true,
      ecmaVersion: "latest",
      sourceType: "script",
    });
  } catch (error) {
    fail(`invalid JavaScript ${filename}: ${error.message}`);
  }
  const dependencies = [];
  walkJavaScript(tree, null, filename, dependencies);
  return dependencies;
}

function main() {
  const options = parseArguments(process.argv.slice(2));
  const sourceRoot = fs.realpathSync.native(options.source);
  const destinationRoot = path.resolve(options.destination);
  fs.mkdirSync(destinationRoot, { recursive: true, mode: 0o755 });

  const pending = options.entries.map((entry) => {
    if (path.isAbsolute(entry) || entry.split(/[\\/]/).includes("..")) {
      fail(`entry must be relative to the desktop source root: ${entry}`);
    }
    return assertRegularPhysicalFile(sourceRoot, path.join(sourceRoot, entry), `entry ${entry}`);
  });
  const visited = new Set();

  while (pending.length > 0) {
    const sourceFile = pending.pop();
    if (visited.has(sourceFile)) continue;
    visited.add(sourceFile);
    const contents = fs.readFileSync(sourceFile, "utf8");
    for (const specifier of relativeRequires(contents, sourceFile)) {
      pending.push(resolveRelativeDependency(sourceRoot, sourceFile, specifier));
    }
  }

  for (const sourceFile of [...visited].sort()) {
    const relative = path.relative(sourceRoot, sourceFile);
    const destination = path.join(destinationRoot, relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true, mode: 0o755 });
    fs.copyFileSync(sourceFile, destination);
    fs.chmodSync(destination, 0o644);
    const staged = fs.lstatSync(destination);
    if (staged.isSymbolicLink() || !staged.isFile()) {
      fail(`staged dependency is not a regular file: ${destination}`);
    }
    if (!fs.readFileSync(destination).equals(fs.readFileSync(sourceFile))) {
      fail(`staged dependency differs from source: ${relative}`);
    }
  }

  process.stdout.write(`Staged ${visited.size} desktop JavaScript closure files.\n`);
}

main();
