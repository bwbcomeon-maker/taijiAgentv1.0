function buildTemplateData({ renderPlan }) {
  const metadata = renderPlan?.documentMetadata || {};
  requireStandaloneMetadata(metadata, 'work_report');
  return mapApprovedData(renderPlan, metadata, '工作汇报');
}

function requireStandaloneMetadata(metadata, documentType) {
  if (!String(metadata.title || '').trim()) {
    throw new Error('brief_incomplete: title');
  }
  if (metadata.documentType !== documentType) {
    throw new Error(`template_selection_required: ${documentType}`);
  }
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
