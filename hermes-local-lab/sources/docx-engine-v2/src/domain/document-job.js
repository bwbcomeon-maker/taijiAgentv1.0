const { STATUSES } = require('./schemas');

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

  return { ...job, ...updates, status };
}

module.exports = { createDocumentJob, transitionJob };
