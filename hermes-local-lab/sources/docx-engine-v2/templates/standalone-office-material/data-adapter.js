const SUBTITLES = Object.freeze({
  notice: '通知通报',
  plan: '方案说明',
  summary_plan: '总结计划',
  other_office_material: '材料润色',
});

function buildTemplateData({ renderPlan }) {
  const metadata = renderPlan?.documentMetadata || {};
  const subtitle = requireStandaloneMetadata(metadata);
  return mapApprovedData(renderPlan, metadata, subtitle);
}

function requireStandaloneMetadata(metadata) {
  if (!String(metadata.title || '').trim()) {
    throw new Error('brief_incomplete: title');
  }
  const subtitle = SUBTITLES[metadata.documentType];
  if (!subtitle) {
    throw new Error('template_selection_required: standalone_office_material');
  }
  return subtitle;
}

function mapApprovedData(renderPlan, metadata, subtitle) {
  return {
    document: {
      title: String(metadata.title).trim(),
      subtitle,
      versionLabel: String(metadata.versionLabel || '').trim(),
      documentDate: String(metadata.documentDate || '').trim(),
    },
    sections: (renderPlan.templateData?.sections || []).map((section) => ({
      sectionId: section.sectionId,
      title: section.title,
      paragraphs: (section.blocks || [])
        .filter((block) => block.type === 'paragraph' && String(block.text || '').trim())
        .map((block) => ({ text: block.text })),
    })),
    tables: [...(renderPlan.templateData?.tables || [])],
    figures: (renderPlan.figures || []).map((figure) => ({
      title: String(figure.caption || ''),
      description: String(figure.anchorText || ''),
    })),
    images: [...(renderPlan.templateData?.images || [])],
  };
}

module.exports = { buildTemplateData };
