const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');

async function normalizeMarkdownSource({ sourcePath = 'inline.md', markdown } = {}) {
  const sourceText = markdown ?? (await fs.readFile(sourcePath, 'utf8'));
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
  };
}

function addHeading(context, text, level, sourceLine) {
  if (level === 1 && !context.title) {
    context.title = text;
  }

  let section = null;
  if (level > 1) {
    section = {
      sectionId: nextId('sec', context.sections.length + 1),
      title: text,
      level,
      blockIds: [],
      metadata: { sourceLine },
    };
    context.sections.push(section);
    context.currentSection = section;
  }

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
  addBlock(context, {
    type: 'paragraph',
    text,
    content: { markdown: text },
    level: context.currentSection?.level || 1,
    anchorText: anchorText(text),
    metadata: { sourceLine },
  });
}

function addImage(context, caption, imagePath, markdown, sourceLine) {
  const imageId = nextId('image', context.images.length + 1);
  context.images.push({
    imageId,
    path: imagePath,
    caption,
    sectionId: context.currentSection?.sectionId || '',
    anchorText: markdown,
    metadata: { sourceLine },
  });

  addBlock(context, {
    type: 'image',
    text: caption,
    content: { markdown },
    path: imagePath,
    caption,
    level: context.currentSection?.level || 1,
    anchorText: markdown,
    metadata: { sourceLine, imageId },
  });
}

function addTable(context, headers, rows, markdown, sourceLine) {
  const tableId = nextId('table', context.tables.length + 1);
  const block = addBlock(context, {
    type: 'table',
    text: headers.join(' | '),
    content: { headers, rows },
    level: context.currentSection?.level || 1,
    anchorText: markdown.trim(),
    metadata: { sourceLine, tableId },
  });

  context.tables.push({
    tableId,
    title: `表格 ${context.tables.length + 1}`,
    sectionId: context.currentSection?.sectionId || '',
    afterBlockId: previousRenderableBlockId(context, block.id),
    anchorText: markdown.trim(),
    headers,
    rows,
    metadata: { sourceLine },
  });
}

function addCodeBlock(context, language, sourceText, sourceLine) {
  if (language === 'mermaid') {
    const figureId = nextId('fig', context.figures.length + 1);
    context.figures.push({
      figureId,
      caption: `图 ${context.figures.length + 1}`,
      sectionId: context.currentSection?.sectionId || '',
      anchorText: anchorText(sourceText),
      sourceType: 'mermaid',
      editable: { format: 'mermaid', sourceText },
      displayPath: `assets/${figureId}/figure.svg`,
      dimensions: { width: 960, height: 540, unit: 'px' },
      quality: { status: 'not_verified', warnings: [] },
      metadata: { sourceLine },
    });

    addBlock(context, {
      type: 'figure',
      text: sourceText,
      content: { language, sourceText },
      level: context.currentSection?.level || 1,
      anchorText: anchorText(sourceText),
      metadata: { sourceLine, figureId },
    });
    return;
  }

  addBlock(context, {
    type: 'code',
    text: sourceText,
    content: { language, sourceText },
    level: context.currentSection?.level || 1,
    anchorText: anchorText(sourceText),
    metadata: { sourceLine },
  });
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

function nextId(prefix, value) {
  return `${prefix}-${String(value).padStart(3, '0')}`;
}

function anchorText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 120);
}

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

module.exports = { normalizeMarkdownSource };
