function resolveSectionAnchors(paragraphs, sections) {
  const anchors = [];
  let searchStart = 0;

  for (const section of sections || []) {
    const title = String(section?.title || '').trim();
    const contentAnchor = findFirstSectionContentParagraph(paragraphs, section, searchStart);
    const anchor = title
      ? findTitleAnchor(paragraphs, title, searchStart, contentAnchor?.start)
      : null;
    anchors.push(anchor);
    if (anchor) {
      searchStart = anchor.end;
    }
  }

  return anchors;
}

function findTitleAnchor(paragraphs, title, searchStart, beforeIndex) {
  const candidates = (paragraphs || []).filter((paragraph) => (
    paragraph.start >= searchStart
      && (beforeIndex == null || paragraph.end <= beforeIndex)
      && !isInternalMarkerText(paragraph.text)
  ));
  const exact = candidates.filter((paragraph) => paragraph.text.trim() === title);
  if (exact.length > 0) {
    return beforeIndex == null ? exact[0] : exact[exact.length - 1];
  }

  const fallback = candidates.filter((paragraph) => paragraph.text.includes(title));
  return beforeIndex == null ? fallback[0] || null : fallback[fallback.length - 1] || null;
}

function findFirstSectionContentParagraph(paragraphs, section, searchStart) {
  const title = String(section?.title || '').trim();
  for (const block of section?.blocks || []) {
    const text = String(block?.text || '').trim();
    if (block?.type !== 'paragraph' || !text || text === title) {
      continue;
    }
    const paragraph = (paragraphs || []).find((candidate) => (
      candidate.start >= searchStart
        && candidate.text.trim() === text
        && !isInternalMarkerText(candidate.text)
    ));
    if (paragraph) {
      return paragraph;
    }
  }
  return null;
}

function isInternalMarkerText(text) {
  return /\b(docx-engine-v2|figureCaption|tableId|figureId|directoryEntry)\b/.test(String(text || ''));
}

module.exports = { resolveSectionAnchors };
