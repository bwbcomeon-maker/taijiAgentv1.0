const fs = require('node:fs');
const path = require('node:path');

const { renderDocx } = require('../rendering/render-docx');
const { inspectRenderedDocx } = require('../validation/docx-render-inspection');
const { assertSafeDirectoryTree } = require('./template-store');
const { validateTemplatePackage } = require('./validate-template-package');

async function renderTemplateSample({
  packageDir,
  outDir,
} = {}) {
  if (!packageDir) {
    throw new Error('packageDir is required.');
  }
  if (!outDir) {
    throw new Error('outDir is required.');
  }

  const absolutePackageDir = path.resolve(packageDir);
  const absoluteOutDir = path.resolve(outDir);
  assertWritableOutDir(absoluteOutDir);

  const templatePackage = loadIncomingPackage({
    packageDir: absolutePackageDir,
  });
  const validation = validateTemplatePackage(templatePackage);
  if (!validation.ok) {
    throw new Error(`Template package validation failed: ${JSON.stringify(validation.errors)}`);
  }
  assertSafeDirectoryTree(absolutePackageDir, 'Template sample package');

  const renderPlan = readJson(templatePackage.adapterSamplePath);
  fs.mkdirSync(absoluteOutDir, { recursive: true });
  const documentPath = path.join(absoluteOutDir, 'sample.docx');
  await renderDocx({
    templatePackage,
    renderPlan,
    outputPath: documentPath,
  });
  assertSafeDirectoryTree(absolutePackageDir, 'Template sample package');
  const docxInspection = inspectRenderedDocx({ docxPath: documentPath, label: 'sample.docx' });

  const report = {
    schemaVersion: 'docx-engine-v2/template-smoke-report',
    ok: docxInspection.ok,
    status: docxInspection.status,
    templateId: templatePackage.templateId,
    packageDir: templatePackage.packageDir,
    outDir: absoluteOutDir,
    documentPath,
    renderPlanPath: templatePackage.adapterSamplePath,
    checks: [
      { id: 'template_package', status: 'passed' },
      { id: 'adapter_sample_render', status: 'passed' },
      ...docxInspection.checks,
    ],
    failures: docxInspection.failures,
    validation,
  };
  const reportPath = path.join(absoluteOutDir, 'template-smoke-report.json');
  writeJson(reportPath, report);

  if (!docxInspection.ok) {
    const error = new Error(docxInspection.failures.join('；') || 'Template sample render inspection failed.');
    error.reportPath = reportPath;
    error.documentPath = documentPath;
    error.report = report;
    throw error;
  }

  return {
    ok: true,
    templateId: templatePackage.templateId,
    packageDir: templatePackage.packageDir,
    outDir: absoluteOutDir,
    documentPath,
    reportPath,
    report,
  };
}

function loadIncomingPackage({ packageDir }) {
  const { loadPackageFromDir } = require('./install-template-package');
  return loadPackageFromDir({
    packageDir,
    registrySource: 'incoming',
  });
}

function assertWritableOutDir(outDir) {
  if (!fs.existsSync(outDir)) {
    return;
  }
  const entries = fs.readdirSync(outDir);
  if (entries.length > 0) {
    throw new Error(`Template sample output directory must be empty or missing; refused to overwrite non-empty directory: ${outDir}`);
  }
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

module.exports = { renderTemplateSample };
