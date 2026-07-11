const fs = require('node:fs');
const path = require('node:path');

const {
  assertSafeDirectoryTree,
  computeTemplateContentDigest,
  readJsonRegularFile,
  resolveContainedFilePath,
  resolveRegistryPackageDir,
  loadTemplateRegistry,
} = require('./template-store');

function resolveTemplateFiles(manifest) {
  return {
    manifest: 'manifest.json',
    template: manifest.template || 'template.docx',
    schema: manifest.schema || 'schema.json',
    prompt: manifest.prompt || 'prompt.md',
    sample: manifest.sample || 'sample.json',
    dataAdapter: manifest.dataAdapter || 'data-adapter.js',
    adapterSample: manifest.adapterSample || 'adapter-sample.render-plan.json',
  };
}

function loadTemplatePackage({ store, registryPath, registryEntry }) {
  const id = registryEntry.templateId || registryEntry.id;
  if (!id) {
    throw new Error(`Template registry entry is missing templateId: ${JSON.stringify(registryEntry)}`);
  }

  const registrySource = registryEntry.registrySource || 'builtin';
  const packageDir = resolveRegistryPackageDir({ store, registryEntry, registrySource });
  assertSafeDirectoryTree(packageDir, `Template package ${id}`);
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJsonRegularFile(manifestPath, `Template manifest ${id}`);
  const files = resolveTemplateFiles(manifest);

  const template = {
    schemaVersion: 'docx-engine-v2/template-package',
    id,
    templateId: id,
    packageDir,
    registryPath,
    registryEntry,
    registrySource,
    files,
    manifest,
    manifestPath,
    templatePath: resolveContainedFilePath(packageDir, files.template, `Template DOCX ${id}`),
    schemaPath: resolveContainedFilePath(packageDir, files.schema, `Template schema ${id}`),
    promptPath: resolveContainedFilePath(packageDir, files.prompt, `Template prompt ${id}`),
    samplePath: resolveContainedFilePath(packageDir, files.sample, `Template sample ${id}`),
    dataAdapterPath: resolveContainedFilePath(packageDir, files.dataAdapter, `Template data adapter ${id}`),
    adapterSamplePath: resolveContainedFilePath(packageDir, files.adapterSample, `Template adapter sample ${id}`),
  };

  if (registrySource === 'installed') {
    const installReportPath = path.join(packageDir, 'template-install-report.json');
    const installReport = readInstalledTemplateReport({ installReportPath, templateId: id, registryEntry });
    template.installReportPath = installReportPath;
    template.installReport = installReport;
    if (
      registryEntry.contentDigest &&
      computeTemplateContentDigest(packageDir) !== registryEntry.contentDigest
    ) {
      throw new Error(`Installed template content digest mismatch: ${id}`);
    }
  }

  return template;
}

function listTemplates(options = {}) {
  const { store, registryPath, registry } = loadTemplateRegistry(options);
  const registryEntries = [
    ...sourceEntries(registry.builtin, 'builtin'),
    ...sourceEntries(registry.installed, 'installed'),
  ];
  assertUniqueTemplateIds(registryEntries);

  return registryEntries.map((registryEntry) =>
    loadTemplatePackage({ store, registryPath, registryEntry })
  );
}

function getTemplatePackage(templateId, options = {}) {
  const template = listTemplates(options).find((candidate) => candidate.id === templateId);
  if (!template) {
    throw new Error(`Unknown template package: ${templateId}`);
  }

  return template;
}

function sourceEntries(entries, registrySource) {
  if (!Array.isArray(entries)) {
    return [];
  }
  return entries.map((entry) => ({ ...entry, registrySource }));
}

function assertUniqueTemplateIds(entries) {
  const seen = new Set();
  for (const entry of entries) {
    const id = entry.templateId || entry.id;
    if (!id) {
      continue;
    }
    if (seen.has(id)) {
      throw new Error(`Duplicate template id in registry: ${id}`);
    }
    seen.add(id);
  }
}

function readInstalledTemplateReport({ installReportPath, templateId, registryEntry }) {
  if (!fs.existsSync(installReportPath) || !fs.statSync(installReportPath).isFile()) {
    throw new Error(`Installed template install report not found: ${installReportPath}`);
  }

  const installReport = readJsonRegularFile(installReportPath, `Installed template report ${templateId}`);
  assertInstalledTemplateReport({ installReport, templateId, registryEntry });
  return installReport;
}

function assertInstalledTemplateReport({ installReport, templateId, registryEntry }) {
  const failures = [];
  if (installReport.schemaVersion !== 'docx-engine-v2/template-install-report') {
    failures.push('schemaVersion must be docx-engine-v2/template-install-report');
  }
  if (installReport.ok !== true || installReport.status !== 'passed') {
    failures.push('status must be passed');
  }
  if (installReport.templateId !== templateId) {
    failures.push(`templateId must be ${templateId}`);
  }
  if (installReport.registryEntry?.templateId !== templateId) {
    failures.push(`registryEntry.templateId must be ${templateId}`);
  }
  if (installReport.registryEntry?.path !== registryEntry.path) {
    failures.push(`registryEntry.path must be ${registryEntry.path || ''}`);
  }
  for (const digestField of ['contentDigest', 'revisionDigest']) {
    if (
      registryEntry[digestField] &&
      installReport.registryEntry?.[digestField] !== registryEntry[digestField]
    ) {
      failures.push(`registryEntry.${digestField} must match registry`);
    }
  }
  for (const checkId of ['template_package', 'sample_render', 'registry_entry']) {
    if (!hasPassedCheck(installReport.checks, checkId)) {
      failures.push(`${checkId} check must be passed`);
    }
  }

  if (failures.length > 0) {
    throw new Error(`Installed template install report mismatch: ${templateId}: ${failures.join('; ')}`);
  }
}

function hasPassedCheck(checks, checkId) {
  return (Array.isArray(checks) ? checks : []).some((check) => check.id === checkId && check.status === 'passed');
}

module.exports = { listTemplates, getTemplatePackage };
