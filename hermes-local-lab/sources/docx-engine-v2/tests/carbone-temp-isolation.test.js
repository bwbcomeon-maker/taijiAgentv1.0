const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const test = require('node:test');

const engineRoot = process.env.DOCX_ENGINE_ROOT || path.resolve(__dirname, '..');
const renderModule = path.join(engineRoot, 'src', 'rendering', 'render-docx.js');

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

test('parallel render-docx loaders isolate Carbone temporary directories', async (t) => {
  const loaderCount = 8;
  const sharedTmp = fs.mkdtempSync(path.join(os.tmpdir(), 'carbone-shared-tmp-'));
  t.after(() => fs.rmSync(sharedTmp, { recursive: true, force: true }));
  const goFile = path.join(sharedTmp, 'go');
  const childCode = `
    const fs = require('node:fs');
    const readyFile = process.argv[1];
    const goFile = process.argv[2];
    const renderModule = process.argv[3];
    const checkedFile = process.argv[4];
    const sharedTmp = process.argv[5];
    const expectedLoaders = Number(process.argv[6]);
    const originalExistsSync = fs.existsSync;
    let interceptedCarboneCheck = false;
    fs.existsSync = (candidate) => {
      if (!interceptedCarboneCheck && String(candidate).endsWith('/carbone_render')) {
        interceptedCarboneCheck = true;
        fs.writeFileSync(checkedFile, 'checked');
        const barrierDeadline = Date.now() + 10_000;
        while (fs.readdirSync(sharedTmp).filter((name) => name.startsWith('checked-')).length < expectedLoaders) {
          if (Date.now() >= barrierDeadline) {
            throw new Error('parallel Carbone loader barrier timed out');
          }
          Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
        }
        return false;
      }
      return originalExistsSync(candidate);
    };
    fs.writeFileSync(readyFile, 'ready');
    while (!fs.existsSync(goFile)) {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
    }
    try {
      require(renderModule);
      if (!interceptedCarboneCheck) {
        throw new Error('Carbone temporary-directory check was not intercepted');
      }
      process.exit(0);
    } catch (error) {
      process.stderr.write(String(error && error.stack ? error.stack : error));
      process.exit(1);
    }
  `;

  const childProcesses = [];
  t.after(() => {
    for (const child of childProcesses) {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill('SIGKILL');
      }
    }
  });
  const loaders = Array.from({ length: loaderCount }, (_, index) => {
    const readyFile = path.join(sharedTmp, `ready-${index}`);
    const checkedFile = path.join(sharedTmp, `checked-${index}`);
    const child = spawn(process.execPath, [
      '-e',
      childCode,
      readyFile,
      goFile,
      renderModule,
      checkedFile,
      sharedTmp,
      String(loaderCount),
    ], {
      env: { ...process.env, TMPDIR: sharedTmp },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    childProcesses.push(child);
    let stdout = '';
    let stderr = '';
    let spawnError = null;
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('error', (error) => {
      spawnError = error;
    });
    const completion = new Promise((resolve) => {
      const timeout = setTimeout(() => child.kill('SIGKILL'), 15_000);
      child.on('close', (status, signal) => {
        clearTimeout(timeout);
        resolve({ status, signal, stdout, stderr, spawnError });
      });
    });
    return { readyFile, completion };
  });

  const readyDeadline = Date.now() + 10_000;
  while (loaders.some(({ readyFile }) => !fs.existsSync(readyFile))) {
    assert.ok(Date.now() < readyDeadline, 'parallel loader readiness timed out');
    await delay(10);
  }
  fs.writeFileSync(goFile, 'go');
  const results = await Promise.all(loaders.map(({ completion }) => completion));
  assert.equal(
    fs.readdirSync(sharedTmp).filter((name) => name.startsWith('checked-')).length,
    loaderCount
  );

  for (const result of results) {
    assert.equal(
      result.status,
      0,
      result.spawnError?.stack || result.stderr || result.stdout || result.signal
    );
    assert.doesNotMatch(result.stderr, /EEXIST|carbone_render/);
  }
});
