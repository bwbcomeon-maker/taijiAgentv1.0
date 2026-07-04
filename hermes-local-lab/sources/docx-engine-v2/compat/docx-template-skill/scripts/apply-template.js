#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const {
  forwardResult,
  getFlagValue,
  removeFlagPair,
  runEngine,
} = require('./lib/engine-cli');

function main(argv = process.argv.slice(2)) {
  const legacyOut = getFlagValue(argv, '--out');
  if (!legacyOut) {
    forwardResult(runEngine('run-job.js', argv, { stdio: 'inherit' }));
    return;
  }

  if (getFlagValue(argv, '--out-dir')) {
    throw new Error('--out and --out-dir cannot be used together.');
  }

  const outputDocx = path.resolve(legacyOut);
  if (fs.existsSync(outputDocx)) {
    throw new Error(`输出文件已存在: ${outputDocx}`);
  }

  const deliveryDir = `${outputDocx}.delivery`;
  const engineArgs = [
    ...removeFlagPair(argv, '--out'),
    '--out-dir',
    deliveryDir,
    '--json',
  ];
  const result = runEngine('run-job.js', engineArgs);
  if (result.status !== 0) {
    forwardResult(result);
    return;
  }

  fs.mkdirSync(path.dirname(outputDocx), { recursive: true });
  fs.copyFileSync(path.join(deliveryDir, 'document.docx'), outputDocx);
  process.stdout.write(`apply-template-ok\t${getFlagValue(argv, '--template-id')}\t${outputDocx}\tdelivery=${deliveryDir}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`apply-template-failed\t${error.message}\n`);
  process.exitCode = 1;
}
