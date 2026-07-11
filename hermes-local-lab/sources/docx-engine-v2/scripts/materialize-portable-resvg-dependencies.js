#!/usr/bin/env node

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const rootDir = path.resolve(__dirname, '..');
const requiredPackageNames = [
  '@resvg/resvg-js-linux-x64-gnu',
  '@resvg/resvg-js-linux-x64-musl',
  '@resvg/resvg-js-linux-arm64-gnu',
  '@resvg/resvg-js-linux-arm64-musl',
];

function readJson(jsonPath) {
  return JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
}

function portablePackageSpecs(packageRoot = rootDir) {
  const lock = readJson(path.join(packageRoot, 'package-lock.json'));
  return requiredPackageNames.map((name) => {
    const entry = lock.packages?.[`node_modules/${name}`];
    if (!entry || entry.version !== '2.6.2' || !entry.integrity?.startsWith('sha512-')) {
      throw new Error(`package-lock 缺少固定版本和 SHA512 integrity: ${name}`);
    }
    return { name, version: entry.version, integrity: entry.integrity };
  });
}

function verifyTarballIntegrity(tarballPath, expectedIntegrity) {
  if (!expectedIntegrity.startsWith('sha512-')) {
    throw new Error(`unsupported integrity: ${expectedIntegrity}`);
  }
  const actual = `sha512-${crypto
    .createHash('sha512')
    .update(fs.readFileSync(tarballPath))
    .digest('base64')}`;
  if (actual !== expectedIntegrity) {
    throw new Error(`tarball integrity mismatch: expected ${expectedIntegrity}, got ${actual}`);
  }
}

function listFilesRecursively(directory) {
  const files = [];
  const entries = fs.readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name));
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) {
      throw new Error(`package contains symbolic link: ${entryPath}`);
    }
    if (entry.isDirectory()) {
      files.push(...listFilesRecursively(entryPath));
    } else if (entry.isFile()) {
      if (fs.lstatSync(entryPath).nlink !== 1) {
        throw new Error(`package contains hard link: ${entryPath}`);
      }
      files.push(entryPath);
    } else {
      throw new Error(`package contains unsupported filesystem node: ${entryPath}`);
    }
  }
  return files;
}

function updateHashFromFile(digest, filePath) {
  const descriptor = fs.openSync(filePath, 'r');
  const buffer = Buffer.alloc(1024 * 1024);
  try {
    while (true) {
      const bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      digest.update(buffer.subarray(0, bytesRead));
    }
  } finally {
    fs.closeSync(descriptor);
  }
}

function canonicalPackageTreeDigest(directory) {
  const digest = crypto.createHash('sha256');
  const rootStat = fs.lstatSync(directory);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
    throw new Error(`package directory is unsafe: ${directory}`);
  }
  digest.update(`D\0.\0${rootStat.mode & 0o777}\0`);

  function walk(current, relativeParent = '') {
    const entries = fs.readdirSync(current, { withFileTypes: true })
      .sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const entryPath = path.join(current, entry.name);
      const relative = path.posix.join(relativeParent, entry.name);
      const entryStat = fs.lstatSync(entryPath);
      if (entryStat.isSymbolicLink()) {
        throw new Error(`package contains symbolic link: ${entryPath}`);
      }
      if (entry.isDirectory()) {
        digest.update(`D\0${relative}\0${entryStat.mode & 0o777}\0`);
        walk(entryPath, relative);
      } else if (entry.isFile()) {
        if (entryStat.nlink !== 1) {
          throw new Error(`package contains hard link: ${entryPath}`);
        }
        digest.update(`F\0${relative}\0${entryStat.mode & 0o777}\0${entryStat.size}\0`);
        updateHashFromFile(digest, entryPath);
        digest.update('\0');
      } else {
        throw new Error(`package contains unsupported filesystem node: ${entryPath}`);
      }
    }
  }

  walk(directory);
  return digest.digest('hex');
}

function replacePackageDirectory(stagingDir, targetDir) {
  if (!fs.existsSync(targetDir)) {
    fs.renameSync(stagingDir, targetDir);
    return;
  }
  const backupDir = path.join(
    path.dirname(targetDir),
    `.${path.basename(targetDir)}.backup-${process.pid}-${crypto.randomBytes(8).toString('hex')}`
  );
  fs.renameSync(targetDir, backupDir);
  let published = false;
  try {
    fs.renameSync(stagingDir, targetDir);
    published = true;
  } catch (error) {
    if (!fs.existsSync(targetDir) && fs.existsSync(backupDir)) {
      fs.renameSync(backupDir, targetDir);
    }
    throw error;
  } finally {
    if (published) {
      fs.rmSync(backupDir, { recursive: true, force: true });
    }
  }
}

function validatePackageDirectory(directory, expectedName, expectedVersion) {
  const directoryStat = fs.lstatSync(directory);
  if (!directoryStat.isDirectory() || directoryStat.isSymbolicLink()) {
    throw new Error(`package directory is unsafe: ${directory}`);
  }
  const packageJsonPath = path.join(directory, 'package.json');
  const packageJsonStat = fs.lstatSync(packageJsonPath);
  if (!packageJsonStat.isFile() || packageJsonStat.isSymbolicLink()) {
    throw new Error(`package.json is unsafe: ${packageJsonPath}`);
  }
  const packageJson = readJson(packageJsonPath);
  if (packageJson.name !== expectedName || packageJson.version !== expectedVersion) {
    throw new Error(
      `package identity mismatch: expected ${expectedName}@${expectedVersion}, got ${packageJson.name}@${packageJson.version}`
    );
  }
  const target = /^@resvg\/resvg-js-linux-(x64|arm64)-(gnu|musl)$/.exec(expectedName);
  if (!target) {
    throw new Error(`unsupported portable resvg package: ${expectedName}`);
  }
  const [, cpu, abi] = target;
  const expectedLibc = abi === 'gnu' ? 'glibc' : 'musl';
  const expectedMain = `resvgjs.linux-${cpu}-${abi}.node`;
  if (
    JSON.stringify(packageJson.os) !== JSON.stringify(['linux']) ||
    JSON.stringify(packageJson.cpu) !== JSON.stringify([cpu]) ||
    JSON.stringify(packageJson.libc) !== JSON.stringify([expectedLibc]) ||
    packageJson.main !== expectedMain
  ) {
    throw new Error(`package platform metadata mismatch: ${expectedName}@${expectedVersion}`);
  }
  const nativePath = path.join(directory, expectedMain);
  if (!fs.existsSync(nativePath)) {
    throw new Error(`package native binary is missing: ${expectedName}@${expectedVersion}`);
  }
  const nativeStat = fs.lstatSync(nativePath);
  if (!nativeStat.isFile() || nativeStat.isSymbolicLink()) {
    throw new Error(`package native binary is unsafe: ${expectedName}@${expectedVersion}`);
  }
  const header = Buffer.alloc(64);
  const descriptor = fs.openSync(nativePath, 'r');
  let bytesRead = 0;
  try {
    bytesRead = fs.readSync(descriptor, header, 0, header.length, 0);
  } finally {
    fs.closeSync(descriptor);
  }
  const expectedMachine = cpu === 'x64' ? 62 : 183;
  if (
    bytesRead < header.length ||
    !header.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])) ||
    header[4] !== 2 ||
    header[5] !== 1 ||
    header[6] !== 1 ||
    header.readUInt16LE(16) !== 3 ||
    header.readUInt16LE(18) !== expectedMachine ||
    header.readUInt32LE(20) !== 1 ||
    header.readUInt16LE(52) !== 64
  ) {
    throw new Error(`package native binary is not Linux ELF64 ${cpu}: ${expectedName}@${expectedVersion}`);
  }
  const fileSize = BigInt(nativeStat.size);
  const programOffset = header.readBigUInt64LE(32);
  const programEntrySize = header.readUInt16LE(54);
  const programEntryCount = header.readUInt16LE(56);
  const programEnd = programOffset + BigInt(programEntrySize) * BigInt(programEntryCount);
  if (
    programEntryCount === 0 ||
    programEntrySize !== 56 ||
    programOffset < 64n ||
    programEnd > fileSize
  ) {
    throw new Error(`package program header table is invalid: ${expectedName}@${expectedVersion}`);
  }
  const programTable = Buffer.alloc(programEntrySize * programEntryCount);
  const programDescriptor = fs.openSync(nativePath, 'r');
  let programBytes = 0;
  try {
    programBytes = fs.readSync(
      programDescriptor,
      programTable,
      0,
      programTable.length,
      Number(programOffset)
    );
  } finally {
    fs.closeSync(programDescriptor);
  }
  if (programBytes !== programTable.length) {
    throw new Error(`package program header table is truncated: ${expectedName}@${expectedVersion}`);
  }
  let hasLoadSegment = false;
  for (let index = 0; index < programEntryCount; index += 1) {
    if (programTable.readUInt32LE(index * programEntrySize) === 1) {
      hasLoadSegment = true;
      break;
    }
  }
  if (!hasLoadSegment) {
    throw new Error(`package native binary has no PT_LOAD segment: ${expectedName}@${expectedVersion}`);
  }
  const sectionOffset = header.readBigUInt64LE(40);
  const sectionEntrySize = header.readUInt16LE(58);
  const sectionEntryCount = header.readUInt16LE(60);
  if (sectionEntryCount > 0) {
    const sectionEnd = sectionOffset + BigInt(sectionEntrySize) * BigInt(sectionEntryCount);
    if (sectionEntrySize !== 64 || sectionOffset < 64n || sectionEnd > fileSize) {
      throw new Error(`package section header table is invalid: ${expectedName}@${expectedVersion}`);
    }
  }
  listFilesRecursively(directory);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || rootDir,
    encoding: 'utf8',
    env: process.env,
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error || result.status !== 0) {
    const details = [result.stdout, result.stderr, result.error?.message].filter(Boolean).join('\n');
    throw new Error(`${command} ${args.join(' ')} failed${details ? `:\n${details}` : ''}`);
  }
  return result.stdout;
}

function assertSafeTarEntries(tarballPath) {
  const entries = run('tar', ['-tzf', tarballPath])
    .split(/\r?\n/)
    .filter(Boolean);
  if (entries.length === 0) {
    throw new Error(`empty npm tarball: ${tarballPath}`);
  }
  for (const entry of entries) {
    const normalized = entry.replace(/\\/g, '/');
    const parts = normalized.split('/');
    if ((parts[0] !== 'package' && normalized !== 'package') || parts.includes('..') || path.posix.isAbsolute(normalized)) {
      throw new Error(`unsafe npm tar entry: ${entry}`);
    }
  }
}

function downloadLockedPackage(spec, packageRoot) {
  const packDir = fs.mkdtempSync(path.join(os.tmpdir(), 'taiji-resvg-pack-'));
  try {
    run(
      'npm',
      [
        'pack',
        '--silent',
        '--ignore-scripts',
        '--pack-destination',
        packDir,
        `${spec.name}@${spec.version}`,
      ],
      { cwd: packageRoot }
    );
    const tarballs = fs.readdirSync(packDir).filter((name) => name.endsWith('.tgz'));
    if (tarballs.length !== 1) {
      throw new Error(`npm pack 未生成唯一 tarball: ${spec.name}@${spec.version}`);
    }
    const tarballPath = path.join(packDir, tarballs[0]);
    verifyTarballIntegrity(tarballPath, spec.integrity);
    assertSafeTarEntries(tarballPath);
    return { packDir, tarballPath };
  } catch (error) {
    fs.rmSync(packDir, { recursive: true, force: true });
    throw error;
  }
}

function materializePortableDependencies(packageRoot = rootDir) {
  const scopeDir = path.join(packageRoot, 'node_modules', '@resvg');
  if (!fs.existsSync(path.join(packageRoot, 'node_modules', '@resvg', 'resvg-js', 'package.json'))) {
    throw new Error('npm ci 未安装 @resvg/resvg-js，拒绝生成无运行依赖的 copyable skill');
  }
  fs.mkdirSync(scopeDir, { recursive: true });

  for (const spec of portablePackageSpecs(packageRoot)) {
    const targetDir = path.join(packageRoot, 'node_modules', ...spec.name.split('/'));
    const stagingDir = fs.mkdtempSync(path.join(scopeDir, '.portable-resvg-'));
    let packDir = '';
    try {
      const downloaded = downloadLockedPackage(spec, packageRoot);
      packDir = downloaded.packDir;
      run('tar', [
        '-xzf',
        downloaded.tarballPath,
        '-C',
        stagingDir,
        '--strip-components=1',
        '--no-same-owner',
        '--no-same-permissions',
      ]);
      fs.chmodSync(stagingDir, 0o755);
      validatePackageDirectory(stagingDir, spec.name, spec.version);
      if (fs.existsSync(targetDir)) {
        validatePackageDirectory(targetDir, spec.name, spec.version);
        if (canonicalPackageTreeDigest(targetDir) === canonicalPackageTreeDigest(stagingDir)) {
          process.stdout.write(`portable-resvg-verified\t${spec.name}@${spec.version}\n`);
          continue;
        }
      }
      replacePackageDirectory(stagingDir, targetDir);
      process.stdout.write(`portable-resvg-installed\t${spec.name}@${spec.version}\n`);
    } finally {
      if (packDir) {
        fs.rmSync(packDir, { recursive: true, force: true });
      }
      fs.rmSync(stagingDir, { recursive: true, force: true });
    }
  }
  process.stdout.write('portable-resvg-dependencies-ok\n');
}

module.exports = {
  materializePortableDependencies,
  canonicalPackageTreeDigest,
  portablePackageSpecs,
  validatePackageDirectory,
  verifyTarballIntegrity,
};

if (require.main === module) {
  try {
    materializePortableDependencies();
  } catch (error) {
    process.stderr.write(`portable-resvg-dependencies-failed\t${error.message}\n`);
    process.exitCode = 1;
  }
}
