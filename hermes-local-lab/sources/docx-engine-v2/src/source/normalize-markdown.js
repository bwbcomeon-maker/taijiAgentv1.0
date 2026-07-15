const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');

const { allocateOccurrence, logicalAssetId } = require('../assets/logical-identity');

async function normalizeMarkdownSource(options = {}) {
  const { sourcePath = 'inline.md', markdownText = '', markdown, assetManifest } = options;
  const hasMarkdownText = Object.prototype.hasOwnProperty.call(options, 'markdownText');
  const hasLegacyMarkdown = Object.prototype.hasOwnProperty.call(options, 'markdown');
  const hasSourcePath = Object.prototype.hasOwnProperty.call(options, 'sourcePath');
  let sourceText = '';
  if (hasMarkdownText) {
    sourceText = markdownText ?? '';
  } else if (hasLegacyMarkdown) {
    sourceText = markdown ?? '';
  } else if (hasSourcePath) {
    sourceText = await fs.readFile(sourcePath, 'utf8');
  }

  return bindRuntimeAssetManifest(
    normalizeMarkdownText({ sourcePath, markdownText: sourceText }),
    assetManifest
  );
}

function bindRuntimeAssetManifest(sourcePackage, assetManifest) {
  if (!assetManifest || !Array.isArray(assetManifest.assets)) {
    return sourcePackage;
  }
  sourcePackage.assetManifest = JSON.parse(JSON.stringify(assetManifest));
  const identityByBlockId = new Map();
  for (const asset of assetManifest.assets) {
    const logicalId = String(asset?.logical_asset_id || '').trim();
    if (!logicalId) {
      continue;
    }
    const occurrences = Array.isArray(asset.occurrences) ? asset.occurrences : [];
    for (const occurrence of occurrences) {
      const blockId = String(occurrence?.block_id || '').trim();
      const runtimeOccurrenceId = String(occurrence?.occurrence_id || '').trim();
      if (blockId && runtimeOccurrenceId) {
        identityByBlockId.set(blockId, { asset, logicalId, occurrence, runtimeOccurrenceId });
      }
    }
    const derivedBlockId = String(asset?.derived_from?.block_id || '').trim();
    if (derivedBlockId && occurrences.length === 1 && !identityByBlockId.has(derivedBlockId)) {
      const occurrence = occurrences[0];
      const runtimeOccurrenceId = String(occurrence?.occurrence_id || '').trim();
      if (runtimeOccurrenceId) {
        identityByBlockId.set(derivedBlockId, { asset, logicalId, occurrence, runtimeOccurrenceId });
      }
    }
  }

  for (const figure of sourcePackage.figures || []) {
    const block = (sourcePackage.blocks || []).find((candidate) => candidate.metadata?.figureId === figure.figureId);
    const identity = block ? identityByBlockId.get(block.id) : null;
    if (!identity) {
      if (assetManifest.assets.length > 0) {
        sourcePackage.warnings.push({
          code: 'asset_identity_missing',
          message: `Runtime asset manifest has no identity for ${figure.figureId}.`,
          severity: 'warning',
          figureId: figure.figureId,
        });
      }
      continue;
    }
    figure.logicalAssetId = identity.logicalId;
    figure.occurrenceId = identity.runtimeOccurrenceId;
    figure.metadata = {
      ...(figure.metadata || {}),
      logicalAssetId: identity.logicalId,
      occurrenceId: identity.runtimeOccurrenceId,
      assetRevision: Number(identity.asset?.asset_revision || 1),
      allowRepeated: identity.occurrence?.allow_repeated === true,
      identitySource: 'runtime_asset_manifest',
    };
    for (const candidate of sourcePackage.blocks || []) {
      if (candidate.metadata?.figureId !== figure.figureId) {
        continue;
      }
      candidate.metadata = {
        ...(candidate.metadata || {}),
        logicalAssetId: identity.logicalId,
        occurrenceId: identity.runtimeOccurrenceId,
        identitySource: 'runtime_asset_manifest',
      };
    }
  }
  return sourcePackage;
}

function normalizeMarkdownText({ sourcePath = 'inline.md', markdownText = '' } = {}) {
  const sourceText = markdownText ?? '';
  const context = createContext({
    sourceType: 'markdown',
    sourcePath,
    sourceText,
  });

  const lines = sourceText.split(/\r?\n/);
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    if (isMarkdownHorizontalRule(trimmed) || isCoverMetadataLine(context, trimmed)) {
      index += 1;
      continue;
    }

    const fence = trimmed.match(/^```([A-Za-z0-9_-]*)\s*$/);
    if (fence) {
      const startLine = index + 1;
      const language = fence[1].toLowerCase();
      const body = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        body.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      addCodeBlock(context, language, body.join('\n'), startLine);
      continue;
    }

    if (isMarkdownTable(lines, index)) {
      const startLine = index + 1;
      const headers = splitTableRow(lines[index]);
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      addTable(context, headers, rows, lines[startLine - 1], startLine);
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      addHeading(context, heading[2].trim(), heading[1].length, index + 1);
      index += 1;
      continue;
    }

    const image = trimmed.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$/);
    if (image) {
      addImage(context, image[1].trim(), image[2].trim(), trimmed, index + 1);
      index += 1;
      continue;
    }

    addParagraph(context, trimmed, index + 1);
    index += 1;
  }

  return toSourcePackage(context);
}

function createContext({ sourceType, sourcePath, sourceText }) {
  return {
    sourceType,
    sourcePath,
    sourceText,
    title: '',
    currentSection: null,
    sections: [],
    blocks: [],
    tables: [],
    figures: [],
    images: [],
    embeddedMedia: [],
    warnings: [],
    logicalOccurrenceCounts: new Map(),
  };
}

function addHeading(context, text, level, sourceLine) {
  const isDocumentTitle = level === 1 && !context.title;
  if (isDocumentTitle) {
    context.title = text;
  }

  const section = isDocumentTitle ? null : addSection(context, { title: text, level, sourceLine });

  const block = addBlock(context, {
    type: 'heading',
    text,
    content: { markdown: '#'.repeat(level) + ` ${text}` },
    level,
    anchorText: text,
    metadata: { sourceLine },
  });

  if (section) {
    block.sectionId = section.sectionId;
    block.sectionTitle = section.title;
  }
}

function addParagraph(context, text, sourceLine) {
  ensureCurrentSection(context, sourceLine);
  addBlock(context, {
    type: 'paragraph',
    text,
    content: { markdown: text },
    level: context.currentSection?.level || 1,
    anchorText: anchorText(text),
    metadata: { sourceLine },
  });
  addHtmlFigureReferences(context, text, sourceLine);
}

function addImage(context, caption, imagePath, markdown, sourceLine) {
  ensureCurrentSection(context, sourceLine);
  const previousBlock = context.blocks.at(-1);
  const derivedFigure = previousBlock?.type === 'mermaid'
    ? context.figures.find((figure) => figure.figureId === previousBlock.metadata?.figureId)
    : null;
  if (derivedFigure && derivedFigure.sectionId === (context.currentSection?.sectionId || '')) {
    const readableCaption = caption || derivedFigure.caption;
    derivedFigure.caption = readableCaption;
    derivedFigure.derivation = {
      sourceRole: 'mermaid_source',
      displayRole: 'derived_display',
      relation: 'derived_from',
    };
    derivedFigure.metadata = {
      ...(derivedFigure.metadata || {}),
      derivedDisplayPath: imagePath,
      derivedDisplaySourceLine: sourceLine,
    };
    previousBlock.caption = readableCaption;
    previousBlock.metadata = {
      ...(previousBlock.metadata || {}),
      derivedDisplayPath: imagePath,
    };
    addBlock(context, {
      type: 'figure-derivative',
      text: readableCaption,
      content: { markdown },
      path: imagePath,
      caption: readableCaption,
      level: context.currentSection?.level || 1,
      anchorText: markdown,
      metadata: {
        sourceLine,
        figureId: derivedFigure.figureId,
        logicalAssetId: derivedFigure.logicalAssetId,
        occurrenceId: derivedFigure.occurrenceId,
        relation: 'derived_from',
      },
    });
    return;
  }
  const imageId = nextId('image', context.images.length + 1);
  const readableCaption = caption || readableFigureCaption(context, context.images.length + 1);
  const logicalId = logicalAssetId({ sourceType: 'image', sourcePath: imagePath });
  const occurrence = allocateOccurrence(context, {
    logicalId,
    sectionKey: context.currentSection?.title || '',
  });
  context.images.push({
    imageId,
    logicalAssetId: logicalId,
    occurrenceId: occurrence,
    path: imagePath,
    caption: readableCaption,
    sectionId: context.currentSection?.sectionId || '',
    anchorText: markdown,
    metadata: { sourceLine, logicalAssetId: logicalId, occurrenceId: occurrence },
  });

  addBlock(context, {
    type: 'image',
    text: readableCaption,
    content: { markdown },
    path: imagePath,
    caption: readableCaption,
    level: context.currentSection?.level || 1,
    anchorText: markdown,
    metadata: { sourceLine, imageId, logicalAssetId: logicalId, occurrenceId: occurrence },
  });
}

function addTable(context, headers, rows, markdown, sourceLine) {
  ensureCurrentSection(context, sourceLine);
  const table = normalizeTableCells(headers, rows);
  const tableId = nextId('tbl', context.tables.length + 1);
  const title = readableTableTitle(context, context.tables.length + 1, table.headers);
  const block = addBlock(context, {
    type: 'table',
    text: table.headers.join(' | '),
    content: { headers: table.headers, rows: table.rows },
    level: context.currentSection?.level || 1,
    anchorText: markdown.trim(),
    metadata: { sourceLine, tableId },
  });

  context.tables.push({
    tableId,
    title,
    sectionId: context.currentSection?.sectionId || '',
    afterBlockId: previousRenderableBlockId(context, block.id),
    anchorText: markdown.trim(),
    headers: table.headers,
    rows: table.rows,
    metadata: { sourceLine },
  });
  table.rows.forEach((row, rowIndex) => {
    const rowText = row.join(' | ');
    addHtmlFigureReferences(context, rowText, sourceLine + rowIndex + 2);
  });
}

function addCodeBlock(context, language, sourceText, sourceLine) {
  if (language === 'mermaid') {
    ensureCurrentSection(context, sourceLine);
    const figureId = nextId('fig', context.figures.length + 1);
    const caption = readableFigureCaption(context, context.figures.length + 1);
    const logicalId = logicalAssetId({ sourceType: 'mermaid', sourceText });
    const occurrence = allocateOccurrence(context, {
      logicalId,
      sectionKey: context.currentSection?.title || '',
    });
    context.figures.push({
      figureId,
      logicalAssetId: logicalId,
      occurrenceId: occurrence,
      derivation: {
        sourceRole: 'mermaid_source',
        displayRole: 'derived_display',
        relation: 'derived_from',
      },
      caption,
      sectionId: context.currentSection?.sectionId || '',
      anchorText: anchorText(sourceText),
      sourceType: 'mermaid',
      editable: { format: 'mermaid', sourceText },
      displayPath: `assets/${figureId}/figure.svg`,
      dimensions: { width: 960, height: 540, unit: 'px' },
      quality: { status: 'not_verified', warnings: [] },
      metadata: { sourceLine, logicalAssetId: logicalId, occurrenceId: occurrence },
    });

    addBlock(context, {
      type: 'mermaid',
      text: sourceText,
      content: { language, sourceText },
      level: context.currentSection?.level || 1,
      anchorText: anchorText(sourceText),
      metadata: { sourceLine, figureId, logicalAssetId: logicalId, occurrenceId: occurrence },
    });
    return;
  }

  ensureCurrentSection(context, sourceLine);
  addBlock(context, {
    type: 'code',
    text: sourceText,
    content: { language, sourceText },
    level: context.currentSection?.level || 1,
    anchorText: anchorText(sourceText),
    metadata: { sourceLine },
  });
}

function addSection(context, { title, level, sourceLine, implicit = false }) {
  const section = {
    sectionId: nextId('sec', context.sections.length + 1),
    title,
    level,
    blockIds: [],
    metadata: { sourceLine, implicit },
  };
  context.sections.push(section);
  context.currentSection = section;
  return section;
}

function ensureCurrentSection(context, sourceLine) {
  if (context.currentSection) {
    return context.currentSection;
  }
  return addSection(context, {
    title: '概述',
    level: 2,
    sourceLine,
    implicit: true,
  });
}

function normalizeTableCells(headers = [], rows = []) {
  const sourceHeaders = Array.isArray(headers) ? headers : [];
  const sourceRows = Array.isArray(rows) ? rows.filter((row) => Array.isArray(row)) : [];
  let width = Math.max(sourceHeaders.length, ...sourceRows.map((row) => row.length), 0);

  while (width > 1 && isEmptyTableColumn(sourceHeaders, sourceRows, width - 1)) {
    width -= 1;
  }

  return {
    headers: padRow(sourceHeaders, width),
    rows: sourceRows.map((row) => padRow(row, width)),
  };
}

function isEmptyTableColumn(headers, rows, index) {
  if (String(headers[index] ?? '').trim()) {
    return false;
  }
  return rows.every((row) => !String(row[index] ?? '').trim());
}

function padRow(row, width) {
  return Array.from({ length: width }, (_value, index) => String(row[index] ?? '').trim());
}

function addHtmlFigureReferences(context, text, sourceLine) {
  for (const htmlPath of htmlPathsInText(text)) {
    const normalizedPath = htmlPath.replace(/^["'“”‘’]+|["'“”‘’。，、；;：:）)]+$/g, '');
    if (context.figures.some((figure) => figure.sourceType === 'html' && figure.displayPath === normalizedPath)) {
      continue;
    }

    const targetSection = inferHtmlFigureSection(context, text) || context.currentSection;
    const previousSection = context.currentSection;
    context.currentSection = targetSection || previousSection;
    const figureId = nextId('fig', context.figures.length + 1);
    context.figures.push({
      figureId,
      caption: inferHtmlFigureCaption(text, context.figures.length + 1),
      sectionId: context.currentSection?.sectionId || '',
      anchorText: anchorText(text),
      sourceType: 'html',
      editable: { format: 'html', sourcePath: normalizedPath },
      displayPath: normalizedPath,
      dimensions: { width: 1200, height: 900, unit: 'px' },
      quality: { status: 'not_verified', warnings: [] },
      metadata: { sourceLine, sourcePath: normalizedPath },
    });

    addBlock(context, {
      type: 'html-figure',
      text,
      content: { sourcePath: normalizedPath },
      level: context.currentSection?.level || 1,
      anchorText: anchorText(text),
      metadata: { sourceLine, figureId },
    });
    context.currentSection = previousSection;
  }
}

function htmlPathsInText(text) {
  return [...String(text || '').matchAll(/(?:^|[\s|（(])([^|\s()（）]+\.html)(?=$|[\s|）),，。；;])/gi)]
    .map((match) => match[1])
    .filter(Boolean);
}

function inferHtmlFigureSection(context, text) {
  const normalizedText = String(text || '').replace(/\s+/g, '');
  const sectionNumber = normalizedText.match(/\b(\d+(?:\.\d+)+)\b/)?.[1] || '';
  if (sectionNumber) {
    const byNumber = context.sections.find((section) => section.title.startsWith(sectionNumber));
    if (byNumber) {
      return byNumber;
    }
  }

  if (/全景架构|架构全景|目标架构/.test(normalizedText)) {
    return context.sections.find((section) => /架构.*全景|目标架构/.test(section.title)) || null;
  }

  return null;
}

function inferHtmlFigureCaption(text, index) {
  const withoutPath = String(text || '')
    .replace(/[^|\s()（）]+\.html/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
  const cells = withoutPath.split('|').map((cell) => cell.trim()).filter(Boolean);
  const preferred = cells.find((cell) => /图|架构|拓扑|流程|甘特|示意/.test(cell)) || cells[0] || '';
  return preferred || `图 ${index}`;
}

function addBlock(context, block) {
  const section = context.currentSection;
  const nextBlock = {
    id: nextId('block', context.blocks.length + 1),
    sectionId: section?.sectionId || '',
    sectionTitle: section?.title || '',
    path: `blocks.${context.blocks.length}`,
    caption: '',
    ...block,
  };
  context.blocks.push(nextBlock);
  if (section && !section.blockIds.includes(nextBlock.id)) {
    section.blockIds.push(nextBlock.id);
  }
  return nextBlock;
}

function toSourcePackage(context) {
  return {
    schemaVersion: 'docx-engine-v2/source-package',
    sourceType: context.sourceType,
    sourceRef: {
      type: context.sourceType,
      path: context.sourcePath || `inline.${context.sourceType}`,
      sha256: sha256(context.sourceText),
    },
    title: context.title || inferTitle(context),
    sections: context.sections,
    blocks: context.blocks,
    tables: context.tables,
    figures: context.figures,
    images: context.images,
    embeddedMedia: context.embeddedMedia,
    warnings: context.warnings,
  };
}

function isMarkdownTable(lines, index) {
  return (
    lines[index]?.includes('|') &&
    index + 1 < lines.length &&
    isTableSeparator(lines[index + 1])
  );
}

function isMarkdownHorizontalRule(value) {
  return /^ {0,3}(?:-{3,}|\*{3,}|_{3,})$/.test(String(value || '').trim());
}

function isCoverMetadataLine(context, value) {
  if (!context.title || context.currentSection || context.sections.length > 0) {
    return false;
  }
  const normalized = String(value || '').trim();
  return /^(?:\*\*)?(?:项目名称|文档名称|文档编号|编号|文档版本|版本|文档密级|密级|编制日期|日期|编制单位|编写单位|编制|编写|作者|审核单位|审核|审批单位|审批|状态|客户单位|客户|部门|单位)(?:\*\*)?\s*[:：]\s*\S/.test(normalized);
}

function isTableSeparator(line) {
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function previousRenderableBlockId(context, currentBlockId) {
  const previous = [...context.blocks].reverse().find((block) => block.id !== currentBlockId);
  return previous?.id || '';
}

function inferTitle(context) {
  const firstTextBlock = context.blocks.find((block) => block.text);
  return firstTextBlock?.text || path.basename(context.sourcePath || 'source');
}

function readableTableTitle(context, index, headers = []) {
  const sectionTitle = cleanSectionTitle(context.currentSection?.title || '');
  if (sectionTitle) {
    return /表$/.test(sectionTitle) ? sectionTitle : `${sectionTitle}表`;
  }

  const headerText = (headers || [])
    .map((header) => String(header || '').trim())
    .filter(Boolean)
    .slice(0, 3)
    .join('、');
  return headerText ? `${headerText}表` : `表格 ${index}`;
}

function readableFigureCaption(context, index) {
  const sectionTitle = cleanSectionTitle(context.currentSection?.title || '');
  return sectionTitle || `图示 ${index}`;
}

function cleanSectionTitle(value) {
  return String(value || '')
    .trim()
    .replace(/^第[一二三四五六七八九十百千万0-9]+[章节篇部分]\s*[、:：.]?\s*/, '')
    .replace(/^[一二三四五六七八九十百千万]+[、.．]\s*/, '')
    .replace(/^\d+(?:\.\d+)*[、.．]?\s+/, '')
    .replace(/[（(]\s*(?:C4Context|flowchart|graph|sequenceDiagram|Mermaid|SVG|PNG)[^)）]*[)）]/gi, '')
    .trim();
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

module.exports = { bindRuntimeAssetManifest, normalizeMarkdownSource, normalizeMarkdownText };
