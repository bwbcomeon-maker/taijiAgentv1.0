#!/usr/bin/env node

const path = require('node:path');

const { renderTemplateSample } = require('../templates/render-template-sample');

const EXIT_CODES = {
  success: 0,
  validationFailed: 3,
};

main();

async function main() {
  const args = parseArgs(process.argv.slice(2));

  try {
    const result = await renderTemplateSample({
      packageDir: path.resolve(args.packageDir),
      outDir: path.resolve(args.outDir),
    });
    const payload = {
      ok: true,
      templateId: result.templateId,
      packageDir: result.packageDir,
      outDir: result.outDir,
      documentPath: result.documentPath,
      reportPath: result.reportPath,
    };
    if (args.json) {
      writeJsonStdout(payload);
    } else {
      process.stdout.write(`render-template-sample-ok\t${result.templateId}\t${result.documentPath}\n`);
    }
    process.exitCode = EXIT_CODES.success;
  } catch (error) {
    const payload = {
      ok: false,
      code: 'template_sample_render_failed',
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
    if (arg === '--package') {
      parsed.packageDir = next;
    } else if (arg === '--out-dir') {
      parsed.outDir = next;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }

  for (const [field, flag] of [
    ['packageDir', '--package'],
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
