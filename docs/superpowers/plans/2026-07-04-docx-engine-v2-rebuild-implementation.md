# DOCX Engine V2 Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a versioned DOCX production engine that turns normalized source content, selected templates, assets, render plans, validation reports, and user-facing delivery packages into one repeatable workflow.

**Architecture:** Add `hermes-local-lab/sources/docx-engine-v2/` as the only source of truth for document jobs, template packages, asset packaging, DOCX rendering, validation, and delivery packages. Keep the current `docx-template-skill` behavior as a generated compatibility shell that calls v2, then connect Taiji WebUI/Desktop to the v2 job API with visible workflow controls.

**Tech Stack:** Node.js CommonJS, `node:test`, Ajv JSON schema validation, Carbone DOCX rendering adapter, `yauzl`/`yazl` DOCX zip inspection and rewriting, Python Flask-style WebUI routes, vanilla JS/CSS frontend, pytest, ESLint runtime guard.

---

## Scope Check

The design spec spans three connected areas: engine core, copyable skill compatibility, and Taiji desktop workflow. This plan keeps them in one vertical implementation because each task produces a working, testable slice and the final acceptance criteria require all three to agree on the same delivery package contract.

If execution discovers a large unrelated WebUI redesign requirement, stop at the v2 API boundary, write a separate frontend-specific plan, and keep the engine commits intact.

## Current Evidence And Constraints

- Design spec: `docs/superpowers/specs/2026-07-04-docx-engine-v2-design.md`
- Current external renderer source: `/Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill`
- Current installed runtime skill: `/Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill`
- Current WebUI source: `hermes-local-lab/sources/hermes-webui`
- Existing WebUI tests already cover template selection, skill invocation, rich-draft routing, and figure adjustment entrypoints.
- The root worktree is dirty before this plan. Preserve unrelated changes and stage only files touched by the current task.
- The external renderer directory is not a git repository. Treat it as migration input and generated runtime output, not as the final source of truth.

## File Structure

### New DOCX Engine

- Create: `hermes-local-lab/sources/docx-engine-v2/package.json`
  - Owns package scripts and runtime dependencies for the v2 engine.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/domain/schemas.js`
  - Defines plain JSON schema objects for `DocumentJob`, `SourcePackage`, `TemplatePackage`, `AssetPackage`, `RenderPlan`, `ValidationReport`, and `DeliveryPackage`.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/domain/validate.js`
  - Compiles schemas and returns structured validation errors.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/domain/document-job.js`
  - Creates and transitions document jobs.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-markdown.js`
  - Converts Markdown into ordered source blocks, sections, tables, Mermaid blocks, and image references.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-text.js`
  - Converts plain text into paragraph-only source packages and records missing rich-content risks.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-docx.js`
  - Extracts basic DOCX paragraphs, tables, media entries, and existing `figureId` metadata.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/templates/registry.js`
  - Reads template registry and resolves template package paths.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/templates/validate-template-package.js`
  - Validates template manifest, schema, sample, and template renderability.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/assets/package-assets.js`
  - Produces stable `figureId` and `tableId` metadata, copies qualified display assets, and preserves editable sources.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/assets/render-figure-asset.js`
  - Renders Mermaid source into SVG display files and updates manifest bindings.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/assets/replace-docx-asset.js`
  - Replaces an existing DOCX image by `figureId`.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/planning/build-render-plan.js`
  - Maps normalized source blocks to deterministic output sections, tables, figures, and template data.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/rendering/render-docx.js`
  - Calls Carbone with the selected template and v2 template data.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/rendering/postprocess-docx.js`
  - Inserts image relationships and writes `figureId` metadata using the render plan.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/validation/validate-delivery-package.js`
  - Validates delivery package completeness, DOCX zip structure, image coverage, table coverage, metadata coverage, and WPS/Word verification state.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/delivery/write-delivery-package.js`
  - Writes `document.docx`, `source.md`, `assets/`, `job.manifest.json`, `template.manifest.json`, `render-plan.json`, `quality-report.json`, and `README-图片调整说明.md`.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/cli/run-job.js`
  - Main engine CLI.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/cli/list-templates.js`
  - Lists template packages for Taiji and compatibility scripts.
- Create: `hermes-local-lab/sources/docx-engine-v2/src/cli/replace-asset.js`
  - Replaces a DOCX asset by stable id.
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/general-proposal/`
  - Migrated template package.
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/meeting-minutes/`
  - Migrated template package.
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/`
  - Versioned compatibility shell source.
- Create: `hermes-local-lab/sources/docx-engine-v2/scripts/build-copyable-skill.js`
  - Builds a copyable `docx-template-skill` package from v2 source into a target directory.
- Create tests under: `hermes-local-lab/sources/docx-engine-v2/tests/*.test.js`

### Taiji WebUI Integration

- Create: `hermes-local-lab/sources/hermes-webui/api/docx_engine_v2.py`
  - Python boundary for invoking Node v2 CLI safely from a workspace.
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
  - Adds routes for template list, job creation, delivery summary, and asset replacement.
- Modify: `hermes-local-lab/sources/hermes-webui/static/ui.js`
  - Adds visible document workbench card states and actions.
- Modify: `hermes-local-lab/sources/hermes-webui/static/messages.js`
  - Routes explicit template workbench actions through the visible workflow.
- Modify: `hermes-local-lab/sources/hermes-webui/static/style.css`
  - Adds restrained document workbench layout.
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_ui_contract.py`

---

## Task 0: Baseline Protection

**Files:**
- Inspect only: root worktree, existing tests, external renderer package

- [ ] **Step 1: Capture current root worktree state**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git status --short
```

Expected:

```text
 M hermes-local-lab/custom-skills/writing-agent/workflow-producer/SKILL.md
 M hermes-local-lab/sources/hermes-webui/api/routes.py
 M hermes-local-lab/sources/hermes-webui/static/messages.js
 M hermes-local-lab/sources/hermes-webui/static/style.css
 M hermes-local-lab/sources/hermes-webui/static/ui.js
?? docs/superpowers/plans/
```

Extra dirty paths may appear. Do not revert or stage paths unrelated to the current task.

- [ ] **Step 2: Verify existing focused gates before edits**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
uv run --with pytest python -m pytest tests/test_docx_template_backend_router.py tests/test_rich_draft_chat_routing.py tests/test_docx_template_skill_invocation.py -q
npm run lint:runtime
```

Expected:

```text
passed
```

For `npm run lint:runtime`, expected result is process exit code `0`.

- [ ] **Step 3: Verify current external renderer baseline**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎
node --test tests/*.test.js
cd /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill
node scripts/self-test.js --out-dir /tmp/docx-template-skill-v2-baseline
```

Expected:

```text
pass
self-test-ok
```

- [ ] **Step 4: Commit only if a baseline doc update is created**

This task normally has no commit because it is inspection only.

## Task 1: Add Engine Contract RED Tests

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/domain-contract.test.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/run-job-contract.test.js`
- Implement in Task 2: `hermes-local-lab/sources/docx-engine-v2/src/domain/schemas.js`
- Implement in Task 6: `hermes-local-lab/sources/docx-engine-v2/src/cli/run-job.js`

- [ ] **Step 1: Write failing domain contract tests**

Create `hermes-local-lab/sources/docx-engine-v2/tests/domain-contract.test.js`:

```js
const assert = require('node:assert/strict');
const test = require('node:test');

const { validateDomainObject } = require('../src/domain/validate');

test('DocumentJob requires template id, status, workspace, inputs, outputs, warnings, and failures', () => {
  const result = validateDomainObject('DocumentJob', {
    jobId: 'job-001',
    createdAt: '2026-07-04T00:00:00.000Z',
    sourceRef: { type: 'markdown', path: '/tmp/source.md' },
    templateId: 'general-proposal',
    status: 'created',
    workspace: '/tmp/docx-job',
    inputs: [],
    outputs: [],
    warnings: [],
    failures: [],
  });

  assert.equal(result.ok, true);
});

test('ValidationReport can explicitly preserve WPS verification as not_verified', () => {
  const result = validateDomainObject('ValidationReport', {
    schemaVersion: 'docx-engine-v2/validation-report',
    status: 'passed_with_warnings',
    checks: [
      { id: 'docx_zip', status: 'passed', message: 'DOCX zip is readable' },
      { id: 'wps_visual', status: 'not_verified', message: 'WPS/Word has not been opened in this run' },
    ],
    warnings: ['WPS/Word visual verification is not verified'],
    failures: [],
  });

  assert.equal(result.ok, true);
});

test('DeliveryPackage requires document, source, assets, manifests, render plan, quality report, and image instructions', () => {
  const result = validateDomainObject('DeliveryPackage', {
    schemaVersion: 'docx-engine-v2/delivery-package',
    deliveryDir: '/tmp/delivery',
    files: {
      document: 'document.docx',
      source: 'source.md',
      assetsDir: 'assets',
      jobManifest: 'job.manifest.json',
      templateManifest: 'template.manifest.json',
      renderPlan: 'render-plan.json',
      qualityReport: 'quality-report.json',
      imageInstructions: 'README-图片调整说明.md',
    },
    status: 'delivered',
  });

  assert.equal(result.ok, true);
});
```

- [ ] **Step 2: Write failing run-job contract test**

Create `hermes-local-lab/sources/docx-engine-v2/tests/run-job-contract.test.js`:

```js
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const rootDir = path.resolve(__dirname, '..');

test('run-job returns template_selection_required when template id is missing', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-missing-template-'));
  const sourcePath = path.join(tempDir, 'source.md');
  fs.writeFileSync(sourcePath, '# 标题\n\n正文。', 'utf8');

  const result = spawnSync(process.execPath, [
    path.join(rootDir, 'src/cli/run-job.js'),
    '--source',
    sourcePath,
    '--out-dir',
    path.join(tempDir, 'delivery'),
  ], { cwd: rootDir, encoding: 'utf8' });

  assert.equal(result.status, 2);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, false);
  assert.equal(payload.code, 'template_selection_required');
  assert.deepEqual(payload.templates.map((item) => item.id), ['general-proposal', 'meeting-minutes']);
  assert.equal(fs.existsSync(path.join(tempDir, 'delivery')), false);
});

test('run-job writes the complete delivery package for rich markdown', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-rich-markdown-'));
  const sourcePath = path.join(tempDir, 'source.md');
  const assetDir = path.join(tempDir, 'assets');
  const deliveryDir = path.join(tempDir, 'delivery');
  fs.mkdirSync(assetDir);
  fs.writeFileSync(path.join(assetDir, 'architecture.svg'), '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="630"></svg>\n', 'utf8');
  fs.writeFileSync(sourcePath, [
    '# 太极 Agent 企业知识助手建设方案',
    '',
    '## 一、总体架构',
    '',
    '![系统总体架构](assets/architecture.svg)',
    '',
    '| 序号 | 重点任务 | 责任单位 |',
    '| --- | --- | --- |',
    '| 1 | 梳理知识库 | 办公室 |',
    '',
    '## 二、实施安排',
    '',
    '| 阶段 | 输出 | 验收 |',
    '| --- | --- | --- |',
    '| 启动 | 计划 | 通过 |',
  ].join('\n'));

  const result = spawnSync(process.execPath, [
    path.join(rootDir, 'src/cli/run-job.js'),
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out-dir',
    deliveryDir,
  ], { cwd: rootDir, encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  for (const name of [
    'document.docx',
    'source.md',
    'assets',
    'job.manifest.json',
    'template.manifest.json',
    'render-plan.json',
    'quality-report.json',
    'README-图片调整说明.md',
  ]) {
    assert.equal(fs.existsSync(path.join(deliveryDir, name)), true, `${name} should exist`);
  }
  const report = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'quality-report.json'), 'utf8'));
  assert.ok(['passed', 'passed_with_warnings'].includes(report.status));
  assert.ok(report.checks.some((check) => check.id === 'wps_visual' && check.status === 'not_verified'));
});
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/domain-contract.test.js tests/run-job-contract.test.js
```

Expected:

```text
not ok
Cannot find module '../src/domain/validate'
```

- [ ] **Step 4: Commit RED tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/tests/domain-contract.test.js hermes-local-lab/sources/docx-engine-v2/tests/run-job-contract.test.js
git commit -m "test: add docx engine v2 contract tests"
```

## Task 2: Implement Domain Schemas And Job State

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/package.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/domain/schemas.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/domain/validate.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/domain/document-job.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/domain-contract.test.js`

- [ ] **Step 1: Add package metadata**

Create `hermes-local-lab/sources/docx-engine-v2/package.json`:

```json
{
  "name": "docx-engine-v2",
  "version": "0.1.0",
  "private": true,
  "type": "commonjs",
  "scripts": {
    "test": "node --test tests/*.test.js"
  },
  "dependencies": {
    "ajv": "^8.20.0",
    "carbone": "^3.8.2",
    "yauzl": "^2.10.0",
    "yazl": "^2.5.1"
  }
}
```

- [ ] **Step 2: Install dependencies in the v2 package**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm install
```

Expected:

```text
added
```

Do not stage `node_modules/`. Stage `package-lock.json`.

- [ ] **Step 3: Add schema and validation modules**

Create the schema names and statuses exactly:

```js
const STATUSES = {
  job: ['created', 'source_normalized', 'template_selected', 'assets_packaged', 'render_planned', 'rendered', 'validated', 'delivered', 'failed'],
  check: ['passed', 'passed_with_warnings', 'failed', 'not_verified'],
  delivery: ['delivered', 'failed'],
};

module.exports = { STATUSES, schemas };
```

`schemas` must contain these exported keys: `DocumentJob`, `SourcePackage`, `TemplatePackage`, `AssetPackage`, `RenderPlan`, `ValidationReport`, `DeliveryPackage`.

Create `src/domain/validate.js` with this public API:

```js
function validateDomainObject(schemaName, value) {
  const schema = schemas[schemaName];
  if (!schema) {
    throw new Error(`Unknown schema: ${schemaName}`);
  }
  const validate = validator.compile(schema);
  const ok = validate(value);
  return {
    ok,
    errors: ok ? [] : (validate.errors || []).map((error) => ({
      path: error.instancePath || '/',
      message: error.message || 'invalid value',
      keyword: error.keyword,
    })),
  };
}

module.exports = { validateDomainObject };
```

- [ ] **Step 4: Add job transition helper**

Create `src/domain/document-job.js` with:

```js
function createDocumentJob({ jobId, sourceRef, templateId = '', workspace, inputs = [] }) {
  return {
    jobId,
    createdAt: new Date().toISOString(),
    sourceRef,
    templateId,
    status: 'created',
    workspace,
    inputs,
    outputs: [],
    warnings: [],
    failures: [],
  };
}

function transitionJob(job, status, updates = {}) {
  if (!STATUSES.job.includes(status)) {
    throw new Error(`Invalid job status: ${status}`);
  }
  return { ...job, ...updates, status };
}

module.exports = { createDocumentJob, transitionJob };
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/package.json hermes-local-lab/sources/docx-engine-v2/package-lock.json hermes-local-lab/sources/docx-engine-v2/src/domain hermes-local-lab/sources/docx-engine-v2/tests/domain-contract.test.js
git commit -m "feat: add docx engine v2 domain contracts"
```

## Task 3: Normalize Markdown, Text, And DOCX Sources

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-markdown.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-text.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/source/normalize-docx.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/source-normalization.test.js`

- [ ] **Step 1: Write source normalization tests**

Create tests that assert:

```js
assert.equal(source.schemaVersion, 'docx-engine-v2/source-package');
assert.equal(source.title, '太极 Agent 企业知识助手建设方案');
assert.deepEqual(source.sections.map((section) => section.title), ['一、总体架构', '二、实施安排']);
assert.equal(source.tables.length, 2);
assert.equal(source.figures.length, 1);
assert.equal(source.images[0].caption, '系统总体架构');
assert.equal(source.blocks[0].type, 'heading');
```

Add a plain-text case:

```js
assert.equal(source.sourceType, 'text');
assert.equal(source.tables.length, 0);
assert.equal(source.figures.length, 0);
assert.ok(source.warnings.some((warning) => warning.code === 'rich_content_missing'));
```

Add a DOCX case using a generated zip fixture with `word/document.xml`, `word/_rels/document.xml.rels`, and `word/media/image1.png`; assert `embeddedMedia.length === 1`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/source-normalization.test.js
```

Expected:

```text
not ok
Cannot find module '../src/source/normalize-markdown'
```

- [ ] **Step 3: Implement normalization modules**

Public APIs:

```js
function normalizeMarkdownSource({ sourcePath, markdownText = '' }) {}
function normalizeTextSource({ sourcePath = '', text }) {}
async function normalizeDocxSource({ sourcePath }) {}

module.exports = { normalizeMarkdownSource };
module.exports = { normalizeTextSource };
module.exports = { normalizeDocxSource };
```

Required behavior:

- Markdown headings become ordered `heading` blocks.
- Markdown tables become `table` blocks and `tables[]` with stable ids `tbl-001`.
- Mermaid fences become `mermaid` blocks and figure candidates with stable ids `fig-001`.
- Markdown image references become `image` blocks and `images[]`.
- Plain text returns paragraph blocks only and a `rich_content_missing` warning.
- DOCX extraction reads zip entries, basic paragraph text from `word/document.xml`, `word/media/*` media paths, and `figureId=fig-###` markers from XML text.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/src/source hermes-local-lab/sources/docx-engine-v2/tests/source-normalization.test.js
git commit -m "feat: normalize docx engine source packages"
```

## Task 4: Migrate Template Packages Into V2

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/template-registry.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/general-proposal/manifest.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/general-proposal/schema.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/general-proposal/sample.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/general-proposal/prompt.md`
- Create: `hermes-local-lab/sources/docx-engine-v2/templates/general-proposal/template.docx`
- Create equivalent files for `templates/meeting-minutes/`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/templates/registry.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/templates/validate-template-package.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/template-package.test.js`

- [ ] **Step 1: Copy template package inputs from the current external renderer**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
mkdir -p hermes-local-lab/sources/docx-engine-v2/templates
cp -R /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill/renderer/templates/general-proposal hermes-local-lab/sources/docx-engine-v2/templates/
cp -R /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill/renderer/templates/meeting-minutes hermes-local-lab/sources/docx-engine-v2/templates/
cp /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill/renderer/template-registry.json hermes-local-lab/sources/docx-engine-v2/template-registry.json
```

Expected:

```text
hermes-local-lab/sources/docx-engine-v2/templates/general-proposal/template.docx
hermes-local-lab/sources/docx-engine-v2/templates/meeting-minutes/template.docx
```

- [ ] **Step 2: Write template validation tests**

Assertions:

```js
const templates = listTemplates({ rootDir });
assert.deepEqual(templates.map((item) => item.id), ['general-proposal', 'meeting-minutes']);
for (const template of templates) {
  const result = validateTemplatePackage(template);
  assert.equal(result.ok, true, JSON.stringify(result.errors));
  assert.equal(fs.existsSync(template.templatePath), true);
  assert.equal(fs.existsSync(template.schemaPath), true);
  assert.equal(fs.existsSync(template.samplePath), true);
}
```

- [ ] **Step 3: Implement registry and validator**

Public APIs:

```js
function listTemplates({ rootDir = path.resolve(__dirname, '../..') } = {}) {}
function getTemplatePackage(templateId, options = {}) {}
function validateTemplatePackage(template) {}

module.exports = { listTemplates, getTemplatePackage };
module.exports = { validateTemplatePackage };
```

Validation must confirm:

- `manifest.id` equals registry id.
- `template.docx`, `schema.json`, `sample.json`, and `prompt.md` exist.
- Ajv validates `sample.json` against `schema.json`.
- Manifest exposes `documentTypes`, `capabilities`, `qualityGates`, and `compatibility`.

- [ ] **Step 4: Run tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/template-registry.json hermes-local-lab/sources/docx-engine-v2/templates hermes-local-lab/sources/docx-engine-v2/src/templates hermes-local-lab/sources/docx-engine-v2/tests/template-package.test.js
git commit -m "feat: migrate docx template packages into v2"
```

## Task 5: Package Assets And Build Render Plans

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/src/assets/package-assets.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/planning/build-render-plan.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/asset-package.test.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/render-plan.test.js`

- [ ] **Step 1: Write asset package tests**

Required assertions:

```js
assert.equal(assetPackage.schemaVersion, 'docx-engine-v2/asset-package');
assert.equal(assetPackage.figures[0].figureId, 'fig-001');
assert.equal(assetPackage.tables[0].tableId, 'tbl-001');
assert.equal(fs.existsSync(path.join(workspace, assetPackage.figures[0].displayPath)), true);
assert.equal(fs.existsSync(path.join(workspace, assetPackage.figures[0].editable.sourcePath)), true);
```

Also assert non-empty output directory fails:

```js
assert.throws(() => packageAssets({ sourcePackage, assetDir, outDir }), /输出目录非空/);
```

- [ ] **Step 2: Write render plan tests**

Required assertions:

```js
assert.equal(renderPlan.schemaVersion, 'docx-engine-v2/render-plan');
assert.deepEqual(renderPlan.sections.map((section) => section.title), ['一、总体架构', '二、实施安排']);
assert.equal(renderPlan.figures[0].figureId, 'fig-001');
assert.equal(renderPlan.figures[0].sectionTitle, '一、总体架构');
assert.equal(renderPlan.tables[0].tableId, 'tbl-001');
assert.equal(renderPlan.templateData.images[0].figureId, 'fig-001');
assert.equal(renderPlan.templateData.tables[0].tableId, 'tbl-001');
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node --test tests/asset-package.test.js tests/render-plan.test.js
```

Expected:

```text
not ok
Cannot find module '../src/assets/package-assets'
```

- [ ] **Step 4: Implement asset packaging and render planning**

Public APIs:

```js
function packageAssets({ sourcePackage, assetDir = '', outDir }) {}
function buildRenderPlan({ sourcePackage, templatePackage, assetPackage }) {}

module.exports = { packageAssets };
module.exports = { buildRenderPlan };
```

Required behavior:

- Stable figure ids start at `fig-001`.
- Stable table ids start at `tbl-001`.
- Existing qualified SVG/PNG display files are copied and preserved.
- Mermaid source is written to `source.mmd` and a deterministic SVG is rendered to `figure.svg`.
- Figure and table placement follows source order and section id.
- Missing required figure assets fail before DOCX rendering.

- [ ] **Step 5: Run tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/src/assets hermes-local-lab/sources/docx-engine-v2/src/planning hermes-local-lab/sources/docx-engine-v2/tests/asset-package.test.js hermes-local-lab/sources/docx-engine-v2/tests/render-plan.test.js
git commit -m "feat: build docx asset packages and render plans"
```

## Task 6: Render DOCX And Write Delivery Packages

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/src/rendering/render-docx.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/rendering/postprocess-docx.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/validation/validate-delivery-package.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/delivery/write-delivery-package.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/cli/run-job.js`
- Modify: `hermes-local-lab/sources/docx-engine-v2/tests/run-job-contract.test.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/delivery-validation.test.js`

- [ ] **Step 1: Write delivery validation tests**

Required assertions:

```js
const report = validateDeliveryPackage({ deliveryDir });
assert.ok(['passed', 'passed_with_warnings'].includes(report.status));
assert.ok(report.checks.some((check) => check.id === 'docx_zip' && check.status === 'passed'));
assert.ok(report.checks.some((check) => check.id === 'figure_id_metadata' && check.status === 'passed'));
assert.ok(report.checks.some((check) => check.id === 'wps_visual' && check.status === 'not_verified'));
```

Add failure case:

```js
fs.rmSync(path.join(deliveryDir, 'render-plan.json'));
const report = validateDeliveryPackage({ deliveryDir });
assert.equal(report.status, 'failed');
assert.ok(report.failures.some((item) => item.includes('render-plan.json')));
```

- [ ] **Step 2: Implement rendering and delivery modules**

Public APIs:

```js
async function renderDocx({ templatePackage, renderPlan, outputPath }) {}
async function postprocessDocx({ docxPath, renderPlan, outputPath }) {}
function writeDeliveryPackage({ deliveryDir, job, sourcePackage, templatePackage, assetPackage, renderPlan, documentPath, qualityReport }) {}
function validateDeliveryPackage({ deliveryDir, wpsVisualStatus = 'not_verified' }) {}
```

`quality-report.json` must include these check ids: `schema`, `docx_zip`, `template_markers`, `image_coverage`, `table_coverage`, `figure_id_metadata`, `delivery_files`, `wps_visual`.

- [ ] **Step 3: Implement `run-job.js`**

CLI arguments:

```text
--template-id <id>
--source <path>
--source-type <markdown|text|docx>
--asset-dir <path>
--out-dir <path>
--json
```

Exit codes:

```text
0 success
2 template_selection_required
3 validation_failed
4 render_failed
```

Successful stdout shape:

```json
{
  "ok": true,
  "jobId": "job-...",
  "deliveryDir": "/tmp/delivery",
  "documentPath": "/tmp/delivery/document.docx",
  "qualityStatus": "passed_with_warnings"
}
```

- [ ] **Step 4: Run end-to-end engine tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 5: Manually run the first v2 delivery package**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node src/cli/run-job.js --template-id general-proposal --source tests/fixtures/rich-proposal.md --asset-dir tests/fixtures/assets --out-dir /tmp/docx-engine-v2-delivery --json
find /tmp/docx-engine-v2-delivery -maxdepth 2 -type f | sort
```

Expected:

```text
/tmp/docx-engine-v2-delivery/README-图片调整说明.md
/tmp/docx-engine-v2-delivery/document.docx
/tmp/docx-engine-v2-delivery/job.manifest.json
/tmp/docx-engine-v2-delivery/quality-report.json
/tmp/docx-engine-v2-delivery/render-plan.json
/tmp/docx-engine-v2-delivery/source.md
/tmp/docx-engine-v2-delivery/template.manifest.json
```

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/src/rendering hermes-local-lab/sources/docx-engine-v2/src/validation hermes-local-lab/sources/docx-engine-v2/src/delivery hermes-local-lab/sources/docx-engine-v2/src/cli/run-job.js hermes-local-lab/sources/docx-engine-v2/tests
git commit -m "feat: render docx engine v2 delivery packages"
```

## Task 7: Move Figure Replacement Into V2

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/src/assets/replace-docx-asset.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/src/cli/replace-asset.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/replace-asset.test.js`

- [ ] **Step 1: Write replacement tests**

Required assertions:

```js
assert.equal(result.figureId, 'fig-001');
assert.equal(fs.existsSync(replacedPath), true);
assert.match(documentXml, /figureId=fig-001/);
assert.match(replacementSvg, /REPLACEMENT_FIGURE_MARKER/);
```

Add safety failure:

```js
const result = spawnSync(process.execPath, [
  'src/cli/replace-asset.js',
  '--docx', oldDocxWithoutFigureId,
  '--figure-id', 'fig-001',
  '--image', replacementPath,
  '--out', replacedPath,
], { cwd: rootDir, encoding: 'utf8' });
assert.equal(result.status, 3);
assert.match(result.stderr, /未在 DOCX 中找到图片标识/);
```

- [ ] **Step 2: Implement v2 replacement**

Public APIs:

```js
async function replaceDocxAsset({ docxPath, figureId, imagePath, outputPath }) {}
```

The implementation may reuse the current algorithm from `/Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill/scripts/replace-docx-image.js`, but the v2 source file becomes the maintained copy.

- [ ] **Step 3: Run tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 4: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/src/assets/replace-docx-asset.js hermes-local-lab/sources/docx-engine-v2/src/cli/replace-asset.js hermes-local-lab/sources/docx-engine-v2/tests/replace-asset.test.js
git commit -m "feat: replace docx assets by stable figure id"
```

## Task 8: Build The Compatibility Skill Shell From V2

**Files:**
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/SKILL.md`
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/skill.json`
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/scripts/apply-template.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/scripts/self-test.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/scripts/package-rich-draft.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/scripts/render-figure-assets.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/compat/docx-template-skill/scripts/replace-docx-image.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/scripts/build-copyable-skill.js`
- Create: `hermes-local-lab/sources/docx-engine-v2/tests/compat-skill.test.js`

- [ ] **Step 1: Write compatibility tests**

Required assertions:

```js
assert.equal(fs.existsSync(path.join(outDir, 'SKILL.md')), true);
assert.equal(fs.existsSync(path.join(outDir, 'skill.json')), true);
assert.equal(fs.existsSync(path.join(outDir, 'scripts/apply-template.js')), true);
assert.equal(fs.existsSync(path.join(outDir, 'templates/general-proposal/template.docx')), true);
assert.equal(fs.existsSync(path.join(outDir, 'runtime')), false);
```

Wrapper behavior:

```js
const result = spawnSync(process.execPath, [
  path.join(outDir, 'scripts/apply-template.js'),
  '--template-id', 'general-proposal',
  '--source', sourcePath,
  '--out-dir', deliveryDir,
], { encoding: 'utf8' });
assert.equal(result.status, 0, result.stderr || result.stdout);
assert.equal(fs.existsSync(path.join(deliveryDir, 'document.docx')), true);
```

- [ ] **Step 2: Implement shell wrappers**

Each wrapper must call the v2 CLI and avoid owning business logic.

Example `scripts/apply-template.js`:

```js
#!/usr/bin/env node
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const packageDir = path.resolve(__dirname, '..');
const engineCli = path.join(packageDir, 'engine', 'src', 'cli', 'run-job.js');
const result = spawnSync(process.execPath, [engineCli, ...process.argv.slice(2)], {
  cwd: packageDir,
  encoding: 'utf8',
  stdio: 'inherit',
});
process.exitCode = result.status || 0;
```

- [ ] **Step 3: Implement package builder**

`scripts/build-copyable-skill.js` arguments:

```text
--out-dir <path>
```

It must copy:

- `compat/docx-template-skill/SKILL.md`
- `compat/docx-template-skill/skill.json`
- `compat/docx-template-skill/scripts/`
- `src/` into `engine/src/`
- `templates/` into `engine/templates/`
- `template-registry.json` into `engine/template-registry.json`
- `package.json` and `package-lock.json` into `engine/`

- [ ] **Step 4: Run compatibility tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/docx-engine-v2/compat hermes-local-lab/sources/docx-engine-v2/scripts/build-copyable-skill.js hermes-local-lab/sources/docx-engine-v2/tests/compat-skill.test.js
git commit -m "feat: build docx template skill from v2"
```

## Task 9: Connect Taiji Backend To V2

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/api/docx_engine_v2.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py`

- [ ] **Step 1: Write backend route tests**

Create tests for:

```python
def test_docx_engine_v2_lists_templates(monkeypatch):
    ...
    assert payload["templates"][0]["id"] == "general-proposal"

def test_docx_engine_v2_create_job_requires_template_selection(monkeypatch, tmp_path):
    ...
    assert payload["code"] == "template_selection_required"

def test_docx_engine_v2_create_job_returns_delivery_package(monkeypatch, tmp_path):
    ...
    assert payload["document_path"].endswith("document.docx")
    assert payload["quality_status"] in {"passed", "passed_with_warnings"}

def test_docx_engine_v2_replace_asset_rejects_bad_figure_id(monkeypatch, tmp_path):
    ...
    assert response_status == 400
```

- [ ] **Step 2: Implement `api/docx_engine_v2.py`**

Public API:

```python
def engine_root() -> Path: ...
def run_engine(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]: ...
def list_templates() -> dict: ...
def create_job(payload: dict, workspace: Path) -> tuple[dict, int]: ...
def replace_asset(payload: dict, workspace: Path) -> tuple[dict, int]: ...
```

Rules:

- Resolve user paths inside the session workspace unless the current figure-adjustment allowlist explicitly permits an absolute path.
- Return parsed JSON for success and known validation failures.
- Preserve raw stderr only in server logs or developer-safe fields, not in ordinary user-facing text.

- [ ] **Step 3: Add routes**

Add route handlers in `api/routes.py`:

```text
GET  /api/docx-engine-v2/templates
POST /api/docx-engine-v2/jobs
POST /api/docx-engine-v2/assets/replace
```

Do not remove current `/api/docx-template/figure-adjust/*` routes in this task. Keep them until the UI calls v2 and focused tests pass.

- [ ] **Step 4: Run backend tests**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
uv run --with pytest python -m pytest tests/test_docx_engine_v2_routes.py tests/test_docx_template_backend_router.py tests/test_rich_draft_chat_routing.py -q
python3 -m py_compile api/routes.py api/docx_engine_v2.py
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/hermes-webui/api/docx_engine_v2.py hermes-local-lab/sources/hermes-webui/api/routes.py hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_routes.py
git commit -m "feat: connect webui backend to docx engine v2"
```

## Task 10: Add Visible Document Workbench UI

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/static/ui.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/messages.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/style.css`
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_ui_contract.py`

- [ ] **Step 1: Write UI contract tests**

Required source assertions:

```python
assert "docx-engine-workbench" in ui_js
assert "renderDocxEngineWorkbench" in ui_js
assert "runDocxEngineJob" in ui_js
assert "openDocxDeliveryFolder" in ui_js
assert "replaceDocxEngineAsset" in ui_js
assert "质量报告" in ui_js
assert "打开 DOCX" in ui_js
assert "打开交付目录" in ui_js
assert "aria-label" in ui_js
assert ".docx-engine-workbench" in style_css
```

Do not accept code-only capability without a visible entrypoint.

- [ ] **Step 2: Add UI workbench states**

The workbench must show:

- Template selector.
- Source path input.
- Generate package action.
- Quality report summary.
- Open DOCX action.
- Open delivery directory action.
- Figure id input.
- Replacement image input.
- Replace image action.
- Old DOCX recovery message when `figureId` metadata is missing.

- [ ] **Step 3: Add accessible controls**

Each icon or compact button must have either visible text or `aria-label`.

Required control names:

```text
选择模板
生成文档包
查看质量报告
打开 DOCX
打开交付目录
重渲染图片
替换 DOCX 图片
从源包重新生成
```

- [ ] **Step 4: Run focused UI tests and lint**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
uv run --with pytest python -m pytest tests/test_docx_engine_v2_ui_contract.py tests/test_docx_template_skill_invocation.py -q
npm run lint:runtime
```

Expected:

```text
passed
```

`npm run lint:runtime` must exit `0`.

- [ ] **Step 5: Real browser QA**

Start or reuse the WebUI server, then verify in a real browser:

```text
Open chat
Send a template request
See template selection
Choose template
Generate package
See quality report
See Open DOCX and Open delivery directory controls
Use keyboard Tab through all controls
Capture a screenshot for the document workbench
```

If Playwright or the desktop shell is unavailable, record `真实浏览器测试：未验证` in the frontend UX QA report and keep final status below "完成".

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git add hermes-local-lab/sources/hermes-webui/static/ui.js hermes-local-lab/sources/hermes-webui/static/messages.js hermes-local-lab/sources/hermes-webui/static/style.css hermes-local-lab/sources/hermes-webui/tests/test_docx_engine_v2_ui_contract.py
git commit -m "feat: add docx engine workbench UI"
```

## Task 11: Build, Sync, And Verify Runtime Skill

**Files:**
- Modify generated output only: `/Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill`
- Modify generated output only: `/Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill.zip`
- Modify runtime output only: `/Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill`

- [ ] **Step 1: Build copyable skill from v2**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
node scripts/build-copyable-skill.js --out-dir /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill
```

Expected:

```text
build-copyable-skill-ok
```

- [ ] **Step 2: Recreate zip**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎
rm -f docx-template-skill.zip
zip -qr docx-template-skill.zip docx-template-skill -x '**/.DS_Store' '**/node_modules/**'
unzip -tq docx-template-skill.zip
```

Expected:

```text
No errors detected
```

- [ ] **Step 3: Sync runtime skill**

Run:

```bash
rsync -a --delete /Users/bwb/Documents/工作/文档模板渲染引擎/docx-template-skill/ /Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill/
node /Users/bwb/.local/share/taiji-agent/runtime-home/skills/productivity/docx-template-skill/scripts/self-test.js --out-dir /tmp/docx-engine-v2-runtime-self-test
```

Expected:

```text
self-test-ok
```

- [ ] **Step 4: Re-run root and external package tests**

Run:

```bash
cd /Users/bwb/Documents/工作/文档模板渲染引擎
node --test tests/*.test.js
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
```

Expected:

```text
pass
```

- [ ] **Step 5: Record generated-output status**

No root commit is expected for generated external output unless a tracked source file changed in this task. In the final task report, list these generated paths separately from git-tracked source.

## Task 12: Full Verification And Frontend UX QA Report

**Files:**
- Read/verify only unless a test exposes a defect

- [ ] **Step 1: Run all focused automated checks**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
npm test
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui
uv run --with pytest python -m pytest tests/test_docx_engine_v2_routes.py tests/test_docx_engine_v2_ui_contract.py tests/test_docx_template_backend_router.py tests/test_docx_template_skill_invocation.py tests/test_rich_draft_chat_routing.py -q
python3 -m py_compile api/routes.py api/docx_engine_v2.py
npm run lint:runtime
```

Expected:

```text
pass
```

- [ ] **Step 2: Run real delivery package check**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/docx-engine-v2
rm -rf /tmp/docx-engine-v2-final-delivery
node src/cli/run-job.js --template-id general-proposal --source tests/fixtures/rich-proposal.md --asset-dir tests/fixtures/assets --out-dir /tmp/docx-engine-v2-final-delivery --json
node src/cli/replace-asset.js --docx /tmp/docx-engine-v2-final-delivery/document.docx --figure-id fig-001 --image /tmp/docx-engine-v2-final-delivery/assets/fig-001-系统总体架构/figure.svg --out /tmp/docx-engine-v2-final-delivery/document-figure-replaced.docx
```

Expected:

```text
"ok": true
replace-docx-asset-ok
```

- [ ] **Step 3: WPS/Word visual verification**

Open:

```text
/tmp/docx-engine-v2-final-delivery/document.docx
/tmp/docx-engine-v2-final-delivery/document-figure-replaced.docx
```

Verify:

- Cover/title page opens.
- Body headings are visible.
- Tables are visible near their source sections.
- Figure appears near its source section.
- The replacement DOCX opens without repair prompt.
- WPS/Word visual state is recorded in `quality-report.json` or final report as current evidence.

- [ ] **Step 4: Output Chinese Frontend UX QA Report**

Report status rules:

- If browser and screenshot checks pass and no P0/P1 remains: `状态：完成`.
- If browser checks cannot run: `状态：带限制完成` or `未完成`, with `真实浏览器测试：未验证`.
- If any backend v2 capability lacks a visible UI entrypoint: `状态：未完成` and record P1.

Required report sections:

```text
《前端 UX QA 报告》
状态
变更范围
主要用户目标
主内容 / 辅助内容 / 高级内容
已测试的主要用户路径
功能契约摘要
浏览器测试证据
截图情况
可访问性检查
视觉层级检查
长时间工作体验检查
空/加载/错误/成功/禁用/破坏性状态检查
自动化检查运行结果
P0/P1/P2/P3 问题列表
已修复问题
剩余风险
未验证项目
后续建议
```

- [ ] **Step 5: Final git status and commit policy**

Run:

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
git status --short
```

Expected for tracked files touched by this plan:

```text
clean for committed v2 implementation files
```

Pre-existing unrelated dirty paths may remain. List them separately and do not stage them.

## Self-Review Checklist

- Spec coverage:
  - Delivery package contract: Tasks 1, 6, 12.
  - Core domain models: Tasks 1, 2.
  - Source normalization: Task 3.
  - Template package validation: Task 4.
  - Asset lifecycle and `figureId`: Tasks 5, 7.
  - RenderPlan: Task 5.
  - Quality report: Tasks 6, 12.
  - Compatibility shell: Tasks 8, 11.
  - Taiji backend and visible UI: Tasks 9, 10, 12.
  - Runtime skill self-test and WPS/Word verification: Tasks 11, 12.
- Red-flag scan:
  - Run the writing-plans red-flag scan against this file before execution.
  - Expected output: no matches.
- Type consistency:
  - CLI paths use `src/cli/run-job.js`, `src/cli/replace-asset.js`, and `src/cli/list-templates.js`.
  - Schema names use `DocumentJob`, `SourcePackage`, `TemplatePackage`, `AssetPackage`, `RenderPlan`, `ValidationReport`, `DeliveryPackage`.
  - Status values use `passed`, `passed_with_warnings`, `failed`, and `not_verified`.

## Execution Handoff

Plan execution should use subagent-driven development unless the user explicitly prefers inline execution. Each implementation task should end with a focused test run and a local commit before moving to the next task.
