const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { packageAssets } = require('../assets/package-assets');
const { writeDeliveryPackage } = require('../delivery/write-delivery-package');
const { createDocumentJob, transitionJob } = require('../domain/document-job');
const { validateDomainObject } = require('../domain/validate');
const { buildRenderPlan } = require('../planning/build-render-plan');
const { postprocessDocx } = require('../rendering/postprocess-docx');
const { renderDocx } = require('../rendering/render-docx');
const { normalizeDocxSource } = require('../source/normalize-docx');
const { normalizeMarkdownSource } = require('../source/normalize-markdown');
const { normalizeTextSource } = require('../source/normalize-text');
const { getTemplatePackage } = require('../templates/registry');
const { validateDeliveryPackage } = require('../validation/validate-delivery-package');

async function runDocumentJob({
  engineRoot = path.resolve(__dirname, '../..'),
  templateId,
  sourcePath,
  sourceType = '',
  assetDir = '',
  deliveryDir,
  workspaceRoot = os.tmpdir(),
} = {}) {
  if (!templateId) {
    throw new Error('templateId is required.');
  }
  if (!sourcePath) {
    throw new Error('sourcePath is required.');
  }
  if (!deliveryDir) {
    throw new Error('deliveryDir is required.');
  }

  const absoluteSourcePath = path.resolve(sourcePath);
  const absoluteDeliveryDir = path.resolve(deliveryDir);
  const workspace = fs.mkdtempSync(path.join(workspaceRoot, 'docx-engine-v2-job-'));
  let job = null;
  let stage = 'validation';

  try {
    assertEmptyDeliveryDir(absoluteDeliveryDir);
    const resolvedSourceType = sourceType || inferSourceType(absoluteSourcePath);
    const sourcePackage = await normalizeSource({
      sourceType: resolvedSourceType,
      sourcePath: absoluteSourcePath,
    });

    job = createDocumentJob({
      jobId: createJobId(sourcePackage),
      sourceRef: sourcePackage.sourceRef,
      templateId,
      workspace,
      inputs: buildInputs({ sourcePath: absoluteSourcePath, assetDir }),
    });
    job = transitionJob(job, 'source_normalized', {
      warnings: collectWarnings(sourcePackage.warnings),
    });

    const templatePackage = getTemplatePackage(templateId, { rootDir: path.resolve(engineRoot) });
    assertSourceMeetsTemplateRequirements({ sourcePackage, templatePackage });
    job = transitionJob(job, 'template_selected', {
      templateId: templatePackage.templateId,
    });

    const assetPackage = packageAssets({
      sourcePackage,
      assetDir,
      outDir: path.join(workspace, 'assets'),
    });
    job = transitionJob(job, 'assets_packaged', {
      warnings: [...job.warnings, ...collectWarnings(assetPackage.warnings)],
    });

    const renderPlan = buildRenderPlan({ sourcePackage, templatePackage, assetPackage });
    job = transitionJob(job, 'render_planned', {
      jobId: renderPlan.jobId,
      warnings: [...job.warnings, ...collectWarnings(renderPlan.warnings)],
    });

    const deliveryAssetPackage = {
      ...assetPackage,
      assetDir: path.join(workspace, 'assets'),
    };
    const renderedPath = path.join(workspace, 'rendered.docx');
    const documentPath = path.join(workspace, 'document.docx');

    stage = 'render';
    await renderDocx({ templatePackage, renderPlan, outputPath: renderedPath });
    await postprocessDocx({ docxPath: renderedPath, renderPlan, outputPath: documentPath });
    job = transitionJob(job, 'rendered', {
      outputs: [{ type: 'rendered_document', path: documentPath }],
    });

    stage = 'delivery';
    const validationDeliveryDir = path.join(workspace, 'delivery-validation');
    writeDeliveryPackage({
      deliveryDir: validationDeliveryDir,
      job,
      sourcePackage,
      templatePackage,
      assetPackage: deliveryAssetPackage,
      renderPlan,
      documentPath,
      qualityReport: initialQualityReport(),
    });

    const qualityReport = validateDeliveryPackage({ deliveryDir: validationDeliveryDir });
    if (qualityReport.status === 'failed') {
      return persistFailureArtifacts({
        deliveryDir: absoluteDeliveryDir,
        result: failureResult({
          code: 'validation_failed',
          message: qualityReport.failures.join('；') || 'Delivery package validation failed.',
          job,
          stage,
          failures: qualityReport.failures,
        }),
      });
    }

    job = transitionJob(job, 'validated', {
      warnings: [...job.warnings, ...collectWarnings(qualityReport.warnings)],
      failures: qualityReport.failures || [],
    });
    job = transitionJob(job, 'delivered', {
      deliveredAt: new Date().toISOString(),
      workspace: absoluteDeliveryDir,
      outputs: [
        { type: 'document', path: path.join(absoluteDeliveryDir, 'document.docx') },
        { type: 'delivery_package', path: absoluteDeliveryDir },
        { type: 'quality_report', path: path.join(absoluteDeliveryDir, 'quality-report.json') },
      ],
    });

    const finalDeliveryDir = path.join(workspace, 'delivery-final');
    writeDeliveryPackage({
      deliveryDir: finalDeliveryDir,
      job,
      sourcePackage,
      templatePackage,
      assetPackage: deliveryAssetPackage,
      renderPlan,
      documentPath,
      qualityReport,
      manifestDeliveryDir: absoluteDeliveryDir,
    });
    const finalQualityReport = validateDeliveryPackage({ deliveryDir: finalDeliveryDir });
    fs.writeFileSync(
      path.join(finalDeliveryDir, 'quality-report.json'),
      `${JSON.stringify(finalQualityReport, null, 2)}\n`,
      'utf8'
    );
    if (finalQualityReport.status === 'failed') {
      return persistFailureArtifacts({
        deliveryDir: absoluteDeliveryDir,
        result: failureResult({
          code: 'validation_failed',
          message: finalQualityReport.failures.join('；') || 'Final delivery package validation failed.',
          job: transitionFailedJob(job, finalQualityReport.failures),
          stage,
          failures: finalQualityReport.failures,
        }),
      });
    }

    moveVerifiedDelivery({ fromDir: finalDeliveryDir, toDir: absoluteDeliveryDir });

    return {
      ok: true,
      job,
      jobId: job.jobId,
      deliveryDir: absoluteDeliveryDir,
      documentPath: path.join(absoluteDeliveryDir, 'document.docx'),
      qualityReport: finalQualityReport,
      qualityStatus: finalQualityReport.status,
    };
  } catch (error) {
    return persistFailureArtifacts({
      deliveryDir: absoluteDeliveryDir,
      result: failureResult({
        code: stage === 'render' ? 'render_failed' : 'validation_failed',
        message: error.message,
        job,
        stage,
        failures: [error.message],
      }),
    });
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true });
  }
}

function assertEmptyDeliveryDir(deliveryDir) {
  if (fs.existsSync(deliveryDir) && fs.readdirSync(deliveryDir).length > 0) {
    throw new Error(`输出目录非空: ${deliveryDir}`);
  }
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

function buildInputs({ sourcePath, assetDir }) {
  const inputs = [{ type: 'source', path: sourcePath }];
  if (assetDir) {
    inputs.push({ type: 'asset_dir', path: assetDir });
  }
  return inputs;
}

function createJobId(sourcePackage) {
  return sourcePackage.sourceRef?.sha256
    ? `job-${sourcePackage.sourceRef.sha256.slice(0, 12)}`
    : 'job-document-render';
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
      { id: 'figure_placement', status: 'not_verified' },
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

function failureResult({ code, message, job, stage, failures }) {
  return {
    ok: false,
    code,
    message,
    stage,
    failures: failures || (message ? [message] : []),
    job: transitionFailedJob(job, failures || [message].filter(Boolean)),
  };
}

function persistFailureArtifacts({ deliveryDir, result }) {
  if (!result || result.ok || !result.job || !canWriteFailureArtifacts(deliveryDir)) {
    return result;
  }

  const jobManifestPath = path.join(deliveryDir, 'job.manifest.json');
  const failureReportPath = path.join(deliveryDir, 'failure-report.json');
  const failureReport = buildFailureReport(result);

  try {
    assertDomainObject('DocumentJob', result.job, 'failed job manifest');
    assertDomainObject('FailureReport', failureReport, 'failure report');
    fs.mkdirSync(deliveryDir, { recursive: true });
    writeJson(jobManifestPath, result.job);
    writeJson(failureReportPath, failureReport);
  } catch (error) {
    return {
      ...result,
      warnings: [...(result.warnings || []), `Failure artifacts were not written: ${error.message}`],
    };
  }

  return {
    ...result,
    jobManifestPath,
    failureReportPath,
    failureReport,
  };
}

function canWriteFailureArtifacts(deliveryDir) {
  if (!deliveryDir) {
    return false;
  }
  if (!fs.existsSync(deliveryDir)) {
    return true;
  }
  return fs.statSync(deliveryDir).isDirectory() && fs.readdirSync(deliveryDir).length === 0;
}

function buildFailureReport(result) {
  return {
    schemaVersion: 'docx-engine-v2/failure-report',
    ok: false,
    code: result.code,
    stage: result.stage,
    message: result.message,
    failures: result.failures || [],
    jobId: result.job.jobId,
    jobManifest: 'job.manifest.json',
  };
}

function transitionFailedJob(job, failures) {
  if (!job || job.status === 'failed') {
    return job;
  }
  try {
    return transitionJob(job, 'failed', {
      failures: [...(job.failures || []), ...(failures || [])].filter(Boolean),
    });
  } catch {
    return {
      ...job,
      status: 'failed',
      failures: [...(job.failures || []), ...(failures || [])].filter(Boolean),
    };
  }
}

function collectWarnings(warnings) {
  return (warnings || []).map((warning) => {
    if (typeof warning === 'string') {
      return warning;
    }
    if (warning?.code && warning?.message) {
      return `${warning.code}: ${warning.message}`;
    }
    if (warning?.message) {
      return warning.message;
    }
    return JSON.stringify(warning);
  });
}

function assertDomainObject(schemaName, value, label) {
  const validation = validateDomainObject(schemaName, value);
  if (!validation.ok) {
    throw new Error(`${label} validation failed: ${JSON.stringify(validation.errors)}`);
  }
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function moveVerifiedDelivery({ fromDir, toDir }) {
  if (fs.existsSync(toDir)) {
    assertEmptyDeliveryDir(toDir);
    fs.rmSync(toDir, { recursive: true, force: true });
  }
  fs.mkdirSync(path.dirname(toDir), { recursive: true });
  fs.renameSync(fromDir, toDir);
}

module.exports = {
  runDocumentJob,
  inferSourceType,
  assertSourceMeetsTemplateRequirements,
  initialQualityReport,
};
