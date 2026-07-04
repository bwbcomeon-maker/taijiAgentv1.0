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
    files,
    manifest,
    manifestPath,
    templatePath: path.join(packageDir, files.template),
    schemaPath: path.join(packageDir, files.schema),
    promptPath: path.join(packageDir, files.prompt),
    samplePath: path.join(packageDir, files.sample),
  };
}

function listTemplates({ rootDir = path.resolve(__dirname, '../..') } = {}) {
  const registryPath = path.join(rootDir, 'template-registry.json');
  const registry = readJson(registryPath);
  const builtinTemplates = Array.isArray(registry.builtin) ? registry.builtin : [];

  return builtinTemplates.map((registryEntry) =>
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

module.exports = { listTemplates, getTemplatePackage };
