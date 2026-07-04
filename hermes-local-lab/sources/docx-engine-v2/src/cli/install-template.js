#!/usr/bin/env node

const path = require('node:path');

const { installTemplatePackage } = require('../templates/install-template-package');

const EXIT_CODES = {
  success: 0,
  validationFailed: 3,
};

main();

async function main() {
  const args = parseArgs(process.argv.slice(2));

  try {
    const engineRoot = args.rootDir ? path.resolve(args.rootDir) : path.resolve(__dirname, '../..');
    const result = await installTemplatePackage({
      rootDir: engineRoot,
      packageDir: path.resolve(args.packageDir),
      replace: args.replace,
    });
    const payload = {
      ok: true,
      action: result.action,
      templateId: result.templateId,
      packageDir: result.packageDir,
      registryPath: result.registryPath,
      registryEntry: result.registryEntry,
    };
    if (args.json) {
      writeJsonStdout(payload);
    } else {
      process.stdout.write(`install-template-${result.action || 'installed'}\t${result.templateId}\t${result.registryEntry.path}\n`);
    }
    process.exitCode = EXIT_CODES.success;
  } catch (error) {
    const payload = {
      ok: false,
      code: 'template_install_failed',
      message: error.message,
    };
    if (args.json) {
      writeJsonStdout(payload);
    } else {
      process.stderr.write(`${error.message}\n`);
    }
    process.exitCode = EXIT_CODES.validationFailed;
  }
}

function parseArgs(argv) {
  const parsed = {
    packageDir: '',
    rootDir: '',
    json: false,
    replace: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--json') {
      parsed.json = true;
      continue;
    }
    if (arg === '--replace') {
      parsed.replace = true;
      continue;
    }

    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }
    if (arg === '--package') {
      parsed.packageDir = next;
    } else if (arg === '--root-dir') {
      parsed.rootDir = next;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }

  if (!parsed.packageDir) {
    throw new Error('缺少必填参数: --package');
  }
  return parsed;
}

function writeJsonStdout(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}
