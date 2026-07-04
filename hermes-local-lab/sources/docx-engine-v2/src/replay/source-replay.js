const zlib = require('node:zlib');

const { normalizeDocxSourceFromEntries } = require('../source/normalize-docx');
const { normalizeMarkdownText } = require('../source/normalize-markdown');
const { normalizeTextContent } = require('../source/normalize-text');

function replayOriginalSourcePackage({ sourceType, sourcePath, sourceBuffer }) {
  if (sourceType === 'markdown') {
    return normalizeMarkdownText({ sourcePath, markdownText: sourceBuffer.toString('utf8') });
  }
  if (sourceType === 'text') {
    return normalizeTextContent({ sourcePath, text: sourceBuffer.toString('utf8') });
  }
  if (sourceType === 'docx') {
    return normalizeDocxSourceFromEntries({
      sourcePath,
      sourceBuffer,
      zipEntries: readZipEntriesFromBuffer(sourceBuffer),
    });
  }
  throw new Error(`Unsupported source type for replay: ${sourceType || 'missing'}`);
}

function sourceReplayFailures({ actual, expected }) {
  const failures = [];
  for (const field of ['sourceType', 'title']) {
    const actualValue = normalizeComparableJsonValue(actual?.[field]);
    const expectedValue = normalizeComparableJsonValue(expected?.[field]);
    if (actualValue !== expectedValue) {
      failures.push(
        `source-package.json ${field}=${displayValue(actualValue)} does not match original source ${field}=${displayValue(expectedValue)}`
      );
    }
  }

  for (const field of ['sourceRef', 'sections', 'blocks', 'tables', 'figures', 'images', 'embeddedMedia', 'warnings']) {
    const actualValue = normalizeComparableJsonValue(actual?.[field]);
    const expectedValue = normalizeComparableJsonValue(expected?.[field]);
    if (actualValue !== expectedValue) {
      failures.push(`source-package.json ${field} does not match original source replay`);
    }
  }
  return failures;
}

function readZipEntriesFromBuffer(buffer) {
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

function normalizeComparableJsonValue(value) {
  if (typeof value === 'string') {
    return value;
  }
  return JSON.stringify(value ?? null);
}

function displayValue(value) {
  return value === '' ? 'missing' : value;
}

module.exports = {
  readZipEntriesFromBuffer,
  replayOriginalSourcePackage,
  sourceReplayFailures,
};
