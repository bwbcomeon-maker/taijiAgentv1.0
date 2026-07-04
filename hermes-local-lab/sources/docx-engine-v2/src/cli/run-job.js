#!/usr/bin/env node

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { packageAssets } = require('../assets/package-assets');
const { writeDeliveryPackage } = require('../delivery/write-delivery-package');
const { buildRenderPlan } = require('../planning/build-render-plan');
const { postprocessDocx } = require('../rendering/postprocess-docx');
const { renderDocx } = require('../rendering/render-docx');
const { normalizeDocxSource } = require('../source/normalize-docx');
const { normalizeMarkdownSource } = require('../source/normalize-markdown');
const { normalizeTextSource } = require('../source/normalize-text');
const { getTemplatePackage, listTemplates } = require('../templates/registry');
const { validateDeliveryPackage } = require('../validation/validate-delivery-package');

const EXIT_CODES = {
  success: 0,
  templateSelectionRequired: 2,
  validationFailed: 3,
  renderFailed: 4,
};

main().catch((error) => {
  writeJsonStdout({ ok: false, code: 'render_failed', message: error.message });
  process.exitCode = EXIT_CODES.renderFailed;
});

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const engineRoot = path.resolve(__dirname, '../..');

  if (!args.templateId) {
    writeJsonStdout({
      ok: false,
      code: 'template_selection_required',
      templates: listTemplates({ rootDir: engineRoot }).map(templateSummary),
    });
    process.exitCode = EXIT_CODES.templateSelectionRequired;
    return;
  }

  const sourcePath = args.source ? path.resolve(args.source) : '';
  if (!sourcePath) {
    throw new Error('--source is required.');
  }
  if (!args.outDir) {
    throw new Error('--out-dir is required.');
  }

  const deliveryDir = path.resolve(args.outDir);
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-job-'));
  let stage = 'validation';

  try {
    assertEmptyDeliveryDir(deliveryDir);
    const sourceType = args.sourceType || inferSourceType(sourcePath);
    const sourcePackage = await normalizeSource({ sourceType, sourcePath });
    const templatePackage = getTemplatePackage(args.templateId, { rootDir: engineRoot });
    assertSourceMeetsTemplateRequirements({ sourcePackage, templatePackage });
    const assetPackage = packageAssets({
      sourcePackage,
      assetDir: args.assetDir || '',
      outDir: path.join(workspace, 'assets'),
    });
    const renderPlan = buildRenderPlan({ sourcePackage, templatePackage, assetPackage });
    const deliveryAssetPackage = {
      ...assetPackage,
      assetDir: path.join(workspace, 'assets'),
    };
    const renderedPath = path.join(workspace, 'rendered.docx');
    const documentPath = path.join(workspace, 'document.docx');

    stage = 'render';
    await renderDocx({ templatePackage, renderPlan, outputPath: renderedPath });
    await postprocessDocx({ docxPath: renderedPath, renderPlan, outputPath: documentPath });

    stage = 'delivery';
    writeDeliveryPackage({
      deliveryDir,
      job: {
        jobId: renderPlan.jobId,
        sourceRef: sourcePackage.sourceRef,
        templateId: templatePackage.templateId,
        status: 'delivered',
      },
      sourcePackage,
      templatePackage,
      assetPackage: deliveryAssetPackage,
      renderPlan,
      documentPath,
      qualityReport: initialQualityReport(),
    });

    const qualityReport = validateDeliveryPackage({ deliveryDir });
    fs.writeFileSync(
      path.join(deliveryDir, 'quality-report.json'),
      `${JSON.stringify(qualityReport, null, 2)}\n`,
      'utf8'
    );

    if (qualityReport.status === 'failed') {
      writeJsonStdout({
        ok: false,
        code: 'validation_failed',
        deliveryDir,
        failures: qualityReport.failures,
      });
      process.exitCode = EXIT_CODES.validationFailed;
      return;
    }

    if (args.json) {
      writeJsonStdout({
        ok: true,
        jobId: renderPlan.jobId,
        deliveryDir,
        documentPath: path.join(deliveryDir, 'document.docx'),
        qualityStatus: qualityReport.status,
      });
    }
    process.exitCode = EXIT_CODES.success;
  } catch (error) {
    const validationFailure = stage === 'validation' || stage === 'delivery';
    writeJsonStdout({
      ok: false,
      code: validationFailure ? 'validation_failed' : 'render_failed',
      message: error.message,
    });
    process.exitCode = validationFailure ? EXIT_CODES.validationFailed : EXIT_CODES.renderFailed;
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true });
  }
}

function assertEmptyDeliveryDir(deliveryDir) {
  if (fs.existsSync(deliveryDir) && fs.readdirSync(deliveryDir).length > 0) {
    throw new Error(`输出目录非空: ${deliveryDir}`);
  }
}

function parseArgs(argv) {
  const parsed = {
    templateId: '',
    source: '',
    sourceType: '',
    assetDir: '',
    outDir: '',
    json: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--json') {
      parsed.json = true;
      continue;
    }

    const next = argv[index + 1];
    if (arg === '--template-id') {
      parsed.templateId = next || '';
      index += 1;
    } else if (arg === '--source') {
      parsed.source = next || '';
      index += 1;
    } else if (arg === '--source-type') {
      parsed.sourceType = next || '';
      index += 1;
    } else if (arg === '--asset-dir') {
      parsed.assetDir = next || '';
      index += 1;
    } else if (arg === '--out-dir') {
      parsed.outDir = next || '';
      index += 1;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return parsed;
}

async function normalizeSource({ sourceType, sourcePath }) {
  if (sourceType === 'markdown') {
    return normalizeMarkdownSource({ sourcePath });
  }
  if (sourceType === 'text') {
    return normalizeTextSource({ sourcePath });
  }
  if (sourceType === 'docx') {
    return normalizeDocxSource({ sourcePath });
  }
  throw new Error(`Unsupported source type: ${sourceType}`);
}

function inferSourceType(sourcePath) {
  const extension = path.extname(sourcePath).toLowerCase();
  if (extension === '.md' || extension === '.markdown') {
    return 'markdown';
  }
  if (extension === '.txt') {
    return 'text';
  }
  if (extension === '.docx') {
    return 'docx';
  }
  return 'markdown';
}

function templateSummary(template) {
  return {
    id: template.id,
    name: template.manifest?.name || template.id,
    description: template.manifest?.description || '',
    documentTypes: template.manifest?.documentTypes || [],
    capabilities: template.manifest?.capabilities || [],
  };
}

function initialQualityReport() {
  return {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'not_verified',
    checks: [
      { id: 'schema', status: 'not_verified' },
      { id: 'docx_zip', status: 'not_verified' },
      { id: 'template_markers', status: 'not_verified' },
      { id: 'image_coverage', status: 'not_verified' },
      { id: 'table_coverage', status: 'not_verified' },
      { id: 'figure_id_metadata', status: 'not_verified' },
      { id: 'delivery_files', status: 'not_verified' },
      { id: 'wps_visual', status: 'not_verified' },
    ],
    warnings: ['Delivery package validation has not run yet.'],
    failures: [],
  };
}

function assertSourceMeetsTemplateRequirements({ sourcePackage, templatePackage }) {
  const requirements = templatePackage?.manifest?.sourceRequirements || {};
  const minTables = Number(requirements.minTables || 0);
  const minVisuals = Number(requirements.minVisuals || 0);
  const tableCount = (sourcePackage.tables || []).length;
  const visualCount = (sourcePackage.figures || []).length + (sourcePackage.images || []).length;
  const failures = [];

  if (requirements.richContentRequired && sourceHasRichContentWarning(sourcePackage)) {
    failures.push('需要富内容初稿，不能直接使用纯文本来源');
  }
  if (tableCount < minTables) {
    failures.push(`至少需要 ${minTables} 个表格，当前为 ${tableCount} 个`);
  }
  if (visualCount < minVisuals) {
    failures.push(`至少需要 ${minVisuals} 个图示或图片，当前为 ${visualCount} 个`);
  }

  if (failures.length > 0) {
    throw new Error(
      `模板 ${templatePackage.id} 的输入不满足要求：${failures.join('；')}。请先补齐表格和图示，再套用该模板。`
    );
  }
}

function sourceHasRichContentWarning(sourcePackage) {
  return (sourcePackage.warnings || []).some((warning) => {
    if (typeof warning === 'string') {
      return /rich_content_missing|缺少.*富内容/.test(warning);
    }
    return warning?.code === 'rich_content_missing';
  });
}

function writeJsonStdout(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}
