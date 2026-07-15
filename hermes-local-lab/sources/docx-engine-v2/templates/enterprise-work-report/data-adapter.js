const REQUIRED_METADATA = ['title', 'documentType', 'issuer', 'compiler', 'versionLabel', 'classification', 'documentDate'];

function buildTemplateData({ renderPlan }) {
  const metadata = renderPlan?.documentMetadata || {};
  for (const field of REQUIRED_METADATA) {
    if (!String(metadata[field] || '').trim()) throw new Error(`brief_incomplete: ${field}`);
  }
  if (metadata.documentType !== 'work_report') throw new Error('template_selection_required: work_report');
  return mapApprovedData(renderPlan, metadata, '工作汇报');
}

function mapApprovedData(renderPlan, metadata, subtitle) {
  return {
    cover: {
      title: metadata.title, subtitle, client: String(metadata.client || ''), issuer: metadata.issuer,
      compiler: metadata.compiler, version: metadata.versionLabel,
      security_level: metadata.classificationLabel || metadata.classification, date: metadata.documentDate,
    },
    sections: (renderPlan.templateData?.sections || []).map((section) => ({
      sectionId: section.sectionId, title: section.title,
      paragraphs: (section.blocks || []).filter((block) => block.type === 'paragraph' && String(block.text || '').trim()).map((block) => ({ text: block.text })),
    })),
    tables: [...(renderPlan.templateData?.tables || [])],
    images: [...(renderPlan.templateData?.images || [])],
  };
}

module.exports = { buildTemplateData };
