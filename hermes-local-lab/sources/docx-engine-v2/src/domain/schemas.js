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

const warningEntrySchema = {
  oneOf: [
    { type: 'string' },
    {
      type: 'object',
      additionalProperties: true,
      required: ['code', 'message'],
      properties: {
        code: { type: 'string', minLength: 1 },
        message: { type: 'string', minLength: 1 },
        severity: { enum: ['info', 'warning', 'error'] },
      },
    },
  ],
};

const warningArraySchema = {
  type: 'array',
  items: warningEntrySchema,
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

const sha256Schema = { type: 'string', pattern: '^[a-f0-9]{64}$' };

const documentMetadataV1Schema = {
  type: 'object',
  additionalProperties: false,
  required: ['title', 'documentType', 'client', 'issuer', 'compiler', 'versionLabel', 'classification', 'classificationLabel', 'documentDate'],
  properties: {
    title: { type: 'string', minLength: 1 },
    documentType: { type: 'string', minLength: 1 },
    client: { type: 'string' },
    issuer: { type: 'string', minLength: 1 },
    compiler: { type: 'string', minLength: 1 },
    versionLabel: { type: 'string', minLength: 1 },
    classification: { type: 'string', minLength: 1 },
    classificationLabel: { type: 'string' },
    documentDate: { type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}$' },
  },
};

const canonicalBindingV1Schema = {
  type: 'object',
  additionalProperties: false,
  required: ['artifactId', 'artifactSha256', 'briefRevision', 'briefSha256'],
  properties: {
    artifactId: { type: 'string', minLength: 1 },
    artifactSha256: sha256Schema,
    briefRevision: { type: 'integer', minimum: 1 },
    briefSha256: sha256Schema,
  },
};

const rendererIdentityV1Schema = {
  type: 'object',
  additionalProperties: false,
  required: ['name', 'version', 'buildSha256', 'profileId', 'profileSha256'],
  properties: {
    name: { const: 'docx-engine-v2' },
    version: { type: 'string', minLength: 1 },
    buildSha256: sha256Schema,
    profileId: { type: 'string', minLength: 1 },
    profileSha256: sha256Schema,
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
    sha256: sha256Schema,
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
    sha256: sha256Schema,
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
    sectionTitle: { type: 'string' },
    afterBlockId: { type: 'string' },
    anchorText: { type: 'string' },
    displayPath: { type: 'string', minLength: 1 },
    metadata: metadataSchema,
  },
};

const templateDataImageSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['figureId', 'path', 'sha256'],
  properties: {
    figureId: { type: 'string', minLength: 1 },
    path: { type: 'string', minLength: 1 },
    sha256: sha256Schema,
    caption: { type: 'string' },
    dimensions: metadataSchema,
    layoutIntent: { type: 'string' },
    metadata: metadataSchema,
  },
};

const templateDataColumnSchema = {
  type: 'object',
  additionalProperties: true,
  required: ['key', 'text'],
  properties: {
    key: { type: 'string', minLength: 1 },
    index: { type: 'integer', minimum: 1 },
    text: { type: 'string' },
  },
};

const templateDataCellSchema = {
  type: 'object',
  additionalProperties: true,
  required: ['key', 'text'],
  properties: {
    key: { type: 'string', minLength: 1 },
    index: { type: 'integer', minimum: 1 },
    text: { type: 'string' },
  },
};

const templateDataTableSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['tableId'],
  properties: {
    tableId: { type: 'string', minLength: 1 },
    title: { type: 'string' },
    headers: metadataSchema,
    columns: {
      type: 'array',
      items: templateDataColumnSchema,
    },
    rows: {
      type: 'array',
      items: {
        oneOf: [
          {
            type: 'array',
            items: true,
          },
          {
            type: 'object',
            additionalProperties: true,
            properties: {
              cells: {
                type: 'array',
                items: templateDataCellSchema,
              },
            },
          },
        ],
      },
    },
    metadata: metadataSchema,
  },
};

const templateManifestSchema = {
  type: 'object',
  additionalProperties: true,
  required: [
    'id',
    'name',
    'version',
    'description',
    'dataAdapter',
    'adapterSample',
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
    dataAdapter: { type: 'string', minLength: 1 },
    adapterSample: { type: 'string', minLength: 1 },
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
};

const schemas = {
  DocumentMetadataV1: documentMetadataV1Schema,
  CanonicalBindingV1: canonicalBindingV1Schema,
  RendererIdentityV1: rendererIdentityV1Schema,
  RenderInputBindingV1: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'brief', 'canonicalArtifact', 'canonicalMarkdownSha256', 'assetManifestSha256', 'semanticGatesSha256', 'template', 'rendererIdentity'],
    properties: {
      schemaVersion: { const: 'render-input-binding/v1' },
      brief: {
        type: 'object', additionalProperties: false, required: ['revision', 'sha256'],
        properties: { revision: { type: 'integer', minimum: 1 }, sha256: sha256Schema },
      },
      canonicalArtifact: {
        type: 'object', additionalProperties: false, required: ['artifactId', 'sha256'],
        properties: { artifactId: { type: 'string', minLength: 1 }, sha256: sha256Schema },
      },
      canonicalMarkdownSha256: sha256Schema,
      assetManifestSha256: sha256Schema,
      semanticGatesSha256: sha256Schema,
      template: {
        type: 'object', additionalProperties: false, required: ['id', 'version', 'packageSha256'],
        properties: { id: { type: 'string', minLength: 1 }, version: { type: 'string', minLength: 1 }, packageSha256: sha256Schema },
      },
      rendererIdentity: rendererIdentityV1Schema,
    },
  },
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
      deliveredAt: {
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
      documentMetadata: documentMetadataV1Schema,
      canonicalBinding: canonicalBindingV1Schema,
      rendererIdentity: rendererIdentityV1Schema,
      renderInputBinding: { type: 'object', additionalProperties: true },
      renderInputFingerprint: sha256Schema,
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
      warnings: warningArraySchema,
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
        required: ['manifest', 'template', 'schema', 'prompt', 'sample', 'dataAdapter', 'adapterSample'],
        properties: {
          manifest: { type: 'string', minLength: 1 },
          template: { type: 'string', minLength: 1 },
          schema: { type: 'string', minLength: 1 },
          prompt: { type: 'string', minLength: 1 },
          sample: { type: 'string', minLength: 1 },
          dataAdapter: { type: 'string', minLength: 1 },
          adapterSample: { type: 'string', minLength: 1 },
        },
      },
      manifest: templateManifestSchema,
    },
  },

  TemplateManifest: templateManifestSchema,

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
      documentMetadata: documentMetadataV1Schema,
      canonicalBinding: canonicalBindingV1Schema,
      rendererIdentity: rendererIdentityV1Schema,
      renderInputBinding: { type: 'object', additionalProperties: true },
      renderInputFingerprint: sha256Schema,
      assetManifestPath: { type: 'string', minLength: 1 },
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
            reviewedAt: { type: 'string' },
            reviewedBy: { type: 'string' },
            documentSha256: { type: 'string', pattern: '^[a-f0-9]{64}$' },
            visualChecks: stringArraySchema,
            visualEvidence: {
              type: 'array',
              items: {
                type: 'object',
                additionalProperties: false,
                required: ['path', 'sha256'],
                properties: {
                  path: { type: 'string', minLength: 1 },
                  sha256: { type: 'string', pattern: '^[a-f0-9]{64}$' },
                  sizeBytes: { type: 'integer', minimum: 1 },
                  mediaType: { enum: ['image/png', 'image/jpeg', 'application/pdf'] },
                },
              },
            },
          },
        },
      },
      warnings: stringArraySchema,
      failures: stringArraySchema,
      documentMetadata: documentMetadataV1Schema,
      canonicalBinding: canonicalBindingV1Schema,
      rendererIdentity: rendererIdentityV1Schema,
      renderInputFingerprint: sha256Schema,
    },
  },

  ReplayReport: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'status', 'replayedAt', 'deliveryDir', 'checks', 'warnings', 'failures'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/replay-report' },
      status: { enum: STATUSES.check },
      replayedAt: { type: 'string', minLength: 1 },
      deliveryDir: { type: 'string', minLength: 1 },
      inputFileSha256: {
        type: 'object',
        additionalProperties: false,
        properties: {
          document: sha256Schema,
          sourcePackage: sha256Schema,
          originalSource: sha256Schema,
          assetPackage: sha256Schema,
          jobManifest: sha256Schema,
          templateManifest: sha256Schema,
          renderPlan: sha256Schema,
          renderInputBinding: sha256Schema,
        },
      },
      replayedDocumentPath: { type: 'string' },
      checks: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: true,
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

  FailureReport: {
    type: 'object',
    additionalProperties: false,
    required: ['schemaVersion', 'ok', 'code', 'stage', 'message', 'failures', 'jobId', 'jobManifest'],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/failure-report' },
      ok: { const: false },
      code: { type: 'string', minLength: 1 },
      stage: { type: 'string', minLength: 1 },
      message: { type: 'string' },
      failures: stringArraySchema,
      jobId: { type: 'string', minLength: 1 },
      jobManifest: { type: 'string', minLength: 1 },
    },
  },

  DeliveryPackage: {
    type: 'object',
    additionalProperties: false,
    required: [
      'schemaVersion',
      'deliveryDir',
      'documentSha256',
      'sourceSha256',
      'fileSha256',
      'files',
      'status',
    ],
    properties: {
      schemaVersion: { const: 'docx-engine-v2/delivery-package' },
      deliveryDir: { type: 'string', minLength: 1 },
      documentSha256: { type: 'string', pattern: '^[a-f0-9]{64}$' },
      sourceSha256: { type: 'string', pattern: '^[a-f0-9]{64}$' },
      fileSha256: {
        type: 'object',
        additionalProperties: false,
        required: [
          'document',
          'source',
          'sourcePackage',
          'originalSource',
          'assetPackage',
          'jobManifest',
          'templateManifest',
          'renderPlan',
          'qualityReport',
          'imageInstructions',
        ],
        properties: {
          document: sha256Schema,
          source: sha256Schema,
          sourcePackage: sha256Schema,
          originalSource: sha256Schema,
          assetPackage: sha256Schema,
          jobManifest: sha256Schema,
          templateManifest: sha256Schema,
          renderPlan: sha256Schema,
          qualityReport: sha256Schema,
          replayReport: sha256Schema,
          imageInstructions: sha256Schema,
          renderInputBinding: sha256Schema,
        },
      },
      files: {
        type: 'object',
        additionalProperties: false,
        required: [
          'document',
          'source',
          'sourcePackage',
          'originalSource',
          'assetsDir',
          'assetPackage',
          'jobManifest',
          'templateManifest',
          'renderPlan',
          'qualityReport',
          'imageInstructions',
        ],
        properties: {
          document: { type: 'string', minLength: 1 },
          source: { type: 'string', minLength: 1 },
          sourcePackage: { type: 'string', minLength: 1 },
          originalSource: { type: 'string', minLength: 1 },
          assetsDir: { type: 'string', minLength: 1 },
          assetPackage: { type: 'string', minLength: 1 },
          jobManifest: { type: 'string', minLength: 1 },
          templateManifest: { type: 'string', minLength: 1 },
          renderPlan: { type: 'string', minLength: 1 },
          qualityReport: { type: 'string', minLength: 1 },
          replayReport: { type: 'string', minLength: 1 },
          imageInstructions: { type: 'string', minLength: 1 },
          renderInputBinding: { type: 'string', minLength: 1 },
        },
      },
      status: { enum: STATUSES.delivery },
    },
  },
};

module.exports = { STATUSES, schemas };
