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
    if (args.officeMode === 'external-office' && args.writeReport) {
      throw new Error('external-office mode forbids --write-report because the bound automatic report is read-only.');
    }
    const qualityReport = validateDeliveryPackage({
      deliveryDir: args.deliveryDir,
      requireReplayReport: true,
      enforceStoredQualityReport: !args.writeReport,
      requireWpsVisualAcceptance: args.officeMode !== 'external-office',
    });
    const qualityReportPath = path.join(args.deliveryDir, 'quality-report.json');
    if (args.writeReport) {
      writeJson(qualityReportPath, qualityReport);
      refreshDeliveryPackageFileHashes({ deliveryDir: args.deliveryDir, roles: ['qualityReport'] });
    }
    const automatedOk = (qualityReport.checks || []).every(
      (check) => check.id === 'wps_visual' || check.status === 'passed'
    );
    const ok = args.officeMode === 'external-office' ? automatedOk : qualityReport.status !== 'failed';
    const payload = {
      ok,
      code: ok ? undefined : 'delivery_validation_failed',
      deliveryDir: args.deliveryDir,
      officeStatus: args.officeMode === 'external-office' ? 'pending' : undefined,
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
    officeMode: 'legacy-embedded',
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
    } else if (arg === '--office-mode') {
      if (!['legacy-embedded', 'external-office'].includes(next)) {
        throw new Error(`Invalid office mode: ${next}`);
      }
      parsed.officeMode = next;
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
