const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { validateDomainObject } = require('../domain/validate');
const { refreshDeliveryPackageFileHashes } = require('../delivery/file-hashes');
const { validateDeliveryPackage } = require('./validate-delivery-package');

const ACCEPTANCE_STATUSES = new Set(['passed', 'passed_with_warnings', 'failed']);

function recordWpsVisualAcceptance({
  deliveryDir,
  status = 'passed',
  reviewedAt = new Date().toISOString(),
  reviewedBy = '',
  note = '',
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
  if (normalizedStatus !== 'failed') {
    assertAutomatedDeliveryGatesPassed(path.resolve(deliveryDir));
  }
  const report = readJson(qualityReportPath);
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
  if (wpsCheckIndex >= 0) {
    checks[wpsCheckIndex] = { ...checks[wpsCheckIndex], ...nextWpsCheck };
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
    .filter((item) => item && !/^WPS\/Word visual inspection failed/i.test(item));
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
  const report = validateDeliveryPackage({ deliveryDir });
  const failedChecks = (report.checks || []).filter(
    (check) => check.id !== 'wps_visual' && check.status === 'failed'
  );
  if (failedChecks.length === 0) {
    return;
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
