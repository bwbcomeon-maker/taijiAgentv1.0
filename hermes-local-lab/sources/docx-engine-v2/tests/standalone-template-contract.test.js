const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { canonicalSha256 } = require('../src/domain/document-job');
const { validateDomainObject } = require('../src/domain/validate');
const { readZipEntriesFromBuffer } = require('../src/replay/source-replay');
const { buildTemplateData } = require('../src/rendering/render-docx');
const { getTemplatePackage, listTemplates } = require('../src/templates/registry');
const { renderTemplateSample } = require('../src/templates/render-template-sample');
const {
  describeRendererIdentity,
  runDocumentJob,
  validateStandaloneJobContract,
} = require('../src/workflow/run-document-job');

const ENGINE_ROOT = path.resolve(__dirname, '..');
const STANDALONE_TEMPLATES = [
  ['standalone-work-report', 'work_report', '工作汇报'],
  ['standalone-research-report', 'research_report', '深度研究报告'],
];
const ENTERPRISE_ONLY_WORDS = [
  '签发单位',
  '编制单位',
  '密级',
  '审批人',
  '企业审批',
];

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function makeTempDir(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'docx-engine-v2-standalone-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));
  return tempDir;
}

function standaloneMetadata(documentType, title = '部门月度工作汇报') {
  return { title, documentType };
}

function contractFixture({ templateId, documentType, sourceSha256 = 'e'.repeat(64), assetManifestSha256 = '1'.repeat(64) }) {
  const documentMetadata = standaloneMetadata(documentType);
  const canonicalBinding = {
    artifactId: 'polish:1',
    artifactSha256: 'a'.repeat(64),
    briefRevision: 3,
    briefSha256: 'b'.repeat(64),
  };
  const rendererIdentity = {
    name: 'docx-engine-v2',
    version: '0.1.0',
    buildSha256: 'c'.repeat(64),
    profileId: 'standalone-default',
    profileSha256: 'd'.repeat(64),
  };
  const renderInputBinding = {
    schemaVersion: 'render-input-binding/v1',
    brief: { revision: 3, sha256: 'b'.repeat(64) },
    canonicalArtifact: { artifactId: 'polish:1', sha256: 'a'.repeat(64) },
    canonicalMarkdownSha256: sourceSha256,
    assetManifestSha256,
    semanticGatesSha256: '2'.repeat(64),
    template: { id: templateId, version: '1.0.0', packageSha256: '3'.repeat(64) },
    rendererIdentity,
  };
  return {
    documentMetadata,
    canonicalBinding,
    rendererIdentity,
    renderInputBinding,
    renderInputFingerprint: canonicalSha256(renderInputBinding),
  };
}

test('registry exposes standalone templates after existing enterprise templates', () => {
  assert.deepEqual(
    listTemplates({ rootDir: ENGINE_ROOT }).map((template) => template.id),
    [
      'general-proposal',
      'meeting-minutes',
      'enterprise-work-report',
      'enterprise-research-report',
      'standalone-work-report',
      'standalone-research-report',
    ]
  );
});

test('standalone manifests isolate local delivery from enterprise and Office gates', () => {
  for (const [templateId, documentType] of STANDALONE_TEMPLATES) {
    const template = getTemplatePackage(templateId, { rootDir: ENGINE_ROOT });
    const manifest = template.manifest;
    assert.equal(manifest.contractProfile, 'standalone');
    assert.equal(manifest.rendererProfile, 'standalone-default');
    assert.deepEqual(manifest.documentTypes, [documentType]);
    assert.deepEqual(manifest.requiredMetadata, ['title', 'documentType']);
    assert.equal(manifest.contentPolicy.allowAdapterGeneratedBusinessContent, false);
    assert.equal(manifest.contentPolicy.allowPlaceholders, false);
    assert.deepEqual(manifest.missingDataPolicy.markers, ['待补充', '需人工确认']);
    assert.equal(manifest.missingDataPolicy.forbidFabrication, true);
    assert.equal(manifest.qualityGates.includes('wps_visual'), false);
    assert.equal(manifest.qualityGates.some((gate) => /office|approval/i.test(gate)), false);
  }
});

test('standalone metadata contract accepts only local document identity', () => {
  assert.equal(
    validateDomainObject('StandaloneDocumentMetadataV1', {
      title: '部门月度工作汇报',
      documentType: 'work_report',
    }).ok,
    true
  );
  for (const field of ['issuer', 'compiler', 'classification', 'approver']) {
    const result = validateDomainObject('StandaloneDocumentMetadataV1', {
      title: '部门月度工作汇报',
      documentType: 'work_report',
      [field]: '不应进入单机合同',
    });
    assert.equal(result.ok, false, field);
  }
});

test('standalone adapters preserve approved content without inventing enterprise metadata', () => {
  for (const [templateId, documentType, subtitle] of STANDALONE_TEMPLATES) {
    const templatePackage = getTemplatePackage(templateId, { rootDir: ENGINE_ROOT });
    const templateData = buildTemplateData({
      templatePackage,
      renderPlan: {
        documentMetadata: standaloneMetadata(documentType, '精确标题'),
        templateData: {
          title: '来源标题',
          sections: [
            {
              sectionId: 'sec-1',
              title: '已确认章节',
              blocks: [
                { type: 'paragraph', text: '已确认正文。' },
                { type: 'paragraph', text: '未提供数据【待补充】。' },
              ],
            },
          ],
          tables: [],
          images: [],
          metadata: {},
        },
        tables: [],
        figures: [],
      },
    });
    const serialized = JSON.stringify(templateData);
    assert.deepEqual(templateData.document, {
      title: '精确标题',
      subtitle,
      versionLabel: '',
      documentDate: '',
    });
    assert.deepEqual(templateData.sections.map((section) => section.title), ['已确认章节']);
    assert.deepEqual(templateData.sections[0].paragraphs, [
      { text: '已确认正文。' },
      { text: '未提供数据【待补充】。' },
    ]);
    assert.equal(serialized.includes('【待补充】'), true);
    for (const forbidden of [...ENTERPRISE_ONLY_WORDS, 'issuer', 'compiler', 'classification', 'security_level']) {
      assert.equal(serialized.includes(forbidden), false, forbidden);
    }
  }
});

test('standalone contract keeps canonical binding strict and requires standalone renderer', () => {
  const fixture = contractFixture({
    templateId: 'standalone-work-report',
    documentType: 'work_report',
  });
  const template = {
    templateId: 'standalone-work-report',
    manifest: {
      contractProfile: 'standalone',
      rendererProfile: 'standalone-default',
      documentTypes: ['work_report'],
    },
  };
  assert.equal(validateStandaloneJobContract(fixture, template), true);
  assert.throws(
    () => validateStandaloneJobContract({
      ...fixture,
      rendererIdentity: { ...fixture.rendererIdentity, profileId: 'enterprise-default' },
    }, template),
    (error) => error.code === 'renderer_identity_invalid'
  );
  assert.throws(
    () => validateStandaloneJobContract({
      ...fixture,
      renderInputFingerprint: '0'.repeat(64),
    }, template),
    (error) => error.code === 'render_input_fingerprint_mismatch'
  );
});

test('standalone run-job fails closed when its explicit contract is missing', async (t) => {
  const root = makeTempDir(t);
  const sourcePath = path.join(root, 'source.md');
  const deliveryDir = path.join(root, 'delivery');
  fs.writeFileSync(sourcePath, '# 部门月度工作汇报\n\n## 工作进展\n\n已完成本月重点工作。\n', 'utf8');

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId: 'standalone-work-report',
    sourcePath,
    sourceType: 'markdown',
    assetDir: root,
    deliveryDir,
  });

  assert.equal(result.ok, false);
  assert.equal(result.code, 'brief_incomplete');
  assert.equal(fs.existsSync(path.join(deliveryDir, 'document.docx')), false);
});

test('standalone template samples are editable A4 DOCX files without enterprise labels', async (t) => {
  for (const [templateId] of STANDALONE_TEMPLATES) {
    const template = getTemplatePackage(templateId, { rootDir: ENGINE_ROOT });
    const outDir = path.join(makeTempDir(t), templateId);
    const result = await renderTemplateSample({ packageDir: template.packageDir, outDir });
    assert.equal(result.ok, true);
    const entries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
    const documentXml = entries.get('word/document.xml').toString('utf8');
    const footerXml = [...entries.entries()]
      .filter(([entry]) => /^word\/footer\d+\.xml$/.test(entry))
      .map(([, buffer]) => buffer.toString('utf8'))
      .join('\n');
    assert.match(documentXml, /<w:pgSz[^>]*w:w="1190[0-9]"[^>]*w:h="1683[0-9]"/);
    assert.equal(documentXml.includes('{d.'), false);
    assert.match(documentXml, /已确认正文|本月重点工作|研究范围/);
    assert.match(footerXml, /PAGE/);
    for (const forbidden of ENTERPRISE_ONLY_WORDS) {
      assert.equal(documentXml.includes(forbidden), false, forbidden);
    }
  }
});

test('standalone run-job accepts minimal metadata and preserves missing-data markers', async (t) => {
  const root = makeTempDir(t);
  const sourcePath = path.join(root, 'source.md');
  const assetManifestPath = path.join(root, 'asset-manifest.json');
  const deliveryDir = path.join(root, 'delivery');
  fs.writeFileSync(
    sourcePath,
    '# 部门月度工作汇报\n\n## 工作进展\n\n已完成本月重点工作。\n\n## 数据情况\n\n详细数据【待补充】。\n',
    'utf8'
  );
  fs.writeFileSync(assetManifestPath, '{"schema_version":"expert-asset-manifest/v1","assets":[]}\n', 'utf8');
  const templateId = 'standalone-work-report';
  const templateBinding = readJson(path.join(ENGINE_ROOT, 'templates', templateId, 'template-package.binding.json'));
  const rendererIdentity = describeRendererIdentity({
    engineRoot: ENGINE_ROOT,
    profileId: 'standalone-default',
  });
  const contract = contractFixture({
    templateId,
    documentType: 'work_report',
    sourceSha256: sha256File(sourcePath),
    assetManifestSha256: sha256File(assetManifestPath),
  });
  contract.rendererIdentity = rendererIdentity;
  contract.renderInputBinding = {
    ...contract.renderInputBinding,
    template: {
      id: templateId,
      version: '1.0.0',
      packageSha256: templateBinding.packageSha256,
    },
    rendererIdentity,
  };
  contract.renderInputFingerprint = canonicalSha256(contract.renderInputBinding);

  const result = await runDocumentJob({
    engineRoot: ENGINE_ROOT,
    templateId,
    sourcePath,
    sourceType: 'markdown',
    assetDir: root,
    assetManifestPath,
    deliveryDir,
    ...contract,
  });

  assert.equal(result.ok, true, JSON.stringify(result));
  const entries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
  const documentXml = entries.get('word/document.xml').toString('utf8');
  assert.equal(documentXml.includes('【待补充】'), true);
  assert.equal(documentXml.includes('{d.'), false);
  const jobManifest = readJson(path.join(deliveryDir, 'job.manifest.json'));
  assert.deepEqual(jobManifest.documentMetadata, {
    title: '部门月度工作汇报',
    documentType: 'work_report',
  });
  const templateManifest = readJson(path.join(deliveryDir, 'template.manifest.json'));
  assert.equal(templateManifest.contractProfile, 'standalone');
  assert.equal(templateManifest.qualityGates.includes('wps_visual'), false);
});
