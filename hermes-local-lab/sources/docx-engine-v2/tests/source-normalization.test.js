const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { once } = require('node:events');
const test = require('node:test');
const yazl = require('yazl');

const { validateDomainObject } = require('../src/domain/validate');
const { normalizeDocxSource } = require('../src/source/normalize-docx');
const { normalizeMarkdownSource } = require('../src/source/normalize-markdown');
const { normalizeTextSource } = require('../src/source/normalize-text');

function assertSourcePackage(source) {
  const result = validateDomainObject('SourcePackage', source);
  assert.equal(result.ok, true, JSON.stringify(result.errors || result));
}

test('normalizeMarkdownSource preserves rich Markdown structure for rendering', async () => {
  const source = await normalizeMarkdownSource({
    sourcePath: 'proposal.md',
    markdownText: `# 太极 Agent 企业知识助手建设方案

## 一、总体架构

太极 Agent 用本地知识库、专家团和模板渲染链交付可编辑文档。

![系统总体架构](./assets/architecture.png)

| 模块 | 职责 |
| --- | --- |
| 知识库 | 管理资料 |
| 专家团 | 组织方案 |

\`\`\`mermaid
flowchart LR
  A[用户资料] --> B[结构化草稿]
  B --> C[模板渲染]
\`\`\`

## 二、实施安排

| 阶段 | 交付物 |
| --- | --- |
| 试点 | 方案文档 |
| 推广 | 培训材料 |
`,
  });

  assertSourcePackage(source);
  assert.equal(source.schemaVersion, 'docx-engine-v2/source-package');
  assert.equal(source.title, '太极 Agent 企业知识助手建设方案');
  assert.deepEqual(
    source.sections.map((section) => section.title),
    ['一、总体架构', '二、实施安排']
  );
  assert.equal(source.tables.length, 2);
  assert.equal(source.tables[0].tableId, 'tbl-001');
  assert.equal(source.figures.length, 1);
  assert.equal(source.figures[0].figureId, 'fig-001');
  assert.equal(source.images[0].caption, '系统总体架构');
  assert.equal(source.blocks[0].type, 'heading');
  assert.equal(
    source.blocks.find((block) => block.metadata.figureId === 'fig-001')?.type,
    'mermaid'
  );
  for (const section of source.sections) {
    for (const blockId of section.blockIds) {
      const block = source.blocks.find((candidate) => candidate.id === blockId);
      assert.equal(block?.sectionId, section.sectionId);
    }
  }
});

test('normalizeTextSource reports missing rich content while keeping paragraphs', async () => {
  const source = await normalizeTextSource({
    sourcePath: 'notes.txt',
    text: '太极 Agent 企业知识助手建设方案\n\n只有普通段落，没有表格、图片或图形。',
  });

  assertSourcePackage(source);
  assert.equal(source.sourceType, 'text');
  assert.equal(source.tables.length, 0);
  assert.equal(source.figures.length, 0);
  assert.ok(source.warnings.some((warning) => warning.code === 'rich_content_missing'));
});

test('normalizeMarkdownSource gives markdownText precedence over legacy markdown input', async () => {
  const source = await normalizeMarkdownSource({
    sourcePath: 'proposal.md',
    markdownText: '# 正式参数标题',
    markdown: '# 兼容参数标题',
  });

  assertSourcePackage(source);
  assert.equal(source.title, '正式参数标题');
});

test('normalizeDocxSource extracts basic text and embedded media from docx zip', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-source-'));
  const sourcePath = path.join(tempDir, 'source.docx');
  await writeDocxFixture(sourcePath);

  const source = await normalizeDocxSource({ sourcePath });

  assertSourcePackage(source);
  assert.equal(source.sourceType, 'docx');
  assert.equal(source.title, '太极 Agent 企业知识助手建设方案');
  assert.ok(
    source.blocks.some((block) => block.text === '一、总体架构'),
    JSON.stringify(source.blocks)
  );
  assert.equal(source.embeddedMedia.length, 1);
  assert.equal(source.embeddedMedia[0].path, 'word/media/image1.png');
  assert.equal(source.tables.length, 1);
  assert.equal(source.tables[0].tableId, 'tbl-001');
  assert.deepEqual(source.tables[0].headers, ['阶段', '交付物']);
  assert.deepEqual(source.tables[0].rows, [['试点', '方案文档']]);
  assert.equal(source.figures.length, 1);
  assert.equal(source.figures[0].figureId, 'fig-001');
  assert.equal(source.figures[0].metadata.mediaPath, 'word/media/image1.png');
});

test('normalizeDocxSource rejects zip files that are missing the Word document body', async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-source-missing-'));
  const sourcePath = path.join(tempDir, 'source.docx');
  await writeDocxFixtureWithoutDocument(sourcePath);

  await assert.rejects(
    () => normalizeDocxSource({ sourcePath }),
    /missing word\/document\.xml/
  );
});

async function writeDocxFixture(filePath) {
  const zip = new yazl.ZipFile();
  const output = fs.createWriteStream(filePath);
  zip.outputStream.pipe(output);

  zip.addBuffer(
    Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
</Types>`),
    '[Content_Types].xml'
  );
  zip.addBuffer(
    Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>`),
    'word/_rels/document.xml.rels'
  );
  zip.addBuffer(
    Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>太极 Agent 企业知识助手建设方案</w:t></w:r></w:p>
    <w:p><w:r><w:t>一、总体架构</w:t></w:r></w:p>
    <w:p><w:r><w:t>围绕 source package 保留基础段落。</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>阶段</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>交付物</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>试点</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>方案文档</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:p><w:r><w:t>figureId=fig-001</w:t></w:r></w:p>
  </w:body>
</w:document>`),
    'word/document.xml'
  );
  zip.addEmptyDirectory('word/media');
  zip.addBuffer(Buffer.from([0x89, 0x50, 0x4e, 0x47]), 'word/media/image1.png');
  zip.end();

  await once(output, 'close');
}

async function writeDocxFixtureWithoutDocument(filePath) {
  const zip = new yazl.ZipFile();
  const output = fs.createWriteStream(filePath);
  zip.outputStream.pipe(output);

  zip.addBuffer(
    Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
</Types>`),
    '[Content_Types].xml'
  );
  zip.end();

  await once(output, 'close');
}
