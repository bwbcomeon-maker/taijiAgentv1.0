const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { renderTemplateSample } = require('./render-template-sample');
const {
  assertSafeDirectoryTree,
  assertSafeInstallTarget,
  computeTemplateContentDigest,
  loadTemplateRegistry,
  nextTemplateRevisionDigest,
  readJsonRegularFile,
  resolveContainedFilePath,
  updateTemplateRegistry,
} = require('./template-store');
const { validateTemplatePackage } = require('./validate-template-package');

async function installTemplatePackage({
  rootDir = path.resolve(__dirname, '../..'),
  builtinRootDir,
  runtimeRootDir,
  packageDir,
  installRoot = 'installed',
  replace = false,
} = {}) {
  if (!packageDir) {
    throw new Error('packageDir is required.');
  }

  const storeOptions = { rootDir, builtinRootDir, runtimeRootDir };
  const { store, registryPath, registry } = loadTemplateRegistry(storeOptions);
  const absoluteRootDir = store.runtimeRootDir;
  const absolutePackageDir = path.resolve(packageDir);
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
  const absoluteInstallRoot = path.resolve(absoluteRootDir, installRoot);
  assertSafeInstallTarget({ store, installRootDir: absoluteInstallRoot, targetDir });
  if (installedIndex < 0) {
    assertTargetAvailable(targetDir);
  }

  const action = installedIndex >= 0 ? 'replaced' : 'installed';
  const installReportPath = path.join(targetDir, 'template-install-report.json');
  assertSafeInstallTarget({ store, installRootDir: absoluteInstallRoot, targetDir });
  const preparedDir = prepareInstallSnapshot({
    sourceDir: absolutePackageDir,
    targetDir,
  });
  let committedDirectory = null;
  let registryEntry = null;
  try {
    assertSafeDirectoryTree(absolutePackageDir, 'Template package source');
    assertSafeDirectoryTree(preparedDir, 'Prepared template snapshot');
    const preparedTemplate = loadPackageFromDir({
      packageDir: preparedDir,
      registryPath,
      registrySource: 'incoming',
    });
    const validation = validateTemplatePackage(preparedTemplate);
    assertSafeDirectoryTree(preparedDir, 'Prepared template snapshot');
    if (!validation.ok) {
      throw new Error(`Template package validation failed: ${JSON.stringify(validation.errors)}`);
    }
    const sampleRender = await assertTemplateSampleRenders(preparedDir);
    assertSafeDirectoryTree(absolutePackageDir, 'Template package source');
    assertSafeDirectoryTree(preparedDir, 'Prepared template snapshot');

    const contentDigest = computeTemplateContentDigest(preparedDir);
    const revisionDigest = nextTemplateRevisionDigest({
      previousEntry: existingEntry,
      contentDigest,
    });
    registryEntry = {
      ...(existingEntry || {}),
      templateId,
      path: relativeInstallPath,
      contentDigest,
      revisionDigest,
    };
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
    writeJson(path.join(preparedDir, 'template-install-report.json'), installReport);
    assertSafeDirectoryTree(absolutePackageDir, 'Template package source');
    assertSafeDirectoryTree(preparedDir, 'Prepared template snapshot');
    assertSafeInstallTarget({ store, installRootDir: absoluteInstallRoot, targetDir });
    updateTemplateRegistry(storeOptions, ({ store: currentStore, registry: currentRegistry }) => {
      assertSafeInstallTarget({
        store: currentStore,
        installRootDir: absoluteInstallRoot,
        targetDir,
      });
      assertRegistryStillInstallable({
        registry: currentRegistry,
        templateId,
        replace,
        expectedExistingEntry: existingEntry,
      });
      if (!existingEntry) {
        assertTargetAvailable(targetDir);
      }
      assertSafeDirectoryTree(preparedDir, 'Prepared template snapshot');
      committedDirectory = commitPreparedDirectory({ preparedDir, targetDir });
      const currentInstalledEntries = Array.isArray(currentRegistry.installed)
        ? [...currentRegistry.installed]
        : [];
      const currentInstalledIndex = currentInstalledEntries.findIndex(
        (entry) => (entry.templateId || entry.id) === templateId
      );
      if (currentInstalledIndex >= 0) {
        currentInstalledEntries[currentInstalledIndex] = registryEntry;
      } else {
        currentInstalledEntries.push(registryEntry);
      }
      currentRegistry.installed = currentInstalledEntries;
    });
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

function prepareInstallSnapshot({ sourceDir, targetDir }) {
  const tempDir = createSiblingTempDir(targetDir, 'new');
  fs.mkdirSync(path.dirname(targetDir), { recursive: true });
  fs.rmSync(tempDir, { recursive: true, force: true });
  try {
    fs.cpSync(sourceDir, tempDir, { recursive: true, errorOnExist: true });
    assertSafeDirectoryTree(sourceDir, 'Template package source');
    assertSafeDirectoryTree(tempDir, 'Prepared template snapshot');
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
  assertSafeDirectoryTree(packageDir, 'Template package');
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJsonRegularFile(manifestPath, 'Template manifest');
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
    templatePath: resolveContainedFilePath(packageDir, files.template, 'Template DOCX'),
    schemaPath: resolveContainedFilePath(packageDir, files.schema, 'Template schema'),
    promptPath: resolveContainedFilePath(packageDir, files.prompt, 'Template prompt'),
    samplePath: resolveContainedFilePath(packageDir, files.sample, 'Template sample'),
    dataAdapterPath: resolveContainedFilePath(packageDir, files.dataAdapter, 'Template data adapter'),
    adapterSamplePath: resolveContainedFilePath(packageDir, files.adapterSample, 'Template adapter sample'),
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

function findRegistryEntry(entries, templateId) {
  return (Array.isArray(entries) ? entries : []).find((entry) => (entry.templateId || entry.id) === templateId) || null;
}

function assertRegistryStillInstallable({
  registry,
  templateId,
  replace,
  expectedExistingEntry,
}) {
  if (findRegistryEntry(registry.builtin, templateId)) {
    if (replace) {
      throw new Error(`Cannot replace builtin template: ${templateId}`);
    }
    throw new Error(`Template already exists: ${templateId}`);
  }
  const currentEntry = findRegistryEntry(registry.installed, templateId);
  if (!expectedExistingEntry && currentEntry) {
    throw new Error(`Template already exists: ${templateId}`);
  }
  if (expectedExistingEntry && JSON.stringify(currentEntry) !== JSON.stringify(expectedExistingEntry)) {
    throw new Error(`Template registry changed during installation: ${templateId}`);
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
