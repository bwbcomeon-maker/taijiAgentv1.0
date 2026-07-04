const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const yauzl = require('yauzl');

async function normalizeDocxSource({ sourcePath } = {}) {
  if (!sourcePath) {
    throw new Error('sourcePath is required for DOCX source normalization.');
  }

  const [sourceBuffer, zipEntries] = await Promise.all([fs.readFile(sourcePath), readZip(sourcePath)]);
  if (!zipEntries.has('word/document.xml')) {
    throw new Error('DOCX source is missing word/document.xml.');
  }

  const documentXml = zipEntries.get('word/document.xml').toString('utf8');
  const relationshipsXml = zipEntries.get('word/_rels/document.xml.rels')?.toString('utf8') || '';
  const embeddedMedia = extractEmbeddedMedia(zipEntries, relationshipsXml);
  const { title, sections, blocks, tables } = extractBodyStructure(documentXml);

  return {
    schemaVersion: 'docx-engine-v2/source-package',
    sourceType: 'docx',
    sourceRef: {
      type: 'docx',
      path: sourcePath,
      sha256: sha256(sourceBuffer),
    },
    title: title || path.basename(sourcePath),
    sections,
    blocks,
    tables,
    figures: extractFigureMarkers(documentXml, embeddedMedia, blocks),
    images: [],
    embeddedMedia,
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
      let settled = false;

      const fail = (error) => {
        if (settled) {
          return;
        }
        settled = true;
        try {
          zipfile.close();
        } catch (_closeError) {
          // Best effort only; preserve the original zip error for the caller.
        }
        reject(error);
      };

      const finish = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(entries);
      };

      zipfile.on('entry', (entry) => {
        if (settled) {
          return;
        }
        if (entry.fileName.endsWith('/')) {
          zipfile.readEntry();
          return;
        }

        zipfile.openReadStream(entry, (streamError, readStream) => {
          if (streamError) {
            fail(streamError);
            return;
          }

          const chunks = [];
          readStream.on('data', (chunk) => chunks.push(chunk));
          readStream.on('error', fail);
          readStream.on('end', () => {
            if (settled) {
              return;
            }
            entries.set(entry.fileName, Buffer.concat(chunks));
            zipfile.readEntry();
          });
        });
      });
      zipfile.on('error', fail);
      zipfile.on('end', finish);
      zipfile.readEntry();
    });
  });
}

function extractBodyStructure(documentXml) {
  const sections = [];
  const blocks = [];
  const tables = [];
  let title = '';
  let currentSection = null;

  for (const match of documentXml.matchAll(/<w:(p|tbl)\b[\s\S]*?<\/w:\1>/g)) {
    const elementType = match[1];
    const xml = match[0];
    const sourceIndex = blocks.length + tables.length;

    if (elementType === 'p') {
      const paragraph = extractText(xml);
      if (!paragraph) {
        continue;
      }

      const isTitle = !title;
      const isSectionHeading = !isTitle && looksLikeSectionHeading(paragraph);
      const block = {
        id: nextId('block', blocks.length + 1),
        type: isTitle || isSectionHeading ? 'heading' : 'paragraph',
        text: paragraph,
        content: { text: paragraph },
        level: isTitle ? 1 : isSectionHeading ? 2 : currentSection?.level || 1,
        sectionId: currentSection?.sectionId || '',
        sectionTitle: currentSection?.title || '',
        anchorText: anchorText(paragraph),
        path: `word/document.xml#block-${blocks.length + 1}`,
        caption: '',
        metadata: { sourceIndex },
      };

      if (isTitle) {
        title = paragraph;
      }

      if (isSectionHeading) {
        currentSection = {
          sectionId: nextId('sec', sections.length + 1),
          title: paragraph,
          level: 2,
          blockIds: [block.id],
          metadata: { sourceIndex },
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

      blocks.push(block);
      continue;
    }

    const rows = extractTableRows(xml);
    const tableId = nextId('tbl', tables.length + 1);
    const block = {
      id: nextId('block', blocks.length + 1),
      type: 'table',
      text: rows[0]?.join(' | ') || `表格 ${tables.length + 1}`,
      content: { headers: rows[0] || [], rows: rows.slice(1) },
      level: currentSection?.level || 1,
      sectionId: currentSection?.sectionId || '',
      sectionTitle: currentSection?.title || '',
      anchorText: rows[0]?.join(' | ') || tableId,
      path: `word/document.xml#block-${blocks.length + 1}`,
      caption: '',
      metadata: { sourceIndex, tableId },
    };
    const previousBlockId = blocks.at(-1)?.id || '';
    blocks.push(block);
    if (currentSection) {
      currentSection.blockIds.push(block.id);
    }

    tables.push({
      tableId,
      title: `表格 ${tables.length + 1}`,
      sectionId: currentSection?.sectionId || '',
      afterBlockId: previousBlockId,
      anchorText: block.anchorText,
      headers: rows[0] || [],
      rows: rows.slice(1),
      metadata: { sourceIndex },
    });
  }

  return { title, sections, blocks, tables };
}

function extractTableRows(tableXml) {
  return [...tableXml.matchAll(/<w:tr\b[\s\S]*?<\/w:tr>/g)]
    .map((rowMatch) =>
      [...rowMatch[0].matchAll(/<w:tc\b[\s\S]*?<\/w:tc>/g)]
        .map((cellMatch) => extractText(cellMatch[0]))
        .filter((cell) => cell !== '')
    )
    .filter((row) => row.length > 0);
}

function extractText(xml) {
  return [...xml.matchAll(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g)]
    .map((textMatch) => decodeXml(textMatch[1]))
    .join('')
    .trim();
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

function extractFigureMarkers(documentXml, embeddedMedia, blocks) {
  const decodedXml = decodeXml(documentXml);
  const figureIds = [
    ...new Set([...decodedXml.matchAll(/\bfigureId=(fig-\d{3,})\b/g)].map((match) => match[1])),
  ];

  return figureIds.map((figureId, index) => {
    const marker = `figureId=${figureId}`;
    const media = embeddedMedia[index] || embeddedMedia[0] || {};
    const markerBlock = blocks.find((block) => block.text.includes(marker));

    return {
      figureId,
      caption: `图 ${index + 1}`,
      sectionId: markerBlock?.sectionId || '',
      anchorText: marker,
      sourceType: 'docx-embedded',
      editable: {
        format: 'docx-embedded',
        sourcePath: media.path || '',
      },
      displayPath: media.path || `assets/${figureId}/figure`,
      dimensions: {},
      quality: { status: 'not_verified', warnings: [] },
      metadata: {
        marker,
        mediaId: media.mediaId || '',
        mediaPath: media.path || '',
        relationshipId: media.relationshipId || '',
      },
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
