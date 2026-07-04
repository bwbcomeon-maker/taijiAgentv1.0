const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { validateDomainObject } = require('../domain/validate');
const {
  buildDeliveryFileSha256,
  deliveryFileHashFailures,
  REPLAY_INPUT_FILE_ROLES,
} = require('../delivery/file-hashes');
const { resolveSectionAnchors } = require('../domain/section-anchors');
const {
  readZipEntriesFromBuffer,
  replayOriginalSourcePackage,
  sourceReplayFailures,
} = require('../replay/source-replay');

const CHECK_IDS = [
  'schema',
  'source_original',
  'source_replay',
  'docx_zip',
  'template_markers',
  'image_coverage',
  'table_coverage',
  'table_content',
  'table_placement',
  'table_caption',
  'block_order',
  'figure_id_metadata',
  'figure_placement',
  'figure_caption',
  'image_instructions',
  'delivery_files',
  'wps_visual',
];

const REQUIRED_ENTRIES = [
  'document.docx',
  'delivery-package.json',
  'source.md',
  'source-package.json',
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
  sourcePackage: 'source-package.json',
  assetsDir: 'assets',
  assetPackage: 'asset-package.json',
  jobManifest: 'job.manifest.json',
  templateManifest: 'template.manifest.json',
  renderPlan: 'render-plan.json',
  qualityReport: 'quality-report.json',
  imageInstructions: 'README-图片调整说明.md',
};

function validateDeliveryPackage({
  deliveryDir,
  wpsVisualStatus = 'not_verified',
  requireReplayReport = false,
  enforceStoredQualityReport = requireReplayReport,
} = {}) {
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
    ['sourcePackage', 'source-package.json'],
    ['assetPackage', 'asset-package.json'],
    ['jobManifest', 'job.manifest.json'],
    ['templateManifest', 'template.manifest.json'],
    ['renderPlan', 'render-plan.json'],
    ['qualityReport', 'quality-report.json'],
    ['replayReport', 'replay-report.json'],
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
  addReplayReportCheck({
    addCheck,
    deliveryDir,
    deliveryPackage: jsonFiles.deliveryPackage,
    replayReport: jsonFiles.replayReport,
    requireReplayReport,
  });
  addQualityReportCheck({
    addCheck,
    qualityReport: jsonFiles.qualityReport,
    enforceStoredQualityReport,
  });
  addDeliveryManifestFilesCheck({
    addCheck,
    deliveryDir,
    deliveryPackage: jsonFiles.deliveryPackage,
    jobManifest: jsonFiles.jobManifest,
  });
  addSourceOriginalCheck({
    addCheck,
    deliveryDir,
    jobManifest: jsonFiles.jobManifest,
    sourcePackage: jsonFiles.sourcePackage,
  });
  addSourceReplayCheck({
    addCheck,
    deliveryDir,
    sourcePackage: jsonFiles.sourcePackage,
    jobManifest: jsonFiles.jobManifest,
  });
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
  addTableContentCheck({
    addCheck,
    documentXml,
    sourcePackage: jsonFiles.sourcePackage,
    assetPackage: jsonFiles.assetPackage,
    renderPlan: jsonFiles.renderPlan,
  });
  addTablePlacementCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addTableCaptionCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addBlockOrderCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addFigureIdMetadataCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addFigurePlacementCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addFigureCaptionCheck({ addCheck, documentXml, renderPlan: jsonFiles.renderPlan });
  addImageInstructionsCheck({
    addCheck,
    deliveryDir,
    assetPackage: jsonFiles.assetPackage,
  });

  addWpsVisualCheck({
    addCheck,
    deliveryDir,
    qualityReport: jsonFiles.qualityReport,
    renderPlan: jsonFiles.renderPlan,
    fallbackStatus: wpsVisualStatus,
  });

  return buildReport(checksById, warnings, failures);
}

function addReplayReportCheck({ addCheck, deliveryDir, deliveryPackage, replayReport, requireReplayReport }) {
  if (!requireReplayReport && !replayReport && !deliveryPackage?.files?.replayReport) {
    return;
  }

  const relativePath = normalizeRelativePackagePath(deliveryPackage?.files?.replayReport);
  const expectedHash = deliveryPackage?.fileSha256?.replayReport || '';
  if (!relativePath) {
    addCheck('replay_report', 'failed', 'replay-report.json is required for final delivery validation.');
    return;
  }
  if (!expectedHash) {
    addCheck('replay_report', 'failed', 'delivery-package.json fileSha256.replayReport is required for final delivery validation.');
    return;
  }

  const replayReportPath = path.join(deliveryDir, relativePath);
  if (!fs.existsSync(replayReportPath) || !fs.statSync(replayReportPath).isFile()) {
    addCheck('replay_report', 'failed', `replay-report.json is required for final delivery validation: missing ${relativePath}`);
    return;
  }

  if (!replayReport) {
    addCheck('replay_report', 'failed', 'replay-report.json must be valid JSON for final delivery validation.');
    return;
  }

  const replayStatus = String(replayReport.status || '').trim();
  const inputHashFailures = replayReportInputFileSha256Failures({
    deliveryDir,
    deliveryPackage,
    replayReport,
  });
  if (inputHashFailures.length > 0) {
    addCheck(
      'replay_report',
      'failed',
      `replay-report.json inputFileSha256 no longer matches current delivery package file hashes: ${inputHashFailures.join('; ')}`
    );
    return;
  }

  const failedReplayChecks = replayReportChecksWithStatus(replayReport, 'failed');
  if (replayStatus === 'failed' || failedReplayChecks.length > 0 || (replayReport.failures || []).length > 0) {
    addCheck(
      'replay_report',
      'failed',
      `replay-report.json records a failed delivery replay: ${replayReportFailureMessage({
        replayReport,
        failedReplayChecks,
      })}`
    );
    return;
  }

  if (!['passed', 'passed_with_warnings'].includes(replayStatus)) {
    addCheck(
      'replay_report',
      'failed',
      `replay-report.json status must be passed or passed_with_warnings for final delivery validation, got ${replayStatus || 'missing'}`
    );
    return;
  }

  const warningMessages = replayReportWarnings(replayReport);
  if (warningMessages.length > 0) {
    addCheck(
      'replay_report',
      'passed_with_warnings',
      `replay-report.json records replay warnings: ${warningMessages.join('; ') || replayStatus}`
    );
    return;
  }

  addCheck('replay_report', 'passed');
}

function addQualityReportCheck({ addCheck, qualityReport, enforceStoredQualityReport }) {
  if (!enforceStoredQualityReport) {
    return;
  }
  if (!qualityReport || typeof qualityReport !== 'object') {
    return;
  }

  const failureMessages = recordedQualityReportFailures(qualityReport);
  if (failureMessages.length > 0) {
    addCheck(
      'quality_report',
      'failed',
      `quality-report.json records failed validation: ${failureMessages.join('; ')}`
    );
    return;
  }

  const status = String(qualityReport.status || '').trim();
  if (!['passed', 'passed_with_warnings'].includes(status)) {
    addCheck(
      'quality_report',
      'failed',
      `quality-report.json status must be passed or passed_with_warnings for final delivery validation, got ${status || 'missing'}`
    );
    return;
  }

  const unverifiedChecks = qualityReportChecks(qualityReport)
    .filter((check) => check?.id !== 'wps_visual' && check?.status === 'not_verified');
  if (unverifiedChecks.length > 0) {
    addCheck(
      'quality_report',
      'failed',
      `quality-report.json records unverified automated checks: ${unverifiedChecks.map(qualityReportCheckMessage).join('; ')}`
    );
    return;
  }

  addCheck('quality_report', 'passed');
}

function recordedQualityReportFailures(qualityReport) {
  return [
    ...qualityReportFailures(qualityReport),
    ...qualityReportChecks(qualityReport)
      .filter((check) => check?.status === 'failed')
      .map(qualityReportCheckMessage),
    String(qualityReport.status || '').trim() === 'failed' ? 'quality-report.json status=failed' : '',
  ].filter(Boolean);
}

function qualityReportChecks(qualityReport) {
  return Array.isArray(qualityReport.checks) ? qualityReport.checks : [];
}

function qualityReportFailures(qualityReport) {
  return Array.isArray(qualityReport.failures) ? qualityReport.failures : [];
}

function qualityReportCheckMessage(check) {
  return `${check.id || 'unknown'} ${check.status || 'missing'}${check.message ? `: ${check.message}` : ''}`;
}

function replayReportInputFileSha256Failures({ deliveryDir, deliveryPackage, replayReport }) {
  const recorded = replayReport.inputFileSha256;
  if (!recorded || typeof recorded !== 'object') {
    return ['inputFileSha256 is required'];
  }
  const current = buildDeliveryFileSha256({
    deliveryDir,
    files: deliveryPackage?.files || {},
    roles: REPLAY_INPUT_FILE_ROLES,
  });
  const failures = [];
  for (const role of REPLAY_INPUT_FILE_ROLES) {
    const recordedHash = String(recorded[role] || '').trim();
    const currentHash = String(current[role] || '').trim();
    if (!recordedHash) {
      failures.push(`${role} hash is missing`);
      continue;
    }
    if (!currentHash) {
      failures.push(`${role} current hash is missing`);
      continue;
    }
    if (recordedHash !== currentHash) {
      failures.push(`${role} expected ${recordedHash}, got ${currentHash}`);
    }
  }
  return failures;
}

function replayReportChecksWithStatus(replayReport, status) {
  return (replayReport.checks || []).filter((check) => check?.status === status);
}

function replayReportFailureMessage({ replayReport, failedReplayChecks }) {
  const failures = [
    ...(replayReport.failures || []),
    ...failedReplayChecks.map((check) => `${check.id || 'unknown'} ${check.status}${check.message ? `: ${check.message}` : ''}`),
  ].filter(Boolean);
  return failures.join('; ') || 'unknown failure';
}

function replayReportWarnings(replayReport) {
  return [
    ...(replayReport.warnings || []),
    ...replayReportChecksWithStatus(replayReport, 'passed_with_warnings')
      .map((check) => check.message || `${check.id || 'unknown'} passed_with_warnings`),
    ...replayReportChecksWithStatus(replayReport, 'not_verified')
      .map((check) => check.message || `${check.id || 'unknown'} not_verified`),
  ].filter((message) => message && !isExpectedPreAcceptanceReplayWarning(message));
}

function isExpectedPreAcceptanceReplayWarning(message) {
  return (
    /WPS\/Word visual inspection has not been performed/i.test(message) ||
    /^Delivery validation passed with warnings\.$/.test(message)
  );
}

function addSchemaCheck({ addCheck, jsonFiles }) {
  if (
    !jsonFiles.deliveryPackage ||
    !jsonFiles.sourcePackage ||
    !jsonFiles.assetPackage ||
    !jsonFiles.jobManifest ||
    !jsonFiles.templateManifest ||
    !jsonFiles.renderPlan ||
    !jsonFiles.qualityReport
  ) {
    addCheck(
      'schema',
      'failed',
      'delivery-package.json, source-package.json, asset-package.json, job.manifest.json, template.manifest.json, render-plan.json and quality-report.json are required for schema validation.'
    );
    return;
  }

  const deliveryPackageResult = validateDomainObject('DeliveryPackage', jsonFiles.deliveryPackage);
  const sourcePackageResult = validateDomainObject('SourcePackage', jsonFiles.sourcePackage);
  const assetPackageResult = validateDomainObject('AssetPackage', jsonFiles.assetPackage);
  const jobManifestResult = validateDomainObject('DocumentJob', jsonFiles.jobManifest);
  const templateManifestResult = validateDomainObject('TemplateManifest', jsonFiles.templateManifest);
  const renderPlanResult = validateDomainObject('RenderPlan', jsonFiles.renderPlan);
  const qualityReportResult = validateDomainObject('ValidationReport', jsonFiles.qualityReport);
  const replayReportResult = jsonFiles.replayReport
    ? validateDomainObject('ReplayReport', jsonFiles.replayReport)
    : { ok: true, errors: [] };
  if (
    !deliveryPackageResult.ok ||
    !sourcePackageResult.ok ||
    !assetPackageResult.ok ||
    !jobManifestResult.ok ||
    !templateManifestResult.ok ||
    !renderPlanResult.ok ||
    !qualityReportResult.ok ||
    !replayReportResult.ok
  ) {
    addCheck(
      'schema',
      'failed',
      `Delivery schema validation failed: ${JSON.stringify([
        ...tagValidationErrors('delivery-package.json', deliveryPackageResult.errors),
        ...tagValidationErrors('source-package.json', sourcePackageResult.errors),
        ...tagValidationErrors('asset-package.json', assetPackageResult.errors),
        ...tagValidationErrors('job.manifest.json', jobManifestResult.errors),
        ...tagValidationErrors('template.manifest.json', templateManifestResult.errors),
        ...tagValidationErrors('render-plan.json', renderPlanResult.errors),
        ...tagValidationErrors('quality-report.json', qualityReportResult.errors),
        ...tagValidationErrors('replay-report.json', replayReportResult.errors),
      ])}`
    );
    return;
  }

  if (jsonFiles.replayReport?.status === 'failed') {
    addCheck(
      'schema',
      'failed',
      `replay-report.json records a failed delivery replay: ${(jsonFiles.replayReport.failures || []).join('; ') || 'unknown failure'}`
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

  const sourceRefFailures = sourceRefConsistencyFailures({
    sourcePackage: jsonFiles.sourcePackage,
    jobManifest: jsonFiles.jobManifest,
  });
  if (sourceRefFailures.length > 0) {
    addCheck(
      'schema',
      'failed',
      `Delivery package sourceRef mismatch: ${sourceRefFailures.join(', ')}`
    );
    return;
  }

  const renderPlanSourceFailures = renderPlanSourceConsistencyFailures({
    sourcePackage: jsonFiles.sourcePackage,
    renderPlan: jsonFiles.renderPlan,
  });
  if (renderPlanSourceFailures.length > 0) {
    addCheck(
      'schema',
      'failed',
      `Delivery package render plan/source package mismatch: ${renderPlanSourceFailures.join(', ')}`
    );
    return;
  }

  const assetPackageSourceFailures = assetPackageSourceConsistencyFailures({
    sourcePackage: jsonFiles.sourcePackage,
    assetPackage: jsonFiles.assetPackage,
  });
  if (assetPackageSourceFailures.length > 0) {
    addCheck(
      'schema',
      'failed',
      `Delivery package asset/source package mismatch: ${assetPackageSourceFailures.join(', ')}`
    );
    return;
  }

  const assetPackageRenderFailures = assetPackageRenderConsistencyFailures({
    assetPackage: jsonFiles.assetPackage,
    renderPlan: jsonFiles.renderPlan,
  });
  if (assetPackageRenderFailures.length > 0) {
    addCheck(
      'schema',
      'failed',
      `Delivery package asset/render plan mismatch: ${assetPackageRenderFailures.join(', ')}`
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

function sourceRefConsistencyFailures({ sourcePackage, jobManifest }) {
  const failures = [];
  const sourceRef = sourcePackage?.sourceRef || {};
  const jobSourceRef = jobManifest?.sourceRef || {};
  for (const field of ['type', 'path', 'sha256']) {
    const sourcePackageValue = String(sourceRef[field] || '');
    const jobManifestValue = String(jobSourceRef[field] || '');
    if (sourcePackageValue !== jobManifestValue) {
      failures.push(
        `source-package.json sourceRef.${field}=${sourcePackageValue || 'missing'} does not match job.manifest.json sourceRef.${field}=${jobManifestValue || 'missing'}`
      );
    }
  }
  return failures;
}

function renderPlanSourceConsistencyFailures({ sourcePackage, renderPlan }) {
  const failures = [];
  const sourceSections = sourcePackage?.sections || [];
  failures.push(
    ...sectionListConsistencyFailures({
      actualSections: renderPlan?.sections || [],
      expectedSections: sourceSections,
      actualLabel: 'render-plan.json sections',
      expectedLabel: 'source-package.json sections',
      compareBlockIds: true,
    })
  );
  failures.push(
    ...sectionListConsistencyFailures({
      actualSections: renderPlan?.templateData?.sections || [],
      expectedSections: sourceSections,
      actualLabel: 'render-plan.json templateData.sections',
      expectedLabel: 'source-package.json sections',
    })
  );
  failures.push(
    ...templateSectionBlockConsistencyFailures({
      actualSections: renderPlan?.templateData?.sections || [],
      sourcePackage,
      renderPlan,
    })
  );
  const sourceTables = sourcePackage?.tables || [];
  failures.push(
    ...tableListConsistencyFailures({
      actualTables: renderPlan?.tables || [],
      expectedTables: sourceTables,
      actualLabel: 'render-plan.json tables',
      expectedLabel: 'source-package.json tables',
    })
  );
  failures.push(
    ...templateTableConsistencyFailures({
      actualTables: renderPlan?.templateData?.tables || [],
      expectedTables: sourceTables,
    })
  );
  const sourceFigures = sourcePackage?.figures || [];
  const sourceImages = sourcePackage?.images || [];
  failures.push(
    ...figureListConsistencyFailures({
      actualFigures: renderPlan?.figures || [],
      expectedFigures: sourceFigures,
    })
  );
  failures.push(
    ...templateImageConsistencyFailures({
      actualImages: renderPlan?.templateData?.images || [],
      expectedFigures: sourceFigures,
      expectedImages: sourceImages,
    })
  );

  const renderTitle = String(renderPlan?.templateData?.title || '');
  const sourceTitle = String(sourcePackage?.title || '');
  if (renderTitle !== sourceTitle) {
    failures.push(
      `render-plan.json templateData.title=${displayValue(renderTitle)} does not match source-package.json title=${displayValue(sourceTitle)}`
    );
  }
  return failures;
}

function assetPackageSourceConsistencyFailures({ sourcePackage, assetPackage }) {
  const failures = [];
  failures.push(
    ...tableListConsistencyFailures({
      actualTables: assetPackage?.tables || [],
      expectedTables: sourcePackage?.tables || [],
      actualLabel: 'asset-package.json tables',
      expectedLabel: 'source-package.json tables',
    })
  );
  failures.push(
    ...figureListConsistencyFailures({
      actualFigures: assetPackage?.figures || [],
      expectedFigures: sourcePackage?.figures || [],
      actualLabel: 'asset-package.json figures',
      expectedLabel: 'source-package.json figures',
    })
  );
  failures.push(
    ...sourceImageListConsistencyFailures({
      actualImages: assetPackage?.images || [],
      expectedImages: sourcePackage?.images || [],
      actualLabel: 'asset-package.json images',
      expectedLabel: 'source-package.json images',
    })
  );
  return failures;
}

function assetPackageRenderConsistencyFailures({ assetPackage, renderPlan }) {
  const failures = [];
  const renderImages = renderPlan?.templateData?.images || [];
  const figureImagesById = new Map(
    renderImages
      .filter((image) => image.metadata?.sourceType === 'figure')
      .map((image) => [image.figureId, image])
  );
  for (const figure of assetPackage?.figures || []) {
    const image = figureImagesById.get(figure.figureId);
    if (!image) {
      failures.push(`asset-package.json figures missing render-plan.json templateData.images figureId=${figure.figureId}`);
      continue;
    }
    comparePackagedImageFileFields({
      failures,
      packaged: figure,
      planned: image,
      actualLabel: `asset-package.json figures.${figure.figureId}`,
      expectedLabel: `render-plan.json templateData.images.${figure.figureId}`,
    });
  }

  const sourceImagesById = new Map(
    renderImages
      .filter((image) => image.metadata?.sourceType === 'image')
      .map((image) => [image.metadata?.sourceImageId || image.metadata?.imageId || '', image])
  );
  for (const sourceImage of assetPackage?.images || []) {
    const image = sourceImagesById.get(sourceImage.imageId);
    if (!image) {
      failures.push(`asset-package.json images missing render-plan.json templateData.images imageId=${sourceImage.imageId}`);
      continue;
    }
    comparePackagedImageFileFields({
      failures,
      packaged: sourceImage,
      planned: image,
      actualLabel: `asset-package.json images.${sourceImage.imageId}`,
      expectedLabel: `render-plan.json templateData.images.${image.figureId}`,
    });
  }
  return failures;
}

function comparePackagedImageFileFields({ failures, packaged, planned, actualLabel, expectedLabel }) {
  const packagedPath = String(packaged.displayPath ?? '');
  const plannedPath = String(planned.path ?? '');
  if (packagedPath !== plannedPath) {
    failures.push(
      `${actualLabel}.displayPath=${displayValue(packagedPath)} does not match ${expectedLabel}.path=${displayValue(plannedPath)}`
    );
  }
  const packagedSha256 = String(packaged.sha256 ?? '');
  const plannedSha256 = String(planned.sha256 ?? '');
  if (packagedSha256 !== plannedSha256) {
    failures.push(
      `${actualLabel}.sha256=${displayValue(packagedSha256)} does not match ${expectedLabel}.sha256=${displayValue(plannedSha256)}`
    );
  }
}

function figureListConsistencyFailures({
  actualFigures,
  expectedFigures,
  actualLabel = 'render-plan.json figures',
  expectedLabel = 'source-package.json figures',
}) {
  const failures = [];
  if (actualFigures.length !== expectedFigures.length) {
    failures.push(
      `${actualLabel} count=${actualFigures.length} does not match ${expectedLabel} count=${expectedFigures.length}`
    );
  }

  const actualById = new Map(actualFigures.map((figure) => [figure.figureId, figure]));
  for (const expected of expectedFigures) {
    const actual = actualById.get(expected.figureId);
    if (!actual) {
      failures.push(`${actualLabel} missing figureId=${expected.figureId}`);
      continue;
    }
    for (const field of ['figureId', 'caption', 'sectionId', 'anchorText']) {
      const actualValue = String(actual[field] ?? '');
      const expectedValue = String(expected[field] ?? '');
      if (actualValue !== expectedValue) {
        failures.push(
          `${actualLabel}.${expected.figureId}.${field}=${displayValue(actualValue)} does not match ${expectedLabel}.${expected.figureId}.${field}=${displayValue(expectedValue)}`
        );
      }
    }
  }
  return failures;
}

function sourceImageListConsistencyFailures({ actualImages, expectedImages, actualLabel, expectedLabel }) {
  const failures = [];
  if (actualImages.length !== expectedImages.length) {
    failures.push(
      `${actualLabel} count=${actualImages.length} does not match ${expectedLabel} count=${expectedImages.length}`
    );
  }

  const actualById = new Map(actualImages.map((image) => [image.imageId, image]));
  for (const expected of expectedImages) {
    const actual = actualById.get(expected.imageId);
    if (!actual) {
      failures.push(`${actualLabel} missing imageId=${expected.imageId}`);
      continue;
    }
    for (const field of ['imageId', 'caption', 'sectionId']) {
      const actualValue = String(actual[field] ?? '');
      const expectedValue = String(expected[field] ?? '');
      if (actualValue !== expectedValue) {
        failures.push(
          `${actualLabel}.${expected.imageId}.${field}=${displayValue(actualValue)} does not match ${expectedLabel}.${expected.imageId}.${field}=${displayValue(expectedValue)}`
        );
      }
    }
    const actualSourcePath = String(actual.sourcePath ?? '');
    const expectedPath = String(expected.path ?? '');
    if (actualSourcePath && expectedPath && !assetSourcePathMatchesExpected(actualSourcePath, expectedPath)) {
      failures.push(
        `${actualLabel}.${expected.imageId}.sourcePath=${displayValue(actualSourcePath)} does not match ${expectedLabel}.${expected.imageId}.path=${displayValue(expectedPath)}`
      );
    }
  }
  return failures;
}

function assetSourcePathMatchesExpected(actualSourcePath, expectedPath) {
  const actual = normalizeComparableRelativePath(actualSourcePath);
  const expected = normalizeComparableRelativePath(expectedPath);
  if (!actual || !expected) {
    return false;
  }
  if (actual === expected) {
    return true;
  }
  return actual.endsWith(`/${expected}`);
}

function templateImageConsistencyFailures({ actualImages, expectedFigures, expectedImages }) {
  const failures = [];
  const actualFigureImages = actualImages.filter((image) => image.metadata?.sourceType === 'figure');
  const actualSourceImages = actualImages.filter((image) => image.metadata?.sourceType === 'image');
  if (actualFigureImages.length !== expectedFigures.length) {
    failures.push(
      `render-plan.json templateData.images figure count=${actualFigureImages.length} does not match source-package.json figures count=${expectedFigures.length}`
    );
  }
  if (actualSourceImages.length !== expectedImages.length) {
    failures.push(
      `render-plan.json templateData.images source image count=${actualSourceImages.length} does not match source-package.json images count=${expectedImages.length}`
    );
  }

  const figuresById = new Map(expectedFigures.map((figure) => [figure.figureId, figure]));
  for (const actual of actualFigureImages) {
    const expected = figuresById.get(actual.figureId);
    if (!expected) {
      failures.push(`render-plan.json templateData.images has unknown source figureId=${actual.figureId || 'missing'}`);
      continue;
    }
    compareTemplateImageSourceFields({
      failures,
      actual,
      expected,
      actualLabel: `render-plan.json templateData.images.${actual.figureId}`,
      expectedLabel: `source-package.json figures.${expected.figureId}`,
    });
  }

  const imagesById = new Map(expectedImages.map((image) => [image.imageId, image]));
  for (const actual of actualSourceImages) {
    const sourceImageId = actual.metadata?.sourceImageId || actual.metadata?.imageId || '';
    const expected = imagesById.get(sourceImageId);
    if (!expected) {
      failures.push(
        `render-plan.json templateData.images has unknown source imageId=${sourceImageId || 'missing'}`
      );
      continue;
    }
    compareTemplateImageSourceFields({
      failures,
      actual,
      expected,
      actualLabel: `render-plan.json templateData.images.${actual.figureId}`,
      expectedLabel: `source-package.json images.${expected.imageId}`,
    });
  }
  return failures;
}

function compareTemplateImageSourceFields({ failures, actual, expected, actualLabel, expectedLabel }) {
  const actualCaption = String(actual.caption ?? '');
  const expectedCaption = String(expected.caption ?? '');
  if (actualCaption !== expectedCaption) {
    failures.push(
      `${actualLabel}.caption=${displayValue(actualCaption)} does not match ${expectedLabel}.caption=${displayValue(expectedCaption)}`
    );
  }
  const actualSectionId = String(actual.metadata?.sectionId ?? '');
  const expectedSectionId = String(expected.sectionId ?? '');
  if (actualSectionId !== expectedSectionId) {
    failures.push(
      `${actualLabel}.metadata.sectionId=${displayValue(actualSectionId)} does not match ${expectedLabel}.sectionId=${displayValue(expectedSectionId)}`
    );
  }
}

function tableListConsistencyFailures({ actualTables, expectedTables, actualLabel, expectedLabel }) {
  const failures = [];
  if (actualTables.length !== expectedTables.length) {
    failures.push(
      `${actualLabel} count=${actualTables.length} does not match ${expectedLabel} count=${expectedTables.length}`
    );
  }

  const actualById = new Map(actualTables.map((table) => [table.tableId, table]));
  for (const expected of expectedTables) {
    const actual = actualById.get(expected.tableId);
    if (!actual) {
      failures.push(`${actualLabel} missing tableId=${expected.tableId}`);
      continue;
    }
    for (const field of ['tableId', 'title', 'sectionId', 'afterBlockId', 'anchorText']) {
      const actualValue = String(actual[field] ?? '');
      const expectedValue = String(expected[field] ?? '');
      if (actualValue !== expectedValue) {
        failures.push(
          `${actualLabel}.${expected.tableId}.${field}=${displayValue(actualValue)} does not match ${expectedLabel}.${expected.tableId}.${field}=${displayValue(expectedValue)}`
        );
      }
    }
  }
  return failures;
}

function templateTableConsistencyFailures({ actualTables, expectedTables }) {
  const failures = [];
  const actualById = new Map(actualTables.map((table) => [table.tableId, table]));
  for (const expected of expectedTables) {
    const actual = actualById.get(expected.tableId);
    if (!actual) {
      failures.push(`render-plan.json templateData.tables missing tableId=${expected.tableId}`);
      continue;
    }
    const actualTitle = String(actual.title ?? '');
    const expectedTitle = String(expected.title ?? '');
    if (actualTitle !== expectedTitle) {
      failures.push(
        `render-plan.json templateData.tables.${expected.tableId}.title=${displayValue(actualTitle)} does not match source-package.json tables.${expected.tableId}.title=${displayValue(expectedTitle)}`
      );
    }
    const actualSectionId = String(actual.metadata?.sectionId ?? '');
    const expectedSectionId = String(expected.sectionId ?? '');
    if (actualSectionId !== expectedSectionId) {
      failures.push(
        `render-plan.json templateData.tables.${expected.tableId}.metadata.sectionId=${displayValue(actualSectionId)} does not match source-package.json tables.${expected.tableId}.sectionId=${displayValue(expectedSectionId)}`
      );
    }
  }
  return failures;
}

function templateSectionBlockConsistencyFailures({ actualSections, sourcePackage, renderPlan }) {
  const failures = [];
  const actualById = new Map((actualSections || []).map((section) => [section.sectionId, section]));
  const tableIds = new Set((renderPlan?.templateData?.tables || []).map((table) => table.tableId));
  const figureIds = new Set(
    (renderPlan?.templateData?.images || [])
      .filter((image) => image.metadata?.sourceType === 'figure')
      .map((image) => image.figureId)
  );
  const sourceImagesById = new Map(
    (renderPlan?.templateData?.images || [])
      .filter((image) => image.metadata?.sourceType === 'image')
      .map((image) => [image.metadata?.sourceImageId || image.metadata?.imageId || '', image])
  );

  for (const section of sourcePackage?.sections || []) {
    const actualSection = actualById.get(section.sectionId);
    if (!actualSection) {
      failures.push(`render-plan.json templateData.sections missing sectionId=${section.sectionId}`);
      continue;
    }

    const expectedBlocks = (sourcePackage?.blocks || [])
      .filter((block) => block.sectionId === section.sectionId)
      .map((block) => expectedTemplateBlockFromSource({ block, tableIds, figureIds, sourceImagesById }))
      .filter(Boolean);
    const actualBlocks = Array.isArray(actualSection.blocks) ? actualSection.blocks : [];
    if (actualBlocks.length !== expectedBlocks.length) {
      failures.push(
        `render-plan.json templateData.sections.${section.sectionId}.blocks count=${actualBlocks.length} does not match source-package.json blocks sectionId=${section.sectionId} count=${expectedBlocks.length}`
      );
    }

    const maxLength = Math.max(actualBlocks.length, expectedBlocks.length);
    for (let index = 0; index < maxLength; index += 1) {
      const actual = actualBlocks[index];
      const expected = expectedBlocks[index];
      if (!actual || !expected) {
        continue;
      }
      for (const field of Object.keys(expected)) {
        const actualValue = String(actual[field] ?? '');
        const expectedValue = String(expected[field] ?? '');
        if (actualValue !== expectedValue) {
          failures.push(
            `render-plan.json templateData.sections.${section.sectionId}.blocks[${index}].${field}=${displayValue(actualValue)} does not match source-package.json blocks.${expected.blockId}.${field}=${displayValue(expectedValue)}`
          );
        }
      }
    }
  }

  return failures;
}

function expectedTemplateBlockFromSource({ block, tableIds, figureIds, sourceImagesById }) {
  const tableId = block.metadata?.tableId;
  if (tableId && tableIds.has(tableId)) {
    return {
      type: 'table',
      blockId: block.id,
      tableId,
      anchor: block.anchorText || tableId,
    };
  }

  const figureId = block.metadata?.figureId;
  if (figureId && figureIds.has(figureId)) {
    return {
      type: 'figure',
      blockId: block.id,
      figureId,
      anchor: block.anchorText || figureId,
    };
  }

  const imageId = block.metadata?.imageId;
  if (imageId && sourceImagesById.has(imageId)) {
    const plannedImage = sourceImagesById.get(imageId);
    return {
      type: 'figure',
      blockId: block.id,
      figureId: plannedImage.figureId,
      sourceImageId: imageId,
      title: block.caption || block.text || imageId,
      anchor: block.anchorText || imageId,
    };
  }

  if (block.type === 'paragraph' || block.type === 'heading') {
    return {
      type: 'paragraph',
      blockId: block.id,
      text: block.text || '',
    };
  }

  return null;
}

function sectionListConsistencyFailures({
  actualSections,
  expectedSections,
  actualLabel,
  expectedLabel,
  compareBlockIds = false,
}) {
  const failures = [];
  if (actualSections.length !== expectedSections.length) {
    failures.push(
      `${actualLabel} count=${actualSections.length} does not match ${expectedLabel} count=${expectedSections.length}`
    );
  }

  const maxLength = Math.max(actualSections.length, expectedSections.length);
  for (let index = 0; index < maxLength; index += 1) {
    const actual = actualSections[index];
    const expected = expectedSections[index];
    if (!actual || !expected) {
      continue;
    }
    for (const field of ['sectionId', 'title', 'level']) {
      const actualValue = String(actual[field] ?? '');
      const expectedValue = String(expected[field] ?? '');
      if (actualValue !== expectedValue) {
        failures.push(
          `${actualLabel}[${index}].${field}=${displayValue(actualValue)} does not match ${expectedLabel}[${index}].${field}=${displayValue(expectedValue)}`
        );
      }
    }
    if (compareBlockIds && !stringArraysEqual(actual.blockIds || [], expected.blockIds || [])) {
      failures.push(
        `${actualLabel}[${index}].blockIds=${displayValue((actual.blockIds || []).join('|'))} does not match ${expectedLabel}[${index}].blockIds=${displayValue((expected.blockIds || []).join('|'))}`
      );
    }
  }
  return failures;
}

function stringArraysEqual(actual, expected) {
  if (actual.length !== expected.length) {
    return false;
  }
  return actual.every((item, index) => String(item) === String(expected[index]));
}

function normalizeComparableRelativePath(value) {
  return String(value || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\.\//, '')
    .replace(/\/+/g, '/');
}

function displayValue(value) {
  return value === '' ? 'missing' : value;
}

function addDeliveryManifestFilesCheck({ addCheck, deliveryDir, deliveryPackage, jobManifest }) {
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
  const expectedOriginalSource = expectedOriginalSourcePath(jobManifest?.sourceRef);
  if (expectedOriginalSource && originalSource && originalSource !== expectedOriginalSource) {
    failures.push(
      `delivery-package.json files.originalSource must be ${expectedOriginalSource}, got ${files.originalSource || ''}`
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

function expectedOriginalSourcePath(sourceRef = {}) {
  if (!sourceRef || typeof sourceRef !== 'object') {
    return '';
  }
  return `source/original/${originalSourceFileName(sourceRef)}`;
}

function addWpsVisualCheck({ addCheck, deliveryDir, qualityReport, renderPlan, fallbackStatus }) {
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
    if (status !== 'failed') {
      const visualChecksValidation = validateWpsVisualChecks({
        recordedCheck,
        renderPlan,
        status: recordedStatus,
      });
      if (!visualChecksValidation.ok) {
        status = 'failed';
        message = visualChecksValidation.message;
      }
    }
    if (status !== 'failed') {
      const visualEvidenceValidation = validateWpsVisualEvidence({
        deliveryDir,
        recordedCheck,
        status: recordedStatus,
      });
      if (!visualEvidenceValidation.ok) {
        status = 'failed';
        message = visualEvidenceValidation.message;
      }
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

function validateWpsVisualChecks({ recordedCheck, renderPlan, status }) {
  if (!['passed', 'passed_with_warnings'].includes(status)) {
    return { ok: true };
  }
  const provided = new Set(normalizeVisualChecks(recordedCheck?.visualChecks || []));
  const required = requiredWpsVisualChecks(renderPlan);
  const missing = required.filter((check) => !provided.has(check));
  if (missing.length > 0) {
    return {
      ok: false,
      message: `WPS/Word visual acceptance is missing required visual checks: ${missing.join(', ')}.`,
    };
  }
  return { ok: true };
}

function validateWpsVisualEvidence({ deliveryDir, recordedCheck, status }) {
  if (!['passed', 'passed_with_warnings'].includes(status)) {
    return { ok: true };
  }
  const evidence = Array.isArray(recordedCheck?.visualEvidence) ? recordedCheck.visualEvidence : [];
  if (evidence.length === 0) {
    return {
      ok: false,
      message: 'WPS/Word visual acceptance is missing visual evidence files.',
    };
  }

  for (const item of evidence) {
    const relativePath = normalizeRelativePackagePath(item?.path);
    const expectedHash = String(item?.sha256 || '').trim();
    if (!relativePath || !relativePath.startsWith('evidence/wps-visual/')) {
      return {
        ok: false,
        message: `WPS/Word visual evidence path must be inside evidence/wps-visual/: ${item?.path || ''}`,
      };
    }
    if (!expectedHash) {
      return {
        ok: false,
        message: `WPS/Word visual evidence sha256 is required: ${relativePath}`,
      };
    }
    const evidencePath = path.join(deliveryDir, relativePath);
    if (!fs.existsSync(evidencePath) || !fs.statSync(evidencePath).isFile()) {
      return {
        ok: false,
        message: `WPS/Word visual evidence file is missing: ${relativePath}`,
      };
    }
    const actualHash = sha256File(evidencePath);
    if (actualHash !== expectedHash) {
      return {
        ok: false,
        message: `WPS/Word visual evidence sha256 mismatch for ${relativePath}: expected ${expectedHash}, got ${actualHash}.`,
      };
    }
  }
  return { ok: true };
}

function requiredWpsVisualChecks(renderPlan) {
  const required = ['document_opened', 'layout_reviewed', 'content_order_reviewed'];
  if ((renderPlan?.templateData?.images || renderPlan?.figures || []).length > 0) {
    required.push('figures_reviewed');
  }
  if ((renderPlan?.templateData?.tables || renderPlan?.tables || []).length > 0) {
    required.push('tables_reviewed');
  }
  return required;
}

function normalizeVisualChecks(visualChecks) {
  return [...new Set((Array.isArray(visualChecks) ? visualChecks : [visualChecks])
    .map((item) => String(item || '').trim())
    .filter(Boolean))];
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
  for (const key of ['reviewedAt', 'reviewedBy', 'documentSha256', 'visualChecks', 'visualEvidence']) {
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

function addSourceOriginalCheck({ addCheck, deliveryDir, jobManifest, sourcePackage }) {
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
  const sourceCopyFailures = sourceMarkdownCopyFailures({
    deliveryDir,
    originalSourcePath,
    sourcePackage,
    sourceRef,
  });
  if (sourceCopyFailures.length > 0) {
    addCheck('source_original', 'failed', sourceCopyFailures.join('; '));
    return;
  }
  addCheck('source_original', 'passed');
}

function sourceMarkdownCopyFailures({ deliveryDir, originalSourcePath, sourcePackage, sourceRef }) {
  const sourceType = String(sourcePackage?.sourceType || sourceRef?.type || '').trim();
  if (sourceType !== 'markdown') {
    return [];
  }

  const sourcePath = path.join(deliveryDir, 'source.md');
  if (!fs.existsSync(sourcePath)) {
    return ['source.md is missing.'];
  }

  const sourceCopy = fs.readFileSync(sourcePath);
  const originalSource = fs.readFileSync(originalSourcePath);
  if (!sourceCopy.equals(originalSource)) {
    return ['source.md no longer matches original markdown source copy.'];
  }
  return [];
}

function addSourceReplayCheck({ addCheck, deliveryDir, sourcePackage, jobManifest }) {
  if (!sourcePackage || typeof sourcePackage !== 'object') {
    addCheck('source_replay', 'failed', 'source-package.json is required for original source replay validation.');
    return;
  }

  const sourceRef = jobManifest?.sourceRef || sourcePackage.sourceRef || {};
  const sourceType = String(sourcePackage.sourceType || sourceRef.type || '').trim();
  const originalSourcePath = path.join(deliveryDir, expectedOriginalSourcePath(sourceRef));
  if (!fs.existsSync(originalSourcePath)) {
    addCheck('source_replay', 'failed', `Original source copy is missing for replay: ${path.relative(deliveryDir, originalSourcePath)}`);
    return;
  }

  let replayedSourcePackage;
  try {
    const sourceBuffer = fs.readFileSync(originalSourcePath);
    replayedSourcePackage = replayOriginalSourcePackage({
      sourceType,
      sourcePath: sourcePackage.sourceRef?.path || sourceRef.path,
      sourceBuffer,
    });
  } catch (error) {
    addCheck('source_replay', 'failed', `Original source replay failed: ${error.message}`);
    return;
  }

  const failures = sourceReplayFailures({
    actual: sourcePackage,
    expected: replayedSourcePackage,
  });
  if (failures.length > 0) {
    addCheck(
      'source_replay',
      'failed',
      `source-package.json no longer matches original source replay: ${failures.join('; ')}`
    );
    return;
  }

  addCheck('source_replay', 'passed');
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
  const untrackedAssetFiles = untrackedDeliveryAssetFiles({ deliveryDir, assetPackage, renderPlan });
  if (untrackedAssetFiles.length > 0) {
    addCheck(
      'image_coverage',
      'failed',
      `Untracked delivery asset files are not declared in asset-package.json or render-plan.json: ${untrackedAssetFiles.join(', ')}`
    );
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

function untrackedDeliveryAssetFiles({ deliveryDir, assetPackage, renderPlan }) {
  const assetsDir = path.join(deliveryDir, 'assets');
  if (!fs.existsSync(assetsDir)) {
    return [];
  }
  const allowedPaths = declaredDeliveryAssetPathSet({ assetPackage, renderPlan });
  return listPackageFiles(assetsDir, 'assets')
    .filter((filePath) => !allowedPaths.has(filePath));
}

function declaredDeliveryAssetPathSet({ assetPackage, renderPlan }) {
  const values = [];
  for (const image of renderPlan?.templateData?.images || []) {
    values.push(image.path);
  }
  for (const figure of assetPackage?.figures || []) {
    values.push(figure.displayPath, figure.editable?.sourcePath);
  }
  for (const image of assetPackage?.images || []) {
    values.push(image.displayPath, image.sourcePath);
  }
  return new Set(
    values
      .map(normalizeRelativePackagePath)
      .filter((filePath) => filePath && filePath.startsWith('assets/'))
  );
}

function listPackageFiles(rootDir, packagePrefix) {
  const files = [];
  for (const entry of fs.readdirSync(rootDir, { withFileTypes: true })) {
    const absolutePath = path.join(rootDir, entry.name);
    const packagePath = `${packagePrefix}/${entry.name}`;
    if (entry.isDirectory()) {
      files.push(...listPackageFiles(absolutePath, packagePath));
      continue;
    }
    files.push(packagePath.replaceAll(path.sep, '/'));
  }
  return files.sort();
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

function addTableContentCheck({ addCheck, documentXml, sourcePackage, assetPackage, renderPlan }) {
  if (!sourcePackage || !assetPackage || !renderPlan) {
    addCheck(
      'table_content',
      'failed',
      'source-package.json, asset-package.json and render-plan.json are required for table content validation.'
    );
    return;
  }

  const tables = sourcePackage.tables || [];
  if (tables.length === 0) {
    addCheck('table_content', 'passed_with_warnings', 'No tables were present in source-package.json.');
    return;
  }

  const failures = tableContentFailures({ documentXml, sourcePackage, assetPackage, renderPlan });
  if (failures.length > 0) {
    addCheck(
      'table_content',
      'failed',
      `DOCX table content does not match source-package tables: ${failures.join(', ')}`
    );
    return;
  }

  addCheck('table_content', 'passed');
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

function addImageInstructionsCheck({ addCheck, deliveryDir, assetPackage }) {
  if (!assetPackage) {
    addCheck('image_instructions', 'failed', 'asset-package.json is required for image instruction validation.');
    return;
  }

  const instructionsPath = path.join(deliveryDir, 'README-图片调整说明.md');
  if (!fs.existsSync(instructionsPath)) {
    addCheck('image_instructions', 'failed', 'README-图片调整说明.md is missing.');
    return;
  }

  const instructions = fs.readFileSync(instructionsPath, 'utf8');
  const failures = [];
  for (const figure of assetPackage.figures || []) {
    failures.push(...missingInstructionTokens({
      assetLabel: `figure ${figure.figureId || 'missing'}`,
      instructions,
      tokens: [
        figure.figureId,
        figure.displayPath,
        figure.editable?.sourcePath,
        figure.caption,
      ],
    }));
  }
  for (const image of assetPackage.images || []) {
    failures.push(...missingInstructionTokens({
      assetLabel: `image ${image.imageId || 'missing'}`,
      instructions,
      tokens: [
        image.imageId,
        image.displayPath,
        image.sourcePath,
        image.caption,
      ],
    }));
  }
  const missingEditableFiles = missingEditableInstructionFiles({ deliveryDir, instructions });
  if (missingEditableFiles.length > 0) {
    failures.push(`missing editable instruction files: ${missingEditableFiles.join(', ')}`);
  }

  if (failures.length > 0) {
    addCheck(
      'image_instructions',
      'failed',
      `README-图片调整说明.md is missing editable asset references: ${failures.join('; ')}`
    );
    return;
  }

  addCheck('image_instructions', 'passed');
}

function missingEditableInstructionFiles({ deliveryDir, instructions }) {
  return [...String(instructions || '').matchAll(/可编辑\/原始文件: `([^`]+)`/g)]
    .map((match) => match[1])
    .filter(Boolean)
    .filter((relativePath) => {
      const normalizedPath = normalizeRelativePackagePath(relativePath);
      if (!normalizedPath) {
        return true;
      }
      const filePath = path.join(deliveryDir, normalizedPath);
      return !fs.existsSync(filePath) || !fs.statSync(filePath).isFile();
    });
}

function missingInstructionTokens({ assetLabel, instructions, tokens }) {
  const failures = [];
  for (const token of tokens) {
    const value = String(token || '').trim();
    if (value && !instructions.includes(value)) {
      failures.push(`${assetLabel} missing ${value}`);
    }
  }
  return failures;
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

function tableContentFailures({ documentXml, sourcePackage, assetPackage, renderPlan }) {
  const failures = [];
  const sourceBlocksByTableId = new Map(
    (sourcePackage.blocks || [])
      .filter((block) => block.metadata?.tableId)
      .map((block) => [block.metadata.tableId, block])
  );
  const assetTablesById = new Map((assetPackage.tables || []).map((table) => [table.tableId, table]));
  const templateTablesById = new Map((renderPlan.templateData?.tables || []).map((table) => [table.tableId, table]));

  for (const sourceTable of sourcePackage.tables || []) {
    const tableId = String(sourceTable?.tableId || '').trim();
    if (!tableId) {
      failures.push('source-package.json tables has table without tableId');
      continue;
    }
    const expected = sourceTableContent(sourceTable);

    const sourceBlock = sourceBlocksByTableId.get(tableId);
    if (sourceBlock) {
      compareTableContent({
        failures,
        actual: sourceBlockContent(sourceBlock),
        expected,
        actualLabel: `source-package.json blocks.${sourceBlock.id}`,
        expectedLabel: `source-package.json tables.${tableId}`,
      });
    }

    const assetTable = assetTablesById.get(tableId);
    if (!assetTable) {
      failures.push(`asset-package.json tables missing tableId=${tableId}`);
    } else {
      compareTableContent({
        failures,
        actual: sourceTableContent(assetTable),
        expected,
        actualLabel: `asset-package.json tables.${tableId}`,
        expectedLabel: `source-package.json tables.${tableId}`,
      });
    }

    const templateTable = templateTablesById.get(tableId);
    if (!templateTable) {
      failures.push(`render-plan.json templateData.tables missing tableId=${tableId}`);
    } else {
      compareTableContent({
        failures,
        actual: templateTableContent(templateTable),
        expected: templateTableContentFromSource(sourceTable),
        actualLabel: `render-plan.json templateData.tables.${tableId}`,
        expectedLabel: `source-package.json tables.${tableId}`,
      });
    }

    const docxFailure = docxTableContentFailure({ documentXml, tableId, expected });
    if (docxFailure) {
      failures.push(docxFailure);
    }
  }

  return failures;
}

function compareTableContent({ failures, actual, expected, actualLabel, expectedLabel }) {
  if (!stringArraysEqual(actual.headers, expected.headers)) {
    failures.push(
      `${actualLabel}.headers=${displayValue(actual.headers.join('|'))} does not match ${expectedLabel}.headers=${displayValue(expected.headers.join('|'))}`
    );
  }
  if (!tableRowsEqual(actual.rows, expected.rows)) {
    failures.push(
      `${actualLabel}.rows=${displayValue(tableRowsDisplay(actual.rows))} does not match ${expectedLabel}.rows=${displayValue(tableRowsDisplay(expected.rows))}`
    );
  }
}

function sourceBlockContent(block) {
  return sourceTableContent(block.content || {});
}

function sourceTableContent(table) {
  const headers = normalizeTableHeaders(table.headers || []);
  return {
    headers,
    rows: normalizeSourceRows(table.rows || [], headers),
  };
}

function templateTableContentFromSource(table) {
  const headers = normalizeTableHeaders(table.headers || []);
  return {
    headers,
    rows: normalizeSourceRows(table.rows || [], headers),
  };
}

function templateTableContent(table) {
  return {
    headers: valuesByTemplateColumns(table.headers || {}),
    rows: (table.rows || []).map((row) => valuesByTemplateColumns(row || {})),
  };
}

function normalizeTableHeaders(headers) {
  return (headers || []).map((header) => normalizeTableCell(header));
}

function normalizeSourceRows(rows, headers) {
  return (rows || []).map((row) => {
    if (Array.isArray(row)) {
      return row.map((cell) => normalizeTableCell(cell));
    }
    if (row && typeof row === 'object') {
      if (headers.length > 0) {
        return headers.map((header, index) =>
          normalizeTableCell(row[`c${index + 1}`] ?? row[header] ?? '')
        );
      }
      return Object.keys(row)
        .sort()
        .map((key) => normalizeTableCell(row[key]));
    }
    return [normalizeTableCell(row)];
  });
}

function valuesByTemplateColumns(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return [];
  }
  return Object.keys(value)
    .filter((key) => /^c\d+$/.test(key))
    .sort((left, right) => Number(left.slice(1)) - Number(right.slice(1)))
    .map((key) => normalizeTableCell(value[key]));
}

function tableRowsEqual(actual, expected) {
  if (actual.length !== expected.length) {
    return false;
  }
  return actual.every((row, index) => stringArraysEqual(row, expected[index] || []));
}

function tableRowsDisplay(rows) {
  return (rows || []).map((row) => (row || []).join('|')).join(' / ');
}

function docxTableContentFailure({ documentXml, tableId, expected }) {
  if (!documentXml) {
    return `${tableId} cannot inspect DOCX table content without word/document.xml`;
  }
  const tableXml = docxTableXmlForTableId(documentXml, tableId);
  if (!tableXml) {
    return `${tableId} missing DOCX table body for content validation`;
  }
  const expectedCells = [...expected.headers, ...expected.rows.flat()].filter(Boolean);
  const actualCells = docxTableCellTexts(tableXml);
  if (!containsOrderedCells(actualCells, expectedCells)) {
    return `${tableId} DOCX table cells=${displayValue(actualCells.join('|'))} do not contain expected source-package.json tables cells=${displayValue(expectedCells.join('|'))}`;
  }
  return '';
}

function docxTableXmlForTableId(documentXml, tableId) {
  const caption = tableCaptionParagraphs(paragraphRanges(documentXml)).get(tableId);
  if (!caption) {
    return '';
  }
  const afterCaption = String(documentXml || '').slice(caption.end);
  const match = afterCaption.match(/^\s*(<w:tbl\b[\s\S]*?<\/w:tbl>)/);
  return match ? match[1] : '';
}

function docxTableCellTexts(tableXml) {
  return [...String(tableXml || '').matchAll(/<w:tc\b[\s\S]*?<\/w:tc>/g)]
    .map((match) => normalizeTableCell(paragraphText(match[0])))
    .filter(Boolean);
}

function containsOrderedCells(actualCells, expectedCells) {
  let actualIndex = 0;
  for (const expected of expectedCells) {
    while (actualIndex < actualCells.length && actualCells[actualIndex] !== expected) {
      actualIndex += 1;
    }
    if (actualIndex >= actualCells.length) {
      return false;
    }
    actualIndex += 1;
  }
  return true;
}

function normalizeTableCell(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
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
  const sections = renderPlan.templateData?.sections || renderPlan.sections || [];
  const sectionAnchors = resolveSectionAnchors(paragraphs, sections);

  for (let sectionIndex = 0; sectionIndex < sections.length; sectionIndex += 1) {
    const section = sections[sectionIndex];
    let previous = {
      blockId: '',
      position: sectionAnchors[sectionIndex]?.end ?? sectionInsertionIndexForOrder(documentXml, section.title),
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
  const sectionAnchors = resolveSectionAnchors(paragraphs, sections);
  const anchors = sectionAnchors
    .map((anchor, index) => {
      const sectionId = sections[index]?.sectionId;
      return anchor && sectionId ? { sectionId, start: anchor.end } : null;
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
  const checks = [
    ...CHECK_IDS.map((id) => checksById.get(id) || { id, status: 'failed' }),
    ...[...checksById.entries()]
      .filter(([id]) => !CHECK_IDS.includes(id))
      .map(([, check]) => check),
  ];
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
  return readZipEntriesFromBuffer(fs.readFileSync(filePath));
}

module.exports = { validateDeliveryPackage };
