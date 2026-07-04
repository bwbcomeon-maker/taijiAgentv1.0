const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const yauzl = require('yauzl');
const yazl = require('yazl');

const IMAGE_CONTENT_TYPES = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
};

async function replaceDocxAsset({ docxPath, figureId, imagePath, outputPath } = {}) {
  assertRequiredFile(docxPath, 'docxPath');
  assertRequiredValue(figureId, 'figureId');
  assertRequiredFile(imagePath, 'imagePath');
  assertRequiredValue(outputPath, 'outputPath');

  const absoluteOutputPath = path.resolve(outputPath);
  if (fs.existsSync(absoluteOutputPath)) {
    throw new Error(`输出文件已存在: ${absoluteOutputPath}`);
  }

  const imageExtension = path.extname(imagePath).toLowerCase();
  const contentType = IMAGE_CONTENT_TYPES[imageExtension];
  if (!contentType) {
    throw new Error(`不支持的图片格式: ${imageExtension || 'unknown'}`);
  }

  const entries = await readZipEntries(path.resolve(docxPath));
  const documentXml = getRequiredEntry(entries, 'word/document.xml').toString('utf8');
  const relationshipsXml = getRequiredEntry(entries, 'word/_rels/document.xml.rels').toString('utf8');
  const contentTypesXml = getRequiredEntry(entries, '[Content_Types].xml').toString('utf8');
  const binding = findFigureBinding(documentXml, figureId);
  if (!binding.relationshipId) {
    throw new Error(`未在 DOCX 中找到图片标识: ${figureId}`);
  }

  const relationship = findRelationship(relationshipsXml, binding.relationshipId);
  if (!relationship) {
    throw new Error(`未在 DOCX 中找到图片关系: ${binding.relationshipId}`);
  }
  if (relationship.attrs.TargetMode === 'External' || !isImageRelationship(relationship.attrs.Type)) {
    throw new Error(`未在 DOCX 中找到图片关系: ${binding.relationshipId}`);
  }

  const replacement = chooseReplacementMediaPath(entries, figureId, imageExtension, relationship.attrs.Target);
  const updatedRelationshipsXml = updateRelationshipTarget(
    relationshipsXml,
    relationship.raw,
    replacement.target
  );
  const updatedContentTypesXml = ensureContentType(contentTypesXml, imageExtension, contentType);

  entries.set(replacement.entryName, await fsp.readFile(imagePath));
  entries.set('word/_rels/document.xml.rels', Buffer.from(updatedRelationshipsXml, 'utf8'));
  entries.set('[Content_Types].xml', Buffer.from(updatedContentTypesXml, 'utf8'));

  await writeZipEntries(entries, absoluteOutputPath);
  return {
    figureId,
    relationshipId: binding.relationshipId,
    mediaPath: replacement.entryName,
    outputPath: absoluteOutputPath,
  };
}

function assertRequiredValue(value, fieldName) {
  if (!value) {
    throw new Error(`${fieldName} is required.`);
  }
}

function assertRequiredFile(filePath, fieldName) {
  assertRequiredValue(filePath, fieldName);
  const absolutePath = path.resolve(filePath);
  if (!fs.existsSync(absolutePath) || !fs.statSync(absolutePath).isFile()) {
    throw new Error(`${fieldName} is not a readable file: ${absolutePath}`);
  }
}

function getRequiredEntry(entries, entryName) {
  const entry = entries.get(entryName);
  if (!entry) {
    throw new Error(`DOCX 缺少必需文件: ${entryName}`);
  }
  return entry;
}

function findFigureBinding(documentXml, figureId) {
  let foundFigureId = false;
  const figurePattern = new RegExp(`\\bfigureId=${escapeRegExp(figureId)}\\b`);

  for (const drawingMatch of String(documentXml || '').matchAll(/<w:drawing\b[\s\S]*?<\/w:drawing>/g)) {
    const drawingXml = drawingMatch[0];
    const docPrTags = drawingXml.match(/<wp:docPr\b[^>]*>/g) || [];
    if (!docPrTags.some((tag) => figurePattern.test(tag))) {
      continue;
    }

    foundFigureId = true;
    const relationshipMatch = drawingXml.match(/\br:embed="([^"]+)"/);
    if (relationshipMatch) {
      return { relationshipId: relationshipMatch[1] };
    }
  }

  if (foundFigureId) {
    throw new Error(`未在 DOCX 中找到图片关系: ${figureId}`);
  }
  return { relationshipId: '' };
}

function findRelationship(relationshipsXml, relationshipId) {
  for (const match of String(relationshipsXml || '').matchAll(/<Relationship\b[^>]*\/?>/g)) {
    const raw = match[0];
    const attrs = parseXmlAttributes(raw);
    if (attrs.Id === relationshipId) {
      return { raw, attrs };
    }
  }
  return null;
}

function parseXmlAttributes(tag) {
  const attrs = {};
  for (const match of String(tag || '').matchAll(/\s([A-Za-z_:][\w:.-]*)="([^"]*)"/g)) {
    attrs[match[1]] = match[2];
  }
  return attrs;
}

function isImageRelationship(type) {
  return String(type || '').endsWith('/image');
}

function chooseReplacementMediaPath(entries, figureId, imageExtension, currentTarget) {
  const safeFigureId = String(figureId).replace(/[^A-Za-z0-9_-]/g, '_');
  const preferredFileName = `${safeFigureId}${imageExtension}`;
  const preferredEntryName = `word/media/${preferredFileName}`;
  if (relationshipTargetEntryName(currentTarget) === preferredEntryName) {
    return {
      entryName: preferredEntryName,
      target: `media/${preferredFileName}`,
    };
  }

  if (!entries.has(preferredEntryName)) {
    return {
      entryName: preferredEntryName,
      target: `media/${preferredFileName}`,
    };
  }

  let index = 0;

  while (true) {
    const suffix = `-${index + 1}`;
    const fileName = `${safeFigureId}${suffix}${imageExtension}`;
    const entryName = `word/media/${fileName}`;
    if (!entries.has(entryName)) {
      return {
        entryName,
        target: `media/${fileName}`,
      };
    }
    index += 1;
  }
}

function relationshipTargetEntryName(target) {
  const rawTarget = String(target || '').trim();
  if (!rawTarget || /^[a-z][a-z0-9+.-]*:/i.test(rawTarget)) {
    return '';
  }
  if (rawTarget.startsWith('/')) {
    return path.posix.normalize(rawTarget.replace(/^\/+/, ''));
  }
  const entryName = path.posix.normalize(path.posix.join('word', rawTarget.replace(/\\/g, '/')));
  return entryName.startsWith('../') ? '' : entryName;
}

function updateRelationshipTarget(relationshipsXml, relationshipTag, nextTarget) {
  const updatedTag = upsertXmlAttribute(relationshipTag, 'Target', nextTarget);
  return relationshipsXml.replace(relationshipTag, updatedTag);
}

function ensureContentType(contentTypesXml, imageExtension, contentType) {
  const extension = imageExtension.replace(/^\./, '');
  const defaultPattern = new RegExp(`<Default\\b[^>]*\\bExtension="${escapeRegExp(extension)}"[^>]*>`, 'i');
  if (defaultPattern.test(contentTypesXml)) {
    return contentTypesXml;
  }
  if (!contentTypesXml.includes('</Types>')) {
    throw new Error('DOCX Content Types 文件格式不完整。');
  }
  return contentTypesXml.replace(
    '</Types>',
    `<Default Extension="${extension}" ContentType="${contentType}"/></Types>`
  );
}

function upsertXmlAttribute(tag, name, value) {
  const escapedValue = escapeXmlAttribute(value);
  const attributePattern = new RegExp(`\\s${escapeRegExp(name)}="[^"]*"`);
  if (attributePattern.test(tag)) {
    return tag.replace(attributePattern, ` ${name}="${escapedValue}"`);
  }
  return tag.replace(/\/?>$/, (ending) => ` ${name}="${escapedValue}"${ending}`);
}

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
          // Preserve the original ZIP error.
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

async function writeZipEntries(entries, outputPath) {
  const absoluteOutputPath = path.resolve(outputPath);
  const outputDir = path.dirname(absoluteOutputPath);
  const tempPath = path.join(outputDir, `.${path.basename(outputPath)}.tmp-${process.pid}-${Date.now()}`);

  await fsp.mkdir(outputDir, { recursive: true });
  const zip = new yazl.ZipFile();
  const output = fs.createWriteStream(tempPath, { flags: 'wx' });
  const writeFinished = waitForZipWrite({ zip, output, tempPath });
  zip.outputStream.pipe(output);

  for (const [entryName, entryBuffer] of entries) {
    zip.addBuffer(entryBuffer, entryName);
  }

  zip.end();
  await writeFinished;
  try {
    await fsp.link(tempPath, absoluteOutputPath);
  } catch (error) {
    if (error.code === 'EEXIST') {
      throw new Error(`输出文件已存在: ${absoluteOutputPath}`);
    }
    throw error;
  } finally {
    await fsp.rm(tempPath, { force: true });
  }
}

function waitForZipWrite({ zip, output, tempPath }) {
  return new Promise((resolve, reject) => {
    let settled = false;

    const fail = (error) => {
      if (settled) {
        return;
      }
      settled = true;
      fs.rmSync(tempPath, { force: true });
      reject(error);
    };

    output.on('error', fail);
    zip.outputStream.on('error', fail);
    output.on('close', () => {
      if (settled) {
        return;
      }
      settled = true;
      resolve();
    });
  });
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function escapeXmlText(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeXmlAttribute(value) {
  return escapeXmlText(value).replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

module.exports = { replaceDocxAsset };
