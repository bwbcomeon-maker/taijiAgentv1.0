function buildTemplateData({ renderPlan }) {
  const sourceTitle = textOr(renderPlan.templateData?.title, '未命名方案');
  const sections = renderPlan.templateData?.sections || [];
  const chapters = sections.length
    ? sections.map((section, index) => ({
      number: chineseChapterNumber(index + 1),
      title: textOr(section.title, `章节 ${index + 1}`),
      sections: [
        {
          number: `${index + 1}.1`,
          title: textOr(section.title, `章节 ${index + 1}`),
          paragraphs: paragraphsFromBlocks(section.blocks),
        },
      ],
    }))
    : [
      {
        number: '第一章',
        title: sourceTitle,
        sections: [{ number: '1.1', title: sourceTitle, paragraphs: [{ text: sourceTitle }] }],
      },
    ];

  return {
    cover: {
      title: sourceTitle,
      subtitle: '技术方案',
      client: '客户单位',
      company: '北京太极信息系统技术有限公司',
      version: 'V1.0',
      security_level: '内部资料',
      date: '2026年7月',
    },
    chapters,
    tables: padArray(
      (renderPlan.templateData?.tables || []).map((table, index) => ({
        title: textOr(table.title, `表格 ${index + 1}`),
        headers: ensureHeaderObject(table.headers, table.rows),
        rows: ensureRows(table.rows),
      })),
      2,
      (index) => ({
        title: `表格 ${index + 1}`,
        headers: { c1: '项目', c2: '内容' },
        rows: [{ c1: '暂无', c2: '暂无' }],
      })
    ),
    figures: padArray(
      (renderPlan.figures || []).map((figure, index) => ({
        title: textOr(figure.caption, `图 ${index + 1}`),
        description: textOr(figure.anchorText, figure.figureId || `图 ${index + 1}`),
      })),
      1,
      (index) => ({ title: `图 ${index + 1}`, description: '暂无图示说明' })
    ),
    images: padArray(
      (renderPlan.templateData?.images || []).map((image, index) => ({
        blockId: image.metadata?.blockId || '',
        path: textOr(image.path, `assets/fig-${String(index + 1).padStart(3, '0')}/figure.svg`),
        figureId: image.figureId || `fig-${String(index + 1).padStart(3, '0')}`,
        title: textOr(image.caption, `图 ${index + 1}`),
        description: textOr(image.metadata?.sectionTitle, image.caption || ''),
        anchor: textOr(image.caption, image.figureId || `图 ${index + 1}`),
        required: true,
      })),
      1,
      (index) => ({
        path: `assets/fig-${String(index + 1).padStart(3, '0')}/figure.svg`,
        figureId: `fig-${String(index + 1).padStart(3, '0')}`,
        title: `图 ${index + 1}`,
        description: '暂无图片说明',
        anchor: `图 ${index + 1}`,
        required: false,
      })
    ),
    conclusion: {
      title: '结论',
      paragraphs: [
        { text: `本方案围绕“${sourceTitle}”形成了可渲染、可交付、可追溯的文档包。` },
        { text: '后续仍需完成 WPS/Word 人工视觉验收，并按业务需要微调最终版式。' },
      ],
    },
  };
}

function paragraphsFromBlocks(blocks) {
  const paragraphs = (blocks || [])
    .filter((block) => block.type === 'paragraph')
    .map((block) => ({ text: textOr(block.text, '') }))
    .filter((item) => item.text);
  return paragraphs.length ? paragraphs : [{ text: '待补充。' }];
}

function ensureHeaderObject(headers, rows) {
  if (headers && typeof headers === 'object' && !Array.isArray(headers) && Object.keys(headers).length) {
    return headers;
  }
  const firstRow = Array.isArray(rows) ? rows[0] : null;
  if (firstRow && typeof firstRow === 'object' && !Array.isArray(firstRow)) {
    const keys = Object.keys(firstRow);
    if (keys.length) {
      return Object.fromEntries(keys.map((key, index) => [key, `列${index + 1}`]));
    }
  }
  return { c1: '项目', c2: '内容' };
}

function ensureRows(rows) {
  if (Array.isArray(rows) && rows.length) {
    return rows.map((row) => (row && typeof row === 'object' && !Array.isArray(row) ? row : { c1: String(row ?? '') }));
  }
  return [{ c1: '暂无', c2: '暂无' }];
}

function padArray(items, minimum, makeItem) {
  const next = [...items];
  while (next.length < minimum) {
    next.push(makeItem(next.length));
  }
  return next;
}

function textOr(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

function chineseChapterNumber(index) {
  const numerals = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
  return `第${numerals[index - 1] || index}章`;
}

module.exports = { buildTemplateData };
