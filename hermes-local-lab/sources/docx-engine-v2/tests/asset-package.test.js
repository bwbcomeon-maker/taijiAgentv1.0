const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { packageAssets } = require('../src/assets/package-assets');
const { validateDomainObject } = require('../src/domain/validate');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');

const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-assets-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

async function makeSourcePackage(workspace) {
  const assetDir = path.join(workspace, 'source.assets');
  fs.mkdirSync(assetDir);
  fs.writeFileSync(path.join(assetDir, 'architecture.png'), ONE_BY_ONE_PNG);

  const sourcePackage = await normalizeMarkdownSource({
    sourcePath: path.join(workspace, 'source.md'),
    markdownText: [
      '# 太极 Agent 企业知识助手建设方案',
      '',
      '## 一、总体架构',
      '',
      '太极 Agent 用本地知识库、专家团和模板渲染链交付可编辑文档。',
      '',
      '| 模块 | 职责 |',
      '| --- | --- |',
      '| 知识库 | 管理资料 |',
      '| 专家团 | 组织方案 |',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[用户资料] --> B[结构化草稿]',
      '  B --> C[模板渲染]',
      '```',
      '',
      '![系统总体架构](architecture.png)',
      '',
      '## 二、实施安排',
      '',
      '按试点、推广、验收分阶段推进。',
      '',
    ].join('\n'),
  });

  return { sourcePackage, assetDir };
}

test('packageAssets writes editable figure assets and copies qualified image files', async (t) => {
  const workspace = makeWorkspace(t);
  const { sourcePackage, assetDir } = await makeSourcePackage(workspace);
  const outDir = path.join(workspace, 'assets');

  const assetPackage = packageAssets({ sourcePackage, assetDir, outDir });

  assert.equal(assetPackage.schemaVersion, 'docx-engine-v2/asset-package');
  assert.equal(assetPackage.figures[0].figureId, 'fig-001');
  assert.equal(assetPackage.tables[0].tableId, 'tbl-001');
  assert.equal(fs.existsSync(path.join(workspace, assetPackage.figures[0].displayPath)), true);
  assert.equal(
    fs.existsSync(path.join(workspace, assetPackage.figures[0].editable.sourcePath)),
    true
  );
  assert.equal(fs.existsSync(path.join(workspace, assetPackage.images[0].displayPath)), true);
  assert.deepEqual(
    fs.readFileSync(path.join(workspace, assetPackage.images[0].displayPath)),
    ONE_BY_ONE_PNG
  );

  const result = validateDomainObject('AssetPackage', assetPackage);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
  assert.throws(() => packageAssets({ sourcePackage, assetDir, outDir }), /输出目录非空/);
});

test('packageAssets resolves relative assetDir beside the source document', async (t) => {
  const workspace = makeWorkspace(t);
  const { sourcePackage } = await makeSourcePackage(workspace);

  const assetPackage = packageAssets({
    sourcePackage,
    assetDir: 'source.assets',
    outDir: path.join(workspace, 'assets'),
  });

  assert.equal(fs.existsSync(path.join(workspace, assetPackage.images[0].displayPath)), true);
});

test('packageAssets fails before rendering when a required image asset is missing', async (t) => {
  const workspace = makeWorkspace(t);
  const sourcePackage = await normalizeMarkdownSource({
    sourcePath: path.join(workspace, 'source.md'),
    markdownText: [
      '# 太极 Agent 企业知识助手建设方案',
      '',
      '## 一、总体架构',
      '',
      '![系统总体架构](missing.png)',
      '',
    ].join('\n'),
  });

  assert.throws(
    () =>
      packageAssets({
        sourcePackage,
        assetDir: path.join(workspace, 'source.assets'),
        outDir: path.join(workspace, 'assets'),
      }),
    /缺少.*资产/
  );
});

test('packageAssets rejects unsafe asset identifiers before writing output paths', async (t) => {
  const workspace = makeWorkspace(t);
  const { sourcePackage, assetDir } = await makeSourcePackage(workspace);
  sourcePackage.images[0].imageId = '../escape';

  assert.throws(
    () =>
      packageAssets({
        sourcePackage,
        assetDir,
        outDir: path.join(workspace, 'assets'),
      }),
    /不安全的资产标识/
  );
});
