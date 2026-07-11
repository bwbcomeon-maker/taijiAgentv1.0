const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const {
  materializePortableDependencies,
  portablePackageSpecs,
  validatePackageDirectory,
  verifyTarballIntegrity,
} = require('../scripts/materialize-portable-resvg-dependencies');

const portableNames = [
  '@resvg/resvg-js-linux-x64-gnu',
  '@resvg/resvg-js-linux-x64-musl',
  '@resvg/resvg-js-linux-arm64-gnu',
  '@resvg/resvg-js-linux-arm64-musl',
];

function portablePackageFixture(packageName) {
  const match = /linux-(x64|arm64)-(gnu|musl)$/.exec(packageName);
  assert.ok(match, packageName);
  const [, cpu, abi] = match;
  return {
    packageJson: {
      name: packageName,
      version: '2.6.2',
      os: ['linux'],
      cpu: [cpu],
      libc: [abi === 'gnu' ? 'glibc' : 'musl'],
      main: `resvgjs.linux-${cpu}-${abi}.node`,
    },
    nativeBinary: createElf64Header(cpu),
  };
}

function createElf64Header(cpu) {
  const header = Buffer.alloc(120);
  Buffer.from([0x7f, 0x45, 0x4c, 0x46]).copy(header);
  header[4] = 2;
  header[5] = 1;
  header[6] = 1;
  header.writeUInt16LE(3, 16);
  header.writeUInt16LE(cpu === 'x64' ? 62 : 183, 18);
  header.writeUInt32LE(1, 20);
  header.writeBigUInt64LE(64n, 32);
  header.writeUInt16LE(64, 52);
  header.writeUInt16LE(56, 54);
  header.writeUInt16LE(1, 56);
  header.writeUInt32LE(1, 64);
  return header;
}

test('portablePackageSpecs binds every Linux target to package-lock integrity', () => {
  const specs = portablePackageSpecs();

  assert.deepEqual(
    specs.map((spec) => spec.name),
    portableNames
  );
  for (const spec of specs) {
    assert.equal(spec.version, '2.6.2');
    assert.match(spec.integrity, /^sha512-[A-Za-z0-9+/=]+$/);
  }
});

test('verifyTarballIntegrity rejects bytes that do not match the lockfile digest', (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'portable-resvg-integrity-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  const tarball = path.join(tempDir, 'package.tgz');
  fs.writeFileSync(tarball, 'locked package bytes');
  const digest = crypto.createHash('sha512').update('locked package bytes').digest('base64');

  assert.doesNotThrow(() => verifyTarballIntegrity(tarball, `sha512-${digest}`));
  assert.throws(
    () => verifyTarballIntegrity(tarball, `sha512-${Buffer.alloc(64).toString('base64')}`),
    /integrity/
  );
});

test('validatePackageDirectory requires the exact package identity and a native binary', (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'portable-resvg-package-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  const fixture = portablePackageFixture('@resvg/resvg-js-linux-x64-gnu');
  fs.writeFileSync(
    path.join(tempDir, 'package.json'),
    JSON.stringify(fixture.packageJson)
  );

  assert.throws(
    () => validatePackageDirectory(tempDir, '@resvg/resvg-js-linux-x64-gnu', '2.6.2'),
    /native binary/
  );
  fs.writeFileSync(path.join(tempDir, fixture.packageJson.main), fixture.nativeBinary);
  assert.doesNotThrow(() =>
    validatePackageDirectory(tempDir, '@resvg/resvg-js-linux-x64-gnu', '2.6.2')
  );
  fs.writeFileSync(path.join(tempDir, fixture.packageJson.main), 'not an ELF binary');
  assert.throws(
    () => validatePackageDirectory(tempDir, '@resvg/resvg-js-linux-x64-gnu', '2.6.2'),
    /ELF64/
  );
  const emptyProgramTable = Buffer.from(fixture.nativeBinary.subarray(0, 64));
  emptyProgramTable.writeUInt16LE(0, 56);
  fs.writeFileSync(path.join(tempDir, fixture.packageJson.main), emptyProgramTable);
  assert.throws(
    () => validatePackageDirectory(tempDir, '@resvg/resvg-js-linux-x64-gnu', '2.6.2'),
    /program header/
  );
  const wrongProgramEntrySize = Buffer.from(fixture.nativeBinary);
  wrongProgramEntrySize.writeUInt16LE(1, 54);
  fs.writeFileSync(path.join(tempDir, fixture.packageJson.main), wrongProgramEntrySize);
  assert.throws(
    () => validatePackageDirectory(tempDir, '@resvg/resvg-js-linux-x64-gnu', '2.6.2'),
    /program header/
  );
  fs.writeFileSync(path.join(tempDir, fixture.packageJson.main), fixture.nativeBinary);
  assert.throws(
    () => validatePackageDirectory(tempDir, '@resvg/resvg-js-linux-arm64-gnu', '2.6.2'),
    /identity/
  );
});

test('materializePortableDependencies verifies, extracts, and atomically installs every locked package', (t) => {
  const packageRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'portable-resvg-materialize-'));
  t.after(() => fs.rmSync(packageRoot, { recursive: true, force: true }));
  const fixturesDir = path.join(packageRoot, 'fixtures');
  const fakeBin = path.join(packageRoot, 'fake-bin');
  fs.mkdirSync(fixturesDir, { recursive: true });
  fs.mkdirSync(fakeBin, { recursive: true });
  fs.mkdirSync(path.join(packageRoot, 'node_modules', '@resvg', 'resvg-js'), { recursive: true });
  fs.writeFileSync(
    path.join(packageRoot, 'node_modules', '@resvg', 'resvg-js', 'package.json'),
    JSON.stringify({ name: '@resvg/resvg-js', version: '2.6.2' })
  );

  const packageEntries = {};
  const tarballs = {};
  for (const packageName of portableNames) {
    const fixtureRoot = path.join(fixturesDir, packageName.replaceAll('/', '_'));
    const packageDir = path.join(fixtureRoot, 'package');
    fs.mkdirSync(packageDir, { recursive: true });
    const fixture = portablePackageFixture(packageName);
    fs.writeFileSync(path.join(packageDir, 'package.json'), JSON.stringify(fixture.packageJson));
    fs.writeFileSync(path.join(packageDir, fixture.packageJson.main), fixture.nativeBinary);
    const tarball = path.join(fixturesDir, `${packageName.replaceAll('/', '_')}.tgz`);
    const packed = spawnSync('tar', ['-czf', tarball, '-C', fixtureRoot, 'package'], {
      encoding: 'utf8',
    });
    assert.equal(packed.status, 0, packed.stderr);
    const integrity = `sha512-${crypto.createHash('sha512').update(fs.readFileSync(tarball)).digest('base64')}`;
    packageEntries[`node_modules/${packageName}`] = { version: '2.6.2', integrity };
    tarballs[packageName] = tarball;
  }
  fs.writeFileSync(
    path.join(packageRoot, 'package-lock.json'),
    JSON.stringify({ lockfileVersion: 3, packages: packageEntries })
  );

  const fakeNpm = path.join(fakeBin, 'npm');
  fs.writeFileSync(
    fakeNpm,
    `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const args = process.argv.slice(2);
const destination = args[args.indexOf('--pack-destination') + 1];
const requested = args.at(-1);
const packageName = requested.slice(0, requested.lastIndexOf('@'));
const tarball = JSON.parse(process.env.TAIJI_TEST_NPM_TARBALLS)[packageName];
if (!destination || !tarball) process.exit(2);
fs.copyFileSync(tarball, path.join(destination, path.basename(tarball)));
`
  );
  fs.chmodSync(fakeNpm, 0o755);
  const previousPath = process.env.PATH;
  const previousTarballs = process.env.TAIJI_TEST_NPM_TARBALLS;
  process.env.PATH = `${fakeBin}${path.delimiter}${previousPath}`;
  process.env.TAIJI_TEST_NPM_TARBALLS = JSON.stringify(tarballs);
  t.after(() => {
    process.env.PATH = previousPath;
    if (previousTarballs === undefined) {
      delete process.env.TAIJI_TEST_NPM_TARBALLS;
    } else {
      process.env.TAIJI_TEST_NPM_TARBALLS = previousTarballs;
    }
  });

  materializePortableDependencies(packageRoot);

  for (const packageName of portableNames) {
    const packageDir = path.join(packageRoot, 'node_modules', ...packageName.split('/'));
    validatePackageDirectory(
      packageDir,
      packageName,
      '2.6.2'
    );
    assert.equal(
      fs.statSync(packageDir).mode & 0o777,
      0o755,
      `${packageName} root must be traversable by the installed desktop user`
    );
  }
  const scopeEntries = fs.readdirSync(path.join(packageRoot, 'node_modules', '@resvg'));
  assert.equal(scopeEntries.some((name) => name.startsWith('.portable-resvg-')), false);

  const tamperedName = '@resvg/resvg-js-linux-x64-gnu';
  const tamperedFixture = portablePackageFixture(tamperedName);
  const tamperedBinary = path.join(
    packageRoot,
    'node_modules',
    ...tamperedName.split('/'),
    tamperedFixture.packageJson.main
  );
  const lockedDigest = crypto.createHash('sha256').update(fs.readFileSync(tamperedBinary)).digest('hex');
  fs.appendFileSync(tamperedBinary, 'valid-elf-but-not-locked');
  assert.doesNotThrow(() =>
    validatePackageDirectory(
      path.dirname(tamperedBinary),
      tamperedName,
      '2.6.2'
    )
  );
  assert.notEqual(
    crypto.createHash('sha256').update(fs.readFileSync(tamperedBinary)).digest('hex'),
    lockedDigest
  );
  const permissionTamperedName = '@resvg/resvg-js-linux-x64-musl';
  const permissionTamperedDir = path.join(
    packageRoot,
    'node_modules',
    ...permissionTamperedName.split('/')
  );
  fs.chmodSync(permissionTamperedDir, 0o700);

  materializePortableDependencies(packageRoot);

  assert.equal(
    crypto.createHash('sha256').update(fs.readFileSync(tamperedBinary)).digest('hex'),
    lockedDigest,
    'an existing structurally valid package must be restored from the lockfile-bound tarball'
  );
  for (const packageName of portableNames) {
    const packageDir = path.join(packageRoot, 'node_modules', ...packageName.split('/'));
    assert.equal(
      fs.statSync(packageDir).mode & 0o777,
      0o755,
      `${packageName} root permissions must be restored from the locked package`
    );
  }
  assert.equal(
    fs.readdirSync(path.join(packageRoot, 'node_modules', '@resvg'))
      .some((name) => name.includes('.backup-') || name.startsWith('.portable-resvg-')),
    false
  );
});
