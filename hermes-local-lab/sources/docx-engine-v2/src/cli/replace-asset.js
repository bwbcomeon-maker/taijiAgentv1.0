#!/usr/bin/env node

const path = require('node:path');

const { replaceDocxAsset } = require('../assets/replace-docx-asset');

const EXIT_CODES = {
  success: 0,
  validationFailed: 3,
};

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = EXIT_CODES.validationFailed;
});

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const result = await replaceDocxAsset({
    docxPath: args.docx,
    figureId: args.figureId,
    imagePath: args.image,
    outputPath: args.out,
  });

  process.stdout.write(
    `${JSON.stringify({
      ok: true,
      figureId: result.figureId,
      relationshipId: result.relationshipId,
      mediaPath: result.mediaPath,
      outputPath: result.outputPath,
    })}\n`
  );
  process.exitCode = EXIT_CODES.success;
}

function parseArgs(argv) {
  const parsed = {
    docx: '',
    figureId: '',
    image: '',
    out: '',
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }

    if (arg === '--docx') {
      parsed.docx = path.resolve(next);
    } else if (arg === '--figure-id') {
      parsed.figureId = next;
    } else if (arg === '--image') {
      parsed.image = path.resolve(next);
    } else if (arg === '--out') {
      parsed.out = path.resolve(next);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }

  for (const [key, flag] of [
    ['docx', '--docx'],
    ['figureId', '--figure-id'],
    ['image', '--image'],
    ['out', '--out'],
  ]) {
    if (!parsed[key]) {
      throw new Error(`缺少必填参数: ${flag}`);
    }
  }

  return parsed;
}
