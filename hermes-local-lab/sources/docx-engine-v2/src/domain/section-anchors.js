function resolveSectionAnchors(paragraphs, sections) {
  const anchors = [];
  let searchStart = 0;

  for (const section of sections || []) {
    const title = String(section?.title || '').trim();
    const anchor = title ? findNextTitleRunAnchor(paragraphs, title, searchStart) : null;
    anchors.push(anchor);
    if (anchor) {
      searchStart = anchor.end;
    }
  }

  return anchors;
}

function findNextTitleRunAnchor(paragraphs, title, searchStart) {
  const exactRun = findNextParagraphRun(
    paragraphs,
    searchStart,
    (paragraph) => paragraph.text.trim() === title
  );
  if (exactRun.length > 0) {
    return exactRun[exactRun.length - 1];
  }

  const fallbackRun = findNextParagraphRun(
    paragraphs,
    searchStart,
    (paragraph) => paragraph.text.includes(title) && !isInternalMarkerText(paragraph.text)
  );
  return fallbackRun.length > 0 ? fallbackRun[fallbackRun.length - 1] : null;
}

function findNextParagraphRun(paragraphs, searchStart, matches) {
  const firstIndex = (paragraphs || []).findIndex(
    (paragraph) => paragraph.start >= searchStart && matches(paragraph)
  );
  if (firstIndex < 0) {
    return [];
  }

  const run = [];
  for (let index = firstIndex; index < paragraphs.length; index += 1) {
    const paragraph = paragraphs[index];
    if (!matches(paragraph)) {
      break;
    }
    run.push(paragraph);
  }
  return run;
}

function isInternalMarkerText(text) {
  return /\b(docx-engine-v2|figureCaption|tableId|figureId|directoryEntry)\b/.test(String(text || ''));
}

module.exports = { resolveSectionAnchors };
