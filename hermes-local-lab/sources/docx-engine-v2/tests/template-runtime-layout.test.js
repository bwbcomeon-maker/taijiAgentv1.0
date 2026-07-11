const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const test = require('node:test');

const { installTemplatePackage } = require('../src/templates/install-template-package');
const { listTemplates } = require('../src/templates/registry');
const {
  quarantineStaleRegistryLock,
  readRegistryLockOwner,
  releaseRegistryLock,
} = require('../src/templates/template-store');

const ENGINE_ROOT = path.join(__dirname, '..');
const LIST_TEMPLATES = path.join(ENGINE_ROOT, 'src', 'cli', 'list-templates.js');
const INSTALL_TEMPLATE = path.join(ENGINE_ROOT, 'src', 'cli', 'install-template.js');
const RUN_JOB = path.join(ENGINE_ROOT, 'src', 'cli', 'run-job.js');

function makeTempRoot(t) {
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'docx-engine-v2-runtime-layout-'));
  t.after(() => {
    makeTreeWritable(root);
    fs.rmSync(root, { recursive: true, force: true });
  });
  return root;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function writeRegistryLockDirectory(lockPath, { pid, token }) {
  const owner = {
    schema: 'taiji.docx.registry-lock.v1',
    pid,
    token,
  };
  fs.mkdirSync(lockPath, { mode: 0o700 });
  fs.writeFileSync(
    path.join(lockPath, 'owner.json'),
    `${JSON.stringify(owner, null, 2)}\n`,
    { encoding: 'utf8', mode: 0o600 }
  );
  return owner;
}

function copyBuiltinTemplate({ seedRoot, sourceId, templateId = sourceId }) {
  const targetDir = path.join(seedRoot, 'templates', templateId);
  fs.cpSync(path.join(ENGINE_ROOT, 'templates', sourceId), targetDir, { recursive: true });
  if (templateId !== sourceId) {
    const manifestPath = path.join(targetDir, 'manifest.json');
    writeJson(manifestPath, {
      ...readJson(manifestPath),
      id: templateId,
      name: `Builtin ${templateId}`,
    });
  }
  return targetDir;
}

function makeSeed(root, templateIds = ['general-proposal', 'meeting-minutes']) {
  const seedRoot = path.join(root, 'seed');
  fs.mkdirSync(seedRoot, { recursive: true });
  for (const templateId of templateIds) {
    copyBuiltinTemplate({ seedRoot, sourceId: templateId });
  }
  writeJson(path.join(seedRoot, 'template-registry.json'), {
    version: 1,
    builtin: templateIds.map((templateId) => ({
      templateId,
      path: `templates/${templateId}`,
    })),
    installed: [],
  });
  return seedRoot;
}

function makeIncomingTemplate(root, templateId) {
  const packageDir = path.join(root, 'incoming', templateId);
  fs.cpSync(path.join(ENGINE_ROOT, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  writeJson(manifestPath, {
    ...readJson(manifestPath),
    id: templateId,
    name: `User ${templateId}`,
  });
  return packageDir;
}

function addDelayedSymlinkMutation({ packageDir, counterPath, linkPath, outsidePath, trigger = 3 }) {
  const adapterPath = path.join(packageDir, 'data-adapter.js');
  const adapterSource = fs.readFileSync(adapterPath, 'utf8');
  fs.writeFileSync(
    adapterPath,
    [
      "const __runtimeMutationFs = require('node:fs');",
      `const __runtimeMutationCounter = ${JSON.stringify(counterPath)};`,
      linkPath
        ? `const __runtimeMutationLink = ${JSON.stringify(linkPath)};`
        : "const __runtimeMutationLink = require('node:path').join(__dirname, 'late-link');",
      `const __runtimeMutationOutside = ${JSON.stringify(outsidePath)};`,
      "let __runtimeMutationCount = 0;",
      "try { __runtimeMutationCount = Number(__runtimeMutationFs.readFileSync(__runtimeMutationCounter, 'utf8')) || 0; } catch (error) { if (error.code !== 'ENOENT') throw error; }",
      '__runtimeMutationCount += 1;',
      "__runtimeMutationFs.writeFileSync(__runtimeMutationCounter, String(__runtimeMutationCount), 'utf8');",
      `if (__runtimeMutationCount >= ${Number(trigger)}) {`,
      '  __runtimeMutationFs.rmSync(__runtimeMutationLink, { recursive: true, force: true });',
      '  __runtimeMutationFs.symlinkSync(__runtimeMutationOutside, __runtimeMutationLink);',
      '}',
      '',
      adapterSource,
    ].join('\n'),
    'utf8'
  );
}

function makeTreeReadOnly(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      makeTreeReadOnly(entryPath);
    } else {
      fs.chmodSync(entryPath, 0o444);
    }
  }
  fs.chmodSync(root, 0o555);
}

function makeTreeWritable(root) {
  if (!fs.existsSync(root)) {
    return;
  }
  const stat = fs.lstatSync(root);
  if (stat.isSymbolicLink() || !stat.isDirectory()) {
    return;
  }
  fs.chmodSync(root, 0o755);
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      makeTreeWritable(entryPath);
    } else if (!entry.isSymbolicLink()) {
      fs.chmodSync(entryPath, 0o644);
    }
  }
}

function snapshotTree(root) {
  const snapshot = {};
  walk(root, '');
  return snapshot;

  function walk(currentDir, relativeDir) {
    for (const entry of fs.readdirSync(currentDir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const relativePath = path.posix.join(relativeDir, entry.name);
      const absolutePath = path.join(currentDir, entry.name);
      if (entry.isDirectory()) {
        snapshot[`${relativePath}/`] = 'directory';
        walk(absolutePath, relativePath);
      } else {
        snapshot[relativePath] = crypto.createHash('sha256').update(fs.readFileSync(absolutePath)).digest('hex');
      }
    }
  }
}

function packagedEnv(seedRoot, runtimeRoot) {
  return {
    ...process.env,
    TAIJI_DOCX_BUILTIN_ROOT: seedRoot,
    TAIJI_DOCX_RUNTIME_HOME: runtimeRoot,
  };
}

function runCli(scriptPath, args, env) {
  return spawnSync(process.execPath, [scriptPath, ...args], {
    cwd: ENGINE_ROOT,
    encoding: 'utf8',
    env,
  });
}

function runListProcess(env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [LIST_TEMPLATES, '--json'], {
      cwd: ENGINE_ROOT,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (status) => resolve({ status, stdout, stderr }));
  });
}

async function waitForFileCount(directoryPath, prefix, expectedCount, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const count = fs.existsSync(directoryPath)
      ? fs.readdirSync(directoryPath).filter((name) => name.startsWith(prefix)).length
      : 0;
    if (count >= expectedCount) {
      return count;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`Timed out waiting for ${expectedCount} ${prefix} files in ${directoryPath}`);
}

function runInstallProcess(env, packageDir, { replace = false } = {}) {
  return new Promise((resolve, reject) => {
    const args = [INSTALL_TEMPLATE, '--package', packageDir, '--json'];
    if (replace) {
      args.push('--replace');
    }
    const child = spawn(process.execPath, args, {
      cwd: ENGINE_ROOT,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (status) => resolve({ status, stdout, stderr }));
  });
}

function addInstallBarrier({ packageDir, barrierDir, marker }) {
  const adapterPath = path.join(packageDir, 'data-adapter.js');
  const adapterSource = fs.readFileSync(adapterPath, 'utf8');
  fs.writeFileSync(
    adapterPath,
    [
      "const __installBarrierFs = require('node:fs');",
      `const __installBarrierDir = ${JSON.stringify(barrierDir)};`,
      `const __installBarrierMarker = ${JSON.stringify(marker)};`,
      '__installBarrierFs.mkdirSync(__installBarrierDir, { recursive: true });',
      "__installBarrierFs.writeFileSync(require('node:path').join(__installBarrierDir, __installBarrierMarker), 'ready', 'utf8');",
      'const __installBarrierDeadline = Date.now() + 5000;',
      'while (__installBarrierFs.readdirSync(__installBarrierDir).length < 2) {',
      "  if (Date.now() >= __installBarrierDeadline) throw new Error('install barrier timed out');",
      '  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);',
      '}',
      '',
      adapterSource,
    ].join('\n'),
    'utf8'
  );
}

function addRegistryLockBeforeCommit({ packageDir, markerPath, lockPath }) {
  const adapterPath = path.join(packageDir, 'data-adapter.js');
  const adapterSource = fs.readFileSync(adapterPath, 'utf8');
  fs.writeFileSync(
    adapterPath,
    [
      "const __registryBarrierFs = require('node:fs');",
      `const __registryBarrierMarker = ${JSON.stringify(markerPath)};`,
      `const __registryBarrierLock = ${JSON.stringify(lockPath)};`,
      'if (!__registryBarrierFs.existsSync(__registryBarrierMarker)) {',
      "  __registryBarrierFs.writeFileSync(__registryBarrierMarker, 'adapter-loaded', 'utf8');",
      '  __registryBarrierFs.mkdirSync(__registryBarrierLock, { mode: 0o700 });',
      "  __registryBarrierFs.writeFileSync(require('node:path').join(__registryBarrierLock, 'owner.json'), JSON.stringify({ schema: 'taiji.docx.registry-lock.v1', pid: process.pid, token: 'f'.repeat(32) }) + '\\n', { encoding: 'utf8', mode: 0o600 });",
      '}',
      '',
      adapterSource,
    ].join('\n'),
    'utf8'
  );
}

async function runWithPreparedSnapshotMutationAtRegistryLock({
  adapterMarkerPath,
  lockPath,
  installedRoot,
  templateId,
  outsidePath,
  operation,
}) {
  const lockAttemptedPath = `${adapterMarkerPath}.lock-attempted`;
  const mutationCompletePath = `${adapterMarkerPath}.mutation-complete`;
  const mutatorSource = [
    "const fs = require('node:fs');",
    "const path = require('node:path');",
    `const lockAttemptedPath = ${JSON.stringify(lockAttemptedPath)};`,
    `const mutationCompletePath = ${JSON.stringify(mutationCompletePath)};`,
    `const lockPath = ${JSON.stringify(lockPath)};`,
    `const installedRoot = ${JSON.stringify(installedRoot)};`,
    `const templateId = ${JSON.stringify(templateId)};`,
    `const outsidePath = ${JSON.stringify(outsidePath)};`,
    'const deadline = Date.now() + 5000;',
    'while (!fs.existsSync(lockAttemptedPath)) {',
    "  if (Date.now() >= deadline) process.exit(2);",
    '  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);',
    '}',
    "const preparedNames = fs.readdirSync(installedRoot).filter((name) => name.startsWith('.' + templateId + '.') && name.endsWith('.new'));",
    'if (preparedNames.length !== 1) process.exit(3);',
    "fs.symlinkSync(outsidePath, path.join(installedRoot, preparedNames[0], 'late-link'));",
    'fs.rmSync(lockPath, { recursive: true, force: true });',
    "fs.writeFileSync(mutationCompletePath, preparedNames[0], 'utf8');",
  ].join('\n');
  const mutator = spawn(process.execPath, ['-e', mutatorSource], {
    cwd: ENGINE_ROOT,
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  let mutatorStderr = '';
  mutator.stderr.setEncoding('utf8');
  mutator.stderr.on('data', (chunk) => { mutatorStderr += chunk; });
  const mutatorCompletion = new Promise((resolve, reject) => {
    mutator.on('error', reject);
    mutator.on('close', (status, signal) => resolve({ status, signal }));
  });

  const originalReaddirSync = fs.readdirSync;
  let observedLockCollision = false;
  fs.readdirSync = function readdirSyncWithRegistryBarrier(directoryPath, ...args) {
    if (
      !observedLockCollision &&
      path.resolve(String(directoryPath)) === path.resolve(lockPath) &&
      fs.existsSync(adapterMarkerPath)
    ) {
      const entries = originalReaddirSync(directoryPath, ...args);
      observedLockCollision = true;
      fs.writeFileSync(lockAttemptedPath, 'contended', 'utf8');
      const deadline = Date.now() + 5000;
      while (!fs.existsSync(mutationCompletePath)) {
        if (Date.now() >= deadline) {
          throw new Error('Timed out waiting for the prepared snapshot mutation barrier.');
        }
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
      }
      return entries;
    }
    return originalReaddirSync(directoryPath, ...args);
  };

  let operationResult;
  let operationError;
  try {
    operationResult = await operation();
  } catch (error) {
    operationError = error;
  } finally {
    fs.readdirSync = originalReaddirSync;
    fs.rmSync(lockPath, { recursive: true, force: true });
    if (!fs.existsSync(mutationCompletePath)) {
      mutator.kill();
    }
  }
  const mutatorResult = await mutatorCompletion;
  if (operationError) {
    throw operationError;
  }
  assert.equal(observedLockCollision, true, 'installer must collide with the live registry lock after its outside-lock scan');
  assert.equal(mutatorResult.status, 0, `snapshot mutator failed (${mutatorResult.signal || mutatorResult.status}): ${mutatorStderr}`);
  assert.equal(fs.existsSync(lockAttemptedPath), true);
  assert.equal(fs.existsSync(mutationCompletePath), true);
  return operationResult;
}

test('packaged CLI keeps a chmod 0555 builtin seed immutable while list, install, and run use writable runtime state', (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root);
  const runtimeRoot = path.join(root, 'runtime-home');
  const packageDir = makeIncomingTemplate(root, 'custom-proposal');
  const sourcePath = path.join(root, 'meeting.txt');
  const deliveryDir = path.join(root, 'delivery');
  fs.writeFileSync(sourcePath, '会议主题：项目启动会\n会议结论：按计划执行。\n', 'utf8');
  const seedBefore = snapshotTree(seedRoot);
  makeTreeReadOnly(seedRoot);
  const env = packagedEnv(seedRoot, runtimeRoot);

  const listed = runCli(LIST_TEMPLATES, ['--json'], env);
  assert.equal(listed.status, 0, `stdout:\n${listed.stdout}\nstderr:\n${listed.stderr}`);
  assert.deepEqual(
    JSON.parse(listed.stdout).templates.map((template) => template.id),
    ['general-proposal', 'meeting-minutes']
  );

  // Packaged environment routing must take precedence over the legacy CLI root.
  const installed = runCli(INSTALL_TEMPLATE, [
    '--root-dir',
    seedRoot,
    '--package',
    packageDir,
    '--json',
  ], env);
  assert.equal(installed.status, 0, `stdout:\n${installed.stdout}\nstderr:\n${installed.stderr}`);
  const installPayload = JSON.parse(installed.stdout);
  assert.equal(installPayload.registryPath, path.join(runtimeRoot, 'template-registry.json'));
  assert.equal(installPayload.packageDir, path.join(runtimeRoot, 'installed', 'custom-proposal'));

  const rendered = runCli(RUN_JOB, [
    '--template-id',
    'meeting-minutes',
    '--source',
    sourcePath,
    '--out-dir',
    deliveryDir,
    '--json',
  ], env);
  assert.equal(rendered.status, 0, `stdout:\n${rendered.stdout}\nstderr:\n${rendered.stderr}`);
  assert.equal(JSON.parse(rendered.stdout).ok, true);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'document.docx')), true);
  assert.deepEqual(snapshotTree(seedRoot), seedBefore);
  assert.equal(fs.statSync(seedRoot).mode & 0o777, 0o555);
  assert.equal(
    fs.statSync(path.join(seedRoot, 'template-registry.json')).mode & 0o777,
    0o444
  );
  assert.equal(fs.statSync(runtimeRoot).mode & 0o777, 0o700);
  assert.equal(
    fs.statSync(path.join(runtimeRoot, 'template-registry.json')).mode & 0o777,
    0o600
  );
});

test('runtime registry merge is idempotent and installed templates win later builtin id collisions', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');

  assert.deepEqual(
    listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot }).map((template) => template.id),
    ['general-proposal']
  );

  const runtimeRegistryPath = path.join(runtimeRoot, 'template-registry.json');
  const runtimeRegistry = readJson(runtimeRegistryPath);
  runtimeRegistry.builtin[0].userLabel = 'preserve-me';
  writeJson(runtimeRegistryPath, runtimeRegistry);

  const packageDir = makeIncomingTemplate(root, 'future-builtin');
  await installTemplatePackage({
    rootDir: seedRoot,
    builtinRootDir: seedRoot,
    runtimeRootDir: runtimeRoot,
    packageDir,
  });
  copyBuiltinTemplate({ seedRoot, sourceId: 'meeting-minutes' });
  copyBuiltinTemplate({ seedRoot, sourceId: 'general-proposal', templateId: 'future-builtin' });
  writeJson(path.join(seedRoot, 'template-registry.json'), {
    version: 2,
    builtin: [
      { templateId: 'general-proposal', path: 'templates/general-proposal', seedRelease: 2 },
      { templateId: 'meeting-minutes', path: 'templates/meeting-minutes' },
      { templateId: 'future-builtin', path: 'templates/future-builtin' },
    ],
    installed: [],
  });

  const first = listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot });
  const registryAfterFirst = fs.readFileSync(runtimeRegistryPath, 'utf8');
  const second = listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot });
  const registryAfterSecond = fs.readFileSync(runtimeRegistryPath, 'utf8');

  assert.deepEqual(first.map((template) => template.id), [
    'general-proposal',
    'meeting-minutes',
    'future-builtin',
  ]);
  assert.deepEqual(second.map((template) => template.id), first.map((template) => template.id));
  assert.equal(first.find((template) => template.id === 'future-builtin').registrySource, 'installed');
  assert.equal(first.find((template) => template.id === 'general-proposal').registryEntry.userLabel, 'preserve-me');
  assert.equal(registryAfterSecond, registryAfterFirst);
  assert.deepEqual(
    readJson(runtimeRegistryPath).builtin.map((entry) => entry.templateId),
    ['general-proposal', 'meeting-minutes']
  );
});

test('concurrent first-use initialization atomically produces one valid runtime registry', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root);
  const runtimeRoot = path.join(root, 'runtime-home');
  const env = packagedEnv(seedRoot, runtimeRoot);

  const results = await Promise.all(Array.from({ length: 8 }, () => runListProcess(env)));

  for (const result of results) {
    assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
    assert.deepEqual(
      JSON.parse(result.stdout).templates.map((template) => template.id),
      ['general-proposal', 'meeting-minutes']
    );
  }
  assert.deepEqual(
    readJson(path.join(runtimeRoot, 'template-registry.json')).builtin.map((entry) => entry.templateId),
    ['general-proposal', 'meeting-minutes']
  );
  assert.deepEqual(
    fs.readdirSync(runtimeRoot).filter((name) => name.includes('.tmp-') || name.endsWith('.lock')),
    []
  );
});

test('registry lock is published only after its owner identity is complete', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root);
  const runtimeRoot = path.join(root, 'runtime-home');
  const lockPath = path.join(runtimeRoot, 'template-registry.json.lock');
  const barrierDir = path.join(root, 'owner-publish-barrier');
  const readyPath = path.join(barrierDir, 'ready');
  const releasePath = path.join(barrierDir, 'release');
  const preloadPath = path.join(root, 'pause-owner-write.cjs');
  fs.mkdirSync(barrierDir, { recursive: true });
  fs.writeFileSync(
    preloadPath,
    [
      "const fs = require('node:fs');",
      `const readyPath = ${JSON.stringify(readyPath)};`,
      `const releasePath = ${JSON.stringify(releasePath)};`,
      'const originalWriteFileSync = fs.writeFileSync;',
      'let paused = false;',
      'fs.writeFileSync = function pauseCompleteOwnerWrite(target, data, ...args) {',
      "  const text = String(data);",
      "  if (!paused && typeof target === 'number' && (/^\\d+\\n$/.test(text) || text.includes('taiji.docx.registry-lock.v1'))) {",
      '    paused = true;',
      "    originalWriteFileSync(readyPath, 'ready', 'utf8');",
      '    const deadline = Date.now() + 5000;',
      '    while (!fs.existsSync(releasePath)) {',
      "      if (Date.now() >= deadline) throw new Error('owner publish barrier timed out');",
      '      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);',
      '    }',
      '  }',
      '  return originalWriteFileSync(target, data, ...args);',
      '};',
    ].join('\n'),
    'utf8'
  );
  t.after(() => {
    if (fs.existsSync(barrierDir)) {
      fs.writeFileSync(releasePath, 'release', 'utf8');
    }
  });

  const resultPromise = runListProcess({
    ...packagedEnv(seedRoot, runtimeRoot),
    NODE_OPTIONS: `--require ${preloadPath}`,
  });
  await waitForFileCount(barrierDir, 'ready', 1);
  const publishedBeforeOwnerWrite = fs.existsSync(lockPath);
  fs.writeFileSync(releasePath, 'release', 'utf8');
  const result = await resultPromise;

  assert.equal(publishedBeforeOwnerWrite, false);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(lockPath), false);
});

test('a delayed stale-lock reaper cannot quarantine a newer lock generation', (t) => {
  const root = makeTempRoot(t);
  const runtimeRoot = path.join(root, 'runtime-home');
  const lockPath = path.join(runtimeRoot, 'template-registry.json.lock');
  fs.mkdirSync(runtimeRoot, { recursive: true });
  const staleOwner = writeRegistryLockDirectory(lockPath, {
    pid: 99999999,
    token: 'a'.repeat(32),
  });
  const tombstonePath = `${lockPath}.stale-${staleOwner.token}`;
  fs.renameSync(lockPath, tombstonePath);
  const newerOwner = writeRegistryLockDirectory(lockPath, {
    pid: process.pid,
    token: 'b'.repeat(32),
  });

  const quarantined = quarantineStaleRegistryLock(lockPath, staleOwner);

  assert.equal(quarantined, false);
  assert.deepEqual(readRegistryLockOwner(lockPath), newerOwner);
  assert.deepEqual(readRegistryLockOwner(tombstonePath), staleOwner);
});

test('an old owner cannot release a newer lock generation', (t) => {
  const root = makeTempRoot(t);
  const runtimeRoot = path.join(root, 'runtime-home');
  const lockPath = path.join(runtimeRoot, 'template-registry.json.lock');
  fs.mkdirSync(runtimeRoot, { recursive: true });
  const oldOwner = {
    schema: 'taiji.docx.registry-lock.v1',
    pid: process.pid,
    token: 'c'.repeat(32),
  };
  const newerOwner = writeRegistryLockDirectory(lockPath, {
    pid: process.pid,
    token: 'd'.repeat(32),
  });

  assert.throws(
    () => releaseRegistryLock(lockPath, oldOwner),
    /ownership changed before release/
  );
  assert.deepEqual(readRegistryLockOwner(lockPath), newerOwner);
});

test('concurrent replacements use a persistent registry revision CAS so at most one succeeds', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const env = packagedEnv(seedRoot, runtimeRoot);
  const initialPackage = makeIncomingTemplate(root, 'race-proposal');
  await installTemplatePackage({
    builtinRootDir: seedRoot,
    runtimeRootDir: runtimeRoot,
    packageDir: initialPackage,
  });

  const replacementOne = makeIncomingTemplate(root, 'race-proposal-one');
  const replacementTwo = makeIncomingTemplate(root, 'race-proposal-two');
  for (const [packageDir, name] of [
    [replacementOne, 'Replacement One'],
    [replacementTwo, 'Replacement Two'],
  ]) {
    const manifestPath = path.join(packageDir, 'manifest.json');
    writeJson(manifestPath, { ...readJson(manifestPath), id: 'race-proposal', name });
  }
  const barrierDir = path.join(root, 'replace-barrier');
  addInstallBarrier({ packageDir: replacementOne, barrierDir, marker: 'one' });
  addInstallBarrier({ packageDir: replacementTwo, barrierDir, marker: 'two' });

  const results = await Promise.all([
    runInstallProcess(env, replacementOne, { replace: true }),
    runInstallProcess(env, replacementTwo, { replace: true }),
  ]);

  assert.deepEqual(results.map((result) => result.status).sort(), [0, 3], JSON.stringify(results));
  const runtimeRegistry = readJson(path.join(runtimeRoot, 'template-registry.json'));
  assert.equal(runtimeRegistry.installed.length, 1);
  assert.equal(runtimeRegistry.installed[0].templateId, 'race-proposal');
  assert.match(runtimeRegistry.installed[0].contentDigest, /^sha256:[a-f0-9]{64}$/);
  assert.match(runtimeRegistry.installed[0].revisionDigest, /^sha256:[a-f0-9]{64}$/);
  assert.deepEqual(
    fs.readdirSync(path.join(runtimeRoot, 'installed')).filter((name) => name.startsWith('.')),
    []
  );
  assert.ok(
    ['Replacement One', 'Replacement Two'].includes(
      readJson(path.join(runtimeRoot, 'installed', 'race-proposal', 'manifest.json')).name
    )
  );
});

test('first-use initialization recovers a stale registry lock from a dead process', (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  fs.mkdirSync(runtimeRoot, { recursive: true });
  const lockPath = path.join(runtimeRoot, 'template-registry.json.lock');
  const staleOwner = writeRegistryLockDirectory(lockPath, {
    pid: 99999999,
    token: 'e'.repeat(32),
  });

  const templates = listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot });

  assert.deepEqual(templates.map((template) => template.id), ['general-proposal']);
  assert.equal(fs.existsSync(lockPath), false);
  assert.deepEqual(
    readRegistryLockOwner(`${lockPath}.stale-${staleOwner.token}`),
    staleOwner
  );
  assert.equal(fs.existsSync(path.join(runtimeRoot, 'template-registry.json')), true);
});

test('concurrent first-use quarantines one stale registry lock without overlapping owners', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const lockPath = path.join(runtimeRoot, 'template-registry.json.lock');
  fs.mkdirSync(runtimeRoot, { recursive: true });
  const staleOwner = writeRegistryLockDirectory(lockPath, {
    pid: 99999999,
    token: '1'.repeat(32),
  });
  const env = packagedEnv(seedRoot, runtimeRoot);

  const results = await Promise.all(Array.from({ length: 8 }, () => runListProcess(env)));

  for (const result of results) {
    assert.equal(result.status, 0, `stdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  }
  assert.equal(fs.existsSync(lockPath), false);
  assert.deepEqual(
    readRegistryLockOwner(`${lockPath}.stale-${staleOwner.token}`),
    staleOwner
  );
  assert.deepEqual(
    readJson(path.join(runtimeRoot, 'template-registry.json')).builtin.map((entry) => entry.templateId),
    ['general-proposal']
  );
  assert.deepEqual(
    fs.readdirSync(runtimeRoot).filter((name) => name.includes('.candidate-') || name.includes('.release-')),
    []
  );
});

test('packaged store derives writable state from TAIJI_RUNTIME_HOME or XDG data home', (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const taijiRuntimeHome = path.join(root, 'taiji-runtime-home');
  const xdgDataHome = path.join(root, 'xdg-data');

  listTemplates({
    rootDir: seedRoot,
    env: {
      TAIJI_DOCX_BUILTIN_ROOT: seedRoot,
      TAIJI_RUNTIME_HOME: taijiRuntimeHome,
    },
  });
  assert.equal(
    fs.existsSync(path.join(taijiRuntimeHome, 'docx-engine-v2', 'template-registry.json')),
    true
  );

  listTemplates({
    rootDir: seedRoot,
    env: {
      TAIJI_DOCX_BUILTIN_ROOT: seedRoot,
      XDG_DATA_HOME: xdgDataHome,
    },
  });
  assert.equal(
    fs.existsSync(path.join(xdgDataHome, 'taiji-agent', 'docx-engine-v2', 'template-registry.json')),
    true
  );
});

test('split template store rejects symlink registries and builtin path traversal', (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const registryPath = path.join(seedRoot, 'template-registry.json');
  const realRegistryPath = path.join(root, 'seed-registry.json');
  fs.renameSync(registryPath, realRegistryPath);
  fs.symlinkSync(realRegistryPath, registryPath);

  assert.throws(
    () => listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot }),
    /symbolic link|symlink/i
  );

  fs.unlinkSync(registryPath);
  writeJson(registryPath, {
    version: 1,
    builtin: [{ templateId: 'general-proposal', path: '../outside/general-proposal' }],
    installed: [],
  });
  assert.throws(
    () => listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot }),
    /outside|traversal|relative path/i
  );
});

test('explicit runtime root rejects a symlink in any existing ancestor before writing', (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const outsideRoot = path.join(root, 'outside-runtime');
  const redirectRoot = path.join(root, 'runtime-redirect');
  fs.mkdirSync(outsideRoot, { recursive: true });
  fs.symlinkSync(outsideRoot, redirectRoot);

  assert.throws(
    () => listTemplates({
      builtinRootDir: seedRoot,
      runtimeRootDir: path.join(redirectRoot, 'nested-runtime'),
    }),
    /symbolic link|symlink/i
  );
  assert.deepEqual(fs.readdirSync(outsideRoot), []);
});

test('split template store rejects symlinks and non-regular files in template packages', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const outsidePath = path.join(root, 'outside.txt');
  fs.writeFileSync(outsidePath, 'outside', 'utf8');
  fs.symlinkSync(outsidePath, path.join(seedRoot, 'templates', 'general-proposal', 'unsafe-link'));

  assert.throws(
    () => listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot }),
    /symbolic link|symlink/i
  );

  fs.unlinkSync(path.join(seedRoot, 'templates', 'general-proposal', 'unsafe-link'));
  const fifoPath = path.join(seedRoot, 'templates', 'general-proposal', 'unsafe-fifo');
  const mkfifo = spawnSync('mkfifo', [fifoPath], { encoding: 'utf8' });
  if (mkfifo.status === 0) {
    assert.throws(
      () => listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot }),
      /regular file|unsupported file type/i
    );
    fs.unlinkSync(fifoPath);
  }

  const packageDir = makeIncomingTemplate(root, 'unsafe-package');
  fs.symlinkSync(outsidePath, path.join(packageDir, 'unsafe-link'));
  await assert.rejects(
    () => installTemplatePackage({
      rootDir: seedRoot,
      builtinRootDir: seedRoot,
      runtimeRootDir: runtimeRoot,
      packageDir,
    }),
    /symbolic link|symlink/i
  );
});

test('template installation rejects a symlinked intermediate install root without writing outside runtime', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const outsideRoot = path.join(root, 'outside');
  const packageDir = makeIncomingTemplate(root, 'escape-proposal');
  fs.mkdirSync(path.join(runtimeRoot, 'installed'), { recursive: true });
  fs.mkdirSync(outsideRoot, { recursive: true });
  fs.symlinkSync(outsideRoot, path.join(runtimeRoot, 'installed', 'redirect'));

  await assert.rejects(
    () => installTemplatePackage({
      builtinRootDir: seedRoot,
      runtimeRootDir: runtimeRoot,
      packageDir,
      installRoot: 'installed/redirect',
    }),
    /symbolic link|symlink/i
  );

  assert.deepEqual(fs.readdirSync(outsideRoot), []);
  assert.deepEqual(readJson(path.join(runtimeRoot, 'template-registry.json')).installed, []);
});

test('template installation identifies a dangling intermediate install-root symlink before copying', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const packageDir = makeIncomingTemplate(root, 'dangling-proposal');
  fs.mkdirSync(path.join(runtimeRoot, 'installed'), { recursive: true });
  fs.symlinkSync(
    path.join(root, 'missing-outside'),
    path.join(runtimeRoot, 'installed', 'redirect')
  );

  await assert.rejects(
    () => installTemplatePackage({
      builtinRootDir: seedRoot,
      runtimeRootDir: runtimeRoot,
      packageDir,
      installRoot: 'installed/redirect',
    }),
    /symbolic link|symlink/i
  );

  assert.deepEqual(readJson(path.join(runtimeRoot, 'template-registry.json')).installed, []);
});

test('template installation cleans up when validation swaps the install root to a symlink', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const outsideRoot = path.join(root, 'outside');
  const redirectRoot = path.join(runtimeRoot, 'installed', 'redirect');
  const packageDir = makeIncomingTemplate(root, 'swap-proposal');
  fs.mkdirSync(redirectRoot, { recursive: true });
  fs.mkdirSync(outsideRoot, { recursive: true });

  const adapterPath = path.join(packageDir, 'data-adapter.js');
  const adapterSource = fs.readFileSync(adapterPath, 'utf8');
  fs.writeFileSync(
    adapterPath,
    [
      "const __runtimeLayoutFs = require('node:fs');",
      `const __runtimeLayoutRedirect = ${JSON.stringify(redirectRoot)};`,
      `const __runtimeLayoutOutside = ${JSON.stringify(outsideRoot)};`,
      '__runtimeLayoutFs.rmSync(__runtimeLayoutRedirect, { recursive: true, force: true });',
      '__runtimeLayoutFs.symlinkSync(__runtimeLayoutOutside, __runtimeLayoutRedirect);',
      '',
      adapterSource,
    ].join('\n'),
    'utf8'
  );

  await assert.rejects(
    () => installTemplatePackage({
      builtinRootDir: seedRoot,
      runtimeRootDir: runtimeRoot,
      packageDir,
      installRoot: 'installed/redirect',
    }),
    /symbolic link|symlink|snapshot not found/i
  );

  assert.deepEqual(fs.readdirSync(outsideRoot), []);
  assert.deepEqual(readJson(path.join(runtimeRoot, 'template-registry.json')).installed, []);
});

test('template installation rejects source symlinks created by the adapter after its final source scan', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const outsidePath = path.join(root, 'outside.txt');
  const packageDir = makeIncomingTemplate(root, 'late-source-link');
  fs.writeFileSync(outsidePath, 'outside', 'utf8');
  addDelayedSymlinkMutation({
    packageDir,
    counterPath: path.join(root, 'adapter-load-count.txt'),
    linkPath: path.join(packageDir, 'late-link'),
    outsidePath,
  });

  await assert.rejects(
    () => installTemplatePackage({
      builtinRootDir: seedRoot,
      runtimeRootDir: runtimeRoot,
      packageDir,
    }),
    /symbolic link|symlink/i
  );

  assert.equal(fs.existsSync(path.join(runtimeRoot, 'installed', 'late-source-link')), false);
  assert.deepEqual(readJson(path.join(runtimeRoot, 'template-registry.json')).installed, []);
  assert.deepEqual(
    fs.readdirSync(path.join(runtimeRoot, 'installed')).filter((name) => name.startsWith('.')),
    []
  );
});

test('template installation rejects symlinks created inside the prepared snapshot during rendering', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const outsidePath = path.join(root, 'outside.txt');
  const packageDir = makeIncomingTemplate(root, 'late-snapshot-link');
  fs.writeFileSync(outsidePath, 'outside', 'utf8');
  addDelayedSymlinkMutation({
    packageDir,
    counterPath: path.join(root, 'snapshot-adapter-load-count.txt'),
    linkPath: '',
    outsidePath,
  });

  await assert.rejects(
    () => installTemplatePackage({
      builtinRootDir: seedRoot,
      runtimeRootDir: runtimeRoot,
      packageDir,
    }),
    /symbolic link|symlink/i
  );

  assert.equal(fs.existsSync(path.join(packageDir, 'late-link')), false);
  assert.equal(fs.existsSync(path.join(runtimeRoot, 'installed', 'late-snapshot-link')), false);
  assert.deepEqual(readJson(path.join(runtimeRoot, 'template-registry.json')).installed, []);
  assert.deepEqual(
    fs.readdirSync(path.join(runtimeRoot, 'installed')).filter((name) => name.startsWith('.')),
    []
  );
});

test('template installation rescans the prepared snapshot inside the registry lock before commit', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const outsidePath = path.join(root, 'outside.txt');
  const markerPath = path.join(root, 'registry-barrier');
  const packageDir = makeIncomingTemplate(root, 'locked-snapshot-link');
  fs.writeFileSync(outsidePath, 'outside', 'utf8');
  const lockPath = path.join(runtimeRoot, 'template-registry.json.lock');
  const installedRoot = path.join(runtimeRoot, 'installed');
  addRegistryLockBeforeCommit({ packageDir, markerPath, lockPath });

  await runWithPreparedSnapshotMutationAtRegistryLock({
    adapterMarkerPath: markerPath,
    lockPath,
    installedRoot,
    templateId: 'locked-snapshot-link',
    outsidePath,
    operation: () => assert.rejects(
      () => installTemplatePackage({
        builtinRootDir: seedRoot,
        runtimeRootDir: runtimeRoot,
        packageDir,
      }),
      /symbolic link|symlink/i
    ),
  });
  assert.equal(fs.existsSync(path.join(runtimeRoot, 'installed', 'locked-snapshot-link')), false);
  assert.deepEqual(readJson(path.join(runtimeRoot, 'template-registry.json')).installed, []);
  assert.deepEqual(
    fs.readdirSync(path.join(runtimeRoot, 'installed')).filter((name) => name.startsWith('.')),
    []
  );
});

test('template replacement preserves target and registry digests when the prepared snapshot changes at the registry lock', async (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  const templateId = 'locked-snapshot-replace';
  const initialPackageDir = makeIncomingTemplate(root, templateId);
  await installTemplatePackage({
    builtinRootDir: seedRoot,
    runtimeRootDir: runtimeRoot,
    packageDir: initialPackageDir,
  });

  const installedRoot = path.join(runtimeRoot, 'installed');
  const targetDir = path.join(installedRoot, templateId);
  const registryPath = path.join(runtimeRoot, 'template-registry.json');
  const targetBefore = snapshotTree(targetDir);
  const registryBefore = readJson(registryPath);
  const entryBefore = registryBefore.installed.find((entry) => entry.templateId === templateId);
  assert.match(entryBefore.contentDigest, /^sha256:[a-f0-9]{64}$/);
  assert.match(entryBefore.revisionDigest, /^sha256:[a-f0-9]{64}$/);

  const replacementPackageDir = makeIncomingTemplate(root, 'locked-snapshot-replacement-source');
  const replacementManifestPath = path.join(replacementPackageDir, 'manifest.json');
  writeJson(replacementManifestPath, {
    ...readJson(replacementManifestPath),
    id: templateId,
    name: 'Replacement that must not commit',
  });
  const outsidePath = path.join(root, 'outside.txt');
  const markerPath = path.join(root, 'replace-registry-barrier');
  const lockPath = `${registryPath}.lock`;
  fs.writeFileSync(outsidePath, 'outside', 'utf8');
  addRegistryLockBeforeCommit({
    packageDir: replacementPackageDir,
    markerPath,
    lockPath,
  });

  await runWithPreparedSnapshotMutationAtRegistryLock({
    adapterMarkerPath: markerPath,
    lockPath,
    installedRoot,
    templateId,
    outsidePath,
    operation: () => assert.rejects(
      () => installTemplatePackage({
        builtinRootDir: seedRoot,
        runtimeRootDir: runtimeRoot,
        packageDir: replacementPackageDir,
        replace: true,
      }),
      /symbolic link|symlink/i
    ),
  });

  assert.deepEqual(snapshotTree(targetDir), targetBefore);
  const registryAfter = readJson(registryPath);
  const entryAfter = registryAfter.installed.find((entry) => entry.templateId === templateId);
  assert.equal(entryAfter.contentDigest, entryBefore.contentDigest);
  assert.equal(entryAfter.revisionDigest, entryBefore.revisionDigest);
  assert.deepEqual(registryAfter, registryBefore);
  assert.deepEqual(
    fs.readdirSync(installedRoot).filter((name) => name.startsWith('.')),
    []
  );
});

test('split template store rejects a symlinked writable registry after initialization', (t) => {
  const root = makeTempRoot(t);
  const seedRoot = makeSeed(root, ['general-proposal']);
  const runtimeRoot = path.join(root, 'runtime-home');
  listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot });

  const runtimeRegistryPath = path.join(runtimeRoot, 'template-registry.json');
  const outsideRegistryPath = path.join(root, 'outside-runtime-registry.json');
  fs.renameSync(runtimeRegistryPath, outsideRegistryPath);
  fs.symlinkSync(outsideRegistryPath, runtimeRegistryPath);

  assert.throws(
    () => listTemplates({ builtinRootDir: seedRoot, runtimeRootDir: runtimeRoot }),
    /symbolic link|symlink/i
  );
});
