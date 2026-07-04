const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const Ajv2020 = require('ajv/dist/2020');
const carbone = require('carbone');

async function renderDocx({ templatePackage, renderPlan, outputPath } = {}) {
  if (!templatePackage) {
    throw new Error('templatePackage is required.');
  }
  if (!renderPlan) {
    throw new Error('renderPlan is required.');
  }
  if (!outputPath) {
    throw new Error('outputPath is required.');
  }

  const templatePath = templatePackage.templatePath;
  if (!templatePath) {
    throw new Error('templatePackage.templatePath is required.');
  }

  const templateData = buildTemplateData({ templatePackage, renderPlan });
  validateTemplateData({ templatePackage, templateData });
  const rendered = await renderCarbone(templatePath, templateData);
  await fsp.mkdir(path.dirname(outputPath), { recursive: true });
  await fsp.writeFile(outputPath, rendered);

  return {
    status: 'rendered',
    documentPath: outputPath,
    templateId: renderPlan.templateId || templatePackage.templateId || templatePackage.id,
  };
}

function renderCarbone(templatePath, data) {
  return new Promise((resolve, reject) => {
    carbone.render(templatePath, data, (error, result) => {
      if (error) {
        reject(error);
        return;
      }
      resolve(result);
    });
  });
}

function validateTemplateData({ templatePackage, templateData }) {
  if (!templatePackage.schemaPath || !fs.existsSync(templatePackage.schemaPath)) {
    return;
  }
  const schema = JSON.parse(fs.readFileSync(templatePackage.schemaPath, 'utf8'));
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  const validate = ajv.compile(schema);
  if (!validate(templateData)) {
    throw new Error(`Template data validation failed: ${JSON.stringify(validate.errors || [])}`);
  }
}

function buildTemplateData({ templatePackage, renderPlan }) {
  const adapter = loadTemplateDataAdapter(templatePackage);
  return adapter.buildTemplateData({ templatePackage, renderPlan });
}

function loadTemplateDataAdapter(templatePackage) {
  const adapterPath = resolveTemplateDataAdapterPath(templatePackage);
  delete require.cache[require.resolve(adapterPath)];
  const adapter = require(adapterPath);
  if (typeof adapter.buildTemplateData !== 'function') {
    throw new Error(`Template data adapter must export buildTemplateData: ${adapterPath}`);
  }
  return adapter;
}

function resolveTemplateDataAdapterPath(templatePackage = {}) {
  if (!templatePackage.packageDir) {
    throw new Error('templatePackage.packageDir is required for data adapter loading.');
  }
  const adapterFile = templatePackage.dataAdapterPath ||
    templatePackage.files?.dataAdapter ||
    templatePackage.manifest?.dataAdapter ||
    '';
  if (!adapterFile) {
    throw new Error('templatePackage dataAdapter is required.');
  }

  const packageDir = path.resolve(templatePackage.packageDir);
  const resolvedAdapterPath = path.resolve(packageDir, adapterFile);
  const relative = path.relative(packageDir, resolvedAdapterPath);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Template data adapter must be inside the template package: ${resolvedAdapterPath}`);
  }
  if (!fs.existsSync(resolvedAdapterPath)) {
    throw new Error(`Template data adapter does not exist: ${resolvedAdapterPath}`);
  }
  return resolvedAdapterPath;
}

module.exports = { renderDocx, buildTemplateData };
