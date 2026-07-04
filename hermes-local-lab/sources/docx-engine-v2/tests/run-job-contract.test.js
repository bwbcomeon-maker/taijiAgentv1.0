const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const ENGINE_ROOT = path.join(__dirname, '..');
const RUN_JOB = path.join(ENGINE_ROOT, 'src', 'cli', 'run-job.js');
const REQUIRED_DELIVERY_ENTRIES = [
  'document.docx',
  'source.md',
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
  assert.ok(result.stdout.trim(), `expected JSON stdout\nstderr:\n${result.stderr}`);
  return JSON.parse(result.stdout);
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
  ]);

  assertExitCode(result, 0);
  for (const entry of REQUIRED_DELIVERY_ENTRIES) {
    assert.equal(fs.existsSync(path.join(deliveryDir, entry)), true, `${entry} must exist`);
  }

  const qualityReport = JSON.parse(
    fs.readFileSync(path.join(deliveryDir, 'quality-report.json'), 'utf8')
  );
  assert.match(qualityReport.status, /^(passed|passed_with_warnings)$/);

  const wpsVisualCheck = findQualityCheck(qualityReport, 'wps_visual');
  assert.ok(wpsVisualCheck, 'quality-report.json must include a wps_visual check');
  assert.equal(wpsVisualCheck.status, 'not_verified');
});
