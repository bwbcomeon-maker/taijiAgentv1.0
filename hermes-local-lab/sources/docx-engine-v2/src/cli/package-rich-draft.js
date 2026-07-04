#!/usr/bin/env node

const path = require('node:path');

const { packageRichDraft } = require('../assets/package-rich-draft');

main().catch((error) => {
  process.stderr.write(`package-rich-draft-failed\t${error.message}\n`);
  process.exitCode = 1;
});

async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  const result = await packageRichDraft(options);
  process.stdout.write(`package-rich-draft-ok\tfigures=${result.figures}\ttables=${result.tables}\tout=${result.outDir}\n`);
}

function parseArgs(argv) {
  const options = { source: '', outDir: '', assetDir: '' };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }
    if (arg === '--source') {
      options.source = path.resolve(next);
    } else if (arg === '--out-dir') {
      options.outDir = path.resolve(next);
    } else if (arg === '--asset-dir') {
      options.assetDir = path.resolve(next);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }
  if (!options.source) {
    throw new Error('缺少必填参数: --source');
  }
  if (!options.outDir) {
    throw new Error('缺少必填参数: --out-dir');
  }
  return options;
}
