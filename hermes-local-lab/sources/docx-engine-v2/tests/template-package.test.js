const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const Ajv2020 = require('ajv/dist/2020');

const { listTemplates, getTemplatePackage } = require('../src/templates/registry');
const { validateTemplatePackage } = require('../src/templates/validate-template-package');

const rootDir = path.resolve(__dirname, '..');
const expectedTemplateIds = ['general-proposal', 'meeting-minutes'];

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

test('template registry lists migrated packages in stable order', () => {
  const templates = listTemplates({ rootDir });

  assert.deepEqual(
    templates.map((template) => template.id),
    expectedTemplateIds
  );
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
