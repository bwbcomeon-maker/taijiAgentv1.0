#!/usr/bin/env node

const path = require('node:path');

const { listTemplates } = require('../templates/registry');

main();

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const engineRoot = path.resolve(__dirname, '../..');
    const templates = listTemplates({ rootDir: engineRoot }).map((template) => ({
      id: template.id,
      name: template.manifest?.name || template.id,
      description: template.manifest?.description || '',
      documentTypes: template.manifest?.documentTypes || [],
      capabilities: template.manifest?.capabilities || [],
      qualityGates: template.manifest?.qualityGates || [],
      compatibility: template.manifest?.compatibility || {},
    }));

    if (args.json) {
      process.stdout.write(`${JSON.stringify({ ok: true, templates })}\n`);
      return;
    }

    for (const template of templates) {
      process.stdout.write(`${template.id}\t${template.name}\n`);
    }
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 3;
  }
}

function parseArgs(argv) {
  const parsed = { json: false };
  for (const arg of argv) {
    if (arg === '--json') {
      parsed.json = true;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }
  return parsed;
}
