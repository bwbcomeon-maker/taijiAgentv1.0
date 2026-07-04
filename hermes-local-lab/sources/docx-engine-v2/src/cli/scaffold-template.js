#!/usr/bin/env node

const path = require('node:path');

const { scaffoldTemplatePackage } = require('../templates/scaffold-template-package');

const EXIT_CODES = {
  success: 0,
  validationFailed: 3,
};

main();

function main() {
  const args = parseArgs(process.argv.slice(2));

  try {
    const engineRoot = path.resolve(__dirname, '../..');
    const result = scaffoldTemplatePackage({
      rootDir: engineRoot,
      fromTemplateId: args.fromTemplateId,
      templateId: args.templateId,
      name: args.name,
      description: args.description,
      outDir: path.resolve(args.outDir),
    });
    const payload = {
      ok: true,
      templateId: result.templateId,
      baseTemplateId: result.baseTemplateId,
      packageDir: result.packageDir,
      validation: result.validation,
    };
    if (args.json) {
      writeJsonStdout(payload);
    } else {
      process.stdout.write(`scaffold-template-ok\t${result.templateId}\t${result.packageDir}\n`);
    }
    process.exitCode = EXIT_CODES.success;
  } catch (error) {
    const payload = {
      ok: false,
      code: 'template_scaffold_failed',
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
    fromTemplateId: '',
    templateId: '',
    name: '',
    description: '',
    outDir: '',
    json: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--json') {
      parsed.json = true;
      continue;
    }

    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }
    if (arg === '--from') {
      parsed.fromTemplateId = next;
    } else if (arg === '--template-id') {
      parsed.templateId = next;
    } else if (arg === '--name') {
      parsed.name = next;
    } else if (arg === '--description') {
      parsed.description = next;
    } else if (arg === '--out-dir') {
      parsed.outDir = next;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }

  for (const [field, flag] of [
    ['fromTemplateId', '--from'],
    ['templateId', '--template-id'],
    ['name', '--name'],
    ['outDir', '--out-dir'],
  ]) {
    if (!parsed[field]) {
      throw new Error(`缺少必填参数: ${flag}`);
    }
  }
  return parsed;
}

function writeJsonStdout(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}
