const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { writeDeliveryPackage } = require('../src/delivery/write-delivery-package');
const { createDocumentJob, transitionJob } = require('../src/domain/document-job');

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-delivery-write-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

function makePackageInput(t) {
  const workspace = makeWorkspace(t);
  const sourcePath = path.join(workspace, 'source.md');
  const documentPath = path.join(workspace, 'document.docx');
  fs.writeFileSync(sourcePath, '# Source\n\nBody\n', 'utf8');
  fs.writeFileSync(documentPath, 'not-a-real-docx-but-writeDeliveryPackage-only-copies-it', 'utf8');

  const sourcePackage = {
    schemaVersion: 'docx-engine-v2/source-package',
    sourceType: 'markdown',
    sourceRef: {
      type: 'markdown',
      path: sourcePath,
      sha256: 'a'.repeat(64),
    },
    title: 'Source',
    sections: [],
    blocks: [],
    tables: [],
    figures: [],
    images: [],
    embeddedMedia: [],
    warnings: [],
  };
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
      name: '通用方案模板',
      version: '1.0.0',
      description: '用于项目建设方案、技术方案、汇报方案等通用方案类文档。',
      documentTypes: ['proposal'],
      capabilities: ['sections', 'tables', 'images'],
      requiredAssets: [],
      qualityGates: ['schema_valid', 'wps_visual'],
      compatibility: { engine: 'docx-engine-v2', minVersion: '0.1.0' },
    },
  };
  const assetPackage = {
    schemaVersion: 'docx-engine-v2/asset-package',
    assetDir: path.join(workspace, 'assets'),
    figures: [],
    tables: [],
    images: [],
    warnings: [],
  };
  const renderPlan = {
    schemaVersion: 'docx-engine-v2/render-plan',
    jobId: 'job-delivery-write',
    templateId: 'general-proposal',
    sections: [],
    tables: [],
    figures: [],
    templateData: {
      metadata: { assetDir: 'assets' },
      sections: [],
      tables: [],
      images: [],
    },
  };
  const qualityReport = {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'passed_with_warnings',
    checks: [{ id: 'wps_visual', status: 'not_verified' }],
    warnings: ['WPS/Word visual inspection has not been performed.'],
    failures: [],
  };
  const job = createValidatedJob({
    sourcePackage,
    renderPlan,
    templatePackage,
    workspace,
    documentPath,
  });

  return {
    workspace,
    deliveryDir: path.join(workspace, 'delivery'),
    documentPath,
    sourcePackage,
    templatePackage,
    assetPackage,
    renderPlan,
    qualityReport,
    job,
  };
}

function createValidatedJob({ sourcePackage, renderPlan, templatePackage, workspace, documentPath }) {
  let job = createDocumentJob({
    jobId: renderPlan.jobId,
    sourceRef: sourcePackage.sourceRef,
    templateId: templatePackage.templateId,
    workspace,
    inputs: [{ type: 'source', path: sourcePackage.sourceRef.path }],
  });
  job = transitionJob(job, 'source_normalized');
  job = transitionJob(job, 'template_selected', { templateId: templatePackage.templateId });
  job = transitionJob(job, 'assets_packaged');
  job = transitionJob(job, 'render_planned');
  job = transitionJob(job, 'rendered', { outputs: [{ type: 'rendered_document', path: documentPath }] });
  return transitionJob(job, 'validated');
}

test('writeDeliveryPackage rejects an untraceable job manifest before writing files', (t) => {
  const input = makePackageInput(t);

  assert.throws(
    () => writeDeliveryPackage({ ...input, job: { jobId: 'missing-traceability' } }),
    /DocumentJob manifest validation failed/
  );
  assert.equal(fs.existsSync(input.deliveryDir), false);
});

test('writeDeliveryPackage rejects an incomplete template manifest before writing files', (t) => {
  const input = makePackageInput(t);

  assert.throws(
    () =>
      writeDeliveryPackage({
        ...input,
        templatePackage: {
          ...input.templatePackage,
          manifest: { id: 'general-proposal', name: '通用方案模板' },
        },
      }),
    /TemplateManifest validation failed/
  );
  assert.equal(fs.existsSync(input.deliveryDir), false);
});
