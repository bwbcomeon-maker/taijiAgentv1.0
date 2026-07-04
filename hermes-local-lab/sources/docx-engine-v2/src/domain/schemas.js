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

const metadataSchema = {
  type: 'object',
  additionalProperties: true,
};

const stringArraySchema = {
  type: 'array',
  items: { type: 'string' },
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

const dimensionsSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    width: { type: 'number' },
    height: { type: 'number' },
    unit: { type: 'string' },
  },
};

const qualitySchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    status: { enum: STATUSES.check },
    warnings: stringArraySchema,
  },
};

const sectionSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['sectionId', 'title', 'level'],
  properties: {
    sectionId: { type: 'string', minLength: 1 },
    title: { type: 'string' },
    level: { type: 'integer', minimum: 1 },
    blockIds: {
      type: 'array',
      items: { type: 'string', minLength: 1 },
    },
    metadata: metadataSchema,
  },
};

const blockSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['id', 'type'],
  properties: {
    id: { type: 'string', minLength: 1 },
    type: { type: 'string', minLength: 1 },
    text: { type: 'string' },
    content: true,
    level: { type: 'integer', minimum: 1 },
    sectionId: { type: 'string' },
    sectionTitle: { type: 'string' },
    anchorText: { type: 'string' },
    path: { type: 'string' },
    caption: { type: 'string' },
    metadata: metadataSchema,
  },
};

const tableSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['tableId', 'title', 'sectionId', 'afterBlockId', 'anchorText', 'metadata'],
  properties: {
    tableId: { type: 'string', minLength: 1 },
    title: { type: 'string' },
    sectionId: { type: 'string' },
    afterBlockId: { type: 'string' },
    anchorText: { type: 'string' },
    headers: {
      type: 'array',
      items: { type: 'string' },
    },
    rows: {
      type: 'array',
      items: {
        oneOf: [
          {
            type: 'array',
            items: true,
          },
          metadataSchema,
        ],
      },
    },
    metadata: metadataSchema,
  },
};

const figureSchema = {
  type: 'object',
  additionalProperties: false,
  required: [
    'figureId',
    'caption',
    'sectionId',
    'anchorText',
    'sourceType',
    'editable',
    'displayPath',
    'dimensions',
    'quality',
    'metadata',
  ],
  properties: {
    figureId: { type: 'string', minLength: 1 },
    caption: { type: 'string' },
    sectionId: { type: 'string' },
    anchorText: { type: 'string' },
    sourceType: { type: 'string', minLength: 1 },
    editable: metadataSchema,
    displayPath: { type: 'string', minLength: 1 },
    dimensions: dimensionsSchema,
    quality: qualitySchema,
    metadata: metadataSchema,
  },
};

const sourceImageSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['imageId', 'path'],
  properties: {
    imageId: { type: 'string', minLength: 1 },
    path: { type: 'string', minLength: 1 },
    caption: { type: 'string' },
    sectionId: { type: 'string' },
    anchorText: { type: 'string' },
    metadata: metadataSchema,
  },
};

const assetImageSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['imageId', 'sourcePath', 'displayPath'],
  properties: {
    imageId: { type: 'string', minLength: 1 },
    sourcePath: { type: 'string', minLength: 1 },
    displayPath: { type: 'string', minLength: 1 },
    caption: { type: 'string' },
    sectionId: { type: 'string' },
    metadata: metadataSchema,
  },
};

const renderTableSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['tableId', 'title', 'sectionId', 'afterBlockId', 'anchorText', 'metadata'],
  properties: {
    tableId: { type: 'string', minLength: 1 },
    title: { type: 'string' },
    sectionId: { type: 'string' },
    afterBlockId: { type: 'string' },
    anchorText: { type: 'string' },
    metadata: metadataSchema,
  },
};

const renderFigureSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['figureId', 'caption', 'sectionId', 'afterBlockId', 'anchorText', 'displayPath', 'metadata'],
  properties: {
    figureId: { type: 'string', minLength: 1 },
    caption: { type: 'string' },
    sectionId: { type: 'string' },
    afterBlockId: { type: 'string' },
    anchorText: { type: 'string' },
    displayPath: { type: 'string', minLength: 1 },
    metadata: metadataSchema,
  },
};

const templateDataImageSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['figureId', 'path'],
  properties: {
    figureId: { type: 'string', minLength: 1 },
    path: { type: 'string', minLength: 1 },
    caption: { type: 'string' },
    metadata: metadataSchema,
  },
};

const templateDataTableSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['tableId'],
  properties: {
    tableId: { type: 'string', minLength: 1 },
    title: { type: 'string' },
    rows: {
      type: 'array',
      items: {
        oneOf: [
          {
            type: 'array',
            items: true,
          },
          metadataSchema,
        ],
      },
    },
    metadata: metadataSchema,
  },
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
    required: [
      'schemaVersion',
      'sourceType',
      'sourceRef',
      'title',
      'sections',
      'blocks',
      'tables',
      'figures',
      'images',
      'embeddedMedia',
      'warnings',
    ],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/source-package' },
      sourceType: { enum: ['markdown', 'text', 'docx'] },
      sourceRef: sourceRefSchema,
      title: { type: 'string' },
      sections: {
        type: 'array',
        items: sectionSchema,
      },
      blocks: {
        type: 'array',
        items: blockSchema,
      },
      tables: {
        type: 'array',
        items: tableSchema,
      },
      figures: {
        type: 'array',
        items: figureSchema,
      },
      images: {
        type: 'array',
        items: sourceImageSchema,
      },
      embeddedMedia: {
        type: 'array',
        items: metadataSchema,
      },
      warnings: stringArraySchema,
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
        required: [
          'id',
          'name',
          'version',
          'description',
          'documentTypes',
          'capabilities',
          'requiredAssets',
          'qualityGates',
          'compatibility',
        ],
        properties: {
          id: { type: 'string', minLength: 1 },
          name: { type: 'string', minLength: 1 },
          version: { type: 'string', minLength: 1 },
          description: { type: 'string' },
          documentTypes: {
            type: 'array',
            items: { type: 'string', minLength: 1 },
          },
          capabilities: {
            type: 'array',
            items: { type: 'string', minLength: 1 },
          },
          requiredAssets: {
            type: 'array',
            items: { type: 'string' },
          },
          qualityGates: {
            type: 'array',
            items: { type: 'string', minLength: 1 },
          },
          compatibility: metadataSchema,
        },
      },
    },
  },

  AssetPackage: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'assetDir', 'figures', 'tables'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/asset-package' },
      assetDir: { type: 'string', minLength: 1 },
      figures: {
        type: 'array',
        items: figureSchema,
      },
      tables: {
        type: 'array',
        items: tableSchema,
      },
      images: {
        type: 'array',
        items: assetImageSchema,
      },
      warnings: stringArraySchema,
    },
  },

  RenderPlan: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'jobId', 'templateId', 'sections', 'tables', 'figures', 'templateData'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/render-plan' },
      jobId: { type: 'string', minLength: 1 },
      templateId: { type: 'string', minLength: 1 },
      sections: {
        type: 'array',
        items: sectionSchema,
      },
      tables: {
        type: 'array',
        items: renderTableSchema,
      },
      figures: {
        type: 'array',
        items: renderFigureSchema,
      },
      templateData: {
        type: 'object',
        additionalProperties: true,
        required: ['images', 'tables'],
        properties: {
          title: { type: 'string' },
          sections: {
            type: 'array',
            items: true,
          },
          images: {
            type: 'array',
            items: templateDataImageSchema,
          },
          tables: {
            type: 'array',
            items: templateDataTableSchema,
          },
          metadata: metadataSchema,
        },
      },
      warnings: stringArraySchema,
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
