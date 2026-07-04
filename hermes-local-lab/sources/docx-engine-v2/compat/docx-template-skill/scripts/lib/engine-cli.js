const { spawnSync } = require('node:child_process');
const path = require('node:path');

function packageDir() {
  return path.resolve(__dirname, '../..');
}

function engineDir() {
  return path.join(packageDir(), 'engine');
}

function engineCli(scriptName) {
  return path.join(engineDir(), 'src', 'cli', scriptName);
}

function runEngine(scriptName, args, options = {}) {
  return spawnSync(process.execPath, [engineCli(scriptName), ...args], {
    cwd: engineDir(),
    encoding: 'utf8',
    ...options,
  });
}

function forwardResult(result) {
  if (result.error) {
    process.stderr.write(`${result.error.message}\n`);
    process.exitCode = 1;
    return;
  }
  if (result.stdout) {
    process.stdout.write(result.stdout);
  }
  if (result.stderr) {
    process.stderr.write(result.stderr);
  }
  process.exitCode = result.status === null ? 1 : result.status;
}

function getFlagValue(argv, flag) {
  const index = argv.indexOf(flag);
  if (index < 0) {
    return '';
  }
  return argv[index + 1] || '';
}

function removeFlagPair(argv, flag) {
  const output = [];
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === flag) {
      index += 1;
      continue;
    }
    output.push(argv[index]);
  }
  return output;
}

module.exports = {
  engineCli,
  engineDir,
  forwardResult,
  getFlagValue,
  packageDir,
  removeFlagPair,
  runEngine,
};
