#!/usr/bin/env node

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  portablePackageSpecs,
  validatePackageDirectory,
} = require('./materialize-portable-resvg-dependencies');

const rootDir = path.resolve(__dirname, '..');

function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const outDir = path.resolve(args.outDir);
  assertSafeOutDir(outDir);
  validateBuildInputs();

  fs.mkdirSync(path.dirname(outDir), { recursive: true });
  const stagingDir = fs.mkdtempSync(
    path.join(path.dirname(outDir), `.${path.basename(outDir)}.tmp-`)
  );
  let promoted = false;
  try {
    buildSkillInto(stagingDir);
    promoteOutput(stagingDir, outDir);
    promoted = true;
  } finally {
    if (!promoted) {
      fs.rmSync(stagingDir, { recursive: true, force: true });
    }
  }

  process.stdout.write(`build-copyable-skill-ok\t${outDir}\n`);
}

function buildSkillInto(outDir) {
  fs.mkdirSync(outDir, { recursive: true });

  copyRecursive(path.join(rootDir, 'compat', 'docx-template-skill', 'SKILL.md'), path.join(outDir, 'SKILL.md'));
  copyRecursive(path.join(rootDir, 'compat', 'docx-template-skill', 'skill.json'), path.join(outDir, 'skill.json'));
  copyRecursive(path.join(rootDir, 'compat', 'docx-template-skill', 'README.md'), path.join(outDir, 'README.md'));
  copyRecursive(
    path.join(rootDir, 'compat', 'docx-template-skill', 'skill-invocation-contract.md'),
    path.join(outDir, 'skill-invocation-contract.md')
  );
  copyRecursive(path.join(rootDir, 'compat', 'docx-template-skill', 'scripts'), path.join(outDir, 'scripts'));
  copyRecursive(path.join(rootDir, 'src'), path.join(outDir, 'engine', 'src'));
  copyRecursive(path.join(rootDir, 'templates'), path.join(outDir, 'engine', 'templates'));
  copyRecursive(path.join(rootDir, 'template-registry.json'), path.join(outDir, 'engine', 'template-registry.json'));
  copyRecursive(path.join(rootDir, 'package.json'), path.join(outDir, 'engine', 'package.json'));
  copyRecursive(path.join(rootDir, 'package-lock.json'), path.join(outDir, 'engine', 'package-lock.json'));
  copyRecursive(path.join(rootDir, 'README.md'), path.join(outDir, 'engine', 'README.md'));

  const nodeModules = path.join(rootDir, 'node_modules');
  if (!fs.existsSync(nodeModules)) {
    throw new Error('缺少 node_modules，拒绝构建没有运行依赖的 skill 包。请先执行 npm ci 和 portable resvg 物化。');
  }
  assertPortableRasterizerDependencies(nodeModules);
  copyRecursive(nodeModules, path.join(outDir, 'engine', 'node_modules'));

  chmodScripts(path.join(outDir, 'scripts'));
  chmodScripts(path.join(outDir, 'engine', 'src', 'cli'));
}

function validateBuildInputs() {
  for (const relative of [
    'compat/docx-template-skill/SKILL.md',
    'compat/docx-template-skill/skill.json',
    'compat/docx-template-skill/README.md',
    'compat/docx-template-skill/skill-invocation-contract.md',
    'compat/docx-template-skill/scripts',
    'src',
    'templates',
    'template-registry.json',
    'package.json',
    'package-lock.json',
    'README.md',
    'node_modules',
  ]) {
    if (!fs.existsSync(path.join(rootDir, relative))) {
      throw new Error(`缺少构建输入: ${path.join(rootDir, relative)}`);
    }
  }
  assertPortableRasterizerDependencies(path.join(rootDir, 'node_modules'));
}

function promoteOutput(
  stagingDir,
  outDir,
  {
    remove = (candidate) => fs.rmSync(candidate, { recursive: true, force: true }),
    warn = (message) => process.stderr.write(`build-copyable-skill-warning\t${message}\n`),
  } = {}
) {
  let backupDir = '';
  if (fs.existsSync(outDir)) {
    backupDir = fs.mkdtempSync(
      path.join(path.dirname(outDir), `.${path.basename(outDir)}.backup-`)
    );
    remove(backupDir);
    fs.renameSync(outDir, backupDir);
  }
  try {
    fs.renameSync(stagingDir, outDir);
  } catch (error) {
    if (backupDir && !fs.existsSync(outDir) && fs.existsSync(backupDir)) {
      fs.renameSync(backupDir, outDir);
    }
    throw error;
  }
  if (backupDir) {
    try {
      remove(backupDir);
    } catch (error) {
      warn(`新产物已生效，但旧备份清理失败并保留在 ${backupDir}: ${error.message}`);
    }
  }
}

function parseArgs(argv) {
  const parsed = { outDir: '' };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg !== '--out-dir') {
      throw new Error(`Unknown argument: ${arg}`);
    }
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }
    parsed.outDir = next;
    index += 1;
  }
  if (!parsed.outDir) {
    throw new Error('缺少必填参数: --out-dir');
  }
  return parsed;
}

function assertSafeOutDir(outDir) {
  const unsafe = new Set([
    path.parse(outDir).root,
    os.homedir(),
    rootDir,
    path.dirname(rootDir),
  ]);
  if (unsafe.has(outDir)) {
    throw new Error(`拒绝写入高风险目录: ${outDir}`);
  }
  if (fs.existsSync(path.join(outDir, '.git'))) {
    throw new Error(`拒绝覆盖 Git 工作区: ${outDir}`);
  }
}

function copyRecursive(sourcePath, targetPath) {
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`缺少构建输入: ${sourcePath}`);
  }
  fs.cpSync(sourcePath, targetPath, {
    recursive: true,
    force: true,
    filter: (candidate) => !candidate.endsWith(`${path.sep}.DS_Store`),
  });
}

function assertPortableRasterizerDependencies(nodeModules) {
  const metaPackagePath = path.join(nodeModules, '@resvg', 'resvg-js', 'package.json');
  if (!fs.existsSync(metaPackagePath)) {
    throw new Error('缺少 @resvg/resvg-js，拒绝构建不可迁移的 skill 包');
  }
  const metaPackage = JSON.parse(fs.readFileSync(metaPackagePath, 'utf8'));
  if (metaPackage.name !== '@resvg/resvg-js' || metaPackage.version !== '2.6.2') {
    throw new Error('要求准确的 @resvg/resvg-js@2.6.2');
  }
  for (const spec of portablePackageSpecs(rootDir)) {
    validatePackageDirectory(
      path.join(nodeModules, ...spec.name.split('/')),
      spec.name,
      spec.version
    );
  }
}

function chmodScripts(dir) {
  if (!fs.existsSync(dir)) {
    return;
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const entryPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      chmodScripts(entryPath);
    } else if (entry.name.endsWith('.js')) {
      fs.chmodSync(entryPath, 0o755);
    }
  }
}

module.exports = { main, promoteOutput };

if (require.main === module) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`build-copyable-skill-failed\t${error.message}\n`);
    process.exitCode = 1;
  }
}
