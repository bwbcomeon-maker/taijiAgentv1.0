const fs = require('node:fs');

function assertVisualEvidenceFile(filePath, displayPath = filePath) {
  const type = detectVisualEvidenceType(filePath);
  if (!type) {
    throw new Error(
      `Unsupported WPS visual evidence file: ${displayPath}. Provide a PNG/JPEG image screenshot or PDF export.`
    );
  }
  return type;
}

function detectVisualEvidenceType(filePath) {
  const header = readHeader(filePath, 12);
  if (isPng(header)) {
    return 'image/png';
  }
  if (isJpeg(header)) {
    return 'image/jpeg';
  }
  if (isPdf(header)) {
    return 'application/pdf';
  }
  return '';
}

function readHeader(filePath, length) {
  const fd = fs.openSync(filePath, 'r');
  try {
    const buffer = Buffer.alloc(length);
    const bytesRead = fs.readSync(fd, buffer, 0, length, 0);
    return buffer.subarray(0, bytesRead);
  } finally {
    fs.closeSync(fd);
  }
}

function isPng(header) {
  return header.length >= 8 &&
    header[0] === 0x89 &&
    header[1] === 0x50 &&
    header[2] === 0x4e &&
    header[3] === 0x47 &&
    header[4] === 0x0d &&
    header[5] === 0x0a &&
    header[6] === 0x1a &&
    header[7] === 0x0a;
}

function isJpeg(header) {
  return header.length >= 3 &&
    header[0] === 0xff &&
    header[1] === 0xd8 &&
    header[2] === 0xff;
}

function isPdf(header) {
  return header.length >= 5 && header.subarray(0, 5).toString('ascii') === '%PDF-';
}

module.exports = {
  assertVisualEvidenceFile,
  detectVisualEvidenceType,
};
