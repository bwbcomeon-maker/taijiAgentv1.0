const fs = require('node:fs');
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
  const absoluteAssetDir = assetDir ? path.resolve(assetDir) : '';
  const sourceBaseDir = sourcePackage.sourceRef?.path
    ? path.dirname(path.resolve(sourcePackage.sourceRef.path))
    : process.cwd();

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
      },
      displayPath: toRelativePath(workspace, displayPath),
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
    },
    displayPath: toRelativePath(workspace, displayPath),
    metadata: { ...(figure.metadata || {}) },
  };
}

function packageImage({ image, workspace, absoluteOutDir, absoluteAssetDir, sourceBaseDir }) {
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
    caption: image.caption || '',
    sectionId: image.sectionId || '',
    metadata: { ...(image.metadata || {}) },
  };
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

  return [
    '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540" role="img">',
    `  <title>${caption}</title>`,
    `  <desc>${escapedSource}</desc>`,
    '  <rect width="960" height="540" fill="#ffffff"/>',
    '  <rect x="24" y="24" width="912" height="492" rx="12" fill="#f8fafc" stroke="#94a3b8" stroke-width="2"/>',
    '  <text x="48" y="72" font-family="Arial, sans-serif" font-size="24" font-weight="700" fill="#0f172a">Mermaid diagram</text>',
    `  <text x="48" y="116" font-family="Menlo, Consolas, monospace" font-size="18" fill="#334155">${firstLine(escapedSource)}</text>`,
    '</svg>',
    '',
  ].join('\n');
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

module.exports = { packageAssets };
