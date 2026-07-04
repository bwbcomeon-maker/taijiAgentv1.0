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

  entries.set(
    'word/document.xml',
    Buffer.from(injectFigureMetadata(documentXml.toString('utf8'), renderPlan), 'utf8')
  );

  await writeZipEntries(entries, outputPath);
  return { status: 'postprocessed', documentPath: outputPath };
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
  const docPrMetadata = `docx-engine-v2 ${figureIds.map((figureId) => `figureId=${figureId}`).join(' ')}`;
  updatedXml = updatedXml.replace(/<wp:docPr\b([^>]*?)\/>/, (match, attributes) => {
    let nextAttributes = upsertXmlAttribute(attributes, 'descr', docPrMetadata);
    nextAttributes = upsertXmlAttribute(nextAttributes, 'title', docPrMetadata);
    return `<wp:docPr${nextAttributes}/>`;
  });

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
