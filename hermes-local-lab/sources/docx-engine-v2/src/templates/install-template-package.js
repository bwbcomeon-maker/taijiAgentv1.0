const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { renderTemplateSample } = require('./render-template-sample');
const { validateTemplatePackage } = require('./validate-template-package');

async function installTemplatePackage({
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
  const sampleRender = await assertTemplateSampleRenders(absolutePackageDir);

  const registryEntry = { ...(existingEntry || {}), templateId, path: relativeInstallPath };
  if (installedIndex >= 0) {
    installedEntries[installedIndex] = registryEntry;
  } else {
    installedEntries.push(registryEntry);
  }

  const action = installedIndex >= 0 ? 'replaced' : 'installed';
  const installReportPath = path.join(targetDir, 'template-install-report.json');
  const installReport = buildInstallReport({
    action,
    templateId,
    targetDir,
    sourcePackageDir: absolutePackageDir,
    registryPath,
    registryEntry,
    validation,
    sampleRender,
  });

  const preparedDir = prepareInstallDirectory({
    sourceDir: absolutePackageDir,
    targetDir,
    installReport,
  });
  let committedDirectory = null;
  try {
    committedDirectory = commitPreparedDirectory({ preparedDir, targetDir });
    registry.installed = installedEntries;
    writeJson(registryPath, registry);
  } catch (error) {
    if (committedDirectory) {
      rollbackCommittedDirectory({ targetDir, ...committedDirectory });
    } else {
      fs.rmSync(preparedDir, { recursive: true, force: true });
    }
    throw error;
  }
  finalizeCommittedDirectory(committedDirectory);

  return {
    ok: true,
    action,
    templateId,
    packageDir: targetDir,
    registryPath,
    registryEntry,
    installReportPath,
  };
}

function prepareInstallDirectory({ sourceDir, targetDir, installReport }) {
  const tempDir = createSiblingTempDir(targetDir, 'new');
  fs.mkdirSync(path.dirname(targetDir), { recursive: true });
  fs.rmSync(tempDir, { recursive: true, force: true });
  try {
    fs.cpSync(sourceDir, tempDir, { recursive: true, errorOnExist: true });
    writeJson(path.join(tempDir, 'template-install-report.json'), installReport);
    return tempDir;
  } catch (error) {
    fs.rmSync(tempDir, { recursive: true, force: true });
    throw error;
  }
}

function commitPreparedDirectory({ preparedDir, targetDir }) {
  const backupDir = createSiblingTempDir(targetDir, 'old');
  let hasBackup = false;
  let committed = false;

  try {
    if (fs.existsSync(targetDir)) {
      fs.renameSync(targetDir, backupDir);
      hasBackup = true;
    }
    fs.renameSync(preparedDir, targetDir);
    committed = true;
    return { backupDir, hasBackup };
  } catch (error) {
    fs.rmSync(preparedDir, { recursive: true, force: true });
    if (committed) {
      fs.rmSync(targetDir, { recursive: true, force: true });
    }
    if (hasBackup && !fs.existsSync(targetDir) && fs.existsSync(backupDir)) {
      fs.renameSync(backupDir, targetDir);
    }
    throw error;
  }
}

function finalizeCommittedDirectory({ backupDir, hasBackup }) {
  if (hasBackup) {
    fs.rmSync(backupDir, { recursive: true, force: true });
  }
}

function rollbackCommittedDirectory({ targetDir, backupDir, hasBackup }) {
  fs.rmSync(targetDir, { recursive: true, force: true });
  if (hasBackup && fs.existsSync(backupDir)) {
    fs.renameSync(backupDir, targetDir);
  }
}

function createSiblingTempDir(targetDir, suffixName) {
  const parentDir = path.dirname(targetDir);
  const baseName = path.basename(targetDir);
  const suffix = `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return path.join(parentDir, `.${baseName}.${suffix}.${suffixName}`);
}

async function assertTemplateSampleRenders(packageDir) {
  const smokeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-install-smoke-'));
  try {
    const result = await renderTemplateSample({ packageDir, outDir: smokeDir });
    return sanitizeSampleRender(result.report);
  } finally {
    fs.rmSync(smokeDir, { recursive: true, force: true });
  }
}

function buildInstallReport({
  action,
  templateId,
  targetDir,
  sourcePackageDir,
  registryPath,
  registryEntry,
  validation,
  sampleRender,
}) {
  return {
    schemaVersion: 'docx-engine-v2/template-install-report',
    ok: true,
    status: 'passed',
    action,
    templateId,
    packageDir: targetDir,
    sourcePackageDir,
    registryPath,
    registryEntry,
    checks: [
      { id: 'template_package', status: 'passed' },
      { id: 'sample_render', status: sampleRender.status || 'passed' },
      { id: 'registry_entry', status: 'passed' },
    ],
    validation,
    sampleRender,
  };
}

function sanitizeSampleRender(report = {}) {
  return {
    ok: report.ok === true,
    status: report.status || (report.ok === true ? 'passed' : 'failed'),
    templateId: report.templateId,
    checks: sanitizeChecks(report.checks),
    failures: Array.isArray(report.failures) ? report.failures : [],
    validation: report.validation,
  };
}

function sanitizeChecks(checks) {
  return (Array.isArray(checks) ? checks : []).map((check) => {
    const sanitized = {
      id: check.id,
      status: check.status,
    };
    if (check.message) {
      sanitized.message = check.message;
    }
    return sanitized;
  });
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
    adapterSamplePath: path.join(packageDir, files.adapterSample),
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
    adapterSample: manifest.adapterSample || 'adapter-sample.render-plan.json',
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
