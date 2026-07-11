const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const DEFAULT_ENGINE_ROOT = path.resolve(__dirname, '../..');
const REGISTRY_FILE_NAME = 'template-registry.json';
const LOCK_WAIT_MS = 25;
const LOCK_TIMEOUT_MS = 5000;

function resolveTemplateStore({
  rootDir,
  builtinRootDir,
  runtimeRootDir,
  env = process.env,
} = {}) {
  const legacyRootDir = path.resolve(rootDir || DEFAULT_ENGINE_ROOT);
  const environmentBuiltinRoot = cleanPathValue(env.TAIJI_DOCX_BUILTIN_ROOT);
  const environmentRuntimeRoot = cleanPathValue(env.TAIJI_DOCX_RUNTIME_HOME);
  const taijiRuntimeHome = cleanPathValue(env.TAIJI_RUNTIME_HOME);
  const explicitRuntimeBoundary = Boolean(runtimeRootDir || environmentRuntimeRoot || taijiRuntimeHome);
  const splitRequested = Boolean(
    builtinRootDir ||
    runtimeRootDir ||
    environmentBuiltinRoot ||
    environmentRuntimeRoot ||
    taijiRuntimeHome
  );
  const resolvedBuiltinRoot = path.resolve(
    builtinRootDir || environmentBuiltinRoot || legacyRootDir
  );
  const resolvedRuntimeRoot = path.resolve(
    runtimeRootDir ||
    environmentRuntimeRoot ||
    (taijiRuntimeHome ? path.join(taijiRuntimeHome, 'docx-engine-v2') : '') ||
    (splitRequested ? defaultXdgRuntimeRoot(env) : legacyRootDir)
  );

  return {
    builtinRootDir: resolvedBuiltinRoot,
    runtimeRootDir: resolvedRuntimeRoot,
    builtinRegistryPath: path.join(resolvedBuiltinRoot, REGISTRY_FILE_NAME),
    registryPath: path.join(resolvedRuntimeRoot, REGISTRY_FILE_NAME),
    installedRootDir: path.join(resolvedRuntimeRoot, 'installed'),
    split: resolvedBuiltinRoot !== resolvedRuntimeRoot,
    explicitRuntimeBoundary,
  };
}

function loadTemplateRegistry(options = {}) {
  const store = resolveTemplateStore(options);
  assertSafeDirectory(store.builtinRootDir, 'Builtin template root');
  assertSafeRuntimeRootBoundary(store);

  if (!store.split) {
    const registry = readAndValidateRegistry(store.builtinRegistryPath, {
      label: 'Template registry',
      store,
      source: 'legacy',
    });
    return { store, registry, registryPath: store.builtinRegistryPath };
  }

  ensureRuntimeDirectories(store);
  assertSafeRuntimeRootBoundary(store);
  return withRegistryLock(store, () => loadMergedRuntimeRegistryLocked(store));
}

function updateTemplateRegistry(options, update) {
  if (typeof update !== 'function') {
    throw new Error('Template registry update callback is required.');
  }
  const store = resolveTemplateStore(options);
  assertSafeDirectory(store.builtinRootDir, 'Builtin template root');
  assertSafeRuntimeRootBoundary(store);
  if (store.split) {
    ensureRuntimeDirectories(store);
    assertSafeRuntimeRootBoundary(store);
  } else {
    assertSafeDirectory(store.runtimeRootDir, 'Template root');
  }

  return withRegistryLock(store, () => {
    const current = store.split
      ? loadMergedRuntimeRegistryLocked(store)
      : {
          store,
          registryPath: store.registryPath,
          registry: readAndValidateRegistry(store.registryPath, {
            label: 'Template registry',
            store,
            source: 'legacy',
          }),
        };
    const workingRegistry = cloneJson(current.registry);
    const result = update({
      store,
      registry: workingRegistry,
      registryPath: current.registryPath,
    });
    validateRegistry(workingRegistry, {
      label: 'Updated template registry',
      store,
      source: store.split ? 'runtime' : 'legacy',
    });
    writeJsonAtomic(current.registryPath, workingRegistry);
    return {
      store,
      registry: workingRegistry,
      registryPath: current.registryPath,
      result,
    };
  });
}

function loadMergedRuntimeRegistryLocked(store) {
  const seedRegistry = readAndValidateRegistry(store.builtinRegistryPath, {
    label: 'Builtin template registry',
    store,
    source: 'seed',
  });
  if ((seedRegistry.installed || []).length > 0) {
    throw new Error('Builtin template registry must not contain installed templates.');
  }

  const runtimeExists = fs.existsSync(store.registryPath);
  const runtimeRegistry = runtimeExists
    ? readAndValidateRegistry(store.registryPath, {
        label: 'Writable template registry',
        store,
        source: 'runtime',
      })
    : { version: seedRegistry.version || 1, builtin: [], installed: [] };
  const mergedRegistry = mergeRegistries(seedRegistry, runtimeRegistry);
  validateRegistry(mergedRegistry, {
    label: 'Merged template registry',
    store,
    source: 'runtime',
  });
  if (!runtimeExists || stableJson(runtimeRegistry) !== stableJson(mergedRegistry)) {
    writeJsonAtomic(store.registryPath, mergedRegistry);
  }
  return { store, registry: mergedRegistry, registryPath: store.registryPath };
}

function mergeRegistries(seedRegistry, runtimeRegistry) {
  const installed = cloneEntries(runtimeRegistry.installed);
  const installedIds = new Set(installed.map(templateEntryId));
  const builtin = [];
  const builtinIds = new Set();

  for (const entry of cloneEntries(runtimeRegistry.builtin)) {
    const templateId = templateEntryId(entry);
    if (installedIds.has(templateId) || builtinIds.has(templateId)) {
      continue;
    }
    builtin.push(entry);
    builtinIds.add(templateId);
  }
  for (const entry of cloneEntries(seedRegistry.builtin)) {
    const templateId = templateEntryId(entry);
    if (installedIds.has(templateId) || builtinIds.has(templateId)) {
      continue;
    }
    builtin.push(entry);
    builtinIds.add(templateId);
  }

  return {
    ...cloneJson(runtimeRegistry),
    version: seedRegistry.version || runtimeRegistry.version || 1,
    builtin,
    installed,
  };
}

function resolveRegistryPackageDir({ store, registryEntry, registrySource }) {
  const templateId = templateEntryId(registryEntry);
  assertSafeTemplateId(templateId);
  const source = registrySource === 'installed' ? 'installed' : 'builtin';
  const entryPath = String(
    registryEntry.path ||
    (source === 'installed'
      ? path.join('installed', templateId)
      : path.join('templates', templateId))
  );
  if (entryPath.includes('\0')) {
    throw new Error(`Template registry path contains a null byte: ${templateId}`);
  }

  const baseRoot = source === 'installed' ? store.runtimeRootDir : store.builtinRootDir;
  const allowedRoot = source === 'installed' ? store.installedRootDir : store.builtinRootDir;
  if (store.split && path.isAbsolute(entryPath)) {
    throw new Error(`Split template registry path must be relative: ${templateId}: ${entryPath}`);
  }
  const packageDir = path.isAbsolute(entryPath)
    ? path.resolve(entryPath)
    : path.resolve(baseRoot, entryPath);
  const legacyExternalBuiltin = (
    !store.split &&
    source === 'builtin' &&
    path.isAbsolute(entryPath) &&
    !isPathWithin(allowedRoot, packageDir)
  );
  if (!legacyExternalBuiltin) {
    assertPathWithin(allowedRoot, packageDir, `Template registry path is outside its managed root: ${templateId}`);
    assertNoSymlinkPath(allowedRoot, packageDir, `Template package path contains a symbolic link: ${templateId}`);
  }
  return packageDir;
}

function assertSafeInstallTarget({ store, installRootDir, targetDir }) {
  ensureSafeDirectory(store.installedRootDir, 'Writable installed templates root');
  assertPathAtOrWithin(
    store.installedRootDir,
    installRootDir,
    'Template install root is outside the managed installed directory'
  );
  assertPathWithin(
    installRootDir,
    targetDir,
    'Installed template path is outside the requested install root'
  );
  if (path.resolve(installRootDir) !== path.resolve(store.installedRootDir)) {
    assertNoSymlinkPath(
      store.installedRootDir,
      installRootDir,
      'Template install root contains a symbolic link'
    );
  }
  assertNoSymlinkPath(
    store.installedRootDir,
    targetDir,
    'Installed template path contains a symbolic link'
  );
}

function computeTemplateContentDigest(packageDir) {
  assertSafeDirectoryTree(packageDir, 'Template package digest source');
  const hash = crypto.createHash('sha256');
  walk(packageDir, '');
  return `sha256:${hash.digest('hex')}`;

  function walk(currentDir, relativeDir) {
    const entries = fs.readdirSync(currentDir, { withFileTypes: true })
      .sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const entryPath = path.join(currentDir, entry.name);
      const relativePath = path.posix.join(relativeDir, entry.name);
      const stat = fs.lstatSync(entryPath);
      if (stat.isSymbolicLink()) {
        throw new Error(`Template package digest source contains a symbolic link: ${entryPath}`);
      }
      if (stat.isDirectory()) {
        hash.update(`directory\0${relativePath}/\0`);
        walk(entryPath, relativePath);
        continue;
      }
      if (!stat.isFile()) {
        throw new Error(`Template package digest source contains an unsupported non-regular file: ${entryPath}`);
      }
      if (relativePath === 'template-install-report.json') {
        continue;
      }
      const contents = fs.readFileSync(entryPath);
      hash.update(`file\0${relativePath}\0${contents.length}\0`);
      hash.update(contents);
    }
  }
}

function nextTemplateRevisionDigest({ previousEntry = null, contentDigest }) {
  if (!/^sha256:[a-f0-9]{64}$/.test(String(contentDigest || ''))) {
    throw new Error(`Invalid template content digest: ${contentDigest || ''}`);
  }
  const previousRevision = String(previousEntry?.revisionDigest || '') ||
    `legacy:${crypto.createHash('sha256').update(stableJson(previousEntry || {})).digest('hex')}`;
  const digest = crypto.createHash('sha256')
    .update(`docx-engine-v2/template-revision/v1\0${previousRevision}\0${contentDigest}`)
    .digest('hex');
  return `sha256:${digest}`;
}

function assertSafeDirectoryTree(rootDir, label = 'Template package') {
  assertSafeDirectory(rootDir, label);
  walk(rootDir);

  function walk(currentDir) {
    for (const entry of fs.readdirSync(currentDir, { withFileTypes: true })) {
      const entryPath = path.join(currentDir, entry.name);
      const stat = fs.lstatSync(entryPath);
      if (stat.isSymbolicLink()) {
        throw new Error(`${label} contains a symbolic link: ${entryPath}`);
      }
      if (stat.isDirectory()) {
        walk(entryPath);
        continue;
      }
      if (!stat.isFile()) {
        throw new Error(`${label} contains an unsupported non-regular file: ${entryPath}`);
      }
    }
  }
}

function assertSafeRegularFile(filePath, label = 'File') {
  let stat;
  try {
    stat = fs.lstatSync(filePath);
  } catch (error) {
    if (error.code === 'ENOENT') {
      const missingError = new Error(`${label} not found: ${filePath}`);
      missingError.code = 'ENOENT';
      throw missingError;
    }
    throw error;
  }
  if (stat.isSymbolicLink()) {
    throw new Error(`${label} must not be a symbolic link: ${filePath}`);
  }
  if (!stat.isFile()) {
    throw new Error(`${label} must be a regular file: ${filePath}`);
  }
}

function resolveContainedFilePath(packageDir, fileName, label) {
  const relativePath = String(fileName || '');
  if (!relativePath || path.isAbsolute(relativePath) || relativePath.includes('\0')) {
    throw new Error(`${label} must be a relative path inside the template package: ${relativePath}`);
  }
  const filePath = path.resolve(packageDir, relativePath);
  assertPathWithin(packageDir, filePath, `${label} path is outside the template package`);
  assertNoSymlinkPath(packageDir, filePath, `${label} path contains a symbolic link`);
  return filePath;
}

function readJsonRegularFile(filePath, label = 'JSON file') {
  assertSafeRegularFile(filePath, label);
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJsonAtomic(filePath, value) {
  const parentDir = path.dirname(filePath);
  assertSafeDirectory(parentDir, 'Template registry directory');
  if (fs.existsSync(filePath)) {
    assertSafeRegularFile(filePath, 'Template registry');
  }
  const existingMode = fs.existsSync(filePath)
    ? fs.lstatSync(filePath).mode & 0o777
    : 0o600;
  const temporaryPath = path.join(
    parentDir,
    `.${path.basename(filePath)}.tmp-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
  let descriptor;
  try {
    descriptor = fs.openSync(
      temporaryPath,
      fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | noFollowFlag(),
      existingMode
    );
    fs.writeFileSync(descriptor, stableJson(value), 'utf8');
    fs.fchmodSync(descriptor, existingMode);
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    fs.renameSync(temporaryPath, filePath);
    syncDirectory(parentDir);
  } catch (error) {
    if (descriptor !== undefined) {
      fs.closeSync(descriptor);
    }
    fs.rmSync(temporaryPath, { force: true });
    throw error;
  }
}

function readAndValidateRegistry(registryPath, context) {
  const registry = readJsonRegularFile(registryPath, context.label);
  validateRegistry(registry, context);
  return registry;
}

function validateRegistry(registry, { label, store, source }) {
  if (!registry || typeof registry !== 'object' || Array.isArray(registry)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  for (const field of ['builtin', 'installed']) {
    if (registry[field] !== undefined && !Array.isArray(registry[field])) {
      throw new Error(`${label}.${field} must be an array.`);
    }
  }

  if (source === 'legacy') {
    const builtinIds = new Set((registry.builtin || []).map(templateEntryId));
    for (const templateId of (registry.installed || []).map(templateEntryId)) {
      if (builtinIds.has(templateId)) {
        throw new Error(`Duplicate template id in registry: ${templateId}`);
      }
    }
  }

  for (const [registrySource, entries] of [
    ['builtin', registry.builtin || []],
    ['installed', registry.installed || []],
  ]) {
    const seen = new Set();
    for (const entry of entries) {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        throw new Error(`${label}.${registrySource} entries must be objects.`);
      }
      const templateId = templateEntryId(entry);
      assertSafeTemplateId(templateId);
      if (seen.has(templateId)) {
        throw new Error(`Duplicate template id in ${label}.${registrySource}: ${templateId}`);
      }
      seen.add(templateId);
      if (entry.path !== undefined && typeof entry.path !== 'string') {
        throw new Error(`Template registry path must be a string: ${templateId}`);
      }
      for (const digestField of ['contentDigest', 'revisionDigest']) {
        if (
          entry[digestField] !== undefined &&
          !/^sha256:[a-f0-9]{64}$/.test(String(entry[digestField]))
        ) {
          throw new Error(`Invalid template registry ${digestField}: ${templateId}`);
        }
      }
      if (source !== 'seed' || registrySource === 'builtin') {
        resolveRegistryEntryPathShape({ store, entry, registrySource, source });
      }
    }
  }
}

function resolveRegistryEntryPathShape({ store, entry, registrySource, source }) {
  const templateId = templateEntryId(entry);
  const entryPath = String(
    entry.path ||
    (registrySource === 'installed'
      ? path.join('installed', templateId)
      : path.join('templates', templateId))
  );
  if (entryPath.includes('\0')) {
    throw new Error(`Template registry path contains a null byte: ${templateId}`);
  }
  if (store.split && path.isAbsolute(entryPath)) {
    throw new Error(`Split template registry path must be relative: ${templateId}: ${entryPath}`);
  }
  const baseRoot = registrySource === 'installed' ? store.runtimeRootDir : store.builtinRootDir;
  const allowedRoot = registrySource === 'installed' ? store.installedRootDir : store.builtinRootDir;
  const resolved = path.isAbsolute(entryPath) ? path.resolve(entryPath) : path.resolve(baseRoot, entryPath);
  if (!(source === 'legacy' && registrySource === 'builtin' && path.isAbsolute(entryPath))) {
    const outsideMessage = registrySource === 'installed'
      ? 'Installed template path is outside the managed installed directory'
      : `Template registry path is outside its managed root: ${templateId}`;
    assertPathWithin(allowedRoot, resolved, outsideMessage);
  }
}

function ensureRuntimeDirectories(store) {
  ensureSafeDirectory(store.runtimeRootDir, 'Writable template runtime root');
  ensureSafeDirectory(store.installedRootDir, 'Writable installed templates root');
}

function assertSafeRuntimeRootBoundary(store) {
  if (!store.explicitRuntimeBoundary) {
    return;
  }
  const resolved = path.resolve(store.runtimeRootDir);
  const parsed = path.parse(resolved);
  let current = parsed.root;
  for (const segment of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = fs.lstatSync(current);
    } catch (error) {
      if (error.code === 'ENOENT') {
        return;
      }
      throw error;
    }
    if (stat.isSymbolicLink()) {
      throw new Error(`Explicit template runtime root contains a symbolic link: ${current}`);
    }
  }
}

function ensureSafeDirectory(directoryPath, label) {
  fs.mkdirSync(directoryPath, { recursive: true, mode: 0o700 });
  assertSafeDirectory(directoryPath, label);
}

function assertSafeDirectory(directoryPath, label) {
  let stat;
  try {
    stat = fs.lstatSync(directoryPath);
  } catch (error) {
    if (error.code === 'ENOENT') {
      throw new Error(`${label} not found: ${directoryPath}`);
    }
    throw error;
  }
  if (stat.isSymbolicLink()) {
    throw new Error(`${label} must not be a symbolic link: ${directoryPath}`);
  }
  if (!stat.isDirectory()) {
    throw new Error(`${label} must be a directory: ${directoryPath}`);
  }
}

function assertNoSymlinkPath(rootDir, targetPath, message) {
  assertPathWithin(rootDir, targetPath, message);
  let current = path.resolve(rootDir);
  assertSafeDirectory(current, 'Template path root');
  const relative = path.relative(current, path.resolve(targetPath));
  for (const segment of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = fs.lstatSync(current);
    } catch (error) {
      if (error.code === 'ENOENT') {
        break;
      }
      throw error;
    }
    if (stat.isSymbolicLink()) {
      throw new Error(`${message}: ${current}`);
    }
  }
}

function assertPathWithin(rootDir, targetPath, message) {
  const resolvedRoot = path.resolve(rootDir);
  const resolvedTarget = path.resolve(targetPath);
  const relative = path.relative(resolvedRoot, resolvedTarget);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`${message}: ${resolvedTarget}`);
  }
}

function assertPathAtOrWithin(rootDir, targetPath, message) {
  const resolvedRoot = path.resolve(rootDir);
  const resolvedTarget = path.resolve(targetPath);
  const relative = path.relative(resolvedRoot, resolvedTarget);
  if (!relative || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))) {
    return;
  }
  throw new Error(`${message}: ${resolvedTarget}`);
}

function isPathWithin(rootDir, targetPath) {
  const relative = path.relative(path.resolve(rootDir), path.resolve(targetPath));
  return Boolean(relative) && !relative.startsWith('..') && !path.isAbsolute(relative);
}

function withRegistryLock(store, operation) {
  const lockPath = `${store.registryPath}.lock`;
  const startedAt = Date.now();
  let descriptor;
  while (descriptor === undefined) {
    try {
      descriptor = fs.openSync(
        lockPath,
        fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | noFollowFlag(),
        0o600
      );
    } catch (error) {
      if (error.code !== 'EEXIST') {
        throw error;
      }
      try {
        assertSafeRegularFile(lockPath, 'Template registry lock');
      } catch (lockError) {
        if (lockError.code === 'ENOENT' || !fs.existsSync(lockPath)) {
          continue;
        }
        throw lockError;
      }
      if (removeDeadProcessLock(lockPath)) {
        continue;
      }
      if (Date.now() - startedAt >= LOCK_TIMEOUT_MS) {
        throw new Error(`Timed out waiting for template registry lock: ${lockPath}`);
      }
      waitSynchronously(LOCK_WAIT_MS);
    }
  }

  try {
    fs.writeFileSync(descriptor, `${process.pid}\n`, 'utf8');
    return operation();
  } finally {
    fs.closeSync(descriptor);
    fs.rmSync(lockPath, { force: true });
  }
}

function removeDeadProcessLock(lockPath) {
  let initialStat;
  let ownerPid;
  try {
    initialStat = fs.lstatSync(lockPath);
    const ownerText = fs.readFileSync(lockPath, 'utf8').trim();
    if (!/^\d+$/.test(ownerText)) {
      return false;
    }
    ownerPid = Number(ownerText);
  } catch (error) {
    return error.code === 'ENOENT';
  }
  if (!Number.isSafeInteger(ownerPid) || ownerPid <= 0 || isProcessAlive(ownerPid)) {
    return false;
  }

  let currentStat;
  try {
    currentStat = fs.lstatSync(lockPath);
  } catch (error) {
    return error.code === 'ENOENT';
  }
  if (currentStat.dev !== initialStat.dev || currentStat.ino !== initialStat.ino) {
    return false;
  }
  fs.unlinkSync(lockPath);
  return true;
}

function isProcessAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code !== 'ESRCH';
  }
}

function defaultXdgRuntimeRoot(env) {
  const xdgDataHome = cleanPathValue(env.XDG_DATA_HOME) || path.join(os.homedir(), '.local', 'share');
  return path.join(xdgDataHome, 'taiji-agent', 'docx-engine-v2');
}

function cleanPathValue(value) {
  return String(value || '').trim();
}

function templateEntryId(entry) {
  return String(entry?.templateId || entry?.id || '');
}

function assertSafeTemplateId(templateId) {
  if (!/^[A-Za-z0-9_-]+$/.test(templateId || '')) {
    throw new Error(`Invalid template id: ${templateId || ''}`);
  }
}

function cloneEntries(entries) {
  return cloneJson(Array.isArray(entries) ? entries : []);
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function stableJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function waitSynchronously(milliseconds) {
  const signal = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(signal, 0, 0, milliseconds);
}

function noFollowFlag() {
  return fs.constants.O_NOFOLLOW || 0;
}

function syncDirectory(directoryPath) {
  let descriptor;
  try {
    descriptor = fs.openSync(directoryPath, fs.constants.O_RDONLY);
    fs.fsyncSync(descriptor);
  } catch (error) {
    // The registry has already been atomically replaced. Directory fsync is a
    // durability improvement and must not turn a successful replace into a
    // false transactional failure on filesystems that reject directory fsync.
  } finally {
    if (descriptor !== undefined) {
      fs.closeSync(descriptor);
    }
  }
}

module.exports = {
  assertSafeDirectoryTree,
  assertSafeInstallTarget,
  assertSafeRegularFile,
  computeTemplateContentDigest,
  loadTemplateRegistry,
  readJsonRegularFile,
  resolveContainedFilePath,
  resolveRegistryPackageDir,
  resolveTemplateStore,
  nextTemplateRevisionDigest,
  updateTemplateRegistry,
  writeJsonAtomic,
};
