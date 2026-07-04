const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const { validateDomainObject } = require('../domain/validate');

const IMAGE_INSTRUCTIONS = [
  '# 图片调整说明',
  '',
  '本交付包保留了可编辑资产和生成后的 DOCX：',
  '',
  '- `document.docx` 是生成文档。',
  '- `delivery-package.json` 是交付包清单，记录文档、源文件、模板清单、渲染计划和质量报告的位置。',
  '- `source/original/` 保存原始输入文件，`source.md` 是用于人工查看的 Markdown 副本。',
  '- `assets/` 保存图片、图形源文件和展示文件。',
  '- `render-plan.json` 记录图表与模板位置绑定关系。',
  '- 如需替换图片，优先修改 `assets/` 中对应源文件，再按 `figureId` 回写或重渲染文档。',
  '',
].join('\n');

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
  assertDomainObject('TemplateManifest', templatePackage.manifest || {}, 'TemplateManifest');

  assertEmptyOutputDirectory(deliveryDir);
  fs.mkdirSync(deliveryDir, { recursive: true });

  const deliveryDocumentPath = path.join(deliveryDir, 'document.docx');
  const deliverySourcePath = path.join(deliveryDir, 'source.md');
  fs.copyFileSync(documentPath, deliveryDocumentPath);
  fs.writeFileSync(deliverySourcePath, sourceMarkdown(sourcePackage), 'utf8');
  const originalSource = copyOriginalSource({ deliveryDir, sourcePackage });
  copyAssets({ deliveryDir, sourcePackage, assetPackage });
  writeJson(path.join(deliveryDir, 'job.manifest.json'), normalizedJob);
  writeJson(path.join(deliveryDir, 'template.manifest.json'), templatePackage.manifest || {});
  writeJson(path.join(deliveryDir, 'render-plan.json'), renderPlan);
  writeJson(path.join(deliveryDir, 'quality-report.json'), qualityReport || emptyQualityReport());
  fs.writeFileSync(path.join(deliveryDir, 'README-图片调整说明.md'), IMAGE_INSTRUCTIONS, 'utf8');

  const deliveryPackage = {
    schemaVersion: 'docx-engine-v2/delivery-package',
    deliveryDir: manifestDeliveryDir || deliveryDir,
    documentSha256: sha256File(deliveryDocumentPath),
    sourceSha256: sha256File(deliverySourcePath),
    files: {
      document: 'document.docx',
      source: 'source.md',
      originalSource,
      assetsDir: 'assets',
      jobManifest: 'job.manifest.json',
      templateManifest: 'template.manifest.json',
      renderPlan: 'render-plan.json',
      qualityReport: 'quality-report.json',
      imageInstructions: 'README-图片调整说明.md',
    },
    status: 'delivered',
  };
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

  copyDirectoryContents(sourceAssetsDir, targetAssetsDir);
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

function copyDirectoryContents(sourceDir, targetDir) {
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);
    if (entry.isDirectory()) {
      fs.mkdirSync(targetPath, { recursive: true });
      copyDirectoryContents(sourcePath, targetPath);
      continue;
    }
    if (entry.isFile()) {
      fs.copyFileSync(sourcePath, targetPath);
    }
  }
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
