const fs = require('node:fs');
const path = require('node:path');

const { validateTemplatePackage } = require('./validate-template-package');

function installTemplatePackage({
  rootDir = path.resolve(__dirname, '../..'),
  packageDir,
  installRoot = 'installed',
} = {}) {
  if (!packageDir) {
    throw new Error('packageDir is required.');
  }

  const absoluteRootDir = path.resolve(rootDir);
  const absolutePackageDir = path.resolve(packageDir);
  const registryPath = path.join(absoluteRootDir, 'template-registry.json');
  const registry = readRegistry(registryPath);
  const sourceTemplate = loadPackageFromDir({
    packageDir: absolutePackageDir,
    registryPath,
    registrySource: 'incoming',
  });
  const templateId = sourceTemplate.templateId;

  assertSafeTemplateId(templateId);
  assertTemplateDoesNotExist(registry, templateId);

  const relativeInstallPath = toPosixPath(path.join(installRoot, templateId));
  const targetDir = path.resolve(absoluteRootDir, relativeInstallPath);
  assertTargetAvailable(targetDir);

  const validation = validateTemplatePackage(sourceTemplate);
  if (!validation.ok) {
    throw new Error(`Template package validation failed: ${JSON.stringify(validation.errors)}`);
  }

  fs.mkdirSync(path.dirname(targetDir), { recursive: true });
  fs.cpSync(absolutePackageDir, targetDir, { recursive: true, errorOnExist: true });

  const registryEntry = { templateId, path: relativeInstallPath };
  registry.installed = Array.isArray(registry.installed) ? [...registry.installed, registryEntry] : [registryEntry];
  writeJson(registryPath, registry);

  return {
    ok: true,
    templateId,
    packageDir: targetDir,
    registryPath,
    registryEntry,
  };
}

function loadPackageFromDir({ packageDir, registryPath = '', registrySource = 'installed' }) {
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  const files = resolveTemplateFiles(manifest);
  const templateId = manifest.id;

  return {
    schemaVersion: 'docx-engine-v2/template-package',
    id: templateId,
    templateId,
    packageDir,
    registryPath,
    registryEntry: { templateId, path: packageDir, registrySource },
    registrySource,
    files,
    manifest,
    manifestPath,
    templatePath: path.join(packageDir, files.template),
    schemaPath: path.join(packageDir, files.schema),
    promptPath: path.join(packageDir, files.prompt),
    samplePath: path.join(packageDir, files.sample),
  };
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

function readRegistry(registryPath) {
  if (!fs.existsSync(registryPath)) {
    throw new Error(`template-registry.json not found: ${registryPath}`);
  }
  return readJson(registryPath);
}

function assertTemplateDoesNotExist(registry, templateId) {
  const entries = [
    ...(Array.isArray(registry.builtin) ? registry.builtin : []),
    ...(Array.isArray(registry.installed) ? registry.installed : []),
  ];
  if (entries.some((entry) => (entry.templateId || entry.id) === templateId)) {
    throw new Error(`Template already exists: ${templateId}`);
  }
}

function assertTargetAvailable(targetDir) {
  if (!fs.existsSync(targetDir)) {
    return;
  }
  throw new Error(`Installed template directory already exists: ${targetDir}`);
}

function assertSafeTemplateId(templateId) {
  if (!/^[A-Za-z0-9_-]+$/.test(templateId || '')) {
    throw new Error(`Invalid template id: ${templateId || ''}`);
  }
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function toPosixPath(value) {
  return String(value || '').split(path.sep).join('/');
}

module.exports = { installTemplatePackage, loadPackageFromDir };
