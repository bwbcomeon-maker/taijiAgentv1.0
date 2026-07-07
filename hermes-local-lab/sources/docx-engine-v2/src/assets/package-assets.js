const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { rasterizeSvgToPng, sanitizeSvgText, svgDimensions } = require('./svg-rasterizer');
const { renderDeterministicMermaidSvg } = require('./mermaid-renderer');
const { validateDomainObject } = require('../domain/validate');

const QUALIFIED_DISPLAY_EXTENSIONS = new Set(['.png', '.svg', '.jpg', '.jpeg']);

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
    const vectorPath = path.join(figureDir, 'figure.svg');
    const displayPath = path.join(figureDir, 'figure.png');
    const svgText = sanitizeSvgText(renderDeterministicMermaidSvg({ figure, sourceText }));
    const dimensions = svgDimensions(svgText);

    fs.writeFileSync(sourcePath, sourceText, 'utf8');
    fs.writeFileSync(vectorPath, svgText, 'utf8');
    const raster = rasterizeSvgToPng({ svgText, pngPath: displayPath, width: dimensions.width });

    return {
      ...figure,
      figureId,
      dimensions,
      editable: {
        ...(figure.editable || {}),
        format: 'mermaid',
        sourcePath: toRelativePath(workspace, sourcePath),
        sourceSha256: sha256File(sourcePath),
      },
      displayPath: toRelativePath(workspace, displayPath),
      sha256: sha256File(displayPath),
      metadata: {
        ...(figure.metadata || {}),
        vectorDisplayPath: toRelativePath(workspace, vectorPath),
        rasterizedFrom: 'svg',
        rasterizer: '@resvg/resvg-js',
        rasterWidth: raster.width,
        rasterHeight: raster.height,
      },
    };
  }

  if (figure.sourceType === 'docx-embedded') {
    return packageDocxEmbeddedFigure({ figure, figureId, sourcePackage, workspace, figureDir });
  }

  if (figure.sourceType === 'html') {
    return packageHtmlFigure({ figure, figureId, workspace, figureDir, absoluteAssetDir, sourceBaseDir });
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

  if (extension === '.svg') {
    const vectorPath = path.join(figureDir, 'figure.svg');
    const displayPath = path.join(figureDir, 'figure.png');
    const svgText = sanitizeSvgText(fs.readFileSync(sourceDisplayPath, 'utf8'));
    const dimensions = svgDimensions(svgText);
    fs.writeFileSync(vectorPath, svgText, 'utf8');
    const raster = rasterizeSvgToPng({ svgText, pngPath: displayPath, width: dimensions.width });
    return {
      ...figure,
      figureId,
      dimensions,
      editable: {
        ...(figure.editable || {}),
        sourcePath: toRelativePath(workspace, vectorPath),
        sourceSha256: sha256File(vectorPath),
      },
      displayPath: toRelativePath(workspace, displayPath),
      sha256: sha256File(displayPath),
      metadata: {
        ...(figure.metadata || {}),
        originalSourcePath: toRelativePath(workspace, sourceDisplayPath),
        vectorDisplayPath: toRelativePath(workspace, vectorPath),
        rasterizedFrom: 'svg',
        rasterizer: '@resvg/resvg-js',
        rasterWidth: raster.width,
        rasterHeight: raster.height,
      },
    };
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

function packageHtmlFigure({ figure, figureId, workspace, figureDir, absoluteAssetDir, sourceBaseDir }) {
  const sourceHtmlPath = resolveExistingAssetPath({
    requestedPath: figure.displayPath || figure.editable?.sourcePath,
    absoluteAssetDir,
    sourceBaseDir,
  });
  if (!sourceHtmlPath || !fs.existsSync(sourceHtmlPath)) {
    throw new Error(`缺少必需 HTML 图形资产: ${figure.displayPath || figureId}`);
  }

  const htmlText = fs.readFileSync(sourceHtmlPath, 'utf8');
  const svgText = sanitizeSvgText(extractSvgFromHtml(htmlText));
  if (!svgText) {
    throw new Error(`HTML 图形资产中未找到可插入 DOCX 的 SVG: ${sourceHtmlPath}`);
  }

  const sourcePath = path.join(figureDir, 'source.html');
  const vectorPath = path.join(figureDir, 'figure.svg');
  const displayPath = path.join(figureDir, 'figure.png');
  const dimensions = svgDimensions(svgText);
  fs.copyFileSync(sourceHtmlPath, sourcePath);
  fs.writeFileSync(vectorPath, svgText, 'utf8');
  const raster = rasterizeSvgToPng({ svgText, pngPath: displayPath, width: dimensions.width });

  return {
    ...figure,
    figureId,
    dimensions,
    editable: {
      ...(figure.editable || {}),
      format: 'html',
      sourcePath: toRelativePath(workspace, sourcePath),
      sourceSha256: sha256File(sourcePath),
    },
    displayPath: toRelativePath(workspace, displayPath),
    sha256: sha256File(displayPath),
    metadata: {
      ...(figure.metadata || {}),
      originalSourcePath: toRelativePath(workspace, sourceHtmlPath),
      extractedFormat: 'svg',
      vectorDisplayPath: toRelativePath(workspace, vectorPath),
      rasterizedFrom: 'svg',
      rasterizer: '@resvg/resvg-js',
      rasterWidth: raster.width,
      rasterHeight: raster.height,
    },
  };
}

function extractSvgFromHtml(htmlText) {
  const match = String(htmlText || '').match(/<svg\b[\s\S]*?<\/svg>/i);
  if (!match) {
    return '';
  }
  let svg = match[0];
  if (!/\sxmlns=/.test(svg)) {
    svg = svg.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"');
  }
  return svg;
}

function packageDocxEmbeddedFigure({ figure, figureId, sourcePackage, workspace, figureDir }) {
  const media = findEmbeddedMediaForFigure(sourcePackage, figure);
  if (!media?.contentBase64) {
    throw new Error(`缺少 DOCX 内嵌图形资产: ${figure.displayPath || figureId}`);
  }

  const extension = path.extname(media.fileName || media.path || figure.displayPath).toLowerCase();
  if (!QUALIFIED_DISPLAY_EXTENSIONS.has(extension)) {
    throw new Error(`不支持的 DOCX 内嵌图形资产格式: ${media.path || figure.displayPath || figureId}`);
  }

  if (extension === '.svg') {
    const vectorPath = path.join(figureDir, 'figure.svg');
    const displayPath = path.join(figureDir, 'figure.png');
    const svgText = sanitizeSvgText(Buffer.from(media.contentBase64, 'base64').toString('utf8'));
    const dimensions = svgDimensions(svgText);
    fs.writeFileSync(vectorPath, svgText, 'utf8');
    const raster = rasterizeSvgToPng({ svgText, pngPath: displayPath, width: dimensions.width });
    return {
      ...figure,
      figureId,
      dimensions,
      editable: {
        ...(figure.editable || {}),
        format: 'docx-embedded',
        sourcePath: toRelativePath(workspace, vectorPath),
        sourceSha256: sha256File(vectorPath),
      },
      displayPath: toRelativePath(workspace, displayPath),
      sha256: sha256File(displayPath),
      metadata: {
        ...(figure.metadata || {}),
        mediaId: media.mediaId || '',
        mediaPath: media.path || '',
        originalContentType: media.contentType || '',
        vectorDisplayPath: toRelativePath(workspace, vectorPath),
        rasterizedFrom: 'svg',
        rasterizer: '@resvg/resvg-js',
        rasterWidth: raster.width,
        rasterHeight: raster.height,
      },
    };
  }

  const displayPath = path.join(figureDir, `figure${extension}`);
  fs.writeFileSync(displayPath, Buffer.from(media.contentBase64, 'base64'));

  return {
    ...figure,
    figureId,
    editable: {
      ...(figure.editable || {}),
      format: 'docx-embedded',
      sourcePath: toRelativePath(workspace, displayPath),
      sourceSha256: sha256File(displayPath),
    },
    displayPath: toRelativePath(workspace, displayPath),
    sha256: sha256File(displayPath),
    metadata: {
      ...(figure.metadata || {}),
      mediaId: media.mediaId || '',
      mediaPath: media.path || '',
      originalContentType: media.contentType || '',
    },
  };
}

function findEmbeddedMediaForFigure(sourcePackage, figure) {
  const mediaId = String(figure?.metadata?.mediaId || '').trim();
  const mediaPath = String(figure?.metadata?.mediaPath || figure?.displayPath || '').trim();
  return (sourcePackage.embeddedMedia || []).find((media) =>
    (mediaId && media.mediaId === mediaId) ||
    (mediaPath && media.path === mediaPath)
  ) || null;
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
  if (extension === '.svg') {
    const vectorPath = path.join(imageDir, path.basename(sourcePath));
    const displayPath = path.join(imageDir, `${path.basename(sourcePath, extension)}.png`);
    const svgText = sanitizeSvgText(fs.readFileSync(sourcePath, 'utf8'));
    const dimensions = svgDimensions(svgText);
    fs.writeFileSync(vectorPath, svgText, 'utf8');
    const raster = rasterizeSvgToPng({ svgText, pngPath: displayPath, width: dimensions.width });
    return {
      imageId: image.imageId,
      sourcePath: toRelativePath(workspace, vectorPath),
      displayPath: toRelativePath(workspace, displayPath),
      sha256: sha256File(displayPath),
      caption: image.caption || '',
      sectionId: image.sectionId || '',
      metadata: {
        ...(image.metadata || {}),
        originalSourcePath: toRelativePath(workspace, sourcePath),
        vectorDisplayPath: toRelativePath(workspace, vectorPath),
        rasterizedFrom: 'svg',
        rasterizer: '@resvg/resvg-js',
        rasterWidth: raster.width,
        rasterHeight: raster.height,
        dimensions,
      },
    };
  }
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

module.exports = { packageAssets, renderDeterministicMermaidSvg };
