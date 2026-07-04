const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const yauzl = require('yauzl');

const rootDir = path.join(__dirname, '..');
const BUILD_SKILL = path.join(rootDir, 'scripts', 'build-copyable-skill.js');

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

test('build-copyable-skill writes a v2-backed skill package without runtime leftovers', (t) => {
  const { outDir } = buildSkillPackage(t);

  assert.equal(fs.existsSync(path.join(outDir, 'SKILL.md')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'skill.json')), true);
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
  assert.equal(fs.existsSync(path.join(outDir, 'engine/templates/general-proposal/template.docx')), true);
  assert.equal(fs.existsSync(path.join(outDir, 'runtime')), false);
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
