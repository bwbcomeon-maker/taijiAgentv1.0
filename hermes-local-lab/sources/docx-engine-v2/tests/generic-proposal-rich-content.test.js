const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { readZipEntriesFromBuffer } = require('../src/replay/source-replay');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');
const { runDocumentJob } = require('../src/workflow/run-document-job');

const ENGINE_ROOT = path.join(__dirname, '..');

function makeTempWorkspace(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-generic-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function writeGenericProposal(root) {
  const sourcePath = path.join(root, '智慧园区综合管理平台建设方案.md');
  fs.writeFileSync(
    sourcePath,
    [
      '# 智慧园区综合管理平台建设方案',
      '',
      '## 1. 建设目标',
      '',
      '本方案围绕园区能耗、设备、工单和报表四类高频场景建设统一管理平台。',
      '',
      '| 场景 | 现状痛点 | 建设目标 | 衡量指标 | 责任部门 | 备注 |',
      '| --- | --- | --- | --- | --- | --- |',
      '| 能耗管理 | 数据分散 | 分项采集与异常预警 | 月度能耗下降 8% | 后勤部 | 首期覆盖办公楼 |',
      '| 设备台账 | 台账不一致 | 统一编码和生命周期管理 | 台账准确率 99% | 设备部 | 对接资产系统 |',
      '| 运维工单 | 响应慢 | 移动派单和闭环跟踪 | 平均响应小于 30 分钟 | 运维中心 | 保留人工兜底 |',
      '',
      '```mermaid',
      'mindmap',
      '  root((园区平台))',
      '    能耗管理',
      '    设备台账',
      '    运维工单',
      '    经营报表',
      '```',
      '',
      '## 2. 业务对象模型',
      '',
      '平台以设备、采集点、工单和告警为核心对象，形成可追踪的运营数据链路。',
      '',
      '```mermaid',
      'classDiagram',
      '  class 设备台账 {',
      '    +设备编码',
      '    +运行状态',
      '  }',
      '  class 采集点 {',
      '    +点位编码',
      '    +采集周期',
      '  }',
      '  设备台账 --> 采集点 : 绑定',
      '  采集点 --> 告警事件 : 触发',
      '```',
      '',
      '| 阶段 | 工作内容 | 输出物 |',
      '| --- | --- | --- |',
      '| 试点 | 接入办公楼能耗和重点设备 | 试点报告 |',
      '| 推广 | 扩展到全园区和移动工单 | 推广清单 |',
      '| 验收 | 指标复盘和制度固化 | 验收报告 |',
      '',
    ].join('\n'),
    'utf8'
  );
  return sourcePath;
}

function writeUniversalAdversarialProposal(root) {
  const sourcePath = path.join(root, '通用能力验收方案.md');
  fs.writeFileSync(
    sourcePath,
    [
      '# 通用能力验收方案',
      '',
      '项目名称：通用模板套用验收',
      '文档编号：TJ-GENERIC-001',
      '文档密级：内部',
      '客户单位：客户单位',
      '编制单位：北京太极信息系统技术有限公司',
      '---',
      '',
      '本文用于验证任意方案类文档在套用模板后，能保持章节结构、图表就地、表格动态列数和图片可读。',
      '',
      '# 1. 总体方案',
      '',
      '本章说明整体目标、范围和验收口径。表格应紧跟当前说明，而不是被放入附录。',
      '',
      '| 维度 | 要求 |',
      '| --- | --- |',
      '| 模板选择 | 用户必须显式选择模板 |',
      '| 图表位置 | 按正文顺序插入 |',
      '',
      '```mermaid',
      'flowchart LR',
      '    A[需求输入] --> B[富内容初稿包]',
      '    B --> C[显式模板选择]',
      '    C --> D[按块渲染 DOCX]',
      '    D --> E[质量验收]',
      '```',
      '',
      '## 1.1 角色分工',
      '',
      '本节验证二级章节内的表格是否保留 5 列，且不会因为模板固定列数导致丢列。',
      '',
      '| 角色 | 职责 | 交付物 | 审核人 | 备注 |',
      '| --- | --- | --- | --- | --- |',
      '| 业务负责人 | 确认目标 | 需求清单 | 分管领导 | 必填 |',
      '| 技术负责人 | 制定方案 | 技术方案 | 项目经理 | 必填 |',
      '',
      '# 2. 实施计划',
      '',
      '本章验证甘特图类资产应根据类型进行清晰布局，不应硬压缩成不可读小图。',
      '',
      '| 阶段 | 周期 | 输入 | 输出 | |',
      '| --- | --- | --- | --- | --- |',
      '| 准备 | 第 1 周 | 需求与资产 | 计划确认 | |',
      '| 实施 | 第 2-3 周 | 环境与配置 | 上线包 | |',
      '| 验收 | 第 4 周 | 测试记录 | 验收报告 | |',
      '',
      '```mermaid',
      'gantt',
      '    title 通用实施节奏',
      '    dateFormat  YYYY-MM-DD',
      '    section 准备',
      '    需求确认 :a1, 2026-07-01, 3d',
      '    资产准备 :a2, after a1, 4d',
      '    section 实施',
      '    环境部署 :b1, 2026-07-08, 5d',
      '    联调验证 :b2, after b1, 5d',
      '    section 验收',
      '    文档归档 :c1, 2026-07-20, 3d',
      '```',
      '',
      '# 3. 风险与验收',
      '',
      '本章验证 4 列风险表格应保留完整列结构，图片和表格不能集中在文末。',
      '',
      '| 风险项 | 触发条件 | 应对措施 | 责任人 |',
      '| --- | --- | --- | --- |',
      '| 图片缺失 | 初稿未生成资产 | 中断并提示补齐资产 | 文档生成器 |',
      '| 锚点错误 | 图表找不到章节 | 中断并提示修正锚点 | 模板引擎 |',
      '',
      '```mermaid',
      'flowchart TD',
      '    A[自动验收] --> B{是否通过}',
      '    B -- 是 --> C[生成交付包]',
      '    B -- 否 --> D[输出中文修正建议]',
      '    D --> A',
      '```',
      '',
    ].join('\n'),
    'utf8'
  );
  return sourcePath;
}

test('general proposal pipeline works for non-sample rich documents with dynamic tables and generic diagrams', async (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = writeGenericProposal(root);
  const deliveryDir = path.join(root, 'delivery');

  const sourcePackage = await normalizeMarkdownSource({ sourcePath });
  assert.equal(sourcePackage.title, '智慧园区综合管理平台建设方案');
  assert.equal(sourcePackage.tables.length, 2);
  assert.equal(sourcePackage.figures.length, 2);
  assert.deepEqual(sourcePackage.tables[0].headers, ['场景', '现状痛点', '建设目标', '衡量指标', '责任部门', '备注']);
  assert.deepEqual(sourcePackage.tables[1].headers, ['阶段', '工作内容', '输出物']);
  assert.equal(sourcePackage.tables[0].title, '建设目标表');
  assert.equal(sourcePackage.figures[0].caption, '建设目标');

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId: 'general-proposal',
    sourcePath,
    deliveryDir,
  });

  assert.equal(result.ok, true, result.message);
  assert.equal(result.qualityReport.checks.find((check) => check.id === 'table_content')?.status, 'passed');
  assert.equal(result.qualityReport.checks.find((check) => check.id === 'table_placement')?.status, 'passed');
  assert.equal(result.qualityReport.checks.find((check) => check.id === 'figure_placement')?.status, 'passed');

  const assetPackage = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'asset-package.json'), 'utf8'));
  assert.equal(assetPackage.figures.length, 2);
  for (const figure of assetPackage.figures) {
    assert.match(figure.displayPath, /\.png$/);
    assert.equal(figure.metadata.rasterizer, '@resvg/resvg-js');
    const svg = fs.readFileSync(path.join(deliveryDir, figure.metadata.vectorDisplayPath), 'utf8');
    assert.doesNotMatch(svg, /通用图示渲染/);
    assert.doesNotMatch(svg, /Mermaid source/);
  }

  const renderPlan = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'render-plan.json'), 'utf8'));
  assert.equal(renderPlan.templateData.tables[0].title, '建设目标表');
  assert.equal(renderPlan.templateData.images[0].caption, '建设目标');
  assert.equal(
    renderPlan.templateData.sections[0].blocks.some((block) => block.type === 'heading' && block.text === '1. 建设目标'),
    true
  );
  assert.equal(
    renderPlan.templateData.sections[0].blocks.some((block) => block.type === 'paragraph' && block.text === '1. 建设目标'),
    false
  );
  assert.equal(renderPlan.templateData.tables[0].columns.length, 6);
  assert.equal(renderPlan.templateData.tables[1].columns.length, 3);
  assert.deepEqual(
    renderPlan.templateData.tables[0].columns.map((column) => column.text),
    ['场景', '现状痛点', '建设目标', '衡量指标', '责任部门', '备注']
  );

  const documentEntries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
  const documentXml = documentEntries.get('word/document.xml').toString('utf8');
  const coverXml = documentXml.slice(0, documentXml.indexOf('<w:sectPr'));
  const footerXml = [...documentEntries.entries()]
    .filter(([entryName]) => /^word\/footer\d+\.xml$/.test(entryName))
    .map(([, buffer]) => buffer.toString('utf8'))
    .join('\n');
  const documentText = documentXml.replace(/<[^>]+>/g, '');
  assert.match(documentXml, /场景/);
  assert.match(documentXml, /备注/);
  assert.match(documentXml, /输出物/);
  assert.match(documentXml, /<w:tblHeader\/>/);
  assert.match(documentXml, /<w:cantSplit\/>/);
  assert.doesNotMatch(documentText, /第\s*2\s*级/);
  assert.doesNotMatch(documentText, /第一章、\s*1[.．、]\s*建设目标/);
  assert.doesNotMatch(documentText, /1\.1\s*1[.．、]\s*建设目标/);
  assert.doesNotMatch(documentXml, /当前为 0 个/);
  assert.doesNotMatch(documentXml, /Mermaid source/);
  assert.doesNotMatch(documentXml, /w:before="4565"[^>]*w:beforeLines="1400"/);
  assert.doesNotMatch(coverXml, /w:before="(?:1[6-9]\d{2}|[2-9]\d{3,})"/);
  assert.doesNotMatch(footerXml, /NUMPAGES/);
  assert.doesNotMatch(footerXml, /<(?:wps:txbx|v:textbox|wp:anchor)\b/);
  assert.match(footerXml, /PAGE/);
});

test('general proposal pipeline is generic across headings, dynamic tables, directories, and diagram types', async (t) => {
  const root = makeTempWorkspace(t);
  const sourcePath = writeUniversalAdversarialProposal(root);
  const deliveryDir = path.join(root, 'delivery');

  const sourcePackage = await normalizeMarkdownSource({ sourcePath });
  assert.equal(sourcePackage.title, '通用能力验收方案');
  assert.deepEqual(
    sourcePackage.sections.map((section) => section.title),
    ['概述', '1. 总体方案', '1.1 角色分工', '2. 实施计划', '3. 风险与验收']
  );
  assert.equal(sourcePackage.blocks.some((block) => /文档密级|文档编号|编制单位/.test(block.text || '')), false);
  assert.deepEqual(sourcePackage.tables.map((table) => table.headers.length), [2, 5, 4, 4]);
  assert.equal(sourcePackage.figures.length, 3);

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId: 'general-proposal',
    sourcePath,
    deliveryDir,
  });

  assert.equal(result.ok, true, result.message);
  for (const checkId of ['table_content', 'table_placement', 'figure_placement', 'block_order']) {
    assert.equal(result.qualityReport.checks.find((check) => check.id === checkId)?.status, 'passed', checkId);
  }

  const renderPlan = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'render-plan.json'), 'utf8'));
  assert.deepEqual(renderPlan.templateData.tables.map((table) => table.columns.length), [2, 5, 4, 4]);
  assert.deepEqual(
    renderPlan.templateData.tables[2].columns.map((column) => column.text),
    ['阶段', '周期', '输入', '输出']
  );
  assert.equal(renderPlan.templateData.images.length, 3);

  const documentEntries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
  const documentXml = documentEntries.get('word/document.xml').toString('utf8');
  const documentText = documentXml.replace(/<[^>]+>/g, '');
  const tables = [...documentXml.matchAll(/<w:tbl[\s\S]*?<\/w:tbl>/g)].map((match) => match[0]);
  const directoryEntries = [...documentXml.matchAll(/<w:p\b[\s\S]*?<\/w:p>/g)]
    .map((match) => match[0])
    .filter((paragraph) => paragraph.includes('docx-engine-v2 directoryEntry'));

  assert.equal(tables.length, 4);
  assert.deepEqual(tables.map((table) => (table.match(/<w:gridCol\b/g) || []).length), [2, 5, 4, 4]);
  assert.equal((documentXml.match(/figureId=fig-/g) || []).length >= 3, true);
  assert.match(documentText, /图 3 风险与验收/);
  assert.doesNotMatch(documentText, /文档密级：内部|TJ-GENERIC-001|北京太极信息系统技术有限公司本文用于/);
  assert.ok(directoryEntries.length >= 12, `expected main/table/figure directory entries, got ${directoryEntries.length}`);
  for (const entry of directoryEntries) {
    assert.match(entry, /<w:spacing\b[^>]*w:before="0"[^>]*w:after="0"[^>]*w:line="240"/);
  }
});
