#!/usr/bin/env node

const path = require('node:path');

const { recordWpsVisualAcceptance } = require('../validation/record-wps-visual-acceptance');

const EXIT_CODES = {
  success: 0,
  validationFailed: 3,
};

main();

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const result = recordWpsVisualAcceptance({
      deliveryDir: args.deliveryDir,
      status: args.status,
      reviewedBy: args.reviewer,
      note: args.note,
    });
    const payload = {
      ok: true,
      deliveryDir: result.deliveryDir,
      qualityReportPath: result.qualityReportPath,
      qualityReport: result.qualityReport,
    };
    if (args.json) {
      process.stdout.write(`${JSON.stringify(payload)}\n`);
    } else {
      process.stdout.write(`record-wps-visual-ok\t${result.deliveryDir}\t${result.qualityReport.status}\n`);
    }
    process.exitCode = EXIT_CODES.success;
  } catch (error) {
    const payload = {
      ok: false,
      code: 'wps_visual_record_failed',
      message: error.message,
    };
    process.stdout.write(`${JSON.stringify(payload)}\n`);
    process.exitCode = EXIT_CODES.validationFailed;
  }
}

function parseArgs(argv) {
  const parsed = {
    deliveryDir: '',
    status: 'passed',
    reviewer: 'user',
    note: '',
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
    if (arg === '--delivery-dir') {
      parsed.deliveryDir = path.resolve(next);
    } else if (arg === '--status') {
      parsed.status = next;
    } else if (arg === '--reviewer') {
      parsed.reviewer = next;
    } else if (arg === '--note') {
      parsed.note = next;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }

  if (!parsed.deliveryDir) {
    throw new Error('缺少必填参数: --delivery-dir');
  }
  return parsed;
}
