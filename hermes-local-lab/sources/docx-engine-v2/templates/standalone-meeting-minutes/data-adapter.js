function buildTemplateData({ renderPlan }) {
  const metadata = renderPlan?.documentMetadata || {};
  requireStandaloneMetadata(metadata);
  return mapApprovedData(renderPlan, metadata);
}

function requireStandaloneMetadata(metadata) {
  if (!String(metadata.title || '').trim()) {
    throw new Error('brief_incomplete: title');
  }
  if (metadata.documentType !== 'meeting_minutes') {
    throw new Error('template_selection_required: meeting_minutes');
  }
}

function mapApprovedData(renderPlan, metadata) {
  return {
    document: {
      title: String(metadata.title).trim(),
      subtitle: '会议纪要',
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
