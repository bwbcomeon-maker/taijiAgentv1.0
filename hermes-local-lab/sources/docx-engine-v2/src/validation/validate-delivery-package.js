const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const zlib = require('node:zlib');

const { validateDomainObject } = require('../domain/validate');

const CHECK_IDS = [
  'schema',
  'source_original',
  'docx_zip',
  'template_markers',
  'image_coverage',
  'table_coverage',
  'figure_id_metadata',
  'delivery_files',
  'wps_visual',
];

const REQUIRED_ENTRIES = [
  'document.docx',
  'delivery-package.json',
  'source.md',
  'source/original',
  'assets',
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
  addImageCoverageCheck({ addCheck, deliveryDir, documentXml, renderPlan: jsonFiles.renderPlan });
  addTableCoverageCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addFigureIdMetadataCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });

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
    !jsonFiles.jobManifest ||
    !jsonFiles.templateManifest ||
    !jsonFiles.renderPlan ||
    !jsonFiles.qualityReport
  ) {
    addCheck(
      'schema',
      'failed',
      'delivery-package.json, job.manifest.json, template.manifest.json, render-plan.json and quality-report.json are required for schema validation.'
    );
    return;
  }

  const deliveryPackageResult = validateDomainObject('DeliveryPackage', jsonFiles.deliveryPackage);
  const jobManifestResult = validateDomainObject('DocumentJob', jsonFiles.jobManifest);
  const templateManifestResult = validateDomainObject('TemplateManifest', jsonFiles.templateManifest);
  const renderPlanResult = validateDomainObject('RenderPlan', jsonFiles.renderPlan);
  const qualityReportResult = validateDomainObject('ValidationReport', jsonFiles.qualityReport);
  if (
    !deliveryPackageResult.ok ||
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
        ...tagValidationErrors('job.manifest.json', jobManifestResult.errors),
        ...tagValidationErrors('template.manifest.json', templateManifestResult.errors),
        ...tagValidationErrors('render-plan.json', renderPlanResult.errors),
        ...tagValidationErrors('quality-report.json', qualityReportResult.errors),
      ])}`
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

function addImageCoverageCheck({ addCheck, deliveryDir, documentXml, renderPlan }) {
  if (!renderPlan) {
    addCheck('image_coverage', 'failed', 'render-plan.json is required for image coverage.');
    return;
  }

  const images = renderPlan.templateData?.images || [];
  const missingImages = images
    .map((image) => image.path)
    .filter(Boolean)
    .filter((imagePath) => !fs.existsSync(path.resolve(deliveryDir, imagePath)));
  if (missingImages.length > 0) {
    addCheck('image_coverage', 'failed', `Missing delivery image assets: ${missingImages.join(', ')}`);
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

  addCheck('figure_id_metadata', 'passed');
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
