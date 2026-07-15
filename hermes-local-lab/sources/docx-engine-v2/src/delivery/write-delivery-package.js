const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { validateDomainObject } = require('../domain/validate');
const { buildDeliveryFileSha256 } = require('./file-hashes');

function writeDeliveryPackage({
  deliveryDir,
  job,
  sourcePackage,
  templatePackage,
  assetPackage,
  renderPlan,
  documentPath,
  qualityReport,
  manifestDeliveryDir,
} = {}) {
  if (!deliveryDir) {
    throw new Error('deliveryDir is required.');
  }
  if (!documentPath) {
    throw new Error('documentPath is required.');
  }
  if (!fs.existsSync(documentPath)) {
    throw new Error(`documentPath does not exist: ${documentPath}`);
  }
  if (!sourcePackage) {
    throw new Error('sourcePackage is required.');
  }
  if (!templatePackage) {
    throw new Error('templatePackage is required.');
  }
  if (!assetPackage) {
    throw new Error('assetPackage is required.');
  }
  if (!renderPlan) {
    throw new Error('renderPlan is required.');
  }

  const normalizedJob = normalizeJob(job, renderPlan, sourcePackage);
  assertDomainObject('DocumentJob', normalizedJob, 'DocumentJob manifest');
  assertDomainObject('SourcePackage', sourcePackage, 'SourcePackage');
  assertDomainObject('TemplateManifest', templatePackage.manifest || {}, 'TemplateManifest');

  assertEmptyOutputDirectory(deliveryDir);
  fs.mkdirSync(deliveryDir, { recursive: true });

  const deliveryDocumentPath = path.join(deliveryDir, 'document.docx');
  const deliverySourcePath = path.join(deliveryDir, 'source.md');
  const deliverySourcePackagePath = path.join(deliveryDir, 'source-package.json');
  fs.copyFileSync(documentPath, deliveryDocumentPath);
  fs.writeFileSync(deliverySourcePath, sourceMarkdown(sourcePackage), 'utf8');
  writeJson(deliverySourcePackagePath, sourcePackage);
  const originalSource = copyOriginalSource({ deliveryDir, sourcePackage });
  copyAssets({ deliveryDir, sourcePackage, assetPackage });
  writeJson(path.join(deliveryDir, 'job.manifest.json'), normalizedJob);
  writeJson(path.join(deliveryDir, 'template.manifest.json'), templatePackage.manifest || {});
  const deliveryAssetPackage = normalizeAssetPackageForDelivery(assetPackage);
  writeJson(path.join(deliveryDir, 'asset-package.json'), deliveryAssetPackage);
  writeJson(path.join(deliveryDir, 'render-plan.json'), renderPlan);
  if (job?.renderInputBinding) {
    writeJson(path.join(deliveryDir, 'render-input-binding.json'), job.renderInputBinding);
  }
  writeJson(path.join(deliveryDir, 'quality-report.json'), qualityReport || emptyQualityReport());
  fs.writeFileSync(
    path.join(deliveryDir, 'README-图片调整说明.md'),
    buildImageInstructions({ assetPackage: deliveryAssetPackage }),
    'utf8'
  );

  const deliveryPackage = {
    schemaVersion: 'docx-engine-v2/delivery-package',
    deliveryDir: manifestDeliveryDir || deliveryDir,
    documentSha256: sha256File(deliveryDocumentPath),
    sourceSha256: sha256File(deliverySourcePath),
    files: {
      document: 'document.docx',
      source: 'source.md',
      sourcePackage: 'source-package.json',
      originalSource,
      assetsDir: 'assets',
      assetPackage: 'asset-package.json',
      jobManifest: 'job.manifest.json',
      templateManifest: 'template.manifest.json',
      renderPlan: 'render-plan.json',
      qualityReport: 'quality-report.json',
      imageInstructions: 'README-图片调整说明.md',
      ...(job?.renderInputBinding ? { renderInputBinding: 'render-input-binding.json' } : {}),
    },
    status: 'delivered',
  };
  deliveryPackage.fileSha256 = buildDeliveryFileSha256({
    deliveryDir,
    files: deliveryPackage.files,
  });
  if (deliveryPackage.files.renderInputBinding) {
    Object.assign(deliveryPackage.fileSha256, buildDeliveryFileSha256({
      deliveryDir,
      files: deliveryPackage.files,
      roles: ['renderInputBinding'],
    }));
  }
  deliveryPackage.documentSha256 = deliveryPackage.fileSha256.document;
  deliveryPackage.sourceSha256 = deliveryPackage.fileSha256.source;
  const validation = validateDomainObject('DeliveryPackage', deliveryPackage);
  if (!validation.ok) {
    throw new Error(`DeliveryPackage validation failed: ${JSON.stringify(validation.errors)}`);
  }
  writeJson(path.join(deliveryDir, 'delivery-package.json'), deliveryPackage);

  return deliveryPackage;
}

function copyOriginalSource({ deliveryDir, sourcePackage }) {
  const sourcePath = sourcePackage?.sourceRef?.path || '';
  if (!sourcePath || !fs.existsSync(sourcePath)) {
    throw new Error(`Original source file does not exist: ${sourcePath}`);
  }
  const originalSource = path.join('source', 'original', originalSourceFileName(sourcePackage.sourceRef));
  const targetPath = path.join(deliveryDir, originalSource);
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.copyFileSync(sourcePath, targetPath);
  return originalSource;
}

function originalSourceFileName(sourceRef = {}) {
  const baseName = path.basename(String(sourceRef.path || '')).trim();
  if (baseName && baseName !== '.' && baseName !== '..') {
    return baseName;
  }
  const extensionByType = {
    markdown: '.md',
    text: '.txt',
    docx: '.docx',
  };
  return `source${extensionByType[sourceRef.type] || ''}`;
}

function assertDomainObject(schemaName, value, label) {
  const validation = validateDomainObject(schemaName, value);
  if (!validation.ok) {
    throw new Error(`${label} validation failed: ${JSON.stringify(validation.errors)}`);
  }
}

function assertEmptyOutputDirectory(deliveryDir) {
  if (fs.existsSync(deliveryDir) && fs.readdirSync(deliveryDir).length > 0) {
    throw new Error(`输出目录非空: ${deliveryDir}`);
  }
}

function copyAssets({ deliveryDir, sourcePackage, assetPackage }) {
  const targetAssetsDir = path.join(deliveryDir, 'assets');
  fs.mkdirSync(targetAssetsDir, { recursive: true });

  const sourceAssetsDir = resolveAssetPackageDir({ sourcePackage, assetPackage, deliveryDir });
  if (!sourceAssetsDir || !fs.existsSync(sourceAssetsDir)) {
    return;
  }

  for (const relativePath of declaredAssetFilePaths(assetPackage)) {
    const normalizedPath = normalizeRelativePackagePath(relativePath);
    if (!normalizedPath || !normalizedPath.startsWith('assets/')) {
      continue;
    }
    const sourcePath = path.join(sourceAssetsDir, normalizedPath.slice('assets/'.length));
    if (!fs.existsSync(sourcePath) || !fs.statSync(sourcePath).isFile()) {
      continue;
    }
    const targetPath = path.join(deliveryDir, normalizedPath);
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fs.copyFileSync(sourcePath, targetPath);
  }
}

function resolveAssetPackageDir({ sourcePackage, assetPackage, deliveryDir }) {
  if (!assetPackage.assetDir) {
    return '';
  }
  if (path.isAbsolute(assetPackage.assetDir)) {
    return assetPackage.assetDir;
  }

  const candidates = [];
  if (sourcePackage.sourceRef?.path) {
    candidates.push(path.resolve(path.dirname(sourcePackage.sourceRef.path), assetPackage.assetDir));
  }
  candidates.push(path.resolve(path.dirname(deliveryDir), assetPackage.assetDir));
  candidates.push(path.resolve(process.cwd(), assetPackage.assetDir));
  return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0] || '';
}

function declaredAssetFilePaths(assetPackage) {
  const paths = [];
  for (const figure of assetPackage.figures || []) {
    paths.push(figure.displayPath, figure.editable?.sourcePath, figure.metadata?.vectorDisplayPath);
  }
  for (const image of assetPackage.images || []) {
    paths.push(image.displayPath, image.sourcePath, image.metadata?.vectorDisplayPath);
  }
  return paths.filter(Boolean);
}

function normalizeRelativePackagePath(value) {
  if (typeof value !== 'string' || !value.trim() || path.isAbsolute(value)) {
    return '';
  }
  const normalized = path.normalize(value).replaceAll(path.sep, '/');
  if (normalized === '..' || normalized.startsWith('../')) {
    return '';
  }
  return normalized;
}

function sourceMarkdown(sourcePackage) {
  const sourcePath = sourcePackage.sourceRef?.path;
  if (sourcePackage.sourceType === 'markdown' && sourcePath && fs.existsSync(sourcePath)) {
    const original = fs.readFileSync(sourcePath, 'utf8');
    if (original.trim()) {
      return original;
    }
  }

  const lines = [];
  if (sourcePackage.title) {
    lines.push(`# ${sourcePackage.title}`, '');
  }
  for (const block of sourcePackage.blocks || []) {
    if (block.type === 'heading') {
      lines.push(`${'#'.repeat(block.level || 2)} ${block.text || ''}`, '');
    } else if (block.type === 'paragraph') {
      lines.push(block.text || '', '');
    } else if (block.type === 'table') {
      lines.push(block.anchorText || block.text || block.id, '');
    } else if (block.type === 'image') {
      lines.push(block.content?.markdown || `![${block.caption || block.text || block.id}](${block.path || ''})`, '');
    } else if (block.type === 'mermaid') {
      lines.push('```mermaid', block.content?.sourceText || block.text || '', '```', '');
    }
  }

  const markdown = lines.join('\n').trim();
  return markdown ? `${markdown}\n` : '# Source\n\nOriginal source text was not available.\n';
}

function normalizeJob(job, renderPlan, sourcePackage) {
  return {
    ...(job || {}),
    jobId: job?.jobId || renderPlan.jobId,
    sourceRef: job?.sourceRef || sourcePackage.sourceRef,
    templateId: job?.templateId || renderPlan.templateId,
    status: job?.status || 'delivered',
    deliveredAt: job?.deliveredAt || new Date().toISOString(),
  };
}

function normalizeAssetPackageForDelivery(assetPackage) {
  return {
    ...assetPackage,
    assetDir: 'assets',
  };
}

function buildImageInstructions({ assetPackage }) {
  const lines = [
    '# 图片调整说明',
    '',
    '本交付包保留了可编辑资产和生成后的 DOCX：',
    '',
    '- `document.docx` 是生成文档。',
    '- `delivery-package.json` 是交付包清单，记录文档、源文件、模板清单、渲染计划和质量报告的位置。',
    '- `source/original/` 保存原始输入文件，`source.md` 是用于人工查看的 Markdown 副本。',
    '- `source-package.json` 是引擎归一化后的结构化源包，用于追溯章节、表格、图片和块级锚点。',
    '- `assets/` 保存图片、图形源文件和展示文件。',
    '- `asset-package.json` 记录资产包结构、可编辑源文件和展示文件。',
    '- `render-plan.json` 记录图表与模板位置绑定关系。',
    '- 如需替换图片，优先修改 `assets/` 中对应源文件，再按 `figureId` 回写或重渲染文档。',
    '',
    '## 图片清单',
    '',
  ];

  const figures = Array.isArray(assetPackage.figures) ? assetPackage.figures : [];
  const images = Array.isArray(assetPackage.images) ? assetPackage.images : [];
  if (figures.length === 0 && images.length === 0) {
    lines.push('本次交付包没有可替换的图片资产。', '');
    return `${lines.join('\n')}\n`;
  }

  if (figures.length > 0) {
    lines.push('### 图形资产', '');
    for (const figure of figures) {
      lines.push(...assetInstructionLines({
        idLabel: 'figureId',
        id: figure.figureId,
        caption: figure.caption,
        displayPath: figure.displayPath,
        sourcePath: figure.editable?.sourcePath,
        sectionId: figure.sectionId,
        logicalAssetId: figure.logicalAssetId,
        occurrenceId: figure.occurrenceId,
      }));
    }
  }

  if (images.length > 0) {
    lines.push('### 图片资产', '');
    for (const image of images) {
      lines.push(...assetInstructionLines({
        idLabel: 'imageId',
        id: image.imageId,
        caption: image.caption,
        displayPath: image.displayPath,
        sourcePath: image.displayPath,
        originPath: image.sourcePath,
        sectionId: image.sectionId,
        logicalAssetId: image.logicalAssetId,
        occurrenceId: image.occurrenceId,
      }));
    }
  }

  return `${lines.join('\n')}\n`;
}

function assetInstructionLines({ idLabel, id, caption, displayPath, sourcePath, originPath, sectionId, logicalAssetId, occurrenceId }) {
  const lines = [
    `- ${idLabel}: \`${id || 'unknown'}\``,
    `  - 标题: ${caption || '未命名图片'}`,
    `  - 展示文件: \`${displayPath || 'missing'}\``,
  ];
  if (sourcePath) {
    lines.push(`  - 可编辑/原始文件: \`${sourcePath}\``);
  }
  if (originPath && originPath !== sourcePath) {
    lines.push(`  - 来源路径: \`${originPath}\``);
  }
  if (sectionId) {
    lines.push(`  - 章节锚点: \`${sectionId}\``);
  }
  if (logicalAssetId) {
    lines.push(`  - 逻辑资产: \`${logicalAssetId}\``);
  }
  if (occurrenceId) {
    lines.push(`  - 正文引用: \`${occurrenceId}\``);
  }
  lines.push('');
  return lines;
}

function emptyQualityReport() {
  return {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'not_verified',
    checks: [],
    warnings: ['Delivery package has not been validated yet.'],
    failures: [],
  };
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

module.exports = { writeDeliveryPackage };
