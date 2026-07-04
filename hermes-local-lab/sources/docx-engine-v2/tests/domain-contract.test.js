const assert = require('node:assert/strict');
const test = require('node:test');

const { validateDomainObject } = require('../src/domain/validate');

test('DocumentJob contract accepts a complete render job', () => {
  const documentJob = {
    jobId: 'job-20260704-001',
    createdAt: '2026-07-04T09:30:00.000Z',
    sourceRef: {
      type: 'markdown',
      path: 'source.md',
      sha256: 'c9ad04f6c5f4d9a46c95c6642ce8c09b',
    },
    templateId: 'general-proposal',
    status: 'planned',
    workspace: {
      root: '/tmp/docx-engine-v2/job-20260704-001',
      deliveryDir: '/tmp/docx-engine-v2/job-20260704-001/delivery',
    },
    inputs: {
      source: 'source.md',
      assetDir: 'assets',
    },
    outputs: {
      document: 'document.docx',
      deliveryPackage: 'delivery',
    },
    warnings: [],
    failures: [],
  };

  assert.deepEqual(validateDomainObject('DocumentJob', documentJob), { ok: true });
});

test('ValidationReport contract preserves WPS and Word visual checks as not_verified', () => {
  const validationReport = {
    reportId: 'quality-report-job-20260704-001',
    jobId: 'job-20260704-001',
    status: 'passed_with_warnings',
    checks: [
      {
        id: 'package_manifest',
        status: 'passed',
      },
      {
        id: 'wps_visual',
        status: 'not_verified',
        required: true,
      },
      {
        id: 'word_visual',
        status: 'not_verified',
        required: true,
      },
    ],
    warnings: [
      {
        code: 'visual_acceptance_not_verified',
        message: 'WPS/Word visual acceptance must be completed by a human reviewer.',
      },
    ],
    failures: [],
  };

  assert.deepEqual(validateDomainObject('ValidationReport', validationReport), { ok: true });
  assert.equal(validationReport.status, 'passed_with_warnings');
  assert.equal(
    validationReport.checks.find((check) => check.id === 'wps_visual')?.status,
    'not_verified'
  );
  assert.equal(
    validationReport.checks.find((check) => check.id === 'word_visual')?.status,
    'not_verified'
  );
});

test('DeliveryPackage contract requires the complete editable delivery bundle', () => {
  const deliveryPackage = {
    packageId: 'delivery-job-20260704-001',
    jobId: 'job-20260704-001',
    templateId: 'general-proposal',
    createdAt: '2026-07-04T09:35:00.000Z',
    status: 'ready',
    delivery: {
      root: '/tmp/docx-engine-v2/job-20260704-001/delivery',
      files: [
        'document.docx',
        'source.md',
        'assets',
        'job.manifest.json',
        'template.manifest.json',
        'render-plan.json',
        'quality-report.json',
        'README-图片调整说明.md',
      ],
    },
  };

  assert.deepEqual(validateDomainObject('DeliveryPackage', deliveryPackage), { ok: true });
});
