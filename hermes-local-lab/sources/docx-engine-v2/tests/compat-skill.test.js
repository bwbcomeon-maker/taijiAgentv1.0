const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const yauzl = require('yauzl');

const rootDir = path.join(__dirname, '..');
const BUILD_SKILL = path.join(rootDir, 'scripts', 'build-copyable-skill.js');
const MATERIALIZE_RESVG = path.join(rootDir, 'scripts', 'materialize-portable-resvg-dependencies.js');
const { promoteOutput } = require('../scripts/build-copyable-skill');
const WPS_EVIDENCE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64'
);

function makeWorkspace(t) {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-compat-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));
  return workspace;
}

function writeRichSource(workspace) {
  const assetDir = path.join(workspace, 'assets');
  fs.mkdirSync(assetDir, { recursive: true });
  fs.writeFileSync(
    path.join(assetDir, 'architecture.svg'),
    '<svg xmlns="http://www.w3.org/2000/svg"><text>ARCHITECTURE_SOURCE</text></svg>\n',
    'utf8'
  );

  const sourcePath = path.join(workspace, 'source.md');
  fs.writeFileSync(
    sourcePath,
    [
      '# Enterprise AI rollout proposal',
      '',
      '## Architecture',
      '',
      '| Component | Role |',
      '| --- | --- |',
      '| Source package | Normalizes Markdown |',
      '| Render plan | Binds template data |',
      '',
      '```mermaid',
      'flowchart LR',
      '  A[Source] --> B[Delivery]',
      '```',
      '',
      '![Architecture](architecture.svg)',
      '',
    ].join('\n'),
    'utf8'
  );

  return { assetDir, sourcePath };
}

function buildSkillPackage(t) {
  const workspace = makeWorkspace(t);
  const outDir = path.join(workspace, 'docx-template-skill');
  const result = spawnSync(process.execPath, [BUILD_SKILL, '--out-dir', outDir], {
    cwd: rootDir,
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /build-copyable-skill-ok/);
  return { workspace, outDir };
}

function recordWpsVisualAcceptance({ outDir, workspace, deliveryDir, evidenceName }) {
  const evidencePath = path.join(workspace, evidenceName);
  fs.writeFileSync(evidencePath, WPS_EVIDENCE_PNG);
  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/record-wps-visual.js'),
    '--delivery-dir',
    deliveryDir,
    '--status',
    'passed',
    '--reviewer',
    'test',
    '--visual-check',
    'document_opened',
    '--visual-check',
    'layout_reviewed',
    '--visual-check',
    'content_order_reviewed',
    '--visual-check',
    'figures_reviewed',
    '--visual-check',
    'tables_reviewed',
    '--evidence-file',
    evidencePath,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  return payload;
}

test('build-copyable-skill writes a v2-backed skill package without runtime leftovers', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);

  assert.equal(fs.existsSync(path.join(outDir, 'SKILL.md')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'skill.json')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'README.md')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'skill-invocation-contract.md')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/apply-template.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/self-test.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/package-rich-draft.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/render-figure-assets.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/replace-docx-image.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/install-template.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/run-job.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/install-template.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/list-templates.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/replace-asset.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/record-wps-visual.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/validate-delivery.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/replay-delivery.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/validate-template.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/scaffold-template.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'scripts/render-template-sample.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/record-wps-visual.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/validate-delivery.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/replay-delivery.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/validate-template.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/scaffold-template.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/src/cli/render-template-sample.js')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/README.md')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'engine/templates/general-proposal/template.docx')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'runtime')), false);
  assert.equal(
    fs.readdirSync(workspace).some((name) => name.startsWith('.docx-template-skill.')),
    false,
    'atomic build staging directories must be cleaned'
  );
  for (const packageName of [
    'resvg-js',
    'resvg-js-linux-x64-gnu',
    'resvg-js-linux-x64-musl',
    'resvg-js-linux-arm64-gnu',
    'resvg-js-linux-arm64-musl',
  ]) {
    const packageDir = path.join(outDir, 'engine/node_modules/@resvg', packageName);
    assert.equal(fs.existsSync(path.join(packageDir, 'package.json')), true, packageName);
    if (packageName !== 'resvg-js') {
      assert.equal(
        fs.readdirSync(packageDir).some((name) => name.endsWith('.node')),
        true,
        `${packageName} native binary`
      );
    }
  }

  const readme = fs.readFileSync(path.join(outDir, 'README.md'), 'utf8');
  const contract = fs.readFileSync(path.join(outDir, 'skill-invocation-contract.md'), 'utf8');
  const engineReadme = fs.readFileSync(path.join(outDir, 'engine/README.md'), 'utf8');
  assert.match(readme, /node scripts\/self-test\.js --out-dir/);
  assert.match(readme, /Do not edit engine\/template-registry\.json by hand/);
  assert.match(contract, /template_selection_required/);
  assert.match(contract, /template-install-report\.json/);
  assert.match(contract, /quality-report\.json/);
  assert.match(contract, /record-wps-visual/);
  assert.match(engineReadme, /node src\/cli\/run-job\.js/);
  assert.match(engineReadme, /node src\/cli\/install-template\.js/);
});

test('build-copyable-skill preserves an existing output when build inputs are invalid', (t) => {
  const workspace = makeWorkspace(t);
  const brokenRoot = path.join(workspace, 'broken-engine');
  const scriptsDir = path.join(brokenRoot, 'scripts');
  const outDir = path.join(workspace, 'existing-skill');
  fs.mkdirSync(scriptsDir, { recursive: true });
  fs.mkdirSync(outDir, { recursive: true });
  fs.copyFileSync(BUILD_SKILL, path.join(scriptsDir, 'build-copyable-skill.js'));
  fs.copyFileSync(MATERIALIZE_RESVG, path.join(scriptsDir, 'materialize-portable-resvg-dependencies.js'));
  fs.writeFileSync(path.join(outDir, 'sentinel.txt'), 'keep existing output');

  const result = spawnSync(
    process.execPath,
    [path.join(scriptsDir, 'build-copyable-skill.js'), '--out-dir', outDir],
    { cwd: brokenRoot, encoding: 'utf8' }
  );

  assert.notEqual(result.status, 0, result.stdout + result.stderr);
  assert.equal(fs.readFileSync(path.join(outDir, 'sentinel.txt'), 'utf8'), 'keep existing output');
});

test('promoteOutput keeps the committed output successful when old backup cleanup fails', (t) => {
  const workspace = makeWorkspace(t);
  const outDir = path.join(workspace, 'skill');
  const stagingDir = path.join(workspace, '.skill.tmp-test');
  fs.mkdirSync(outDir);
  fs.mkdirSync(stagingDir);
  fs.writeFileSync(path.join(outDir, 'old.txt'), 'old');
  fs.writeFileSync(path.join(stagingDir, 'new.txt'), 'new');
  const warnings = [];

  promoteOutput(stagingDir, outDir, {
    remove(candidate) {
      if (candidate.includes('.skill.backup-') && fs.existsSync(path.join(candidate, 'old.txt'))) {
        throw new Error('simulated backup cleanup failure');
      }
      fs.rmSync(candidate, { recursive: true, force: true });
    },
    warn(message) {
      warnings.push(message);
    },
  });

  assert.equal(fs.readFileSync(path.join(outDir, 'new.txt'), 'utf8'), 'new');
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /新产物已生效.*旧备份清理失败/);
});

test('copyable apply-template wrapper renders a delivery package through v2', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const { assetDir, sourcePath } = writeRichSource(workspace);
  const deliveryDir = path.join(workspace, 'delivery');

  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/apply-template.js'),
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out-dir',
    deliveryDir,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'document.docx')), true);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'render-plan.json')), true);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'quality-report.json')), true);

  recordWpsVisualAcceptance({
    outDir,
    workspace,
    deliveryDir,
    evidenceName: 'apply-template-wps-evidence.png',
  });

  const validateResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/validate-delivery.js'),
    '--delivery-dir',
    deliveryDir,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(validateResult.status, 0, validateResult.stderr || validateResult.stdout);
  const validationPayload = JSON.parse(validateResult.stdout);
  assert.equal(validationPayload.ok, true);
  assert.ok(['passed', 'passed_with_warnings'].includes(validationPayload.qualityReport.status));

  const replayResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/replay-delivery.js'),
    '--delivery-dir',
    deliveryDir,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(replayResult.status, 0, replayResult.stderr || replayResult.stdout);
  const replayPayload = JSON.parse(replayResult.stdout);
  assert.equal(replayPayload.ok, true);
  assert.ok(
    replayPayload.replayReport.checks.some(
      (check) => check.id === 'render_plan_replay' && check.status === 'passed'
    )
  );
});

test('copyable apply-template wrapper keeps the legacy --out docx path working', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const { assetDir, sourcePath } = writeRichSource(workspace);
  const outputDocx = path.join(workspace, 'legacy-output.docx');

  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/apply-template.js'),
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out',
    outputDocx,
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(fs.existsSync(outputDocx), true);
});

test('copyable package-rich-draft wrapper creates editable draft package through v2', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const { assetDir, sourcePath } = writeRichSource(workspace);
  const packageDir = path.join(workspace, 'rich-draft-package');

  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/package-rich-draft.js'),
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out-dir',
    packageDir,
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /package-rich-draft-ok/);
  const manifest = JSON.parse(fs.readFileSync(path.join(packageDir, 'draft.manifest.json'), 'utf8'));
  assert.equal(manifest.schemaVersion, 'rich-draft-package/v2');
  assert.equal(fs.existsSync(path.join(packageDir, '图片清单.md')), true);
  assert.ok(manifest.figures.some((figure) => figure.editable?.sourcePath));
});

test('copyable replace-docx-image wrapper delegates stable figure replacement to v2', async (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const { assetDir, sourcePath } = writeRichSource(workspace);
  const deliveryDir = path.join(workspace, 'delivery-for-replace');
  const replacedPath = path.join(workspace, 'replaced.docx');
  const replacementPath = path.join(workspace, 'replacement.svg');
  fs.writeFileSync(
    replacementPath,
    '<svg xmlns="http://www.w3.org/2000/svg"><text>COMPAT_REPLACEMENT_MARKER</text></svg>\n',
    'utf8'
  );

  const renderResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/apply-template.js'),
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out-dir',
    deliveryDir,
  ], { encoding: 'utf8' });
  assert.equal(renderResult.status, 0, renderResult.stderr || renderResult.stdout);

  const replaceResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/replace-docx-image.js'),
    '--docx',
    path.join(deliveryDir, 'document.docx'),
    '--figure-id',
    'fig-001',
    '--image',
    replacementPath,
    '--out',
    replacedPath,
  ], { encoding: 'utf8' });

  assert.equal(replaceResult.status, 0, replaceResult.stderr || replaceResult.stdout);
  const entries = await readZipEntries(replacedPath);
  assert.match(entries.get('word/media/fig-001.svg').toString('utf8'), /COMPAT_REPLACEMENT_MARKER/);
});

test('copyable render-figure-assets wrapper keeps a delivery package internally consistent', async (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const { assetDir, sourcePath } = writeRichSource(workspace);
  const deliveryDir = path.join(workspace, 'delivery-for-rerender');

  const renderResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/apply-template.js'),
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out-dir',
    deliveryDir,
  ], { encoding: 'utf8' });
  assert.equal(renderResult.status, 0, renderResult.stderr || renderResult.stdout);

  const sourceMmdPath = path.join(deliveryDir, 'assets/fig-001/source.mmd');
  fs.writeFileSync(
    sourceMmdPath,
    ['flowchart LR', '  A[Source] --> B[PACKAGE_RERENDER_MARKER]', ''].join('\n'),
    'utf8'
  );

  const rerenderResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/render-figure-assets.js'),
    '--manifest',
    path.join(deliveryDir, 'render-plan.json'),
    '--figure-id',
    'fig-001',
  ], { encoding: 'utf8' });

  assert.equal(rerenderResult.status, 0, rerenderResult.stderr || rerenderResult.stdout);
  assert.match(rerenderResult.stdout, /render-figure-assets-ok/);

  const displayPath = path.join(deliveryDir, 'assets/fig-001/figure.png');
  const vectorPath = path.join(deliveryDir, 'assets/fig-001/figure.svg');
  const displayHash = sha256File(displayPath);
  const sourceHash = sha256File(sourceMmdPath);
  const assetPackage = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'asset-package.json'), 'utf8'));
  const renderPlan = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'render-plan.json'), 'utf8'));
  const deliveryPackage = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));
  const figure = assetPackage.figures.find((item) => item.figureId === 'fig-001');
  const image = renderPlan.templateData.images.find((item) => item.figureId === 'fig-001');

  assert.equal(figure.sha256, displayHash);
  assert.equal(figure.editable.sourceSha256, sourceHash);
  assert.equal(image.sha256, displayHash);
  assert.equal(fs.existsSync(vectorPath), true);
  assert.equal(deliveryPackage.fileSha256.assetPackage, sha256File(path.join(deliveryDir, 'asset-package.json')));
  assert.equal(deliveryPackage.fileSha256.renderPlan, sha256File(path.join(deliveryDir, 'render-plan.json')));
  assert.equal(deliveryPackage.fileSha256.document, sha256File(path.join(deliveryDir, 'document.docx')));
  assert.equal(deliveryPackage.files.replayReport, undefined);
  assert.equal(deliveryPackage.fileSha256.replayReport, undefined);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'replay-report.json')), false);

  const qualityReport = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'quality-report.json'), 'utf8'));
  assert.equal(qualityReport.checks.find((check) => check.id === 'image_coverage')?.status, 'passed');
  assert.equal(qualityReport.checks.find((check) => check.id === 'wps_visual')?.status, 'not_verified');

  const entries = await readZipEntries(path.join(deliveryDir, 'document.docx'));
  assert.equal(entries.has('word/media/fig-001.png'), true);
  assert.match(fs.readFileSync(vectorPath, 'utf8'), /PACKAGE_RERENDER_MARKER/);
});

test('copyable replay-delivery wrapper can rebind replay evidence after package rerender', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const { assetDir, sourcePath } = writeRichSource(workspace);
  const deliveryDir = path.join(workspace, 'delivery-for-replay-rebind');

  const renderResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/apply-template.js'),
    '--template-id',
    'general-proposal',
    '--source',
    sourcePath,
    '--asset-dir',
    assetDir,
    '--out-dir',
    deliveryDir,
  ], { encoding: 'utf8' });
  assert.equal(renderResult.status, 0, renderResult.stderr || renderResult.stdout);

  fs.writeFileSync(
    path.join(deliveryDir, 'assets/fig-001/source.mmd'),
    ['flowchart LR', '  A[Source] --> B[REBOUND_REPLAY_MARKER]', ''].join('\n'),
    'utf8'
  );

  const rerenderResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/render-figure-assets.js'),
    '--manifest',
    path.join(deliveryDir, 'render-plan.json'),
    '--figure-id',
    'fig-001',
  ], { encoding: 'utf8' });
  assert.equal(rerenderResult.status, 0, rerenderResult.stderr || rerenderResult.stdout);

  const replayResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/replay-delivery.js'),
    '--delivery-dir',
    deliveryDir,
    '--write-report',
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(replayResult.status, 0, replayResult.stderr || replayResult.stdout);
  const replayPayload = JSON.parse(replayResult.stdout);
  assert.equal(replayPayload.ok, true);
  assert.equal(fs.existsSync(path.join(deliveryDir, 'replay-report.json')), true);

  const deliveryPackage = JSON.parse(fs.readFileSync(path.join(deliveryDir, 'delivery-package.json'), 'utf8'));
  assert.equal(deliveryPackage.files.replayReport, 'replay-report.json');
  assert.equal(deliveryPackage.fileSha256.replayReport, sha256File(path.join(deliveryDir, 'replay-report.json')));

  recordWpsVisualAcceptance({
    outDir,
    workspace,
    deliveryDir,
    evidenceName: 'replay-rebind-wps-evidence.png',
  });

  const validateResult = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/validate-delivery.js'),
    '--delivery-dir',
    deliveryDir,
    '--write-report',
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(validateResult.status, 0, validateResult.stderr || validateResult.stdout);
  const validationPayload = JSON.parse(validateResult.stdout);
  assert.equal(validationPayload.ok, true);
  assert.ok(['passed', 'passed_with_warnings'].includes(validationPayload.qualityReport.status));
  assert.equal(
    validationPayload.qualityReport.checks.find((check) => check.id === 'replay_report')?.status,
    'passed'
  );
});

test('copyable list-templates cli exposes migrated templates', (t) => {
  const { outDir } = buildSkillPackage(t);
  const result = spawnSync(process.execPath, [
    path.join(outDir, 'engine/src/cli/list-templates.js'),
    '--json',
  ], { cwd: outDir, encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.ok(payload.templates.some((template) => template.id === 'general-proposal'));
});

test('copyable install-template wrapper installs a validated template package into engine registry', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const packageDir = path.join(workspace, 'custom-proposal');
  fs.cpSync(path.join(rootDir, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: 'custom-proposal', name: 'Custom Proposal' }, null, 2)}\n`,
    'utf8'
  );

  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/install-template.js'),
    '--package',
    packageDir,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'custom-proposal');
  assert.equal(fs.existsSync(path.join(outDir, 'engine', 'installed', 'custom-proposal', 'template.docx')), true);

  const templates = spawnSync(process.execPath, [
    path.join(outDir, 'engine/src/cli/list-templates.js'),
    '--json',
  ], { cwd: outDir, encoding: 'utf8' });
  assert.equal(templates.status, 0, templates.stderr || templates.stdout);
  assert.ok(JSON.parse(templates.stdout).templates.some((template) => template.id === 'custom-proposal'));
});

test('copyable validate-template wrapper validates a template package without installing it', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const packageDir = path.join(workspace, 'validated-proposal');
  fs.cpSync(path.join(rootDir, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: 'validated-proposal', name: 'Validated Proposal' }, null, 2)}\n`,
    'utf8'
  );

  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/validate-template.js'),
    '--package',
    packageDir,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'validated-proposal');
  assert.equal(fs.existsSync(path.join(outDir, 'engine', 'installed', 'validated-proposal')), false);
});

test('copyable scaffold-template wrapper creates a valid template package', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const packageDir = path.join(workspace, 'scaffolded-proposal');

  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/scaffold-template.js'),
    '--from',
    'general-proposal',
    '--template-id',
    'scaffolded-proposal',
    '--name',
    'Scaffolded Proposal',
    '--out-dir',
    packageDir,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'scaffolded-proposal');

  const validation = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/validate-template.js'),
    '--package',
    packageDir,
    '--json',
  ], { encoding: 'utf8' });
  assert.equal(validation.status, 0, validation.stderr || validation.stdout);
  assert.equal(JSON.parse(validation.stdout).ok, true);
});

test('copyable render-template-sample wrapper renders an uninstalled template package', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const packageDir = path.join(workspace, 'sample-render-proposal');
  const smokeDir = path.join(workspace, 'template-smoke-output');

  const scaffold = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/scaffold-template.js'),
    '--from',
    'general-proposal',
    '--template-id',
    'sample-render-proposal',
    '--name',
    'Sample Render Proposal',
    '--out-dir',
    packageDir,
    '--json',
  ], { encoding: 'utf8' });
  assert.equal(scaffold.status, 0, scaffold.stderr || scaffold.stdout);

  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/render-template-sample.js'),
    '--package',
    packageDir,
    '--out-dir',
    smokeDir,
    '--json',
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.equal(payload.templateId, 'sample-render-proposal');
  assert.equal(fs.existsSync(path.join(smokeDir, 'sample.docx')), true);
  assert.equal(fs.existsSync(path.join(smokeDir, 'template-smoke-report.json')), true);
});

test('copyable self-test renders both smoke documents with template-appropriate sources', (t) => {
  const { workspace, outDir } = buildSkillPackage(t);
  const smokeDir = path.join(workspace, 'self-test-output');
  const result = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/self-test.js'),
    '--out-dir',
    smokeDir,
  ], { encoding: 'utf8' });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /self-test-ok/);
  assert.equal(fs.existsSync(path.join(smokeDir, 'general-proposal.docx')), true);
  assert.equal(fs.existsSync(path.join(smokeDir, 'meeting-minutes.docx')), true);

  const repeat = spawnSync(process.execPath, [
    path.join(outDir, 'scripts/self-test.js'),
    '--out-dir',
    smokeDir,
  ], { encoding: 'utf8' });
  assert.equal(repeat.status, 0, repeat.stderr || repeat.stdout);
});

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function readZipEntries(docxPath) {
  return new Promise((resolve, reject) => {
    yauzl.open(docxPath, { lazyEntries: true }, (openError, zipfile) => {
      if (openError) {
        reject(openError);
        return;
      }

      const entries = new Map();
      let settled = false;

      const fail = (error) => {
        if (settled) {
          return;
        }
        settled = true;
        try {
          zipfile.close();
        } catch (_closeError) {
          // Preserve the original error.
        }
        reject(error);
      };

      const finish = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(entries);
      };

      zipfile.on('entry', (entry) => {
        if (settled) {
          return;
        }
        if (entry.fileName.endsWith('/')) {
          zipfile.readEntry();
          return;
        }

        zipfile.openReadStream(entry, (streamError, readStream) => {
          if (streamError) {
            fail(streamError);
            return;
          }

          const chunks = [];
          readStream.on('data', (chunk) => chunks.push(chunk));
          readStream.on('error', fail);
          readStream.on('end', () => {
            if (settled) {
              return;
            }
            entries.set(entry.fileName, Buffer.concat(chunks));
            zipfile.readEntry();
          });
        });
      });
      zipfile.on('error', fail);
      zipfile.on('end', finish);
      zipfile.readEntry();
    });
  });
}
