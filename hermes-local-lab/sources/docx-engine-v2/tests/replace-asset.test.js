const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const yauzl = require('yauzl');

const { packageAssets } = require('../src/assets/package-assets');
const { buildRenderPlan } = require('../src/planning/build-render-plan');
const { postprocessDocx } = require('../src/rendering/postprocess-docx');
const { renderDocx } = require('../src/rendering/render-docx');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');
const { getTemplatePackage } = require('../src/templates/registry');

const rootDir = path.join(__dirname, '..');
const REPLACE_ASSET_CLI = path.join(rootDir, 'src', 'cli', 'replace-asset.js');
const PNG_REPLACEMENT = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);
const JPEG_REPLACEMENT = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-replace-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

test('replaceDocxAsset replaces the media bound to a stable figure id', async (t) => {
  const workspace = makeWorkspace(t);
  const docxPath = await makeDocxWithFigureMetadata(workspace);
  const replacementPath = path.join(workspace, 'replacement.svg');
  const replacedPath = path.join(workspace, 'replaced.docx');
  fs.writeFileSync(
    replacementPath,
    [
      '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180">',
      '<text>REPLACEMENT_FIGURE_MARKER</text>',
      '</svg>',
      '',
    ].join('\n'),
    'utf8'
  );

  const { replaceDocxAsset } = require('../src/assets/replace-docx-asset');
  const result = await replaceDocxAsset({
    docxPath,
    figureId: 'fig-001',
    imagePath: replacementPath,
    outputPath: replacedPath,
  });

  assert.equal(result.figureId, 'fig-001');
  assert.equal(result.mediaPath, 'word/media/fig-001.svg');
  assert.equal(fs.existsSync(replacedPath), true);
  const entries = await readZipEntries(replacedPath);
  const documentXml = entries.get('word/document.xml')?.toString('utf8') || '';
  const relationshipsXml = entries.get('word/_rels/document.xml.rels')?.toString('utf8') || '';
  const contentTypesXml = entries.get('[Content_Types].xml')?.toString('utf8') || '';
  assert.match(documentXml, /figureId=fig-001/);
  assert.match(relationshipsXml, /Target="media\/fig-001\.svg"/);
  assert.match(contentTypesXml, /Extension="svg" ContentType="image\/svg\+xml"/);
  const replacementSvg = findMediaText(entries, /REPLACEMENT_FIGURE_MARKER/);
  assert.match(replacementSvg, /REPLACEMENT_FIGURE_MARKER/);
});

test('replaceDocxAsset supports png replacements without changing unrelated paths', async (t) => {
  const workspace = makeWorkspace(t);
  const docxPath = await makeDocxWithFigureMetadata(workspace);
  const replacementPath = path.join(workspace, 'replacement.png');
  const replacedPath = path.join(workspace, 'replaced-png.docx');
  fs.writeFileSync(replacementPath, PNG_REPLACEMENT);

  const { replaceDocxAsset } = require('../src/assets/replace-docx-asset');
  await replaceDocxAsset({
    docxPath,
    figureId: 'fig-001',
    imagePath: replacementPath,
    outputPath: replacedPath,
  });

  const entries = await readZipEntries(replacedPath);
  const replacementBuffer = findMediaBuffer(entries, PNG_REPLACEMENT);
  assert.deepEqual(replacementBuffer, PNG_REPLACEMENT);
});

test('replaceDocxAsset supports jpeg replacements with matching content type', async (t) => {
  const workspace = makeWorkspace(t);
  const docxPath = await makeDocxWithFigureMetadata(workspace);
  const replacementPath = path.join(workspace, 'replacement.jpg');
  const replacedPath = path.join(workspace, 'replaced-jpeg.docx');
  fs.writeFileSync(replacementPath, JPEG_REPLACEMENT);

  const { replaceDocxAsset } = require('../src/assets/replace-docx-asset');
  const result = await replaceDocxAsset({
    docxPath,
    figureId: 'fig-001',
    imagePath: replacementPath,
    outputPath: replacedPath,
  });

  assert.equal(result.mediaPath, 'word/media/fig-001.jpg');
  const entries = await readZipEntries(replacedPath);
  const relationshipsXml = entries.get('word/_rels/document.xml.rels')?.toString('utf8') || '';
  const contentTypesXml = entries.get('[Content_Types].xml')?.toString('utf8') || '';
  assert.match(relationshipsXml, /Target="media\/fig-001\.jpg"/);
  assert.match(contentTypesXml, /Extension="jpg" ContentType="image\/jpeg"/);
  assert.deepEqual(findMediaBuffer(entries, JPEG_REPLACEMENT), JPEG_REPLACEMENT);
});

test('replace-asset CLI writes JSON on successful replacement', async (t) => {
  const workspace = makeWorkspace(t);
  const docxPath = await makeDocxWithFigureMetadata(workspace);
  const replacementPath = path.join(workspace, 'replacement.svg');
  const replacedPath = path.join(workspace, 'replaced-from-cli.docx');
  fs.writeFileSync(
    replacementPath,
    '<svg xmlns="http://www.w3.org/2000/svg"><text>REPLACEMENT_FIGURE_MARKER</text></svg>\n',
    'utf8'
  );

  const result = spawnSync(process.execPath, [
    REPLACE_ASSET_CLI,
    '--docx',
    docxPath,
    '--figure-id',
    'fig-001',
    '--image',
    replacementPath,
    '--out',
    replacedPath,
  ], { cwd: rootDir, encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(payload.figureId, 'fig-001');
  assert.equal(payload.mediaPath, 'word/media/fig-001.svg');
  assert.equal(fs.existsSync(replacedPath), true);
  const entries = await readZipEntries(replacedPath);
  const replacementSvg = findMediaText(entries, /REPLACEMENT_FIGURE_MARKER/);
  assert.match(replacementSvg, /REPLACEMENT_FIGURE_MARKER/);
});

test('replace-asset CLI exits 3 when the requested figure id is missing', async (t) => {
  const workspace = makeWorkspace(t);
  const oldDocxWithoutFigureId = await makeDocxWithoutFigureMetadata(workspace);
  const replacementPath = path.join(workspace, 'replacement.svg');
  const replacedPath = path.join(workspace, 'replaced.docx');
  fs.writeFileSync(
    replacementPath,
    '<svg xmlns="http://www.w3.org/2000/svg"><text>REPLACEMENT_FIGURE_MARKER</text></svg>\n',
    'utf8'
  );

  const result = spawnSync(process.execPath, [
    'src/cli/replace-asset.js',
    '--docx',
    oldDocxWithoutFigureId,
    '--figure-id',
    'fig-001',
    '--image',
    replacementPath,
    '--out',
    replacedPath,
  ], { cwd: rootDir, encoding: 'utf8' });

  assert.equal(result.status, 3);
  assert.match(result.stderr, /未在 DOCX 中找到图片标识/);
});

test('replace-asset CLI refuses to overwrite an existing output file', async (t) => {
  const workspace = makeWorkspace(t);
  const docxPath = await makeDocxWithFigureMetadata(workspace);
  const replacementPath = path.join(workspace, 'replacement.svg');
  const replacedPath = path.join(workspace, 'replaced.docx');
  fs.writeFileSync(replacementPath, '<svg xmlns="http://www.w3.org/2000/svg"/>\n', 'utf8');
  fs.writeFileSync(replacedPath, 'existing output', 'utf8');

  const result = spawnSync(process.execPath, [
    REPLACE_ASSET_CLI,
    '--docx',
    docxPath,
    '--figure-id',
    'fig-001',
    '--image',
    replacementPath,
    '--out',
    replacedPath,
  ], { cwd: rootDir, encoding: 'utf8' });

  assert.equal(result.status, 3);
  assert.match(result.stderr, /输出文件已存在/);
  assert.equal(fs.readFileSync(replacedPath, 'utf8'), 'existing output');
});

async function makeDocxWithFigureMetadata(workspace) {
  const { renderedPath, renderPlan } = await renderTemplateDocx(workspace);
  const outputPath = path.join(workspace, 'with-figure-id.docx');
  await postprocessDocx({ docxPath: renderedPath, renderPlan, outputPath });
  return outputPath;
}

async function makeDocxWithoutFigureMetadata(workspace) {
  const { renderedPath } = await renderTemplateDocx(workspace);
  return renderedPath;
}

async function renderTemplateDocx(workspace) {
  const sourcePath = path.join(workspace, 'source.md');
  const sourcePackage = await normalizeMarkdownSource({
    sourcePath,
    markdownText: [
      '# Enterprise AI rollout proposal',
      '',
      '## Architecture',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[Source] --> B[Render plan]',
      '  B --> C[Delivery package]',
      '```',
      '',
    ].join('\n'),
  });
  const assetPackage = packageAssets({
    sourcePackage,
    outDir: path.join(workspace, 'assets'),
  });
  const templatePackage = getTemplatePackage('general-proposal');
  const renderPlan = buildRenderPlan({ sourcePackage, templatePackage, assetPackage });
  const renderedPath = path.join(workspace, 'rendered.docx');
  await renderDocx({ templatePackage, renderPlan, outputPath: renderedPath });

  return { renderedPath, renderPlan };
}

function findMediaText(entries, pattern) {
  for (const [entryName, entryBuffer] of entries) {
    if (!entryName.startsWith('word/media/')) {
      continue;
    }
    const text = entryBuffer.toString('utf8');
    if (pattern.test(text)) {
      return text;
    }
  }
  return '';
}

function findMediaBuffer(entries, expectedBuffer) {
  for (const [entryName, entryBuffer] of entries) {
    if (entryName.startsWith('word/media/') && entryBuffer.equals(expectedBuffer)) {
      return entryBuffer;
    }
  }
  return Buffer.alloc(0);
}

function readZipEntries(docxPath) {
  return new Promise((resolve, reject) => {
    yauzl.open(docxPath, { lazyEntries: true }, (openError, zipfile) => {
      if (openError) {
        reject(openError);
        return;
      }

      const entries = new Map();
      let settled = false;

      const fail = (error) => {
        if (settled) {
          return;
        }
        settled = true;
        try {
          zipfile.close();
        } catch (_closeError) {
          // Preserve the original error.
        }
        reject(error);
      };

      const finish = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(entries);
      };

      zipfile.on('entry', (entry) => {
        if (settled) {
          return;
        }
        if (entry.fileName.endsWith('/')) {
          zipfile.readEntry();
          return;
        }

        zipfile.openReadStream(entry, (streamError, readStream) => {
          if (streamError) {
            fail(streamError);
            return;
          }

          const chunks = [];
          readStream.on('data', (chunk) => chunks.push(chunk));
          readStream.on('error', fail);
          readStream.on('end', () => {
            if (settled) {
              return;
            }
            entries.set(entry.fileName, Buffer.concat(chunks));
            zipfile.readEntry();
          });
        });
      });
      zipfile.on('error', fail);
      zipfile.on('end', finish);
      zipfile.readEntry();
    });
  });
}
