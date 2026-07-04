const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const ENGINE_ROOT = path.join(__dirname, '..');
const RENDER_TEMPLATE_SAMPLE = path.join(ENGINE_ROOT, 'src', 'cli', 'render-template-sample.js');

function makeTempDir(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-template-smoke-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  return tempDir;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function makeTemplatePackage(rootDir, templateId) {
  const packageDir = path.join(rootDir, templateId);
  fs.cpSync(path.join(ENGINE_ROOT, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: templateId, name: 'Smoke Proposal' }, null, 2)}\n`,
    'utf8'
  );

  const adapterSamplePath = path.join(packageDir, 'adapter-sample.render-plan.json');
  const adapterSample = readJson(adapterSamplePath);
  fs.writeFileSync(
    adapterSamplePath,
    `${JSON.stringify(
      {
        ...adapterSample,
        jobId: `job-${templateId}-adapter-sample`,
        templateId,
        templateData: {
          ...adapterSample.templateData,
          metadata: { ...adapterSample.templateData.metadata, templateId },
        },
      },
      null,
      2
    )}\n`,
    'utf8'
  );
  return packageDir;
}

function runRenderTemplateSample(args) {
  return spawnSync(process.execPath, [RENDER_TEMPLATE_SAMPLE, ...args], {
    cwd: ENGINE_ROOT,
    encoding: 'utf8',
  });
}

test('render-template-sample CLI renders an uninstalled template package sample', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = makeTemplatePackage(tempRoot, 'smoke-proposal');
  const outDir = path.join(tempRoot, 'smoke-output');
  const registryPath = path.join(ENGINE_ROOT, 'template-registry.json');
  const registryBefore = fs.readFileSync(registryPath, 'utf8');

  const result = runRenderTemplateSample([
    '--package',
    packageDir,
    '--out-dir',
    outDir,
    '--json',
  ]);

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'smoke-proposal');
  assert.equal(payload.packageDir, packageDir);
  assert.equal(payload.outDir, outDir);
  assert.equal(payload.documentPath, path.join(outDir, 'sample.docx'));
  assert.equal(payload.reportPath, path.join(outDir, 'template-smoke-report.json'));
  assert.equal(fs.existsSync(payload.documentPath), true);
  assert.equal(fs.existsSync(payload.reportPath), true);
  assert.equal(fs.readFileSync(registryPath, 'utf8'), registryBefore);

  const report = readJson(payload.reportPath);
  assert.equal(report.schemaVersion, 'docx-engine-v2/template-smoke-report');
  assert.equal(report.ok, true);
  assert.equal(report.status, 'passed');
  assert.equal(report.templateId, 'smoke-proposal');
  assert.ok(report.checks.some((check) => check.id === 'template_package' && check.status === 'passed'));
  assert.ok(report.checks.some((check) => check.id === 'adapter_sample_render' && check.status === 'passed'));
});

test('render-template-sample CLI refuses to write into a non-empty output directory', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = makeTemplatePackage(tempRoot, 'non-empty-smoke-proposal');
  const outDir = path.join(tempRoot, 'smoke-output');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'keep.txt'), 'do not overwrite', 'utf8');

  const result = runRenderTemplateSample([
    '--package',
    packageDir,
    '--out-dir',
    outDir,
    '--json',
  ]);

  assert.equal(result.status, 3);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'template_sample_render_failed');
  assert.match(payload.message, /non-empty/i);
  assert.equal(fs.readFileSync(path.join(outDir, 'keep.txt'), 'utf8'), 'do not overwrite');
});
