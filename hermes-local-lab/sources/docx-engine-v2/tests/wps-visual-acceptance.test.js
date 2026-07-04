const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const { recordWpsVisualAcceptance } = require('../src/validation/record-wps-visual-acceptance');

const ENGINE_ROOT = path.resolve(__dirname, '..');
const CLI = path.join(ENGINE_ROOT, 'src', 'cli', 'record-wps-visual.js');

function makeDelivery(t) {
  const deliveryDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-wps-'));
  t.after(() => fs.rmSync(deliveryDir, { recursive: true, force: true }));
  fs.writeFileSync(path.join(deliveryDir, 'document.docx'), 'reviewed document bytes');
  writeQualityReport(deliveryDir, {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'passed_with_warnings',
    checks: [
      { id: 'schema', status: 'passed' },
      { id: 'docx_zip', status: 'passed' },
      { id: 'template_markers', status: 'passed' },
      { id: 'image_coverage', status: 'passed' },
      { id: 'table_coverage', status: 'passed' },
      { id: 'figure_id_metadata', status: 'passed' },
      { id: 'delivery_files', status: 'passed' },
      { id: 'wps_visual', status: 'not_verified', message: 'WPS/Word visual inspection has not been performed.' },
    ],
    warnings: ['WPS/Word visual inspection has not been performed.'],
    failures: [],
  });
  return deliveryDir;
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function writeQualityReport(deliveryDir, report) {
  fs.writeFileSync(path.join(deliveryDir, 'quality-report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
}

function readQualityReport(deliveryDir) {
  return JSON.parse(fs.readFileSync(path.join(deliveryDir, 'quality-report.json'), 'utf8'));
}

test('recordWpsVisualAcceptance marks WPS visual check as passed and clears not-verified warning', (t) => {
  const deliveryDir = makeDelivery(t);
  const qualityReportPath = path.join(deliveryDir, 'quality-report.json');
  const report = JSON.parse(fs.readFileSync(qualityReportPath, 'utf8'));
  report.warnings.push('WPS visual acceptance has not been verified by a human reviewer.');
  report.warnings.push('WPS visual inspection has not been performed.');
  fs.writeFileSync(qualityReportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');

  const result = recordWpsVisualAcceptance({
    deliveryDir,
    status: 'passed',
    reviewedAt: '2026-07-05T10:00:00.000Z',
    reviewedBy: 'user',
    note: '目录、图表、图片和版式已在 WPS 检查。',
  });

  assert.equal(result.ok, true);
  assert.equal(result.qualityReport.status, 'passed');
  assert.deepEqual(result.qualityReport.warnings, []);
  const wpsVisual = result.qualityReport.checks.find((check) => check.id === 'wps_visual');
  assert.equal(wpsVisual.status, 'passed');
  assert.equal(wpsVisual.reviewedAt, '2026-07-05T10:00:00.000Z');
  assert.equal(wpsVisual.reviewedBy, 'user');
  assert.equal(wpsVisual.documentSha256, sha256File(path.join(deliveryDir, 'document.docx')));
  assert.match(wpsVisual.message, /目录、图表、图片和版式/);
  assert.equal(readQualityReport(deliveryDir).checks.find((check) => check.id === 'wps_visual').status, 'passed');
});

test('recordWpsVisualAcceptance records failed WPS visual inspection as report failure', (t) => {
  const deliveryDir = makeDelivery(t);

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

test('record-wps-visual CLI updates quality-report and emits JSON', (t) => {
  const deliveryDir = makeDelivery(t);

  const result = spawnSync(process.execPath, [
    CLI,
    '--delivery-dir',
    deliveryDir,
    '--status',
    'passed',
    '--reviewer',
    'user',
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
