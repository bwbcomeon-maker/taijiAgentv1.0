const fs = require('node:fs');
const Ajv2020 = require('ajv/dist/2020');

const { validateDomainObject } = require('../domain/validate');

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function fileExists(filePath) {
  return typeof filePath === 'string' && fs.existsSync(filePath);
}

function toDomainTemplatePackage(template) {
  return {
    schemaVersion: 'docx-engine-v2/template-package',
    templateId: template?.templateId || template?.id,
    files: template?.files,
    manifest: template?.manifest,
  };
}

function validateTemplatePackage(template) {
  const errors = [];
  const domainTemplate = toDomainTemplatePackage(template);
  const contractResult = validateDomainObject('TemplatePackage', domainTemplate);

  if (!contractResult.ok) {
    errors.push(
      ...contractResult.errors.map((error) => ({
        code: 'template_contract_invalid',
        path: error.path,
        message: error.message,
        details: error,
      }))
    );
  }

  if (template?.manifest?.id !== domainTemplate.templateId) {
    errors.push({
      code: 'manifest_id_mismatch',
      message: `Manifest id must match registry id: ${domainTemplate.templateId}`,
    });
  }

  for (const [key, filePath] of [
    ['manifest', template?.manifestPath],
    ['template', template?.templatePath],
    ['schema', template?.schemaPath],
    ['prompt', template?.promptPath],
    ['sample', template?.samplePath],
  ]) {
    if (!fileExists(filePath)) {
      errors.push({
        code: 'template_file_missing',
        file: key,
        path: filePath,
        message: `Template package is missing ${key} file.`,
      });
    }
  }

  if (fileExists(template?.schemaPath) && fileExists(template?.samplePath)) {
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    const validate = ajv.compile(readJson(template.schemaPath));
    const ok = validate(readJson(template.samplePath));

    if (!ok) {
      errors.push({
        code: 'sample_schema_invalid',
        path: template.samplePath,
        message: 'Template sample does not match schema.',
        details: validate.errors || [],
      });
    }
  }

  if (errors.length > 0) {
    return { ok: false, errors };
  }

  return { ok: true };
}

module.exports = { validateTemplatePackage };
