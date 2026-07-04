const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { once } = require('node:events');
const test = require('node:test');
const yauzl = require('yauzl');
const yazl = require('yazl');

const { packageAssets } = require('../src/assets/package-assets');
const { buildRenderPlan } = require('../src/planning/build-render-plan');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');
const { getTemplatePackage } = require('../src/templates/registry');
const { writeDeliveryPackage } = require('../src/delivery/write-delivery-package');
const { createDocumentJob, transitionJob } = require('../src/domain/document-job');
const { postprocessDocx } = require('../src/rendering/postprocess-docx');
const { renderDocx } = require('../src/rendering/render-docx');
const { validateDeliveryPackage } = require('../src/validation/validate-delivery-package');
const { recordWpsVisualAcceptance } = require('../src/validation/record-wps-visual-acceptance');

const ENGINE_ROOT = path.join(__dirname, '..');
const VALIDATE_DELIVERY = path.join(ENGINE_ROOT, 'src', 'cli', 'validate-delivery.js');
const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function parseStdoutJson(result) {
  try {
    return JSON.parse(result.stdout.trim());
  } catch (error) {
    assert.fail(`stdout is not JSON:\n${result.stdout}\nstderr:\n${result.stderr}\nerror: ${error.message}`);
  }
}

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-delivery-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

function defaultSourceLines() {
  return [
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
  ];
}

async function makeDeliveryPackage(t, sourceLines = defaultSourceLines()) {
  const workspace = makeWorkspace(t);
  const assetDir = path.join(workspace, 'source.assets');
  const sourcePath = path.join(workspace, 'source.md');
  const assetOutDir = path.join(workspace, 'assets');
  const renderPath = path.join(workspace, 'rendered.docx');
  const postprocessedPath = path.join(workspace, 'document.docx');
  const deliveryDir = path.join(workspace, 'delivery');

  fs.mkdirSync(assetDir);
  fs.writeFileSync(path.join(assetDir, 'architecture.png'), ONE_BY_ONE_PNG);
  fs.writeFileSync(sourcePath, sourceLines.join('\n'));

  const sourcePackage = await normalizeMarkdownSource({ sourcePath });
  const templatePackage = getTemplatePackage('general-proposal');
  const assetPackage = packageAssets({ sourcePackage, assetDir, outDir: assetOutDir });
  const renderPlan = buildRenderPlan({ sourcePackage, templatePackage, assetPackage });

  await renderDocx({ templatePackage, renderPlan, outputPath: renderPath });
  await postprocessDocx({ docxPath: renderPath, renderPlan, outputPath: postprocessedPath });
  const job = buildValidatedJob({
    workspace,
    sourcePackage,
    templatePackage,
    renderPlan,
    sourcePath,
    assetDir,
    documentPath: postprocessedPath,
  });
  writeDeliveryPackage({
    deliveryDir,
    job,
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
        { id: 'table_placement', status: 'passed' },
        { id: 'table_caption', status: 'passed' },
        { id: 'block_order', status: 'passed' },
        { id: 'figure_id_metadata', status: 'passed' },
        { id: 'figure_placement', status: 'passed' },
        { id: 'figure_caption', status: 'passed' },
        { id: 'delivery_files', status: 'passed' },
        { id: 'wps_visual', status: 'not_verified' },
      ],
      warnings: ['WPS visual inspection has not been performed.'],
      failures: [],
    },
  });

  return { deliveryDir };
}

function buildValidatedJob({
  workspace,
  sourcePackage,
  templatePackage,
  renderPlan,
  sourcePath,
  assetDir,
  documentPath,
}) {
  let job = createDocumentJob({
    jobId: renderPlan.jobId,
    sourceRef: sourcePackage.sourceRef,
    templateId: templatePackage.templateId,
    workspace,
    inputs: [
      { type: 'source', path: sourcePath },
      { type: 'asset_dir', path: assetDir },
    ],
  });
  job = transitionJob(job, 'source_normalized');
  job = transitionJob(job, 'template_selected', { templateId: templatePackage.templateId });
  job = transitionJob(job, 'assets_packaged');
  job = transitionJob(job, 'render_planned');
  job = transitionJob(job, 'rendered', { outputs: [{ type: 'rendered_document', path: documentPath }] });
  return transitionJob(job, 'validated');
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
  assert.ok(
    report.checks.some(
      (check) => check.id === 'image_coverage' && check.status === 'passed'
    )
  );
  assert.ok(
    report.checks.some(
      (check) => check.id === 'table_coverage' && check.status === 'passed'
    )
  );
  assert.ok(
    report.checks.some(
      (check) => check.id === 'table_placement' && check.status === 'passed'
    )
  );
  assert.ok(
    report.checks.some(
      (check) => check.id === 'table_caption' && check.status === 'passed'
    )
  );
  assert.ok(
    report.checks.some(
      (check) => check.id === 'block_order' && check.status === 'passed'
    )
  );
  assert.ok(
    report.checks.some(
      (check) => check.id === 'figure_placement' && check.status === 'passed'
    )
  );
  assert.ok(
    report.checks.some(
      (check) => check.id === 'figure_caption' && check.status === 'passed'
    )
  );
  assert.deepEqual(
    report.checks.map((check) => check.id),
    [
      'schema',
      'source_original',
      'docx_zip',
      'template_markers',
      'image_coverage',
      'table_coverage',
      'table_placement',
      'table_caption',
      'block_order',
      'figure_id_metadata',
      'figure_placement',
      'figure_caption',
      'delivery_files',
      'wps_visual',
    ]
  );
});

test('validate-delivery CLI emits a delivery quality report as JSON', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  const result = spawnSync(process.execPath, [
    VALIDATE_DELIVERY,
    '--delivery-dir',
    deliveryDir,
    '--json',
  ], { cwd: ENGINE_ROOT, encoding: 'utf8' });

  assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = parseStdoutJson(result);
  assert.equal(payload.ok, true);
  assert.equal(payload.deliveryDir, deliveryDir);
  assert.ok(['passed', 'passed_with_warnings'].includes(payload.qualityReport.status));
  assert.ok(payload.qualityReport.checks.some((check) => check.id === 'delivery_files'));
  assert.ok(payload.qualityReport.checks.some((check) => check.id === 'wps_visual'));
});

test('validate-delivery CLI exits nonzero when the delivery package is invalid', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  fs.rmSync(path.join(deliveryDir, 'render-plan.json'));

  const result = spawnSync(process.execPath, [
    VALIDATE_DELIVERY,
    '--delivery-dir',
    deliveryDir,
    '--json',
  ], { cwd: ENGINE_ROOT, encoding: 'utf8' });

  assert.equal(result.status, 3, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = parseStdoutJson(result);
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'delivery_validation_failed');
  assert.equal(payload.qualityReport.status, 'failed');
  assert.ok(payload.failures.some((failure) => /render-plan\.json/.test(failure)));
});

test('validate-delivery CLI writes the refreshed quality report when requested', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  fs.rmSync(path.join(deliveryDir, 'render-plan.json'));

  const result = spawnSync(process.execPath, [
    VALIDATE_DELIVERY,
    '--delivery-dir',
    deliveryDir,
    '--write-report',
    '--json',
  ], { cwd: ENGINE_ROOT, encoding: 'utf8' });

  assert.equal(result.status, 3, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  const payload = parseStdoutJson(result);
  const writtenReport = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'quality-report.json'), 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));

  assert.equal(payload.ok, false);
  assert.equal(payload.qualityReport.status, 'failed');
  assert.equal(writtenReport.status, 'failed');
  assert.ok(writtenReport.failures.some((failure) => /render-plan\.json/.test(failure)));
  assert.equal(deliveryManifest.fileSha256.qualityReport, sha256File(path.join(deliveryDir, 'quality-report.json')));
});

test('validateDeliveryPackage fails when document.docx no longer matches the delivery manifest hash', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const documentPath = path.join(deliveryDir, 'document.docx');
  const deliveryManifest = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));

  assert.equal(deliveryManifest.documentSha256, sha256File(documentPath));

  fs.appendFileSync(documentPath, 'tampered-after-delivery');
  const report = validateDeliveryPackage({ deliveryDir });

  assert.equal(report.status, 'failed');
  assert.ok(report.failures.some((failure) => /document\.docx sha256 mismatch/.test(failure)));
});

test('validateDeliveryPackage fails when source.md no longer matches the delivery manifest hash', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePath = path.join(deliveryDir, 'source.md');
  const deliveryManifest = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));

  assert.equal(deliveryManifest.sourceSha256, sha256File(sourcePath));

  fs.appendFileSync(sourcePath, '\nTampered source summary.\n');
  const report = validateDeliveryPackage({ deliveryDir });

  assert.equal(report.status, 'failed');
  assert.ok(report.failures.some((failure) => /source\.md sha256 mismatch/.test(failure)));
});

test('validateDeliveryPackage fails when source-package.json no longer matches the delivery manifest hash', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifest = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));

  assert.ok(deliveryManifest.fileSha256?.sourcePackage, 'delivery manifest must bind source-package.json sha256');
  assert.equal(deliveryManifest.fileSha256.sourcePackage, sha256File(sourcePackagePath));

  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  sourcePackage.warnings = [...(sourcePackage.warnings || []), 'tampered after delivery'];
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  const report = validateDeliveryPackage({ deliveryDir });

  assert.equal(report.status, 'failed');
  assert.ok(report.failures.some((failure) => /source-package\.json sha256 mismatch/.test(failure)));
});

test('validateDeliveryPackage fails when source package sourceRef disagrees with the job manifest', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  sourcePackage.sourceRef.sha256 = '0'.repeat(64);
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.sourcePackage = sha256File(sourcePackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /source-package\.json sourceRef.*job\.manifest\.json sourceRef/);
});

test('validateDeliveryPackage fails when render plan sections disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(sourcePackage.sections.length > 0, 'fixture must include normalized source sections');
  sourcePackage.sections[0].title = 'Tampered source section';
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.sourcePackage = sha256File(sourcePackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /render-plan\.json sections.*source-package\.json sections/);
});

test('validateDeliveryPackage fails when render plan tables disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(sourcePackage.tables.length > 0, 'fixture must include normalized source tables');
  sourcePackage.tables[0].title = 'Tampered source table';
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.sourcePackage = sha256File(sourcePackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /render-plan\.json tables.*source-package\.json tables/);
});

test('validateDeliveryPackage fails when render plan figures disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(sourcePackage.figures.length > 0, 'fixture must include normalized source figures');
  sourcePackage.figures[0].caption = 'Tampered source figure';
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.sourcePackage = sha256File(sourcePackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /render-plan\.json figures.*source-package\.json figures/);
});

test('validateDeliveryPackage fails when render plan source images disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(sourcePackage.images.length > 0, 'fixture must include normalized source images');
  sourcePackage.images[0].caption = 'Tampered source image';
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.sourcePackage = sha256File(sourcePackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /render-plan\.json templateData\.images.*source-package\.json images/);
});

test('validateDeliveryPackage fails when render plan section block text disagrees with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));
  const paragraph = sourcePackage.blocks.find((block) => block.type === 'paragraph' && block.text);

  assert.ok(paragraph, 'fixture must include normalized paragraph blocks');
  paragraph.text = 'Tampered normalized source paragraph';
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.sourcePackage = sha256File(sourcePackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /render-plan\.json templateData\.sections.*source-package\.json blocks/);
});

test('validateDeliveryPackage fails when render plan section block ids disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const sourcePackagePath = path.join(deliveryDir, 'source-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const sourcePackage = JSON.parse(fs.readFileSync(sourcePackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));
  const section = sourcePackage.sections.find((item) => (item.blockIds || []).length > 1);

  assert.ok(section, 'fixture must include a section with multiple block ids');
  section.blockIds = section.blockIds.slice(1);
  fs.writeFileSync(sourcePackagePath, `${JSON.stringify(sourcePackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.sourcePackage = sha256File(sourcePackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /blockIds|render-plan\.json sections.*source-package\.json sections/);
});

test('validateDeliveryPackage fails when render-plan.json no longer matches the delivery manifest hash', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const renderPlanPath = path.join(deliveryDir, 'render-plan.json');
  const deliveryManifest = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));

  assert.ok(deliveryManifest.fileSha256?.renderPlan, 'delivery manifest must bind render-plan.json sha256');
  assert.equal(deliveryManifest.fileSha256.renderPlan, sha256File(renderPlanPath));

  const renderPlan = JSON.parse(fs.readFileSync(renderPlanPath, 'utf8'));
  renderPlan.warnings = [...(renderPlan.warnings || []), 'tampered after delivery'];
  fs.writeFileSync(renderPlanPath, `${JSON.stringify(renderPlan, null, 2)}\n`, 'utf8');
  const report = validateDeliveryPackage({ deliveryDir });

  assert.equal(report.status, 'failed');
  assert.ok(report.failures.some((failure) => /render-plan\.json sha256 mismatch/.test(failure)));
});

test('validateDeliveryPackage fails when asset-package.json no longer matches the delivery manifest hash', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifest = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));

  assert.ok(deliveryManifest.fileSha256?.assetPackage, 'delivery manifest must bind asset-package.json sha256');
  assert.equal(deliveryManifest.fileSha256.assetPackage, sha256File(assetPackagePath));

  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  assetPackage.warnings = [...(assetPackage.warnings || []), 'tampered after delivery'];
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  const report = validateDeliveryPackage({ deliveryDir });

  assert.equal(report.status, 'failed');
  assert.ok(report.failures.some((failure) => /asset-package\.json sha256 mismatch/.test(failure)));
});

test('validateDeliveryPackage fails when asset package tables disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(assetPackage.tables.length > 0, 'fixture must include packaged tables');
  assetPackage.tables[0].title = 'Tampered asset table';
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.assetPackage = sha256File(assetPackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /asset-package\.json tables.*source-package\.json tables/);
});

test('validateDeliveryPackage fails when asset package figures disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(assetPackage.figures.length > 0, 'fixture must include packaged figures');
  assetPackage.figures[0].caption = 'Tampered asset figure';
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.assetPackage = sha256File(assetPackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /asset-package\.json figures.*source-package\.json figures/);
});

test('validateDeliveryPackage fails when asset package source images disagree with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(assetPackage.images.length > 0, 'fixture must include packaged source images');
  assetPackage.images[0].caption = 'Tampered asset image';
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.assetPackage = sha256File(assetPackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /asset-package\.json images.*source-package\.json images/);
});

test('validateDeliveryPackage fails when asset package source image path disagrees with the source package', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(assetPackage.images.length > 0, 'fixture must include packaged source images');
  assetPackage.images[0].sourcePath = 'source.assets/other-image.png';
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.assetPackage = sha256File(assetPackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /asset-package\.json images.*source-package\.json images.*path/);
});

test('validateDeliveryPackage fails when asset package figure files disagree with the render plan', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(assetPackage.figures.length > 0, 'fixture must include packaged figures');
  assetPackage.figures[0].displayPath = 'assets/fig-001/wrong-figure.svg';
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.assetPackage = sha256File(assetPackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /asset-package\.json figures.*render-plan\.json templateData\.images/);
});

test('validateDeliveryPackage fails when asset package source image files disagree with the render plan', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));

  assert.ok(assetPackage.images.length > 0, 'fixture must include packaged source images');
  assetPackage.images[0].displayPath = 'assets/img-001/wrong-image.png';
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  deliveryManifest.fileSha256.assetPackage = sha256File(assetPackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /asset-package\.json images.*render-plan\.json templateData\.images/);
});

test('validateDeliveryPackage preserves recorded WPS visual acceptance evidence', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  recordWpsVisualAcceptance({
    deliveryDir,
    status: 'passed',
    reviewedAt: '2026-07-05T10:00:00.000Z',
    reviewedBy: 'user',
    note: '目录、图片和表格已检查。',
  });

  const report = validateDeliveryPackage({ deliveryDir });
  const wpsVisual = report.checks.find((check) => check.id === 'wps_visual');

  assert.equal(report.status, 'passed');
  assert.equal(wpsVisual?.status, 'passed');
  assert.equal(wpsVisual?.reviewedAt, '2026-07-05T10:00:00.000Z');
  assert.equal(wpsVisual?.reviewedBy, 'user');
  assert.equal(wpsVisual?.documentSha256, sha256File(path.join(deliveryDir, 'document.docx')));
  assert.match(wpsVisual?.message || '', /目录、图片和表格/);
  assert.deepEqual(report.warnings, []);
});

test('validateDeliveryPackage fails when recorded WPS visual acceptance belongs to a different document', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  recordWpsVisualAcceptance({
    deliveryDir,
    status: 'passed',
    reviewedAt: '2026-07-05T10:00:00.000Z',
    reviewedBy: 'user',
    note: 'WPS/Word visual inspection passed.',
  });
  fs.appendFileSync(path.join(deliveryDir, 'document.docx'), 'changed after review');

  const report = validateDeliveryPackage({ deliveryDir });
  const wpsVisual = report.checks.find((check) => check.id === 'wps_visual');

  assert.equal(report.status, 'failed');
  assert.equal(wpsVisual?.status, 'failed');
  assert.match(wpsVisual?.message || '', /document\.docx.*changed/i);
});

test('validateDeliveryPackage fails when the original source copy is missing', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  fs.rmSync(path.join(deliveryDir, 'source', 'original'), { recursive: true, force: true });
  const report = validateDeliveryPackage({ deliveryDir });
  const sourceCheck = report.checks.find((check) => check.id === 'source_original');

  assert.equal(report.status, 'failed');
  assert.equal(sourceCheck?.status, 'failed');
  assert.match(sourceCheck?.message || '', /original source/i);
});

test('validateDeliveryPackage fails when the original source copy hash differs from sourceRef', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  const sourceCopy = path.join(deliveryDir, 'source', 'original', 'source.md');
  fs.writeFileSync(sourceCopy, '# Tampered source\n', 'utf8');
  const report = validateDeliveryPackage({ deliveryDir });
  const sourceCheck = report.checks.find((check) => check.id === 'source_original');

  assert.equal(report.status, 'failed');
  assert.equal(sourceCheck?.status, 'failed');
  assert.match(sourceCheck?.message || '', /hash/i);
});

test('validateDeliveryPackage fails when a required delivery file is missing', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  fs.rmSync(path.join(deliveryDir, 'render-plan.json'));
  const report = validateDeliveryPackage({ deliveryDir });

  assert.equal(report.status, 'failed');
  assert.ok(report.failures.some((item) => item.includes('render-plan.json')));
});

test('validateDeliveryPackage fails when delivery package manifest is not traceable', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  fs.writeFileSync(
    path.join(deliveryDir, 'delivery-package.json'),
    JSON.stringify({ schemaVersion: 'docx-engine-v2/delivery-package', files: {} }, null, 2),
    'utf8'
  );

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /delivery-package\.json|DeliveryPackage/);
});

test('validateDeliveryPackage fails when delivery package manifest points at missing files', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const manifestPath = path.join(deliveryDir, 'delivery-package.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  manifest.files.document = 'missing-document.docx';
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const filesCheck = report.checks.find((check) => check.id === 'delivery_files');

  assert.equal(report.status, 'failed');
  assert.equal(filesCheck?.status, 'failed');
  assert.match(filesCheck?.message || '', /files\.document|missing-document\.docx/);
});

test('validateDeliveryPackage fails when render plan images do not point inside assets', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const renderPlanPath = path.join(deliveryDir, 'render-plan.json');
  const renderPlan = JSON.parse(fs.readFileSync(renderPlanPath, 'utf8'));
  renderPlan.templateData.images[0].path = 'document.docx';
  fs.writeFileSync(renderPlanPath, `${JSON.stringify(renderPlan, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const imageCoverage = report.checks.find((check) => check.id === 'image_coverage');

  assert.equal(report.status, 'failed');
  assert.equal(imageCoverage?.status, 'failed');
  assert.match(imageCoverage?.message || '', /assets/);
});

test('validateDeliveryPackage fails when a render plan image asset is modified', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const renderPlan = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'render-plan.json'), 'utf8'));
  const imagePath = renderPlan.templateData.images.find((image) => image.path.endsWith('.png'))?.path;
  assert.ok(imagePath, 'fixture must include a packaged PNG image');
  fs.writeFileSync(path.join(deliveryDir, imagePath), Buffer.from('tampered image asset'));

  const report = validateDeliveryPackage({ deliveryDir });
  const imageCoverage = report.checks.find((check) => check.id === 'image_coverage');

  assert.equal(report.status, 'failed');
  assert.equal(imageCoverage?.status, 'failed');
  assert.match(imageCoverage?.message || '', /sha256|changed|modified/i);
});

test('validateDeliveryPackage fails when an editable figure source changes without rerendering', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const assetPackage = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'asset-package.json'), 'utf8'));
  const sourcePath = assetPackage.figures.find((figure) => figure.editable?.format === 'mermaid')?.editable?.sourcePath;
  assert.equal(sourcePath, 'assets/fig-001/source.mmd');

  fs.appendFileSync(path.join(deliveryDir, sourcePath), '\n  C[Changed after delivery]\n');
  const report = validateDeliveryPackage({ deliveryDir });
  const imageCoverage = report.checks.find((check) => check.id === 'image_coverage');

  assert.equal(report.status, 'failed');
  assert.equal(imageCoverage?.status, 'failed');
  assert.match(imageCoverage?.message || '', /editable source|source\.mmd|sha256/i);
});

test('validateDeliveryPackage fails when DOCX embedded media no longer matches the delivery image asset', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const renderPlanPath = path.join(deliveryDir, 'render-plan.json');
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const deliveryManifestPath = path.join(deliveryDir, 'delivery-package.json');
  const renderPlan = JSON.parse(fs.readFileSync(renderPlanPath, 'utf8'));
  const assetPackage = JSON.parse(fs.readFileSync(assetPackagePath, 'utf8'));
  const image = renderPlan.templateData.images.find((item) => item.figureId === 'fig-001');
  assert.equal(image?.path, 'assets/fig-001/figure.svg');

  fs.writeFileSync(
    path.join(deliveryDir, image.path),
    '<svg xmlns="http://www.w3.org/2000/svg"><text>RERENDERED_BUT_NOT_WRITTEN_TO_DOCX</text></svg>\n',
    'utf8'
  );
  const updatedImageHash = sha256File(path.join(deliveryDir, image.path));
  image.sha256 = updatedImageHash;
  assetPackage.figures[0].sha256 = updatedImageHash;
  fs.writeFileSync(renderPlanPath, `${JSON.stringify(renderPlan, null, 2)}\n`, 'utf8');
  fs.writeFileSync(assetPackagePath, `${JSON.stringify(assetPackage, null, 2)}\n`, 'utf8');
  const deliveryManifest = JSON.parse(fs.readFileSync(deliveryManifestPath, 'utf8'));
  deliveryManifest.fileSha256.renderPlan = sha256File(renderPlanPath);
  deliveryManifest.fileSha256.assetPackage = sha256File(assetPackagePath);
  fs.writeFileSync(deliveryManifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const imageCoverage = report.checks.find((check) => check.id === 'image_coverage');

  assert.equal(report.status, 'failed');
  assert.equal(imageCoverage?.status, 'failed');
  assert.match(imageCoverage?.message || '', /DOCX embedded media|fig-001|sha256/i);
});

test('validateDeliveryPackage fails when delivery package manifest maps a role to the wrong path', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const manifestPath = path.join(deliveryDir, 'delivery-package.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  manifest.files.document = 'source.md';
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const filesCheck = report.checks.find((check) => check.id === 'delivery_files');

  assert.equal(report.status, 'failed');
  assert.equal(filesCheck?.status, 'failed');
  assert.match(filesCheck?.message || '', /files\.document.*document\.docx/);
});

test('validateDeliveryPackage fails when job manifest is not traceable', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  fs.writeFileSync(
    path.join(deliveryDir, 'job.manifest.json'),
    JSON.stringify({ jobId: 'missing-required-traceability-fields' }, null, 2),
    'utf8'
  );

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /job\.manifest\.json|DocumentJob/);
});

test('validateDeliveryPackage fails when template manifest loses quality gate metadata', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);

  fs.writeFileSync(
    path.join(deliveryDir, 'template.manifest.json'),
    JSON.stringify({ id: 'general-proposal', name: '通用方案模板' }, null, 2),
    'utf8'
  );

  const report = validateDeliveryPackage({ deliveryDir });
  const schemaCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(schemaCheck?.status, 'failed');
  assert.match(schemaCheck?.message || '', /template\.manifest\.json|TemplateManifest/);
});

test('validateDeliveryPackage fails when package manifests disagree on template id', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const templateManifestPath = path.join(deliveryDir, 'template.manifest.json');
  const templateManifest = JSON.parse(fs.readFileSync(templateManifestPath, 'utf8'));
  templateManifest.id = 'meeting-minutes';
  templateManifest.name = '会议纪要模板';
  fs.writeFileSync(templateManifestPath, `${JSON.stringify(templateManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const consistencyCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(consistencyCheck?.status, 'failed');
  assert.match(consistencyCheck?.message || '', /template id|job\.manifest|render-plan|template\.manifest/i);
});

test('validateDeliveryPackage fails when job and render plan disagree on job id', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const renderPlanPath = path.join(deliveryDir, 'render-plan.json');
  const renderPlan = JSON.parse(fs.readFileSync(renderPlanPath, 'utf8'));
  renderPlan.jobId = 'job-other-render-plan';
  fs.writeFileSync(renderPlanPath, `${JSON.stringify(renderPlan, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const consistencyCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(consistencyCheck?.status, 'failed');
  assert.match(consistencyCheck?.message || '', /job id|job\.manifest|render-plan/i);
});

test('validateDeliveryPackage fails when delivered job workspace disagrees with delivery manifest', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const jobManifestPath = path.join(deliveryDir, 'job.manifest.json');
  const jobManifest = JSON.parse(fs.readFileSync(jobManifestPath, 'utf8'));
  jobManifest.status = 'delivered';
  jobManifest.deliveredAt = '2026-07-05T10:00:00.000Z';
  jobManifest.workspace = path.join(path.dirname(deliveryDir), 'stale-build-workspace');
  fs.writeFileSync(jobManifestPath, `${JSON.stringify(jobManifest, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const consistencyCheck = report.checks.find((check) => check.id === 'schema');

  assert.equal(report.status, 'failed');
  assert.equal(consistencyCheck?.status, 'failed');
  assert.match(consistencyCheck?.message || '', /workspace|delivery-package|job\.manifest/i);
});

test('validateDeliveryPackage requires figure ids to be bound to DOCX image metadata', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await removeFigureIdFromDocPr(path.join(deliveryDir, 'document.docx'), 'fig-002');

  const report = validateDeliveryPackage({ deliveryDir });
  const metadataCheck = report.checks.find((check) => check.id === 'figure_id_metadata');

  assert.equal(report.status, 'failed');
  assert.equal(metadataCheck?.status, 'failed');
  assert.match(metadataCheck?.message || '', /fig-002/);
});

test('validateDeliveryPackage fails when DOCX figure section metadata drifts from render plan', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const renderPlan = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'render-plan.json'), 'utf8'));
  const plannedImage = renderPlan.templateData.images.find((image) => image.figureId === 'fig-001');
  assert.equal(plannedImage?.metadata?.sectionId, 'sec-001');

  await rewriteDocPrForFigure(path.join(deliveryDir, 'document.docx'), 'fig-001', (tag) =>
    tag.replace(/\bsectionId=sec-001\b/g, 'sectionId=sec-tampered')
  );
  refreshDeliveryDocumentHash(deliveryDir);

  const report = validateDeliveryPackage({ deliveryDir });
  const metadataCheck = report.checks.find((check) => check.id === 'figure_id_metadata');

  assert.equal(report.status, 'failed');
  assert.equal(metadataCheck?.status, 'failed');
  assert.match(metadataCheck?.message || '', /fig-001|sectionId|render-plan/i);
});

test('validateDeliveryPackage fails when a DOCX figure appears before its render-plan section', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await moveFigureDrawingBeforeBody(path.join(deliveryDir, 'document.docx'), 'fig-001');
  refreshDeliveryDocumentHash(deliveryDir);

  const report = validateDeliveryPackage({ deliveryDir });
  const placementCheck = report.checks.find((check) => check.id === 'figure_placement');

  assert.equal(report.status, 'failed');
  assert.equal(placementCheck?.status, 'failed');
  assert.match(placementCheck?.message || '', /fig-001|section|placement/i);
});

test('validateDeliveryPackage fails when a DOCX figure caption is missing', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await removeFigureCaptionForFigure(path.join(deliveryDir, 'document.docx'), 'fig-002');
  refreshDeliveryDocumentHash(deliveryDir);

  const report = validateDeliveryPackage({ deliveryDir });
  const captionCheck = report.checks.find((check) => check.id === 'figure_caption');

  assert.equal(report.status, 'failed');
  assert.equal(captionCheck?.status, 'failed');
  assert.match(captionCheck?.message || '', /fig-002|caption|SEQ|figureId/i);
});

test('validateDeliveryPackage fails when a DOCX table appears before its render-plan section', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const renderPlan = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'render-plan.json'), 'utf8'));
  const table = renderPlan.tables.find((item) => item.tableId === 'tbl-001');
  assert.ok(table?.title, 'fixture must include table title for tbl-001');
  await moveTableBlockBeforeBody(path.join(deliveryDir, 'document.docx'), 'tbl-001', table.title);
  refreshDeliveryDocumentHash(deliveryDir);

  const report = validateDeliveryPackage({ deliveryDir });
  const placementCheck = report.checks.find((check) => check.id === 'table_placement');

  assert.equal(report.status, 'failed');
  assert.equal(placementCheck?.status, 'failed');
  assert.match(placementCheck?.message || '', /tbl-001|section|placement/i);
});

test('validateDeliveryPackage fails when a DOCX table caption is missing', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await removeTableCaptionForTable(path.join(deliveryDir, 'document.docx'), 'tbl-001');
  refreshDeliveryDocumentHash(deliveryDir);

  const report = validateDeliveryPackage({ deliveryDir });
  const captionCheck = report.checks.find((check) => check.id === 'table_caption');

  assert.equal(report.status, 'failed');
  assert.equal(captionCheck?.status, 'failed');
  assert.match(captionCheck?.message || '', /tbl-001|caption|SEQ|tableId/i);
});

test('validateDeliveryPackage fails when DOCX contains an unbound table caption', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await injectUnboundTableCaption(path.join(deliveryDir, 'document.docx'));
  refreshDeliveryDocumentHash(deliveryDir);

  const report = validateDeliveryPackage({ deliveryDir });
  const captionCheck = report.checks.find((check) => check.id === 'table_caption');

  assert.equal(report.status, 'failed');
  assert.equal(captionCheck?.status, 'failed');
  assert.match(captionCheck?.message || '', /unbound|SEQ 表|table caption/i);
});

test('validateDeliveryPackage fails when DOCX rich content no longer follows source block order', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await moveFigureBlockBeforeSourceAnchor(path.join(deliveryDir, 'document.docx'), 'fig-001', 'Architecture');
  refreshDeliveryDocumentHash(deliveryDir);

  const report = validateDeliveryPackage({ deliveryDir });
  const orderCheck = report.checks.find((check) => check.id === 'block_order');

  assert.equal(report.status, 'failed');
  assert.equal(orderCheck?.status, 'failed');
  assert.match(orderCheck?.message || '', /fig-001|block order|afterBlockId|source/i);
});

test('postprocessDocx keeps rich blocks before the next repeated section title', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t, [
    '# Duplicate sections',
    '',
    '## Overview',
    '',
    'First overview intro.',
    '',
    '| Item | Status |',
    '| --- | --- |',
    '| Render plan | Ready |',
    '',
    '```mermaid',
    'flowchart LR',
    '  A[Source] --> B[Render plan]',
    '```',
    '',
    '![Architecture](architecture.png)',
    '',
    '## Overview',
    '',
    'Second overview intro.',
    '',
  ]);
  const documentXml = await readDocumentXml(path.join(deliveryDir, 'document.docx'));
  const paragraphs = paragraphRanges(documentXml);
  const firstIntro = paragraphs.find((paragraph) => paragraph.text.trim() === 'First overview intro.');
  const secondIntroIndex = paragraphs.findIndex((paragraph) => paragraph.text.trim() === 'Second overview intro.');
  const nextRepeatedTitle = paragraphs
    .slice((firstIntro?.paragraphIndex || 0) + 1, secondIntroIndex)
    .find((paragraph) => paragraph.text.trim() === 'Overview');

  assert.ok(firstIntro, 'fixture must include first section body text');
  assert.ok(nextRepeatedTitle, 'fixture must include the next repeated section title');

  const tablePosition = documentXml.indexOf('tableId=tbl-001');
  const mermaidFigurePosition = documentXml.indexOf('figureId=fig-001');
  const sourceImagePosition = documentXml.indexOf('figureId=fig-002');

  assert.ok(tablePosition > firstIntro.end, 'table should follow its source paragraph');
  assert.ok(tablePosition < nextRepeatedTitle.start, 'table should stay before the next repeated section');
  assert.ok(mermaidFigurePosition < nextRepeatedTitle.start, 'mermaid figure should stay before the next repeated section');
  assert.ok(sourceImagePosition < nextRepeatedTitle.start, 'source image should stay before the next repeated section');
});

test('validateDeliveryPackage fails when template data markers remain in DOCX', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await injectTemplateMarker(path.join(deliveryDir, 'document.docx'));

  const report = validateDeliveryPackage({ deliveryDir });
  const markerCheck = report.checks.find((check) => check.id === 'template_markers');

  assert.equal(report.status, 'failed');
  assert.equal(markerCheck?.status, 'failed');
  assert.match(markerCheck?.message || '', /markers remain/);
});

async function removeFigureIdFromDocPr(docxPath, figureId) {
  await rewriteDocPrForFigure(docxPath, figureId, (tag) =>
    tag.replace(new RegExp(`\\s?figureId=${figureId}`, 'g'), '')
  );
}

async function rewriteDocPrForFigure(docxPath, figureId, rewriteTag) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  const updatedXml = documentXml.replace(/<wp:docPr\b[^>]*>/g, (tag) => {
    if (!new RegExp(`\\bfigureId=${figureId}\\b`).test(tag)) {
      return tag;
    }
    return rewriteTag(tag);
  });
  entries.set('word/document.xml', Buffer.from(updatedXml, 'utf8'));
  await writeZipEntries(entries, docxPath);
}

async function moveFigureDrawingBeforeBody(docxPath, figureId) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  const drawing = findDrawingForFigure(documentXml, figureId);
  assert.ok(drawing, `fixture must include drawing for ${figureId}`);
  const withoutDrawing = documentXml.replace(drawing, '');
  entries.set(
    'word/document.xml',
    Buffer.from(withoutDrawing.replace('<w:body>', `<w:body>${drawing}`), 'utf8')
  );
  await writeZipEntries(entries, docxPath);
}

async function moveFigureBlockBeforeSourceAnchor(docxPath, figureId, sectionTitle) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  const figureBlock = findFigureBlock(documentXml, figureId);
  assert.ok(figureBlock, `fixture must include figure block for ${figureId}`);
  const withoutFigure = documentXml.replace(figureBlock, '');
  const insertionIndex = sectionAnchorEnd(withoutFigure, sectionTitle);
  assert.ok(insertionIndex > 0, `fixture must include section anchor ${sectionTitle}`);
  entries.set(
    'word/document.xml',
    Buffer.from(
      `${withoutFigure.slice(0, insertionIndex)}${figureBlock}${withoutFigure.slice(insertionIndex)}`,
      'utf8'
    )
  );
  await writeZipEntries(entries, docxPath);
}

async function removeFigureCaptionForFigure(docxPath, figureId) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  let removed = false;
  const updatedXml = documentXml.replace(/<w:p\b[\s\S]*?<\/w:p>/g, (paragraph) => {
    if (
      !removed &&
      /\bfigureCaption\b/.test(paragraph) &&
      new RegExp(`\\bfigureId=${figureId}\\b`).test(paragraph)
    ) {
      removed = true;
      return '';
    }
    return paragraph;
  });
  entries.set('word/document.xml', Buffer.from(updatedXml, 'utf8'));
  await writeZipEntries(entries, docxPath);
}

async function removeTableCaptionForTable(docxPath, tableId) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  let removed = false;
  const updatedXml = documentXml.replace(/<w:p\b[\s\S]*?<\/w:p>/g, (paragraph) => {
    if (
      !removed &&
      /\btableCaption\b/.test(paragraph) &&
      new RegExp(`\\btableId=${tableId}\\b`).test(paragraph)
    ) {
      removed = true;
      return '';
    }
    return paragraph;
  });
  entries.set('word/document.xml', Buffer.from(updatedXml, 'utf8'));
  await writeZipEntries(entries, docxPath);
}

async function injectUnboundTableCaption(docxPath) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  const caption = [
    '<w:p>',
    '<w:r><w:t xml:space="preserve">表 </w:t></w:r>',
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>',
    '<w:r><w:instrText xml:space="preserve"> SEQ 表 \\* ARABIC </w:instrText></w:r>',
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>',
    '<w:r><w:t>99</w:t></w:r>',
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
    '<w:r><w:t xml:space="preserve"> 未绑定表题</w:t></w:r>',
    '</w:p>',
  ].join('');
  entries.set(
    'word/document.xml',
    Buffer.from(documentXml.replace('</w:body>', `${caption}</w:body>`), 'utf8')
  );
  await writeZipEntries(entries, docxPath);
}

function findDrawingForFigure(documentXml, figureId) {
  const figurePattern = new RegExp(`\\bfigureId=${figureId}\\b`);
  for (const match of String(documentXml || '').matchAll(/<w:drawing\b[\s\S]*?<\/w:drawing>/g)) {
    if (figurePattern.test(match[0])) {
      return match[0];
    }
  }
  return '';
}

function findFigureBlock(documentXml, figureId) {
  const drawing = findDrawingForFigure(documentXml, figureId);
  if (!drawing) {
    return '';
  }
  const paragraphs = [...String(documentXml || '').matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)];
  const drawingParagraphIndex = paragraphs.findIndex((paragraph) => paragraph[0].includes(drawing));
  if (drawingParagraphIndex < 0) {
    return '';
  }
  const drawingParagraph = paragraphs[drawingParagraphIndex][0];
  const captionParagraph = paragraphs[drawingParagraphIndex + 1]?.[0] || '';
  if (!new RegExp(`\\bfigureId=${figureId}\\b`).test(captionParagraph)) {
    return drawingParagraph;
  }
  return drawingParagraph + captionParagraph;
}

function sectionAnchorEnd(documentXml, sectionTitle) {
  const title = String(sectionTitle || '').trim();
  const matches = [...String(documentXml || '').matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)]
    .filter((match) => paragraphText(match[0]).trim() === title);
  const anchor = matches[matches.length - 1];
  return anchor ? anchor.index + anchor[0].length : -1;
}

async function moveTableBlockBeforeBody(docxPath, tableId, tableTitle) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  const tableBlock = findTableBlock(documentXml, tableId, tableTitle);
  assert.ok(tableBlock, `fixture must include table block for ${tableId}`);
  const withoutTableBlock = documentXml.replace(tableBlock, '');
  entries.set(
    'word/document.xml',
    Buffer.from(withoutTableBlock.replace('<w:body>', `<w:body>${tableBlock}`), 'utf8')
  );
  await writeZipEntries(entries, docxPath);
}

function findTableBlock(documentXml, tableId, tableTitle) {
  const markerPattern = new RegExp(`docx-engine-v2 tableId=${tableId}\\b`);
  const title = String(tableTitle || '').trim();
  const paragraphs = [...String(documentXml || '').matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)];
  for (const paragraph of paragraphs) {
    const paragraphText = paragraph[0].replace(/<[^>]+>/g, '');
    if (!markerPattern.test(paragraph[0]) && (!title || !paragraphText.includes(title))) {
      continue;
    }
    const tableMatch = String(documentXml || '').slice(paragraph.index + paragraph[0].length).match(/^[\s\S]*?<w:tbl\b[\s\S]*?<\/w:tbl>/);
    if (!tableMatch) {
      return paragraph[0];
    }
    return paragraph[0] + tableMatch[0];
  }
  return '';
}

function paragraphText(paragraphXml) {
  return [...String(paragraphXml || '').matchAll(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g)]
    .map((match) => match[1]
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&apos;/g, "'")
      .replace(/&amp;/g, '&'))
    .join('');
}

async function readDocumentXml(docxPath) {
  const entries = await readZipEntries(docxPath);
  return entries.get('word/document.xml')?.toString('utf8') || '';
}

function paragraphRanges(documentXml) {
  const ranges = [];
  let paragraphIndex = 0;
  for (const match of String(documentXml || '').matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)) {
    ranges.push({
      paragraphIndex,
      start: match.index,
      end: match.index + match[0].length,
      text: paragraphText(match[0]),
    });
    paragraphIndex += 1;
  }
  return ranges;
}

function refreshDeliveryDocumentHash(deliveryDir) {
  const documentPath = path.join(deliveryDir, 'document.docx');
  const manifestPath = path.join(deliveryDir, 'delivery-package.json');
  const deliveryManifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const documentHash = sha256File(documentPath);
  deliveryManifest.documentSha256 = documentHash;
  deliveryManifest.fileSha256.document = documentHash;
  fs.writeFileSync(manifestPath, `${JSON.stringify(deliveryManifest, null, 2)}\n`, 'utf8');
}

async function injectTemplateMarker(docxPath) {
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  entries.set(
    'word/document.xml',
    Buffer.from(documentXml.replace('</w:body>', '<w:p><w:r><w:t>{d.cover.title}</w:t></w:r></w:p></w:body>'), 'utf8')
  );
  await writeZipEntries(entries, docxPath);
}

function readZipEntries(docxPath) {
  return new Promise((resolve, reject) => {
    yauzl.open(docxPath, { lazyEntries: true }, (openError, zipfile) => {
      if (openError) {
        reject(openError);
        return;
      }

      const entries = new Map();
      let settled = false;

      const fail = (error) => {
        if (settled) {
          return;
        }
        settled = true;
        try {
          zipfile.close();
        } catch (_closeError) {
          // Preserve the original error.
        }
        reject(error);
      };

      const finish = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(entries);
      };

      zipfile.on('entry', (entry) => {
        if (settled) {
          return;
        }
        if (entry.fileName.endsWith('/')) {
          zipfile.readEntry();
          return;
        }

        zipfile.openReadStream(entry, (streamError, readStream) => {
          if (streamError) {
            fail(streamError);
            return;
          }

          const chunks = [];
          readStream.on('data', (chunk) => chunks.push(chunk));
          readStream.on('error', fail);
          readStream.on('end', () => {
            if (settled) {
              return;
            }
            entries.set(entry.fileName, Buffer.concat(chunks));
            zipfile.readEntry();
          });
        });
      });
      zipfile.on('error', fail);
      zipfile.on('end', finish);
      zipfile.readEntry();
    });
  });
}

async function writeZipEntries(entries, docxPath) {
  const tempPath = `${docxPath}.tmp-${process.pid}`;
  const zip = new yazl.ZipFile();
  const output = fs.createWriteStream(tempPath);
  zip.outputStream.pipe(output);

  for (const [entryName, entryBuffer] of entries) {
    zip.addBuffer(entryBuffer, entryName);
  }

  zip.end();
  await once(output, 'close');
  fs.renameSync(tempPath, docxPath);
}
