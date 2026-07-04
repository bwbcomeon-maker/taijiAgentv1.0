#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const { renderDeterministicMermaidSvg } = require('../assets/package-assets');

main();

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const result = renderFigureAsset(args);
    if (args.json) {
      process.stdout.write(`${JSON.stringify({ ok: true, ...result })}\n`);
      return;
    }
    process.stdout.write(`render-figure-assets-ok\t${result.figureId}\t${result.displayPath}\n`);
  } catch (error) {
    process.stderr.write(`render-figure-assets-failed\t${error.message}\n`);
    process.exitCode = 3;
  }
}

function renderFigureAsset({ manifest, figureId }) {
  const manifestPath = path.resolve(manifest);
  const deliveryDir = path.dirname(manifestPath);
  const renderPlan = readJson(manifestPath);
  const figure = (renderPlan.figures || []).find((item) => item.figureId === figureId);
  if (!figure) {
    throw new Error(`manifest 中找不到图片: ${figureId}`);
  }

  const sourcePath = path.join(deliveryDir, 'assets', figureId, 'source.mmd');
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`找不到可编辑源: ${sourcePath}`);
  }

  const displayPath = normalizeRelativePath(figure.displayPath || `assets/${figureId}/figure.svg`);
  const outputPath = path.join(deliveryDir, displayPath);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  const sourceText = fs.readFileSync(sourcePath, 'utf8');
  fs.writeFileSync(outputPath, renderDeterministicMermaidSvg({ figure, sourceText }), 'utf8');

  return { figureId, displayPath, outputPath };
}

function parseArgs(argv) {
  const parsed = { manifest: '', figureId: '', json: false };
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
    if (arg === '--manifest') {
      parsed.manifest = next;
    } else if (arg === '--figure-id') {
      parsed.figureId = next;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
    index += 1;
  }

  if (!parsed.manifest) {
    throw new Error('缺少必填参数: --manifest');
  }
  if (!parsed.figureId) {
    throw new Error('缺少必填参数: --figure-id');
  }
  if (!/^[A-Za-z0-9_-]+$/.test(parsed.figureId)) {
    throw new Error(`不安全的图片标识: ${parsed.figureId}`);
  }
  return parsed;
}

function readJson(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`找不到 manifest: ${filePath}`);
  }
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function normalizeRelativePath(value) {
  return String(value || '').replace(/\\/g, '/').replace(/^\/+/, '');
}
