#!/usr/bin/env node

const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { renderDeterministicMermaidSvg } = require('../assets/package-assets');
const { renderMermaidSvg } = require('../assets/package-rich-draft');
const { replaceDocxAsset } = require('../assets/replace-docx-asset');
const { refreshDeliveryPackageFileHashes } = require('../delivery/file-hashes');
const { validateDeliveryPackage } = require('../validation/validate-delivery-package');

main();

async function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const result = await renderFigureAsset(args);
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

async function renderFigureAsset({ manifest, figureId }) {
  const manifestPath = path.resolve(manifest);
  const manifestDoc = readJson(manifestPath);
  if (isRichDraftManifest(manifestDoc)) {
    return renderRichDraftFigureAsset({ manifestPath, manifestDoc, figureId });
  }
  return renderDeliveryFigureAsset({ manifestPath, renderPlan: manifestDoc, figureId });
}

async function renderDeliveryFigureAsset({ manifestPath, renderPlan, figureId }) {
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
  const outputPath = resolvePackagePath(deliveryDir, displayPath);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  const sourceText = fs.readFileSync(sourcePath, 'utf8');
  fs.writeFileSync(outputPath, renderDeterministicMermaidSvg({ figure, sourceText }), 'utf8');
  const displaySha256 = sha256File(outputPath);
  const sourceSha256 = sha256File(sourcePath);

  updateDeliveryAssetPackage({ deliveryDir, figureId, displayPath, displaySha256, sourceSha256 });
  updateDeliveryRenderPlan({ manifestPath, renderPlan, figureId, displayPath, displaySha256 });
  await updateDeliveryDocument({ deliveryDir, figureId, imagePath: outputPath });
  invalidateReplayEvidence({ deliveryDir });
  writePendingQualityReport({ deliveryDir });
  refreshDeliveryPackageFileHashes({
    deliveryDir,
    roles: ['document', 'assetPackage', 'renderPlan', 'qualityReport'],
  });
  const qualityReport = validateDeliveryPackage({ deliveryDir, wpsVisualStatus: 'not_verified' });
  writeJson(path.join(deliveryDir, 'quality-report.json'), qualityReport);
  refreshDeliveryPackageFileHashes({ deliveryDir, roles: ['qualityReport'] });

  return {
    figureId,
    displayPath,
    outputPath,
    packageUpdated: true,
    qualityStatus: qualityReport.status,
  };
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

function updateDeliveryAssetPackage({ deliveryDir, figureId, displayPath, displaySha256, sourceSha256 }) {
  const assetPackagePath = path.join(deliveryDir, 'asset-package.json');
  const assetPackage = readJson(assetPackagePath);
  const figure = (assetPackage.figures || []).find((item) => item.figureId === figureId);
  if (!figure) {
    throw new Error(`asset-package.json 中找不到图片: ${figureId}`);
  }
  figure.displayPath = displayPath;
  figure.sha256 = displaySha256;
  figure.editable = {
    ...(figure.editable || {}),
    format: figure.editable?.format || 'mermaid',
    sourcePath: figure.editable?.sourcePath || `assets/${figureId}/source.mmd`,
    sourceSha256,
  };
  writeJson(assetPackagePath, assetPackage);
}

function updateDeliveryRenderPlan({ manifestPath, renderPlan, figureId, displayPath, displaySha256 }) {
  const figure = (renderPlan.figures || []).find((item) => item.figureId === figureId);
  if (figure) {
    figure.displayPath = displayPath;
  }
  const image = (renderPlan.templateData?.images || []).find((item) => item.figureId === figureId);
  if (!image) {
    throw new Error(`render-plan.json templateData.images 中找不到图片: ${figureId}`);
  }
  image.path = displayPath;
  image.sha256 = displaySha256;
  writeJson(manifestPath, renderPlan);
}

async function updateDeliveryDocument({ deliveryDir, figureId, imagePath }) {
  const docxPath = path.join(deliveryDir, 'document.docx');
  if (!fs.existsSync(docxPath)) {
    throw new Error(`找不到交付文档: ${docxPath}`);
  }
  const tempPath = path.join(deliveryDir, `.document.${figureId}.${process.pid}.${Date.now()}.docx`);
  try {
    await replaceDocxAsset({
      docxPath,
      figureId,
      imagePath,
      outputPath: tempPath,
    });
    fs.renameSync(tempPath, docxPath);
  } finally {
    fs.rmSync(tempPath, { force: true });
  }
}

function invalidateReplayEvidence({ deliveryDir }) {
  const deliveryPackagePath = path.join(deliveryDir, 'delivery-package.json');
  const deliveryPackage = readJson(deliveryPackagePath);
  const replayPath = normalizeRelativePackagePath(deliveryPackage.files?.replayReport || '');
  if (deliveryPackage.files) {
    delete deliveryPackage.files.replayReport;
  }
  if (deliveryPackage.fileSha256) {
    delete deliveryPackage.fileSha256.replayReport;
  }
  writeJson(deliveryPackagePath, deliveryPackage);
  if (replayPath) {
    fs.rmSync(path.join(deliveryDir, replayPath), { force: true });
  }
}

function writePendingQualityReport({ deliveryDir }) {
  writeJson(path.join(deliveryDir, 'quality-report.json'), {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'not_verified',
    checks: [],
    warnings: ['Delivery package was rerendered and requires replay plus WPS/Word visual validation.'],
    failures: [],
  });
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

function normalizeRelativePackagePath(value) {
  const normalized = normalizeRelativePath(value);
  if (!normalized || normalized.split('/').includes('..')) {
    return '';
  }
  return normalized;
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
