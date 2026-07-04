const STATUSES = {
  job: [
    'created',
    'source_normalized',
    'template_selected',
    'assets_packaged',
    'render_planned',
    'rendered',
    'validated',
    'delivered',
    'failed',
  ],
  check: ['passed', 'passed_with_warnings', 'failed', 'not_verified'],
  delivery: ['delivered', 'failed'],
};

const sourceRefSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['type', 'path', 'sha256'],
  properties: {
    type: { enum: ['markdown', 'text', 'docx'] },
    path: { type: 'string', minLength: 1 },
    sha256: { type: 'string', pattern: '^[a-f0-9]{64}$' },
  },
};

const pathEntrySchema = {
  type: 'object',
  additionalProperties: false,
  required: ['type', 'path'],
  properties: {
    type: { type: 'string', minLength: 1 },
    path: { type: 'string', minLength: 1 },
  },
};

const stringArraySchema = {
  type: 'array',
  items: { type: 'string' },
};

const schemas = {
  DocumentJob: {
    type: 'object',
    additionalProperties: false,
    required: [
      'jobId',
      'createdAt',
      'sourceRef',
      'templateId',
      'status',
      'workspace',
      'inputs',
      'outputs',
      'warnings',
      'failures',
    ],
    properties: {
      jobId: { type: 'string', minLength: 1 },
      createdAt: {
        type: 'string',
        pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{3}Z$',
      },
      sourceRef: sourceRefSchema,
      templateId: { type: 'string' },
      status: { enum: STATUSES.job },
      workspace: { type: 'string', minLength: 1 },
      inputs: {
        type: 'array',
        items: pathEntrySchema,
      },
      outputs: {
        type: 'array',
        items: pathEntrySchema,
      },
      warnings: stringArraySchema,
      failures: stringArraySchema,
    },
  },

  SourcePackage: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'sourceRef', 'title', 'blocks'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/source-package' },
      sourceRef: sourceRefSchema,
      title: { type: 'string' },
      blocks: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['type', 'content'],
          properties: {
            type: { type: 'string', minLength: 1 },
            content: true,
          },
        },
      },
    },
  },

  TemplatePackage: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'templateId', 'files', 'manifest'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/template-package' },
      templateId: { type: 'string', minLength: 1 },
      files: {
        type: 'object',
        additionalProperties: false,
        required: ['manifest', 'template', 'schema', 'prompt', 'sample'],
        properties: {
          manifest: { type: 'string', minLength: 1 },
          template: { type: 'string', minLength: 1 },
          schema: { type: 'string', minLength: 1 },
          prompt: { type: 'string', minLength: 1 },
          sample: { type: 'string', minLength: 1 },
        },
      },
      manifest: {
        type: 'object',
        additionalProperties: true,
        required: ['id', 'name', 'version'],
        properties: {
          id: { type: 'string', minLength: 1 },
          name: { type: 'string', minLength: 1 },
          version: { type: 'string', minLength: 1 },
        },
      },
    },
  },

  AssetPackage: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'assetDir', 'assets'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/asset-package' },
      assetDir: { type: 'string', minLength: 1 },
      assets: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['assetId', 'kind', 'sourcePath', 'displayPath'],
          properties: {
            assetId: { type: 'string', minLength: 1 },
            kind: { enum: ['figure', 'table', 'image'] },
            sourcePath: { type: 'string', minLength: 1 },
            displayPath: { type: 'string', minLength: 1 },
            caption: { type: 'string' },
          },
        },
      },
    },
  },

  RenderPlan: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'jobId', 'templateId', 'steps', 'outputPath'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/render-plan' },
      jobId: { type: 'string', minLength: 1 },
      templateId: { type: 'string', minLength: 1 },
      steps: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['type', 'target'],
          properties: {
            type: { type: 'string', minLength: 1 },
            target: { type: 'string', minLength: 1 },
            source: { type: 'string' },
          },
        },
      },
      outputPath: { type: 'string', minLength: 1 },
    },
  },

  ValidationReport: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'status', 'checks', 'warnings', 'failures'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/validation-report' },
      status: { enum: STATUSES.check },
      checks: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['id', 'status'],
          properties: {
            id: { type: 'string', minLength: 1 },
            status: { enum: STATUSES.check },
            message: { type: 'string' },
          },
        },
      },
      warnings: stringArraySchema,
      failures: stringArraySchema,
    },
  },

  DeliveryPackage: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'deliveryDir', 'files', 'status'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/delivery-package' },
      deliveryDir: { type: 'string', minLength: 1 },
      files: {
        type: 'object',
        additionalProperties: false,
        required: [
          'document',
          'source',
          'assetsDir',
          'jobManifest',
          'templateManifest',
          'renderPlan',
          'qualityReport',
          'imageInstructions',
        ],
        properties: {
          document: { type: 'string', minLength: 1 },
          source: { type: 'string', minLength: 1 },
          assetsDir: { type: 'string', minLength: 1 },
          jobManifest: { type: 'string', minLength: 1 },
          templateManifest: { type: 'string', minLength: 1 },
          renderPlan: { type: 'string', minLength: 1 },
          qualityReport: { type: 'string', minLength: 1 },
          imageInstructions: { type: 'string', minLength: 1 },
        },
      },
      status: { enum: STATUSES.delivery },
    },
  },
};

module.exports = { STATUSES, schemas };
