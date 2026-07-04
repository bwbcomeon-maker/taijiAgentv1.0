const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { validateDomainObject } = require('../src/domain/validate');

test('DocumentJob contract accepts a complete render job', () => {
  const documentJob = {
    jobId: 'job-20260704-001',
    createdAt: '2026-07-04T09:30:00.000Z',
    sourceRef: {
      type: 'markdown',
      path: 'source.md',
      sha256: '3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7',
    },
    templateId: 'general-proposal',
    status: 'created',
    workspace: path.join(os.tmpdir(), 'docx-engine-v2', 'job-20260704-001'),
    inputs: [
      { type: 'source', path: 'source.md' },
      { type: 'asset_dir', path: 'assets' },
    ],
    outputs: [
      { type: 'document', path: 'delivery/document.docx' },
      { type: 'delivery_package', path: 'delivery' },
    ],
    warnings: [],
    failures: [],
  };

  const result = validateDomainObject('DocumentJob', documentJob);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});

test('ValidationReport contract preserves WPS visual acceptance as not_verified', () => {
  const validationReport = {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'passed_with_warnings',
    checks: [
      {
        id: 'docx_zip',
        status: 'passed',
      },
      {
        id: 'wps_visual',
        status: 'not_verified',
      },
    ],
    warnings: ['WPS visual acceptance has not been verified by a human reviewer.'],
    failures: [],
  };

  const result = validateDomainObject('ValidationReport', validationReport);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
  assert.equal(validationReport.status, 'passed_with_warnings');
  assert.equal(
    validationReport.checks.find((check) => check.id === 'docx_zip')?.status,
    'passed'
  );
  assert.equal(
    validationReport.checks.find((check) => check.id === 'wps_visual')?.status,
    'not_verified'
  );
});

test('DeliveryPackage contract requires the complete editable delivery bundle', () => {
  const deliveryPackage = {
    schemaVersion: 'docx-engine-v2/delivery-package',
    deliveryDir: path.join(os.tmpdir(), 'delivery'),
    files: {
      document: 'document.docx',
      source: 'source.md',
      assetsDir: 'assets',
      jobManifest: 'job.manifest.json',
      templateManifest: 'template.manifest.json',
      renderPlan: 'render-plan.json',
      qualityReport: 'quality-report.json',
      imageInstructions: 'README-图片调整说明.md',
    },
    status: 'delivered',
  };

  const result = validateDomainObject('DeliveryPackage', deliveryPackage);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});
