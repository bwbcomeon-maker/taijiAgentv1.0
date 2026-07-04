#!/usr/bin/env node

const path = require('node:path');
const fs = require('node:fs');

const { buildDeliveryFileSha256, refreshDeliveryPackageFileHashes } = require('../delivery/file-hashes');
const { validateDomainObject } = require('../domain/validate');
const { replayDeliveryPackage } = require('../replay/replay-delivery-package');
const { validateDeliveryPackage } = require('../validation/validate-delivery-package');

const EXIT_CODES = {
  success: 0,
  replayFailed: 3,
};

main();

async function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const engineRoot = path.resolve(__dirname, '../..');
    const replayReport = await replayDeliveryPackage({
      engineRoot,
      deliveryDir: args.deliveryDir,
      outDir: args.outDir,
    });
    const writtenReport = args.writeReport
      ? writeReplayReportIntoDeliveryPackage({ deliveryDir: args.deliveryDir, replayReport })
      : null;
    const replayReportPath = writtenReport?.path || (args.outDir ? path.join(args.outDir, 'replay-report.json') : '');
    const payload = {
      ok: replayReport.status !== 'failed',
      code: replayReport.status === 'failed' ? 'delivery_replay_failed' : undefined,
      deliveryDir: args.deliveryDir,
      replayReportPath: replayReportPath && fs.existsSync(replayReportPath) ? replayReportPath : undefined,
      qualityReportPath: writtenReport?.qualityReportPath,
      replayReport,
      failures: replayReport.failures,
    };

    if (args.json) {
      process.stdout.write(`${JSON.stringify(payload)}\n`);
    } else {
      process.stdout.write(`replay-delivery-${payload.ok ? 'ok' : 'failed'}\t${args.deliveryDir}\t${replayReport.status}\n`);
    }
    process.exitCode = payload.ok ? EXIT_CODES.success : EXIT_CODES.replayFailed;
  } catch (error) {
    const payload = {
      ok: false,
      code: 'delivery_replay_failed',
      message: error.message,
    };
    process.stdout.write(`${JSON.stringify(payload)}\n`);
    process.exitCode = EXIT_CODES.replayFailed;
  }
}

function parseArgs(argv) {
  const parsed = {
    deliveryDir: '',
    outDir: '',
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
    } else if (arg === '--out-dir') {
      parsed.outDir = path.resolve(next);
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

function writeReplayReportIntoDeliveryPackage({ deliveryDir, replayReport }) {
  const deliveryPackagePath = path.join(deliveryDir, 'delivery-package.json');
  const deliveryPackage = JSON.parse(fs.readFileSync(deliveryPackagePath, 'utf8'));
  const finalReplayReport = {
    ...replayReport,
    deliveryDir: deliveryPackage.deliveryDir || deliveryDir,
  };
  const replayReportPath = path.join(deliveryDir, 'replay-report.json');
  writeJson(replayReportPath, finalReplayReport);

  deliveryPackage.files = {
    ...(deliveryPackage.files || {}),
    replayReport: 'replay-report.json',
  };
  deliveryPackage.fileSha256 = {
    ...(deliveryPackage.fileSha256 || {}),
    ...buildDeliveryFileSha256({
      deliveryDir,
      files: deliveryPackage.files,
      roles: ['replayReport'],
    }),
  };
  assertValidDeliveryPackage(deliveryPackage);
  writeJson(deliveryPackagePath, deliveryPackage);

  const qualityReport = validateDeliveryPackage({
    deliveryDir,
    requireReplayReport: true,
    enforceStoredQualityReport: false,
  });
  const qualityReportPath = path.join(deliveryDir, 'quality-report.json');
  writeJson(qualityReportPath, qualityReport);
  refreshDeliveryPackageFileHashes({ deliveryDir, roles: ['qualityReport'] });
  return { path: replayReportPath, qualityReportPath };
}

function assertValidDeliveryPackage(deliveryPackage) {
  const validation = validateDomainObject('DeliveryPackage', deliveryPackage);
  if (!validation.ok) {
    throw new Error(`DeliveryPackage validation failed: ${JSON.stringify(validation.errors)}`);
  }
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}
