const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
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
  assert.deepEqual(
    report.checks.map((check) => check.id),
    [
      'schema',
      'source_original',
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

test('validateDeliveryPackage preserves recorded WPS visual acceptance evidence', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  const reportPath = path.join(deliveryDir, 'quality-report.json');
  const qualityReport = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
  qualityReport.status = 'passed';
  qualityReport.checks = qualityReport.checks.map((check) =>
    check.id === 'wps_visual'
      ? {
          id: 'wps_visual',
          status: 'passed',
          message: 'WPS/Word visual inspection passed. 目录、图片和表格已检查。',
          reviewedAt: '2026-07-05T10:00:00.000Z',
          reviewedBy: 'user',
        }
      : check
  );
  qualityReport.warnings = [];
  qualityReport.failures = [];
  fs.writeFileSync(reportPath, `${JSON.stringify(qualityReport, null, 2)}\n`, 'utf8');

  const report = validateDeliveryPackage({ deliveryDir });
  const wpsVisual = report.checks.find((check) => check.id === 'wps_visual');

  assert.equal(report.status, 'passed');
  assert.equal(wpsVisual?.status, 'passed');
  assert.equal(wpsVisual?.reviewedAt, '2026-07-05T10:00:00.000Z');
  assert.equal(wpsVisual?.reviewedBy, 'user');
  assert.match(wpsVisual?.message || '', /目录、图片和表格/);
  assert.deepEqual(report.warnings, []);
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

test('validateDeliveryPackage requires figure ids to be bound to DOCX image metadata', async (t) => {
  const { deliveryDir } = await makeDeliveryPackage(t);
  await removeFigureIdFromDocPr(path.join(deliveryDir, 'document.docx'), 'fig-002');

  const report = validateDeliveryPackage({ deliveryDir });
  const metadataCheck = report.checks.find((check) => check.id === 'figure_id_metadata');

  assert.equal(report.status, 'failed');
  assert.equal(metadataCheck?.status, 'failed');
  assert.match(metadataCheck?.message || '', /fig-002/);
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
  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  const updatedXml = documentXml.replace(/<wp:docPr\b[^>]*>/g, (tag) =>
    tag.replace(new RegExp(`\\s?figureId=${figureId}`, 'g'), '')
  );
  entries.set('word/document.xml', Buffer.from(updatedXml, 'utf8'));
  await writeZipEntries(entries, docxPath);
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
