const fs = require('node:fs');
const path = require('node:path');

const { normalizeMarkdownSource } = require('../source/normalize-markdown');

const TARGET_WIDTH = 1600;
const TARGET_HEIGHT = 900;

async function packageRichDraft({ source, outDir, assetDir = '' } = {}) {
  if (!source) {
    throw new Error('缺少必填参数: --source');
  }
  if (!outDir) {
    throw new Error('缺少必填参数: --out-dir');
  }
  const sourcePath = path.resolve(source);
  const outputDir = path.resolve(outDir);
  const sourceAssetDir = assetDir ? path.resolve(assetDir) : path.dirname(sourcePath);
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`找不到输入文件: ${sourcePath}`);
  }
  assertOutputDirIsEmpty(outputDir);

  const sourcePackage = await normalizeMarkdownSource({ sourcePath });
  if (sourcePackage.sourceType !== 'markdown') {
    throw new Error('package-rich-draft.js 当前只支持 Markdown 富内容初稿');
  }

  const imageReferences = buildImageReferences(sourcePackage);
  if (imageReferences.length === 0) {
    throw new Error('富内容初稿缺少图片引用或 Mermaid 图源，无法生成图片资产包');
  }

  fs.mkdirSync(outputDir, { recursive: true });
  const markdownFileName = path.basename(sourcePath);
  const markdownOutputPath = path.join(outputDir, markdownFileName);
  let packagedMarkdown = fs.readFileSync(sourcePath, 'utf8');
  const figures = [];

  imageReferences.forEach((reference, index) => {
    const figureId = `fig-${String(index + 1).padStart(3, '0')}`;
    const caption = textOr(reference.caption, `图片 ${index + 1}`);
    const assetDirName = `${figureId}-${slugForFileName(caption)}`;
    const figureDir = path.join(outputDir, 'assets', assetDirName);
    fs.mkdirSync(figureDir, { recursive: true });

    const mermaidBlock = reference.mermaidBlock;
    const resolvedAssetPath = resolveSourceAsset({
      sourcePath,
      assetDir: sourceAssetDir,
      rawPath: reference.path,
    });
    let displayPath = '';
    let editable = {};

    if (mermaidBlock) {
      const sourceMmdPath = path.join(figureDir, 'source.mmd');
      fs.writeFileSync(sourceMmdPath, `${String(mermaidBlock.text || '').trim()}\n`, 'utf8');
      if (imageQualityPasses(resolvedAssetPath)) {
        displayPath = copyDisplayAsset({ outputDir, figureDir, sourcePath: resolvedAssetPath });
      } else {
        const figurePath = path.join(figureDir, 'figure.svg');
        fs.writeFileSync(
          figurePath,
          `${renderMermaidSvg({ title: caption, mermaidText: mermaidBlock.text })}\n`,
          'utf8'
        );
        displayPath = relativeForManifest(outputDir, figurePath);
      }
      editable = {
        sourceType: 'mermaid',
        sourcePath: relativeForManifest(outputDir, sourceMmdPath),
      };
    } else {
      displayPath = copyDisplayAsset({ outputDir, figureDir, sourcePath: resolvedAssetPath });
      editable = {
        sourceType: 'imported-image',
        sourcePath: displayPath,
      };
    }

    packagedMarkdown = reference.generatedFromMermaid
      ? insertGeneratedImageReference(packagedMarkdown, reference, displayPath)
      : replaceImageReference(packagedMarkdown, reference, displayPath);

    figures.push({
      figureId,
      blockId: reference.blockId || '',
      caption,
      sectionId: reference.sectionId || '',
      sectionTitle: reference.sectionTitle || '',
      afterBlockId: reference.afterBlockId || '',
      anchorText: reference.anchorText || '',
      assetDir: relativeForManifest(outputDir, figureDir),
      displayPath,
      sourcePath: displayPath,
      layoutIntent: inferLayoutIntent({
        caption,
        mermaidText: mermaidBlock?.text || '',
        assetPath: reference.path,
      }),
      editable,
    });
  });

  fs.writeFileSync(markdownOutputPath, packagedMarkdown, 'utf8');
  const tables = tableManifestItems(sourcePackage);
  const manifest = {
    schemaVersion: 'rich-draft-package/v2',
    title: sourcePackage.title || path.basename(sourcePath, path.extname(sourcePath)),
    sourcePath,
    createdAt: new Date().toISOString(),
    files: {
      markdown: markdownFileName,
      manifest: 'draft.manifest.json',
      imageList: '图片清单.md',
    },
    sections: sourcePackage.sections || [],
    blocks: manifestBlocks(sourcePackage, tables, figures),
    figures,
    tables,
    quality: {
      figures: figures.length,
      tables: tables.length,
      hasEditableFigureSources: figures.every((figure) => Boolean(figure.editable?.sourcePath)),
    },
  };

  fs.writeFileSync(path.join(outputDir, 'draft.manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  fs.writeFileSync(path.join(outputDir, '图片清单.md'), buildImageList(manifest), 'utf8');

  return {
    outDir: outputDir,
    figures: figures.length,
    tables: tables.length,
  };
}

function assertOutputDirIsEmpty(outDir) {
  if (!fs.existsSync(outDir)) {
    return;
  }
  const entries = fs.readdirSync(outDir).filter((entry) => entry !== '.DS_Store');
  if (entries.length > 0) {
    throw new Error(`输出目录非空: ${outDir}。请更换新的初稿包目录，避免旧图片资产混入本次交付。`);
  }
}

function buildImageReferences(sourcePackage) {
  const blocks = sourcePackage.blocks || [];
  const references = [];
  const usedMermaidBlocks = new Set();
  for (const block of blocks) {
    if (block.type !== 'image') {
      continue;
    }
    const mermaidBlock = findNearestMermaidBlock(blocks, block);
    if (mermaidBlock) {
      usedMermaidBlocks.add(mermaidBlock.id);
    }
    references.push({
      caption: block.caption || block.text || '图片',
      path: block.path || '',
      blockId: block.id || '',
      sectionId: block.sectionId || '',
      sectionTitle: block.sectionTitle || '',
      afterBlockId: previousBlockId(blocks, block.id),
      anchorText: block.anchorText || '',
      mermaidBlock,
      generatedFromMermaid: false,
    });
  }

  for (const block of blocks) {
    if (block.type !== 'mermaid' || usedMermaidBlocks.has(block.id)) {
      continue;
    }
    references.push({
      caption: block.sectionTitle || block.anchorText || '图示',
      path: '',
      blockId: block.id || '',
      sectionId: block.sectionId || '',
      sectionTitle: block.sectionTitle || '',
      afterBlockId: previousBlockId(blocks, block.id),
      anchorText: block.anchorText || '',
      mermaidBlock: block,
      mermaidOrdinal: mermaidOrdinalBefore(blocks, block),
      generatedFromMermaid: true,
    });
  }

  return references;
}

function findNearestMermaidBlock(blocks, imageBlock) {
  const imageIndex = blocks.findIndex((block) => block.id === imageBlock.id);
  if (imageIndex < 0) {
    return null;
  }
  for (let index = imageIndex - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if (block.sectionId !== imageBlock.sectionId) {
      return null;
    }
    if (block.type === 'mermaid') {
      return block;
    }
  }
  return null;
}

function previousBlockId(blocks, blockId) {
  const index = blocks.findIndex((block) => block.id === blockId);
  if (index <= 0) {
    return '';
  }
  return blocks[index - 1].id || '';
}

function mermaidOrdinalBefore(blocks, targetBlock) {
  let ordinal = 0;
  for (const block of blocks) {
    if (block.id === targetBlock.id) {
      return ordinal;
    }
    if (block.type === 'mermaid') {
      ordinal += 1;
    }
  }
  return ordinal;
}

function resolveSourceAsset({ sourcePath, assetDir, rawPath }) {
  const assetPath = normalizeAssetTarget(rawPath);
  if (!assetPath || /^(https?:|data:)/i.test(assetPath)) {
    return '';
  }
  if (path.isAbsolute(assetPath)) {
    return assetPath;
  }
  const sourceRelative = path.resolve(path.dirname(sourcePath), assetPath);
  if (fs.existsSync(sourceRelative)) {
    return sourceRelative;
  }
  const assetDirRelative = path.resolve(assetDir, assetPath);
  if (fs.existsSync(assetDirRelative)) {
    return assetDirRelative;
  }
  return sourceRelative;
}

function normalizeAssetTarget(rawPath) {
  return String(rawPath || '')
    .trim()
    .replace(/^<|>$/g, '')
    .replace(/\s+["'][\s\S]*["']\s*$/, '')
    .trim();
}

function copyDisplayAsset({ outputDir, figureDir, sourcePath }) {
  if (!sourcePath || !fs.existsSync(sourcePath)) {
    throw new Error(`图片资产不可读: ${sourcePath || 'unknown'}`);
  }
  const extension = path.extname(sourcePath).toLowerCase() || '.bin';
  const outputPath = path.join(figureDir, `figure${extension}`);
  fs.copyFileSync(sourcePath, outputPath);
  return relativeForManifest(outputDir, outputPath);
}

function imageQualityPasses(sourcePath) {
  if (!sourcePath || !fs.existsSync(sourcePath)) {
    return false;
  }
  const extension = path.extname(sourcePath).toLowerCase();
  if (extension !== '.svg') {
    return true;
  }
  const text = fs.readFileSync(sourcePath, 'utf8');
  const width = numericSvgAttribute(text, 'width');
  const height = numericSvgAttribute(text, 'height');
  if (!width || !height) {
    return false;
  }
  const aspectRatio = width / height;
  return width >= 600 && height >= 300 && aspectRatio >= 0.8 && aspectRatio <= 2.4;
}

function numericSvgAttribute(text, name) {
  const match = String(text || '').match(new RegExp(`\\b${name}="([0-9.]+)`));
  return match ? Number(match[1]) : 0;
}

function replaceImageReference(markdown, reference, nextPath) {
  const alt = String(reference.caption || '').trim();
  const rawPath = String(reference.path || '').trim();
  const pattern = new RegExp(`!\\[${escapeRegExp(alt)}\\]\\(${escapeRegExp(rawPath)}\\)`);
  return String(markdown || '').replace(pattern, `![${alt}](${nextPath})`);
}

function insertGeneratedImageReference(markdown, reference, nextPath) {
  const lines = String(markdown || '').split(/\r?\n/);
  const output = [];
  let insideMermaid = false;
  let mermaidOrdinal = -1;
  let inserted = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!insideMermaid && /^```\s*mermaid\b/i.test(trimmed)) {
      insideMermaid = true;
      mermaidOrdinal += 1;
      output.push(line);
      continue;
    }
    output.push(line);
    if (insideMermaid && /^```\s*$/.test(trimmed)) {
      insideMermaid = false;
      if (!inserted && mermaidOrdinal === reference.mermaidOrdinal) {
        output.push('');
        output.push(`![${reference.caption || `图示 ${mermaidOrdinal + 1}`}](${nextPath})`);
        inserted = true;
      }
    }
  }

  if (!inserted) {
    throw new Error(`无法为 Mermaid 图示插入图片引用: ${reference.caption || reference.blockId || 'unknown'}`);
  }
  return output.join('\n');
}

function tableManifestItems(sourcePackage) {
  const blocksByTableId = new Map(
    (sourcePackage.blocks || [])
      .filter((block) => block.metadata?.tableId)
      .map((block) => [block.metadata.tableId, block])
  );
  return (sourcePackage.tables || []).map((table, index) => {
    const block = blocksByTableId.get(table.tableId) || {};
    return {
      tableId: table.tableId || `tbl-${String(index + 1).padStart(3, '0')}`,
      title: table.title || table.sectionTitle || `表 ${index + 1}`,
      blockId: block.id || '',
      sectionId: table.sectionId || '',
      sectionTitle: block.sectionTitle || '',
      afterBlockId: table.afterBlockId || '',
      anchorText: table.anchorText || '',
      headers: Array.isArray(table.headers) ? table.headers : [],
      rowCount: Array.isArray(table.rows) ? table.rows.length : 0,
    };
  });
}

function manifestBlocks(sourcePackage, tables, figures) {
  const tableByBlockId = new Map(tables.map((table) => [table.blockId, table]));
  const figureByBlockId = new Map(figures.map((figure) => [figure.blockId, figure]));
  return (sourcePackage.blocks || []).map((block) => {
    const base = {
      id: block.id || '',
      type: block.type || '',
      sectionId: block.sectionId || '',
      sectionTitle: block.sectionTitle || '',
      afterBlockId: previousBlockId(sourcePackage.blocks || [], block.id),
    };
    if (block.type === 'table') {
      const table = tableByBlockId.get(block.id) || {};
      return { ...base, tableId: table.tableId || '', title: table.title || '' };
    }
    if (block.type === 'image' || figureByBlockId.has(block.id)) {
      const figure = figureByBlockId.get(block.id) || {};
      return {
        ...base,
        type: 'figure',
        figureId: figure.figureId || '',
        caption: figure.caption || block.caption || block.text || '',
        layoutIntent: figure.layoutIntent || 'normal',
      };
    }
    if (block.type === 'mermaid') {
      return { ...base, language: 'mermaid', text: block.text || '' };
    }
    return { ...base, text: block.text || '' };
  });
}

function buildImageList(manifest) {
  const lines = [
    '# 图片清单',
    '',
    '| 图 ID | 图题 | 所属章节 | 可编辑源 | 展示文件 |',
    '| --- | --- | --- | --- | --- |',
  ];
  for (const figure of manifest.figures || []) {
    lines.push(
      [
        figure.figureId,
        figure.caption,
        figure.sectionTitle || '正文',
        figure.editable?.sourcePath || '',
        figure.displayPath,
      ]
        .map((item) => String(item || '').replace(/\|/g, '\\|'))
        .join(' | ')
        .replace(/^/, '| ')
        .replace(/$/, ' |')
    );
  }
  lines.push('');
  lines.push('调整图片时优先修改可编辑源文件，再执行后续图片重渲染或 DOCX 图片替换脚本。');
  lines.push('');
  return lines.join('\n');
}

function inferLayoutIntent({ caption = '', mermaidText = '', assetPath = '' } = {}) {
  const text = `${caption}\n${mermaidText}\n${assetPath}`.toLowerCase();
  if (/\bgantt\b|甘特/.test(text)) return 'gantt';
  if (/网络|拓扑|vlan|交换机|路由|防火墙|network|topology|switch/.test(text)) return 'network';
  if (/组织|职责|架构|org|organization/.test(text)) return 'org';
  if (/流程|迁移|割接|flowchart|graph\s+(td|tb|lr|rl)/i.test(text)) return 'flowchart';
  if (/架构|architecture/.test(text)) return 'architecture';
  return 'normal';
}

function renderMermaidSvg({ title, mermaidText }) {
  const phases = parseMermaidFlow(mermaidText);
  const safePhases = phases.length > 0
    ? phases.slice(0, 6)
    : [{ title: title || '流程图', nodes: ['请根据初稿 Mermaid 内容确认流程节点'] }];
  const marginX = 70;
  const top = 135;
  const gap = 24;
  const columnWidth = (TARGET_WIDTH - marginX * 2 - gap * (safePhases.length - 1)) / safePhases.length;
  const bodyHeight = 650;
  const palette = ['#dff3ff', '#e7f8ef', '#fff1d6', '#f0eaff', '#ffe8ea', '#e7f1ff'];
  const borderPalette = ['#2787b7', '#2a8d5d', '#bf8428', '#7c64b5', '#b65d69', '#4d77b4'];

  const columns = safePhases.map((phase, index) => {
    const x = marginX + index * (columnWidth + gap);
    const nodes = (phase.nodes.length > 0 ? phase.nodes : ['待补充']).slice(0, 6);
    const nodeGap = 18;
    const nodeHeight = Math.min(72, (bodyHeight - 120 - nodeGap * (nodes.length - 1)) / nodes.length);
    const nodeXml = nodes.map((node, nodeIndex) => {
      const nodeX = x + 28;
      const nodeY = top + 92 + nodeIndex * (nodeHeight + nodeGap);
      return [
        `<rect x="${nodeX}" y="${nodeY}" width="${columnWidth - 56}" height="${nodeHeight}" rx="14" fill="#ffffff" stroke="${borderPalette[index % borderPalette.length]}" stroke-width="1.6"/>`,
        svgTextBlock(node, nodeX + (columnWidth - 56) / 2, nodeY + Math.max(32, nodeHeight / 2 - 6), {
          fontSize: 20,
          lineHeight: 23,
          maxChars: 15,
          maxLines: 2,
          fill: '#22344d',
        }),
      ].join('\n');
    }).join('\n');
    const arrow = index < safePhases.length - 1
      ? [
        `<line x1="${x + columnWidth + 6}" y1="${top + bodyHeight / 2}" x2="${x + columnWidth + gap - 8}" y2="${top + bodyHeight / 2}" stroke="#5b6b7d" stroke-width="3" stroke-linecap="round"/>`,
        `<path d="M ${x + columnWidth + gap - 8} ${top + bodyHeight / 2} l -11 -7 v 14 z" fill="#5b6b7d"/>`,
      ].join('\n')
      : '';
    return [
      `<rect x="${x}" y="${top}" width="${columnWidth}" height="${bodyHeight}" rx="24" fill="${palette[index % palette.length]}" stroke="${borderPalette[index % borderPalette.length]}" stroke-width="2"/>`,
      svgTextBlock(phase.title || `阶段 ${index + 1}`, x + columnWidth / 2, top + 48, {
        fontSize: 24,
        lineHeight: 28,
        maxChars: 13,
        maxLines: 2,
        weight: '700',
        fill: borderPalette[index % borderPalette.length],
      }),
      nodeXml,
      arrow,
    ].join('\n');
  }).join('\n');

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${TARGET_WIDTH}" height="${TARGET_HEIGHT}" viewBox="0 0 ${TARGET_WIDTH} ${TARGET_HEIGHT}">`,
    '<rect width="1600" height="900" fill="#f8fbff"/>',
    '<rect x="40" y="42" width="1520" height="816" rx="30" fill="#ffffff" stroke="#d8e4f0" stroke-width="2"/>',
    svgTextBlock(title || '流程图', 800, 88, {
      fontSize: 34,
      lineHeight: 38,
      maxChars: 24,
      maxLines: 1,
      weight: '700',
      fill: '#102a43',
    }),
    columns,
    '</svg>',
  ].join('\n');
}

function parseMermaidFlow(mermaidText) {
  const phases = [];
  let currentPhase = null;
  for (const originalLine of String(mermaidText || '').split(/\r?\n/)) {
    const line = originalLine.replace(/%%.*$/, '').trim();
    if (!line || /^(flowchart|graph)\b/i.test(line)) {
      continue;
    }
    const subgraphMatch = line.match(/^subgraph\s+[^\[]*\["([^"]+)"\]/i)
      || line.match(/^subgraph\s+[^\[]*\['([^']+)'\]/i)
      || line.match(/^subgraph\s+(.+)$/i);
    if (subgraphMatch) {
      currentPhase = { title: subgraphMatch[1].trim(), nodes: [] };
      phases.push(currentPhase);
      continue;
    }
    if (/^end$/i.test(line)) {
      currentPhase = null;
      continue;
    }
    const labels = [];
    const labelRegex = /[\w.-]+\s*(?:\["([^"]+)"\]|\['([^']+)'\]|\[([^\]]+)\]|\("([^"]+)"\)|\('([^']+)'\)|\(([^)]+)\)|\{"([^"]+)"\}|\{'([^']+)'\}|\{([^}]+)\})/g;
    let match = labelRegex.exec(line);
    while (match) {
      const label = match.slice(1).find((item) => item !== undefined);
      if (label && !labels.includes(label.trim())) {
        labels.push(label.trim());
      }
      match = labelRegex.exec(line);
    }
    if (labels.length > 0) {
      if (!currentPhase) {
        currentPhase = { title: phases.length === 0 ? '流程步骤' : `流程补充 ${phases.length + 1}`, nodes: [] };
        phases.push(currentPhase);
      }
      for (const label of labels) {
        if (!currentPhase.nodes.includes(label)) {
          currentPhase.nodes.push(label);
        }
      }
    }
  }
  return phases.filter((phase) => phase.title || phase.nodes.length > 0);
}

function svgTextBlock(text, x, y, options = {}) {
  const lines = wrapSvgText(text, options.maxChars || 16, options.maxLines || 2);
  const lineHeight = options.lineHeight || 24;
  const fontSize = options.fontSize || 22;
  const weight = options.weight ? ` font-weight="${options.weight}"` : '';
  const fill = options.fill || '#1d3557';
  const anchor = options.anchor || 'middle';
  return [
    `<text x="${x}" y="${y}" text-anchor="${anchor}" font-family="PingFang SC, Microsoft YaHei, Arial, sans-serif" font-size="${fontSize}" fill="${fill}"${weight}>`,
    ...lines.map((line, index) => `<tspan x="${x}" dy="${index === 0 ? 0 : lineHeight}">${escapeXml(line)}</tspan>`),
    '</text>',
  ].join('');
}

function wrapSvgText(text, maxCharsPerLine = 18, maxLines = 3) {
  const chars = Array.from(String(text || '').trim());
  if (chars.length === 0) {
    return [''];
  }
  const lines = [];
  for (let index = 0; index < chars.length && lines.length < maxLines; index += maxCharsPerLine) {
    lines.push(chars.slice(index, index + maxCharsPerLine).join(''));
  }
  if (chars.length > maxCharsPerLine * maxLines) {
    lines[maxLines - 1] = `${Array.from(lines[maxLines - 1]).slice(0, Math.max(1, maxCharsPerLine - 1)).join('')}...`;
  }
  return lines;
}

function slugForFileName(value) {
  const slug = String(value || 'figure')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug || 'figure';
}

function relativeForManifest(baseDir, filePath) {
  return path.relative(baseDir, filePath).split(path.sep).join('/');
}

function textOr(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function escapeXml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

module.exports = {
  packageRichDraft,
  renderMermaidSvg,
};
