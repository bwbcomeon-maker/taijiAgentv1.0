const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { packageAssets } = require('../src/assets/package-assets');
const { buildRenderPlan } = require('../src/planning/build-render-plan');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');
const { getTemplatePackage } = require('../src/templates/registry');
const { writeDeliveryPackage } = require('../src/delivery/write-delivery-package');
const { postprocessDocx } = require('../src/rendering/postprocess-docx');
const { renderDocx } = require('../src/rendering/render-docx');
const { validateDeliveryPackage } = require('../src/validation/validate-delivery-package');

const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-delivery-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

async function makeDeliveryPackage(t) {
  const workspace = makeWorkspace(t);
  const assetDir = path.join(workspace, 'source.assets');
  const sourcePath = path.join(workspace, 'source.md');
  const assetOutDir = path.join(workspace, 'assets');
  const renderPath = path.join(workspace, 'rendered.docx');
  const postprocessedPath = path.join(workspace, 'document.docx');
  const deliveryDir = path.join(workspace, 'delivery');

  fs.mkdirSync(assetDir);
  fs.writeFileSync(path.join(assetDir, 'architecture.png'), ONE_BY_ONE_PNG);
  fs.writeFileSync(
    sourcePath,
    [
      '# Enterprise AI rollout proposal',
      '',
      '## Architecture',
      '',
      'The delivery package must keep source, assets, render plan, and quality checks together.',
      '',
      '| Item | Status |',
      '| --- | --- |',
      '| Render plan | Ready |',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[Source] --> B[Render plan]',
      '  B --> C[Delivery package]',
      '```',
      '',
      '![Architecture](architecture.png)',
      '',
    ].join('\n')
  );

  const sourcePackage = await normalizeMarkdownSource({ sourcePath });
  const templatePackage = getTemplatePackage('general-proposal');
  const assetPackage = packageAssets({ sourcePackage, assetDir, outDir: assetOutDir });
  const renderPlan = buildRenderPlan({ sourcePackage, templatePackage, assetPackage });

  await renderDocx({ templatePackage, renderPlan, outputPath: renderPath });
  await postprocessDocx({ docxPath: renderPath, renderPlan, outputPath: postprocessedPath });
  writeDeliveryPackage({
    deliveryDir,
    job: {
      jobId: renderPlan.jobId,
      sourceRef: sourcePackage.sourceRef,
      templateId: templatePackage.templateId,
      status: 'validated',
    },
    sourcePackage,
    templatePackage,
    assetPackage,
    renderPlan,
    documentPath: postprocessedPath,
    qualityReport: {
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
        { id: 'wps_visual', status: 'not_verified' },
      ],
      warnings: ['WPS visual inspection has not been performed.'],
      failures: [],
    },
  });

  return { deliveryDir };
}

test('validateDeliveryPackage accepts complete delivery package and reports required checks', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  const report = validateDeliveryPackage({ deliveryDir });

  assert.ok(['passed', 'passed_with_warnings'].includes(report.status));
  assert.ok(report.checks.some((check) => check.id === 'schema' && check.status === 'passed'));
  assert.ok(report.checks.some((check) => check.id === 'docx_zip' && check.status === 'passed'));
  assert.ok(
    report.checks.some((check) => check.id === 'figure_id_metadata' && check.status === 'passed')
  );
  assert.ok(
    report.checks.some((check) => check.id === 'wps_visual' && check.status === 'not_verified')
  );
  assert.deepEqual(
    report.checks.map((check) => check.id),
    [
      'schema',
      'docx_zip',
      'template_markers',
      'image_coverage',
      'table_coverage',
      'figure_id_metadata',
      'delivery_files',
      'wps_visual',
    ]
  );
});

test('validateDeliveryPackage fails when a required delivery file is missing', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  fs.rmSync(path.join(deliveryDir, 'render-plan.json'));
  const report = validateDeliveryPackage({ deliveryDir });

  assert.equal(report.status, 'failed');
  assert.ok(report.failures.some((item) => item.includes('render-plan.json')));
});
