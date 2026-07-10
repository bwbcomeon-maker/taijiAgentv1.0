const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { validateDomainObject } = require('../domain/validate');
const { refreshDeliveryPackageFileHashes } = require('../delivery/file-hashes');
const { validateDeliveryPackage } = require('./validate-delivery-package');
const { assertVisualEvidenceFile, detectVisualEvidenceType } = require('./visual-evidence');

const ACCEPTANCE_STATUSES = new Set(['passed', 'passed_with_warnings', 'failed']);
const BASE_REQUIRED_VISUAL_CHECKS = ['document_opened', 'layout_reviewed', 'content_order_reviewed'];
const FIGURE_VISUAL_CHECK = 'figures_reviewed';
const TABLE_VISUAL_CHECK = 'tables_reviewed';

function recordWpsVisualAcceptance({
  deliveryDir,
  status = 'passed',
  reviewedAt = new Date().toISOString(),
  reviewedBy = '',
  note = '',
  visualChecks = [],
  evidenceFiles = [],
} = {}) {
  if (!deliveryDir) {
    throw new Error('deliveryDir is required.');
  }
  const qualityReportPath = path.join(path.resolve(deliveryDir), 'quality-report.json');
  if (!fs.existsSync(qualityReportPath)) {
    throw new Error(`quality-report.json not found: ${qualityReportPath}`);
  }
  const documentPath = path.join(path.resolve(deliveryDir), 'document.docx');
  if (!fs.existsSync(documentPath)) {
    throw new Error(`document.docx not found: ${documentPath}`);
  }
  const normalizedStatus = normalizeAcceptanceStatus(status);
  let report = null;
  let visualEvidence = [];
  if (normalizedStatus !== 'failed') {
    report = assertAutomatedDeliveryGatesPassed(path.resolve(deliveryDir));
    assertVisualChecksComplete({
      deliveryDir: path.resolve(deliveryDir),
      visualChecks,
    });
    visualEvidence = packageVisualEvidenceFiles({
      deliveryDir: path.resolve(deliveryDir),
      evidenceFiles,
    });
  }
  report = report || readJson(qualityReportPath);
  const normalizedVisualChecks = normalizeVisualChecks(visualChecks);
  const checks = Array.isArray(report.checks) ? [...report.checks] : [];
  const wpsCheckIndex = checks.findIndex((check) => check.id === 'wps_visual');
  const nextWpsCheck = {
    id: 'wps_visual',
    status: normalizedStatus,
    message: buildWpsMessage(normalizedStatus, note),
    reviewedAt,
    reviewedBy: String(reviewedBy || 'user'),
    documentSha256: sha256File(documentPath),
  };
  if (normalizedVisualChecks.length > 0) {
    nextWpsCheck.visualChecks = normalizedVisualChecks;
  }
  if (visualEvidence.length > 0) {
    nextWpsCheck.visualEvidence = visualEvidence;
  }
  if (wpsCheckIndex >= 0) {
    checks[wpsCheckIndex] = nextWpsCheck;
  } else {
    checks.push(nextWpsCheck);
  }

  const warnings = removeWpsNotVerifiedWarnings(report.warnings || []);
  if (normalizedStatus === 'passed_with_warnings' && note) {
    warnings.push(note);
  }
  const failures = removePreviousWpsFailures(report.failures || []);
  if (normalizedStatus === 'failed') {
    failures.push(buildWpsFailure(note));
  }

  const nextReport = {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: reportStatus({ checks, warnings, failures }),
    checks,
    warnings: uniqueStrings(warnings),
    failures: uniqueStrings(failures),
  };
  const validation = validateDomainObject('ValidationReport', nextReport);
  if (!validation.ok) {
    throw new Error(`ValidationReport update failed: ${JSON.stringify(validation.errors)}`);
  }
  writeJson(qualityReportPath, nextReport);
  refreshDeliveryPackageFileHashes({ deliveryDir: path.resolve(deliveryDir), roles: ['qualityReport'] });
  return {
    ok: true,
    deliveryDir: path.resolve(deliveryDir),
    qualityReportPath,
    qualityReport: nextReport,
  };
}

function normalizeAcceptanceStatus(status) {
  const normalized = String(status || '').trim();
  if (!ACCEPTANCE_STATUSES.has(normalized)) {
    throw new Error(`Invalid WPS visual status: ${normalized || ''}`);
  }
  return normalized;
}

function assertVisualChecksComplete({ deliveryDir, visualChecks }) {
  const provided = new Set(normalizeVisualChecks(visualChecks));
  const required = requiredVisualChecksForDelivery(deliveryDir);
  const missing = required.filter((check) => !provided.has(check));
  if (missing.length > 0) {
    throw new Error(`Missing WPS visual checks: ${missing.join(', ')}`);
  }
}

function requiredVisualChecksForDelivery(deliveryDir) {
  const renderPlanPath = path.join(deliveryDir, 'render-plan.json');
  const renderPlan = fs.existsSync(renderPlanPath) ? readJson(renderPlanPath) : {};
  const required = [...BASE_REQUIRED_VISUAL_CHECKS];
  if ((renderPlan.templateData?.images || renderPlan.figures || []).length > 0) {
    required.push(FIGURE_VISUAL_CHECK);
  }
  if ((renderPlan.templateData?.tables || renderPlan.tables || []).length > 0) {
    required.push(TABLE_VISUAL_CHECK);
  }
  return required;
}

function normalizeVisualChecks(visualChecks) {
  return [...new Set((Array.isArray(visualChecks) ? visualChecks : [visualChecks])
    .map((item) => String(item || '').trim())
    .filter(Boolean))];
}

function packageVisualEvidenceFiles({ deliveryDir, evidenceFiles }) {
  const normalizedFiles = normalizeEvidenceFiles(evidenceFiles);
  if (normalizedFiles.length === 0) {
    throw new Error('Missing WPS visual evidence file for passed visual acceptance.');
  }

  const evidenceDir = path.join(deliveryDir, 'evidence', 'wps-visual');
  fs.mkdirSync(evidenceDir, { recursive: true });
  const usedNames = new Set();
  return normalizedFiles.map((sourcePath) => {
    const resolvedSourcePath = path.resolve(sourcePath);
    if (!fs.existsSync(resolvedSourcePath) || !fs.statSync(resolvedSourcePath).isFile()) {
      throw new Error(`WPS visual evidence file is missing: ${sourcePath}`);
    }
    assertVisualEvidenceFile(resolvedSourcePath, sourcePath);
    const targetName = uniqueEvidenceFileName({
      evidenceDir,
      usedNames,
      fileName: safeEvidenceFileName(resolvedSourcePath),
    });
    const targetPath = path.join(evidenceDir, targetName);
    if (path.resolve(targetPath) !== resolvedSourcePath) {
      fs.copyFileSync(resolvedSourcePath, targetPath);
    }
    return {
      path: path.relative(deliveryDir, targetPath).replaceAll(path.sep, '/'),
      sha256: sha256File(targetPath),
      sizeBytes: fs.statSync(targetPath).size,
      mediaType: detectVisualEvidenceType(targetPath),
    };
  });
}

function normalizeEvidenceFiles(evidenceFiles) {
  return [...new Set((Array.isArray(evidenceFiles) ? evidenceFiles : [evidenceFiles])
    .map((item) => String(item || '').trim())
    .filter(Boolean))];
}

function safeEvidenceFileName(filePath) {
  const baseName = path.basename(filePath).replace(/[^A-Za-z0-9._-]/g, '_');
  if (!baseName || baseName === '.' || baseName === '..') {
    return 'evidence.txt';
  }
  return baseName;
}

function uniqueEvidenceFileName({ evidenceDir, usedNames, fileName }) {
  const extension = path.extname(fileName);
  const stem = fileName.slice(0, fileName.length - extension.length) || 'evidence';
  let candidate = fileName;
  let suffix = 1;
  while (usedNames.has(candidate) || fs.existsSync(path.join(evidenceDir, candidate))) {
    candidate = `${stem}-${suffix}${extension}`;
    suffix += 1;
  }
  usedNames.add(candidate);
  return candidate;
}

function buildWpsMessage(status, note) {
  const prefix = status === 'failed'
    ? 'WPS/Word visual inspection failed.'
    : status === 'passed_with_warnings'
      ? 'WPS/Word visual inspection passed with warnings.'
      : 'WPS/Word visual inspection passed.';
  const suffix = String(note || '').trim();
  return suffix ? `${prefix} ${suffix}` : prefix;
}

function buildWpsFailure(note) {
  const suffix = String(note || '').trim();
  return suffix ? `WPS/Word visual inspection failed: ${suffix}` : 'WPS/Word visual inspection failed.';
}

function removeWpsNotVerifiedWarnings(warnings) {
  return (Array.isArray(warnings) ? warnings : [])
    .map((item) => String(item || ''))
    .filter((item) => item && !/WPS(?:\/Word)? visual (?:inspection has not been performed|acceptance has not been verified)/i.test(item));
}

function removePreviousWpsFailures(failures) {
  return (Array.isArray(failures) ? failures : [])
    .map((item) => String(item || ''))
    .filter((item) => item && !/^WPS\/Word visual (?:inspection|acceptance)/i.test(item));
}

function reportStatus({ checks, warnings, failures }) {
  if ((failures || []).length || (checks || []).some((check) => check.status === 'failed')) {
    return 'failed';
  }
  if (
    (warnings || []).length ||
    (checks || []).some((check) => check.status === 'passed_with_warnings' || check.status === 'not_verified')
  ) {
    return 'passed_with_warnings';
  }
  return 'passed';
}

function uniqueStrings(items) {
  return [...new Set((items || []).map((item) => String(item || '')).filter(Boolean))];
}

function assertAutomatedDeliveryGatesPassed(deliveryDir) {
  const report = validateDeliveryPackage({
    deliveryDir,
    requireReplayReport: true,
    enforceStoredQualityReport: false,
    requireWpsVisualAcceptance: false,
  });
  const failedChecks = (report.checks || []).filter(
    (check) => check.id !== 'wps_visual' && check.status === 'failed'
  );
  if (failedChecks.length === 0) {
    return report;
  }

  const failedMessages = failedChecks
    .map((check) => check.message || check.id)
    .filter(Boolean)
    .join('; ');
  throw new Error(`Cannot record WPS visual acceptance while automated validation fails: ${failedMessages}`);
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

module.exports = { recordWpsVisualAcceptance };
