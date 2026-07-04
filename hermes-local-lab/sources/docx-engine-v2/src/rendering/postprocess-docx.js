const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const yauzl = require('yauzl');
const yazl = require('yazl');

async function postprocessDocx({ docxPath, renderPlan, outputPath } = {}) {
  if (!docxPath) {
    throw new Error('docxPath is required.');
  }
  if (!renderPlan) {
    throw new Error('renderPlan is required.');
  }
  if (!outputPath) {
    throw new Error('outputPath is required.');
  }

  const entries = await readZipEntries(docxPath);
  const documentXml = entries.get('word/document.xml');
  if (!documentXml) {
    throw new Error('DOCX is missing word/document.xml.');
  }

  bindPlannedImages({ entries, documentXml: documentXml.toString('utf8'), renderPlan, outputPath });

  await writeZipEntries(entries, outputPath);
  return { status: 'postprocessed', documentPath: outputPath };
}

function bindPlannedImages({ entries, documentXml, renderPlan, outputPath }) {
  const images = renderPlan.templateData?.images || [];
  if (images.length === 0) {
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(documentXml, renderPlan), 'utf8'));
    return;
  }

  const relationshipsEntry = 'word/_rels/document.xml.rels';
  const contentTypesEntry = '[Content_Types].xml';
  let relationshipsXml = entries.get(relationshipsEntry)?.toString('utf8') || '';
  let contentTypesXml = entries.get(contentTypesEntry)?.toString('utf8') || '';
  if (!relationshipsXml || !contentTypesXml) {
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(documentXml, renderPlan), 'utf8'));
    return;
  }

  const drawingTemplate = firstDrawingXml(documentXml);
  if (!drawingTemplate) {
    entries.set('word/document.xml', Buffer.from(injectFigureMetadata(documentXml, renderPlan), 'utf8'));
    return;
  }

  const outputDir = path.dirname(path.resolve(outputPath));
  const relationshipIds = collectRelationshipIds(relationshipsXml);
  const boundDrawings = [];
  images.forEach((image, index) => {
    const imagePath = resolvePackagePath(outputDir, image.path || '');
    if (!imagePath || !fs.existsSync(imagePath)) {
      return;
    }
    const extension = path.extname(imagePath).toLowerCase();
    const contentType = imageContentType(extension);
    if (!contentType) {
      return;
    }

    const mediaFileName = `${safeFileName(image.figureId || `fig-${index + 1}`)}${extension}`;
    const mediaEntry = `word/media/${mediaFileName}`;
    const relationshipId = nextRelationshipId(relationshipIds);
    relationshipIds.add(relationshipId);
    entries.set(mediaEntry, fs.readFileSync(imagePath));
    relationshipsXml = appendImageRelationship(relationshipsXml, relationshipId, `media/${mediaFileName}`);
    contentTypesXml = ensureContentType(contentTypesXml, extension, contentType);
    boundDrawings.push(updateDrawingTemplate({
      drawingXml: drawingTemplate,
      relationshipId,
      figureId: image.figureId || `fig-${index + 1}`,
      docPrId: 9000 + index,
      title: image.caption || image.figureId || `图 ${index + 1}`,
      metadata: image.metadata || {},
    }));
  });

  entries.set(relationshipsEntry, Buffer.from(relationshipsXml, 'utf8'));
  entries.set(contentTypesEntry, Buffer.from(contentTypesXml, 'utf8'));
  const nextDocumentXml = boundDrawings.length
    ? replaceFirstDrawing(documentXml, boundDrawings.join(''))
    : documentXml;
  entries.set('word/document.xml', Buffer.from(injectFigureMetadata(nextDocumentXml, renderPlan), 'utf8'));
}

function collectFigureIds(renderPlan) {
  const ids = new Set();
  for (const figure of renderPlan.figures || []) {
    if (figure.figureId) {
      ids.add(figure.figureId);
    }
  }
  for (const image of renderPlan.templateData?.images || []) {
    if (image.figureId) {
      ids.add(image.figureId);
    }
  }
  return [...ids];
}

function injectFigureMetadata(documentXml, renderPlan) {
  const figureIds = collectFigureIds(renderPlan);
  if (figureIds.length === 0) {
    return documentXml;
  }

  const markerText = `docx-engine-v2 metadata ${figureIds
    .map((figureId) => `figureId=${figureId}`)
    .join(' ')}`;
  if (documentXml.includes(markerText)) {
    return documentXml;
  }

  let updatedXml = documentXml;
  if (!hasDocPrFigureMetadata(updatedXml, figureIds)) {
    const docPrMetadata = `docx-engine-v2 ${figureIds.map((figureId) => `figureId=${figureId}`).join(' ')}`;
    updatedXml = updatedXml.replace(/<wp:docPr\b([^>]*?)\/>/, (match, attributes) => {
      let nextAttributes = upsertXmlAttribute(attributes, 'descr', docPrMetadata);
      nextAttributes = upsertXmlAttribute(nextAttributes, 'title', docPrMetadata);
      return `<wp:docPr${nextAttributes}/>`;
    });
  }

  const paragraph = [
    '<w:p>',
    '<w:r>',
    '<w:rPr><w:vanish/></w:rPr>',
    `<w:t>${escapeXmlText(markerText)}</w:t>`,
    '</w:r>',
    '</w:p>',
  ].join('');

  if (updatedXml.includes('</w:body>')) {
    return updatedXml.replace('</w:body>', `${paragraph}</w:body>`);
  }

  return `${updatedXml}${paragraph}`;
}

function firstDrawingXml(documentXml) {
  return String(documentXml || '').match(/<w:drawing\b[\s\S]*?<\/w:drawing>/)?.[0] || '';
}

function replaceFirstDrawing(documentXml, drawingsXml) {
  return String(documentXml || '').replace(/<w:drawing\b[\s\S]*?<\/w:drawing>/, drawingsXml);
}

function hasDocPrFigureMetadata(documentXml, figureIds) {
  const presentFigureIds = new Set();
  for (const match of String(documentXml || '').matchAll(/<wp:docPr\b[^>]*>/g)) {
    for (const figureMatch of match[0].matchAll(/\bfigureId=([A-Za-z0-9_-]+)/g)) {
      presentFigureIds.add(figureMatch[1]);
    }
  }
  return figureIds.every((figureId) => presentFigureIds.has(figureId));
}

function updateDrawingTemplate({ drawingXml, relationshipId, figureId, docPrId, title, metadata = {} }) {
  const docPrMetadata = figureDocPrMetadata(figureId, metadata);
  let next = String(drawingXml || '').replace(/\br:embed="[^"]+"/, `r:embed="${relationshipId}"`);
  next = next.replace(/<wp:docPr\b([^>]*?)\/>/, (match, attributes) => {
    let nextAttributes = upsertXmlAttribute(attributes, 'id', String(docPrId));
    nextAttributes = upsertXmlAttribute(nextAttributes, 'name', title || figureId);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'descr', docPrMetadata);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'title', docPrMetadata);
    return `<wp:docPr${nextAttributes}/>`;
  });
  return next;
}

function figureDocPrMetadata(figureId, metadata = {}) {
  const tokens = ['docx-engine-v2', `figureId=${safeMetadataValue(figureId)}`];
  for (const key of ['sectionId', 'blockId', 'afterBlockId', 'sourceImageId']) {
    const value = safeMetadataValue(metadata[key]);
    if (value) {
      tokens.push(`${key}=${value}`);
    }
  }
  return tokens.join(' ');
}

function collectRelationshipIds(relationshipsXml) {
  const ids = new Set();
  for (const match of String(relationshipsXml || '').matchAll(/\bId="([^"]+)"/g)) {
    ids.add(match[1]);
  }
  return ids;
}

function nextRelationshipId(existingIds) {
  let index = 9000;
  while (existingIds.has(`rId${index}`)) {
    index += 1;
  }
  return `rId${index}`;
}

function appendImageRelationship(relationshipsXml, relationshipId, target) {
  const relationship = `<Relationship Id="${escapeXmlAttribute(relationshipId)}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="${escapeXmlAttribute(target)}"/>`;
  if (!relationshipsXml.includes('</Relationships>')) {
    return relationshipsXml;
  }
  return relationshipsXml.replace('</Relationships>', `${relationship}</Relationships>`);
}

function ensureContentType(contentTypesXml, extension, contentType) {
  const normalizedExtension = extension.replace(/^\./, '');
  const defaultPattern = new RegExp(`<Default\\b[^>]*\\bExtension="${escapeRegExp(normalizedExtension)}"[^>]*>`, 'i');
  if (defaultPattern.test(contentTypesXml)) {
    return contentTypesXml;
  }
  if (!contentTypesXml.includes('</Types>')) {
    return contentTypesXml;
  }
  return contentTypesXml.replace(
    '</Types>',
    `<Default Extension="${escapeXmlAttribute(normalizedExtension)}" ContentType="${escapeXmlAttribute(contentType)}"/></Types>`
  );
}

function imageContentType(extension) {
  return {
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
  }[extension] || '';
}

function resolvePackagePath(baseDir, relativePath) {
  const normalizedPath = String(relativePath || '').replace(/\\/g, '/').replace(/^\/+/, '');
  if (!normalizedPath || normalizedPath.split('/').includes('..')) {
    return '';
  }
  const resolvedPath = path.join(baseDir, normalizedPath);
  const relative = path.relative(baseDir, resolvedPath);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    return '';
  }
  return resolvedPath;
}

function safeFileName(value) {
  return String(value || '').replace(/[^A-Za-z0-9_-]/g, '_') || 'image';
}

function safeMetadataValue(value) {
  return String(value || '').replace(/[^A-Za-z0-9_-]/g, '_');
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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
  const tempPath = `${absoluteOutputPath}.tmp-${process.pid}`;

  await fsp.mkdir(path.dirname(absoluteOutputPath), { recursive: true });
  const zip = new yazl.ZipFile();
  const output = fs.createWriteStream(tempPath);
  const writeFinished = waitForZipWrite({ zip, output, tempPath });
  zip.outputStream.pipe(output);

  for (const [entryName, entryBuffer] of entries) {
    zip.addBuffer(entryBuffer, entryName);
  }

  zip.end();
  await writeFinished;
  await fsp.rename(tempPath, absoluteOutputPath);
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

function escapeXmlText(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeXmlAttribute(value) {
  return escapeXmlText(value).replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

function upsertXmlAttribute(attributes, name, value) {
  const escapedValue = escapeXmlAttribute(value);
  const attributePattern = new RegExp(`\\s${name}="[^"]*"`);
  if (attributePattern.test(attributes)) {
    return attributes.replace(attributePattern, ` ${name}="${escapedValue}"`);
  }
  return `${attributes} ${name}="${escapedValue}"`;
}

module.exports = { postprocessDocx };
