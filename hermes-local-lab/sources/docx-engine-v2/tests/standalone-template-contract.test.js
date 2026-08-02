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
const CONTENT_TEMPLATE_CASES = [
  ['standalone-meeting-minutes', 'meeting_minutes', '会议纪要'],
  ['standalone-office-material', 'notice', '通知通报'],
  ['standalone-office-material', 'plan', '方案说明'],
  ['standalone-office-material', 'summary_plan', '总结计划'],
  ['standalone-office-material', 'other_office_material', '材料润色'],
];
const RELEASED_TASK_CASES = [
  {
    templateId: 'standalone-work-report', documentType: 'work_report', title: '部门月度工作汇报',
    sections: ['工作开展情况', '存在问题', '下一步工作安排'],
  },
  {
    templateId: 'standalone-meeting-minutes', documentType: 'meeting_minutes', title: '专题会议纪要',
    sections: ['会议基本情况', '议定事项', '责任分工', '后续跟踪'],
  },
  {
    templateId: 'standalone-office-material', documentType: 'notice', title: '安全生产专项检查通知',
    sections: ['背景与总体要求', '通知事项', '时间安排', '责任分工', '报送要求'],
  },
  {
    templateId: 'standalone-office-material', documentType: 'plan', title: '服务质量提升实施方案',
    sections: ['目标', '现状与问题', '主要措施', '进度安排', '保障机制'],
  },
  {
    templateId: 'standalone-office-material', documentType: 'summary_plan', title: '上半年总结及下半年计划',
    sections: ['阶段性工作总结', '成效与亮点', '问题与不足', '下一步工作计划'],
  },
  {
    templateId: 'standalone-office-material', documentType: 'other_office_material', title: '服务情况说明（润色稿）',
    sections: ['润色后正文', '修改说明'],
  },
  {
    templateId: 'standalone-research-report', documentType: 'research_report', title: '本地智能办公应用研究报告',
    sections: ['研究问题', '证据', '分析', '结论边界', '引用'],
  },
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
      'standalone-meeting-minutes',
      'standalone-office-material',
    ]
  );
});

test('standalone templates target the current Word compatibility mode', () => {
  const templateIds = [
    'standalone-work-report',
    'standalone-research-report',
    'standalone-meeting-minutes',
    'standalone-office-material',
  ];
  for (const templateId of templateIds) {
    const entries = readZipEntriesFromBuffer(
      fs.readFileSync(path.join(ENGINE_ROOT, 'templates', templateId, 'template.docx'))
    );
    const settingsXml = entries.get('word/settings.xml')?.toString('utf8') || '';
    assert.match(
      settingsXml,
      /w:name="compatibilityMode"[^>]*w:val="15"/,
      `${templateId} must open in the current Word document mode`
    );
    assert.doesNotMatch(
      settingsXml,
      /w:name="compatibilityMode"[^>]*w:val="14"/,
      `${templateId} must not force Word 2010 compatibility mode`
    );
  }
});

test('standalone templates advertise only portable font families', () => {
  const templateIds = [
    'standalone-work-report',
    'standalone-research-report',
    'standalone-meeting-minutes',
    'standalone-office-material',
  ];
  const portableFonts = new Set(['', 'Arial', 'Symbol', '宋体', '黑体']);
  for (const templateId of templateIds) {
    const entries = readZipEntriesFromBuffer(
      fs.readFileSync(path.join(ENGINE_ROOT, 'templates', templateId, 'template.docx'))
    );
    const fontTableXml = entries.get('word/fontTable.xml')?.toString('utf8') || '';
    const packageXmlParts = [...entries]
      .filter(([entryName]) => entryName.endsWith('.xml'))
      .map(([, contents]) => contents.toString('utf8'));
    const advertisedFonts = [
      ...[...fontTableXml.matchAll(/<w:font\b[^>]*w:name="([^"]+)"/g)].map((match) => match[1]),
      ...packageXmlParts.flatMap((xml) =>
        [...xml.matchAll(/<w:rFonts\b[^>]*>/g)].flatMap((match) =>
          [...match[0].matchAll(/w:(?:ascii|hAnsi|eastAsia|cs)="([^"]+)"/g)]
            .map((fontMatch) => fontMatch[1])
        )
      ),
      ...packageXmlParts.flatMap((xml) =>
        [...xml.matchAll(/\btypeface="([^"]*)"/g)].map((match) => match[1])
      ),
    ];
    for (const fontName of advertisedFonts) {
      assert.equal(
        portableFonts.has(fontName),
        true,
        `${templateId} must not advertise non-portable font ${fontName}`
      );
    }
  }
});

test('standalone templates declare Chinese proofing language for document text', () => {
  const templateIds = [
    'standalone-work-report',
    'standalone-research-report',
    'standalone-meeting-minutes',
    'standalone-office-material',
  ];
  for (const templateId of templateIds) {
    const entries = readZipEntriesFromBuffer(
      fs.readFileSync(path.join(ENGINE_ROOT, 'templates', templateId, 'template.docx'))
    );
    const packageXmlParts = [...entries]
      .filter(([entryName]) => entryName.endsWith('.xml'))
      .map(([, contents]) => contents.toString('utf8'));
    const languageTags = packageXmlParts.flatMap((xml) => [...xml.matchAll(/<w:lang\b[^>]*>/g)]);
    assert.ok(languageTags.length > 0, `${templateId} must declare a proofing language`);
    for (const [languageTag] of languageTags) {
      assert.match(languageTag, /w:val="zh-CN"/, `${templateId} must use Chinese proofing language`);
      assert.match(languageTag, /w:eastAsia="zh-CN"/, `${templateId} must use Chinese East Asian language`);
    }
  }
});

test('content task standalone templates preserve only approved business content', () => {
  for (const [templateId, documentType, subtitle] of CONTENT_TEMPLATE_CASES) {
    const templatePackage = getTemplatePackage(templateId, { rootDir: ENGINE_ROOT });
    const manifest = templatePackage.manifest;
    assert.equal(manifest.contractProfile, 'standalone');
    assert.equal(manifest.rendererProfile, 'standalone-default');
    assert.equal(manifest.documentTypes.includes(documentType), true);
    assert.deepEqual(manifest.requiredMetadata, ['title', 'documentType']);
    assert.equal(manifest.contentPolicy.allowAdapterGeneratedBusinessContent, false);
    assert.equal(manifest.contentPolicy.allowPlaceholders, false);
    assert.equal(manifest.qualityGates.includes('wps_visual'), false);

    const templateData = buildTemplateData({
      templatePackage,
      renderPlan: {
        documentMetadata: standaloneMetadata(documentType, '用户确认标题'),
        templateData: {
          title: '模型来源标题',
          sections: [{
            sectionId: 'sec-1',
            title: '用户确认章节',
            blocks: [{ type: 'paragraph', text: '用户确认正文。' }],
          }],
          tables: [],
          images: [],
          metadata: {},
        },
        tables: [],
        figures: [],
      },
    });
    const serialized = JSON.stringify(templateData);
    assert.equal(templateData.document.title, '用户确认标题');
    assert.equal(templateData.document.subtitle, subtitle);
    assert.deepEqual(templateData.sections.map((section) => section.title), ['用户确认章节']);
    assert.deepEqual(templateData.sections[0].paragraphs, [{ text: '用户确认正文。' }]);
    for (const forbidden of [...ENTERPRISE_ONLY_WORDS, '客户单位', '北京太极', '2026年7月', '项目组']) {
      assert.equal(serialized.includes(forbidden), false, `${templateId}/${documentType}: ${forbidden}`);
    }
  }
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
  for (const documentType of [
    'work_report',
    'research_report',
    'meeting_minutes',
    'notice',
    'plan',
    'summary_plan',
    'other_office_material',
  ]) {
    assert.equal(
      validateDomainObject('StandaloneDocumentMetadataV1', {
        title: '用户确认标题',
        documentType,
      }).ok,
      true,
      documentType
    );
  }
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

test('content task standalone template samples render editable A4 DOCX files', async (t) => {
  for (const templateId of ['standalone-meeting-minutes', 'standalone-office-material']) {
    const template = getTemplatePackage(templateId, { rootDir: ENGINE_ROOT });
    const outDir = path.join(makeTempDir(t), templateId);
    const result = await renderTemplateSample({ packageDir: template.packageDir, outDir });
    assert.equal(result.ok, true);
    const entries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
    const documentXml = entries.get('word/document.xml').toString('utf8');
    assert.match(documentXml, /<w:pgSz[^>]*w:w="1190[0-9]"[^>]*w:h="1683[0-9]"/);
    assert.equal(documentXml.includes('{d.'), false);
    assert.match(documentXml, /已确认正文/);
    for (const forbidden of [...ENTERPRISE_ONLY_WORDS, '客户单位', '北京太极']) {
      assert.equal(documentXml.includes(forbidden), false, `${templateId}: ${forbidden}`);
    }
  }
});

test('all released content document types complete a standalone DOCX job', async (t) => {
  for (const [templateId, documentType] of CONTENT_TEMPLATE_CASES) {
    const root = makeTempDir(t);
    const sourcePath = path.join(root, 'source.md');
    const assetManifestPath = path.join(root, 'asset-manifest.json');
    const deliveryDir = path.join(root, 'delivery');
    fs.writeFileSync(
      sourcePath,
      '# 用户确认标题\n\n## 用户确认章节\n\n用户确认正文；缺失数据【待补充】。\n',
      'utf8'
    );
    fs.writeFileSync(
      assetManifestPath,
      '{"schema_version":"expert-asset-manifest/v1","assets":[]}\n',
      'utf8'
    );
    const binding = readJson(path.join(ENGINE_ROOT, 'templates', templateId, 'template-package.binding.json'));
    const rendererIdentity = describeRendererIdentity({
      engineRoot: ENGINE_ROOT,
      profileId: 'standalone-default',
    });
    const contract = contractFixture({
      templateId,
      documentType,
      sourceSha256: sha256File(sourcePath),
      assetManifestSha256: sha256File(assetManifestPath),
    });
    contract.documentMetadata.title = '用户确认标题';
    contract.rendererIdentity = rendererIdentity;
    contract.renderInputBinding = {
      ...contract.renderInputBinding,
      template: { id: templateId, version: '1.0.0', packageSha256: binding.packageSha256 },
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

    assert.equal(result.ok, true, `${documentType}: ${JSON.stringify(result)}`);
    const entries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
    const documentXml = entries.get('word/document.xml').toString('utf8');
    assert.equal(documentXml.includes('用户确认正文'), true, documentType);
    assert.equal(documentXml.includes('【待补充】'), true, documentType);
    assert.equal(documentXml.includes('{d.'), false, documentType);
  }
});

test('all seven released tasks preserve every required section in the final DOCX', async (t) => {
  for (const task of RELEASED_TASK_CASES) {
    const root = makeTempDir(t);
    const sourcePath = path.join(root, 'source.md');
    const assetManifestPath = path.join(root, 'asset-manifest.json');
    const deliveryDir = path.join(root, 'delivery');
    const markdown = [
      `# ${task.title}`,
      '',
      ...task.sections.flatMap((section, index) => [
        `## ${section}`,
        '',
        task.documentType === 'research_report' && section === '引用'
          ? '本节引用已核对资料 [SRC-001]。'
          : `第 ${index + 1} 节为用户确认内容。`,
        '',
      ]),
    ].join('\n');
    fs.writeFileSync(sourcePath, markdown, 'utf8');
    fs.writeFileSync(
      assetManifestPath,
      '{"schema_version":"expert-asset-manifest/v1","assets":[]}\n',
      'utf8'
    );

    const templateBinding = readJson(path.join(
      ENGINE_ROOT,
      'templates',
      task.templateId,
      'template-package.binding.json'
    ));
    const rendererIdentity = describeRendererIdentity({
      engineRoot: ENGINE_ROOT,
      profileId: 'standalone-default',
    });
    const contract = contractFixture({
      templateId: task.templateId,
      documentType: task.documentType,
      sourceSha256: sha256File(sourcePath),
      assetManifestSha256: sha256File(assetManifestPath),
    });
    contract.documentMetadata.title = task.title;
    contract.rendererIdentity = rendererIdentity;
    contract.renderInputBinding = {
      ...contract.renderInputBinding,
      template: {
        id: task.templateId,
        version: '1.0.0',
        packageSha256: templateBinding.packageSha256,
      },
      rendererIdentity,
    };
    contract.renderInputFingerprint = canonicalSha256(contract.renderInputBinding);

    const result = await runDocumentJob({
      engineRoot: ENGINE_ROOT,
      templateId: task.templateId,
      sourcePath,
      sourceType: 'markdown',
      assetDir: root,
      assetManifestPath,
      deliveryDir,
      ...contract,
    });

    assert.equal(result.ok, true, `${task.documentType}: ${JSON.stringify(result)}`);
    assert.equal(path.basename(result.documentPath), 'document.docx');
    const entries = readZipEntriesFromBuffer(fs.readFileSync(result.documentPath));
    const documentXml = entries.get('word/document.xml').toString('utf8');
    assert.equal(documentXml.includes(task.title), true, `${task.documentType}: title`);
    for (const section of task.sections) {
      assert.equal(documentXml.includes(section), true, `${task.documentType}: ${section}`);
    }
    assert.equal(documentXml.includes('{d.'), false, task.documentType);
    assert.equal(documentXml.includes('<<<TAIJI_'), false, task.documentType);
    for (const forbidden of ENTERPRISE_ONLY_WORDS) {
      assert.equal(documentXml.includes(forbidden), false, `${task.documentType}: ${forbidden}`);
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

test('standalone work-report renders markdown tables without template table placeholders', async (t) => {
  const root = makeTempDir(t);
  const sourcePath = path.join(root, 'source.md');
  const assetManifestPath = path.join(root, 'asset-manifest.json');
  const deliveryDir = path.join(root, 'delivery');
  fs.writeFileSync(
    sourcePath,
    [
      '# 部门月度工作汇报',
      '',
      '## 工作开展情况',
      '',
      '- **稳定性验证**：已完成专家团恢复链路检查。',
      '',
      '| 工作类别 | 完成情况 |',
      '| --- | --- |',
      '| 设备巡检 | 待补充 |',
      '',
      '## 存在问题',
      '',
      '问题明细待补充，问题及下一步安排如下：',
      '',
      '| 问题类别 | 下一步措施 |',
      '| --- | --- |',
      '| 设备风险 | 待补充 |',
      '',
      '## 下一步工作安排',
      '',
      '下一步安排待补充。',
      '',
    ].join('\n'),
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
  assert.match(documentXml, /tableId=tbl-001/);
  assert.match(documentXml, /tableId=tbl-002/);
  assert.match(documentXml, /<w:tbl\b/);
  assert.equal(
    (documentXml.match(/<w:tbl\b/g) || []).length,
    2,
    'standalone output must not retain duplicate template tables after dynamic insertion'
  );
  assert.equal(
    (documentXml.match(/<w:drawing\b/g) || []).length,
    0,
    'standalone output without figures must not retain the template placeholder drawing'
  );
  assert.match(documentXml, /设备巡检/);
  assert.match(documentXml, /待补充/);
  assert.match(documentXml, /稳定性验证/);
  assert.equal(
    documentXml.includes('**'),
    false,
    'standalone output must not leak markdown emphasis markers into visible DOCX text'
  );
});
