const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const ENGINE_ROOT = path.join(__dirname, '..');
const RUN_JOB = path.join(ENGINE_ROOT, 'src', 'cli', 'run-job.js');
const REQUIRED_DELIVERY_ENTRIES = [
  'document.docx',
  'delivery-package.json',
  'source.md',
  'source/original',
  'assets',
  'job.manifest.json',
  'template.manifest.json',
  'render-plan.json',
  'quality-report.json',
  'README-图片调整说明.md',
];
const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);

function makeTempWorkspace(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-contract-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function runJob(args) {
  return spawnSync(process.execPath, [RUN_JOB, ...args], {
    cwd: ENGINE_ROOT,
    encoding: 'utf8',
  });
}

function assertExitCode(result, expectedCode) {
  assert.equal(
    result.status,
    expectedCode,
    `expected exit code ${expectedCode}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`
  );
}

function parseStdoutJson(result) {
  const rawStdout = result.stdout.trim();
  assert.ok(
    rawStdout,
    `expected JSON stdout\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`
  );

  try {
    return JSON.parse(rawStdout);
  } catch (error) {
    assert.fail(
      `failed to parse stdout as JSON\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}\nerror: ${error.message}`
    );
  }
}

function readJsonFile(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8');

  try {
    return JSON.parse(raw);
  } catch (error) {
    assert.fail(
      `failed to parse JSON file: ${filePath}\ncontents:\n${raw}\nerror: ${error.message}`
    );
  }
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function findQualityCheck(report, checkId) {
  if (Array.isArray(report.checks)) {
    return report.checks.find((check) => check.id === checkId || check.code === checkId);
  }

  if (report.checks && typeof report.checks === 'object') {
    const check = report.checks[checkId];
    if (typeof check === 'string') {
      return { id: checkId, status: check };
    }
    return check;
  }

  return undefined;
}

function assertFailureArtifacts({ deliveryDir, payload, messagePattern }) {
  assert.equal(fs.existsSync(deliveryDir), true, 'failed jobs should leave trace artifacts');

  const jobManifest = readJsonFile(path.join(deliveryDir, 'job.manifest.json'));
  assert.equal(jobManifest.status, 'failed');
  assert.ok(jobManifest.failures.some((failure) => messagePattern.test(failure)));

  const failureReport = readJsonFile(path.join(deliveryDir, 'failure-report.json'));
  assert.equal(failureReport.schemaVersion, 'docx-engine-v2/failure-report');
  assert.equal(failureReport.ok, false);
  assert.equal(failureReport.code, payload.code);
  assert.equal(failureReport.stage, payload.stage);
  assert.equal(failureReport.jobId, jobManifest.jobId);
  assert.equal(failureReport.jobManifest, 'job.manifest.json');
  assert.ok(failureReport.failures.some((failure) => messagePattern.test(failure)));
  assert.equal(payload.jobManifestPath, path.join(deliveryDir, 'job.manifest.json'));
  assert.equal(payload.failureReportPath, path.join(deliveryDir, 'failure-report.json'));
}

test('run-job requires explicit template selection and does not create delivery output', (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = path.join(root, 'source.md');
  const deliveryDir = path.join(root, 'delivery');

  fs.writeFileSync(sourcePath, '# Weekly project memo\n\nThe delivery needs a template.\n');

  const result = runJob(['--source', sourcePath, '--out-dir', deliveryDir]);

  assertExitCode(result, 2);
  const payload = parseStdoutJson(result);
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'template_selection_required');
  assert.ok(Array.isArray(payload.templates), 'templates must be an array');
  assert.ok(payload.templates.every((item) => item && typeof item === 'object'));
  assert.deepEqual(
    payload.templates.map((item) => item.id),
    ['general-proposal', 'meeting-minutes']
  );
  assert.equal(fs.existsSync(deliveryDir), false);
});

test('run-job renders rich Markdown into a complete editable delivery package', (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = path.join(root, 'source.md');
  const assetDir = path.join(root, 'source.assets');
  const deliveryDir = path.join(root, 'delivery');

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
      '| Template-based DOCX delivery | Document team | In progress |',
      '',
      '### 实施安排表',
      '',
      '| Phase | Window | Deliverable |',
      '| --- | --- | --- |',
      '| Phase 1 | Week 1 | Source normalization and render plan |',
      '| Phase 2 | Week 2 | DOCX package and visual acceptance report |',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[Source Markdown] --> B[Render Plan]',
      '  B --> C[Delivery Package]',
      '```',
      '',
      '![Architecture](source.assets/architecture.png)',
      '',
      '- Preserve the original Markdown source.',
      '- Keep editable assets beside the generated document.',
      '',
    ].join('\n')
  );

  const result = runJob([
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out-dir',
    deliveryDir,
    '--json',
  ]);

  assertExitCode(result, 0);
  const payload = parseStdoutJson(result);
  assert.equal(payload.ok, true);
  assert.match(payload.jobId, /^job-/);
  assert.equal(payload.deliveryDir, deliveryDir);
  assert.equal(payload.documentPath, path.join(deliveryDir, 'document.docx'));
  assert.match(payload.qualityStatus, /^(passed|passed_with_warnings)$/);

  for (const entry of REQUIRED_DELIVERY_ENTRIES) {
    assert.equal(fs.existsSync(path.join(deliveryDir, entry)), true, `${entry} must exist`);
  }
  assert.equal(
    fs.readFileSync(path.join(deliveryDir, 'source', 'original', path.basename(sourcePath)), 'utf8'),
    fs.readFileSync(sourcePath, 'utf8')
  );

  const qualityReport = readJsonFile(path.join(deliveryDir, 'quality-report.json'));
  assert.match(qualityReport.status, /^(passed|passed_with_warnings)$/);

  const deliveryManifest = readJsonFile(path.join(deliveryDir, 'delivery-package.json'));
  assert.equal(deliveryManifest.schemaVersion, 'docx-engine-v2/delivery-package');
  assert.equal(deliveryManifest.deliveryDir, deliveryDir);
  assert.equal(deliveryManifest.documentSha256, sha256File(path.join(deliveryDir, 'document.docx')));
  assert.equal(deliveryManifest.sourceSha256, sha256File(path.join(deliveryDir, 'source.md')));
  assert.equal(deliveryManifest.files.document, 'document.docx');
  assert.equal(deliveryManifest.files.source, 'source.md');
  assert.equal(deliveryManifest.files.originalSource, `source/original/${path.basename(sourcePath)}`);
  assert.equal(deliveryManifest.files.assetsDir, 'assets');
  assert.equal(deliveryManifest.files.jobManifest, 'job.manifest.json');
  assert.equal(deliveryManifest.files.templateManifest, 'template.manifest.json');
  assert.equal(deliveryManifest.files.renderPlan, 'render-plan.json');
  assert.equal(deliveryManifest.files.qualityReport, 'quality-report.json');
  assert.equal(deliveryManifest.files.imageInstructions, 'README-图片调整说明.md');
  assert.equal(deliveryManifest.status, 'delivered');

  const renderPlan = readJsonFile(path.join(deliveryDir, 'render-plan.json'));
  assert.equal(renderPlan.templateData.metadata.assetDir, 'assets');
  assert.equal(path.isAbsolute(renderPlan.templateData.metadata.assetDir), false);

  const jobManifest = readJsonFile(path.join(deliveryDir, 'job.manifest.json'));
  assert.equal(jobManifest.jobId, payload.jobId);
  assert.equal(jobManifest.status, 'delivered');
  assert.match(jobManifest.createdAt, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(jobManifest.workspace, deliveryDir);
  assert.equal(fs.existsSync(jobManifest.workspace), true);
  assert.ok(jobManifest.inputs.some((input) => input.type === 'source' && input.path === sourcePath));
  assert.ok(jobManifest.inputs.some((input) => input.type === 'asset_dir' && input.path === assetDir));
  assert.ok(
    jobManifest.outputs.some(
      (output) => output.type === 'document' && output.path === path.join(deliveryDir, 'document.docx')
    )
  );
  assert.ok(
    jobManifest.outputs.some(
      (output) => output.type === 'delivery_package' && output.path === deliveryDir
    )
  );
  assert.ok(Array.isArray(jobManifest.warnings));
  assert.deepEqual(jobManifest.failures, []);

  const wpsVisualCheck = findQualityCheck(qualityReport, 'wps_visual');
  assert.ok(wpsVisualCheck, 'quality-report.json must include a wps_visual check');
  assert.equal(wpsVisualCheck.status, 'not_verified');
  const originalSourceCheck = findQualityCheck(qualityReport, 'source_original');
  assert.ok(originalSourceCheck, 'quality-report.json must include a source_original check');
  assert.equal(originalSourceCheck.status, 'passed');
  const templateMarkersCheck = findQualityCheck(qualityReport, 'template_markers');
  assert.ok(templateMarkersCheck, 'quality-report.json must include a template_markers check');
  assert.equal(templateMarkersCheck.status, 'passed');
});

test('run-job reports missing source assets as validation failure before rendering', (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = path.join(root, 'source.md');
  const deliveryDir = path.join(root, 'delivery');

  fs.writeFileSync(
    sourcePath,
    [
      '# Missing asset proposal',
      '',
      '## Architecture',
      '',
      '| Component | Status |',
      '| --- | --- |',
      '| Required image | Missing |',
      '',
      '![Architecture](missing.png)',
      '',
    ].join('\n')
  );

  const result = runJob([
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    path.join(root, 'source.assets'),
    '--out-dir',
    deliveryDir,
    '--json',
  ]);

  assertExitCode(result, 3);
  const payload = parseStdoutJson(result);
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'validation_failed');
  assert.match(payload.message, /缺少必需图片资产/);
  assertFailureArtifacts({
    deliveryDir,
    payload,
    messagePattern: /缺少必需图片资产/,
  });
});

test('run-job rejects text-only sources for rich proposal templates before rendering', (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = path.join(root, 'source.txt');
  const deliveryDir = path.join(root, 'delivery');

  fs.writeFileSync(sourcePath, '项目建设方案\n需要整理为标准通用方案。\n');

  const result = runJob([
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--out-dir',
    deliveryDir,
    '--json',
  ]);

  assertExitCode(result, 3);
  const payload = parseStdoutJson(result);
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'validation_failed');
  assert.match(payload.message, /富内容初稿|表格|图示|图片/);
  assertFailureArtifacts({
    deliveryDir,
    payload,
    messagePattern: /富内容初稿|表格|图示|图片/,
  });
});

test('run-job reports non-empty delivery directories as validation failure', (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = path.join(root, 'source.md');
  const deliveryDir = path.join(root, 'delivery');

  fs.mkdirSync(deliveryDir);
  fs.writeFileSync(path.join(deliveryDir, 'existing.txt'), 'do not overwrite', 'utf8');
  fs.writeFileSync(sourcePath, '# Proposal\n\n## Section\n\nText.\n');

  const result = runJob([
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--out-dir',
    deliveryDir,
    '--json',
  ]);

  assertExitCode(result, 3);
  const payload = parseStdoutJson(result);
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'validation_failed');
  assert.match(payload.message, /输出目录非空/);
  assert.equal(fs.readFileSync(path.join(deliveryDir, 'existing.txt'), 'utf8'), 'do not overwrite');
  assert.equal(fs.existsSync(path.join(deliveryDir, 'job.manifest.json')), false);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'failure-report.json')), false);
});
