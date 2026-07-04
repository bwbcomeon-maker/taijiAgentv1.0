#!/usr/bin/env node

const path = require('node:path');

const { listTemplates } = require('../templates/registry');
const { summarizeTemplate } = require('../templates/template-summary');
const { runDocumentJob } = require('../workflow/run-document-job');

const EXIT_CODES = {
  success: 0,
  templateSelectionRequired: 2,
  validationFailed: 3,
  renderFailed: 4,
};

main().catch((error) => {
  writeJsonStdout({ ok: false, code: 'render_failed', message: error.message });
  process.exitCode = EXIT_CODES.renderFailed;
});

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const engineRoot = path.resolve(__dirname, '../..');

  if (!args.templateId) {
    writeJsonStdout({
      ok: false,
      code: 'template_selection_required',
      templates: listTemplates({ rootDir: engineRoot }).map(summarizeTemplate),
    });
    process.exitCode = EXIT_CODES.templateSelectionRequired;
    return;
  }

  const sourcePath = args.source ? path.resolve(args.source) : '';
  if (!sourcePath) {
    throw new Error('--source is required.');
  }
  if (!args.outDir) {
    throw new Error('--out-dir is required.');
  }

  const result = await runDocumentJob({
    engineRoot,
    templateId: args.templateId,
    sourcePath,
    sourceType: args.sourceType,
    assetDir: args.assetDir || '',
    deliveryDir: path.resolve(args.outDir),
  });

  if (!result.ok) {
    writeJsonStdout(toCliPayload(result));
    process.exitCode = result.code === 'render_failed' ? EXIT_CODES.renderFailed : EXIT_CODES.validationFailed;
    return;
  }

  if (args.json) {
    writeJsonStdout(toCliPayload(result));
  }
  process.exitCode = EXIT_CODES.success;
}

function parseArgs(argv) {
  const parsed = {
    templateId: '',
    source: '',
    sourceType: '',
    assetDir: '',
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
    if (arg === '--template-id') {
      parsed.templateId = next || '';
      index += 1;
    } else if (arg === '--source') {
      parsed.source = next || '';
      index += 1;
    } else if (arg === '--source-type') {
      parsed.sourceType = next || '';
      index += 1;
    } else if (arg === '--asset-dir') {
      parsed.assetDir = next || '';
      index += 1;
    } else if (arg === '--out-dir') {
      parsed.outDir = next || '';
      index += 1;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return parsed;
}

function toCliPayload(result) {
  if (!result.ok) {
    return {
      ok: false,
      code: result.code,
      message: result.message,
      stage: result.stage,
      failures: result.failures || [],
      job: result.job || undefined,
      jobManifestPath: result.jobManifestPath || undefined,
      failureReportPath: result.failureReportPath || undefined,
    };
  }

  return {
    ok: true,
    jobId: result.jobId,
    deliveryDir: result.deliveryDir,
    documentPath: result.documentPath,
    qualityStatus: result.qualityStatus,
    job: result.job,
  };
}

function writeJsonStdout(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}
