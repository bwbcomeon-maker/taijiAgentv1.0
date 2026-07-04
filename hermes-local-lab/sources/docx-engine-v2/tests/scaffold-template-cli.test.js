const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const ENGINE_ROOT = path.join(__dirname, '..');
const SCAFFOLD_TEMPLATE = path.join(ENGINE_ROOT, 'src', 'cli', 'scaffold-template.js');
const VALIDATE_TEMPLATE = path.join(ENGINE_ROOT, 'src', 'cli', 'validate-template.js');

function makeTempDir(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-scaffold-cli-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  return tempDir;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function runScaffoldTemplate(args) {
  return spawnSync(process.execPath, [SCAFFOLD_TEMPLATE, ...args], {
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

test('scaffold-template CLI creates a valid package without mutating registry', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = path.join(tempRoot, 'work-ticket');
  const registryPath = path.join(ENGINE_ROOT, 'template-registry.json');
  const registryBefore = fs.readFileSync(registryPath, 'utf8');

  const result = runScaffoldTemplate([
    '--from',
    'general-proposal',
    '--template-id',
    'work-ticket',
    '--name',
    '工作票模板',
    '--description',
    '用于工作票类文档的模板包。',
    '--out-dir',
    packageDir,
    '--json',
  ]);

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'work-ticket');
  assert.equal(payload.baseTemplateId, 'general-proposal');
  assert.equal(payload.packageDir, packageDir);
  assert.equal(fs.readFileSync(registryPath, 'utf8'), registryBefore);

  const manifest = readJson(path.join(packageDir, 'manifest.json'));
  assert.equal(manifest.id, 'work-ticket');
  assert.equal(manifest.name, '工作票模板');
  assert.equal(manifest.description, '用于工作票类文档的模板包。');

  const schema = readJson(path.join(packageDir, 'schema.json'));
  assert.equal(schema.$id, 'work-ticket.schema.json');
  assert.equal(schema.title, '工作票模板数据');
  assert.match(fs.readFileSync(path.join(packageDir, 'prompt.md'), 'utf8'), /工作票模板/);

  const validation = runValidateTemplate(['--package', packageDir, '--json']);
  assert.equal(validation.status, 0, `stdout:\n${validation.stdout}\nstderr:\n${validation.stderr}`);
  assert.equal(JSON.parse(validation.stdout).ok, true);
});

test('scaffold-template CLI refuses to overwrite a non-empty package directory', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = path.join(tempRoot, 'existing-template');
  fs.mkdirSync(packageDir, { recursive: true });
  fs.writeFileSync(path.join(packageDir, 'keep.txt'), 'do not overwrite', 'utf8');

  const result = runScaffoldTemplate([
    '--from',
    'general-proposal',
    '--template-id',
    'existing-template',
    '--name',
    'Existing Template',
    '--out-dir',
    packageDir,
    '--json',
  ]);

  assert.equal(result.status, 3);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'template_scaffold_failed');
  assert.match(payload.message, /non-empty/i);
  assert.equal(fs.readFileSync(path.join(packageDir, 'keep.txt'), 'utf8'), 'do not overwrite');
});
