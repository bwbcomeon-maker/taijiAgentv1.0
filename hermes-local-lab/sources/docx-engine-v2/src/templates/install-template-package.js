const fs = require('node:fs');
const path = require('node:path');

const { validateTemplatePackage } = require('./validate-template-package');

function installTemplatePackage({
  rootDir = path.resolve(__dirname, '../..'),
  packageDir,
  installRoot = 'installed',
  replace = false,
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
  const builtinEntry = findRegistryEntry(registry.builtin, templateId);
  if (builtinEntry) {
    if (replace) {
      throw new Error(`Cannot replace builtin template: ${templateId}`);
    }
    throw new Error(`Template already exists: ${templateId}`);
  }
  const installedEntries = Array.isArray(registry.installed) ? [...registry.installed] : [];
  const installedIndex = installedEntries.findIndex((entry) => (entry.templateId || entry.id) === templateId);
  if (installedIndex >= 0 && !replace) {
    throw new Error(`Template already exists: ${templateId}`);
  }

  const existingEntry = installedIndex >= 0 ? installedEntries[installedIndex] : null;
  const installPath = existingEntry?.path || toPosixPath(path.join(installRoot, templateId));
  const relativeInstallPath = path.isAbsolute(installPath) ? installPath : toPosixPath(installPath);
  const targetDir = path.isAbsolute(installPath)
    ? path.resolve(installPath)
    : path.resolve(absoluteRootDir, relativeInstallPath);
  assertInstallTargetWithinRoot(targetDir, path.resolve(absoluteRootDir, installRoot));
  if (installedIndex < 0) {
    assertTargetAvailable(targetDir);
  }

  const validation = validateTemplatePackage(sourceTemplate);
  if (!validation.ok) {
    throw new Error(`Template package validation failed: ${JSON.stringify(validation.errors)}`);
  }

  if (installedIndex >= 0) {
    replaceDirectory(absolutePackageDir, targetDir);
  } else {
    fs.mkdirSync(path.dirname(targetDir), { recursive: true });
    fs.cpSync(absolutePackageDir, targetDir, { recursive: true, errorOnExist: true });
  }

  const registryEntry = { ...(existingEntry || {}), templateId, path: relativeInstallPath };
  if (installedIndex >= 0) {
    installedEntries[installedIndex] = registryEntry;
  } else {
    installedEntries.push(registryEntry);
  }
  registry.installed = installedEntries;
  writeJson(registryPath, registry);

  return {
    ok: true,
    action: installedIndex >= 0 ? 'replaced' : 'installed',
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
    dataAdapterPath: path.join(packageDir, files.dataAdapter),
  };
}

function resolveTemplateFiles(manifest) {
  return {
    manifest: 'manifest.json',
    template: manifest.template || 'template.docx',
    schema: manifest.schema || 'schema.json',
    prompt: manifest.prompt || 'prompt.md',
    sample: manifest.sample || 'sample.json',
    dataAdapter: manifest.dataAdapter || 'data-adapter.js',
  };
}

function readRegistry(registryPath) {
  if (!fs.existsSync(registryPath)) {
    throw new Error(`template-registry.json not found: ${registryPath}`);
  }
  return readJson(registryPath);
}

function findRegistryEntry(entries, templateId) {
  return (Array.isArray(entries) ? entries : []).find((entry) => (entry.templateId || entry.id) === templateId) || null;
}

function assertTargetAvailable(targetDir) {
  if (!fs.existsSync(targetDir)) {
    return;
  }
  throw new Error(`Installed template directory already exists: ${targetDir}`);
}

function assertInstallTargetWithinRoot(targetDir, rootDir) {
  const resolvedTarget = path.resolve(targetDir);
  const resolvedRoot = path.resolve(rootDir);
  const relative = path.relative(resolvedRoot, resolvedTarget);
  if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) {
    return;
  }
  throw new Error(`Installed template path is outside the managed installed directory: ${resolvedTarget}`);
}

function assertSafeTemplateId(templateId) {
  if (!/^[A-Za-z0-9_-]+$/.test(templateId || '')) {
    throw new Error(`Invalid template id: ${templateId || ''}`);
  }
}

function replaceDirectory(sourceDir, targetDir) {
  const parentDir = path.dirname(targetDir);
  const baseName = path.basename(targetDir);
  const suffix = `${process.pid}-${Date.now()}`;
  const tempDir = path.join(parentDir, `.${baseName}.${suffix}.new`);
  const backupDir = path.join(parentDir, `.${baseName}.${suffix}.old`);
  let hasBackup = false;

  fs.mkdirSync(parentDir, { recursive: true });
  fs.rmSync(tempDir, { recursive: true, force: true });
  fs.cpSync(sourceDir, tempDir, { recursive: true, errorOnExist: true });

  try {
    if (fs.existsSync(targetDir)) {
      fs.renameSync(targetDir, backupDir);
      hasBackup = true;
    }
    fs.renameSync(tempDir, targetDir);
    if (hasBackup) {
      fs.rmSync(backupDir, { recursive: true, force: true });
      hasBackup = false;
    }
  } catch (error) {
    fs.rmSync(tempDir, { recursive: true, force: true });
    if (hasBackup && !fs.existsSync(targetDir) && fs.existsSync(backupDir)) {
      fs.renameSync(backupDir, targetDir);
      hasBackup = false;
    }
    throw error;
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
