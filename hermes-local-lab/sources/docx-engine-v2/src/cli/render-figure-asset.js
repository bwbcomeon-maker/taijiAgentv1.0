#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const { renderDeterministicMermaidSvg } = require('../assets/package-assets');
const { renderMermaidSvg } = require('../assets/package-rich-draft');

main();

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const result = renderFigureAsset(args);
    if (args.json) {
      process.stdout.write(`${JSON.stringify({ ok: true, ...result })}\n`);
      return;
    }
    process.stdout.write(`render-figure-assets-ok\t${result.figureId}\t${result.displayPath}\n`);
  } catch (error) {
    process.stderr.write(`render-figure-assets-failed\t${error.message}\n`);
    process.exitCode = 3;
  }
}

function renderFigureAsset({ manifest, figureId }) {
  const manifestPath = path.resolve(manifest);
  const manifestDoc = readJson(manifestPath);
  if (isRichDraftManifest(manifestDoc)) {
    return renderRichDraftFigureAsset({ manifestPath, manifestDoc, figureId });
  }
  return renderDeliveryFigureAsset({ manifestPath, renderPlan: manifestDoc, figureId });
}

function renderDeliveryFigureAsset({ manifestPath, renderPlan, figureId }) {
  const deliveryDir = path.dirname(manifestPath);
  const figure = (renderPlan.figures || []).find((item) => item.figureId === figureId);
  if (!figure) {
    throw new Error(`manifest 中找不到图片: ${figureId}`);
  }

  const sourcePath = path.join(deliveryDir, 'assets', figureId, 'source.mmd');
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`找不到可编辑源: ${sourcePath}`);
  }

  const displayPath = normalizeRelativePath(figure.displayPath || `assets/${figureId}/figure.svg`);
  const outputPath = path.join(deliveryDir, displayPath);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  const sourceText = fs.readFileSync(sourcePath, 'utf8');
  fs.writeFileSync(outputPath, renderDeterministicMermaidSvg({ figure, sourceText }), 'utf8');

  return { figureId, displayPath, outputPath };
}

function renderRichDraftFigureAsset({ manifestPath, manifestDoc, figureId }) {
  const packageDir = path.dirname(manifestPath);
  const figure = (manifestDoc.figures || []).find((item) => item.figureId === figureId);
  if (!figure) {
    throw new Error(`manifest 中找不到图片: ${figureId}`);
  }
  if (figure.editable?.sourceType !== 'mermaid') {
    throw new Error(`图片不可用 Mermaid 源重渲染: ${figureId}`);
  }

  const sourcePath = resolvePackagePath(packageDir, figure.editable?.sourcePath || '');
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`找不到可编辑源: ${sourcePath}`);
  }

  const previousDisplayPath = normalizeRelativePath(figure.displayPath || '');
  const assetDir = normalizeRelativePath(figure.assetDir || path.posix.dirname(normalizeRelativePath(figure.editable.sourcePath)));
  const displayPath = normalizeRelativePath(path.posix.join(assetDir, 'figure.svg'));
  const outputPath = resolvePackagePath(packageDir, displayPath);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  const sourceText = fs.readFileSync(sourcePath, 'utf8');
  fs.writeFileSync(
    outputPath,
    `${renderMermaidSvg({ title: figure.caption || figure.figureId, mermaidText: sourceText })}\n`,
    'utf8'
  );

  figure.displayPath = displayPath;
  figure.sourcePath = displayPath;
  writeJson(manifestPath, manifestDoc);
  updatePackagedMarkdown({ packageDir, manifestDoc, figure, previousDisplayPath, displayPath });
  updateImageList({ packageDir, manifestDoc, previousDisplayPath, displayPath });

  return { figureId, displayPath, outputPath };
}

function isRichDraftManifest(manifestDoc) {
  return manifestDoc?.schemaVersion === 'rich-draft-package/v2' || Boolean(manifestDoc?.files?.markdown);
}

function parseArgs(argv) {
  const parsed = { manifest: '', figureId: '', json: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--json') {
      parsed.json = true;
      continue;
    }
    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }
    if (arg === '--manifest') {
      parsed.manifest = next;
    } else if (arg === '--figure-id') {
      parsed.figureId = next;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }

  if (!parsed.manifest) {
    throw new Error('缺少必填参数: --manifest');
  }
  if (!parsed.figureId) {
    throw new Error('缺少必填参数: --figure-id');
  }
  if (!/^[A-Za-z0-9_-]+$/.test(parsed.figureId)) {
    throw new Error(`不安全的图片标识: ${parsed.figureId}`);
  }
  return parsed;
}

function readJson(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`找不到 manifest: ${filePath}`);
  }
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function updatePackagedMarkdown({ packageDir, manifestDoc, figure, previousDisplayPath, displayPath }) {
  const markdownFile = manifestDoc.files?.markdown;
  if (!markdownFile || !previousDisplayPath || previousDisplayPath === displayPath) {
    return;
  }
  const markdownPath = resolvePackagePath(packageDir, markdownFile);
  if (!fs.existsSync(markdownPath)) {
    return;
  }
  const markdown = fs.readFileSync(markdownPath, 'utf8');
  fs.writeFileSync(
    markdownPath,
    replaceMarkdownImagePath({
      markdown,
      caption: figure.caption || '',
      previousDisplayPath,
      displayPath,
    }),
    'utf8'
  );
}

function replaceMarkdownImagePath({ markdown, caption, previousDisplayPath, displayPath }) {
  const imagePattern = new RegExp(
    `(!\\[${escapeRegExp(caption)}\\]\\()${escapeRegExp(previousDisplayPath)}(\\))`
  );
  const replacedByCaption = String(markdown || '').replace(imagePattern, `$1${displayPath}$2`);
  if (replacedByCaption !== markdown) {
    return replacedByCaption;
  }
  return String(markdown || '').split(previousDisplayPath).join(displayPath);
}

function updateImageList({ packageDir, manifestDoc, previousDisplayPath, displayPath }) {
  const imageListFile = manifestDoc.files?.imageList;
  if (!imageListFile || !previousDisplayPath || previousDisplayPath === displayPath) {
    return;
  }
  const imageListPath = resolvePackagePath(packageDir, imageListFile);
  if (!fs.existsSync(imageListPath)) {
    return;
  }
  const imageList = fs.readFileSync(imageListPath, 'utf8');
  fs.writeFileSync(imageListPath, imageList.split(previousDisplayPath).join(displayPath), 'utf8');
}

function resolvePackagePath(packageDir, relativePath) {
  const normalizedPath = normalizeRelativePath(relativePath);
  if (!normalizedPath || normalizedPath.split('/').includes('..')) {
    throw new Error(`不安全的包内路径: ${relativePath || ''}`);
  }
  const resolvedPath = path.join(packageDir, normalizedPath);
  const relative = path.relative(packageDir, resolvedPath);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`不安全的包内路径: ${relativePath || ''}`);
  }
  return resolvedPath;
}

function normalizeRelativePath(value) {
  return String(value || '').replace(/\\/g, '/').replace(/^\/+/, '');
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
