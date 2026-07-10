function buildTemplateData({ renderPlan }) {
  const title = textOr(renderPlan.templateData?.title, '会议纪要');
  const sections = renderPlan.templateData?.sections || [];
  const sourceTopics = sections.map((section) => ({
      title: textOr(section.title, '会议议题'),
      summary: paragraphsFromBlocks(section.blocks).map((item) => item.text).join(' ') || textOr(section.title, '待补充'),
    }));
  const topics = sourceTopics.length > 2
    ? [
        sourceTopics[0],
        {
          title: sourceTopics[1].title,
          summary: sourceTopics
            .slice(1)
            .map((topic) => topic.summary)
            .join(' '),
        },
      ]
    : padArray(
        sourceTopics,
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

module.exports = { buildTemplateData };
