const fs = require('node:fs');

const { readZipEntriesFromBuffer } = require('../replay/source-replay');

function inspectRenderedDocx({ docxPath, label = 'document.docx' } = {}) {
  const checks = [];
  const failures = [];

  if (!docxPath) {
    addCheck(checks, failures, 'docx_zip', 'failed', `${label} path is required.`);
    addCheck(
      checks,
      failures,
      'template_markers',
      'failed',
      'Cannot inspect template markers without word/document.xml.'
    );
    return { ok: false, status: 'failed', checks, failures, documentXml: '' };
  }
  if (!fs.existsSync(docxPath)) {
    addCheck(checks, failures, 'docx_zip', 'failed', `${label} is missing.`);
    addCheck(
      checks,
      failures,
      'template_markers',
      'failed',
      'Cannot inspect template markers without word/document.xml.'
    );
    return { ok: false, status: 'failed', checks, failures, documentXml: '' };
  }

  let documentXml = '';
  try {
    const entries = readZipEntriesFromBuffer(fs.readFileSync(docxPath));
    const documentXmlBuffer = entries.get('word/document.xml');
    if (!documentXmlBuffer) {
      addCheck(checks, failures, 'docx_zip', 'failed', `${label} is missing word/document.xml.`);
    } else {
      documentXml = documentXmlBuffer.toString('utf8');
      addCheck(checks, failures, 'docx_zip', 'passed');
    }
  } catch (error) {
    addCheck(checks, failures, 'docx_zip', 'failed', `${label} is not a readable DOCX zip: ${error.message}`);
  }

  if (!documentXml) {
    addCheck(
      checks,
      failures,
      'template_markers',
      'failed',
      'Cannot inspect template markers without word/document.xml.'
    );
  } else if (hasTemplateMarkers(documentXml)) {
    addCheck(
      checks,
      failures,
      'template_markers',
      'failed',
      'Template data markers remain in document.xml; DOCX template rendering did not complete.'
    );
  } else {
    addCheck(checks, failures, 'template_markers', 'passed');
  }

  const ok = failures.length === 0;
  return {
    ok,
    status: ok ? 'passed' : 'failed',
    checks,
    failures,
    documentXml,
  };
}

function hasTemplateMarkers(documentXml) {
  return /\{d\.[^}]+}/.test(documentXml || '');
}

function addCheck(checks, failures, id, status, message = '') {
  const check = { id, status };
  if (message) {
    check.message = message;
  }
  checks.push(check);
  if (status === 'failed') {
    failures.push(message || `${id} failed`);
  }
}

module.exports = { hasTemplateMarkers, inspectRenderedDocx };
