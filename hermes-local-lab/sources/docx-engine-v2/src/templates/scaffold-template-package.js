const fs = require('node:fs');
const path = require('node:path');

const { loadPackageFromDir } = require('./install-template-package');
const { getTemplatePackage } = require('./registry');
const { validateTemplatePackage } = require('./validate-template-package');

function scaffoldTemplatePackage({
  rootDir = path.resolve(__dirname, '../..'),
  fromTemplateId,
  templateId,
  name,
  description = '',
  outDir,
} = {}) {
  if (!fromTemplateId) {
    throw new Error('fromTemplateId is required.');
  }
  if (!templateId) {
    throw new Error('templateId is required.');
  }
  if (!name) {
    throw new Error('name is required.');
  }
  if (!outDir) {
    throw new Error('outDir is required.');
  }

  assertSafeTemplateId(templateId);
  const absoluteRootDir = path.resolve(rootDir);
  const absoluteOutDir = path.resolve(outDir);
  const baseTemplate = getTemplatePackage(fromTemplateId, { rootDir: absoluteRootDir });

  assertWritableOutDir(absoluteOutDir);
  fs.mkdirSync(absoluteOutDir, { recursive: true });
  copyPackageContents(baseTemplate.packageDir, absoluteOutDir);

  updateManifest({
    manifestPath: path.join(absoluteOutDir, 'manifest.json'),
    templateId,
    name,
    description,
  });
  updateSchema({
    schemaPath: path.join(absoluteOutDir, 'schema.json'),
    templateId,
    name,
  });
  updatePrompt({
    promptPath: path.join(absoluteOutDir, 'prompt.md'),
    name,
  });
  updateAdapterSample({
    adapterSamplePath: path.join(absoluteOutDir, 'adapter-sample.render-plan.json'),
    templateId,
    name,
  });

  const templatePackage = loadPackageFromDir({
    packageDir: absoluteOutDir,
    registrySource: 'incoming',
  });
  const validation = validateTemplatePackage(templatePackage);
  if (!validation.ok) {
    throw new Error(`Scaffolded template package is invalid: ${JSON.stringify(validation.errors)}`);
  }

  return {
    ok: true,
    templateId,
    baseTemplateId: baseTemplate.id,
    packageDir: absoluteOutDir,
    validation,
  };
}

function updateManifest({ manifestPath, templateId, name, description }) {
  const manifest = readJson(manifestPath);
  writeJson(manifestPath, {
    ...manifest,
    id: templateId,
    name,
    description: description || manifest.description || '',
  });
}

function updateSchema({ schemaPath, templateId, name }) {
  const schema = readJson(schemaPath);
  writeJson(schemaPath, {
    ...schema,
    $id: `${templateId}.schema.json`,
    title: `${name}数据`,
  });
}

function updatePrompt({ promptPath, name }) {
  const text = fs.readFileSync(promptPath, 'utf8');
  const lines = text.split(/\r?\n/);
  if (lines[0]?.startsWith('# ')) {
    lines[0] = `# ${name} JSON 生成要求`;
  } else {
    lines.unshift(`# ${name} JSON 生成要求`, '');
  }
  fs.writeFileSync(promptPath, lines.join('\n'), 'utf8');
}

function updateAdapterSample({ adapterSamplePath, templateId, name }) {
  const sample = readJson(adapterSamplePath);
  writeJson(adapterSamplePath, {
    ...sample,
    jobId: `job-${templateId}-adapter-sample`,
    templateId,
    templateData: {
      ...(sample.templateData || {}),
      title: `${name}适配器样例`,
      metadata: {
        ...((sample.templateData || {}).metadata || {}),
        templateId,
      },
    },
  });
}

function assertWritableOutDir(outDir) {
  if (!fs.existsSync(outDir)) {
    return;
  }
  const entries = fs.readdirSync(outDir);
  if (entries.length > 0) {
    throw new Error(`Template package output directory must be empty or missing; refused to overwrite non-empty directory: ${outDir}`);
  }
}

function copyPackageContents(sourceDir, targetDir) {
  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    if (isJunkFileName(entry.name)) {
      continue;
    }
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);
    fs.cpSync(sourcePath, targetPath, {
      recursive: true,
      errorOnExist: true,
      force: false,
      filter: (candidate) => !isJunkFileName(path.basename(candidate)),
    });
  }
}

function assertSafeTemplateId(templateId) {
  if (!/^[A-Za-z0-9_-]+$/.test(templateId || '')) {
    throw new Error(`Invalid template id: ${templateId || ''}`);
  }
}

function isJunkFileName(fileName) {
  return (
    fileName === '.DS_Store' ||
    fileName.startsWith('._') ||
    fileName.startsWith('.~') ||
    fileName.startsWith('~')
  );
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

module.exports = { scaffoldTemplatePackage };
