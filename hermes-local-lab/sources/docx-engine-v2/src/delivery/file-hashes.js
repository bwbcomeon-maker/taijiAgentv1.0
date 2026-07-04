const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const HASHED_FILE_ROLES = [
  'document',
  'source',
  'sourcePackage',
  'originalSource',
  'assetPackage',
  'jobManifest',
  'templateManifest',
  'renderPlan',
  'qualityReport',
  'imageInstructions',
];
const OPTIONAL_HASHED_FILE_ROLES = [
  'replayReport',
];

function buildDeliveryFileSha256({ deliveryDir, files, roles = HASHED_FILE_ROLES } = {}) {
  const hashes = {};
  for (const role of roles) {
    const relativePath = normalizeRelativePackagePath(files?.[role]);
    if (!relativePath) {
      continue;
    }
    const filePath = path.join(deliveryDir, relativePath);
    if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
      hashes[role] = sha256File(filePath);
    }
  }
  return hashes;
}

function refreshDeliveryPackageFileHashes({ deliveryDir, roles = HASHED_FILE_ROLES } = {}) {
  const manifestPath = path.join(deliveryDir, 'delivery-package.json');
  const deliveryPackage = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const nextHashes = buildDeliveryFileSha256({
    deliveryDir,
    files: deliveryPackage.files,
    roles,
  });
  deliveryPackage.fileSha256 = {
    ...(deliveryPackage.fileSha256 || {}),
    ...nextHashes,
  };
  if (nextHashes.document) {
    deliveryPackage.documentSha256 = nextHashes.document;
  }
  if (nextHashes.source) {
    deliveryPackage.sourceSha256 = nextHashes.source;
  }
  writeJson(manifestPath, deliveryPackage);
  return deliveryPackage;
}

function deliveryFileHashFailures({ deliveryDir, files, fileSha256 }) {
  const failures = [];
  if (!fileSha256 || typeof fileSha256 !== 'object') {
    return ['delivery-package.json fileSha256 is required.'];
  }

  for (const role of HASHED_FILE_ROLES) {
    failures.push(...deliveryFileHashRoleFailures({ deliveryDir, files, fileSha256, role, required: true }));
  }
  for (const role of OPTIONAL_HASHED_FILE_ROLES) {
    failures.push(...deliveryFileHashRoleFailures({ deliveryDir, files, fileSha256, role, required: false }));
  }
  return failures;
}

function deliveryFileHashRoleFailures({ deliveryDir, files, fileSha256, role, required }) {
  const failures = [];
  const relativePath = normalizeRelativePackagePath(files?.[role]);
  const expectedHash = String(fileSha256[role] || '').trim();
  const displayPath = files?.[role] || role;

  if (!relativePath && !expectedHash && !required) {
    return failures;
  }
  if (!expectedHash) {
    failures.push(`delivery-package.json fileSha256.${role} is required.`);
    return failures;
  }
  if (!relativePath) {
    failures.push(`delivery-package.json files.${role} is required.`);
    return failures;
  }

  const filePath = path.join(deliveryDir, relativePath);
  if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
    const actualHash = sha256File(filePath);
    if (actualHash !== expectedHash) {
      failures.push(`${displayPath} sha256 mismatch: expected ${expectedHash}, got ${actualHash}`);
    }
  }
  return failures;
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

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

module.exports = {
  HASHED_FILE_ROLES,
  OPTIONAL_HASHED_FILE_ROLES,
  buildDeliveryFileSha256,
  deliveryFileHashFailures,
  refreshDeliveryPackageFileHashes,
};
