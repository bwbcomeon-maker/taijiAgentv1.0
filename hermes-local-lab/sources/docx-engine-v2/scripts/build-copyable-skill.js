#!/usr/bin/env node

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const rootDir = path.resolve(__dirname, '..');

function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const outDir = path.resolve(args.outDir);
  assertSafeOutDir(outDir);

  fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(outDir, { recursive: true });

  copyRecursive(path.join(rootDir, 'compat', 'docx-template-skill', 'SKILL.md'), path.join(outDir, 'SKILL.md'));
  copyRecursive(path.join(rootDir, 'compat', 'docx-template-skill', 'skill.json'), path.join(outDir, 'skill.json'));
  copyRecursive(path.join(rootDir, 'compat', 'docx-template-skill', 'scripts'), path.join(outDir, 'scripts'));
  copyRecursive(path.join(rootDir, 'src'), path.join(outDir, 'engine', 'src'));
  copyRecursive(path.join(rootDir, 'templates'), path.join(outDir, 'engine', 'templates'));
  copyRecursive(path.join(rootDir, 'template-registry.json'), path.join(outDir, 'engine', 'template-registry.json'));
  copyRecursive(path.join(rootDir, 'package.json'), path.join(outDir, 'engine', 'package.json'));
  copyRecursive(path.join(rootDir, 'package-lock.json'), path.join(outDir, 'engine', 'package-lock.json'));

  const nodeModules = path.join(rootDir, 'node_modules');
  if (fs.existsSync(nodeModules)) {
    copyRecursive(nodeModules, path.join(outDir, 'engine', 'node_modules'));
  }

  chmodScripts(path.join(outDir, 'scripts'));
  chmodScripts(path.join(outDir, 'engine', 'src', 'cli'));
  process.stdout.write(`build-copyable-skill-ok\t${outDir}\n`);
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

try {
  main();
} catch (error) {
  process.stderr.write(`build-copyable-skill-failed\t${error.message}\n`);
  process.exitCode = 1;
}
