const fs = require('node:fs');
const crypto = require('node:crypto');
const os = require('node:os');
const path = require('node:path');

const { buildRenderPlan } = require('../planning/build-render-plan');
const { buildDeliveryFileSha256, REPLAY_INPUT_FILE_ROLES } = require('../delivery/file-hashes');
const { postprocessDocx } = require('../rendering/postprocess-docx');
const { renderDocx } = require('../rendering/render-docx');
const { readZipEntriesFromBuffer, replayOriginalSourcePackage, sourceReplayFailures } = require('./source-replay');
const { getTemplatePackage } = require('../templates/registry');
const { validateDeliveryPackage } = require('../validation/validate-delivery-package');

async function replayDeliveryPackage({
  deliveryDir,
  engineRoot = path.resolve(__dirname, '../..'),
  outDir = '',
} = {}) {
  const absoluteDeliveryDir = deliveryDir ? path.resolve(deliveryDir) : '';
  const replayOutputDir = outDir ? path.resolve(outDir) : '';
  const checks = [];
  const failures = [];
  const warnings = [];
  let replayedSourcePackage = null;
  let replayedRenderPlan = null;
  let replayedDocumentPath = '';
  let inputFileSha256 = {};
  let replayOutputWritable = !replayOutputDir;

  const addCheck = (id, status, message = '', extra = {}) => {
    const check = { ...extra, id, status };
    if (message) {
      check.message = message;
    }
    checks.push(check);
    if (status === 'failed' && message) {
      failures.push(message);
    }
    if ((status === 'passed_with_warnings' || status === 'not_verified') && message) {
      warnings.push(message);
    }
  };

  if (!absoluteDeliveryDir || !fs.existsSync(absoluteDeliveryDir) || !fs.statSync(absoluteDeliveryDir).isDirectory()) {
    addCheck('delivery_files', 'failed', `deliveryDir is missing: ${absoluteDeliveryDir || ''}`);
    return buildReport({ absoluteDeliveryDir, checks, warnings, failures });
  }

  const qualityReport = validateDeliveryPackage({ deliveryDir: absoluteDeliveryDir });
  if (qualityReport.status === 'failed') {
    addCheck(
      'delivery_validation',
      'failed',
      `Delivery validation failed before replay: ${(qualityReport.failures || []).join('; ') || 'unknown failure'}`
    );
  } else if (qualityReport.status === 'passed_with_warnings') {
    addCheck('delivery_validation', 'passed_with_warnings', 'Delivery validation passed with warnings.');
    warnings.push(...(qualityReport.warnings || []));
  } else {
    addCheck('delivery_validation', 'passed');
  }

  let files;
  try {
    files = readDeliveryFiles(absoluteDeliveryDir);
    inputFileSha256 = buildDeliveryFileSha256({
      deliveryDir: absoluteDeliveryDir,
      files: files.deliveryPackage.files,
      roles: REPLAY_INPUT_FILE_ROLES,
    });
  } catch (error) {
    addCheck('delivery_files', 'failed', error.message);
    return finalizeReport({
      absoluteDeliveryDir,
      replayOutputDir,
      checks,
      warnings,
      failures,
      replayedSourcePackage,
      replayedRenderPlan,
      replayedDocumentPath,
      inputFileSha256,
      replayOutputWritable,
    });
  }

  const sourceReplayResult = replaySourcePackage({ deliveryDir: absoluteDeliveryDir, files });
  replayedSourcePackage = sourceReplayResult.sourcePackage;
  addReplayResultCheck({ addCheck, result: sourceReplayResult, passedId: 'source_replay' });

  let templatePackage = null;
  const templateReplayResult = replayTemplatePackage({ engineRoot, files });
  templatePackage = templateReplayResult.templatePackage;
  addReplayResultCheck({ addCheck, result: templateReplayResult, passedId: 'template_replay' });

  if (replayedSourcePackage && templatePackage) {
    try {
      replayedRenderPlan = buildRenderPlan({
        sourcePackage: replayedSourcePackage,
        templatePackage,
        assetPackage: files.assetPackage,
      });
      const renderPlanFailures = deterministicJsonFailures({
        label: 'render-plan.json',
        actual: files.renderPlan,
        expected: replayedRenderPlan,
      });
      if (renderPlanFailures.length > 0) {
        addCheck(
          'render_plan_replay',
          'failed',
          `render-plan.json does not match deterministic replay: ${renderPlanFailures.join(', ')}`
        );
      } else {
        addCheck('render_plan_replay', 'passed');
      }
    } catch (error) {
      addCheck('render_plan_replay', 'failed', `Render plan replay failed: ${error.message}`);
    }
  } else {
    addCheck('render_plan_replay', 'not_verified', 'Render plan replay was skipped because source or template replay failed.');
  }

  const renderPlanCheck = checks.find((check) => check.id === 'render_plan_replay');
  if (replayedRenderPlan && templatePackage && renderPlanCheck?.status === 'passed') {
    const documentReplayResult = await rebuildDocument({
      deliveryDir: absoluteDeliveryDir,
      replayOutputDir,
      templatePackage,
      renderPlan: replayedRenderPlan,
    });
    replayedDocumentPath = documentReplayResult.documentPath || '';
    replayOutputWritable = documentReplayResult.outputWritable !== false;
    addReplayResultCheck({ addCheck, result: documentReplayResult, passedId: 'document_rebuild' });
    if (documentReplayResult.status === 'passed') {
      const documentFailures = documentReplayResult.documentReplayFailures || [];
      if (documentFailures.length > 0) {
        addCheck(
          'document_replay',
          'failed',
          `document.docx does not match deterministic replay: ${documentFailures.join(', ')}`
        );
      } else {
        addCheck('document_replay', 'passed');
      }
    } else {
      addCheck('document_replay', 'not_verified', 'Document replay was skipped because document rebuild failed.');
    }
  } else {
    addCheck('document_rebuild', 'not_verified', 'Document rebuild was skipped because render plan replay did not pass.');
    addCheck('document_replay', 'not_verified', 'Document replay was skipped because render plan replay did not pass.');
  }

  return finalizeReport({
    absoluteDeliveryDir,
    replayOutputDir,
    checks,
    warnings,
    failures,
    replayedSourcePackage,
    replayedRenderPlan,
    replayedDocumentPath,
    inputFileSha256,
    replayOutputWritable,
  });
}

function replaySourcePackage({ deliveryDir, files }) {
  const sourcePackage = files.sourcePackage;
  const sourceRef = files.jobManifest?.sourceRef || sourcePackage.sourceRef || {};
  const sourceType = String(sourcePackage.sourceType || sourceRef.type || '').trim();
  const originalSourcePath = path.join(deliveryDir, expectedOriginalSourcePath(sourceRef));
  if (!fs.existsSync(originalSourcePath)) {
    return {
      status: 'failed',
      message: `Original source copy is missing for replay: ${path.relative(deliveryDir, originalSourcePath)}`,
    };
  }

  try {
    const replayedSourcePackage = replayOriginalSourcePackage({
      sourceType,
      sourcePath: sourcePackage.sourceRef?.path || sourceRef.path,
      sourceBuffer: fs.readFileSync(originalSourcePath),
    });
    const replayFailures = sourceReplayFailures({
      actual: sourcePackage,
      expected: replayedSourcePackage,
    });
    if (replayFailures.length > 0) {
      return {
        status: 'failed',
        message: `source-package.json does not match original source replay: ${replayFailures.join('; ')}`,
        sourcePackage: replayedSourcePackage,
      };
    }
    return { status: 'passed', sourcePackage: replayedSourcePackage };
  } catch (error) {
    return { status: 'failed', message: `Original source replay failed: ${error.message}` };
  }
}

function replayTemplatePackage({ engineRoot, files }) {
  const templateId = String(
    files.renderPlan?.templateId ||
    files.jobManifest?.templateId ||
    files.templateManifest?.id ||
    ''
  ).trim();
  if (!templateId) {
    return { status: 'failed', message: 'Template id is missing from delivery package.' };
  }

  try {
    const templatePackage = getTemplatePackage(templateId, { rootDir: path.resolve(engineRoot) });
    const templateFailures = deterministicJsonFailures({
      label: 'template.manifest.json',
      actual: files.templateManifest,
      expected: templatePackage.manifest,
    });
    if (templateFailures.length > 0) {
      return {
        status: 'failed',
        message: `template.manifest.json does not match engine registry replay: ${templateFailures.join(', ')}`,
        templatePackage,
      };
    }
    return { status: 'passed', templatePackage };
  } catch (error) {
    return { status: 'failed', message: `Template replay failed: ${error.message}` };
  }
}

async function rebuildDocument({ deliveryDir, replayOutputDir, templatePackage, renderPlan }) {
  let workspace = replayOutputDir;
  let cleanupWorkspace = false;
  let outputWritable = !replayOutputDir;
  try {
    if (workspace) {
      assertEmptyOutputDirectory(workspace);
      fs.mkdirSync(workspace, { recursive: true });
      outputWritable = true;
    } else {
      workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-replay-'));
      cleanupWorkspace = true;
    }

    const deliveryAssetsDir = path.join(deliveryDir, 'assets');
    const replayAssetsDir = path.join(workspace, 'assets');
    if (fs.existsSync(deliveryAssetsDir)) {
      copyDirectoryContents(deliveryAssetsDir, replayAssetsDir);
    }

    const renderedPath = path.join(workspace, 'rendered.replayed.docx');
    const documentPath = path.join(workspace, 'document.replayed.docx');
    await renderDocx({ templatePackage, renderPlan, outputPath: renderedPath });
    await postprocessDocx({ docxPath: renderedPath, renderPlan, outputPath: documentPath });
    const documentReplayFailures = replayedDocumentFailures({
      deliveredPath: path.join(deliveryDir, 'document.docx'),
      replayedPath: documentPath,
    });

    return {
      status: 'passed',
      documentPath: cleanupWorkspace ? '' : documentPath,
      outputWritable,
      extra: cleanupWorkspace ? {} : { replayedDocumentPath: documentPath },
      documentReplayFailures,
    };
  } catch (error) {
    return { status: 'failed', message: `Document rebuild failed: ${error.message}`, outputWritable };
  } finally {
    if (cleanupWorkspace && workspace) {
      fs.rmSync(workspace, { recursive: true, force: true });
    }
  }
}

function replayedDocumentFailures({ deliveredPath, replayedPath }) {
  const failures = [];
  if (!fs.existsSync(deliveredPath)) {
    return ['document.docx is missing'];
  }
  if (!fs.existsSync(replayedPath)) {
    return ['replayed document.docx is missing'];
  }

  let deliveredEntries;
  let replayedEntries;
  try {
    deliveredEntries = readZipEntriesFromBuffer(fs.readFileSync(deliveredPath));
  } catch (error) {
    return [`document.docx is not a readable DOCX zip: ${error.message}`];
  }
  try {
    replayedEntries = readZipEntriesFromBuffer(fs.readFileSync(replayedPath));
  } catch (error) {
    return [`replayed document.docx is not a readable DOCX zip: ${error.message}`];
  }

  const entryNames = new Set([...deliveredEntries.keys(), ...replayedEntries.keys()]);
  for (const entryName of [...entryNames].sort()) {
    const deliveredEntry = deliveredEntries.get(entryName);
    const replayedEntry = replayedEntries.get(entryName);
    if (!deliveredEntry) {
      failures.push(`${entryName} is missing from document.docx`);
      continue;
    }
    if (!replayedEntry) {
      failures.push(`${entryName} is extra in document.docx`);
      continue;
    }
    const deliveredHash = sha256Buffer(deliveredEntry);
    const replayedHash = sha256Buffer(replayedEntry);
    if (deliveredHash !== replayedHash) {
      failures.push(`${entryName} sha256 mismatch`);
    }
  }
  return failures;
}

function addReplayResultCheck({ addCheck, result, passedId }) {
  if (result.status === 'passed') {
    addCheck(passedId, 'passed', '', result.extra || {});
  } else {
    addCheck(passedId, result.status || 'failed', result.message || `${passedId} failed.`, result.extra || {});
  }
}

function readDeliveryFiles(deliveryDir) {
  return {
    deliveryPackage: readJson(path.join(deliveryDir, 'delivery-package.json')),
    sourcePackage: readJson(path.join(deliveryDir, 'source-package.json')),
    assetPackage: readJson(path.join(deliveryDir, 'asset-package.json')),
    jobManifest: readJson(path.join(deliveryDir, 'job.manifest.json')),
    templateManifest: readJson(path.join(deliveryDir, 'template.manifest.json')),
    renderPlan: readJson(path.join(deliveryDir, 'render-plan.json')),
  };
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    throw new Error(`${path.basename(filePath)} could not be read as JSON: ${error.message}`);
  }
}

function deterministicJsonFailures({ label, actual, expected }) {
  if (stableStringify(actual) === stableStringify(expected)) {
    return [];
  }

  const fields = topLevelDifferences(actual, expected);
  if (fields.length === 0) {
    return [`${label} differs from replay`];
  }
  return fields.map((field) => `${label}.${field}`);
}

function topLevelDifferences(actual, expected) {
  const fields = new Set([
    ...Object.keys(actual || {}),
    ...Object.keys(expected || {}),
  ]);
  return [...fields]
    .filter((field) => stableStringify(actual?.[field]) !== stableStringify(expected?.[field]))
    .sort();
}

function stableStringify(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value ?? null);
}

function finalizeReport({
  absoluteDeliveryDir,
  replayOutputDir,
  checks,
  warnings,
  failures,
  replayedSourcePackage,
  replayedRenderPlan,
  replayedDocumentPath,
  inputFileSha256 = {},
  replayOutputWritable,
}) {
  const report = buildReport({ absoluteDeliveryDir, checks, warnings, failures, replayedDocumentPath, inputFileSha256 });
  if (replayOutputDir && replayOutputWritable) {
    fs.mkdirSync(replayOutputDir, { recursive: true });
    if (replayedSourcePackage) {
      writeJson(path.join(replayOutputDir, 'source-package.replayed.json'), replayedSourcePackage);
    }
    if (replayedRenderPlan) {
      writeJson(path.join(replayOutputDir, 'render-plan.replayed.json'), replayedRenderPlan);
    }
    writeJson(path.join(replayOutputDir, 'replay-report.json'), report);
  }
  return report;
}

function buildReport({ absoluteDeliveryDir, checks, warnings, failures, replayedDocumentPath = '', inputFileSha256 = {} }) {
  const uniqueFailures = uniqueStrings(failures);
  const uniqueWarnings = uniqueStrings(warnings);
  let status = 'passed';
  if (uniqueFailures.length > 0 || checks.some((check) => check.status === 'failed')) {
    status = 'failed';
  } else if (
    uniqueWarnings.length > 0 ||
    checks.some((check) => check.status === 'passed_with_warnings' || check.status === 'not_verified')
  ) {
    status = 'passed_with_warnings';
  }

  return {
    schemaVersion: 'docx-engine-v2/replay-report',
    status,
    replayedAt: new Date().toISOString(),
    deliveryDir: absoluteDeliveryDir,
    inputFileSha256,
    replayedDocumentPath: replayedDocumentPath || undefined,
    checks,
    warnings: uniqueWarnings,
    failures: uniqueFailures,
  };
}

function assertEmptyOutputDirectory(outDir) {
  if (fs.existsSync(outDir) && fs.readdirSync(outDir).length > 0) {
    throw new Error(`输出目录非空: ${outDir}`);
  }
}

function copyDirectoryContents(sourceDir, targetDir) {
  fs.mkdirSync(targetDir, { recursive: true });
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);
    if (entry.isDirectory()) {
      copyDirectoryContents(sourcePath, targetPath);
    } else if (entry.isFile()) {
      fs.copyFileSync(sourcePath, targetPath);
    }
  }
}

function expectedOriginalSourcePath(sourceRef = {}) {
  return `source/original/${originalSourceFileName(sourceRef)}`;
}

function originalSourceFileName(sourceRef = {}) {
  const baseName = path.basename(String(sourceRef.path || '')).trim();
  if (baseName && baseName !== '.' && baseName !== '..') {
    return baseName;
  }
  const extensionByType = {
    markdown: '.md',
    text: '.txt',
    docx: '.docx',
  };
  return `source${extensionByType[sourceRef.type] || ''}`;
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function uniqueStrings(items) {
  return [...new Set((items || []).filter(Boolean))];
}

module.exports = { replayDeliveryPackage };
