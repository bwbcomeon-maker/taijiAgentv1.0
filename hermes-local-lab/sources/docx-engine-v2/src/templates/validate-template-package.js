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

function validateTemplateDocx(templatePath) {
  if (!fileExists(templatePath)) {
    return [];
  }

  try {
    const stat = fs.statSync(templatePath);
    if (!stat.isFile()) {
      return [
        {
          code: 'template_docx_invalid',
          path: templatePath,
          message: 'Template DOCX path is not a file.',
        },
      ];
    }

    const entries = readZipEntryNames(fs.readFileSync(templatePath));
    const requiredEntries = ['[Content_Types].xml', 'word/document.xml'];
    return requiredEntries
      .filter((entryName) => !entries.includes(entryName))
      .map((entryName) => ({
        code: 'template_docx_invalid',
        path: templatePath,
        message: `Template DOCX is missing ${entryName}.`,
      }));
  } catch (error) {
    return [
      {
        code: 'template_docx_invalid',
        path: templatePath,
        message: `Template DOCX cannot be inspected: ${error.message}`,
      },
    ];
  }
}

function readZipEntryNames(buffer) {
  const eocdOffset = findEndOfCentralDirectory(buffer);
  if (eocdOffset < 0) {
    throw new Error('missing ZIP end of central directory');
  }

  const centralDirectorySize = buffer.readUInt32LE(eocdOffset + 12);
  const centralDirectoryOffset = buffer.readUInt32LE(eocdOffset + 16);
  const centralDirectoryEnd = centralDirectoryOffset + centralDirectorySize;
  if (centralDirectoryOffset < 0 || centralDirectoryEnd > buffer.length) {
    throw new Error('invalid ZIP central directory bounds');
  }

  const entries = [];
  let offset = centralDirectoryOffset;
  while (offset < centralDirectoryEnd) {
    if (offset + 46 > buffer.length || buffer.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error('invalid ZIP central directory entry');
    }

    const fileNameLength = buffer.readUInt16LE(offset + 28);
    const extraFieldLength = buffer.readUInt16LE(offset + 30);
    const fileCommentLength = buffer.readUInt16LE(offset + 32);
    const fileNameStart = offset + 46;
    const fileNameEnd = fileNameStart + fileNameLength;
    if (fileNameEnd > buffer.length) {
      throw new Error('invalid ZIP file name bounds');
    }

    entries.push(buffer.subarray(fileNameStart, fileNameEnd).toString('utf8'));
    offset = fileNameEnd + extraFieldLength + fileCommentLength;
  }

  return entries;
}

function findEndOfCentralDirectory(buffer) {
  const minOffset = Math.max(0, buffer.length - 65557);
  for (let offset = buffer.length - 22; offset >= minOffset; offset -= 1) {
    if (buffer.readUInt32LE(offset) === 0x06054b50) {
      return offset;
    }
  }
  return -1;
}

function validateSchemaSample(template) {
  if (!fileExists(template?.schemaPath) || !fileExists(template?.samplePath)) {
    return [];
  }

  try {
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    const validate = ajv.compile(readJson(template.schemaPath));
    const ok = validate(readJson(template.samplePath));

    if (!ok) {
      return [
        {
          code: 'sample_schema_invalid',
          path: template.samplePath,
          message: 'Template sample does not match schema.',
          details: validate.errors || [],
        },
      ];
    }
  } catch (error) {
    return [
      {
        code: 'template_schema_validation_failed',
        path: template.schemaPath,
        message: `Template schema/sample validation failed: ${error.message}`,
      },
    ];
  }

  return [];
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

  errors.push(...validateTemplateDocx(template?.templatePath));
  errors.push(...validateSchemaSample(template));

  if (errors.length > 0) {
    return { ok: false, errors };
  }

  return { ok: true };
}

module.exports = { validateTemplatePackage };
