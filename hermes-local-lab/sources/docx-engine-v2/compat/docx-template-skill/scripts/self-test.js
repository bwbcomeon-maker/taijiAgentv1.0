#!/usr/bin/env node

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { runEngine } = require('./lib/engine-cli');

function parseArgs(argv) {
  const parsed = { outDir: '' };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg !== '--out-dir') {
      throw new Error(`未知参数: ${arg}`);
    }
    if (!next || next.startsWith('--')) {
      throw new Error(`参数缺少值: ${arg}`);
    }
    parsed.outDir = path.resolve(next);
    index += 1;
  }
  if (!parsed.outDir) {
    parsed.outDir = path.join(path.resolve(__dirname, '..'), '.self-test-output');
  }
  return parsed;
}

function writeSelfTestSources(workspace) {
  const assetDir = path.join(workspace, 'assets');
  fs.mkdirSync(assetDir, { recursive: true });
  fs.writeFileSync(
    path.join(assetDir, 'architecture.svg'),
    '<svg xmlns="http://www.w3.org/2000/svg"><text>SELF_TEST_IMAGE</text></svg>\n',
    'utf8'
  );

  const richSourcePath = path.join(workspace, 'source.md');
  fs.writeFileSync(
    richSourcePath,
    [
      '# DOCX Engine V2 self test',
      '',
      '## Architecture',
      '',
      '| Component | Role |',
      '| --- | --- |',
      '| Source package | Normalizes Markdown |',
      '| Delivery package | Writes editable DOCX assets |',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[Source] --> B[Document]',
      '```',
      '',
      '![Architecture](architecture.svg)',
      '',
    ].join('\n'),
    'utf8'
  );
  const textSourcePath = path.join(workspace, 'meeting.txt');
  fs.writeFileSync(
    textSourcePath,
    [
      'DOCX Engine V2 周例会',
      '时间: 2026年7月4日',
      '议题: 验证会议纪要模板的基础交付链路。',
      '结论: 会议纪要自检使用纯文本输入，不要求图片占位。',
      '',
    ].join('\n'),
    'utf8'
  );
  return { assetDir, richSourcePath, textSourcePath };
}

function runTemplate({ templateId, outDir, sourcePath, assetDir }) {
  const deliveryDir = path.join(outDir, `${templateId}.delivery`);
  const outputDocx = path.join(outDir, `${templateId}.docx`);
  fs.rmSync(deliveryDir, { recursive: true, force: true });
  fs.rmSync(outputDocx, { force: true });
  const args = [
    '--template-id',
    templateId,
    '--source',
    sourcePath,
    '--out-dir',
    deliveryDir,
    '--json',
  ];
  if (assetDir) {
    args.push('--asset-dir', assetDir);
  }
  const result = runEngine('run-job.js', args);
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `template failed: ${templateId}`);
  }
  fs.copyFileSync(path.join(deliveryDir, 'document.docx'), outputDocx);
}

function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  fs.mkdirSync(options.outDir, { recursive: true });
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-template-skill-self-test-'));
  try {
    const { assetDir, richSourcePath, textSourcePath } = writeSelfTestSources(workspace);
    runTemplate({
      templateId: 'general-proposal',
      outDir: options.outDir,
      sourcePath: richSourcePath,
      assetDir,
    });
    runTemplate({
      templateId: 'meeting-minutes',
      outDir: options.outDir,
      sourcePath: textSourcePath,
    });
    process.stdout.write(`self-test-ok\t${options.outDir}\n`);
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true });
  }
}

try {
  main();
} catch (error) {
  process.stderr.write(`self-test-failed\t${error.message}\n`);
  process.exitCode = 1;
}
