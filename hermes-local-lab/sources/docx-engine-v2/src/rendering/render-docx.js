const fs = require('node:fs/promises');
const path = require('node:path');

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

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.copyFile(templatePath, outputPath);

  return {
    status: 'rendered',
    documentPath: outputPath,
    templateId: renderPlan.templateId || templatePackage.templateId || templatePackage.id,
  };
}

module.exports = { renderDocx };
