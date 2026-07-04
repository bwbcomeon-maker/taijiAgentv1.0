const fs = require('node:fs');
const path = require('node:path');

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

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

function loadTemplatePackage({ rootDir, registryPath, registryEntry }) {
  const id = registryEntry.templateId || registryEntry.id;
  if (!id) {
    throw new Error(`Template registry entry is missing templateId: ${JSON.stringify(registryEntry)}`);
  }

  const relativePath = registryEntry.path || path.join('templates', id);
  const packageDir = path.resolve(rootDir, relativePath);
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  const files = resolveTemplateFiles(manifest);

  return {
    schemaVersion: 'docx-engine-v2/template-package',
    id,
    templateId: id,
    packageDir,
    registryPath,
    registryEntry,
    registrySource: registryEntry.registrySource || 'builtin',
    files,
    manifest,
    manifestPath,
    templatePath: path.join(packageDir, files.template),
    schemaPath: path.join(packageDir, files.schema),
    promptPath: path.join(packageDir, files.prompt),
    samplePath: path.join(packageDir, files.sample),
    dataAdapterPath: path.join(packageDir, files.dataAdapter),
    adapterSamplePath: path.join(packageDir, files.adapterSample),
  };
}

function listTemplates({ rootDir = path.resolve(__dirname, '../..') } = {}) {
  const registryPath = path.join(rootDir, 'template-registry.json');
  const registry = readJson(registryPath);
  const registryEntries = [
    ...sourceEntries(registry.builtin, 'builtin'),
    ...sourceEntries(registry.installed, 'installed'),
  ];
  assertUniqueTemplateIds(registryEntries);

  return registryEntries.map((registryEntry) =>
    loadTemplatePackage({ rootDir, registryPath, registryEntry })
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

module.exports = { listTemplates, getTemplatePackage };
