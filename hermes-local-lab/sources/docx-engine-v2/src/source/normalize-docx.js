const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const yauzl = require('yauzl');

async function normalizeDocxSource({ sourcePath } = {}) {
  if (!sourcePath) {
    throw new Error('sourcePath is required for DOCX source normalization.');
  }

  const [sourceBuffer, zipEntries] = await Promise.all([fs.readFile(sourcePath), readZip(sourcePath)]);
  const documentXml = zipEntries.get('word/document.xml')?.toString('utf8') || '';
  const relationshipsXml = zipEntries.get('word/_rels/document.xml.rels')?.toString('utf8') || '';
  const paragraphs = extractParagraphs(documentXml);
  const sections = [];
  let currentSection = null;

  const blocks = paragraphs.map((paragraph, index) => {
    const isTitle = index === 0;
    const isSectionHeading = !isTitle && looksLikeSectionHeading(paragraph);
    const block = {
      id: nextId('block', index + 1),
      type: isTitle || isSectionHeading ? 'heading' : 'paragraph',
      text: paragraph,
      content: { text: paragraph },
      level: isTitle ? 1 : isSectionHeading ? 2 : currentSection?.level || 1,
      sectionId: currentSection?.sectionId || '',
      sectionTitle: currentSection?.title || '',
      anchorText: anchorText(paragraph),
      path: `word/document.xml#p-${index + 1}`,
      caption: '',
      metadata: { sourceIndex: index },
    };

    if (isSectionHeading) {
      currentSection = {
        sectionId: nextId('sec', sections.length + 1),
        title: paragraph,
        level: 2,
        blockIds: [block.id],
        metadata: { sourceIndex: index },
      };
      sections.push(currentSection);
      block.sectionId = currentSection.sectionId;
      block.sectionTitle = currentSection.title;
      block.level = currentSection.level;
    } else if (currentSection) {
      block.sectionId = currentSection.sectionId;
      block.sectionTitle = currentSection.title;
      currentSection.blockIds.push(block.id);
    }

    return block;
  });

  return {
    schemaVersion: 'docx-engine-v2/source-package',
    sourceType: 'docx',
    sourceRef: {
      type: 'docx',
      path: sourcePath,
      sha256: sha256(sourceBuffer),
    },
    title: paragraphs[0] || path.basename(sourcePath),
    sections,
    blocks,
    tables: [],
    figures: [],
    images: [],
    embeddedMedia: extractEmbeddedMedia(zipEntries, relationshipsXml),
    warnings: [],
  };
}

function readZip(sourcePath) {
  return new Promise((resolve, reject) => {
    yauzl.open(sourcePath, { lazyEntries: true }, (openError, zipfile) => {
      if (openError) {
        reject(openError);
        return;
      }

      const entries = new Map();
      zipfile.readEntry();
      zipfile.on('entry', (entry) => {
        zipfile.openReadStream(entry, (streamError, readStream) => {
          if (streamError) {
            reject(streamError);
            return;
          }

          const chunks = [];
          readStream.on('data', (chunk) => chunks.push(chunk));
          readStream.on('error', reject);
          readStream.on('end', () => {
            entries.set(entry.fileName, Buffer.concat(chunks));
            zipfile.readEntry();
          });
        });
      });
      zipfile.on('error', reject);
      zipfile.on('end', () => resolve(entries));
    });
  });
}

function extractParagraphs(documentXml) {
  return [...documentXml.matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)]
    .map((match) =>
      [...match[0].matchAll(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g)]
        .map((textMatch) => decodeXml(textMatch[1]))
        .join('')
        .trim()
    )
    .filter(Boolean);
}

function extractEmbeddedMedia(zipEntries, relationshipsXml) {
  const relationshipByTarget = new Map(
    [...relationshipsXml.matchAll(/<Relationship\b([^>]*)\/?>/g)].map((match) => {
      const attributes = parseAttributes(match[1]);
      return [normalizeRelationshipTarget(attributes.Target || ''), attributes];
    })
  );

  return [...zipEntries.entries()]
    .filter(([entryPath]) => entryPath.startsWith('word/media/'))
    .map(([entryPath, buffer], index) => {
      const relationship = relationshipByTarget.get(entryPath) || {};
      return {
        mediaId: nextId('media', index + 1),
        path: entryPath,
        fileName: path.basename(entryPath),
        relationshipId: relationship.Id || '',
        relationshipType: relationship.Type || '',
        contentType: contentTypeFor(entryPath),
        size: buffer.length,
        sha256: sha256(buffer),
      };
    });
}

function normalizeRelationshipTarget(target) {
  if (!target) {
    return '';
  }
  return target.startsWith('word/') ? target : `word/${target.replace(/^\//, '')}`;
}

function parseAttributes(value) {
  return Object.fromEntries(
    [...value.matchAll(/\s+([A-Za-z_:][\w:.-]*)="([^"]*)"/g)].map((match) => [
      match[1],
      decodeXml(match[2]),
    ])
  );
}

function looksLikeSectionHeading(value) {
  return /^([一二三四五六七八九十]+、|第[一二三四五六七八九十\d]+[章节])/.test(value);
}

function contentTypeFor(entryPath) {
  const extension = path.extname(entryPath).toLowerCase();
  if (extension === '.png') {
    return 'image/png';
  }
  if (extension === '.jpg' || extension === '.jpeg') {
    return 'image/jpeg';
  }
  if (extension === '.svg') {
    return 'image/svg+xml';
  }
  return 'application/octet-stream';
}

function decodeXml(value) {
  return String(value || '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
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

module.exports = { normalizeDocxSource };
