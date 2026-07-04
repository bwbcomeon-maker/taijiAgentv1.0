const fs = require('node:fs');
const path = require('node:path');

const { renderDocx } = require('../rendering/render-docx');
const { loadPackageFromDir } = require('./install-template-package');
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

  const templatePackage = loadPackageFromDir({
    packageDir: absolutePackageDir,
    registrySource: 'incoming',
  });
  const validation = validateTemplatePackage(templatePackage);
  if (!validation.ok) {
    throw new Error(`Template package validation failed: ${JSON.stringify(validation.errors)}`);
  }

  const renderPlan = readJson(templatePackage.adapterSamplePath);
  fs.mkdirSync(absoluteOutDir, { recursive: true });
  const documentPath = path.join(absoluteOutDir, 'sample.docx');
  await renderDocx({
    templatePackage,
    renderPlan,
    outputPath: documentPath,
  });

  const report = {
    schemaVersion: 'docx-engine-v2/template-smoke-report',
    ok: true,
    status: 'passed',
    templateId: templatePackage.templateId,
    packageDir: templatePackage.packageDir,
    outDir: absoluteOutDir,
    documentPath,
    renderPlanPath: templatePackage.adapterSamplePath,
    checks: [
      { id: 'template_package', status: 'passed' },
      { id: 'adapter_sample_render', status: 'passed' },
    ],
    validation,
  };
  const reportPath = path.join(absoluteOutDir, 'template-smoke-report.json');
  writeJson(reportPath, report);

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
