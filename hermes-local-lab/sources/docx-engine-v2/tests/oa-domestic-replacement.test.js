const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { readZipEntriesFromBuffer } = require('../src/replay/source-replay');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');
const { runDocumentJob } = require('../src/workflow/run-document-job');

const ENGINE_ROOT = path.join(__dirname, '..');
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'oa-domestic-replacement');
const OA_SOURCE = path.join(FIXTURE_DIR, 'OA系统国产化替代详细设计方案.md');

function makeTempWorkspace(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-oa-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('normalizes OA domestic replacement proposal as rich source content', async () => {
  const sourcePackage = await normalizeMarkdownSource({ sourcePath: OA_SOURCE });

  assert.equal(sourcePackage.title, 'OA系统及关联系统国产化替代详细设计方案');
  assert.equal(sourcePackage.sections.length, 40);
  assert.equal(sourcePackage.sections[0].title, '1. 项目概述');
  assert.equal(
    sourcePackage.blocks.some((block) => /文档编号|XC-OA-DESIGN|^\s*---\s*$/.test(block.text || '')),
    false
  );
  assert.equal(sourcePackage.tables.length, 21);
  assert.equal(sourcePackage.figures.length, 7);
  assert.equal(sourcePackage.images.length, 0);
  assert.deepEqual(sourcePackage.tables[0].headers, ['序号', '替代层级', '现网技术', '国产替代方案', '替代策略']);
  assert.equal(sourcePackage.figures[6].sourceType, 'html');
  assert.equal(sourcePackage.figures[6].displayPath, 'oa-architecture.html');
  assert.equal(sourcePackage.figures[6].sectionId, 'sec-007');
});

test('OA domestic replacement proposal template job preserves rich table content', async (t) => {
  const root = makeTempWorkspace(t);
  const deliveryDir = path.join(root, 'delivery');

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId: 'general-proposal',
    sourcePath: OA_SOURCE,
    deliveryDir,
  });

  assert.doesNotMatch(result.message || '', /当前为 0 个/);
  assert.equal(result.ok, true, result.message);
  assert.equal(result.qualityReport.checks.find((check) => check.id === 'table_content')?.status, 'passed');
  assert.equal(result.qualityReport.checks.find((check) => check.id === 'table_placement')?.status, 'passed');

  const assetPackage = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'asset-package.json'), 'utf8'));
  const mermaidFigures = assetPackage.figures.filter((figure) => figure.sourceType === 'mermaid');
  const htmlFigure = assetPackage.figures.find((figure) => figure.sourceType === 'html');
  assert.equal(mermaidFigures.length, 6);
  assert.equal(htmlFigure?.dimensions?.width, 1200);
  assert.equal(htmlFigure?.dimensions?.height, 920);
  for (const figure of mermaidFigures) {
    assert.match(figure.displayPath, /\.png$/);
    assert.equal(figure.metadata.rasterizer, '@resvg/resvg-js');
    const vectorPath = path.join(deliveryDir, figure.metadata.vectorDisplayPath);
    const vectorText = fs.readFileSync(vectorPath, 'utf8');
    assert.doesNotMatch(vectorText, /Mermaid source/);
  }

  const documentEntries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
  const documentXml = documentEntries.get('word/document.xml').toString('utf8');
  assert.doesNotMatch(documentXml, /<wp:anchor\b(?:(?!<\/wp:anchor>)[\s\S])*figureId=fig-/);
  assert.match(documentXml, /<wp:inline\b[\s\S]*?figureId=fig-001/);
});
