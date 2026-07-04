const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const ENGINE_ROOT = path.join(__dirname, '..');
const PACKAGE_RICH_DRAFT = path.join(ENGINE_ROOT, 'src', 'cli', 'package-rich-draft.js');

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-rich-draft-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

function runPackage(args) {
  return spawnSync(process.execPath, [PACKAGE_RICH_DRAFT, ...args], {
    cwd: ENGINE_ROOT,
    encoding: 'utf8',
  });
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

test('package-rich-draft creates editable figure assets and a manifest from markdown', (t) => {
  const workspace = makeWorkspace(t);
  const assetDir = path.join(workspace, 'assets');
  const sourcePath = path.join(workspace, 'source.md');
  const outDir = path.join(workspace, 'package');
  fs.mkdirSync(assetDir, { recursive: true });
  fs.writeFileSync(
    path.join(assetDir, 'bad-flow.svg'),
    '<svg xmlns="http://www.w3.org/2000/svg" width="290" height="2038" viewBox="0 0 290 2038"></svg>\n'
  );
  fs.writeFileSync(
    sourcePath,
    [
      '# 集团公司信创终端配发实施方案',
      '',
      '## 五、实施流程',
      '',
      '```mermaid',
      'flowchart TD',
      '  A["签订采购合同"] --> B["集团中心仓验收"]',
      '  B --> C["分拣打包"]',
      '```',
      '',
      '![实施流程图](assets/bad-flow.svg)',
      '',
      '| 风险 | 表现 | 应对措施 |',
      '| --- | --- | --- |',
      '| 进度滞后 | 节点延期 | 日调度 |',
      '',
    ].join('\n')
  );

  const result = runPackage([
    '--source',
    sourcePath,
    '--out-dir',
    outDir,
    '--asset-dir',
    assetDir,
  ]);

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /package-rich-draft-ok/);
  const manifest = readJson(path.join(outDir, 'draft.manifest.json'));
  assert.equal(manifest.schemaVersion, 'rich-draft-package/v2');
  assert.equal(manifest.title, '集团公司信创终端配发实施方案');
  assert.equal(manifest.files.markdown, 'source.md');
  assert.equal(manifest.files.imageList, '图片清单.md');
  assert.equal(manifest.figures.length, 1);
  assert.equal(manifest.figures[0].figureId, 'fig-001');
  assert.equal(manifest.figures[0].caption, '实施流程图');
  assert.equal(manifest.figures[0].editable.sourceType, 'mermaid');
  assert.equal(manifest.tables.length, 1);
  assert.ok(manifest.blocks.some((block) => block.type === 'figure' && block.figureId === 'fig-001'));

  const sourceMmd = path.join(outDir, manifest.figures[0].editable.sourcePath);
  const displaySvg = path.join(outDir, manifest.figures[0].displayPath);
  assert.equal(fs.existsSync(sourceMmd), true);
  assert.equal(fs.existsSync(displaySvg), true);
  assert.match(fs.readFileSync(sourceMmd, 'utf8'), /签订采购合同/);
  assert.match(fs.readFileSync(displaySvg, 'utf8'), /width="1600"/);
  assert.match(fs.readFileSync(displaySvg, 'utf8'), /集团中心仓验收/);
  assert.match(fs.readFileSync(path.join(outDir, '图片清单.md'), 'utf8'), /fig-001/);
  assert.match(
    fs.readFileSync(path.join(outDir, 'source.md'), 'utf8'),
    /!\[实施流程图\]\(assets\/fig-001-实施流程图\/figure\.svg\)/
  );
});

test('package-rich-draft preserves a qualified display image while saving Mermaid source', (t) => {
  const workspace = makeWorkspace(t);
  const assetDir = path.join(workspace, 'assets');
  const sourcePath = path.join(workspace, 'source.md');
  const outDir = path.join(workspace, 'package');
  fs.mkdirSync(assetDir, { recursive: true });
  fs.writeFileSync(
    path.join(assetDir, 'qualified.svg'),
    [
      '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="630" viewBox="0 0 1120 630">',
      '<text>ORIGINAL_QUALIFIED_DISPLAY_IMAGE</text>',
      '</svg>',
      '',
    ].join('\n')
  );
  fs.writeFileSync(
    sourcePath,
    [
      '# 保留既有展示图方案',
      '',
      '## 二、组织架构',
      '',
      '```mermaid',
      'graph TD',
      '  A["领导小组"] --> B["项目办公室"]',
      '```',
      '',
      '![组织架构图](assets/qualified.svg)',
      '',
      '| 角色 | 职责 |',
      '| --- | --- |',
      '| PMO | 统筹协调 |',
      '',
    ].join('\n')
  );

  const result = runPackage([
    '--source',
    sourcePath,
    '--out-dir',
    outDir,
    '--asset-dir',
    assetDir,
  ]);

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const manifest = readJson(path.join(outDir, 'draft.manifest.json'));
  const displaySvg = fs.readFileSync(path.join(outDir, manifest.figures[0].displayPath), 'utf8');
  assert.match(displaySvg, /ORIGINAL_QUALIFIED_DISPLAY_IMAGE/);
  assert.equal(fs.existsSync(path.join(outDir, manifest.figures[0].editable.sourcePath)), true);
});

test('package-rich-draft refuses to write into a non-empty output directory', (t) => {
  const workspace = makeWorkspace(t);
  const sourcePath = path.join(workspace, 'source.md');
  const outDir = path.join(workspace, 'package');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'stale.svg'), '<svg></svg>\n');
  fs.writeFileSync(
    sourcePath,
    [
      '# 非空目录检测方案',
      '',
      '## 二、流程',
      '',
      '```mermaid',
      'flowchart TD',
      '  A["启动"] --> B["完成"]',
      '```',
      '',
      '| 项 | 值 |',
      '| --- | --- |',
      '| 状态 | 正常 |',
      '',
    ].join('\n')
  );

  const result = runPackage(['--source', sourcePath, '--out-dir', outDir]);

  assert.notEqual(result.status, 0, result.stdout);
  assert.match(result.stderr, /输出目录.*非空|非空.*输出目录/);
});
