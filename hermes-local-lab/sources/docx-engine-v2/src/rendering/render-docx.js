const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const Ajv2020 = require('ajv/dist/2020');
const carbone = require('carbone');

async function renderDocx({ templatePackage, renderPlan, outputPath } = {}) {
  if (!templatePackage) {
    throw new Error('templatePackage is required.');
  }
  if (!renderPlan) {
    throw new Error('renderPlan is required.');
  }
  if (!outputPath) {
    throw new Error('outputPath is required.');
  }

  const templatePath = templatePackage.templatePath;
  if (!templatePath) {
    throw new Error('templatePackage.templatePath is required.');
  }

  const templateData = buildTemplateData({ templatePackage, renderPlan });
  validateTemplateData({ templatePackage, templateData });
  const rendered = await renderCarbone(templatePath, templateData);
  await fsp.mkdir(path.dirname(outputPath), { recursive: true });
  await fsp.writeFile(outputPath, rendered);

  return {
    status: 'rendered',
    documentPath: outputPath,
    templateId: renderPlan.templateId || templatePackage.templateId || templatePackage.id,
  };
}

function renderCarbone(templatePath, data) {
  return new Promise((resolve, reject) => {
    carbone.render(templatePath, data, (error, result) => {
      if (error) {
        reject(error);
        return;
      }
      resolve(result);
    });
  });
}

function validateTemplateData({ templatePackage, templateData }) {
  if (!templatePackage.schemaPath || !fs.existsSync(templatePackage.schemaPath)) {
    return;
  }
  const schema = JSON.parse(fs.readFileSync(templatePackage.schemaPath, 'utf8'));
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  const validate = ajv.compile(schema);
  if (!validate(templateData)) {
    throw new Error(`Template data validation failed: ${JSON.stringify(validate.errors || [])}`);
  }
}

function buildTemplateData({ templatePackage, renderPlan }) {
  const templateId = templatePackage.templateId || templatePackage.id || renderPlan.templateId;
  if (templateId === 'meeting-minutes') {
    return buildMeetingMinutesData(renderPlan);
  }
  return buildGeneralProposalData(renderPlan);
}

function buildGeneralProposalData(renderPlan) {
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

function buildMeetingMinutesData(renderPlan) {
  const title = textOr(renderPlan.templateData?.title, '会议纪要');
  const sections = renderPlan.templateData?.sections || [];
  const topics = padArray(
    sections.map((section) => ({
      title: textOr(section.title, '会议议题'),
      summary: paragraphsFromBlocks(section.blocks).map((item) => item.text).join(' ') || textOr(section.title, '待补充'),
    })),
    2,
    (index) => ({ title: `议题 ${index + 1}`, summary: '待补充' })
  );

  return {
    meeting: {
      title,
      time: '2026年7月',
      location: '待补充',
      host: '待补充',
      recorder: '待补充',
    },
    attendees: [
      { name: '待补充', role: '主持人' },
      { name: '待补充', role: '记录人' },
      { name: '待补充', role: '参会人' },
    ],
    topics,
    decisions: [
      { item: `围绕“${title}”继续推进后续工作。`, owner: '项目组' },
      { item: '会后补充确认责任人、时间和交付物。', owner: '项目组' },
    ],
    actionItems: [
      { task: '完善会议纪要内容并确认分工。', owner: '项目组', dueDate: '待定', status: '进行中' },
      { task: '完成文档模板视觉验收。', owner: '项目组', dueDate: '待定', status: '未开始' },
    ],
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

module.exports = { renderDocx, buildTemplateData };
