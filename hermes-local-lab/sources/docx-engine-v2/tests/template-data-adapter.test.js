const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { buildTemplateData } = require('../src/rendering/render-docx');
const { getTemplatePackage } = require('../src/templates/registry');
const { loadPackageFromDir } = require('../src/templates/install-template-package');
const { validateTemplatePackage } = require('../src/templates/validate-template-package');

const ENGINE_ROOT = path.join(__dirname, '..');

function makeTempDir(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-adapter-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  return tempDir;
}

test('template packages declare a package-local data adapter', () => {
  for (const templateId of ['general-proposal', 'meeting-minutes']) {
    const template = getTemplatePackage(templateId, { rootDir: ENGINE_ROOT });

    assert.equal(template.files.dataAdapter, 'data-adapter.js');
    assert.equal(template.files.adapterSample, 'adapter-sample.render-plan.json');
    assert.equal(template.manifest.dataAdapter, 'data-adapter.js');
    assert.equal(template.manifest.adapterSample, 'adapter-sample.render-plan.json');
    assert.equal(template.dataAdapterPath, path.join(template.packageDir, 'data-adapter.js'));
    assert.equal(template.adapterSamplePath, path.join(template.packageDir, 'adapter-sample.render-plan.json'));
    assert.equal(fs.existsSync(template.dataAdapterPath), true);
    assert.equal(fs.existsSync(template.adapterSamplePath), true);
  }
});

test('buildTemplateData delegates render-plan mapping to the selected template package adapter', (t) => {
  const packageDir = makeTempDir(t);
  const adapterPath = path.join(packageDir, 'custom-adapter.js');
  fs.writeFileSync(
    adapterPath,
    [
      'function buildTemplateData({ renderPlan }) {',
      '  return {',
      '    mappedBy: "package-adapter",',
      '    title: renderPlan.templateData.title,',
      '    sectionCount: renderPlan.templateData.sections.length,',
      '  };',
      '}',
      '',
      'module.exports = { buildTemplateData };',
      '',
    ].join('\n'),
    'utf8'
  );

  const templatePackage = {
    templateId: 'custom-template',
    packageDir,
    manifest: { id: 'custom-template', dataAdapter: 'custom-adapter.js' },
    files: { dataAdapter: 'custom-adapter.js' },
    dataAdapterPath: adapterPath,
  };
  const renderPlan = {
    templateData: {
      title: 'Adapter owned mapping',
      sections: [{ sectionId: 'sec-001', title: 'Scope', blocks: [] }],
    },
  };

  assert.deepEqual(buildTemplateData({ templatePackage, renderPlan }), {
    mappedBy: 'package-adapter',
    title: 'Adapter owned mapping',
    sectionCount: 1,
  });
});

test('template validation rejects packages without a usable data adapter', (t) => {
  const packageDir = path.join(makeTempDir(t), 'custom-template');
  fs.cpSync(path.join(ENGINE_ROOT, 'templates', 'general-proposal'), packageDir, { recursive: true });
  fs.rmSync(path.join(packageDir, 'data-adapter.js'), { force: true });
  const template = loadPackageFromDir({ packageDir });

  const result = validateTemplatePackage(template);

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some(
      (error) =>
        error.code === 'template_file_missing' &&
        error.file === 'dataAdapter'
    ),
    JSON.stringify(result.errors)
  );
});

test('template validation rejects adapter sample output that does not satisfy schema', (t) => {
  const packageDir = path.join(makeTempDir(t), 'bad-adapter-template');
  fs.cpSync(path.join(ENGINE_ROOT, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: 'bad-adapter-template', adapterSample: 'adapter-sample.render-plan.json' }, null, 2)}\n`,
    'utf8'
  );
  fs.writeFileSync(
    path.join(packageDir, 'adapter-sample.render-plan.json'),
    `${JSON.stringify(
      {
        schemaVersion: 'docx-engine-v2/render-plan',
        jobId: 'job-adapter-sample',
        templateId: 'bad-adapter-template',
        sections: [],
        tables: [],
        figures: [],
        templateData: {
          title: 'Bad adapter sample',
          sections: [],
          tables: [],
          images: [],
          metadata: { templateId: 'bad-adapter-template' },
        },
        warnings: [],
      },
      null,
      2
    )}\n`,
    'utf8'
  );
  fs.writeFileSync(
    path.join(packageDir, 'data-adapter.js'),
    [
      'function buildTemplateData() {',
      '  return { broken: true };',
      '}',
      '',
      'module.exports = { buildTemplateData };',
      '',
    ].join('\n'),
    'utf8'
  );

  const result = validateTemplatePackage(loadPackageFromDir({ packageDir }));

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some((error) => error.code === 'template_data_adapter_sample_invalid'),
    JSON.stringify(result.errors)
  );
});
