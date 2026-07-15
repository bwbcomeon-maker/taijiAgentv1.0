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
  for (const templateId of ['general-proposal', 'meeting-minutes', 'enterprise-work-report', 'enterprise-research-report']) {
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

test('enterprise adapters map approved content and metadata without defaults or invented sections', () => {
  for (const templateId of ['enterprise-work-report', 'enterprise-research-report']) {
    const templatePackage = getTemplatePackage(templateId, { rootDir: ENGINE_ROOT });
    const renderPlan = {
      documentMetadata: {
        title: '精确标题', documentType: templateId.includes('research') ? 'research_report' : 'work_report',
        client: '真实客户', issuer: '真实签发单位', compiler: '真实编制单位', versionLabel: 'V2.1',
        classification: 'internal', classificationLabel: '内部资料', documentDate: '2026-07-15',
      },
      templateData: {
        title: 'Markdown 中的另一个标题',
        sections: [{ sectionId: 'sec-1', title: '批准章节', blocks: [{ type: 'paragraph', text: '批准正文。' }] }],
        tables: [], images: [], metadata: {},
      },
      tables: [], figures: [],
    };
    const data = buildTemplateData({ templatePackage, renderPlan });
    const serialized = JSON.stringify(data);
    assert.equal(data.cover.title, '精确标题');
    assert.deepEqual(data.sections.map((item) => item.title), ['批准章节']);
    assert.deepEqual(data.sections[0].paragraphs, [{ text: '批准正文。' }]);
    assert.deepEqual(data.tables, []);
    assert.deepEqual(data.images, []);
    for (const forbidden of ['客户单位', '暂无', '待补充', '北京太极', '2026年7月']) {
      assert.equal(serialized.includes(forbidden), false, forbidden);
    }
  }
});

test('enterprise adapters reject incomplete cover metadata', () => {
  const templatePackage = getTemplatePackage('enterprise-work-report', { rootDir: ENGINE_ROOT });
  assert.throws(
    () => buildTemplateData({ templatePackage, renderPlan: { documentMetadata: { title: '只有标题' }, templateData: { sections: [] } } }),
    /brief_incomplete/
  );
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

test('general proposal adapter exposes table columns and row cells dynamically', () => {
  const templatePackage = getTemplatePackage('general-proposal', { rootDir: ENGINE_ROOT });
  const renderPlan = {
    templateData: {
      title: 'Dynamic table proposal',
      sections: [
        {
          sectionId: 'sec-001',
          title: '1. Dynamic Tables',
          blocks: [],
        },
      ],
      tables: [
        {
          tableId: 'tbl-001',
          title: 'Six column table',
          headers: {
            c1: '列1',
            c2: '列2',
            c3: '列3',
            c4: '列4',
            c5: '列5',
            c6: '列6',
          },
          columns: [
            { key: 'c1', text: '列1' },
            { key: 'c2', text: '列2' },
            { key: 'c3', text: '列3' },
            { key: 'c4', text: '列4' },
            { key: 'c5', text: '列5' },
            { key: 'c6', text: '列6' },
          ],
          rows: [
            {
              c1: '值1',
              c2: '值2',
              c3: '值3',
              c4: '值4',
              c5: '值5',
              c6: '值6',
              cells: [
                { key: 'c1', text: '值1' },
                { key: 'c2', text: '值2' },
                { key: 'c3', text: '值3' },
                { key: 'c4', text: '值4' },
                { key: 'c5', text: '值5' },
                { key: 'c6', text: '值6' },
              ],
            },
          ],
          metadata: {},
        },
      ],
      images: [],
      metadata: { templateId: 'general-proposal' },
    },
    tables: [],
    figures: [],
  };

  const templateData = buildTemplateData({ templatePackage, renderPlan });

  assert.equal(templateData.chapters[0].title, 'Dynamic Tables');
  assert.equal(templateData.chapters[0].sections[0].title, 'Dynamic Tables');
  assert.deepEqual(
    templateData.tables[0].columns.map((column) => column.text),
    ['列1', '列2', '列3', '列4', '列5', '列6']
  );
  assert.deepEqual(
    templateData.tables[0].rows[0].cells.map((cell) => cell.text),
    ['值1', '值2', '值3', '值4', '值5', '值6']
  );
  assert.equal(templateData.tables[0].headers.c6, '列6');
  assert.equal(templateData.tables[0].rows[0].c6, '值6');
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
