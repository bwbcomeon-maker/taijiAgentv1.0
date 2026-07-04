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

function createDocumentJob({ jobId, sourceRef, templateId = '', workspace, inputs = [] }) {
  return {
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

module.exports = { createDocumentJob, transitionJob };
