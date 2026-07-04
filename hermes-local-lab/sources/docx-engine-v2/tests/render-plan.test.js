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

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-plan-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

async function makeSourcePackage(workspace) {
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
    assetDir: '',
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
  assert.equal(renderPlan.templateData.tables[0].tableId, 'tbl-001');

  const result = validateDomainObject('RenderPlan', renderPlan);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
});
