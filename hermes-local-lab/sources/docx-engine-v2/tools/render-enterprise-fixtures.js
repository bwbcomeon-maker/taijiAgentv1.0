const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const { canonicalSha256 } = require('../src/domain/document-job');
const { describeRendererIdentity, runDocumentJob } = require('../src/workflow/run-document-job');

const root = path.resolve(__dirname, '..');
const qaRoot = path.join(root, '.qa', 'enterprise-fixtures');

async function render(templateId, documentType, title, sourceName) {
  const sourcePath = path.join(root, 'tests', 'fixtures', sourceName);
  const jobRoot = path.join(qaRoot, templateId);
  const deliveryDir = path.join(jobRoot, 'delivery');
  fs.rmSync(jobRoot, { recursive: true, force: true });
  fs.mkdirSync(jobRoot, { recursive: true });
  const assetManifestPath = path.join(jobRoot, 'asset-manifest.json');
  fs.writeFileSync(assetManifestPath, '{"schema_version":"expert-asset-manifest/v1","assets":[]}\n');
  const rendererIdentity = describeRendererIdentity({ engineRoot: root });
  const packageBinding = JSON.parse(fs.readFileSync(path.join(root, 'templates', templateId, 'template-package.binding.json'), 'utf8'));
  const documentMetadata = {
    title, documentType, client: '国家电网有限公司', issuer: documentType === 'work_report' ? '办公室' : '研究中心',
    compiler: '信息化工作组', versionLabel: 'V1.0', classification: 'internal',
    classificationLabel: '内部资料', documentDate: '2026-07-15',
  };
  const canonicalBinding = { artifactId: 'polish:1', artifactSha256: 'a'.repeat(64), briefRevision: 3, briefSha256: 'b'.repeat(64) };
  const renderInputBinding = {
    schemaVersion: 'render-input-binding/v1', brief: { revision: 3, sha256: 'b'.repeat(64) },
    canonicalArtifact: { artifactId: 'polish:1', sha256: 'a'.repeat(64) },
    canonicalMarkdownSha256: sha256File(sourcePath), assetManifestSha256: sha256File(assetManifestPath),
    semanticGatesSha256: 'c'.repeat(64),
    template: { id: templateId, version: '1.0.0', packageSha256: packageBinding.packageSha256 },
    rendererIdentity,
  };
  const result = await runDocumentJob({
    engineRoot: root, templateId, sourcePath, sourceType: 'markdown', assetDir: jobRoot,
    assetManifestPath, deliveryDir, documentMetadata, canonicalBinding, rendererIdentity,
    renderInputBinding, renderInputFingerprint: canonicalSha256(renderInputBinding),
  });
  if (!result.ok) throw new Error(`${templateId}: ${result.code}: ${result.message}`);
  process.stdout.write(`${JSON.stringify({ templateId, deliveryDir, qualityStatus: result.qualityStatus })}\n`);
}

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

Promise.all([
  render('enterprise-work-report', 'work_report', '迎峰度夏保供电重点工作月度汇报', 'enterprise-work-report.md'),
  render('enterprise-research-report', 'research_report', '企业本地优先 AI 助理落地研究报告', 'enterprise-research-report.md'),
]).catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
