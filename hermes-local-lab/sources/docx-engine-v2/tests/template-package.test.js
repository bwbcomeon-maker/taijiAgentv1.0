const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { once } = require('node:events');
const test = require('node:test');
const Ajv2020 = require('ajv/dist/2020');
const yazl = require('yazl');

const { listTemplates, getTemplatePackage } = require('../src/templates/registry');
const { installTemplatePackage } = require('../src/templates/install-template-package');
const { validateTemplatePackage } = require('../src/templates/validate-template-package');

const rootDir = path.resolve(__dirname, '..');
const expectedTemplateIds = ['general-proposal', 'meeting-minutes'];

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function makeTempDir(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-template-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  return tempDir;
}

test('template registry lists migrated packages in stable order', () => {
  const templates = listTemplates({ rootDir });

  assert.deepEqual(
    templates.map((template) => template.id),
    expectedTemplateIds
  );
});

test('template registry includes installed templates after builtin templates', (t) => {
  const tempRoot = makeTempDir(t);
  const installedDir = path.join(tempRoot, 'installed', 'custom-proposal');
  fs.cpSync(path.join(rootDir, 'templates', 'general-proposal'), installedDir, { recursive: true });
  const manifestPath = path.join(installedDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: 'custom-proposal', name: 'Custom Proposal' }, null, 2)}\n`,
    'utf8'
  );
  fs.writeFileSync(
    path.join(tempRoot, 'template-registry.json'),
    `${JSON.stringify(
      {
        version: 1,
        builtin: [{ templateId: 'general-proposal', path: path.join(rootDir, 'templates', 'general-proposal') }],
        installed: [{ templateId: 'custom-proposal', path: 'installed/custom-proposal' }],
      },
      null,
      2
    )}\n`,
    'utf8'
  );

  const templates = listTemplates({ rootDir: tempRoot });

  assert.deepEqual(
    templates.map((template) => template.id),
    ['general-proposal', 'custom-proposal']
  );
  assert.equal(templates[0].registrySource, 'builtin');
  assert.equal(templates[1].registrySource, 'installed');
  assert.equal(getTemplatePackage('custom-proposal', { rootDir: tempRoot }).manifest.name, 'Custom Proposal');
});

test('template registry rejects duplicate template ids across sources', (t) => {
  const tempRoot = makeTempDir(t);
  fs.writeFileSync(
    path.join(tempRoot, 'template-registry.json'),
    `${JSON.stringify(
      {
        version: 1,
        builtin: [{ templateId: 'general-proposal', path: path.join(rootDir, 'templates', 'general-proposal') }],
        installed: [{ templateId: 'general-proposal', path: path.join(rootDir, 'templates', 'meeting-minutes') }],
      },
      null,
      2
    )}\n`,
    'utf8'
  );

  assert.throws(
    () => listTemplates({ rootDir: tempRoot }),
    /Duplicate template id in registry: general-proposal/
  );
});

test('installTemplatePackage validates, copies, and registers a new installed template', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = path.join(tempRoot, 'incoming', 'custom-proposal');
  fs.cpSync(path.join(rootDir, 'templates', 'general-proposal'), packageDir, { recursive: true });
  const manifestPath = path.join(packageDir, 'manifest.json');
  const manifest = readJson(manifestPath);
  fs.writeFileSync(
    manifestPath,
    `${JSON.stringify({ ...manifest, id: 'custom-proposal', name: 'Custom Proposal' }, null, 2)}\n`,
    'utf8'
  );
  fs.writeFileSync(
    path.join(tempRoot, 'template-registry.json'),
    `${JSON.stringify(
      {
        version: 1,
        builtin: [{ templateId: 'general-proposal', path: path.join(rootDir, 'templates', 'general-proposal') }],
        installed: [],
      },
      null,
      2
    )}\n`,
    'utf8'
  );

  const result = installTemplatePackage({ rootDir: tempRoot, packageDir });

  assert.equal(result.ok, true);
  assert.equal(result.templateId, 'custom-proposal');
  assert.equal(result.registryEntry.path, 'installed/custom-proposal');
  assert.equal(fs.existsSync(path.join(tempRoot, 'installed', 'custom-proposal', 'template.docx')), true);
  assert.deepEqual(
    listTemplates({ rootDir: tempRoot }).map((template) => template.id),
    ['general-proposal', 'custom-proposal']
  );
  const registry = readJson(path.join(tempRoot, 'template-registry.json'));
  assert.deepEqual(registry.installed, [{ templateId: 'custom-proposal', path: 'installed/custom-proposal' }]);
});

test('installTemplatePackage rejects duplicate template ids before copying files', (t) => {
  const tempRoot = makeTempDir(t);
  const packageDir = path.join(tempRoot, 'incoming', 'general-proposal');
  fs.cpSync(path.join(rootDir, 'templates', 'general-proposal'), packageDir, { recursive: true });
  fs.writeFileSync(
    path.join(tempRoot, 'template-registry.json'),
    `${JSON.stringify(
      {
        version: 1,
        builtin: [{ templateId: 'general-proposal', path: path.join(rootDir, 'templates', 'general-proposal') }],
        installed: [],
      },
      null,
      2
    )}\n`,
    'utf8'
  );

  assert.throws(
    () => installTemplatePackage({ rootDir: tempRoot, packageDir }),
    /Template already exists: general-proposal/
  );
  assert.equal(fs.existsSync(path.join(tempRoot, 'installed', 'general-proposal')), false);
  assert.deepEqual(readJson(path.join(tempRoot, 'template-registry.json')).installed, []);
});

test('migrated template packages expose required files and manifest metadata', () => {
  for (const templateId of expectedTemplateIds) {
    const template = getTemplatePackage(templateId, { rootDir });

    assert.equal(template.id, templateId);
    assert.equal(template.manifest.id, templateId);
    assert.equal(fs.existsSync(template.templatePath), true, template.templatePath);
    assert.equal(fs.existsSync(template.schemaPath), true, template.schemaPath);
    assert.equal(fs.existsSync(template.samplePath), true, template.samplePath);

    for (const fileName of ['template.docx', 'schema.json', 'sample.json', 'prompt.md']) {
      assert.equal(
        fs.existsSync(path.join(template.packageDir, fileName)),
        true,
        `${templateId} is missing ${fileName}`
      );
    }

    for (const field of ['documentTypes', 'capabilities', 'qualityGates', 'compatibility']) {
      assert.ok(
        Object.hasOwn(template.manifest, field),
        `${templateId} manifest is missing ${field}`
      );
    }

    assert.deepEqual(validateTemplatePackage(template), { ok: true });
  }
});

test('migrated template samples validate against their package schemas', () => {
  const ajv = new Ajv2020({ allErrors: true, strict: false });

  for (const templateId of expectedTemplateIds) {
    const template = getTemplatePackage(templateId, { rootDir });
    const validate = ajv.compile(readJson(template.schemaPath));
    const ok = validate(readJson(template.samplePath));

    assert.equal(ok, true, JSON.stringify(validate.errors || []));
  }
});

test('template validation rejects unreadable docx files', (t) => {
  const tempDir = makeTempDir(t);
  const template = getTemplatePackage('general-proposal', { rootDir });
  const badDocxPath = path.join(tempDir, 'template.docx');
  fs.writeFileSync(badDocxPath, 'not a zip package', 'utf8');

  const result = validateTemplatePackage({ ...template, templatePath: badDocxPath });

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some((error) => error.code === 'template_docx_invalid'),
    JSON.stringify(result.errors)
  );
});

test('template validation rejects docx zips that are missing word document body', async (t) => {
  const tempDir = makeTempDir(t);
  const template = getTemplatePackage('general-proposal', { rootDir });
  const badDocxPath = path.join(tempDir, 'template.docx');
  await writeZipWithoutDocumentXml(badDocxPath);

  const result = validateTemplatePackage({ ...template, templatePath: badDocxPath });

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some(
      (error) =>
        error.code === 'template_docx_invalid' &&
        /word\/document\.xml/.test(error.message)
    ),
    JSON.stringify(result.errors)
  );
});

test('template validation returns structured errors for invalid schema json', (t) => {
  const tempDir = makeTempDir(t);
  const template = getTemplatePackage('general-proposal', { rootDir });
  const badSchemaPath = path.join(tempDir, 'schema.json');
  fs.writeFileSync(badSchemaPath, '{ invalid json', 'utf8');

  const result = validateTemplatePackage({ ...template, schemaPath: badSchemaPath });

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some((error) => error.code === 'template_schema_validation_failed'),
    JSON.stringify(result.errors)
  );
});

test('template validation rejects invalid source requirement metadata', () => {
  const template = getTemplatePackage('general-proposal', { rootDir });
  const result = validateTemplatePackage({
    ...template,
    manifest: {
      ...template.manifest,
      sourceRequirements: {
        richContentRequired: true,
        minTables: -1,
        minVisuals: 1.5,
      },
    },
  });

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some((error) => error.code === 'source_requirements_invalid'),
    JSON.stringify(result.errors)
  );
});

async function writeZipWithoutDocumentXml(filePath) {
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
