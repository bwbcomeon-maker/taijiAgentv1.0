# DOCX Template Source Binding And Quality Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Taiji Agent 1.0 桌面端“用户给出源文件后套用模板仍按当前成果执行”的断链问题，并补齐 OA 国产化方案这类富内容文档生成 DOCX 时的宽表、HTML 图资产和错误提示质量门禁。

**Architecture:** 以 Taiji Agent 1.0 为唯一真实入口。模板选择阶段必须把 `source_path` 作为一等状态从后端传到前端卡片，再由前端选择按钮带回后端；DOCX 引擎必须保留源文档表格列、渲染 Mermaid/HTML 图为可插入资产，并用质量报告证明结果可读、可审、可交付。

**Tech Stack:** Python Flask-style routes in `hermes-webui/api/routes.py`, vanilla JS UI in `static/messages.js` and `static/ui.js`, Node DOCX engine v2 under `hermes-local-lab/sources/docx-engine-v2`, runtime skill under `/Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill`, pytest, Node test runner, WPS/Word manual gate.

---

## First-Principles Constraints

1. 源文件是套模板任务的事实来源。只要用户明确给了 `.md/.docx/.txt` 路径或上传了源文件，后续所有模板选择、生成 DOCX、质量报告都必须绑定这个源文件，不能再回退成“当前成果”。
2. 模板选择只决定版式，不决定内容。选择模板卡片必须保留 `source_path`、模板列表、选择/取消状态；点击模板时发送 `template_id + source_path`。
3. 没有源文件时必须安全失败。不能偷偷拿聊天摘要、文件清单、上一条 assistant 文本代替源文档生成 DOCX。
4. 富内容不能被静默丢失。Markdown 中的表格、Mermaid 图、HTML 图、普通图片都必须进入 source package 或 asset package；渲染器不能截列、不能把图表集中到末尾。
5. 用户看到的错误必须指向真实原因。如果源文件没绑定，应提示“未读取到源文件”；如果源文件缺富内容，才提示“请补齐表格和图示”。
6. “验证通过”必须包含结构验证、内容验证、视觉验证和桌面端真实验收；未跑 WPS/Word 时只能标记“未验证”或“带限制通过”。

## File Map

- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/api/routes.py`
  - 负责后端意图识别、模板选择 payload、源文件路径解析、DOCX job 调用。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/messages.js`
  - 负责用户输入归一化、模板选择点击回填、非流式模板卡片消息。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/ui.js`
  - 负责模板选择卡片渲染、source path 可见提示、按钮 data 字段和可访问性。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/style.css`
  - 负责模板选择卡片源文件提示、错误/禁用/长路径换行样式。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py`
  - 覆盖源文件路径经过模板选择后不丢失。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_template_skill_invocation.py`
  - 覆盖前端选择模板时不再固定写“当前成果”。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_ui_contract.py`
  - 覆盖模板选择卡片存在源文件可见提示和 accessible name。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/source/normalize-markdown.js`
  - 负责 Markdown 表格、Mermaid、HTML 图引用归一化。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/assets/render-figure-assets.js`
  - 负责 Mermaid/HTML 图生成可插入图片资产。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/render/docx-table-renderer.js`
  - 负责宽表动态列数、横向页或拆表策略，禁止截列。
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/quality/assert-rich-docx-quality.js`
  - 负责源表格列完整性、图表落位、图片数量、错误文本、质量状态。
- Add: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/fixtures/oa-domestic-replacement/OA系统国产化替代详细设计方案.md`
  - 复制用户当前 OA fixture。
- Add: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/fixtures/oa-domestic-replacement/oa-architecture.html`
  - 复制用户当前 HTML 架构图 fixture。
- Add: `/Users/bwb/Documents/工作/taiji-agentv1.0/docs/superpowers/reviews/2026-07-06-docx-template-source-binding-ux-qa.md`
  - 保存最终中文《前端 UX QA 报告》和桌面端/WPS 验收结果。

## Task 1: Baseline And RED Tests For Source Binding

**Files:**
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py`
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_template_skill_invocation.py`

- [ ] **Step 1: Add backend RED test for path + template selection**

Add a test that sends `将"/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md"套用模板` and asserts:

```python
result = routes._docx_template_invocation_result_for_session(
    '将"/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md"套用模板',
    session,
)
assert result["docx_template_selection_required"] is True
assert result["source_path"] == "/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md"
assistant = routes._docx_non_streaming_assistant_message(result, 1)
assert assistant["docx_template_selection"]["source_path"].endswith("OA系统国产化替代详细设计方案.md")
```

- [ ] **Step 2: Add backend RED test for selected template + explicit source**

Add a test that calls:

```python
result = routes._docx_template_invocation_result_for_session(
    '/docx-template-skill 请将源文件 "/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md" 套用通用方案模板（templateId: general-proposal）。',
    session,
)
assert seen["payload"]["source_path"] == "/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md"
assert seen["payload"]["template_id"] == "general-proposal"
```

- [ ] **Step 3: Add frontend static RED test for preserving source path**

In `test_docx_template_skill_invocation.py`, assert the UI code contains:

```python
assert "data-source-path" in UI_JS
assert "sourcePath" in MESSAGES_JS
assert "请将源文件" in MESSAGES_JS
```

- [ ] **Step 4: Run RED tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
pytest tests/test_docx_engine_v2_routes.py::test_docx_template_selection_preserves_explicit_source_path tests/test_docx_engine_v2_routes.py::test_docx_template_selected_uses_explicit_source_path tests/test_docx_template_skill_invocation.py -q
```

Expected: fail because `source_path` is not present in selection payload and frontend click still sends `当前成果`.

## Task 2: Backend Source Path Propagation

**Files:**
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/api/routes.py`
- Test: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py`

- [ ] **Step 1: Preserve explicit source path in template selection result**

Update `_docx_template_invocation_result(prompt)` so it extracts:

```python
source_path = _docx_template_source_path_from_text(prompt)
```

When no `template_id` exists, return it in the selection payload:

```python
"source_path": source_path,
"message": (
    f"请选择要套用的模板；选择前不会生成 JSON 或渲染 DOCX。源文件：{source_path}"
    if source_path
    else "请选择要套用的模板；在选择前不会生成 JSON 或渲染 DOCX。"
),
```

- [ ] **Step 2: Preserve source path in assistant message**

Update `_docx_non_streaming_assistant_message(result, now)` selection branch:

```python
assistant["docx_template_selection"] = {
    "code": result.get("code") or _DOCX_TEMPLATE_SELECTION_REQUIRED_CODE,
    "templates": list(result.get("templates") or []),
    "examples": list(result.get("examples") or []),
    "source_path": str(result.get("source_path") or ""),
}
```

- [ ] **Step 3: Preserve source path when normalizing explicit template messages**

Update `_normalize_docx_template_invocation_message(prompt)` so if `template_id` and `source_path` both exist, it returns:

```python
return f'/docx-template-skill 请将源文件 "{source_path}" 套用{template_name}（templateId: {template_id}）。'
```

If there is no `source_path`, keep the current `当前成果` fallback.

- [ ] **Step 4: Run backend tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
pytest tests/test_docx_engine_v2_routes.py -q
```

Expected: source binding tests pass; existing “current result” tests still pass.

## Task 3: Frontend Template Card Source Binding

**Files:**
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/messages.js`
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/ui.js`
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/style.css`
- Test: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_template_skill_invocation.py`
- Test: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_ui_contract.py`

- [ ] **Step 1: Include source path in live selection message**

Update `renderDocxTemplateSelectionMessage`:

```javascript
docx_template_selection:{
  code:startData&&startData.code||'template_selection_required',
  templates:Array.isArray(startData&&startData.templates)?startData.templates:[],
  examples:Array.isArray(startData&&startData.examples)?startData.examples:[],
  source_path:String(startData&&startData.source_path||startData&&startData.sourcePath||''),
}
```

- [ ] **Step 2: Render source path visibly in template card**

Update `_docxTemplateSelectionHtml(selection)`:

```javascript
const sourcePath=String(selection&&selection.source_path||selection&&selection.sourcePath||'').trim();
const sourceHtml=sourcePath
  ? `<div class="docx-template-selection-source"><strong>源文件</strong><span>${esc(sourcePath)}</span></div>`
  : '<div class="docx-template-selection-source is-missing"><strong>源文件</strong><span>未绑定，将尝试使用当前结果。</span></div>';
```

Root element:

```javascript
`<div class="docx-template-selection-card" role="group" aria-label="选择文档模板" data-source-path="${esc(sourcePath)}">`
```

Insert `sourceHtml` under the note.

- [ ] **Step 3: Send source path when choosing template**

Update `chooseDocxTemplate(button)`:

```javascript
const sourcePath=root?String(root.dataset.sourcePath||'').trim():'';
if(sourcePath){
  composer.value=`/docx-template-skill 请将源文件 "${sourcePath}" 套用${templateName||templateId}（templateId: ${templateId}）。`;
}else{
  composer.value=`/docx-template-skill 请把当前成果套用${templateName||templateId}（templateId: ${templateId}）。`;
}
```

- [ ] **Step 4: Add long-path and state styles**

Add CSS:

```css
.docx-template-selection-source{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px;padding:0 16px 12px;color:var(--muted);font-size:12px;line-height:1.45;}
.docx-template-selection-source strong{color:var(--text);}
.docx-template-selection-source span{min-width:0;overflow-wrap:anywhere;}
.docx-template-selection-source.is-missing span{color:var(--error,#b42318);}
```

- [ ] **Step 5: Run frontend static contract tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
pytest tests/test_docx_template_skill_invocation.py tests/test_docx_engine_v2_ui_contract.py -q
```

Expected: tests pass and source path is visible and carried by click handler.

## Task 4: Error Message Accuracy And Preflight Counts

**Files:**
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/api/routes.py`
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/workflow/run-document-job.js`
- Test: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/workflow.test.js`

- [ ] **Step 1: Distinguish missing source from missing rich content**

Before calling `create_job`, if explicit `source_path` does not exist, return:

```python
return {
    "ok": False,
    "docx_source_required": True,
    "template_id": template_id,
    "template": template,
    "source_path": source_path,
    "message": f"未读取到源文件：{source_path}。请确认文件存在，或重新上传 Markdown/DOCX 源文件。",
}
```

- [ ] **Step 2: Improve rich-content gate error**

In `assertSourceMeetsTemplateRequirements`, include source summary:

```javascript
throw new Error(
  `模板 ${templatePackage.id} 的输入不满足要求：${failures.join('；')}。` +
  `本次读取到的源内容统计为：表格 ${tableCount} 个，图示或图片 ${visualCount} 个。` +
  `如果你的源文件实际包含图表，请检查模板选择卡片中的源文件路径是否正确。`
);
```

- [ ] **Step 3: Add tests**

Add tests for:

```javascript
assert.match(error.message, /源文件路径是否正确/);
assert.match(error.message, /表格 0 个/);
assert.match(error.message, /图示或图片 0 个/);
```

- [ ] **Step 4: Run workflow tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/*.test.js
```

Expected: missing rich content errors are specific and no longer falsely instruct users to “补内容” when the likely failure is source binding.

## Task 5: OA Fixture Rich Content Regression

**Files:**
- Add: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/fixtures/oa-domestic-replacement/OA系统国产化替代详细设计方案.md`
- Add: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/fixtures/oa-domestic-replacement/oa-architecture.html`
- Add/Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/oa-domestic-replacement.test.js`

- [ ] **Step 1: Copy fixture files**

Copy exactly:

```bash
mkdir -p /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/fixtures/oa-domestic-replacement
cp /Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/fixtures/oa-domestic-replacement/
cp /Users/bwb/Desktop/OA国产化替代方案/oa-architecture.html /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/fixtures/oa-domestic-replacement/
```

- [ ] **Step 2: Add source normalize test**

Assert:

```javascript
assert.equal(sourcePackage.tables.length, 21);
assert.equal(sourcePackage.figures.length, 6);
assert.equal(sourcePackage.images.length, 0);
assert.equal(sourcePackage.sections.length, 40);
```

- [ ] **Step 3: Add end-to-end RED test**

Run the document job against the fixture and assert final delivery must not fail on `0/0`; current expected RED failure is table truncation until Task 7 is fixed:

```javascript
assert.notMatch(result.message || '', /当前为 0 个/);
assert.match(result.message || '', /DOCX table content|validation/i);
```

- [ ] **Step 4: Run fixture test**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/oa-domestic-replacement.test.js
```

Expected before later fixes: normalize passes, DOCX quality fails on real content mismatch, proving we moved past source binding.

## Task 6: HTML Diagram Asset Conversion

**Files:**
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/source/normalize-markdown.js`
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/assets/render-figure-assets.js`
- Test: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/oa-domestic-replacement.test.js`

- [ ] **Step 1: Detect local HTML figure references**

When Markdown table rows or paragraphs reference a sibling `.html` file, create a figure item:

```javascript
{
  type: 'html',
  sourcePath: '/absolute/path/oa-architecture.html',
  caption: '全景架构图（汇报用）',
  sectionId: currentSection.id,
  afterBlockId: previousBlock.id,
  layoutIntent: 'architecture'
}
```

- [ ] **Step 2: Render HTML to PNG asset**

Use existing browser/render tooling if already present in docx-engine-v2. Render at no less than `1800x1200` for architecture diagrams and save:

```text
assets/figures/fig-xxx/figure.png
assets/figures/fig-xxx/source.html
```

- [ ] **Step 3: Add offline failure rule**

If HTML depends on external fonts or network resources, rendering must still succeed with system fonts. Network failure cannot block local DOCX generation.

- [ ] **Step 4: Test**

Assert:

```javascript
assert.ok(assetPackage.figures.some((figure) => figure.sourcePath.endsWith('oa-architecture.html')));
assert.ok(fs.existsSync(htmlFigure.displayPath));
assert.match(htmlFigure.caption, /全景架构图/);
```

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/oa-domestic-replacement.test.js
```

Expected: OA fixture visual count includes Mermaid figures plus the HTML architecture figure asset.

## Task 7: Wide Table Rendering Without Column Loss

**Files:**
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/render/docx-table-renderer.js`
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/quality/assert-rich-docx-quality.js`
- Test: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/oa-domestic-replacement.test.js`

- [ ] **Step 1: Add dynamic table layout policy**

Implement:

```javascript
function chooseTableLayout(table) {
  const columnCount = Math.max(table.headers.length, ...table.rows.map((row) => row.length));
  if (columnCount <= 4) return { mode: 'portrait', maxColumnsPerPart: columnCount };
  if (columnCount <= 6) return { mode: 'landscape', maxColumnsPerPart: columnCount };
  return { mode: 'split', maxColumnsPerPart: 5, repeatFirstColumn: true };
}
```

- [ ] **Step 2: Render all source columns**

For `landscape`, create a section break around the table if the DOCX library supports it. If not, use smaller table font and full page width, but preserve every cell.

For `split`, create `表 X-1` and `表 X-2` parts that together contain all original columns, with the first business key column repeated.

- [ ] **Step 3: Strengthen quality validation**

Quality checker must compare source table cell text against all rendered table parts. Failure message must name missing columns:

```text
表 tbl-001 缺失列：替代策略
```

- [ ] **Step 4: Test OA wide tables**

Assert:

```javascript
assert.doesNotMatch(report.message || '', /do not contain expected/);
assert.doesNotMatch(report.message || '', /缺失列/);
assert.equal(report.status, 'passed_with_warnings');
```

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/oa-domestic-replacement.test.js
```

Expected: no table column is lost; if WPS visual was not run, status is `passed_with_warnings`, not `passed`.

## Task 8: Quality Gate And Delivery Semantics

**Files:**
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/src/quality/assert-rich-docx-quality.js`
- Modify: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/ui.js`
- Test: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2/tests/quality.test.js`

- [ ] **Step 1: Quality status rules**

Set statuses:

```text
failed: structural/content/layout gate fails
passed_with_warnings: automated gates pass but WPS/Word visual is not verified, or source contains 待确认/待填
passed: automated gates pass and manual visual marker exists
```

- [ ] **Step 2: Required checks**

Quality report must include:

```text
source_path_bound
table_count
visual_count
table_column_integrity
figure_asset_exists
docx_media_count
docx_error_text_absent
markdown_residue_absent
wps_visual
```

- [ ] **Step 3: UI wording**

Delivery card must display:

```text
质量状态：带限制通过（尚未进行 WPS/Word 目视验收）
```

when `quality_status === "passed_with_warnings"`.

- [ ] **Step 4: Run tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/quality.test.js tests/oa-domestic-replacement.test.js
```

Expected: no path claims full pass without visual evidence.

## Task 9: Runtime Skill And Canonical Sync

**Files:**
- Modify generated copy: `/Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill`
- Modify zip: `/Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill.zip`
- Modify runtime copy: `/Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill`

- [ ] **Step 1: Build copyable skill from v1 source**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node scripts/build-copyable-skill.js --out-dir /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill
```

- [ ] **Step 2: Rebuild zip**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎
rm -f docx-template-skill.zip
zip -qr docx-template-skill.zip docx-template-skill -x '**/.DS_Store' '**/._*' '**/~$*'
unzip -tq docx-template-skill.zip
```

- [ ] **Step 3: Sync runtime skill**

Run:

```bash
rsync -a --delete /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill/ /Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill/
```

- [ ] **Step 4: Runtime self-test**

Run:

```bash
node /Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill/scripts/self-test.js --out-dir /tmp/docx-template-runtime-self-test
```

Expected: runtime self-test passes and runtime skill contains the same source binding and quality code.

## Task 10: Desktop And WPS End-To-End Verification

**Files:**
- Add: `/Users/bwb/Documents/工作/taiji-agentv1.0/docs/superpowers/reviews/2026-07-06-docx-template-source-binding-ux-qa.md`

- [ ] **Step 1: Start Taiji Agent 1.0 only**

Confirm running app is Taiji Agent 1.0, not 2.0. If 2.0 is open, close it without editing.

- [ ] **Step 2: Reproduce fixed user path**

In Taiji Agent 1.0 desktop chat, send:

```text
将"/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md"套用模板
```

Expected: template selection card appears and visibly displays the source file path.

- [ ] **Step 3: Select general-proposal**

Click `通用方案模板`.

Expected: generated command or backend payload includes the same source path, not `当前成果`.

- [ ] **Step 4: Generate DOCX**

Expected:

```text
document_path exists
quality_report_path exists
quality_status is passed_with_warnings or passed
```

If WPS/Word visual is not opened, status must not be `passed`.

- [ ] **Step 5: WPS/Word visual gate**

Open generated DOCX and capture screenshots for:

```text
封面
主目录
图目录/表目录
2.1 架构图章节
1.2 替代范围宽表
至少一个 Mermaid 图页面
质量报告页面或交付目录
```

Record the result in `/Users/bwb/Documents/工作/taiji-agentv1.0/docs/superpowers/reviews/2026-07-06-docx-template-source-binding-ux-qa.md`.

## Test Matrix

| Case | Input | Expected |
|---|---|---|
| Natural template request without source | `套用模板` | Shows template selection or source request; no DOCX generated silently |
| Absolute path + no template | `将"/Users/.../方案.md"套用模板` | Shows template selection with visible source path |
| Select template after path | click `通用方案模板` | Sends same `source_path`; no `当前成果` fallback |
| Explicit template + absolute path | `/docx-template-skill 请将源文件 "/Users/.../方案.md" 套用通用方案模板（templateId: general-proposal）` | Direct job uses source file |
| Missing source file | path does not exist | Error says file unreadable, not “请补齐表格和图示” |
| Source has 21 tables and 6 Mermaid figures | OA fixture | Gate does not report 0/0 |
| HTML architecture diagram | `oa-architecture.html` beside MD | Converted to DOCX image asset or explicit warning |
| 5+ column tables | OA fixture tables | No column loss; landscape/split policy applies |
| No WPS visual gate | automated tests only | `passed_with_warnings`, not `passed` |
| Taiji Agent 2.0 accidentally open | desktop verification | Stop and switch to 1.0; no v2 verification claims |

## Adversarial Review

### Attack 1: Source path still lost through frontend state

Risk: Backend returns `source_path`, but UI card ignores it.

Check: Static test must assert `data-source-path` exists and click handler emits `请将源文件 "{sourcePath}"`. Desktop screenshot must show source file path in the card.

Verdict: Covered by Tasks 2, 3, 10.

### Attack 2: Source path with Chinese, spaces, quotes breaks parsing

Risk: Regex extracts partial path or drops quotes.

Check: Tests must include `/Users/bwb/Desktop/OA国产化替代方案/OA系统国产化替代详细设计方案.md` and a path containing spaces.

Adjustment: If existing regex fails, update `_DOCX_TEMPLATE_SOURCE_PATH_RE` to accept quoted absolute paths until closing quote.

Verdict: Covered by Task 1 and Task 2, but execution must add the spaces fixture before declaring pass.

### Attack 3: Missing source incorrectly falls back to current result

Risk: If file does not exist, system materializes assistant text and creates misleading DOCX.

Check: Missing explicit path must fail with “未读取到源文件”.

Verdict: Covered by Task 4.

### Attack 4: 0/0 fixed but final DOCX still unusable

Risk: Passing source binding only moves failure to table truncation or unreadable figures.

Check: OA fixture must run through full DOCX quality gate, including table column integrity and figure assets.

Verdict: Covered by Tasks 5, 6, 7, 8.

### Attack 5: HTML architecture diagram never enters DOCX

Risk: The file exists beside Markdown but is only mentioned as text.

Check: HTML reference must become a figure asset or produce a clear warning naming `oa-architecture.html`.

Verdict: Covered by Task 6.

### Attack 6: Quality report says “通过” without visual proof

Risk: Automated XML checks pass but WPS shows unreadable layout.

Check: Without WPS/Word manual marker, status must be `passed_with_warnings`.

Verdict: Covered by Task 8 and Task 10.

### Attack 7: Source fixed in source repo but runtime still old

Risk: Desktop app keeps using installed runtime skill with old behavior.

Check: Must sync canonical skill, zip, and runtime skill, then run runtime self-test.

Verdict: Covered by Task 9.

### Attack 8: Wrong app version used for验收

Risk: Verifying in Taiji Agent 2.0 gives false confidence for 1.0.

Check: Desktop verification explicitly starts Taiji Agent 1.0 only.

Verdict: Covered by Task 10.

### Attack 9: UI has state but user cannot understand it

Risk: Card technically stores source path but user cannot see what will be used.

Check: Source path must be visibly displayed, wrap long paths, and be announced by accessible label/region text.

Verdict: Covered by Task 3 and UX report.

## 中文《前端 UX QA 报告》计划项

| 检查项 | 计划状态 | 验收方式 |
|---|---|---|
| 模板选择卡片可见 | 待执行 | Taiji Agent 1.0 桌面截图 |
| 源文件路径可见 | 待执行 | 卡片中展示完整路径并可换行 |
| 选择按钮可访问 | 待执行 | 按钮有可见文字和 aria-label |
| 取消路径可用 | 待执行 | 点击取消后不生成 DOCX |
| 错误提示可操作 | 待执行 | 缺源文件、缺富内容、宽表失败分别给不同提示 |
| 长路径不撑破布局 | 待执行 | 中文路径和空格路径截图 |
| 键盘可达 | 未验证，执行时必须验证 | Tab 到模板按钮和取消按钮 |
| WPS/Word 目视 | 未验证，执行时必须验证 | 保存截图和人工检查结果 |

## Completion Criteria

This plan is complete only when:

1. Backend and frontend tests prove source path survives template selection.
2. OA fixture normalize result is `tables=21`, `figures>=6`, and the template gate no longer reports `0/0`.
3. Generated DOCX preserves wide-table columns or explicitly splits tables without losing cells.
4. HTML architecture diagram is inserted as a DOCX figure asset or reported as a named actionable warning.
5. Runtime skill is synced and runtime self-test passes.
6. Taiji Agent 1.0 desktop flow is verified with screenshots.
7. WPS/Word visual result is recorded; if not recorded, final status remains `passed_with_warnings`.
