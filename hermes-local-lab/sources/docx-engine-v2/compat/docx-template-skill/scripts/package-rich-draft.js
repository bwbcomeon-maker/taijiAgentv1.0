#!/usr/bin/env node

const { forwardResult, getFlagValue, runEngine } = require('./lib/engine-cli');

function main(argv = process.argv.slice(2)) {
  const engineArgs = [...argv];
  if (!getFlagValue(engineArgs, '--template-id')) {
    engineArgs.unshift('--template-id', 'general-proposal');
  }

  if (engineArgs.includes('--json')) {
    forwardResult(runEngine('run-job.js', engineArgs, { stdio: 'inherit' }));
    return;
  }

  const result = runEngine('run-job.js', [...engineArgs, '--json']);
  if (result.status !== 0) {
    forwardResult(result);
    return;
  }

  const payload = JSON.parse(result.stdout);
  process.stdout.write(`package-rich-draft-ok\tout=${payload.deliveryDir}\tquality=${payload.qualityStatus}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`package-rich-draft-failed\t${error.message}\n`);
  process.exitCode = 1;
}
