const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const ENGINE_ROOT = path.join(__dirname, '..');
const INSTALL_TEMPLATE = path.join(ENGINE_ROOT, 'src', 'cli', 'install-template.js');
const VALIDATE_TEMPLATE = path.join(ENGINE_ROOT, 'src', 'cli', 'validate-template.js');

function makeTempDir(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-install-cli-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  return tempDir;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function makeRegistry(rootDir) {
  fs.writeFileSync(
    path.join(rootDir, 'template-registry.json'),
    `${JSON.stringify(
      {
        version: 1,
        builtin: [{ templateId: 'general-proposal', path: path.join(ENGINE_ROOT, 'templates', 'general-proposal') }],
        installed: [],
      },
      null,
      2
    )}\n`,
    'utf8'
  );
}

function makeTemplatePackage(rootDir, templateId) {
  const packageDir = path.join(rootDir, 'incoming', templateId);
  fs.cpSync(path.join(ENGINE_ROOT, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: templateId, name: 'Installed Template' }, null, 2)}\n`,
    'utf8'
  );
  return packageDir;
}

function runInstallTemplate(args) {
  return spawnSync(process.execPath, [INSTALL_TEMPLATE, ...args], {
    cwd: ENGINE_ROOT,
    encoding: 'utf8',
  });
}

function runValidateTemplate(args) {
  return spawnSync(process.execPath, [VALIDATE_TEMPLATE, ...args], {
    cwd: ENGINE_ROOT,
    encoding: 'utf8',
  });
}

test('validate-template CLI validates a package without mutating registry', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = makeTemplatePackage(tempRoot, 'custom-proposal');
  const registryPath = path.join(tempRoot, 'template-registry.json');

  const result = runValidateTemplate([
    '--package',
    packageDir,
    '--json',
  ]);

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'custom-proposal');
  assert.equal(payload.packageDir, packageDir);
  assert.deepEqual(payload.validation, { ok: true });
  assert.equal(fs.existsSync(registryPath), false);
});

test('validate-template CLI reports package validation errors as JSON', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = makeTemplatePackage(tempRoot, 'broken-proposal');
  fs.rmSync(path.join(packageDir, 'adapter-sample.render-plan.json'));

  const result = runValidateTemplate([
    '--package',
    packageDir,
    '--json',
  ]);

  assert.equal(result.status, 3);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'template_validation_failed');
  assert.equal(payload.templateId, 'broken-proposal');
  assert.ok(payload.errors.some((error) => error.code === 'template_file_missing' && error.file === 'adapterSample'));
});

test('install-template CLI installs a validated package and emits JSON', (t) => {
  const tempRoot = makeTempDir(t);
  makeRegistry(tempRoot);
  const packageDir = makeTemplatePackage(tempRoot, 'custom-proposal');

  const result = runInstallTemplate([
    '--root-dir',
    tempRoot,
    '--package',
    packageDir,
    '--json',
  ]);

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'custom-proposal');
  assert.equal(payload.registryEntry.path, 'installed/custom-proposal');
  assert.equal(fs.existsSync(path.join(tempRoot, 'installed', 'custom-proposal', 'manifest.json')), true);
});

test('install-template CLI reports validation failure without mutating registry', (t) => {
  const tempRoot = makeTempDir(t);
  makeRegistry(tempRoot);
  const packageDir = makeTemplatePackage(tempRoot, 'general-proposal');

  const result = runInstallTemplate([
    '--root-dir',
    tempRoot,
    '--package',
    packageDir,
    '--json',
  ]);

  assert.equal(result.status, 3);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'template_install_failed');
  assert.match(payload.message, /Template already exists: general-proposal/);
  assert.deepEqual(readJson(path.join(tempRoot, 'template-registry.json')).installed, []);
});

test('install-template CLI replaces an installed template only with explicit flag', (t) => {
  const tempRoot = makeTempDir(t);
  makeRegistry(tempRoot);
  const firstPackageDir = makeTemplatePackage(tempRoot, 'custom-proposal');
  const firstResult = runInstallTemplate([
    '--root-dir',
    tempRoot,
    '--package',
    firstPackageDir,
    '--json',
  ]);
  assert.equal(firstResult.status, 0, firstResult.stderr);

  const replacementPackageDir = makeTemplatePackage(tempRoot, 'custom-proposal');
  const manifestPath = path.join(replacementPackageDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, name: 'Updated Installed Template' }, null, 2)}\n`,
    'utf8'
  );

  const result = runInstallTemplate([
    '--root-dir',
    tempRoot,
    '--package',
    replacementPackageDir,
    '--replace',
    '--json',
  ]);

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, true);
  assert.equal(payload.action, 'replaced');
  assert.equal(payload.templateId, 'custom-proposal');
  assert.equal(
    readJson(path.join(tempRoot, 'installed', 'custom-proposal', 'manifest.json')).name,
    'Updated Installed Template'
  );
  assert.deepEqual(readJson(path.join(tempRoot, 'template-registry.json')).installed, [
    { templateId: 'custom-proposal', path: 'installed/custom-proposal' },
  ]);
});
