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
  const drawingBindings = extractDrawingBindings(documentXml, relationshipsXml);
  const warnings = extractUnsupportedImageWarnings(documentXml, relationshipsXml);
  const { title, sections, blocks, tables } = extractBodyStructure(documentXml, drawingBindings);
  const figures = extractFigureMarkers(documentXml, relationshipsXml, embeddedMedia, blocks);
  bindFigureMarkersToBlocks(blocks, figures);

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
    figures,
    images: [],
    embeddedMedia,
    warnings,
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

function extractBodyStructure(documentXml, drawingBindings = []) {
  const sections = [];
  const blocks = [];
  const tables = [];
  let title = '';
  let currentSection = null;
  const drawingBindingByRelationshipId = new Map(
    drawingBindings
      .filter((binding) => binding.relationshipId)
      .map((binding) => [binding.relationshipId, binding])
  );

  for (const match of documentXml.matchAll(/<w:(p|tbl)\b[\s\S]*?<\/w:\1>/g)) {
    const elementType = match[1];
    const xml = match[0];
    const sourceIndex = blocks.length + tables.length;

    if (elementType === 'p') {
      const paragraph = extractText(xml);
      const paragraphDrawings = extractParagraphDrawingBindings(xml, drawingBindingByRelationshipId);
      if (!paragraph && paragraphDrawings.length === 0) {
        continue;
      }

      if (paragraph) {
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
      }

      for (const drawing of paragraphDrawings) {
        const block = {
          id: nextId('block', blocks.length + 1),
          type: 'figure',
          text: drawing.figureId ? `figureId=${drawing.figureId}` : `DOCX 图片 ${drawing.drawingIndex}`,
          content: {
            relationshipId: drawing.relationshipId,
            mediaPath: drawing.mediaPath,
          },
          level: currentSection?.level || 1,
          sectionId: currentSection?.sectionId || '',
          sectionTitle: currentSection?.title || '',
          anchorText: drawing.figureId ? `figureId=${drawing.figureId}` : `docx-drawing:${drawing.relationshipId}`,
          path: `word/document.xml#block-${blocks.length + 1}`,
          caption: drawing.caption || `图 ${drawing.drawingIndex}`,
          metadata: {
            sourceIndex,
            drawingIndex: drawing.drawingIndex,
            figureId: drawing.figureId || '',
            relationshipId: drawing.relationshipId,
            mediaPath: drawing.mediaPath,
          },
        };
        blocks.push(block);
        if (currentSection) {
          currentSection.blockIds.push(block.id);
        }
      }
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

  bindVisibleFigureCaptionBlocks(blocks, sections);
  bindVisibleTableCaptionBlocks(blocks, sections, tables);
  return { title, sections, blocks, tables };
}

function bindVisibleFigureCaptionBlocks(blocks, sections) {
  const captionBlockIds = new Set();

  for (let index = 0; index < blocks.length - 1; index += 1) {
    const block = blocks[index];
    const nextBlock = blocks[index + 1];
    if (block.type !== 'figure' || nextBlock?.type !== 'paragraph') {
      continue;
    }
    if ((block.sectionId || '') !== (nextBlock.sectionId || '')) {
      continue;
    }

    const caption = parseVisibleFigureCaption(nextBlock.text);
    if (!caption) {
      continue;
    }

    block.caption = caption;
    block.metadata = {
      ...(block.metadata || {}),
      captionSource: 'visible-paragraph',
      visibleCaptionText: nextBlock.text,
    };
    captionBlockIds.add(nextBlock.id);
    index += 1;
  }

  if (captionBlockIds.size === 0) {
    return;
  }

  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    if (captionBlockIds.has(blocks[index].id)) {
      blocks.splice(index, 1);
    }
  }

  for (const section of sections) {
    section.blockIds = (section.blockIds || []).filter((blockId) => !captionBlockIds.has(blockId));
  }
}

function parseVisibleFigureCaption(value) {
  return parseNumberedCaption(value, '图');
}

function bindVisibleTableCaptionBlocks(blocks, sections, tables) {
  const tableById = new Map((tables || []).map((table) => [table.tableId, table]));
  const captionBlockIds = new Set();

  for (let index = 1; index < blocks.length; index += 1) {
    const block = blocks[index];
    const previousBlock = blocks[index - 1];
    if (block.type !== 'table' || previousBlock?.type !== 'paragraph') {
      continue;
    }
    if ((block.sectionId || '') !== (previousBlock.sectionId || '')) {
      continue;
    }

    const caption = parseVisibleTableCaption(previousBlock.text);
    if (!caption) {
      continue;
    }

    const tableId = block.metadata?.tableId || '';
    const table = tableById.get(tableId);
    block.caption = caption;
    block.metadata = {
      ...(block.metadata || {}),
      captionSource: 'visible-paragraph',
      visibleCaptionText: previousBlock.text,
    };
    if (table) {
      table.title = caption;
      table.afterBlockId = previousBlockIdBefore(blocks, index - 1, block.sectionId);
      table.metadata = {
        ...(table.metadata || {}),
        captionSource: 'visible-paragraph',
        visibleCaptionText: previousBlock.text,
      };
    }
    captionBlockIds.add(previousBlock.id);
  }

  if (captionBlockIds.size === 0) {
    return;
  }

  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    if (captionBlockIds.has(blocks[index].id)) {
      blocks.splice(index, 1);
    }
  }

  for (const section of sections) {
    section.blockIds = (section.blockIds || []).filter((blockId) => !captionBlockIds.has(blockId));
  }
}

function parseVisibleTableCaption(value) {
  return parseNumberedCaption(value, '表');
}

function parseNumberedCaption(value, label) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim();
  const match = normalized.match(
    new RegExp(`^${label}\\s*(?:\\d+(?:[-.．\\u2013\\u2014]\\d+)*|[一二三四五六七八九十百]+)\\s*[.．、:：-]?\\s*(.+)$`)
  );
  return match?.[1]?.trim() || '';
}

function previousBlockIdBefore(blocks, beforeIndex, sectionId) {
  for (let index = beforeIndex - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if ((block.sectionId || '') === (sectionId || '')) {
      return block.id;
    }
  }
  return '';
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
  const relationshipByTarget = relationshipByTargetPath(relationshipsXml);

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
        contentBase64: buffer.toString('base64'),
      };
    });
}

function extractFigureMarkers(documentXml, relationshipsXml, embeddedMedia, blocks) {
  const decodedXml = decodeXml(documentXml);
  const figureIds = [
    ...new Set([...decodedXml.matchAll(/\bfigureId=(fig-\d{3,})\b/g)].map((match) => match[1])),
  ];
  const drawingBindingByFigureId = extractDrawingFigureBindings(documentXml, relationshipsXml);
  const mediaByPath = new Map(embeddedMedia.map((media) => [media.path, media]));
  const usedFigureIds = new Set(figureIds);
  const figures = [];

  for (const figureId of figureIds) {
    const marker = `figureId=${figureId}`;
    const markerBlock = blocks.find(
      (block) => block.metadata?.figureId === figureId || block.text.includes(marker)
    );
    const drawingBinding = drawingBindingByFigureId.get(figureId) || {
      relationshipId: markerBlock?.metadata?.relationshipId || '',
      mediaPath: markerBlock?.metadata?.mediaPath || '',
    };
    const media = mediaByPath.get(drawingBinding.mediaPath) || embeddedMedia[figures.length] || embeddedMedia[0] || {};

    figures.push({
      figureId,
      caption: markerBlock?.caption || `图 ${figures.length + 1}`,
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
        relationshipId: drawingBinding.relationshipId || media.relationshipId || '',
      },
    });
  }

  for (const block of blocks || []) {
    if (block.type !== 'figure' || !block.metadata?.relationshipId || block.metadata.figureId) {
      continue;
    }

    const figureId = nextAvailableFigureId(usedFigureIds);
    usedFigureIds.add(figureId);
    block.metadata.figureId = figureId;
    block.anchorText = block.anchorText || `docx-drawing:${block.metadata.relationshipId}`;
    const media = mediaByPath.get(block.metadata.mediaPath) || {};
    figures.push({
      figureId,
      caption: block.caption || `图 ${figures.length + 1}`,
      sectionId: block.sectionId || '',
      anchorText: block.anchorText,
      sourceType: 'docx-embedded',
      editable: {
        format: 'docx-embedded',
        sourcePath: media.path || block.metadata.mediaPath || '',
      },
      displayPath: media.path || block.metadata.mediaPath || `assets/${figureId}/figure`,
      dimensions: {},
      quality: { status: 'not_verified', warnings: [] },
      metadata: {
        generatedFrom: 'docx-drawing',
        mediaId: media.mediaId || '',
        mediaPath: media.path || block.metadata.mediaPath || '',
        relationshipId: block.metadata.relationshipId || media.relationshipId || '',
        drawingIndex: block.metadata.drawingIndex || 0,
      },
    });
  }

  return figures;
}

function extractDrawingFigureBindings(documentXml, relationshipsXml) {
  const bindings = new Map();
  for (const drawing of extractDrawingBindings(documentXml, relationshipsXml)) {
    if (drawing.figureId) {
      bindings.set(drawing.figureId, drawing);
    }
  }
  return bindings;
}

function extractDrawingBindings(documentXml, relationshipsXml) {
  const relationshipById = relationshipByRelationshipId(relationshipsXml);
  const bindings = [];
  let drawingIndex = 0;

  for (const drawingMatch of String(documentXml || '').matchAll(/<w:drawing\b[\s\S]*?<\/w:drawing>/g)) {
    drawingIndex += 1;
    const drawingXml = drawingMatch[0];
    const relationshipMatch = drawingXml.match(/\br:embed="([^"]+)"/);
    if (!relationshipMatch) {
      continue;
    }

    const relationshipId = relationshipMatch[1];
    const relationship = relationshipById.get(relationshipId) || {};
    if (!isImageRelationship(relationship.Type)) {
      continue;
    }
    const mediaPath = normalizeRelationshipTarget(relationship.Target || '');
    if (!mediaPath) {
      continue;
    }

    bindings.push({
      drawingIndex,
      figureId: drawingXml.match(/\bfigureId=([A-Za-z0-9_-]+)/)?.[1] || '',
      caption: drawingCaption(drawingXml, drawingIndex),
      relationshipId,
      mediaPath,
    });
  }

  return bindings;
}

function extractParagraphDrawingBindings(paragraphXml, drawingBindingByRelationshipId) {
  return [...String(paragraphXml || '').matchAll(/<w:drawing\b[\s\S]*?<\/w:drawing>/g)]
    .map((drawingMatch) => drawingMatch[0].match(/\br:embed="([^"]+)"/)?.[1] || '')
    .filter(Boolean)
    .map((relationshipId) => drawingBindingByRelationshipId.get(relationshipId))
    .filter(Boolean);
}

function extractUnsupportedImageWarnings(documentXml, relationshipsXml) {
  const relationshipById = relationshipByRelationshipId(relationshipsXml);
  const seenRelationshipIds = new Set();
  const warnings = [];

  for (const drawingMatch of String(documentXml || '').matchAll(/<w:drawing\b[\s\S]*?<\/w:drawing>/g)) {
    const drawingXml = drawingMatch[0];
    for (const relationshipMatch of drawingXml.matchAll(/\br:(?:embed|link)="([^"]+)"/g)) {
      const relationshipId = relationshipMatch[1];
      if (seenRelationshipIds.has(relationshipId)) {
        continue;
      }
      seenRelationshipIds.add(relationshipId);

      const relationship = relationshipById.get(relationshipId) || {};
      if (!isImageRelationship(relationship.Type)) {
        continue;
      }
      if (!isUnsupportedImageRelationship(relationship)) {
        continue;
      }

      warnings.push({
        code: 'unsupported_external_docx_image',
        severity: 'error',
        message: `DOCX 外链图片暂不支持: ${relationship.Target || relationshipId}。请先在 Word/WPS 中将图片嵌入文档后再渲染。`,
        relationshipId,
        target: relationship.Target || '',
        targetMode: relationship.TargetMode || '',
      });
    }
  }

  return warnings;
}

function isUnsupportedImageRelationship(relationship) {
  const target = String(relationship?.Target || '').trim();
  const targetMode = String(relationship?.TargetMode || '').trim().toLowerCase();
  return targetMode === 'external' || /^[a-z][a-z0-9+.-]*:/i.test(target) || !normalizeRelationshipTarget(target);
}

function drawingCaption(drawingXml, drawingIndex) {
  const docPrMatch = String(drawingXml || '').match(/<wp:docPr\b([^>]*)\/?>/);
  const attributes = docPrMatch ? parseAttributes(docPrMatch[1]) : {};
  return [attributes.title, attributes.descr, attributes.name]
    .map((value) => String(value || '').replace(/\s+/g, ' ').trim())
    .find(isMeaningfulDrawingCaption) || `图 ${drawingIndex}`;
}

function isMeaningfulDrawingCaption(value) {
  if (!value) {
    return false;
  }
  if (/\bfigureId=[A-Za-z0-9_-]+\b/.test(value)) {
    return false;
  }
  if (/^(picture|image)\s*\d*$/i.test(value)) {
    return false;
  }
  return true;
}

function relationshipByTargetPath(relationshipsXml) {
  return new Map(
    relationshipEntries(relationshipsXml).map((attributes) => [
      normalizeRelationshipTarget(attributes.Target || ''),
      attributes,
    ])
  );
}

function relationshipByRelationshipId(relationshipsXml) {
  return new Map(
    relationshipEntries(relationshipsXml)
      .filter((attributes) => attributes.Id)
      .map((attributes) => [attributes.Id, attributes])
  );
}

function relationshipEntries(relationshipsXml) {
  return [...String(relationshipsXml || '').matchAll(/<Relationship\b([^>]*)\/?>/g)].map((match) =>
    parseAttributes(match[1])
  );
}

function isImageRelationship(type) {
  return String(type || '').endsWith('/image');
}

function bindFigureMarkersToBlocks(blocks, figures) {
  for (const figure of figures || []) {
    const marker = figure.metadata?.marker;
    if (!marker) {
      continue;
    }
    const block = (blocks || []).find((candidate) => candidate.text.includes(marker));
    if (!block) {
      continue;
    }
    block.type = 'figure';
    block.caption = figure.caption || '';
    block.anchorText = marker;
    block.metadata = {
      ...(block.metadata || {}),
      figureId: figure.figureId,
      mediaId: figure.metadata?.mediaId || '',
      mediaPath: figure.metadata?.mediaPath || '',
      relationshipId: figure.metadata?.relationshipId || '',
    };
  }
}

function nextAvailableFigureId(usedFigureIds) {
  let index = 1;
  let figureId = nextId('fig', index);
  while (usedFigureIds.has(figureId)) {
    index += 1;
    figureId = nextId('fig', index);
  }
  return figureId;
}

function normalizeRelationshipTarget(target) {
  const rawTarget = String(target || '').trim().replace(/\\/g, '/');
  if (!rawTarget || /^[a-z][a-z0-9+.-]*:/i.test(rawTarget)) {
    return '';
  }
  if (rawTarget.startsWith('/')) {
    return path.posix.normalize(rawTarget.replace(/^\/+/, ''));
  }

  const entryName = path.posix.normalize(path.posix.join('word', rawTarget));
  return entryName.startsWith('../') ? '' : entryName;
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
