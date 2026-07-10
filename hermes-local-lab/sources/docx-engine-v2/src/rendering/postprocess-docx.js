const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const yauzl = require('yauzl');
const yazl = require('yazl');

const { resolveSectionAnchors } = require('../domain/section-anchors');

const DIRECTORY_TAB_POS_DXA = 8306;
const ESTIMATED_BODY_START_PAGE = 3;
const ESTIMATED_PAGE_UNITS = 22;
const TABLE_WIDTH_DXA = 8520;
const TABLE_MIN_COLUMN_WIDTH_DXA = 720;

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
  normalizePortableFooters(entries);

  await writeZipEntries(entries, outputPath);
  return { status: 'postprocessed', documentPath: outputPath };
}

function bindPlannedContent({ entries, documentXml, renderPlan, outputPath }) {
  const images = renderPlan.templateData?.images || [];
  if (images.length === 0) {
    const nextDocumentXml = insertRichBlocksBySourceOrder(documentXml, { boundDrawings: [], renderPlan });
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(nextDocumentXml, renderPlan), 'utf8'));
    return;
  }

  const relationshipsEntry = 'word/_rels/document.xml.rels';
  const contentTypesEntry = '[Content_Types].xml';
  let relationshipsXml = entries.get(relationshipsEntry)?.toString('utf8') || '';
  let contentTypesXml = entries.get(contentTypesEntry)?.toString('utf8') || '';
  if (!relationshipsXml || !contentTypesXml) {
    const nextDocumentXml = insertRichBlocksBySourceOrder(documentXml, { boundDrawings: [], renderPlan });
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(nextDocumentXml, renderPlan), 'utf8'));
    return;
  }

  const drawingTemplate = firstDrawingXml(documentXml);
  if (!drawingTemplate) {
    const nextDocumentXml = insertRichBlocksBySourceOrder(documentXml, { boundDrawings: [], renderPlan });
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
        image,
        metadata: image.metadata || {},
      }),
    });
  });

  entries.set(relationshipsEntry, Buffer.from(relationshipsXml, 'utf8'));
  entries.set(contentTypesEntry, Buffer.from(contentTypesXml, 'utf8'));
  const nextDocumentXml = insertRichBlocksBySourceOrder(documentXml, { boundDrawings, renderPlan });
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

  if (hasDocPrFigureMetadata(documentXml, figureIds)) {
    return documentXml;
  }

  const docPrMetadata = `docx-engine-v2 ${figureIds.map((figureId) => `figureId=${figureId}`).join(' ')}`;
  return documentXml.replace(/<wp:docPr\b([^>]*?)\/>/, (match, attributes) => {
    let nextAttributes = upsertXmlAttribute(attributes, 'descr', docPrMetadata);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'title', docPrMetadata);
    return `<wp:docPr${nextAttributes}/>`;
  });
}

function firstDrawingXml(documentXml) {
  return String(documentXml || '').match(/<w:drawing\b[\s\S]*?<\/w:drawing>/)?.[0] || '';
}

function insertRichBlocksBySourceOrder(documentXml, { boundDrawings = [], renderPlan } = {}) {
  const extractedTables = extractPlannedTableBlocks(documentXml, renderPlan);
  let cleanDocumentXml = removeRanges(documentXml, extractedTables.map((table) => table.range));
  cleanDocumentXml = removeUnboundTableBlocks(cleanDocumentXml);
  cleanDocumentXml = removeUnboundFigureCaptionBlocks(cleanDocumentXml);
  cleanDocumentXml = compactCoverPageSpacing(cleanDocumentXml);

  const richBlockXmlByBlockId = new Map();
  for (const table of extractedTables) {
    if (table.blockId) {
      richBlockXmlByBlockId.set(table.blockId, table.xml);
    }
  }

  const figureBlockIdByFigureId = figureBlockIdByFigureIdMap(renderPlan);
  for (const binding of boundDrawings) {
    const blockId = binding.image?.metadata?.blockId || figureBlockIdByFigureId.get(binding.image?.figureId) || '';
    if (blockId) {
      richBlockXmlByBlockId.set(blockId, figureBlockXml(binding));
    }
  }

  const insertions = richBlockInsertions(cleanDocumentXml, renderPlan, richBlockXmlByBlockId);
  let nextXml = cleanDocumentXml;
  for (const insertion of insertions.sort((left, right) => right.index - left.index)) {
    nextXml = `${nextXml.slice(0, insertion.index)}${insertion.xml}${nextXml.slice(insertion.index)}`;
  }
  return addDirectoryBookmarks(replaceStaticDirectories(nextXml, renderPlan), renderPlan);
}

function compactCoverPageSpacing(documentXml) {
  const firstSectionIndex = String(documentXml || '').indexOf('<w:sectPr');
  if (firstSectionIndex < 0) {
    return documentXml;
  }

  const coverXml = documentXml.slice(0, firstSectionIndex);
  const restXml = documentXml.slice(firstSectionIndex);
  const compactedCoverXml = coverXml.replace(/<w:spacing\b([^>]*)\/>/g, compactCoverSpacingTag);
  return `${compactedCoverXml}${restXml}`;
}

function compactCoverSpacingTag(match, attributes = '') {
  const beforeMatch = String(attributes || '').match(/\bw:before="(\d+)"/);
  const before = beforeMatch ? Number(beforeMatch[1]) : 0;
  const hasBeforeLines = /\bw:beforeLines="/.test(String(attributes || ''));
  if (!hasBeforeLines && (!before || before <= 1200)) {
    return match;
  }

  const restAttributes = String(attributes || '')
    .replace(/\s*w:before="[^"]*"/g, '')
    .replace(/\s*w:beforeLines="[^"]*"/g, '')
    .trim();
  const compactBefore = Math.min(before || 1200, 1200);
  const nextAttributes = [`w:before="${compactBefore}"`, restAttributes].filter(Boolean).join(' ');
  return `<w:spacing ${nextAttributes}/>`;
}

function normalizePortableFooters(entries) {
  for (const [entryName, entryBuffer] of entries) {
    if (!/^word\/footer\d+\.xml$/.test(entryName)) {
      continue;
    }
    const footerXml = entryBuffer.toString('utf8');
    if (!isNonPortablePageFooter(footerXml)) {
      continue;
    }
    entries.set(entryName, Buffer.from(portablePageFooterXml(), 'utf8'));
  }
}

function isNonPortablePageFooter(footerXml) {
  const source = String(footerXml || '');
  return /NUMPAGES/.test(source) || /<(?:wps:txbx|v:textbox|wp:anchor)\b/.test(source);
}

function portablePageFooterXml() {
  const runProperties = [
    '<w:rPr>',
    '<w:rFonts w:hint="eastAsia" w:eastAsia="宋体" w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>',
    '<w:sz w:val="18"/>',
    '<w:szCs w:val="18"/>',
    '</w:rPr>',
  ].join('');
  return [
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
    '<w:p>',
    '<w:pPr><w:jc w:val="center"/></w:pPr>',
    `<w:r>${runProperties}<w:t xml:space="preserve">第 </w:t></w:r>`,
    `<w:r>${runProperties}<w:fldChar w:fldCharType="begin"/></w:r>`,
    `<w:r>${runProperties}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>`,
    `<w:r>${runProperties}<w:fldChar w:fldCharType="separate"/></w:r>`,
    `<w:r>${runProperties}<w:t>1</w:t></w:r>`,
    `<w:r>${runProperties}<w:fldChar w:fldCharType="end"/></w:r>`,
    `<w:r>${runProperties}<w:t xml:space="preserve"> 页</w:t></w:r>`,
    '</w:p>',
    '</w:ftr>',
  ].join('');
}

function extractPlannedTableBlocks(documentXml, renderPlan) {
  const templateTablesById = new Map((renderPlan.templateData?.tables || []).map((table) => [table.tableId, table]));
  const blockIdByTableId = tableBlockIdByTableIdMap(renderPlan);
  const tableBlocks = [];
  const consumedRanges = [];
  (renderPlan.tables || []).forEach((table, index) => {
    const templateTable = templateTablesById.get(table.tableId) || {};
    const tableBlock = findRenderedTableBlock({
      documentXml,
      title: templateTable.title || table.title,
      consumedRanges,
    });
    if (!tableBlock) {
      return;
    }
    consumedRanges.push({ start: tableBlock.start, end: tableBlock.end });
    tableBlocks.push({
      blockId: blockIdByTableId.get(table.tableId) || '',
      range: { start: tableBlock.start, end: tableBlock.end },
      xml: tableBlockXml(table, templateTable, tableBlock.xml, blockIdByTableId.get(table.tableId) || '', index + 1),
    });
  });
  return tableBlocks;
}

function richBlockInsertions(documentXml, renderPlan, richBlockXmlByBlockId) {
  const insertionsByIndex = new Map();
  const sections = renderPlan.templateData?.sections || renderPlan.sections || [];
  const sectionAnchors = resolveSectionAnchors(paragraphRanges(documentXml), sections);
  for (let sectionIndex = 0; sectionIndex < sections.length; sectionIndex += 1) {
    const section = sections[sectionIndex];
    let insertionIndex = sectionAnchors[sectionIndex]?.end ?? sectionInsertionIndex(documentXml, section.title);
    if (insertionIndex < 0) {
      insertionIndex = fallbackInsertionIndex(documentXml);
    }
    let pendingXml = '';
    for (const block of section.blocks || []) {
      const richXml = richBlockXmlByBlockId.get(block.blockId);
      if (richXml) {
        pendingXml += richXml;
        continue;
      }
      if (block.type !== 'paragraph' || isSectionTitleBlock(block, section)) {
        continue;
      }
      if (pendingXml) {
        appendInsertion(insertionsByIndex, insertionIndex, pendingXml);
        pendingXml = '';
      }
      const paragraph = findParagraphAfter(documentXml, block.text, insertionIndex);
      if (paragraph) {
        insertionIndex = paragraph.end;
      }
    }
    if (pendingXml) {
      appendInsertion(insertionsByIndex, insertionIndex, pendingXml);
    }
  }
  return [...insertionsByIndex.entries()].map(([index, xml]) => ({ index, xml }));
}

function appendInsertion(insertionsByIndex, index, xml) {
  insertionsByIndex.set(index, `${insertionsByIndex.get(index) || ''}${xml}`);
}

function findParagraphAfter(documentXml, text, afterIndex) {
  const normalizedText = String(text || '').trim();
  if (!normalizedText) {
    return null;
  }
  return paragraphRanges(documentXml).find(
    (paragraph) => paragraph.start >= afterIndex && paragraph.text.trim() === normalizedText
  ) || null;
}

function isSectionTitleBlock(block, section) {
  return String(block?.text || '').trim() === String(section?.title || '').trim();
}

function tableBlockIdByTableIdMap(renderPlan) {
  const blockIdByTableId = new Map();
  for (const section of renderPlan.templateData?.sections || renderPlan.sections || []) {
    for (const block of section.blocks || []) {
      if (block.tableId && block.blockId) {
        blockIdByTableId.set(block.tableId, block.blockId);
      }
    }
  }
  return blockIdByTableId;
}

function figureBlockIdByFigureIdMap(renderPlan) {
  const blockIdByFigureId = new Map();
  for (const section of renderPlan.templateData?.sections || renderPlan.sections || []) {
    for (const block of section.blocks || []) {
      if (block.figureId && block.blockId) {
        blockIdByFigureId.set(block.figureId, block.blockId);
      }
    }
  }
  return blockIdByFigureId;
}

function removeRanges(documentXml, ranges) {
  let nextXml = documentXml;
  for (const range of mergeRanges(ranges).sort((left, right) => right.start - left.start)) {
    nextXml = `${nextXml.slice(0, range.start)}${nextXml.slice(range.end)}`;
  }
  return nextXml;
}

function mergeRanges(ranges) {
  const sorted = [...(ranges || [])]
    .filter((range) => Number.isInteger(range?.start) && Number.isInteger(range?.end) && range.end > range.start)
    .sort((left, right) => left.start - right.start);
  const merged = [];
  for (const range of sorted) {
    const previous = merged[merged.length - 1];
    if (previous && range.start <= previous.end) {
      previous.end = Math.max(previous.end, range.end);
      continue;
    }
    merged.push({ ...range });
  }
  return merged;
}

function figureBlockXml(binding) {
  return `${figureDrawingParagraph(binding.drawingXml)}${figureCaptionParagraph(binding.image, binding.index)}`;
}

function figureDrawingParagraph(drawingXml) {
  return [
    '<w:p>',
    '<w:pPr><w:spacing w:before="120" w:after="120" w:line="240" w:lineRule="auto"/><w:ind w:left="0" w:right="0" w:firstLine="0" w:hanging="0"/><w:jc w:val="center"/></w:pPr>',
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
    '<w:pPr><w:spacing w:before="120" w:after="120" w:line="240" w:lineRule="auto"/><w:ind w:left="0" w:right="0" w:firstLine="0" w:hanging="0"/><w:jc w:val="center"/></w:pPr>',
    '<w:r>',
    '<w:rPr><w:vanish/></w:rPr>',
    `<w:t>${escapeXmlText(figureCaptionMetadata(image))}</w:t>`,
    '</w:r>',
    `<w:r><w:t>${escapeXmlText(`图 ${index} ${caption}`)}</w:t></w:r>`,
    '</w:p>',
  ].join('');
}

function figureCaptionText(value, index) {
  const raw = String(value || '').trim() || `图 ${index}`;
  const withoutPrefix = raw.replace(/^(?:图|图片|图示)\s*\d+\s*[:：、.．-]?\s*/, '').trim();
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
      removals.push(findContainingTableRange(documentXml, previous) || { start: previous.start, end: previous.end });
    }
  }

  return removeRanges(documentXml, removals);
}

function hasFigureSequenceField(paragraphXml) {
  return /<w:instrText\b[^>]*>[^<]*SEQ\s+图(?:\s|<|$)/.test(String(paragraphXml || ''));
}

function tableBlockXml(table, templateTable, blockXml, blockId = '', index = 1) {
  const captionXml = tableCaptionXml(table, blockId, index);
  const tableXml = dynamicTableXml(templateTable);
  if (!tableXml) {
    return `${captionXml}${String(blockXml || '')}`;
  }
  return `${captionXml}${tableXml}`;
}

function tableCaptionXml(table, blockId = '', index = 1) {
  const caption = tableCaptionText(table.title || table.caption || `表格 ${index}`, index);
  return [
    '<w:p>',
    '<w:pPr><w:spacing w:before="120" w:after="120" w:line="240" w:lineRule="auto"/><w:ind w:left="0" w:right="0" w:firstLine="0" w:hanging="0"/><w:jc w:val="center"/></w:pPr>',
    '<w:r>',
    '<w:rPr><w:vanish/></w:rPr>',
    `<w:t>${escapeXmlText(tableCaptionMetadata(table, blockId))}</w:t>`,
    '</w:r>',
    `<w:r><w:t>${escapeXmlText(`表 ${index} ${caption}`)}</w:t></w:r>`,
    '</w:p>',
  ].join('');
}

function tableCaptionText(value, index) {
  const raw = String(value || '').trim() || `表格 ${index}`;
  const withoutPrefix = raw.replace(/^(?:表|表格)\s*\d+\s*[:：、.．-]?\s*/, '').trim();
  return withoutPrefix || raw;
}

function dynamicTableXml(templateTable = {}) {
  const columns = templateTableColumns(templateTable);
  const rows = templateTableRows(templateTable, columns);
  if (columns.length === 0) {
    return '';
  }

  const widths = dynamicTableColumnWidths(TABLE_WIDTH_DXA, columns.length);
  const tableWidth = widths.reduce((sum, width) => sum + width, 0);

  return [
    '<w:tbl>',
    dynamicTableProperties(tableWidth),
    dynamicTableGrid(widths),
    dynamicTableRow(columns.map((column) => column.text), widths, { header: true }),
    ...rows.map((row) => dynamicTableRow(row, widths, { header: false })),
    '</w:tbl>',
  ].join('');
}

function dynamicTableColumnWidths(tableWidth, columnCount) {
  if (columnCount <= 0) {
    return [];
  }

  const baseWidth = Math.floor(tableWidth / columnCount);
  if (baseWidth < TABLE_MIN_COLUMN_WIDTH_DXA) {
    return Array.from({ length: columnCount }, () => TABLE_MIN_COLUMN_WIDTH_DXA);
  }

  const remainder = tableWidth - baseWidth * columnCount;
  return Array.from({ length: columnCount }, (_value, index) => baseWidth + (index < remainder ? 1 : 0));
}

function dynamicTableProperties(tableWidth = TABLE_WIDTH_DXA) {
  return [
    '<w:tblPr>',
    '<w:tblStyle w:val="51"/>',
    `<w:tblW w:w="${tableWidth}" w:type="dxa"/>`,
    '<w:jc w:val="center"/>',
    '<w:tblBorders>',
    '<w:top w:val="single" w:color="000000" w:sz="4" w:space="0"/>',
    '<w:left w:val="single" w:color="000000" w:sz="4" w:space="0"/>',
    '<w:bottom w:val="single" w:color="000000" w:sz="4" w:space="0"/>',
    '<w:right w:val="single" w:color="000000" w:sz="4" w:space="0"/>',
    '<w:insideH w:val="single" w:color="000000" w:sz="4" w:space="0"/>',
    '<w:insideV w:val="single" w:color="000000" w:sz="4" w:space="0"/>',
    '</w:tblBorders>',
    '<w:tblLayout w:type="fixed"/>',
    '<w:tblCellMar>',
    '<w:top w:w="80" w:type="dxa"/>',
    '<w:left w:w="100" w:type="dxa"/>',
    '<w:bottom w:w="80" w:type="dxa"/>',
    '<w:right w:w="100" w:type="dxa"/>',
    '</w:tblCellMar>',
    '</w:tblPr>',
  ].join('');
}

function dynamicTableGrid(widths) {
  return [
    '<w:tblGrid>',
    ...widths.map((width) => `<w:gridCol w:w="${width}"/>`),
    '</w:tblGrid>',
  ].join('');
}

function dynamicTableRow(values, widths, { header = false } = {}) {
  return [
    '<w:tr>',
    dynamicTableRowProperties({ header }),
    ...(values || []).map((value, index) =>
      dynamicTableCell(value, widths[index] || widths[0] || 1440, { header, columnCount: widths.length })
    ),
    '</w:tr>',
  ].join('');
}

function dynamicTableRowProperties({ header = false } = {}) {
  return [
    '<w:trPr>',
    ...(header ? ['<w:tblHeader/>'] : []),
    `<w:trHeight w:val="${header ? 760 : 680}" w:hRule="atLeast"/>`,
    '<w:cantSplit/>',
    '</w:trPr>',
  ].join('');
}

function dynamicTableCell(value, width, { header = false, columnCount = 1 } = {}) {
  const fontSize = tableFontSize(columnCount);
  return [
    '<w:tc>',
    '<w:tcPr>',
    `<w:tcW w:w="${width}" w:type="dxa"/>`,
    '<w:vAlign w:val="center"/>',
    ...(header ? ['<w:shd w:val="clear" w:color="auto" w:fill="D9EAF7"/>'] : []),
    '<w:tcMar>',
    '<w:top w:w="80" w:type="dxa"/>',
    '<w:left w:w="100" w:type="dxa"/>',
    '<w:bottom w:w="80" w:type="dxa"/>',
    '<w:right w:w="100" w:type="dxa"/>',
    '</w:tcMar>',
    '</w:tcPr>',
    '<w:p>',
    '<w:pPr>',
    '<w:spacing w:before="0" w:after="0" w:line="300" w:lineRule="auto"/>',
    '<w:ind w:left="0" w:right="0" w:firstLine="0" w:hanging="0"/>',
    '<w:jc w:val="center"/>',
    '</w:pPr>',
    '<w:r>',
    '<w:rPr>',
    `<w:rFonts w:hint="eastAsia" w:eastAsia="${header ? '黑体' : '宋体'}" w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>`,
    `<w:sz w:val="${fontSize}"/>`,
    `<w:szCs w:val="${fontSize}"/>`,
    ...(header ? ['<w:b/>', '<w:bCs/>'] : []),
    '</w:rPr>',
    tableCellTextXml(value),
    '</w:r>',
    '</w:p>',
    '</w:tc>',
  ].join('');
}

function tableFontSize(columnCount) {
  if (columnCount >= 9) {
    return 16;
  }
  if (columnCount >= 6) {
    return 18;
  }
  return 21;
}

function tableCellTextXml(value) {
  const parts = String(value ?? '').split(/\r?\n/);
  return parts
    .map((part, index) => `${index === 0 ? '' : '<w:br/>'}<w:t xml:space="preserve">${escapeXmlText(part)}</w:t>`)
    .join('');
}

function templateTableColumns(templateTable = {}) {
  if (Array.isArray(templateTable.columns) && templateTable.columns.length) {
    return templateTable.columns.map((column, index) => ({
      key: String(column?.key || `c${index + 1}`),
      text: String(column?.text ?? column?.label ?? `列${index + 1}`),
    }));
  }

  const headers = templateTable.headers || {};
  const keys = Object.keys(headers)
    .filter((key) => /^c\d+$/.test(key))
    .sort((left, right) => Number(left.slice(1)) - Number(right.slice(1)));
  return keys.map((key, index) => ({
    key,
    text: String(headers[key] ?? `列${index + 1}`),
  }));
}

function templateTableRows(templateTable = {}, columns = []) {
  return (templateTable.rows || []).map((row) => {
    if (row && typeof row === 'object' && !Array.isArray(row)) {
      return columns.map((column, index) => {
        const cell = Array.isArray(row.cells)
          ? row.cells.find((candidate) => candidate?.key === column.key) || row.cells[index]
          : null;
        return String(cell?.text ?? row[column.key] ?? '');
      });
    }
    if (Array.isArray(row)) {
      return columns.map((_column, index) => String(row[index] ?? ''));
    }
    return columns.map((column) => (column.key === 'c1' ? String(row ?? '') : ''));
  });
}

function findRenderedTableBlock({ documentXml, title, consumedRanges }) {
  const normalizedTitle = String(title || '').trim();
  if (!normalizedTitle) {
    return null;
  }

  for (const paragraph of paragraphRanges(documentXml)) {
    if (
      !hasTableSequenceField(paragraph.xml) ||
      !paragraph.text.includes(normalizedTitle) ||
      rangeOverlaps(paragraph, consumedRanges)
    ) {
      continue;
    }
    const containingTableRange = findContainingTableRange(documentXml, paragraph);
    if (containingTableRange && !rangeOverlaps(containingTableRange, consumedRanges)) {
      return {
        start: containingTableRange.start,
        end: containingTableRange.end,
        xml: String(documentXml || '').slice(containingTableRange.start, containingTableRange.end),
      };
    }
    const tableRange = findFirstTableRangeAfter(documentXml, paragraph.end);
    if (!tableRange) {
      continue;
    }
    return {
      start: paragraph.start,
      end: tableRange.end,
      xml: String(documentXml || '').slice(paragraph.start, tableRange.end),
    };
  }
  return null;
}

function rangeOverlaps(range, consumedRanges) {
  return consumedRanges.some((consumed) => range.start < consumed.end && range.end > consumed.start);
}

function tableCaptionMetadata(table, blockId = '') {
  const tokens = [
    'docx-engine-v2',
    'tableCaption',
    `tableId=${safeMetadataValue(table.tableId)}`,
  ];
  if (blockId) {
    tokens.push(`blockId=${safeMetadataValue(blockId)}`);
  }
  for (const key of ['sectionId', 'afterBlockId']) {
    const value = safeMetadataValue(table[key]);
    if (value) {
      tokens.push(`${key}=${value}`);
    }
  }
  return tokens.join(' ');
}

function removeUnboundTableBlocks(documentXml) {
  const removals = [];
  const paragraphs = paragraphRanges(documentXml);
  for (const paragraph of paragraphs) {
    if (!hasTableSequenceField(paragraph.xml) || /\btableCaption\b/.test(paragraph.text)) {
      continue;
    }
    const tableRange = findFirstTableRangeAfter(documentXml, paragraph.end);
    removals.push({
      start: paragraph.start,
      end: tableRange ? tableRange.end : paragraph.end,
    });
  }

  return removeRanges(documentXml, removals);
}

function replaceStaticDirectories(documentXml, renderPlan) {
  const replacements = [];
  for (const paragraph of paragraphRanges(documentXml)) {
    if (isMainTocParagraph(paragraph.xml)) {
      replacements.push({ start: paragraph.start, end: paragraph.end, xml: staticMainDirectoryXml(renderPlan) });
      continue;
    }
    if (isTableTocParagraph(paragraph.xml)) {
      replacements.push({ start: paragraph.start, end: paragraph.end, xml: staticTableDirectoryXml(renderPlan) });
      continue;
    }
    if (isFigureTocParagraph(paragraph.xml)) {
      replacements.push({ start: paragraph.start, end: paragraph.end, xml: staticFigureDirectoryXml(renderPlan) });
    }
  }

  let nextXml = documentXml;
  for (const replacement of replacements.sort((left, right) => right.start - left.start)) {
    nextXml = `${nextXml.slice(0, replacement.start)}${replacement.xml}${nextXml.slice(replacement.end)}`;
  }
  return nextXml;
}

function isMainTocParagraph(paragraphXml) {
  return /TOC\s+\\o\s+"1-7"/.test(String(paragraphXml || '')) || /更新主目录后显示章节条目/.test(String(paragraphXml || ''));
}

function isTableTocParagraph(paragraphXml) {
  return /TOC\s+\\h\s+\\z\s+\\c\s+&quot;表&quot;/.test(String(paragraphXml || '')) || /TOC\s+\\h\s+\\z\s+\\c\s+"表"/.test(String(paragraphXml || '')) || /更新表目录后显示表题条目/.test(String(paragraphXml || ''));
}

function isFigureTocParagraph(paragraphXml) {
  return /TOC\s+\\h\s+\\z\s+\\c\s+&quot;图&quot;/.test(String(paragraphXml || '')) || /TOC\s+\\h\s+\\z\s+\\c\s+"图"/.test(String(paragraphXml || '')) || /更新图目录后显示图题条目/.test(String(paragraphXml || ''));
}

function staticMainDirectoryXml(renderPlan) {
  const sections = renderPlan?.templateData?.sections || renderPlan?.sections || [];
  const pageNumbers = estimatedSectionPageNumbers(renderPlan);
  const entries = sections
    .filter((section) => String(section?.title || '').trim())
    .map((section, index) => ({
      left: `${chineseChapterLabel(index + 1)}    ${cleanDirectoryTitle(section.title)}`,
      right: String(pageNumberForSection(pageNumbers, section, index)),
      bookmark: sectionBookmarkName(index),
    }));
  return staticDirectoryEntriesXml(entries, '暂无章节条目');
}

function staticTableDirectoryXml(renderPlan) {
  const pageNumbers = estimatedSectionPageNumbers(renderPlan);
  const entries = (renderPlan?.tables || []).map((table, index) => ({
    left: `表 ${index + 1} ${tableCaptionText(table.title || table.caption || `表格 ${index + 1}`, index + 1)}`,
    right: String(pageNumberForSectionId(pageNumbers, table.sectionId, index)),
    bookmark: tableBookmarkName(index),
  }));
  return staticDirectoryEntriesXml(entries, '暂无表目录条目');
}

function staticFigureDirectoryXml(renderPlan) {
  const pageNumbers = estimatedSectionPageNumbers(renderPlan);
  const entries = (renderPlan?.templateData?.images || []).map((image, index) => ({
    left: `图 ${index + 1} ${figureCaptionText(image.caption || image.figureId || `图 ${index + 1}`, index + 1)}`,
    right: String(pageNumberForSectionId(pageNumbers, image.metadata?.sectionId || image.sectionId, index)),
    bookmark: figureBookmarkName(index),
  }));
  return staticDirectoryEntriesXml(entries, '暂无图目录条目');
}

function addDirectoryBookmarks(documentXml, renderPlan) {
  const targets = directoryBookmarkTargets(renderPlan);
  if (targets.length === 0) {
    return documentXml;
  }

  let nextBookmarkId = nextBookmarkIdStart(documentXml);
  const usedRanges = [];
  const replacements = [];
  const paragraphs = paragraphRanges(documentXml);
  for (const target of targets) {
    const paragraph = findDirectoryBookmarkParagraph({ paragraphs, target, usedRanges });
    if (!paragraph || paragraph.xml.includes(`w:name="${target.bookmark}"`)) {
      continue;
    }
    replacements.push({
      start: paragraph.start,
      end: paragraph.end,
      xml: paragraphXmlWithBookmark(paragraph.xml, nextBookmarkId, target.bookmark),
    });
    nextBookmarkId += 1;
    usedRanges.push({ start: paragraph.start, end: paragraph.end });
  }

  let nextXml = documentXml;
  for (const replacement of replacements.sort((left, right) => right.start - left.start)) {
    nextXml = `${nextXml.slice(0, replacement.start)}${replacement.xml}${nextXml.slice(replacement.end)}`;
  }
  return nextXml;
}

function directoryBookmarkTargets(renderPlan) {
  const sections = renderPlan?.templateData?.sections || renderPlan?.sections || [];
  const targets = sections.map((section, index) => ({
    type: 'section',
    title: section.title,
    bookmark: sectionBookmarkName(index),
  }));
  targets.push(
    ...(renderPlan?.tables || []).map((table, index) => ({
      type: 'table',
      tableId: table.tableId,
      title: table.title,
      bookmark: tableBookmarkName(index),
    }))
  );
  targets.push(
    ...(renderPlan?.templateData?.images || []).map((image, index) => ({
      type: 'figure',
      figureId: image.figureId,
      title: image.caption,
      bookmark: figureBookmarkName(index),
    }))
  );
  return targets.filter((target) => target.bookmark);
}

function findDirectoryBookmarkParagraph({ paragraphs, target, usedRanges }) {
  const contentStart = lastDirectoryEntryEnd(paragraphs);
  return paragraphs.find((paragraph) => {
    if (paragraph.start <= contentStart || rangeOverlaps(paragraph, usedRanges)) {
      return false;
    }
    if (/\bdirectoryEntry\b/.test(paragraph.text)) {
      return false;
    }
    if (target.type === 'section') {
      return paragraphMatchesDirectorySection(paragraph.text, target.title);
    }
    if (target.type === 'table') {
      return /\btableCaption\b/.test(paragraph.text) && paragraph.text.includes(`tableId=${safeMetadataValue(target.tableId)}`);
    }
    if (target.type === 'figure') {
      return /\bfigureCaption\b/.test(paragraph.text) && paragraph.text.includes(`figureId=${safeMetadataValue(target.figureId)}`);
    }
    return false;
  }) || null;
}

function lastDirectoryEntryEnd(paragraphs) {
  const directoryParagraphs = paragraphs.filter((paragraph) => /\bdirectoryEntry\b/.test(paragraph.text));
  const last = directoryParagraphs[directoryParagraphs.length - 1];
  return last ? last.end : 0;
}

function paragraphMatchesDirectorySection(paragraphTextValue, sectionTitle) {
  const paragraphTitle = cleanDirectoryTitle(paragraphTextValue);
  const expectedTitle = cleanDirectoryTitle(sectionTitle);
  return Boolean(expectedTitle) && (paragraphTitle === expectedTitle || paragraphTitle.includes(expectedTitle));
}

function nextBookmarkIdStart(documentXml) {
  const ids = [...String(documentXml || '').matchAll(/<w:bookmarkStart\b[^>]*\bw:id="(\d+)"/g)]
    .map((match) => Number(match[1]))
    .filter((value) => Number.isInteger(value));
  return Math.max(7000, ...ids, 0) + 1;
}

function paragraphXmlWithBookmark(paragraphXml, bookmarkId, bookmarkName) {
  const start = `<w:bookmarkStart w:id="${bookmarkId}" w:name="${bookmarkName}"/>`;
  const end = `<w:bookmarkEnd w:id="${bookmarkId}"/>`;
  return String(paragraphXml || '')
    .replace(/(<w:p\b[^>]*>)/, `$1${start}`)
    .replace('</w:p>', `${end}</w:p>`);
}

function sectionBookmarkName(index) {
  return `DocxEngineV2Section${String(index + 1).padStart(3, '0')}`;
}

function tableBookmarkName(index) {
  return `DocxEngineV2Table${String(index + 1).padStart(3, '0')}`;
}

function figureBookmarkName(index) {
  return `DocxEngineV2Figure${String(index + 1).padStart(3, '0')}`;
}

function estimatedSectionPageNumbers(renderPlan) {
  const sections = renderPlan?.templateData?.sections || renderPlan?.sections || [];
  const pageNumbers = new Map();
  let consumedUnits = 0;
  sections.forEach((section, index) => {
    const pageNumber = ESTIMATED_BODY_START_PAGE + Math.floor(consumedUnits / ESTIMATED_PAGE_UNITS);
    const keys = [
      sectionDirectoryKey(section, index),
      section.sectionId,
      section.id,
      section.title,
    ];
    for (const key of keys) {
      const normalizedKey = String(key || '').trim();
      if (normalizedKey) {
        pageNumbers.set(normalizedKey, pageNumber);
      }
    }
    consumedUnits += estimatedSectionUnits(section);
  });
  return pageNumbers;
}

function pageNumberForSection(pageNumbers, section, index = 0) {
  return pageNumbers.get(sectionDirectoryKey(section, index)) || ESTIMATED_BODY_START_PAGE + index;
}

function pageNumberForSectionId(pageNumbers, sectionId, index = 0) {
  const normalizedSectionId = String(sectionId || '').trim();
  return (normalizedSectionId && pageNumbers.get(normalizedSectionId)) || ESTIMATED_BODY_START_PAGE + index;
}

function sectionDirectoryKey(section, index = 0) {
  return String(section?.sectionId || section?.id || section?.title || `section-${index + 1}`).trim();
}

function estimatedSectionUnits(section = {}) {
  const blocks = Array.isArray(section.blocks) ? section.blocks : [];
  if (blocks.length === 0) {
    return 4;
  }
  return Math.max(4, blocks.reduce((sum, block) => sum + estimatedBlockUnits(block), 2));
}

function estimatedBlockUnits(block = {}) {
  if (block.tableId || block.type === 'table') {
    const rowCount = Number(block.rowCount || block.rows?.length || 0);
    return 5 + Math.max(1, rowCount) * 1.2;
  }
  if (block.figureId || block.type === 'figure' || block.type === 'image') {
    return 9;
  }
  if (block.type === 'heading') {
    return 2;
  }
  const text = String(block.text || block.content?.text || '');
  return Math.max(1, Math.ceil(text.length / 140));
}

function chineseChapterLabel(index) {
  return `第${chineseNumber(index)}章`;
}

function chineseNumber(value) {
  const number = Math.max(1, Math.floor(Number(value) || 1));
  const digits = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
  if (number <= 10) {
    return number === 10 ? '十' : digits[number];
  }
  if (number < 20) {
    return `十${digits[number % 10]}`;
  }
  if (number < 100) {
    const tens = Math.floor(number / 10);
    const ones = number % 10;
    return `${digits[tens]}十${ones ? digits[ones] : ''}`;
  }
  return String(number);
}

function cleanDirectoryTitle(value) {
  return String(value || '')
    .trim()
    .replace(/^第[一二三四五六七八九十百千万0-9]+[章节篇部分]\s*[、:：.]?\s*/, '')
    .replace(/^[一二三四五六七八九十百千万]+[、.．]\s*/, '')
    .replace(/^\d+(?:\.\d+)*[、.．]?\s+/, '')
    .trim();
}

function staticDirectoryEntriesXml(entries, emptyText) {
  const normalizedEntries = (entries || []).filter((entry) => String(entry?.left || '').trim());
  if (normalizedEntries.length === 0) {
    return staticDirectoryEntryParagraph(emptyText, '');
  }
  return normalizedEntries
    .map((entry) => staticDirectoryEntryParagraph(entry.left, entry.right, entry.bookmark))
    .join('');
}

function staticDirectoryEntryParagraph(left, right = '', bookmark = '') {
  const rightText = String(right || '').trim();
  return [
    '<w:p>',
    '<w:pPr>',
    '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>',
    '<w:ind w:left="0" w:right="0" w:firstLine="0" w:hanging="0"/>',
    `<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="${DIRECTORY_TAB_POS_DXA}"/></w:tabs>`,
    '</w:pPr>',
    '<w:r><w:rPr><w:vanish/></w:rPr><w:t>docx-engine-v2 directoryEntry</w:t></w:r>',
    `<w:r><w:t>${escapeXmlText(String(left || '').trim())}</w:t></w:r>`,
    ...(rightText ? staticDirectoryPageRuns(rightText, bookmark) : []),
    '</w:p>',
  ].join('');
}

function staticDirectoryPageRuns(rightText, bookmark = '') {
  const normalizedBookmark = String(bookmark || '').trim();
  if (!normalizedBookmark) {
    return ['<w:r><w:tab/></w:r>', `<w:r><w:t>${escapeXmlText(rightText)}</w:t></w:r>`];
  }
  return [
    '<w:r><w:tab/></w:r>',
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>',
    `<w:r><w:instrText xml:space="preserve"> PAGEREF ${escapeXmlText(normalizedBookmark)} \\h </w:instrText></w:r>`,
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>',
    `<w:r><w:t>${escapeXmlText(rightText)}</w:t></w:r>`,
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
  ];
}

function hasTableSequenceField(paragraphXml) {
  return /<w:instrText\b[^>]*>[^<]*SEQ\s+表(?:\s|<|$)/.test(String(paragraphXml || ''));
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
  const source = String(documentXml || '');
  const paragraphStartPattern = /<w:p\b[^>]*\/>|<w:p\b[^>]*>/g;
  let match;
  while ((match = paragraphStartPattern.exec(source))) {
    const start = match.index;
    let end = start + match[0].length;
    if (!/\/\s*>$/.test(match[0])) {
      const closeIndex = source.indexOf('</w:p>', paragraphStartPattern.lastIndex);
      if (closeIndex < 0) {
        continue;
      }
      end = closeIndex + '</w:p>'.length;
      paragraphStartPattern.lastIndex = end;
    }
    const xml = source.slice(start, end);
    ranges.push({
      start,
      end,
      xml,
      text: paragraphText(xml),
    });
  }
  return ranges;
}

function findFirstTableRangeAfter(documentXml, afterIndex) {
  const source = String(documentXml || '');
  const tableTagPattern = /<\/?w:tbl(?=[\s>])[^>]*>/g;
  tableTagPattern.lastIndex = Math.max(0, afterIndex || 0);
  let match;
  while ((match = tableTagPattern.exec(source))) {
    if (!isClosingTableTag(match[0])) {
      return findTableRangeAt(source, match.index);
    }
  }
  return null;
}

function findContainingTableRange(documentXml, range) {
  const source = String(documentXml || '');
  const tableTagPattern = /<\/?w:tbl(?=[\s>])[^>]*>/g;
  const stack = [];
  let match;
  while ((match = tableTagPattern.exec(source))) {
    if (match.index >= range.start) {
      break;
    }
    if (isClosingTableTag(match[0])) {
      stack.pop();
    } else {
      stack.push(match.index);
    }
  }

  const tableStart = stack[stack.length - 1];
  if (tableStart === undefined) {
    return null;
  }
  const tableRange = findTableRangeAt(source, tableStart);
  if (!tableRange || tableRange.end < range.end) {
    return null;
  }
  return tableRange;
}

function findTableRangeAt(documentXml, tableStart) {
  const source = String(documentXml || '');
  const tableTagPattern = /<\/?w:tbl(?=[\s>])[^>]*>/g;
  tableTagPattern.lastIndex = tableStart;
  let depth = 0;
  let match;
  while ((match = tableTagPattern.exec(source))) {
    if (isClosingTableTag(match[0])) {
      depth -= 1;
      if (depth === 0) {
        return { start: tableStart, end: match.index + match[0].length };
      }
    } else {
      depth += 1;
    }
  }
  return null;
}

function isClosingTableTag(tag) {
  return /^<\s*\//.test(tag);
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

function updateDrawingTemplate({ drawingXml, relationshipId, figureId, docPrId, title, image = {}, metadata = {} }) {
  const docPrMetadata = figureDocPrMetadata(figureId, metadata);
  let next = String(drawingXml || '').replace(/\br:embed="[^"]+"/, `r:embed="${relationshipId}"`);
  next = next.replace(/<wp:docPr\b([^>]*?)\/>/, (match, attributes) => {
    let nextAttributes = upsertXmlAttribute(attributes, 'id', String(docPrId));
    nextAttributes = upsertXmlAttribute(nextAttributes, 'name', title || figureId);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'descr', docPrMetadata);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'title', docPrMetadata);
    return `<wp:docPr${nextAttributes}/>`;
  });
  const extent = drawingExtent(image);
  next = next.replace(/<wp:extent\b[^>]*\/>/, `<wp:extent cx="${extent.cx}" cy="${extent.cy}"/>`);
  next = next.replace(/<a:ext\b[^>]*\/>/, `<a:ext cx="${extent.cx}" cy="${extent.cy}"/>`);
  return normalizeGeneratedDrawingToInline(next);
}

function normalizeGeneratedDrawingToInline(drawingXml) {
  let next = String(drawingXml || '');
  if (!/<wp:anchor\b/.test(next)) {
    return next;
  }
  next = next.replace(/<wp:anchor\b[^>]*>/, '<wp:inline distT="0" distB="0" distL="0" distR="0">');
  next = next.replace(/<\/wp:anchor>/, '</wp:inline>');
  next = next.replace(/<wp:simplePos\b[^>]*\/>/g, '');
  next = next.replace(/<wp:positionH\b[\s\S]*?<\/wp:positionH>/g, '');
  next = next.replace(/<wp:positionV\b[\s\S]*?<\/wp:positionV>/g, '');
  next = next.replace(/<wp:wrapNone\/>/g, '');
  return next;
}

function drawingExtent(image = {}) {
  const dimensions = image.dimensions || {};
  const widthPx = positiveNumber(dimensions.width) || 960;
  const heightPx = positiveNumber(dimensions.height) || 540;
  const ratio = Math.max(0.1, Math.min(10, widthPx / heightPx));
  const maxWidth = 5669280;
  const maxHeight = 6858000;
  const minWidth = image.layoutIntent === 'flowchart' ? 5000000 : 4200000;
  let cx = maxWidth;
  let cy = Math.round(cx / ratio);
  if (cy > maxHeight) {
    cy = maxHeight;
    cx = Math.round(cy * ratio);
  }
  if (cx < minWidth && ratio >= 0.35) {
    cx = minWidth;
    cy = Math.min(maxHeight, Math.round(cx / ratio));
  }
  return { cx, cy };
}

function positiveNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
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
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
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
