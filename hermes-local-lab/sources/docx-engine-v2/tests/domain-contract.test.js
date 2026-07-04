const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { createDocumentJob, transitionJob } = require('../src/domain/document-job');
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

test('DocumentJob contract accepts deliveredAt after final delivery', () => {
  const documentJob = createDocumentJob({
    jobId: 'job-20260704-delivered',
    sourceRef: {
      type: 'markdown',
      path: 'source.md',
      sha256: '3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7',
    },
    templateId: 'general-proposal',
    workspace: path.join(os.tmpdir(), 'docx-engine-v2', 'job-20260704-delivered'),
    inputs: [{ type: 'source', path: 'source.md' }],
  });
  const sourceNormalized = transitionJob(documentJob, 'source_normalized');
  const templateSelected = transitionJob(sourceNormalized, 'template_selected');
  const assetsPackaged = transitionJob(templateSelected, 'assets_packaged');
  const renderPlanned = transitionJob(assetsPackaged, 'render_planned');
  const rendered = transitionJob(renderPlanned, 'rendered');
  const validated = transitionJob(rendered, 'validated');
  const delivered = transitionJob(validated, 'delivered', {
    deliveredAt: '2026-07-04T09:45:00.000Z',
  });

  const result = validateDomainObject('DocumentJob', delivered);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});

test('createDocumentJob returns a created job that matches DocumentJob schema', () => {
  const documentJob = createDocumentJob({
    jobId: 'job-20260704-helper',
    sourceRef: {
      type: 'markdown',
      path: 'source.md',
      sha256: '3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7',
    },
    templateId: 'general-proposal',
    workspace: path.join(os.tmpdir(), 'docx-engine-v2', 'job-20260704-helper'),
    inputs: [{ type: 'source', path: 'source.md' }],
  });

  assert.equal(documentJob.status, 'created');
  const result = validateDomainObject('DocumentJob', documentJob);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});

test('transitionJob allows the planned first transition', () => {
  const job = { status: 'created' };
  const nextJob = transitionJob(job, 'source_normalized', {
    warnings: ['source normalization preserved original Markdown.'],
  });

  assert.equal(nextJob.status, 'source_normalized');
  assert.deepEqual(nextJob.warnings, ['source normalization preserved original Markdown.']);
});

test('transitionJob rejects unknown target status', () => {
  assert.throws(
    () => transitionJob({ status: 'created' }, 'archived'),
    /Invalid job status: archived/
  );
});

test('transitionJob rejects backwards transitions after delivery', () => {
  assert.throws(
    () => transitionJob({ status: 'delivered' }, 'rendered'),
    /Invalid job transition: delivered -> rendered/
  );
});

test('validateDomainObject exposes Ajv error diagnostics', () => {
  const result = validateDomainObject('DocumentJob', {});

  assert.equal(result.ok, false);
  assert.ok(result.errors.length > 0);
  assert.equal(typeof result.errors[0].path, 'string');
  assert.equal(typeof result.errors[0].message, 'string');
  assert.equal(typeof result.errors[0].keyword, 'string');
  assert.equal(typeof result.errors[0].schemaPath, 'string');
  assert.equal(typeof result.errors[0].params, 'object');
});

test('SourcePackage contract models normalized source structure', () => {
  const sourcePackage = {
    schemaVersion: 'docx-engine-v2/source-package',
    sourceType: 'markdown',
    sourceRef: {
      type: 'markdown',
      path: 'source.md',
      sha256: '3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7',
    },
    title: 'Enterprise AI rollout proposal',
    sections: [
      {
        sectionId: 'sec-001',
        title: 'Executive summary',
        level: 1,
        blockIds: ['block-001'],
        metadata: { sourceLine: 1 },
      },
    ],
    blocks: [
      {
        id: 'block-001',
        type: 'paragraph',
        text: 'This proposal keeps source structure available for rendering.',
        content: { markdown: 'This proposal keeps source structure available for rendering.' },
        level: 1,
        sectionId: 'sec-001',
        sectionTitle: 'Executive summary',
        anchorText: 'This proposal keeps source structure',
        path: 'sections.0.blocks.0',
        caption: '',
        metadata: { sourceLine: 3 },
      },
    ],
    tables: [
      {
        tableId: 'tbl-001',
        title: '重点任务表',
        sectionId: 'sec-001',
        afterBlockId: 'block-001',
        anchorText: '重点任务表',
        headers: ['Task', 'Owner', 'Status'],
        rows: [['Template-based DOCX delivery', 'Document team', 'In progress']],
        metadata: { sourceLine: 8 },
      },
    ],
    figures: [
      {
        figureId: 'fig-001',
        caption: 'System architecture',
        sectionId: 'sec-001',
        anchorText: 'flowchart LR',
        sourceType: 'mermaid',
        editable: { path: 'assets/fig-001/source.mmd' },
        displayPath: 'assets/fig-001/figure.svg',
        dimensions: { width: 960, height: 540, unit: 'px' },
        quality: { status: 'not_verified', warnings: [] },
        metadata: { sourceLine: 18 },
      },
    ],
    images: [
      {
        imageId: 'image-001',
        path: 'source.assets/architecture.png',
        caption: 'Architecture',
        sectionId: 'sec-001',
        anchorText: '![Architecture]',
        metadata: { sourceLine: 24 },
      },
    ],
    embeddedMedia: [],
    warnings: [],
  };

  const result = validateDomainObject('SourcePackage', sourcePackage);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});

test('TemplatePackage contract requires plan-level manifest metadata', () => {
  const templatePackage = {
    schemaVersion: 'docx-engine-v2/template-package',
    templateId: 'general-proposal',
    files: {
      manifest: 'manifest.json',
      template: 'template.docx',
      schema: 'schema.json',
      prompt: 'prompt.md',
      sample: 'sample.json',
    },
    manifest: {
      id: 'general-proposal',
      name: 'General Proposal',
      version: '0.1.0',
      description: 'Proposal template for editable DOCX delivery.',
      documentTypes: ['proposal'],
      capabilities: ['sections', 'tables', 'figures', 'images'],
      requiredAssets: [],
      qualityGates: ['docx_zip', 'wps_visual'],
      compatibility: { engine: 'docx-engine-v2', minVersion: '0.1.0' },
    },
  };

  const result = validateDomainObject('TemplatePackage', templatePackage);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});

test('TemplatePackage rejects manifests missing plan-level quality metadata', () => {
  const result = validateDomainObject('TemplatePackage', {
    schemaVersion: 'docx-engine-v2/template-package',
    templateId: 'general-proposal',
    files: {
      manifest: 'manifest.json',
      template: 'template.docx',
      schema: 'schema.json',
      prompt: 'prompt.md',
      sample: 'sample.json',
    },
    manifest: {
      id: 'general-proposal',
      name: 'General Proposal',
      version: '0.1.0',
      description: 'Proposal template for editable DOCX delivery.',
    },
  });

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some(
      (error) => error.path === '/manifest' && error.params?.missingProperty === 'documentTypes'
    ),
    JSON.stringify(result.errors)
  );
});

test('AssetPackage contract separates figures and tables as first-class assets', () => {
  const assetPackage = {
    schemaVersion: 'docx-engine-v2/asset-package',
    assetDir: 'assets',
    figures: [
      {
        figureId: 'fig-001',
        caption: 'System architecture',
        sectionId: 'sec-001',
        anchorText: 'flowchart LR',
        sourceType: 'mermaid',
        editable: { path: 'assets/fig-001/source.mmd', format: 'mermaid' },
        displayPath: 'assets/fig-001/figure.svg',
        dimensions: { width: 960, height: 540, unit: 'px' },
        quality: { status: 'not_verified', warnings: [] },
        metadata: { sourceLine: 18 },
      },
    ],
    tables: [
      {
        tableId: 'tbl-001',
        title: '重点任务表',
        sectionId: 'sec-001',
        afterBlockId: 'block-001',
        anchorText: '重点任务表',
        metadata: { sourceLine: 8 },
      },
    ],
    images: [
      {
        imageId: 'image-001',
        sourcePath: 'source.assets/architecture.png',
        displayPath: 'assets/image-001/architecture.png',
        caption: 'Architecture',
        sectionId: 'sec-001',
        metadata: { sourceLine: 24 },
      },
    ],
    warnings: [],
  };

  const result = validateDomainObject('AssetPackage', assetPackage);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});

test('RenderPlan contract carries sections, assets, and template data bindings', () => {
  const renderPlan = {
    schemaVersion: 'docx-engine-v2/render-plan',
    jobId: 'job-20260704-001',
    templateId: 'general-proposal',
    sections: [
      {
        sectionId: 'sec-001',
        title: 'Executive summary',
        level: 1,
        blockIds: ['block-001'],
        metadata: { sourceLine: 1 },
      },
    ],
    tables: [
      {
        tableId: 'tbl-001',
        title: '重点任务表',
        sectionId: 'sec-001',
        afterBlockId: 'block-001',
        anchorText: '重点任务表',
        metadata: { templatePath: 'tables.0' },
      },
    ],
    figures: [
      {
        figureId: 'fig-001',
        caption: 'System architecture',
        sectionId: 'sec-001',
        afterBlockId: 'block-001',
        anchorText: 'flowchart LR',
        displayPath: 'assets/fig-001/figure.svg',
        metadata: { templatePath: 'images.0' },
      },
    ],
    templateData: {
      title: 'Enterprise AI rollout proposal',
      sections: [],
      images: [
        {
          figureId: 'fig-001',
          path: 'assets/fig-001/figure.svg',
          caption: 'System architecture',
          metadata: { templatePath: 'images.0' },
        },
      ],
      tables: [
        {
          tableId: 'tbl-001',
          title: '重点任务表',
          rows: [{ Task: 'Template-based DOCX delivery', Owner: 'Document team' }],
          metadata: { templatePath: 'tables.0' },
        },
      ],
      metadata: { templateId: 'general-proposal' },
    },
    warnings: [],
  };

  assert.equal(renderPlan.figures[0].figureId, 'fig-001');
  assert.equal(renderPlan.templateData.images[0].figureId, 'fig-001');
  assert.equal(renderPlan.templateData.tables[0].tableId, 'tbl-001');
  const result = validateDomainObject('RenderPlan', renderPlan);
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

test('ValidationReport contract can record WPS visual reviewer evidence', () => {
  const validationReport = {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'passed',
    checks: [
      {
        id: 'wps_visual',
        status: 'passed',
        message: 'WPS/Word visual inspection passed.',
        reviewedAt: '2026-07-05T10:00:00.000Z',
        reviewedBy: 'user',
      },
    ],
    warnings: [],
    failures: [],
  };

  const result = validateDomainObject('ValidationReport', validationReport);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
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
