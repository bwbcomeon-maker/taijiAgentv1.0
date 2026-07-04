#!/usr/bin/env node

const path = require('node:path');
const fs = require('node:fs');

const { validateDeliveryPackage } = require('../validation/validate-delivery-package');
const { refreshDeliveryPackageFileHashes } = require('../delivery/file-hashes');

const EXIT_CODES = {
  success: 0,
  validationFailed: 3,
};

main();

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const qualityReport = validateDeliveryPackage({
      deliveryDir: args.deliveryDir,
      requireReplayReport: true,
    });
    const qualityReportPath = path.join(args.deliveryDir, 'quality-report.json');
    if (args.writeReport) {
      writeJson(qualityReportPath, qualityReport);
      refreshDeliveryPackageFileHashes({ deliveryDir: args.deliveryDir, roles: ['qualityReport'] });
    }
    const payload = {
      ok: qualityReport.status !== 'failed',
      code: qualityReport.status === 'failed' ? 'delivery_validation_failed' : undefined,
      deliveryDir: args.deliveryDir,
      qualityReportPath: args.writeReport ? qualityReportPath : undefined,
      qualityReport,
      failures: qualityReport.failures,
    };

    if (args.json) {
      process.stdout.write(`${JSON.stringify(payload)}\n`);
    } else {
      process.stdout.write(`validate-delivery-${payload.ok ? 'ok' : 'failed'}\t${args.deliveryDir}\t${qualityReport.status}\n`);
    }
    process.exitCode = payload.ok ? EXIT_CODES.success : EXIT_CODES.validationFailed;
  } catch (error) {
    const payload = {
      ok: false,
      code: 'delivery_validation_failed',
      message: error.message,
    };
    process.stdout.write(`${JSON.stringify(payload)}\n`);
    process.exitCode = EXIT_CODES.validationFailed;
  }
}

function parseArgs(argv) {
  const parsed = {
    deliveryDir: '',
    json: false,
    writeReport: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--json') {
      parsed.json = true;
      continue;
    }
    if (arg === '--write-report') {
      parsed.writeReport = true;
      continue;
    }

    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }
    if (arg === '--delivery-dir') {
      parsed.deliveryDir = path.resolve(next);
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

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}
