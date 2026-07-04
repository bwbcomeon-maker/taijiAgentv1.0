const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const zlib = require('node:zlib');

const { validateDomainObject } = require('../domain/validate');
const { deliveryFileHashFailures } = require('../delivery/file-hashes');

const CHECK_IDS = [
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
];

const REQUIRED_ENTRIES = [
  'document.docx',
  'delivery-package.json',
  'source.md',
  'source/original',
  'assets',
  'asset-package.json',
  'job.manifest.json',
  'template.manifest.json',
  'render-plan.json',
  'quality-report.json',
  'README-图片调整说明.md',
];

const DELIVERY_MANIFEST_FIXED_FILES = {
  document: 'document.docx',
  source: 'source.md',
  assetsDir: 'assets',
  assetPackage: 'asset-package.json',
  jobManifest: 'job.manifest.json',
  templateManifest: 'template.manifest.json',
  renderPlan: 'render-plan.json',
  qualityReport: 'quality-report.json',
  imageInstructions: 'README-图片调整说明.md',
};

function validateDeliveryPackage({ deliveryDir, wpsVisualStatus = 'not_verified' } = {}) {
  const checksById = new Map();
  const failures = [];
  const warnings = [];
  const jsonFiles = {};
  let documentXml = '';

  const addCheck = (id, status, message = '', extra = {}) => {
    const check = { ...extra, id, status };
    if (message) {
      check.message = message;
    }
    checksById.set(id, check);
    if (status === 'failed' && message) {
      failures.push(message);
    }
    if ((status === 'passed_with_warnings' || status === 'not_verified') && message) {
      warnings.push(message);
    }
  };

  if (!deliveryDir || !fs.existsSync(deliveryDir) || !fs.statSync(deliveryDir).isDirectory()) {
    addCheck('delivery_files', 'failed', `deliveryDir is missing: ${deliveryDir || ''}`);
    for (const checkId of CHECK_IDS) {
      if (!checksById.has(checkId)) {
        addCheck(checkId, checkId === 'wps_visual' ? normalizeWpsStatus(wpsVisualStatus) : 'failed');
      }
    }
    return buildReport(checksById, warnings, failures);
  }

  const missingEntries = REQUIRED_ENTRIES.filter((entry) => !fs.existsSync(path.join(deliveryDir, entry)));
  if (missingEntries.length > 0) {
    addCheck(
      'delivery_files',
      'failed',
      `Missing delivery files: ${missingEntries.join(', ')}`
    );
  } else {
    addCheck('delivery_files', 'passed');
  }

  for (const [key, fileName] of [
    ['deliveryPackage', 'delivery-package.json'],
    ['assetPackage', 'asset-package.json'],
    ['jobManifest', 'job.manifest.json'],
    ['templateManifest', 'template.manifest.json'],
    ['renderPlan', 'render-plan.json'],
    ['qualityReport', 'quality-report.json'],
  ]) {
    const filePath = path.join(deliveryDir, fileName);
    if (!fs.existsSync(filePath)) {
      continue;
    }
    try {
      jsonFiles[key] = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    } catch (error) {
      failures.push(`${fileName} is not valid JSON: ${error.message}`);
    }
  }

  addSchemaCheck({ addCheck, jsonFiles });
  addDeliveryManifestFilesCheck({ addCheck, deliveryDir, deliveryPackage: jsonFiles.deliveryPackage });
  addSourceOriginalCheck({ addCheck, deliveryDir, jobManifest: jsonFiles.jobManifest });
  documentXml = addDocxZipCheck({ addCheck, deliveryDir });
  addTemplateMarkersCheck({ addCheck, documentXml });
  addImageCoverageCheck({
    addCheck,
    deliveryDir,
    documentXml,
    renderPlan: jsonFiles.renderPlan,
    assetPackage: jsonFiles.assetPackage,
  });
  addTableCoverageCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addTablePlacementCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addTableCaptionCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addBlockOrderCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addFigureIdMetadataCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addFigurePlacementCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addFigureCaptionCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });

  addWpsVisualCheck({
    addCheck,
    deliveryDir,
    qualityReport: jsonFiles.qualityReport,
    fallbackStatus: wpsVisualStatus,
  });

  return buildReport(checksById, warnings, failures);
}

function addSchemaCheck({ addCheck, jsonFiles }) {
  if (
    !jsonFiles.deliveryPackage ||
    !jsonFiles.assetPackage ||
    !jsonFiles.jobManifest ||
    !jsonFiles.templateManifest ||
    !jsonFiles.renderPlan ||
    !jsonFiles.qualityReport
  ) {
    addCheck(
      'schema',
      'failed',
      'delivery-package.json, asset-package.json, job.manifest.json, template.manifest.json, render-plan.json and quality-report.json are required for schema validation.'
    );
    return;
  }

  const deliveryPackageResult = validateDomainObject('DeliveryPackage', jsonFiles.deliveryPackage);
  const assetPackageResult = validateDomainObject('AssetPackage', jsonFiles.assetPackage);
  const jobManifestResult = validateDomainObject('DocumentJob', jsonFiles.jobManifest);
  const templateManifestResult = validateDomainObject('TemplateManifest', jsonFiles.templateManifest);
  const renderPlanResult = validateDomainObject('RenderPlan', jsonFiles.renderPlan);
  const qualityReportResult = validateDomainObject('ValidationReport', jsonFiles.qualityReport);
  if (
    !deliveryPackageResult.ok ||
    !assetPackageResult.ok ||
    !jobManifestResult.ok ||
    !templateManifestResult.ok ||
    !renderPlanResult.ok ||
    !qualityReportResult.ok
  ) {
    addCheck(
      'schema',
      'failed',
      `Delivery schema validation failed: ${JSON.stringify([
        ...tagValidationErrors('delivery-package.json', deliveryPackageResult.errors),
        ...tagValidationErrors('asset-package.json', assetPackageResult.errors),
        ...tagValidationErrors('job.manifest.json', jobManifestResult.errors),
        ...tagValidationErrors('template.manifest.json', templateManifestResult.errors),
        ...tagValidationErrors('render-plan.json', renderPlanResult.errors),
        ...tagValidationErrors('quality-report.json', qualityReportResult.errors),
      ])}`
    );
    return;
  }

  if (normalizeRelativePackagePath(jsonFiles.assetPackage.assetDir) !== 'assets') {
    addCheck(
      'schema',
      'failed',
      `Delivery asset package assetDir must be assets, got ${jsonFiles.assetPackage.assetDir || 'missing'}`
    );
    return;
  }

  const templateIds = {
    'job.manifest.json': String(jsonFiles.jobManifest.templateId || ''),
    'render-plan.json': String(jsonFiles.renderPlan.templateId || ''),
    'template.manifest.json': String(jsonFiles.templateManifest.id || ''),
  };
  const uniqueTemplateIds = [...new Set(Object.values(templateIds).filter(Boolean))];
  if (uniqueTemplateIds.length !== 1) {
    addCheck(
      'schema',
      'failed',
      `Delivery package template id mismatch: ${Object.entries(templateIds)
        .map(([fileName, templateId]) => `${fileName}=${templateId || 'missing'}`)
        .join(', ')}`
    );
    return;
  }

  const jobIds = {
    'job.manifest.json': String(jsonFiles.jobManifest.jobId || ''),
    'render-plan.json': String(jsonFiles.renderPlan.jobId || ''),
  };
  const uniqueJobIds = [...new Set(Object.values(jobIds).filter(Boolean))];
  if (uniqueJobIds.length !== 1) {
    addCheck(
      'schema',
      'failed',
      `Delivery package job id mismatch: ${Object.entries(jobIds)
        .map(([fileName, jobId]) => `${fileName}=${jobId || 'missing'}`)
        .join(', ')}`
    );
    return;
  }

  if (
    jsonFiles.jobManifest.status === 'delivered' &&
    normalizeComparablePath(jsonFiles.jobManifest.workspace) !==
      normalizeComparablePath(jsonFiles.deliveryPackage.deliveryDir)
  ) {
    addCheck(
      'schema',
      'failed',
      `Delivery package workspace mismatch: job.manifest.json workspace=${
        jsonFiles.jobManifest.workspace || 'missing'
      }, delivery-package.json deliveryDir=${jsonFiles.deliveryPackage.deliveryDir || 'missing'}`
    );
    return;
  }

  addCheck('schema', 'passed');
}

function addDeliveryManifestFilesCheck({ addCheck, deliveryDir, deliveryPackage }) {
  const files = deliveryPackage?.files;
  if (!files || typeof files !== 'object') {
    return;
  }

  const failures = [];
  for (const [field, expectedPath] of Object.entries(DELIVERY_MANIFEST_FIXED_FILES)) {
    if (files[field] !== expectedPath) {
      failures.push(
        `delivery-package.json files.${field} must be ${expectedPath}, got ${files[field] || ''}`
      );
    }
  }

  const originalSource = normalizeRelativePackagePath(files.originalSource);
  if (!originalSource || !originalSource.startsWith('source/original/')) {
    failures.push(
      `delivery-package.json files.originalSource must be inside source/original/, got ${files.originalSource || ''}`
    );
  }

  for (const [field, relativePath] of Object.entries(files)) {
    const normalizedPath = normalizeRelativePackagePath(relativePath);
    if (!normalizedPath) {
      failures.push(`delivery-package.json files.${field} must be a relative package path.`);
      continue;
    }
    if (!fs.existsSync(path.join(deliveryDir, normalizedPath))) {
      failures.push(`delivery-package.json files.${field} points to missing path: ${relativePath}`);
    }
  }

  const expectedDocumentHash = String(deliveryPackage.documentSha256 || '').trim();
  const documentPath = path.join(deliveryDir, normalizeRelativePackagePath(files.document) || 'document.docx');
  if (!expectedDocumentHash) {
    failures.push('delivery-package.json documentSha256 is required.');
  } else if (fs.existsSync(documentPath)) {
    const actualDocumentHash = sha256File(documentPath);
    if (actualDocumentHash !== expectedDocumentHash) {
      failures.push(
        `document.docx sha256 mismatch: expected ${expectedDocumentHash}, got ${actualDocumentHash}`
      );
    }
  }

  const expectedSourceHash = String(deliveryPackage.sourceSha256 || '').trim();
  const sourcePath = path.join(deliveryDir, normalizeRelativePackagePath(files.source) || 'source.md');
  if (!expectedSourceHash) {
    failures.push('delivery-package.json sourceSha256 is required.');
  } else if (fs.existsSync(sourcePath)) {
    const actualSourceHash = sha256File(sourcePath);
    if (actualSourceHash !== expectedSourceHash) {
      failures.push(
        `source.md sha256 mismatch: expected ${expectedSourceHash}, got ${actualSourceHash}`
      );
    }
  }

  failures.push(
    ...deliveryFileHashFailures({
      deliveryDir,
      files,
      fileSha256: deliveryPackage.fileSha256,
    })
  );

  if (failures.length > 0) {
    addCheck('delivery_files', 'failed', `Invalid delivery package file references: ${failures.join('; ')}`);
  }
}

function addWpsVisualCheck({ addCheck, deliveryDir, qualityReport, fallbackStatus }) {
  const recordedCheck = Array.isArray(qualityReport?.checks)
    ? qualityReport.checks.find((check) => check?.id === 'wps_visual')
    : null;
  const recordedStatus = normalizeOptionalWpsStatus(recordedCheck?.status);
  let status = recordedStatus || normalizeWpsStatus(fallbackStatus);
  let message = recordedCheck?.message ||
    (status === 'not_verified' ? 'WPS/Word visual inspection has not been performed.' : '');
  if (recordedStatus && recordedStatus !== 'not_verified') {
    const hashValidation = validateWpsVisualDocumentHash({ deliveryDir, recordedCheck });
    if (!hashValidation.ok) {
      status = 'failed';
      message = hashValidation.message;
    }
  }
  addCheck('wps_visual', status, message, wpsVisualEvidence(recordedCheck));
}

function validateWpsVisualDocumentHash({ deliveryDir, recordedCheck }) {
  const expectedHash = String(recordedCheck?.documentSha256 || '').trim();
  if (!expectedHash) {
    return {
      ok: false,
      message: 'WPS/Word visual acceptance is not bound to document.docx sha256.',
    };
  }
  const documentPath = path.join(deliveryDir, 'document.docx');
  if (!fs.existsSync(documentPath)) {
    return { ok: false, message: 'WPS/Word visual acceptance cannot be checked because document.docx is missing.' };
  }
  const actualHash = sha256File(documentPath);
  if (actualHash !== expectedHash) {
    return {
      ok: false,
      message: `WPS/Word visual acceptance document.docx changed since review: expected ${expectedHash}, got ${actualHash}.`,
    };
  }
  return { ok: true };
}

function normalizeOptionalWpsStatus(status) {
  const normalized = String(status || '').trim();
  return normalized ? normalizeWpsStatus(normalized) : '';
}

function wpsVisualEvidence(check) {
  if (!check || typeof check !== 'object') {
    return {};
  }
  const evidence = {};
  for (const key of ['reviewedAt', 'reviewedBy', 'documentSha256']) {
    if (check[key]) {
      evidence[key] = check[key];
    }
  }
  return evidence;
}

function normalizeRelativePackagePath(value) {
  if (typeof value !== 'string' || !value.trim() || path.isAbsolute(value)) {
    return '';
  }
  const normalized = path.normalize(value).replaceAll(path.sep, '/');
  if (normalized === '..' || normalized.startsWith('../')) {
    return '';
  }
  return normalized;
}

function normalizeComparablePath(value) {
  const normalized = String(value || '').trim();
  return normalized ? path.resolve(normalized) : '';
}

function tagValidationErrors(source, errors) {
  return (errors || []).map((error) => ({ source, ...error }));
}

function addSourceOriginalCheck({ addCheck, deliveryDir, jobManifest }) {
  const sourceRef = jobManifest?.sourceRef || {};
  const expectedHash = String(sourceRef.sha256 || '').trim();
  const originalSourcePath = path.join(deliveryDir, 'source', 'original', originalSourceFileName(sourceRef));
  if (!expectedHash) {
    addCheck('source_original', 'failed', 'job.manifest.json sourceRef.sha256 is required for original source validation.');
    return;
  }
  if (!fs.existsSync(originalSourcePath)) {
    addCheck('source_original', 'failed', `Original source copy is missing: ${path.relative(deliveryDir, originalSourcePath)}`);
    return;
  }
  const actualHash = sha256File(originalSourcePath);
  if (actualHash !== expectedHash) {
    addCheck(
      'source_original',
      'failed',
      `Original source hash mismatch: expected ${expectedHash}, got ${actualHash}`
    );
    return;
  }
  addCheck('source_original', 'passed');
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

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function addDocxZipCheck({ addCheck, deliveryDir }) {
  const documentPath = path.join(deliveryDir, 'document.docx');
  if (!fs.existsSync(documentPath)) {
    addCheck('docx_zip', 'failed', 'document.docx is missing.');
    return '';
  }

  try {
    const entries = readZipEntries(documentPath);
    const documentXml = entries.get('word/document.xml');
    if (!documentXml) {
      addCheck('docx_zip', 'failed', 'document.docx is missing word/document.xml.');
      return '';
    }
    addCheck('docx_zip', 'passed');
    return documentXml.toString('utf8');
  } catch (error) {
    addCheck('docx_zip', 'failed', `document.docx is not a readable DOCX zip: ${error.message}`);
    return '';
  }
}

function addTemplateMarkersCheck({ addCheck, documentXml }) {
  if (!documentXml) {
    addCheck('template_markers', 'failed', 'Cannot inspect template markers without word/document.xml.');
    return;
  }

  if (hasTemplateMarkers(documentXml)) {
    addCheck(
      'template_markers',
      'failed',
      'Template data markers remain in document.xml; DOCX template rendering did not complete.'
    );
    return;
  }

  addCheck('template_markers', 'passed');
}

function addImageCoverageCheck({ addCheck, deliveryDir, documentXml, renderPlan, assetPackage }) {
  if (!renderPlan) {
    addCheck('image_coverage', 'failed', 'render-plan.json is required for image coverage.');
    return;
  }

  const images = renderPlan.templateData?.images || [];
  const invalidImagePaths = images
    .map((image) => String(image?.path || '').trim())
    .filter(Boolean)
    .filter((imagePath) => {
      const normalizedPath = normalizeRelativePackagePath(imagePath);
      return !normalizedPath || !normalizedPath.startsWith('assets/');
    });
  if (invalidImagePaths.length > 0) {
    addCheck(
      'image_coverage',
      'failed',
      `Render plan image paths must be package-relative assets paths: ${invalidImagePaths.join(', ')}`
    );
    return;
  }
  const missingImages = images
    .map((image) => image.path)
    .filter(Boolean)
    .filter((imagePath) => !fs.existsSync(path.join(deliveryDir, normalizeRelativePackagePath(imagePath))));
  if (missingImages.length > 0) {
    addCheck('image_coverage', 'failed', `Missing delivery image assets: ${missingImages.join(', ')}`);
    return;
  }
  const missingHashes = images
    .filter((image) => !/^[a-f0-9]{64}$/.test(String(image?.sha256 || '')))
    .map((image) => image.path || image.figureId || 'unknown');
  if (missingHashes.length > 0) {
    addCheck('image_coverage', 'failed', `Render plan image sha256 is required: ${missingHashes.join(', ')}`);
    return;
  }
  const changedImages = images.filter((image) => {
    const imagePath = path.join(deliveryDir, normalizeRelativePackagePath(image.path));
    return fs.existsSync(imagePath) && sha256File(imagePath) !== image.sha256;
  });
  if (changedImages.length > 0) {
    addCheck(
      'image_coverage',
      'failed',
      `Delivery image asset sha256 changed: ${changedImages.map((image) => image.path).join(', ')}`
    );
    return;
  }
  const editableSourceFailures = editableFigureSourceFailures({ deliveryDir, assetPackage });
  if (editableSourceFailures.length > 0) {
    addCheck(
      'image_coverage',
      'failed',
      `Editable figure source sha256 mismatch: ${editableSourceFailures.join(', ')}`
    );
    return;
  }
  const embeddedMediaFailures = docxEmbeddedMediaFailures({ deliveryDir, documentXml, images });
  if (embeddedMediaFailures.length > 0) {
    addCheck(
      'image_coverage',
      'failed',
      `DOCX embedded media sha256 mismatch: ${embeddedMediaFailures.join(', ')}`
    );
    return;
  }

  if (hasTemplateMarkers(documentXml)) {
    addCheck(
      'image_coverage',
      'passed_with_warnings',
      'Image assets are present in the delivery package, but DOCX visual insertion is not fully verified while template markers remain.'
    );
    return;
  }

  addCheck('image_coverage', 'passed');
}

function editableFigureSourceFailures({ deliveryDir, assetPackage }) {
  const failures = [];
  for (const figure of assetPackage?.figures || []) {
    const sourcePath = normalizeRelativePackagePath(figure?.editable?.sourcePath);
    if (!sourcePath || !sourcePath.startsWith('assets/')) {
      continue;
    }
    const expectedHash = String(figure?.editable?.sourceSha256 || '').trim();
    if (!/^[a-f0-9]{64}$/.test(expectedHash)) {
      failures.push(`${sourcePath} missing editable source sha256`);
      continue;
    }
    const absoluteSourcePath = path.join(deliveryDir, sourcePath);
    if (!fs.existsSync(absoluteSourcePath)) {
      failures.push(`${sourcePath} missing editable source file`);
      continue;
    }
    const actualHash = sha256File(absoluteSourcePath);
    if (actualHash !== expectedHash) {
      failures.push(`${sourcePath} expected ${expectedHash}, got ${actualHash}`);
    }
  }
  return failures;
}

function docxEmbeddedMediaFailures({ deliveryDir, documentXml, images }) {
  const failures = [];
  if (!documentXml) {
    return failures;
  }

  let entries;
  try {
    entries = readZipEntries(path.join(deliveryDir, 'document.docx'));
  } catch (error) {
    return [`document.docx media cannot be inspected: ${error.message}`];
  }
  const relationshipsXml = entries.get('word/_rels/document.xml.rels')?.toString('utf8') || '';
  if (!relationshipsXml) {
    return ['word/_rels/document.xml.rels is missing'];
  }

  for (const image of images || []) {
    const figureId = String(image?.figureId || '').trim();
    const expectedHash = String(image?.sha256 || '').trim();
    if (!figureId || !/^[a-f0-9]{64}$/.test(expectedHash)) {
      continue;
    }

    const relationshipId = findFigureRelationshipId(documentXml, figureId);
    if (!relationshipId) {
      failures.push(`${figureId} missing DOCX image relationship`);
      continue;
    }

    const relationship = findRelationshipById(relationshipsXml, relationshipId);
    if (!relationship) {
      failures.push(`${figureId} missing DOCX relationship ${relationshipId}`);
      continue;
    }
    if (relationship.attrs.TargetMode === 'External' || !isImageRelationship(relationship.attrs.Type)) {
      failures.push(`${figureId} relationship ${relationshipId} is not an embedded image`);
      continue;
    }

    const entryName = relationshipTargetEntryName(relationship.attrs.Target);
    const media = entryName ? entries.get(entryName) : null;
    if (!media) {
      failures.push(`${figureId} missing DOCX media ${entryName || relationship.attrs.Target || ''}`);
      continue;
    }

    const actualHash = sha256Buffer(media);
    if (actualHash !== expectedHash) {
      failures.push(`${figureId} ${entryName} expected ${expectedHash}, got ${actualHash}`);
    }
  }
  return failures;
}

function findFigureRelationshipId(documentXml, figureId) {
  const figurePattern = new RegExp(`\\bfigureId=${escapeRegExp(figureId)}\\b`);
  for (const drawingMatch of String(documentXml || '').matchAll(/<w:drawing\b[\s\S]*?<\/w:drawing>/g)) {
    const drawingXml = drawingMatch[0];
    const docPrTags = drawingXml.match(/<wp:docPr\b[^>]*>/g) || [];
    if (!docPrTags.some((tag) => figurePattern.test(tag))) {
      continue;
    }
    const relationshipMatch = drawingXml.match(/\br:embed="([^"]+)"/);
    if (relationshipMatch) {
      return relationshipMatch[1];
    }
  }
  return '';
}

function findRelationshipById(relationshipsXml, relationshipId) {
  for (const match of String(relationshipsXml || '').matchAll(/<Relationship\b[^>]*\/?>/g)) {
    const raw = match[0];
    const attrs = parseXmlAttributes(raw);
    if (attrs.Id === relationshipId) {
      return { raw, attrs };
    }
  }
  return null;
}

function parseXmlAttributes(tag) {
  const attrs = {};
  for (const match of String(tag || '').matchAll(/\s([A-Za-z_:][\w:.-]*)="([^"]*)"/g)) {
    attrs[match[1]] = match[2];
  }
  return attrs;
}

function isImageRelationship(type) {
  return String(type || '').endsWith('/image');
}

function relationshipTargetEntryName(target) {
  const rawTarget = String(target || '').trim();
  if (!rawTarget || /^[a-z][a-z0-9+.-]*:/i.test(rawTarget)) {
    return '';
  }
  if (rawTarget.startsWith('/')) {
    return path.posix.normalize(rawTarget.replace(/^\/+/, ''));
  }
  const entryName = path.posix.normalize(path.posix.join('word', rawTarget.replace(/\\/g, '/')));
  return entryName.startsWith('../') ? '' : entryName;
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function addTableCoverageCheck({ addCheck, documentXml, renderPlan }) {
  if (!renderPlan) {
    addCheck('table_coverage', 'failed', 'render-plan.json is required for table coverage.');
    return;
  }

  const plannedTableIds = new Set((renderPlan.tables || []).map((table) => table.tableId));
  const templateTableIds = new Set((renderPlan.templateData?.tables || []).map((table) => table.tableId));
  const missingTableIds = [...plannedTableIds].filter((tableId) => !templateTableIds.has(tableId));
  if (missingTableIds.length > 0) {
    addCheck('table_coverage', 'failed', `Missing template table data: ${missingTableIds.join(', ')}`);
    return;
  }

  if (hasTemplateMarkers(documentXml)) {
    addCheck(
      'table_coverage',
      'passed_with_warnings',
      'Table data is present in render-plan.json, but DOCX table rendering is not fully verified while template markers remain.'
    );
    return;
  }

  addCheck('table_coverage', 'passed');
}

function addTablePlacementCheck({ addCheck, documentXml, renderPlan }) {
  if (!documentXml) {
    addCheck('table_placement', 'failed', 'Cannot inspect table placement without word/document.xml.');
    return;
  }
  if (!renderPlan) {
    addCheck('table_placement', 'failed', 'render-plan.json is required for table placement validation.');
    return;
  }

  const tables = renderPlan.tables || [];
  if (tables.length === 0) {
    addCheck('table_placement', 'passed_with_warnings', 'No tables were present in render-plan.json.');
    return;
  }

  const placementFailures = tablePlacementFailures({ documentXml, renderPlan });
  if (placementFailures.length > 0) {
    addCheck(
      'table_placement',
      'failed',
      `DOCX table placement does not match render-plan sections: ${placementFailures.join(', ')}`
    );
    return;
  }

  addCheck('table_placement', 'passed');
}

function addTableCaptionCheck({ addCheck, documentXml, renderPlan }) {
  if (!documentXml) {
    addCheck('table_caption', 'failed', 'Cannot inspect table captions without word/document.xml.');
    return;
  }
  if (!renderPlan) {
    addCheck('table_caption', 'failed', 'render-plan.json is required for table caption validation.');
    return;
  }

  const tables = renderPlan.tables || [];
  if (tables.length === 0) {
    addCheck('table_caption', 'passed_with_warnings', 'No tables were present in render-plan.json.');
    return;
  }

  const captionFailures = tableCaptionFailures({ documentXml, renderPlan });
  if (captionFailures.length > 0) {
    addCheck(
      'table_caption',
      'failed',
      `DOCX table captions are not bound to render-plan tables: ${captionFailures.join(', ')}`
    );
    return;
  }

  addCheck('table_caption', 'passed');
}

function addBlockOrderCheck({ addCheck, documentXml, renderPlan }) {
  if (!documentXml) {
    addCheck('block_order', 'failed', 'Cannot inspect block order without word/document.xml.');
    return;
  }
  if (!renderPlan) {
    addCheck('block_order', 'failed', 'render-plan.json is required for block order validation.');
    return;
  }

  const orderFailures = blockOrderFailures({ documentXml, renderPlan });
  if (orderFailures.length > 0) {
    addCheck(
      'block_order',
      'failed',
      `DOCX rich content does not follow source block order: ${orderFailures.join(', ')}`
    );
    return;
  }

  addCheck('block_order', 'passed');
}

function addFigureIdMetadataCheck({ addCheck, documentXml, renderPlan }) {
  if (!documentXml) {
    addCheck('figure_id_metadata', 'failed', 'Cannot inspect figure metadata without word/document.xml.');
    return;
  }
  if (!renderPlan) {
    addCheck('figure_id_metadata', 'failed', 'render-plan.json is required for figure metadata validation.');
    return;
  }

  const figureIds = collectFigureIds(renderPlan);
  if (figureIds.length === 0) {
    addCheck('figure_id_metadata', 'passed_with_warnings', 'No figures or images were present in render-plan.json.');
    return;
  }

  const docPrFigureIds = extractDocPrFigureIds(documentXml);
  const missingFigureIds = figureIds.filter((figureId) => !docPrFigureIds.has(figureId));
  if (missingFigureIds.length > 0) {
    addCheck(
      'figure_id_metadata',
      'failed',
      `Missing DOCX figureId metadata on image objects: ${missingFigureIds.join(', ')}`
    );
    return;
  }

  const bindingFailures = figureBindingMetadataFailures({ documentXml, renderPlan });
  if (bindingFailures.length > 0) {
    addCheck(
      'figure_id_metadata',
      'failed',
      `DOCX figure metadata does not match render-plan: ${bindingFailures.join(', ')}`
    );
    return;
  }

  addCheck('figure_id_metadata', 'passed');
}

function addFigurePlacementCheck({ addCheck, documentXml, renderPlan }) {
  if (!documentXml) {
    addCheck('figure_placement', 'failed', 'Cannot inspect figure placement without word/document.xml.');
    return;
  }
  if (!renderPlan) {
    addCheck('figure_placement', 'failed', 'render-plan.json is required for figure placement validation.');
    return;
  }

  const images = renderPlan.templateData?.images || [];
  if (images.length === 0) {
    addCheck('figure_placement', 'passed_with_warnings', 'No images were present in render-plan.json.');
    return;
  }

  const placementFailures = figurePlacementFailures({ documentXml, renderPlan });
  if (placementFailures.length > 0) {
    addCheck(
      'figure_placement',
      'failed',
      `DOCX figure placement does not match render-plan sections: ${placementFailures.join(', ')}`
    );
    return;
  }

  addCheck('figure_placement', 'passed');
}

function addFigureCaptionCheck({ addCheck, documentXml, renderPlan }) {
  if (!documentXml) {
    addCheck('figure_caption', 'failed', 'Cannot inspect figure captions without word/document.xml.');
    return;
  }
  if (!renderPlan) {
    addCheck('figure_caption', 'failed', 'render-plan.json is required for figure caption validation.');
    return;
  }

  const images = renderPlan.templateData?.images || [];
  if (images.length === 0) {
    addCheck('figure_caption', 'passed_with_warnings', 'No images were present in render-plan.json.');
    return;
  }

  const captionFailures = figureCaptionFailures({ documentXml, renderPlan });
  if (captionFailures.length > 0) {
    addCheck(
      'figure_caption',
      'failed',
      `DOCX figure captions are not bound to render-plan images: ${captionFailures.join(', ')}`
    );
    return;
  }

  addCheck('figure_caption', 'passed');
}

function hasTemplateMarkers(documentXml) {
  return /\{d\.[^}]+}/.test(documentXml || '');
}

function extractDocPrFigureIds(documentXml) {
  const figureIds = new Set();
  for (const match of String(documentXml || '').matchAll(/<wp:docPr\b[^>]*>/g)) {
    for (const figureMatch of match[0].matchAll(/\bfigureId=([A-Za-z0-9_-]+)/g)) {
      figureIds.add(figureMatch[1]);
    }
  }
  return figureIds;
}

function figureBindingMetadataFailures({ documentXml, renderPlan }) {
  const failures = [];
  const docPrMetadataByFigureId = extractDocPrFigureMetadata(documentXml);
  const expectedBindings = expectedFigureBindings(renderPlan);

  for (const [figureId, expected] of expectedBindings) {
    const actual = docPrMetadataByFigureId.get(figureId);
    if (!actual) {
      continue;
    }

    for (const key of ['sectionId', 'blockId', 'afterBlockId']) {
      if (!expected[key]) {
        continue;
      }
      if (actual[key] !== expected[key]) {
        failures.push(
          `${figureId} ${key} expected ${expected[key]} from render-plan, got ${actual[key] || 'missing'}`
        );
      }
    }
  }

  return failures;
}

function expectedFigureBindings(renderPlan) {
  const bindings = new Map();
  const figuresById = new Map((renderPlan?.figures || []).map((figure) => [figure.figureId, figure]));
  for (const image of renderPlan?.templateData?.images || []) {
    const figureId = String(image?.figureId || '').trim();
    if (!figureId) {
      continue;
    }
    const metadata = image.metadata || {};
    const figure = figuresById.get(figureId) || {};
    bindings.set(figureId, {
      sectionId: String(metadata.sectionId || figure.sectionId || '').trim(),
      blockId: String(metadata.blockId || '').trim(),
      afterBlockId: String(metadata.afterBlockId || figure.afterBlockId || '').trim(),
    });
  }
  return bindings;
}

function extractDocPrFigureMetadata(documentXml) {
  const metadataByFigureId = new Map();
  for (const match of String(documentXml || '').matchAll(/<wp:docPr\b[^>]*>/g)) {
    const metadata = parseDocPrMetadata(match[0]);
    if (!metadata.figureId) {
      continue;
    }
    metadataByFigureId.set(metadata.figureId, metadata);
  }
  return metadataByFigureId;
}

function parseDocPrMetadata(tag) {
  const metadata = {};
  for (const match of String(tag || '').matchAll(/\b([A-Za-z][A-Za-z0-9_]*)=([A-Za-z0-9_-]+)/g)) {
    metadata[match[1]] = match[2];
  }
  return metadata;
}

function figurePlacementFailures({ documentXml, renderPlan }) {
  const failures = [];
  const sectionRanges = figureSectionRanges(documentXml, renderPlan);
  const figurePositions = docPrFigurePositions(documentXml);

  for (const image of renderPlan.templateData?.images || []) {
    const figureId = String(image?.figureId || '').trim();
    const sectionId = String(image?.metadata?.sectionId || '').trim();
    if (!figureId || !sectionId) {
      continue;
    }

    const position = figurePositions.get(figureId);
    if (!Number.isInteger(position)) {
      failures.push(`${figureId} missing DOCX drawing for placement check`);
      continue;
    }

    const range = sectionRanges.get(sectionId);
    if (!range) {
      failures.push(`${figureId} missing render-plan section anchor ${sectionId}`);
      continue;
    }

    if (position < range.start || position >= range.end) {
      failures.push(
        `${figureId} appears outside section ${sectionId} placement range`
      );
    }
  }

  return failures;
}

function tablePlacementFailures({ documentXml, renderPlan }) {
  const failures = [];
  const sectionRanges = figureSectionRanges(documentXml, renderPlan);
  const tablePositions = tableMarkerPositions(documentXml);

  for (const table of renderPlan.tables || []) {
    const tableId = String(table?.tableId || '').trim();
    const sectionId = String(table?.sectionId || '').trim();
    if (!tableId || !sectionId) {
      continue;
    }

    const position = tablePositions.get(tableId);
    if (!Number.isInteger(position)) {
      failures.push(`${tableId} missing DOCX table marker for placement check`);
      continue;
    }

    const range = sectionRanges.get(sectionId);
    if (!range) {
      failures.push(`${tableId} missing render-plan section anchor ${sectionId}`);
      continue;
    }

    if (position < range.start || position >= range.end) {
      failures.push(`${tableId} appears outside section ${sectionId} placement range`);
    }
  }

  return failures;
}

function tableCaptionFailures({ documentXml, renderPlan }) {
  const failures = [];
  const sectionRanges = figureSectionRanges(documentXml, renderPlan);
  const paragraphs = paragraphRanges(documentXml);
  const captionParagraphs = tableCaptionParagraphs(paragraphs);

  for (const table of renderPlan.tables || []) {
    const tableId = String(table?.tableId || '').trim();
    const sectionId = String(table?.sectionId || '').trim();
    if (!tableId) {
      continue;
    }

    const caption = captionParagraphs.get(tableId);
    if (!caption) {
      failures.push(`${tableId} missing local table caption with tableId marker`);
      continue;
    }

    if (!hasTableSequenceField(caption.xml)) {
      failures.push(`${tableId} caption missing Word SEQ 表 field`);
    }
    if (!tableBodyImmediatelyFollowsCaption(documentXml, caption)) {
      failures.push(`${tableId} caption must immediately precede its table body`);
    }
    if (sectionId) {
      const range = sectionRanges.get(sectionId);
      if (!range) {
        failures.push(`${tableId} missing render-plan section anchor ${sectionId} for caption`);
      } else if (caption.start < range.start || caption.start >= range.end) {
        failures.push(`${tableId} caption appears outside section ${sectionId}`);
      }
      if (caption.metadata.sectionId !== sectionId) {
        failures.push(
          `${tableId} caption sectionId expected ${sectionId}, got ${caption.metadata.sectionId || 'missing'}`
        );
      }
    }

    const expectedTitle = String(table.title || '').trim();
    if (expectedTitle && !visibleParagraphText(caption.xml).includes(expectedTitle)) {
      failures.push(`${tableId} caption text missing ${expectedTitle}`);
    }
  }

  const unboundSeqParagraphs = paragraphs
    .filter((paragraph) => hasTableSequenceField(paragraph.xml) && !/\btableCaption\b/.test(paragraph.text))
    .map((paragraph) => `unbound SEQ 表 field at paragraph ${paragraph.paragraphIndex + 1}`);
  failures.push(...unboundSeqParagraphs);

  return failures;
}

function tableCaptionParagraphs(paragraphs) {
  const positions = new Map();
  for (const paragraph of paragraphs) {
    if (!/\btableCaption\b/.test(paragraph.text)) {
      continue;
    }
    const metadata = parseDocPrMetadata(paragraph.text);
    if (metadata.tableId && !positions.has(metadata.tableId)) {
      positions.set(metadata.tableId, { ...paragraph, metadata });
    }
  }
  return positions;
}

function hasTableSequenceField(paragraphXml) {
  return /<w:instrText\b[^>]*>[^<]*SEQ\s+表(?:\s|<|$)/.test(String(paragraphXml || ''));
}

function tableBodyImmediatelyFollowsCaption(documentXml, caption) {
  const afterCaption = String(documentXml || '').slice(caption.end);
  return /^\s*<w:tbl\b/.test(afterCaption);
}

function blockOrderFailures({ documentXml, renderPlan }) {
  const failures = [];
  const paragraphs = paragraphRanges(documentXml);
  const tableCaptions = tableCaptionParagraphs(paragraphs);
  const figureDrawings = figureDrawingParagraphs(paragraphs);

  for (const section of renderPlan.templateData?.sections || renderPlan.sections || []) {
    let previous = {
      blockId: '',
      position: sectionInsertionIndexForOrder(documentXml, section.title),
    };
    for (const block of section.blocks || []) {
      if (block.type === 'paragraph' && isSectionTitleBlock(block, section)) {
        continue;
      }
      const current = blockOrderPosition({
        block,
        section,
        paragraphs,
        tableCaptions,
        figureDrawings,
        afterPosition: previous.position,
      });
      if (!current) {
        failures.push(`${block.blockId || block.tableId || block.figureId || 'unknown'} missing DOCX position for block order`);
        continue;
      }
      if (Number.isInteger(previous.position) && current.position < previous.position) {
        failures.push(
          `${current.label} appears before source block order afterBlockId=${previous.blockId || 'section'}`
        );
      }
      previous = {
        blockId: block.blockId || current.label,
        position: current.position,
      };
    }
  }

  return failures;
}

function blockOrderPosition({ block, section, paragraphs, tableCaptions, figureDrawings, afterPosition }) {
  if (block.type === 'paragraph') {
    const paragraph = paragraphs.find(
      (item) =>
        item.start >= afterPosition &&
        item.text.trim() === String(block.text || '').trim()
    );
    return paragraph ? { label: block.blockId, position: paragraph.start } : null;
  }
  if (block.type === 'table') {
    const caption = tableCaptions.get(block.tableId);
    return caption ? { label: block.tableId || block.blockId, position: caption.start } : null;
  }
  if (block.type === 'figure') {
    const drawing = figureDrawings.get(block.figureId);
    return drawing ? { label: block.figureId || block.blockId, position: drawing.start } : null;
  }
  return { label: block.blockId || section.sectionId || 'unknown', position: afterPosition };
}

function isSectionTitleBlock(block, section) {
  return String(block?.text || '').trim() === String(section?.title || '').trim();
}

function sectionInsertionIndexForOrder(documentXml, sectionTitle) {
  const title = String(sectionTitle || '').trim();
  if (!title) {
    return 0;
  }
  const anchors = paragraphRanges(documentXml).filter((paragraph) => paragraph.text.trim() === title);
  return anchors.length ? anchors[anchors.length - 1].end : 0;
}

function figureCaptionFailures({ documentXml, renderPlan }) {
  const failures = [];
  const sectionRanges = figureSectionRanges(documentXml, renderPlan);
  const paragraphs = paragraphRanges(documentXml);
  const drawingParagraphs = figureDrawingParagraphs(paragraphs);
  const captionParagraphs = figureCaptionParagraphs(paragraphs);

  for (const image of renderPlan.templateData?.images || []) {
    const figureId = String(image?.figureId || '').trim();
    const sectionId = String(image?.metadata?.sectionId || '').trim();
    if (!figureId) {
      continue;
    }

    const drawing = drawingParagraphs.get(figureId);
    if (!drawing) {
      failures.push(`${figureId} missing DOCX drawing paragraph for caption check`);
      continue;
    }

    const caption = captionParagraphs.get(figureId);
    if (!caption) {
      failures.push(`${figureId} missing local figure caption with figureId marker`);
      continue;
    }

    if (caption.paragraphIndex !== drawing.paragraphIndex + 1) {
      failures.push(`${figureId} caption must immediately follow its drawing paragraph`);
    }
    if (!hasFigureSequenceField(caption.xml)) {
      failures.push(`${figureId} caption missing Word SEQ 图 field`);
    }
    if (sectionId) {
      const range = sectionRanges.get(sectionId);
      if (!range) {
        failures.push(`${figureId} missing render-plan section anchor ${sectionId} for caption`);
      } else if (caption.start < range.start || caption.start >= range.end) {
        failures.push(`${figureId} caption appears outside section ${sectionId}`);
      }
      if (caption.metadata.sectionId !== sectionId) {
        failures.push(
          `${figureId} caption sectionId expected ${sectionId}, got ${caption.metadata.sectionId || 'missing'}`
        );
      }
    }

    const expectedCaption = String(image.caption || '').trim();
    if (expectedCaption && !visibleParagraphText(caption.xml).includes(expectedCaption)) {
      failures.push(`${figureId} caption text missing ${expectedCaption}`);
    }
  }

  const unboundSeqParagraphs = paragraphs
    .filter((paragraph) => hasFigureSequenceField(paragraph.xml) && !/\bfigureCaption\b/.test(paragraph.text))
    .map((paragraph) => `unbound SEQ 图 field at paragraph ${paragraph.paragraphIndex + 1}`);
  failures.push(...unboundSeqParagraphs);

  return failures;
}

function figureDrawingParagraphs(paragraphs) {
  const positions = new Map();
  for (const paragraph of paragraphs) {
    if (!/<w:drawing\b/.test(paragraph.xml)) {
      continue;
    }
    for (const docPrTag of paragraph.xml.match(/<wp:docPr\b[^>]*>/g) || []) {
      const metadata = parseDocPrMetadata(docPrTag);
      if (metadata.figureId && !positions.has(metadata.figureId)) {
        positions.set(metadata.figureId, paragraph);
      }
    }
  }
  return positions;
}

function figureCaptionParagraphs(paragraphs) {
  const positions = new Map();
  for (const paragraph of paragraphs) {
    if (!/\bfigureCaption\b/.test(paragraph.text)) {
      continue;
    }
    const metadata = parseDocPrMetadata(paragraph.text);
    if (metadata.figureId && !positions.has(metadata.figureId)) {
      positions.set(metadata.figureId, { ...paragraph, metadata });
    }
  }
  return positions;
}

function hasFigureSequenceField(paragraphXml) {
  return /<w:instrText\b[^>]*>[^<]*SEQ\s+图(?:\s|<|$)/.test(String(paragraphXml || ''));
}

function visibleParagraphText(paragraphXml) {
  return paragraphText(
    String(paragraphXml || '').replace(/<w:r\b(?:(?!<\/w:r>)[\s\S])*<w:vanish\/>(?:(?!<\/w:r>)[\s\S])*<\/w:r>/g, '')
  );
}

function tableMarkerPositions(documentXml) {
  const positions = new Map();
  for (const match of String(documentXml || '').matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)) {
    const marker = parseDocPrMetadata(paragraphText(match[0]));
    if (marker.tableId && !positions.has(marker.tableId)) {
      positions.set(marker.tableId, match.index);
    }
  }
  return positions;
}

function figureSectionRanges(documentXml, renderPlan) {
  const ranges = new Map();
  const paragraphs = paragraphRanges(documentXml);
  const sections = renderPlan.templateData?.sections || renderPlan.sections || [];
  const anchors = sections
    .map((section) => {
      const title = String(section?.title || '').trim();
      if (!section?.sectionId || !title) {
        return null;
      }
      const candidates = sectionAnchorParagraphs(paragraphs, title);
      const anchor = candidates[candidates.length - 1];
      return anchor ? { sectionId: section.sectionId, start: anchor.end } : null;
    })
    .filter(Boolean)
    .sort((left, right) => left.start - right.start);

  for (let index = 0; index < anchors.length; index += 1) {
    ranges.set(anchors[index].sectionId, {
      start: anchors[index].start,
      end: anchors[index + 1]?.start || bodyEndIndex(documentXml),
    });
  }
  return ranges;
}

function sectionAnchorParagraphs(paragraphs, title) {
  const exact = paragraphs.filter((paragraph) => paragraph.text.trim() === title);
  if (exact.length > 0) {
    return exact;
  }
  return paragraphs.filter(
    (paragraph) => paragraph.text.includes(title) && !/\b(docx-engine-v2|figureCaption|tableId)\b/.test(paragraph.text)
  );
}

function docPrFigurePositions(documentXml) {
  const positions = new Map();
  for (const match of String(documentXml || '').matchAll(/<wp:docPr\b[^>]*>/g)) {
    const metadata = parseDocPrMetadata(match[0]);
    if (metadata.figureId && !positions.has(metadata.figureId)) {
      positions.set(metadata.figureId, match.index);
    }
  }
  return positions;
}

function paragraphRanges(documentXml) {
  const ranges = [];
  let paragraphIndex = 0;
  for (const match of String(documentXml || '').matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)) {
    ranges.push({
      paragraphIndex,
      start: match.index,
      end: match.index + match[0].length,
      xml: match[0],
      text: paragraphText(match[0]),
    });
    paragraphIndex += 1;
  }
  return ranges;
}

function paragraphText(paragraphXml) {
  return [...String(paragraphXml || '').matchAll(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g)]
    .map((match) => unescapeXmlText(match[1]))
    .join('');
}

function unescapeXmlText(value) {
  return String(value || '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
}

function bodyEndIndex(documentXml) {
  const bodyEnd = String(documentXml || '').indexOf('</w:body>');
  return bodyEnd >= 0 ? bodyEnd : String(documentXml || '').length;
}

function buildReport(checksById, warnings, failures) {
  const checks = CHECK_IDS.map((id) => checksById.get(id) || { id, status: 'failed' });
  const hasFailure = checks.some((check) => check.status === 'failed') || failures.length > 0;
  const hasWarning = checks.some(
    (check) => check.status === 'passed_with_warnings' || check.status === 'not_verified'
  );
  const status = hasFailure ? 'failed' : hasWarning || warnings.length > 0 ? 'passed_with_warnings' : 'passed';

  return {
    schemaVersion: 'docx-engine-v2/validation-report',
    status,
    checks,
    warnings: uniqueStrings(warnings),
    failures: uniqueStrings(failures),
  };
}

function normalizeWpsStatus(status) {
  return ['passed', 'passed_with_warnings', 'failed', 'not_verified'].includes(status)
    ? status
    : 'not_verified';
}

function collectFigureIds(renderPlan) {
  const figureIds = new Set();
  for (const figure of renderPlan.figures || []) {
    if (figure.figureId) {
      figureIds.add(figure.figureId);
    }
  }
  for (const image of renderPlan.templateData?.images || []) {
    if (image.figureId) {
      figureIds.add(image.figureId);
    }
  }
  return [...figureIds];
}

function uniqueStrings(items) {
  return [...new Set(items.filter(Boolean))];
}

function readZipEntries(filePath) {
  const buffer = fs.readFileSync(filePath);
  const eocdOffset = findEndOfCentralDirectory(buffer);
  if (eocdOffset < 0) {
    throw new Error('missing ZIP end of central directory');
  }

  const centralDirectorySize = buffer.readUInt32LE(eocdOffset + 12);
  const centralDirectoryOffset = buffer.readUInt32LE(eocdOffset + 16);
  const centralDirectoryEnd = centralDirectoryOffset + centralDirectorySize;
  if (centralDirectoryEnd > buffer.length) {
    throw new Error('invalid ZIP central directory bounds');
  }

  const entries = new Map();
  let offset = centralDirectoryOffset;
  while (offset < centralDirectoryEnd) {
    if (offset + 46 > buffer.length || buffer.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error('invalid ZIP central directory entry');
    }

    const compressionMethod = buffer.readUInt16LE(offset + 10);
    const compressedSize = buffer.readUInt32LE(offset + 20);
    const fileNameLength = buffer.readUInt16LE(offset + 28);
    const extraFieldLength = buffer.readUInt16LE(offset + 30);
    const fileCommentLength = buffer.readUInt16LE(offset + 32);
    const localHeaderOffset = buffer.readUInt32LE(offset + 42);
    const fileNameStart = offset + 46;
    const fileNameEnd = fileNameStart + fileNameLength;
    const fileName = buffer.subarray(fileNameStart, fileNameEnd).toString('utf8');

    if (!fileName.endsWith('/')) {
      entries.set(
        fileName,
        readZipEntryBuffer({ buffer, compressionMethod, compressedSize, localHeaderOffset })
      );
    }

    offset = fileNameEnd + extraFieldLength + fileCommentLength;
  }

  return entries;
}

function readZipEntryBuffer({ buffer, compressionMethod, compressedSize, localHeaderOffset }) {
  if (localHeaderOffset + 30 > buffer.length || buffer.readUInt32LE(localHeaderOffset) !== 0x04034b50) {
    throw new Error('invalid ZIP local file header');
  }

  const fileNameLength = buffer.readUInt16LE(localHeaderOffset + 26);
  const extraFieldLength = buffer.readUInt16LE(localHeaderOffset + 28);
  const dataStart = localHeaderOffset + 30 + fileNameLength + extraFieldLength;
  const dataEnd = dataStart + compressedSize;
  if (dataEnd > buffer.length) {
    throw new Error('invalid ZIP entry data bounds');
  }

  const compressed = buffer.subarray(dataStart, dataEnd);
  if (compressionMethod === 0) {
    return compressed;
  }
  if (compressionMethod === 8) {
    return zlib.inflateRawSync(compressed);
  }

  throw new Error(`unsupported ZIP compression method: ${compressionMethod}`);
}

function findEndOfCentralDirectory(buffer) {
  const minOffset = Math.max(0, buffer.length - 65557);
  for (let offset = buffer.length - 22; offset >= minOffset; offset -= 1) {
    if (buffer.readUInt32LE(offset) === 0x06054b50) {
      return offset;
    }
  }
  return -1;
}

module.exports = { validateDeliveryPackage };
