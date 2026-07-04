const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const { runDocumentJob } = require('../src/workflow/run-document-job');
const { recordWpsVisualAcceptance } = require('../src/validation/record-wps-visual-acceptance');

const ENGINE_ROOT = path.resolve(__dirname, '..');
const CLI = path.join(ENGINE_ROOT, 'src', 'cli', 'record-wps-visual.js');
const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);
const WPS_EVIDENCE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);
const VISUAL_CHECKS = [
  'document_opened',
  'layout_reviewed',
  'content_order_reviewed',
  'figures_reviewed',
  'tables_reviewed',
];

async function makeDelivery(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-wps-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  const deliveryDir = path.join(workspace, 'delivery');
  const assetDir = path.join(workspace, 'source.assets');
  const sourcePath = path.join(workspace, 'source.md');

  fs.mkdirSync(assetDir);
  fs.writeFileSync(path.join(assetDir, 'architecture.png'), ONE_BY_ONE_PNG);
  fs.writeFileSync(
    sourcePath,
    [
      '# WPS visual acceptance proposal',
      '',
      '## Architecture',
      '',
      'The package must pass automated checks before a visual reviewer can accept it.',
      '',
      '| Item | Status |',
      '| --- | --- |',
      '| Render plan | Ready |',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[Source] --> B[Delivery]',
      '```',
      '',
      '![Architecture](architecture.png)',
      '',
    ].join('\n')
  );

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId: 'general-proposal',
    sourcePath,
    assetDir,
    deliveryDir,
  });
  assert.equal(result.ok, true, JSON.stringify(result, null, 2));
  return deliveryDir;
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function readQualityReport(deliveryDir) {
  return JSON.parse(fs.readFileSync(path.join(deliveryDir, 'quality-report.json'), 'utf8'));
}

function readDeliveryManifest(deliveryDir) {
  return JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));
}

function writeEvidenceFile(deliveryDir, name = 'wps-visual-evidence.png') {
  const evidencePath = path.join(path.dirname(deliveryDir), name);
  fs.writeFileSync(evidencePath, WPS_EVIDENCE_PNG);
  return evidencePath;
}

function writeTextEvidenceFile(deliveryDir, name = 'wps-visual-evidence.txt') {
  const evidencePath = path.join(path.dirname(deliveryDir), name);
  fs.writeFileSync(evidencePath, 'WPS visual review evidence\n', 'utf8');
  return evidencePath;
}

test('recordWpsVisualAcceptance marks WPS visual check as passed and clears not-verified warning', async (t) => {
  const deliveryDir = await makeDelivery(t);
  const evidencePath = writeEvidenceFile(deliveryDir);

  const result = recordWpsVisualAcceptance({
    deliveryDir,
    status: 'passed',
    reviewedAt: '2026-07-05T10:00:00.000Z',
    reviewedBy: 'user',
    note: '目录、图表、图片和版式已在 WPS 检查。',
    visualChecks: VISUAL_CHECKS,
    evidenceFiles: [evidencePath],
  });

  assert.equal(result.ok, true);
  assert.equal(result.qualityReport.status, 'passed');
  assert.deepEqual(result.qualityReport.warnings, []);
  const wpsVisual = result.qualityReport.checks.find((check) => check.id === 'wps_visual');
  assert.equal(wpsVisual.status, 'passed');
  assert.equal(wpsVisual.reviewedAt, '2026-07-05T10:00:00.000Z');
  assert.equal(wpsVisual.reviewedBy, 'user');
  assert.equal(wpsVisual.documentSha256, sha256File(path.join(deliveryDir, 'document.docx')));
  assert.deepEqual(wpsVisual.visualChecks, VISUAL_CHECKS);
  assert.equal(wpsVisual.visualEvidence.length, 1);
  assert.equal(wpsVisual.visualEvidence[0].sha256, sha256File(evidencePath));
  assert.equal(
    fs.existsSync(path.join(deliveryDir, wpsVisual.visualEvidence[0].path)),
    true
  );
  assert.match(wpsVisual.message, /目录、图表、图片和版式/);
  assert.equal(readQualityReport(deliveryDir).checks.find((check) => check.id === 'wps_visual').status, 'passed');
  assert.equal(
    readDeliveryManifest(deliveryDir).fileSha256.qualityReport,
    sha256File(path.join(deliveryDir, 'quality-report.json'))
  );
});

test('recordWpsVisualAcceptance rejects passed status without required visual checklist', async (t) => {
  const deliveryDir = await makeDelivery(t);

  assert.throws(
    () => recordWpsVisualAcceptance({
      deliveryDir,
      status: 'passed',
      reviewedAt: '2026-07-05T10:00:30.000Z',
      reviewedBy: 'user',
      note: '只写一句已检查不应算最终验收。',
    }),
    /visual checks.*document_opened|missing visual checks/i
  );
  const wpsVisual = readQualityReport(deliveryDir).checks.find((check) => check.id === 'wps_visual');
  assert.notEqual(wpsVisual?.status, 'passed');
});

test('recordWpsVisualAcceptance rejects passed status without visual evidence files', async (t) => {
  const deliveryDir = await makeDelivery(t);

  assert.throws(
    () => recordWpsVisualAcceptance({
      deliveryDir,
      status: 'passed',
      reviewedAt: '2026-07-05T10:00:45.000Z',
      reviewedBy: 'user',
      note: '只打勾但没有证据文件不应算最终验收。',
      visualChecks: VISUAL_CHECKS,
    }),
    /visual evidence|evidence file/i
  );
  const wpsVisual = readQualityReport(deliveryDir).checks.find((check) => check.id === 'wps_visual');
  assert.notEqual(wpsVisual?.status, 'passed');
});

test('recordWpsVisualAcceptance rejects non-visual evidence files for passed status', async (t) => {
  const deliveryDir = await makeDelivery(t);
  const evidencePath = writeTextEvidenceFile(deliveryDir);

  assert.throws(
    () => recordWpsVisualAcceptance({
      deliveryDir,
      status: 'passed',
      reviewedAt: '2026-07-05T10:00:50.000Z',
      reviewedBy: 'user',
      note: '文本说明不能冒充截图或导出件。',
      visualChecks: VISUAL_CHECKS,
      evidenceFiles: [evidencePath],
    }),
    /visual evidence.*image|screenshot|PDF|unsupported/i
  );
  const wpsVisual = readQualityReport(deliveryDir).checks.find((check) => check.id === 'wps_visual');
  assert.notEqual(wpsVisual?.status, 'passed');
});

test('recordWpsVisualAcceptance writes back the full final validation report', async (t) => {
  const deliveryDir = await makeDelivery(t);
  const evidencePath = writeEvidenceFile(deliveryDir, 'wps-final-evidence.png');
  const qualityReportPath = path.join(deliveryDir, 'quality-report.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const staleQualityReport = readQualityReport(deliveryDir);
  const deliveryManifest = readDeliveryManifest(deliveryDir);

  staleQualityReport.checks = staleQualityReport.checks.filter((check) => check.id !== 'replay_report');
  fs.writeFileSync(qualityReportPath, `${JSON.stringify(staleQualityReport, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.qualityReport = sha256File(qualityReportPath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const result = recordWpsVisualAcceptance({
    deliveryDir,
    status: 'passed',
    reviewedAt: '2026-07-05T10:01:00.000Z',
    reviewedBy: 'user',
    note: '最终验收前先刷新质量报告。',
    visualChecks: VISUAL_CHECKS,
    evidenceFiles: [evidencePath],
  });

  assert.ok(
    result.qualityReport.checks.some(
      (check) => check.id === 'replay_report' && check.status === 'passed'
    )
  );
  assert.ok(
    readQualityReport(deliveryDir).checks.some(
      (check) => check.id === 'replay_report' && check.status === 'passed'
    )
  );
});

test('recordWpsVisualAcceptance rejects WPS pass when automated package validation fails', async (t) => {
  const deliveryDir = await makeDelivery(t);
  fs.rmSync(path.join(deliveryDir, 'render-plan.json'));

  assert.throws(
    () => recordWpsVisualAcceptance({
      deliveryDir,
      status: 'passed',
      reviewedAt: '2026-07-05T10:03:00.000Z',
      reviewedBy: 'user',
      note: '不应允许自动校验失败后记录通过。',
      visualChecks: VISUAL_CHECKS,
      evidenceFiles: [writeEvidenceFile(deliveryDir, 'blocked-automation-evidence.txt')],
    }),
    /automated validation.*render-plan\.json/i
  );
  const wpsVisual = readQualityReport(deliveryDir).checks.find((check) => check.id === 'wps_visual');
  assert.notEqual(wpsVisual?.status, 'passed');
});

test('recordWpsVisualAcceptance rejects WPS pass when replay-report.json is missing', async (t) => {
  const deliveryDir = await makeDelivery(t);
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const deliveryManifest = readDeliveryManifest(deliveryDir);

  fs.rmSync(path.join(deliveryDir, 'replay-report.json'));
  delete deliveryManifest.files.replayReport;
  delete deliveryManifest.fileSha256.replayReport;
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  assert.throws(
    () => recordWpsVisualAcceptance({
      deliveryDir,
      status: 'passed',
      reviewedAt: '2026-07-05T10:03:30.000Z',
      reviewedBy: 'user',
      note: '不能把缺少重放证据的包标记为人工通过。',
      visualChecks: VISUAL_CHECKS,
      evidenceFiles: [writeEvidenceFile(deliveryDir, 'missing-replay-evidence.txt')],
    }),
    /automated validation.*replay-report\.json/i
  );
  const wpsVisual = readQualityReport(deliveryDir).checks.find((check) => check.id === 'wps_visual');
  assert.notEqual(wpsVisual?.status, 'passed');
});

test('recordWpsVisualAcceptance can record WPS failure when automated package validation fails', async (t) => {
  const deliveryDir = await makeDelivery(t);
  fs.rmSync(path.join(deliveryDir, 'render-plan.json'));

  const result = recordWpsVisualAcceptance({
    deliveryDir,
    status: 'failed',
    reviewedAt: '2026-07-05T10:04:00.000Z',
    reviewedBy: 'user',
    note: 'WPS 中也确认图目录异常。',
  });

  assert.equal(result.qualityReport.status, 'failed');
  const wpsVisual = result.qualityReport.checks.find((check) => check.id === 'wps_visual');
  assert.equal(wpsVisual.status, 'failed');
  assert.match(wpsVisual.message, /图目录异常/);
});

test('recordWpsVisualAcceptance records failed WPS visual inspection as report failure', async (t) => {
  const deliveryDir = await makeDelivery(t);

  const result = recordWpsVisualAcceptance({
    deliveryDir,
    status: 'failed',
    reviewedAt: '2026-07-05T10:05:00.000Z',
    reviewedBy: 'user',
    note: '图目录没有刷新。',
  });

  assert.equal(result.qualityReport.status, 'failed');
  assert.ok(result.qualityReport.failures.some((failure) => /图目录没有刷新/.test(failure)));
  const wpsVisual = result.qualityReport.checks.find((check) => check.id === 'wps_visual');
  assert.equal(wpsVisual.status, 'failed');
});

test('recordWpsVisualAcceptance clears previous visual evidence when recording WPS failure', async (t) => {
  const deliveryDir = await makeDelivery(t);
  const evidencePath = writeEvidenceFile(deliveryDir, 'previous-pass-evidence.txt');
  recordWpsVisualAcceptance({
    deliveryDir,
    status: 'passed',
    reviewedAt: '2026-07-05T10:05:30.000Z',
    reviewedBy: 'user',
    visualChecks: VISUAL_CHECKS,
    evidenceFiles: [evidencePath],
  });

  const result = recordWpsVisualAcceptance({
    deliveryDir,
    status: 'failed',
    reviewedAt: '2026-07-05T10:06:00.000Z',
    reviewedBy: 'user',
    note: '人工复查发现图片没有刷新。',
  });

  const wpsVisual = result.qualityReport.checks.find((check) => check.id === 'wps_visual');
  assert.equal(wpsVisual.status, 'failed');
  assert.equal(wpsVisual.visualChecks, undefined);
  assert.equal(wpsVisual.visualEvidence, undefined);
});

test('record-wps-visual CLI updates quality-report and emits JSON', async (t) => {
  const deliveryDir = await makeDelivery(t);
  const evidencePath = writeEvidenceFile(deliveryDir, 'cli-evidence.png');

  const result = spawnSync(process.execPath, [
    CLI,
    '--delivery-dir',
    deliveryDir,
    '--status',
    'passed',
    '--reviewer',
    'user',
    '--visual-check',
    'document_opened',
    '--visual-check',
    'layout_reviewed',
    '--visual-check',
    'content_order_reviewed',
    '--visual-check',
    'figures_reviewed',
    '--visual-check',
    'tables_reviewed',
    '--evidence-file',
    evidencePath,
    '--note',
    '已在 WPS 打开检查。',
    '--json',
  ], { cwd: ENGINE_ROOT, encoding: 'utf8' });

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = JSON.parse(result.stdout.trim());
  assert.equal(payload.ok, true);
  assert.equal(payload.qualityReport.status, 'passed');
  assert.equal(readQualityReport(deliveryDir).status, 'passed');
});
