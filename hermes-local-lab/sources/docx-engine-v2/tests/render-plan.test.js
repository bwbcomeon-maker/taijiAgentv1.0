const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { packageAssets } = require('../src/assets/package-assets');
const { validateDomainObject } = require('../src/domain/validate');
const { buildRenderPlan } = require('../src/planning/build-render-plan');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');
const { getTemplatePackage } = require('../src/templates/registry');

const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-plan-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

async function makeSourcePackage(workspace) {
  const assetDir = path.join(workspace, 'source.assets');
  fs.mkdirSync(assetDir);
  fs.writeFileSync(path.join(assetDir, 'architecture.png'), ONE_BY_ONE_PNG);

  return normalizeMarkdownSource({
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
}

test('buildRenderPlan binds sections, assets, and template data in source order', async (t) => {
  const workspace = makeWorkspace(t);
  const sourcePackage = await makeSourcePackage(workspace);
  const assetPackage = packageAssets({
    sourcePackage,
    assetDir: 'source.assets',
    outDir: path.join(workspace, 'assets'),
  });
  const templatePackage = getTemplatePackage('general-proposal');

  const renderPlan = buildRenderPlan({ sourcePackage, templatePackage, assetPackage });

  assert.equal(renderPlan.schemaVersion, 'docx-engine-v2/render-plan');
  assert.deepEqual(
    renderPlan.sections.map((section) => section.title),
    ['一、总体架构', '二、实施安排']
  );
  assert.equal(renderPlan.figures[0].figureId, 'fig-001');
  assert.equal(renderPlan.figures[0].sectionTitle, '一、总体架构');
  assert.equal(renderPlan.tables[0].tableId, 'tbl-001');
  assert.equal(renderPlan.templateData.images[0].figureId, 'fig-001');
  assert.equal(renderPlan.templateData.images.length, 1);
  assert.match(renderPlan.templateData.images[0].logicalAssetId, /^logical-[a-f0-9]{16}$/);
  assert.match(renderPlan.templateData.images[0].occurrenceId, /^occurrence-[a-f0-9]{16}$/);
  assert.equal(renderPlan.templateData.images[0].metadata.sectionId, 'sec-001');
  assert.equal(renderPlan.templateData.images[0].metadata.blockId, 'block-005');
  assert.equal(renderPlan.templateData.images[0].metadata.afterBlockId, 'block-004');
  assert.match(renderPlan.templateData.images[0].sha256, /^[a-f0-9]{64}$/);
  assert.equal(renderPlan.templateData.tables[0].tableId, 'tbl-001');
  assert.equal(renderPlan.templateData.sections[0].blocks.filter((block) => block.type === 'figure').length, 1);

  const result = validateDomainObject('RenderPlan', renderPlan);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});

test('explicit repeated Mermaid occurrences share logical identity but keep distinct occurrences', async (t) => {
  const workspace = makeWorkspace(t);
  const markdownText = [
    '# 重复引用测试',
    '',
    '## 流程',
    '',
    '```mermaid',
    'flowchart LR',
    '  A[开始] --> B[结束]',
    '```',
    '',
    '```mermaid',
    'flowchart LR',
    '  A[开始] --> B[结束]',
    '```',
    '',
  ].join('\n');
  const sourcePackage = await normalizeMarkdownSource({
    sourcePath: path.join(workspace, 'source.md'),
    markdownText,
  });
  const assetPackage = packageAssets({ sourcePackage, outDir: path.join(workspace, 'assets') });
  const renderPlan = buildRenderPlan({
    sourcePackage,
    templatePackage: getTemplatePackage('general-proposal'),
    assetPackage,
  });

  assert.equal(renderPlan.templateData.images.length, 2);
  assert.equal(renderPlan.templateData.images[0].logicalAssetId, renderPlan.templateData.images[1].logicalAssetId);
  assert.notEqual(renderPlan.templateData.images[0].occurrenceId, renderPlan.templateData.images[1].occurrenceId);
});

test('runtime asset manifest is the identity source for canonical Mermaid figures', async (t) => {
  const workspace = makeWorkspace(t);
  const sourcePackage = await normalizeMarkdownSource({
    sourcePath: path.join(workspace, 'canonical.md'),
    markdownText: [
      '# 资产身份测试',
      '',
      '## 流程',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[开始] --> B[结束]',
      '```',
      '',
    ].join('\n'),
    assetManifest: {
      schema_version: 'expert-asset-manifest/v1',
      assets: [{
        logical_asset_id: 'stable-runtime-asset-id',
        asset_revision: 3,
        derived_from: { section_id: 'sec-001', block_id: 'block-003' },
        occurrences: [{ occurrence_id: 'runtime-occurrence-1', block_id: 'block-003', allow_repeated: false }],
      }],
    },
  });

  assert.equal(sourcePackage.figures[0].logicalAssetId, 'stable-runtime-asset-id');
  assert.equal(sourcePackage.figures[0].occurrenceId, 'runtime-occurrence-1');
  assert.equal(sourcePackage.figures[0].metadata.assetRevision, 3);
  assert.equal(sourcePackage.figures[0].metadata.identitySource, 'runtime_asset_manifest');
});

test('buildRenderPlan orders template images by source block order across image types', async (t) => {
  const workspace = makeWorkspace(t);
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
      '![系统总体架构](architecture.png)',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[用户资料] --> B[结构化草稿]',
      '```',
      '',
    ].join('\n'),
  });
  const assetPackage = packageAssets({
    sourcePackage,
    assetDir: 'source.assets',
    outDir: path.join(workspace, 'assets'),
  });

  const renderPlan = buildRenderPlan({
    sourcePackage,
    templatePackage: getTemplatePackage('general-proposal'),
    assetPackage,
  });

  assert.deepEqual(
    renderPlan.templateData.images.map((image) => image.figureId),
    ['fig-002', 'fig-001']
  );
  assert.equal(renderPlan.templateData.images[0].metadata.sourceImageId, 'image-001');
});
