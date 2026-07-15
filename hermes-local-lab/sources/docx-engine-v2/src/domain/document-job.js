const crypto = require('node:crypto');
const { STATUSES } = require('./schemas');

const ALLOWED_TRANSITIONS = {
  created: ['source_normalized', 'failed'],
  source_normalized: ['template_selected', 'failed'],
  template_selected: ['assets_packaged', 'failed'],
  assets_packaged: ['render_planned', 'failed'],
  render_planned: ['rendered', 'failed'],
  rendered: ['validated', 'failed'],
  validated: ['delivered', 'failed'],
  delivered: [],
  failed: [],
};

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function canonicalSha256(value) {
  return crypto.createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');
}

function createDocumentJob({ jobId, sourceRef, templateId = '', workspace, inputs = [], documentMetadata, canonicalBinding, rendererIdentity, renderInputBinding, renderInputFingerprint }) {
  const job = {
    jobId,
    createdAt: new Date().toISOString(),
    sourceRef,
    templateId,
    status: 'created',
    workspace,
    inputs,
    outputs: [],
    warnings: [],
    failures: [],
  };
  if (documentMetadata) job.documentMetadata = documentMetadata;
  if (canonicalBinding) job.canonicalBinding = canonicalBinding;
  if (rendererIdentity) job.rendererIdentity = rendererIdentity;
  if (renderInputBinding) job.renderInputBinding = renderInputBinding;
  if (renderInputFingerprint) job.renderInputFingerprint = renderInputFingerprint;
  return job;
}

function transitionJob(job, status, updates = {}) {
  if (!STATUSES.job.includes(status)) {
    throw new Error(`Invalid job status: ${status}`);
  }

  if (!STATUSES.job.includes(job.status)) {
    throw new Error(`Invalid current job status: ${job.status}`);
  }

  const allowedTargets = ALLOWED_TRANSITIONS[job.status] || [];
  if (!allowedTargets.includes(status)) {
    throw new Error(`Invalid job transition: ${job.status} -> ${status}`);
  }

  return { ...job, ...updates, status };
}

module.exports = { canonicalJson, canonicalSha256, createDocumentJob, transitionJob };
