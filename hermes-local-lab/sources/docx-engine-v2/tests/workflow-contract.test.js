const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { installTemplatePackage } = require('../src/templates/install-template-package');
const { runDocumentJob } = require('../src/workflow/run-document-job');

const ENGINE_ROOT = path.join(__dirname, '..');
const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);

function makeTempWorkspace(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-workflow-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeRichMarkdown({ root, sourcePath, assetDir }) {
  fs.mkdirSync(assetDir);
  fs.writeFileSync(path.join(assetDir, 'architecture.png'), ONE_BY_ONE_PNG);
  fs.writeFileSync(
    sourcePath,
    [
      '# Enterprise AI rollout proposal',
      '',
      '## Executive summary',
      '',
      'This proposal includes rich Markdown that must survive DOCX rendering.',
      '',
      '### 重点任务表',
      '',
      '| Task | Owner | Status |',
      '| --- | --- | --- |',
      '| Local-first assistant rollout | PMO | Ready |',
      '',
      '### 实施安排表',
      '',
      '| Phase | Window | Deliverable |',
      '| --- | --- | --- |',
      '| Phase 1 | Week 1 | Source normalization and render plan |',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[Source Markdown] --> B[Render Plan]',
      '  B --> C[Delivery Package]',
      '```',
      '',
      '![Architecture](source.assets/architecture.png)',
      '',
      `Workspace: ${root}`,
      '',
    ].join('\n')
  );
}

test('runDocumentJob drives the canonical job lifecycle and writes a complete manifest', async (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = path.join(root, 'source.md');
  const assetDir = path.join(root, 'source.assets');
  const deliveryDir = path.join(root, 'delivery');
  writeRichMarkdown({ root, sourcePath, assetDir });

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId: 'general-proposal',
    sourcePath,
    assetDir,
    deliveryDir,
  });

  assert.equal(result.ok, true);
  assert.equal(result.job.status, 'delivered');
  assert.equal(result.job.jobId, result.jobId);
  assert.equal(result.job.templateId, 'general-proposal');
  assert.ok(result.job.inputs.some((input) => input.type === 'source' && input.path === sourcePath));
  assert.ok(result.job.inputs.some((input) => input.type === 'asset_dir' && input.path === assetDir));
  assert.ok(result.job.outputs.some((output) => output.type === 'document'));
  assert.ok(result.job.outputs.some((output) => output.type === 'delivery_package'));
  assert.equal(result.job.workspace, deliveryDir);
  assert.equal(fs.existsSync(result.job.workspace), true);
  assert.deepEqual(result.job.failures, []);

  const manifest = readJson(path.join(deliveryDir, 'job.manifest.json'));
  assert.deepEqual(manifest, result.job);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'quality-report.json')), true);
});

test('runDocumentJob writes traceable failure artifacts for input validation failures', async (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = path.join(root, 'source.txt');
  const deliveryDir = path.join(root, 'delivery');
  fs.writeFileSync(sourcePath, '项目建设方案\n需要整理为标准通用方案。\n');

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId: 'general-proposal',
    sourcePath,
    deliveryDir,
  });

  assert.equal(result.ok, false);
  assert.equal(result.code, 'validation_failed');
  assert.equal(result.job.status, 'failed');
  assert.match(result.message, /富内容初稿|表格|图示|图片/);
  assert.ok(result.job.failures.some((failure) => /富内容初稿|表格|图示|图片/.test(failure)));
  assert.equal(result.jobManifestPath, path.join(deliveryDir, 'job.manifest.json'));
  assert.equal(result.failureReportPath, path.join(deliveryDir, 'failure-report.json'));

  const jobManifest = readJson(path.join(deliveryDir, 'job.manifest.json'));
  const failureReport = readJson(path.join(deliveryDir, 'failure-report.json'));
  assert.equal(jobManifest.status, 'failed');
  assert.ok(jobManifest.failures.some((failure) => /富内容初稿|表格|图示|图片/.test(failure)));
  assert.equal(failureReport.schemaVersion, 'docx-engine-v2/failure-report');
  assert.equal(failureReport.ok, false);
  assert.equal(failureReport.code, 'validation_failed');
  assert.equal(failureReport.jobId, jobManifest.jobId);
  assert.equal(failureReport.jobManifest, 'job.manifest.json');
});

test('runDocumentJob rejects an installed template that was modified after installation', async (t) => {
  const root = makeTempWorkspace(t);
  const engineRoot = path.join(root, 'engine');
  const sourcePath = path.join(root, 'source.md');
  const assetDir = path.join(root, 'source.assets');
  const deliveryDir = path.join(root, 'delivery');
  const packageDir = path.join(root, 'incoming', 'custom-proposal');
  fs.mkdirSync(engineRoot, { recursive: true });
  fs.cpSync(path.join(ENGINE_ROOT, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: 'custom-proposal', name: 'Custom Proposal' }, null, 2)}\n`,
    'utf8'
  );
  fs.writeFileSync(
    path.join(engineRoot, 'template-registry.json'),
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
  await installTemplatePackage({ rootDir: engineRoot, packageDir });
  fs.writeFileSync(path.join(engineRoot, 'installed', 'custom-proposal', 'template.docx'), 'not a docx package', 'utf8');
  writeRichMarkdown({ root, sourcePath, assetDir });

  const result = await runDocumentJob({
    engineRoot,
    templateId: 'custom-proposal',
    sourcePath,
    assetDir,
    deliveryDir,
  });

  assert.equal(result.ok, false);
  assert.equal(result.code, 'validation_failed');
  assert.equal(result.stage, 'validation');
  assert.match(result.message, /Template package validation failed|template_docx_invalid/);
  assert.equal(result.job.status, 'failed');
  assert.ok(result.job.failures.some((failure) => /template_docx_invalid|Template DOCX/.test(failure)));
  assert.equal(result.jobManifestPath, path.join(deliveryDir, 'job.manifest.json'));
  assert.equal(result.failureReportPath, path.join(deliveryDir, 'failure-report.json'));

  const jobManifest = readJson(path.join(deliveryDir, 'job.manifest.json'));
  const failureReport = readJson(path.join(deliveryDir, 'failure-report.json'));
  assert.equal(jobManifest.status, 'failed');
  assert.equal(failureReport.code, 'validation_failed');
  assert.equal(failureReport.stage, 'validation');
});
