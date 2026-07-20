const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { packageAssets, renderDeterministicMermaidSvg } = require('../src/assets/package-assets');
const { parseSequenceDiagram } = require('../src/assets/mermaid-renderer');
const { svgDimensions } = require('../src/assets/svg-rasterizer');
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
      '以下为独立上传的系统总体架构图。',
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
  assert.match(assetPackage.figures[0].editable.sourceSha256, /^[a-f0-9]{64}$/);
  assert.equal(fs.existsSync(path.join(workspace, assetPackage.images[0].displayPath)), true);
  assert.match(assetPackage.figures[0].sha256, /^[a-f0-9]{64}$/);
  assert.match(assetPackage.images[0].sha256, /^[a-f0-9]{64}$/);
  assert.match(assetPackage.figures[0].displayPath, /figure\.png$/);
  assert.equal(isPngFile(path.join(workspace, assetPackage.figures[0].displayPath)), true);
  assert.match(assetPackage.figures[0].metadata.vectorDisplayPath, /figure\.svg$/);
  assert.equal(assetPackage.figures[0].metadata.rasterizer, '@resvg/resvg-js');
  const generatedFigureSvg = fs.readFileSync(
    path.join(workspace, assetPackage.figures[0].metadata.vectorDisplayPath),
    'utf8'
  );
  assert.match(generatedFigureSvg, />用户资料</);
  assert.match(generatedFigureSvg, />结构化草稿</);
  assert.match(generatedFigureSvg, /<line\b/);
  assert.doesNotMatch(generatedFigureSvg, />Mermaid diagram</);
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

test('renderDeterministicMermaidSvg renders C4 context diagrams as readable business architecture', () => {
  const svg = renderDeterministicMermaidSvg({
    figure: { figureId: 'fig-c4', caption: '目标架构全景图' },
    sourceText: [
      'C4Context',
      '  title OA系统国产化替代 — 目标架构全景',
      '  Person(user, "全体职工", "PC浏览器 / 移动端APP")',
      '  System_Boundary(oa_system, "OA办公系统") {',
      '    System(oa_app, "OA应用服务", "泛微/致远信创版")',
      '    System(sso, "统一身份认证", "竹云BIM 4A")',
      '  }',
      '  System_Ext(hr, "人事系统", "外部对接")',
      '  Rel(user, oa_app, "HTTPS", "国密SSL")',
      '  Rel(oa_app, sso, "OAuth2.0/CAS", "认证")',
      '  Rel(oa_app, hr, "WebService", "组织同步")',
    ].join('\n'),
  });

  assert.match(svg, /目标架构全景图/);
  assert.match(svg, /全体职工/);
  assert.match(svg, /OA应用服务/);
  assert.match(svg, /统一身份认证/);
  assert.match(svg, /人事系统/);
  assert.match(svg, /HTTPS/);
  assert.doesNotMatch(svg, /Mermaid source/);
});

test('renderDeterministicMermaidSvg renders sequence diagrams as readable swimlanes', () => {
  const svg = renderDeterministicMermaidSvg({
    figure: { figureId: 'fig-seq', caption: '上线切换时序' },
    sourceText: [
      'sequenceDiagram',
      '  participant PM as 项目经理',
      '  participant OPS as 运维团队',
      '  participant DEV as 开发团队',
      '  Note over PM,DEV: T0 — 周五 20:00 停服窗口开始',
      '  PM->>OPS: 发布停服公告',
      '  OPS->>DEV: 停止现网OA所有服务',
      '  DEV-->>PM: 完成切换确认',
    ].join('\n'),
  });

  assert.match(svg, /上线切换时序/);
  assert.match(svg, /项目经理/);
  assert.match(svg, /运维团队/);
  assert.match(svg, /开发团队/);
  assert.match(svg, /发布停服公告/);
  assert.match(svg, /完成切换确认/);
  assert.doesNotMatch(svg, /Mermaid source/);
});

test('renderDeterministicMermaidSvg uses compact canvases for simple diagrams', () => {
  const flowchartSvg = renderDeterministicMermaidSvg({
    figure: { figureId: 'fig-flow', caption: '业务闭环流程' },
    sourceText: [
      'flowchart LR',
      '  A[事件接报] --> B[统一研判]',
      '  B --> C[指挥调度]',
      '  C --> D[处置反馈]',
    ].join('\n'),
  });
  const sequenceSvg = renderDeterministicMermaidSvg({
    figure: { figureId: 'fig-seq-simple', caption: '业务协同时序' },
    sourceText: [
      'sequenceDiagram',
      '  participant Center as 指挥中心',
      '  participant Dept as 业务部门',
      '  participant Team as 现场队伍',
      '  Center->>Dept: 发送事件信息',
      '  Dept-->>Center: 返回研判建议',
      '  Center->>Team: 下发处置任务',
      '  Team-->>Center: 回传处置结果',
    ].join('\n'),
  });

  assert.ok(svgDimensions(flowchartSvg).height <= 520);
  assert.ok(svgDimensions(sequenceSvg).height <= 680);
});

test('parseSequenceDiagram does not create phantom participants from arrow syntax', () => {
  const diagram = parseSequenceDiagram([
    'sequenceDiagram',
    '  participant Sensor as 设备/传感器',
    '  participant Platform as 运营中台',
    '  participant Dispatcher as 调度员',
    '  participant Worker as 现场人员',
    '  participant Manager as 管理人员',
    '  Sensor->>Platform: 上报异常数据',
    '  Platform->>Dispatcher: 生成待派发工单',
    '  Dispatcher->>Worker: 派单并确认到场时间',
    '  Worker-->>Platform: 提交处理结果和照片',
    '  Platform->>Manager: 推送闭环结果和指标影响',
  ].join('\n'));

  assert.deepEqual(
    diagram.participants.map((participant) => participant.id),
    ['Sensor', 'Platform', 'Dispatcher', 'Worker', 'Manager']
  );
  assert.equal(diagram.steps.filter((step) => step.type === 'message').length, 5);
});

test('renderDeterministicMermaidSvg renders mindmap diagrams as semantic hierarchy trees', () => {
  const svg = renderDeterministicMermaidSvg({
    figure: { figureId: 'fig-generic', caption: '能力分解图' },
    sourceText: [
      'mindmap',
      '  root((园区平台))',
      '    能耗管理',
      '    设备台账',
      '    运维工单',
    ].join('\n'),
  });

  assert.match(svg, /能力分解图/);
  assert.doesNotMatch(svg, /通用图示渲染/);
  assert.match(svg, /园区平台/);
  assert.match(svg, /能耗管理/);
  assert.match(svg, /设备台账/);
  assert.doesNotMatch(svg, /Mermaid source/);
});

test('renderDeterministicMermaidSvg renders gantt diagrams as timelines', () => {
  const svg = renderDeterministicMermaidSvg({
    figure: { figureId: 'fig-gantt', caption: '实施计划' },
    sourceText: [
      'gantt',
      '  title 智慧园区建设计划',
      '  dateFormat YYYY-MM-DD',
      '  section 准备阶段',
      '  需求确认与数据盘点 :a1, 2026-07-10, 14d',
      '  办公楼试点接入 :a2, after a1, 21d',
      '  section 推广阶段',
      '  全园区数据接入 :a3, after a2, 30d',
    ].join('\n'),
  });

  assert.match(svg, /智慧园区建设计划/);
  assert.match(svg, /准备阶段/);
  assert.match(svg, /需求确认与数据盘点/);
  assert.match(svg, /全园区数据接入/);
  assert.match(svg, /2026-07-10/);
  assert.doesNotMatch(svg, /通用图示渲染/);
});

test('renderDeterministicMermaidSvg renders class diagrams as object relationship diagrams', () => {
  const svg = renderDeterministicMermaidSvg({
    figure: { figureId: 'fig-class', caption: '对象模型' },
    sourceText: [
      'classDiagram',
      '  class 设备台账 {',
      '    +设备编码',
      '    +运行状态',
      '  }',
      '  class 采集点 {',
      '    +点位编码',
      '  }',
      '  设备台账 --> 采集点 : 绑定',
    ].join('\n'),
  });

  assert.match(svg, /对象模型/);
  assert.match(svg, /设备台账/);
  assert.match(svg, /运行状态/);
  assert.match(svg, /采集点/);
  assert.match(svg, /绑定/);
  assert.doesNotMatch(svg, /通用图示渲染/);
});

test('renderDeterministicMermaidSvg rejects unsupported Mermaid types instead of silent degradation', () => {
  assert.throws(
    () => renderDeterministicMermaidSvg({
      figure: { figureId: 'fig-unsupported', caption: '未知图' },
      sourceText: ['pie', '  title 投资比例', '  "软件": 70', '  "服务": 30'].join('\n'),
    }),
    /不支持的 Mermaid 图类型/
  );
});

test('svgDimensions uses the root svg viewBox instead of nested shape dimensions', () => {
  const dimensions = svgDimensions([
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 920">',
    '  <defs>',
    '    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"></pattern>',
    '  </defs>',
    '  <rect width="100%" height="100%" fill="url(#grid)"/>',
    '</svg>',
  ].join('\n'));

  assert.equal(dimensions.width, 1200);
  assert.equal(dimensions.height, 920);
  assert.equal(Math.round(dimensions.aspectRatio * 1000), 1304);
});

function isPngFile(filePath) {
  const signature = fs.readFileSync(filePath).subarray(0, 8);
  return signature.equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
}
