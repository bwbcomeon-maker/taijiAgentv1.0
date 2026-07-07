const fs = require('node:fs');
const path = require('node:path');

const { renderDeterministicMermaidSvg } = require('./mermaid-renderer');
const { rasterizeSvgToPng, sanitizeSvgText, svgDimensions } = require('./svg-rasterizer');
const { normalizeMarkdownSource } = require('../source/normalize-markdown');

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
    let displayAsset = {};

    if (mermaidBlock) {
      const sourceMmdPath = path.join(figureDir, 'source.mmd');
      fs.writeFileSync(sourceMmdPath, `${String(mermaidBlock.text || '').trim()}\n`, 'utf8');
      if (imageQualityPasses(resolvedAssetPath)) {
        displayAsset = copyDisplayAsset({ outputDir, figureDir, sourcePath: resolvedAssetPath });
        displayPath = displayAsset.displayPath;
      } else {
        const figurePath = path.join(figureDir, 'figure.svg');
        const pngPath = path.join(figureDir, 'figure.png');
        const svgText = sanitizeSvgText(`${renderMermaidSvg({ title: caption, mermaidText: mermaidBlock.text })}\n`);
        fs.writeFileSync(
          figurePath,
          svgText,
          'utf8'
        );
        const dimensions = svgDimensions(svgText);
        const raster = rasterizeSvgToPng({ svgText, pngPath, width: dimensions.width });
        displayAsset = {
          displayPath: relativeForManifest(outputDir, pngPath),
          sourcePath: relativeForManifest(outputDir, figurePath),
          vectorDisplayPath: relativeForManifest(outputDir, figurePath),
          rasterizedFrom: 'svg',
          rasterizer: '@resvg/resvg-js',
          rasterWidth: raster.width,
          rasterHeight: raster.height,
        };
        displayPath = displayAsset.displayPath;
      }
      editable = {
        sourceType: 'mermaid',
        sourcePath: relativeForManifest(outputDir, sourceMmdPath),
      };
    } else {
      displayAsset = copyDisplayAsset({ outputDir, figureDir, sourcePath: resolvedAssetPath });
      displayPath = displayAsset.displayPath;
      editable = {
        sourceType: 'imported-image',
        sourcePath: displayAsset.sourcePath || displayPath,
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
      metadata: {
        ...(displayAsset.vectorDisplayPath ? { vectorDisplayPath: displayAsset.vectorDisplayPath } : {}),
        ...(displayAsset.rasterizedFrom ? { rasterizedFrom: displayAsset.rasterizedFrom } : {}),
        ...(displayAsset.rasterizer ? { rasterizer: displayAsset.rasterizer } : {}),
        ...(displayAsset.rasterWidth ? { rasterWidth: displayAsset.rasterWidth } : {}),
        ...(displayAsset.rasterHeight ? { rasterHeight: displayAsset.rasterHeight } : {}),
      },
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
  if (extension === '.svg') {
    const vectorPath = path.join(figureDir, 'figure.svg');
    const pngPath = path.join(figureDir, 'figure.png');
    const svgText = sanitizeSvgText(fs.readFileSync(sourcePath, 'utf8'));
    const dimensions = svgDimensions(svgText);
    fs.copyFileSync(sourcePath, vectorPath);
    const raster = rasterizeSvgToPng({ svgText, pngPath, width: dimensions.width });
    return {
      displayPath: relativeForManifest(outputDir, pngPath),
      sourcePath: relativeForManifest(outputDir, vectorPath),
      vectorDisplayPath: relativeForManifest(outputDir, vectorPath),
      rasterizedFrom: 'svg',
      rasterizer: '@resvg/resvg-js',
      rasterWidth: raster.width,
      rasterHeight: raster.height,
    };
  }
  const outputPath = path.join(figureDir, `figure${extension}`);
  fs.copyFileSync(sourcePath, outputPath);
  const displayPath = relativeForManifest(outputDir, outputPath);
  return { displayPath, sourcePath: displayPath };
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
  return renderDeterministicMermaidSvg({
    figure: { caption: title || '图示' },
    sourceText: mermaidText,
  });
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

module.exports = {
  packageRichDraft,
  renderMermaidSvg,
};
