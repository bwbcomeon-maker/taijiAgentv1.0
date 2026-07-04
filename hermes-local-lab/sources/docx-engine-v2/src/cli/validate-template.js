#!/usr/bin/env node

const path = require('node:path');

const { loadPackageFromDir } = require('../templates/install-template-package');
const { validateTemplatePackage } = require('../templates/validate-template-package');

const EXIT_CODES = {
  success: 0,
  validationFailed: 3,
};

main();

function main() {
  const args = parseArgs(process.argv.slice(2));
  const absolutePackageDir = path.resolve(args.packageDir);

  try {
    const templatePackage = loadPackageFromDir({
      packageDir: absolutePackageDir,
      registrySource: 'incoming',
    });
    const validation = validateTemplatePackage(templatePackage);
    const payload = {
      ok: validation.ok,
      templateId: templatePackage.templateId,
      packageDir: templatePackage.packageDir,
      validation,
    };

    if (validation.ok) {
      if (args.json) {
        writeJsonStdout(payload);
      } else {
        process.stdout.write(`validate-template-ok\t${templatePackage.templateId}\t${templatePackage.packageDir}\n`);
      }
      process.exitCode = EXIT_CODES.success;
      return;
    }

    const failurePayload = {
      ...payload,
      code: 'template_validation_failed',
      errors: validation.errors || [],
    };
    if (args.json) {
      writeJsonStdout(failurePayload);
    } else {
      process.stderr.write(`Template package validation failed: ${JSON.stringify(failurePayload.errors)}\n`);
    }
    process.exitCode = EXIT_CODES.validationFailed;
  } catch (error) {
    const payload = {
      ok: false,
      code: 'template_validation_failed',
      packageDir: absolutePackageDir,
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
    if (arg === '--package') {
      parsed.packageDir = next;
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
