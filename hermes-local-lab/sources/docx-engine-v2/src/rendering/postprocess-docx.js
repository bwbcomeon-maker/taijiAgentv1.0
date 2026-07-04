const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const yauzl = require('yauzl');
const yazl = require('yazl');

async function postprocessDocx({ docxPath, renderPlan, outputPath } = {}) {
  if (!docxPath) {
    throw new Error('docxPath is required.');
  }
  if (!renderPlan) {
    throw new Error('renderPlan is required.');
  }
  if (!outputPath) {
    throw new Error('outputPath is required.');
  }

  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml');
  if (!documentXml) {
    throw new Error('DOCX is missing word/document.xml.');
  }

  bindPlannedContent({ entries, documentXml: documentXml.toString('utf8'), renderPlan, outputPath });

  await writeZipEntries(entries, outputPath);
  return { status: 'postprocessed', documentPath: outputPath };
}

function bindPlannedContent({ entries, documentXml, renderPlan, outputPath }) {
  const images = renderPlan.templateData?.images || [];
  if (images.length === 0) {
    const nextDocumentXml = insertTablesBySection(documentXml, renderPlan);
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(nextDocumentXml, renderPlan), 'utf8'));
    return;
  }

  const relationshipsEntry = 'word/_rels/document.xml.rels';
  const contentTypesEntry = '[Content_Types].xml';
  let relationshipsXml = entries.get(relationshipsEntry)?.toString('utf8') || '';
  let contentTypesXml = entries.get(contentTypesEntry)?.toString('utf8') || '';
  if (!relationshipsXml || !contentTypesXml) {
    const nextDocumentXml = insertTablesBySection(documentXml, renderPlan);
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(nextDocumentXml, renderPlan), 'utf8'));
    return;
  }

  const drawingTemplate = firstDrawingXml(documentXml);
  if (!drawingTemplate) {
    const nextDocumentXml = insertTablesBySection(documentXml, renderPlan);
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(nextDocumentXml, renderPlan), 'utf8'));
    return;
  }

  const outputDir = path.dirname(path.resolve(outputPath));
  const relationshipIds = collectRelationshipIds(relationshipsXml);
  const boundDrawings = [];
  images.forEach((image, index) => {
    const imagePath = resolvePackagePath(outputDir, image.path || '');
    if (!imagePath || !fs.existsSync(imagePath)) {
      return;
    }
    const extension = path.extname(imagePath).toLowerCase();
    const contentType = imageContentType(extension);
    if (!contentType) {
      return;
    }

    const mediaFileName = `${safeFileName(image.figureId || `fig-${index + 1}`)}${extension}`;
    const mediaEntry = `word/media/${mediaFileName}`;
    const relationshipId = nextRelationshipId(relationshipIds);
    relationshipIds.add(relationshipId);
    entries.set(mediaEntry, fs.readFileSync(imagePath));
    relationshipsXml = appendImageRelationship(relationshipsXml, relationshipId, `media/${mediaFileName}`);
    contentTypesXml = ensureContentType(contentTypesXml, extension, contentType);
    boundDrawings.push({
      image,
      index: index + 1,
      drawingXml: updateDrawingTemplate({
        drawingXml: drawingTemplate,
        relationshipId,
        figureId: image.figureId || `fig-${index + 1}`,
        docPrId: 9000 + index,
        title: image.caption || image.figureId || `图 ${index + 1}`,
        metadata: image.metadata || {},
      }),
    });
  });

  entries.set(relationshipsEntry, Buffer.from(relationshipsXml, 'utf8'));
  entries.set(contentTypesEntry, Buffer.from(contentTypesXml, 'utf8'));
  let nextDocumentXml = boundDrawings.length
    ? insertDrawingsBySection(documentXml, boundDrawings, renderPlan)
    : documentXml;
  nextDocumentXml = insertTablesBySection(nextDocumentXml, renderPlan);
  entries.set('word/document.xml', Buffer.from(injectFigureMetadata(nextDocumentXml, renderPlan), 'utf8'));
}

function collectFigureIds(renderPlan) {
  const ids = new Set();
  for (const figure of renderPlan.figures || []) {
    if (figure.figureId) {
      ids.add(figure.figureId);
    }
  }
  for (const image of renderPlan.templateData?.images || []) {
    if (image.figureId) {
      ids.add(image.figureId);
    }
  }
  return [...ids];
}

function injectFigureMetadata(documentXml, renderPlan) {
  const figureIds = collectFigureIds(renderPlan);
  if (figureIds.length === 0) {
    return documentXml;
  }

  const markerText = `docx-engine-v2 metadata ${figureIds
    .map((figureId) => `figureId=${figureId}`)
    .join(' ')}`;
  if (documentXml.includes(markerText)) {
    return documentXml;
  }

  let updatedXml = documentXml;
  if (!hasDocPrFigureMetadata(updatedXml, figureIds)) {
    const docPrMetadata = `docx-engine-v2 ${figureIds.map((figureId) => `figureId=${figureId}`).join(' ')}`;
    updatedXml = updatedXml.replace(/<wp:docPr\b([^>]*?)\/>/, (match, attributes) => {
      let nextAttributes = upsertXmlAttribute(attributes, 'descr', docPrMetadata);
      nextAttributes = upsertXmlAttribute(nextAttributes, 'title', docPrMetadata);
      return `<wp:docPr${nextAttributes}/>`;
    });
  }

  const paragraph = [
    '<w:p>',
    '<w:r>',
    '<w:rPr><w:vanish/></w:rPr>',
    `<w:t>${escapeXmlText(markerText)}</w:t>`,
    '</w:r>',
    '</w:p>',
  ].join('');

  if (updatedXml.includes('</w:body>')) {
    return updatedXml.replace('</w:body>', `${paragraph}</w:body>`);
  }

  return `${updatedXml}${paragraph}`;
}

function firstDrawingXml(documentXml) {
  return String(documentXml || '').match(/<w:drawing\b[\s\S]*?<\/w:drawing>/)?.[0] || '';
}

function insertDrawingsBySection(documentXml, boundDrawings, renderPlan) {
  const sectionsById = new Map((renderPlan.templateData?.sections || renderPlan.sections || []).map((section) => [
    section.sectionId,
    section,
  ]));
  const groups = new Map();
  for (const binding of boundDrawings) {
    const sectionId = binding.image?.metadata?.sectionId || '';
    if (!groups.has(sectionId)) {
      groups.set(sectionId, []);
    }
    groups.get(sectionId).push(figureBlockXml(binding));
  }

  const cleanDocumentXml = removeUnboundFigureCaptionBlocks(documentXml);
  const insertions = [];
  for (const [sectionId, drawings] of groups) {
    const section = sectionsById.get(sectionId);
    const insertionIndex = section ? sectionInsertionIndex(cleanDocumentXml, section.title) : -1;
    insertions.push({
      index: insertionIndex >= 0 ? insertionIndex : fallbackInsertionIndex(cleanDocumentXml),
      xml: drawings.join(''),
    });
  }

  let nextXml = cleanDocumentXml;
  for (const insertion of insertions.sort((left, right) => right.index - left.index)) {
    nextXml = `${nextXml.slice(0, insertion.index)}${insertion.xml}${nextXml.slice(insertion.index)}`;
  }
  return nextXml;
}

function figureBlockXml(binding) {
  return `${figureDrawingParagraph(binding.drawingXml)}${figureCaptionParagraph(binding.image, binding.index)}`;
}

function figureDrawingParagraph(drawingXml) {
  return [
    '<w:p>',
    '<w:pPr><w:jc w:val="center"/></w:pPr>',
    '<w:r>',
    drawingXml,
    '</w:r>',
    '</w:p>',
  ].join('');
}

function figureCaptionParagraph(image = {}, index = 1) {
  const caption = figureCaptionText(image.caption || image.figureId || `图 ${index}`, index);
  return [
    '<w:p>',
    '<w:pPr><w:jc w:val="center"/></w:pPr>',
    '<w:r>',
    '<w:rPr><w:vanish/></w:rPr>',
    `<w:t>${escapeXmlText(figureCaptionMetadata(image))}</w:t>`,
    '</w:r>',
    '<w:r><w:t>图 </w:t></w:r>',
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>',
    '<w:r><w:instrText xml:space="preserve"> SEQ 图 \\* ARABIC </w:instrText></w:r>',
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>',
    `<w:r><w:t>${escapeXmlText(String(index))}</w:t></w:r>`,
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
    `<w:r><w:t xml:space="preserve"> ${escapeXmlText(caption)}</w:t></w:r>`,
    '</w:p>',
  ].join('');
}

function figureCaptionText(value, index) {
  const raw = String(value || '').trim() || `图 ${index}`;
  const withoutPrefix = raw.replace(/^图\s*\d+\s*[:：、.．-]?\s*/, '').trim();
  return withoutPrefix || raw;
}

function figureCaptionMetadata(image = {}) {
  const metadata = image.metadata || {};
  const tokens = [
    'docx-engine-v2',
    'figureCaption',
    `figureId=${safeMetadataValue(image.figureId)}`,
  ];
  for (const key of ['sectionId', 'blockId', 'afterBlockId', 'sourceImageId']) {
    const value = safeMetadataValue(metadata[key]);
    if (value) {
      tokens.push(`${key}=${value}`);
    }
  }
  return tokens.join(' ');
}

function removeUnboundFigureCaptionBlocks(documentXml) {
  const removals = [];
  const paragraphs = paragraphRanges(documentXml);
  for (let index = 0; index < paragraphs.length; index += 1) {
    const paragraph = paragraphs[index];
    if (!hasFigureSequenceField(paragraph.xml) || /\bfigureCaption\b/.test(paragraph.text)) {
      continue;
    }
    removals.push({ start: paragraph.start, end: paragraph.end });
    const previous = paragraphs[index - 1];
    if (previous && /图片占位/.test(previous.text)) {
      removals.push({ start: previous.start, end: previous.end });
    }
  }

  let nextXml = documentXml;
  for (const removal of removals.sort((left, right) => right.start - left.start)) {
    nextXml = `${nextXml.slice(0, removal.start)}${nextXml.slice(removal.end)}`;
  }
  return nextXml;
}

function hasFigureSequenceField(paragraphXml) {
  return /<w:instrText\b[^>]*>[^<]*SEQ\s+图(?:\s|<|$)/.test(String(paragraphXml || ''));
}

function insertTablesBySection(documentXml, renderPlan) {
  const tables = renderPlan.tables || [];
  if (tables.length === 0) {
    return documentXml;
  }

  const templateTablesById = new Map((renderPlan.templateData?.tables || []).map((table) => [table.tableId, table]));
  const tableBlocks = [];
  const consumedRanges = [];
  for (const table of tables) {
    const templateTable = templateTablesById.get(table.tableId) || {};
    const tableBlock = findRenderedTableBlock({
      documentXml,
      title: templateTable.title || table.title,
      consumedRanges,
    });
    if (!tableBlock) {
      continue;
    }
    consumedRanges.push({ start: tableBlock.start, end: tableBlock.end });
    tableBlocks.push({
      table,
      start: tableBlock.start,
      end: tableBlock.end,
      xml: `${tableMarkerParagraph(table)}${tableBlock.xml}`,
    });
  }

  if (tableBlocks.length === 0) {
    return documentXml;
  }

  let withoutTables = documentXml;
  for (const block of [...tableBlocks].sort((left, right) => right.start - left.start)) {
    withoutTables = `${withoutTables.slice(0, block.start)}${withoutTables.slice(block.end)}`;
  }

  const sectionsById = new Map((renderPlan.templateData?.sections || renderPlan.sections || []).map((section) => [
    section.sectionId,
    section,
  ]));
  const groups = new Map();
  for (const block of tableBlocks) {
    const sectionId = block.table.sectionId || '';
    if (!groups.has(sectionId)) {
      groups.set(sectionId, []);
    }
    groups.get(sectionId).push(block.xml);
  }

  const insertions = [];
  for (const [sectionId, blocks] of groups) {
    const section = sectionsById.get(sectionId);
    const insertionIndex = section ? sectionInsertionIndex(withoutTables, section.title) : -1;
    insertions.push({
      index: insertionIndex >= 0 ? insertionIndex : fallbackInsertionIndex(withoutTables),
      xml: blocks.join(''),
    });
  }

  let nextXml = withoutTables;
  for (const insertion of insertions.sort((left, right) => right.index - left.index)) {
    nextXml = `${nextXml.slice(0, insertion.index)}${insertion.xml}${nextXml.slice(insertion.index)}`;
  }
  return nextXml;
}

function findRenderedTableBlock({ documentXml, title, consumedRanges }) {
  const normalizedTitle = String(title || '').trim();
  if (!normalizedTitle) {
    return null;
  }

  for (const paragraph of paragraphRanges(documentXml)) {
    if (!paragraph.text.includes(normalizedTitle) || rangeOverlaps(paragraph, consumedRanges)) {
      continue;
    }
    const afterParagraph = String(documentXml || '').slice(paragraph.end);
    const tableMatch = afterParagraph.match(/^[\s\S]*?<w:tbl\b[\s\S]*?<\/w:tbl>/);
    if (!tableMatch) {
      continue;
    }
    return {
      start: paragraph.start,
      end: paragraph.end + tableMatch[0].length,
      xml: String(documentXml || '').slice(paragraph.start, paragraph.end + tableMatch[0].length),
    };
  }
  return null;
}

function rangeOverlaps(range, consumedRanges) {
  return consumedRanges.some((consumed) => range.start < consumed.end && range.end > consumed.start);
}

function tableMarkerParagraph(table) {
  const tokens = [
    'docx-engine-v2',
    `tableId=${safeMetadataValue(table.tableId)}`,
  ];
  for (const key of ['sectionId', 'afterBlockId']) {
    const value = safeMetadataValue(table[key]);
    if (value) {
      tokens.push(`${key}=${value}`);
    }
  }
  return [
    '<w:p>',
    '<w:r>',
    '<w:rPr><w:vanish/></w:rPr>',
    `<w:t>${escapeXmlText(tokens.join(' '))}</w:t>`,
    '</w:r>',
    '</w:p>',
  ].join('');
}

function sectionInsertionIndex(documentXml, sectionTitle) {
  const title = String(sectionTitle || '').trim();
  if (!title) {
    return -1;
  }

  const paragraphs = paragraphRanges(documentXml);
  let candidates = paragraphs.filter((paragraph) => paragraph.text.trim() === title);
  if (candidates.length === 0) {
    candidates = paragraphs.filter(
      (paragraph) => paragraph.text.includes(title) && !/\b(docx-engine-v2|figureCaption|tableId)\b/.test(paragraph.text)
    );
  }
  return candidates.length ? candidates[candidates.length - 1].end : -1;
}

function fallbackInsertionIndex(documentXml) {
  const bodyEnd = String(documentXml || '').indexOf('</w:body>');
  return bodyEnd >= 0 ? bodyEnd : String(documentXml || '').length;
}

function paragraphRanges(documentXml) {
  const ranges = [];
  for (const match of String(documentXml || '').matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)) {
    ranges.push({
      start: match.index,
      end: match.index + match[0].length,
      xml: match[0],
      text: paragraphText(match[0]),
    });
  }
  return ranges;
}

function paragraphText(paragraphXml) {
  return [...String(paragraphXml || '').matchAll(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g)]
    .map((match) => unescapeXmlText(match[1]))
    .join('');
}

function unescapeXmlText(value) {
  return String(value || '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
}

function hasDocPrFigureMetadata(documentXml, figureIds) {
  const presentFigureIds = new Set();
  for (const match of String(documentXml || '').matchAll(/<wp:docPr\b[^>]*>/g)) {
    for (const figureMatch of match[0].matchAll(/\bfigureId=([A-Za-z0-9_-]+)/g)) {
      presentFigureIds.add(figureMatch[1]);
    }
  }
  return figureIds.every((figureId) => presentFigureIds.has(figureId));
}

function updateDrawingTemplate({ drawingXml, relationshipId, figureId, docPrId, title, metadata = {} }) {
  const docPrMetadata = figureDocPrMetadata(figureId, metadata);
  let next = String(drawingXml || '').replace(/\br:embed="[^"]+"/, `r:embed="${relationshipId}"`);
  next = next.replace(/<wp:docPr\b([^>]*?)\/>/, (match, attributes) => {
    let nextAttributes = upsertXmlAttribute(attributes, 'id', String(docPrId));
    nextAttributes = upsertXmlAttribute(nextAttributes, 'name', title || figureId);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'descr', docPrMetadata);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'title', docPrMetadata);
    return `<wp:docPr${nextAttributes}/>`;
  });
  return next;
}

function figureDocPrMetadata(figureId, metadata = {}) {
  const tokens = ['docx-engine-v2', `figureId=${safeMetadataValue(figureId)}`];
  for (const key of ['sectionId', 'blockId', 'afterBlockId', 'sourceImageId']) {
    const value = safeMetadataValue(metadata[key]);
    if (value) {
      tokens.push(`${key}=${value}`);
    }
  }
  return tokens.join(' ');
}

function collectRelationshipIds(relationshipsXml) {
  const ids = new Set();
  for (const match of String(relationshipsXml || '').matchAll(/\bId="([^"]+)"/g)) {
    ids.add(match[1]);
  }
  return ids;
}

function nextRelationshipId(existingIds) {
  let index = 9000;
  while (existingIds.has(`rId${index}`)) {
    index += 1;
  }
  return `rId${index}`;
}

function appendImageRelationship(relationshipsXml, relationshipId, target) {
  const relationship = `<Relationship Id="${escapeXmlAttribute(relationshipId)}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="${escapeXmlAttribute(target)}"/>`;
  if (!relationshipsXml.includes('</Relationships>')) {
    return relationshipsXml;
  }
  return relationshipsXml.replace('</Relationships>', `${relationship}</Relationships>`);
}

function ensureContentType(contentTypesXml, extension, contentType) {
  const normalizedExtension = extension.replace(/^\./, '');
  const defaultPattern = new RegExp(`<Default\\b[^>]*\\bExtension="${escapeRegExp(normalizedExtension)}"[^>]*>`, 'i');
  if (defaultPattern.test(contentTypesXml)) {
    return contentTypesXml;
  }
  if (!contentTypesXml.includes('</Types>')) {
    return contentTypesXml;
  }
  return contentTypesXml.replace(
    '</Types>',
    `<Default Extension="${escapeXmlAttribute(normalizedExtension)}" ContentType="${escapeXmlAttribute(contentType)}"/></Types>`
  );
}

function imageContentType(extension) {
  return {
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
  }[extension] || '';
}

function resolvePackagePath(baseDir, relativePath) {
  const normalizedPath = String(relativePath || '').replace(/\\/g, '/').replace(/^\/+/, '');
  if (!normalizedPath || normalizedPath.split('/').includes('..')) {
    return '';
  }
  const resolvedPath = path.join(baseDir, normalizedPath);
  const relative = path.relative(baseDir, resolvedPath);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    return '';
  }
  return resolvedPath;
}

function safeFileName(value) {
  return String(value || '').replace(/[^A-Za-z0-9_-]/g, '_') || 'image';
}

function safeMetadataValue(value) {
  return String(value || '').replace(/[^A-Za-z0-9_-]/g, '_');
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function readZipEntries(docxPath) {
  return new Promise((resolve, reject) => {
    yauzl.open(docxPath, { lazyEntries: true }, (openError, zipfile) => {
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
          // Preserve the original ZIP error.
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

async function writeZipEntries(entries, outputPath) {
  const absoluteOutputPath = path.resolve(outputPath);
  const tempPath = `${absoluteOutputPath}.tmp-${process.pid}`;

  await fsp.mkdir(path.dirname(absoluteOutputPath), { recursive: true });
  const zip = new yazl.ZipFile();
  const output = fs.createWriteStream(tempPath);
  const writeFinished = waitForZipWrite({ zip, output, tempPath });
  zip.outputStream.pipe(output);

  for (const [entryName, entryBuffer] of entries) {
    zip.addBuffer(entryBuffer, entryName);
  }

  zip.end();
  await writeFinished;
  await fsp.rename(tempPath, absoluteOutputPath);
}

function waitForZipWrite({ zip, output, tempPath }) {
  return new Promise((resolve, reject) => {
    let settled = false;

    const fail = (error) => {
      if (settled) {
        return;
      }
      settled = true;
      fs.rmSync(tempPath, { force: true });
      reject(error);
    };

    output.on('error', fail);
    zip.outputStream.on('error', fail);
    output.on('close', () => {
      if (settled) {
        return;
      }
      settled = true;
      resolve();
    });
  });
}

function escapeXmlText(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeXmlAttribute(value) {
  return escapeXmlText(value).replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

function upsertXmlAttribute(attributes, name, value) {
  const escapedValue = escapeXmlAttribute(value);
  const attributePattern = new RegExp(`\\s${name}="[^"]*"`);
  if (attributePattern.test(attributes)) {
    return attributes.replace(attributePattern, ` ${name}="${escapedValue}"`);
  }
  return `${attributes} ${name}="${escapedValue}"`;
}

module.exports = { postprocessDocx };
