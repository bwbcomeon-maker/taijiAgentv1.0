const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');

async function normalizeTextSource({ sourcePath = 'inline.txt', text } = {}) {
  const sourceText = text ?? (await fs.readFile(sourcePath, 'utf8'));
  const paragraphs = sourceText
    .split(/\n\s*\n|\r?\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  return {
    schemaVersion: 'docx-engine-v2/source-package',
    sourceType: 'text',
    sourceRef: {
      type: 'text',
      path: sourcePath || 'inline.txt',
      sha256: sha256(sourceText),
    },
    title: paragraphs[0] || path.basename(sourcePath || 'source.txt'),
    sections: [],
    blocks: paragraphs.map((paragraph, index) => ({
      id: nextId('block', index + 1),
      type: 'paragraph',
      text: paragraph,
      content: { text: paragraph },
      level: 1,
      sectionId: '',
      sectionTitle: '',
      anchorText: anchorText(paragraph),
      path: `blocks.${index}`,
      caption: '',
      metadata: { sourceLine: index + 1 },
    })),
    tables: [],
    figures: [],
    images: [],
    embeddedMedia: [],
    warnings: [
      {
        code: 'rich_content_missing',
        message: 'Plain text source does not contain tables, figures, images, or embedded media.',
        severity: 'warning',
      },
    ],
  };
}

function nextId(prefix, value) {
  return `${prefix}-${String(value).padStart(3, '0')}`;
}

function anchorText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 120);
}

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

module.exports = { normalizeTextSource };
