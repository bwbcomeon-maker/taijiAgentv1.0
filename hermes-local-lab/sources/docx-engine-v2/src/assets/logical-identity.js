const crypto = require('node:crypto');

function logicalAssetId({ sourceType = 'asset', sourceText = '', sourcePath = '', contentSha256 = '' } = {}) {
  const identityMaterial = contentSha256 || normalizeSourceText(sourceText) || normalizeSourcePath(sourcePath);
  return `logical-${sha256(`${sourceType}\0${identityMaterial}`).slice(0, 16)}`;
}

function occurrenceId({ logicalId, sectionKey = '', ordinal = 1 } = {}) {
  return `occurrence-${sha256(`${logicalId}\0${sectionKey}\0${ordinal}`).slice(0, 16)}`;
}

function allocateOccurrence(context, { logicalId, sectionKey = '' } = {}) {
  const key = `${logicalId}\0${sectionKey}`;
  const ordinal = (context.logicalOccurrenceCounts.get(key) || 0) + 1;
  context.logicalOccurrenceCounts.set(key, ordinal);
  return occurrenceId({ logicalId, sectionKey, ordinal });
}

function normalizeSourceText(value) {
  return String(value || '').replace(/\r\n?/g, '\n').trim();
}

function normalizeSourcePath(value) {
  return String(value || '').trim().replaceAll('\\', '/');
}

function sha256(value) {
  return crypto.createHash('sha256').update(String(value)).digest('hex');
}

module.exports = { allocateOccurrence, logicalAssetId, occurrenceId };
