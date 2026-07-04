const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { validateDomainObject } = require('../domain/validate');

const QUALIFIED_DISPLAY_EXTENSIONS = new Set(['.png', '.svg']);

function packageAssets({ sourcePackage, assetDir = '', outDir } = {}) {
  if (!sourcePackage) {
    throw new Error('sourcePackage is required.');
  }
  if (!outDir) {
    throw new Error('outDir is required.');
  }

  const absoluteOutDir = path.resolve(outDir);
  assertEmptyOutputDirectory(absoluteOutDir);
  fs.mkdirSync(absoluteOutDir, { recursive: true });

  const workspace = path.dirname(absoluteOutDir);
  const sourceBaseDir = sourcePackage.sourceRef?.path
    ? path.dirname(path.resolve(sourcePackage.sourceRef.path))
    : process.cwd();
  const absoluteAssetDir = resolveAssetDir(assetDir, sourceBaseDir);

  const assetPackage = {
    schemaVersion: 'docx-engine-v2/asset-package',
    assetDir: toRelativePath(workspace, absoluteOutDir),
    figures: (sourcePackage.figures || []).map((figure, index) =>
      packageFigure({ figure, index, sourcePackage, workspace, absoluteOutDir, absoluteAssetDir, sourceBaseDir })
    ),
    tables: (sourcePackage.tables || []).map((table) => ({
      ...table,
      metadata: { ...(table.metadata || {}) },
    })),
    images: (sourcePackage.images || []).map((image) =>
      packageImage({ image, workspace, absoluteOutDir, absoluteAssetDir, sourceBaseDir })
    ),
    warnings: [],
  };

  assertValidDomainObject('AssetPackage', assetPackage);
  return assetPackage;
}

function assertEmptyOutputDirectory(outDir) {
  if (fs.existsSync(outDir) && fs.readdirSync(outDir).length > 0) {
    throw new Error(`输出目录非空: ${outDir}`);
  }
}

function packageFigure({
  figure,
  index,
  sourcePackage,
  workspace,
  absoluteOutDir,
  absoluteAssetDir,
  sourceBaseDir,
}) {
  const figureId = figure.figureId || nextId('fig', index + 1);
  assertSafeAssetId(figureId, 'figureId');
  const figureDir = path.join(absoluteOutDir, figureId);
  fs.mkdirSync(figureDir, { recursive: true });

  if (figure.sourceType === 'mermaid') {
    const sourceText = resolveMermaidSourceText(sourcePackage, figure);
    const sourcePath = path.join(figureDir, 'source.mmd');
    const displayPath = path.join(figureDir, 'figure.svg');

    fs.writeFileSync(sourcePath, sourceText, 'utf8');
    fs.writeFileSync(displayPath, renderDeterministicMermaidSvg({ figure, sourceText }), 'utf8');

    return {
      ...figure,
      figureId,
      editable: {
        ...(figure.editable || {}),
        format: 'mermaid',
        sourcePath: toRelativePath(workspace, sourcePath),
        sourceSha256: sha256File(sourcePath),
      },
      displayPath: toRelativePath(workspace, displayPath),
      sha256: sha256File(displayPath),
      metadata: { ...(figure.metadata || {}) },
    };
  }

  const sourceDisplayPath = resolveExistingAssetPath({
    requestedPath: figure.displayPath,
    absoluteAssetDir,
    sourceBaseDir,
  });
  if (!sourceDisplayPath || !fs.existsSync(sourceDisplayPath)) {
    throw new Error(`缺少必需图形资产: ${figure.displayPath || figureId}`);
  }

  const extension = path.extname(sourceDisplayPath).toLowerCase();
  if (!QUALIFIED_DISPLAY_EXTENSIONS.has(extension)) {
    throw new Error(`不支持的图形资产格式: ${sourceDisplayPath}`);
  }

  const displayPath = path.join(figureDir, `figure${extension}`);
  fs.copyFileSync(sourceDisplayPath, displayPath);

  return {
    ...figure,
    figureId,
    editable: {
      ...(figure.editable || {}),
      sourcePath: toRelativePath(workspace, sourceDisplayPath),
      sourceSha256: sha256File(sourceDisplayPath),
    },
    displayPath: toRelativePath(workspace, displayPath),
    sha256: sha256File(displayPath),
    metadata: { ...(figure.metadata || {}) },
  };
}

function packageImage({ image, workspace, absoluteOutDir, absoluteAssetDir, sourceBaseDir }) {
  assertSafeAssetId(image.imageId, 'imageId');
  const sourcePath = resolveExistingAssetPath({
    requestedPath: image.path,
    absoluteAssetDir,
    sourceBaseDir,
  });
  if (!sourcePath || !fs.existsSync(sourcePath)) {
    throw new Error(`缺少必需图片资产: ${image.path || image.imageId}`);
  }

  const extension = path.extname(sourcePath).toLowerCase();
  if (!QUALIFIED_DISPLAY_EXTENSIONS.has(extension)) {
    throw new Error(`不支持的图片资产格式: ${sourcePath}`);
  }

  const imageDir = path.join(absoluteOutDir, image.imageId);
  const outputPath = path.join(imageDir, path.basename(sourcePath));
  fs.mkdirSync(imageDir, { recursive: true });
  fs.copyFileSync(sourcePath, outputPath);

  return {
    imageId: image.imageId,
    sourcePath: toRelativePath(workspace, sourcePath),
    displayPath: toRelativePath(workspace, outputPath),
    sha256: sha256File(outputPath),
    caption: image.caption || '',
    sectionId: image.sectionId || '',
    metadata: { ...(image.metadata || {}) },
  };
}

function resolveAssetDir(assetDir, sourceBaseDir) {
  if (!assetDir) {
    return '';
  }
  if (path.isAbsolute(assetDir)) {
    return assetDir;
  }

  const sourceRelative = path.resolve(sourceBaseDir, assetDir);
  if (fs.existsSync(sourceRelative)) {
    return sourceRelative;
  }

  return path.resolve(process.cwd(), assetDir);
}

function assertSafeAssetId(assetId, fieldName) {
  if (!/^[A-Za-z0-9_-]+$/.test(assetId || '')) {
    throw new Error(`不安全的资产标识: ${fieldName}=${assetId || ''}`);
  }
}

function resolveExistingAssetPath({ requestedPath, absoluteAssetDir, sourceBaseDir }) {
  if (!requestedPath) {
    return '';
  }
  if (path.isAbsolute(requestedPath)) {
    return requestedPath;
  }

  const candidates = [];
  if (absoluteAssetDir) {
    candidates.push(path.resolve(absoluteAssetDir, requestedPath));
    candidates.push(path.resolve(absoluteAssetDir, path.basename(requestedPath)));
  }
  candidates.push(path.resolve(sourceBaseDir, requestedPath));
  candidates.push(path.resolve(process.cwd(), requestedPath));

  return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0] || '';
}

function resolveMermaidSourceText(sourcePackage, figure) {
  if (figure.editable?.sourceText) {
    return figure.editable.sourceText;
  }

  const block = (sourcePackage.blocks || []).find((candidate) => candidate.metadata?.figureId === figure.figureId);
  return block?.content?.sourceText || block?.text || figure.anchorText || '';
}

function renderDeterministicMermaidSvg({ figure, sourceText }) {
  const caption = escapeXml(figure.caption || figure.figureId || 'Figure');
  const escapedSource = escapeXml(sourceText);
  const flowchart = parseMermaidFlowchart(sourceText);

  if (flowchart.nodes.length > 0) {
    return renderFlowchartSvg({ caption, escapedSource, flowchart });
  }

  return renderMermaidSourcePreviewSvg({ caption, escapedSource });
}

function renderFlowchartSvg({ caption, escapedSource, flowchart }) {
  const nodeWidth = 180;
  const nodeHeight = 72;
  const centerY = 286;
  const left = 100;
  const right = 860;
  const gap = flowchart.nodes.length > 1 ? (right - left) / (flowchart.nodes.length - 1) : 0;
  const nodeCenters = new Map();
  const nodeElements = [];
  flowchart.nodes.forEach((node, index) => {
    const cx = flowchart.nodes.length > 1 ? left + gap * index : 480;
    const cy = centerY;
    nodeCenters.set(node.id, { x: cx, y: cy });
    nodeElements.push(
      `  <rect x="${cx - nodeWidth / 2}" y="${cy - nodeHeight / 2}" width="${nodeWidth}" height="${nodeHeight}" rx="10" fill="#e0f2fe" stroke="#0369a1" stroke-width="2"/>`,
      `  <text x="${cx}" y="${cy + 6}" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#0f172a">${escapeXml(node.label)}</text>`
    );
  });

  const edgeElements = [];
  for (const edge of flowchart.edges) {
    const from = nodeCenters.get(edge.from);
    const to = nodeCenters.get(edge.to);
    if (!from || !to) {
      continue;
    }
    const fromX = from.x < to.x ? from.x + nodeWidth / 2 : from.x - nodeWidth / 2;
    const toX = from.x < to.x ? to.x - nodeWidth / 2 : to.x + nodeWidth / 2;
    edgeElements.push(
      `  <line x1="${fromX}" y1="${from.y}" x2="${toX}" y2="${to.y}" stroke="#0f766e" stroke-width="3" marker-end="url(#arrow)"/>`
    );
  }

  return [
    '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540" role="img">',
    `  <title>${caption}</title>`,
    `  <desc>${escapedSource}</desc>`,
    '  <defs>',
    '    <marker id="arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="strokeWidth">',
    '      <path d="M2,2 L10,6 L2,10 Z" fill="#0f766e"/>',
    '    </marker>',
    '  </defs>',
    '  <rect width="960" height="540" fill="#ffffff"/>',
    '  <rect x="24" y="24" width="912" height="492" rx="12" fill="#f8fafc" stroke="#94a3b8" stroke-width="2"/>',
    `  <text x="48" y="74" font-family="Arial, sans-serif" font-size="24" font-weight="700" fill="#0f172a">${caption}</text>`,
    ...edgeElements,
    ...nodeElements,
    '</svg>',
    '',
  ].join('\n');
}

function renderMermaidSourcePreviewSvg({ caption, escapedSource }) {
  return [
    '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540" role="img">',
    `  <title>${caption}</title>`,
    `  <desc>${escapedSource}</desc>`,
    '  <rect width="960" height="540" fill="#ffffff"/>',
    '  <rect x="24" y="24" width="912" height="492" rx="12" fill="#f8fafc" stroke="#94a3b8" stroke-width="2"/>',
    '  <text x="48" y="72" font-family="Arial, sans-serif" font-size="24" font-weight="700" fill="#0f172a">Mermaid source</text>',
    `  <text x="48" y="116" font-family="Menlo, Consolas, monospace" font-size="18" fill="#334155">${firstLine(escapedSource)}</text>`,
    '</svg>',
    '',
  ].join('\n');
}

function parseMermaidFlowchart(sourceText) {
  const nodesById = new Map();
  const edges = [];
  for (const rawLine of String(sourceText || '').split(/\r?\n/)) {
    const line = rawLine.trim().replace(/;$/, '');
    if (!line || /^(flowchart|graph)\b/i.test(line)) {
      continue;
    }
    const edge = line.match(/^(.+?)\s*(-->|---|==>|-\.->)\s*(.+)$/);
    if (!edge) {
      continue;
    }
    const from = parseMermaidEndpoint(edge[1]);
    const to = parseMermaidEndpoint(edge[3]);
    if (!from.id || !to.id) {
      continue;
    }
    upsertMermaidNode(nodesById, from);
    upsertMermaidNode(nodesById, to);
    edges.push({ from: from.id, to: to.id });
  }

  return { nodes: [...nodesById.values()], edges };
}

function parseMermaidEndpoint(value) {
  const endpoint = String(value || '').trim().replace(/^\|[^|]*\|/, '').replace(/\|[^|]*\|$/, '').trim();
  const match = endpoint.match(/^([A-Za-z0-9_-]+)\s*(?:\["([^"]+)"\]|\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\})?$/);
  if (!match) {
    return { id: '', label: '' };
  }
  const label = match[2] || match[3] || match[4] || match[5] || match[1];
  return { id: match[1], label: normalizeMermaidLabel(label) };
}

function upsertMermaidNode(nodesById, node) {
  const existing = nodesById.get(node.id);
  if (!existing) {
    nodesById.set(node.id, node);
    return;
  }
  if (existing.label === existing.id && node.label !== node.id) {
    existing.label = node.label;
  }
}

function normalizeMermaidLabel(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 28);
}

function firstLine(value) {
  return String(value || '').split(/\r?\n/)[0].slice(0, 80);
}

function assertValidDomainObject(schemaName, value) {
  const result = validateDomainObject(schemaName, value);
  if (!result.ok) {
    throw new Error(`${schemaName} validation failed: ${JSON.stringify(result.errors)}`);
  }
}

function toRelativePath(baseDir, targetPath) {
  const relative = path.relative(baseDir, targetPath) || path.basename(targetPath);
  return relative.split(path.sep).join('/');
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function nextId(prefix, value) {
  return `${prefix}-${String(value).padStart(3, '0')}`;
}

function escapeXml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

module.exports = { packageAssets, renderDeterministicMermaidSvg };
